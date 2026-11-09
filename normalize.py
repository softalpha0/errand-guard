"""Turning messy values into comparable ones: phones, emails, money, eSIM packages.

Every helper returns None when it cannot be sure, so the guard can fail closed.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3,4}$")


# ---- email / phone ---------------------------------------------------------------
def email(value: Any) -> Optional[str]:
    """Lower-cased, trimmed email, or None if it is not one."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text if _EMAIL_RE.match(text) else None


def phone(value: Any) -> Optional[str]:
    """Digits of a phone number. International numbers keep a leading '+'.

    '+234 803-123-4567' -> '+2348031234567'; '00234...' -> '+234...'; '0803 123 4567' -> '08031234567'.
    """
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or re.search(r"[A-Za-z@]", text):
        return None
    plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not plus and digits.startswith("00"):
        plus, digits = True, digits[2:]
    if len(digits) < 6 or len(digits) > 15:
        return None
    return ("+" + digits) if plus else digits


def same_phone(a: Any, b: Any) -> bool:
    """True only when both numbers are clearly the same line.

    Equal digits match. A local number with one leading 0 ('08031234567') matches an
    international one ('+2348031234567') when the international number is a 1-3 digit
    country code followed by exactly the local number without its 0.
    """
    pa, pb = phone(a), phone(b)
    if pa is None or pb is None:
        return False
    if pa.lstrip("+") == pb.lstrip("+"):
        return True
    intl, local = (pa, pb) if pa.startswith("+") else (pb, pa)
    if not intl.startswith("+") or local.startswith("+"):
        return False
    if not local.startswith("0") or local.startswith("00"):
        return False
    national = local[1:]
    if len(national) < 6:
        return False
    digits = intl[1:]
    return digits.endswith(national) and 1 <= len(digits) - len(national) <= 3


def recipient(value: Any) -> tuple[str, Optional[str]]:
    """Classify a recipient: ('email', x) / ('phone', x) / ('none', '') / ('invalid', None)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "none", ""
    mail = email(value)
    if mail:
        return "email", mail
    tel = phone(value)
    if tel:
        return "phone", tel
    return "invalid", None


def same_recipient(expected_type: str, expected: str, actual_type: str, actual: Any) -> bool:
    if expected_type == "none":
        return actual_type == "none"
    if expected_type != actual_type:
        return False
    if expected_type == "email":
        return email(actual) is not None and email(actual) == email(expected)
    if expected_type == "phone":
        return same_phone(expected, actual)
    return False


# ---- money ---------------------------------------------------------------------
def money(value: Any) -> Optional[Decimal]:
    """A finite, non-negative Decimal amount, or None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


def fmt_money(amount: Any, currency: str = "") -> str:
    value = money(amount)
    if value is None:
        return "unknown"
    text = str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return f"{text} {currency.upper()}".strip()


def currency(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if _CURRENCY_RE.match(code) else None


# ---- eSIM / data packages -----------------------------------------------------------
_DATA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(TB|GB|MB)\b", re.I)
_DAYS_RE = re.compile(r"(\d+)\s*(day|days|d)\b", re.I)
_WEEKS_RE = re.compile(r"(\d+)\s*(week|weeks)\b", re.I)
_MONTHS_RE = re.compile(r"(\d+)\s*(month|months)\b", re.I)


def data_gb(text: Any) -> Optional[Decimal]:
    """Data allowance in GB from text like '5GB, 7 Days' or '500 MB'. 'Unlimited' -> Decimal('Infinity').
    None when there is no single clear amount."""
    if not isinstance(text, str):
        return None
    found = _DATA_RE.findall(text)
    if len(found) == 1:
        number, unit = Decimal(found[0][0]), found[0][1].upper()
        return {"TB": number * 1000, "GB": number, "MB": number / 1000}[unit]
    if not found and re.search(r"\bunlimited\b", text, re.I):
        return Decimal("Infinity")
    return None


def duration_days(text: Any) -> Optional[int]:
    """Validity in days from text like '5GB, 7 Days', '2 weeks' or '1 month' (30 days)."""
    if not isinstance(text, str):
        return None
    hits = [(int(n), 1) for n, _ in _DAYS_RE.findall(text)]
    hits += [(int(n), 7) for n, _ in _WEEKS_RE.findall(text)]
    hits += [(int(n), 30) for n, _ in _MONTHS_RE.findall(text)]
    if len(hits) != 1:
        return None
    number, factor = hits[0]
    return number * factor


def fmt_gb(value: Optional[Decimal]) -> str:
    if value is None:
        return "unknown data"
    if value == Decimal("Infinity"):
        return "unlimited data"
    return f"{value.normalize():f} GB"


# ---- keywords ------------------------------------------------------------------
def words(text: Any) -> str:
    """Lower-case text with punctuation turned into spaces, for keyword matching."""
    if not isinstance(text, str):
        return ""
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def keyword_missing(keywords: list[str], haystack: str) -> list[str]:
    """Keywords not found as whole words (or whole phrases) in haystack."""
    hay = words(haystack)
    missing = []
    for keyword in keywords:
        needle = words(keyword)
        if needle.strip() and needle not in hay:
            missing.append(keyword)
    return missing
