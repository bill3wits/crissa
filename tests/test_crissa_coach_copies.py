"""The Register coach has two copies, and Init's three changes are in both (the User 2026-10-10).

modules/id/crissa_id/house_enroll is the coach when modules/id is installed; modules/presence/crissa_presence/
enroll_panel serves an install without it (modules/presence/scripts/crissa-enroll.py coach_cli). The short walk's
Voice ID and face photos need, from whichever copy serves:
  1. the face vouch: Init's own face step may take the new User's photos before anyone can sign in as them;
  2. face progress in GET /session ({phase, line, done, total});
  3. voice-only Register under no camera (no token, no lease, no aim, no still);
and a pack's coach is on the token path by default (register_camera.on()).

Each copy is imported under its own package name from its own module folder; nothing reaches a camera, a speaker or
the house (urlopen is a stub where a test reads the player)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
COPIES = {
    "id": (ROOT / "modules" / "id", "crissa_id", "crissa_id.house_enroll"),
    "presence": (ROOT / "modules" / "presence", "crissa_presence", "crissa_presence.enroll_panel"),
}


@pytest.fixture(params=sorted(COPIES))
def copy(request, monkeypatch, tmp_path):
    """(coach, register_camera, voice_register, paths module, how the copy reads the camera switch) for one copy,
    imported fresh under its package name; whatever held that name before is put back after."""
    mod_dir, top, pkg = COPIES[request.param]
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CRISSA_NO_CAMERA", raising=False)
    monkeypatch.setenv("HOUSE_ENROLL_PREARM", str(tmp_path / "prearm.json"))
    saved = {k: v for k, v in sys.modules.items() if k == top or k.startswith(top + ".")}
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, str(mod_dir))
    try:
        import importlib
        coach = importlib.import_module(pkg + ".coach")
        rc = importlib.import_module(pkg + ".register_camera")
        vr = importlib.import_module(pkg + ".voice_register")
        paths = importlib.import_module(top + ".paths")
        yield SimpleNamespace(name=request.param, coach=coach, rc=rc, vr=vr, paths=paths,
                              src=Path(coach.__file__).parent)
    finally:
        sys.path.remove(str(mod_dir))
        for k in [k for k in sys.modules if k == top or k.startswith(top + ".")]:
            del sys.modules[k]
        sys.modules.update(saved)


def test_a_packs_coach_is_on_the_token_path_by_default(copy, monkeypatch):
    monkeypatch.delenv("HOUSE_REGISTER_TOKEN", raising=False)
    monkeypatch.setattr(copy.paths, "is_house", lambda: False)
    assert copy.rc.on() is True and copy.coach.health_body()["register_token"] is True
    monkeypatch.setattr(copy.paths, "is_house", lambda: True)
    assert copy.rc.on() is False, "the house keeps its own switch"
    monkeypatch.setenv("HOUSE_REGISTER_TOKEN", "0")
    monkeypatch.setattr(copy.paths, "is_house", lambda: False)
    assert copy.rc.on() is False, "a pack can still turn it off by name"
    monkeypatch.setenv("HOUSE_REGISTER_TOKEN", "1")
    monkeypatch.setattr(copy.paths, "is_house", lambda: True)
    assert copy.rc.on() is True


def test_is_house_reads_the_state_home_manifest_and_a_scratch_tree_is_a_pack(copy):
    assert isinstance(copy.paths.is_house(), bool)


def test_the_coach_reports_the_face_sections_progress(copy):
    hub = copy.coach.CoachHub()
    t0 = time.time() - 0.01
    hub.publish({"type": "phase", "text": "face 1/2", "phase": "face-1/2"})
    hub.publish({"type": "coach", "text": "Please look at the camera."})
    hub.publish({"type": "face", "text": "Please look at the camera. — facefinder framing"})
    hub.publish({"type": "face_ok", "text": "snap 1 ok"})
    assert hub.progress(t0) == {"phase": "face 1/2", "line": "Please look at the camera.", "done": 1, "total": 2,
                                "why": ""}
    assert hub.progress(0) == {"phase": "", "line": "", "done": 0, "total": 0, "why": ""}, "no session, no progress"
    hub._session["started"] = t0
    assert hub.session_status()["progress"]["done"] == 1


class _R:
    def __init__(self, d):
        self.d = d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.d).encode()


def test_the_face_section_is_vouched_for_only_by_inits_own_face_step(copy, monkeypatch):
    snap = {"status": "running", "step_id": "face", "plate": {"face": {"person_id": "sam"}}}
    asked = []
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=3: asked.append(url) or _R(snap))
    v = copy.coach._init_vouches
    assert v("sam", "face", True) is True
    assert asked and asked[-1].startswith("http://127.0.0.1:") and asked[-1].endswith("/v1/init"), asked
    assert v("sam", "face", False) is False, "only a first boot's request"
    assert v("sam", "voice", True) is False, "only the face section"
    assert v("bob", "face", True) is False, "only the person the step is for"
    snap["step_id"] = "voice_id"
    assert v("sam", "face", True) is False, "only while Init is on its face step"
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=3: (_ for _ in ()).throw(OSError("down")))
    snap["step_id"] = "face"
    assert v("sam", "face", True) is False, "no player answering is no vouch"
    src = (copy.src / "coach.py").read_text(encoding="utf-8")
    assert 'mode != "voice-register" and not _init_vouches(person_id, mode, first_boot)' in src


def test_register_with_no_camera_records_the_voice_only(copy, monkeypatch, tmp_path):
    vr, rc = copy.vr, copy.rc
    monkeypatch.setenv("CRISSA_NO_CAMERA", "1")
    monkeypatch.setattr(vr, "_hold_take", lambda: True)
    monkeypatch.setattr(vr, "release_ear_for_enroll", lambda: {})
    monkeypatch.setattr(vr, "_loop_enter_host", lambda: None)
    monkeypatch.setattr(vr, "_host_warm", lambda: {})
    monkeypatch.setattr(rc, "take", lambda *a, **k: (_ for _ in ()).throw(AssertionError("token asked")))
    monkeypatch.setattr(vr, "_aim_verified", lambda *a, **k: (_ for _ in ()).throw(AssertionError("camera aimed")))
    monkeypatch.setattr(vr, "_ff_set", lambda *a, **k: (_ for _ in ()).throw(AssertionError("facefinder asked")))
    monkeypatch.setattr(vr, "enroll_hold_snap", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a still")))
    monkeypatch.setattr(vr, "PREARM_FILE", tmp_path / "prearm.json")
    out = vr.prearm(SimpleNamespace(root=tmp_path), station="desk", write_marker=False)
    assert out["voice_only"] is True and out["lease_id"] is None and "token" not in out
    assert vr._snap_now(SimpleNamespace(root=tmp_path), "sam", station="desk", tag="front", note="x") is None
    src = (copy.src / "voice_register.py").read_text(encoding="utf-8")
    assert 'if register_camera.on() and not arm.get("token") and not arm.get("voice_only"):' in src
    assert 'if (lease_id or not register_camera.on()) and not out.get("voice_only"):' in src


def test_a_pack_records_voice_id_from_the_default_input(copy, monkeypatch, tmp_path):
    """The kit's clean-install trial (2026-10-10): Voice ID stopped on "site.audio.mic is empty". A pack's site.json
    names no microphone, so a pack records from the computer's default input; the house never reaches the default."""
    vr = copy.vr
    import importlib
    lv = importlib.import_module(vr.__name__.rsplit(".", 1)[0] + ".local_values")
    monkeypatch.setattr(vr, "enroll_mic", lambda site, **k: "")
    monkeypatch.setattr(lv, "local", lambda key, generic: "")
    monkeypatch.delenv("HOUSE_ENROLL_MIC", raising=False)
    monkeypatch.delenv("HOUSE_ENROLL_MIC_REQUIRED", raising=False)
    bare = vr.Site(root=tmp_path, site_id="crissa", display="This room", raw={"schema": "house_site/v1"})
    monkeypatch.setattr(copy.paths, "is_house", lambda: False)
    assert vr.room_mic_for(bare, None, "desk") == "@DEFAULT_SOURCE@" == vr.DEFAULT_SOURCE
    assert vr.room_mic_for(bare, "my-usb-mic", "desk") == "my-usb-mic", "a named microphone wins"
    named = vr.Site(root=tmp_path, site_id="crissa", display="x", raw={"audio": {"mic": "room-mic"}})
    assert vr.room_mic_for(named, None, "desk") == "room-mic", "site.json's audio.mic wins over the default"
    monkeypatch.setenv("HOUSE_ENROLL_MIC_REQUIRED", "1")
    assert vr.room_mic_for(bare, None, "desk") == "", "the refusal stays when a microphone is required"
    monkeypatch.delenv("HOUSE_ENROLL_MIC_REQUIRED")
    monkeypatch.setattr(copy.paths, "is_house", lambda: True)
    assert vr.room_mic_for(bare, None, "desk") == "", "the house names its own; it never takes the default"
    src = (copy.src / "voice_register.py").read_text(encoding="utf-8")
    assert "room_mic = room_mic_for(site, mic, station)" in src


