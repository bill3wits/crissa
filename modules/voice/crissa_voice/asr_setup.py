"""The voice module's ASR, installed on first use (2026-10-01).

ARK-ASR 0.6B, INT8 ONNX (Apache-2.0, Hugging Face Edge0/ark-asr-0.6b-int8-onnx at a pinned revision),
runs on the CPU. Its bundled runtime needs torch and transformers, so it is never part of the light
install: `python3 -m crissa_voice.asr_setup` (or the first start of scripts/crissa-asr.py with --install)
builds a private Python env and fetches the model, on the user's own machine, from PyPI, the PyTorch CPU
index and Hugging Face. About 1.7 GB of model and 1 GB of packages. Nothing is redistributed.

    <crissa home>/asr/env      the venv (python -m venv)
    <crissa home>/asr/model    the model repo at MODEL_REV, with its infer_ark_audio_onnx.py
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import egress, locks, pyenv


def _home() -> Path:
    env = os.environ.get("CRISSA_HOME")
    return Path(env).expanduser() if env else Path.home() / ".crissa"


ROOT = _home() / "asr"
ENV = ROOT / "env"
MODEL = ROOT / "model"
BUILD = ROOT / "build"      # the runtime reads its LLM metadata from runtime_root/build (the house links them)
DONE = ROOT / "installed.json"
MODEL_REPO = "Edge0/ark-asr-0.6b-int8-onnx"
MODEL_REV = "ced3e6c0cc45eda7f718b885e9fd9562ed3ec94d"
# The versions the house's own ARK-ASR service runs (transformers, onnxruntime, numpy), torch from the CPU
# index (the model runs on onnxruntime; torch is only its feature extractor's dependency).
# Pinned with their sha256, and everything they pull in, in locks/asr-*.txt (locks.py); this list is the fallback
# for a machine with no lock, and what a bundle carries. librosa: the model's processor needs it.
PACKAGES = ["numpy==1.26.4", "onnxruntime==1.18.0", "transformers==4.51.3", "librosa==0.10.2"]
TORCH = ["torch==2.6.0"]
TORCH_INDEX = "https://download.pytorch.org/whl/cpu"


def env_python() -> Path:
    return ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed() -> bool:
    return DONE.is_file() and env_python().exists() and (MODEL / "runtime_manifest.json").is_file()


def _fetch(url: str, dest: Path, timeout: float = 300.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with egress.urlopen(url, timeout, "the speech-in model download") as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(tmp, dest)


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(cmd[:4]), (r.stderr or r.stdout)[-400:]))


def make_env(run=_run, log=print) -> str:
    """Build the venv once; the package step always runs (pip skips what is already there), so an env
    from an older pin list is repaired rather than trusted. From the hash lock for this machine
    (locks/asr-<target>-py<v>.txt, --require-hashes --no-deps) when there is one, else by the exact pins below,
    said on screen. Returns how it installed."""
    log("asr: " + pyenv.make(ENV, run, log))
    torch = (["--index-url", TORCH_INDEX] if sys.platform.startswith("linux") else []) + TORCH   # the CPU build
    return locks.pip_install(env_python(), "asr", run, log, version=pyenv.target_version(), fallback=[torch, PACKAGES])


def in_env() -> bool:
    """Is this interpreter the ASR env's? A venv's python is a symlink to its base, so compare prefixes."""
    try:
        return Path(sys.prefix).resolve() == ENV.resolve()
    except OSError:
        return False


def place_build() -> None:
    """The runtime's build/ is two metadata files that ship at the model's top level."""
    BUILD.mkdir(parents=True, exist_ok=True)
    for f in MODEL.glob("llm_kv_*.json"):
        shutil.copy2(f, BUILD / f.name)


def get_model(fetch=_fetch, dest: Path | None = None, scratch: Path | None = None) -> None:
    dest, scratch = dest or MODEL, scratch or ROOT
    if (dest / ".complete").is_file():
        if dest == MODEL:
            place_build()
        return
    scratch.mkdir(parents=True, exist_ok=True)
    listing = scratch / "model-tree.json"
    fetch("https://huggingface.co/api/models/%s/tree/%s?recursive=1" % (MODEL_REPO, MODEL_REV), listing)
    files = [e["path"] for e in json.loads(listing.read_text()) if e.get("type") == "file"]
    if "runtime_manifest.json" not in files or "infer_ark_audio_onnx.py" not in files:
        raise RuntimeError("the model listing is missing its runtime files")
    for rel in files:
        fetch("https://huggingface.co/%s/resolve/%s/%s" % (MODEL_REPO, MODEL_REV, rel), dest / rel)
    if dest == MODEL:
        place_build()
    (dest / ".complete").write_text(MODEL_REV + "\n")
    listing.unlink()


# ---- the offline bundle (crissa_bundle; the User 2026-10-05) -------------------------------------------------
# Made on a connected machine like this one, with the same Python minor version (the wheels are tied to it):
# every wheel the env needs and the model. Installed with pip --no-index from the bundle alone.

def _bundle_mod():
    try:
        from . import crissa_bundle as B          # the copy the pack puts beside this file
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
        import crissa_bundle as B                 # noqa: E402
    return B


def build_bundle(out_dir: Path, run=_run, fetch=_fetch, log=print) -> Path:
    import tempfile
    B = _bundle_mod()
    why = unsupported()
    if why:
        raise RuntimeError(why)
    egress.refuse_install("making the speech-in bundle")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="asr-bundle-", dir=out_dir))
    try:
        folder = work / "asr"
        wheels = folder / "wheels"
        wheels.mkdir(parents=True)
        log("asr bundle: the wheels …")
        dl = [sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check", "--only-binary=:all:",
              "--dest", str(wheels)]
        run(dl + (["--index-url", TORCH_INDEX] if sys.platform.startswith("linux") else []) + TORCH)
        run(dl + PACKAGES)
        log("asr bundle: the model …")
        get_model(fetch, folder / "model", work / "scratch")
        shutil.rmtree(work / "scratch", ignore_errors=True)
        tar = B.write(folder, "asr", pins={"model": MODEL_REPO, "model_rev": MODEL_REV, "packages": PACKAGES + TORCH},
                      python=B.python_tag(), out=out_dir / ("asr-%s-py%s%s" % (B.platform_tag(), B.python_tag(), B.SUFFIX)))
        log("asr bundle: %s" % tar)
        return tar
    finally:
        shutil.rmtree(work, ignore_errors=True)


def install_bundle(path: Path, run=_run, log=print) -> dict:
    """Install from a bundle with no network. Refused before anything is written when the bundle is not this
    machine's, or not this Python's."""
    B = _bundle_mod()
    why = unsupported()
    if why:
        raise RuntimeError(why)
    folder, man = B.open_bundle(Path(path), "asr", python=B.python_tag())
    try:
        if (man.get("pins") or {}).get("model_rev") != MODEL_REV:
            raise B.BundleRefused("the bundle's model is not the one this module expects")
        ROOT.mkdir(parents=True, exist_ok=True)
        if not env_python().exists():
            run([sys.executable, "-m", "venv", str(ENV)])
        log("asr: installing the env from the bundle (offline) …")
        run([str(env_python()), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-index",
             "--find-links", str(folder / "wheels")] + TORCH + PACKAGES)
        B.copy_tree(folder / "model", MODEL)
        place_build()
        rec = {"model": MODEL_REPO, "rev": MODEL_REV, "packages": PACKAGES + TORCH, "from": "bundle",
               "built_at": man.get("built_at")}
        DONE.write_text(json.dumps(rec) + "\n")
        log("asr: installed from the bundle")
        return rec
    finally:
        if not Path(path).is_dir():
            shutil.rmtree(folder, ignore_errors=True)


def python_advice(plat: str | None = None) -> str:
    """What to do about a wrong Python (2026-10-07): the env makes its own (pyenv.py); only its switch being off
    leaves the running Python in charge."""
    return pyenv.ADVICE


def unsupported(version=None, plat: str | None = None, machine: str | None = None) -> str:
    """'' when this Python can build the env, else why not, said before anything downloads (2026-10-04).
    The pins have wheels for CPython 3.9-3.12 only, and torch has no Intel Mac build after 2.2 (an Apple
    Silicon Mac running an Intel Python under Rosetta reads as Intel too). Kill CRISSA_ASR_ANY_PYTHON=1."""
    if os.environ.get("CRISSA_ASR_ANY_PYTHON", "").strip() == "1":
        return ""
    v = tuple(version or pyenv.target_version())
    p = plat or sys.platform
    m = (machine or platform.machine()).lower()
    if not ((3, 9) <= v <= (3, 12)):
        return ("speech-to-text needs Python 3.9 to 3.12 (this is %d.%d): its pinned numpy and onnxruntime "
                "have no wheels for it. " % v) + python_advice(p)
    if p == "darwin" and m in ("x86_64", "i386"):
        return ("speech-to-text needs an Apple Silicon Mac: torch 2.6 has no Intel Mac build. "
                "On Apple Silicon, run the installer with an arm64 Python, not an Intel one under Rosetta.")
    return ""


def install(log=print) -> dict:
    why = unsupported()
    if why:
        raise RuntimeError(why)
    egress.refuse_install("speech in (the ASR)")
    ROOT.mkdir(parents=True, exist_ok=True)
    log("asr: building the Python env (torch CPU, onnxruntime, transformers) …")
    how = make_env(log=log)
    log("asr: fetching %s @ %s …" % (MODEL_REPO, MODEL_REV[:8]))
    get_model()
    rec = {"model": MODEL_REPO, "rev": MODEL_REV, "packages": PACKAGES + TORCH, "installed_by": how}
    DONE.write_text(json.dumps(rec) + "\n")
    log("asr: installed in %s" % ROOT)
    return rec


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="speech in: install online, or make / install an offline bundle")
    ap.add_argument("--bundle-build", metavar="DIR", help="make an offline bundle in DIR (on a connected machine)")
    ap.add_argument("--bundle-install", metavar="FILE", help="install from a bundle, no network")
    a = ap.parse_args()
    if a.bundle_build:
        build_bundle(Path(a.bundle_build))
    elif a.bundle_install:
        install_bundle(Path(a.bundle_install))
    else:
        install()
