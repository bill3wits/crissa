"""Round 27: a laptop camera (the User 2026-10-16, .101 round 26: guest with his face in view, Secure tiles back, a
refused Secure click that still switched Secure on). A crop pan is fixed whatever a pan check measured; on a fixed
camera in a pack the door watch does not run, a face check every 30-60 s renews AUTH, and a spot check answers the
face-match sequence."""
from __future__ import annotations

import importlib
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

# .101's camera-kind.json in round 26: the crop pan slid the picture (dx 0.44)
THE_101 = {"camera_name": "Elgato Facecam 4K", "kind": "digital", "fixed": False,
           "how": "measured: a pan of 125 deg moved the picture",
           "last": {"dx": 0.4437, "mad": 50.55, "same": False, "moved": True}}


@pytest.fixture
def P(monkeypatch):
    held = {k: v for k, v in sys.modules.items() if k == "crissa_presence" or k.startswith("crissa_presence.")}
    for k in held:
        monkeypatch.delitem(sys.modules, k)
    sys.path.insert(0, str(ROOT / "modules" / "presence"))
    for k in ("RAIL_CAMERA_FIXED", "RAIL_FIXED_CAM_DIGITAL", "RAIL_FIXED_CAM_CROP", "CRISSA_LAPTOP_AUTH",
              "CRISSA_LAPTOP_AUTH_MIN_S", "CRISSA_LAPTOP_AUTH_MAX_S"):
        monkeypatch.delenv(k, raising=False)
    fc = importlib.import_module("crissa_presence.fixed_cam")
    la = importlib.import_module("crissa_presence.laptop_auth")
    yield fc, la
    for k in [k for k in sys.modules if k == "crissa_presence" or k.startswith("crissa_presence.")]:
        del sys.modules[k]
    sys.modules.update(held)


def test_the_101_slide_still_reads_fixed(P, tmp_path):
    fc, _ = P
    (tmp_path / "camera-kind.json").write_text(json.dumps(THE_101))
    assert fc.is_fixed({}, home=tmp_path) is True
    assert fc.is_fixed({"camera_ptz": "digital", "pan_seen": True}, home=tmp_path) is True


def test_the_kill_and_a_motor_keep_the_measurement(P, tmp_path, monkeypatch):
    fc, _ = P
    (tmp_path / "camera-kind.json").write_text(json.dumps(THE_101))
    monkeypatch.setenv("RAIL_FIXED_CAM_DIGITAL", "0")
    assert fc.is_fixed({}, home=tmp_path) is False
    monkeypatch.delenv("RAIL_FIXED_CAM_DIGITAL")
    (tmp_path / "camera-kind.json").write_text(json.dumps(dict(THE_101, kind="motor")))
    assert fc.is_fixed({}, home=tmp_path) is False


def test_secure_is_not_offered_on_the_101_camera(P, tmp_path, monkeypatch):
    fc, _ = P
    (tmp_path / "camera-kind.json").write_text(json.dumps(THE_101))
    monkeypatch.setattr(fc, "is_house", lambda: False)
    got = fc.secure_offer({}, home=tmp_path)
    assert got["ok"] is False and got["kind"] == "fixed"


def test_the_gap_is_30_to_60_s(P):
    _, la = P
    rng = random.Random(7)
    gaps = [la.next_gap(rng) for _ in range(200)]
    assert min(gaps) >= 30.0 and max(gaps) <= 60.0 and max(gaps) - min(gaps) > 20


class FakeSvc:
    def __init__(self, sim):
        self.sim = sim
        self._guest_until_secure = True
        self.granted = []
        self.present = []
        self.left = []

    def _user_id(self):
        return "user"

    def _fixed_cam(self):
        return True

    def _match_user_in_jpegs(self, uid, jpegs, *, source):
        assert source.startswith("desk_ff")
        return self.sim

    def grant_user_face_auth(self, *, sim, source, greet):
        self.granted.append((sim, source, greet))

    def assert_present(self, names, source):
        self.present.append(names)

    def assert_leave(self, names, source):
        self.left.append(names)


def test_a_check_that_sees_him_renews_auth_and_lets_the_latch_go(P):
    _, la = P
    svc = FakeSvc(0.72)
    a = la.LaptopAuth()
    got = a.check(svc, snap=lambda: b"jpeg")
    assert got["found"] and svc.granted and svc.granted[0][2] is False
    assert svc._guest_until_secure is False and svc.present == [["user"]]
    assert not a.due()                               # the next check is 30-60 s away


def test_misses_drop_nothing_but_two_take_him_off_the_roster(P, monkeypatch):
    _, la = P
    monkeypatch.setattr(la, "security_on", lambda: False)   # round 28: under Security On the session decides instead
    svc = FakeSvc(0.2)
    a = la.LaptopAuth()
    a.check(svc, snap=lambda: b"jpeg")
    assert not svc.granted and not svc.left and svc._guest_until_secure is True
    a.check(svc, snap=lambda: b"jpeg")
    assert svc.left == [["user"]]


def test_a_camera_that_does_not_answer_measures_nothing(P):
    _, la = P
    svc = FakeSvc(0.9)

    def broken():
        raise OSError("no camera")
    got = la.LaptopAuth().check(svc, snap=broken)
    assert got["checked"] is False and not svc.granted


def test_on_needs_a_pack_a_fixed_camera_and_the_switch(P, monkeypatch):
    fc, la = P
    monkeypatch.setattr(fc, "is_house", lambda: False)
    assert la.on(FakeSvc(0)) is True
    monkeypatch.setenv("CRISSA_LAPTOP_AUTH", "0")
    assert la.on(FakeSvc(0)) is False
    monkeypatch.delenv("CRISSA_LAPTOP_AUTH")
    monkeypatch.setattr(fc, "is_house", lambda: True)
    assert la.on(FakeSvc(0)) is False


def test_the_face_sequence_takes_the_spot_check_on_a_laptop():
    import crissa_home as H
    p = {"visit_lamp_info": {"face_check": "spot", "secure_offer": {"ok": False, "kind": "fixed", "camera": "fixed"}},
         "face": {"fixed": True}, "user_id": "user"}
    looked = []
    got = H.face_sequence(lambda: p, uid="user", presence=p,
                          look=lambda: looked.append(1) or {"found": True, "sim": 0.7}, sleep=lambda s: None)
    assert looked and got["ok"] and got["looked"]
    assert H.spot_check_of(p, {"CRISSA_LAPTOP_SPOT_CHECK": "0"}) is False


SRC = (ROOT / "modules/presence/crissa_presence/service.py").read_text()


def test_secure_on_refuses_where_it_is_not_offered():
    i = SRC.index("def _visit_secure_on(")
    body = SRC[i:SRC.index("with self._visit_secure_lock:", i)]
    assert "_secure_not_offered()" in body and "return" in body


def test_the_loop_runs_the_laptop_check_instead_of_the_door_watch():
    i = SRC.index("    def _loop(self) -> None:")
    body = SRC[i:SRC.index("    def health(self)", i)]
    assert body.index("self._laptop_loop()") < body.index("run_door_once")
