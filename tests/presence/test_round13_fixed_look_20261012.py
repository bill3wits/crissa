"""Round 13 (the User 2026-10-12, .101 round 12): a session look on a FIXED camera never finishes (there is no beam to
aim, so _f2f_scan_ready stays False), and every turn read "a look is in flight". A block the oracle's fixed look wrote
is published with no pending look; a moving camera's look still pends. Checkpoint docs/checkpoints/round13-ear-20261012/."""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
sys.path.insert(0, os.path.dirname(__file__))

from crissa_presence import fixed_cam  # noqa: E402
from test_round12_fixed_face_20261012 import ELGATO, _svc, clean  # noqa: E402,F401  (clean is the fixture)


def test_a_fixed_look_is_not_pending_during_a_session_look(monkeypatch):
    svc = _svc(monkeypatch, ELGATO)
    assert svc._fixed_cam() is True
    fixed_cam.note_look(name="user", sim=0.677, faces=1, owner="user")
    svc._f2f, svc._f2f_scan_ready = True, False          # the wake opened a session look; no beam will ever finish it
    h = svc.faceid_hud()
    assert h["source"] == "fixed_look" and h["fixed"] is True and h["f2f"] is True
    assert h["pending_look"] is False


def test_the_kill_and_a_block_that_is_not_a_fixed_look(monkeypatch):
    svc = _svc(monkeypatch, ELGATO)
    fixed_cam.note_look(name="user", sim=0.677, faces=1, owner="user")
    svc._f2f, svc._f2f_scan_ready = True, False
    monkeypatch.setenv("CRISSA_FIXED_LOOK_NOT_PENDING", "0")
    assert svc.faceid_hud()["pending_look"] is True                          # the old reading
    monkeypatch.delenv("CRISSA_FIXED_LOOK_NOT_PENDING")
    fixed_cam._LOOK.clear()
    svc._faceid_hud = {"ok": True, "ts": time.time(), "source": "beam_look", "faces": [], "n_faces": 0}
    monkeypatch.setattr(svc, "_fixed_cam", lambda: False)
    assert svc.faceid_hud()["pending_look"] is True                          # a moving camera still waits
