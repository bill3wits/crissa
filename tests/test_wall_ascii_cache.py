"""The wall's ASCII cache (the User 2026-10-11: "ascii cache sounds like a huge win with zero downside ... no need to
paint cached loops"; default ON only if UNDETECTABLE, byte-identical to a fresh render).

The reference is the fresh render: render_background with the glow made for the frame (R._GLOW cleared first), which
is R.background_fresh. Every picture the cache hands out must equal it byte for byte: from the JPEG's bytes, from the
glyph grid, drawn as a region over a remembered picture, or drawn whole; at the house's three glass geometries and on
both sides of the brightness dial's ink point; on every frame of every core loop of both faces, and with mouths
painted in.
"""
from __future__ import annotations

import gc
import hashlib
import io
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import house_wall_ascii_render as R  # noqa: E402
import wall_ascii_cache as C  # noqa: E402
import wall_film_proc as F  # noqa: E402

GEOMS = ((2560, 1440, 1.0), (3440, 1440, 1.0), (3440, 1440, 2.08))
BRIGHT = (0.8, 1.3)              # under the ink point and over it (wall_prefs.BRIGHT_FULL_WALL is 1.0)
W, H = 640, 360


def _fresh_state():
    for v in vars(R).values():
        if callable(getattr(v, "cache_clear", None)):
            v.cache_clear()
    R._GLOW.update(n=0, im=None, fp=None, out=None)


def _reference(jpeg, g, own, t, brightness, hole_zoom=None, shift=None) -> bytes:
    """render_background with the glow made for this frame."""
    R._GLOW.update(n=0, im=None, fp=None, out=None)
    return R.render_background(R._open(jpeg) if jpeg else None, g, own, t, hole_zoom=hole_zoom,
                               brightness=brightness, shift=shift).tobytes()


def _painted(p, pal) -> bytes:
    """What the wall's film process makes of the child's coverage (FilmProcess.render_patch, mode L)."""
    im = Image.frombytes("L", p.size, p.px)
    im.putpalette(pal)
    return im.convert("RGB").tobytes()


def _cache(**kw) -> C.AsciiCache:
    c = C.AsciiCache(**kw)
    c.cap = 3 << 30
    c.cap_at = time.monotonic() + 1e9           # the test owns the budget
    return c


def _jpeg(im: Image.Image, q: int = 86) -> bytes:
    b = io.BytesIO()
    im.save(b, "JPEG", quality=q)
    return b.getvalue()


