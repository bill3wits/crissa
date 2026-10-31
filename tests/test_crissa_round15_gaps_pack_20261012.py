"""Round 15 (2026-10-12): a pause in her voice is a pause in her mouth sheet IN A PACK too. Pocket on a busy CPU runs
slower than the player at the start of an answer; the player runs dry and the room hears a pause, and every row after it
landed early (replayed at 1.1x real time: 230-330 ms early by the last pieces). The house's fix (sheet_gaps_on,
d9e2f1d18) is on in a pack now, kills unchanged; a Pocket piece's logged margin counts the pauses before it.

No voice, aligner or player runs here: the functions are taken from house-speak's source and run on stand-ins."""
from __future__ import annotations

import ast
import base64
import json
import os
import queue
import sys
import threading
import time
import urllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
SPEAK = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")


def _take(names: tuple, ns: dict) -> dict:
    tree = ast.parse(SPEAK)
    got = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert sorted(n.name for n in got) == sorted(names)
    exec(compile(ast.Module(body=got, type_ignores=[]), "src", "exec"), ns)
    return ns


class _Air:
    def __init__(self, gap_from_hop: int, gap: float):
        self.air_at = time.time() - 5.0
        self.g = (gap_from_hop, gap)

    def _gap_at(self, hop_i):
        return self.g[1] if hop_i >= self.g[0] else 0.0

    def set_piece_clock(self, *a, **k):
        return True


def test_a_pieces_logged_margin_counts_the_pauses_before_it():
    ns = _take(("_PieceClock", "_MarkClock"), {
        "os": os, "sys": sys, "time": time, "json": json, "base64": base64, "queue": queue, "threading": threading,
        "urllib": urllib, "MOUTH_ALIGN_URL": "http://127.0.0.1:9/align", "TEMPO": 1.0, "_HOUSE": False,
        "_LINE": {}, "MOUTH_HOP_MS": 20.833333, "PIECE_ONCE": True, "_voice_onset_s": lambda pcm, sr: None})
    mc = ns["_MarkClock"](24000)
    mc.texts = ["Call me Ishmael.", "Some years ago,"]
    mc.ends = [48000 * 2, 48000 * 4]                  # 2 s, then 2 s more
    mc.raw = bytearray(b"\x00" * mc.ends[-1])
    mc.words = {0: [{"w": "call", "a": 0.1, "b": 0.4}], 1: [{"w": "some", "a": 2.1, "b": 2.4}]}
    mc.ms = [200.0, 200.0]
    mc.air = _Air(gap_from_hop=48, gap=0.7)           # a 0.7 s pause from the first second on
    mc.commit()
    p = ns["_LINE"]["pieces"]
    assert [x[0] for x in p] == [1, 2]
    assert p[0][2:] == [0.0, 2.0], "the first piece starts before the pause"
    assert p[1][2:] == [2.7, 4.7], "the second is heard 0.7 s later than its bytes say"


def test_the_pause_fix_has_no_house_only_rule_left():
    i = SPEAK.index("def sheet_gaps_on() -> bool:")
    body = SPEAK[i:SPEAK.index("\nSHEET_GAP_MIN_S", i)]
    assert "if not _HOUSE:" not in body and "mouth-sheet-gaps.off" in body and "HOUSE_SPEAK_SHEET_GAPS" in body
