"""The door watch rests when the room is empty (the User 2026-10-10, .101 round 3: idle, nobody at the camera, the
presence service ran at 631 % CPU - seven threads of the face landmarker on frames with faces=0 - and the User felt his
daily laptop slow).

The door loop ticks every RAIL_PRESENCE_DOOR_INTERVAL (0.5 s) and scans up to RAIL_PRESENCE_FACE_MAX_PER_TICK (12)
frames of the camera's ring buffer each tick: about 24 landmarker runs a second, whoever is there. The house did that
on purpose (a walk-in seen in any frame of the buffer). Here, once no face has been found and nothing has moved for
IDLE_AFTER_S, the loop ticks every IDLE_INTERVAL_S and scans only the newest frame - well under one run a second. It
is back at full rate at once:
  - on MOTION: the newest frame against the last one, both shrunk to 64x36 grey, mean absolute difference over
    MOTION_LEVEL (0-255 scale) - a few microseconds a tick, measured on every tick even while resting;
  - on a FACE in a scanned frame.
Kill: CRISSA_PRESENCE_IDLE=0 (the house's full rate always).

SETTLED (round 14, 2026-10-12, .101: presence at 188-209 % the whole time the User sat at the desk, and at rest): a
face in view that stays put is not news. Once the same number of faces has been seen, nothing has moved and nobody has
spoken for SETTLE_AFTER_S, each tick scans only the newest frame (about 2 runs a second instead of ~24). Back to the
full rate at once on motion, on a change in the number of faces, and for TURN_HOLD_S after a voice turn (the ear's
report reaching presence). The face rule's own freshness does not ride on this loop: on a fixed camera its face is the
oracle's look (fixed_look, once a second). Kill: CRISSA_PRESENCE_SETTLE=0."""
from __future__ import annotations

import os
import time

IDLE_AFTER_S = float(os.environ.get("CRISSA_PRESENCE_IDLE_AFTER_S", "10"))
IDLE_INTERVAL_S = float(os.environ.get("CRISSA_PRESENCE_IDLE_INTERVAL_S", "2.0"))
MOTION_LEVEL = float(os.environ.get("CRISSA_PRESENCE_MOTION_LEVEL", "6.0"))
SETTLE_AFTER_S = float(os.environ.get("CRISSA_PRESENCE_SETTLE_AFTER_S", "8"))
TURN_HOLD_S = float(os.environ.get("CRISSA_PRESENCE_TURN_HOLD_S", "8"))
# MOTION OVER A FIXED SPAN (round 15, 2026-10-12, .101: never settled - `since_motion_s` reset on every tick). The test
# compared the newest frame with the previous TICK's newest; on .101 a busy tick takes ~3.5 s (12 face scans on two
# cores), and a person at a desk always moves in 3.5 s, so every tick read as motion and the loop never left the full
# rate. Now it compares the newest frame with the buffered frame MOTION_SPAN_S older (the previous tick's newest only
# when the buffer holds one frame), so motion means moving now, however long a tick takes. Kill:
# CRISSA_PRESENCE_MOTION_SPAN_S=0 (tick to tick, as before).
MOTION_SPAN_S = float(os.environ.get("CRISSA_PRESENCE_MOTION_SPAN_S", "0.5"))
# A SETTLED FACE IGNORES ORDINARY MOVEMENT (round 15): .101's own camera, the User at the desk, 64x36 grey mean
# difference - 0.5 s apart median 2.1, p90 9.8, max 19.7 (6 of 39 over the empty room's 6); 3.5 s apart median 9.4, 24
# of 33 over 6. A person in view moves; the door loop's job then is a newcomer (the face count, read on the newest frame
# every tick) or a big change, so the settled state needs BIG_MOTION_LEVEL over the span (3x the empty room's level).
SETTLED_INTERVAL_S = float(os.environ.get("CRISSA_PRESENCE_SETTLED_INTERVAL_S", "1.0"))   # a settled face's tick
# A SETTLED FACE WAKES ON A CHANGE BETWEEN ITS TICKS TOO, AND ON A NEW FACE (round 16, 2026-10-13, measured in a sandbox
# on a film camera). A settled tick comes once a second and the span test compares the newest frame with the one 0.5 s
# before it, so a change in the other half of the second was never seen (a cut from one person to another was missed
# when it fell 0.5-1 s before the tick). While settled the newest frame is also compared with the oldest frame of the
# buffer that came in since the last tick (at most SETTLED_WINDOW_S old), at the big level. And a face that was not in
# view since the room was last empty - a name not seen, or more unnamed faces than seen - wakes it like a new count
# (`who`), so a newcomer who takes the same seat is not a settled face. Kill: CRISSA_PRESENCE_SETTLED_WAKE=0.
SETTLED_WINDOW_S = float(os.environ.get("CRISSA_PRESENCE_SETTLED_WINDOW_S", "2.5"))


