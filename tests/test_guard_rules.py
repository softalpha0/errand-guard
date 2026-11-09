"""One test (at least) for every guard rule. Each checks the decision AND that the block
message names the rule and shows expected vs actual."""

import time
from decimal import Decimal

import pytest

from errand_guard import guard
from conftest import CONV, make_errand, make_purchase

NOW = time.time()


def check(errand, purchase, **kw):
    params = dict(conversation_id=CONV, now=NOW, auto_approve_max=Decimal("0"))
    params.update(kw)
    return guard.check_purchase(errand, purchase, **params)


def assert_block(decision, rule, *fragments):
    assert decision.action == guard.BLOCK, decision
    assert decision.rule == rule, decision
    assert f"rule: {rule}" in decision.message
    assert "Expected:" in decision.message and "Got:" in decision.message
    for fragment in fragments:
        assert fragment in decision.message, (fragment, decision.message)


# ---- happy paths --------------------------------------------------------------------
def test_matching_purchase_asks_human_when_auto_approve_is_zero(store):
    decision = check(make_errand(store), make_purchase())
    assert decision.action == guard.APPROVE
    assert "12.00 USD" in decision.message and "max 15.00 USD" in decision.message


def test_matching_purchase_under_auto_approve_limit_is_allowed(store):
    decision = check(make_errand(store), make_purchase(price="12"), auto_approve_max=Decimal("12"))
    assert decision.action == guard.ALLOW


def test_price_just_above_auto_approve_limit_asks_human(store):
    decision = check(make_errand(store), make_purchase(price="12.01"), auto_approve_max=Decimal("12"))
    assert decision.action == guard.APPROVE


# ---- 1. no mandate ------------------------------------------------------------------
def test_no_errand_blocks():
    assert_block(check(None, make_purchase()), "no-mandate", "confirmed errand")


def test_errand_from_other_conversation_blocks(store):
    errand = make_errand(store, conversation_id="someone-else")
    assert_block(check(errand, make_purchase()), "no-mandate", "different conversation")


def test_missing_conversation_id_blocks(store):
    assert_block(check(make_errand(store), make_purchase(), conversation_id=""), "no-mandate")


def test_unconfirmed_draft_blocks(store):
    assert_block(check(make_errand(store, status="draft"), make_purchase()), "not-confirmed", "status 'draft'")


def test_cancelled_errand_blocks(store):
    assert_block(check(make_errand(store, status="cancelled"), make_purchase()), "errand-cancelled")


def test_unreadable_purchase_blocks(store):
    assert_block(check(make_errand(store), None), "unreadable-purchase")


# ---- 2. expired / fulfilled / duplicate -------------------------------------------------
def test_expired_errand_blocks(store):
    errand = make_errand(store, expires_at=NOW - 60)
    assert_block(check(errand, make_purchase()), "expired", "payment before")


def test_fulfilled_errand_blocks_duplicate(store):
    assert_block(check(make_errand(store, status="fulfilled"), make_purchase()), "already-fulfilled")


def test_payment_in_progress_blocks_second_payment(store):
    assert_block(check(make_errand(store, status="buying"), make_purchase()), "in-progress")


def test_unknown_payment_result_blocks_retry(store):
    assert_block(check(make_errand(store, status="payment_unknown"), make_purchase()), "payment-unknown")


# ---- 3. recipient -----------------------------------------------------------------
def test_different_email_blocks(store):
    decision = check(make_errand(store), make_purchase(recipient="friend@example.com"))
    assert_block(decision, "recipient-mismatch", "me@example.com", "friend@example.com")


def test_email_case_and_spaces_still_match(store):
    assert check(make_errand(store), make_purchase(recipient="  ME@Example.com ")).action == guard.APPROVE


def test_missing_recipient_blocks_when_errand_has_one(store):
    assert_block(check(make_errand(store), make_purchase(recipient="")), "recipient-mismatch", "no recipient")


def test_recipient_added_when_errand_has_none_blocks(store):
    errand = make_errand(store, recipient="", recipient_type="none")
    assert_block(check(errand, make_purchase(recipient="x@example.com")), "recipient-mismatch")


def test_unreadable_recipient_blocks(store):
    assert_block(check(make_errand(store), make_purchase(recipient="not a recipient")), "recipient-mismatch")


