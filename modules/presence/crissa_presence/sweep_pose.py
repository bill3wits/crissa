"""Shared pose for live room VIDEO and empty-ref VIDEO.

The subtract IS the body only when pan/tilt/zoom match. One block in
camera-stops.json (`video_sweep`) is the SoT. Door rest stays tilt 0.

Main arc is −5° (room). Sitters that a −5° arc would miss get short holds
at their measured pitch: seat 12 = −15°, chair-under-cam = −20°. Count and
refs run this exact path — not a separate station patrol.
"""
from __future__ import annotations

from . import paths as _paths

import json
import os
from pathlib import Path
from typing import Any

_STOPS = Path(
    os.environ.get(
        "RAIL_CAMERA_STOPS",
        str(_paths.DATA / "camera-stops.json"),
    )
)

# Firmware units = deg × 3600. Default −5° (meet-in-the-middle).
_DEFAULT_TILT_DEG = -5


def tilt_units(tilt_deg: int | float) -> int:
    return int(round(float(tilt_deg) * 3600.0))


def video_sweep_pose(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Live sweep and empty-ref capture must pass the same dict into the gimbal."""
    raw = cfg
    if raw is None:
        try:
            loaded = json.loads(_STOPS.read_text()) if _STOPS.is_file() else {}
            raw = loaded if isinstance(loaded, dict) else {}
        except Exception:
            raw = {}
    vs = raw.get("video_sweep") if isinstance(raw.get("video_sweep"), dict) else {}
    try:
        tilt_deg = int(vs.get("tilt_deg", _DEFAULT_TILT_DEG))
    except (TypeError, ValueError):
        tilt_deg = _DEFAULT_TILT_DEG
    try:
        tilt = int(vs["tilt"]) if vs.get("tilt") is not None else tilt_units(tilt_deg)
    except (TypeError, ValueError):
        tilt = tilt_units(tilt_deg)
    try:
        start_pan = int(vs.get("start_pan", 0))
    except (TypeError, ValueError):
        start_pan = 0
    try:
        end_pan = int(vs.get("end_pan", 515000))
    except (TypeError, ValueError):
        end_pan = 515000
    try:
        zoom = int(vs.get("zoom", 150))
    except (TypeError, ValueError):
        zoom = 150
    sitters: list[dict[str, Any]] = []
    for raw_s in vs.get("sitters") or []:
        if not isinstance(raw_s, dict):
            continue
        try:
            s_deg = int(raw_s.get("tilt_deg", -15))
        except (TypeError, ValueError):
            s_deg = -15
        try:
            s_tilt = (
                int(raw_s["tilt"])
                if raw_s.get("tilt") is not None
                else tilt_units(s_deg)
            )
        except (TypeError, ValueError):
            s_tilt = tilt_units(s_deg)
        try:
            s_pan = int(raw_s["pan"])
        except (TypeError, ValueError):
            continue
        try:
            hold = float(raw_s.get("hold_sec") or 1.0)
        except (TypeError, ValueError):
            hold = 1.0
        sitters.append(
            {
                "id": str(raw_s.get("id") or f"sit_{s_pan}"),
                "station": raw_s.get("station"),
                "pan": s_pan,
                "tilt_deg": s_deg,
                "tilt": s_tilt,
                "hold_sec": max(0.4, hold),
            }
        )
    return {
        "tilt_deg": tilt_deg,
        "tilt": tilt,
        "start_pan": start_pan,
        "end_pan": end_pan,
        "zoom": zoom,
        "sitters": sitters,
    }


# Station stills (Who-is-here + Scan-empty) share camera-stops.json
# `stops[]`: rest tilt 0, seat 12 = −15°. That is not the VIDEO sweep
# pitch (−5° + sitter holds). 2026-08-18 we remapped Scan to −5° / extra
# −10° at 12 so empty-ref subtract would match; that aimed 11 at −15°
# and 12 at −10°. the User: 12 down 15°, the rest normal. VIDEO refs stay
# on video_sweep; a tilt-mismatched subtract still must not write SoT.
ERROR_CHECK_DOWN12_DEG = -15
ERROR_CHECK_DOWN12_TILT = -54000


def error_check_down_12(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Seat 12 from camera-stops: same pan as stand 11, tilt −15°."""
    _ = cfg
    from .error_peek import _load_camera_stops

    for s in (_load_camera_stops().get("stops") or []):
        if not isinstance(s, dict) or str(s.get("id") or "") != "seat_12":
            continue
        try:
            pan = int(s["pan"])
            tilt = int(s.get("tilt") or ERROR_CHECK_DOWN12_TILT)
        except (TypeError, ValueError, KeyError):
            continue
        return {
            "id": "seat_12",
            "pan": pan,
            "tilt": tilt,
            "tilt_deg": int(round(tilt / 3600.0)),
            "snaps": 1,
        }
    pose = video_sweep_pose(cfg)
    return {
        "id": "seat_12",
        "pan": int(pose["end_pan"]),
        "tilt": ERROR_CHECK_DOWN12_TILT,
        "tilt_deg": ERROR_CHECK_DOWN12_DEG,
        "snaps": 1,
    }


def error_check_stops(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Same (id, pan, tilt) list as Who-is-here (`_seat_patrol_stops`).

    Four stills: desk / another person / stand_11 level, seat_12 down 15°.
    Same pan at 11 and 12 is two stops. Door is rest. No extra −10°.
    """
    _ = cfg
    from .error_peek import _load_camera_stops, _seat_patrol_stops

    by_id: dict[str, dict[str, Any]] = {}
    for s in (_load_camera_stops().get("stops") or []):
        if isinstance(s, dict) and s.get("id"):
            by_id[str(s["id"])] = s
    out: list[dict[str, Any]] = []
    for name, pan, tilt in _seat_patrol_stops():
        row = by_id.get(str(name)) or {}
        try:
            hour = int(row["station"]) if row.get("station") is not None else None
        except (TypeError, ValueError):
            hour = None
        tilt_i = int(tilt)
        kind = "down_12" if str(name) == "seat_12" else "level"
        out.append(
            {
                "id": str(name),
                "pan": int(pan),
                "tilt": tilt_i,
                "tilt_deg": int(round(tilt_i / 3600.0)),
                "hour": hour,
                "kind": kind,
                "snaps": 1,
            }
        )
    return out
