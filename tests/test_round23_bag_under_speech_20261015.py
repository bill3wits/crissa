"""Round 23, the bag under speech (the User 2026-10-15, .101 "tile picking is not great": with no krew - "the bag
picks" - yawn-work and three stretches played under her reading; "no yawns/stretches under speech; a talking plate under
a reading"). A pack holds them like a `speech: false` row, and the seconds between a reading's pages count as on air.
The house sets neither (both empty / 0)."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _conductor(monkeypatch, clips: str):
    monkeypatch.setenv("HOUSE_SPEECH_HOLD_CLIPS", clips)
    import conductor
    return importlib.reload(conductor)


def test_the_pack_holds_yawn_and_stretches(monkeypatch):
    env = json.loads((ROOT / "config" / "crissa-mouth.json").read_text())["env"]
    assert env["HOUSE_SPEECH_HOLD_CLIPS"] == "flourish-yawn-work,flourish-stretch-*,flourish-bun-wobble-work"   # round 31 (.101 fix 17)
    assert float(env["HOUSE_SPEECH_TAIL_HOLD_S"]) == 3.0
    c = _conductor(monkeypatch, env["HOUSE_SPEECH_HOLD_CLIPS"])
    try:
        for k in ("flourish-yawn-work", "flourish-stretch-neck-work", "flourish-stretch-shoulders-work",
                  "flourish-stretch-eyes-work"):
            assert c.speech_held(k), k
        assert c.speech_held("flourish-bun-wobble-work")     # round 31 (.101 fix 17): waits for a pause too
        for k in ("flourish-glance", "flourish-bun-wobble-think-work", "listen"):
            assert not c.speech_held(k), k
    finally:
        monkeypatch.delenv("HOUSE_SPEECH_HOLD_CLIPS")
        importlib.reload(c)


def test_the_house_holds_nothing_more(monkeypatch):
    c = _conductor(monkeypatch, "")
    try:
        assert c.SPEECH_HOLD_CLIPS == () and not c.speech_held("flourish-stretch-neck-work")
    finally:
        importlib.reload(c)


def test_speech_ok_reads_the_list():
    src = (ROOT / "lib" / "conductor.py").read_text()
    assert '.get("speech") is not False and not speech_held(key)' in src


def test_a_readings_pages_are_one_turn():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'SPEECH_TAIL_HOLD_S = max(0.0, min(10.0, float(os.environ.get("HOUSE_SPEECH_TAIL_HOLD_S", "0") or 0)))' in src
    assert "elif SPEECH_TAIL_HOLD_S > 0 and time.time() - float(getattr(self, \"_air_last\", 0.0) or 0.0) < SPEECH_TAIL_HOLD_S:" in src
    assert "self.seq.hold_flourishes = on_air" in src
