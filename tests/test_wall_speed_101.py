"""The desk Wall on a slower box (wall-speed-101-20261011; the User 2026-10-11 via the .101 tester: "let's try to get
these in from that side and pull again").

1. The lean reply: the film child sends today's picture as its coverage (L + palette) and skips the diff unless the
   wall patches. Byte-identical to render_background; an older parent still gets today's RGB reply.
2. Her mouth does not set the exposure: with the conductor's mouth box (X-Mouth-Box) the focus box's percentiles
   leave her mouth out, so a mouth step changes only the cells around it, and the ASCII cache stays exact.
3. The plumbing: the conductor names the box, the wall reads it and hands it to the film, the switches.
"""
from __future__ import annotations

import ast
import io
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import house_wall_ascii_render as R  # noqa: E402
import wall_ascii_cache as C  # noqa: E402
import wall_film_proc as F  # noqa: E402

W, H = 640, 360
MOUTH = (350.0, 150.0, 40.0, 15.0)        # inside the focus box (frame x 288-447, y 67-190), like Lila's loops


def _fresh():
    for v in vars(R).values():
        if callable(getattr(v, "cache_clear", None)):
            v.cache_clear()
    R._GLOW.update(n=0, im=None, fp=None, out=None)


def _jpeg(im: Image.Image, q: int = 86) -> bytes:
    b = io.BytesIO()
    im.save(b, "JPEG", quality=q)
    return b.getvalue()


def _face(seed: int = 5) -> Image.Image:
    """A frame with a lit face over the focus box and a darker room around it."""
    rng = np.random.default_rng(seed)
    a = (rng.random((50, 92, 3)) * 80 + 20).astype(np.uint8)
    im = Image.fromarray(a, "RGB").resize((736, 400), Image.Resampling.BILINEAR)
    d = ImageDraw.Draw(im)
    d.ellipse((290, 40, 450, 240), fill=(205, 172, 150))
    tex = (rng.random((40, 32)) * 40).astype(np.uint8)
    im.paste(Image.fromarray(tex, "L").resize((160, 200), Image.Resampling.BILINEAR).convert("RGB"), (290, 40),
             Image.new("L", (160, 200), 60))
    return im


def _mouth(im: Image.Image, k: int, box=MOUTH) -> Image.Image:
    """A painted mouth: dark inside, opening with k, over the box (the paint stays inside its jaw extent)."""
    out = im.copy()
    x, y, w, h = box
    d = ImageDraw.Draw(out)
    o = 2 + (k * 5) % int(h)
    d.ellipse((x + 2, y + h / 2 - o, x + w - 2, y + h / 2 + o), fill=(15 + 10 * (k % 3), 8, 12))
    return out


def _bbox_share(a, b) -> float:
    d = a != b
    if not d.any():
        return 0.0
    rows, cols = np.flatnonzero(d.any(1)), np.flatnonzero(d.any(0))
    return (rows[-1] - rows[0] + 1) * (cols[-1] - cols[0] + 1) / d.size


# ---- 1. the lean reply ------------------------------------------------------------------------------------------
def test_the_lean_reply_is_render_background_byte_for_byte_glow_memo_and_all():
    _fresh()
    frames = [_jpeg(_mouth(_face(), k)) for k in range(5)]
    t = time.time()
    g = R.Geom(W, H, density=1.3)
    want = [R.render_background(R._open(jp), g, True, t, brightness=b).tobytes()
            for jp, b in zip(frames, (1.0, 1.0, 1.3, 1.3, 0.8))]
    fp = F.FilmProcess(W, H)
    try:
        assert fp.lean is True
        for jp, b, w in zip(frames, (1.0, 1.0, 1.3, 1.3, 0.8), want):
            got = fp.render(jp, 1.3, True, t, brightness=b)
            assert got is not None and got.mode == "RGB" and got.tobytes() == w
    finally:
        fp.close()


