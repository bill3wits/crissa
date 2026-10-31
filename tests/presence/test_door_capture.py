"""door_capture v2: onset walk-back, 12/36/12 selection, pack integrity.

Synthetic 30fps sequences (white blob marching on black — test_motion_vision
pattern). The pack-integrity assertions here ARE the gemma/cosmo regression:
frames must be monotonic in (seq, ts) by construction, orientation stamped,
artifacts must not collide with the f*.jpg frame glob.
"""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from crissa_presence import door_capture as dc

W, H = 320, 180
FPS = 30.0
T0 = 1_700_000_000.0


def _jpeg(img: np.ndarray) -> bytes:
    ok, enc = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    assert ok
    return enc.tobytes()


def _frame_img(blob_x: int | None) -> np.ndarray:
    img = np.zeros((H, W, 3), dtype=np.uint8)
    if blob_x is not None:
        x = max(0, min(W - 60, blob_x))
        img[60:120, x : x + 60] = 255
    return img


def make_frames(
    *, rest_a: float, motion: float, rest_b: float
) -> list[dict]:
    """rest_a s empty, motion s blob marching left→right, rest_b s blob parked."""
    frames = []
    seq = 1000
    t = T0
    n_a, n_m, n_b = (int(s * FPS) for s in (rest_a, motion, rest_b))
    for _ in range(n_a):
        frames.append({"seq": seq, "ts": t, "jpeg": _jpeg(_frame_img(None))})
        seq += 1
        t += 1.0 / FPS
    for i in range(n_m):
        x = int(i * (W - 60) / max(1, n_m - 1))
        frames.append({"seq": seq, "ts": t, "jpeg": _jpeg(_frame_img(x))})
        seq += 1
        t += 1.0 / FPS
    for _ in range(n_b):
        frames.append({"seq": seq, "ts": t, "jpeg": _jpeg(_frame_img(W - 60))})
        seq += 1
        t += 1.0 / FPS
    return frames


@pytest.fixture(scope="module")
def transit():
    """3s rest, 4s march, 4s parked. Trigger fires 0.5s into motion."""
    frames = make_frames(rest_a=3.0, motion=4.0, rest_b=4.0)
    rows = dc.analysis_rows(frames)
    trigger_ts = T0 + 3.0 + 0.5
    return frames, rows, trigger_ts


def test_analysis_rows_cadence_and_energy(transit):
    frames, rows, _ = transit
    # ~5fps over 11s ≈ 55 rows, energies high only during the march
    assert 40 <= len(rows) <= 60
    motion_e = [r["energy"] for r in rows if T0 + 3.2 < r["ts"] < T0 + 6.8]
    rest_e = [r["energy"] for r in rows if r["ts"] < T0 + 2.8]
    assert min(motion_e) > dc.STILL_ENERGY
    assert max(rest_e) <= 0.5


def test_find_onset_lands_one_step_before_motion(transit):
    frames, rows, trigger_ts = transit
    onset_idx = dc.find_onset(rows, trigger_ts)
    onset_ts = frames[onset_idx]["ts"]
    # last resting analysis step: at or before motion start, within one
    # analysis period (0.2s @5fps) of it
    assert onset_ts <= T0 + 3.0 + 0.05
    assert onset_ts >= T0 + 3.0 - 2.5 / dc.ANALYSIS_FPS


def test_find_still_end_after_settle(transit):
    frames, rows, trigger_ts = transit
    still_row = dc.find_still_end(rows, trigger_ts)
    assert still_row is not None
    still_ts = rows[still_row]["ts"]
    # first still step of the quiet run: just after the march ends at T0+7
    assert T0 + 6.9 <= still_ts <= T0 + 7.0 + 3.0 / dc.ANALYSIS_FPS


