"""Fixtures .eml et objets partagés pour les tests."""

from __future__ import annotations

import os
from typing import Any, Dict

import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _read_fixture(name: str) -> bytes:
    path = os.path.join(FIXTURES_DIR, name)
    with open(path, "rb") as fh:
        return fh.read()


class FakeCfg:
    """Config minimale pour les tests, accès par attributs."""

    def __init__(self, **overrides: Any) -> None:
        self.mail_domain = "diggerradio.com"
        self.whitelist = ["partenaire@example.com"]
        self.templates = _Templates()
        self.limits = _Limits()
        self.llm = _LLM()

        env = _Env()
        self.env = {
            "OVH_MAILTEST_USER": env.user,
            "OVH_MAILTEST_PASSWORD": env.password,
            "LLM_API_KEY": "test-key",
        }
        for key, value in overrides.items():
            setattr(self, key, value)

    @property
    def whitelist_lower(self):
        return [w.lower() for w in self.whitelist]

    @property
    def llm_system_prompt(self) -> str:
        return "Tu es l'assistant de DiggerRadio."

    @property
    def kb_path(self) -> str:
        return self.llm.tools.get("kb_path", "knowledge_base.md")

    @property
    def kb_format(self) -> str:
        return "md"

    @property
    def throttle_seconds(self) -> int:
        return self.limits.throttle_hours * 3600


class _Templates:
    def __init__(self) -> None:
        base = os.path.dirname(__file__)
        self.reply = os.path.join(base, "templates", "reply.html")
        self.refuse = os.path.join(base, "templates", "refuse.html")


class _Limits:
    max_body_chars = 10000
    fresh_message_seconds = 90
    throttle_hours = 24


class _Env:
    user = "mailtest@diggerradio.com"
    password = "secret"


class _Tools(dict):
    pass


class _LLM:
    def __init__(self) -> None:
        self.endpoint = "https://example.invalid/v1/chat/completions"
        self.model = "gpt-4o-mini"
        self.temperature = 0.3
        self.max_tokens = 800
        self.timeout_seconds = 60
        self.tools = _Tools(
            enabled=True,
            kb_path="knowledge_base.md",
            kb_format="md",
            max_iterations=3,
            max_result_chars=20000,
        )


@pytest.fixture
def cfg() -> FakeCfg:
    return FakeCfg()


@pytest.fixture
def raw_simple() -> bytes:
    return _read_fixture("simple.eml")


@pytest.fixture
def raw_reply() -> bytes:
    return _read_fixture("reply.eml")


@pytest.fixture
def raw_bounce() -> bytes:
    return _read_fixture("bounce.eml")


@pytest.fixture
def raw_bulk() -> bytes:
    return _read_fixture("bulk.eml")