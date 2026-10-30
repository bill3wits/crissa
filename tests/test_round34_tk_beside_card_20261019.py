"""Round 34 (.101 round 33 on the full desk Wall: "render did not move window"): on a pack's Tk Wall her render - the
corner tile with BLUR / WALL on its bar - sits in the player's layout box beside the card while it is up, at its own
shape and left of the card's sheet, and goes back to its corner when the card is down. Never on the house."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import house_wall_ascii as A  # noqa: E402
import house_wall_ascii_render as R  # noqa: E402
import crissa_layout  # noqa: E402


def test_the_card_box_is_the_players_layout_in_glass_px():
    W, H = 2560, 1440
    box = A.card_box(W, H, decide=lambda up, screen=None: crissa_layout.decide(
        up, cfg={"move": True, "anchor": "center"}, screen=screen))
    want = crissa_layout.center_box((W, H))
    assert box == tuple(int(round(v)) for v in (want[0] * W, want[1] * H, want[2] * W, want[3] * H))
    x, y, w, h = box
    sheet_left = R.letter_rect(R.Geom(W, H))[0]
    assert x + w <= sheet_left, "left of the card's sheet: in view"


def test_no_layout_box_keeps_her_home():
    assert A.card_box(1920, 1080, decide=lambda up, screen=None: {"render": "home", "box": None}) is None
    assert A.card_box(1920, 1080, decide=lambda up, screen=None: 1 / 0) is None


def test_her_tile_takes_the_box_at_its_own_shape():
    g = R.Geom(2560, 1440)
    box = (154, 275, 1178, 663)
    for mode, ar in (("room", 16 / 9), ("ft", 4 / 6)):
        x, y, w, h = R.ft_box(g, {"ft_mode": mode, "ft_card_box": box, "ft_big": True, "ft_dx": 500})
        assert abs(w / h - ar) < 0.01
        assert box[0] <= x and x + w <= box[0] + box[2] + 1 and box[1] <= y and y + h <= box[1] + box[3] + 1
    home = R.ft_box(g, {"ft_mode": "room"})
    assert home != R.ft_box(g, {"ft_mode": "room", "ft_card_box": box}), "the card box moves her"
    assert R.ft_tile_from_xy(g, 10, 10, {"ft_card_box": box}) == R.ft_tile_from_xy(g, 10, 10, {}), "a drag reads home"


def test_a_pack_only_and_the_kill(monkeypatch):
    monkeypatch.setattr(A.crissa_home, "is_house", lambda *a, **k: False)
    assert A.render_beside_card_on(env={})
    assert not A.render_beside_card_on(env={"CRISSA_WALL_RENDER_BESIDE_CARD": "0"})
    monkeypatch.setattr(A.crissa_home, "is_house", lambda *a, **k: True)
    assert not A.render_beside_card_on(env={})


def test_the_wall_reads_it_with_the_page_up():
    # round 35: the trigger is the player's own /v1/layout (round 34's page check never saw the card on .101)
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert "cb = layout_tile_box(lay_p, W, H) if render_beside_card_on() else None" in src
    assert 'out["ft_card_box"] = cb' in src