def test_selection_budget_and_order(transit):
    frames, rows, trigger_ts = transit
    front_start = max(0, dc.find_onset(rows, trigger_ts) - 1)
    still_row = dc.find_still_end(rows, trigger_ts)
    end_anchor = min(rows[still_row]["idx"] + 1, len(frames) - 1)
    sel = dc.select_frames(frames, front_start, end_anchor)

    assert 0 < len(sel) <= dc.TOTAL_MAX
    segs = [s for _, s in sel]
    assert segs.count("front") <= dc.FRONT_N
    assert segs.count("back") <= dc.BACK_N
    assert segs.count("mid") <= dc.MID_MAX
    # seq strictly increasing — the backwards-corpus regression
    seqs = [frames[i]["seq"] for i, _ in sel]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    # anchored: first pick is the onset-1 frame, last is the settle anchor
    assert sel[0][0] == front_start
    assert sel[-1][0] == end_anchor
    # dense front really is ~12fps: covers ~1s from onset
    front_ts = [frames[i]["ts"] for i, s in sel if s == "front"]
    assert front_ts[-1] - front_ts[0] == pytest.approx(
        (dc.FRONT_N - 1) / dc.DENSE_FPS, abs=0.1
    )


def test_selection_caps_long_event():
    frames = make_frames(rest_a=1.0, motion=20.0, rest_b=3.0)
    rows = dc.analysis_rows(frames)
    front_start = max(0, dc.find_onset(rows, T0 + 1.5) - 1)
    still_row = dc.find_still_end(rows, T0 + 1.5)
    assert still_row is not None
    end_anchor = min(rows[still_row]["idx"] + 1, len(frames) - 1)
    sel = dc.select_frames(frames, front_start, end_anchor)
    assert len(sel) <= dc.TOTAL_MAX
    assert [s for _, s in sel].count("mid") <= dc.MID_MAX


def test_filter_selection_door_view_keeps_when_unknown(monkeypatch):
    """Synthetic empty jpegs → view unknown → keep (tests / no ref)."""
    frames = [{"seq": i, "ts": T0 + i / 30.0, "jpeg": b"\xff\xd8" + bytes([i])}
              for i in range(5)]
    sel = [(i, "mid") for i in range(5)]
    monkeypatch.setattr(dc, "_jpeg_is_door_view", lambda j: None)
    kept, meta = dc.filter_selection_door_view(frames, sel)
    assert len(kept) == 5
    assert meta["dropped_non_door"] == 0


def test_filter_selection_demotes_non_door(monkeypatch):
    frames = [{"seq": i, "ts": T0 + i / 30.0, "jpeg": bytes([i])} for i in range(6)]
    sel = [(i, "mid") for i in range(6)]

    def _dv(j: bytes):
        # even indices = door, odd = facefinder / person-in-door
        return (j[0] % 2 == 0) if j else None

    monkeypatch.setattr(dc, "_jpeg_is_door_view", _dv)
    kept, meta = dc.filter_selection_door_view(frames, sel)
    assert [i for i, _ in kept] == [0, 1, 2, 3, 4, 5]
    assert meta["dropped_non_door"] == 0
    assert meta["demoted_non_door"] == 3
    assert meta["door_view_policy"] == "demote"


def test_write_pack_keeps_all_demoted_door_view(monkeypatch, tmp_path):
    """Person-in-doorway frames fail is_door_view — still write the pack."""
    frames = [{"seq": i, "ts": T0 + i / 30.0, "jpeg": b"x" * 100} for i in range(4)]
    sel = [(i, "front") for i in range(4)]
    monkeypatch.setattr(dc, "_jpeg_is_door_view", lambda j: False)
    man = dc.write_pack(
        kind="enter",
        frames=frames,
        selection=sel,
        trigger_seq=1,
        trigger_ts=T0,
        resolve="still",
        suspect_reversed=None,
        face_meta=None,
        body_idx=None,
        slot_key="user",
        wall_sec=0.1,
        events_dir=tmp_path,
    )
    assert man is not None
    assert man["n"] == 4
    assert man["door_view"]["demoted_non_door"] == 4


