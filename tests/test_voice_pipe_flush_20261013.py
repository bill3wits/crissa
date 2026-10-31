"""Her voice reaches the player when it is written (2026-10-13, round 17 on .101: in conversation her mouth ran ~0.5 s
ahead of her voice, by a different amount on each line, while the tester's sentence was in sync).

  * Every write into paplay's pipe (and into the tempo stage's) is in the pipe when write() returns. A Popen pipe is
    buffered by Python: 4096 bytes on 3.12 (the pipe's st_blksize), 131072 on 3.14. Kill HOUSE_SPEAK_PIPE_FLUSH=0.
  * The stamp hold keeps 0.2 s in the sink while she waits (was 0.08 s): HOUSE_SPEAK_STAMP_HOLD_LOW_S.
  * The stamp hold is OFF by default (round 16's path, in sync at lip sync 0 on .101); HOUSE_SPEAK_STAMP_HOLD=1 is on.
  * The pack's ear bridge runs her lines on its own Python, not on whatever `python3` the PATH finds.

Real pipes and stub readers. Nothing is played: no paplay, no sound server, no wall; the voice is a stand-in and the
sound server is simulated from the reader's arrival log (PipeWire 1.6's pulse server rule, and PulseAudio's)."""
from __future__ import annotations

import ast
import base64
import bisect
import contextlib
import glob
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
SPEAK_PY = ROOT / "scripts" / "house-speak.py"
SPEAK = SPEAK_PY.read_text(encoding="utf-8")
BRIDGE = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text(encoding="utf-8")
SR = 24000
BPS = 2.0 * SR                 # bytes a second: 24 kHz s16 mono, Pocket's voice
HOP_B = 1000                   # one semiframe hop (500 samples, 20.833 ms): the stamp hold's own write
FRAME_S = 1.0 / 24.0           # one film frame


# ---- the pipe itself, on each Python this machine has --------------------------------------------------------------

READER = r'''
import json, os, sys, time
out = sys.argv[1]
got, data = [], bytearray()
open(out + ".ready", "w").close()          # reading from here on (paplay is up before house-speak writes its pad)
while True:
    b = os.read(0, 1 << 20)
    t = time.time()
    if not b:
        break
    got.append((t, len(b)))
    data += b
with open(out + ".bin", "wb") as f:
    f.write(data)
with open(out, "w") as f:
    json.dump(got, f)
'''

WRITER = r'''
import ast, json, os, subprocess, sys, time
src = open(sys.argv[1], encoding="utf-8").read()
names = ("pipe_flush_on", "_FlushedPipe", "_flushed", "_pipe_player")
got = [n for n in ast.parse(src).body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
ns = {"os": os, "subprocess": subprocess}
exec(compile(ast.Module(body=got, type_ignores=[]), "house-speak", "exec"), ns)
reader, out, hops, hop_b, hop_s = sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), float(sys.argv[6])
bufsize = int(sys.argv[7]) if len(sys.argv) > 7 else -1
if bufsize < 0:
    p = ns["_pipe_player"]([sys.executable, reader, out])    # house-speak's own player pipe, a stub at the far end
else:                                                         # the same pipe with a buffer of that size
    p = subprocess.Popen([sys.executable, reader, out], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         bufsize=bufsize)
    p.stdin = ns["_flushed"](p.stdin)
while not os.path.exists(out + ".ready"):
    time.sleep(0.002)
f = getattr(p.stdin, "_f", p.stdin)
buf = f.__sizeof__() - type(f).__basicsize__ if hasattr(f, "__sizeof__") else None
writes = []
t0 = time.time()
for i in range(hops):
    d = t0 + i * hop_s - time.time()
    if d > 0:
        time.sleep(d)
    p.stdin.write(bytes([1 + i % 250]) * hop_b)
    writes.append(time.time())
closed = time.time()
p.stdin.close()
p.wait(timeout=30)
with open(out + ".w", "w") as fh:
    json.dump({"py": sys.version.split()[0], "buffer": buf, "writes": writes, "closed": closed}, fh)
'''


