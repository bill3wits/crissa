"""Room heading — monitor-12 frame + camera door-home pose.

the User 2026-08-14: User sits at 6, looks at 12, door is 10. USB = 12 stays
at the top of the icon — do not remap that top mark to 10. The door hour
is a label on the dial, not a rotation of incoming bearings.

Camera rest still *faces the door*. That pose is a measured pan/tilt/zoom,
taught in Setup on a camera that turns, never a guess from the hour.

heading.door_hour = monitor-frame door. Camera sitters stay v2. Do not smash the two clocks.

NOTHING HERE KNOWS A ROOM (the User 2026-10-25, the 1.0.0 stranger install: the kit rested a stranger's camera on
this office's door pan, and every face check read "not seen"; "Maybe Users could call me up and ask me to
hard code their doors"). Every pose and hour is MEASURED and read from the person's own state: the geometry's heading,
else (a pack) Setup's stations.heading in site.json, else the env. Untaught, the door hour is None (the dial shows no
door) and the rest pose is the camera's own forward - pan 0, level, 1x - which is the camera's default, not a room
fact: on a camera that sits on the screen it rests on the person. The pose is read when it is used (mtime-cached), so
a door taught in Setup holds without a restart.
"""
from __future__ import annotations

from . import paths as _paths

import json
import os
from pathlib import Path
from typing import Any

_GEOM = Path(
    os.environ.get(
        "ROOM_GEOMETRY",
        str(_paths.DATA / "room_geometry.json"),
    )
)

# The camera's own forward (V4L2 pan/tilt 0, zoom 100 = 1x): where an untaught camera rests. No room's numbers.
_FALLBACK_PAN = 0
_FALLBACK_TILT = 0
_FALLBACK_ZOOM = 100
_FALLBACK_DOOR_HOUR = None   # untaught: no door on the dial


_CACHE: dict[str, tuple] = {}


def _read_json(path: Path) -> dict[str, Any]:
    """A room file, re-read only when it changes (the grabber asks for the door pose inside its frame loop)."""
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        _CACHE.pop(str(path), None)
        return {}
    hit = _CACHE.get(str(path))
    if hit and hit[0] == mt:
        return hit[1]
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        d = d if isinstance(d, dict) else {}
    except Exception:
        d = {}
    _CACHE[str(path)] = (mt, d)
    return d


def _geom() -> dict[str, Any]:
    return _read_json(_GEOM)


def heading() -> dict[str, Any]:
    h = (_geom().get("heading") or {}) if isinstance(_geom().get("heading"), dict) else {}
    return dict(h)


