"""A guest's reply and the backup LLM go to the Connect endpoint (the house switched two local engines by its
GPU occupancy; a pack has one agent, 2026-10-09). An env URL still names another model for guests."""
from __future__ import annotations

import json

from crissa_bridge import connect
from crissa_bridge.occupancy_llm import chat, gpu_mode


def test_no_occupancy_in_a_pack():
    assert gpu_mode() == "normal"


def test_guest_goes_to_the_connect_endpoint(tmp_path, monkeypatch):
    (tmp_path / "agent.json").write_text(json.dumps({"url": "http://127.0.0.1:9999/v1", "model": "agent-x"}))
    monkeypatch.setattr(connect.A, "HOME", tmp_path, raising=False)
    monkeypatch.setattr(connect.A, "path", lambda home=None: tmp_path / "agent.json")
    monkeypatch.delenv("RAIL_VOICE_LLM", raising=False)
    monkeypatch.delenv("EAR_BRIDGE_OPS", raising=False)
    url, model = chat()
    assert url == "http://127.0.0.1:9999/v1/chat/completions"
    assert model == "agent-x"


def test_env_names_a_lighter_guest_model(monkeypatch):
    monkeypatch.setenv("RAIL_VOICE_LLM", "http://127.0.0.1:8000/v1/chat/completions")
    monkeypatch.setenv("RAIL_VOICE_MODEL", "small")
    assert chat() == ("http://127.0.0.1:8000/v1/chat/completions", "small")


def test_nothing_connected_is_an_empty_url(tmp_path, monkeypatch):
    monkeypatch.setattr(connect.A, "path", lambda home=None: tmp_path / "agent.json")
    monkeypatch.delenv("RAIL_VOICE_LLM", raising=False)
    monkeypatch.delenv("EAR_BRIDGE_OPS", raising=False)
    for k in ("CRISSA_AGENT_URL", "CRISSA_AGENT_KEY", "CRISSA_AGENT_MODEL", "CRISSA_AGENT_PRESET"):
        monkeypatch.delenv(k, raising=False)
    url, _model = chat()
    assert url == ""
