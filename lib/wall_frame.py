"""Her film's frame on the glass (the User 2026-10-12: "On Close we see only a fraction of her face. Maybe just match
the render to capture the whole image and let User zoom in from there", and "if we have a black bar at left it is okay
as that is logs section anyway", and of that bar: "maybe just blend it to base color over there").

Two frames, one switch:
- `kiosk` (today's): the ZFRAME crop of the film, its top-left 67.5 % x 70 % (house_wall_ascii_render.ZFRAME), fills
  the glass. On a close-up that cut her chin and her right cheek and put her mouth under the bottom fade; on a 21:9
  glass it is also stretched (the crop is 1.77:1 and the glass 2.39:1, so her face drew 35 % wide).
- `whole`: the whole film, never stretched, fitted to the glass and anchored to its RIGHT edge. Spare width is on the
  LEFT (the logs side) and spare height (a 16:9 glass is a little narrower than the films' 1.84:1) is split top and
  bottom; the film's edge on a spare side BLENDS into the ground (the glyphs thin out to nothing over BLEND_X / BLEND_Y
  of the glass), so there is no bar and no seam. The User zooms in from there, ZOOM_MIN 1.0 to ZOOM_MAX 3.0 in
  ZOOM_STEP: the zoom is about her face (the clip's `face_point`, the median of its head track's nose: one fixed point
  per clip, never a per-frame crop that would wobble), and as it grows her face glides from where the whole film puts
  it to where the kiosk frame put it (KIOSK_SCREEN), reaching it at the kiosk's own scale. On a 16:9 glass that scale
  is zoom 1/0.675 = 1.48, where `whole` draws the kiosk crop itself (her desk loops' face point sits on ZFRAME's
  focus): today's picture is one zoom away. No zoom opens a gap on the right, top or bottom.

Who decides: HOUSE_WALL_FRAME=kiosk (or 0/off) forces today's frame, a kill; else the live switch file
<state home>/wall-frame (its first word, whole | kiosk; read at most once a second, no restart); else
HOUSE_WALL_FRAME=whole; else the default: `kiosk` on the house (crissa_home.is_house), `whole` in a pack.
The zoom is the glass prefs' `frame_zoom` (shared by both glasses through :8099's record, like density).

`layout()` is the one geometry; display/voiceonly-home.html carries the same function in JavaScript
(wallFrameLayout) and a test holds the two to the same numbers. Units: the glass is a grid of COLS x ROWS cells, each
cell_w x cell_h glass pixels (the page's cells are 0.6 x 0.98 em; the Tk wall's are W/COLS x H/ROWS, because it
stretches the glyph sheet onto the glass). Rounding is floor(x + 0.5) in both languages (Python's round() is the
banker's).
"""
from __future__ import annotations

import json
import math
import os
import statistics
import threading
import time
from functools import lru_cache
from pathlib import Path

import crissa_home

MODES = ("whole", "kiosk")
SWITCH_FILE = crissa_home.HOME / "wall-frame"
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 1.0, 3.0, 0.25
# today's frame, as film fractions: the ZFRAME crop, ZFOCUS inside it, and where that focus lands on the glass
KIOSK_CROP_W, KIOSK_CROP_H = 0.675, 0.700
KIOSK_SCREEN = (0.74, 0.46)
KIOSK_FACE = (round(0.74 * KIOSK_CROP_W, 4), round(0.46 * KIOSK_CROP_H, 4))   # (0.4995, 0.322): no head track
# the bokeh's sharp ellipse and the exposure's metering box, as film fractions: the kiosk's own (ZFOCUS r 0.30 x
# 1.15 / 1.85 of the crop; the box +-0.16 / +-0.22 of the crop), so the whole frame meters on the same part of her
FOCUS_R = (0.30 * 1.15 * KIOSK_CROP_W, 0.30 * 1.85 * KIOSK_CROP_H)
METER_HALF = (0.16 * KIOSK_CROP_W, 0.22 * KIOSK_CROP_H)
BLEND_X = 0.06          # of the glass width: the film's left edge thins into the ground over this
BLEND_Y = 0.04          # of the glass height: the same for a spare top or bottom


def rnd(x: float) -> int:
    """floor(x + 0.5): the page's Math.floor(x + 0.5), never the banker's round."""
    return int(math.floor(x + 0.5))


@lru_cache(maxsize=1)
def _house() -> bool:
    return crissa_home.is_house()


_SW = {"at": 0.0, "mtime": None, "word": None}
_SW_LOCK = threading.Lock()


