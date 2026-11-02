"""G3 — snapshot / reload DoorInventory so named slots survive restarts.

Does not invent headcount. Does not restore provisional TV ghosts older than TTL.
"""

from __future__ import annotations

from . import paths as _paths

import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(
    os.environ.get(
        "RAIL_PRESENCE_INVENTORY_PATH",
        str(_paths.DATA / "inventory_snapshot.json"),
    )
)
# drop unnamed-only cold restores older than this (seconds)
MAX_AGE_SEC = float(os.environ.get("RAIL_PRESENCE_INVENTORY_MAX_AGE_SEC", str(12 * 3600)))
# Per-occupant staleness: a named slot whose last_seen is older than this is
# NOT restored across a restart (downtime honesty, the User 2026-08-07).
OCCUPANT_MAX_AGE_SEC = float(
    os.environ.get("RAIL_PRESENCE_OCCUPANT_MAX_AGE_SEC", "900")
)


def snapshot_path() -> Path:
    return DEFAULT_PATH


def inventory_to_dict(inv: Any) -> dict[str, Any]:
    return {
        "schema": "door_inventory_snap/v1",
        "ts": time.time(),
        "present": dict(getattr(inv, "present", {}) or {}),
        "sticky": sorted(getattr(inv, "sticky", set()) or []),
        "gender": dict(getattr(inv, "gender", {}) or {}),
        "station_guess": {
            str(k): int(v)
            for k, v in (getattr(inv, "station_guess", {}) or {}).items()
            if v is not None
        },
        "unnamed": int(getattr(inv, "unnamed", 0) or 0),
        "unnamed_keys": list(getattr(inv, "unnamed_keys", []) or []),
        "entry_grace_until": dict(getattr(inv, "entry_grace_until", {}) or {}),
        "exit_armed": sorted(getattr(inv, "exit_armed", set()) or []),
        "arrival_seq": dict(getattr(inv, "arrival_seq", {}) or {}),
        "_arrival_counter": int(getattr(inv, "_arrival_counter", 0) or 0),
        "baseline_set": bool(getattr(inv, "baseline_set", False)),
        "traits_live": dict(getattr(inv, "traits_live", {}) or {}),
        "peak_headcount": int(getattr(inv, "peak_headcount", 0) or 0),
        "session_peak_headcount": int(getattr(inv, "session_peak_headcount", 0) or 0),
        "visitor_today": dict(getattr(inv, "visitor_today", {}) or {}),
        # enter_still paths omitted (large); stills stay on disk under enter_stills/
    }


def save_inventory(inv: Any, path: Path | None = None) -> dict[str, Any]:
    p = path or snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = inventory_to_dict(inv)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(p)
    return {"ok": True, "path": str(p), "n_named": len(data.get("present") or {}), "ts": data["ts"]}


def load_into_inventory(inv: Any, path: Path | None = None) -> dict[str, Any]:
    p = path or snapshot_path()
    if not p.is_file():
        return {"ok": False, "reason": "no_snapshot", "path": str(p)}
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        return {"ok": False, "reason": f"read_error:{e}"[:120], "path": str(p)}
    age = time.time() - float(data.get("ts") or 0)
    if age > MAX_AGE_SEC:
        return {"ok": False, "reason": "stale_snapshot", "age_sec": round(age, 1), "path": str(p)}
    present = data.get("present") or {}
    if not present and not data.get("unnamed"):
        return {"ok": False, "reason": "empty_snapshot", "path": str(p)}

    now = time.time()
    # Downtime honesty (the User 2026-08-07): the snapshot FILE stays fresh (saved
    # on a cadence) while an occupant's last_seen can be hours old — that is
    # how "another person" rode a 13-hour gap into the morning roster as
    # confirmed-but-doubted. A person nobody has seen for OCCUPANT_MAX_AGE is
    # not restored as a body; the room re-acquires them from evidence (sweep,
    # door, voice). Never invent a body across a gap.
    stale_named = {
        str(k).lower(): float(v)
        for k, v in present.items()
        if (now - float(v)) > OCCUPANT_MAX_AGE_SEC
    }
    if stale_named:
        present = {
            k: v for k, v in present.items() if str(k).lower() not in stale_named
        }
    inv.present = {str(k).lower(): float(v) for k, v in present.items()}
    inv.sticky = set(str(x).lower() for x in (data.get("sticky") or []))
    inv.gender = {
        str(k).lower(): v
        for k, v in (data.get("gender") or {}).items()
        if v in ("M", "F")
    }
    inv.station_guess = {
        str(k).lower(): int(v)
        for k, v in (data.get("station_guess") or {}).items()
    }
    # §1.4 invariant: unnamed == len(unnamed_keys). Ghost recovery iterates
    # keys only, so a restored count above the keys can never be recovered —
    # phantom guests that survive every restart (the User 2026-08-05).
    inv.unnamed_keys = list(data.get("unnamed_keys") or [])
    inv.unnamed = len(inv.unnamed_keys)
    inv.entry_grace_until = {
        str(k).lower(): float(v)
        for k, v in (data.get("entry_grace_until") or {}).items()
        if float(v) > now  # drop expired grace
    }
    inv.exit_armed = set(str(x).lower() for x in (data.get("exit_armed") or []))
    inv.arrival_seq = {
        str(k).lower(): int(v) for k, v in (data.get("arrival_seq") or {}).items()
    }
    inv._arrival_counter = int(data.get("_arrival_counter") or 0)
    inv.baseline_set = True
    inv.traits_live = dict(data.get("traits_live") or {})
    inv.peak_headcount = max(
        int(data.get("peak_headcount") or 0), inv.headcount()
    )
    inv.session_peak_headcount = max(
        int(data.get("session_peak_headcount") or 0), inv.headcount()
    )
    try:
        from .guests import is_guest, visitor_day

        day = visitor_day()
        raw_vis = data.get("visitor_today") or {}
        inv.visitor_today = {
            str(k).lower(): dict(v)
            for k, v in raw_vis.items()
            if isinstance(v, dict)
            and v.get("day") == day
            and is_guest(str(k))
        }
        # Guests must not ride a snapshot onto occupancy.
        for n in list(inv.present):
            if is_guest(n):
                inv.present.pop(n, None)
                inv.sticky.discard(n)
                inv.visitor_today.setdefault(
                    n, {"ts": now, "source": "reload_evict", "day": day}
                )
    except Exception:
        inv.visitor_today = {}
    inv.events.append(
        {
            "kind": "inventory_reload",
            "source": "disk",
            "names": sorted(inv.present),
            "unnamed": inv.unnamed,
            "age_sec": round(age, 1),
            "ts": now,
        }
    )
    return {
        "ok": True,
        "path": str(p),
        "n_named": len(inv.present),
        "unnamed": inv.unnamed,
        "age_sec": round(age, 1),
        "headcount": inv.headcount(),
        # downtime honesty: who was dropped for being unseen too long, and
        # how long the rail was actually down (file age between saves)
        "dropped_stale": {
            k: round(now - v, 1) for k, v in sorted(stale_named.items())
        },
        "needs_reacquire": bool(stale_named),
        "downtime_sec": round(age, 1),
    }
