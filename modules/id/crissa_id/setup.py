"""The id module's own Python env and models, installed on first use on the user's machine (2026-10-09).

    <state home>/id/env                      a venv from locks/id-<target>-py312.txt (pip --require-hashes --no-deps):
                                             numpy, onnxruntime, mediapipe, opencv-python-headless, torch + torchaudio
                                             (CPU; the WeSpeaker features are a kaldi fbank)
    <state home>/oracle/models/...           the face landmarker and AuraFace's recognition model, the oracle module's
                                             own files in the oracle module's own place (one download serves both)
    <state home>/models/voice-id/...         the voice-ID engine's model (wespeaker_onnx.py: WeSpeaker's published ONNX
                                             of the house's ResNet221 checkpoint, pinned by revision, checked by
                                             sha256), one file for this module, the ear and the presence module's coach
    <state home>/id/installed.json           the install record (how the env was made, which lock, what is missing)

A machine with no lock for its platform or Python is told so and installs the same exact versions without hashes.
Every download goes through the egress switch (crissa_egress), like the voice and oracle modules.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from . import paths as _paths
from . import wespeaker_onnx

ROOT = _paths.STATE / "id"
ENV = ROOT / "env"
MODELS = ROOT / "models"
DONE = ROOT / "installed.json"
LOCKS = _paths.MODULE / "locks"
PYPI = "https://pypi.org/simple"
TORCH_CPU = "https://download.pytorch.org/whl/cpu"
# the exact versions the locks were made from (locks/id.in); the fallback for a machine with no lock
PACKAGES = ["numpy==1.26.4", "onnxruntime==1.18.0", "mediapipe==0.10.21", "opencv-python-headless==4.10.0.84"]
TORCH = ["torch==2.6.0", "torchaudio==2.6.0"]
WESPEAKER_ONNX = wespeaker_onnx.default_model(_paths.STATE)


def env_python() -> Path:
    return ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed() -> bool:
    return DONE.is_file() and env_python().exists()


def target(plat: str | None = None, machine: str | None = None) -> str:
    p = plat or sys.platform
    m = (machine or platform.machine() or "").lower()
    if p == "darwin":
        return "macos-arm64" if m in ("arm64", "aarch64") else "macos-" + (m or "unknown")
    if p.startswith("linux"):
        arch = "aarch64" if m in ("aarch64", "arm64") else ("x86_64" if m in ("x86_64", "amd64") else (m or "unknown"))
        return "linux-%s-cpu" % arch
    return p


def lock_path(plat: str | None = None, machine: str | None = None, version=None) -> Path | None:
    v = tuple(version or (3, 12))
    p = LOCKS / ("id-%s-py%d%d.txt" % (target(plat, machine), v[0], v[1]))
    return p if p.is_file() else None


def unsupported(plat: str | None = None, machine: str | None = None) -> str:
    """'' when this machine can run the engine; else why not (one line)."""
    t = target(plat, machine)
    if t.endswith("unknown"):
        return "no face engine build for this machine (%s)" % t
    return ""


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(cmd[:4]), (r.stderr or r.stdout)[-400:]))


def _egress():
    try:
        from . import crissa_egress as E            # the copy a pack puts beside this file
    except ImportError:
        sys.path.insert(0, str(_paths.PACK / "lib"))
        import crissa_egress as E                   # noqa: E402
    return E


def make_env(run=_run, log=print) -> str:
    """The venv from the running Python (3.12 has locks; another Python installs by pins and says so)."""
    _egress().refuse_install("the id module's Python packages")
    if not env_python().exists():
        log("id: making the env at %s ..." % ENV)
        run([sys.executable, "-m", "venv", str(ENV)])
    py = str(env_python())
    pip = [py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    v = sys.version_info[:2]
    lp = lock_path(version=v)
    tgt = target()
    if lp is not None:
        log("id: installing %s (hash lock) ..." % lp.name)
        cmd = pip + ["--require-hashes", "--no-deps"]
        if tgt.startswith("linux-"):
            cmd += ["--index-url", TORCH_CPU, "--extra-index-url", PYPI]
        run(cmd + ["-r", str(lp)])
        return "lock:" + lp.name
    why = "no hash lock for %s on Python %d.%d" % (tgt, v[0], v[1])
    log("id: %s; installing by exact version pins without a hash check (Python 3.12 has locks)" % why)
    if tgt.startswith("linux-"):
        run(pip + ["--index-url", TORCH_CPU] + TORCH)
    else:
        run(pip + TORCH)
    run(pip + PACKAGES)
    return "pins (%s)" % why


def models(log=print) -> dict:
    """The face models through the oracle module's own installer (shared files), AuraFace included."""
    os.environ["CRISSA_ORACLE_AURAFACE"] = "1"
    oracle = _paths.MODULE.parent / "oracle"
    if str(oracle) not in sys.path:
        sys.path.insert(0, str(oracle))
    try:
        from crissa_oracle import setup as osetup    # type: ignore
    except ImportError:
        return {"landmarker": False, "auraface": False, "why": "the oracle module is not installed beside this one"}
    return osetup.install(log=log)


def _fetch(url: str, dest: Path) -> None:
    with _egress().urlopen(url, 600.0, "the voice ID model") as r, open(dest, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def voice_id_model(log=print, fetch=None) -> dict:
    """The voice-ID engine's model at its pinned revision, checked by sha256 (wespeaker_onnx.fetch_model): what
    Register writes the User's voice prints with, and what the ear matches them with. Present and matching: nothing
    is fetched. A failure says why and leaves no file; voice prints then wait (voice_embed.status() says so)."""
    try:
        _egress().refuse_install("the voice ID model")
        return wespeaker_onnx.fetch_model(_paths.STATE, fetch=fetch or _fetch, log=lambda m: log("id: " + m))
    except Exception as e:  # noqa: BLE001
        why = "%s: %s" % (type(e).__name__, str(e)[:200])
        log("id: voice ID model not installed: " + why)
        return {"ok": False, "why": why}


def install(log=print) -> dict:
    why = unsupported()
    if why:
        rec = {"ok": False, "why": why, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        ROOT.mkdir(parents=True, exist_ok=True)
        DONE.write_text(json.dumps(rec, indent=1) + "\n")
        return rec
    how = make_env(log=log)
    got = models(log=log)
    ws = voice_id_model(log=log)
    rec = {"ok": True, "env": how, "python": str(env_python()), "models": got, "voice_id": ws,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    ROOT.mkdir(parents=True, exist_ok=True)
    DONE.write_text(json.dumps(rec, indent=1) + "\n")
    return rec


def in_env() -> bool:
    try:
        return Path(sys.prefix).resolve() == ENV.resolve()
    except OSError:
        return False


def reexec_in_env(argv: list[str]) -> None:
    """Run `argv` under the id env's Python when this process is not already in that env (the coach and the CLI).
    "In the env" is sys.prefix: a venv's python resolves to its base Python, the same file for every venv made from
    it (the presence module's reexec_in_env, 2026-10-10)."""
    py = env_python()
    if py.exists() and not in_env() and not os.environ.get("CRISSA_ID_IN_ENV"):
        env = dict(os.environ, CRISSA_ID_IN_ENV="1",
                   PYTHONPATH=str(_paths.MODULE) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""))
        os.execve(str(py), [str(py)] + argv, env)
