"""
Order workflow. Every state change goes through here so stock, payments,
invoices, history and emails stay consistent.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.cart.models import CouponError, CouponUsage
from apps.cart.pricing import build_quote
from apps.cart.services import cart_items
from apps.core.emails import notify_admins, send_email
from apps.core.models import SiteSettings
from apps.core.utils import audit
from apps.inventory.models import StockMovement
from apps.inventory.services import InsufficientStock, adjust_stock
from apps.products.models import ProductVariant

from .models import Order, OrderItem, OrderRequest, OrderStatusHistory

logger = logging.getLogger(__name__)


class CheckoutError(Exception):
    pass


class OrderActionError(Exception):
    pass


# ---------------------------------------------------------------- helpers
def record(order, field, from_value, to_value, note="", by=None, visible=True):
    OrderStatusHistory.objects.create(order=order, field=field, from_value=from_value or "",
                                      to_value=to_value or "", note=note[:300], changed_by=by,
                                      visible_to_customer=visible)


def email_customer(order, template, **context):
    """Queue a customer email to send after the transaction commits."""
    order_id = order.pk
    attachment_ref = context.pop("attachment_ref", "")

    def _send():
        fresh = Order.objects.prefetch_related("items").get(pk=order_id)
        send_email(template, fresh.email, {"order": fresh, **context}, related=f"order:{fresh.order_number}",
                   attachment_ref=attachment_ref)

    transaction.on_commit(_send)


def payments_available(store=None):
    store = store or SiteSettings.load()
    return bool(settings.PAYMENTS_ENABLED and store.online_payments_enabled)


def cod_available(store, amount):
    if not store.cod_enabled:
        return False, "Cash on Delivery is currently unavailable."
    if amount < store.cod_min_order:
        return False, f"Cash on Delivery is available on orders of ₹{store.cod_min_order} or more."
    if store.cod_max_order is not None and amount > store.cod_max_order:
        return False, f"Cash on Delivery is available on orders up to ₹{store.cod_max_order}."
    return True, ""


# ---------------------------------------------------------------- checkout
def _payment_due_by(store, payment_method):
    if payment_method == Order.METHOD_ONLINE:
        return timezone.now() + timedelta(minutes=store.payment_timeout_minutes)
    if payment_method in Order.MANUAL_METHODS:
        return timezone.now() + timedelta(hours=store.manual_payment_hours)
    return None


def place_order(*, user, cart, billing, shipping, payment_method, checkout_token, customer_gstin="",
                delivery_instructions=""):
    """
    Create an order from the cart. Safe against:
    - duplicate submissions (unique checkout_token → the first order is returned),
    - price tampering (everything is recomputed from the database),
    - overselling (variant rows locked; stock deducted in the same transaction).
    """
    store = SiteSettings.load()
    existing = Order.objects.filter(checkout_token=checkout_token).first()
    if existing:
        return existing
    if payment_method not in dict(Order.METHOD_CHOICES):
        raise CheckoutError("Please choose a payment method.")
    if payment_method == Order.METHOD_ONLINE and not payments_available(store):
        raise CheckoutError("Online payment is currently unavailable. Please choose another payment method.")
    if (payment_method == Order.METHOD_UPI and not store.upi_enabled) or \
            (payment_method == Order.METHOD_BANK and not store.bank_transfer_enabled):
        raise CheckoutError("That payment method is currently unavailable. Please choose another one.")
    if not store.is_pincode_serviceable(shipping["pincode"]):
        raise CheckoutError(f"Sorry, we don't deliver to PIN code {shipping['pincode']} yet.")

    try:
        with transaction.atomic():
            items = cart_items(cart)
            if not items:
                raise CheckoutError("Your cart is empty.")
            ids = sorted(i.variant_id for i in items)
            locked = {v.pk: v for v in ProductVariant.objects.select_for_update()
                      .select_related("product__tax_class").filter(pk__in=ids).order_by("pk")}
            lines = []
            for item in items:
                variant = locked[item.variant_id]
                if not variant.is_purchasable:
                    raise CheckoutError(f"“{variant}” is no longer available. Please review your cart.")
                if item.quantity > variant.stock:
                    raise CheckoutError(f"Only {variant.stock} of “{variant}” left. Please update your cart.")
                lines.append((variant, item.quantity))
            try:
                quote = build_quote(lines, store=store, coupon=cart.coupon, ship_state_code=shipping["state_code"],
                                    payment_method=payment_method, user=user)
            except CouponError as exc:
                raise CheckoutError(f"Coupon: {exc}")
            if payment_method == Order.METHOD_COD:
                ok, why = cod_available(store, quote.grand_total)
                if not ok:
                    raise CheckoutError(why)

            order = Order.objects.create(
                user=user, customer_name=billing["full_name"], email=user.email, phone=shipping["phone"],
                billing_address=billing, shipping_address=shipping, customer_gstin=customer_gstin.upper(),
                delivery_instructions=delivery_instructions[:300], place_of_supply=shipping["state_code"],
                is_intra_state=quote.is_intra_state, payment_method=payment_method,
                status=Order.CONFIRMED if payment_method == Order.METHOD_COD else Order.PENDING,
                payment_due_by=_payment_due_by(store, payment_method),
                prices_include_gst=quote.prices_include_gst, subtotal=quote.subtotal,
                discount_total=quote.discount, coupon_code=quote.coupon_code,
                shipping_total=quote.shipping_gross, cod_fee=quote.cod_fee,
                shipping_taxable=quote.shipping_taxable, shipping_tax=quote.shipping_tax,
                shipping_gst_rate=quote.shipping_gst_rate, taxable_total=quote.taxable_total,
                cgst_total=quote.cgst_total, sgst_total=quote.sgst_total, igst_total=quote.igst_total,
                tax_total=quote.tax_total, round_off=quote.round_off, grand_total=quote.grand_total,
                checkout_token=checkout_token,
            )
            for line in quote.lines:
                v = line.variant
                OrderItem.objects.create(
                    order=order, variant=v, sku=v.sku,
                    product_name=(f"FREE GIFT: {v.product.name}" if line.is_gift else v.product.name)[:150],
                    variant_name=v.name,
                    hsn_code=line.hsn_code, unit=v.product.unit, quantity=line.quantity, mrp=line.mrp,
                    unit_price=line.unit_price, line_gross=line.gross, discount=line.discount,
                    taxable_value=line.taxable, gst_rate=line.gst_rate, cgst=line.cgst, sgst=line.sgst,
                    igst=line.igst, tax_amount=line.tax, line_total=line.total, product_slug=v.product.slug)
                adjust_stock(v.pk, -line.quantity, StockMovement.SALE, order=order,
                             note=f"Order {order.order_number}", key=f"sale:{order.pk}:{v.pk}")
            if cart.coupon_id and quote.coupon_discount > 0:
                CouponUsage.objects.create(coupon=cart.coupon, user=user, order=order,
                                           discount_amount=quote.coupon_discount)
            record(order, "order", "", order.status, "Order placed")
            if payment_method == Order.METHOD_COD:
                from apps.payments.models import Payment

                Payment.objects.create(order=order, gateway=Payment.GATEWAY_COD, amount=order.grand_total)
                email_customer(order, "order_cod_confirmed")
            elif payment_method in Order.MANUAL_METHODS:
                email_customer(order, "order_awaiting_payment")
            cart.items.all().delete()
            cart.coupon = None
            cart.save(update_fields=["coupon"])
    except InsufficientStock as exc:
        raise CheckoutError(f"Only {exc.variant.stock} of “{exc.variant}” left. Please update your cart.")
    except IntegrityError:
        # Lost a race on the same checkout_token: return the order the other request created.
        existing = Order.objects.filter(checkout_token=checkout_token).first()
        if existing:
            return existing
        raise
    return order


# ---------------------------------------------------------------- stock
def release_stock(order, *, by=None, note="Order cancelled — stock restored"):
    """Return all items to stock once. Idempotent (flag + per-line ledger keys)."""
    if order.stock_released:
        return
    for item in order.items.all():
        if item.variant_id:
            adjust_stock(item.variant_id, item.quantity, StockMovement.CANCELLATION, order=order, user=by,
                         note=note, key=f"release:{order.pk}:{item.pk}")
    order.stock_released = True
    order.save(update_fields=["stock_released", "updated_at"])


# ---------------------------------------------------------------- transitions
def _lock(order):
    return Order.objects.select_for_update().get(pk=order.pk)


def cancel_order(order, *, by=None, reason="", restock=True, notify=True, request=None):
    from apps.invoicing.services import credit_full_invoice

    with transaction.atomic():
        order = _lock(order)
        if order.status == Order.CANCELLED:
            return order
        if not order.can_transition(Order.CANCELLED):
            raise OrderActionError(f"An order that is {order.get_status_display().lower()} cannot be cancelled.")
        old = order.status
        order.status = Order.CANCELLED
        order.cancelled_at = timezone.now()
        order.cancel_reason = reason[:300]
        order.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
        if restock:
            release_stock(order, by=by)
        CouponUsage.objects.filter(order=order).delete()
        record(order, "order", old, Order.CANCELLED, reason or "Cancelled", by=by)
        if hasattr(order, "invoice"):
            credit_full_invoice(order.invoice, reason=f"Order cancelled: {reason}"[:200], by=by)
        if by is not None and by.is_staff:
            audit("order.cancel", f"Cancelled {order.order_number}: {reason}", actor=by, obj=order, request=request)
        if notify:
            email_customer(order, "order_cancelled", reason=reason)
    return order


def change_status(order, new_status, *, by=None, note="", request=None, notify=True):
    from apps.invoicing.services import maybe_issue_invoice

    if new_status == Order.CANCELLED:
        return cancel_order(order, by=by, reason=note, request=request)
    with transaction.atomic():
        order = _lock(order)
        if order.status == new_status:
            return order
        if not order.can_transition(new_status):
            raise OrderActionError(f"Can't move from {order.get_status_display()} to "
                                   f"{dict(Order.STATUS_CHOICES)[new_status]}.")
        if new_status in (Order.CONFIRMED, Order.PROCESSING, Order.SHIPPED) and \
                order.payment_method != Order.METHOD_COD and not order.is_paid:
            raise OrderActionError("This order hasn't been paid yet. For direct UPI / bank orders, use "
                                   "“Confirm payment received” first.")
        old = order.status
        order.status = new_status
        fields = ["status", "updated_at"]
        if new_status == Order.SHIPPED:
            order.shipped_at = timezone.now()
            fields.append("shipped_at")
        elif new_status == Order.DELIVERED:
            order.delivered_at = timezone.now()
            fields.append("delivered_at")
        order.save(update_fields=fields)
        record(order, "order", old, new_status, note, by=by)
        if by is not None:
            audit("order.status", f"{order.order_number}: {old} → {new_status}", actor=by, obj=order,
                  request=request)
        if new_status == Order.SHIPPED:
            maybe_issue_invoice(order, event="shipped", by=by)
            if notify:
                email_customer(order, "order_shipped")
        elif new_status == Order.PROCESSING and notify:
            email_customer(order, "order_processing")
        elif new_status == Order.DELIVERED and notify:
            email_customer(order, "order_delivered")
    return order


def update_tracking(order, *, courier_name, tracking_number, tracking_url="", by=None, request=None):
    order.courier_name = courier_name[:80]
    order.tracking_number = tracking_number[:80]
    order.tracking_url = tracking_url
    order.save(update_fields=["courier_name", "tracking_number", "tracking_url", "updated_at"])
    record(order, "note", "", "", f"Tracking: {courier_name} {tracking_number}", by=by)
    audit("order.tracking", f"{order.order_number} tracking updated", actor=by, obj=order, request=request)


# ---------------------------------------------------------------- payments
def mark_paid(order, payment, *, by=None):
    """Idempotent. Returns True only on the first successful call."""
    from apps.invoicing.services import maybe_issue_invoice

    with transaction.atomic():
        order = _lock(order)
        if order.is_paid or order.payment_status == Order.PAY_REFUNDED:
            return False
        old_pay = order.payment_status
        order.payment_status = Order.PAY_PAID
        fields = ["payment_status", "updated_at"]
        if order.status == Order.CANCELLED:
            # Money arrived after we released the order (e.g. very late UPI confirmation).
            note = "Payment received after the order was cancelled — refund required."
            order.admin_notes = (order.admin_notes + "\n" + note).strip()
            fields.append("admin_notes")
            order.save(update_fields=fields)
            record(order, "payment", old_pay, Order.PAY_PAID, note, visible=False)
            transaction.on_commit(lambda: notify_admins("late_payment_alert", {"order": order},
                                                        related=f"order:{order.order_number}"))
            return True
        order.save(update_fields=fields)
        record(order, "payment", old_pay, Order.PAY_PAID, "Payment received", by=by)
        if order.status == Order.PENDING:
            order.status = Order.CONFIRMED
            order.save(update_fields=["status", "updated_at"])
            record(order, "order", Order.PENDING, Order.CONFIRMED, "Payment confirmed")
        email_customer(order, "payment_confirmed")
        maybe_issue_invoice(order, event="paid", by=by)
    return True


def mark_payment_failed(order, *, reason=""):
    with transaction.atomic():
        order = _lock(order)
        if order.payment_status != Order.PAY_PENDING:
            return
        order.payment_status = Order.PAY_FAILED
        order.save(update_fields=["payment_status", "updated_at"])
        record(order, "payment", Order.PAY_PENDING, Order.PAY_FAILED, reason[:200] or "Payment failed")
        email_customer(order, "payment_failed")


def mark_cod_collected(order, *, by, request=None):
    from apps.invoicing.services import maybe_issue_invoice
    from apps.payments.models import Payment

    with transaction.atomic():
        order = _lock(order)
        if not order.is_cod:
            raise OrderActionError("Only Cash on Delivery orders can be marked as collected.")
        if order.is_paid:
            return order
        if order.status not in (Order.SHIPPED, Order.DELIVERED):
            raise OrderActionError("Mark the order shipped or delivered before recording COD collection.")
        payment = order.payments.filter(gateway=Payment.GATEWAY_COD).first() or \
            Payment(order=order, gateway=Payment.GATEWAY_COD, amount=order.grand_total)
        payment.status = Payment.CAPTURED
        payment.captured_at = timezone.now()
        payment.recorded_by = by
        payment.save()
        order.payment_status = Order.PAY_PAID
        order.cod_collected_at = timezone.now()
        order.save(update_fields=["payment_status", "cod_collected_at", "updated_at"])
        record(order, "payment", Order.PAY_PENDING, Order.PAY_PAID, "Cash collected on delivery", by=by)
        audit("order.cod_collected", f"COD collected for {order.order_number} (₹{order.grand_total})",
              actor=by, obj=order, request=request)
        maybe_issue_invoice(order, event="paid", by=by)
    return order


def submit_payment_reference(order, user, reference):
    """Customer reports a direct UPI / bank payment. Staff still confirm the money actually arrived."""
    reference = "".join(reference.split())[:40]
    if not (6 <= len(reference) <= 40) or not reference.replace("-", "").isalnum():
        raise OrderActionError("Enter the UTR / transaction reference exactly as shown in your bank or UPI app "
                               "(letters and numbers only).")
    with transaction.atomic():
        order = _lock(order)
        if order.user_id != user.pk:
            raise OrderActionError("Order not found.")
        if not order.awaiting_manual_payment:
            raise OrderActionError("This order is not waiting for a payment reference.")
        old = order.payment_reference
        order.payment_reference = reference
        order.save(update_fields=["payment_reference", "updated_at"])
        record(order, "payment", old, reference, "Customer entered payment reference — waiting for us to check")
        transaction.on_commit(lambda: notify_admins("manual_payment_submitted", {"order": order},
                                                    related=f"order:{order.order_number}"))
    return order


def confirm_manual_payment(order, *, by, reference="", request=None):
    """Staff confirm a direct UPI / bank payment reached the account. Idempotent."""
    from apps.payments.models import Payment

    with transaction.atomic():
        order = _lock(order)
        if not order.is_manual_payment:
            raise OrderActionError("Only direct UPI / bank transfer orders are confirmed here.")
        if order.is_paid:
            return order
        if order.status == Order.CANCELLED:
            raise OrderActionError("This order was cancelled (payment time ran out). Place a new order for the "
                                   "customer, or refund the money.")
        reference = ("".join(reference.split()) or order.payment_reference)[:40]
        if not reference:
            raise OrderActionError("Enter the UTR / reference number you see in your bank statement.")
        gateway = Payment.GATEWAY_UPI if order.payment_method == Order.METHOD_UPI else Payment.GATEWAY_BANK
        payment = Payment.objects.create(order=order, gateway=gateway, amount=order.grand_total,
                                         status=Payment.CAPTURED, captured_at=timezone.now(), recorded_by=by,
                                         method_detail=f"ref {reference}"[:30])
        if order.payment_reference != reference:
            order.payment_reference = reference
            order.save(update_fields=["payment_reference", "updated_at"])
        audit("order.manual_payment_confirmed",
              f"{order.get_payment_method_display()} ₹{order.grand_total} confirmed for {order.order_number} "
              f"(ref {reference})", actor=by, obj=order, request=request)
    mark_paid(order, payment, by=by)
    order.refresh_from_db()
    return order


def expire_unpaid_orders(now=None):
    """Cancel unpaid orders whose payment window has passed.

    Online: checks the gateway first. Direct UPI / bank: only orders where the customer never entered a
    payment reference — once a reference exists, staff must check the bank statement and decide.
    """
    from apps.payments.services import reconcile_order

    now = now or timezone.now()
    manual = Order.objects.filter(payment_method__in=Order.MANUAL_METHODS, status=Order.PENDING,
                                  payment_status=Order.PAY_PENDING, payment_reference="", payment_due_by__lt=now)
    manual_cancelled = 0
    for order in manual:
        cancel_order(order, reason="Payment was not received in time.", notify=True)
        manual_cancelled += 1
    expired = Order.objects.filter(payment_method=Order.METHOD_ONLINE, status=Order.PENDING,
                                   payment_status__in=[Order.PAY_PENDING, Order.PAY_FAILED],
                                   payment_due_by__lt=now)
    cancelled = 0
    for order in expired:
        try:
            reconcile_order(order)
        except Exception:  # gateway unreachable: skip this round rather than cancel a paid order
            logger.exception("Could not reconcile %s; will retry", order.order_number)
            continue
        order.refresh_from_db()
        if order.is_paid:
            continue
        cancel_order(order, reason="Payment was not completed in time.", notify=True)
        cancelled += 1
    return cancelled + manual_cancelled


# ---------------------------------------------------------------- customer requests
def customer_cancel(order, user, reason):
    """Unpaid/COD orders cancel immediately; paid ones become a request for staff (refund needed)."""
    store = SiteSettings.load()
    if order.user_id != user.pk:
        raise OrderActionError("Not your order.")
    if not order.customer_cancellable(store):
        raise OrderActionError("This order can no longer be cancelled online. Please contact us.")
    # A direct UPI/bank order with a reference may already be paid: staff must check before cancelling.
    if order.is_paid or (order.is_manual_payment and order.payment_reference):
        if order.requests.filter(kind=OrderRequest.KIND_CANCEL, status=OrderRequest.REQUESTED).exists():
            raise OrderActionError("A cancellation request is already pending.")
        req = OrderRequest.objects.create(order=order, kind=OrderRequest.KIND_CANCEL, reason=reason[:500])
        record(order, "note", "", "", "Cancellation requested by customer")
        transaction.on_commit(lambda: notify_admins("staff_request_alert", {"req": req},
                                                    related=f"order:{order.order_number}"))
        return req
    return cancel_order(order, by=user, reason=f"Cancelled by customer: {reason}"[:300])


def customer_request_return(order, user, reason, problem="", photos=()):
    """Customer reports a problem with a delivered order (damaged, wrong, missing...). Staff then decide."""
    from .models import RequestPhoto

    store = SiteSettings.load()
    if order.user_id != user.pk:
        raise OrderActionError("Not your order.")
    if not order.return_window_open(store):
        raise OrderActionError("The time to report a problem with this order has passed. Please contact us.")
    with transaction.atomic():
        req = OrderRequest.objects.create(order=order, kind=OrderRequest.KIND_RETURN, reason=reason[:500],
                                          problem=problem)
        for f in list(photos)[:RequestPhoto.MAX_PER_REQUEST]:
            RequestPhoto.objects.create(request=req, image=f)
        order.status = Order.RETURN_REQUESTED
        order.save(update_fields=["status", "updated_at"])
        label = req.get_problem_display() if problem else "Return requested"
        record(order, "order", Order.DELIVERED, Order.RETURN_REQUESTED, f"Problem reported by customer: {label}")
        transaction.on_commit(lambda: notify_admins("staff_request_alert", {"req": req},
                                                    related=f"order:{order.order_number}"))
    return req


def resolve_request(req, *, approve, by, note="", request=None):
    """Approve/reject. Approving a cancel cancels + refunds; approving a return waits for the parcel."""
    from apps.payments.services import refund_order

    with transaction.atomic():
        req = OrderRequest.objects.select_for_update().get(pk=req.pk)
        if req.status != OrderRequest.REQUESTED:
            raise OrderActionError("This request has already been handled.")
        req.handled_by = by
        req.staff_note = note[:500]
        order = req.order
        if not approve:
            req.status = OrderRequest.REJECTED
            req.save()
            if req.kind == OrderRequest.KIND_RETURN and order.status == Order.RETURN_REQUESTED:
                change_status(order, Order.DELIVERED, by=by, note=f"Return rejected: {note}", notify=False)
            email_customer(order, "request_update", req=req)
            audit("order.request_reject", f"Rejected {req.kind} for {order.order_number}", actor=by, obj=order,
                  request=request)
            return req
        if req.kind == OrderRequest.KIND_CANCEL:
            cancel_order(order, by=by, reason=f"Cancellation approved: {req.reason}"[:300], request=request)
            order.refresh_from_db()
            if order.is_paid:
                refund_order(order, amount=order.grand_total - order.refunded_total,
                             reason="Order cancelled", by=by)
            req.status = OrderRequest.COMPLETED
        else:
            req.status = OrderRequest.APPROVED
        req.save()
        email_customer(order, "request_update", req=req)
        audit("order.request_approve", f"Approved {req.kind} for {order.order_number}", actor=by, obj=order,
              request=request)
    return req


def complete_return(req, *, by, restock, refund_amount=None, note="", request=None):
    """After the parcel is back and inspected: restock (only if approved), credit note, refund."""
    from apps.invoicing.services import credit_full_invoice
    from apps.payments.services import refund_order

    with transaction.atomic():
        req = OrderRequest.objects.select_for_update().get(pk=req.pk)
        if req.kind != OrderRequest.KIND_RETURN or req.status != OrderRequest.APPROVED:
            raise OrderActionError("Only approved returns can be completed.")
        order = _lock(req.order)
        if restock:
            for item in order.items.all():
                if item.variant_id:
                    adjust_stock(item.variant_id, item.quantity, StockMovement.RETURN_RESTOCK, order=order,
                                 user=by, note="Return inspected and restocked",
                                 key=f"return:{order.pk}:{item.pk}")
        order.status = Order.RETURNED
        order.save(update_fields=["status", "updated_at"])
        record(order, "order", Order.RETURN_REQUESTED, Order.RETURNED, note or "Return received", by=by)
        if hasattr(order, "invoice"):
            credit_full_invoice(order.invoice, reason="Goods returned", by=by)
        amount = order.grand_total - order.refunded_total if refund_amount is None else refund_amount
        if order.is_paid and amount > 0:
            refund_order(order, amount=amount, reason="Return", by=by)
        req.status = OrderRequest.COMPLETED
        req.restock_approved = restock
        req.staff_note = (req.staff_note + " " + note).strip()[:500]
        req.handled_by = by
        req.save()
        audit("order.return_complete", f"Return completed for {order.order_number}; restock={restock}",
              actor=by, obj=order, request=request)
        email_customer(order, "request_update", req=req)
    return req
