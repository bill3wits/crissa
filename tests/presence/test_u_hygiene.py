"""U-slot hygiene (presence-perfect P1-P2): pending crossings hold no
headcount, absorption is gated by the §2.1 same-body proof, ghost clocks
pause (never reset), and every U removal goes through the one choke point.

All clocks injectable (now=) — observation-driven, never wall clock.
"""

import json

from crissa_presence import door_watch
import os
from contextlib import contextmanager

from crissa_presence.door_watch import DoorInventory


@contextmanager
def GATE_OFF():
    """Disable the uncorroborated-expiry cancel gate (Grok break-pass D)
    for tests that target the raw mint-at-expiry mechanism itself."""
    os.environ["RAIL_PRESENCE_PENDING_MINT_REQUIRES_CORROBORATION"] = "0"
    try:
        yield
    finally:
        del os.environ["RAIL_PRESENCE_PENDING_MINT_REQUIRES_CORROBORATION"]



def _inv_ok(inv):
    """Invariant sweep: count == keys and every __uN has provenance."""
    assert inv.unnamed == len(inv.unnamed_keys)
    for uk in inv.unnamed_keys:
        assert uk in inv.unnamed_meta, f"{uk} missing unnamed_meta"


def test_echo_no_mint():
    """Echo shape (user matched frame 1, unmatched body frame 2 at
    max_bodies_same_frame=1): the §1.3 formula yields door_unnamed=0; and
    even a raw door_unnamed=1 next tick merges into user's same transit
    (true per-frame concurrency 1) — no U, no pending, headcount 1."""
    # the service formula: max(0, min(max_unnamed, max_bodies - len(best)))
    assert max(0, min(1, 1 - 1)) == 0
    inv = DoorInventory()
    inv.observe_door(["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                     now=100.0, door_bodies_max=1)
    assert inv.headcount() == 1
    inv.observe_door([], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                     now=102.0, door_bodies_max=1)
    assert inv.headcount() == 1, "echo rise must merge, not mint"
    assert inv.unnamed == 0
    assert not inv.pending_crossings
    _inv_ok(inv)


def test_solo_resight_past_transit_window_no_u():
    """user alone long after enter-transit window; unmatched door body must
    re-sight him (explained_by_roster), not mint U1 → headcount 2."""
    inv = DoorInventory()
    inv.observe_door(["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                     now=100.0, door_bodies_max=1, transit_window_sec=12.0)
    assert inv.headcount() == 1
    # well past 12s transit window — old merge path no longer covers
    inv.observe_door([], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                     now=200.0, door_bodies_max=1, transit_window_sec=12.0)
    assert inv.headcount() == 1
    assert inv.unnamed == 0
    assert not inv.pending_crossings
    # still solo after "expiry" tick
    inv.observe_door([], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                     now=220.0, door_bodies_max=1, transit_window_sec=12.0)
    assert inv.headcount() == 1
    assert inv.unnamed == 0
    _inv_ok(inv)


def test_genuine_pair_mints():
    """One frame user + unknown (max_bodies=2) → pending → expiry mint →
    headcount 2; U carries grace + provenance, mint_ts backdated."""
    inv = DoorInventory()
    inv.observe_door(["user"], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                     now=100.0, door_bodies_max=2)
    assert inv.headcount() == 1, "pendings hold zero headcount"
    assert len(inv.pending_crossings) == 1
    # window closes uncontradicted → mint
    evs = inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                           now=113.0, door_bodies_max=0)
    assert inv.headcount() == 2
    assert not inv.pending_crossings
    uk = inv.unnamed_keys[-1]
    meta = inv.unnamed_meta[uk]
    assert meta["mint_ts"] == 100.0, "mint_ts backdated to crossing ts"
    assert meta["true_concurrent"] == 2
    assert inv.entry_grace_until[uk] == 100.0 + 45.0, "grace anchored at crossing"
    assert any(e.get("kind") == "enter" and e.get("unnamed_key") == uk
               for e in evs)
    _inv_ok(inv)


def test_outbound_cancel():
    """Armed non-sudo guest exits same tick as a rise → crossing_cancelled
    {outbound_resolved}; headcount 0; no U ever existed; no pending.

    AUTH-first: sudo User (user) blocks door_empty_after leave — use a
    non-protected person for the real leave+cancel path (see leave_blocked).
    """
    inv = DoorInventory()
    who = "wanda"
    inv.observe_door([who], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=10.0, door_bodies_max=1)
    inv.observe_door([who], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=20.0, door_bodies_max=1)   # past grace → armed
    assert who in inv.exit_armed
    inv.observe_door([], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=21.0, door_bodies_max=0)   # empty → leave clock
    # same tick: exit confirms AND door_live shows a 1-body rise (his back)
    evs = inv.observe_door([], 1, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                           now=24.0, door_bodies_max=1)
    kinds = [e.get("kind") for e in evs]
    assert "leave" in kinds
    cancel = next(e for e in evs if e.get("kind") == "crossing_cancelled")
    assert cancel["reason"] == "outbound_resolved"
    assert inv.headcount() == 0
    assert inv.unnamed == 0 and not inv.unnamed_keys
    assert not inv.pending_crossings, "cancel never creates a pending"
    _inv_ok(inv)


def test_user_door_empty_after_leave_blocked():
    """AUTH-first: arm+empty must NOT leave sudo User (approach-and-return)."""
    inv = DoorInventory()
    inv.observe_door(["user"], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=10.0, door_bodies_max=1)
    inv.observe_door(["user"], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=20.0, door_bodies_max=1)
    assert "user" in inv.exit_armed
    inv.observe_door([], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=21.0, door_bodies_max=0)
    evs = inv.observe_door([], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                           now=24.0, door_bodies_max=0)
    kinds = [e.get("kind") for e in evs]
    assert "leave" not in kinds
    assert "leave_blocked" in kinds or "user" in inv.present
    assert "user" in inv.present
    _inv_ok(inv)


def test_witness_out_cancel(monkeypatch):
    """tc=1 pending + outbound witness cancels (flag ON).

    AUTH-first empty-room FOV mints immediately, so a classic empty-room
    pending no longer forms. Inject a tc=1 pending (the only shape that
    settle() cancels on outbound_witness) to drive the shipped path.
    """
    monkeypatch.setenv("RAIL_PRESENCE_WITNESS_CANCEL", "1")
    inv = DoorInventory()
    inv.pending_crossings.append({
        "ts": 100.0, "expires": 112.0, "true_concurrent": 1,
        "witness_out": 0, "witness_in": 0, "grace_sec": 45.0,
    })
    inv.note_witness("leave", ts=105.0, source="qwen")
    assert not inv.pending_crossings, "unanimous outbound witness cancels"
    assert inv.headcount() == 0, "cancel decrements nothing (nothing counted)"
    kinds = [e.get("kind") for e in inv.events]
    assert "crossing_cancelled" in kinds
    ev = next(e for e in inv.events if e.get("kind") == "crossing_cancelled")
    assert ev["reason"] == "outbound_witness"

    # flag OFF: uncorroborated_expiry cancels only when room is non-empty
    # (empty room forces mint at expiry under AUTH-first walk-in law).
    monkeypatch.setenv("RAIL_PRESENCE_WITNESS_CANCEL", "0")
    inv2 = DoorInventory()
    inv2.observe_door(["wanda"], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                      now=90.0, door_bodies_max=1)
    inv2.pending_crossings.append({
        "ts": 100.0, "expires": 112.0, "true_concurrent": 1,
        "witness_out": 0, "witness_in": 0, "grace_sec": 45.0,
    })
    inv2.note_witness("leave", ts=105.0, source="qwen")
    # may cancel immediately via explained_by_roster if tc<=room; if still open:
    if inv2.pending_crossings:
        inv2.observe_door(["wanda"], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                          now=113.0, door_bodies_max=1)
    assert inv2.headcount() == 1, "settled occupant stays; no extra mint"
    assert any(e.get("kind") == "crossing_cancelled" for e in inv2.events)
    # empty-room FOV body: AUTH-first immediate mint
    inv3 = DoorInventory()
    inv3.observe_door([], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                      now=100.0, door_bodies_max=1)
    assert inv3.headcount() == 1
    assert inv3.unnamed_keys
    _inv_ok(inv)
    _inv_ok(inv2)
    _inv_ok(inv3)


def test_empty_room_door_immediate_mint():
    """AUTH-first: empty inventory + FOV body mints immediately (walk-in)."""
    inv = DoorInventory()
    evs = inv.observe_door([], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                           now=100.0, door_bodies_max=1)
    assert inv.headcount() == 1
    assert not inv.pending_crossings
    assert inv.unnamed_keys
    meta = inv.unnamed_meta[inv.unnamed_keys[-1]]
    assert "empty_room_door_immediate" in (meta.get("corroborations") or [])
    assert any(e.get("kind") == "enter" for e in evs)
    _inv_ok(inv)


def test_witness_in_early_mint():
    """Same-frame pair → pending; ENTER witness mints early with ['vl']."""
    inv = DoorInventory()
    inv.observe_door(["wanda"], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                     now=100.0, door_bodies_max=2)
    assert inv.headcount() == 1
    assert len(inv.pending_crossings) == 1
    inv.note_witness("enter", ts=104.0, source="qwen")
    assert inv.headcount() == 2, "enter corroboration mints early"
    assert not inv.pending_crossings
    uk = inv.unnamed_keys[-1]
    assert inv.unnamed_meta[uk]["corroborations"] == ["vl"]
    assert inv.unnamed_meta[uk]["mint_ts"] == 100.0
    _inv_ok(inv)


def test_absorb_same_transit():
    """Rise-minted U (in grace) + face enter of user → u_absorbed merge:
    headcount 1, U's stills/traits now user's."""
    inv = DoorInventory()
    with GATE_OFF():   # target: absorb mechanics; use raw expiry mint
        inv.observe_door([], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                         now=100.0, door_bodies_max=1)
        inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                         now=113.0, door_bodies_max=0)  # expiry mint
    assert inv.headcount() == 1
    uk = inv.unnamed_keys[-1]
    inv.set_traits(uk, {"shirt": "white"}, source="test")
    inv.enter_still[uk] = {"paths": ["/tmp/x.jpg"], "count": 1}
    evs = inv.observe_door(["user"], 0, leave_confirm_sec=2.0,
                           entry_grace_sec=45.0, now=118.0, door_bodies_max=1)
    assert inv.headcount() == 1, "same body merged, not stacked"
    assert inv.names() == ["user"]
    assert inv.unnamed == 0 and not inv.unnamed_keys
    assert any(e.get("kind") == "u_absorbed" for e in evs)
    assert inv.traits_live.get("user", {}).get("shirt") == "white"
    assert inv.enter_still.get("user", {}).get("count") == 1
    _inv_ok(inv)


def test_absorb_old_guest_protected():
    """A 20-minute-old settled guest U is NEVER eaten by a face re-enter:
    headcount 2; the guest keeps traits/stills/arrival_seq and roster slot."""
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="qwen", now=100.0,
                       enter_traits={"shirt": "green"})
    uk = inv.unnamed_keys[-1]
    inv.enter_still[uk] = {"paths": ["/tmp/g.jpg"], "count": 1}
    seq = inv.arrival_seq[uk]
    evs = inv.observe_door(["user"], 0, leave_confirm_sec=2.0,
                           entry_grace_sec=45.0, now=100.0 + 1250.0,
                           door_bodies_max=1)
    assert inv.headcount() == 2, "settled guest survives"
    assert uk in inv.unnamed_keys
    assert not any(e.get("kind") == "u_absorbed" for e in evs)
    assert inv.traits_live.get(uk, {}).get("shirt") == "green"
    assert inv.enter_still.get(uk, {}).get("count") == 1
    assert inv.arrival_seq[uk] == seq
    assert inv.guest_roster()[0] == uk, "roster order unchanged"
    _inv_ok(inv)


def test_absorb_distinct_body_protected():
    """A U corroborated as a second body ('vl_second_body') is un-absorbable
    even inside the transit window."""
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="qwen", now=100.0)
    uk = inv.unnamed_keys[-1]
    inv.unnamed_meta[uk]["corroborations"].append("vl_second_body")
    inv.observe_door(["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                     now=105.0, door_bodies_max=2)
    assert inv.headcount() == 2
    assert uk in inv.unnamed_keys
    _inv_ok(inv)


def test_absorb_missing_meta_protected():
    """Legacy U without unnamed_meta ⇒ NOT absorbable (fail toward
    overcount, which the ghost-clock pause now genuinely bounds)."""
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="qwen", now=100.0)
    uk = inv.unnamed_keys[-1]
    del inv.unnamed_meta[uk]
    assert inv.absorbable_u(101.0) is None


def test_ghost_clock_pauses():
    """Settled phantom U with an occupant walking through the FOV: busy
    ticks PAUSE the ghost clock (never reset it); the drop fires on an
    empty observation past the horizon. The old pop-on-nonempty reset made
    the phantom immortal — leave_since must survive busy ticks."""
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="qwen", now=0.0, entry_grace_sec=5.0)
    uk = inv.unnamed_keys[-1]
    # empty tick past grace starts the clock
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                     now=10.0, ghost_recovery_sec=1800.0)
    assert inv.leave_since.get(uk) == 10.0
    # user walks through the FOV — old code reset the clock here
    inv.observe_door(["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                     now=900.0, ghost_recovery_sec=1800.0)
    assert inv.leave_since.get(uk) == 10.0, "busy FOV pauses, never resets"
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                     now=1700.0, ghost_recovery_sec=1800.0)
    assert uk in inv.unnamed_keys, "horizon not reached yet"
    evs = inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                           now=1811.0, ghost_recovery_sec=1800.0)
    assert uk not in inv.unnamed_keys, "true 1800s backstop fires"
    assert inv.unnamed == 0
    assert any(e.get("source") == "ghost_recovery" for e in evs)
    _inv_ok(inv)