def _site_heading() -> dict[str, Any]:
    """A pack's Init answer (the User 2026-10-11, Your room): site.json stations.heading in the identity store, as the
    house's own Init writes it. Read for the dial's DOOR tick only, under the geometry's own heading; never on the house,
    and never for pose_hflip (a tap on the clock never mirrors her picture). Kill CRISSA_ROOM_FROM_SITE=0."""
    if os.environ.get("CRISSA_ROOM_FROM_SITE", "1").strip().lower() in ("0", "off", "false", "no"):
        return {}
    if _paths.is_house():
        return {}
    try:
        st = _read_json(_paths.IDENTITY / "site.json").get("stations") or {}
        h = st.get("heading") if isinstance(st, dict) else None
        return dict(h) if isinstance(h, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def door_hour() -> int | None:
    """Where the door sits on the monitor-12 clock (1–12): the geometry's, else (a pack) Init's Your room, else None."""
    h = heading()
    if h.get("door_hour") is None and h.get("door_az_deg") is None:
        h = _site_heading()
    try:
        hr = int(h["door_hour"]) if h.get("door_hour") is not None else None
    except (TypeError, ValueError):
        hr = None
    if hr is None and h.get("door_az_deg") is not None:
        try:
            hr = round(float(h["door_az_deg"]) % 360.0 / 30.0) or 12
        except (TypeError, ValueError):
            hr = None
    if hr == 0:
        hr = 12
    return hr if hr is not None and 1 <= hr <= 12 else _FALLBACK_DOOR_HOUR


def door_az_deg() -> float | None:
    """Clockwise degrees from monitor-12 to the door (hour 10 → 300); None while no door is taught."""
    h = heading()
    if h.get("door_hour") is None and h.get("door_az_deg") is None:
        h = _site_heading()
    if h.get("door_az_deg") is not None:
        try:
            return float(h["door_az_deg"]) % 360.0
        except (TypeError, ValueError):
            pass
    hr = door_hour()
    if hr is None:
        return None
    return 0.0 if hr == 12 else float(hr) * 30.0


def pose_hflip_from_az(az_deg: float | None, hour: int | None = None) -> bool:
    """Whether wall pose clips should HFLIP (door/hall on her *left*).

    Shot with hall on her right (viewer left). User at 6 faces 12.
    Sign test on monitor-frame azimuth about that 6–12 sightline:

      user-left  (180, 360) → False (as shot; this office door az 300)
      user-right (0, 180)   → True
      on-axis 0°/180° (12/6) or missing → False (global fail-safe)

    Dial ``door_az_deg`` is the pose frame (user 6→12), even though heading
    notes it is not a puck bearing. Do **not** derive from ``door_beam`` /
    ``camera_door_beam`` (camera sitters). Camera grabber HFLIP is the feed,
    not this bit.

    Background *light* (window/hall) is the same side as the door in the
    takes; we do not live-meter the still (exposure, empty chair, time of day).
    """
    az: float | None = None
    if az_deg is not None:
        try:
            az = float(az_deg) % 360.0
        except (TypeError, ValueError):
            az = None
    if az is None and hour is not None:
        try:
            hr = int(hour)
        except (TypeError, ValueError):
            hr = None
        else:
            if hr == 0:
                hr = 12
            if 1 <= hr <= 12:
                az = 0.0 if hr == 12 else float(hr) * 30.0
    if az is None:
        return False
    if az == 0.0 or az == 180.0:
        return False
    # User faces 12: left is 7–11 (180–360), right is 1–5 (0–180).
    return 0.0 < az < 180.0


def pose_hflip() -> bool:
    """Office bit for glass pose draws. Derive only — not stored."""
    h = heading()
    az = None
    if h.get("door_az_deg") is not None:
        try:
            az = float(h["door_az_deg"])
        except (TypeError, ValueError):
            az = None
    hour = None
    if h.get("door_hour") is not None:
        try:
            hour = int(h["door_hour"])
        except (TypeError, ValueError):
            hour = None
    if az is None and hour is None:
        # heading missing both: door_hour() firmware fallback would lie
        # "this office 10". Fail-safe as-shot.
        return False
    return pose_hflip_from_az(az, hour)


def _door_heading() -> dict[str, Any]:
    """The heading that carries a taught door pose: the geometry's, else (a pack) Setup's site.json heading."""
    h = heading()
    if h.get("door_home_pan") is None:
        site = _site_heading()
        if site.get("door_home_pan") is not None:
            return site
    return h


def door_taught() -> bool:
    """A door pose was measured (Setup, or the house's own Init) or named in the env - not the camera's forward."""
    return _door_heading().get("door_home_pan") is not None or bool(os.environ.get("RAIL_PRESENCE_DOOR_PAN", "").strip())


def door_home_pose() -> dict[str, int]:
    """PTZ that *faces* the door. Prefer the measured pose, then env, then the camera's own forward (untaught)."""
    h = _door_heading()
    pan = h.get("door_home_pan")
    tilt = h.get("door_home_tilt")
    zoom = h.get("door_home_zoom")
    try:
        pan_i = int(pan) if pan is not None else int(
            os.environ.get("RAIL_PRESENCE_DOOR_PAN", str(_FALLBACK_PAN))
        )
    except (TypeError, ValueError):
        pan_i = _FALLBACK_PAN
    try:
        tilt_i = int(tilt) if tilt is not None else int(
            os.environ.get("RAIL_PRESENCE_DOOR_TILT", str(_FALLBACK_TILT))
        )
    except (TypeError, ValueError):
        tilt_i = _FALLBACK_TILT
    try:
        zoom_i = int(zoom) if zoom is not None else _FALLBACK_ZOOM
    except (TypeError, ValueError):
        zoom_i = _FALLBACK_ZOOM
    return {"pan": pan_i, "tilt": tilt_i, "zoom": zoom_i}


def glass_clock() -> dict[str, Any]:
    """Fields the VoiceOnly dial needs. VL FOV notes stay camera-centered."""
    pose = door_home_pose()
    return {
        "door_hour": door_hour(),
        "door_az_deg": door_az_deg(),
        "twelve": "monitor",
        "twelve_is": "mic USB plug / facing the monitor",
        # Monitor-12 pose frame (user 6→12). Not seat_clock.twelve door_center.
        "pose_hflip": pose_hflip(),
        "door_home_pan": pose["pan"],
        "door_home_tilt": pose["tilt"],
        "door_home_zoom": pose["zoom"],
        "door_taught": door_taught(),
    }
