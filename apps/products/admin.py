from django.contrib import admin, messages
from django.utils.html import format_html

from apps.core.utils import audit
from apps.inventory.models import StockMovement
from apps.inventory.services import adjust_stock

from .models import Category, Product, ProductMedia, ProductVariant, TaxClass


@admin.register(TaxClass)
class TaxClassAdmin(admin.ModelAdmin):
    list_display = ("name", "hsn_code", "gst_rate", "confirmed_by_ca", "product_count")
    list_filter = ("confirmed_by_ca",)

    @admin.display(description="Products")
    def product_count(self, obj):
        return obj.products.count()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and form.changed_data:
            audit("taxclass.change", f"Tax class '{obj}' changed: {', '.join(form.changed_data)}",
                  actor=request.user, obj=obj, request=request)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("__str__", "slug", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active", "parent")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


class VariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    min_num = 1
    fields = ("name", "sku", "mrp", "price", "cost_price", "stock", "low_stock_threshold",
              "weight_grams", "is_active", "sort_order")

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if not request.user.has_perm("products.view_cost_price"):
            fields.remove("cost_price")
        return fields

    def get_readonly_fields(self, request, obj=None):
        # Existing variants' stock changes only through the stock ledger.
        return ("stock",) if obj else ()


class MediaInline(admin.TabularInline):
    model = ProductMedia
    extra = 1
    fields = ("preview", "kind", "file", "alt_text", "sort_order")
    readonly_fields = ("preview",)

    @admin.display(description="Preview")
    def preview(self, obj):
        if obj.pk and obj.kind == ProductMedia.KIND_IMAGE and obj.file:
            return format_html('<img src="{}" style="height:60px;border-radius:4px">', obj.file.url)
        return "—"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("thumb", "name", "category", "tax_class", "status", "is_featured", "price_range",
                    "total_stock", "updated_at")
    list_display_links = ("thumb", "name")
    list_filter = ("status", "is_featured", "category", "tax_class")
    list_editable = ("status", "is_featured")
    search_fields = ("name", "variants__sku")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [VariantInline, MediaInline]
    actions = ["publish", "unpublish", "archive"]
    save_on_top = True
    fieldsets = (
        (None, {"fields": ("name", "slug", "category", "status", "is_featured")}),
        ("Tax & invoicing", {"fields": ("tax_class", "unit")}),
        ("Content", {"fields": ("short_description", "description", "main_image", "main_image_alt",
                                "video_url")}),
        ("SEO", {"classes": ("collapse",), "fields": ("seo_title", "meta_description")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("category", "tax_class").prefetch_related("variants")

    @admin.display(description="")
    def thumb(self, obj):
        if obj.main_image:
            return format_html('<img src="{}" style="height:40px;width:40px;object-fit:cover;border-radius:4px">',
                               obj.main_image.url)
        return "—"

    @admin.display(description="Price")
    def price_range(self, obj):
        prices = sorted(v.price for v in obj.variants.all())
        if not prices:
            return "—"
        return f"₹{prices[0]}" if prices[0] == prices[-1] else f"₹{prices[0]} – ₹{prices[-1]}"

    @admin.display(description="Stock")
    def total_stock(self, obj):
        return sum(v.stock for v in obj.variants.all())

    def save_formset(self, request, form, formset, change):
        if formset.model is not ProductVariant:
            return super().save_formset(request, form, formset, change)
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            if StockMovement.objects.filter(variant=obj).exists():
                messages.warning(request, f"'{obj.sku}' has stock history, so it was deactivated instead of deleted.")
                obj.is_active = False
                obj.save(update_fields=["is_active"])
            else:
                obj.delete()
        for variant in instances:
            is_new = variant.pk is None
            opening = variant.stock if is_new else None
            if is_new:
                variant.stock = 0
            variant.save()
            if is_new and opening:
                adjust_stock(variant.pk, opening, StockMovement.OPENING, user=request.user,
                             note="Opening stock (product created)")
        formset.save_m2m()

    @admin.action(description="Publish selected products")
    def publish(self, request, queryset):
        n = queryset.update(status=Product.STATUS_PUBLISHED)
        self.message_user(request, f"{n} product(s) published.")

    @admin.action(description="Unpublish (back to draft)")
    def unpublish(self, request, queryset):
        n = queryset.update(status=Product.STATUS_DRAFT)
        self.message_user(request, f"{n} product(s) unpublished.")

    @admin.action(description="Archive selected products")
    def archive(self, request, queryset):
        n = queryset.update(status=Product.STATUS_ARCHIVED, is_featured=False)
        self.message_user(request, f"{n} product(s) archived.")

    def delete_model(self, request, obj):
        audit("product.delete", f"Deleted product '{obj.name}'", actor=request.user, request=request)
        super().delete_model(request, obj)
