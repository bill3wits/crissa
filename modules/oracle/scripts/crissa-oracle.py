#!/usr/bin/env python3
"""The oracle service: GET /v1/oracle says what the face check sees (2026-10-01).

    python3 modules/oracle/scripts/crissa-oracle.py --install     # once: fetch the models
    python3 modules/oracle/scripts/crissa-oracle.py               # serve on 127.0.0.1:8197
    python3 modules/oracle/scripts/crissa-oracle.py --port 8297   # or CRISSA_PORT_ORACLE / CRISSA_PORT_OFFSET

Loopback only. Point the Wall at it with CRISSA_ORACLE=http://127.0.0.1:8197. A taken port is one line, exit 2.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crissa_oracle import service, setup  # noqa: E402
from crissa_oracle import guard as _guard  # noqa: E402

# EVERY ROUTE IS THIS MACHINE'S (round 46, the local review of kit crissa-063a2813, 2026-10-23): /v1/oracle says who is
# in the room and where; any web page open in the buyer's browser could read it. The module's copy of the one loopback
# rule (crissa_oracle.guard, byte-equal to lib/crissa_guard.py) refuses a Host that is not this machine, a foreign
# Origin, and a cross-site mark with no loopback Referer; the player and curl pass. Kill: CRISSA_LOOPBACK_GUARD=0.


# THE SHARED PORT TABLE, READ INLINE (lib/crissa_ports, 2026-10-06 crissatest: this ignored --port-offset). This module
# installs on its own, so the three rules are repeated here: CRISSA_PORT_<NAME>, the older variable, else the
# default + CRISSA_PORT_OFFSET.
_DEFAULT_PORTS = {"player": 8100, "asr": 8096, "stt": 8190, "oracle": 8197}
_LEGACY_PORTS = {"player": "HOUSE_CONDUCTOR_PORT", "oracle": "CRISSA_ORACLE_PORT"}


def _port(name: str, env=None) -> int:
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


def _bind(name: str, port: int, handler, how: str = "--port N"):
    """The server on 127.0.0.1:port, or None after one clear line when the port is taken (never a traceback)."""
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        print("%s: port %d is already in use (%s). Another copy may be running: stop it, or pick another port "
              "with %s, CRISSA_PORT_%s=N or CRISSA_PORT_OFFSET=N." % (name, port, e.strerror or e, how,
                                                                     name.split("-")[-1].upper()),
              file=sys.stderr, flush=True)
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--install", action="store_true", help="fetch the models, then exit")
    ap.add_argument("--bundle-build", metavar="DIR", help="make an offline bundle of the models in DIR, then exit")
    ap.add_argument("--bundle-install", metavar="FILE", help="install the models from a bundle (no network), then exit")
    ap.add_argument("--port", type=int, default=_port("oracle"))
    ap.add_argument("--mode", default=os.environ.get("CRISSA_ORACLE_MODE", "ff"))
    ap.add_argument("--no-camera", action="store_true", help="open no capture device (CRISSA_NO_CAMERA=1; the launcher's "
                    "--no-camera passes it): the service still answers, camera false")
    a = ap.parse_args(argv)
    if a.no_camera:
        os.environ["CRISSA_NO_CAMERA"] = "1"
    if a.install:
        print(json.dumps(setup.install()))
        return 0
    if a.bundle_build:
        print(setup.build_bundle(Path(a.bundle_build)))
        return 0
    if a.bundle_install:
        print(json.dumps(setup.install_bundle(Path(a.bundle_install))))
        return 0
    py = setup.face_env_python()
    if py is not None:                      # name faces in the presence module's env (setup.face_env_python)
        print("crissa-oracle: naming faces with AuraFace in the presence module's Python (%s)" % py, file=sys.stderr,
              flush=True)
        env = dict(os.environ, CRISSA_ORACLE_AURAFACE="1", **{setup.FACE_ENV_FLAG: "1"})
        os.execve(str(py), [str(py), str(Path(__file__).resolve())] + list(sys.argv[1:] if argv is None else argv), env)
    orc = service.Oracle(wanted_mode=a.mode)
    print("crissa-oracle: face engine %s; names faces: %s" % (service.face.engine_reason() or "ready",
                                                             setup.recogniser_state()), file=sys.stderr, flush=True)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if not _guard.check(self):
                return
            if self.path.split("?", 1)[0] != "/v1/oracle":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(orc.state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = _bind("crissa-oracle", a.port, H)
    if srv is None:
        return 2
    threading.Thread(target=orc.run, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        orc.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
