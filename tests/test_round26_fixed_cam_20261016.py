"""Round 26: a crop cannot turn to the door (.101 10-16: Secure and Double Secure offered on the fixed Elgato Facecam
4K, and presence emptied the room - "door_empty_after", headcount 0, guest while the User sat in front). One pan read
dx 0.0001 with mean difference 40.3: the crop zoomed, nothing slid, and the pixel rule recorded "moved"."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

THE_101 = {"camera_name": "Elgato Facecam 4K", "kind": "digital", "fixed": False,
           "how": "measured: a pan of 125 deg moved the picture",
           "last": {"dx": 0.0001, "mad": 40.3, "response": 0.137, "same": False, "moved": True}}


@pytest.fixture
def F(monkeypatch):
    held = {k: v for k, v in sys.modules.items() if k == "crissa_presence" or k.startswith("crissa_presence.")}
    for k in held:
        monkeypatch.delitem(sys.modules, k)
    sys.path.insert(0, str(ROOT / "modules" / "presence"))
    mod = importlib.import_module("crissa_presence.fixed_cam")
    monkeypatch.delenv("RAIL_CAMERA_FIXED", raising=False)
    monkeypatch.delenv("RAIL_FIXED_CAM_CROP", raising=False)
    monkeypatch.delenv("RAIL_FIXED_CAM_DIGITAL", raising=False)
    yield mod
    for k in [k for k in sys.modules if k == "crissa_presence" or k.startswith("crissa_presence.")]:
        del sys.modules[k]
    sys.modules.update(held)


def test_the_101_record_reads_fixed(F, tmp_path):
    (tmp_path / "camera-kind.json").write_text(json.dumps(THE_101))
    assert F.is_fixed({}, home=tmp_path) is True


def test_the_kill_reads_it_as_before(F, tmp_path, monkeypatch):
    (tmp_path / "camera-kind.json").write_text(json.dumps(THE_101))
    monkeypatch.setenv("RAIL_FIXED_CAM_CROP", "0")
    monkeypatch.setenv("RAIL_FIXED_CAM_DIGITAL", "0")      # round 27: a crop pan is fixed by its kind first
    assert F.is_fixed({}, home=tmp_path) is False


def test_a_zoom_on_a_crop_camera_is_not_recorded_as_a_turn(F, tmp_path, monkeypatch):
    (tmp_path / "camera-kind.json").write_text(json.dumps({"camera_name": "Elgato Facecam 4K", "kind": "digital"}))
    monkeypatch.setattr(F, "compare", lambda a, b: dict(THE_101["last"]))
    rec = F.note_pan_check(b"a", b"b", 0, 125 * 3600, home=tmp_path)
    assert rec.get("fixed") is not False and rec["last"].get("crop") is True
    assert F.is_fixed({"camera_ptz": "digital", "pan_seen": True}, home=tmp_path) is True


def test_a_slide_still_turns_a_crop_camera_and_a_motor_keeps_the_pixel_rule(F, tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_FIXED_CAM_KIND_ONCE", "0")   # round 39 (N14): a digital pan never turns; this pins the kill
    (tmp_path / "camera-kind.json").write_text(json.dumps({"kind": "digital"}))
    monkeypatch.setattr(F, "compare", lambda a, b: {"dx": 0.2, "mad": 40.0, "response": 0.05, "same": False,
                                                    "moved": True})
    assert F.note_pan_check(b"a", b"b", 0, 90 * 3600, home=tmp_path)["fixed"] is False
    (tmp_path / "camera-kind.json").write_text(json.dumps({"kind": "motor"}))
    monkeypatch.setattr(F, "compare", lambda a, b: dict(THE_101["last"]))
    assert F.note_pan_check(b"a", b"b", 0, 90 * 3600, home=tmp_path)["fixed"] is False


def test_secure_says_no_turning_camera(F, tmp_path, monkeypatch):
    (tmp_path / "camera-kind.json").write_text(json.dumps(THE_101))
    monkeypatch.setattr(F, "is_house", lambda: False)
    got = F.secure_offer({}, home=tmp_path)
    assert got["ok"] is False and "No turning (360) camera" in got["why"]


def test_the_security_panel_says_why_there_is_no_secure(tmp_path):
    import crissa_home as H
    assert H.security_note(tmp_path, "fixed") == H.SECURITY_NO_DOOR_NOTE
    assert H.security_note(tmp_path, "turning") == ""
    assert "secure" not in [k for k in H.security_positions("fixed")]
