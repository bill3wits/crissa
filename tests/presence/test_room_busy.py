"""Busy-on drops AUTH; mill silent; AUTH stays off until recheck."""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.service import PresenceService


class _FakeCam:
    mode = "dir"
    last_error = None

    def grab(self):
        return None

    def grab_buffer(self, **kw):
        return []


def test_busy_on_drops_auth_and_stays_off_without_recheck(monkeypatch):
    svc = PresenceService(camera=_FakeCam())
    monkeypatch.setattr(svc, "_busy_off_recount", lambda: {"ok": True, "via": "test"})
    svc._note_face_auth("user", sim=0.72, source="beam_look")
    assert "user" in svc._face_auth_hold
    on = svc.set_room_busy(True, reason="linger")
    assert on["room_busy"] is True
    assert on["auth"] is False
    assert "user" not in svc._face_auth_hold
    assert svc.get().user_auth is False
    assert svc.get().user_auth_reason == "room_busy"
    assert svc.get().room_busy is True
    # Speech look may re-open AUTH. Linger is not a veto.
    svc._note_face_auth("user", sim=0.80, source="beam_look")
    assert "user" in svc._face_auth_hold
    off = svc.set_room_busy(False, reason="empty_ref")
    assert off["room_busy"] is False
    assert "user" in svc._face_auth_hold
    assert svc._auth_blocked_until_recheck is False


def test_mill_silent_while_busy():
    src = inspect.getsource(PresenceService.run_door_once)
    assert 'getattr(self, "_room_busy", False)' in src
    assert "mill silent" in src
    assert "observe_door" in src


def test_ensure_auth_defers_hunt_while_busy(monkeypatch):
    svc = PresenceService(camera=_FakeCam())
    monkeypatch.setattr(svc, "_busy_off_recount", lambda: {"ok": True, "via": "test"})
    monkeypatch.setattr(svc, "_auth_refused_lights_out", lambda: False)
    monkeypatch.setattr(svc, "_auth_hunt_blocked_locked", lambda: False)
    svc.set_room_busy(True, reason="linger")
    out = svc.ensure_user_auth(reason="command")
    assert out["auth"] is False
    assert out["reason"] == "room_busy"
    assert out["via"] == "busy_deferred"


def test_linger_does_not_arm_auth_scan(monkeypatch):
    svc = PresenceService(camera=_FakeCam())
    scans = []
    desks = []
    monkeypatch.setattr(svc, "_busy_auth_scan", lambda: scans.append("scan") or {"ok": True, "via": "scan"})
    monkeypatch.setattr(svc, "_busy_off_recount", lambda: desks.append("desk") or {"ok": True, "via": "desk"})
    svc.set_room_busy(True, reason="linger")
    assert svc._busy_auth_scan_armed is False
    svc.set_room_busy(False, reason="empty_ref")
    assert scans == []
    assert desks == ["desk"]


def test_queue_flood_arms_one_scan_even_after_linger(monkeypatch):
    """Linger and flood both set Busy. Linger does not scan. One scan on off."""
    svc = PresenceService(camera=_FakeCam())
    scans = []
    desks = []
    monkeypatch.setattr(svc, "_busy_auth_scan", lambda: scans.append("scan") or {"ok": True, "via": "scan"})
    monkeypatch.setattr(svc, "_busy_off_recount", lambda: desks.append("desk") or {"ok": True, "via": "desk"})
    svc.set_room_busy(True, reason="linger")
    svc.set_room_busy(True, reason="queue_flood")
    assert svc._busy_auth_scan_armed is True
    svc.set_room_busy(True, reason="queue_flood")
    svc.set_room_busy(False, reason="empty_ref")
    assert scans == ["scan"]
    assert desks == []
    svc.set_room_busy(False, reason="empty_ref")
    assert scans == ["scan"], "Busy flag already off — no second scan"


def test_desk_snap_defers_when_door_pending(monkeypatch):
    svc = PresenceService(camera=_FakeCam())
    monkeypatch.setattr(svc, "_door_work_pending", lambda: True)
    monkeypatch.setattr(svc.inventory, "headcount", lambda: 1)
    svc._oracle_visits = {}
    out = svc._busy_off_recount()
    assert out.get("via") == "door_pending"


def test_fanout_does_not_post_busy_on_linger():
    import inspect as ins

    from crissa_presence import door_fanout as df

    src = ins.getsource(df._promote) + ins.getsource(df.poll_once)
    assert "_ensure_busy(True, \"linger\"" not in src
    assert "_ensure_busy(True, \"queue_flood\")" not in src
