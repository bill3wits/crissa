"""Round 37, her tile beside a card and at home (.101 round 36 A, the User: "tuck her to the real edge"; "can we fix home
as always home"; a tremble past 4 px set a new home).

    edge    the tuck uses the letter sheet's real left edge (crissa_window.letter_rect for the screen): 0.677 on 3440x1440,
            0.58 on 1920x1080 as before; her picture sits against the box's page side
    home    in a pack nothing writes ft_tile: a drag holds her only until the next card or page-down; a saved ft_tile
            from before is not her home either
    slop    a click is not a drag: the slop is millimetres of glass in a pack (3 mm), never under the old 4 px
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_layout as L  # noqa: E402
import crissa_window as Wn  # noqa: E402


@pytest.fixture
def pack(monkeypatch):
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    for k in ("CRISSA_LAYOUT_SHEET_REAL", "CRISSA_LAYOUT_TUCK", "CRISSA_WALL_HOME_FIXED", "CRISSA_WALL_DRAG_MM",
              "CRISSA_WALL_BESIDE_RIGHT"):
        monkeypatch.delenv(k, raising=False)


def test_the_tuck_meets_the_real_sheet(pack, monkeypatch):
    for screen in ((3440, 1440), (1920, 1080), (2560, 1440)):
        r = Wn.letter_rect(screen)
        assert L.sheet_left(screen) == pytest.approx(r["x"] / screen[0], abs=1 / screen[0])
        x, _y, w, _h = L.center_box(screen, tuck=True)
        assert x + w == pytest.approx(L.sheet_left(screen) - L.TUCK_GAP, abs=1e-3)
    assert L.center_box((1920, 1080), tuck=True) == [0.11, 0.27, 0.46, 0.46], "the 16:9 glass is as round 36"
    monkeypatch.setenv("CRISSA_LAYOUT_SHEET_REAL", "0")
    assert L.sheet_left((3440, 1440)) == L.SHEET_LEFT


def test_her_picture_sits_against_the_page_side():
    import house_wall_ascii_render as R
    box = (211, 292, 883, 497)
    cx, _, w, _ = R.ft_in_box(box, "ft")
    rx, _, w2, _ = R.ft_in_box(box, "ft", right=True)
    assert w == w2 and rx + w2 == box[0] + box[2] and cx == round(box[0] + (box[2] - w) / 2)


def test_the_slop_is_millimetres_of_glass(pack, monkeypatch):
    import house_wall_ascii as G
    assert G.drag_slop_px(3440, 800.0) == 13           # 3 mm at 4.3 px/mm; his tremble was 4 px
    assert G.drag_slop_px(1920, 0.0) == 4, "no size from the screen: the old 4 px"
    assert G.drag_slop_px(1366, 1000.0) == 4, "never under 4 px"
    monkeypatch.setattr(G.crissa_home, "is_house", lambda *a, **k: True)
    assert G.drag_slop_px(3440, 800.0) == 4, "the house keeps 4 px"


def test_home_is_fixed_and_a_drag_holds_only_for_its_card(pack, monkeypatch):
    import house_wall_ascii as G
    assert G.home_fixed_on() is True
    monkeypatch.setenv("CRISSA_WALL_HOME_FIXED", "0")
    assert G.home_fixed_on() is False
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    rel = src.split("    def on_b1_release(ev):", 1)[1].split("        if docdrag[\"on\"]:", 1)[0]
    pack_branch = rel.split("and home_fixed_on():", 1)[1].split("elif tiledrag", 1)[0]
    assert "prefs.save" not in pack_branch and 'tiledrag["held"] = (list(tiledrag["live"]), card_r)' in pack_branch
    place = src.split("    def _place_keys(pref: dict) -> dict:", 1)[1].split("\n    beside = ", 1)[0]
    assert "if held is not None and held[1] != card_p0:" in place       # the next card or page-down drops it
    assert '(held[0] if held is not None else None)}' in place         # a saved ft_tile is not her home in a pack
    assert "DRAG_SLOP = drag_slop_px(W, _screen_mm(root))" in src
