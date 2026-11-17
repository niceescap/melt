"""Chargement de la configuration (.env + config.yaml)."""

from __future__ import annotations

import os
from typing import Any, Dict, List

import yaml
from dotenv import load_dotenv


class _Node:
    """Objet à accès par attributs, avec repli sur les clés brutes."""

    def __init__(self, data: Dict[str, Any]) -> None:
        for key, value in data.items():
            if isinstance(value, dict):
                setattr(self, key, _Node(value))
            elif isinstance(value, list):
                setattr(self, key, value)
            else:
                setattr(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __repr__(self) -> str:  # pragma: no cover - debug confort
        return f"Node({self.__dict__})"


class Config(_Node):
    """Configuration applicative.

    Attributs dérivés pratiques :
      - whitelist_lower: adresses en minuscules
      - system_prompt / llm_system_prompt
    """

    @property
    def whitelist_lower(self) -> List[str]:
        return [str(w).strip().lower() for w in (self.whitelist or [])]

    @property
    def llm_system_prompt(self) -> str:
        return str(self.llm.system_prompt)

    @property
    def kb_path(self) -> str:
        return str(self.llm.tools.get("kb_path", "knowledge_base.md"))

    @property
    def kb_format(self) -> str:
        return str(self.llm.tools.get("kb_format", "md")).lower()

    @property
    def logo_path(self) -> Optional[str]:
        """Chemin du logo inline, ou None pour le défaut (à côté du template)."""
        configured = self.templates.get("logo")
        return str(configured) if configured else None

    @property
    def throttle_seconds(self) -> int:
        return int(self.limits.throttle_hours) * 3600


def load_config(
    config_path: str = "config.yaml",
    env_path: str | None = ".env",
) -> Config:
    """Charge le .env puis le config.yaml et retourne un Config."""
    if env_path:
        load_dotenv(env_path, override=True)

    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    node = Config(raw)

    # Les secrets restent dans l'environnement, exposés en attributs.
    node.env = {
        "OVH_MAILTEST_USER": os.getenv("OVH_MAILTEST_USER", ""),
        "OVH_MAILTEST_PASSWORD": os.getenv("OVH_MAILTEST_PASSWORD", ""),
        "LLM_API_KEY": os.getenv("LLM_API_KEY", ""),
    }
    return node