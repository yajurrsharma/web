from django.conf import settings
from django.db import models


class Payment(models.Model):
    """One payment attempt. An order may have several (e.g. a failed try, then success)."""

    GATEWAY_RAZORPAY = "razorpay"
    GATEWAY_COD = "cod"
    GATEWAY_UPI = "upi"
    GATEWAY_BANK = "bank"
    GATEWAY_CHOICES = [(GATEWAY_RAZORPAY, "Razorpay"), (GATEWAY_COD, "Cash on Delivery"),
                       (GATEWAY_UPI, "Direct UPI"), (GATEWAY_BANK, "Bank transfer")]

    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    STATUS_CHOICES = [(CREATED, "Created"), (AUTHORIZED, "Authorized"), (CAPTURED, "Captured / collected"),
                      (FAILED, "Failed")]

    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="payments")
    gateway = models.CharField(max_length=10, choices=GATEWAY_CHOICES)
    gateway_order_id = models.CharField(max_length=60, unique=True, null=True, blank=True)
    gateway_payment_id = models.CharField(max_length=60, unique=True, null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=CREATED, db_index=True)
    method_detail = models.CharField(max_length=30, blank=True, help_text="upi / card / netbanking …")
    error_code = models.CharField(max_length=60, blank=True)
    error_description = models.CharField(max_length=300, blank=True)
    captured_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", help_text="Staff who confirmed a COD collection.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_gateway_display()} {self.amount} ({self.status})"


class Refund(models.Model):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"
    METHOD_GATEWAY = "gateway"
    METHOD_MANUAL = "manual"

    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="refunds")
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=300)
    method = models.CharField(max_length=7, choices=[(METHOD_GATEWAY, "Via Razorpay"),
                                                     (METHOD_MANUAL, "Manual (bank/UPI transfer)")])
    status = models.CharField(max_length=10, default=PENDING,
                              choices=[(PENDING, "Pending"), (PROCESSED, "Processed"), (FAILED, "Failed")])
    gateway_refund_id = models.CharField(max_length=60, unique=True, null=True, blank=True)
    reference = models.CharField(max_length=100, blank=True, help_text="UTR / transaction reference for manual refunds.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Refund ₹{self.amount} for {self.order} ({self.status})"


class WebhookEvent(models.Model):
    """Every webhook delivery is recorded once; duplicates are detected by event id."""

    gateway = models.CharField(max_length=10, default=Payment.GATEWAY_RAZORPAY)
    event_id = models.CharField(max_length=80, unique=True)
    event_type = models.CharField(max_length=60)
    status = models.CharField(max_length=10, default="received",
                              choices=[("received", "Received"), ("processed", "Processed"),
                                       ("ignored", "Ignored"), ("error", "Error")])
    note = models.CharField(max_length=300, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.event_type} {self.event_id}"
