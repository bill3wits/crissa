"""This install's addresses for the presence module, and the camera switch (2026-10-10).

The module is the house's presence rail copied whole, and that rail spelled its neighbours as the house's own ports
(the camera grabber on 8192, the ear on 8190 / 8194, the feed on 8099, the player on 8100, itself on 8191). A pack
runs beside other installs (CRISSA_PORT_OFFSET), so on the trial of 2026-10-10 a pack's presence service sent
/v1/facelock and /v1/return_door to another install's camera. Every address here comes from the shared port table
(lib/crissa_ports), read inline because the module installs on its own; tests/test_crissa_loopback_guard.py holds the
copy equal to the table, rule for rule.

    port(name)            CRISSA_PORT_<NAME>, the older per-piece variable, else the default + CRISSA_PORT_OFFSET
    url(name, path="")    http://127.0.0.1:<port><path>
    camera(path="")       the room camera: RAIL_CAMERA_URL / CRISSA_CAMERA_URL when named, else this install's grabber
    camera_off()          CRISSA_NO_CAMERA=1 (the launcher's --no-camera) or RAIL_PRESENCE_CAMERA=off
    guard_camera()        with the camera off, no request from this process reaches a camera address (a backstop
                          under the explicit no-ops: it answers URLError("camera: off") and says so once)
    brain(path)           the describers' engine: RAIL_PRESENCE_VLM_URL / RAIL_OPS_URL, else a Connect endpoint on
                          this machine (<state home>/agent.json), else '' (the describers are off)
"""
from __future__ import annotations

import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

# THE SHARED PORT TABLE (lib/crissa_ports.DEFAULTS and LEGACY), repeated: this module installs on its own.
_DEFAULT_PORTS = {"player": 8100, "asr": 8096, "stt": 8190, "align": 8195, "tts": 8189, "oracle": 8197, "turbo": 8186,
                  "multilingual": 8185, "audio8": 8187, "pocket": 8188, "presence": 8219, "camera": 8192,
                  "enroll": 8196, "panel": 8097, "feed": 8099, "bridge": 8194, "comfy": 8184, "demo": 8124, "chip": 8218, "face_shell": 8088, "browser": 8183, "phone": 8226}
_LEGACY_PORTS = {"player": "HOUSE_CONDUCTOR_PORT", "align": "CRISSA_ALIGN_PORT", "tts": "CRISSA_TTS_PORT",
                 "oracle": "CRISSA_ORACLE_PORT"}
HOST = "127.0.0.1"
OFF = {"ok": False, "camera": "off"}          # what a camera call answers while the camera is off


def port(name: str, env=None) -> int:
    e = os.environ if env is None else env
    for k in ("CRISSA_PORT_" + name.upper(), _LEGACY_PORTS.get(name)):
        v = str(e.get(k) or "").strip() if k else ""
        if v.isdigit() and 0 < int(v) < 65536:
            return int(v)
    try:
        off = int(str(e.get("CRISSA_PORT_OFFSET") or "0").strip())
    except ValueError:
        off = 0
    return _DEFAULT_PORTS[name] + off


def url(name: str, path: str = "", env=None) -> str:
    return "http://%s:%d%s" % (HOST, port(name, env), path)


def camera(path: str = "", env=None) -> str:
    e = os.environ if env is None else env
    base = str(e.get("RAIL_CAMERA_URL") or e.get("CRISSA_CAMERA_URL") or "").strip().rstrip("/") or url("camera", env=e)
    return base + path


def camera_off(env=None) -> bool:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_NO_CAMERA") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return str(e.get("RAIL_PRESENCE_CAMERA") or "").strip().lower() in ("off", "none")


# every key the copied rail reads a camera address from (the launch script pins each; tests hold the list)
CAMERA_KEYS = ("RAIL_CAMERA_URL", "CRISSA_CAMERA_URL", "RAIL_CAMERA_HTTP", "RAIL_CAMERA_HEALTH", "RAIL_CAMERA_LEASE",
               "RAIL_CAMERA_LEASE_RELEASE", "RAIL_CAMERA_COVERAGE", "RAIL_DOOR_TRIGGERS", "RAIL_PRESENCE_SNAPSHOT_URL",
               "RAIL_PRESENCE_BUFFER_URL", "RAIL_PRESENCE_SWEEP_URL", "RAIL_PRESENCE_GRABBER_HEALTH",
               "RAIL_PRESENCE_GRABBER_AIM", "RAIL_PRESENCE_GRABBER_FACELOCK", "RAIL_PRESENCE_GRABBER_FACEFIND",
               "RAIL_PRESENCE_GRABBER_SMOOTH_SCAN", "RAIL_PRESENCE_GRABBER_LOOK_AROUND",
               "RAIL_PRESENCE_GRABBER_RETURN_DOOR", "RAIL_ORACLE_SNAPSHOT", "RAIL_ORACLE_FRAME", "RAIL_ORACLE_BUFFER",
               "RAIL_ORACLE_LOOK_AROUND", "RAIL_ORACLE_TRIGGERS", "RAIL_ORACLE_TRIGGER_POST", "RAIL_ORACLE_SMOOTH_SCAN")


