"""Register aim + renew + verified home (the User 2026-10-04, Init v2).

Under Register's token Init aims the camera itself (the 11 o'clock / 20 deg down room
shot), renews the token while the User works, and gives it back with the home
verified against the door watch's own pose. RAIL_REGISTER_AIM off (default): the new
ops answer 404 {"error": "off"} and take / give_back are exactly as before. Every
camera call is mocked; nothing here reaches rail-camera or v4l2-ctl.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

from test_camera_token_service import _authed, _cam  # noqa: E402
from test_visit_lamp import _svc  # noqa: E402

from crissa_presence import beam_look as bl, ptz, room_heading, room_sweep as rs, seat_shot  # noqa: E402


@pytest.fixture
def token_on(monkeypatch, tmp_path):
    monkeypatch.setenv("RAIL_REGISTER_TOKEN", "1")
    cfg = tmp_path / "register-token.json"
    cfg.write_text(json.dumps({"ttl_sec": 150}))
    monkeypatch.setenv("RAIL_REGISTER_CONFIG", str(cfg))
    monkeypatch.delenv("RAIL_REGISTER_TTL", raising=False)
    monkeypatch.delenv("RAIL_CAMERA_PROFILE", raising=False)
    geom = tmp_path / "room_geometry.json"
    geom.write_text(json.dumps({"heading": {"door_home_pan": 432000, "door_home_tilt": 0, "door_home_zoom": 100}}))
    monkeypatch.setattr(room_heading, "_GEOM", geom)
    monkeypatch.setattr(seat_shot, "SETTLE_SEC", 0.0)


@pytest.fixture
def aim_on(monkeypatch, token_on):
    monkeypatch.setenv("RAIL_REGISTER_AIM", "1")


def _camera(monkeypatch, *, pan_off=0, tilt_off=0, aim_ok=True, tilt_in_aim=True, ff_ok=True, aim_raises=None):
    """_cam's lease broker plus a camera that lands `pan_off` / `tilt_off` units from the command."""
    calls, state = _cam(monkeypatch)
    lease_post = bl._cam_post
    at = {"pan": 432000, "tilt": 0}

    def post(path, body, timeout=8.0):
        if path == "/v1/facefinder":
            calls.append((path, dict(body)))
            return {"ok": ff_ok}
        if path == "/v1/aim":
            calls.append((path, dict(body)))
            if aim_raises is not None:
                raise aim_raises
            at["pan"] = int(body["pan"]) + pan_off
            at["tilt"] = int(body["tilt"]) + tilt_off
            out = {"ok": aim_ok, "pan_cmd": body["pan"], "pan_actual": at["pan"], "error": None if aim_ok else "pan mismatch"}
            if tilt_in_aim:
                out["tilt_actual"] = at["tilt"]
            return out
        return lease_post(path, body, timeout=timeout)

    monkeypatch.setattr(bl, "_cam_post", post)
    monkeypatch.setattr(ptz, "get_ctrl", lambda dev, name: {"pan_absolute": at["pan"], "tilt_absolute": at["tilt"]}[name])
    snaps: list = []
    monkeypatch.setattr(rs, "_snapshot", lambda timeout=5.0: (snaps.append(1), b"\xff\xd8jpeg")[1])
    return calls, state, at, snaps


def _held(tmp_path, monkeypatch, **cam):
    tmp_path.mkdir(parents=True, exist_ok=True)
    svc = _svc(tmp_path, monkeypatch)
    calls, state, at, snaps = _camera(monkeypatch, **cam)
    _authed(svc)
    assert svc.register_take("init")["ok"] and svc.cam_holder() == "register"
    return svc, calls, at, snaps


# ------------------------------------------------------------------ off --

def test_off_the_new_ops_say_off_and_take_give_back_are_unchanged(tmp_path, monkeypatch, token_on):
    monkeypatch.delenv("RAIL_REGISTER_AIM", raising=False)
    svc, calls, at, snaps = _held(tmp_path, monkeypatch)
    assert svc.register_aim({"pan_deg": 150, "tilt_deg": -20}) == {"ok": False, "error": "off", "why": "RAIL_REGISTER_AIM is off"}
    assert svc.register_renew()["error"] == "off"
    assert not [p for p, _ in calls if p in ("/v1/aim", "/v1/facefinder")] and not snaps
    monkeypatch.setattr(bl, "rest", lambda lid=None: {"ok": True})
    gb = svc.register_give_back_verified()
    assert gb["released"] and "home" not in gb                       # give_back as before: no verification


# --------------------------------------------------------------- renew --

