"""Round 12, the .101 lips (2026-10-12, RESULTS-20261012-0605 section 1).

  * Her answers logged no `lip clock:` in player.log, only the player's own Say: a reply with pauses closes the
    semiframe counter's line before house-speak's last post (the one carrying the clock) lands, so the clock was never
    there to print. house-speak now sends the clock on every post once one has landed, and the counter prints a line's
    clock when it changes, also after the line has closed.
  * "sheet lead 271 ms against the dials' 21-42": the lead is the library's dial + its calibrated zero + this
    machine's lip sync; the line now says so.
  * Long lines: every Pocket piece's stamp against when its audio plays is kept in the line log, and the player's line
    says how many pieces were stamped before they played.

No player, voice or aligner runs here: the functions are taken from the scripts' source and run on stand-ins."""
from __future__ import annotations

import ast
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import mouth_mailbox  # noqa: E402

SPEAK = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")
CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def _take(src: str, names: tuple, ns: dict) -> dict:
    tree = ast.parse(src)
    got = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert sorted(n.name for n in got) == sorted(names)
    exec(compile(ast.Module(body=got, type_ignores=[]), "src", "exec"), ns)
    return ns


class _Lipsync:
    @staticmethod
    def offset_ms():
        return 166.667


def _player(mb, bench=False) -> dict:
    return _take(CONDUCTOR, ("lip_clock_line", "hop_pts_at", "SemiCount"),
                 {"MAILBOX": mb, "IS_BENCH": bench, "MOUTH_SHEET_LEAD_MS": 20.833, "SYNC_ZERO_MS": 83.333,
                  "crissa_lipsync": _Lipsync, "SEMI_COUNT_ON": True, "time": time})


class _Mouth:
    def __init__(self, air_at):
        self.air_at = air_at
        self.speaking = True
        self.frames = []


def _speak_rows(m, t0, n, now):
    m.frames = [[t0 + i * 0.0208, 0.5, 3] for i in range(n)]


CLOCK = {"how": "aligned by sentence (11 of 11, 322 ms last)", "first_s": 1.64, "after_s": 23.47, "lead_ms": 270.8,
         "engine": "pocket", "marks": True, "chars": 614, "pieces": 11, "early_pieces": 10, "late_s": 1.64,
         "late_piece": 1}


def test_a_reply_closed_before_its_last_post_still_logs_its_clock(capsys):
    mb = mouth_mailbox.MouthMailbox()
    ns = _player(mb)
    sc = ns["SemiCount"]()
    now = time.time()
    m = _Mouth(now)
    mb.ingest({"air_at": now, "rms": [0.1], "pts0": now, "speaker": "crissa"})
    _speak_rows(m, now - 0.3, 30, now)
    sc.tick(m, now)
    m.speaking = False
    sc.tick(m, now + 0.05)                          # the counter closes the line: no clock has landed yet
    out = capsys.readouterr().out
    assert "[conductor] semis:" in out and "lip clock" not in out
    mb.ingest({"air_at": now, "air_until": now + 2.0, "clock": CLOCK})   # the last post lands after the close
    sc.tick(m, now + 0.7)
    out = capsys.readouterr().out
    assert out.count("[conductor] lip clock:") == 1
    assert ("sheet lead 270.8 ms (dial 20.8 + zero 83.3 + lip sync 166.7, now)" in out
            and "10 of 11 pieces stamped before they played, the latest spoke 1.64s unstamped (piece 1)" in out)
    sc.tick(m, now + 1.4)                           # the same clock is said once
    assert "lip clock" not in capsys.readouterr().out