def test_a_lean_patch_rebuilds_the_whole_picture_and_a_new_palette_is_whole():
    _fresh()
    shut, opened = _jpeg(_face()), _jpeg(_mouth(_face(), 3))
    t = time.time()
    g = R.Geom(W, H, density=1.3)
    whole_a = R.render_background(R._open(shut), g, True, t, brightness=1.0)
    whole_b = R.render_background(R._open(opened), g, True, t, brightness=1.0)
    whole_c = R.render_background(R._open(shut), g, True, t, brightness=1.3)
    fp = F.FilmProcess(W, H)
    try:
        a, xy = fp.render_patch(shut, 1.3, True, t, brightness=1.0, patch_max=0.4)
        assert xy is None and a.tobytes() == whole_a.tobytes()
        got = fp.render_patch(opened, 1.3, True, t, brightness=1.0, patch_max=1.0)
        rebuilt = a.copy()
        if got[1] is not None:
            rebuilt.paste(got[0], got[1])
        else:
            rebuilt = got[0]
        assert rebuilt.tobytes() == whole_b.tobytes()
        c, xy_c = fp.render_patch(shut, 1.3, True, t, brightness=1.3, patch_max=1.0)
        assert xy_c is None and c.tobytes() == whole_c.tobytes()          # the palette moved: the whole picture
    finally:
        fp.close()


def _raw_request(fp, ask: dict) -> dict:
    """One request on the child's pipe as an older (or newer) parent would send it; the reply's meta."""
    import pickle
    req = pickle.dumps(ask)
    assert F._write_all(fp.p.stdin.fileno(), F._HDR.pack(len(req)) + req, time.monotonic() + 30)
    hdr = F._read_exact(fp.p.stdout.fileno(), F._HDR.size, time.monotonic() + 30)
    return pickle.loads(bytes(F._read_exact(fp.p.stdout.fileno(), F._HDR.unpack(hdr)[0], time.monotonic() + 30)))


def test_the_child_skips_the_diff_unless_asked_and_an_older_parent_still_gets_rgb():
    _fresh()
    jp, jp2 = _jpeg(_face()), _jpeg(_mouth(_face(), 2))
    base = {"density": 1.0, "own": True, "t": 0.0, "hole_zoom": None, "brightness": 1.0, "shift": None,
            "cache": False}
    fp = F.FilmProcess(W, H)
    try:
        r1 = _raw_request(fp, dict(base, jpeg=jp, lean=True, box=False))
        r2 = _raw_request(fp, dict(base, jpeg=jp2, lean=True, box=False))
        assert r1["mode"] == "L" and r2["mode"] == "L" and r2["box"] is None      # no box asked, none made
        r3 = _raw_request(fp, dict(base, jpeg=jp, lean=True, box=True))
        assert r3["box"] is not None                                               # asked: the box
        old1 = _raw_request(fp, dict(base, jpeg=jp))                               # a parent from before 10-11
        old2 = _raw_request(fp, dict(base, jpeg=jp2))
        assert old1.get("mode") in (None, "RGB") and old2.get("mode") in (None, "RGB")
        assert old2["box"] is not None                                             # ... and its diff, as ever
        n = old2["size"][0] * old2["size"][1] * 3
        R._GLOW.update(n=0, im=None, fp=None, out=None)
        R.render_background(R._open(jp), R.Geom(W, H), True, 0.0, brightness=1.0)
        want = R.render_background(R._open(jp2), R.Geom(W, H), True, 0.0, brightness=1.0).tobytes()
        assert fp.mm[:n] == want
    finally:
        fp.close()


