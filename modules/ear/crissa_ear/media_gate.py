"""Media-vs-person gate for live voice-bank promote (the User 2026-08-02).

Problem: self-sim is a bad sole-room gate (room acoustics, new enrolls).
Media (TV/podcast) can still stamp or sole-fill and poison the bank.

Approach:
  * After dead air, first sound opens a ~20s **characterize** window.
  * Collect turn shape (gaps, density, speaker changes, ghosts) → media | human | unsure.
  * Promote writes only when **clear_human**.
  * If media/unsure, hold until conversational pauses / multi-speaker prove a person.

No GPU — CODE on turn timing + text + emb person_id. Optional later: 35B assist.

2026-08-02 fix: ear max_utter is 12s — old MEDIA_MIN_SPEECH_SEC=12 labeled every
full chunk as dense media. Real conversation (back-and-forth) was stuck media_on.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

def _media_station_from_geom() -> int:
    """Init-written hour; default 9 (DD desk)."""
    try:
        from .media_locus import get_media_locus

        return int(get_media_locus().get("station") or 9)
    except Exception:
        return int(os.environ.get("RAIL_MEDIA_STATION", "9"))


# Camera center, 12=door → 9 = camera-left = the User's desk sound source (default).
MEDIA_STATION = _media_station_from_geom()

# Characterization window after first sound out of dead air.
# Ear max_utter is ~12s — window must hold ≥2 full chunks or dense media never
# reaches MEDIA_MIN_TURNS / MEDIA_MIN_SPEECH (measured stuck n_turns=1, 2026-08-03).
CHAR_WINDOW_SEC = float(os.environ.get("RAIL_MEDIA_CHAR_SEC", "48"))
# Quiet this long → reset session / re-open characterize on next sound
DEAD_AIR_SEC = float(os.environ.get("RAIL_MEDIA_DEAD_AIR_SEC", "8"))
# Human: at least this many inter-utterance pauses ≥ PAUSE_MIN in the window
PAUSE_MIN_SEC = float(os.environ.get("RAIL_MEDIA_PAUSE_MIN_SEC", "0.55"))
HUMAN_MIN_PAUSES = int(os.environ.get("RAIL_MEDIA_HUMAN_MIN_PAUSES", "2"))
# Media: sustained continuous talk across *multiple* ear chunks.
# Two full 12s chunks (~24s speech) with no pauses is enough; old 28s+3-turn
# bar was unreachable inside a 20s window.
MEDIA_MIN_SPEECH_SEC = float(os.environ.get("RAIL_MEDIA_MIN_SPEECH_SEC", "20"))
MEDIA_MAX_PAUSE_FRAC = float(os.environ.get("RAIL_MEDIA_MAX_PAUSE_FRAC", "0.08"))
MEDIA_MIN_TURNS = int(os.environ.get("RAIL_MEDIA_MIN_TURNS", "2"))
# Single maxed-out chunk (no pause) + weak/no emb → lean media early
MEDIA_CHUNK_MAX_SEC = float(os.environ.get("RAIL_MEDIA_CHUNK_MAX_SEC", "11.0"))
# Dead-air / no-speech floor (align with pipeline silence ~-50 dB)
MIN_RMS_DB = float(os.environ.get("RAIL_MEDIA_MIN_RMS_DB", "-48"))
MIN_UTTER_SEC = float(os.environ.get("RAIL_MEDIA_MIN_UTTER_SEC", "0.8"))

_GHOST = re.compile(
    r"thank you for watching|thanks for watching|subscribe|bankrupt\.com|"
    r"www\.|♪|字幕|\[music\]|\(music\)|applause|commercial break|"
    r"committee of infecti|american academy|studies involving|"
    r"five million people|link between vaccines",
    re.I,
)


@dataclass
class _Turn:
    ts: float
    dur: float
    gap_before: float  # sec since previous turn end (0 if first)
    rms_db: float
    text: str
    person_id: str | None


@dataclass
class MediaGateState:
    phase: str = "dead"  # dead | characterize | clear_human | media_hold
    char_start: float = 0.0
    last_speech_end: float = 0.0
    last_turn_end: float = 0.0
    turns: deque = field(default_factory=lambda: deque(maxlen=40))
    verdict: str = "unsure"  # unsure | human | media
    reason: str = ""
    updated_ts: float = 0.0


_lock = threading.Lock()
_state = MediaGateState()


def _score_window(
    turns: list[_Turn],
    window_sec: float,
    *,
    room_hc: int = 0,
    named_hc: int = 0,
    emb_top_sim: float | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Return (verdict, reason, metrics).

    room_hc: raw presence headcount (may include U1/U2 ghosts).
    named_hc: only confirmed/provisional *named* people — used for media bar bias.
    emb_top_sim: best house-bank cosine this turn (weak → media lean).
    """
    if not turns:
        return "unsure", "no_turns", {}
    speech = sum(t.dur for t in turns)
    pauses = [t.gap_before for t in turns if t.gap_before >= PAUSE_MIN_SEC]
    n_pause = len(pauses)
    pause_mass = sum(pauses)
    span = max(window_sec, 0.1)
    pause_frac = pause_mass / span
    ghost_n = sum(1 for t in turns if _GHOST.search(t.text or ""))
    # Distinct emb stamps in window — back-and-forth conversation
    speakers = sorted({(t.person_id or "").strip().lower() for t in turns if t.person_id})
    n_speakers = len(speakers)
    # speaker handoffs (adjacent turns, both stamped, different ids)
    handoffs = 0
    for a, b in zip(turns, turns[1:]):
        pa = (a.person_id or "").strip().lower()
        pb = (b.person_id or "").strip().lower()
        if pa and pb and pa != pb:
            handoffs += 1
    long_chunks = sum(1 for t in turns if t.dur >= MEDIA_CHUNK_MAX_SEC)

    metrics: dict[str, Any] = {
        "speech": round(speech, 2),
        "n_pause": n_pause,
        "pause_frac": round(pause_frac, 3),
        "ghost_n": ghost_n,
        "n_turns": len(turns),
        "n_speakers": n_speakers,
        "handoffs": handoffs,
        "speakers": speakers,
        "long_chunks": long_chunks,
        "emb_top_sim": emb_top_sim,
        "named_hc": named_hc,
        "room_hc": room_hc,
    }

    # Multi-speaker / alternating stamps = live conversation, not TV monologue
    if n_speakers >= 2 or handoffs >= 1:
        return "human", "multi_speaker_conversation", metrics

    if ghost_n >= 1:
        return "media", "asr_media_ghosts", metrics

    # Sustained dense monologue across several ear chunks with almost no pauses.
    # Bias only when *named* people ≥2 (U1/U2 ghosts must not raise the bar).
    media_speech_need = MEDIA_MIN_SPEECH_SEC
    media_turns_need = MEDIA_MIN_TURNS
    if named_hc >= 2:
        media_speech_need = max(media_speech_need, 32.0)
        media_turns_need = max(media_turns_need, 3)
        metrics["named_hc_bias"] = named_hc
    if (
        speech >= media_speech_need
        and len(turns) >= media_turns_need
        and n_pause < HUMAN_MIN_PAUSES
        and pause_frac <= MEDIA_MAX_PAUSE_FRAC
        and handoffs == 0
        and n_speakers <= 1
    ):
        return "media", "dense_continuous", metrics

    # One or two maxed ear chunks, no pauses, weak/no emb match → media
    # Occupied room: a long weak-emb chunk is a hoarse person (laryngitis
    # 2026-08-24: 18.6s, sim 0.40, named_hc=1), not TV. Keep this rule for
    # empty-room TV/podcast so it cannot poison the bank.
    weak_emb = emb_top_sim is None or float(emb_top_sim) < 0.50
    occupied = named_hc >= 1 or room_hc >= 1
    if (
        not occupied
        and long_chunks >= 1
        and speech >= MEDIA_CHUNK_MAX_SEC
        and n_pause == 0
        and handoffs == 0
        and weak_emb
        and (len(turns) >= 2 or speech >= 11.5)
    ):
        return "media", "long_chunk_weak_emb", metrics

    # clear human: multiple natural pauses + enough speech
    if n_pause >= HUMAN_MIN_PAUSES and speech >= 2.0:
        return "human", "conversational_pauses", metrics

    # single long human sentence with one gap still human-leaning
    if n_pause >= 1 and speech >= 3.0 and pause_frac >= 0.05:
        return "human", "pause_present", metrics

    return "unsure", "need_more_turns", metrics


