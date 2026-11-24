"""
Pricing & GST engine — the single source of truth for every amount shown in the
cart, charged at checkout and printed on invoices.

Rules implemented (all configurable in Store settings; confirm with your CA):
- Selling prices are GST-inclusive by default (`prices_include_gst`). Taxable
  value = gross × 100 / (100 + rate). If off, GST is added on top.
- Automatic offers (Buy X Get Y, any-N bundles) come first, then the coupon, then the prepaid
  discount. Every discount is applied before tax and spread over the lines it belongs to, so each
  line's taxable value reflects it. A free gift is a ₹0 line (no tax on ₹0; confirm treatment with
  your CA).
- Shipping (+ COD fee) is taxed at the highest item rate (composite supply), a
  fixed rate, or not at all.
- Intra-state supply (place of supply == seller state) → CGST + SGST (half each);
  otherwise IGST. If either state is unknown, the split is left undetermined.
- Every amount is rounded to paise with ROUND_HALF_UP. CGST is rounded and SGST
  is the remainder so the two always add up to the line tax exactly.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from apps.core.utils import ZERO, money

HUNDRED = Decimal("100")


@dataclass
class QuoteLine:
    variant: object
    quantity: int
    unit_price: Decimal
    mrp: Decimal
    gross: Decimal
    gst_rate: Decimal
    hsn_code: str
    discount: Decimal = ZERO
    taxable: Decimal = ZERO
    tax: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    igst: Decimal = ZERO
    total: Decimal = ZERO
    is_gift: bool = False
    offer_name: str = ""


@dataclass
class Quote:
    lines: list = field(default_factory=list)
    subtotal: Decimal = ZERO           # gross item value before discount
    discount: Decimal = ZERO           # all discounts: offers + coupon + prepaid
    offer_discount: Decimal = ZERO
    offers_applied: list = field(default_factory=list)   # [(offer name, amount)]
    offer_hints: list = field(default_factory=list)      # ["Add 1 more … to get 1 free"]
    coupon_discount: Decimal = ZERO
    prepaid_discount: Decimal = ZERO
    coupon_code: str = ""
    shipping_gross: Decimal = ZERO      # what the customer pays for delivery (+COD fee)
    cod_fee: Decimal = ZERO
    shipping_taxable: Decimal = ZERO
    shipping_tax: Decimal = ZERO
    shipping_gst_rate: Decimal = ZERO
    shipping_cgst: Decimal = ZERO
    shipping_sgst: Decimal = ZERO
    shipping_igst: Decimal = ZERO
    taxable_total: Decimal = ZERO       # items + shipping
    tax_total: Decimal = ZERO
    cgst_total: Decimal = ZERO
    sgst_total: Decimal = ZERO
    igst_total: Decimal = ZERO
    round_off: Decimal = ZERO
    grand_total: Decimal = ZERO
    is_intra_state: Optional[bool] = None
    prices_include_gst: bool = True
    free_shipping_gap: Optional[Decimal] = None

    @property
    def item_lines(self):
        """Lines the customer put in the cart (same order as the cart items)."""
        return [line for line in self.lines if not line.is_gift]

    @property
    def gift_lines(self):
        return [line for line in self.lines if line.is_gift]

    @property
    def item_count(self):
        return sum(line.quantity for line in self.item_lines)

    @property
    def savings_vs_mrp(self):
        return money(sum((line.mrp - line.unit_price) * line.quantity for line in self.item_lines
                         if line.mrp > line.unit_price))

    @property
    def total_savings(self):
        """Everything the customer saves: MRP difference + offers + coupon + prepaid + free gifts."""
        gifts = sum((line.mrp * line.quantity for line in self.gift_lines), ZERO)
        return money(self.savings_vs_mrp + self.discount + gifts)


PREPAID_METHODS = ("online", "upi", "bank")


def _split(tax, intra):
    if intra is None:
        return ZERO, ZERO, ZERO
    if intra:
        cgst = money(tax / 2)
        return cgst, tax - cgst, ZERO
    return ZERO, ZERO, tax


def _tax_from(amount, rate, inclusive):
    """Return (taxable, tax) for an amount at a rate."""
    if rate <= 0:
        return money(amount), ZERO
    if inclusive:
        taxable = money(amount * HUNDRED / (HUNDRED + rate))
        return taxable, money(amount) - taxable
    taxable = money(amount)
    return taxable, money(taxable * rate / HUNDRED)


def _spread(lines, amount):
    """Add `amount` to the lines' discounts in proportion to what is still left on each line."""
    bases = [line.gross - line.discount for line in lines]
    total = sum(bases, ZERO)
    if amount <= 0 or total <= 0:
        return ZERO
    amount = min(amount, total)
    remaining = amount
    payable = [i for i, b in enumerate(bases) if b > 0]
    for n, i in enumerate(payable):
        share = remaining if n == len(payable) - 1 else min(money(amount * bases[i] / total), bases[i])
        share = min(share, bases[i])
        lines[i].discount += share
        remaining -= share
    return amount - remaining


