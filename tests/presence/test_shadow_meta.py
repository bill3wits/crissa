"""Stills at the right moment from the right frames (presence-perfect §3.5)
+ D7 slot resolution. frames_dir hints are strictly path-confined; stills
are missing-only everywhere; a witnessed sub never pops anything.
"""

import os
import time

import pytest

from crissa_presence.service import PresenceService


# These guard the occupancy arithmetic, which the service publishes only with the occupancy mill on: RAIL_OCCUPANCY,
# default off since the rail's 00a232b ("Occupancy mill off", 2026-08-27; crissa_presence.service.occupancy_on). With
# it unset every named person is dropped from the published state and headcount reads 0, whatever the arithmetic did.
@pytest.fixture(autouse=True)
def _occupancy_mill_on(monkeypatch):
    monkeypatch.setenv("RAIL_OCCUPANCY", "1")


class _FakeCam:
    mode = "dir"
    last_error = None

    def __init__(self, jpeg: bytes | None = None):
        self._jpeg = jpeg

    def grab(self):
        return self._jpeg

    def grab_buffer(self, **kw):
        return [self._jpeg] if self._jpeg else []


def _svc(jpeg: bytes | None = None):
    return PresenceService(camera=_FakeCam(jpeg=jpeg))


def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("RAIL_VL_MODE"):
            monkeypatch.delenv(k, raising=False)


JPG_A = b"\xff\xd8\xff\xe0AAAA\xff\xd9"

@pytest.fixture(autouse=True)
def _no_live_rail(monkeypatch):
    """Keep these tests off the running house.

    _capture_enter_stills(prefer_stillness=True) calls
    stillness.pick_still_body_jpegs(), which reaches the LIVE camera rail on
    :8192 and returns real door frames — so the fake camera was never consulted
    and the assertions measured whatever the house happened to be doing. Both
    door-view tests flapped for exactly that reason (a live pick returned
    seqs [3580, 3582, 3584] mid-run on 2026-08-16).
    """
    import crissa_presence.stillness as _st

    monkeypatch.setattr(
        _st, "pick_still_body_jpegs",
        lambda *a, **k: {"ok": False, "reason": "test_no_live_rail"},
    )

JPG_B = b"\xff\xd8\xff\xe0BBBB\xff\xd9"
JPG_EVIL = b"\xff\xd8\xff\xe0EVIL\xff\xd9"


def test_still_hint_frames_dir(monkeypatch, tmp_path):
    """Allowed hint dir → those exact bytes stored; traversal, outside-root
    and symlink-escape dirs → rejected (camera fallback)."""
    allowed = tmp_path / "allowed"
    (allowed / "ev1").mkdir(parents=True)
    (allowed / "ev1" / "a.jpg").write_bytes(JPG_A)
    (allowed / "ev1" / "b.jpg").write_bytes(JPG_B)
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "x.jpg").write_bytes(JPG_EVIL)
    # root-prefix trick dir: /…/allowed-evil must NOT match root /…/allowed
    prefix_evil = tmp_path / "allowed-evil"
    prefix_evil.mkdir()
    (prefix_evil / "x.jpg").write_bytes(JPG_EVIL)
    monkeypatch.setenv("RAIL_ENTER_STILL_HINT_ROOTS", str(allowed))
    svc = _svc()
    svc._capture_enter_stills(
        "guest_hint", hint={"frames_dir": str(allowed / "ev1")}, source="qwen")
    meta = svc.inventory.enter_still["guest_hint"]
    stored = {open(p, "rb").read() for p in meta["paths"]}
    assert stored == {JPG_A, JPG_B}, "exact hint bytes stored"

    # outside-root / traversal / prefix / symlink: reject the hint and do
    # not invent a still (empty camera → no enter_still row).
    for key, hint_dir in (
        ("guest_out", evil),
        ("guest_trav", allowed / "ev1" / ".." / ".." / "evil"),
        ("guest_prefix", prefix_evil),
    ):
        svc._capture_enter_stills(key, hint={"frames_dir": str(hint_dir)}, source="qwen")
        assert key not in svc.inventory.enter_still

    link_dir = allowed / "ev2"
    link_dir.mkdir()
    (link_dir / "link.jpg").symlink_to(evil / "x.jpg")
    svc._capture_enter_stills(
        "guest_link", hint={"frames_dir": str(link_dir)}, source="qwen")
    assert "guest_link" not in svc.inventory.enter_still


def test_door_enter_does_not_store_non_door_jpeg():
    """Named enter still fires the hook; a non-door JPEG is refused
    (no chair-zoom / placeholder still)."""
    svc = _svc(jpeg=JPG_A)
    inv = svc.inventory
    now = time.time()
    evs = inv.observe_door(
        ["user"], 1, leave_confirm_sec=2.0, entry_grace_sec=45.0,
        now=now - 13.0, door_bodies_max=2)
    evs += inv.observe_door(
        [], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0,
        now=now, door_bodies_max=0)
    assert inv.unnamed == 1
    svc._capture_door_enter_stills(evs)
    assert "user" not in inv.enter_still


def test_missing_only_no_overwrite(monkeypatch):
    """A slot that already has stills keeps them: witness post and
    door_buffer capture never overwrite."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "shadow")
    svc = _svc()
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    sentinel = {"paths": ["/nonexistent/keep.jpg"], "count": 1,
                "source": "sentinel", "ts": 1.0}
    svc.inventory.enter_still["user"] = dict(sentinel)
    st = svc.apply_vl_delta(1, 0, source="qwen")  # witness resolving to user
    assert (st.last_event or {}).get("kind") == "vl_witnessed"
    assert svc.inventory.enter_still["user"]["source"] == "sentinel"
    assert (st.last_event or {}).get("shadow_still_captured") is False
    svc._capture_door_enter_stills(
        [{"kind": "enter", "name": "user", "ts": time.time()}])
    assert svc.inventory.enter_still["user"]["source"] == "sentinel"


def test_witness_sub_never_pops(monkeypatch):
    """Occupied room, witnessed left=1 with leave_guest: roster, headcount,
    traits and stills all unchanged; the event carries the leave payload
    for audit."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAIL_VL_MODE_QWEN", "shadow")
    svc = _svc()
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True,
                       enter_traits={"shirt": "white"})
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["wanda"], force=True)
    before_roster = svc.inventory.guest_roster()
    before_stills = dict(svc.inventory.enter_still)
    st = svc.apply_vl_delta(
        0, 1, source="qwen", leave_guest=1,
        leave_traits={"shirt": "white"})
    ev = st.last_event or {}
    assert ev.get("kind") == "vl_witnessed"
    assert ev.get("left") == 1
    assert ev.get("leave_guest") == 1
    assert ev.get("slot_key") == "user", "audit linkage to guest1"
    assert ev.get("leave_traits", {}).get("shirt") == "white"
    assert st.headcount == 2
    assert svc.inventory.guest_roster() == before_roster
    assert svc.inventory.enter_still == before_stills
    assert svc.inventory.traits_live.get("user", {}).get("shirt") == "white"


