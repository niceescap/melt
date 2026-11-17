"""Connexion IMAP et attente IDLE (synchrone, mono-thread)."""

from __future__ import annotations

import logging
import time
from typing import List

from imapclient import IMAPClient

log = logging.getLogger(__name__)

# Durée max d'un tour d'IDLE avant de revérifier le flag shutdown.
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
    """Entre en IDLE, attend un EXISTS, sort de l'IDLE, retourne les UID > last.

    ``shutdown_flag`` peut être un objet avec ``is_set()`` ou un simple booléen
    évalué à chaque tour de boucle. En sortie (signalement serveur ou arrêt
    demandé), on quitte systématiquement l'IDLE proprement via ``idle_done``.
    """
    client.idle()
    log.info("IDLE engagé (last_uid_seen=%s)", last_uid_seen)
    try:
        deadline = time.monotonic() + poll_seconds
        while not _is_shutdown(shutdown_flag):
            if time.monotonic() >= deadline:
                # Tour de surveillance : on revérifie le flag sans quitter l'IDLE.
                deadline = time.monotonic() + poll_seconds
            client.idle_check(timeout=5)
            # idle_check lève une exception si le serveur ferme la session ;
            # on laisse remonter pour que main.py gère le backoff.
            if _server_signaled(client):
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


def _server_signaled(client: IMAPClient) -> bool:
    """Vrai si des messages ont été signalés par le serveur."""
    try:
        return client.has_idle_data()
    except AttributeError:  # pragma: no cover - versions anciennes
        return False


def _safe_idle_done(client: IMAPClient) -> None:
    try:
        client.idle_done()
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("idle_done a échoué: %s", exc)


def search_uids_after(client: IMAPClient, last_uid_seen: int) -> List[int]:
    """Retourne les UID strictement supérieurs à last_uid_seen, triés."""
    criteria = [f"UID {last_uid_seen + 1}:*"] if last_uid_seen else ["UID 1:*"]
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
    except Exception as exc:  # pragma: no cover
        log.warning("logout a échoué: %s", exc)