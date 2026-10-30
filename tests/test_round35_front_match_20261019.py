"""Round 35, 2026-10-19 (the User via .101 round 33: "check pics, they are all back of head practically"): a turned still
Voice ID asks to store must match the person's own front prints; under the turn-aware bar it is refused (not_his_face)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "presence"))


@pytest.fixture
def sf(monkeypatch):
    monkeypatch.delenv("CRISSA_FACE_FRONT_MATCH", raising=False)
    from crissa_presence import store_face
    return store_face


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def _print(d: Path, name: str, b: str, v):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({"bin": b, "embedding": [float(x) for x in v]}))


def test_the_bar_follows_the_turn(sf):
    assert sf.front_bar(-0.30) == sf.FRONT_MATCH_TURN == 0.30
    assert sf.front_bar(1.87) == sf.FRONT_MATCH_PROFILE == 0.17
    assert sf.front_bar(None) == sf.FRONT_MATCH_TURN


def test_front_sim_reads_only_his_front_prints(sf, tmp_path):
    a = _unit([1.0] + [0.0] * 15)
    b = _unit([0.0, 1.0] + [0.0] * 14)
    _print(tmp_path, "001.json", "front", a)
    _print(tmp_path, "002.json", "profile_R", b)          # a profile print is never the reference
    assert sf.front_sim(a, tmp_path) == pytest.approx(1.0)
    assert sf.front_sim(b, tmp_path) == pytest.approx(0.0)
    assert sf.front_sim(a, tmp_path / "nobody") is None, "no front print yet: no check"


def test_the_kill(sf, monkeypatch):
    assert sf.front_match_on()
    monkeypatch.setenv("CRISSA_FACE_FRONT_MATCH", "0")
    assert not sf.front_match_on()


def test_the_store_refuses_a_still_under_the_bar_and_says_its_number():
    src = (ROOT / "modules/presence/crissa_presence/store_face.py").read_text()
    assert 'if match_front and front_match_on():' in src and 'out["error"] = "not_his_face"' in src
    assert 'out["front_sim"] = None if fs is None else round(fs, 4)' in src
    srv = (ROOT / "modules/presence/crissa_presence/server.py").read_text()
    assert 'match_front=bool(body.get("match_front")),' in srv


@pytest.mark.parametrize("d", ["modules/id/crissa_id/house_enroll", "modules/presence/crissa_presence/enroll_panel"])
def test_both_copies_ask_for_it_on_a_turned_still(d):
    src = (ROOT / d / "voice_register.py").read_text()
    assert '"match_front": bool(side) and turn_check_on()},' in src
