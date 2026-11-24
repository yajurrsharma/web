"""
Invoice issuance. Rules (confirm with your CA):
- One tax invoice per order (OneToOne) — duplicates are impossible.
- Number format: <PREFIX>/<YYYY>/<NNNNN>, e.g. LLD/2627/00001 (≤16 chars,
  consecutive, unique per financial year April–March).
- When it is issued is set in Store settings (on dispatch by default).
- Values are copied from the order snapshot; issued invoices never change.
  Cancellations/returns after issue get a credit note.
"""
import logging
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.models import SiteSettings
from apps.core.utils import ZERO, amount_in_words, audit, money

from .models import CreditNote, Invoice, InvoiceItem, InvoiceSequence

logger = logging.getLogger(__name__)


class InvoiceError(Exception):
    pass


def financial_year_for(d: date) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def _short_fy(fy: str) -> str:
    return f"{fy[2:4]}{fy[-2:]}"


def _allocate(series, prefix, fy):
    """Return the next number in a series. Row-locked so concurrent issuers can't collide."""
    InvoiceSequence.objects.get_or_create(series=series, financial_year=fy, prefix=prefix)
    seq = InvoiceSequence.objects.select_for_update().get(series=series, financial_year=fy, prefix=prefix)
    number = seq.next_number
    seq.next_number = number + 1
    seq.save(update_fields=["next_number"])
    return number, f"{prefix}/{_short_fy(fy)}/{number:05d}"


def _seller_snapshot(store):
    return {
        "legal_name": store.legal_name, "trade_name": store.trade_name, "gstin": store.gstin,
        "pan": store.pan, "address": store.full_address, "state_code": store.state_code,
        "state": store.state_name, "email": store.support_email, "phone": store.support_phone,
    }


def maybe_issue_invoice(order, *, event, by=None):
    """Called from the order workflow; issues the invoice when the configured trigger fires."""
    store = SiteSettings.load()
    trigger = store.invoice_trigger
    should = (trigger == store.INVOICE_ON_DISPATCH and event == "shipped") or \
             (trigger == store.INVOICE_ON_PAYMENT and (event == "paid" and not order.is_cod
                                                       or event == "shipped" and order.is_cod))
    if not should or hasattr(order, "invoice"):
        return None
    if not store.can_issue_tax_invoice:
        logger.warning("Invoice for %s not issued — store GST details incomplete: %s",
                       order.order_number, ", ".join(store.missing_invoice_fields))
        return None
    try:
        return issue_invoice(order, by=by)
    except InvoiceError as exc:
        logger.warning("Invoice for %s not issued: %s", order.order_number, exc)
        return None


def issue_invoice(order, *, by=None, request=None):
    from apps.orders.models import Order

    store = SiteSettings.load()
    if not store.can_issue_tax_invoice:
        raise InvoiceError("Fill in GSTIN, legal name, address and state in Store settings before issuing "
                           "tax invoices: missing " + ", ".join(store.missing_invoice_fields))
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        existing = Invoice.objects.filter(order=order).first()
        if existing:
            return existing
        if order.status in (Order.PENDING, Order.CANCELLED):
            raise InvoiceError("Invoices can't be issued for pending or cancelled orders.")
        if order.payment_method != Order.METHOD_COD and not order.is_paid:
            raise InvoiceError("This order hasn't been paid yet.")
        today = timezone.localdate()
        fy = financial_year_for(today)
        seq_no, number = _allocate(InvoiceSequence.SERIES_INVOICE, store.invoice_prefix, fy)
        intra = order.place_of_supply == store.state_code
        invoice = Invoice.objects.create(
            order=order, number=number, financial_year=fy, sequence_no=seq_no, invoice_date=today,
            channel=order.channel, seller=_seller_snapshot(store), buyer_name=order.customer_name,
            buyer_gstin=order.customer_gstin, billing_address=order.billing_address,
            shipping_address=order.shipping_address, place_of_supply=order.place_of_supply,
            place_of_supply_name=order.place_of_supply_name, is_intra_state=intra,
            taxable_total=ZERO, cgst_total=ZERO, sgst_total=ZERO, igst_total=ZERO, tax_total=ZERO,
            discount_total=order.discount_total, round_off=order.round_off, grand_total=order.grand_total,
            amount_in_words=amount_in_words(order.grand_total), terms=store.invoice_terms,
            order_number=order.order_number, order_date=timezone.localtime(order.created_at).date(),
            payment_method=order.get_payment_method_display(), issued_by=by,
        )
        lines = []
        for idx, item in enumerate(order.items.all(), start=1):
            tax = item.tax_amount
            cgst, sgst, igst = _split(tax, intra)
            lines.append(InvoiceItem(
                invoice=invoice, line_no=idx, description=item.description, sku=item.sku, hsn_code=item.hsn_code,
                quantity=Decimal(item.quantity), unit=item.unit, unit_price=item.unit_price,
                gross_amount=item.line_gross, discount=item.discount, taxable_value=item.taxable_value,
                gst_rate=item.gst_rate, cgst=cgst, sgst=sgst, igst=igst, total=item.line_total))
        if order.shipping_total > 0:
            principal = max(order.items.all(), key=lambda i: i.gst_rate)
            cgst, sgst, igst = _split(order.shipping_tax, intra)
            desc = "Delivery charges" + (" incl. COD fee" if order.cod_fee else "")
            lines.append(InvoiceItem(
                invoice=invoice, line_no=len(lines) + 1, description=desc,
                hsn_code=store_shipping_hsn(store, principal.hsn_code), quantity=Decimal("1"), unit="NOS",
                unit_price=order.shipping_total, gross_amount=order.shipping_total, discount=ZERO,
                taxable_value=order.shipping_taxable, gst_rate=order.shipping_gst_rate, cgst=cgst, sgst=sgst,
                igst=igst, total=order.shipping_taxable + order.shipping_tax, is_shipping=True))
        InvoiceItem.objects.bulk_create(lines)
        invoice.taxable_total = money(sum(line.taxable_value for line in lines))
        invoice.cgst_total = money(sum(line.cgst for line in lines))
        invoice.sgst_total = money(sum(line.sgst for line in lines))
        invoice.igst_total = money(sum(line.igst for line in lines))
        invoice.tax_total = invoice.cgst_total + invoice.sgst_total + invoice.igst_total
        expected = invoice.taxable_total + invoice.tax_total + invoice.round_off
        if money(expected) != money(order.grand_total):
            raise InvoiceError(f"Invoice totals ({expected}) don't match the order ({order.grand_total}).")
        invoice.save(allow_update=True)
        audit("invoice.issue", f"Issued {invoice.number} for {order.order_number}", actor=by, obj=invoice,
              request=request)
        if store.email_invoice_to_customer:
            from apps.orders.services import email_customer

            email_customer(order, "invoice_issued", invoice=invoice, attachment_ref=f"invoice:{invoice.pk}")
    return invoice


