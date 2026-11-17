"""Appel LLM (format OpenAI-compatible) et outil read_kb."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

import requests

log = logging.getLogger(__name__)

MAX_BODY_PROMPT = 10000
TOOL_NAME = "read_kb"

# Nombre de relances quand le modèle répond un contenu vide sans tool_call.
# Distinct du budget d'itérations d'outils : une réponse vide n'est pas un tour
# d'outil et ne doit pas consommer le budget de max_iterations.
EMPTY_CONTENT_RETRIES = 1

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
    """Erreur LLM : timeout, HTTP, réponse vide ou aucune réponse exploitable."""


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

def _extract_text(message: Dict[str, Any]) -> str:
    """Contenu texte du message (certains modèles renvoient une liste)."""
    content = message.get("content")
    if isinstance(content, list):
        # Format OpenAI multi-parties : on concatène les morceaux texte.
        chunks = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        ]
        content = "".join(chunks)
    return (content or "").strip()


def call_llm(cfg: Any, prompt: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Boucle d'appel LLM avec tools.

    Retourne (texte_final, trace) où trace est une liste de dicts
    {name, result_chars}.

    Deux budgets séparés :
      - ``max_iterations`` : tours d'outils (lecture KB).
      - ``EMPTY_CONTENT_RETRIES`` : relances sur réponse vide.
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

    tool_iterations = 0
    empty_retries = 0

    while True:
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

        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"Réponse LLM sans choix : {data}")

        choice = choices[0]
        message = choice.get("message") or {}
        finish_reason = choice.get("finish_reason")
        tool_calls = message.get("tool_calls") or []
        text = _extract_text(message)
        reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
        usage = data.get("usage") or {}

        if not tool_calls:
            if text:
                log.debug(
                    "Réponse LLM (%d chars, finish_reason=%s, tokens=%s/%s)",
                    len(text), finish_reason,
                    usage.get("completion_tokens"), usage.get("total_tokens"),
                )
                return text, trace

            # Réponse vide : on distingue les cas pour un diagnostic utile.
            if finish_reason == "length":
                raise LLMError(
                    "Réponse LLM tronquée (finish_reason=length) : le budget "
                    f"max_tokens={llm.max_tokens} a été consommé avant toute "
                    "réponse. Augmente max_tokens dans config.yaml."
                )
            if finish_reason == "content_filter":
                raise LLMError("Réponse LLM bloquée par le filtre de contenu (content_filter).")
            if message.get("refusal"):
                raise LLMError(f"Le LLM a refusé de répondre : {message['refusal']}")

            log.warning(
                "Réponse LLM vide (finish_reason=%s, reasoning=%d chars, "
                "completion_tokens=%s, retry %d/%d)",
                finish_reason, len(reasoning), usage.get("completion_tokens"),
                empty_retries, EMPTY_CONTENT_RETRIES,
            )
            if reasoning:
                log.debug("Extrait raisonnement: %s", reasoning[:300])

            if empty_retries < EMPTY_CONTENT_RETRIES:
                empty_retries += 1
                continue
            raise LLMError("Réponse LLM vide après relance (aucun texte exploitable).")

        # --- présence de tool_calls ---
        if tool_iterations >= max_iterations:
            log.warning("max_tool_iterations atteint (%d)", max_iterations)
            break

        tool_iterations += 1
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