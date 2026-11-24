"""
Minimal Shiprocket API client (standard library only — no extra package to install).

Docs: https://apidocs.shiprocket.in  ·  Help: Settings → API in the Shiprocket panel.
Login uses a Shiprocket *API user*; the token it returns is valid for 10 days, so it is cached for 9.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)
TOKEN_CACHE_KEY = "shiprocket_token_v1"
TOKEN_SECONDS = 9 * 24 * 3600
TIMEOUT = 25


class ShippingError(Exception):
    """A problem talking to Shiprocket, with a message staff can act on."""


def _http(method, path, *, data=None, params=None, token=None):
    url = settings.SHIPROCKET_BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode() or "{}")
        except ValueError:
            payload = {}
        return exc.code, payload
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ShippingError(f"Could not reach Shiprocket ({type(exc).__name__}). Check the internet connection "
                            "and try again.") from exc


def _message(payload):
    if isinstance(payload, dict):
        msg = payload.get("message") or payload.get("error") or ""
        errors = payload.get("errors")
        if isinstance(errors, dict):
            msg = (msg + " " + "; ".join(f"{k}: {v}" for k, v in errors.items())).strip()
        return str(msg)[:300] or "Unknown error"
    return "Unknown error"


def get_token(force=False):
    if not settings.SHIPPING_ENABLED:
        raise ShippingError("Shiprocket is not set up: add SHIPROCKET_API_EMAIL and SHIPROCKET_API_PASSWORD "
                            "to the .env file and restart the server.")
    token = None if force else cache.get(TOKEN_CACHE_KEY)
    if token:
        return token
    status, payload = _http("POST", "/auth/login", data={"email": settings.SHIPROCKET_API_EMAIL,
                                                         "password": settings.SHIPROCKET_API_PASSWORD})
    token = payload.get("token") if isinstance(payload, dict) else None
    if status != 200 or not token:
        raise ShippingError("Shiprocket login failed — check the API user email/password in .env "
                            f"(use the API user, not your main login). Shiprocket said: {_message(payload)}")
    cache.set(TOKEN_CACHE_KEY, token, TOKEN_SECONDS)
    return token


def call(method, path, *, data=None, params=None):
    """Authenticated call. Logs in again once if the token has expired."""
    status, payload = _http(method, path, data=data, params=params, token=get_token())
    if status == 401:
        status, payload = _http(method, path, data=data, params=params, token=get_token(force=True))
    if status >= 400:
        logger.warning("Shiprocket %s %s failed: %s", method, path, status)
        raise ShippingError(f"Shiprocket said: {_message(payload)}")
    return payload
