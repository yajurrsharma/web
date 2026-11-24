import os
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template
from django.conf import settings
from django.templatetags.static import static

register = template.Library()
_versions = {}


@register.simple_tag
def static_v(path):
    """Static URL with ?v=<last-modified> so browsers fetch a CSS/JS file again as soon as it changes."""
    version = None if settings.DEBUG else _versions.get(path)
    if version is None:
        from django.contrib.staticfiles import finders

        found = finders.find(path)
        version = str(int(os.path.getmtime(found))) if found else ""
        _versions[path] = version
    url = static(path)
    return f"{url}?v={version}" if version else url


def _indian_group(int_part: str) -> str:
    if len(int_part) <= 3:
        return int_part
    head, tail = int_part[:-3], int_part[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


@register.filter
def inr(value, decimals=2):
    """₹1,23,456.50 (Indian digit grouping). Use |inr:0 for whole rupees."""
    if value in (None, ""):
        return ""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    decimals = int(decimals)
    q = Decimal(1) if decimals == 0 else Decimal(1).scaleb(-decimals)
    amount = amount.quantize(q, rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    text = f"{abs(amount):.{decimals}f}"
    int_part, _, frac = text.partition(".")
    return f"{sign}₹{_indian_group(int_part)}" + (f".{frac}" if frac else "")


@register.filter
def inr_short(value):
    """Shop-style price: ₹225 for whole rupees, ₹225.50 otherwise."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    return inr(amount, 0 if amount == amount.to_integral_value() else 2)


@register.filter
def stars(value):
    """4.6 → '★★★★★', 4.2 → '★★★★☆' (rounded to the nearest whole star)."""
    try:
        n = int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return ""
    n = max(0, min(5, n))
    return "★" * n + "☆" * (5 - n)


@register.filter
def thumb(fieldfile, size=480):
    """URL of a small copy of an uploaded photo (made once, then reused). Falls back to the original."""
    if not fieldfile:
        return ""
    try:
        from apps.core.images import thumb_url

        return thumb_url(fieldfile, size)
    except Exception:  # noqa: BLE001 — a missing/odd file must never break the page
        try:
            return fieldfile.url
        except ValueError:
            return ""


@register.filter
def pct(value):
    try:
        return f"{Decimal(str(value)).normalize():f}%"
    except (InvalidOperation, ValueError):
        return value


@register.simple_tag(takes_context=True)
def page_url(context, page_number):
    qs = context.get("querystring", "")
    return f"?{qs + '&' if qs else ''}page={page_number}"


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def wa_number(value):
    """Digits for a wa.me link: '98765 43210' → '919876543210' (adds India's 91 to 10-digit numbers)."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return "91" + digits if len(digits) == 10 else digits