def test_a_reply_in_several_pieces_prints_each_new_clock_once_and_counts_all_its_rows(capsys):
    mb = mouth_mailbox.MouthMailbox()
    ns = _player(mb)
    sc = ns["SemiCount"]()
    now = time.time()
    m = _Mouth(now)
    mb.ingest({"air_at": now, "rms": [0.1], "pts0": now, "clock": dict(CLOCK, how="aligned by sentence (2 of 2)",
                                                                       after_s=2.1, pieces=2, early_pieces=1)})
    for seg in range(2):                            # a pause between sentences closes the counter's line
        m.speaking = True
        _speak_rows(m, now + seg * 2.0, 40, now)
        sc.tick(m, now + seg * 2.0 + 0.2)
        sc.tick(m, now + seg * 2.0 + 0.5)
        m.speaking = False
        sc.tick(m, now + seg * 2.0 + 0.6)
    out = capsys.readouterr().out
    assert out.count("[conductor] semis:") == 2 and out.count("[conductor] lip clock:") == 1
    mb.ingest({"air_at": now, "air_until": now + 5.0, "clock": CLOCK})
    sc.tick(m, now + 5.0)
    out = capsys.readouterr().out
    assert out.count("[conductor] lip clock:") == 1 and "11 of 11" in out
    rows = sum(int(x["rows"]) for x in sc.lines)
    assert ("of %d rows on the feed" % rows) in out, "the whole reply's rows, not its last piece's"


def test_a_bench_has_no_lip_sync_and_the_house_logs_nothing():
    mb = mouth_mailbox.MouthMailbox()
    mb.ingest({"air_at": 5.0, "air_until": 9.0, "clock": CLOCK})
    line = _player(mb, bench=True)["lip_clock_line"]({"air_at": 5.0, "rows": 10, "skipped": 0})
    assert "(dial 20.8 + zero 83.3 + lip sync 0.0, now)" in line
    assert _player(None)["lip_clock_line"]({"air_at": 5.0, "rows": 10}) == "", "the wall's mailbox is :8099's"


def test_house_speak_sends_the_clock_on_every_post_once_landed_and_never_on_the_house():
    assert 'if not _HOUSE and (air_until is not None or _CLOCK_HOW):\n' in SPEAK
    ns = _take(SPEAK, ("line_clock_note",), {"os": os, "_CLOCK_HOW": "aligned by sentence (3 of 3)", "sheet_lead_s": lambda: 0.2708,
                                             "_LINE": {"engine": "pocket", "chars": 144, "piece_marks": True,
                                                       "pieces": [[1, 1.4, 0.0, 2.8], [2, 2.1, 2.8, 5.0],
                                                                  [3, 3.0, 5.0, 7.5]]}})
    k = ns["line_clock_note"]()
    assert k["pieces"] == 3 and k["early_pieces"] == 2 and k["late_s"] == 1.4 and k["late_piece"] == 1
    assert len(k) <= 16, "the mailbox keeps 16 keys of a clock"
    k = _take(SPEAK, ("line_clock_note",), {"os": os, "_CLOCK_HOW": "", "sheet_lead_s": lambda: 0.1,
                                            "_LINE": {"pieces": [[1, 0.5, 0.0, 2.0], [2, 1.0, 2.0, 4.0]]}})["line_clock_note"]()
    assert k["early_pieces"] == 1 and k["late_piece"] == 1


def test_the_line_log_keeps_each_pocket_pieces_margin_in_a_pack_only():
    assert 'if not _HOUSE and len(_LINE.setdefault("pieces", [])) < 40:' in SPEAK
    assert '_LINE["pieces"].append([i + 1, round(at, 2), round(a, 2), round(b, 2)])' in SPEAK   # round 15: heard times


def test_the_mailbox_keeps_sixteen_clock_keys():
    mb = mouth_mailbox.MouthMailbox()
    mb.ingest({"air_at": 1.0, "clock": {"k%02d" % i: i for i in range(20)}})
    assert len(mb.snapshot()["clock"]) == 16


def test_the_buffered_lines_stamp_is_logged_against_its_real_first_sample():
    """.101 read the test line as "aligned 0.02-0.08 s after her first sample": the landing was logged against the air
    time guessed before the aligner's wait; the buffered path re-reads it once its real air time is known."""
    assert 'self._aligned_t = time.time()       # the buffered path re-reads it once it knows air_at' in SPEAK
    i = SPEAK.index("            air.air_at = air_at\n            if not _HOUSE and getattr(air, \"_aligned_t\", None):")
    j = SPEAK.index('_LINE["aligned_after_s"] = round(float(air._aligned_t) - air_at, 2)', i)
    assert i < j < SPEAK.index("            air.feed(pcm)", i), "before the envelope is published"
