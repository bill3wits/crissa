"""The Agent Look pop-up (the User 2026-10-31, relayed by house-rails-84: "agent edit v2. quick switch outfit for crissa here
in release 1.0"): one tap on the Wall MENU's places row opens her looks, one tap wears one through the Control Panel's
own pick path (lib/crissa_avatar.wear). Her looks only (the offer rule's agents, built), ON in a pack, never on the house,
CRISSA_SKIN_LOOK_POPUP=0 = the row links to the Control Panel's Choose skin as before."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_avatar as AV  # noqa: E402
import crissa_home  # noqa: E402
import crissa_menu as MENU  # noqa: E402
import crissa_procs  # noqa: E402
import crissa_wall_key as WK  # noqa: E402


@pytest.fixture
def pack(monkeypatch, tmp_path):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    for s in ("CRISSA_SKIN_LOOK_POPUP", "CRISSA_SKIN_MENU", "CRISSA_SKIN_ALL_AGENTS", "CRISSA_AGENT_PHONE"):
        monkeypatch.delenv(s, raising=False)
    monkeypatch.setenv("CRISSA_AGENT_PHONE", "0")
    root, home = tmp_path / "root", tmp_path / "home"
    _lib(root / "display" / "lila-poses", None)                      # the shipped Setup face: never one of her looks
    _lib(root / "libraries" / "crissa-desk-v2", {"agent": "crissa", "name": "Crissa desk v2"})
    _lib(home / "libraries" / "denim", {"agent": "crissa", "flavor": "denim"})
    _lib(home / "libraries" / "teacher", {"agent": "crissa", "flavor": "teacher"}, built=False)
    _lib(home / "libraries" / "bash-hub", {"agent": "bash"})
    monkeypatch.setattr(AV, "ROOT", root)
    monkeypatch.setattr(AV, "SHIPPED", {"lila": "display/lila-poses"})
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "crissa-skin.json").write_text(json.dumps({"offer": {"agents": ["crissa"], "note": "Crissa only"},
                                                                  "changing": {"notice_s": 30}}))
    monkeypatch.setattr(crissa_home, "HOME", home)
    return root, home


def _lib(d: Path, lj: dict | None, built: bool = True) -> None:
    (d / "visemes" / "phoneme-map").mkdir(parents=True)
    (d / "inventory.json").write_text(json.dumps({"clips": {"listen": {"file": "clip-listen.mp4"}}}))
    (d / "visemes" / "phoneme-map" / "map.json").write_text(json.dumps({"entries": [{"tile": "t.png"}] if built else []}))
    if lj is not None:
        (d / "library.json").write_text(json.dumps(lj))


def test_her_looks_only_built_the_worn_one_marked(pack):
    root, home = pack
    AV.choose("crissa-desk-v2", root=root, home=home)
    lk = AV.looks(root=root, home=home)
    keys = [l["key"] for l in lk["looks"]]
    assert sorted(keys) == ["crissa-desk-v2", "denim"], "never the Setup Lila, another agent, or a look not built"
    assert lk["pending"] == 1 and lk["agent"] == "crissa" and lk["worn"] == "crissa-desk-v2"
    assert [l["key"] for l in lk["looks"] if l["on"]] == ["crissa-desk-v2"]
    assert next(l for l in lk["looks"] if l["key"] == "denim")["title"] == "Denim Crissa"


def test_the_switch_and_the_house(pack, monkeypatch):
    assert AV.look_popup_on() is True, "ON in a pack"
    monkeypatch.setenv("CRISSA_SKIN_LOOK_POPUP", "0")
    assert AV.look_popup_on() is False and MENU.look_view(pack[1], "http://p/") == {}
    monkeypatch.delenv("CRISSA_SKIN_LOOK_POPUP")
    monkeypatch.setenv("CRISSA_SKIN_MENU", "0")
    assert AV.look_popup_on() is False, "no Choose skin entry, no pop-up"
    monkeypatch.delenv("CRISSA_SKIN_MENU")
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert AV.look_popup_on() is False, "never on the house"


def test_the_menu_item_and_one_tap(pack, monkeypatch):
    root, home = pack
    AV.choose("crissa-desk-v2", root=root, home=home)
    asked = []
    monkeypatch.setattr(crissa_procs, "request", lambda h, name, act: asked.append((name, act)) or {"ok": True})
    monkeypatch.setattr(crissa_procs, "known", lambda h, name: True)
    monkeypatch.setattr(AV, "libraries", lambda root_=None, home_=None, _f=AV.libraries: _f(root, home))
    monkeypatch.setattr(MENU, "_presence", lambda: None)
    monkeypatch.setattr(MENU, "face_gate", lambda *a, **k: None)
    v = MENU.look_view(home, "http://127.0.0.1:8097/")
    assert v["action"] == "look" and v["label"] == "Agent Look" and v["more"] == "http://127.0.0.1:8097/#skin"
    got = MENU.act("look", {"key": "bash-hub"}, home=home)
    assert got["ok"] is False and not asked, "only a look the pop-up lists"
    got = MENU.act("look", {"key": "crissa-desk-v2"}, home=home)
    assert got["ok"] is True and got.get("same") and not asked, "the one she wears changes nothing"
    got = MENU.act("look", {"key": "denim", "by": "browser wall"}, home=home)
    assert got["ok"] is True and asked == [("player", "restart")], got
    assert json.loads((home / "avatar.json").read_text())["key"] == "denim"
    assert json.loads((home / AV.CHANGING_FILE).read_text())["label"] == "Denim Crissa", "the Wall says Changing to ..."
    assert "look" in MENU.ACTIONS, "it goes through the menu's face rule like every other change"


def test_the_desk_wall_row_and_dialog():
    lk = {"action": "look", "label": "Agent Look", "more": "http://p/#skin", "pending": 1,
          "looks": [{"key": "crissa-desk-v2", "title": "Crissa desk v2", "on": True}, {"key": "denim", "title": "Denim Crissa", "on": False}]}
    rows = WK.place_items({"look": lk, "skin": {"url": "http://p/#skin"}})
    assert ("Agent Look", "look", True) in rows and not any(r[1] == "skin" for r in rows), "the pop-up replaces the link"
    assert ("Agent Look", "skin", True) in WK.place_items({"look": {}, "skin": {"url": "http://p/#skin", "label": "Agent Look"}})
    assert WK.look_lines(lk) == ["> Crissa desk v2", "  Denim Crissa", "1 look is still being built."]
    sent, opened = [], []
    b = WK.look_buttons(lk, lambda body, fail_title="": sent.append(body), opened.append)
    labels = [x[0] for x in b]
    assert labels[0] == "Cancel" and "More in the Control Panel" in labels and "Crissa desk v2  (now)" in labels
    assert dict(b)["Crissa desk v2  (now)"] is None, "the worn look changes nothing"
    dict(b)["Denim Crissa"]()
    assert sent == [{"action": "look", "key": "denim", "by": "the desk Wall"}]
    dict(b)["More in the Control Panel"]()
    assert opened == ["http://p/#skin"]
    assert WK.PLACE_MARKS["look"] == WK.PLACE_MARKS["skin"]


def test_the_browser_wall_draws_it():
    page = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
    assert "if (v.look && v.look.action) box.appendChild(menuItem(" in page and "() => agentLook(box, v.look)" in page
    assert "else if (v.skin && v.skin.url)" in page, "off: the Control Panel's link as before"
    assert 'menuPost({action: "look", key: l.key, by: "browser wall"})' in page


def test_the_panel_and_the_menu_share_one_pick_path():
    src = (ROOT / "scripts" / "crissa-panel-server.py").read_text(encoding="utf-8")
    assert "res = crissa_avatar.wear(key, by, home, procs)" in src
    assert "return crissa_avatar.phone_follow(home, procs, tick)" in src


def test_the_switch_is_ledgered():
    rows = json.loads((ROOT / "config" / "owed-verdicts.json").read_text(encoding="utf-8"))["rows"]
    assert any(r.get("switch") == "CRISSA_SKIN_LOOK_POPUP" for r in rows)
