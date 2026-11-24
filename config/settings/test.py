"""Settings used by the automated test suite. No real credentials ever."""
from .base import *  # noqa: F401,F403
from .base import BASE_DIR

DEBUG = False
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # speed only
MEDIA_ROOT = BASE_DIR / "test_media"
PRIVATE_MEDIA_ROOT = BASE_DIR / "test_private_media"
RAZORPAY_KEY_ID = "rzp_test_dummy"
RAZORPAY_KEY_SECRET = "test_secret_dummy"
RAZORPAY_WEBHOOK_SECRET = "test_webhook_secret_dummy"
PAYMENTS_ENABLED = True
SITE_SETTINGS_CACHE_SECONDS = 0  # DB rollbacks between tests must not leave stale cached settings
ADMIN_ALERT_EMAILS = []
SHIPROCKET_API_EMAIL = "api@example.com"
SHIPROCKET_API_PASSWORD = "test-password-dummy"
SHIPROCKET_WEBHOOK_TOKEN = "test-webhook-token-dummy"
SHIPPING_ENABLED = True
