from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .models import Cart
from .services import merge_carts

GUEST_CART_SESSION_KEY = "_guest_cart_id"


@receiver(user_logged_in)
def merge_guest_cart(sender, request, user, **kwargs):
    if request is None:
        return
    cart_id = request.session.pop(GUEST_CART_SESSION_KEY, None)
    if cart_id:
        merge_carts(Cart.objects.filter(pk=cart_id, user__isnull=True).first(), user)


@receiver(user_logged_out)
def forget_cart(sender, request, user, **kwargs):
    if request is not None:
        request.session.pop(GUEST_CART_SESSION_KEY, None)
