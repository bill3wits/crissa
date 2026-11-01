"""Round 17 (2026-10-13; the User 2026-10-11 and 2026-10-13 on .101's fixed Elgato): Secure needs a camera that can
turn to the door. On a fixed camera (or none) in a pack presence offers no Secure: the lamp's info says so with the
reason and a short explanation, a click is refused without touching AUTH or the lamp, and the restart's guest latch -
which waits for a Secure click that can no longer come - does not hold the User's face ladder. A camera that turns,
one not yet measured, and the house keep today's Secure. Kill: RAIL_FIXED_CAM_SECURE=0."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence import fixed_cam  # noqa: E402
from crissa_presence.service import PresenceService  # noqa: E402


class _FakeCam:
    mode = "dir"
    last_error = None

    def grab(self):
        return None

    def grab_buffer(self, **kw):
        return []


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    for k in ("RAIL_CAMERA_FIXED", "RAIL_FIXED_CAM_SECURE", "CRISSA_NO_CAMERA", "RAIL_CAMERA_PTZ_BY_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(fixed_cam, "is_house", lambda: False)
    monkeypatch.setattr(fixed_cam, "kind_path", lambda home=None: tmp_path / "camera-kind.json")
    yield tmp_path


def _record(tmp_path, rec: dict) -> None:
    (tmp_path / "camera-kind.json").write_text(json.dumps(rec), encoding="utf-8")


# ── the offer ───────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_fixed_camera_is_offered_no_secure_with_the_reason_and_the_explanation():
    got = fixed_cam.secure_offer({"camera_ptz": "digital", "pan_seen": True})       # .101's Elgato: a crop, one view
    assert got["ok"] is False and got["kind"] == "fixed"
    assert "turn to the door" in got["why"] and "password hold" in got["about"]


def test_the_record_stands_in_for_a_health_not_to_hand(clean):
    _record(clean, {"camera_name": "a webcam", "kind": "digital"})
    assert fixed_cam.secure_offer(None)["ok"] is False
    _record(clean, {"camera_name": "a gimbal", "kind": "motor"})
    assert fixed_cam.secure_offer(None)["ok"] is True
    _record(clean, {"kind": "motor", "fixed": True, "how": "measured"})               # a measurement wins
    assert fixed_cam.secure_offer(None)["ok"] is False
    _record(clean, {"kind": "digital", "fixed": False, "how": "measured: it moved"})
    assert fixed_cam.secure_offer(None)["ok"] is False                  # round 27: a crop pan is fixed by its kind


def test_a_camera_that_turns_or_one_not_yet_measured_keeps_secure():
    assert fixed_cam.secure_offer({"camera_ptz": "motor", "pan_seen": True})["ok"] is True
    assert fixed_cam.secure_offer(None)["ok"] is True                                  # before Setup: nothing known
    assert fixed_cam.secure_offer({"pan_seen": True})["ok"] is True


def test_no_camera_is_offered_no_secure(monkeypatch):
    monkeypatch.setenv("CRISSA_NO_CAMERA", "1")
    got = fixed_cam.secure_offer({"camera_ptz": "motor"})
    assert got["ok"] is False and got["kind"] == "none" and "no camera" in got["why"]


def test_the_house_and_the_switch_say_nothing(monkeypatch):
    monkeypatch.setenv("RAIL_FIXED_CAM_SECURE", "0")
    assert fixed_cam.secure_offer({"camera_ptz": "digital"}) is None
    monkeypatch.delenv("RAIL_FIXED_CAM_SECURE")
    monkeypatch.setattr(fixed_cam, "is_house", lambda: True)
    assert fixed_cam.secure_offer({"camera_ptz": "digital"}) is None
    monkeypatch.setenv("RAIL_CAMERA_FIXED", "1")
    assert fixed_cam.secure_offer({}) is None


# ── the service ──────────────────────────────────────────────────────────────────────────────────────────────────────
def _svc(monkeypatch, ptz="digital"):
    monkeypatch.setenv("RAIL_OCCUPANCY", "0")
    svc = PresenceService(camera=_FakeCam())
    svc.last_grabber = {"camera_ptz": ptz, "pan_seen": True}
    monkeypatch.setattr(svc, "_user_id", lambda: "user")
    return svc


def test_the_lamps_info_carries_the_offer(monkeypatch):
    svc = _svc(monkeypatch)
    st = svc.get()
    svc._project_visit_lamp(st)
    off = st.visit_lamp_info["secure_offer"]
    assert off["ok"] is False and off["kind"] == "fixed"
    svc2 = _svc(monkeypatch, ptz="motor")
    st2 = svc2.get()
    svc2._project_visit_lamp(st2)
    assert st2.visit_lamp_info["secure_offer"]["ok"] is True


def test_the_house_lamp_info_is_as_before(monkeypatch):
    monkeypatch.setattr(fixed_cam, "is_house", lambda: True)
    svc = _svc(monkeypatch)
    st = svc.get()
    svc._project_visit_lamp(st)
    assert "secure_offer" not in st.visit_lamp_info


def test_a_click_on_a_fixed_camera_is_refused_and_nothing_moves(monkeypatch):
    svc = _svc(monkeypatch)
    looked = []
    monkeypatch.setattr(svc, "_visit_lamp_click_locked", lambda silent: looked.append(1) or {"ok": True})
    st = svc.get()
    auth_before = bool(getattr(st, "user_auth", False))
    out = svc.visit_lamp_click()
    assert out["ok"] is False and out["error"] == "secure_not_offered" and "turn to the door" in out["why"]
    assert looked == [], "no FaceFinder look, no Secure"
    assert not getattr(svc, "_visit_secure", False) and bool(getattr(svc.get(), "user_auth", False)) == auth_before


def test_secure_already_on_is_still_turned_off_by_a_click(monkeypatch):
    svc = _svc(monkeypatch)
    svc._visit_secure = True
    taken = []
    monkeypatch.setattr(svc, "_visit_lamp_click_locked", lambda silent: taken.append(1) or {"ok": True, "via": "off"})
    assert svc.visit_lamp_click()["ok"] is True and taken == [1]


def test_a_camera_that_turns_clicks_as_before(monkeypatch):
    svc = _svc(monkeypatch, ptz="motor")
    taken = []
    monkeypatch.setattr(svc, "_visit_lamp_click_locked", lambda silent: taken.append(1) or {"ok": True})
    svc.visit_lamp_click()
    assert taken == [1]
    monkeypatch.setenv("RAIL_FIXED_CAM_SECURE", "0")
    svc2 = _svc(monkeypatch)
    monkeypatch.setattr(svc2, "_visit_lamp_click_locked", lambda silent: taken.append(2) or {"ok": True})
    svc2.visit_lamp_click()
    assert taken == [1, 2], "the switch off: round 16's click"


def _ladder(svc, monkeypatch):
    looks = []
    monkeypatch.setattr(svc, "_auth_hunt_blocked_locked", lambda: False)
    monkeypatch.setattr(svc, "_desk_look_once", lambda uid, force, owner: looks.append(owner) or {"found": False})
    svc._guest_until_secure = True
    svc._run_reauth_ladder("user")
    return looks


def test_the_restart_latch_does_not_hold_his_face_ladder_on_a_fixed_camera(monkeypatch):
    assert _ladder(_svc(monkeypatch), monkeypatch) == ["reauth_ladder"]


def test_the_restart_latch_holds_where_secure_is_offered(monkeypatch):
    assert _ladder(_svc(monkeypatch, ptz="motor"), monkeypatch) == []
    monkeypatch.setenv("RAIL_FIXED_CAM_SECURE", "0")
    assert _ladder(_svc(monkeypatch), monkeypatch) == []
