"""The first second of motion, cut from raw and handed to Glimmer as video.

Why this exists — measured 2026-08-15 on ev_1786807037052, a real entry:

  The pack spans 76.7 s and the crossing is 0.2 s of it: 14 moving frames out
  of 323. Argus was sent 5 stills spread evenly across the whole 76.7 s, about
  25 s apart, so ZERO of the two motion runs contained a frame it ever saw. It
  replied "no door-plane crossing is visible", which was correct about its
  input and useless about the room. The model was never the problem.

  Same event, 14 raw frames across 2.4 s at the first motion, sent as VIDEO:

      {"event":"enter", "clothes":"shirtless with black shorts, holding a
       green towel", "sex":"M", "why":"The man steps forward from the hallway
       through the open doorway into the room, crossing the door plane toward
       the camera, while a dog remains in the hallway behind him."}

  3.3 s, 1194 prompt tokens.

Two things make this work, and both are easy to get wrong:

  * ENERGY MUST BE GAP-NORMALISED. The raw store idles near 0.5 fps and bursts
    to 30 fps when something moves. A plain frame-to-frame difference across a
    2 s idle gap looks exactly like motion — which is why "first motion" kept
    landing on regular 2 s ticks where nothing happened at all. Only frames
    genuinely adjacent in time may be differenced.

  * THE FRAME COUNT MUST BE EVEN. Glimmer has patch_temporal=2 and rejects an
    odd count outright: "video frame count 9 must be a positive multiple of
    patch_temporal=2".

This module reads raw and returns bytes. It writes nothing, decides nothing
about identity, and never touches presence state.
"""

from __future__ import annotations

from . import paths as _paths

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

RAW_ROOT = Path(os.environ.get("SECURITY_RAW", str(_paths.RECORDS / "raw")))

# Frames further apart than this are not comparable — differencing them
# measures elapsed time, not movement. 200 ms is generous against the 33 ms
# burst rate and tight against the ~2 s idle rate.
ADJACENT_SEC = float(os.environ.get("RAIL_RAWCLIP_ADJACENT_SEC", "0.20"))
# A real event usually moves for more than one frame, so two consecutive frames
# over the floor is the normal bar. But the FIRST frame of a walk-in is often a
# lone spike, and requiring two loses it:
#
#   the User's 13:19:39 entry — 107576 scored 15.24 and its neighbours 2.6, so the
#   two-frame rule rejected it and took the bigger burst at 107585, 1.8 s later.
#   By then he was through the door; at 107574 he is mid-stride in the hallway
#   with his face square to the camera. The camera's own trigger ring had it
#   right (seq 107576, energy 14.5) and the re-derivation talked itself out of
#   it — which is the argument for reading the ring instead of re-deriving.
#
# So a single frame counts when it is emphatic. STRONG_MULT is the bar for a
# lone frame; anything quieter still needs a companion.
MIN_BURST_FRAMES = int(os.environ.get("RAIL_RAWCLIP_MIN_BURST", "2"))
ENERGY_FLOOR = float(os.environ.get("RAIL_RAWCLIP_ENERGY_FLOOR", "4.0"))
STRONG_MULT = float(os.environ.get("RAIL_RAWCLIP_STRONG_MULT", "3.0"))
# How much to keep. the User 2026-08-15: "1 sec sb enough". The crossing measured
# 0.2 s, so a second is four crossings wide; the extra is context for
# direction, which is the thing a single second of motion cannot show.
CLIP_SEC = float(os.environ.get("RAIL_RAWCLIP_SEC", "2.5"))
# Start slightly before the first moving frame so the clip opens on the empty
# doorway. "Empty then occupied" is how direction reads.
LEAD_SEC = float(os.environ.get("RAIL_RAWCLIP_LEAD_SEC", "0.3"))
MAX_FRAMES = int(os.environ.get("RAIL_RAWCLIP_MAX_FRAMES", "48"))
CLIP_FPS = float(os.environ.get("RAIL_RAWCLIP_FPS", "8"))
CLIP_WIDTH = int(os.environ.get("RAIL_RAWCLIP_WIDTH", "768"))

_NAME = re.compile(r"^f_(\d+)_(\d+)\.jpg$")


