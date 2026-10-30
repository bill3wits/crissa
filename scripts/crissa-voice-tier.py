#!/usr/bin/env python3
"""Show or change which voice this machine uses (2026-10-06; lib/crissa_voice_tiers).

    python3 scripts/crissa-voice-tier.py              # what was picked, why, what is missing
    python3 scripts/crissa-voice-tier.py --probe      # probe this machine again and pick again
    python3 scripts/crissa-voice-tier.py --pick pocket  # your own choice (multilingual, turbo, pocket, kokoro, say, piper, text); kept
    python3 scripts/crissa-voice-tier.py --reset      # forget the pick; the next start probes again
    python3 scripts/crissa-voice-tier.py --install    # install the voice this machine can run, as Init does (Init's
                                                      # retry): Turbo -> Pocket -> the Mac's say -> text
    python3 scripts/crissa-voice-tier.py --json       # the record as JSON

CRISSA_TTS, agent.json's tts engine and the launcher's --tts still win over the pick for one run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import crissa_voice_tiers as VT  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--probe", action="store_true")
    g.add_argument("--pick", metavar="ENGINE")
    g.add_argument("--reset", action="store_true")
    g.add_argument("--install", action="store_true", help="install the voice this machine can run, falling down "
                   "the chain when an install fails (what Init does; this is its retry)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--language", help="your preferred language (a name such as Deutsch, or a code such as de); "
                    "the voice is picked again for it, and the order per language is shown")
    ap.add_argument("--faces", action="store_true", help="say one short line as each face (crissa, lila, announcer) "
                    "through the running voices and report what each used")
    a = ap.parse_args(argv)
    if VT.is_house():
        rec = VT.house_pick()
        print(json.dumps(rec, indent=2) if a.json else "the house's voice: %s, backup %s (config/house-machine.json)"
              % (rec["main"], rec.get("backup") or "none"))
        return 0
    if a.install:
        import crissa_voice_install as VI
        res = VI.run(retry=True, progress=lambda line: print("voice: " + line, flush=True),
                     start=VI.launcher_start())
        print(json.dumps(res, indent=2) if a.json else "voice: " + res["line"])
        return 0 if res.get("ok") else 1
    if a.reset:
        try:
            VT.record_path().unlink()
        except FileNotFoundError:
            pass
        print("voice: pick forgotten; the next start probes this machine again")
        return 0
    if a.pick:
        try:
            rec = VT.override(a.pick)
        except ValueError as e:
            print("voice: %s" % e, file=sys.stderr)
            return 2
        print("voice: %s is your choice (this machine's own pick was %s)" % (rec["main"], rec.get("init_pick")))
        for w in rec.get("warn") or []:
            print("voice: note: %s %s" % (rec["main"], w))
        miss = VT.missing_line(rec)
        if miss:
            print("voice: " + miss)
        return 0
    if a.language:
        res = VT.set_language(a.language)
        if not res.get("ok"):
            print("voice: %s" % res.get("error"), file=sys.stderr)
            return 2
        rec = res["rec"]
        for line in rec.get("say") or []:
            print(line)
        return 0
    rec = VT.ensure(reprobe=a.probe, faces_live=a.faces)
    if a.json:
        print(json.dumps(rec, indent=2))
        return 0
    for line in rec.get("say") or []:
        print(line)
    print("voice: order on this machine: %s" % " -> ".join(rec.get("chain") or []))
    return 0


if __name__ == "__main__":
    sys.exit(main())