def observe_turn(
    *,
    duration_s: float | None,
    rms_db: float | None = None,
    text: str = "",
    person_id: str | None = None,
    now: float | None = None,
    room_hc: int | None = None,
    named_hc: int | None = None,
    emb_top_sim: float | None = None,
) -> dict[str, Any]:
    """Feed one finished ear turn into the media gate. Call from stamp/promote path."""
    global _state
    now = float(now if now is not None else time.time())
    dur = float(duration_s or 0.0)
    rms = float(rms_db if rms_db is not None else -20.0)

    with _lock:
        st = _state
        # Dead air reset
        if st.last_speech_end and (now - st.last_speech_end) >= DEAD_AIR_SEC:
            st.phase = "dead"
            st.turns.clear()
            st.verdict = "unsure"
            st.reason = "dead_air_reset"
            st.char_start = 0.0

        # No-dead-air / silence: ignore for characterization & promote
        if rms < MIN_RMS_DB or dur < MIN_UTTER_SEC:
            st.updated_ts = now
            return {
                "phase": st.phase,
                "verdict": st.verdict,
                "reason": "dead_or_short",
                "promote_ok": False,
                "media_on": st.verdict == "media",
                "rms_db": rms,
                "dur": dur,
            }

        gap = 0.0
        if st.last_turn_end > 0:
            gap = max(0.0, now - dur - st.last_turn_end)
        # if first speech after dead → open characterize window
        if st.phase in ("dead",) or st.char_start <= 0:
            st.phase = "characterize"
            st.char_start = now
            st.verdict = "unsure"
            st.reason = "char_window_open"
            st.turns.clear()
            gap = 0.0

        st.turns.append(
            _Turn(
                ts=now,
                dur=dur,
                gap_before=gap,
                rms_db=rms,
                text=text or "",
                person_id=person_id,
            )
        )
        st.last_speech_end = now
        st.last_turn_end = now
        st.updated_ts = now

        # Score window (turns in last CHAR_WINDOW_SEC)
        recent = [t for t in st.turns if (now - t.ts) <= CHAR_WINDOW_SEC + 1.0]
        elapsed = now - st.char_start if st.char_start else 0.0
        hc = int(room_hc or 0)
        nhc = int(named_hc if named_hc is not None else 0)
        verdict, reason, metrics = _score_window(
            recent,
            min(elapsed, CHAR_WINDOW_SEC) or CHAR_WINDOW_SEC,
            room_hc=hc,
            named_hc=nhc,
            emb_top_sim=emb_top_sim,
        )

        prev_phase = st.phase
        # Sticky clear_human until dead air or dense media returns
        if st.phase == "clear_human" and verdict != "media":
            st.verdict = "human"
            st.reason = st.reason or reason
        elif verdict == "media":
            st.phase = "media_hold"
            st.verdict = "media"
            st.reason = reason
        elif verdict == "human":
            st.phase = "clear_human"
            st.verdict = "human"
            st.reason = (
                "human_after_media_hold" if prev_phase == "media_hold" else reason
            )
        else:
            if st.phase == "clear_human":
                pass  # sticky human
            elif elapsed >= CHAR_WINDOW_SEC:
                # timed out still unsure → stay closed for promote, but NOT media_on
                st.phase = "media_hold"
                st.verdict = "unsure"
                st.reason = "char_timeout_unsure"
            else:
                st.phase = "characterize"
                st.verdict = "unsure"
                st.reason = reason

        promote_ok = st.phase == "clear_human" and st.verdict == "human"
        media_on = st.verdict == "media"
        return {
            "phase": st.phase,
            "verdict": st.verdict,
            "reason": st.reason,
            "promote_ok": promote_ok,
            "media_on": media_on,
            "elapsed_char": round(elapsed, 2),
            "char_window_sec": CHAR_WINDOW_SEC,
            "metrics": metrics,
            "n_turns": len(recent),
            "dead_air_sec": DEAD_AIR_SEC,
            "min_rms_db": MIN_RMS_DB,
        }


def status() -> dict[str, Any]:
    with _lock:
        st = _state
        # Dial green only for true media verdict — not "unsure" hold / char timeout
        media_on = st.verdict == "media"
        station = _media_station_from_geom()
        return {
            "ok": True,
            "phase": st.phase,
            "verdict": st.verdict,
            "reason": st.reason,
            "promote_ok": st.phase == "clear_human" and st.verdict == "human",
            "media_on": media_on,
            "media_station": station,
            "media_label": "speakers locus (blanked sit; dial ×)",
            "char_window_sec": CHAR_WINDOW_SEC,
            "dead_air_sec": DEAD_AIR_SEC,
            "n_turns": len(st.turns),
            "updated_ts": st.updated_ts,
        }


def reset() -> None:
    global _state
    with _lock:
        _state = MediaGateState()
