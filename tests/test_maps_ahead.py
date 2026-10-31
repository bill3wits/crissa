"""The cheeks maps run ahead of the paint and are kept compact (the User 2026-10-13: "pull from existing map ... some
frames in advance", "compressing ... if no tradeoffs"). Every entry handed back is the same bytes as before."""
from __future__ import annotations

import ast
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import maps_ahead  # noqa: E402
import maps_disk  # noqa: E402
import maps_pack  # noqa: E402
import poly_cache  # noqa: E402

pytestmark = pytest.mark.skipif(not maps_pack.available(), reason="zstandard not installed")


def _maps(seed=0, wh=(80, 60)):
    rng = np.random.default_rng(seed)
    mx = np.full((wh[1], wh[0]), -1.0, np.float32)
    my = np.full((wh[1], wh[0]), -1.0, np.float32)
    mx[10:40, 20:60] = rng.random((30, 40), dtype=np.float32) * 50 - 0.3
    my[10:40, 20:60] = rng.random((30, 40), dtype=np.float32) * 30
    return mx, my


def _same(a, b):
    return all(np.array_equal(x, y) and x.dtype == y.dtype for x, y in zip(a, b))


def _flush(tier, n, timeout=5.0):
    t0 = time.time()
    while tier.stats["write"] < n and time.time() - t0 < timeout:
        time.sleep(0.02)
    assert tier.stats["write"] >= n


@pytest.fixture
def tier(tmp_path, monkeypatch):
    monkeypatch.setattr(poly_cache, "cheeks_cache_on", lambda: True)
    monkeypatch.setattr(poly_cache, "cheeks_maps_limit", lambda: 16)
    poly_cache.reset_mouth_caches()
    t = maps_disk.MapsDisk("fp-test", root=tmp_path, cap_gb=1.0, log=lambda *_: None)
    poly_cache.set_maps_disk(t, lambda loop, rel: b"salt")
    yield t
    poly_cache.set_maps_disk(None, None)
    poly_cache.set_ahead(None)
    poly_cache.reset_mouth_caches()


KW = dict(loop_id="clip", frame_i=7, version=(1, 2), rel="t.png", dst={"nose": [10.0, 20.0]},
          wh=(80, 60), scale=1.0, pin_key=("nose-pixel", 0.0, 0.0), tile_hw=(30, 40))


def _build(log):
    def build():
        log.append(1)
        return _maps(), {"delta": (1.5, -0.5)}
    return build


def test_pack_is_lossless_and_smaller():
    roi = poly_cache.crop_maps(_maps(3))
    v = (roi, {"delta": (0.5, 1.0), "pin": {"ncc": 0.97}})
    blob = maps_pack.pack(v)
    w = maps_pack.unpack(blob)
    assert w[0][:2] == roi[:2] and _same(w[0][2:], roi[2:]) and w[1] == v[1]
    assert len(blob) < len(pickle.dumps(v, protocol=5))


def test_disk_writes_the_compact_form_and_still_reads_the_old(tier):
    built = []
    want = poly_cache.cheeks_maps_cached(build=_build(built), **KW)
    _flush(tier, 1)
    files = list(tier.root.rglob("*.map"))
    assert len(files) == 1 and files[0].read_bytes()[:4] == maps_disk.MAGIC2
    # an HMD1 file from before reads the same
    kb = b"old-key"
    payload = pickle.dumps(("x", 1), protocol=5)
    import hashlib
    blob = maps_disk.MAGIC + len(kb).to_bytes(4, "little") + kb + hashlib.blake2b(payload, digest_size=16).digest() + payload
    p = tier._path(kb)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(blob)
    assert tier.get(kb) == ("x", 1)
    poly_cache.reset_mouth_caches()
    got = poly_cache.cheeks_maps_cached(build=_build(built), **KW)
    assert len(built) == 1 and _same(got[0], want[0]) and got[1] == want[1]


def test_the_compressed_tier_answers_before_the_disk(tier):
    built = []
    want = poly_cache.cheeks_maps_cached(build=_build(built), **KW)
    _flush(tier, 1)
    assert poly_cache.mouth_cache_stats()["maps_z"] == 1             # the writer handed its bytes over
    with poly_cache._LOCK:
        poly_cache._CHEEKS.clear()                                    # memory evicted it; the packed copy stays
    reads = tier.stats["hit"]
    got = poly_cache.cheeks_maps_cached(build=_build(built), **KW)
    assert _same(got[0], want[0]) and len(built) == 1 and tier.stats["hit"] == reads
    assert poly_cache.mouth_cache_stats()["cheeks_z_hit"] == 1


def test_a_reload_clears_the_compressed_tier(tier):
    poly_cache.cheeks_maps_cached(build=_build([]), **KW)
    _flush(tier, 1)
    poly_cache.reset_tile_caches()
    assert poly_cache.mouth_cache_stats()["maps_z"] == 0


