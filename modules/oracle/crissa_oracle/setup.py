"""The oracle's models, fetched on first use on the user's own machine (2026-10-01). Nothing is redistributed.

  detector + landmarks  MediaPipe Face Landmarker (Apache-2.0), the `mediapipe` package and its
                        face_landmarker.task model from Google's model store.
  identity (optional)   AuraFace v1 (Apache-2.0, Hugging Face fal/AuraFace-v1 at a pinned revision):
                        ONLY its recognition model, glintr100.onnx. The repository also holds a
                        detector and landmark models of another lineage; they are never fetched.
                        OFF unless CRISSA_ORACLE_AURAFACE=1: without it the oracle only says whether
                        a face is there, not whose.

    <crissa home>/oracle/models/face_landmarker.task
    <crissa home>/oracle/models/auraface/glintr100.onnx
"""
from __future__ import annotations

import os
import shutil
import urllib.request
from pathlib import Path

from . import egress


def _home() -> Path:
    env = os.environ.get("CRISSA_HOME")
    return Path(env).expanduser() if env else Path.home() / ".crissa"


ROOT = _home() / "oracle"
MODELS = ROOT / "models"
LANDMARKER = MODELS / "face_landmarker.task"
LANDMARKER_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
                  "float16/1/face_landmarker.task")
AURAFACE_REPO = "fal/AuraFace-v1"
AURAFACE_REV = "af6d057c9b0ec4071d4c49c80e3539258798b609"
AURAFACE_FILE = "glintr100.onnx"          # the recognition model, and nothing else from that repository
AURAFACE = MODELS / "auraface" / AURAFACE_FILE
PACKAGES = ["mediapipe", "opencv-python-headless", "numpy"]
AURAFACE_PACKAGES = ["onnxruntime"]


def auraface_wanted(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_ORACLE_AURAFACE", "0")).strip().lower() in ("1", "on", "true", "yes")


# THE ORACLE NAMES THE FACE WHERE THE PRESENCE MODULE CAN (2026-10-12, .101 round 11: /v1/oracle read face true, faces
# 1, posted 0 all morning, so presence never had a look, the face block stayed the door glance's - 7 to 50 s old - and
# the ear's "the face decides" never could). The recogniser was opt-in (CRISSA_ORACLE_AURAFACE=1, set by no one), and
# the player's .venv the oracle runs in has no onnxruntime. Where the presence module is installed, its env has every
# package the recogniser needs (the same MediaPipe and AuraFace engine), and the AuraFace file it reads is this
# module's (the id module's --install fetched it): the service then runs there with the recogniser on, and its looks
# reach /v1/see. Without the presence module, or with CRISSA_ORACLE_AURAFACE=0 named, nothing changes.
# Kill: CRISSA_ORACLE_FACE_ENV=0.
FACE_ENV_FLAG = "CRISSA_ORACLE_IN_FACE_ENV"


def face_env_python(env: dict | None = None, home: Path | None = None) -> Path | None:
    """The presence module's Python when the oracle should name faces there and is not already in it, else None."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_ORACLE_FACE_ENV", "1")).strip().lower() in ("0", "off", "false", "no"):
        return None
    if str(e.get("CRISSA_ORACLE_AURAFACE", "")).strip().lower() in ("0", "off", "false", "no"):
        return None
    if e.get(FACE_ENV_FLAG) or not AURAFACE.is_file():
        return None
    pres = (home or _home()) / "presence"
    py = pres / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not ((pres / "install.json").is_file() and py.exists()):
        return None
    import sys
    try:
        if Path(sys.prefix).resolve() == (pres / "env").resolve():
            return None
    except OSError:
        pass
    return py


def recogniser_state() -> str:
    """Why the oracle does or does not name faces: 'auraface', or the reason it cannot (shown on /v1/oracle)."""
    if not auraface_wanted():
        return "off: CRISSA_ORACLE_AURAFACE is not 1 (no presence module env to run in)"
    if not AURAFACE.is_file():
        return "off: no AuraFace model at %s" % AURAFACE
    try:
        import onnxruntime  # noqa: F401
    except Exception:  # noqa: BLE001
        return "off: no onnxruntime in this Python"
    return "auraface"


def _fetch(url: str, dest: Path, timeout: float = 300.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with egress.urlopen(url, timeout, "the oracle model download") as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(tmp, dest)


def auraface_url() -> str:
    return "https://huggingface.co/%s/resolve/%s/%s" % (AURAFACE_REPO, AURAFACE_REV, AURAFACE_FILE)


def install(fetch=_fetch, env: dict | None = None, log=print) -> dict:
    """Fetch what is missing. Packages are the user's own `pip install` (see the README)."""
    if not LANDMARKER.is_file() or (auraface_wanted(env) and not AURAFACE.is_file()):
        egress.refuse_install("the oracle's face models")
    if not LANDMARKER.is_file():
        log("oracle: fetching the face landmarker …")
        fetch(LANDMARKER_URL, LANDMARKER)
    if auraface_wanted(env) and not AURAFACE.is_file():
        log("oracle: fetching the AuraFace recognition model …")
        fetch(auraface_url(), AURAFACE)
    return {"landmarker": LANDMARKER.is_file(), "auraface": AURAFACE.is_file()}


# ---- the offline bundle (crissa_bundle; the User 2026-10-05) -------------------------------------------------
# The models only (they are the same files on every machine); the packages stay the user's own pip install.

def _bundle_mod():
    import sys
    try:
        from . import crissa_bundle as B          # the copy the pack puts beside this file
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
        import crissa_bundle as B                 # noqa: E402
    return B


def build_bundle(out_dir: Path, fetch=_fetch, env: dict | None = None, log=print) -> Path:
    import tempfile
    B = _bundle_mod()
    egress.refuse_install("making the oracle bundle")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="oracle-bundle-", dir=out_dir))
    try:
        folder = work / "oracle"
        log("oracle bundle: the face landmarker …")
        fetch(LANDMARKER_URL, folder / LANDMARKER.name)
        pins = {"landmarker": LANDMARKER_URL}
        if auraface_wanted(env):
            log("oracle bundle: the AuraFace recognition model …")
            fetch(auraface_url(), folder / "auraface" / AURAFACE_FILE)
            pins.update(auraface=AURAFACE_REPO, auraface_rev=AURAFACE_REV)
        tar = B.write(folder, "oracle", pins=pins, platform="any", out=out_dir / ("oracle-any" + B.SUFFIX))
        log("oracle bundle: %s" % tar)
        return tar
    finally:
        shutil.rmtree(work, ignore_errors=True)


def install_bundle(path: Path, log=print) -> dict:
    """Install the models from a bundle, no network."""
    B = _bundle_mod()
    folder, man = B.open_bundle(Path(path), "oracle")
    try:
        pins = man.get("pins") or {}
        if pins.get("landmarker") != LANDMARKER_URL or (pins.get("auraface") and pins.get("auraface_rev") != AURAFACE_REV):
            raise B.BundleRefused("the bundle's models are not the ones this module expects")
        MODELS.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / LANDMARKER.name, LANDMARKER)
        if (folder / "auraface" / AURAFACE_FILE).is_file():
            AURAFACE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(folder / "auraface" / AURAFACE_FILE, AURAFACE)
        log("oracle: installed from the bundle")
        return {"landmarker": LANDMARKER.is_file(), "auraface": AURAFACE.is_file(), "from": "bundle"}
    finally:
        if not Path(path).is_dir():
            shutil.rmtree(folder, ignore_errors=True)

