"""Round 27: the Wall's log scrolls and fades line by line (.101 2026-10-16: "her reading sits as a fixed 5-line block at
the bottom (older text just drops off the top edge)"; asked in round 25 too)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import house_wall_ascii_render as R  # noqa: E402

LONG = " ".join(["Mix the flour, the yeast and the warm water until a soft dough forms, then knead it."] * 14)


def _ink_rows(img):
    a = img.getchannel("A")
    W, H = img.size
    px = a.load()
    rows = {}
    for y in range(0, H, 2):
        m = max(px[x, y] for x in range(0, W, 3))
        if m:
            rows[y] = m
    return rows


def test_a_tall_reading_is_dim_at_the_top_and_full_at_the_floor(monkeypatch):
    monkeypatch.setattr(R, "ROLL_SMOOTH", True)
    g = R.Geom(1280, 720)
    roll = R.Roll(since=0)
    roll.lines = [{"who": "crissa", "text": LONG, "at": 100.0, "open": True, "ts": 100.0}]
    rows = _ink_rows(roll.draw(g, now=100.0))
    ys = sorted(rows)
    low = max(rows[y] for y in ys if y >= ys[-1] - 40)
    high = max(rows[y] for y in ys if y <= ys[0] + 20)
    assert low >= 250 and high < 160                        # newest lines full, the oldest faded
    monkeypatch.setattr(R, "ROLL_SMOOTH", False)
    rows_old = _ink_rows(R.Roll.draw(roll, g, now=100.0))
    assert max(rows_old.values()) < 60                      # round 26: the whole block dimmed by its top line


def test_a_new_line_rises_in_rather_than_jumping(monkeypatch):
    monkeypatch.setattr(R, "ROLL_SMOOTH", True)
    g = R.Geom(1280, 720)
    roll = R.Roll(since=0)
    ln = {"who": "crissa", "text": "One short line.", "at": 100.0, "open": True, "ts": 100.0}
    roll.lines = [ln]
    roll.draw(g, now=100.0)
    top0 = roll.placed[0][1]
    ln["text"] = LONG[:400]                                  # her line grew by several wrapped lines
    roll.draw(g, now=100.05)
    mid = roll.placed[0][1]
    for k in range(40):
        roll.draw(g, now=100.1 + k * 0.1)
    end = roll.placed[0][1]
    assert end < mid < top0 and (top0 - mid) < (top0 - end) / 2


def test_the_house_keeps_its_roll_unless_asked():
    src = (ROOT / "lib" / "house_wall_ascii_render.py").read_text()
    assert '"HOUSE_WALL_ROLL_SMOOTH", "0" if crissa_home.is_house() else "1"' in src
