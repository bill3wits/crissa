#!/usr/bin/env python3
"""The ear module's speech-to-text service (2026-10-09): the microphone in, turns out - the house's ear, whole.

    GET  /health                  the ear's state (engine, ASR, voice bank, the open ear, the beam)
    GET  /v1/turns?n=30[&via=..]  {"turns": [newest first], "count"}  - what the bridge and the player read
    POST /stt                     raw int16 PCM (X-Sample-Rate) -> a transcript with its speaker stamp
    POST /v1/mic                  raw int16 PCM from the Wall page (--mic page)
    POST /v1/ear/open|close, /v1/listen, /v1/house/voice, /panel/*, /v1/omni, /v1/media_locus ... (crissa_ear.server)

    python3 modules/ear/scripts/crissa-stt.py --install            # once: the env from modules/ear/locks + the voice-ID model
    python3 modules/ear/scripts/crissa-stt.py --install-voice-id   # the voice-ID model alone (pinned, sha256-checked)
    python3 modules/ear/scripts/crissa-stt.py [--port 8190] [--asr http://127.0.0.1:8096] [--mic host|page|NAME]

Ports follow the shared table (CRISSA_PORT_STT / _ASR / _PRESENCE, CRISSA_PORT_OFFSET); a taken port is one line and
exit 2. Settings: <crissa home>/ear.json "ear" section (crissa_ear.settings: the house's drop-ins as keys); a variable
already in the environment wins. The ear opens itself at start and is kept open (the house had a keeper for that).
Loopback only. Nothing is written outside the state home.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

# A MAC'S SYSTEM PROXY NEVER CARRIES A LOOPBACK CALL (2026-10-04): kill CRISSA_NO_PROXY=0.
if sys.platform == "darwin" and os.environ.get("CRISSA_NO_PROXY", "1").strip() != "0":
    for _k in ("NO_PROXY", "no_proxy"):
        os.environ.setdefault(_k, "127.0.0.1,localhost")

from crissa_ear import settings as S  # noqa: E402  (stdlib only; seeds the environment before the rest imports)

# .101 round 30: the ear's decoder hung after a fresh Setup twice (round 29 13:09, round 30 15:39: decoding true, 2
# queued, 0 chunks, every later utterance dropped as queue full, so no wake word; no socket to the ASR, every thread on a
# futex) and nothing could read its stack (ptrace_scope 1). `kill -USR1 <pid>` writes every thread's stack to its log.
if hasattr(__import__("signal"), "SIGUSR1"):
    import faulthandler as _fh
    import signal as _sig
    _fh.register(_sig.SIGUSR1, all_threads=True)


def _keep_open(port: int, log=print) -> None:
    """The house's keeper reopened the ear when it closed; here a thread does, every few seconds."""
    base = "http://127.0.0.1:%d" % port
    body = json.dumps({"max_open_sec": 86400}).encode()
    waited = False
    while True:
        try:
            with urllib.request.urlopen(base + "/v1/ear/status", timeout=2.0) as r:
                st = json.loads(r.read().decode() or "{}")
            # .101 round 29: Voice ID's mic hold (voice_session._set_mic_hold) keeps the ear closed, as the house's
            # keeper did; reopening it made every GO wait out CRISSA_GO_WAIT_EAR_S. Kill CRISSA_KEEPER_HONORS_HOLD=0.
            held = (os.environ.get("CRISSA_KEEPER_HONORS_HOLD", "1").strip().lower() not in ("0", "off", "false", "no")
                    and os.path.isfile(os.environ.get("HOUSE_ENROLL_MIC_HOLD", "/tmp/house-enroll.mic-hold")))
            if not st.get("open") and not held:
                req = urllib.request.Request(base + "/v1/ear/open", data=body, method="POST",
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5.0) as r:
                    out = json.loads(r.read().decode() or "{}")
                log("crissa-stt: ear %s" % ("open" if out.get("ok", True) else out))
        except Exception as e:  # noqa: BLE001  the server may still be warming
            # round 23 (.101: "Connection refused" once per start read like an error): its own port is not open yet
            if "refused" in str(e).lower():
                if not waited:
                    log("crissa-stt: ear keeper: waiting for the ear's own port to open")
                    waited = True
            else:
                log("crissa-stt: ear keeper: %s" % str(e)[:120])
        time.sleep(5.0)


