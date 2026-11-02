"""Crissa's voice-ID engine: WeSpeaker ResNet221-LM on onnxruntime, with a numpy front end (2026-10-10).

ONE ENGINE, ONE FILE. This file is carried byte for byte in three packages - modules/ear/crissa_ear (the ear that
matches a voice), modules/id/crissa_id and modules/presence/crissa_presence (the two copies of the Register coach that
write the prints) - so the print Init's Voice ID writes and the print the ear compares are made by the same code with
the same model, whichever modules a pack carries. tests/test_crissa_voice_id.py holds the three copies equal. It
imports numpy and onnxruntime only: no torch, no `wespeaker` package, nothing package-relative.

THE ENGINE IS THE HOUSE'S. The house enrols and matches with WeSpeaker's VoxCeleb ResNet221-LM ("english" in the
wespeaker hub), 256-d, through an ONNX export on onnxruntime (CPU, fp32), with the kaldi fbank of
torchaudio.compliance.kaldi as its features. Its checkpoint is byte-identical to the one published beside this ONNX
(sha256 47d76239...). The pack fetches the ONNX that WeSpeaker publishes for that checkpoint instead of exporting one
(the export needed the `wespeaker` package, which is not on PyPI and calls a torchaudio function the locked torchaudio
removed), and computes the same fbank in numpy. Measured 2026-10-10 against the house's own path (its export +
torchaudio's fbank) on synthetic speech: see tests/test_crissa_voice_id.py and the commit that added this file.

    MODEL     https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet221-LM @ 26732f43821f238bfb258c5ef54924a798e62118
              voxceleb_resnet221_LM.onnx, 95,037,357 bytes, sha256 e6b8c14c022288ca186de39f038a9bd9404be4f174ac8a6a3b8dbf229c8f607d
              input feats [B, T, 80] float32 -> embs [B, 256]
    LICENCES  (read 2026-10-10 from the primary sources)
              the weights: the model card says apache-2.0; WeSpeaker's docs/pretrained.md says its VoxCeleb models follow
              the VoxCeleb dataset's licence, Creative Commons Attribution 4.0 (CC BY 4.0) - attribution, commercial use
              allowed. VoxCeleb's own page (mm.kaist.ac.kr/datasets/voxceleb) offers the dataset "for research purposes
              under a Creative Commons Attribution 4.0 International License".
              WeSpeaker's code (github.com/wenet-e2e/wespeaker): Apache-2.0. None of it runs here.
    WHERE     <state home>/models/voice-id/voxceleb_resnet221_LM.onnx, one file for every module (fetched once by the
              first module's --install, checked by sha256 by each). CRISSA_VOICE_ID_MODEL (or the house's
              HOUSE_VOICE_ORT_MODEL) names another file; a file named that way is used as it is.

Features, exactly as torchaudio.compliance.kaldi.fbank(wav, num_mel_bins=80, frame_length=25, frame_shift=10,
sample_frequency=16000, window_type="hamming") computes them (dither 0, DC removal, pre-emphasis 0.97, a 400-sample
frame padded to 512, power spectrum, 80 triangular mel bins 20 Hz - 8 kHz, log floored at float32 epsilon,
snip_edges), then the mean over the utterance taken off each bin (the house's CMN). The waveform is in [-1, 1], as the
house feeds it.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

SR = 16000
DIM = 256
FAMILY = "wespeaker"
MODEL_ID = "wespeaker/english_resnet221 (onnxruntime fp32)"   # the string the house writes on a print (its family)
MODEL_REPO = "Wespeaker/wespeaker-voxceleb-resnet221-LM"
MODEL_REV = "26732f43821f238bfb258c5ef54924a798e62118"
MODEL_FILE = "voxceleb_resnet221_LM.onnx"
MODEL_SHA256 = "e6b8c14c022288ca186de39f038a9bd9404be4f174ac8a6a3b8dbf229c8f607d"
MODEL_BYTES = 95037357
MODEL_URL = "https://huggingface.co/%s/resolve/%s/%s" % (MODEL_REPO, MODEL_REV, MODEL_FILE)
LICENCE = ("WeSpeaker VoxCeleb ResNet221-LM (wenet-e2e/wespeaker): model card apache-2.0; WeSpeaker's pretrained-model "
           "note: CC BY 4.0, the VoxCeleb dataset's licence")
THREADS = int(os.environ.get("HOUSE_VOICE_ORT_THREADS", "2") or 2)

# kaldi fbank, the house's settings
_FRAME = 400            # 25 ms at 16 kHz
_SHIFT = 160            # 10 ms
_NFFT = 512             # the frame rounded up to a power of two
_MELS = 80
_LOW_HZ = 20.0
_PREEMPH = 0.97
_EPS = float(np.finfo(np.float32).eps)

_sessions: dict[str, Any] = {}
_errors: dict[str, str] = {}


# ---- where the model lives, and its fetch --------------------------------------------------------------------------
def default_model(home: Path | str) -> Path:
    return Path(home).expanduser() / "models" / "voice-id" / MODEL_FILE


def named_model() -> Optional[Path]:
    """A model file the person named (CRISSA_VOICE_ID_MODEL, else the house's HOUSE_VOICE_ORT_MODEL), or None."""
    raw = (os.environ.get("CRISSA_VOICE_ID_MODEL") or os.environ.get("HOUSE_VOICE_ORT_MODEL") or "").strip()
    return Path(raw).expanduser() if raw else None


def model_path(home: Path | str) -> Path:
    return named_model() or default_model(home)


def sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _urllib_fetch(url: str, dest: Path, timeout: float = 600.0) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)


