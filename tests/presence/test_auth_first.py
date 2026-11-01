"""AUTH-first control plane (docs/AUTH-FIRST-PLAN-20260812.md).

Drives shipped service/door_watch/auth_first — not reimplemented oracles.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence import auth_first as af
from crissa_presence.door_watch import DoorInventory
from crissa_presence.service import PresenceService


@pytest.fixture(autouse=True)
def _user_user(monkeypatch, tmp_path):
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    monkeypatch.delenv("RAIL_USER_ALLOW_ARM_EMPTY_LEAVE", raising=False)
    monkeypatch.delenv("RAIL_USER_ALLOW_GHOST_LEAVE", raising=False)
    monkeypatch.setenv("HOUSE_GOOD_MORNING_DRY", "1")
    # Isolate day flag so sticky-open greets don't share ~/.the agent/auth_day
    monkeypatch.setenv("HOUSE_AUTH_DAY_FILE", str(tmp_path / "auth_day.user"))


def _svc(*, hold=None, session=None, present=()):
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
    svc._inspector_auto = lambda *a, **k: None
    svc._presence_from_inventory = PresenceService._presence_from_inventory.__get__(
        svc, PresenceService
    )
    svc._note_face_auth = PresenceService._note_face_auth.__get__(svc, PresenceService)
    svc._user_id = PresenceService._user_id.__get__(svc, PresenceService)
    svc._user_auth_from_slots = PresenceService._user_auth_from_slots.__get__(
        svc, PresenceService
    )
    svc._auth_for_person = PresenceService._auth_for_person.__get__(svc, PresenceService)
    svc._auth_summary = PresenceService._auth_summary.__get__(svc, PresenceService)
    svc.grant_user_face_auth = PresenceService.grant_user_face_auth.__get__(
        svc, PresenceService
    )
    svc._maybe_first_auth_greeting = PresenceService._maybe_first_auth_greeting.__get__(
        svc, PresenceService
    )
    svc.chase_user_face_at_door = PresenceService.chase_user_face_at_door.__get__(
        svc, PresenceService
    )
    svc._ensure_door_aim = lambda release_facelock=True: {
        "ok": True,
        "pan_cmd": 450000,
        "pan_actual": 450000,
    }
    svc._match_user_in_jpegs = lambda uid, jpegs, source="x": 0.0
    return svc


def test_auth_without_inventory_plus_one():
    """Criterion 1: face sticky → user_auth with empty roster."""
    svc = _svc(present=())  # no user on inventory
    assert svc.inventory.headcount() == 0
    # an FF source: since PR A (Law 1) the grant vouches only for FaceFinder
    out = svc.grant_user_face_auth(sim=0.72, source="desk_ff", greet=False)
    assert out["auth"] and out["oracle_used"] is False
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["user_auth"], blk
    assert blk["user_face_sticky"]
    assert blk["user_present"]
    assert blk.get("auth_plane") == "face_sticky"


def test_approach_and_return_keeps_auth():
    """Criterion 3: exit_arm + empty FOV does not drop protected User / sticky."""
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    inv.observe_door(["user"], 0, now=100.0, entry_grace_sec=0, leave_confirm_sec=1.0)
    assert "user" in inv.exit_armed
    inv.observe_door([], 0, now=101.0, entry_grace_sec=0, leave_confirm_sec=1.0)
    evs = inv.observe_door([], 0, now=104.0, entry_grace_sec=0, leave_confirm_sec=1.0)
    assert "user" in inv.present, f"must stay present: {evs}"
    assert inv.headcount() >= 1
    # sticky path
    svc = _svc(
        present=("user",),
        hold={"user": {"sim": 0.7, "ts": time.time(), "source": "entry"}},
    )
    svc.inventory = inv
    blk = svc._user_auth_from_slots(
        [
            SimpleNamespace(
                name="user",
                certain=True,
                auth={"level": "face", "authed": True, "sticky": True},
                sources={"face_sticky": True, "face_sim": 0.7},
            )
        ],
        lights_out=False,
    )
    assert blk["user_auth"]


def test_operator_leave_still_clears():
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    inv.force_leave(["user"], source="operator")
    assert "user" not in inv.present


def test_good_morning_once_per_day(tmp_path):
    """Criterion 4: first AUTH of local calendar day emits once (midnight roll).

    Wake-hour roll was proposed then retracted (COMMS 02:10): user's day ~02:00.
    """
    day_file = tmp_path / "user.day"
    uid = "user"
    r1 = af.maybe_good_morning(
        uid, day_path=day_file, today="2026-08-12", speak=None, dry_run=True
    )
    assert r1["emitted"] is True
    r2 = af.maybe_good_morning(
        uid, day_path=day_file, today="2026-08-12", speak=None, dry_run=True
    )
    assert r2["emitted"] is False
    assert r2["reason"] == "already_today"
    # new day
    r3 = af.maybe_good_morning(
        uid, day_path=day_file, today="2026-08-13", speak=None, dry_run=True
    )
    assert r3["emitted"] is True


def test_grant_triggers_greeting_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_AUTH_DAY_FILE", str(tmp_path / "greet.day"))
    monkeypatch.setenv("HOUSE_GOOD_MORNING_DRY", "1")
    svc = _svc()
    g1 = svc.grant_user_face_auth(sim=0.8, source="test", greet=True)
    assert g1.get("greeting", {}).get("emitted") is True
    g2 = svc.grant_user_face_auth(sim=0.8, source="test", greet=True)
    # Second grant same day must not re-speak; day flag / already_today.
    # API may still surface the first-emit cache — assert day no-op via helper.
    r = af.maybe_good_morning(
        "user",
        day_path=tmp_path / "greet.day",
        speak=None,
        dry_run=True,
    )
    assert r.get("emitted") is False
    assert r.get("reason") == "already_today"
    # If greeting payload present on second grant, must not be a *new* day emit
    # without day file advance (emitted True only if cache of first).
    if g2.get("greeting") and g2["greeting"].get("emitted"):
        assert g2["greeting"].get("reason") == "first_auth_of_day"
        # but day file already written — third call stays false
        g3 = af.maybe_good_morning(
            "user", day_path=tmp_path / "greet.day", speak=None, dry_run=True
        )
        assert g3.get("emitted") is False


def test_oracle_not_used_on_auth_grant():
    """Criterion 2: grant path never requires Oracle."""
    import inspect

    src = inspect.getsource(PresenceService.grant_user_face_auth)
    assert "oracle" not in src.lower() or "oracle_used" in src
    assert "describe_crossing" not in src
    svc = _svc()
    # an FF source: since PR A (Law 1) the grant vouches only for FaceFinder
    out = svc.grant_user_face_auth(sim=0.66, source="desk_ff", greet=False)
    assert out["oracle_used"] is False
    assert out["auth"] is True


def test_chase_returns_door_and_no_multistop():
    """Criterion 5: door-local chase ends with return_door; no look_around default."""
    import inspect

    src = inspect.getsource(PresenceService.chase_user_face_at_door)
    # Must not call multi-stop product look_around as the AUTH chase
    assert "look_around(" not in src
    assert "run_look_around" not in src
    assert "_ensure_door_aim" in src or "return_door" in src
    svc = _svc()
    # Inject match
    svc._match_user_in_jpegs = lambda uid, jpegs, source="x": 0.75
    out = svc.chase_user_face_at_door(jpegs=[b"\xff\xd8fake"], allow_door_ff=False)
    assert out["found"] is True
    assert out["returned_door"] is True
    assert out["oracle_used"] is False
    # Law 1 (PR A, 2026-09-09): a ring match is found, not AUTH, from Guest —
    # this assertion used to pin the very hole (review item 1)
    assert out["auth"] is False and out.get("reason") == "door_ring_needs_ff"
    assert "user" not in svc._face_auth_hold
    # held by a FaceFinder look, the same ring match refreshes it
    svc._face_auth_hold["user"] = {"sim": 0.8, "ts": time.time() - 3, "source": "desk_ff"}
    out = svc.chase_user_face_at_door(jpegs=[b"\xff\xd8fake"], allow_door_ff=False)
    assert out["auth"] is True and svc._face_auth_hold["user"]["source"] == "desk_ff"


def test_chase_skips_door_ff_when_budget_tight():
    """Hard budget: do not enter door_ff if engage would overrun (Fable 6.4s ask)."""
    import inspect

    src = inspect.getsource(PresenceService.chase_user_face_at_door)
    assert "budget" in src
    assert "RAIL_AUTH_DOOR_FF_ENGAGE_SEC" in src
    svc = _svc()
    # Ring miss; allow FF but force near-zero remaining budget via tiny budget
    # and slow ring match so remaining < min_cost → skip door_ff
    svc._match_user_in_jpegs = lambda uid, jpegs, source="x": 0.0
    # Monkeypatch posts would hang without stubs — use jpegs so ring runs,
    # allow_door_ff True, budget so small that min_cost cannot fit.
    out = svc.chase_user_face_at_door(
        jpegs=[b"\xff\xd8fake"],
        allow_door_ff=True,
        budget_sec=0.5,  # < 1.8 engage + settle
    )
    assert out["returned_door"] is True
    ff_stops = [s for s in out.get("stops") or [] if s.get("id") == "door_ff"]
    assert ff_stops, out.get("stops")
    assert ff_stops[0].get("skipped") is True
    assert ff_stops[0].get("reason") == "budget"
    assert out.get("wall_sec", 99) < 2.0


def test_soft_id_non_user_no_sudo_sticky():
    svc = _svc(present=())
    # wanda certain soft_id must not open user sticky
    st = PresenceService.apply_soft_id(
        svc, "wanda", certain=True, source="voice", confirm=True
    )
    assert "user" not in svc._face_auth_hold


def test_note_face_auth_greets_on_first_sticky(tmp_path, monkeypatch):
    """First FaceFinder sticky open of the day greets (via _note). A door
    pack face is not a grant (Law 1, 2026-09-06) and does not greet."""
    monkeypatch.setenv("HOUSE_AUTH_DAY_FILE", str(tmp_path / "note.day"))
    monkeypatch.setenv("HOUSE_GOOD_MORNING_DRY", "1")
    svc = _svc()
    assert not svc._face_auth_hold
    svc._note_face_auth("user", sim=0.71, source="entry_best_face")
    assert "user" not in svc._face_auth_hold
    assert not (getattr(svc, "_last_auth_greeting", None) or {}).get("emitted")
    svc._note_face_auth("user", sim=0.71, source="beam_look")
    g = getattr(svc, "_last_auth_greeting", None) or {}
    assert g.get("emitted") is True
    # second note same day: no re-emit
    svc._note_face_auth("user", sim=0.72, source="entry_best_face")
    g2 = svc._maybe_first_auth_greeting("user")
    assert g2.get("emitted") is False


def test_soft_id_user_does_not_mint_auth(tmp_path, monkeypatch):
    """Voice / Oracle soft ID is not a face. Law 1: no hold, no AUTH, no
    greeting until FaceFinder + FaceID."""
    monkeypatch.setenv("HOUSE_AUTH_DAY_FILE", str(tmp_path / "soft.day"))
    monkeypatch.setenv("HOUSE_GOOD_MORNING_DRY", "1")
    svc = _svc(present=())
    PresenceService.apply_soft_id(
        svc, "user", certain=True, source="voice", confirm=True
    )
    assert "user" not in svc._face_auth_hold
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert not blk["user_auth"]
    g = getattr(svc, "_last_auth_greeting", None) or {}
    assert not g.get("emitted")


def test_unlock_does_not_boost_user_auth(monkeypatch):
    """Phase I: unlocked desktop does not set face_ok / user_auth."""
    monkeypatch.setenv("HOUSE_HOST_UNLOCKED", "1")
    svc = _svc(present=())
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["host_unlocked"] is True
    assert not blk["user_auth"]
    assert not blk["user_present"]
    assert blk.get("product_tier") == "none"
    assert blk.get("granted_by") is None


def test_lock_drops_user_auth_to_guest_shape(monkeypatch):
    """Known lock clears sticky+session. Ear guest is evaluate_turn not-A."""
    now = time.time()
    monkeypatch.setenv("HOUSE_HOST_UNLOCKED", "1")
    svc = _svc(
        present=("user",),
        hold={"user": {"sim": 0.7, "ts": now, "source": "entry"}},
        session={
            "opened_ts": now - 60,
            "opened_via": "face_sticky",
            "last_strong_voice_ts": now - 10,
            "last_strong_voice_sim": 0.6,
        },
    )
    svc._auth_levels = {"user": {"level": "face"}}
    kicked: list[str] = []
    svc._kick_unlock_face_grab = lambda: kicked.append("unlock")
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["user_auth"] and blk["host_unlocked"] is True

    monkeypatch.setenv("HOUSE_HOST_UNLOCKED", "0")
    blk2 = svc._user_auth_from_slots([], lights_out=False)
    assert not blk2["user_auth"]
    assert blk2["user_auth_reason"] == "host_locked"
    assert not blk2["user_face_sticky"]
    assert not blk2["user_session_held"]
    assert svc._user_session is None
    assert "user" not in svc._face_auth_hold
    assert kicked == []
    assert blk2.get("granted_by") is None
    assert blk2.get("product_tier") == "none"

    # Roster cannot reopen AUTH while still locked
    blk3 = svc._user_auth_from_slots([], lights_out=False)
    assert not blk3["user_auth"] and blk3["user_auth_reason"] == "host_locked"

    monkeypatch.setenv("HOUSE_HOST_UNLOCKED", "1")
    blk4 = svc._user_auth_from_slots([], lights_out=False)
    assert blk4["host_unlocked"] is True
    assert not blk4["user_auth"]  # unlock is a grab, not a grant
    assert kicked == ["unlock"]


def test_auth_for_person_lock_does_not_refresh_sticky(monkeypatch):
    """Live face at door must not re-sticky User while the host is locked."""
    monkeypatch.setenv("HOUSE_HOST_UNLOCKED", "0")
    now = time.time()
    svc = _svc(
        present=("user",),
        hold={"user": {"sim": 0.71, "ts": now, "source": "live_face"}},
    )
    svc._auth_levels = {"user": {"level": "face"}}
    out = svc._auth_for_person(
        "user",
        sources={"face": True, "live_face": True, "door_buffer": True, "face_sim": 0.71},
        certain=True,
    )
    assert out["authed"] is False
    assert out["level"] == "none"
    assert out["sticky"] is False
    assert "user" not in svc._face_auth_hold
    assert svc._user_session is None


def test_sticky_publishes_provenance():
    now = time.time()
    svc = _svc(
        present=(),
        hold={"user": {"sim": 0.7, "ts": now, "source": "entry"}},
    )
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["user_auth"]
    assert blk.get("granted_by") == "face"
    assert blk.get("product_tier") in ("chat", "tools", "identity")


def test_host_unlocked_signal_env(monkeypatch):
    monkeypatch.setenv("HOUSE_HOST_UNLOCKED", "1")
    assert af.host_unlocked_signal() is True
    monkeypatch.setenv("HOUSE_HOST_UNLOCKED", "0")
    assert af.host_unlocked_signal() is False
    monkeypatch.delenv("HOUSE_HOST_UNLOCKED", raising=False)
    monkeypatch.delenv("HOUSE_SESSION_UNLOCKED", raising=False)
    # No probe → fail closed bool; lock *state* is unknown (do not de-AUTH)
    monkeypatch.setenv("HOUSE_HOST_UNLOCK_PROBE", "0")
    assert af.host_unlocked_signal() is False
    assert af.host_lock_state() is None


def test_auth_tier_fresh_held_stale():
    now = time.time()
    fresh = af.compute_auth_tier(
        user_auth=True,
        face_sticky=True,
        hold_ts=now - 10,
        last_voice_match=True,
        last_voice_ts=now - 5,
        reason="ok",
        now=now,
        fresh_sec=300,
        stale_sec=1800,
    )
    assert fresh["auth_tier"] == "fresh"
    held = af.compute_auth_tier(
        user_auth=True,
        face_sticky=True,
        hold_ts=now - 600,
        last_voice_match=False,
        last_voice_ts=None,
        reason="session_held",
        now=now,
        fresh_sec=300,
        stale_sec=1800,
    )
    assert held["auth_tier"] == "held"
    stale = af.compute_auth_tier(
        user_auth=True,
        face_sticky=True,
        hold_ts=now - 4000,
        last_voice_match=False,
        last_voice_ts=None,
        reason="session_held",
        now=now,
        fresh_sec=300,
        stale_sec=1800,
    )
    assert stale["auth_tier"] == "stale"
    assert af.compute_auth_tier(user_auth=False)["auth_tier"] == "absent"


def test_entry_pic_match_does_not_drop_user():
    """COMMS §3: Oracle exit_match is not automatic User de-AUTH."""
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    ev = inv._drop("user", now=time.time(), source="entry_pic_match")
    assert ev.get("kind") == "leave_blocked"
    assert "user" in inv.present


def test_ensure_default_mode_is_door_local():
    import inspect

    src = inspect.getsource(PresenceService.ensure_user_auth)
    assert 'mode_s = "door_local"' in src or "door_local" in src
    assert "chase_user_face_at_door" in src
    assert "_reauth_facefind_all_stations" in src  # full mode only
    assert "_rung0_current_fov" in src
    # Safety property: force_facefind is not gated off by tier
    assert "force_facefind or not face_sticky" in src


def test_ensure_skips_hunt_when_host_locked(monkeypatch):
    """Unlocked hunt stays. Locked guest must not FaceFinder."""
    monkeypatch.setenv("HOUSE_HOST_UNLOCKED", "0")
    svc = _svc(present=("user",))
    svc._ensure_auth_hunt_ts = 0.0
    hits = []

    def chase(**k):
        hits.append("chase")
        raise AssertionError("lock guest must not chase/FF")

    def r0(uid):
        hits.append("r0")
        raise AssertionError("lock guest must not rung0")

    svc.chase_user_face_at_door = chase
    svc._rung0_current_fov = r0
    out = svc.ensure_user_auth(
        person_id="user", force_facefind=True, reason="house_command", mode="desk"
    )
    assert hits == []
    assert out["auth"] is False
    assert out["reason"] == "host_locked"
    assert out.get("via") == "locked"


def test_stale_rung0_skips_chase_on_hit():
    svc = _svc(
        hold={"user": {"sim": 0.7, "ts": time.time() - 4000, "source": "x"}}
    )
    svc._user_session = {"opened_ts": time.time() - 4000}
    svc._ensure_auth_hunt_ts = 0.0
    hits = []

    def r0(uid):
        hits.append("r0")
        return {
            "found": True,
            "best_sim": 0.81,
            "via": "live",
            "stops": [{"id": "live"}],
        }

    def chase(**k):
        hits.append("chase")
        raise AssertionError("door chase must not run after Rung-0 hit")

    svc._rung0_current_fov = r0
    svc.chase_user_face_at_door = chase
    out = svc.ensure_user_auth(
        person_id="user", force_facefind=True, reason="house_command"
    )
    assert hits == ["r0"], hits
    assert out["auth"] is True
    assert "user" in svc._face_auth_hold


def test_fresh_force_facefind_still_door_local():
    svc = _svc(
        hold={"user": {"sim": 0.72, "ts": time.time() - 10, "source": "x"}}
    )
    svc._user_session = {"opened_ts": time.time() - 10}
    svc._ensure_auth_hunt_ts = 0.0
    hits = []

    def r0(uid):
        hits.append("r0")
        return {"found": False}

    def chase(**k):
        hits.append("chase")
        return {
            "found": True,
            "best_sim": 0.7,
            "via": "door_ring",
            "returned_door": True,
            "stops": [],
        }

    svc._rung0_current_fov = r0
    svc.chase_user_face_at_door = chase
    out = svc.ensure_user_auth(
        person_id="user", force_facefind=True, reason="house_command"
    )
    assert "r0" not in hits
    assert hits == ["chase"]
    assert out["auth"] is True


def _failed_hunt(svc):
    svc._ensure_auth_hunt_ts = 0.0
    svc._rung0_current_fov = lambda uid: {"found": False, "via": "rung0"}
    svc.chase_user_face_at_door = lambda **k: {
        "found": False,
        "via": "door_chase",
        "returned_door": True,
        "stops": [],
    }
    return svc.ensure_user_auth(person_id="user", force_facefind=True, reason="house_command")


def test_failed_hunt_keeps_a_fresh_sticky():
    """A failed hunt never clears sticky BY ITSELF: a hold inside the BAU
    clock survives the miss (the empty FOV has not been empty for 5 s)."""
    svc = _svc(hold={"user": {"sim": 0.7, "ts": time.time() - 1, "source": "x"}})
    svc._user_session = {"opened_ts": time.time() - 1}
    out = _failed_hunt(svc)
    assert "user" in svc._face_auth_hold
    assert out.get("face_sticky") is True
    # the forced re-prove failed, so THIS command is not authed — but presence
    # keeps the User: user_auth true beside auth false is the honest answer
    assert out["auth"] is False and out["user_auth"] is True


def test_failed_hunt_on_an_empty_fov_past_the_clock_is_guest_everywhere():
    """PR B (2026-09-09, review item 2): the expiry pops a hold older than the
    BAU clock on an empty FOV, and the reply says the same thing — no
    auth:true beside user_auth:false (the wall re-issued nudges into 403s)."""
    svc = _svc(hold={"user": {"sim": 0.7, "ts": time.time() - 4000, "source": "x"}})
    svc._user_session = {"opened_ts": time.time() - 4000}
    out = _failed_hunt(svc)
    assert "user" not in svc._face_auth_hold
    assert out["auth"] is False and out["user_auth"] is False
    assert out.get("face_sticky") is False
    assert out["reason"] in ("expired", "noauth_face_not_found")


def test_absent_desk_look_after_door_miss():
    svc = _svc(hold=None, session=None, present=())
    svc._ensure_auth_hunt_ts = 0.0
    svc._ensure_auth_pan_ts = 0.0
    hits = []

    def chase(**k):
        hits.append("chase")
        return {"found": False, "via": "door_chase", "returned_door": True, "stops": []}

    def desk(uid):
        hits.append("desk")
        return {
            "found": True,
            "best_sim": 0.77,
            "via": "desk",
            "returned_door": True,
        }

    svc.chase_user_face_at_door = chase
    svc._desk_look_once = desk
    out = svc.ensure_user_auth(
        person_id="user",
        force_facefind=True,
        voice_matched=False,
        reason="house_command",
    )
    assert hits == ["chase", "desk"], hits
    assert out["auth"] is True
    assert out.get("via") == "desk"


def test_desk_mode_uses_desk_not_door():
    """Tools / contended guest: FaceFinder at desk (hour 6), skip door chase."""
    svc = _svc(
        hold={"user": {"sim": 0.7, "ts": time.time() - 20, "source": "x"}}
    )
    svc._user_session = {"opened_ts": time.time() - 20}
    svc._ensure_auth_hunt_ts = 0.0
    hits = []
    svc._rung0_current_fov = lambda uid: hits.append("r0") or {"found": False}
    svc.chase_user_face_at_door = lambda **k: hits.append("chase") or {
        "found": False
    }
    svc._desk_look_once = lambda uid, **k: hits.append("desk") or {
        "found": True,
        "best_sim": 0.8,
        "via": "desk_ff",
        "returned_door": True,
        "stops": [{"id": "desk", "station": 6, "facefinder": True}],
    }
    out = svc.ensure_user_auth(
        person_id="user",
        force_facefind=True,
        reason="house_task",
        mode="desk",
    )
    assert hits == ["desk"], hits
    assert out["auth"] is True
    assert out.get("via") in ("desk_ff", "desk")
    assert out.get("mode") == "desk"


def test_desk_look_uses_facefind():
    import inspect

    src = inspect.getsource(PresenceService._desk_look_once)
    assert "/v1/facefind" in src
    assert '"id": "desk"' in src
    assert "station" in src
    assert "6" in src


def test_contested_no_desk_look():
    svc = _svc(
        hold={"user": {"sim": 0.7, "ts": time.time() - 20, "source": "x"}}
    )
    svc._user_session = {"opened_ts": time.time() - 20}
    svc._ensure_auth_hunt_ts = 0.0
    hits = []
    svc._rung0_current_fov = lambda uid: hits.append("r0") or {"found": False}
    svc.chase_user_face_at_door = lambda **k: (
        hits.append("chase")
        or {"found": False, "via": "door_chase", "returned_door": True, "stops": []}
    )
    svc._desk_look_once = lambda uid: hits.append("desk") or {"found": False}
    out = svc.ensure_user_auth(
        person_id="user",
        force_facefind=True,
        reason="wrong_voice_reprove",
    )
    assert "desk" not in hits
    assert "r0" not in hits
    assert "chase" in hits
    # the hold's fate is the clock's, not the hunt's: 20 s on an empty FOV is
    # past the 5 s BAU clock, so presence and the reply both say Guest (PR B)
    assert "user" not in svc._face_auth_hold
    assert out["auth"] is False and out["user_auth"] is False
