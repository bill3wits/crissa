"""The wall's film in its own process (the User 2026-10-10: "Split the film renderer into its own process").

The Tk painter and the film thread shared one interpreter lock at 80-100 % of a core. render_background runs in a
child now; the frames must be byte-identical to the in-thread ones, and any failure must draw in-thread as before.
"""
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import house_wall_ascii_render as R  # noqa: E402
import wall_film_proc as F  # noqa: E402
from PIL import Image  # noqa: E402

W, H = 640, 360


def _jpeg() -> bytes:
    im = Image.new("RGB", (320, 180))
    for x in range(320):
        for y in range(0, 180, 6):
            im.putpixel((x, y), (x % 256, (x * 3) % 256, (y * 7) % 256))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return b.getvalue()


def test_the_child_draws_the_same_bytes_as_the_thread():
    # an earlier test can leave the renderer's caches built under its own env (HOUSE_WALL_ZOOM); the child starts
    # fresh, and so does this side
    for v in vars(R).values():
        if callable(getattr(v, "cache_clear", None)):
            v.cache_clear()
    R._GLOW.update(n=0, im=None, fp=None, out=None)     # the glow is reused every other frame: same history both sides
    jpg = _jpeg()
    fp = F.FilmProcess(W, H)
    try:
        for density, bright, shift, hz, own in [(1.0, 1.0, None, None, True), (1.7, 1.3, (0.25, 0.0), 0.5, True),
                                                 (1.2, 0.6, None, None, False)]:
            t = time.time()
            a = R.render_background(R._open(jpg), R.Geom(W, H, density=density), own, t,
                                    hole_zoom=hz, brightness=bright, shift=shift)
            b = fp.render(jpg, density, own, t, hole_zoom=hz, brightness=bright, shift=shift)
            assert b is not None and b.size == a.size and b.tobytes() == a.tobytes()
        t = time.time()
        none = fp.render(None, 1.0, False, t)
        assert none is not None and none.tobytes() == R.render_background(None, R.Geom(W, H), False, t).tobytes()
    finally:
        fp.close()
    assert not os.path.exists(fp.shm_path)


def test_a_dead_child_means_the_thread_draws_and_it_comes_back_later(monkeypatch):
    fp = F.FilmProcess(W, H, log=lambda m: None)
    try:
        fp.p.kill()
        fp.p.wait()
        assert fp.render(_jpeg(), 1.0, True, time.time()) is None       # the caller draws this frame itself
        assert fp.p is None and fp.fails == 1
        assert fp.render(_jpeg(), 1.0, True, time.time()) is None       # not before RESPAWN_S
        monkeypatch.setattr(F, "RESPAWN_S", 0.0)
        assert fp.render(_jpeg(), 1.0, True, time.time()) is not None   # back
    finally:
        fp.close()


def test_a_child_that_does_not_answer_is_dropped(monkeypatch):
    monkeypatch.setattr(F, "REPLY_S", 0.2)
    fp = F.FilmProcess(W, H, log=lambda m: None)
    try:
        for _ in range(F.COLD_REPLIES):                                  # past its cold start (wall-speed-101)
            assert fp.render(_jpeg(), 1.0, True, time.time()) is not None
        os.kill(fp.p.pid, 19)                                            # SIGSTOP: alive, silent
        t0 = time.time()
        assert fp.render(_jpeg(), 1.0, True, time.time()) is None
        assert time.time() - t0 < 2.0 and fp.p is None
    finally:
        fp.close()


def test_a_stale_frame_file_is_swept():
    stale = Path("/dev/shm/house-wall-film-999999999")
    stale.write_bytes(b"x")
    fp = F.FilmProcess(W, H)
    fp.close()
    assert not stale.exists()


