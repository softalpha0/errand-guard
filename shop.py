"""Bitrefill errand tools: errand_search_products, errand_quote, errand_buy, errand_status,
errand_receipt.

Money only moves in errand_buy, and only after:
  1. the pre_tool_call guard allowed it (or a person approved it), and
  2. errand_buy re-ran the same checks itself and, when approval was needed, found a
     real person's yes for this exact call.
"""

from __future__ import annotations

import re
import secrets
import time
from decimal import Decimal
from typing import Any, Optional

from . import approvals, guard, settings
from . import normalize as n
from .bitrefill import BitrefillError, Client, packages
from .hooks import evaluate_buy
from .store import get_store
from .tools import err, ok, safe_handler

_client_factory = Client  # tests replace this with a fake


def set_client_factory(factory) -> None:
    global _client_factory
    _client_factory = factory


def client() -> Client:
    return _client_factory()


# ---- masking ------------------------------------------------------------------------
SECRET_KEYS = ("redemption_info", "pin", "code", "barcode_value", "activation_code", "lpa", "gift_url",
               "link", "url", "password", "serial", "card_number")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_$.:]{7,}")


def mask_code(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 6:
        return "•" * len(text)
    keep = 4 if len(text) >= 14 else 2
    middle = re.sub(r"[A-Za-z0-9]", "•", text[keep:-keep])
    return text[:keep] + middle + text[-keep:]


def mask_text(text: Any) -> str:
    """Mask every code-looking token (8+ chars with a digit) in free text."""
    def repl(match: re.Match) -> str:
        token = match.group(0)
        return mask_code(token) if re.search(r"\d", token) else token
    return _TOKEN_RE.sub(repl, str(text or ""))


def masked_redemption(redemption: dict) -> dict:
    out = {}
    for key, value in (redemption or {}).items():
        if isinstance(value, dict):
            out[key] = masked_redemption(value)
        elif any(s in key.lower() for s in SECRET_KEYS):
            out[key] = mask_text(value) if key.lower() == "redemption_info" else mask_code(value)
        else:
            out[key] = value
    return out


# ---- search ------------------------------------------------------------------------
def _product_summary(product: dict, max_packages: int = 15) -> dict:
    pkgs = packages(product)
    out = {
        "product_id": product.get("id"), "name": product.get("name"),
        "country": product.get("country_code") or product.get("country_name"),
        "currency": product.get("currency"), "in_stock": product.get("in_stock"),
        "recipient_type": product.get("recipient_type"),
        "packages": [{"value": p.get("value"), "price": p.get("price"), "package_id": p.get("id")}
                     for p in pkgs[:max_packages]],
    }
    if len(pkgs) > max_packages:
        out["more_packages"] = len(pkgs) - max_packages
    if isinstance(product.get("range"), dict):
        out["range"] = product["range"]
    return out


@safe_handler
def errand_search_products(args: dict, **kwargs: Any) -> str:
    try:
        api = client()
        test = bool(args.get("include_test_products"))
        kind = str(args.get("kind") or "")
        if args.get("product_id"):
            product = api.product(str(args["product_id"]), esim=kind == "esim")
            return ok(product=_product_summary(product, max_packages=40),
                      note="Package prices are in your Bitrefill account currency.")
        if args.get("phone_number"):
            found = api.check_phone(str(args["phone_number"]))
            data = found.get("data")
            items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
            return ok(operator_found=found.get("operator_found"),
                      products=[_product_summary(p) for p in items][:10])
        if kind == "esim":
            items = api.esim_products(str(args.get("country") or ""), include_test_products=test)
            return ok(products=[_product_summary(p) for p in items][:20])
        query = str(args.get("query") or "").strip()
        if not query:
            return err("Give a query, a product_id, a phone_number, or kind='esim' with a country.")
        return ok(products=[_product_summary(p) for p in api.search(query, include_test_products=test)][:10])
    except BitrefillError as exc:
        return err(str(exc))


# ---- quote ------------------------------------------------------------------------
def _same_value(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    na, nb = n.money(a), n.money(b)
    if na is not None and nb is not None:
        return na == nb
    return n.words(str(a)) == n.words(str(b))


def _derive_kind(errand_kind: str, product: dict, esim: bool) -> str:
    """Our own reading of what the product is, from Bitrefill's data (not the model's words)."""
    if esim:
        return "esim"
    rtype = str(product.get("recipient_type") or "").lower()
    if "phone" in rtype:
        return errand_kind if errand_kind in ("airtime", "data") else "airtime"
    return "gift_card"


@safe_handler
def errand_quote(args: dict, **kwargs: Any) -> str:
    store = get_store()
    conversation = approvals.conversation_key(kwargs)
    errand = store.get_errand(args.get("errand_id"))
    problem = guard._errand_state(errand, conversation, time.time(), purpose="buy")
    if problem:
        return err(problem.message, rule=problem.rule)

    product_id = str(args.get("product_id") or "").strip()
    if not product_id:
        return err("product_id is required (from errand_search_products).")
    quantity = args.get("quantity", 1)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 10:
        return err("quantity must be a whole number from 1 to 10.")

    rtype, recipient = n.recipient(args.get("recipient") or errand.get("recipient") or "")
    if rtype == "invalid":
        return err(f"recipient {args.get('recipient')!r} is not a valid email or phone number.")

    esim = errand["kind"] == "esim"
    try:
        api = client()
        product = api.product(product_id, esim=esim)
    except BitrefillError as exc:
        return err(str(exc))
    if not product:
        return err(f"Bitrefill has no product {product_id!r}.")

    wanted = args.get("package_value")
    package = None
    for pkg in packages(product):
        if (args.get("package_id") and str(pkg.get("id")) == str(args["package_id"])) or _same_value(pkg.get("value"), wanted):
            package = pkg
            break
    if package is None and not isinstance(product.get("range"), dict):
        listed = ", ".join(str(p.get("value")) for p in packages(product)[:20])
        return err(f"Package {wanted!r} is not offered for {product_id}. Offered: {listed or 'none'}.")
    value = package.get("value") if package else wanted

    line: dict[str, Any] = {"product_id": product_id, "quantity": quantity}
    number = n.money(value)
    line["value"] = (float(number) if number is not None and not esim else value)
    invoice_email, send_email = "", False
    kind = _derive_kind(errand["kind"], product, esim)
    if rtype == "phone":
        line["phone_number"] = recipient
    elif rtype == "email" and esim:
        invoice_email, send_email = recipient, True
    elif rtype == "email":
        line["gift"] = {"recipient_email": recipient,
                        "recipient_name": str(args.get("recipient_name") or recipient.split("@")[0])[:80],
                        "sender_name": str(args.get("sender_name") or "A friend")[:80]}
        if args.get("gift_message"):
            line["gift"]["message"] = str(args["gift_message"])[:500]

    try:
        invoice = api.create_invoice([line], esim=esim, email=invoice_email, send_email=send_email)
    except BitrefillError as exc:
        return err(str(exc))
    invoice_id = str(invoice.get("id") or "")
    if not invoice_id:
        return err("Bitrefill did not return an invoice id.")

    payment = invoice.get("payment") or {}
    order_product = {}
    orders = invoice.get("orders") or []
    if orders and isinstance(orders[0], dict):
        order_product = orders[0].get("product") or {}
    name = order_product.get("name") or product.get("name") or product_id
    package_text = str(order_product.get("value") or value or "")
    spec_text = package_text if esim or kind == "data" else ""
    gb = n.data_gb(spec_text) if spec_text else None
    if gb is None and kind in ("esim", "data"):
        gb = n.data_gb(name)
    days = n.duration_days(spec_text) if spec_text else None
    if days is None and kind in ("esim", "data"):
        days = n.duration_days(name)
    purchase = {
        "kind": kind, "merchant": "bitrefill", "product_id": order_product.get("id") or product_id,
        "product_name": name, "package": package_text,
        "data_gb": (str(gb) if gb is not None else None), "duration_days": days,
        "face_value": (str(n.money(package_text)) if kind not in ("esim", "data") and n.money(package_text) is not None
                       else None),
        "face_currency": order_product.get("currency") or product.get("currency") or "",
        "quantity": quantity, "recipient": recipient or "",
        "price": (str(n.money(payment.get("price"))) if n.money(payment.get("price")) is not None else None),
        "currency": payment.get("currency") or "", "invoice_id": invoice_id, "esim": esim,
    }
    quote_id = "q_" + secrets.token_hex(6)
    store.add_quote(quote_id, errand["id"], conversation, purchase, invoice_id=invoice_id,
                    raw={"payment_status": payment.get("status"), "invoice_status": invoice.get("status")})
    preview = evaluate_buy(store, {"quote_id": quote_id}, conversation)
    store.log(tool_name="errand_quote", outcome="ok", errand_id=errand["id"], conversation_id=conversation,
              message=f"{quote_id}: {guard.describe_purchase(purchase)} -> guard would {preview.action}")
    return ok(quote_id=quote_id, quote=guard.describe_purchase(purchase),
              guard_preview={"decision": preview.action, "rule": preview.rule, "message": preview.message},
              next_step=("Call errand_buy with this quote_id." if preview.action != guard.BLOCK else
                         "This quote does not match the errand and would be blocked. Pick a matching package, "
                         "or ask the user."))


# ---- buy ----------------------------------------------------------------------------
@safe_handler
def errand_buy(args: dict, **kwargs: Any) -> str:
    store = get_store()
    conversation = approvals.conversation_key(kwargs)
    # Re-check everything here too: if the hook were bypassed, the tool still refuses.
    decision = evaluate_buy(store, args, conversation)
    if decision.blocked:
        store.log(tool_name="errand_buy", outcome="blocked", errand_id=decision.errand_id,
                  conversation_id=conversation, rule=decision.rule, message=decision.message)
        return err(decision.message, rule=decision.rule)
    if decision.action == guard.APPROVE and approvals.take_human_approval(
            store, tool_name="errand_buy", args=args, conversation_id=conversation) is None:
        store.log(tool_name="errand_buy", outcome="blocked", errand_id=decision.errand_id,
                  conversation_id=conversation, rule="human-approval-required", message=approvals.NO_HUMAN_MESSAGE)
        return err(approvals.NO_HUMAN_MESSAGE, rule="human-approval-required")

    quote = store.get_quote(args["quote_id"])
    errand_id = quote["errand_id"]
    purchase = quote["purchase"]
    # Compare-and-set: only one payment per errand can start.
    if not store.set_errand_status(errand_id, "buying", expect=["active"]):
        return err(f"Errand #{errand_id} is no longer active; another payment may be running.", rule="in-progress")
    if not store.set_quote_status(quote["id"], "paying", expect=["quoted"]):
        store.set_errand_status(errand_id, "active", expect=["buying"])
        return err(f"Quote {quote['id']} was already used.", rule="quote-already-used")

    receipt_base = dict(errand_id=errand_id, quote_id=quote["id"], invoice_id=quote["invoice_id"],
                        amount=purchase.get("price") or "", currency=purchase.get("currency") or "",
                        item=guard.describe_purchase(purchase), recipient=purchase.get("recipient") or "")
    try:
        paid = client().pay_invoice(quote["invoice_id"], esim=bool(purchase.get("esim")))
    except BitrefillError as exc:
        if exc.uncertain:
            store.set_quote_status(quote["id"], "unknown")
            store.set_errand_status(errand_id, "payment_unknown")
            store.add_receipt(status="unknown", order_ids=[], redemption={}, **receipt_base)
            return err(f"The payment result is unknown ({exc}). Do NOT retry: run errand_status for errand "
                       f"#{errand_id} to check whether it went through.", rule="payment-unknown")
        store.set_quote_status(quote["id"], "failed")
        store.set_errand_status(errand_id, "active", expect=["buying"])
        return err(f"Bitrefill did not take the payment: {exc}. Nothing was charged.")

    order_ids = [str(o.get("id")) for o in (paid.get("orders") or []) if isinstance(o, dict) and o.get("id")]
    status = str((paid.get("payment") or {}).get("status") or paid.get("status") or "paid")
    receipt = store.add_receipt(status=status, order_ids=order_ids, redemption={}, **receipt_base)
    store.set_quote_status(quote["id"], "paid")
    store.set_errand_status(errand_id, "fulfilled", fulfilled_at=time.time())
    return ok(message=f"✅ Paid. Receipt #{receipt['id']} for errand #{errand_id}: {receipt['item']}.",
              receipt=_receipt_public(receipt),
              next_step=f"Call errand_status for errand #{errand_id} to fetch delivery / redemption details.")


# ---- status / receipt ---------------------------------------------------------------
def _receipt_public(receipt: dict) -> dict:
    return {
        "receipt_id": receipt["id"], "errand_id": receipt["errand_id"], "status": receipt["status"],
        "amount": n.fmt_money(receipt["amount"], receipt["currency"]) if receipt["amount"] else "unknown",
        "item": receipt["item"], "recipient": receipt["recipient"] or None,
        "invoice_id": receipt["invoice_id"], "order_ids": receipt["order_ids"],
        "redemption": masked_redemption(receipt["redemption"]),
        "paid_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(receipt["created_at"])),
    }


def _redemption_from_order(order: dict) -> dict:
    keep = {}
    for key in ("redemption_info", "pin", "code", "barcode_value", "link", "status", "delivered_time"):
        if order.get(key) not in (None, ""):
            keep[key] = order[key]
    gift = order.get("gift_info")
    if isinstance(gift, dict) and gift.get("gift_url"):
        keep["gift_url"] = gift["gift_url"]
    return keep


@safe_handler
def errand_status(args: dict, **kwargs: Any) -> str:
    store = get_store()
    errand = store.get_errand(args.get("errand_id"))
    if errand is None:
        return err(f"No errand #{args.get('errand_id')}.")
    receipts = store.receipts_for_errand(errand["id"])
    if not receipts:
        return ok(errand_status=errand["status"], message="No payment has been made for this errand.")
    receipt = receipts[-1]
    quote = store.get_quote(receipt["quote_id"])
    esim = bool((quote or {}).get("purchase", {}).get("esim"))
    try:
        api = client()
        invoice = api.invoice(receipt["invoice_id"], esim=esim)
        orders = [o for o in invoice.get("orders") or [] if isinstance(o, dict)]
        redemption: dict[str, Any] = {}
        for order in orders:
            if not order.get("id"):
                continue
            try:
                full = api.order(str(order["id"]))
            except BitrefillError:
                full = order  # keep what the invoice already told us
            redemption[str(order["id"])] = _redemption_from_order(full)
    except BitrefillError as exc:
        return err(f"Could not refresh from Bitrefill: {exc}")
    pay_status = str((invoice.get("payment") or {}).get("status") or "")
    status = pay_status or str(invoice.get("status") or receipt["status"])
    store.update_receipt(receipt["id"], status=status, order_ids=[str(o.get("id")) for o in orders if o.get("id")],
                         redemption=redemption)
    if errand["status"] == "payment_unknown":
        if pay_status.lower() in ("paid", "complete", "completed", "confirmed"):
            store.set_errand_status(errand["id"], "fulfilled", fulfilled_at=time.time())
            if quote:
                store.set_quote_status(quote["id"], "paid")
        elif pay_status.lower() in ("unpaid", "expired", "failed", "cancelled"):
            store.set_errand_status(errand["id"], "active", expect=["payment_unknown"])
            if quote:
                store.set_quote_status(quote["id"], "failed")
    return ok(errand_status=store.get_errand(errand["id"])["status"],
              receipt=_receipt_public(store.get_receipt(receipt["id"])),
              note="Redemption codes are stored on this computer only and masked here. "
                   "The user can run /errands code <errand_id> to save them to a private file.")


@safe_handler
def errand_receipt(args: dict, **kwargs: Any) -> str:
    store = get_store()
    errand = store.get_errand(args.get("errand_id"))
    if errand is None:
        return err(f"No errand #{args.get('errand_id')}.")
    from .tools import errand_public
    blocks = store.activity(errand_id=errand["id"], outcome="blocked", limit=10)
    return ok(errand=errand_public(errand),
              receipts=[_receipt_public(r) for r in store.receipts_for_errand(errand["id"])],
              blocked_attempts=[{"rule": b["rule"], "message": b["message"],
                                 "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(b["created_at"]))}
                                for b in blocks])
