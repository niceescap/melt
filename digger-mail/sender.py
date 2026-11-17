"""Envoi SMTP : wrapper autour de send_mail.envoyer."""

from __future__ import annotations

import logging
import os
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any, Dict, Optional, Tuple

import send_mail

log = logging.getLogger(__name__)


def build_message(
    cfg: Any,
    to_addr: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
    parsed: Optional[Dict[str, Any]] = None,
) -> Tuple[EmailMessage, str]:
    """Construit le EmailMessage avec les en-têtes d'auto-réponse."""
    domain = cfg.mail_domain
    msgid = make_msgid(domain=domain)
    parsed = parsed or {}

    msg = EmailMessage()
    msg["From"] = cfg.env["OVH_MAILTEST_USER"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = msgid
    msg["Auto-Submitted"] = "auto-replied"
    if parsed.get("in_reply_to"):
        msg["In-Reply-To"] = parsed["in_reply_to"]
    references = _build_references(parsed)
    if references:
        msg["References"] = references

    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg, msgid


def _build_references(parsed: Dict[str, Any]) -> str:
    refs = (parsed.get("references") or "").strip()
    in_reply_to = (parsed.get("in_reply_to") or "").strip()
    parts = [p.strip() for p in refs.split() if p.strip()]
    if in_reply_to and in_reply_to not in parts:
        parts.append(in_reply_to)
    return " ".join(parts)


def send(
    cfg: Any,
    to_addr: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
    parsed: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
) -> str:
    """Envoie la réponse. Retourne le Message-ID généré."""
    msg, msgid = build_message(cfg, to_addr, subject, text_body, html_body, parsed)

    if dry_run:
        log.info(
            "[dry-run] SMTP non appelé — To=%s Subject=%s Message-ID=%s",
            to_addr,
            subject,
            msgid,
        )
        return msgid

    send_mail.envoyer(
        msg["To"],
        msg["Subject"],
        text_body,
        html_body=html_body,
        smtp_host=cfg.smtp.host,
        smtp_port=cfg.smtp.port,
        user=cfg.env["OVH_MAILTEST_USER"],
        password=cfg.env["OVH_MAILTEST_PASSWORD"],
        message_id=msgid,
        extra_headers={
            "Auto-Submitted": "auto-replied",
            **({"In-Reply-To": parsed["in_reply_to"]} if parsed and parsed.get("in_reply_to") else {}),
        },
    )
    return msgid