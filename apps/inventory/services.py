"""The only code path that changes stock. Locks the variant row to stop overselling."""
import logging

from django.db import transaction

from .models import StockMovement

logger = logging.getLogger(__name__)


class InsufficientStock(Exception):
    def __init__(self, variant, requested):
        self.variant = variant
        self.requested = requested
        super().__init__(f"Only {variant.stock} left of {variant}; {requested} requested.")


def adjust_stock(variant_id, change, reason, *, order=None, user=None, note="", key=None,
                 allow_negative=False):
    """
    Apply `change` to a variant's stock and record it in the ledger, atomically.

    - `select_for_update` serialises concurrent orders for the same variant (MySQL/InnoDB);
      SQLite serialises writes at the database level.
    - `key` makes the call idempotent: a second call with the same key returns the
      original movement and changes nothing (duplicate webhooks, double clicks).
    """
    from apps.products.models import ProductVariant

    if change == 0:
        raise ValueError("Stock change cannot be zero.")
    with transaction.atomic():
        if key:
            existing = StockMovement.objects.filter(idempotency_key=key).first()
            if existing:
                return existing
        variant = ProductVariant.objects.select_for_update().get(pk=variant_id)
        new_stock = variant.stock + change
        if new_stock < 0 and not allow_negative:
            raise InsufficientStock(variant, -change)
        new_stock = max(new_stock, 0)
        before = variant.stock
        ProductVariant.objects.filter(pk=variant.pk).update(stock=new_stock)
        # If another process inserted the same key meanwhile, the unique constraint raises
        # IntegrityError and this whole transaction (including the stock update) rolls back.
        movement = StockMovement.objects.create(
            variant=variant, change=new_stock - before, stock_before=before, stock_after=new_stock,
            reason=reason, order=order, created_by=user, note=note[:200], idempotency_key=key)
        variant.stock = new_stock
        if before > variant.low_stock_threshold >= new_stock:
            transaction.on_commit(lambda: _low_stock_alert(variant.pk))
        return movement


def _low_stock_alert(variant_id):
    from apps.core.emails import notify_admins
    from apps.core.models import SiteSettings
    from apps.products.models import ProductVariant

    if not SiteSettings.load().low_stock_email_alerts:
        return
    variant = ProductVariant.objects.select_related("product").get(pk=variant_id)
    try:
        notify_admins("low_stock_alert", {"variant": variant}, related=f"variant:{variant.pk}")
    except Exception:  # pragma: no cover - never break checkout because of an alert
        logger.exception("Low-stock alert failed for variant %s", variant_id)
