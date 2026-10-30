#!/usr/bin/env python3
"""The presence service (modules/presence, 2026-10-09): who is in the room, Secure and Guest, the door watch,
the visit lamp, registration tokens, the room sweep - the house's rail, extracted.

    python3 modules/presence/scripts/crissa-presence.py --install     # the env from the hash lock + the face models
    python3 modules/presence/scripts/crissa-presence.py [--port N]    # re-runs itself in the module's env

    GET  /health  /v1/presence  /v1/visit_lamp  /v1/room_events ...   (crissa_presence/server.py has the whole tree)
    POST /v1/visit_lamp {op}  /v1/see {embedding, source}  /v1/enroll {owner, faces}  /v1/vl_delta  /v1/inspector ...

The camera is the grabber child (crissa-camera.py, RAIL_PRESENCE_SNAPSHOT_URL); the port follows the shared table
(CRISSA_PORT_PRESENCE, CRISSA_PORT_OFFSET). Loopback only. State under <state home>/presence (crissa_presence/paths.py).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

def _load_ports():
    """The module's inline copy of the shared port table (crissa_presence/ports.py), by its path: a process may hold
    another module under the package's name (the player's lib/crissa_presence lamp), and this script needs only the
    table (2026-10-10)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("crissa_presence_launch_ports", HERE / "crissa_presence" / "ports.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_P = _load_ports()


def _port(name: str, env=None) -> int:
    return _P.port(name, env)


# BESIDE ANOTHER INSTALL (the User 2026-10-10). The service is the reference install's presence rail copied whole, and
# its other addresses default to that install's own ports: the camera grabber's aim / facelock / return_door / lease on
# 8192, the ear on 8190 / 8194, the feed on 8099, the player on 8100, the presence base on 8191. A port offset moved
# this service's own port and left those defaults where they were, so on the trial of 2026-10-10 (this pack beside the
# reference install, host network) the service's door-first start sent /v1/facelock and /v1/return_door to that
# install's real camera. Every address the running service reads is pinned here to THIS install's port table before
# the service loads; a value already in the environment still wins (a camera elsewhere, CRISSA_CAMERA_URL).
def pinned_addresses(cam: str, env=None) -> dict:
    u = lambda name, path="": _P.url(name, path, env)  # noqa: E731
    cam = cam.rstrip("/")
    return {
        "RAIL_PRESENCE_SNAPSHOT_URL": cam + "/snapshot", "RAIL_CAMERA_URL": cam, "CRISSA_CAMERA_URL": cam,
        "RAIL_CAMERA_HTTP": cam, "RAIL_CAMERA_HEALTH": cam + "/v1/health", "RAIL_DOOR_TRIGGERS": cam + "/v1/triggers",
        "RAIL_CAMERA_LEASE": cam + "/v1/lease", "RAIL_CAMERA_LEASE_RELEASE": cam + "/v1/lease/release",
        "RAIL_PRESENCE_GRABBER_HEALTH": cam + "/health", "RAIL_PRESENCE_GRABBER_AIM": cam + "/aim",
        "RAIL_PRESENCE_GRABBER_FACELOCK": cam + "/v1/facelock", "RAIL_PRESENCE_GRABBER_FACEFIND": cam + "/v1/facefind",
        "RAIL_PRESENCE_GRABBER_SMOOTH_SCAN": cam + "/v1/smooth_scan",
        "RAIL_PRESENCE_GRABBER_LOOK_AROUND": cam + "/v1/look_around",
        "RAIL_PRESENCE_GRABBER_RETURN_DOOR": cam + "/v1/return_door",
        "RAIL_ORACLE_SNAPSHOT": cam + "/snapshot", "RAIL_ORACLE_FRAME": cam + "/v1/frame",
        "RAIL_ORACLE_BUFFER": cam + "/v1/buffer", "RAIL_ORACLE_LOOK_AROUND": cam + "/v1/look_around",
        "RAIL_ORACLE_TRIGGERS": cam + "/v1/triggers", "RAIL_ORACLE_TRIGGER_POST": cam + "/v1/trigger",
        "RAIL_PRESENCE_BASE": u("presence"), "RAIL_ORACLE_VISIT": u("presence", "/v1/oracle_visit"),
        "RAIL_ROOM_BUSY": u("presence", "/v1/room_busy"),
        "RAIL_ORACLE_STT": u("stt", "/stt"), "RAIL_STT_URL": u("stt"), "RAIL_OMNI_URL": u("stt", "/v1/omni"),
        "RAIL_STT_OMNI_NOTE": u("stt", "/v1/omni/note"), "RAIL_EAR_HEALTH_URL": u("bridge", "/health"),
        "VO_HOME_URL": u("feed"), "HOUSE_CONDUCTOR_URL": u("player"),
    }


# THE HOUSE'S ORACLE SWITCHES TRAVEL WITH THE SERVICE (round 16, 2026-10-13, .101: presence at 1.83 cores with the
# User seated and the door watch settled). The service is the house's rail copied whole, but the house runs it with a
# drop-in (rail-presence.service.d/ops-vram.conf, 2026-08-01: "gemma/cosmos scrubbed") that sets RAIL_ORACLE_BENCH=0,
# RAIL_ORACLE_GPU=0 and RAIL_ORACLE_MODE=off, and the copy's own defaults are on. So every pack ran the old A/B/C
# Oracle bench inside presence: every 2 s it fetched 60 buffered frames, decoded 13, ran the face landmarker AND the
# recognizer on each, wrote a strip and a pack file, and posted a face trigger that woke the door glance - whatever the
# door watch's rest or settle said, and read by nothing in a pack (the fixed camera's face is the oracle service's
# look). Measured in the sandbox: oracle-bench 0.63 + its native threads 0.78 + oracle-strip-live 0.10 of 1.82 cores.
# A value already in the environment still wins. Kill: CRISSA_PRESENCE_ORACLE_BENCH=1 (the copy's defaults, bench on).
HOUSE_PRESENCE_SWITCHES = {"RAIL_ORACLE_BENCH": "0", "RAIL_ORACLE_GPU": "0", "RAIL_ORACLE_MODE": "off"}


def house_switches(env=None) -> dict:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_PRESENCE_ORACLE_BENCH", "")).strip().lower() in ("1", "on", "true", "yes"):
        return {}
    return dict(HOUSE_PRESENCE_SWITCHES)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Crissa presence: who is in the room")
    ap.add_argument("--port", type=int, default=_port("presence"))
    ap.add_argument("--camera", default="", help="the grabber's address (default: the port table's camera)")
    ap.add_argument("--install", action="store_true", help="build the env from the lock and fetch the face models, then exit")
    ap.add_argument("--no-camera", action="store_true", default=_P.camera_off(),
                    help="no camera: every camera call is a no-op that answers camera: off (CRISSA_NO_CAMERA=1)")
    a = ap.parse_args(argv)
    if a.no_camera:
        os.environ["CRISSA_NO_CAMERA"] = "1"      # before the re-exec, so the module's env carries it (2026-10-10)
    from crissa_presence import setup as S
    if a.install:
        why = S.unsupported()
        if why:
            print("crissa-presence: " + why, file=sys.stderr)
            return 2
        S.install()
        return 0
    if not S.installed():
        print("crissa-presence: not installed yet; run with --install first", file=sys.stderr)
        return 2
    S.reexec_in_env([__file__] + list(sys.argv[1:] if argv is None else argv))
    cam = a.camera or os.environ.get("RAIL_CAMERA_URL") or _P.url("camera")
    # NO CAMERA (2026-10-10): the camera backend is "off", so the door-first start does not aim and no call reaches a
    # camera (crissa_presence/ports.py); the pinned addresses below stay this install's own all the same
    os.environ.setdefault("RAIL_PRESENCE_CAMERA", "off" if _P.camera_off() else "http")
    for key, value in pinned_addresses(cam).items():
        os.environ.setdefault(key, value)
    for key, value in house_switches().items():
        os.environ.setdefault(key, value)
    os.environ.setdefault("RAIL_PRESENCE_DOOR_FIRST", "1")
    os.environ.setdefault("RAIL_PRESENCE_USE_BUFFER", "1")
    os.environ.setdefault("RAIL_PRESENCE_PORT", str(a.port))
    from crissa_presence import server
    server.main(port=a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
