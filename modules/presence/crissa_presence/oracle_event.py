"""One door event row, opened on motion. FaceID and Argus attach — no field race.

event_id is the trigger timestamp (ev_<ms>), the same id pack dirs use.
Oracle opens the row at start_event. Parallel:
  FaceID stamps person_id / face_sim onto that id
  Argus stamps event / clothes / hair / sex onto that id
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .oracle_visit import short_id

_LOCK = threading.Lock()
_EVENTS: dict[str, dict[str, Any]] = {}
_ALIASES: dict[str, str] = {}  # trigger-id → pack id (join key)
# Live speaker init for Inspector/Oracle. Not a door visit, never counted.
SPEAKER_EID = "ev_speaker"


def event_id_from_ts(ts: float | int | None) -> str:
    t = float(ts or 0.0)
    if t <= 0:
        t = time.time()
    if t < 1e12:
        t *= 1000.0
    return f"ev_{int(t)}"


def event_id_from_pack(pack: str | Path | None) -> str:
    name = Path(str(pack or "")).name
    if name.startswith("ev_") and name[3:].split("s")[0].isdigit():
        return "ev_" + "".join(c for c in name[3:] if c.isdigit())
    return ""


def _blank(eid: str, trigger_ts: float) -> dict[str, Any]:
    ts = float(trigger_ts)
    if ts > 1e12:
        ts = ts / 1000.0
    return {
        "event_id": eid,
        "trigger_ts": ts,
        "opened_by": "oracle",
        "opened_ts": time.time(),
        "event": None,
        "person_id": None,
        "short_id": None,
        "enter_ts": ts,
        "exit_ts": None,
        "clothes": None,
        "hair": None,
        "sex": None,
        "faceid": None,
        "argus": None,
        "agrees": None,
        "scratch": False,
        "counted": False,
        "body_i": 0,
        "pack_id": eid,
        # The first frame of motion, as a grabber sequence number. The row is
        # opened on the door tick, so this is unknown here and filled by
        # set_onset() once door_capture has walked back to it.
        #
        # Both witnesses need it and they need the SAME one: Argus starts its
        # clip at this frame, and the hardware facefinder's shot joins on it.
        # A timestamp would not do — event_id, trigger_ts and the pack name are
        # three different instants (the pre-roll ran 6.2 s on 2026-08-15), which
        # is how a Glimmer exit landed on an old pack. A seq is one frame.
        "onset_seq": None,
        "onset_ts": None,
    }


def open_event(trigger_ts: float, *, pack_id: str | None = None) -> dict[str, Any]:
    """Create the row. Idempotent. Must run at motion trigger, before workers."""
    eid = (pack_id or "").strip() or event_id_from_ts(trigger_ts)
    if not eid.startswith("ev_"):
        eid = event_id_from_ts(trigger_ts)
    with _LOCK:
        if eid not in _EVENTS:
            _EVENTS[eid] = _blank(eid, trigger_ts)
        return dict(_EVENTS[eid])


def _canonical(event_id: str) -> str:
    eid = (event_id or "").strip()
    return _ALIASES.get(eid, eid)


def get_event(event_id: str) -> dict[str, Any] | None:
    with _LOCK:
        row = _EVENTS.get(_canonical(event_id))
        return dict(row) if row else None


def rekey_event(old_id: str, new_id: str) -> dict[str, Any]:
    """Point the trigger-time row at the pack id (join key)."""
    old = (old_id or "").strip()
    new = (new_id or "").strip()
    if not old or not new:
        return {"ok": False, "reason": "need_ids"}
    with _LOCK:
        if old == new:
            row = _EVENTS.get(new)
            return {"ok": True, "event_id": new, "row": dict(row) if row else None}
        row = _EVENTS.pop(old, None)
        if row is None:
            row = _EVENTS.get(new)
            if row is None:
                return {"ok": False, "reason": "no_event", "event_id": old}
            _ALIASES[old] = new
            return {"ok": True, "event_id": new, "row": dict(row)}
        if new in _EVENTS:
            # pack row already exists — keep pack row, alias trigger id to it
            _ALIASES[old] = new
            return {"ok": True, "event_id": new, "row": dict(_EVENTS[new])}
        row["event_id"] = new
        row["pack_id"] = new
        _EVENTS[new] = row
        _ALIASES[old] = new
        return {"ok": True, "event_id": new, "row": dict(row)}


def mark_counted(event_id: str, counted: bool) -> dict[str, Any]:
    with _LOCK:
        row = _EVENTS.get(_canonical(event_id))
        if row is None:
            return {"ok": False, "reason": "no_event", "event_id": event_id}
        row["counted"] = bool(counted)
        return {"ok": True, "event_id": row["event_id"], "row": dict(row)}


def list_events() -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(r) for r in _EVENTS.values()]


def open_visits() -> list[dict[str, Any]]:
    """Rows still open (exit_ts is null) — occupancy mill. Speaker inits are not visits."""
    return [
        r
        for r in list_events()
        if r.get("exit_ts") is None
        and str(r.get("event") or "").lower() not in ("speaker", "security")
        and str(r.get("event_id") or "") != SPEAKER_EID
        and not str(r.get("event_id") or "").startswith("ev_x")
    ]


def _set_agrees(row: dict[str, Any]) -> None:
    sex = row.get("sex")
    buf = row.get("faceid") if isinstance(row.get("faceid"), dict) else {}
    bg = (buf or {}).get("gender") or (buf or {}).get("sex")
    if sex in ("M", "F") and bg:
        row["agrees"] = str(sex).upper()[:1] == str(bg).upper()[:1]
    elif sex in ("M", "F") and buf.get("name"):
        row["agrees"] = None


def set_onset(
    event_id: str,
    *,
    seq: int | None,
    ts: float | None = None,
) -> dict[str, Any]:
    """Record the first frame of motion on an open row. Does not open a row.

    Write-once: the onset is a measurement of the clip, not an opinion about it,
    so a later writer must not move it. Anything joining on this key would be
    reading a different frame than the one it was handed.
    """
    eid = _canonical((event_id or "").strip())
    if seq is None:
        return {"ok": False, "reason": "no_seq", "event_id": eid}
    with _LOCK:
        row = _EVENTS.get(eid)
        if row is None:
            return {"ok": False, "reason": "no_event", "event_id": eid}
        if row.get("onset_seq") is not None:
            return {
                "ok": True,
                "reason": "already_set",
                "event_id": eid,
                "row": dict(row),
            }
        row["onset_seq"] = int(seq)
        if ts is not None:
            row["onset_ts"] = float(ts)
        return {"ok": True, "event_id": eid, "row": dict(row)}


def stamp_speaker_face(
    *,
    name: str | None,
    sim: float | None = None,
    sex: str | None = None,
    source: str = "faceid",
    bbox: Any = None,
) -> dict[str, Any]:
    """FaceID face of the speaker onto the Oracle ring. Not a count."""
    pid = str(name or "").strip().lower() or None
    if not pid:
        return {"ok": False, "reason": "need_name"}
    now = time.time()
    with _LOCK:
        row = _EVENTS.get(SPEAKER_EID)
        if row is None:
            row = _blank(SPEAKER_EID, now)
            row["event"] = "speaker"
            row["counted"] = False
            row["opened_by"] = "faceid"
            _EVENTS[SPEAKER_EID] = row
        row["event"] = "speaker"
        row["counted"] = False
        row["person_id"] = pid
        row["short_id"] = short_id(pid)
        row["scratch"] = False
        row["source"] = str(source or "faceid")
        row["trigger_ts"] = now
        buf = {
            "name": pid,
            "sim": sim,
            "bbox": bbox,
            "source": source,
        }
        if sex in ("M", "F", "m", "f"):
            sx = str(sex).upper()[:1]
            buf["gender"] = sx
            row["sex"] = sx
        row["faceid"] = buf
        _set_agrees(row)
        return {"ok": True, "event_id": SPEAKER_EID, "row": dict(row)}


def stamp_stranger(
    xid: str,
    *,
    kind: str = "enter",
    seq: int | None = None,
    photo: str | None = None,
    source: str = "faceid",
    event_id: str | None = None,
    sex: str | None = None,
) -> dict[str, Any]:
    """X# security init on the Oracle ring. Not a count."""
    pid = str(xid or "").strip().lower()
    if not pid.startswith("x") or not pid[1:].isdigit():
        return {"ok": False, "reason": "need_xid"}
    eid = f"ev_{pid}"
    now = time.time()
    with _LOCK:
        row = _EVENTS.get(eid)
        if row is None:
            row = _blank(eid, now)
            row["opened_by"] = "security"
            _EVENTS[eid] = row
        row["event"] = "security"
        row["counted"] = False
        row["person_id"] = pid
        row["short_id"] = f"X{pid[1:]}"
        row["scratch"] = True
        row["source"] = str(source or "faceid")
        row["trigger_ts"] = now
        row["kind"] = str(kind or "enter")
        if seq is not None:
            row["seq"] = int(seq)
        if photo:
            row["photo"] = str(photo)
        if event_id:
            row["door_event_id"] = str(event_id)
        if sex in ("M", "F", "m", "f"):
            row["sex"] = str(sex).upper()[:1]
        return {"ok": True, "event_id": eid, "row": dict(row)}


