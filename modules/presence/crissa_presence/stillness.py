"""Pick door-buffer frames where the body is still (low inter-frame energy).

the User: record body on stillness — not mid-motion blur. Used for enter_still /
clothing leave match. Prefer ~1s of quiet before locking stills.
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

import base64
import os
import time
import urllib.request
from typing import Any

import numpy as np

CAMERA = os.environ.get("RAIL_CAMERA_URL", _ports.camera()).rstrip("/")
# Mean absdiff on 160x90 gray — measured empty room ~1.4–1.7
STILL_ENERGY_MAX = float(os.environ.get("RAIL_STILL_ENERGY_MAX", "2.5"))
# Want this many consecutive still steps (~stride frames)
STILL_RUN_MIN = int(os.environ.get("RAIL_STILL_RUN_MIN", "3"))
# Wall time of still run preferred (seconds of buffer span)
STILL_SEC_MIN = float(os.environ.get("RAIL_STILL_SEC_MIN", "0.8"))
BUFFER_MAX = int(os.environ.get("RAIL_STILL_BUFFER_MAX", "40"))
BUFFER_STRIDE = int(os.environ.get("RAIL_STILL_BUFFER_STRIDE", "2"))


def _energy(g0: np.ndarray, g1: np.ndarray) -> float:
    import cv2

    return float(cv2.absdiff(g0, g1).mean())


def _gray_small(jpeg: bytes) -> np.ndarray | None:
    import cv2

    arr = np.frombuffer(jpeg, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    small = cv2.resize(img, (160, 90), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def fetch_buffer_jpegs(
    *,
    max_frames: int = BUFFER_MAX,
    stride: int = BUFFER_STRIDE,
) -> list[dict[str, Any]]:
    """Pull recent door ring frames with jpeg bytes."""
    url = (
        f"{CAMERA}/v1/buffer?max={max(1, int(max_frames))}"
        f"&full=1&stride={max(1, int(stride))}"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            import json

            body = json.loads(r.read().decode())
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for fr in body.get("frames") or []:
        b64 = fr.get("jpeg_b64")
        if not b64:
            continue
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue
        if len(raw) < 500:
            continue
        out.append(
            {
                "seq": fr.get("seq"),
                "ts": float(fr.get("capture_ts") or time.time()),
                "jpeg": raw,
                "sha": fr.get("sha"),
            }
        )
    return out


def score_stillness(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate each frame with energy vs previous (0 for first)."""
    prev = None
    scored: list[dict[str, Any]] = []
    for fr in frames:
        g = _gray_small(fr["jpeg"])
        if g is None:
            continue
        e = 0.0 if prev is None else _energy(prev, g)
        prev = g
        row = dict(fr)
        row["energy"] = round(e, 3)
        row["still"] = e <= STILL_ENERGY_MAX
        scored.append(row)
    return scored


def pick_still_body_jpegs(
    *,
    max_out: int = 3,
    max_frames: int = BUFFER_MAX,
    stride: int = BUFFER_STRIDE,
    energy_max: float | None = None,
) -> dict[str, Any]:
    """Select up to max_out jpegs from the longest still run in the door buffer.

    Prefers the **end** of a still run (body settled) spanning ≥ STILL_SEC_MIN
    when possible. Falls back to lowest-energy frames if no run is long enough.
    """
    thr = float(energy_max if energy_max is not None else STILL_ENERGY_MAX)
    raw = fetch_buffer_jpegs(max_frames=max_frames, stride=stride)
    scored = score_stillness(raw)
    if not scored:
        return {
            "ok": False,
            "jpegs": [],
            "reason": "no_buffer_frames",
            "energy_max": thr,
        }

    # Find still runs (energy vs prev ≤ thr, first frame always eligible start)
    runs: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for i, fr in enumerate(scored):
        is_still = i == 0 or fr["energy"] <= thr
        if is_still:
            cur.append(fr)
        else:
            if cur:
                runs.append(cur)
            cur = []  # motion breaks the still run
    if cur:
        runs.append(cur)

    # Rank runs by (span_sec, length)
    def run_span(r: list[dict[str, Any]]) -> float:
        if len(r) < 2:
            return 0.0
        return max(0.0, float(r[-1]["ts"]) - float(r[0]["ts"]))

    good = [r for r in runs if len(r) >= STILL_RUN_MIN or run_span(r) >= STILL_SEC_MIN]
    if not good:
        # lowest energy singles
        ordered = sorted(scored, key=lambda x: x["energy"])
        pick = ordered[:max_out]
        jpegs = [p["jpeg"] for p in pick]
        return {
            "ok": bool(jpegs),
            "jpegs": jpegs,
            "reason": "fallback_lowest_energy",
            "n_scored": len(scored),
            "energies": [p["energy"] for p in pick],
            "seqs": [p.get("seq") for p in pick],
            "span_sec": 0.0,
            "energy_max": thr,
        }

    best = max(good, key=lambda r: (run_span(r), len(r)))
    # take last max_out frames of the still run (most settled)
    pick = best[-max_out:]
    jpegs = [p["jpeg"] for p in pick]
    return {
        "ok": True,
        "jpegs": jpegs,
        "reason": "still_run",
        "n_scored": len(scored),
        "run_len": len(best),
        "span_sec": round(run_span(best), 3),
        "energies": [p["energy"] for p in pick],
        "seqs": [p.get("seq") for p in pick],
        "energy_max": thr,
    }
