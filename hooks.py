"""Hermes hooks: pre_tool_call (the guard), post_tool_call (activity log),
post_approval_response (records whether a real person said yes).
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any, Optional

from . import approvals, guard, settings
from .store import get_store

logger = logging.getLogger(__name__)

OWN_TOOLS = frozenset({
    "errand_create", "errand_confirm", "errand_list", "errand_cancel", "errand_search_products",
    "errand_quote", "errand_buy", "errand_status", "errand_receipt",
})
GUARDED_TOOLS = frozenset({"errand_confirm", "errand_buy"})


def evaluate_buy(store, args: Any, conversation_id: str, now: Optional[float] = None) -> guard.Decision:
    """Load the quote and its errand from our own database (never trusting numbers the
    model typed) and run the purchase rules."""
    now = time.time() if now is None else now
    args = args if isinstance(args, dict) else {}
    quote_id = args.get("quote_id")
    quote = store.get_quote(quote_id) if isinstance(quote_id, str) else None
    if quote is None:
        return guard._block("no-mandate", None, "a quote_id from errand_quote", f"unknown quote {quote_id!r}",
                            "Quote the product with errand_quote first.")
    errand = store.get_errand(quote["errand_id"])
    if quote["status"] != "quoted":
        return guard._block("quote-already-used", quote["errand_id"], "a fresh quote",
                            f"quote {quote_id} is '{quote['status']}'", "Make a new quote with errand_quote.")
    if errand is not None and quote.get("conversation_id") != conversation_id:
        return guard._block("no-mandate", errand["id"], "a quote made in this conversation",
                            "a quote from a different conversation")
    cap = settings.daily_cap()
    spent = Decimal("0")
    if cap is not None and errand is not None:
        from .tools import spent_today
        spent = spent_today(store, errand["currency"])
    return guard.check_purchase(errand, quote["purchase"], conversation_id=conversation_id, now=now,
                                auto_approve_max=settings.auto_approve_max(), daily_cap=cap,
                                spent_today=spent)



def _decide(tool_name: str, args: Any, conversation_id: str) -> Optional[guard.Decision]:
    store = get_store()
    now = time.time()
    if tool_name == "errand_confirm":
        errand = store.get_errand((args or {}).get("errand_id") if isinstance(args, dict) else None)
        return guard.check_confirm(errand, conversation_id=conversation_id, now=now)
    if tool_name == "errand_buy":
        return evaluate_buy(store, args, conversation_id, now)
    if tool_name in OWN_TOOLS:
        return None
    return guard.check_direct_api(tool_name, args)


def on_pre_tool_call(tool_name: str = "", args: Any = None, task_id: str = "", **kwargs: Any) -> Optional[dict]:
    """The guard. Returns a block / approve directive, or None to let the call run."""
    conversation_id = approvals.conversation_key(kwargs)
    try:
        decision = _decide(tool_name, args, conversation_id)
        if decision is None or decision.action == guard.ALLOW:
            if decision is not None:
                _log(tool_name, "allowed", decision, conversation_id)
            return None
        if decision.action == guard.BLOCK:
            _log(tool_name, "blocked", decision, conversation_id)
            return decision.as_hook_result()
        # APPROVE: a one-time ticket, so no earlier "always" answer can cover this payment.
        rule_key = approvals.new_ticket(get_store(), tool_name=tool_name, args=args,
                                        conversation_id=conversation_id, errand_id=decision.errand_id)
        _log(tool_name, "approval_asked", decision, conversation_id)
        return decision.as_hook_result(rule_key=rule_key)
    except Exception as exc:
        # Fail closed: if the guard cannot decide, the call does not run.
        logger.warning("errand-guard pre_tool_call failed for %s: %s", tool_name, exc, exc_info=True)
        if tool_name in OWN_TOOLS and tool_name not in GUARDED_TOOLS:
            return None  # read-only / no-money tools stay usable
        return {"action": "block",
                "message": f"⛔ Blocked by Errand Guard — rule: guard-error. Expected: a clear guard decision. "
                           f"Got: an internal error ({type(exc).__name__}). Blocking to be safe."}


def _log(tool_name: str, outcome: str, decision: guard.Decision, conversation_id: str) -> None:
    try:
        get_store().log(tool_name=tool_name, outcome=outcome, errand_id=decision.errand_id,
                        conversation_id=conversation_id, rule=decision.rule, message=decision.message)
    except Exception as exc:
        logger.warning("errand-guard could not log activity: %s", exc)


def on_post_approval_response(pattern_key: str = "", choice: str = "", **kwargs: Any) -> None:
    try:
        status = approvals.record_answer(get_store(), pattern_key, choice, kwargs.get("decided_by"))
        if status:
            logger.info("errand-guard approval %s -> %s", pattern_key, status)
    except Exception as exc:
        logger.warning("errand-guard could not record approval answer: %s", exc)


def on_post_tool_call(tool_name: str = "", args: Any = None, result: Any = None, status: str = "",
                      error_message: Any = None, **kwargs: Any) -> None:
    """Activity log for errand tools: what happened after the guard (person said no,
    tool error, success). The guard's own blocks were logged when they happened."""
    if tool_name not in GUARDED_TOOLS:
        return
    try:
        store = get_store()
        conversation_id = approvals.conversation_key(kwargs)
        errand_id = None
        if isinstance(args, dict):
            if tool_name == "errand_confirm":
                try:
                    errand_id = int(args.get("errand_id"))
                except (TypeError, ValueError):
                    errand_id = None
            else:
                quote = store.get_quote(args.get("quote_id")) if isinstance(args.get("quote_id"), str) else None
                errand_id = quote["errand_id"] if quote else None
        text = str(error_message or result or "")
        if status == "blocked":
            if "Blocked by Errand Guard" in text and "human-approval-required" not in text:
                return  # already logged by the guard
            store.log(tool_name=tool_name, outcome="denied", errand_id=errand_id, conversation_id=conversation_id,
                      rule="approval-denied", message=text[:500])
        else:
            failed = status == "error" or '"ok": false' in text
            store.log(tool_name=tool_name, outcome="error" if failed else "ok", errand_id=errand_id,
                      conversation_id=conversation_id, message=text[:500])
    except Exception as exc:
        logger.warning("errand-guard post_tool_call logging failed: %s", exc)
