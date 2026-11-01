"""Lossless compact form of a cached cheeks map (the User 2026-10-13, "consider compressing the renders ... if no
tradeoffs").

A map entry is (roi, extra): roi = (x0, y0, mx, my, *rest) with float32 planes. Each array is byte-shuffled (the four
bytes of every float grouped plane by plane, so the smooth high bytes sit together) and zstd-compressed; everything
else rides in a pickle with a placeholder per array. Measured on 60 real entries from the disk tier: 2.31x smaller,
0.20 ms to decode (the plain read was 0.31 ms). unpack(pack(v)) gives back the same bytes, so a remap is identical.

No zstandard (a pack without it): pack() returns None and the callers keep their plain pickle.
"""
from __future__ import annotations

import pickle

import numpy as np

try:
    import zstandard as _zs
except ImportError:  # pragma: no cover - a pack without it keeps the plain form
    _zs = None

LEVEL = 3


class _A:
    """Placeholder for the i-th array in the header pickle."""
    __slots__ = ("i",)

    def __init__(self, i: int) -> None:
        self.i = i

    def __reduce__(self):
        return (_A, (self.i,))


def available() -> bool:
    return _zs is not None


def _strip(obj, arrs: list):
    if isinstance(obj, np.ndarray):
        arrs.append(obj)
        return _A(len(arrs) - 1)
    if isinstance(obj, tuple):
        return tuple(_strip(v, arrs) for v in obj)
    if isinstance(obj, list):
        return [_strip(v, arrs) for v in obj]
    if isinstance(obj, dict):
        return {k: _strip(v, arrs) for k, v in obj.items()}
    return obj


def _fill(obj, arrs: list):
    if isinstance(obj, _A):
        return arrs[obj.i]
    if isinstance(obj, tuple):
        return tuple(_fill(v, arrs) for v in obj)
    if isinstance(obj, list):
        return [_fill(v, arrs) for v in obj]
    if isinstance(obj, dict):
        return {k: _fill(v, arrs) for k, v in obj.items()}
    return obj


def pack(value) -> bytes | None:
    if _zs is None:
        return None
    arrs: list = []
    head = _strip(value, arrs)
    meta = []
    blobs = []
    c = _zs.ZstdCompressor(level=LEVEL)
    for a in arrs:
        a = np.ascontiguousarray(a)
        k = a.dtype.itemsize
        raw = np.frombuffer(a.tobytes(), np.uint8)
        if k > 1 and raw.size:
            raw = raw.reshape(-1, k).T.ravel()
        b = c.compress(raw.tobytes())
        meta.append((a.dtype.str, a.shape, len(b)))
        blobs.append(b)
    h = pickle.dumps((head, meta), protocol=5)
    return len(h).to_bytes(4, "little") + h + b"".join(blobs)


def unpack(blob: bytes):
    hl = int.from_bytes(blob[:4], "little")
    head, meta = pickle.loads(blob[4:4 + hl])
    off = 4 + hl
    d = _zs.ZstdDecompressor()
    arrs = []
    for dt, shape, n in meta:
        dtype = np.dtype(dt)
        raw = np.frombuffer(d.decompress(blob[off:off + n]), np.uint8)
        off += n
        k = dtype.itemsize
        if k > 1 and raw.size:
            raw = raw.reshape(k, -1).T
        arrs.append(np.ascontiguousarray(raw).view(dtype).reshape(shape))
    return _fill(head, arrs)
