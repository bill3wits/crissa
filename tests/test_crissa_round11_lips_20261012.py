"""Round 11, the .101 lips (2026-10-12): "maybe three or five lips movement at all for entire passage", "lips are
trying but not close", `patch=0` in every speed window while she spoke (kit crissa-604f9987).

  * The player's speaking paint took 55-77 ms p50 on .101 (a film frame is 41.7 ms), so the feed carried 12-16
    pictures a second while she spoke, the desk Wall never saw two pictures of one film frame (no patch) and two of
    every three mouth rows were never painted. 44 % of that paint was numpy reducing a size-3 last axis over the whole
    frame: lib/fast_reduce gives the same answers from the channel planes, on in a pack, off on the house.
  * A Pocket line was aligned only once Pocket had made all of it (13.75 s into a 25.7 s answer): Pocket now names
    each chunk's end in its stream (a chunk extension) and house-speak stamps each chunk as it arrives (_MarkClock).
  * The player's log names each line's lip clock (house-speak's last post carries it; lip_clock_line prints it).

No player, voice or aligner runs here: the functions are taken from the scripts' source or run on stand-ins."""
from __future__ import annotations

import ast
import http.client
import importlib
import importlib.util
import io
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import fast_reduce  # noqa: E402

SPEAK = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")
CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def _take(src: str, names: tuple, ns: dict) -> dict:
    tree = ast.parse(src)
    got = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert sorted(n.name for n in got) == sorted(names)
    exec(compile(ast.Module(body=got, type_ignores=[]), "src", "exec"), ns)
    return ns


def _method(src: str, cls: str, name: str, ns: dict):
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == cls:
            for m in n.body:
                if isinstance(m, ast.FunctionDef) and m.name == name:
                    exec(compile(ast.Module(body=[m], type_ignores=[]), "src", "exec"), ns)
                    return ns[name]
    raise AssertionError("%s.%s not found" % (cls, name))


def _tile_frame(seed: int = 3, h: int = 400, w: int = 736) -> np.ndarray:
    """A warped tile as the paint has it: zeros but a mouth-sized patch, with a few black pixels inside it."""
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w, 3), np.uint8)
    out[214:262, 330:418] = rng.integers(0, 256, (48, 88, 3), dtype=np.uint8)
    out[230:234, 350:360] = 0
    out[240, 400] = (5, 4, 3)                      # sum 12: not "> 12"
    return out


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(fast_reduce, "ON", True)
    return fast_reduce


# ---- the reductions ------------------------------------------------------------------------------------------------

def test_each_fast_reduction_is_exactly_the_expression_it_replaces(fast):
    for seed in range(4):
        w = _tile_frame(seed)
        assert np.array_equal(fast.any_channel(w), w.max(axis=2) > 0)
        assert np.array_equal(fast.sum_gt(w, 12), w.sum(axis=2) > 12)
        assert np.array_equal(fast.sum_gt_f32(w, 12.0), w.astype(np.float32).sum(axis=2) > 12.0)
        m = (w.sum(axis=2) > 12).astype(np.uint8)
        ys, xs = np.nonzero(m)
        assert fast.nonzero_extent(m) == (ys.min(), ys.max(), xs.min(), xs.max())
    z = np.zeros((40, 50, 3), np.uint8)
    assert not fast.any_channel(z).any() and fast.nonzero_extent(z[..., 0]) is None
    f = np.random.default_rng(1).random((20, 30, 3)).astype(np.float32) * 10
    assert np.array_equal(fast.sum_gt_f32(f, 12.0), f.sum(axis=2) > 12.0), "a float picture keeps today's sum"
    assert np.array_equal(fast.any_channel(f), f.max(axis=2) > 0)
    b = np.zeros((10, 12), bool)
    b[3, 4] = b[7, 9] = True
    assert fast.nonzero_extent(b) == (3, 7, 4, 9)


