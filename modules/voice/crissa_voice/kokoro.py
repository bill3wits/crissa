"""Kokoro-82M on the CPU: text -> phonemes (misaki, no espeak) -> 24 kHz speech (onnxruntime).

Runs inside the TTS env that kokoro_setup builds; every heavy import is inside Kokoro() so the light install can
import this module (and its tests can stand the engine in). Measured 2026-10-05 on 3 cores, warm: real-time
factor 0.13 on the fp32 model; thread count matters (onnxruntime's default of every core under a CPU cap ran
1.6-2.4), so the session takes CRISSA_TTS_THREADS, default min(4, cores).
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

SR = 24000
MAX_TOKENS = 510                      # the model's context, less the two pad tokens
_SPLIT = re.compile(r"(?<=[.!?;:])\s+")


def accent_of(voice: str) -> str:
    """Kokoro voice ids lead with a language letter: a = American, b = British."""
    return "gb" if voice[:1].lower() == "b" else "us"


def chunks(phonemes: str, limit: int = MAX_TOKENS) -> list[str]:
    """Split a phoneme string at sentence ends so each piece fits the model; a piece with no end inside the
    limit is cut at the last space, then hard."""
    out, cur = [], ""
    for part in _SPLIT.split(phonemes.strip()):
        cand = (cur + " " + part).strip() if cur else part
        if len(cand) <= limit:
            cur = cand
            continue
        if cur:
            out.append(cur)
        while len(part) > limit:
            cut = part.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            out.append(part[:cut].strip())
            part = part[cut:].strip()
        cur = part
    if cur:
        out.append(cur)
    return [c for c in out if c]


class Kokoro:
    def __init__(self, model_dir: Path, g2p_dirs: dict, threads: int | None = None):
        import numpy as np
        import onnxruntime as ort
        self.np = np
        self.dir = Path(model_dir)
        self.vocab = json.loads((self.dir / "tokenizer.json").read_text())["model"]["vocab"]
        so = ort.SessionOptions()
        n = threads or int(os.environ.get("CRISSA_TTS_THREADS") or min(4, os.cpu_count() or 4))
        so.intra_op_num_threads = max(1, n)
        so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(self.dir / "onnx" / "model.onnx"), so,
                                         providers=["CPUExecutionProvider"])
        self.g2p_dirs = {k: Path(v) for k, v in g2p_dirs.items()}
        self._g2p: dict = {}
        self._voices: dict = {}
        self._lock = threading.Lock()          # one line at a time; the session is not ours to share

    def voices(self) -> list[str]:
        return sorted(p.stem for p in (self.dir / "voices").glob("*.bin"))

    def _voice(self, name: str):
        if name not in self._voices:
            path = self.dir / "voices" / (name + ".bin")
            if not path.is_file():
                raise KeyError("no Kokoro voice %r" % name)
            self._voices[name] = self.np.fromfile(path, dtype=self.np.float32).reshape(-1, 1, 256)
        return self._voices[name]

    def g2p(self, accent: str):
        if accent not in self._g2p:
            check_spacy_model()                # before misaki: it would fetch a missing model with spacy.cli.download
            from misaki import en
            self._g2p[accent] = en.G2P(trf=False, british=(accent == "gb"),
                                       fallback=_Fallback(self.g2p_dirs[accent]))
        return self._g2p[accent]

    def phonemes(self, text: str, voice: str) -> str:
        ph, _ = self.g2p(accent_of(voice))(text)
        return ph.replace("❓", "")        # a word nothing could read is left silent, never spelled as a glyph

    def synth(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        """16-bit mono PCM at 24 kHz."""
        np = self.np
        with self._lock:
            style_bank = self._voice(voice)
            pieces = []
            for ph in chunks(self.phonemes(text, voice)):
                ids = [self.vocab[c] for c in ph if c in self.vocab][:MAX_TOKENS]
                if not ids:
                    continue
                feeds = {"input_ids": np.array([[0] + ids + [0]], dtype=np.int64),
                         "style": style_bank[min(len(ids), len(style_bank) - 1)],
                         "speed": np.array([float(speed)], dtype=np.float32)}
                pieces.append(self.sess.run(None, feeds)[0].reshape(-1))
            if not pieces:
                return b""
            audio = np.concatenate(pieces)
            return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


SPACY_MODEL = "en_core_web_sm"


class MissingModel(RuntimeError):
    pass


def check_spacy_model(is_package=None) -> None:
    """misaki's English G2P loads spaCy's en_core_web_sm and, when it is missing, runs spacy.cli.download - a pip
    install from the network, on a spoken line, past every pin (the licence check, 2026-10-06). The install puts
    the model in the env from its hash lock; a damaged env stops here with what to do instead."""
    if is_package is None:
        import spacy.util
        is_package = spacy.util.is_package
    if not is_package(SPACY_MODEL):
        raise MissingModel("the Kokoro env has no spaCy English model (%s), and misaki would download it now. "
                           "Nothing was fetched. Reinstall the voice: python3 modules/voice/scripts/crissa-tts.py "
                           "--install" % SPACY_MODEL)


class _Fallback:
    """misaki's out-of-dictionary model, loaded from the pinned local copy (misaki's own class fetches it by name
    at first use, which would reach the network on a line)."""

    def __init__(self, path: Path):
        import torch
        from transformers import BartForConditionalGeneration
        self.torch = torch
        self.model = BartForConditionalGeneration.from_pretrained(str(path)).eval()
        c = self.model.config
        self.g2t = {g: i for i, g in enumerate(c.grapheme_chars)}
        self.t2p = {i: p for i, p in enumerate(c.phoneme_chars)}

    def __call__(self, token):
        ids = self.torch.tensor([[1] + [self.g2t.get(g, 3) for g in token.text] + [2]])
        with self.torch.no_grad():
            out = self.model.generate(input_ids=ids)
        return "".join(self.t2p.get(t, "") for t in out[0].tolist() if t > 3), 1
