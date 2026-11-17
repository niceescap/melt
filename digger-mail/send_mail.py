#!/usr/bin/env python3
"""Envoi d'email SMTP OVH.

La fonction ``envoyer`` accepte ``message`` : un message déjà construit
(EmailMessage ou MIMEMultipart), envoyé tel quel par sender.py pour préserver
la structure MIME multipart/related et le logo inline.

Les fonctions interactives (choisir_compte, saisir_*, main) sont conservées
pour un usage manuel mais ne sont pas utilisées par le daemon.
"""

from __future__ import annotations

import getpass
import os
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Dict, Optional

DEFAULT_SMTP_HOST = "ssl0.ovh.net"
DEFAULT_SMTP_PORT = 465


@dataclass
class Compte:
    nom: str
    user: str
    password: str = ""

    @property
    def display(self) -> str:
        return self.nom


def envoyer(
    destinataire: str,
    sujet: str,
    corps: str,
    *,
    html_body: Optional[str] = None,
    smtp_host: str = DEFAULT_SMTP_HOST,
    smtp_port: int = DEFAULT_SMTP_PORT,
    user: Optional[str] = None,
    password: Optional[str] = None,
    message_id: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    message: Optional[Any] = None,
) -> str:
    """Envoie un email. Retourne le Message-ID.

    Si ``message`` est fourni, il est envoyé tel quel (structure MIME déjà
    construite, logo inline inclus) et les paramètres de contenu sont ignorés.
    """
    user = user or os.getenv("OVH_MAILTEST_USER", "")
    password = password or os.getenv("OVH_MAILTEST_PASSWORD", "")

    if message is None:
        message = EmailMessage()
        message["From"] = user
        message["To"] = destinataire
        message["Subject"] = sujet
        if message_id:
            message["Message-ID"] = message_id
        for key, value in (extra_headers or {}).items():
            message[key] = value
        message.set_content(corps)
        if html_body:
            message.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=timeout) as smtp:
        smtp.login(user, password)
        smtp.send_message(message)

    if message_id:
        return message_id
    return message.get("Message-ID") or ""


# ------------------------------------------------------- fonctions interactives

def choisir_compte(comptes: list[Compte]) -> Compte:
    print("Comptes disponibles :")
    for i, compte in enumerate(comptes, 1):
        print(f"  {i}. {compte.nom}")
    index = int(input("Votre choix : ")) - 1
    return comptes[index]


def saisir_destinataire() -> str:
    return input("Destinataire : ").strip()


def saisir_sujet() -> str:
    return input("Sujet : ").strip()


def saisir_corps() -> str:
    print("Corps (Ctrl-D pour terminer) :")
    return sys.stdin.read()


def saisir_fichier_html(path: Optional[str] = None) -> Optional[str]:
    path = path or input("Fichier HTML (vide pour aucun) : ").strip()
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def main() -> int:
    user = os.getenv("OVH_MAILTEST_USER", "")
    password = getpass.getpass("Mot de passe SMTP : ")
    destinataire = saisir_destinataire()
    sujet = saisir_sujet()
    corps = saisir_corps()
    html_body = saisir_fichier_html()
    envoyer(destinataire, sujet, corps, html_body=html_body, user=user, password=password)
    print("Envoyé.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())