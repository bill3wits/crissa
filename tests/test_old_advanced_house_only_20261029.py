"""The old Advanced panel is the house's (the User 2026-10-29: "i think we can delete more display settings if all have
been transferred into popup menu"): in a pack the MENU carries every row a kit has; the panel's last row, ZOS desktop,
hands the glass to the house's Brave and stopped a kit's own Wall."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import house_wall_dock as dock  # noqa: E402
import crissa_wall_key as K  # noqa: E402

PREFS = {"brightness": 1.0, "density": 2.0, "flourish_pct": 35, "work_start": 9, "work_end": 17, "folders": True,
         "leaves_at_end": False}


def _pack(monkeypatch, house=False):
    monkeypatch.setattr(dock.crissa_home, "is_house", lambda *a, **k: house)
    monkeypatch.delenv("CRISSA_OLD_ADVANCED", raising=False)


def test_every_row_a_kit_has_is_in_the_menu():
    verbs = {v for _l, v, _on in K.display_items(PREFS)}
    assert verbs == {"disp:" + k for k in ("brightness", "density", "flourish_pct", "work_start", "work_end", "folders",
                                           "leaves_at_end")}
    panel = (ROOT / "lib" / "house_wall_ascii_render.py").read_text(encoding="utf-8")
    for label in ("Brightness", "Resolution", "Desktop folders", "Flourish %", "Leaves at day end", "Wall desktop"):
        assert 'row_label(ry, "%s")' % label in panel, label          # the panel's rows: ZOS desktop is the only extra (was "ZOS desktop")


def test_a_pack_has_no_old_panel_and_the_house_keeps_it(monkeypatch):
    _pack(monkeypatch)
    assert dock.advanced_on() is False
    rows = [v for _l, v, _on in K.menu_items({}, False, advanced=True, display=PREFS)]
    assert "advanced" not in rows and "disp:brightness" in rows
    assert "advanced" in [v for _l, v, _on in K.menu_items({}, False, advanced=True, display=None)]   # no rows: the way in
    monkeypatch.setenv("CRISSA_OLD_ADVANCED", "1")
    assert dock.advanced_on() is True
    _pack(monkeypatch, house=True)
    assert dock.advanced_on() is True
    assert "advanced" in [v for _l, v, _on in K.house_items({}, False, advanced=True, display=PREFS)]


def test_the_rail_hover_and_click_ask_the_same_gate():
    r = (ROOT / "lib" / "house_wall_ascii_render.py").read_text(encoding="utf-8")
    w = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    assert 'keys = [k for k in keys if k[0] != "menu" or _dk.advanced_on()]' in r
    assert w.count("dock.advanced_on()") == 2
