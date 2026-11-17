"""Tests reply_builder."""

from __future__ import annotations

import os

import reply_builder


class _Templates:
    def __init__(self, base):
        self.reply = os.path.join(base, "templates", "reply.html")
        self.refuse = os.path.join(base, "templates", "refuse.html")


def _cfg():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    class Cfg:
        pass

    c = Cfg()
    c.templates = _Templates(base)
    return c


def test_build_reply_subject_no_double_re():
    parsed = {"subject": "Question"}
    subject, text, html = reply_builder.build_reply(parsed, "Bonjour", _cfg())
    assert subject == "Re: Question"
    assert text == "Bonjour"
    assert "<" in html


def test_build_reply_subject_already_re():
    parsed = {"subject": "Re: Question"}
    subject, _, _ = reply_builder.build_reply(parsed, "Bonjour", _cfg())
    assert subject == "Re: Question"


def test_build_reply_subject_empty():
    parsed = {"subject": ""}
    subject, _, _ = reply_builder.build_reply(parsed, "Bonjour", _cfg())
    assert subject.startswith("Re:")


def test_build_reply_html_contains_converted_text():
    parsed = {"subject": "Sujet"}
    _, _, html = reply_builder.build_reply(parsed, "**gras**", _cfg())
    assert "<strong>gras</strong>" in html


def test_build_refuse():
    parsed = {"subject": "Message"}
    subject, text, html = reply_builder.build_refuse(parsed, _cfg())
    assert subject == "Re: Message"
    assert "service sur inscription" in text
    assert "<html" in html.lower()