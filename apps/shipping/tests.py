"""Shiprocket integration tests — Shiprocket itself is faked; nothing leaves this computer."""
import json
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import Group
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import SiteSettings
from apps.core.testing import configure_store, make_product, make_user, place_test_order
from apps.orders.models import Order

from . import services
from .client import ShippingError
from .models import Shipment, ShipmentEvent

TOKEN = "test-webhook-token-dummy"


class FakeShiprocket:
    """Answers like Shiprocket's API and remembers every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, path, *, data=None, params=None, token=None):
        self.calls.append((method, path, data, params))
        if path == "/auth/login":
            return 200, {"token": "tok-123"}
        if path.startswith("/courier/serviceability"):
            return 200, {"data": {"recommended_courier_company_id": 2, "available_courier_companies": [
                {"courier_company_id": 1, "courier_name": "Xpressbees Surface", "rate": 72.5, "etd": "Oct 25, 2026"},
                {"courier_company_id": 2, "courier_name": "Delhivery Surface", "rate": 79.0, "etd": "Oct 24, 2026"}]}}
        if path == "/orders/create/adhoc":
            return 200, {"order_id": 9001, "shipment_id": 7001, "status": "NEW"}
        if path == "/courier/assign/awb":
            return 200, {"awb_assign_status": 1, "response": {"data": {
                "awb_code": "1234567890123", "courier_name": "Delhivery Surface", "courier_company_id": 2}}}
        if path == "/courier/generate/pickup":
            return 200, {"pickup_status": 1}
        if path == "/courier/generate/label":
            return 200, {"label_created": 1, "label_url": "https://labels.example.com/7001.pdf"}
        if path.startswith("/courier/track/awb/"):
            return 200, {"tracking_data": {"shipment_track": [{"current_status": "Delivered"}],
                                           "shipment_track_activities": [
                {"date": "2026-10-21 10:00:00", "sr-status-label": "PICKED UP", "activity": "Picked up",
                 "location": "Zirakpur"},
                {"date": "2026-10-23 15:30:00", "sr-status-label": "DELIVERED", "activity": "Delivered",
                 "location": "Varanasi"}]}}
        return 404, {"message": "not faked"}


@override_settings(ADMIN_ALERT_EMAILS=["owner@example.com"])
class ShiprocketTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("setup_roles", stdout=StringIO())
        configure_store(shiprocket_pickup_pincode="160104", invoice_trigger=SiteSettings.INVOICE_ON_DISPATCH)
        self.variant = make_product(price="250.00", mrp="300.00", stock=10)
        self.variant.weight_grams = 400
        self.variant.save()
        self.order = place_test_order(make_user(), self.variant, qty=2)  # COD, confirmed
        self.staff = make_user(email="mgr@example.com", is_staff=True)
        self.staff.groups.add(Group.objects.get(name="Store Manager"))
        self.fake = FakeShiprocket()
        patcher = mock.patch("apps.shipping.client._http", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _book(self):
        return services.book(self.order, courier_id="2", by=self.staff)

    def test_courier_list_uses_pickup_pin_weight_and_cod(self):
        rows = services.couriers_for(self.order)
        self.assertEqual(rows[0]["name"], "Delhivery Surface")  # recommended first
        params = next(c[3] for c in self.fake.calls if c[1].startswith("/courier/serviceability"))
        self.assertEqual((params["pickup_postcode"], params["weight"], params["cod"]), ("160104", "0.8", 1))

    def test_booking_saves_awb_label_and_exact_cod_amount(self):
        shipment, notes = self._book()
        self.assertEqual((shipment.awb, shipment.courier_name), ("1234567890123", "Delhivery Surface"))
        self.assertEqual(shipment.label_url, "https://labels.example.com/7001.pdf")
        self.assertEqual(notes, [])
        self.order.refresh_from_db()
        self.assertEqual(self.order.tracking_number, "1234567890123")
        payload = next(c[2] for c in self.fake.calls if c[1] == "/orders/create/adhoc")
        self.assertEqual(payload["payment_method"], "COD")
        self.assertEqual(payload["pickup_location"], "Primary")
        total = (Decimal(str(payload["sub_total"])) + Decimal(str(payload["shipping_charges"]))
                 + Decimal(str(payload["transaction_charges"])) - Decimal(str(payload["total_discount"])))
        self.assertEqual(total, self.order.grand_total)  # courier collects exactly what the customer owes
        with self.assertRaises(ShippingError):
            self._book()  # never book twice

    def test_failed_awb_retry_does_not_create_second_shiprocket_order(self):
        original = self.fake.__call__

        def no_awb(method, path, **kw):
            if path == "/courier/assign/awb":
                return 200, {"awb_assign_status": 0, "response": {"data": {"awb_assign_error": "Wallet low"}}}
            return original(method, path, **kw)

        with mock.patch("apps.shipping.client._http", no_awb):
            with self.assertRaises(ShippingError):
                self._book()
        self._book()
        creates = [c for c in self.fake.calls if c[1] == "/orders/create/adhoc"]
        self.assertEqual(len(creates), 1)

    def test_webhook_needs_token_and_moves_order_automatically(self):
        self._book()
        url = reverse("shipping:webhook")
        body = {"awb": "1234567890123", "current_status": "PICKED UP", "scans": [
            {"date": "2026-10-21 10:00:00", "status": "PKD", "sr-status-label": "PICKED UP", "activity": "Picked",
             "location": "Zirakpur"}]}
        self.assertEqual(self.client.post(url, json.dumps(body), content_type="application/json").status_code, 401)
        self.assertEqual(self.client.post(url, json.dumps(body), content_type="application/json",
                                          HTTP_X_API_KEY="wrong").status_code, 401)
        with self.captureOnCommitCallbacks(execute=True):
            ok = self.client.post(url, json.dumps(body), content_type="application/json", HTTP_X_API_KEY=TOKEN)
        self.assertEqual(ok.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.SHIPPED)  # by itself, no staff action
        self.assertTrue(any("shipped" in m.subject.lower() for m in mail.outbox))
        # Same webhook again (Shiprocket retries): no duplicate event.
        self.client.post(url, json.dumps(body), content_type="application/json", HTTP_X_API_KEY=TOKEN)
        self.assertEqual(ShipmentEvent.objects.filter(status="PICKED UP").count(), 1)
        body = {"awb": "1234567890123", "scans": [{"date": "2026-10-23 15:30:00", "sr-status-label": "DELIVERED",
                                                   "activity": "Delivered", "location": "Varanasi"}]}
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, json.dumps(body), content_type="application/json", HTTP_X_API_KEY=TOKEN)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.DELIVERED)
        self.assertEqual(Shipment.objects.get().stage, Shipment.DELIVERED)
        # Unknown AWB: accepted (so Shiprocket stops retrying) but changes nothing.
        resp = self.client.post(url, json.dumps({"awb": "999"}), content_type="application/json",
                                HTTP_X_API_KEY=TOKEN)
        self.assertEqual(resp.json(), {"ok": True, "matched": False})

    def test_rto_alerts_staff_and_does_not_cancel(self):
        shipment, _ = self._book()
        with self.captureOnCommitCallbacks(execute=True):
            services.apply_update(shipment, "RTO INITIATED", when=None)
        shipment.refresh_from_db()
        self.assertEqual(shipment.stage, Shipment.RTO)
        self.order.refresh_from_db()
        self.assertNotEqual(self.order.status, Order.CANCELLED)  # staff inspect the parcel first
        self.assertTrue(any("returning" in m.subject.lower() for m in mail.outbox if "owner@example.com" in m.to))

    def test_hourly_check_and_dashboard_buttons(self):
        self._book()
        self.assertEqual(services.poll_open(), 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.DELIVERED)
        self.client.force_login(self.staff)
        page = self.client.get(reverse("dashboard:order_detail", args=[self.order.order_number]))
        self.assertContains(page, "1234567890123")
        resp = self.client.post(reverse("dashboard:order_action", args=[self.order.order_number]),
                                {"action": "ship_label"})
        self.assertEqual(resp["Location"], "https://labels.example.com/7001.pdf")

    def test_dashboard_courier_list_and_booking(self):
        self.client.force_login(self.staff)
        url = reverse("dashboard:order_detail", args=[self.order.order_number])
        self.assertContains(self.client.get(url, {"couriers": "1"}), "Xpressbees Surface")
        self.client.post(reverse("dashboard:order_action", args=[self.order.order_number]),
                         {"action": "ship_book", "courier_id": "2"})
        self.assertEqual(Shipment.objects.get(order=self.order).awb, "1234567890123")

    def test_status_words(self):
        for text, stage in [("Delivered", Shipment.DELIVERED), ("UNDELIVERED", Shipment.IN_TRANSIT),
                            ("RTO DELIVERED", Shipment.RTO), ("Out For Delivery", Shipment.IN_TRANSIT),
                            ("PICKUP SCHEDULED", Shipment.BOOKED), ("Canceled", Shipment.CANCELLED)]:
            self.assertEqual(services.classify(text), stage, text)

    def test_bad_login_gives_clear_message(self):
        with mock.patch("apps.shipping.client._http", return_value=(403, {"message": "Invalid credentials"})):
            cache.clear()
            with self.assertRaisesMessage(ShippingError, "use the API user"):
                services.couriers_for(self.order)
