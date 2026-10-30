"""The registration hold: one file in the state home while an Add/Edit IDs run has the camera (round 36, 2026-10-19).

The User: "you will need to suspend that Auth requirement for the time [the] guest is in camera FOV." From the
User's Ready press to the guest's last take (or the run's cancel) `registration-hold.json` sits in the state home:
presence's continual auth does not drop the User's auth, does not mark the guest a stranger and shows no amber, and
the ear's trailing voice naming names nobody. presence (crissa_presence/registration_hold.py) and the ear
(crissa_ear/registration_hold.py) read the same file with their own tiny readers; this module is Init's writer.
A hold never outlives HOLD_MAX_S: a reader treats an older file as no hold. Kill CRISSA_IDS_FLOW=0 (no run writes it).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

FILE = "registration-hold.json"
HOLD_MAX_S = 20 * 60.0


def path(home: Path) -> Path:
    return Path(home) / FILE


def set_hold(home: Path, run_id: str, who: str, name: str = "") -> dict[str, Any]:
    rec = {"schema": "registration-hold/1", "run_id": str(run_id), "who": str(who), "name": str(name)[:40],
           "since": time.time(), "max_s": HOLD_MAX_S}
    p = path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return rec


def clear_hold(home: Path, run_id: str = "") -> bool:
    p = path(home)
    try:
        if run_id:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if str(rec.get("run_id") or "") not in ("", run_id):
                return False
        p.unlink()
        return True
    except (OSError, ValueError):
        return False


def held(home: Path, now: float | None = None) -> dict[str, Any] | None:
    """The hold record while one is live, else None (missing, unreadable or older than its max)."""
    try:
        rec = json.loads(path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    since = float(rec.get("since") or 0)
    if (now if now is not None else time.time()) - since > float(rec.get("max_s") or HOLD_MAX_S):
        return None
    return rec
