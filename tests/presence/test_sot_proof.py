"""SoT rev bumps only on material writes; Busy TTL fails to unknown."""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence import sot_proof
from crissa_presence.service import PresenceService
from crissa_presence.schema import RoomPresence


class _FakeCam:
    mode = "dir"
    last_error = None

    def grab(self):
        return None

    def grab_buffer(self, **kw):
        return []


def _svc(tmp_path, monkeypatch):
    monkeypatch.setattr(sot_proof, "REV_PATH", tmp_path / "sot_rev.json")
    monkeypatch.setattr(sot_proof, "AUTH_LOG", tmp_path / "auth.jsonl")
    monkeypatch.setattr("crissa_presence.service.BUSY_TTL_SEC", 15.0)
    return PresenceService(camera=_FakeCam())


def test_same_material_does_not_bump_rev(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    st = RoomPresence(headcount=1, user_auth=True, user_auth_reason="face_sticky")
    svc._set(st)
    r1 = svc.get().rev
    st2 = RoomPresence(headcount=1, user_auth=True, user_auth_reason="face_sticky")
    svc._set(st2)
    assert svc.get().rev == r1


def test_busy_on_bumps_rev_and_fills_writers_last(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "_busy_off_recount", lambda: {"ok": True, "via": "test"})
    r0 = int(getattr(svc.get(), "rev", 0) or 0)
    out = svc.set_room_busy(True, reason="linger")
    assert out["room_busy"] is True
    st = svc.get()
    assert st.rev > r0
    last = (st.writers or {}).get("last") or st.writer or {}
    assert last.get("who")
    assert st.user_auth is False
    log = (tmp_path / "auth.jsonl").read_text(encoding="utf-8")
    assert "room_busy" in log or "false" in log.lower()
    svc.set_room_busy(False, reason="empty_ref")
    assert svc.get().user_auth is False


def test_stale_busy_beat_expires_to_unknown(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "_busy_off_recount", lambda: {"ok": True, "via": "test"})
    svc.set_room_busy(True, reason="linger")
    svc._busy_beat_ts = time.time() - 20.0
    st = svc.get()
    assert st.room_busy is False
    assert st.count_trust == "unknown"
    assert st.to_dict().get("wall_count") == "unknown"
    assert st.user_auth is False


def test_schema_to_dict_includes_rev():
    d = RoomPresence().to_dict()
    assert "rev" in d
    assert d["rev"] == 0
