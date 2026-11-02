"""Status marks (2026-10-01): off by default; every optional piece is ok / fallback / off, never an error."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_status as S  # noqa: E402
import crissa_tts as T  # noqa: E402


def test_off_by_default():
    assert S.enabled({}) is False and S.enabled({"CRISSA_STATUS_MARKS": "1"}) is True
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'if head == "/v1/marks" and crissa_status.enabled():' in src


def test_every_piece_has_a_mark_and_none_is_an_error(monkeypatch):
    monkeypatch.delenv("CRISSA_ORACLE", raising=False)
    monkeypatch.delenv("CRISSA_EAR_TURNS", raising=False)
    got = S.marks()
    assert [m["piece"] for m in got] == ["agent", "voice", "ear", "mouths", "oracle", "beam", "import", "library"]
    for m in got:
        assert m["state"] in S.GLYPH and m["mark"] == S.GLYPH[m["state"]]
        assert len(m["note"]) <= 40 and "/" not in m["note"] and "Error" not in m["note"]


def test_a_piece_that_raises_is_a_quiet_off_mark(monkeypatch):
    def boom():
        raise RuntimeError("secret /path sk-key")
    boom.__name__ = "agent_mark"
    monkeypatch.setattr(S, "agent_mark", boom)
    m = S.marks()[0]
    assert m == {"piece": "agent", "state": "off", "mark": S.GLYPH["off"], "note": "not available"}


def test_no_beam_is_the_decorative_line_and_a_dead_oracle_is_a_fallback():
    assert S.beam_mark({})["note"] == "no beam sig"
    assert S.oracle_mark({})["state"] == "off"
    dead = {"CRISSA_ORACLE": "http://127.0.0.1:9"}
    assert S.oracle_mark(dead)["state"] == "fallback" and S.beam_mark(dead)["note"] == "no beam sig"
    assert S.ear_mark({"CRISSA_EAR_TURNS": "http://127.0.0.1:9/v1/turns"}) == \
        {"piece": "ear", "state": "fallback", "mark": S.GLYPH["fallback"], "note": "typing only for now"}


def test_the_voice_mark_reads_the_fallback_record(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "VOICE_STATE", tmp_path / "voice-state.json")
    env = {"CRISSA_TTS": "openai", "CRISSA_TTS_URL": "http://127.0.0.1:9/v1"}
    assert S.voice_out_mark(env)["note"] == "speech address"
    (tmp_path / "voice-state.json").write_text(json.dumps({"spoke": "say", "named": "openai", "fallback": True}))
    assert S.voice_out_mark(env) == {"piece": "voice", "state": "fallback", "mark": S.GLYPH["fallback"],
                                     "note": "speaking with say"}
