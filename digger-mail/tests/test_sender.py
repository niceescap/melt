"""Tests sender : construction du message et headers."""

from __future__ import annotations

import sender


def test_build_message_headers(cfg):
    parsed = {"in_reply_to": "<orig@example.org>", "references": "<a@x> <orig@example.org>"}
    msg, msgid = sender.build_message(
        cfg, "alice@example.org", "Re: Sujet", "Corps", "<p>Corps</p>", parsed
    )
    assert msg["Auto-Submitted"] == "auto-replied"
    assert msg["In-Reply-To"] == "<orig@example.org>"
    assert msg["Message-ID"] == msgid
    assert msg["To"] == "alice@example.org"


def test_build_message_no_reply_headers(cfg):
    msg, _ = sender.build_message(cfg, "alice@example.org", "Sujet", "Corps", None, {})
    assert msg["In-Reply-To"] is None
    assert msg["Auto-Submitted"] == "auto-replied"


def test_send_dry_run(cfg, monkeypatch):
    called = {"smtp": False}

    def fake_envoyer(*args, **kwargs):
        called["smtp"] = True
        return "ok"

    monkeypatch.setattr(sender.send_mail, "envoyer", fake_envoyer)
    msgid = sender.send(cfg, "alice@example.org", "Sujet", "Corps", dry_run=True)
    assert called["smtp"] is False
    assert msgid


def test_send_invokes_smtp(cfg, monkeypatch):
    captured = {}

    def fake_envoyer(to, subject, body, **kwargs):
        captured["to"] = to
        captured["subject"] = subject
        captured["message_id"] = kwargs.get("message_id")
        return "ok"

    monkeypatch.setattr(sender.send_mail, "envoyer", fake_envoyer)
    sender.send(cfg, "alice@example.org", "Sujet", "Corps", html_body="<p>Corps</p>",
                parsed={"in_reply_to": "<o@x>"}, dry_run=False)
    assert captured["to"] == "alice@example.org"
    assert captured["subject"] == "Sujet"