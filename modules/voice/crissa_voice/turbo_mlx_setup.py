"""Chatterbox-Turbo on Apple Silicon via MLX (2026-10-06, the User): Init detection and tier logic only.

Models considered: mlx-community/chatterbox-turbo-4bit (~415 MB) and chatterbox-turbo-8bit (~706 MB), converted
from ResembleAI/chatterbox-turbo (MIT) with mlx-audio (conversion cards Apache-2.0). NOT YET VERIFIED ON APPLE
SILICON HARDWARE: this module detects a capable Mac (Apple Silicon, macOS 14+, 16 GB unified RAM) and refuses to
install until a measured self-test on real hardware lands. An 8 GB M1/M2 stays on Pocket.

WATERMARK: mlx-audio's Chatterbox path does not apply Resemble's Perth watermark (unlike the CUDA Turbo service).
Any future install must stamp every clip with resemble-perth when turbo_mlx.settings.watermark is on (the default),
or refuse to start unmarked — same rule as CUDA Turbo.

CoreML ports (ebrinz/chatterbox-turbo-coreml, iliasaz) and GGUF (cstr/chatterbox-turbo-GGUF) were noted and are
not adopted.

    python3 modules/voice/scripts/crissa-turbo-mlx.py --install   # explains why it refuses today
"""
from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

CREDIT = ("Voice: Chatterbox-Turbo on Apple Silicon via mlx-community (4bit/8bit) and mlx-audio; base model "
          "ResembleAI/chatterbox-turbo (MIT); conversion Apache-2.0. NOT YET VERIFIED ON APPLE SILICON HARDWARE.")
QUANTS = ("4bit", "8bit")
DEFAULT_QUANT = "4bit"
MODELS = {
    "4bit": "mlx-community/chatterbox-turbo-4bit",
    "8bit": "mlx-community/chatterbox-turbo-8bit",
}
MIN_MACOS = (14, 0)
MIN_RAM_GB = 16


def _home() -> Path:
    env = os.environ.get("CRISSA_HOME")
    return Path(env).expanduser() if env else Path.home() / ".crissa"


ROOT = _home() / "turbo-mlx"
DONE = ROOT / "installed.json"
LOG = ROOT / "server.log"


def installed() -> bool:
    """True only after a verified install writes installed.json with ok self-test. Never true today."""
    try:
        rec = json.loads(DONE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(rec.get("ok") and rec.get("verified_on_hardware"))


def record() -> dict:
    try:
        return json.loads(DONE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def self_test_failed() -> str:
    """Why an install that claimed to be here failed; '' when nothing is installed."""
    if not DONE.is_file():
        return ""
    rec = record()
    if not rec.get("verified_on_hardware"):
        return "NOT YET VERIFIED ON APPLE SILICON HARDWARE"
    st = rec.get("self_test") or {}
    if st.get("ok") is False:
        return str(st.get("why") or "self-test failed")
    return ""


def unsupported(version=None, plat: str | None = None, machine: str | None = None,
                mac_ver=None, ram_gb: float | None = None) -> str:
    """'' when this Mac could carry Turbo MLX once verified; else exactly why not."""
    v = tuple(version or sys.version_info[:2])
    p = plat or sys.platform
    m = (machine or platform.machine()).lower()
    if v != (3, 12):
        return ("Turbo MLX's install is locked for Python 3.12 (this is %d.%d)."
                % (v[0], v[1]))
    if p != "darwin":
        return ("Turbo MLX is Apple Silicon only (mlx-community / mlx-audio). On Linux with an NVIDIA GPU, Turbo's "
                "CUDA path speaks; elsewhere Pocket speaks.")
    if m not in ("arm64", "aarch64"):
        return "Turbo MLX needs an Apple Silicon Mac (this one is %s). Pocket or say speaks here." % m
    mv = mac_ver
    if mv is None:
        try:
            mv = tuple(int(x) for x in platform.mac_ver()[0].split(".")[:2] if x.isdigit())
        except (TypeError, ValueError):
            mv = ()
    if mv and mv < MIN_MACOS:
        return "Turbo MLX needs macOS %d.%d or later (this Mac runs %s)." % (MIN_MACOS + (platform.mac_ver()[0],))
    if ram_gb is not None and float(ram_gb) < MIN_RAM_GB:
        return ("Turbo MLX needs %d GB of unified RAM (this Mac has %.0f GB). An 8 GB M1/M2 stays on Pocket; "
                "4-bit peak RAM is not yet verified on Apple Silicon." % (MIN_RAM_GB, float(ram_gb)))
    return ""


def install(log=print, quant: str = DEFAULT_QUANT) -> dict:
    """Refuses today: the tier is wired for Init, but the install is not opened until hardware verification."""
    why = unsupported()
    if why:
        raise RuntimeError(why)
    q = quant if quant in QUANTS else DEFAULT_QUANT
    msg = (
        "Turbo on Apple Silicon (MLX) is wired in Init and the tier table but is NOT YET VERIFIED ON APPLE SILICON "
        "HARDWARE. Nothing was downloaded. Model when it opens: %s (quant=%s). Until a measured self-test on a real "
        "Mac lands, Pocket speaks. CoreML and GGUF ports were noted and are not adopted."
        % (MODELS[q], q)
    )
    log("turbo-mlx: " + msg)
    raise RuntimeError(msg)
