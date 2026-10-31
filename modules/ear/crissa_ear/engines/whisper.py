"""faster-whisper large-v3 engine. Direct model load — no fleet code."""

from __future__ import annotations

import time
from typing import Optional

import numpy as np

from .base import EngineResult

_model = None
_model_key = None


class WhisperEngine:
    name = "whisper"

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "int8_float16",
        language: str = "en",
        initial_prompt: str = "",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.initial_prompt = initial_prompt
        self._model = None

    def load(self) -> None:
        global _model, _model_key
        key = (self.model_size, self.device, self.compute_type)
        if _model is not None and _model_key == key:
            self._model = _model
            return
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        _model_key = key
        self._model = _model

    def transcribe(self, audio: np.ndarray, sr: int = 16000) -> EngineResult:
        t0 = time.monotonic()
        if self._model is None:
            try:
                self.load()
            except Exception as e:
                return EngineResult(text="", engine=self.name, error=f"load failed: {e}")

        if audio.dtype == np.int16:
            wav = audio.astype(np.float32) / 32768.0
        else:
            wav = audio.astype(np.float32)

        if len(wav) < int(0.05 * sr):
            return EngineResult(text="", engine=self.name, duration_s=0.0)

        # Resample if needed (faster-whisper expects 16k)
        if sr != 16000:
            n = int(len(wav) * 16000 / sr)
            wav = np.interp(
                np.linspace(0, len(wav) - 1, n),
                np.arange(len(wav)),
                wav,
            ).astype(np.float32)
            sr = 16000

        try:
            segments_gen, info = self._model.transcribe(
                wav,
                language=self.language,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=300),
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                initial_prompt=self.initial_prompt or None,
                word_timestamps=True,
            )
            segs = list(segments_gen)
        except Exception as e:
            return EngineResult(
                text="",
                engine=self.name,
                error=str(e)[:200],
                decode_ms=(time.monotonic() - t0) * 1000,
            )

        texts = []
        words = []
        segments = []
        logprobs = []
        no_speech = []
        for s in segs:
            t = (s.text or "").strip()
            if not t:
                continue
            texts.append(t)
            logprobs.append(float(s.avg_logprob))
            no_speech.append(float(s.no_speech_prob))
            segments.append(
                {
                    "t": t,
                    "s": round(float(s.start), 2),
                    "e": round(float(s.end), 2),
                    "avg_logprob": float(s.avg_logprob),
                    "no_speech_prob": float(s.no_speech_prob),
                }
            )
            if s.words:
                for w in s.words:
                    words.append(
                        {
                            "w": w.word,
                            "s": round(float(w.start or 0), 2),
                            "e": round(float(w.end or 0), 2),
                        }
                    )

        text = " ".join(texts).strip()
        return EngineResult(
            text=text,
            engine=self.name,
            duration_s=len(wav) / 16000.0,
            decode_ms=(time.monotonic() - t0) * 1000,
            avg_logprob=float(np.mean(logprobs)) if logprobs else None,
            no_speech_prob=float(np.max(no_speech)) if no_speech else None,
            words=words,
            segments=segments,
            raw={"language": getattr(info, "language", None)},
        )
