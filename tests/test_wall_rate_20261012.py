"""Round 12, the desk Wall's mouth rate on .101 (2026-10-12; docs/checkpoints/round12-wall-rate-20261012).

.101 drew ~20 pictures/s while she spoke, under 2/s of them mouth patches, against a feed of 32-45/s: its ASCII cache
held 204 of the listen loop's 403 pictures at 3440x1440 (1024 MB), and after a density change it sat full of the old
grid's pictures and refused every new one for a minute. Pinned here: the budget holds a loop, another framing goes
first, a region is the same bytes drawn straight into them, the cells' lower bound never overstates the pixel box, the
child writes only a patch's rows, and the speed line counts what the glass painted.
"""
from __future__ import annotations

import mmap
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))
import house_wall_ascii_render as R  # noqa: E402
import wall_ascii_cache as C  # noqa: E402
import wall_film_proc as F  # noqa: E402
from test_wall_ascii_cache import W, H, _cache, _fresh_state, _jpeg, _mouth, _painted, _scene  # noqa: E402

WALL_SRC = (ROOT / "lib" / "house_wall_ascii.py").read_text()


def test_the_default_budget_holds_a_3440_listen_loop_and_a_tenth_of_free_memory_still_caps_it():
    G = 2 ** 30
    assert C.DEFAULT_MB == 4096
    pic = 3440 * 1440
    assert C.budget(0, {}, avail=79 * G) // pic >= 403 * 2            # .101: 79 GB free, the loop twice over
    assert C.budget(0, {}, avail=6 * G) == int(0.6 * G)               # a small machine: 10 % of free memory
    assert C.budget(0, {"HOUSE_WALL_ASCII_CACHE_MB": "1024"}, avail=79 * G) == 1024 * 2 ** 20   # the owner's word


