"""Round 23 pack defaults (the User 2026-10-15, .101's round-22 trial: "smoothing should ship with those settings as
defaults. perfect run in that respect"; "You fixed the library we ship, not mine"). The trial drop-in's values are the
pack's own now; only lip sync stays per machine. The house reads none of this (its units carry their own drop-ins)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "lib", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

TRIAL = {"HOUSE_MOUTH_SEMI_GRID": "1", "HOUSE_MOUTH_POLL_S": "0.02", "HOUSE_MOUTH_XFADE_MS": "20.833",
         "HOUSE_MOUTH_CHEEKS_MAPS": "1024", "HOUSE_MOUTH_DENTAL_MAPS": "1024", "HOUSE_SPEAK_STAMP_HOLD": "0"}


def _mouth_env(face):
    import importlib.util
    spec = importlib.util.spec_from_file_location("crissa_launch_r23", ROOT / "scripts" / "crissa.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.mouth_env(face)


def test_the_player_starts_on_the_trial_values_for_both_faces():
    for face in ("crissa", "lila"):
        env = _mouth_env(face)
        for k, v in TRIAL.items():
            assert env.get(k) == v, (face, k, env.get(k))


def test_the_crossfade_is_one_semiframe():
    import mouth_timing
    xf = float(json.loads((ROOT / "config" / "crissa-mouth.json").read_text())["env"]["HOUSE_MOUTH_XFADE_MS"])
    semi = 1000.0 / 24 / mouth_timing.SLOTS_PER_FRAME
    assert abs(xf - semi) < 0.001, (xf, semi)


def test_the_house_keeps_its_own_lock_crossfade():
    d = json.loads((ROOT / "display" / "crissa-poses" / "mouth-dials.json").read_text())
    assert float(d["xfade_ms"]) == 42.0
