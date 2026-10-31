"""Name-barge-in: stop the mouth; do not talk over him."""
from __future__ import annotations

import json
from pathlib import Path

from crissa_bridge.ear_bridge import (
    abort_mouth,
    ear_still_decoding,
    ear_still_hearing,
    name_interrupt_hit,
    _env_http_timeout,
    _gate_notice,
    _write_tts_gate,
)


def test_crissa_gateway_timeout_zero_is_unbounded(monkeypatch):
    monkeypatch.delenv("EAR_BRIDGE_CRISSA_TIMEOUT", raising=False)
    assert _env_http_timeout("EAR_BRIDGE_CRISSA_TIMEOUT", 0.0) is None
    monkeypatch.setenv("EAR_BRIDGE_CRISSA_TIMEOUT", "0")
    assert _env_http_timeout("EAR_BRIDGE_CRISSA_TIMEOUT", 90.0) is None
    monkeypatch.setenv("EAR_BRIDGE_CRISSA_TIMEOUT", "none")
    assert _env_http_timeout("EAR_BRIDGE_CRISSA_TIMEOUT", 90.0) is None
    monkeypatch.setenv("EAR_BRIDGE_CRISSA_TIMEOUT", "90")
    assert _env_http_timeout("EAR_BRIDGE_CRISSA_TIMEOUT", 0.0) == 90.0


def test_name_interrupt_only_while_on_air():
    assert name_interrupt_hit("Crissa, wait", speaking=False, busy=False) is False
    assert name_interrupt_hit("Crissa, wait", speaking=True, busy=False) is True
    assert name_interrupt_hit("Hey Crissa", speaking=False, busy=True) is True
    assert name_interrupt_hit("Crissa.", speaking=True, busy=False) is True


def test_name_interrupt_ignores_other_speech():
    assert name_interrupt_hit("so we should go", speaking=True, busy=True) is False
    assert name_interrupt_hit("Hey Lila, draw that", speaking=True, busy=False) is False


def test_name_interrupt_tv_scratch_does_not_cut():
    """Same case as unpause: TV 'Hey Zoe' must not abort her mouth."""
    assert (
        name_interrupt_hit(
            "Hey Crissa.",
            speaking=True,
            busy=False,
            person_id="s1",
        )
        is False
    )
    assert (
        name_interrupt_hit(
            "Hey Crissa",
            speaking=True,
            busy=True,
            stamp={"media_gate": {"verdict": "media", "media_on": True}},
        )
        is False
    )
    assert (
        name_interrupt_hit("Hey Crissa, wait", speaking=True, busy=False, person_id="user")
        is True
    )


def test_ear_still_hearing_reads_live_flags(monkeypatch):
    def fake_get(url, timeout=3.0):
        assert "health" in url
        return {"ear": {"in_utterance": True, "decoding": False}}

    monkeypatch.setattr("crissa_bridge.ear_bridge._get", fake_get)
    assert ear_still_hearing() is True

    monkeypatch.setattr(
        "crissa_bridge.ear_bridge._get",
        lambda *a, **k: {"ear": {"in_utterance": False, "decoding": True}},
    )
    assert ear_still_hearing() is True
    monkeypatch.setattr(
        "crissa_bridge.ear_bridge._get",
        lambda *a, **k: {"ear": {"in_utterance": False, "decoding": False}},
    )
    assert ear_still_hearing() is False


def test_ear_still_decoding_ignores_keyboard_vad(monkeypatch):
    monkeypatch.setattr(
        "crissa_bridge.ear_bridge._get",
        lambda *a, **k: {"ear": {"in_utterance": True, "decoding": False}},
    )
    assert ear_still_hearing() is True
    assert ear_still_decoding() is False
    monkeypatch.setattr(
        "crissa_bridge.ear_bridge._get",
        lambda *a, **k: {"ear": {"in_utterance": False, "decoding": True}},
    )
    assert ear_still_decoding() is True


def test_abort_mouth_writes_interrupted_notice(tmp_path, monkeypatch):
    gate = tmp_path / "house-tts-gate.json"
    monkeypatch.setattr("crissa_bridge.ear_bridge.TTS_GATE", gate)
    monkeypatch.setattr("crissa_bridge.ear_bridge.LOG", tmp_path / "ear.jsonl")
    monkeypatch.setattr(
        "crissa_bridge.ear_bridge.urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no cosy")),
    )
    abort_mouth("name_interrupt")
    g = json.loads(gate.read_text())
    assert g["speaking"] is False
    assert g["notice"] == "interrupted"
    assert g["last_reply"] == ""


def test_gate_notice_fresh_and_stale(tmp_path, monkeypatch):
    gate = tmp_path / "house-tts-gate.json"
    monkeypatch.setattr("crissa_bridge.ear_bridge.TTS_GATE", gate)
    _write_tts_gate(
        speaking=False, last_reply="", deaf_until=0.0, notice="interrupted"
    )
    assert _gate_notice() == "interrupted"
    g = json.loads(gate.read_text())
    g["updated_ts"] = 1.0
    gate.write_text(json.dumps(g))
    assert _gate_notice() == ""
