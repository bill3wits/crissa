"""Round 36, 2026-10-20 (.101 round 35 FAIL 2 + the tuck): a card with no page body sends her render home, his click on
her tile sends it to its corner until the next card, and her box sits tucked against the sheet. Acceptance walks all
three (beside-card on the wall pack)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_layout as L  # noqa: E402


def test_a_title_only_card_is_not_a_page(monkeypatch):
    monkeypatch.setenv("CRISSA_HOME", "/tmp/r36-not-the-house")
    monkeypatch.delenv("CRISSA_LAYOUT_PAGE_BODY", raising=False)
    monkeypatch.setattr(L, "page_up", L.page_up)
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    assert L.page_up({"up": True, "title": "Genesis 1"}) is False
    assert L.page_up({"up": True, "title": "Genesis 1", "doc_title": "x"}) is False
    assert L.page_up({"up": True, "title": "Genesis 1", "doc": "In the beginning"}) is True
    assert L.page_up({"up": False}) is False


def test_the_kill_and_the_house_keep_any_card(monkeypatch):
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    monkeypatch.setenv("CRISSA_LAYOUT_PAGE_BODY", "0")
    assert L.page_up({"up": True, "title": "t"}) is True
    monkeypatch.delenv("CRISSA_LAYOUT_PAGE_BODY")
    monkeypatch.setattr(crissa_home, "is_house", lambda: True)
    assert L.page_up({"up": True, "title": "t"}) is True


def test_her_box_is_tucked_against_the_sheet():
    b = L.center_box((1920, 1080), tuck=True)
    assert abs(b[0] + b[2] - (L.SHEET_LEFT - L.TUCK_GAP)) < 1e-3
    old = L.center_box((1920, 1080), tuck=False)
    assert old[1] == b[1] and old[2:] == b[2:], "same size, same vertical centre"
    assert abs(old[0] - (L.SHEET_LEFT - L.CENTER_W) / 2.0) < 1e-3


def test_the_tuck_kill(monkeypatch):
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    monkeypatch.setenv("CRISSA_LAYOUT_TUCK", "0")
    assert L.tuck_on() is False


def test_the_player_and_the_wall_are_wired():
    src = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    assert "crissa_layout.decide(crissa_layout.page_up(SCREEN.view()))" in src
    assert '"card_at": SCREEN.view().get("at")' in src
    assert 'if not crissa_layout.page_up(out["screen"]) and crissa_display.on():' in src
    wall = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    assert 'beside["sent_home"] = ("card", card_c)' in wall
    assert 'if beside["sent_home"] == ("card", card_p):' in wall
    acc = (ROOT / "scripts" / "public-loops-accept.py").read_text(encoding="utf-8")
    assert "lines += _beside_round36(disp, wlog, lay, W, H, fails)" in acc
