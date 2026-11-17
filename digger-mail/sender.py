"""Envoi SMTP : wrapper autour de send_mail.envoyer.

Structure MIME produite :
  multipart/mixed
    └ multipart/related
        ├ multipart/alternative
        │   ├ text/plain
        │   └ text/html   (référence le logo via cid:logo-digger-radio)
        └ image/png       (Content-Disposition: inline, Content-ID: <logo-digger-radio>)

Si le logo est absent, on retombe sur un simple multipart/alternative
(message valide, mais l'image cid: ne s'affichera pas).
"""

from __future__ import annotations

import logging
import os
from email.message import EmailMessage
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid
from typing import Any, Dict, Optional, Tuple

import send_mail

log = logging.getLogger(__name__)

LOGO_CID = "logo-digger-radio"
LOGO_FILENAME = "logo-digger-radio.png"


def _cfg_get(obj: Any, key: str, default: Any = None) -> Any:
    getter = getattr(obj, "get", None)
    if callable(getter):
        return getter(key, default)
    return getattr(obj, key, default)


def _logo_path(cfg: Any) -> str:
    """Chemin du logo : config d'abord, sinon à côté du template reply."""
    configured = _cfg_get(cfg.templates, "logo")
    if configured:
        return str(configured)
    base = os.path.dirname(os.path.abspath(str(cfg.templates.reply)))
    return os.path.join(base, LOGO_FILENAME)


def load_logo(cfg: Any) -> Optional[bytes]:
    """Charge le logo PNG. Retourne None s'il est absent ou illisible."""
    path = _logo_path(cfg)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        log.warning(
            "Logo inline absent (%s): %s — l'image cid:%s ne s'affichera pas",
            path, exc, LOGO_CID,
        )
        return None
    if not data:
        log.warning("Logo inline vide (%s) — ignoré", path)
        return None
    return data


def build_message(
    cfg: Any,
    to_addr: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
    parsed: Optional[Dict[str, Any]] = None,
) -> Tuple[EmailMessage, str]:
    """Construit le message complet, avec logo inline si disponible."""
    msgid = make_msgid(domain=cfg.mail_domain)
    parsed = parsed or {}

    headers: Dict[str, str] = {
        "From": cfg.env["OVH_MAILTEST_USER"],
        "To": to_addr,
        "Subject": subject,
        "Message-ID": msgid,
        "Auto-Submitted": "auto-replied",
    }
    if parsed.get("in_reply_to"):
        headers["In-Reply-To"] = parsed["in_reply_to"]
    references = _build_references(parsed)
    if references:
        headers["References"] = references

    logo = load_logo(cfg)
    if html_body and logo:
        msg = _build_related(headers, text_body, html_body, logo)
    else:
        if html_body:
            log.warning("Envoi sans logo inline — HTML toujours inclus")
        msg = _build_alternative(headers, text_body, html_body)

    return msg, msgid


def _build_alternative(
    headers: Dict[str, str],
    text_body: str,
    html_body: Optional[str],
) -> EmailMessage:
    """Repli : multipart/alternative sans image inline."""
    msg = EmailMessage()
    for key, value in headers.items():
        msg[key] = value
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


def _build_related(
    headers: Dict[str, str],
    text_body: str,
    html_body: str,
    logo_bytes: bytes,
) -> MIMEMultipart:
    """multipart/mixed -> related(alternative + image inline)."""
    root = MIMEMultipart("mixed")
    for key, value in headers.items():
        root[key] = value

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(text_body, "plain", "utf-8"))
    alternative.attach(MIMEText(html_body, "html", "utf-8"))

    related = MIMEMultipart("related")
    related.attach(alternative)

    image = MIMEImage(logo_bytes, "png")
    image.add_header("Content-Disposition", "inline", filename=LOGO_FILENAME)
    image.add_header("Content-ID", f"<{LOGO_CID}>")
    related.attach(image)

    root.attach(related)
    return root


def _build_references(parsed: Dict[str, Any]) -> str:
    refs = (parsed.get("references") or "").strip()
    in_reply_to = (parsed.get("in_reply_to") or "").strip()
    parts = [p.strip() for p in refs.split() if p.strip()]
    if in_reply_to and in_reply_to not in parts:
        parts.append(in_reply_to)
    return " ".join(parts)


def has_inline_logo(msg: Any) -> bool:
    """Vrai si le message contient la partie image avec le bon Content-ID."""
    return any(
        part.get("Content-ID") == f"<{LOGO_CID}>"
        for part in msg.walk()
    )


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
            "[dry-run] SMTP non appelé — To=%s Subject=%s Message-ID=%s "
            "inline_logo=%s html=%s",
            to_addr,
            subject,
            msgid,
            has_inline_logo(msg),
            bool(html_body),
        )
        return msgid

    send_mail.envoyer(
        msg["To"],
        msg["Subject"],
        text_body,
        smtp_host=cfg.smtp.host,
        smtp_port=cfg.smtp.port,
        user=cfg.env["OVH_MAILTEST_USER"],
        password=cfg.env["OVH_MAILTEST_PASSWORD"],
        message_id=msgid,
        message=msg,
    )
    return msgid