def test_off_switch(monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_FILM_PROCESS", "0")
    assert F.enabled() is False
    monkeypatch.delenv("HOUSE_WALL_FILM_PROCESS")
    assert F.enabled() is True


def test_the_wall_uses_it_and_keeps_the_thread_path():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    worker = src.split("def render_worker", 1)[1].split("threading.Thread(target=render_worker", 1)[0]
    assert "film = wall_film_proc.FilmProcess(W, H, log=log, feed=FEED)" in worker   # 2026-10-13: the feed goes to the child
    assert "pkt[\"bg\"] = bg_im if bg_im is not None else R.render_background(" in worker
    assert "film.close()" in worker


def test_the_film_queue_is_a_live_file_switch(tmp_path, monkeypatch):
    """The User 2026-10-10 ("Let's test"): every distinct frame in order, switched by a file, no restart."""
    import house_wall_ascii as G
    monkeypatch.setattr(G, "FILM_QUEUE_FILE", tmp_path / "wall-film-queue")
    monkeypatch.setattr(G, "_FQ", {"at": 0.0, "on": False})
    monkeypatch.setattr(G.crissa_home, "is_house", lambda *a, **k: True)     # the house: absent = off
    assert G._film_queue_on() is False
    (tmp_path / "wall-film-queue").write_text("on\n")
    G._FQ["at"] = 0.0
    assert G._film_queue_on() is True
    monkeypatch.setenv("HOUSE_WALL_FILM_QUEUE", "0")
    G._FQ["at"] = 0.0
    assert G._film_queue_on() is False


def test_the_reader_queues_each_distinct_frame_and_the_film_draws_the_oldest():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    reader = src.split("def t_frames", 1)[1].split("\ndef ", 1)[0]
    assert reader.count("st.fifo.append(") == 2
    worker = src.split("def render_worker", 1)[1].split("threading.Thread(target=render_worker", 1)[0]
    assert "frame, frame_stamp, frame_at, frame_mouth, frame_clip = st.fifo.popleft()" in worker   # round 13: + X-Clip
    assert "backlog = queued and bool(st.fifo)" in worker


def _jpeg_with_mouth(open_: bool) -> bytes:
    im = Image.open(io.BytesIO(_jpeg())).convert("RGB")
    if open_:
        for x in range(150, 170):
            for y in range(120, 132):
                im.putpixel((x, y), (250, 240, 230))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return b.getvalue()


def test_a_mouth_step_comes_back_as_a_patch_that_rebuilds_the_whole_picture():
    """The User 2026-10-10: both semiframes of a frame on the glass. The patch laid over the previous picture must
    be byte-identical to the whole next picture."""
    for v in vars(R).values():
        if callable(getattr(v, "cache_clear", None)):
            v.cache_clear()
    R._GLOW.update(n=0, im=None, fp=None, out=None)
    shut, opened = _jpeg_with_mouth(False), _jpeg_with_mouth(True)
    t = time.time()
    g = R.Geom(W, H, density=1.3)
    whole_a = R.render_background(R._open(shut), g, True, t, brightness=1.0)
    whole_b = R.render_background(R._open(opened), g, True, t, brightness=1.0)
    fp = F.FilmProcess(W, H)
    try:
        a, xy_a = fp.render_patch(shut, 1.3, True, t, brightness=1.0, patch_max=0.4)
        assert xy_a is None and a.tobytes() == whole_a.tobytes()              # the first picture is whole
        patch, xy = fp.render_patch(opened, 1.3, True, t, brightness=1.0, patch_max=0.4)
        assert xy is not None and patch.size[0] * patch.size[1] < 0.4 * W * H
        rebuilt = a.copy()
        rebuilt.paste(patch, xy)
        assert rebuilt.tobytes() == whole_b.tobytes()
        same = fp.render_patch(opened, 1.3, True, t, brightness=1.0, patch_max=0.4)
        assert same == (None, None) or (same[1] is not None)                  # nothing (or only the glow) moved
        full, xy_full = fp.render_patch(shut, 1.3, True, t, brightness=1.0, patch_max=0.0)
        assert xy_full is None                                                  # patch_max 0 = always whole
    finally:
        fp.close()


def test_the_wall_paints_patches_by_region_and_asks_for_whole_pictures_when_out_of_step():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    worker = src.split("def render_worker", 1)[1].split("threading.Thread(target=render_worker", 1)[0]
    assert 'patch_max=(0.4 if (patching and not need_full) else 0.0)' in worker
    assert "need_full = True        # the glass missed a picture" in worker
    assert "need_full = got is None" in worker
    assert "stride = 1 if patching else" in worker
    pump = src.split("def pump():", 1)[1].split("\n    def ", 1)[0]
    assert 'root.tk.call(str(photos["bg"]), "copy", str(pp), "-to", x, y)' in pump
    assert "full_bg.paste(im, (x, y))" in pump
    assert 'and live_switch(FILM_PATCH_FILE, _pack_default(FILM_PATCH_PACK)) and AR_PLANE == "off"' in src
