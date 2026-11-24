"""
GST reference data and helpers.

State codes are the two-digit GST state codes used in GSTINs and for place of
supply. Confirm any change with your CA; this list is reference data only.
"""
import re

GST_STATES = [
    ("01", "Jammu and Kashmir"),
    ("02", "Himachal Pradesh"),
    ("03", "Punjab"),
    ("04", "Chandigarh"),
    ("05", "Uttarakhand"),
    ("06", "Haryana"),
    ("07", "Delhi"),
    ("08", "Rajasthan"),
    ("09", "Uttar Pradesh"),
    ("10", "Bihar"),
    ("11", "Sikkim"),
    ("12", "Arunachal Pradesh"),
    ("13", "Nagaland"),
    ("14", "Manipur"),
    ("15", "Mizoram"),
    ("16", "Tripura"),
    ("17", "Meghalaya"),
    ("18", "Assam"),
    ("19", "West Bengal"),
    ("20", "Jharkhand"),
    ("21", "Odisha"),
    ("22", "Chhattisgarh"),
    ("23", "Madhya Pradesh"),
    ("24", "Gujarat"),
    ("26", "Dadra and Nagar Haveli and Daman and Diu"),
    ("27", "Maharashtra"),
    ("29", "Karnataka"),
    ("30", "Goa"),
    ("31", "Lakshadweep"),
    ("32", "Kerala"),
    ("33", "Tamil Nadu"),
    ("34", "Puducherry"),
    ("35", "Andaman and Nicobar Islands"),
    ("36", "Telangana"),
    ("37", "Andhra Pradesh"),
    ("38", "Ladakh"),
]
STATE_CHOICES = [(code, f"{name}") for code, name in GST_STATES]
STATE_NAMES = dict(GST_STATES)

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_checksum_ok(gstin: str) -> bool:
    """Validate the 15th check character of a GSTIN (mod-36 algorithm)."""
    gstin = gstin.upper()
    total = 0
    for i, ch in enumerate(gstin[:14]):
        value = _CHARSET.index(ch) * (2 if i % 2 else 1)
        total += value // 36 + value % 36
    check = (36 - total % 36) % 36
    return _CHARSET[check] == gstin[14]


def validate_gstin(value: str):
    from django.core.exceptions import ValidationError

    value = (value or "").strip().upper()
    if not GSTIN_RE.match(value):
        raise ValidationError("Enter a valid 15-character GSTIN.")
    if value[:2] not in STATE_NAMES:
        raise ValidationError("GSTIN state code is not recognised.")
    if not gstin_checksum_ok(value):
        raise ValidationError("GSTIN check digit is invalid. Please re-check the number.")


PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")


def validate_pincode(value: str):
    from django.core.exceptions import ValidationError

    if not PINCODE_RE.match((value or "").strip()):
        raise ValidationError("Enter a valid 6-digit PIN code.")
