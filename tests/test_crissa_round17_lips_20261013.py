"""Round 17, her lips in conversation (2026-10-13, .101 RESULTS-20261013-0630, ASK-20261013-0405 addendum 05:57).

  * Pocket's piece mark goes out with its piece's last audio (one sample held back carries it), not with the next
    piece's first frame: measured 103-159 ms sooner on a free CPU, 296-444 ms on a busy one. The PCM is unchanged; the
    last mark stays on the closing chunk. Kill POCKET_MARKS_NOW=0.
  * A piece is noted once: the worker and the end of the stream both committed, and a one-piece line logged its piece
    twice, which made the next line's round-13 wait hold her first word 0.8 s for nothing. Kill HOUSE_SPEAK_PIECE_ONCE=0.
  * Her voice waits for its stamp, in silence she already has: before the first voiced hop of an unstamped piece is
    written, house-speak waits; when the sink is about to run dry it writes one hop of silence at a time and moves the
    sheet by exactly that. Capped; pack only; kill HOUSE_SPEAK_STAMP_HOLD=0.
  * The clock counts her voice, not the piece's first sample (Pocket's lead-in quiet), and says the silence it held.
    Kill HOUSE_SPEAK_VOICE_CLOCK=0.

No player, voice or aligner runs here: Pocket's handler runs on loopback with a stand-in model, house-speak's pieces
are taken from its source and run on stand-ins."""
from __future__ import annotations

import array
import ast
import base64
import importlib.util
import json
import math
import os
import queue
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
SPEAK = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")
CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
POCKET = (ROOT / "modules" / "voice" / "scripts" / "crissa-pocket.py").read_text(encoding="utf-8")
SR = 24000
HOP = 500                      # one semiframe at 24 kHz (20.833 ms)


def _take(src: str, names: tuple, ns: dict) -> dict:
    tree = ast.parse(src)
    got = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert sorted(n.name for n in got) == sorted(names)
    exec(compile(ast.Module(body=got, type_ignores=[]), "src", "exec"), ns)
    return ns


# ---- Pocket: the mark goes out with its piece's last audio ----------------------------------------------------------

