import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core.gst import STATE_NAMES
from apps.core.validators import safe_upload_path, shrink_image, validate_image_upload

_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L confusion


def new_order_number():
    """Human-readable, not sequential: LLD-261018-7K4Q9."""
    now = timezone.localtime()
    return f"LLD-{now:%y%m%d}-" + "".join(secrets.choice(_ALPHABET) for _ in range(5))


class Order(models.Model):
    # --- order lifecycle ---
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURN_REQUESTED = "return_requested"
    RETURNED = "returned"
    STATUS_CHOICES = [
        (PENDING, "Pending"), (CONFIRMED, "Confirmed"), (PROCESSING, "Processing"), (SHIPPED, "Shipped"),
        (DELIVERED, "Delivered"), (CANCELLED, "Cancelled"), (RETURN_REQUESTED, "Return requested"),
        (RETURNED, "Returned"),
    ]
    TRANSITIONS = {
        PENDING: {CONFIRMED, CANCELLED},
        CONFIRMED: {PROCESSING, SHIPPED, CANCELLED},
        PROCESSING: {SHIPPED, CANCELLED},
        SHIPPED: {DELIVERED, CANCELLED},  # SHIPPED→CANCELLED = returned to origin / undelivered
        DELIVERED: {RETURN_REQUESTED},
        RETURN_REQUESTED: {RETURNED, DELIVERED},
        RETURNED: set(),
        CANCELLED: set(),
    }

    # --- payment lifecycle (separate from order status) ---
    PAY_PENDING = "pending"
    PAY_AUTHORIZED = "authorized"
    PAY_PAID = "paid"
    PAY_FAILED = "failed"
    PAY_PARTIALLY_REFUNDED = "partially_refunded"
    PAY_REFUNDED = "refunded"
    PAYMENT_STATUS_CHOICES = [
        (PAY_PENDING, "Pending"), (PAY_AUTHORIZED, "Authorized"), (PAY_PAID, "Paid"), (PAY_FAILED, "Failed"),
        (PAY_PARTIALLY_REFUNDED, "Partially refunded"), (PAY_REFUNDED, "Refunded"),
    ]

    METHOD_ONLINE = "online"
    METHOD_COD = "cod"
    METHOD_UPI = "upi"
    METHOD_BANK = "bank"
    METHOD_CHOICES = [(METHOD_ONLINE, "Online payment (Razorpay)"), (METHOD_COD, "Cash on Delivery"),
                      (METHOD_UPI, "Direct UPI"), (METHOD_BANK, "Bank transfer")]
    MANUAL_METHODS = (METHOD_UPI, METHOD_BANK)  # paid to the seller directly; staff confirm receipt

    CHANNEL_WEBSITE = "website"
    CHANNEL_AMAZON = "amazon"
    CHANNEL_CHOICES = [(CHANNEL_WEBSITE, "Website"), (CHANNEL_AMAZON, "Amazon")]

    order_number = models.CharField(max_length=20, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default=CHANNEL_WEBSITE, db_index=True)
    external_order_id = models.CharField(max_length=60, blank=True,
                                         help_text="Marketplace order id (e.g. Amazon). Never an invoice number.")

    # Customer + address snapshots (unchanged even if the customer edits their profile later)
    customer_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=15)
    billing_address = models.JSONField()
    shipping_address = models.JSONField()
    customer_gstin = models.CharField("Customer GSTIN", max_length=15, blank=True)
    delivery_instructions = models.CharField(max_length=300, blank=True)
    place_of_supply = models.CharField(max_length=2, help_text="GST state code of the delivery address.")
    is_intra_state = models.BooleanField(null=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    payment_method = models.CharField(max_length=6, choices=METHOD_CHOICES)
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default=PAY_PENDING,
                                      db_index=True)
    payment_due_by = models.DateTimeField(null=True, blank=True,
                                          help_text="Unpaid online orders are released after this time.")
    payment_reference = models.CharField(
        max_length=40, blank=True,
        help_text="UTR / transaction reference the customer entered for a direct UPI or bank payment.")

    # Amounts (₹). `subtotal` is gross item value before discount.
    prices_include_gst = models.BooleanField(default=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    coupon_code = models.CharField(max_length=30, blank=True)
    shipping_total = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                         help_text="Delivery + COD fee as charged.")
    cod_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shipping_taxable = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shipping_tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shipping_gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    taxable_total = models.DecimalField(max_digits=12, decimal_places=2)
    cgst_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sgst_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    igst_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2)
    round_off = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2)
    refunded_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Fulfilment (no live courier integration in v1 — staff enter these)
    courier_name = models.CharField(max_length=80, blank=True)
    tracking_number = models.CharField(max_length=80, blank=True)
    tracking_url = models.URLField(blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cod_collected_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    stock_released = models.BooleanField(default=False, help_text="Stock already returned to inventory.")

    admin_notes = models.TextField(blank=True, help_text="Internal — never shown to the customer.")
    checkout_token = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status", "payment_status"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["channel", "external_order_id"],
                                    condition=~models.Q(external_order_id=""),
                                    name="unique_marketplace_order"),
        ]
        permissions = [
            ("manage_orders", "Can manage orders from the dashboard"),
            ("view_reports", "Can view sales & tax reports"),
            ("issue_refunds", "Can issue refunds"),
        ]

    def __str__(self):
        return self.order_number

    def save(self, *args, **kwargs):
        if not self.order_number:
            number = new_order_number()
            while Order.objects.filter(order_number=number).exists():
                number = new_order_number()
            self.order_number = number
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("orders:detail", args=[self.order_number])

    # --- helpers ---
    def can_transition(self, new_status):
        return new_status in self.TRANSITIONS.get(self.status, set())

    @property
    def is_cod(self):
        return self.payment_method == self.METHOD_COD

    @property
    def is_paid(self):
        return self.payment_status in (self.PAY_PAID, self.PAY_PARTIALLY_REFUNDED)

    @property
    def awaiting_online_payment(self):
        return (self.payment_method == self.METHOD_ONLINE and self.status == self.PENDING
                and self.payment_status in (self.PAY_PENDING, self.PAY_FAILED))

    @property
    def is_manual_payment(self):
        return self.payment_method in self.MANUAL_METHODS

    @property
    def awaiting_manual_payment(self):
        return self.is_manual_payment and self.status == self.PENDING and self.payment_status == self.PAY_PENDING

    @property
    def place_of_supply_name(self):
        return STATE_NAMES.get(self.place_of_supply, "")

    @property
    def item_count(self):
        return sum(i.quantity for i in self.items.all())

    def customer_cancellable(self, store):
        allowed = {"confirmed": {self.PENDING},
                   "processing": {self.PENDING, self.CONFIRMED},
                   "shipped": {self.PENDING, self.CONFIRMED, self.PROCESSING}}[store.cancel_allowed_until]
        return self.status in allowed

    def return_window_open(self, store):
        if self.status != self.DELIVERED or not self.delivered_at or store.return_window_days == 0:
            return False
        return timezone.now() <= self.delivered_at + timedelta(days=store.return_window_days)