def fetch_model(home: Path | str, fetch: Optional[Callable[[str, Path], None]] = None,
                log: Callable[[str], Any] = print) -> dict:
    """The model at <home>/models/voice-id/, fetched at the pinned revision and checked by sha256.

    Present and matching: nothing is fetched. A file the person named (named_model) is used as it is and never
    replaced. `fetch(url, dest)` writes the file (the module's egress-checked fetch); a download that does not match
    its sha256 is deleted and raises, so no unchecked file is ever left where the engine looks."""
    named = named_model()
    if named is not None:
        ok = named.is_file()
        return {"ok": ok, "path": str(named), "named": True,
                **({} if ok else {"why": "the named voice-ID model %s is not there" % named})}
    dest = default_model(home)
    if dest.is_file() and dest.stat().st_size == MODEL_BYTES and sha256(dest) == MODEL_SHA256:
        return {"ok": True, "path": str(dest), "already": True, "sha256": MODEL_SHA256}
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    log("voice ID: fetching %s @ %s (%d MB) ..." % (MODEL_FILE, MODEL_REV[:8], MODEL_BYTES // 1000000))
    try:
        (fetch or _urllib_fetch)(MODEL_URL, part)
        got = sha256(part) if part.is_file() else ""
        if got != MODEL_SHA256:
            raise RuntimeError("%s does not match its pinned sha256 (got %s); nothing was installed"
                               % (MODEL_FILE, got[:16] or "no file"))
        os.replace(part, dest)
    finally:
        if part.exists():
            part.unlink()
    _sessions.pop(str(dest), None)
    _errors.pop(str(dest), None)
    log("voice ID: %s checked (sha256 %s...)" % (dest, MODEL_SHA256[:12]))
    return {"ok": True, "path": str(dest), "sha256": MODEL_SHA256}


def model_ok(home: Path | str) -> bool:
    """The file the engine would load is there (a named one), or is the pinned one (size first, then sha256)."""
    named = named_model()
    if named is not None:
        return named.is_file()
    p = default_model(home)
    return p.is_file() and p.stat().st_size == MODEL_BYTES


# ---- the front end: kaldi fbank in numpy ----------------------------------------------------------------------------
def _mel(f):
    return 1127.0 * np.log(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


def _mel_banks() -> np.ndarray:
    nyquist = 0.5 * SR
    width = SR / _NFFT
    lo, hi = float(_mel(_LOW_HZ)), float(_mel(nyquist))
    delta = (hi - lo) / (_MELS + 1)
    b = np.arange(_MELS, dtype=np.float64)[:, None]
    left, center, right = lo + b * delta, lo + (b + 1.0) * delta, lo + (b + 2.0) * delta
    mel = _mel(width * np.arange(_NFFT // 2, dtype=np.float64))[None, :]
    banks = np.maximum(0.0, np.minimum((mel - left) / (center - left), (right - mel) / (right - center)))
    return np.pad(banks, ((0, 0), (0, 1)))           # the Nyquist bin carries no weight: [80, 257]


_BANKS = _mel_banks()
_WINDOW = 0.54 - 0.46 * np.cos(2.0 * np.pi * np.arange(_FRAME, dtype=np.float64) / (_FRAME - 1))   # hamming, symmetric


def fbank(wav: np.ndarray) -> np.ndarray:
    """[T, 80] float32 log-mel features of a 16 kHz mono waveform in [-1, 1], mean-normalised over the utterance."""
    w = np.asarray(wav, dtype=np.float64).reshape(-1)
    if w.size < _FRAME:
        return np.zeros((0, _MELS), dtype=np.float32)
    n = 1 + (w.size - _FRAME) // _SHIFT
    idx = np.arange(_FRAME)[None, :] + _SHIFT * np.arange(n)[:, None]
    fr = w[idx]
    fr = fr - fr.mean(axis=1, keepdims=True)
    fr = fr - _PREEMPH * np.concatenate([fr[:, :1], fr[:, :-1]], axis=1)
    fr = fr * _WINDOW
    spec = np.abs(np.fft.rfft(fr, n=_NFFT, axis=1)) ** 2
    feat = np.log(np.maximum(spec @ _BANKS.T, _EPS))
    feat = feat - feat.mean(axis=0, keepdims=True)
    return feat.astype(np.float32)


def resample16k(audio: np.ndarray, sr: int) -> np.ndarray:
    """The house's linear resample to 16 kHz."""
    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    if sr == SR or a.size < 2:
        return a
    n = int(a.size / float(sr) * SR)
    x = np.linspace(0, a.size - 1, n)
    return np.interp(x, np.arange(a.size), a).astype(np.float32)


# ---- the encoder ----------------------------------------------------------------------------------------------------
def session(model: Path | str):
    """The onnxruntime session for `model`, once per process; None when the file or onnxruntime is missing."""
    key = str(model)
    if key in _sessions:
        return _sessions[key]
    if key in _errors or not Path(key).is_file():
        return None
    try:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = THREADS
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _sessions[key] = ort.InferenceSession(key, so, providers=["CPUExecutionProvider"])
        return _sessions[key]
    except Exception as e:  # noqa: BLE001
        _errors[key] = "%s: %s" % (type(e).__name__, str(e)[:200])
        return None


def last_error(model: Path | str) -> Optional[str]:
    key = str(model)
    if key in _errors:
        return _errors[key]
    if not Path(key).is_file():
        return "no voice-ID model at %s (the module's --install fetches it)" % key
    return None


def embed_wav16k(wav: np.ndarray, model: Path | str) -> Optional[np.ndarray]:
    """The unit-length 256-d print of a 16 kHz waveform; None under half a second, or with no engine."""
    w = np.asarray(wav, dtype=np.float32).reshape(-1)
    if w.size < SR // 2:
        return None
    sess = session(model)
    if sess is None:
        return None
    try:
        feat = fbank(w)[None, :, :]
        name = sess.get_inputs()[0].name
        out = sess.run(None, {name: feat})[0]
        v = np.asarray(out[0], dtype=np.float32).reshape(-1)
        nrm = float(np.linalg.norm(v))
        return None if nrm < 1e-8 else v / nrm
    except Exception as e:  # noqa: BLE001
        _errors[str(model)] = "%s: %s" % (type(e).__name__, str(e)[:200])
        return None


def embed_pcm16(pcm: bytes, model: Path | str, sr: int = SR) -> Optional[np.ndarray]:
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    return embed_wav16k(resample16k(audio, sr), model)


def cosine(a, b) -> float:
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    b = np.asarray(b, dtype=np.float32).reshape(-1)
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / d) if d > 0 else 0.0


def status(home: Path | str) -> dict:
    p = model_path(home)
    return {"encoder": FAMILY, "model": MODEL_ID, "dim": DIM, "file": str(p), "file_ok": model_ok(home),
            "pinned": "%s@%s %s sha256 %s" % (MODEL_REPO, MODEL_REV[:12], MODEL_FILE, MODEL_SHA256[:12]),
            "available": session(p) is not None, "error": last_error(p)}