def _netloc(u: str) -> str:
    try:
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return ""
    if p.scheme not in ("http", "https") or not p.hostname:
        return ""
    host = "127.0.0.1" if p.hostname in ("localhost", "::1") else p.hostname
    try:
        return "%s:%d" % (host, p.port or (443 if p.scheme == "https" else 80))
    except ValueError:
        return ""


def camera_netlocs(env=None) -> set[str]:
    """host:port of every camera address this process could read: the named keys and this install's grabber."""
    e = os.environ if env is None else env
    out = {_netloc(str(e.get(k) or "")) for k in CAMERA_KEYS} | {_netloc(camera(env=e)), _netloc(url("camera", env=e))}
    out.discard("")
    return out


_said = set()
_said_lock = threading.Lock()
REFUSED: list[str] = []                       # what the backstop stopped (path only), for tests and /health


def say_once(what: str) -> None:
    with _said_lock:
        if what in _said:
            return
        _said.add(what)
    print("[presence] camera: off - %s" % what, file=sys.stderr, flush=True)


def is_camera_request(target, env=None) -> bool:
    u = target.get_full_url() if isinstance(target, urllib.request.Request) else str(target or "")
    n = _netloc(u)
    return bool(n) and n in camera_netlocs(env)


_orig_urlopen = None


def guard_camera() -> bool:
    """With the camera off, wrap urllib.request.urlopen once so a request to a camera address never leaves the process.
    Idempotent; True when the guard is in place. With the camera on nothing is wrapped (behaviour unchanged)."""
    global _orig_urlopen
    if not camera_off():
        return False
    if _orig_urlopen is not None:
        return True
    _orig_urlopen = urllib.request.urlopen

    def urlopen(target, *args, **kwargs):
        if camera_off() and is_camera_request(target):
            u = target.get_full_url() if isinstance(target, urllib.request.Request) else str(target)
            path = urllib.parse.urlsplit(u).path or "/"
            REFUSED.append(path)
            say_once("a camera call was stopped before it left (first: %s; every later one too)" % path)
            raise urllib.error.URLError("camera: off")
        return _orig_urlopen(target, *args, **kwargs)

    urlopen.__wrapped__ = _orig_urlopen  # type: ignore[attr-defined]
    urllib.request.urlopen = urlopen
    return True


def brain(path: str = "/chat/completions") -> tuple[str, str]:
    """(url, model) of the engine the describers ask. The house asked its own engines by port (ops 8000, crew 8005, a
    side channel on 8197); a pack asks what the person named: RAIL_PRESENCE_VLM_URL / RAIL_OPS_URL, else the Connect
    endpoint (connect.py, <state home>/agent.json) when it is on this machine - a room picture never leaves the machine
    unless a variable names where it goes - else ('', '') and the describers skip. `path` replaces /chat/completions
    (the crew's /metrics, the engine's /models)."""
    named = (os.environ.get("RAIL_PRESENCE_VLM_URL") or os.environ.get("RAIL_OPS_URL") or "").strip()
    model = (os.environ.get("RAIL_PRESENCE_VLM_MODEL") or os.environ.get("RAIL_OPS_MODEL") or "").strip()
    if not named:
        try:
            from . import connect as _connect
            cfg = _connect.load()
            named = _connect.chat_url(dict(cfg, url=os.environ.get("CRISSA_AGENT_URL") or cfg.get("url")))
            model = model or os.environ.get("CRISSA_AGENT_MODEL", "").strip() or _connect.model(cfg)
        except Exception:  # noqa: BLE001  no Connect yet
            named = ""
        if urllib.parse.urlsplit(named).hostname not in ("127.0.0.1", "localhost", "::1"):
            return "", model
    if path != "/chat/completions" and named.endswith("/chat/completions"):
        named = named[: -len("/chat/completions")] + path
    return named, model
