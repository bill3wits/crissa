"""Her whole film on both glasses (the User 2026-10-12): lib/wall_frame.py, the render's whole branch, the cache key,
the shared zoom, the conductor's report, and the page's copy of the same geometry."""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import wall_frame as WF  # noqa: E402

from _kit import kit  # noqa: E402  the kit's copy (2026-10-24: a release clone has no crissa/overlay; GitHub ran this)
PAGES = [ROOT / "display" / "voiceonly-home.html", kit("display/voiceonly-home.html")]
CASES = [
    # (iw, ih, cols, rows, zoom, face, cell_w, cell_h)
    (736, 400, 333, 115, 1.0, (0.5268, 0.4796), 0.6, 0.98),     # 2560x1440 at density 1
    (736, 400, 601, 207, 1.0, (0.5128, 0.6098), 0.6, 0.98),     # 2560x1440 at the house's 1.803
    (736, 400, 334, 86, 1.0, (0.5268, 0.4796), 0.6, 0.98),      # 3440x1440 at density 1
    (736, 400, 694, 178, 1.75, (0.51, 0.53), 0.6, 0.98),        # .101's grid, zoomed
    (736, 400, 333, 115, 1.5, None, 0.6, 0.98),                 # no head track: the kiosk's point
    (736, 400, 333, 115, 3.0, (0.9, 0.1), 0.6, 0.98),           # a face at the corner
    (736, 400, 601, 207, 2.25, (0.5268, 0.4796), 2560 / 601, 1440 / 207),   # the Tk wall's own cell
    (1296, 704, 400, 140, 1.25, (0.3, 0.7), 0.6, 0.98),
]


def _page_block(page: Path) -> str:
    s = page.read_text(encoding="utf-8")
    a = s.index("function wfRnd(x)")
    b = s.index("var WF_BLEND={key:")
    head = s[s.index("var WF_ZOOM_MIN="):a]
    return head + s[a:b]


def test_both_pages_carry_the_same_frame_code():
    blocks = [_page_block(p) for p in PAGES]
    assert blocks[0] == blocks[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="no node")
@pytest.mark.parametrize("page", PAGES, ids=["house", "kit"])
def test_the_page_lays_her_out_to_the_same_numbers(page, tmp_path):
    js = _page_block(page) + "\nvar out=[];\n"
    for c in CASES:
        js += "out.push(wallFrameLayout(%s));\n" % ",".join(json.dumps(v) for v in
                                                            (c[0], c[1], c[2], c[3], c[4], list(c[5]) if c[5] else None,
                                                             c[6], c[7]))
    js += "console.log(JSON.stringify(out));\n"
    f = tmp_path / "wf.js"
    f.write_text(js)
    got = json.loads(subprocess.run(["node", str(f)], capture_output=True, text=True, check=True).stdout)
    for c, j in zip(CASES, got):
        p = WF.layout(*c[:5], c[5], c[6], c[7])
        assert list(p["place"]) == j["place"], c
        assert list(p["meter"]) == j["meter"], c
        assert list(p["blend"]) == j["blend"], c
        for k in ("crop", "face", "focus"):
            assert all(math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9) for a, b in zip(p[k], j[k])), (k, c)
        assert math.isclose(p["zoom"], j["zoom"]) and math.isclose(p["scale"], j["scale"])


@pytest.mark.parametrize("c", CASES)
def test_whole_never_stretches_her(c):
    """film pixels per glass unit are the same across and down (to the cell's rounding)"""
    L = WF.layout(*c[:5], c[5], c[6], c[7])
    sx0, sy0, sx1, sy1 = L["crop"]
    dx, dy, dw, dh = L["place"]
    kx = dw * c[6] / (sx1 - sx0)
    ky = dh * c[7] / (sy1 - sy0)
    assert abs(kx / ky - 1.0) < 0.04, (kx, ky)


def test_the_kiosk_frame_stretches_a_wide_glass_and_whole_does_not():
    # the kiosk crop (1.77:1) fills the grid: on 3440x1440 (2.39:1) that is 35 % wide; whole is 1:1
    crop_aspect = (0.675 * 736) / (0.700 * 400)
    glass = 3440 / 1440
    assert glass / crop_aspect == pytest.approx(1.346, abs=0.01)


