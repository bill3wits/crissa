"""Motion ghost + heading — synthetic moving blob."""

from __future__ import annotations

import numpy as np

from crissa_presence.motion_vision import (
    build_motion_pack,
    heading_from_motion,
    temporal_trail,
)


def _blob_sequence(*, n: int = 12, w: int = 160, h: int = 90, dx: int = 8):
    """White square marching right across black frames."""
    frames = []
    for i in range(n):
        im = np.zeros((h, w, 3), dtype=np.uint8)
        x = 10 + i * dx
        y = h // 2 - 8
        im[y : y + 16, x : x + 16] = 255
        frames.append(im)
    return frames


def test_temporal_trail_preserves_shape():
    frames = _blob_sequence()
    trail = temporal_trail(frames)
    assert trail is not None
    assert trail.shape == frames[0].shape


def test_heading_right_on_moving_blob():
    frames = _blob_sequence(dx=10)
    h = heading_from_motion(frames)
    assert h["ok"] is True
    assert h["label"] == "right"
    assert h["dx"] > 0


def test_heading_still_on_static():
    im = np.zeros((90, 160, 3), dtype=np.uint8)
    im[40:50, 70:80] = 200
    frames = [im.copy() for _ in range(8)]
    h = heading_from_motion(frames)
    assert h["label"] in ("still", "unknown")


def test_build_motion_pack_writes(tmp_path):
    import cv2

    frames = _blob_sequence()
    jpegs = []
    for im in frames:
        ok, buf = cv2.imencode(".jpg", im)
        assert ok
        jpegs.append(buf.tobytes())
    meta = build_motion_pack(jpegs, out_dir=tmp_path, write_video=True)
    assert meta["ok"] is True
    assert meta["heading"]["label"] == "right"
    assert (tmp_path / "ghost_heading.jpg").is_file()
    assert (tmp_path / "ghost_trail.jpg").is_file()
