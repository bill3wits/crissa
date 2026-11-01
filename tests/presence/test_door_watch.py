"""Door inventory arithmetic — enter/leave without a room tour."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
from crissa_presence.door_watch import DoorInventory


def test_baseline_then_enter():
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    assert inv.headcount() == 1
    evs = inv.observe_door(["user", "wanda"], 0, leave_confirm_sec=1.0, now=100.0)
    kinds = [e["kind"] for e in evs]
    assert "enter" in kinds
    assert inv.headcount() == 2
    assert "wanda" in inv.names()


def test_enter_then_walk_into_room_is_not_leave():
    """Entered then FOV empty = walk into room, not leave (no exit_arm yet).

    Entry grace deleted 2026-08-11 — leave still requires exit_armed + empty.
    """
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    inv.observe_door(
        ["wanda"], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0, now=10.0
    )
    assert "wanda" in inv.names()
    # walks through FOV into room — doorway empty, never re-armed at door
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0, now=11.0)
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0, now=20.0)
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0, now=60.0)
    assert "wanda" in inv.names(), "walk-into-room must not clear enter"
    assert inv.headcount() == 2


def test_real_exit_after_revisit_door():
    """In room → returns to door → empty FOV → leave (no entry grace)."""
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    inv.observe_door(
        ["wanda"], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0, now=10.0
    )
    # into room
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0, now=12.0)
    assert "wanda" in inv.names()
    # approaches door to exit → arms exit immediately (grace gone)
    evs = inv.observe_door(
        ["wanda"], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0, now=15.0
    )
    assert any(e.get("kind") == "exit_armed" for e in evs)
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0, now=16.0)
    evs = inv.observe_door(
        [], 0, leave_confirm_sec=2.0, entry_grace_sec=0.0, now=18.5
    )
    assert any(e.get("name") == "wanda" and e["kind"] == "leave" for e in evs)
    assert "wanda" not in inv.names()


def test_leave_only_after_confirm_empty_of_them():
    """Already-present (baseline) person at door then gone → leave (exit path)."""
    inv = DoorInventory()
    inv.set_baseline(["user", "wanda"], 0)
    # wanda already present — door sighting arms exit
    inv.observe_door(["user", "wanda"], 0, leave_confirm_sec=2.0, now=10.0)
    assert "wanda" in inv.exit_armed
    evs = inv.observe_door(["user"], 0, leave_confirm_sec=2.0, now=11.0)
    assert inv.headcount() == 2
    assert not any(e["kind"] == "leave" for e in evs)
    evs = inv.observe_door(["user"], 0, leave_confirm_sec=2.0, now=13.5)
    assert any(e.get("name") == "wanda" and e["kind"] == "leave" for e in evs)
    assert inv.headcount() == 1
    assert "wanda" not in inv.names()


def test_still_in_doorway_is_not_leave():
    inv = DoorInventory()
    inv.set_baseline(["wanda"], 0)
    inv.observe_door(["wanda"], 0, leave_confirm_sec=1.0, now=1.0)
    inv.observe_door(["wanda"], 0, leave_confirm_sec=1.0, now=5.0)
    assert inv.headcount() == 1


def test_vl_delta_enter_leave_named():
    """Gemma ±1 write-through into inventory SoT."""
    inv = DoorInventory()
    inv.set_baseline([], 0)
    evs = inv.apply_vl_delta(1, 0, source="gemma", stamp_names=["user"], now=10.0)
    assert any(e.get("kind") == "enter" and e.get("name") == "user" for e in evs)
    assert inv.headcount() == 1
    assert "user" in inv.names()
    inv.apply_vl_delta(0, 1, source="gemma", now=20.0)
    assert inv.headcount() == 0


def test_vl_delta_unnamed_enter():
    inv = DoorInventory()
    inv.apply_vl_delta(2, 0, source="gemma", now=1.0)
    assert inv.headcount() == 2
    assert inv.unnamed == 2
    inv.apply_vl_delta(0, 1, source="gemma", now=2.0)
    assert inv.unnamed == 1
    assert inv.headcount() == 1


def test_reconcile_emits_deltas():
    inv = DoorInventory()
    inv.set_baseline(["user", "wanda"], 0)
    ev = inv.apply_reconcile(["user"], 0)
    assert "wanda" in ev["left"]
    assert inv.headcount() == 1


def test_never_drop_baseline_person_never_seen_at_door():
    inv = DoorInventory()
    inv.set_baseline(["user", "wanda"], 0)
    for t in range(0, 20):
        inv.observe_door(["user"], 0, leave_confirm_sec=2.0, now=float(t))
    assert "wanda" in inv.names()
    assert inv.headcount() == 2


def test_voice_sticky_survives_faceblind_reconcile():
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    inv.mark_sticky(["wanda"], source="voice")
    assert inv.headcount() == 2
    inv.apply_reconcile(["user"], 0)
    assert "wanda" in inv.names()
    assert inv.headcount() == 2


def test_door_leave_clears_sticky():
    """Sticky person approaches door (exit arm) then gone — leave clears sticky."""
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    inv.mark_sticky(["wanda"], source="operator")
    inv.observe_door(["wanda"], 0, leave_confirm_sec=2.0, now=10.0)  # arms exit
    assert "wanda" in inv.exit_armed
    inv.observe_door([], 0, leave_confirm_sec=2.0, now=11.0)
    inv.observe_door([], 0, leave_confirm_sec=2.0, now=13.5)
    assert "wanda" not in inv.names()
    assert "wanda" not in inv.sticky
    inv.apply_reconcile(["user"], 0)
    assert "wanda" not in inv.names()
    assert inv.headcount() == 1


def test_force_leave_operator():
    inv = DoorInventory()
    inv.mark_sticky(["wanda", "user"], source="operator")
    inv.force_leave(["wanda"], source="operator")
    assert inv.names() == ["user"]
    assert "wanda" not in inv.sticky


def test_sole_empty_leave_is_ghost_recovery_only():
    """Non-User sole: ghost recovery after long empty FOV (User is protected)."""
    import os
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="gemma", stamp_names=["wanda"], now=10.0, entry_grace_sec=5.0)
    assert inv.names() == ["wanda"]
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=16.0)
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=16.0 + 121.0)
    assert "wanda" in inv.names()
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=16.0 + 200.0)
    assert "wanda" in inv.names()
    evs = inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=16.0 + 1801.0)
    leave = next(e for e in evs if e.get("kind") == "leave")
    assert leave.get("source") == "ghost_recovery"
    assert leave.get("name") == "wanda"
    assert inv.headcount() == 0


def test_sole_empty_env_tunable():
    """Mechanism preserved: short timer via env still works for non-User."""
    import os
    os.environ["RAIL_PRESENCE_SOLE_EMPTY_LEAVE_SEC"] = "120"
    try:
        inv = DoorInventory()
        inv.apply_vl_delta(1, 0, source="gemma", stamp_names=["wanda"], now=10.0, entry_grace_sec=5.0)
        inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=16.0)
        evs = inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=16.0 + 121.0)
        assert any(e.get("kind") == "leave" and e.get("name") == "wanda" for e in evs)
    finally:
        del os.environ["RAIL_PRESENCE_SOLE_EMPTY_LEAVE_SEC"]


def test_sole_empty_does_not_drop_second_person():
    inv = DoorInventory()
    inv.set_baseline(["user", "wanda"], 0)
    for t in (10.0, 20.0, 30.0, 40.0):
        inv.observe_door([], 0, leave_confirm_sec=2.0, now=t)
    assert inv.headcount() == 2


def test_door_face_enter_absorbs_unnamed():
    """VL +1 U then door face user → single named body, not user+U1."""
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="gemma", now=1.0)
    assert inv.unnamed == 1
    inv.observe_door(["user"], 0, leave_confirm_sec=2.0, now=2.0)
    assert inv.names() == ["user"]
    assert inv.unnamed == 0
    assert inv.headcount() == 1


def test_peaks_ratchet_on_object_not_llm():
    """Peak headcount/door concurrent live on inventory (code)."""
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="gemma", now=1.0)
    assert inv.peak_headcount == 1
    assert inv.session_peak_headcount == 1
    inv.apply_vl_delta(1, 0, source="qwen", now=2.0)
    # cap may keep unnamed at 1 — force second named
    inv.apply_vl_delta(1, 0, source="op", stamp_names=["user"], now=3.0)
    inv.apply_vl_delta(1, 0, source="op", stamp_names=["wanda"], now=4.0)
    assert inv.headcount() >= 2
    assert inv.peak_headcount >= 2
    inv.observe_door(["user", "wanda"], 1, leave_confirm_sec=2.0, now=5.0)
    assert inv.peak_door_concurrent >= 2
    # empty session rolls session peak into last_session
    inv.force_leave(["user", "wanda"], source="test")
    inv.unnamed = 0
    inv._touch_peaks(now=10.0)
    assert inv.headcount() == 0
    assert inv.session_peak_headcount == 0
    assert inv.last_session_peak_headcount >= 2
    assert inv.peak_headcount >= 2  # process high-water kept
    peaks = inv.peaks_dict()
    assert peaks["owner"] == "sot_inventory"
    assert peaks["headcount"] >= 2


def test_phantom_empty_fov_leave_needs_body_witness():
    """2026-08-04: two phantom "leave user" on hallway-light changes — the
    empty-FOV confirm fired with no body seen at the door for minutes. Law (a)
    now requires a fresh body witness; stale-witness confirms disarm instead."""
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    # past entry grace, re-sight at door arms exit (and is a body witness)
    evs = inv.observe_door(["user"], 0, entry_grace_sec=45.0, now=100.0)
    assert "user" in inv.exit_armed or any(
        e["kind"] == "exit_armed" for e in evs
    )
    # nothing at the door for 60s (witness stale), then empty-FOV confirm window
    evs = inv.observe_door([], 0, leave_confirm_sec=2.5, now=160.0,
                           exit_arm_ttl_sec=600.0)
    evs = inv.observe_door([], 0, leave_confirm_sec=2.5, now=163.5,
                           exit_arm_ttl_sec=600.0)
    kinds = [e["kind"] for e in evs]
    assert "leave" not in kinds, "phantom empty-FOV leave must not fire"
    assert "exit_disarmed" in kinds
    assert "user" in inv.names(), "nobody was dropped"
    assert "user" not in inv.exit_armed


def test_genuine_exit_still_fast():
    """Real walk-out for non-User: arm + empty FOV. User is timer-protected."""
    inv = DoorInventory()
    inv.set_baseline(["wanda"], 0)
    inv.observe_door(["wanda"], 0, entry_grace_sec=45.0, now=100.0)  # arms
    inv.observe_door([], 0, leave_confirm_sec=2.5, now=101.0)
    evs = inv.observe_door([], 0, leave_confirm_sec=2.5, now=104.0)
    assert any(e["kind"] == "leave" and e.get("name") == "wanda" for e in evs)
    assert "wanda" not in inv.names()


def test_a_seat_tour_that_misses_someone_does_not_evict_them():
    """The door is the only way out, so "camera did not see her" is not a leave.

    apply_reconcile's docstring already records the incident this reproduces —
    "user 2026-08-11: body_count found user and wiped wanda still in the
    room" — and union mode was added to prevent it. room_sweep kept taking the
    default, which is replace, so it regressed: 2026-08-16 the sweep dropped a
    named occupant it simply had not panned to yet.
    """
    inv = DoorInventory()
    inv.set_baseline(["user", "wanda"], 0)
    inv.sticky = set()
    assert inv.headcount() == 2

    # the tour finds user at his desk and never reaches wanda
    inv.apply_reconcile(["user"], 0, mode="union")
    assert "wanda" in inv.present, "a partial tour must not evict"
    assert inv.headcount() == 2

    # replace is the legacy full-reinit path and still drops her, which is
    # exactly why a sweep must never use it
    inv2 = DoorInventory()
    inv2.set_baseline(["user", "wanda"], 0)
    inv2.sticky = set()
    inv2.apply_reconcile(["user"], 0, mode="replace")
    assert "wanda" not in inv2.present
