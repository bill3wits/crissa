"""Her picture drags with no page up, in a pack (the User 2026-10-16: "if clicked AND in normal mode we should make
the render window draggable - just like when in display mode"). The house keeps its rule: a drag only under a page."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import doc_placement as dp  # noqa: E402
import crissa_home  # noqa: E402

UP = {"screen": {"doc": "How cron works"}}
DOWN = {"screen": {"doc": None}}
PACK = {"CRISSA_DRAG_HER": ""}


def _files(tmp_path, home=None, place=None):
    s, p, h = tmp_path / "shift.json", tmp_path / "place.json", tmp_path / "home.json"
    if home is not None:
        h.write_text(json.dumps(home))
    if place is not None:
        p.write_text(json.dumps(place))
    return {"shift_file": s, "place_file": p, "home_file": h}


def test_on_in_a_pack_off_on_the_house_and_the_switch_decides(monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    assert dp.free_drag_on({}) is True
    assert dp.free_drag_on({"CRISSA_DRAG_HER": "0"}) is False
    monkeypatch.setattr(crissa_home, "is_house", lambda: True)
    assert dp.free_drag_on({}) is False, "the house keeps its rule"
    assert dp.free_drag_on({"CRISSA_DRAG_HER": "1"}) is True


def test_no_page_up_she_sits_where_dragged_in_a_pack(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    f = _files(tmp_path, home={"cx": 0.1, "cy": 0.05})
    assert dp.origin(DOWN, env=PACK, **f) == (0.1, 0.05)
    assert dp.home_placed(DOWN, home_file=f["home_file"], env=PACK) is True


def test_never_dragged_nothing_moves(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    f = _files(tmp_path)
    assert dp.origin(DOWN, env=PACK, **f) is None
    assert dp.home_placed(DOWN, home_file=f["home_file"], env=PACK) is False


def test_the_house_ignores_a_home_spot(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda: True)
    f = _files(tmp_path, home={"cx": 0.1, "cy": 0.05})
    assert dp.origin(DOWN, env={}, **f) is None
    assert dp.home_placed(DOWN, home_file=f["home_file"], env={}) is False


def test_a_page_up_keeps_its_own_spot(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    f = _files(tmp_path, home={"cx": 0.1, "cy": 0.05}, place={"cx": 0.25, "cy": 0.0})
    assert dp.origin(UP, env=PACK, **f) == (0.25, 0.0)
    assert dp.home_placed(UP, home_file=f["home_file"], env=PACK) is False
    (tmp_path / "place.json").unlink()
    assert dp.origin(UP, env=PACK, **f) is None, "under a page and never dragged there, she does not move"


def test_the_drag_kill_also_stops_the_free_drag(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    monkeypatch.setenv("HOUSE_DOC_PLACE_DRAG", "0")
    f = _files(tmp_path, home={"cx": 0.1, "cy": 0.05})
    assert dp.origin(DOWN, env=PACK, **f) is None


def test_forget_puts_her_back(tmp_path):
    h = tmp_path / "home.json"
    dp.save(0.1, 0.05, path=h)
    assert dp.forget_home_place(h) is True
    assert not h.exists()
    assert dp.forget_home_place(h) is False


def test_the_wall_arms_the_drag_saves_apart_and_double_click_resets():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert "if doc_placement.free_drag_on():" in src
    assert "home=True)" in src and "path=doc_placement.home_place_path()" in src
    assert 'cv.bind("<Double-Button-1>", on_double)' in src
    assert "doc_placement.home_placed(house_f)" in src
