"""Painter size from the library (the User 2026-10-15, Crissa phone S0.1).

A library may paint at its own size (a 405x720 vertical cut). head_frame.frame_wh(pose_dir) reads the inventory's
`size`, else the frame of the avatar manifest that names the library, else 736x400. HOUSE_FRAME_FROM_LIBRARY is
OFF by default, and OFF is 736x400 verbatim. The conductor reads the size through one accessor (paint_wh) and never
through a bare CLIP_W/CLIP_H: one missed site would warp a 405 library into a 736x400 canvas and cut her off at
x=405.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import head_frame as hf  # noqa: E402

ON = {"HOUSE_FRAME_FROM_LIBRARY": "1"}
OFF = {"HOUSE_FRAME_FROM_LIBRARY": "0"}


def _lib(tmp_path: Path, name: str, size=None) -> Path:
    d = tmp_path / name
    d.mkdir()
    inv = {"schema": "pose_inventory/v1", "clips": {}}
    if size is not None:
        inv["size"] = size
    (d / "inventory.json").write_text(json.dumps(inv))
    return d


def test_off_is_the_house_constant_whatever_the_library_says(tmp_path):
    d = _lib(tmp_path, "crissa-poses", [405, 720])
    assert hf.frame_wh(d, env=OFF) == (736, 400)
    assert hf.frame_wh(d, env={}) == (736, 400), "unset is off"
    assert hf.frame_wh(None, env=ON) == (736, 400)


def test_on_reads_the_inventory_size(tmp_path):
    d = _lib(tmp_path, "crissa-poses", [405, 720])
    assert hf.frame_wh(d, env=ON) == (405, 720)


def test_on_falls_back_to_the_manifest_frame_then_the_constant(tmp_path, monkeypatch):
    d = _lib(tmp_path, "phone-test-poses")          # an inventory with no size
    av = tmp_path / "avatars"
    (av / "phonetest").mkdir(parents=True)
    (av / "phonetest" / "manifest.json").write_text(json.dumps(
        {"name": "phonetest", "poses_dir": "display/phone-test-poses", "frame": {"w": 405, "h": 720, "fps": 24}}))
    monkeypatch.setenv("HOUSE_AVATARS_DIR", str(av))
    assert hf.frame_wh(d, env=ON) == (405, 720)
    bare = _lib(tmp_path, "nobody-poses")
    assert hf.frame_wh(bare, env=ON) == (736, 400)


def test_a_changed_inventory_is_read_again(tmp_path):
    d = _lib(tmp_path, "crissa-poses", [405, 720])
    assert hf.frame_wh(d, env=ON) == (405, 720)
    import os
    import time
    (d / "inventory.json").write_text(json.dumps({"size": [1080, 1920]}))
    t = time.time() + 5
    os.utime(d / "inventory.json", (t, t))
    assert hf.frame_wh(d, env=ON) == (1080, 1920)


def test_crissas_library_is_736x400_on_and_off():
    z = ROOT / "display" / "crissa-poses"
    assert hf.frame_wh(z, env=ON) == (736, 400)
    assert hf.frame_wh(z, env=OFF) == (736, 400)


def test_the_conductor_reads_the_size_through_one_accessor():
    src = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    lines = src.split("\n")
    body = src.split("def paint_wh() -> tuple[int, int]:", 1)
    assert len(body) == 2, "paint_wh is the accessor"
    acc = body[1].split("\n\n\n", 1)[0]
    bare = []
    for i, ln in enumerate(lines, 1):
        code = ln.split("#", 1)[0]
        if re.search(r"\bCLIP_[WH]\b", code) and ln.strip() != "CLIP_W, CLIP_H = 736, 400" and ln not in acc:
            bare.append("%d: %s" % (i, ln.strip()))
    assert not bare, "a bare CLIP_W/CLIP_H outside paint_wh:\n" + "\n".join(bare)
    assert src.count("paint_wh()") >= 35


def test_the_head_tracker_and_the_intake_follow_the_library():
    tracker = (ROOT / "scripts" / "pose-head-frame.py").read_text(encoding="utf-8")
    assert "CLIP_W" not in tracker.split("SIZE_OVERRIDE", 1)[1], "the tracker decodes and stamps in paint_wh()"
    assert '"src_wh": list(paint_wh()),' in tracker
    assert 'ap.add_argument("--size"' in tracker
    intake = (ROOT / "lib" / "pose_intake.py").read_text(encoding="utf-8")
    assert "fw, fh = frame_wh(POSE)" in intake and "x + w > 736" not in intake


def test_the_solve_and_maps_caches_key_on_the_size(monkeypatch):
    import poly_cache as pc
    calls = []

    def fake_solve(dst, wh, scale=1.0):
        calls.append(tuple(wh))
        return {"t": np.zeros((2, 2)), "h": np.zeros((2, 2))}

    monkeypatch.setattr(pc, "cheeks_dest_solve", fake_solve)
    monkeypatch.setattr(pc, "cheeks_cache_on", lambda: True)
    monkeypatch.setattr(pc, "cheeks_solve_limit", lambda: 64)
    dst = {"nose": [100.0, 120.0]}
    pc.cheeks_solve_cached("frame-size-test", 3, "v", dst, (736, 400), 1.0)
    pc.cheeks_solve_cached("frame-size-test", 3, "v", dst, (405, 720), 1.0)
    pc.cheeks_solve_cached("frame-size-test", 3, "v", dst, (405, 720), 1.0)
    assert calls == [(736, 400), (405, 720)], "a 405 solve is never served for a 736 frame (and it is cached)"
    src = (ROOT / "lib" / "poly_cache.py").read_text(encoding="utf-8")
    maps = src.split("def cheeks_maps_cached(", 1)[1].split("\ndef ", 1)[0]
    assert "(int(wh[0]), int(wh[1]))" in maps, "the maps key (and the disk key built from it) carries wh"
