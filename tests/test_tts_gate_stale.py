"""A dead line's speaking flag must not hide his beam (the User 2026-10-03)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import tts_gate_stale as t  # noqa: E402

STUCK = {"speaking": True, "last_reply": "", "updated_ts": 1000.0, "air_at": 1000.0, "air_until": None, "air_firm": True}


def test_the_10_03_gate_is_stale():
    assert t.is_stale(STUCK, sink_live=False, now=1000.0 + 2400, env={})


def test_a_live_line_is_never_stale():
    assert not t.is_stale(STUCK, sink_live=True, now=1000.0 + 2400, env={})          # her stream is playing
    assert not t.is_stale(dict(STUCK, air_until=9e9), sink_live=False, now=3400, env={})
    assert not t.is_stale(STUCK, sink_live=False, now=1000.0 + 120, env={})            # under the ear's 180 s
    assert not t.is_stale(dict(STUCK, speaking=False), sink_live=False, now=9e9, env={})


def test_kill_switch():
    assert not t.is_stale(STUCK, sink_live=False, now=9e9, env={"VO_TTS_GATE_STALE_S": "0"})


def test_one_definition_with_the_ear():
    assert t.stale_after_s({}) == 180.0
    assert t.stale_after_s({"RAIL_TTS_STUCK_GATE_SEC": "240"}) == 240.0