def raw_frames(t0: float, t1: float) -> list[tuple[float, int, Path]]:
    """Every raw frame in [t0, t1], oldest first, as (ts, seq, path).

    Raw is laid out DAY/HHMM/f_<ms>_<seq>.jpg, so only the minute directories
    the window touches are scanned — an hour of raw is ~15k files.
    """
    import time as _time

    if t1 < t0:
        t0, t1 = t1, t0
    out: list[tuple[float, int, Path]] = []
    minute = 60.0
    t = t0 - (t0 % minute)
    seen: set[Path] = set()
    while t <= t1 + minute:
        lt = _time.localtime(t)
        d = RAW_ROOT / _time.strftime("%Y%m%d", lt) / _time.strftime("%H%M", lt)
        t += minute
        if d in seen or not d.is_dir():
            continue
        seen.add(d)
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            m = _NAME.match(p.name)
            if not m:
                continue
            ts = int(m.group(1)) / 1000.0
            if t0 <= ts <= t1:
                out.append((ts, int(m.group(2)), p))
    out.sort()
    return out


def _gray(path: Path) -> Any:
    import cv2
    import numpy as np

    try:
        buf = np.frombuffer(path.read_bytes(), np.uint8)
    except OSError:
        return None
    img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    return cv2.resize(img, (160, 90))


def energies(frames: list[tuple[float, int, Path]]) -> list[float]:
    """Per-frame motion energy, or -1.0 where it cannot honestly be measured.

    -1.0 means "the previous frame is too far away in time to compare with" —
    not "still". Treating that as zero would be a lie in the safe direction;
    treating it as motion is the bug this module exists to avoid.
    """
    import numpy as np

    out: list[float] = []
    prev_g = None
    prev_ts = None
    for ts, _seq, p in frames:
        g = _gray(p)
        if g is None:
            out.append(-1.0)
            continue
        if prev_g is None or prev_ts is None or ts - prev_ts > ADJACENT_SEC:
            out.append(-1.0)
        else:
            d = np.abs(g.astype(np.int16) - prev_g.astype(np.int16))
            out.append(float(d.mean()))
        prev_g = g
        prev_ts = ts
    return out


def motion_bursts(
    frames: list[tuple[float, int, Path]],
    en: list[float] | None = None,
) -> list[tuple[int, int, float]]:
    """Runs of >=MIN_BURST_FRAMES consecutive moving frames, as (i0, i1, peak)."""
    import numpy as np

    if not frames:
        return []
    en = energies(frames) if en is None else en
    valid = [e for e in en if e >= 0]
    if not valid:
        return []
    # A fixed floor, deliberately, with no adaptive term.
    #
    # An adaptive threshold wants a quiet baseline to sit above, and there
    # isn't one here: idle frames are seconds apart so they abstain (-1), and
    # every SCORED pair therefore comes from inside a burst. A median taken
    # over those is a median of motion, and scaling it up thresholds away the
    # very thing being looked for. The camera only stores dense frames when it
    # saw something, so "adjacent and above the floor" already means moving.
    thr = ENERGY_FLOOR
    strong = thr * STRONG_MULT
    runs: list[tuple[int, int, float]] = []
    start = None
    for i, e in enumerate(en):
        if e >= thr:
            if start is None:
                start = i
        elif start is not None:
            n = i - start
            if n >= MIN_BURST_FRAMES or max(en[start:i]) >= strong:
                runs.append((start, i - 1, max(en[start:i])))
            start = None
    if start is not None:
        n = len(en) - start
        if n >= MIN_BURST_FRAMES or max(en[start:]) >= strong:
            runs.append((start, len(en) - 1, max(en[start:])))
    return runs


def first_motion(t0: float, t1: float) -> dict[str, Any] | None:
    """The first real movement in [t0, t1]. None when nothing moved.

    None is a legitimate answer — a pack can be triggered by light change or
    camera pan with no person in it — and callers must not substitute a guess.
    """
    frames = raw_frames(t0, t1)
    if not frames:
        return None
    en = energies(frames)
    runs = motion_bursts(frames, en)
    if not runs:
        return None
    i0, i1, peak = runs[0]
    return {
        "ts": frames[i0][0],
        "seq": frames[i0][1],
        "end_ts": frames[i1][0],
        "peak": round(peak, 2),
        "n_bursts": len(runs),
        "n_raw": len(frames),
    }