def test_a_full_cache_evicts_another_framing_first_instead_of_refusing_everything(monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_ZMEM", "0")    # the budget below is counted in raw pictures (round 17)
    _fresh_state()
    c = _cache()
    g = R.Geom(W, H, density=1.0)
    old = [_jpeg(_scene(200 + i)) for i in range(6)]
    p, _, _ = c.picture(old[0], g, True, 0.0)
    c.cap = 6 * p.nbytes + 4 * 60000
    c.pics.clear(), c.by_idx.clear(), c.by_band.clear(), c.by_jpeg.clear()
    c.bytes = c.jpeg_bytes = 0
    for jp in old:
        c.picture(jp, g, True, 0.0)                                   # full, every picture young
    held_old = set(c.pics)
    # the crop slide changes (a new framing): the old pictures can never be handed out now
    for jp in [_jpeg(_scene(300 + i)) for i in range(4)]:
        c.picture(jp, g, True, 0.0, shift=(0.0, 0.12))
    assert c.n["reframed"] >= 4 and c.n["refused"] == 0
    assert len(held_old & set(c.pics)) <= 2


def test_the_kill_puts_back_the_minute_of_refusals(monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_REFRAME", "0")
    monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_ZMEM", "0")    # the budget below is counted in raw pictures (round 17)
    _fresh_state()
    c = _cache()
    g = R.Geom(W, H, density=1.0)
    p, _, _ = c.picture(_jpeg(_scene(400)), g, True, 0.0)
    c.cap = 2 * p.nbytes + 4 * 60000
    for i in range(3):
        c.picture(_jpeg(_scene(401 + i)), g, True, 0.0)
    for i in range(3):
        c.picture(_jpeg(_scene(500 + i)), g, True, 0.0, shift=(0.0, 0.12))
    assert c.n["reframed"] == 0 and c.n["refused"] >= 3


def test_the_framing_key_ignores_the_whole_frames_face_point():
    _fresh_state()
    c = _cache()
    g = R.Geom(W, H, density=1.0)
    c.picture(_jpeg(_scene(600)), g, True, 0.0, frame=("whole", 1.0, 0.50, 0.32))
    a = c.fk_now
    c.picture(_jpeg(_scene(601)), g, True, 0.0, frame=("whole", 1.0, 0.61, 0.40))     # a flourish's own face point
    assert c.fk_now == a
    c.picture(_jpeg(_scene(602)), g, True, 0.0, frame=("whole", 1.5, 0.61, 0.40))     # the User zoomed
    assert c.fk_now != a


def test_a_region_drawn_straight_into_its_bytes_is_the_whole_render():
    _fresh_state()
    c = _cache()
    g = R.Geom(W, H, density=1.0)
    c.prepare(g)
    base_im = _scene(700)
    box = (330, 220, 60, 22)
    prev = None
    hows = []
    for k in range(6):
        jp = _jpeg(_mouth(base_im, box, k))
        p, how, _ = c.picture(jp, g, True, 0.0, prev=prev)
        hows.append(how)
        assert bytes(p.px) == R.coverage_fresh(R._open(jp), g, True, 0.0).tobytes(), (k, how)
        prev = p
    assert "region" in hows, hows


def test_the_cells_lower_bound_never_overstates_the_pixel_box():
    _fresh_state()
    c = _cache()
    g = R.Geom(W, H, density=1.0)
    c.prepare(g)
    pal = R.palette_for(0.8)
    seq = [_jpeg(_scene(s)) for s in (800, 801, 802)] + [_jpeg(_mouth(_scene(802), (330, 220, 60, 22), k)) for k in range(3)]
    prev = None
    for jp in seq:
        p, _, region = c.picture(jp, g, True, 0.0, prev=prev)
        if prev is not None and region is None:
            span = c._cells_span(prev, p)
            bb = ImageChops.difference(Image.frombytes("RGB", (W, H), _painted(prev, pal)),
                                       Image.frombytes("RGB", (W, H), _painted(p, pal))).getbbox()
            area = ((bb[2] - bb[0]) * (bb[3] - bb[1]) / float(W * H)) if bb else 0.0
            assert span <= area + 1e-9, (span, area)
            if span > 0.4:
                assert c.box(prev, pal, p, pal, None, whole_over=0.4) is None
        prev = p


def test_the_child_writes_only_a_patchs_rows_and_nothing_for_no_change():
    w, h = 64, 32
    px = bytes(range(256)) * (w * h // 256)
    mm = bytearray(b"\xee" * (w * h * 3))
    F._lay(mm, px, (w, h), (8, 4, 24, 12), 0.4)
    a = np.frombuffer(bytes(mm[:w * h]), np.uint8).reshape(h, w)
    want = np.frombuffer(px, np.uint8).reshape(h, w)
    assert (a[4:12, 8:24] == want[4:12, 8:24]).all()
    a2 = a.copy(); a2[4:12, 8:24] = 0xEE
    assert (a2 == 0xEE).all()                                          # nothing outside the box
    mm2 = bytearray(b"\xee" * (w * h * 3))
    F._lay(mm2, px, (w, h), (0, 0, 0, 0), 0.4)
    assert mm2 == bytearray(b"\xee" * (w * h * 3))                    # no change: nothing written
    F._lay(mm2, px, (w, h), (0, 0, w, h), 0.4)                         # past the limit: the whole picture
    assert bytes(mm2[:w * h]) == px
    F._lay(mm2, px[::-1], (w, h), (8, 4, 24, 12), None)                # an older parent (no box_max): all of it
    assert bytes(mm2[:w * h]) == px[::-1]


def test_the_parent_names_its_patch_limit_and_the_kill_drops_it(monkeypatch):
    assert "ask[\"box_max\"] = float(patch_max)" in (ROOT / "lib" / "wall_film_proc.py").read_text()
    monkeypatch.setenv("HOUSE_WALL_FILM_BOX_LEAN", "0")
    assert not F.box_lean_enabled()
    monkeypatch.delenv("HOUSE_WALL_FILM_BOX_LEAN")
    assert F.box_lean_enabled()


def test_the_speed_line_counts_the_glass_not_the_clip():
    assert 'speed["glass"] += 1' in WALL_SRC and 'speed["glass_patch"] += 1' in WALL_SRC
    # round 15 (.101's frozen build): a pack's line says what was DRAWN into the window; the house keeps this one
    assert "({speed['drawn'] / speed['every']:.1f}/s drawn:" in WALL_SRC
    assert "({(speed['glass'] + speed['glass_patch']) / speed['every']:.1f}/s on the glass:" in WALL_SRC
    assert "({1 / present_period / stride:.0f}/s on the glass)" not in WALL_SRC
    assert 'f"resting ({REST_FPS:g}/s on the glass)" if REST["on"]' in WALL_SRC     # round 4's pin still holds


def test_the_paint_trace_is_off_unless_named():
    assert 'PAINT_TRACE = os.environ.get("HOUSE_WALL_PAINT_TRACE", "").strip()' in WALL_SRC
