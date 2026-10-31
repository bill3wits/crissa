"""Every route is this machine's, not the web's: one loopback guard for every server of a pack (round 46).

The player has refused the web since the ship review of 2026-10-07 (scripts/house-conductor.py foreign_request). Its
sibling servers never got that guard (the local review of kit crissa-063a2813, 2026-10-23): any web page open in the
buyer's browser could reach 127.0.0.1 and read the room camera (`Access-Control-Allow-Origin: *` on the grabber's
mjpeg), replace the owner's face, read the ear's turns, or drive Setup through the feed. This is that guard, one
rule, stdlib only, so a module that installs on its own carries a byte-equal copy (tests/test_round46_loopback_guard
holds every copy equal to this file):

  * Host must name this machine (127.0.0.1, localhost, ::1, or a name the server adds): a page that rebinds its own
    domain to 127.0.0.1 is same-origin and sends no Origin, but its Host is still its own name.
  * A present Origin must be loopback. Every browser sends Origin on a cross-origin POST, form posts and other
    "simple" requests included, so a cross-site write is refused before any route reads its body.
  * A request the browser marks `Sec-Fetch-Site: cross-site` with no Origin (a no-cors GET: an <img> or <video> on
    another site pointed at the camera) needs a loopback Referer.
  * No response carries `Access-Control-Allow-Origin: *`; allow_origin() answers a loopback Origin with itself, so this
    machine's own pages on another port still read across ports.

curl, the launcher, the ear and the player's own calls send none of those headers and pass. Kill:
CRISSA_LOOPBACK_GUARD=0 (a server may give its own default: the house's own servers keep today's way until it is
flipped there).
"""
from __future__ import annotations

import os

LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")


def guard_on(env=None, default: str = "1") -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_LOOPBACK_GUARD", default) or default).strip().lower() not in ("0", "false", "no", "off")


def loopback_hostport(hostport: str, names=()) -> bool:
    """`host`, `host:port`, `[::1]` or `[::1]:port` names this machine (or one of `names`)."""
    h = str(hostport or "").strip().lower()
    if h.startswith("["):
        h = h[1:].split("]", 1)[0]
    elif h.count(":") == 1:
        h = h.rsplit(":", 1)[0]
    return h in LOOPBACK_NAMES or h in tuple(str(n).lower() for n in names or ())


def loopback_url(url: str, names=()) -> bool:
    """`scheme://host[:port]/...` (an Origin or a Referer) names this machine."""
    return loopback_hostport(str(url or "").split("://", 1)[-1].split("/", 1)[0], names)


def foreign_request(headers, names=()) -> str:
    """Why a request did not come from this machine: 'host', 'origin', 'cross-site'; '' = it did."""
    host = str(headers.get("Host") or "").strip()
    if host and not loopback_hostport(host, names):
        return "host"
    origin = str(headers.get("Origin") or "").strip()
    if origin:
        return "" if origin != "null" and loopback_url(origin, names) else "origin"
    if str(headers.get("Sec-Fetch-Site") or "").strip().lower() == "cross-site":
        ref = str(headers.get("Referer") or "").strip()
        return "" if ref and loopback_url(ref, names) else "cross-site"
    return ""


def allow_origin(headers) -> str:
    """The value for Access-Control-Allow-Origin: the request's own Origin when it is this machine's, else ''."""
    origin = str(headers.get("Origin") or "").strip()
    return origin if origin and origin != "null" and loopback_url(origin) else ""


def refuse(handler, why: str) -> None:
    """403 with a short JSON body and no CORS header (the page that asked cannot read it). The connection closes: the
    refused request's body is never read, so a kept-alive connection would take it for the next request."""
    body = ('{"ok": false, "error": "this machine only (%s)"}' % why).encode("utf-8")
    handler.close_connection = True
    try:
        handler.send_response(403)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)
    except OSError:
        pass


def check(handler, names=(), env=None, default: str = "1") -> bool:
    """True when the request may go on; else refuses it (403) and returns False. Call first in every do_*."""
    if not guard_on(env, default):
        return True
    why = foreign_request(handler.headers, names)
    if not why:
        return True
    refuse(handler, why)
    return False


def body_length(headers, cap: int | None = None) -> int | None:
    """The Content-Length to read: 0 when absent; None when it is not a number, negative (rfile.read(-1) reads until
    the peer closes) or over `cap` (the server's own size for its biggest body)."""
    raw = str(headers.get("Content-Length") or "").strip()
    if not raw:
        return 0
    try:
        n = int(raw)
    except ValueError:
        return None
    if n < 0 or (cap is not None and n > cap):
        return None
    return n
