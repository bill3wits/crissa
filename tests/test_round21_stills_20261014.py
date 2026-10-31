"""Round 21, Voice ID's stills, the camera and Lila's turn film (2026-10-14; docs/checkpoints/r21-stills-20261014).
.101 round 20 (crissa-e86395cb): the User's turned stills read "no picture in 0 looks", a visitor's "15 looks, yaw None
... no_face", Face photos' first pass a camera 503 on enroll_begin_fov / facefind / look_around, the visitor's face not
found at desk distance until they leaned in; and the User: "Lila's 'turn your head' clip fits there". Pinned in both
coach copies where the coach is involved.
"""
from __future__ import annotations

import base64
import importlib
import json
import sys
import urllib.error
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
PKGS = ["crissa_id.house_enroll", "crissa_presence.enroll_panel"]


@pytest.fixture(autouse=True)
def _own_presence_modules():
    held = {k: v for k, v in sys.modules.items() if k == "crissa_presence" or k.startswith("crissa_presence.")}
    yield
    for k in [k for k in sys.modules if k == "crissa_presence" or k.startswith("crissa_presence.")]:
        if k not in held:
            del sys.modules[k]
    sys.modules.update(held)


def _mod(pkg, name, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "modules" / ("id" if pkg.startswith("crissa_id") else "presence")))
    return importlib.import_module(pkg + "." + name)


class _Site:
    camera_url = "http://127.0.0.1:59192"
    raw: dict = {}


def _err(code):
    return urllib.error.HTTPError("http://cam/snapshot", code, "x", {}, None)


# ---- 2. a camera that cannot turn is only asked for pictures ----------------------------------------------------------
@pytest.mark.parametrize("pkg", PKGS)
@pytest.mark.parametrize("ptz,moves", [("motor", True), ("digital", False), ("none", False)])
def test_the_camera_says_whether_it_turns(pkg, ptz, moves, monkeypatch):
    FS = _mod(pkg, "face_snap", monkeypatch)
    monkeypatch.setattr(FS, "_CAM_KIND", {})
    monkeypatch.setattr(FS, "_get_bytes", lambda url, timeout=15.0: json.dumps({"camera_ptz": ptz}).encode())
    assert FS.camera_moves(_Site()) is moves
    monkeypatch.setenv("CRISSA_FIXED_CAM_SNAP_ONLY", "0")
    assert FS.camera_moves(_Site()) is True, "the kill switch: the old choreography"


@pytest.mark.parametrize("pkg", PKGS)
def test_an_unreadable_camera_keeps_the_old_choreography_and_is_asked_again(pkg, monkeypatch):
    FS = _mod(pkg, "face_snap", monkeypatch)
    monkeypatch.setattr(FS, "_CAM_KIND", {})

    def down(url, timeout=15.0):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(FS, "_get_bytes", down)
    assert FS.camera_moves(_Site()) is True and FS._CAM_KIND == {}


@pytest.mark.parametrize("pkg", PKGS)
def test_a_fixed_camera_is_never_aimed_or_face_found(pkg, monkeypatch):
    FS = _mod(pkg, "face_snap", monkeypatch)
    monkeypatch.setattr(FS, "_CAM_KIND", {_Site.camera_url: "digital"})
    posted = []
    monkeypatch.setattr(FS, "_post_json", lambda url, body, timeout=20.0: posted.append(url))
    monkeypatch.setattr(FS, "_facefinder", lambda site, enable: posted.append("facefinder") or True)
    FS.enroll_begin_fov(_Site(), "desk")
    FS._release_facelock(_Site())
    assert posted == [] and FS._enroll_fov.get("aimed") is True
    src = (ROOT / "modules" / ("id/crissa_id/house_enroll" if pkg.startswith("crissa_id") else
                               "presence/crissa_presence/enroll_panel") / "face_snap.py").read_text()
    assert "    if want_ff and not camera_moves(site):\n        want_ff = False" in src


