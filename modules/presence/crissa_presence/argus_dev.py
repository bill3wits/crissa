"""Argus Dev ledger — his count, his misses, parallel to Code SoT.

the User 2026-08-25: second ledger for Dev. Argus holds the roster and
picks who to +1/−1. Code SoT stays :8191/v1/presence until inherit.
This object is GET /v1/argus_dev. Training log: data/argus_dev.jsonl.
"""
from __future__ import annotations

from . import paths as _paths

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_ROOT = _paths.DATA
STATE = Path(os.environ.get("RAIL_ARGUS_DEV_STATE", str(_ROOT / "argus_dev.json")))
LOG = Path(os.environ.get("RAIL_ARGUS_DEV_LOG", str(_ROOT / "argus_dev.jsonl")))
USER_ID = (os.environ.get("HOUSE_USER_ID") or "user").strip().lower()
_LOCK = threading.Lock()


def _empty() -> dict[str, Any]:
    return {
        "schema": "argus_dev/v1",
        "headcount": 0,
        "people": [],
        "n_anon": 0,
        "updated_ts": None,
        "last": None,
    }


def load() -> dict[str, Any]:
    try:
        if STATE.is_file():
            d = json.loads(STATE.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("people"), list):
                return d
    except (OSError, ValueError):
        pass
    return _empty()


def _save(st: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE)


def _append(row: dict[str, Any]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def seed_from_code(names: list[str] | None) -> dict[str, Any]:
    """Fair start: copy Code's present names once if Argus is empty."""
    with _LOCK:
        st = load()
        if st.get("people"):
            return st
        for n in names or []:
            n = str(n or "").strip().lower()
            if not n:
                continue
            st["people"].append(
                {"id": n, "clothes": None, "hair": None, "sex": None, "enter_ts": time.time()}
            )
        st["headcount"] = len(st["people"])
        st["updated_ts"] = time.time()
        st["last"] = {"action": "seed_from_code", "names": [p["id"] for p in st["people"]]}
        _save(st)
        return st


def _ids(st: dict[str, Any]) -> list[str]:
    return [str(p.get("id") or "") for p in st.get("people") or [] if p.get("id")]


def _pick_leave(st: dict[str, Any], who: str, sex: str) -> str | None:
    """Argus names who left. Never pin the User by arithmetic.

    the User 2026-08-25: never label someone else as User; miss_empty beats a guess.
    The User is only picked when Argus actually said the User.
    """
    ids = _ids(st)
    if who == USER_ID:
        return USER_ID if USER_ID in ids else None
    if who and who in ids:
        return who
    return None


def apply(
    *,
    event: str,
    who: str | None = None,
    clothes: str | None = None,
    hair: str | None = None,
    sex: str | None = None,
    seq: int | None = None,
    eid: str | None = None,
    code: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Argus writes HIS ledger. Does not wait on Code clothes-match."""
    ev = str(event or "").strip().lower()
    who_n = str(who or "").strip().lower()
    if who_n in ("", "unknown", "none", "no_face", "null"):
        who_n = ""
    sx = str(sex or "").strip().upper()[:1] or None
    with _LOCK:
        st = load()
        pid = None
        action = "ignore"
        if ev == "enter":
            ids = _ids(st)
            pid = who_n if who_n else f"a{int(st.get('n_anon') or 0) + 1}"
            if pid in ids:
                action = "already_present"
            else:
                st["people"].append(
                    {
                        "id": pid,
                        "clothes": clothes,
                        "hair": hair,
                        "sex": sx,
                        "enter_ts": time.time(),
                    }
                )
                if not who_n:
                    st["n_anon"] = int(st.get("n_anon") or 0) + 1
                action = "enter"
        elif ev == "exit":
            pid = _pick_leave(st, who_n, sx or "")
            if pid:
                st["people"] = [p for p in st["people"] if str(p.get("id")) != pid]
                action = "exit"
            else:
                action = "miss_empty"
        elif ev in ("sighting", "unresolved"):
            action = ev
            pid = who_n or None
        st["headcount"] = len(st["people"])
        st["updated_ts"] = time.time()
        last = {
            "action": action,
            "event": ev,
            "who": who_n or None,
            "pid": pid,
            "seq": seq,
            "eid": eid,
            "headcount": st["headcount"],
        }
        st["last"] = last
        _save(st)
        row = dict(last)
        row["ts"] = st["updated_ts"]
        row["people"] = _ids(st)
        if isinstance(code, dict):
            row["code_ok"] = code.get("ok")
            row["code_applied"] = code.get("applied")
            row["code_reason"] = code.get("reason") or code.get("error")
            row["code_pid"] = code.get("person_id")
        _append(row)
        return {"ok": True, **last, "headcount": st["headcount"], "people": _ids(st)}


def snapshot() -> dict[str, Any]:
    with _LOCK:
        st = load()
    st["ok"] = True
    return st
