"""Turn ring — recent STT turns in memory, optional JSONL on disk."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path

from .home import EAR
from typing import Any, Optional

RING_MAX = 200


def _jsonl_path() -> Optional[Path]:
    """logs/turns.jsonl under the repo unless disabled via RAIL_STT_TURN_JSONL=0."""
    if os.environ.get("RAIL_STT_TURN_JSONL", "1").lower() in ("0", "false", "no"):
        return None
    override = os.environ.get("RAIL_STT_TURN_JSONL_PATH")
    if override:
        return Path(override)
    return EAR / "turns.jsonl"


class TurnLog:
    def __init__(self, maxlen: int = RING_MAX, jsonl: Optional[Path] = None):
        self._ring: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._jsonl = jsonl
        self._seeded = False
        # After restart, ring is empty but disk may have history — seed for omni/dashboard.
        self._seed_from_jsonl()

    def _seed_from_jsonl(self) -> None:
        """Load recent turns from turns.jsonl into memory (oldest→newest).

        Prefer speech rows (non-empty text) so continuous-ear silence cannot
        leave omni with an empty history after restart.

        the User 2026-08-06: only seed turns newer than SEED_MAX_AGE_SEC (default
        15 min). Old ghosts ("zero stuff" etc.) must not reappear after
        silence / restart — disk jsonl stays for train, ring is display-live.
        """
        if self._seeded or self._jsonl is None or not self._jsonl.is_file():
            self._seeded = True
            return
        self._seeded = True
        try:
            max_age = float(os.environ.get("RAIL_STT_SEED_MAX_AGE_SEC", "900"))
            cutoff = time.time() - max(60.0, max_age)
            size = self._jsonl.stat().st_size
            with self._jsonl.open("rb") as f:
                # Read a larger tail so we can filter for speech
                take = min(size, 8_000_000)
                if size > take:
                    f.seek(size - take)
                    f.readline()
                raw = f.read().decode("utf-8", errors="replace")
            speech: list[dict[str, Any]] = []
            any_rows: list[dict[str, Any]] = []
            for ln in raw.splitlines():
                if not ln.strip():
                    continue
                try:
                    rec = json.loads(ln)
                except Exception:
                    continue
                if not isinstance(rec, dict):
                    continue
                ts = rec.get("ts")
                try:
                    ts_f = float(ts) if ts is not None else None
                except (TypeError, ValueError):
                    ts_f = None
                if ts_f is not None and ts_f < cutoff:
                    continue  # too old for live display seed
                any_rows.append(rec)
                if (rec.get("text") or "").strip() or rec.get("error"):
                    speech.append(rec)
            # Prefer speech-only seed; fall back to any if file has no text yet
            chosen = speech if speech else any_rows
            chosen = chosen[-self._ring.maxlen :]
            with self._lock:
                for rec in chosen:
                    self._ring.append(rec)
        except Exception:
            pass

    def append(self, turn: dict[str, Any], via: str = "stt") -> dict[str, Any]:
        entry = dict(turn)
        entry["ts"] = time.time()
        entry["ts_local"] = time.strftime("%Y-%m-%d %H:%M:%S")
        entry["via"] = via
        # the player reads `her_voice` on a turn (its own-voice check, lib: _mic_busy): a turn the TTS
        # fingerprint veto or the own-voice stamp named as hers carries it
        st = entry.get("stamp") if isinstance(entry.get("stamp"), dict) else {}
        if st.get("tts_veto") or str(st.get("source") or "") == "tts_echo_veto":
            entry["her_voice"] = True
        with self._lock:
            self._ring.append(entry)
        if self._jsonl is not None:
            # Disk is best-effort; the ring is the product.
            try:
                self._jsonl.parent.mkdir(parents=True, exist_ok=True)
                with self._jsonl.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception:
                pass
        return entry

    def list(self, n: int = 50, via: str | None = None) -> list[dict[str, Any]]:
        """Newest first. ``via`` is a comma allowlist (e.g. ``ear,stt``)."""
        if not self._seeded:
            self._seed_from_jsonl()
        with self._lock:
            items = list(self._ring)
        items.reverse()
        if via:
            allow = {v.strip().lower() for v in str(via).split(",") if v.strip()}
            if allow:
                items = [
                    t
                    for t in items
                    if str(t.get("via") or "").strip().lower() in allow
                ]
        return items[: max(0, n)]

    def __len__(self) -> int:
        with self._lock:
            return len(self._ring)

    def clear(self) -> int:
        with self._lock:
            n = len(self._ring)
            self._ring.clear()
        return n


_turn_log: Optional[TurnLog] = None


def get_turn_log() -> TurnLog:
    global _turn_log
    if _turn_log is None:
        _turn_log = TurnLog(jsonl=_jsonl_path())
    return _turn_log
