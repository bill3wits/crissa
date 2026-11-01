"""ONE MENU, ONE MARK (the User 2026-10-28: "bring the popup menus together ... quick click vs scouring control panel ...
agent-only view ... the take with on Mobile QR popup ... change it from Crissa to 'Menu' ... a whitelabel"). Step one
of the two systems' unification: the desk Wall's key and the browser Wall's key say MENU (never the agent's name), open
the same order - the places row (Composer/Store, Control Panel, Krew), the quick keys (Microphone, Back to her desk,
Agent only, Your phone, Security), the people items, Display settings (the Wall's own Advanced panel) - and the dock's
keys stay as shortcuts to the same things, polled faster."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_wall_key as K  # noqa: E402
import crissa_menu as M  # noqa: E402

WALL_PAGE = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
WALL_SRC = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")


def test_the_key_wears_no_name():
    assert K.KEY_TEXT == "◆ MENU"
    assert "MENU</button>" in WALL_PAGE and 'aria-label="Menu"' in WALL_PAGE
    for word in ("CRISSA</button>", 'aria-label="Crissa"'):
        assert word not in WALL_PAGE


def _view(**kw):
    v = {"mic": {"label": "Microphone: off - turn it on", "ear": True}, "connect": {"label": "Connect an agent", "url": "/connect"},
         "panel": {"label": "Control Panel", "url": "http://127.0.0.1:8097/"}, "composer": {"label": "Store", "url": "http://x/store"},
         "voice_id": {"label": "Voice ID again"}, "lip_sync": {"label": "Lip sync"}, "security": {"label": "Security: Off"},
         "help": {"label": "Help"}, "stop": {"label": "All OS Off"}, "agent": {"label": "Close Agent", "action": "agent_close"}}
    v.update(kw)
    return v


def test_the_order_places_first_then_the_quick_keys():
    v = _view(phone={"label": "Your phone: http://192.0.2.9:8226", "url": "http://192.0.2.9:8226"},
              agent_only={"label": "Agent only", "action": "wall_close", "ok": True},
              krew={"label": "Krew", "url": "http://127.0.0.1:8097/"})
    verbs = [vb for _l, vb, _e in K.menu_items(v, False) if vb and vb != "na"]
    assert verbs[:3] == ["composer", "panel", "krew"]
    assert verbs[3:8] == ["mic", "agent_only", "phone", "security", "connect"]
    assert verbs[-2:] == ["agent_close", "stop"]
    assert "advanced" not in verbs
    verbs2 = [vb for _l, vb, _e in K.menu_items(v, False, advanced=True) if vb]
    assert verbs2.index("advanced") == verbs2.index("help") - 1          # Display settings right before Help


def test_the_places_row_is_one_row_and_a_missing_place_is_greyed_not_gone():
    v = _view(unavailable=[{"id": "krew", "label": "krew", "why": "krew is not installed"}], krew={"label": "Krew", "url": ""})
    rows = K.menu_rows(K.menu_items(v, False))
    assert rows[0][0] == "places"
    cells = rows[0][1]
    assert [c[1] for c in cells] == ["composer", "panel", "krew"]
    assert cells[0][2] is True and cells[1][2] is True and cells[2][2] is False
    assert rows[1][0] == "sep" and rows[2][2] == "mic"
    assert K.place_items({})[2] == ("Krew", "krew", False)


def test_agent_only_is_offered_only_while_the_wall_is_up_and_her_face_stays(tmp_path, monkeypatch):
    import crissa_watch
    import crissa_procs
    monkeypatch.setattr(crissa_procs, "known", lambda home, name: True)
    monkeypatch.setenv("CRISSA_FACE_ONLY", "1")                    # Face only by name (this box is the house)
    monkeypatch.setenv("CRISSA_WALL_OFF_FACE", "1")
    got = M.agent_only_view(tmp_path)
    assert got and got["action"] == "wall_close" and got["label"] == "Agent only" and "face stays" in got["ask"]
    crissa_watch.set_closed("wall", True, by="a test", home=tmp_path)
    assert M.agent_only_view(tmp_path) is None                     # closed already: Open the Wall is the item then
    crissa_watch.set_closed("wall", False, home=tmp_path)
    monkeypatch.setenv("CRISSA_WALL_OFF_FACE", "0")
    assert M.agent_only_view(tmp_path) is None                     # plain Close the Wall is not an agent-only view
    monkeypatch.setattr(crissa_procs, "known", lambda home, name: False)
    monkeypatch.setenv("CRISSA_WALL_OFF_FACE", "1")
    assert M.agent_only_view(tmp_path) is None                     # no desk Wall in this install (--no-desk)


def test_krew_place_reads_the_capability():
    assert M.krew_view("http://127.0.0.1:8097/", {"unavailable": []}) == {"label": "Krew", "url": "http://127.0.0.1:8097/", "why": ""}
    got = M.krew_view("http://127.0.0.1:8097/", {"unavailable": [{"id": "krew", "why": "krew is not installed"}]})
    assert got["url"] == "" and got["why"] == "krew is not installed"


def test_the_menus_view_carries_both():
    src = (ROOT / "lib" / "crissa_menu.py").read_text(encoding="utf-8")
    assert '"agent_only": agent_only_view(home)' in src and '"krew": krew_view(panel, cap)' in src


def test_the_browser_wall_mirrors_it():
    for needle in ('id: "m-composer"', 'id: "m-panel"', 'id: "m-krew"', 'places.className = "places"', 'id: "m-agent-only"',
                   'menuPost({action: ao.action, by: "her window (Agent only)"})', 'id: "m-phone"', 'id: "m-security"'):
        assert needle in WALL_PAGE, needle
    body = WALL_PAGE.split("async function menuOpen() {", 1)[1].split("box.hidden = false;", 1)[0]
    order = [body.index(x) for x in ('id: "m-composer"', 'id: "m-mic"', 'id: "m-agent-only"', 'id: "m-phone"', 'id: "m-security"',
                                     'id: "m-connect"', 'id: "m-help"', 'id: "m-agent"', 'id: "m-stop"')]
    assert order == sorted(order)


def test_the_wall_hands_the_key_its_advanced_panel_and_polls_the_dock_faster():
    assert 'advanced=(lambda: wall_keys["dock_press"]("menu", "menu key")) if wall_keys.get("dock_press") else None' in WALL_SRC
    assert 'wall_keys["dock_press"] = dock_press' in WALL_SRC
    assert 'DOCK_POLL_MS = max(20, int(os.environ.get("HOUSE_WALL_DOCK_POLL_MS", "60") or 60))' in WALL_SRC
    assert "root.after(150, _press_poll)" not in WALL_SRC
    src = (ROOT / "lib" / "crissa_wall_key.py").read_text(encoding="utf-8")
    assert 'elif verb == "advanced":' in src and '_S["advanced"] = advanced' in src
    assert 'act({"action": "wall_close", "by": "the desk Wall (Agent only)"}' in src


def test_no_house_word_in_the_shipped_pieces():
    spec = importlib.util.spec_from_file_location("zx_menu", ROOT / "scripts" / "crissa-export.py")
    zx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(zx)
    for rel in ("display/crissa-wall.html", "lib/crissa_wall_key.py", "lib/crissa_menu.py"):
        assert zx.scrub_text((ROOT / rel).read_text(encoding="utf-8")) == {}, rel


# ---- the Advanced rows as menu rows (step two, the User 2026-10-28 "love it. go.") -----------------------------------
PREFS = {"brightness": 1.0, "density": 1.05, "flourish_pct": 35, "work_start": 9, "work_end": 17, "folders": False,
         "leaves_at_end": True}


def test_the_display_rows_carry_their_values_and_sit_before_help():
    items = K.menu_items(_view(), False, advanced=True, display=PREFS)
    labels = {vb: lab for lab, vb, _e in items if vb}
    assert labels["disp:brightness"] == "Brightness: 1.00" and labels["disp:density"] == "Resolution: 1.05"
    assert labels["disp:flourish_pct"] == "Flourish: 35 %" and labels["disp:work_start"] == "Day starts: 9:00"
    assert labels["disp:work_end"] == "Day ends: 17:00"
    assert labels["disp:folders"] == "Desktop folders: hidden" and labels["disp:leaves_at_end"] == "Leaves at day end: on"
    assert labels["advanced"] == "More display settings"                 # ZOS desktop and the rest stay on the panel
    verbs = [vb for _l, vb, _e in items if vb]
    assert verbs.index("disp:brightness") > verbs.index("lip_sync") and verbs.index("disp:leaves_at_end") < verbs.index("advanced") < verbs.index("help")
    assert K.display_items(None) == [] and "disp:" not in " ".join(vb or "" for _l, vb, _e in K.menu_items(_view(), False))


def test_the_rows_are_dials_and_switches_in_the_panel():
    rows = K.menu_rows(K.menu_items(_view(), False, advanced=True, display=PREFS))
    kinds = {r[2]: r[0] for r in rows if r[2]}
    assert kinds["disp:brightness"] == "dial" and kinds["disp:work_end"] == "dial"
    assert kinds["disp:folders"] == "toggle" and kinds["disp:leaves_at_end"] == "toggle"
    assert kinds["advanced"] == "item"


def test_the_wall_shares_one_step_path_with_the_menu():
    assert "    def step_dial(key: str, direction: int) -> dict | None:" in WALL_SRC
    assert 'step_dial(key, -1 if name.endswith("_lo") else 1)' in WALL_SRC          # the panel's click
    assert 'wall_keys["display"] = {"view": _prefs, "step": step_dial, "toggle": toggle_display, "paint": lambda: paint_ui()}' in WALL_SRC
    assert 'display=wall_keys.get("display"),' in WALL_SRC
    assert WALL_SRC.count("prefs.nudge(key,") == 1, "one nudge call: the dial's step has one home"
    src = (ROOT / "lib" / "crissa_wall_key.py").read_text(encoding="utf-8")
    assert 'elif verb.startswith("disp:"):' in src and 'd_["step"](key_, -1 if sign_ == "-" else 1)' in src
    assert 'd_["toggle"](key_)' in src


# ---- the house Wall on the same key (step two, the User 2026-10-28 "Crissa Wall + Krew = house") --------------------
def test_the_house_wall_wears_the_key_only_when_asked(monkeypatch):
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert K.enabled({}) is False                                         # today's house: its rail and dock keys
    assert K.enabled({"HOUSE_WALL_MENU_KEY": "1"}) is True
    assert K.enabled({"HOUSE_WALL_MENU_KEY": "1", "CRISSA_CRISSA_KEY": "0"}) is False
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    assert K.enabled({}) is True                                          # a pack: as before


def test_the_house_menu_is_places_mic_display_help_and_nothing_of_a_kits_agent():
    v = {"house": True, "mic": {"label": "Microphone: on - turn it off", "on": True, "ear": True},
         "composer": {"label": "Composer", "url": "http://composer.home.arpa:8199/"},
         "panel": {"label": "Control Panel", "url": "http://panel.home.arpa:8099/panel"},
         "krew": {"label": "Krew", "url": "http://krew.home.arpa:8099/krew"}}
    verbs = [vb for _l, vb, _e in K.menu_items(v, False, advanced=True, display=PREFS) if vb]
    assert verbs[:4] == ["composer", "panel", "krew", "mic"]
    assert verbs[-2:] == ["advanced", "help"] and "disp:brightness" in verbs
    for kit_only in ("connect", "voice_id", "agent_close", "stop", "agent_only", "phone", "security", "lip_sync"):
        assert kit_only not in verbs, kit_only
    rows = K.menu_rows(K.menu_items(v, False))
    assert rows[0][0] == "places" and all(c[2] for c in rows[0][1])      # the house has all three places


def test_the_house_wall_hands_the_key_its_own_view_and_verbs():
    assert "house=_house_menu_hooks() if crissa_home.is_house() else None," in WALL_SRC
    body = WALL_SRC.split("    def _house_menu_hooks() -> dict:", 1)[1].split("    try:\n        import crissa_wall_key", 1)[0]
    assert 'dock.DOCK_KEYS.get(name)' in body and '(h_.get("voice") or {}).get("voice_on")' in body
    assert '"voice_on" if want_on else "voice_off"' in body and 'return {"view": view, "place": rail_key, "mic": mic}' in body
    src = (ROOT / "lib" / "crissa_wall_key.py").read_text(encoding="utf-8")
    assert 'if h_ and verb in PLACES and callable(h_.get("place")):' in src
    assert 'return dict(house["view"]() or {}, house=True)' in src
