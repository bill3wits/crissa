"""Quick error-correction peeks for training-curve period (no User stamp required).

Modes (tightest first):
  buffer_blast  — N low-res frames from door ring (no pan, no door gap)
  three_pulse   — 3 door snapshots ~0.4s apart (tiny gap if any)
  seat_scan     — **default short pan**: door + left seat arc only (~2 aims). Wall 4–7 skipped.
  mid_arc       — legacy wider L·door·R (slower; avoid unless needed)

Firmware does not stitch panoramas. Absolute count still CODE same-frame.
Wall hours 4–7 eliminated at this phase — no scan stop into wall.
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

CAMERA = os.environ.get("RAIL_CAMERA_URL", _ports.camera()).rstrip("/")
# Door pose comes from camera-stops.json via seat_shot.door_pose() — the
# one reader. A literal here goes stale the moment the door is re-measured
# (450000 -> 432000 on 2026-08-16) and is a portability bug besides: the
# pan is per-house init data, not a constant of the software.
from .seat_shot import door_pose as _door_pose

DOOR_PAN = _door_pose()["pan"]
# ~45° left/right of door for mid coverage (~130k units @ 2900 u/°)
MID_DELTA = int(os.environ.get("RAIL_PEEK_MID_DELTA", str(45 * 2900)))
# Soft right of door = 1–2 o'clock seats (measured 2026-08-11: pan~515k + −15° tilt)
SEAT_RIGHT_PAN = int(os.environ.get("RAIL_PEEK_SEAT_RIGHT_PAN", "515000"))
SEAT_RIGHT_TILT = int(os.environ.get("RAIL_PEEK_SEAT_RIGHT_TILT", "-54000"))  # −15°
# Legacy deltas only used if camera-stops.json missing seat pans
SEAT_LEFT_DELTA = int(os.environ.get("RAIL_PEEK_SEAT_LEFT_DELTA", str(28 * 2900)))
SEAT_RIGHT_DELTA = int(os.environ.get("RAIL_PEEK_SEAT_RIGHT_DELTA", str(60 * 2900)))
CAMERA_STOPS_PATH = os.environ.get(
    "RAIL_CAMERA_STOPS",
    str(_paths.DATA / "camera-stops.json"),
)


def _get_json(url: str, timeout: float = 8.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get_bytes(url: str, timeout: float = 8.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def _post_json(url: str, body: dict, timeout: float = 30.0) -> dict[str, Any]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def buffer_blast(
    *,
    n: int = 10,
    stride: int = 3,
    scale: float = 0.35,
) -> dict[str, Any]:
    """Pull recent door ring frames — fastest, **zero pan / zero FOV leave**."""
    t0 = time.time()
    try:
        # Prefer compact buffer meta + frames
        meta = _get_json(f"{CAMERA}/v1/buffer/meta", timeout=3.0)
    except Exception:
        meta = {}
    frames: list[dict[str, Any]] = []
    try:
        # Camera ring: max=N&full=1 returns jpegs (scale not supported — keep small N)
        want = max(1, min(int(n) * max(1, int(stride)), 40))
        buf = _get_json(
            f"{CAMERA}/v1/buffer?max={want}&full=1&stride={max(1, int(stride))}",
            timeout=15.0,
        )
        raw = buf.get("frames") or []
        # newest last — take last n after stride already applied by camera if any
        picked = raw[-int(n) :] if len(raw) > n else raw
        for fr in picked:
            frames.append(
                {
                    "seq": fr.get("seq") or fr.get("frame_seq"),
                    "ts": fr.get("ts") or fr.get("capture_ts"),
                    # omit full b64 from peek response by default (huge) — keep bytes+seq
                    "bytes": fr.get("bytes"),
                    "sha": fr.get("sha"),
                    "has_jpeg": bool(fr.get("jpeg_b64")),
                }
            )
        # optional: caller can re-fetch by seq via camera /v1/frame
    except Exception as e:
        # fallback: N snapshots (still door FOV if aimed home)
        for i in range(n):
            try:
                jpeg = _get_bytes(f"{CAMERA}/snapshot", timeout=5.0)
                frames.append({"i": i, "bytes": len(jpeg), "ts": time.time()})
            except Exception as e2:
                frames.append({"i": i, "error": str(e2)[:80]})
            time.sleep(0.05)
        return {
            "ok": True,
            "mode": "buffer_blast_fallback_snap",
            "n": len(frames),
            "frames": frames,
            "door_gap_sec": 0.0,
            "wall_sec": round(time.time() - t0, 3),
            "note": f"buffer failed: {e}"[:120],
        }
    return {
        "ok": True,
        "mode": "buffer_blast",
        "n": len(frames),
        "frames": frames,
        "buffer_meta": {k: meta.get(k) for k in ("seq", "len", "span_sec", "hz") if k in meta or True},
        "door_gap_sec": 0.0,
        "wall_sec": round(time.time() - t0, 3),
        "note": "door FOV only — no pan; best for training harvest + continuous watch",
    }


def three_pulse(*, gap_sec: float = 0.4) -> dict[str, Any]:
    """Three door snapshots — ~1s wall, no pan."""
    t0 = time.time()
    frames = []
    for i in range(3):
        try:
            jpeg = _get_bytes(f"{CAMERA}/snapshot", timeout=5.0)
            frames.append({"i": i, "bytes": len(jpeg), "ts": time.time()})
        except Exception as e:
            frames.append({"i": i, "error": str(e)[:80]})
        if i < 2:
            time.sleep(gap_sec)
    return {
        "ok": True,
        "mode": "three_pulse",
        "n": len(frames),
        "frames": frames,
        "door_gap_sec": 0.0,
        "wall_sec": round(time.time() - t0, 3),
    }


def _load_camera_stops() -> dict[str, Any]:
    try:
        raw = json.loads(Path(CAMERA_STOPS_PATH).read_text())
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _seat_patrol_stops(*, include_right: bool = True) -> list[tuple[str, int, int]]:
    """Patrol hours come from init geometry; pans come from camera-stops.

    Visit every legal person hour that has a measured stop. Skip wall
    and the door (door is the hold at the end). Same pan + different
    tilt is two stops (stand 11 level, seat 12 down 15°).
    """
    _ = include_right
    from .seat_slots import camera_door_station, eligible_stations, wall_stations

    cfg = _load_camera_stops()
    by_hour: dict[int, dict] = {}
    for s in cfg.get("stops") or []:
        if not isinstance(s, dict):
            continue
        if str(s.get("id") or "") in ("door", "door_home") or str(s.get("role") or "") == "hold":
            continue
        try:
            h = int(s.get("station"))
        except (TypeError, ValueError):
            continue
        by_hour[h] = s

    wall = set(wall_stations())
    door = camera_door_station()
    hours = [h for h in eligible_stations() if h != door and h not in wall]
    user = 6
    try:
        from .seat_slots import load_room_geometry, user_seat

        user = int(user_seat() or (load_room_geometry() or {}).get("user_seat") or 6)
    except (TypeError, ValueError):
        user = 6
    hours.sort(key=lambda h: (int(h) - user) % 12)

    out: list[tuple[str, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for h in hours:
        s = by_hour.get(h)
        if not s:
            continue
        try:
            pan_i = max(-522000, min(522000, int(s.get("pan"))))
        except (TypeError, ValueError):
            continue
        tilt_i = int(s.get("tilt") or 0)
        if (pan_i, tilt_i) in seen or str(s.get("id") or "").startswith("wrap"):
            continue
        if -520000 <= pan_i <= -200000:
            continue
        seen.add((pan_i, tilt_i))
        name = str(s.get("id") or f"station_{h}")
        out.append((name, pan_i, tilt_i))
    return out


# ~3.3°. Commanded stop vs firmware readback. Bigger than 1° noise, smaller
# than a whole clock hour.
_PAN_ARRIVE = int(os.environ.get("RAIL_PEEK_PAN_ARRIVE", "12000"))


def _ff_off(*, lease_id: str | None = None) -> dict[str, Any]:
    """Double OFF. One release leaves frame=1 and head-framing steals pan."""
    body: dict[str, Any] = {"on": False}
    if lease_id:
        body["lease_id"] = lease_id
    last: dict[str, Any] = {}
    for _ in range(2):
        try:
            last = _post_json(f"{CAMERA}/v1/facefinder", body, timeout=5.0)
        except Exception as e:
            last = {"ok": False, "error": type(e).__name__}
        time.sleep(0.15)
    return last


def _aim_snap(
    name: str,
    pan: int,
    *,
    settle: float,
    tilt: int = 0,
    zoom: int = 100,
    face_zoom: bool = True,
    lease_id: str | None = None,
) -> dict[str, Any]:
    """FF off → aim → read pan back → wide snap → FF reframe → face snap.

    FaceFinder does not take a picture — it only reframes. Must run under
    a camera lease or REST OFF DOOR yanks home at 4s.
    """
    pan = max(-522000, min(522000, int(pan)))
    is_door = name in ("door_home", "door")
    ff_sec = float(os.environ.get("RAIL_PRESENCE_FACEFINDER_ENGAGE_SEC", "2.5"))

    def _aim() -> dict[str, Any]:
        body: dict[str, Any] = {
            "pan": pan,
            "tilt": int(tilt),
            "zoom": int(zoom),
        }
        if lease_id:
            body["lease_id"] = lease_id
        return _post_json(f"{CAMERA}/v1/aim", body, timeout=25.0)

    try:
        _ff_off(lease_id=lease_id)
        aim = _aim()
        actual = aim.get("pan_actual")
        try:
            miss = actual is None or abs(int(actual) - pan) > _PAN_ARRIVE
        except (TypeError, ValueError):
            miss = True
        if miss:
            _ff_off(lease_id=lease_id)
            aim = _aim()
            actual = aim.get("pan_actual")
        time.sleep(settle)
        jpeg_wide = _get_bytes(f"{CAMERA}/snapshot", timeout=5.0)
        jpeg_face = b""
        if not is_door and face_zoom:
            on_body: dict[str, Any] = {"on": True}
            if lease_id:
                on_body["lease_id"] = lease_id
            try:
                _post_json(f"{CAMERA}/v1/facefinder", on_body, timeout=5.0)
                time.sleep(max(0.4, ff_sec))
                jpeg_face = _get_bytes(f"{CAMERA}/snapshot", timeout=5.0)
            except Exception:
                jpeg_face = b""
            _ff_off(lease_id=lease_id)
        arrived = True
        try:
            if actual is not None:
                arrived = abs(int(actual) - pan) <= _PAN_ARRIVE
        except (TypeError, ValueError):
            arrived = False
        return {
            "stop": name,
            "pan_cmd": pan,
            "tilt_cmd": int(tilt),
            "pan_actual": actual,
            "arrived": bool(arrived),
            "jpeg_wide": jpeg_wide,
            "jpeg_face": jpeg_face,
            "bytes": len(jpeg_face or jpeg_wide or b""),
            "aim_ok": bool(aim.get("ok")),
            "facefinder": bool(jpeg_face),
            "lease_id": lease_id,
            "ts": time.time(),
        }
    except Exception as e:
        _ff_off(lease_id=lease_id)
        return {
            "stop": name,
            "pan_cmd": pan,
            "tilt_cmd": int(tilt),
            "error": str(e)[:100],
        }


def seat_scan(*, settle: float = 0.28, include_right: bool = False) -> dict[str, Any]:
    """Seat patrol: measured pans + FaceFinder on non-door stops. No wall."""
    t0 = time.time()
    patrol = _seat_patrol_stops(include_right=include_right)
    stops: list[tuple[str, int, int]] = list(patrol)
    from .room_heading import door_home_pose

    _home = door_home_pose()
    stops.append(("door_home", int(_home["pan"]), int(_home["tilt"])))
    frames = [
        _aim_snap(n, p, settle=settle, tilt=t, zoom=100 if n != "door_home" else 150)
        for n, p, t in stops
    ]
    wall = time.time() - t0
    return {
        "ok": True,
        "mode": "seat_scan",
        "n": len(frames),
        "frames": frames,
        "door_gap_sec": round(wall, 3),
        "wall_sec": round(wall, 3),
        "wall_skipped": True,
        "stops": [{"id": n, "pan": p, "tilt": t} for n, p, t in stops],
        "legal_focus": [6, 8, 9, 10, 11, 12],
        "note": "B6 → M8 → stand11 → seat12(-15°) → door10. stand9 skipped until a pan is measured.",
    }


def mid_arc_peek(*, settle: float = 0.35) -> dict[str, Any]:
    """Wider L·door·R — slower. Prefer seat_scan. Kept for rare deep check."""
    t0 = time.time()
    # Reuse calibrated seat patrol (no wall wraps)
    frames = []
    for n, p, t in _seat_patrol_stops(include_right=True):
        frames.append(_aim_snap(n, p, settle=settle, tilt=t))
    _dp = _door_pose()
    frames.append(_aim_snap("door_home", _dp["pan"], settle=settle,
                            tilt=_dp["tilt"], zoom=_dp["zoom"]))
    wall = time.time() - t0
    return {
        "ok": True,
        "mode": "mid_arc",
        "n": len(frames),
        "frames": frames,
        "door_gap_sec": round(wall, 3),
        "wall_sec": round(wall, 3),
        "note": "calibrated seat arc — still no wall 4–7; prefer seat_scan",
    }


def run_peek(mode: str = "buffer_blast", **kwargs: Any) -> dict[str, Any]:
    m = (mode or "buffer_blast").strip().lower()
    if m in ("buffer", "buffer_blast", "blast", "fast"):
        return buffer_blast(**{k: kwargs[k] for k in ("n", "stride", "scale") if k in kwargs})
    if m in ("three", "three_pulse", "pulse", "15m"):
        return three_pulse(**{k: kwargs[k] for k in ("gap_sec",) if k in kwargs})
    if m in ("seat", "seat_scan", "seats", "correct", "scan"):
        return seat_scan(
            **{
                k: kwargs[k]
                for k in ("settle", "include_right")
                if k in kwargs
            }
        )
    if m in ("mid", "mid_arc", "arc", "wide"):
        return mid_arc_peek(**{k: kwargs[k] for k in ("settle",) if k in kwargs})
    return {
        "ok": False,
        "error": f"unknown mode {mode!r}",
        "modes": ["buffer_blast", "three_pulse", "seat_scan", "mid_arc"],
    }
