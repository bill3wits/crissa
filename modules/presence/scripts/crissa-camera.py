#!/usr/bin/env python3
"""The room camera (modules/presence, 2026-10-09): ONE process opens the camera and serves JPEG + a ring buffer -
the house's grabber (crissa_presence/grabber.py), extracted. The presence service, the oracle's door feed and the
registration panel read it; nothing else opens the device.

    python3 modules/presence/scripts/crissa-camera.py [--port N] [--device 0]

    GET  /health  /snapshot  /v1/mjpeg  /v1/buffer  /v1/frame  /v1/energy  /v1/firmware ...
    POST /v1/aim  /v1/pause  /v1/resume  /v1/lease  /v1/sweep  /v1/look_around  /v1/facefind ...

PTZ needs v4l2-ctl (Linux); without it the camera serves stills and the aim routes answer that they cannot move.
Loopback only. Installed by crissa-presence.py --install (same env).
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


def _port(env=None) -> int:
    return _P.port("camera", env)


# THE HOUSE'S CAMERA RATE (the User 2026-10-31, after Secure never ended on his walk-in on crissatest: "refer to working
# code"). The house's door camera runs at 15 fps with a 600-frame ring (rail-camera.service drop-ins fps.conf +
# buffer); the kit ran 30 fps / 150. The door-motion trigger is the same code with the same 8.0 threshold on the
# frame-to-frame difference, so at 30 fps each difference is about half as large and a walk-in can stay under it: the
# kit's camera posted no trigger for his walk at 01:28:47 (face 72-151 px, plainly in frame). An env value still wins.
# Kill switch: CRISSA_CAMERA_HOUSE_RATE=0 (30 fps / 5 s as before).
HOUSE_RATE = (
    {"RAIL_CAMERA_HZ": "15", "RAIL_CAMERA_BUFFER_SEC": "20", "RAIL_CAMERA_BUFFER_LEN": "600"}
    if os.environ.get("CRISSA_CAMERA_HOUSE_RATE", "1").strip() != "0"
    else {"RAIL_CAMERA_HZ": "30", "RAIL_CAMERA_BUFFER_SEC": "5"}
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Crissa camera: the one process that opens the room camera")
    ap.add_argument("--port", type=int, default=_port())
    ap.add_argument("--device", default=os.environ.get("RAIL_CAMERA_DEVICE", "0"))
    ap.add_argument("--hz", default=os.environ.get("RAIL_CAMERA_HZ", HOUSE_RATE["RAIL_CAMERA_HZ"]))
    a = ap.parse_args(argv)
    if _P.camera_off():                           # --no-camera / CRISSA_NO_CAMERA=1: nothing opens it (2026-10-10)
        print("crissa-camera: camera: off (CRISSA_NO_CAMERA=1); not opening the camera", file=sys.stderr)
        return 0
    from crissa_presence import setup as S
    if not S.installed():
        print("crissa-camera: not installed yet; run crissa-presence.py --install first", file=sys.stderr)
        return 2
    S.reexec_in_env([__file__] + list(sys.argv[1:] if argv is None else argv))
    os.environ["RAIL_CAMERA_PORT"] = str(a.port)
    os.environ["RAIL_CAMERA_DEVICE"] = str(a.device)
    os.environ.setdefault("RAIL_CAMERA_HZ", str(a.hz))
    for k, v in HOUSE_RATE.items():
        if k != "RAIL_CAMERA_HZ":
            os.environ.setdefault(k, v)
    os.environ.setdefault("RAIL_CAMERA_JPEG_Q", "75")
    os.environ.setdefault("RAIL_CAMERA_STALE_SEC", "2")
    from crissa_presence import grabber
    grabber.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