# Fraction of the frame that must differ from the room-as-it-was for the frame
# to count as action.
#
# 0.5%, not 2%. The first measurement (ev_1786876567076) had a person CLOSE to
# the camera reading 11-13.8% against 0.0% for the restored room, and 2% looked
# like a comfortable middle. It was not: someone across the room reads far less
# than someone in the lens. Traced on ev_1786879945476, where the clip stopped
# twelve seconds before the exit:
#
#   seq 412595   4.3%   moving, far from the camera
#   seq 412599   1.6%   STILL THERE — but under a 2% floor, so "restored"
#   seq 412617   0.0%   what an empty room actually reads
#   seq 412626  33.6%   the real exit, finally
#
# Empty is 0.0%, not "small". Anything above the noise floor is somebody.
ACTION_FG = float(os.environ.get("RAIL_RAWCLIP_ACTION_FG", "0.005"))
# Frames of restored room to see before calling the action over — one still
# frame can be a pause mid-stride.
ACTION_END_RUN = int(os.environ.get("RAIL_RAWCLIP_ACTION_END_RUN", "3"))


def action_window(
    trigger_ts: float,
    *,
    window_before: float = 8.0,
    window_after: float = 4.0,
) -> list[tuple[float, int, Path]]:
    """The frames of this crossing — the SAME set for every witness.

    the User 2026-08-16, reading the evidence page: *"the movie got an arm and the
    faceid no face got the exit. Something wrong here."* It was: Argus received
    8 action-bounded frames while FaceID scanned a fixed count of 16, so the
    two were judging different footage. FaceID's window ran on past the action
    into frames Argus never saw — a back at the dresser — which is how one
    witness reported in-room motion and the other found no face at all.

    One trigger, one set of frames. Both callers compute it independently from
    the same inputs rather than one handing it to the other, so neither can gate
    the other — that separation is the point of the fan-out.

    Empty when nothing ever differs from the room.
    """
    ref = still_ref(trigger_ts)
    span = raw_frames(trigger_ts - window_before, trigger_ts + window_after)
    if ref is None or not span:
        return []
    b = action_bounds(span, ref_gray=ref)
    if not b:
        return []
    i0, i1 = b
    return span[i0:i1 + 1]


def still_ref(before_ts: float, *, look_back: float = 90.0) -> Any:
    """An empty-door reference, taken from the cache, minutes old at most.

    the User 2026-08-16: *"take any REF of empty door literally millions of them in
    cache"* — and he is right, the raw store holds an hour of frames and the
    overwhelming majority are an empty doorway. There is no need to curate a
    reference set and no need to keep one current; pick a recent one.

    "Recent" is the whole point. The curated set was 28 frames from a single
    7-minute window on a previous afternoon, and against a pre-dawn frame an
    EMPTY room measured 31-43% foreground. A ref from the same hour has none of
    that drift.

    Picks the middle of the longest still run before `before_ts` — still meaning
    consecutive frames that barely differ from each other, which in a doorway
    means nobody is in it. Returns a greyscale image, or None.
    """
    import numpy as np

    frames = raw_frames(before_ts - look_back, before_ts - 0.2)
    if len(frames) < 4:
        return None
    grays = [(i, _gray(p)) for i, (_t, _s, p) in enumerate(frames)]
    grays = [(i, g) for i, g in grays if g is not None]
    if len(grays) < 4:
        return None
    best_run: tuple[int, int] = (0, 0)
    run_start = 0
    for k in range(1, len(grays)):
        prev, cur = grays[k - 1][1], grays[k][1]
        moved = prev.shape != cur.shape or float(
            np.abs(cur.astype(np.int16) - prev.astype(np.int16)).mean()
        ) > 2.0
        if moved:
            run_start = k
        elif k - run_start > best_run[1] - best_run[0]:
            best_run = (run_start, k)
    if best_run[1] - best_run[0] < 2:
        return None
    return grays[(best_run[0] + best_run[1]) // 2][1]


def action_bounds(
    frames: list[tuple[float, int, Path]],
    *,
    ref_idx: int = 0,
    ref_gray: Any = None,
) -> tuple[int, int] | None:
    """First and last frame of the action, bounded by the room itself.

    the User 2026-08-16: *"end of action is when fov MATCHES REFS!"* — and the
    reference that matters is the room as it was moments before this event, not
    a stored empty-room set.

    The stored set could not do this job: all 28 refs were captured inside one
    7-minute window on a previous afternoon, so against a pre-dawn frame an
    EMPTY room measured 31-43% foreground and mean |diff| 47.4. Normalising
    brightness made it worse (79.2%), because the scene genuinely differs — lamp
    state, door angle, shadows. A reference taken from this event's own pre-roll
    has none of that drift by construction.

    Returns (i0, i1) inclusive, or None when nothing ever differs from the room.
    """
    import cv2
    import numpy as np

    if len(frames) < 2:
        return None
    ref = ref_gray
    if ref is None:
        ref_idx = max(0, min(int(ref_idx), len(frames) - 1))
        ref = _gray(frames[ref_idx][2])
    if ref is None:
        return None
    kern = np.ones((5, 5), np.uint8)
    fg: list[float] = []
    for _ts, _seq, p in frames:
        g = _gray(p)
        if g is None or g.shape != ref.shape:
            fg.append(-1.0)
            continue
        m = (cv2.absdiff(g, ref) > DIFF_ABS).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kern)
        fg.append(float(m.mean()))
    hot = [i for i, v in enumerate(fg) if v >= ACTION_FG]
    if not hot:
        return None
    i0 = hot[0]
    # End on a RUN of restored-room frames, not the first quiet one.
    i1 = hot[-1]
    quiet = 0
    for i in range(i0, len(fg)):
        if 0 <= fg[i] < ACTION_FG:
            quiet += 1
            if quiet >= ACTION_END_RUN:
                i1 = i - quiet
                break
        else:
            quiet = 0
    return (i0, max(i0, i1))


