"""Continuous ear open — VAD-endpointed capture + STT until idle or max timeout.

Spine: one open session owns the mic (shared lock with /v1/listen).
Presence usefulness: every speech chunk is stamped against room roster without
a button press per utterance.

History (the User / Fable 2026-07-31 → 2026-08-01):
  Fixed wall-clock 3.0 s blocks guillotined phrases, bare-wake rate ~72%, and
  dual-engine agreement on fragments. Museum jasper used ~1.5 s silence EOM;
  ``cli.cmd_utter`` already had the right shape. This loop now packs frames with
  WebRTC VAD (+ energy floor) until hangover silence, then decodes once.
"""

from __future__ import annotations

from .settings import url as _url  # this install's addresses (the shared port table, 2026-10-10)

import os
import queue
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

import numpy as np

from . import audio as audio_mod
from .config import Config
from .vad import detect_speech_spans
from . import phone_mic as _phone_mic   # 2026-10-19: the phone as the second mic; CRISSA_EAR_PHONE_MIC=1
from . import turn_end as _turn_end   # round 17: his turn ends sooner (turn_end.py); kill CRISSA_EAR_SPECULATE=0



# WHEN EACH UTTERANCE WAS SPOKEN (round 13, .101 2026-10-12): the decode runs after the hangover and behind a queue, so a
# turn's own `ts` is seconds after its speech. The decoder publishes the utterance's speech window to the handler it
# calls (self_echo reads it: an echo of her line is judged against WHEN it was heard, not when it was decoded).
_DECODE_META = threading.local()


# A BARGE-IN IS HEARD WITHIN SECONDS (round 14, .101: "hey Chris, stop!" sat inside a chunk of her reading that ran on
# until the reading paused, up to the 20-30 s cap, so she read on). While one of her lines is on the air an utterance is
# closed at ON_AIR_MAX_S: her echo in short pieces (the CPU veto skips those), and a person's words over her decoded
# within a few seconds. Kill: CRISSA_EAR_ON_AIR_MAX_S=0.
ON_AIR_MAX_S = float(os.environ.get("CRISSA_EAR_ON_AIR_MAX_S", "4.0") or 0)
_AIR = {"t": 0.0, "on": False}


def her_air_now(now: float | None = None) -> bool:
    """Is one of her lines on the air (house-speak's gate, <home>/house-tts-gate.json), read at most twice a second."""
    now = time.time() if now is None else float(now)
    if now - _AIR["t"] < 0.5:
        return bool(_AIR["on"])
    on = False
    try:
        import json as _json

        from .home import HOME as _H

        g = _json.loads((_H / "house-tts-gate.json").read_text(encoding="utf-8"))
        at, until = float(g.get("air_at") or 0.0), float(g.get("air_until") or 0.0)
        on = bool(at and at - 0.3 <= now and (now <= until + 0.6 if until else bool(g.get("speaking"))))
    except Exception:  # noqa: BLE001 - no gate, no air
        on = False
    _AIR.update(t=now, on=on)
    return on


# A SHORT LINE OVER HERS CLOSES SOONER (round 16, .101 2026-10-13 00:40:39: "Crissa, stop." waited ~1.9 s after his last
# word for the chunk to close - the session's 1.8 s hangover - before the decode even began). While one of her lines is
# on the air an utterance of ON_AIR_SHORT_S or less closes after ON_AIR_HANGOVER_S of quiet: a stop or a hold is
# decoded at once, and a longer sentence of his keeps the session's hangover. Kill: CRISSA_EAR_ON_AIR_HANGOVER_S=0.
ON_AIR_HANGOVER_S = float(os.environ.get("CRISSA_EAR_ON_AIR_HANGOVER_S", "0.6") or 0)
ON_AIR_SHORT_S = float(os.environ.get("CRISSA_EAR_ON_AIR_SHORT_S", "3.0") or 0)


def on_air_hangover(hang: float, utter_s: float) -> float:
    """The silence that closes this utterance: ON_AIR_HANGOVER_S for a short one while she is on the air, else `hang`."""
    if ON_AIR_HANGOVER_S > 0 and utter_s <= ON_AIR_SHORT_S and hang > ON_AIR_HANGOVER_S and her_air_now():
        return ON_AIR_HANGOVER_S
    return hang


def on_air_cap(max_utter: float) -> float:
    """The utterance cap now: ON_AIR_MAX_S while she is on the air, else the ear's own."""
    if ON_AIR_MAX_S > 0 and her_air_now():
        return min(float(max_utter), ON_AIR_MAX_S)
    return float(max_utter)


def _id_parallel() -> bool:
    """Today's voice print beside the decode (Security On), or not (ID-only / Off name the speaker after the turn is
    sent: id_trail.py, round 17)."""
    try:
        from . import id_trail
        return id_trail.parallel_ok()
    except Exception:  # noqa: BLE001
        return True


def decode_meta() -> dict:
    """The utterance being decoded on this thread: {"speech_start_ts", "speech_end_ts", ...} or {}."""
    m = getattr(_DECODE_META, "meta", None)
    return m if isinstance(m, dict) else {}

class OnsetHold:
    """Keep ~1.2 s of frames so a VAD-missed first word still reaches ASR.

    ``open_frames`` still requires consecutive speech ticks before an
    utterance opens (a lone 0.3 s tick is fan/hum). Pre-roll is only
    consumed on that open, so quiet onset (laryngitis, whisper) is not
    thrown away when the second frame misses.
    """

    def __init__(self, *, open_frames: int = 2, preroll_frames: int = 3) -> None:
        self.open_frames = max(1, int(open_frames))
        n = max(1, int(preroll_frames))
        self.preroll: deque = deque(maxlen=n)
        self.consec = 0
        self.heard = False

    def push(self, frame: Any, has_speech: bool) -> str:
        self.preroll.append(frame)
        _turn_end.note_frame(has_speech)           # round 17: when he was last heard (speech_at)
        if has_speech:
            self.consec += 1
            if not self.heard:
                if self.consec < self.open_frames:
                    return "hold"
                self.heard = True
                return "open"
            return "speech"
        self.consec = 0
        if self.heard:
            return "hangover"
        return "idle"

    def seed(self) -> list:
        return list(self.preroll)

    def reset_utterance(self) -> None:
        self.heard = False
        self.consec = 0


