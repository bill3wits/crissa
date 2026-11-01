"""A Mac has no /dev/shm (2026-10-13): the film child's frame file was opened there, so on a Mac the wall's film
process never started and its film thread drew every frame (no ASCII cache, no disk tier). Where /dev/shm is not a
directory the frame file goes in the temp directory now. Linux is unchanged. Kill: HOUSE_WALL_FILM_SHM_FALLBACK=0.

The fallback is forced here on Linux by pointing SHM_DIR at a directory that does not exist, with the temp directory
on disk (pytest's basetemp), as a Mac's $TMPDIR is.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import house_wall_ascii_render as R  # noqa: E402
import wall_film_proc as F  # noqa: E402
from PIL import Image  # noqa: E402

W, H = 640, 360
DEAD_PID = 999999999            # past any pid_max: os.kill says ESRCH


def _jpeg() -> bytes:
    im = Image.new("RGB", (320, 180))
    for x in range(320):
        for y in range(0, 180, 6):
            im.putpixel((x, y), (x % 256, (x * 3) % 256, (y * 7) % 256))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return b.getvalue()


@pytest.fixture
def no_shm(monkeypatch, tmp_path):
    """A machine with no /dev/shm whose temp directory is on disk; returns that directory."""
    tmp = tmp_path / "T"
    tmp.mkdir()
    monkeypatch.setattr(F, "SHM_DIR", str(tmp_path / "no-dev-shm"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    monkeypatch.delenv("HOUSE_WALL_FILM_SHM_FALLBACK", raising=False)
    return tmp


def test_linux_keeps_dev_shm_and_the_same_path(monkeypatch):
    if not os.path.isdir("/dev/shm"):
        pytest.skip("this machine has no /dev/shm")
    assert F.SHM_DIR == "/dev/shm" and F.frame_dir() == "/dev/shm"
    assert F.frame_dir({"HOUSE_WALL_FILM_SHM_FALLBACK": "0"}) == "/dev/shm"
    monkeypatch.setattr(F.FilmProcess, "_start", lambda self: None)       # the path only, no child
    fp = F.FilmProcess(W, H)
    try:
        assert fp.shm_path == "/dev/shm/house-wall-film-%d" % os.getpid()   # the string it always was
    finally:
        fp.close()
    assert not os.path.exists(fp.shm_path)


def test_with_no_dev_shm_the_frame_file_goes_in_the_temp_directory(no_shm):
    assert F.frame_dir() == str(no_shm)
    assert F.frame_dir({"HOUSE_WALL_FILM_SHM_FALLBACK": "0"}) == F.SHM_DIR         # the kill: /dev/shm only


def test_the_child_starts_there_and_draws_the_same_bytes_as_the_thread(no_shm):
    for v in vars(R).values():
        if callable(getattr(v, "cache_clear", None)):
            v.cache_clear()
    R._GLOW.update(n=0, im=None, fp=None, out=None)
    jpg = _jpeg()
    fp = F.FilmProcess(W, H, log=lambda m: None)
    try:
        assert fp.shm_path == str(no_shm / ("house-wall-film-%d" % os.getpid()))
        assert os.path.getsize(fp.shm_path) == W * H * 3
        assert fp.p is not None and fp.p.poll() is None                         # the child is up
        for density, bright, own in [(1.0, 1.0, True), (1.7, 1.3, True), (1.2, 0.6, False)]:
            t = time.time()
            a = R.render_background(R._open(jpg), R.Geom(W, H, density=density), own, t, brightness=bright)
            b = fp.render(jpg, density, own, t, brightness=bright)
            assert b is not None and b.size == a.size and b.tobytes() == a.tobytes()
    finally:
        fp.close()
    assert not os.path.exists(fp.shm_path)


def test_the_kill_gives_back_todays_failure_with_no_dev_shm(no_shm, monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_FILM_SHM_FALLBACK", "0")
    with pytest.raises(FileNotFoundError):                 # what the wall catches: "film process: off"
        F.FilmProcess(W, H, log=lambda m: None)
    assert list(no_shm.iterdir()) == []


def test_a_stale_frame_file_is_swept_from_the_temp_directory(no_shm, monkeypatch):
    stale = no_shm / ("house-wall-film-%d" % DEAD_PID)
    stale.write_bytes(b"x")
    other = no_shm / "someone-elses-file"
    other.write_bytes(b"y")
    monkeypatch.setattr(F.FilmProcess, "_start", lambda self: None)
    fp = F.FilmProcess(W, H)
    try:
        assert not stale.exists() and other.exists()
        assert Path(fp.shm_path).exists()                                  # its own, live, is kept
        F._sweep_stale(str(no_shm))
        assert Path(fp.shm_path).exists()
    finally:
        fp.close()
