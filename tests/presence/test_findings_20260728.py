"""Regression guards for the 2026-07-28 afternoon findings (TRIAL-LOG 13:42,
13:50): A — /v1/vl_delta response label must tell the truth about whether the
count moved; C — an enter-witness resolved to an armed slot disarms the
pending door_empty_after confirm; assert_leave U-wipe goes through the
_drop_unnamed_key choke point (§1.4).
"""

import time

from crissa_presence.service import PresenceService
import pytest


# These guard the occupancy arithmetic, which the service publishes only with the occupancy mill on: RAIL_OCCUPANCY,
# default off since the rail's 00a232b ("Occupancy mill off", 2026-08-27; crissa_presence.service.occupancy_on). With
# it unset every named person is dropped from the published state and headcount reads 0, whatever the arithmetic did.
@pytest.fixture(autouse=True)
def _occupancy_mill_on(monkeypatch):
    monkeypatch.setenv("RAIL_OCCUPANCY", "1")


class _FakeCam:
    mode = "dir"
    last_error = None

    def grab(self):
        return None

    def grab_buffer(self, **kw):
        return []


def _svc():
    return PresenceService(camera=_FakeCam())


def _clean_env(monkeypatch):
    import os
    for k in list(os.environ):
        if k.startswith("RAIL_VL_MODE"):
            monkeypatch.delenv(k, raising=False)


def test_held_sub_answers_vl_leave_held(monkeypatch):
    """Finding A: multi-named board + subs_only −1 with no U → hold, and the
    RESPONSE kind must be vl_leave_held (not vl_delta) so auto_label R5 does
    not key false applies. Sole-named is no longer held (Finding B)."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    svc = _svc()
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["wanda"], force=True)
    inv = svc.inventory
    inv.entry_grace_until["user"] = 0.0
    inv.entry_grace_until["wanda"] = 0.0
    st = svc.apply_vl_delta(0, 1, source="qwen")
    assert st.headcount == 2, "multi-named board: no identity guess under subs_only"
    assert any(e.get("kind") == "vl_leave_held"
               for e in svc.inventory.events)
    assert (st.last_event or {}).get("kind") == "vl_leave_held", \
        f"held sub mislabeled: {(st.last_event or {}).get('kind')}"


def test_subs_only_sole_named_applies(monkeypatch):
    """Finding B (Stage-2 hinge): board = sole named user, VL −1 → drop user.
    Unambiguous sole ladder — not an identity guess."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    svc = _svc()
    inv = svc.inventory
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    inv.entry_grace_until["user"] = 0.0
    old = time.time() - 600
    inv.present["user"] = old
    inv.last_door_seen["user"] = old
    inv.recent_transits.clear()  # settled occupant: no fresh inward evidence
    st = svc.apply_vl_delta(0, 1, source="qwen")
    assert st.headcount == 0, "sole named must drain on VL leave"
    assert "user" not in inv.present
    assert (st.last_event or {}).get("kind") == "vl_delta"
    leaves = [e for e in inv.events if e.get("kind") == "leave"]
    assert leaves and (leaves[-1].get("leave_match") or {}).get(
        "method") == "subs_only_sole_named"


def test_sole_drain_fresh_entry_holds(monkeypatch):
    """Fresh-entry guard (OPT-IN: default off after 15:13:02 proved to be a
    REAL 13s quick-turnaround exit): with the env set, an anon −1 seconds
    after the sole occupant's witnessed entry HOLDS instead of draining."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    monkeypatch.setenv("RAIL_PRESENCE_SOLE_DRAIN_MIN_AGE_SEC", "30")
    svc = _svc()
    inv = svc.inventory
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    # entry seconds old: grace active AND fresh enter transit both present
    st = svc.apply_vl_delta(0, 1, source="qwen")
    assert st.headcount == 1, "just-entered sole occupant must not drain"
    assert (st.last_event or {}).get("kind") == "vl_leave_held"
    # aged out: same board 31s+ later drains fine (covered by
    # test_subs_only_sole_named_applies)


def test_sole_drain_guard_default_off(monkeypatch):
    """Default: guard disabled — a quick-turnaround exit (real pattern,
    15:13:02 traits-confirmed) must drain even seconds after entry."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    svc = _svc()
    inv = svc.inventory
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    inv.entry_grace_until["user"] = 0.0
    st = svc.apply_vl_delta(0, 1, source="qwen")
    assert st.headcount == 0


def test_subs_only_sole_named_env_off(monkeypatch):
    """Finding B gate: RAIL_PRESENCE_SUBS_SOLE_NAMED=0 restores pure hold."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    monkeypatch.setenv("RAIL_PRESENCE_SUBS_SOLE_NAMED", "0")
    svc = _svc()
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    st = svc.apply_vl_delta(0, 1, source="qwen")
    assert st.headcount == 1
    assert (st.last_event or {}).get("kind") == "vl_leave_held"


def test_subs_only_guest_named_drops_target(monkeypatch):
    """Finding B+: multi-named + leave_guest → drop that arrival-order slot.
    wanda-class exit while user stays (14:24:17 held → should apply)."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    svc = _svc()
    inv = svc.inventory
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["wanda"], force=True)
    inv.entry_grace_until["user"] = 0.0
    inv.entry_grace_until["wanda"] = 0.0
    # guest1 = first arrival = user; guest2 = wanda
    roster = inv.guest_roster()
    assert roster[0] == "user" and "wanda" in roster
    g_wanda = roster.index("wanda") + 1
    st = svc.apply_vl_delta(0, 1, source="qwen", leave_guest=g_wanda)
    assert st.headcount == 1
    assert "wanda" not in inv.present
    assert "user" in inv.present
    leaves = [e for e in inv.events if e.get("kind") == "leave"]
    assert leaves and (leaves[-1].get("leave_match") or {}).get(
        "method") == "subs_only_guest_named"
    assert (st.last_event or {}).get("kind") == "vl_delta"


