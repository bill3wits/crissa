"""Durable User enter + auth-until-leave (user 2026-08-11 goal).

Drives real DoorInventory + PresenceService helpers — not reimplemented oracles.
"""
from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.door_watch import DoorInventory
from crissa_presence.service import PresenceService


@pytest.fixture(autouse=True)
def _user_is_user(monkeypatch):
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    monkeypatch.delenv("RAIL_USER_ALLOW_GHOST_LEAVE", raising=False)
    monkeypatch.setenv("RAIL_PRESENCE_MATCH_SIM", "0.60")


def test_ghost_recovery_does_not_drop_user():
    inv = DoorInventory()
    inv.apply_vl_delta(
        1, 0, source="gemma", stamp_names=["user"], now=10.0, entry_grace_sec=5.0
    )
    assert "user" in inv.present
    # Past ghost timer — User still protected
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=16.0)
    evs = inv.observe_door(
        [], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=16.0 + 1801.0
    )
    leaves = [e for e in evs if e.get("kind") == "leave" and e.get("name") == "user"]
    blocked = [e for e in evs if e.get("kind") == "leave_blocked"]
    assert not leaves, f"User must not ghost-leave: {leaves}"
    assert blocked or "user" in inv.present
    assert "user" in inv.present
    assert inv.headcount() >= 1


def test_unarmed_empty_door_does_not_drop_user():
    """Empty FOV without exit arm must not wipe User (no walk-out evidence)."""
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    # Never re-sight at door → never exit_armed
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=1.0, now=50.0)
    evs = inv.observe_door(
        [], 0, leave_confirm_sec=2.0, entry_grace_sec=1.0, now=50.0 + 1801.0
    )
    hard = [e for e in evs if e.get("kind") == "leave" and e.get("name") == "user"]
    assert not hard
    assert "user" in inv.present


def test_user_real_exit_armed_empty_does_not_drop_user():
    """AUTH-first: exit_armed + empty FOV alone must NOT leave sudo User.

    Approach-and-return (walk to door, sit) looked like leave under arm+empty.
    Strong leave = operator / VL / force (see may_drop_protected).
    """
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    inv.observe_door(["user"], 0, leave_confirm_sec=2.5, entry_grace_sec=1.0, now=100.0)
    assert "user" in inv.exit_armed
    inv.observe_door([], 0, leave_confirm_sec=2.5, entry_grace_sec=1.0, now=101.0)
    evs = inv.observe_door(
        [], 0, leave_confirm_sec=2.5, entry_grace_sec=1.0, now=104.0
    )
    hard = [
        e
        for e in evs
        if e.get("kind") == "leave"
        and e.get("name") == "user"
        and e.get("source") == "door_empty_after"
    ]
    assert not hard, f"arm+empty must not leave User: {evs}"
    assert "user" in inv.present
    assert inv.headcount() >= 1


def test_operator_leave_still_drops_user():
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    inv.force_leave(["user"], source="operator")
    assert "user" not in inv.present
    assert inv.headcount() == 0


def test_stranger_still_ghost_recovers():
    inv = DoorInventory()
    inv.apply_vl_delta(
        1, 0, source="gemma", stamp_names=["wanda"], now=10.0, entry_grace_sec=5.0
    )
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=16.0)
    evs = inv.observe_door(
        [], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=16.0 + 1801.0
    )
    assert any(
        e.get("kind") == "leave" and e.get("name") == "wanda" for e in evs
    ), "non-User still ghost-recovers"
    assert "wanda" not in inv.present


def test_reconcile_replace_keeps_user():
    inv = DoorInventory()
    inv.set_baseline(["user", "wanda"], 0)
    ev = inv.apply_reconcile(["wanda"], 0, mode="replace")
    assert "user" in inv.present, "replace must not drop protected User"
    assert "user" not in (ev.get("left") or [])


def test_reconcile_union_never_drops():
    inv = DoorInventory()
    inv.set_baseline(["user", "wanda"], 0)
    inv.apply_reconcile(["user"], 0, mode="union")
    assert "wanda" in inv.present and "user" in inv.present