def test_renew_is_take_by_the_same_owner(tmp_path, monkeypatch, aim_on):
    svc, calls, at, snaps = _held(tmp_path, monkeypatch)
    lid = svc._cam_token["lease_id"]
    out = svc.register_renew("init still working")
    assert out["ok"] and out["act"] == "renew" and out["ttl"] == 150.0 and out["op"] == "renew"
    assert out["lease_id"] == lid and svc.cam_holder() == "register"


# ----------------------------------------------------------------- aim --

def test_not_the_holder_is_refused_and_nothing_moves(tmp_path, monkeypatch, aim_on):
    svc = _svc(tmp_path, monkeypatch)
    calls, *_ = _camera(monkeypatch)
    _authed(svc)
    out = svc.register_aim({"pan_deg": 150, "tilt_deg": -20})
    assert out["ok"] is False and out["error"] == "not_holder" and out["owner"] is None
    assert svc.camera_aim("nudge", 1.0, 0.0)["ok"]                      # the joystick holds it
    out = svc.register_aim({"pan_deg": 150, "tilt_deg": -20})
    assert out["error"] == "not_holder" and out["owner"] == "user_joystick"
    assert not [p for p, _ in calls if p == "/v1/facefinder"]


def test_ok_aims_with_the_tokens_lease_reads_back_and_brings_frames(tmp_path, monkeypatch, aim_on):
    svc, calls, at, snaps = _held(tmp_path, monkeypatch)
    lid = svc._cam_token["lease_id"]
    out = svc.register_aim({"pan_deg": 150, "tilt_deg": -20, "frames": 2})
    assert out["ok"] is True, out
    # 11 o'clock = 150 deg, past the 145 deg limit: clamped (never wrapped) one step inside
    assert out["pan_cmd"] == 518400 and out["pan_clamped"] is True
    assert out["tilt_cmd"] == -72000 and out["zoom"] == seat_shot.sweep_zoom()
    assert (out["pan_actual"], out["tilt_actual"]) == (518400, -72000) and out["pan_ok"] and out["tilt_ok"]
    assert out["n_frames"] == 2 and base64.b64decode(out["images"][0]) == b"\xff\xd8jpeg"
    seq = [(p, b) for p, b in calls if p in ("/v1/facefinder", "/v1/aim")]
    assert seq[0] == ("/v1/facefinder", {"on": False, "lease_id": lid})        # FF off first, on the token's lease
    assert seq[1][0] == "/v1/aim" and seq[1][1]["lease_id"] == lid and seq[1][1]["pan"] > 0
    assert not [p for p, _ in calls if p in ("/v1/return_door", "/v1/lease/release")], "aim does not home"
    assert svc.cam_holder() == "register"


def test_pan_in_units_is_clamped_too_and_never_negative(tmp_path, monkeypatch, aim_on):
    svc, calls, at, snaps = _held(tmp_path, monkeypatch)
    out = svc.register_aim({"pan": 540000, "tilt_deg": 0})
    assert out["ok"] and out["pan_cmd"] == 518400 and out["pan_clamped"]
    out = svc.register_aim({"pan": 432000, "tilt_deg": -5})
    assert out["ok"] and out["pan_cmd"] == 432000 and out["pan_clamped"] is False and out["tilt_cmd"] == -18000


@pytest.mark.parametrize("axis", ["pan", "tilt"])
def test_a_mismatched_readback_is_an_error_never_a_picture(tmp_path, monkeypatch, aim_on, axis):
    off = {"pan_off": 7200} if axis == "pan" else {"tilt_off": -7200}
    svc, calls, at, snaps = _held(tmp_path, monkeypatch, **off)
    out = svc.register_aim({"pan_deg": 100, "tilt_deg": -20, "frames": 3})
    assert out["ok"] is False and out["error"] == "readback_mismatch"
    assert out[f"{axis}_ok"] is False and out["images"] == [] and not snaps
    assert out["pan_actual"] is not None and out["tilt_actual"] is not None


def test_within_one_step_passes(tmp_path, monkeypatch, aim_on):
    svc, *_ = _held(tmp_path, monkeypatch, pan_off=3600, tilt_off=-3600)
    assert svc.register_aim({"pan_deg": 100, "tilt_deg": -20})["ok"]


def test_tilt_read_from_the_camera_when_aim_does_not_carry_it(tmp_path, monkeypatch, aim_on):
    """Today's rail-camera /v1/aim answers pan_actual only; tilt comes from ptz.get_ctrl like the joystick's."""
    svc, calls, at, snaps = _held(tmp_path, monkeypatch, tilt_in_aim=False)
    out = svc.register_aim({"pan_deg": 100, "tilt_deg": -20})
    assert out["ok"] and out["readback"] == {"pan": "aim", "tilt": "v4l2"} and out["tilt_actual"] == -72000
    svc2, *_ = _held(tmp_path / "b", monkeypatch, tilt_in_aim=False, tilt_off=-10800)
    out = svc2.register_aim({"pan_deg": 100, "tilt_deg": -20})
    assert out["error"] == "readback_mismatch" and out["tilt_actual"] == -82800


