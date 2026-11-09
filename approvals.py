"""Proof that a real person approved a specific call.

Hermes's approval gate auto-approves in YOLO mode, with approvals off, or when its
"smart" AI approval decides. That is not good enough for spending money. So:

1. When the guard asks Hermes for approval, it creates a one-time *ticket* and uses it
   as the approval rule key ("errand-guard:<ticket>"), so a previous "always" answer can
   never cover a new payment.
2. Hermes reports the person's answer through the ``post_approval_response`` hook; we
   record it on the ticket only when a person said once / session / always.
3. The tool itself (errand_confirm, errand_buy) consumes a human-approved ticket for that
   exact call before doing anything. No ticket -> no action (fail closed).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any, Optional

RULE_PREFIX = "errand-guard:"
PATTERN_PREFIX = "plugin_rule:" + RULE_PREFIX
HUMAN_YES = frozenset({"once", "session", "always"})
MAX_AGE_SECONDS = 15 * 60


def conversation_key(kwargs: dict) -> str:
    """The id errands are scoped to. Hooks and tool handlers both receive these keywords,
    so both sides compute the same key. Empty means 'no conversation' (fails closed)."""
    for key in ("conversation_id", "session_id"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def fingerprint(tool_name: str, args: Any, conversation_id: str) -> str:
    try:
        canonical = json.dumps(args if isinstance(args, dict) else {}, sort_keys=True, default=str)
    except Exception:
        canonical = repr(args)
    raw = f"{tool_name}\n{conversation_id}\n{canonical}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_ticket(store, *, tool_name: str, args: Any, conversation_id: str, errand_id: Optional[int]) -> str:
    ticket = secrets.token_hex(8)
    store.add_approval(ticket, fingerprint(tool_name, args, conversation_id), tool_name, errand_id,
                       conversation_id)
    return RULE_PREFIX + ticket


def record_answer(store, pattern_key: Any, choice: Any, decided_by: Any = None) -> Optional[str]:
    """Record an approval answer. Returns the ticket status written, or None if not ours."""
    if not isinstance(pattern_key, str) or not pattern_key.startswith(PATTERN_PREFIX):
        return None
    ticket = pattern_key[len(PATTERN_PREFIX):]
    human = isinstance(choice, str) and choice in HUMAN_YES and not decided_by
    status = "human_approved" if human else "denied"
    store.answer_approval(ticket, status, str(choice))
    return status


def take_human_approval(store, *, tool_name: str, args: Any, conversation_id: str) -> Optional[dict]:
    return store.consume_approval(fingerprint(tool_name, args, conversation_id), MAX_AGE_SECONDS)


NO_HUMAN_MESSAGE = (
    "⛔ Blocked by Errand Guard — rule: human-approval-required. Expected: a person approving this "
    "exact action in Hermes's approval prompt. Got: no human answer (YOLO mode, approvals off, smart "
    "auto-approval, an unattended run, or the approval was denied). Errand Guard never spends or "
    "confirms an errand without a real person saying yes.")
