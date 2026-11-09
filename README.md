# Errand Guard for Hermes

**Errand Guard for Hermes: the agent runs real-world errands (airtime, data, eSIMs, gift cards — including Amazon and food-delivery gift cards) and cannot pay for anything that doesn't match your instruction. Wrong amount, wrong item or wrong recipient gets blocked before payment. You get receipts and a record of every errand.**

![Errand Guard demo](./docs/screenshots/demo.gif)
_Add a GIF of the blocked-then-approved errand._

- **Demo video:** _add X link_
- **Install:** `hermes plugins install errand-guard` _(after catalog review)_

---

## The problem

Would you give your AI agent your money? Agents don't always follow instructions, and with payments one mistake — the wrong amount, the wrong item, the wrong phone number — costs real money. Spending limits help, but they only know *how much*, not *what for*.

## How Errand Guard works

1. **You give an errand:** "Get me a Japan eSIM with 5 GB for 7 days, max $15, send it to me@example.com."
2. **Errand Guard makes a mandate** — item, recipient, merchant, max amount, quantity, expiry — and Hermes asks you to confirm it.
3. **The agent shops:** it searches Bitrefill and gets a quote. A quote is an unpaid Bitrefill invoice, so no money moves yet.
4. **Every payment is checked against your mandate** by a `pre_tool_call` hook, using Bitrefill's own numbers (not the agent's). Wrong amount, item, recipient, merchant or quantity → **blocked before payment**, with the reason. Matching payments go to Hermes's approval prompt.
5. **Receipts** land in `/errands`. Redemption codes stay on your computer and are masked in chat.

```
You: "Japan eSIM, 5 GB for 7 days, max $15, to me@example.com"
Errand Guard: Confirm errand #12: Japan eSIM · 5 GB / 7 days · to me@example.com · max 15.00 USD → approve? ✅
Agent → errand_buy(quote: Japan eSIM 20GB, 30 Days, $35)
Errand Guard: ⛔ Blocked — rule: item-mismatch on errand #12. Expected: 5 GB. Got: 20 GB
Agent → errand_buy(quote: Japan eSIM 5GB, 7 Days, $12)
Errand Guard: Errand #12: pay 12.00 USD … approve? ✅ → Paid. Receipt #1
```

### Why it is more than a prompt

- The guard runs **in code** before the payment tool runs. The model cannot talk its way past it.
- It checks twice: once in Hermes's `pre_tool_call` hook, and again inside `errand_buy` right before money moves.
- **A real person must say yes.** Hermes auto-approves in YOLO mode, with approvals off, or with its AI "smart" approval. Errand Guard listens to `post_approval_response` and only pays or confirms an errand when a person approved that exact action. Every prompt uses a one-time rule key, so an old "always" answer can never approve a new payment.
- It **fails closed**: no confirmed errand, a missing or unreadable value, an error inside the guard, or an unknown payment result → blocked.
- The agent can't go around it: terminal, code or web calls to Bitrefill's API are blocked too.

## Guard rules (in order)

| Rule | Blocks when |
|---|---|
| `no-mandate` / `not-confirmed` / `errand-cancelled` | no errand you confirmed in this conversation |
| `expired` · `already-fulfilled` · `in-progress` · `payment-unknown` | errand expired, already bought, a payment is running, or the last payment's result is unclear |
| `recipient-mismatch` | phone/email differs (`+234 803…` and `0803…` count as the same number) |
| `item-mismatch` | wrong kind, product, country/brand keyword, data GB, days or face value |
| `merchant-not-allowed` | not an allowed merchant |
| `quantity-mismatch` | different quantity |
| `amount-over-max` · `currency-mismatch` · `amount-unknown` | price above your max, a different currency, or no clear price |
| `daily-cap` | today's spending would exceed `ERRAND_DAILY_CAP` |
| `above-auto-approve` → asks you | price above `ERRAND_AUTO_APPROVE_MAX` (default 0 = always ask) |
| `direct-api` | any other tool calling Bitrefill's API directly |

Each block message names the rule, the errand, and the expected vs actual value.

## Features

