"""A reader of Init's registration hold (lib/crissa_registration.py; round 36, 2026-10-19): while an Add/Edit IDs run
has the camera, `held()` is true and the continual auth stands down (service.py: drop_to_guest and the visit lamp's
auth drop keep the User's auth and show no amber). Read fresh at most every CACHE_S; a file older than its own max_s
is no hold. Kill CRISSA_REGISTRATION_HOLD=0 (never held)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

FILE = "registration-hold.json"
CACHE_S = 1.0
_C = {"at": 0.0, "rec": None}


def _home() -> Path:
    env = os.environ.get("CRISSA_HOME")
    return Path(env).expanduser() if env else Path.home() / ".crissa"


def enabled() -> bool:
    return os.environ.get("CRISSA_REGISTRATION_HOLD", "1").strip().lower() not in ("0", "off", "false", "no")


def held(now: float | None = None) -> dict | None:
    if not enabled():
        return None
    now = time.time() if now is None else now
    if now - _C["at"] < CACHE_S:
        rec = _C["rec"]
    else:
        try:
            rec = json.loads((_home() / FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = None
        _C.update(at=now, rec=rec)
    if not isinstance(rec, dict):
        return None
    if now - float(rec.get("since") or 0) > float(rec.get("max_s") or 1200.0):
        return None
    return rec
