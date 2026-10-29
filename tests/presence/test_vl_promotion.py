"""Object-governed writer modes (presence-perfect P3): the write gate lives
in the OBJECT (D1); witnessed verdicts are recorded+flagged, never counted
(D2); force cannot escape an explicit demotion (D4); budgets demote to
witness; auto-shadow is demote-only.
"""

import json
import time

from crissa_presence import door_watch
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
    monkeypatch.delenv("RAIL_PRESENCE_WITNESS_CANCEL", raising=False)


def test_shadow_add_witnessed(monkeypatch):
    """Shadow +1: headcount unchanged, vl_witnessed event, flagged
    _vl_recent row, traits land on the D7-resolved slot (silent-noop
    regression guard)."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "shadow")
    svc = _svc()
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    assert svc.get().headcount == 1
    st = svc.apply_vl_delta(
        1, 0, source="qwen", enter_traits={"shirt": "white"})
    assert st.headcount == 1, "shadow never mutates"
    ev = st.last_event or {}
    assert ev.get("kind") == "vl_witnessed"
    assert ev.get("mode") == "shadow"
    assert ev.get("headcount_unchanged") is True
    assert ev.get("slot_key") == "user"
    assert ev.get("shadow_slot") == "user"
    assert ev.get("shadow_slot_named") is True
    assert svc._vl_recent[-1].get("witness") is True
    assert svc.inventory.traits_live.get("user", {}).get("shirt") == "white", \
        "witnessed traits must still land (refresh_meta)"


def test_shadow_sub_feeds_cancel(monkeypatch):
    """Shadowed −1 then a door_live rise while user still on roster → no U mint.

    FOV concurrency ≤ room headcount is a re-sight (explained_by_roster), not a
    second occupant. Witness-cancel may also apply when room is empty.
    """
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "shadow")
    monkeypatch.setenv("RAIL_PRESENCE_WITNESS_CANCEL", "1")
    svc = _svc()
    # user entered long ago (his enter transit is far outside the window)
    svc.inventory.observe_door(
        ["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0,
        now=time.time() - 600.0, door_bodies_max=1)
    st = svc.apply_vl_delta(0, 1, source="qwen")  # witnessed exit
    assert st.headcount == 1
    assert (st.last_event or {}).get("kind") == "vl_witnessed"
    evs = svc.inventory.observe_door(
        [], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
        now=time.time(), door_bodies_max=1)
    assert svc.inventory.unnamed == 0, "no U minted from the outbound rise"
    assert not svc.inventory.pending_crossings
    # Either explicit cancel or roster re-sight — never +1
    assert svc.inventory.headcount() == 1
    assert "user" in svc.inventory.present
    kinds = {e.get("kind") for e in evs}
    assert kinds & {
        "crossing_cancelled",
        "refresh",
        "door_live_resight",
        "resight",
    } or any(
        e.get("kind") in ("refresh_meta", "meta_refresh")
        or e.get("source") in ("door_live_resight", "pending_roster_resight")
        for e in evs
    ) or not any(e.get("kind") == "enter" for e in evs)


def test_applied_exit_feeds_cancel():
    """Non-User leave transit then door rise → outbound_resolved cancel.

    Sudo User no longer leaves on arm+empty (AUTH-first); use a non-protected
    occupancy name for this outbound pairing test.
    """
    from crissa_presence.door_watch import DoorInventory
    inv = DoorInventory()
    inv.observe_door(["wanda"], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=10.0, door_bodies_max=1)
    inv.observe_door(["wanda"], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=20.0, door_bodies_max=1)
    inv.observe_door([], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=21.0, door_bodies_max=0)
    inv.observe_door([], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=24.0, door_bodies_max=0)   # armed exit applies
    assert inv.headcount() == 0
    # note leave transit already recorded; rise should cancel
    evs = inv.observe_door([], 1, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                           now=30.0, door_bodies_max=1)
    cancel = next(e for e in evs if e.get("kind") == "crossing_cancelled")
    assert cancel["reason"] == "outbound_resolved"
    assert inv.headcount() == 0
    assert not inv.pending_crossings


def test_window_boundary():
    """Rise 13s after leave (outside outbound window) into empty room → enter.

    Empty-room walk-ins mint immediately (no pending wait / no oracle required).
    """
    from crissa_presence.door_watch import DoorInventory
    inv = DoorInventory()
    inv.observe_door(["wanda"], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=10.0, door_bodies_max=1)
    inv.observe_door(["wanda"], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=20.0, door_bodies_max=1)
    inv.observe_door([], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=21.0, door_bodies_max=0)
    inv.observe_door([], 0, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                     now=24.0, door_bodies_max=0)   # leave transit ts=24
    assert inv.headcount() == 0
    evs = inv.observe_door([], 1, leave_confirm_sec=2.5, entry_grace_sec=5.0,
                           now=37.0, door_bodies_max=1)
    assert any(e.get("kind") == "enter" for e in evs), evs
    assert inv.headcount() == 1, "empty-room door rise must count"


def test_subs_only_drains_u(monkeypatch):
    """subs_only −1 drains a __uN via the choke point with
    method=subs_only_unnamed."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    svc = _svc()
    svc.apply_vl_delta(1, 0, source="gemma")   # full-mode mint of U
    assert svc.inventory.unnamed == 1
    svc._vl_recent = []  # outside the multi-voter window
    st = svc.apply_vl_delta(0, 1, source="qwen")
    assert st.headcount == 0
    assert svc.inventory.unnamed == 0 and not svc.inventory.unnamed_keys
    leave = next(
        e for e in (st.last_event or {}).get("events", [])
        if e.get("kind") == "leave")
    assert leave.get("leave_match", {}).get("method") == "subs_only_unnamed"


