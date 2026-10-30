"""The short walk's seams (the User 2026-10-10): the Register coach over real HTTP, the id module's Register parts, the
launcher's part and the two pages. The walk's own steps are tests/test_crissa_init.py; the browser clicking through
the page is scripts/public-loops-accept.py (init-page).

Nothing here reaches a camera, a speaker, a model or the house: every service is a loopback stub in this process."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT / "lib"), str(ROOT / "modules" / "id")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import crissa_agent as za  # noqa: E402
import crissa_home  # noqa: E402
import crissa_init as zi  # noqa: E402


# ---- the Register coach over real HTTP --------------------------------------------------------------------------
class Coach(BaseHTTPRequestHandler):
    """The coach's HTTP surface, as modules/id house_enroll/coach.py serves it, answering a voice-register run and a
    face section in a few reads of /session."""
    calls: list = []
    state: dict = {}

    def log_message(self, *a):
        pass

    def _json(self, code, d):
        raw = json.dumps(d).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        st = type(self).state
        type(self).calls.append(("GET", self.path, None))
        if self.path == "/health":
            return self._json(200, {"ok": True, "service": "house-enroll-coach", "register_token": True})
        if self.path == "/people":
            return self._json(200, {"ok": True, "people": [{"id": "sam", "voice": st.get("voice", 0), "face": st.get("face", 0)}]})
        if self.path == "/session":
            if st.get("running"):
                st["polls"] = st.get("polls", 0) + 1
                if st["polls"] >= 3:
                    st["running"] = False
                    if st["mode"] == "voice-register":
                        st["voice"] = 2
                    else:
                        st["face"] = st.get("face", 0) + 2
                        # the records the real coach writes (face_snap.capture_face): Init counts the ones with
                        # numbers (crissa_init.face_prints, 2026-10-10)
                        d = zi.identity_root() / "people" / "sam" / "face"
                        d.mkdir(parents=True, exist_ok=True)
                        for i in (1, 2):
                            (d / ("enroll_desk_%03d.json" % i)).write_text(json.dumps(
                                {"modality": "face", "model": "auraface-v1", "encoding": [0.044] * 512}))
            return self._json(200, {"running": st.get("running", False), "person_id": "sam", "last_rc": 0,
                                    "last_error": None, "cancelled": False,
                                    "progress": {"phase": "face 1/2", "line": "Please look at the camera.", "done": 1,
                                                 "total": 2}})
        return self._json(404, {"ok": False})

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).calls.append(("POST", self.path, body))
        if self.path == "/start":
            type(self).state.update(running=True, polls=0, mode=body.get("mode"))
            return self._json(200, {"ok": True, "person_id": body.get("person_id")})
        return self._json(200, {"ok": True})


@pytest.fixture
def coach(tmp_path, monkeypatch):
    Coach.calls, Coach.state = [], {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Coach)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    home = tmp_path / "home"
    monkeypatch.setenv("HOUSE_ENROLL_COACH_URL", "http://127.0.0.1:%d" % srv.server_address[1])
    monkeypatch.setenv("CRISSA_INIT", "1")
    monkeypatch.setenv("CRISSA_INIT_SILENT", "1")
    monkeypatch.setenv("CRISSA_IDENTITY", str(home / "identity"))
    monkeypatch.setenv("CRISSA_INIT_WALKER", "standin")
    monkeypatch.setenv("CRISSA_SETUP_PARALLEL", "0")         # round 18's walk is tests/test_round18_setup_20261013.py
    monkeypatch.setenv("CRISSA_SETUP_TEAM", "0")
    monkeypatch.delenv("CRISSA_NO_CAMERA", raising=False)
    monkeypatch.setattr(zi, "_IDM", {"tried": False, "mods": None})
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(zi, "SYNC", True)
    monkeypatch.setattr(zi, "POLL_S", 0.02)
    monkeypatch.setattr(zi, "_machine_mod", lambda: None)
    monkeypatch.setattr(zi, "_voice_install_mod", lambda: None)
    monkeypatch.setattr(zi, "aligner_after_voice", lambda: {"state": "stood-in"})
    monkeypatch.setattr(zi, "_post", lambda path, body, timeout=4.0: {"ok": True})
    monkeypatch.setattr(zi, "grab_frame", lambda timeout=4.0: b"\xff\xd8")
    monkeypatch.setattr(za, "load", lambda home=None, env=None: {})
    monkeypatch.setattr(za, "last_test", lambda home=None: {})
    monkeypatch.setattr(za, "last_pass_cfg", lambda: {})
    zi.configure(base_url="http://conductor.invalid", home=home)
    yield {"home": home}
    srv.shutdown()
    zi.configure()


def _act(action, **body):
    return zi.apply(action, dict(body, run_id=zi.load_state().get("run_id") or ""))


def test_the_walk_drives_the_coach_over_its_http_surface(coach):
    zi.apply("start", {})
    _act("connect_later")
    assert zi.load_state()["step_id"] == "voice_id"
    _act("name", text="Sam")
    st = zi.load_state()
    assert st["register"]["state"] == "done" and st["register"]["voice"] == 2 and st["step_id"] == "face"
    starts = [b for m, p, b in Coach.calls if p == "/start"]
    assert starts[0]["mode"] == "voice-register" and starts[0]["first_boot"] is True
    assert ("GET", "/health", None) in Coach.calls and any(p == "/session" for m, p, b in Coach.calls)
    _act("face", verb="start")
    assert zi.load_state()["step_id"] == "room"         # Your room, then Your hours (the User 2026-10-11)
    _act("room", verb="skip")
    _act("hours", verb="skip")
    st = zi.load_state()
    assert st["status"] == "done" and st["face"]["faces"] == 2
    assert [b["mode"] for m, p, b in Coach.calls if p == "/start"] == ["voice-register", "face"]
    m = json.loads((coach["home"] / "init.json").read_text())
    assert m["done"] is True and m["steps_done"] == ["connect", "machine", "voice_id", "face", "room", "hours"]


def test_with_no_coach_listening_voice_id_says_so(coach, monkeypatch):
    monkeypatch.setenv("HOUSE_ENROLL_COACH_URL", "http://127.0.0.1:9")
    zi.apply("start", {})
    _act("connect_later")
    _act("name", text="Sam")
    assert zi.load_state()["register"]["state"] == "no_coach"


# ---- the id module's Register parts --------------------------------------------------------------------------------
def test_a_packs_coach_is_on_the_token_path_by_default(monkeypatch):
    from crissa_id import paths as idpaths
    from crissa_id.house_enroll import coach, register_camera
    monkeypatch.delenv("HOUSE_REGISTER_TOKEN", raising=False)
    monkeypatch.setattr(idpaths, "is_house", lambda: False)
    assert register_camera.on() is True and coach.health_body()["register_token"] is True
    monkeypatch.setattr(idpaths, "is_house", lambda: True)
    assert register_camera.on() is False, "the house keeps its own switch"
    monkeypatch.setenv("HOUSE_REGISTER_TOKEN", "0")
    monkeypatch.setattr(idpaths, "is_house", lambda: False)
    assert register_camera.on() is False, "a pack can still turn it off by name"


def test_the_coach_reports_the_face_sections_progress():
    from crissa_id.house_enroll import coach
    hub = coach.CoachHub()
    t0 = time.time() - 0.01
    hub.publish({"type": "phase", "text": "face 1/2", "phase": "face-1/2"})
    hub.publish({"type": "coach", "text": "Please look at the camera."})
    hub.publish({"type": "face", "text": "Please look at the camera. — facefinder framing"})
    hub.publish({"type": "face_ok", "text": "snap 1 ok"})
    pr = hub.progress(t0)
    assert pr == {"phase": "face 1/2", "line": "Please look at the camera.", "done": 1, "total": 2, "why": ""}
    hub._session["started"] = t0
    assert hub.session_status()["progress"]["done"] == 1


def test_the_face_section_is_vouched_for_only_by_inits_own_face_step(monkeypatch):
    from crissa_id.house_enroll import coach
    snap = {"status": "running", "step_id": "face", "plate": {"face": {"person_id": "sam"}}}

    class R:
        def __init__(self, d):
            self.d = d

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.d).encode()
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=3: R(snap))
    assert coach._init_vouches("sam", "face", True) is True
    assert coach._init_vouches("sam", "face", False) is False, "only a first boot's request"
    assert coach._init_vouches("sam", "voice", True) is False, "only the face section"
    assert coach._init_vouches("bob", "face", True) is False, "only the person the step is for"
    snap["step_id"] = "voice_id"
    assert coach._init_vouches("sam", "face", True) is False, "only while Init is on its face step"
    src = (ROOT / "modules" / "id" / "crissa_id" / "house_enroll" / "coach.py").read_text()
    assert 'mode != "voice-register" and not _init_vouches(person_id, mode, first_boot)' in src


def test_register_with_no_camera_records_the_voice_only(monkeypatch, tmp_path):
    from crissa_id.house_enroll import register_camera, voice_register as vr
    monkeypatch.setenv("CRISSA_NO_CAMERA", "1")
    monkeypatch.setattr(vr, "_hold_take", lambda: True)
    monkeypatch.setattr(vr, "release_ear_for_enroll", lambda: {})
    monkeypatch.setattr(vr, "_loop_enter_host", lambda: None)
    monkeypatch.setattr(vr, "_host_warm", lambda: {})
    monkeypatch.setattr(register_camera, "take", lambda *a, **k: (_ for _ in ()).throw(AssertionError("token asked")))
    monkeypatch.setattr(vr, "_aim_verified", lambda *a, **k: (_ for _ in ()).throw(AssertionError("camera aimed")))
    monkeypatch.setattr(vr, "PREARM_FILE", tmp_path / "prearm.json")
    out = vr.prearm(SimpleNamespace(root=tmp_path), station="desk", write_marker=False)
    assert out["voice_only"] is True and out["lease_id"] is None
    assert vr._snap_now(SimpleNamespace(root=tmp_path), "sam", station="desk", tag="front", note="x") is None
    src = (ROOT / "modules" / "id" / "crissa_id" / "house_enroll" / "voice_register.py").read_text()
    assert 'if register_camera.on() and not arm.get("token") and not arm.get("voice_only"):' in src


# ---- the launcher ---------------------------------------------------------------------------------------------------
def _launcher():
    spec = importlib.util.spec_from_file_location("crissa_launcher_for_init", ROOT / "scripts" / "crissa.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _args(**kw):
    base = dict(face="crissa", audio_at="host", port_offset=0, voice=None, with_asr=False, with_aligner=False,
                no_camera=False, no_enroll=False, no_desk=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_every_child_gets_registers_token_path_unless_the_caller_named_it(monkeypatch):
    z = _launcher()
    monkeypatch.setattr(z, "presence_module_installed", lambda pmod: True)
    p = z.plan(_args(), env_in={})
    for kid in p["children"]:
        assert kid["env"]["HOUSE_REGISTER_TOKEN"] == "1" and kid["env"]["RAIL_REGISTER_TOKEN"] == "1", kid["name"]
    p = z.plan(_args(), env_in={"HOUSE_REGISTER_TOKEN": "0"})
    assert all("HOUSE_REGISTER_TOKEN" not in k["env"] or k["env"]["HOUSE_REGISTER_TOKEN"] != "1" for k in p["children"])


def test_the_voice_pick_is_not_written_while_init_is_needed(monkeypatch):
    z = _launcher()
    seen = []
    monkeypatch.setattr(z, "voice_tier", lambda write=True: seen.append(write) or {"main": "pocket"})
    monkeypatch.setattr(z, "init_needed", lambda env=None: True)
    assert z.pick_tier(SimpleNamespace(no_voice=False, plan=False)) == {"main": "pocket"} and seen == [False]
    monkeypatch.setattr(z, "init_needed", lambda env=None: False)
    z.pick_tier(SimpleNamespace(no_voice=False, plan=False))
    assert seen[-1] is True, "after Init the launcher keeps the pick as before"
    assert z.pick_tier(SimpleNamespace(no_voice=True, plan=False)) == {}


def test_the_voice_init_picks_waits_as_a_late_lane(monkeypatch):
    z = _launcher()
    assert z.LATE_ENGINES == ("multilingual", "turbo", "pocket", "audio8")
    src = (ROOT / "scripts" / "crissa.py").read_text()
    assert "THE VOICE PICK WAITS FOR INIT (2026-10-10)" in src and "a.tier = pick_tier(a)" in src


def test_init_asks_the_launcher_to_start_the_voice_it_picked(monkeypatch, tmp_path):
    asked = []

    class VI:
        NO_INSTALL = ("text", "say")

        def launcher_start(self, home=None):
            return lambda eng: asked.append(eng)
    monkeypatch.setattr(zi, "_voice_install_mod", lambda: VI())
    got = zi.start_lanes({"chosen": "turbo", "backup": {"chosen": "pocket"}, "announcer": {"chosen": "audio8"}})
    assert got == asked == ["turbo", "pocket", "audio8"]
    asked.clear()
    assert zi.start_lanes({"chosen": "text"}) == [] and asked == []
    monkeypatch.setattr(zi, "_voice_install_mod", lambda: None)
    assert zi.start_lanes({"chosen": "turbo"}) == []


def test_a_first_boot_opens_the_init_page(monkeypatch):
    z = _launcher()
    monkeypatch.setattr(z, "init_needed", lambda env=None: True)
    assert z.first_page("http://127.0.0.1:8100") == "http://127.0.0.1:8100/init"


# ---- the pages ------------------------------------------------------------------------------------------------------
PAGE = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")


def test_the_init_page_reads_the_walk_and_posts_its_keys():
    assert "<title>Set up Crissa</title>" in PAGE
    for needle in ('getJSON("/v1/init")', 'fetch("/v1/init", { method: "POST"', 'getJSON("/v1/register")',
                   'act("connect_test"', 'act("connect_continue")', 'act("connect_later", { by: "page" })', 'act("name"',
                   '"machine"', '"register"', '"face"', 'act("abort", {', 'run_id'):
        assert needle in PAGE, needle
    assert not re.search(r"""(src|href)=["']https?://""", PAGE), "no external loads"
    assert not re.search(r"(?<![A-Za-z0-9_])Crissa(?![A-Za-z0-9_])", PAGE), "her name is Crissa"
    assert "@media (max-width: 720px)" in PAGE and "prefers-color-scheme: dark" in PAGE


def test_the_wall_page_sends_a_first_boot_to_init():
    wall = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
    assert "async function initGate()" in wall and 'location.replace("/init")' in wall
    assert 'Q.get("noinit") === "1"' in wall and "if (await initGate()) return;" in wall


def test_the_connect_page_carries_the_walk_on():
    page = (ROOT / "display" / "crissa-connect.html").read_text(encoding="utf-8")
    assert 'id="init-continue"' in page and 'action: "connect_continue", run_id: INIT.run' in page
    assert 'location.href = "/init"' in page and "loadInit();" in page
    assert "j.thinking" in page, "a model still thinking is not shown as a failure"


def test_the_conductor_serves_the_page_only_with_init_on():
    # page_served is serves(), plus Voice ID again's own page (?voice_id=1) in a pack (voice-id-101, 2026-10-11)
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    anchor = 'if head in ("/init", "/init/") and crissa_init.page_served('
    assert src.count(anchor) == 1
    i = src.index(anchor)
    assert '"crissa-init.html"' in src[i:i + 400]
    lib = (ROOT / "lib" / "crissa_init.py").read_text()
    j = lib.index("def page_served(")
    assert "if serves():" in lib[j:j + 600] and "crissa_home.is_house()" in lib[j:j + 800]
