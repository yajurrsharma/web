from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.core.models import SiteSettings
from apps.core.utils import client_ip, rate_limited
from apps.products.models import ProductVariant

from .models import MAX_QTY_PER_LINE, CartItem, Coupon, CouponError
from .services import add_item, cart_items, get_cart, quote_for_cart, validate_cart


def _wants_json(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _default_state(request):
    if request.user.is_authenticated:
        address = request.user.addresses.filter(is_default=True).first()
        return address.state_code if address else None
    return None


@require_GET
def cart_detail(request):
    cart = get_cart(request)
    notices = validate_cart(cart) if cart else []
    for notice in notices:
        messages.warning(request, notice)
    quote, coupon_error = (quote_for_cart(cart, user=request.user, ship_state_code=_default_state(request))
                           if cart else (None, None))
    if coupon_error:
        messages.warning(request, f"Coupon removed: {coupon_error}")
        cart.coupon = None
        cart.save(update_fields=["coupon"])
    items = cart_items(cart)
    threshold = SiteSettings.load().free_shipping_threshold
    ship_pct = None  # free-delivery progress bar (0–100), shown only when a threshold is set
    if quote and quote.item_lines and threshold:
        reached = threshold - (quote.free_shipping_gap or 0)
        ship_pct = max(0, min(100, int(reached * 100 / threshold)))
    return render(request, "cart/cart.html", {
        "cart": cart, "items": items, "quote": quote,
        "lines": list(zip(items, quote.item_lines, strict=True)) if quote else [],
        "max_qty": MAX_QTY_PER_LINE, "ship_pct": ship_pct,
    })


@require_POST
def add(request):
    variant = get_object_or_404(ProductVariant.objects.select_related("product"),
                                pk=request.POST.get("variant_id"))
    try:
        qty = int(request.POST.get("quantity", 1))
    except (TypeError, ValueError):
        qty = 1
    if not variant.is_purchasable:
        msg = "Sorry, this item is out of stock."
        if _wants_json(request):
            return JsonResponse({"ok": False, "message": msg}, status=409)
        messages.error(request, msg)
        return redirect(variant.product.get_absolute_url())
    cart = get_cart(request, create=True)
    item, capped = add_item(cart, variant, qty)
    msg = (f"Only {variant.stock} available — your cart now has the maximum." if capped
           else f"Added “{variant}” to your cart.")
    count = sum(cart.items.values_list("quantity", flat=True))
    if _wants_json(request):
        return JsonResponse({"ok": True, "message": msg, "cart_count": count})
    (messages.warning if capped else messages.success)(request, msg)
    if request.POST.get("buy_now") == "1":
        return redirect("orders:checkout")
    return redirect("cart:detail")


@require_POST
def update(request, item_id):
    cart = get_cart(request)
    item = get_object_or_404(CartItem, pk=item_id, cart=cart)  # only your own cart
    try:
        qty = int(request.POST.get("quantity", item.quantity))
    except (TypeError, ValueError):
        qty = item.quantity
    if qty <= 0:
        item.delete()
        messages.info(request, "Item removed.")
    else:
        new_qty = min(qty, item.variant.stock, MAX_QTY_PER_LINE)
        if new_qty < qty:
            messages.warning(request, f"Only {item.variant.stock} of “{item.variant}” available.")
        if new_qty < 1:
            item.delete()
        else:
            item.quantity = new_qty
            item.save(update_fields=["quantity"])
    return redirect("cart:detail")


@require_POST
def remove(request, item_id):
    cart = get_cart(request)
    item = get_object_or_404(CartItem, pk=item_id, cart=cart)
    item.delete()
    messages.info(request, "Item removed.")
    return redirect("cart:detail")


@require_POST
def apply_coupon(request):
    if rate_limited(f"coupon:{client_ip(request)}", limit=20, window_seconds=600):
        messages.error(request, "Too many coupon attempts. Please wait a few minutes.")
        return redirect("cart:detail")
    cart = get_cart(request)
    code = (request.POST.get("code") or "").strip().upper()[:30]
    if not cart or not code:
        return redirect("cart:detail")
    coupon = Coupon.objects.filter(code=code).first()
    if coupon is None:
        messages.error(request, "That coupon code isn't valid.")
        return redirect("cart:detail")
    subtotal = sum(i.variant.price * i.quantity for i in cart_items(cart))
    try:
        coupon.check_eligible(request.user, subtotal)
    except CouponError as exc:
        messages.error(request, str(exc))
        return redirect("cart:detail")
    cart.coupon = coupon
    cart.save(update_fields=["coupon"])
    messages.success(request, f"Coupon {coupon.code} applied.")
    return redirect(request.POST.get("next") if request.POST.get("next") == "/checkout/" else "cart:detail")


@require_POST
def remove_coupon(request):
    cart = get_cart(request)
    if cart and cart.coupon_id:
        cart.coupon = None
        cart.save(update_fields=["coupon"])
        messages.info(request, "Coupon removed.")
    return redirect(request.POST.get("next") if request.POST.get("next") == "/checkout/" else "cart:detail")
