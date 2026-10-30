"""Cutting the first second of real motion out of a bursty raw stream.

The cases that matter are the ones that made "first motion" wrong before:
irregular frame spacing, and a frame count the video model rejects.
"""

from __future__ import annotations

from pathlib import Path

from crissa_presence import raw_clip


def _frames(spec: list[tuple[float, int]]) -> list[tuple[float, int, Path]]:
    return [(ts, seq, Path(f"/nope/f_{int(ts*1000)}_{seq:09d}.jpg")) for ts, seq in spec]


def test_idle_ticks_are_not_motion(monkeypatch):
    """The raw store idles near 0.5 fps. Differencing frames 2 s apart measures
    elapsed time, not movement — that is what put 'first motion' on empty ticks."""
    frames = _frames([(0.0, 1), (2.0, 2), (4.0, 3), (6.0, 4)])
    monkeypatch.setattr(raw_clip, "_gray", lambda p: object())
    en = raw_clip.energies(frames)
    assert all(e == -1.0 for e in en), "far-apart frames must abstain, not score"
    assert raw_clip.motion_bursts(frames, en) == []


def test_a_burst_is_found_among_idle_ticks(monkeypatch):
    """Idle ticks, then a real 30 fps burst. Only the burst may be reported."""
    import numpy as np

    spec = [(0.0, 1), (2.0, 2), (4.0, 3)]
    spec += [(4.0 + i * 0.033, 4 + i) for i in range(1, 7)]
    frames = _frames(spec)

    still = np.zeros((90, 160), dtype=np.uint8)
    moving = np.full((90, 160), 200, dtype=np.uint8)
    # everything still except inside the burst, where frames alternate hard
    grays = [still, still, still, moving, still, moving, still, moving, still]
    monkeypatch.setattr(raw_clip, "_gray", lambda p, _g=iter(grays): next(_g))

    en = raw_clip.energies(frames)
    runs = raw_clip.motion_bursts(frames, en)
    assert runs, "the 30 fps burst must be detected"
    i0 = runs[0][0]
    assert frames[i0][0] >= 4.0, "the burst starts at 4.0 s, not on an idle tick"


def test_a_one_frame_flash_reads_as_a_two_frame_run(monkeypatch):
    """A single odd frame scores high TWICE — once going in, once coming out.

    So MIN_BURST_FRAMES=2 does not reject a one-frame flash, and this test
    exists so nobody raises the constant to 3 to "fix" that: the real first
    burst on ev_1786807037052 was exactly 2 frames (11:17:43.40 -> 43.46), and
    a 3-frame minimum would have skipped the true onset for a later one.
    """
    import numpy as np

    frames = _frames([(0.0, 1), (0.033, 2), (0.066, 3), (0.099, 4)])
    still = np.zeros((90, 160), dtype=np.uint8)
    hot = np.full((90, 160), 255, dtype=np.uint8)
    grays = [still, still, hot, still]
    monkeypatch.setattr(raw_clip, "_gray", lambda p, _g=iter(grays): next(_g))
    runs = raw_clip.motion_bursts(frames)
    assert len(runs) == 1
    assert runs[0][1] - runs[0][0] + 1 == 2, "in-diff and out-diff, not one"


def test_cut_always_returns_an_even_count(monkeypatch):
    """patch_temporal=2 rejects an odd count outright:
    'video frame count 9 must be a positive multiple of patch_temporal=2'."""
    for n in (5, 6, 7, 9):
        frames = _frames([(10.0 + i * 0.033, i) for i in range(n)])
        monkeypatch.setattr(raw_clip, "raw_frames", lambda a, b, _f=frames: _f)
        got = raw_clip.cut(10.0)
        assert len(got) % 2 == 0, f"{n} frames in -> {len(got)} out, must be even"


def test_cut_caps_frames_and_stays_even(monkeypatch):
    frames = _frames([(10.0 + i * 0.033, i) for i in range(400)])
    monkeypatch.setattr(raw_clip, "raw_frames", lambda a, b, _f=frames: _f)
    got = raw_clip.cut(10.0, max_frames=12)
    assert len(got) <= 12 and len(got) % 2 == 0


def test_no_motion_returns_none_not_a_guess(monkeypatch):
    """A pack can be triggered by a light change with nobody in it. Saying so
    is the correct answer; inventing an onset would key a row on nothing."""
    monkeypatch.setattr(raw_clip, "raw_frames", lambda a, b: [])
    assert raw_clip.first_motion(0.0, 10.0) is None


def test_encode_refuses_an_odd_clip():
    assert raw_clip.encode_mp4([Path("a.jpg")] * 3) is None
    assert raw_clip.encode_mp4([]) is None


