"""A look ends at the door, and Paused keeps the camera there (the User 2026-10-09).

04:35-07:4x on 10-09 the camera sat on the User's seat while every readback said door: the look's go_home sent
return_door while its own lease was still held, bffc609 (09-06) refused every lease instead of "another owner's",
so 840f496's picture check and head-framing OFF never ran; and release_and_hold sent head-framing OFF only when
tracking was already off. Paused had no camera rule at all.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
sys.path.insert(0, os.path.dirname(__file__))

import crissa_presence.grabber as g  # noqa: E402
import crissa_presence.firmware_facelock as fl  # noqa: E402


# ---- B: the camera's return_door -------------------------------------------------

def _serve(monkeypatch):
    homes = []
    monkeypatch.setattr(g, "_finish_at_door_home", lambda **kw: homes.append(kw) or {"ok": True})
    monkeypatch.setattr(g, "resume_watchdogs", lambda **kw: {"ok": True})
    monkeypatch.setattr(g, "note_intentional_aim", lambda *a, **k: None)
    monkeypatch.setattr(g, "clear_aim_hold", lambda: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, homes


def _post(srv, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.server_port}/v1/return_door",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _hold(monkeypatch, lease_id="L1", owner="beam_look"):
    monkeypatch.setattr(g, "_lease", {"owner": owner, "lease_id": lease_id, "until": time.time() + 30})
    monkeypatch.setattr(g, "_hold_until", time.time() + 30)


def test_the_lease_holder_homes_its_own_camera(monkeypatch):
    _hold(monkeypatch)
    srv, homes = _serve(monkeypatch)
    try:
        out = _post(srv, {"force": True, "force_door": True, "lease_id": "L1"})
    finally:
        srv.shutdown()
    assert not out.get("skipped") and len(homes) == 1, "go_home's own return reaches the picture check"


def test_another_owners_lease_still_beats_force(monkeypatch):
    """bffc609's rule stands for the joystick: the door loop never yanks another owner's lease."""
    _hold(monkeypatch, owner="user_joystick")
    srv, homes = _serve(monkeypatch)
    try:
        bare = _post(srv, {"force": True})
        wrong = _post(srv, {"force": True, "lease_id": "L-other"})
    finally:
        srv.shutdown()
    assert bare.get("skipped") and wrong.get("skipped") and homes == []
    assert bare.get("reason") == "lease_active"


def test_the_own_lease_rule_is_one_switch_away(monkeypatch):
    monkeypatch.setenv("RAIL_DOOR_OWN_LEASE", "0")
    _hold(monkeypatch)
    srv, homes = _serve(monkeypatch)
    try:
        out = _post(srv, {"force": True, "lease_id": "L1"})
    finally:
        srv.shutdown()
    assert out.get("skipped") and homes == []


def test_go_home_sends_its_lease_with_the_return(monkeypatch):
    from crissa_presence import seat_shot

    sent = []

    class _RS:
        @staticmethod
        def _post(path, body, timeout=0):
            sent.append((path, dict(body)))
            return {"ok": True}

    seat_shot.go_home(_RS, "L7")
    ret = [b for p, b in sent if p == "/v1/return_door"]
    assert ret == [{"force": True, "force_door": True, "lease_id": "L7"}]


def test_a_live_release_turns_head_framing_off(monkeypatch):
    """840f496 sent frame_off only when track was already off; a look's real release has track on."""
    states = iter([{"mode": 1, "track_on": True}, {"mode": 0, "track_on": False}])
    monkeypatch.setattr(fl, "firmware_status", lambda device=None: next(states))
    monkeypatch.setattr(fl, "facelock_live", lambda st: bool(st.get("track_on")))
    monkeypatch.setattr(fl, "firmware_track", lambda on, device=None: True)
    offs = []
    monkeypatch.setattr(fl, "frame_off", lambda device=None: offs.append(1) or True)
    out = fl.release_and_hold("/dev/null")
    assert offs == [1] and out["frame_off"] is True


# ---- C: Paused keeps the camera home ---------------------------------------------

from test_visit_lamp import _svc  # noqa: E402


def _paused(svc, monkeypatch, on=True):
    monkeypatch.setattr(svc, "_ear_paused", lambda: on)


def test_paused_refuses_an_automatic_look(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _paused(svc, monkeypatch)
    rests = []
    monkeypatch.setattr(svc, "_beam_rest", lambda: rests.append(1))
    out = svc.set_f2f(True, reason="session_open", look=True, hour=6)
    assert out["skipped"] == "paused" and out["f2f"] is False and rests == []


def test_paused_ends_a_look_in_flight(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _paused(svc, monkeypatch)
    rests = []
    monkeypatch.setattr(svc, "_beam_rest", lambda: rests.append(1))
    svc._f2f = True
    assert svc._pause_home_tick() is True and rests == [1]


def test_not_paused_changes_nothing(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _paused(svc, monkeypatch, on=False)
    rests = []
    monkeypatch.setattr(svc, "_beam_rest", lambda: rests.append(1))
    svc._f2f = True
    assert svc._pause_home_tick() is False and rests == []


def test_secure_still_answers_first(tmp_path, monkeypatch):
    """Secure's own block (camera stays on the door, session remembered) runs before the Paused rule."""
    svc = _svc(tmp_path, monkeypatch)
    _paused(svc, monkeypatch)
    monkeypatch.setattr(svc, "_ff_at_beam_blocked", lambda: "secure")
    out = svc.set_f2f(True, reason="session_open", look=True, hour=6)
    assert out["skipped"] == "secure" and svc._visit_ff_saved["reason"] == "session_open"


def test_the_pause_rule_is_one_switch_away(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _paused(svc, monkeypatch)
    monkeypatch.setenv("RAIL_PAUSE_HOME", "0")
    assert svc._paused_holds_home() is False


def test_an_unreadable_ear_is_not_paused(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setenv("RAIL_EAR_HEALTH_URL", "http://127.0.0.1:9/health")
    assert svc._ear_paused() is False