def test_settled_u_immortal_to_witness():
    """A settled U + witnessed leave with no pending → doubt only;
    headcount unchanged (law 4: witnesses never remove)."""
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="qwen", now=100.0, entry_grace_sec=45.0)
    uk = inv.unnamed_keys[-1]
    inv.note_witness("leave", ts=1000.0, source="qwen")
    assert inv.headcount() == 1
    assert uk in inv.unnamed_keys
    assert uk in inv.doubt_since, "doubt raised (arms ghost recovery)"
    assert any(e.get("kind") == "doubt_raised" for e in inv.events)
    _inv_ok(inv)


def test_pending_freezes_on_stall():
    """Observation-driven clock: a pending with no further observations
    never expires and never mints — wall time advancing alone is nothing.

    Empty-room FOV body mints immediately; use same-frame pair for pending.
    """
    inv = DoorInventory()
    inv.observe_door(["wanda"], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                     now=100.0, door_bodies_max=2)
    assert len(inv.pending_crossings) == 1
    assert inv.headcount() == 1
    # (no observe calls — stalled grabber) nothing changed
    assert len(inv.pending_crossings) == 1
    assert inv.headcount() == 1
    # a pre-expiry observation keeps it pending
    inv.observe_door(["wanda"], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                     now=105.0, door_bodies_max=2)
    assert len(inv.pending_crossings) == 1, "open pending covers the sighting"
    assert inv.headcount() == 1
    _inv_ok(inv)


