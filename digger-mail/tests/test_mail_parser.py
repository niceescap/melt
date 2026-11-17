"""Tests mail_parser."""

from __future__ import annotations

import mail_parser


def test_parse_simple(raw_simple):
    parsed = mail_parser.parse(raw_simple)
    assert parsed["from_addr"] == "alice@example.org"
    assert parsed["subject"] == "Question tarif"
    assert "bonjour" in parsed["body_text"].lower()
    assert parsed["auto_submitted"] == ""
    assert parsed["precedence"] == ""


def test_parse_reply_headers(raw_reply):
    parsed = mail_parser.parse(raw_reply)
    assert parsed["in_reply_to"] == "<abc123@example.org>"
    assert "abc123@example.org" in parsed["references"]


def test_normalize_keeps_quotes():
    raw = (
        "From: bob@example.org\r\n"
        "Subject: Re: Coucou\r\n"
        "\r\n"
        "Nouvelle partie.\r\n"
        "\r\n"
        "> Ancien message\r\n"
        "> toujours cite\r\n"
    )
    parsed = mail_parser.parse(raw.encode("utf-8"))
    assert "> Ancien message" in parsed["body_text"]
    assert "> toujours cite" in parsed["body_text"]


def test_normalize_collapses_whitespace():
    raw = (
        "From: bob@example.org\r\n"
        "Subject: Hi\r\n"
        "\r\n"
        "ligne   avec     espaces\r\n"
        "\r\n"
        "\r\n"
        "\r\n"
        "fin\r\n"
    )
    parsed = mail_parser.parse(raw.encode("utf-8"))
    assert "ligne avec espaces" in parsed["body_text"]
    assert "\n\n\n" not in parsed["body_text"]


def test_html_fallback():
    raw = (
        "From: carol@example.org\r\n"
        "Subject: HTML\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        "<html><body><p>Bonjour <b>carol</b></p></body></html>\r\n"
    )
    parsed = mail_parser.parse(raw.encode("utf-8"))
    assert "Bonjour" in parsed["body_text"]
    assert "carol" in parsed["body_text"]


def test_empty_body():
    raw = "From: dave@example.org\r\nSubject: Vide\r\n\r\n"
    parsed = mail_parser.parse(raw.encode("utf-8"))
    assert parsed["body_text"] == "(corps vide)"


def test_is_reply_true_on_in_reply_to(raw_reply):
    parsed = mail_parser.parse(raw_reply)
    assert mail_parser.is_reply(parsed) is True


def test_is_reply_false_on_new_subject(raw_simple):
    parsed = mail_parser.parse(raw_simple)
    assert mail_parser.is_reply(parsed) is False


def test_is_reply_prefix_variants():
    for prefix in ("Re:", "Réf:", "Rep:", "Aw:", "Sv:"):
        assert mail_parser.is_reply({"subject": f"{prefix} sujet", "in_reply_to": "", "references": ""}) is True
    assert mail_parser.is_reply({"subject": "Nouveau sujet", "in_reply_to": "", "references": ""}) is False