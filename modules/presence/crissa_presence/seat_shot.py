"""Take one face shot at a seat, and ALWAYS give the camera back.

the User 2026-08-16, after a hand-rolled capture left the camera staring at him:

    "be sure that tool deZOOMs like I already mentioned"
    "it has been pointing at me for over a minute. Please fix that tool to
     return lease to firmware"

Both faults were the same fault: the capture path released the *lease* and
thought it was done. Releasing a lease only says "I am finished holding the
camera" — it does not move it. The PTZ stays wherever it was left, zoomed in on
whoever it was last looking at, and the firmware's own behaviour never resumes
because the physical pose is wrong. To the room it looks like the house is
watching you.

So the contract here is: coming home is not the caller's job and not a
best-effort step at the end of the happy path. It happens in `finally`, in this
order, and it happens even when the capture throws:

    1. facefinder OFF   — it must not keep hunting once we stop looking
    2. de-zoom          — back to the door's zoom, never left tight on a face
    3. aim door home    — the pose from init (data/camera-stops.json)
    4. release lease    — last, so nothing else can grab a wrongly-aimed camera

Seat captures also use the SWEEP zoom while shooting, not the door's. The door
is zoomed for a face crossing a known plane at a known distance; a seat is not,
and shooting a seat at door zoom crops the head.

Why this exists at all: the facefinder at station 6 is the tool that makes a
desk shot possible. Off it, the same man at the same desk is a 14x20px face the
bank cannot use. Aimed and engaged, he is 272x415px at det 0.86 and matches at
0.8234 — the strongest of the day.
"""

from __future__ import annotations

from . import paths as _paths
from .room_heading import door_home_pose

import json
import os
import pathlib
import time
from typing import Any

STOPS = pathlib.Path(os.environ.get(
    "RAIL_CAMERA_STOPS",
    str(_paths.DATA / "camera-stops.json"),
))
ENGAGE_SEC = float(os.environ.get("RAIL_SEAT_SHOT_ENGAGE", "3.0"))
SETTLE_SEC = float(os.environ.get("RAIL_SEAT_SHOT_SETTLE", "1.4"))
SHOTS = int(os.environ.get("RAIL_SEAT_SHOT_N", "5"))


