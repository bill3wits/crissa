"""The presence module's own Python env and models, installed on first use on the user's machine (2026-10-09).

The module's three scripts (crissa-presence.py, crissa-camera.py, crissa-enroll.py) import this and re-run
themselves in the env it builds; the launcher (scripts/crissa.py) starts them only once `install.json` is there.

    <state home>/presence/env            a venv from locks/presence-<target>-py312.txt (pip --require-hashes
                                         --no-deps): numpy, opencv-python-headless, mediapipe, onnxruntime, Pillow,
                                         python-pam - the service, the grabber and the registration panel share it
    <state home>/oracle/models/...       MediaPipe's face_landmarker.task and AuraFace's glintr100.onnx, the oracle
                                         module's own files in its own place (one download serves both)
    <state home>/models/voice-id/...     the voice-ID engine's model (wespeaker_onnx.py: WeSpeaker's published ONNX of
                                         the house's ResNet221 checkpoint, pinned by revision, checked by sha256) -
                                         the Register coach writes the User's voice prints with it, the ear matches
                                         with it; one file for every module (2026-10-10)
    <state home>/presence/install.json   the install record (how the env was made, which lock, the models)

A machine with no lock for its platform or Python is told so and installs the same packages by the version pins in
locks/presence.in without a hash check. Every download goes through the egress switch (lib/crissa_egress), like the
voice, oracle and id modules. Same shape as modules/id/crissa_id/setup.py.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

from . import paths as _paths
from . import wespeaker_onnx

ROOT = _paths.ROOT
ENV = ROOT / "env"
DONE = ROOT / "install.json"          # what scripts/crissa.py presence_module_installed() reads
MODULE = Path(__file__).resolve().parents[1]
LOCKS = MODULE / "locks"
PYPI = "https://pypi.org/simple"
IN_ENV_FLAG = "CRISSA_PRESENCE_IN_ENV"


def env_python() -> Path:
    return ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed() -> bool:
    return DONE.is_file() and env_python().exists()


def in_env() -> bool:
    try:
        return Path(sys.prefix).resolve() == ENV.resolve()
    except OSError:
        return False


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
    p = LOCKS / ("presence-%s-py%d%d.txt" % (target(plat, machine), v[0], v[1]))
    return p if p.is_file() else None


def pins() -> list[str]:
    """The version pins in locks/presence.in (the fallback for a machine with no lock)."""
    out = []
    try:
        for line in (LOCKS / "presence.in").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and re.match(r"^[A-Za-z0-9_.\-]+", line):
                out.append(line)
    except OSError:
        pass
    return out or ["numpy", "opencv-python-headless", "mediapipe", "onnxruntime", "Pillow", "python-pam"]


def unsupported(plat: str | None = None, machine: str | None = None) -> str:
    """'' when this machine can run the engine; else why not (one line)."""
    t = target(plat, machine)
    if t.endswith("unknown"):
        return "no face engine build for this machine (%s)" % t
    if not (t.startswith("linux-") or t.startswith("macos-")):
        return "the presence module installs on Linux and macOS (%s)" % t
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
    _egress().refuse_install("the presence module's Python packages")
    if not env_python().exists():
        log("presence: making the env at %s ..." % ENV)
        run([sys.executable, "-m", "venv", str(ENV)])
    py = str(env_python())
    pip = [py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    v = sys.version_info[:2]
    lp = lock_path(version=v)
    if lp is not None:
        log("presence: installing %s (hash lock) ..." % lp.name)
        run(pip + ["--require-hashes", "--no-deps", "--index-url", PYPI, "-r", str(lp)])
        return "lock:" + lp.name
    why = "no hash lock for %s on Python %d.%d" % (target(), v[0], v[1])
    log("presence: %s; installing by the version pins without a hash check (Python 3.12 has locks)" % why)
    run(pip + pins())
    return "pins (%s)" % why


def models(log=print) -> dict:
    """The two model files, into the oracle module's place (face_engine.install): shared with the oracle and the id
    module, so one download serves all three."""
    from . import face_engine
    if not face_engine.installed():
        _egress().refuse_install("the presence module's face models")
    return face_engine.install(log=log)


def _fetch(url: str, dest: Path) -> None:
    with _egress().urlopen(url, 600.0, "the voice ID model") as r, open(dest, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def voice_id_model(log=print, fetch=None) -> dict:
    """The model the Register coach writes voice prints with (wespeaker_onnx.fetch_model: pinned revision, sha256).
    Present and matching: nothing is fetched. A failure says why and leaves no file; Voice ID then refuses to bank a
    print (the coach says the encoder returned none) rather than writing one nothing can match."""
    try:
        _egress().refuse_install("the voice ID model")
        return wespeaker_onnx.fetch_model(_paths.HOME, fetch=fetch or _fetch, log=lambda m: log("presence: " + m))
    except Exception as e:  # noqa: BLE001
        why = "%s: %s" % (type(e).__name__, str(e)[:200])
        log("presence: voice ID model not installed: " + why)
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
    vid = voice_id_model(log=log)
    rec = {"ok": True, "env": how, "python": str(env_python()), "models": got, "voice_id": vid,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    ROOT.mkdir(parents=True, exist_ok=True)
    DONE.write_text(json.dumps(rec, indent=1) + "\n")
    log("presence: installed (%s); models %s" % (how, "present" if all(got.values()) else got))
    return rec


def status() -> dict:
    try:
        rec = json.loads(DONE.read_text(encoding="utf-8")) if DONE.is_file() else {}
    except ValueError:
        rec = {}
    return {"installed": installed(), "env": str(ENV), "record": rec,
            "lock": (lock_path(version=sys.version_info[:2]) or Path("")).name or None}


def reexec_in_env(argv: list[str]) -> None:
    """Run `argv` under the module env's Python when this process is not already in that env (the three scripts).

    "In the env" is sys.prefix, never the interpreter's resolved path: a venv's bin/python is a symlink to its base
    Python, so the player's .venv and this env (made from it) resolve to the same file, and the old check never
    re-ran anything - the service, the grabber and the Register coach ran in the player's .venv, outside this
    module's lock, and the coach had no onnxruntime for a voice print (the voice-print proof run, 2026-10-10)."""
    py = env_python()
    if py.exists() and not in_env() and not os.environ.get(IN_ENV_FLAG):
        env = dict(os.environ, **{IN_ENV_FLAG: "1"},
                   PYTHONPATH=str(MODULE) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""))
        os.execve(str(py), [str(py)] + argv, env)


if __name__ == "__main__":
    print(json.dumps(install(), indent=1))
