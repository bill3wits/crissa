"""Playback sink: AEC loopback for STT cancel."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from audio_sink import live_mouth_line, speak_sink  # noqa: E402


def test_speak_sink_uses_aec_when_present(monkeypatch):
    sinks = [
        "echocancel_sink",
        "alsa_output.usb-ESS_Technology_AUDIO_DONGLE-00.analog-stereo",
        "alsa_output.usb-Seeed_Studio_reSpeaker_XVF3800_x-00.analog-stereo",
    ]
    monkeypatch.setattr("audio_sink._sink_names", lambda: sinks)
    monkeypatch.delenv("VOICE_SINK", raising=False)
    assert speak_sink() == "echocancel_sink"
    assert speak_sink("echocancel_sink") == "echocancel_sink"


def test_speak_sink_never_seeed(monkeypatch):
    sinks = [
        "alsa_output.usb-Seeed_Studio_reSpeaker_XVF3800_x-00.analog-stereo",
        "alsa_output.usb-ESS_Technology_AUDIO_DONGLE-00.analog-stereo",
    ]
    monkeypatch.setattr("audio_sink._sink_names", lambda: sinks)
    monkeypatch.delenv("VOICE_SINK", raising=False)
    assert "Seeed" not in speak_sink()
    assert speak_sink() == "alsa_output.usb-ESS_Technology_AUDIO_DONGLE-00.analog-stereo"


def test_live_mouth_line_only_while_speaking(tmp_path, monkeypatch):
    gate = tmp_path / "house-tts-gate.json"
    monkeypatch.setattr("audio_sink.TTS_GATE", gate)
    gate.write_text(
        json.dumps(
            {
                "speaking": True,
                "last_reply": "I'm here.",
                "who": "crissa",
                "updated_ts": 1787250000.0,
            }
        )
    )
    line = live_mouth_line()
    assert line is not None
    assert line["text"] == "I'm here."
    assert line["who"] == "crissa"
    assert line["live"] is True
    assert line["source"] == "mouth_air"
    gate.write_text(
        json.dumps(
            {
                "speaking": False,
                "last_reply": "I'm here.",
                "who": "crissa",
                "updated_ts": 1787250000.0,
            }
        )
    )
    after = live_mouth_line()
    assert after is not None
    assert after["text"] == "I'm here."
    assert after["live"] is False
    gate.write_text(
        json.dumps({"speaking": True, "last_reply": "announcer", "who": "announcer"})
    )
    assert live_mouth_line() is None


def test_mark_tts_gate_keeps_line_when_mouth_closes(tmp_path, monkeypatch):
    from audio_sink import mark_tts_gate

    gate = tmp_path / "house-tts-gate.json"
    monkeypatch.setattr("audio_sink.TTS_GATE", gate)
    mark_tts_gate(speaking=True, last_reply="I'm here.", who="crissa", extra_sec=0.4)
    mark_tts_gate(speaking=False, last_reply="", who="", extra_sec=0.0)
    line = live_mouth_line()
    assert line is not None
    assert line["text"] == "I'm here."
    assert line["who"] == "crissa"
    assert line["live"] is False


def test_live_mouth_line_keeps_800_chars(tmp_path, monkeypatch):
    from audio_sink import MOUTH_TEXT_CAP, mark_tts_gate

    gate = tmp_path / "house-tts-gate.json"
    monkeypatch.setattr("audio_sink.TTS_GATE", gate)
    long = "x" * 900
    mark_tts_gate(speaking=True, last_reply=long, who="crissa", extra_sec=0.4)
    line = live_mouth_line()
    assert line is not None
    assert len(line["text"]) == MOUTH_TEXT_CAP
    assert len(line["text"]) > 220


def test_mark_tts_gate_atomic_json(tmp_path, monkeypatch):
    from audio_sink import mark_tts_gate

    gate = tmp_path / "house-tts-gate.json"
    monkeypatch.setattr("audio_sink.TTS_GATE", gate)
    mark_tts_gate(speaking=True, last_reply="First.", who="crissa", extra_sec=0.4)
    json.loads(gate.read_text(encoding="utf-8"))
    mark_tts_gate(speaking=False, last_reply="First.", who="crissa", extra_sec=0.0)
    g = json.loads(gate.read_text(encoding="utf-8"))
    assert g["speaking"] is False
    assert g["last_reply"] == "First."
    leftovers = list(tmp_path.glob(".tts-gate-*"))
    assert leftovers == []


def test_speak_sink_never_returns_a_mic_name(monkeypatch):
    mic = "alsa_output.usb-Seeed_Studio_reSpeaker_XVF3800_x-00.analog-stereo"
    monkeypatch.setattr("audio_sink._sink_names", lambda: [mic])
    monkeypatch.setattr("audio_sink._default_sink", lambda: mic)
    monkeypatch.setattr("audio_sink.AEC_SINK", "")
    monkeypatch.setattr("audio_sink.MIC_ARRAY_MARK", "Seeed")
    monkeypatch.setattr("audio_sink.DONGLE_SINK_MARK", "")
    monkeypatch.setenv("VOICE_SINK", mic)
    assert speak_sink(mic) == "@DEFAULT_SINK@"
    assert "Seeed" not in speak_sink(mic)
