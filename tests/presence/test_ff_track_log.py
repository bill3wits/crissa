"""FaceFinder's correction, measured (the User 2026-10-02): pan/tilt and the scene's own shift at engage and release."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from crissa_presence import ff_track_log as ff

ROOT = Path(__file__).resolve().parents[1]


def _scene(seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.zeros((420, 640), np.uint8)
    for _ in range(160):
        x, y = int(rng.integers(10, 600)), int(rng.integers(10, 380))
        cv2.rectangle(img, (x, y), (x + int(rng.integers(6, 40)), y + int(rng.integers(6, 40))), int(rng.integers(60, 255)), -1)
    return img


def _jpeg(img: np.ndarray) -> bytes:
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()


@pytest.fixture
def on(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "LOG", tmp_path / "ff_track.jsonl")
    monkeypatch.setattr(ff, "SWITCH_FILE", tmp_path / "missing.on")
    monkeypatch.setenv("RAIL_FF_TRACK_LOG", "1")
    ff._start = None
    return tmp_path


def test_off_by_default_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("RAIL_FF_TRACK_LOG", raising=False)
    monkeypatch.setattr(ff, "SWITCH_FILE", tmp_path / "missing.on")
    monkeypatch.setattr(ff, "LOG", tmp_path / "ff_track.jsonl")
    assert ff.enabled() is False
    ff.engaged(lambda c: 0, lambda: b"x")
    assert ff.releasing(lambda c: 0, lambda: b"x") is None
    assert not (tmp_path / "ff_track.jsonl").exists()
    (tmp_path / "missing.on").write_text("")
    assert ff.enabled() is True, "the switch file turns it on without a restart"
    monkeypatch.setenv("RAIL_FF_TRACK_LOG", "0")
    assert ff.enabled() is False, "the env beats the file"


def test_an_engagement_logs_controls_and_the_scene_shift(on):
    big = _scene()
    before = big[40:400, 40:600]
    after = big[40 + 12:400 + 12, 40 - 25:600 - 25]          # camera moved: picture shifts +25 px right, -12 px up
    ctrl = {"pan_absolute": 0, "tilt_absolute": 0, "zoom_absolute": 100}
    ff.engaged(lambda c: ctrl[c], lambda: _jpeg(before), client="test")
    ctrl.update(pan_absolute=-36000, tilt_absolute=7200)
    row = ff.releasing(lambda c: ctrl[c], lambda: _jpeg(after))
    assert row["start"] == {"pan": 0, "tilt": 0, "zoom": 100}
    assert row["ctrl_delta"] == {"pan": -36000, "tilt": 7200, "zoom": 0}
    assert abs(row["shift"]["dx"] - 25) <= 2 and abs(row["shift"]["dy"] + 12) <= 2, row["shift"]
    assert row["client"] == "test"
    logged = [json.loads(x) for x in (on / "ff_track.jsonl").read_text().splitlines()]
    assert len(logged) == 1 and logged[0]["shift"]["dx"] == row["shift"]["dx"]


def test_no_movement_reads_zero_and_unreadable_controls_read_none(on):
    img = _jpeg(_scene()[40:400, 40:600])
    ff.engaged(lambda c: None, lambda: img)
    row = ff.releasing(lambda c: None, lambda: img)
    assert abs(row["shift"]["dx"]) < 1 and abs(row["shift"]["dy"]) < 1
    assert row["ctrl_delta"] == {"pan": None, "tilt": None, "zoom": None}


def test_a_release_with_no_engage_seen_logs_nothing(on):
    assert ff.releasing(lambda c: 0, lambda: b"") is None
    assert not (on / "ff_track.jsonl").exists()


def test_a_broken_reader_never_breaks_the_camera(on):
    def boom(_):
        raise RuntimeError("v4l2 gone")
    ff.engaged(boom, lambda: None)
    row = ff.releasing(boom, lambda: None)
    assert row["start"]["pan"] is None and row["shift"] is None


def test_the_grabber_logs_the_release_before_releasing():
    src = (ROOT.parent / "modules" / "presence" / "crissa_presence" / "grabber.py").read_text()
    i_log = src.index("ff_track_log.releasing(")
    i_rel = src.index("res = release_and_hold(V4L_DEV)")
    assert i_log < i_rel, "the end reading must come before the release (and so before any re-aim)"
    assert "ff_track_log.engaged(" in src