def test_subs_only_multi_named_unreachable(monkeypatch):
    """subs_only −1 with 2+ named and no U holds (identity guess forbidden).
    Sole-named is intentionally reachable (Finding B / subs_only_sole_named)."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    svc = _svc()
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["wanda"], force=True)
    svc.inventory.entry_grace_until["user"] = 0.0
    svc.inventory.entry_grace_until["wanda"] = 0.0
    svc._vl_recent = []
    st = svc.apply_vl_delta(0, 1, source="qwen", leave_person_id="user")
    assert st.headcount == 2
    assert "user" in svc.inventory.names()
    held = next(
        e for e in (st.last_event or {}).get("events", [])
        if e.get("kind") == "vl_leave_held")
    assert held["leave_match"]["method"] == "subs_only_no_unnamed"


def test_subs_only_add_witnessed(monkeypatch):
    """subs_only +1 → witnessed only (adds stay witnessed until full)."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    svc = _svc()
    st = svc.apply_vl_delta(1, 0, source="qwen")
    assert st.headcount == 0
    assert (st.last_event or {}).get("kind") == "vl_witnessed"
    assert (st.last_event or {}).get("mode") == "subs_only"


def test_budget_demotes(monkeypatch):
    """Burst cap: 5th mutating delta inside the burst window is demoted to
    witness with vl_budget_exhausted; operator force bypasses uncharged."""
    _clean_env(monkeypatch)
    svc = _svc()
    for _ in range(2):
        svc._vl_recent = []
        svc.apply_vl_delta(1, 0, source="qwen")
        svc._vl_recent = []
        svc.apply_vl_delta(0, 1, source="qwen")
    assert len(svc._vl_writer_stats["qwen"]["apply"]) == 4
    svc._vl_recent = []
    st = svc.apply_vl_delta(1, 0, source="qwen")
    assert st.headcount == 0, "demoted delta must not mutate"
    assert (st.last_event or {}).get("kind") == "vl_budget_exhausted"
    assert "vl_budget_exhausted" in ((st.last_event or {}).get("note") or "")
    # demotion is lossless: witness row recorded
    assert svc._vl_recent[-1].get("witness") is True
    # operator force bypasses and is not charged
    st2 = svc.apply_vl_delta(
        1, 0, source="op", stamp_names=["user"], force=True)
    assert st2.headcount == 1
    assert (st2.last_event or {}).get("kind") == "vl_delta"
    assert len(svc._vl_writer_stats["qwen"]["apply"]) == 4


