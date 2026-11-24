from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class StockMovement(models.Model):
    """
    Append-only stock ledger. `ProductVariant.stock` is only ever changed through
    `apps.inventory.services.adjust_stock`, which writes one of these rows in the
    same transaction.
    """

    OPENING = "opening"
    PURCHASE = "purchase"
    ADJUSTMENT = "adjustment"
    SALE = "sale"
    CANCELLATION = "cancellation"
    RETURN_RESTOCK = "return_restock"
    DAMAGE = "damage"
    REASON_CHOICES = [
        (OPENING, "Opening stock"),
        (PURCHASE, "New stock received"),
        (ADJUSTMENT, "Manual correction / stock count"),
        (SALE, "Sold (order placed)"),
        (CANCELLATION, "Order cancelled — restored"),
        (RETURN_RESTOCK, "Return inspected — restocked"),
        (DAMAGE, "Damaged / expired / lost"),
    ]

    variant = models.ForeignKey("products.ProductVariant", on_delete=models.PROTECT, related_name="movements")
    change = models.IntegerField(help_text="Positive adds stock, negative removes.")
    stock_before = models.IntegerField()
    stock_after = models.IntegerField()
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, db_index=True)
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="stock_movements")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", help_text="Blank = automatic system event.")
    note = models.CharField(max_length=200, blank=True)
    idempotency_key = models.CharField(max_length=100, unique=True, null=True, blank=True,
                                       help_text="Prevents the same event from moving stock twice.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["variant", "created_at"])]

    def __str__(self):
        sign = "+" if self.change > 0 else ""
        return f"{self.variant.sku} {sign}{self.change} ({self.get_reason_display()})"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Stock movements are permanent. Record a new adjustment instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Stock movements cannot be deleted.")
