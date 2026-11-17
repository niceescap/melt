"""Tests filters."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import filters
import state


def test_is_fresh_recent():
    when = datetime.now(timezone.utc) - timedelta(seconds=30)
    parsed = {"date": when.strftime("%a, %d %b %Y %H:%M:%S +0000")}
    assert filters.is_fresh(parsed, 90) is True


def test_is_fresh_old():
    when = datetime.now(timezone.utc) - timedelta(seconds=300)
    parsed = {"date": when.strftime("%a, %d %b %Y %H:%M:%S +0000")}
    assert filters.is_fresh(parsed, 90) is False


def test_is_bounce_empty_return_path():
    assert filters.is_bounce({"return_path": "", "from_addr": "x@example.org"}) is True
    assert filters.is_bounce({"return_path": "<>", "from_addr": "x@example.org"}) is True


def test_is_bounce_mailer_daemon():
    parsed = {"return_path": "<x@example.org>", "from_addr": "mailer-daemon@example.org"}
    assert filters.is_bounce(parsed) is True


def test_is_bounce_normal():
    parsed = {"return_path": "<x@example.org>", "from_addr": "alice@example.org"}
    assert filters.is_bounce(parsed) is False


def test_is_bulk_precedence():
    assert filters.is_bulk({"precedence": "bulk", "list_unsubscribe": ""}) is True
    assert filters.is_bulk({"precedence": "list", "list_unsubscribe": ""}) is True


def test_is_bulk_list_unsubscribe():
    assert filters.is_bulk({"precedence": "", "list_unsubscribe": "<mailto:unsub@x>"}) is True


def test_is_bulk_normal():
    assert filters.is_bulk({"precedence": "", "list_unsubscribe": ""}) is False


def test_is_auto_submitted():
    assert filters.is_auto_submitted({"auto_submitted": "auto-replied"}) is True
    assert filters.is_auto_submitted({"auto_submitted": "no"}) is False
    assert filters.is_auto_submitted({"auto_submitted": ""}) is False


def test_is_whitelisted_case_insensitive():
    whitelist = ["partenaire@example.com"]
    assert filters.is_whitelisted({"from_addr": "Partenaire@Example.com"}, whitelist) is True
    assert filters.is_whitelisted({"from_addr": "inconnu@example.com"}, whitelist) is False


def test_is_whitelisted_empty_from():
    assert filters.is_whitelisted({"from_addr": ""}, ["a@b.c"]) is False


def test_is_throttled_recent_reply(tmp_path):
    conn = state.init_db(str(tmp_path / "db.sqlite"))
    state.insert_pending(conn, 1, "<m1@x>", "alice@example.org", "s")
    state.mark(conn, 1, "replied", reply_body="ok", replied=True)
    assert filters.is_throttled(conn, "alice@example.org", 24 * 3600) is True


def test_is_throttled_after_window(tmp_path):
    conn = state.init_db(str(tmp_path / "db.sqlite"))
    state.insert_pending(conn, 1, "<m1@x>", "alice@example.org", "s")
    state.mark(conn, 1, "replied", reply_body="ok", replied=True)
    conn.execute("UPDATE received SET replied_at = ?", ("2020-01-01T00:00:00+00:00",))
    conn.commit()
    assert filters.is_throttled(conn, "alice@example.org", 24 * 3600) is False


def test_is_throttled_no_history(tmp_path):
    conn = state.init_db(str(tmp_path / "db.sqlite"))
    assert filters.is_throttled(conn, "alice@example.org", 24 * 3600) is False