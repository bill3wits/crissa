"""The semiframe counter (the User 2026-10-11, "yes on counter"): while she speaks, which mouth row each conductor
paint lands on - rows painted, skipped, doubled per line. Measure only."""
from __future__ import annotations

import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def _ns():
    block = SRC.split("SEMI_COUNT_ON = ", 1)[1].split("SEMIS = SemiCount()", 1)[0]
    ns = {"os": os, "time": time}
    exec("from __future__ import annotations\nSEMI_COUNT_ON = " + block, ns)
    return ns


class _M:
    def __init__(self, frames, air=100.0):
        self.frames, self.air_at, self.speaking = frames, air, True


SEMI = 1.0 / 48.0
ROWS = [[100.0 + i * SEMI, 0.5, 3] for i in range(48)]


def _run(ticks):
    ns = _ns()
    c = ns["SemiCount"]()
    m = _M(ROWS)
    for t in ticks:
        c.tick(m, t)
    m.speaking = False
    c.tick(m, ticks[-1] + 1)
    return c.lines[-1]


def test_a_paint_on_every_row_skips_nothing():
    line = _run([100.0 + i * SEMI + 0.002 for i in range(40)])
    assert (line["painted"], line["skipped"], line["doubled"]) == (40, 0, 0)


def test_a_slow_loop_is_counted_as_skipped_rows():
    line = _run([100.0 + i * (SEMI * 1.5) + 0.002 for i in range(20)])     # 32 fps over a 48/s grid
    assert line["skipped"] > 0 and line["painted"] == 20


def test_a_fast_loop_doubles_rows_and_skips_none():
    line = _run([100.0 + i * (1 / 60.0) + 0.001 for i in range(50)])      # 60 fps over a 48/s grid
    assert line["doubled"] > 0 and line["skipped"] == 0


def test_the_state_and_the_switch():
    ns = _ns()
    st = ns["SemiCount"]().state()
    assert st["now"] is None and st["total"]["skipped"] == 0
    assert '"semis": SEMIS.state(),' in SRC and "SEMIS.tick(self.mouth, time.time())" in SRC
    assert 'os.environ.get("HOUSE_SEMI_COUNT", "1")' in SRC


def test_a_line_reports_where_its_paint_time_went():
    ns = _ns()
    c = ns["SemiCount"]()
    m = _M(ROWS)
    for i in range(10):
        c.tick(m, 100.0 + i * SEMI + 0.002)
        c.cost(composite=40.0 + i, encode=6.0, pip=0.0, loop=55.0)
    m.speaking = False
    c.tick(m, 102.0)
    cost = c.lines[-1]["cost_ms"]
    assert cost["composite"]["p50"] >= 40.0 and cost["encode"]["p50"] == 6.0 and cost["loop"]["n"] == 10
    assert "SEMIS.cost(composite=_t_comp, encode=_t_enc, pip=_t_pip," in SRC
