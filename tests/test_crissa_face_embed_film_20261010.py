"""The face photos' real embed path, end to end, on a real image (the User 2026-10-10, .101 W: every embed failed with
NameError("name '_paths' is not defined") and Init still said the face step was done).

The image is a frame of the character's OWN film from the shipped library (display/crissa-poses, her listen loop): she is
a character, not a person. Never a person's photo, never a house photo.

This runs the pack's own engine (MediaPipe Face Landmarker + AuraFace) through each coach copy's own child process, so
it needs an installed face env and its two model files: CRISSA_FACE_PY (an id or presence env's python, e.g.
<state home>/id/env/bin/python after `crissa-id.py --install`) and CRISSA_FACE_HOME (a state home holding
oracle/models/face_landmarker.task and oracle/models/auraface/glintr100.onnx). Without them it is skipped and says why;
the kit's sandbox trial runs it on its fresh install."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
FILM = ROOT / "display" / "crissa-poses" / "clip-listen-back.mp4"
PY = os.environ.get("CRISSA_FACE_PY", "")
HOME = os.environ.get("CRISSA_FACE_HOME", "")

pytestmark = pytest.mark.skipif(
    not (PY and Path(PY).is_file() and HOME and (Path(HOME) / "oracle" / "models" / "auraface" / "glintr100.onnx").is_file()
         and (Path(HOME) / "oracle" / "models" / "face_landmarker.task").is_file() and shutil.which("ffmpeg")
         and FILM.is_file()),
    reason="needs CRISSA_FACE_PY (an installed face env), CRISSA_FACE_HOME (its models), ffmpeg and her listen film")


@pytest.fixture
def frame(tmp_path):
    jpg = tmp_path / "her-listen-frame.jpg"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "1.0", "-i", str(FILM), "-frames:v", "1", str(jpg)],
                   check=True, timeout=60)
    return jpg


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "oracle").mkdir(parents=True)
    (h / "oracle" / "models").symlink_to(Path(HOME) / "oracle" / "models")     # read, never copied (270 MB)
    monkeypatch.setenv("CRISSA_HOME", str(h))
    monkeypatch.setenv("CRISSA_IDENTITY", str(h / "identity"))
    monkeypatch.setenv("HOUSE_ENROLL_FACE_PYTHON", PY)
    return h


def _copy(monkeypatch, which):
    import importlib
    if which == "presence":
        held = sys.modules.get("crissa_presence")
        if held is not None and not hasattr(held, "__path__"):
            for k in [k for k in sys.modules if k == "crissa_presence" or k.startswith("crissa_presence.")]:
                monkeypatch.delitem(sys.modules, k)
        monkeypatch.syspath_prepend(str(ROOT / "modules" / "presence"))
        return importlib.import_module("crissa_presence.enroll_panel.face_snap")
    monkeypatch.syspath_prepend(str(ROOT / "modules" / "id"))
    return importlib.import_module("crissa_id.house_enroll.face_snap")


@pytest.mark.parametrize("which", ["id", "presence"])
def test_each_coach_copy_embeds_her_film_frame(which, frame, home, monkeypatch):
    fs = _copy(monkeypatch, which)
    rec = fs.embed_jpegs([frame])[0]
    assert rec.get("ok") is True, rec.get("error")
    assert rec["dim"] == 512 and len(rec["encoding"]) == 512 and str(rec.get("model")).startswith("auraface")
    assert rec.get("bin") in ("front", "three_quarter_L", "three_quarter_R"), rec.get("bin")


def test_the_face_section_saves_two_prints_and_init_counts_them(frame, home, monkeypatch):
    """The id coach's own face section (the one Init's face step runs), the camera's two looks stood in by her film
    frame: two prints with numbers on disk, the counter at 2 of 2, enroll.json written, and Init's count agrees."""
    fs = _copy(monkeypatch, "id")
    from crissa_id.house_enroll import sectioned_session as S
    from crissa_id.house_enroll.coach import CoachHub
    hub = CoachHub()
    monkeypatch.setattr(S, "coach_emit", lambda **e: hub.publish(e))
    for name in ("speak_loud", "announce_turn_profile", "announce_hold_still", "done_chirp", "announce_section_voice"):
        monkeypatch.setattr(S.cues, name, lambda *a, **k: None)
    monkeypatch.setattr(S, "enroll_begin_fov", lambda *a, **k: None)
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    jpeg = frame.read_bytes()
    monkeypatch.setattr(fs, "point_then_facefinder_snap", lambda *a, **k: (jpeg, {"tool": "film-frame"}))
    from crissa_id.house_enroll.person import ensure_person
    root = home / "identity"
    ensure_person("sam", display="Sam", root=root)
    site = type("Site", (), {"root": root, "site_id": "crissa", "raw": {}, "sink": None, "mic": ""})()
    t0 = hub.history[0]["ts"] - 1 if hub.history else 0.0
    out = S.run_face_section(site, "sam", at_desk=True)
    assert [r["quality"]["pass"] for r in out] == [True, True], [r["quality"] for r in out]
    pr = hub.progress(t0 or 1.0)
    assert (pr["done"], pr["total"]) == (2, 2), pr
    faces = sorted((root / "people" / "sam" / "face").glob("*.json"))
    encs = [json.loads(f.read_text()).get("encoding") for f in faces]
    assert sum(1 for e in encs if isinstance(e, list) and len(e) == 512) == 2
    import crissa_init as zi
    zi.configure(home=home)
    try:
        assert zi.face_prints("sam") == 2
        assert zi.face_view({"state": "live", "progress": pr})["hint"] == "Photo 2 of 2 saved"
    finally:
        zi.configure()