def _pythons() -> dict:
    """{"3.12": path, "3.14": path} for the interpreters on this machine (this one, the uv ones, the system's)."""
    cands = [sys.executable, "/usr/bin/python3.14", "/usr/bin/python3.12"]
    for pat in ("~/.local/share/uv/python/cpython-3.12*/bin/python3.12",
                "~/.local/share/uv/python/cpython-3.14*/bin/python3.14"):
        cands += sorted(glob.glob(os.path.expanduser(pat)))
    out: dict = {}
    for c in cands:
        if not c or not os.path.isfile(c):
            continue
        try:
            v = subprocess.run([c, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], capture_output=True,
                               text=True, timeout=20).stdout.strip()
        except Exception:  # noqa: BLE001
            continue
        if v in ("3.12", "3.14") and v not in out:
            out[v] = c
    return out


PYTHONS = _pythons()


def _arrivals(tmp_path: Path, py: str, flush: bool, hops: int = 48, bufsize: int = -1) -> dict:
    """One 1 s line of hops written 20.833 ms apart into house-speak's player pipe; each hop's write and arrival."""
    d = tmp_path / ("%s-%s-%d" % (Path(py).name, "flush" if flush else "buffered", bufsize))
    d.mkdir()
    (d / "reader.py").write_text(READER)
    (d / "writer.py").write_text(WRITER)
    env = dict(os.environ, HOUSE_SPEAK_PIPE_FLUSH="1" if flush else "0", PYTHONDONTWRITEBYTECODE="1")
    out = d / "got.json"
    subprocess.run([py, str(d / "writer.py"), str(SPEAK_PY), str(d / "reader.py"), str(out), str(hops), str(HOP_B),
                    str(1.0 / 48), str(bufsize)], env=env, check=True, timeout=60)
    w = json.loads((d / "got.json.w").read_text())
    got = json.loads(out.read_text())
    cum, at = [], []
    total = 0
    for t, n in got:
        total += n
        cum.append(total)
        at.append(t)
    late = []
    for i, tw in enumerate(w["writes"]):
        j = bisect.bisect_left(cum, (i + 1) * HOP_B)       # the read that brought this hop's last byte
        late.append(at[j] - tw)
    return {"py": w["py"], "buffer": w["buffer"], "late": late, "reads": [n for _t, n in got],
            "first_read": at[0], "closed": w["closed"], "total": total}


def _ms(x: float) -> str:
    return "%.1f ms" % (x * 1000.0)


