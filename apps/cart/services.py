from django.db import transaction

from apps.core.models import SiteSettings

from .models import MAX_QTY_PER_LINE, Cart, CartItem
from .pricing import build_quote


def _session_key(request, create):
    if not request.session.session_key and create:
        request.session.save()
    return request.session.session_key


def get_cart(request, create=False):
    if request.user.is_authenticated:
        if create:
            cart, _ = Cart.objects.get_or_create(user=request.user)
            return cart
        return Cart.objects.filter(user=request.user).first()
    key = _session_key(request, create)
    if not key:
        return None
    if create:
        from .signals import GUEST_CART_SESSION_KEY

        cart, _ = Cart.objects.get_or_create(session_key=key, user=None)
        # Session data survives login(); the session key does not. Remember the id for merging.
        request.session[GUEST_CART_SESSION_KEY] = cart.pk
        return cart
    return Cart.objects.filter(session_key=key, user=None).first()


def cart_items(cart):
    if cart is None:
        return []
    return list(cart.items.select_related("variant__product__tax_class", "variant__product__category"))


def validate_cart(cart):
    """
    Bring the cart in line with current stock and availability.
    Returns a list of human-readable notices for anything that changed.
    """
    notices = []
    for item in cart_items(cart):
        variant = item.variant
        if not variant.is_purchasable:
            notices.append(f"“{variant}” is no longer available and was removed from your cart.")
            item.delete()
        elif item.quantity > variant.stock:
            notices.append(f"Only {variant.stock} of “{variant}” left — quantity updated.")
            item.quantity = variant.stock
            item.save(update_fields=["quantity"])
    return notices


def add_item(cart, variant, quantity):
    quantity = max(1, min(int(quantity), MAX_QTY_PER_LINE))
    with transaction.atomic():
        item, created = CartItem.objects.select_for_update().get_or_create(
            cart=cart, variant=variant, defaults={"quantity": 0})
        requested = item.quantity + quantity
        new_qty = min(requested, variant.stock, MAX_QTY_PER_LINE)
        if new_qty < 1:
            item.delete()
            return None, True
        item.quantity = new_qty
        item.save()
    return item, new_qty < requested


def merge_carts(guest_cart, user):
    if guest_cart is None or guest_cart.user_id:
        return
    user_cart, _ = Cart.objects.get_or_create(user=user)
    if guest_cart.pk == user_cart.pk:
        return
    for item in cart_items(guest_cart):
        if item.variant.is_purchasable:
            add_item(user_cart, item.variant, item.quantity)
    if guest_cart.coupon_id and not user_cart.coupon_id:
        user_cart.coupon_id = guest_cart.coupon_id
        user_cart.save(update_fields=["coupon"])
    guest_cart.delete()


def cart_item_count(request):
    cached = getattr(request, "_cart_count", None)
    if cached is not None:
        return cached
    count = 0
    if hasattr(request, "session"):
        cart = get_cart(request)
        if cart:
            count = sum(cart.items.values_list("quantity", flat=True))
    request._cart_count = count
    return count


def quote_for_cart(cart, *, user=None, ship_state_code=None, payment_method=None):
    """Build a quote; drops an ineligible coupon and reports why."""
    from .models import CouponError

    store = SiteSettings.load()
    items = [(i.variant, i.quantity) for i in cart_items(cart)]
    coupon = cart.coupon if cart else None
    coupon_error = None
    if coupon is not None:
        try:
            coupon.check_eligible(user, sum((v.price * q for v, q in items), 0))
        except CouponError as exc:
            coupon_error = str(exc)
            coupon = None
    quote = build_quote(items, store=store, coupon=coupon, ship_state_code=ship_state_code,
                        payment_method=payment_method, user=user, validate_coupon=False)
    return quote, coupon_error
