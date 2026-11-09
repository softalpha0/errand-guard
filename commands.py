"""/errands slash command: list, show, receipts, blocked attempts, save codes."""

from __future__ import annotations

import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import guard
from . import normalize as n
from .settings import PLUGIN_NAME
from .shop import _receipt_public
from .store import get_store

USAGE = """Errand Guard — /errands
  /errands                 list recent errands
  /errands show <id>       one errand: mandate, receipts, blocked attempts
  /errands receipts        recent receipts and this month's totals
  /errands blocked         recent blocked payments and why
  /errands code <id>       save that errand's redemption codes to a private file on this computer
  /errands help            this help"""


def _when(epoch: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.gmtime(float(epoch)))
    except (TypeError, ValueError):
        return "?"


def _status(errand: dict) -> str:
    if errand["status"] in ("draft", "active") and time.time() > float(errand["expires_at"]):
        return "expired"
    return errand["status"]


def _list() -> str:
    errands = get_store().list_errands(limit=15)
    if not errands:
        return "No errands yet. Ask Hermes to run one, e.g. “get me a $5 Amazon gift card, max $6”."
    lines = ["Recent errands:"]
    for e in errands:
        lines.append(f"  #{e['id']} · {_status(e):<9} · {guard.describe_errand(e)}")
    lines.append("Details: /errands show <id>")
    return "\n".join(lines)


def _show(arg: str) -> str:
    store = get_store()
    errand = store.get_errand(arg)
    if errand is None:
        return f"No errand #{arg}. Try /errands to list them."
    lines = [f"Errand #{errand['id']} — {_status(errand)}",
             f"  Mandate: {guard.describe_errand(errand)}",
             f"  Created {_when(errand['created_at'])} UTC · expires {_when(errand['expires_at'])} UTC"]
    if errand.get("confirmed_at"):
        lines.append(f"  Confirmed by you {_when(errand['confirmed_at'])} UTC")
    receipts = store.receipts_for_errand(errand["id"])
    for r in receipts:
        pub = _receipt_public(r)
        lines.append(f"  Receipt #{pub['receipt_id']}: {pub['amount']} · {pub['status']} · {pub['item']}")
        if pub["redemption"]:
            lines.append(f"    Codes (masked): {pub['redemption']}  → /errands code {errand['id']} to save them")
    activity = store.activity(errand_id=errand["id"], limit=20)
    if activity:
        lines.append("  Activity:")
        for a in reversed(activity):
            text = a["message"] if a["outcome"] in ("blocked", "denied") else (a["rule"] or a["message"][:90])
            lines.append(f"    {_when(a['created_at'])} {a['tool_name']} → {a['outcome']}: {text}")
    return "\n".join(lines)


def _receipts() -> str:
    store = get_store()
    receipts = store.list_receipts(limit=15)
    if not receipts:
        return "No receipts yet."
    lines = ["Recent receipts:"]
    month = time.strftime("%Y-%m", time.gmtime())
    totals: dict[str, Decimal] = {}
    for r in receipts:
        pub = _receipt_public(r)
        lines.append(f"  #{pub['receipt_id']} · errand #{pub['errand_id']} · {pub['amount']} · {pub['status']} · "
                     f"{pub['paid_at']} · {pub['item']}")
    for r in store.list_receipts(limit=500):
        if time.strftime("%Y-%m", time.gmtime(r["created_at"])) == month and r["status"] not in ("failed",):
            totals[r["currency"]] = totals.get(r["currency"], Decimal("0")) + (n.money(r["amount"]) or Decimal("0"))
    if totals:
        lines.append("This month: " + ", ".join(n.fmt_money(v, k) for k, v in sorted(totals.items())))
    return "\n".join(lines)


def _blocked() -> str:
    rows = get_store().activity(outcome="blocked", limit=15) + get_store().activity(outcome="denied", limit=15)
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    if not rows:
        return "No blocked payments. 🎉"
    lines = ["Blocked or declined attempts (newest first):"]
    for r in rows[:15]:
        who = f"errand #{r['errand_id']}" if r["errand_id"] else "no errand"
        lines.append(f"  {_when(r['created_at'])} · {r['tool_name']} · {who} · {r['outcome']}\n    {r['message']}")
    return "\n".join(lines)


def _codes_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir  # Hermes public helper
    return plugin_data_dir(PLUGIN_NAME) / "codes"


def _code(arg: str) -> str:
    store = get_store()
    errand = store.get_errand(arg)
    if errand is None:
        return f"No errand #{arg}."
    receipts = [r for r in store.receipts_for_errand(errand["id"]) if r["redemption"]]
    if not receipts:
        return (f"No redemption details stored for errand #{errand['id']} yet. Ask Hermes to run errand_status "
                "for it first.")
    folder = _codes_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"errand-{errand['id']}.txt"
    lines = [f"Errand #{errand['id']}: {guard.describe_errand(errand)}", ""]
    for r in receipts:
        lines.append(f"Receipt #{r['id']} · invoice {r['invoice_id']}")
        for order_id, details in r["redemption"].items():
            lines.append(f"  Order {order_id}:")
            for key, value in (details or {}).items():
                lines.append(f"    {key}: {value}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    return (f"Saved the codes for errand #{errand['id']} to:\n  {path}\n"
            "Only your user account can read it. Codes are never shown in chat.")


def handle_errands(raw_args: str = "", **kwargs: Any) -> str:
    try:
        parts = (raw_args or "").strip().split()
        sub = parts[0].lower() if parts else "list"
        arg = parts[1].lstrip("#") if len(parts) > 1 else ""
        if sub in ("list", "ls"):
            return _list()
        if sub == "show" and arg:
            return _show(arg)
        if sub in ("receipts", "receipt"):
            return _receipts()
        if sub in ("blocked", "blocks"):
            return _blocked()
        if sub == "code" and arg:
            return _code(arg)
        if sub.lstrip("#").isdigit():
            return _show(sub.lstrip("#"))
        return USAGE
    except Exception as exc:
        return f"/errands failed: {type(exc).__name__}: {exc}"
