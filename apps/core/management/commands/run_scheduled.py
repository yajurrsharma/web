"""
One command for a PythonAnywhere scheduled task (run hourly):

    cd ~/lifeline && source venv/bin/activate && python manage.py run_scheduled

- Reconciles recent pending online payments with Razorpay
- Cancels online orders not paid within the payment window (restores stock)
- Retries failed (non-sensitive) emails
- Sends the next batch of offer emails (within the daily limit)
- Backup tracking check for Shiprocket shipments (in case a webhook was missed)
- Deletes expired OTP records and abandoned guest carts
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Periodic maintenance: payment reconciliation, unpaid-order expiry, email retries, cleanup."

    def handle(self, *args, **opts):
        from django.conf import settings

        from apps.accounts.models import EmailOTP
        from apps.cart.models import Cart
        from apps.core.emails import retry_failed
        from apps.orders.models import Order
        from apps.orders.services import expire_unpaid_orders
        from apps.payments.services import reconcile_order

        now = timezone.now()
        if settings.PAYMENTS_ENABLED:
            checked = 0
            for order in Order.objects.filter(payment_method=Order.METHOD_ONLINE, status=Order.PENDING,
                                              created_at__gte=now - timedelta(days=2)):
                try:
                    reconcile_order(order)
                    checked += 1
                except Exception as exc:  # noqa: BLE001
                    self.stderr.write(f"reconcile {order.order_number}: {type(exc).__name__}")
            self.stdout.write(f"Reconciled {checked} pending online order(s).")
        # Outside the Razorpay check: direct UPI / bank orders must expire even without Razorpay keys.
        self.stdout.write(f"Expired {expire_unpaid_orders(now)} unpaid order(s).")
        retried, sent = retry_failed()
        self.stdout.write(f"Retried {retried} email(s); {sent} sent.")
        from apps.campaigns.services import send_batch

        self.stdout.write(f"Sent {send_batch()} offer email(s).")
        if settings.SHIPPING_ENABLED:
            from apps.shipping.services import poll_open

            self.stdout.write(f"Checked tracking for {poll_open()} open shipment(s).")
        n, _ = EmailOTP.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()
        self.stdout.write(f"Deleted {n} old OTP record(s).")
        n, _ = Cart.objects.filter(user__isnull=True, updated_at__lt=now - timedelta(days=30)).delete()
        self.stdout.write(f"Deleted {n} abandoned guest cart(s).")
