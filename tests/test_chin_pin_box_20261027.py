"""The chin pin reads only the tile's own box (2026-10-27, Crissa desk v2 at 1280x720: the whole-frame mask in
pin_chin_pixel was 15 ms a call). Same answers as the whole-frame read, byte for byte."""
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import head_frame as hf  # noqa: E402


def _scene(seed=3, H=720, W=1280):
    rng = np.random.default_rng(seed)
    tile = cv2.GaussianBlur((rng.random((360, 360, 3)) * 255).astype(np.uint8), (0, 0), 3)
    mx = np.full((H, W), -1, np.float32)
    my = mx.copy()
    yy, xx = np.mgrid[0:300, 0:300].astype(np.float32)
    mx[300:600, 500:800] = xx * 1.02 + 20.3
    my[300:600, 500:800] = yy * 0.98 + 25.7
    full = cv2.remap(tile, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    base = cv2.GaussianBlur((rng.random((H, W, 3)) * 255).astype(np.uint8), (0, 0), 3)
    base[310:590, 510:790] = np.roll(full, (4, -3), axis=(0, 1))[310:590, 510:790]
    return tile, (mx, my), full, base


def test_pin_chin_pixel_in_the_box_is_the_whole_frame_answer():
    tile, maps, full, base = _scene()
    roi = hf.crop_maps(maps)
    box = (roi[0], roi[1], roi[0] + roi[2].shape[1], roi[1] + roi[2].shape[0])
    for chin in [(650, 520), (640, 560), (700, 590), (520, 320), (100, 100)]:
        for s in (120.0, 300.0):
            assert hf.pin_chin_pixel(full, base, chin, s) == hf.pin_chin_pixel(full, base, chin, s, box=box)


def test_the_conductor_remaps_inside_the_box_byte_for_byte():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    body = src.split("    def _remap_in_box(tile, maps):", 1)[1].split("\n    def ", 1)[0]
    ns = {"np": np, "cv2": cv2, "_hf_mod": hf}
    exec("def _remap_in_box(tile, maps):" + body, ns)
    tile, maps, full, _ = _scene()
    warped, box = ns["_remap_in_box"](tile, maps)
    assert np.array_equal(warped, full)
    assert box == (500, 300, 800, 600)
    assert src.count("self._remap_in_box(tile, maps") == 2      # the landing and the rest-pose baseline


def test_landed_in_the_box_is_the_whole_frame_answer():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    body = src.split("    def _landed(self, maps, Maff, p)", 1)[1].split("\n    def ", 1)[0]

    class P:
        pass
    ns = {"np": np, "_hf_mod": hf, "MP": None}
    exec("def _landed(self, maps, Maff, p)" + body, ns)
    _, maps, _, _ = _scene()
    rng = np.random.default_rng(1)
    pts = [(float(x), float(y)) for x, y in rng.uniform(-5, 380, (200, 2))] + [(-1.0, -1.0), (0.0, 0.0), (20.3, 25.7)]
    for on in (True, False):
        ns["MOUTH_LANDED_ROI"] = on
        got = [ns["_landed"](P(), maps, None, q) for q in pts]
        if on:
            box = got
        else:
            full = got
    assert [None if g is None else tuple(g) for g in box] == [None if g is None else tuple(g) for g in full]
    assert sum(g is not None for g in full) > 100
