"""Walk an energy timeline to first motion and end-of-storm.

Same contract as scripts/event-clip.py find_bounds **onset half**:
rest baseline from the quiet head, thr = max(STILL_ENERGY, rest * ONSET_MARGIN),
walk back until QUIET_N consecutive sub-threshold frames. Live uses the camera
energy ring so sub-threshold approach is not discarded (gap A).

REST_MATCH (blob vs rest for end-of-storm) needs JPEGs, not this float ring.
find_rest here is energy-quiet only. Empty-FOV end stays on the tape / empty-ref
path (Busy-off, clip rest_ts).
"""
from __future__ import annotations

import os
from typing import Any

STILL_ENERGY = float(os.environ.get("CLIP_STILL_ENERGY", "3.5"))
ONSET_MARGIN = float(os.environ.get("CLIP_ONSET_MARGIN", "1.6"))
QUIET_N = int(os.environ.get("RAIL_ONSET_QUIET_N", "5"))
# Consecutive energy<0 (pan / lease) is a hard floor. Walking through it
# gave seq 88615 trigger_offset_s=8.73 into a sweep (the User 2026-08-26).
BLIND_N = int(os.environ.get("RAIL_ONSET_BLIND_N", "3"))


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return float(s[len(s) // 2])


def _en(rows: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for r in rows:
        try:
            out.append(float(r.get("energy")))
        except (TypeError, ValueError):
            out.append(-1.0)
    return out


def find_onset(
    rows: list[dict[str, Any]],
    trig_i: int,
) -> dict[str, Any]:
    """rows oldest-first: {seq, ts, energy}. energy < 0 = no sample (pan)."""
    n = len(rows)
    if n == 0:
        return {"why": "empty", "onset_i": 0}
    trig_i = max(0, min(int(trig_i), n - 1))
    en = _en(rows)
    rest_vals = [e for e in en[: max(3, trig_i // 3)] if e >= 0]
    rest = _median(rest_vals)
    thr = max(STILL_ENERGY, rest * ONSET_MARGIN)
    i0, quiet, blind = 0, 0, 0
    why = "buffer_start"
    blind_from_ts = None
    blind_to_ts = None
    for i in range(trig_i, 0, -1):
        if en[i] < 0:
            blind += 1
            quiet = 0
            if blind >= BLIND_N:
                i0 = min(trig_i, i + BLIND_N)
                why = "blind_floor"
                try:
                    blind_from_ts = float(rows[i].get("ts") or 0) or None
                    blind_to_ts = float(
                        rows[min(trig_i, i + BLIND_N - 1)].get("ts") or 0
                    ) or None
                except (TypeError, ValueError, AttributeError):
                    pass
                break
            continue
        blind = 0
        if en[i] < thr:
            quiet += 1
            if quiet >= QUIET_N:
                i0 = i
                why = "onset"
                break
        else:
            quiet = 0
    else:
        why = "onset" if i0 else "buffer_start"
    r0 = rows[i0]
    rt = rows[trig_i]
    try:
        offset = float(rt["ts"]) - float(r0["ts"])
    except (TypeError, ValueError, KeyError):
        offset = None
    out = {
        "onset_i": i0,
        "onset_seq": r0.get("seq"),
        "onset_ts": r0.get("ts"),
        "rest_baseline": round(rest, 3),
        "threshold": round(thr, 3),
        "trigger_offset_s": None if offset is None else round(offset, 3),
        "why": why,
    }
    if why == "blind_floor":
        out["blind_from_ts"] = blind_from_ts
        out["blind_to_ts"] = blind_to_ts
        try:
            out["blind_gap_s"] = round(float(blind_to_ts) - float(blind_from_ts), 3)
        except (TypeError, ValueError):
            out["blind_gap_s"] = None
    return out


def find_rest(
    rows: list[dict[str, Any]],
    trig_i: int,
    *,
    rest_baseline: float | None = None,
    threshold: float | None = None,
) -> dict[str, Any]:
    """Forward from trigger until QUIET_N sub-threshold frames (end of storm)."""
    n = len(rows)
    if n == 0:
        return {"why": "empty", "rest_i": 0}
    trig_i = max(0, min(int(trig_i), n - 1))
    en = _en(rows)
    if threshold is None:
        rest_vals = [e for e in en[: max(3, trig_i // 3)] if e >= 0]
        rest_baseline = _median(rest_vals) if rest_baseline is None else rest_baseline
        threshold = max(STILL_ENERGY, float(rest_baseline) * ONSET_MARGIN)
    i1, quiet, blind = n - 1, 0, 0
    found = False
    why = "buffer_end"
    for i in range(trig_i, n):
        if en[i] < 0:
            blind += 1
            quiet = 0
            if blind >= BLIND_N:
                i1 = max(trig_i, i - BLIND_N)
                why = "blind_floor"
                found = True
                break
            continue
        blind = 0
        if en[i] < float(threshold):
            quiet += 1
            if quiet >= QUIET_N:
                i1 = i
                found = True
                why = "quiet"
                break
        else:
            quiet = 0
    r = rows[i1]
    return {
        "rest_i": i1,
        "rest_seq": r.get("seq"),
        "rest_ts": r.get("ts"),
        "threshold": round(float(threshold), 3),
        "why": why if found else "buffer_end",
    }