# ---- 1/2. a picture the camera cannot give yet is asked for again -----------------------------------------------------
@pytest.mark.parametrize("pkg", PKGS)
def test_a_snapshot_rides_out_a_503(pkg, monkeypatch):
    FS = _mod(pkg, "face_snap", monkeypatch)
    answers = [_err(503), _err(503), b"jpeg"]

    def get(url, timeout=15.0):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(FS, "_get_bytes", get)
    monkeypatch.setattr(FS.time, "sleep", lambda s: None)
    assert FS.snapshot_jpeg(_Site()) == b"jpeg"


@pytest.mark.parametrize("pkg", PKGS)
def test_a_snapshot_says_a_real_error_at_once_and_a_503_at_the_deadline(pkg, monkeypatch):
    FS = _mod(pkg, "face_snap", monkeypatch)
    n = {"get": 0}

    def get404(url, timeout=15.0):
        n["get"] += 1
        raise _err(404)
    monkeypatch.setattr(FS, "_get_bytes", get404)
    with pytest.raises(urllib.error.HTTPError):
        FS.snapshot_jpeg(_Site())
    assert n["get"] == 1
    monkeypatch.setattr(FS, "SNAP_RETRY_S", 0.0)          # CRISSA_SNAPSHOT_RETRY_S=0

    def get503(url, timeout=15.0):
        n["get"] += 1
        raise _err(503)
    monkeypatch.setattr(FS, "_get_bytes", get503)
    with pytest.raises(urllib.error.HTTPError):
        FS.snapshot_jpeg(_Site())
    assert n["get"] == 2


# ---- 1. the turn is looked at through its count -----------------------------------------------------------------------
def _vr(pkg, monkeypatch, looks, pictures=None):
    VR = _mod(pkg, "voice_register", monkeypatch)
    monkeypatch.setattr(VR, "_cancelled", lambda: False)
    monkeypatch.setattr(VR, "_camera_off", lambda: False)
    monkeypatch.setattr(VR, "coach_emit", lambda **kw: None)
    pics = list(pictures) if pictures is not None else None
    n = {"look": 0}

    def look(site, station):
        n["look"] += 1
        if pics is not None:
            p = pics.pop(0) if pics else (None, "HTTP Error 503: no frame")
            return p
        return b"jpeg-%d" % n["look"], ""
    monkeypatch.setattr(VR, "_turn_look", look)
    answers = list(looks)
    calls = []

    def post(url, body, timeout=12.0):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("/v1/face_yaw"):
            return answers.pop(0) if answers else {"ok": False, "error": "no_face", "n_faces": 0}
        return {"ok": True, "bin": "three_quarter_L", "yaw": -0.3}
    monkeypatch.setattr(VR, "_post", post)
    clock = {"t": 100.0}

    def tick():
        clock["t"] += 0.1
        return clock["t"]
    return VR, calls, tick


def _yaw(y):
    return {"ok": True, "n_faces": 1, "yaw": y, "bin": "x", "det": 0.9}


@pytest.mark.parametrize("pkg", PKGS)
def test_no_face_by_the_wait_and_the_looks_go_on_through_the_count(pkg, monkeypatch):
    nofaces = [{"ok": False, "error": "no_face", "n_faces": 0}] * 40      # the visitor's: 15 looks and nothing, at 3 s
    VR, calls, tick = _vr(pkg, monkeypatch, nofaces + [_yaw(-0.35)])
    got = VR._snap_turned(object(), "guest", station="desk", tag="profile", note="verbal away", side="left",
                          sleep=lambda s: None, clock=tick)
    assert got["why"] == "turned" and got["stored"] is True and got["looks"] == 41
    assert VR.TURN_LOOK_MAX_S == 9.0