def test_write_pack_integrity(tmp_path, transit):
    frames, rows, trigger_ts = transit
    front_start = max(0, dc.find_onset(rows, trigger_ts) - 1)
    still_row = dc.find_still_end(rows, trigger_ts)
    end_anchor = min(rows[still_row]["idx"] + 1, len(frames) - 1)
    sel = dc.select_frames(frames, front_start, end_anchor)

    # Synthetic black/blob frames are not door-ref; integrity test disables
    # door_view filter so selection math stays the unit under test.
    man = dc.write_pack(
        kind="enter",
        frames=frames,
        selection=sel,
        trigger_seq=frames[-1]["seq"],
        trigger_ts=trigger_ts,
        resolve="still",
        suspect_reversed=None,
        face_meta={"idx": front_start + 3, "det_score": 0.9, "bbox": [0, 0, 5, 5],
                   "face_px": 100.0, "face_low_res": False, "score": 0.8,
                   "seq": frames[front_start + 3]["seq"]},
        body_idx=end_anchor,
        slot_key="user",
        wall_sec=4.2,
        energies={r["idx"]: r["energy"] for r in rows},
        events_dir=tmp_path,
        filter_door_view=False,
    )
    assert man is not None
    ed = tmp_path / man["id"]
    assert ed.name == f"ev_{int(frames[sel[0][0]]['ts'] * 1000)}"

    # manifest + sidecar identical
    m2 = json.loads((ed / "manifest.json").read_text())
    assert m2 == json.loads((ed / f"{man['id']}.json").read_text())

    # guards: monotonic seq AND ts, orientation stamped, mode stamped
    seqs = [r["seq"] for r in m2["frames"]]
    tss = [r["ts"] for r in m2["frames"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert tss == sorted(tss)
    assert m2["orientation"] == "true_handed"
    assert m2["capture_mode"] == "door_v2"
    assert m2["n"] == len(m2["frames"]) <= dc.TOTAL_MAX

    # frame files match rows; artifacts do NOT enter the frame glob
    frame_files = sorted(p.name for p in ed.glob("f[0-9]*_seq*.jpg"))
    assert frame_files == sorted(r["file"] for r in m2["frames"])
    assert (ed / "entry_face.jpg").exists()
    assert (ed / "entry_body.jpg").exists()
    assert "entry_face.jpg" not in frame_files
    assert m2["artifacts"]["entry_face"]["file"] == "entry_face.jpg"


def test_leave_pack_writes_leave_body_not_face(tmp_path, transit):
    """WP-B: leave packs keep a body crop. FaceID has no back class."""
    frames, rows, trigger_ts = transit
    front_start = max(0, dc.find_onset(rows, trigger_ts) - 1)
    still_row = dc.find_still_end(rows, trigger_ts)
    end_anchor = min(rows[still_row]["idx"] + 1, len(frames) - 1)
    sel = dc.select_frames(frames, front_start, end_anchor)
    man = dc.write_pack(
        kind="leave",
        frames=frames,
        selection=sel,
        trigger_seq=frames[-1]["seq"],
        trigger_ts=trigger_ts,
        resolve="still",
        suspect_reversed=None,
        face_meta={"idx": front_start + 3, "det_score": 0.9, "bbox": [0, 0, 5, 5],
                   "face_px": 100.0, "face_low_res": False, "score": 0.8,
                   "seq": frames[front_start + 3]["seq"]},
        body_idx=end_anchor,
        slot_key="user",
        wall_sec=1.0,
        energies={r["idx"]: r["energy"] for r in rows},
        events_dir=tmp_path,
        filter_door_view=False,
    )
    assert man is not None
    ed = tmp_path / man["id"]
    assert (ed / "leave_body.jpg").exists()
    assert not (ed / "entry_face.jpg").exists()
    assert not (ed / "entry_body.jpg").exists()
    assert man["artifacts"]["leave_body"]["file"] == "leave_body.jpg"

    # dedup: same start refuses a second pack
    assert (
        dc.write_pack(
            kind="enter", frames=frames, selection=sel,
            trigger_seq=None, trigger_ts=trigger_ts, resolve="still",
            suspect_reversed=None, face_meta=None, body_idx=None,
            slot_key=None, wall_sec=0.1, events_dir=tmp_path,
            filter_door_view=False,
        )
        is None
    )


def test_capture_kind_for_event():
    assert dc.capture_kind_for_event({"kind": "enter"}) == "enter"
    assert dc.capture_kind_for_event({"kind": "leave"}) == "leave"
    assert dc.capture_kind_for_event({"kind": "pending_crossing"}) == "motion"
    assert (
        dc.capture_kind_for_event(
            {"kind": "transit_dedup_noop", "source": "door_live_resight"}
        )
        == "motion"
    )
    assert dc.capture_kind_for_event({"kind": "crossing_cancelled"}) is None
    assert (
        dc.capture_kind_for_event(
            {"kind": "leave", "leave_match": {"method": "ghost_recovery"}}
        )
        is None
    )
    assert dc.capture_kind_for_event({"kind": "door_live_gauge"}) is None
    assert dc.capture_kind_for_event(None) is None


def test_write_pack_motion_keeps_demoted_door_view(monkeypatch, tmp_path):
    frames = [{"seq": i, "ts": T0 + i / 30.0, "jpeg": b"x" * 100} for i in range(4)]
    sel = [(i, "front") for i in range(4)]
    monkeypatch.setattr(dc, "_jpeg_is_door_view", lambda j: False)
    man = dc.write_pack(
        kind="motion",
        frames=frames,
        selection=sel,
        trigger_seq=1,
        trigger_ts=T0,
        resolve="still",
        suspect_reversed=None,
        face_meta=None,
        body_idx=None,
        slot_key=None,
        wall_sec=0.1,
        events_dir=tmp_path,
    )
    assert man is not None
    assert man["n"] == 4
    assert man["door_view"]["demoted_non_door"] == 4


def test_clamp_onset_bounds_walkback():
    # first live transit regression: occupied room never rests, fallback
    # anchored the front 19.8s before the trigger
    frames = [{"seq": i, "ts": T0 + i / 30.0, "jpeg": b""} for i in range(600)]
    trigger_ts = T0 + 19.0
    clamped = dc.clamp_onset(frames, 0, trigger_ts)
    assert frames[clamped]["ts"] >= trigger_ts - dc.MAX_PREROLL_SEC - 1 / 30.0
    # a genuine near onset is untouched
    assert dc.clamp_onset(frames, 540, trigger_ts) == 540


def test_thin_follow_protects_dense_front():
    # first live transit regression: follow-thinning collapsed the dense
    # front to 4fps before selection ran
    frames = [{"seq": i, "ts": T0 + i / 30.0} for i in range(900)]  # 30s
    protect = T0 + dc.FRONT_PROTECT_SEC
    out = dc.thin_follow(frames, protect_until_ts=protect)
    front = [f for f in out if f["ts"] <= protect]
    assert len(front) == int(dc.FRONT_PROTECT_SEC * 30) + 1  # full 30fps kept
    mid = [f for f in out if protect < f["ts"] < T0 + 26.0]
    assert len(mid) <= (26.0 - dc.FRONT_PROTECT_SEC) * dc.MID_FPS + 2  # ~4fps


def _gray_with_blob(x0: int) -> np.ndarray:
    g = np.zeros((90, 160), dtype=np.uint8)
    g[30:60, x0 : x0 + 30] = 255
    return g


def test_direction_check(monkeypatch):
    monkeypatch.setattr(dc, "ROI_ENV", "0.0,0.0,0.3,1.0")  # door = left 30%
    ts = [T0 + i * 0.2 for i in range(4)]
    empty = np.zeros((90, 160), dtype=np.uint8)
    door_then_in = [
        {"ts": ts[0], "energy": 0.0, "gray": empty},
        {"ts": ts[1], "energy": 20.0, "gray": _gray_with_blob(5)},   # door
        {"ts": ts[2], "energy": 20.0, "gray": _gray_with_blob(100)},  # interior
        {"ts": ts[3], "energy": 0.0, "gray": _gray_with_blob(100)},
    ]
    in_then_door = [
        {"ts": ts[0], "energy": 0.0, "gray": empty},
        {"ts": ts[1], "energy": 20.0, "gray": _gray_with_blob(100)},
        {"ts": ts[2], "energy": 20.0, "gray": _gray_with_blob(5)},
        {"ts": ts[3], "energy": 0.0, "gray": _gray_with_blob(5)},
    ]
    assert dc.direction_check(door_then_in, "enter") is False
    assert dc.direction_check(in_then_door, "enter") is True
    # leave expects the opposite order
    assert dc.direction_check(in_then_door, "leave") is False

    monkeypatch.setattr(dc, "ROI_ENV", "")
    assert dc.direction_check(door_then_in, "enter") is None


def test_find_onset_skips_post_transit_quiet():
    """Leave-shaped timeline: crossing motion, then ~2.5s of settled quiet,
    THEN the trigger (leave confirm). Onset must anchor before the crossing,
    not on the post-transit quiet (ev_1785848993188 regression)."""
    frames = make_frames(rest_a=3.0, motion=2.0, rest_b=4.0)
    rows = dc.analysis_rows(frames)
    trigger_ts = T0 + 3.0 + 2.0 + 2.5  # confirm fires 2.5s after motion ends
    onset_idx = dc.find_onset(rows, trigger_ts)
    onset_ts = frames[onset_idx]["ts"]
    # anchored at the rest BEFORE the crossing (T0+3.0), not the quiet at T0+5+
    assert onset_ts <= T0 + 3.0 + 0.05
    assert onset_ts >= T0 + 3.0 - 2.5 / dc.ANALYSIS_FPS


def test_find_onset_quiet_only_window_unchanged():
    """No motion anywhere near the trigger: fall back to the quiet row nearest
    the trigger (old behavior), not a walk into ancient history."""
    frames = make_frames(rest_a=2.0, motion=1.0, rest_b=10.0)
    rows = dc.analysis_rows(frames)
    trigger_ts = T0 + 12.0  # 9s after motion ended — outside SKIP_QUIET_SEC
    onset_idx = dc.find_onset(rows, trigger_ts)
    assert frames[onset_idx]["ts"] >= trigger_ts - dc.SKIP_QUIET_SEC - 1.0


def test_body_signature_and_dist():
    """Shadow matcher: signature extractable from synthetic crossing, self-
    distance 0, and a different-colored body measurably farther."""
    def blob_frames(color):
        frames = []
        seq, t = 1, T0
        for i in range(30):  # 1s: blob marches
            img = np.zeros((H, W, 3), dtype=np.uint8)
            if 3 <= i < 27:
                x = 20 + i * 8
                img[40:160, x : x + 50] = color
            frames.append({"seq": seq, "ts": t, "jpeg": _jpeg(img)})
            seq += 1
            t += 1 / 30.0
        return frames

    red = blob_frames((40, 40, 220))
    sel = list(range(len(red)))
    sig_r = dc.body_signature(red, sel)
    assert sig_r is not None and sig_r["n_body_frames"] >= 5
    assert dc.sig_dist(sig_r, sig_r) == 0.0
    sig_b = dc.body_signature(blob_frames((220, 40, 40)), sel)
    assert sig_b is not None
    assert dc.sig_dist(sig_r, sig_b) > 0.15


def test_sharp_beats_big_in_face_scoring():
    """wanda's 06:36 entry, measured. The old area x confidence rule chose a
    166x208 face blurred to sharpness 66.6 (sim 0.217) over a 68x95 face at
    sharpness 201.6 (sim 0.586). Blur destroys the embedding; area does not
    rescue it."""
    from crissa_presence.entry_face import SHARP_FLOOR, _score

    blurred_big = _score(166 * 208, 0.670, 66.6)
    sharp_small = _score(68 * 95, 0.829, 201.6)
    assert sharp_small > blurred_big, "a sharp face must outrank a smeared one"
    assert SHARP_FLOOR > 0


def test_a_blurred_face_still_beats_no_face():
    """The demotion is a factor, not a veto — someone at the door with a bad
    crop is still someone at the door."""
    from crissa_presence.entry_face import _score

    assert _score(166 * 208, 0.670, 66.6) > 0


def test_door_view_is_a_bonus_not_a_tier():
    """Two buckets returned `best_door or best_unknown`, so ANY door-view frame
    beat every other frame regardless of score. is_door_view only reads True
    once the subject fills the doorway — exactly when they are moving fastest —
    so the preference was selecting for motion blur."""
    from crissa_presence.entry_face import DOOR_VIEW_BONUS

    assert 1.0 < DOOR_VIEW_BONUS < 3.0, "a tie-breaker, not a trump card"
    blurred_doorview = 2020 * DOOR_VIEW_BONUS
    sharp_offdoor = 9991
    assert sharp_offdoor > blurred_doorview
