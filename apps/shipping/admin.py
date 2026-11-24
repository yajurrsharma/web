from django.contrib import admin

from .models import Shipment, ShipmentEvent


class EventInline(admin.TabularInline):
    model = ShipmentEvent
    extra = 0
    can_delete = False
    fields = ("event_time", "status", "activity", "location", "source")
    readonly_fields = fields


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ("order", "courier_name", "awb", "stage", "status_text", "last_update_at")
    list_filter = ("stage", "courier_name")
    search_fields = ("awb", "order__order_number")
    readonly_fields = ("order", "provider_order_id", "provider_shipment_id", "awb", "courier_name", "courier_id",
                       "label_url", "tracking_url", "stage", "status_text", "last_update_at", "weight_kg",
                       "created_by")
    inlines = [EventInline]
