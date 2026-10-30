"""Round 28 (the User 2026-10-17 on .101, ASK 12): on a laptop Security defaults to Off; turned On, one face match signs
in a session that holds on his face, movement, speech or keyboard/mouse use and ends after 30 s with none of them."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "lib", ROOT / "modules" / "presence"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import crissa_home as H  # noqa: E402
from crissa_presence import laptop_auth as LA  # noqa: E402


def _kind(tmp_path, rec):
    (tmp_path / "presence").mkdir(exist_ok=True)
    (tmp_path / "presence" / "camera-kind.json").write_text(json.dumps(rec))


# ---- the default ----------------------------------------------------------------------------------------------------

def test_a_laptop_starts_off(tmp_path):
    _kind(tmp_path, {"kind": "digital", "fixed": True})
    assert H.security_default({}, tmp_path) == "off"


def test_no_camera_record_is_a_laptop(tmp_path):
    assert H.security_default({}, tmp_path) == "off"


def test_a_camera_that_turns_keeps_id_only(tmp_path, monkeypatch):
    _kind(tmp_path, {"kind": "motor"})
    assert H.security_default({}, tmp_path) == "id_only"
    _kind(tmp_path, {"kind": "", "fixed": False})                # a measured turn on a camera that is not a crop
    assert H.security_default({}, tmp_path) == "id_only"
    # round 39 (N14): a digital pan's measured move is not a turn - the fixed Facecam stays Off
    _kind(tmp_path, {"kind": "digital", "fixed": False})
    assert H.security_default({}, tmp_path) == "off"
    monkeypatch.setenv("CRISSA_DIGITAL_NEVER_TURNS", "0")
    assert H.security_default({}, tmp_path) == "id_only", "the kill: round 28"


def test_the_switches(tmp_path):
    assert H.security_default({"CRISSA_LAPTOP_SECURITY_OFF": "0"}, tmp_path) == "id_only", "round 27"
    assert H.security_default({"CRISSA_SECURITY_DEFAULT_IDONLY": "0"}, tmp_path) == "on"


def test_a_saved_choice_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "is_house", lambda *a, **k: False)
    (tmp_path / H.SECURITY_FILE).write_text(json.dumps({"mode": "on"}))
    assert H.security_mode({}, tmp_path) == "on"
    (tmp_path / H.SECURITY_FILE).unlink()
    assert H.security_mode({}, tmp_path) == "off"


# ---- the session ----------------------------------------------------------------------------------------------------

class _Svc:
    def __init__(self):
        self._face_auth_hold = {"u": {"ts": 1000.0, "source": "desk_ff_laptop"}}
        self._auth_levels = {"u": 1}
        self._user_session = {"opened_ts": 1000.0}
        self._last_user_voice = {}
        self.republished = []

    def _user_id(self):
        return "u"

    def _laptop_republish(self, kind):
        self.republished.append(kind)


def _la(monkeypatch):
    monkeypatch.setattr(LA, "security_on", lambda: True)
    monkeypatch.delenv("CRISSA_LAPTOP_SESSION", raising=False)
    monkeypatch.delenv("CRISSA_LAPTOP_SESSION_S", raising=False)
    la = LA.LaptopAuth()
    la.next_at = 10_000.0
    return la


def _tick(la, svc, now, idle=None, snap=None):
    return la.session_tick(svc, now=now, idle_s=(lambda: idle), snap=snap or (lambda: b""))


def test_an_idle_session_ends_after_30_s(monkeypatch):
    la, svc = _la(monkeypatch), _Svc()
    assert _tick(la, svc, 1020.0)["session"] is True, "face 20 s ago holds it"
    out = _tick(la, svc, 1031.0)
    assert out.get("ended") and not svc._face_auth_hold and svc._user_session is None
    assert svc.republished == ["laptop_session_idle"]


def test_speech_keyboard_and_his_look_hold_it(monkeypatch):
    la, svc = _la(monkeypatch), _Svc()
    svc._last_user_voice = {"ts": 1025.0, "match": False}
    assert _tick(la, svc, 1050.0)["session"] is True, "speech 25 s ago"
    assert _tick(la, svc, 1080.0, idle=3.0)["why"] == "keyboard/mouse"
    from crissa_presence import fixed_cam
    monkeypatch.setattr(fixed_cam, "look", lambda: {"ts": 1100.0, "name": "u"})
    assert _tick(la, svc, 1125.0)["session"] is True and la.activity_why == "face"
    assert _tick(la, svc, 1131.0).get("ended"), "nothing for 31 s"


def test_movement_holds_it(monkeypatch):
    import numpy as np
    la, svc = _la(monkeypatch), _Svc()
    frames = iter([np.zeros((36, 64), "float32"), np.full((36, 64), 40.0, "float32")])
    monkeypatch.setattr(LA, "_small_grey", lambda jpeg: next(frames))
    _tick(la, svc, 1020.0)
    assert _tick(la, svc, 1040.0)["why"] == "movement"
    assert _tick(la, svc, 1065.0)["session"] is True


def test_activity_after_an_ended_session_asks_for_his_face(monkeypatch):
    la, svc = _la(monkeypatch), _Svc()
    _tick(la, svc, 1031.0)
    la.next_at = time.time() + 60
    _tick(la, svc, time.time(), idle=0.5)
    assert la.next_at <= time.time(), "a face check now starts the next session"


def test_no_two_miss_leave_under_a_session(monkeypatch):
    la = _la(monkeypatch)
    left = []

    class S(_Svc):
        def _match_user_in_jpegs(self, *a, **k):
            return 0.1

        def assert_leave(self, ids, source=""):
            left.append(ids)

    s = S()
    for _ in range(3):
        la.check(s, snap=lambda: b"x")
    assert left == []
    monkeypatch.setattr(LA, "security_on", lambda: False)
    la.misses = 1
    la.check(s, snap=lambda: b"x")
    assert left == [["u"]], "Security Off/ID-only: round 27's two-miss leave"


def test_the_kill(monkeypatch):
    la = _la(monkeypatch)
    monkeypatch.setenv("CRISSA_LAPTOP_SESSION", "0")
    assert la.session_rule() is False


def test_idle_readers():
    assert LA.parse_idle("gdbus", "(uint64 4523,)\n") == 4.523
    assert LA.parse_idle("xprintidle", "1200\n") == 1.2
    assert LA.parse_idle("ioreg", '  "HIDIdleTime" = 2500000000\n') == 2.5
    assert LA.parse_idle("gdbus", "Error") is None


def test_the_service_hands_the_clock_to_the_session():
    src = (ROOT / "modules" / "presence" / "crissa_presence" / "service.py").read_text()
    assert "if la.session_rule():                       # round 28" in src
    assert "return None  # round 28: a laptop's session (laptop_auth.session_tick) is the one clock" in src
