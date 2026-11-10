"""Tool schemas: what the model reads to decide how to call each tool."""

_KIND = {"type": "string", "enum": ["airtime", "data", "esim", "gift_card"],
         "description": "What is being bought. Food delivery / online shopping = a gift_card (e.g. Uber Eats, Amazon)."}

ERRAND_CREATE = {
    "name": "errand_create",
    "description": (
        "ALWAYS call this first when the user asks you to buy, top up or pay for anything. It turns the "
        "user's instruction into an errand (a mandate): exactly what to buy, for whom, the maximum price and "
        "quantity. Copy the user's own words; never invent or raise limits. Creates a DRAFT; then call "
        "errand_confirm so the user approves it. Errand Guard blocks any payment that does not match. "
        "Full workflow: skill_view('errand-guard:errand-guard')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": _KIND,
            "item": {"type": "string", "description": "Plain description in the user's words, e.g. 'Japan eSIM 5 GB / 7 days'."},
            "product_keywords": {"type": "array", "items": {"type": "string"},
                                 "description": "Words the product name must contain, e.g. ['japan'] or ['amazon']. Required unless product_id is given."},
            "product_id": {"type": "string", "description": "Exact Bitrefill product id, only if the user named it."},
            "recipient": {"type": "string", "description": "Phone number (airtime/data) or email (eSIM, gift card) exactly as the user gave it. Empty = the user's own Bitrefill account (eSIM/gift card only)."},
            "max_amount": {"type": "number", "description": "The most the user allows in total for this errand."},
            "currency": {"type": "string", "description": "3-letter currency of max_amount, e.g. USD."},
            "quantity": {"type": "integer", "description": "How many (default 1)."},
            "data_gb": {"type": ["number", "string"], "description": "eSIM/data: GB the user asked for, or 'unlimited'."},
            "duration_days": {"type": "integer", "description": "eSIM/data: validity in days the user asked for."},
            "face_value": {"type": "number", "description": "Gift card / airtime value the user asked for (e.g. 25 for a $25 card)."},
            "face_currency": {"type": "string", "description": "Currency of face_value if different from currency."},
            "merchants": {"type": "array", "items": {"type": "string"}, "description": "Allowed merchants. Default ['bitrefill']."},
            "expires_in_hours": {"type": "number", "description": "How long the errand stays valid (default 24)."},
        },
        "required": ["kind", "item", "max_amount", "currency"],
    },
}

ERRAND_CONFIRM = {
    "name": "errand_confirm",
    "description": (
        "Ask the user to confirm a draft errand. Hermes shows the user an approval prompt with the full "
        "errand; it only becomes active if a real person approves. Call right after errand_create."
    ),
    "parameters": {"type": "object", "properties": {"errand_id": {"type": "integer"}}, "required": ["errand_id"]},
}

ERRAND_LIST = {
    "name": "errand_list",
    "description": "List errands (mandates) and their status: draft, active, fulfilled, cancelled, expired.",
    "parameters": {"type": "object", "properties": {
        "scope": {"type": "string", "enum": ["this_conversation", "all"], "description": "Default this_conversation."}}},
}

ERRAND_CANCEL = {
    "name": "errand_cancel",
    "description": "Cancel a draft or active errand so nothing more can be bought for it.",
    "parameters": {"type": "object", "properties": {"errand_id": {"type": "integer"}}, "required": ["errand_id"]},
}

ERRAND_SEARCH_PRODUCTS = {
    "name": "errand_search_products",
    "description": (
        "Search Bitrefill for products: gift cards, airtime, data and eSIMs. For airtime/data give "
        "phone_number to find the operator. For eSIMs give kind='esim' and a country code. Returns product "
        "ids and their packages (value + price). Searching never spends money."
    ),
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Search words, e.g. 'amazon us', 'uber eats'."},
        "kind": _KIND,
        "country": {"type": "string", "description": "eSIM: ISO country code(s), e.g. 'JP' or 'JP,KR'."},
        "phone_number": {"type": "string", "description": "airtime/data: number to look up the operator for."},
        "product_id": {"type": "string", "description": "Get one product's full details and packages."},
        "include_test_products": {"type": "boolean", "description": "Include Bitrefill test products (for trying the flow without real money)."},
    }},
}

ERRAND_QUOTE = {
    "name": "errand_quote",
    "description": (
        "Get a real price for one product package for an ACTIVE errand. Creates an unpaid Bitrefill invoice "
        "(no money moves) and returns a quote_id plus whether Errand Guard would allow it. Then call "
        "errand_buy with the quote_id."
    ),
    "parameters": {"type": "object", "properties": {
        "errand_id": {"type": "integer"},
        "recipient": {"type": "string", "description": "Phone or email to deliver to. Leave empty to use the errand's recipient (recommended)."},
        "product_id": {"type": "string"},
        "package_value": {"type": ["string", "number"], "description": "The package's value exactly as listed, e.g. '5GB, 7 Days' or 25. For ranged products, the amount."},
        "package_id": {"type": "string", "description": "Optional package id from the product details."},
        "quantity": {"type": "integer", "description": "Default 1."},
        "recipient_name": {"type": "string", "description": "Gift card sent by email: recipient's name."},
        "sender_name": {"type": "string", "description": "Gift card sent by email: sender's name shown in the email."},
        "gift_message": {"type": "string"},
    }, "required": ["errand_id", "product_id", "package_value"]},
}

ERRAND_BUY = {
    "name": "errand_buy",
    "description": (
        "Pay for a quote from your Bitrefill balance. Errand Guard checks the quote against the confirmed "
        "errand (item, recipient, merchant, quantity, price) and blocks anything that does not match; the "
        "user is asked to approve the payment. If blocked, explain the reason to the user; do not retry "
        "with a different product unless it matches the errand."
    ),
    "parameters": {"type": "object", "properties": {"quote_id": {"type": "string"}}, "required": ["quote_id"]},
}

ERRAND_STATUS = {
    "name": "errand_status",
    "description": "Refresh an errand's order status from Bitrefill (delivery, redemption ready). Codes are stored locally and masked.",
    "parameters": {"type": "object", "properties": {"errand_id": {"type": "integer"}}, "required": ["errand_id"]},
}

ERRAND_RECEIPT = {
    "name": "errand_receipt",
    "description": "Show the receipt for an errand: order id, amount, item, recipient, status. Redemption codes are masked.",
    "parameters": {"type": "object", "properties": {"errand_id": {"type": "integer"}}, "required": ["errand_id"]},
}

ALL = [ERRAND_CREATE, ERRAND_CONFIRM, ERRAND_LIST, ERRAND_CANCEL, ERRAND_SEARCH_PRODUCTS, ERRAND_QUOTE,
       ERRAND_BUY, ERRAND_STATUS, ERRAND_RECEIPT]
