"""HOUSE_MOUTH_FAST_TRACK (2026-10-02, OFF by default): the nose is smoothed with no trail and a dissolve's
outgoing picture travels with her head. Nothing here is fitted to a face or a clip."""
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import mouth_pins as MP  # noqa: E402
from head_frame import ema_nose  # noqa: E402
from poly_cache import nose_ema_track  # noqa: E402

SRC = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def _track(noses):
    return [None if n is None else {"nose": n} for n in noses]


def test_off_by_default_and_each_half_has_a_kill():
    assert re.search(r'MOUTH_FAST_TRACK = os\.environ\.get\("HOUSE_MOUTH_FAST_TRACK", "0"\)', SRC)
    assert 'NOSE_ZERO_LAG = MOUTH_FAST_TRACK and not _off("HOUSE_MOUTH_FAST_TRACK_NOSE")' in SRC
    assert 'XFADE_FOLLOW = MOUTH_FAST_TRACK and not _off("HOUSE_MOUTH_FAST_TRACK_XFADE")' in SRC


def test_the_default_track_is_the_one_way_ema_it_always_was():
    rng = np.random.default_rng(3)
    noses = [list(p) for p in np.cumsum(rng.normal(0, 2, (40, 2)), axis=0) + 300]
    noses[17] = None                                             # a frame with no head restarts the run
    got = nose_ema_track(_track(noses), 0.35)
    prev, prev_i = None, None
    for i, n in enumerate(noses):
        if n is None:
            assert got[i] is None
            continue
        prev = ema_nose(None if (prev_i is None or i - prev_i > 1) else prev, n, 0.35 if not (prev_i is None or i - prev_i > 1) else 0.0)
        prev_i = i
        assert np.array_equal(got[i][1], prev) and np.array_equal(got[i][0], np.asarray(n, dtype=np.float64))


def test_a_moving_head_is_not_trailed():
    v = np.array([4.0, -3.0])                                    # px a frame, any direction
    noses = [list(np.array([300.0, 200.0]) + v * i) for i in range(60)]
    old = nose_ema_track(_track(noses), 0.35)
    new = nose_ema_track(_track(noses), 0.35, zero_lag=True)
    lag = lambda trk, i: float(np.linalg.norm(trk[i][1] - trk[i][0]))
    assert abs(lag(old, 30) - np.linalg.norm(v) * (1 - 0.35) / 0.35) < 0.01     # the trail: (1-k)/k frames of travel
    assert lag(new, 30) < 1e-3                                                   # none in the run's interior
    assert max(lag(new, i) for i in range(60)) < 0.1                             # nor at the run's first and last frame


def test_a_still_head_is_still_quieted():
    rng = np.random.default_rng(5)
    noses = [list(np.array([300.0, 200.0]) + rng.normal(0, 1.0, 2)) for _ in range(400)]
    raw = np.array(noses)
    new = np.array([r[1] for r in nose_ema_track(_track(noses), 0.35, zero_lag=True)])
    old = np.array([r[1] for r in nose_ema_track(_track(noses), 0.35)])
    assert new.std(axis=0).max() < old.std(axis=0).max() < raw.std(axis=0).max()


def test_zero_lag_restarts_at_a_headless_frame_and_passes_k_zero_through():
    noses = [[0.0, 0.0], [10.0, 0.0], None, [100.0, 0.0], [110.0, 0.0]]
    got = nose_ema_track(_track(noses), 0.35, zero_lag=True)
    assert got[2] is None and got[3][1][0] > 90 and got[1][1][0] < 20        # no blend across the gap
    off = nose_ema_track(_track(noses), 0.0, zero_lag=True)
    assert all(np.array_equal(r[0], r[1]) for r in off if r is not None)


def _dot(x, y):
    img = np.zeros((120, 160, 3), dtype=np.uint8)
    img[y - 2:y + 3, x - 2:x + 3] = 255
    a = np.zeros((120, 160), dtype=np.float32)
    a[y - 2:y + 3, x - 2:x + 3] = 1.0
    return img, a


def _pts(dx=0.0, dy=0.0, deg=0.0):
    p = np.array([[60.0, 50.0], [90.0, 50.0], [75.0, 55.0], [75.0, 35.0], [75.0, 80.0]])
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return (p - p.mean(0)) @ np.array([[c, -s], [s, c]]).T + p.mean(0) + (dx, dy)


def test_the_outgoing_picture_travels_with_her_head():
    img, a = _dot(75, 55)                                        # a mark on her lip line
    w, al = MP.carry(("lips5", "clip", 10, _pts()), ("lips5", "clip", 11, _pts(12, 7, 20)), img, a)
    ys, xs = np.nonzero(al > 0.5)
    want = MP.apply(MP.similarity(_pts(), _pts(12, 7, 20)), [75, 55])[0]
    assert abs(xs.mean() - want[0]) < 1.0 and abs(ys.mean() - want[1]) < 1.0
    assert w.shape == img.shape and al.shape == a.shape and w[int(round(want[1])), int(round(want[0]))].max() > 128
    a3 = a[..., None]
    assert MP.carry(("lips5", "clip", 10, _pts()), ("lips5", "clip", 11, _pts(3, 0)), img, a3)[1].shape == a3.shape


def test_what_is_not_a_move_is_left_alone():
    img, a = _dot(75, 55)
    was = ("lips5", "clip", 10, _pts())
    for now in (None, ("lips5", "clip", 10, _pts(9, 9)),          # the same film frame painted again
                ("lips5", "other", 11, _pts(9, 9)),               # another clip
                ("head", "clip", 11, _pts(9, 9)[:3]),             # another kind of points
                ("lips5", "clip", 14, _pts(9, 9))):               # a cut, not a move
        w, al = MP.carry(was, now, img, a)
        assert w is img and al is a
    assert MP.carry(None, was, img, a)[0] is img
