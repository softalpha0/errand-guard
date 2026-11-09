"""Mandate tools: errand_create, errand_confirm, errand_list, errand_cancel.

Handlers follow Hermes's rules: take (args, **kwargs), always return a JSON string,
never raise.
"""

from __future__ import annotations

import functools
import json
import time
from decimal import Decimal
from typing import Any, Callable

from . import approvals, guard, settings
from . import normalize as n
from .store import get_store


def ok(**payload: Any) -> str:
    return json.dumps({"ok": True, **payload}, default=str)


def err(message: str, **payload: Any) -> str:
    return json.dumps({"ok": False, "error": message, **payload}, default=str)


def safe_handler(fn: Callable[..., str]) -> Callable[..., str]:
    @functools.wraps(fn)
    def wrapper(args: Any, **kwargs: Any) -> str:
        try:
            return fn(args if isinstance(args, dict) else {}, **kwargs)
        except Exception as exc:  # never raise into Hermes
            return err(f"{fn.__name__} failed: {type(exc).__name__}: {exc}")
    return wrapper


def errand_public(errand: dict) -> dict:
    """Errand fields safe to show the model and the user."""
    expires = float(errand["expires_at"])
    status = errand["status"]
    if status in ("draft", "active") and time.time() > expires:
        status = "expired"
    return {
        "errand_id": errand["id"], "status": status, "kind": errand["kind"], "item": errand["item"],
        "summary": guard.describe_errand(errand),
        "recipient": errand.get("recipient") or None,
        "max_amount": n.fmt_money(errand["max_amount"]), "currency": errand["currency"],
        "quantity": errand["quantity"], "merchants": errand.get("merchants") or [],
        "product_keywords": errand.get("keywords") or [], "product_id": errand.get("product_id") or None,
        "expires_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(expires)),
    }