def airtime_errand(store, **kw):
    fields = dict(kind="airtime", item="MTN airtime 1000 NGN", keywords=["mtn"], data_gb=None, duration_days=None,
                  face_value="1000", face_currency="NGN", recipient="+2348031234567", recipient_type="phone",
                  max_amount="2", currency="USD")
    fields.update(kw)
    return make_errand(store, **fields)


def airtime_purchase(**kw):
    fields = dict(kind="airtime", product_id="mtn-nigeria", product_name="MTN Nigeria", package="1000",
                  data_gb=None, duration_days=None, face_value="1000", face_currency="NGN",
                  recipient="+2348031234567", price="1.20")
    fields.update(kw)
    return make_purchase(**fields)


@pytest.mark.parametrize("number", ["+234 803 123 4567", "08031234567", "002348031234567", "234-803-123-4567"])
def test_same_phone_in_other_formats_matches(store, number):
    assert check(airtime_errand(store), airtime_purchase(recipient=number)).action == guard.APPROVE


@pytest.mark.parametrize("number", ["+2348031234568", "08031234568", "+2338031234567", "8031234567"])
def test_different_phone_blocks(store, number):
    assert_block(check(airtime_errand(store), airtime_purchase(recipient=number)), "recipient-mismatch",
                 "+2348031234567")


def test_phone_instead_of_email_blocks(store):
    assert_block(check(make_errand(store), make_purchase(recipient="+2348031234567")), "recipient-mismatch")


# ---- 4. item ------------------------------------------------------------------------
def test_bigger_esim_plan_blocks_with_expected_and_actual(store):
    decision = check(make_errand(store), make_purchase(package="20GB, 30 Days", data_gb="20", duration_days=30,
                                                       price="35"))
    assert_block(decision, "item-mismatch", "5 GB", "20 GB")


def test_wrong_duration_blocks(store):
    assert_block(check(make_errand(store), make_purchase(duration_days=30, package="5GB, 30 Days")),
                 "item-mismatch", "7 days", "30 days")


def test_unknown_data_amount_blocks(store):
    assert_block(check(make_errand(store), make_purchase(data_gb=None)), "item-mismatch", "unknown data")


def test_unlimited_plan_does_not_match_5gb(store):
    assert_block(check(make_errand(store), make_purchase(data_gb="Infinity")), "item-mismatch", "unlimited")


def test_wrong_country_keyword_blocks(store):
    decision = check(make_errand(store), make_purchase(product_id="bitrefill-esim-korea", product_name="Korea eSIM"))
    assert_block(decision, "item-mismatch", "'japan'", "Korea eSIM")


def test_keyword_must_be_a_whole_word(store):
    errand = make_errand(store, keywords=["us"])
    decision = check(errand, make_purchase(product_id="x", product_name="Russia eSIM Japan"))
    assert_block(decision, "item-mismatch", "'us'")


def test_wrong_kind_blocks(store):
    assert_block(check(make_errand(store), make_purchase(kind="gift_card")), "item-mismatch",
                 "a esim purchase", "a gift_card purchase")


def test_exact_product_id_enforced(store):
    errand = make_errand(store, product_id="bitrefill-esim-japan-premium")
    assert_block(check(errand, make_purchase()), "item-mismatch", "bitrefill-esim-japan-premium")


def test_errand_without_keywords_or_product_id_blocks(store):
    assert_block(check(make_errand(store, keywords=[]), make_purchase()), "item-mismatch", "names the product")


def test_gift_card_face_value_mismatch_blocks(store):
    errand = make_errand(store, kind="gift_card", item="Amazon US $5", keywords=["amazon"], data_gb=None,
                         duration_days=None, face_value="5", face_currency="USD", max_amount="6")
    purchase = make_purchase(kind="gift_card", product_id="amazon-us", product_name="Amazon.com US", package="25",
                             data_gb=None, duration_days=None, face_value="25", face_currency="USD", price="5.10")
    assert_block(check(errand, purchase), "item-mismatch", "value 5.00 USD", "value 25.00 USD")


def test_face_value_currency_mismatch_blocks(store):
    errand = airtime_errand(store)
    assert_block(check(errand, airtime_purchase(face_currency="GHS")), "item-mismatch", "NGN", "GHS")


