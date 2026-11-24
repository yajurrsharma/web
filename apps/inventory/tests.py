from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.testing import make_product

from .models import StockMovement
from .services import InsufficientStock, adjust_stock


class StockLedgerTests(TestCase):
    def setUp(self):
        self.variant = make_product(stock=5)

    def test_every_change_is_ledgered(self):
        adjust_stock(self.variant.pk, -2, StockMovement.SALE)
        m = StockMovement.objects.filter(reason=StockMovement.SALE).get()
        self.assertEqual((m.stock_before, m.stock_after, m.change), (5, 3, -2))

    def test_cannot_go_negative(self):
        with self.assertRaises(InsufficientStock):
            adjust_stock(self.variant.pk, -6, StockMovement.SALE)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 5)

    def test_idempotency_key(self):
        a = adjust_stock(self.variant.pk, 3, StockMovement.CANCELLATION, key="cancel:1:1")
        b = adjust_stock(self.variant.pk, 3, StockMovement.CANCELLATION, key="cancel:1:1")
        self.assertEqual(a.pk, b.pk)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 8)

    def test_ledger_is_append_only(self):
        m = StockMovement.objects.first()
        with self.assertRaises(ValidationError):
            m.note = "edited"
            m.save()
        with self.assertRaises(ValidationError):
            m.delete()

    def test_low_stock_alert_once_when_crossing_threshold(self):
        from django.core import mail
        from django.test import override_settings

        with override_settings(ADMIN_ALERT_EMAILS=["owner@example.com"]):
            with self.captureOnCommitCallbacks(execute=True):
                adjust_stock(self.variant.pk, -1, StockMovement.SALE)  # 5 → 4: crosses threshold 5? (5 > 5 is false)
            with self.captureOnCommitCallbacks(execute=True):
                adjust_stock(self.variant.pk, -1, StockMovement.SALE)  # 4 → 3: already below, no new alert
        self.assertEqual(len([m for m in mail.outbox if "Low stock" in m.subject]), 0)
        self.variant.refresh_from_db()
        adjust_stock(self.variant.pk, 10, StockMovement.PURCHASE)  # back to 13
        with override_settings(ADMIN_ALERT_EMAILS=["owner@example.com"]):
            with self.captureOnCommitCallbacks(execute=True):
                adjust_stock(self.variant.pk, -9, StockMovement.SALE)  # 13 → 4 crosses 5
        self.assertEqual(len([m for m in mail.outbox if "Low stock" in m.subject]), 1)
