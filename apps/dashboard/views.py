import csv
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.core.codes import barcode_svg
from apps.core.models import SiteSettings
from apps.core.utils import audit
from apps.inventory.models import StockMovement
from apps.inventory.services import InsufficientStock, adjust_stock
from apps.invoicing.services import InvoiceError, issue_credit_note, issue_invoice
from apps.orders.models import Order, OrderRequest
from apps.orders.services import (OrderActionError, cancel_order, change_status, complete_return,
                                  confirm_manual_payment, mark_cod_collected, record, resolve_request,
                                  update_tracking)
from apps.payments.gateways import GatewayError
from apps.payments.services import PaymentError, reconcile_order, refund_order
from apps.products.models import Product, ProductVariant, TaxClass
from apps.shipping.client import ShippingError
from apps.shipping.models import Shipment
from apps.shipping.services import book as book_shipment
from apps.shipping.services import couriers_for, fetch_label
from apps.shipping.services import refresh as refresh_tracking

from . import reports, storage


def staff_required(perm=None):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_staff:
                raise PermissionDenied
            if perm and not request.user.has_perm(perm):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def _dates(request, default_days=30):
    today = timezone.localdate()
    try:
        end = date.fromisoformat(request.GET.get("to", ""))
    except ValueError:
        end = today
    try:
        start = date.fromisoformat(request.GET.get("from", ""))
    except ValueError:
        start = end - timedelta(days=default_days - 1)
    if start > end:
        start, end = end, start
    if (end - start).days > 731:
        start = end - timedelta(days=731)
    return start, end


def _csv(filename, header, rows):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write("﻿")  # Excel-friendly UTF-8
    writer = csv.writer(resp)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_csv_safe(v) for v in row])
    return resp


