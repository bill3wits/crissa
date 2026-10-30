"""Round 17: the desk Wall's memory tier held compressed, and the clip on the glass first (2026-10-13; the User via
.101: "keep the memory tier compressed", three minutes into a long reading `held=4092MB of 4096MB pictures=816
refused=813`). docs/checkpoints/round17-wall-zmem-20261013.

Pinned: on in a pack, off on the house unless asked, never without zstd; a kept picture is held as zstd-1 bytes and
counted so, and every picture handed out is still the fresh render byte for byte (whole, from its bytes, a mouth step
over a packed base, after a restart from disk); nothing is packed on the film path (the disk writer hands its bytes
back, else the squeeze thread packs); a disk entry is held as the file's own bytes; the unpacked copies are bounded by
RAW_KEEP; a picture of the clip on the glass takes another clip's room instead of being refused, never its own clip's.
"""
from __future__ import annotations

import gc
import sys
import threading
import time
import weakref
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))
import house_wall_ascii_render as R  # noqa: E402
import wall_ascii_cache as C  # noqa: E402
import wall_ascii_disk as D  # noqa: E402
from test_wall_ascii_cache import W, H, _cache, _fresh_state, _jpeg, _mouth, _scene  # noqa: E402
from test_wall_ascii_disk_20261012 import FR, _fresh, _new_cache, _settle, disk  # noqa: E402,F401

pytestmark = pytest.mark.skipif(C._zstd_mod() is None, reason="no zstd in this Python")
BOX = (330, 230, 60, 24)


