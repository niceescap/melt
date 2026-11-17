#!/usr/bin/env python3
"""DiggerMail — boucle principale IMAP IDLE + réponse LLM.

Ssynchrone, mono-thread. Un email à la fois, puis retour à l'IDLE.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
import traceback
from typing import Optional

import filters
import imap_listener
import llm_handler
import mail_parser
import reply_builder
import sender
import state
from config import Config, load_config

log = logging.getLogger("digger-mail")

BACKOFF_SEQUENCE = [1, 2, 4, 8, 16, 30]


class Shutdown:
    """Flag partagé posé par les handlers de signaux."""

    def __init__(self) -> None:
        self.shutdown_requested = False

    def is_set(self) -> bool:
        return self.shutdown_requested

    def request(self) -> None:
        self.shutdown_requested = True


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Réponse mail autonome par LLM")
    parser.add_argument("--config", default="config.yaml", help="Chemin config.yaml")
    parser.add_argument("--env", default=".env", help="Chemin .env")
    parser.add_argument("--dry-run", action="store_true",
                        help="N'envoie aucun email, logge seulement")
    return parser.parse_args(argv)


def seed_initial(conn, cfg: Config) -> None:
    """Seed unique de last_uid_seen avec le MAX(UID) de la boîte."""
    client = imap_listener.connect(
        cfg.imap.host, cfg.imap.port,
        cfg.env["OVH_MAILTEST_USER"], cfg.env["OVH_MAILTEST_PASSWORD"],
        cfg.imap.get("mailbox", "INBOX"),
    )
    try:
        uids = client.search(["1:*"])
        max_uid = max(int(u) for u in uids) if uids else 0
        state.set_last_uid(conn, max_uid)
        log.info("Seed initial: last_uid_seen=%s", max_uid)
    finally:
        imap_listener.logout(client)


# ----------------------------------------------------------------- traitement

def handle_message(client, conn, cfg: Config, uid: int, dry_run: bool,
                   at_seed: bool) -> bool:
    """Traite un UID. Retourne True si last_uid_seen doit être mis à jour."""
    raw = imap_listener.fetch_message(client, uid)
    if raw is None:
        log.error("UID %s: fetch a échoué", uid)
        return True

    parsed = mail_parser.parse(raw)
    log.info("Message reçu uid=%s from=%s subject=%r",
             uid, parsed["from_addr"], parsed["subject"])

    # Insertion en base (statut pending) avant traitement.
    state.insert_pending(conn, uid, parsed.get("message_id"),
                         parsed.get("from_addr"), parsed.get("subject"))
    state.mark(conn, uid, "processing")

    # --- filtres anti-boucle ---
    if filters.is_bounce(parsed):
        log.warning("Filtre bounce uid=%s", uid)
        state.mark(conn, uid, "filtered")
        return True

    if filters.is_bulk(parsed):
        log.warning("Filtre bulk uid=%s", uid)
        state.mark(conn, uid, "filtered")
        return True

    if filters.is_auto_submitted(parsed):
        log.warning("Filtre auto_submitted uid=%s", uid)
        state.mark(conn, uid, "filtered")
        return True

    if at_seed and not filters.is_fresh(parsed, cfg.limits.fresh_message_seconds):
        log.warning("Filtre fraîcheur uid=%s", uid)
        state.mark(conn, uid, "filtered")
        return True

    from_addr = parsed.get("from_addr") or ""
    whitelisted = filters.is_whitelisted(parsed, cfg.whitelist_lower)

    # --- branche hors whitelist : refus + throttle ---
    if not whitelisted:
        if filters.is_throttled(conn, from_addr, cfg.throttle_seconds):
            log.warning("Throttle uid=%s from=%s", uid, from_addr)
            state.mark(conn, uid, "throttled")
            return True

        subject, text_body, html_body = reply_builder.build_refuse(parsed, cfg)
        msgid = sender.send(
            cfg, from_addr, subject, text_body, html_body,
            parsed=parsed, dry_run=dry_run,
        )
        log.info("Refus envoyé à %s (%s)", from_addr, msgid)
        state.mark(conn, uid, "replied", reply_body=text_body, replied=True)
        return True

    # --- branche whitelistée : LLM ---
    try:
        prompt = llm_handler.build_prompt(
            parsed["body_text"], mail_parser.is_reply(parsed),
            cfg.limits.max_body_chars,
        )
        llm_text, trace = llm_handler.call_llm(cfg, prompt)
    except llm_handler.LLMError as exc:
        log.error("Échec LLM uid=%s: %s", uid, exc)
        state.mark(conn, uid, "failed", error=str(exc))
        return True

    subject, text_body, html_body = reply_builder.build_reply(parsed, llm_text, cfg)
    msgid = sender.send(
        cfg, from_addr, subject, text_body, html_body,
        parsed=parsed, dry_run=dry_run,
    )
    log.info("Réponse LLM envoyée à %s (%s, tools=%d)",
             from_addr, msgid, len(trace))
    state.mark(conn, uid, "replied", reply_body=text_body, replied=True,
               tool_trace=trace or None)
    return True


def run_idle_cycle(client, conn, cfg: Config, shutdown: Shutdown, dry_run: bool) -> None:
    """Un cycle : attente IDLE, traitement des UID, mise à jour du curseur."""
    last_uid = state.get_last_uid(conn)
    uids = imap_listener.idle_wait(client, last_uid, shutdown)

    at_seed = False
    for uid in sorted(uids):
        if shutdown.is_set():
            log.info("Arrêt demandé — traitement interrompu à l'uid %s", uid)
            break
        if uid <= last_uid:
            continue
        if state.is_seen(conn, uid):
            log.info("UID %s déjà en base — ignoré", uid)
            state.set_last_uid(conn, uid)
            last_uid = uid
            continue

        updated = handle_message(client, conn, cfg, uid, dry_run, at_seed)
        if updated:
            state.set_last_uid(conn, uid)
            last_uid = uid


# ----------------------------------------------------------------------- main

def run(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    cfg = load_config(args.config, args.env)
    setup_logging(cfg.logging.level)

    log.info("Démarrage DiggerMail (dry_run=%s, config=%s)", args.dry_run, args.config)

    conn = state.init_db()
    state.reset_stale_processing(conn)

    shutdown = Shutdown()
    signal.signal(signal.SIGTERM, lambda *_: _on_signal(shutdown, "SIGTERM"))
    signal.signal(signal.SIGINT, lambda *_: _on_signal(shutdown, "SIGINT"))

    client = None
    try:
        if not state.get_meta(conn, "seeded"):
            seed_initial(conn, cfg)
            state.set_meta(conn, "seeded", "1")

        backoff_index = 0
        while not shutdown.is_set():
            try:
                client = imap_listener.connect(
                    cfg.imap.host, cfg.imap.port,
                    cfg.env["OVH_MAILTEST_USER"], cfg.env["OVH_MAILTEST_PASSWORD"],
                    cfg.imap.get("mailbox", "INBOX"),
                )
                run_idle_cycle(client, conn, cfg, shutdown, args.dry_run)
                imap_listener.logout(client)
                client = None
                backoff_index = 0
            except Exception as exc:  # déconnexion IMAP ou erreur de cycle
                log.error("Erreur de cycle IMAP: %s", exc)
                if client is not None:
                    try:
                        imap_listener.logout(client)
                    except Exception:
                        pass
                    client = None
                if shutdown.is_set():
                    break
                delay = BACKOFF_SEQUENCE[min(backoff_index, len(BACKOFF_SEQUENCE) - 1)]
                backoff_index += 1
                log.warning("Reconnexion dans %ss", delay)
                _interruptible_sleep(delay, shutdown)

        # Sortie propre.
        log.info("Arrêt demandé — idle_done, logout, fermeture SQLite, exit 0")
        if client is not None:
            try:
                imap_listener.logout(client)
            except Exception:
                pass
        conn.commit()
        conn.close()
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _on_signal(shutdown: Shutdown, name: str) -> None:
    log.warning("Signal %s reçu — arrêt demandé", name)
    shutdown.request()


def _interruptible_sleep(seconds: int, shutdown: Shutdown) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if shutdown.is_set():
            return
        time.sleep(0.2)


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(run())
    except KeyboardInterrupt:  # pragma: no cover
        log.warning("KeyboardInterrupt — sortie")
        raise SystemExit(0)
    except Exception:  # pragma: no cover
        log.error("Erreur fatale:\n%s", traceback.format_exc())
        raise SystemExit(1)