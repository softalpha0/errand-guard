# Project: Errand Guard for Hermes

Problem: People won't let AI agents pay for things because agents don't always follow instructions — one wrong amount, item or recipient costs real money.
User: Hermes Agent users who want real-world errands done (airtime, data, eSIMs, gift cards, food, online shopping), mostly on a laptop; approvals sometimes from Telegram.
Done statement: A user tells Hermes "get me a Japan eSIM with 5 GB for 7 days, max $15, send it to me@example.com"; Errand Guard turns it into a mandate the user confirms; the agent buys it; any purchase that doesn't match the mandate is blocked before payment with a clear reason; the receipt appears in /errands and the dashboard.
Must-have features (in order): M1 plugin skeleton · M2 mandate tools + confirmation · M3 pre_tool_call guard · M9 guard tests · M4 Bitrefill tools · M5 airtime/data/eSIM/gift cards · M6 receipts (post_tool_call) · M7 /errands · M8 skill · M10 hermes plugins validate passes.
Should/Could: see PRD.md §6 — only after every Must works and is committed.
Tools: Python Hermes native plugin (plugin.yaml + __init__.py register(ctx)), SQLite, Bitrefill Personal API (Bearer key from env), pytest. Read the Hermes plugin developer guide, hooks guide and catalog-submission guide before building.

Rules:
- Full spec in PRD.md. Read it before planning.
- Write all code new for this plugin.
- pre_tool_call returns {"action": "block", "message": ...}, {"action": "approve", ...} or nothing. Block messages must name the rule and expected vs actual values.
- The guard must fail closed: if unsure, block.
- Never store card numbers. Mask redemption codes in chat.
- Real purchases only $1–2, and only after I approve.
- I'm not a developer. Explain things in plain language.
- Plan first; wait for my approval before writing code.
- Build one feature at a time; tell me how to test it and how to try to break it; write a pytest for every guard rule.
- Never put API keys in code. Use .env and keep .env.example updated.
- Explain errors before fixing them.
- Commit after each feature that works.
- Feature freeze: Wed Nov 11, 10:00 Lagos.
- Add anything important you learn about the project here.

## Learned while building (2026-11-09)
- Hermes auto-approves `{"action": "approve"}` in YOLO mode, with `approvals.mode: off`, and via "smart" AI approval. So Errand Guard records real answers via the `post_approval_response` hook (choice once/session/always, no `decided_by`) and the tools (`errand_confirm`, `errand_buy`) refuse without a human-approved one-time ticket. Each prompt uses a unique `rule_key` (`errand-guard:<ticket>`) so an old "always" can't approve a new payment.
- If Hermes's hook dispatch itself errors, the tool still runs (model_tools.py logs and continues). So `errand_buy` re-runs every guard check itself before paying.
- Tool handlers receive `task_id`, `session_id`, `conversation_id` (not `tool_call_id`). Errands are scoped to `conversation_id` (falls back to `session_id`).
- Catalog rule 11: read secrets only with `agent.secret_scope.get_secret`, never `os.environ` or .env files. Users set the key with `hermes config set BITREFILL_API_KEY ...`.
- The install scanner marks a plugin "dangerous" if any file contains the literal Hermes secrets-file path (home/.hermes/.env), shell commands that print .env files, hardcoded keys, etc. Don't write those strings. Never commit `__pycache__`/`.pyc` (rule 17).
- Store data with `plugins.plugin_storage.plugin_db("errand-guard")` (survives updates), never in the plugin folder.
- Bitrefill: base `https://api.bitrefill.com/v2`, Bearer personal key, pay from balance is supported (create unpaid invoice = quote, then `/invoices/{id}/pay`). eSIMs use `/esims` endpoints. Free test products: `test-gift-card-code`, `test-esim`. Details in docs/bitrefill-api-notes.md.
- Run tests with `python -m pytest tests` (the pytest.ini lives in tests/ because the plugin root has an `__init__.py`).
- `requires_hermes: ">=0.22.7"` (same floor as other approval-gated payment plugins in the catalog); set the same in the catalog entry.
- The user pushes code themselves: deliver a zip, no git commits by Claude, no Claude co-author lines.
- The user is on **Windows + PowerShell 5**: no `&&`, no `unzip`. Give PowerShell commands one per block (`Expand-Archive`, `New-Item`). Hermes home on Windows is `%LOCALAPPDATA%\hermes` (not `~/.hermes`), so plugins go in `%LOCALAPPDATA%\hermes\plugins`.
- M10 passed on the user's laptop (Hermes v0.23.0, Windows): `hermes plugins doctor . --ci` OK, `hermes plugins validate . --install-deps` passed, security scan safe.
- Always open text files with `encoding="utf-8"` (Windows defaults to cp1252) and tolerate `\r\n` line endings.
