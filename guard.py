"""The guard: compare a purchase with the errand (mandate) the user confirmed.

Pure functions, no Hermes imports, so every rule can be unit-tested.

Rule order (PRD section 8):
  no confirmed errand -> expired / fulfilled / in progress -> recipient -> item ->
  merchant -> quantity -> amount (and currency) -> daily cap ->
  above auto-approve limit: ask a human -> otherwise allow.

Every block message names the rule, the errand, and the expected vs actual value.
Anything missing or unreadable blocks (fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from . import normalize as n

ALLOW, BLOCK, APPROVE = "allow", "block", "approve"

# Kinds Errand Guard can buy today (all through Bitrefill). Food delivery and online
# shopping are covered as gift cards (e.g. an Amazon or Uber Eats gift card).
KINDS = ("airtime", "data", "esim", "gift_card")
# Which recipient each kind accepts: airtime/data top up a phone; eSIMs and gift cards go
# to an email, or to the user's own Bitrefill account when no recipient is given.
RECIPIENT_TYPES = {"airtime": ("phone",), "data": ("phone",), "esim": ("email", "none"),
                   "gift_card": ("email", "none")}


@dataclass
class Decision:
    action: str
    rule: str
    message: str
    errand_id: Optional[int] = None
    expected: str = ""
    actual: str = ""
    details: dict = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.action == BLOCK

    def as_hook_result(self, rule_key: Optional[str] = None) -> Optional[dict]:
        if self.action == BLOCK:
            return {"action": "block", "message": self.message}
        if self.action == APPROVE:
            result = {"action": "approve", "message": self.message}
            if rule_key:
                result["rule_key"] = rule_key
            return result
        return None


def _block(rule: str, errand_id: Optional[int], expected: str, actual: str, hint: str = "") -> Decision:
    where = f" on errand #{errand_id}" if errand_id is not None else ""
    message = (f"⛔ Blocked by Errand Guard — rule: {rule}{where}. "
               f"Expected: {expected}. Got: {actual}.")
    if hint:
        message += f" {hint}"
    return Decision(BLOCK, rule, message, errand_id, expected, actual)


# ---- human-readable descriptions ----------------------------------------------------
def describe_errand(errand: dict) -> str:
    parts = [str(errand.get("item") or errand.get("kind") or "errand")]
    specs = errand_specs(errand)
    if specs:
        parts.append(specs)
    recipient = errand.get("recipient") or "your account (no recipient)"
    parts.append(f"to {recipient}")
    parts.append(f"max {n.fmt_money(errand.get('max_amount'), errand.get('currency') or '')}")
    qty = int(errand.get("quantity") or 1)
    if qty != 1:
        parts.append(f"quantity {qty}")
    merchants = errand.get("merchants") or []
    if merchants:
        parts.append("via " + ", ".join(merchants))
    return " · ".join(parts)


def errand_specs(errand: dict) -> str:
    bits = []
    if errand.get("data_gb") not in (None, ""):
        bits.append(n.fmt_gb(_gb(errand["data_gb"])))
    if errand.get("duration_days") not in (None, ""):
        bits.append(f"{int(errand['duration_days'])} days")
    if errand.get("face_value") not in (None, ""):
        bits.append(f"value {n.fmt_money(errand['face_value'], errand.get('face_currency') or errand.get('currency') or '')}")
    return " / ".join(bits)


def purchase_specs(purchase: dict) -> str:
    bits = []
    gb = _gb(purchase.get("data_gb"))
    if gb is not None:
        bits.append(n.fmt_gb(gb))
    if purchase.get("duration_days") not in (None, ""):
        bits.append(f"{int(purchase['duration_days'])} days")
    if purchase.get("face_value") not in (None, ""):
        bits.append(f"value {n.fmt_money(purchase['face_value'], purchase.get('face_currency') or '')}")
    return " / ".join(bits)


def describe_purchase(purchase: dict) -> str:
    name = purchase.get("product_name") or purchase.get("product_id") or "unknown product"
    package = purchase.get("package")
    text = f"{name}" + (f" ({package})" if package else "")
    recipient = purchase.get("recipient") or "no recipient"
    price = n.fmt_money(purchase.get("price"), purchase.get("currency") or "")
    qty = int(purchase.get("quantity") or 0)
    return f"{text} · to {recipient} · {price}" + (f" · quantity {qty}" if qty != 1 else "")


def _gb(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    if str(value).lower() in ("infinity", "inf", "unlimited"):
        return Decimal("Infinity")
    return n.money(value)


# ---- errand state checks (shared by confirm and buy) --------------------------------------
def _errand_state(errand: Optional[dict], conversation_id: str, now: float,
                  *, purpose: str) -> Optional[Decision]:
    if errand is None:
        return _block("no-mandate", None, "a confirmed errand for this payment", "none",
                      "Create one with errand_create and have the user confirm it first.")
    eid = errand.get("id")
    if not conversation_id or errand.get("conversation_id") != conversation_id:
        return _block("no-mandate", eid, "an errand created in this conversation",
                      "an errand from a different conversation" if conversation_id else "no conversation id",
                      "Create and confirm a new errand here.")
    status = errand.get("status")
    if status == "cancelled":
        return _block("errand-cancelled", eid, "an active errand", "it was cancelled")
    if status == "fulfilled":
        return _block("already-fulfilled", eid, "an errand not yet bought", "it was already bought",
                      "Each errand pays once. Create a new errand for another purchase.")
    if status == "buying":
        return _block("in-progress", eid, "no payment already running", "a payment is in progress")
    if status == "payment_unknown":
        return _block("payment-unknown", eid, "a clear payment state",
                      "the last payment's result is unknown",
                      "Check it with errand_status before trying again.")
    try:
        expires_at = float(errand.get("expires_at"))
    except (TypeError, ValueError):
        return _block("expired", eid, "a valid expiry time", "unreadable expiry")
    if now > expires_at:
        return _block("expired", eid, f"payment before {_ts(expires_at)}", f"now is {_ts(now)}")
    if purpose == "buy" and status != "active":
        return _block("not-confirmed", eid, "an errand the user confirmed", f"status '{status}'",
                      "Call errand_confirm so the user can approve it.")
    if purpose == "confirm" and status != "draft":
        return _block("not-a-draft", eid, "a draft errand waiting for confirmation", f"status '{status}'")
    return None


def _ts(epoch: float) -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(epoch))


def check_confirm(errand: Optional[dict], *, conversation_id: str, now: float) -> Decision:
    """errand_confirm always needs a real person's yes (never auto-approved)."""
    problem = _errand_state(errand, conversation_id, now, purpose="confirm")
    if problem:
        return problem
    # Short, key facts first: Hermes's approval box gives the description only the rows left over.
    message = f"Confirm errand #{errand['id']}: {describe_errand(errand)}"
    return Decision(APPROVE, "confirm-errand", message, errand["id"])


