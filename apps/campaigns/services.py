"""
Sending offer emails.

- Only customers who ticked "send me offers" (verified, active, not staff) are included.
- One personal email each, with a one-click unsubscribe link and List-Unsubscribe headers
  (required by Gmail / Yahoo for bulk senders).
- Sent in small batches with a daily cap so the email provider does not block the shop; the
  hourly `run_scheduled` task and the "Send next batch now" button both call `send_batch`.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import Campaign, CampaignRecipient

logger = logging.getLogger(__name__)
UNSUB_SALT = "lld-unsubscribe"
MAX_ATTEMPTS = 3


class CampaignError(Exception):
    pass


def subscribers():
    from apps.accounts.models import User

    return User.objects.filter(marketing_opt_in=True, is_verified=True, is_active=True, is_staff=False)


def unsubscribe_token(user):
    return signing.dumps(user.pk, salt=UNSUB_SALT)


def user_from_token(token):
    from apps.accounts.models import User

    try:
        pk = signing.loads(token, salt=UNSUB_SALT)
    except signing.BadSignature:
        return None
    return User.objects.filter(pk=pk).first()


def _render(campaign, *, name, unsubscribe_url):
    from apps.core.models import SiteSettings
    from apps.products.models import Product

    products = list(Product.objects.storefront().filter(pk__in=campaign.products.values("pk"))[:6])
    ctx = {"campaign": campaign, "name": name, "products": products, "unsubscribe_url": unsubscribe_url,
           "store": SiteSettings.load(), "SITE_URL": settings.SITE_URL}
    return render_to_string("emails/campaign.txt", ctx), render_to_string("emails/campaign.html", ctx)


def _send(campaign, to_email, name, unsubscribe_url):
    text, html = _render(campaign, name=name, unsubscribe_url=unsubscribe_url)
    msg = EmailMultiAlternatives(
        subject=campaign.subject, body=text, from_email=settings.DEFAULT_FROM_EMAIL, to=[to_email],
        headers={"List-Unsubscribe": f"<{unsubscribe_url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
    msg.attach_alternative(html, "text/html")
    msg.send(fail_silently=False)


def send_test(campaign, user):
    """A preview to the staff member's own inbox (the unsubscribe link is shown but does nothing harmful)."""
    url = settings.SITE_URL + reverse("campaigns:unsubscribe", args=[unsubscribe_token(user)])
    _send(campaign, user.email, f"{user.get_short_name()} (test)", url)


def start(campaign, *, by):
    """Freeze the list of subscribers and switch the campaign to Sending."""
    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().get(pk=campaign.pk)
        if campaign.status != Campaign.DRAFT:
            raise CampaignError("This campaign has already been sent.")
        people = list(subscribers().values_list("pk", "email"))
        if not people:
            raise CampaignError("No customers have agreed to receive offer emails yet.")
        CampaignRecipient.objects.bulk_create(
            [CampaignRecipient(campaign=campaign, user_id=pk, email=email) for pk, email in people],
            ignore_conflicts=True)
        campaign.status = Campaign.SENDING
        campaign.started_at = timezone.now()
        campaign.save(update_fields=["status", "started_at"])
    return len(people)


def sent_last_24h():
    return CampaignRecipient.objects.filter(status=CampaignRecipient.SENT,
                                            sent_at__gte=timezone.now() - timedelta(hours=24)).count()


def send_batch(limit=None):
    """Send the next few emails of campaigns that are Sending. Returns how many were sent now."""
    limit = limit or settings.CAMPAIGN_BATCH_SIZE
    room = max(0, settings.CAMPAIGN_DAILY_LIMIT - sent_last_24h())
    budget = min(limit, room)
    sent = 0
    for campaign in Campaign.objects.filter(status=Campaign.SENDING).order_by("started_at"):
        todo = (campaign.recipients.filter(Q(status=CampaignRecipient.PENDING)
                                           | Q(status=CampaignRecipient.FAILED, attempts__lt=MAX_ATTEMPTS))
                .select_related("user").order_by("pk"))
        for r in todo[:max(budget - sent, 0)]:
            # Claim the row (attempts+1) so two runs at the same time never email the same person twice.
            if not CampaignRecipient.objects.filter(pk=r.pk, attempts=r.attempts).update(attempts=F("attempts") + 1):
                continue
            user = r.user
            if user is None or not user.marketing_opt_in or not user.is_active:
                CampaignRecipient.objects.filter(pk=r.pk).update(status=CampaignRecipient.SKIPPED,
                                                                 error="Unsubscribed or account closed")
                continue
            url = settings.SITE_URL + reverse("campaigns:unsubscribe", args=[unsubscribe_token(user)])
            try:
                _send(campaign, r.email, user.get_short_name(), url)
            except Exception as exc:  # noqa: BLE001 — one bad address must not stop the batch
                CampaignRecipient.objects.filter(pk=r.pk).update(status=CampaignRecipient.FAILED,
                                                                 error=f"{type(exc).__name__}: {str(exc)[:150]}")
                logger.warning("Campaign %s email to %s failed: %s", campaign.pk, r.email, type(exc).__name__)
            else:
                CampaignRecipient.objects.filter(pk=r.pk).update(status=CampaignRecipient.SENT, error="",
                                                                 sent_at=timezone.now())
                sent += 1
        _finish_if_done(campaign)
        if sent >= budget:
            break
    return sent


def _finish_if_done(campaign):
    left = campaign.recipients.filter(Q(status=CampaignRecipient.PENDING)
                                      | Q(status=CampaignRecipient.FAILED, attempts__lt=MAX_ATTEMPTS)).exists()
    if not left:
        Campaign.objects.filter(pk=campaign.pk, status=Campaign.SENDING).update(
            status=Campaign.SENT, finished_at=timezone.now())


def unsubscribe(user):
    from apps.core.utils import audit

    if user.marketing_opt_in:
        user.set_marketing_consent(False)
        audit("marketing.unsubscribe", f"{user.email} unsubscribed from offer emails", actor=user)
