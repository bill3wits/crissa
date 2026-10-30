"""THE PHONE AS THE SECOND MIC: the best signal wins (the User 2026-10-19, ASK-20261019-1230 from .101: "or they
compete with 2s sample avg"; "go on build 1").

The desk microphone stays the default. While the phone has its mic open it streams its audio here (the phone server's
/api/mic relays it to the ear's /v1/phone-mic), and the two compete:
  - each mic is scored on speech over ITS OWN noise floor (the phone's auto gain makes it loud in a quiet room, so raw
    level would always pick it): a frame's level minus the 10th percentile of that mic's last FLOOR_S of frames;
  - the score is a SCORE_S (2 s) average over the frames where either mic heard speech;
  - the other mic takes over only when it leads by MARGIN_DB (about 3 dB), so near-equal readings do not flip;
  - both are buffered (the desk's utterance as today, the phone's ring BUF_S long), and at the utterance's flush the
    winner's copy of the WHOLE utterance goes to the ASR, from its first word; one copy, never both.
  - While one of her lines plays from the desk speakers the phone is ignored (the desk's echo canceller covers the
    desk mic only; the phone would hear her and she could answer herself). While her line plays ON THE PHONE (the phone
    server stamps `line_until` in the floor file) the desk is ignored instead: the desk mic hears the phone's speaker,
    and the phone's own echo cancelling covers its mic.
While the phone mic is open this rule replaces the floor hold for the button (server.py); the floor hold stays for the
web app's typed Send, and for a desk utterance while her line plays on the phone.

A phone chunk carries no clock the ear shares, so it is placed on the ear's timeline at its arrival minus LAG_S (the
phone's capture-to-arrival delay). Over a 2 s average and a 1.2 s preroll a quarter second either way does not move
the pick.

Switch: CRISSA_EAR_PHONE_MIC=1 (default off: the ear never reads the phone and /v1/phone-mic answers off).
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Any, Optional

import numpy as np

DESK = "desk"
PHONE = "phone"


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


SCORE_S = _f("CRISSA_EAR_PHONE_MIC_SCORE_S", 2.0)       # the average's window, over speech frames
MARGIN_DB = _f("CRISSA_EAR_PHONE_MIC_MARGIN_DB", 3.0)   # the lead the other mic needs to take over
FLOOR_S = _f("CRISSA_EAR_PHONE_MIC_FLOOR_S", 10.0)      # each mic's noise floor is read over this much of its frames
FLOOR_PCT = 10.0
OPEN_S = _f("CRISSA_EAR_PHONE_MIC_OPEN_S", 1.0)         # a phone silent on the wire this long has closed its mic
LAG_S = _f("CRISSA_EAR_PHONE_MIC_LAG_S", 0.25)          # capture to arrival
SPEECH_DB = _f("CRISSA_EAR_PHONE_MIC_SPEECH_DB", 6.0)   # a phone frame opens an utterance only this far over its floor
BUF_S = 40.0                                            # longer than the ear's longest utterance + preroll
MAX_CHUNK_S = 2.0


def on() -> bool:
    return os.environ.get("CRISSA_EAR_PHONE_MIC", "0").strip().lower() in ("1", "true", "yes", "on")


def _db(x: Optional[np.ndarray]) -> float:
    if x is None or len(x) == 0:
        return -120.0
    r = float(np.sqrt(np.mean(np.square(np.asarray(x, dtype=np.float64)))))
    return 20.0 * np.log10(max(r, 1e-6))


def _resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or len(x) == 0:
        return x.astype(np.float32, copy=False)
    n = max(1, int(round(len(x) * sr_out / float(sr_in))))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def line_air(now: float, start: Optional[float] = None) -> bool:
    """Was her line playing on the phone at any time in [start, now] (the phone server's `line_until`)?"""
    try:
        from . import veto_first

        d = json.loads(veto_first.phone_floor_path().read_text(encoding="utf-8"))
        since, until = float(d.get("line_since") or 0.0), float(d.get("line_until") or 0.0)
    except Exception:  # noqa: BLE001 - no file, no line on the phone
        return False
    s = now if start is None else float(start)
    return bool(since and since <= now and until >= s)


def desk_air(now: float) -> bool:
    """One of her lines on the air from the desk speakers (house-speak's gate)."""
    try:
        from .ear_loop import her_air_now

        return bool(her_air_now(now))
    except Exception:  # noqa: BLE001
        return False


class Floor:
    """A mic's noise floor: the FLOOR_PCT percentile of its frame levels over the last FLOOR_S."""

    def __init__(self) -> None:
        self.rows: deque = deque()

    def add(self, t: float, db: float) -> None:
        self.rows.append((t, db))
        while self.rows and self.rows[0][0] < t - FLOOR_S:
            self.rows.popleft()

    def value(self) -> Optional[float]:
        if not self.rows:
            return None
        return float(np.percentile([d for _, d in self.rows], FLOOR_PCT))

    def clear(self) -> None:
        self.rows.clear()


class PhoneMic:
    def __init__(self, sr: int = 16000) -> None:
        self.sr = int(sr)
        self.lock = threading.Lock()
        self.segs: deque = deque()          # (t0 on the ear's clock, float32 samples at self.sr)
        self.last_arrival = 0.0
        self.chunks = 0
        self.floors = {DESK: Floor(), PHONE: Floor()}
        self.scores: dict[str, deque] = {DESK: deque(), PHONE: deque()}
        self.winner = DESK
        self.ignored: deque = deque()       # (t, why) frames the phone (or the desk) sat out
        self.picks = {DESK: 0, PHONE: 0}
        self.last_pick: dict[str, Any] = {}
        self.decided: dict[str, Any] = {}   # the averages behind the winner, as of its last speech frame

    # -- the wire ---------------------------------------------------------------------------------------------------
    def push(self, pcm16: bytes, sr: int, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else float(now)
        x = np.frombuffer(pcm16[: len(pcm16) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        if len(x) == 0:
            return {"ok": False, "error": "no audio"}
        if len(x) > int(MAX_CHUNK_S * int(sr)):
            return {"ok": False, "error": "send chunks of %.0f s or less" % MAX_CHUNK_S}
        x = _resample(x, int(sr), self.sr)
        with self.lock:
            t1 = now - LAG_S
            t0 = t1 - len(x) / float(self.sr)
            if self.segs:                    # back to back on the wire: keep it contiguous, never overlapping
                p0, px = self.segs[-1]
                pend = p0 + len(px) / float(self.sr)
                if t0 < pend or t0 - pend < 0.05:
                    t0 = pend
            self.segs.append((t0, x))
            while self.segs and self.segs[0][0] + len(self.segs[0][1]) / float(self.sr) < now - BUF_S:
                self.segs.popleft()
            self.last_arrival = now
            self.chunks += 1
        return {"ok": True, "s": round(len(x) / float(self.sr), 3)}

    def is_open(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else float(now)
        return bool(self.last_arrival) and now - self.last_arrival <= OPEN_S

    def window(self, t0: float, t1: float) -> np.ndarray:
        """The phone's audio for [t0, t1] on the ear's clock; silence where it sent none."""
        n = max(0, int(round((t1 - t0) * self.sr)))
        out = np.zeros(n, dtype=np.float32)
        with self.lock:
            segs = list(self.segs)
        for s0, x in segs:
            a = int(round((s0 - t0) * self.sr))
            b = a + len(x)
            if b <= 0 or a >= n:
                continue
            out[max(a, 0):min(b, n)] = x[max(0, -a):max(0, -a) + (min(b, n) - max(a, 0))]
        return out

    # -- one ear frame ----------------------------------------------------------------------------------------------
    def frame(self, t_cap: float, frame_s: float) -> Optional[np.ndarray]:
        """The phone's copy of the ear frame that ended at t_cap (the latest whole one: LAG_S back), or None when its
        mic is closed or it sits this frame out (her line on the desk speakers)."""
        if not self.is_open(t_cap):
            self._close()                     # its scores go; the winner stands until someone speaks again
            return None
        if desk_air(t_cap) and not line_air(t_cap):
            with self.lock:
                self.ignored.append((t_cap, "desk_air"))
            return None
        return self.window(t_cap - frame_s - LAG_S, t_cap - LAG_S)

    def heard(self, phone: Optional[np.ndarray], vad: bool) -> bool:
        """Does the phone's frame open (or carry) an utterance: the speech detector's yes, and SPEECH_DB over the
        phone's own floor (a phone's auto gain lifts its hiss to where the detector fires on it)."""
        if phone is None or not vad:
            return False
        with self.lock:
            fl = self.floors[PHONE].value() if len(self.floors[PHONE].rows) >= 5 else None
        return fl is None or _db(phone) >= fl + SPEECH_DB

    def score(self, t: float, desk: np.ndarray, phone: Optional[np.ndarray], speech: bool) -> dict:
        """Feed one frame's levels; the 2 s averages and the winner after it."""
        with self.lock:
            d_db, p_db = _db(desk), (_db(phone) if phone is not None else None)
            self.floors[DESK].add(t, d_db)
            if p_db is not None:
                self.floors[PHONE].add(t, p_db)
            if speech:
                self._add(DESK, t, d_db)
                if p_db is not None:
                    self._add(PHONE, t, p_db)
            for k in (DESK, PHONE):
                q = self.scores[k]
                while q and q[0][0] < t - SCORE_S:
                    q.popleft()
            if speech:                        # quiet frames (a hangover, a pause) never move the winner
                self.winner = decide(self.winner, self._avg(DESK), self._avg(PHONE))
                self.decided = {"desk_avg": self._avg(DESK), "phone_avg": self._avg(PHONE), "t": round(t, 3)}
            return self._view()

    def _add(self, mic: str, t: float, db: float) -> None:
        fl = self.floors[mic].value()
        self.scores[mic].append((t, max(0.0, db - (db if fl is None else fl))))

    def _avg(self, mic: str) -> Optional[float]:
        q = self.scores[mic]
        return float(np.mean([s for _, s in q])) if q else None

    def _close(self) -> None:
        with self.lock:
            self.scores[PHONE].clear()
            self.floors[PHONE].clear()

    # -- the utterance ----------------------------------------------------------------------------------------------
    def pick(self, t0: float, t1: float, desk_wav: np.ndarray) -> tuple[np.ndarray, dict]:
        """At the utterance's flush: the copy that goes to the ASR (one, never both) and the stamp it carries.
        `desk_wav` is the ear's own copy of [t0, t1]."""
        why, mic = "winner", self.winner
        opened = bool(self.last_arrival) and self.last_arrival >= t0   # it streamed during the utterance (a released
        if not opened:                                                  # push to talk has stopped by the flush)
            mic, why = DESK, "phone_closed"
        elif line_air(t1, t0):
            mic, why = PHONE, "line_on_phone"        # the desk hears the phone's speaker
        else:
            with self.lock:
                sat_out = any(t0 <= t <= t1 for t, _ in self.ignored)
                while self.ignored and self.ignored[0][0] < t1 - BUF_S:
                    self.ignored.popleft()
            if sat_out or desk_air(t1):
                mic, why = DESK, "desk_air"
        with self.lock:
            meta = dict(self._view(), mic_pick=mic, mic_pick_why=why, phone_open=opened)
            r = lambda v: None if v is None else round(v, 1)  # noqa: E731
            meta.update(desk_avg=r(self.decided.get("desk_avg")), phone_avg=r(self.decided.get("phone_avg")))
            self.picks[mic] += 1
            self.last_pick = {"t": round(t1, 3), "mic": mic, "why": why, "dur": round(t1 - t0, 2),
                              "desk_avg": meta.get("desk_avg"), "phone_avg": meta.get("phone_avg")}
        if mic == PHONE:
            return self.window(t0, t1)[: len(desk_wav)] if len(desk_wav) else self.window(t0, t1), meta
        return desk_wav, meta

    def _view(self) -> dict:
        da, pa = self._avg(DESK), self._avg(PHONE)
        df, pf = self.floors[DESK].value(), self.floors[PHONE].value()
        r = lambda v: None if v is None else round(v, 1)  # noqa: E731
        return {"winner": self.winner, "desk_avg": r(da), "phone_avg": r(pa), "desk_floor": r(df), "phone_floor": r(pf)}

    def status(self, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else float(now)
        with self.lock:
            v = self._view()
            v.update(on=on(), open=self.is_open(now), chunks=self.chunks, picks=dict(self.picks),
                     last_chunk_age_s=(round(now - self.last_arrival, 2) if self.last_arrival else None),
                     last_pick=dict(self.last_pick), margin_db=MARGIN_DB, score_s=SCORE_S, lag_s=LAG_S)
        return v


def decide(current: str, desk_avg: Optional[float], phone_avg: Optional[float], margin: Optional[float] = None) -> str:
    """The mic that wins now: the other one takes over only on a lead of `margin` dB; no phone score = the desk."""
    m = MARGIN_DB if margin is None else float(margin)
    if phone_avg is None:
        return DESK
    d = 0.0 if desk_avg is None else desk_avg
    if current == PHONE:
        return DESK if d >= phone_avg + m else PHONE
    return PHONE if phone_avg >= d + m else DESK


_ONE: Optional[PhoneMic] = None
_ONE_LOCK = threading.Lock()


def get(sr: int = 16000) -> PhoneMic:
    global _ONE
    with _ONE_LOCK:
        if _ONE is None:
            _ONE = PhoneMic(sr)
        return _ONE