# Per-pixel grey difference that counts as "not the room".
#
# 12, not 30. the User asked to SEE a frame the code had called restored, and it was
# a woman standing in the doorway holding a mug, facing the camera. She read
# 1.6% foreground because she was in a light grey robe against light grey-blue
# walls — at a threshold of 30 most of her simply is not a big enough change.
#
# Measured on that exact pair (seq 412599 = her, seq 412617 = genuinely empty):
#
#     DIFF_ABS   her     empty
#         30    1.60%    0.00%
#         12    4.61%    0.00%     <- 3x the margin, empty still exactly zero
#          6    6.08%    2.21%     <- noise starts registering
#
# The lesson is the one that keeps repeating: a number looked reasonable and the
# picture said otherwise. Empty reads 0.00% across a wide range of thresholds,
# so there is room to be generous about what counts as somebody.
DIFF_ABS = int(os.environ.get("RAIL_RAWCLIP_DIFF_ABS", "12"))
# Bound clips by "the room is restored" rather than by a fixed duration.
BOUND_BY_ROOM = os.environ.get("RAIL_RAWCLIP_BOUND_BY_ROOM", "1") not in (
    "0", "false", "no",
)


def cut(
    t_start: float,
    *,
    dur: float = CLIP_SEC,
    lead: float = LEAD_SEC,
    max_frames: int = MAX_FRAMES,
) -> list[Path]:
    """Raw frames for a clip opening `lead` before `t_start`. Even count.

    Even because patch_temporal=2 rejects anything else. Dropping the last
    frame rather than duplicating one keeps the clip honest — a repeated
    final frame reads as a pause that did not happen.
    """
    frames = raw_frames(t_start - lead, t_start - lead + dur)
    if len(frames) > max_frames:
        step = (len(frames) - 1) / (max_frames - 1)
        frames = [frames[int(round(i * step))] for i in range(max_frames)]
    paths = [p for _ts, _seq, p in frames]
    if len(paths) % 2:
        paths = paths[:-1]
    return paths


# Where the face is, counted in frames from the motion onset. the User 2026-08-15
# asked for frames rather than seconds and he is right: seq only advances while
# the camera is capturing, so a seq delta IS a frame count and does not drift
# with the burst cadence.
#
# Measured, onset -> the frame FaceID picked (or the User picked by eye):
#   ev_1786812335833  15     ev_1786812272591  10
#   ev_1786813523348   7     the User's 13:19 lap   8
# Median 8, range 7-15. This window brackets every case seen with margin.
FACE_WINDOW_FROM = int(os.environ.get("RAIL_RAWCLIP_FACE_FROM", "6"))
FACE_WINDOW_TO = int(os.environ.get("RAIL_RAWCLIP_FACE_TO", "16"))


