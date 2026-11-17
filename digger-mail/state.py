"""Persistance SQLite : état des messages et statistiques."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

DB_PATH = os.getenv("DIGGER_MAIL_DB", "digger_mail.sqlite3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS received (
    uid INTEGER PRIMARY KEY,
    message_id TEXT,
    from_addr TEXT,
    subject TEXT,
    received_at TEXT,
    status TEXT,
    reply_body TEXT,
    replied_at TEXT,
    error TEXT,
    tool_trace TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_received_status
    ON received(status);
CREATE INDEX IF NOT EXISTS idx_received_from
    ON received(from_addr, replied_at);
"""


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Crée le schéma si nécessaire et retourne la connexion."""
    conn = _connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------- meta / uid

def get_meta(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def get_last_uid(conn: sqlite3.Connection) -> int:
    value = get_meta(conn, "last_uid_seen")
    return int(value) if value is not None else 0


def set_last_uid(conn: sqlite3.Connection, uid: int) -> None:
    set_meta(conn, "last_uid_seen", str(int(uid)))


# ------------------------------------------------------------------ messages

def is_seen(conn: sqlite3.Connection, uid: int) -> bool:
    row = conn.execute("SELECT 1 FROM received WHERE uid = ?", (uid,)).fetchone()
    return row is not None


def insert_pending(
    conn: sqlite3.Connection,
    uid: int,
    message_id: Optional[str],
    from_addr: Optional[str],
    subject: Optional[str],
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO received(uid, message_id, from_addr, subject, "
        "received_at, status) VALUES(?, ?, ?, ?, ?, 'pending')",
        (uid, message_id, from_addr, subject, _now_iso()),
    )
    conn.commit()


def mark(
    conn: sqlite3.Connection,
    uid: int,
    status: str,
    reply_body: Optional[str] = None,
    replied: bool = False,
    error: Optional[str] = None,
    tool_trace: Optional[List[Dict[str, Any]]] = None,
) -> None:
    conn.execute(
        "UPDATE received SET status = ?, reply_body = ?, replied_at = ?, "
        "error = ?, tool_trace = ? WHERE uid = ?",
        (
            status,
            reply_body,
            _now_iso() if replied else None,
            error,
            json.dumps(tool_trace, ensure_ascii=False) if tool_trace else None,
            uid,
        ),
    )
    conn.commit()


def reset_stale_processing(conn: sqlite3.Connection, older_seconds: int = 300) -> int:
    """Remet en pending les lignes restées processing (crash en cours de run)."""
    cutoff = _iso_seconds_ago(older_seconds)
    cur = conn.execute(
        "UPDATE received SET status = 'pending' "
        "WHERE status = 'processing' AND received_at < ?",
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount


# -------------------------------------------------------------------- stats

def stats(conn: Optional[sqlite3.Connection] = None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Décompte par statut sur aujourd'hui, 7 jours et 30 jours."""
    owns = conn is None
    if owns:
        conn = init_db(db_path)

    windows = {
        "today": 1,
        "7d": 7,
        "30d": 30,
    }
    result: Dict[str, Any] = {}
    for label, days in windows.items():
        cutoff = _iso_seconds_ago(max(0, days - 1) * 86400)
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM received "
            "WHERE received_at >= ? GROUP BY status",
            (cutoff,),
        ).fetchall()
        result[label] = {row["status"]: row["n"] for row in rows}
    total = conn.execute("SELECT COUNT(*) AS n FROM received").fetchone()["n"]
    result["total"] = total

    if owns:
        conn.close()
    return result


# ------------------------------------------------------------------- utils

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _iso_seconds_ago(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).replace(microsecond=0).isoformat()


def last_replied_at(conn: sqlite3.Connection, from_addr: str) -> Optional[datetime]:
    row = conn.execute(
        "SELECT replied_at FROM received WHERE from_addr = ? AND status = 'replied' "
        "AND replied_at IS NOT NULL ORDER BY replied_at DESC LIMIT 1",
        (from_addr,),
    ).fetchone()
    if not row or not row["replied_at"]:
        return None
    try:
        return datetime.fromisoformat(row["replied_at"])
    except ValueError:
        return None