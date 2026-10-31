#!/usr/bin/env python3
"""Can this computer run the Wall and her voice? Measure, recommend a tier, write <state home>/machine.json.

    python3 scripts/crissa-machine-check.py [--quick] [--json] [--dry] [--card]

On by default in a pack (the User 2026-10-10); CRISSA_MACHINE_CHECK=0 switches it off, and on the house it is off
unless CRISSA_MACHINE_CHECK=1 (which then still answers "house"). Running it is the person's own Allow: the answer
is kept with permission "allow", so Init does not ask again. --card prints the permission card Init would show here
and measures nothing.

Exit 0 = measured (the tier is in the answer), 2 = could not measure anything (the reasons say why),
3 = the check is switched off. It starts nothing and changes no setting: the tier is a recommendation that
Init shows and the user confirms. See lib/crissa_machine.py for the stages and config/crissa-machine-tiers.json
for the bars.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import crissa_machine as M  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="measure this machine for the Wall and her voice")
    ap.add_argument("--quick", action="store_true", help="fewer frames, one line (Init's first boot)")
    ap.add_argument("--json", action="store_true", help="print the whole answer as JSON")
    ap.add_argument("--dry", action="store_true", help="measure but do not write machine.json")
    ap.add_argument("--card", action="store_true", help="print the permission card Init would show; measure nothing")
    a = ap.parse_args(argv)
    if a.card:
        if not M.on() or M.crissa_home.is_house():
            print("machine check is off here: no card", file=sys.stderr)
            return 3
        c = M.card(quick=True)
        print(json.dumps(c, indent=1) if a.json else "\n".join((c["title"], c["what"], c["facts"], c["skip"])))
        return 0
    rec = M.run_check(quick=a.quick, write=not a.dry, permission="allow")
    if rec.get("skipped") == "off":
        print("machine check is off (CRISSA_MACHINE_CHECK=0, or the house, where it is off unless =1)", file=sys.stderr)
        return 3
    if rec.get("skipped"):
        print("machine check skipped: %s" % rec["skipped"], file=sys.stderr)
        return 3
    if a.json:
        print(json.dumps(rec, indent=1))
    else:
        st = rec["stages"]
        print("tier: %s (measured in %.1f s)" % (rec["tier"], rec.get("elapsed_s") or 0.0))
        if "render" in st:
            print("  render  %.1fx real time (%s fps)" % (st["render"]["x_realtime"], st["render"]["fps"]))
        if "tts" in st:
            print("  voice   %s: first audio %.2f s, rtf %s" % (st["tts"]["engine"], st["tts"]["first_audio_s"], st["tts"]["rtf"]))
        if "asr" in st:
            print("  hearing rtf %s (%r)" % (st["asr"]["rtf"], st["asr"]["text"]))
        if "align" in st:
            print("  aligner %.2f s a line" % st["align"]["s_per_line"])
        for r in rec["reasons"] + rec["notes"]:
            print("  - " + r)
        if not a.dry:
            print("written: %s" % M.out_path())
    return 0 if rec["stages"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
