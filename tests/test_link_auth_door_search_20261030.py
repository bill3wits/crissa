"""The kit's door watch sees a door face again (the User 2026-10-30, his walk of 1.0.2: "Secure mode on with auth but
failed to turn off on face detected").

Measured on the house (docs/checkpoints/link-auth-20261030): a door face on this camera is median 106 px (2,383 door
glances in the house's face log); the house's detector found his real face pasted on a real door frame down to 33 px;
the kit's landmarker, called plainly, found none under ~120 px, and 0 of the 10 door faces (27-99 px) the house's
detector found in an hour of the kit's own frames - so every door glance read faces=0 and the Secure tripwire, which
is byte-equal to the house's, never had a face to fire on. The engine's own crop search reaches 60 px, the door's
naming bar. The two door paths ask for it through face_engine.detect_door; a look at the seat does not.
Kill CRISSA_DOOR_FACE_SEARCH=0.
"""
from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def P(monkeypatch, tmp_path):
    """The presence package from modules/, fresh, with its state under tmp."""
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CRISSA_DOOR_FACE_SEARCH", raising=False)
    monkeypatch.delenv("CRISSA_FACE_CROP_RESCUE", raising=False)
    held = {k: v for k, v in sys.modules.items() if k == "crissa_presence" or k.startswith("crissa_presence.")}
    for k in held:
        monkeypatch.delitem(sys.modules, k)
    sys.path.insert(0, str(ROOT / "modules" / "presence"))
    yield importlib.import_module("crissa_presence.face_engine")
    for k in [k for k in sys.modules if k == "crissa_presence" or k.startswith("crissa_presence.")]:
        del sys.modules[k]
    sys.modules.update(held)


class _Blind:
    """An engine like the kit's landmarker on a door still: the plain look finds nothing, the crop search finds the face."""

    def __init__(self):
        self.calls = []
        self.face = SimpleNamespace(bbox=np.array([600.0, 200.0, 680.0, 300.0]), det_score=1.0, kps=None)

    def detect(self, im, rescue=False):
        self.calls.append(rescue)
        return [self.face] if rescue else []


class _Old:
    """An engine without the search (a stand-in): detect takes no rescue."""

    def __init__(self):
        self.calls = 0

    def detect(self, im):
        self.calls += 1
        return []


FRAME = np.zeros((720, 1280, 3), dtype=np.uint8)


def test_the_door_look_asks_for_the_crop_search(P):
    eng = _Blind()
    assert P.door_search_on() is True
    assert P.detect_door(eng, FRAME) == [eng.face]
    assert eng.calls == [True]


def test_the_kill_is_the_plain_door_look_of_102(P, monkeypatch):
    monkeypatch.setenv("CRISSA_DOOR_FACE_SEARCH", "0")
    eng = _Blind()
    assert P.door_search_on() is False
    assert P.detect_door(eng, FRAME) == []
    assert eng.calls == [False]


def test_an_engine_without_the_search_is_asked_plainly(P):
    eng = _Old()
    assert P.detect_door(eng, FRAME) == []
    assert eng.calls == 1
    assert P.detect_door(None, FRAME) == [] and P.detect_door(eng, None) == []


def _svc(SV, eng):
    svc = object.__new__(SV.PresenceService)
    svc.engine = SimpleNamespace(ensure=lambda: None, face=eng, enrolled={})
    svc._user_id = lambda: "sam"
    svc._conv_auth_profile_min_sim = lambda: 0.40
    svc.note_faceid_hud = lambda **k: None
    svc._door_background_split = lambda *a, **k: None
    return svc


def _jpeg():
    import cv2

    ok, buf = cv2.imencode(".jpg", np.full((64, 64, 3), 90, dtype=np.uint8))
    assert ok
    return buf.tobytes()


def test_the_door_glance_searches_and_a_seat_look_does_not(P):
    SV = importlib.import_module("crissa_presence.service")
    eng = _Blind()
    svc = _svc(SV, eng)
    out = svc._identify_all_in_jpegs([_jpeg()], source="door_glance")
    assert eng.calls == [True] and out["n_faces"] == 1          # the door: searched, the face counted
    eng.calls.clear()
    out = svc._identify_all_in_jpegs([_jpeg()], source="visor")
    assert eng.calls == [False] and out["n_faces"] == 0         # the seat: the plain look, as before
    for src in SV.PresenceService.DOOR_SOURCES:
        eng.calls.clear()
        svc._identify_all_in_jpegs([_jpeg()], source=src)
        assert eng.calls == [True], src


def test_the_door_buffer_loop_searches_too(P):
    SV = importlib.import_module("crissa_presence.service")
    src = inspect.getsource(SV.PresenceService.run_door_once)
    assert "_detect_door(self.engine.face, fr.bgr)" in src
    assert "self.engine.face.detect(fr.bgr)" not in src
    assert src.count("from .face_engine import detect_door as _detect_door") == 1
