"""Staff pages for products, pack sizes, photos, categories, HSN & GST, stock-in, banners and shop settings."""
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Min, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.models import Banner, SiteSettings
from apps.core.themes import THEMES
from apps.core.utils import audit
from apps.inventory.models import StockMovement
from apps.inventory.services import InsufficientStock, adjust_stock
from apps.products.models import Category, Product, ProductMedia, ProductVariant, TaxClass

from .forms import (BannerForm, CategoryForm, GalleryUploadForm, NewVariantForm, ProductForm, StockInForm,
                    StoreSettingsForm, TaxClassForm, VariantForm, is_video)
from .views import staff_required


def _show_cost(user):
    return user.has_perm("products.view_cost_price")


# ---------------------------------------------------------------- products
@staff_required("products.view_product")
@require_http_methods(["GET"])
def product_list(request):
    qs = (Product.objects.select_related("category", "tax_class")
          .annotate(total_stock=Sum("variants__stock"), min_price=Min("variants__price"),
                    max_price=Max("variants__price"), n_variants=Count("variants", distinct=True)))
    q = (request.GET.get("q") or "").strip()
    status = request.GET.get("status", "")
    category = request.GET.get("category", "")
    if q:
        # Subquery, not a join: a join here would multiply the stock/price annotations.
        skus = ProductVariant.objects.filter(sku__icontains=q).values("product_id")
        qs = qs.filter(Q(name__icontains=q) | Q(pk__in=skus))
    if status in dict(Product.STATUS_CHOICES):
        qs = qs.filter(status=status)
    else:
        status = ""
        qs = qs.exclude(status=Product.STATUS_ARCHIVED)
    if category.isdigit():
        qs = qs.filter(category_id=int(category))
    page = Paginator(qs.order_by("name"), 30).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "dashboard/products.html", {
        "querystring": params.urlencode(),
        "page_obj": page, "products": page.object_list, "q": q, "status": status, "category": category,
        "statuses": Product.STATUS_CHOICES, "categories": Category.objects.order_by("sort_order", "name"),
        "counts": {s: Product.objects.filter(status=s).count() for s, _ in Product.STATUS_CHOICES},
    })


@staff_required("products.add_product")
@require_http_methods(["GET", "POST"])
def product_create(request):
    pform = ProductForm(request.POST or None, request.FILES or None, prefix="p")
    vform = NewVariantForm(request.POST or None, prefix="v", show_cost=_show_cost(request.user),
                           initial={"name": "Standard"})
    gform = GalleryUploadForm(request.POST or None, request.FILES or None, prefix="g")
    gform.fields["files"].required = False
    if request.method == "GET" and not (TaxClass.objects.exists() and Category.objects.exists()):
        messages.warning(request, "First add at least one category and one HSN & GST rate.")
    if request.method == "POST" and pform.is_valid() and vform.is_valid() and gform.is_valid():
        with transaction.atomic():
            product = pform.save()
            variant = vform.save(commit=False)
            variant.product = product
            variant.stock = 0
            variant.save()
            opening = vform.cleaned_data["opening_stock"]
            if opening:
                adjust_stock(variant.pk, opening, StockMovement.OPENING, user=request.user,
                             note="Opening stock (product added)")
            _save_gallery(product, gform.cleaned_data.get("files") or [])
            audit("product.create", f"Added product '{product.name}' ({variant.sku})", actor=request.user,
                  obj=product, request=request)
        messages.success(request, f"Product '{product.name}' saved." + (
            "" if product.is_published else " It is a draft — set Status to Published to show it in the shop."))
        return redirect("dashboard:product_edit", pk=product.pk)
    return render(request, "dashboard/product_form.html", {
        "pform": pform, "vform": vform, "gform": gform, "creating": True})


@staff_required("products.view_product")
@require_http_methods(["GET", "POST"])
def product_edit(request, pk):
    product = get_object_or_404(Product.objects.select_related("category", "tax_class"), pk=pk)
    can_change = request.user.has_perm("products.change_product")
    pform = ProductForm(request.POST or None, request.FILES or None, instance=product, prefix="p")
    gform = GalleryUploadForm(request.POST or None, request.FILES or None, prefix="g", product=product)
    gform.fields["files"].required = False
    can_upload = request.user.has_perm("products.add_productmedia")
    if request.method == "POST":
        if not can_change:
            messages.error(request, "You do not have permission to change products.")
            return redirect("dashboard:product_edit", pk=pk)
        if pform.is_valid() and gform.is_valid():
            new_files = (gform.cleaned_data.get("files") or []) if can_upload else []
            with transaction.atomic():
                pform.save()
                _save_gallery(product, new_files)
            if pform.changed_data:
                audit("product.change", f"Product '{product.name}' changed: {', '.join(pform.changed_data)}",
                      actor=request.user, obj=product, request=request)
            messages.success(request, "Product details saved." + (
                f" {len(new_files)} photo/video file(s) added." if new_files else ""))
            return redirect("dashboard:product_edit", pk=pk)
    return render(request, "dashboard/product_form.html", {
        "pform": pform, "product": product, "creating": False, "can_change": can_change,
        "variants": product.variants.order_by("sort_order", "price"),
        "media": product.gallery(), "gform": gform, "can_upload": can_upload,
        "show_cost": _show_cost(request.user),
    })


