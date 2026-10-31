"""She is on the phone (the User 2026-10-20, .101 ASK-20261020-0230: "her room is empty while she is on the phone"): while
a phone session is active, on any device, the desk Wall shows her empty office and walks her back in when it ends.

Active means the phone page or app is in use, never merely open: the talk floor taken (the button, a typed Send, voice
in, a chat), or her answer playing on the phone, each good for its own length plus IDLE_S. A page left open in a pocket
posts nothing, so it goes idle by itself; a page that closes or hides says so (POST /api/session {"end": true}) and the
session ends at once.

State: <state home>/phone-session.json, written only by the phone server (scripts/phone-app-server.py):
    {"v": 1, "active_until": epoch, "since": epoch, "last": epoch, "why": "floor|line|end", "ended": epoch|0, "idle_s": s}
The Wall's whole rule is `active_until > now`; a missing or unreadable file is not active. The idle window is folded in
here, so a reader needs no dial. Dials: CRISSA_PHONE_IDLE_S (180). Kill: CRISSA_PHONE_SESSION=0 (writes nothing).

THE PAGE'S HEARTBEAT (round 39, .101 round 38 N7, the User: "i closed phone and she did not return"): his iPhone sent no
end beacon when he closed the page, so she stayed away for the whole idle window. While visible the page beats every
10 s (POST /api/session {"here": true}); a beat is never a use, it only says the page is still there. While beats arrive
the session can last no longer than BEAT_S past the latest one, so a closed or hidden page ends it within BEAT_S even
when no end beacon arrives; a page left open and idle still ends at the idle window. A client that never beats (the
iPhone app today) is not capped. Both are folded into active_until, so the Wall still reads one number:
    "use_until": epoch    the uses' own end (last use + her line + the idle window)
    "beat_until": epoch   the latest beat + BEAT_S
Dial CRISSA_PHONE_BEAT_S (25). Kill CRISSA_PHONE_BEAT=0 (beats write nothing and cap nothing: the old rule).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

IDLE_S = 180.0
MAX_HOLD_S = 600.0
BEAT_S = 25.0               # the page beats every 10 s: two missed beats and a margin          # one use never keeps her out longer than this past the idle window
_LOCK = threading.Lock()


def enabled() -> bool:
    return os.environ.get("CRISSA_PHONE_SESSION", "1").strip().lower() not in ("0", "false", "off", "no")


def beat_on() -> bool:
    return os.environ.get("CRISSA_PHONE_BEAT", "1").strip().lower() not in ("0", "false", "off", "no")


def beat_s() -> float:
    try:
        return max(5.0, float(os.environ.get("CRISSA_PHONE_BEAT_S") or BEAT_S))
    except ValueError:
        return BEAT_S


def _beating(d: dict, now: float) -> bool:
    """Beats are arriving: the latest is no older than one BEAT_S window past its own (a client that never beat, or
    a page closed long ago, is not)."""
    try:
        return beat_on() and float(d.get("beat_until") or 0.0) >= now - beat_s()
    except (TypeError, ValueError):
        return False


def _fold(d: dict, now: float) -> None:
    """active_until = the uses' end, capped at the beats' end while beats arrive."""
    until = float(d.get("use_until") or d.get("active_until") or 0.0)
    if _beating(d, now):
        until = min(until, float(d.get("beat_until") or 0.0))
    d["active_until"] = round(until, 3)


def idle_s() -> float:
    try:
        return max(0.0, float(os.environ.get("CRISSA_PHONE_IDLE_S") or IDLE_S))
    except ValueError:
        return IDLE_S


def read(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def active(d: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    try:
        return float(d.get("active_until") or 0.0) > now
    except (TypeError, ValueError):
        return False


def _save(path: Path, d: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, path)


def use(path: Path, why: str = "floor", hold_s: float = 0.0, now: float | None = None) -> dict:
    """The phone is in use now, and stays busy `hold_s` (her line's length); the session is active until that plus
    the idle window. Never shortens a longer one. {} when switched off."""
    if not enabled():
        return {}
    now = time.time() if now is None else now
    hold_s = max(0.0, min(MAX_HOLD_S, float(hold_s or 0.0)))
    idle = idle_s()
    with _LOCK:
        d = read(path)
        if not active(d, now):
            d = {"v": 1, "since": round(now, 3), "active_until": 0.0, "use_until": 0.0, "ended": 0,
                 **({"beat_until": d["beat_until"]} if _beating(d, now) else {})}
        if _beating(d, now):                   # a use is the page alive now too
            d["beat_until"] = round(max(float(d.get("beat_until") or 0.0), now + beat_s()), 3)
        d.update({"last": round(now, 3), "why": str(why or "floor")[:20], "idle_s": idle, "ended": 0,
                  "use_until": round(max(float(d.get("use_until") or d.get("active_until") or 0.0),
                                         now + hold_s + idle), 3)})
        _fold(d, now)
        _save(path, d)
    return d


def beat(path: Path, now: float | None = None) -> dict:
    """The page is still open and visible. Never a use: it starts nothing and never lengthens a session; it moves the
    cap. {} when switched off."""
    if not (enabled() and beat_on()):
        return {}
    now = time.time() if now is None else now
    with _LOCK:
        d = read(path)
        d["beat_until"] = round(now + beat_s(), 3)
        if active(d, now):
            _fold(d, now)
        _save(path, d)
    return d


def end(path: Path, now: float | None = None) -> dict:
    """The page or app closed or hid: the session is over now. A session that is not active is left as it is."""
    if not enabled():
        return {}
    now = time.time() if now is None else now
    with _LOCK:
        d = read(path)
        if not active(d, now):
            return d
        d.update({"active_until": round(now, 3), "ended": round(now, 3), "why": "end"})
        _save(path, d)
    return d


def state(path: Path, now: float | None = None) -> dict:
    d = read(path)
    now = time.time() if now is None else now
    return dict(d, active=active(d, now), on=enabled(), beating=_beating(d, now))
