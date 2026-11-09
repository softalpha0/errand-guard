"""End-to-end flows through the real hooks and tool handlers (Bitrefill faked).

`run_tool` mimics what Hermes does: pre_tool_call hook -> (approval prompt) -> handler.
"""

import json
import os
import stat

import pytest

from errand_guard import approvals, commands, hooks, settings, shop, tools
from conftest import CONV, approve_last_ticket, make_errand

KW = {"conversation_id": CONV, "session_id": "sess-1", "task_id": "t1"}
HANDLERS = {"errand_create": tools.errand_create, "errand_confirm": tools.errand_confirm,
            "errand_list": tools.errand_list, "errand_cancel": tools.errand_cancel,
            "errand_quote": shop.errand_quote, "errand_buy": shop.errand_buy,
            "errand_status": shop.errand_status, "errand_receipt": shop.errand_receipt,
            "errand_search_products": shop.errand_search_products}


def run_tool(store, name, args, *, answer="once", decided_by=None, kw=KW):
    """Returns (hook_directive, parsed_result or None if blocked)."""
    directive = hooks.on_pre_tool_call(tool_name=name, args=args, **kw)
    if directive and directive["action"] == "block":
        return directive, None
    if directive and directive["action"] == "approve":
        if answer is not None:  # None = YOLO / approvals off: Hermes approves silently, no hook fires
            approve_last_ticket(store, choice=answer, decided_by=decided_by)
            if answer not in approvals.HUMAN_YES or decided_by:
                return directive, None  # Hermes itself blocks on deny / timeout
    return directive, json.loads(HANDLERS[name](args, **kw))


def create(store, **overrides):
    args = dict(kind="esim", item="Japan eSIM 5 GB / 7 days", product_keywords=["japan"], data_gb=5,
                duration_days=7, recipient="me@example.com", max_amount=15, currency="USD")
    args.update(overrides)
    _, result = run_tool(store, "errand_create", args)
    return result


# ---- errand_create ----------------------------------------------------------------------
def test_create_makes_a_draft(store):
    result = create(store)
    assert result["ok"] and result["errand"]["status"] == "draft"
    assert "Japan eSIM" in result["errand"]["summary"] and "15.00 USD" in result["errand"]["summary"]


@pytest.mark.parametrize("overrides,fragment", [
    ({"kind": "car"}, "kind must be"),
    ({"max_amount": 0}, "max_amount"),
    ({"max_amount": "lots"}, "max_amount"),
    ({"currency": "dollars"}, "currency"),
    ({"quantity": 0}, "quantity"),
    ({"recipient": "nobody"}, "not a valid"),
    ({"kind": "airtime", "recipient": "me@example.com"}, "phone"),
    ({"product_keywords": []}, "product_keywords"),
    ({"data_gb": None, "duration_days": None}, "data_gb"),
])
def test_create_rejects_bad_input(store, overrides, fragment):
    result = create(store, **overrides)
    assert not result["ok"] and fragment in result["error"]


def test_create_without_conversation_is_refused(store):
    result = json.loads(tools.errand_create({"kind": "esim"}, conversation_id="", session_id=""))
    assert not result["ok"] and "conversation" in result["error"]


# ---- errand_confirm and the real-human rule ------------------------------------------------
def test_confirm_with_a_person_saying_yes_activates(store):
    errand_id = create(store)["errand"]["errand_id"]
    directive, result = run_tool(store, "errand_confirm", {"errand_id": errand_id}, answer="once")
    assert directive["action"] == "approve" and directive["rule_key"].startswith("errand-guard:")
    assert result["ok"] and result["errand"]["status"] == "active"


@pytest.mark.parametrize("answer,decided_by", [
    (None, None),                   # YOLO / approvals off: no human answer recorded
    ("smart_approve", "aux_llm"),   # Hermes's AI approver
    ("deny", None),
    ("timeout", None),
])
def test_confirm_without_a_human_yes_never_activates(store, answer, decided_by):
    errand_id = create(store)["errand"]["errand_id"]
    _, result = run_tool(store, "errand_confirm", {"errand_id": errand_id}, answer=answer, decided_by=decided_by)
    if result is not None:
        assert not result["ok"] and result["rule"] == "human-approval-required"
    assert store.get_errand(errand_id)["status"] == "draft"


