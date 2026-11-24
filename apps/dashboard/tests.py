from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.core.testing import configure_store, make_product, make_user, place_test_order
from apps.inventory.models import StockMovement
from apps.invoicing.models import Invoice
from apps.orders.models import Order


def staff_in(group, email):
    user = make_user(email=email, is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    return user


class DashboardPermissionTests(TestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        configure_store()
        self.order = place_test_order(make_user(), make_product(price="105.00", stock=5))

    def test_customers_get_403(self):
        self.client.force_login(make_user(email="c@example.com"))
        for name in ("dashboard:home", "dashboard:orders", "dashboard:inventory", "dashboard:reports"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_anonymous_redirected_to_login(self):
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 302)

    def test_inventory_staff_cannot_manage_orders_or_reports(self):
        self.client.force_login(staff_in("Inventory Staff", "inv@example.com"))
        self.assertEqual(self.client.get(reverse("dashboard:inventory")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:orders")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard:reports")).status_code, 403)
        resp = self.client.post(reverse("dashboard:order_action", args=[self.order.order_number]),
                                {"action": "cancel", "note": "x"})
        self.assertEqual(resp.status_code, 403)

    def test_order_staff_cannot_refund(self):
        self.client.force_login(staff_in("Order Staff", "ops@example.com"))
        Order.objects.filter(pk=self.order.pk).update(payment_status=Order.PAY_PAID)
        resp = self.client.post(reverse("dashboard:order_action", args=[self.order.order_number]),
                                {"action": "refund", "amount": "10", "note": "x", "reference": "UTR1"})
        self.assertEqual(resp.status_code, 403)

    def test_manager_full_workflow(self):
        manager = staff_in("Store Manager", "mgr@example.com")
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)
        url = reverse("dashboard:order_action", args=[self.order.order_number])
        self.client.post(url, {"action": "status", "status": "processing"})
        self.client.post(url, {"action": "tracking", "courier_name": "India Post", "tracking_number": "EE123IN"})
        self.client.post(url, {"action": "status", "status": "shipped"})
        self.client.post(url, {"action": "cod_collected"})
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), (Order.SHIPPED, Order.PAY_PAID))
        self.assertEqual(self.order.tracking_number, "EE123IN")
        self.assertTrue(Invoice.objects.filter(order=self.order).exists())
        resp = self.client.get(reverse("dashboard:order_detail", args=[self.order.order_number]))
        self.assertContains(resp, Invoice.objects.get().number)
        self.assertEqual(self.client.get(reverse("dashboard:packing_slip", args=[self.order.order_number])).status_code, 200)
        label = self.client.get(reverse("dashboard:shipping_label", args=[self.order.order_number]))
        self.assertContains(label, "AWB EE123IN")  # courier AWB barcode once tracking is saved
        self.assertContains(label, Invoice.objects.get().number)
        self.assertContains(label, "PREPAID")  # COD collected, so nothing to collect at the door
        self.assertContains(label, "<svg")

    def test_shipping_label_for_unshipped_cod_order(self):
        self.client.force_login(staff_in("Order Staff", "ops@example.com"))
        resp = self.client.get(reverse("dashboard:shipping_label", args=[self.order.order_number]))
        self.assertContains(resp, "No AWB / tracking number yet")
        self.assertContains(resp, f"ORDER {self.order.order_number}")
        self.assertContains(resp, "Collect")
        self.client.force_login(make_user(email="cust2@example.com"))
        self.assertEqual(self.client.get(reverse("dashboard:shipping_label",
                                                 args=[self.order.order_number])).status_code, 403)

    def test_stock_adjustment_recorded_with_user(self):
        user = staff_in("Inventory Staff", "inv@example.com")
        self.client.force_login(user)
        variant = self.order.items.first().variant
        self.client.post(reverse("dashboard:stock_adjust"), {"variant_id": variant.pk, "change": "+10",
                                                             "reason": "purchase", "note": "Supplier bill 42"})
        m = StockMovement.objects.filter(reason="purchase").get()
        self.assertEqual((m.change, m.created_by), (10, user))
        self.client.post(reverse("dashboard:stock_adjust"), {"variant_id": variant.pk, "change": "3", "reason": "damage"})
        self.assertEqual(StockMovement.objects.filter(reason="damage").get().change, -3)
        self.client.post(reverse("dashboard:stock_adjust"), {"variant_id": variant.pk, "change": "-999",
                                                                    "reason": "adjustment"})
        variant.refresh_from_db()
        self.assertGreaterEqual(variant.stock, 0)

    def test_reports_and_csv(self):
        self.client.force_login(staff_in("Accounts", "acc@example.com"))
        for kind in ("sales", "products", "status", "payments", "invoices", "tax", "refunds", "stock"):
            resp = self.client.get(reverse("dashboard:report", args=[kind]))
            self.assertEqual(resp.status_code, 200, kind)
            if kind != "status":
                csv_resp = self.client.get(reverse("dashboard:report", args=[kind]), {"format": "csv"})
                self.assertEqual(csv_resp["Content-Type"], "text/csv; charset=utf-8", kind)
        body = self.client.get(reverse("dashboard:report", args=["sales"]), {"format": "csv"}).content.decode("utf-8-sig")
        self.assertIn(str(self.order.grand_total), body)

    def test_csv_formula_injection_neutralised(self):
        from .views import _csv_safe

        self.assertEqual(_csv_safe("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(_csv_safe(Decimal("-5")), Decimal("-5"))

    def test_sales_widgets_exclude_cancelled(self):
        from apps.orders.services import cancel_order

        from .reports import summary_widgets

        before = summary_widgets()["today_sales"]
        cancel_order(self.order, reason="x")
        self.assertEqual(summary_widgets()["today_sales"], before - self.order.grand_total)