class OrderItem(models.Model):
    """Immutable snapshot of what was sold, at the price and tax that applied then."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("products.ProductVariant", null=True, on_delete=models.SET_NULL, related_name="+")
    product_name = models.CharField(max_length=150)
    variant_name = models.CharField(max_length=80, blank=True)
    sku = models.CharField(max_length=40)
    hsn_code = models.CharField(max_length=8)
    unit = models.CharField(max_length=3, default="PCS")
    quantity = models.PositiveIntegerField()
    mrp = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Selling price per unit as charged.")
    line_gross = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    taxable_value = models.DecimalField(max_digits=12, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    cgst = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    product_slug = models.CharField(max_length=170, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantity} × {self.description}"

    @property
    def description(self):
        if self.variant_name and self.variant_name != "Standard":
            return f"{self.product_name} — {self.variant_name}"
        return self.product_name


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="history")
    field = models.CharField(max_length=10, choices=[("order", "Order status"), ("payment", "Payment status"),
                                                     ("note", "Note")], default="order")
    from_value = models.CharField(max_length=20, blank=True)
    to_value = models.CharField(max_length=20, blank=True)
    note = models.CharField(max_length=300, blank=True)
    visible_to_customer = models.BooleanField(default=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "order status history"

    def __str__(self):
        return f"{self.order} {self.field}: {self.from_value} → {self.to_value}"


class OrderRequest(models.Model):
    """Customer cancellation (for paid orders) and return requests, handled by staff."""

    KIND_CANCEL = "cancel"
    KIND_RETURN = "return"
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="requests")
    kind = models.CharField(max_length=6, choices=[(KIND_CANCEL, "Cancellation"), (KIND_RETURN, "Return")])
    status = models.CharField(max_length=10, default=REQUESTED,
                              choices=[(REQUESTED, "Requested"), (APPROVED, "Approved — awaiting item"),
                                       (REJECTED, "Rejected"), (COMPLETED, "Completed")])
    PROBLEM_CHOICES = [
        ("damaged", "Item arrived damaged or broken"),
        ("wrong_item", "Wrong item received"),
        ("missing_item", "Item missing from the parcel"),
        ("leaked", "Leaked, melted or spoiled"),
        ("defective", "Item does not work (does not light / burn)"),
        ("other", "Other problem"),
    ]
    PHOTO_OPTIONAL = {"missing_item"}  # nothing to photograph; staff check the parcel weight instead

    reason = models.CharField(max_length=500)
    problem = models.CharField(max_length=20, choices=PROBLEM_CHOICES, blank=True)
    staff_note = models.CharField(max_length=500, blank=True)
    restock_approved = models.BooleanField(null=True, blank=True,
                                           help_text="Decided after physical inspection of returned goods.")
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} for {self.order}"


class RequestPhoto(models.Model):
    """Photo the customer attaches when reporting a problem with a delivered order."""

    MAX_PER_REQUEST = 4

    request = models.ForeignKey(OrderRequest, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to=safe_upload_path("problem-photos"), validators=[validate_image_upload])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]

    def save(self, *args, **kwargs):
        shrink_image(self.image)
        super().save(*args, **kwargs)