def _switch_word() -> str | None:
    """The switch file's first word, read at most once a second (its mtime decides a re-read)."""
    now = time.monotonic()
    with _SW_LOCK:
        if now - _SW["at"] < 1.0:
            return _SW["word"]
        _SW["at"] = now
        try:
            mt = SWITCH_FILE.stat().st_mtime_ns
        except OSError:
            _SW.update(mtime=None, word=None)
            return None
        if mt != _SW["mtime"]:
            try:
                words = SWITCH_FILE.read_text(encoding="utf-8").split()
            except OSError:
                words = []
            _SW.update(mtime=mt, word=(words[0].lower() if words else None))
        return _SW["word"]


def mode(env=None) -> str:
    """whole | kiosk (see the module doc)."""
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_WALL_FRAME", "")).strip().lower()
    if raw in ("kiosk", "0", "off", "false", "no"):
        return "kiosk"
    word = _switch_word()
    if word in MODES:
        return word
    if raw in MODES:
        return raw
    return "kiosk" if _house() else "whole"


def clamp_zoom(z) -> float:
    try:
        z = float(z)
    except (TypeError, ValueError):
        return ZOOM_MIN
    if z != z:
        return ZOOM_MIN
    return rnd(max(ZOOM_MIN, min(ZOOM_MAX, z)) / ZOOM_STEP) * ZOOM_STEP


# ---- her face, one point per clip ------------------------------------------------------------------------------------
def face_point(frames, src_wh=None) -> tuple[float, float] | None:
    """The median nose over a head track's frames, as film fractions (x, y), or None (fewer than 3 faces)."""
    xs, ys = [], []
    for f in frames or ():
        if not isinstance(f, dict):
            continue
        n, wh = f.get("nose"), f.get("src_wh") or src_wh
        try:
            x, y, w, h = float(n[0]), float(n[1]), float(wh[0]), float(wh[1])
        except (TypeError, ValueError, IndexError):
            continue
        if w > 0 and h > 0:
            xs.append(x / w)
            ys.append(y / h)
    if len(xs) < 3:
        return None
    return (round(statistics.median(xs), 4), round(statistics.median(ys), 4))


_FP: dict = {}


def face_point_file(path) -> tuple[float, float] | None:
    """face_point of a <stem>.head.json, remembered against the file's mtime."""
    p = Path(path)
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return None
    got = _FP.get(str(p))
    if got is not None and got[0] == mt:
        return got[1]
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        fp = face_point(d.get("frames") or [], d.get("src_wh"))
    except (OSError, ValueError, AttributeError):
        fp = None
    _FP[str(p)] = (mt, fp)
    return fp


def face_of(fp) -> tuple[float, float]:
    """A usable face point: the one given if it is two numbers inside the film, else KIOSK_FACE."""
    try:
        x, y = float(fp[0]), float(fp[1])
    except (TypeError, ValueError, IndexError):
        return KIOSK_FACE
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return KIOSK_FACE
    return (x, y)


