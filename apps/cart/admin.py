from django.contrib import admin

from .models import Coupon, CouponUsage


class CouponUsageInline(admin.TabularInline):
    model = CouponUsage
    extra = 0
    readonly_fields = ("user", "order", "discount_amount", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_type", "value", "min_order_amount", "times_used", "usage_limit",
                    "starts_at", "ends_at", "is_active")
    list_filter = ("is_active", "discount_type")
    search_fields = ("code",)
    inlines = [CouponUsageInline]
