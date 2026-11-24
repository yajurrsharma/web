import json

from django.conf import settings
from django.contrib import messages
from django.db import connection
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import cache_control, never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .emails import notify_admins
from .forms import ContactForm
from .models import Banner, Page, SiteSettings
from .utils import client_ip, rate_limited


@require_GET
def home(request):
    from apps.products.models import Category, Product

    from django.db.models import Q

    products = Product.objects.storefront()
    categories = list(Category.objects.filter(is_active=True, parent__isnull=True)[:8])
    # Shop-style home: a photo box per category (up to 4 products each), round category tiles,
    # combos, deals and real best sellers.
    category_cards, category_tiles = [], []
    for c in categories:
        items = list(products.filter(category_id__in=c.descendant_ids()).order_by("-is_featured", "-created_at")[:4])
        if items and len(category_cards) < 4:
            category_cards.append((c, items))
        image = c.image if c.image else next((p.main_image for p in items if p.main_image), None)
        category_tiles.append((c, image))
    latest = list(products.order_by("-created_at")[:12])
    deals = sorted((p for p in latest if (v := p.default_variant()) and v.discount_percent),
                   key=lambda p: -p.default_variant().discount_percent)[:10]
    combos = list(products.filter(Q(name__icontains="combo") | Q(category__slug__icontains="combo"))[:8])
    context = {
        "banners": Banner.objects.filter(is_active=True)[:6],
        "featured": products.filter(is_featured=True)[:10],
        "latest": latest,
        "deals": deals,
        "combos": combos,
        "best_sellers": _best_sellers(products),
        "categories": categories,
        "category_cards": category_cards,
        "category_tiles": category_tiles,
    }
    return render(request, "core/home.html", context)


def _best_sellers(products, days=90, limit=10):
    """Products ranked by units actually sold (cancelled orders excluded). Empty until there are sales."""
    from datetime import timedelta

    from django.db.models import Sum
    from django.utils import timezone

    from apps.orders.models import Order, OrderItem

    since = timezone.now() - timedelta(days=days)
    ranked = (OrderItem.objects.filter(order__created_at__gte=since, variant__isnull=False)
              .exclude(order__status__in=[Order.CANCELLED, Order.PENDING])
              .values("variant__product_id").annotate(qty=Sum("quantity")).order_by("-qty")[:limit])
    ids = [r["variant__product_id"] for r in ranked]
    by_id = {p.pk: p for p in products.filter(pk__in=ids)}
    return [by_id[i] for i in ids if i in by_id]


@require_GET
def page(request, slug):
    obj = get_object_or_404(Page, slug=slug, is_published=True)
    return render(request, "core/page.html", {"page": obj})


@require_http_methods(["GET", "POST"])
def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == "POST":
        if rate_limited(f"contact:{client_ip(request)}", limit=5, window_seconds=3600):
            messages.error(request, "Too many messages from your connection. Please try again later.")
            return redirect("core:contact")
        if form.is_valid():
            if form.cleaned_data.get("website"):
                # Honeypot hit: pretend success, store nothing.
                messages.success(request, "Thank you — we'll get back to you soon.")
                return redirect("core:contact")
            msg = form.save()
            notify_admins("contact_message", {"msg": msg}, related=f"contact:{msg.pk}")
            messages.success(request, "Thank you — we'll get back to you soon.")
            return redirect("core:contact")
    return render(request, "core/contact.html", {"form": form})


@require_GET
def business_details(request):
    return render(request, "core/business_details.html")


@require_GET
def grievance(request):
    return render(request, "core/grievance.html")


@require_GET
def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /" + settings.ADMIN_URL_PATH,
        "Disallow: /dashboard/",
        "Disallow: /account/",
        "Disallow: /accounts/",
        "Disallow: /cart/",
        "Disallow: /checkout/",
        "Disallow: /orders/",
        "Disallow: /payments/",
        f"Sitemap: {settings.SITE_URL}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


@require_GET
@cache_control(max_age=3600)
def manifest(request):
    store = SiteSettings.load()
    data = {
        "name": store.store_name,
        "short_name": store.store_name[:12],
        "description": store.tagline,
        "start_url": "/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "background_color": "#FBF6EC",
        "theme_color": "#6E1423",
        "lang": "en-IN",
        "icons": [
            {"src": static("img/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": static("img/icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": static("img/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }
    return HttpResponse(json.dumps(data), content_type="application/manifest+json")


@require_GET
def service_worker(request):
    offline_url = reverse("core:offline")
    css = static("css/site.css")
    js = f"""
const CACHE = 'lld-v1';
const OFFLINE = '{offline_url}';
self.addEventListener('install', e => {{
  e.waitUntil(caches.open(CACHE).then(c => c.addAll([OFFLINE, '{css}'])));
  self.skipWaiting();
}});
self.addEventListener('activate', e => {{
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
}});
// Network-first for pages (prices/stock must be live); offline page as fallback.
self.addEventListener('fetch', e => {{
  if (e.request.method !== 'GET') return;
  if (e.request.mode === 'navigate') {{
    e.respondWith(fetch(e.request).catch(() => caches.match(OFFLINE)));
  }}
}});
"""
    resp = HttpResponse(js, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    resp["Cache-Control"] = "no-cache"
    return resp


@require_GET
def offline(request):
    return render(request, "core/offline.html")


@require_GET
def assetlinks(request):
    """Digital Asset Links for the Play Store Trusted Web Activity."""
    package = getattr(settings, "ANDROID_PACKAGE_NAME", "")
    fingerprints = getattr(settings, "ANDROID_SHA256_FINGERPRINTS", [])
    if not package or not fingerprints:
        raise Http404
    data = [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {"namespace": "android_app", "package_name": package,
                   "sha256_cert_fingerprints": fingerprints},
    }]
    return JsonResponse(data, safe=False)


@require_GET
@never_cache
def healthz(request):
    """Lightweight check that the app and database respond. Exposes no details."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db_ok = True
    except Exception:  # pragma: no cover - only on DB outage
        db_ok = False
    return JsonResponse({"status": "ok" if db_ok else "degraded"}, status=200 if db_ok else 503)


def error_403(request, exception=None):
    return render(request, "errors/403.html", status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


def error_500(request):
    # Rendered without context processors so a DB fault can't break the error page.
    from django.template import loader

    return HttpResponse(loader.get_template("errors/500.html").render(), status=500)