def test_face_veto(monkeypatch):
    """Full-mode −1 against a named slot face-seen 5s ago and NOT armed →
    vl_face_veto, no drop; the same slot exit_armed → the leave applies."""
    _clean_env(monkeypatch)
    svc = _svc()
    now = time.time()
    svc.inventory.observe_door(
        ["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0,
        now=now - 5.0, door_bodies_max=1)
    assert "user" in svc.inventory.names()
    st = svc.apply_vl_delta(0, 1, source="qwen", leave_person_id="user")
    assert (st.last_event or {}).get("kind") == "vl_face_veto"
    assert "user" in svc.inventory.names(), "veto holds the slot"
    # armed-at-door face SUPPORTS the leave — exempt from the veto
    svc.inventory.exit_armed.add("user")
    svc.inventory.observe_door(
        [], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0,
        now=now, door_bodies_max=0)  # clears last_door_names
    svc._vl_recent = []
    st2 = svc.apply_vl_delta(0, 1, source="qwen", leave_person_id="user")
    assert st2.headcount == 0
    assert "user" not in svc.inventory.names()


def test_witness_never_dedups_real(monkeypatch):
    """D2: a witnessed +1 inside VL_DEDUP_SEC must not same_sign_noop a
    later REAL +1 (shadow voter influencing live arithmetic = smuggling)."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "shadow")
    svc = _svc()
    st = svc.apply_vl_delta(1, 0, source="qwen")
    assert st.headcount == 0
    assert svc._vl_recent[-1].get("witness") is True
    st2 = svc.apply_vl_delta(1, 0, source="gemma")
    # an unnamed body: the inventory counts it, the published headcount is named only (588400a)
    assert svc.inventory.headcount() == 1, "real +1 applies despite witness row"
    assert (st2.last_event or {}).get("kind") == "vl_delta"


def test_error_budget_auto_shadow(monkeypatch, tmp_path):
    """3 disagreements in the window → override=shadow, vl_auto_shadow
    mirrored; subsequent −1 witnessed; never auto-clears; vl.mode clear
    restores the env mode."""
    _clean_env(monkeypatch)
    occ = tmp_path / "occ.jsonl"
    monkeypatch.setattr(door_watch, "_IN_PYTEST", False)
    monkeypatch.setattr(door_watch, "OCCUPANCY_HISTORY", occ)
    svc = _svc()
    svc.apply_vl_delta(1, 0, source="gemma")  # applied +1 (prior row)
    for _ in range(3):
        st = svc.apply_vl_delta(0, 1, source="qwen")  # opposite → disagree
    assert svc._vl_mode_override.get("qwen") == "shadow"
    rows = [json.loads(l) for l in occ.read_text().splitlines()]
    shadow_rows = [r for r in rows if r.get("kind") == "vl_auto_shadow"]
    assert shadow_rows and shadow_rows[0]["source"] == "qwen"
    assert shadow_rows[0]["reason"] == "error_budget"
    # subsequent −1 is witnessed (demote-only; no auto-clear on good behavior)
    svc._vl_recent = []
    st = svc.apply_vl_delta(0, 1, source="qwen")
    assert (st.last_event or {}).get("kind") == "vl_witnessed"
    assert svc.inventory.headcount() == 1      # unnamed body: the inventory's count (588400a)
    assert svc._vl_mode_override.get("qwen") == "shadow"
    # operator vl.mode clear restores env-resolved mode (full default)
    svc.apply_instruction({"op": "vl.mode", "source": "qwen", "mode": "clear"})
    assert "qwen" not in svc._vl_mode_override
    assert svc._vl_mode("qwen") == ("full", "default")
    mode_rows = [r for r in rows if r.get("kind") == "vl_mode_set"]
    # (mode_set row written after our read — re-read)
    rows2 = [json.loads(l) for l in occ.read_text().splitlines()]
    assert any(r.get("kind") == "vl_mode_set" for r in rows2)


def test_sign_vs_face_instant_shadow(monkeypatch):
    """An applied −1 that drives headcount to 0 while faces are AT the
    door → instant auto-shadow, not just a disagreement mark."""
    _clean_env(monkeypatch)
    svc = _svc()
    now = time.time()
    # user's face seen at door 15s ago (outside veto window) and the last
    # door observation still shows him — last_door_names={'user'}
    svc.inventory.observe_door(
        ["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0,
        now=now - 15.0, door_bodies_max=1)
    st = svc.apply_vl_delta(0, 1, source="qwen", leave_person_id="user")
    assert st.headcount == 0
    assert svc._vl_mode_override.get("qwen") == "shadow"
    assert any(
        e.get("kind") == "vl_auto_shadow" and e.get("reason") == "sign_vs_face"
        for e in svc.inventory.events)


def test_force_cannot_escape_demotion(monkeypatch):
    """D4: force from an explicitly demoted source is witnessed with
    witness_force_ignored; default-mode operator force keeps full
    semantics (byte-compatible with today)."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "shadow")
    svc = _svc()
    st = svc.apply_vl_delta(1, 0, source="qwen", force=True)
    assert st.headcount == 0, "force must not smuggle arithmetic past the gate"
    assert (st.last_event or {}).get("kind") == "vl_witnessed"
    assert "witness_force_ignored" in ((st.last_event or {}).get("note") or "")
    st2 = svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    assert st2.headcount == 1
    assert (st2.last_event or {}).get("kind") == "vl_delta"


def test_no_env_regression(monkeypatch):
    """No RAIL_VL_MODE_* envs → event flow for operator/test sources is
    unchanged (protects the 130-green baseline)."""
    _clean_env(monkeypatch)
    svc = _svc()
    st1 = svc.apply_vl_delta(1, 0, source="gemma")
    assert (st1.last_event or {}).get("kind") == "vl_delta"
    assert svc.inventory.headcount() == 1      # unnamed bodies: the inventory's count (588400a)
    st2 = svc.apply_vl_delta(1, 0, source="qwen")
    assert (st2.last_event or {}).get("kind") == "vl_delta_deduped"
    assert (st2.last_event or {}).get("action") == "same_sign_noop"
    st3 = svc.apply_vl_delta(0, 1, source="cosmos")
    assert (st3.last_event or {}).get("kind") == "vl_delta_conflict"
    assert svc.inventory.headcount() == 1
    st4 = svc.apply_vl_delta(0, 1, source="op", force=True)
    assert (st4.last_event or {}).get("kind") == "vl_delta"
    assert st4.headcount == 0 and svc.inventory.headcount() == 0


def test_writers_block_published(monkeypatch):
    """/health and /v1/presence carry the additive `writers` block with
    correct counters; existing schema fields untouched."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "subs_only")
    svc = _svc()
    svc.apply_vl_delta(1, 0, source="qwen")            # witnessed add
    svc.apply_vl_delta(1, 0, source="gemma")           # applied add
    h = svc.health()
    w = h["writers"]
    assert w["qwen"]["mode"] == "subs_only"
    assert w["qwen"]["mode_source"] == "env"
    assert w["qwen"]["witnessed_1h"] == 1
    assert w["qwen"]["applied_1h"] == 0
    assert w["gemma"]["mode"] == "full"
    assert w["gemma"]["applied_1h"] == 1
    assert w["gemma"]["budget_left_1h"] == 11
    assert w["qwen"]["auto_shadowed"] is False
    assert "silent_sec" in w["qwen"]
    st = svc.get()
    d = st.to_dict()
    # the VL writers sit under "vl" beside the last SoT writer since the rail's 64258a1 (writers.last, 2026-08-26)
    assert "writers" in d and "qwen" in d["writers"]["vl"] and d["writers"]["last"]["who"] == "gemma"
    # additive: legacy fields intact
    for k in ("headcount", "people", "peaks", "auth", "metrics"):
        assert k in d
