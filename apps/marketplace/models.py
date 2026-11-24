"""
Phase 2 — Amazon Selling Partner API. Disabled until credentials are supplied
(AMAZON_SP_API_ENABLED=False). Only the SKU mapping table exists now so you can
prepare listings; see docs/AMAZON_PHASE2.md for the full design.
"""
from django.db import models


class SkuMapping(models.Model):
    CHANNEL_AMAZON = "amazon"

    channel = models.CharField(max_length=10, choices=[(CHANNEL_AMAZON, "Amazon")], default=CHANNEL_AMAZON)
    variant = models.ForeignKey("products.ProductVariant", on_delete=models.PROTECT, related_name="channel_skus")
    external_sku = models.CharField("Seller SKU on the marketplace", max_length=60)
    asin = models.CharField("ASIN", max_length=20, blank=True)
    sync_inventory = models.BooleanField(default=False, help_text="Push stock levels when the integration is live.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["channel", "external_sku"], name="unique_channel_sku")]
        verbose_name = "marketplace SKU mapping"

    def __str__(self):
        return f"{self.get_channel_display()} {self.external_sku} → {self.variant.sku}"
