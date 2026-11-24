"""Production settings (PythonAnywhere). HTTPS-only, DEBUG off, secrets from env."""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import ALLOWED_HOSTS, SECRET_KEY, env_bool, env_int

DEBUG = False

if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Set DJANGO_ALLOWED_HOSTS to your real domain(s) in production.")
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("django-insecure") or "change-me" in SECRET_KEY:
    raise ImproperlyConfigured("Set a strong, unique DJANGO_SECRET_KEY in production.")

# PythonAnywhere terminates TLS in front of the app and sets X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Start HSTS small; raise to 31536000 only after HTTPS is confirmed stable.
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 3600)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = False
