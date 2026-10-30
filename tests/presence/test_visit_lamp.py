"""Visit lamp: occupancy *dot*, not occupancy mill."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence import sot_proof, visit_lamp as vl
from crissa_presence.schema import RoomPresence
from crissa_presence.service import PresenceService
from crissa_presence.stranger_x import record_event


class _FakeCam:
    mode = "dir"
    last_error = None

    def grab(self):
        return None

    def grab_buffer(self, **kw):
        return []


def _unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    e = rng.normal(size=8).astype(np.float32)
    e /= np.linalg.norm(e)
    return e


def _svc(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_FACEID_LOG", str(tmp_path / "faceid"))
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path / "strangers"))
    monkeypatch.setenv("RAIL_VISIT_LAMP", str(tmp_path / "visit_lamp.json"))
    monkeypatch.setenv("RAIL_STORE_FACE_AUDIT", str(tmp_path / "store_face.jsonl"))
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    monkeypatch.delenv("RAIL_LAMP_GREEN_IDS", raising=False)
    monkeypatch.setattr(sot_proof, "REV_PATH", tmp_path / "sot_rev.json")
    monkeypatch.setattr(sot_proof, "AUTH_LOG", tmp_path / "auth.jsonl")
    monkeypatch.setattr(vl, "LAMP_PATH", tmp_path / "visit_lamp.json")
    monkeypatch.setattr(vl, "TOKEN_PATH", tmp_path / "secure_token.json")
    monkeypatch.setattr(vl, "SECURITY_AUDIT", tmp_path / "store_face.jsonl")
    (tmp_path / "faceid").mkdir()
    svc = PresenceService(camera=_FakeCam())
    svc.last_grabber = {}
    monkeypatch.setattr(
        svc, "_poll_grabber", lambda: {"ok": True, "at_door": True, "lease": None}
    )
    monkeypatch.setattr(svc, "faceid_hud", lambda: {})
    monkeypatch.setattr(svc, "_visit_lamp_hold_door", lambda: None)
    monkeypatch.setattr(svc, "_beam_rest", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_ensure_door_aim", lambda **k: {"ok": True})
    return svc


def _census(people):
    return {"ok": True, "people": people, "n": len(people)}


def test_boot_is_amber_before_any_user_face(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([]),
    )
    svc._set(RoomPresence())
    assert svc.get().visit_lamp == "amber"
    assert svc.get().visit_lamp_info.get("why") in ("boot", "insecure")
    assert svc._visit_is_secure() is False


def test_only_click_makes_sage(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([{"id": "user", "kind": "user", "ts": time.time()}]),
    )
    svc._visit_boot_unproven = False
    svc._set(RoomPresence())
    assert svc.get().visit_lamp == "amber"
    assert svc._visit_is_secure() is False


def test_hourly_scan_cannot_clear_an_unacked_visit(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    now = time.time()
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([{"id": "xena", "kind": "named", "ts": now}]),
    )
    svc._visit_boot_unproven = False
    svc._set(RoomPresence())
    assert svc.get().visit_lamp == "amber"
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    monkeypatch.setattr(svc, "_desk_look_once", lambda uid: {"found": False})
    monkeypatch.setattr(svc, "faceid_scan", lambda: {})
    out = svc.visit_lamp_scan()
    assert out.get("skipped") == "retired"
    assert out.get("looked") is False
    assert svc.get().visit_lamp == "amber"
    assert (tmp_path / "visit_lamp.json").exists() is False or (
        vl.load_ack(tmp_path / "visit_lamp.json").get("ack_ts") or 0
    ) == 0


def test_click_while_authed_sages_with_guest_already_here(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    now = time.time()
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census(
            [
                {"id": "user", "kind": "user", "ts": now - 60},
                {"id": "wanda", "kind": "named", "ts": now - 3600, "last_ts": now},
            ]
        ),
    )
    svc._visit_boot_unproven = False
    svc._set(RoomPresence(user_auth=True))
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    called = {"n": 0}

    def desk(uid, force=False):
        called["n"] += 1
        called["force"] = force
        return {"found": True, "via": "desk_aim", "best_sim": 0.85, "names": ["user"]}

    monkeypatch.setattr(svc, "_desk_look_once", desk)
    st = svc.get()
    st.user_auth = True
    out = svc.visit_lamp_click()
    assert called["n"] == 0, "live AUTH is the proof — no second desk still"
    assert out.get("ok") is True
    assert out.get("via") == "auth"
    assert svc.get().visit_lamp == "sage"


def test_guest_resight_does_not_kill_sage():
    now = time.time()
    color, why, _ = vl.project_color(
        secure=True,
        cannot_see=False,
        census=_census(
            [{"id": "wanda", "kind": "named", "ts": now - 100, "last_ts": now + 50}]
        ),
        now=now,
    )
    assert color == "sage"
    assert why == "secure"


def test_census_does_not_move_the_flag():
    now = time.time()
    color, why, _ = vl.project_color(
        secure=True,
        cannot_see=False,
        census=_census([{"id": "xena", "kind": "named", "ts": now + 1}]),
        now=now + 2,
    )
    assert color == "sage"
    assert why == "secure"


def test_click_does_not_trust_stale_sticky(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._face_auth_hold["user"] = {"sim": 0.9, "ts": time.time(), "source": "old"}
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": False, "via": "desk_aim", "best_sim": 0.0},
    )
    monkeypatch.setattr(svc, "_last_desk_unmatched", [])
    out = svc.visit_lamp_click()
    assert out.get("ok") is False
    assert out.get("checked") is False
    assert svc._visit_ack.get("ack_ts") in (0, 0.0, None)


def test_click_found_writes_ack_and_can_sage(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([{"id": "user", "kind": "user", "ts": time.time()}]),
    )
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": True, "via": "desk_aim", "best_sim": 0.9},
    )
    out = svc.visit_lamp_click()
    assert out.get("ok") is True
    assert svc.get().visit_lamp == "sage"
    ack = vl.load_ack(tmp_path / "visit_lamp.json")
    assert ack.get("ack_ts", 0) > 0


def test_scan_while_sage_user_only_keeps_sage(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    called = {"desk": 0, "door": 0}

    def desk(uid, **k):
        called["desk"] += 1
        return {"found": True, "names": ["user"]}

    def glance():
        called["door"] += 1
        return {"ok": True, "n_faces": 0, "names": {}}

    monkeypatch.setattr(svc, "_desk_look_once", desk)
    monkeypatch.setattr(svc, "door_glance", glance)
    monkeypatch.setattr(svc, "faceid_scan", lambda: {})
    out = svc.visit_lamp_scan()
    assert called["desk"] == 0
    assert called["door"] == 0
    assert out.get("skipped") == "retired"
    assert out.get("looked") is False
    assert svc.get().visit_lamp == "sage"
    assert not out.get("need_click")


def test_scan_while_sage_other_occupant_keeps_sage(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    called = {"desk": 0}

    def desk(uid, **k):
        called["desk"] += 1
        return {"found": True, "names": ["user", "wanda"]}

    monkeypatch.setattr(svc, "_desk_look_once", desk)
    monkeypatch.setattr(
        svc, "door_glance", lambda: {"ok": True, "n_faces": 0, "names": {}}
    )
    monkeypatch.setattr(svc, "faceid_scan", lambda: {})
    out = svc.visit_lamp_scan()
    assert called["desk"] == 0
    assert out.get("skipped") == "retired"
    assert out.get("looked") is False
    assert svc.get().visit_lamp == "sage"


def test_hour_elapsed_no_longer_turns_amber():
    now = time.time()
    color, why, info = vl.project_color(
        secure=True,
        cannot_see=False,
        since=now - 3601,
        now=now,
    )
    assert color == "sage"
    assert why == "secure"
    assert info.get("secure") is True


def test_entry_trips_amber_unnamed_is_stranger():
    uid = "user"
    assert vl.entry_trips_amber({"names": {}, "n_faces": 1}, uid) is True
    assert vl.entry_trips_amber({"names": {"user": 0.9}, "n_faces": 1}, uid) is False
    assert vl.entry_trips_amber({"names": {"wanda": 0.8}, "n_faces": 1}, uid) is True
    assert vl.entry_trips_amber({"names": {"user": 0.9}, "n_faces": 2}, uid) is True
    assert vl.entry_trips_amber({"names": {}, "n_faces": 0}, uid) is False


def test_door_unnamed_face_kills_sage(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc._visit_lamp_on_door_faces(
        {"names": {}, "n_faces": 1, "unmatched": [{"bbox": [0, 0, 80, 80]}]}
    )
    assert svc.get().visit_lamp == "amber"
    ack = vl.load_ack(tmp_path / "visit_lamp.json")
    assert (ack.get("ack_ts") or 0) == 0


def test_door_raw_detects_without_crop_do_not_kill_sage(tmp_path, monkeypatch):
    """Hallway portraits: n_faces=4 names=[] unmatched=[]. Quality gate
    already refused them; backup must not drop Secure."""
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc._visit_lamp_on_door_faces({"names": {}, "n_faces": 4, "unmatched": []})
    assert svc.get().visit_lamp == "sage"
    assert svc._visit_is_secure() is True


def test_door_wanda_kills_sage(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc._visit_lamp_on_door_faces({"names": {"wanda": 0.8}, "n_faces": 1})
    assert svc.get().visit_lamp == "amber"


def test_door_user_only_kills_sage(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc._visit_lamp_on_door_faces({"names": {"user": 0.85}, "n_faces": 1})
    assert svc.get().visit_lamp == "amber"
    assert svc._visit_is_secure() is False


def test_scan_while_sage_empty_look_keeps_sage(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    called = {"desk": 0}
    monkeypatch.setattr(
        svc, "_desk_look_once", lambda uid, **k: called.__setitem__("desk", 1)
    )
    monkeypatch.setattr(
        svc, "door_glance", lambda: {"ok": True, "n_faces": 0, "names": {}}
    )
    monkeypatch.setattr(svc, "faceid_scan", lambda: {})
    svc.visit_lamp_scan()
    assert called["desk"] == 0
    assert svc.get().visit_lamp == "sage"


def test_pan_cooldown_is_silent_not_rust(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([]),
    )
    svc._visit_boot_unproven = False
    before = svc.get().visit_lamp
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": False, "skipped": "pan_cooldown"},
    )
    out = svc.visit_lamp_click()
    assert out == {"ok": False, "checked": False}
    assert svc.get().visit_lamp == before
    assert svc.get().visit_lamp != "rust"


def test_scan_clears_boot_unproven(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([]),
    )
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    monkeypatch.setattr(svc, "_desk_look_once", lambda uid: {"found": True})
    monkeypatch.setattr(svc, "faceid_scan", lambda: {})
    out = svc.visit_lamp_scan()
    assert out.get("skipped") == "retired"
    # still not sage — only a click acks
    assert svc.get().visit_lamp == "amber"
    assert svc._visit_is_secure() is False


def test_user_id_reads_house_mode_json(tmp_path, monkeypatch):
    monkeypatch.delenv("HOUSE_USER_ID", raising=False)
    monkeypatch.delenv("RAIL_PRIMARY_USER", raising=False)
    monkeypatch.setattr(vl._paths, "MODE_FILE", tmp_path / "house-mode.json")
    (tmp_path / "house-mode.json").write_text(
        json.dumps({"auth": {"user_id": "crissa-test"}}), encoding="utf-8"
    )
    assert vl.user_id() == "crissa-test"
    svc = _svc(tmp_path, monkeypatch)
    assert svc._user_id() == "crissa-test"


def test_cannot_look_paints_rust_not_amber(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([]),
    )
    svc._visit_boot_unproven = False
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: "lights_out")
    svc.visit_lamp_click()
    assert svc.get().visit_lamp == "rust"
    assert svc.get().visit_lamp_info.get("why") == "cannot_see"


@pytest.mark.parametrize(
    "blocked",
    ["leased", "speaking", "f2f", "room_busy"],
)
def test_scan_refuses_while_blocked(tmp_path, monkeypatch, blocked):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: blocked)
    called = {"n": 0}
    monkeypatch.setattr(svc, "_desk_look_once", lambda uid: called.__setitem__("n", 1))
    out = svc.visit_lamp_scan()
    assert out.get("skipped") == "retired"
    assert called["n"] == 0
    # Retired scan never paints Faulty.
    assert svc.get().visit_lamp != "rust"


def test_scan_rusts_when_host_locked(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: "host_locked")
    called = {"n": 0}
    monkeypatch.setattr(svc, "_desk_look_once", lambda uid: called.__setitem__("n", 1))
    out = svc.visit_lamp_scan()
    assert out.get("skipped") == "retired"
    assert called["n"] == 0
    assert svc.get().visit_lamp != "rust"


def test_click_lease_does_not_rust_and_then_looks(tmp_path, monkeypatch):
    """Hammering Secure while the gimbal is held used to paint rust."""
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([{"id": "user", "kind": "user", "ts": time.time()}]),
    )
    n = {"i": 0}

    def blocked(click=False):
        n["i"] += 1
        return "leased" if n["i"] < 3 else None

    monkeypatch.setattr(svc, "_visit_camera_blocked", blocked)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": True, "via": "desk_aim", "best_sim": 0.9},
    )
    out = svc.visit_lamp_click()
    assert out.get("ok") is True
    assert svc.get().visit_lamp == "sage"
    assert svc.get().visit_lamp_info.get("why") != "cannot_see"


def test_click_from_ff_lease_inherits_auth(tmp_path, monkeypatch):
    """2026-09-09 06:41: Secure click during session FaceFinder sat in
    blocked=leased and never reached via=auth. AUTH is the proof — sage now."""
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setenv("RAIL_LAMP_CLICK_WAIT_SEC", "0")
    st = svc.get() or RoomPresence()
    st.user_auth = True
    svc._set(st)
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: "leased")
    called = {"n": 0}

    def desk(uid, **k):
        called["n"] += 1
        return {"found": True, "via": "desk_aim", "best_sim": 0.9}

    monkeypatch.setattr(svc, "_desk_look_once", desk)
    out = svc.visit_lamp_click()
    assert out.get("ok") is True
    assert out.get("via") == "auth"
    assert called["n"] == 0
    assert svc._visit_is_secure() is True
    assert svc.get().visit_lamp == "sage"


def test_hold_door_forces_aim_through_a_look_lease():
    src = (
        Path(__file__).resolve().parents[2] / "modules/presence/crissa_presence/service.py"
    ).read_text(encoding="utf-8")
    block = src.split("def _visit_lamp_hold_door")[1].split("def _visit_secure_on")[0]
    assert "_ensure_door_aim(release_facelock=True, force=True)" in block


def test_click_still_busy_stays_previous_colour(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setenv("RAIL_LAMP_CLICK_WAIT_SEC", "0")
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: "leased")
    called = {"n": 0}
    monkeypatch.setattr(
        svc, "_desk_look_once", lambda uid, **k: called.__setitem__("n", 1)
    )
    out = svc.visit_lamp_click()
    assert out.get("ok") is False
    assert called["n"] == 0
    assert svc.get().visit_lamp != "rust"


def test_click_in_flight_second_does_not_rust(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    assert svc._visit_click_lock.acquire(blocking=False)
    try:
        out = svc.visit_lamp_click()
        assert out.get("ok") is False
        assert svc.get().visit_lamp != "rust"
    finally:
        svc._visit_click_lock.release()


def test_rust_clears_when_the_camera_answers_again(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([]),
    )
    svc._visit_cannot_see = True
    svc._visit_boot_unproven = False
    svc._set(RoomPresence())
    assert svc.get().visit_lamp == "rust"
    svc._visit_cannot_see = False
    svc._set(RoomPresence())
    assert svc.get().visit_lamp != "rust"


def test_stranger_click_fails_silently(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": False, "via": "desk_aim"},
    )
    a = svc.visit_lamp_click()
    b = {"ok": False, "checked": False}
    assert a == b


def test_stranger_click_records_hold_auth_not_speech(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": False, "via": "desk_aim"},
    )
    emb = _unit(7)
    svc._last_desk_unmatched = [
        {"jpeg": b"\xff\xd8x", "crop": b"\xff\xd8x", "emb": emb, "bbox": [1, 2, 3, 4]}
    ]
    svc.visit_lamp_click()
    audit = (tmp_path / "store_face.jsonl").read_text(encoding="utf-8")
    assert "Hold Auth req failed" in audit
    assert '"kind": "hold_auth"' in audit
    assert "repeated verbal attempts" not in audit
    rows = list((tmp_path / "strangers").glob("X*/events.jsonl"))
    assert rows
    ev = rows[0].read_text(encoding="utf-8")
    assert "hold_auth" in ev
    assert "Hold Auth req failed" in ev


def test_stranger_click_ambers_via_the_log(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    now = time.time()
    people = [{"id": "x1", "kind": "stranger", "ts": now}]
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census(people),
    )
    svc._visit_boot_unproven = False
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": False, "via": "desk_aim"},
    )
    svc.visit_lamp_click()
    assert svc.get().visit_lamp == "amber"
    assert svc._visit_is_secure() is False


def test_failed_click_never_writes_ack_or_disk(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": False, "via": "desk_aim"},
    )
    svc.visit_lamp_click()
    p = tmp_path / "visit_lamp.json"
    assert not p.is_file()


def test_boot_file_does_not_restore_secure():
    assert vl.load_secure() is False
    color, why, info = vl.project_color(
        secure=False,
        cannot_see=False,
        ack={"day": "1999-01-01", "ack_ts": 1.0},
        now=time.time(),
    )
    assert color == "amber"
    assert info.get("secure") is False


def test_placeholder_and_zero_ts_never_latch():
    ts = vl.latest_non_user_ts(
        _census(
            [
                {"id": "unknown-1", "ts": time.time()},
                {"id": "u3", "ts": time.time()},
                {"id": "xena", "ts": 0},
            ]
        ),
        frozenset({"user"}),
    )
    assert ts == 0.0


def test_green_ids_env_override(monkeypatch):
    monkeypatch.setenv("RAIL_LAMP_GREEN_IDS", "user,wanda")
    assert vl.lamp_green_ids() == frozenset({"user", "wanda"})
    ts = vl.latest_non_user_ts(
        _census([{"id": "wanda", "ts": 9.0}, {"id": "xena", "ts": 3.0}]),
        vl.lamp_green_ids(),
    )
    assert ts == 3.0


def test_lamp_never_touches_occupancy():
    src = Path(vl.__file__).read_text(encoding="utf-8")
    for bad in (
        "count.add",
        "apply_vl_delta",
        "note_visitor",
        "self.inventory",
        "DoorInventory",
    ):
        assert bad not in src
    assert "headcount =" not in src
    assert "st.headcount" not in src
    svc_src = Path(__file__).resolve().parents[2] / "modules/presence/crissa_presence/service.py"
    text = svc_src.read_text(encoding="utf-8")
    block = text.split("def _project_visit_lamp")[1].split("def _kick_unlock_face_grab")[0]
    for bad in ("count.add", "apply_vl_delta", "note_visitor", "headcount"):
        assert bad not in block


def test_hold_auth_kind_is_not_speech(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path))
    out = record_event(
        kind="hold_auth",
        jpeg=b"\xff\xd8z",
        crop=b"\xff\xd8z",
        emb=_unit(2),
        source="visit_lamp",
    )
    assert out.get("ok")
    assert out.get("kind") == "hold_auth"
    line = (tmp_path / "X1" / "events.jsonl").read_text(encoding="utf-8")
    assert "Hold Auth req failed" in line
    assert "speech" not in line


def test_schema_emits_visit_lamp():
    d = RoomPresence().to_dict()
    assert d.get("visit_lamp") == "amber"
    assert "visit_lamp_info" in d


def _make_sage(svc, monkeypatch):
    now = time.time()
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([{"id": "user", "kind": "user", "ts": now}]),
    )
    svc._visit_cannot_see = False
    svc._visit_secure = True
    svc._visit_secure_reason = "click"
    # a SETTLED Secure: tripwire waits RAIL_SECURE_TRIP_SETTLE_S after the pan
    svc._visit_secure_since = now - 30.0
    svc._door_trip_logged = None
    svc._set(RoomPresence())
    assert svc.get().visit_lamp == "sage"
    assert svc._visit_is_secure() is True
    assert svc.get().user_auth is True
    assert svc.get().user_auth_reason == "secure"


def test_sage_holds_auth_skips_hunt_when_flag_on(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_LAMP_DRIVES_AUTH", "1")
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    called = {"n": 0}

    def hunt(**kw):
        called["n"] += 1
        return {"found": False}

    monkeypatch.setattr(svc, "chase_user_face_at_door", hunt)
    monkeypatch.setattr(svc, "_desk_look_once", lambda uid: hunt())
    out = svc.ensure_user_auth(reason="command")
    assert out.get("auth") is True
    assert called["n"] == 0
    assert out.get("via") in ("sticky", "voice")


def test_sage_force_facefind_does_not_leave_the_door(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_LAMP_DRIVES_AUTH", "1")
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    called = {"n": 0}

    def desk(uid, force=False):
        called["n"] += 1
        called["force"] = force
        return {"found": True, "via": "desk_ff", "best_sim": 0.9}

    monkeypatch.setattr(svc, "_desk_look_once", desk)
    out = svc.ensure_user_auth(force_facefind=True, mode="desk", reason="command")
    assert called["n"] == 0
    assert out.get("auth") is True


def test_amber_ignores_sticky_and_hunts_when_flag_on(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_LAMP_DRIVES_AUTH", "1")
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([{"id": "xena", "kind": "named", "ts": time.time()}]),
    )
    svc._visit_boot_unproven = False
    svc._face_auth_hold["user"] = {"sim": 0.9, "ts": time.time(), "source": "old"}
    svc._set(RoomPresence())
    assert svc.get().visit_lamp == "amber"
    called = {"n": 0}

    def chase(**kw):
        called["n"] += 1
        return {"found": False, "via": "door_chase"}

    monkeypatch.setattr(svc, "chase_user_face_at_door", chase)
    out = svc.ensure_user_auth(reason="command")
    assert called["n"] == 1
    assert out.get("auth") is False
    assert out.get("hunt", {}).get("found") is False


def test_env_off_does_not_park_sticky_on_amber(tmp_path, monkeypatch):
    """RAIL_LAMP_DRIVES_AUTH is unread. Amber still hunts (Law 1)."""
    monkeypatch.setenv("RAIL_LAMP_DRIVES_AUTH", "0")
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([{"id": "xena", "kind": "named", "ts": time.time()}]),
    )
    svc._visit_boot_unproven = False
    svc._face_auth_hold["user"] = {"sim": 0.9, "ts": time.time(), "source": "old"}
    svc._set(RoomPresence())
    assert svc.get().visit_lamp == "amber"
    called = {"n": 0}
    monkeypatch.setattr(
        svc,
        "chase_user_face_at_door",
        lambda **kw: called.__setitem__("n", called["n"] + 1) or {"found": False},
    )
    out = svc.ensure_user_auth(reason="command")
    assert called["n"] == 1
    assert out.get("auth") is False


def test_amber_edge_drops_auth_without_desk_hunt(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_LAMP_DRIVES_AUTH", "1")
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc._face_auth_hold["user"] = {"sim": 0.9, "ts": time.time(), "source": "click"}
    svc._user_session = {"opened_ts": time.time(), "opened_via": "face_sticky"}
    st = svc.get()
    st.user_auth = True
    svc._set(st)
    assert svc.get().visit_lamp == "sage"
    called = {"keep": 0, "door": 0}

    def keep():
        called["keep"] += 1

    monkeypatch.setattr(svc, "_visit_lamp_keep_auth", keep)
    monkeypatch.setattr(
        svc, "_visit_lamp_hold_door", lambda: called.__setitem__("door", called["door"] + 1)
    )
    svc._visit_lamp_on_door_faces({"names": {"xena": 0.8}, "n_faces": 1})
    assert called["keep"] == 0
    assert svc.get().visit_lamp == "amber"
    assert svc.get().user_auth is False
    assert "user" not in svc._face_auth_hold


def test_amber_keep_auth_miss_drops(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_LAMP_DRIVES_AUTH", "1")
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc._face_auth_hold["user"] = {"sim": 0.9, "ts": time.time(), "source": "click"}
    st = svc.get()
    st.user_auth = True
    svc._set(st)
    svc._visit_lamp_on_door_faces({"names": {"xena": 0.8}, "n_faces": 1})
    assert svc.get().user_auth is False
    assert svc.get().user_auth_reason == "visit_lamp"
    assert svc.get().visit_lamp == "amber"


def test_new_face_drops_secure_auth_even_if_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_LAMP_DRIVES_AUTH", "0")
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc._face_auth_hold["user"] = {"sim": 0.9, "ts": time.time(), "source": "click"}
    st = svc.get()
    st.user_auth = True
    svc._set(st)
    svc._visit_lamp_on_door_faces({"names": {"xena": 0.8}, "n_faces": 1})
    assert svc.get().visit_lamp == "amber"
    assert svc.get().user_auth is False
    assert "user" not in svc._face_auth_hold


def test_secure_end_does_not_facefind_the_desk():
    src = Path(__file__).resolve().parents[2] / "modules/presence/crissa_presence/service.py"
    text = src.read_text(encoding="utf-8")
    off = text.split("def _visit_secure_off")[1].split("def _visit_resume_ff")[0]
    assert "/v1/facefind" not in off
    assert '"id": "desk"' not in off
    assert "user_auth = False" in off


def test_drop_does_not_touch_tts_gate():
    src = Path(__file__).resolve().parents[2] / "modules/presence/crissa_presence/service.py"
    text = src.read_text(encoding="utf-8")
    block = text.split("def _visit_lamp_drop_auth")[1].split("def _visit_camera_blocked")[0]
    low = block.lower()
    assert "tts" not in low
    assert "house-tts-gate" not in low
    assert "speaking" not in low


def test_click_does_not_rust_on_fresh_mic_sound(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([{"id": "user", "kind": "user", "ts": time.time()}]),
    )
    svc._sound = {"ts": time.time(), "az_deg": 184}
    svc._last_user_voice = {"ts": time.time(), "match": True}
    svc.last_grabber = {}
    monkeypatch.setattr(svc, "_poll_grabber", lambda: {"ok": True, "lease": None})
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": True, "via": "desk_aim", "best_sim": 0.9},
    )
    out = svc.visit_lamp_click()
    assert out.get("ok") is True
    assert svc.get().visit_lamp == "sage"
    assert svc.get().visit_lamp_info.get("why") != "cannot_see"


def test_desk_look_force_skips_pan_cooldown(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._ensure_auth_pan_ts = time.time()
    out = svc._desk_look_once("user")
    assert out.get("skipped") == "pan_cooldown"
    called = {"n": 0}

    class _RS:
        @staticmethod
        def _post(path, body, timeout=30.0):
            called["n"] += 1
            called["body"] = body
            return {"frames": [], "door_return": {"ok": True}}

        @staticmethod
        def _snapshot():
            return b"\xff\xd8"

    monkeypatch.setattr("crissa_presence.room_sweep._post", _RS._post)
    monkeypatch.setattr("crissa_presence.room_sweep._snapshot", _RS._snapshot)
    monkeypatch.setattr(svc, "_match_user_in_jpegs", lambda *a, **k: 0.91)
    monkeypatch.setattr(svc, "_identify_all_in_jpegs", lambda *a, **k: {"unmatched": []})
    monkeypatch.setattr(svc, "_ensure_door_aim", lambda **k: {"ok": True})
    monkeypatch.setattr(
        "crissa_presence.seat_shot.shot_at",
        lambda *a, **k: {
            "ok": True,
            "aim_ok": True,
            "frames": [b"\xff\xd8"],
            "home": {"home": True},
        },
    )
    out = svc._desk_look_once("user", force=True)
    assert out.get("skipped") != "pan_cooldown"
    assert out.get("found") is True
    assert (out.get("aim") or {}).get("via") == "seat_shot"


def test_click_desk_look_engages_facefinder():
    src = Path(__file__).resolve().parents[2] / "modules/presence/crissa_presence/service.py"
    block = src.read_text(encoding="utf-8").split("def _desk_look_once")[1].split(
        "def ensure_user_auth"
    )[0]
    force = block.split("if force:")[1].split("else:")[0]
    assert "shot_at" in force
    assert "owner=owner" in force
    assert 'owner: str = "visit_lamp_click"' in block
    assert 'body["settle_sec"] = 0.4' not in force


def test_click_visor_lock_skips_desk_still(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([{"id": "user", "kind": "user", "ts": time.time()}]),
    )
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    monkeypatch.setattr(
        svc,
        "faceid_hud",
        lambda: {"locked": "user", "age_s": 1.2, "faces": [{"name": "user", "sim": 0.85}]},
    )
    called = {"n": 0}

    def desk(uid, force=False):
        called["n"] += 1
        called["force"] = force
        return {"found": True, "via": "desk_aim", "best_sim": 0.85}

    monkeypatch.setattr(svc, "_desk_look_once", desk)
    out = svc.visit_lamp_click()
    assert called["n"] == 1
    assert called.get("force") is True
    assert out.get("ok") is True
    assert svc.get().visit_lamp == "sage"


def test_drives_auth_always(monkeypatch):
    monkeypatch.delenv("RAIL_LAMP_DRIVES_AUTH", raising=False)
    assert vl.drives_auth() is True
    monkeypatch.setenv("RAIL_LAMP_DRIVES_AUTH", "0")
    assert vl.drives_auth() is True
    assert vl.continual_auth("amber") is True
    assert vl.continual_auth("rust") is True
    assert vl.continual_auth("sage") is False
    assert vl.sticky_counts("sage") is True
    assert vl.sticky_counts("amber") is False
    assert vl.sticky_counts("amber", force_facefind=True) is True
    assert vl.is_secure("sage") is True
    assert vl.is_secure("amber") is False
    assert vl.HOVER[vl.SAGE] == "Secure"
    assert vl.HOVER[vl.AMBER] == "not Secure"


def test_secure_hover_on_project_color():
    now = time.time()
    color, why, info = vl.project_color(
        secure=True,
        cannot_see=False,
        now=now,
    )
    assert color == "sage"
    assert why == "secure"
    assert info.get("hover") == "Secure"


def test_secure_skips_session_look(tmp_path, monkeypatch):
    """Sage keeps the camera on the door so it can still watch for a face.
    Hold is the same skip — faces do not matter; casuals stay unlocked."""
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    parked = {"n": 0}
    monkeypatch.setattr(
        svc, "_visit_lamp_hold_door", lambda: parked.__setitem__("n", parked["n"] + 1)
    )
    out = svc.set_f2f(True, reason="session_open", look=True, hour=6)
    assert out.get("skipped") == "secure"
    assert out.get("f2f") is False
    assert svc._f2f is False
    assert parked["n"] == 1
    assert svc._visit_is_secure() is True
    beam = svc.request_beam_look(hour=6, reason="session_open")
    assert beam.get("skipped") == "secure"


def test_click_live_auth_mints_sage_without_a_second_look(tmp_path, monkeypatch):
    """Legit AUTH this process → sage. Desk miss must not veto the flag."""
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([{"id": "user", "kind": "user", "ts": time.time()}]),
    )
    st = svc.get()
    st.user_auth = True
    svc._set(st)
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    called = {"desk": 0}
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: called.__setitem__("desk", 1) or {"found": False, "via": "desk_aim"},
    )
    monkeypatch.setattr(svc, "faceid_hud", lambda: {})
    out = svc.visit_lamp_click()
    assert called["desk"] == 0
    assert out.get("ok") is True
    assert out.get("via") == "auth"
    assert svc._visit_is_secure() is True
    assert svc.get().visit_lamp == "sage"


def test_click_visor_lock_still_facefinds_the_desk(tmp_path, monkeypatch):
    """Visor locked=user is not a skip — leftover FF and door FOV both mint it."""
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([{"id": "user", "kind": "user", "ts": time.time()}]),
    )
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    monkeypatch.setattr(
        svc,
        "faceid_hud",
        lambda: {
            "locked": "user",
            "age_s": 0.3,
            "faces": [{"name": "user", "sim": 0.84}],
        },
    )
    called = {"desk": 0}

    def desk(uid, **k):
        called["desk"] += 1
        called["force"] = k.get("force")
        return {"found": True, "via": "desk_aim", "best_sim": 0.84}

    monkeypatch.setattr(svc, "_desk_look_once", desk)
    out = svc.visit_lamp_click()
    assert called["desk"] == 1
    assert called.get("force") is True
    assert out.get("ok") is True
    assert svc.get().visit_lamp == "sage"


def test_click_parks_on_the_door_after_auth(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "crissa_presence.visitors_log.seen_since_midnight",
        lambda **kw: _census([{"id": "user", "kind": "user", "ts": time.time()}]),
    )
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    parked = {"n": 0}
    monkeypatch.setattr(
        svc, "_visit_lamp_hold_door", lambda: parked.__setitem__("n", parked["n"] + 1)
    )
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {
            "found": True,
            "via": "desk_aim",
            "best_sim": 0.9,
            "returned_door": True,
        },
    )
    out = svc.visit_lamp_click()
    assert out.get("ok") is True
    assert svc.get().visit_lamp == "sage"
    assert svc.get().visit_lamp_info.get("hover") == "Secure"
    assert parked["n"] == 1
    assert out.get("returned_door") is True


def test_face_qualifies_strict_and_50x80_does_not_trip():
    assert vl.face_qualifies(None, [0, 0, 80, 80]) is False
    assert vl.face_qualifies(0.9, None) is False
    assert vl.face_qualifies(0.5, [0, 0, 80, 80]) is False
    # 50×80: shortest side 50 < 60
    assert vl.face_qualifies(0.9, [0, 0, 80, 50]) is False
    assert vl.face_qualifies(0.9, [0, 0, 80, 80]) is True


def test_secure_stays_sage_when_cannot_see():
    color, why, info = vl.project_color(secure=True, cannot_see=True)
    assert color == "sage"
    assert why == "secure"
    assert info.get("cannot_see") is True


def test_rust_only_when_insecure_and_blind():
    color, why, _ = vl.project_color(secure=False, cannot_see=True)
    assert color == "rust"
    assert why == "cannot_see"


def test_click_toggles_off_without_a_look(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    st = svc.get()
    st.user_auth = True
    svc._set(st)
    called = {"desk": 0}
    monkeypatch.setattr(
        svc, "_desk_look_once", lambda uid, **k: called.__setitem__("desk", 1)
    )
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    out = svc.visit_lamp_click()
    assert out.get("via") == "toggle_off"
    assert out.get("ok") is True
    assert called["desk"] == 0
    assert svc._visit_is_secure() is False
    assert svc.get().visit_lamp == "amber"
    assert svc.get().user_auth is False


def test_sage_holds_user_auth_on_presence(tmp_path, monkeypatch):
    """user 2026-09-09: sage is User mode on the same object. Face minted
    sage; empty door FOV must not Guest him while the lamp is green."""
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    assert svc._visit_is_secure() is True
    assert svc.get().visit_lamp == "sage"
    assert svc.get().user_auth is True
    assert svc.get().user_auth_reason == "secure"
    assert "user" in (svc._face_auth_hold or {})


def test_visit_is_secure_reads_the_flag_not_colour(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._visit_secure = True
    assert svc._visit_is_secure() is True
    svc._visit_secure = False
    assert svc._visit_is_secure() is False


# ---- keyboard hold (user 2026-09-06) ---------------------------------------
def _hold(svc, monkeypatch, ok=True):
    monkeypatch.setattr(vl, "verify_password", lambda user, pw: bool(ok))
    svc._visit_hold_last_try = 0.0
    return svc.visit_lamp_hold("whatever")


def test_hold_needs_the_password_and_mints_sage(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._visit_boot_unproven = False
    svc._set(RoomPresence())
    out = _hold(svc, monkeypatch, ok=False)
    assert out["ok"] is False and out["held"] is False
    assert svc.get().visit_lamp == "amber"
    out = _hold(svc, monkeypatch, ok=True)
    assert out["ok"] is True and out["held"] is True
    st = svc.get()
    assert st.visit_lamp == "sage"
    assert st.visit_lamp_info.get("hold") is True
    assert st.visit_lamp_info.get("hover") == vl.HOVER_HELD
    assert svc.visit_lamp_pub()["hold"] is True
    assert st.user_auth is True, "sudo hold is User mode"


def test_hold_survives_door_faces_and_the_tripwire(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._visit_boot_unproven = False
    svc._set(RoomPresence())
    assert _hold(svc, monkeypatch)["ok"]
    svc._visit_lamp_on_door_faces(
        {"names": {"wanda": 1}, "n_faces": 1, "unmatched": [{"bbox": [0, 0, 80, 80]}]}
    )
    assert svc.get().visit_lamp == "sage"
    assert svc._visit_secure_off(reason="entry_face") is False
    assert svc._visit_is_secure() is True


def test_one_click_releases_the_hold_and_goes_bau(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._visit_boot_unproven = False
    svc._set(RoomPresence())
    assert _hold(svc, monkeypatch)["ok"]
    out = svc.visit_lamp_click()
    assert out.get("via") == "toggle_off"
    assert svc.get().visit_lamp == "amber"
    assert svc._visit_hold is False
    # BAU again: a door face ends a plain Secure as before
    _make_sage(svc, monkeypatch)
    svc._visit_lamp_on_door_faces({"names": {}, "n_faces": 1, "unmatched": [{"bbox": [0, 0, 80, 80]}]})
    assert svc.get().visit_lamp == "amber"


def test_release_keeps_secure_but_drops_the_hold(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._visit_boot_unproven = False
    svc._set(RoomPresence())
    assert _hold(svc, monkeypatch)["ok"]
    out = svc.visit_lamp_release()
    assert out["released"] is True and svc._visit_hold is False
    assert svc.get().visit_lamp == "sage"
    assert svc.get().visit_lamp_info.get("hold") is False


def test_hold_attempts_are_rate_limited(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._visit_boot_unproven = False
    svc._set(RoomPresence())
    monkeypatch.setattr(vl, "verify_password", lambda user, pw: False)
    svc._visit_hold_last_try = 0.0
    assert svc.visit_lamp_hold("a")["error"] == "password refused"
    assert "again" in svc.visit_lamp_hold("b")["error"]


def test_verify_password_refuses_wrong_and_empty():
    assert vl.verify_password("agent", "") is False
    assert vl.verify_password("", "x") is False
    assert vl.verify_password("agent", "definitely-not-the-password") is False


# ── camera joystick (user 2026-09-06): AUTH required, never touched ─────────

def _joy(svc, monkeypatch, *, authed: bool):
    from crissa_presence import beam_look as bl, ptz
    calls: list = []

    def fake_post(path, body, timeout=8.0):
        calls.append((path, dict(body)))
        if path == "/v1/lease":
            return {"ok": True, "lease_id": "Ljoy"}
        return {"ok": True}
    monkeypatch.setattr(bl, "_cam_post", fake_post)
    monkeypatch.setattr(ptz, "get_ctrl", lambda dev, name: {"pan_absolute": 432000, "tilt_absolute": 0}[name])
    st = RoomPresence()
    st.user_auth = authed
    svc._set(st)
    return calls


def test_joystick_needs_auth_and_never_changes_it(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    calls = _joy(svc, monkeypatch, authed=False)
    out = svc.camera_aim("nudge", 3.0, 0.0)
    assert out["ok"] is False and out["error"] == "not_authed"
    assert calls == []                       # no lease, no aim without AUTH
    calls = _joy(svc, monkeypatch, authed=True)
    out = svc.camera_aim("nudge", 3.0, -2.0)
    assert out["ok"] is True
    aim = [b for p, b in calls if p == "/v1/aim"][0]
    assert aim["pan"] == 432000 + 3 * 3600 and aim["tilt"] == -2 * 3600
    assert aim["lease_id"] == "Ljoy"
    assert svc.get().user_auth is True       # "does NOT drop AUTH"
    assert not out.get("secure_dropped")
    # a second nudge renews the same lease
    svc.camera_aim("nudge", -1.0, 0.0)
    leases = [b for p, b in calls if p == "/v1/lease"]
    assert leases[-1]["lease_id"] == "Ljoy"
    # home releases the lease and resumes the camera's own watchdogs
    out = svc.camera_aim("home")
    assert out["ok"] is True and out["released"] is True
    assert ("/v1/lease/release", {"lease_id": "Ljoy"}) in calls
    assert any(p == "/v1/resume" for p, _ in calls)
    assert svc.get().user_auth is True


def test_sage_without_hold_nudge_keeps_secure_until_home(tmp_path, monkeypatch):
    """Joystick work keeps Secure+AUTH; home returns the token and drops sage."""
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    assert svc.get().user_auth is True
    _joy(svc, monkeypatch, authed=True)
    svc._visit_secure = True
    svc._visit_hold = False
    svc._set(svc.get())
    out = svc.camera_aim("nudge", 3.0, 0.0)
    assert out["ok"] is True
    assert not out.get("secure_dropped")
    assert svc._visit_is_secure() is True
    assert svc.get().user_auth is True
    home = svc.camera_aim("home")
    assert home.get("secure_dropped") is True
    assert svc._visit_is_secure() is False
    assert svc.get().visit_lamp == "amber"
    assert home.get("note") and "SECURE OFF" in home["note"]


def test_faceid_hold_guests_after_home_grace(tmp_path, monkeypatch):
    """After the token is back on the door, BAU 5 s empty FOV Guests you."""
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc._face_auth_hold["user"] = {"sim": 0.9, "ts": time.time(), "source": "desk_ff"}
    _joy(svc, monkeypatch, authed=True)
    svc._visit_secure = True
    svc._visit_hold = False
    svc._set(svc.get())
    assert svc.camera_aim("nudge", 3.0, 0.0)["ok"] is True
    assert svc._visit_is_secure() is True
    home = svc.camera_aim("home")
    assert home.get("secure_dropped") is True
    assert svc.get().visit_lamp == "amber"
    assert svc.get().user_auth is True
    assert "user" in svc._face_auth_hold
    svc._conv_auth_lock_ts = time.time() - 6.0
    svc._face_auth_hold["user"]["ts"] = time.time() - 6.0
    svc.last_grabber = {"at_door": True}
    monkeypatch.setattr(
        svc, "faceid_hud",
        lambda: {"n_faces": 0, "locked": None, "faces": [], "auth_hold": False},
    )
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["user_auth"] is False
    assert "user" not in svc._face_auth_hold


def test_joystick_holds_auth_until_the_token_is_back(tmp_path, monkeypatch):
    """Manual aim: AUTH as if Secure until the token is back (matrix 2.8)."""
    svc = _svc(tmp_path, monkeypatch)
    svc.last_grabber = {"at_door": False}
    monkeypatch.setattr(
        svc, "faceid_hud",
        lambda: {"n_faces": 0, "locked": None, "faces": [], "auth_hold": False},
    )
    svc._face_auth_hold["user"] = {"sim": 0.9, "ts": time.time() - 6.0, "source": "desk_ff"}
    svc._cam_token = {
        "owner": svc.CAM_JOY_OWNER,
        "lease_id": "Ljoy",
        "until": time.time() + 300,
        "since": time.time(),
        "why": "nudge",
    }
    assert svc.cam_holder() == "user_joystick"
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["user_auth"] is True
    assert "user" in svc._face_auth_hold

    svc._cam_token = None
    svc.last_grabber = {"at_door": True}
    svc._face_auth_hold["user"]["ts"] = time.time() - 6.0
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["user_auth"] is False
    assert "user" not in svc._face_auth_hold


def test_joystick_nudge_continues_after_guest_while_holding(tmp_path, monkeypatch):
    """AUTH gated the first nudge; the lease gates the rest (wrap finding 6)."""
    svc = _svc(tmp_path, monkeypatch)
    calls = _joy(svc, monkeypatch, authed=True)
    out = svc.camera_aim("nudge", 3.0, 0.0)
    assert out["ok"] is True
    assert svc.cam_holder() == "user_joystick"
    st = svc.get()
    st.user_auth = False
    svc._set(st)
    n = len(calls)
    out = svc.camera_aim("nudge", -1.0, 0.0)
    assert out["ok"] is True
    assert len(calls) > n
    assert svc.cam_holder() == "user_joystick"
    assert svc.get().user_auth is False


def test_insecure_sticky_at_empty_door_expires(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc.last_grabber = {"at_door": True}
    monkeypatch.setattr(
        svc, "faceid_hud",
        lambda: {"n_faces": 0, "locked": None, "faces": [], "auth_hold": False},
    )
    svc._face_auth_hold["user"] = {"sim": 0.9, "ts": time.time() - 6.0, "source": "desk_ff"}
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["user_auth"] is False
    assert "user" not in svc._face_auth_hold


def test_sage_minted_hold_expires_immediately_when_insecure(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._face_auth_hold["user"] = {"sim": None, "ts": time.time(), "source": "secure_hold"}
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["user_auth"] is False
    assert "user" not in svc._face_auth_hold


def test_sage_empty_door_keeps_auth(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc.last_grabber = {"at_door": True}
    monkeypatch.setattr(
        svc, "faceid_hud",
        lambda: {"n_faces": 0, "locked": None, "faces": [], "auth_hold": False},
    )
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["user_auth"] is True


def test_named_other_face_guests_when_insecure(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._face_auth_hold["user"] = {"sim": 0.9, "ts": time.time(), "source": "desk_ff"}
    monkeypatch.setattr(
        svc, "faceid_hud",
        lambda: {"n_faces": 1, "locked": "wanda", "faces": [{"name": "wanda"}]},
    )
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert blk["user_auth"] is False
    assert "user" not in svc._face_auth_hold


def test_keyboard_hold_stays_green_on_a_nudge(tmp_path, monkeypatch):
    """Double-press hold: faces do not matter; casuals stay unlocked."""
    svc = _svc(tmp_path, monkeypatch)
    svc._visit_boot_unproven = False
    svc._set(RoomPresence())
    assert _hold(svc, monkeypatch)["ok"]
    assert svc.get().user_auth is True
    calls = _joy(svc, monkeypatch, authed=True)
    svc._visit_secure = True
    svc._visit_hold = True
    svc._set(svc.get())
    out = svc.camera_aim("nudge", 3.0, 0.0)
    assert out["ok"] is True
    assert not out.get("secure_dropped")
    assert svc._visit_is_secure() is True
    assert svc.get().visit_lamp == "sage"
    assert svc.get().visit_lamp_info.get("hold") is True
    assert svc.get().user_auth is True
    assert svc.get().camera_note is None
    home = svc.camera_aim("home")
    assert not home.get("secure_dropped"), "Hold survives token return"
    assert svc._visit_is_secure() is True
    assert svc.get().user_auth is True
    lease = [b for p, b in calls if p == "/v1/lease"][-1]
    assert lease["ttl_sec"] == svc.CAM_JOY_TTL, "hold is not door-watch; keep the aim"


def test_joystick_tilt_is_clamped_to_the_gimbal(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    calls = _joy(svc, monkeypatch, authed=True)
    svc.camera_aim("nudge", 0.0, 200.0)
    aim = [b for p, b in calls if p == "/v1/aim"][0]
    assert aim["tilt"] == svc.CAM_TILT_MAX


def _door_user(svc):
    svc._visit_lamp_on_door_faces({"names": {"user": 0.88}, "n_faces": 1})
    svc._visit_door_tripwire(1)


def test_door_trip_settle_holds_sage_at_one_second(tmp_path, monkeypatch):
    """07:37: pan still named user. at_door True is not enough before 8 s."""
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc._visit_secure_since = time.time()
    svc._door_trip_logged = None
    _door_user(svc)
    assert svc._visit_is_secure() is True
    assert svc.get().user_auth is True
    assert svc._door_trip_logged == "settle"


def test_door_trip_after_settle_at_door_drops_sage(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc._visit_secure_since = time.time() - 9.0
    svc._door_trip_logged = None
    svc._visit_lamp_on_door_faces({"names": {"user": 0.88}, "n_faces": 1})
    assert svc._visit_is_secure() is False
    assert svc.get().visit_lamp == "amber"
    assert svc.get().user_auth is False


def test_door_trip_off_door_holds_sage_after_settle(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    monkeypatch.setattr(
        svc, "_poll_grabber", lambda: {"ok": True, "at_door": False, "pan_actual": 300000}
    )
    svc._last_grabber_ts = 0.0
    lines = []
    monkeypatch.setattr(
        "builtins.print",
        lambda *a, **k: lines.append(" ".join(str(x) for x in a)),
    )
    _door_user(svc)
    assert svc._visit_is_secure() is True
    assert any("off_door" in x for x in lines)


def test_door_trip_sudo_logs_once_and_never_drops(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc._visit_hold = True
    lines = []
    monkeypatch.setattr(
        "builtins.print",
        lambda *a, **k: lines.append(" ".join(str(x) for x in a)),
    )
    _door_user(svc)
    _door_user(svc)
    assert svc._visit_is_secure() is True
    sudo = [x for x in lines if "under sudo" in x]
    assert len(sudo) == 1


def test_door_trip_poll_fail_holds_sage(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    monkeypatch.setattr(svc, "_poll_grabber", lambda: {"ok": False, "error": "down"})
    svc._last_grabber_ts = 0.0
    _door_user(svc)
    assert svc._visit_is_secure() is True
    monkeypatch.setattr(svc, "_poll_grabber", lambda: None)
    svc._last_grabber_ts = 0.0
    svc._door_trip_logged = None
    _door_user(svc)
    assert svc._visit_is_secure() is True


def test_grabber_fresh_reuses_one_second_and_ignores_stale(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    n = {"i": 0}

    def poll():
        n["i"] += 1
        return {"ok": True, "at_door": True, "n": n["i"]}

    monkeypatch.setattr(svc, "_poll_grabber", poll)
    a = svc._grabber_fresh(max_age=1.0)
    b = svc._grabber_fresh(max_age=1.0)
    assert n["i"] == 1 and a is b
    svc.last_grabber = {"ok": True, "at_door": False}
    svc._last_grabber_ts = time.time() - 5.0
    c = svc._grabber_fresh(max_age=1.0)
    assert n["i"] == 2
    assert c.get("at_door") is True


def test_door_trip_env_settle_holds_at_ten_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_SECURE_TRIP_SETTLE_S", "20")
    svc = _svc(tmp_path, monkeypatch)
    _make_sage(svc, monkeypatch)
    svc._visit_secure_since = time.time() - 10.0
    svc._door_trip_logged = None
    _door_user(svc)
    assert svc._visit_is_secure() is True
    assert svc._door_trip_logged == "settle"