# ---- the geometry ----------------------------------------------------------------------------------------------------
def layout(iw: float, ih: float, cols: int, rows: int, zoom: float = 1.0, face=None,
           cell_w: float = 0.6, cell_h: float = 0.98) -> dict:
    """Where the film goes on a grid of cols x rows cells of cell_w x cell_h glass units.

    crop  (sx0, sy0, sx1, sy1)  the film pixels the glass shows
    place (dx, dy, dw, dh)      the cells they fill (ints)
    face  (fc, fr)              her face point, in cells (floats)
    focus (cx, cy, rx, ry)      the bokeh's sharp ellipse, in the crop's own pixels
    meter (c0, c1, r0, r1)      the exposure's metering box, in cells (clipped to `place`)
    blend (left, top, bottom)   the spare sides the film's edge thins on
    """
    iw, ih = float(iw), float(ih)
    gw, gh = cols * cell_w, rows * cell_h
    z = clamp_zoom(zoom)
    fx, fy = face_of(face)
    s0 = min(gw / iw, gh / ih)
    s = s0 * z
    fw, fh = iw * s, ih * s
    ffx, ffy = fx * iw, fy * ih
    # zoom 1: right-anchored, centred top to bottom; her face glides to the kiosk's spot by the kiosk's own scale
    x1, y1 = gw - iw * s0, (gh - ih * s0) / 2.0
    p1x, p1y = x1 + ffx * s0, y1 + ffy * s0
    zk = (gw / (KIOSK_CROP_W * iw)) / s0
    t = 1.0 if zk <= 1.0 else max(0.0, min(1.0, (z - 1.0) / (zk - 1.0)))
    px = p1x + (KIOSK_SCREEN[0] * gw - p1x) * t
    py = p1y + (KIOSK_SCREEN[1] * gh - p1y) * t
    x, y = px - ffx * s, py - ffy * s
    if x + fw < gw:                       # never a gap on the right
        x = gw - fw
    if fh >= gh:                          # never a gap at the top or the bottom once she is taller than the glass
        y = min(0.0, max(y, gh - fh))
    else:
        y = (gh - fh) / 2.0
    sx0, sx1 = max(0.0, -x / s), min(iw, (gw - x) / s)
    sy0, sy1 = max(0.0, -y / s), min(ih, (gh - y) / s)
    c0 = max(0, min(cols, rnd((x + sx0 * s) / cell_w)))
    c1 = max(0, min(cols, rnd((x + sx1 * s) / cell_w)))
    r0 = max(0, min(rows, rnd((y + sy0 * s) / cell_h)))
    r1 = max(0, min(rows, rnd((y + sy1 * s) / cell_h)))
    dw, dh = max(1, c1 - c0), max(1, r1 - r0)
    fc, fr = (x + ffx * s) / cell_w, (y + ffy * s) / cell_h
    mc0 = int(math.floor((x + (ffx - METER_HALF[0] * iw) * s) / cell_w))
    mc1 = int(math.ceil((x + (ffx + METER_HALF[0] * iw) * s) / cell_w))
    mr0 = int(math.floor((y + (ffy - METER_HALF[1] * ih) * s) / cell_h))
    mr1 = int(math.ceil((y + (ffy + METER_HALF[1] * ih) * s) / cell_h))
    mc0, mc1 = max(c0, mc0), min(c0 + dw, mc1)
    mr0, mr1 = max(r0, mr0), min(r0 + dh, mr1)
    if mc1 <= mc0 or mr1 <= mr0:
        mc0, mc1, mr0, mr1 = c0, c0 + dw, r0, r0 + dh
    return {
        "crop": (sx0, sy0, sx1, sy1),
        "place": (c0, r0, dw, dh),
        "face": (fc, fr),
        "focus": (ffx - sx0, ffy - sy0, FOCUS_R[0] * iw, FOCUS_R[1] * ih),
        "meter": (mc0, mc1, mr0, mr1),
        "blend": (c0 > 0, r0 > 0, r0 + dh < rows),
        "zoom": z,
        "scale": s,
    }


@lru_cache(maxsize=8)
def blend_weights(cols: int, rows: int, place: tuple, blend: tuple):
    """(rows x cols) float32 in 0..1: 1 inside the film, a smoothstep down to 0 across its edge on a spare side."""
    import numpy as np
    c0, r0, dw, dh = place
    left, top, bottom = blend
    wx = np.ones(cols, dtype=np.float32)
    wy = np.ones(rows, dtype=np.float32)
    if left:
        band = max(1, rnd(BLEND_X * cols))
        k = np.clip((np.arange(cols, dtype=np.float32) - c0 + 0.5) / band, 0.0, 1.0)
        wx = k * k * (3.0 - 2.0 * k)
    by = max(1, rnd(BLEND_Y * rows))
    ry = np.arange(rows, dtype=np.float32)
    if top:
        k = np.clip((ry - r0 + 0.5) / by, 0.0, 1.0)
        wy = wy * (k * k * (3.0 - 2.0 * k))
    if bottom:
        k = np.clip((r0 + dh - ry - 0.5) / by, 0.0, 1.0)
        wy = wy * (k * k * (3.0 - 2.0 * k))
    return wy[:, None] * wx[None, :]


# ---- what the wall asks for --------------------------------------------------------------------------------------------
def frame_for(own: bool, zoom, face, *, hole_zoom=None, shift=None, env=None):
    """The frame a picture is drawn with: None = today's kiosk path (also under a page's slide), else
    ("whole", zoom, fx, fy). Hashable: it is part of the ASCII cache's key. The AR hole no longer sends the whole frame
    back to the kiosk (round 14, 2026-10-12, .101: "I do not have whole frame zoom on wallpaper" - with AR on, the
    -/+ moved the hole and the wallpaper stayed on the kiosk path): under `whole` the wallpaper is always the whole
    film at frame_zoom, and the AR hole on the page scans that plane 1:1. `hole_zoom` is accepted and ignored."""
    if not own or shift is not None:
        return None
    if mode(env) != "whole":
        return None
    fx, fy = face_of(face)
    return ("whole", clamp_zoom(zoom), fx, fy)
