"""What THIS computer can do, in one place (the User 2026-10-10, after .101: a machine with no NVIDIA GPU and no krew).

The Control Panels (:8097 and the feed's :8099/panel) and the Crissa key's menu ask features() and agree: a feature
this computer cannot run is shown greyed with one short reason, or folded into one "Not on this computer" group,
never as a live key or an error. The facts already exist; this reads them, it does not measure:

    nvidia   an NVIDIA GPU: the voice tier record's probe (<state home>/voice-tier.json facts, written by Init), else
             the same probe now (lib/crissa_voice_tiers.probe: nvidia-smi); CUDA_VISIBLE_DEVICES="" hides it, as it
             hides it from every engine; None when this install has no probe (then nothing is blanked for it)
    krew     the krew add-on installed (lib/crissa_addon_link: its endpoint offer or its panel manifest)
    camera   a camera for Crissa: not switched off (CRISSA_NO_CAMERA=1) and a video device (or CRISSA_CAMERA_URL)
    house    the house's own install: everything is the house's, nothing is blanked

FEATURES, each {"ok", "why", "label"}:
    gpu_modes    the Mode row's Beast, Render, heavy-render and Park GPU            needs an NVIDIA GPU
    render       Render mode and its studio (Comfy on the GPU)                       needs an NVIDIA GPU
    turbo        Chatterbox Turbo, her GPU voice                                    needs an NVIDIA GPU
    multilingual Chatterbox Multilingual, the GPU voice for other languages          needs an NVIDIA GPU
    keep_warm    keeping a GPU voice warm                                           needs an NVIDIA GPU
    krew         the krew tiles, the board and tools tapes, the Krew key            krew is not installed
    turbokrew    TurboKrew (krew sharing the house's engine)                        needs krew and an NVIDIA GPU
    camera       the camera tiles: reset, its log, the door mark, the wanted poster no camera on this computer
    house_glass  the house's own glass keys: its kiosk, Brave glass, ZOS desktop    the house's own, not in Crissa
    house_only   the house's janitor and its engines' journal (ops)                 the house's own, not in Crissa
    house_register the feed panel's Register an ID, New User initialization and its   Crissa's Setup is /init; Voice ID
                 First Boot tick (the house's own routine; Crissa has Init)           again is in the Crissa menu
Kill: CRISSA_CAPABILITY=0 (every feature shown as before).
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crissa_home  # noqa: E402

NEEDS_GPU = "needs an NVIDIA GPU"
NO_KREW = "krew is not installed"
NO_CAMERA = "no camera on this computer"
HOUSE_ONLY = "the house's own, not in Crissa"
IN_CRISSA_MENU = "in Crissa: Setup is at /init, and Voice ID again is in the Crissa key's menu"
LABELS = {
    "gpu_modes": "GPU modes (Beast, Render, Park GPU)",
    "render": "Render and its studio",
    "turbo": "Chatterbox Turbo voice",
    "multilingual": "Chatterbox Multilingual voice",
    "keep_warm": "Keep-warm for a GPU voice",
    "krew": "krew: its tiles, board and tools",
    "turbokrew": "TurboKrew",
    "camera": "Camera tiles",
    "house_glass": "The house's glass keys",
    "house_only": "The house's janitor and engine journal",
    "house_register": "The house's Register an ID and New User keys",
}
ORDER = tuple(LABELS)
_CACHE: dict = {"at": 0.0, "key": None, "facts": None}
CACHE_S = 30.0


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_CAPABILITY", "1")).strip().lower() not in ("0", "off", "false", "no")


def _nvidia(home: Path, env: dict) -> bool | None:
    if str(env.get("CUDA_VISIBLE_DEVICES", "x")).strip() == "":
        return False                                   # hidden from every engine, so from this too
    try:
        rec = json.loads((home / "voice-tier.json").read_text(encoding="utf-8"))
        kind = str((((rec.get("facts") or {}).get("gpu")) or {}).get("kind") or "")
        # A recorded "none" is re-asked (the User 2026-10-27, an RTX PRO 6000 box whose Init record said none: "why does it
        # say I need an NVIDIA GPU, I have a good one"): the record is a fact only when it names a GPU; "none" may be the
        # probe's environment at Init (CUDA hidden, nvidia-smi off the PATH), so the live probe below decides.
        if kind and kind != "none":
            return kind == "cuda"
    except (OSError, ValueError, AttributeError):
        pass
    try:
        import crissa_voice_tiers
    except Exception:  # noqa: BLE001  no probe in this install: cannot tell (nothing is blanked for it)
        return None
    try:
        return str((crissa_voice_tiers.probe(env=env).get("gpu") or {}).get("kind") or "") == "cuda"
    except Exception:  # noqa: BLE001
        return None


def _krew(home: Path, env: dict) -> bool:
    try:
        import crissa_addon_link as L
        cfg, mans = L.expected(home)
        for x in L.links(cfg, home, mans, env=env, answer=lambda url: False):
            if x.get("id") == "krew":
                return bool(x.get("installed"))
        return bool(L.offers_for("krew", home)) or any(m.get("id") == "krew" for m in mans)
    except Exception:  # noqa: BLE001
        return False


def _camera(env: dict) -> bool:
    if str(env.get("CRISSA_NO_CAMERA") or "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    if str(env.get("CRISSA_CAMERA_URL") or env.get("RAIL_CAMERA_URL") or "").strip():
        return True
    if sys.platform == "darwin":
        return True                                     # a Mac's camera is not a /dev node; the oracle says if none
    return bool(glob.glob("/dev/video*"))


def facts(home: Path | None = None, env: dict | None = None, *, now: float | None = None) -> dict:
    """{"house", "nvidia", "krew", "camera"} - cached CACHE_S (the panels read it on every poll)."""
    e = dict(os.environ if env is None else env)
    home = Path(home or crissa_home.HOME)
    t = time.time() if now is None else now
    key = (str(home), e.get("CRISSA_NO_CAMERA"), e.get("CUDA_VISIBLE_DEVICES"), e.get("CRISSA_CAMERA_URL"))
    if _CACHE["facts"] is not None and _CACHE["key"] == key and t - _CACHE["at"] < CACHE_S:
        return dict(_CACHE["facts"])
    house = crissa_home.is_house()
    out = {"house": house, "nvidia": True if house else _nvidia(home, e), "krew": True if house else _krew(home, e),
           "camera": True if house else _camera(e)}
    _CACHE.update(at=t, key=key, facts=dict(out))
    return out


# TWO VOICES WITHOUT KREW (round 20, the User 2026-10-14, .101 round 19: "feels like a Lila and Crissa show only ...
# Maybe just ship two voices (with sound leveler)"; the announcer seemed awkward in that mix without krew). The announcer
# speaks for krew (a job done, an agent stuck), so an install without krew has none: no announcer voice is installed,
# Setup has no announcer lines or prints, and Lila says every cue. The house always has its announcer. Kill:
# CRISSA_TWO_VOICES=0 (the announcer as before, krew or not).
def two_voices(home: Path | None = None, env: dict | None = None) -> bool:
    e = dict(os.environ if env is None else env)
    if str(e.get("CRISSA_TWO_VOICES", "1")).strip().lower() in ("0", "off", "false", "no") or crissa_home.is_house():
        return False
    return not _krew(Path(home or crissa_home.HOME), e)


def decide(f: dict) -> dict:
    """{feature: {"ok", "why", "label"}} from the facts. Pure: the tests and both panels call it."""
    # nvidia None = this install cannot tell: the GPU features are not blanked for a fact nobody measured
    house, gpu, krew, cam = (bool(f.get("house")), f.get("nvidia") is not False, bool(f.get("krew")),
                             bool(f.get("camera")))

    def one(ok: bool, why: str) -> dict:
        return {"ok": bool(ok), "why": "" if ok else why}
    out = {
        "gpu_modes": one(gpu, NEEDS_GPU),
        "render": one(gpu, NEEDS_GPU),
        "turbo": one(gpu, NEEDS_GPU),
        "multilingual": one(gpu, NEEDS_GPU),
        "keep_warm": one(gpu, NEEDS_GPU),
        "krew": one(krew, NO_KREW),
        "turbokrew": one(krew and gpu, "needs krew and an NVIDIA GPU" if not (krew or gpu) else
                         (NO_KREW if not krew else NEEDS_GPU)),
        "camera": one(cam, NO_CAMERA),
        "house_glass": one(house, HOUSE_ONLY),
        "house_only": one(house, HOUSE_ONLY),
        "house_register": one(house, IN_CRISSA_MENU),
    }
    for k, v in out.items():
        v["label"] = LABELS[k]
    return out


def features(home: Path | None = None, env: dict | None = None) -> dict:
    """{"facts", "features", "unavailable": [{"id", "label", "why"}] in ORDER} - the one answer every surface reads."""
    e = os.environ if env is None else env
    f = facts(home, e)
    feats = decide(f)
    if not enabled(e):
        feats = {k: dict(v, ok=True, why="") for k, v in feats.items()}
    return {"facts": f, "features": feats,
            "unavailable": [{"id": k, "label": feats[k]["label"], "why": feats[k]["why"]} for k in ORDER
                            if not feats[k]["ok"]]}
