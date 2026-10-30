"""Unlock → FaceID burst. Evidence only — never enrollment.

the User 2026-08-24: an unlock grabs a face. ~0.5 s of movement, a few frames.
Multi-user office: the body at the keyboard may not be the sticky User.
Writes <records>/unlock/<ts>/ — not <identity>/people/.
"""
from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(
    os.environ.get("RAIL_UNLOCK_EVIDENCE", str(_paths.RECORDS / "unlock"))
).expanduser()
BURST_SEC = float(os.environ.get("RAIL_UNLOCK_BURST_SEC", "0.5"))
BURST_N = max(2, int(os.environ.get("RAIL_UNLOCK_BURST_N", "4")))
MAX_EDGE = int(os.environ.get("RAIL_UNLOCK_JPEG_EDGE", "320"))
CAMERA = os.environ.get("RAIL_CAMERA_URL", _ports.camera()).rstrip("/")
MATCH_SIM = float(os.environ.get("RAIL_PRESENCE_MATCH_SIM") or os.environ.get("CRISSA_FACE_MATCH") or "0.45")  # AuraFace bar, see sweep.py

_lock = threading.Lock()
_last_kick_ts = 0.0


def downscale_jpeg(jpeg: bytes, max_edge: int = MAX_EDGE) -> bytes:
    """Low-res evidence. Pass-through if decode fails."""
    if not jpeg:
        return b""
    try:
        import cv2
        import numpy as np

        im = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            return jpeg
        h, w = im.shape[:2]
        edge = max(h, w)
        if edge <= max_edge:
            ok, buf = cv2.imencode(".jpg", im, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            return buf.tobytes() if ok else jpeg
        scale = max_edge / float(edge)
        im2 = cv2.resize(
            im, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA
        )
        ok, buf = cv2.imencode(".jpg", im2, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        return buf.tobytes() if ok else jpeg
    except Exception:
        return jpeg


def write_burst(
    jpegs: list[bytes],
    *,
    uid: str,
    sim: float | None,
    match: bool,
    granted_by: str | None,
    session_id: str | None = None,
    reason: str = "unlock",
    now: float | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Write low-res frames + sidecar. Never enrollment."""
    ts = time.time() if now is None else float(now)
    dest = (root or ROOT) / time.strftime("%Y%m%dT%H%M%S", time.localtime(ts))
    dest.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for i, raw in enumerate(jpegs):
        if not raw:
            continue
        blob = downscale_jpeg(raw)
        p = dest / f"f{i:02d}.jpg"
        p.write_bytes(blob)
        paths.append(str(p))
    side = {
        "schema": "unlock_face/v1",
        "ts": ts,
        "uid": uid,
        "sim": round(float(sim), 3) if sim is not None else None,
        "match": bool(match),
        "granted_by": granted_by if match else None,
        "session_id": session_id,
        "reason": reason,
        "n_frames": len(paths),
        "frames": paths,
        "enrollment": False,
    }
    sidecar = dest / "sidecar.json"
    sidecar.write_text(json.dumps(side, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "dir": str(dest), "sidecar": str(sidecar), **side}


def snapshot_once(url: str | None = None, timeout: float = 2.0) -> bytes:
    import urllib.request

    u = (url or f"{CAMERA}/snapshot").strip()
    with urllib.request.urlopen(u, timeout=timeout) as r:
        return r.read() or b""


def capture_burst(
    *,
    n: int | None = None,
    span_sec: float | None = None,
    snap: Callable[[], bytes] | None = None,
) -> list[bytes]:
    n = int(n if n is not None else BURST_N)
    span = float(span_sec if span_sec is not None else BURST_SEC)
    gap = span / max(1, n - 1) if n > 1 else 0.0
    take = snap or snapshot_once
    out: list[bytes] = []
    for i in range(n):
        try:
            out.append(take() or b"")
        except Exception:
            out.append(b"")
        if i + 1 < n and gap > 0:
            time.sleep(gap)
    return out


def maybe_kick(
    prev_unlocked: bool | None,
    now_unlocked: bool,
    *,
    min_gap_sec: float = 8.0,
) -> bool:
    """True on locked → unlocked rising edge (not first-unknown)."""
    global _last_kick_ts
    if prev_unlocked is not False or now_unlocked is not True:
        return False
    now = time.time()
    with _lock:
        if now - _last_kick_ts < min_gap_sec:
            return False
        _last_kick_ts = now
        return True