def test_with_no_camera_the_voice_take_takes_no_stills(copy):
    src = (copy.src / "voice_register.py").read_text(encoding="utf-8")
    take = src[src.index("def _take_voice("):src.index("def _cam_loop()")]
    cam_off = "_paths.camera_off()" if copy.name == "id" else "_ports.camera_off()"
    assert "if snap_into is not None and not %s:" % cam_off in take


def test_both_copies_carry_the_same_three_rules():
    """A drift guard: the rule bodies are the same in both copies (only how each reaches its port table and its
    camera switch differs)."""
    def body(path: Path, start: str, end: str) -> str:
        s = path.read_text(encoding="utf-8")
        return s[s.index(start):s.index(end, s.index(start))]
    idc = ROOT / "modules" / "id" / "crissa_id" / "house_enroll"
    prc = ROOT / "modules" / "presence" / "crissa_presence" / "enroll_panel"
    a = body(idc / "coach.py", "    def progress(self, since: float)", "    def cancel_enroll(")
    b = body(prc / "coach.py", "    def progress(self, since: float)", "    def cancel_enroll(")
    assert a == b
    a = body(idc / "register_camera.py", "def on() -> bool:", "def user_mode(")
    b = body(prc / "register_camera.py", "def on() -> bool:", "def user_mode(")
    assert a == b
    a = body(idc / "voice_register.py", "def room_mic_for(", "def run_voice_register(").replace("_paths.is_house()", "IS_HOUSE")
    b = body(prc / "voice_register.py", "def room_mic_for(", "def run_voice_register(").replace("_ppaths.is_house()", "IS_HOUSE")
    assert a == b
    a = body(idc / "coach.py", "    face = ((snap.get(\"plate\")", "def health_body(")
    b = body(prc / "coach.py", "    face = ((snap.get(\"plate\")", "def health_body(")
    assert a == b
