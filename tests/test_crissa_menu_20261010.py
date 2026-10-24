"""The Crissa key's menu (the User 2026-10-10, after .101: the kit "looks great but lacks features to operate it").

One menu, served by the player (lib/crissa_menu, GET/POST /v1/menu), opened by a small key on the desk Wall
(lib/crissa_wall_key) and on the browser Wall (display/crissa-wall.html). Stop Crissa is a request to the launcher
(lib/crissa_procs.request_quit) that stops everything in both modes. No player, no Tk window, no launcher runs here:
the menu's pieces are called with stand-ins, the launcher's loop is read from its source."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_home  # noqa: E402
import crissa_init as zi  # noqa: E402
import crissa_menu as M  # noqa: E402
import crissa_procs as P  # noqa: E402
import crissa_wall_key as K  # noqa: E402

WALL_PAGE = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "scripts" / "crissa.py").read_text(encoding="utf-8")


@pytest.fixture
def pack(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(crissa_home, "HOME", home)
    monkeypatch.setattr(M.crissa_home, "HOME", home)
    zi.configure(home=home)
    monkeypatch.setattr(M, "_presence", lambda: {"_err": "no presence here"})
    yield home
    zi.configure()


def _beat(home, state="running"):
    P._write(P.table_path(home), {"launcher": {"pid": 1, "beat": time.time(), "state": state}, "children": {}})


def test_the_menu_shows_every_item_both_walls_need(pack):
    _beat(pack)
    v = M.view(pack, ear_up=lambda: True, presence={})
    assert v["ok"] and v["route"] == "/v1/menu"
    assert v["mic"] == {"on": False, "ear": True, "label": M.LABELS["mic_off"]}
    assert v["connect"]["url"].endswith("/connect")
    assert v["panel"]["url"] and v["composer"]["url"]
    assert v["voice_id"]["ok"] is True
    assert any("Ctrl+Q" in ln and "F11" in ln for ln in v["help"]["lines"])
    assert any("background" in ln for ln in v["help"]["lines"])
    assert any("Start her again" in ln for ln in v["help"]["lines"])
    assert v["stop"]["ok"] is True and v["stop"]["ask"].startswith("All OS Off?")   # was Stop Crissa (2026-10-12)
    # EE (round 4): the label follows the desk Wall's own state - no note, or the wallpaper: nothing to exit
    assert v["fullscreen"] == {"on": False, "label": "", "mode": ""}
    M.note_desk_screen("fullscreen", True, home=pack)
    assert M.view(pack, ear_up=lambda: True, presence={})["fullscreen"] == {"on": True, "label": "Exit full screen",
                                                                            "mode": "fullscreen"}
    # her microphone: the ear's own lock, read the way the ear reads it
    zi._write_json(zi.voice_lock_path(), {"voice": "on"})
    assert M.view(pack, ear_up=lambda: True, presence={})["mic"]["label"] == M.LABELS["mic_on"]
    assert M.view(pack, ear_up=lambda: False, presence={})["mic"]["ear"] is False


def test_help_says_how_to_stop_and_start_for_the_way_she_runs():
    hand = M.help_lines("hand", Path("/opt/crissa"))
    assert any("Ctrl-C" in ln for ln in hand) and any("python3 scripts/crissa.py in /opt/crissa" in ln for ln in hand)
    svc = M.help_lines("systemd")
    assert any("systemctl --user stop crissa-launcher" in ln for ln in svc)
    assert any("systemctl --user start crissa-launcher" in ln for ln in svc)


def test_the_mic_item_turns_her_microphone_through_the_ears_lock(pack, monkeypatch):
    calls = []
    monkeypatch.setattr(zi, "mic", lambda on: calls.append(on) or {"ok": True, "mic": {"on": on}})
    got = M.act("mic", {"on": True}, home=pack)
    assert got["ok"] and calls == [True] and "menu" in got


def test_stop_asks_first_then_asks_the_launcher_to_stop_everything(pack):
    _beat(pack)
    assert M.act("stop", {}, home=pack)["ask"] is True
    assert not list((P.req_dir(pack)).glob("*.json")) if P.req_dir(pack).is_dir() else True
    got = M.act("stop", {"confirm": True}, home=pack)
    assert got["ok"] and got["job"]
    reqs = P.take_requests(pack)
    assert reqs == [{"name": "crissa", "action": "quit", "job": got["job"], "by": "menu"}]


def test_stop_with_no_launcher_says_so(pack):
    _beat(pack, state="stopped")
    got = M.act("stop", {"confirm": True}, home=pack)
    assert got["ok"] is False and "stopped" in got["error"]


def test_the_launcher_stops_everything_on_the_quit_request_and_exits_0():
    """The loop takes the request before any other, stops the group as on Ctrl-C (stopping["why"]), rc stays 0."""
    i = LAUNCHER.index("if req[\"action\"] == crissa_procs.QUIT:")
    block = LAUNCHER[i:i + 700]
    assert 'stopping["why"] = "the Crissa menu"' in block and "crissa_procs.finish(" in block and "break" in block
    assert "rc = 1" not in block
    assert P.take_requests is not None and P.QUIT == "quit" and P.QUIT_NAME == "crissa"


def test_a_quit_request_is_only_the_launchers_own_name(tmp_path):
    P._write(P.req_dir(tmp_path) / "player.json", {"name": "player", "action": "quit", "job": "j1"})
    P._write(P.req_dir(tmp_path) / "crissa.json", {"name": "crissa", "action": "restart", "job": "j2"})
    got = P.take_requests(tmp_path)
    assert got == [] or all(r["action"] != "quit" for r in got)


def test_voice_id_again_waits_for_the_user_to_be_seen_once_one_is_enrolled(pack, monkeypatch):
    assert M.voice_id_gate({})["ok"] is True                    # no User yet: Register may run (first boot's rule)
    zi._write_json(zi.user_path(), {"schema": zi.USER_SCHEMA, "name": "Sam", "person_id": "sam", "role": "user"})
    # Y (2026-10-10): with no voice print yet there is nothing to protect, and Voice ID again stays open
    assert M.voice_id_gate({"user_auth": False})["ok"] is True
    vdir = zi.identity_root() / "people" / "sam" / "voice"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "ws_1.json").write_text('{"encoding": [%s]}' % ", ".join(["0.1"] * 256))
    assert M.voice_id_gate({"user_auth": False})["ok"] is False
    assert "camera" in M.voice_id_gate({"user_auth": False})["why"]
    assert M.voice_id_gate({"user_auth": True})["ok"] is True
    monkeypatch.setattr(M, "voice_id_gate", lambda presence=None: {"ok": False, "why": "not now"})
    assert M.act("voice_id", {}, home=pack) == {"ok": False, "error": "not now", "password": False}


def test_never_on_the_house(monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert M.act("mic", {"on": True})["ok"] is False
    assert "if head == crissa_menu.ROUTE and not crissa_home.is_house():" in CONDUCTOR


def test_the_player_serves_the_menu_and_only_its_own_pages_may_post():
    assert CONDUCTOR.count("if head == crissa_menu.ROUTE and not crissa_home.is_house():") == 2
    i = CONDUCTOR.index("the Crissa key's menu: her microphone, Voice ID again, Stop Crissa")
    assert "connect_origin_ok(self.headers)" in CONDUCTOR[i:i + 300]
    assert "import crissa_menu" in CONDUCTOR


# ---- the desk Wall's key (Tk): the pure parts ---------------------------------------------------------------------
def test_the_keys_menu_lists_every_item_and_exit_full_screen_only_in_full_screen(pack):
    _beat(pack)
    v = M.view(pack, ear_up=lambda: True, presence={})
    verbs = [vb for _l, vb, _on in K.menu_items(v, fullscreen=False) if vb and vb != "na"]
    # 2026-10-12 (the User, "Close Agent vs OS off"): Close Agent replaced Close the Wall; Stop Crissa is All OS Off
    # 2026-10-13 (round 17, the User: "Maybe then On, ID-only, Off"): Security after Lip sync
    # 2026-10-16 (round 25, Face only): after Lip sync, where Face only is on (a pack)
    face = ["face"] if (v.get("face") or {}).get("action") else []
    # 2026-10-19 (round 33, Back to her desk): after the microphone, where it is on (a pack)
    desk = ["desk"] if (v.get("desk") or {}).get("action") else []
    assert verbs == ["mic", *desk, "connect", "panel", "composer", "voice_id", "lip_sync", *face, "security", "help",
                     "agent_close", "stop"]
    verbs = [vb for _l, vb, _on in K.menu_items(v, fullscreen=True) if vb and vb != "na"]
    assert verbs == ["mic", *desk, "connect", "panel", "composer", "voice_id", "lip_sync", *face, "security", "help",
                     "fullscreen", "agent_close", "stop"]
    labels = {vb: lab for lab, vb, _on in K.menu_items(v, fullscreen=True) if vb}
    assert labels["mic"] == M.LABELS["mic_off"] and labels["stop"] == "All OS Off"
    assert labels["agent_close"] == "Close Agent"


def test_the_key_sits_inside_the_work_area_right_of_the_walls_left_column():
    class R:
        stdout = "_NET_WORKAREA(CARDINAL) = 70, 32, 3370, 1408, 70, 32, 3370, 1408"
    wa = K.workarea(3440, 1440, run=lambda *a, **k: R)
    assert wa == (70, 32, 3370, 1408)
    x, y = K.key_place(wa, 110, 30, 3440)
    assert x >= 70 + K.MARGIN and x >= round(K.HUD_COLUMN_U * 3440 / 88) and y == 32 + 1408 - K.MARGIN - 30
    assert K.workarea(1920, 1080, run=lambda *a, **k: (_ for _ in ()).throw(OSError())) == (0, 0, 1920, 1080)


def test_the_key_is_off_on_the_house_and_by_its_switch(monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert K.enabled({}) is False
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    assert K.enabled({}) is True and K.enabled({"CRISSA_CRISSA_KEY": "0"}) is False


def test_the_wall_puts_the_key_in_its_input_shape():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    assert 'boxes.append(crissa_key["box"]())' in src
    assert "crissa_wall_key.install(root, feed=FEED, open_url=open_url" in src


# ---- the browser Wall's key -------------------------------------------------------------------------------------
def test_the_browser_wall_carries_the_key_and_the_same_menu():
    assert 'id="ckey"' in WALL_PAGE and 'id="cmenu"' in WALL_PAGE and "CRISSA</button>" in WALL_PAGE
    # Voice ID again goes to its one page (voice-id-101, 2026-10-11): /init?voice_id=1 with the name and the password
    for needle in ('fetch("/v1/menu"', 'menuPost({action: "mic"', 'location.href = VOICE_ID_PAGE;',
                   'menuPost({action: "stop", confirm: true})', "menuConfirm(box, \"All OS Off\", st.ask",
                   "document.exitFullscreen()",
                   "document.fullscreenElement", 'const VOICE_ID_PAGE = "/init?voice_id=1";', 'id: "m-help"',
                   'id: "m-panel"',
                   'id: "m-composer"', 'id: "m-connect"'):
        assert needle in WALL_PAGE, needle
    assert ".ckey { position: fixed;" in WALL_PAGE
