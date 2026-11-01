#!/usr/bin/env python3
"""Compat wrapper → house_enroll voice session.

Prefer:
  modules/id/scripts/house-enroll voice <user_id>
  modules/id/scripts/house-enroll voice <regular_id> --only 2 --replace

  <the id env's python> crissa_id/voice_enroll_room.py --person <user_id>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from crissa_id.house_enroll.site import load_site  # noqa: E402
from crissa_id.house_enroll.voice_session import run_voice_session  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Voice enroll (compat → house_enroll)")
    ap.add_argument("--person", required=True)
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--beep-sec", type=float, default=None, help="ignored if site.json set; use site.voice.beep_sec")
    ap.add_argument("--only", type=int, choices=[1, 2, 3], default=None)
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if args.quiet:
        import os

        os.environ["HOUSE_ENROLL_QUIET"] = "1"
    site = load_site()
    if args.beep_sec is not None:
        site.raw.setdefault("voice", {})["beep_sec"] = args.beep_sec
    written = run_voice_session(
        site,
        args.person,
        only=args.only,
        replace_slots=args.replace,
        seconds=args.seconds,
    )
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
