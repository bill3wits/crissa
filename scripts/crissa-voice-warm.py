#!/usr/bin/env python3
"""Keep the active GPU voice warm (lib/crissa_voice_warm.py): warm on start and every keep_warm.interval_s.

    python3 scripts/crissa-voice-warm.py           run the loop (the launcher starts it on a GPU tier)
    python3 scripts/crissa-voice-warm.py --once    one warm now (the Control Panel's Run now), then exit
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import crissa_voice_warm as W  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    if a.once:
        out = W.warm_once()
        print(json.dumps(out))
        return 0 if out.get("ok") or out.get("skipped") else 1
    engine, why = W.target()
    print("crissa-voice-warm: %s" % (("warming %s every %ss" % (engine, W.settings().get("interval_s"))) if engine
                                     else why), flush=True)
    W.loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
