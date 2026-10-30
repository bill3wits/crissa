"""Look-around = room rescan — one tool, two ranges.

the User 2026-08-02:
  * **look_around** is the product name (UI: Rescan room · init: full look).
  * **rescan** / **reinit_scan mode=room** are aliases.
  * Init vs everyday differ **only by range**:
      - ``full``  — wide arc (learn/confirm blocked seats; first boot / desk moved)
      - ``limited`` — skip blocked/wall arcs once known (default everyday)
  * Same work either way: null seat allocations → pan that range → count/ID
    evidence from frames → re-allocate seats. No occupancy pre-gate.
  * Blocked seats stay zeroed (never allocated). Empty refs only if hc=0.
  * Headcount still SoT law (same-frame / door path) — pans do not sum bodies.
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths

import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from . import error_peek as peek
from . import seat_slots as seats

PRESENCE = (os.environ.get("RAIL_PRESENCE_URL") or os.environ.get("RAIL_PRESENCE_BASE")  # this install's own (2026-10-10)
            or _ports.url("presence")).rstrip("/")
CAMERA = os.environ.get("RAIL_CAMERA_URL", _ports.camera()).rstrip("/")
GEOM_PATH = Path(
    os.environ.get(
        "RAIL_ROOM_GEOMETRY",
        str(_paths.DATA / "room_geometry.json"),
    )
)
# Door pose comes from camera-stops.json via seat_shot.door_pose() — the
# one reader. A literal here goes stale the moment the door is re-measured
# (the old door pan -> the re-measured door pan on 2026-08-16) and is a portability bug besides: the
# pan is per-house init data, not a constant of the software.
from .seat_shot import door_pose as _door_pose

DOOR_PAN = _door_pose()["pan"]

_auto_lock = threading.Lock()
_last_auto_ts = 0.0
_AUTO_MIN_INTERVAL_SEC = float(os.environ.get("RAIL_LOOKAROUND_AUTO_MIN_SEC", "120"))


def _svc():
    try:
        from .server import get_svc

        return get_svc()
    except Exception:
        return None


def load_geometry() -> dict[str, Any]:
    """Blocked/legal stations — default wall 4–7 until init writes more."""
    default = {
        "schema": "room_geometry/v1",
        "blocked_stations": sorted(seats.wall_stations()),
        "legal_stations": seats.legal_stations(),
        "multi_ok": seats.multi_ok_stations(),
        "source": "default_wall",
        "range_default": "limited",
    }
    if not GEOM_PATH.is_file():
        return default
    try:
        raw = json.loads(GEOM_PATH.read_text())
        if not isinstance(raw, dict):
            return default
        out = {**default, **raw}
        out["schema"] = "room_geometry/v1"
        return out
    except Exception:
        return default


def save_geometry(geom: dict[str, Any]) -> dict[str, Any]:
    GEOM_PATH.parent.mkdir(parents=True, exist_ok=True)
    geom = dict(geom)
    geom["schema"] = "room_geometry/v1"
    geom["ts"] = time.time()
    GEOM_PATH.write_text(json.dumps(geom, indent=2) + "\n")
    return geom


def blocked_set(geom: dict[str, Any] | None = None) -> set[int]:
    g = geom or load_geometry()
    blocked = set()
    for x in g.get("blocked_stations") or seats.wall_stations():
        try:
            blocked.add(int(x))
        except (TypeError, ValueError):
            continue
    # wall always blocked
    blocked |= set(seats.wall_stations())
    return blocked


def resolve_range(range_name: str | None, *, init: bool = False) -> str:
    """full | limited — init/full are aliases for wide range."""
    r = (range_name or "").strip().lower()
    if init or r in ("full", "360", "init", "wide", "all", "c9"):
        return "full"
    if r in ("limited", "short", "seats", "custom", "everyday", "rescan", ""):
        if not r:
            # first-boot: no geom file → full once
            if not GEOM_PATH.is_file():
                return "full"
            return str((load_geometry() or {}).get("range_default") or "limited")
        return "limited"
    return "limited"


# Calibrated clock scale (the User 2026-08-04): the room's two hard anchors are
# door 12 = DOOR_PAN (read from camera-stops.json) and desk 8 = 0 (factory
# forward), so one clock
# hour = the old door pan/4 = 112500 pan units — NOT the old 30°×2900=87000, which
# reverse-mapped the desk to hour ~6.8 and planted the User "in the wall".
PAN_PER_HOUR = int(os.environ.get("RAIL_PAN_PER_HOUR", "112500"))
PAN_LIMIT = 522000


def _pan_for_station(hour: int) -> int:
    """Pan from door-centered clock hour, anchored + wrapped to the PTZ range."""
    delta_h = (int(hour) - 12) % 12
    if delta_h > 6:
        delta_h -= 12
    pan = DOOR_PAN + delta_h * PAN_PER_HOUR
    # hours right of the door (1..3) run past +PAN_LIMIT — wrap the circle
    if pan > PAN_LIMIT:
        pan -= 2 * PAN_LIMIT
    elif pan < -PAN_LIMIT:
        pan += 2 * PAN_LIMIT
    return int(pan)


def _stops_for_range(range_kind: str, geom: dict[str, Any]) -> list[tuple[str, int, int]]:
    """Build ordered (name, pan, tilt) stops from calibrated camera-stops.

    the User 2026-08-11: never use _pan_for_station for hours 1–3 (wraps to blank
    wall). Never aim wrap_* . Right-of-door = seat_right (~510k) only.
    full and limited share the same seat tour; full may add station_11.
    """
    _ = range_kind, geom  # range no longer invents wall probes
    patrol = peek._seat_patrol_stops(include_right=False)  # noqa: SLF001
    _ = range_kind  # same short tour; no formula hours
    from .room_heading import door_home_pose

    home = door_home_pose()
    stops = list(patrol)
    stops.append(("door_home", int(home["pan"]), int(home["tilt"])))
    return stops


def _run_range_scan(range_kind: str, *, settle: float = 0.28) -> dict[str, Any]:
    geom = load_geometry()
    # Both ranges use calibrated seat pans (include right-of-door). full adds
    # station_11; neither aims wall wraps.
    if range_kind == "limited":
        out = peek.seat_scan(settle=max(settle, 1.0), include_right=False)
        out["range"] = "limited"
        out["blocked"] = sorted(blocked_set(geom))
        return out

    t0 = time.time()
    stops = _stops_for_range("full", geom)
    frames = [
        peek._aim_snap(  # noqa: SLF001
            n,
            p,
            settle=settle,
            tilt=t,
            zoom=150 if n == "door_home" else 100,
        )
        for n, p, t in stops
    ]
    wall = time.time() - t0
    return {
        "ok": True,
        "mode": "look_around_full",
        "range": "full",
        "n": len(frames),
        "frames": frames,
        "stops": [{"id": n, "pan": p, "tilt": t} for n, p, t in stops],
        "door_gap_sec": round(wall, 3),
        "wall_sec": round(wall, 3),
        "blocked": sorted(blocked_set(geom)),
        "note": (
            "full look-around — calibrated seats + station_11 + seat_right; "
            "no wall wraps"
        ),
    }


def null_seats() -> dict[str, Any]:
    svc = _svc()
    if svc is not None and hasattr(svc, "rescan_null_seats"):
        return svc.rescan_null_seats()
    return {"ok": False, "error": "service_unavailable"}


def look_around(
    *,
    range: str | None = None,  # noqa: A002 — product API field
    init: bool = False,
    null_seats_first: bool = True,
    inspect: bool = True,
    capture_refs_if_empty: bool = True,
    source: str = "manual",
    presence: dict | None = None,
) -> dict[str, Any]:
    """One look-around: null seats → pan range → evidence → optional pin User.

    ``range``: full | limited (only difference vs init).
    No occupancy pre-check — bodies counted/stamped on same pass.
    """
    t0 = time.time()
    range_kind = resolve_range(range, init=init)
    geom = load_geometry()
    steps: list[dict[str, Any]] = []

    # 1) zero seat allocations (blocked stay unusable)
    null_out: dict[str, Any] | None = None
    if null_seats_first:
        null_out = null_seats()
        steps.append(
            {
                "step": "null_seats",
                "ok": null_out.get("ok"),
                "blocked_zeroed": sorted(blocked_set(geom)),
                "before": null_out.get("before"),
            }
        )

    # 2) Product SoT path: room_sweep (calibrated seat pans + faces → one
    #    inventory write). Seat-only pan without reconcile left ghosts (e.g.
    #    another person sticky) on the wall — the User 2026-08-11.
    b = peek.buffer_blast(n=8, stride=2)
    steps.append(
        {
            "step": "buffer_blast",
            **{k: b.get(k) for k in ("ok", "n", "wall_sec", "door_gap_sec")},
        }
    )

    scan_out: dict[str, Any] = {"ok": False, "n": 0}
    sweep_out: dict[str, Any] | None = None
    svc = _svc()
    if svc is not None:
        try:
            from .room_sweep import run_room_sweep

            sweep_out = run_room_sweep(
                svc,
                reason=f"look_around_{range_kind}",
                source=str(source or "rescan"),
            )
            scan_out = {
                "ok": bool(sweep_out.get("ok")),
                "n": len(sweep_out.get("stops") or []) or sweep_out.get("n_frames") or 0,
                "wall_sec": sweep_out.get("wall_sec"),
                "bodies": sweep_out.get("bodies"),
                "names": [
                    b.get("who")
                    for b in (sweep_out.get("bodies") or [])
                    if isinstance(b, dict) and b.get("who")
                ],
                "dropped_ghosts": (sweep_out.get("presence") or {})
                .get("last_event", {})
                .get("dropped_ghosts")
                if isinstance((sweep_out.get("presence") or {}).get("last_event"), dict)
                else None,
                "mode": "room_sweep",
            }
            # surface drop list from sweep detail if present
            for line in sweep_out.get("detail") or []:
                if str(line).startswith("rescan_drop_ghosts:"):
                    scan_out["dropped_ghosts"] = str(line).split(":", 1)[-1].split(",")
            steps.append(
                {
                    "step": "room_sweep",
                    "ok": sweep_out.get("ok"),
                    "range": range_kind,
                    "wall_sec": sweep_out.get("wall_sec"),
                    "bodies": sweep_out.get("bodies"),
                    "detail": (sweep_out.get("detail") or [])[:12],
                }
            )
        except Exception as e:
            steps.append({"step": "room_sweep", "ok": False, "error": str(e)[:160]})
            sweep_out = None

    if sweep_out is None or not sweep_out.get("ok"):
        # Fallback: pan-only (no SoT write) if sweep unavailable
        scan_out = _run_range_scan(range_kind, settle=1.0)
        steps.append(
            {
                "step": "look_around_pan",
                "range": range_kind,
                "ok": scan_out.get("ok"),
                "n": scan_out.get("n"),
                "wall_sec": scan_out.get("wall_sec"),
                "blocked": scan_out.get("blocked"),
                "note": "fallback_no_sot_write",
            }
        )

    # 3) inspect / stamp only if sweep did not already set face certainty
    insp_out = None
    if inspect and (sweep_out is None or not sweep_out.get("ok")):
        try:
            from .inspector import live_snap

            pres = presence
            if pres is None:
                svc = _svc()
                if svc is not None:
                    st = svc.get()
                    pres = st.to_dict() if hasattr(st, "to_dict") else {}
            insp_out = live_snap(
                presence=pres or {},
                force=True,
                trigger=f"look_around_{range_kind}",
                attach_voice=True,
            )
            v = insp_out.get("verdict") if isinstance(insp_out.get("verdict"), dict) else {}
            steps.append(
                {
                    "step": "live_snap",
                    "ok": insp_out.get("ok"),
                    "n_faces": insp_out.get("n_faces"),
                    "stamp_level": v.get("stamp_level"),
                    "person_id": v.get("person_id"),
                    "best_sim": v.get("best_sim"),
                }
            )
            pid = v.get("person_id")
            if pid and v.get("stamp_level") in ("certain", "provisional"):
                svc = _svc()
                if svc is not None and str(pid).lower() == seats.primary_user():
                    pin = svc.pin_user_seat(user_id=str(pid).lower())
                    steps.append(
                        {
                            "step": "pin_user",
                            **{k: pin.get(k) for k in ("ok", "seat", "person_id")},
                        }
                    )
        except Exception as e:
            steps.append({"step": "live_snap", "ok": False, "error": str(e)[:120]})
    elif inspect and sweep_out and sweep_out.get("ok"):
        # Summarize sweep faces as inspect block for UI
        bodies = sweep_out.get("bodies") or []
        named = [
            b.get("who")
            for b in bodies
            if isinstance(b, dict) and b.get("face") and b.get("who")
        ]
        insp_out = {
            "ok": True,
            "n_faces": len(named),
            "verdict": {
                "person_id": named[0] if len(named) == 1 else None,
                "stamp_level": "certain" if named else "none",
                "names": named,
            },
        }
        steps.append(
            {
                "step": "inspect_from_sweep",
                "ok": True,
                "n_faces": len(named),
                "names": named,
            }
        )
        if len(named) == 1 and str(named[0]).lower() == seats.primary_user():
            try:
                pin = svc.pin_user_seat(user_id=str(named[0]).lower()) if svc else {}
                steps.append(
                    {
                        "step": "pin_user",
                        **{k: pin.get(k) for k in ("ok", "seat", "person_id") if pin},
                    }
                )
            except Exception:
                pass

    # 4) if full range and no geom yet, persist default blocked (wall) so next is limited
    if range_kind == "full":
        g = dict(geom)
        g["blocked_stations"] = sorted(blocked_set(geom))
        g["range_default"] = "limited"
        g["source"] = source
        g["last_full_ts"] = time.time()
        save_geometry(g)
        steps.append({"step": "geometry_saved", "path": str(GEOM_PATH), "blocked": g["blocked_stations"]})

    # 5) empty refs only when truly empty (optional)
    refs_out = None
    hc = None
    try:
        svc = _svc()
        if svc is not None:
            hc = int(svc.inventory.headcount())
    except Exception:
        hc = None
    if capture_refs_if_empty and hc == 0:
        try:
            from .reinit_scan import capture_reference_frames

            refs_out = capture_reference_frames(force=True)
            steps.append(
                {
                    "step": "reference_capture",
                    **{k: refs_out.get(k) for k in ("ok", "n_ok", "error", "rc")},
                }
            )
        except Exception as e:
            steps.append({"step": "reference_capture", "ok": False, "error": str(e)[:80]})
    elif capture_refs_if_empty:
        steps.append(
            {
                "step": "reference_capture",
                "skipped": True,
                "reason": "hc_not_zero",
                "headcount": hc,
            }
        )

    slots = None
    svc = _svc()
    if svc is not None and hasattr(svc, "seat_slots_view"):
        slots = svc.seat_slots_view()

    return {
        "ok": True,
        "schema": "look_around/v1",
        # naming aliases for Fable / verbal
        "name": "look_around",
        "aliases": ["rescan", "rescan_room", "room_scan"],
        "range": range_kind,
        "init": bool(init or range_kind == "full"),
        "source": source,
        "wall_sec": round(time.time() - t0, 3),
        "blocked_stations": sorted(blocked_set(geom)),
        "steps": steps,
        "scan": {
            "range": range_kind,
            "n": scan_out.get("n"),
            "wall_sec": scan_out.get("wall_sec"),
            "mode": scan_out.get("mode") or "pan",
            "names": scan_out.get("names"),
            "dropped_ghosts": scan_out.get("dropped_ghosts"),
        },
        "sweep": (
            {
                "ok": sweep_out.get("ok"),
                "wall_sec": sweep_out.get("wall_sec"),
                "bodies": sweep_out.get("bodies"),
            }
            if sweep_out
            else None
        ),
        "inspect": {
            "n_faces": (insp_out or {}).get("n_faces"),
            "person_id": ((insp_out or {}).get("verdict") or {}).get("person_id")
            if isinstance((insp_out or {}).get("verdict"), dict)
            else None,
            "names": ((insp_out or {}).get("verdict") or {}).get("names")
            if isinstance((insp_out or {}).get("verdict"), dict)
            else None,
        }
        if insp_out
        else None,
        "seat_slots": slots,
        "null_seats": null_out,
        "refs": refs_out,
        "ux": {
            "scan_pulse": True,
            "fable_scene": {"kind": "scene", "scan": True},
            "narration": (
                "I'm going to look all the way around the room now…"
                if range_kind == "full"
                else "Looking around the room — resetting seats."
            ),
        },
        "note": (
            "look_around: same tool for init and rescan; only range differs "
            "(full vs limited). Seats nulled then refilled from this pass. "
            "Blocked stations stay zero."
        ),
    }


# ---- aliases ------------------------------------------------------------

def rescan_room(**kwargs: Any) -> dict[str, Any]:
    """UI alias: Rescan room → limited look_around (unless range=full)."""
    kwargs.setdefault("source", kwargs.get("source") or "rescan")
    if kwargs.get("range") is None and not kwargs.get("init"):
        kwargs["range"] = "limited"
    return look_around(**kwargs)


def init_room_scan(**kwargs: Any) -> dict[str, Any]:
    """Init / C9 alias → full range look_around."""
    kwargs["init"] = True
    kwargs["range"] = "full"
    kwargs.setdefault("source", "init")
    return look_around(**kwargs)


def maybe_auto_look_around(
    *,
    reason: str = "empty_transition",
    force_full: bool = False,
) -> dict[str, Any]:
    """Throttle auto look-around (empty transition / scenery). Always limited unless force_full."""
    global _last_auto_ts
    now = time.time()
    with _auto_lock:
        if now - _last_auto_ts < _AUTO_MIN_INTERVAL_SEC:
            return {
                "ok": True,
                "ran": False,
                "reason": "throttled",
                "wait_sec": round(_AUTO_MIN_INTERVAL_SEC - (now - _last_auto_ts), 1),
            }
        _last_auto_ts = now
    out = look_around(
        range="full" if force_full else "limited",
        source=f"auto:{reason}",
        capture_refs_if_empty=True,
    )
    out["ran"] = bool(out.get("ok"))
    out["auto"] = True
    return out
