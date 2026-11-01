"""The glow's upsample by OpenCV (the User 2026-10-13, "yes go ahead with the opencv glow").

house_wall_ascii_render.glow_up: OpenCV INTER_LINEAR_EXACT where PIL BILINEAR was. A new look, so these pin how new:
at most 1 grey level off PIL's picture, on synthetic glows and on recorded frames of the library; the kill switch
(HOUSE_WALL_GLOW_CV=0) is PIL's bytes exactly; without cv2 it is PIL's, said once; a crop's upsample equals the whole
picture's (what the cache's region drawing stands on); and the disk tier's code print names which one drew a picture.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageChops, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import house_wall_ascii_render as R  # noqa: E402

POSES = ROOT / "display" / "crissa-poses"


@pytest.fixture
def cv_on(monkeypatch):
    monkeypatch.setattr(R, "GLOW_CV", True)
    assert R.glow_method().startswith("cv2 "), "this env has no OpenCV"


def _pil_up(lit: Image.Image, size) -> Image.Image:
    """The upsample as it was before 2026-10-13 (glow_from_small's last step)."""
    return lit.resize(size, Image.Resampling.BILINEAR)


def _old_coverage(src, g, frame=None) -> bytes:
    """coverage_fresh as it was before 2026-10-13, written out: the glyphs, the glow by PIL, the vignette."""
    im = R.mask_from_idx(R.ascii_idx(src, g, True, 0.0, frame=frame), g)
    small = R.glow_small(im, g)
    glow = small.filter(ImageFilter.GaussianBlur(R.GLOW_PX / 2.0 / R.GLOW_Q)).point(R._glow_lut())
    im = ImageChops.screen(im, glow.resize((g.W, g.H), Image.Resampling.BILINEAR))
    return ImageChops.multiply(im, R._vignette_inv(g.W, g.H)).tobytes()


def _diff(a: Image.Image, b: Image.Image) -> np.ndarray:
    assert a.size == b.size and a.mode == b.mode == "L"
    return np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16))


