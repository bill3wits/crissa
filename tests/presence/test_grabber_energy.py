"""Camera energy ring + onset on the trigger (gap A)."""
from __future__ import annotations

import inspect
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence import grabber as g


@pytest.fixture
def _clean_energy():
    g.resume_watchdogs(force=True)
    g._prev_gray_small = None
    g._energy.clear()
    g._hold_until = 0.0
    g._sweeping.clear()
    g._ff_quiet_until = 0.0
    yield
    g._energy.clear()
    g._prev_gray_small = None
    g.resume_watchdogs(force=True)


def test_http_energy_route_exists():
    src = inspect.getsource(g.Handler.do_GET)
    assert '"/v1/energy"' in src
    assert "list_energy" in src


def test_health_exposes_energy():
    src = inspect.getsource(g.health)
    assert "energy_n" in src
    assert "energy_last" in src


def test_json_swallows_client_hangup():
    """Inspector/tabs cancel; BrokenPipe must not traceback or retry 500."""
    src = inspect.getsource(g.Handler._json)
    assert "BrokenPipeError" in src
    post = inspect.getsource(g.Handler.do_POST)
    assert "BrokenPipeError" in post
    main = inspect.getsource(g.main)
    assert "handle_error" in main
    assert "BrokenPipeError" in main


def test_note_and_list_energy(_clean_energy):
    g.note_energy(10, 1000.0, 1.2)
    g.note_energy(11, 1000.07, 9.4)
    g.note_energy(12, 1000.14, -1.0)
    body = g.list_energy()
    assert body["ok"] is True
    assert body["n"] == 3
    assert body["rows"][0]["energy"] == 1.2
    assert body["rows"][-1]["energy"] == -1.0
    since = g.list_energy(since_seq=10)
    assert [r["seq"] for r in since["rows"]] == [11, 12]
    capped = g.list_energy(max_n=1)
    assert capped["n"] == 1
    assert capped["rows"][0]["seq"] == 12


def test_onset_at_seq_walks_quiet(_clean_energy):
    t0 = 2000.0
    dt = 1.0 / 15.0
    for i in range(20):
        e = 1.0 if i < 12 else 11.0
        g.note_energy(100 + i, t0 + i * dt, e)
    out = g.onset_at_seq(119)
    assert out["onset_seq"] is not None
    assert int(out["onset_seq"]) < 112
    assert out["trigger_offset_s"] is not None
    assert out["trigger_offset_s"] > 0.2


def test_motion_records_subthreshold_and_onset(monkeypatch, _clean_energy):
    captured: list[dict] = []
    monkeypatch.setattr(g, "camera_is_ours", lambda now=None: False)
    monkeypatch.setattr(g, "get_ctrl", lambda *a, **k: g.DOOR_HOME_PAN)
    monkeypatch.setattr(
        g, "record_trigger", lambda **k: captured.append(k) or {"ok": True, **k}
    )

    dark = np.zeros((90, 160, 3), dtype=np.uint8)
    for i in range(12):
        g._maybe_motion_trigger(dark, i + 1, 3000.0 + i / 15.0)
    bright = np.full((90, 160, 3), 255, dtype=np.uint8)
    g._maybe_motion_trigger(bright, 13, 3000.0 + 12 / 15.0)

    rows = g.energy_rows()
    assert len(rows) == 13
    assert rows[0]["energy"] == 0.0  # first door-home, no prev
    assert all(r["energy"] >= 0 for r in rows)
    assert captured, "spike should fire a motion trigger"
    meta = captured[-1]["meta"]
    assert meta["energy"] >= g.TRIGGER_MOTION_MIN
    assert "onset_seq" in meta
    assert "trigger_offset_s" in meta


def test_pan_records_no_sample(monkeypatch, _clean_energy):
    monkeypatch.setattr(g, "camera_is_ours", lambda now=None: False)
    monkeypatch.setattr(g, "get_ctrl", lambda *a, **k: 0)  # factory home
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    g._maybe_motion_trigger(frame, 1, 4000.0)
    rows = g.energy_rows()
    assert len(rows) == 1
    assert rows[0]["energy"] == -1.0
