#!/usr/bin/env python3
"""The ear bridge (2026-10-09): a turn from the ear becomes a question to the connected agent, and her reply is spoken.

Strict wake grammar ("Hey Crissa", "Crissa,", a bare "Crissa" answered with "Yeah?"; crissa_bridge/wake.py holds the
spellings the speech-to-text was measured to hear for her name), the hear gate, voice-ID authorize
(bau | guest | silent), a guest's reply with no persona and no tools, her own voice coming back (own_voice), a
name interrupt that cuts her line, the quiet wait before she speaks, the thinking file the Wall's pulse reads,
tool callouts from the agent's stream, the claim check, heard corrections, house commands, the session machine.

    GET  /health                       speaking, session_open, paused, agent_connected, tool_events_seen, callouts
    POST /v1/abort                     cut her line now
    POST /v1/session/open|pause|hold   the Wall's LISTENING / PAUSED chips

    python3 modules/ear/scripts/crissa-bridge.py [--port 8194]

The agent is the Connect endpoint (<crissa home>/agent.json; crissa_bridge.connect). The ear is the stt service on
the shared port table; the mouth is the pack's scripts/house-speak.py. Settings: <crissa home>/ear.json "bridge"
section (crissa_bridge.settings: the house's drop-ins as keys); a variable already set wins. Runs in the ear's env
(--install on crissa-stt.py); the standard library and the pack's lib/ are all it needs beyond that.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "lib"))

if sys.platform == "darwin" and os.environ.get("CRISSA_NO_PROXY", "1").strip() != "0":
    for _k in ("NO_PROXY", "no_proxy"):
        os.environ.setdefault(_k, "127.0.0.1,localhost")

from crissa_bridge import settings as S  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the ear bridge: turns to the agent, her reply to the mouth")
    ap.add_argument("--port", type=int, default=S.port("bridge"))
    ap.add_argument("--stt", default=None, help="the ear's url (default: the port table)")
    ap.add_argument("--player", default=None, help="the player's url (default: the port table)")
    ap.add_argument("--status", action="store_true", help="print the settings in force, then exit")
    a = ap.parse_args(argv)
    if a.stt:
        os.environ["RAIL_STT_URL"] = a.stt.rstrip("/")
    if a.player:
        os.environ["CRISSA_PLAYER"] = a.player.rstrip("/")
    os.environ["EAR_BRIDGE_ABORT_PORT"] = str(a.port)
    S.apply()
    if a.status:
        import json
        print(json.dumps(S.view(), indent=1))
        return 0
    try:
        from crissa_ear import setup as EAR_SETUP  # the ear's env carries numpy + yaml; use it when it is there
        if EAR_SETUP.installed() and not EAR_SETUP.in_env() and os.environ.get("CRISSA_BRIDGE_OWN_PYTHON", "") != "1":
            os.execv(str(EAR_SETUP.env_python()), [str(EAR_SETUP.env_python()), __file__, *sys.argv[1:]])
    except Exception:  # noqa: BLE001  no ear env: run on this Python
        pass
    from crissa_bridge.ear_bridge import main as bridge_main  # noqa: E402
    print("crissa-bridge: http://127.0.0.1:%d  (ear %s, player %s)" % (
        a.port, os.environ.get("RAIL_STT_URL"), os.environ.get("CRISSA_PLAYER")), flush=True)
    try:
        return int(bridge_main() or 0)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
