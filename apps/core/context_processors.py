from django.conf import settings

from .models import Page, SiteSettings


def site(request):
    """Expose only non-secret values to templates."""
    from apps.cart.services import cart_item_count

    store = SiteSettings.load()
    return {
        "SITE_NAME": store.store_name or settings.SITE_NAME,
        "SITE_URL": settings.SITE_URL,
        "store": store,
        "footer_pages": Page.objects.filter(is_published=True, show_in_footer=True).only("slug", "title"),
        "cart_count": cart_item_count(request),
        "nav_categories": _nav_categories(),
        "offer_bar": _offer_bar(store),
        "pending_reviews": _pending_reviews(request),
        "ADMIN_URL": settings.ADMIN_URL_PATH if getattr(request, "user", None) and request.user.is_staff else "",
    }


def _nav_categories():
    from apps.products.models import Category

    return Category.objects.filter(is_active=True, parent__isnull=True).only("name", "slug", "tagline",
                                                                                "image")[:8]


def _offer_bar(store):
    """Messages for the top offer bar: the text from Store settings + live offers marked 'show in offer bar'."""
    from apps.cart.models import Promotion

    items = [part.strip() for part in (store.announcement_text or "").split("|") if part.strip()]
    items += [p.name for p in Promotion.objects.live().filter(show_in_bar=True).only("name")[:5]]
    return items[:8]


def _pending_reviews(request):
    """Count shown in the staff menu; nothing is queried for customers."""
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and user.is_staff):
        return 0
    from apps.products.models import Review

    return Review.objects.filter(status=Review.PENDING).count()
