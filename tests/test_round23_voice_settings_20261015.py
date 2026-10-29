"""Round 23, her voice's settings (the User 2026-10-15, .101 round 22: Setup picked Audio8 with `settings: {}` and she
read "one sentence at a time", 6-7 chars/s against Pocket's 13-15; "never leave them empty").
The empty dict was not what slowed her (no reply path read Audio8's settings); a reply on Audio8 was buffered by
design, only the announcer streamed. Now every tier Setup can pick carries settings, an empty or partial record never
hides them, and Audio8's `stream_replies` streams her replies (the stream's own under-real-time fallback stays)."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


@pytest.fixture()
def T(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path))
    monkeypatch.delenv("CRISSA_AUDIO8_STREAM", raising=False)
    import crissa_home
    import crissa_voice_tiers
    import crissa_tts
    importlib.reload(crissa_home)
    vt = importlib.reload(crissa_voice_tiers)
    tts = importlib.reload(crissa_tts)
    monkeypatch.setattr(tts, "audio8_stream_note", lambda *a, **k: {})
    yield vt, tts, tmp_path
    monkeypatch.undo()
    importlib.reload(crissa_home)
    importlib.reload(crissa_voice_tiers)
    importlib.reload(crissa_tts)


def test_every_voice_setup_can_pick_for_her_has_settings(T):
    vt, _, _ = T
    tab = vt.table()
    for name in ("turbo", "multilingual", "pocket", "audio8"):
        assert tab["tiers"][name].get("settings"), name
    cfg = json.loads((ROOT / "config" / "crissa-voice-tiers.json").read_text())
    assert cfg["tiers"]["audio8"]["settings"] == {"stream_replies": True, "piece_chars": 72}


def test_round_22s_empty_record_no_longer_hides_them(T):
    vt, _, home = T
    (home / "voice-tier.json").write_text(json.dumps({"schema": vt.SCHEMA, "tier": "audio8", "main": "audio8",
                                                      "picked_by": "init", "settings": {}}))
    assert vt.record_path(home).name == "voice-tier.json"
    got = vt.settings("audio8", home)
    assert got["stream_replies"] is True and got["piece_chars"] == 72


def test_a_record_value_still_wins(T):
    vt, _, home = T
    (home / "voice-tier.json").write_text(json.dumps({"schema": vt.SCHEMA, "main": "pocket",
                                                      "settings": {"threads": 4}}))
    s = vt.settings("pocket", home)
    assert s["threads"] == 4 and s["emit"] == "frames" and s["chunk_first_max"] == 40


def test_her_replies_stream_on_audio8_and_the_switch_still_decides(T, monkeypatch):
    _, tts, _ = T
    assert tts.audio8_stream_wanted("crissa") is True
    assert tts.audio8_stream_wanted("announcer") is True
    monkeypatch.setenv("CRISSA_AUDIO8_STREAM", "0")
    assert tts.audio8_stream_wanted("crissa") is False
    monkeypatch.delenv("CRISSA_AUDIO8_STREAM")
    monkeypatch.setattr(tts, "audio8_streams_replies", lambda *a, **k: False)
    assert tts.audio8_stream_wanted("crissa") is False, "round 22: a reply did not stream"


def test_a_slow_stream_still_falls_back(T, monkeypatch):
    _, tts, _ = T
    monkeypatch.setattr(tts, "audio8_stream_note", lambda *a, **k: {"rate": 0.5})
    assert tts.audio8_stream_wanted("crissa") is False