def _pocket(monkeypatch, now: bool):
    sys.path.insert(0, str(ROOT / "modules" / "voice"))
    spec = importlib.util.spec_from_file_location("crissa_pocket_r17", ROOT / "modules" / "voice" / "scripts" /
                                                  "crissa-pocket.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    texts = ["First piece.", "Second piece, made slowly.", "Third."]
    monkeypatch.setattr(mod, "chunks_for", lambda text, a=None, b=None, st=None: list(texts))

    def frames(chunk, name):
        i = texts.index(chunk)
        if i:
            time.sleep(0.4)                       # the next piece's start cost: its first frame is 0.4 s away
        for k in range(3):
            yield bytes([i + 1, k + 1]) * 960     # three 80 ms frames of 960 samples each
    monkeypatch.setattr(mod, "frames", frames)
    monkeypatch.setitem(mod.RT, "settings", dict(mod.DEFAULTS, emit="frames"))
    monkeypatch.setattr(mod, "MARKS_NOW", now)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, texts


def _stream(port: int):
    """[(arrived, kind, payload)] of one marked /stream: kind 'pcm' (bytes) or 'mark' (index, end, text)."""
    body = json.dumps({"text": "x", "voice": "crissa", "marks": True}).encode()
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(b"POST /stream HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n"
              % len(body) + body)
    f = s.makefile("rb")
    while f.readline() not in (b"\r\n", b"\n", b""):
        pass
    out, total = [], 0
    while True:
        line = f.readline()
        now = time.time()
        if not line:
            break
        size = int(line.split(b";", 1)[0].strip() or b"0", 16)
        cur = None
        for part in line.strip().split(b";")[1:]:
            k, _, v = part.partition(b"=")
            if k == b"piece-end":
                cur = int(v)
            elif k == b"text" and cur is not None:
                out.append((now, "mark", (cur, total, urllib.parse.unquote(v.decode()))))
                cur = None
        if size == 0:
            break
        data = f.read(size)
        f.read(2)
        total += size
        out.append((time.time(), "pcm", data))
    s.close()
    return out


def test_pocket_sends_a_piece_mark_with_its_last_audio_and_the_audio_is_unchanged(monkeypatch):
    srv, texts = _pocket(monkeypatch, now=True)
    try:
        got = _stream(srv.server_address[1])
    finally:
        srv.shutdown()
    pcm = b"".join(p for _t, k, p in got if k == "pcm")
    want = b"".join(bytes([i + 1, k + 1]) * 960 for i in range(3) for k in range(3))
    assert pcm == want, "the same bytes in the same order"
    marks = [(t, p) for t, k, p in got if k == "mark"]
    assert [m[1][0] for m in marks] == [0, 1, 2] and [m[1][2] for m in marks] == texts
    piece0_end = 3 * 1920
    t_last0 = max(t for t, k, p in got if k == "pcm" and sum(len(q) for tt, kk, q in got
                                                               if kk == "pcm" and tt <= t) <= piece0_end)
    t_next = min(t for t, k, p in got if k == "pcm" and p[0] == 2)
    assert marks[0][0] - t_last0 < 0.2 and marks[0][0] < t_next, "the mark, then the next piece's audio"
    assert marks[0][1][1] == piece0_end - 2, "its end named one sample early (the sample that carried it)"
    assert marks[2][1][1] == len(want), "the last piece's mark closes the stream at the line's whole length"


def test_with_the_switch_off_a_mark_waits_for_the_next_pieces_first_frame(monkeypatch):
    srv, _texts = _pocket(monkeypatch, now=False)
    try:
        got = _stream(srv.server_address[1])
    finally:
        srv.shutdown()
    marks = [(t, p) for t, k, p in got if k == "mark"]
    t_next = min(t for t, k, p in got if k == "pcm" and p[0] == 2)
    assert abs(marks[0][0] - t_next) < 0.05, "round 16: on the next chunk-size line"
    assert marks[0][1][1] == 3 * 1920


def test_the_reader_and_the_mark_clock_take_the_early_mark_whole(monkeypatch):
    import turbo_stream
    srv, texts = _pocket(monkeypatch, now=True)
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/stream" % srv.server_address[1],
                                     data=json.dumps({"text": "x", "voice": "crissa", "marks": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        ends = []
        r = turbo_stream.TurboReader(resp, on_mark=lambda i, end, t: ends.append((i, end, t)))
        data = b""
        while True:
            x = r.read(4096, timeout_s=5.0)
            if not x:
                break
            data += x
    finally:
        srv.shutdown()
    assert len(data) == 9 * 1920 and ends[-1][1] == len(data)
    assert [e[0] for e in ends] == [0, 1, 2] and ends[0][1] == 3 * 1920 - 2


def test_pocket_marks_now_is_a_switch_and_only_with_marks():
    assert 'MARKS_NOW = os.environ.get("POCKET_MARKS_NOW", "1")' in POCKET
    assert "now_marks = marks_on and MARKS_NOW" in POCKET
    assert "if now_marks and held and int(i) < n_chunks - 1:" in POCKET


# ---- house-speak: a piece noted once ---------------------------------------------------------------------------------

class _Air:
    """MouthAir's surface for the mark clock and the stamp hold, on the sink's own clock."""

    def __init__(self, sr=SR):
        self.sr, self.hop = sr, HOP
        self.air_at = time.time() + 0.1           # the pad still playing
        self.samples, self.gap_s, self._gaps = 0, 0.0, []
        self._armed, self._q = False, queue.Queue()
        self.fed = bytearray()
        self.clocks = []

    def set_piece_clock(self, pcm, text, words, partial, tempo=1.0, note=""):
        self.clocks.append(text)
        return True

    def _gap_at(self, hop_i):
        g = 0.0
        for i, gs in self._gaps:
            if hop_i >= i:
                g = gs
        return g

    def sheet_end(self):
        return self.air_at + self.gap_s + self.samples / float(self.sr)

    def feed(self, pcm):
        if not self._armed:
            self._armed = True
            self.air_at = max(self.air_at, time.time() + 0.09)
        self.fed += pcm
        self.samples = (len(self.fed) // 2 // self.hop) * self.hop

    def _post_lead_prefix(self):
        pass

    def _flush(self):
        pass


def _speak_ns(**over) -> dict:
    ns = {"os": os, "sys": sys, "time": time, "json": json, "base64": base64, "urllib": urllib, "threading": threading,
          "queue": queue, "array": array, "math": math, "_HOUSE": False, "_LINE": {}, "TEMPO": 1.0,
          "MOUTH_HOP_MS": 1000.0 / 48, "MOUTH_LATENCY_MS": 90.0, "PIECE_ONCE": True, "STAMP_VOICE_RMS": 0.01,
          "STAMP_HOLD_S": 1.0, "STAMP_HOLD_NEXT_S": 0.5, "STAMP_HOLD_LOW_S": 0.08, "MOUTH_ALIGN_URL": "http://0/",
          "STAMP_HOLD_FIRST_S": 0.6,
          "sheet_lead_s": lambda: 0.104, "_AIR_WHO": "crissa", "_CLOCK_HOW": "", "_AIR_TEXT": ""}
    ns.update(over)
    return _take(SPEAK, ("_PieceClock", "_MarkClock", "_hop_rms", "_voice_onset_s", "stamp_hold_on",
                         "stamp_hold_pre_s", "_StampHold", "line_clock_note"), ns)


def test_a_piece_is_noted_once_when_the_worker_and_the_stream_end_commit_together():
    ns = _speak_ns()
    for _ in range(30):
        ns["_LINE"].clear()
        mc = ns["_MarkClock"](SR)
        mc.texts, mc.ends, mc.words = ["Yeah, sure."], [48000], {0: [{"w": "yeah", "a": 0.5, "b": 0.7}]}
        mc.air = _Air()
        go = threading.Barrier(2)

        def both():
            go.wait()
            mc.commit()
        ts = [threading.Thread(target=both) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert [p[0] for p in ns["_LINE"]["pieces"]] == [1], "one line per stamped piece"


# ---- house-speak: her voice waits for its stamp ---------------------------------------------------------------------

def _line(quiet_s=0.6, voice_s=0.5):
    """Pocket's shape: a near-silent lead-in (tiny noise, never all-zero) then her voice (a 220 Hz tone)."""
    a = array.array("h", [3 if (i % 7) else -3 for i in range(int(quiet_s * SR))])
    a.extend(int(6000 * math.sin(2 * math.pi * 220 * i / SR)) for i in range(int(voice_s * SR)))
    return a.tobytes()


class _Sink:
    def __init__(self):
        self.writes = []

    def write(self, b):
        self.writes.append((time.time(), bytes(b)))


class _MC:
    def __init__(self, ends=(), said=0):
        self.ends, self._said = list(ends), said
        self.failed = self.mismatch = self._fell_back = False


def _run_hold(ns, mc, stamp_after=None, pcm=None, blocks=4800):
    """blocks: the stream's read size (house-speak's prefill is ONE block holding her first voice)."""
    pcm = pcm if pcm is not None else _line()
    blocks = blocks or len(pcm)
    air, sink = _Air(), _Sink()
    h = ns["_StampHold"](mc, air, sink, SR)
    if stamp_after is not None:
        def stamp():
            time.sleep(stamp_after)
            mc._said = max(mc._said, 1)
        threading.Thread(target=stamp, daemon=True).start()
    t0 = time.time()
    for i in range(0, len(pcm), blocks):
        h.write(pcm[i:i + blocks], pcm[i:i + blocks])
    zero = b"\x00" * (HOP * 2)
    return {"air": air, "sink": sink, "t0": t0, "pcm": pcm, "hold": h,
            "written": b"".join(b for _t, b in sink.writes if b != zero),
            "zeros": sum(1 for _t, b in sink.writes if b == zero)}


def test_her_voice_is_written_only_after_its_stamp_and_nothing_is_lost(monkeypatch):
    monkeypatch.setenv("HOUSE_MOUTH_SENT_PAD", "6")
    ns = _speak_ns()
    r = _run_hold(ns, _MC(), stamp_after=0.7, blocks=None)
    assert r["written"] == r["pcm"], "every byte of hers, in order; only whole hops of silence between"
    on = ns["_voice_onset_s"](r["pcm"], SR)
    first_voice_at = min(t for t, b in r["sink"].writes if b != b"\x00" * (HOP * 2)
                         and ns["_hop_rms"](b, 0, len(b)) >= 0.01)
    assert first_voice_at >= r["t0"] + 0.65, "her voice waited for the stamp"
    assert r["zeros"] > 0 and abs(r["air"].gap_s - r["zeros"] * HOP / SR) < 1e-9, "the sheet moved by exactly that"
    cut_hop = r["air"]._gaps[-1][0]
    assert cut_hop * HOP / SR <= on - ns["stamp_hold_pre_s"]() + 1.0 / 48, "the hold sits before her mouth forms"
    held = ns["_LINE"]["stamp_hold"][0]
    assert held[0] == 1 and held[1] == pytest.approx(r["zeros"] * HOP / SR, abs=0.002)
    assert held[3] == pytest.approx(cut_hop * HOP / SR, abs=0.001)


def test_a_stamped_piece_and_a_failed_clock_are_not_held():
    ns = _speak_ns()
    r = _run_hold(ns, _MC(said=1))
    assert r["zeros"] == 0 and r["written"] == r["pcm"] and not ns["_LINE"].get("stamp_hold")
    mc = _MC()
    mc.failed = True
    r = _run_hold(ns, mc)
    assert r["zeros"] == 0 and r["written"] == r["pcm"]


def test_a_stamp_that_never_comes_costs_at_most_the_cap():
    ns = _speak_ns(STAMP_HOLD_S=0.2)
    r = _run_hold(ns, _MC())
    assert r["written"] == r["pcm"]
    assert 0 < r["zeros"] * HOP / SR <= 0.2 + 1e-9


def test_a_later_piece_waits_at_its_own_voice_with_its_own_cap():
    ns = _speak_ns(STAMP_HOLD_NEXT_S=0.15)
    one = _line(0.3, 0.4)
    pcm = one + _line(0.5, 0.4)
    mc = _MC(ends=[len(one)], said=1)               # piece 1 stamped, piece 2 never
    r = _run_hold(ns, mc, pcm=pcm)
    assert r["written"] == pcm
    assert ns["_LINE"]["stamp_hold"][0][0] == 2 and r["zeros"] * HOP / SR <= 0.15 + 1e-9
    assert r["air"]._gaps[-1][0] * HOP * 2 >= len(one), "inside piece 2, before its voice"


def test_the_voice_onset_is_found_on_the_sheets_hops():
    ns = _speak_ns()
    assert ns["_voice_onset_s"](_line(0.6, 0.3), SR) == pytest.approx(0.6, abs=1000.0 / 48 / 1000.0 + 1e-9)
    assert ns["_voice_onset_s"](_line(0.4, 0.0), SR) is None


def test_the_hold_is_a_packs_and_the_switch_puts_round_16_back(monkeypatch):
    # off by default 2026-10-13 (round 17 on .101: her mouth led her voice in conversation); round 20 (2026-10-14)
    # holds a line's first piece by default ("first"), every piece at =1 (tests/test_voice_pipe_flush_20261013.py)
    # round 28 (.101 2026-10-17): off by default again ("hold off, perfect"); =first is round 20-27's
    monkeypatch.delenv("HOUSE_SPEAK_STAMP_HOLD", raising=False)
    assert _speak_ns()["stamp_hold_on"]() == ""
    monkeypatch.setenv("HOUSE_SPEAK_STAMP_HOLD", "first")
    assert _speak_ns()["stamp_hold_on"]() == "first"
    monkeypatch.setenv("HOUSE_SPEAK_STAMP_HOLD", "1")
    assert _speak_ns()["stamp_hold_on"]() == "all"
    assert _speak_ns(_HOUSE=True)["stamp_hold_on"]() == "", "the house never holds her voice"
    monkeypatch.setenv("HOUSE_SPEAK_STAMP_HOLD", "0")
    assert _speak_ns()["stamp_hold_on"]() == ""
    assert "    hold_on = mc is not None and stamp_hold_on()" in SPEAK
    assert "    if mc is not None and not _HOUSE and not hold_on:\n        _pocket_first_wait(" in SPEAK
    assert "hold = _StampHold(mc, air, sink_in, sr, hold_on) if (hold_on and air is not None and filt is None) else None" in SPEAK
    assert SPEAK.count("hold.write(") == 2, "the prefill and the stream, the only two writes of her voice"


# ---- the clock counts her voice ----------------------------------------------------------------------------------

def test_the_clock_note_counts_her_voice_and_the_silence_held(monkeypatch):
    line = {"engine": "pocket", "piece_marks": True, "chars": 23, "stamp_hold_s": 0.375,
            "pieces": [[1, 0.73, 0.0, 2.16], [2, 2.6, 2.16, 4.0]], "piece_voice": [[1, 1.1], [2, 2.5]]}
    ns = _speak_ns(_LINE=line, _CLOCK_HOW="aligned by sentence (2 of 2)")
    k = ns["line_clock_note"]()
    assert k["early_pieces"] == 0 and k["late_s"] == 0.73, "round 12's count from the first sample stands"
    assert k["voice_early"] == 1 and k["voice_late_s"] == 0.1 and k["held_s"] == 0.375
    assert len(k) <= 16, "the mailbox keeps 16 keys of a clock"
    monkeypatch.setenv("HOUSE_SPEAK_VOICE_CLOCK", "0")
    k = _speak_ns(_LINE=line)["line_clock_note"]()
    assert "voice_early" not in k and "held_s" not in k


def test_the_player_line_says_her_voice_and_the_held_silence():
    import mouth_mailbox

    class _Lipsync:
        @staticmethod
        def offset_ms():
            return 0.0
    mb = mouth_mailbox.MouthMailbox()
    clock = {"how": "aligned by sentence (1 of 1, 148 ms last)", "first_s": 0.73, "after_s": 0.73, "lead_ms": 104.2,
             "engine": "pocket", "marks": True, "chars": 23, "pieces": 1, "early_pieces": 0, "late_s": 0.73,
             "late_piece": 1, "voice_early": 1, "voice_late_s": 0.0, "held_s": 0.375}
    mb.ingest({"air_at": 5.0, "air_until": 9.0, "clock": clock})
    ns = _take(CONDUCTOR, ("lip_clock_line",), {"MAILBOX": mb, "IS_BENCH": False, "MOUTH_SHEET_LEAD_MS": 20.833,
                                                 "SYNC_ZERO_MS": 83.333, "crissa_lipsync": _Lipsync})
    out = ns["lip_clock_line"]({"air_at": 5.0, "rows": 100, "skipped": 4})
    assert "0 of 1 pieces stamped before they played, the latest spoke 0.73s unstamped (piece 1)" in out
    assert "1 of 1 stamped before her voice in them; 0.375s of silence held for stamps" in out
    old = {k: v for k, v in clock.items() if k not in ("voice_early", "voice_late_s", "held_s")}
    mb.ingest({"air_at": 6.0, "air_until": 9.0, "clock": old})
    out = ns["lip_clock_line"]({"air_at": 6.0, "rows": 100, "skipped": 4})
    assert "her voice" not in out and "held for stamps" not in out, "a round-16 speaker's clock reads as before"
