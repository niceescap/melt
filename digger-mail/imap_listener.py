"""Connexion IMAP et attente IDLE (synchrone, mono-thread)."""

from __future__ import annotations

import logging
import time
from typing import List

from imapclient import IMAPClient

log = logging.getLogger(__name__)

IDLE_POLL_SECONDS = 60


def connect(host: str, port: int, user: str, password: str,
            mailbox: str = "INBOX") -> IMAPClient:
    """Connexion SSL, login, sélection de la mailbox."""
    client = IMAPClient(host, port=port, use_uid=True, ssl=True)
    client.login(user, password)
    client.select_folder(mailbox)
    log.info("Connecté à %s:%s (mailbox %s)", host, port, mailbox)
    return client


def idle_wait(client: IMAPClient, last_uid_seen: int,
              shutdown_flag, poll_seconds: int = IDLE_POLL_SECONDS) -> List[int]:
    """Entre en IDLE, poll idle_check(timeout=1), sort si nouveaux UID ou shutdown."""
    client.idle()
    log.info("IDLE engagé (last_uid_seen=%s)", last_uid_seen)
    try:
        deadline = time.monotonic() + poll_seconds
        while not _is_shutdown(shutdown_flag):
            if time.monotonic() >= deadline:
                deadline = time.monotonic() + poll_seconds
            client.idle_check(timeout=1)
            if _has_new_messages(client, last_uid_seen):
                break
    finally:
        _safe_idle_done(client)

    uids = search_uids_after(client, last_uid_seen)
    if uids:
        log.info("IDLE terminé — %d nouveau(x) message(s)", len(uids))
    return uids


def _is_shutdown(shutdown_flag) -> bool:
    if shutdown_flag is None:
        return False
    if callable(shutdown_flag):
        return bool(shutdown_flag())
    if hasattr(shutdown_flag, "is_set"):
        return bool(shutdown_flag.is_set())
    return bool(getattr(shutdown_flag, "set", False))


def _safe_idle_done(client: IMAPClient) -> None:
    try:
        client.idle_done()
    except Exception as exc:
        log.warning("idle_done a échoué: %s", exc)


def _has_new_messages(client: IMAPClient, last_uid_seen: int) -> bool:
    try:
        uids = client.search([f"{last_uid_seen + 1}:*"])
        return len(uids) > 0
    except Exception:
        return False


def search_uids_after(client: IMAPClient, last_uid_seen: int) -> List[int]:
    criteria = [f"{last_uid_seen + 1}:*"] if last_uid_seen else ["1:*"]
    try:
        uids = client.search(criteria)
    except Exception as exc:
        log.error("Recherche UID échouée: %s", exc)
        return []
    return sorted(int(u) for u in uids if int(u) > last_uid_seen)


def fetch_message(client: IMAPClient, uid: int) -> bytes | None:
    try:
        result = client.fetch([uid], ["RFC822"])
    except Exception as exc:
        log.error("Fetch UID %s échoué: %s", uid, exc)
        return None
    data = result.get(uid) or {}
    return data.get(b"RFC822")


def logout(client: IMAPClient) -> None:
    try:
        client.logout()
        log.info("Déconnecté IMAP")
    except Exception as exc:
        log.warning("logout a échoué: %s", exc)