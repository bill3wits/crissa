"""TV static / channel-switch cut onto FT, and the fade back to the room.

Hey Crissa is not a lean-in slide. 0.6 s analog TV channel change
(Sapphire TVChannelChange / Channel Switch): snow, hold tear, RGB split,
then the already-leaned-in webcam loop. Pause / Secure / first tool call
fades back to heads-down work.
"""
from __future__ import annotations

import math

import numpy as np

CUT_S = 0.6
FADE_S = 0.5


def _u8(img: np.ndarray) -> np.ndarray:
    return np.clip(img, 0, 255).astype(np.uint8)


def switch_frame(src: np.ndarray, t: float, dur: float = CUT_S) -> np.ndarray:
    """Analog camera-switch squiggles on ``src``. ``t`` in [0, dur]."""
    dur = max(float(dur), 1e-6)
    p = float(np.clip(t / dur, 0.0, 1.0))
    env = math.sin(p * math.pi)  # 0 at the cut-in and the cut-out
    img = np.asarray(src, dtype=np.float32)
    if img.ndim == 2:
        img = np.repeat(img[..., None], 3, axis=2)
    h, w = img.shape[:2]
    # Chromatic split — the RGB guns lose lock mid-switch.
    s = int(round(env * 14.0))
    if s:
        out = np.empty_like(img)
        out[..., 0] = np.roll(img[..., 0], s, axis=1)
        out[..., 1] = img[..., 1]
        out[..., 2] = np.roll(img[..., 2], -s, axis=1)
        img = out
    # Horizontal tear / rolling bar.
    tear = int(h * (0.12 + 0.55 * p)) % max(h, 1)
    band = max(3, int(6 + env * 28))
    sign = 1 if int(t * 50) % 2 == 0 else -1
    img[tear:tear + band] = np.roll(
        img[tear:tear + band], int(env * 48) * sign, axis=1)
    # Sync-loss: a few rows jump independently.
    rng = np.random.RandomState(int(t * 1000.0) & 0xFFFF)
    if env > 0.05:
        n_rows = max(1, int(env * 22))
        rows = rng.randint(0, max(h, 1), size=n_rows)
        for r in rows:
            img[r] = np.roll(img[r], int(rng.randint(-40, 41) * env), axis=0)
        snow = rng.randn(h, w, 1).astype(np.float32)
        img = img * (1.0 - 0.55 * env) + (128.0 + snow * 90.0) * (0.55 * env)
        img[::2] = img[::2] * (1.0 - 0.25 * env)
    # Desaturate through the lock.
    gray = img.mean(axis=2, keepdims=True)
    img = img * (1.0 - 0.35 * env) + gray * (0.35 * env)
    # Brief black flash at the cut-out so the webcam frame is a new camera.
    if p > 0.86:
        img = img * (1.0 - (p - 0.86) / 0.14)
    return _u8(img)


def fade_frame(src: np.ndarray, dst: np.ndarray, t: float,
               dur: float = FADE_S) -> np.ndarray:
    """Dissolve ``src`` into ``dst``. ``t`` in [0, dur]."""
    dur = max(float(dur), 1e-6)
    p = float(np.clip(t / dur, 0.0, 1.0))
    e = p * p * (3.0 - 2.0 * p)
    a = np.asarray(src, dtype=np.float32)
    b = np.asarray(dst, dtype=np.float32)
    if a.shape != b.shape:
        return _u8(b if e >= 0.5 else a)
    return _u8(a * (1.0 - e) + b * e)
