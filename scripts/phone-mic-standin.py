#!/usr/bin/env python3
"""A stand-in for the phone's open mic (the User 2026-10-19, the phone as the second mic, build 1): streams a WAV to
the phone server's /api/mic in real time, in the chunks a phone would send, so the ear's compete rule can be proved
before the iPhone app exists.

    phone-mic-standin.py --wav line.wav [--url http://127.0.0.1:8226/api/mic] [--gain-db -6] [--lead-s 3] [--tail-s 3]
                         [--floor-db -50] [--token T] [--status]

--lead-s / --tail-s send silence around the WAV (the mic is open and quiet, so the ear learns the phone's floor first);
--gain-db scales it (a phone far from the speaker, or close); --floor-db lays a noise bed under all of it (a real phone
is never digitally silent, and its floor is what the ear scores it against). --status prints the ear's phone_mic block afterwards
(this computer's ear, /v1/ear/status). Needs CRISSA_PHONE_MIC=1 on the phone server and CRISSA_EAR_PHONE_MIC=1 on the
ear. Standard library only.
"""
from __future__ import annotations

import argparse
import array
import json
import random
import sys
import time
import urllib.error
import urllib.request
import wave


def read_mono16(path: str) -> tuple[array.array, int]:
    with wave.open(path, "rb") as w:
        ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        if width != 2:
            raise SystemExit("%s: 16-bit PCM only" % path)
        a = array.array("h", w.readframes(n))
    if sys.byteorder == "big":
        a.byteswap()
    if ch > 1:
        a = array.array("h", (int(sum(a[i:i + ch]) / ch) for i in range(0, len(a), ch)))
    return a, sr


def scaled(a: array.array, gain_db: float) -> array.array:
    if not gain_db:
        return a
    g = 10 ** (gain_db / 20.0)
    return array.array("h", (max(-32768, min(32767, int(v * g))) for v in a))


def with_floor(a: array.array, floor_db: float | None, seed: int = 7) -> array.array:
    if floor_db is None:
        return a
    amp = 32768.0 * 10 ** (floor_db / 20.0)
    r = random.Random(seed)
    return array.array("h", (max(-32768, min(32767, int(v + r.gauss(0.0, amp)))) for v in a))


def post(url: str, pcm: bytes, sr: int, token: str) -> tuple[int, dict]:
    h = {"Content-Type": "application/octet-stream", "X-Sample-Rate": str(sr)}
    if token:
        h["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=pcm, headers=h, method="POST"), timeout=3) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except (OSError, ValueError) as e:
        return 0, {"ok": False, "error": type(e).__name__}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--wav", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8226/api/mic")
    ap.add_argument("--chunk-s", type=float, default=0.2)
    ap.add_argument("--gain-db", type=float, default=0.0)
    ap.add_argument("--lead-s", type=float, default=3.0)
    ap.add_argument("--tail-s", type=float, default=3.0)
    ap.add_argument("--floor-db", type=float, default=None, help="a noise bed at this level (dBFS), e.g. -50")
    ap.add_argument("--token", default="")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--ear-status", default="http://127.0.0.1:8190/v1/ear/status")
    a = ap.parse_args()
    x, sr = read_mono16(a.wav)
    x = scaled(x, a.gain_db)
    body = array.array("h", bytes(2 * int(a.lead_s * sr))) + x + array.array("h", bytes(2 * int(a.tail_s * sr)))
    body = with_floor(body, a.floor_db)
    step = max(1, int(a.chunk_s * sr))
    sent, bad, t0 = 0, {}, time.monotonic()
    for i in range(0, len(body), step):
        part = body[i:i + step]
        code, res = post(a.url, part.tobytes(), sr, a.token)
        sent += 1
        if code != 200:
            bad[code] = res.get("error") or res
        due = t0 + (i + len(part)) / float(sr)          # real time: a phone sends as it hears
        time.sleep(max(0.0, due - time.monotonic()))
    print(json.dumps({"chunks": sent, "seconds": round(len(body) / float(sr), 2), "refused": bad}))
    if a.status:
        time.sleep(2.5)                                 # the ear flushes after its hangover
        try:
            with urllib.request.urlopen(a.ear_status, timeout=3) as r:
                print(json.dumps(json.loads(r.read()).get("phone_mic"), indent=1))
        except (OSError, ValueError) as e:
            print("ear status: %s" % type(e).__name__)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
