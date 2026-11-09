"""Local SQLite store: errands (mandates), quotes, receipts, approvals, activity log.

The database lives in Hermes's per-plugin data folder (it survives plugin updates),
never in the plugin folder. Card numbers are never stored: the plugin never sees them.
Redemption codes are stored here, locally only.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from .settings import PLUGIN_NAME

SCHEMA = """
CREATE TABLE IF NOT EXISTS errands (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL DEFAULT '',
    session_id      TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    expires_at      REAL NOT NULL,
    status          TEXT NOT NULL,          -- draft | active | buying | fulfilled | cancelled | payment_unknown
    kind            TEXT NOT NULL,
    item            TEXT NOT NULL,
    product_id      TEXT NOT NULL DEFAULT '',
    keywords        TEXT NOT NULL DEFAULT '[]',
    data_gb         TEXT,
    duration_days   INTEGER,
    face_value      TEXT,
    face_currency   TEXT NOT NULL DEFAULT '',
    recipient       TEXT NOT NULL DEFAULT '',
    recipient_type  TEXT NOT NULL DEFAULT 'none',
    merchants       TEXT NOT NULL DEFAULT '["bitrefill"]',
    max_amount      TEXT NOT NULL,
    currency        TEXT NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    confirmed_at    REAL,
    fulfilled_at    REAL,
    cancelled_at    REAL
);
CREATE TABLE IF NOT EXISTS quotes (
    id              TEXT PRIMARY KEY,
    errand_id       INTEGER NOT NULL,
    conversation_id TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    status          TEXT NOT NULL,          -- quoted | paying | paid | failed | unknown
    purchase        TEXT NOT NULL,          -- JSON: the normalised purchase the guard checks
    invoice_id      TEXT NOT NULL DEFAULT '',
    raw             TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS receipts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    errand_id       INTEGER NOT NULL,
    quote_id        TEXT NOT NULL,
    invoice_id      TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    status          TEXT NOT NULL,
    amount          TEXT NOT NULL DEFAULT '',
    currency        TEXT NOT NULL DEFAULT '',
    item            TEXT NOT NULL DEFAULT '',
    recipient       TEXT NOT NULL DEFAULT '',
    order_ids       TEXT NOT NULL DEFAULT '[]',
    redemption      TEXT NOT NULL DEFAULT '{}'   -- local only; masked whenever shown
);
CREATE TABLE IF NOT EXISTS approvals (
    ticket          TEXT PRIMARY KEY,
    fingerprint     TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    errand_id       INTEGER,
    conversation_id TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    status          TEXT NOT NULL,          -- pending | human_approved | denied | used
    choice          TEXT NOT NULL DEFAULT '',
    answered_at     REAL
);
CREATE TABLE IF NOT EXISTS activity (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      REAL NOT NULL,
    errand_id       INTEGER,
    conversation_id TEXT NOT NULL DEFAULT '',
    tool_name       TEXT NOT NULL,
    outcome         TEXT NOT NULL,          -- blocked | approval_asked | allowed | denied | ok | error
    rule            TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_errands_conv ON errands(conversation_id, status);
CREATE INDEX IF NOT EXISTS idx_approvals_fp ON approvals(fingerprint, status);
CREATE INDEX IF NOT EXISTS idx_activity_errand ON activity(errand_id);
"""

ERRAND_JSON_FIELDS = ("keywords", "merchants")


class Store:
    """Thread-safe wrapper around one SQLite connection."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---- low level -------------------------------------------------------
    def _run(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def _one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    # ---- errands ---------------------------------------------------------
    @staticmethod
    def _errand_out(row: Optional[dict]) -> Optional[dict]:
        if row is None:
            return None
        for key in ERRAND_JSON_FIELDS:
            try:
                row[key] = json.loads(row.get(key) or "[]")
            except (TypeError, ValueError):
                row[key] = []
        return row

    def create_errand(self, fields: dict) -> dict:
        data = dict(fields)
        for key in ERRAND_JSON_FIELDS:
            data[key] = json.dumps(list(data.get(key) or []))
        data.setdefault("created_at", time.time())
        data.setdefault("status", "draft")
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        cur = self._run(f"INSERT INTO errands ({cols}) VALUES ({marks})", data.values())
        return self.get_errand(cur.lastrowid)

    def get_errand(self, errand_id: Any) -> Optional[dict]:
        try:
            eid = int(errand_id)
        except (TypeError, ValueError):
            return None
        return self._errand_out(self._one("SELECT * FROM errands WHERE id = ?", (eid,)))

    def list_errands(self, *, conversation_id: Optional[str] = None, limit: int = 20) -> list[dict]:
        if conversation_id is None:
            rows = self._all("SELECT * FROM errands ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = self._all("SELECT * FROM errands WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                             (conversation_id, limit))
        return [self._errand_out(r) for r in rows]

    def active_errands(self, conversation_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM errands WHERE conversation_id = ? AND status = 'active' "
                         "ORDER BY id DESC", (conversation_id,))
        return [self._errand_out(r) for r in rows]

    def set_errand_status(self, errand_id: int, status: str, *, expect: Optional[Iterable[str]] = None,
                          **extra: Any) -> bool:
        """Change status. With ``expect``, only when the current status is one of those
        (compare-and-set, so two parallel buys cannot both start). Returns True if changed."""
        sets = ["status = ?"] + [f"{k} = ?" for k in extra]
        params: list[Any] = [status, *extra.values()]
        sql = f"UPDATE errands SET {', '.join(sets)} WHERE id = ?"
        params.append(int(errand_id))
        if expect is not None:
            expected = list(expect)
            sql += f" AND status IN ({', '.join('?' for _ in expected)})"
            params.extend(expected)
        return self._run(sql, params).rowcount == 1

    # ---- quotes ----------------------------------------------------------
    def add_quote(self, quote_id: str, errand_id: int, conversation_id: str, purchase: dict,
                  invoice_id: str = "", raw: Optional[dict] = None) -> dict:
        self._run("INSERT INTO quotes (id, errand_id, conversation_id, created_at, status, purchase, "
                   "invoice_id, raw) VALUES (?, ?, ?, ?, 'quoted', ?, ?, ?)",
                   (quote_id, int(errand_id), conversation_id, time.time(), json.dumps(purchase),
                    invoice_id, json.dumps(raw or {})))
        return self.get_quote(quote_id)

    def get_quote(self, quote_id: Any) -> Optional[dict]:
        if not isinstance(quote_id, str) or not quote_id:
            return None
        row = self._one("SELECT * FROM quotes WHERE id = ?", (quote_id,))
        if row is None:
            return None
        try:
            row["purchase"] = json.loads(row["purchase"])
        except (TypeError, ValueError):
            row["purchase"] = None
        try:
            row["raw"] = json.loads(row["raw"])
        except (TypeError, ValueError):
            row["raw"] = {}
        return row

    def set_quote_status(self, quote_id: str, status: str, *, expect: Optional[Iterable[str]] = None) -> bool:
        sql = "UPDATE quotes SET status = ? WHERE id = ?"
        params: list[Any] = [status, quote_id]
        if expect is not None:
            expected = list(expect)
            sql += f" AND status IN ({', '.join('?' for _ in expected)})"
            params.extend(expected)
        return self._run(sql, params).rowcount == 1

    # ---- receipts --------------------------------------------------------
    def add_receipt(self, *, errand_id: int, quote_id: str, invoice_id: str, status: str, amount: str,
                    currency: str, item: str, recipient: str, order_ids: list[str],
                    redemption: dict) -> dict:
        now = time.time()
        cur = self._run(
            "INSERT INTO receipts (errand_id, quote_id, invoice_id, created_at, updated_at, status, amount, "
            "currency, item, recipient, order_ids, redemption) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(errand_id), quote_id, invoice_id, now, now, status, amount, currency, item, recipient,
             json.dumps(order_ids), json.dumps(redemption)))
        return self.get_receipt(cur.lastrowid)

    def update_receipt(self, receipt_id: int, *, status: Optional[str] = None,
                       order_ids: Optional[list[str]] = None, redemption: Optional[dict] = None) -> None:
        sets, params = ["updated_at = ?"], [time.time()]
        if status is not None:
            sets.append("status = ?"); params.append(status)
        if order_ids is not None:
            sets.append("order_ids = ?"); params.append(json.dumps(order_ids))
        if redemption is not None:
            sets.append("redemption = ?"); params.append(json.dumps(redemption))
        params.append(int(receipt_id))
        self._run(f"UPDATE receipts SET {', '.join(sets)} WHERE id = ?", params)

    @staticmethod
    def _receipt_out(row: Optional[dict]) -> Optional[dict]:
        if row is None:
            return None
        for key, empty in (("order_ids", []), ("redemption", {})):
            try:
                row[key] = json.loads(row.get(key) or json.dumps(empty))
            except (TypeError, ValueError):
                row[key] = empty
        return row

    def get_receipt(self, receipt_id: Any) -> Optional[dict]:
        return self._receipt_out(self._one("SELECT * FROM receipts WHERE id = ?", (receipt_id,)))

    def receipts_for_errand(self, errand_id: int) -> list[dict]:
        return [self._receipt_out(r) for r in
                self._all("SELECT * FROM receipts WHERE errand_id = ? ORDER BY id", (int(errand_id),))]

    def list_receipts(self, limit: int = 20) -> list[dict]:
        return [self._receipt_out(r) for r in
                self._all("SELECT * FROM receipts ORDER BY id DESC LIMIT ?", (limit,))]

    def spent_since(self, since: float, currency: str) -> list[dict]:
        return self._all("SELECT amount, currency FROM receipts WHERE created_at >= ? AND upper(currency) = ? "
                         "AND status NOT IN ('failed')", (since, currency.upper()))

    # ---- approvals -------------------------------------------------------
    def add_approval(self, ticket: str, fingerprint: str, tool_name: str, errand_id: Optional[int],
                     conversation_id: str) -> None:
        self._run("INSERT INTO approvals (ticket, fingerprint, tool_name, errand_id, conversation_id, "
                   "created_at, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                   (ticket, fingerprint, tool_name, errand_id, conversation_id, time.time()))

    def answer_approval(self, ticket: str, status: str, choice: str) -> bool:
        return self._run("UPDATE approvals SET status = ?, choice = ?, answered_at = ? "
                          "WHERE ticket = ? AND status = 'pending'",
                          (status, choice, time.time(), ticket)).rowcount == 1

    def consume_approval(self, fingerprint: str, max_age_seconds: float) -> Optional[dict]:
        """Atomically take the newest unused human approval for this exact call."""
        with self._lock:
            row = self._one("SELECT * FROM approvals WHERE fingerprint = ? AND status = 'human_approved' "
                            "AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
                            (fingerprint, time.time() - max_age_seconds))
            if row is None:
                return None
            changed = self._run("UPDATE approvals SET status = 'used' WHERE ticket = ? "
                                 "AND status = 'human_approved'", (row["ticket"],)).rowcount
            return row if changed == 1 else None

    def get_approval(self, ticket: str) -> Optional[dict]:
        return self._one("SELECT * FROM approvals WHERE ticket = ?", (ticket,))

    # ---- activity --------------------------------------------------------
    def log(self, *, tool_name: str, outcome: str, errand_id: Optional[int] = None, conversation_id: str = "",
            rule: str = "", message: str = "") -> None:
        self._run("INSERT INTO activity (created_at, errand_id, conversation_id, tool_name, outcome, rule, "
                   "message) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (time.time(), errand_id, conversation_id, tool_name, outcome, rule, message[:2000]))

    def activity(self, *, errand_id: Optional[int] = None, outcome: Optional[str] = None,
                 limit: int = 50) -> list[dict]:
        sql, params = "SELECT * FROM activity WHERE 1=1", []
        if errand_id is not None:
            sql += " AND errand_id = ?"; params.append(int(errand_id))
        if outcome is not None:
            sql += " AND outcome = ?"; params.append(outcome)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(limit)
        return self._all(sql, params)


# ---- process-wide instance ----------------------------------------------------
_store: Optional[Store] = None
_store_lock = threading.Lock()


def get_store() -> Store:
    """The plugin's store, opened on first use in Hermes's plugin data folder."""
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            from plugins.plugin_storage import plugin_db  # Hermes public helper
            _store = Store(plugin_db(PLUGIN_NAME))
    return _store


def use_store(store: Optional[Store]) -> None:
    """Tests: install a store (e.g. on a temp file) or reset with None."""
    global _store
    with _store_lock:
        _store = store


def open_file_store(path: Path) -> Store:
    return Store(sqlite3.connect(str(path), check_same_thread=False))
