"""The broker on the service: one token, passed hand to hand.

Joystick beats a look and the look stops on its next tick; the Secure watch
waits for the joystick; a non-holder's give-back is a no-op; the joystick's
hold is short under Secure so the door watch is not blinded."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
sys.path.insert(0, os.path.dirname(__file__))

from test_visit_lamp import _svc  # noqa: E402

from crissa_presence import beam_look as bl, ptz  # noqa: E402
from crissa_presence.schema import RoomPresence  # noqa: E402


def _cam(monkeypatch):
    calls: list = []
    state = {"lease": None, "n": 0}

    def fake_post(path, body, timeout=8.0):
        calls.append((path, dict(body)))
        if path == "/v1/lease":
            lid = body.get("lease_id")
            if lid and state["lease"] and state["lease"]["lease_id"] == lid:
                state["lease"] = {"lease_id": lid, "owner": body["owner"]}
                return {"ok": True, "lease_id": lid, "owner": body["owner"], "renewed": True}
            if state["lease"] and not lid:
                return {"ok": False, "error": "camera_leased", "owner": state["lease"]["owner"]}
            state["n"] += 1
            lid = f"L{state['n']}"
            state["lease"] = {"lease_id": lid, "owner": body["owner"]}
            return {"ok": True, "lease_id": lid, "owner": body["owner"]}
        if path == "/v1/lease/release":
            state["lease"] = None
        if path == "/v1/pause" and body.get("steal"):
            state["lease"] = None
        return {"ok": True}

    monkeypatch.setattr(bl, "_cam_post", fake_post)
    monkeypatch.setattr(ptz, "get_ctrl", lambda dev, name: {"pan_absolute": 432000, "tilt_absolute": 0}[name])
    return calls, state


def _authed(svc, on=True):
    st = RoomPresence()
    st.user_auth = on
    svc._set(st)


def test_the_joystick_takes_the_token_from_a_look_and_the_look_stops(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    calls, cam = _cam(monkeypatch)
    _authed(svc)
    tok = svc.cam_take("beam_look", 45, why="session_open")
    assert tok["ok"] and tok["act"] == "grant" and svc.cam_holder() == "beam_look"
    out = svc.camera_aim("nudge", 3.0, 0.0)
    assert out["ok"] is True
    # same lease_id, re-owned: the grabber saw a renew with the new owner
    assert svc.cam_holder() == "user_joystick"
    assert cam["lease"] == {"lease_id": tok["lease_id"], "owner": "user_joystick"}
    assert out["lease_id"] == tok["lease_id"]
    # the look's next renew is the loser's answer
    r = svc.cam_renew("beam_look")
    assert r["ok"] is False and r["error"] == "lost" and r["owner"] == "user_joystick"
    # a non-holder's give-back is a no-op: the camera is not homed under the User
    gb = svc.cam_give_back("beam_look")
    assert gb["released"] is False and svc.cam_holder() == "user_joystick"
    assert not [p for p, b in calls if p == "/v1/lease/release"]
    # home gives it back
    out = svc.camera_aim("home")
    assert out["released"] is True and svc.cam_holder() is None
    assert [p for p, b in calls if p == "/v1/lease/release"]


def test_a_lower_asker_is_refused_with_the_holders_name(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _cam(monkeypatch)
    _authed(svc)
    assert svc.camera_aim("nudge", 1.0, 0.0)["ok"] is True
    r = svc.cam_take("beam_look", 45, why="session_open")
    assert r["ok"] is False and r["error"] == "camera_leased" and r["owner"] == "user_joystick"
    r = svc.cam_take("ff_mode", 8, why="chase")
    assert r["ok"] is False and r["owner"] == "user_joystick"
    # the chase outranks a look
    svc.camera_aim("home")
    assert svc.cam_take("beam_look", 45, why="look")["act"] == "grant"
    assert svc.cam_take("ff_mode", 8, why="chase")["act"] == "handoff"
    assert svc.cam_holder() == "ff_mode"
    # and the Secure watch outranks the chase (Law 2)
    assert svc.cam_take("secure_glance", 6, why="glance")["act"] == "handoff"


def test_the_secure_watch_waits_while_the_joystick_holds_and_the_hold_is_short_under_secure(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    calls, cam = _cam(monkeypatch)
    _authed(svc)
    assert svc.camera_aim("nudge", 1.0, 0.0)["ok"] is True
    lease = [b for p, b in calls if p == "/v1/lease"][-1]
    assert lease["ttl_sec"] == svc.CAM_JOY_TTL and svc.CAM_JOY_TTL >= 120, "the User holds while aiming"
    g = svc.door_glance()
    assert g.get("skipped") == "held:user_joystick"
    # sage stays for the whole aim; the token is long until home (then sage drops)
    monkeypatch.setattr(svc, "_visit_is_secure", lambda: True)
    svc.camera_aim("nudge", 1.0, 0.0)
    lease = [b for p, b in calls if p == "/v1/lease"][-1]
    assert lease["ttl_sec"] == svc.CAM_JOY_TTL


def test_an_external_holder_refuses_everyone_and_the_token_is_published(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    calls, cam = _cam(monkeypatch)
    cam["lease"] = {"lease_id": "Lx", "owner": "inspector"}
    _authed(svc)
    r = svc.cam_take("beam_look", 45, why="look")
    assert r["ok"] is False and r["owner"] == "inspector" and r["why"] == "external"
    out = svc.camera_aim("nudge", 1.0, 0.0)
    assert out["ok"] is False and out["owner"] == "inspector"
    # only the User may steal, on request
    r = svc.cam_take("user_joystick", 300, why="nudge", steal=True)
    assert r["ok"] and r["act"] == "steal" and svc.cam_holder() == "user_joystick"
    assert [p for p, b in calls if p == "/v1/pause"]
    svc._set(RoomPresence())
    pub = svc.get().to_dict()["camera_token"]
    assert pub and pub["owner"] == "user_joystick" and pub["why"] == "nudge"
    from crissa_presence.service import PresenceService
    assert PresenceService.faceid_hud(svc).get("cam_owner") == "user_joystick"  # the fixture stubs the bound one


def test_under_secure_the_door_rest_yields_to_the_joystick_instead_of_fighting_it(tmp_path, monkeypatch):
    """Found 2026-09-07 09:25: with Secure held, _beam_maybe_rest called
    _visit_lamp_hold_door every 2 s → facelock + return_door, and every nudge
    bounced back. While the joystick holds the token nothing homes the camera;
    when the token lapses (45 s under Secure) the next tick homes it."""
    svc = _svc(tmp_path, monkeypatch)
    calls, cam = _cam(monkeypatch)
    _authed(svc)
    monkeypatch.setattr(svc, "_visit_is_secure", lambda: True)
    posted: list = []
    monkeypatch.setattr(svc, "_grabber_post", lambda url, body, timeout=20.0: (posted.append(url), {"ok": True})[1])
    monkeypatch.setattr(svc, "_poll_grabber", lambda: {"ok": True, "lease": None})
    monkeypatch.setattr(svc, "_visit_lamp_hold_door", type(svc)._visit_lamp_hold_door.__get__(svc))
    monkeypatch.setattr(svc, "_ensure_door_aim", type(svc)._ensure_door_aim.__get__(svc))
    svc.camera.mode = "http"
    svc.last_grabber = {"at_door": False}
    assert svc.camera_aim("nudge", 3.0, 0.0)["ok"] is True
    assert svc.cam_holder() == "user_joystick"
    for _ in range(10):
        svc._beam_maybe_rest()
    assert svc._ensure_door_aim() == {"ok": True, "skipped": "joystick"}
    assert not [u for u in posted if "return_door" in u or "facelock" in u], posted
    # the token lapses → the door rest is back
    svc._cam_token["until"] = 0.0
    assert svc.cam_holder() is None
    svc._beam_maybe_rest()
    assert [u for u in posted if "return_door" in u], "off-door under Secure with no holder homes"
