"""The room camera is found, picked in Setup and checked (the User 2026-10-31, after 1.0.4: "When a dropdown list for
Sound why not a dropdown list for camera? We could run quick capture then to see params ... can we detect settings
invisibly there? Just in background based on system files?")."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "camera_pick_t", ROOT / "modules" / "presence" / "crissa_presence" / "camera_pick.py")
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)


def _sysfs(tmp: Path, rows):
    """rows: (videoN, name, sysfs index, usb port, removable) -> a fake /sys/class/video4linux + /dev/v4l/by-id."""
    sysr, byid = tmp / "sys", tmp / "by-id"
    sysr.mkdir()
    byid.mkdir()
    for n, name, idx, port, rem in rows:
        usb = tmp / "devices" / port
        (usb / (port + ":1.0")).mkdir(parents=True, exist_ok=True)
        (usb / "removable").write_text(rem)
        d = sysr / n
        d.mkdir()
        (d / "name").write_text(name)
        (d / "index").write_text(str(idx))
        os.symlink(usb / (port + ":1.0"), d / "device")
        os.symlink(tmp / "dev" / n, byid / ("usb-%s-video-index%d" % (name.replace(" ", "_"), idx)))
    (tmp / "dev").mkdir()
    return str(sysr), str(byid), str(tmp / "dev")


def _scan(tmp, rows, caps, ctls=None):
    sysr, byid, dev = _sysfs(tmp, rows)
    return C.scan(sys_root=sysr, by_id=byid, dev_root=dev, cap=lambda node: caps.get(os.path.basename(node)),
                  ctl=lambda node: (ctls or {}).get(os.path.basename(node), {}), platform="linux")


def test_a_metadata_node_is_never_offered_and_each_camera_keeps_its_reboot_name(tmp_path):
    cams = _scan(tmp_path, [("video0", "Insta360 Link 2", 0, "1-2", "removable"),
                            ("video1", "Insta360 Link 2", 1, "1-2", "removable"),
                            ("video2", "Integrated Camera", 0, "3-4", "fixed")],
                 caps={"video0": True, "video1": False, "video2": True},
                 ctls={"video0": {"pan": True, "tilt": True, "zoom": True}})
    assert [c["node"].rsplit("/", 1)[1] for c in cams] == ["video0", "video2"]
    assert cams[0]["by_id"].endswith("usb-Insta360_Link_2-video-index0") and cams[0]["usb"] == "1-2"
    assert cams[0]["pan"] and cams[0]["removable"] and cams[1]["builtin"] and not cams[1]["pan"]


def test_a_node_that_cannot_be_asked_counts_by_its_sysfs_index(tmp_path):
    cams = _scan(tmp_path, [("video0", "Cam", 0, "1-1", "removable"), ("video1", "Cam", 1, "1-1", "removable")],
                 caps={})
    assert [c["index"] for c in cams] == [0]


def test_best_is_a_camera_that_pans_then_a_plugged_in_one_then_the_lowest_number():
    builtin = {"node": "/dev/video0", "index": 0, "capture": True, "builtin": True, "removable": False}
    usb = {"node": "/dev/video2", "index": 2, "capture": True, "removable": True}
    ptz = {"node": "/dev/video4", "index": 4, "capture": True, "removable": True, "pan": True}
    assert C.best([builtin, usb])["node"] == "/dev/video2", "the laptop's own camera is not the room camera"
    assert C.best([builtin, usb, ptz])["node"] == "/dev/video4"
    assert C.best([builtin])["node"] == "/dev/video0"
    assert C.best([]) is None


CAMS = [{"node": "/dev/video0", "index": 0, "by_id": "/x/builtin-index0", "capture": True, "builtin": True},
        {"node": "/dev/video2", "index": 2, "by_id": "/x/elgato-index0", "capture": True, "removable": True}]


def test_resolve_env_wins_then_the_pick_then_the_best(tmp_path):
    assert C.resolve(tmp_path, env={"RAIL_CAMERA_DEVICE": "3"}, cams=CAMS) == ("3", "/dev/video3", "RAIL_CAMERA_DEVICE")
    assert C.resolve(tmp_path, env={"CRISSA_CAMERA_PICK": "0"}, cams=CAMS)[:2] == ("0", "/dev/video0")
    dev, node, why = C.resolve(tmp_path, env={}, cams=CAMS, platform="linux")
    assert (dev, node) == ("2", "/dev/video2") and "best" in why
    C.write_choice(tmp_path, CAMS[0], by="setup")
    assert C.resolve(tmp_path, env={}, cams=CAMS, platform="linux")[:3] == ("0", "/dev/video0", "picked in Setup")


def test_a_picked_camera_that_moved_ports_is_found_by_its_name_and_one_unplugged_falls_back(tmp_path):
    C.write_choice(tmp_path, CAMS[1], by="setup")
    moved = [dict(CAMS[0]), dict(CAMS[1], node="/dev/video6", index=6)]
    assert C.resolve(tmp_path, env={}, cams=moved, platform="linux")[:2] == ("6", "/dev/video6")
    dev, node, why = C.resolve(tmp_path, env={}, cams=[CAMS[0]], platform="linux")
    assert (dev, node) == ("0", "/dev/video0") and "not here" in why
    assert C.resolve(tmp_path, env={}, cams=[], platform="linux")[:2] == ("0", "/dev/video0")


def test_the_choice_file_drops_an_old_check_when_the_camera_changes(tmp_path):
    C.write_choice(tmp_path, CAMS[0], check={"state": "done", "fps": 15})
    assert C.read_choice(tmp_path)["check"]["fps"] == 15
    C.write_choice(tmp_path, CAMS[0])
    assert "check" in C.read_choice(tmp_path), "the same camera keeps its check"
    C.write_choice(tmp_path, CAMS[1])
    d = C.read_choice(tmp_path)
    assert "check" not in d and d["by_id"] == "/x/elgato-index0"


def test_a_mac_lists_its_cameras_by_system_profiler():
    class R:
        stdout = json.dumps({"SPCameraDataType": [{"_name": "FaceTime HD Camera (Built-in)"},
                                                  {"_name": "Elgato Facecam", "spcamera_unique-id": "0xabc"}]})
    cams = C.scan(platform="darwin", run=lambda *a, **k: R())
    assert [c["node"] for c in cams] == ["0", "1"] and cams[0]["builtin"] and cams[1]["removable"]
    assert C.best(cams)["name"] == "Elgato Facecam"


def test_the_camera_service_and_presence_read_the_same_answer():
    cam = (ROOT / "modules/presence/scripts/crissa-camera.py").read_text(encoding="utf-8")
    assert 'default=os.environ.get("RAIL_CAMERA_DEVICE", "")' in cam, "the first camera is no longer forced"
    grab = (ROOT / "modules/presence/crissa_presence/grabber.py").read_text(encoding="utf-8")
    assert "DEVICE, V4L_DEV, DEVICE_WHY = _camera_device()" in grab
    assert '"device_why": DEVICE_WHY' in grab and '"trigger_motion_min": TRIGGER_MOTION_MIN' in grab
    fw = (ROOT / "modules/presence/crissa_presence/firmware_facelock.py").read_text(encoding="utf-8")
    assert "DEVICE = _device()" in fw and "camera_pick.resolve(HOME)[1]" in fw


# ── Setup ──────────────────────────────────────────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(ROOT / "lib"))


def _init(monkeypatch, tmp_path):
    import crissa_init as I
    monkeypatch.setattr(I, "_home", lambda: tmp_path)
    monkeypatch.setattr(I, "camera_off", lambda: False)
    monkeypatch.setattr(I, "SYNC", True)
    monkeypatch.setattr(I, "camera_list", lambda fresh=False: [dict(c) for c in CAMS])
    monkeypatch.delenv("CRISSA_CAMERA_PICK", raising=False)
    return I


def test_setup_lists_the_cameras_with_the_best_preselected(monkeypatch, tmp_path):
    I = _init(monkeypatch, tmp_path)
    v = I.camera_view()
    assert [c["name"] for c in v["choices"]] == ["/x/builtin-index0", "/x/elgato-index0"]
    assert v["pick"] == "/x/elgato-index0" and [c["best"] for c in v["choices"]] == [False, True]
    monkeypatch.setenv("CRISSA_CAMERA_PICK", "0")
    assert I.camera_view()["off"] is True


def test_a_pick_is_written_restarts_camera_and_presence_and_is_checked(monkeypatch, tmp_path):
    I = _init(monkeypatch, tmp_path)
    restarted = []
    out = I.set_camera("/x/builtin-index0", restart=lambda n: restarted.append(n) or {"ok": True},
                       check=lambda cam: {"state": "done", "fps": 14.8, "still_p95": 0.4, "trigger": 8.0})
    assert out["ok"] and restarted == ["camera", "presence"]
    d = C.read_choice(tmp_path)
    assert d["by_id"] == "/x/builtin-index0" and d["check"]["fps"] == 14.8
    v = I.camera_view()
    assert v["pick"] == "/x/builtin-index0" and "15 pictures a second" in v["line"] and "0.4" in v["line"]
    assert I.set_camera("/x/gone")["ok"] is False


def test_the_check_counts_pictures_and_reads_a_still_room(monkeypatch, tmp_path):
    I = _init(monkeypatch, tmp_path)
    clock = {"t": 100.0}
    frames = iter([0, 0, 10, 30, 105])

    def get(path):
        if path.startswith("/v1/energy"):
            return {"rows": [{"energy": e} for e in [0.2, 0.3, 0.3, 0.4, 5.0, -1.0]]}
        return {"ok": True, "frames": next(frames), "hz": 15.0, "trigger_motion_min": 8.0}

    def sleep(s):
        clock["t"] += s

    chk = I.camera_check({}, seconds=5, get=get, sleep=sleep, now=lambda: clock["t"])
    assert chk["state"] == "done" and chk["fps"] == 15.0 and chk["still_p50"] == 0.3
    assert chk["still_p95"] == 5.0 and "busy" in chk["note"]


def test_a_camera_that_gives_no_pictures_fails_the_check(monkeypatch, tmp_path):
    I = _init(monkeypatch, tmp_path)
    clock = {"t": 0.0}
    chk = I.camera_check({}, seconds=5, get=lambda p: {"ok": False, "error": "cannot open V4L 2"},
                         sleep=lambda s: clock.__setitem__("t", clock["t"] + s), now=lambda: clock["t"])
    assert chk == {"state": "failed", "why": "cannot open V4L 2"}
    assert "did not finish" in I.camera_check_line(chk)


def test_the_page_offers_the_camera_list_beside_the_microphone():
    page = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")
    assert 'camList(m.camera, "m-cam")' in page and 'act("camera_pick", { camera: ev.target.value })' in page
    src = (ROOT / "lib" / "crissa_init.py").read_text(encoding="utf-8")
    assert '"camera_pick")' in src and 'if act == "camera_pick":' in src


def test_only_a_camera_that_turns_is_re_aimed_by_the_door_watchdogs():
    """Measured 2026-10-31 on an Elgato Facecam 4K: its pan is a crop and reads 0 for good, so the house's factory-home
    guard re-aimed it every 0.8 s and the camera gave 0.4 pictures a second; with the guard it gave 15.0."""
    assert C.ptz_kind("Elgato Facecam 4K", True) == "digital"
    assert C.ptz_kind("Insta360 Link 2: Insta360 Link", True) == "motor"
    assert C.ptz_kind("Integrated Camera", False) == "none"
    grab = (ROOT / "modules/presence/crissa_presence/grabber.py").read_text(encoding="utf-8")
    assert 'os.environ.get("CRISSA_CAMERA_RECOVER_MOTOR_ONLY", "1")' in grab
    for fn in ("def _recover_from_factory_home(", "def _recover_rest_to_door("):
        body = grab.split(fn, 1)[1].split("\ndef ", 1)[0]
        assert "if _recover_needs_motor():" in body, fn
    factory = grab.split("def _recover_from_factory_home(", 1)[1].split("\ndef ", 1)[0]
    assert "if RECOVER_MOTOR_ONLY and abs(want) <= FACTORY_HOME_TOL:" in factory, "a door straight ahead is the door"


def test_the_check_skips_the_cameras_first_seconds():
    src = (ROOT / "lib" / "crissa_init.py").read_text(encoding="utf-8")
    assert "sleep(CAM_SETTLE_S)" in src and 'float(r.get("ts", t0)) >= t0' in src