def test_the_stage_learns_a_key_and_brings_it_in_before_the_paint(tier, tmp_path):
    stage = maps_ahead.Ahead(tmp_path / "j.pkl", log=lambda *_: None)
    poly_cache.set_ahead(stage)
    built = []
    want = poly_cache.cheeks_maps_cached(build=_build(built), tag="AH", **KW)
    _flush(tier, 1)
    assert stage.slots[("clip", 7, "AH")]
    poly_cache.reset_mouth_caches()                                   # memory and the packed tier empty: disk only
    stage.plan("clip", [(7, "AH"), (8, "EE")])
    t0 = time.time()
    while stage.stats["disk"] < 1 and time.time() - t0 < 5:
        time.sleep(0.02)
    assert stage.stats["disk"] == 1
    reads = tier.stats["hit"]
    got = poly_cache.cheeks_maps_cached(build=_build(built), tag="AH", **KW)
    assert _same(got[0], want[0]) and len(built) == 1 and tier.stats["hit"] == reads   # the paint read nothing
    assert poly_cache.mouth_cache_stats()["cheeks_ahead_used"] == 1


def test_the_journal_survives_a_restart(tmp_path):
    a = maps_ahead.Ahead(tmp_path / "j.pkl", fetch=lambda k, b: "none", log=lambda *_: None)
    a.note("clip", 3, "OH", ("clip", 3, "k"), b"kb")
    a.save()
    b = maps_ahead.Ahead(tmp_path / "j.pkl", fetch=lambda k, b: "none", log=lambda *_: None)
    assert b.slots[("clip", 3, "OH")] == [(("clip", 3, "k"), b"kb")]
    (tmp_path / "j.pkl").write_bytes(b"junk")
    c = maps_ahead.Ahead(tmp_path / "j.pkl", fetch=lambda k, b: "none", log=lambda *_: None)
    assert c.slots == {}


def test_a_slot_keeps_its_newest_keys_only(tmp_path):
    a = maps_ahead.Ahead(None, fetch=lambda k, b: "none", log=lambda *_: None)
    for i in range(6):
        a.note("clip", 1, "AH", ("k", i), None)
    assert [k for k, _ in a.slots[("clip", 1, "AH")]] == [("k", 5), ("k", 4), ("k", 3), ("k", 2)]


def test_adopt_takes_the_old_directory_once(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "x.map").write_bytes(b"1")
    (tmp_path / "adopt.json").write_text(json.dumps({"from": "old", "to": "new"}))
    t = maps_disk.MapsDisk("new", root=tmp_path, cap_gb=1.0, log=lambda *_: None)
    assert (t.root / "x.map").exists() and not old.exists()
    other = maps_disk.MapsDisk("other", root=tmp_path, cap_gb=1.0, log=lambda *_: None)
    assert not any(other.root.iterdir())


def test_switches(monkeypatch):
    monkeypatch.setenv("HOUSE_MOUTH_MAPS_AHEAD", "0")
    assert maps_ahead.enabled() is False
    monkeypatch.setenv("HOUSE_MOUTH_MAPS_PACK", "0")
    assert maps_disk.pack_on() is False
    monkeypatch.setenv("HOUSE_MOUTH_MAPS_Z_MB", "0")
    assert poly_cache.z_limit_bytes() == 0


def _playhead_methods(*names):
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    lines = src.splitlines(keepends=True)
    ph = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Playhead")
    fn = {n.name: n for n in ph.body if isinstance(n, ast.FunctionDef)}
    body = "".join("".join(lines[fn[m].lineno - 1:fn[m].end_lineno]) for m in names)
    return "class P:\n" + body


def test_the_conductor_plans_the_next_frames_from_the_sheet():
    from conductor import viseme_at
    from mouth_viseme import viseme_of
    ns = {"time": time, "viseme_at": viseme_at, "viseme_of": viseme_of, "maps_ahead": maps_ahead,
          "CLIP_FPS": 24.0}
    exec(_playhead_methods("_ahead_plan", "_class_at"), ns)

    class Stage:
        def plan(self, loop, ft):
            self.got = (loop, list(ft))

    stage = Stage()
    ns["_MAPS_AHEAD"] = {"stage": stage}
    now = time.time()
    rows = [[now + k * 0.020833, 0.5, "AH" if k < 24 else "MBP"] for k in range(48)]

    class Mouth:
        force_class = None
        force = None
        STALE_S = 0.5
        frames = rows

    class Lib:
        def head_track_len(self, key):
            return 10

    p = ns["P"]()
    p.key, p.i, p.mouth, p.lib = "clip", 8, Mouth(), Lib()
    p._ahead_plan()
    loop, ft = stage.got
    assert loop == "clip" and ft[0] == (9, "AH")
    assert (1, "AH") in ft or (1, "MBP") in ft                         # the loop wraps at its length
    assert any(t == "MBP" for _f, t in ft)                             # the sheet's next class is planned
    stage.got = None
    p._ahead_plan()                                                    # once a film frame
    assert stage.got is None
