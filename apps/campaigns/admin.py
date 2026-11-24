from django.contrib import admin

from .models import Campaign, CampaignRecipient


class RecipientInline(admin.TabularInline):
    model = CampaignRecipient
    extra = 0
    can_delete = False
    fields = ("email", "status", "attempts", "sent_at", "error")
    readonly_fields = fields


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("subject", "status", "created_at", "started_at", "finished_at")
    list_filter = ("status",)
    readonly_fields = ("status", "created_by", "started_at", "finished_at")
    inlines = [RecipientInline]
