"""
Report queries. Day grouping is done in Python using the store's local time zone
(Asia/Kolkata) instead of database time-zone functions, because MySQL on shared
hosting often lacks time-zone tables.
"""
from collections import OrderedDict, defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.inventory.models import StockMovement
from apps.invoicing.models import CreditNote, Invoice
from apps.orders.models import Order, OrderItem
from apps.payments.models import Payment, Refund

ZERO = Decimal("0.00")
SALE_STATUSES_EXCLUDED = [Order.CANCELLED]


def local_day_bounds(day):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    return start, start + timedelta(days=1)


def range_bounds(start_day, end_day):
    return local_day_bounds(start_day)[0], local_day_bounds(end_day)[1]


def sales_orders(start, end):
    return Order.objects.filter(created_at__gte=start, created_at__lt=end).exclude(status__in=SALE_STATUSES_EXCLUDED)


def summary_widgets():
    today = timezone.localdate()
    t0, t1 = local_day_bounds(today)
    m0 = local_day_bounds(today.replace(day=1))[0]
    today_qs = sales_orders(t0, t1)
    month_qs = sales_orders(m0, t1)
    open_statuses = [Order.PENDING, Order.CONFIRMED, Order.PROCESSING]
    return {
        "today_orders": today_qs.count(),
        "today_sales": today_qs.aggregate(s=Sum("grand_total"))["s"] or ZERO,
        "month_sales": month_qs.aggregate(s=Sum("grand_total"))["s"] or ZERO,
        "month_orders": month_qs.count(),
        "total_orders": Order.objects.exclude(status=Order.CANCELLED).count(),
        "to_fulfil": Order.objects.filter(status__in=[Order.CONFIRMED, Order.PROCESSING]).count(),
        "awaiting_payment": Order.objects.filter(status=Order.PENDING).count(),
        "paid_orders": Order.objects.filter(payment_status=Order.PAY_PAID).exclude(status=Order.CANCELLED).count(),
        "unpaid_orders": Order.objects.filter(payment_status__in=[Order.PAY_PENDING, Order.PAY_FAILED])
        .exclude(status=Order.CANCELLED).count(),
        "cod_open": Order.objects.filter(payment_method=Order.METHOD_COD, payment_status=Order.PAY_PENDING)
        .exclude(status=Order.CANCELLED).count(),
        "open_statuses": open_statuses,
    }


def daily_sales(days=14):
    end_day = timezone.localdate()
    start_day = end_day - timedelta(days=days - 1)
    start, end = range_bounds(start_day, end_day)
    buckets = OrderedDict((start_day + timedelta(days=i), {"orders": 0, "sales": ZERO}) for i in range(days))
    for created, total in sales_orders(start, end).values_list("created_at", "grand_total"):
        day = timezone.localtime(created).date()
        buckets[day]["orders"] += 1
        buckets[day]["sales"] += total
    peak = max((b["sales"] for b in buckets.values()), default=ZERO) or Decimal("1")
    return [{"day": d, **b, "pct": int(b["sales"] / peak * 100)} for d, b in buckets.items()]


def sales_by_period(start_day, end_day, period="day"):
    start, end = range_bounds(start_day, end_day)
    rows = defaultdict(lambda: {"orders": 0, "gross": ZERO, "discount": ZERO, "taxable": ZERO, "tax": ZERO,
                                "shipping": ZERO, "total": ZERO})
    qs = sales_orders(start, end).values_list("created_at", "subtotal", "discount_total", "taxable_total",
                                              "tax_total", "shipping_total", "grand_total")
    for created, gross, disc, taxable, tax, ship, total in qs:
        d = timezone.localtime(created).date()
        key = d.strftime("%Y-%m") if period == "month" else d.isoformat()
        r = rows[key]
        r["orders"] += 1
        r["gross"] += gross
        r["discount"] += disc
        r["taxable"] += taxable
        r["tax"] += tax
        r["shipping"] += ship
        r["total"] += total
    return [{"period": k, **v} for k, v in sorted(rows.items())]


def product_sales(start_day, end_day):
    start, end = range_bounds(start_day, end_day)
    return (OrderItem.objects.filter(order__created_at__gte=start, order__created_at__lt=end)
            .exclude(order__status=Order.CANCELLED)
            .values("sku", "product_name", "variant_name")
            .annotate(qty=Sum("quantity"), taxable=Sum("taxable_value"), tax=Sum("tax_amount"),
                      total=Sum("line_total"), orders=Count("order", distinct=True))
            .order_by("-qty"))


