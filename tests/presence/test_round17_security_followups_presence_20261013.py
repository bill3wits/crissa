"""Round 17 follow-ups, presence's side (the User 2026-10-13: "Fold Secure / Double Secure INTO the Security toggle so
there is ONE source of truth"; "a stranger should then be locked out from any further adjusting"; Off idles face
matching). Stand-ins only: no camera, no service started.

- The offer names the camera's tree; a camera that does not turn says `hide` (no Secure key at all, not greyed); on a
  camera that turns Secure is offered whatever the toggle says (raising to it is open).
- Every publish of the lamp writes the flag back to the toggle (secure_sync): Secure -> secure, held -> double_secure,
  Secure ended -> on with a note; a saved Secure on a camera that does not turn -> on with the camera's note.
- A step down out of Secure through presence's own keys needs his face (security_lower_refused, the one face-match
  sequence with presence's own look); a raise, a password just given, the house, a fixed camera: as before.
- visit_lamp_face_check: the sequence's look (FaceFinder at his seat where the camera turns, else the face block).
- Off idles the visor's scan; the door watch stays.
The rest: tests/test_round17_security_followups_20261013.py.
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "lib"))   # after the package: lib has a crissa_presence.py

from crissa_presence import fixed_cam  # noqa: E402
from crissa_presence.service import PresenceService  # noqa: E402
import crissa_home as H  # noqa: E402

UID = "user"
TURNS = {"camera_ptz": "motor", "pan_seen": True}


@pytest.fixture()
def kit(tmp_path, monkeypatch):
    for k in ("RAIL_CAMERA_FIXED", "RAIL_FIXED_CAM_SECURE", "CRISSA_NO_CAMERA", "RAIL_CAMERA_PTZ_BY_MODEL",
              "CRISSA_SECURITY_ONE_TOGGLE", "CRISSA_SECURITY_LOCK_ADJUST", "CRISSA_SECURITY_LOWER_NEEDS_FACE",
              "CRISSA_SECURITY_DEFAULT_IDONLY", "CRISSA_SECURITY_OFF_IDLES_FACES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(fixed_cam, "is_house", lambda: False)
    monkeypatch.setattr(fixed_cam, "kind_path", lambda home=None: tmp_path / "camera-kind.json")
    monkeypatch.setattr(H, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(H, "HOME", tmp_path)
    monkeypatch.setenv("CRISSA_FACE_SEQ_WAIT_S", "0")
    H._SEC_CACHE.clear()

    def set_(mode):
        if mode is not None:
            (tmp_path / "security.json").write_text(json.dumps({"mode": mode}))
        H._SEC_CACHE.clear()
        return tmp_path
    return set_


def _saved(tmp_path):
    H._SEC_CACHE.clear()
    return H.security_record(tmp_path)


# ---- the offer -------------------------------------------------------------------------------------------------------

def test_the_offer_names_the_tree_and_hides_secure_where_the_camera_does_not_turn(kit):
    kit("id_only")
    got = fixed_cam.secure_offer({"camera_ptz": "digital", "pan_seen": True})
    assert got["ok"] is False and got["camera"] == "fixed" and got["hide"] is True and "turn" in got["why"]
    got = fixed_cam.secure_offer(TURNS)
    assert got["ok"] is True and got["camera"] == "turns"                  # ID-only: Secure is a step up, offered
    assert H.camera_kind_of({"visit_lamp_info": {"secure_offer": got}}) == "turning"


def test_the_kill_is_round_17(kit, monkeypatch):
    kit("id_only")
    monkeypatch.setenv("CRISSA_SECURITY_ONE_TOGGLE", "0")
    assert fixed_cam.secure_offer(TURNS)["kind"] == "security"
    assert "hide" not in fixed_cam.secure_offer({"camera_ptz": "digital", "pan_seen": True})
    assert fixed_cam.secure_sync(True, False) is None


# ---- the sync --------------------------------------------------------------------------------------------------------

def test_every_edge_of_the_flag_is_the_toggles(kit):
    home = kit("id_only")
    assert fixed_cam.secure_sync(False, False) is None and _saved(home)["mode"] == "id_only"   # nothing to say
    assert fixed_cam.secure_sync(True, False) == "secure" and _saved(home)["mode"] == "secure"
    assert fixed_cam.secure_sync(True, False) is None                                         # written once
    assert fixed_cam.secure_sync(True, True) == "double_secure"
    assert fixed_cam.secure_sync(False, False, why="a known face at the door") == "on"
    rec = _saved(home)
    assert rec["mode"] == "on" and rec["by"] == "presence" and "a known face at the door" in rec["note"]
    kit("secure")
    assert fixed_cam.secure_sync(False, False, turns=False) == "on" and _saved(home)["note"] == H.SECURITY_FIXED_NOTE


def test_the_service_syncs_at_every_publish_of_the_lamp(kit):
    home = kit("id_only")
    svc = types.SimpleNamespace(_visit_secure=True, _visit_hold=False, _visit_secure_reason="click",
                                SECURE_END_WORDS=PresenceService.SECURE_END_WORDS)
    PresenceService._security_sync(svc, {"ok": True, "kind": "turns"})
    assert _saved(home)["mode"] == "secure"
    svc._visit_secure, svc._visit_secure_reason = False, "entry_face_named"
    PresenceService._security_sync(svc, {"ok": True, "kind": "turns"})
    rec = _saved(home)
    assert rec["mode"] == "on" and "a known face at the door" in rec["note"]
    kit("secure")
    PresenceService._security_sync(svc, {"ok": False, "kind": "fixed"})
    assert _saved(home)["mode"] == "on"
    kit("secure")
    PresenceService._security_sync(svc, None)                              # no word on the camera: nothing
    assert _saved(home)["mode"] == "secure"
    src = open(os.path.join(os.path.dirname(fixed_cam.__file__), "service.py"), encoding="utf-8").read()
    assert "self._security_sync(offer)" in src


# ---- a step down out of Secure ---------------------------------------------------------------------------------------

class _St:
    def __init__(self, d):
        self.d = d

    def to_dict(self):
        return dict(self.d)


def _svc(secure=True, hold=False, face=None, look=None, offer=None, hold_at=0.0):
    pub = {"ok": True, "visit_lamp": "sage" if secure else "amber"}
    return types.SimpleNamespace(
        _visit_secure=secure, _visit_hold=hold, _visit_hold_at=hold_at,
        SECURE_PROOF_RELEASE_S=PresenceService.SECURE_PROOF_RELEASE_S,
        _secure_offer=lambda: offer if offer is not None else {"ok": True, "kind": "turns", "camera": "turns"},
        get=lambda: _St({"user_id": UID, "face": face or {},
                         "visit_lamp_info": {"secure_offer": offer or {"ok": True, "kind": "turns"}}}),
        _user_id=lambda: UID,
        visit_lamp_face_check=look or (lambda: {"ok": True, "checked": True, "found": False}),
        visit_lamp_pub=lambda: dict(pub))


def test_a_step_down_out_of_secure_needs_his_face(kit):
    kit("secure")
    looks = []
    svc = _svc(look=lambda: looks.append(1) or {"ok": True, "checked": True, "found": False})
    got = PresenceService.security_lower_refused(svc, "click")
    assert got["ok"] is False and got["face"] == "refused" and got["error"] == H.FACE_SEQ_FAIL and looks == [1]
    svc = _svc(look=lambda: {"ok": True, "checked": True, "found": True, "sim": 0.8})
    assert PresenceService.security_lower_refused(svc, "click") is None          # his face: the click goes on
    assert PresenceService.security_lower_refused(_svc(secure=False), "click") is None   # a raise: Secure's own look
    kit("double_secure")
    assert PresenceService.security_lower_refused(_svc(hold=True), "release")["face"] == "refused"
    import time
    assert PresenceService.security_lower_refused(_svc(hold=True, hold_at=time.time() - 5), "release") is None


def test_a_fixed_camera_the_house_and_the_kills_keep_todays_click(kit, monkeypatch):
    kit("secure")
    assert PresenceService.security_lower_refused(_svc(offer={"ok": False, "kind": "fixed"}), "click") is None
    monkeypatch.setenv("CRISSA_SECURITY_LOWER_NEEDS_FACE", "0")
    assert PresenceService.security_lower_refused(_svc(), "click") is None
    monkeypatch.delenv("CRISSA_SECURITY_LOWER_NEEDS_FACE")
    monkeypatch.setenv("CRISSA_SECURITY_ONE_TOGGLE", "0")
    assert PresenceService.security_lower_refused(_svc(), "click") is None
    monkeypatch.delenv("CRISSA_SECURITY_ONE_TOGGLE")
    monkeypatch.setattr(fixed_cam, "is_house", lambda: True)
    assert PresenceService.security_lower_refused(_svc(), "click") is None


def test_the_routes_ask_before_the_click_and_serve_the_look():
    src = open(os.path.join(os.path.dirname(fixed_cam.__file__), "server.py"), encoding="utf-8").read()
    assert src.count('svc.security_lower_refused(') == 2 and 'if op == "face_check":' in src
    assert "svc.visit_lamp_face_check()" in src


def test_the_face_check_is_presences_own_look(kit):
    seen = []
    svc = types.SimpleNamespace(
        _user_id=lambda: UID, _secure_offer=lambda: {"ok": True, "kind": "turns"},
        _desk_look_once=lambda uid, force=False, owner="": seen.append((uid, force, owner)) or
        {"found": True, "best_sim": 0.77},
        get=lambda: _St({}))
    got = PresenceService.visit_lamp_face_check(svc)
    assert got == {"ok": True, "checked": True, "found": True, "sim": 0.77, "via": "desk_look"}
    assert seen == [(UID, True, "security_face_check")]
    face = {"locked": UID, "faces": [{"name": UID, "sim": 0.7}], "n_faces": 1, "fixed": True, "age_s": 2.0,
            "source": "fixed_look"}
    svc = types.SimpleNamespace(_user_id=lambda: UID, _secure_offer=lambda: {"ok": False, "kind": "fixed"},
                                get=lambda: _St({"user_id": UID, "face": face}))
    got = PresenceService.visit_lamp_face_check(svc)
    assert got["found"] is True and got["via"] == "face_block"


# ---- Off idles the visor's scan --------------------------------------------------------------------------------------

def test_off_idles_the_visor_scan_and_the_door_watch_stays(kit, monkeypatch):
    scans = []
    svc = types.SimpleNamespace(_f2f=True, _faceid_hb_ts=0.0, faceid_scan=lambda: scans.append(1))
    kit("off")
    assert fixed_cam.faces_idle() is True
    PresenceService._faceid_heartbeat(svc)
    assert scans == []
    kit("id_only")
    PresenceService._faceid_heartbeat(svc)
    assert scans == [1]
    kit("off")
    monkeypatch.setenv("CRISSA_SECURITY_OFF_IDLES_FACES", "0")
    svc._faceid_hb_ts = 0.0
    PresenceService._faceid_heartbeat(svc)
    assert scans == [1, 1]
    monkeypatch.delenv("CRISSA_SECURITY_OFF_IDLES_FACES")
    monkeypatch.setattr(fixed_cam, "is_house", lambda: True)
    assert fixed_cam.faces_idle() is False
