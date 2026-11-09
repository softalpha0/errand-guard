# Bitrefill API notes (read from docs.bitrefill.com, 2026-11-09)

Source: Bitrefill's docs MCP server (`https://docs.bitrefill.com/mcp`, tools `list-endpoints` / `get-endpoint`).

- **Base URL:** `https://api.bitrefill.com/v2`. The OpenAPI spec lists `api-bitrefill.com`, which looks like a typo, because the spec's own example links use `api.bitrefill.com`. **Verify with `GET /ping` once you have a key.**
- **Auth:** `Authorization: Bearer <Personal API key>` (the legacy Basic auth is for business accounts).
- **Pay from balance: supported.** `POST /invoices` with `payment_method: "balance"`, `auto_pay: false` creates an **unpaid** invoice (Errand Guard's "quote"). `POST /invoices/{id}/pay` pays it (403 = not enough funds, 404 = not found or already paid).
- **eSIMs use separate endpoints:** `GET /products/esims?country=JP`, `GET /products/esims/{id}`, `POST /esims`, `POST /esims/invoice/{id}/pay`, `GET /esims/invoice/{id}`, `GET /esims/{id}`. eSIM package values look like `"1GB, 7 Days"`.
- **Product:** `{id, name, country_code, currency, recipient_type, in_stock, packages: [{id, value, price}], range: {min, max, step, price_rate}}`. A package's `price` is in the account's currency.
- **Invoice:** `{id, status, payment: {method, currency, price, status}, orders: [{id, status, product: {id, name, value, currency}}]}`.
- **Order:** `{id, status, redemption_info, gift_info: {gift_url, recipient_email, ...}, invoice}`.
- **eSIM:** `{id, pin, barcode_value ("LPA:1$..."), rsp_url, package_history: [{package_name, data_mb, package_duration}]}`.
- **Invoice product line fields:** `product_id`, `quantity`, `value` (or `package_id`), `phone_number` (top-ups), `gift: {recipient_email, recipient_name, sender_name, message, send_date}`.
- **Invoice-level fields:** `email` + `send_email` (receipt email; used for eSIM delivery).
- **Phone lookup:** `GET /check_phone_number?phone_number=...` returns the operator product (or a list when unsure).
- **Free test products:** `test-gift-card-code`, `test-gift-card-link`, `test-esim`; searches need `include_test_products=true`.
- **Rate limits:** 429 with `x-product-quota-*` headers on product endpoints.

Not yet verified live (needs a real key):
1. That `/ping` works on `api.bitrefill.com`.
2. That a personal key can pay from balance (`/invoices/{id}/pay`).
3. The exact shape of `packages` (a list, as assumed) and of eSIM delivery details after payment.