def _scene(seed: int, size=(736, 400)) -> Image.Image:
    """A frame with a face-like figure where the house's films put her, and a mouth box."""
    rng = np.random.default_rng(seed)
    a = (rng.random((size[1] // 8, size[0] // 8, 3)) * 90).astype(np.uint8)
    im = Image.fromarray(a, "RGB").resize(size, Image.Resampling.BILINEAR)
    d = ImageDraw.Draw(im)
    d.ellipse((300, 90, 420, 290), fill=(200, 170, 150))
    d.rectangle((250, 290, 470, 400), fill=(30, 30, 40))
    return im


def _mouth(im: Image.Image, box, k: int) -> Image.Image:
    out = im.copy()
    x, y, w, h = box
    d = ImageDraw.Draw(out)
    o = 2 + (k * 5) % max(3, h)
    d.ellipse((x + 4, y + h // 2 - o // 2, x + w - 4, y + h // 2 + o // 2), fill=(40 + 20 * (k % 3), 10, 20))
    return out


# ---- the core loops' every frame --------------------------------------------------------------------------------
def _core_loops() -> list[tuple[str, Path]]:
    """Every loop the two faces' inventories name (role loop): (name, film)."""
    out = []
    for face in ("crissa-poses", "lila-poses"):
        inv = json.loads((ROOT / "display" / face / "inventory.json").read_text(encoding="utf-8"))
        rows = inv.get("clips") if isinstance(inv.get("clips"), dict) else inv
        for key, row in rows.items():
            if isinstance(row, dict) and row.get("role") == "loop" and row.get("file"):
                out.append((key, ROOT / "display" / face / row["file"]))
    return out


@lru_cache(maxsize=None)
def _film_jpegs(path: str) -> tuple:
    """The conductor's feed of a film: each frame at 736x400 (INTER_AREA), JPEG q86, and its mouth box."""
    import cv2
    cap = cv2.VideoCapture(path)
    out = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if fr.shape[1] != 736 or fr.shape[0] != 400:
            fr = cv2.resize(fr, (736, 400), interpolation=cv2.INTER_AREA)
        out.append(cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 86])[1].tobytes())
    try:
        boxes = json.loads(Path(str(path)[:-len(".mp4")] + ".mouth.json").read_text())["frames"]
    except (OSError, ValueError, KeyError):
        boxes = [None] * len(out)
    return tuple(out), tuple(tuple(b) if b else None for b in boxes)


# THE SWEEP'S MEMORY (2026-10-12): it kept every frame's reference picture (RGB, both brightness values) for a whole
# loop at the largest glass - 3440x1440 is 14.9 MB a picture, about 10.7 GB a job - and gave its cache 3 GiB, in three
# forked workers at once: two runs side by side took the house to 3 GB available and 62 GB of swap while the User was
# talking to her. The references are kept as 16-byte digests now (the comparison stays exact: equal pictures, equal
# digests; a different picture with the same blake2b-128 digest is not a thing a test meets), the cache's budget is
# SWEEP_CACHE_MB (still enough for the second pass to answer from it), two workers (SWEEP_WORKERS), the mouth frames
# are opened one at a time, and each job hands its memory back before the next. Measured under safe-test's 6 GB cap:
# see docs/checkpoints/test-leak-20261012.
SWEEP_CACHE_MB = int(os.environ.get("HOUSE_TEST_SWEEP_CACHE_MB", "768"))
SWEEP_WORKERS = int(os.environ.get("HOUSE_TEST_SWEEP_WORKERS", "2"))


def _digest(px: bytes) -> bytes:
    return hashlib.blake2b(px, digest_size=16).digest()


def _loop_worker(args) -> dict:
    """One geometry, one loop: every frame through a fresh cache twice (drawn, then remembered), then every 3rd
    frame with two mouths painted in (regions over the remembered frame and over the previous picture). Each answer
    against the reference at both brightness values (the first pass) or one (alternating)."""
    try:
        return _loop_job(args)
    finally:
        gc.collect()                                # the next job starts from this worker's own small footprint


def _loop_job(args) -> dict:
    (Wg, Hg, dens), name, path, mouths = args
    os.environ["HOUSE_WALL_ASCII_CACHE_VERIFY"] = "0"
    import importlib
    importlib.reload(C)
    _fresh_state()
    g = R.Geom(Wg, Hg, density=dens)
    jpegs, boxes = _film_jpegs(str(path))
    c = _cache()
    c.cap = SWEEP_CACHE_MB << 20
    c.prepare(g)
    pals = {b: R.palette_for(b) for b in BRIGHT}
    bad, hows, n = [], {}, 0
    ref = {}
    prev = prev_pal = None
    for rep in range(2):
        for i, jp in enumerate(jpegs):
            p, how, region = c.picture(jp, g, True, 0.0, None, None, prev)
            hows[how] = hows.get(how, 0) + 1
            for b in (BRIGHT if rep == 0 else (BRIGHT[i % 2],)):
                if (i, b) not in ref:
                    ref[(i, b)] = _digest(_reference(jp, g, True, 0.0, b))
                n += 1
                if _digest(_painted(p, pals[b])) != ref[(i, b)]:
                    bad.append((name, rep, i, how, b))
            box = c.box(prev, prev_pal, p, pals[BRIGHT[i % 2]], region)
            prev, prev_pal = p, pals[BRIGHT[i % 2]]
            assert box is None or len(box) == 4
    ref.clear()
    if mouths:
        for i in range(0, len(jpegs), 3):
            if boxes[i] is None:
                continue
            frame = Image.open(io.BytesIO(jpegs[i])).convert("RGB")
            p, _, _ = c.picture(jpegs[i], g, True, 0.0, None, None, prev)
            prev = p
            for k in (1, 2):
                jp = _jpeg(_mouth(frame, boxes[i], i + k))
                p, how, region = c.picture(jp, g, True, 0.0, None, None, prev)
                hows["mouth-" + how] = hows.get("mouth-" + how, 0) + 1
                b = BRIGHT[(i + k) % 2]
                n += 1
                if _painted(p, pals[b]) != _reference(jp, g, True, 0.0, b):
                    bad.append((name, "mouth", i, how, b))
                prev = p
    return {"geom": (Wg, Hg, dens), "loop": name, "n": n, "bad": bad[:10], "nbad": len(bad), "hows": hows}


def _run_all(mouths: bool):
    loops = _core_loops()
    assert len(loops) >= 7, loops                  # Crissa's five, Lila's two (2026-10-11)
    jobs = [(geo, name, str(path), mouths) for geo in GEOMS for name, path in loops]
    with ProcessPoolExecutor(max_workers=SWEEP_WORKERS) as ex:
        return list(ex.map(_loop_worker, jobs))


@pytest.mark.slow
def test_every_core_loop_frame_and_mouth_paint_is_the_fresh_render_at_every_geometry():
    res = _run_all(mouths=True)
    for r in res:
        assert r["nbad"] == 0, r
    total = sum(r["n"] for r in res)
    hows = {}
    for r in res:
        for k, v in r["hows"].items():
            hows[k] = hows.get(k, 0) + v
    assert hows.get("bytes", 0) > 0 and hows.get("full", 0) > 0 and hows.get("mouth-region", 0) > 0, hows
    print("compared %d pictures: %s" % (total, hows))


# ---- the region path, small and fast ----------------------------------------------------------------------------
def test_a_mouth_drawn_as_a_region_is_the_whole_render():
    _fresh_state()
    c = _cache()
    W, H = 1280, 720                                # big enough that a mouth is under REGION_MAX of the glass
    g = R.Geom(W, H, density=1.3)
    assert c.prepare(g).region_ok
    pal = R.palette_for(1.0)
    base = _scene(1)
    prev = prev_pal = None
    regions = 0
    for k in range(8):
        jp = _jpeg(_mouth(base, (330, 220, 60, 22), k)) if k else _jpeg(base)
        p, how, region = c.picture(jp, g, True, 0.0, None, None, prev)
        regions += how == "region"
        assert _painted(p, pal) == _reference(jp, g, True, 0.0, 1.0), (k, how)
        box = c.box(prev, prev_pal, p, pal, region)
        if prev is not None:
            want = ImageChops.difference(Image.frombytes("RGB", (W, H), _painted(prev, prev_pal)),
                                         Image.frombytes("RGB", (W, H), _painted(p, pal))).getbbox()
            assert box == (tuple(want) if want else (0, 0, 0, 0)), (k, how)
        prev, prev_pal = p, pal
    assert regions >= 5


def test_the_region_self_check_refuses_a_reach_that_is_too_short(monkeypatch):
    """A padding that misses the glow's spread must fail the per-geometry check and turn regions off."""
    monkeypatch.setattr(C, "BLUR_REACH", 0)
    monkeypatch.setattr(C, "UP_REACH", 0)
    logs = []
    c = _cache(log=logs.append)
    g = R.Geom(W, H, density=1.3)
    assert c.prepare(g).region_ok is False
    assert any("self-check failed" in m for m in logs)


def test_a_geometrys_checks_are_spread_over_its_first_frames():
    """The film child answers each frame within REPLY_S: the checks (hundreds of ms in all at 3440x1440) run one
    step a frame, regions off until every step has passed, every picture exact meanwhile."""
    logs = []
    c = _cache(log=logs.append)
    g = R.Geom(W, H, density=1.3)
    geo = c._geo(g)
    assert geo.region_ok is False and geo.setup is not None
    steps = 0
    while geo.setup is not None:
        jp = _jpeg(_mouth(_scene(7), (330, 220, 60, 22), steps))
        p, how, _ = c.picture(jp, g, True, 0.0)
        # no region before the checks are done (the last check runs inside this call, and its frame may be one:
        # since REGION_MAX 0.16, wall-speed-101-20261011, a mouth this size on a 640x360 glass is under the ceiling)
        assert how in ("full", "bytes", "grid") or (how == "region" and geo.region_ok)
        assert _painted(p, R.palette_for(1.0)) == _reference(jp, g, True, 0.0, 1.0)
        steps += 1
    assert steps >= 8 and geo.region_ok
    assert any("regions on" in m for m in logs)


def test_regions_need_a_glass_in_whole_glow_blocks():
    c = _cache()
    g = R.Geom(1366, 768, density=1.0)              # 1366 is not a multiple of 8: no regions, still exact
    assert c._geo(g).region_ok is False and c._geo(g).setup is None
    jp = _jpeg(_scene(3))
    p, how, _ = c.picture(jp, g, True, 0.0)
    assert how == "full" and _painted(p, R.palette_for(1.2)) == _reference(jp, g, True, 0.0, 1.2)


# ---- keys -----------------------------------------------------------------------------------------------------
def test_the_same_bytes_skip_the_decode_and_another_encoding_of_the_same_grid_skips_the_draw():
    _fresh_state()
    c = _cache()
    g = R.Geom(W, H, density=1.0)
    im = _scene(5)
    a, b = _jpeg(im, 86), _jpeg(im, 85)
    p1, h1, _ = c.picture(a, g, True, 0.0)
    p2, h2, _ = c.picture(a, g, True, 0.0)
    assert (h1, h2) == ("full", "bytes") and p2 is p1
    p3, h3, _ = c.picture(b, g, True, 0.0)
    assert h3 in ("grid", "region", "full")
    assert _painted(p3, R.palette_for(1.0)) == _reference(b, g, True, 0.0, 1.0)
    if np.array_equal(R.ascii_idx(R._open(a), g, True, 0.0), R.ascii_idx(R._open(b), g, True, 0.0)):
        assert h3 == "grid" and p3 is p1


def test_every_dial_that_changes_the_picture_is_in_the_key():
    _fresh_state()
    c = _cache()
    jp = _jpeg(_scene(9))
    for dens, own, hz, shift, t in ((1.0, True, None, None, 0.0), (1.4, True, None, None, 0.0),
                                    (1.0, False, None, None, 0.0), (1.0, True, 0.5, None, 0.0),
                                    (1.0, True, None, (0.2, 0.05), 0.0), (1.0, True, 0.7, (0.1, 0.0), 0.0)):
        g = R.Geom(W, H, density=dens)
        for b in BRIGHT:
            p, _, _ = c.picture(jp, g, own, t, hz, shift)
            assert _painted(p, R.palette_for(b)) == _reference(jp, g, own, t, b, hz, shift), (dens, own, hz, shift, b)
    # no frame: the slow field moves with t, glow only when it is her own
    for own in (False, True):
        for t in (0.0, 40.0, 0.0):
            g = R.Geom(W, H, density=1.0)
            p, _, _ = c.picture(None, g, own, t)
            assert _painted(p, R.palette_for(0.8)) == _reference(None, g, own, t, 0.8), (own, t)


# ---- the box (what changed since the picture sent last) --------------------------------------------------------
def test_the_box_is_exactly_the_rgb_change_and_a_new_palette_is_everything():
    _fresh_state()
    c = _cache()
    g = R.Geom(W, H, density=1.0)
    pa, pb = R.palette_for(0.8), R.palette_for(1.3)
    seq = [_jpeg(_scene(s)) for s in (1, 1, 2, 2)] + [_jpeg(_mouth(_scene(2), (330, 220, 60, 22), 3))]
    prev = prev_pal = None
    for i, jp in enumerate(seq):
        p, _, region = c.picture(jp, g, True, 0.0, None, None, prev)
        box = c.box(prev, prev_pal, p, pa, region)
        if prev is None:
            assert box is None
        else:
            want = ImageChops.difference(Image.frombytes("RGB", (W, H), _painted(prev, prev_pal)),
                                         Image.frombytes("RGB", (W, H), _painted(p, pa))).getbbox()
            assert box == (tuple(want) if want else (0, 0, 0, 0)), i
        prev, prev_pal = p, pa
    assert c.box(prev, pa, prev, pb) is None                        # a brightness change: the whole picture
    assert c.box(prev, pa, prev, pa) == (0, 0, 0, 0)


def test_the_canon_lut_says_when_two_coverages_paint_the_same():
    for b in (0.22, 0.62, 1.0, 1.5):
        pal = R.palette_for(b)
        lut = C._canon_lut(pal)
        for i in range(256):
            for j in (0, i // 2, max(0, i - 1), i):
                same = pal[3 * i:3 * i + 3] == pal[3 * j:3 * j + 3]
                assert (lut[i] == lut[j]) == same


# ---- memory ----------------------------------------------------------------------------------------------------
def test_the_budget_is_the_smaller_of_the_cap_and_a_tenth_of_free_memory_and_gives_back_when_short():
    G = 2 ** 30
    env = {}
    assert C.budget(0, env, avail=48 * G, total=16 * G) == C.DEFAULT_MB * 2 ** 20 == 4096 * 2 ** 20   # round 12
    env = {"HOUSE_WALL_ASCII_CACHE_RAM_SHARE": "0"}                   # round 26's machine share off: round 12's cap
    assert C.budget(0, env, avail=4 * G) == int(0.4 * G)
    assert C.budget(0, {"HOUSE_WALL_ASCII_CACHE_MB": "256"}, avail=48 * G) == 256 * 2 ** 20
    # 600 MB free, 400 MB held, floor 1 GB: give back the 424 MB short (holds nothing)
    assert C.budget(400 * 2 ** 20, env, avail=600 * 2 ** 20) == 0
    assert C.budget(0, env, avail=None) == 0                         # cannot see free memory: off
    assert C.budget(0, {"HOUSE_WALL_ASCII_CACHE_MB": "128"}, avail=None) == 128 * 2 ** 20


def test_a_budget_under_a_second_of_film_is_no_cache():
    c = _cache()
    c.cap = C.MIN_PICTURES * W * H - 1
    assert c.holds(W, H) is False
    c.cap = C.MIN_PICTURES * W * H
    assert c.holds(W, H) is True


def test_a_loop_longer_than_the_budget_keeps_what_fits_instead_of_thrashing(monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_ZMEM", "0")    # the budget below is counted in raw pictures (round 17)
    _fresh_state()
    c = _cache()
    g = R.Geom(W, H, density=1.0)
    loop = [_jpeg(_scene(100 + i)) for i in range(10)]
    p, _, _ = c.picture(loop[0], g, True, 0.0)
    c.cap = 6 * p.nbytes + 4 * 60000                                # room for 6 of the 10
    c.pics.clear(), c.by_idx.clear(), c.by_band.clear(), c.by_jpeg.clear()
    c.bytes = c.jpeg_bytes = 0
    for rep in range(4):
        hows = [c.picture(jp, g, True, 0.0)[1] for jp in loop]
    assert hows.count("bytes") >= 5, hows                           # LRU alone would evict each just before use
    assert c.n["refused"] > 0


# ---- switches ----------------------------------------------------------------------------------------------------
def test_the_mode_is_the_env_word_the_file_wins_and_off_is_a_kill(tmp_path, monkeypatch):
    sw = tmp_path / "wall-ascii-cache"
    monkeypatch.setattr(C, "SWITCH_FILE", sw)
    assert C.mode({}) == C.DEFAULT_MODE
    assert C.mode({"HOUSE_WALL_ASCII_CACHE": "fallback"}) == "fallback"
    sw.write_text("")
    assert C.mode({"HOUSE_WALL_ASCII_CACHE": "fallback"}) == "on"     # present = on
    sw.write_text("fallback\n")
    assert C.mode({}) == "fallback"
    sw.write_text("off")
    assert C.mode({}) == "off"
    sw.write_text("on")
    assert C.mode({"HOUSE_WALL_ASCII_CACHE": "off"}) == "off"         # the env kill beats the file
    assert C.mode({"HOUSE_WALL_ASCII_CACHE": "0"}) == "off"


def test_a_spot_check_that_differs_turns_the_cache_off(monkeypatch):
    monkeypatch.setattr(C, "VERIFY_EVERY", 1)
    logs = []
    c = _cache(log=logs.append)
    g = R.Geom(W, H, density=1.0)
    jp = _jpeg(_scene(4))
    p, _, _ = c.picture(jp, g, True, 0.0)
    p.px = bytes(len(p.px))                                         # a remembered picture gone wrong
    q, how, _ = c.picture(jp, g, True, 0.0)
    assert how == "bytes" and c.dead and not c.usable()
    assert _painted(q, R.palette_for(1.0)) == _reference(jp, g, True, 0.0, 1.0)   # this frame is still right
    assert any("MISMATCH" in m for m in logs)


# ---- the film process ----------------------------------------------------------------------------------------------
def test_the_film_process_paints_the_fresh_render_from_the_cache_and_patches_rebuild_it():
    _fresh_state()
    base = _scene(21)
    seq = [_jpeg(base), _jpeg(_mouth(base, (330, 220, 60, 22), 1)), _jpeg(_mouth(base, (330, 220, 60, 22), 2)),
           _jpeg(base), _jpeg(_scene(22)), _jpeg(base), _jpeg(base)]
    fp = F.FilmProcess(W, H, log=lambda m: None)
    glass = None
    hows = []
    try:
        for i, jp in enumerate(seq):
            b = BRIGHT[i % 2] if i < 5 else 1.0
            got = fp.render_patch(jp, 1.2, True, 0.0, brightness=b, patch_max=0.4, cache=True)
            assert got is not None
            hows.append(fp.how)
            im, xy = got
            if xy is None and im is not None:
                glass = im.copy()
            elif im is not None:
                glass.paste(im, xy)
            assert glass.tobytes() == _reference(jp, R.Geom(W, H, density=1.2), True, 0.0, b), (i, fp.how)
        assert fp.cache_stats and fp.cache_stats["pictures"] >= 2
        assert "bytes" in hows and fp.how == "bytes"
        # off again: today's path, the whole picture first
        R._GLOW.update(n=0, im=None, fp=None, out=None)
        im, xy = fp.render_patch(seq[4], 1.2, True, 0.0, brightness=1.0, patch_max=0.4, cache=False)
        assert xy is None and fp.how == "today"
        assert im.tobytes() == R.render_background(R._open(seq[4]), R.Geom(W, H, density=1.2), True, 0.0,
                                                   brightness=1.0).tobytes()
    finally:
        fp.close()


def test_the_fresh_render_is_render_background_without_the_glow_memo():
    _fresh_state()
    g = R.Geom(W, H, density=1.0)
    for s in range(4):
        jp = _jpeg(_scene(s))
        for b in BRIGHT:
            assert R.background_fresh(R._open(jp), g, True, 0.0, brightness=b).tobytes() == _reference(jp, g, True, 0.0, b)


def test_the_wall_asks_the_child_for_the_cache_and_logs_it_on_its_own_line():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    worker = src.split("def render_worker", 1)[1].split("threading.Thread(target=render_worker", 1)[0]
    assert 'cache=own and (ac_mode == "on" or (ac_mode == "fallback" and ac["engaged"])),' in worker
    assert 'log(f"ascii cache: {ac[\'mode\']}' in worker
    # the speed line another reader parses is unchanged
    assert ("f\" in {speed['every']:.0f}s{' PATCH' if patching else ''}\")" in worker)
    assert "behind = not REST[\"on\"] and not stale and made < 0.95 * speed[\"every\"] / present_period" in worker



def test_a_fault_in_the_cache_turns_it_off_and_the_film_goes_on(tmp_path, monkeypatch):
    """The child must never stop drawing because the cache broke: that frame and every one after is today's."""
    child = tmp_path / "planted_child.py"
    child.write_text("import sys\n"
                     "sys.path.insert(0, %r)\n"
                     "import wall_ascii_cache\n"
                     "def boom(self, *a, **k):\n"
                     "    raise RuntimeError('planted')\n"
                     "wall_ascii_cache.AsciiCache.picture = boom\n"
                     "import wall_film_proc\n"
                     "sys.exit(wall_film_proc._child(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]))\n"
                     % str(ROOT / "lib"))
    monkeypatch.setattr(F, "__file__", str(child))
    fp = F.FilmProcess(W, H, log=lambda m: None)
    try:
        jp = _jpeg(_scene(31))
        g = R.Geom(W, H, density=1.0)
        for i in range(2):
            got = fp.render_patch(jp, 1.0, True, 0.0, brightness=1.0, patch_max=0.0, cache=True)
            assert got is not None and got[1] is None and fp.how == "today", i
            R._GLOW.update(n=0, im=None, fp=None, out=None)
            if i == 0:                                   # the child's glow memo was fresh: the first is the reference
                assert got[0].tobytes() == R.render_background(R._open(jp), g, True, 0.0, brightness=1.0).tobytes()
        assert fp.cache_stats and "planted" in fp.cache_stats["dead"]
    finally:
        fp.close()


def test_a_region_mismatch_names_its_base(monkeypatch):
    """Round 40 N16 (.101 round 38: "a region answer differed from the fresh render at 3440x1440", cause not found):
    the MISMATCH line names the base the region was laid over, so the next one says where it came from."""
    monkeypatch.setattr(C, "VERIFY_EVERY", 1)
    logs = []
    _fresh_state()
    c = _cache(log=logs.append)
    g = R.Geom(1280, 720, density=1.3)                              # a mouth is under REGION_MAX of this glass
    assert c.prepare(g).region_ok
    base = _scene(1)
    p, _, _ = c.picture(_jpeg(base), g, True, 0.0)
    p.px = bytes(len(p.px))                                         # the base gone wrong
    q, how, _ = c.picture(_jpeg(_mouth(base, (330, 220, 60, 22), 3)), g, True, 0.0, None, None, p)
    assert how == "region" and c.dead
    line = next(m for m in logs if "MISMATCH" in m)
    assert "previous base" in line and "serial %s" % p.serial in line