@pytest.mark.parametrize("pkg", PKGS)
def test_the_looks_end_when_the_next_count_starts(pkg, monkeypatch):
    VR, calls, tick = _vr(pkg, monkeypatch, [])
    state = {"n": 0}

    def tick_and_next():
        state["n"] += 1
        if state["n"] == 6:
            VR._TURN_GEN["n"] += 1                       # the next count's cue
        return tick()
    got = VR._snap_turned(object(), "sam", station="desk", tag="profile", note="n", side="right",
                          sleep=lambda s: None, clock=tick_and_next)
    assert got["looks"] <= 6, "the next count's cue ends the looks (the whole window is ~90 looks)"


@pytest.mark.parametrize("pkg", PKGS)
def test_no_picture_says_the_cameras_error(pkg, monkeypatch, capsys):
    VR, calls, tick = _vr(pkg, monkeypatch, [], pictures=[])
    got = VR._snap_turned(object(), "sam", station="desk", tag="profile", note="n", side="left",
                          sleep=lambda s: None, clock=tick)
    assert got["why"] == "no picture" and got["looks"] == 0 and "503" in got["error"]
    assert "no picture in 0 looks (the camera: HTTP Error 503: no frame)" in capsys.readouterr().out


@pytest.mark.parametrize("pkg", PKGS)
def test_a_face_seen_by_the_wait_still_ends_the_looks_there(pkg, monkeypatch):
    VR, calls, tick = _vr(pkg, monkeypatch, [_yaw(0.02), _yaw(-0.09)] + [_yaw(0.01)] * 200)
    got = VR._snap_turned(object(), "sam", station="desk", tag="profile", note="n", side="left",
                          sleep=lambda s: None, clock=tick)
    assert got["why"] == "time" and got["looks"] <= int(VR.TURN_WAIT_S / 0.1) + 2


@pytest.mark.parametrize("pkg", PKGS)
def test_the_kill_switch_is_round_20s_window(pkg, monkeypatch):
    VR, calls, tick = _vr(pkg, monkeypatch, [{"ok": False, "error": "no_face", "n_faces": 0}] * 200)
    monkeypatch.setattr(VR, "TURN_LOOK_MAX_S", 0.0)                       # CRISSA_TURN_LOOK_MAX_S=0
    got = VR._snap_turned(object(), "sam", station="desk", tag="profile", note="n", side="left",
                          sleep=lambda s: None, clock=tick)
    assert got["looks"] <= int(VR.TURN_WAIT_S / 0.1) + 2


