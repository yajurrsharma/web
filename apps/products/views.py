from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from .forms import ReviewForm
from .models import Category, Product, Review

SORTS = {
    "newest": ("-created_at", "Newest"),
    "price_asc": ("min_price", "Price: low to high"),
    "price_desc": ("-min_price", "Price: high to low"),
    "name": ("name", "Name A–Z"),
}
PER_PAGE = 12


def _price(value):
    try:
        v = Decimal(value)
        return v if v >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _listing(request, base_qs, *, category=None, template="products/shop.html", extra=None):
    qs = base_qs
    q = (request.GET.get("q") or "").strip()[:100]
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(short_description__icontains=q)
                       | Q(category__name__icontains=q) | Q(variants__sku__iexact=q))
    min_p, max_p = _price(request.GET.get("min")), _price(request.GET.get("max"))
    if min_p is not None:
        qs = qs.filter(min_price__gte=min_p)
    if max_p is not None:
        qs = qs.filter(min_price__lte=max_p)
    if request.GET.get("in_stock") == "1":
        qs = qs.filter(variants__is_active=True, variants__stock__gt=0)
    sort = request.GET.get("sort", "newest")
    if sort not in SORTS:
        sort = "newest"
    qs = qs.order_by(SORTS[sort][0], "pk").distinct()
    page = Paginator(qs, PER_PAGE).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    context = {
        "page_obj": page, "products": page.object_list, "q": q, "sort": sort,
        "sorts": [(k, v[1]) for k, v in SORTS.items()], "min": request.GET.get("min", ""),
        "max": request.GET.get("max", ""), "in_stock": request.GET.get("in_stock") == "1",
        "querystring": params.urlencode(), "category": category,
        "categories": Category.objects.filter(is_active=True, parent__isnull=True).prefetch_related("children"),
    }
    context.update(extra or {})
    return render(request, template, context)


@require_GET
def shop(request):
    return _listing(request, Product.objects.storefront())


@require_GET
def search(request):
    return _listing(request, Product.objects.storefront(), template="products/search.html")


@require_GET
def category(request, slug):
    cat = get_object_or_404(Category, slug=slug, is_active=True)
    qs = Product.objects.storefront().filter(category_id__in=cat.descendant_ids())
    return _listing(request, qs, category=cat, template="products/category.html")


@require_GET
def detail(request, slug):
    product = get_object_or_404(Product.objects.select_related("category", "tax_class"), slug=slug)
    is_preview = not product.is_published
    if is_preview and not (request.user.is_authenticated and request.user.is_staff):
        raise Http404
    variants = list(product.variants.filter(is_active=True).order_by("sort_order", "price"))
    if not variants and not is_preview:
        raise Http404
    selected = None
    vid = request.GET.get("v")
    if vid and vid.isdigit():
        selected = next((v for v in variants if v.pk == int(vid)), None)
    if selected is None:
        selected = next((v for v in variants if v.stock > 0), variants[0] if variants else None)
    related = (Product.objects.storefront().filter(category=product.category)
               .exclude(pk=product.pk).order_by("-is_featured", "-created_at")[:4])
    approved = product.reviews.filter(status=Review.APPROVED)
    counts = dict(approved.values_list("rating").annotate(n=Count("id")).order_by())
    total = sum(counts.values())
    return render(request, "products/detail.html", {
        "product": product, "variants": variants, "selected": selected, "related": related,
        "gallery": list(product.gallery()), "is_preview": is_preview,
        "jsonld": _jsonld(request, product, variants),
        "reviews": approved.select_related("user")[:20],
        "rating_bars": [{"stars": s, "pct": round(counts.get(s, 0) * 100 / total) if total else 0}
                        for s in range(5, 0, -1)],
        "can_review": request.user.is_authenticated and can_review(request.user, product),
    })


def delivered_purchase(user, product):
    """The customer's delivered order line for this product — what makes a review 'verified'."""
    from apps.orders.models import Order, OrderItem

    return (OrderItem.objects.filter(order__user=user, variant__product=product,
                                     order__status__in=[Order.DELIVERED, Order.RETURN_REQUESTED, Order.RETURNED])
            .order_by("-order__created_at").first())


def can_review(user, product):
    return (not Review.objects.filter(user=user, product=product).exists()
            and delivered_purchase(user, product) is not None)


@login_required
@require_http_methods(["GET", "POST"])
def write_review(request, slug):
    product = get_object_or_404(Product, slug=slug, status=Product.STATUS_PUBLISHED)
    if Review.objects.filter(user=request.user, product=product).exists():
        messages.info(request, "You have already reviewed this product. Thank you!")
        return redirect(product.get_absolute_url() + "#reviews")
    item = delivered_purchase(request.user, product)
    if item is None:
        messages.info(request, "You can review this product once an order with it has been delivered to you.")
        return redirect(product.get_absolute_url() + "#reviews")
    form = ReviewForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        review = form.save(commit=False)
        review.product, review.user, review.order_item = product, request.user, item
        try:
            with transaction.atomic():
                review.save()
        except IntegrityError:  # double click: the first one was saved
            pass
        messages.success(request, "Thank you for your review! It will appear on the product page after a quick check.")
        return redirect(product.get_absolute_url() + "#reviews")
    return render(request, "products/review_form.html", {"form": form, "product": product})


def _jsonld(request, product, variants):
    """Schema.org Product data: prices, availability and — only when real approved reviews exist — ratings."""
    import json

    from django.conf import settings
    from django.utils.safestring import mark_safe

    from apps.core.models import SiteSettings

    data = {
        "@context": "https://schema.org", "@type": "Product", "name": product.name,
        "description": product.short_description or product.name,
        "sku": variants[0].sku if variants else "",
        "brand": {"@type": "Brand", "name": SiteSettings.load().store_name or settings.SITE_NAME},
        "url": settings.SITE_URL + product.get_absolute_url(),
        "offers": [{
            "@type": "Offer", "sku": v.sku, "priceCurrency": "INR", "price": str(v.price),
            "availability": "https://schema.org/InStock" if v.stock > 0 else "https://schema.org/OutOfStock",
            "url": settings.SITE_URL + product.get_absolute_url() + f"?v={v.pk}",
        } for v in variants],
    }
    if product.main_image:
        data["image"] = request.build_absolute_uri(product.main_image.url)
    if product.rating_count:
        data["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": str(product.rating_avg),
                                   "reviewCount": product.rating_count}
    text = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return mark_safe(text)
