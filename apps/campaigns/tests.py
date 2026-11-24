from io import StringIO

from django.contrib.auth.models import Group
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.testing import make_product, make_user

from . import services
from .models import Campaign, CampaignRecipient


@override_settings(CAMPAIGN_BATCH_SIZE=2, CAMPAIGN_DAILY_LIMIT=3)
class CampaignTests(TestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.manager = make_user(email="mgr@example.com", is_staff=True)
        self.manager.groups.add(Group.objects.get(name="Store Manager"))
        self.fans = []
        for i in range(4):
            u = make_user(email=f"fan{i}@example.com", full_name=f"Fan {i}")
            u.set_marketing_consent(True)
            self.fans.append(u)
        make_user(email="quiet@example.com")  # did not agree
        make_user(email="unverified@example.com", verified=False).set_marketing_consent(True)
        self.product = make_product(name="Diya Combo", price="250.00", mrp="300.00").product
        self.campaign = Campaign.objects.create(subject="Diwali offer", heading="Diwali sale", message="Hello!\n\nBig offers.")
        self.campaign.products.add(self.product)

    def test_only_consenting_verified_customers_get_one_personal_email_each(self):
        self.assertEqual(set(services.subscribers().values_list("email", flat=True)),
                         {f"fan{i}@example.com" for i in range(4)})
        self.client.force_login(self.manager)
        self.client.post(reverse("dashboard:campaign_action", args=[self.campaign.pk]), {"action": "start"})
        self.assertEqual(len(mail.outbox), 2)  # batch size
        m = mail.outbox[0]
        self.assertEqual(len(m.to), 1)  # separate email, nobody sees other addresses
        self.assertIn("Namaste Fan", m.body)
        self.assertIn("Diya Combo", m.body)
        self.assertIn("/email/unsubscribe/", m.extra_headers["List-Unsubscribe"])
        self.assertEqual(m.extra_headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        services.send_batch()  # daily limit 3 → only one more
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(services.send_batch(), 0)
        self.assertEqual(Campaign.objects.get().status, Campaign.SENDING)
        with override_settings(CAMPAIGN_DAILY_LIMIT=100):
            services.send_batch()
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(Campaign.objects.get().status, Campaign.SENT)
        self.assertEqual(CampaignRecipient.objects.filter(status="sent").count(), 4)

    def test_unsubscribe_link_one_click_and_skips_pending(self):
        services.start(self.campaign, by=self.manager)
        fan = self.fans[3]
        url = reverse("campaigns:unsubscribe", args=[services.unsubscribe_token(fan)])
        self.assertContains(self.client.get(url), "Stop offer emails?")
        fan.refresh_from_db()
        self.assertTrue(fan.marketing_opt_in)  # a GET (link preview / scanner) changes nothing
        self.client.post(url, {"List-Unsubscribe": "One-Click"})  # mail provider's one-click POST, no CSRF
        fan.refresh_from_db()
        self.assertFalse(fan.marketing_opt_in)
        self.assertIsNotNone(fan.marketing_opt_in_at)
        with override_settings(CAMPAIGN_DAILY_LIMIT=100, CAMPAIGN_BATCH_SIZE=100):
            services.send_batch()
        self.assertNotIn("fan3@example.com", [m.to[0] for m in mail.outbox])
        self.assertEqual(CampaignRecipient.objects.get(email="fan3@example.com").status, "skipped")
        self.assertEqual(self.client.get(reverse("campaigns:unsubscribe", args=["forged"])).status_code, 404)

    def test_cannot_send_twice_and_permissions(self):
        services.start(self.campaign, by=self.manager)
        with self.assertRaises(services.CampaignError):
            services.start(self.campaign, by=self.manager)
        staff = make_user(email="ops@example.com", is_staff=True)
        staff.groups.add(Group.objects.get(name="Order Staff"))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("dashboard:campaigns")).status_code, 403)

    def test_dashboard_pages_and_test_email(self):
        self.client.force_login(self.manager)
        for url in [reverse("dashboard:campaigns"), reverse("dashboard:campaign_create"),
                    reverse("dashboard:campaign_detail", args=[self.campaign.pk]),
                    reverse("dashboard:campaign_edit", args=[self.campaign.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        resp = self.client.post(reverse("dashboard:campaign_create"), {
            "subject": "New", "heading": "H", "message": "M", "button_text": "Go", "button_link": "javascript:x"})
        self.assertContains(resp, "starting with /")
        self.client.post(reverse("dashboard:campaign_action", args=[self.campaign.pk]), {"action": "test"})
        self.assertEqual(mail.outbox[-1].to, ["mgr@example.com"])


class ConsentTests(TestCase):
    def test_register_box_is_optional_and_profile_can_change_it(self):
        from apps.accounts.models import User

        base = {"full_name": "A", "email": "a@example.com", "password1": "Str0ng-pass!x",
                "password2": "Str0ng-pass!x", "accept_terms": "on"}
        self.client.post(reverse("accounts:register"), base)
        a = User.objects.get(email="a@example.com")
        self.assertFalse(a.marketing_opt_in)  # not ticked → no offer emails
        self.client.post(reverse("accounts:register"), {**base, "email": "b@example.com", "marketing_opt_in": "on"})
        b = User.objects.get(email="b@example.com")
        self.assertTrue(b.marketing_opt_in)
        self.assertIsNotNone(b.marketing_opt_in_at)
        a.is_verified = True
        a.save()
        self.client.force_login(a)
        self.client.post(reverse("accounts:profile"), {"full_name": "A", "mobile": "", "marketing_opt_in": "on"})
        a.refresh_from_db()
        self.assertTrue(a.marketing_opt_in)
