"""Filtres anti-boucle, whitelist et throttle."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict

import state as state_mod

BOUNCE_MAILERS = ("mailer-daemon", "postmaster")


def is_bounce(parsed: Dict[str, Any]) -> bool:
    return_path = (parsed.get("return_path") or "").strip()
    if return_path in ("", "<>"):
        return True
    from_addr = (parsed.get("from_addr") or "").strip().lower()
    return any(from_addr.startswith(m) for m in BOUNCE_MAILERS)


def is_bulk(parsed: Dict[str, Any]) -> bool:
    precedence = (parsed.get("precedence") or "").strip().lower()
    if precedence in ("bulk", "list", "junk"):
        return True
    if (parsed.get("list_unsubscribe") or "").strip():
        return True
    return False


def is_auto_submitted(parsed: Dict[str, Any]) -> bool:
    value = (parsed.get("auto_submitted") or "").strip().lower()
    return bool(value) and value != "no"


def is_whitelisted(parsed: Dict[str, Any], whitelist_lower: list[str]) -> bool:
    from_addr = (parsed.get("from_addr") or "").strip().lower()
    return bool(from_addr) and from_addr in whitelist_lower


def is_fresh(parsed: Dict[str, Any], fresh_seconds: int = 90,
             now: datetime | None = None) -> bool:
    """Fraîcheur d'un message, utilisée uniquement au seed initial."""
    date_header = (parsed.get("date") or "").strip()
    if not date_header:
        return False
    from email.utils import parsedate_to_datetime

    try:
        when = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:  # pragma: no cover
        when = when.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return abs((reference - when).total_seconds()) <= fresh_seconds


def is_throttled(conn: sqlite3.Connection, from_addr: str,
                 throttle_seconds: int, now: datetime | None = None) -> bool:
    """Vrai si une réponse a déjà été envoyée à cette adresse dans la fenêtre."""
    last = state_mod.last_replied_at(conn, (from_addr or "").strip().lower())
    if last is None:
        return False
    reference = now or datetime.now(timezone.utc)
    if last.tzinfo is None:  # pragma: no cover - ISO sans tz
        last = last.replace(tzinfo=timezone.utc)
    return (reference - last).total_seconds() < throttle_seconds