# ---------------------------------------------------------------- pack sizes (variants)
@staff_required("products.add_productvariant")
@require_http_methods(["GET", "POST"])
def variant_create(request, product_pk):
    product = get_object_or_404(Product, pk=product_pk)
    form = NewVariantForm(request.POST or None, show_cost=_show_cost(request.user))
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            variant = form.save(commit=False)
            variant.product = product
            variant.stock = 0
            variant.save()
            if form.cleaned_data["opening_stock"]:
                adjust_stock(variant.pk, form.cleaned_data["opening_stock"], StockMovement.OPENING,
                             user=request.user, note="Opening stock (pack size added)")
            audit("variant.create", f"Added pack size {variant.sku} to '{product.name}'", actor=request.user,
                  obj=variant, request=request)
        messages.success(request, f"Pack size '{variant.name}' ({variant.sku}) added.")
        return redirect("dashboard:product_edit", pk=product.pk)
    return render(request, "dashboard/variant_form.html", {"form": form, "product": product, "creating": True})


@staff_required("products.change_productvariant")
@require_http_methods(["GET", "POST"])
def variant_edit(request, pk):
    variant = get_object_or_404(ProductVariant.objects.select_related("product"), pk=pk)
    form = VariantForm(request.POST or None, instance=variant, show_cost=_show_cost(request.user))
    if request.method == "POST" and form.is_valid():
        form.save()
        if form.changed_data:
            audit("variant.change", f"{variant.sku} changed: {', '.join(form.changed_data)}", actor=request.user,
                  obj=variant, request=request)
        messages.success(request, f"{variant.sku} saved.")
        return redirect("dashboard:product_edit", pk=variant.product_id)
    return render(request, "dashboard/variant_form.html", {
        "form": form, "product": variant.product, "variant": variant, "creating": False,
        "movements": variant.movements.select_related("order", "created_by")[:10]})


# ---------------------------------------------------------------- photos & videos
def _save_gallery(product, files):
    start = (product.media.aggregate(m=Max("sort_order"))["m"] or 0) + 1
    for i, f in enumerate(files):
        kind = ProductMedia.KIND_VIDEO if is_video(f) else ProductMedia.KIND_IMAGE
        ProductMedia.objects.create(product=product, kind=kind, file=f, alt_text=product.name[:150],
                                    sort_order=start + i)