def _decode_heal_on() -> bool:
    """THE EAR NEVER STRANDS ITS QUEUE (round 31, .101 2026-10-18 twice after a fresh Setup: decoding true, queue 2,
    chunks 0, every later utterance dropped until a restart). Kill RAIL_STT_DECODE_HEAL=0."""
    return os.environ.get("RAIL_STT_DECODE_HEAL", "1").strip().lower() not in ("0", "off", "false", "no")


class EarLoop:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._decode_q: queue.Queue = queue.Queue()
        self._decode_thread: Optional[threading.Thread] = None
        self._state: dict[str, Any] = {
            "open": False,
            "started_ts": None,
            "mode": "vad",
            "frame_sec": 0.3,
            # the User 2026-08-06: 12s max_utter split long free-air into 2 posts
            # ("cut off" + double log). Default 20; cap still 30 in run loop.
            "hangover_sec": float(os.environ.get("RAIL_STT_HANGOVER_SEC", "1.8")),
            "max_utter_sec": float(os.environ.get("RAIL_STT_MAX_UTTER_SEC", "20")),
            "chunk_sec": float(os.environ.get("RAIL_STT_MAX_UTTER_SEC", "20")),
            "idle_timeout_sec": 45.0,
            "max_open_sec": 600.0,
            "chunks": 0,
            "speech_chunks": 0,
            "last_text": None,
            "last_person_id": None,
            "last_reason": None,
            "last_error": None,
            "last_duration_s": None,
            "closed_reason": None,
            "idle_sec": 0.0,
            # Live capture vs ASR. Mouth waits on these so Cosy does not
            # start while he is still talking (or while decode has the loop).
            "in_utterance": False,
            "decoding": False,
            "decode_queued": 0,
            "decode_dropped": 0,
            # CP Mic OFF swallows pierce/ASR with no turn. Count + rate-limit
            # so a wake word with Voice OFF is visible on /health, not "broken".
            "mic_off_drop": 0,
            "mic_off_last": None,
            "mic": None,
            "mic_why": None,
            "mic_near_db": None,
            "mic_room_db": None,
            "pierce_ts": None,
        }
        # LISTENING cache from :8194/health. Not in _state — open() rebuilds that.
        self._hear_lock = threading.Lock()
        self._hear: dict[str, Any] = {"open": None, "ts": 0.0, "barge": None}
        self._probe = None                      # round 17: the barge probe (barge.py), made at the first open

    def _wake_hangover_sec(self) -> float:
        """Paused vocative: one silence frame by default. No 0.4 floor."""
        raw = (os.environ.get("RAIL_STT_WAKE_HANGOVER_SEC") or "0.3").strip()
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = 0.3
        return max(0.0, min(4.0, v))

    def _engaged_hangover_sec(self) -> float:
        """In-session sentences: glue Silero dips. Clamp 0.4–4."""
        raw = (os.environ.get("RAIL_STT_HANGOVER_SEC") or "").strip()
        v: float | None = None
        if raw:
            try:
                v = float(raw)
            except (TypeError, ValueError):
                v = None
        if v is None:
            with self._lock:
                v = float(self._state.get("hangover_sec") or 1.8)
        # round 17: turn.end_s (0.6 s) in a pack with speculation on, not the 2.0 s (turn_end.py)
        return _turn_end.engaged_hangover(max(0.4, min(4.0, float(v))))

    def _hear_session_open(self) -> bool | None:
        """True/False from ear-bridge; None if never fetched.

        Cache 0.2 s, GET timeout 0.05 s. Fetch fail keeps last known.
        Pytest skips HTTP unless RAIL_STT_HEAR_HEALTH_TEST is set — otherwise
        unit tests would follow the live room session.
        """
        now = time.time()
        with self._hear_lock:
            cached = self._hear.get("open")
            ts = float(self._hear.get("ts") or 0.0)
            if ts and (now - ts) < 0.2:
                return cached if cached is None else bool(cached)
        test_opt = os.environ.get("RAIL_STT_HEAR_HEALTH_TEST", "").strip().lower()
        if os.environ.get("PYTEST_CURRENT_TEST") and test_opt not in (
            "1",
            "true",
            "yes",
        ):
            with self._hear_lock:
                cached = self._hear.get("open")
                return cached if cached is None else bool(cached)
        url = os.environ.get(
            "RAIL_STT_HEAR_HEALTH", _url("bridge", path="/health")
        )
        got: bool | None = None
        try:
            import json
            import urllib.request

            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=0.05) as r:
                data = json.loads(r.read() or b"{}")
            if isinstance(data, dict) and "session_open" in data:
                got = bool(data.get("session_open"))
            barge_on = bool(data.get("barge")) if isinstance(data, dict) else None
        except Exception:
            got = None
            barge_on = None
        with self._hear_lock:
            if got is not None:
                self._hear["open"] = got
            self._hear["barge"] = barge_on        # round 17: no answer is no barge (the old path)
            self._hear["ts"] = now
            cached = self._hear.get("open")
            return cached if cached is None else bool(cached)

    def _hangover_sec_now(self) -> float:
        """Paused → wake hangover. Listening / unknown → engaged 1.8."""
        sess = self._hear_session_open()
        if sess is False:
            return self._wake_hangover_sec()
        return self._engaged_hangover_sec()

    def _engaged_open_frames(self) -> int:
        """In-session: 2 consecutive speech frames (drop-in). Lone ticks stay idle."""
        raw = (os.environ.get("RAIL_STT_EAR_OPEN_FRAMES") or "1").strip()
        try:
            v = int(raw)
        except (TypeError, ValueError):
            v = 1
        return max(1, min(8, v))

    def _wake_open_frames(self) -> int:
        """Paused vocative: one speech frame. A short 'Hey Crissa' is often a
        single 0.3 s Silero hit after AEC; 2 frames meant it never opened and
        the second attempt was the one that woke (measured 2026-08-29)."""
        raw = (os.environ.get("RAIL_STT_WAKE_OPEN_FRAMES") or "1").strip()
        try:
            v = int(raw)
        except (TypeError, ValueError):
            v = 1
        return max(1, min(8, v))

    def _open_frames_now(self) -> int:
        sess = self._hear_session_open()
        if sess is False:
            return self._wake_open_frames()
        return self._engaged_open_frames()

    # ── round 17: the first word over her line stops her, straight from the ASR (barge.py) ──────────────────────────
    def _barge_armed(self) -> bool:
        """Is one of her conversation lines on the air, with the bridge ready to cut it (its /health `barge`)?"""
        self._hear_session_open()
        with self._hear_lock:
            return bool(self._hear.get("barge"))

    def _barge_probe(self, cfg: Config):
        if self._probe is None:
            from .barge import Probe

            self._probe = Probe(str(getattr(cfg, "live_ear_url", "") or ""))
        return self._probe

    def _barge_frame(self, probe, bp: dict, frames, action: str, frame: float, sr: int, t_cap: float,
                     seq: int = 0) -> None:
        """One frame of an episode: a look at its newest audio while she is on the air; an episode that never opened
        an utterance ends at the next idle frame."""
        from . import barge

        if action in ("hold", "open", "speech"):
            if bp.get("id") is None:
                if not self._barge_armed():
                    return
                bp.update(id=probe.begin(t_cap), start=seq - barge.LEAD_FRAMES, onset=t_cap - frame)
            # Mic OFF (the Control Panel's voice lock): no ASR at all, looks included
            if probe.wants(bp["id"]) and self._barge_armed() and not self._voice_locked_off():
                wav, t0, t1 = barge.window(frames, int(bp["start"]), frame)
                if len(wav):
                    probe.offer(bp["id"], wav, sr, t0, t1, bp.get("onset"))
        elif action == "idle" and bp.get("id") is not None:
            eid = bp["id"]
            bp.update(id=None, start=0.0, onset=None)
            probe.end(eid, "idle")                 # never opened an utterance: the probe tells the bridge if it cut

    def _barge_meta(self, probe, bp: dict, mic_meta: dict[str, Any] | None) -> dict[str, Any] | None:
        """The utterance is flushed: its episode ends and travels with it to the decode (`barge_episode`), which
        settles the episode's last look and then knows whether it cut her."""
        eid = bp.get("id")
        bp.update(id=None, start=0.0, onset=None)
        if probe is None or eid is None:
            return mic_meta
        probe.end(eid, "decode")
        return {**(mic_meta or {}), "barge_episode": eid}

    def _barge_dropped(self, meta: dict[str, Any] | None, why: str) -> None:
        """An utterance that will not be decoded: if its episode cut her, the bridge hears that no turn comes."""
        eid = (meta or {}).get("barge_episode")
        probe = self._probe
        if not eid or probe is None:
            return

        def _go() -> None:
            probe.settle(eid)
            if probe.fired(eid):
                probe.tell_end(str(eid), {}, why=why)

        threading.Thread(target=_go, name="ear-barge-drop", daemon=True).start()

    def status(self) -> dict[str, Any]:
        with self._lock:
            s = dict(self._state)
        if s.get("open") and s.get("started_ts"):
            s["open_for_sec"] = round(time.time() - float(s["started_ts"]), 1)
        else:
            s["open_for_sec"] = 0
        # Overlay live hangover (paused vs engaged). Do not hold _lock —
        # it is not reentrant, and _engaged_hangover_sec may take it.
        sess = self._hear_session_open()
        s["session_open"] = sess
        s["barge"] = dict(self._probe.stats) if self._probe is not None else None   # round 17: the looks (barge.py)
        s["hangover_sec"] = self._hangover_sec_now()
        s["open_frames"] = self._open_frames_now()
        try:
            from .vad import silero_threshold, vad_engine

            s["vad"] = vad_engine()
            s["silero_thr"] = silero_threshold()
        except Exception:
            s["vad"] = None
            s["silero_thr"] = None
        s.update(_turn_end.state())               # round 17: speech_at, for the bridge's rule for her mouth
        return s

    def open(
        self,
        *,
        cfg: Config,
        process_chunk: Callable[[Any, float], dict[str, Any]],
        listen_lock: threading.Lock,
        chunk_sec: float = 12.0,
        idle_timeout_sec: float = 45.0,
        max_open_sec: float = 600.0,
        frame_sec: float = 0.3,
        hangover_sec: float = 1.5,
        max_utter_sec: float | None = None,
        mode: str = "vad",
    ) -> dict[str, Any]:
        """Start continuous listen. process_chunk(wav_float, sr) → turn dict.

        Never call status() while holding _lock — Lock is not reentrant
        (deadlocked /health and /v1/listen on first ear open).

        mode:
          * ``vad`` (default) — frame capture + silence hangover endpointing
          * ``fixed`` — legacy fixed windows of ``chunk_sec`` (escape hatch)
        """
        # Legacy clients sent chunk_sec=3 meaning fixed window; for vad mode that
        # value is too small for a full phrase — promote to env default (20s).
        if max_utter_sec is None:
            env_mu = os.environ.get("RAIL_STT_MAX_UTTER_SEC", "").strip()
            default_mu = float(env_mu) if env_mu else 20.0
            if mode == "fixed":
                max_utter_sec = float(chunk_sec) if chunk_sec else default_mu
            else:
                max_utter_sec = max(float(chunk_sec), 8.0) if chunk_sec else default_mu
                if chunk_sec is not None and float(chunk_sec) <= 4.0:
                    # Dashboard historically sent 3 — treat as "use defaults"
                    max_utter_sec = default_mu

        already = False
        with self._lock:
            if self._state.get("open") and self._thread and self._thread.is_alive():
                already = True
            else:
                self._stop.clear()
                mic_off_keep = int(self._state.get("mic_off_drop") or 0)
                mic_off_last = self._state.get("mic_off_last")
                self._state = {
                    "open": True,
                    "started_ts": time.time(),
                    "mode": mode if mode in ("vad", "fixed") else "vad",
                    "frame_sec": float(frame_sec),
                    "hangover_sec": float(hangover_sec),
                    "max_utter_sec": float(max_utter_sec),
                    "chunk_sec": float(max_utter_sec),
                    "idle_timeout_sec": float(idle_timeout_sec),
                    "max_open_sec": float(max_open_sec),
                    "chunks": 0,
                    "speech_chunks": 0,
                    "last_text": None,
                    "last_person_id": None,
                    "last_reason": None,
                    "last_error": None,
                    "last_duration_s": None,
                    "closed_reason": None,
                    "idle_sec": 0.0,
                    "in_utterance": False,
                    "decoding": False,
                    "decode_queued": 0,
                    "mic_off_drop": mic_off_keep,
                    "mic_off_last": mic_off_last,
                }
                self._ensure_decoder()
                self._thread = threading.Thread(
                    target=self._run,
                    kwargs={
                        "cfg": cfg,
                        "process_chunk": process_chunk,
                        "listen_lock": listen_lock,
                    },
                    name="rail-stt-ear-loop",
                    daemon=True,
                )
                self._thread.start()
        return {"ok": True, "already_open": already, **self.status()}

    def close(self, reason: str = "manual") -> dict[str, Any]:
        self._stop.set()
        join_s = 3.0
        with self._lock:
            if self._state.get("open"):
                self._state["open"] = False
                self._state["closed_reason"] = reason
            join_s = max(
                2.0,
                float(self._state.get("frame_sec") or 0.3) + 2.0,
                float(self._state.get("max_utter_sec") or 12) * 0.25,
            )
            t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=join_s)
        dt = self._decode_thread
        try:
            if not _decode_heal_on() or (dt is not None and dt.is_alive()):
                self._decode_q.put_nowait(None)    # round 31: a marker only for a decode thread that will take it
        except Exception:
            pass
        if dt and dt.is_alive() and dt is not threading.current_thread():
            dt.join(timeout=2.0)
        with self._lock:
            self._state["in_utterance"] = False
            self._state["decoding"] = False
        return {"ok": True, **self.status()}

    def _ensure_decoder(self) -> None:
        t = self._decode_thread
        if t is not None and t.is_alive():
            return
        self._decode_thread = threading.Thread(
            target=self._decode_loop, name="rail-stt-decode", daemon=True
        )
        self._decode_thread.start()

    def _heal_decoder(self) -> None:
        """Round 31: the queue is counted full but no decode thread is alive (it took a stale stop marker and left):
        start one and count what is really waiting, so the ear does not drop every later utterance until a restart."""
        t = self._decode_thread
        if t is not None and t.is_alive():
            return
        with self._lock:
            counted = int(self._state.get("decode_queued") or 0)
        if counted <= 0:
            return
        self._ensure_decoder()
        with self._lock:
            waiting = sum(1 for it in list(self._decode_q.queue) if it is not None)
            self._state["decode_queued"] = waiting
            self._state["decoding"] = waiting > 0
            self._state["decode_healed"] = int(self._state.get("decode_healed") or 0) + 1
        print("[rail-stt] decode: no decoder for %d queued, started one (%d waiting)" % (counted, waiting), flush=True)

    def _decode_loop(self) -> None:
        while True:
            try:
                item = self._decode_q.get(timeout=0.3)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            if item is None:
                if _decode_heal_on() and not self._stop.is_set():
                    continue           # a stop marker left by a close() nobody was decoding for: not ours (round 31)
                return
            wav, sr, dur, process_chunk = item[:4]
            meta = item[4] if len(item) > 4 and isinstance(item[4], dict) else {}
            voice_fut = item[5] if len(item) > 5 else None
            _eid = meta.get("barge_episode")
            if _eid and self._probe is not None:
                self._probe.settle(_eid)           # round 17: its last look has answered; did it cut her?
                if self._probe.fired(_eid):
                    meta["barge_id"] = _eid        # the ring row carries it (server.py): the turn that cut her
                    from .barge import TRIM_LEAD_S, trim_on

                    _hf, _s0 = self._probe.heard_from(_eid), meta.get("speech_start_ts")
                    if trim_on() and _hf and _s0 and _hf - TRIM_LEAD_S > float(_s0):
                        # from where someone was first heard: her echo before it is not decoded with their words
                        _cut = int((_hf - TRIM_LEAD_S - float(_s0)) * int(sr))
                        if 0 < _cut < len(wav) - int(0.3 * int(sr)):
                            wav = wav[_cut:]
                            meta["speech_start_ts"] = round(float(_s0) + _cut / float(sr), 3)
                            meta["barge_trimmed_s"] = round(_cut / float(sr), 2)
                else:
                    from .barge import skip_echo_on

                    if skip_echo_on() and self._probe.hers_through(_eid, meta.get("speech_end_ts")):
                        # her own line heard back, every word of it looked at already (barge.skip_echo_on)
                        with self._lock:
                            self._state["barge_echo_skipped"] = int(self._state.get("barge_echo_skipped") or 0) + 1
                        self._apply_turn({"reason": "barge_echo_skip"}, float(dur))
                        continue
            _DECODE_META.meta = meta
            try:
                try:
                    turn = process_chunk(wav, sr, voice_fut=voice_fut)
                except TypeError:
                    turn = process_chunk(wav, sr)
                if isinstance(turn, dict):
                    for _k in ("speech_start_ts", "speech_end_ts"):
                        if meta.get(_k) is not None and turn.get(_k) is None:
                            turn[_k] = meta[_k]
                if isinstance(turn, dict) and meta.get("mic"):
                    st = turn.get("stamp") if isinstance(turn.get("stamp"), dict) else None
                    if st is not None:
                        st["mic"] = meta.get("mic")
                        st["mic_why"] = meta.get("why")
                        st["mic_near_db"] = meta.get("near_db")
                        st["mic_room_db"] = meta.get("room_db")
                    turn["mic"] = meta.get("mic")
                if isinstance(turn, dict) and meta.get("phone_mic"):
                    turn["phone_mic"] = meta["phone_mic"]        # the two mics' scores and the pick (phone_mic.py)
            except Exception as e:
                with self._lock:
                    self._state["last_error"] = f"decode: {e}"[:200]
                    self._state["chunks"] = int(self._state.get("chunks") or 0) + 1
                    self._state["last_duration_s"] = round(float(dur), 2)
                turn = {}
            finally:
                _DECODE_META.meta = None
            if meta.get("barge_id") and self._probe is not None:
                self._probe.tell_end(str(meta["barge_id"]), turn if isinstance(turn, dict) else {})
            self._apply_turn(turn if isinstance(turn, dict) else {}, float(dur))

    def _capture_frame(
        self,
        *,
        seconds: float,
        cfg: Config,
        listen_lock: threading.Lock,
    ) -> tuple[Optional[np.ndarray], Optional[str]]:
        """Exclusive mic frame. Returns (wav, error)."""
        if not listen_lock.acquire(blocking=False):
            return None, "mic_busy"
        try:
            try:
                wav = audio_mod.record_seconds(
                    seconds, mic=cfg.mic, sample_rate=cfg.sample_rate
                )
                # Rolling security cache. Tapped HERE rather than at utterance
                # flush so it holds the room, not only what decoded as speech --
                # an intrusion is recognised after the words, by which point the
                # audio is otherwise gone. Never raises; the ear outranks it.
                try:
                    from .voice_cache import offer as _vc_offer

                    _vc_offer(wav, cfg.sample_rate)
                except Exception:
                    pass
                return wav, None
            except Exception as e:
                if isinstance(e, audio_mod.DeadRecorder):
                    # round 46: a dead recorder returns at once; the frame's own time passes before the next try, so
                    # the loop's idle count is real seconds and it does not spin (audio.DeadRecorder)
                    self._stop.wait(max(0.0, float(seconds)))
                return None, str(e)[:200]
        finally:
            if listen_lock.locked():
                try:
                    listen_lock.release()
                except RuntimeError:
                    pass

    def _capture_near(
        self, *, seconds: float, sr: int, device: str | None
    ) -> Optional[np.ndarray]:
        """Desk capsule. Not the listen_lock — room ear stays exclusive."""
        if not device or os.environ.get("PYTEST_CURRENT_TEST"):
            return None
        try:
            return audio_mod.record_seconds(
                seconds, mic=device, sample_rate=sr
            )
        except Exception:
            return None

    def _apply_turn(self, turn: dict[str, Any], dur: float) -> None:
        text = (turn.get("text") or "").strip()
        with self._lock:
            self._state["chunks"] = int(self._state.get("chunks") or 0) + 1
            self._state["last_reason"] = turn.get("reason")
            self._state["last_error"] = turn.get("error")
            self._state["last_duration_s"] = round(dur, 2)
            q = max(0, int(self._state.get("decode_queued") or 1) - 1)
            self._state["decode_queued"] = q
            self._state["decoding"] = q > 0
            if text:
                self._state["speech_chunks"] = (
                    int(self._state.get("speech_chunks") or 0) + 1
                )
                self._state["last_text"] = text[:200]
                self._state["last_person_id"] = turn.get("person_id")
                self._state["idle_sec"] = 0.0
                self._state["cache_release_ts"] = time.time() + float(
                    os.environ.get("RAIL_VOICE_CACHE_RELEASE_SEC", "15")
                )

    def _kick_voice_id(self, parts: list, sr: int) -> None:
        """Rank+publish WeSpeaker on the PCM we have. Beats the gimbal."""
        if not parts or not _id_parallel():         # round 17: ID-only / Off name the speaker after the turn
            return
        try:
            wav = np.concatenate(parts).astype(np.float32, copy=False)
            pcm = audio_mod.float_to_pcm16(wav).tobytes()
            from .voice_live import kick

            kick(pcm, int(sr))
        except Exception:
            pass

    def _voice_locked_off(self) -> bool:
        """CP Mic OFF. Dual-mic compare still runs; product surfaces must not."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        try:
            from .house_voice import is_voice_on

            return not is_voice_on()
        except Exception as e:
            # Fail closed. Do not take _lock here (callers may hold it).
            now = time.time()
            last = getattr(self, "_mic_off_err_log_ts", 0.0)
            if (now - last) >= 30.0:
                self._mic_off_err_log_ts = now
                print(
                    f"[rail-stt] mic off — is_voice_on raised "
                    f"{type(e).__name__}: {e}"[:160],
                    flush=True,
                )
            return True

    def _note_mic_off_drop(self, where: str, *, err: str | None = None) -> None:
        """Count + rate-limit a Mic OFF swallow so /health can explain silence."""
        now = time.time()
        with self._lock:
            n = int(self._state.get("mic_off_drop") or 0) + 1
            self._state["mic_off_drop"] = n
            self._state["mic_off_last"] = where
            self._state["mic_off_last_ts"] = now
            if err:
                self._state["mic_off_error"] = err
        last = getattr(self, "_mic_off_log_ts", 0.0)
        if (now - last) < 30.0:
            return
        self._mic_off_log_ts = now
        extra = f" err={err}" if err else ""
        print(
            f"[rail-stt] mic off — drop {where} n={n} (no pierce/ASR){extra}",
            flush=True,
        )

    def _fire_look_impulse(self) -> None:
        """Two-frame ear VAD piercing. Does not aim the camera.

        Presence look_ok refuses source=vad. Posture peek reads pierce_ts.
        Voice OFF: no pierce — that was lifting her head with Mic OFF.
        """
        if self._voice_locked_off():
            self._note_mic_off_drop("pierce")
            return
        with self._lock:
            self._state["look_impulse"] = True
            self._state["pierce_ts"] = time.time()
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        threading.Thread(target=self._post_look_impulse, daemon=True).start()

    def _post_look_impulse(self) -> None:
        """Hearing-only. Do not POST a camera look (station_hour residual-0)."""
        return

    def _flush_utterance(
        self,
        parts: list[np.ndarray],
        *,
        cfg: Config,
        process_chunk: Callable[[Any, float], dict[str, Any]],
        idle_acc: float,
        mic_meta: dict[str, Any] | None = None,
    ) -> tuple[float, bool]:
        """Queue one packed utterance for ASR. Does not block the VAD loop.

        Blocking decode (1–5 s) used to deafen the ear, so Cosy started
        while he was still talking. Capture keeps running; /health
        ``decoding`` / ``in_utterance`` tell the mouth to wait.
        """
        if not parts:
            self._barge_dropped(mic_meta, "no_audio")
            return idle_acc, False
        if self._voice_locked_off():
            # Shure vs Seeed already compared this frame. Do not ASR/publish.
            self._note_mic_off_drop("flush")
            self._barge_dropped(mic_meta, "mic_off")
            return idle_acc, False
        max_q = int(os.environ.get("RAIL_STT_DECODE_Q_MAX", "2"))
        if _decode_heal_on():
            self._heal_decoder()
        with self._lock:
            qnow = int(self._state.get("decode_queued") or 0)
            if qnow >= max_q:
                # TV/media outruns Audio8. Drop this utterance; keep capturing.
                self._state["decode_dropped"] = int(
                    self._state.get("decode_dropped") or 0
                ) + 1
                dropped = True
            else:
                dropped = False
        if dropped:
            self._barge_dropped(mic_meta, "decode_queue_full")
            return idle_acc, False
        wav = np.concatenate(parts).astype(np.float32, copy=False)
        dur = float(len(wav) / max(1, cfg.sample_rate))
        sr = int(cfg.sample_rate)
        _pick: dict[str, Any] = {}
        if _phone_mic.on():
            try:                             # one copy to the ASR: the winner's, from its first word (phone_mic.py)
                _t1 = time.time()
                wav, _pick = _phone_mic.get(sr).pick(_t1 - dur, _t1, wav)
            except Exception as e:  # noqa: BLE001 - the desk's copy, as before
                _pick = {"mic_pick": "desk", "mic_pick_why": f"error: {type(e).__name__}"}
        pcm = audio_mod.float_to_pcm16(wav).tobytes()
        voice_fut = None
        try:
            from .voice_live import kick

            voice_fut = kick(pcm, sr) if _id_parallel() else None   # round 17: not beside the decode in ID-only / Off
        except Exception:
            voice_fut = None
        self._ensure_decoder()
        with self._lock:
            q = int(self._state.get("decode_queued") or 0) + 1
            self._state["decode_queued"] = q
            self._state["decoding"] = True
            self._state["last_duration_s"] = round(dur, 2)
        meta = dict(mic_meta or {})
        if _pick:
            meta["phone_mic"] = _pick
            meta["phone_open"] = bool(_pick.get("phone_open"))
            if _pick.get("mic_pick") == "phone":
                meta.update(mic="phone", why=_pick.get("mic_pick_why"))
        try:
            _now = time.time()
            _hang = getattr(self, "_hang_used", None)
            self._hang_used = None
            meta["speech_end_ts"] = round(_now - float(self._hangover_sec_now() if _hang is None else _hang), 3)
            meta["speech_start_ts"] = round(_now - float(dur), 3)
        except Exception:  # noqa: BLE001 - timing is a hint; the decode never waits on it
            pass
        self._decode_q.put(
            (
                wav,
                sr,
                dur,
                process_chunk,
                meta,
                voice_fut,
            )
        )
        return idle_acc, True

    def _maybe_release_cache(self) -> None:
        """After an utterance, drop PCM once the mouth is quiet (or skip-play)."""
        with self._lock:
            due = float(self._state.get("cache_release_ts") or 0.0)
            if not due or time.time() < due:
                return
            who = self._state.get("last_person_id")
            heard = self._state.get("last_text")
            self._state["cache_release_ts"] = 0.0
        try:
            from .tts_gate import mouth_open

            if mouth_open():
                with self._lock:
                    self._state["cache_release_ts"] = time.time() + 4.0
                return
            from .voice_cache import release as _vc_rel

            _vc_rel({"who": who, "heard": heard, "person_id": who})
        except Exception:
            return

    def _run(
        self,
        *,
        cfg: Config,
        process_chunk: Callable[[Any, float], dict[str, Any]],
        listen_lock: threading.Lock,
    ) -> None:
        mode = str(self._state.get("mode") or "vad")
        frame = max(0.15, min(1.0, float(self._state.get("frame_sec") or 0.3)))
        max_utter = max(
            2.0,
            min(30.0, float(self._state.get("max_utter_sec") or
                            os.environ.get("RAIL_STT_MAX_UTTER_SEC", "20") or 20)),
        )
        idle_lim = max(5.0, float(self._state.get("idle_timeout_sec") or 45))
        max_open = max(30.0, float(self._state.get("max_open_sec") or 600))
        started = float(self._state.get("started_ts") or time.time())
        idle_acc = 0.0
        sr = int(cfg.sample_rate)

        # Quiet house mic (Fable median ~-29 dB) — floor slightly looser than default -45.
        # Live VAD is Silero; this floor only hits the webrtc/energy fallback.
        energy_floor = -42.0
        # Frames of consecutive speech needed to OPEN. Engaged stays at the
        # drop-in (2) so a lone tick is still idle. Paused vocative is 1 —
        # see _open_frames_now. Recomputed every frame; session can flip.

        try:
            if mode == "fixed":
                self._run_fixed(
                    cfg=cfg,
                    process_chunk=process_chunk,
                    listen_lock=listen_lock,
                    chunk=max_utter if max_utter <= 8 else 3.0,
                    idle_lim=idle_lim,
                    max_open=max_open,
                    started=started,
                )
                return

            parts: list[np.ndarray] = []
            # round 17: the barge probe's episode and the frames it looks at (barge.py)
            from . import barge as _barge

            probe = self._barge_probe(cfg) if _barge.on() else None
            bp: dict[str, Any] = {"id": None, "start": 0.0, "onset": None}
            recent: deque = deque(maxlen=int(_barge.WINDOW_S / frame) + 3)
            fseq = 0
            preroll_n = max(1, int(os.environ.get("RAIL_STT_EAR_PREROLL_FRAMES", "4")))
            onset = OnsetHold(
                open_frames=self._open_frames_now(), preroll_frames=preroll_n
            )
            silence_run = 0.0
            speech_n = 0
            impulse_sent = False
            impulse_need = max(2, int(os.environ.get("RAIL_STT_LOOK_SPEECH_FRAMES", "2")))
            held_mic: str | None = None
            mic_meta: dict[str, Any] = {}
            close_dev = None
            try:
                from .mic_select import close_device

                close_dev = close_device()
            except Exception:
                close_dev = None

            while not self._stop.is_set():
                self._maybe_release_cache()
                if time.time() - started >= max_open:
                    if parts and onset.heard:
                        idle_acc, _ = self._flush_utterance(
                            parts,
                            cfg=cfg,
                            process_chunk=process_chunk,
                            idle_acc=idle_acc,
                            mic_meta=self._barge_meta(probe, bp, mic_meta),
                        )
                    self.close(reason="max_open_timeout")
                    return

                near_box: list = [None]
                near_th = None
                if close_dev:
                    near_th = threading.Thread(
                        target=lambda: near_box.__setitem__(
                            0,
                            self._capture_near(
                                seconds=frame, sr=sr, device=close_dev
                            ),
                        ),
                        name="ear-near",
                        daemon=True,
                    )
                    near_th.start()
                wav, err = self._capture_frame(
                    seconds=frame, cfg=cfg, listen_lock=listen_lock
                )
                if near_th is not None:
                    near_th.join(timeout=max(0.4, frame + 0.5))
                if err == "mic_busy":
                    time.sleep(0.15)
                    continue
                if err or wav is None:
                    with self._lock:
                        self._state["last_error"] = (err or "capture")[:200]
                        self._state["chunks"] = int(self._state.get("chunks") or 0) + 1
                    idle_acc += frame
                    with self._lock:
                        self._state["idle_sec"] = round(idle_acc, 1)
                    if idle_acc >= idle_lim:
                        self.close(reason="idle_after_errors")
                        return
                    continue

                from .mic_select import NEAR, ROOM, decide, rms_db

                near_wav = near_box[0]
                pick = decide(
                    near_db=rms_db(near_wav),
                    room_db=rms_db(wav),
                    held=held_mic,
                )
                mic_meta = pick
                if pick.get("mic") != ROOM and near_wav is not None:
                    wav = near_wav
                with self._lock:
                    self._state["mic"] = pick.get("mic")
                    self._state["mic_why"] = pick.get("why")
                    self._state["mic_near_db"] = pick.get("near_db")
                    self._state["mic_room_db"] = pick.get("room_db")
                t_cap = time.time()
                fseq += 1
                recent.append((fseq, t_cap, wav))

                spans = detect_speech_spans(
                    wav,
                    sr,
                    aggressiveness=2,
                    min_speech_s=0.10,
                    pad_s=0.05,
                    energy_floor_db=energy_floor,
                )
                has_speech = bool(spans)
                # THE PHONE AS THE SECOND MIC (2026-10-19, phone_mic.py; CRISSA_EAR_PHONE_MIC=1): its copy of this
                # frame is heard too, and both are scored; the winner's copy is chosen at the flush
                if _phone_mic.on():
                    try:
                        _pm = _phone_mic.get(sr)
                        _ph = _pm.frame(t_cap, frame)
                        if _ph is not None:
                            has_speech = _pm.heard(_ph, bool(detect_speech_spans(
                                _ph, sr, aggressiveness=2, min_speech_s=0.10, pad_s=0.05,
                                energy_floor_db=energy_floor))) or has_speech
                        _pm.score(t_cap, wav, _ph, has_speech)
                    except Exception as e:  # noqa: BLE001 - the desk hears as before
                        with self._lock:
                            self._state["last_error"] = f"phone_mic: {e}"[:200]
                onset.open_frames = self._open_frames_now()
                action = onset.push(wav, has_speech)
                with self._lock:
                    self._state["in_utterance"] = bool(onset.heard)
                if probe is not None:
                    self._barge_frame(probe, bp, recent, action, frame, sr, t_cap, fseq)
                # an utterance that cut her line is the person's turn from then on: no on-air cap, no on-air hangover
                barged = probe is not None and probe.fired(bp.get("id"))

                if action in ("open", "speech"):
                    silence_run = 0.0
                    if action == "open":
                        try:
                            from .voice_cache import arm as _vc_arm

                            _vc_arm()
                        except Exception:
                            pass
                        held_mic = str(mic_meta.get("mic") or ROOM)
                        # Near-field open is already loud; room preroll would splice.
                        if held_mic == NEAR:
                            parts = [wav]
                        else:
                            parts = onset.seed()
                        speech_n = 1
                        impulse_sent = False
                    else:
                        parts.append(wav)
                        speech_n += 1
                    if not impulse_sent and speech_n >= impulse_need:
                        impulse_sent = True
                        # Always call fire — Mic OFF no-ops and counts the drop.
                        self._fire_look_impulse()
                        if not self._voice_locked_off():
                            # Voice guess now — do not wait for hangover/ASR/camera.
                            self._kick_voice_id(parts, sr)
                    utter_s = sum(len(p) for p in parts) / float(sr)
                    # round 17: a look still deciding about this stretch holds the on-air cap a little (barge.CAP_HOLD_S)
                    cap_hold = probe is not None and not barged and probe.holds_cap(bp.get("id"), utter_s, ON_AIR_MAX_S)
                    if utter_s >= (on_air_cap(max_utter) if utter_s >= ON_AIR_MAX_S and not (barged or cap_hold)
                                   else max_utter):
                        idle_acc, _ = self._flush_utterance(
                            parts,
                            cfg=cfg,
                            process_chunk=process_chunk,
                            idle_acc=idle_acc,
                            mic_meta=self._barge_meta(probe, bp, mic_meta),
                        )
                        parts = []
                        onset.reset_utterance()
                        held_mic = None
                        silence_run = 0.0
                        speech_n = 0
                        impulse_sent = False
                        if idle_acc >= idle_lim:
                            self.close(reason="idle_timeout")
                            return
                    continue

                if action == "hold":
                    continue

                if action == "hangover":
                    # Trailing consonants + one Silero miss. Wake (paused)
                    # hangover is env (default 0.3, live 0.6 so "Crissa" isn't
                    # clipped to "Oi"). Engaged LISTENING keeps 1.8 s.
                    # Recompute every silence tick — session can open mid-utterance.
                    parts.append(wav)
                    silence_run += frame
                    hang = self._hangover_sec_now()
                    if not barged:
                        hang = on_air_hangover(hang, sum(len(p) for p in parts) / float(sr) - silence_run)
                    if silence_run >= hang:
                        self._hang_used = hang
                        idle_acc, _ = self._flush_utterance(
                            parts,
                            cfg=cfg,
                            process_chunk=process_chunk,
                            idle_acc=idle_acc,
                            mic_meta=self._barge_meta(probe, bp, mic_meta),
                        )
                        parts = []
                        onset.reset_utterance()
                        held_mic = None
                        silence_run = 0.0
                        speech_n = 0
                        impulse_sent = False
                        if idle_acc >= idle_lim:
                            self.close(reason="idle_timeout")
                            return
                    continue

                idle_acc += frame
                with self._lock:
                    self._state["idle_sec"] = round(idle_acc, 1)
                if idle_acc >= idle_lim:
                    self.close(reason="idle_timeout")
                    return
        finally:
            with self._lock:
                self._state["open"] = False
                if not self._state.get("closed_reason"):
                    self._state["closed_reason"] = "loop_exit"

    def _run_fixed(
        self,
        *,
        cfg: Config,
        process_chunk: Callable[[Any, float], dict[str, Any]],
        listen_lock: threading.Lock,
        chunk: float,
        idle_lim: float,
        max_open: float,
        started: float,
    ) -> None:
        """Legacy fixed-window path (escape hatch via mode=fixed)."""
        chunk = max(1.0, min(8.0, float(chunk)))
        idle_acc = 0.0
        while not self._stop.is_set():
            self._maybe_release_cache()
            if time.time() - started >= max_open:
                self.close(reason="max_open_timeout")
                return
            wav, err = self._capture_frame(
                seconds=chunk, cfg=cfg, listen_lock=listen_lock
            )
            if err == "mic_busy":
                time.sleep(0.3)
                continue
            if err or wav is None:
                with self._lock:
                    self._state["last_error"] = (err or "capture")[:200]
                    self._state["chunks"] = int(self._state.get("chunks") or 0) + 1
                idle_acc += chunk
                if idle_acc >= idle_lim:
                    self.close(reason="idle_after_errors")
                    return
                continue
            idle_acc, _ = self._flush_utterance(
                [wav], cfg=cfg, process_chunk=process_chunk, idle_acc=idle_acc
            )
            if idle_acc >= idle_lim:
                self.close(reason="idle_timeout")
                return


_EAR: Optional[EarLoop] = None


def get_ear_loop() -> EarLoop:
    global _EAR
    if _EAR is None:
        _EAR = EarLoop()
    return _EAR
