from django.contrib import admin

from .models import Order, OrderItem, OrderRequest, OrderStatusHistory


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ("product_name", "variant_name", "sku", "hsn_code", "quantity", "unit_price", "discount",
              "taxable_value", "gst_rate", "tax_amount", "line_total")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class HistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = ("created_at", "field", "from_value", "to_value", "note", "changed_by", "visible_to_customer")
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Read-mostly view. Status changes, shipping, COD and refunds are done in the staff dashboard
    so that stock, invoices and emails stay in sync."""

    list_display = ("order_number", "created_at", "customer_name", "grand_total", "payment_method", "status",
                    "payment_status", "channel")
    list_filter = ("status", "payment_status", "payment_method", "channel")
    search_fields = ("order_number", "email", "customer_name", "phone", "tracking_number")
    date_hierarchy = "created_at"
    inlines = [OrderItemInline, HistoryInline]
    fields = ("order_number", "user", "channel", "status", "payment_method", "payment_status", "customer_name",
              "email", "phone", "billing_address", "shipping_address", "customer_gstin", "place_of_supply",
              "subtotal", "discount_total", "coupon_code", "shipping_total", "taxable_total", "tax_total",
              "grand_total", "refunded_total", "courier_name", "tracking_number", "shipped_at", "delivered_at",
              "admin_notes")
    readonly_fields = tuple(f for f in fields if f != "admin_notes")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrderRequest)
class OrderRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "order", "kind", "status", "restock_approved", "handled_by")
    list_filter = ("kind", "status")
    readonly_fields = ("order", "kind", "status", "reason", "restock_approved", "handled_by", "created_at")

    def has_add_permission(self, request):
        return False
