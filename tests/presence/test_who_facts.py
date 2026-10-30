"""Who's here writes the one SoT. Look-only is revoked (user 2026-08-18)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.door_watch import DoorInventory
from crissa_presence.inspector import (
    apply_operator_scan,
    apply_who_facts,
    tally_operator_looks,
)


def test_who_facts_drops_unseen_named_keeps_user() -> None:
    inv = DoorInventory()
    inv.set_baseline(["user", "wanda"], 0)
    out = apply_who_facts(inv, ["user"], uid="user", confirm_user=lambda _n: {"confirmed": False})
    assert "user" in inv.names()
    assert "wanda" not in inv.names()
    assert out["left"] == ["wanda"]
    assert inv.headcount() == 1


def test_who_facts_empty_look_does_not_evict_user() -> None:
    inv = DoorInventory()
    inv.set_baseline(["user", "wanda"], 0)
    out = apply_who_facts(inv, [], uid="user", confirm_user=lambda _n: {"confirmed": False})
    assert "user" in inv.names()
    assert "wanda" not in inv.names()
    assert out["user_confirm"]["confirmed"] is False


def test_who_facts_user_confirm_true_leaves_user() -> None:
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    out = apply_who_facts(inv, [], uid="user", confirm_user=lambda _n: {"confirmed": True})
    assert "user" not in inv.names()
    assert "user" in out["left"]


def test_operator_scan_always_keeps_user_and_leftover_s() -> None:
    inv = DoorInventory()
    inv.set_baseline(["wanda"], 0)
    out = apply_operator_scan(
        inv, named=["wanda"], unnamed_want=1, uid="user"
    )
    assert "user" in inv.names()
    assert "wanda" in inv.names()
    assert inv.unnamed == 1
    assert inv.headcount() == 3
    assert out["mode"] == "replace"


def test_operator_scan_empty_sot_still_has_user() -> None:
    inv = DoorInventory()
    inv.set_baseline([], 0)
    apply_operator_scan(inv, named=[], unnamed_want=0, uid="user")
    assert "user" in inv.names()
    assert inv.headcount() == 1


def test_tally_user_desk_body_is_not_s() -> None:
    named, unnamed = tally_operator_looks(
        [
            {"hour": 6, "names": [], "n_faces": 1, "body": True},
            {"hour": 12, "names": [], "n_faces": 0, "body": True},
        ],
        user_hour=6,
    )
    assert named == []
    assert unnamed == 0


def test_tally_smiths_poster_faces_are_not_s() -> None:
    """n_faces must already be live-only. 3 Smiths detections with no names → 0."""
    named, unnamed = tally_operator_looks(
        [{"hour": 11, "names": [], "n_faces": 0, "body": True, "stop": "stand_11"}],
        user_hour=6,
    )
    assert named == []
    assert unnamed == 0


def test_tally_faceid_name_plus_extra_body() -> None:
    named, unnamed = tally_operator_looks(
        [
            {"hour": 8, "names": ["wanda"], "n_faces": 1, "body": True},
            {"hour": 11, "names": [], "n_faces": 1, "body": False},
        ],
        user_hour=6,
    )
    assert named == ["wanda"]
    assert unnamed == 1


def test_who_is_here_refuses_a_second_walk() -> None:
    from crissa_presence import inspector as insp

    assert insp._WALK_LOCK.acquire(blocking=False)
    try:
        out = insp.who_is_here({})
    finally:
        insp._WALK_LOCK.release()
    assert out["ok"] is False
    assert out["error"] == "already_running"


def test_who_is_here_leases_and_releases(monkeypatch) -> None:
    from crissa_presence.door_watch import DoorInventory
    from crissa_presence import inspector as insp

    posts: list[tuple] = []

    def _post(path, body=None, timeout=8.0):
        posts.append((str(path), dict(body or {})))
        if str(path).endswith("/lease") and not str(path).endswith("/release"):
            return {"ok": True, "lease_id": "Lwalk"}
        if str(path).endswith("/lease/release"):
            return {"ok": True}
        return {"ok": True}

    monkeypatch.setattr("crissa_presence.room_sweep._post", _post)
    monkeypatch.setattr(
        "crissa_presence.seat_shot.go_home",
        lambda _rs, lid=None: {"home": True, "lease_id": lid},
    )

    def _snap(name, pan, **kw):
        assert kw.get("lease_id") == "Lwalk"
        return {
            "aim_ok": True,
            "pan_actual": pan,
            "arrived": True,
            "jpeg_wide": b"W" * 600,
            "jpeg_face": b"F" * 600,
            "facefinder": True,
        }

    monkeypatch.setattr("crissa_presence.error_peek._aim_snap", _snap)
    monkeypatch.setattr(insp, "_name_from_jpeg", lambda *_a, **_k: {"n_faces": 0, "names": [], "person_id": None})
    monkeypatch.setattr(insp, "_body_blob_same_tilt", lambda *_a, **_k: False)

    class _Inv(DoorInventory):
        pass

    class _St:
        headcount = 1
        people = []
        frame_age_sec = 0.2
        camera_open = True
        camera_blind = False

    class _Svc:
        inventory = _Inv()

        def get(self):
            return _St()

        def _presence_from_inventory(self, **kw):
            return _St()

        def _set(self, st):
            return None

        def _save_inventory_disk(self, force=False):
            return None

    monkeypatch.setattr("crissa_presence.server.get_svc", lambda: _Svc())
    out = insp.who_is_here({})
    assert out["ok"] is True
    assert out["lease_released"] is True
    assert any(p.endswith("/lease") and not p.endswith("/release") for p, _ in posts)
    assert any(p.endswith("/lease/release") for p, _ in posts)
    rel = [b for p, b in posts if p.endswith("/lease/release")]
    assert rel and rel[0].get("lease_id") == "Lwalk"


def test_who_facts_stamps_new_name() -> None:
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    apply_who_facts(inv, ["user", "wanda"], uid="user")
    assert "wanda" in inv.names()
    assert inv.headcount() == 2
