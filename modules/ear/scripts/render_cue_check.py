#!/usr/bin/env python3
"""Does the ear's corrections table reach Crissa? (the User 2026-10-03, crissa_bridge/heard_corrections.py)

FEEDS one recorded line straight into the ear - the ear's own POST /stt, which transcribes the audio and appends it to
the turn log the ear-bridge reads (via "stt", a source it accepts) - so NOTHING PLAYS ALOUD (the User 2026-10-03; the
first version played it through the room speakers). By default the clip is the User's own 'Put the render cue on the
wall', which the raw ear hears as 'render cue'. Then it reads what the ear-bridge logged for it (the `heard` text is the
CORRECTED text Crissa receives). Prints one line: `RESULT: PASS|FAIL - ...`.

OFF by default: runs only with RAIL_RENDER_CUE_CHECK=1 (the restart window's after-check passes it). Never plays while a
conversation with her is live (a session open, her voice on air, a reply in flight, or a hands job running); waits up to
--wait seconds, then says HELD. The clip is the site's own recording, never in a repo: --clip, default
<crissa home>/ear/render-cue-line.wav.

    RAIL_RENDER_CUE_CHECK=1 render_cue_check.py [--clip PATH] [--expect 'render queue'] [--reject 'render cue,render cube']
Exit: 0 PASS, 1 FAIL, 2 off/no clip, 3 held.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crissa_bridge.settings import url as _port_url  # noqa: E402
from crissa_bridge.home import EAR  # noqa: E402

HEALTH = os.environ.get("EAR_BRIDGE_HEALTH", _port_url("bridge") + "/health")
STT = os.environ.get("RAIL_STT_URL", _port_url("stt") + "/stt")
LOG = Path(os.environ.get("EAR_BRIDGE_LOG", str(ROOT / "logs" / "ear-bridge.jsonl")))


def health() -> dict | None:
    try:
        return json.loads(urllib.request.urlopen(HEALTH, timeout=2).read())
    except Exception:  # noqa: BLE001
        return None


def live_session() -> str:
    h = health()
    if h is None:
        return "ear-bridge not answering"
    if h.get("session_open"):
        return "a session with her is open"
    if h.get("speaking"):
        return "she is speaking"
    if h.get("dispatch_busy"):
        return "a reply is in flight"
    if subprocess.run(["pgrep", "-f", r"^(/usr/bin/)?python3 \S*crissa-hands\.py "], capture_output=True).returncode == 0:
        return "a hands job is running"
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clip", default=str(EAR / "render-cue-line.wav"))
    ap.add_argument("--expect", default="render queue")
    ap.add_argument("--reject", default="render cue,render cube")
    ap.add_argument("--wait", type=float, default=900)
    a = ap.parse_args(argv)
    if os.environ.get("RAIL_RENDER_CUE_CHECK", "0").strip().lower() not in ("1", "true", "yes", "on"):
        print("RESULT: OFF - set RAIL_RENDER_CUE_CHECK=1 to run (nothing played)"); return 2
    clip = Path(a.clip).expanduser()
    if not clip.is_file():
        print(f"RESULT: OFF - no clip at {clip} (nothing played)"); return 2
    t0 = time.time()
    while (why := live_session()):
        if time.time() - t0 > a.wait:
            print(f"RESULT: HELD - {why} (nothing played)"); return 3
        time.sleep(15)
    n0 = sum(1 for _ in LOG.open()) if LOG.is_file() else 0
    fed = time.strftime("%H:%M:%S")
    import wave
    with wave.open(str(clip)) as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            print("RESULT: OFF - the clip must be 16-bit mono (nothing fed)"); return 2
        sr, pcm = w.getframerate(), w.readframes(w.getnframes())
    req = urllib.request.Request(STT, data=pcm, method="POST",
                                 headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": str(sr)})
    try:
        urllib.request.urlopen(req, timeout=60).read()
    except Exception as e:  # noqa: BLE001
        print(f"RESULT: FAIL - the ear did not take the clip ({type(e).__name__}: {e})"); return 1
    time.sleep(8)
    rows = []
    if LOG.is_file():
        for i, line in enumerate(LOG.open()):
            if i >= n0 and line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    heard = [str(r.get("text") or "") for r in rows if r.get("event") == "heard"]
    ok = any(a.expect in t.lower() for t in heard)
    bad = any(r.strip() and r.strip() in t.lower() for t in heard for r in a.reject.split(","))
    verdict = "PASS" if ok and not bad else "FAIL"
    print(f"RESULT: {verdict} - fed the line to the ear at {fed} (nothing played); the ear-bridge heard: "
          f"{heard[:3] if heard else 'nothing (is the ear-bridge reading the turn log?)'}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