def _cfg() -> dict[str, Any]:
    try:
        return json.loads(STOPS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _door_pose_stops() -> dict[str, int]:
    """The old reader (camera-stops.json door{}), kept byte for byte behind
    RAIL_SEAT_SHOT_OWN_DOOR=1. Its `or` fallbacks turned a configured tilt of 0
    into +5 deg (18000)."""
    d = (_cfg().get("door") or {})
    return {
        "pan": int(d.get("pan") or door_home_pose()["pan"]),
        "tilt": int(d.get("tilt") or door_home_pose()["tilt"]),
        "zoom": int(d.get("zoom") or door_home_pose()["zoom"]),
    }


def door_pose() -> dict[str, int]:
    """ONE door pose (the User 2026-10-04, Init v2): the door watch's own rest,
    room_heading.door_home_pose() (room_geometry.json heading.door_home_*), so
    go_home and the door watch home to the same place and a configured tilt of
    0 stays 0. RAIL_SEAT_SHOT_OWN_DOOR=1 puts the old camera-stops reader back."""
    if os.environ.get("RAIL_SEAT_SHOT_OWN_DOOR", "").strip() == "1":
        return _door_pose_stops()
    from .room_heading import door_home_pose

    return dict(door_home_pose())


def hour_from_az(az_deg: float | None, *, residual: float | None = None) -> int | None:
    """Mic az → clock hour for a camera-stop look. Not occupancy.

    Residual is clap-at-12 when set. Unset → 0 (mic-frame as camera-frame)
    so a live lobe can still pick a measured station. Does not publish
    sound.beam_hour (that stay gated on the desk check).
    """
    if az_deg is None:
        return None
    if residual is None:
        raw = (os.environ.get("HOUSE_MIC_AZ_RESIDUAL_DEG") or "").strip()
        try:
            residual = float(raw) if raw else 0.0
        except (TypeError, ValueError):
            residual = 0.0
    try:
        cam = (float(az_deg) - float(residual)) % 360.0
    except (TypeError, ValueError):
        return None
    hour = int(round(cam / 30.0)) % 12
    return 12 if hour == 0 else hour


def pose_for_az(az_deg: float | None) -> dict[str, Any] | None:
    """Beam heading → measured stop. None if that hour has no pan / is media."""
    return pose_for_hour(hour_from_az(az_deg))


def pose_for_hour(hour: int | None) -> dict[str, Any] | None:
    """Measured camera stop for a mic-clock hour. None if we must not aim.

    Media and wall hours have no person station. Hour 12 (ROOM-CLOCK door
    plane) and init ``camera_door`` stay at live door rest — not the stale
    stops[].door pan, not seat_12's down-tilt. Other hours use camera-stops
    ``station`` when a pan was measured. Invented pans are not a look.
    """
    if hour is None:
        return None
    try:
        h = int(hour)
    except (TypeError, ValueError):
        return None
    if h < 1 or h > 12:
        return None
    from .seat_slots import camera_door_station, media_station, wall_stations

    ms = media_station()
    if ms is not None and h == int(ms):
        return None
    if h in set(wall_stations()):
        return None
    try:
        door_h = int(camera_door_station())
    except (TypeError, ValueError):
        door_h = 10
    # Mic clock 12 = door plane. Init door hour (10) is rest. Do not use
    # the stale stops[] door pan (the old door pan vs live the re-measured door pan).
    if h in (12, door_h):
        d = door_pose()
        return {
            "id": "door",
            "pan": d["pan"],
            "tilt": d["tilt"],
            "station": h,
            "home": True,
        }
    for s in _cfg().get("stops") or []:
        if not isinstance(s, dict):
            continue
        if str(s.get("id") or "") == "door":
            continue
        try:
            st = int(s["station"]) if s.get("station") is not None else None
        except (TypeError, ValueError):
            st = None
        if st != h:
            continue
        if s.get("pan") is None:
            return None
        try:
            return {
                "id": str(s.get("id") or f"h{h}"),
                "pan": int(s["pan"]),
                "tilt": int(s.get("tilt") or 0),
                "station": h,
                "home": False,
            }
        except (TypeError, ValueError):
            return None
    # No measured stop for this hour. The Oracle clock Setup writes puts the User at 6 and the camera's rest faces the
    # seat, so his hour is the seat the Secure click and the reauth ladder already aim at (user_seat_pose: a measured
    # desk stop, else the camera's forward). A pack has no camera-stops.json, so without this every session look said
    # no_station and the camera never stayed on him (the User 2026-10-30: "it knows User is always at 6 ... that is
    # embedded in our Oracle clock during Init"). Other hours stay measured-only. Kill RAIL_SEAT_FROM_CLOCK=0.
    if os.environ.get("RAIL_SEAT_FROM_CLOCK", "1") != "0":
        try:
            from .seat_slots import user_seat
            from .user_exit_confirm import user_seat_pose

            if h == int(user_seat()):
                p = user_seat_pose(_cfg())
                return {"id": "desk", "pan": int(p["pan"]), "tilt": int(p["tilt"]), "station": h, "home": False}
        except Exception:  # noqa: BLE001
            return None
    return None


def sweep_zoom() -> int:
    """Zoom for looking at a seat. Never the door's."""
    try:
        return int(_cfg().get("sweep_zoom") or 100)
    except (TypeError, ValueError):
        return 100


def go_home(rs, lease_id: str | None = None) -> dict[str, Any]:
    """facefinder off -> de-zoom -> door home. Safe to call any time."""
    out: dict[str, Any] = {}
    d = door_pose()
    try:
        body = {"on": False}
        if lease_id:
            body["lease_id"] = lease_id
        out["ff_off"] = bool(rs._post("/v1/facefinder", body, timeout=5.0).get("ok"))
    except Exception as e:
        out["ff_off_err"] = type(e).__name__
    try:
        aim = {"pan": d["pan"], "tilt": d["tilt"], "zoom": d["zoom"]}
        if lease_id:
            aim["lease_id"] = lease_id
        r = rs._post("/v1/aim", aim, timeout=25.0)
        out["home"] = bool(r.get("ok"))
        out["pan_actual"] = r.get("pan_actual")
    except Exception as e:
        out["home_err"] = type(e).__name__
    try:
        ret = {"force": True, "force_door": True}
        if lease_id:
            ret["lease_id"] = lease_id   # its own lease: the camera homes it (the User 2026-10-09)
        rs._post("/v1/return_door", ret, timeout=12.0)
    except Exception:
        pass
    out["zoom"] = d["zoom"]
    return out


def _picture(rs) -> bytes | None:
    """One picture for the fixed-camera check (RAIL_FIXED_CAM_MEASURE=0: none)."""
    if str(os.environ.get("RAIL_FIXED_CAM_MEASURE", "1")).strip().lower() in ("0", "off", "false", "no"):
        return None
    try:
        return rs._snapshot()
    except Exception:  # noqa: BLE001
        return None


def _measure_pan(before: bytes | None, after: bytes | None, pan_to) -> None:
    """A FIXED CAMERA IS MEASURED, NOT BELIEVED (2026-10-11, .101's Elgato reads back any pan it is sent): the aim from
    the door pose moved the camera, or the picture stayed where it was (crissa_presence.fixed_cam.note_pan_check)."""
    if not before or not after:
        return
    try:
        from . import fixed_cam
        pan_from = door_pose().get("pan")
        got = fixed_cam.note_pan_check(before, after, pan_from, pan_to)
        if got.get("fixed") is not None:
            print("[fixed-cam] pan %s -> %s: %s (%s)" % (pan_from, pan_to, "fixed" if got.get("fixed") else "moves",
                                                         got.get("last")), flush=True)
    except Exception:  # noqa: BLE001  a check that fails changes nothing
        pass


def shot_at(pan: int, tilt: int = 0, *, owner: str = "seat_shot",
            engage: float = ENGAGE_SEC, n: int = SHOTS) -> dict[str, Any]:
    """Aim at a seat, engage the facefinder, grab n frames, come home.

    Returns {ok, frames: [bytes], home: {...}}. The camera is parked at the
    door pose, de-zoomed, facefinder off, lease released — whatever happened.
    """
    from . import room_sweep as rs

    from . import camera_token as ct

    out: dict[str, Any] = {"ok": False, "frames": []}
    lid = ct.borrowed()          # under the register token: its lease, never our own take/release
    if lid is None:
        lease = rs._post("/v1/lease", {"owner": owner, "ttl_sec": 60}, timeout=8.0)
        if not lease.get("ok"):
            out["error"] = "camera_leased"
            out["lease"] = lease
            return out
        lid = lease.get("lease_id")
    else:
        out["borrowed"] = True
    try:
        before = _picture(rs)                 # the view before the aim: a fixed camera's is the same after it
        aim = rs._post(
            "/v1/aim",
            {"pan": int(pan), "tilt": int(tilt), "zoom": sweep_zoom(), "lease_id": lid},
            timeout=25.0,
        )
        out["aim_ok"] = bool(aim.get("ok"))
        if not aim.get("ok"):
            out["error"] = "aim_failed"
            return out
        time.sleep(SETTLE_SEC)
        _measure_pan(before, _picture(rs) if before else None, pan)
        try:
            rs._post("/v1/facefinder", {"on": True, "lease_id": lid}, timeout=5.0)
            time.sleep(max(0.8, engage))
        except Exception:
            time.sleep(0.8)
        for _ in range(max(1, n)):
            try:
                out["frames"].append(rs._snapshot())
            except Exception:
                break
            time.sleep(0.5)
        out["ok"] = bool(out["frames"])
    finally:
        # Coming home is not optional and not the caller's job.
        out["home"] = go_home(rs, lid)
        if out.get("borrowed"):
            out["lease_released"] = False          # the token's holder gives it back
        else:
            try:
                rs._post("/v1/lease/release", {"lease_id": lid}, timeout=8.0)
                out["lease_released"] = True
            except Exception:
                out["lease_released"] = False
    return out


# ---------------------------------------------------------------- stations --
# The ONE camera tool. the User 2026-08-16: "tool consolidate into one. I would
# like for crissa to be able to look at anything with it for interaction via
# video. We can create the stations but also controls for tilt. Just give her
# the lease on tilt and return at close of tool to firmware."
#
# Named stops come from init (camera-stops + room_geometry roles), never from a
# formula on the hour — an invented pan aims at a wall. Anything without a
# MEASURED pan is simply absent from this map and cannot be looked at until it
# is calibrated. Station 7 (media) is deliberately absent: the User 2026-08-16,
# "skip media ... maybe the beam direction will be enough".

TILT_MIN, TILT_MAX, TILT_STEP = -324000, 360000, 3600


def stations() -> dict[str, dict[str, int]]:
    """name -> {pan, tilt}, from measured stops only."""
    out: dict[str, dict[str, int]] = {}
    try:
        from .error_peek import _seat_patrol_stops

        for name, pan, tilt in _seat_patrol_stops():
            out[str(name).strip().lower()] = {"pan": int(pan), "tilt": int(tilt)}
    except Exception:
        pass
    d = door_pose()
    out["door"] = {"pan": d["pan"], "tilt": d["tilt"]}
    # friendly aliases for the roles init already names
    for alias, real in (("user_desk", "desk"), ("user", "desk"), ("6", "desk"),
                        ("8", "another person"), ("10", "door"), ("11", "stand_11"),
                        ("12", "seat_12")):
        if real in out and alias not in out:
            out[alias] = dict(out[real])
    return out


def clamp_tilt(tilt: Any) -> int:
    try:
        t = int(tilt)
    except (TypeError, ValueError):
        return 0
    t = max(TILT_MIN, min(TILT_MAX, t))
    return int(round(t / TILT_STEP) * TILT_STEP)


def look_at(target: str | int, *, tilt: int | None = None,
            facefinder: bool = True, n: int = 3,
            owner: str = "look_at") -> dict[str, Any]:
    """Point the camera at a named station (or raw pan) and bring back frames.

    tilt overrides the station's own tilt, clamped to the hardware range and
    snapped to its step — this is the tilt control Crissa gets.

    The camera always returns to the door, de-zoomed, facefinder off, lease
    released, even if this raises. That is `shot_at`'s finally, not a promise.
    """
    known = stations()
    key = str(target).strip().lower()
    if key in known:
        pan = known[key]["pan"]
        base_tilt = known[key]["tilt"]
    else:
        try:
            pan = int(target)
            base_tilt = 0
        except (TypeError, ValueError):
            return {"ok": False, "error": "unknown_station",
                    "known": sorted(known)}
    use_tilt = clamp_tilt(base_tilt if tilt is None else tilt)
    res = shot_at(pan, use_tilt, owner=owner,
                  engage=(ENGAGE_SEC if facefinder else 0.0), n=n)
    res["station"] = key if key in known else None
    res["pan"] = pan
    res["tilt"] = use_tilt
    return res