@staff_required("products.add_productmedia")
@require_POST
def gallery_upload(request, product_pk):
    product = get_object_or_404(Product, pk=product_pk)
    form = GalleryUploadForm(request.POST, request.FILES, prefix="g", product=product)
    if form.is_valid():
        _save_gallery(product, form.cleaned_data["files"])
        messages.success(request, f"{len(form.cleaned_data['files'])} file(s) added to the gallery.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect(reverse("dashboard:product_edit", args=[product.pk]) + "#photos")


@staff_required("products.change_productmedia")
@require_POST
def gallery_update(request, product_pk):
    product = get_object_or_404(Product, pk=product_pk)
    delete_id = request.POST.get("delete")
    if delete_id:
        if not request.user.has_perm("products.delete_productmedia"):
            messages.error(request, "You do not have permission to delete photos.")
        else:
            item = get_object_or_404(ProductMedia, pk=delete_id, product=product)
            item.file.delete(save=False)
            item.delete()
            messages.success(request, "File removed from the gallery.")
        return redirect(reverse("dashboard:product_edit", args=[product.pk]) + "#photos")
    for item in product.media.all():
        raw = request.POST.get(f"order_{item.pk}", "").strip()
        alt = request.POST.get(f"alt_{item.pk}")
        fields = []
        if raw.isdigit():
            item.sort_order = int(raw)
            fields.append("sort_order")
        if alt is not None:
            item.alt_text = alt.strip()[:150]
            fields.append("alt_text")
        if fields:
            item.save(update_fields=fields)
    messages.success(request, "Gallery saved.")
    return redirect(reverse("dashboard:product_edit", args=[product.pk]) + "#photos")


# ---------------------------------------------------------------- stock in
@staff_required("inventory.add_stockmovement")
@require_http_methods(["GET", "POST"])
def stock_in(request):
    initial = {}
    if request.GET.get("variant", "").isdigit():
        initial["variant"] = int(request.GET["variant"])
    form = StockInForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        variant = form.cleaned_data["variant"]
        reason = form.cleaned_data["reason"]
        try:
            movement = adjust_stock(variant.pk, form.cleaned_data["change"], reason, user=request.user,
                                    note=form.cleaned_data["note"])
        except InsufficientStock as exc:
            form.add_error("change", str(exc))
        else:
            audit("stock.adjust", f"{variant.sku}: {movement.change:+d} ({reason}) → {movement.stock_after}",
                  actor=request.user, obj=variant, request=request)
            messages.success(request, f"{variant}: {movement.change:+d}. Stock is now {movement.stock_after}.")
            return redirect("dashboard:stock_in")
    recent = StockMovement.objects.select_related("variant__product", "created_by", "order")[:15]
    return render(request, "dashboard/stock_in.html", {"form": form, "recent": recent})


# ---------------------------------------------------------------- categories
@staff_required("products.view_category")
@require_http_methods(["GET", "POST"])
def category_list(request, pk=None):
    obj = get_object_or_404(Category, pk=pk) if pk else None
    perm = "products.change_category" if obj else "products.add_category"
    can_save = request.user.has_perm(perm)
    form = CategoryForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST":
        if not can_save:
            messages.error(request, "You do not have permission to save categories.")
            return redirect("dashboard:categories")
        if form.is_valid():
            cat = form.save()
            messages.success(request, f"Category '{cat.name}' saved.")
            return redirect("dashboard:categories")
    cats = Category.objects.select_related("parent").annotate(n=Count("products")).order_by("sort_order", "name")
    return render(request, "dashboard/categories.html", {
        "form": form, "obj": obj, "categories": cats, "can_save": can_save})


# ---------------------------------------------------------------- HSN & GST
@staff_required("products.view_taxclass")
@require_http_methods(["GET", "POST"])
def tax_list(request, pk=None):
    obj = get_object_or_404(TaxClass, pk=pk) if pk else None
    perm = "products.change_taxclass" if obj else "products.add_taxclass"
    can_save = request.user.has_perm(perm)
    form = TaxClassForm(request.POST or None, instance=obj)
    if request.method == "POST":
        if not can_save:
            messages.error(request, "You do not have permission to change HSN & GST rates.")
            return redirect("dashboard:tax_classes")
        if form.is_valid():
            tax = form.save()
            audit("taxclass.change" if obj else "taxclass.create",
                  f"Tax class '{tax}' saved: {', '.join(form.changed_data)}", actor=request.user, obj=tax,
                  request=request)
            messages.success(request, f"'{tax.name}' saved.")
            return redirect("dashboard:tax_classes")
    taxes = TaxClass.objects.annotate(n=Count("products")).order_by("name")
    return render(request, "dashboard/tax_classes.html", {
        "form": form, "obj": obj, "taxes": taxes, "can_save": can_save})


# ---------------------------------------------------------------- home page banners
@staff_required("core.view_banner")
@require_http_methods(["GET", "POST"])
def banner_list(request, pk=None):
    obj = get_object_or_404(Banner, pk=pk) if pk else None
    can_save = request.user.has_perm("core.change_banner" if obj else "core.add_banner")
    form = BannerForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST":
        if not can_save:
            messages.error(request, "You do not have permission to change banners.")
            return redirect("dashboard:banners")
        if form.is_valid():
            banner = form.save()
            audit("banner.save", f"Home banner '{banner.heading}' saved", actor=request.user, obj=banner,
                  request=request)
            messages.success(request, f"Banner '{banner.heading}' saved.")
            return redirect("dashboard:banners")
    return render(request, "dashboard/banners.html", {
        "form": form, "obj": obj, "banners": Banner.objects.all(), "can_save": can_save})


@staff_required("core.delete_banner")
@require_POST
def banner_delete(request, pk):
    banner = get_object_or_404(Banner, pk=pk)
    if banner.image:
        banner.image.delete(save=False)
    audit("banner.delete", f"Home banner '{banner.heading}' deleted", actor=request.user, request=request)
    banner.delete()
    messages.success(request, "Banner deleted.")
    return redirect("dashboard:banners")


# ---------------------------------------------------------------- store settings (payments, cards, theme)
@staff_required("core.view_sitesettings")
@require_http_methods(["GET", "POST"])
def store_settings(request):
    store = SiteSettings.load()
    can_save = request.user.has_perm("core.change_sitesettings")
    form = StoreSettingsForm(request.POST or None, instance=store)
    if request.method == "POST":
        if not can_save:
            messages.error(request, "You do not have permission to change store settings.")
            return redirect("dashboard:store_settings")
        if form.is_valid():
            form.save()
            if form.changed_data:
                audit("settings.change", f"Store settings changed: {', '.join(form.changed_data)}",
                      actor=request.user, request=request)
            messages.success(request, "Settings saved. The shop is updated.")
            return redirect("dashboard:store_settings")
    return render(request, "dashboard/store_settings.html", {
        "form": form, "can_save": can_save, "themes": THEMES, "payments_configured": settings.PAYMENTS_ENABLED})
