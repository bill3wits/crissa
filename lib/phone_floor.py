"""The phone has the floor, or the desk does: one or the other, never both (the User 2026-10-18 18:2x, "sounds right to
me to have one or the other from that button"). On .101 the desk Crissa answered while he talked to her on the phone:
the room's ear heard him, or her phone voice, and took it as a turn at the desk.

The phone's talk button takes the floor (the native app's push to talk or open mic, the web app's Send) and holds it
until her answer has finished playing on the phone; while it is held the desk ear drops what it hears before decoding it
(modules/ear/crissa_ear/veto_first.phone_floor_held reads this file). A holder that goes quiet loses the floor by
itself: every take or refresh is good for TTL_S, so a phone that walks off the Wi-Fi or a closed app never leaves the
desk deaf for longer than that.

State: <state home>/phone-floor.json  {"on": bool, "since": epoch, "until": epoch, "by": str}
`until` is when the floor stops being the phone's: a refresh moves it on, a release sets it TAIL_S from now (the ear
flushes an utterance up to ~2 s after its last word, so his last word is still the phone's). Kill: CRISSA_PHONE_FLOOR=0
(the server writes nothing; the ear's own kill is CRISSA_EAR_PHONE_FLOOR=0).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

TTL_S = 6.0
TAIL_S = 1.0
MAX_HOLD_S = 120.0          # a hold for a line is its length plus a little; never longer than this
_LOCK = threading.Lock()


def enabled() -> bool:
    return os.environ.get("CRISSA_PHONE_FLOOR", "1").strip().lower() not in ("0", "false", "off", "no")


def read(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, d: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, path)


def held(d: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    try:
        return bool(d.get("on")) and float(d.get("until") or 0.0) > now
    except (TypeError, ValueError):
        return False


def take(path: Path, for_s: float = TTL_S, by: str = "phone", now: float | None = None, line: bool = False) -> dict:
    """The phone has the floor for at least `for_s` from now (a take or a refresh; never shortens a longer hold).
    `line`: her line plays on the phone for that long; `line_since` / `line_until` say so apart from the button, for the
    ear's phone mic (modules/ear/crissa_ear/phone_mic.py: while the phone's mic is open only her line drops the desk)."""
    now = time.time() if now is None else now
    for_s = max(0.0, min(MAX_HOLD_S, float(for_s)))
    with _LOCK:
        d = read(path)
        keep = {k: d[k] for k in ("line_since", "line_until") if k in d}
        if not held(d, now):
            d = dict({"on": True, "since": round(now, 3), "until": 0.0, "by": str(by or "phone")[:40]}, **keep)
        d["until"] = round(max(float(d.get("until") or 0.0), now + for_s), 3)
        if line:
            if float(d.get("line_until") or 0.0) <= now:
                d["line_since"] = round(now, 3)
            d["line_until"] = round(max(float(d.get("line_until") or 0.0), now + for_s), 3)
        _save(path, d)
    return d


def release(path: Path, now: float | None = None) -> dict:
    """The desk has the floor again, TAIL_S from now. A floor that is not held is left as it is."""
    now = time.time() if now is None else now
    with _LOCK:
        d = read(path)
        if not held(d, now):
            return d
        d["until"] = round(now + TAIL_S, 3)
        d["on"] = True                       # still the phone's through the tail; held() turns it over at `until`
        _save(path, d)
    return d
