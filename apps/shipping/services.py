"""
Booking parcels with Shiprocket and turning courier updates into order updates.

Flow: staff press "Book shipment" → order created in Shiprocket → AWB assigned → pickup requested →
label link saved. After that, Shiprocket's webhook (and an hourly check as backup) moves the order to
Shipped / Delivered automatically and emails the customer. RTO (returning parcels) are flagged for staff.
"""
import hashlib
import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .client import ShippingError, call
from .models import Shipment, ShipmentEvent

logger = logging.getLogger(__name__)
PUBLIC_TRACKING_URL = "https://shiprocket.co/tracking/{awb}"
_RANK = {Shipment.BOOKED: 0, Shipment.IN_TRANSIT: 1, Shipment.DELIVERED: 2, Shipment.RTO: 2, Shipment.CANCELLED: 3}


# ---------------------------------------------------------------- helpers
def _money(value):
    return float(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parcel_weight_kg(order, store):
    grams = sum((i.variant.weight_grams if i.variant else 0) * i.quantity for i in order.items.all())
    return Decimal(max(grams or store.parcel_default_weight_g, 10)) / 1000


def classify(status_text):
    """Map Shiprocket's status words to our stages."""
    s = (status_text or "").upper().replace("_", " ").strip()
    if s.startswith("RTO") or "RETURN" in s:
        return Shipment.RTO
    if s in ("DELIVERED", "DELIVERY DONE"):
        return Shipment.DELIVERED
    if "CANCEL" in s:
        return Shipment.CANCELLED
    if any(w in s for w in ("PICKED UP", "IN TRANSIT", "SHIPPED", "OUT FOR DELIVERY", "REACHED", "DESTINATION",
                            "UNDELIVERED", "DELAYED", "MISROUTED", "OUT FOR DELIVER")):
        return Shipment.IN_TRANSIT
    return Shipment.BOOKED  # AWB assigned, pickup scheduled, out for pickup …


def _parse_time(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d %m %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return timezone.make_aware(datetime.strptime(str(value)[:19], fmt))
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- courier choice
def couriers_for(order):
    """Couriers that deliver this order's PIN code, cheapest first."""
    from apps.core.models import SiteSettings

    store = SiteSettings.load()
    pickup = store.shiprocket_pickup_pincode or store.pincode
    if not pickup:
        raise ShippingError("Add your pickup PIN code in Website → Payments, Theme & Cards → Shiprocket.")
    payload = call("GET", "/courier/serviceability/", params={
        "pickup_postcode": pickup, "delivery_postcode": order.shipping_address.get("pincode", ""),
        "weight": str(parcel_weight_kg(order, store)), "cod": 1 if _is_cod_due(order) else 0})
    data = payload.get("data") or {}
    recommended = str(data.get("recommended_courier_company_id") or "")
    rows = []
    for c in data.get("available_courier_companies") or []:
        cid = str(c.get("courier_company_id") or c.get("id") or "")
        if not cid:
            continue
        rows.append({"id": cid, "name": c.get("courier_name", ""), "rate": c.get("rate") or c.get("freight_charge"),
                     "etd": c.get("etd") or "", "days": c.get("estimated_delivery_days") or "",
                     "rating": c.get("rating") or "", "recommended": cid == recommended})
    rows.sort(key=lambda r: (not r["recommended"], float(r["rate"] or 0)))
    if not rows:
        raise ShippingError("No courier delivers to this PIN code for this weight/payment type.")
    return rows


def _is_cod_due(order):
    return order.is_cod and not order.is_paid


# ---------------------------------------------------------------- booking
def _order_payload(order, store):
    a = order.shipping_address
    first, _, last = (a.get("full_name") or order.customer_name).partition(" ")
    items = list(order.items.all())
    lines = [{"name": f"{i.product_name}{'' if i.variant_name in ('', 'Standard') else ' — ' + i.variant_name}"[:100],
              "sku": i.sku, "units": i.quantity, "selling_price": _money(i.line_total / i.quantity),
              "discount": 0, "tax": _money(i.gst_rate), "hsn": i.hsn_code} for i in items]
    item_total = sum(Decimal(str(line["selling_price"])) * line["units"] for line in lines)
    # Make Shiprocket's total equal exactly what the customer owes (COD amount must be right to the paisa).
    extra = order.grand_total - item_total - order.shipping_total
    return {
        "order_id": order.order_number, "order_date": timezone.localtime(order.created_at).strftime("%Y-%m-%d %H:%M"),
        "pickup_location": store.shiprocket_pickup_location,
        "billing_customer_name": first or order.customer_name, "billing_last_name": last,
        "billing_address": a.get("line1", ""), "billing_address_2": a.get("line2", ""),
        "billing_city": a.get("city", ""), "billing_pincode": a.get("pincode", ""), "billing_state": a.get("state", ""),
        "billing_country": "India", "billing_email": order.email, "billing_phone": a.get("phone") or order.phone,
        "shipping_is_billing": True, "order_items": lines,
        "payment_method": "COD" if _is_cod_due(order) else "Prepaid",
        "shipping_charges": _money(order.shipping_total),
        "transaction_charges": _money(max(extra, 0)), "total_discount": _money(max(-extra, 0)),
        "sub_total": _money(item_total),
        "length": float(store.parcel_length_cm), "breadth": float(store.parcel_breadth_cm),
        "height": float(store.parcel_height_cm), "weight": float(parcel_weight_kg(order, store)),
    }


def _dig(obj, key):
    """Find `key` anywhere in Shiprocket's nested responses (their shapes vary between endpoints)."""
    if isinstance(obj, dict):
        if obj.get(key) not in (None, ""):
            return obj[key]
        for v in obj.values():
            found = _dig(v, key)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _dig(v, key)
            if found not in (None, ""):
                return found
    return None


def book(order, *, courier_id, by=None):
    """Create the Shiprocket order, assign an AWB with the chosen courier, request pickup, get the label."""
    from apps.core.models import SiteSettings
    from apps.core.utils import audit
    from apps.orders.models import Order
    from apps.orders.services import record

    store = SiteSettings.load()
    if order.status not in (Order.CONFIRMED, Order.PROCESSING):
        raise ShippingError("Only confirmed or processing orders can be booked.")
    if not order.is_cod and not order.is_paid:
        raise ShippingError("This order hasn't been paid yet.")
    shipment, _ = Shipment.objects.get_or_create(order=order, defaults={"created_by": by})
    if shipment.awb:
        raise ShippingError(f"Already booked: {shipment.courier_name} AWB {shipment.awb}.")

    if not shipment.provider_shipment_id:  # never create the same order twice in Shiprocket
        resp = call("POST", "/orders/create/adhoc", data=_order_payload(order, store))
        shipment.provider_order_id = str(resp.get("order_id") or "")
        shipment.provider_shipment_id = str(resp.get("shipment_id") or "")
        shipment.weight_kg = parcel_weight_kg(order, store)
        shipment.save()
        if not shipment.provider_shipment_id:
            raise ShippingError("Shiprocket did not return a shipment id. Check the order in the Shiprocket panel.")

    resp = call("POST", "/courier/assign/awb", data={"shipment_id": shipment.provider_shipment_id,
                                                     "courier_id": courier_id})
    awb = _dig(resp, "awb_code")
    if not awb:
        raise ShippingError("Courier could not be assigned: " + str(_dig(resp, "message") or "please try another "
                                                                     "courier or check your Shiprocket wallet."))
    shipment.awb = str(awb)
    shipment.courier_name = str(_dig(resp, "courier_name") or "")
    shipment.courier_id = str(courier_id)
    shipment.tracking_url = PUBLIC_TRACKING_URL.format(awb=shipment.awb)
    shipment.save()

    notes = []
    try:
        call("POST", "/courier/generate/pickup", data={"shipment_id": [shipment.provider_shipment_id]})
    except ShippingError as exc:
        notes.append(f"Pickup not requested automatically ({exc}) — request it in the Shiprocket panel.")
    try:
        label = call("POST", "/courier/generate/label", data={"shipment_id": [shipment.provider_shipment_id]})
        shipment.label_url = str(_dig(label, "label_url") or "")
        shipment.save(update_fields=["label_url", "updated_at"])
    except ShippingError as exc:
        notes.append(f"Label not ready yet ({exc}) — use 'Get label' again in a minute.")

    order.courier_name = shipment.courier_name[:80]
    order.tracking_number = shipment.awb[:80]
    order.tracking_url = shipment.tracking_url
    order.save(update_fields=["courier_name", "tracking_number", "tracking_url", "updated_at"])
    record(order, "note", "", "", f"Booked with {shipment.courier_name}, AWB {shipment.awb}", by=by, visible=False)
    _store_event(shipment, "AWB ASSIGNED", f"Booked with {shipment.courier_name}", "", timezone.now(), "booking")
    audit("shipping.book", f"{order.order_number} booked: {shipment.courier_name} {shipment.awb}", actor=by,
          obj=order)
    return shipment, notes


def fetch_label(shipment):
    label = call("POST", "/courier/generate/label", data={"shipment_id": [shipment.provider_shipment_id]})
    shipment.label_url = str(_dig(label, "label_url") or "")
    shipment.save(update_fields=["label_url", "updated_at"])
    if not shipment.label_url:
        raise ShippingError("Shiprocket has not made the label yet. Try again in a minute.")
    return shipment.label_url


# ---------------------------------------------------------------- tracking updates
def _store_event(shipment, status, activity, location, when, source):
    key = hashlib.sha256(f"{shipment.awb}|{status}|{activity}|{location}|{when}".encode()).hexdigest()
    try:
        with transaction.atomic():
            ShipmentEvent.objects.create(shipment=shipment, status=status[:100], activity=(activity or "")[:300],
                                         location=(location or "")[:150], event_time=when, source=source,
                                         event_key=key)
        return True
    except IntegrityError:  # the same update arrived again (webhook retry / hourly check) — ignore
        return False


def apply_update(shipment, status, *, activity="", location="", when=None, source="webhook"):
    """Record one courier update and move the order forward. Safe to call many times with the same update."""
    if not status or not _store_event(shipment, status, activity, location, when, source):
        return False
    stage = classify(status)
    if _RANK[stage] >= _RANK[shipment.stage] or stage == Shipment.CANCELLED:
        shipment.stage = stage
    shipment.status_text = status[:100]
    shipment.last_update_at = when or timezone.now()
    shipment.save(update_fields=["stage", "status_text", "last_update_at", "updated_at"])
    _move_order(shipment, stage, status)
    return True


def _move_order(shipment, stage, status):
    from apps.core.emails import notify_admins
    from apps.orders.models import Order
    from apps.orders.services import OrderActionError, change_status, record

    order = Order.objects.get(pk=shipment.order_id)
    try:
        if stage in (Shipment.IN_TRANSIT, Shipment.DELIVERED) and order.status in (Order.CONFIRMED, Order.PROCESSING):
            change_status(order, Order.SHIPPED, note=f"Picked up by {shipment.courier_name}")
            order.refresh_from_db()
        if stage == Shipment.DELIVERED and order.status == Order.SHIPPED:
            change_status(order, Order.DELIVERED, note="Delivered by courier")
        elif stage == Shipment.IN_TRANSIT and "OUT FOR DELIVER" in status.upper():
            record(order, "note", "", "", "Out for delivery today", visible=True)
        elif stage == Shipment.RTO:
            record(order, "note", "", "", f"Courier: {status} — parcel coming back to us", visible=False)
            transaction.on_commit(lambda: notify_admins("shipment_rto_alert", {"order": order, "shipment": shipment},
                                                        related=f"order:{order.order_number}"))
    except OrderActionError as exc:  # e.g. an unpaid online order — keep the update, let staff decide
        logger.warning("Courier update for %s not applied to order: %s", order.order_number, exc)
        record(order, "note", "", "", f"Courier says '{status}' but order could not move: {exc}", visible=False)


def handle_webhook(payload):
    """Shiprocket tracking webhook body → updates. Returns True if it matched one of our shipments."""
    awb = str(payload.get("awb") or payload.get("awb_code") or "").strip()
    shipment = Shipment.objects.filter(awb=awb).first() if awb else None
    if shipment is None:
        return False
    for scan in sorted(payload.get("scans") or [], key=lambda s: str(s.get("date", ""))):
        apply_update(shipment, scan.get("sr-status-label") or scan.get("status") or "",
                     activity=scan.get("activity", ""), location=scan.get("location", ""),
                     when=_parse_time(scan.get("date")), source="webhook")
    current = payload.get("current_status") or payload.get("shipment_status") or ""
    if current and not payload.get("scans"):
        # No scan list: record the headline status. Its timestamp (or none) keeps repeats from duplicating.
        apply_update(shipment, str(current), activity="Latest status",
                     when=_parse_time(payload.get("current_timestamp")), source="webhook")
    return True


def refresh(shipment):
    """Ask Shiprocket for the latest tracking (used by the hourly check and the 'Refresh' button)."""
    if not shipment.awb:
        return 0
    data = call("GET", f"/courier/track/awb/{shipment.awb}").get("tracking_data") or {}
    applied = 0
    activities = data.get("shipment_track_activities") or []
    for a in sorted(activities, key=lambda x: str(x.get("date", ""))):
        applied += apply_update(shipment, a.get("sr-status-label") or a.get("status") or "",
                                activity=a.get("activity", ""), location=a.get("location", ""),
                                when=_parse_time(a.get("date")), source="poll")
    track = (data.get("shipment_track") or [{}])[0]
    if track.get("current_status") and not activities:
        applied += apply_update(shipment, track["current_status"], activity="Latest status",
                                when=_parse_time(track.get("updated_time") or track.get("delivered_date")),
                                source="poll")
    return applied


def poll_open(limit=50):
    """Hourly backup for missed webhooks: check open shipments from the last 45 days."""
    since = timezone.now() - timedelta(days=45)
    checked = 0
    for shipment in Shipment.objects.filter(stage__in=Shipment.OPEN_STAGES, created_at__gte=since).exclude(awb="")[:limit]:
        try:
            refresh(shipment)
            checked += 1
        except ShippingError as exc:
            logger.warning("Tracking check for %s failed: %s", shipment.awb, exc)
    return checked


def shipping_ready():
    return settings.SHIPPING_ENABLED