def _svc(*, present=("user",), hold=None, session=None):
    svc = PresenceService.__new__(PresenceService)
    svc._face_auth_hold = dict(hold or {})
    svc._last_user_voice = {"match": False, "sim": None, "ts": 0.0}
    svc._user_session = dict(session) if session else None
    svc._id_certain = {}
    svc._id_sources = {}
    svc._auth_levels = {}
    svc._mode = "holding"
    svc.cycles = 0
    svc.last_cycle_at = 0.0
    svc._vl_writer_stats = {}
    svc._vl_writer_note = None
    svc._left_block_until = {}
    svc._pets = {}
    svc._auth_hold_path = None
    inv = DoorInventory()
    now = time.time()
    for p in present:
        inv.present[p] = now
    inv.baseline_set = True
    svc.inventory = inv
    from crissa_presence.schema import empty_room

    svc._state = empty_room(camera_open=True)
    svc._set = lambda st: setattr(svc, "_state", st)
    svc.get = lambda: getattr(svc, "_state", empty_room(camera_open=True))
    svc._save_inventory_disk = lambda force=False: None
    svc._sync_oracle_hand = lambda reason="": None
    svc._presence_from_inventory = PresenceService._presence_from_inventory.__get__(
        svc, PresenceService
    )
    return svc


def test_entry_face_mint_puts_user_on_sot():
    """Door face mints occupancy. AUTH is FaceFinder + FaceID (Law 1,
    2026-09-06) — the door still does not open the hold."""
    svc = _svc(present=())
    out = svc.apply_entry_face_mint(
        "user", sim=0.63, face_px=[90, 100], door_view=True, kind="enter"
    )
    assert out["ok"] and out["minted"]
    assert "user" in svc.inventory.present
    assert "user" not in svc._face_auth_hold
    assert out["headcount"] >= 1


def test_entry_face_mint_below_floor_no_mint():
    svc = _svc(present=())
    out = svc.apply_entry_face_mint(
        "user", sim=0.40, face_px=[90, 100], door_view=True, kind="enter"
    )
    assert not out["ok"]
    assert "user" not in svc.inventory.present


def test_auth_holds_with_face_sticky_until_leave():
    svc = _svc(
        present=("user",),
        hold={"user": {"sim": 0.7, "ts": time.time(), "source": "entry_best_face"}},
    )
    slot = SimpleNamespace(
        name="user",
        certain=True,
        auth={"level": "face", "authed": True, "sticky": True},
        sources={"face": True, "face_sim": 0.7, "face_sticky": True},
    )
    blk = svc._user_auth_from_slots([slot], lights_out=False)
    assert blk["user_auth"], blk
    assert blk["user_present"]
    assert blk["user_auth_reason"] in ("ok", "face_sticky", "session_held")


def test_auth_holds_without_inventory_when_sticky():
    """AUTH-first: empty roster + sticky still AUTH until strong leave."""
    svc = _svc(
        present=(),
        hold={"user": {"sim": 0.7, "ts": time.time(), "source": "door"}},
        session={
            "opened_ts": time.time() - 60,
            "last_strong_voice_ts": time.time() - 10,
            "last_strong_voice_sim": 0.6,
        },
    )
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["user_auth"], blk
    assert blk["user_face_sticky"]
    assert "user" in svc._face_auth_hold


def test_auth_clears_after_operator_leave():
    """Strong leave (operator) clears inventory; sticky clear via _note_left path."""
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    inv.force_leave(["user"], source="operator")
    assert "user" not in inv.present
    svc = _svc(present=(), hold={"user": {"sim": 0.7, "ts": time.time()}})
    svc.inventory = inv
    # AUTH-first: sticky alone still AUTH until _note_left clears it
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["user_auth"]
    # Simulate on_leave tombstone clear
    svc._note_left(["user"])
    blk2 = svc._user_auth_from_slots([], lights_out=False)
    assert not blk2["user_auth"] and blk2["user_auth_reason"] == "user_absent"


def test_match_user_zero_jpegs_does_not_clear_sticky():
    svc = _svc(
        present=("user",),
        hold={"user": {"sim": 0.72, "ts": time.time(), "source": "entry"}},
    )
    # No engine — returns 0 without clearing hold
    svc.engine = SimpleNamespace(
        ensure=lambda: None, face=None, enrolled={}
    )
    sim = svc._match_user_in_jpegs("user", [b""], source="test_empty")
    assert sim == 0.0
    assert "user" in svc._face_auth_hold


def test_match_user_empty_list_preserves_sticky():
    svc = _svc(
        present=("user",),
        hold={"user": {"sim": 0.8, "ts": time.time(), "source": "entry"}},
    )

    class _FakeFace:
        def detect(self, im):
            return []

        def embed(self, f):
            return None

    svc.engine = SimpleNamespace(
        ensure=lambda: None, face=_FakeFace(), enrolled={}
    )
    # valid jpeg header-ish but detect returns []
    import numpy as np
    import cv2

    jpg = cv2.imencode(".jpg", np.zeros((64, 64, 3), dtype=np.uint8))[1].tobytes()
    sim = svc._match_user_in_jpegs("user", [jpg], source="test_zero_face")
    assert sim == 0.0
    assert "user" in svc._face_auth_hold, "zero-face hunt must not clear sticky"
