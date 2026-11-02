"""Energy-ring onset: walk back through sub-threshold approach (gap A)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.onset import QUIET_N, find_onset, find_rest


def _rows(energies, t0=1000.0, seq0=1, dt=1.0 / 15.0):
    return [
        {"seq": seq0 + i, "ts": t0 + i * dt, "energy": e}
        for i, e in enumerate(energies)
    ]


def test_onset_walks_back_through_approach():
    # 10 quiet, 8 approach (below trigger 8, above still 3.5), 4 spike
    quiet = [1.0] * 10
    approach = [4.0, 5.0, 6.0, 6.5, 7.0, 7.2, 7.5, 7.8]
    spike = [9.0, 12.0, 14.0, 11.0]
    rows = _rows(quiet + approach + spike)
    out = find_onset(rows, len(rows) - 1)
    assert out["why"] in ("onset", "buffer_start")
    assert out["onset_i"] < 10 + len(approach)  # not the spike
    assert out["trigger_offset_s"] is not None
    assert out["trigger_offset_s"] > 0.3
    assert out["threshold"] >= 3.5


def test_onset_empty():
    out = find_onset([], 0)
    assert out["why"] == "empty"
    assert out["onset_i"] == 0


def test_onset_blind_floor_stops_walkback():
    """3 consecutive energy<0 is a hard floor, not a skip."""
    from crissa_presence.onset import BLIND_N

    quiet = [1.0] * 8
    gap = [-1.0] * BLIND_N
    approach = [6.0, 7.0, 8.0]
    spike = [12.0, 14.0]
    rows = _rows(quiet + gap + approach + spike)
    out = find_onset(rows, len(rows) - 1)
    assert out["why"] == "blind_floor"
    assert out["onset_i"] >= len(quiet)
    assert out.get("blind_gap_s") is not None
    assert (out.get("trigger_offset_s") or 0) < 2.0


def test_onset_skips_pan_samples():
    # pan (-1) then quiet then spike
    rows = _rows([-1.0] * 5 + [1.0] * 8 + [10.0, 12.0])
    out = find_onset(rows, len(rows) - 1)
    assert out["onset_seq"] is not None
    assert out["rest_baseline"] >= 0.0
    assert out["threshold"] >= 3.5


def test_find_rest_forward_quiet():
    rows = _rows([1.0] * 6 + [10.0, 12.0, 9.0] + [1.0] * 8)
    trig = 7
    out = find_rest(rows, trig)
    assert out["why"] == "quiet"
    assert out["rest_i"] > trig
    assert out["rest_i"] >= trig + QUIET_N - 1


def test_find_rest_buffer_end_if_still_busy():
    rows = _rows([1.0] * 4 + [10.0] * 20)
    out = find_rest(rows, 5)
    assert out["why"] == "buffer_end"
    assert out["rest_i"] == len(rows) - 1