def store_shipping_hsn(store, principal_hsn):
    return principal_hsn  # composite supply: delivery follows the principal goods' HSN


def _split(tax, intra):
    if intra:
        cgst = money(tax / 2)
        return cgst, tax - cgst, ZERO
    return ZERO, ZERO, tax


def credit_full_invoice(invoice, *, reason, by=None):
    """Credit whatever has not yet been credited on an invoice (cancellation / full return)."""
    already = {}
    for note in invoice.credit_notes.all():
        for line in note.lines:
            already[line["line_no"]] = already.get(line["line_no"], Decimal("0")) + Decimal(line["quantity"])
    quantities = {}
    for item in invoice.items.all():
        remaining = item.quantity - already.get(item.line_no, Decimal("0"))
        if remaining > 0:
            quantities[item.line_no] = remaining
    if not quantities:
        return None
    return issue_credit_note(invoice, quantities=quantities, reason=reason, by=by)


def issue_credit_note(invoice, *, quantities, reason, by=None, request=None):
    """
    `quantities`: {invoice line_no: quantity to credit}. Values are prorated from
    the invoice line, so tax always reverses at the original rate.
    """
    store = SiteSettings.load()
    quantities = {int(k): Decimal(str(v)) for k, v in quantities.items()}
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        credited = {}
        for note in invoice.credit_notes.all():
            for line in note.lines:
                credited[line["line_no"]] = credited.get(line["line_no"], Decimal("0")) + Decimal(line["quantity"])
        snapshot, totals = [], {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO}
        items = {i.line_no: i for i in invoice.items.all()}
        for line_no, qty in quantities.items():
            item = items.get(line_no)
            if item is None or qty <= 0:
                raise InvoiceError(f"Invalid line {line_no}.")
            if qty > item.quantity - credited.get(item.line_no, Decimal("0")):
                raise InvoiceError(f"Line {line_no}: cannot credit more than was invoiced.")
            share = qty / item.quantity
            taxable, cgst, sgst, igst = (money(item.taxable_value * share), money(item.cgst * share),
                                         money(item.sgst * share), money(item.igst * share))
            snapshot.append({"line_no": item.line_no, "description": item.description, "hsn_code": item.hsn_code,
                             "quantity": str(qty), "unit": item.unit, "gst_rate": str(item.gst_rate),
                             "taxable_value": str(taxable), "cgst": str(cgst), "sgst": str(sgst), "igst": str(igst),
                             "total": str(taxable + cgst + sgst + igst)})
            totals["taxable"] += taxable
            totals["cgst"] += cgst
            totals["sgst"] += sgst
            totals["igst"] += igst
        today = timezone.localdate()
        fy = financial_year_for(today)
        seq_no, number = _allocate(InvoiceSequence.SERIES_CREDIT, store.credit_note_prefix, fy)
        tax = totals["cgst"] + totals["sgst"] + totals["igst"]
        grand = totals["taxable"] + tax
        note = CreditNote.objects.create(
            invoice=invoice, number=number, financial_year=fy, sequence_no=seq_no, note_date=today,
            reason=reason[:200], lines=snapshot, taxable_total=totals["taxable"], cgst_total=totals["cgst"],
            sgst_total=totals["sgst"], igst_total=totals["igst"], tax_total=tax, grand_total=grand,
            amount_in_words=amount_in_words(grand), issued_by=by)
        fully = all(credited.get(n, 0) + quantities.get(n, 0) >= i.quantity for n, i in items.items())
        invoice.status = Invoice.STATUS_CREDITED if fully else Invoice.STATUS_PARTLY_CREDITED
        invoice.save(allow_update=True, update_fields=["status"])
        audit("creditnote.issue", f"Credit note {number} against {invoice.number}: {reason}", actor=by,
              obj=note, request=request)
    return note
