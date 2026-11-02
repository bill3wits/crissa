"""The mouth stamp's arithmetic without its spare copies, and what one film frame shares between its two semiframes
(the User 2026-10-12, "go ahead with the stamp speedup").

Measured on her close-up (laptop-closeup-eyes-down, the wake cut), 189 real paints captured from a scratch bench:
composite_stamp took 15.9 ms p50 where a semiframe is 20.8 ms, so with the warp in front of it most close-up rows
missed their slot. The tile's live box there is ~78,000 px (a quarter of the frame), and the time was the hole's
TELEA inpaint (4.3 ms), brighten + b* tone (6.4 ms, mostly float64 copies of arrays it already had) and the blend
(~4 ms of temporaries). The conductor paints two mouths on every film frame, and on the second one the hole is the
same pixels under the same mask, so the inpaint and the arch band are answered from the first.

Every helper returns exactly what the code it stands in for returns (tests pin each byte for byte, and the captured
paints compare whole frames), so a paint is bit-identical; only its time changes. A cache hit is checked against the
stored arrays themselves, never a hash alone. On in a pack, off on the house until its restart window;
HOUSE_MOUTH_STAMP_FAST=1|0 forces it.
"""
from __future__ import annotations

import collections
import hashlib
import os
import threading

import cv2
import numpy as np


def _on() -> bool:
    raw = os.environ.get("HOUSE_MOUTH_STAMP_FAST", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        import crissa_home
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001 - no state home to ask: today's stamp
        return False


ON = _on()
_KEEP = 4                       # a film frame's two semiframes, plus a crossfade's outgoing frame, with room
_lock = threading.Lock()
_inpaints: collections.OrderedDict = collections.OrderedDict()
_arches: collections.OrderedDict = collections.OrderedDict()
stats = {"inpaint_hit": 0, "inpaint_miss": 0, "arch_hit": 0, "arch_miss": 0}


def _digest(*arrs: np.ndarray) -> bytes:
    h = hashlib.blake2b(digest_size=16)
    for a in arrs:
        a = np.ascontiguousarray(a)
        h.update(repr((a.shape, a.dtype.str)).encode())
        h.update(memoryview(a).cast("B"))
    return h.digest()


def _remember(store: collections.OrderedDict, key, value) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > _KEEP:
        store.popitem(last=False)


def inpaint(patch: np.ndarray, mask: np.ndarray, radius: float = 3) -> np.ndarray:
    """`cv2.inpaint(patch, mask, radius, cv2.INPAINT_TELEA)`, answered from memory when the same pixels and the same
    mask were inpainted a moment ago (the film frame's other semiframe). The caller owns the returned array."""
    key = _digest(patch, mask) + repr(float(radius)).encode()
    with _lock:
        hit = _inpaints.get(key)
    if hit is not None and np.array_equal(hit[0], patch) and np.array_equal(hit[1], mask):
        with _lock:
            _inpaints.move_to_end(key)
            stats["inpaint_hit"] += 1
        return hit[2].copy()
    out = cv2.inpaint(patch, mask, radius, cv2.INPAINT_TELEA)
    with _lock:
        _remember(_inpaints, key, (patch.copy(), mask.copy(), out.copy()))
        stats["inpaint_miss"] += 1
    return out


def _arch_key(h: int, w: int, arch: dict) -> tuple:
    parts = [int(h), int(w)]
    for k in sorted(arch):
        v = arch[k]
        if isinstance(v, (int, float, str, bool)) or v is None:
            parts.append((k, type(v).__name__, repr(v)))
        else:
            a = np.asarray(v)
            parts.append((k, a.dtype.str, a.shape, a.tobytes()))
    return tuple(parts)


def arch_mask(h: int, w: int, arch: dict, make) -> np.ndarray:
    """`make(h, w, arch)` (head_frame.arch_mask), remembered by the arch's exact values. The caller owns the array."""
    try:
        key = _arch_key(h, w, arch)
    except Exception:  # noqa: BLE001 - an arch we cannot key is drawn as today
        return make(h, w, arch)
    with _lock:
        hit = _arches.get(key)
        if hit is not None:
            _arches.move_to_end(key)
            stats["arch_hit"] += 1
    if hit is not None:
        return hit.copy()
    out = make(h, w, arch)
    with _lock:
        _remember(_arches, key, out.copy())
        stats["arch_miss"] += 1
    return out


def brighten_tone_blend(warped_u8: np.ndarray, dst_u8: np.ndarray, ww: np.ndarray, aa: np.ndarray,
                        tone: bool) -> np.ndarray | None:
    """composite_stamp's ROI tail in one pass, without its copies:

        patch = warped_u8.astype(f32); dstp = dst_u8.astype(f32)
        patch = brighten_and_tone(patch, dstp, ww, tone=tone)
        return (dstp * (1 - aa) + patch * aa).astype(np.uint8)

    Same operations on the same values in the same order: a float32 array times a float64 one is the float32 value
    widened (exact), a sum over a C-ordered array of one shape reduces in one order, and in-place ufuncs compute what
    their temporaries did. None = not a case this covers (the caller runs today's code)."""
    if aa.dtype != np.float32 or warped_u8.dtype != np.uint8 or dst_u8.dtype != np.uint8:
        return None
    if warped_u8.ndim != 3 or warped_u8.shape[2] != 3 or aa.ndim != 3 or aa.shape[2] != 1:
        return None
    patch = warped_u8.astype(np.float32)
    dstp = dst_u8.astype(np.float32)
    # brighten_and_tone
    w64 = np.clip(np.asarray(ww, dtype=np.float64), 0.0, 1.0)
    tot = float(w64.sum())
    if tot > 8.0:
        a3 = max(tot * 3, 1)
        buf = np.empty(patch.shape, np.float64)
        w3 = w64[..., None]
        np.multiply(dstp, w3, out=buf)
        dm = float(buf.sum() / a3) or 1.0
        np.multiply(patch, w3, out=buf)
        pm = float(buf.sum() / a3) or 1.0
        del buf
        patch = np.clip(patch * max(0.5, min(2.0, dm / pm)), 0, 255)
        if tone:
            # tone_b(patch, dstp, ww): patch is already inside 0..255 and dstp is dst_u8 widened, so their uint8
            # copies are patch truncated and dst_u8 itself
            try:
                pl = cv2.cvtColor(patch.astype(np.uint8), cv2.COLOR_BGR2LAB)
                dl = cv2.cvtColor(dst_u8 if dst_u8.flags.c_contiguous else np.ascontiguousarray(dst_u8),
                                  cv2.COLOR_BGR2LAB)
                shift = float((dl[..., 2].astype(np.float64) * w64).sum() / tot) - float(
                    (pl[..., 2].astype(np.float64) * w64).sum() / tot)
                pl[..., 2] = np.clip(pl[..., 2].astype(np.float32) + shift, 0, 255).astype(np.uint8)
                patch = cv2.cvtColor(pl, cv2.COLOR_LAB2BGR).astype(np.float32)
            except cv2.error:
                pass
    # the blend: dstp * (1 - aa) + patch * aa
    np.multiply(dstp, 1 - aa, out=dstp)
    np.multiply(patch, aa, out=patch)
    np.add(dstp, patch, out=dstp)
    return dstp.astype(np.uint8)
