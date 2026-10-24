"""Per-station head scale under MediaPipe (the User 2026-09-28: Crissa's close-up mouth landed ~5 % large
and low). head_frame.station_scales / scale_about_nose and pose-head-frame's clip_station_scale."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import head_frame as HF

ROOT = Path(__file__).resolve().parents[1]
REC = {"s": 100.0, "R": [1, 0, 0, 0, 1, 0, 0, 0, 1], "t": [200.0, 100.0, 10.0], "pose": [1, 2, 3],
       "u": [100.0, 0.0], "v": [0.0, 100.0], "A": [200.0, 180.0], "nose": [200.0, 120.0],
       "chin": [200.0, 220.0], "open": 1.0, "cheek_l": [150.0, 140.0], "cheek_r": [250.0, 140.0],
       "mouth": [170.0, 170.0, 60.0, 20.0]}


def test_scale_about_nose_moves_points_toward_the_nose_and_keeps_ratios():
    out = HF.scale_about_nose(REC, 0.95)
    assert out["nose"] == REC["nose"] and out["s"] == 95.0 and out["open"] == 1.0
    assert out["A"] == [200.0, 177.0] and out["chin"] == [200.0, 215.0]
    assert out["cheek_l"] == [152.5, 139.0] and out["u"] == [95.0, 0.0]
    x, y, w, h = out["mouth"]
    assert (w, h) == (57.0, 19.0) and abs((y + h / 2) - (120 + 0.95 * 60)) < 1e-6   # smaller and higher
    assert out["station_scale"] == 0.95 and REC["s"] == 100.0                       # input untouched
    assert HF.scale_about_nose(REC, 1.0) is REC and HF.scale_about_nose(None, 0.95) is None


def test_station_scales_only_under_mediapipe_and_switch(monkeypatch):
    monkeypatch.setenv("HOUSE_POSE_DIR", "/x/crissa-poses")
    monkeypatch.delenv("HOUSE_FACE_MEANSHAPE", raising=False)
    HF.mp_meanshape_path.cache_clear()                   # one process = one library, so it is cached
    monkeypatch.setenv("HOUSE_FACE_ENGINE", "insightface")
    assert HF.station_scales() == {}
    monkeypatch.setenv("HOUSE_FACE_ENGINE", "mediapipe")
    assert HF.station_scales() == {"close": 0.92}
    monkeypatch.setenv("HOUSE_FACE_STATION_SCALE", "0")
    assert HF.station_scales() == {}
    monkeypatch.delenv("HOUSE_FACE_STATION_SCALE")
    monkeypatch.setenv("HOUSE_POSE_DIR", "/x/lila-poses")
    HF.mp_meanshape_path.cache_clear()
    assert HF.station_scales() == {}
    HF.mp_meanshape_path.cache_clear()


def test_the_meanshape_points_are_unchanged_by_the_table():
    import subprocess
    d = json.loads((ROOT / "lib" / "data" / "meanshape_68_mp_crissa.json").read_text())
    # The baseline is the version that ADDED the file (before any station_scale table), found by git rather
    # than pinned: the 10-01 history rewrite renumbered every commit and orphaned the old pin (ae082b95).
    path = "lib/data/meanshape_68_mp_crissa.json"
    added = subprocess.run(["git", "-C", str(ROOT), "log", "--diff-filter=A", "--format=%H", "--", path],
                           capture_output=True, text=True, check=True).stdout.split()[-1]
    before = json.loads(subprocess.run(["git", "-C", str(ROOT), "show", f"{added}:{path}"],
                                       capture_output=True, text=True, check=True).stdout)
    assert "station_scale" not in before
    assert d["points"] == before["points"] and d["scale_from_old_data"] == before["scale_from_old_data"]


def test_clip_station_scale_by_row_and_guest_faces(tmp_path, monkeypatch):
    pose = tmp_path / "crissa-poses"
    pose.mkdir()
    (pose / "inventory.json").write_text(json.dumps({"clips": {
        "a": {"file": "clip-up-close-harvest-4.mp4", "pose_class": "down_close"},
        "b": {"file": "clip-work.mp4", "pose_class": "down"},
        "c": {"file": "clip-lila-ob-close-door.mp4", "pose_class": "down_close"}}}))
    monkeypatch.setenv("HOUSE_POSE_DIR", str(pose))
    monkeypatch.setenv("HOUSE_FACE_ENGINE", "mediapipe")
    spec = importlib.util.spec_from_file_location("phf_for_test", ROOT / "scripts" / "pose-head-frame.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "POSE", pose)
    monkeypatch.setattr(m, "station_scales", lambda: {"close": 0.95})
    assert m.clip_station_scale("clip-up-close-harvest-4") == 0.95
    assert m.clip_station_scale("clip-work") == 1.0
    assert m.clip_station_scale("clip-lila-ob-close-door") == 1.0


# ---- the head-track smoother (MediaPipe keeps no temporal filter; the User 09-28) -------------------
def _track(noses):
    return [{"nose": [p[0], p[1]], "s": 100.0, "open": 1.0, "R": [1, 0, 0, 0, 1, 0, 0, 0, 1], "pose": [0, 0, 0]}
            if p is not None else None for p in noses]


def test_smoother_cuts_jitter_without_lag():
    import numpy as np
    rng = np.random.default_rng(0)
    t = np.arange(120)
    true = np.stack([200 + 40 * np.sin(t / 6.0), 150 + 0 * t], 1)      # a fast sweep, up to 6.7 px/f
    noisy = true + rng.normal(0, 1.0, true.shape)
    sm = HF.smooth_track(_track(noisy.tolist()), 5)
    got = np.array([r["nose"] for r in sm])
    jerk = lambda n: np.median(np.linalg.norm(n[:-2] - 2 * n[1:-1] + n[2:], axis=1))
    assert jerk(got) < 0.6 * jerk(noisy)
    err = np.linalg.norm(got - true, axis=1)[3:-3]
    assert err.mean() < np.linalg.norm(noisy - true, axis=1)[3:-3].mean()
    vt, vg = np.diff(true[:, 0]), np.diff(got[:, 0])
    corr = [np.corrcoef(vt[max(0, -L): len(vt) - max(0, L)], vg[max(0, L): len(vg) - max(0, -L)])[0, 1] for L in (-2, -1, 0, 1, 2)]
    assert int(np.argmax(corr)) == 2                                    # best lag is 0 frames
    R = np.array(sm[10]["R"]).reshape(3, 3)
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-6)


def test_smoother_keeps_gaps_and_short_runs_and_never_crosses_a_cut():
    noses = [(10, 0), (10, 0), None, (10, 0), (11, 0), (10, 0), (11, 0), (10, 0), (10, 0),
             (40, 0), (40, 0), (40, 0), (41, 0), (40, 0)]
    tr = _track(noses)
    assert HF.track_cuts(tr) == {9}
    sm = HF.smooth_track(tr, 5)
    assert sm[2] is None and sm[0]["nose"] == [10, 0]                   # a 2-frame run stays raw
    assert all(r["nose"][0] < 12 for r in sm[3:9]) and all(r["nose"][0] > 39 for r in sm[9:])
    assert tr[4]["nose"] == [11, 0]                                     # input untouched


def test_small_tracker_steps_are_not_cuts():
    tr = _track([(0, 0), (0.3, 0), (0.1, 0), (3.5, 0), (3.6, 0), (3.4, 0)])
    assert HF.track_cuts(tr) == set()


def test_smooth_window_switch(monkeypatch):
    monkeypatch.setenv("HOUSE_FACE_ENGINE", "insightface")
    assert HF.track_smooth_window() == 0
    monkeypatch.setenv("HOUSE_FACE_ENGINE", "mediapipe")
    assert HF.track_smooth_window() == 5
    monkeypatch.setenv("HOUSE_FACE_SMOOTH_WIN", "7")
    assert HF.track_smooth_window() == 7
    monkeypatch.setenv("HOUSE_FACE_SMOOTH_WIN", "4")
    assert HF.track_smooth_window() == 0
    monkeypatch.setenv("HOUSE_FACE_SMOOTH", "0")
    assert HF.track_smooth_window() == 0
