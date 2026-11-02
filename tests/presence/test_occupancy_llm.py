"""Door VLM / crossing describe follow occupancy: crew in Render, ops otherwise.

A pack (2026-10-10): the house's two engine ports are gone. Normal asks ports.brain() - a variable that names one, else
the Connect endpoint when it is on this machine, else nothing (the describers skip); Render / Beast ride
RAIL_PRESENCE_CREW_URL when an install names a second engine, else the same one.
"""
from __future__ import annotations

import json

from crissa_presence import paths
from crissa_presence.occupancy_llm import chat, gpu_mode


def _clear(monkeypatch):
    for k in ("RAIL_PRESENCE_VLM_URL", "RAIL_OPS_URL", "RAIL_PRESENCE_VLM_MODEL", "RAIL_OPS_MODEL", "CRISSA_AGENT_URL",
              "CRISSA_AGENT_MODEL", "RAIL_PRESENCE_CREW_URL"):
        monkeypatch.delenv(k, raising=False)


def _mode(tmp_path, monkeypatch, mode):
    f = tmp_path / "house-gpu-mode"
    f.write_text(mode + "\n")
    monkeypatch.setattr("crissa_presence.occupancy_llm.MODE_FILE", f)


def _connect(tmp_path, monkeypatch, url, model="local-27b"):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "agent.json").write_text(json.dumps({"url": url, "model": model}))
    monkeypatch.setattr(paths, "HOME", home)


def test_normal_is_the_connect_engine_on_this_machine(tmp_path, monkeypatch):
    _clear(monkeypatch)
    _mode(tmp_path, monkeypatch, "normal")
    _connect(tmp_path, monkeypatch, "http://127.0.0.1:18000/v1")
    assert gpu_mode() == "normal"
    assert chat() == ("http://127.0.0.1:18000/v1/chat/completions", "local-27b")


def test_a_remote_connect_endpoint_gets_no_room_picture(tmp_path, monkeypatch):
    _clear(monkeypatch)
    _mode(tmp_path, monkeypatch, "normal")
    _connect(tmp_path, monkeypatch, "https://api.example.com/v1")
    url, _model = chat()
    assert url == "", "a picture of the room never leaves the machine unless a variable names where"


def test_no_engine_anywhere_means_no_describer_and_never_the_houses_port(tmp_path, monkeypatch):
    _clear(monkeypatch)
    _mode(tmp_path, monkeypatch, "normal")
    monkeypatch.setattr(paths, "HOME", tmp_path / "empty")
    url, model = chat()
    assert url == "" and model == "ops"


def test_a_named_engine_wins(tmp_path, monkeypatch):
    _clear(monkeypatch)
    _mode(tmp_path, monkeypatch, "normal")
    monkeypatch.setenv("RAIL_PRESENCE_VLM_URL", "http://127.0.0.1:8000/v1/chat/completions")
    monkeypatch.setenv("RAIL_PRESENCE_VLM_MODEL", "ops")
    assert chat() == ("http://127.0.0.1:8000/v1/chat/completions", "ops")


def test_render_rides_a_named_crew(tmp_path, monkeypatch):
    _clear(monkeypatch)
    _mode(tmp_path, monkeypatch, "render")
    monkeypatch.setenv("RAIL_PRESENCE_VLM_URL", "http://127.0.0.1:18000/v1/chat/completions")
    monkeypatch.setenv("RAIL_PRESENCE_CREW_URL", "http://127.0.0.1:18005/v1/chat/completions")
    assert chat() == ("http://127.0.0.1:18005/v1/chat/completions", "crew")


def test_beast_without_a_crew_keeps_the_one_engine(tmp_path, monkeypatch):
    _clear(monkeypatch)
    _mode(tmp_path, monkeypatch, "beast")
    monkeypatch.setenv("RAIL_PRESENCE_VLM_URL", "http://127.0.0.1:18000/v1/chat/completions")
    assert chat()[0] == "http://127.0.0.1:18000/v1/chat/completions"
