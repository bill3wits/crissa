#!/usr/bin/env python3
"""Better mouths, offline: make the aligner's bundle on a connected machine, install it on one with no network
(the User 2026-10-05; lib/crissa_bundle.py, lib/crissa_aligner.py).

    python3 scripts/crissa-aligner-bundle.py build <dir>      # on a machine like the offline one, with the internet
    python3 scripts/crissa-aligner-bundle.py install <file>   # on the offline machine; makes no connection

A bundle is made for one platform (the one it is made on). It holds other projects' software under their own
licences (THIRD-PARTY-NOTICES.md); it is yours to make and carry, not something Crissa hands out.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import crissa_aligner  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("build").add_argument("dir")
    sub.add_parser("install").add_argument("file")
    a = ap.parse_args(argv)
    try:
        if a.verb == "build":
            print(crissa_aligner.build_bundle(Path(a.dir)))
        else:
            print(json.dumps(crissa_aligner.install_bundle(Path(a.file))))
    except (OSError, ValueError, RuntimeError) as exc:
        print("crissa-aligner-bundle: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
