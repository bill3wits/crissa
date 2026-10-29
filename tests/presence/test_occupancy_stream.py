"""DESIGN-OCCUPANCY-STREAM regression tests (user law 2026-07-27).

Occupancy is a fold over evidence events: pushes/pops by evidence only,
ghost_recovery (1800s) is the single time-based remover.
"""

from crissa_presence.door_watch import DoorInventory


def test_vl_unnamed_survives_empty_doorway():
    """The old door_live_shrink annihilated a VL-entered body on the next
    empty tick (~0.5s, measured). Door-FOV absence is not room absence."""
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="qwen", now=10.0, entry_grace_sec=5.0)
    assert inv.headcount() == 1
    assert inv.unnamed == 1 and len(inv.unnamed_keys) == 1
    for t in (10.5, 11.0, 20.0, 120.0, 300.0):
        inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=t)
    assert inv.headcount() == 1, "empty doorway must not delete a VL body"
    # ghost recovery (the only time-based remover) eventually ages it out
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=300.0 + 1801.0)
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=300.0 + 3700.0)
    assert inv.headcount() == 0


def test_face_enter_then_vl_plus_one_merges_with_traits():
    """One transit, two writers: face counts user, VL +1 with traits 3s later
    must NOT mint a second body — and the traits must land (refresh_meta)."""
    inv = DoorInventory()
    inv.observe_door(["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0, now=100.0)
    assert inv.headcount() == 1
    inv.apply_vl_delta(
        1, 0, source="qwen", now=103.0,
        enter_traits={"shirt": "white", "hair": "dark"},
    )
    assert inv.headcount() == 1, "same transit must not double-count"
    assert inv.traits_live.get("user", {}).get("shirt") == "white", \
        "deduped +1 must still land its traits"


def test_two_simultaneous_entrants_do_not_collapse():
    """bodies>1 guard: two genuine concurrent entrants stay two bodies."""
    inv = DoorInventory()
    inv.observe_door(["user", "wanda"], 0, leave_confirm_sec=2.0,
                     entry_grace_sec=45.0, now=100.0)
    assert inv.headcount() == 2
    # a VL +1 seconds later merges (door_concurrent>1 transits are not
    # merge candidates, so it mints a third only if no eligible transit) —
    # the guard means it must NOT collapse the two named into one.
    inv.apply_vl_delta(1, 0, source="qwen", now=104.0,
                       enter_traits={"shirt": "green"})
    assert inv.headcount() >= 2


def test_unmatched_minus_one_holds_on_all_certain_roster():
    """A hallucinated −1 can never delete a face-certain seated person.

    (Sole occupant is different — contract: sole → that person, no match
    game. HOLD guards the multi-person all-certain roster.)"""
    inv = DoorInventory()
    inv.observe_door(["user", "wanda"], 0, leave_confirm_sec=2.0,
                     entry_grace_sec=5.0, now=10.0)
    evs = inv.apply_vl_delta(
        0, 1, source="cosmos", now=200.0,
        protected_names={"user", "wanda"},
    )
    assert inv.headcount() == 2, "HOLD: certain-only roster keeps its slots"
    kinds = [e.get("kind") for e in evs]
    assert "vl_leave_held" in kinds
    assert inv.doubt_since, "LRU person raised to doubt (arms ghost recovery)"


def test_unmatched_minus_one_evicts_lru_unconfirmed_first():
    inv = DoorInventory()
    inv.observe_door(["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=10.0)
    inv.apply_vl_delta(1, 0, source="op", stamp_names=["guest"], now=100.0)
    assert inv.headcount() == 2
    evs = inv.apply_vl_delta(
        0, 1, source="cosmos", now=400.0, protected_names={"user"},
    )
    leave = next(e for e in evs if e.get("kind") == "leave")
    assert leave.get("name") == "guest"
    assert leave.get("leave_match", {}).get("method") == "fallback_lru_unconfirmed"
    assert "user" in inv.names()


def test_stale_arm_disarms_instead_of_dropping():
    """Approach-and-return: an arm older than the TTL decays; nobody leaves."""
    inv = DoorInventory()
    inv.observe_door(["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=10.0)
    # past grace, re-sighted at door → armed
    inv.observe_door(["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=20.0)
    assert "user" in inv.exit_armed
    # 130s quiet with user back in the room (door empty) — arm must decay
    # rather than the empty-confirm dropping him late
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                     now=151.0, exit_arm_ttl_sec=90.0)
    assert "user" not in inv.exit_armed
    assert "user" in inv.names(), "disarm removes nobody"


def test_armed_exit_still_fast():
    """Law (a): armed + empty door leaves non-User; User is protected."""
    inv = DoorInventory()
    inv.observe_door(["wanda"], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=10.0)
    inv.observe_door(["wanda"], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=20.0)
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=21.0)
    evs = inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=24.0)
    leave = next(e for e in evs if e.get("kind") == "leave")
    assert leave.get("source") == "door_empty_after"
    assert leave.get("name") == "wanda"
    assert inv.headcount() == 0


def test_doubted_slot_past_horizon_asks_match_game_not_janitor():
    """SPEC CHANGE (user 2026-08-04): with MULTIPLE occupants, the ghost
    horizon no longer guesses who to remove — that guess removed the wrong
    person live (wanda left, the janitor dropped USER). Past the horizon
    it emits leave_pending; the entry-pic match game names the leaver and
    the object does the -1. Nobody drops on the timer alone."""
    inv = DoorInventory()
    inv.observe_door(["user", "wanda"], 0, leave_confirm_sec=2.0,
                     entry_grace_sec=5.0, now=10.0)
    inv.raise_doubt("wanda", now=100.0, reason="test")
    # 200s of empty door: doubted slot must still be present
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=120.0)
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=320.0)
    assert "wanda" in inv.names()
    # past ghost horizon → the QUESTION is raised, nobody is dropped
    evs = inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                           now=120.0 + 1801.0)
    assert "wanda" in inv.names() and "user" in inv.names()
    pend = [e for e in evs if e.get("kind") == "leave_pending"]
    assert pend, "ambiguous multi-occupant ghost must ask the match game"
