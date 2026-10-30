"""Main Transcriber — dual-engine consensus path."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from . import audio as audio_mod
from .config import Config, is_live_ear_engine, live_asr_name
from .engines.live_ear import LiveEarEngine
from .engines.whisper import WhisperEngine
from .quality import consensus, is_hallucination, is_prompt_echo, strip_foreign
from .vad import detect_speech_spans, extract_spans


@dataclass
class Transcript:
    text: str
    confidence: float
    engine: str
    agreed: bool = False
    reason: str = ""
    duration_s: float = 0.0
    decode_ms: float = 0.0
    rms_db: float = -120.0
    speech_spans: list[dict[str, float]] = field(default_factory=list)
    candidates: dict[str, Any] = field(default_factory=dict)
    words: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    # Ear join: filled by server/cli after presence stamp (optional)
    person_id: str | None = None
    stamp: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "text": self.text,
            "confidence": round(self.confidence, 3),
            "engine": self.engine,
            "agreed": self.agreed,
            "reason": self.reason,
            "duration_s": round(self.duration_s, 3),
            "decode_ms": round(self.decode_ms, 1),
            "rms_db": round(self.rms_db, 1),
            "speech_spans": self.speech_spans,
            "candidates": self.candidates,
            "words": self.words,
            "error": self.error,
            "person_id": self.person_id,
        }
        if self.stamp is not None:
            d["stamp"] = self.stamp
        return d


def asr_warm_wait_s() -> float:
    """How long the ear keeps asking for the ASR after its start (round 23); CRISSA_ASR_WARM_WAIT_S=0 = round 22."""
    try:
        return max(0.0, float(os.environ.get("CRISSA_ASR_WARM_WAIT_S", "600")))
    except ValueError:
        return 600.0


class Transcriber:
    def __init__(self, config: Optional[Config] = None):
        self.cfg = config or Config()
        self.cfg.ensure_work()
        self.live_ear = LiveEarEngine(url=self.cfg.live_ear_url)
        self.parakeet = self.live_ear  # alias — historical client name
        self.whisper: WhisperEngine | None = None
        if self.cfg.engine in ("whisper", "dual"):
            self.whisper = WhisperEngine(
                model_size=self.cfg.whisper_model,
                device=self.cfg.whisper_device,
                compute_type=self.cfg.whisper_compute,
                language=self.cfg.language,
                initial_prompt=self.cfg.initial_prompt,
            )

    # round 33: what /health says of the ASR's warm-up (the start bar waits on it): warming | warm | cold | None
    asr_warm: Optional[str] = None

    def warm(self) -> dict[str, Any]:
        """Optional preload. Live-ear is remote HTTP; whisper loads into VRAM."""
        out: dict[str, Any] = {live_asr_name(): self.live_ear.health()}
        if out[live_asr_name()]:
            self._warm_line()
        elif asr_warm_wait_s() > 0:
            self.asr_warm = "warming"
            # ROUND 23 (.101: `warm={'ark-asr': False}` on all 11 starts of 10-15 - the ear asked once, before the ASR
            # service had loaded, and her first heard line after a start was cold): the ear does not wait for it (its
            # port opens now); a thread asks again until the ASR answers, then sends it one throwaway clip.
            threading.Thread(target=self._warm_later, name="asr-warm", daemon=True).start()
            out["later"] = True
        if self.whisper is not None:
            self.whisper.load()
            out["whisper"] = self.cfg.whisper_model
        return out

    def _warm_line(self) -> None:
        self.asr_warm = "warming"
        try:
            t0 = time.monotonic()
            self.live_ear.transcribe(np.zeros(16000, dtype=np.int16), 16000)
            print("[rail-stt] the ASR warmed in %d ms" % int((time.monotonic() - t0) * 1000), flush=True)
        except Exception as e:  # noqa: BLE001  a warm-up that fails changes nothing: the first line is cold, as before
            print("[rail-stt] the ASR warm line: %s" % str(e)[:120], flush=True)
        finally:
            self.asr_warm = "warm"

    def _warm_later(self, sleep=time.sleep) -> bool:
        t0 = time.monotonic()
        while time.monotonic() - t0 < asr_warm_wait_s():
            sleep(5.0)
            if self.live_ear.health():
                print("[rail-stt] the ASR answers after %d s" % int(time.monotonic() - t0), flush=True)
                self._warm_line()
                return True
        print("[rail-stt] the ASR did not answer in %d s" % int(asr_warm_wait_s()), flush=True)
        self.asr_warm = "cold"
        return False


    def transcribe_array(self, audio: np.ndarray, sr: int = 16000) -> Transcript:
        t0 = time.monotonic()
        if sr != 16000:
            audio = audio_mod.resample_linear(audio, sr, 16000)
            sr = 16000
        audio = audio.astype(np.float32)
        dur = len(audio) / float(sr) if len(audio) else 0.0
        rms = float(audio_mod.rms_db(audio))

        # Too quiet overall — don't invent
        if rms < -50.0 and dur > 0.5:
            # still try VAD; if no speech spans, abstain
            spans = detect_speech_spans(audio, sr)
            if not spans:
                return Transcript(
                    text="",
                    confidence=0.0,
                    engine=self.cfg.engine,
                    reason="silence_rms",
                    duration_s=dur,
                    rms_db=rms,
                    decode_ms=(time.monotonic() - t0) * 1000,
                )
        else:
            spans = detect_speech_spans(audio, sr)

        span_dicts = [{"start": s.start, "end": s.end} for s in spans]
        # Decode speech-only if we found spans; else full buffer (short clips)
        if spans and dur > 1.0:
            speech = extract_spans(audio, sr, spans)
        else:
            speech = audio

        if len(speech) < int(0.08 * sr):
            return Transcript(
                text="",
                confidence=0.0,
                engine=self.cfg.engine,
                reason="no_speech",
                duration_s=dur,
                rms_db=rms,
                speech_spans=span_dicts,
                decode_ms=(time.monotonic() - t0) * 1000,
            )

        eng = self.cfg.engine.lower()
        p_res = None
        w_res = None
        errors = []

        if is_live_ear_engine(eng) or eng == "dual":
            p_res = self.live_ear.transcribe(speech, sr)
            if p_res.error:
                errors.append(p_res.error)

        if eng in ("whisper", "dual"):
            if self.whisper is None:
                self.whisper = WhisperEngine(
                    model_size=self.cfg.whisper_model,
                    device=self.cfg.whisper_device,
                    compute_type=self.cfg.whisper_compute,
                    language=self.cfg.language,
                    initial_prompt=self.cfg.initial_prompt,
                )
            w_res = self.whisper.transcribe(speech, sr)
            if w_res.error:
                errors.append(w_res.error)
            if is_prompt_echo(w_res.text, self.cfg.initial_prompt):
                w_res.text = ""

        if is_live_ear_engine(eng) and p_res is not None:
            # Majority-foreign output is garbage and is dropped; a stray
            # injected character in otherwise-English speech is scrubbed so the
            # wall never shows words nobody said.
            text = strip_foreign(p_res.text)
            if is_hallucination(text):
                text = ""
            live = p_res.engine or "audio8-ark"
            return Transcript(
                text=text,
                confidence=0.8 if text else 0.0,
                engine=live,
                reason=live if text else "empty",
                duration_s=dur,
                decode_ms=(time.monotonic() - t0) * 1000,
                rms_db=rms,
                speech_spans=span_dicts,
                candidates={live: p_res.raw or {"text": p_res.text, "score": p_res.score}},
                words=p_res.words,
                error="; ".join(errors) if errors else None,
            )

        if eng == "whisper" and w_res is not None:
            text = strip_foreign(w_res.text)
            if is_hallucination(text):
                text = ""
            if w_res.no_speech_prob is not None and w_res.no_speech_prob >= 0.55:
                text = ""
            if w_res.avg_logprob is not None and w_res.avg_logprob <= -1.05:
                text = ""
            return Transcript(
                text=text,
                confidence=0.75 if text else 0.0,
                engine="whisper",
                reason="whisper" if text else "empty_or_filtered",
                duration_s=dur,
                decode_ms=(time.monotonic() - t0) * 1000,
                rms_db=rms,
                speech_spans=span_dicts,
                candidates={"whisper": {"text": w_res.text, "avg_logprob": w_res.avg_logprob}},
                words=w_res.words,
                error="; ".join(errors) if errors else None,
            )

        # dual
        c = consensus(
            p_res.text if p_res else None,
            p_res.score if p_res else None,
            w_res.text if w_res else None,
            w_res.avg_logprob if w_res else None,
            w_res.no_speech_prob if w_res else None,
        )
        words = []
        if c.agreed or c.reason.startswith("parakeet") or c.reason.startswith("live_ear"):
            words = (p_res.words if p_res else []) or (w_res.words if w_res else [])
        else:
            words = (w_res.words if w_res else []) or (p_res.words if p_res else [])

        return Transcript(
            text=c.text,
            confidence=c.confidence,
            engine="dual",
            agreed=c.agreed,
            reason=c.reason,
            duration_s=dur,
            decode_ms=(time.monotonic() - t0) * 1000,
            rms_db=rms,
            speech_spans=span_dicts,
            candidates=c.candidates,
            words=words,
            error="; ".join(errors) if errors else None,
        )

    def transcribe_file(self, path: str) -> Transcript:
        wav, sr = audio_mod.load_wav_mono16k(path)
        return self.transcribe_array(wav, sr)

    def transcribe_pcm16(self, pcm: np.ndarray, sr: int = 16000) -> Transcript:
        return self.transcribe_array(audio_mod.pcm16_to_float(pcm), sr)
