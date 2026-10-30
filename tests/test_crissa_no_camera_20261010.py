"""With no camera, nothing in the pack talks to a camera (the User 2026-10-10).

The trial of 2026-10-10 ran with --no-camera, which stopped the pack's own grabber and nothing else: the presence
service's door-first start still posted /v1/facelock and /v1/return_door, and the oracle opened camera index 0 itself.
The rule now: with --no-camera / CRISSA_NO_CAMERA=1 the launcher hands the switch to every child; the presence
service's camera calls are no-ops that answer {"camera": "off"} (camera.py's "off" backend, the door aim, the grabber
and broker posts, the PTZ controls, and a backstop that refuses any request to a camera address); the oracle opens no
capture device; the feed's camera cell answers the mark. With a camera configured nothing changes, and the control
cases below prove the harness sees camera calls when there are some.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import types
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PMOD = ROOT / "modules" / "presence"
OMOD = ROOT / "modules" / "oracle"
OFFSET = 10000
REFERENCE_PORTS = {8099, 8100, 8190, 8191, 8192, 8193, 8194, 8195, 8196, 8197, 8219, 8000, 8005, 7789, 8188}

# ---- presence: a start and one door-first tick, every HTTP request recorded at http.client ----------------------
_PRESENCE = r"""
import http.client, importlib.util, json, os, socket, sys, threading, time
from pathlib import Path
mod, no_camera = Path(sys.argv[1]), sys.argv[2] == "1"
calls = []
def record(self, method, url, body=None, headers={}, **kw):
    calls.append({"host": self.host, "port": self.port, "method": method, "path": url.split("?", 1)[0]})
    raise ConnectionRefusedError("the test's network is a recorder")
http.client.HTTPConnection.request = record
http.client.HTTPSConnection.request = record
if no_camera:
    os.environ["CRISSA_NO_CAMERA"] = "1"          # what the launcher hands the presence child under --no-camera
# what modules/presence/scripts/crissa-presence.py does before the service loads (its install check aside)
spec = importlib.util.spec_from_file_location("presence_launch", mod / "scripts" / "crissa-presence.py")
launch = importlib.util.module_from_spec(spec); spec.loader.exec_module(launch)
from crissa_presence import ports as P
cam = os.environ.get("RAIL_CAMERA_URL") or P.url("camera")
os.environ.setdefault("RAIL_PRESENCE_CAMERA", "off" if P.camera_off() else "http")
for k, v in launch.pinned_addresses(cam).items():
    os.environ.setdefault(k, v)
os.environ.setdefault("RAIL_PRESENCE_DOOR_FIRST", "1")
os.environ.setdefault("RAIL_PRESENCE_USE_BUFFER", "1")
P.guard_camera()
from crissa_presence import server
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
threading.Thread(target=server.main, kwargs={"port": port}, daemon=True).start()
deadline = time.time() + 40
svc = None
while time.time() < deadline:
    svc = server._svc
    if svc is not None and (svc.cycles >= 1 or svc.door_cycles >= 1):
        break
    time.sleep(0.2)
time.sleep(2.0)
aim = svc._ensure_door_aim(release_facelock=True) if svc is not None else None
print(json.dumps({"calls": calls, "cycles": getattr(svc, "cycles", None), "door_cycles": getattr(svc, "door_cycles", None),
                  "mode": getattr(getattr(svc, "camera", None), "mode", None), "aim": aim,
                  "refused": list(P.REFUSED), "camera_port": P.port("camera")}))
"""


def _presence_run(tmp_path: Path, no_camera: bool) -> dict:
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "HOUSE_ID_ROOT", "CRISSA_NO_CAMERA", "RAIL_PRESENCE_CAMERA") and not k.startswith(("RAIL_", "CRISSA_PORT"))}
    env.update(CRISSA_HOME=str(tmp_path / "home"), CRISSA_PORT_OFFSET=str(OFFSET), PYTHONPATH=str(PMOD),
               RAIL_GUEST_IDS="val", RAIL_OCCUPANCY_IDS="user", HOUSE_USER_ID="user")
    r = subprocess.run([sys.executable, "-c", _PRESENCE, str(PMOD), "1" if no_camera else "0"], capture_output=True,
                       text=True, timeout=240, env=env, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr[-4000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    out["stderr"] = r.stderr
    return out


def test_no_camera_a_presence_start_and_a_door_first_tick_make_no_camera_call(tmp_path):
    got = _presence_run(tmp_path, no_camera=True)
    assert got["camera_port"] == 8192 + OFFSET
    assert got["mode"] == "off", "the camera backend is off"
    assert (got["cycles"] or 0) >= 1 or (got["door_cycles"] or 0) >= 1, "the door-first tick ran"
    cam = [c for c in got["calls"] if c["port"] in (8192, 8192 + OFFSET)]
    assert cam == [], "a camera call left the process: %s" % cam
    assert not [c for c in cam if c["method"] == "POST"], "zero camera POSTs"
    ref = [c for c in got["calls"] if c["port"] in REFERENCE_PORTS]
    assert ref == [], "a call reached a reference install's port: %s" % ref
    assert got["aim"] == {"ok": False, "camera": "off"}, "the door aim answers the mark"
    assert got["refused"] == [], "a start and its ticks need no backstop: every camera path is an explicit no-op"
    assert "camera: off" in got["stderr"], "said once"


def test_with_a_camera_the_same_start_asks_this_installs_camera_and_never_the_reference(tmp_path):
    """The control: the recorder does see camera calls, and they go to this install's port, not 8192."""
    got = _presence_run(tmp_path, no_camera=False)
    assert got["mode"] == "http"
    cam = [c for c in got["calls"] if c["port"] == 8192 + OFFSET]
    assert cam, "the harness records camera calls when a camera is configured"
    assert any(c["method"] == "POST" for c in cam), cam
    assert not [c for c in got["calls"] if c["port"] in REFERENCE_PORTS], "beside another install: its ports untouched"
    assert got["refused"] == [], "with a camera on nothing is refused"