def _lit(seed: int, w: int, h: int) -> Image.Image:
    """A glow at 1/8 of a glass: blurred noise through the glow's LUT, plus hard 0/255 steps for the rounding."""
    rng = np.random.default_rng(seed)
    a = Image.fromarray(rng.integers(0, 256, (h, w), dtype=np.uint8), "L")
    a = a.filter(ImageFilter.GaussianBlur(R.GLOW_PX / 2.0 / R.GLOW_Q)).point(R._glow_lut())
    arr = np.array(a)
    arr[: h // 4, : w // 4] = 0
    arr[h // 2:, w // 2: w // 2 + 3] = 255
    return Image.fromarray(arr, "L")


# ---- how far from PIL ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("W,H", [(3440, 1440), (2560, 1600), (2560, 1440), (1920, 1080), (1366, 768), (640, 360)])
def test_the_upsample_is_within_one_grey_level_of_pils(cv_on, W, H):
    for seed in range(3):
        lit = _lit(seed, max(1, W // R.GLOW_Q), max(1, H // R.GLOW_Q))
        d = _diff(R.glow_up(lit, (W, H)), _pil_up(lit, (W, H)))
        assert d.max() <= 1, (W, H, seed, int(d.max()))
        assert d.mean() < 0.5


def _film_frames(stem: str, picks=(0, 0.5, 0.95)) -> list[bytes]:
    """A few frames of a film as the player serves them (736x400 INTER_AREA, JPEG q86)."""
    cv2 = pytest.importorskip("cv2")
    path = POSES / (stem + ".mp4")
    if not path.is_file():
        pytest.skip("no film %s on disk" % path.name)
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    for f in picks:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(f * max(0, n - 1)))
        ok, fr = cap.read()
        if ok:
            fr = cv2.resize(fr, (736, 400), interpolation=cv2.INTER_AREA)
            out.append(cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 86])[1].tobytes())
    cap.release()
    assert out
    return out


@pytest.mark.parametrize("W,H,density", [(3440, 1440, 2.0), (2560, 1600, 5.0), (2560, 1440, 1.0)])
def test_recorded_frames_are_within_one_grey_level_of_pils(cv_on, monkeypatch, W, H, density):
    """Her listen loop and her work loop, the whole glass: the glow layer and the coverage the Wall paints."""
    g = R.Geom(W, H, density)
    for stem in ("clip-listen-back", "clip-work"):
        for jp in _film_frames(stem, picks=(0.1, 0.6)):
            src = R._open(jp)
            mask = R.mask_from_idx(R.ascii_idx(src, g, True, 0.0), g)
            lit = R.glow_small(mask, g).filter(ImageFilter.GaussianBlur(R.GLOW_PX / 2.0 / R.GLOW_Q)).point(R._glow_lut())
            dg = _diff(R.glow_up(lit, (W, H)), _pil_up(lit, (W, H)))
            new = Image.frombytes("L", (W, H), R.coverage_fresh(src, g, True, 0.0).tobytes())
            old = Image.frombytes("L", (W, H), _old_coverage(src, g))
            dc = _diff(new, old)
            assert dg.max() <= 1 and dc.max() <= 1, (stem, W, H, density, int(dg.max()), int(dc.max()))
            assert dg.any(), "not a new look at all: is OpenCV drawing it?"


# ---- the kill switch and the fallback --------------------------------------------------------------------------------
def test_the_kill_switch_is_pils_bytes_exactly(monkeypatch):
    monkeypatch.setattr(R, "GLOW_CV", False)
    assert R.glow_method() == "pil"
    lit = _lit(5, 430, 180)
    assert R.glow_up(lit, (3440, 1440)).tobytes() == _pil_up(lit, (3440, 1440)).tobytes()
    g = R.Geom(2560, 1440, 2.0)
    for jp in _film_frames("clip-work", picks=(0.3,)):
        src = R._open(jp)
        assert R.coverage_fresh(src, g, True, 0.0).tobytes() == _old_coverage(src, g)
        R._GLOW.update(n=0, im=None, fp=None, out=None)
        assert R.render_coverage(src, g, True, 0.0).tobytes() == _old_coverage(src, g)


def _import_with(env_extra: dict, drop=("HOUSE_WALL_GLOW_CV",)) -> str:
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(env_extra)
    code = ("import sys; sys.path.insert(0, %r); import house_wall_ascii_render as R; print(R.GLOW_CV, R.glow_method())"
            % str(ROOT / "lib"))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    return r.stdout.strip().splitlines()[-1]


def test_on_by_default_and_off_by_the_switch(tmp_path):
    home = {"CRISSA_HOME": str(tmp_path)}
    assert _import_with(home).startswith("True cv2 ")
    for off in ("0", "off", "false"):
        assert _import_with(dict(home, HOUSE_WALL_GLOW_CV=off)) == "False pil"
    assert _import_with(dict(home, HOUSE_WALL_GLOW_CV="1")).startswith("True cv2 ")


def test_without_cv2_the_glow_is_pils_and_says_so_once(monkeypatch, capsys):
    monkeypatch.setattr(R, "GLOW_CV", True)
    monkeypatch.setitem(R._CV, "tried", False)
    monkeypatch.setitem(R._CV, "cv2", None)
    monkeypatch.setitem(sys.modules, "cv2", None)          # `import cv2` raises ImportError
    lit = _lit(7, 320, 180)
    for _ in range(3):
        assert R.glow_up(lit, (2560, 1440)).tobytes() == _pil_up(lit, (2560, 1440)).tobytes()
    assert R.glow_method() == "pil"
    err = capsys.readouterr().err
    assert err.count("no OpenCV") == 1, err


def test_the_first_use_runs_opencv_on_one_thread(cv_on):
    import cv2
    R.glow_up(_lit(1, 40, 20), (320, 160))
    assert cv2.getNumThreads() == 1


# ---- what the caches stand on ----------------------------------------------------------------------------------------
def test_a_crops_upsample_is_the_whole_pictures(cv_on):
    """The cache draws a mouth step as a region: the glow upsampled from a crop of the 1/8 picture, UP_REACH small
    pixels wider than what it keeps. That is exact only if the upsample is position-independent (INTER_LINEAR_EXACT is
    bit-exact by OpenCV's contract; measured here on random crops, both edges and the middle)."""
    import wall_ascii_cache as C
    q, reach = R.GLOW_Q, C.UP_REACH
    lit = _lit(11, 430, 180)
    SW, SH = lit.size
    whole = np.asarray(R.glow_up(lit, (SW * q, SH * q)))
    rng = np.random.default_rng(3)
    boxes = [(0, 0, 9, 7), (SW - 12, SH - 9, SW, SH), (0, SH - 6, 14, SH), (SW - 5, 0, SW, 5)]
    for _ in range(60):
        x0, y0 = int(rng.integers(0, SW - 2)), int(rng.integers(0, SH - 2))
        boxes.append((x0, y0, int(rng.integers(x0 + 1, min(SW, x0 + 40) + 1)), int(rng.integers(y0 + 1, min(SH, y0 + 30) + 1))))
    for (os0, ot0, os1, ot1) in boxes:
        gx0, gx1 = max(0, os0 - reach), min(SW, os1 + reach)
        gy0, gy1 = max(0, ot0 - reach), min(SH, ot1 + reach)
        up = np.asarray(R.glow_up(lit.crop((gx0, gy0, gx1, gy1)), ((gx1 - gx0) * q, (gy1 - gy0) * q)))
        got = up[(ot0 - gy0) * q:(ot1 - gy0) * q, (os0 - gx0) * q:(os1 - gx0) * q]
        assert np.array_equal(got, whole[ot0 * q:ot1 * q, os0 * q:os1 * q]), (os0, ot0, os1, ot1)


_CACHE_SRC = (ROOT / "lib" / "wall_ascii_cache.py").read_text(encoding="utf-8")


@pytest.mark.xfail("R.glow_up(" not in _CACHE_SRC, strict=True,
                   reason="wall_ascii_cache._draw_region still upsamples a region's glow with PIL; its one line to "
                          "R.glow_up lands with that file's owner (docs/checkpoints/wall-glow-cv-20261013)")
def test_the_caches_regions_draw_the_same_glow(cv_on):
    """A region drawn with another upsample than the whole picture's fails the cache's own per-geometry self-check
    and regions go off (safe, but every mouth step is then a whole picture)."""
    import wall_ascii_cache as C
    logs = []
    c = C.AsciiCache(log=logs.append)
    assert c.prepare(R.Geom(640, 360, density=1.3)).region_ok, logs


def test_the_disk_print_names_the_glow(monkeypatch):
    import wall_ascii_disk as D
    prints = {}
    for on in (True, False):
        monkeypatch.setattr(R, "GLOW_CV", on)
        monkeypatch.setattr(D, "_PRINT", {})
        prints[on] = D.code_print()
    assert prints[True] != prints[False]
