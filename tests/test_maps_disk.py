"""The cheeks maps' disk tier: a restart starts warm, and an entry is exactly what was built (the User 2026-10-12)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import maps_disk  # noqa: E402
import poly_cache  # noqa: E402


def _flush(tier, n, timeout=5.0):
    t0 = time.time()
    while tier.stats["write"] < n and time.time() - t0 < timeout:
        time.sleep(0.02)
    assert tier.stats["write"] >= n


def _maps(seed=0, wh=(80, 60)):
    rng = np.random.default_rng(seed)
    mx = np.full((wh[1], wh[0]), -1.0, np.float32)
    my = np.full((wh[1], wh[0]), -1.0, np.float32)
    mx[10:40, 20:60] = rng.random((30, 40), dtype=np.float32) * 50
    my[10:40, 20:60] = rng.random((30, 40), dtype=np.float32) * 30
    return mx, my


@pytest.fixture
def tier(tmp_path):
    t = maps_disk.MapsDisk("fp-test", root=tmp_path, cap_gb=1.0, log=lambda *_: None)
    yield t
    poly_cache.set_maps_disk(None, None)


def test_a_value_comes_back_byte_for_byte(tier):
    roi = (3, 4, np.arange(12, dtype=np.float32).reshape(3, 4), np.ones((3, 4), np.float32))
    tier.put(b"k1", (roi, {"pin": {"ncc": 0.97}}))
    _flush(tier, 1)
    got = tier.get(b"k1")
    assert got[1] == {"pin": {"ncc": 0.97}}
    assert got[0][:2] == (3, 4) and np.array_equal(got[0][2], roi[2]) and got[0][2].dtype == np.float32
    assert tier.get(b"k2") is None


def test_a_damaged_file_is_a_miss_and_goes(tier):
    tier.put(b"k1", ("x", None))
    _flush(tier, 1)
    p = tier._path(b"k1")
    blob = bytearray(p.read_bytes())
    blob[-1] ^= 0xFF
    p.write_bytes(bytes(blob))
    assert tier.get(b"k1") is None and not p.exists() and tier.stats["bad"] == 1


def test_another_key_with_the_same_name_is_a_miss(tier):
    tier.put(b"k1", ("x", None))
    _flush(tier, 1)
    p = tier._path(b"k1")
    q = tier._path(b"k2")
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_bytes(p.read_bytes())                       # k1's file where k2's would be
    assert tier.get(b"k2") is None


def test_the_code_fingerprint_names_the_methods():
    assert maps_disk.code_fingerprint(("a",)) != maps_disk.code_fingerprint(("b",))
    assert maps_disk.code_fingerprint(("a",)) == maps_disk.code_fingerprint(("a",))


def test_poly_cache_reads_disk_before_building(tier, monkeypatch):
    monkeypatch.setattr(poly_cache, "cheeks_cache_on", lambda: True)
    monkeypatch.setattr(poly_cache, "cheeks_maps_limit", lambda: 16)
    salt = {"v": b"dials-1"}
    poly_cache.set_maps_disk(tier, lambda loop, rel: salt["v"])
    built = []

    def build():
        built.append(1)
        return _maps(), {"delta": (1.5, -0.5)}

    kw = dict(loop_id="clip", frame_i=7, version=(1, 2), rel="t.png", dst={"nose": [10.0, 20.0]},
              wh=(80, 60), scale=1.0, pin_key=("nose-pixel", 0.0, 0.0), tile_hw=(30, 40))
    want_maps, want_extra = poly_cache.cheeks_maps_cached(build=build, **kw)
    _flush(tier, 1)
    poly_cache.reset_mouth_caches()                     # a new process: memory empty
    got_maps, got_extra = poly_cache.cheeks_maps_cached(build=build, **kw)
    assert len(built) == 1 and got_extra == want_extra
    assert all(np.array_equal(a, b) for a, b in zip(got_maps, want_maps))
    poly_cache.reset_mouth_caches()
    salt["v"] = b"dials-2"                              # a changed dial is a different entry
    poly_cache.cheeks_maps_cached(build=build, **kw)
    assert len(built) == 2


def test_off_on_the_house_unless_asked(monkeypatch):
    assert maps_disk.enabled({"HOUSE_MOUTH_MAPS_DISK": "0"}) is False
    assert maps_disk.enabled({"HOUSE_MOUTH_MAPS_DISK": "1"}) is True
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda: True)
    assert maps_disk.enabled({}) is False
