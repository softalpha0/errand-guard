---
name: errand-guard
description: Run real-world purchases (airtime, mobile data, travel eSIMs, gift cards incl. Amazon and food delivery) safely through Errand Guard and Bitrefill. Use whenever the user asks you to buy, top up, send a gift card or pay for anything.
---

# Errand Guard: buy things only the way the user asked

(This skill's full name is `errand-guard:errand-guard`.)

Errand Guard turns the user's instruction into an **errand** (a mandate) and blocks any
payment that does not match it. Its checks run in code before every payment; you cannot
talk past them, so don't try. Work with them.

## The workflow (always in this order)

1. **Create the errand first: `errand_create`.** Copy the user's own words:
   - `kind`: `airtime`, `data`, `esim` or `gift_card` (food delivery or Amazon = `gift_card`).
   - `item`: plain description ("Japan eSIM 5 GB / 7 days").
   - `product_keywords`: words the product name must contain (`["japan"]`, `["amazon"]`).
   - `recipient`: the phone or email **exactly as the user gave it**. Never guess one.
   - `max_amount` + `currency`: the user's limit. **Never invent or raise a limit.** If the
     user gave no limit, ask for one.
   - eSIM/data: `data_gb` and `duration_days`. Gift card/airtime: `face_value`.
   If anything important is missing or unclear, ask the user before creating the errand.
2. **Confirm: `errand_confirm`.** Tell the user the summary in plain words. Hermes then
   shows them an approval prompt. Only a real person's "yes" activates the errand.
3. **Search: `errand_search_products`.** For eSIMs use `kind: "esim"` plus a country code;
   for airtime/data pass the `phone_number`; for gift cards a short `query`.
4. **Quote: `errand_quote`** with the product id and the package value exactly as listed
   (e.g. `"5GB, 7 Days"`). No money moves. Read `guard_preview`: if it says `block`, pick
   a package that matches the errand instead.
5. **Buy: `errand_buy`** with the `quote_id`. The user may be asked to approve the payment.
6. **Receipt: `errand_status`**, then tell the user what was bought, for how much and for
   whom. Codes are masked. To save them, the user runs `/errands code <id>`.

## When a payment is blocked

The message names the rule and the expected vs actual values. Tell the user plainly
what happened. Then either quote a product that matches the errand, or ask the user
whether they want a new errand (for example with a higher limit). Never:
- retry the same blocked purchase,
- create a new errand with different limits without the user asking for it,
- call Bitrefill's API any other way (terminal, code, web). That is blocked too.

## Good to know

- One errand pays once. A second purchase needs a new errand.
- Errands expire (24 hours by default).
- `errand_list` and `/errands` show every errand, receipt and blocked attempt.
- With Bitrefill test products (`include_test_products: true`, e.g. `test-gift-card-code`)
  the whole flow can be tried without real money.