def test_an_old_always_answer_does_not_cover_a_new_prompt(store):
    first = create(store)["errand"]["errand_id"]
    second = create(store)["errand"]["errand_id"]
    d1, _ = run_tool(store, "errand_confirm", {"errand_id": first}, answer="always")
    d2 = hooks.on_pre_tool_call(tool_name="errand_confirm", args={"errand_id": second}, **KW)
    assert d1["rule_key"] != d2["rule_key"]  # new one-time ticket: Hermes must ask again
    result = json.loads(tools.errand_confirm({"errand_id": second}, **KW))
    assert not result["ok"] and result["rule"] == "human-approval-required"


def test_handler_refuses_when_hook_was_skipped(store):
    errand_id = create(store)["errand"]["errand_id"]
    result = json.loads(tools.errand_confirm({"errand_id": errand_id}, **KW))
    assert not result["ok"] and result["rule"] == "human-approval-required"


def test_cancel_then_buy_is_blocked(store, fake_bitrefill):
    errand_id = create(store)["errand"]["errand_id"]
    run_tool(store, "errand_confirm", {"errand_id": errand_id})
    _, quote = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "bitrefill-esim-japan",
                                                "package_value": "5GB, 7 Days"})
    _, result = run_tool(store, "errand_cancel", {"errand_id": errand_id})
    assert result["ok"]
    directive, _ = run_tool(store, "errand_buy", {"quote_id": quote["quote_id"]})
    assert directive["action"] == "block" and "errand-cancelled" in directive["message"]


# ---- the demo: Japan eSIM -----------------------------------------------------------------
def confirmed_esim(store):
    errand_id = create(store)["errand"]["errand_id"]
    run_tool(store, "errand_confirm", {"errand_id": errand_id})
    return errand_id


def test_demo_wrong_plan_blocked_then_right_plan_bought(store, fake_bitrefill):
    errand_id = confirmed_esim(store)
    # The agent "helpfully" picks the 20 GB / $35 plan.
    _, big = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "bitrefill-esim-japan",
                                              "package_value": "20GB, 30 Days"})
    assert big["guard_preview"]["decision"] == "block"
    directive, _ = run_tool(store, "errand_buy", {"quote_id": big["quote_id"]})
    assert directive["action"] == "block"
    assert "item-mismatch" in directive["message"] and "5 GB" in directive["message"] and "20 GB" in directive["message"]
    assert fake_bitrefill.paid == []

    # Corrected: the 5 GB / 7 days plan.
    _, small = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "bitrefill-esim-japan",
                                                "package_value": "5GB, 7 Days"})
    assert small["guard_preview"]["decision"] == "approve"
    directive, result = run_tool(store, "errand_buy", {"quote_id": small["quote_id"]})
    assert directive["action"] == "approve" and result["ok"], result
    assert fake_bitrefill.paid == ["inv-2"]
    assert fake_bitrefill.created[-1]["email"] == "me@example.com"
    assert store.get_errand(errand_id)["status"] == "fulfilled"

    # It cannot be bought twice.
    _, again = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "bitrefill-esim-japan",
                                                "package_value": "5GB, 7 Days"})
    assert not again["ok"] and again["rule"] == "already-fulfilled"


def test_wrong_recipient_in_quote_is_blocked_at_payment(store, fake_bitrefill):
    errand_id = confirmed_esim(store)
    _, quote = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "bitrefill-esim-japan",
                                                "package_value": "5GB, 7 Days", "recipient": "attacker@evil.com"})
    directive, _ = run_tool(store, "errand_buy", {"quote_id": quote["quote_id"]})
    assert directive["action"] == "block" and "recipient-mismatch" in directive["message"]
    assert "me@example.com" in directive["message"] and "attacker@evil.com" in directive["message"]
    assert fake_bitrefill.paid == []


def test_buy_without_quote_is_blocked(store, fake_bitrefill):
    directive, _ = run_tool(store, "errand_buy", {"quote_id": "q_made_up"})
    assert directive["action"] == "block" and "no-mandate" in directive["message"]


def test_buy_needs_a_human_even_in_yolo_mode(store, fake_bitrefill):
    errand_id = confirmed_esim(store)
    _, quote = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "bitrefill-esim-japan",
                                                "package_value": "5GB, 7 Days"})
    _, result = run_tool(store, "errand_buy", {"quote_id": quote["quote_id"]}, answer=None)
    assert not result["ok"] and result["rule"] == "human-approval-required"
    assert fake_bitrefill.paid == [] and store.get_errand(errand_id)["status"] == "active"