# ---- errand_create ------------------------------------------------------------------
def _parse_create(args: dict) -> tuple[dict | None, str | None]:
    kind = str(args.get("kind") or "").strip().lower().replace(" ", "_").replace("-", "_")
    if kind not in guard.KINDS:
        return None, f"kind must be one of {', '.join(guard.KINDS)} (got {args.get('kind')!r})."
    item = str(args.get("item") or "").strip()
    if not item or len(item) > 200:
        return None, "item is required: a short plain description, e.g. 'Japan eSIM 5 GB / 7 days'."

    max_amount = n.money(args.get("max_amount"))
    if max_amount is None or max_amount <= 0:
        return None, "max_amount must be a positive number: the most the user allows for this errand."
    currency = n.currency(args.get("currency") or "")
    if currency is None:
        return None, "currency must be a 3-letter code like USD or EUR."

    quantity = args.get("quantity", 1)
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or int(quantity) != quantity \
            or not 1 <= int(quantity) <= 10:
        return None, "quantity must be a whole number from 1 to 10."
    quantity = int(quantity)

    rtype, recipient = n.recipient(args.get("recipient"))
    if rtype == "invalid":
        return None, f"recipient {args.get('recipient')!r} is not a valid email or phone number."
    allowed = guard.RECIPIENT_TYPES[kind]
    if rtype not in allowed:
        need = " or ".join("no recipient" if a == "none" else f"a {a} number" if a == "phone" else f"an {a}"
                           for a in allowed)
        return None, f"a {kind} errand needs {need} as recipient (got {args.get('recipient')!r})."

    keywords = args.get("product_keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        return None, "product_keywords must be a list of words, e.g. ['japan'] or ['amazon', 'us']."
    keywords = [k.strip() for k in keywords if k.strip()][:8]
    product_id = str(args.get("product_id") or "").strip()
    if not keywords and not product_id:
        return None, ("give product_keywords (words the product name must contain, e.g. ['japan'] for a Japan "
                      "eSIM, ['amazon'] for an Amazon gift card) or an exact product_id.")

    data_gb = args.get("data_gb")
    if data_gb not in (None, ""):
        if str(data_gb).strip().lower() == "unlimited":
            data_gb = "Infinity"
        else:
            value = n.money(data_gb)
            if value is None or value <= 0:
                return None, "data_gb must be a positive number of GB or 'unlimited'."
            data_gb = str(value)
    else:
        data_gb = None
    days = args.get("duration_days")
    if days not in (None, ""):
        if isinstance(days, bool) or not isinstance(days, (int, float)) or int(days) != days or days <= 0:
            return None, "duration_days must be a whole number of days."
        days = int(days)
    else:
        days = None
    if kind == "esim" and data_gb is None and days is None:
        return None, "an esim errand needs data_gb and/or duration_days so the right plan can be checked."

    face_value = args.get("face_value")
    face_currency = ""
    if face_value not in (None, ""):
        value = n.money(face_value)
        if value is None or value <= 0:
            return None, "face_value must be a positive number (e.g. 25 for a $25 gift card)."
        face_value = str(value)
        face_currency = n.currency(args.get("face_currency") or currency) or ""
        if not face_currency:
            return None, "face_currency must be a 3-letter code."
    else:
        face_value = None

    merchants = args.get("merchants") or ["bitrefill"]
    if isinstance(merchants, str):
        merchants = [merchants]
    merchants = [str(m).strip().lower() for m in merchants if str(m).strip()]
    if not merchants:
        merchants = ["bitrefill"]

    hours = args.get("expires_in_hours")
    if hours in (None, ""):
        hours = settings.default_expiry_hours()
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        return None, "expires_in_hours must be a number."
    if not 0 < hours <= 24 * 30:
        return None, "expires_in_hours must be between 0 and 720."

    now = time.time()
    return {
        "kind": kind, "item": item, "product_id": product_id, "keywords": keywords,
        "data_gb": data_gb, "duration_days": days, "face_value": face_value, "face_currency": face_currency,
        "recipient": recipient or "", "recipient_type": rtype, "merchants": merchants,
        "max_amount": str(max_amount), "currency": currency, "quantity": quantity,
        "created_at": now, "expires_at": now + hours * 3600, "status": "draft",
    }, None


@safe_handler
def errand_create(args: dict, **kwargs: Any) -> str:
    conversation = approvals.conversation_key(kwargs)
    if not conversation:
        return err("No conversation id is available, so an errand cannot be scoped safely. "
                   "Run this from a normal Hermes chat.")
    fields, problem = _parse_create(args)
    if problem:
        return err(problem)
    fields["conversation_id"] = conversation
    fields["session_id"] = str(kwargs.get("session_id") or "")
    errand = get_store().create_errand(fields)
    get_store().log(tool_name="errand_create", outcome="ok", errand_id=errand["id"],
                    conversation_id=conversation, message=guard.describe_errand(errand))
    return ok(errand=errand_public(errand),
              next_step=("Show the user this summary in plain words, then call errand_confirm with this "
                         "errand_id. Hermes will ask the user to approve. Nothing can be bought until they do."))


# ---- errand_confirm -----------------------------------------------------------------
@safe_handler
def errand_confirm(args: dict, **kwargs: Any) -> str:
    store = get_store()
    conversation = approvals.conversation_key(kwargs)
    errand = store.get_errand(args.get("errand_id"))
    decision = guard.check_confirm(errand, conversation_id=conversation, now=time.time())
    if decision.blocked:
        return err(decision.message, rule=decision.rule)
    # Defence in depth: the pre_tool_call hook asked a person; make sure a person said yes.
    if approvals.take_human_approval(store, tool_name="errand_confirm", args=args,
                                     conversation_id=conversation) is None:
        store.log(tool_name="errand_confirm", outcome="blocked", errand_id=errand["id"],
                  conversation_id=conversation, rule="human-approval-required",
                  message=approvals.NO_HUMAN_MESSAGE)
        return err(approvals.NO_HUMAN_MESSAGE, rule="human-approval-required")
    if not store.set_errand_status(errand["id"], "active", expect=["draft"], confirmed_at=time.time()):
        return err(f"Errand #{errand['id']} changed while confirming; check errand_list.")
    errand = store.get_errand(errand["id"])
    return ok(errand=errand_public(errand),
              message=f"Errand #{errand['id']} is confirmed. You may now search, quote and buy exactly this.")


# ---- errand_list --------------------------------------------------------------------
@safe_handler
def errand_list(args: dict, **kwargs: Any) -> str:
    conversation = approvals.conversation_key(kwargs)
    scope = str(args.get("scope") or "this_conversation")
    store = get_store()
    if scope == "all":
        errands = store.list_errands(limit=25)
    else:
        errands = store.list_errands(conversation_id=conversation, limit=25)
    return ok(errands=[errand_public(e) for e in errands], count=len(errands))


# ---- errand_cancel ------------------------------------------------------------------
@safe_handler
def errand_cancel(args: dict, **kwargs: Any) -> str:
    store = get_store()
    conversation = approvals.conversation_key(kwargs)
    errand = store.get_errand(args.get("errand_id"))
    if errand is None or errand["conversation_id"] != conversation:
        return err(f"No errand #{args.get('errand_id')} in this conversation.")
    if not store.set_errand_status(errand["id"], "cancelled", expect=["draft", "active"],
                                   cancelled_at=time.time()):
        return err(f"Errand #{errand['id']} is '{errand['status']}' and cannot be cancelled.")
    store.log(tool_name="errand_cancel", outcome="ok", errand_id=errand["id"], conversation_id=conversation,
              message="cancelled")
    return ok(message=f"Errand #{errand['id']} cancelled. Nothing more can be bought for it.")


def spent_today(store, currency: str) -> Decimal:
    start = time.time() - (time.time() % 86400)  # midnight UTC
    total = Decimal("0")
    for row in store.spent_since(start, currency):
        total += n.money(row.get("amount")) or Decimal("0")
    return total
