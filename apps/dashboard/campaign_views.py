"""Staff pages for offer / marketing emails."""
from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.campaigns import services
from apps.campaigns.models import Campaign
from apps.core.utils import audit

from .forms import CampaignForm
from .views import staff_required


@staff_required("campaigns.view_campaign")
@require_http_methods(["GET"])
def campaign_list(request):
    return render(request, "dashboard/campaigns.html", {
        "campaigns": Campaign.objects.all()[:100],
        "subscribers": services.subscribers().count(),
        "sent_24h": services.sent_last_24h(), "daily_limit": settings.CAMPAIGN_DAILY_LIMIT,
    })


@staff_required("campaigns.add_campaign")
@require_http_methods(["GET", "POST"])
def campaign_edit(request, pk=None):
    campaign = get_object_or_404(Campaign, pk=pk) if pk else None
    if campaign and campaign.status != Campaign.DRAFT:
        messages.info(request, "This email has already been sent and can no longer be changed.")
        return redirect("dashboard:campaign_detail", pk=campaign.pk)
    form = CampaignForm(request.POST or None, instance=campaign)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if obj.created_by_id is None:
            obj.created_by = request.user
        obj.save()
        form.save_m2m()
        messages.success(request, "Saved. Check the preview, send a test to yourself, then send it to customers.")
        return redirect("dashboard:campaign_detail", pk=obj.pk)
    return render(request, "dashboard/campaign_form.html", {"form": form, "campaign": campaign})


@staff_required("campaigns.view_campaign")
@require_http_methods(["GET"])
def campaign_detail(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    _, preview = services._render(campaign, name=request.user.get_short_name(), unsubscribe_url="#")
    return render(request, "dashboard/campaign_detail.html", {
        "campaign": campaign, "counts": campaign.counts(), "preview": preview,
        "subscribers": services.subscribers().count(),
        "sent_24h": services.sent_last_24h(), "daily_limit": settings.CAMPAIGN_DAILY_LIMIT,
        "batch_size": settings.CAMPAIGN_BATCH_SIZE,
        "failed": campaign.recipients.filter(status="failed").order_by("email")[:50],
    })


@staff_required("campaigns.change_campaign")
@require_POST
def campaign_action(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    action = request.POST.get("action")
    back = redirect(reverse("dashboard:campaign_detail", args=[pk]))
    try:
        if action == "test":
            services.send_test(campaign, request.user)
            messages.success(request, f"Test email sent to {request.user.email}. Check your inbox (and spam folder).")
        elif action == "start":
            n = services.start(campaign, by=request.user)
            audit("campaign.start", f"Offer email '{campaign.subject}' started to {n} subscribers",
                  actor=request.user, obj=campaign, request=request)
            sent = services.send_batch()
            messages.success(request, f"Sending to {n} customers started — {sent} sent now; the rest go out "
                                      "automatically in batches.")
        elif action == "batch":
            sent = services.send_batch()
            messages.success(request, f"{sent} more email(s) sent." if sent else
                             "Nothing sent: either all are done or today's sending limit is reached.")
        else:
            messages.error(request, "Unknown action.")
    except services.CampaignError as exc:
        messages.error(request, str(exc))
    except Exception as exc:  # noqa: BLE001 — e.g. SMTP not configured; show it, don't crash
        messages.error(request, f"The email server refused: {type(exc).__name__}. Check the email settings.")
    return back
