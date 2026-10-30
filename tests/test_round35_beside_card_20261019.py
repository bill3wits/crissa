"""Round 35, 2026-10-19 (.101 round 34: "render window does not shift to center when she displays document"): the desk
Tk Wall reads the player's own /v1/layout and puts her render tile in that box; acceptance walks it (beside-card)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import house_wall_ascii as W  # noqa: E402


def test_the_tile_takes_the_players_box_in_glass_px():
    lay = {"render": "on_tile", "box": [0.06, 0.27, 0.46, 0.46], "unit": "fraction"}
    assert W.layout_tile_box(lay, 1920, 1080) == (115, 292, 883, 497)


def test_home_or_no_box_keeps_the_tile_home():
    assert W.layout_tile_box({"render": "home", "box": None}, 1920, 1080) is None
    assert W.layout_tile_box({}, 1920, 1080) is None
    assert W.layout_tile_box(None, 1920, 1080) is None
    assert W.layout_tile_box({"render": "on_tile", "box": [0, 0, 0, 0.4], "unit": "fraction"}, 1920, 1080) is None


def test_the_wall_reads_the_player_not_the_house_feed():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert 'lay = http_json(f"{FEED}/v1/layout", timeout=2.0)' in src
    assert "cb = layout_tile_box(lay_p, W, H) if render_beside_card_on() else None" in src
    assert "elif PLAYER_CARD:" in src and "st.house = dict(st.house or {}, screen=scr)" in src


def test_acceptance_walks_it_by_default():
    acc = (ROOT / "scripts" / "public-loops-accept.py").read_text()
    assert "bl, bf = _beside_card_case(agent)" in acc and '"beside-card": _beside_card_case' in acc
