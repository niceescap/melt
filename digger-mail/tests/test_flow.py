"""Tests du flux de traitement complet (main.handle_message)."""

from __future__ import annotations

import main
import state


class FakeClient:
    def __init__(self, raw_by_uid):
        self.raw_by_uid = raw_by_uid
        self.fetch_calls = []

    def fetch(self, uids, _criteria=None):
        uid = int(uids[0])
        self.fetch_calls.append(uid)
        if uid in self.raw_by_uid:
            return {uid: {b"RFC822": self.raw_by_uid[uid]}}
        return {}


def _raw(from_addr, subject, body="Bonjour", extra=""):
    return (
        f"From: {from_addr}\r\n"
        f"Subject: {subject}\r\n"
        "Message-ID: <new@example.org>\r\n"
        "Return-Path: <bounce@example.org>\r\n"
        + extra
        + "\r\n"
        + body
        + "\r\n"
    ).encode("utf-8")


def _client_for(raw, uid=1):
    return FakeClient({uid: raw})


def _use_db(monkeypatch, tmp_path):
    conn = state.init_db(str(tmp_path / "d.sqlite"))
    monkeypatch.setattr(main.state, "init_db", lambda *a, **k: conn)
    return conn


def test_whitelisted_triggers_llm_and_smtp(monkeypatch, cfg, tmp_path):
    conn = _use_db(monkeypatch, tmp_path)

    sent = []
    monkeypatch.setattr(main.sender, "send", lambda *a, **k: sent.append(k) or "<mid@x>")
    monkeypatch.setattr(main.llm_handler, "call_llm",
                        lambda cfg, prompt: ("Réponse du LLM", []))

    raw = _raw("partenaire@example.com", "Nouvelle question")
    main.handle_message(_client_for(raw), conn, cfg, 1, dry_run=False, at_seed=False)

    row = conn.execute("SELECT status, reply_body, tool_trace FROM received WHERE uid=1").fetchone()
    assert row["status"] == "replied"
    assert row["reply_body"] == "Réponse du LLM"
    assert row["tool_trace"] is None
    assert len(sent) == 1


def test_non_whitelisted_sends_refuse(monkeypatch, cfg, tmp_path):
    conn = _use_db(monkeypatch, tmp_path)

    sent = []
    monkeypatch.setattr(main.sender, "send", lambda *a, **k: sent.append(k) or "<mid@x>")

    raw = _raw("inconnu@example.org", "Bonjour")
    main.handle_message(_client_for(raw), conn, cfg, 1, dry_run=False, at_seed=False)

    row = conn.execute("SELECT status, reply_body FROM received WHERE uid=1").fetchone()
    assert row["status"] == "replied"
    assert "service sur inscription" in row["reply_body"]
    assert len(sent) == 1


def test_auto_submitted_filtered(monkeypatch, cfg, tmp_path):
    conn = _use_db(monkeypatch, tmp_path)

    called = {"llm": 0}

    def no_send(*a, **k):
        raise AssertionError("pas d'envoi attendu")

    monkeypatch.setattr(main.sender, "send", no_send)
    monkeypatch.setattr(main.llm_handler, "call_llm",
                        lambda *a, **k: called.__setitem__("llm", called["llm"] + 1))

    raw = _raw("partenaire@example.com", "Test", extra="Auto-Submitted: auto-replied\r\n")
    main.handle_message(_client_for(raw), conn, cfg, 1, dry_run=False, at_seed=False)

    row = conn.execute("SELECT status FROM received WHERE uid=1").fetchone()
    assert row["status"] == "filtered"
    assert called["llm"] == 0


def test_bulk_filtered(monkeypatch, cfg, tmp_path):
    conn = _use_db(monkeypatch, tmp_path)

    def no_send(*a, **k):
        raise AssertionError("pas d'envoi attendu")

    monkeypatch.setattr(main.sender, "send", no_send)

    raw = _raw("partenaire@example.com", "Test", extra="Precedence: bulk\r\n")
    main.handle_message(_client_for(raw), conn, cfg, 1, dry_run=False, at_seed=False)

    row = conn.execute("SELECT status FROM received WHERE uid=1").fetchone()
    assert row["status"] == "filtered"