def test_whole_at_zoom_one_shows_the_whole_film_right_anchored():
    L = WF.layout(736, 400, 334, 86, 1.0, (0.5, 0.5))          # 21:9: spare width on the left
    assert L["crop"] == (0.0, 0.0, 736.0, 400.0)
    c0, r0, dw, dh = L["place"]
    assert c0 > 0 and c0 + dw == 334 and r0 == 0 and dh == 86
    assert L["blend"] == (True, False, False)
    L = WF.layout(736, 400, 333, 115, 1.0, (0.5, 0.5))          # 16:9: a little spare height, split
    c0, r0, dw, dh = L["place"]
    assert c0 == 0 and dw == 333 and r0 > 0 and r0 + dh < 115
    assert L["blend"] == (False, True, True)


def test_zoom_glides_her_face_to_the_kiosk_spot_and_never_opens_a_right_gap():
    f = (0.5268, 0.4796)
    xs = []
    for z in (1.0, 1.25, 1.5, 2.0, 3.0):
        L = WF.layout(736, 400, 333, 115, z, f)
        xs.append(L["face"][0] / 333)
        c0, r0, dw, dh = L["place"]
        assert c0 + dw == 333
    assert xs == sorted(xs) and xs[-1] == pytest.approx(0.74, abs=0.01)


def test_clamp_zoom():
    assert WF.clamp_zoom(0.2) == 1.0 and WF.clamp_zoom(9) == 3.0
    assert WF.clamp_zoom(1.6) == 1.5 and WF.clamp_zoom("x") == 1.0 and WF.clamp_zoom(float("nan")) == 1.0
    assert WF.clamp_zoom(1.125) == 1.25           # floor(x + .5), as the page rounds


def test_mode_kill_switch_file_and_default(tmp_path, monkeypatch):
    sw = tmp_path / "wall-frame"
    monkeypatch.setattr(WF, "SWITCH_FILE", sw)
    monkeypatch.setitem(WF._SW, "at", 0.0)
    monkeypatch.setattr(WF, "_house", lambda: True)
    assert WF.mode({}) == "kiosk"                                # the house's default
    monkeypatch.setattr(WF, "_house", lambda: False)
    WF._SW["at"] = 0.0
    assert WF.mode({}) == "whole"                                # a pack's default
    sw.write_text("kiosk\n")
    WF._SW["at"] = 0.0
    assert WF.mode({}) == "kiosk"                                # the switch file, read live
    monkeypatch.setattr(WF, "_house", lambda: True)
    sw.write_text("whole")
    WF._SW.update(at=0.0, mtime=None)
    assert WF.mode({}) == "whole"                                # the house's one-line flip
    assert WF.mode({"HOUSE_WALL_FRAME": "kiosk"}) == "kiosk"     # the kill beats the file


def test_frame_for_keeps_a_page_slide_on_the_kiosk_frame_and_ar_on_the_whole(monkeypatch):
    monkeypatch.setattr(WF, "mode", lambda env=None: "whole")
    assert WF.frame_for(True, 1.5, (0.5, 0.4)) == ("whole", 1.5, 0.5, 0.4)
    assert WF.frame_for(True, 1.5, None) == ("whole", 1.5) + WF.KIOSK_FACE
    # round 14: the AR hole no longer sends the wallpaper back to the kiosk frame
    assert WF.frame_for(True, 1.5, (0.5, 0.4), hole_zoom=0.5) == ("whole", 1.5, 0.5, 0.4)
    assert WF.frame_for(True, 1.5, (0.5, 0.4), shift=(0.2, 0.0)) is None
    assert WF.frame_for(False, 1.5, (0.5, 0.4)) is None
    monkeypatch.setattr(WF, "mode", lambda env=None: "kiosk")
    assert WF.frame_for(True, 1.5, (0.5, 0.4)) is None


def test_face_point_is_the_median_nose(tmp_path):
    frames = [{"nose": [368.0, 160.0 + i], "src_wh": [736, 400]} for i in range(5)] + [None, {"nose": None}]
    assert WF.face_point(frames) == (0.5, 0.405)
    assert WF.face_point(frames[:2]) is None
    p = tmp_path / "x.head.json"
    p.write_text(json.dumps({"src_wh": [736, 400], "frames": [{"nose": [184, 100]}] * 3}))
    assert WF.face_point_file(p) == (0.25, 0.25)
    assert WF.face_point_file(tmp_path / "missing.head.json") is None


def test_blend_weights_thin_the_spare_sides_only():
    np = pytest.importorskip("numpy")
    w = WF.blend_weights(100, 40, (20, 0, 80, 40), (True, False, False))
    assert w.shape == (40, 100)
    assert float(w[:, :20].max()) == 0.0 and float(w[:, 30:].min()) == 1.0
    assert 0.0 < float(w[0, 22]) < 1.0
    assert np.all(np.diff(w[0, 18:30]) >= 0)
    w = WF.blend_weights(100, 40, (0, 0, 100, 40), (False, False, False))
    assert float(w.min()) == 1.0