def _report(ver: str, label: str, r: dict) -> str:
    late = sorted(r["late"])
    return ("%s (%s) %s, pipe buffer %s bytes: %d reads of %s bytes; each hop reached the reader %s after its write "
            "(median), %s at most; first byte %s after the line closed"
            % (ver, r["py"], label, r["buffer"], len(r["reads"]), sorted(set(r["reads"])), _ms(late[len(late) // 2]),
               _ms(late[-1]), _ms(r["first_read"] - r["closed"])))


@pytest.mark.parametrize("ver", ["3.12", "3.14"])
@pytest.mark.parametrize("bufsize", [-1, 8192], ids=["popen-default", "8192"])
def test_a_hop_is_in_the_readers_pipe_when_its_write_returns(tmp_path, ver, bufsize):
    """Hops of 24 kHz s16 mono (1000 bytes, 20.833 ms), as the stamp hold writes them. `8192` is io.DEFAULT_BUFFER_SIZE
    on 3.12, what a pipe gets where the OS gives no st_blksize (Linux gives 4096)."""
    py = PYTHONS.get(ver)
    if not py:
        pytest.skip("no Python %s on this machine" % ver)
    on = _arrivals(tmp_path, py, flush=True, bufsize=bufsize)
    off = _arrivals(tmp_path, py, flush=False, bufsize=bufsize)
    print("\n" + _report(ver, "flushed ", on) + "\n" + _report(ver, "buffered", off))
    assert on["total"] == off["total"] == 48 * HOP_B, "every byte, in both"
    late = sorted(on["late"])
    assert late[len(late) // 2] < 0.003 and late[-1] < 0.02, "each hop is the reader's within a few ms of its write"
    assert len(on["reads"]) >= 40, "one read per write, give or take a reader that woke late"
    buf = off["buffer"]
    if bufsize > 0:
        assert buf == bufsize
    if buf and buf < 48 * HOP_B:
        clump = (buf // HOP_B) * HOP_B
        assert all(n == clump for n in off["reads"][:-1]), "round 17: the hops reach the reader a buffer at a time"
        assert off["late"][0] > (clump // HOP_B - 1) * (1.0 / 48) - 0.005, "the first hop waits for the rest"
    else:
        assert off["first_read"] >= off["closed"] - 0.002, "a line shorter than the buffer reaches it at its close"


# ---- a held line through house-speak's own streamed path, against a simulated sound server -------------------------

def _serve(arrivals: list, q: int, rule: str, prebuf: int = 12000) -> tuple[list, int]:
    """When each byte of the stream is played: [(t, offset, n)], and how many quanta were silence (underruns).

    `rule` "pipewire": every quantum takes q bytes; with fewer it plays silence and keeps them (PipeWire 1.6.2's pulse
    server, src/modules/module-protocol-pulse/pulse-server.c stream_process: `avail < minreq` and `attr.prebuf > 0`).
    "pulse": PulseAudio's memblockq: an emptied queue waits until `prebuf` bytes are queued again (paplay's prebuf at
    --latency-msec=500 is ~250 ms after the latency adjust). The stream starts on the first quantum after prebuf."""
    ts, cum = [], []
    total = 0
    for t, n in arrivals:
        total += n
        ts.append(t)
        cum.append(total)

    def by(t: float) -> int:
        j = bisect.bisect_right(ts, t)
        return cum[j - 1] if j else 0

    j0 = next(i for i, c in enumerate(cum) if c >= min(prebuf, total))
    t0, q_s = ts[j0], q / BPS
    runs, off, k, silent, refill = [], 0, 0, 0, False
    while off < total and k < 100000:
        due = t0 + k * q_s
        have = by(due)
        avail, ended = have - off, have >= total
        k += 1
        if refill:
            if avail >= prebuf or (ended and avail > 0):
                refill = False
            else:
                silent += 1
                continue
        if avail >= q or (ended and avail > 0):
            n = min(q, avail)
            runs.append((due, off, n))
            off += n
            continue
        silent += 1
        if rule == "pulse":
            if avail > 0:
                runs.append((due, off, avail))
                off += avail
            refill = True
    return runs, silent


def _played_at(runs: list, s: int) -> float:
    i = bisect.bisect_right([r[1] for r in runs], s) - 1
    t, off, _n = runs[i]
    return t + (s - off) / BPS


class _Pocket:
    """Pocket's stream as house-speak's reader shows it: 80 ms frames made at `pace` x real time, from `lag` s in."""

    is_turbo_reader = True

    def __init__(self, pcm: bytes, pace: float, lag: float, frame_b: int = 3840):
        self.pcm, self.pace, self.lag, self.frame_b = pcm, pace, lag, frame_b
        self.t0 = None
        self.pos = 0
        self.headers = {"X-Sample-Rate": str(SR)}

    def start(self) -> None:
        self.t0 = time.time()

    def _made(self, now: float) -> int:
        n = int(max(0.0, now - self.t0 - self.lag) * self.pace * BPS) // self.frame_b * self.frame_b
        return min(len(self.pcm), n)

    def read(self, n: int, timeout_s: float | None = None) -> bytes:
        t_ask = time.time()
        while True:
            if self.pos >= len(self.pcm):
                return b""
            made = self._made(time.time())
            if made > self.pos:
                out = self.pcm[self.pos:min(made, self.pos + n)]
                self.pos += len(out)
                return out
            if timeout_s is not None and time.time() - t_ask > timeout_s:
                raise TimeoutError("pocket starve")
            time.sleep(0.002)

    def close(self) -> None:
        pass


def _line_pcm(pieces=((0.6, 1.0),), seed: int = 17) -> tuple[bytes, list]:
    """Pocket's shape, piece by piece: a near-silent lead-in, then her voice. Random samples, so any 256 bytes of it are
    found once in the player's stream. Returns the line and each piece's end (bytes)."""
    import array
    rnd = random.Random(seed)
    a = array.array("h")
    ends = []
    for quiet_s, voice_s in pieces:
        a.extend(rnd.choice((-3, -2, -1, 1, 2, 3)) for _ in range(int(quiet_s * SR)))
        a.extend(rnd.randint(-9000, 9000) for _ in range(int(voice_s * SR)))
        ends.append(len(a) * 2)
    return a.tobytes(), ends


def _held_line(tmp_path: Path, monkeypatch, flush: bool, low_s: float, hold: "bool | str | None" = True,
               pieces=((0.6, 1.0),), stamps=(1.3,), bufsize: int = -1) -> dict:
    """voice_stream_play itself, on Pocket's stand-in (2.5x real time), piece k stamped `stamps[k]` s after the request,
    paplay replaced by the arrival reader (same pipe, same flags). Returns the reader's stream and arrivals, every row
    house-speak posted for the wall, when the clock was first armed, and the line log."""
    tag = "%s-%d-%s-%d-%d" % ("flush" if flush else "buffered", int(low_s * 1000),
                              hold if isinstance(hold, str) else {True: "hold", False: "nohold", None: "default"}[hold],
                              max(0, bufsize),
                              len(list(tmp_path.iterdir())))
    spec = importlib.util.spec_from_file_location("house_speak_pipe_%s" % tag.replace("-", "_"), SPEAK_PY)
    hs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hs)
    d = tmp_path / tag
    d.mkdir()
    (d / "reader.py").write_text(READER)
    got = d / "got.json"

    if hold is None:
        monkeypatch.delenv("HOUSE_SPEAK_STAMP_HOLD", raising=False)       # the default
    else:
        monkeypatch.setenv("HOUSE_SPEAK_STAMP_HOLD", hold if isinstance(hold, str) else ("1" if hold else "0"))
    monkeypatch.setenv("HOUSE_SPEAK_PIPE_FLUSH", "1" if flush else "0")
    monkeypatch.setenv("HOUSE_SPEAK_SHEET_GAPS", "1")
    pcm, ends = _line_pcm(pieces)
    pocket = _Pocket(pcm, pace=2.5, lag=0.05)
    # the mark of each piece goes out with its last audio (round 17), long before any of these voices is reached
    mc = types.SimpleNamespace(ends=list(ends[:-1]), _said=0, failed=False, mismatch=False, _fell_back=False)
    early = types.SimpleNamespace(reader=pocket, mc=mc, attached=False)
    early.attach = lambda air: setattr(early, "attached", True)
    posts: list = []
    airs: list = []
    armed: list = []
    voice_url, mouth_url = "http://voice.test/stream", "http://127.0.0.1:9/v1/mouth"

    def urlopen(req, timeout=None, **_k):
        url = getattr(req, "full_url", req)
        if url == voice_url:
            pocket.start()

            def stamp():
                for k, at in enumerate(stamps):
                    time.sleep(max(0.0, pocket.t0 + at - time.time()))
                    mc._said = k + 1
            threading.Thread(target=stamp, daemon=True).start()
            return pocket
        if url == mouth_url:
            posts.append(json.loads(req.data))
            return types.SimpleNamespace(close=lambda: None, read=lambda *a: b"")
        raise urllib.error.URLError("nothing but the stand-ins answers in this test")

    real_popen = subprocess.Popen

    class _Sub:
        """house-speak's subprocess, with paplay swapped for the arrival reader (the pipe and its flags unchanged)."""

        def __getattr__(self, k):
            return getattr(subprocess, k)

        def Popen(self, argv, *a, **kw):  # noqa: N802
            if argv and argv[0] == "paplay":
                if bufsize > 0:
                    kw["bufsize"] = bufsize
                p = real_popen([sys.executable, str(d / "reader.py"), str(got)], *a, **kw)
                t = time.time()
                while not Path(str(got) + ".ready").exists() and time.time() - t < 10:
                    time.sleep(0.002)          # up and reading, as paplay is before its first write is played
                return p
            return real_popen(argv, *a, **kw)

    class _Air(hs.MouthAir):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            airs.append(self)

        def feed(self, pcm):
            if not self._armed:
                armed.append((time.time(), bytes(pcm[:256])))
            return super().feed(pcm)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    for name, val in {
        "VOICE_URL": voice_url, "MOUTH_URL": mouth_url, "MOUTH_AIR": True, "TEMPO": 1.0, "_HOUSE": False,
        "STAMP_HOLD_LOW_S": low_s, "subprocess": _Sub(), "MouthAir": _Air,
        "pocket_marks_on": lambda: True, "_piece_clock_for": lambda *a, **k: None,
        "_early_clock_for": lambda *a, **k: early, "_unmute_line_sink": lambda *a, **k: None,
        "pa_yield": lambda *a, **k: False, "_mouth_air": lambda *a, **k: None, "_boost_paplay": lambda *a, **k: None,
        "_speech_lock_cm": lambda *a, **k: contextlib.nullcontext(), "_za": lambda: None,
        "boost_play_pcm": lambda pcm, sr=SR, gain=None: (bytes(pcm), 1.0), "follow_rider": lambda *a, **k: None,
        "sheet_lead_s": lambda: 0.0, "last_pocket_pace": lambda: None,
    }.items():
        monkeypatch.setattr(hs, name, val)
    hs._LINE.clear()
    played, _sr, _buf = hs.voice_stream_play("Call me Ishmael. Some years ago.", "crissa", "stub-sink")
    for air in airs:                                  # rows the drain had not posted when the line closed
        while not air._q.empty():
            posts.append(air._q.get_nowait())
    held = (dict(hs._LINE).get("stamp_hold") or [])
    return {"stream": (d / "got.json.bin").read_bytes(), "arrivals": json.loads(got.read_text()), "posts": posts,
            "line": dict(hs._LINE), "held": held, "lat": hs.MOUTH_LATENCY_MS / 1000.0,
            "hop_s": hs.MOUTH_HOP_MS / 1000.0, "played": played, "pcm": pcm, "ends": ends, "armed": armed}


def _leads(r: dict, runs: list) -> list:
    """For every row that carries her audio: when the server plays it + the sink's latency (the 90 ms the sheet adds)
    minus when the sheet says it is heard. Positive = the mouth is EARLY (round 17's complaint)."""
    out = []
    stream = r["stream"]
    for p in r["posts"]:
        if not (p.get("pcm16") and isinstance(p.get("rms"), list)):
            continue
        b = base64.b64decode(p["pcm16"])
        s0 = stream.find(b[:256])
        assert s0 >= 0, "every posted sample is in the player's stream"
        for i in range(len(p["rms"])):
            heard = _played_at(runs, s0 + i * HOP_B) + r["lat"]
            out.append(heard - (float(p["pts0"]) + i * r["hop_s"]))
    return out


def _check(r: dict, label: str, want_rows: int) -> dict:
    """Every server rule and quantum: silent quanta and the mouth's lead over her audio, row by row."""
    zeros = sum(int(round(h[1] * BPS / HOP_B)) for h in r["held"]) * HOP_B
    assert len(r["stream"]) == 19200 + len(r["pcm"]) + zeros, "the pad, every byte of hers, the held silence in hops"
    out = {}
    for rule in ("pipewire", "pulse"):
        for q in (1024, 2048):                      # PipeWire's 1024 and 2048 quanta at 48 kHz, in this stream's bytes
            runs, silent = _serve(r["arrivals"], q, rule)
            leads = _leads(r, runs)
            assert len(leads) >= want_rows
            out[(rule, q)] = (silent, min(leads), max(leads))
            print("%s, %s q=%d: %d rows, %d silent quanta, mouth ahead of her audio by %s .. %s"
                  % (label, rule, q, len(leads), silent, _ms(min(leads)), _ms(max(leads))))
    return out


def test_a_held_line_stays_on_its_sheet_through_a_real_pipe(tmp_path, monkeypatch):
    """The hold switched on (=1), both pieces held: every row of both pieces is within a frame of her audio, whatever
    the server's rule or quantum, because the sink is never dry and the sheet moved by exactly the silence written."""
    r = _held_line(tmp_path, monkeypatch, flush=True, low_s=0.2, pieces=((0.6, 1.0), (0.4, 0.8)), stamps=(1.3, 3.0))
    print("\nheld [piece, silence s, waited s, at s]: %s" % r["held"])
    assert [h[0] for h in r["held"]] == [1, 2] and r["held"][0][1] > 0.2 and r["held"][1][1] > 0.1, \
        "piece 1 and piece 2 each waited in silence it wrote"
    for (rule, q), (silent, lo, hi) in _check(r, "flushed, low 0.2 s, hold", 130).items():
        assert silent == 0, "the sink never ran dry (%s q=%d)" % (rule, q)
        assert max(abs(lo), abs(hi)) < FRAME_S, "every row within a frame of her audio (%s q=%d)" % (rule, q)


def test_round_18s_default_line_has_no_hold_and_stays_on_its_sheet(tmp_path, monkeypatch):
    """Round 18-19's default, now its switch (HOUSE_SPEAK_STAMP_HOLD=0, round 16's path): nothing held, every row on her
    audio. Round 20's default holds the first piece (test_round_20s_default_holds_the_first_piece_only)."""
    r = _held_line(tmp_path, monkeypatch, flush=True, low_s=0.2, hold=False)
    assert not r["held"]
    for (rule, q), (silent, lo, hi) in _check(r, "flushed, no hold", 70).items():
        assert max(abs(lo), abs(hi)) < FRAME_S, "%s q=%d" % (rule, q)


def test_round_17s_buffered_hold_runs_the_sink_dry_and_her_mouth_leads(tmp_path, monkeypatch):
    """The kill switches put round 17 back (HOUSE_SPEAK_PIPE_FLUSH=0, HOUSE_SPEAK_STAMP_HOLD_LOW_S=0.08): the hold's
    silence reaches the player a pipe buffer at a time (83 ms on 3.12) against an 80 ms margin, the sink goes dry while
    the hold thinks it is fed, and every row after leads her audio by what the server lost."""
    for bufsize in (-1, 8192):                       # this Python's own pipe buffer, and an 8192-byte one
        r = _held_line(tmp_path, monkeypatch, flush=False, low_s=0.08, bufsize=bufsize)
        assert r["held"] and r["held"][0][1] > 0.2
        got = _check(r, "buffered (%s), low 0.08 s, hold (round 17)"
                     % ("Popen's default" if bufsize < 0 else "8192 bytes"), 70)
        assert got[("pipewire", 2048)][0] > 0 and got[("pipewire", 2048)][2] > 0.02, "PipeWire loses a quantum or more"
        assert got[("pulse", 2048)][2] > FRAME_S, "PulseAudio waits to refill and the mouth leads by more than a frame"


@pytest.mark.parametrize("flush", [True, False])
def test_the_first_feed_stamps_only_what_paplay_has(tmp_path, monkeypatch, flush):
    """MouthAir's first-feed correction says "these bytes are in the buffer NOW". A short quiet prefix (under 4 KB, the
    stamp hold's first write when her voice starts soon after the piece) was still in Python's buffer at that moment."""
    r = _held_line(tmp_path, monkeypatch, flush=flush, low_s=0.2 if flush else 0.08, pieces=((0.15, 0.6),),
                   stamps=(0.9,))
    t_arm, head = r["armed"][0]
    s = r["stream"].find(head)
    assert s >= 0
    total, at = 0, None
    for t, n in r["arrivals"]:
        total += n
        if total >= s + len(head):
            at = t
            break
    print("\n%s: the first fed bytes reached the reader %s after the clock was armed on them"
          % ("flushed" if flush else "buffered", _ms(at - t_arm)))
    if flush:
        assert at - t_arm < 0.005, "in paplay's pipe when the clock is armed on them"
    else:
        assert at - t_arm > 0.02, "round 17: stamped while they sat in this process"


# ---- the switches and where they sit ---------------------------------------------------------------------------------

def _take(names: tuple, ns: dict) -> dict:
    got = [n for n in ast.parse(SPEAK).body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert sorted(n.name for n in got) == sorted(names)
    exec(compile(ast.Module(body=got, type_ignores=[]), "src", "exec"), ns)
    return ns


def test_round_20s_default_holds_the_first_piece_only(tmp_path, monkeypatch):
    """Round 20's hold (HOUSE_SPEAK_STAMP_HOLD=first, the default rounds 20-27; off by default since round 28): the first piece, stamped 1.3 s after the request,
    waits in silence it writes; the second (stamped at 3.0 s) is not waited for. Every row of both pieces stays within a
    frame of her audio, whatever the server's rule or quantum: the hold only moves the sheet by what it writes."""
    r = _held_line(tmp_path, monkeypatch, flush=True, low_s=0.2, hold="first", pieces=((0.6, 1.0), (0.4, 0.8)),
                   stamps=(1.3, 3.0))
    print("\nheld [piece, silence s, waited s, at s]: %s" % r["held"])
    assert [h[0] for h in r["held"]] == [1] and r["held"][0][1] > 0.2, "the first piece alone waited, in silence"
    assert r["line"].get("stamp_hold_s", 0) < 0.6 + 1e-6, "capped at STAMP_HOLD_FIRST_S"
    for (rule, q), (silent, lo, hi) in _check(r, "flushed, low 0.2 s, first piece", 130).items():
        assert silent == 0, "the sink never ran dry (%s q=%d)" % (rule, q)
        assert max(abs(lo), abs(hi)) < FRAME_S, "every row within a frame of her audio (%s q=%d)" % (rule, q)


def test_the_switches(monkeypatch):
    ns = _take(("pipe_flush_on", "stamp_hold_on", "_FlushedPipe", "_flushed"),
               {"os": os, "_HOUSE": False, "STAMP_HOLD_S": 1.0, "STAMP_HOLD_NEXT_S": 0.5, "STAMP_HOLD_FIRST_S": 0.6})
    for k in ("HOUSE_SPEAK_PIPE_FLUSH", "HOUSE_SPEAK_STAMP_HOLD"):
        monkeypatch.delenv(k, raising=False)
    assert ns["pipe_flush_on"]() is True and ns["stamp_hold_on"]() == "", "flush on; round 28: no hold by default"
    monkeypatch.setenv("HOUSE_SPEAK_STAMP_HOLD", "first")
    assert ns["stamp_hold_on"]() == "first", "=first: round 20-27's first-piece hold"
    monkeypatch.setenv("HOUSE_SPEAK_STAMP_HOLD", "1")
    assert ns["stamp_hold_on"]() == "all"
    monkeypatch.setenv("HOUSE_SPEAK_STAMP_HOLD", "0")
    assert ns["stamp_hold_on"]() == "", "=0: round 18-19's path"
    monkeypatch.setenv("HOUSE_SPEAK_STAMP_HOLD", "1")
    ns["_HOUSE"] = True
    assert ns["stamp_hold_on"]() == "", "the house never holds her voice"
    sentinel = object()
    monkeypatch.setenv("HOUSE_SPEAK_PIPE_FLUSH", "0")
    assert ns["pipe_flush_on"]() is False and ns["_flushed"](sentinel) is sentinel, "the kill hands the pipe back as is"
    monkeypatch.setenv("HOUSE_SPEAK_PIPE_FLUSH", "1")
    w = ns["_flushed"](sentinel)
    assert isinstance(w, ns["_FlushedPipe"]) and ns["_flushed"](w) is w and ns["_flushed"](None) is None
    assert 'STAMP_HOLD_LOW_S = float(os.environ.get("HOUSE_SPEAK_STAMP_HOLD_LOW_S", "0.2") or 0)' in SPEAK


def test_every_pipe_her_voice_goes_into_is_flushed():
    stream = SPEAK.split("def voice_stream_play", 1)[1].split("\ndef ", 1)[0]
    assert "player = _pipe_player(" in stream and "subprocess.Popen(" not in stream, "paplay through the one helper"
    tempo = SPEAK.split("def _tempo_filter", 1)[1].split("\ndef ", 1)[0]
    assert "p.stdin = _flushed(p.stdin)" in tempo
    player = SPEAK.split("def _pipe_player", 1)[1].split("\ndef ", 1)[0]
    assert "p.stdin = _flushed(p.stdin)" in player
    chunks = SPEAK.split("def _play_chunks", 1)[1].split("\n    def ", 1)[0]
    assert "player.stdin.flush()" in chunks, "Audio8's stream already flushed every chunk"
    # every other write into a Popen pipe in the file: the three above, nothing else
    pipes = re.findall(r"stdin=subprocess\.PIPE", SPEAK)
    assert len(pipes) == 3, "paplay (_pipe_player), the tempo stage, Audio8's chunks"


def test_the_flushed_pipe_is_the_pipe_to_its_callers(tmp_path):
    ns = _take(("pipe_flush_on", "_FlushedPipe", "_flushed", "_pipe_player"), {"os": os, "subprocess": subprocess})
    p = ns["_pipe_player"]([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"])
    assert p.stdin.write(b"abc") == 3 and p.stdin.fileno() > 2 and not p.stdin.closed
    p.stdin.close()
    assert p.stdin.closed and p.wait(timeout=20) == 0
    gone = ns["_pipe_player"]([sys.executable, "-c", "pass"])
    gone.wait(timeout=20)
    with pytest.raises(BrokenPipeError):
        for _ in range(64):
            gone.stdin.write(b"x" * 4096)            # a player that has gone raises at the write, as before at a flush
    with contextlib.suppress(Exception):
        gone.stdin.close()


def test_the_ear_bridge_speaks_on_its_own_python(monkeypatch):
    assert "SPEAK_PYTHON = os.environ.get(\"EAR_BRIDGE_SPEAK_PYTHON\", \"\").strip() or sys.executable or \"python3\"" \
        in BRIDGE
    assert BRIDGE.count("[SPEAK_PYTHON, str(HOUSE_SPEAK), \"--pa-yield\"]") == 1
    assert "                    SPEAK_PYTHON,\n                    str(HOUSE_SPEAK)," in BRIDGE
    assert not re.search(r'"python3",\s*\n?\s*str\(HOUSE_SPEAK\)', BRIDGE), "no line of hers on the PATH's python3"
