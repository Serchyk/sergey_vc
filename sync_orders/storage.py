"""SQLite persistence. The stored result is always a full recomputation of the
source snapshot inside a single transaction, which makes repeated syncs
idempotent: same input -> same rows, no duplicated clients."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .core import SyncResult, Stats, group_winners, parse_order

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    phone        TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    email        TEXT NOT NULL DEFAULT '',
    amount       REAL NOT NULL DEFAULT 0,
    source_id    TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS raw_orders (
    row_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    phone_raw     TEXT NOT NULL DEFAULT '',
    phone         TEXT,
    email         TEXT NOT NULL DEFAULT '',
    amount_raw    TEXT,
    amount        REAL NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT '',
    is_valid      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sync_meta (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    received         INTEGER NOT NULL DEFAULT 0,
    unique_clients   INTEGER NOT NULL DEFAULT 0,
    duplicates_merged INTEGER NOT NULL DEFAULT 0,
    invalid_dropped  INTEGER NOT NULL DEFAULT 0,
    source_sha256    TEXT NOT NULL DEFAULT '',
    synced_at        TEXT NOT NULL DEFAULT '',
    runs             INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sync_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    source_sha256 TEXT NOT NULL DEFAULT '',
    stats_json  TEXT NOT NULL DEFAULT '{}'
);
"""


def payload_digest(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str = "data/sync.db"):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------ writes
    def replace_snapshot(self, raw_orders: List[Any], result: SyncResult, digest: str) -> None:
        """Atomically swap in a new snapshot: the outcome depends only on the
        source payload, never on how many times this was called before."""
        parsed = [parse_order(raw, index) for index, raw in enumerate(raw_orders)]
        winners = group_winners(parsed)

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                cur.execute("DELETE FROM clients")
                cur.execute("DELETE FROM raw_orders")
                cur.executemany(
                    """
                    INSERT INTO clients (phone, name, email, amount, source_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            client["phone"],
                            client["name"],
                            client["email"],
                            float(client["amount"])
                            if isinstance(client["amount"], (int, float))
                            else 0.0,
                            winners[client["phone"]].source_id,
                            winners[client["phone"]].created_at_text,
                        )
                        for client in result.clients
                    ],
                )
                cur.executemany(
                    """
                    INSERT INTO raw_orders
                        (source_id, name, phone_raw, phone, email, amount_raw, amount, created_at, is_valid)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            order.source_id,
                            order.name,
                            order.phone_raw,
                            order.phone,
                            order.email,
                            order.amount_raw
                            if isinstance(order.amount_raw, (str, int, float))
                            else str(order.amount_raw),
                            order.amount,
                            order.created_at_text,
                            1 if order.valid else 0,
                        )
                        for order in parsed
                    ],
                )
                cur.execute(
                    """
                    INSERT INTO sync_meta
                        (id, received, unique_clients, duplicates_merged, invalid_dropped,
                         source_sha256, synced_at, runs)
                    VALUES (1, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        received = excluded.received,
                        unique_clients = excluded.unique_clients,
                        duplicates_merged = excluded.duplicates_merged,
                        invalid_dropped = excluded.invalid_dropped,
                        source_sha256 = excluded.source_sha256,
                        synced_at = excluded.synced_at,
                        runs = sync_meta.runs + 1
                    """,
                    (
                        result.stats.received,
                        result.stats.unique,
                        result.stats.duplicates_merged,
                        result.stats.invalid_dropped,
                        digest,
                        _now(),
                    ),
                )
                cur.execute(
                    "INSERT INTO sync_runs (started_at, source_sha256, stats_json) VALUES (?, ?, ?)",
                    (
                        _now(),
                        digest,
                        json.dumps(result.stats.as_dict(), ensure_ascii=False),
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------- reads
    def load_result(self) -> Optional[SyncResult]:
        rows = self._conn.execute(
            "SELECT phone, name, email, amount FROM clients ORDER BY phone ASC"
        ).fetchall()
        meta = self._conn.execute("SELECT * FROM sync_meta WHERE id = 1").fetchone()
        if meta is None and not rows:
            return None
        clients = [
            {
                "name": row["name"],
                "phone": row["phone"],
                "email": row["email"],
                "amount": int(row["amount"]) if float(row["amount"]).is_integer() else row["amount"],
            }
            for row in rows
        ]
        stats = Stats(
            received=meta["received"] if meta else 0,
            unique=meta["unique_clients"] if meta else len(clients),
            duplicates_merged=meta["duplicates_merged"] if meta else 0,
            invalid_dropped=meta["invalid_dropped"] if meta else 0,
        )
        return SyncResult(clients=clients, stats=stats)

    def meta(self) -> Dict[str, Any]:
        row = self._conn.execute("SELECT * FROM sync_meta WHERE id = 1").fetchone()
        if row is None:
            return {}
        return dict(row)

    def invalid_orders(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT source_id, name, phone_raw,
                   CASE WHEN phone_raw = '' THEN 'empty' ELSE 'not 11 digits' END AS reason
            FROM raw_orders
            WHERE is_valid = 0
            ORDER BY rowid
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()