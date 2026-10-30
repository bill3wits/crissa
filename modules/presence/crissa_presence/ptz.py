"""V4L PTZ helpers — v4l2-ctl only, no Crissa imports.

Critical (measured): always read pan_absolute back; refuse frame if actual
is not within pan_tol of commanded.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass


@dataclass
class AimResult:
    ok: bool
    pan_cmd: int
    pan_actual: int | None
    tilt_cmd: int
    tilt_actual: int | None
    error: str | None = None


def _ctl(
    device: str, *args: str, timeout: float = 10.0
) -> subprocess.CompletedProcess:
    from . import ports as _ports

    if _ports.camera_off():                      # no camera: the device is not asked (2026-10-10)
        _ports.say_once("PTZ controls are no-ops")
        return subprocess.CompletedProcess(["v4l2-ctl", "-d", device, *args], 1, "", "camera: off")
    try:
        return subprocess.run(
            ["v4l2-ctl", "-d", device, *args],
            capture_output=True,
            text=True,
            timeout=float(timeout),
        )
    except OSError as e:                         # no v4l2-ctl here (v4l-utils not installed): a failed read, never a crash
        return subprocess.CompletedProcess(["v4l2-ctl", "-d", device, *args], 1, "", "v4l2-ctl: %s" % e)


def get_ctrl(device: str, name: str) -> int | None:
    r = _ctl(device, f"--get-ctrl={name}")
    if r.returncode != 0:
        return None
    # pan_absolute: 3600   (v4l2-ctl --get-ctrl prints "name: value")
    m = re.search(r":\s*(-?\d+)", r.stdout)
    return int(m.group(1)) if m else None


def set_ctrl(
    device: str, name: str, value: int, *, timeout: float = 10.0
) -> bool:
    r = _ctl(device, f"--set-ctrl={name}={int(value)}", timeout=timeout)
    return r.returncode == 0


def wrap_pan(pan: int, lo: int = -522000, hi: int = 522000) -> int:
    """Keep pan in device range with modular wrap (FILESYSTEM-v2)."""
    span = hi - lo
    return lo + ((int(pan) - lo) % span)


def aim(
    device: str,
    pan: int,
    tilt: int = 0,
    *,
    zoom: int | None = None,
    settle_sec: float = 1.2,
    pan_tol: int = 3600,
    retries: int = 2,
) -> AimResult:
    """Command pan/tilt/(zoom), wait, read back. ok only if within pan_tol.

    Always read pan_absolute back — never trust command alone
    (camera-control-reference + 2026-07-22 root fault).
    """
    pan = wrap_pan(pan)
    for _attempt in range(retries + 1):
        if not set_ctrl(device, "pan_absolute", pan):
            return AimResult(False, pan, None, tilt, None, "set pan failed")
        if not set_ctrl(device, "tilt_absolute", tilt):
            return AimResult(False, pan, None, tilt, None, "set tilt failed")
        if zoom is not None:
            set_ctrl(device, "zoom_absolute", int(zoom))
        time.sleep(settle_sec)
        pa = get_ctrl(device, "pan_absolute")
        ta = get_ctrl(device, "tilt_absolute")
        if pa is None:
            return AimResult(False, pan, None, tilt, ta, "readback pan failed")
        # compare with wrap distance
        span = 522000 - (-522000)
        d = abs(pa - pan)
        d = min(d, span - d)
        if d <= pan_tol:
            return AimResult(True, pan, pa, tilt, ta)
        time.sleep(0.3)
    return AimResult(
        False,
        pan,
        get_ctrl(device, "pan_absolute"),
        tilt,
        get_ctrl(device, "tilt_absolute"),
        f"pan mismatch cmd={pan} actual={get_ctrl(device, 'pan_absolute')}",
    )


# House door home — NOT firmware center (pan 0 = desk). See FIRMWARE-INSTA360-LINK2.
# Control step is 3600 units = 1 degree on both axes.
# the User 2026-08-09: "aim the camera up 5d ... missing the top of my face when I
# walk in." +5 deg puts the doorframe header fully in frame, so a tall crossing
# keeps its forehead instead of being cut at the top edge. Measured FOV at
# zoom 150 is 49.0 x 27.6 deg, so 5 deg = 131 px = 18% of frame height —
# changing this REQUIRES re-capturing data/body_audit_refs/door_home_live.jpg
# and re-measuring the door_home rect in data/face_zone_masks.json, or
# is_door_view() rejects real door frames and the hallway portraits escape
# their mask.
#
# 2026-08-14: pose comes from room_geometry.json heading (init). heading.door_hour is the clock label - do not
# recompute pan from it. 2026-10-25: no room's numbers here - an untaught camera rests on its own forward
# (room_heading), and the pose is read when aimed, so a door taught in Setup holds without a restart. The DOOR_HOME_*
# names stay for callers that import them; they are the pose at import, and door_pose() is the live one.
from .room_heading import door_home_pose as _door_home_pose

_DOOR = _door_home_pose()
DOOR_HOME_PAN = int(_DOOR["pan"])
DOOR_HOME_TILT = int(_DOOR["tilt"])
DOOR_HOME_ZOOM = int(_DOOR["zoom"])


def door_pose() -> dict[str, int]:
    """The door rest as it stands now (Setup may have taught it since this module loaded)."""
    return _door_home_pose()


def pan_error(actual: int | None, cmd: int) -> int:
    if actual is None:
        return 10**9
    span = 522000 - (-522000)
    d = abs(int(actual) - int(cmd))
    return min(d, span - d)


def aim_door_home(
    device: str,
    *,
    pan: int | None = None,
    tilt: int | None = None,
    zoom: int | None = None,
    settle_sec: float = 1.6,
    pan_tol: int = 3600,
    retries: int = 6,
) -> AimResult:
    """Force door FOV after facefinder/scan.

    Firmware forward/center is pan≈0. Room *hold* home is the taught door pose (door_pose()).
    Facefinder release often leaves or snaps toward 0 — re-assert door hard.
    """
    d = door_pose()
    return aim(
        device,
        int(d["pan"] if pan is None else pan),
        int(d["tilt"] if tilt is None else tilt),
        zoom=int(d["zoom"] if zoom is None else zoom),
        settle_sec=settle_sec,
        pan_tol=pan_tol,
        retries=retries,
    )
