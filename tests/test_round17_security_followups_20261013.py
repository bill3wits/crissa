"""Round 17 follow-ups to Security (the User 2026-10-13), stand-ins only: no camera, no microphone, no presence.

1. ONE TOGGLE: Secure and Double Secure are positions of the Security toggle above On, only on a camera that turns
   (lib/crissa_home); security_mode() still answers on | id_only | off; a saved Secure on a camera that does not turn
   reads On with a note. Kill CRISSA_SECURITY_ONE_TOGGLE=0.
2. A STRANGER IS LOCKED OUT OF ADJUSTING: raising needs no face; while On or above every change but a stop needs his
   face (one face-match sequence, lib/crissa_home.face_sequence) - the Crissa menu, the Control Panel, the feed, Setup,
   Connect; ID-only's before-action check runs the same sequence. Kills CRISSA_SECURITY_LOCK_ADJUST=0,
   CRISSA_SECURITY_LOWER_NEEDS_FACE=0.
3. OFF IDLES THE FACE LOOK: the oracle takes no frame in Off and resumes on the next tick after. Kill
   CRISSA_SECURITY_OFF_IDLES_FACES=0.
Presence's side (the sync, a step down out of Secure, the face check op, the visor's idle): tests/presence/
test_round17_security_followups_presence_20261013.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ["CRISSA_HOME"] = tempfile.mkdtemp(prefix="crissa-secfu-")    # never this machine's own state home
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "modules" / "ear"))
sys.path.insert(0, str(ROOT / "modules" / "oracle"))

import crissa_home as H  # noqa: E402
import crissa_menu as M  # noqa: E402
from crissa_bridge import house_commands, id_gate, user_mode  # noqa: E402

UID = "user"
KILLS = ("CRISSA_SECURITY_ONE_TOGGLE", "CRISSA_SECURITY_LOCK_ADJUST", "CRISSA_SECURITY_LOWER_NEEDS_FACE",
         "CRISSA_SECURITY_DEFAULT_IDONLY", "CRISSA_SECURITY_OFF_IDLES_FACES", "CRISSA_FACE_SEQ_WAIT_S",
         "HOUSE_LIVE_FACE_AGE_S", "HOUSE_FIXED_FACE_AGE_S", "HOUSE_LIVE_FACE_MIN_SIM", "HOUSE_LIVE_PROFILE_MIN_SIM")


@pytest.fixture()
def kit(tmp_path, monkeypatch):
    """A pack's state home in tmp_path with a User; kit(mode) saves a position."""
    monkeypatch.setattr(H, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(H, "HOME", tmp_path)
    for k in KILLS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOUSE_USER_ID", UID)
    monkeypatch.setattr(user_mode, "HOUSE_MODE_PATH", tmp_path / "house-mode.json")
    user_mode._cache.update(ts=0.0, mode=None)
    (tmp_path / "user.json").write_text(json.dumps({"person_id": UID, "name": "Tester"}))
    H._SEC_CACHE.clear()
    id_gate.grace_end("test")                     # round 20: no face grace or turn words left by an earlier test
    id_gate.turn_text("")

    def set_(mode):
        if mode is not None:
            (tmp_path / "security.json").write_text(json.dumps({"mode": mode}))
        H._SEC_CACHE.clear()
        return tmp_path
    return set_


def _face(age=0.4, name=UID, sim=0.74, fixed=False, offer=None):
    f = {"locked": name, "faces": [{"name": name, "sim": sim}], "n_faces": 1, "source": "visor", "age_s": age}
    if fixed:
        f["fixed"] = True
    p = {"user_id": UID, "face": f}
    if offer:
        p["visit_lamp_info"] = {"secure_offer": offer}
    return p


TURNS = {"ok": True, "kind": "turns", "camera": "turns"}
FIXED = {"ok": False, "kind": "fixed", "camera": "fixed", "hide": True}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


# ---- 1: one toggle ---------------------------------------------------------------------------------------------------

def test_1_positions_follow_the_camera_tree(kit):
    assert H.security_positions("turning") == ("double_secure", "secure", "on", "id_only", "off")
    assert H.security_positions("fixed") == H.security_positions("none") == H.security_positions("") == \
        ("on", "id_only", "off")
    assert H.camera_kind_of({"visit_lamp_info": {"secure_offer": TURNS}}) == "turning"
    assert H.camera_kind_of({"presence": {"visit_lamp_info": {"secure_offer": FIXED}}}) == "fixed"
    assert H.camera_kind_of({"visit_lamp_info": {"secure_offer": {"ok": False, "kind": "none"}}}) == "none"
    assert H.camera_kind_of(_face(fixed=True)) == "fixed" and H.camera_kind_of({}) == ""


def test_1_secure_is_on_for_every_gate_and_reads_on_where_the_camera_does_not_turn(kit):
    kit("secure")
    assert H.security_mode() == "on"                                     # every round-17 gate: On
    assert H.security_position(camera="turning") == "secure"
    assert H.security_position(camera="") == "secure"                    # not known yet: as saved
    assert H.security_position(camera="fixed") == "on"
    assert H.security_note(camera="fixed") == H.SECURITY_FIXED_NOTE
    kit("double_secure")
    assert H.security_mode() == "on" and H.security_position(camera="turning") == "double_secure"


def test_1_saving_a_position_and_the_kill(kit, monkeypatch):
    assert H.set_security_mode("double-secure", by="t")["ok"] and H.security_position(camera="turning") == "double_secure"
    assert H.set_security_mode("off", note="why")["ok"] and H.security_record()["note"] == "why"
    monkeypatch.setenv("CRISSA_SECURITY_ONE_TOGGLE", "0")
    assert not H.set_security_mode("secure")["ok"]                       # round 17: three positions
    kit("secure")
    assert H.security_position(camera="turning") == "on" and H.security_positions("turning") == ("on", "id_only", "off")
    monkeypatch.delenv("CRISSA_SECURITY_ONE_TOGGLE")
    monkeypatch.setattr(H, "is_house", lambda manifest=None: True)
    assert H.security_position() == "on" and not H.one_toggle() and not H.set_security_mode("off")["ok"]


# ---- 2: the lock -----------------------------------------------------------------------------------------------------

def test_2_raising_is_open_lowering_from_on_needs_his_face_and_a_stop_never(kit, monkeypatch):
    need = H.adjust_needs_face
    for cur in ("off", "id_only"):
        assert need("security", current=cur, target="on") == "" and need("lip_sync", current=cur) == ""
    assert need("security", current="id_only", target="off") == ""      # not from On: open
    assert need("security", current="on", target="secure") == ""        # (a) any step up
    assert need("security", current="secure", target="double_secure") == ""
    assert need("security", current="on", target="id_only") == "lower"
    assert need("security", current="double_secure", target="secure") == "lower"
    assert need("lip_sync", current="on") == need("mic", current="secure") == "lock"
    for stop in H.OPEN_TO_ANYONE:
        assert need(stop, current="double_secure") == ""
    assert set(H.OPEN_TO_ANYONE) >= {"agent_close", "stop", "mic_off", "pause", "cancel"}
    monkeypatch.setenv("CRISSA_SECURITY_LOCK_ADJUST", "0")
    assert need("lip_sync", current="on") == "" and need("security", current="on", target="off") == "lower"
    monkeypatch.setenv("CRISSA_SECURITY_LOWER_NEEDS_FACE", "0")
    assert need("security", current="on", target="off") == ""
    monkeypatch.setattr(H, "is_house", lambda manifest=None: True)
    monkeypatch.delenv("CRISSA_SECURITY_LOCK_ADJUST")
    assert need("lip_sync", current="on") == ""


PARITY = [
    _face(), _face(age=3.0), _face(age=5.0, fixed=True), _face(age=7.0, fixed=True), _face(sim=0.5),
    _face(name="someone"), {"user_id": UID, "face": {}}, {},
    {"user_id": UID, "f2f": True, "face": {"faces": [{"name": UID, "sim": 0.45}], "age_s": 0.5, "source": "visor"}},
    {"user_id": UID, "face": {"faces": [{"name": UID, "sim": 0.8}], "age_s": 0.5, "pending_look": True}},
    {"user_id": UID, "face": {"faces": [{"name": UID, "sim": 0.8}], "age_s": 0.5, "pending_look": True,
                               "fixed": True}},
]


@pytest.mark.parametrize("p", PARITY)
def test_2_the_live_face_rule_is_the_ears(kit, p):
    ok, sim, _why = H.live_face(p, UID)
    live, sim2, fresh = user_mode._live_face_user(p, UID)
    assert ok == bool(live and fresh) and sim == sim2


def test_2_the_sequence_goes_at_once_with_his_face_and_asks_once_without(kit):
    asked, clk = [], Clock()
    got = H.face_sequence(lambda: pytest.fail("no read"), uid=UID, presence=_face(), ask=asked.append)
    assert got["ok"] and not got["asked"] and asked == [] and got["line"] == ""
    reads = iter([_face(age=9.0), _face(age=9.0), _face()])
    got = H.face_sequence(lambda: next(reads), uid=UID, ask=asked.append, sleep=clk.sleep, clock=clk)
    assert got["ok"] and asked == [H.FACE_SEQ_ASK] and got["waited_s"] == pytest.approx(0.8)
    asked.clear()
    got = H.face_sequence(lambda: _face(name="someone"), uid=UID, ask=asked.append, sleep=clk.sleep, clock=clk)
    assert not got["ok"] and asked == [H.FACE_SEQ_ASK] and got["line"] == H.FACE_SEQ_FAIL
    assert 5.0 <= got["waited_s"] <= 5.4                                  # the guided wait (CRISSA_FACE_SEQ_WAIT_S)
    got = H.face_sequence(lambda: {"_err": "down"}, uid=UID, sleep=clk.sleep, clock=clk, wait_s=1.0)
    assert not got["ok"] and got["line"] == H.FACE_SEQ_NO_CAMERA


def test_2_a_camera_that_turns_looks_instead_of_waiting(kit):
    first = _face(age=9.0, offer=TURNS)
    looked = []
    got = H.face_sequence(lambda: pytest.fail("no poll"), uid=UID, presence=first,
                          look=lambda: looked.append(1) or {"ok": True, "checked": True, "found": True, "sim": 0.8})
    assert got["ok"] and got["looked"] and looked == [1]
    got = H.face_sequence(None, uid=UID, presence=first, look=lambda: {"ok": True, "checked": True, "found": False})
    assert not got["ok"] and got["line"] == H.FACE_SEQ_FAIL


def _menu(monkeypatch, presence, posts=None):
    """The menu with presence stood in: reads give `presence` (a list: one per read), posts are recorded."""
    seq = list(presence) if isinstance(presence, list) else None
    reads = []

    def read():
        reads.append(1)
        if seq is not None:
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return presence
    monkeypatch.setattr(M, "_presence", read)
    monkeypatch.setattr(M, "_face_look", lambda: {"ok": True, "checked": True, "found": False})
    rec = posts if posts is not None else []

    def post(path, body, timeout=8.0):
        rec.append((path, dict(body)))
        return {"ok": True, "checked": True}
    monkeypatch.setattr(M, "_presence_post", post)
    monkeypatch.setenv("CRISSA_FACE_SEQ_WAIT_S", "0")
    return reads


def test_2_the_one_gate_every_route_calls(kit, monkeypatch):
    home = kit("on")
    monkeypatch.setenv("CRISSA_FACE_SEQ_WAIT_S", "0")
    got = H.face_gate("feed_voice_on", fetch=lambda: {"user_id": UID, "face": {}})
    assert got["face"] == "refused" and got["ask"] == H.FACE_SEQ_ASK and got["error"] == H.FACE_SEQ_FAIL
    assert H.face_gate("feed_voice_on", fetch=lambda: _face()) is None
    assert H.face_gate("mic_off", fetch=lambda: pytest.fail("a stop never reads presence")) is None
    assert H.face_gate("feed_voice_on", fetch=lambda: {}, owner="") is None      # no User yet: nothing to protect
    st = H.security_state(home, {"visit_lamp_info": {"secure_offer": TURNS}})
    assert st["label"] == "Security: On\u2026" and st["lock"] and [m["password"] for m in st["modes"]][0] is True


def test_2_the_menu_locks_a_change_in_on_and_lets_a_stop_through(kit, monkeypatch):
    home = kit("on")
    reads = _menu(monkeypatch, {"user_id": UID, "face": {}})
    got = M.act("lip_sync", {"step": 1}, home)
    assert got["ok"] is False and got["face"] == "refused" and got["error"] == H.FACE_SEQ_FAIL
    assert got["ask"] == H.FACE_SEQ_ASK and not (home / "lip-sync.json").exists()
    got = M.act("security", {"mode": "off"}, home)
    assert got["face"] == "refused" and H.security_mode() == "on" and got["security"]["mode"] == "on"
    reads.clear()
    monkeypatch.setattr(M, "agent_close", lambda home=None, by="": {"ok": True, "closed": True})
    monkeypatch.setattr(M, "stop", lambda home=None: {"ok": True})
    assert M.act("agent_close", {}, home)["ok"] and M.act("stop", {"confirm": True}, home)["ok"]
    assert reads == []                                                    # a stop never asks presence
    _menu(monkeypatch, _face())
    assert M.act("security", {"mode": "id_only"}, home)["ok"] and H.security_mode() == "id_only"


def test_2_raising_never_asks_and_id_only_is_open(kit, monkeypatch):
    home = kit("off")
    _menu(monkeypatch, {"user_id": UID, "face": {}})
    assert M.act("security", {"mode": "id_only"}, home)["ok"]
    assert M.act("lip_sync", {"step": 1}, home).get("face") is None      # ID-only: settings are open
    assert M.act("security", {"mode": "on"}, home)["ok"] and H.security_mode() == "on"
    assert M.act("lip_sync", {"step": 1}, home)["face"] == "refused"


def test_2_open_agent_is_the_way_back_and_no_user_means_nothing_to_protect(kit, monkeypatch):
    home = kit("on")
    _menu(monkeypatch, {"_err": "down"})                                 # Close Agent took presence down with her
    monkeypatch.setattr(M, "agent_state", lambda home=None: {"closed": True})
    monkeypatch.setattr(M, "agent_open", lambda home=None, by="": {"ok": True, "closed": False})
    assert M.act("agent_open", {}, home)["ok"]
    (home / "user.json").unlink()
    assert M.face_gate("lip_sync", home=home) is None


def test_2_the_kills(kit, monkeypatch):
    home = kit("on")
    _menu(monkeypatch, {"user_id": UID, "face": {}})
    monkeypatch.setenv("CRISSA_SECURITY_LOCK_ADJUST", "0")
    assert M.act("lip_sync", {"step": 1}, home).get("face") is None
    assert M.act("security", {"mode": "off"}, home)["face"] == "refused"
    monkeypatch.setenv("CRISSA_SECURITY_LOWER_NEEDS_FACE", "0")
    assert M.act("security", {"mode": "off"}, home)["ok"]


def test_2_secure_runs_through_presence_on_a_camera_that_turns(kit, monkeypatch):
    home = kit("on")
    posts = []
    lamp = {"visit_lamp": "amber", "visit_lamp_info": {"secure_offer": TURNS}}
    _menu(monkeypatch, dict(_face(), **lamp), posts)
    view = M.security_view(home, dict(_face(), **lamp))
    assert [m["mode"] for m in view["modes"]] == ["double_secure", "secure", "on", "id_only", "off"]
    assert view["lock"] and view["ask"] == H.FACE_SEQ_ASK and view["camera"] == "turning"
    assert [m["face"] for m in view["modes"]] == [False, False, False, True, True]

    def post(path, body, timeout=8.0):
        posts.append((path, dict(body)))
        if body["op"] == "click":
            return {"ok": True, "checked": True, "visit_lamp": "sage"}
        if body["op"] == "hold":
            return {"ok": True, "held": True, "visit_lamp": "sage"}
        return {"ok": True}
    monkeypatch.setattr(M, "_presence_post", post)
    got = M.act("security", {"mode": "secure"}, home)                     # (a): no face asked, Secure's own look
    assert got["ok"] and posts[-1] == ("/v1/visit_lamp", {"op": "click"}) and H.security_position(camera="turning") == "secure"
    got = M.act("security", {"mode": "double_secure"}, home)
    assert got["ok"] is False and got["password"] and got["error"] == M.DOUBLE_NEEDS_PW
    got = M.act("security", {"mode": "double_secure", "password": "pw"}, home)
    assert got["ok"] and posts[-1][1]["op"] == "hold" and H.security_position(camera="turning") == "double_secure"
    # out of Secure: presence checks his face itself (its route), and its refusal is the menu's
    sage = dict({"user_id": UID, "face": {}}, visit_lamp="sage",
                visit_lamp_info={"secure_offer": TURNS, "hold": True})
    _menu(monkeypatch, sage)
    monkeypatch.setattr(M, "_presence_post", lambda path, body, timeout=8.0: {
        "ok": False, "checked": False, "face": "refused", "error": H.FACE_SEQ_FAIL, "visit_lamp": "sage"})
    got = M.act("security", {"mode": "off"}, home)
    assert got["ok"] is False and got["face"] == "refused" and H.security_position(camera="turning") == "double_secure"


def test_2_a_fixed_camera_has_no_secure(kit, monkeypatch):
    home = kit("on")
    pres = dict(_face(fixed=True), visit_lamp="amber", visit_lamp_info={"secure_offer": FIXED})
    _menu(monkeypatch, pres)
    assert [m["mode"] for m in M.security_view(home, pres)["modes"]] == ["on", "id_only", "off"]
    got = M.act("security", {"mode": "secure"}, home)
    assert got["ok"] is False and got["error"] == M.SECURE_NOT_HERE
    kit("secure")
    v = M.security_view(home, pres)
    assert v["mode"] == "on" and v["note"] == H.SECURITY_FIXED_NOTE


def test_2_setup_connect_and_the_kits_panel_and_feed_ask_the_same_gate(kit, monkeypatch):
    import crissa_init
    seen = []
    monkeypatch.setattr(M, "face_gate", lambda name, **kw: seen.append(name) or {"ok": False, "face": "refused",
                                                                                   "error": H.FACE_SEQ_FAIL})
    got = crissa_init.apply("restart", {})
    assert got["face"] == "refused" and seen == ["setup_restart"]
    assert crissa_init.apply("mic", {"on": False}).get("face") is None   # the mic off: open
    assert crissa_init.action({"action": "kiosk_init_user"})["face"] == "refused"
    src = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    for name in ("connect", "voice_install", "mouths"):
        assert 'refused = crissa_menu.face_gate("%s")' % name in src
    monkeypatch.setenv("CRISSA_HOME", str(kit(None)))
    spec = importlib.util.spec_from_file_location("crissa_panel_secfu", ROOT / "scripts" / "crissa-panel-server.py")
    panel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(panel)
    name = panel.panel_gate_name
    assert name("/v1/mic", {"on": False}) == "mic_off" and name("/v1/mic", {"on": True}) == "mic"
    assert name("/v1/action", {"action": "stop", "unit": "x"}) == "unit_stop" and name("/v1/action", {"action": "os_off"}) == "stop"
    assert name("/v1/action", {"action": "visit_click"}) == "" and name("/v1/screen", {}) == ""
    assert name("/v1/aec", {"setting": "off"}) == "panel_v1_aec" and name("/v1/wall", {"action": "wall_close"}) == "wall_close"
    assert name("/v1/glass-prefs", {"vitals_folded": True, "rev": 3}) == ""           # the glass's own view: open
    assert name("/v1/glass-prefs", {"brightness": 1.2}) == "glass_prefs"             # how it draws her: locked
    assert M.glass_gate_name({"log_room": False}) == "" and M.glass_gate_name({"density": 2.0, "ft_bar": 1}) == "glass_prefs"
    seen.clear()
    assert panel.face_refused("/v1/aec", {"setting": "off"})["note"] == H.FACE_SEQ_FAIL and seen == ["panel_v1_aec"]
    assert panel.face_refused("/v1/screen", {}) is None
    feed = (ROOT / "scripts" / "voiceonly-home-server.py").read_text(encoding="utf-8")   # one file since the fold
    assert "refused = feed_face_refused(act)" in feed and "crissa_home.glass_gate_name(body)" in feed
    assert "crissa_home.face_gate(FEED_OPEN.get(a, " in feed and "out = security_set(body)" in feed
    assert 'house["security"] = sec' in feed and "import crissa_menu" not in feed   # the feed reaches what it did
    assert '"voice_off": "mic_off"' in feed and '"enroll_cancel": "cancel"' in feed


# ---- 2: ID-only's before-action check is the same sequence -----------------------------------------------------------

def test_2_a_house_command_in_id_only_asks_and_waits_for_his_face(kit, monkeypatch):
    kit("id_only")
    said, ran, logs = [], [], []
    monkeypatch.setattr(house_commands, "try_handle_utterance", lambda *a, **k: ran.append(a[0]) or True)
    monkeypatch.setenv("CRISSA_FACE_SEQ_WAIT_S", "2")
    monkeypatch.setattr(id_gate, "presence_look", lambda: pytest.fail("a fixed camera does not turn"))
    reads = iter([_face(age=9.0, fixed=False), _face()])
    assert id_gate.house_command("voice off", {}, {}, speak=said.append, log=logs.append, fetch=lambda: next(reads))
    assert said == [H.FACE_SEQ_ASK] and ran == ["voice off"]
    said.clear()
    assert id_gate.house_command("voice off", {}, {}, speak=said.append, log=logs.append,
                                 fetch=lambda: {"user_id": UID, "face": {}})
    assert said == [H.FACE_SEQ_ASK, H.FACE_SEQ_FAIL] and ran == ["voice off"]
    assert house_commands._refusal() == H.FACE_SEQ_FAIL
    assert "look at the camera for a moment and say it again" in id_gate.reply_line(False)


def test_2_the_tool_guard_waits_its_own_short_time_and_never_speaks(kit, monkeypatch):
    kit("id_only")
    calls = []
    monkeypatch.setattr(id_gate, "act_check", lambda p, fetch=None, wait_s=None, ask=None, look=None:
                        calls.append((wait_s, ask)) or (False, "not_seen", p))
    guard = id_gate.tool_guard(lambda n: n, lambda: {})
    with pytest.raises(id_gate.ActRefused):
        guard(3)
    assert calls == [(id_gate.ACT_FACE_WAIT_S, None)]
    assert id_gate.act_line("not_seen") == H.FACE_SEQ_FAIL and id_gate.act_line("no_camera") == H.FACE_SEQ_NO_CAMERA


# ---- the surfaces ----------------------------------------------------------------------------------------------------

def test_the_walls_say_the_ask_and_offer_double_secures_password():
    import crissa_wall_key as K
    view = {"security": {"lock": True, "ask": H.FACE_SEQ_ASK,
                         "modes": [{"mode": "on", "face": False}, {"mode": "off", "face": True}]}}
    assert K.needs_ask(view, {"action": "lip_sync"}) == H.FACE_SEQ_ASK
    assert K.needs_ask(view, {"action": "mic", "on": False}) == "" and K.needs_ask(view, {"action": "stop"}) == ""
    assert K.needs_ask(view, {"action": "security", "mode": "on"}) == ""
    assert K.needs_ask(view, {"action": "security", "mode": "off"}) == H.FACE_SEQ_ASK
    assert K.needs_ask({"security": {"lock": False}}, {"action": "lip_sync"}) == ""
    sent, asked = [], []
    rows = K.security_buttons({"modes": [{"mode": "double_secure", "label": "Double Secure", "password": True},
                                         {"mode": "on", "label": "On", "on": True}]},
                              lambda body, fail_title="": sent.append(body),
                              password=lambda mode, then: asked.append(mode) or then("pw"))
    assert [r[0] for r in rows] == ["Cancel", "On  (now)", "Double Secure"]
    rows[2][1]()
    assert asked == ["double_secure"] and sent[-1]["password"] == "pw" and sent[-1]["mode"] == "double_secure"
    wall = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
    assert "function menuAsk(body)" in wall and "securityPassword(box, m)" in wall and "m-sec-note" in wall
    panel = (ROOT / "display" / "crissa-panel.html").read_text(encoding="utf-8")
    assert "function paintSecurity(h)" in panel and "b.hidden=hide" in panel and '"/v1/security"' in panel
    for rel in ("display/voiceonly-home.html", "display/voiceonly-panel.html"):
        from _kit import kit
        page = kit(rel).read_text(encoding="utf-8")   # the Wall page is one file since the fold; the panel's overlay stays
        assert "function paintSecRow(h, boxId)" in page and "secHidden(" in page and 'id="secRow"' in page
    render = (ROOT / "lib" / "house_wall_ascii_render.py").read_text(encoding="utf-8")
    assert '_so.get("hide") and lamp_c != "sage"' in render
    desk = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    assert 'crissa_wall_key.open_item("security")' in desk and 'r.get("face") == "refused"' in desk


# ---- 3: Off idles the oracle's look ----------------------------------------------------------------------------------

class _Cam:
    off = False

    def __init__(self):
        self.reads = 0

    def read(self):
        self.reads += 1
        return object()

    def close(self):
        pass


def test_3_off_takes_no_frame_and_the_next_look_after_off_runs(kit, monkeypatch):
    from crissa_oracle import service as S
    monkeypatch.setattr(S, "_names_faces", lambda: "stand-in")
    monkeypatch.setattr(S.face, "engine_reason", lambda: "")             # no face engine is loaded here
    monkeypatch.setattr(S.beam, "present", lambda: False)                # and no microphone is asked
    monkeypatch.setattr(S.beam, "label", lambda: "")
    monkeypatch.setattr(S.enroll, "load", lambda: None)
    looks, posts = [], []
    cam = _Cam()
    orc = S.Oracle(cam=cam, look=lambda frame, enrolled: looks.append(1) or {"seen": True, "faces": 1, "sim": 0.9,
                                                                              "embedding": [0.1] * 8},
                   post=lambda emb, faces=None: posts.append(faces) or True)
    orc.state["camera"] = True
    kit("off")
    st = orc.tick(now=1.0)
    assert st["idle"] == "security_off" and st["camera"] is True and cam.reads == 0 and looks == [] and posts == []
    assert orc.next_wait(now=2.0) == max(orc.period_s, orc.REST_PERIOD_S)
    kit("id_only")
    st = orc.tick(now=3.0)
    assert "idle" not in st and cam.reads == 1 and looks == [1] and posts == [1]
    kit("off")
    monkeypatch.setenv("CRISSA_SECURITY_OFF_IDLES_FACES", "0")
    assert "idle" not in orc.tick(now=4.0) and cam.reads == 2


def test_3_the_status_mark_says_idle(monkeypatch):
    import crissa_status

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"camera": False, "idle": "security_off"}).encode()
    monkeypatch.setattr(crissa_status.crissa_egress, "urlopen", lambda *a, **k: R())
    got = crissa_status.oracle_mark({"CRISSA_ORACLE": "http://127.0.0.1:9"})
    assert got["state"] == "off" and "Security Off" in got["note"]
