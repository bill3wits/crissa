"""The house hands its session to the phone (the User 2026-10-20 via .101: "default to house connection only if the QR
code is used ... able to connect directly on its own if session not transferred from house"). The desk Wall's QR card
asks the phone server for a single-use token (POST /api/handoff, loopback only) and encodes
http://<lan>:<port>/?handoff=<token>; the phone page claims it once (POST /api/handoff/claim) and is then on the house
connection. A page opened without one does not default to the house: it opens on its Connect card.

Tokens live in the phone server's memory only: single use, TTL_S, a restart forgets them (the card asks again each
time it opens). Kill: CRISSA_PHONE_HANDOFF=0 (the page is on the house always, as before).

A refused token says why (claim_why): "used" (it already connected a phone), "expired" (older than TTL_S), "unknown"
(not from this server, or the server restarted since the card showed it). Spent and expired tokens are remembered by
their hash for SPENT_KEEP_S, so the phone can say which.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import socket
import threading
import time

TTL_S = 180.0               # round 38 (the User: "can we close QR after a period?"): the card closes at ~2 min
MAX_OPEN = 32               # tokens not yet claimed; the oldest go first
SPENT_KEEP_S = 86400.0      # how long a used or expired token is still recognised, to say why it is refused
SPENT_MAX = 256
_LOCK = threading.Lock()
_OPEN: dict[str, float] = {}
_SPENT: dict[str, tuple[str, float]] = {}   # sha256(token) -> ("used" | "expired", when)

WHY = {
    "used": "That QR code was already used. Open Your phone in Crissa's menu on your computer for a new one.",
    "expired": "That QR code is more than 3 minutes old. Open Your phone in Crissa's menu on your computer for a new one.",
    "unknown": ("That QR code is not from this computer, or Crissa restarted since it was shown. Open Your phone in "
                "Crissa's menu on your computer for a new one."),
}


def _sha(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


def _spend(tok: str, why: str, now: float) -> None:
    _SPENT[_sha(tok)] = (why, now)
    for k in [k for k, (_, at) in _SPENT.items() if now - at > SPENT_KEEP_S]:
        del _SPENT[k]
    while len(_SPENT) > SPENT_MAX:
        del _SPENT[min(_SPENT, key=lambda k: _SPENT[k][1])]


def enabled() -> bool:
    return os.environ.get("CRISSA_PHONE_HANDOFF", "1").strip().lower() not in ("0", "false", "off", "no")


def lan_ip() -> str:
    """This computer's address on its home network ('' when it has none); the same reading as the menu's phone_view.
    A UDP socket is pointed at a private address to read the route's source; nothing is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as so:
            so.connect(("10.255.255.255", 9))
            ip = so.getsockname()[0]
    except OSError:
        return ""
    return "" if ip.startswith("127.") else ip


def mint(now: float | None = None) -> dict:
    """{"token", "expires"}: a fresh single-use token."""
    now = time.time() if now is None else now
    tok = secrets.token_urlsafe(16)
    with _LOCK:
        for k in [k for k, exp in _OPEN.items() if exp <= now]:
            _spend(k, "expired", now)
            del _OPEN[k]
        while len(_OPEN) >= MAX_OPEN:
            k = min(_OPEN, key=_OPEN.get)
            _spend(k, "expired", now)
            del _OPEN[k]
        _OPEN[tok] = now + TTL_S
    return {"token": tok, "expires": round(now + TTL_S, 3)}


def claim_why(token: str, now: float | None = None) -> str:
    """"ok" once for a token inside its time (it is spent either way), else "used", "expired" or "unknown"."""
    now = time.time() if now is None else now
    token = str(token or "")
    if not token:
        return "unknown"
    with _LOCK:
        hit = next((k for k in _OPEN if hmac.compare_digest(k, token)), None)
        if hit is None:
            was = _SPENT.get(_sha(token))
            return was[0] if was else "unknown"
        live = _OPEN.pop(hit) > now
        _spend(hit, "used" if live else "expired", now)
        return "ok" if live else "expired"


def claim(token: str, now: float | None = None) -> bool:
    """True once for a token inside its time; it is spent either way."""
    return claim_why(token, now) == "ok"
