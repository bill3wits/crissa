"""Door watch yields when AUTH / tools need the camera.

user 2026-08-14: pause the oracle service, take the tool, turn it back on.
Restored 2026-08-21 for voice-register (desk pan 0 is factory home to the
watchdog — without pause, _ensure_door_aim yanks the gimbal back to the door).
"""
from __future__ import annotations

import inspect
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))


def test_watchdogs_skip_when_paused():
    from crissa_presence import grabber as g

    g.resume_watchdogs(force=True)
    g.pause_watchdogs("voice-register", 20, reason="unit", steal=True)
    assert g.watchdogs_paused()
    assert g._recover_from_factory_home() is None
    assert g._recover_rest_to_door() is None
    g.resume_watchdogs(force=True)
    assert not g.watchdogs_paused()


def test_pause_steals_lease_for_auth():
    from crissa_presence import grabber as g

    g.resume_watchdogs(force=True)
    got = g.acquire_lease("room_sweep", 30)
    assert got.get("ok")
    out = g.pause_watchdogs("auth", 15, reason="ensure", steal=True)
    assert out.get("stole_lease") == "room_sweep"
    assert g.lease_state() is None
    assert g.watchdogs_paused()
    g.resume_watchdogs(force=True)


def test_health_exposes_pause():
    from crissa_presence import grabber as g

    src = inspect.getsource(g.health)
    assert "pause_remaining_sec" in src
    assert '"paused"' in src or "paused" in src


def test_pause_ttl_covers_register():
    from crissa_presence import grabber as g

    g.resume_watchdogs(force=True)
    out = g.pause_watchdogs("voice-register", 180, reason="voice-register")
    assert out.get("ttl_sec") == 180
    rem = g._pause_until - time.time()
    assert rem > 170
    g.resume_watchdogs(force=True)


def test_http_pause_resume_routes_exist():
    from crissa_presence import grabber as g

    src = inspect.getsource(g.Handler.do_POST)
    assert '"/v1/pause"' in src
    assert '"/v1/resume"' in src
    home = src.split('if path in ("/return_door"')[1].split("if path in")[0]
    assert "resume_watchdogs(force=True)" in home


def test_ensure_door_aim_yields_to_lease_and_pause():
    from crissa_presence import service as svc_mod

    src = inspect.getsource(svc_mod.PresenceService._ensure_door_aim)
    assert 'g.get("paused")' in src
    assert 'g.get("tool_hold")' in src
    assert 'g.get("lease")' in src
