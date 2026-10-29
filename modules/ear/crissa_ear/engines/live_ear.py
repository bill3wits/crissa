"""HTTP client to the live-ear ASR slot.

Role name, like ops/crew — not the vendor. Today the worker on the other
end is Audio8 ark-asr :8096. Swap LIVE_EAR_URL when the worker changes.
No fleet imports.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import numpy as np

from .base import EngineResult
from ..settings import url as _url  # this install's asr (the shared port table, 2026-10-10)

DEFAULT_LIVE_EAR_URL = _url("asr")


class LiveEarEngine:
    name = "live-ear"

    def __init__(self, url: str = DEFAULT_LIVE_EAR_URL, timeout: float = 60.0):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._ok: bool | None = None

    def health(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=3) as r:
                body = json.loads(r.read().decode())
            self._ok = bool(body.get("ok"))
            return self._ok
        except Exception:
            self._ok = False
            return False

    def transcribe(self, audio: np.ndarray, sr: int = 16000) -> EngineResult:
        """audio: float32 mono [-1,1] or int16."""
        t0 = time.monotonic()
        if audio.dtype != np.int16:
            pcm = np.clip(audio.astype(np.float32), -1.0, 1.0)
            pcm = (pcm * 32767.0).astype(np.int16)
        else:
            pcm = audio
        if len(pcm) < int(0.05 * sr):
            return EngineResult(text="", engine=self.name, duration_s=0.0, decode_ms=0.0)

        data = np.ascontiguousarray(pcm).tobytes()
        req = urllib.request.Request(
            f"{self.url}/stt",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Sample-Rate": str(sr),
                "Content-Length": str(len(data)),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = json.loads(r.read().decode())
        except urllib.error.URLError as e:
            return EngineResult(
                text="",
                engine=self.name,
                error=f"live-ear unreachable: {e}",
                decode_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return EngineResult(
                text="",
                engine=self.name,
                error=str(e)[:200],
                decode_ms=(time.monotonic() - t0) * 1000,
            )

        text = (body.get("text") or "").strip()
        from ..config import live_asr_name

        live = str(body.get("engine") or live_asr_name())
        return EngineResult(
            text=text,
            engine=live,
            duration_s=float(body.get("duration_s") or 0.0),
            decode_ms=float(body.get("decode_ms") or (time.monotonic() - t0) * 1000),
            score=body.get("score"),
            words=list(body.get("words") or []),
            segments=list(body.get("segments") or []),
            raw=body,
        )


# Historical name. Do not use in new code.
ParakeetEngine = LiveEarEngine
