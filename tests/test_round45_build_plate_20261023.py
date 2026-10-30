"""Round 45 (.101 round 44): the build plate's time left read faster than the build finished ("does not seem to finish
as fast as it claims"), and an hour on it came back for "2 of the last 130 pictures drawn fresh". The time left is read
at the slower of the recent rate and the run's own (kill CRISSA_PLATE_RUN_RATE=0); the jumpy plate shows only while a
lap dropped at least PLATE_DROP_SHARE of its film frames (dial CRISSA_PLATE_DROP_SHARE, 0 = any skip)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_build as zb  # noqa: E402

BUILT = {"running": False, "loops_total": 10, "loops_built": 10, "name": "Crissa"}
BUILDING = {"running": True, "loops_total": 1741, "loops_built": 1428, "loops_counted": True, "name": "Crissa"}


def test_the_time_left_takes_the_slower_rate(monkeypatch):
    monkeypatch.delenv("CRISSA_PLATE_RUN_RATE", raising=False)
    fast = zb.plate_view(dict(BUILDING, rate=12.0))["line"]
    assert fast.endswith("less than a minute left")
    v = zb.plate_view(dict(BUILDING, rate=12.0, rate_run=2.0))["line"]
    assert v.endswith("about 3 min left"), v                 # 313 pictures at 2 a second
    monkeypatch.setenv("CRISSA_PLATE_RUN_RATE", "0")
    assert zb.plate_view(dict(BUILDING, rate=12.0, rate_run=2.0))["line"] == fast


def test_a_built_wall_drawing_two_fresh_is_not_jumpy(monkeypatch):
    monkeypatch.delenv("CRISSA_PLATE_DROP_SHARE", raising=False)
    assert zb.plate_view(dict(BUILT, lap={"clip": "work", "n": 130, "fresh": 2, "skipped": 2})) == {"show": False}
    v = zb.plate_view(dict(BUILT, lap={"clip": "listen", "n": 72, "fresh": 72, "skipped": 24}))
    assert v["show"] and v["jumpy"]
    monkeypatch.setenv("CRISSA_PLATE_DROP_SHARE", "0")       # round 44: any skip
    assert zb.plate_view(dict(BUILT, lap={"clip": "work", "n": 130, "fresh": 2, "skipped": 2}))["show"]


def test_the_build_writes_the_run_rate():
    src = (ROOT / "lib" / "wall_ascii_disk.py").read_text(encoding="utf-8")
    assert '"rate_run": round(rate_run, 2) if rate_run else None' in src
    assert "run_mark[:] = [now, finished]" in src
    assert '"rate_run": best.get("rate_run")' in (ROOT / "lib" / "crissa_build.py").read_text(encoding="utf-8")


def test_round46_a_stall_never_sets_the_time_left(monkeypatch):
    """Round 46 (.101 round 45: 424 / 374 / 467 min while the build stalled under her lines)."""
    monkeypatch.delenv("CRISSA_PLATE_RATE_WORK", raising=False)
    monkeypatch.delenv("CRISSA_PLATE_RUN_RATE", raising=False)
    stalled = dict(BUILDING, rate=0.01, rate_run=2.0, rate_worked=False)
    assert zb.plate_view(stalled)["line"].endswith("about 3 min left")         # the run's rate, never hours
    working = dict(BUILDING, rate=1.0, rate_run=2.0, rate_worked=True)
    assert zb.plate_view(working)["line"].endswith("about 5 min left")         # the recent rate, a window with work
    monkeypatch.setenv("CRISSA_PLATE_RATE_WORK", "0")
    assert zb.plate_view(stalled)["line"].endswith("min left") and "about 522 min" in zb.plate_view(stalled)["line"]
    src = (ROOT / "lib" / "wall_ascii_disk.py").read_text(encoding="utf-8")
    assert '"rate_worked": worked' in src and "held - old_[2] <= 0.0" in src
