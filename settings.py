"""Settings and secrets for Errand Guard.

Secrets are read only through Hermes's secret helper (catalog rule 11): never by
opening .env files ourselves. Outside Hermes (unit tests) nothing is read from the
environment at all; tests inject values with ``set_test_values``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional

DEFAULT_BASE_URL = "https://api.bitrefill.com/v2"
PLUGIN_NAME = "errand-guard"

_TEST_VALUES: Optional[dict[str, str]] = None
_CTX: Any = None  # the PluginContext, set by register()


def bind_context(ctx: Any) -> None:
    global _CTX
    _CTX = ctx


def set_test_values(values: Optional[dict[str, str]]) -> None:
    """Unit tests only: replace every secret/env lookup with this mapping."""
    global _TEST_VALUES
    _TEST_VALUES = dict(values) if values is not None else None


def read_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    if _TEST_VALUES is not None:
        return _TEST_VALUES.get(name, default)
    try:
        from agent.secret_scope import get_secret
    except ImportError:
        return default
    value = get_secret(name, default)
    return value if value not in ("", None) else default


def _config(key: str) -> Any:
    if _TEST_VALUES is not None:
        return _TEST_VALUES.get(f"config:{key}")
    if _CTX is None:
        return None
    try:
        return _CTX.get_config(key, default=None)
    except Exception:
        return None


def _decimal(raw: Any) -> Optional[Decimal]:
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite():
        return None
    return value


def auto_approve_max() -> Decimal:
    """Payments at or below this amount skip the extra human prompt.

    Fail closed: anything missing, unreadable or negative means 0 (always ask).
    """
    raw = _config("auto_approve_max")
    if raw is None:
        raw = read_secret("ERRAND_AUTO_APPROVE_MAX")
    value = _decimal(raw)
    if value is None or value < 0:
        return Decimal("0")
    return value


def daily_cap() -> Optional[Decimal]:
    """Daily spending cap, or None for no cap. Unreadable values mean no extra cap
    (the per-errand max still applies)."""
    raw = _config("daily_cap")
    if raw is None:
        raw = read_secret("ERRAND_DAILY_CAP")
    value = _decimal(raw)
    if value is None or value <= 0:
        return None
    return value


def default_expiry_hours() -> int:
    raw = _config("default_expiry_hours")
    try:
        hours = int(raw) if raw not in (None, "") else 24
    except (TypeError, ValueError):
        hours = 24
    return min(max(hours, 1), 24 * 30)


def bitrefill_api_key() -> Optional[str]:
    return read_secret("BITREFILL_API_KEY")


def bitrefill_base_url() -> str:
    url = (read_secret("BITREFILL_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    return url
