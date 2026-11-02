"""Secure is one token, passed across Oracle restarts.

the User 2026-09-08: the camera lease is a token they pass around; do the same
for Secure. Disk sage is not a restore (``load_secure()`` stays False). If
they were Secure, the token rides on disk. The next process FaceFinds the
desk:

* hit  → sage (the button goes green) + AUTH held — never keyboard Hold:
  ``hold`` rides in the token as audit only; Hold is this process, this
  click, this password (BUTTON-MODE-MATRIX 2.4, ruling 2026-09-09)
* miss → Guest, token gone

Occupancy switch while presence is still up keeps the live flag (Oracle did
not go down). A door tripwire still drops Secure and burns the token.

This module is the decision only: no I/O, no clock, no camera.
"""
from __future__ import annotations

import secrets
from typing import Any


def mint(
    uid: str,
    *,
    now: float,
    reason: str,
    hold: bool = False,
    token_id: str | None = None,
) -> dict[str, Any]:
    """A new token, or the same id handed to the next process."""
    tid = str(token_id or "").strip() or secrets.token_hex(16)
    return {
        "id": tid,
        "uid": str(uid or "").strip().lower(),
        "hold": bool(hold),
        "ts": float(now),
        "reason": str(reason or "click"),
    }


def valid(token: dict | None, uid: str) -> bool:
    if not isinstance(token, dict):
        return False
    tid = str(token.get("id") or "").strip()
    if not tid:
        return False
    got = str(token.get("uid") or "").strip().lower()
    want = str(uid or "").strip().lower()
    return bool(got and want and got == want)


def decide(token: dict | None, uid: str) -> dict[str, Any]:
    """What boot / occupancy does with a disk token.

    act: reauth — FaceFinder the desk; sage on hit, Guest on miss
         guest  — no token (or it is not theirs): stay Guest until a click
    """
    if not valid(token, uid):
        why = "no_token" if not token else "token_uid"
        return {"act": "guest", "why": why, "hold": False, "id": None}
    return {
        "act": "reauth",
        "why": "token",
        "hold": bool(token.get("hold")),
        "id": str(token.get("id") or ""),
    }