@pytest.fixture
def zmem(monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_ZMEM", "1")
    monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_GLASS_FIRST", "1")
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK", "0")
    _fresh_state()


def _drained(c, timeout=10.0):
    """Wait for the squeeze thread / disk writer, then swap their bytes in (the film child does it per picture)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        c._drain()
        if all(p.z is not None and p.zidx is not None for p in c.pics.values()):
            return
        time.sleep(0.02)


def _ask(c, jp, g, clip=None, prev=None, mouth=None):
    return c.picture(jp, g, True, 0.0, prev=prev, mouth=mouth, frame=FR, clip=clip)


def test_on_in_a_pack_off_on_the_house_and_the_word_wins(monkeypatch):
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda: True)
    assert not C.zmem_enabled({}) and not C.glass_first_enabled({})
    assert C.zmem_enabled({"HOUSE_WALL_ASCII_CACHE_ZMEM": "1"})
    assert C.glass_first_enabled({"HOUSE_WALL_ASCII_CACHE_GLASS_FIRST": "1"})
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    assert C.zmem_enabled({}) and C.glass_first_enabled({})
    assert not C.zmem_enabled({"HOUSE_WALL_ASCII_CACHE_ZMEM": "0"})
    assert not C.glass_first_enabled({"HOUSE_WALL_ASCII_CACHE_GLASS_FIRST": "0"})


def test_no_zstd_is_todays_raw_tier(monkeypatch, zmem):
    monkeypatch.setattr(C, "_zstd_mod", lambda: None)
    assert not C.zmem_enabled({"HOUSE_WALL_ASCII_CACHE_ZMEM": "1"})
    c = _cache()
    g = R.Geom(W, H, density=1.0)
    p, how, _ = _ask(c, _jpeg(_scene(1)), g)
    assert not c.zmem and how == "full" and p.z is None and p.zm is None


def test_kept_pictures_are_held_as_zstd_counted_so_and_exact(zmem):
    g = R.Geom(W, H, density=1.0)
    c = _cache(); c.prepare(g)
    assert c.zmem
    frames = [_jpeg(_scene(700 + i)) for i in range(8)]
    grids = {}
    for jp in frames:
        p, how, _ = _ask(c, jp, g)
        assert how == "full" and bytes(p.px) == _fresh(jp, g)
        grids[p.serial] = bytes(p.idx)
    _drained(c)
    held = sum(p.nbytes for p in c.pics.values())
    assert c.bytes == held
    for p in c.pics.values():
        assert p.z is not None and p.zidx is not None and p.nbytes < W * H   # the zstd bytes are what is counted
        assert p.nbytes == len(p.z) + len(p.zidx) + p.small.size[0] * p.small.size[1] + 512
        assert p.idx == grids[p.serial] and len(p.zidx) < p.nidx           # the grid packed too, the same grid
    assert c.zn["squeezed"] == len(frames) and c.zn["writer"] == 0       # no disk tier: the squeeze thread packed
    # a second lap: every picture from its bytes, unpacked when drawn, the same bytes
    before = c.zn["unpacks"]
    for jp in frames:
        p, how, _ = _ask(c, jp, g)
        assert how == "bytes" and bytes(p.px) == _fresh(jp, g)
    assert c.zn["unpacks"] - before >= len(frames) - C.RAW_KEEP
    assert len(c.raw) <= C.RAW_KEEP
    assert c.raw_bytes == sum(C.AsciiCache._lru_size(q) for q in c.raw.values())
    assert c.held_bytes() == c.bytes + c.jpeg_bytes + c.raw_bytes
    s = c.stats()["zmem"]
    assert s["unpacks"] == c.zn["unpacks"] and s["unpack_p50"] is not None


def test_a_mouth_step_over_a_packed_base_is_exact_and_unpacks_the_base_once(zmem):
    g = R.Geom(W, H, density=1.0)
    c = _cache(); c.prepare(g)
    base_im = _scene(41)
    idle = _jpeg(base_im)
    p0, how, _ = _ask(c, idle, g, clip=("listen", 3, 10, "loop"), mouth=BOX)
    assert how == "full"
    _ask(c, _jpeg(_scene(42)), g)                                          # another picture is the last one
    _drained(c)
    assert p0.z is not None
    if p0.serial in c.raw:                                                 # it was the last one when it was packed
        c._raw_drop(p0.serial)
    assert p0._raw is None                                                 # packed, its unpacked copy given back
    before = c.zn["unpacks"]
    prev = None
    for k in (1, 2):                                                       # two semiframes of film frame 3
        jp = _jpeg(_mouth(base_im, BOX, k))
        q, how, _ = _ask(c, jp, g, clip=("listen", 3, 10, "loop"), prev=prev, mouth=BOX)
        want = R.coverage_fresh(R._open(jp), g, True, 0.0, mouth=BOX, frame=FR).tobytes()
        assert how == "region" and bytes(q.px) == want
        assert q.z is None and not q.kept                                  # a mouth step is never packed
        prev = q
    assert c.zn["unpacks"] - before == 1                                   # the base once, then from the raw LRU


def test_the_raw_lru_is_bounded_and_eviction_gives_the_copy_back(zmem):
    g = R.Geom(W, H, density=1.0)
    c = _cache(); c.prepare(g)
    frames = [_jpeg(_scene(800 + i)) for i in range(C.RAW_KEEP + 4)]
    for jp in frames:
        _ask(c, jp, g)
    _drained(c)
    for jp in frames:
        p, how, _ = _ask(c, jp, g)
        assert how == "bytes" and bytes(p.px) == _fresh(jp, g)            # the child reads px to lay the frame
    assert len(c.raw) == C.RAW_KEEP
    s = next(iter(c.raw))
    c._evict(s)
    assert s not in c.raw and c.raw_bytes == sum(C.AsciiCache._lru_size(q) for q in c.raw.values())


def test_a_dropped_cache_frees_its_pictures_at_once_and_its_squeeze_thread_ends(zmem):
    """No reference cycle (a picture holds its cache weakly) and no thread holding pictures: a test makes dozens of
    caches, and a cycle or a lingering queue kept each one's pictures until a GC pass (round 17: an OOM in the sweep)."""
    g = R.Geom(W, H, density=1.0)
    alive = lambda: sum(1 for th in threading.enumerate() if th.name == "ascii-zmem")   # noqa: E731
    before = alive()
    gc.disable()
    try:
        c = _cache(); c.prepare(g)
        refs = []
        for i in range(6):
            p, how, _ = _ask(c, _jpeg(_scene(1200 + i)), g)
            refs.append(weakref.ref(p))
        assert alive() == before + 1
        cref = weakref.ref(c)
        del c, p
        t0 = time.time()
        while time.time() - t0 < 10 and (cref() is not None or any(r() is not None for r in refs) or alive() > before):
            time.sleep(0.05)
        assert cref() is None and all(r() is None for r in refs) and alive() == before
    finally:
        gc.enable()


def test_a_disk_entry_is_held_as_its_files_bytes_and_the_writer_packs_once(disk, monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_ZMEM", "1")
    if D.codec() != "zstd-1":
        pytest.skip("the disk tier writes raw here")
    g = R.Geom(W, H, density=1.0)
    frames = [_jpeg(_scene(900 + i)) for i in range(5)]
    a = _new_cache(); a.prepare(g)
    assert a.zmem
    for i, jp in enumerate(frames):
        p, how, _ = _ask(a, jp, g, clip=("listen", i, 5, "loop"))
        assert how == "full"
    _settle(a, len(frames))
    _drained(a)
    assert a.zn["writer"] == len(frames) and a.zn["squeezed"] == 0         # the writer's bytes, nothing packed twice
    # a restart: the lap from disk, held as the entries' own zstd bytes
    b = _new_cache(); b.prepare(g)
    for i, jp in enumerate(frames):
        q, how, _ = _ask(b, jp, g, clip=("listen", i, 5, "loop"))
        assert how == "disk" and bytes(q.px) == _fresh(jp, g)
        assert q.kept and q.z is not None
        path = b.disk.lookup("listen", i)[1]
        assert q.z == D.read_entry(path, keep_z=True)["zpx"]
    _drained(b)
    assert b.zn["squeezed"] == 0 and b.zn["writer"] == 0                  # the pixels: nothing packed again
    assert all(q.zidx is not None for q in b.pics.values())              # the grids: packed by the squeeze thread


def test_the_disk_writer_still_takes_an_item_without_a_hook(disk):
    g = R.Geom(W, H, density=1.0)
    c = _new_cache(); c.prepare(g)
    jp = _jpeg(_scene(990))
    p, how, _ = _ask(c, jp, g, clip=("old", 0, 1, "loop"))
    c.disk.q.put((c.disk.fdir, "older", 0, "ab" * 12,
                  (W, H, g.ROWS, g.COLS, p.idx, p.small.size, p.small.tobytes(), jp, None, bytes(p.px), {})))
    _settle(c, 2)
    assert c.disk.stats()["writes"] == 2


def _jpegs(n, seed):
    return [_jpeg(_scene(seed + i)) for i in range(n)]


def _fill(c, g, clip, jps):
    out = []
    for i, jp in enumerate(jps):
        p, how, _ = _ask(c, jp, g, clip=(clip, i, len(jps), "loop"))
        assert how == "full"
        out.append(p)
    return out


def test_the_clip_on_the_glass_takes_another_clips_room_instead_of_a_refusal(monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK", "0")
    monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_ZMEM", "0")
    g = R.Geom(W, H, density=1.0)
    for glass, want_refused in (("1", 0), ("0", 3)):
        monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_GLASS_FIRST", glass)
        _fresh_state()
        c = _cache(); c.prepare(g)
        a = _fill(c, g, "close", _jpegs(4, 300))                           # the loop she just left: all fresh
        now = _jpegs(3, 400)
        # room for every picture held now and for the JPEG names to come, none for another picture
        c.cap = sum(p.nbytes for p in a) + c.jpeg_bytes + sum(len(j) for j in now) + 1000
        b = _fill(c, g, "listen", now)                                     # the loop she speaks in now
        assert c.n["refused"] == want_refused
        if glass == "1":
            assert c.n["glass"] == 3 and all(p.kept for p in b)
            assert [p.kept for p in a] == [False, False, False, True]      # least recently used first
        else:
            assert c.n["glass"] == 0 and all(p.kept for p in a) and not any(p.kept for p in b)


def test_the_clip_on_the_glass_never_takes_its_own_room(monkeypatch):
    """A loop longer than the budget keeps the part that fits (the STALE_S rule), as before."""
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK", "0")
    monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_ZMEM", "0")
    monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_GLASS_FIRST", "1")
    _fresh_state()
    g = R.Geom(W, H, density=1.0)
    c = _cache(); c.prepare(g)
    a = _fill(c, g, "listen", _jpegs(3, 500))
    c.cap = sum(p.nbytes for p in a) + c.jpeg_bytes + 1000
    more = _fill(c, g, "listen", _jpegs(3, 600))
    assert c.n["refused"] == 3 and c.n["glass"] == 0 and all(p.kept for p in a) and not any(p.kept for p in more)


def test_the_wall_line_says_the_memory_tier_is_compressed():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert "zmem unpacks=%d (%.1f/%.1f ms) glass=%d" in src
