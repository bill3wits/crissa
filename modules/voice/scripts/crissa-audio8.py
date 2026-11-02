#!/usr/bin/env python3
"""The announcer's voice: Audio8 TTS on the CPU, on loopback (modules/voice/crissa_voice/audio8_setup.py).

    python3 crissa-audio8.py --install     build the env, fetch the runtime, model and clip, register the voice
    python3 crissa-audio8.py               serve /api/tts and /api/health on CRISSA_PORT_AUDIO8 (8187); the log
                                           goes to <crissa home>/audio8/server.log, the pid to server.pid
    python3 crissa-audio8.py --self-test   start it on a spare port, say one line as the announcer, stop
    python3 crissa-audio8.py --restart     stop the recorded server and start it again, detached (the panel's key)
    python3 crissa-audio8.py --unit        write a systemd user unit, crissa-audio8.service (not enabled): the
                                           Control Panel then shows it with its journal and restart key

Which voice the announcer uses is config (the tier table's announcer.audio8.voice; default anna). If Audio8 is not
installed, not running or its voice is missing, the announcer's lines fall to Pocket, then Kokoro, then say, then
text, and Init, the log and the panel say why.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from crissa_voice import audio8_setup as S  # noqa: E402

UNIT = """[Unit]
Description=crissa-audio8 - the announcer's voice (Audio8 TTS, CPU, loopback)

[Service]
Type=simple
ExecStart={python} {script} --port {port}
Restart=on-failure
RestartSec=5
Nice=10

[Install]
WantedBy=default.target
"""


def _port() -> int:
    v = os.environ.get("CRISSA_PORT_AUDIO8", "").strip()
    return int(v) if v.isdigit() else 8187


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _say(port: int, text: str, voice: str, timeout: float = 120.0) -> bytes:
    body = json.dumps({"text": text, "voice_name": voice, "max_new_tokens": 256, "temperature": 0.3}).encode()
    req = urllib.request.Request("http://127.0.0.1:%d/api/tts" % port, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def start(port: int, detach: bool = False) -> subprocess.Popen:
    S.ROOT.mkdir(parents=True, exist_ok=True)
    log = open(S.LOG, "ab")
    p = subprocess.Popen(S.server_argv(port), env=S.server_env(), cwd=str(S.CODE / "onnx_runtime"), stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=detach)
    S.PID.write_text("%d %d\n" % (p.pid, port))
    return p


def stop_recorded() -> int | None:
    try:
        pid, port = (int(x) for x in S.PID.read_text().split()[:2])
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            os.kill(pid, 0)
            time.sleep(0.1)
    except ProcessLookupError:
        pass
    return port


def self_test() -> dict:
    port = _free_port()
    voice = S.voice_name()
    if not S.registered(voice):
        voice = S.DEFAULT_VOICE
    p = start(port)
    out = {"ok": False, "voice": voice}
    try:
        for _ in range(240):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=2) as r:
                    if json.loads(r.read()).get("ok"):
                        break
            except OSError:
                time.sleep(0.5)
        t0 = time.monotonic()
        wav = _say(port, "Attention please. The announcer is ready.", voice)
        out.update(ok=len(wav) > 44 + 4800, bytes=len(wav), seconds=round(time.monotonic() - t0, 2))
        if not out["ok"]:
            out["why"] = "it answered with %d bytes, not speech" % len(wav)
    except Exception as e:  # noqa: BLE001
        out["why"] = "%s: %s" % (type(e).__name__, str(e)[:200])
    finally:
        p.terminate()
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
    rec = dict(S.record(), self_test=dict(out, at=time.strftime("%Y-%m-%dT%H:%M:%S%z")))
    S.DONE.write_text(json.dumps(rec) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audio8 TTS (the announcer) on loopback")
    ap.add_argument("--port", type=int, default=_port())
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--unit", action="store_true")
    a = ap.parse_args(argv)
    if a.install:
        try:
            S.install()
        except RuntimeError as e:
            print("crissa-audio8: %s" % e, file=sys.stderr)
            return 2
        st = self_test()
        print("crissa-audio8: self-test %s" % ("passed (%s, %.1f s)" % (st["voice"], st["seconds"]) if st["ok"]
                                                else "FAILED: %s; Pocket says the announcer's lines" % st.get("why")))
        return 0 if st["ok"] else 1
    if not S.installed():
        print("crissa-audio8: not installed yet; run with --install first (about 1 GB)", file=sys.stderr)
        return 2
    if a.self_test:
        st = self_test()
        print(json.dumps(st))
        return 0 if st["ok"] else 1
    if a.unit:
        d = Path.home() / ".config" / "systemd" / "user"
        d.mkdir(parents=True, exist_ok=True)
        (d / "crissa-audio8.service").write_text(UNIT.format(python=sys.executable, script=Path(__file__).resolve(),
                                                             port=a.port))
        print("crissa-audio8: wrote %s (not enabled: systemctl --user enable --now crissa-audio8)" % (d / "crissa-audio8.service"))
        return 0
    if a.restart:
        port = stop_recorded() or a.port
        p = start(port, detach=True)
        print("crissa-audio8: restarted, pid %d on %d (log %s)" % (p.pid, port, S.LOG))
        return 0
    print("crissa-audio8: serving on 127.0.0.1:%d as %r (log %s)" % (a.port, S.voice_name(), S.LOG), flush=True)
    p = start(a.port)
    stopping = []

    def _stop(_sig, _frame):
        # Stopping this lane stops the server it started (2026-10-07: the panel's Stop for Audio8 ended this
        # wrapper and left the Audio8 server serving on the port, so it never stopped and a Restart could not bind).
        stopping.append(True)
        p.terminate()
    signal.signal(signal.SIGTERM, _stop)
    try:
        rc = p.wait()
        return 0 if stopping else rc
    except KeyboardInterrupt:
        p.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
