"""The menu's press is timed, report only (the User 2026-10-29: "it takes forever to come up or it requires a second
press"; the view answers in 0.04 s, so the wait is the Tk thread's)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import crissa_wall_key as K  # noqa: E402


def test_a_late_beat_is_kept_for_the_window_and_then_forgotten(monkeypatch):
    monkeypatch.setattr(K, "_TICK", {"last": 0.0, "late": []})
    K.tick(None, now=100.0)
    assert K.tick(None, now=100.1) == 0.0 or K.tick(None, now=100.1) < 1
    K.tick(None, now=101.6)                                   # 1.5 s between beats: the loop was busy 1.4 s
    assert 1390 <= K.tk_late_ms(now=101.7) <= 1410
    assert K.tk_late_ms(now=110.0) == 0.0


def test_a_press_writes_one_line_and_the_switch_stops_it(tmp_path, monkeypatch):
    import time
    row = K.note_timing("menu", time.time() - 0.25, home=tmp_path)
    got = [json.loads(x) for x in (tmp_path / "logs" / "menu-timing.jsonl").read_text().splitlines()]
    assert got == [row] and row["what"] == "menu" and row["shown_ms"] >= 250
    monkeypatch.setenv("CRISSA_MENU_TIMING", "0")
    K.note_timing("menu", time.time(), home=tmp_path)
    assert len((tmp_path / "logs" / "menu-timing.jsonl").read_text().splitlines()) == 1


def test_the_key_and_every_item_are_timed():
    src = (ROOT / "lib" / "crissa_wall_key.py").read_text(encoding="utf-8")
    assert 'note_timing("menu", t0)' in src and 'note_timing("item " + str(verb), time.time())' in src
    assert "root.after(TICK_MS, tick, root)" in src
