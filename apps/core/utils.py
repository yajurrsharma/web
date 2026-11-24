"""Shared helpers: money, client IP, rate limiting, audit logging."""
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache

TWO = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value) -> Decimal:
    """Round to paise with ROUND_HALF_UP. Never use float for money."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def to_paise(amount: Decimal) -> int:
    return int((money(amount) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def client_ip(request):
    """
    PythonAnywhere puts the real client IP first in X-Real-IP / X-Forwarded-For.
    Used only for rate-limit keys and audit logs.
    """
    real = request.META.get("HTTP_X_REAL_IP")
    if real:
        return real.strip()
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def rate_limited(key: str, limit: int, window_seconds: int) -> bool:
    """
    Fixed-window counter. Returns True when the caller is OVER the limit.
    Uses the configured cache (database cache in production so all workers share it).
    """
    cache_key = f"rl:{key}"
    added = cache.add(cache_key, 1, window_seconds)
    if added:
        return False
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, window_seconds)
        return False
    return count > limit


def audit(action, summary, actor=None, obj=None, request=None, **details):
    from .models import AuditLog

    return AuditLog.objects.create(
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        action=action,
        object_type=obj._meta.label if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        summary=summary[:300],
        details=details,
        ip_address=client_ip(request) or None if request else None,
    )


INDIAN_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
               "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
               "Eighteen", "Nineteen"]
INDIAN_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two_digits(n):
    if n < 20:
        return INDIAN_ONES[n]
    return (INDIAN_TENS[n // 10] + " " + INDIAN_ONES[n % 10]).strip()


def _three_digits(n):
    hundred, rest = divmod(n, 100)
    parts = []
    if hundred:
        parts.append(INDIAN_ONES[hundred] + " Hundred")
    if rest:
        parts.append(_two_digits(rest))
    return " ".join(parts)


def _indian_words(n: int) -> str:
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore:
        parts.append(_indian_words(crore) + " Crore")
    if lakh:
        parts.append(_two_digits(lakh) + " Lakh")
    if thousand:
        parts.append(_two_digits(thousand) + " Thousand")
    if n:
        parts.append(_three_digits(n))
    return " ".join(parts)


def amount_in_words(amount: Decimal) -> str:
    """Indian numbering: 123456.50 -> 'Rupees One Lakh Twenty Three Thousand Four Hundred Fifty Six and Fifty Paise Only'."""
    amount = money(amount)
    rupees = int(amount)
    paise = int((amount - rupees) * 100)
    words = _indian_words(rupees) if rupees else "Zero"
    out = f"Rupees {words}"
    if paise:
        out += f" and {_two_digits(paise)} Paise"
    return out + " Only"
