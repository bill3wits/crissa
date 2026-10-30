"""The paint on the semiframe grid, the counter's "by paint" rows, and the sheet poll (the User 2026-10-12, "go ahead
with 2 and 3")."""
from __future__ import annotations

import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
SEMI = 1.0 / 48.0
ROWS = [[100.0 + i * SEMI, 0.5, 3] for i in range(48)]


def _ns():
    block = SRC.split("SEMI_COUNT_ON = ", 1)[1].split("SEMIS = SemiCount()", 1)[0]
    ns = {"os": os, "time": time}
    exec("from __future__ import annotations\nSEMI_COUNT_ON = " + block, ns)
    return ns


def test_an_unpainted_row_is_painted_at_once():
    ns = _ns()
    now = 100.0 + 5 * SEMI + 0.004
    assert ns["semi_grid_wait"](ROWS, now, painted=100.0 + 4 * SEMI) == 0.0
    assert ns["semi_grid_wait"](ROWS, now, painted=None) == 0.0


def test_a_painted_row_waits_for_the_next_one():
    ns = _ns()
    now = 100.0 + 5 * SEMI + 0.016
    w = ns["semi_grid_wait"](ROWS, now, painted=100.0 + 5 * SEMI)
    assert abs((now + w) - (100.0 + 6 * SEMI + 0.0015)) < 1e-9


def test_past_the_fetched_rows_the_grid_goes_on():
    ns = _ns()
    last = ROWS[-1][0]
    now = last + 2 * SEMI + 0.003                      # two rows past the last batch the poll has
    row = ns["semi_grid_row"](ROWS, now)
    assert abs(row - (last + 2 * SEMI)) < 1e-9
    assert ns["semi_grid_wait"](ROWS, now, painted=last) == 0.0
    w = ns["semi_grid_wait"](ROWS, now, painted=row)
    assert abs((now + w) - (last + 3 * SEMI + 0.0015)) < 1e-9


def test_no_grid_means_pace_as_before():
    ns = _ns()
    assert ns["semi_grid_wait"]([], 100.0, None) is None
    assert ns["semi_grid_wait"](ROWS, ROWS[-1][0] + 1.0, None) is None     # a stale sheet


def test_off_by_default_and_the_poll_as_before(monkeypatch):
    monkeypatch.delenv("HOUSE_MOUTH_SEMI_GRID", raising=False)
    monkeypatch.delenv("HOUSE_MOUTH_POLL_S", raising=False)
    ns = _ns()
    assert ns["SEMI_GRID"] is False and ns["MOUTH_POLL_S"] == 0.1
    monkeypatch.setenv("HOUSE_MOUTH_SEMI_GRID", "1")
    monkeypatch.setenv("HOUSE_MOUTH_POLL_S", "0.02")
    ns = _ns()
    assert ns["SEMI_GRID"] is True and ns["MOUTH_POLL_S"] == 0.02


class _M:
    def __init__(self, frames, air=100.0):
        self.frames, self.air_at, self.speaking = frames, air, True


def test_by_paint_counts_the_row_looked_up_not_the_pass_end():
    ns = _ns()
    c = ns["SemiCount"]()
    m = _M(ROWS)
    for k in range(1, 11):
        start = 100.0 + k * SEMI + 0.002             # each pass looks its row up at the slot's start...
        end = start + 0.0195                         # ...and ends just inside the NEXT slot
        c.tick(m, end)
        c.tick_paint(m, start)
    cur = c.cur
    assert cur["p_painted"] == 10 and cur.get("p_skipped", 0) == 0 and cur.get("p_doubled", 0) == 0
    m.speaking = False
    c.tick(m, 101.0)
    assert c.lines and c.lines[-1]["p_painted"] == 10


def test_the_grid_paces_only_a_speaking_pass():
    loop = SRC.split("    def run(self) -> None:", 1)[1].split("    def wait_frame(", 1)[0]
    assert "if SEMI_GRID and not _idle and getattr(self.mouth, \"speaking\", False):" in loop
    assert "nxt += idle_period if _idle else period" in loop


def test_a_pause_in_the_sheet_is_not_a_skip():
    ns = _ns()
    c = ns["SemiCount"]()
    rows = [r for r in ROWS if not (10 <= int(round((r[0] - 100.0) / SEMI)) < 30)]   # 20 rows of pause
    m = _M(rows)
    for k in list(range(1, 10)) + list(range(30, 40)):
        t = 100.0 + k * SEMI + 0.002
        c.tick(m, t)
        c.tick_paint(m, t)
    assert c.cur.get("p_skipped", 0) == 0
