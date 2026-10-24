""".101 round 46 overnight (2026-10-24), the Wall's picture cache at 3440x1440:

- O1: "a region answer differed from the fresh render at 3440x1440 (band base, box (1224, 200, 1624, 1016))", again 2 min
  after a restart. Pillow 13 boxes a TALL crop in the other pass order than the wide glass, so a region's glow input was
  up to a level off the whole picture's; the region boxes in the glass's own order now, and the self-check tries a tall
  and a wide block near REGION_MAX (the six small ones passed).
- O2: an off cache still held 5 GB. It gives everything back.
- O4: a restart that did not help is not offered again.
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import house_wall_ascii_render as R  # noqa: E402
import wall_ascii_cache as C  # noqa: E402
import crissa_build as zb  # noqa: E402
import crissa_home  # noqa: E402


def _whole(idx, g, vinv):
    m = R.mask_from_idx(idx, g)
    small = R.glow_small(m, g)
    return ImageChops.multiply(ImageChops.screen(m, R.glow_from_small(small, g)), vinv), small


def test_the_glow_input_boxes_a_tall_crop_as_the_whole_glass_does():
    rng = np.random.default_rng(1)
    for W, H in ((3440, 1440), (2560, 1440), (1920, 1080)):
        im = Image.fromarray(rng.integers(0, 256, (H, W), dtype=np.uint8), "L")
        whole = np.asarray(im.resize((W // 8, H // 8), Image.Resampling.BOX))
        assert np.array_equal(np.asarray(C._box_down(im, W // 8, H // 8)), whole)     # the look is today's
        for bw, bh, x0, y0 in ((128, 512, 0, 0), (40, 600, 800, 200), (296, 696, 1200, 64), (512, 128, 8, 8)):
            got = np.asarray(C._box_down(im.crop((x0, y0, x0 + bw, y0 + bh)), bw // 8, bh // 8))
            assert np.array_equal(got, whole[y0 // 8:(y0 + bh) // 8, x0 // 8:(x0 + bw) // 8]), (W, H, bw, bh)


def test_a_tall_and_a_wide_region_at_3440_are_the_whole_render():
    g = R.Geom(3440, 1440, density=2.08)
    c = C.AsciiCache()
    c.cap, c.cap_at = 3 << 30, time.monotonic() + 1e9
    geo = c.prepare(g)
    assert geo.region_ok
    rng = np.random.default_rng(3)
    base_idx = rng.integers(0, len(R.RAMP), (g.ROWS, g.COLS)).astype(np.uint8)
    cov, small = _whole(base_idx, g, geo.vinv)
    base = c._new(("t",), base_idx.tobytes(), cov, small, g.ROWS)
    # .101's box was 400 x 816 px over her head and mouth: ~80 x 102 cells here; and a wide one
    for r0, c0, h, w in ((25, 245, 100, 80), (40, 100, 20, 250), (2, 344, 85, 53)):
        new = base_idx.copy()
        new[r0:r0 + h, c0:c0 + w] = rng.integers(0, len(R.RAMP), (h, w))
        box = c._padded(geo, c0, c0 + w, r0, r0 + h)
        (buf, _), sm = c._draw_region(geo, g, new, base, box)
        want, wsmall = _whole(new, g, geo.vinv)
        assert sm.tobytes() == wsmall.tobytes(), (r0, c0, h, w)
        assert bytes(buf) == want.tobytes(), (r0, c0, h, w)


def test_the_self_check_turns_regions_off_when_a_tall_crop_boxes_differently(monkeypatch):
    real = C._box_down

    def other_order(im, w, h):                      # a library that boxes a tall crop vertical-first
        if im.size[1] > im.size[0]:
            return im.resize((im.size[0], h), Image.Resampling.BOX).resize((w, h), Image.Resampling.BOX)
        return real(im, w, h)
    monkeypatch.setattr(C, "_box_down", other_order)
    logs = []
    c = C.AsciiCache(log=logs.append)
    assert not c.prepare(R.Geom(3440, 1440, density=2.08)).region_ok
    assert any("region self-check failed" in m for m in logs)


def test_the_kill_is_the_one_call_resize_and_the_self_check_judges_it(monkeypatch):
    monkeypatch.setattr(C, "BOX2", False)
    im = Image.fromarray(np.random.default_rng(2).integers(0, 256, (600, 40), dtype=np.uint8), "L")
    assert C._box_down(im, 5, 75).tobytes() == im.resize((5, 75), Image.Resampling.BOX).tobytes()
    c = C.AsciiCache()
    geo = c.prepare(R.Geom(3440, 1440, density=2.08))
    big = np.asarray(Image.fromarray(np.random.default_rng(1).integers(0, 256, (512, 128), dtype=np.uint8), "L")
                     .resize((16, 64), Image.Resampling.BOX))
    two = np.asarray(Image.fromarray(np.random.default_rng(1).integers(0, 256, (512, 128), dtype=np.uint8), "L")
                     .resize((16, 512), Image.Resampling.BOX).resize((16, 64), Image.Resampling.BOX))
    assert geo.region_ok == bool(np.array_equal(big, two))      # on only where this Pillow boxes a tall crop alike


def test_an_off_cache_gives_its_memory_back(monkeypatch):
    monkeypatch.delenv("HOUSE_WALL_ASCII_CACHE_RELEASE", raising=False)
    logs = []
    c = C.AsciiCache(log=logs.append)
    c.cap, c.cap_at = 3 << 30, time.monotonic() + 1e9
    g = R.Geom(640, 360, density=1.0)
    for k in range(6):
        c.picture(None, g, True, float(k * 40))
    assert c.pics and c.held_bytes() > 0
    c.dead = "a region answer differed from the fresh render at 640x360"
    assert not c.usable()
    assert not c.pics and not c.by_idx and not c.by_band and not c.by_jpeg and c.held_bytes() == 0
    assert c.stats()["bytes_held"] == 0 and c.stats()["pictures"] == 0
    assert any("given back" in m for m in logs)
    monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_RELEASE", "0")        # the kill keeps today's hold
    c2 = C.AsciiCache()
    c2.cap, c2.cap_at = 3 << 30, time.monotonic() + 1e9
    c2.picture(None, g, True, 0.0)
    c2.dead = "x"
    assert not c2.usable() and c2.pics


def test_a_restart_that_did_not_help_is_not_offered_again(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.delenv("CRISSA_PLATE_OFF_STARTS", raising=False)
    built = {"running": False, "loops_total": 10, "loops_built": 10, "name": "Crissa"}

    def lap():
        return json.loads((zb.root(tmp_path) / "lap.json").read_text())
    zb.note_lap("listen-warm", 1116, 1116, 560.0, home=tmp_path, cache_off="a region answer differed")
    zb.note_lap("listen-warm", 1116, 1116, 560.0, home=tmp_path, cache_off="a region answer differed")
    assert lap()["cache_off_starts"] == 1                            # one Wall process, however many laps
    assert "restart the Wall" in zb.plate_view(dict(built, lap=lap()))["line"]
    # the User restarts the Wall (a new process) and the check fails again
    f = zb.root(tmp_path) / "cache-off.json"
    rec = json.loads(f.read_text())
    f.write_text(json.dumps({"pids": [os.getpid() + 1000000] + rec["pids"][1:], "at": rec["at"]}))
    zb.note_lap("listen-warm", 1116, 1116, 560.0, home=tmp_path, cache_off="a region answer differed")
    v = zb.plate_view(dict(built, lap=lap()))
    assert lap()["cache_off_starts"] == 2 and v["title"] == "Picture cache off"
    assert "restart" not in v["line"] and "fine" in v["line"]
    monkeypatch.setenv("CRISSA_PLATE_OFF_STARTS", "0")
    assert "restart the Wall" in zb.plate_view(dict(built, lap=lap()))["line"]
    # a start whose cache stays on clears the count
    zb.note_lap("listen-warm", 1116, 0, 560.0, home=tmp_path)
    assert not f.exists() and "cache_off_starts" not in lap()
