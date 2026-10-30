"""The ear's env, installed on first use (2026-10-09; the voice module's pattern, crissa_voice/asr_setup.py).

The house ran its ear from a project env with a CUDA torch and faster-whisper beside the live engine. A pack
builds the ear's own env under the state home from the hash locks in modules/ear/locks (every package and
every transitive one by exact version and sha256; pip --require-hashes --no-deps), on the Python pinned in
locks/python-env.json (micromamba from conda-forge; `CRISSA_EAR_PYENV=0` = a venv from the running Python).

    <crissa home>/ear/env            the env
    <crissa home>/ear/installed.json the record of how it was installed
    <crissa home>/models/voice-id/   the voice-ID engine's model (wespeaker_onnx.py: WeSpeaker's published ONNX of the
                                     house's ResNet221 checkpoint, at a pinned revision, checked by sha256; one file
                                     for the ear and both copies of the Register coach, fetched by whichever installs
                                     first)

What is NOT installed here, and why:
  - the ASR model: the voice module's crissa-asr.py serves /stt (ARK-ASR); the ear is its client (`asr_url`).
  - whisper (faster-whisper, CUDA): the house's second engine for its dual/consensus mode. Opt-in by hand
    (`pip install faster-whisper` into the env) - a GPU build is a machine's own choice, not a lock's.
  - the `wespeaker` package and its torch hub path: not on PyPI (git only), and it calls a torchaudio function the
    locked torchaudio removed. The engine needs numpy and onnxruntime only, both in the lock (2026-10-10).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import egress, locks, pyenv, wespeaker_onnx
from .home import EAR, HOME

ENV = EAR / "env"
DONE = EAR / "installed.json"
# the fallback for a machine with no lock: the versions the locks were made from (uv pip compile, 2026-10-09)
PACKAGES = ["numpy", "onnxruntime", "webrtcvad", "silero-vad", "soundfile", "sounddevice", "PyYAML"]
TORCH = ["torch", "torchaudio"]
TORCH_INDEX = "https://download.pytorch.org/whl/cpu"
WESPEAKER_ONNX = wespeaker_onnx.default_model(HOME)


def env_python() -> Path:
    return pyenv.env_python(ENV)


def installed() -> bool:
    return DONE.is_file() and env_python().exists()


def in_env() -> bool:
    try:
        return Path(sys.prefix).resolve() == ENV.resolve()
    except OSError:
        return False


def unsupported() -> str | None:
    if sys.platform not in ("linux", "darwin") and not sys.platform.startswith("linux"):
        return "the ear installs on Linux and macOS (PulseAudio/PipeWire or sounddevice for the microphone)"
    return None


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(cmd[:4]), (r.stderr or r.stdout)[-400:]))


def make_env(run=_run, log=print) -> str:
    """The env once, the packages every time (pip skips what is there; an older env is repaired, not trusted)."""
    egress.refuse_install("the ear's Python packages")
    log("ear: " + pyenv.make(ENV, run, log))
    torch = (["--index-url", TORCH_INDEX] if sys.platform.startswith("linux") else []) + TORCH
    return locks.pip_install(env_python(), "ear", run, log, version=pyenv.target_version(), fallback=[torch, PACKAGES])


def _fetch(url: str, dest: Path) -> None:
    with egress.urlopen(url, 600.0, "the voice ID model") as r, open(dest, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def install(run=_run, log=print, fetch=None) -> dict:
    how = make_env(run, log)
    vid = install_voice_id(log=log, fetch=fetch)
    rec = {"installed": time.strftime("%Y-%m-%dT%H:%M:%S"), "python": str(env_python()), "packages": how,
           "voice_id": bool(vid.get("ok")), "voice_id_model": vid}
    DONE.parent.mkdir(parents=True, exist_ok=True)
    DONE.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    log("ear: installed (%s); voice ID model %s" % (how, "checked" if rec["voice_id"] else
                                                      "absent: %s" % vid.get("why", "not fetched")))
    return rec


def install_voice_id(run=_run, log=print, fetch=None) -> dict:
    """The voice-ID engine's model, at its pinned revision, checked by sha256 (wespeaker_onnx.fetch_model): the same
    file the Register coach writes prints with. Already there and matching: nothing is fetched. A failed or
    mismatched download leaves no file behind and says why; the ear then runs without voice ID."""
    try:
        egress.refuse_install("the voice ID model")
        return wespeaker_onnx.fetch_model(HOME, fetch=fetch or _fetch, log=lambda m: log("ear: " + m))
    except Exception as e:  # noqa: BLE001
        why = "%s: %s" % (type(e).__name__, str(e)[:200])
        log("ear: voice ID model not installed: " + why)
        return {"ok": False, "why": why}


def status() -> dict:
    try:
        rec = json.loads(DONE.read_text(encoding="utf-8")) if DONE.is_file() else {}
    except ValueError:
        rec = {}
    return {"installed": installed(), "env": str(ENV), "record": rec, "voice_id_model": wespeaker_onnx.model_ok(HOME),
            "lock": (locks.lock_path("ear", version=pyenv.target_version()) or Path("")).name or None}


if __name__ == "__main__":
    print(json.dumps(install(), indent=1))
