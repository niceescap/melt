"""Construction des corps de réponse (texte + HTML)."""

from __future__ import annotations

from string import Template
from typing import Any, Dict, Tuple

import mistune

REFUSE_TEXT = (
    "Bonjour,\n\n"
    "Votre message n'a pas pu être traité par cette adresse. Elle est dédiée à "
    "un service sur inscription et ne répond pas automatiquement aux demandes "
    "générales.\n\n"
    "Merci de votre compréhension."
)


def _load_template(path: str) -> Template:
    with open(path, "r", encoding="utf-8") as fh:
        return Template(fh.read())


def _subject_with_re(subject: str) -> str:
    subject = (subject or "").strip()
    if not subject:
        return "Re: (sans objet)"
    lowered = subject.lower()
    for prefix in ("re:", "réf:", "rep:", "aw:", "sv:"):
        if lowered.startswith(prefix):
            return subject
    return f"Re: {subject}"


def build_reply(
    parsed: Dict[str, Any],
    llm_text: str,
    cfg: Any,
) -> Tuple[str, str, str]:
    """Retourne (subject, text_body, html_body)."""
    subject = _subject_with_re(parsed.get("subject", ""))
    text_body = (llm_text or "").strip()

    template = _load_template(cfg.templates.reply)
    html_body = template.safe_substitute(
        body_html=mistune.html(text_body) if text_body else ""
    )
    return subject, text_body, html_body


def build_refuse(parsed: Dict[str, Any], cfg: Any) -> Tuple[str, str, str]:
    """Retourne (subject, text_body, html_body) pour la branche hors whitelist."""
    subject = _subject_with_re(parsed.get("subject", ""))
    template = _load_template(cfg.templates.refuse)
    html_body = template.safe_substitute()
    return subject, REFUSE_TEXT, html_body