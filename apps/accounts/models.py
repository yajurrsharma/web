"""
Custom user model — email is the unique login identifier.

Created in Stage 1 on purpose: Django requires AUTH_USER_MODEL to be set before
the first migration. Stage 2/4 add OTP, addresses and profile features on top.
"""
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from apps.core.gst import STATE_CHOICES

indian_mobile_validator = RegexValidator(
    regex=r"^[6-9]\d{9}$",
    message="Enter a valid 10-digit Indian mobile number (without +91).",
)


def normalize_mobile(value):
    """'+91 98765-43210', '098765 43210', '9876543210' → '9876543210'. Anything else → ''."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 and digits[0] in "6789" else ""


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()  # OTP-only accounts
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_verified", True)
        if extra_fields.get("is_staff") is not True or extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField("email address", unique=True)
    full_name = models.CharField(max_length=150)
    mobile = models.CharField(max_length=10, blank=True, validators=[indian_mobile_validator],
                              help_text="Optional. Can be used instead of the email to log in, so each number "
                                        "belongs to one account only.")

    is_verified = models.BooleanField(default=False, help_text="Email verified via OTP.")
    # Marketing consent (DPDP Act): only the customer can switch this on, and the time is kept as proof.
    marketing_opt_in = models.BooleanField(
        "Agreed to offer emails", default=False,
        help_text="Set only by the customer (registration, profile). Unsubscribe links switch it off.")
    marketing_opt_in_at = models.DateTimeField("Consent given / withdrawn at", null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Can log in to the admin/dashboard.")

    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        ordering = ["-date_joined"]
        constraints = [
            # A mobile number logs in to exactly one account; blank stays allowed for many users.
            models.UniqueConstraint(
                fields=["mobile"], condition=~models.Q(mobile=""), name="unique_mobile_if_set",
                violation_error_message="This mobile number already belongs to another account. Use a different "
                                        "number, or leave Mobile empty."),
        ]

    def __str__(self):
        return self.email

    def clean(self):
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email).lower()

    def set_marketing_consent(self, agreed, *, save=True):
        """Record a change of the customer's own choice, with the time (proof of consent)."""
        agreed = bool(agreed)
        if agreed != self.marketing_opt_in or self.marketing_opt_in_at is None:
            self.marketing_opt_in = agreed
            self.marketing_opt_in_at = timezone.now()
            if save and self.pk:
                self.save(update_fields=["marketing_opt_in", "marketing_opt_in_at", "updated_at"])

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.email


class Address(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=30, blank=True, help_text="e.g. Home, Office")
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=10, validators=[indian_mobile_validator])
    line1 = models.CharField("House / flat / street", max_length=200)
    line2 = models.CharField("Area / landmark", max_length=200, blank=True)
    city = models.CharField(max_length=100)
    state_code = models.CharField("State", max_length=2, choices=STATE_CHOICES)
    pincode = models.CharField("PIN code", max_length=6)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "-updated_at"]
        verbose_name_plural = "addresses"

    def __str__(self):
        parts = [self.full_name, self.line1, self.line2, self.city, f"{self.state_name} {self.pincode}"]
        return ", ".join(p for p in parts if p)

    def clean(self):
        from apps.core.gst import validate_pincode

        validate_pincode(self.pincode)

    @property
    def state_name(self):
        from apps.core.gst import STATE_NAMES

        return STATE_NAMES.get(self.state_code, "")

    def as_snapshot(self):
        return {
            "full_name": self.full_name, "phone": self.phone, "line1": self.line1, "line2": self.line2,
            "city": self.city, "state_code": self.state_code, "state": self.state_name,
            "pincode": self.pincode,
        }


class EmailOTP(models.Model):
    """
    One-time codes are stored only as an HMAC (keyed with SECRET_KEY), never in
    plain text. Each code expires, has an attempt limit and can be used once.
    """

    PURPOSE_VERIFY = "verify"
    PURPOSE_LOGIN = "login"
    PURPOSE_CHOICES = [(PURPOSE_VERIFY, "Email verification"), (PURPOSE_LOGIN, "Passwordless login")]

    email = models.EmailField(db_index=True)
    purpose = models.CharField(max_length=10, choices=PURPOSE_CHOICES)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    invalidated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["email", "purpose", "created_at"])]
        verbose_name = "email OTP"

    def __str__(self):
        return f"{self.email} ({self.purpose})"

    @property
    def is_usable(self):
        from django.conf import settings

        return (not self.used_at and not self.invalidated and self.expires_at > timezone.now()
                and self.attempts < settings.OTP_MAX_ATTEMPTS)