# ---- 5. merchant -------------------------------------------------------------------
def test_merchant_not_allowed_blocks(store):
    assert_block(check(make_errand(store), make_purchase(merchant="shady-shop")), "merchant-not-allowed",
                 "bitrefill", "shady-shop")


def test_missing_merchant_blocks(store):
    assert_block(check(make_errand(store), make_purchase(merchant="")), "merchant-not-allowed", "unknown merchant")


# ---- 6. quantity -------------------------------------------------------------------
def test_quantity_mismatch_blocks(store):
    assert_block(check(make_errand(store), make_purchase(quantity=2, price="12")), "quantity-mismatch",
                 "Expected: 1", "Got: 2")


def test_non_integer_quantity_blocks(store):
    assert_block(check(make_errand(store), make_purchase(quantity="1")), "quantity-mismatch")


# ---- 7. amount -----------------------------------------------------------------------
def test_amount_over_max_blocks(store):
    assert_block(check(make_errand(store), make_purchase(price="15.01")), "amount-over-max",
                 "at most 15.00 USD", "15.01 USD")


def test_amount_equal_to_max_is_fine(store):
    assert check(make_errand(store), make_purchase(price="15")).action == guard.APPROVE


def test_currency_mismatch_blocks(store):
    assert_block(check(make_errand(store), make_purchase(price="12", currency="EUR")), "currency-mismatch",
                 "USD", "EUR")


@pytest.mark.parametrize("price", [None, "", "abc", "-1", "NaN", "Infinity", True])
def test_unknown_or_bad_price_blocks(store, price):
    assert_block(check(make_errand(store), make_purchase(price=price)), "amount-unknown")


# ---- 8. daily cap --------------------------------------------------------------------
def test_daily_cap_blocks_when_exceeded(store):
    decision = check(make_errand(store), make_purchase(price="12"), daily_cap=Decimal("20"),
                     spent_today=Decimal("10"))
    assert_block(decision, "daily-cap", "at most 20.00 USD", "22.00 USD")


def test_daily_cap_allows_when_under(store):
    decision = check(make_errand(store), make_purchase(price="12"), daily_cap=Decimal("30"),
                     spent_today=Decimal("10"))
    assert decision.action == guard.APPROVE


# ---- rule order -------------------------------------------------------------------------
def test_rule_order_recipient_before_item_before_amount(store):
    purchase = make_purchase(recipient="friend@example.com", data_gb="20", price="35")
    assert check(make_errand(store), purchase).rule == "recipient-mismatch"
    assert check(make_errand(store), make_purchase(data_gb="20", price="35")).rule == "item-mismatch"


def test_rule_order_expiry_before_everything(store):
    errand = make_errand(store, expires_at=NOW - 1)
    assert check(errand, make_purchase(recipient="x@y.com", price="999")).rule == "expired"


# ---- confirm -------------------------------------------------------------------------
def test_confirm_draft_needs_human(store):
    decision = guard.check_confirm(make_errand(store, status="draft"), conversation_id=CONV, now=NOW)
    assert decision.action == guard.APPROVE
    assert "Japan eSIM" in decision.message and "me@example.com" in decision.message


def test_confirm_already_active_blocks(store):
    assert_block(guard.check_confirm(make_errand(store), conversation_id=CONV, now=NOW), "not-a-draft")


def test_confirm_expired_draft_blocks(store):
    errand = make_errand(store, status="draft", expires_at=NOW - 1)
    assert_block(guard.check_confirm(errand, conversation_id=CONV, now=NOW), "expired")


# ---- direct API bypass -------------------------------------------------------------------
@pytest.mark.parametrize("tool,args", [
    ("terminal", {"command": "curl -X POST https://api.bitrefill.com/v2/invoices/abc/pay -H 'Authorization: Bearer x'"}),
    ("execute_code", {"code": "requests.post('https://API.BITREFILL.COM/v2/invoices')"}),
    ("web_extract", {"urls": ["https://api.bitrefill.com/v2/accounts/balance"]}),
])
def test_direct_bitrefill_api_calls_are_blocked(tool, args):
    assert_block(guard.check_direct_api(tool, args), "direct-api", tool)


def test_reading_bitrefill_docs_or_website_is_fine():
    assert guard.check_direct_api("web_extract", {"urls": ["https://docs.bitrefill.com/"]}) is None
    assert guard.check_direct_api("browser_navigate", {"url": "https://www.bitrefill.com/us/en/"}) is None