def _csv_safe(value):
    """Neutralise spreadsheet formula injection in text cells."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


# ---------------------------------------------------------------- home
@staff_required()
@require_GET
def home(request):
    store = SiteSettings.load()
    warnings = []
    if not store.can_issue_tax_invoice:
        warnings.append("Tax invoices are blocked until GST details are complete in Store settings: "
                        + ", ".join(store.missing_invoice_fields) + ".")
    unconfirmed = TaxClass.objects.filter(confirmed_by_ca=False).count()
    if unconfirmed:
        warnings.append(f"{unconfirmed} tax class(es) (HSN & GST rate) are not yet confirmed by your CA.")
    if not settings.PAYMENTS_ENABLED:
        warnings.append("Razorpay keys are not set — only Cash on Delivery is offered at checkout.")
    elif settings.RAZORPAY_KEY_ID.startswith("rzp_test_"):
        warnings.append("Razorpay is in TEST mode — no real money is collected.")
    if not settings.EMAIL_HOST and "smtp" in settings.EMAIL_BACKEND:
        warnings.append("Email (SMTP) is not configured — customers won't receive emails.")
    today = timezone.localdate()
    space = storage.usage(refresh=request.GET.get("storage") == "refresh") \
        if request.user.has_perm("core.view_sitesettings") else None
    if space and space["percent"] is not None and space["percent"] >= 80:
        warnings.append(f"Hosting space is {space['percent']}% full ({space['total_mb']} of {space['quota_mb']} MB). "
                        "Remove unused videos/photos or use YouTube links for videos.")
    return render(request, "dashboard/home.html", {
        "w": reports.summary_widgets(), "daily": reports.daily_sales(14), "best": reports.best_sellers(),
        "low_stock": reports.low_stock_variants()[:10], "attention": reports.needs_attention(),
        "channels": reports.channel_breakdown(today.replace(day=1), today), "warnings": warnings,
        "recent": Order.objects.order_by("-created_at")[:8], "space": space,
    })


# ---------------------------------------------------------------- orders
@staff_required("orders.manage_orders")
@require_GET
def order_list(request):
    qs = Order.objects.all().order_by("-created_at")
    status = request.GET.get("status", "")
    pay = request.GET.get("payment", "")
    method = request.GET.get("method", "")
    q = (request.GET.get("q") or "").strip()
    if status:
        qs = qs.filter(status=status)
    if pay:
        qs = qs.filter(payment_status=pay)
    if method:
        qs = qs.filter(payment_method=method)
    if q:
        qs = qs.filter(Q(order_number__icontains=q) | Q(email__icontains=q) | Q(customer_name__icontains=q)
                       | Q(phone__icontains=q) | Q(tracking_number__icontains=q))
    if request.GET.get("format") == "csv":
        return _csv("orders.csv", ["Order", "Date", "Customer", "Email", "Phone", "Method", "Status", "Payment",
                                   "Taxable", "Tax", "Total", "Channel"],
                    [(o.order_number, timezone.localtime(o.created_at).strftime("%Y-%m-%d %H:%M"), o.customer_name,
                      o.email, o.phone, o.payment_method, o.status, o.payment_status, o.taxable_total, o.tax_total,
                      o.grand_total, o.channel) for o in qs])
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "dashboard/orders.html", {
        "page_obj": page, "orders": page.object_list, "status": status, "pay": pay, "method": method, "q": q,
        "statuses": Order.STATUS_CHOICES, "pay_statuses": Order.PAYMENT_STATUS_CHOICES,
        "methods": Order.METHOD_CHOICES, "querystring": params.urlencode(),
    })


@staff_required("orders.manage_orders")
@require_GET
def order_detail(request, order_number):
    order = get_object_or_404(Order.objects.prefetch_related("items", "history__changed_by", "payments",
                                                             "refunds", "requests"), order_number=order_number)
    invoice = getattr(order, "invoice", None)
    shipment = Shipment.objects.filter(order=order).prefetch_related("events").first()
    couriers = None
    if request.GET.get("couriers") == "1" and settings.SHIPPING_ENABLED and not (shipment and shipment.awb):
        try:
            couriers = couriers_for(order)
        except ShippingError as exc:
            messages.error(request, str(exc))
    return render(request, "dashboard/order_detail.html", {
        "order": order, "invoice": invoice,
        "shipment": shipment, "couriers": couriers, "shipping_enabled": settings.SHIPPING_ENABLED,
        "credit_notes": invoice.credit_notes.all() if invoice else [],
        "next_statuses": [(s, label) for s, label in Order.STATUS_CHOICES
                          if order.can_transition(s) and s not in (Order.RETURN_REQUESTED, Order.RETURNED)],
        "store": SiteSettings.load(),
        "refundable": order.grand_total - order.refunded_total,
        "can_refund": request.user.has_perm("orders.issue_refunds"),
        "can_cancel": order.can_transition(Order.CANCELLED),
    })


def _back(order):
    return redirect("dashboard:order_detail", order_number=order.order_number)


@staff_required("orders.manage_orders")
@require_POST
def order_action(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    action = request.POST.get("action")
    note = (request.POST.get("note") or "").strip()[:300]
    try:
        if action == "status":
            change_status(order, request.POST.get("status", ""), by=request.user, note=note, request=request)
            messages.success(request, "Status updated.")
        elif action == "cancel":
            if not note:
                raise OrderActionError("Enter a reason for cancelling.")
            cancel_order(order, by=request.user, reason=note, request=request)
            messages.success(request, "Order cancelled and stock restored.")
        elif action == "tracking":
            update_tracking(order, courier_name=request.POST.get("courier_name", "").strip(),
                            tracking_number=request.POST.get("tracking_number", "").strip(),
                            tracking_url=request.POST.get("tracking_url", "").strip()
                            if request.POST.get("tracking_url", "").startswith("https://") else "",
                            by=request.user, request=request)
            messages.success(request, "Tracking details saved.")
        elif action == "cod_collected":
            mark_cod_collected(order, by=request.user, request=request)
            messages.success(request, "Cash collection recorded.")
        elif action == "ship_book":
            courier_id = (request.POST.get("courier_id") or "").strip()
            if not courier_id.isdigit():
                raise OrderActionError("Choose a courier first.")
            shipment, notes = book_shipment(order, courier_id=courier_id, by=request.user)
            messages.success(request, f"Booked with {shipment.courier_name} — AWB {shipment.awb}. Print the label "
                                      "and keep the parcel ready for pickup. Status will now update automatically.")
            for note in notes:
                messages.warning(request, note)
        elif action == "ship_label":
            shipment = get_object_or_404(Shipment, order=order)
            return redirect(fetch_label(shipment))
        elif action == "ship_refresh":
            shipment = get_object_or_404(Shipment, order=order)
            n = refresh_tracking(shipment)
            messages.info(request, f"{n} new courier update(s)." if n else "No new courier updates.")
        elif action == "manual_paid":
            confirm_manual_payment(order, by=request.user, reference=request.POST.get("reference", ""),
                                   request=request)
            messages.success(request, "Payment confirmed. The order is now Confirmed and the customer has been "
                                      "emailed.")
        elif action == "invoice":
            if not request.user.has_perm("invoicing.add_invoice"):
                raise PermissionDenied
            invoice = issue_invoice(order, by=request.user, request=request)
            messages.success(request, f"Invoice {invoice.number} issued.")
        elif action == "note":
            if note:
                record(order, "note", "", "", note, by=request.user, visible=False)
                messages.success(request, "Note added.")
        elif action == "reconcile":
            changed = reconcile_order(order)
            messages.info(request, "Payment status updated from Razorpay." if changed
                          else "No change — Razorpay shows nothing new for this order.")
        elif action == "refund":
            if not request.user.has_perm("orders.issue_refunds"):
                raise PermissionDenied
            try:
                amount = Decimal(request.POST.get("amount", ""))
            except InvalidOperation:
                raise OrderActionError("Enter a valid refund amount.")
            refund = refund_order(order, amount=amount, reason=note or "Refund", by=request.user,
                                  manual_reference=(request.POST.get("reference") or "").strip(), request=request)
            messages.success(request, f"Refund of ₹{refund.amount} recorded ({refund.get_status_display()}).")
        elif action == "credit_note":
            invoice = getattr(order, "invoice", None)
            if invoice is None:
                raise OrderActionError("No invoice to credit.")
            quantities = {}
            for item in invoice.items.all():
                raw = request.POST.get(f"cn_qty_{item.line_no}", "").strip()
                if raw:
                    quantities[item.line_no] = Decimal(raw)
            if not quantities:
                raise OrderActionError("Enter the quantity to credit on at least one line.")
            cn = issue_credit_note(invoice, quantities=quantities, reason=note or "Adjustment", by=request.user,
                                   request=request)
            messages.success(request, f"Credit note {cn.number} issued.")
        else:
            messages.error(request, "Unknown action.")
    except (OrderActionError, InvoiceError, PaymentError, GatewayError, InsufficientStock, ShippingError) as exc:
        messages.error(request, str(exc))
    except (InvalidOperation, ValueError):
        messages.error(request, "Please check the numbers you entered.")
    return _back(order)


@staff_required("orders.manage_orders")
@require_POST
def request_action(request, pk):
    req = get_object_or_404(OrderRequest, pk=pk)
    action = request.POST.get("action")
    note = (request.POST.get("note") or "").strip()
    try:
        if action in ("approve", "reject"):
            if action == "approve" and req.kind == OrderRequest.KIND_CANCEL and req.order.is_paid and \
                    not request.user.has_perm("orders.issue_refunds"):
                raise PermissionDenied
            resolve_request(req, approve=action == "approve", by=request.user, note=note, request=request)
            messages.success(request, "Request updated.")
        elif action == "complete_return":
            if not request.user.has_perm("orders.issue_refunds"):
                raise PermissionDenied
            restock = request.POST.get("restock") == "yes"
            raw = request.POST.get("refund_amount", "").strip()
            complete_return(req, by=request.user, restock=restock, refund_amount=Decimal(raw) if raw else None,
                            note=note, request=request)
            messages.success(request, "Return completed." + (" Items restocked." if restock else
                                                             " Items NOT restocked."))
    except (OrderActionError, PaymentError, GatewayError, InvoiceError) as exc:
        messages.error(request, str(exc))
    except InvalidOperation:
        messages.error(request, "Enter a valid refund amount.")
    return _back(req.order)


@staff_required("orders.manage_orders")
@require_GET
def packing_slip(request, order_number):
    order = get_object_or_404(Order.objects.prefetch_related("items"), order_number=order_number)
    return render(request, "dashboard/packing_slip.html", {"order": order})


@staff_required("orders.manage_orders")
@require_GET
def shipping_label(request, order_number):
    """4 × 6 inch label to stick on the parcel. Courier sort codes need a courier integration."""
    order = get_object_or_404(Order.objects.prefetch_related("items__variant"), order_number=order_number)
    items = list(order.items.all())
    grams = sum((i.variant.weight_grams if i.variant else 0) * i.quantity for i in items)
    return render(request, "dashboard/shipping_label.html", {
        "order": order, "items": items, "store": SiteSettings.load(),
        "invoice": getattr(order, "invoice", None),
        "weight_kg": f"{grams / 1000:.2f}" if grams else "",
        "awb_barcode": barcode_svg(order.tracking_number) if order.tracking_number else None,
        "order_barcode": barcode_svg(order.order_number, height=34),
        "cod_due": order.is_cod and not order.is_paid,
        "today": timezone.localdate(),
    })


# ---------------------------------------------------------------- inventory
@staff_required("inventory.view_stockmovement")
@require_GET
def inventory(request):
    qs = ProductVariant.objects.select_related("product").exclude(product__status=Product.STATUS_ARCHIVED)
    if request.GET.get("low") == "1":
        qs = qs.filter(stock__lte=F("low_stock_threshold"))
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(sku__icontains=q) | Q(product__name__icontains=q))
    qs = qs.order_by("stock", "product__name")
    if request.GET.get("format") == "csv":
        cost = request.user.has_perm("products.view_cost_price")
        header = ["SKU", "Product", "Variant", "Stock", "Low-stock threshold", "Price"] + (["Cost"] if cost else [])
        return _csv("inventory.csv", header, [
            [v.sku, v.product.name, v.name, v.stock, v.low_stock_threshold, v.price] + ([v.cost_price] if cost else [])
            for v in qs])
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "dashboard/inventory.html", {
        "page_obj": page, "variants": page.object_list, "q": q, "low": request.GET.get("low") == "1",
        "reasons": [(k, v) for k, v in StockMovement.REASON_CHOICES
                    if k in (StockMovement.PURCHASE, StockMovement.ADJUSTMENT, StockMovement.DAMAGE)],
    })


@staff_required("inventory.add_stockmovement")
@require_POST
def stock_adjust(request):
    variant = get_object_or_404(ProductVariant, pk=request.POST.get("variant_id"))
    reason = request.POST.get("reason")
    if reason not in (StockMovement.PURCHASE, StockMovement.ADJUSTMENT, StockMovement.DAMAGE):
        messages.error(request, "Choose a reason.")
        return redirect("dashboard:inventory")
    try:
        change = int((request.POST.get("change") or "0").replace("−", "-").replace(" ", ""))
    except ValueError:
        change = 0
    if change == 0:
        messages.error(request, "Enter a non-zero quantity (use minus for removals).")
        return redirect("dashboard:inventory")
    if reason == StockMovement.DAMAGE and change > 0:
        change = -change
    note = (request.POST.get("note") or "").strip()[:200]
    try:
        movement = adjust_stock(variant.pk, change, reason, user=request.user, note=note)
    except InsufficientStock as exc:
        messages.error(request, str(exc))
        return redirect("dashboard:inventory")
    audit("stock.adjust", f"{variant.sku}: {movement.change:+d} ({reason}) → {movement.stock_after}",
          actor=request.user, obj=variant, request=request)
    messages.success(request, f"{variant.sku}: stock now {movement.stock_after}.")
    return redirect(request.POST.get("next") if request.POST.get("next", "").startswith("/dashboard/")
                    else "dashboard:inventory")


# ---------------------------------------------------------------- reports
REPORTS = [
    ("sales", "Daily & monthly sales"), ("products", "Product-wise sales"), ("status", "Order status summary"),
    ("payments", "Payment reconciliation"), ("invoices", "Invoice register"), ("tax", "Tax summary"),
    ("refunds", "Refunds & cancellations"), ("stock", "Stock movements"),
]


@staff_required("orders.view_reports")
@require_GET
def report(request, kind="sales"):
    if kind not in dict(REPORTS):
        kind = "sales"
    start, end = _dates(request)
    csv_mode = request.GET.get("format") == "csv"
    ctx = {"kind": kind, "reports": REPORTS, "start": start, "end": end, "title": dict(REPORTS)[kind]}
    fname = f"{kind}_{start}_{end}.csv"

    if kind == "sales":
        period = "month" if request.GET.get("period") == "month" else "day"
        rows = reports.sales_by_period(start, end, period)
        if csv_mode:
            return _csv(fname, ["Period", "Orders", "Gross", "Discount", "Shipping", "Taxable", "GST", "Total"],
                        [(r["period"], r["orders"], r["gross"], r["discount"], r["shipping"], r["taxable"], r["tax"],
                          r["total"]) for r in rows])
        ctx.update(rows=rows, period=period, channels=reports.channel_breakdown(start, end))
    elif kind == "products":
        rows = reports.product_sales(start, end)
        if csv_mode:
            return _csv(fname, ["SKU", "Product", "Variant", "Qty", "Orders", "Taxable", "GST", "Total"],
                        [(r["sku"], r["product_name"], r["variant_name"], r["qty"], r["orders"], r["taxable"],
                          r["tax"], r["total"]) for r in rows])
        ctx.update(rows=rows)
    elif kind == "status":
        ctx.update(rows=reports.status_summary(), labels=dict(Order.STATUS_CHOICES))
    elif kind == "payments":
        rows = reports.payment_reconciliation(start, end)
        if csv_mode:
            return _csv(fname, ["Date", "Order", "Gateway", "Gateway order", "Gateway payment", "Amount", "Status",
                                "Order payment status", "Issue"],
                        [(timezone.localtime(r["payment"].created_at).strftime("%Y-%m-%d %H:%M"),
                          r["order"].order_number, r["payment"].gateway, r["payment"].gateway_order_id or "",
                          r["payment"].gateway_payment_id or "", r["payment"].amount, r["payment"].status,
                          r["order"].payment_status, r["issue"]) for r in rows])
        ctx.update(rows=rows)
    elif kind == "invoices":
        rows = reports.invoice_register(start, end)
        notes = reports.credit_note_register(start, end)
        if csv_mode:
            data = [(i.invoice_date, i.number, "Invoice", i.order_number, i.channel, i.buyer_name, i.buyer_gstin,
                     i.place_of_supply, i.taxable_total, i.cgst_total, i.sgst_total, i.igst_total, i.grand_total)
                    for i in rows]
            data += [(n.note_date, n.number, "Credit note", n.invoice.number, n.invoice.channel, n.invoice.buyer_name,
                      n.invoice.buyer_gstin, n.invoice.place_of_supply, -n.taxable_total, -n.cgst_total,
                      -n.sgst_total, -n.igst_total, -n.grand_total) for n in notes]
            return _csv(fname, ["Date", "Number", "Type", "Order / against", "Channel", "Customer", "Customer GSTIN",
                                "Place of supply", "Taxable", "CGST", "SGST", "IGST", "Total"], data)
        ctx.update(rows=rows, notes=notes)
    elif kind == "tax":
        data = reports.tax_summary(start, end)
        if csv_mode:
            return _csv(fname, ["HSN", "GST rate", "Qty", "Taxable", "CGST", "SGST", "IGST", "Total"],
                        [(r["hsn_code"], r["gst_rate"], r["qty"], r["taxable"], r["cgst"], r["sgst"], r["igst"],
                          r["total"]) for r in data["by_hsn"]])
        ctx.update(data=data)
    elif kind == "refunds":
        data = reports.refunds_and_cancellations(start, end)
        if csv_mode:
            return _csv(fname, ["Date", "Order", "Amount", "Method", "Status", "Reference", "Reason"],
                        [(timezone.localtime(r.created_at).strftime("%Y-%m-%d"), r.order.order_number, r.amount,
                          r.method, r.status, r.gateway_refund_id or r.reference, r.reason) for r in data["refunds"]])
        ctx.update(data=data)
    elif kind == "stock":
        reason = request.GET.get("reason", "")
        rows = reports.stock_movements(start, end, reason)
        if csv_mode:
            return _csv(fname, ["Date", "SKU", "Product", "Change", "Before", "After", "Reason", "Order", "By", "Note"],
                        [(timezone.localtime(m.created_at).strftime("%Y-%m-%d %H:%M"), m.variant.sku,
                          m.variant.product.name, m.change, m.stock_before, m.stock_after, m.reason,
                          m.order.order_number if m.order else "", m.created_by.email if m.created_by else "system",
                          m.note) for m in rows])
        ctx.update(rows=rows[:500], reason=reason, reasons=StockMovement.REASON_CHOICES)
    return render(request, "dashboard/report.html", ctx)