def test_invariant_after_every_scenario():
    """Mixed flow: mint, merge, absorb, drain, ghost — the one-object
    invariant holds at every step and no breach event is ever emitted."""
    inv = DoorInventory()
    steps = [
        lambda: inv.apply_vl_delta(1, 0, source="qwen", now=10.0),
        lambda: inv.observe_door(["user"], 0, leave_confirm_sec=2.0,
                                 entry_grace_sec=45.0, now=12.0,
                                 door_bodies_max=1),
        lambda: inv.apply_vl_delta(1, 0, source="gemma", now=40.0),
        lambda: inv.apply_vl_delta(0, 1, source="qwen", now=60.0),
        lambda: inv.observe_door([], 1, leave_confirm_sec=2.0,
                                 entry_grace_sec=45.0, now=80.0,
                                 door_bodies_max=1),
        lambda: inv.observe_door([], 0, leave_confirm_sec=2.0,
                                 entry_grace_sec=45.0, now=95.0,
                                 door_bodies_max=0),
    ]
    for step in steps:
        step()
        _inv_ok(inv)
    assert not any(e.get("kind") == "invariant_breach" for e in inv.events)


def test_drop_helper_clears_all_maps():
    """_drop_unnamed_key clears keys/count/traits/stills/grace/arrival/
    leave_since/doubt/meta/station and emits exactly one event."""
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="qwen", now=10.0,
                       enter_traits={"shirt": "red"})
    uk = inv.unnamed_keys[-1]
    inv.enter_still[uk] = {"paths": [], "count": 0}
    inv.leave_since[uk] = 11.0
    inv.doubt_since[uk] = 11.0
    inv.station_guess[uk] = 8
    n_ev = len(inv.events)
    ev = inv._drop_unnamed_key(uk, now=20.0, source="test", reason="unit")
    assert len(inv.events) == n_ev + 1
    assert ev["unnamed_key"] == uk and ev["unnamed_delta"] == -1
    assert inv.unnamed == 0 and uk not in inv.unnamed_keys
    for d in (inv.traits_live, inv.enter_still, inv.entry_grace_until,
              inv.leave_since, inv.arrival_seq, inv.doubt_since,
              inv.unnamed_meta, inv.station_guess):
        assert uk not in d
    _inv_ok(inv)


