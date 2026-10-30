"""Rule 2 helper: LIVE vs SCREEN for unmatched faces (HTTP only, no Crissa).

Uses nightly empty-room reference frames (M&S room-reference-capture) when present
so the live 27B (ops in Normal, crew in Render/Beast) can compare candidate
face crops against known empty/poster context.

OWNERSHIP (the User / Fable 2026-08-02):
  - This is NOT door-event judgment. Door add/sub is CODE + human gold / oracle.
  - Caller: rail-presence door buffer / sweep for unmatched faces only.
  - Must NOT hammer ops: enable flag + rate limit + crop cache (measured ~200/min
    when is_live ran on every face every buffer frame — that was a bug).

Env:
  RAIL_VLM_LIVE=0|1          master enable (default 1, but rate-limited)
  RAIL_VLM_LIVE_MAX_PER_MIN  hard cap (default 12)
  RAIL_VLM_LIVE_CACHE_SEC    cache same crop hash (default 45)
  RAIL_PRESENCE_VLM_URL      Normal default :8000; Render/Beast ride crew
  RAIL_PRESENCE_VLM_MODEL    Normal default ops; Render/Beast crew
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

import numpy as np

from .occupancy_llm import chat as occupancy_chat
from .refs import LIVE_DIR as REF_DIR

# Must match --served-model-name. Occupancy picks crew when ops is parked.
# Attach at most one empty-room ref so prompt stays small
USE_REF = os.environ.get("RAIL_VLM_LIVE_USE_REF", "1") not in ("0", "false", "no")
# Master switch — set 0 to stop all ops LIVE/SCREEN traffic from presence
ENABLED = os.environ.get("RAIL_VLM_LIVE", "1") not in ("0", "false", "no")
MAX_PER_MIN = max(0, int(os.environ.get("RAIL_VLM_LIVE_MAX_PER_MIN", "12")))
CACHE_SEC = float(os.environ.get("RAIL_VLM_LIVE_CACHE_SEC", "45"))

_lock = threading.Lock()
_call_ts: deque[float] = deque()
_cache: dict[str, tuple[float, bool]] = {}  # hash -> (ts, live)
_stats = {
    "calls": 0,
    "cache_hits": 0,
    "rate_limited": 0,
    "disabled": 0,
    "errors": 0,
    "last_call_ts": None,
}


def stats() -> dict:
    with _lock:
        return {
            "enabled": ENABLED,
            "max_per_min": MAX_PER_MIN,
            "cache_sec": CACHE_SEC,
            "url": occupancy_chat()[0],
            "model": occupancy_chat()[1],
            "owner": "rail-presence.vlm_live.is_live — LIVE/SCREEN only, not door oracle",
            **dict(_stats),
            "recent_calls_60s": sum(1 for t in _call_ts if time.time() - t < 60),
        }


def _allow_call() -> bool:
    if not ENABLED or MAX_PER_MIN <= 0:
        with _lock:
            _stats["disabled"] = int(_stats["disabled"]) + 1
        return False
    now = time.time()
    with _lock:
        while _call_ts and now - _call_ts[0] > 60.0:
            _call_ts.popleft()
        if len(_call_ts) >= MAX_PER_MIN:
            _stats["rate_limited"] = int(_stats["rate_limited"]) + 1
            return False
        _call_ts.append(now)
        _stats["calls"] = int(_stats["calls"]) + 1
        _stats["last_call_ts"] = now
        return True


def _pick_empty_ref_b64(max_side: int = 512) -> str | None:
    """Newest home-empty or room-empty ref, downscaled JPEG b64."""
    if not USE_REF or not REF_DIR.is_dir():
        return None
    # DOOR refs only (the User 2026-08-31). This used to glob the station plates
    # too and take whichever was newest by mtime, so the LIVE/SCREEN reference
    # was whatever the retired multi-stop sweep happened to shoot last — an
    # accident of sorting, not a choice. The station plates are gone with the
    # count path; the door refs are the ones that stay current.
    cands = sorted(
        list(REF_DIR.glob("home-empty-*.jpg"))
        + list(REF_DIR.glob("room-empty-stop-door.jpg")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in cands[:6]:
        try:
            if p.stat().st_size < 2000:
                continue
            import cv2

            img = cv2.imread(str(p))
            if img is None:
                continue
            h, w = img.shape[:2]
            scale = min(1.0, float(max_side) / max(h, w))
            if scale < 1.0:
                img = cv2.resize(img, (int(w * scale), int(h * scale)))
            ok, jpg = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if not ok:
                continue
            return base64.b64encode(jpg.tobytes()).decode()
        except Exception:
            continue
    return None


def is_live(frame_bgr: np.ndarray, bbox, *, timeout: float = 20.0) -> bool:
    """Positive LIVE only. Fail closed (no verdict → not a body).

    Call ONLY for unmatched / low-sim faces — never for every buffer face.
    Rate-limited + crop-hash cached so ops is not melted.
    """
    try:
        import cv2

        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = frame_bgr.shape[:2]
        m = 40
        crop = frame_bgr[max(0, y1 - m) : min(h, y2 + m), max(0, x1 - m) : min(w, x2 + m)]
        if crop is None or crop.size == 0:
            return False
        ok, jpg = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return False
        raw = jpg.tobytes()
        # cheap cache key: downsample hash
        key = hashlib.sha1(raw[::17]).hexdigest()[:20]
        now = time.time()
        with _lock:
            hit = _cache.get(key)
            if hit and (now - hit[0]) < CACHE_SEC:
                _stats["cache_hits"] = int(_stats["cache_hits"]) + 1
                return bool(hit[1])
            # prune cache
            if len(_cache) > 256:
                cutoff = now - CACHE_SEC
                dead = [k for k, (t, _) in _cache.items() if t < cutoff]
                for k in dead:
                    _cache.pop(k, None)
        if not _allow_call():
            # fail closed under rate limit / disabled — not a body invent
            return False
        content: list[dict] = [
            {
                "type": "text",
                "text": (
                    "Is this face a LIVE person physically in the room, "
                    "or a face on a SCREEN/poster/photo? One word: LIVE or SCREEN."
                ),
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64,"
                    + base64.b64encode(jpg.tobytes()).decode()
                },
            },
        ]
        # Optional empty-room / baseline ref for poster filtering (M&S nightly)
        ref_b64 = _pick_empty_ref_b64()
        if ref_b64:
            content.insert(
                0,
                {
                    "type": "text",
                    "text": (
                        "Reference image = empty room baseline (no live people). "
                        "Use it to recognize posters/TVs/prints on walls. "
                        "Candidate crop follows."
                    ),
                },
            )
            content.insert(
                1,
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64," + ref_b64},
                },
            )
        vlm_url, vlm_model = occupancy_chat()
        payload = {
            "model": vlm_model,
            "max_tokens": 8,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": content}],
        }
        req = urllib.request.Request(
            vlm_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            msg = json.load(r)["choices"][0]["message"]
        ans = (msg.get("content") or msg.get("reasoning_content") or "").strip().upper()
        live = "LIVE" in ans and "SCREEN" not in ans
        with _lock:
            _cache[key] = (time.time(), live)
        return live
    except Exception:
        with _lock:
            _stats["errors"] = int(_stats["errors"]) + 1
        return False