def port_free(port: int) -> None:
    """Raise OSError when another process LISTENS on the port. SO_REUSEADDR, as the ear's own server binds (http.server):
    a restart's old connections in TIME_WAIT are not a running copy (the User 2026-10-10, .101 round 4: after the mic
    pick restarted stt, the bare probe failed for the ~60 s of TIME_WAIT and systemd restarted the unit 17 times -
    "restarted x17" on both panels; the ear was deaf for that minute)."""
    import socket
    probe = socket.socket()
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", int(port)))
    finally:
        probe.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the ear: the microphone's turns, with speaker stamps")
    ap.add_argument("--port", type=int, default=S.port("stt"))
    ap.add_argument("--asr", default=None, help="the /stt service (default: this install's ASR from the port table)")
    ap.add_argument("--player", default=None, help="the player (default: the port table)")
    ap.add_argument("--mic", default=None, help="host | page | a device name or substring (default: settings `mic`)")
    ap.add_argument("--device", default=None, help="alias of --mic NAME")
    ap.add_argument("--no-mic", action="store_true", help="serve /stt and /v1/turns without opening the ear")
    ap.add_argument("--install", action="store_true", help="build the env from the locks, then exit")
    ap.add_argument("--install-voice-id", action="store_true",
                    help="fetch the voice-ID model (pinned, checked by sha256; --install does it too), then exit")
    ap.add_argument("--status", action="store_true", help="print the install record and the settings, then exit")
    a = ap.parse_args(argv)
    cfg = S.apply()
    if a.asr:
        os.environ["LIVE_EAR_URL"] = a.asr
    if a.player:
        os.environ["CRISSA_PLAYER"] = a.player
    mic = a.mic or a.device
    if mic:
        os.environ["RAIL_STT_MIC"] = "page" if mic == "page" else mic
        if mic == "page":
            os.environ["RAIL_STT_MIC_MODE"] = "page"
    elif os.environ.get("CRISSA_AUDIO_AT", "").strip().lower() == "page" and os.environ.get("RAIL_STT_MIC_MODE", "host") != "host":
        os.environ["RAIL_STT_MIC"] = "page"
    if os.environ.get("RAIL_STT_MIC_MODE") == "page":
        os.environ["RAIL_STT_MIC"] = "page"
    from crissa_ear import setup as SETUP  # noqa: E402
    if a.status:
        print(json.dumps({"install": SETUP.status(), "settings": S.view()}, indent=1))
        return 0
    if a.install:
        why = SETUP.unsupported()
        if why:
            print("crissa-stt: " + why, file=sys.stderr)
            return 2
        SETUP.install()
        return 0
    if a.install_voice_id:
        print(json.dumps(SETUP.install_voice_id()))
        return 0
    if not SETUP.installed():
        print("crissa-stt: not installed yet; run with --install first (the ear's env, about 1 GB)", file=sys.stderr)
        return 2
    if not SETUP.in_env():
        os.execv(str(SETUP.env_python()), [str(SETUP.env_python()), __file__, *sys.argv[1:]])
    import socket
    try:
        port_free(a.port)
    except OSError as e:
        print("crissa-stt: port %d is already in use (%s): another copy may be running. Stop it, then start Crissa "
              "again. (for developers: --port N, CRISSA_PORT_STT=N or CRISSA_PORT_OFFSET=N)" % (a.port, e.strerror or e),
              file=sys.stderr, flush=True)
        return 2
    from crissa_ear.config import Config  # noqa: E402
    from crissa_ear.server import serve  # noqa: E402
    if not a.no_mic:
        threading.Thread(target=_keep_open, args=(a.port,), name="ear-keeper", daemon=True).start()
    pref = os.environ.get("RAIL_STT_MIC", "auto")
    try:                                   # say which microphone "auto" is (the User 2026-10-10: the default input)
        from crissa_ear import audio as _audio  # noqa: E402
        heard_on = _audio.resolve_mic(pref) if pref not in ("page",) else pref
    except Exception as e:  # noqa: BLE001
        heard_on = "none found (%s)" % str(e)[:80]
    print("crissa-stt: http://127.0.0.1:%d  (turns at /v1/turns, ASR %s, mic %s -> %s)" % (
        a.port, os.environ.get("LIVE_EAR_URL"), pref, heard_on), flush=True)
    try:
        serve(host="127.0.0.1", port=a.port, cfg=Config())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