def test_a_refused_aim_is_an_error_with_the_readback(tmp_path, monkeypatch, aim_on):
    svc, calls, at, snaps = _held(tmp_path, monkeypatch, aim_ok=False)
    out = svc.register_aim({"pan_deg": 100, "tilt_deg": -20})
    assert out["ok"] is False and out["error"] == "aim_failed" and not snaps
    assert "pan_actual" in out and "tilt_actual" in out


def test_an_http_503_aim_keeps_the_cameras_answer(tmp_path, monkeypatch, aim_on):
    import io

    err = urllib.error.HTTPError("http://cam/v1/aim", 503, "x", {},
                                 io.BytesIO(json.dumps({"ok": False, "pan_actual": 0, "error": "pan mismatch"}).encode()))
    svc, calls, at, snaps = _held(tmp_path, monkeypatch, aim_raises=err)
    out = svc.register_aim({"pan_deg": 100, "tilt_deg": -20})
    assert out["error"] == "aim_failed" and out["pan_actual"] == 0 and out["detail"] == "pan mismatch" and not snaps


def test_facefinder_not_off_means_no_aim(tmp_path, monkeypatch, aim_on):
    svc, calls, at, snaps = _held(tmp_path, monkeypatch, ff_ok=False)
    out = svc.register_aim({"pan_deg": 100, "tilt_deg": -20})
    assert out["error"] == "facefinder_off_failed" and not [p for p, _ in calls if p == "/v1/aim"]


@pytest.mark.parametrize("body", [{}, {"tilt_deg": -20}, {"pan_deg": "left"}, {"pan_deg": 10, "frames": 4},
                                  {"pan_deg": 10, "frames": 0}, {"pan_deg": float("nan")}])
def test_bad_requests(tmp_path, monkeypatch, aim_on, body):
    svc, calls, *_ = _held(tmp_path, monkeypatch)
    assert svc.register_aim(body)["error"] == "bad_request"
    assert not [p for p, _ in calls if p == "/v1/aim"]


# ------------------------------------------------------- give_back verify --

def test_give_back_reports_the_verified_home(tmp_path, monkeypatch, aim_on):
    svc, calls, at, snaps = _held(tmp_path, monkeypatch)
    assert svc.register_aim({"pan_deg": 100, "tilt_deg": -20})["ok"]

    def rest(lid=None, verify=None):
        at["pan"], at["tilt"] = 432000, 0
        return {"ok": True, **({"verify": verify()} if verify else {})}

    monkeypatch.setattr(bl, "rest", rest)
    gb = svc.register_give_back_verified()
    assert gb["released"] and svc.cam_holder() is None
    assert gb["home"] == {"pan_want": 432000, "tilt_want": 0, "pan_actual": 432000, "tilt_actual": 0,
                          "pan_ok": True, "tilt_ok": True, "read": "before_release"}


def test_give_back_verify_reports_a_wrong_home_and_never_raises(tmp_path, monkeypatch, aim_on):
    svc, calls, at, snaps = _held(tmp_path, monkeypatch)
    monkeypatch.setattr(bl, "rest", lambda lid=None, verify=None: (at.update(pan=432000, tilt=18000), {"ok": True, "verify": verify()})[1])
    gb = svc.register_give_back_verified()
    assert gb["home"]["pan_ok"] is True and gb["home"]["tilt_ok"] is False and gb["home"]["tilt_actual"] == 18000
    # a dead readback is a report, not a raise
    svc2, *_ = _held(tmp_path / "b", monkeypatch)
    monkeypatch.setattr(bl, "rest", lambda lid=None, verify=None: {"ok": True, "verify": verify()})
    monkeypatch.setattr(ptz, "get_ctrl", lambda dev, name: (_ for _ in ()).throw(OSError("no v4l2")))
    gb = svc2.register_give_back_verified()
    assert gb["released"] and gb["home"]["pan_actual"] is None and gb["home"]["pan_ok"] is False


