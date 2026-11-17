"""Tests sender : structure MIME, headers, logo inline."""

from __future__ import annotations

import sender

# PNG 1x1 valide, sert uniquement à vérifier la structure MIME.
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _html_parts(msg):
    return [p for p in msg.walk() if p.get_content_type() == "text/html"]


def _image_parts(msg):
    return [p for p in msg.walk() if p.get_content_maintype() == "image"]


def _with_logo(cfg, tmp_path):
    logo = tmp_path / "logo-digger-radio.png"
    logo.write_bytes(TINY_PNG)
    cfg.templates.logo = str(logo)
    return cfg


# ------------------------------------------------------------- sans logo

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


def test_build_message_without_logo_falls_back_to_alternative(cfg, tmp_path):
    cfg.templates.logo = str(tmp_path / "absent.png")
    msg, _ = sender.build_message(
        cfg, "alice@example.org", "Sujet", "Corps", "<p>Corps</p>", {}
    )
    assert msg.get_content_type() == "multipart/alternative"
    assert sender.has_inline_logo(msg) is False


def test_build_message_text_only_without_logo(cfg, tmp_path):
    cfg.templates.logo = str(tmp_path / "absent.png")
    msg, _ = sender.build_message(cfg, "alice@example.org", "Sujet", "Corps", None, {})
    assert msg.get_content_type() == "multipart/alternative"
    assert _html_parts(msg) == []


# ------------------------------------------------------------- avec logo

def test_build_message_with_logo_structure(cfg, tmp_path):
    cfg = _with_logo(cfg, tmp_path)
    msg, _ = sender.build_message(
        cfg, "alice@example.org", "Re: Sujet", "Corps", "<p>Corps</p>", {}
    )

    # multipart/mixed -> related -> (alternative, image)
    assert msg.get_content_type() == "multipart/mixed"
    assert sender.has_inline_logo(msg) is True

    images = _image_parts(msg)
    assert len(images) == 1
    image = images[0]
    assert image.get_content_type() == "image/png"
    assert image.get("Content-ID") == "<logo-digger-radio>"
    disposition = image.get("Content-Disposition")
    assert disposition.startswith("inline")
    assert "logo-digger-radio.png" in disposition

    html = _html_parts(msg)
    assert len(html) == 1
    assert html[0].get_content_type() == "text/html"
    assert b"<p>Corps</p>" in html[0].get_payload(decode=True)


def test_build_message_related_contains_alternative(cfg, tmp_path):
    cfg = _with_logo(cfg, tmp_path)
    msg, _ = sender.build_message(
        cfg, "alice@example.org", "Sujet", "Corps", "<p>Corps</p>", {}
    )
    related = [p for p in msg.walk() if p.get_content_type() == "multipart/related"]
    assert len(related) == 1
    children = related[0].get_payload()
    assert children[0].get_content_type() == "multipart/alternative"
    assert children[1].get_content_type() == "image/png"


def test_build_message_logo_cid_matches_template_reference(cfg, tmp_path):
    """Le Content-ID doit correspondre au src="cid:..." des templates."""
    cfg = _with_logo(cfg, tmp_path)
    msg, _ = sender.build_message(
        cfg, "alice@example.org", "Sujet", "Corps",
        '<img src="cid:logo-digger-radio" alt="Digger-Radio">', {}
    )
    image = _image_parts(msg)[0]
    cid = image.get("Content-ID").strip("<>")
    assert f"cid:{cid}" in '<img src="cid:logo-digger-radio"'


# ------------------------------------------------------------- envoi

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
        captured["message"] = kwargs.get("message")
        return "ok"

    monkeypatch.setattr(sender.send_mail, "envoyer", fake_envoyer)
    sender.send(cfg, "alice@example.org", "Sujet", "Corps", html_body="<p>Corps</p>",
                parsed={"in_reply_to": "<o@x>"}, dry_run=False)
    assert captured["to"] == "alice@example.org"
    assert captured["subject"] == "Sujet"
    # Le message pré-construit doit être transmis tel quel.
    assert captured["message"] is not None


def test_send_passes_logo_through_to_smtp(cfg, tmp_path, monkeypatch):
    cfg = _with_logo(cfg, tmp_path)
    captured = {}

    def fake_envoyer(to, subject, body, **kwargs):
        captured["message"] = kwargs.get("message")
        return "ok"

    monkeypatch.setattr(sender.send_mail, "envoyer", fake_envoyer)
    sender.send(cfg, "alice@example.org", "Sujet", "Corps",
                html_body="<p>Corps</p>", dry_run=False)
    assert sender.has_inline_logo(captured["message"]) is True