def stamp_faceid(
    event_id: str,
    *,
    name: str | None,
    sim: float | None = None,
    face_px: Any = None,
    door_view: Any = None,
    gender: str | None = None,
) -> dict[str, Any]:
    """Attach FaceID face to an existing event. Does not open a row."""
    eid = _canonical((event_id or "").strip())
    with _LOCK:
        row = _EVENTS.get(eid)
        if row is None:
            return {"ok": False, "reason": "no_event", "event_id": eid}
        buf = {
            "name": (name or "").strip().lower() or None,
            "sim": sim,
            "face_px": face_px,
            "door_view": door_view,
        }
        if gender:
            buf["gender"] = str(gender).upper()[:1]
        row["faceid"] = buf
        if buf["name"]:
            row["person_id"] = buf["name"]
            row["short_id"] = short_id(buf["name"])
            row["scratch"] = False
        _set_agrees(row)
        return {"ok": True, "event_id": eid, "row": dict(row)}


def stamp_argus(
    event_id: str,
    *,
    event: str | None,
    clothes: str | None = None,
    hair: str | None = None,
    sex: str | None = None,
    note: str | None = None,
    pack_id: str | None = None,
) -> dict[str, Any]:
    """Attach Argus judgment to an existing event. Does not open a row."""
    eid = _canonical((event_id or "").strip())
    with _LOCK:
        row = _EVENTS.get(eid)
        if row is None:
            return {"ok": False, "reason": "no_event", "event_id": eid}
        ev = str(event or "").strip().lower() or None
        if ev:
            row["event"] = ev
        if clothes:
            row["clothes"] = str(clothes)[:240]
        if hair:
            row["hair"] = str(hair)[:120]
        if sex in ("M", "F", "m", "f"):
            row["sex"] = str(sex).upper()
        row["argus"] = {
            "kind": ev or row.get("event"),
            "ts": time.time(),
            "pack": pack_id or eid,
        }
        if note:
            row["note"] = str(note)[:240]
        _set_agrees(row)
        return {"ok": True, "event_id": eid, "row": dict(row)}


