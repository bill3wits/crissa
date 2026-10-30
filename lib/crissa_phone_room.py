"""Her room is empty while she is on the phone (round 36, the User on .101 2026-10-20, ASK-20261020-0230).

"phone popup did not halt the second crissa renders": while a phone session is active, on any device, the desk Wall and
her render tile show her empty office - one Crissa. Unpaused she walks out on her own walk-off film; paused it is a cut
to the empty office. When the session ends or goes idle she walks back in on her entrance (as after Setup).

The session is the phone server's (crissa-3e): <state home>/phone-session.json, written only there, active while
`active_until` is in the future. The idle window is already folded into active_until (CRISSA_PHONE_IDLE_S, 180 s on the
phone side), so this reads one number and keeps no clock of its own. A missing or unreadable file is no session.

This module decides; the player (scripts/house-conductor.py, _phone_room_loop) plays. A pack only, never during Setup.
Kill CRISSA_PHONE_EMPTY_ROOM=0.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

try:
    import crissa_home
except ImportError:  # imported by path from a test or a tool
    crissa_home = None

WALK_OFF = "crissa-walk-off"          # the kit's crissa-walk-off (the rename carries it)
TICK_S = 1.0


def on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    if crissa_home is not None and crissa_home.is_house():
        return False
    return str(e.get("CRISSA_PHONE_EMPTY_ROOM", "1")).strip().lower() not in ("0", "off", "false", "no")


def session_path(env: dict | None = None) -> Path:
    e = os.environ if env is None else env
    own = str(e.get("CRISSA_PHONE_SESSION") or "").strip()
    if own:
        return Path(own).expanduser()
    home = getattr(crissa_home, "HOME", None) or Path(e.get("CRISSA_HOME") or (Path.home() / ".crissa"))
    return Path(home) / "phone-session.json"


def active(path: Path | None = None, now: float | None = None) -> bool:
    """A phone session is on now: active_until in the future. Anything else (no file, a bad one) is no session."""
    try:
        d = json.loads(Path(path or session_path()).read_text(encoding="utf-8"))
        until = float(d.get("active_until") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    return until > (time.time() if now is None else float(now))


def card_on(env: dict | None = None) -> bool:
    """The menu's "Your phone" is a QR card (lib/crissa_phone_card). Kill CRISSA_PHONE_QR_CARD=0 (round 35's address)."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PHONE_QR_CARD", "1")).strip().lower() not in ("0", "off", "false", "no")


def popup_on(env: dict | None = None) -> bool:
    """The phone page in a phone-shaped window on this computer (crissa_window.open_phone): a second Crissa on the same
    screen, so OFF unless CRISSA_PHONE_POPUP=1 (a test). When on it is a phone session like any other (the page writes
    the phone server's session file), so her room empties under it."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PHONE_POPUP", "0")).strip().lower() in ("1", "on", "true", "yes")


# NO SECOND WALK WITHIN 10 S (round 37, the User via .101 round 36 B: "put a no repeat"): a change that comes within
# MIN_GAP_S of the last walk out or in waits until the gap has passed (the session's own state still decides then).
MIN_GAP_S = 10.0


class Room:
    """Out or in. step() answers what to do on a change and nothing otherwise."""

    def __init__(self):
        self.out = False
        self.how = ""
        self.last = None                    # time.time() of the last walk out or in

    def step(self, phone: bool, paused: bool, setup: bool, now: float | None = None) -> tuple[str, str] | None:
        """("out", "walked out" | "cut, paused"), ("back", "walk-in"), or None (nothing changes)."""
        if setup:
            return None                     # Setup owns the picture; a phone session waits for it
        t = time.time() if now is None else float(now)
        if phone != self.out and self.last is not None and t - self.last < MIN_GAP_S:
            return None                     # too soon after the last walk: asked again on the next tick
        if phone and not self.out:
            self.out, self.how, self.last = True, ("cut, paused" if paused else "walked out"), t
            return ("out", self.how)
        if not phone and self.out:
            self.out, self.how, self.last = False, "", t
            return ("back", "walk-in")
        return None


def line(act: tuple[str, str]) -> str:
    """The log line .101 greps."""
    if act[0] == "out":
        return "on the phone: her room empty (%s)" % act[1]
    return "back from the phone: %s" % act[1]