# ---- purchase checks -------------------------------------------------------------
def check_purchase(errand: Optional[dict], purchase: Optional[dict], *, conversation_id: str, now: float,
                   auto_approve_max: Decimal, daily_cap: Optional[Decimal] = None,
                   spent_today: Decimal = Decimal("0")) -> Decision:
    problem = _errand_state(errand, conversation_id, now, purpose="buy")
    if problem:
        return problem
    eid = errand["id"]
    if not isinstance(purchase, dict):
        return _block("unreadable-purchase", eid, "a quote created by errand_quote", "nothing readable")

    for check in (_check_recipient, _check_item, _check_merchant, _check_quantity, _check_amount):
        decision = check(errand, purchase)
        if decision:
            return decision

    price = n.money(purchase.get("price"))
    cur = n.currency(purchase.get("currency")) or ""
    if daily_cap is not None:
        total = spent_today + price
        if total > daily_cap:
            return _block("daily-cap", eid, f"at most {n.fmt_money(daily_cap, cur)} spent today",
                          f"{n.fmt_money(spent_today, cur)} already spent + {n.fmt_money(price, cur)} "
                          f"= {n.fmt_money(total, cur)}")

    product = purchase.get("product_name") or purchase.get("product_id") or "product"
    if purchase.get("package"):
        product += f" ({purchase['package']})"
    qty = int(purchase.get("quantity") or 1)
    summary = (f"PAY {n.fmt_money(price, cur)} · {product}" + (f" × {qty}" if qty != 1 else "")
               + f" · to {purchase.get('recipient') or 'your account'}"
               + f" · errand #{eid}, max {n.fmt_money(errand.get('max_amount'), errand.get('currency') or '')}")
    if price > auto_approve_max:
        return Decision(APPROVE, "above-auto-approve", summary,
                        eid, details={"price": str(price), "currency": cur})
    return Decision(ALLOW, "matches-errand", summary, eid, details={"price": str(price), "currency": cur})


def _check_recipient(errand: dict, purchase: dict) -> Optional[Decision]:
    eid = errand["id"]
    expected_type = errand.get("recipient_type") or "none"
    expected = errand.get("recipient") or ""
    actual_type, actual = n.recipient(purchase.get("recipient"))
    shown_actual = purchase.get("recipient") or "no recipient"
    if expected_type not in ("none", "email", "phone"):
        return _block("recipient-mismatch", eid, "a readable recipient on the errand", "unreadable recipient")
    if actual_type == "invalid":
        return _block("recipient-mismatch", eid, expected or "no recipient", f"unreadable '{shown_actual}'")
    if not n.same_recipient(expected_type, expected, actual_type, actual):
        return _block("recipient-mismatch", eid, expected or "no recipient", shown_actual)
    return None


