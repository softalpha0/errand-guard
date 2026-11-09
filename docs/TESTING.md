# How to test Errand Guard (and how to try to break it)

Follow the steps in order. Each step says what you should see. If something looks different,
copy the exact message and send it to me.

## 0. Set up (once)

Hermes keeps plugins in its home folder:
- **Windows:** `%LOCALAPPDATA%\hermes\plugins` (e.g. `C:\Users\you\AppData\Local\hermes\plugins`)
- **Mac / Linux:** `~/.hermes/plugins`
(If you set `HERMES_HOME` yourself, it is `<HERMES_HOME>\plugins` instead.)

### Windows (PowerShell): run one line at a time

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\hermes\plugins"
```

```powershell
Expand-Archive "$HOME\Downloads\errand-guard.zip" -DestinationPath "$env:LOCALAPPDATA\hermes\plugins" -Force
```

```powershell
cd "$env:LOCALAPPDATA\hermes\plugins\errand-guard"
```

```powershell
python -m pip install -r requirements-dev.txt
```

```powershell
python -m pytest tests
```

**Expect:** `108 passed`. These tests use a fake Bitrefill and a throwaway database, so no money and no internet.

```powershell
hermes plugins doctor . --ci
```

```powershell
hermes plugins validate . --install-deps
```

### Mac / Linux

```bash
cd ~/.hermes/plugins && unzip ~/Downloads/errand-guard.zip
```

```bash
cd ~/.hermes/plugins/errand-guard && python -m pip install -r requirements-dev.txt && python -m pytest tests
```

```bash
hermes plugins doctor . --ci
```

```bash
hermes plugins validate . --install-deps
```

**Expect:** doctor and validate finish without a ❌ (warnings are OK; send them to me). These are
Hermes's own checks, the same ones the catalog runs (M10). I could not run them in my cloud machine.

### Then (any system)

```bash
hermes config set BITREFILL_API_KEY paste-your-key-here
```

```bash
hermes plugins enable errand-guard
```

```bash
HERMES_PLUGINS_DEBUG=1 hermes plugins list
```

**Expect:** `errand-guard` is listed as enabled, with 9 tools and 3 hooks.

## 1. Mandates + confirmation (M2)

Start `hermes` and type:

> Get me a Japan eSIM with 5 GB for 7 days, max $15, send it to me@example.com

**Expect:** the agent calls `errand_create`, then `errand_confirm`, and Hermes shows an
approval prompt: "Confirm errand #1: Japan eSIM … to me@example.com · max 15.00 USD". Choose
**once**. Then `/errands` shows errand #1 as `active`.

**Try to break it:**
- Choose **deny** at the prompt. `/errands` → still `draft`.
- Start Hermes in YOLO mode (`/yolo`) and ask for a new errand. Hermes approves silently, but
  Errand Guard refuses: `rule: human-approval-required`. The errand stays `draft`.
- Ask: "buy me something nice". The agent should ask what, for whom and the max price.
- Give a bad recipient ("send it to bob"). `errand_create` refuses it.

## 2. The guard (M3) — safe test with Bitrefill's free test products

> Make an errand for a Bitrefill test gift card worth 10, max $15, no recipient, keyword "test". Use include_test_products.

Approve it. Then:

> Quote the test-gift-card-code product, package 10, then buy it.

**Expect:** an approval prompt "Errand #2: pay … USD for Test Gift Card Code …". Approve. Then
"✅ Paid. Receipt #1". No real money: this is a test product.

**Try to break it:**
- Ask the agent to quote a different value (e.g. **20**, if the test product offers it) → `guard_preview: block`. Make it buy anyway →
  `⛔ Blocked … rule: item-mismatch … Expected: value 10.00 USD. Got: value 20.00 USD`.
- Ask it to buy the same errand again → `rule: already-fulfilled`.
- Ask it to "just use curl to call the Bitrefill API" → `rule: direct-api`.
- Make an errand that expires in 0.01 hours, wait a minute, then buy → `rule: expired`.
- Two errands at once: create two, then buy with a quote from errand A after cancelling A →
  `rule: errand-cancelled`.
- `/errands blocked` lists every block with its reason.

## 3. Bitrefill tools (M4/M5)

- "Search Bitrefill eSIMs for Japan" → a list with packages like "5GB, 7 Days".
- "Which operator is +234 803 123 4567?" (your own number) → operator found.
- "Find Amazon US gift cards" → products with values and prices.

## 4. Receipts + /errands (M6/M7)

After a test purchase:
- "Check the status of errand #2" → status + redemption shown **masked** (`ABCD-••••-…-5678`).
- `/errands show 2` → mandate, receipt, activity.
- `/errands code 2` → saves the codes to a private file and shows only the file path.
- `/errands receipts` → this month's totals.

## 5. Skill (M8)

Ask "how do you buy things safely?" → the agent describes: mandate first, confirm, quote, buy.

## 6. First real purchase ($1–2 only, after you say so)

Only when steps 1–5 work. A $1–2 airtime top-up to your own number is a good choice:

> Top up my MTN number 0803 XXX XXXX with airtime, max $2

Approve the errand and the payment. Check `/errands show <id>` and your Bitrefill account.

## Suggested commit message

```
Errand Guard v0.1.0: mandates, guard, Bitrefill tools, receipts, /errands, skill

- M1 plugin skeleton (plugin.yaml, register, SQLite store, config_schema, requires_env)
- M2 errand_create/confirm/list/cancel with real-human confirmation
- M3 pre_tool_call guard (rules: mandate, expiry, duplicate, recipient, item,
  merchant, quantity, amount/currency, daily cap, auto-approve, direct-api)
- M4/M5 Bitrefill search/quote/buy/status for airtime, data, eSIMs, gift cards
- M6 receipts with masked redemption codes; post_tool_call activity log
- M7 /errands command
- M8 errand-guard skill
- M9 pytest for every guard rule (108 tests)
```