def best_face_window(
    onset_ts: float,
    *,
    span_sec: float = 6.0,
    start_frame: int | None = None,
) -> list[Path]:
    """Raw frames likely to hold the face, at full rate, oldest first.

    Indexed in FRAMES from the anchor, not seconds — the camera bursts at 30 fps
    and idles between, so a time window would sometimes contain two bursts and
    sometimes none.

    `start_frame` overrides FACE_WINDOW_FROM. The default 6 was measured from the
    PACK's onset, which door_capture walks back to rest before the approach. A
    caller anchoring on the raw camera TRIGGER is already a second or two into
    the motion, so the same offset opens after the face has passed: on the User's
    02:28 re-entry the only usable face (83x126 px, det 0.874) sat at frame 2 and
    the 6..16 window began at frame 6. Anchor on a trigger, pass start_frame=0.
    """
    frames = raw_frames(onset_ts - 0.1, onset_ts + span_sec)
    if not frames:
        return []
    first = FACE_WINDOW_FROM if start_frame is None else max(0, int(start_frame))
    lo = min(first, max(0, len(frames) - 1))
    hi = min(FACE_WINDOW_TO + 1, len(frames))
    return [p for _ts, _seq, p in frames[lo:hi]]


def _ts_from_name(path: Path) -> float | None:
    """Raw names are f_<ms>_<seq>.jpg. ms is epoch milliseconds."""
    m = _NAME.match(path.name)
    if not m:
        return None
    try:
        return int(m.group(1)) / 1000.0
    except (TypeError, ValueError):
        return None


def span_fps(timestamps: list[float], n: int) -> float:
    """Wall-clock fps for n frames covering timestamps[0]..timestamps[-1]."""
    if n < 2 or len(timestamps) < 2:
        return CLIP_FPS
    span = float(timestamps[-1]) - float(timestamps[0])
    if span <= 1e-6:
        return CLIP_FPS
    return (n - 1) / span


def encode_mp4(
    paths: list[Path],
    *,
    fps: float | None = None,
    width: int = CLIP_WIDTH,
    timestamps: list[float] | None = None,
) -> bytes | None:
    """H.264 mp4 whose container clock matches wall time.

    Default used to be CLIP_FPS=8 regardless of how the jpegs were spaced, so
    vLLM told Qwen3-VL the event lasted n/8 seconds (measured: 4.60 s of door
    became 0.25 s in tokens). Pass timestamps (or keep raw f_<ms>_*.jpg names)
    so duration = last−first. RAIL_RAWCLIP_FPS remains an explicit override
    when `fps` is set.
    """
    import shutil

    if len(paths) < 2 or len(paths) % 2:
        return None
    n = len(paths)
    ts: list[float] | None = None
    if timestamps is not None and len(timestamps) >= n:
        ts = [float(x) for x in timestamps[:n]]
    else:
        parsed = [_ts_from_name(Path(p)) for p in paths]
        if all(x is not None for x in parsed):
            ts = [float(x) for x in parsed]  # type: ignore[misc]
    with tempfile.TemporaryDirectory() as td:
        for i, p in enumerate(paths):
            try:
                shutil.copy(p, f"{td}/f{i:04d}.jpg")
            except OSError:
                return None
        out = f"{td}/clip.mp4"
        if fps is not None:
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-framerate", f"{float(fps):g}",
                "-i", f"{td}/f%04d.jpg",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-vf", f"scale={int(width)}:-2",
                out,
            ]
        elif ts and (ts[-1] - ts[0]) > 1e-6:
            list_path = f"{td}/concat.txt"
            lines = ["ffconcat version 1.0\n"]
            for i in range(n):
                dur = (ts[i + 1] - ts[i]) if i + 1 < n else max(
                    (ts[-1] - ts[-2]) if n > 1 else 1.0 / CLIP_FPS,
                    1e-3,
                )
                # concat demuxer paths cannot be empty; duration is display time
                lines.append(f"file 'f{i:04d}.jpg'\n")
                lines.append(f"duration {max(dur, 1e-3):.6f}\n")
            lines.append(f"file 'f{n - 1:04d}.jpg'\n")
            Path(list_path).write_text("".join(lines), encoding="utf-8")
            span = ts[-1] - ts[0]
            last_dt = max(
                (ts[-1] - ts[-2]) if n > 1 else 1.0 / CLIP_FPS,
                1e-3,
            )
            # concat lists the last jpeg twice; -t last−first used to cut the
            # last unique frame (24→23). Include its duration. [gap D]
            clip_t = span + last_dt
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-vsync", "vfr",
                "-t", f"{clip_t:.4f}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-vf", f"scale={int(width)}:-2",
                out,
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-framerate", f"{CLIP_FPS:g}",
                "-i", f"{td}/f%04d.jpg",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-vf", f"scale={int(width)}:-2",
                out,
            ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0 or not os.path.isfile(out):
            # concat VFR can fail on some ffmpeg builds — constant honest fps
            if ts and fps is None and (ts[-1] - ts[0]) > 1e-6:
                honest = span_fps(ts, n)
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-framerate", f"{honest:g}",
                    "-i", f"{td}/f%04d.jpg",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-vf", f"scale={int(width)}:-2",
                    out,
                ]
                try:
                    r = subprocess.run(cmd, capture_output=True, timeout=60)
                except (OSError, subprocess.SubprocessError):
                    return None
                if r.returncode != 0 or not os.path.isfile(out):
                    return None
            else:
                return None
        try:
            return Path(out).read_bytes()
        except OSError:
            return None


