"""OTP issue/verify logic. Raw codes exist only in memory and in the email itself."""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import EmailOTP


class OTPError(Exception):
    pass


class OTPCooldown(OTPError):
    def __init__(self, seconds):
        self.seconds = seconds
        super().__init__(f"Please wait {seconds} seconds before requesting a new code.")


def _hash(email, purpose, code):
    msg = f"{email.lower()}|{purpose}|{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def generate_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def cooldown_remaining(email, purpose):
    last = EmailOTP.objects.filter(email__iexact=email, purpose=purpose).order_by("-created_at").first()
    if not last:
        return 0
    elapsed = (timezone.now() - last.created_at).total_seconds()
    return max(0, int(settings.OTP_RESEND_COOLDOWN_SECONDS - elapsed))


def issue_otp(email, purpose):
    """Create a new code (invalidating earlier ones) and return the raw code for emailing."""
    email = email.lower()
    wait = cooldown_remaining(email, purpose)
    if wait:
        raise OTPCooldown(wait)
    code = generate_code()
    with transaction.atomic():
        EmailOTP.objects.filter(email=email, purpose=purpose, used_at__isnull=True,
                                invalidated=False).update(invalidated=True)
        EmailOTP.objects.create(email=email, purpose=purpose, code_hash=_hash(email, purpose, code),
                                expires_at=timezone.now() + timedelta(seconds=settings.OTP_EXPIRY_SECONDS))
    return code


def verify_otp(email, purpose, code):
    """Return True and mark used if correct; otherwise count the attempt. Constant-time compare."""
    email = (email or "").lower()
    code = (code or "").strip()
    with transaction.atomic():
        otp = (EmailOTP.objects.select_for_update()
               .filter(email=email, purpose=purpose, used_at__isnull=True, invalidated=False)
               .order_by("-created_at").first())
        if otp is None or not otp.is_usable:
            return False
        if len(code) == 6 and code.isdigit() and hmac.compare_digest(otp.code_hash, _hash(email, purpose, code)):
            otp.used_at = timezone.now()
            otp.save(update_fields=["used_at"])
            return True
        otp.attempts += 1
        if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
            otp.invalidated = True
        otp.save(update_fields=["attempts", "invalidated"])
        return False


def send_otp_email(email, purpose, name=""):
    from apps.core.emails import send_email

    code = issue_otp(email, purpose)
    template = "otp_verify" if purpose == EmailOTP.PURPOSE_VERIFY else "otp_login"
    return send_email(template, email, {"code": code, "name": name,
                                        "minutes": settings.OTP_EXPIRY_SECONDS // 60},
                      sensitive=True, related=f"otp:{purpose}")
