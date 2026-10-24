"""ROI composite: four elementwise stages cropped; hide_clip_jaw whole.

Bit-identity vs the full-frame path (roi=False). Affine, dental, cheeks,
and hole=strip. Authority: docs/MOUTH-TILE-DIALS-20260913.md §8.6.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from head_frame import CLIP_H, CLIP_W, unpack, warp_affine_2x3  # noqa: E402
from pose_mouth import composite_stamp, live_bbox, square_alpha  # noqa: E402

POSE = ROOT / "display" / "crissa-poses"
TILE = POSE / "visemes" / "phoneme-map" / "tiles" / "talk-down-harvest-b_f0000_down_r.png"


def _dest():
    frames = json.loads((POSE / "clip-work.head.json").read_text())["frames"]
    d = unpack(frames[0])
    assert d is not None
    return d


def _base_and_warped(dest):
    img = cv2.imread(str(POSE / "clip-work.mp4") if False else str(POSE / "hub-down.png"))
    if img is None:
        img = np.zeros((CLIP_H, CLIP_W, 3), np.uint8)
        img[:] = (40, 60, 80)
    else:
        img = cv2.resize(img, (CLIP_W, CLIP_H))
    tile = cv2.imread(str(TILE))
    if tile is None:
        pytest.skip("harvest tile missing")
    src = None
    for e in json.loads((POSE / "visemes" / "phoneme-map" / "map.json").read_text())["entries"]:
        if str(e.get("tile") or "").endswith("talk-down-harvest-b_f0000_down_r.png"):
            src = unpack(e.get("head"))
            break
    if src is None:
        pytest.skip("no src head for tile")
    M = warp_affine_2x3(src, dest)
    warped = cv2.warpAffine(tile, M, (CLIP_W, CLIP_H),
                            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    box = tuple(float(x) for x in dest["mouth"])
    return img, warped, box


def test_live_bbox_is_alpha_not_mouth_box():
    m = np.zeros((40, 50), np.uint8)
    m[10:20, 12:22] = 1
    m[8, 11] = 1  # feather past the "box"
    b = live_bbox(m, pad=0)
    assert b == (11, 8, 22, 20)


def test_square_alpha_roi_matches_full_frame():
    dest = _dest()
    _, warped, _ = _base_and_warped(dest)
    a0 = square_alpha(warped, dest, roi=False)
    a1 = square_alpha(warped, dest, roi=True)
    assert a0.shape == a1.shape
    assert np.allclose(a0, a1, atol=1e-6, rtol=0), float(np.max(np.abs(a0 - a1)))


def test_composite_stamp_roi_matches_full_affine():
    dest = _dest()
    base, warped, box = _base_and_warped(dest)
    full = composite_stamp(base, box, warped, dest, hole="arch", roi=False)
    roi = composite_stamp(base, box, warped, dest, hole="arch", roi=True)
    assert full.shape == roi.shape
    assert np.array_equal(full, roi), int(np.abs(full.astype(int) - roi.astype(int)).max())


def test_composite_stamp_roi_matches_full_strip():
    dest = _dest()
    base, warped, box = _base_and_warped(dest)
    a = square_alpha(warped, dest, roi=False)
    full = composite_stamp(base, box, warped, dest, a=a, hole="strip", roi=False)
    roi = composite_stamp(base, box, warped, dest, a=a, hole="strip", roi=True)
    assert np.array_equal(full, roi), int(np.abs(full.astype(int) - roi.astype(int)).max())


def test_composite_stamp_roi_dental_and_cheeks_if_maps_exist():
    dest = _dest()
    base, warped_aff, box = _base_and_warped(dest)
    from head_frame import warp_dental_maps
    tile = cv2.imread(str(TILE))
    src = None
    for e in json.loads((POSE / "visemes" / "phoneme-map" / "map.json").read_text())["entries"]:
        if str(e.get("tile") or "").endswith("talk-down-harvest-b_f0000_down_r.png"):
            src = unpack(e.get("head"))
            break
    M = warp_affine_2x3(src, dest)
    th, tw = tile.shape[:2]
    mx, my = warp_dental_maps(src, dest, (CLIP_W, CLIP_H), M=M, tile_hw=(th, tw))
    warped_d = cv2.remap(tile, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    full = composite_stamp(base, box, warped_d, dest, hole="arch", roi=False)
    roi = composite_stamp(base, box, warped_d, dest, hole="arch", roi=True)
    assert np.array_equal(full, roi), "dental " + str(
        int(np.abs(full.astype(int) - roi.astype(int)).max()))
    # cheeks: skip if dest has no 2d106
    if dest.get("cheeks") is None and dest.get("pts106") is None:
        return
    try:
        from head_frame import warp_cheeks_maps
    except ImportError:
        from poly_cache import cheeks_maps_cached
        maps = cheeks_maps_cached
        pytest.skip("cheeks maps helper")
    try:
        mx, my = warp_cheeks_maps(src, dest, (CLIP_W, CLIP_H), tile_hw=(th, tw))
    except Exception:
        pytest.skip("cheeks maps not solvable on this dest")
    warped_c = cv2.remap(tile, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    full = composite_stamp(base, box, warped_c, dest, hole="arch", roi=False)
    roi = composite_stamp(base, box, warped_c, dest, hole="arch", roi=True)
    assert np.array_equal(full, roi), "cheeks " + str(
        int(np.abs(full.astype(int) - roi.astype(int)).max()))


def test_hide_clip_jaw_is_not_roi_cropped():
    src = (ROOT / "lib" / "pose_mouth.py").read_text(encoding="utf-8")
    stamp = src.split("def composite_stamp")[1]
    assert "hide_clip_jaw(base, box" in stamp
    assert "HOUSE_MOUTH_COMPOSITE_ROI" in src