def test_fast_reductions_are_a_packs_and_the_switch_forces_them(monkeypatch):
    import crissa_home
    monkeypatch.delenv("HOUSE_MOUTH_FAST_REDUCE", raising=False)
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert fast_reduce._on() is False, "the house paints as it did until its restart window, and it is not asked to"
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    assert fast_reduce._on() is True
    monkeypatch.setenv("HOUSE_MOUTH_FAST_REDUCE", "0")
    assert fast_reduce._on() is False
    monkeypatch.setenv("HOUSE_MOUTH_FAST_REDUCE", "1")
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert fast_reduce._on() is True


POSE = ROOT / "display" / "crissa-poses"


def _real():
    """A real paint's inputs (test_composite_roi's): clip-work frame 0's head, a harvest tile warped onto it."""
    import cv2
    from head_frame import CLIP_H, CLIP_W, unpack, warp_affine_2x3
    frames = json.loads((POSE / "clip-work.head.json").read_text())["frames"]
    dest = unpack(frames[0])
    img = cv2.imread(str(POSE / "hub-down.png"))
    if dest is None or img is None:
        pytest.skip("the work loop's head or hub is missing")
    img = cv2.resize(img, (CLIP_W, CLIP_H))
    src = tile = None
    tiles = POSE / "visemes" / "phoneme-map" / "tiles"
    for e in json.loads((POSE / "visemes" / "phoneme-map" / "map.json").read_text())["entries"]:
        name = Path(str(e.get("tile") or "")).name                # the first harvest tile on disk with its head
        if "harvest" in name and e.get("head") and (tiles / name).is_file():
            src, tile = unpack(e.get("head")), cv2.imread(str(tiles / name))
            if src is not None and tile is not None:
                break
    if src is None or tile is None:
        pytest.skip("no harvest tile with a head on disk")
    M = warp_affine_2x3(src, dest)
    warped = cv2.warpAffine(tile, M, (CLIP_W, CLIP_H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    return img, warped, tuple(float(x) for x in dest["mouth"]), dest, src, tile, M


def _both(monkeypatch, fn):
    monkeypatch.setattr(fast_reduce, "ON", False)
    a = fn()
    monkeypatch.setattr(fast_reduce, "ON", True)
    b = fn()
    return a, b


def test_the_paint_is_bit_identical_with_the_fast_reductions(monkeypatch):
    import pose_mouth as PM
    base, warped, box, dest, _, _, _ = _real()
    for roi in (True, False):
        a0, a1 = _both(monkeypatch, lambda: PM.square_alpha(warped, dest, roi=roi))
        assert a0.dtype == a1.dtype and np.array_equal(a0, a1)
        for hole in ("arch", "ellipse"):
            for cover_alpha in (False, True):
                o0, o1 = _both(monkeypatch, lambda: PM.composite_stamp(base.copy(), box, warped, dest, hole=hole,
                                                                        roi=roi, cover_alpha=cover_alpha))
                assert o0.dtype == o1.dtype and np.array_equal(o0, o1), (roi, hole, cover_alpha)
    j0, j1 = _both(monkeypatch, lambda: PM.hide_clip_jaw(base, box, cover=warped.max(axis=2) > 0))
    assert np.array_equal(j0, j1)
    lb0, lb1 = _both(monkeypatch, lambda: PM.live_bbox(warped, pad=3))
    assert lb0 == lb1


def test_the_nose_pin_is_identical_with_the_fast_reductions(monkeypatch):
    import head_frame as HF
    base, _, _, dest, src, tile, M = _real()
    r0, r1 = _both(monkeypatch, lambda: HF.pin_nose_pixel(M, tile, src, dest, base))
    assert np.array_equal(r0[0], r1[0]) and r0[1] == r1[1] and r0[2] == r1[2]


def test_the_neck_cut_is_worked_out_only_where_the_square_paints_and_gives_the_same_alpha(monkeypatch):
    ns = {"np": np, "fast_reduce": fast_reduce, "MOUTH_SCALE": 1.0, "MOUTH_NECK_MARGIN": 0.08, "MOUTH_FACE_HALF": 0.55}
    neck = _method(CONDUCTOR, "Playhead", "_neck_cut_alpha", ns)
    base, warped, box, dest, src, tile, M = _real()
    for rec, m in (({"chin": [float(tile.shape[1]) / 2, float(tile.shape[0]) - 4]}, M), ({}, None)):
        a0, a1 = _both(monkeypatch, lambda: neck(None, warped, dest, rec, m, "cheeks", 0.28, 0.18))
        assert a0.dtype == a1.dtype == np.float32 and np.array_equal(a0, a1) and a1.any()
    empty = np.zeros_like(warped)
    a0, a1 = _both(monkeypatch, lambda: neck(None, empty, dest, {}, None, "cheeks", 0.28, 0.18))
    assert np.array_equal(a0, a1) and not a1.any()


def test_the_three_paint_modules_use_the_fast_reductions_and_nothing_else_changed_in_them():
    pm = (ROOT / "lib" / "pose_mouth.py").read_text()
    hf = (ROOT / "lib" / "head_frame.py").read_text()
    assert "warped.max(axis=2) > 0" not in pm, "both live-tile tests go through fast_reduce.any_channel"
    assert ".astype(np.float32).sum(axis=2) > 12.0" not in pm
    assert "have = warped.sum(axis=2) > 12" not in hf
    assert "import fast_reduce" in CONDUCTOR and "fast_reduce.nonzero_extent((a != 0)" in CONDUCTOR


# ---- Pocket's piece marks ------------------------------------------------------------------------------------------

def _marked_stream(pieces: list, ext_at: dict, tail: str = "") -> bytes:
    """A chunked body: pieces of PCM, ext_at[i] = the extension written on chunk i's size line, tail on the last."""
    out = b""
    for i, p in enumerate(pieces):
        out += b"%X" % len(p) + ext_at.get(i, b"") + b"\r\n" + p + b"\r\n"
    return out + b"0" + tail.encode() + b"\r\n\r\n"


def _ext(i: int, text: str) -> bytes:
    return b";piece-end=%d;text=%s" % (i, urllib.parse.quote(text, safe="").encode())


class _Resp:
    def __init__(self, raw: bytes, headers: dict | None = None):
        self.fp = io.BytesIO(raw)
        self.headers = headers or {"Transfer-Encoding": "chunked"}

    def close(self):
        pass


def test_the_reader_hands_each_piece_mark_its_end_and_text_and_the_audio_is_unchanged():
    import turbo_stream
    a, b, c, d = b"\x01\x00" * 100, b"\x02\x00" * 50, b"\x03\x00" * 80, b"\x04\x00" * 30
    raw = _marked_stream([a, b, c, d], {2: _ext(0, "Sure, here is how; the morning = looks.")},
                         tail=_ext(1, "The kitchen lights came on at six.").decode())
    got = []
    r = turbo_stream.TurboReader(_Resp(raw), on_mark=lambda i, end, t: got.append((i, end, t)))
    data = b""
    while True:
        x = r.read(64, timeout_s=2.0)
        if not x:
            break
        data += x
    assert data == a + b + c + d
    assert got == [(0, len(a) + len(b), "Sure, here is how; the morning = looks."),
                   (1, len(a) + len(b) + len(c) + len(d), "The kitchen lights came on at six.")]
    plain = turbo_stream.TurboReader(_Resp(raw))                 # no on_mark: the marks are ignored
    while plain.read(4096, timeout_s=2.0):
        pass
    assert plain.marks == 0 and plain.total == len(data)


def test_an_http_client_that_knows_nothing_of_the_marks_reads_the_same_audio():
    pcm = [b"\x05\x00" * 40, b"\x06\x00" * 40]
    body = _marked_stream(pcm, {1: _ext(0, "one two")}, tail=_ext(1, "three").decode())
    head = b"HTTP/1.1 200 OK\r\nContent-Type: audio/L16\r\nTransfer-Encoding: chunked\r\n\r\n"

    class Sock:
        def makefile(self, *a, **k):
            return io.BytesIO(head + body)

    resp = http.client.HTTPResponse(Sock())
    resp.begin()
    assert resp.read() == b"".join(pcm)


def _pocket_server():
    sys.path.insert(0, str(ROOT / "modules" / "voice"))
    spec = importlib.util.spec_from_file_location("crissa_pocket_r11", ROOT / "modules" / "voice" / "scripts" /
                                                  "crissa-pocket.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pocket_marks_each_chunk_after_its_last_audio_and_before_the_next(monkeypatch):
    srv = _pocket_server()
    monkeypatch.setattr(srv, "chunks_for", lambda text, a=None, b=None, st=None: ["First piece.", "Second piece here."])
    monkeypatch.setattr(srv, "frames", lambda chunk, name: iter([chunk.encode()[:4] * 2, b"\x00\x00"]))
    monkeypatch.setitem(srv.RT, "settings", {"emit": "frames"})
    seen = []
    srv.run_job("x", "crissa", lambda b: seen.append(("pcm", b)), {}, mark=lambda i, t: seen.append(("mark", i, t)))
    kinds = [s[0] for s in seen]
    assert kinds == ["pcm", "pcm", "mark", "pcm", "pcm", "mark"]
    assert seen[2] == ("mark", 0, "First piece.") and seen[5] == ("mark", 1, "Second piece here.")
    plain = []
    srv.run_job("x", "crissa", plain.append, {})                  # no mark callback: today's bytes, nothing else
    assert plain == [s[1] for s in seen if s[0] == "pcm"]


def test_pocket_writes_marks_only_when_asked_and_never_logs_the_words():
    src = (ROOT / "modules" / "voice" / "scripts" / "crissa-pocket.py").read_text()
    assert 'marks_on = bool(req.get("marks")) and MARKS_ON' in src
    assert "mark=mark if marks_on else None" in src
    assert 'b"0" + (ext() if pending else b"") + b"\\r\\n\\r\\n"' in src


# ---- house-speak's mark clock --------------------------------------------------------------------------------------

class _Aligner:
    """A stand-in aligner on loopback: one word per text word, spread over the audio it was sent."""

    def __init__(self, fail_on: str | None = None):
        fail = fail_on

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                d = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if fail and fail in d["text"]:
                    out = {"ok": False, "error": "no"}
                else:
                    import base64
                    secs = len(base64.b64decode(d["pcm16_b64"])) / 2.0 / d["sr"]
                    ws = d["text"].split()
                    out = {"ok": True, "ms": 3.0, "words": [{"w": w, "a": secs * k / len(ws),
                                                               "b": secs * (k + 0.9) / len(ws)} for k, w in enumerate(ws)]}
                b = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/align" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


class _Air:
    def __init__(self):
        self.air_at = time.time()
        self.stamps = []
        self.armed = []

    def set_piece_clock(self, pcm, text, words, partial, tempo=1.0, note=""):
        self.stamps.append((len(pcm), text, len(words), partial))
        return True

    def arm_clock(self, pcm, text, wait_s=0.0, tempo=1.0):
        self.armed.append((len(pcm), text))


def _speak_ns(url: str, house: bool = False, main: str = "pocket") -> dict:
    import base64
    import queue
    import urllib.request
    ns = {"os": os, "sys": sys, "Path": Path, "threading": threading, "time": time, "json": json, "base64": base64,
          "queue": queue, "urllib": urllib, "_HOUSE": house, "_LINE": {}, "_AIR_TEXT": "the line", "TEMPO": 1.0,
          "MouthAir": object, "MOUTH_ALIGN_URL": url, "mouth_align_on": lambda: True,
          "_main_name": lambda: main, "__file__": str(ROOT / "scripts" / "house-speak.py"),
          "PIECE_ONCE": True, "_voice_onset_s": lambda pcm, sr: None}     # round 17's names
    return _take(SPEAK, ("align_early_on", "pocket_marks_on", "_PieceClock", "_MarkClock", "_EarlyClock",
                         "_early_clock_for"), ns)


def _wait(cond, secs=5.0):
    t0 = time.time()
    while time.time() - t0 < secs and not cond():
        time.sleep(0.02)
    return cond()


def test_pocket_marks_are_a_packs_on_pocket_and_the_switch_forces_them(monkeypatch):
    monkeypatch.delenv("HOUSE_MOUTH_POCKET_MARKS", raising=False)
    monkeypatch.delenv("HOUSE_MOUTH_ALIGN_EARLY", raising=False)
    assert _speak_ns("x")["pocket_marks_on"]() is True
    assert _speak_ns("x", house=True)["pocket_marks_on"]() is False, "the house speaks with Turbo's own stamps"
    assert _speak_ns("x", main="turbo")["pocket_marks_on"]() is False
    monkeypatch.setenv("HOUSE_MOUTH_POCKET_MARKS", "0")
    assert _speak_ns("x")["pocket_marks_on"]() is False
    monkeypatch.setenv("HOUSE_MOUTH_POCKET_MARKS", "1")
    assert _speak_ns("x", house=True)["pocket_marks_on"]() is True


def test_each_pocket_piece_is_stamped_as_its_mark_arrives_and_the_line_ends_whole():
    al = _Aligner()
    ns = _speak_ns(al.url)
    p = [b"\x10\x00" * 2400, b"\x20\x00" * 4800, b"\x30\x00" * 1200]
    raw = _marked_stream(p, {1: _ext(0, "Sure, here it is."), 2: _ext(1, "The lights came on at six.")},
                         tail=_ext(2, "Ready when you are.").decode())
    resp = _Resp(raw, {"Transfer-Encoding": "chunked", "X-Voice-Engine": "pocket", "X-Sample-Rate": "24000"})
    ec = ns["_early_clock_for"](resp, True)
    assert ec is not None and ec.mc is not None and ns["_LINE"].get("piece_marks") is True
    air = _Air()
    ec.attach(air)
    while ec.reader.read(4096, timeout_s=2.0):
        pass
    assert _wait(lambda: air.stamps and air.stamps[-1][3] is False)
    assert [s[0] for s in air.stamps][:3] == [len(p[0]), len(p[0]) + len(p[1]), sum(map(len, p))]
    assert all(s[3] for s in air.stamps[:3]), "while the stream is open later pieces keep the letter clock"
    assert air.stamps[-1][1] == "Sure, here it is. The lights came on at six. Ready when you are."
    assert air.armed == [], "every piece stamped: the whole line is not aligned again"


def test_a_failed_piece_or_a_server_without_marks_aligns_the_whole_line_at_the_end():
    al = _Aligner(fail_on="lights")
    ns = _speak_ns(al.url)
    p = [b"\x10\x00" * 2400, b"\x20\x00" * 2400]
    raw = _marked_stream(p, {1: _ext(0, "Sure, here it is.")}, tail=_ext(1, "The lights came on.").decode())
    ec = ns["_early_clock_for"](_Resp(raw, {"Transfer-Encoding": "chunked", "X-Voice-Engine": "pocket"}), True)
    air = _Air()
    ec.attach(air)
    while ec.reader.read(4096, timeout_s=2.0):
        pass
    assert _wait(lambda: air.armed)
    assert air.armed == [(sum(map(len, p)), "the line")]
    old = _marked_stream(p, {})                                   # an older Pocket: no marks at all
    ec = ns["_early_clock_for"](_Resp(old, {"Transfer-Encoding": "chunked", "X-Voice-Engine": "pocket"}), True)
    air = _Air()
    ec.attach(air)
    while ec.reader.read(4096, timeout_s=2.0):
        pass
    assert _wait(lambda: air.armed) and air.stamps == []


def test_house_speak_asks_pocket_for_marks_only_through_the_switch():
    assert 'marks = pocket_marks_on()' in SPEAK
    assert '**({"marks": True} if marks else {})' in SPEAK
    assert "early = _early_clock_for(resp, marks) if pc is None else None" in SPEAK


# ---- the lip clock in the player's log -----------------------------------------------------------------------------

def test_the_mailbox_keeps_the_lines_clock_and_a_new_line_drops_it():
    import mouth_mailbox
    mb = mouth_mailbox.MouthMailbox()
    mb.ingest({"air_at": 10.0, "rms": [0.1], "pts0": 10.0, "speaker": "crissa"})
    assert "clock" not in mb.snapshot(), "a line that sends no clock reads as before"
    mb.ingest({"air_at": 10.0, "air_until": 12.0, "clock": {"how": "aligned by sentence (3 of 3)", "after_s": 4.2,
                                                           "lead_ms": 20.8, "engine": "pocket", "x" * 40: "y" * 300}})
    k = mb.snapshot()["clock"]
    assert k["how"] == "aligned by sentence (3 of 3)" and k["after_s"] == 4.2 and len(k["x" * 24]) == 120
    mb.ingest({"air_at": 20.0, "rms": [0.1], "pts0": 20.0})
    assert "clock" not in mb.snapshot()


def test_the_player_prints_the_clock_beside_the_semis_line_and_never_on_the_house():
    import mouth_mailbox
    mb = mouth_mailbox.MouthMailbox()
    ns = _take(CONDUCTOR, ("lip_clock_line",), {"MAILBOX": mb, "IS_BENCH": False, "MOUTH_SHEET_LEAD_MS": 20.8,
                                                 "SYNC_ZERO_MS": 83.3,
                                                 "crissa_lipsync": type("L", (), {"offset_ms": staticmethod(lambda: 166.7)})})
    c = {"air_at": 5.0, "rows": 100, "skipped": 30, "painted": 90}
    assert ns["lip_clock_line"](c) == ""
    mb.ingest({"air_at": 5.0, "air_until": 9.0, "clock": {"how": "aligned by sentence (6 of 6, 214 ms last)",
                                                          "first_s": 1.28, "after_s": 9.1, "lead_ms": 270.8,
                                                          "engine": "pocket", "marks": True, "chars": 383}})
    line = ns["lip_clock_line"](c)
    assert line == ("aligned by sentence (6 of 6, 214 ms last); first stamp 1.28s into the line; last stamp 9.1s; "
                    "sheet lead 270.8 ms (dial 20.8 + zero 83.3 + lip sync 166.7, now); voice pocket (piece marks); "
                    "383 chars; 70 of 100 rows on the feed")
    assert ns["lip_clock_line"](dict(c, air_at=6.0)) == "", "another line's clock is not this one's"
    assert _take(CONDUCTOR, ("lip_clock_line",), {"MAILBOX": None})["lip_clock_line"](c) == ""


def test_house_speak_sends_the_clock_with_its_last_post_in_a_pack_only():
    assert 'if not _HOUSE and (air_until is not None or _CLOCK_HOW):' in SPEAK        # round 12: every post
    ns = _take(SPEAK, ("line_clock_note",), {"_CLOCK_HOW": "", "_LINE": {"engine": "pocket", "chars": 12},
                                             "sheet_lead_s": lambda: 0.0208333})
    assert ns["line_clock_note"]() == {"how": "letter", "after_s": None, "first_s": None, "lead_ms": 20.8,
                                       "engine": "pocket", "marks": False, "early": False, "chars": 12}