def test_subs_only_anonymous_multi_still_holds(monkeypatch):
    """Anonymous multi-named −1 (no leave_guest) still holds."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    svc = _svc()
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["wanda"], force=True)
    st = svc.apply_vl_delta(0, 1, source="qwen")
    assert st.headcount == 2
    assert (st.last_event or {}).get("kind") == "vl_leave_held"


def test_applied_sub_still_answers_vl_delta(monkeypatch):
    """Finding A inverse: a −1 that really drops keeps kind=vl_delta."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "full")
    svc = _svc()
    inv = svc.inventory
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    # age the entry past face-veto / grace windows: settled occupant exits
    old = time.time() - 600
    inv.present["user"] = old
    inv.last_door_seen["user"] = old
    inv.entry_grace_until["user"] = 0.0
    st = svc.apply_vl_delta(0, 1, source="qwen", leave_person_id="user")
    assert st.headcount == 0
    assert (st.last_event or {}).get("kind") == "vl_delta"


def test_enter_witness_disarms_same_slot(monkeypatch):
    """Finding C: armed user + witnessed +1 resolving to user → disarm, so
    the 2.5s door_empty_after confirm cannot eat the return (13:48:02 race)."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    svc = _svc()
    inv = svc.inventory
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    now = time.time()
    inv.entry_grace_until["user"] = 0.0
    inv.exit_armed.add("user")
    inv.armed_at["user"] = now - 5
    inv.leave_since["user"] = now - 2
    st = svc.apply_vl_delta(1, 0, source="qwen")  # subs_only add → witness
    assert (st.last_event or {}).get("kind") == "vl_witnessed"
    assert st.headcount == 1, "witness stays arithmetic-free"
    assert "user" not in inv.exit_armed, "inward witness must disarm"
    assert "user" not in inv.leave_since
    assert any(e.get("kind") == "exit_arm_disarmed"
               and e.get("reason") == "inward_witness"
               for e in inv.events)


def test_enter_witness_disarm_env_off(monkeypatch):
    """Finding C gate: RAIL_PRESENCE_INWARD_DISARM=0 restores old behavior."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    monkeypatch.setenv("RAIL_PRESENCE_INWARD_DISARM", "0")
    svc = _svc()
    inv = svc.inventory
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    inv.entry_grace_until["user"] = 0.0
    inv.exit_armed.add("user")
    inv.armed_at["user"] = time.time() - 5
    svc.apply_vl_delta(1, 0, source="qwen")
    assert "user" in inv.exit_armed


def test_enter_witness_leaves_other_arms(monkeypatch):
    """Finding C scope: B entering must NOT disarm A's genuine exit."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    svc = _svc()
    inv = svc.inventory
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    svc.apply_vl_delta(
        1, 0, source="op", stamp_names=["wanda"], force=True)
    inv.entry_grace_until["user"] = 0.0
    inv.entry_grace_until["wanda"] = 0.0
    inv.exit_armed.add("user")
    inv.armed_at["user"] = time.time() - 5
    # witness resolves to wanda (stamps ladder), not the armed user
    svc.apply_vl_delta(1, 0, source="qwen", stamp_names=["wanda"])
    assert "user" in inv.exit_armed, "unrelated arm must survive"


def test_assert_leave_uwipe_choke_point():
    """§1.4: operator U-wipe removes keys via _drop_unnamed_key — invariant
    unnamed == len(unnamed_keys) holds, per-key leave rows emitted (the raw
    unnamed=0 shortcut caused the 13:18 invariant_breach + missing audit)."""
    svc = _svc()
    inv = svc.inventory
    svc.apply_vl_delta(2, 0, source="op", force=True)  # two U slots
    assert inv.unnamed == 2 and len(inv.unnamed_keys) == 2
    keys = list(inv.unnamed_keys)
    st = svc.assert_leave(["U1"], source="operator_poster_zero")
    assert st.headcount == 0
    assert inv.unnamed == 0 and not inv.unnamed_keys, "no orphan keys"
    dropped = [e for e in inv.events
               if e.get("kind") == "leave" and e.get("unnamed_key") in keys]
    assert len(dropped) == 2, "one audit row per wiped slot"
    for k in keys:
        assert k not in inv.unnamed_meta and k not in inv.traits_live


def test_armed_confirm_defers_on_inward_witness():
    """15:30:56 group-return eat: an armed confirm whose firing tick carries
    a fresh inward WITNESS defers — the door provably saw a body come in.
    A later tick without an inward witness still fires (clock not reset)."""
    svc = _svc()
    inv = svc.inventory
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["wanda"], force=True)
    now = time.time()
    for n in ("user", "wanda"):
        inv.entry_grace_until[n] = 0.0
        inv.seen_at_door.add(n)
        inv.exit_armed.add(n)
        inv.armed_at[n] = now - 60
        inv.leave_since[n] = now - 55  # clock long past confirm threshold
    inv.note_witness("enter", ts=now - 4, source="qwen")  # the return, seen
    evs = inv.observe_door([], door_unnamed=0, now=now)
    assert inv.headcount() == 2, "no drop on a tick with an inward witness"
    assert not any(e.get("source") == "door_empty_after" for e in evs)
    # witness ages out of the transit window: confirm fires (defer, not veto)
    evs2 = inv.observe_door([], door_unnamed=0, now=now + 13)
    assert any(e.get("source") == "door_empty_after" for e in evs2), \
        "stale witness no longer defers; clock was never reset"