def test_the_lean_kill_switch_is_todays_rgb_path(monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_FILM_LEAN", "0")
    assert F.lean_enabled() is False
    fp = F.FilmProcess(W, H)
    try:
        assert fp.lean is False
        _fresh()
        jp = _jpeg(_face())
        got = fp.render(jp, 1.0, True, 0.0, brightness=1.0)
        R._GLOW.update(n=0, im=None, fp=None, out=None)
        assert got.tobytes() == R.render_background(R._open(jp), R.Geom(W, H), True, 0.0, brightness=1.0).tobytes()
    finally:
        fp.close()
    monkeypatch.delenv("HOUSE_WALL_FILM_LEAN")
    assert F.lean_enabled() is True


# ---- 2. her mouth out of the exposure ---------------------------------------------------------------------------
def test_no_mouth_and_a_mouth_outside_the_focus_box_are_todays_grid():
    g = R.Geom(W, H, density=1.3)
    src = R._open(_jpeg(_mouth(_face(), 4)))
    today = R.ascii_idx(src, g, True, 0.0)
    assert np.array_equal(R.ascii_idx(src, g, True, 0.0, mouth=None), today)
    assert np.array_equal(R.ascii_idx(src, g, True, 0.0, mouth=(600.0, 330.0, 40.0, 15.0)), today)
    assert np.array_equal(R.ascii_idx(src, g, True, 0.0, mouth=("x", 1, 2, 3)), today)      # a bad box: today
    assert np.array_equal(R.ascii_idx(src, g, False, 0.0, mouth=MOUTH), R.ascii_idx(src, g, False, 0.0))  # camera


def test_a_mouth_over_most_of_the_focus_box_falls_back_to_the_whole_box():
    g = R.Geom(W, H, density=1.3)
    src = R._open(_jpeg(_face()))
    huge = (300.0, 70.0, 140.0, 100.0)
    assert np.array_equal(R.ascii_idx(src, g, True, 0.0, mouth=huge), R.ascii_idx(src, g, True, 0.0))


@pytest.mark.parametrize("geom", [(640, 360, 1.3), (1280, 720, 1.0), (1280, 720, 2.08), (1280, 720, 0.964)])
def test_a_mouth_step_moves_the_whole_glass_today_and_only_the_mouth_with_the_box(geom):
    Wg, Hg, d = geom
    g = R.Geom(Wg, Hg, density=d)
    face = _face()
    shut = R._open(_jpeg(face))
    opened = R._open(_jpeg(_mouth(face, 2)))          # a wide-open mouth: dark enough to move the 2nd percentile
    today = _bbox_share(R.ascii_idx(shut, g, True, 0.0), R.ascii_idx(opened, g, True, 0.0))
    held = _bbox_share(R.ascii_idx(shut, g, True, 0.0, mouth=MOUTH), R.ascii_idx(opened, g, True, 0.0, mouth=MOUTH))
    assert today > 0.50                     # the scene does what Lila's loops do: the exposure moves
    assert 0.0 < held < 0.03                # with her mouth left out: the cells around it


def test_the_skip_is_the_box_and_its_reach_and_skin_around_it_does_not_move_the_exposure():
    x, y, w, h = MOUTH
    p = R.NORM_SKIP_PX
    x0, y0, x1, y1 = R.norm_skip_rect(MOUTH)
    assert x0 <= x - p and y0 <= y - p and x1 >= x + w + p and y1 >= y + h + p
    assert R.norm_skip_rect(None) is None and R.norm_skip_rect((1, 2, 0, 3)) is None
    # the jaw hide inpaints skin over its ellipse (pose_mouth.hide_clip_jaw): mid-tones, beside the dark mouth
    g = R.Geom(1280, 720, density=2.08)
    face = _face()
    jaw = face.copy()
    d = ImageDraw.Draw(jaw)
    cx, cy, ax, ay = x + w / 2, y + 0.62 * h, 0.70 * w, max(1.2 * h, 0.36 * w)
    d.ellipse((cx - ax, cy - ay, cx + ax, cy + ay), fill=(196, 165, 146))
    painted = _mouth(jaw, 2)
    a = R.ascii_idx(R._open(_jpeg(face)), g, True, 0.0, mouth=MOUTH)
    b = R.ascii_idx(R._open(_jpeg(painted)), g, True, 0.0, mouth=MOUTH)
    assert 0.0 < _bbox_share(a, b) < 0.03


def test_the_cache_with_a_mouth_box_is_the_fresh_render_and_a_mouth_step_is_a_region():
    _fresh()
    g = R.Geom(1280, 720, density=1.0)
    c = C.AsciiCache()
    c.cap, c.cap_at = 3 << 30, time.monotonic() + 1e9
    c.prepare(g)
    face = _face()
    idle = _jpeg(face)
    steps = [_jpeg(_mouth(face, k)) for k in (1, 2, 3)]
    prev = None
    p, how, _ = c.picture(idle, g, True, 0.0, None, None, prev, mouth=MOUTH)
    assert p.px == R.coverage_fresh(R._open(idle), g, True, 0.0, mouth=MOUTH).tobytes()
    prev = p
    hows = []
    for jp in steps:
        p, how, _ = c.picture(jp, g, True, 0.0, None, None, prev, mouth=MOUTH)
        hows.append(how)
        assert p.px == R.coverage_fresh(R._open(jp), g, True, 0.0, mouth=MOUTH).tobytes()
        prev = p
    assert hows == ["region"] * 3
    # the same bytes under another box (or none) are another picture: the box is in the bytes' key
    p2, how2, _ = c.picture(idle, g, True, 0.0, None, None, prev, mouth=None)
    assert p2.px == R.coverage_fresh(R._open(idle), g, True, 0.0).tobytes()


def test_today_the_same_mouth_step_is_not_a_region():
    _fresh()
    g = R.Geom(1280, 720, density=1.0)
    c = C.AsciiCache()
    c.cap, c.cap_at = 3 << 30, time.monotonic() + 1e9
    c.prepare(g)
    face = _face()
    p, _, _ = c.picture(_jpeg(face), g, True, 0.0, None, None, None)
    q, how, _ = c.picture(_jpeg(_mouth(face, 3)), g, True, 0.0, None, None, p)
    assert how == "full"


def test_the_film_child_carries_the_mouth_box_both_ways():
    _fresh()
    face = _face()
    frames = [_jpeg(face), _jpeg(_mouth(face, 2)), _jpeg(_mouth(face, 4))]
    g = R.Geom(W, H, density=1.3)
    today = [R.render_background(R._open(jp), g, True, 0.0, brightness=1.0, mouth=MOUTH).tobytes() for jp in frames]
    fp = F.FilmProcess(W, H)
    try:
        for jp, want in zip(frames, today):          # today's path, its glow memo in the same order on both sides
            assert fp.render(jp, 1.3, True, 0.0, brightness=1.0, mouth=MOUTH).tobytes() == want
        for jp in frames:                            # the cache: the fresh render
            got = fp.render(jp, 1.3, True, 0.0, brightness=1.0, cache=True, mouth=MOUTH)
            assert got.tobytes() == R.background_fresh(R._open(jp), g, True, 0.0, brightness=1.0,
                                                       mouth=MOUTH).tobytes()
    finally:
        fp.close()


# ---- 3. the plumbing --------------------------------------------------------------------------------------------
def _wall():
    import house_wall_ascii as G
    return G


def test_the_wall_reads_x_mouth_box_per_part():
    G = _wall()
    part = (b"--frame\r\nContent-Type: image/jpeg\r\nX-Frame-T: 12.5\r\nX-Mouth-Box: 350.0,150.0,40.0,15.0\r\n"
            b"Content-Length: 4\r\n\r\nJPEG\r\n")
    jpeg, rest = G._mjpg_take_one(part)
    assert jpeg == b"JPEG" and G._MJPG["mouth"] == MOUTH and G._MJPG["t"] == 12.5
    jpeg, _ = G._mjpg_take_one(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\nJPEG\r\n")
    assert jpeg == b"JPEG" and G._MJPG["mouth"] is None                     # each part says its own
    for bad in (b"1,2,3", b"a,b,c,d", b"1,2,0,4", b"nan,1,2,3"):
        assert G._mouth_box(bad) is None


def test_the_norm_switch_is_on_in_a_pack_off_on_the_house_and_the_file_names_it(tmp_path, monkeypatch):
    G = _wall()
    import crissa_home
    monkeypatch.delenv("HOUSE_WALL_NORM_MOUTH", raising=False)
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    assert G.norm_mouth_default() is True
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: True)
    assert G.norm_mouth_default() is False
    assert G.norm_mouth_default({"HOUSE_WALL_NORM_MOUTH": "1"}) is True
    assert G.norm_mouth_default({"HOUSE_WALL_NORM_MOUTH": "0"}) is False
    f = tmp_path / "wall-norm-mouth"
    monkeypatch.setattr(G, "NORM_MOUTH_FILE", f)
    G._NM.update(at=0.0, on=False)
    assert G._norm_mouth_on() is False                                     # the house, no file
    f.write_text("")
    G._NM["at"] = 0.0
    assert G._norm_mouth_on() is True                                      # present, empty: on
    f.write_text("off\n")
    G._NM["at"] = 0.0
    assert G._norm_mouth_on() is False
    f.write_text("on\n")
    monkeypatch.setenv("HOUSE_WALL_NORM_MOUTH", "0")
    G._NM["at"] = 0.0
    assert G._norm_mouth_on() is False                                     # the kill beats the file


def test_the_wall_hands_the_box_to_the_film_and_to_its_own_fallback():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    reader = src.split("def t_frames", 1)[1].split("\ndef ", 1)[0]
    assert 'st.fifo.append((jpeg, _MJPG["t"], now_f, _MJPG["mouth"], _MJPG["clip"]))' in reader   # round 13: + X-Clip
    assert "st.fifo.append((b, 0.0, t0, None, None))" in reader                                    # round 13: + X-Clip
    worker = src.split("def render_worker", 1)[1].split("threading.Thread(target=render_worker", 1)[0]
    assert "frame, frame_stamp, frame_at, frame_mouth, frame_clip = st.fifo.popleft()" in worker   # round 13: + X-Clip
    assert "mouth_f = frame_mouth if (own and _norm_mouth_on()) else None" in worker
    assert worker.count("mouth=mouth_f") == 2


SRC_C = (ROOT / "scripts" / "house-conductor.py").read_text()


def _fn(name: str, **globs):
    tree = ast.parse(SRC_C)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns: dict = dict(globs)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "house-conductor.py", "exec"), ns)
    return ns[name]