def test_the_backstop_refuses_a_camera_address_and_lets_others_through(monkeypatch):
    sys.path.insert(0, str(PMOD))
    try:
        spec = importlib.util.spec_from_file_location("guard_presence_ports_nc", PMOD / "crissa_presence" / "ports.py")
        P = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(P)
    finally:
        sys.path.remove(str(PMOD))
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda target, *a, **k: seen.append(target) or "passed")
    monkeypatch.setenv("CRISSA_PORT_OFFSET", str(OFFSET))
    monkeypatch.delenv("CRISSA_NO_CAMERA", raising=False)
    monkeypatch.delenv("RAIL_PRESENCE_CAMERA", raising=False)
    assert P.guard_camera() is False, "a camera on: nothing wrapped"
    assert urllib.request.urlopen("http://127.0.0.1:18192/v1/facelock") == "passed"
    monkeypatch.setenv("CRISSA_NO_CAMERA", "1")
    assert P.guard_camera() is True
    with pytest.raises(urllib.error.URLError, match="camera: off"):
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:18192/v1/return_door", data=b"{}"))
    monkeypatch.setenv("RAIL_CAMERA_URL", "http://10.9.8.7:7000")
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen("http://10.9.8.7:7000/snapshot")
    assert urllib.request.urlopen("http://127.0.0.1:18219/v1/presence") == "passed", "not a camera: through"
    assert P.REFUSED[-2:] == ["/v1/return_door", "/snapshot"]
    monkeypatch.setattr(urllib.request, "urlopen", P._orig_urlopen)


# ---- the oracle: no capture device ----------------------------------------------------------------------------
def _oracle(monkeypatch):
    opened = []

    class Cap:
        def __init__(self, idx):
            opened.append(idx)

        def isOpened(self):
            return False

        def release(self):
            pass

    monkeypatch.setitem(sys.modules, "cv2", types.SimpleNamespace(VideoCapture=Cap))
    sys.path.insert(0, str(OMOD))
    try:
        from crissa_oracle import camera, service
    finally:
        sys.path.remove(str(OMOD))
    return camera, service, opened


def test_no_camera_the_oracle_never_opens_a_capture_device(monkeypatch, capsys):
    monkeypatch.setenv("CRISSA_NO_CAMERA", "1")
    camera, service, opened = _oracle(monkeypatch)
    cam = camera.Camera()
    assert cam.off is True
    assert cam.read() is None and cam.read() is None
    orc = service.Oracle(cam=cam, look=lambda frame, enrolled: None, post=None)
    st = orc.tick(now=1.0)
    assert opened == [], "cv2.VideoCapture was called"
    assert st["camera"] is False and st["camera_off"] is True and st["ok"] is True
    assert capsys.readouterr().err.count("camera: off") == 1, "said once"


def test_the_oracle_switch_names(monkeypatch):
    camera, _service, _opened = _oracle(monkeypatch)
    assert camera.off({"CRISSA_NO_CAMERA": "1"}) and camera.off({"CRISSA_ORACLE_CAMERA": "off"})
    assert not camera.off({}) and not camera.off({"CRISSA_ORACLE_CAMERA": "0"})


def test_with_a_camera_the_oracle_opens_its_index_as_before(monkeypatch):
    monkeypatch.delenv("CRISSA_NO_CAMERA", raising=False)
    monkeypatch.delenv("CRISSA_ORACLE_CAMERA", raising=False)
    camera, _service, opened = _oracle(monkeypatch)
    cam = camera.Camera(clock=lambda: 0.0)
    assert cam.off is False
    assert cam.read() is None and opened == [0], "the control: VideoCapture(0) is what a camera-on start does"


