from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import EmailLog
from apps.core.testing import make_address, make_user

from .models import Address, EmailOTP
from .otp import OTPCooldown, issue_otp, verify_otp

User = get_user_model()


def code_from_last_email():
    import re

    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class CustomUserModelTests(TestCase):
    def test_auth_user_model_is_custom(self):
        self.assertEqual(User._meta.label, "accounts.User")
        self.assertEqual(User.USERNAME_FIELD, "email")

    def test_create_user_normalises_email_and_hashes_password(self):
        user = User.objects.create_user(email="Customer@Example.COM", full_name="Asha", password="S3cure-pass!")
        self.assertEqual(user.email, "customer@example.com")
        self.assertNotEqual(user.password, "S3cure-pass!")
        self.assertTrue(user.check_password("S3cure-pass!"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_verified)

    def test_email_is_unique(self):
        User.objects.create_user(email="a@example.com", full_name="A", password="x-Pass-1234")
        with self.assertRaises(IntegrityError):
            User.objects.create_user(email="a@example.com", full_name="B", password="x-Pass-1234")

    def test_create_superuser(self):
        admin = User.objects.create_superuser(email="admin@example.com", full_name="Admin", password="x-Pass-1234")
        self.assertTrue(admin.is_staff and admin.is_superuser and admin.is_verified)


class OTPServiceTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_code_is_stored_hashed_not_plain(self):
        code = issue_otp("a@example.com", EmailOTP.PURPOSE_VERIFY)
        otp = EmailOTP.objects.get()
        self.assertEqual(len(code), 6)
        self.assertNotIn(code, otp.code_hash)
        self.assertEqual(len(otp.code_hash), 64)

    def test_correct_code_verifies_once(self):
        code = issue_otp("a@example.com", EmailOTP.PURPOSE_VERIFY)
        self.assertTrue(verify_otp("a@example.com", EmailOTP.PURPOSE_VERIFY, code))
        self.assertFalse(verify_otp("a@example.com", EmailOTP.PURPOSE_VERIFY, code))  # no reuse

    def test_code_for_other_purpose_rejected(self):
        code = issue_otp("a@example.com", EmailOTP.PURPOSE_VERIFY)
        self.assertFalse(verify_otp("a@example.com", EmailOTP.PURPOSE_LOGIN, code))

    def test_expired_code_rejected(self):
        code = issue_otp("a@example.com", EmailOTP.PURPOSE_VERIFY)
        EmailOTP.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(verify_otp("a@example.com", EmailOTP.PURPOSE_VERIFY, code))

    @override_settings(OTP_MAX_ATTEMPTS=3)
    def test_attempt_limit_locks_code(self):
        code = issue_otp("a@example.com", EmailOTP.PURPOSE_VERIFY)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(3):
            self.assertFalse(verify_otp("a@example.com", EmailOTP.PURPOSE_VERIFY, wrong))
        self.assertTrue(EmailOTP.objects.get().invalidated)
        self.assertFalse(verify_otp("a@example.com", EmailOTP.PURPOSE_VERIFY, code))  # even the right code

    def test_resend_cooldown(self):
        issue_otp("a@example.com", EmailOTP.PURPOSE_VERIFY)
        with self.assertRaises(OTPCooldown):
            issue_otp("a@example.com", EmailOTP.PURPOSE_VERIFY)

    def test_new_code_invalidates_previous(self):
        first = issue_otp("a@example.com", EmailOTP.PURPOSE_VERIFY)
        EmailOTP.objects.update(created_at=timezone.now() - timedelta(minutes=5))
        second = issue_otp("a@example.com", EmailOTP.PURPOSE_VERIFY)
        if first != second:
            self.assertFalse(verify_otp("a@example.com", EmailOTP.PURPOSE_VERIFY, first))
        self.assertTrue(verify_otp("a@example.com", EmailOTP.PURPOSE_VERIFY, second))

    def test_codes_use_secrets_module(self):
        with mock.patch("apps.accounts.otp.secrets.randbelow", return_value=42) as rnd:
            self.assertEqual(issue_otp("b@example.com", EmailOTP.PURPOSE_LOGIN), "000042")
            rnd.assert_called_once_with(1_000_000)


class RegistrationFlowTests(TestCase):
    def setUp(self):
        cache.clear()

    def register(self, email="new@example.com"):
        return self.client.post(reverse("accounts:register"), {
            "full_name": "Ravi Kumar", "email": email, "mobile": "9876543210",
            "password1": "Gh33-dhoop!", "password2": "Gh33-dhoop!", "accept_terms": "on"})

    def test_register_sends_otp_and_verifies(self):
        resp = self.register()
        self.assertRedirects(resp, reverse("accounts:verify_email"))
        user = User.objects.get(email="new@example.com")
        self.assertFalse(user.is_verified)
        code = code_from_last_email()
        log = EmailLog.objects.filter(template="otp_verify").get()
        self.assertTrue(log.is_sensitive)
        self.assertEqual(log.body_text, "")  # OTP never stored in the email log
        resp = self.client.post(reverse("accounts:verify_email"), {"code": code})
        self.assertRedirects(resp, reverse("accounts:dashboard"))
        user.refresh_from_db()
        self.assertTrue(user.is_verified)
        self.assertIn("_auth_user_id", self.client.session)

    def test_wrong_code_shows_error(self):
        self.register()
        resp = self.client.post(reverse("accounts:verify_email"), {"code": "999999"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_registering_existing_email_does_not_reveal_account(self):
        make_user(email="taken@example.com")
        resp = self.register("taken@example.com")
        self.assertRedirects(resp, reverse("accounts:verify_email"))  # same response as a new email
        self.assertEqual(mail.outbox[-1].subject, "You already have a Life Line Dynamics account")
        self.assertEqual(User.objects.filter(email="taken@example.com").count(), 1)

    def test_weak_password_rejected(self):
        resp = self.client.post(reverse("accounts:register"), {
            "full_name": "Ravi", "email": "w@example.com", "password1": "12345678", "password2": "12345678",
            "accept_terms": "on"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="w@example.com").exists())


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user(email="login@example.com", password="Corr3ct-pass!")

    def test_login_with_email(self):
        resp = self.client.post(reverse("accounts:login"),
                                {"username": "LOGIN@example.com", "password": "Corr3ct-pass!"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_wrong_password_rejected(self):
        resp = self.client.post(reverse("accounts:login"), {"username": "login@example.com", "password": "wrong"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unverified_user_sent_to_verification(self):
        make_user(email="unv@example.com", password="Corr3ct-pass!", verified=False)
        resp = self.client.post(reverse("accounts:login"), {"username": "unv@example.com", "password": "Corr3ct-pass!"})
        self.assertRedirects(resp, reverse("accounts:verify_email"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_rate_limited(self):
        for _ in range(10):
            self.client.post(reverse("accounts:login"), {"username": "login@example.com", "password": "bad"})
        resp = self.client.post(reverse("accounts:login"), {"username": "login@example.com", "password": "Corr3ct-pass!"})
        self.assertEqual(resp.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_requires_post(self):
        self.client.login(username="login@example.com", password="Corr3ct-pass!")
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_session_key_rotates_on_login(self):
        self.client.get("/")
        self.client.session.save()
        before = self.client.session.session_key
        self.client.post(reverse("accounts:login"), {"username": "login@example.com", "password": "Corr3ct-pass!"})
        self.assertNotEqual(before, self.client.session.session_key)


class OTPLoginTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_passwordless_login(self):
        make_user(email="otp@example.com")
        self.client.post(reverse("accounts:otp_login"), {"email": "otp@example.com"})
        code = code_from_last_email()
        resp = self.client.post(reverse("accounts:otp_login_verify"), {"code": code})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_unknown_email_gets_same_response_and_no_email(self):
        resp = self.client.post(reverse("accounts:otp_login"), {"email": "nobody@example.com"})
        self.assertRedirects(resp, reverse("accounts:otp_login_verify"))
        self.assertEqual(len(mail.outbox), 0)


class PasswordResetTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_reset_flow(self):
        user = make_user(email="reset@example.com", password="Old-pass-123!")
        resp = self.client.post(reverse("accounts:password_reset"), {"email": "reset@example.com"})
        self.assertRedirects(resp, reverse("accounts:password_reset_done"))
        body = mail.outbox[-1].body
        self.assertNotIn("Old-pass-123!", body)
        link = [w for w in body.split() if "/accounts/reset/" in w][0]
        path = link[link.index("/accounts/reset/"):]
        resp = self.client.get(path, follow=True)
        set_url = resp.redirect_chain[-1][0]
        resp = self.client.post(set_url, {"new_password1": "Brand-new-pass-9", "new_password2": "Brand-new-pass-9"})
        self.assertRedirects(resp, reverse("accounts:password_reset_complete"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("Brand-new-pass-9"))
        # Single use: the link no longer works.
        resp = self.client.get(path, follow=True)
        self.assertContains(resp, "This link has expired")

    def test_unknown_email_same_response(self):
        resp = self.client.post(reverse("accounts:password_reset"), {"email": "ghost@example.com"})
        self.assertRedirects(resp, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)


class AddressAuthorizationTests(TestCase):
    def test_cannot_edit_or_delete_someone_elses_address(self):
        owner = make_user(email="owner@example.com")
        other = make_user(email="other@example.com")
        address = make_address(owner)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("accounts:address_edit", args=[address.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("accounts:address_delete", args=[address.pk])).status_code, 404)
        self.assertTrue(Address.objects.filter(pk=address.pk).exists())

    def test_account_pages_require_login(self):
        for name in ("accounts:dashboard", "accounts:addresses", "accounts:profile", "orders:list"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, name)
            self.assertIn(reverse("accounts:login"), resp["Location"])


class MobileLoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="asha@example.com", full_name="Asha", password="Corr3ct-pass!",
                                             mobile="9876543210", is_verified=True)

    def _login(self, username, password="Corr3ct-pass!"):
        return self.client.post(reverse("accounts:login"), {"username": username, "password": password})

    def test_login_with_email_or_mobile_in_any_format(self):
        for value in ["asha@example.com", "ASHA@example.com", "9876543210", "+91 98765 43210", "098765-43210"]:
            self.client.logout()
            resp = self._login(value)
            self.assertEqual(resp.status_code, 302, value)
            self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk, value)

    def test_wrong_password_or_unknown_mobile_gives_same_message(self):
        a = self._login("9876543210", "wrong-pass")
        b = self._login("9123456789")  # nobody has this number
        for resp in (a, b):
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Email / mobile number or password is incorrect.")
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_mobile_must_be_unique(self):
        from django.db import IntegrityError

        resp = self.client.post(reverse("accounts:register"), {
            "full_name": "Other", "email": "other@example.com", "mobile": "+91 98765 43210",
            "password1": "An0ther-pass!", "password2": "An0ther-pass!", "accept_terms": "on"})
        self.assertContains(resp, "already linked to another account")
        self.assertFalse(User.objects.filter(email="other@example.com").exists())
        with self.assertRaises(IntegrityError):
            User.objects.create_user(email="x@example.com", full_name="X", mobile="9876543210")

    def test_profile_saves_normalised_mobile_and_blocks_duplicates(self):
        other = User.objects.create_user(email="b@example.com", full_name="B", password="Corr3ct-pass!",
                                         is_verified=True)
        self.client.force_login(other)
        resp = self.client.post(reverse("accounts:profile"), {"full_name": "B", "mobile": "9876543210"})
        self.assertContains(resp, "already linked to another account")
        self.client.post(reverse("accounts:profile"), {"full_name": "B", "mobile": "+91 91234 56789"})
        other.refresh_from_db()
        self.assertEqual(other.mobile, "9123456789")
        self.client.post(reverse("accounts:profile"), {"full_name": "B", "mobile": ""})  # can be removed
        other.refresh_from_db()
        self.assertEqual(other.mobile, "")


class AddressLabelTests(TestCase):
    def test_label_is_a_dropdown_and_rejects_free_text(self):
        user = User.objects.create_user(email="ad@example.com", full_name="Ad", password="Corr3ct-pass!",
                                        is_verified=True)
        self.client.force_login(user)
        page = self.client.get(reverse("accounts:address_create"))
        self.assertContains(page, '<select name="label"')
        self.assertContains(page, "🏢 Office")
        data = {"full_name": "Ad", "phone": "9876543210", "line1": "1 Road", "city": "Mohali",
                "state_code": "03", "pincode": "160055"}
        self.client.post(reverse("accounts:address_create"), {**data, "label": "Office"})
        self.assertEqual(user.addresses.get().label, "Office")
        resp = self.client.post(reverse("accounts:address_create"), {**data, "label": "<b>hack</b>"})
        self.assertEqual(resp.status_code, 200)  # not one of the choices → form error, nothing saved
        self.assertEqual(user.addresses.count(), 1)