def test_mint_events_mirrored(monkeypatch, tmp_path):
    """u_mint / u_absorbed / crossing_cancelled / ghost_recovery rows land
    in the (tmp-path) mirror files; pytest default never writes production."""
    occ = tmp_path / "occupancy_history.jsonl"
    shadow = tmp_path / "shadow_history.jsonl"
    monkeypatch.setattr(door_watch, "_IN_PYTEST", False)
    monkeypatch.setattr(door_watch, "OCCUPANCY_HISTORY", occ)
    monkeypatch.setattr(door_watch, "SHADOW_HISTORY", shadow)
    inv = DoorInventory()
    # mint (pending expiry, gate off) → u_mint + enter rows
    with GATE_OFF():
        inv.observe_door([], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                         now=100.0, door_bodies_max=1)
        inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                         now=113.0, door_bodies_max=0)
    # absorb (U still inside its crossing-anchored grace)
    inv.observe_door(["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0,
                     now=115.0, door_bodies_max=1)
    # cancel: inject tc=1 pending (empty-room FOV no longer creates pending)
    inv.force_leave(["user"], source="operator")
    monkeypatch.setenv("RAIL_PRESENCE_WITNESS_CANCEL", "1")
    inv.pending_crossings.append({
        "ts": 130.0, "expires": 142.0, "true_concurrent": 1,
        "witness_out": 0, "witness_in": 0, "grace_sec": 45.0,
    })
    pe = {
        "kind": "pending_crossing", "ts": 130.0, "expires": 142.0,
        "true_concurrent": 1, "source": "test_inject",
    }
    inv.events.append(pe)
    inv._mirror_event(pe, history=occ)
    inv.note_witness("leave", ts=131.0, source="qwen")
    # ghost recovery of a settled U
    inv.apply_vl_delta(1, 0, source="qwen", now=200.0, entry_grace_sec=5.0)
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                     now=210.0, ghost_recovery_sec=1800.0)
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                     now=2011.0, ghost_recovery_sec=1800.0)
    rows = [json.loads(l) for l in occ.read_text().splitlines()]
    kinds = {r.get("kind") for r in rows}
    assert "u_mint" in kinds
    assert "u_absorbed" in kinds
    assert "crossing_cancelled" in kinds
    assert "pending_crossing" in kinds
    ghost_rows = [r for r in rows if r.get("source") == "ghost_recovery"]
    assert ghost_rows, "U ghost_recovery drop persisted"
    # pytest default (_IN_PYTEST True) writes nowhere
    monkeypatch.setattr(door_watch, "_IN_PYTEST", True)
    before = occ.read_text()
    inv2 = DoorInventory()
    inv2.apply_vl_delta(1, 0, source="qwen", now=1.0)
    assert occ.read_text() == before


