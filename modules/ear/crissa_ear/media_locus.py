"""Media sound locus — init setups + dial tiny red/green ×.

Setups (the User 2026-08-02):
  * internal — media only on VoiceOS host; block via sink/speak; no dial hour
  * external — speakers in room (often contiguous with user seat);
               one blanked clock hour; tiny × red idle / green when media_on

This house default: external @ 9 (DD desk speakers), user seat 8 next to it.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .home import EAR
from typing import Any

GEOM_PATH = Path(
    os.environ.get(
        "RAIL_ROOM_GEOMETRY",
        str(EAR / "room_geometry.json"),
    )
)
DEFAULT_STATION = int(os.environ.get("RAIL_MEDIA_STATION", "9"))
DEFAULT_MODE = os.environ.get("RAIL_MEDIA_MODE", "external").strip().lower()
DEFAULT_USER_SEAT = int(os.environ.get("RAIL_USER_SEAT", "8"))


def _load() -> dict[str, Any]:
    if not GEOM_PATH.is_file():
        return {
            "schema": "room_geometry/v1",
            "media_mode": DEFAULT_MODE,
            "media_station": DEFAULT_STATION if DEFAULT_MODE != "internal" else None,
            "blanked_sit": [DEFAULT_STATION] if DEFAULT_MODE != "internal" else [],
            "blocked_stations": [4, 5, 6, 7],
            "legal_stations": [8, 10, 11, 12, 1, 2, 3],
            "user_seat": DEFAULT_USER_SEAT,
        }
    try:
        return json.loads(GEOM_PATH.read_text())
    except Exception:
        return {"schema": "room_geometry/v1", "media_mode": DEFAULT_MODE}


def _norm_hour(st: int | None) -> int | None:
    if st is None:
        return None
    st = int(st)
    if st == 0:
        st = 12
    if st < 1 or st > 12:
        return None
    return st


def get_media_locus() -> dict[str, Any]:
    g = _load()
    mode = str(g.get("media_mode") or DEFAULT_MODE).lower()
    if mode not in ("internal", "external"):
        mode = "external"
    st = _norm_hour(g.get("media_station"))
    if mode == "external" and st is None:
        st = DEFAULT_STATION
    if mode == "internal":
        st = None
    user_seat = _norm_hour(g.get("user_seat") or DEFAULT_USER_SEAT) or 8
    contiguous = bool(
        mode == "external"
        and st is not None
        and min(abs(st - user_seat), 12 - abs(st - user_seat)) <= 2
    )
    return {
        "ok": True,
        "schema": "media_locus/v1",
        "mode": mode,
        "station": st,
        "blanked_sit": mode == "external" and st is not None,
        "user_seat": user_seat,
        "contiguous_with_user_seat": contiguous,
        "internal_block": mode == "internal",
        "label": g.get("media_label")
        or (
            "same computer — block media internally"
            if mode == "internal"
            else f"speakers @ hour {st} (tiny red/green ×; not a sit seat)"
        ),
        "dial": {
            "door": 12,
            "camera": "center",
            "media_hour": st,
            "mark": "tiny_x",
            "idle": "red_x",
            "active": "green_x",
            "show_mark": mode == "external" and st is not None,
            "note": (
                "internal: no × (sink block). "
                "external: tiny × on hour — red idle, green media on"
            ),
        },
        "path": str(GEOM_PATH),
    }


def set_media_locus(
    station: int | None = None,
    *,
    mode: str | None = None,
    label: str | None = None,
    source: str = "init_interview",
    user_seat: int | None = None,
    contiguous_with_user: bool | None = None,
) -> dict[str, Any]:
    """Write media setup from init.

    mode=internal → no station, internal_block true
    mode=external → station required (default 9)
    """
    g = _load()
    m = (mode or g.get("media_mode") or DEFAULT_MODE or "external").strip().lower()
    if m in ("same_computer", "same-pc", "host", "none", "no_speakers"):
        m = "internal"
    if m in ("room", "speakers", "locus", "desk"):
        m = "external"
    if m not in ("internal", "external"):
        return {"ok": False, "error": "mode must be internal|external"}

    st = _norm_hour(station) if station is not None else _norm_hour(g.get("media_station"))
    if m == "external":
        if st is None:
            st = DEFAULT_STATION
    else:
        st = None

    us = _norm_hour(user_seat if user_seat is not None else g.get("user_seat") or DEFAULT_USER_SEAT)

    g["schema"] = "room_geometry/v1"
    g["media_mode"] = m
    g["media_station"] = st
    g["user_seat"] = us
    if contiguous_with_user is not None:
        g["media_contiguous_user"] = bool(contiguous_with_user)
    elif m == "external" and st and us:
        g["media_contiguous_user"] = min(abs(st - us), 12 - abs(st - us)) <= 2

    if m == "internal":
        g["media_label"] = label or "same computer — block media internally (no dial hour)"
        # remove media hours from blanked_sit only
        blanked = [
            int(x)
            for x in (g.get("blanked_sit") or [])
            if str(x).lstrip("-").isdigit()
        ]
        # drop previous media stations if tagged
        g["blanked_sit"] = [b for b in blanked if b not in (9, st or -1)]
    else:
        g["media_label"] = label or (
            f"speakers @ hour {st} · tiny red/green × · blanked sit"
            + (" · next to user seat" if g.get("media_contiguous_user") else "")
        )
        blanked = set(
            int(x) for x in (g.get("blanked_sit") or []) if str(x).lstrip("-").isdigit()
        )
        old = g.get("_prev_media_station")
        # clear old media hour from blanked
        for prev in list(blanked):
            if prev == st:
                continue
        # only one media blank
        blanked = {b for b in blanked if b != (g.get("media_station") or -1)}
        # re-read after we set station
        blanked = set(
            int(x)
            for x in (g.get("blanked_sit") or [])
            if str(x).lstrip("-").isdigit() and int(x) != int(st or -1)
        )
        # remove any prior media hours  (keep simple: only blank current)
        legal = set(int(x) for x in (g.get("legal_stations") or []) if str(x).isdigit())
        blanked = {b for b in blanked if b in legal or b == st}
        # force single media blank
        blanked = {st} if st else set()
        g["blanked_sit"] = sorted(blanked)

    g["media_source"] = source
    g["media_set_ts"] = time.time()
    g["ts"] = time.time()
    GEOM_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEOM_PATH.write_text(json.dumps(g, indent=2) + "\n")
    out = get_media_locus()
    out["wrote"] = True
    out["wall_warn"] = st in {4, 5, 6, 7} if st else False
    return out


def parse_init_utterance(text: str) -> dict[str, Any]:
    """Parse Crissa interview answer → mode + optional station."""
    t = (text or "").strip().lower()
    if not t:
        return {"ok": False, "error": "empty"}
    internal_cues = (
        "same computer",
        "same machine",
        "this computer",
        "no external",
        "no speakers",
        "internal",
        "just this",
        "only this machine",
        "built-in",
    )
    if any(c in t for c in internal_cues):
        return {"ok": True, "mode": "internal", "station": None}
    hour = parse_hour_utterance(t)
    if hour is not None:
        return {"ok": True, "mode": "external", "station": hour}
    if "desk" in t or "speaker" in t:
        return {"ok": True, "mode": "external", "station": DEFAULT_STATION}
    return {"ok": False, "error": "unparsed", "text": t[:80]}


def parse_hour_utterance(text: str) -> int | None:
    t = (text or "").strip().lower()
    if not t:
        return None
    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "desk": 9,
        "speakers": 9,
        "door": 12,
    }
    toks = t.replace(",", " ").split()
    for w, h in words.items():
        if w in toks or t == w:
            return h
    m = re.search(r"\b(1[0-2]|[1-9])\b", t)
    if m:
        return int(m.group(1))
    return None
