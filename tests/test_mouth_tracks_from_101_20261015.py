"""Mouth tracks fixed on .101 (the User 2026-10-15, round 22 notes: "You fixed the library we ship, not mine ... They
need to ship."). Two kinds of fix, both pinned here:

1. LIP PINS: on five flourishes every box sits on her own lip marks (`<stem>.lipmarks.json`), at most 10 px off, in
   every track the player can read (`.mouth.turnfill.json` first, then `.mouth.turn.json`, then `.mouth.json`);
2. TILE BLANKINGS: frames where a hand is inside the tile's width (stretch-eyes 23-101) or the head is back past
   10 degrees and the shroud flares (stretch-shoulders 60-94) are null, so the filmed mouth shows.
The checker is `scripts/mouth-rebox-lipmarks.py --check` (exit 1 on a box > 10 px off the lips).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POSES = ROOT / "display" / "crissa-poses"
TOOL = ROOT / "scripts" / "mouth-rebox-lipmarks.py"
FIXED = ("stretch-eyes", "bun-wobble", "bun-wobble-think", "stretch-neck", "stretch-shoulders")
BLANK = {"stretch-eyes": (23, 101), "stretch-shoulders": (60, 94)}


def _tracks():
    out = []
    for c in FIXED:
        out += sorted(POSES.glob(f"clip-flourish-{c}-work.mouth*.json"))
    return out


def test_every_fixed_track_is_present():
    names = {p.name for p in _tracks()}
    assert len(names) == 11, names
    for c in FIXED:
        assert (POSES / f"clip-flourish-{c}-work.lipmarks.json").is_file()


def test_lip_pins_hold_on_every_fixed_track():
    r = subprocess.run([sys.executable, str(TOOL), "--check", *map(str, _tracks())],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "skipped" not in r.stdout, r.stdout


def test_the_checker_fails_a_box_off_the_lips(tmp_path):
    src = POSES / "clip-flourish-bun-wobble-work.mouth.json"
    d = json.loads(src.read_text())
    i = next(n for n, b in enumerate(d["frames"]) if b)
    d["frames"][i] = [d["frames"][i][0] + 40, d["frames"][i][1], d["frames"][i][2], d["frames"][i][3]]
    t = tmp_path / src.name
    t.write_text(json.dumps(d))
    (tmp_path / "clip-flourish-bun-wobble-work.lipmarks.json").write_text(
        (POSES / "clip-flourish-bun-wobble-work.lipmarks.json").read_text())
    r = subprocess.run([sys.executable, str(TOOL), "--check", str(t)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "1 boxes" in r.stdout, r.stdout


@pytest.mark.parametrize("clip", sorted(BLANK))
def test_tile_blankings_stay_null(clip):
    lo, hi = BLANK[clip]
    for p in POSES.glob(f"clip-flourish-{clip}-work.mouth*.json"):
        f = json.loads(p.read_text())["frames"]
        assert all(f[i] is None for i in range(lo, hi + 1)), (p.name, [i for i in range(lo, hi + 1) if f[i]])
        assert f[lo - 1] is not None and f[hi + 1] is not None, p.name     # the blank is the measured span, no wider


# ---- batch 2 (.101, 10-15 evening): the flagged profile and walk frames --------------------------------------------
BATCH2 = ("clip-flourish-glance.mouth.turnfill.json", "clip-lila-reg-turn.mouth.turnfill.json",
          "clip-crissa-walk-in.mouth.json", "clip-crissa-walk-off.mouth.json")
BLANK2 = {"clip-flourish-glance.mouth.turnfill.json": (33, 40), "clip-crissa-walk-in.mouth.json": (100, 125),
          "clip-crissa-walk-off.mouth.json": (95, 131)}


def test_batch_2_holds_on_the_lips():
    r = subprocess.run([sys.executable, str(TOOL), "--check", *[str(POSES / n) for n in BATCH2]],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "skipped" not in r.stdout, r.stdout


@pytest.mark.parametrize("name", sorted(BLANK2))
def test_batch_2_blankings(name):
    lo, hi = BLANK2[name]
    f = json.loads((POSES / name).read_text())["frames"]
    assert all(f[i] is None for i in range(lo, min(hi, len(f) - 1) + 1)), name
