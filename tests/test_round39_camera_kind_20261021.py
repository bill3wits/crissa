"""Round 39 (.101 round 38 N14, must-fix): Security flipped Off -> ID only by itself. Presence rewrote camera-kind.json
for the FIXED Elgato Facecam 4K as `"kind": "digital", "fixed": false` from a measured digital pan, and
camera_known_to_turn() then said True. The User: "maybe just a flag". A digital pan is a crop of a fixed sensor: it
never rewrites the camera as turning, the Wall never reads it as turning, and once Setup is done the install's default
mode is written down so only a person changes it."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "modules" / "presence"))

THE_101_RECORD = {"camera_name": "Elgato Facecam 4K", "kind": "digital", "fixed": False,
                  "how": "measured: a pan of 125 deg moved the picture", "last": {"dx": 0.3692, "moved": True}}


def test_the_wall_never_reads_a_digital_pan_as_turning(tmp_path, monkeypatch):
    import crissa_home as zh
    monkeypatch.delenv("CRISSA_DIGITAL_NEVER_TURNS", raising=False)
    (tmp_path / "presence").mkdir()
    (tmp_path / "presence" / "camera-kind.json").write_text(json.dumps(THE_101_RECORD))
    assert zh.camera_known_to_turn(tmp_path) is False, ".101's exact record: a fixed camera"
    assert zh.kind_turns({"kind": "motor"}) and zh.kind_turns({"kind": "", "fixed": False})
    monkeypatch.setenv("CRISSA_DIGITAL_NEVER_TURNS", "0")
    assert zh.camera_known_to_turn(tmp_path) is True, "the kill: round 38"


def test_presence_never_rewrites_a_digital_or_known_fixed_camera_as_turning(tmp_path, monkeypatch):
    from crissa_presence import fixed_cam as fc
    monkeypatch.delenv("RAIL_FIXED_CAM_KIND_ONCE", raising=False)
    monkeypatch.setattr(fc, "compare", lambda a, b: {"moved": True, "same": False, "dx": 0.3692})
    monkeypatch.setattr(fc, "kind_path", lambda home=None: tmp_path / "camera-kind.json")
    for rec in ({"camera_name": "Elgato Facecam 4K", "kind": "digital", "fixed": True},
                {"camera_name": "Elgato Facecam 4K", "kind": "", "fixed": True}):
        (tmp_path / "camera-kind.json").write_text(json.dumps(rec))
        got = fc.note_pan_check(b"a", b"b", 0, 125 * 3600, env={})
        assert got.get("fixed") is not False, (rec, got)
    (tmp_path / "camera-kind.json").write_text(json.dumps({"camera_name": "Insta360 Link", "kind": "motor"}))
    assert fc.note_pan_check(b"a", b"b", 0, 125 * 3600, env={}).get("fixed") is False, "a motor that moved turns"


def test_security_never_changes_unless_a_person_chooses(tmp_path, monkeypatch):
    import crissa_home as zh
    monkeypatch.setattr(zh, "is_house", lambda *a, **k: False)
    monkeypatch.delenv("CRISSA_SECURITY_DEFAULT_KEEP", raising=False)
    (tmp_path / "presence").mkdir()
    kind = tmp_path / "presence" / "camera-kind.json"
    kind.write_text(json.dumps({"camera_name": "Elgato Facecam 4K", "kind": "digital", "fixed": True}))
    assert zh.security_mode(home_dir=tmp_path) == "off" and not (tmp_path / "security.json").exists(), \
        "before Setup is done nothing is written"
    (tmp_path / "init.json").write_text(json.dumps({"done": True}))
    assert zh.security_mode(home_dir=tmp_path) == "off"
    rec = json.loads((tmp_path / "security.json").read_text())
    assert rec["mode"] == "off" and rec["by"] == "the install's default"
    kind.write_text(json.dumps({"camera_name": "Insta360 Link", "kind": "motor"}))      # the camera reads as turning
    assert zh.security_mode(home_dir=tmp_path) == "off", "the kept default holds; only a person moves it"