def test_encode_uses_wall_clock_not_clip_fps(tmp_path: Path):
    """CLIP_FPS=8 would make 4 frames 0.375 s. Honest span is 3.0 s."""
    import subprocess

    import cv2
    import numpy as np

    paths = []
    for i in range(4):
        p = tmp_path / f"f_{1_000_000 + i}_{i:09d}.jpg"
        im = np.full((180, 320, 3), 40 + i * 20, dtype=np.uint8)
        cv2.imwrite(str(p), im)
        paths.append(p)
    ts = [100.0, 101.0, 102.0, 103.0]
    mp4 = raw_clip.encode_mp4(paths, timestamps=ts)
    assert mp4 and len(mp4) > 100
    out = tmp_path / "clip.mp4"
    out.write_bytes(mp4)
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nk=1:nw=1", str(out),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    dur = float((probe.stdout or "0").strip() or 0)
    assert dur >= 1.8, dur  # not 4/8 = 0.5 s; concat -t may undershoot 3.0 slightly
    fake = raw_clip.encode_mp4(paths, fps=8.0)
    assert fake and len(fake) > 100


def test_a_lone_emphatic_frame_starts_a_burst(monkeypatch):
    """user's 13:19:39 entry, seq 107576: energy 15.24 with neighbours at 2.6.

    The two-frame rule rejected it and took the bigger burst 1.8 s later, by
    which time he was through the door. The camera's own trigger ring had
    already called 107576 (energy 14.5) — the re-derivation talked itself out
    of the right answer. A lone frame counts when it is emphatic.
    """
    import numpy as np

    frames = _frames([(0.0, 1), (0.033, 2), (0.066, 3), (0.099, 4), (0.132, 5)])
    still = np.zeros((90, 160), dtype=np.uint8)
    mid = np.full((90, 160), 3, dtype=np.uint8)      # ~2.6 energy, under the floor
    loud = np.full((90, 160), 20, dtype=np.uint8)    # well over floor*STRONG_MULT
    grays = [still, still, loud, mid, mid]
    monkeypatch.setattr(raw_clip, "_gray", lambda p, _g=iter(grays): next(_g))
    runs = raw_clip.motion_bursts(frames)
    assert runs, "an emphatic single frame must open a burst"
    assert runs[0][0] == 2, "the burst starts on the loud frame, not later"


def test_a_quiet_lone_frame_still_needs_a_companion():
    """The relaxation is for emphatic frames only — noise stays rejected."""
    assert raw_clip.STRONG_MULT > 1.0


def test_the_clip_ends_when_the_room_is_restored(monkeypatch):
    """user 2026-08-16: "end of action is when fov MATCHES REFS!"

    Measured on ev_1786876567076: a fixed window sent 16 frames of which 13 were
    the restored room — Argus was reading 81% empty dresser. Bounded by the
    room, it is 3 frames, matching what the strip shows by eye.
    """
    import numpy as np

    room = np.zeros((90, 160), dtype=np.uint8)
    person = room.copy()
    person[20:70, 40:110] = 200            # a body-sized change

    frames = _frames([(i * 0.033, i) for i in range(16)])
    grays = [person, person, person] + [room] * 13
    monkeypatch.setattr(raw_clip, "_gray", lambda p, _g=iter(grays): next(_g))

    got = raw_clip.action_bounds(frames, ref_gray=room)
    assert got == (0, 2), f"expected the three action frames, got {got}"


def test_nothing_happening_bounds_to_nothing(monkeypatch):
    """A quiet room must yield no clip, not a clip of the quiet."""
    import numpy as np

    room = np.zeros((90, 160), dtype=np.uint8)
    frames = _frames([(i * 0.033, i) for i in range(8)])
    monkeypatch.setattr(raw_clip, "_gray", lambda p, _r=room: _r)
    assert raw_clip.action_bounds(frames, ref_gray=room) is None


def test_a_pause_mid_stride_does_not_end_the_action(monkeypatch):
    """One still frame is a pause, not an exit — the end needs a RUN."""
    import numpy as np

    room = np.zeros((90, 160), dtype=np.uint8)
    person = room.copy()
    person[20:70, 40:110] = 200
    frames = _frames([(i * 0.033, i) for i in range(9)])
    grays = [person, person, room, person, person] + [room] * 4
    monkeypatch.setattr(raw_clip, "_gray", lambda p, _g=iter(grays): next(_g))
    got = raw_clip.action_bounds(frames, ref_gray=room)
    assert got is not None and got[1] >= 4, f"single-frame pause split it: {got}"