def stamp_person(event_id: str, person_id: str) -> dict[str, Any]:
    """Who this row is about. Does not open a row or change count."""
    eid = _canonical((event_id or "").strip())
    pid = str(person_id or "").strip().lower()
    if not pid or pid == "unknown":
        return {"ok": False, "reason": "need_person_id", "event_id": eid}
    with _LOCK:
        row = _EVENTS.get(eid)
        if row is None:
            return {"ok": False, "reason": "no_event", "event_id": eid}
        row["person_id"] = pid
        row["short_id"] = short_id(pid)
        return {"ok": True, "event_id": eid, "row": dict(row)}


def close_open_for(
    person_id: str,
    *,
    now: float | None = None,
    leave_match: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """End every still-open row for this person. Enter clothes stay on the card."""
    pid = str(person_id or "").strip().lower()
    if not pid:
        return []
    ts = float(now if now is not None else time.time())
    closed: list[dict[str, Any]] = []
    with _LOCK:
        for row in _EVENTS.values():
            if str(row.get("person_id") or "").strip().lower() != pid:
                continue
            if row.get("exit_ts") is not None:
                continue
            row["exit_ts"] = ts
            if str(row.get("event") or "").lower() == "exit" and leave_match:
                row["leave_match"] = dict(leave_match)
            closed.append(dict(row))
    return closed


def record_leave(
    event_id: str,
    person_id: str,
    *,
    now: float | None = None,
    leave_match: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tie a leave to the enter card: stamp who, write the match, close them.

    Measured hole 2026-08-17 05:56:57: Argus described a man in a gray
    long-sleeve walking out; CODE applied it to s1 (cardigan, F) and left
    her enter row open with exit_ts=None. The next leave still saw her as
    present. close_event existed and was never called from the visit path.
    """
    eid = (event_id or "").strip()
    pid = str(person_id or "").strip().lower()
    ts = float(now if now is not None else time.time())
    stamped = stamp_person(eid, pid) if eid else {"ok": False, "reason": "no_event"}
    if stamped.get("ok"):
        with _LOCK:
            row = _EVENTS.get(_canonical(eid))
            if row is not None:
                row["event"] = "exit"
                if leave_match:
                    row["leave_match"] = dict(leave_match)
    closed = close_open_for(pid, now=ts, leave_match=leave_match)
    return {
        "ok": True,
        "event_id": eid or None,
        "person_id": pid or None,
        "closed": closed,
        "row": get_event(eid) if eid else None,
    }


def close_event(event_id: str, *, now: float | None = None) -> dict[str, Any]:
    eid = _canonical((event_id or "").strip())
    with _LOCK:
        row = _EVENTS.get(eid)
        if row is None:
            return {"ok": False, "reason": "no_event", "event_id": eid}
        row["exit_ts"] = float(now if now is not None else time.time())
        if not row.get("event"):
            row["event"] = "exit"
        return {"ok": True, "event_id": eid, "row": dict(row)}


def mint_scratch(event_id: str) -> dict[str, Any]:
    """FaceID null → S<n> on THIS visit only. Released when exit_ts is set."""
    eid = _canonical((event_id or "").strip())
    with _LOCK:
        row = _EVENTS.get(eid)
        if row is None:
            return {"ok": False, "reason": "no_event", "event_id": eid}
        if row.get("person_id") and not row.get("scratch"):
            return {"ok": True, "event_id": eid, "row": dict(row)}
        used: set[int] = set()
        for r in _EVENTS.values():
            if r.get("exit_ts") is not None:
                continue
            sid = str(r.get("short_id") or "")
            if sid.upper().startswith("S") and sid[1:].isdigit():
                used.add(int(sid[1:]))
        n = 1
        while n in used:
            n += 1
        row["person_id"] = f"s{n}"
        row["short_id"] = f"S{n}"
        row["scratch"] = True
        return {"ok": True, "event_id": eid, "row": dict(row)}


def dump_json() -> str:
    return json.dumps(list_events(), default=str, indent=2)
