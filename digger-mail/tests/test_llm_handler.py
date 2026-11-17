"""Tests llm_handler : build_prompt, execute_tool, call_llm (mock)."""

from __future__ import annotations

import json

import pytest

import llm_handler


def test_build_prompt_new_conversation():
    prompt = llm_handler.build_prompt("Bonjour", is_reply=False, max_body_chars=10000)
    assert prompt.startswith("Nouvelle conversation.")
    assert "<email>" in prompt
    assert "</email>" in prompt
    assert prompt.rstrip().endswith("Réponds à cet email.")


def test_build_prompt_reply():
    prompt = llm_handler.build_prompt("Re: sujet", is_reply=True, max_body_chars=10000)
    assert prompt.startswith("Reprise de discussion.")


def test_build_prompt_truncation():
    body = "a" * 500
    prompt = llm_handler.build_prompt(body, is_reply=False, max_body_chars=100)
    assert "[…tronqué]" in prompt
    assert prompt.count("a") == 100


def test_build_prompt_keeps_quotes():
    body = "> ancien\n> message"
    prompt = llm_handler.build_prompt(body, is_reply=True, max_body_chars=10000)
    assert "> ancien" in prompt
    assert "> message" in prompt


def test_execute_tool_unknown():
    out = llm_handler.execute_tool(cfg=_FakeCfg(), name="unknown_tool")
    assert "inconnu" in out.lower() or "erreur" in out.lower()


def test_execute_tool_reads_kb(tmp_path, cfg):
    kb = tmp_path / "kb.md"
    kb.write_text("# Services\nDiggerRadio diffuse 24h/24.\n", encoding="utf-8")
    cfg.llm.tools["kb_path"] = str(kb)
    out = llm_handler.execute_tool(cfg, "read_kb")
    assert "DiggerRadio" in out


def test_execute_tool_missing_kb(tmp_path, cfg):
    cfg.llm.tools["kb_path"] = str(tmp_path / "absent.md")
    out = llm_handler.execute_tool(cfg, "read_kb")
    assert "erreur" in out.lower()


def test_execute_tool_invalid_json(tmp_path, cfg):
    kb = tmp_path / "kb.json"
    kb.write_text("{not json", encoding="utf-8")
    cfg.llm.tools["kb_path"] = str(kb)
    cfg.llm.tools["kb_format"] = "json"
    out = llm_handler.execute_tool(cfg, "read_kb")
    assert "erreur" in out.lower()


def test_execute_tool_json_valid(tmp_path, cfg):
    kb = tmp_path / "kb.json"
    kb.write_text(json.dumps({"services": ["a", "b"]}), encoding="utf-8")
    cfg.llm.tools["kb_path"] = str(kb)
    cfg.llm.tools["kb_format"] = "json"
    out = llm_handler.execute_tool(cfg, "read_kb")
    assert '"services"' in out


def test_execute_tool_truncates(tmp_path, cfg):
    kb = tmp_path / "kb.md"
    kb.write_text("x" * 5000, encoding="utf-8")
    cfg.llm.tools["kb_path"] = str(kb)
    cfg.llm.tools["max_result_chars"] = 100
    out = llm_handler.execute_tool(cfg, "read_kb")
    assert "[…tronqué]" in out


# --------------------------------------------------------------- call_llm

class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _patch_post(monkeypatch, responses):
    calls = {"n": 0, "payloads": []}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["payloads"].append(json)
        payload = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return _Resp(payload)

    monkeypatch.setattr(llm_handler.requests, "post", fake_post)
    return calls


def test_call_llm_simple(monkeypatch, cfg):
    responses = [
        {"choices": [{"message": {"role": "assistant", "content": "Bonjour !"}}]}
    ]
    _patch_post(monkeypatch, responses)
    text, trace = llm_handler.call_llm(cfg, "Un prompt")
    assert text == "Bonjour !"
    assert trace == []


def test_call_llm_with_tool_call(monkeypatch, cfg, tmp_path):
    kb = tmp_path / "kb.md"
    kb.write_text("DiggerRadio: tarif 5 EUR/mois.", encoding="utf-8")
    cfg.llm.tools["kb_path"] = str(kb)

    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "read_kb", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"role": "assistant", "content": "Le tarif est de 5 EUR."}}]},
    ]
    calls = _patch_post(monkeypatch, responses)
    text, trace = llm_handler.call_llm(cfg, "Question tarif DiggerRadio")
    assert text == "Le tarif est de 5 EUR."
    assert trace == [{"name": "read_kb", "result_chars": trace[0]["result_chars"]}]
    assert trace[0]["result_chars"] > 0
    assert calls["n"] == 2
    # Le second appel doit contenir le message tool
    assert any(m.get("role") == "tool" for m in calls["payloads"][1]["messages"])


def test_call_llm_http_error(monkeypatch, cfg):
    def fake_post(*args, **kwargs):
        raise llm_handler.requests.RequestException("boom")

    monkeypatch.setattr(llm_handler.requests, "post", fake_post)
    with pytest.raises(llm_handler.LLMError):
        llm_handler.call_llm(cfg, "prompt")


def test_call_llm_max_iterations(monkeypatch, cfg):
    tool_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "read_kb", "arguments": "{}"},
                        }
                    ],
                }
            }
        ]
    }
    kb = cfg.llm.tools["kb_path"]
    import os

    if not os.path.exists(kb):
        # KB absente : l'outil renvoie une erreur texte, le LLM reçoit ce message.
        pass
    _patch_post(monkeypatch, [tool_resp])
    with pytest.raises(llm_handler.LLMError):
        llm_handler.call_llm(cfg, "prompt")


def test_call_llm_tools_disabled(monkeypatch, cfg):
    cfg.llm.tools["enabled"] = False
    responses = [{"choices": [{"message": {"role": "assistant", "content": "Réponse"}}]}]
    calls = _patch_post(monkeypatch, responses)
    text, trace = llm_handler.call_llm(cfg, "prompt")
    assert text == "Réponse"
    assert trace == []
    assert "tools" not in calls["payloads"][0]


class _FakeCfg:
    class llm:
        class tools:
            max_result_chars = 20000


class _FakeTools:
    max_result_chars = 20000