def clip_for_event(
    trigger_ts: float,
    *,
    window_before: float = 8.0,
    window_after: float = 90.0,
    onset_ts: float | None = None,
    onset_seq: int | None = None,
) -> dict[str, Any] | None:
    """Cut video of the first motion for a door event.

    Prefers the onset the PACK already measured. door_capture and event-clip
    both walk back to rest at capture time (find_onset / find_bounds) and
    persist the result as onset_seq/onset_ts, so re-deriving it here is a third
    opinion on a settled question — and measured 2026-08-15 it is not a better
    one: against event-clip's walk-back this module landed 0.84 s earlier on
    ev_1786823429156 and 2.00 s LATER on ev_1786823032334, disagreeing in both
    directions, while the pack's own onset was earliest on both.

    first_motion stays as the fallback for a manifest with no onset recorded.

    Returns {"mp4": bytes, "n_frames": int, "onset": {...}} or None. The search
    window is wide after the trigger because the door tick can lead the actual
    crossing by tens of seconds — measured 26.4 s on ev_1786807037052.
    """
    # Bound the clip by the room, not by a duration (the User 2026-08-16: "end of
    # action is when fov MATCHES REFS!"). The reference comes straight out of
    # the cache — thousands of empty-doorway frames flow through it every hour,
    # so one from the last minute is correctly lit for right now.
    #
    # On ev_1786876567076 the fixed window sent 16 frames of which 13 were the
    # restored room; bounded, it is 3. Argus was reading 81% empty dresser.
    if BOUND_BY_ROOM:
        try:
            span = action_window(
                trigger_ts,
                window_before=window_before,
                window_after=window_after,
            )
            if span:
                    paths = [p for _t, _s, p in span]
                    times = [float(_t) for _t, _s, _p in span]
                    if len(paths) % 2:
                        paths = paths[:-1]
                        times = times[:-1]
                    mp4 = (
                        encode_mp4(paths, timestamps=times)
                        if len(paths) >= 2
                        else None
                    )
                    if mp4:
                        return {
                            "mp4": mp4,
                            "n_frames": len(paths),
                            "paths": paths,
                            "onset": {
                                "ts": span[0][0],
                                "seq": span[0][1],
                                "source": "action_bounds",
                            },
                        }
        except Exception:
            pass  # fall through to the duration-based cut

    onset: dict[str, Any] | None = None
    if onset_ts:
        onset = {
            "ts": float(onset_ts),
            "seq": int(onset_seq) if onset_seq is not None else None,
            "source": "pack",
        }
    if not onset:
        onset = first_motion(trigger_ts - window_before, trigger_ts + window_after)
        if onset:
            onset["source"] = "raw_clip"
    if not onset:
        return None
    paths = cut(float(onset["ts"]))
    if len(paths) < 2:
        return None
    mp4 = encode_mp4(paths)  # names carry epoch ms when they are raw frames
    if not mp4:
        return None
    # `paths` rides along so the caller can persist exactly what was encoded.
    # Reconstructing it later is not equivalent: raw is pruned within the hour,
    # and a replay only ever sees the frames that survived — a perfect subset
    # (the User 2026-08-16). Evidence has to be captured at fire time or it is
    # evidence of something else.
    return {"mp4": mp4, "n_frames": len(paths), "onset": onset, "paths": paths}
