"""Offer / marketing emails, sent only to customers who agreed to receive them."""
from django.conf import settings
from django.db import models


class Campaign(models.Model):
    DRAFT = "draft"
    SENDING = "sending"
    SENT = "sent"
    STATUS_CHOICES = [(DRAFT, "Draft"), (SENDING, "Sending"), (SENT, "Sent")]

    subject = models.CharField("Email subject", max_length=150,
                               help_text="What customers see in their inbox, e.g. 'Diwali offer: 20% off diya batti'.")
    heading = models.CharField("Big heading inside the email", max_length=120)
    message = models.TextField("Message", help_text="Plain text. Leave a blank line between paragraphs. "
                                                    "The email starts with 'Namaste <customer name>' automatically.")
    products = models.ManyToManyField("products.Product", blank=True, related_name="+",
                                      help_text="Optional: up to 6 products shown with photo, price and a link.")
    button_text = models.CharField(max_length=40, blank=True, default="Shop now")
    button_link = models.CharField(max_length=200, blank=True, default="/shop/",
                                   help_text="A page of this website, e.g. /shop/ or /category/diya-batti/.")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.subject

    def counts(self):
        rows = dict(self.recipients.values_list("status").annotate(n=models.Count("id")))
        return {s: rows.get(s, 0) for s, _ in CampaignRecipient.STATUS_CHOICES} | {"total": sum(rows.values())}


class CampaignRecipient(models.Model):
    """One personal email. The list is frozen when sending starts; unsubscribing later skips it."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"
    STATUS_CHOICES = [(PENDING, "Waiting"), (SENT, "Sent"), (FAILED, "Failed"), (SKIPPED, "Skipped")]

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="recipients")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    email = models.EmailField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=200, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["campaign", "email"], name="one_email_per_campaign")]
        indexes = [models.Index(fields=["campaign", "status"])]

    def __str__(self):
        return f"{self.campaign_id} → {self.email} ({self.status})"
