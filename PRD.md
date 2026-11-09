# PRD — Errand Guard for Hermes

> **Errand Guard for Hermes: the agent runs real-world errands (books or orders sushi, shops on Amazon, airtime, data, eSIMs, gift cards) and cannot pay for anything that doesn't match your instruction. Wrong amount, wrong item or wrong recipient gets blocked before payment. You get receipts and a dashboard of every errand.**

| | |
|---|---|
| **Hackathon** | Hermes Agent Plugin Hackathon (Nous Research) — tracker ID HK-014 |
| **Deadline** | **Wed Nov 11, 2026, 5:00 PM EST = 11:00 PM Lagos** |
| **Our targets** | Catalog PR open **Tue Nov 10, 9 PM Lagos** · form submitted **Wed Nov 11, 5 PM Lagos** |
| **Prizes** | $10K / $5K / $2.5K / $1.5K / $1K + 5 runners-up · accepted entries listed in the Hermes Marketplace |
| **Judged on** | Usefulness, reliability, polish, creativity — **favouring plugins that do more than a prompt or a skill could** |
| **Enter by** | 1) PR adding `plugin-catalog/errand-guard.yaml` to `NousResearch/hermes-agent` · 2) demo video **< 90 s** on X tagging @NousResearch · 3) both links in the [Typeform](https://form.typeform.com/to/FwJLvOln) |
| **Team** | Solo — softalpha0 |
| **Version** | v1.0 — 2026-11-09 (Hackathon 101 method) |

**Originality note:** all code is written new, in a new repo, during the hackathon.

---

## 1. Done statement

> **"A user tells Hermes 'get me a Japan eSIM with 5 GB for 7 days, max $15, send it to me@example.com'; Errand Guard turns it into a mandate the user confirms; the agent buys it; any purchase that doesn't match the mandate is blocked before payment with a clear reason; and the receipt appears in the Errands dashboard."**

## 2. The problem (five-question test)

| Question | Answer |
|---|---|
| **Who exactly has it?** | Hermes users who want their agent to do real errands — top-ups, travel eSIMs, gift cards, food orders, online shopping — for themselves or family. |
| **How painful?** | Giving an agent money is scary: agents sometimes **don't follow instructions**. One wrong amount, item or recipient costs real money. So most people never let agents pay. |
| **How do they cope today?** | Do it by hand; or use payment plugins with spending **limits** (e.g. PayGuard, pay) that know *how much* but not *what for*. |
| **Our angle** | **Intent matching, enforced in code:** every payment-capable tool call is checked against the user's own instruction (item, amount, recipient, merchant) by a `pre_tool_call` hook and **blocked** if it doesn't match. The model cannot talk its way past it. |
| **Convincing version by Wednesday?** | Yes — the guard + API purchases (Bitrefill) are the Must; browser checkouts (Amazon, sushi) are Should. |

**User and moment (demo):** *Maya lands in Tokyo tomorrow and asks Hermes for a 5 GB, 7-day Japan eSIM, max $15. The agent "helpfully" picks the 20 GB plan for $35. Errand Guard stops it: "Blocked — 20 GB / $35 doesn't match errand #12 (5 GB, max $15)."*

## 3. Gap check

| Existing plugin | What it does | Errand Guard's difference |
|---|---|---|
| hermes-payguard | USDC/x402 payments with per-payment limits, allowlists, out-of-loop approval | Checks **what** is bought and **for whom**, not just amount; buys real-world goods |
| pay / pay-cli (Certified) | Pays for priced APIs in stablecoins | Real-world errands, not API calls |
| letsfg | Books flights and hotels | Everyday errands + a guard that works across any payment tool |
| hermes-memlock | Re-reminds standing instructions after compaction | **Enforces** the instruction at the moment of payment |

No marketplace plugin found that buys airtime/data/eSIMs/gift cards or enforces instruction-matching on payments.

## 4. Why it's more than a prompt or a skill

- A **`pre_tool_call` hook** returns `{"action": "block", "message": …}` — the tool never runs. A prompt can only ask the model to behave.
- `{"action": "approve"}` escalates matching-but-sensitive payments to Hermes's **human approval gate** (fails closed on deny/timeout/error).
- Real **tools** that call Bitrefill's API and record receipts in a local database.
- A **dashboard tab** and **slash command** to review every errand.
- Works as a guard for **other** payment tools too (its own, Hermes browser checkout clicks, and optionally PayGuard/pay tools).

## 5. How it works

1. **Mandate.** When the user gives an errand, the agent calls `errand_create` with structured fields parsed from the instruction: `kind` (airtime, data, esim, gift_card, food, shopping), `item` (e.g. "Japan eSIM 5 GB / 7 days", "Amazon US gift card $25", "salmon sushi set"), `recipient` (phone, email, delivery name/address), `merchant` (optional allowlist), `max_amount` + `currency`, `quantity`, `expires_at`. The tool returns the mandate for the user to **confirm** (Hermes approval). Only confirmed mandates are active.
2. **Purchase.** The agent uses Errand Guard's tools (`errand_quote`, `errand_buy`) or a browser checkout.
3. **Guard.** On every payment-capable tool call, the `pre_tool_call` hook:
   - finds the active mandate for the session (none → **block**: "No confirmed errand for this payment");
   - compares the call's **amount ≤ max**, **item/product matches**, **recipient matches exactly** (normalised phone/email), **merchant allowed**, **quantity**, **not expired**, **not already fulfilled**;
   - **block** with a specific reason, or **approve** (escalate to the human gate) when amount is above the auto-approve threshold, or allow.
4. **Receipt.** `post_tool_call` records the result: order ID, amount, item, recipient, status, redemption info (stored locally), and marks the mandate fulfilled.
5. **Review.** `/errands` and the **Errands dashboard tab** show every errand: mandate, attempts, blocks (with reasons), receipts, totals.

## 6. Features — MoSCoW

### Must have
| # | Feature |
|---|---|
| M1 | Plugin skeleton: `plugin.yaml`, `register(ctx)`, schemas, tools, local SQLite store, `config_schema` + `requires_env` (`BITREFILL_API_KEY`) |
| M2 | `errand_create` (mandate from instruction) + user confirmation via Hermes approval; `errand_list`, `errand_cancel` |
| M3 | **Guard hook** (`pre_tool_call`) with the checks in §5.3 and clear block messages |
| M4 | Bitrefill tools: `errand_search_products` (incl. phone-number operator check), `errand_quote`, `errand_buy` (create invoice; pay from Bitrefill account balance — verify personal-API support; fallback: show Lightning invoice for the user to pay), `errand_status` |
| M5 | Products: **airtime, data, eSIMs, gift cards** (incl. Amazon and food-delivery gift cards where Bitrefill offers them) |
| M6 | Receipts: `post_tool_call` logging; `errand_receipt` tool; redemption codes stored locally, masked in chat |
| M7 | `/errands` slash command (list, show, receipts, blocked attempts) |
| M8 | Skill `skills/errand-guard/SKILL.md` teaching the agent the workflow (always create a mandate first) |
| M9 | Tests for every guard rule (amount, item, recipient, merchant, quantity, expiry, duplicate) |
| M10 | Passes `hermes plugins validate . --install-deps` and `hermes plugins doctor . --ci` |

### Should have
| # | Feature |
|---|---|
| S1 | **Errands dashboard tab** (dashboard extension): errands list, block log with reasons, receipts, monthly totals |
| S2 | **Browser checkout guard** for **Amazon** and **sushi ordering/booking** sites: before a final "Place order / Pay / Book" browser click, read the checkout page (items, total, ship-to name/address, merchant) and match it to the mandate; always escalate to the human gate; never stores card details |
| S3 | Daily and per-errand spending caps (config) |
| S4 | Telegram: approvals and receipts via the Hermes Telegram gateway |

### Could have
| # | Feature |
|---|---|
| C1 | Guard optional third-party payment tools (PayGuard / pay) by mapping their tool args |
| C2 | Desktop pane mirroring the dashboard |
| C3 | CSV export of receipts |

### Won't have (for now) → parking lot
Storing card numbers · auto-buying without a confirmed mandate · recurring errands · splitting bills · multi-user households.

**Cut order if behind:** C1–C3 → S4 → S3 → S2 (keep Amazon/food as gift cards via M5) → S1 (keep `/errands`). **Never cut M1–M10.**

## 7. Main user journey (5 steps)

1. User: "Get me a Japan eSIM with 5 GB for 7 days, max $15, send it to me@example.com."
2. Agent creates the mandate → user confirms.
3. Agent searches and quotes the product → buys it.
4. (Demo twist) Agent tries the 20 GB / $35 plan, or sends it to a different email → **blocked** with the reason → agent corrects → allowed.
5. Receipt in chat and in `/errands` / dashboard.

## 8. Plugin surfaces & files

```
errand-guard/
├─ plugin.yaml            # name, version, description, provides_tools, provides_hooks, capabilities, requires_env, config_schema
├─ __init__.py            # register(ctx): tools, hooks (pre_tool_call, post_tool_call), command (/errands), skill, dashboard
├─ schemas.py             # tool schemas the model reads
├─ tools.py               # errand_create/list/cancel, search_products, quote, buy, status, receipt
├─ guard.py               # mandate matching rules → block / approve / allow
├─ bitrefill.py           # API client (Bearer key from env)
├─ store.py               # SQLite: mandates, attempts, receipts
├─ skills/errand-guard/SKILL.md
├─ dashboard/             # S1 dashboard tab
├─ tests/                 # pytest for guard rules + tool flows (mock Bitrefill)
├─ README.md · LICENSE (MIT) · .env.example
```

**Hooks used:** `pre_tool_call` (block / approve / allow), `post_tool_call` (receipts). **Commands:** `/errands`. **Env:** `BITREFILL_API_KEY`, `BITREFILL_BASE_URL`, `ERRAND_AUTO_APPROVE_MAX` (default 0 = always ask), `ERRAND_DAILY_CAP`.

**Guard rule order:** no mandate → block · expired/fulfilled → block · recipient mismatch → block · item/product mismatch → block · merchant not allowed → block · quantity mismatch → block · amount > max → block · amount > auto-approve → approve (human gate) · else allow. Every block message names the rule and the expected vs actual value.

## 9. Testing (Hackathon 101 loop)

For each feature: build → run Hermes with the plugin enabled (`hermes plugins enable errand-guard`) → try it like a user → **try to break it** (empty recipient, different number format, currency mismatch, two errands at once, expired mandate, model trying to call `errand_buy` without a mandate) → `pytest` → commit. Use `HERMES_PLUGINS_DEBUG=1 hermes plugins list` and `hermes logs --level WARNING | grep -i plugin` when something doesn't load. Real purchases: **$1–2 only**.

## 10. Plan backwards from the deadline (Lagos)

| Reserved | When |
|---|---|
| Buffer | Wed 17:00–23:00 |
| **Typeform submitted** (PR link + X video link) | **Wed 16:00–17:00** |
| Post demo on X tagging @NousResearch | Wed 15:00–16:00 |
| Record + edit demo (< 90 s, captions) | Wed 12:00–15:00 |
| Update PR pin to final commit; README + screenshots | Wed 10:00–12:00 |
| **Feature freeze** | **Wed 10:00** |

| Build block | Work |
|---|---|
| Mon (rest of day) | M1 skeleton, M2 mandate, **M3 guard + M9 tests** (riskiest + core) |
| Tue morning | M4–M5 Bitrefill tools; first real small purchase ($1–5 gift card or eSIM); M6 receipts; M7 `/errands`; M8 skill |
| Tue afternoon | M10 validation; **open the catalog PR** (Tue 21:00) |
| Tue evening | S1 dashboard; S3 caps |
| Wed early morning | S2 browser checkout guard (Amazon / sushi) if Must all green; S4 Telegram |

Stuck 30 min → fresh chat, simpler way, ask in the Nous Discord, or cut.

## 11. Demo script (< 90 s — target 80 s, captions on)

| Part | Time | Show / say |
|---|---|---|
| Problem | 0–10 s | "Would you give your AI agent your money? Agents don't always follow instructions." |
| Solution | 10–18 s | "Errand Guard: your agent runs real errands, and can't pay for anything you didn't ask for." |
| Demo | 18–65 s | Japan eSIM errand → mandate confirm → agent tries the 20 GB / $35 plan → **BLOCKED** (pause 2 s) → corrected → bought → QR receipt → second errand: $25 Amazon gift card to a friend's email → `/errands` dashboard |
| How | 65–75 s | "A `pre_tool_call` hook checks every payment against your mandate — the model can't skip it." |
| Close | 75–80 s | "Errands, not accidents." Tag @NousResearch. |

## 12. Submission checklist

- [ ] Public repo `softalpha0/errand-guard` (MIT), README with screenshots, `.env.example`, no keys
- [ ] `hermes plugins validate . --install-deps` passes at the pinned commit
- [ ] PR to `NousResearch/hermes-agent` adding **only** `plugin-catalog/errand-guard.yaml` (name, title, description, repo, 40-char `sha`, `tier: community`, `category: tools`, maintainer, `capabilities` incl. tools/hooks/env, `requires_hermes`, quoted `version`, screenshots)
- [ ] PR description: what it does, Hermes surfaces used (tools, `pre_tool_call`, `post_tool_call`, `/errands`, dashboard), **risk disclosures** (spends real money via Bitrefill; stores order/redemption data locally; browser checkout acts in the user's own logged-in session)
- [ ] Demo < 90 s posted on X, tagging @NousResearch
- [ ] Typeform submitted with both links before **Wed 11 PM Lagos**
- [ ] Tell Claude "we submitted" (tracker)

## 13. Risks & fallbacks

| Risk | Fallback |
|---|---|
| Bitrefill balance payment not available on personal API | Return the Lightning invoice for the user to pay; guard still approves/blocks the invoice creation |
| Browser checkout (Amazon, sushi) breaks or is blocked by the site | Use Amazon / food-delivery **gift cards** via Bitrefill; keep browser guard as "beta" |
| Site terms on automated purchasing | Browser path always runs in the user's own session and always escalates to human approval before the final click |
| Model parses the mandate wrong | User must confirm the mandate before it's active; mandate shown in plain words |
| Catalog review timing | Open the PR early (Tue); the form can be submitted as soon as the PR is open |

## 14. Parking lot (what's next)

Recurring errands ("top up my phone plan every month") · household shared budgets · more providers (local food delivery, bills) · guard any wallet tool via PayGuard · spending insights.
