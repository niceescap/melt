"""Tests state (base en mémoire)."""

from __future__ import annotations

import json

import pytest

import state


@pytest.fixture
def conn():
    c = state.init_db(":memory:")
    yield c
    c.close()


def test_init_and_last_uid(conn):
    assert state.get_last_uid(conn) == 0
    state.set_last_uid(conn, 42)
    assert state.get_last_uid(conn) == 42


def test_insert_and_is_seen(conn):
    assert state.is_seen(conn, 1) is False
    state.insert_pending(conn, 1, "<m1@x>", "a@b.c", "sujet")
    assert state.is_seen(conn, 1) is True
    row = conn.execute("SELECT status FROM received WHERE uid = 1").fetchone()
    assert row["status"] == "pending"


def test_insert_pending_idempotent(conn):
    state.insert_pending(conn, 1, "<m1@x>", "a@b.c", "sujet")
    state.mark(conn, 1, "replied", reply_body="ok", replied=True)
    state.insert_pending(conn, 1, "<m1@x>", "a@b.c", "sujet")
    row = conn.execute("SELECT status FROM received WHERE uid = 1").fetchone()
    assert row["status"] == "replied"


def test_mark_with_tool_trace(conn):
    state.insert_pending(conn, 2, "<m2@x>", "a@b.c", "sujet")
    trace = [{"name": "read_kb", "result_chars": 120}]
    state.mark(conn, 2, "replied", reply_body="ok", replied=True, tool_trace=trace)
    row = conn.execute("SELECT tool_trace, status FROM received WHERE uid = 2").fetchone()
    assert row["status"] == "replied"
    assert json.loads(row["tool_trace"]) == trace


def test_reset_stale_processing(conn):
    state.insert_pending(conn, 3, "<m3@x>", "a@b.c", "sujet")
    state.mark(conn, 3, "processing")
    conn.execute("UPDATE received SET received_at = '2000-01-01T00:00:00+00:00' WHERE uid = 3")
    conn.commit()
    reset = state.reset_stale_processing(conn, older_seconds=300)
    assert reset == 1
    row = conn.execute("SELECT status FROM received WHERE uid = 3").fetchone()
    assert row["status"] == "pending"


def test_stats(conn):
    state.insert_pending(conn, 1, "<m1@x>", "a@b.c", "s")
    state.mark(conn, 1, "replied", reply_body="ok", replied=True)
    state.insert_pending(conn, 2, "<m2@x>", "c@d.e", "s")
    state.mark(conn, 2, "filtered")
    result = state.stats(conn)
    assert result["today"].get("replied") == 1
    assert result["today"].get("filtered") == 1
    assert result["total"] == 2
    assert "7d" in result and "30d" in result


def test_stats_no_conn(tmp_path):
    result = state.stats(db_path=str(tmp_path / "empty.sqlite"))
    assert result["total"] == 0


def test_last_replied_at(conn):
    state.insert_pending(conn, 1, "<m1@x>", "a@b.c", "s")
    state.mark(conn, 1, "replied", reply_body="ok", replied=True)
    assert state.last_replied_at(conn, "a@b.c") is not None
    assert state.last_replied_at(conn, "zzz@b.c") is None