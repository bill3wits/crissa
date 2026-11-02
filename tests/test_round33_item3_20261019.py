"""Round 33 item 3 (.101 10-19, the User: "how do I get her out of close view"): Back to her desk in both Walls' menus,
the player's own /v1/home as the person; open to anyone; a pack only; kill CRISSA_MENU_DESK=0."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_home  # noqa: E402
import crissa_menu as M  # noqa: E402
import crissa_wall_key as K  # noqa: E402


@pytest.fixture
def pack(monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.delenv("CRISSA_MENU_DESK", raising=False)


def test_the_item_is_in_the_view_and_the_key_menu(pack, monkeypatch):
    assert M.desk_view() == {"label": "Back to her desk", "action": "desk"}
    items = K.menu_items({"mic": {"label": "Microphone"}, "desk": M.desk_view()}, False)
    verbs = [x[1] for x in items]
    assert ("Back to her desk", "desk", True) in items and verbs.index("desk") == verbs.index("mic") + 1   # after the mic (2026-10-28 order)
    monkeypatch.setenv("CRISSA_MENU_DESK", "0")
    assert M.desk_view() is None
    assert "desk" not in [x[1] for x in K.menu_items({"mic": {}, "desk": M.desk_view()}, False)]


def test_it_posts_home_as_the_person(pack):
    sent = []
    got = M.to_desk("the desk Wall", post=lambda b: sent.append(b) or {"taken": True})
    assert got["ok"] and sent == [{"by": "user", "via": "the desk Wall"}]
    got = M.to_desk(post=lambda b: {"taken": False, "why": "the guest holds her plate"})
    assert not got["ok"] and "the guest holds her plate" in got["error"]


def test_the_action_asks_no_face_and_is_never_the_houses(pack, monkeypatch):
    monkeypatch.setattr(M, "to_desk", lambda by="": {"ok": True, "by": by})
    monkeypatch.setattr(M, "face_gate", lambda *a, **k: pytest.fail("no face for her desk"))
    assert M.act("desk", {"by": "her window"}) == {"ok": True, "by": "her window"}
    assert K.needs_ask({"security": {"lock": True}}, {"action": "desk"}) == ""
    monkeypatch.setenv("CRISSA_MENU_DESK", "0")
    assert not M.act("desk", {})["ok"]
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    monkeypatch.delenv("CRISSA_MENU_DESK")
    assert not M.act("desk", {})["ok"]


def test_the_browser_wall_draws_it_and_asks_no_face():
    page = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
    assert 'menuPost({action: "desk", by: "her window"})' in page
    assert '["agent_close", "stop", "wall_close", "voice_id", "desk", "face_again"].includes(a)' in page   # round 34 adds face_again
