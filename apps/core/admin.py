from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse

from .models import AuditLog, Banner, ContactMessage, EmailLog, Page, SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Store", {"fields": ("store_name", "tagline", "logo", "support_email", "support_phone",
                              "whatsapp_number", "footer_text")}),
        ("Legal & GST details (from your GST certificate)", {
            "description": "Tax invoices are blocked until GSTIN, legal name, address and state are filled. "
                           "Confirm all values with your CA.",
            "fields": ("gst_registered", "legal_name", "trade_name", "gstin", "pan", "address_line1",
                       "address_line2", "city", "state_code", "pincode")}),
        ("Pricing & tax rules (confirm with your CA)", {
            "fields": ("prices_include_gst", "shipping_tax_mode", "shipping_tax_rate",
                       "round_off_invoice_total")}),
        ("Invoices", {"fields": ("invoice_trigger", "invoice_prefix", "credit_note_prefix",
                                 "invoice_terms", "email_invoice_to_customer")}),
        ("Shipping", {"fields": ("shipping_flat_charge", "free_shipping_threshold", "serviceable_pincodes",
                                 "estimated_delivery_text", "delivery_days_local_min",
                                 "delivery_days_local_max", "delivery_days_india_min", "delivery_days_india_max")}),
        ("Offer bar & prepaid benefit", {"fields": ("announcement_text", "prepaid_discount_percent",
                                                    "prepaid_free_shipping")}),
        ("Cash on Delivery", {"fields": ("cod_enabled", "cod_min_order", "cod_max_order", "cod_fee")}),
        ("Online payments (Razorpay)", {"fields": ("online_payments_enabled", "payment_timeout_minutes",
                                                   "razorpay_show_upi", "razorpay_show_cards",
                                                   "razorpay_show_netbanking", "razorpay_show_wallets")}),
        ("Direct UPI & bank transfer (staff confirm receipt)", {
            "fields": ("upi_enabled", "upi_id", "upi_payee_name", "bank_transfer_enabled", "bank_account_name",
                       "bank_account_number", "bank_ifsc", "bank_name", "bank_branch", "manual_payment_hours")}),
        ("Shiprocket", {"fields": ("shiprocket_pickup_location", "shiprocket_pickup_pincode", "parcel_length_cm", "parcel_breadth_cm",
                                   "parcel_height_cm", "parcel_default_weight_g")}),
        ("Look of the shop", {"fields": ("trust_items", "shloka_text", "shloka_meaning", "card_text_limit", "hero_interval_seconds", "theme",
                                         "theme_header_color", "theme_strip_color", "theme_accent_color")}),
        ("Orders & returns", {"fields": ("cancel_allowed_until", "return_window_days",
                                         "low_stock_email_alerts")}),
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = SiteSettings.load()
        return redirect(reverse("admin:core_sitesettings_change", args=[obj.pk]))


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "is_published", "show_in_footer", "sort_order", "updated_at")
    list_editable = ("is_published", "show_in_footer", "sort_order")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ("heading", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("subject", "name", "email", "created_at", "is_resolved")
    list_filter = ("is_resolved",)
    search_fields = ("name", "email", "subject")
    readonly_fields = ("name", "email", "phone", "subject", "message", "created_at")


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "action", "actor", "summary", "ip_address")
    list_filter = ("action",)
    search_fields = ("summary", "object_id", "actor__email")


@admin.register(EmailLog)
class EmailLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "template", "to_email", "status", "attempts", "last_error")
    list_filter = ("status", "template")
    search_fields = ("to_email", "related_object")
    exclude = ("body_html",)
