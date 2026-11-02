"""Which camera is the room camera: found from the system's own files, picked in Setup, kept by a name that survives a
reboot (the User 2026-10-31, after 1.0.4: "When a dropdown list for Sound why not a dropdown list for camera? ... can we
detect settings invisibly there? Just in background based on system files?").

Before this the camera was always the computer's first one (RAIL_CAMERA_DEVICE "0", RAIL_CAMERA_V4L_DEV /dev/video0):
on a laptop with a USB webcam that is usually the built-in one, and a UVC camera's second node (/dev/video1 on the
Insta360 Link 2) is a metadata node that gives no picture at all.

    scan()      every camera, read without taking a picture: /sys/class/video4linux (name, USB port, removable),
                /dev/v4l/by-id (the name that survives a reboot), VIDIOC_QUERYCAP (does the node give pictures?),
                and, when v4l2-ctl is on the computer, whether it pans / tilts / zooms. On a Mac: system_profiler.
    best()      the camera to use when nobody picked one: one that gives pictures; one that turns first (a motor
                pan by fixed_cam.ptz_kind: a room camera), then a plugged-in one (removable USB) over a built-in one,
                then the lowest number.
    resolve()   (device for OpenCV, node for the controls, why): RAIL_CAMERA_DEVICE still wins; else the camera picked
                in Setup (<state home>/camera.json, by its by-id name); else best(); else the old "0" / /dev/video0.
    write_choice() / read_choice()   <state home>/camera.json, written by Setup's Camera list.

Standard library only: Setup (the kit's own Python) and the camera and presence services (the presence env) both load
it. Kill: CRISSA_CAMERA_PICK=0 (the first camera, as before).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

CHOICE_FILE = "camera.json"
SYS_ROOT = "/sys/class/video4linux"
BY_ID = "/dev/v4l/by-id"
# VIDIOC_QUERYCAP = _IOR('V', 0, struct v4l2_capability): 104 bytes; capabilities at 84, device_caps at 88
VIDIOC_QUERYCAP = 0x80685600
CAP_VIDEO_CAPTURE = 0x00000001
CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
CAP_DEVICE_CAPS = 0x80000000


def on(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("CRISSA_CAMERA_PICK", "1")).strip().lower() not in ("0", "off", "false", "no")


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def querycap(node: str) -> bool | None:
    """Does this node give pictures? None when it cannot be asked (no permission, gone)."""
    try:
        fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        buf = bytearray(104)
        fcntl.ioctl(fd, VIDIOC_QUERYCAP, buf, True)
        caps, dcaps = struct.unpack_from("<II", buf, 84)
        use = dcaps if caps & CAP_DEVICE_CAPS else caps
        return bool(use & (CAP_VIDEO_CAPTURE | CAP_VIDEO_CAPTURE_MPLANE))
    except OSError:
        return None
    finally:
        os.close(fd)


def controls(node: str, run=subprocess.run) -> dict:
    """{"pan": bool, "tilt": bool, "zoom": bool} from v4l2-ctl's control list; {} when v4l2-ctl is not here."""
    if not shutil.which("v4l2-ctl"):
        return {}
    try:
        r = run(["v4l2-ctl", "-d", node, "-l"], capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if r.returncode != 0:
        return {}
    names = set(re.findall(r"^\s*(\w+)\s+0x[0-9a-f]+", r.stdout or "", re.M))
    return {"pan": "pan_absolute" in names, "tilt": "tilt_absolute" in names, "zoom": "zoom_absolute" in names}


def formats(node: str, run=subprocess.run) -> list[dict]:
    """[{"size": "1280x720", "fps": [60.0, 30.0, ...]}] from v4l2-ctl --list-formats-ext; [] without v4l2-ctl."""
    if not shutil.which("v4l2-ctl"):
        return []
    try:
        r = run(["v4l2-ctl", "-d", node, "--list-formats-ext"], capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out: dict[str, set] = {}
    size = ""
    for line in (r.stdout or "").splitlines():
        m = re.search(r"Size: \w+ (\d+x\d+)", line)
        if m:
            size = m.group(1)
            out.setdefault(size, set())
            continue
        m = re.search(r"\(([\d.]+) fps\)", line)
        if m and size:
            out[size].add(float(m.group(1)))
    return [{"size": s, "fps": sorted(f, reverse=True)} for s, f in out.items()]


def _fixed_cam():
    """The presence module's own motor-vs-digital rule (fixed_cam.ptz_kind: a camera that reports a pan is a motor only
    by its model's name; any other pan is a crop of one view - the Elgato Facecam 4K reports pan/tilt/zoom and never
    turns). One rule, never a second list here."""
    try:
        from . import fixed_cam
        return fixed_cam
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location("camera_pick_fixed_cam", Path(__file__).with_name("fixed_cam.py"))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001
            return None
        return mod


def ptz_kind(name: str, pan: bool) -> str:
    """'motor' | 'digital' | 'none'."""
    fc = _fixed_cam()
    if fc is None:
        return "motor" if pan else "none"
    return fc.ptz_kind(name, bool(pan))


def _by_id_names(by_id: str) -> dict[str, str]:
    """/dev/videoN -> its /dev/v4l/by-id name (the name that survives a reboot and a different plug order)."""
    out: dict[str, str] = {}
    try:
        for e in sorted(os.listdir(by_id)):
            p = os.path.join(by_id, e)
            out.setdefault(os.path.realpath(p), p)
    except OSError:
        pass
    return out


def scan(sys_root: str = SYS_ROOT, by_id: str = BY_ID, dev_root: str = "/dev", cap=querycap, ctl=controls,
         platform: str | None = None, run=subprocess.run) -> list[dict]:
    """Every camera on this computer, no picture taken. Each: {node, index, name, by_id, usb, removable, capture,
    pan, tilt, zoom}. capture None = could not be asked (then a node whose sysfs index is 0 counts as the picture one)."""
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return _scan_mac(run)
    ids = _by_id_names(by_id)
    cams = []
    try:
        entries = sorted(os.listdir(sys_root), key=lambda n: int(re.sub(r"\D", "", n) or 0))
    except OSError:
        return []
    for n in entries:
        if not n.startswith("video"):
            continue
        d = Path(sys_root) / n
        node = os.path.join(dev_root, n)
        dev = Path(os.path.realpath(d / "device"))
        usb = dev.parent.name if dev.parent.name and re.match(r"^\d+-[\d.]+$", dev.parent.name) else ""
        removable = _read(dev.parent / "removable")
        got = cap(node)
        if got is None:
            got = _read(d / "index") in ("", "0")
        if not got:
            continue                                # a metadata node: no picture, never offered
        c = {"node": node, "index": int(re.sub(r"\D", "", n) or 0), "name": _read(d / "name") or n,
             "by_id": ids.get(os.path.realpath(node), ""), "usb": usb,
             "removable": removable == "removable", "builtin": removable == "fixed", "capture": True}
        c.update({k: False for k in ("pan", "tilt", "zoom")})
        c.update(ctl(node) or {})
        c["ptz"] = ptz_kind(c["name"], c.get("pan"))
        cams.append(c)
    return cams


def _scan_mac(run=subprocess.run) -> list[dict]:
    try:
        r = run(["system_profiler", "SPCameraDataType", "-json"], capture_output=True, text=True, timeout=10)
        rows = (json.loads(r.stdout or "{}") or {}).get("SPCameraDataType") or []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    out = []
    for i, row in enumerate(rows):
        name = str(row.get("_name") or "Camera %d" % i)
        out.append({"node": str(i), "index": i, "name": name, "by_id": str(row.get("spcamera_unique-id") or ""),
                    "usb": "", "removable": "built" not in name.lower(), "builtin": "built" in name.lower(),
                    "capture": True, "pan": False, "tilt": False, "zoom": False, "ptz": "none"})
    return out


def best(cams: list[dict]) -> dict | None:
    """One that turns (a motor pan) first, then a plugged-in one over a built-in one, then the lowest number."""
    ok = [c for c in cams if c.get("capture")]
    if not ok:
        return None
    return sorted(ok, key=lambda c: (c.get("ptz", "motor" if c.get("pan") else "none") != "motor",
                                     not c.get("removable"), c.get("index", 0)))[0]


def choice_path(home) -> Path:
    return Path(home) / CHOICE_FILE


def read_choice(home) -> dict:
    try:
        d = json.loads(choice_path(home).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_choice(home, cam: dict, by: str = "setup", check: dict | None = None) -> dict:
    d = read_choice(home)
    same = (d.get("by_id") or d.get("node")) == (cam.get("by_id") or cam.get("node"))
    d.update({"by_id": cam.get("by_id") or "", "node": cam.get("node") or "", "name": cam.get("name") or "",
              "usb": cam.get("usb") or "", "pan": bool(cam.get("pan")), "by": by,
              "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    if check is not None:
        d["check"] = check
    elif not same:
        d.pop("check", None)
    p = choice_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return d


def find(cams: list[dict], want: str) -> dict | None:
    """A camera by its node, its by-id name or its index."""
    want = str(want or "").strip()
    for c in cams:
        if want and want in (c.get("node"), c.get("by_id"), str(c.get("index"))):
            return c
    return None


def resolve(home, env: dict | None = None, cams: list[dict] | None = None,
            platform: str | None = None) -> tuple[str, str, str]:
    """(device for OpenCV, node for the controls, why)."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    dev = str(env.get("RAIL_CAMERA_DEVICE") or "").strip()
    v4l = str(env.get("RAIL_CAMERA_V4L_DEV") or "").strip()
    if dev:
        return dev, v4l or ("/dev/video" + dev if dev.isdigit() else dev), "RAIL_CAMERA_DEVICE"
    if not on(env):
        return "0", v4l or "/dev/video0", "CRISSA_CAMERA_PICK=0"
    if cams is None:
        cams = scan(platform=platform)
    picked = read_choice(home)
    cam = None
    why = ""
    if picked:
        cam = find(cams, picked.get("by_id") or "") or (None if picked.get("by_id") else find(cams, picked.get("node")))
        why = "picked in Setup" if cam else "the camera picked in Setup is not here; "
    if cam is None:
        cam = best(cams)
        why += "best of %d found" % len(cams) if cam else ""
    if cam is None:
        return "0", v4l or "/dev/video0", (why + "no camera found; the first one").strip()
    if platform == "darwin":
        return str(cam["index"]), v4l or "", why
    return str(cam["index"]), v4l or cam["node"], why
