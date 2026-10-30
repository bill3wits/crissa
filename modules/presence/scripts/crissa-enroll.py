#!/usr/bin/env python3
"""The registration panel (modules/presence, 2026-10-09): the enroll coach's page and its sessions
(crissa_presence/enroll_panel, the house's registration tools, extracted).

    python3 modules/presence/scripts/crissa-enroll.py [--port N]       # the coach on 127.0.0.1:8196, no browser
    python3 modules/presence/scripts/crissa-enroll.py -- <command> ... # the CLI (doctor, status, person add ...)

It reads the presence service (RAIL_PRESENCE) and the camera (RAIL_CAMERA_URL); the identity store it writes is
<state home>/identity/people/<id>/ (HOUSE_ID_ROOT names another). Loopback only; same env as crissa-presence.py.
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
    return _P.port("enroll", env)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rest: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, rest = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description="Crissa registration panel")
    ap.add_argument("--port", type=int, default=_port())
    a = ap.parse_args(argv)
    from crissa_presence import setup as S
    if not S.installed():
        print("crissa-enroll: not installed yet; run crissa-presence.py --install first", file=sys.stderr)
        return 2
    S.reexec_in_env([__file__] + list(sys.argv[1:]))
    os.environ.setdefault("HOUSE_ENROLL_NO_BROWSER", "1")
    os.environ["ENROLL_UI_PORT"] = str(a.port)
    cli = coach_cli()
    return int(cli(rest or ["coach", "--no-browser", "--port", str(a.port)]) or 0)


def id_module(root: Path | None = None) -> Path | None:
    """modules/id beside this module, when its enrol package is there (the id module is the one coach; the copy in
    crissa_presence.enroll_panel is the fallback for an install without it). CRISSA_ENROLL_COACH=presence keeps
    the copy."""
    if os.environ.get("CRISSA_ENROLL_COACH", "").strip().lower() == "presence":
        return None
    idm = (HERE.parent if root is None else root) / "id"
    return idm if (idm / "crissa_id" / "house_enroll" / "__main__.py").is_file() else None


def coach_cli(root: Path | None = None):
    """ONE COACH (2026-10-09): the registration panel is the id module's house_enroll when modules/id is installed
    beside this one (its identity store, its face engine, its voice-register), else this module's own copy."""
    idm = id_module(root)
    if idm is not None:
        if str(idm) not in sys.path:
            sys.path.insert(0, str(idm))
        try:
            from crissa_id.house_enroll.__main__ import main as cli   # crissa_id.paths exports HOUSE_ID_ROOT
            return cli
        except Exception as e:  # noqa: BLE001  a broken id module never takes Register down
            print("crissa-enroll: the id module's coach did not import (%s: %s); this module's copy serves"
                  % (type(e).__name__, str(e)[:120]), file=sys.stderr)
    from crissa_presence.enroll_panel.__main__ import main as cli
    return cli


if __name__ == "__main__":
    raise SystemExit(main())
