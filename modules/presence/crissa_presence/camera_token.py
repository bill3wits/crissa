"""The camera lease is ONE TOKEN, passed hand to hand.

the User 2026-09-07: "Camera lease won't yield … make the lease a token they pass
around" — the Oracle joystick lost to a session look or a Secure glance mid-
dwell, and nothing told the holder to let go. Every aimer used to take its own
:8192 lease; the grabber refuses the next asker until expiry.

Now presence holds exactly one token — the grabber's ``lease_id`` — and every
aimer asks the broker (``PresenceService.cam_take / cam_renew /
cam_give_back``). This module is the decision only: no I/O, no clock.

Ladder (first outranks all): the User's joystick, then Register (behind RAIL_REGISTER_TOKEN), then the Secure door watch
(Law 2: under Secure the camera stays on the door), then the FF-mode chase,
then session looks, then the door mill. A higher asker takes the token from a
lower holder at once — the grabber re-owns the same lease_id — and the loser's
loop sees ``holder != me`` on its next tick and stops (yielding is passive).
An equal or lower asker is refused with the holder's name. A holder that is
not on the ladder (the inspector, a tool pause) is external: refused unless
the asker is the joystick and says ``steal`` (the User is the arbiter).
"""
from __future__ import annotations

import os
from typing import Any

LADDER: tuple[str, ...] = (
    "user_joystick",
    "secure_glance",
    "ff_mode",
    "beam_look",
    "f2f",
    "seat_shot",
    "door_mill",
)


# Register (the User 2026-10-02, Init step 3): Register and Init's room sweep / Crissa-seat look take the camera as
# `register`, below the User's joystick and above Secure's door watch, so a Register under Secure is not yanked back
# to the door. RAIL_REGISTER_TOKEN=1 puts it on the ladder; =0 takes it off (refused).
# UNSET IS ON IN A PACK (the User 2026-10-10, .101): the coach's own switch (register_camera.on) was already on in a
# pack by default, and this one was off unless the launcher handed it over - which its service units did not, so every
# Voice ID on a real camera was refused. Both sides now read the same rule; the house keeps its own (off unless set).
REGISTER = "register"


def register_on() -> bool:
    got = os.environ.get("RAIL_REGISTER_TOKEN", "").strip().lower()
    if got:
        return got in ("1", "on", "true", "yes")
    from . import paths as _paths
    return not _paths.is_house()


def register_aim_on() -> bool:
    """Init v2 (the User 2026-10-04): Register's aim / renew ops and the verified home on give_back.
    Off by default: the new ops answer 404 {"error": "off"} and take / give_back are exactly as before."""
    return os.environ.get("RAIL_REGISTER_AIM", "").strip().lower() in ("1", "on", "true", "yes")


def ladder() -> tuple[str, ...]:
    return (LADDER[0], REGISTER, *LADDER[1:]) if register_on() else LADDER


def rank(owner: str | None) -> int:
    """Position on the ladder (0 = highest); -1 = not ours (external)."""
    o = str(owner or "").strip()
    try:
        return ladder().index(o)
    except ValueError:
        return -1


def is_ours(owner: str | None) -> bool:
    return rank(owner) >= 0


def outranks(asker: str | None, holder: str | None) -> bool:
    """True when ``asker`` may take the token from ``holder`` without asking."""
    ra, rh = rank(asker), rank(holder)
    if ra < 0 or rh < 0:
        return False
    return ra < rh


def expired(token: dict | None, now: float) -> bool:
    if not token:
        return True
    try:
        return float(token.get("until") or 0.0) <= float(now)
    except (TypeError, ValueError):
        return True


def decide(asker: str, token: dict | None, now: float, *, steal: bool = False) -> dict[str, Any]:
    """What the broker does for ``asker``.

    act: grant   — nobody holds it (or it expired): take a fresh lease
         renew   — the asker already holds it
         handoff — a lower holder loses it; the same lease_id is re-owned
         steal   — an external holder is paused off (joystick only, on request)
         refuse  — the holder outranks (or equals) the asker
    """
    a = str(asker or "").strip()
    if not token or expired(token, now):
        return {"act": "grant", "holder": None, "why": "free"}
    holder = str(token.get("owner") or "").strip() or None
    if holder == a:
        return {"act": "renew", "holder": holder, "why": "mine"}
    if not is_ours(holder):
        if steal and a == "user_joystick":
            return {"act": "steal", "holder": holder, "why": "external:stolen"}
        return {"act": "refuse", "holder": holder, "why": "external"}
    if outranks(a, holder):
        return {"act": "handoff", "holder": holder, "why": "outranks"}
    return {"act": "refuse", "holder": holder, "why": "held"}


# A call made under the register token (Init's room sweep, its Crissa-seat look) aims with the token's lease instead of
# taking and releasing its own: the helpers that lease the camera ask borrowed() first.
import contextlib as _ctx
import threading as _th

_BORROW_LOCK = _th.Lock()
_BORROWED: list[str] = []


def borrowed() -> str | None:
    with _BORROW_LOCK:
        return _BORROWED[-1] if _BORROWED else None


@_ctx.contextmanager
def borrow(lease_id: str | None):
    if not lease_id:
        yield None
        return
    with _BORROW_LOCK:
        _BORROWED.append(lease_id)
    try:
        yield lease_id
    finally:
        with _BORROW_LOCK:
            if lease_id in _BORROWED:
                _BORROWED.remove(lease_id)
