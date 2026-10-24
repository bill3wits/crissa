"""SoT rev + AUTH transition log — proof of which writer acted.

Glass :8099 has its own rev (1032→1033 on the 05:28 sheet clear). This is
the presence analog: bump only on a material SoT write, record who did it.
"""
from __future__ import annotations

from . import paths as _paths

import json
import os
from pathlib import Path
from typing import Any

from .schema import epoch_to_iso

DATA = _paths.DATA
REV_PATH = Path(os.environ.get("RAIL_SOT_REV", str(DATA / "sot_rev.json")))
AUTH_LOG = Path(os.environ.get("RAIL_AUTH_TRANSITIONS", str(DATA / "auth_transitions.jsonl")))
AUTH_LOG_MAX = 2000


def material(st: Any) -> tuple:
    """Identity of a SoT publish. Mill ticks with the same occupancy do not bump."""
    names = tuple(
        sorted(
            str(getattr(p, "name", "") or "").lower()
            for p in (getattr(st, "people", None) or [])
        )
    )
    return (
        int(getattr(st, "headcount", 0) or 0),
        names,
        bool(getattr(st, "user_auth", False)),
        str(getattr(st, "user_auth_reason", "") or ""),
        bool(getattr(st, "room_busy", False)),
        str(getattr(st, "busy_reason", "") or ""),
        bool(getattr(st, "f2f", False)),
        str(getattr(st, "count_trust", "") or ""),
        str(getattr(st, "visit_lamp", "") or ""),
    )


def who_why(st: Any) -> tuple[str, str]:
    le = getattr(st, "last_event", None) or {}
    if not isinstance(le, dict):
        le = {}
    who = str(le.get("source") or le.get("kind") or "presence")[:80]
    why = str(le.get("reason") or le.get("kind") or "sot")[:120]
    return who, why


def load_rev() -> tuple[int, dict | None]:
    try:
        d = json.loads(REV_PATH.read_text(encoding="utf-8"))
        return int(d.get("rev") or 0), d.get("writer") if isinstance(d.get("writer"), dict) else None
    except Exception:
        return 0, None


def save_rev(rev: int, writer: dict | None) -> None:
    try:
        REV_PATH.parent.mkdir(parents=True, exist_ok=True)
        REV_PATH.write_text(
            json.dumps({"rev": int(rev), "writer": writer}, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def append_auth(row: dict[str, Any]) -> None:
    try:
        AUTH_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, default=str) + "\n"
        with AUTH_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)
        _cap_auth_log()
    except Exception:
        pass


def _cap_auth_log() -> None:
    try:
        raw = AUTH_LOG.read_text(encoding="utf-8")
    except Exception:
        return
    lines = raw.splitlines()
    if len(lines) <= AUTH_LOG_MAX:
        return
    AUTH_LOG.write_text("\n".join(lines[-AUTH_LOG_MAX:]) + "\n", encoding="utf-8")


def auth_row(
    *,
    prev_auth: bool | None,
    prev_reason: str | None,
    st: Any,
    writer: dict | None,
    rev: int,
    now: float,
) -> dict[str, Any]:
    return {
        "ts": now,
        "ts_local": epoch_to_iso(now),
        "from": prev_auth,
        "to": bool(getattr(st, "user_auth", False)),
        "from_reason": prev_reason,
        "reason": str(getattr(st, "user_auth_reason", "") or ""),
        # who minted the hold (FaceFinder source) — the writer is only the
        # publish that crossed the edge
        "source": getattr(st, "user_face_auth_source", None),
        "writer": writer,
        "rev": int(rev),
    }