# ---- the launcher hands the switch to every child --------------------------------------------------------------
def _launcher():
    spec = importlib.util.spec_from_file_location("crissa_launcher_nc", ROOT / "scripts" / "crissa.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _args(**kw):
    base = dict(face="crissa", audio_at="host", port_offset=OFFSET, voice=None, with_asr=False, with_aligner=False,
                no_camera=False, no_enroll=False, no_desk=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_launcher_passes_no_camera_to_the_oracle_presence_and_every_child(monkeypatch):
    z = _launcher()
    monkeypatch.setattr(z, "presence_module_installed", lambda pmod: True)
    p = z.plan(_args(no_camera=True), env_in={})
    names = [k["name"] for k in p["children"]]
    assert "camera" not in names and "oracle" in names and "presence" in names and "enroll" in names
    for k in p["children"]:
        assert k["env"].get("CRISSA_NO_CAMERA") == "1", k["name"]
    enroll = next(k for k in p["children"] if k["name"] == "enroll")
    assert "RAIL_CAMERA_URL" not in enroll["env"] and "CRISSA_CAMERA_URL" not in enroll["env"], \
        "an empty camera address made the coach fall back to a fixed port"
    on = z.plan(_args(), env_in={})
    assert "camera" in [k["name"] for k in on["children"]]
    assert all("CRISSA_NO_CAMERA" not in k["env"] for k in on["children"]), "a camera on: no switch handed"


def test_the_presence_and_oracle_scripts_take_the_switch_by_flag_too():
    src = (PMOD / "scripts" / "crissa-presence.py").read_text()
    assert '"--no-camera"' in src and '"off" if _P.camera_off() else "http"' in src
    assert '"--no-camera"' in (OMOD / "scripts" / "crissa-oracle.py").read_text()
    cam = (PMOD / "scripts" / "crissa-camera.py").read_text()
    assert "if _P.camera_off():" in cam, "the grabber script does not open the camera under the switch"


# ---- the feed's camera cell --------------------------------------------------------------------------------------
_FEED = r"""
import importlib.util, json, sys, threading, urllib.request, urllib.error
from pathlib import Path
spec = importlib.util.spec_from_file_location("feed_nc", Path(sys.argv[1]) / "scripts" / "voiceonly-home-server.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
asked = []
real = mod.fetch
mod.fetch = lambda url, *a, **k: asked.append(url) or (0, b"", "")
frame, pan = mod.get_frame(), mod._cam_pan()
mod.fetch = real
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
try:
    urllib.request.urlopen("http://127.0.0.1:%d/frame" % srv.server_address[1], timeout=10)
    cell = None
except urllib.error.HTTPError as e:
    cell = [e.code, json.loads(e.read().decode())]
srv.shutdown()
print(json.dumps({"asked": asked, "frame": frame, "pan": pan, "cell": cell, "off": mod.CAMERA_OFF,
                  "frame_url": mod.FRAME_URL, "firmware_url": mod.FIRMWARE_URL}))
"""


def _feed(tmp_path: Path, **env_over) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "HOUSE_DOCTOR_URL", "CRISSA_NO_CAMERA")
           and not k.startswith(("VO_", "CRISSA_PORT"))}
    (tmp_path / "poses").mkdir(exist_ok=True)
    (tmp_path / "poses" / "inventory.json").write_bytes((ROOT / "display" / "crissa-poses" / "inventory.json").read_bytes())
    env.update({"HOME": str(tmp_path / "home"), "CRISSA_HOME": str(tmp_path / "state"), "HOUSE_POSE_DIR": str(tmp_path / "poses"),
                "HOUSE_WALL_PREFS": str(tmp_path / "prefs.json"), "VO_GLASS_STATE": str(tmp_path / "glass.json"),
                "HOUSE_RENDER_WIP": "0", "CRISSA_PORT_OFFSET": str(OFFSET)})
    env.update(env_over)
    (tmp_path / "home").mkdir(exist_ok=True)
    r = subprocess.run([sys.executable, "-c", _FEED, str(ROOT)], cwd=str(tmp_path), env=env, capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-4000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_no_camera_the_feeds_camera_cell_shows_the_mark_and_asks_nothing(tmp_path):
    got = _feed(tmp_path, CRISSA_NO_CAMERA="1")
    assert got["off"] is True
    assert got["asked"] == [] and got["frame"] is None and got["pan"] is None
    assert got["cell"] == [503, {"ok": False, "camera": "off"}]


def test_with_a_camera_the_feeds_cell_asks_this_installs_grabber(tmp_path):
    got = _feed(tmp_path)
    assert got["off"] is False
    assert got["frame_url"] == "http://127.0.0.1:%d/snapshot" % (8192 + OFFSET)
    assert got["firmware_url"] == "http://127.0.0.1:%d/v1/firmware" % (8192 + OFFSET)
    assert got["asked"] == [got["frame_url"], got["firmware_url"]], "the control: the cell asks when a camera is on"
