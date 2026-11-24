"""Local development settings (Windows PC). Never use on the live server."""
from .base import *  # noqa: F401,F403
from .base import env

DEBUG = True

# Print emails (including OTPs) to the console in development only.
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
