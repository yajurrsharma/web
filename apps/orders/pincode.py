"""
City / state from an Indian PIN code, to save typing at checkout.

Uses the free public India Post lookup (api.postalpincode.in). Results are cached for 30 days. If the
service is slow or down, nothing breaks — the customer simply types the city and state.
"""
import json
import logging
import re
import urllib.request

from django.core.cache import cache

from apps.core.gst import GST_STATES

logger = logging.getLogger(__name__)
URL = "https://api.postalpincode.in/pincode/{pin}"
TIMEOUT = 4
_NAME_TO_CODE = {name.lower(): code for code, name in GST_STATES}
_ALIASES = {"delhi": "07", "nct of delhi": "07", "jammu & kashmir": "01", "jammu and kashmir": "01",
            "andaman & nicobar islands": "35", "andaman and nicobar islands": "35",
            "dadra and nagar haveli and daman and diu": "26", "dadra & nagar haveli": "26",
            "daman & diu": "26", "pondicherry": "34", "puducherry": "34", "orissa": "21", "odisha": "21",
            "uttaranchal": "05", "chattisgarh": "22", "chhattisgarh": "22", "telangana": "36",
            "ladakh": "38", "lakshadweep": "31", "chandigarh": "04"}


def state_code_for(name):
    key = (name or "").strip().lower()
    return _NAME_TO_CODE.get(key) or _ALIASES.get(key, "")


def lookup(pin):
    """{'city': 'Zirakpur', 'state_code': '03', 'state': 'Punjab'} or None."""
    if not re.fullmatch(r"[1-9]\d{5}", pin or ""):
        return None
    key = f"pin:{pin}"
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    result = {}
    try:
        with urllib.request.urlopen(URL.format(pin=pin), timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode() or "[]")
        offices = (data[0] or {}).get("PostOffice") or [] if data else []
        if offices:
            office = offices[0]
            state = office.get("State", "")
            code = state_code_for(state)
            city = office.get("District") or office.get("Block") or office.get("Name") or ""
            if code:
                result = {"city": city, "state_code": code, "state": state}
    except Exception as exc:  # noqa: BLE001 — never block checkout on this helper
        logger.info("PIN lookup %s failed: %s", pin, type(exc).__name__)
        return None  # don't cache failures: try again next time
    cache.set(key, result, 30 * 24 * 3600)
    return result or None


def add_working_days(start, days):
    """Date `days` working days after `start` (Sundays don't count)."""
    from datetime import timedelta

    day, left = start, days
    while left > 0:
        day += timedelta(days=1)
        if day.weekday() != 6:
            left -= 1
    return day


def delivery_check(store, pin, today=None):
    """What the "Check delivery" box on a product page shows for a PIN code."""
    from django.utils import timezone

    if not re.fullmatch(r"[1-9]\d{5}", pin or ""):
        return {"ok": False, "message": "Enter a valid 6-digit PIN code."}
    if not store.is_pincode_serviceable(pin):
        return {"ok": False, "message": f"Sorry, we don't deliver to {pin} yet."}
    place = lookup(pin) or {}
    local = bool(place) and place.get("state_code") == store.state_code
    lo, hi = ((store.delivery_days_local_min, store.delivery_days_local_max) if local
              else (store.delivery_days_india_min, store.delivery_days_india_max))
    today = today or timezone.localdate()
    first, last = add_working_days(today, lo), add_working_days(today, max(hi, lo))
    when = (first.strftime("%a, %d %b") if first == last
            else f"{first.strftime('%a, %d %b')} – {last.strftime('%a, %d %b')}")
    return {"ok": True, "pin": pin, "place": f"{place['city']}, {place['state']}" if place else "",
            "delivery": when, "cod": bool(store.cod_enabled)}
