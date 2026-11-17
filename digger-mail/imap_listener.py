"""Connexion IMAP et polling (synchrone, mono-thread)."""

from __future__ import annotations

import logging
import time
from typing import List

from imapclient import IMAPClient

log = logging.getLogger(__name__)

POLL_INTERVAL = 5  # secondes entre deux vérifications


def connect(host: str, port: int, user: str, password: str,
            mailbox: str = "INBOX") -> IMAPClient:
    """Connexion SSL, login, sélection de la mailbox."""
    client = IMAPClient(host, port=port, use_uid=True, ssl=True)
    client.login(user, password)
    client.select_folder(mailbox)
    log.info("Connecté à %s:%s (mailbox %s)", host, port, mailbox)
    return client


def wait_for_messages(client: IMAPClient, last_uid_seen: int,
                      shutdown_flag) -> List[int]:
    """Poll INBOX toutes les POLL_INTERVAL secondes. Retourne les UID > last."""
    while not _is_shutdown(shutdown_flag):
        uids = search_uids_after(client, last_uid_seen)
        if uids:
            log.info("%d nouveau(x) message(s) détecté(s)", len(uids))
            return uids
        time.sleep(POLL_INTERVAL)
    return []


def _is_shutdown(shutdown_flag) -> bool:
    if shutdown_flag is None:
        return False
    if callable(shutdown_flag):
        return bool(shutdown_flag())
    if hasattr(shutdown_flag, "is_set"):
        return bool(shutdown_flag.is_set())
    return bool(getattr(shutdown_flag, "set", False))


def search_uids_after(client: IMAPClient, last_uid_seen: int) -> List[int]:
    """Retourne les UID strictement supérieurs à last_uid_seen, triés."""
    criteria = [f"{last_uid_seen + 1}:*"] if last_uid_seen else ["1:*"]
    try:
        uids = client.search(criteria)
    except Exception as exc:
        log.error("Recherche UID échouée: %s", exc)
        return []
    return sorted(int(u) for u in uids if int(u) > last_uid_seen)


def fetch_message(client: IMAPClient, uid: int) -> bytes | None:
    """Retourne le RFC822 brut du message."""
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