# ---- 3. a small face is looked for on crops ----------------------------------------------------------------------------
def _engine(monkeypatch, found_in):
    monkeypatch.syspath_prepend(str(ROOT / "modules" / "presence"))
    FE = importlib.import_module("crissa_presence.face_engine")
    eng = object.__new__(FE.FaceEngine)
    eng._lm, eng._rec, eng._why = object(), None, ""
    seen = []

    class P:
        def __init__(self, x, y):
            self.x, self.y = x, y

    def marks(frame):
        h, w = frame.shape[:2]
        seen.append((w, h))
        if not found_in(w, h):
            return []
        mesh = [P(0.5, 0.5)] * 478                         # a face in the middle of whatever picture it is shown
        mesh = [P(0.45 + 0.1 * (i % 2), 0.45 + 0.1 * ((i // 2) % 2)) for i in range(478)]
        return [mesh]
    monkeypatch.setattr(eng, "_landmarks", marks)
    return FE, eng, seen


def test_a_face_the_whole_still_misses_is_found_on_a_crop_for_enrolment(monkeypatch):
    frame = np.zeros((400, 640, 3), np.uint8)
    FE, eng, seen = _engine(monkeypatch, lambda w, h: w < 640)              # found only once the picture is a crop
    assert eng.detect(frame) == [], "the room's own looks never pay for crops"
    faces = eng.detect(frame, rescue=True)
    assert len(faces) == 1 and seen[-1] == (384, 240)                        # the 60 % centre crop
    x1, y1, x2, y2 = faces[0].bbox
    assert 640 * 0.2 + 384 * 0.45 - 1 <= x1 <= 640 * 0.2 + 384 * 0.45 + 1     # mapped back to the whole still
    monkeypatch.setenv("CRISSA_FACE_CROP_RESCUE", "0")
    assert eng.detect(frame, rescue=True) == []


def test_the_crops_cover_the_still():
    sys.path.insert(0, str(ROOT / "modules" / "presence"))
    FE = importlib.import_module("crissa_presence.face_engine")
    crops = FE.rescue_crops(640, 400)
    assert crops[0] == (128, 80, 384, 240) and crops[1] == (192, 120, 256, 160) and len(crops) == 6
    assert {(x, y) for x, y, _, _ in crops[2:]} == {(0, 0), (0, 160), (256, 0), (256, 160)}
    store = (ROOT / "modules" / "presence" / "crissa_presence" / "store_face.py").read_text()
    assert store.count("faces = _detect_enrol(get_face_engine(), img)") == 2   # store and probe: enrolment's two
    assert "return list(engine.detect(img, rescue=True) or [])" in store


# ---- 4. Lila's turn film under the first turn's cue --------------------------------------------------------------------
@pytest.mark.parametrize("pkg", PKGS)
def test_the_turn_film_plays_under_the_cue_and_her_loop_follows(pkg, monkeypatch, tmp_path):
    VR = _mod(pkg, "voice_register", monkeypatch)
    monkeypatch.setattr(VR, "_cancelled", lambda: False)
    monkeypatch.setattr(VR, "_house_tree", lambda: False)
    monkeypatch.setattr(VR, "_inventory_clip", lambda key: tmp_path / key)
    monkeypatch.setattr(VR, "_drop_register_cues", lambda: [])
    sent = []
    monkeypatch.setattr(VR, "_conductor", lambda path, payload=None, timeout=4.0: sent.append((path, payload["key"])))
    assert VR.turn_film_under_cue(object()) is True
    assert sent == [("/v1/play", "lila-reg-turn"), ("/v1/cue", "lila-reg-listen")]
    monkeypatch.setenv("CRISSA_TURN_FILM", "0")
    assert VR.turn_film_under_cue(object()) is False
    monkeypatch.setattr(VR, "_house_tree", lambda: True)
    monkeypatch.delenv("CRISSA_TURN_FILM")
    assert VR.turn_film_under_cue(object()) is False, "never on the house"


@pytest.mark.parametrize("pkg", PKGS)
def test_only_the_first_turn_of_the_counts_shows_the_film(pkg):
    src = (ROOT / "modules" / ("id/crissa_id/house_enroll" if pkg.startswith("crissa_id") else
                               "presence/crissa_presence/enroll_panel") / "voice_register.py").read_text()
    assert ("            if away and not any(t[3] for t in plan[:i - 1]):\n"
            "                turn_film_under_cue(site)") in src
    assert src.index("turn_film_under_cue(site)       #") < src.index("            _announce(site, _cue(words))")


def test_the_turn_film_borrows_her_lips_and_its_turn_is_filled():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'os.environ.get("CRISSA_LILA_PAINT_FILMS", "lila-reg-turn")' in src
    assert 'or key in LIPS_BORROW_FILMS):' in src
    poses = ROOT / "display" / "crissa-poses"
    marks = json.loads((poses / "clip-lila-reg-turn.lipmarks.json").read_text())
    fill = json.loads((poses / "clip-lila-reg-turn.mouth.turnfill.json").read_text())
    mouth = json.loads((poses / "clip-lila-reg-turn.mouth.json").read_text())
    head = json.loads((poses / "clip-lila-reg-turn.head.json").read_text())["frames"]
    frames = marks.get("frames") or []
    assert len([f for f in frames if f]) == 240
    blank = [i for i, f in enumerate(mouth["frames"]) if not f]
    assert blank == list(range(63, 113)), "the turn the live sidecar keeps blank"
    assert all(fill["frames"][i] for i in blank), "every turn frame has a box in the fill"
    assert all(head[i] for i in (63, 64)), "the two frames the tracker lost are filled from either side"