def _apply_offers(quote, promotions):
    """Buy X Get Y and any-N bundles. Each cart unit is used by one offer at most."""
    from .models import Promotion

    units = []  # [line index, unit price, used]
    for i, line in enumerate(quote.lines):
        units.extend([i, line.unit_price, False] for _ in range(line.quantity))
    per_line = [ZERO] * len(quote.lines)
    for promo in promotions:
        if promo.kind not in (Promotion.BUY_X_GET_Y, Promotion.BUNDLE):
            continue
        eligible = {p.pk for p in promo.products.all()}
        cand = sorted((u for u in units if not u[2] and (not eligible or
                       quote.lines[u[0]].variant.product_id in eligible)), key=lambda u: u[1], reverse=True)
        size = promo.buy_qty + promo.free_qty if promo.kind == Promotion.BUY_X_GET_Y else promo.bundle_qty
        saved = ZERO
        for g in range(len(cand) // size):
            chunk = cand[g * size:(g + 1) * size]
            if promo.kind == Promotion.BUY_X_GET_Y:
                for u in chunk[-promo.free_qty:]:  # the cheapest ones are free
                    per_line[u[0]] += u[1]
                    saved += u[1]
            else:
                chunk_total = sum((u[1] for u in chunk), ZERO)
                off = money(chunk_total - promo.bundle_price)
                if off <= 0:
                    continue
                left = off
                for n, u in enumerate(chunk):
                    share = left if n == len(chunk) - 1 else money(off * u[1] / chunk_total)
                    per_line[u[0]] += share
                    left -= share
                saved += off
            for u in chunk:
                u[2] = True
        if saved > 0:
            quote.offers_applied.append((promo.name, money(saved)))
        rest = len(cand) % size
        if rest:
            quote.offer_hints.append(f"Add {size - rest} more eligible item{'s' if size - rest > 1 else ''} "
                                     f"to get “{promo.name}”.")
    for i, line in enumerate(quote.lines):
        line.discount += min(money(per_line[i]), line.gross)
    quote.offer_discount = money(sum((line.discount for line in quote.lines), ZERO))


def _add_gifts(quote, promotions, net_items):
    from .models import Promotion

    for promo in promotions:
        if promo.kind != Promotion.FREE_GIFT or promo.gift_variant is None:
            continue
        gift = promo.gift_variant
        if net_items >= promo.min_amount:
            if gift.is_active and gift.stock > 0 and gift.product.status != "archived":
                quote.lines.append(QuoteLine(
                    variant=gift, quantity=1, unit_price=ZERO, mrp=money(gift.mrp), gross=ZERO,
                    gst_rate=gift.product.tax_class.gst_rate, hsn_code=gift.product.tax_class.hsn_code,
                    is_gift=True, offer_name=promo.name))
                quote.offers_applied.append((promo.name, ZERO))
        else:
            quote.offer_hints.append(f"Add ₹{money(promo.min_amount - net_items)} more to get “{promo.name}”.")


def live_promotions():
    from .models import Promotion

    return list(Promotion.objects.live().select_related("gift_variant__product__tax_class")
                .prefetch_related("products"))


def build_quote(items, *, store, coupon=None, ship_state_code=None, payment_method=None, user=None,
                validate_coupon=True, promotions=None):
    """
    `items`: iterable of (variant, quantity); variants need product__tax_class loaded.
    Prices are always read from the variant (database), never from the browser.
    `promotions`: active automatic offers (loaded from the database when not given).
    """
    inclusive = store.prices_include_gst
    quote = Quote(prices_include_gst=inclusive)
    seller_state = store.state_code or None
    intra = None if not (seller_state and ship_state_code) else (seller_state == ship_state_code)
    quote.is_intra_state = intra

    for variant, qty in items:
        price = money(variant.price)
        quote.lines.append(QuoteLine(
            variant=variant, quantity=qty, unit_price=price, mrp=money(variant.mrp),
            gross=money(price * qty), gst_rate=variant.product.tax_class.gst_rate,
            hsn_code=variant.product.tax_class.hsn_code))
    quote.subtotal = money(sum((line.gross for line in quote.lines), ZERO))
    if promotions is None:
        promotions = live_promotions() if quote.lines else []

    # --- Automatic offers ---
    if quote.lines:
        _apply_offers(quote, promotions)

    # --- Coupon (on what is left after offers) ---
    after_offers = quote.subtotal - quote.offer_discount
    if coupon is not None and after_offers > 0:
        if validate_coupon:
            coupon.check_eligible(user, after_offers)
        quote.coupon_discount = _spread(quote.lines, coupon.discount_for(after_offers))
        quote.coupon_code = coupon.code

    # --- Shipping threshold is judged on the value after offers and coupon ---
    net_items = quote.subtotal - quote.offer_discount - quote.coupon_discount
    prepaid = payment_method in PREPAID_METHODS
    pct = getattr(store, "prepaid_discount_percent", ZERO) or ZERO
    if prepaid and pct > 0 and net_items > 0:
        quote.prepaid_discount = _spread(quote.lines, money(net_items * pct / HUNDRED))
    quote.discount = money(quote.offer_discount + quote.coupon_discount + quote.prepaid_discount)

    # --- Free gifts (₹0 lines) ---
    if quote.lines:
        _add_gifts(quote, promotions, net_items)

    # --- Item tax ---
    for line in quote.lines:
        line.taxable, line.tax = _tax_from(line.gross - line.discount, line.gst_rate, inclusive)
        line.cgst, line.sgst, line.igst = _split(line.tax, intra)
        line.total = line.taxable + line.tax

    # --- Shipping ---
    shipping = money(store.shipping_flat_charge) if quote.lines else ZERO
    threshold = store.free_shipping_threshold
    if threshold is not None and net_items >= threshold:
        shipping = ZERO
    elif threshold is not None and quote.lines:
        quote.free_shipping_gap = money(threshold - net_items)
    if prepaid and getattr(store, "prepaid_free_shipping", False):
        shipping = ZERO
    if payment_method == "cod" and quote.lines:
        quote.cod_fee = money(store.cod_fee)
    quote.shipping_gross = shipping + quote.cod_fee
    if store.shipping_tax_mode == store.SHIPPING_TAX_PRINCIPAL:
        rate = max((line.gst_rate for line in quote.lines), default=ZERO)
    elif store.shipping_tax_mode == store.SHIPPING_TAX_FIXED:
        rate = store.shipping_tax_rate
    else:
        rate = ZERO
    quote.shipping_gst_rate = rate
    quote.shipping_taxable, quote.shipping_tax = _tax_from(quote.shipping_gross, rate, inclusive)
    quote.shipping_cgst, quote.shipping_sgst, quote.shipping_igst = _split(quote.shipping_tax, intra)

    # --- Totals ---
    quote.taxable_total = sum((line.taxable for line in quote.lines), ZERO) + quote.shipping_taxable
    quote.tax_total = sum((line.tax for line in quote.lines), ZERO) + quote.shipping_tax
    quote.cgst_total = sum((line.cgst for line in quote.lines), ZERO) + quote.shipping_cgst
    quote.sgst_total = sum((line.sgst for line in quote.lines), ZERO) + quote.shipping_sgst
    quote.igst_total = sum((line.igst for line in quote.lines), ZERO) + quote.shipping_igst
    total = quote.taxable_total + quote.tax_total
    if store.round_off_invoice_total:
        rounded = total.quantize(Decimal("1"), rounding="ROUND_HALF_UP")
        quote.round_off = money(rounded - total)
        total = money(rounded)
    quote.grand_total = money(total)
    return quote
