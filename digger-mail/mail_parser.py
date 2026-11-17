"""Parsing des emails entrants et extraction du corps."""

from __future__ import annotations

import re
from email.header import decode_header, make_header
from email.message import Message
from typing import Dict, Optional

from bs4 import BeautifulSoup

REPLY_PREFIXES = ("re", "réf", "rep", "aw", "sv")
_EMPTY_BODY = "(corps vide)"


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # pragma: no cover - en-têtes exotiques
        return value


def _header(message: Message, name: str) -> str:
    return _decode(message.get(name)).strip()


def _extract_body(message: Message) -> str:
    """text/plain prioritaire, repli text/html via BeautifulSoup."""
    if message.is_multipart():
        plain_part = None
        html_part = None
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and plain_part is None:
                plain_part = part
            elif ctype == "text/html" and html_part is None:
                html_part = part
        if plain_part is not None:
            body = _decode_payload(plain_part)
            if body.strip():
                return body
        if html_part is not None:
            return _html_to_text(_decode_payload(html_part))
        return _EMPTY_BODY

    if message.get_content_type() == "text/html":
        return _html_to_text(_decode_payload(message))

    body = _decode_payload(message)
    return body if body.strip() else _EMPTY_BODY


def _decode_payload(part: Message) -> str:
    raw = part.get_payload(decode=True)
    if raw is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:  # pragma: no cover
        return raw.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n")


def normalize_body(body: str) -> str:
    """Normalise les espaces et sauts de ligne excédentaires, garde les >."""
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in body.split("\n"):
        stripped = line.rstrip()
        if stripped.startswith(">"):
            # On préserve la citation, on compacte juste les espaces internes.
            stripped = re.sub(r"[ \t]{2,}", " ", stripped)
            lines.append(stripped)
            continue
        collapsed = re.sub(r"[ \t]{2,}", " ", stripped)
        lines.append(collapsed)

    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def parse(raw_bytes: bytes) -> Dict[str, object]:
    """Retourne le dict normalisé d'un email brut."""
    message = _parse_message(raw_bytes)
    body = normalize_body(_extract_body(message))

    return {
        "message_id": _header(message, "Message-ID"),
        "from_addr": _extract_address(_header(message, "From")),
        "from_name": _extract_name(_header(message, "From")),
        "subject": _header(message, "Subject"),
        "in_reply_to": _header(message, "In-Reply-To"),
        "references": _header(message, "References"),
        "auto_submitted": _header(message, "Auto-Submitted"),
        "precedence": _header(message, "Precedence"),
        "list_unsubscribe": _header(message, "List-Unsubscribe"),
        "return_path": _header(message, "Return-Path"),
        "date": _header(message, "Date"),
        "body_text": body,
    }


def _parse_message(raw_bytes: bytes) -> Message:
    from email import message_from_bytes

    if isinstance(raw_bytes, str):  # pragma: no cover - tolérance tests
        raw_bytes = raw_bytes.encode("utf-8")
    return message_from_bytes(raw_bytes)


def _extract_address(from_value: str) -> str:
    from email.utils import parseaddr

    return parseaddr(from_value)[1].strip().lower()


def _extract_name(from_value: str) -> str:
    from email.utils import parseaddr

    return parseaddr(from_value)[0].strip()


def is_reply(parsed: Dict[str, object]) -> bool:
    """Vrai si le message répond à un autre (en-têtes ou préfixe de sujet)."""
    if (parsed.get("in_reply_to") or "").strip():
        return True
    if (parsed.get("references") or "").strip():
        return True
    subject = (parsed.get("subject") or "").lstrip()
    for line in subject.split("\n"):
        line = line.strip().lower()
        if not line:
            continue
        for prefix in REPLY_PREFIXES:
            if line.startswith(prefix):
                return True
    return False