def test_the_conductor_names_each_frames_mouth_box():
    hdr = _fn("mouth_box_header", MOUTH_BOX_HEADER=True)
    assert hdr((350, 150, 40, 15)) == b"X-Mouth-Box: 350.0,150.0,40.0,15.0\r\n"
    assert hdr(None) == b"" and hdr((1, 2, 0, 3)) == b"" and hdr(("a", 1, 2, 3)) == b""
    assert _fn("mouth_box_header", MOUTH_BOX_HEADER=False)((350, 150, 40, 15)) == b""
    assert 'os.environ.get("HOUSE_CONDUCTOR_MOUTH_BOX", "1")' in SRC_C
    # the box is the film frame's own (Library.box), read with the bytes under one lock, on both routes
    assert "mouth_box = self.lib.box(self.key, self.i)" in SRC_C
    assert "self.frame_mouth = mouth_box" in SRC_C
    assert SRC_C.count("meta=True)") == 2
    mjpg = SRC_C.split('if head in ("/frame.mjpg", "/frame-pip.mjpg"):', 1)[1][:1500]
    assert "+ mouth_box_header(f_mouth)\n" in mjpg and 'b"X-Clip: "' in mjpg        # round 13: X-Clip after it


def test_the_wall_parses_what_the_conductor_writes():
    G = _wall()
    hdr = _fn("mouth_box_header", MOUTH_BOX_HEADER=True)((351.5, 150, 40, 15.25))
    part = b"--frame\r\nContent-Type: image/jpeg\r\nX-Frame-T: 1.0\r\n" + hdr + b"Content-Length: 1\r\n\r\nJ\r\n"
    G._mjpg_take_one(part)
    assert G._MJPG["mouth"] == (351.5, 150.0, 40.0, 15.2)


