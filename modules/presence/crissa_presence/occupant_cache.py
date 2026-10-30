"""The occupant cache — the User's design, 2026-08-09.

Oracle has exactly two jobs: decide ADD or SUB, and hand over one best frame.
Everything after that belongs to Inspector:

    ADD  →  embed the face, match the bank, store THIS frame as that
            occupant's entry photo. Occupancy +1.
    SUB  →  match the exit frame's CLOTHING against the entry photos of the
            occupants who have not left. Remove the winner. Occupancy -1.

Face answers ADD because at entry you get a face. Clothing answers SUB because
an exit is a back walking away and there is no face to work with. The entry
photo is therefore not a trophy — it is the wardrobe reference that the next
exit will be matched against, and it is retired the moment its owner leaves.

**Occupancy is the size of this cache.** Not a counter kept beside it, not a
headcount derived from face hits — the cache IS the room. One object.

Two rules fall out of that and are enforced here rather than by convention:

  * A SUB with no occupants is a no-op, not a negative. If the room is empty
    nobody can leave it, so an unmatched exit is evidence that an ADD was
    missed, and it is recorded as such instead of corrupting the count.
  * An ADD for someone already in the cache REFRESHES their photo instead of
    adding a second body. Walking past the door twice is not two people.
"""
from __future__ import annotations

from . import paths as _paths

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

_REPO = _paths.ROOT
CACHE_DIR = Path(
    os.environ.get("RAIL_OCCUPANT_CACHE", str(_REPO / "data" / "occupants"))
)
CACHE_JSON = CACHE_DIR / "cache.json"

# sig_dist is a distance: lower is a better clothing match. Measured on the
# 08-04 labelled walk-out probe, a correct pairing sat well under 0.5 while
# unrelated people sat above it. Kept generous because the alternative to a
# match is losing the person from the room entirely.
SUB_MAX_DIST = float(os.environ.get("RAIL_SUB_MAX_DIST", "0.55"))
# With two or more candidates the winner must beat the runner-up by this much,
# otherwise the exit is ambiguous and belongs to the human match game rather
# than to an automatic decrement.
SUB_MIN_MARGIN = float(os.environ.get("RAIL_SUB_MIN_MARGIN", "0.08"))

_lock = threading.RLock()


def _load() -> dict[str, Any]:
    if not CACHE_JSON.is_file():
        return {"schema": "occupant_cache/v1", "occupants": {}}
    try:
        d = json.loads(CACHE_JSON.read_text(encoding="utf-8"))
        d.setdefault("occupants", {})
        return d
    except Exception:
        return {"schema": "occupant_cache/v1", "occupants": {}}


def _save(d: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    d["ts"] = time.time()
    tmp = CACHE_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(CACHE_JSON)


def occupants() -> list[dict[str, Any]]:
    """Who is in the room, newest arrival last. This IS the headcount."""
    with _lock:
        d = _load()
    return sorted(d["occupants"].values(), key=lambda o: o.get("entered_ts") or 0)


def headcount() -> int:
    with _lock:
        return len(_load()["occupants"])


def add(
    name: str,
    *,
    photo: str | Path | None = None,
    trait_sig: dict | None = None,
    sim: float | None = None,
    face_px: list | None = None,
    event_id: str | None = None,
    ts: float | None = None,
) -> dict[str, Any]:
    """Oracle said ADD and Inspector named the face. Cache the entry photo."""
    key = str(name or "").strip().lower()
    if not key:
        return {"ok": False, "reason": "no_name"}
    ts = float(ts or time.time())
    with _lock:
        d = _load()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        stored = None
        if photo:
            src = Path(photo)
            if src.is_file():
                stored = CACHE_DIR / f"{key}_entry.jpg"
                try:
                    shutil.copyfile(src, stored)
                except OSError:
                    stored = None
        already = key in d["occupants"]
        d["occupants"][key] = {
            "name": key,
            "entered_ts": (
                d["occupants"][key].get("entered_ts") if already else ts
            ),
            "refreshed_ts": ts if already else None,
            "entry_photo": str(stored) if stored else None,
            "trait_sig": trait_sig,
            "face_sim": sim,
            "face_px": face_px,
            "event_id": event_id,
        }
        _save(d)
        return {
            "ok": True,
            "op": "refresh" if already else "add",
            "name": key,
            "headcount": len(d["occupants"]),
        }


def sub(
    *,
    trait_sig: dict | None = None,
    event_id: str | None = None,
    ts: float | None = None,
) -> dict[str, Any]:
    """Oracle said SUB. Match this exit's clothing against the people who are
    still here, and retire the best match."""
    from .door_capture import sig_dist

    ts = float(ts or time.time())
    with _lock:
        d = _load()
        present = d["occupants"]
        if not present:
            # Nobody is here, so nobody can leave. This is an ADD we missed,
            # not a person to subtract. Never let the count go negative.
            return {"ok": False, "reason": "empty_room", "headcount": 0}

        if len(present) == 1 and trait_sig is None:
            only = next(iter(present))
            present.pop(only)
            _save(d)
            return {"ok": True, "name": only, "method": "sole_occupant",
                    "headcount": len(present)}

        if trait_sig is None:
            return {"ok": False, "reason": "no_exit_signature",
                    "candidates": sorted(present), "headcount": len(present)}

        scored: list[tuple[float, str]] = []
        for k, o in present.items():
            osig = o.get("trait_sig")
            if not osig:
                continue
            try:
                scored.append((float(sig_dist(osig, trait_sig)), k))
            except Exception:
                continue
        if not scored:
            if len(present) == 1:
                only = next(iter(present))
                present.pop(only)
                _save(d)
                return {"ok": True, "name": only, "method": "sole_no_sig",
                        "headcount": len(present)}
            return {"ok": False, "reason": "no_comparable_entry_photo",
                    "candidates": sorted(present), "headcount": len(present)}

        scored.sort()
        best_d, best = scored[0]
        margin = (scored[1][0] - best_d) if len(scored) > 1 else None
        if best_d > SUB_MAX_DIST:
            return {"ok": False, "reason": "no_clothing_match",
                    "best": best, "dist": round(best_d, 4),
                    "candidates": sorted(present), "headcount": len(present)}
        if margin is not None and margin < SUB_MIN_MARGIN:
            # Two people dressed alike enough that the machine should not
            # guess — this is exactly the case the human match game exists for.
            return {"ok": False, "reason": "ambiguous",
                    "best": best, "dist": round(best_d, 4),
                    "margin": round(margin, 4),
                    "candidates": sorted(present), "headcount": len(present)}

        present.pop(best, None)
        _save(d)
        return {"ok": True, "name": best, "method": "clothing",
                "dist": round(best_d, 4),
                "margin": round(margin, 4) if margin is not None else None,
                "headcount": len(present)}


def reset() -> dict[str, Any]:
    with _lock:
        _save({"schema": "occupant_cache/v1", "occupants": {}})
    return {"ok": True, "headcount": 0}