def test_auto_approve_limit_skips_prompt_for_small_matching_payment(store, fake_bitrefill):
    settings.set_test_values({"ERRAND_AUTO_APPROVE_MAX": "12"})
    errand_id = confirmed_esim(store)
    _, quote = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "bitrefill-esim-japan",
                                                "package_value": "5GB, 7 Days"})
    directive, result = run_tool(store, "errand_buy", {"quote_id": quote["quote_id"]})
    assert directive is None and result["ok"]


def test_buy_from_another_conversation_is_blocked(store, fake_bitrefill):
    errand_id = confirmed_esim(store)
    _, quote = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "bitrefill-esim-japan",
                                                "package_value": "5GB, 7 Days"})
    other = {"conversation_id": "other-chat", "session_id": "s2"}
    directive, _ = run_tool(store, "errand_buy", {"quote_id": quote["quote_id"]}, kw=other)
    assert directive["action"] == "block" and "no-mandate" in directive["message"]


def test_uncertain_payment_blocks_retry_until_status_checked(store, fake_bitrefill):
    from errand_guard.bitrefill import BitrefillError
    errand_id = confirmed_esim(store)
    _, quote = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "bitrefill-esim-japan",
                                                "package_value": "5GB, 7 Days"})
    fake_bitrefill.pay_error = BitrefillError("timed out", uncertain=True)
    _, result = run_tool(store, "errand_buy", {"quote_id": quote["quote_id"]})
    assert not result["ok"] and result["rule"] == "payment-unknown"
    assert store.get_errand(errand_id)["status"] == "payment_unknown"
    # errand_status finds the invoice still unpaid -> errand becomes active again.
    _, status = run_tool(store, "errand_status", {"errand_id": errand_id})
    assert status["errand_status"] == "active"


def test_failed_payment_charges_nothing_and_keeps_errand_open(store, fake_bitrefill):
    from errand_guard.bitrefill import BitrefillError
    errand_id = confirmed_esim(store)
    _, quote = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "bitrefill-esim-japan",
                                                "package_value": "5GB, 7 Days"})
    fake_bitrefill.pay_error = BitrefillError("not enough funds", status=403)
    _, result = run_tool(store, "errand_buy", {"quote_id": quote["quote_id"]})
    assert not result["ok"] and "Nothing was charged" in result["error"]
    assert store.get_errand(errand_id)["status"] == "active"
    directive, _ = run_tool(store, "errand_buy", {"quote_id": quote["quote_id"]})
    assert directive["action"] == "block" and "quote-already-used" in directive["message"]


# ---- gift card + airtime --------------------------------------------------------------------
def test_gift_card_to_friend_email_is_sent_as_gift(store, fake_bitrefill):
    _, created = run_tool(store, "errand_create", dict(kind="gift_card", item="Amazon US $5 gift card",
                                                        product_keywords=["amazon"], face_value=5,
                                                        recipient="friend@example.com", max_amount=6, currency="USD"))
    errand_id = created["errand"]["errand_id"]
    run_tool(store, "errand_confirm", {"errand_id": errand_id})
    _, wrong = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "amazon-us", "package_value": 25})
    assert wrong["guard_preview"]["decision"] == "block"
    _, quote = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "amazon-us",
                                                "package_value": "5", "sender_name": "Maya"})
    line = fake_bitrefill.created[-1]["products"][0]
    assert line["gift"]["recipient_email"] == "friend@example.com" and line["gift"]["sender_name"] == "Maya"
    _, result = run_tool(store, "errand_buy", {"quote_id": quote["quote_id"]})
    assert result["ok"]


def test_airtime_goes_to_the_phone_number(store, fake_bitrefill):
    _, created = run_tool(store, "errand_create", dict(kind="airtime", item="MTN airtime 1000 NGN",
                                                        product_keywords=["mtn"], face_value=1000, face_currency="NGN",
                                                        recipient="0803 123 4567", max_amount=2, currency="USD"))
    errand_id = created["errand"]["errand_id"]
    run_tool(store, "errand_confirm", {"errand_id": errand_id})
    _, quote = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "mtn-nigeria",
                                                "package_value": "1000"})
    assert fake_bitrefill.created[-1]["products"][0]["phone_number"] == "08031234567"
    _, result = run_tool(store, "errand_buy", {"quote_id": quote["quote_id"]})
    assert result["ok"]


# ---- receipts, masking, /errands -------------------------------------------------------------
def bought_esim(store, fake_bitrefill):
    errand_id = confirmed_esim(store)
    _, quote = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "bitrefill-esim-japan",
                                                "package_value": "5GB, 7 Days"})
    run_tool(store, "errand_buy", {"quote_id": quote["quote_id"]})
    return errand_id


