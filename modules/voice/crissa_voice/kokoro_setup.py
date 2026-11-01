"""The voice module's own TTS, Kokoro-82M, installed on first use (2026-10-05).

Kokoro-82M as ONNX (Apache-2.0, Hugging Face onnx-community/Kokoro-82M-v1.0-ONNX at a pinned revision) runs on
the CPU. Words become phonemes through misaki (Apache-2.0) with its own out-of-dictionary model, a small BART
(PeterReid/graphemes_to_phonemes_en_us / _en_gb, Apache-2.0). No espeak: misaki is installed WITHOUT its `[en]`
extra, which would pull phonemizer + espeakng-loader (a GPL espeak-ng library). check_no_espeak() refuses an env
that has either, and the install says so and stops.

`python3 -m crissa_voice.kokoro_setup` (or scripts/crissa-tts.py --install) builds a private Python env and
fetches the models, on the user's own machine, from PyPI, the PyTorch CPU index, GitHub (the spaCy English
model) and Hugging Face. About 0.4 GB of model and 1 GB of packages. Nothing is redistributed.

    <crissa home>/tts/env      the venv (python -m venv)
    <crissa home>/tts/model    the Kokoro ONNX repo at MODEL_REV (onnx/model.onnx, tokenizer.json, voices/)
    <crissa home>/tts/g2p-us   misaki's fallback for American English, at its pinned revision
    <crissa home>/tts/g2p-gb   and for British English

Offline (the User 2026-10-05, like the aligner, speech in and the face check): `--bundle-build DIR` on a connected
machine like this one, with the same Python minor version, packs every wheel the env needs and the three model
folders into one file (crissa_bundle); `--bundle-install FILE` installs from it with pip --no-index and no network.
Under kill-search the online install and making a bundle are refused; installing a bundle is not.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import egress, locks, pyenv


def _home() -> Path:
    env = os.environ.get("CRISSA_HOME")
    return Path(env).expanduser() if env else Path.home() / ".crissa"


ROOT = _home() / "tts"
ENV = ROOT / "env"
MODEL = ROOT / "model"
DONE = ROOT / "installed.json"
MODEL_REPO = "onnx-community/Kokoro-82M-v1.0-ONNX"
MODEL_REV = "1939ad2a8e416c0acfeecc08a694d14ef25f2231"
# fp32: on the CPU it is the fast one (measured 2026-10-05, 3 threads, warm: rtf 0.13 vs 0.39-0.56 for the
# 8-bit file, whose dynamic-quant ops run slower than the float ones they replace).
MODEL_FILES = ["onnx/model.onnx", "tokenizer.json", "config.json"]
G2P = {
    "us": ("PeterReid/graphemes_to_phonemes_en_us", "a5631b285d18d59483c32c0c3379cb9fac924f4b"),
    "gb": ("PeterReid/graphemes_to_phonemes_en_gb", "d8357d5067fa26a5c34134d6bbcf4bbf000c0ac8"),
}
G2P_FILES = ["config.json", "generation_config.json", "model.safetensors"]
# The ASR env's pins where the two share a package (one day they can share an env); misaki last, --no-deps.
# Every one of these, and everything they pull in, is pinned with its sha256 in locks/kokoro-*.txt (locks.py);
# this list is the fallback for a machine with no lock, and what a bundle carries.
PACKAGES = ["numpy==1.26.4", "onnxruntime==1.18.0", "transformers==4.51.3", "spacy==3.8.16",
            "num2words==0.5.14", "addict==2.4.0", "regex==2024.11.6"]
SPACY_MODEL = ("https://github.com/explosion/spacy-models/releases/download/"
               "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl")
MISAKI = "misaki==0.9.4"
TORCH = ["torch==2.6.0"]
TORCH_INDEX = "https://download.pytorch.org/whl/cpu"
# Never in this env: each one carries or loads the GPL espeak-ng library.
ESPEAK_DISTS = ("espeakng-loader", "espeakng_loader", "phonemizer", "phonemizer-fork", "piper-phonemize")


def env_python() -> Path:
    return ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def g2p_dir(accent: str) -> Path:
    return ROOT / ("g2p-" + accent)


def installed() -> bool:
    return (DONE.is_file() and env_python().exists() and (MODEL / ".complete").is_file()
            and all((g2p_dir(a) / ".complete").is_file() for a in G2P))


def _fetch(url: str, dest: Path, timeout: float = 300.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with egress.urlopen(url, timeout, "the Kokoro voice download") as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(tmp, dest)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(cmd[:4]), (r.stderr or r.stdout)[-400:]))
    return r


def make_env(run=_run, log=print) -> str:
    """Build the venv once; the package step always runs (pip skips what is already there). From the hash lock
    for this machine (locks/kokoro-<target>-py<v>.txt, --require-hashes --no-deps) when there is one, else by
    the exact pins below, said on screen. Returns how it installed."""
    log("kokoro: " + pyenv.make(ENV, run, log))
    torch = (["--index-url", TORCH_INDEX] if sys.platform.startswith("linux") else []) + TORCH   # the CPU build
    return locks.pip_install(env_python(), "kokoro", run, log, version=pyenv.target_version(),
                             fallback=[torch, PACKAGES + [SPACY_MODEL], ["--no-deps", MISAKI]])  # NOT misaki[en]


def check_no_espeak(run=_run) -> None:
    """Refuse an env that holds any espeak carrier, whatever put it there."""
    r = run([str(env_python()), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"])
    names = {str(d.get("name", "")).lower().replace("_", "-") for d in json.loads(r.stdout or "[]")}
    bad = sorted(n for n in names if n in {e.replace("_", "-") for e in ESPEAK_DISTS})
    if bad:
        raise RuntimeError("the TTS env holds %s (GPL espeak); remove it or rebuild %s" % (", ".join(bad), ENV))


def _get_repo(repo: str, rev: str, dest: Path, want, fetch) -> None:
    if (dest / ".complete").is_file():
        return
    listing = dest.parent / ("tree-%s.json" % dest.name)     # beside its folder: a bundle build never writes ROOT
    listing.parent.mkdir(parents=True, exist_ok=True)
    fetch("https://huggingface.co/api/models/%s/tree/%s?recursive=1" % (repo, rev), listing)
    files = [e["path"] for e in json.loads(listing.read_text()) if e.get("type") == "file" and want(e["path"])]
    if not files:
        raise RuntimeError("the %s listing named no files" % repo)
    for rel in files:
        fetch("https://huggingface.co/%s/resolve/%s/%s" % (repo, rev, rel), dest / rel)
    (dest / ".complete").write_text(rev + "\n")
    listing.unlink()


def get_models(fetch=_fetch, into: Path | None = None) -> None:
    """The model and the two G2P fallbacks, into ROOT (or `into`, for a bundle)."""
    base = Path(into) if into else ROOT
    _get_repo(MODEL_REPO, MODEL_REV, base / MODEL.name,
              lambda p: p in MODEL_FILES or (p.startswith("voices/") and p.endswith(".bin")), fetch)
    for accent, (repo, rev) in G2P.items():
        _get_repo(repo, rev, base / g2p_dir(accent).name, lambda p: p in G2P_FILES, fetch)


def python_advice(plat: str | None = None) -> str:
    """What to do about a wrong Python (2026-10-07): the env makes its own (pyenv.py); only its switch being off
    leaves the running Python in charge."""
    return pyenv.ADVICE


def unsupported(version=None, plat: str | None = None, machine: str | None = None) -> str:
    """'' when this Python can build the env, else why not, said before anything downloads. misaki needs
    3.8-3.13, the shared pins have wheels for 3.9-3.12, and torch has no Intel Mac build after 2.2.
    Kill CRISSA_TTS_ANY_PYTHON=1."""
    if os.environ.get("CRISSA_TTS_ANY_PYTHON", "").strip() == "1":
        return ""
    v = tuple(version or pyenv.target_version())
    p = plat or sys.platform
    m = (machine or platform.machine()).lower()
    if not ((3, 9) <= v <= (3, 12)):
        return ("the Kokoro voice needs Python 3.9 to 3.12 (this is %d.%d). " % v) + python_advice(p)
    if p == "darwin" and m in ("x86_64", "i386"):
        return ("the Kokoro voice needs an Apple Silicon Mac: torch 2.6 has no Intel Mac build. "
                "On Apple Silicon, run the installer with an arm64 Python, not an Intel one under Rosetta.")
    return ""


def install(log=print) -> dict:
    why = unsupported()
    if why:
        raise RuntimeError(why)
    egress.refuse_install("the Kokoro voice")
    ROOT.mkdir(parents=True, exist_ok=True)
    log("tts: building the Python env (torch CPU, onnxruntime, misaki without espeak) …")
    how = make_env(log=log)
    check_no_espeak()
    log("tts: fetching %s @ %s and the G2P fallbacks …" % (MODEL_REPO, MODEL_REV[:8]))
    get_models()
    rec = {"model": MODEL_REPO, "rev": MODEL_REV, "g2p": {a: list(r) for a, r in G2P.items()},
           "packages": PACKAGES + TORCH + [MISAKI, SPACY_MODEL], "installed_by": how}
    DONE.write_text(json.dumps(rec) + "\n")
    log("tts: installed in %s" % ROOT)
    return rec


# ---- the offline bundle (crissa_bundle; the User 2026-10-05) -------------------------------------------------
# Made on a connected machine like this one, with the same Python minor version (the wheels are tied to it). pip
# WHEEL, not pip download --only-binary: num2words needs docopt, which PyPI has as a source package only, so the
# build turns it into a wheel here and the offline machine never builds anything. misaki goes in with --no-deps
# on both sides, so no espeak carrier can come in through a bundle either (check_no_espeak runs after install).
SPACY_MODEL_REQ = "en_core_web_sm==3.8.0"        # the name SPACY_MODEL's wheel installs under, from the bundle


def _bundle_mod():
    try:
        from . import crissa_bundle as B          # the copy the pack puts beside this file
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
        import crissa_bundle as B                 # noqa: E402
    return B


def _pins() -> dict:
    return {"model": MODEL_REPO, "model_rev": MODEL_REV, "g2p": {a: list(r) for a, r in G2P.items()},
            "packages": PACKAGES + TORCH + [MISAKI, SPACY_MODEL]}


def build_bundle(out_dir: Path, run=_run, fetch=_fetch, log=print) -> Path:
    import tempfile
    B = _bundle_mod()
    why = unsupported()
    if why:
        raise RuntimeError(why)
    egress.refuse_install("making the Kokoro bundle")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="kokoro-bundle-", dir=out_dir))
    try:
        folder = work / "kokoro"
        wheels = folder / "wheels"
        wheels.mkdir(parents=True)
        wh = [sys.executable, "-m", "pip", "wheel", "--quiet", "--disable-pip-version-check", "--wheel-dir", str(wheels)]
        log("kokoro bundle: torch (CPU) …")
        run(wh + (["--index-url", TORCH_INDEX] if sys.platform.startswith("linux") else []) + TORCH)
        log("kokoro bundle: the packages and the spaCy model …")
        run(wh + PACKAGES + [SPACY_MODEL])
        run(wh + ["--no-deps", MISAKI])
        log("kokoro bundle: the model and the G2P fallbacks …")
        get_models(fetch, into=folder)
        tar = B.write(folder, "kokoro", pins=_pins(), python=B.python_tag(),
                      out=out_dir / ("kokoro-%s-py%s%s" % (B.platform_tag(), B.python_tag(), B.SUFFIX)))
        log("kokoro bundle: %s" % tar)
        return tar
    finally:
        shutil.rmtree(work, ignore_errors=True)


def install_bundle(path: Path, run=_run, log=print) -> dict:
    """Install from a bundle with no network. Refused before anything is written when the bundle is not this
    machine's, not this Python's, or carries other models than this module expects."""
    B = _bundle_mod()
    why = unsupported()
    if why:
        raise RuntimeError(why)
    folder, man = B.open_bundle(Path(path), "kokoro", python=B.python_tag())
    try:
        pins = man.get("pins") or {}
        if pins.get("model_rev") != MODEL_REV or (pins.get("g2p") or {}) != {a: list(r) for a, r in G2P.items()}:
            raise B.BundleRefused("the bundle's models are not the ones this module expects")
        ROOT.mkdir(parents=True, exist_ok=True)
        if not env_python().exists():
            run([sys.executable, "-m", "venv", str(ENV)])
        log("tts: installing the env from the bundle (offline) …")
        pip = [str(env_python()), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-index",
               "--find-links", str(folder / "wheels")]
        run(pip + TORCH + PACKAGES + [SPACY_MODEL_REQ])
        run(pip + ["--no-deps", MISAKI])
        check_no_espeak(run)
        B.copy_tree(folder / MODEL.name, MODEL)
        for accent in G2P:
            B.copy_tree(folder / g2p_dir(accent).name, g2p_dir(accent))
        rec = dict(_pins(), rev=MODEL_REV, **{"from": "bundle", "built_at": man.get("built_at")})
        DONE.write_text(json.dumps(rec) + "\n")
        log("tts: installed from the bundle in %s" % ROOT)
        return rec
    finally:
        if not Path(path).is_dir():
            shutil.rmtree(folder, ignore_errors=True)


def in_env() -> bool:
    """Is this interpreter the TTS env's? A venv's python is a symlink to its base, so compare prefixes."""
    try:
        return Path(sys.prefix).resolve() == ENV.resolve()
    except OSError:
        return False


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="the Kokoro voice: install online, or make / install an offline bundle")
    ap.add_argument("--bundle-build", metavar="DIR", help="make an offline bundle in DIR (on a connected machine)")
    ap.add_argument("--bundle-install", metavar="FILE", help="install from a bundle, no network")
    a = ap.parse_args()
    if a.bundle_build:
        build_bundle(Path(a.bundle_build))
    elif a.bundle_install:
        install_bundle(Path(a.bundle_install))
    else:
        install()