| Feature | Status |
|---|---|
| Mandates from your instruction, confirmed by you | ✅ built + tested (unit tests) |
| Guard: amount, item, recipient, merchant, quantity, expiry, duplicates | ✅ built + tested (one test per rule) |
| Airtime, data, eSIMs, gift cards (via Bitrefill) | ✅ built, tested against a simulated Bitrefill — live check pending |
| Receipts + `/errands` command | ✅ built + tested |
| Daily spending cap | ✅ built + tested |
| Errands dashboard tab | ⬜ |
| Amazon & sushi checkout guard (browser, beta) | ⬜ (Amazon / food delivery work today as gift cards) |
| Telegram approvals and receipts | ⬜ not tested yet (uses Hermes's own approval prompts) |

## Hermes surfaces used

| Surface | Use |
|---|---|
| Tools | `errand_create`, `errand_confirm`, `errand_list`, `errand_cancel`, `errand_search_products`, `errand_quote`, `errand_buy`, `errand_status`, `errand_receipt` |
| `pre_tool_call` hook | Blocks or escalates payments that don't match the mandate |
| `post_approval_response` hook | Records whether a real person approved |
| `post_tool_call` hook | Activity log (declined approvals, errors, successes) |
| Slash command | `/errands` |
| Skill | `errand-guard:errand-guard` — teaches the agent: mandate first, then buy |

## Install & run

```bash
hermes plugins install errand-guard
hermes plugins enable errand-guard
```

Or from source (Mac / Linux; on Windows the plugins folder is `%LOCALAPPDATA%\hermes\plugins`, see docs/TESTING.md):

```bash
git clone https://github.com/softalpha0/errand-guard.git ~/.hermes/plugins/errand-guard
hermes plugins validate ~/.hermes/plugins/errand-guard --install-deps
hermes config set BITREFILL_API_KEY your-bitrefill-personal-api-key
hermes plugins enable errand-guard
hermes
```

`hermes config set` saves the key in Hermes's own `.env` file. Errand Guard reads it through Hermes's secret helper and never opens that file itself. See `.env.example` for every setting:

```bash
BITREFILL_API_KEY=          # bitrefill.com → Account → Developers
BITREFILL_BASE_URL=         # optional; default https://api.bitrefill.com/v2
ERRAND_AUTO_APPROVE_MAX=0   # matching payments at or below this skip the extra prompt (0 = always ask)
ERRAND_DAILY_CAP=           # optional
```

The same settings appear in the Desktop app under Settings → Plugins → Errand Guard.

### `/errands`

```
/errands                 list recent errands
/errands show <id>       one errand: mandate, receipts, blocked attempts
/errands receipts        recent receipts and this month's totals
/errands blocked         recent blocked payments and why
/errands code <id>       save that errand's codes to a private file (never shown in chat)
```

### Run the tests

```bash
cd ~/.hermes/plugins/errand-guard
python -m pip install -r requirements-dev.txt
python -m pytest tests
```

## Safety and disclosures

- **Spends real money** from your own Bitrefill account balance. Start with small amounts and Bitrefill's test products.
- **Network:** calls only Bitrefill's API (`api.bitrefill.com`) with your API key, sending the product, package, quantity and recipient (phone or email) for the errand.
- **Stored locally:** errands, quotes, receipts, approval answers, an activity log, and redemption codes, in Hermes's plugin data folder (`plugin-data/errand-guard/`). Nothing is sent anywhere else. No telemetry.
- **Never stores card numbers.** Payment uses your Bitrefill balance; the plugin never sees a card.
- **Codes are masked in chat.** `/errands code <id>` writes them to a file only your user account can read.
- **Fails closed:** no confirmed errand, or any doubt → blocked. Unattended runs (cron) cannot confirm errands, and cannot pay anything above your auto-approve limit (0 by default, so nothing), because no person is there to approve.

## Team

[softalpha0](https://github.com/softalpha0)

Built with help from [Claude Code](https://claude.com/claude-code).

## What's next

Errands dashboard tab · browser checkout guard (Amazon, food) · recurring errands · household budgets · more providers · guarding other wallet tools.

## License

[MIT](./LICENSE)
