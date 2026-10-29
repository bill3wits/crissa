#!/usr/bin/env python3
"""Run every test file in its own pytest process, a few at a time, and sum them up (what CI runs).

Several modules are loaded under two names (a module's own package and the Wall's copy, a script by path and by
import), so one process for the whole suite lets one file's import shadow another's. One process per file is how
the suite is proven. usage: python scripts/run-tests.py [-j N] [pytest args...]   exit 0 = every file green
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    jobs = os.cpu_count() or 2
    if argv[:1] == ["-j"]:
        jobs, argv = int(argv[1]), argv[2:]
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests").rglob("test_*.py"))

    def one(f: str) -> tuple[str, int, str]:
        r = subprocess.run([sys.executable, "-m", "pytest", f, "-o", "addopts=", "-q", "-p", "no:cacheprovider", *argv],
                           cwd=ROOT, capture_output=True, text=True)
        return f, r.returncode, (r.stdout + r.stderr)

    totals = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    bad = []
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for f, rc, out in ex.map(one, files):
            for k in totals:
                m = re.search(r"(\d+) %s" % ("error" if k == "errors" else k), out.splitlines()[-1] if out.strip() else "")
                totals[k] += int(m.group(1)) if m else 0
            if rc not in (0, 5):
                bad.append(f)
                print(out, end="")
    print("%d files: %d passed, %d failed, %d errors, %d skipped" % (
        len(files), totals["passed"], totals["failed"], totals["errors"], totals["skipped"]))
    if bad:
        print("red: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
