"""Shipments booked through Shiprocket, and the tracking updates received for them."""
from django.conf import settings
from django.db import models


class Shipment(models.Model):
    BOOKED = "booked"            # AWB assigned / pickup scheduled
    IN_TRANSIT = "in_transit"    # picked up, moving, out for delivery
    DELIVERED = "delivered"
    RTO = "rto"                  # returning to us (customer refused / not reachable)
    CANCELLED = "cancelled"
    STAGE_CHOICES = [(BOOKED, "Booked — waiting for pickup"), (IN_TRANSIT, "In transit"),
                     (DELIVERED, "Delivered"), (RTO, "Returning to us (RTO)"), (CANCELLED, "Cancelled")]
    OPEN_STAGES = (BOOKED, IN_TRANSIT)

    order = models.OneToOneField("orders.Order", on_delete=models.PROTECT, related_name="shipment")
    provider = models.CharField(max_length=20, default="shiprocket")
    provider_order_id = models.CharField(max_length=40, blank=True)
    provider_shipment_id = models.CharField(max_length=40, blank=True)
    awb = models.CharField("AWB", max_length=60, blank=True, db_index=True)
    courier_name = models.CharField(max_length=100, blank=True)
    courier_id = models.CharField(max_length=20, blank=True)
    label_url = models.URLField(max_length=500, blank=True)
    tracking_url = models.URLField(max_length=500, blank=True)
    stage = models.CharField(max_length=12, choices=STAGE_CHOICES, default=BOOKED, db_index=True)
    status_text = models.CharField("Latest courier status", max_length=100, blank=True)
    last_update_at = models.DateTimeField(null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order.order_number} — {self.courier_name} {self.awb}"


class ShipmentEvent(models.Model):
    """One courier scan / status update. `event_key` makes repeated webhooks harmless."""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=100)
    activity = models.CharField(max_length=300, blank=True)
    location = models.CharField(max_length=150, blank=True)
    event_time = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=10, choices=[("webhook", "Webhook"), ("poll", "Hourly check"),
                                                     ("booking", "Booking")])
    event_key = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-event_time", "-id"]

    def __str__(self):
        return f"{self.shipment.awb}: {self.status}"