def test_status_stores_codes_locally_but_masks_them(store, fake_bitrefill):
    errand_id = bought_esim(store, fake_bitrefill)
    _, status = run_tool(store, "errand_status", {"errand_id": errand_id})
    text = json.dumps(status)
    assert "ABCD-1234-EFGH-5678" not in text and "145G7-NW5BE-RU3TW-IDEMO" not in text
    assert "5678" in text  # last digits shown so the user can tell codes apart
    stored = store.receipts_for_errand(errand_id)[0]["redemption"]
    assert stored["ord-inv-1"]["pin"] == "145G7-NW5BE-RU3TW-IDEMO"


def test_errands_command_lists_and_shows_without_codes(store, fake_bitrefill):
    errand_id = bought_esim(store, fake_bitrefill)
    run_tool(store, "errand_status", {"errand_id": errand_id})
    listing = commands.handle_errands("")
    assert f"#{errand_id}" in listing and "fulfilled" in listing
    shown = commands.handle_errands(f"show {errand_id}")
    assert "Receipt #" in shown and "IDEMO" not in shown
    assert "12.00 USD" in commands.handle_errands("receipts")


def test_errands_blocked_lists_reasons(store, fake_bitrefill):
    errand_id = confirmed_esim(store)
    _, big = run_tool(store, "errand_quote", {"errand_id": errand_id, "product_id": "bitrefill-esim-japan",
                                              "package_value": "20GB, 30 Days"})
    run_tool(store, "errand_buy", {"quote_id": big["quote_id"]})
    text = commands.handle_errands("blocked")
    assert "item-mismatch" in text and f"errand #{errand_id}" in text


def test_errands_code_writes_private_file(store, fake_bitrefill, tmp_path, monkeypatch):
    errand_id = bought_esim(store, fake_bitrefill)
    run_tool(store, "errand_status", {"errand_id": errand_id})
    monkeypatch.setattr(commands, "_codes_dir", lambda: tmp_path / "codes")
    reply = commands.handle_errands(f"code {errand_id}")
    path = tmp_path / "codes" / f"errand-{errand_id}.txt"
    assert str(path) in reply and "IDEMO" not in reply
    assert "145G7-NW5BE-RU3TW-IDEMO" in path.read_text(encoding="utf-8")
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ---- fail closed ---------------------------------------------------------------------------
def test_guard_crash_blocks_payment(store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("database on fire")
    monkeypatch.setattr(hooks, "evaluate_buy", boom)
    directive = hooks.on_pre_tool_call(tool_name="errand_buy", args={"quote_id": "q_x"}, **KW)
    assert directive["action"] == "block" and "guard-error" in directive["message"]


def test_guard_crash_does_not_block_read_only_tools(store, monkeypatch):
    monkeypatch.setattr(hooks, "_decide", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert hooks.on_pre_tool_call(tool_name="errand_list", args={}, **KW) is None


def test_terminal_curl_to_bitrefill_is_blocked(store):
    directive = hooks.on_pre_tool_call(tool_name="terminal",
                                       args={"command": "curl https://api.bitrefill.com/v2/invoices/x/pay"}, **KW)
    assert directive["action"] == "block" and "direct-api" in directive["message"]


def test_other_tools_are_left_alone(store):
    assert hooks.on_pre_tool_call(tool_name="read_file", args={"path": "notes.txt"}, **KW) is None


# ---- search ---------------------------------------------------------------------------------
@pytest.mark.parametrize("args,expect", [
    ({"query": "amazon"}, "amazon-us"),
    ({"kind": "esim", "country": "JP"}, "bitrefill-esim-japan"),
    ({"phone_number": "+2348031234567"}, "mtn-nigeria"),
    ({"product_id": "bitrefill-esim-japan", "kind": "esim"}, "5GB, 7 Days"),
])
def test_search_products(store, fake_bitrefill, args, expect):
    _, result = run_tool(store, "errand_search_products", args)
    assert result["ok"] and expect in json.dumps(result)


def test_search_needs_something_to_search(store, fake_bitrefill):
    _, result = run_tool(store, "errand_search_products", {})
    assert not result["ok"]


def test_missing_api_key_gives_a_clear_error(store):
    _, result = run_tool(store, "errand_search_products", {"query": "amazon"})
    assert not result["ok"] and "BITREFILL_API_KEY" in result["error"]
