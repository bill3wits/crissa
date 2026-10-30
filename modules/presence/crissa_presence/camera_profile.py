"""Camera profile: degrees in, units once (the User 2026-10-04, Init v2).

Init asked /v1/look_at for tilt -20 meaning degrees; the route passed it as raw
units and clamp_tilt snapped it to 0. Callers now say degrees (`tilt_deg`,
`pan_deg`) and this module turns them into the camera's units in ONE place,
from config/camera-profile.json (the product default for this camera model;
RAIL_CAMERA_PROFILE points at another). A missing or broken profile reads the
same product default, so the camera never goes unbounded.
"""
from __future__ import annotations

from . import paths as _paths

import json
import math
import os
from pathlib import Path
from typing import Any

_DEFAULT_PATH = _paths.CONFIG / "camera-profile.json"

# Same numbers as the shipped config/camera-profile.json (Insta360 Link); used
# only when that file cannot be read.
PRODUCT_DEFAULT: dict[str, int] = {
    "units_per_deg": 3600,
    "pan_limit": 522000,
    "tilt_min": -324000,
    "tilt_max": 360000,
}


def path() -> Path:
    return Path(os.environ.get("RAIL_CAMERA_PROFILE") or _DEFAULT_PATH)


def load() -> dict[str, Any]:
    """The profile, read fresh (it is a few bytes). Keys it lacks keep the product default."""
    out: dict[str, Any] = dict(PRODUCT_DEFAULT)
    out["source"] = "product_default"
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    if not isinstance(raw, dict):
        return out
    for k in PRODUCT_DEFAULT:
        try:
            if raw.get(k) is not None:
                out[k] = int(raw[k])
        except (TypeError, ValueError):
            pass
    if out["units_per_deg"] <= 0:
        out["units_per_deg"] = PRODUCT_DEFAULT["units_per_deg"]
    out["source"] = str(path())
    return out


def step(prof: dict[str, Any] | None = None) -> int:
    """One control step, in units (= one degree on this camera)."""
    return int((prof or load())["units_per_deg"])


def deg_to_units(deg: float, prof: dict[str, Any] | None = None) -> int:
    d = float(deg)
    if not math.isfinite(d):
        raise ValueError(f"degrees must be a finite number, got {deg!r}")
    return int(round(d * step(prof)))


def units_to_deg(units: float, prof: dict[str, Any] | None = None) -> float:
    return float(units) / float(step(prof))


def _snap(units: int, st: int) -> int:
    return int(round(int(units) / st) * st)


def clamp_tilt(units: Any, prof: dict[str, Any] | None = None) -> int:
    """Tilt units clamped to the profile's range and snapped to its step."""
    p = prof or load()
    st = step(p)
    t = max(int(p["tilt_min"]), min(int(p["tilt_max"]), int(units)))
    return _snap(t, st)


def pan_max(prof: dict[str, Any] | None = None) -> int:
    """Largest pan an aim may command: one step inside pan_limit, because the
    grabber wraps pan modulo 2 x pan_limit (ptz.wrap_pan(522000) == -522000)."""
    p = prof or load()
    return int(p["pan_limit"]) - step(p)


def clamp_pan(units: Any, prof: dict[str, Any] | None = None) -> tuple[int, bool]:
    """(pan units clamped to +/- pan_max and snapped, whether it was clamped).
    Clamped, never wrapped: a wrap swings the camera to the other side."""
    p = prof or load()
    st = step(p)
    hi = pan_max(p)
    want = _snap(int(units), st)
    got = max(-hi, min(hi, want))
    return got, got != want


def tilt_from_body(body: dict[str, Any]) -> Any:
    """A request's tilt in UNITS: `tilt_deg` (degrees, converted here, once)
    wins; else `tilt` exactly as sent (raw units, as before; the caller clamps
    it as it always did); else None. A bad `tilt_deg` raises ValueError."""
    if body.get("tilt_deg") is not None:
        return deg_to_units(body["tilt_deg"])
    return body.get("tilt")