def _check_item(errand: dict, purchase: dict) -> Optional[Decision]:
    eid = errand["id"]
    expected_desc = f"{errand.get('item')}" + (f" ({errand_specs(errand)})" if errand_specs(errand) else "")
    actual_desc = (purchase.get("product_name") or purchase.get("product_id") or "unknown product") + (
        f" ({purchase_specs(purchase)})" if purchase_specs(purchase) else "")

    def mismatch(expected: str, actual: str) -> Decision:
        return _block("item-mismatch", eid, expected, actual)

    if purchase.get("kind") != errand.get("kind"):
        return mismatch(f"a {errand.get('kind')} purchase", f"a {purchase.get('kind') or 'unknown'} purchase")

    want_id = (errand.get("product_id") or "").strip().lower()
    got_id = (purchase.get("product_id") or "").strip().lower()
    if want_id and want_id != got_id:
        return mismatch(f"product '{want_id}'", f"product '{got_id or 'unknown'}'")

    keywords = [k for k in (errand.get("keywords") or []) if isinstance(k, str) and k.strip()]
    if not keywords and not want_id:
        return mismatch("an errand that names the product (keywords or product id)", "an errand with neither")
    haystack = " ".join(str(purchase.get(k) or "") for k in ("product_id", "product_name", "package"))
    missing = n.keyword_missing(keywords, haystack)
    if missing:
        return mismatch(f"{expected_desc} — must mention {', '.join(repr(k) for k in missing)}", actual_desc)

    want_gb = _gb(errand.get("data_gb"))
    if want_gb is not None:
        got_gb = _gb(purchase.get("data_gb"))
        if got_gb is None or got_gb != want_gb:
            return mismatch(n.fmt_gb(want_gb), n.fmt_gb(got_gb) + f" — {actual_desc}")

    if errand.get("duration_days") not in (None, ""):
        got_days = purchase.get("duration_days")
        if got_days in (None, "") or int(got_days) != int(errand["duration_days"]):
            return mismatch(f"{int(errand['duration_days'])} days",
                            (f"{int(got_days)} days" if got_days not in (None, "") else "unknown duration")
                            + f" — {actual_desc}")

    if errand.get("face_value") not in (None, ""):
        want_value = n.money(errand["face_value"])
        got_value = n.money(purchase.get("face_value"))
        want_cur = n.currency(errand.get("face_currency") or "")
        got_cur = n.currency(purchase.get("face_currency") or "")
        if want_value is None or got_value is None or got_value != want_value or (
                want_cur and got_cur != want_cur):
            return mismatch(f"value {n.fmt_money(want_value, want_cur or '')}",
                            f"value {n.fmt_money(got_value, got_cur or '')}")
    return None


def _check_merchant(errand: dict, purchase: dict) -> Optional[Decision]:
    allowed = [m.strip().lower() for m in (errand.get("merchants") or []) if isinstance(m, str) and m.strip()]
    merchant = (purchase.get("merchant") or "").strip().lower()
    if not allowed or merchant not in allowed:
        return _block("merchant-not-allowed", errand["id"], " or ".join(allowed) or "an allowed merchant",
                      merchant or "unknown merchant")
    return None


def _check_quantity(errand: dict, purchase: dict) -> Optional[Decision]:
    want = errand.get("quantity")
    got = purchase.get("quantity")
    if not isinstance(got, int) or isinstance(got, bool) or int(want or 0) != got:
        return _block("quantity-mismatch", errand["id"], str(want), str(got if got is not None else "unknown"))
    return None


def _check_amount(errand: dict, purchase: dict) -> Optional[Decision]:
    eid = errand["id"]
    max_amount = n.money(errand.get("max_amount"))
    want_cur = n.currency(errand.get("currency") or "")
    price = n.money(purchase.get("price"))
    got_cur = n.currency(purchase.get("currency") or "")
    if max_amount is None or want_cur is None:
        return _block("amount-over-max", eid, "a readable maximum on the errand", "unreadable maximum")
    if price is None:
        return _block("amount-unknown", eid, f"a known price of at most {n.fmt_money(max_amount, want_cur)}",
                      "no readable price")
    if got_cur != want_cur:
        return _block("currency-mismatch", eid, f"a price in {want_cur}",
                      f"{n.fmt_money(price, got_cur or '')} (no conversion is done, to be safe)")
    if price > max_amount:
        return _block("amount-over-max", eid, f"at most {n.fmt_money(max_amount, want_cur)}",
                      f"{n.fmt_money(price, got_cur)} for {describe_purchase(purchase)}")
    return None


# ---- other tools that try to reach Bitrefill directly -------------------------------------
BITREFILL_API_MARKERS = ("api.bitrefill.com", "api-bitrefill.com", "bitrefill.com/v2", "bitrefill.com/api")


def check_direct_api(tool_name: str, args: Any) -> Optional[Decision]:
    """Block any non-Errand-Guard tool call that targets Bitrefill's API directly
    (terminal curl, execute_code, web tools...), so the agent cannot pay around the guard."""
    try:
        import json
        text = json.dumps(args, default=str).lower()
    except Exception:
        text = str(args).lower()
    for marker in BITREFILL_API_MARKERS:
        if marker in text:
            return _block("direct-api", None, "payments only through errand_buy",
                          f"tool '{tool_name}' calling the Bitrefill API directly",
                          "Use errand_search_products / errand_quote / errand_buy instead.")
    return None