# ---- 4. the pack defaults and the cold start ------------------------------------------------------------------
def test_live_switch_absent_is_the_default_present_is_on_unless_it_says_off(tmp_path):
    G = _wall()
    f = tmp_path / "switch"
    assert G.live_switch(f, True) is True and G.live_switch(f, False) is False
    for word, want in (("", True), ("on\n", True), ("1", True), ("off\n", False), ("0", False), ("no", False)):
        f.write_text(word)
        assert G.live_switch(f, False) is want and G.live_switch(f, True) is want


def test_a_pack_patches_and_queues_by_default_and_the_house_keeps_its_files(tmp_path, monkeypatch):
    G = _wall()
    monkeypatch.setattr(G, "FILM_PATCH_FILE", tmp_path / "wall-film-patch")
    monkeypatch.setattr(G, "FILM_QUEUE_FILE", tmp_path / "wall-film-queue")
    monkeypatch.setattr(G, "AR_PLANE", "off")
    monkeypatch.delenv("HOUSE_WALL_FILM_PATCH", raising=False)
    monkeypatch.delenv("HOUSE_WALL_FILM_QUEUE", raising=False)

    def both():
        G._FP.update(at=0.0); G._FQ.update(at=0.0)
        return G._film_patch_on(), G._film_queue_on()

    monkeypatch.setattr(G.crissa_home, "is_house", lambda *a, **k: False)
    assert both() == (G.FILM_PATCH_PACK, G.FILM_QUEUE_PACK)
    (tmp_path / "wall-film-patch").write_text("off\n")
    (tmp_path / "wall-film-queue").write_text("off\n")
    assert both() == (False, False)                          # a pack turns them off live
    monkeypatch.setattr(G.crissa_home, "is_house", lambda *a, **k: True)
    (tmp_path / "wall-film-patch").unlink(); (tmp_path / "wall-film-queue").unlink()
    assert both() == (False, False)                          # the house: absent = off, as before
    (tmp_path / "wall-film-patch").write_text("on\n")       # the house's own file today
    assert both() == (True, False)
    monkeypatch.setenv("HOUSE_WALL_FILM_PATCH", "0")
    assert both() == (False, False)                          # the kill beats the file
    monkeypatch.delenv("HOUSE_WALL_FILM_PATCH")
    monkeypatch.setattr(G, "AR_PLANE", "always")
    assert both() == (False, False)                          # the AR plane reads whole pictures: never patches


