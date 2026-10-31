"""Latest ASCII wallpaper frame, for Hybrid-AR holes in other windows.

The wallpaper maps the conductor JPEG onto the monitor. A window at (x,y,w,h)
is a 1× hole in that plane — same trick as voiceonly-home.html crissa_hole.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

from PIL import Image

_HDR = struct.Struct("<III")


def path() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "house-ascii.bg"


def write_bg(im: Image.Image) -> None:
    rgb = im.convert("RGB")
    raw = rgb.tobytes()
    p = path()
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(_HDR.pack(rgb.width, rgb.height, len(raw)) + raw)
    tmp.replace(p)


def read_bg() -> Image.Image | None:
    p = path()
    try:
        b = p.read_bytes()
    except OSError:
        return None
    if len(b) < _HDR.size:
        return None
    w, h, n = _HDR.unpack_from(b)
    blob = b[_HDR.size : _HDR.size + n]
    if w < 1 or h < 1 or len(blob) < n or n != w * h * 3:
        return None
    return Image.frombytes("RGB", (w, h), blob)


def crop_screen(x: int, y: int, w: int, h: int) -> Image.Image | None:
    """Slice of the wallpaper that sits under this screen rectangle."""
    im = read_bg()
    if im is None:
        return None
    W, H = im.size
    x1 = max(0, min(W - 1, int(x)))
    y1 = max(0, min(H - 1, int(y)))
    x2 = max(x1 + 1, min(W, int(x) + max(1, int(w))))
    y2 = max(y1 + 1, min(H, int(y) + max(1, int(h))))
    return im.crop((x1, y1, x2, y2))
