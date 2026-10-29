"""The chin pin (the User 2026-10-26, Bash's double chin on a lift: "pin chin to pixel of chin extent" / "two pins to
nose and chin tip"): lib/head_frame.pin_chin_pixel matches the landed tile's chin into the frame by pixels, and the
stretch along her axis moves the chin while the nose stays put. The conductor wiring is pinned by its source."""
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import head_frame as hf  # noqa: E402


def _face(chin_y: int, h: int = 240, w: int = 200) -> np.ndarray:
    """A grey frame with a lit face oval whose lower edge (the chin) sits at chin_y, dark neck below."""
    im = np.full((h, w, 3), 40, np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    oval = ((xx - 100) / 45.0) ** 2 + ((yy - (chin_y - 60)) / 60.0) ** 2 <= 1.0
    im[oval] = (170, 150, 140)
    im[(yy > chin_y + 4) & (np.abs(xx - 100) < 20)] = (90, 80, 75)
    return im


def test_the_chin_match_finds_her_chin_below_a_tile_chin_that_landed_high():
    base = _face(170)
    tile = _face(160)                          # the same face, its chin landed 10 px higher
    mv, ncc = hf.pin_chin_pixel(tile, base, (100, 160), s=120.0)
    dx, dy = mv
    assert ncc > 0.9
    assert abs(dx) <= 1 and abs(dy - 10) <= 1


def test_a_weak_or_far_chin_match_moves_nothing():
    base = np.random.default_rng(0).integers(0, 255, (240, 200, 3), dtype=np.uint8)
    tile = _face(160)
    mv, ncc = hf.pin_chin_pixel(tile, base, (100, 160), s=120.0)
    assert mv is None                          # a miss is None, so a true zero move is still a pixel answer
    assert hf.pin_chin_pixel(None, base, (100, 160), s=120.0) == (None, 0.0)


def test_the_stretch_keeps_the_nose_and_moves_the_chin_along_her_axis():
    M = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], np.float32)
    nose, chin = np.array([100.0, 100.0]), np.array([100.0, 160.0])
    M2 = hf.stretch_affine_axis(M, nose, (0.0, 1.0), 1.1)
    ap = lambda m, p: m[:, :2] @ p + m[:, 2]
    assert np.allclose(ap(M2.astype(np.float64), nose), nose, atol=1e-4)
    assert np.allclose(ap(M2.astype(np.float64), chin), [100.0, 166.0], atol=1e-3)
    assert np.allclose(ap(M2.astype(np.float64), np.array([140.0, 100.0])), [140.0, 100.0], atol=1e-3)   # across: nothing


def test_the_maps_stretch_moves_a_landed_pixel_the_same_way():
    H, W = 240, 200
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    maps = (xx.copy(), yy.copy())              # identity: dest pixel reads the same source pixel
    m2 = hf.stretch_maps_axis(maps, (100.0, 100.0), (0.0, 1.0), 1.1)
    # the source chin row 160 now lands at dest row 166
    assert abs(float(m2[1][166, 100]) - 160.0) <= 1.0
    assert float(m2[1][100, 100]) == 100.0
    assert hf.stretch_maps_axis(maps, (100.0, 100.0), (0.0, 1.0), 1.0) is maps


def test_the_conductor_wears_it_on_by_default_and_beside_the_pin():
    # ON by default since the User 2026-10-27 ("we solved a double chin on ups with a chin pin. We need that always")
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'MOUTH_CHIN_PIN = (MOUTH_PIN == "nose+chin-pixel"' in src
    assert 'os.environ.get("HOUSE_MOUTH_CHIN_PIN", "1")' in src
    assert re.search(r'if MOUTH_PIN == "nose\+chin-pixel":\n\s+MOUTH_PIN = "nose-pixel"', src)
    # both landings run it, after the nose pin, and the cheeks cache keys on it
    assert src.count("self._chin_stretch(rel, src, dst, base, warped") == 2
    assert 'pin_key = pin_key + ("chin-rest5", MOUTH_CHIN_PIN_LO, MOUTH_CHIN_PIN_HI)' in src
    # the class baseline is the tile's chin as it lands on the loop's rest pose (the median-pitch frame), not a
    # constant and not frame 0 (Bash's anchor frame is itself lifted)
    assert "def _chin_anchor_below(self, rel, src, tile, cheeks: bool):" in src
    assert "da = self._her_anchors(j0)" in src
    assert "med = float(np.median([p for p, _ in ps]))" in src
    # it IS a lock dial since the User 2026-10-27 ("maybe add a real control"): the lock saves pin nose+chin-pixel
    live = src.split("def live_dials() -> dict:", 1)[1].split("\ndef ", 1)[0]
    assert '"pin": pin_dial()' in live and '"chin_pin": MOUTH_CHIN_PIN' in live
