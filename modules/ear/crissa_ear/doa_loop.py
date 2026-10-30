"""Live XVF3800 beam → presence.sound, independent of ASR and utterance close.

Same feed as the Oracle lobe: HID DOA, constantly. Not Shure, not the
Seeed PCM used for STT. Default ~5 Hz to match the wall SSE.

Az only. Identity stays on the last utterance stamp (presence merges).
Does not consult media_gate — TV should not darken the lobe.
Skips while Crissa's mouth is open (her sink is not a speaker).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

from .doa import read_doa
from .presence import post_sound_heard


def loop_hz() -> float:
    try:
        hz = float(os.environ.get("RAIL_DOA_HZ", "5"))
    except (TypeError, ValueError):
        hz = 1.0
    return max(0.2, min(hz, 10.0))


def loop_enabled() -> bool:
    return os.environ.get("RAIL_DOA_LOOP", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def should_publish(
    *,
    now: float,
    last_pub: float,
    hz: float,
    ok: bool,
    speech: bool,
    mouth_open: bool,
) -> str | None:
    """Return None to publish, or a skip reason."""
    if not ok:
        return "doa_bad"
    if mouth_open:
        return "mouth_open"
    min_gap = 1.0 / max(hz, 0.2)
    if last_pub and (now - last_pub) < min_gap:
        return "min_gap"
    return None


class DoaLoop:
    def __init__(
        self,
        *,
        read: Callable[[], dict[str, Any]] = read_doa,
        post: Callable[..., Any] = post_sound_heard,
        mouth: Callable[[], bool] | None = None,
    ) -> None:
        self._read = read
        self._post = post
        self._mouth = mouth
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.posts = 0
        self.skips_quiet = 0
        self.skips_mouth = 0
        self.skips_gap = 0
        self.errors = 0
        self.last_az: float | None = None
        self.last_pub_ts: float = 0.0
        self.last_skip: str | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": loop_enabled(),
                "running": bool(self._thread and self._thread.is_alive()),
                "hz": loop_hz(),
                "posts": self.posts,
                "skips_quiet": self.skips_quiet,
                "skips_mouth": self.skips_mouth,
                "skips_gap": self.skips_gap,
                "errors": self.errors,
                "last_az": self.last_az,
                "last_pub_ts": self.last_pub_ts or None,
                "last_skip": self.last_skip,
            }

    def start(self) -> None:
        if not loop_enabled():
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="doa-loop", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def tick(self, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        try:
            d = self._read()
        except Exception:
            with self._lock:
                self.errors += 1
                self.last_skip = "read_error"
            return "read_error"
        mouth = False
        if self._mouth is not None:
            try:
                mouth = bool(self._mouth())
            except Exception:
                mouth = False
        else:
            try:
                from .tts_gate import mouth_open

                mouth = bool(mouth_open(grace_s=0.3))
            except Exception:
                mouth = False
        speech = bool(d.get("speech")) and not d.get("held")
        az = d.get("az_deg")
        try:
            az_f = float(az) % 360.0 if d.get("ok") and az is not None else None
        except (TypeError, ValueError):
            az_f = None
        if az_f is not None:
            with self._lock:
                self.last_az = az_f
        reason = should_publish(
            now=now,
            last_pub=self.last_pub_ts,
            hz=loop_hz(),
            ok=bool(d.get("ok")) and az_f is not None,
            speech=speech,
            mouth_open=mouth,
        )
        if reason:
            with self._lock:
                self.last_skip = reason
                if reason == "quiet":
                    self.skips_quiet += 1
                elif reason == "mouth_open":
                    self.skips_mouth += 1
                elif reason == "min_gap":
                    self.skips_gap += 1
            return reason
        try:
            self._post(
                az_f,
                conf=d.get("conf"),
                speech=speech,
                source="xvf3800",
            )
        except Exception:
            with self._lock:
                self.errors += 1
                self.last_skip = "post_error"
            return "post_error"
        with self._lock:
            self.posts += 1
            self.last_az = az_f
            self.last_pub_ts = now
            self.last_skip = None
        return None

    def _run(self) -> None:
        # Read faster than publish so the first speech tick is not late.
        poll = min(0.2, 1.0 / loop_hz())
        while not self._stop.wait(poll):
            self.tick()


_LOOP: DoaLoop | None = None
_LOOP_LOCK = threading.Lock()


def get_doa_loop() -> DoaLoop:
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None:
            _LOOP = DoaLoop()
        return _LOOP