def test_unmatched_live_does_not_census_while_user_present():
    """Guest/unknown face while user is counted is not occupancy (2026-08-13)."""
    inv = DoorInventory()
    inv.observe_door(
        ["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0,
        now=100.0, door_bodies_max=1,
    )
    assert inv.headcount() == 1
    evs = inv.observe_door(
        [], 1, leave_confirm_sec=2.0, entry_grace_sec=0.0,
        now=200.0, door_bodies_max=1,
        unmatched_live=1, unmatched_sim_to_present=0.18,
    )
    assert inv.headcount() == 1
    assert inv.unnamed == 0
    assert not any(e.get("kind") == "enter" for e in evs)
    _inv_ok(inv)


def test_unmatched_live_high_sim_resights():
    """Weak user face must not mint U1."""
    inv = DoorInventory()
    inv.observe_door(
        ["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0,
        now=100.0, door_bodies_max=1,
    )
    evs = inv.observe_door(
        [], 1, leave_confirm_sec=2.0, entry_grace_sec=0.0,
        now=200.0, door_bodies_max=1,
        unmatched_live=1, unmatched_sim_to_present=0.48,
    )
    assert inv.headcount() == 1
    assert inv.unnamed == 0
    assert not any(e.get("kind") == "enter" for e in evs)
    _inv_ok(inv)


def test_sequential_guest_faces_are_not_occupancy():
    """Two guest faces one-at-a-time: visitor_today, headcount stays User."""
    inv = DoorInventory()
    inv.observe_door(
        ["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0,
        now=100.0, door_bodies_max=1,
    )
    inv.observe_door(
        ["xena"], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0,
        now=200.0, door_bodies_max=1,
    )
    inv.observe_door(
        [], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0,
        now=206.0, door_bodies_max=0,
    )
    inv.observe_door(
        ["val"], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0,
        now=214.0, door_bodies_max=1,
    )
    assert inv.headcount() == 1
    assert inv.unnamed == 0
    assert "xena" not in inv.names()
    assert "val" not in inv.names()
    assert "xena" in inv.visitor_today
    assert "val" in inv.visitor_today
    _inv_ok(inv)


def test_same_fov_unmatched_does_not_mint():
    """Standing unmatched body while User is counted: still one body."""
    inv = DoorInventory()
    inv.observe_door(
        ["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0,
        now=100.0, door_bodies_max=1,
    )
    inv.observe_door(
        [], 1, leave_confirm_sec=2.0, entry_grace_sec=0.0,
        now=200.0, door_bodies_max=1,
        unmatched_live=1, unmatched_sim_to_present=0.11,
    )
    inv.observe_door(
        [], 1, leave_confirm_sec=2.0, entry_grace_sec=0.0,
        now=201.0, door_bodies_max=1,
        unmatched_live=1, unmatched_sim_to_present=0.11,
    )
    assert inv.headcount() == 1
    assert inv.unnamed == 0
    _inv_ok(inv)