def test_second_unknown_email_throttled(monkeypatch, cfg, tmp_path):
    conn = _use_db(monkeypatch, tmp_path)

    sent = []
    monkeypatch.setattr(main.sender, "send", lambda *a, **k: sent.append(k) or "<mid@x>")

    raw1 = _raw("inconnu@example.org", "Un")
    main.handle_message(_client_for(raw1), conn, cfg, 1, dry_run=False, at_seed=False)
    assert len(sent) == 1

    raw2 = _raw("inconnu@example.org", "Deux")
    main.handle_message(_client_for(raw2, uid=2), conn, cfg, 2, dry_run=False, at_seed=False)

    assert len(sent) == 1
    row = conn.execute("SELECT status FROM received WHERE uid=2").fetchone()
    assert row["status"] == "throttled"


def test_llm_error_marks_failed(monkeypatch, cfg, tmp_path):
    conn = _use_db(monkeypatch, tmp_path)

    def boom(cfg, prompt):
        raise main.llm_handler.LLMError("timeout")

    monkeypatch.setattr(main.llm_handler, "call_llm", boom)

    def no_send(*a, **k):
        raise AssertionError("pas d'envoi attendu")

    monkeypatch.setattr(main.sender, "send", no_send)

    raw = _raw("partenaire@example.com", "Question")
    main.handle_message(_client_for(raw), conn, cfg, 1, dry_run=False, at_seed=False)

    row = conn.execute("SELECT status, error FROM received WHERE uid=1").fetchone()
    assert row["status"] == "failed"
    assert "timeout" in row["error"]


def test_kb_absent_still_replies(monkeypatch, cfg, tmp_path):
    conn = _use_db(monkeypatch, tmp_path)

    # KB absente : l'outil renvoie une erreur texte au LLM, qui répond quand même
    # en signalant qu'il n'a pas l'information. Aucun appel réseau ici.
    cfg.llm.tools["kb_path"] = str(tmp_path / "absent.md")
    sent = []
    monkeypatch.setattr(main.sender, "send", lambda *a, **k: sent.append(k) or "<mid@x>")
    monkeypatch.setattr(
        main.llm_handler, "call_llm",
        lambda cfg, prompt: ("Je n'ai pas cette information dans ma base.", []),
    )

    raw = _raw("partenaire@example.com", "Question DiggerRadio")
    main.handle_message(_client_for(raw), conn, cfg, 1, dry_run=False, at_seed=False)

    row = conn.execute("SELECT status FROM received WHERE uid=1").fetchone()
    assert row["status"] == "replied"
    assert len(sent) == 1


def test_dry_run_does_not_call_smtp(monkeypatch, cfg, tmp_path):
    conn = _use_db(monkeypatch, tmp_path)

    def fake_send(cfg, to, subject, body, html_body=None, parsed=None, dry_run=False):
        assert dry_run is True
        return "<mid@x>"

    monkeypatch.setattr(main.sender, "send", fake_send)
    monkeypatch.setattr(main.llm_handler, "call_llm",
                        lambda cfg, prompt: ("Réponse", []))

    raw = _raw("partenaire@example.com", "Question")
    main.handle_message(_client_for(raw), conn, cfg, 1, dry_run=True, at_seed=False)
    row = conn.execute("SELECT status FROM received WHERE uid=1").fetchone()
    assert row["status"] == "replied"


def test_already_seen_uid_is_skipped(monkeypatch, cfg, tmp_path):
    conn = _use_db(monkeypatch, tmp_path)
    state.insert_pending(conn, 5, "<m5@x>", "a@b.c", "sujet")
    assert state.is_seen(conn, 5) is True