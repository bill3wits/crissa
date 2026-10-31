"""Fix for good (the User 2026-10-05): every take's short gaps laced at Intake, lip marks laced like the head
track, a grid pin that belongs to its angles, and the landing measured. All behind switches that are OFF."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_lip_marks_fill_only_short_gaps_between_found_frames():
    m = _script("pose-lipmarks-fill-gaps")
    fr = [{"nose": [0, 0], "hull": [[0, 0], [2, 2]]}, None, None, {"nose": [3, 6], "hull": [[3, 3], [5, 5]]}, None]
    assert m.fill(fr, 16) == [1, 2] and fr[1]["nose"] == [1.0, 2.0] and fr[2]["hull"][1] == [4.0, 4.0]
    assert fr[1]["filled"] == "interp" and fr[4] is None                 # an end is never invented
    long_gap = [{"n": [0]}] + [None] * 20 + [{"n": [1]}]
    assert m.fill(long_gap, 16) == []                                   # a deep profile / face-down stays empty


def test_a_lone_pin_is_held_flat_by_default_and_local_with_a_span(monkeypatch):
    import mouth_pins as M
    rows = [{"yaw": 7.36, "u": 0, "v": -5}]
    assert M.pin_at(rows, 40.0) == (0.0, -5.0)                           # today's rule, byte for byte
    assert M.pin_at(rows, 7.36, 8) == (0.0, -5.0)
    assert abs(M.pin_at(rows, 11.36, 8)[1] + 2.5) < 1e-9
    assert M.pin_at(rows, 40.0, 8) == (0.0, 0.0)
    assert M.pin_at([{"yaw": 0, "u": 0, "v": 0}, {"yaw": 20, "u": 0, "v": 4}], 10, 8) == (0.0, 2.0)


def test_intake_laces_only_when_switched_on(monkeypatch):
    import pose_intake as PI
    seen = []
    monkeypatch.setattr(PI, "_run_tool", lambda argv, log, ctx=-1: seen.append(argv) or 0)
    monkeypatch.setattr(PI, "INTAKE_LACE", False)
    assert PI.lace_pass("clip-x", "head", print) == 0 and seen == []
    monkeypatch.setattr(PI, "INTAKE_LACE", True)
    PI.lace_pass("clip-x", "head", print)
    PI.lace_pass("clip-x", "lip_marks", print)
    assert [Path(a[1]).name for a in seen] == ["pose-head-fill-gaps.py", "pose-lipmarks-fill-gaps.py"]
    assert all(a[a.index("--max") + 1] == str(PI.INTAKE_LACE_MAX) for a in seen)


def test_the_sweep_never_drives_a_house_bench():
    m = _script("lip-landing-sweep")
    assert set(m.HOUSE_PORTS) >= {8100, 8110, 8101}
    rows = [{"frame": 0, "err_px": 1.0}, {"frame": 1, "err_px": 7.5}, {"frame": 2}]
    s = m.summary(rows, flag=6.0)
    assert s["measured"] == 2 and s["flagged"] == [1]
