"""Appel LLM (format OpenAI-compatible) et outil read_kb."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

import requests

log = logging.getLogger(__name__)

MAX_BODY_PROMPT = 10000
TOOL_NAME = "read_kb"

READ_KB_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Consulte la base de connaissances DiggerRadio. À utiliser pour toute "
            "question portant sur les services, la tarification, les conditions "
            "ou les fonctionnalités de DiggerRadio."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


class LLMError(RuntimeError):
    """Erreur LLM : timeout, HTTP, ou aucune réponse texte exploitable."""


def build_prompt(body_text: str, is_reply: bool, max_body_chars: int = MAX_BODY_PROMPT) -> str:
    """Encadre le corps du message et ajoute l'instruction finale."""
    body = body_text or ""
    truncated = False
    if len(body) > max_body_chars:
        body = body[:max_body_chars] + " […tronqué]"
        truncated = True

    intro = "Reprise de discussion." if is_reply else "Nouvelle conversation."
    parts = [intro, "", "<email>", body, "</email>", "", "Réponds à cet email."]
    if truncated:
        log.info("Corps tronqué à %d caractères", max_body_chars)
    return "\n".join(parts)


# ----------------------------------------------------------------- read_kb

def execute_tool(cfg: Any, name: str, arguments: Dict[str, Any] | None = None) -> str:
    """Exécute un outil. Retourne le résultat texte ou une erreur textuelle."""
    arguments = arguments or {}
    if name != TOOL_NAME:
        return f"Erreur: outil inconnu '{name}'."

    kb_path = cfg.kb_path
    try:
        with open(kb_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        log.error("KB inaccessible (%s): %s", kb_path, exc)
        return f"Erreur: base de connaissances inaccessible ({kb_path})."

    if cfg.kb_format == "json":
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            log.error("KB JSON invalide (%s): %s", kb_path, exc)
            return f"Erreur: base de connaissances JSON invalide ({kb_path})."
        content = json.dumps(parsed, indent=2, ensure_ascii=False)

    max_chars = int(cfg.llm.tools.get("max_result_chars", 20000))
    if len(content) > max_chars:
        content = content[:max_chars] + " […tronqué]"
    return content


# ------------------------------------------------------------------ call_llm

def call_llm(cfg: Any, prompt: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Boucle d'appel LLM avec tools.

    Retourne (texte_final, trace) où trace est une liste de dicts
    {name, result_chars}.
    """
    llm = cfg.llm
    headers = {
        "Authorization": f"Bearer {cfg.env['LLM_API_KEY']}",
        "Content-Type": "application/json",
    }
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": cfg.llm_system_prompt},
        {"role": "user", "content": prompt},
    ]

    tools_enabled = bool(llm.tools.get("enabled", True))
    tools = [READ_KB_TOOL_SCHEMA] if tools_enabled else None
    max_iterations = int(llm.tools.get("max_iterations", 3))
    trace: List[Dict[str, Any]] = []

    for iteration in range(max_iterations + 1):
        payload = {
            "model": llm.model,
            "messages": messages,
            "temperature": float(llm.temperature),
            "max_tokens": int(llm.max_tokens),
        }
        if tools:
            payload["tools"] = tools

        try:
            resp = requests.post(
                llm.endpoint,
                headers=headers,
                json=payload,
                timeout=float(llm.timeout_seconds),
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise LLMError(f"Échec appel LLM: {exc}") from exc

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls") or []
        content = message.get("content")

        if not tool_calls:
            if content and content.strip():
                return content.strip(), trace
            if iteration >= max_iterations:
                break
            log.warning("Réponse LLM vide sans tool_calls, itération %d", iteration)
            continue

        if iteration >= max_iterations:
            log.warning("max_tool_iterations atteint (%d)", max_iterations)
            break

        messages.append(message)
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                args = {}
            result = execute_tool(cfg, name, args)
            trace.append({"name": name, "result_chars": len(result)})
            log.info("tool_call %s (%d chars)", name, len(result))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result,
                }
            )

    raise LLMError("Aucune réponse texte obtenue du LLM.")


def build_llm_messages(cfg: Any, prompt: str) -> List[Dict[str, Any]]:
    """Construit les messages initiaux (utile pour les tests)."""
    return [
        {"role": "system", "content": cfg.llm_system_prompt},
        {"role": "user", "content": prompt},
    ]