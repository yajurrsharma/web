"""
Base settings shared by every environment (development, test, production).

All secrets and environment-specific values come from environment variables,
loaded from a local `.env` file when present. Never hardcode real credentials.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and (value is None or value == ""):
        raise ImproperlyConfigured(f"Missing required environment variable: {name}")
    return value


def env_bool(name, default=False):
    return str(env(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in str(env(name, default)).split(",") if item.strip()]


def env_int(name, default):
    return int(env(name, default))


# --- Core ---------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

SITE_NAME = env("SITE_NAME", "Life Line Dynamics")
SITE_URL = env("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_URL_PATH = env("ADMIN_URL_PATH", "manage-lld/").strip("/") + "/"

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.sitemaps",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.products",
    "apps.cart",
    "apps.orders",
    "apps.inventory",
    "apps.payments",
    "apps.invoicing",
    "apps.dashboard",
    "apps.marketplace",
    "apps.campaigns",
    "apps.shipping",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
            ],
        },
    },
]

# --- Database -----------------------------------------------------------
# DB_ENGINE=sqlite (local default) or mysql (production on PythonAnywhere).
DB_ENGINE = env("DB_ENGINE", "sqlite").lower()

if DB_ENGINE == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": env("DB_NAME", required=True),
            "USER": env("DB_USER", required=True),
            "PASSWORD": env("DB_PASSWORD", required=True),
            "HOST": env("DB_HOST", required=True),
            "PORT": env("DB_PORT", "3306"),
            "OPTIONS": {
                "charset": "utf8mb4",
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            },
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
        }
    }
elif DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / env("SQLITE_NAME", "db.sqlite3"),
        }
    }
else:
    raise ImproperlyConfigured("DB_ENGINE must be 'sqlite' or 'mysql'.")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentication -----------------------------------------------------
# Custom user model is defined BEFORE the first migration (see apps/accounts).
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:home"
PASSWORD_RESET_TIMEOUT = 60 * 60  # 1 hour, single-use tokens (Django built-in)

# --- I18N / time --------------------------------------------------------
LANGUAGE_CODE = "en-in"
TIME_ZONE = env("TIME_ZONE", "Asia/Kolkata")  # display zone; DB stores UTC
USE_I18N = True
USE_TZ = True

# --- Static & media -----------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = Path(env("STATIC_ROOT", BASE_DIR / "staticfiles"))

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", BASE_DIR / "media"))
# Private files (e.g. issued invoice PDFs) are never served from MEDIA_URL;
# they are streamed through permission-checked views.
PRIVATE_MEDIA_ROOT = Path(env("PRIVATE_MEDIA_ROOT", BASE_DIR / "private_media"))

# Upload limits — configurable; large videos belong on external storage (YouTube).
# Photos may be large on upload because they are shrunk to max 1600 px (~150–300 KB) before saving.
MAX_IMAGE_UPLOAD_SIZE = env_int("MAX_IMAGE_UPLOAD_SIZE", 10 * 1024 * 1024)
MAX_VIDEO_UPLOAD_SIZE = env_int("MAX_VIDEO_UPLOAD_SIZE", 10 * 1024 * 1024)
MAX_VIDEO_SECONDS = env_int("MAX_VIDEO_SECONDS", 10)  # 0 = no length check
MAX_VIDEOS_PER_PRODUCT = env_int("MAX_VIDEOS_PER_PRODUCT", 1)  # 0 = YouTube links only (no hosting space)
MAX_PHOTOS_PER_PRODUCT = env_int("MAX_PHOTOS_PER_PRODUCT", 12)
# Your hosting disk allowance in MB (PythonAnywhere: Account page). 0 = unknown; the dashboard then shows
# usage without a warning. Shared with every other app on the same hosting account.
STORAGE_QUOTA_MB = env_int("STORAGE_QUOTA_MB", 0)
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_PERMISSIONS = 0o644

# --- Email --------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 20)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", f"{SITE_NAME} <no-reply@example.com>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
ADMIN_ALERT_EMAILS = env_list("ADMIN_ALERT_EMAILS", "")
# Offer emails go out in batches so the email provider does not block the shop.
# Gmail allows ~500/day; business providers (Zoho, Google Workspace, Brevo …) allow more — raise to match yours.
CAMPAIGN_BATCH_SIZE = env_int("CAMPAIGN_BATCH_SIZE", 40)
CAMPAIGN_DAILY_LIMIT = env_int("CAMPAIGN_DAILY_LIMIT", 400)

# --- Shiprocket (courier booking + automatic tracking) ------------------
# Use a Shiprocket *API user* (Settings → API → Configure in Shiprocket), never your main login.
SHIPROCKET_API_EMAIL = env("SHIPROCKET_API_EMAIL", "")
SHIPROCKET_API_PASSWORD = env("SHIPROCKET_API_PASSWORD", "")
# Any long random text; paste the same value as the "token" when adding the webhook in Shiprocket.
SHIPROCKET_WEBHOOK_TOKEN = env("SHIPROCKET_WEBHOOK_TOKEN", "")
SHIPROCKET_BASE_URL = env("SHIPROCKET_BASE_URL", "https://apiv2.shiprocket.in/v1/external").rstrip("/")
SHIPPING_ENABLED = bool(SHIPROCKET_API_EMAIL and SHIPROCKET_API_PASSWORD)

# --- Payments (Razorpay) ------------------------------------------------
# Keys only from the environment. Key secret & webhook secret never reach templates.
RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = env("RAZORPAY_WEBHOOK_SECRET", "")
PAYMENTS_ENABLED = bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)

# --- OTP ----------------------------------------------------------------
OTP_EXPIRY_SECONDS = env_int("OTP_EXPIRY_SECONDS", 300)
OTP_MAX_ATTEMPTS = env_int("OTP_MAX_ATTEMPTS", 5)
OTP_RESEND_COOLDOWN_SECONDS = env_int("OTP_RESEND_COOLDOWN_SECONDS", 60)

# --- Sessions & cookies -------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
CSRF_COOKIE_HTTPONLY = False  # Required for JS fetch with CSRF header
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

# --- Cache (used for rate limiting) -------------------------------------
# Local-memory cache is per-process. On PythonAnywhere use the database cache
# (run `python manage.py createcachetable`) so limits hold across workers.
CACHE_BACKEND = env("CACHE_BACKEND", "locmem")
if CACHE_BACKEND == "db":
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache",
                          "LOCATION": "django_cache"}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# --- Messages → Bootstrap alert classes ---
from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {message_constants.ERROR: "danger", message_constants.DEBUG: "secondary"}

# --- Google Play (Trusted Web Activity) ---
ANDROID_PACKAGE_NAME = env("ANDROID_PACKAGE_NAME", "")
ANDROID_SHA256_FINGERPRINTS = env_list("ANDROID_SHA256_FINGERPRINTS", "")

# --- Amazon SP-API (phase 2; keep disabled until onboarding is complete) ---
AMAZON_SP_API_ENABLED = env_bool("AMAZON_SP_API_ENABLED", False)

# --- Logging ------------------------------------------------------------
LOG_DIR = Path(env("LOG_DIR", BASE_DIR / "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "app.log",
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console", "file"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.security": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
    },
}
