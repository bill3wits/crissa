#!/usr/bin/env python3
"""The id module: Init and enrolment, the house's house-id tools on a Crissa install (2026-10-09).

    python3 modules/id/scripts/crissa-id.py --install              # the id env from the hash lock + the face models
    python3 modules/id/scripts/crissa-id.py --run [--port 8196]    # the enroll coach, when no presence module serves one
    python3 modules/id/scripts/crissa-id.py --status               # what is installed, the identity store, the engine
    python3 modules/id/scripts/crissa-id.py -- <command> ...       # the enrol CLI: doctor, status, person add, voice,
                                                                   # voice-register, face, room-refs, init, add-regular

Init itself is served by the player on /v1/init (lib/crissa_init routes to crissa_id.init_wizard); the Wall walks it.
The coach is normally the presence module's child on the enroll port (modules/presence/scripts/crissa-enroll.py);
--run is for an install without that module. Loopback only. A taken port is one line, exit 2.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

# THE SHARED PORT TABLE, READ INLINE (lib/crissa_ports): the module installs on its own, so the rules are repeated.
_DEFAULT_PORTS = {"enroll": 8196}
_LEGACY_PORTS = {"enroll": "ENROLL_UI_PORT"}


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


def status() -> dict:
    from crissa_id import face_engine, paths, setup
    from crissa_id.house_enroll import person
    root = paths.identity_root()
    try:
        people = [p.get("id") for p in person.list_people(root)]
    except Exception:  # noqa: BLE001
        people = []
    rec = {}
    try:
        rec = json.loads(setup.DONE.read_text())
    except (OSError, ValueError):
        pass
    return {"installed": setup.installed(), "env": str(setup.ENV), "install": rec, "identity": str(root),
            "people": people, "face_engine": face_engine.reason() or "ready", "enroll_port": _port("enroll")}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rest: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, rest = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--install", action="store_true", help="build the id env and fetch the face models, then exit")
    ap.add_argument("--run", action="store_true", help="serve the enroll coach on the enroll port")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--port", type=int, default=_port("enroll"))
    a = ap.parse_args(argv)
    from crissa_id import setup
    if a.install:
        why = setup.unsupported()
        if why:
            print("crissa-id: " + why, file=sys.stderr)
            return 2
        print(json.dumps(setup.install(), indent=1))
        return 0
    if a.status:
        print(json.dumps(status(), indent=1))
        return 0
    if a.run or rest:
        setup.reexec_in_env([__file__] + list(sys.argv[1:]))
        os.environ.setdefault("HOUSE_ENROLL_NO_BROWSER", "1")
        os.environ["ENROLL_UI_PORT"] = str(a.port)
        from crissa_id.house_enroll.__main__ import main as cli
        return int(cli(rest or ["coach", "--no-browser", "--port", str(a.port)]) or 0)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
