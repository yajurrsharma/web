from django.contrib import admin

from .models import Payment, Refund, WebhookEvent


class NoEditAdmin(admin.ModelAdmin):
    """Payments are written by the payment flow only; staff use the dashboard for actions."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(NoEditAdmin):
    list_display = ("created_at", "order", "gateway", "amount", "status", "method_detail", "gateway_payment_id")
    list_filter = ("gateway", "status")
    search_fields = ("order__order_number", "gateway_order_id", "gateway_payment_id")


@admin.register(Refund)
class RefundAdmin(NoEditAdmin):
    list_display = ("created_at", "order", "amount", "method", "status", "gateway_refund_id", "reference")
    list_filter = ("method", "status")
    search_fields = ("order__order_number", "gateway_refund_id", "reference")


@admin.register(WebhookEvent)
class WebhookEventAdmin(NoEditAdmin):
    list_display = ("received_at", "event_type", "event_id", "status", "note")
    list_filter = ("status", "event_type")