def settled_wake_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PRESENCE_SETTLED_WAKE", "1")).strip().lower() not in ("0", "off", "false", "no")


BIG_MOTION_LEVEL = float(os.environ.get("CRISSA_PRESENCE_BIG_MOTION_LEVEL", str(3.0 * MOTION_LEVEL)))


def settle_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PRESENCE_SETTLE", "1")).strip().lower() not in ("0", "off", "false", "no")


def on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PRESENCE_IDLE", "1")).strip().lower() not in ("0", "off", "false", "no")


def _small(bgr):
    """64x36 grey of a BGR frame (numpy array), or None."""
    try:
        import cv2
        return cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (64, 36), interpolation=cv2.INTER_AREA)
    except Exception:  # noqa: BLE001
        try:
            g = bgr.mean(axis=2) if getattr(bgr, "ndim", 0) == 3 else bgr
            h, w = g.shape[:2]
            return g[:: max(1, h // 36), :: max(1, w // 64)][:36, :64]
        except Exception:  # noqa: BLE001
            return None


class IdleGate:
    """pick(frames, now) -> the frames to scan this tick; saw(n_faces, now) after the scan; interval(full, now)."""

    def __init__(self, enabled: bool | None = None, clock=time.time, settle: bool | None = None):
        self.enabled = on() if enabled is None else bool(enabled)
        self.settle = settle_on() if settle is None else bool(settle)
        self.clock = clock
        t = clock()
        self.last_face = t                 # a fresh start scans at full rate for IDLE_AFTER_S
        self.last_motion = t
        self.last_turn = 0.0               # the last voice turn presence heard of (turn())
        self.last_big = t                  # the last big change over the span (settled() reads it)
        self.n_faces = None                # faces in the last scanned frame
        self.last_count_change = t
        self.seen_names: set = set()       # names in view since the room was last empty (who())
        self.seen_unnamed = 0              # the most unnamed faces in one look since then
        self.wake_new = settled_wake_on()
        self.prev = None
        self.scans = 0                     # frames handed to the landmarker (for the idle-load test and /health)
        self.ticks = 0

    def turn(self, ts: float | None = None) -> None:
        """A voice turn (the ear's report): full rate for TURN_HOLD_S."""
        try:
            t = float(ts) if ts is not None else self.clock()
        except (TypeError, ValueError):
            return
        if t > self.last_turn:
            self.last_turn = t

    def settled(self, now: float | None = None) -> bool:
        """A face in view, the same count, nothing moving and nobody speaking for a while (not the empty-room rest)."""
        now = self.clock() if now is None else now
        if not (self.enabled and self.settle) or self.resting(now):
            return False
        if not self.n_faces:
            return False
        moved_at = self.last_big if MOTION_SPAN_S > 0 else self.last_motion
        return bool(now - moved_at >= SETTLE_AFTER_S and now - self.last_count_change >= SETTLE_AFTER_S
                    and now - self.last_turn >= TURN_HOLD_S)

    def resting(self, now: float | None = None) -> bool:
        now = self.clock() if now is None else now
        return bool(self.enabled and now - self.last_face >= IDLE_AFTER_S and now - self.last_motion >= IDLE_AFTER_S)

    def motion(self, frame_bgr, now: float | None = None) -> bool:
        now = self.clock() if now is None else now
        cur = _small(frame_bgr) if frame_bgr is not None else None
        moved = False
        if cur is not None and self.prev is not None:
            try:
                import numpy as np
                moved = float(np.abs(cur.astype("float32") - self.prev.astype("float32")).mean()) > MOTION_LEVEL
            except Exception:  # noqa: BLE001
                moved = False
        if cur is not None:
            self.prev = cur
        if moved:
            self.last_motion = now
        return moved

    def pick(self, frames: list, now: float | None = None) -> list:
        """All of them at full rate; the newest only while resting. Motion is read on the newest first."""
        now = self.clock() if now is None else now
        self.ticks += 1
        if not frames:
            return []
        newest = max(frames, key=lambda f: getattr(f, "ts", 0.0))
        was_settled = self.settled(now)
        if MOTION_SPAN_S > 0:
            moved, big = self._moved(frames, newest)
            if was_settled and self.wake_new and not big:
                big = self._changed_since_last_tick(frames, newest)
            if moved:
                self.last_motion = now
            if big:
                self.last_big = now
        else:
            self.motion(getattr(newest, "bgr", None), now)
        out = [newest] if (self.resting(now) or self.settled(now)) else list(frames)
        self.scans += len(out)
        return out

    def _moved(self, frames: list, newest) -> "tuple[bool, bool]":
        """(moved, big). moved, for the empty-room rest: the newest frame against the buffered frame MOTION_SPAN_S
        older over MOTION_LEVEL; against the previous tick's newest only when that tick was at most two spans ago; and
        a large change (3x the level, a walk-in) against the previous tick whatever its age. big, for a settled face:
        over BIG_MOTION_LEVEL against the span's frame only (a slow tick's own gap is not a movement)."""
        import numpy as np
        cur = _small(getattr(newest, "bgr", None)) if getattr(newest, "bgr", None) is not None else None
        if cur is None:
            return False, False
        try:
            t_new = float(getattr(newest, "ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            t_new = 0.0

        def diff(a) -> float:
            return float(np.abs(cur.astype("float32") - a.astype("float32")).mean())
        moved = big = False
        ref = self._span_ref(frames, newest)
        if ref is not None:
            r = _small(getattr(ref, "bgr", None))
            if r is not None:
                d_span = diff(r)
                moved = d_span > MOTION_LEVEL
                big = d_span > BIG_MOTION_LEVEL
        if self.prev is not None:
            d = diff(self.prev)
            gap = t_new - float(getattr(self, "prev_ts", 0.0) or 0.0)
            if d > 3.0 * MOTION_LEVEL or (d > MOTION_LEVEL and 0.0 <= gap <= 2.0 * MOTION_SPAN_S):
                moved = True               # the empty-room rest's rules, as before
            if d > BIG_MOTION_LEVEL and 0.0 <= gap <= 2.0 * MOTION_SPAN_S:
                big = True                 # a change that landed between two fast ticks; a slow tick's gap never
        self.prev, self.prev_ts = cur, t_new
        return moved, big

    @staticmethod
    def _span_ref(frames: list, newest):
        """The buffered frame closest to MOTION_SPAN_S before the newest (at least half the span older), or None: then
        the previous tick's newest is the reference, as before."""
        if MOTION_SPAN_S <= 0 or len(frames) < 2:
            return None
        try:
            t_new = float(getattr(newest, "ts", 0.0))
            older = [f for f in frames if f is not newest and t_new - float(getattr(f, "ts", 0.0)) >= MOTION_SPAN_S / 2]
        except (TypeError, ValueError):
            return None
        if not older:
            return None
        return min(older, key=lambda f: abs((t_new - float(getattr(f, "ts", 0.0))) - MOTION_SPAN_S))

    def _changed_since_last_tick(self, frames: list, newest) -> bool:
        """The newest frame against the oldest buffered frame of this tick (at most SETTLED_WINDOW_S old), big level."""
        try:
            t_new = float(getattr(newest, "ts", 0.0) or 0.0)
            older = [f for f in frames if f is not newest and getattr(f, "bgr", None) is not None
                     and 0.0 < t_new - float(getattr(f, "ts", 0.0) or 0.0) <= SETTLED_WINDOW_S]
        except (TypeError, ValueError):
            return False
        if not older:
            return False
        first = min(older, key=lambda f: float(getattr(f, "ts", 0.0) or 0.0))
        a, b = _small(getattr(newest, "bgr", None)), _small(getattr(first, "bgr", None))
        if a is None or b is None:
            return False
        import numpy as np
        return float(np.abs(a.astype("float32") - b.astype("float32")).mean()) > BIG_MOTION_LEVEL

    def who(self, names, unnamed: int = 0, now: float | None = None) -> bool:
        """The faces the door loop named this tick: a name not seen, or more unnamed faces than seen, since the room
        was last empty is a newcomer and wakes a settled face (as a new count). Returns whether it woke."""
        now = self.clock() if now is None else now
        if self.resting(now):
            self.seen_names, self.seen_unnamed = set(), 0
        cur = {str(n).strip().lower() for n in (names or []) if str(n or "").strip()}
        try:
            k = max(0, int(unnamed or 0))
        except (TypeError, ValueError):
            k = 0
        new = bool(cur - self.seen_names) or k > self.seen_unnamed
        first = not self.seen_names and not self.seen_unnamed
        self.seen_names |= cur
        self.seen_unnamed = max(self.seen_unnamed, k)
        if new and not first and self.wake_new:
            self.last_count_change = now
            return True
        return False

    def saw(self, n_faces: int, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        n = int(n_faces or 0)
        if n > 0:
            self.last_face = now
        if self.n_faces is None or n != self.n_faces:
            self.last_count_change = now
        self.n_faces = n

    def interval(self, full: float, now: float | None = None) -> float:
        if self.resting(now):
            return max(float(full), IDLE_INTERVAL_S)
        if self.settled(now):
            return max(float(full), SETTLED_INTERVAL_S)   # one newest frame a second: a newcomer is seen within it
        return float(full)

    def status(self, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        return {"on": self.enabled, "resting": self.resting(now), "settled": self.settled(now), "scans": self.scans,
                "ticks": self.ticks, "since_face_s": round(now - self.last_face, 1),
                "since_motion_s": round(now - self.last_motion, 1), "since_big_motion_s": round(now - self.last_big, 1),
                "since_turn_s": round(now - self.last_turn, 1) if self.last_turn else None, "faces": self.n_faces}
