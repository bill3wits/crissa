"""Her render sits on the Oracle live tile while a card is up and goes home when it comes down (2026-10-06):
the tile comes from config and the ASCII Wall's published anchors (read only), its placeholder's spot when the
live tile is missing, nothing moves without a card, and no timer drives it (lib/crissa_layout.py, GET
/v1/layout, display/crissa-wall.html)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_layout as zl  # noqa: E402

LIVE = [10.0, 1168.0, 409.0, 230.0]
SPARE = [11.0, 1169.0, 410.0, 230.0]


def test_card_up_puts_her_on_the_live_tile_and_down_sends_her_home(tmp_path):
    (tmp_path / "house-wall-ascii.anchors.json").write_text(json.dumps({"camera_box": LIVE, "pip": SPARE}))
    cfg = dict(zl.load(tmp_path), anchor="camera_box")        # round 27: the shipped anchor is "center"
    before = zl.decide(False, cfg, home=tmp_path)
    during = zl.decide(True, cfg, home=tmp_path)
    after = zl.decide(False, cfg, home=tmp_path)
    assert before["render"] == after["render"] == "home" and before["box"] is None
    assert during == {"render": "on_tile", "box": LIVE, "unit": "screen_px", "spot": "camera_box",
                      "why": "on the Oracle live tile"}
    assert "camera_box" in zl.line(during) and zl.line(after).startswith("render home")


def test_no_link_uses_the_placeholder_spot_then_the_config_box(tmp_path):
    cfg = dict(zl.load(tmp_path), anchor="camera_box")
    d = zl.decide(True, cfg, marks={"pip": SPARE})
    assert d["spot"] == "pip" and d["box"] == SPARE and "placeholder" in d["why"]
    d = zl.decide(True, cfg, marks={})
    assert d["spot"] == "box_if_no_wall" and d["unit"] == "fraction" and d["box"] == cfg["box_if_no_wall"]
    bad = dict(cfg, box_if_no_wall=None)
    d = zl.decide(True, bad, marks={"camera_box": [1, 2, 0, 0]})   # a zero-size tile is not a tile
    assert d["render"] == "home" and "config" in d["why"]


def test_the_anchor_lives_in_config_and_the_home_file_wins(tmp_path):
    shipped = json.loads((ROOT / "config" / "crissa-wall-layout.json").read_text())["card_render"]
    assert shipped["anchor"] == "center" and shipped["fallback"] == "pip"      # round 27: in view beside the page
    (tmp_path / "wall-layout.json").write_text(json.dumps({"card_render": {"anchor": "oracle_tile", "junk": 1}}))
    cfg = zl.load(tmp_path)
    assert cfg["anchor"] == "oracle_tile" and "junk" not in cfg
    assert zl.decide(True, cfg, marks={"oracle_tile": LIVE, "camera_box": SPARE})["box"] == LIVE
    (tmp_path / "wall-layout.json").write_text(json.dumps({"card_render": {"move": False}}))
    assert zl.decide(True, zl.load(tmp_path), marks={"camera_box": LIVE})["render"] == "home"


def test_page_moves_only_the_render_with_no_timer_and_player_serves_it():
    page = (ROOT / "display" / "crissa-wall.html").read_text()
    m = re.search(r"async function placeRender\(\) \{(.*?)\n\}", page, re.S)
    assert m, "placeRender missing"
    body = m.group(1)
    assert "/v1/layout" in body and '$("face")' in body
    assert "setTimeout" not in body and "setInterval" not in body
    assert "card" not in body and "pre" not in body          # the card and its art do not move
    card = re.search(r"async function refreshCard\(\) \{(.*?)\n\}", page, re.S).group(1)
    assert "placeRender();" in card
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'head == "/v1/layout"' in src and 'out["layout"] = layout_now(' in src
    lay = (ROOT / "lib" / "crissa_layout.py").read_text()
    assert "write_text" not in lay and "house_wall_ascii" not in lay   # reads the ASCII Wall's file, never writes


def test_round27_center_keeps_her_large_and_clear_of_the_sheet(tmp_path):
    d = zl.decide(True, zl.load(tmp_path), screen=(3440, 1440))
    assert d["spot"] == "center" and d["unit"] == "fraction" and "in view" in d["why"]
    x, y, w, h = d["box"]
    assert x + w <= zl.SHEET_LEFT and w >= 0.4 and 0 <= y and y + h <= 1
    assert abs((w * 3440) / (h * 1440) - 16 / 9) < 0.02         # her 16:9 picture, not stretched
    x, y, w, h = zl.center_box((1920, 1080))
    assert abs(w - h) < 1e-6 and x + w <= zl.SHEET_LEFT
