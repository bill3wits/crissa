"""Register's camera token (the User 2026-10-02, Init step 3).

Under Secure, presence pulled the camera home from every holder but the joystick, so Register (and Init's room
sweep / Crissa-seat look) had to click the lamp off first. Now `register` sits on the ladder below the joystick and
above Secure's door watch; presence grants it only in User mode; its time limit is config; giving it back homes the
camera and never drops Secure or Hold. Off (RAIL_REGISTER_TOKEN unset) nothing changes. Pinned in Guest, Secure
and Secure Hold.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

from test_camera_token_service import _authed, _cam  # noqa: E402
from test_visit_lamp import _svc  # noqa: E402

from crissa_presence import beam_look as bl, camera_token as ct  # noqa: E402


@pytest.fixture
def on(monkeypatch, tmp_path):
    monkeypatch.setenv("RAIL_REGISTER_TOKEN", "1")
    cfg = tmp_path / "register-token.json"
    cfg.write_text(json.dumps({"ttl_sec": 150}))
    monkeypatch.setenv("RAIL_REGISTER_CONFIG", str(cfg))
    monkeypatch.delenv("RAIL_REGISTER_TTL", raising=False)


def _secure(svc, monkeypatch, hold=False):
    monkeypatch.setattr(svc, "_visit_is_secure", lambda: True)
    svc._visit_secure = True
    svc._visit_hold = hold


def _door_watch(svc, monkeypatch):
    posted: list = []
    monkeypatch.setattr(svc, "_grabber_post", lambda url, body, timeout=20.0: (posted.append(url), {"ok": True})[1])
    monkeypatch.setattr(svc, "_poll_grabber", lambda: {"ok": True, "lease": None})
    monkeypatch.setattr(svc, "_visit_lamp_hold_door", type(svc)._visit_lamp_hold_door.__get__(svc))
    monkeypatch.setattr(svc, "_ensure_door_aim", type(svc)._ensure_door_aim.__get__(svc))
    svc.camera.mode = "http"
    svc.last_grabber = {"at_door": False}
    return posted


def test_off_the_ladder_and_the_verb_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("RAIL_REGISTER_TOKEN", raising=False)
    assert ct.ladder() == ct.LADDER and ct.rank("register") == -1
    svc = _svc(tmp_path, monkeypatch)
    _cam(monkeypatch)
    _authed(svc)
    assert svc.register_take()["error"] == "off"
    assert svc._door_spared() == {"user_joystick"}


def test_the_rank_is_below_the_joystick_and_above_secures_door_watch(on):
    assert ct.ladder()[:3] == ("user_joystick", "register", "secure_glance")
    assert ct.outranks("register", "secure_glance") and ct.outranks("register", "beam_look")
    assert ct.outranks("user_joystick", "register") and not ct.outranks("secure_glance", "register")


def test_guest_is_refused(tmp_path, monkeypatch, on):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr("crissa_presence.house_user.enrolled", lambda: True)   # a pack with nobody enrolled is on first boot: Register may take
    _cam(monkeypatch)
    _authed(svc, False)
    out = svc.register_take()
    assert out["ok"] is False and out["error"] == "not_user_mode" and svc.cam_holder() is None


def test_user_mode_gets_it_for_the_configured_time(tmp_path, monkeypatch, on):
    svc = _svc(tmp_path, monkeypatch)
    calls, _ = _cam(monkeypatch)
    _authed(svc)
    out = svc.register_take("init room sweep")
    assert out["ok"] and out["ttl"] == 150.0 and svc.cam_holder() == "register"
    assert [b for p, b in calls if p == "/v1/lease"][-1]["ttl_sec"] == 150.0
    monkeypatch.setenv("RAIL_REGISTER_CONFIG", str(tmp_path / "absent.json"))
    svc.register_give_back()
    assert svc.register_take()["error"] == "no_ttl"            # no config, no token: the limit is never code


@pytest.mark.parametrize("hold", [False, True], ids=["secure", "secure_hold"])
def test_under_secure_the_door_watch_waits_and_give_back_homes_without_touching_secure(tmp_path, monkeypatch, on, hold):
    svc = _svc(tmp_path, monkeypatch)
    _cam(monkeypatch)
    _authed(svc)
    _secure(svc, monkeypatch, hold=hold)
    posted = _door_watch(svc, monkeypatch)
    assert svc.register_take()["ok"] and svc.cam_holder() == "register"
    for _ in range(10):
        svc._beam_maybe_rest()
    svc._visit_lamp_hold_door()
    assert svc._ensure_door_aim() == {"ok": True, "skipped": "register"}
    assert not [u for u in posted if "return_door" in u or "facelock" in u], posted
    assert svc.door_glance().get("skipped") == "held:register"          # Secure's glance does not take it
    rested: list = []
    monkeypatch.setattr(bl, "rest", lambda lid=None: (rested.append(lid), {"ok": True})[1])
    gb = svc.register_give_back()
    assert gb["released"] and rested, "giving it back sends the camera home"
    assert "secure_dropped" not in gb
    assert svc._visit_secure is True and svc._visit_hold is hold, "Secure and Hold are exactly as they were"


def test_the_joystick_still_outranks_register(tmp_path, monkeypatch, on):
    svc = _svc(tmp_path, monkeypatch)
    _cam(monkeypatch)
    _authed(svc)
    assert svc.register_take()["ok"]
    assert svc.camera_aim("nudge", 2.0, 0.0)["ok"] and svc.cam_holder() == "user_joystick"
    assert svc.register_take()["ok"] is False                                # it does not take it back


def test_the_ttl_lapse_never_drops_secure(tmp_path, monkeypatch, on):
    svc = _svc(tmp_path, monkeypatch)
    _cam(monkeypatch)
    _authed(svc)
    _secure(svc, monkeypatch, hold=True)
    assert svc.register_take()["ok"]
    svc._cam_token["until"] = 0.0
    assert svc.cam_holder() is None and svc._visit_secure is True and svc._visit_hold is True


def test_inits_look_aims_with_the_tokens_lease_and_leaves_it_to_the_holder(monkeypatch):
    """Init's Crissa-seat look and room sweep run under Register's token: the helpers aim with its lease and never take
    or release one of their own (the holder gives it back, which homes the camera)."""
    from crissa_presence import room_sweep as rs, seat_shot

    posted: list = []
    monkeypatch.setattr(rs, "_post", lambda path, body, timeout=8.0: (posted.append((path, dict(body))), {"ok": True, "lease_id": "Lown"})[1])
    monkeypatch.setattr(rs, "_snapshot", lambda: b"jpeg")
    monkeypatch.setattr(seat_shot, "go_home", lambda r, lid=None: {"ok": True, "lease_id": lid})
    monkeypatch.setattr(seat_shot.time, "sleep", lambda s: None)
    with ct.borrow("Ltoken"):
        out = seat_shot.shot_at(1000, 0, owner="init-crissa-seat", n=1)
    paths = [p for p, _ in posted]
    assert out["ok"] and out["borrowed"] and out["lease_released"] is False
    assert "/v1/lease" not in paths and "/v1/lease/release" not in paths
    assert all(b.get("lease_id") == "Ltoken" for p, b in posted if "lease_id" in b)
    posted.clear()
    out = seat_shot.shot_at(1000, 0, owner="seat_shot", n=1)            # no token: today's path, its own lease
    assert [p for p, _ in posted][0] == "/v1/lease" and "/v1/lease/release" in [p for p, _ in posted]
    assert ct.borrowed() is None


def _store(tmp_path, monkeypatch, role):
    root = tmp_path / "identity"
    (root / "people" / "someone").mkdir(parents=True)
    (root / "people" / "someone" / "person.json").write_text(json.dumps({"role": role}))
    monkeypatch.setenv("HOUSE_ID_ROOT", str(root))


def test_guest_with_a_user_enrolled_is_refused(on, tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, "user")
    svc = _svc(tmp_path, monkeypatch)
    _cam(monkeypatch)
    out = svc.register_take()
    assert out["ok"] is False and out["error"] == "not_user_mode"


def test_first_boot_with_no_user_enrolled_takes_the_token(on, tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, "guest")
    svc = _svc(tmp_path, monkeypatch)
    _cam(monkeypatch)
    out = svc.register_take()
    assert out.get("error") != "not_user_mode"