def test_a_fresh_child_gets_its_cold_start_and_then_the_short_deadline(monkeypatch):
    import signal
    import threading
    monkeypatch.setattr(F, "REPLY_S", 0.15)
    monkeypatch.setattr(F, "COLD_S", 3.0)
    fp = F.FilmProcess(W, H, log=lambda m: None)
    try:
        os.kill(fp.p.pid, signal.SIGSTOP)                        # a cold start slower than REPLY_S
        threading.Timer(0.6, lambda: os.kill(fp.p.pid, signal.SIGCONT)).start()
        assert fp.render(_jpeg(_face()), 1.0, True, 0.0) is not None
        assert fp.render(_jpeg(_face()), 1.0, True, 0.0) is not None
        assert fp.answered == F.COLD_REPLIES
        pid = fp.p.pid
        os.kill(pid, signal.SIGSTOP)                             # warm now: the short deadline drops it
        t0 = time.time()
        assert fp.render(_jpeg(_face()), 1.0, True, 0.0) is None
        assert time.time() - t0 < 1.0 and fp.p is None          # dropped (and killed), the thread draws
    finally:
        fp.close()


def test_a_tall_mouth_paint_over_five_bands_is_still_a_region_and_exact():
    """The close-up's jaw paint spans 4-5 of the cache's 12 row bands and ~13 % of the glass (2026-10-11): with
    BAND_SLACK 3 its own film frame was never found as a base and REGION_MAX 0.08 refused it, so every speaking
    picture there was drawn whole."""
    assert C.BAND_SLACK >= 5 and C.REGION_MAX >= 0.13
    _fresh()
    g = R.Geom(1280, 720, density=1.0)
    c = C.AsciiCache()
    c.cap, c.cap_at = 3 << 30, time.monotonic() + 1e9
    c.prepare(g)
    face = _face()
    p, _, _ = c.picture(_jpeg(face), g, True, 0.0, None, None, None)
    tall = face.copy()
    ImageDraw.Draw(tall).rectangle((350, 95, 390, 205), fill=(150, 120, 105))     # mid-tone: the exposure holds
    rows = R.ascii_idx(R._open(_jpeg(tall)), g, True, 0.0) != R.ascii_idx(R._open(_jpeg(face)), g, True, 0.0)
    r = np.flatnonzero(rows.any(1))
    assert (r[-1] - r[0] + 1) > 3 * g.ROWS / C.BANDS                               # more than 3 bands of rows
    q, how, _ = c.picture(_jpeg(tall), g, True, 0.0, None, None, None)              # no prev: found by its bands
    assert how == "region"
    assert q.px == R.coverage_fresh(R._open(_jpeg(tall)), g, True, 0.0).tobytes()


def test_the_speed_line_says_the_childs_own_time():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    worker = src.split("def render_worker", 1)[1].split("threading.Thread(target=render_worker", 1)[0]
    assert 'speed["child"].append(float(film.child_ms))' in worker
    assert '(f" child={_ms(\'child\')}" if speed["child"] else "")' in worker      # on the ascii cache line
    assert "f\" in {speed['every']:.0f}s{' PATCH' if patching else ''}\")" in worker   # the speed line as it was
    fp = F.FilmProcess(W, H)
    try:
        fp.render(_jpeg(_face()), 1.0, True, 0.0)
        assert isinstance(fp.child_ms, float) and fp.child_ms > 0
    finally:
        fp.close()


def test_a_bad_mouth_box_in_a_request_is_todays_exposure_never_a_fault():
    assert F.mouth_of(None) is None and F.mouth_of("abcd") is None and F.mouth_of((1, 2, 3)) is None
    assert F.mouth_of((1, 2, 0, 4)) is None and F.mouth_of((1, 2, float("nan"), 4)) is None
    assert F.mouth_of([350, 150, 40, 15]) == MOUTH
    _fresh()
    jp = _jpeg(_face())
    fp = F.FilmProcess(W, H)
    try:
        got = fp.render(jp, 1.0, True, 0.0, brightness=1.0, cache=True, mouth=("x", "y", "z", "w"))
        assert got.tobytes() == R.background_fresh(R._open(jp), R.Geom(W, H), True, 0.0, brightness=1.0).tobytes()
        assert not (fp.cache_stats or {}).get("dead")
    finally:
        fp.close()