def _src():
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (736, 400), (40, 40, 40))
    d = ImageDraw.Draw(im)
    d.ellipse((300, 120, 460, 330), fill=(220, 190, 170))
    d.rectangle((350, 260, 420, 280), fill=(90, 40, 40))
    return im


def test_the_kiosk_path_is_untouched_by_the_argument():
    np = pytest.importorskip("numpy")
    import house_wall_ascii_render as R
    g = R.Geom(640, 360, 1.0)
    a = R.ascii_idx(_src(), g, True, 0.0)
    b = R.ascii_idx(_src(), g, True, 0.0, frame=None)
    assert np.array_equal(a, b)


def test_whole_draws_her_whole_face_and_blends_the_left():
    np = pytest.importorskip("numpy")
    import house_wall_ascii_render as R
    g = R.Geom(860, 360, 1.0)                 # 2.39:1, a 21:9 glass
    fr = ("whole", 1.0, 0.516, 0.56)
    idx = R.ascii_idx(_src(), g, True, 0.0, frame=fr)
    kiosk = R.ascii_idx(_src(), g, True, 0.0)
    assert idx.shape == kiosk.shape and not np.array_equal(idx, kiosk)
    L = WF.layout(736, 400, g.COLS, g.ROWS, 1.0, (0.516, 0.56), *R.cell_glass(g))
    c0 = L["place"][0]
    assert c0 > 0 and int(idx[:, :c0].max()) == 0               # the spare left is ground
    # her lips (the dark bar in her face) land inside the grid, not under the bottom edge
    rows = np.where(idx[:, c0:].max(axis=1) > 0)[0]
    assert rows.max() < g.ROWS - 1


def test_a_new_frame_is_a_new_cache_key():
    pytest.importorskip("numpy")
    import io
    import house_wall_ascii_render as R
    import wall_ascii_cache as AC
    buf = io.BytesIO()
    _src().save(buf, "JPEG", quality=90)
    jpeg = buf.getvalue()
    g = R.Geom(640, 360, 1.0)
    c = AC.AsciiCache(env={"HOUSE_WALL_ASCII_CACHE_MB": "256"})
    p1, how1, _ = c.picture(jpeg, g, True, 0.0)
    p2, how2, _ = c.picture(jpeg, g, True, 0.0)
    assert how2 == "bytes" and p2.serial == p1.serial
    p3, how3, _ = c.picture(jpeg, g, True, 0.0, frame=("whole", 1.0, 0.516, 0.56))
    assert how3 != "bytes" and p3.px != p1.px
    p4, how4, _ = c.picture(jpeg, g, True, 0.0, frame=("whole", 1.5, 0.516, 0.56))
    assert how4 != "bytes" and p4.px != p3.px
    assert p3.px == R.coverage_fresh(R._open(jpeg), g, True, 0.0, frame=("whole", 1.0, 0.516, 0.56)).tobytes()


def test_the_film_child_is_asked_with_the_frame_only_when_set():
    src = (ROOT / "lib" / "wall_film_proc.py").read_text()
    assert 'if frame is not None:\n                ask["frame"] = tuple(frame)' in src
    assert src.count('frame=req.get("frame")') == 3


def test_frame_zoom_is_a_shared_pref_and_clamps():
    import glass_prefs
    import house_wall_prefs as P
    assert "frame_zoom" in P.SHARED_KEYS and "frame_zoom" in glass_prefs.SHARED_KEYS
    assert P.DEFAULTS["frame_zoom"] == 1.0
    assert P._clip({"frame_zoom": 7})["frame_zoom"] == 3.0
    assert glass_prefs.coerce("frame_zoom", "1.6") == 1.5 and glass_prefs.coerce("frame_zoom", "x") is None


def test_the_conductor_reports_the_frame_and_the_face_point():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert '"wall_frame": wall_frame_state(lib, ph.key),' in src
    assert "def wall_frame_state(lib, key) -> dict:" in src


def test_the_desk_wall_routes_frame_and_zoom():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert "shift_f, frame_f = _her_frame(house_f, own, hz, pref," in src
    assert src.count("shift=shift_f, frame=frame_f,") == 2
    assert '_glass_post({"frame_zoom": z})' in src
    assert 'st.wall_frame = wf if isinstance(wf, dict) else {}' in src
