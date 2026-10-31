"""One door for every tool that moves the camera.

Barge (same idea as TTS speaking=True): POST /v1/pause first so the
door-watch recoveries yield, then do the real request. return_door /
lease release resume the watch.

Callers must not talk to :8192 with a bare urllib POST for PTZ/FF.
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

import json
import os
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

CAMERA = os.environ.get("RAIL_CAMERA_URL", _ports.camera()).rstrip("/")

MUTATE_PATHS = frozenset(
    {
        "/aim",
        "/v1/aim",
        "/facelock",
        "/v1/facelock",
        "/facefinder",
        "/v1/facefinder",
        "/look_around",
        "/v1/look_around",
        "/lease",
        "/v1/lease",
        "/sweep",
        "/v1/sweep",
        "/smooth_scan",
        "/v1/smooth_scan",
        "/error_check_sweep",
        "/v1/error_check_sweep",
        "/facefind",
        "/v1/facefind",
        "/pause",
        "/v1/pause",
    }
)
RESUME_PATHS = frozenset(
    {
        "/return_door",
        "/v1/return_door",
        "/door_home",
        "/v1/door_home",
        "/lease/release",
        "/v1/lease/release",
        "/resume",
        "/v1/resume",
    }
)

_DEFAULT_TTL = {
    "look_around": 15.0,
    "sweep": 15.0,
    "smooth_scan": 15.0,
    "error_check_sweep": 15.0,
    "facefind": 15.0,
    "lease": 15.0,
    "aim": 8.0,
    "pause": 15.0,
}


def path_of(path_or_url: str) -> str:
    raw = (path_or_url or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return urlparse(raw).path or "/"
    if not raw.startswith("/"):
        return "/" + raw
    return raw


def is_mutate(path_or_url: str) -> bool:
    return path_of(path_or_url) in MUTATE_PATHS


def is_resume(path_or_url: str) -> bool:
    return path_of(path_or_url) in RESUME_PATHS


def ttl_for(path_or_url: str, body: dict[str, Any] | None = None) -> float:
    path = path_of(path_or_url)
    body = body if isinstance(body, dict) else {}
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    if leaf in ("facefinder", "facelock"):
        on = body.get("on")
        if on is None:
            on = body.get("enable")
        if on is False or str(body.get("action") or "").lower() in (
            "release",
            "off",
            "disable",
        ):
            return 3.0
        return 8.0
    if leaf == "aim":
        try:
            hold = float(body.get("hold_sec") or body.get("lease_sec") or 0)
        except (TypeError, ValueError):
            hold = 0.0
        return max(hold, 8.0)
    if leaf == "lease":
        return 15.0
    return float(_DEFAULT_TTL.get(leaf, 8.0))


def _raw_post(
    path_or_url: str, body: dict[str, Any] | None, timeout: float
) -> dict[str, Any]:
    path = path_of(path_or_url)
    url = path_or_url if path_or_url.startswith("http") else f"{CAMERA}{path}"
    payload = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if not raw:
                return {"ok": True}
            try:
                out = json.loads(raw.decode())
            except Exception:
                return {"ok": True, "bytes": len(raw)}
            return out if isinstance(out, dict) else {"ok": True, "data": out}
    except urllib.error.HTTPError as e:
        try:
            parsed = json.loads(e.read().decode())
            if isinstance(parsed, dict):
                parsed.setdefault("ok", False)
                parsed.setdefault("status", e.code)
                return parsed
        except Exception:
            pass
        return {"ok": False, "error": str(e)[:200], "status": e.code}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


def pause(
    owner: str = "tool",
    ttl_sec: float | None = None,
    reason: str = "",
    timeout: float = 4.0,
    steal: bool = True,
) -> dict[str, Any]:
    """Barge the door watch. AUTH / tools take the camera immediately."""
    ttl = 15.0 if ttl_sec is None else float(ttl_sec)
    return _raw_post(
        "/v1/pause",
        {
            "owner": str(owner or "tool")[:40],
            "ttl_sec": ttl,
            "reason": str(reason or "")[:80],
            "steal": bool(steal),
        },
        timeout,
    )


def resume(owner: str = "", timeout: float = 4.0) -> dict[str, Any]:
    return _raw_post("/v1/resume", {"owner": str(owner or "")[:40]}, timeout)


@contextmanager
def hold(owner: str = "tool", ttl_sec: float = 180.0, reason: str = ""):
    """Take the camera for a whole tool, and always give it back.

    the User 2026-08-14: "pause the oracle service then take the tool, then turn
    oracle back on ... be sure to turn back on when tool closes out."

    Why a session hold and not per-call pauses: `post()` pauses with a TTL sized
    for ONE call (8 s for an aim) and never resumes -- it waits for the TTL to
    lapse. A patrol stop is aim + settle + snap, which routinely runs longer, so
    the door watch wakes up mid-patrol and fights the next aim. One hold across
    the run removes that race.

    The resume is in `finally`, so a raising tool still hands the door back. The
    TTL remains the backstop for the case `finally` cannot cover -- the process
    dying outright.
    """
    pause(owner=owner, ttl_sec=ttl_sec, reason=reason or "tool_hold")
    try:
        yield
    finally:
        try:
            resume(owner=owner)
        except Exception:
            pass  # TTL will expire; never mask the tool's own error


def post(
    path_or_url: str,
    body: dict[str, Any] | None = None,
    *,
    owner: str = "tool",
    ttl_sec: float | None = None,
    timeout: float = 25.0,
) -> dict[str, Any]:
    """Pause (if this call moves the gimbal), then POST, then resume on home."""
    if _ports.camera_off():                      # no camera: a no-op that says so (2026-10-10)
        _ports.say_once("camera calls are no-ops")
        return dict(_ports.OFF)
    path = path_of(path_or_url)
    payload = dict(body or {})
    if is_mutate(path):
        if "owner" not in payload and owner:
            payload = {**payload, "owner": owner}
        pause(
            owner=owner,
            ttl_sec=ttl_sec if ttl_sec is not None else ttl_for(path, payload),
            reason=path,
        )
        return _raw_post(path_or_url, payload, timeout)
    if is_resume(path):
        if path.rstrip("/").endswith("lease/release"):
            out = _raw_post(path_or_url, payload, timeout)
            resume(owner=owner)
            return out
        resume(owner=owner)
    return _raw_post(path_or_url, payload, timeout)
