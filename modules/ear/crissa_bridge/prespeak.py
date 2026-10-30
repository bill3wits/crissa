"""The rest of her reply is started while her first clause plays (round 26).

.101 2026-10-16 ("her replies still come in chunks ... seems something slowed it"; "the halting"): every piece of a
reply is its own house-speak process, and a process spends 0.61-0.72 s from its start to her voice (the interpreter,
the voice's first audio). Her first clause is spoken as soon as it streams in (TTFS), the rest after it - so between
the two there were 0.7-0.9 s of silence every time, even in a two-sentence answer.

house-speak synthesises its first audio BEFORE it takes the shared speech lock (house-speech.lock, held only while it
plays), so a second process started while the first is ON THE AIR does its start-up during her clause and then waits
on the lock: it plays the moment the clause ends. Started any earlier it could take the lock first and say the rest
before the clause, so `arm` waits until the player's mouth reports her clause on the air (air_at at or after the
clause's own start) and gives up after WAIT_AIR_S.

Only the exact text and the same turn (cancel token) take a started process; anything else - a revised remainder, a
stop, a new turn - kills it unheard (`take`, `drop`). abort_mouth drops it with the line on the air.
Kill: EAR_BRIDGE_PRESPEAK=0.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path
from typing import Any, Callable

WAIT_AIR_S = 8.0
POLL_S = 0.1


def on() -> bool:
    return str(os.environ.get("EAR_BRIDGE_PRESPEAK", "1")).strip().lower() not in ("0", "off", "false", "no")


class Pre:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.item: dict[str, Any] | None = None
        self._gen = 0

    def arm(self, text: str, token: int, since: float, *, spawn: Callable[[str], tuple],
            air_at: Callable[[], float | None], token_now: Callable[[], int], log=None,
            wait_s: float = WAIT_AIR_S, sleep=time.sleep) -> threading.Thread | None:
        """Start `text`'s process once the line begun at `since` is on the air (a thread); None when off or empty."""
        if not on() or not str(text or "").strip():
            return None
        with self._lock:
            self._gen += 1
            gen = self._gen

        def _run() -> None:
            end = time.time() + wait_s
            while True:
                if token_now() != token:
                    return
                with self._lock:
                    if gen != self._gen:
                        return
                a = None
                try:
                    a = air_at()
                except Exception:  # noqa: BLE001
                    a = None
                if a is not None and float(a) >= since - 0.05:
                    break
                if time.time() > end:
                    if log is not None:
                        log({"event": "prespeak_skipped", "why": "clause_not_on_air"})
                    return
                sleep(POLL_S)
            proc, tmp = spawn(text)
            with self._lock:
                stale = gen != self._gen or token_now() != token
                if not stale:
                    self._kill(self.item)
                    self.item = {"text": text, "token": token, "proc": proc, "tmp": tmp, "at": time.time()}
            if stale:
                self._kill({"proc": proc, "tmp": tmp})
            elif log is not None:
                log({"event": "prespeak_started", "chars": len(text)})

        t = threading.Thread(target=_run, name="prespeak", daemon=True)
        t.start()
        return t

    def take(self, text: str, token: int):
        """(proc, tmp) started for exactly this text in this turn and still running, else None (and it is killed)."""
        with self._lock:
            self._gen += 1                       # an arm still waiting will not start one now
            it, self.item = self.item, None
        if not it:
            return None
        if it["text"] == text and it["token"] == token and it["proc"].poll() is None:
            return it["proc"], it["tmp"]
        self._kill(it)
        return None

    def drop(self) -> None:
        with self._lock:
            self._gen += 1
            it, self.item = self.item, None
        self._kill(it)

    @staticmethod
    def _kill(it) -> None:
        if not it:
            return
        p = it.get("proc")
        if p is not None and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except Exception:  # noqa: BLE001
                try:
                    p.terminate()
                except Exception:  # noqa: BLE001
                    pass
        tmp = it.get("tmp")
        if tmp:
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass


PRE = Pre()