def best_sellers(days=30, limit=5):
    end = timezone.localdate()
    return list(product_sales(end - timedelta(days=days), end)[:limit])


def status_summary():
    return (Order.objects.values("status").annotate(n=Count("id"), total=Sum("grand_total")).order_by("status"))


def invoice_register(start_day, end_day):
    return (Invoice.objects.filter(invoice_date__gte=start_day, invoice_date__lte=end_day)
            .order_by("invoice_date", "sequence_no"))


def credit_note_register(start_day, end_day):
    return CreditNote.objects.filter(note_date__gte=start_day, note_date__lte=end_day).select_related("invoice")


def tax_summary(start_day, end_day):
    """Rate-wise and HSN-wise totals from issued invoices, net of credit notes (for GSTR-1 prep)."""
    from apps.invoicing.models import InvoiceItem

    items = (InvoiceItem.objects.filter(invoice__invoice_date__gte=start_day, invoice__invoice_date__lte=end_day))
    by_rate = (items.values("gst_rate").annotate(taxable=Sum("taxable_value"), cgst=Sum("cgst"), sgst=Sum("sgst"),
                                                 igst=Sum("igst"), total=Sum("total")).order_by("gst_rate"))
    by_hsn = (items.values("hsn_code", "gst_rate").annotate(qty=Sum("quantity"), taxable=Sum("taxable_value"),
                                                            cgst=Sum("cgst"), sgst=Sum("sgst"), igst=Sum("igst"),
                                                            total=Sum("total")).order_by("hsn_code", "gst_rate"))
    notes = credit_note_register(start_day, end_day).aggregate(
        taxable=Sum("taxable_total"), cgst=Sum("cgst_total"), sgst=Sum("sgst_total"), igst=Sum("igst_total"),
        total=Sum("grand_total"))
    b2b = Invoice.objects.filter(invoice_date__gte=start_day, invoice_date__lte=end_day).exclude(buyer_gstin="").count()
    return {"by_rate": by_rate, "by_hsn": by_hsn, "credit_notes": {k: v or ZERO for k, v in notes.items()},
            "b2b_count": b2b}


def payment_reconciliation(start_day, end_day):
    start, end = range_bounds(start_day, end_day)
    payments = (Payment.objects.filter(created_at__gte=start, created_at__lt=end)
                .select_related("order").order_by("-created_at"))
    rows = []
    for p in payments:
        o = p.order
        issue = ""
        if p.status == Payment.CAPTURED and not o.is_paid and o.payment_status != Order.PAY_REFUNDED:
            issue = "Captured but order not marked paid"
        elif p.status == Payment.CAPTURED and p.amount != o.grand_total:
            issue = "Amount differs from order total"
        elif p.error_description.startswith("Duplicate capture"):
            issue = "Duplicate capture — refund"
        elif o.is_paid and o.status == Order.CANCELLED and o.refunded_total < o.grand_total:
            issue = "Paid but cancelled — refund due"
        rows.append({"payment": p, "order": o, "issue": issue})
    return rows


def refunds_and_cancellations(start_day, end_day):
    start, end = range_bounds(start_day, end_day)
    return {
        "refunds": Refund.objects.filter(created_at__gte=start, created_at__lt=end).select_related("order"),
        "cancelled": Order.objects.filter(cancelled_at__gte=start, cancelled_at__lt=end),
    }


def stock_movements(start_day, end_day, reason=""):
    start, end = range_bounds(start_day, end_day)
    qs = (StockMovement.objects.filter(created_at__gte=start, created_at__lt=end)
          .select_related("variant__product", "order", "created_by"))
    if reason:
        qs = qs.filter(reason=reason)
    return qs


def channel_breakdown(start_day, end_day):
    start, end = range_bounds(start_day, end_day)
    return sales_orders(start, end).values("channel").annotate(n=Count("id"), total=Sum("grand_total"))


def low_stock_variants():
    from django.db.models import F

    from apps.products.models import ProductVariant

    return (ProductVariant.objects.filter(is_active=True, stock__lte=F("low_stock_threshold"))
            .exclude(product__status="archived").select_related("product").order_by("stock"))


def needs_attention():
    from apps.orders.models import OrderRequest

    return {
        "requests": OrderRequest.objects.filter(status__in=["requested", "approved"]).select_related("order"),
        "refund_due": Order.objects.filter(Q(status=Order.CANCELLED) | Q(status=Order.RETURNED),
                                           payment_status=Order.PAY_PAID),
    }
