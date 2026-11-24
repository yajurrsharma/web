"""
Amazon SP-API integration interface (phase 2, intentionally not implemented yet).

When enabling, implement these methods with the official SP-API endpoints and
Login-with-Amazon (LWA) credentials stored in environment variables. Rules that
the implementation must keep (see docs/AMAZON_PHASE2.md):
- Import each Amazon order once: Order(channel="amazon", external_order_id=<AmazonOrderId>)
  is unique, so re-imports are no-ops.
- Never issue a website invoice for an Amazon order. Store Amazon's invoice
  number/PDF reference instead — Amazon or your Seller Central invoicing
  settings decide who issues it.
- Keep gross sales, Amazon fees, GST on fees, TCS, refunds and settlement
  payouts as separate accounting entries; settlement ≠ sales.
"""
from django.conf import settings


class MarketplaceDisabled(Exception):
    pass


class AmazonClient:
    def __init__(self):
        if not getattr(settings, "AMAZON_SP_API_ENABLED", False):
            raise MarketplaceDisabled("Amazon integration is disabled. Set AMAZON_SP_API_ENABLED and credentials.")

    def import_orders(self, created_after):  # pragma: no cover - phase 2
        raise NotImplementedError("Phase 2: GET /orders/v0/orders, map SKUs via SkuMapping, create Orders.")

    def push_inventory(self, mappings):  # pragma: no cover - phase 2
        raise NotImplementedError("Phase 2: Listings/FBA inventory APIs for mappings with sync_inventory=True.")

    def fetch_settlements(self, since):  # pragma: no cover - phase 2
        raise NotImplementedError("Phase 2: Finances API → separate fee/TCS/settlement entries.")
