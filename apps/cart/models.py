from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.utils import money

ZERO = Decimal("0.00")
MAX_QTY_PER_LINE = 50


class CouponError(Exception):
    pass


class Coupon(models.Model):
    PERCENT = "percent"
    FLAT = "flat"

    code = models.CharField(max_length=30, unique=True, help_text="Customers type this. Stored in capitals.")
    description = models.CharField(max_length=200, blank=True)
    discount_type = models.CharField(max_length=7, choices=[(PERCENT, "Percentage"), (FLAT, "Flat amount (₹)")])
    value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    min_order_amount = models.DecimalField("Minimum order (₹)", max_digits=10, decimal_places=2, default=ZERO)
    max_discount = models.DecimalField("Maximum discount (₹)", max_digits=10, decimal_places=2,
                                       null=True, blank=True, help_text="Cap for percentage coupons.")
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Total uses allowed. Blank = unlimited.")
    per_user_limit = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.code

    def clean(self):
        self.code = (self.code or "").strip().upper()
        if self.discount_type == self.PERCENT and self.value and self.value > 100:
            raise ValidationError({"value": "Percentage cannot exceed 100."})
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "End must be after start."})

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    @property
    def times_used(self):
        return self.usages.count()

    def check_eligible(self, user, subtotal):
        """Raise CouponError with a customer-friendly message when not usable."""
        now = timezone.now()
        if not self.is_active or (self.starts_at and now < self.starts_at) or (self.ends_at and now > self.ends_at):
            raise CouponError("This coupon is not active.")
        if subtotal < self.min_order_amount:
            raise CouponError(f"Add items worth ₹{self.min_order_amount - subtotal} more to use this coupon.")
        if self.usage_limit is not None and self.usages.count() >= self.usage_limit:
            raise CouponError("This coupon has reached its usage limit.")
        if user is not None and user.is_authenticated and \
                self.usages.filter(user=user).count() >= self.per_user_limit:
            raise CouponError("You have already used this coupon.")

    def discount_for(self, subtotal):
        if self.discount_type == self.PERCENT:
            amount = subtotal * self.value / Decimal("100")
            if self.max_discount is not None:
                amount = min(amount, self.max_discount)
        else:
            amount = self.value
        return money(min(amount, subtotal))


class CouponUsage(models.Model):
    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT, related_name="usages")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="coupon_usages")
    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="coupon_usage")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.coupon.code} on {self.order.order_number}"


class Cart(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
                                related_name="cart")
    session_key = models.CharField(max_length=40, null=True, blank=True, unique=True)
    coupon = models.ForeignKey(Coupon, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Cart {self.pk} ({self.user or 'guest'})"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("products.ProductVariant", on_delete=models.CASCADE, related_name="+")
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1),
                                                                  MaxValueValidator(MAX_QTY_PER_LINE)])
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["added_at", "id"]
        constraints = [models.UniqueConstraint(fields=["cart", "variant"], name="unique_variant_per_cart")]

    def __str__(self):
        return f"{self.quantity} × {self.variant}"


class PromotionQuerySet(models.QuerySet):
    def live(self, now=None):
        now = now or timezone.now()
        return (self.filter(is_active=True)
                .filter(models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now))
                .filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now)))


class Promotion(models.Model):
    """
    Automatic offers — no code to type. Applied by the pricing engine before any coupon, so GST is
    always charged on the price after the offer. Each unit in the cart is used by one offer at most.
    """

    BUY_X_GET_Y = "bxgy"
    BUNDLE = "bundle"
    FREE_GIFT = "gift"
    KIND_CHOICES = [(BUY_X_GET_Y, "Buy X, get Y free (cheapest free)"),
                    (BUNDLE, "Any N for a fixed price (e.g. any 5 for ₹799)"),
                    (FREE_GIFT, "Free gift on orders above an amount")]

    name = models.CharField(max_length=80, help_text="Shown to customers, e.g. 'Buy 2 Get 1 Free'.")
    kind = models.CharField(max_length=6, choices=KIND_CHOICES)
    products = models.ManyToManyField("products.Product", blank=True, related_name="+",
                                      help_text="Products the offer applies to. Leave empty for all products.")
    buy_qty = models.PositiveSmallIntegerField("Buy (X)", default=2, validators=[MinValueValidator(1)])
    free_qty = models.PositiveSmallIntegerField("Get free (Y)", default=1, validators=[MinValueValidator(1)])
    bundle_qty = models.PositiveSmallIntegerField("Any N items", default=5, validators=[MinValueValidator(2)])
    bundle_price = models.DecimalField("For (₹)", max_digits=10, decimal_places=2, null=True, blank=True,
                                       validators=[MinValueValidator(Decimal("1"))])
    min_amount = models.DecimalField("Order amount needed (₹)", max_digits=10, decimal_places=2, default=ZERO,
                                     help_text="For a free gift: order value (after other discounts) needed.")
    gift_variant = models.ForeignKey("products.ProductVariant", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", help_text="The free gift (comes out of stock).")
    show_in_bar = models.BooleanField("Show in the top offer bar", default=True)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Real end date. Blank = until switched off.")
    sort_order = models.PositiveSmallIntegerField(default=0, help_text="Smaller numbers are applied first.")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PromotionQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.kind == self.BUNDLE and not self.bundle_price:
            raise ValidationError({"bundle_price": "Enter the price for the bundle."})
        if self.kind == self.FREE_GIFT:
            if not self.gift_variant_id:
                raise ValidationError({"gift_variant": "Choose the free gift."})
            if not self.min_amount or self.min_amount <= 0:
                raise ValidationError({"min_amount": "Enter the order amount needed for the gift."})
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "End must be after the start."})