def test_the_home_is_read_before_the_lease_goes_and_a_watchdog_moves_the_camera(tmp_path, monkeypatch, aim_on):
    """X-hi's review (2026-10-04): the read-back must happen while the token's lease still holds the camera. Here the
    REAL beam_look.rest runs: the door rest lands the camera at the door watch's own pose, and a watchdog moves it the
    moment the lease is released. The check still reports the true home, and it was read before the release."""
    svc, calls, at, snaps = _held(tmp_path, monkeypatch)
    order: list = []
    monkeypatch.setattr(bl, "_pytest", lambda: False)

    def go_home(rs_, lid):
        order.append("home")
        at["pan"], at["tilt"] = 432000, 0
        return {"home": True}
    monkeypatch.setattr(seat_shot, "go_home", go_home)
    lease_post = bl._cam_post

    def post(path, body, timeout=8.0):
        if path == "/v1/lease/release":
            order.append("release")
            at["pan"], at["tilt"] = 0, 18000            # a watchdog takes the camera the instant it is free
        elif path == "/v1/resume":
            order.append("resume")
            at["pan"] = 150000                           # and another one after the resume
        return lease_post(path, body, timeout=timeout)
    monkeypatch.setattr(bl, "_cam_post", post)
    real_read = svc._ptz_read

    def read(name):
        order.append("read:" + name)
        return real_read(name)
    monkeypatch.setattr(svc, "_ptz_read", read)

    gb = svc.register_give_back_verified()
    assert gb["released"] and svc.cam_holder() is None
    assert gb["home"]["pan_actual"] == 432000 and gb["home"]["tilt_actual"] == 0
    assert gb["home"]["pan_ok"] and gb["home"]["tilt_ok"] and gb["home"]["read"] == "before_release"
    assert order.index("read:tilt_absolute") < order.index("release") < order.index("resume")
    assert (at["pan"], at["tilt"]) == (150000, 18000)       # the watchdogs did move it - after the check


def test_give_back_by_a_non_holder_verifies_nothing(tmp_path, monkeypatch, aim_on):
    svc = _svc(tmp_path, monkeypatch)
    _camera(monkeypatch)
    _authed(svc)
    gb = svc.register_give_back_verified()
    assert gb["released"] is False and gb["home"] == {"skipped": "not_released"}


def test_give_back_keeps_secure_and_hold(tmp_path, monkeypatch, aim_on):
    svc, *_ = _held(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "_visit_is_secure", lambda: True)
    svc._visit_secure, svc._visit_hold = True, True
    monkeypatch.setattr(bl, "rest", lambda lid=None, verify=None: {"ok": True, "verify": verify() if verify else None})
    gb = svc.register_give_back_verified()
    assert gb["released"] and svc._visit_secure is True and svc._visit_hold is True and "secure_dropped" not in gb


# --------------------------------------------------------------- route --

class _RouteSvc:
    def __init__(self):
        self.seen: list = []

    def register_take(self, why="register"):
        self.seen.append(("take", why))
        return {"ok": True, "act": "grant", "ttl": 150.0}

    def register_give_back(self):
        self.seen.append(("give_back",))
        return {"ok": True, "released": True}

    def register_give_back_verified(self):
        self.seen.append(("give_back_verified",))
        return {"ok": True, "released": True, "home": {"pan_ok": True, "tilt_ok": True}}

    def register_renew(self, why="register"):
        self.seen.append(("renew", why))
        return {"ok": True, "act": "renew", "ttl": 150.0}

    def register_aim(self, body):
        self.seen.append(("aim", body))
        return dict(self.answer)

    answer = {"ok": True}


@pytest.fixture
def route(monkeypatch):
    from crissa_presence import server as srv

    fake = _RouteSvc()
    monkeypatch.setattr(srv, "_svc", fake)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def post(body):
        req = urllib.request.Request(base + "/v1/camera/register", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    yield fake, post
    httpd.shutdown()
    httpd.server_close()


def test_route_off_new_ops_404_and_old_ops_unchanged(route, monkeypatch):
    monkeypatch.delenv("RAIL_REGISTER_AIM", raising=False)
    fake, post = route
    assert post({"op": "aim", "pan_deg": 150}) == (404, {"ok": False, "error": "off", "why": "RAIL_REGISTER_AIM is off"})
    assert post({"op": "renew"})[0] == 404
    assert post({"op": "take", "why": "init"})[0] == 200
    assert post({"op": "give_back"})[1] == {"ok": True, "released": True}
    assert fake.seen == [("take", "init"), ("give_back",)]


def test_route_on_dispatches_and_maps_codes(route, monkeypatch):
    monkeypatch.setenv("RAIL_REGISTER_AIM", "1")
    fake, post = route
    assert post({"op": "renew", "why": "w"}) == (200, {"ok": True, "act": "renew", "ttl": 150.0})
    assert post({"op": "give_back"})[1]["home"] == {"pan_ok": True, "tilt_ok": True}
    assert post({"op": "aim", "pan_deg": 150, "tilt_deg": -20})[0] == 200
    assert fake.seen[-1] == ("aim", {"op": "aim", "pan_deg": 150, "tilt_deg": -20})
    for err, code in (("not_holder", 409), ("busy", 409), ("lost", 409), ("bad_request", 400),
                      ("readback_mismatch", 502), ("aim_failed", 502), ("off", 404)):
        fake.answer = {"ok": False, "error": err}
        assert post({"op": "aim", "pan_deg": 1})[0] == code, err
