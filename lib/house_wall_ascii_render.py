#!/usr/bin/env python3
"""house_wall_render — draw the wall the way display/voiceonly-home.html draws it,
in Pillow, with no browser.

Inputs are the same three things the page reads:
  frame  : bytes of :8100/frame.jpg   (Crissa, 736x400, mouth already pasted)
  camera : bytes of :8099/frame       (door camera) or None
  house  : dict from :8099/v1/house
  presence : dict from :8191/v1/presence (Secure / AUTH)

Output: a PIL.Image (RGB) at the wall size.

Numbers below are the page's own CSS/JS constants (2026-09-05 copy), so the
grid, the crops and the type sizes land where the kiosk put them:
  --u = width/88 ; #ascii .44em ; ZFRAME 0.675x0.700 ; ZFOCUS (0.74,0.46,r.30)
  RAMP " ·:-=+*#%@" ; FLOOR .42 ; gamma .80 ; ZFADE .60 ; FT_SRC 240,0,267,400
  --pip-w 16vw ; --ft-w 9.5vw ; --pip-edge 1.6vw ; --page-left .378em
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import time
from functools import lru_cache
from pathlib import Path

import crissa_home  # the state directory, resolved once (2026-09-26)
import wall_frame  # her film's frame, kiosk | whole (2026-10-12)

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import house_wall_prefs as wall_prefs

try:
    import numpy as np
except Exception:  # numpy comes with the wall's own env. Without it the ASCII cannot be
    np = None      # drawn; house_wall_ascii.main() refuses to start (loud, not black).

# ---- palette (:root) --------------------------------------------------------
GROUND = (0x10, 0x13, 0x1A)
GROUND2 = (0x15, 0x19, 0x22)
HAIR = (0x24, 0x2A, 0x37)
INK = (0xE9, 0xE5, 0xDB)
INK2 = (0x8B, 0x93, 0xA6)
INK3 = (0x4A, 0x51, 0x63)
EMBER = (0xD9, 0xA0, 0x5B)
EMBER2 = (0xF0, 0xC4, 0x89)
EMBER_DEEP = (0x7A, 0x52, 0x23)
FT_BORDER = (0x4E, 0x3E, 0x2B)     # the thin amber hairline on both pips
SAGE = (0x6F, 0x9B, 0x7E)
RUST = (0xB5, 0x60, 0x54)

RAMP = " ·:-=+*#%@"
CLIP_W, CLIP_H = 736, 400
ZFRAME = dict(cx=0.0, cy=0.0, cw=0.675, ch=0.700)
ZFOCUS = dict(x=0.74, y=0.46, r=0.30)
ZFADE = 0.60
FT_SRC = dict(x=240, y=0, w=267, h=400)

MONO_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
]
MONO_LIGHT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]
SERIF_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
]
SERIF_ITALIC_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
]


def _first_font(paths):
    for p in paths:
        try:
            ImageFont.truetype(p, 12)
            return p
        except Exception:
            continue
    return None


MONO_PATH = _first_font(MONO_CANDIDATES)
MONO_LIGHT_PATH = _first_font(MONO_LIGHT_CANDIDATES) or MONO_PATH
SERIF_PATH = _first_font(SERIF_CANDIDATES) or MONO_PATH
SERIF_ITALIC_PATH = _first_font(SERIF_ITALIC_CANDIDATES) or SERIF_PATH


@lru_cache(maxsize=64)
def font(kind: str, px: float) -> ImageFont.FreeTypeFont:
    path = {"mono": MONO_PATH, "light": MONO_LIGHT_PATH, "serif": SERIF_PATH, "serif-italic": SERIF_ITALIC_PATH}[kind]
    if path is None:
        return ImageFont.load_default()
    return ImageFont.truetype(path, max(6, int(round(px))))


# ---- geometry (the page's CSS, in pixels) ------------------------------------
class Geom:
    def __init__(self, W: int, H: int, density: float = 1.0):
        self.W, self.H = W, H
        # ONE ceiling, the pref's (2026-09-15). A second literal here quietly
        # capped the dial: the wheel moved the number and the grid did not.
        self.density = max(wall_prefs.DENSITY_MIN,
                           min(wall_prefs.DENSITY_MAX, float(density)))
        # THE TYPE FITS THE HEIGHT TOO (2026-10-10, .101's 3440x1440 ultrawide: the vitals rows ran into the oracle
        # dial and the roll across it): the body font-size is the page's W/88 up to a 16:9 screen's (H * 16/9 / 88);
        # on a wider screen it is that, and the camera, FT and oracle tiles are sized to that 16:9 width too, so a 21:9
        # Wall stacks like a 16:9 one and the film fills the rest. 16:9 and taller: unchanged. The film's character grid
        # keeps W/88 (u_film), so the film looks and costs what it did. HOUSE_WALL_U_FIT=0: all of it W-based again.
        self.u_film = W / 88.0
        # a pack's fit (2026-10-10); the house's glass keeps its own layout on any screen (the Crissa merge, 2026-10-11)
        fit = os.environ.get("HOUSE_WALL_U_FIT", "0" if crissa_home.is_house() else "1").strip() != "0"
        self.u = min(self.u_film, H * 16.0 / 9.0 / 88.0) if fit else self.u_film      # body font-size
        self.page_left = 0.378 * self.u
        wf = min(float(W), H * 16.0 / 9.0) if fit else float(W)   # the tiles too: a 16:9 screen's width at most
        self.pip_w = 0.16 * wf
        self.pip_h = self.pip_w * 9 / 16
        self.ft_w = 0.095 * wf
        self.ft_h = self.ft_w * 6 / 4
        self.pip_edge = 0.016 * wf
        self.rail_w = 0.575 * W
        # ascii grid — density>1 is finer (more cells). 1.0 matches the page.
        fs = self.u_film * 0.44 / self.density
        self.ascii_fs = fs
        self.cell_w = fs * 0.6
        self.cell_h = fs * 0.98
        self.COLS = max(24, math.ceil(W / self.cell_w))
        self.ROWS = max(12, math.ceil(H / self.cell_h))

    def oracle_box(self) -> tuple[int, float]:
        """Size of the oracle and the baseline it sits on: the page's
        .clockwrap — width --pip-w, bottom = --pip-edge + the camera tile +
        .45em. The .90 / 1.35u shrink of 2026-09-07 morning was a workaround
        for a bloom drawn twice as wide as the page's (see _ember_bloom);
        with the page's shadow the dial sits where the page puts it."""
        return int(self.pip_w), self.H - (self.pip_edge + self.pip_h + 0.45 * self.u)

    def oracle_center(self) -> tuple[float, float]:
        """Centre of the oracle, same box render_hud uses."""
        dial_size, dial_bottom = self.oracle_box()
        return self.page_left + dial_size / 2, dial_bottom - dial_size / 2


# ---- ascii layer -------------------------------------------------------------
@lru_cache(maxsize=4)
def _glyph_atlas(fs: float, cw: int, ch: int):
    """One tile per RAMP character. Glyphs are drawn 2× and LANCZOS-down so the
    stems stay sharp like the kiosk <pre>; CSS text-shadow sits *behind* the
    letter (a tight halo), it does not blur the letter itself."""
    from PIL import ImageChops
    scale = 2
    f = font("mono", fs * scale)
    tw, th = cw * scale, ch * scale
    tiles = []
    for chdr in RAMP:
        t = Image.new("L", (tw, th), 0)
        d = ImageDraw.Draw(t)
        try:
            bx0, by0, bx1, by1 = f.getbbox(chdr)
            gw = bx1 - bx0
        except Exception:
            bx0, gw = 0, tw
        # GLYPH WEIGHT. Chromium's text rasteriser (Skia) boosts contrast on a
        # dark ground; FreeType through PIL, then LANCZOS down from 2×, thins
        # every stem to ~half-covered pixels. A 1 px stroke at 2× (½ px at 1×)
        # puts the weight back: measured against the 10:33 kiosk screenshot
        # the figure's mean lifted from 27 to 36 (kiosk 37) and p90 from 58
        # to ~85 (kiosk 80). The 14 px text-shadow is applied at page scale
        # in _text_shadow, not here.
        d.text(((tw - gw) / 2 - bx0, -1), chdr, font=f, fill=255, stroke_width=1, stroke_fill=255)
        tiles.append(t.resize((cw, ch), Image.Resampling.LANCZOS) if scale != 1 else t)
    if np is not None:
        return np.stack([np.asarray(t, dtype=np.uint8) for t in tiles])
    return tiles


def crop_box(iw: int, ih: int, hole_zoom: float | None = None,
             shift: tuple[float, float] | None = None) -> tuple[float, float, float, float]:
    """Source crop (sx, sy, cw, ch) in pixels.

    ``shift`` None or (0, 0) keeps ZFRAME's origin, zoom included, so the
    crop is today's. A pair replaces that origin (film fraction). Zoom still
    widens about the crop's own centre.
    """
    z = float(hole_zoom) if hole_zoom and hole_zoom > 0 else 1.0
    sx, sy = ZFRAME["cx"] * iw, ZFRAME["cy"] * ih
    cw, ch = ZFRAME["cw"] * iw, ZFRAME["ch"] * ih
    if shift is not None:
        ox, oy = float(shift[0]), float(shift[1])
        if ox or oy:
            sx, sy = ox * iw, oy * ih
    if z != 1.0:
        cx, cy = sx + cw / 2.0, sy + ch / 2.0
        cw, ch = cw / z, ch / z
        sx, sy = cx - cw / 2.0, cy - ch / 2.0
    return sx, sy, cw, ch


def render_shift(path: Path | None = None) -> tuple[float, float] | None:
    """Crop-origin override from the state file, or None (today's ZFRAME).

    Absent, unreadable, or (0, 0) is no shift. HOUSE_RENDER_SHIFT=0 ignores
    the file. A wall process that imported this module before the reader
    existed does not honour the file until that process starts again.
    """
    if os.environ.get("HOUSE_RENDER_SHIFT", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    p = path if path is not None else (crissa_home.HOME / "crissa-render-shift.json")
    try:
        raw = json.loads(Path(p).read_text(encoding="utf-8"))
        cx = float(raw.get("cx") or 0)
        cy = float(raw.get("cy") or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError):
        return None
    if not cx and not cy:
        return None
    return (cx, cy)


def _luma_grid(src: Image.Image, g: Geom, crissa_own: bool, hole_zoom: float | None = None,
               shift: tuple[float, float] | None = None, mouth=None, frame=None):
    """Return a float grid (ROWS x COLS) in 0..1 following renderAscii.

    crissa_own      : ZFRAME crop filling the grid, bokeh + face metering (crissaOwnFrame)
    hole_zoom     : fovSourceFromBounds() for a FULLSCREEN window: the clip is a
                    plane pinned to screen height, the window samples all of it,
                    z<1 widens. This is the kiosk's crissa_hole path (screen.crissa_hole
                    true, screen.fov_zoom_crissa). Letterboxed like the hybrid branch.
    neither       : CONTAIN fit of the whole JPEG (camera / Voice-Only)
    mouth         : her mouth box in this frame's pixels (x, y, w, h), or None. The exposure (the focus box's
                    2nd/98th percentile) is then read without the cells her mouth paint can reach (norm_skip), so
                    a mouth step changes only the cells around the mouth. None is today's grid, byte for byte.
    frame         : None = the kiosk frame above (today's, byte for byte); ("whole", zoom, fx, fy) = the whole film,
                    right-anchored and never stretched, zoomed about her face (lib/wall_frame.py, 2026-10-12)
    """
    COLS, ROWS = g.COLS, g.ROWS
    iw, ih = src.size
    crop_px = None
    if crissa_own and frame is not None and frame[0] == "whole":
        return _luma_grid_whole(src, g, frame, mouth)
    if crissa_own:
        # THE KIOSK'S FRAMING (renderAscii crissaOwnFrame): the ZFRAME crop of
        # the clip fills the WHOLE grid — sx,sy,sw,sh = ZFRAME; dx=dy=0,
        # dw=COLS, dh=ROWS. "This is a shot someone pointed at a person."
        # The grid's cell aspect (0.6 : 0.98) already matches the crop's
        # 497x280, so this is not a stretch. Grok's height-fill-block-right
        # put her in the right quarter and metered the wrong pixels (Claude,
        # 2026-09-06, crop-for-crop against the 10:33 kiosk screenshot).
        # hole_zoom (screen.fov_zoom_crissa) widens about the crop's centre.
        # shift replaces the origin only; (0, 0) stays on ZFRAME.
        sx, sy, cw, ch = crop_box(iw, ih, hole_zoom, shift)
        src_x, src_y = max(0.0, sx), max(0.0, sy)
        src_r, src_b = min(float(iw), sx + cw), min(float(ih), sy + ch)
        dx = int((src_x - sx) / cw * COLS)
        dy = int((src_y - sy) / ch * ROWS)
        dw = max(1, int(round((src_r - src_x) / cw * COLS)))
        dh = max(1, int(round((src_b - src_y) / ch * ROWS)))
        crop_px = (int(src_x), int(src_y), max(int(src_x) + 1, int(src_r)), max(int(src_y) + 1, int(src_b)))
        crop = src.crop(crop_px).convert("L")
        # Bokeh: blur the whole frame (kiosk 2px at grid scale), put her back
        # sharp inside the ZFOCUS ellipse. In L from the start: only the luma
        # is metered, and blur/composite/resize are linear, so one channel
        # gives the same grid as three at a third of the cost (2026-09-08).
        blurred = crop.filter(ImageFilter.GaussianBlur(2))
        crop = Image.composite(crop, blurred, _focus_mask(crop.size))
        small = Image.new("L", (COLS, ROWS), 0)
        small.paste(crop.resize((dw, dh), Image.Resampling.LANCZOS), (dx, dy))
    else:
        sa = iw / max(1, ih)
        ta = (COLS * 0.6) / (ROWS * 0.98)
        dw, dh = COLS, ROWS
        if sa > ta:
            dh = max(1, round(COLS * 0.6 / (0.98 * sa)))
        else:
            dw = max(1, round(ROWS * 0.98 * sa / 0.6))
        dx, dy = (COLS - dw) // 2, (ROWS - dh) // 2
        small = Image.new("L", (COLS, ROWS), 0)
        small.paste(src.resize((dw, dh), Image.Resampling.LANCZOS).convert("L"), (dx, dy))

    if np is None:
        return None, (dx, dy, dw, dh)
    small_u8 = np.asarray(small, dtype=np.uint8)
    lv = small_u8.astype(np.float32) / 255.0

    r0, r1, c0, c1 = dy, dy + dh, dx, dx + dw
    if crissa_own:
        r0 = max(r0, int(dy + dh * (ZFOCUS["y"] - 0.22)))
        r1 = min(r1, int(math.ceil(dy + dh * (ZFOCUS["y"] + 0.22))))
        c0 = max(c0, int(dx + dw * (ZFOCUS["x"] - 0.16)))
        c1 = min(c1, int(math.ceil(dx + dw * (ZFOCUS["x"] + 0.16))))
    box = lv[r0:r1, c0:c1]
    if box.size == 0:
        box = lv
    elif crissa_own and mouth is not None and crop_px is not None:
        box = norm_cells(box, (r0, r1, c0, c1), (dx, dy, dw, dh), crop_px, mouth)
    if crissa_own:
        lo, hi = np.percentile(box, 2), np.percentile(box, 98)
        if hi - lo < 0.04:
            hi = lo + 0.04
    else:
        lo, hi = float(box.min()), float(box.max())
        if hi - lo <= 0.04:
            hi = lo + 0.04

    v = _exposure(small_u8, lv, lo, hi)
    if crissa_own and ZFADE > 0:
        # renderAscii: "She stays lit; the room falls away under the roll."
        # Left of ZFOCUS.x - 0.10 the value is scaled by 1 - ZFADE * k^1.25,
        # k rising toward the left edge. To 40%, not to black.
        edge = ZFOCUS["x"] - 0.10
        xr = np.arange(COLS, dtype=np.float32)[None, :] / max(1, COLS - 1)
        kf = np.clip((edge - xr) / (edge if edge > 0 else 1.0), 0.0, 1.0)
        v = v * (1.0 - ZFADE * np.power(kf, 1.25))
    return np.clip(v, 0, 1), (dx, dy, dw, dh)



# THE EXPOSURE CURVE BY TABLE (round 14, 2026-10-12, density 5 on .101: 1667x428 cells, the grid cost 21 ms a picture
# against 5.5 ms at density 2.08). Every cell of the luma grid is one of 256 values (the resized L picture, /255), so
# the exposure stretch, the floor and the 0.80 curve are worked out once per picture for those 256 values and looked up
# - the same numpy operations on the same float32 inputs, so the same float64 results: measured 2026-10-12, 77,242,140
# cells (recorded frames, densities 0.859 / 2.08 / 5, fixed and measured lo/hi) 0 differing, 7x less time for the
# step. The fade and the blend still multiply per cell, as before. Kill: HOUSE_WALL_CURVE_TABLE=0.
CURVE_TABLE = os.environ.get("HOUSE_WALL_CURVE_TABLE", "1").strip().lower() not in ("0", "false", "no", "off")
_LV256 = None


def _exposure(small_u8, lv, lo, hi):
    """(lv - lo) / (hi - lo), the floor and the 0.80 curve, for every cell: by table when on, else per cell."""
    FLOOR = 0.42
    if CURVE_TABLE and small_u8 is not None:
        global _LV256
        if _LV256 is None:
            _LV256 = np.arange(256, dtype=np.float32) / 255.0
        t = (_LV256 - lo) / (hi - lo)
        t = np.where(t <= FLOOR, 0.0, (t - FLOOR) / (1 - FLOOR))
        return (np.clip(t, 0, 1) ** 0.80)[small_u8]
    v = (lv - lo) / (hi - lo)
    v = np.where(v <= FLOOR, 0.0, (v - FLOOR) / (1 - FLOOR))
    return np.clip(v, 0, 1) ** 0.80

def cell_glass(g: Geom) -> tuple[float, float]:
    """One cell's size on the glass: mask_from_idx stretches the integer glyph sheet onto W x H, so W/COLS x H/ROWS."""
    return g.W / float(g.COLS), g.H / float(g.ROWS)


@lru_cache(maxsize=4)
def _focus_mask_film(size, cx: float, cy: float, rx: float, ry: float):
    """The whole frame's sharp ellipse, at the film's own size (her face point, the kiosk's radii in film pixels)."""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    return mask


def _luma_grid_whole(src: Image.Image, g: Geom, frame, mouth=None):
    """_luma_grid's `whole` frame (the User 2026-10-12): the whole film, never stretched, right-anchored, zoomed about
    her face; the bokeh, the metering box and the room's fade follow her face point; a spare side blends into the
    ground. Same exposure rules as the kiosk frame (2nd/98th percentile of the metering box, the floor, the gamma)."""
    COLS, ROWS = g.COLS, g.ROWS
    iw, ih = src.size
    cw_g, ch_g = cell_glass(g)
    L = wall_frame.layout(iw, ih, COLS, ROWS, frame[1], (frame[2], frame[3]), cw_g, ch_g)
    sx0, sy0, sx1, sy1 = L["crop"]
    dx, dy, dw, dh = L["place"]
    lum = src.convert("L")
    blurred = lum.filter(ImageFilter.GaussianBlur(2))
    fcx, fcy, frx, fry = L["focus"]
    mask = _focus_mask_film(lum.size, round(fcx + sx0, 2), round(fcy + sy0, 2), round(frx, 2), round(fry, 2))
    lum = Image.composite(lum, blurred, mask)
    small = Image.new("L", (COLS, ROWS), 0)
    small.paste(lum.resize((dw, dh), Image.Resampling.LANCZOS, box=(sx0, sy0, sx1, sy1)), (dx, dy))
    if np is None:
        return None, (dx, dy, dw, dh)
    small_u8 = np.asarray(small, dtype=np.uint8)
    lv = small_u8.astype(np.float32) / 255.0
    c0, c1, r0, r1 = L["meter"]
    box = lv[r0:r1, c0:c1]
    if box.size == 0:
        box = lv
    elif mouth is not None:
        box = norm_cells(box, (r0, r1, c0, c1), (dx, dy, dw, dh), (sx0, sy0, sx1, sy1), mouth)
    lo, hi = np.percentile(box, 2), np.percentile(box, 98)
    if hi - lo < 0.04:
        hi = lo + 0.04
    v = _exposure(small_u8, lv, lo, hi)
    if ZFADE > 0:
        # the kiosk's room fade, from her face instead of ZFOCUS: left of her face less 0.10 of the glass, to 40 %
        edge = L["face"][0] / max(1, COLS - 1) - 0.10
        if edge > 0:
            xr = np.arange(COLS, dtype=np.float32)[None, :] / max(1, COLS - 1)
            kf = np.clip((edge - xr) / edge, 0.0, 1.0)
            v = v * (1.0 - ZFADE * np.power(kf, 1.25))
    if any(L["blend"]):
        v = v * wall_frame.blend_weights(COLS, ROWS, tuple(L["place"]), tuple(L["blend"]))
    return np.clip(v, 0, 1), (dx, dy, dw, dh)


# THE MOUTH DOES NOT SET THE EXPOSURE (2026-10-11, the .101 lips: "make the normalization independent of the
# painted mouth"). The film's exposure is the focus box's 2nd/98th luma percentile, and on a loop whose mouth sits in
# that box (Lila's three) a mouth step moved it: the open mouth's dark inside (and her teeth) are the focus box's
# darkest (brightest) cells, every cell near a glyph step flipped, the whole glass changed, and the step could be
# neither a patch nor a cache region. With the conductor's mouth box (the film frame's own sidecar box, X-Mouth-Box)
# the percentiles skip the box's cells, widened by NORM_SKIP_PX frame pixels for the JPEG's 16 px blocks, the bokeh
# blur and the LANCZOS taps (and NORM_SKIP_X / _UP / _DOWN of its size, 0: the jaw hide around it inpaints skin from
# skin, mid-tones that never reach the 2nd/98th percentile's rank). Measured on the seven core loops at 3440x1440
# d2.08 with her talking takes' mouths pasted over the jaw hide's reach: mouth steps are regions on 96-100 % (Lila's
# loops: 28-76 % today); a generous skip (0.5 w, 1 h up, 1 h down, +24 px) kept the same 100 % but moved 8-17 % of the
# cells of an idle picture against today's, this one 0 % on Crissa's loops and 2.7-4.1 % on Lila's
# (docs/checkpoints/wall-speed-101-20261011). Fewer than NORM_KEEP_MIN of the focus box's cells left: the whole box,
# as today. The box is fixed per film frame, so a frame's idle picture and its speaking pictures share one exposure.
NORM_SKIP_X = 0.0
NORM_SKIP_UP = 0.0
NORM_SKIP_DOWN = 0.0
NORM_SKIP_PX = 16.0
NORM_KEEP_MIN = 0.25


def norm_skip_rect(mouth) -> tuple[float, float, float, float] | None:
    """The frame pixels (x0, y0, x1, y1) the exposure leaves out for a mouth box (x, y, w, h), or None."""
    try:
        x, y, w, h = (float(v) for v in mouth)
    except (TypeError, ValueError):
        return None
    if not (w > 0 and h > 0) or any(v != v for v in (x, y, w, h)):
        return None
    p = NORM_SKIP_PX
    return (x - NORM_SKIP_X * w - p, y - NORM_SKIP_UP * h - p,
            x + w + NORM_SKIP_X * w + p, y + h + NORM_SKIP_DOWN * h + p)


def norm_cells(box, rc, place, crop_px, mouth):
    """The focus box's cells the exposure reads: all of `box` (lv[r0:r1, c0:c1]) but those a mouth paint can reach.
    `place` (dx, dy, dw, dh) is where the crop `crop_px` (frame pixels) lands on the grid. Returns `box` itself when
    nothing is left out or too little would be left."""
    rect = norm_skip_rect(mouth)
    if rect is None:
        return box
    r0, r1, c0, c1 = rc
    dx, dy, dw, dh = place
    px0, py0, px1, py1 = crop_px
    kx, ky = dw / max(1, px1 - px0), dh / max(1, py1 - py0)       # grid cells per frame pixel
    ec0 = max(c0, int(math.floor(dx + (rect[0] - px0) * kx)))
    ec1 = min(c1, int(math.ceil(dx + (rect[2] - px0) * kx)))
    er0 = max(r0, int(math.floor(dy + (rect[1] - py0) * ky)))
    er1 = min(r1, int(math.ceil(dy + (rect[3] - py0) * ky)))
    if ec1 <= ec0 or er1 <= er0:
        return box
    keep = np.ones(box.shape, dtype=bool)
    keep[er0 - r0:er1 - r0, ec0 - c0:ec1 - c0] = False
    n = int(keep.sum())
    if n < NORM_KEEP_MIN * box.size:
        return box
    return box[keep]


@lru_cache(maxsize=2)
def _focus_mask(size):
    mask = Image.new("L", size, 0)
    md = ImageDraw.Draw(mask)
    cx, cy = ZFOCUS["x"] * size[0], ZFOCUS["y"] * size[1]
    rx, ry = ZFOCUS["r"] * size[0] * 1.15, ZFOCUS["r"] * size[1] * 1.85
    md.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    return mask


def cell_px(g: Geom) -> tuple[int, int]:
    """One glyph tile's integer size (cw, ch) on the sheet."""
    return max(1, int(round(g.cell_w))), max(1, int(round(g.cell_h)))


def ascii_idx(src: Image.Image | None, g: Geom, crissa_own: bool, t: float, hole_zoom: float | None = None,
              shift: tuple[float, float] | None = None, mouth=None, frame=None):
    """The glyph index of every cell (ROWS x COLS, 0..len(RAMP)-1): everything the picture knows about the frame.
    `t` matters only when there is no frame (the slow field). `mouth`: see _luma_grid. Needs numpy."""
    COLS, ROWS = g.COLS, g.ROWS
    v = None
    if src is not None:
        v, _ = _luma_grid(src, g, crissa_own, hole_zoom, shift, mouth=mouth, frame=frame)
    if v is None:
        x = np.arange(COLS)[None, :] / max(1, COLS - 1)
        y = np.arange(ROWS)[:, None] / max(1, ROWS - 1)
        v = 0.12 + 0.07 * np.sin(x * 3.1 + t * 0.13) * np.cos(y * 2.3 - t * 0.09)
    return np.rint(np.clip(v, 0, 1) * (len(RAMP) - 1)).astype(np.int64)


def mask_from_idx(idx, g: Geom) -> Image.Image:
    """The glyphs of a cell grid at wall size, before the glow (L)."""
    COLS, ROWS = g.COLS, g.ROWS
    cw, ch = cell_px(g)
    atlas = _glyph_atlas(g.ascii_fs, cw, ch)          # (10, ch, cw)
    sheet = atlas[idx].transpose(0, 2, 1, 3).reshape(ROWS * ch, COLS * cw)
    im = Image.fromarray(np.ascontiguousarray(sheet), "L")
    if im.size != (g.W, g.H):
        # integer tiles are slightly larger than the CSS cell; NEAREST keeps stems
        im = im.resize((g.W, g.H), Image.Resampling.NEAREST)
    return im


def ascii_mask(src: Image.Image | None, g: Geom, crissa_own: bool, t: float, hole_zoom: float | None = None,
               shift: tuple[float, float] | None = None, mouth=None, frame=None) -> Image.Image:
    """The #ascii <pre> as an L coverage image (0..255) at wall size."""
    if np is None:
        return Image.new("L", (g.W, g.H), 0)
    im = mask_from_idx(ascii_idx(src, g, crissa_own, t, hole_zoom, shift, mouth=mouth, frame=frame), g)
    return _text_shadow(im, g) if crissa_own or src is not None else im


GLOW_PX = 14        # #ascii text-shadow: 0 0 14px rgba(217,160,91,.30)
GLOW_ALPHA = 0.36   # .30 in CSS; a touch more here lifts the median toward the kiosk's (25 vs 18)


def _text_shadow(im: Image.Image, g: Geom) -> Image.Image:
    """CSS text-shadow 0 0 14px at 30%: a soft halo BEHIND the glyphs. The
    blur is done at quarter scale (sigma 14/2/4) and bilinear-upsampled —
    a full-size 2560x1440 Gaussian would eat the 67 ms budget on its own.
    Coverage = glyph + glow * (1 - glyph): the letter stays crisp, the halo
    fills the gaps between stems, which is what lifts the kiosk's figure
    off the ground."""
    if np is None:
        return im
    from PIL import ImageChops
    # Held clip / still: the luma grid is unchanged. Fingerprint a 32×18
    # thumb and reuse the whole shadowed frame (Claude 2026-09-15, 5.7 ms).
    fp = im.resize((32, 18), Image.Resampling.BOX).tobytes()
    if _GLOW.get("fp") == fp and _GLOW.get("out") is not None and _GLOW["out"].size == im.size:
        return _GLOW["out"]
    _GLOW["n"] += 1
    glow = _GLOW.get("im")
    if glow is None or glow.size != (g.W, g.H) or _GLOW["n"] % 2 == 0:
        glow = glow_from_small(glow_small(im, g), g)
        _GLOW["im"] = glow
    # a + b(1-a) is exactly ImageChops.screen — one C pass over uint8, not
    # three float passes over 3.7 MP (that was +20 ms on the bg line).
    out = ImageChops.screen(im, glow)
    _GLOW["fp"] = fp
    _GLOW["out"] = out
    return out


_GLOW: dict = {"n": 0, "im": None, "fp": None, "out": None}

GLOW_Q = 8          # the glow is blurred at 1/GLOW_Q scale


def glow_small(im: Image.Image, g: Geom) -> Image.Image:
    """The glyph mask at 1/GLOW_Q scale (BOX): the glow's input."""
    return im.resize((max(1, g.W // GLOW_Q), max(1, g.H // GLOW_Q)), Image.Resampling.BOX)


def glow_from_small(small: Image.Image, g: Geom) -> Image.Image:
    """The text-shadow layer at wall size from glow_small's picture."""
    glow = small.filter(ImageFilter.GaussianBlur(GLOW_PX / 2.0 / GLOW_Q))
    return glow_up(glow.point(_glow_lut()), (g.W, g.H))


# THE GLOW'S UPSAMPLE BY OPENCV (the User 2026-10-13, "yes go ahead with the opencv glow"). The x8 upsample of the
# blurred glow to the glass was the biggest single stage of a fresh picture: PIL BILINEAR, 7.6 ms of 28.0 at 3440x1440
# density 2 on one core (docs/checkpoints/lowend-bench-20261013). OpenCV's INTER_LINEAR_EXACT samples the same way
# (pixel centres, (x + 0.5) / 8 - 0.5, edges clamped) and rounds once, where PIL rounds after its horizontal pass too,
# so it is at most 1 grey level off PIL's picture and NOT byte-equal: a new look, which is why it waited for his OK.
# Measured 2026-10-13 on one core (docs/checkpoints/wall-glow-cv-20261013, 90 recorded frames of three films): the
# upsample 7.6 -> 0.8 ms and the whole fresh picture to RGB 27.9 -> 21.2 ms p50 at 3440x1440 d2 (38.7 -> 32.1 at d5);
# at densities 1/2/3/5 on 3440x1440 and 2560x1600 the glow is at most 1 level off PIL's (5-8 % of its pixels) and so is
# the coverage the Wall paints (0.8-2.1 % of its pixels).
# EXACT, not INTER_LINEAR (0.15 ms faster, also 1 level off): bit-exact by OpenCV's contract, the same bytes on every
# SIMD path and on a Mac's, so the cache's region (an upsample of a crop) equals the whole picture's; and nearer PIL.
# ONE THREAD: OpenCV's pool ran it on all 32 cores here for 4.3 ms of CPU against 0.7 on the calling thread, so the
# first use sets cv2.setNumThreads(1) for the process (the processes that draw the glow - the film child, the pre-fill
# workers, the Wall when the child is off - use OpenCV for nothing heavier; with no pool a fork is safe too).
# Every glow upsample must go through glow_up: glow_from_small (fresh, memo, the film child, the cache's whole pictures,
# the disk pre-fill) AND the cache's region (wall_ascii_cache._draw_region, its one line in the checkpoint's patch) - a
# region drawn the other way fails the cache's own region self-check and turns regions off (exact, but every mouth step
# drawn whole). The disk tier's code print names glow_method(), so the two
# looks never share a directory. Kill: HOUSE_WALL_GLOW_CV=0 (PIL, the bytes before). No cv2: PIL, said once.
GLOW_CV = os.environ.get("HOUSE_WALL_GLOW_CV", "1").strip().lower() not in ("0", "false", "no", "off")
_CV: dict = {"tried": False, "cv2": None}


def _glow_cv():
    """cv2 for the glow's upsample, or None (switched off, no numpy, or cv2 will not import: said once)."""
    if not GLOW_CV or np is None:
        return None
    if not _CV["tried"]:
        _CV["tried"] = True
        try:
            import cv2
            cv2.setNumThreads(1)
            _CV["cv2"] = cv2
        except Exception as e:  # noqa: BLE001 - any failure to load is PIL's glow, never no glow
            import sys
            print("house-wall-ascii: glow: no OpenCV (%s: %s); the glow's upsample is PIL's"
                  % (type(e).__name__, str(e)[:120]), file=sys.stderr, flush=True)
    return _CV["cv2"]


def glow_up(lit: Image.Image, size: tuple[int, int]) -> Image.Image:
    """The lit glow (L, 1/GLOW_Q of the glass) upsampled to `size`: OpenCV INTER_LINEAR_EXACT, else PIL BILINEAR."""
    cv2 = _glow_cv()
    if cv2 is None:
        return lit.resize(size, Image.Resampling.BILINEAR)
    return Image.fromarray(cv2.resize(np.asarray(lit), (int(size[0]), int(size[1])),
                                      interpolation=cv2.INTER_LINEAR_EXACT), "L")


def glow_method() -> str:
    """What draws the glow's upsample in this process: 'cv2 <version> linear-exact' or 'pil'."""
    cv2 = _glow_cv()
    return "pil" if cv2 is None else "cv2 %s linear-exact" % cv2.__version__


@lru_cache(maxsize=1)
def _glow_lut():
    return [int(round(i * GLOW_ALPHA)) for i in range(256)]


# ---- vignette ----------------------------------------------------------------
@lru_cache(maxsize=2)
def vignette(W: int, H: int) -> Image.Image:
    """The .vignette stack: three linear gradients + one radial, ground-coloured."""
    if np is None:
        return Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x = np.linspace(0, 1, W, dtype=np.float32)[None, :]
    y = np.linspace(0, 1, H, dtype=np.float32)[:, None]

    def lin(t, stops):
        out = np.zeros_like(t)
        for (p0, a0), (p1, a1) in zip(stops, stops[1:]):
            m = (t >= p0) & (t <= p1)
            k = np.where(p1 > p0, (t - p0) / max(1e-6, p1 - p0), 0)
            out = np.where(m, a0 + (a1 - a0) * k, out)
        out = np.where(t < stops[0][0], stops[0][1], out)
        out = np.where(t > stops[-1][0], stops[-1][1], out)
        return out

    # 100deg gradient ≈ left→right with a slight tilt; approximate with x + 0.18*y
    tx = np.clip(x * 0.98 + (y - 0.5) * 0.18, 0, 1)
    g1 = lin(tx, [(0.0, 0.94), (0.26, 0.78), (0.52, 0.0), (1.0, 0.0)])
    g2 = lin(1 - y, [(0.0, 0.92), (0.22, 0.0), (1.0, 0.0)])       # 0deg: bottom up
    g3 = lin(y, [(0.0, 0.88), (0.20, 0.0), (1.0, 0.0)])           # 180deg: top down
    rx = (x - 0.52) / 1.25
    ry = (y - 0.45) / 0.95
    rr = np.sqrt(rx * rx + ry * ry) * 2.0
    g4 = lin(rr, [(0.0, 0.0), (0.22, 0.0), (1.0, 0.90)])
    # stack with source-over: alpha = 1 - Π(1 - a)
    a = 1 - (1 - g1) * (1 - g2) * (1 - g3) * (1 - g4)
    layer = Image.new("RGBA", (W, H), GROUND + (0,))
    layer.putalpha(Image.fromarray((a * 255).astype(np.uint8), "L"))
    return layer


@lru_cache(maxsize=2)
def _vignette_inv(W: int, H: int) -> Image.Image:
    """(1 - vignette alpha) as L — how much of the ascii survives the scrim."""
    a = vignette(W, H).getchannel("A")
    return a.point(lambda p: 255 - p)


ASCII_OPACITY = 0.62   # body.chan-live #ascii — the wall is always chan-live once live
# Logs live on the left reading column (--rail-w 57.5%; roll right ≈ 0.548).
# Glyph coverage is zero through that column so the roll/HUD sit on GROUND,
# then eases in so the figure has no hard vertical edge. 0.36–0.52 used to
# leave ember under the said-text (the mismatched blacks).
LOG_FADE_LO = 0.55
LOG_FADE_HI = 0.67


def _log_fade(im: Image.Image) -> Image.Image:
    """Smoothstep the glyph coverage from black (logs) into full ASCII."""
    if np is None:
        return im
    w, h = im.size
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)
    t = np.clip((x - LOG_FADE_LO) / max(1e-6, LOG_FADE_HI - LOG_FADE_LO), 0.0, 1.0)
    k = t * t * (3.0 - 2.0 * t)
    arr = (np.asarray(im, dtype=np.float32) * k[None, :]).astype(np.uint8)
    return Image.fromarray(arr, "L")


@lru_cache(maxsize=4)
def _ember_luts(opacity: float = ASCII_OPACITY, ink: float = 0.0):
    """GROUND -> the hot end, at `opacity`. `ink` (0..1) walks that hot end from
    EMBER to INK — the only thing left once the layer is opaque, and what the
    brightness dial buys past 1.0 (wall_prefs.ink_gain). ink=0 is byte-for-byte
    the palette this drew before the dial went past 1."""
    luts = []
    for c in range(3):
        lo = GROUND[c]
        hi = EMBER[c] if ink <= 0 else int(round(EMBER[c] + (INK[c] - EMBER[c]) * min(1.0, ink)))
        luts.append([int(round(lo + (hi - lo) * opacity * i / 255)) for i in range(256)])
    return luts


# ---- small helpers -----------------------------------------------------------
def _text(d, xy, s, f, fill, spacing_em=0.0):
    """draw text with letter-spacing (em of the font size)."""
    if not spacing_em:
        d.text(xy, s, font=f, fill=fill)
        return d.textlength(s, font=f)
    x, y = xy
    sp = f.size * spacing_em
    for chh in s:
        d.text((x, y), chh, font=f, fill=fill)
        x += d.textlength(chh, font=f) + sp
    return x - xy[0]


def _text_w(d, s, f, spacing_em=0.0):
    if not s:
        return 0.0
    if not spacing_em:
        return float(d.textlength(s, font=f))
    sp = f.size * spacing_em
    return float(sum(d.textlength(c, font=f) for c in s) + sp * (len(s) - 1))

def fit_note(d, text, f, max_w, u, spacing_em=0.06, min_size_u=0.26):
    """The Advanced panel's note line, made to fit `max_w` px: the same font
    stepped down (never below `min_size_u` x u), then the text cut with an
    ellipsis at the floor size. Returns (font, text). Kill
    HOUSE_WALL_NOTE_FIT=0 = today's line as written (may overrun)."""
    if os.environ.get("HOUSE_WALL_NOTE_FIT", "1").strip().lower() in ("0", "off", "false", "no"):
        return f, text
    if max_w <= 0 or _text_w(d, text, f, spacing_em) <= max_w:
        return f, text
    floor = max(6, int(min_size_u * u))
    size = int(f.size)
    cur = f
    while size > floor:
        size -= 1
        cur = font("mono", size)
        if _text_w(d, text, cur, spacing_em) <= max_w:
            return cur, text
    t = text
    while len(t) > 1 and _text_w(d, t + "…", cur, spacing_em) > max_w:
        t = t[:-1].rstrip()
    return cur, t + "…"



# NOT ON THIS COMPUTER (2026-10-10, lib/crissa_capability): HUD keys this computer cannot use (krew's Board and Tools
# tapes without krew) are drawn greyed and take no click; the Wall fills this from the capability answer
NA_HUD: set = set()


def _chip(d, x, y, label, f, kind="", pad_x=None, pad_y=None):
    """.chip — uppercase, letter-spaced, 1px border, ground-2 fill. Returns width. kind "na": greyed, not a key."""
    if kind == "na":
        color = tuple(int(c * 0.45) for c in INK2[:3])
        border = tuple(int(c * 0.6) for c in HAIR[:3])
        px = pad_x if pad_x is not None else f.size * 0.55
        py = pad_y if pad_y is not None else f.size * 0.30
        s = label.upper()
        w = sum(d.textlength(c, font=f) + f.size * 0.11 for c in s) - f.size * 0.11
        d.rounded_rectangle((x, y, x + w + 2 * px, y + f.size + 2 * py), radius=2, fill=GROUND2, outline=border)
        _text(d, (x + px, y + py - 1), s, f, color, 0.11)
        return w + 2 * px
    color = {"on": SAGE, "warn": EMBER, "bad": RUST}.get(kind, INK2)
    border = {"on": (0x3F, 0x5B, 0x4A), "warn": (0x6A, 0x50, 0x30), "bad": (0x5E, 0x3A, 0x36)}.get(kind, HAIR)
    px = pad_x if pad_x is not None else f.size * 0.55
    py = pad_y if pad_y is not None else f.size * 0.30
    s = label.upper()
    w = sum(d.textlength(c, font=f) + f.size * 0.11 for c in s) - f.size * 0.11
    h = f.size + 2 * py
    d.rounded_rectangle((x, y, x + w + 2 * px, y + h), radius=2, fill=GROUND2, outline=border)
    _text(d, (x + px, y + py - 1), s, f, color, 0.11)
    return w + 2 * px


def _rounded_paste(base: Image.Image, im: Image.Image, box, radius):
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=radius, fill=255)
    base.paste(im, box, m)


def _pos(h, r, cx=130.0, cy=130.0):
    a = ((h % 12) * 30 - 90) * math.pi / 180
    return cx + math.cos(a) * r, cy + math.sin(a) * r


# ---- the dial ----------------------------------------------------------------
def _ember_bloom(im: Image.Image, layers: tuple[tuple[float, float], ...] = ((5.0, 0.55),)) -> Image.Image:
    """The page's `.dial { filter: drop-shadow(0 0 5px rgba(217,160,91,.55)) }`
    and its hover `drop-shadow(0 0 9px rgba(217,160,91,.85))`: the amber
    shadow of the dial's own alpha. A CSS blur radius is TWICE the Gaussian's
    standard deviation (Filter Effects: "a Gaussian blur with a standard
    deviation equal to half the blur radius"), and Pillow's GaussianBlur takes
    the deviation — so 9px here is GaussianBlur(4.5). Blurring at the CSS
    number spread the glow twice as wide as the page's, washed the camera
    tile's top border and read as "Less glowing Oracle" / "the border of
    Oracle looks so good in html" (the User 2026-09-07). layers: (css_px, opacity)."""
    if not layers:
        return im
    max_r = max(r for r, _ in layers) / 2.0
    pad = int(math.ceil(max_r * 3.0))
    w, h = im.size
    canvas = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    canvas.paste(im, (pad, pad), im)
    out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    alpha = canvas.getchannel("A")
    for radius, opacity in layers:
        glow_a = alpha.filter(ImageFilter.GaussianBlur(radius / 2.0))
        if opacity != 1.0:
            glow_a = glow_a.point(lambda p, o=opacity: min(255, int(p * o)))
        glow = Image.new("RGBA", canvas.size, EMBER + (0,))
        glow.putalpha(glow_a)
        out.alpha_composite(glow)
    out.alpha_composite(canvas)
    return out


_DIAL_MEMO = {"key": None, "im": None}


# THE PAGE IS THE REFERENCE (the User 2026-09-07 pm: "html looks perfect to me
# ... make our wallpaper background version as beautiful as the html version
# - so seamless"). Its dial at rest: drop-shadow(0 0 5px rgba(217,160,91,.55))
# on a HAIR ring. The glow NEVER changes: hover is thin white rings drawn on
# the chrome layer over this standing dial (render_chrome, p["dial_hover"]),
# nothing re-rendered — the User 2026-09-07: "thinner white lines and same glow
# on hover at same time". The page's brightness(1.9) hover was tried and read
# as stages, because a second dial had to land under the first.
DIAL_BLOOM_REST = ((5.0, 0.55),)
DIAL_BLOOM_HOVER = DIAL_BLOOM_REST
# hover = a soft AMBER glow along the rings on the chrome layer, over the
# standing one — no lines (the User 2026-09-07: "maybe just the white glow then
# vs lines on hover"; 2026-09-08: "Oracle needs amber on hover too", like
# every other control's hover). (css_blur_px, opacity) per ring, outer then
# inner; the colour is DIAL_HOVER_INK.
DIAL_HOVER_GLOW = ((10.0, 0.42), (10.0, 0.16))    # "subtler, softer yet" (the User 2026-09-07)
DIAL_HOVER_INK = EMBER2


@lru_cache(maxsize=4)
def _dial_hover_glow(size: int) -> Image.Image:
    """The white hover glow for a dial of `size` px: the r96 and r72 rings,
    2 px wide, blurred at the CSS radius / 2 like _ember_bloom. Cached, so a
    hover costs one paste per pointer event."""
    S = size / 260.0
    pad = int(math.ceil(max(r for r, _ in DIAL_HOVER_GLOW) * 1.5)) + 2
    box = size + 2 * pad
    out = Image.new("RGBA", (box, box), DIAL_HOVER_INK + (0,))
    alpha = Image.new("L", (box, box), 0)
    c = box / 2.0
    for r_, (blur, op) in zip((96.0, 72.0), DIAL_HOVER_GLOW):
        layer = Image.new("L", (box, box), 0)
        rr = r_ * S
        ImageDraw.Draw(layer).ellipse((c - rr, c - rr, c + rr, c + rr), outline=255, width=max(2, int(round(2 * S))))
        layer = layer.filter(ImageFilter.GaussianBlur(blur / 2.0)).point(lambda p, o=op: int(p * o))
        alpha = Image.fromarray(np.maximum(np.asarray(alpha), np.asarray(layer))) if np is not None else alpha
    out.putalpha(alpha)
    return out


def _brighten(im: Image.Image, k: float) -> Image.Image:
    """CSS brightness(k): every colour channel times k, clamped; alpha kept."""
    if abs(k - 1.0) < 1e-6:
        return im
    lut = [min(255, int(round(i * k))) for i in range(256)]
    r, g, b, a = im.split()
    return Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))


# THE ROLL READS THROUGH THE DIAL AND THE CAMERA BOX (the User 2026-09-09: "the
# oracle clock and camera box are where logs roll … make the oracle
# transparent and lighten the Hud box … just the visage of the box but the
# text is more easy to read when it rolls"). The dusk (.35 on 2026-09-06,
# "Needs that Dusk matching everywhere") goes to 0: rings on bare glass. The
# camera corner drops its plate and its picture sits lighter; the outline,
# the label and the visor HUD stay — the box's visage. The page's
# .clockwrap::before and #pipwrap carry the same numbers.
DIAL_DUSK_ALPHA = 0.0        # was .35
FT_FADE_ALPHA = 0.07         # the FT window's Fade veil: the picture at this alpha (.18 → .12 → .07, the User 2026-09-09: "fade more for fade")
FT_REST_LINE = INK + (76,)   # the FT window's hairline at rest: pale white like the camera box (the User 2026-09-09); ember under the pointer
FT_FADE_FRAME = 0.37         # Fade: the hairline and the shadow at this share too, so the frame is as faint as the camera box's (the User 2026-09-09: "its border is not fading as much as the other side")
DIAL_MUTE = 0.30             # the clock at rest: its alpha × this, near the down camera box (the User 2026-09-09: "fade the oracle clock so similar in brightness to below camera box"); the pointer on it brings it up full; the lamps (lobe, door, beam) are their own layers and stay
CAM_PLATE_ALPHA = 0          # the GROUND plate under the corner picture; was 52


@lru_cache(maxsize=4)
def _dial_dusk(size: int) -> Image.Image:
    """The page's `.clockwrap::before`: a disc 170% of the dial, GROUND at .35
    out to 40% of its radius and gone by 78% — the dusk that lets the room
    show THROUGH the open dial without the glyphs fighting the rings. It is
    the wrap's, not the dial's, so it stays when the dial folds to the dot."""
    box = max(2, int(round(size * 1.7)))
    im = Image.new("RGBA", (box, box), GROUND + (0,))
    if np is None or DIAL_DUSK_ALPHA <= 0.0:
        return im
    rr = box / 2.0
    yy, xx = np.mgrid[0:box, 0:box]
    r = np.hypot(xx - rr + 0.5, yy - rr + 0.5) / rr
    a = np.clip((0.78 - r) / (0.78 - 0.40), 0.0, 1.0) * DIAL_DUSK_ALPHA
    im.putalpha(Image.fromarray((a * 255.0 + 0.5).astype("uint8"), "L"))
    return im


def _dial_key(house: dict, presence: dict, size: int, hover: bool = False):
    sc = ((house.get("room_event") or {}).get("seat_clock")) or {}
    return (
        int(size), bool(hover),
        tuple(sc.get("legal_sit") or (12, 1, 2, 3, 8, 10, 11)),
        tuple(sc.get("wall") or sc.get("wall_stations") or (4, 5, 6, 7)),
        tuple(sc.get("blanked_sit") or (9,)),
        int(sc.get("door_hour") or 12),
        tuple(sorted((house.get("seats") or {}).items())) if isinstance(house.get("seats"), dict) else (),
        user_authed(house, presence),
        mood_of(house),
        secure_state(house, presence),
    )


def dial(house: dict, presence: dict, size: int, t: float = 0.0, hover: bool = False) -> Image.Image:
    """The oracle clock, drawn from the SVG the page uses (viewBox 260).

    `hover` is accepted for callers but changes nothing: the hover is the
    chrome's white rings over this drawing (DIAL_HOVER_RING), so the dial is
    one image, cached, whatever the pointer does."""
    speaking = mood_of(house) == "speaking"
    key = _dial_key(house, presence, size, hover)
    if not speaking and _DIAL_MEMO["key"] == key and _DIAL_MEMO["im"] is not None:
        return _DIAL_MEMO["im"]
    S = size / 260.0
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    sc = ((house.get("room_event") or {}).get("seat_clock")) or {}
    legal = sc.get("legal_sit") or [12, 1, 2, 3, 8, 10, 11]
    wall = sc.get("wall") or sc.get("wall_stations") or [4, 5, 6, 7]
    blank = sc.get("blanked_sit") or [9]
    door_h = int(sc.get("door_hour") or 12) % 12 or 12
    seats = house.get("seats") if isinstance(house.get("seats"), dict) else {}
    try:
        media_h = int(seats.get("media") or (blank[0] if blank else 0)) % 12 or None
    except (TypeError, ValueError, IndexError):
        media_h = None
    marks = seats.get("marks") if isinstance(seats.get("marks"), dict) else {}
    if not marks:
        regs = seats.get("regulars") if isinstance(seats.get("regulars"), dict) else {}
        marks = {str(h): user_mark(pid) for h, pid in regs.items() if pid}
    if not marks and seats.get("user_seat") is not None:
        marks = {str(seats.get("user_seat")): str(seats.get("user_mark") or user_mark(seats.get("user")))}

    def P(h, r):
        x, y = _pos(h, r)
        return x * S, y * S

    def circle(cx, cy, r, fill=None, outline=None, width=1):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline, width=max(1, int(round(width))))

    c = 130 * S
    # The page's two rings, exactly: r96 stroke #242A37 (HAIR), r72 stroke
    # #1B202B. No aura, no ink-3 "clean ring": the 5px .55 amber shadow under
    # a HAIR hairline is the border the User likes on the page, and hover lifts it
    # with brightness(1.9) below, not with a hand-drawn white ring.
    circle(c, c, 96 * S, outline=HAIR + (255,), width=S)
    circle(c, c, 72 * S, outline=(0x1B, 0x20, 0x2B, 255), width=S)
    # a hair bigger (the User 2026-09-06: "numbers on the dial a hair bigger")
    fl = font("mono", 7.4 * S)
    fd = font("mono", 8.4 * S)
    marks_out: list = []      # hour labels, painted on top of the bloom
    for h in range(1, 13):
        is_wall, is_blank, is_legal = h in wall, h in blank, h in legal
        a = P(h, 96)
        b = P(h, 86 if is_wall else 89)
        col = HAIR if is_wall else ((0x1B, 0x20, 0x2B) if is_blank else INK3)
        d.line([a, b], fill=col, width=max(1, int(round((3 if is_wall else 1) * S))))
        lx, ly = P(h, 110)
        # The labels are the places (the User 2026-09-07: "Put Bi where 6 is in
        # its stead. Put Mi where 8 is. 1-5 is a wall so dim them like other
        # offs, amber just faint. Dim [the numerals] to barely perceptible and
        # redden them"): DOOR and MEDIA from the geometry, a regular's mark
        # at their registered hour, wall hours faint amber, the rest a faint red.
        mark = marks.get(str(h))
        if h == door_h:
            label, f, fill = "DOOR", fd, EMBER
        elif h == media_h:
            label, f, fill = "MEDIA", fd, INK2
        elif mark:
            label, f, fill = mark, fd, INK2
        elif is_wall:
            label, f, fill = str(h), fl, EMBER + (77,)
        else:
            label, f, fill = str(h), fl, RUST + (90,)
        tw = d.textlength(label, font=f)
        marks_out.append((lx - tw / 2, ly + 2.6 * S - f.size * 0.8, label, f, fill))
    # the door globe is NOT here: on the page #doorhalo is dark until the room
    # event is door-ish and then breathes — a 5 Hz tile (render_door_globe)
    # centre: the AUTH orb, then her white dot. No pip disc, no ear ring, no
    # mark — "drop the green dot. Crissa and only the white dot no circle. from
    # that the pale green orb glow ... Nothing there for no reason" (the User
    # 2026-09-07). Kiosk: #authOrb + #crissaCore. The User's fixed seat is the desk.
    circle(c, c, 3.2 * S, fill=INK)
    # the regulars' desks are their labels on the ring (marks above); nothing
    # else sits inside the dial — "People can be at all labeled spots."
    fz = font("mono", 7 * S)
    nm = crissa_home.her_name().upper()          # the house's own name for her there (the Crissa merge, 2026-10-11)
    tw = sum(d.textlength(ch_, font=fz) + 2.4 * S for ch_ in nm) - 2.4 * S
    _text(d, (c - tw / 2, 112 * S - fz.size * 0.8), nm, fz, INK3, 2.4 / 7)
    # the sound lobe is its own 5 Hz layer now (render_lobe); the dial stays
    # a 1 Hz drawing (the User 2026-09-07: "No way to just repaint the dots?")
    # #crissaGlow — voice on-air rings, scaled into the rest breath (kiosk s=10/42).
    # HUD is ~1 Hz, so breathe slowly; the 6.2 Hz page pulse would alias.
    # the on-air rings moved to the 5 Hz lobe tile (render_lobe), keyed on
    # her BROADCAST — the sink — not on the logged line (the User 2026-09-07)
    # the page's drop-shadow, rest or hover (see DIAL_BLOOM_*)
    out = _ember_bloom(im, layers=DIAL_BLOOM_HOVER if hover else DIAL_BLOOM_REST)
    # The hour labels go on LAST. Under the bloom the glow spread into the
    # letterforms and ate them — "I cant read the M in media" (the User
    # 2026-09-07) — so the ring glows and the words stay crisp.
    lpad = (out.size[0] - size) // 2
    ld = ImageDraw.Draw(out)
    for lx_, ly_, label, f_, fill_ in marks_out:
        ld.text((lx_ + lpad, ly_ + lpad), label, font=f_, fill=fill_)
    if user_authed(house, presence):
        # the orb rides ABOVE the bloom (under it the ember wash turned the
        # pale sage warm and dim), then the white dot comes back on top
        pad = (out.size[0] - size) // 2
        orb = render_orb(S, SAGE, 1.0)
        oc = int(round(c + pad - orb.size[0] / 2))
        out.alpha_composite(orb, (oc, oc))
        od = ImageDraw.Draw(out)
        rr = 3.2 * S
        od.ellipse((c + pad - rr, c + pad - rr, c + pad + rr, c + pad + rr), fill=INK)
    if not speaking:
        _DIAL_MEMO["key"] = key
        _DIAL_MEMO["im"] = out
    return out


# ---- SoT: presence is the User; the house service is the panel ---------------
def _css_filter_matrix() -> "np.ndarray":
    """The kiosk #pipwrap filter as one 3x3 + offset on linear-ish RGB:
    sepia(1) saturate(2) hue-rotate(-12deg) contrast(.82) brightness(.40)
    (display/voiceonly-home.html; "Sepia carries the corner into the ember
    scheme"). the User 2026-09-06: "not seeing the Amber tint on the Oracle feed
    thumbnail square"."""
    sep = np.array([[.393, .769, .189], [.349, .686, .168], [.272, .534, .131]])
    sat = 2.0
    satm = np.array([[.213 + .787 * sat, .715 - .715 * sat, .072 - .072 * sat],
                     [.213 - .213 * sat, .715 + .285 * sat, .072 - .072 * sat],
                     [.213 - .213 * sat, .715 - .715 * sat, .072 + .928 * sat]])
    th = math.radians(-12.0)
    c, s_ = math.cos(th), math.sin(th)
    hue = np.array([[.213 + c * .787 - s_ * .213, .715 - c * .715 - s_ * .715, .072 - c * .072 + s_ * .928],
                    [.213 - c * .213 + s_ * .143, .715 + c * .285 + s_ * .140, .072 - c * .072 - s_ * .283],
                    [.213 - c * .213 - s_ * .787, .715 - c * .715 + s_ * .715, .072 + c * .928 + s_ * .072]])
    m = hue @ satm @ sep
    contrast, bright = .82, .40
    # contrast(c): x*c + (1-c)/2 ; brightness(b): x*b
    return m * contrast * bright, (1 - contrast) / 2 * bright


_PIP_M, _PIP_OFF = None, 0.0


def _pip_filter(im: Image.Image) -> Image.Image:
    global _PIP_M, _PIP_OFF
    if _PIP_M is None:
        _PIP_M, _PIP_OFF = _css_filter_matrix()
    a = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    out = a @ _PIP_M.T.astype(np.float32) + np.float32(_PIP_OFF)
    out = np.clip(out * 255.0, 0, 255).astype(np.uint8)
    res = Image.fromarray(out, "RGB").convert("RGBA")
    return res


def clock_from_az(az_deg) -> float | None:
    """Mic-frame az → clock hours with tenths. 0° = 12.0, 180° = 6.0, 191° = 6.4.
    The presence speaker_stack rule (BEAM-IS-FF-CLUE), ported so the wall can
    print the beam at the beam's own rate."""
    if az_deg is None:
        return None
    try:
        cam = float(az_deg) % 360.0
    except (TypeError, ValueError):
        return None
    raw = cam / 30.0
    tenth = round(raw + 1e-9, 1)
    if tenth <= 0.0 or tenth >= 12.0:
        return 12.0
    if tenth < 1.0:
        return round(12.0 + tenth, 1)
    return tenth


def lobe_live(sound: dict | None, ear: dict | None, t: float) -> float:
    """The kiosk's fade: exp(-age/1.2) while the tick is under 6 s old and
    speech is heard (puck flag or the ear mid-utterance), conf-scaled (never
    below .35); 0 when dark."""
    snd = sound or {}
    if snd.get("az_deg") is None:
        return 0.0
    try:
        age = max(0.0, t - float(snd.get("ts") or 0.0))
    except (TypeError, ValueError):
        return 0.0
    heard = bool(snd.get("speech")) or bool((ear or {}).get("in_utterance"))
    if not (age < 6.0 and heard):
        return 0.0
    fade = math.exp(-age / 1.2)
    conf = float(snd.get("conf") or 0.0)
    if 0 < conf < 1:
        fade *= max(0.35, conf)
    return fade


NO_BEAM_TEXT = "no beam sig"
NO_BEAM_STALE_S = 15.0       # a beam reading older than this is no signal


def no_beam_sig(house: dict | None, t: float, env: dict | None = None) -> bool:
    """Is there no beam-microphone signal to show? True when the sound feed carries no
    direction, or its last one is older than NO_BEAM_STALE_S. OFF by default
    (CRISSA_NO_BEAM_SIG=1): off, the dial is exactly what it was."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_NO_BEAM_SIG", "0")).strip().lower() not in ("1", "on", "true", "yes"):
        return False
    snd = (house or {}).get("sound") or {}
    if snd.get("az_deg") is None:
        return True
    try:
        return float(t) - float(snd.get("ts") or 0.0) > NO_BEAM_STALE_S
    except (TypeError, ValueError):
        return True


def on_air(crissa: dict | None, t: float | None = None) -> bool:
    """She is BROADCASTING: paplay holds the sink (mouth_sink), or a firm air
    window is open. The kiosk's onAir(): sink and gate, never the logged line
    (the User 2026-09-07: "it really should only pump when she is broadcasting").
    A speaking flag with no sink and no air window is a gate, not a mouth."""
    z = crissa or {}
    if "mouth_sink" in z:
        # the sink is the broadcast, 36 ms behind the audio; the air window
        # opens at the request, ~3 s early (the User 2026-09-07: "3s lead") —
        # when the payload carries the sink flag, it is the only vote
        return z.get("mouth_sink") is True
    t = time.time() if t is None else t
    try:
        a, u = z.get("air_at"), z.get("air_until")
        if z.get("air_firm") and a is not None and u is not None and float(a) <= t <= float(u):
            return True
    except (TypeError, ValueError):
        pass
    return False


def user_mark(name) -> str:
    """"the user" → "Bi": the desk mark, same rule as vo_sound.user_mark."""
    n = str(name or "").strip()
    return (n[0].upper() + n[1:2].lower()) if n else "U"


def star_alpha(r, R: float, r0: float, A: float):
    """Inverse-square light: A / (1 + (r/r0)²), feathered to zero at R.
    numpy array in, array out (the User 2026-09-07: "glowing star with inverse sq")."""
    import numpy as np
    f = np.clip(1.0 - (r / R) ** 4, 0.0, 1.0)
    return A / (1.0 + (r / r0) ** 2) * f


def star_geom(S: float, lvl: float):
    """Radius, core radius and peak alpha of the star for a mouth level 0..1:
    bigger and brighter on the beat. Kiosk: #crissaStar r 30–44, opacity .45–1."""
    lvl = max(0.0, min(1.0, float(lvl)))
    return (30.0 + 14.0 * lvl) * S, 9.0 * S, 0.45 + 0.55 * lvl


def render_star(S: float, lvl: float) -> Image.Image:
    """Her broadcast: a translucent RUST star over the core — bright centre,
    1/r² tail past the ear ring, beating with the mouth level. A separate
    globe on top of the dial, never a fill of the ring (the User 2026-09-07:
    "Just a separate red globe beats with her voice")."""
    import numpy as np
    R, r0, A = star_geom(S, lvl)
    box = int(2 * R) + 3
    box += (box + 1) % 2
    c = box // 2
    yy, xx = np.mgrid[0:box, 0:box]
    r = np.hypot(xx - c, yy - c).astype(np.float32)
    a = np.clip(star_alpha(r, R, r0, A) * 255.0, 0, 255).astype(np.uint8)
    rgba = np.zeros((box, box, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = RUST
    rgba[..., 3] = a
    return Image.fromarray(rgba, "RGBA")


def render_orb(S: float, color, A: float) -> Image.Image:
    """A soft orb from the centre dot: the star's inverse-square shape at
    r0 6·S dying at 22·S. AUTH is the pale sage one (kiosk #authOrb)."""
    import numpy as np
    R, r0 = 22.0 * S, 6.0 * S
    box = int(2 * R) + 3
    box += (box + 1) % 2
    c = box // 2
    yy, xx = np.mgrid[0:box, 0:box]
    r = np.hypot(xx - c, yy - c).astype(np.float32)
    a = np.clip(star_alpha(r, R, r0, A) * 255.0, 0, 255).astype(np.uint8)
    rgba = np.zeros((box, box, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = color
    rgba[..., 3] = a
    return Image.fromarray(rgba, "RGBA")


# K: SHE IS ON IT (the User 2026-10-01). The ear bridge writes <state home>/crissa-thinking.json (EAR_BRIDGE_THINKING)
# from the moment his turn is sent; the Crissa dot pulses while it says `on` and none of her lines has entered
# the log roll since `since` - so when the pulse stops her line is already on screen (gap 0 by construction,
# within one 0.2 s lobe tick). A file older than THINK_MAX_S never pulses. HOUSE_WALL_THINKING_PULSE=1.
THINK_PERIOD_S = 1.6   # one breath, a chat app's thinking dot
THINK_MAX_S = 180.0


def thinking_pulse(th: dict | None, line_ts: float, now: float) -> float | None:
    """The pulse phase 0..1, or None when the dot is still. `line_ts`: the newest Crissa line in the roll."""
    if not isinstance(th, dict) or th.get("on") is not True:
        return None
    try:
        since = float(th.get("since") or 0.0)
    except (TypeError, ValueError):
        return None
    if since <= 0 or not (0.0 <= now - since <= THINK_MAX_S):
        return None
    if float(line_ts or 0.0) >= since:
        return None  # her line is up: stop
    return ((now - since) % THINK_PERIOD_S) / THINK_PERIOD_S


def think_breath(phase: float) -> float:
    """0 -> 1 -> 0 over one period, eased (in at the bottom, out at the top) like a breathing spinner."""
    return 0.5 - 0.5 * math.cos(2.0 * math.pi * float(phase))


def render_think_glow(S: float, phase: float) -> Image.Image:
    """K's glow (the User 2026-10-01, "bigger and much more noticeable ... breathe in size and brightness the
    way a thinking spinner does ... spot it from across the room"): her white ink as a soft orb with the
    star's inverse-square light, its radius 16 -> 56 S (inside the 72 S ring) and its peak 0.55 -> 1.0 on one
    eased breath, over a core 4 -> 7 S. The dial behind it rests at DIAL_MUTE; this tile is full strength on purpose."""
    import numpy as np
    b = think_breath(phase)
    R, r0, A = (16.0 + 40.0 * b) * S, (5.0 + 10.0 * b) * S, 0.55 + 0.45 * b
    box = int(2 * R) + 3
    box += (box + 1) % 2
    c = box // 2
    yy, xx = np.mgrid[0:box, 0:box]
    r = np.hypot(xx - c, yy - c).astype(np.float32)
    a = np.clip(star_alpha(r, R, r0, A) * 255.0, 0, 255).astype(np.uint8)
    rgba = np.zeros((box, box, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = INK
    rgba[..., 3] = a
    im = Image.fromarray(rgba, "RGBA")
    core = (4.0 + 3.0 * b) * S
    ImageDraw.Draw(im).ellipse((c - core, c - core, c + core, c + core), fill=INK + (255,))
    return im


def draw_think_pulse(im, c: float, S: float, phase: float) -> None:
    glow = render_think_glow(S, phase)
    im.alpha_composite(glow, (int(round(c - glow.size[0] / 2)),) * 2)


def render_lobe(sound: dict | None, ear: dict | None, t: float, oracle_scale: float = 1.0,
                collapsed: bool = False, crissa: dict | None = None, level: float = 0.0,
                pulse: float | None = None):
    """The 36 lamps alone, at the beam's rate: a dial-sized transparent tile
    placed over the oracle (ANCHORS["oracle"]). None when dark or collapsed.
    ~1 ms to draw, ~0.5 ms to paste — the dots repaint at 5 Hz without the
    HUD (the User 2026-09-07: "No way to just repaint the dots?")."""
    od = ANCHORS.get("oracle")
    if not od or collapsed or oracle_scale < 0.3:
        return None
    fade = lobe_live(sound, ear, t)
    air = on_air(crissa, t)
    if fade <= 0.01 and not air and pulse is None:
        return None
    cx, cy, size = float(od[0]), float(od[1]), float(od[2]) * float(oracle_scale)
    S = size / 260.0
    r = 96 * S
    lamp = 3.4 * S
    box = int(math.ceil(2 * (r + lamp) + 4))
    im = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = box / 2
    if air:
        # #crissaStar — her broadcast is a separate RUST globe ON TOP of the
        # dial: bright core, 1/r² tail past the ear ring, beating with the
        # Conductor's mouth level (never a filled interior of the ring).
        # The lobe tile sits above the HUD, so the dial shows through.
        star = render_star(S, level)
        im.alpha_composite(star, (int(round(c - star.size[0] / 2)),) * 2)
    if pulse is not None:
        draw_think_pulse(im, c, S, pulse)
    if fade <= 0.01:
        return im, (int(round(cx - c)), int(round(cy - c)))
    az = float(sound["az_deg"])
    for i in range(36):
        ang = i * 10.0
        dd = abs((ang - az + 180) % 360 - 180)
        k = fade * math.exp(-0.5 * (dd / 10.0) ** 2)
        if k < 0.02:
            continue
        a = (ang - 90) * math.pi / 180
        lx, ly = c + math.cos(a) * r, c + math.sin(a) * r
        d.ellipse((lx - lamp, ly - lamp, lx + lamp, ly + lamp), fill=EMBER + (int(255 * min(1.0, k)),))
    return im, (int(round(cx - c)), int(round(cy - c)))


def doorish(house: dict | None) -> bool:
    """The page's rule for #doorhalo: house.room_event.kind matches
    /door|enter|leave|cross/."""
    kind = str(((house or {}).get("room_event") or {}).get("kind") or "")
    return any(k in kind for k in ("door", "enter", "leave", "cross"))


DOOR_GLOBE_R = 17.0     # #doorhalo r=17 in the dial's 260 box


def render_door_globe(house: dict | None, t: float, oracle_scale: float = 1.0,
                      collapsed: bool = False, hover: bool = False):
    """#doorhalo, as the page paints it: at the door hour on the r96 ring, a
    radial EMBER .55 → 0 over r=17, opacity 0.25 + |sin(0.9 t)| * 0.75 while
    the room event is door-ish, nothing otherwise (the User 2026-09-07: "match
    the glowing orb at the door of oracle dial"). A 5 Hz tile over the HUD;
    hover brightens it 1.9 like the rest of the dial. None when dark."""
    od = ANCHORS.get("oracle")
    if not od or collapsed or oracle_scale < 0.3 or not doorish(house) or np is None:
        return None
    sc = ((house or {}).get("room_event") or {}).get("seat_clock") or {}
    door_h = int(sc.get("door_hour") or 12) % 12 or 12
    cx, cy, size = float(od[0]), float(od[1]), float(od[2]) * float(oracle_scale)
    S = size / 260.0
    hx, hy = _pos(door_h, 96)
    gx, gy = cx + (hx - 130.0) * S, cy + (hy - 130.0) * S
    op = 0.25 + abs(math.sin(float(t) * 0.9)) * 0.75
    rr = DOOR_GLOBE_R * S
    box = int(2 * rr) + 3
    box += (box + 1) % 2
    c = box // 2
    yy, xx = np.mgrid[0:box, 0:box]
    r = np.hypot(xx - c, yy - c).astype(np.float32) / max(1e-6, rr)
    a = np.clip((1.0 - r) * 0.55 * op * 255.0, 0, 255).astype(np.uint8)
    col = EMBER                      # hover changes no light (the User 2026-09-07)
    rgba = np.zeros((box, box, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = col
    rgba[..., 3] = a
    return Image.fromarray(rgba, "RGBA"), (int(round(gx - c)), int(round(gy - c)))


def beam_of(sound: dict | None) -> float | None:
    """The published clock reading (sound.beam, from :8099 enrich_sound) when
    it is there; the same rule computed here otherwise."""
    sp = sound or {}
    b = sp.get("beam")
    if isinstance(b, (int, float)) and not isinstance(b, bool):
        return float(b)
    return clock_from_az(sp.get("az_deg"))


def render_beam_cell(sound: dict | None, g: Geom):
    """The Speaker row's beam number alone (ANCHORS["beam_cell"] from the
    HUD), GROUND-plated so it paints over the HUD's 1 Hz copy."""
    bc = ANCHORS.get("beam_cell")
    if not bc or not sound or sound.get("az_deg") is None:
        return None
    x, y, w, fsz = (float(v) for v in bc)
    f = font("mono", int(round(fsz)))
    im = Image.new("RGBA", (int(w) + 4, int(fsz * 1.3) + 2), GROUND + (255,))
    d = ImageDraw.Draw(im)
    beam = beam_of(sound)
    d.text((0, 0), f"{beam:.1f}" if beam is not None else "—", font=f, fill=INK)
    return im, (int(round(x)), int(round(y)))


GPU_LABELS = {"render": "Render", "normal": "Standard", "beast": "Beast", "off": "All OS Off"}


def gpu_label(gpu: dict | None) -> str:
    """The SERVICES word: the Control Panel's names, one law for both glasses
    (the wall printed 'normal' where Brave printed 'Standard', 2026-09-07).
    live = the settled glass; while a switch is pending: 'Standard → Render'."""
    g = gpu or {}
    live = str(g.get("glass") or "")
    pending = str(g.get("pending") or "")
    mode = str(g.get("mode") or "")
    name = lambda k: GPU_LABELS.get(k, k)   # noqa: E731
    label = str(g.get("label") or "") if live else ""
    if not label:
        label = name(live) if live else ""
    going = pending if (pending and pending != live) else ("" if live else mode)
    if going and going != live:
        return (label + " → " if label else "") + name(going)
    if live == "render" and label:
        # why Crissa is away (the User 2026-09-13: "I do not even know why")
        return label + " · Lila has the helm"
    return label or "—"


# ---- the Turbo pilot light ---------------------------------------------------
# TurboKrew lends Crissa's model to Vera and Steward for an hour. It is not a
# fifth occupancy — it is a pilot light on Standard (the User 2026-09-15: "the
# flame belongs on Standard as a pilot light, not as a fifth occupancy key").
# ONE CHANNEL: the flame and its mm:ss say it, so gpu_label never appends
# "· turbo".
# OCCUPANCY IS THE SoT. The flag leaving normal is what cancels the pins (in
# write_flag, before lane YAML is snapshotted); the flame is only the glass of
# that fact, so it goes the moment the live glass is not Standard. If the flag
# and turbo_krew.on disagree, nothing paints.
# The clock counts down from turbo_krew.until on the LOCAL clock, the way HELD
# counts from comms_hold_until on both glasses — never from the polled
# remaining_s, which arrives every 2 s and would stutter a 1 Hz repaint. It
# starts at 240:00 (four hours) and never reads 00:00: at zero there is nothing to paint.
TURBO_FULL_S = 14400.0


def turbo_left(gpu: dict | None, now: float) -> float | None:
    """Seconds of TurboKrew left, or None when the glass must not paint it."""
    if os.environ.get("HOUSE_WALL_TURBO_FLAME", "1").strip() == "0":
        return None
    g = gpu or {}
    tk = g.get("turbo_krew") or {}
    if not tk.get("on"):
        return None
    # SHARING, not merely armed (Grok 2026-09-15): leaving Standard PARKS
    # Vera/Steward back on crew and leaves the hour running, so `on` can be
    # true while nothing is shared — and coming back needs ops up for the pins
    # to resume. `sharing` is the YAML pin itself (vera == ops). The flame
    # claims a brain is being shared; it must not claim it on a parked lease.
    # ONLY an explicit False hides. The live house service serves `sharing: null` from
    # a turbo module loaded before the lease landed, and "key present" would
    # have read that null as "not sharing" and blanked a burning hour
    # (measured 2026-09-15). Missing or null: fall through to the occupancy law.
    if tk.get("sharing") is False:
        return None
    pending = str(g.get("pending") or "")
    if pending and pending != "normal":
        return None                       # already on its way out of Standard
    live = str(g.get("glass") or "") or str(g.get("mode") or "")
    if live != "normal":
        return None
    try:
        left = float(tk.get("until") or 0.0) - float(now)
    except (TypeError, ValueError):
        return None
    return left if left > 0 else None


def turbo_clock(left: float) -> str:
    """mm:ss, rounded up: 240:00 the second a 4h lease is armed, 00:01 the last one."""
    s = max(1, int(math.ceil(float(left))))
    return f"{s // 60:02d}:{s % 60:02d}"


# The kiosk's #turboKrew <svg class="flame">, in the SVG's own units
# (viewBox -3 0 71 100) so the two glasses draw the same curve from the same
# numbers. A tiny flame reads as a water drop unless the tip sits off the
# mass and the base silhouette is broken, so: a hooked tip, and one lick.
# Segments are (x0, y0) then (c1x, c1y, c2x, c2y, x, y) cubics, as in the path.
FLAME_VIEW_X, FLAME_VIEW_W, FLAME_VIEW_H = -3.0, 71.0, 100.0
FLAME_BODY = ((24.5, 0.0), (34, 8, 39.4, 19, 40.8, 33), (42.2, 45, 65.3, 50, 62.6, 72),
              (60.5, 90, 49, 99, 35.4, 99), (21.8, 99, 11.6, 89, 11.6, 71),
              (11.6, 51, 24.5, 43, 21.8, 23), (20.4, 13, 20.4, 7, 24.5, 0))
FLAME_LICK = ((8.8, 33.0), (15.6, 50, 15, 66, 12.9, 86), (0, 76, -2.7, 52, 8.8, 33))
FLAME_CORE = ((31.3, 44.0), (38.1, 54, 39.4, 62, 42.2, 70), (47.6, 84, 42.2, 93, 35.4, 93),
              (27.2, 93, 23.1, 84, 25.8, 72), (27.9, 63, 31.3, 56, 31.3, 44))
# the page's `.flame .core { fill: var(--ember-2); opacity: .55 }` over the
# ember body, resolved: ember + (ember-2 - ember) * .55
FLAME_CORE_INK = tuple(int(round(a + (b - a) * 0.55)) for a, b in zip(EMBER, EMBER2))
FLAME_SS = 6                              # drawn 6x and LANCZOS down, like the glyph atlas


def _flame_points(segs, k: float, dx: float, steps: int = 20):
    pts = [(segs[0][0] * k + dx, segs[0][1] * k)]
    for c1x, c1y, c2x, c2y, x3, y3 in segs[1:]:
        x0, y0 = (pts[-1][0] - dx) / k, pts[-1][1] / k
        for i in range(1, steps + 1):
            t_ = i / steps
            m = 1 - t_
            a, b, c, e = m ** 3, 3 * m * m * t_, 3 * m * t_ * t_, t_ ** 3
            pts.append(((a * x0 + b * c1x + c * c2x + e * x3) * k + dx,
                        (a * y0 + b * c1y + c * c2y + e * y3) * k))
    return pts


@lru_cache(maxsize=8)
def _flame(px: int) -> Image.Image:
    """The pilot light, `px` tall. Cached: the HUD repaints every second."""
    h = max(6, int(px))
    k = h / FLAME_VIEW_H
    w = max(4, int(round(FLAME_VIEW_W * k)))
    im = Image.new("RGBA", (w * FLAME_SS, h * FLAME_SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ss = k * FLAME_SS
    dx = -FLAME_VIEW_X * ss
    for seg in (FLAME_BODY, FLAME_LICK):
        d.polygon(_flame_points(seg, ss, dx), fill=EMBER + (255,))
    d.polygon(_flame_points(FLAME_CORE, ss, dx), fill=FLAME_CORE_INK + (255,))
    return im.resize((w, h), Image.Resampling.LANCZOS)


def camera_label(speaker: dict | None) -> str:
    """The pip's label: a small lowercase "live" (it blinks on the glass, the User
    2026-09-06: "just a blinking 'Live' but in smaller font and not in Caps"),
    then " · <name> <sim>" from /v1/speaker when the visor has a name.
    No name (S1/S2 scratch, or nobody) reads plain "live"."""
    sp = speaker or {}
    name = str(sp.get("name") or sp.get("locked") or "").strip()
    if not name or name.lower().startswith("s") and name[1:].isdigit():
        return "live"
    sim = sp.get("sim")
    try:
        return "live · %s %.2f" % (name, float(sim)) if sim is not None else "live · " + name
    except (TypeError, ValueError):
        return "live · " + name


def user_authed(house: dict | None, presence: dict | None) -> bool:
    """User on the wall is presence's user_auth — same hand the camera obeys.
    The house echo used to OR sage and light User while presence said Guest
    (the User 2026-09-09). Sage holds AUTH on :8191; this does not mint a second."""
    return camera_authed(presence)


# HIS FACE, NAMED AND ALONE, IS NOT "GUEST" (the User 2026-10-12, .101 at rest: the fixed camera named him - faceid
# 0.75, locked, source fixed_look - while presence said user_auth false / user_absent, so this HUD read "Standard Guest").
# The house's rule, as ported in the kit's bridge (rail-voice user_mode.evaluate_turn): a conversation's A is the live
# User face lock; this-turn voice is B; with HOUSE_ORACLE_FACE_WINS a fresh live face of 0.60 or more decides the turn
# alone. presence's user_auth is the AUTH ledger (Law 1: minted by a FaceFinder look), and a fixed camera has no
# FaceFinder, so the ledger stays empty at rest while A is there. The HUD therefore says what the house's HUD says when
# his face holds A - User - and that it is the face alone: "User (face; next turn full)". Only a pack's presence
# publishes a fixed_look block; the house's HUD reads user_auth exactly as before. Kill: HOUSE_HUD_FACE_USER=0.
FACE_USER_MAX_AGE_S = 6.0          # the bridge's freshness for a fixed camera's face (HOUSE_FIXED_FACE_AGE_S)


def face_named_alone(presence: dict | None) -> dict | None:
    """{"sim", "age_s"} when a fixed camera's live look names the User, alone and fresh, else None."""
    if str(os.environ.get("HOUSE_HUD_FACE_USER", "1")).strip().lower() in ("0", "off", "false", "no"):
        return None
    p = presence or {}
    f = p.get("face") if isinstance(p.get("face"), dict) else (p.get("faceid") if isinstance(p.get("faceid"), dict)
                                                              else {})
    if str(f.get("source") or "") != "fixed_look" or not f.get("fixed"):
        return None
    uid = str(p.get("user_id") or (p.get("user") or {}).get("user_id") or "").strip().lower()
    if not uid or str(f.get("locked") or "").strip().lower() != uid or f.get("n_faces") not in (1, None):
        return None
    try:
        age = float(f.get("age_s"))
    except (TypeError, ValueError):
        return None
    if age > FACE_USER_MAX_AGE_S:
        return None
    sim = next((x.get("sim") for x in (f.get("faces") or []) if isinstance(x, dict)
                and str(x.get("name") or "").strip().lower() == uid), None)
    return {"sim": sim, "age_s": round(age, 2)}


def face_decides() -> bool:
    """The bridge's "the face decides" rule (HOUSE_ORACLE_FACE_WINS): this process's environment, else the ear's own
    settings file (<state home>/ear.json, bridge.oracle_face_wins), else on (a pack's default)."""
    v = os.environ.get("HOUSE_ORACLE_FACE_WINS")
    if v is None:
        try:
            import crissa_home
            d = json.loads((crissa_home.HOME / "ear.json").read_text(encoding="utf-8"))
            v = ((d or {}).get("bridge") or {}).get("oracle_face_wins")
        except Exception:  # noqa: BLE001
            v = None
    return str("1" if v is None else v).strip().lower() in ("1", "on", "true", "yes")


def who_label(house: dict | None, presence: dict | None) -> tuple[str, bool]:
    """(the HUD's word for who is here, lit as the User?): "User" on presence's AUTH (the house's rule, unchanged);
    when only his named face holds (face_named_alone), "User (face; next turn full)" - or, with the face rule off,
    "User (face; voice decides)"; else "Guest"."""
    if user_authed(house, presence):
        return "User", True
    if face_named_alone(presence) is not None:
        return ("User (face; next turn full)" if face_decides() else "User (face; voice decides)"), True
    return "Guest", False


def camera_authed(presence: dict | None) -> bool:
    """The joystick's AUTH: presence's own user_auth and nothing else. The
    camera (presence.camera_aim) refuses a nudge unless ITS user_auth is
    True; the house record's Secure hold lit the pad while presence said
    "absent", so every press was refused 403 (the User 2026-09-09: "no movement
    at all now"). Light the pad from the hand that will answer it."""
    p = presence or {}
    u = p.get("user") or {}
    return p.get("user_auth") is True or u.get("auth") is True


def secure_state(house: dict | None, presence: dict | None) -> str:
    """'sage' (Secure) | 'amber' (not Secure) | 'rust' (Faulty). presence first."""
    u = ((presence or {}).get("user") or {})
    if u.get("secure") is True:
        return "sage"
    for src in (presence or {}, house or {}):
        c = str(src.get("visit_lamp") or (src.get("visit_lamp_info") or {}).get("color") or "").lower()
        if c in ("sage", "rust", "amber"):
            return c
    return "amber"


def secure_offer(house: dict | None, presence: dict | None) -> dict | None:
    """Presence's answer to "may Secure be offered on this camera?" (round 17: modules/presence fixed_cam.secure_offer,
    in the lamp's info), presence first: {"ok", "kind", "why", "about"}, or None where presence says nothing (the
    house, the switch off, an older presence) - then the Secure dot is drawn and clicked as before."""
    for src in (presence or {}, house or {}, (house or {}).get("presence") or {}):
        info = src.get("visit_lamp_info") if isinstance(src, dict) else None
        off = info.get("secure_offer") if isinstance(info, dict) else None
        if isinstance(off, dict) and "ok" in off:
            return off
    return None


# ---- mode / secure ------------------------------------------------------------
MOOD = {
    "away": ("AWAY", INK3),
    "speaking": ("SPEAKING", EMBER2),
    "alert": ("ALERT", RUST),
    "attending": ("LISTENING", SAGE),
    "paused": ("PAUSED", EMBER),
}


def mood_of(house: dict):
    voice = house.get("voice") or {}
    scr = house.get("screen") or {}
    hear = house.get("hear") or {}
    von = bool(voice.get("voice_on"))
    if not von:
        return "alert" if scr.get("mood") == "alert" else "away"
    if hear.get("speaking") or scr.get("mood") == "speaking":
        return "speaking"
    if scr.get("mood") == "alert":
        return "alert"
    if hear.get("session_open"):
        return "attending"
    return "paused"


# ---- layers ------------------------------------------------------------------
def _open(b):
    if not b:
        return None
    try:
        return Image.open(io.BytesIO(b)).convert("RGB")
    except Exception:
        return None


def hole_zoom_of(house: dict | None) -> float | None:
    """screen.crissa_hole -> the fullscreen hole crop at screen.fov_zoom_crissa (default .5)."""
    scr = (house or {}).get("screen") or {}
    if not scr.get("crissa_hole"):
        return None
    z = scr.get("fov_zoom_crissa")
    return float(z) if isinstance(z, (int, float)) and z > 0 else 0.5


def render_background(src: Image.Image | None, g: Geom, crissa_own: bool, t: float,
                      hole_zoom: float | None = None, brightness: float | None = None,
                      shift: tuple[float, float] | None = None, mouth=None, frame=None) -> Image.Image:
    """Ground + #ascii (chan-live) + vignette. brightness is #ascii opacity up to
    1.0; past that it is INK on the hot end of the palette (see _ember_luts).
    shift None is today's crop; mouth None is today's exposure (see _luma_grid)."""
    cov = render_coverage(src, g, crissa_own, t, hole_zoom, shift, mouth=mouth, frame=frame)
    # coverage -> ember through one palette: the three LUT passes + merge were
    # 4.2 ms on 3.7 MP, the palette convert is 2.8 (2026-09-08)
    cov.putpalette(palette_for(brightness))
    return cov.convert("RGB")


def render_coverage(src: Image.Image | None, g: Geom, crissa_own: bool, t: float,
                    hole_zoom: float | None = None, shift: tuple[float, float] | None = None,
                    mouth=None, frame=None) -> Image.Image:
    """render_background before its last step: the L coverage it paints through palette_for(brightness), glow memo
    and all. The film child sends this (a third of the RGB's bytes) and the wall paints it (2026-10-11)."""
    from PIL import ImageChops
    return ImageChops.multiply(ascii_mask(src, g, crissa_own, t, hole_zoom, shift, mouth=mouth, frame=frame),
                               _vignette_inv(g.W, g.H))


def palette_for(brightness: float | None) -> bytes:
    """The ember palette (768 bytes) a brightness dial paints the coverage with."""
    op = ASCII_OPACITY if brightness is None else max(0.15, min(1.0, float(brightness)))
    # past opaque the dial buys INK, not opacity: the hot end walks EMBER -> INK
    # (the User 2026-09-22, wall_prefs.ink_gain). Rounded so the palette cache has a
    # dial's worth of keys and not a float's.
    ink = round(wall_prefs.ink_gain(brightness), 3)
    return _ember_palette(op, ink)


def coverage_fresh(src: Image.Image | None, g: Geom, crissa_own: bool, t: float, hole_zoom: float | None = None,
                   shift: tuple[float, float] | None = None, mouth=None, frame=None) -> Image.Image:
    """render_background's coverage (L, before the palette) with the glow made for THIS frame: no memo, no
    reuse, so a pure function of the frame and the dials. render_background with _GLOW cleared first is this
    through palette_for(brightness)."""
    from PIL import ImageChops
    if np is None:
        im = Image.new("L", (g.W, g.H), 0)
    else:
        im = mask_from_idx(ascii_idx(src, g, crissa_own, t, hole_zoom, shift, mouth=mouth, frame=frame), g)
        if crissa_own or src is not None:
            im = ImageChops.screen(im, glow_from_small(glow_small(im, g), g))
    return ImageChops.multiply(im, _vignette_inv(g.W, g.H))


def background_fresh(src: Image.Image | None, g: Geom, crissa_own: bool, t: float, hole_zoom: float | None = None,
                     brightness: float | None = None, shift: tuple[float, float] | None = None,
                     mouth=None, frame=None) -> Image.Image:
    """The reference picture: render_background with the glow made for this frame (see coverage_fresh)."""
    cov = coverage_fresh(src, g, crissa_own, t, hole_zoom, shift, mouth=mouth, frame=frame)
    cov.putpalette(palette_for(brightness))
    return cov.convert("RGB")


@lru_cache(maxsize=16)
def _ember_palette(opacity: float, ink: float = 0.0) -> bytes:
    r, gg, b = _ember_luts(opacity, ink)
    return bytes(c for i in range(256) for c in (r[i], gg[i], b[i]))


# The opened pop's height at ft_size 1.0, in glass px: the page's 570 (its
# `#ftPop.big{height:570px}` — 2x the 285 px source crop, "the edge of
# sharp"); the dial multiplies it on both glasses, one shared number.
FT_BIG_H = 570.0


def ft_box(g: Geom, p: dict | None = None) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the FaceTime pop on the glass, from the prefs.
    FT = her face, the 4:6 FT_SRC crop at ft_w. ROOM = the whole 736x400
    frame, 16:9, as wide as the camera pip (kiosk ftZoomSet: "Conference is
    the whole room"). the User 2026-09-06: "ROOM view not working on PIP" — the
    chips flipped a pref nothing read. ft_size scales the OPENED box only;
    ft_dx / ft_dy offset it (the drag)."""
    p = p or {}
    cb = p.get("ft_card_box")
    if isinstance(cb, (list, tuple)) and len(cb) == 4:
        return ft_in_box(cb, str(p.get("ft_mode") or "ft"),     # round 34: beside the card, while it is up
                         right=bool(p.get("ft_card_right")))        # round 37: against the page's side of the box
    size = float(p.get("ft_size") or 1.0)
    k = p.get("ft_k")
    if k is not None and 0.0 < float(k) < 1.0:
        # mid-grow (the User 2026-09-08: "duplicate that open and close action
        # for the PIP and Camera"): the box between its corner and its
        # opened place, on the page's .32 curve; the wall steps k
        k = float(k)
        q = dict(p, ft_k=None)
        a = ft_box(g, dict(q, ft_big=False))
        b = ft_box(g, dict(q, ft_big=True))
        return tuple(int(round(a[i] + (b[i] - a[i]) * k)) for i in range(4))
    if p.get("ft_big"):
        # enlarged on click: the page's rule (voiceonly-home.html ftStepApply):
        # 570 px tall × the ft_size dial, ROOM at 16:9, FT at 4:6, centred.
        # The dial is THE OPENED WINDOW'S (the User 2026-08-30: "the +/- will be
        # for tile size when up, not the thumbnail"; 2026-09-10: "the
        # wallpaper one changes the size of the icon minimized. We want the
        # popup window to adjust"). Clamped to the glass so the top of the
        # dial never runs the box off the edges. ft_dx / ft_dy: the User's
        # drag, glass px from the centred place (2026-09-10: "let us drag it
        # ... tucked up top corner"); the box stays on the glass.
        h = FT_BIG_H * size
        w = h * 16 / 9 if str(p.get("ft_mode") or "ft") == "room" else h * 4 / 6
        fit = min(1.0, (g.W - 2 * g.pip_edge) / w, (g.H - 2 * g.pip_edge) / h)
        w = int(round(w * fit)); h = int(round(h * fit))
        x = int((g.W - w) / 2) + int(round(float(p.get("ft_dx") or 0.0)))
        y = int((g.H - h) / 2) + int(round(float(p.get("ft_dy") or 0.0)))
        x = max(0, min(int(g.W) - w, x))
        y = max(0, min(int(g.H) - h, y))
        # a page is up (the User 2026-10-09): the opened window slides left just clear of the letter, its size kept
        page_left = p.get("page_left")
        if page_left is not None:
            x = max(0, min(x, int(page_left - g.pip_edge - w)))
        return x, y, w, h
    if str(p.get("ft_mode") or "ft") == "room":
        # ROOM is the camera box's own dims, whatever the FT size dial says
        # (the User 2026-09-06: "shrink the ROOM view to match Camera box dims")
        w = int(round(g.pip_w))
        h = int(round(g.pip_h))
    else:
        # FT: her face at 4:6, about the ROOM square's area, a tiny bit less
        # (the User 2026-09-06: "similar or same area as the Room square. Tiny bit
        # smaller when minimized"). The size dial does NOT touch the corner
        # (2026-09-10: "the resize of max window, not icon") — like the page,
        # the thumbnail is sized to pair with the camera box opposite it.
        area = 0.9 * g.pip_w * g.pip_h
        w = int(round((area / 1.5) ** 0.5))
        h = int(round(w * 6 / 4))
    tile = p.get("ft_tile")
    if isinstance(tile, (list, tuple)) and len(tile) == 2:
        # dragged (the User 2026-10-09): fractions of the free travel, so the tile is always whole on the glass
        fx, fy = (min(1.0, max(0.0, float(v))) for v in tile)
        return int(round(fx * (g.W - w))), int(round(fy * (g.H - h))), w, h
    right = g.pip_edge + max(0.0, (g.pip_w - w) / 2)
    x = int(round(g.W - right - w))
    y = int(round(g.H - g.pip_edge - h))
    return x, y, w, h


def ft_in_box(box, mode: str = "ft", right: bool = False) -> tuple[int, int, int, int]:
    """(x, y, w, h): her tile at its own shape (ROOM 16:9, FT 4:6) as large as fits in `box` (glass px), centred, or
    against the box's right edge (`right`, round 37: tucked against the page, her PICTURE's edge at the page, not the
    box's)."""
    bx, by, bw, bh = (float(v) for v in box)
    ar = 16 / 9 if mode == "room" else 4 / 6
    w, h = (bw, bw / ar) if bw / ar <= bh else (bh * ar, bh)
    x = bx + bw - w if right else bx + (bw - w) / 2
    return int(round(x)), int(round(by + (bh - h) / 2)), int(round(w)), int(round(h))


def ft_tile_from_xy(g: Geom, x: float, y: float, p: dict | None = None) -> tuple[float, float]:
    """The small tile's top-left (glass px) as the fractions ft_box reads."""
    _x, _y, w, h = ft_box(g, dict(p or {}, ft_big=False, ft_k=None, ft_tile=None, ft_card_box=None))
    fx = float(x) / max(1.0, g.W - w)
    fy = float(y) / max(1.0, g.H - h)
    return (round(min(1.0, max(0.0, fx)), 4), round(min(1.0, max(0.0, fy)), 4))


def ft_src_box(w: int, h: int, env=None) -> tuple[int, int, int, int]:
    """FT_SRC in THIS picture's pixels (the User 2026-10-29, the first-time walk of 1.0.1: the FT tile showed "a zoomed
    in ... door handle ... empty chair"): FT_SRC is in the 736x400 clip's pixels, and Crissa desk v2 paints at 1280x720,
    so the fixed box landed on the door. The box keeps its 4:6 shape and its place as fractions of the picture, scaled
    by the height; at 736x400 it is FT_SRC exactly. Kill HOUSE_FT_SRC_SCALE=0. Returns (x0, y0, x1, y1)."""
    fx, fy, fw, fh = FT_SRC["x"], FT_SRC["y"], FT_SRC["w"], FT_SRC["h"]
    e = os.environ if env is None else env
    if (w, h) == (CLIP_W, CLIP_H) or str(e.get("HOUSE_FT_SRC_SCALE", "1")).strip().lower() in ("0", "off", "false", "no"):
        return fx, fy, min(w, fx + fw), min(h, fy + fh)
    k = h / float(CLIP_H)
    cx, bw, bh = (fx + fw / 2.0) / CLIP_W * w, fw * k, fh * k
    x0 = int(round(max(0.0, min(w - bw, cx - bw / 2.0))))
    y0 = int(round(fy * k))
    return x0, y0, min(w, x0 + int(round(bw))), min(h, y0 + int(round(bh)))


def render_ft(crissa: Image.Image, g: Geom, p: dict | None = None) -> tuple[Image.Image, tuple[int, int]]:
    """The FaceTime pop, bottom-right: her face (FT) or the room (ROOM), blurred
    when Blur is lit (#ftPop.blur #ftCan: the sepia grade + blur(7px)).
    Returns (RGBA image with shadow margin, (x, y) to paste it at)."""
    u = g.u
    p = p or {}
    bx, by, ftw, fth = ft_box(g, p)
    if str(p.get("ft_mode") or "ft") == "room":
        crop = crissa
    else:
        crop = crissa.crop(ft_src_box(crissa.width, crissa.height))
    crop = crop.resize((ftw, fth), Image.Resampling.LANCZOS)
    if not p.get("ft_big"):          # kiosk #pipwrap.big: filter none
        crop = _ft_filter(crop)
    if p.get("blur"):
        crop = crop.filter(ImageFilter.GaussianBlur(7))
    crop = crop.convert("RGBA")
    if p.get("ft_fade") and not p.get("ft_big"):
        # the third veil (the User 2026-09-09: Blur → Fade → off): the picture
        # at the down camera box's transparency; the pop's frame stays
        crop.putalpha(crop.getchannel("A").point(lambda a_, k=FT_FADE_ALPHA: int(a_ * k)))
    M = 60
    fade_on = bool(p.get("ft_fade")) and not p.get("ft_big")
    frame_k = FT_FADE_FRAME if fade_on else 1.0
    out = _ft_shadow(ftw, fth, M, int(0.45 * u), int(round(168 * frame_k))).copy()
    _rounded_paste(out, crop, (M, M), int(0.45 * u))
    # the hairline: pale white in every veil, like the camera box on the other
    # side (the User 2026-09-09: "pale white border to match camera box … for all
    # modes"); the chrome lays the ember one over it under the pointer. In
    # Fade the frame fades with the picture (FT_FADE_FRAME).
    line = FT_REST_LINE[:3] + (int(round(FT_REST_LINE[3] * frame_k)),)
    ImageDraw.Draw(out).rounded_rectangle((M, M, M + ftw - 1, M + fth - 1), radius=int(0.45 * u), outline=line)
    return out, (bx - M, by - M)


def _ft_filter(im: Image.Image) -> Image.Image:
    """#ftCan at rest: grayscale(1) brightness(.86) contrast(1.04) — black and
    white in the corner, colour only when opened (the User 2026-09-09: "make the
    Normal mode like it was, just black and white vs color. Color screen when
    it maximizes only"). The sepia grade of 2026-08 is gone."""
    from PIL import ImageEnhance
    out = im.convert("L").convert("RGB")
    out = ImageEnhance.Brightness(out).enhance(0.86)
    return ImageEnhance.Contrast(out).enhance(1.04)


@lru_cache(maxsize=4)
def _ft_shadow(ftw, fth, M, radius, alpha: int = 168):
    sh = Image.new("RGBA", (ftw + 2 * M, fth + 2 * M), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((M, M, M + ftw, M + fth), radius=radius, fill=(0, 0, 0, alpha))
    return sh.filter(ImageFilter.GaussianBlur(23))



def _skin_changing() -> str:
    """"Changing to <name>..." while a Choose skin pick restarts the player (CRISSA_SKIN_MENU), else ""."""
    try:
        import crissa_avatar
        return crissa_avatar.changing_text()
    except Exception:  # noqa: BLE001 - the notice is never a reason the Wall fails to paint
        return ""

def render_hud(house: dict, presence: dict, cam: Image.Image | None, g: Geom,
               t: float, crissa_her_live: bool, stale: bool = False,
               oracle_scale: float = 1.0, vitals_folded: bool = False,
               pip_bar: bool = False, mode_bar: bool = False,
               speaker: dict | None = None, ft_prefs: dict | None = None,
               big: str | None = None, ptr: tuple | None = None,
               big_k: float = 1.0) -> Image.Image:
    """Everything that reads /v1/house: band, vitals, roll, dial, camera PiP.
    big_k: how far the enlarged corner (big) has grown, 0..1 — the wall steps
    it on the page's .32 curve (the User 2026-09-08); 1 is the opened box.
    NOTHING HERE RIDES THE IDLE (the User 2026-09-09: "if they are open, of
    course they stay. If one closes them, the collapse bar DOES fade"): the
    HUD is information and stays; only the chrome's affordances and the bar /
    dot of a corner the User collapsed go with the fade.
    Transparent RGBA; refresh every second (the clock) or when house changes.
    oracle_scale: 1 = full clock, 0.05 = collapsed to #oracleDot (kiosk .clockwrap.dot)."""
    W, H, u = g.W, g.H, g.u
    wall = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(wall)
    scr = house.get("screen") or {}
    facts = house.get("facts") or {}
    voice = house.get("voice") or {}
    wx = house.get("weather") or {}
    lamp = house.get("visit_lamp_info") or {}

    # .band — time / date / headline / note (left), mode chip + Secure dot (right)
    band_top = 2.1 * u
    x0 = g.page_left
    lt = time.localtime(t)
    hh = lt.tm_hour % 12 or 12
    tstr = f"{hh}:{lt.tm_min:02d}"
    f_time = font("light", 3.05 * u)
    d.text((x0, band_top), tstr, font=f_time, fill=INK)
    tw = d.textlength(tstr, font=f_time)
    f_sec = font("mono", 3.05 * u * 0.42)
    _text(d, (x0 + tw + 0.35 * f_sec.size, band_top + 3.05 * u * 0.20), f"{lt.tm_sec:02d}", f_sec, INK3, 0.14)
    f_mer = font("mono", 3.05 * u * 0.32)
    _text(d, (x0 + tw + f_sec.size * 2.2, band_top + 3.05 * u * 0.12), "AM" if lt.tm_hour < 12 else "PM", f_mer, INK2, 0.2)
    y = band_top + 3.05 * u * 0.94 + 0.35 * u
    f_date = font("mono", 0.72 * u)
    _text(d, (x0, y), time.strftime("%A · %B ", lt).upper() + str(lt.tm_mday), f_date, INK3, 0.16)
    y += f_date.size * 1.3 + 0.5 * u
    f_head = font("serif", 1.42 * u)
    head = (scr.get("headline") or "").strip()
    if head:
        maxw = g.rail_w - 2.727 * W / 100 * 2
        lines = _wrap_balanced(d, head, f_head, maxw)
        for ln in lines[:3]:
            d.text((x0, y), ln, font=f_head, fill=INK)
            y += f_head.size * 1.22
    note = (scr.get("note") or "").strip()
    if note:
        y += 0.3 * u
        f_note = font("serif-italic", 0.86 * u)
        d.text((x0, y), note, font=f_note, fill=INK2)
        y += f_note.size * 1.3

    mk = mood_of(house)
    label, dot = MOOD[mk]
    f_chip = font("mono", 0.6 * u)
    f_mode = font("mono", 0.62 * u)
    # HELD mm:ss counts the ear's hold window down (the User 2026-09-09); the
    # chip column is as wide as the wider label, no dot allowance any more.
    hear_h = house.get("hear") or {}
    hold_label = "HOLD"
    if hear_h.get("comms_hold"):
        try:
            left = max(0.0, float(hear_h.get("comms_hold_until") or 0.0) - time.time())
        except (TypeError, ValueError):
            left = 0.0
        hold_label = "HELD %02d:%02d" % (int(left // 60), int(left % 60)) if left else "HELD"

    def _lbl_w(t_):
        return sum(d.textlength(c_, font=f_mode) + f_mode.size * 0.2 for c_ in t_) - f_mode.size * 0.2

    chip_w = max(_lbl_w(label), _lbl_w(hold_label)) + f_mode.size * (0.7 * 2)
    chip_h = f_mode.size * (1.2 + 0.7)
    cx1 = W - 2.4 * u
    cx0 = cx1 - chip_w
    authed = user_authed(house, presence)
    hold_on = bool((house.get("hear") or {}).get("comms_hold"))
    hold_h = f_mode.size * (1.2 + 0.7)
    if not mode_bar:
        border = {"attending": (0x45, 0x63, 0x4F), "paused": EMBER_DEEP}.get(mk, EMBER_DEEP)
        tint = {"attending": (0x1A, 0x22, 0x22), "paused": (0x18, 0x1A, 0x1E)}.get(mk, GROUND2)
        if authed:   # .modechip.auth — sage border/tint, the AUTH fact
            border, tint = (0x45, 0x63, 0x4F), (0x1A, 0x22, 0x22)
        d.rounded_rectangle((cx0, band_top, cx1, band_top + chip_h), radius=2, fill=tint, outline=border)
        # No dot on the mode chip or HOLD (the User 2026-09-09 "drop the dots on
        # all four"): text starts at the same left pad on both, both glasses.
        _text(d, (cx0 + f_mode.size * 0.7, band_top + chip_h / 2 - f_mode.size * 0.6), label, f_mode, dot, 0.2)
        # Comms hold: ember = hour clock armed. Sage is AUTH/Secure/LISTENING.
        hy0 = band_top + chip_h + 0.18 * u
        hy1 = hy0 + hold_h
        hold_ink = EMBER if hold_on else INK3
        hold_border = EMBER if hold_on else EMBER_DEEP
        hold_tint = (0x1C, 0x18, 0x12) if hold_on else (0x18, 0x1A, 0x1E)
        d.rounded_rectangle((cx0, hy0, cx1, hy1), radius=2, fill=hold_tint, outline=hold_border)
        _text(d, (cx0 + f_mode.size * 0.7, hy0 + hold_h / 2 - f_mode.size * 0.6), hold_label, f_mode, hold_ink, 0.2)  # "HOLD" / "HELD mm:ss"
        HOTSPOTS["comms_hold"] = (cx0, hy0, cx1, hy1)
        lamp_c = secure_state(house, presence)
        dot_c = SAGE if lamp_c == "sage" else (RUST if lamp_c == "rust" else EMBER)
        vr = f_mode.size * 0.21
        vx, vy = (cx0 + cx1) / 2, hy1 + 0.28 * u + f_mode.size * 0.35 + vr
        _so = secure_offer(house, presence)
        if _so is not None and _so.get("hide") and lamp_c != "sage":
            # ONE TOGGLE (round 17 follow-ups, the User 2026-10-13): a camera that does not turn has no Secure at all -
            # no dot, not greyed; the Security toggle's position stands in its place and a click opens it
            _sec = (house or {}).get("security") if isinstance((house or {}).get("security"), dict) else {}
            _word = str(_sec.get("mode") or "").replace("_", "-") or "security"
            _text(d, (vx - vr * 2.4, vy - f_mode.size * 0.55), _word.upper()[:9], f_mode, INK3, 0.2)
        elif _so is not None and _so.get("ok") is False and lamp_c != "sage":
            # SECURE GREYED (round 17): a camera that cannot turn to the door is offered no Secure - a hollow grey
            # ring; a click says why (lib/house_wall_ascii). Presence's word; never on the house.
            d.ellipse((vx - vr, vy - vr, vx + vr, vy + vr), outline=INK3, width=max(1, int(vr * 0.45)))
        else:
            d.ellipse((vx - vr, vy - vr, vx + vr, vy + vr), fill=dot_c)
        HOTSPOTS["mode"] = (cx0, band_top, cx1, band_top + chip_h)
        HOTSPOTS["secure"] = (cx0, hy1, cx1, vy + vr * 3)
        ANCHORS["mode"] = [float(cx0), float(band_top), float(chip_w), float((vy + vr * 3) - band_top)]
    else:
        ANCHORS["mode"] = [float(cx0), float(band_top), float(chip_w), float(chip_h + 1.2 * u)]
        HOTSPOTS.pop("mode", None)
        HOTSPOTS.pop("secure", None)
        HOTSPOTS.pop("comms_hold", None)
    lx = cx0

    # .vitals rows
    y += 1.1 * u
    f_k = font("mono", 0.6 * u)
    f_v = font("mono", 0.82 * u)
    kcol_w = 4.6 * u * 0.6 + 1.0 * u
    rows_gap = 0.62 * u

    def row(label_, draw_value):
        nonlocal y
        _text(d, (x0, y + (f_v.size - f_k.size) * 0.6), label_.upper(), f_k, INK3, 0.2)
        h_used = draw_value(x0 + kcol_w, y)
        y += max(f_v.size * 1.25, h_used) + rows_gap

    def v_out(x, yy):
        d.text((x, yy), wx.get("text") or "—", font=f_v, fill=INK)
        return f_v.size
    row("Out", v_out)

    if not vitals_folded:
        def v_spk(x, yy):
            spk = (house.get("speaker") or (presence.get("speaker") or {}))
            who = ((house.get("auth") or {}).get("speaker")) or "None"
            if speaker_names_her() and she_is_speaking(house):
                who = crissa_home.her_name()       # while she talks the row names her (2026-10-12)
            d.text((x, yy), str(who), font=f_v, fill=INK)
            xx = x + max(d.textlength(str(who), font=f_v), 4 * f_v.size) + 1.0 * u
            beam = spk.get("beam")
            beam_s = f"{float(beam):.1f}" if isinstance(beam, (int, float)) else "—"
            # the fast layer repaints just this cell at the beam's rate
            ANCHORS["beam_cell"] = [float(xx + d.textlength("beam ", font=f_v)), float(yy),
                                    float(d.textlength("88.8", font=f_v)), float(f_v.size)]
            # cam is the real face similarity, SAGE at the 0.60 bar and RUST below
            # (the User 2026-10-01). A live face, else the score from this look,
            # fading across FACE_FLASH_S (the User 2026-10-09). Same reading as the visor.
            cam_txt, cam_state, cam_a, cam_score = _face_cell_held(
                speaker or {}, None, presence, t, sign=False)
            cam_ink = SAGE if _face_pass(cam_score) else (INK2 if (none_grey_on() and cam_score is None) else RUST)
            if cam_state == "flash":
                cam_ink = cam_ink + (max(1, int(round(255 * cam_a))),)
            note_ok("voice", spk.get("voice"), spk.get("voice_green"))
            note_ok("stack", spk.get("stack"), spk.get("stack_green"))
            vv, va = held_score("voice", spk.get("voice"), t)       # A SCORE STAYS UP, THEN FADES (round 21)
            sv, sa = held_score("stack", spk.get("stack"), t)
            parts = [("beam ", INK2), (beam_s, INK), (" · cam ", INK2),
                     (cam_txt, cam_ink),
                     (" · voice ", INK2), (_score_txt(vv, False),
                                           INK2 if (none_grey_on() and _no_reading(vv))
                                           else _held_ink(SAGE if (none_grey_on() and held_ok("voice")) else EMBER, va)),
                     (" · stack ", INK2), (_score_txt(sv, False),
                                           INK2 if (none_grey_on() and _no_reading(sv))
                                           else _held_ink(SAGE if (none_grey_on() and held_ok("stack")) else EMBER, sa))]
            for txt, col in parts:
                d.text((xx, yy), txt, font=f_v, fill=col)
                xx += d.textlength(txt, font=f_v)
            return f_v.size
        row("Speaker", v_spk)

        def v_house(x, yy):
            chips = list(scr.get("chips") or [])
            if not chips:
                chips = ["ear open" if facts.get("ear_open") else "ear quiet",
                         "VOICE LIVE" if voice.get("voice_on") else "voice locked"]
            xx = x
            for c_ in chips:
                k = str(c_).lower()
                kind = "on" if ("open" in k or "live" in k) else ("warn" if ("quiet" in k or "locked" in k) else "")
                xx += _chip(d, xx, yy - f_chip.size * 0.2, str(c_), f_chip, kind) + 0.45 * u
            return f_chip.size * 1.9
        row("House", v_house)

        def v_svc(x, yy):
            xx = x
            for s in ((house.get("svc") or {}).get("summary") or []):
                lvl = s.get("level") or ""
                kind = "on" if lvl == "ok" else ("warn" if lvl in ("warn", "degraded") else ("bad" if lvl else ""))
                xx += _chip(d, xx, yy - f_chip.size * 0.2, s.get("chip") or s.get("label") or s.get("id") or "?", f_chip, kind) + 0.45 * u
            gpu = (house.get("gpu") or {})
            f_g = font("mono", 0.82 * u)
            gtxt = gpu_label(gpu)
            _text(d, (xx + 2.4 * u * 0.82, yy), gtxt, f_g, INK2, 0.08)
            xx += 2.4 * u * 0.82 + _text_w(d, gtxt, f_g, 0.08) + 1.1 * u
            who, lit = who_label(house, presence)
            _text(d, (xx, yy), who, f_g, SAGE if lit else INK2, 0.08)
            left = turbo_left(gpu, t)
            if left is not None:
                # the pilot light rides this line and folds with this box. It is
                # read-only, like the occupancy word beside it (the page has
                # pointer-events:none on .gpu-mode): a hotspot here would be
                # pressed as a mode switch. The toggle is the Control Panel's.
                xx += _text_w(d, who, f_g, 0.08) + 1.1 * u
                _, dy0, _, dy1 = f_g.getbbox("8")
                fl = _flame(int(round((dy1 - dy0) * 1.12)))
                wall.alpha_composite(fl, (int(round(xx)), int(round(yy + dy1 - fl.height))))
                xx += fl.width + 0.34 * f_g.size
                _text(d, (xx, yy), turbo_clock(left), f_g, EMBER, 0.08)
            return f_chip.size * 1.9
        row("Services", v_svc)

        def v_logs(x, yy):
            # the kiosk's three log toggles, wired (the User
            # 2026-09-07): lit = on; ROOM reads UserOnly when the room is off
            lp = ft_prefs or {}
            xx = x
            room_on = bool(lp.get("log_room", True))
            for name, key, on in (("Board", "log_board", bool(lp.get("log_board", True))),
                                  ("ROOM" if room_on else "UserOnly", "log_room", True),
                                  ("Tools", "log_tools", bool(lp.get("log_tools", True)))):
                if key in NA_HUD:                   # krew's tape on a computer without krew: greyed, no click
                    cw = _chip(d, xx, yy - f_chip.size * 0.2, name, f_chip, "na")
                    HOTSPOTS.pop(key, None)
                else:
                    cw = _chip(d, xx, yy - f_chip.size * 0.2, name, f_chip, "on" if on else "")
                    HOTSPOTS[key] = (xx, yy - f_chip.size * 0.2, xx + cw, yy + f_chip.size * 1.2)
                xx += cw + 0.45 * u
            return f_chip.size * 1.9
        row("Logs", v_logs)
    # caret is chrome (house-rail); record the seat for the ▲/▼
    ANCHORS["vitals_tog"] = [float(x0), float(y), 28.0, 22.0]
    # the group the caret belongs to (band + rows), for the chrome's hover
    ANCHORS["vitals_box"] = [float(x0), float(band_top), float(g.rail_w - x0), float(y + 22 - band_top)]
    HOTSPOTS["vitals_tog"] = (x0, y, x0 + 28, y + 22)

    # the oracle dial (bottom-left, stacked above the camera PiP)
    # Click the middle core to collapse to her dot; click the dot to restore.
    # kiosk: .clockwrap.dot { .dial scale(.05)+opacity 0; #oracleDot opacity .62 }
    dial_size, dial_bottom = g.oracle_box()
    cx = x0 + dial_size / 2
    cy = dial_bottom - dial_size / 2
    sc = max(ORACLE_SCALE_DOT, min(1.0, float(oracle_scale)))
    dial_op = max(0.0, min(1.0, (sc - ORACLE_SCALE_DOT) / (1.0 - ORACLE_SCALE_DOT)))
    # .clockwrap::before — the page's dusk, open or folded (it is the wrap's)
    dusk = _dial_dusk(dial_size)
    wall.alpha_composite(dusk, (int(round(cx - dusk.width / 2)), int(round(cy - dusk.height / 2))))
    if dial_op > 0.02:
        # No backing disc: the kiosk's .clockwrap sits straight on the page
        # and the ZFADE already dims the ASCII under it. The GROUND disc read
        # as "a dark black box around Oracle Clock" (the User 2026-09-06):
        # "Needs that Dusk matching everywhere".
        # hover: the pointer inside the dial's own circle, like .clockwrap:hover
        hover = False
        if ptr:
            hover = math.hypot(float(ptr[0]) - cx, float(ptr[1]) - cy) <= dial_size * sc / 2.0
        oracle = dial(house, presence, dial_size, t, hover=hover)
        if not hover and DIAL_MUTE < 0.999:
            oracle = oracle.copy()
            oracle.putalpha(oracle.getchannel("A").point(lambda a_, k=DIAL_MUTE: int(a_ * k)))
        if sc < 0.995:
            nw = max(1, int(oracle.width * sc))
            nh = max(1, int(oracle.height * sc))
            oracle = oracle.resize((nw, nh), Image.BILINEAR)
            a = oracle.getchannel("A").point(lambda p, o=dial_op: int(p * o))
            oracle.putalpha(a)
        wall.alpha_composite(oracle, (int(cx - oracle.width / 2), int(cy - oracle.height / 2)))
        if sc > 0.5 and no_beam_sig(house, t):
            # NO BEAM MICROPHONE (the User 2026-10-01): tiny type under the 6, the dial's own muted ink.
            # Decorative: no hotspot, no error, the lamps simply stay dark. OFF unless CRISSA_NO_BEAM_SIG=1.
            f_nb = font("mono", max(7.0, 8.0 * dial_size * sc / 260.0))
            tw = ImageDraw.Draw(wall).textlength(NO_BEAM_TEXT, font=f_nb)
            tile = Image.new("RGBA", (int(tw) + 2, f_nb.size + 4), (0, 0, 0, 0))
            ImageDraw.Draw(tile).text((1, 1), NO_BEAM_TEXT, font=f_nb, fill=INK2 + (int(255 * max(DIAL_MUTE, 0.45)),))
            wall.alpha_composite(tile, (int(round(cx - tile.width / 2)),
                                        int(round(cy + 0.86 * dial_size * sc / 2 - tile.height / 2))))
    # Restore disc is chrome (idle-hides with the toolbar), not HUD.
    hr = max(22.0, 0.10 * dial_size)
    HOTSPOTS["oracle"] = (cx - hr, cy - hr, cx + hr, cy + hr)
    HOTSPOTS["dial"] = (x0, dial_bottom - dial_size, x0 + dial_size, dial_bottom) if sc > 0.5 \
        else HOTSPOTS["oracle"]
    ANCHORS["oracle"] = [float(cx), float(cy), float(dial_size)]
    d = ImageDraw.Draw(wall)

    # camera PiP (bottom-left) — dimmed to .32 while Crissa is live
    px, py = int(x0), int(H - g.pip_edge - g.pip_h)
    pw, ph = int(g.pip_w), int(g.pip_h)
    ANCHORS["pip"] = [float(px), float(py), float(pw), float(ph)]
    ANCHORS["cam_hover"] = [1.0] if (ptr and px <= ptr[0] <= px + pw and py <= ptr[1] <= py + ph) else [0.0]
    cam_hover = bool(ptr) and (px <= ptr[0] <= px + pw and py <= ptr[1] <= py + ph)
    if big == "camera" and cam is not None:
        # enlarged on click: the camera FULL SCREEN, cover-fit like the page's
        # #pipwrap.big (100vw x 100vh, the visor's img object-fit:cover), no
        # grade, no border, at the page's .32 — she shows through it; a click
        # anywhere closes it (the hotspot is the whole glass).
        # big_k < 1: the box on its way from the corner to the whole glass
        # (the User 2026-09-08: "duplicate that open and close action for the PIP
        # and Camera") — the same cover-fit, the corner's grade and alpha
        # until half way, the visor HUD sized to the box.
        gk = min(1.0, max(0.0, float(big_k)))
        bx, by = int(round(px * (1.0 - gk))), int(round(py * (1.0 - gk)))
        bw, bh = int(round(pw + (W - pw) * gk)), int(round(ph + (H - ph) * gk))
        k = max(bw / max(1, cam.width), bh / max(1, cam.height))
        sw, sh_ = max(1, int(round(cam.width * k))), max(1, int(round(cam.height * k)))
        shot = cam.resize((sw, sh_), Image.Resampling.LANCZOS).convert("RGBA")
        ox, oy = (sw - bw) // 2, (sh_ - bh) // 2
        shot = shot.crop((ox, oy, ox + bw, oy + bh))
        if gk < 0.5:
            shot = _pip_filter(shot)
        shot.putalpha(int(255 * (CAM_PIP_ALPHA + (CAM_OPEN_ALPHA - CAM_PIP_ALPHA) * gk)))
        wall.alpha_composite(shot, (bx, by))
        d = ImageDraw.Draw(wall)
        # the visor's HUD on the same feed (the User 2026-09-07: "HUD effect we
        # are missing"): corners, LIVE FACE ID, the meters, PAUSED. The sweep
        # line is a 5 Hz tile (render_scan_line) over ANCHORS["camera_big"].
        render_visor_hud(wall, (bx, by, bw, bh), speaker, voice, u, small=(gk < 0.5),
                         presence=presence, now=t)
        ANCHORS["camera_big"] = [float(bx), float(by), float(bw), float(bh)]
        ANCHORS["camera_box"] = [float(bx), float(by), float(bw), float(bh)]
        HOTSPOTS["camera"] = (bx, by, bx + bw, by + bh)
        HOTSPOTS["pip_fold"] = (px, py, px + pw, py + 22)
    elif not pip_bar:
        ANCHORS.pop("camera_big", None)
        ANCHORS.pop("camera_box", None)
        pip = Image.new("RGBA", (pw, ph), GROUND + (CAM_PLATE_ALPHA,))
        if cam is not None:
            thumb = _pip_filter(cam.resize((pw, ph), Image.Resampling.LANCZOS).convert("RGBA"))
            cam_a = CAM_PIP_ALPHA_HOVER if cam_hover else CAM_PIP_ALPHA
            thumb.putalpha(int(255 * (CAM_PIP_ALPHA_LIVE if crissa_her_live else cam_a)))
            pip.alpha_composite(thumb)
        wall.alpha_composite(pip, (px, py))
        d = ImageDraw.Draw(wall)
        # the same visor HUD as the big view and as Brave's iframe, sized to
        # the corner (the User 2026-09-07: "HUD from html to here")
        render_visor_hud(wall, (px, py, pw, ph), speaker, voice, u, small=True,
                         mute=1.0 if cam_hover else CAM_MUTE,
                         presence=presence, now=t)
        # the sweep is on the corner tile too (the User 2026-09-07: "can we get the
        # scan line on the camera feed tile too?")
        ANCHORS["camera_box"] = [float(px), float(py), float(pw), float(ph)]
        # DOWN: a pale green box, its HUD muted with it; UP (pointer on it):
        # ember frame, HUD bright (the User 2026-09-09: "muted colors when down,
        # back to bright when up"). The amber hairline of 2026-09-06 gave way
        # to the HUD's own green so the box reads as one thing.
        d.rectangle((px, py, px + pw - 1, py + ph - 1),
                    outline=(EMBER + (255,)) if cam_hover else (SAGE + (int(round(150 * CAM_MUTE)),)))
        f_pl = font("mono", 0.46 * u)
        if int(t) % 2 == 0:        # blinks: on one second, off the next
            _text(d, (px + 0.5 * u, py + ph - f_pl.size * 1.9), camera_label(speaker), f_pl,
                  INK3 if cam_hover else (SAGE + (int(round(200 * CAM_MUTE)),)), 0.14)
        HOTSPOTS["camera"] = (px, py, px + pw, py + ph)
        HOTSPOTS["pip_fold"] = (px, py, px + pw, py + 22)
    else:
        ANCHORS.pop("camera_big", None)
        ANCHORS.pop("camera_box", None)
        HOTSPOTS.pop("camera", None)
        HOTSPOTS["pip_fold"] = (px, py + ph - 24, px + pw, py + ph)
    fbx, fby, fbw, fbh = ft_box(g, ft_prefs)
    HOTSPOTS["ft"] = (fbx, fby, fbx + fbw, fby + fbh)
    write_anchors()

    if stale:
        f_s = font("mono", 0.6 * u)
        # A PICK IS BEING PUT ON (the User 2026-10-31, Choose skin as product): while the player lane restarts for a new
        # skin the corner says so instead of FEED STALE (lib/crissa_avatar.changing_text; "" off / on the house)
        msg = _skin_changing() or "FEED STALE"
        _text(d, (W - 2.4 * u - d.textlength(msg, font=f_s) - 0.6 * u * 3, H - 2.6 * u - f_s.size), msg, f_s,
              RUST if msg == "FEED STALE" else SAGE, 0.2)
    _paint_notes_sheet(wall, d, g, scr)
    _paint_toast(wall, d, g, house)
    return wall


# The letter's last layout (the User 2026-10-09): its rect, wrapped line count and lines per page, for the wall's
# wheel and Crissa's scroll. Written by every paint; empty when no letter is up.
LETTER: dict = {}


def letter_rect(g: Geom) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) of the 8.5x11 letter on the right."""
    W, H = g.W, g.H
    sw = min(0.40 * W, (0.936 * H) * 8.5 / 11.0)
    sh = sw * 11.0 / 8.5
    x1 = W - 0.02 * W
    y0 = max(0.06 * H, (H - sh) / 2)
    return (x1 - sw, y0, x1, y0 + sh)


# THE LETTER READS MARKDOWN AS TEXT (the User 2026-10-21, the teaching take: her lesson showed "##" and "**"). Her
# reports are markdown (team-crissa show_me 'report', the crissa-report shape) and the letter is one monospace font, so a
# heading becomes its words (a short section heading in capitals, as a typed page would), **bold** / *italic* /
# `code` lose their marks, "- " / "* " bullets become "• ", a rule line goes. Text that is not markdown is unchanged
# ("3*4*5" keeps its stars: a mark must sit against its words). Kill HOUSE_WALL_LETTER_MD=0.
def letter_plain(body: str) -> str:
    if os.environ.get("HOUSE_WALL_LETTER_MD", "1") == "0":
        return body
    out = []
    for line in str(body or "").split("\n"):
        m = re.match(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            words = m.group(2)
            # capitals only on a heading of plain words: "Solve 3x + 7 = 22" must not become "3X"
            plain = re.fullmatch(r"[A-Za-z][A-Za-z ,'&/-]*", words) is not None
            line = words.upper() if len(m.group(1)) >= 2 and len(words) <= 32 and plain else words
        elif re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", line):
            continue
        line = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"\1", line)
        line = re.sub(r"(?<![\w*])__(?=\S)(.+?)(?<=\S)__(?![\w])", r"\1", line)
        line = re.sub(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])", r"\1", line)
        line = re.sub(r"`([^`]*)`", r"\1", line)
        line = re.sub(r"^(\s*)[-*+]\s+", "\\1\u2022 ", line)
        out.append(line)
    return "\n".join(out)


def letter_body(scr: dict | None) -> tuple[str, str]:
    """(title, body). The notes letter; a document sent without one shows as the letter too (the User 2026-10-09:
    one page style, the right-hand letter). HOUSE_LETTER_SHOWS_DOC=0 = notes only."""
    scr = scr or {}
    body = str(scr.get("notes") or "").strip()
    title = str(scr.get("notes_title") or "").strip()
    if not body and os.environ.get("HOUSE_LETTER_SHOWS_DOC", "1") != "0":
        body = str(scr.get("doc") or "").strip()
        title = str(scr.get("doc_title") or "").strip()
    return letter_plain(title or "Notes").strip(), letter_plain(body)


def letter_lines(d: ImageDraw.ImageDraw, body: str, f_b, max_w: float) -> list[tuple[str, bool]]:
    """Wrapped lines as (text, ends_paragraph)."""
    out: list[tuple[str, bool]] = []
    for para in body.split("\n"):
        line = ""
        for word in (para or " ").split(" "):
            trial = (line + " " + word).strip()
            if d.textlength(trial, font=f_b) > max_w and line:
                out.append((line, False))
                line = word
            else:
                line = trial
        out.append((line, True))
    return out


# THE TEACHER'S POINTER READS AT STREAM SIZE (the User 2026-10-21 via the conductor: "the marker must read at stream
# size, not a small marker in the letter's ink"). The demo lap (lib/teach_pointer) marks the step she is speaking: its
# first line starts with "\u25b6 " and its other lines with "\u2503 ". When a letter carries a mark, the marked
# paragraphs get a bright band with an ember edge and the hot ink, and every other line is dimmed. A letter with no
# mark is drawn exactly as before. Kill HOUSE_WALL_LETTER_POINTER=0.
LETTER_POINTER = "\u25b6 "
LETTER_BAR = "\u2503 "
LETTER_BAND = (255, 214, 120, 255)
LETTER_BAND_EDGE = (217, 120, 30, 255)
LETTER_HOT = (20, 14, 8, 255)
LETTER_DIM = (150, 142, 132, 255)


def _letter_marks(lines: list[tuple[str, bool]]) -> list[bool]:
    """Per wrapped line: does it belong to a marked paragraph (one that starts with the pointer or the bar)?"""
    if os.environ.get("HOUSE_WALL_LETTER_POINTER", "1") == "0":
        return [False] * len(lines)
    out, in_mark, para_start = [], False, True
    for text, ends in lines:
        if para_start:
            in_mark = text.startswith((LETTER_POINTER, LETTER_BAR))
        out.append(in_mark)
        para_start = ends
    return out


def _paint_notes_sheet(wall: Image.Image, d: ImageDraw.ImageDraw, g: Geom, scr: dict) -> None:
    """Brave's 8.5×11 letter on the right. HUD layer — stays through idle. `_letter_line` (the wall's scroll, the
    User 2026-10-09) is the first wrapped line shown; a mark says there is more above / below."""
    title, body = letter_body(scr)
    if not body:
        LETTER.clear()
        return
    u = g.u
    x0, y0, x1, y1 = letter_rect(g)
    sw = x1 - x0
    paper = (243, 238, 228, 230)
    d.rounded_rectangle((x0, y0, x1, y1), radius=max(2, int(0.15 * u)), fill=paper, outline=HAIR + (180,))
    f_t = font("mono", 0.72 * u)
    f_b = font("mono", 0.50 * u)
    ink = (40, 36, 32, 255)
    _text(d, (x0 + 0.7 * u, y0 + 0.55 * u), title, f_t, ink, 0)
    lines = letter_lines(d, body, f_b, sw - 1.4 * u)
    top, bottom = y0 + 1.5 * u, y1 - 0.8 * u
    per_page = max(1, int((bottom - top) // (f_b.size * 1.40)))
    scroll = os.environ.get("HOUSE_LETTER_SCROLL", "1") != "0"
    first = int(scr.get("_letter_line") or 0) if scroll else 0
    first = max(0, min(first, max(0, len(lines) - per_page)))
    LETTER.update(rect=(x0, y0, x1, y1), total=len(lines), per_page=per_page, first=first)
    y = top
    shown = first
    marks = _letter_marks(lines)
    pointing = any(marks)
    for i, (text, ends) in enumerate(lines[first:], start=first):
        if y > bottom:
            break
        step = f_b.size * (1.45 if ends else 1.35)
        if pointing and marks[i]:
            # the step she is speaking: a bright band across its lines, the text in the hot ink
            d.rectangle((x0 + 0.35 * u, y - 0.18 * f_b.size, x1 - 0.35 * u, y + step - 0.12 * f_b.size),
                        fill=LETTER_BAND)
            d.rectangle((x0 + 0.35 * u, y - 0.18 * f_b.size, x0 + 0.55 * u, y + step - 0.12 * f_b.size),
                        fill=LETTER_BAND_EDGE)
            _text(d, (x0 + 0.7 * u, y), text.replace(LETTER_BAR, "", 1), f_b, LETTER_HOT, 0)
        else:
            _text(d, (x0 + 0.7 * u, y), text, f_b, LETTER_DIM if pointing else ink, 0)
        y += step
        shown += 1
    if not scroll:
        return
    f_m = font("mono", 0.42 * u)
    mark = (120, 110, 96, 255)
    if first > 0:
        _text(d, (x1 - 0.7 * u - d.textlength("more above", font=f_m), y0 + 0.62 * u), "more above", f_m, mark, 0)
    if shown < len(lines):
        _text(d, (x1 - 0.7 * u - d.textlength("more below", font=f_m), y1 - 0.62 * u - f_m.size), "more below", f_m, mark, 0)


def _paint_toast(wall: Image.Image, d: ImageDraw.ImageDraw, g: Geom, house: dict) -> None:
    """3 s ember line (Brave #toast). Failed wall actions; cam_note already has its own."""
    text = str(house.get("_wall_toast") or (house.get("screen") or {}).get("toast") or "").strip()
    if not text:
        return
    text = text.upper()[:72]
    f = font("mono", 0.62 * g.u)
    tw = d.textlength(text, font=f)
    pad_x, pad_y = 0.9 * g.u, 0.5 * g.u
    bw, bh = tw + 2 * pad_x, f.size + 2 * pad_y
    x0 = (g.W - bw) / 2
    y0 = g.H - 4.4 * g.u - bh
    d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=2, fill=GROUND2 + (230,), outline=EMBER_DEEP + (255,))
    _text(d, (x0 + pad_x, y0 + pad_y * 0.6), text, f, EMBER, 0)


SCAN_SWEEP_S = 3.6      # the visor's .scan animation: one pass top → bottom


CAM_MUTE = 0.18     # the corner box DOWN: every HUD alpha × this — a pale green; .45 read too strong beside Brave (the User 2026-09-09: "a bit paler/fainter greens on the outlines and hud", then "more transparent")


FACE_PASS = 0.60   # rail-presence's camera bar: a live face sim at or above it is the User's face


FACE_NO_READING = "—"   # no live face reading: a dash, never a fake 00 (the User 2026-10-09)


FACE_FLASH_S = 10.0   # a score flashes, then fades out (the User 2026-10-09)


def _fnum(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return x


def _flash_s() -> float:
    try:
        v = float(os.environ.get("HOUSE_WALL_FACE_FLASH_S", "") or FACE_FLASH_S)
    except (TypeError, ValueError):
        v = FACE_FLASH_S
    return v if v > 0 else FACE_FLASH_S


def _face_pct(fs, sign: bool = True) -> str:
    """The real face similarity as a whole percentage (0.823 -> '82%').
    No reading -> '—'. rail-presence's stack sends 0.0 when nothing is fresh
    (the camera saw no scored face in the last 2.5 s), so 0 or below is a dash
    too: "it is 00 when i am looking at it" (the User 2026-10-09). Kill
    HOUSE_WALL_FACE_DASH=0 puts the old '00' back."""
    try:
        v = float(fs)
    except (TypeError, ValueError):
        v = None
    if (v is None or v <= 0.0) and os.environ.get("HOUSE_WALL_FACE_DASH", "1") != "0":
        return FACE_NO_READING
    n = max(0, min(100, int(round((v or 0.0) * 100))))
    return f"{n:02d}" + ("%" if sign else "")


def _face_hold(sp: dict, st: dict, presence: dict | None, now):
    """The youngest face score still inside the flash window, or None.

    Two clocks. The speaker proxy's sim dies when the door glance clears
    the detected faces (n_faces 0, sim null) while the grant stays on presence
    as user_face_sim. face_age_s is how old that note was at presence.ts;
    ``now`` carries the fade between the wall's 2 s polls. A sticky grant
    older than the window is not a score. Kill HOUSE_WALL_FACE_AGE=0.
    """
    if os.environ.get("HOUSE_WALL_FACE_AGE", "1") == "0":
        return None
    cands: list[tuple[float, float]] = []
    sim, age = _fnum(sp.get("sim")), _fnum(st.get("age_s"))
    if sim is not None and sim > 0.0 and age is not None and age >= 0.0:
        cands.append((sim, age))
    pres = presence if isinstance(presence, dict) else {}
    user = pres.get("user") if isinstance(pres.get("user"), dict) else {}
    psim = _fnum(user.get("user_face_sim"))
    if psim is None:
        psim = _fnum(pres.get("user_face_sim"))
    page = _fnum(user.get("face_age_s"))
    if page is None:
        page = _fnum(pres.get("user_face_age_s"))
    if psim is not None and psim > 0.0 and page is not None and page >= 0.0:
        pts, n = _fnum(pres.get("ts")), _fnum(now)
        if pts is not None and n is not None:
            page = max(0.0, (n - pts) + page)
        cands.append((psim, page))
    if not cands:
        return None
    score, age = min(cands, key=lambda it: it[1])
    if age >= _flash_s():
        return None
    return score, age


def _face_cell(sp: dict | None, st: dict | None = None, presence: dict | None = None,
               now=None, sign: bool = True):
    """The CAMERA cell: (text, state, alpha, score).

    'live' — stack face > 0, full ink, the 0.60 bar colours it.
    'flash' — that reading has gone, so the score from this look fades from
    full ink to nothing across FACE_FLASH_S (the User 2026-10-09: the cam
    never budged for FaceFinder; flash a score, then fade over 10 s).
    'none' — a dash. A grant that has been sitting is not a flash.
    """
    sp = sp or {}
    stack = dict(st or {})
    if not stack and isinstance(sp.get("stack"), dict):
        stack = dict(sp["stack"])
    psp = {}
    if isinstance(presence, dict) and isinstance(presence.get("speaker"), dict):
        psp = presence["speaker"]
    pf, sf = _fnum(psp.get("face")), _fnum(stack.get("face"))
    if pf is not None and pf > (sf or 0.0):
        stack["face"] = pf
    if stack.get("age_s") is None and psp.get("age_s") is not None:
        stack["age_s"] = psp["age_s"]
    fs = stack.get("face")
    shown = _fnum(fs)
    if shown is not None and shown > 0.0:
        return _face_pct(shown, sign=sign), "live", 1.0, shown
    held = _face_hold(sp, stack, presence, now)
    if held is not None:
        score, age = held
        alpha = max(0.0, 1.0 - (age / _flash_s()))
        if alpha > 0.0:
            return _face_pct(score, sign=sign), "flash", alpha, score
    return _face_pct(fs), "none", 1.0, None


# A SCORE STAYS UP, THEN FADES (round 21, the User 2026-10-14, .101 round 20: "took a while for my score to serve and
# the dwell/fade feature is not working. it flashes only quickly"). The camera cell's fade (FACE_FLASH_S) runs only off
# the feed's own age fields, which a pack's presence does not always send, and the voice and stack numbers had none: a
# score showed only while its reading lasted. The Wall now keeps each reading it has drawn: once a reading goes, it
# stays at full ink for SCORE_DWELL_S, then fades out across SCORE_FADE_S (10 s in all, the camera's own window). A
# fresh reading replaces it at once. A pack only. Kill HOUSE_WALL_SCORE_DWELL=0.
SCORE_DWELL_S = 4.0
SCORE_FADE_S = 6.0
_SCORE_HELD: dict = {}
# .101 10-19 (the User: "same with voice. Give me the last score in faintest text after a period of fading. Should we
# use - for anything after 30 s without a signal to measure?"): a held score fades to SCORE_FAINT, not to nothing, and
# stays there until SCORE_STALE_S after its reading; then the cell is a dash. The face check fades to SCORE_FAINT at
# its next check's time and is a dash SCORE_STALE_S after that check was due. Kill HOUSE_WALL_SCORE_FAINT=0 (fade out).
SCORE_FAINT = 0.2
SCORE_STALE_S = 30.0


# NOTHING TO MEASURE IS A GREY DASH (.101 10-19, the User: "should 00 be amber for alarm? or grey for nothing to
# see?"): voice, stack and sex drew "00" in amber with no reading, and the camera's dash was amber too - read as a
# failed score. No reading (None, or 0 and below) is a dash in the labels' own grey; amber stays a real reading under
# its bar, green one over it (no new colour). The User: "only a measurement under the bar should show amber for
# stranger": a held voice or stack score keeps the colour its reading had (note_ok / held_ok, the ear's own per-person
# bar via speaker *_green), so your own voice fades green, a stranger's amber. Kill HOUSE_WALL_NONE_GREY=0 (00 in amber).
# The house keeps its own colours (off by default there, round 31); a pack has it on.
def none_grey_on() -> bool:
    return os.environ.get("HOUSE_WALL_NONE_GREY", "0" if crissa_home.is_house() else "1").strip().lower() not in ("0", "off", "false", "no")


def _no_reading(v) -> bool:
    x = _fnum(v)
    return x is None or x <= 0.0


def _score_txt(v, sign: bool = True) -> str:
    if none_grey_on() and _no_reading(v):
        return FACE_NO_READING
    return f"{int((_fnum(v) or 0) * 100):02d}" + ("%" if sign else "")


def score_faint_on() -> bool:
    return os.environ.get("HOUSE_WALL_SCORE_FAINT", "0" if crissa_home.is_house() else "1").strip().lower() not in ("0", "off", "false", "no")


def score_dwell_on() -> bool:
    return (not crissa_home.is_house()) and os.environ.get("HOUSE_WALL_SCORE_DWELL", "1").strip().lower() not in (
        "0", "off", "false", "no")


_SCORE_OK: dict = {}


def held_ok(field: str) -> bool:
    """Whether the held reading of `field` passed its bar when it came (note_ok), so a held score keeps its colour."""
    return bool(_SCORE_OK.get(field))


def note_ok(field: str, value, ok) -> None:
    """A live reading's pass (the ear's own per-person bar, speaker *_green), kept with the reading."""
    v = _fnum(value)
    if v is not None and v > 0.0:
        _SCORE_OK[field] = bool(ok)


def held_score(field: str, value, now) -> tuple:
    """(the reading to show or None, its alpha 0..1). A live reading (> 0) is kept with its time and shown as is; once
    it goes, the kept one shows at full ink for SCORE_DWELL_S, then fades across SCORE_FADE_S."""
    t = _fnum(now)
    if t is None:
        t = time.time()
    v = _fnum(value)
    if v is not None and v > 0.0:
        _SCORE_HELD[field] = (v, t)
        return v, 1.0
    got = _SCORE_HELD.get(field)
    if not got or not score_dwell_on():
        return None, 1.0
    age = t - got[1]
    faint = score_faint_on()
    if age < 0.0 or age >= (SCORE_STALE_S if faint else SCORE_DWELL_S + SCORE_FADE_S):
        return None, 1.0
    if age < SCORE_DWELL_S:
        return got[0], 1.0
    floor = SCORE_FAINT if faint else 0.0
    return got[0], max(floor, 1.0 - (age - SCORE_DWELL_S) / SCORE_FADE_S)


def _held_ink(col: tuple, alpha: float) -> tuple:
    """A held score's ink: the colour as it was at full ink, its alpha once it fades."""
    return col if alpha >= 1.0 else col + (max(1, int(round(255 * alpha))),)


# THE FACE CHECK'S SCORE LINGERS TO THE NEXT CHECK (.101 10-19, the User: "make that linger but grow faint over the
# whole 30 s, or whatever the rate is when off"): a fixed camera (laptop_auth) checks his face once every 30-60 s, and
# the 10 s hold above left the cell a dash for most of each gap. Presence sends the check (face_check: at, sim, found,
# next_at); its score shows from the check and fades to nothing as the next check comes due. A fresh live reading
# still wins. Kill HOUSE_WALL_FACE_CHECK_FADE=0 (the 10 s hold).
def face_check_cell(presence: dict | None, now=None, sign: bool = True):
    """(text, "flash", alpha, score) for the newest face check while it is not yet replaced, else None."""
    if os.environ.get("HOUSE_WALL_FACE_CHECK_FADE", "1").strip().lower() in ("0", "off", "false", "no"):
        return None
    fc = (presence or {}).get("face_check") if isinstance(presence, dict) else None
    if not isinstance(fc, dict) or not fc.get("found"):
        return None
    at, nxt, sim = _fnum(fc.get("at")), _fnum(fc.get("next_at")), _fnum(fc.get("sim"))
    t = _fnum(now)
    t = time.time() if t is None else t
    if at is None or nxt is None or sim is None or sim <= 0.0 or nxt <= at:
        return None
    if t < at:
        return None
    if score_faint_on():
        if t >= nxt + SCORE_STALE_S:
            return None                           # the next check is long overdue: a dash
        alpha = max(SCORE_FAINT, 1.0 - (1.0 - SCORE_FAINT) * (t - at) / (nxt - at))
    else:
        alpha = 1.0 - (t - at) / (nxt - at)
        if alpha <= 0.0:
            return None
    return _face_pct(sim, sign=sign), "flash", min(1.0, alpha), sim


def _face_cell_held(sp: dict | None, st: dict | None = None, presence: dict | None = None, now=None,
                    sign: bool = True):
    """_face_cell with the Wall's own hold: a face score the feed has stopped sending stays up, then fades."""
    txt, state, a, sc = _face_cell(sp, st, presence, now, sign=sign)
    if state != "live":
        fcc = face_check_cell(presence, now, sign=sign)
        if fcc is not None and not (state == "flash" and a >= fcc[2]):
            return fcc
    if not score_dwell_on():
        return txt, state, a, sc
    if state == "live":
        held_score("cam", sc, now)
        return txt, state, a, sc
    hv, ha = held_score("cam", None, now)
    if hv is not None and not (state == "flash" and a >= ha):
        return _face_pct(hv, sign=sign), "flash", ha, hv
    return txt, state, a, sc


def _face_pass(fs) -> bool:
    try:
        return float(fs) >= FACE_PASS
    except (TypeError, ValueError):
        return False


def render_visor_hud(wall: Image.Image, box, speaker: dict | None, voice: dict | None,
                     u: float, small: bool = False, mute: float = 1.0,
                     presence: dict | None = None, now=None) -> None:
    """The face-ID visor HUD drawn on the wall's big camera view from the same
    feed (/v1/speaker): four corner brackets, the LIVE FACE ID panel (name or
    NONE; BEAM / CAMERA / VOICE / SEX / STACK — gold until green), PAUSED.
    The visor's HUD green is SAGE here (tokens, not the visor's hex).
    mute: 0..1 on every alpha — the corner box DOWN (small, pointer off it)
    is a pale green, back to bright under the pointer or enlarged (the User
    2026-09-09: "dim the HUD box to just a pale green when minimized …
    muted colors when down, back to bright when up")."""
    bx, by, bw, bh = (int(v) for v in box)
    d = ImageDraw.Draw(wall)
    sp = speaker or {}
    st = sp.get("stack") if isinstance(sp.get("stack"), dict) else {}
    mute = min(1.0, max(0.0, float(mute)))
    def mu(a: int) -> int:
        return int(round(a * mute))
    hud = SAGE + (mu(205),)
    dim = SAGE + (mu(90),)
    k = 0.34 if small else 0.8       # "HUD smaller and tucked in the corner" (the User 2026-09-07)
    # corners
    L = max(7, int(0.9 * u * k))
    m = max(4, int(0.35 * u * k))
    # thin and see-through: "no thick corners, much thinner and transparent so
    # you can see ascii behind it" (the User 2026-09-07)
    corner = SAGE + (mu(150),)
    for cx_, cy_, sx, sy in ((bx + m, by + m, 1, 1), (bx + bw - m, by + m, -1, 1),
                             (bx + m, by + bh - m, 1, -1), (bx + bw - m, by + bh - m, -1, -1)):
        d.line([(cx_, cy_), (cx_ + sx * L, cy_)], fill=corner, width=1)
        d.line([(cx_, cy_), (cx_, cy_ + sy * L)], fill=corner, width=1)
    # PAUSED chip (top-right), like the visor's
    if (voice or {}).get("paused") or str((voice or {}).get("state") or "").lower() == "paused":
        f_c = font("mono", 0.42 * u * k)
        txt = "• PAUSED"
        tw = _text_w(d, txt, f_c, 0.1)
        if small:   # top-LEFT in the corner box: the panel owns the right
            cx0_ = bx + m + 0.4 * u * k
            cy1 = by + m + 0.3 * u * k
            d.rectangle((cx0_, cy1, cx0_ + tw + 0.5 * u * k, cy1 + f_c.size * 1.5), outline=dim)
            _text(d, (cx0_ + 0.25 * u * k, cy1 + f_c.size * 0.2), txt, f_c, hud, 0.1)
        else:
            cx1, cy1 = bx + bw - m - 0.4 * u, by + m + 0.4 * u
            d.rectangle((cx1 - tw - 0.5 * u, cy1, cx1, cy1 + f_c.size * 1.5), outline=dim)
            _text(d, (cx1 - tw - 0.25 * u, cy1 + f_c.size * 0.2), txt, f_c, hud, 0.1)
    # LIVE FACE ID panel (right, upper third)
    pw_ = int(min(0.46 * bw, 11 * u * k) if small else min(0.34 * bw, 11 * u))
    ph_ = int(6.2 * u * k)
    px_ = bx + bw - m - int(0.4 * u * k) - pw_
    py_ = by + m + int(0.3 * u * k)          # tucked into the corner, not floating
    panel = Image.new("RGBA", (pw_, ph_), GROUND + (mu(110),))
    wall.alpha_composite(panel, (px_, py_))
    d = ImageDraw.Draw(wall)
    d.rectangle((px_, py_, px_ + pw_, py_ + ph_), outline=SAGE + (mu(120),))
    f_l = font("mono", 0.38 * u * k)
    f_n = font("mono", 0.9 * u * k)
    f_v = font("mono", 0.5 * u * k)
    name = str(sp.get("name") or st.get("person_id") or "").strip()
    _text(d, (px_ + 0.5 * u * k, py_ + 0.45 * u * k), "LIVE FACE ID", f_l, dim, 0.25)
    _text(d, (px_ + 0.5 * u * k, py_ + 1.15 * u * k), (name.upper() if name else "NONE"), f_n, hud, 0.06)
    beam = st.get("beam")
    beam_s = f"{float(beam):.1f}" if isinstance(beam, (int, float)) else "—"
    cam_txt, cam_state, cam_a, cam_score = _face_cell_held(sp, st, presence, now)
    note_ok("visor_voice", st.get("voice"), st.get("voice_green"))
    vis_v, _vis_a = held_score("visor_voice", st.get("voice"), now)
    nothing = {"CAMERA": cam_score is None, "VOICE": _no_reading(vis_v), "SEX": _no_reading(st.get("sex")),
               "STACK": _no_reading(st.get("stack"))} if none_grey_on() else {}
    cells = [("BEAM", beam_s, bool(st.get("beam_green"))),
             ("CAMERA", cam_txt, _face_pass(st.get("face")) and cam_state == "live"),
             ("VOICE", _score_txt(vis_v), bool(st.get("voice_green")) or (none_grey_on() and held_ok("visor_voice"))),
             ("SEX", _score_txt(st.get("sex")), bool(st.get("sex_green"))),
             ("STACK", _score_txt(st.get("stack")), bool(st.get("stack_green")))]
    colw = pw_ / 3.0
    for i, (lab, val, green) in enumerate(cells):
        cx_ = px_ + 0.5 * u * k + (i % 3) * colw
        cy_ = py_ + 2.6 * u * k + (i // 3) * 1.7 * u * k
        _text(d, (cx_, cy_), lab, f_l, dim, 0.2)
        # the meters: amber until green — the same two tokens, dimmed with the
        # box when it is down (the User 2026-09-09: "amber, don't add colors").
        # a flashing camera score keeps its own ink and fades on its own,
        # so the corner's mute does not swallow the number.
        if nothing.get(lab):
            col, ink_a = INK2, mu(255)                 # nothing to measure: a grey dash
        elif lab == "CAMERA":
            col = SAGE if _face_pass(cam_score) else RUST
            ink_a = max(1, int(round(255 * cam_a))) if cam_state == "flash" else mu(255)
        else:
            col = SAGE if green else EMBER
            ink_a = mu(255)
            if lab == "VOICE" and score_dwell_on() and _vis_a < 1.0:
                ink_a = max(1, int(round(ink_a * _vis_a)))   # the voice fades to faint as the face does
        _text(d, (cx_, cy_ + 0.55 * u * k), val, f_v, (col + (ink_a,)), 0.08)


def render_scan_line(t: float, g: Geom):
    """The visor's sweep: one faint SAGE line crossing the camera view top →
    bottom every SCAN_SWEEP_S, as a 5 Hz tile. Corner tile or expanded, the
    same line; None when the camera is folded to a bar."""
    cb = ANCHORS.get("camera_box") or ANCHORS.get("camera_big")
    if not cb:
        return None
    bx, by, bw, bh = (float(v) for v in cb)
    frac = (float(t) % SCAN_SWEEP_S) / SCAN_SWEEP_S
    w = max(4, int(bw * 0.84))
    x = int(round(bx + (bw - w) / 2))
    y = int(round(by + 3 + frac * (bh - 6)))
    im = Image.new("RGBA", (w, 3), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i in range(w):
        k = 1.0 - abs((i / max(1, w - 1)) * 2.0 - 1.0)
        d.point((i, 1), fill=SAGE + (int(120 * k),))
    return im, (x, y)


# The kiosk's corner: quiet until you reach for it. the User 2026-09-07 showed the
# two states side by side — at rest the picture is a ghost with no caret; under
# the pointer it comes up and the caret and keys appear.
CAM_PIP_ALPHA = 0.12         # at rest: the ASCII and the roll read straight through it — Brave's #pipwrap .18 (the User 2026-09-09: "on Brave you can see the ascii behind it")
CAM_PIP_ALPHA_HOVER = 0.86   # reaching for it
CAM_PIP_ALPHA_LIVE = 0.10    # while she is live: lighter still (.32 until 2026-09-09)
# The page's enlarged camera is #pipwrap.big — full screen, object-fit cover,
# no border — but body.chan-live #pipwrap{opacity:.32} outranks .big's
# opacity:1 (same specificity, later rule), so on Brave the open camera is a
# third opaque: "fullscreen but very transparent" (the User 2026-09-07).
CAM_OPEN_ALPHA = 0.32
ORACLE_SCALE_DOT = 0.05   # kiosk .clockwrap.dot .dial { transform:scale(.05) }
ORACLE_DOT_PX = 8         # small white restore, the size of a caret; "just so it can be seen" (the User 09-06)


def _oracle_dot(diameter: int, opacity: float = 0.62, white: bool = False) -> Image.Image:
    """#oracleDot — white seat when collapsed, ember glow while speaking."""
    d_px = max(8, int(diameter))
    pad = max(6, d_px)
    s = d_px + 2 * pad
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    c = s / 2
    r = d_px / 2
    col = (0xFF, 0xFF, 0xFF) if white else EMBER
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        (c - r * 1.6, c - r * 1.6, c + r * 1.6, c + r * 1.6),
        fill=col + (int(180 * opacity),),
    )
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(max(2.0, d_px * 0.35))))
    ImageDraw.Draw(im).ellipse(
        (c - r, c - r, c + r, c + r),
        fill=col + (int(255 * opacity),),
        outline=col + (int(255 * min(1.0, opacity + 0.1)),),
        width=max(1, d_px // 12),
    )
    return im


# click targets, in wall pixels, filled in by render_hud (measured, not guessed)
HOTSPOTS: dict = {}
ANCHORS: dict = {}
# the rail's rect on the chrome image, shadow margin and fold square
# included — the wall slides this band in from above (the page's
# #rail transform: translate(-50%,-130%) -> 0, .32s cubic-bezier(.2,.8,.2,1))
RAIL_BAND: list = []
# kiosk chrome painted ON the wallpaper (not a sidecar window). mouse-move.
CHROME_HITS: dict = {}
# WHAT THE CHROME ACTUALLY PAINTS, for the band pop's bounding shape (the
# wall clips the pop to these rects so a window under the band shows through
# everywhere else — the User 2026-09-11 "it is penetrating the window below it").
# Every hit is painted except the inert plates, which are surfaces for the
# pointer over bare glass, not ink; tips and the stack's halo are painted
# but are not hits, so they register themselves.
CHROME_PAINT: dict = {}
PAINT_SKIP = frozenset({"rail_plate", "dock_plate", "dock_pick_box"})

# display/voiceonly-home.html #rail / .railbtn (px, not em)
_RAIL_FS = 12          # kiosk 11.5px
_RAIL_PAD_X = 13
_RAIL_PAD_Y = 6
_RAIL_MIN_W = 51
_RAIL_GAP = 7
_RAIL_SEP = 5          # .railsep 1px + margin 2px each side
_RAIL_TRACK = 0.16
_RAIL_FG = (0x99, 0xA0, 0xA9, 255)
_RAIL_FG_ON = (0xDE, 0xE2, 0xE7, 255)


def _inside(ptr, box) -> bool:
    """Is the pointer (x, y) inside (x0, y0, x1, y1)? False without either."""
    return bool(ptr) and bool(box) and box[0] <= ptr[0] <= box[2] and box[1] <= ptr[1] <= box[3]


# the page's #rail .tip under a hovered key: the words the symbols stand for
RAIL_TIPS = {
    "krew": "Krew — the agent dashboard and the board",
    "composer": "Store — packs of new loops for %s" % crissa_home.her_name(),
    "panel": "Control Panel — her services, voices, security and Connect",
}
# #pipfold / #ftFold on the glass: the caret is INK inside a .32 group (left)
# or at opacity .32 (right) — the same muted white either side; its scrim is
# rgba(13,16,22,.72) under the same .32
FOLD_INK_A = 82

# ONE HEIGHT FOR EVERY COLLAPSED CORNER (the User 2026-09-21: "The box on the right
# should look like the bar on left, same collapse bar with arrow to expand that
# fades. Everything is correct but the height").
#
# The camera bar was 1.15 u and the FT bar 1.5 u — the same element, drawn by
# the same kiosk_bar(), differing only in the multiplier each call site happened
# to carry. So the right-hand corner sat ~30% taller than the left and the pair
# never read as one control. The number lives here now so the two cannot drift
# again; a call site that wants its own height has to say so on purpose.
#
# This is the COLLAPSED bar only. The open FT pop's hover scrim (caret_strip)
# keeps its own 1.5 u: that strip is sized to the opened window, not to the bar.
FOLD_BAR_H_U = 1.15
FOLD_SCRIM_A = 60


@lru_cache(maxsize=8)
def _rail_plate(w: int, h: int, m: int = 24) -> Image.Image:
    """#rail: linear-gradient(180deg, rgba(10,12,15,.97), rgba(10,12,15,.86)),
    1px #121417 border with no top, radius 0 0 4px 4px, box-shadow 0 6px 16px
    rgba(0,0,0,.5). Returned with an m-px margin for the shadow."""
    W_, H_ = w + 2 * m, h + 2 * m
    sh = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((m, m + 6, m + w - 1, m + h - 1 + 6), radius=4, fill=(0, 0, 0, 128))
    out = sh.filter(ImageFilter.GaussianBlur(8))
    # the shadow may not rise above the bar's top edge (it hangs from the crown)
    out.paste((0, 0, 0, 0), (0, 0, W_, m))
    plate = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(plate)
    for y in range(h):
        t = y / max(1, h - 1)
        pd.line((0, y, w - 1, y), fill=(10, 12, 15, int(round(255 * (0.97 + (0.86 - 0.97) * t)))))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, -8, w - 1, h - 1), radius=4, fill=255)
    plate.putalpha(Image.composite(plate.getchannel("A"), Image.new("L", (w, h), 0), mask))
    pd = ImageDraw.Draw(plate)
    pd.line((0, 0, 0, h - 5), fill=(0x12, 0x14, 0x17, 255))
    pd.line((w - 1, 0, w - 1, h - 5), fill=(0x12, 0x14, 0x17, 255))
    pd.line((4, h - 1, w - 5, h - 1), fill=(0x12, 0x14, 0x17, 255))
    pd.arc((0, h - 9, 8, h - 1), 90, 180, fill=(0x12, 0x14, 0x17, 255))
    pd.arc((w - 9, h - 9, w - 1, h - 1), 0, 90, fill=(0x12, 0x14, 0x17, 255))
    out.alpha_composite(plate, (m, m))
    return out


@lru_cache(maxsize=8)
def _dock_plate(w: int, h: int, m: int = 24) -> Image.Image:
    """The dock column's plate: the #rail's gradient, hairline and hung shadow,
    rounded on all four corners (it floats at the right edge, not under the
    crown). Returned with an m-px margin for the shadow."""
    W_, H_ = w + 2 * m, h + 2 * m
    sh = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((m, m + 6, m + w - 1, m + h - 1 + 6), radius=6, fill=(0, 0, 0, 128))
    out = sh.filter(ImageFilter.GaussianBlur(8))
    plate = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(plate)
    for y in range(h):
        t = y / max(1, h - 1)
        pd.line((0, y, w - 1, y), fill=(10, 12, 15, int(round(255 * (0.97 + (0.86 - 0.97) * t)))))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=6, fill=255)
    plate.putalpha(Image.composite(plate.getchannel("A"), Image.new("L", (w, h), 0), mask))
    ImageDraw.Draw(plate).rounded_rectangle((0, 0, w - 1, h - 1), radius=6, outline=(0x12, 0x14, 0x17, 255))
    out.alpha_composite(plate, (m, m))
    return out


# THE STACK IS BARE (the User 2026-09-11: "make the background transparent vs
# dusk? Just show the icons?"): no plate, no header, no caret — the cards
# float over the wallpaper on a soft halo so the titles still read. True
# brings the dusk plate back.
DOCK_STACK_PLATE = False
DOCK_TILE = 38          # the favourite's tile, px (icon 30 inside) — about the rail keys' height, 20% down from 46 (the User 2026-09-09)
DOCK_GAP = 8
DOCK_PAD = 8
DOCK_EDGE = 10          # from the bottom edge of the glass
DOCK_SEAM = 2 * DOCK_GAP   # the gap between the launchers (favourites, APPS) and the desk's own tiles (folders, Clear desktop): structure, not a divider
RAIL_DOCK_GAP = 28      # the rail's last key ends here, the dock's first tile starts (one band, split)


BAND: dict = {}         # this frame's row placement: the rail block sets it, the dock block reads it


def dock_extras_w(n_extra: int) -> int:
    """The desk's own tiles after APPS — folders, then Clear desktop — past a seam."""
    n_extra = int(n_extra or 0)
    return (DOCK_SEAM + n_extra * DOCK_TILE + (n_extra - 1) * DOCK_GAP) if n_extra else 0


def dock_row(W: int, H: int, n: int, rail_w: int = 0, span=None, n_extra: int = 0) -> tuple[int, int, int, int]:
    """The bottom band the dock AND the rail share (the User 2026-09-08: "cede the
    top to Ubuntu and split the lower rail with the folders bar").
    (dx0, dy0, hy0, total_w): the dock's left edge (n tiles, the APPS tile,
    then the handle), the band's top, the tiles' top, the dock's width.

    The group [rail][RAIL_DOCK_GAP][dock] is centred in `span` = (left,
    right) — the free run between the camera box and the FT box (the User
    2026-09-08: "if the dock grows to the right, split that space between
    Camera box and the dock") — or on the glass when no span is given or
    the group does not fit it."""
    row_h = DOCK_TILE + 2 * DOCK_PAD
    total_w = DOCK_TILE + DOCK_GAP + (n + 1) * DOCK_TILE + n * DOCK_GAP + dock_extras_w(n_extra)    # +1: the APPS tile
    group_w = (rail_w + RAIL_DOCK_GAP if rail_w else 0) + total_w
    left, right = 0, W
    if span and span[1] - span[0] >= group_w + 2 * RAIL_DOCK_GAP:
        left, right = int(span[0]), int(span[1])
    dx0 = int((left + right - group_w) / 2) + (rail_w + RAIL_DOCK_GAP if rail_w else 0)
    dy0 = int(H - DOCK_EDGE - row_h)
    BAND.update(dx0=dx0, rail_x=dx0 - RAIL_DOCK_GAP - rail_w, n=n)
    return dx0, dy0, dy0 + DOCK_PAD, total_w


def _dock_places(p: dict) -> list[dict]:
    """The folder tiles: p["places"] when the wall handed them, else the
    library's own list (the contract test and a fresh desk draw them too)."""
    pl = p.get("places")
    if pl is None:
        import house_wall_dock as _dock
        pl = _dock.places()
    return [dict(x) for x in list(pl)[:4]]


def band_span(W: int, H: int) -> tuple[int, int]:
    """The free run of the bottom band: from the camera box's right edge to
    the FT box's left edge, where each one stands in the band."""
    dy0 = int(H - DOCK_EDGE - DOCK_TILE - 2 * DOCK_PAD)
    left, right = 0, W
    cam = HOTSPOTS.get("camera")
    if cam and cam[3] > dy0 and cam[0] < W / 2:
        left = int(cam[2])
    ft = HOTSPOTS.get("ft")
    if ft and ft[3] > dy0 and ft[2] > W / 2:
        right = int(ft[0])
    return left, right


@lru_cache(maxsize=48)
def _railbtn_face(w: int, h: int, kind: str) -> Image.Image:
    """kiosk .railbtn / [aria-current=page]. kind: rest | current."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if kind == "current":
        top, bot = (0x3C, 0x42, 0x4A), (0x2F, 0x34, 0x3B)
        border = (0x3A, 0x40, 0x48, 255)
        hi = (255, 255, 255, 31)
    else:
        top, bot = (0x31, 0x35, 0x3C), (0x27, 0x2A, 0x30)
        border = (0x15, 0x17, 0x1B, 255)
        hi = (255, 255, 255, 22)
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,)
        d.line((1, y, w - 2, y), fill=c)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=3, outline=border)
    d.line((3, 1, w - 4, 1), fill=hi)
    if kind == "current":
        d.rectangle((2, h - 3, w - 3, h - 1), fill=EMBER + (255,))
    return im


# the User 2026-09-09: "painfully slow as one click at a time … 5 deg adjustments
# left/right and 2 degrees up down". One tick = one click; a hold repeats it
# every 200 ms. The page's JOY_STEP_X/Y are the same numbers.
PAN_STEP_X_DEG = 5.0    # pan, per tick
PAN_STEP_Y_DEG = 2.0    # tilt, per tick
PAN_STEP_DEG = PAN_STEP_X_DEG   # the old single step, kept as the pan step
# A D-pad, clockwise from the top, home in the middle (the User 2026-09-07: "can
# they go in a square shape … up, r, dn, left like playstation — clockwise").
# col, row on a 3x3 grid.
CAM_KEYS = (("▲", "cam_u", 0, 1, 1, 0), ("▶", "cam_r", 1, 0, 2, 1),
            ("▼", "cam_d", 0, -1, 1, 2), ("◀", "cam_l", -1, 0, 0, 1),
            ("⌂", "cam_home", 0, 0, 1, 1))


def pan_vector(name: str, step: float | None = None) -> tuple[float, float]:
    """Arrow name → (dx, dy) degrees for one tick of a held key. Up is +dy.
    Pan steps PAN_STEP_X_DEG, tilt PAN_STEP_Y_DEG (step overrides both)."""
    for _glyph, n, sx, sy, _c, _r in CAM_KEYS:
        if n == name:
            return (round(sx * (PAN_STEP_X_DEG if step is None else step), 3),
                    round(sy * (PAN_STEP_Y_DEG if step is None else step), 3))
    return 0.0, 0.0


def _wash(im: Image.Image, box, alpha: int) -> None:
    """A translucent GROUND wash laid OVER a drawn control (alpha_composite),
    the page's opacity — never ImageDraw.rectangle, which replaces pixels."""
    x, y, w, h = (int(round(float(v))) for v in box)
    if w <= 0 or h <= 0:
        return
    tile = Image.new("RGBA", (w, h), GROUND + (max(0, min(255, int(alpha))),))
    im.alpha_composite(tile, (x, y))


def render_chrome(g: Geom, p: dict, *, on: bool, menu: bool, rail_top: int = 0) -> Image.Image:
    """Kiosk #rail + #arToggle/#arZoom + #ftCtl + fold carets, on the glass.

    Geometry copies display/voiceonly-home.html: control `em` is the chip's
    own .42em font (so left:.9em == --page-left), not body `u`. ⋯ after CP
    is the wallpaper-only Advanced (folders); kiosk #menu stays off this bar.
    rail_top: GNOME panel height on the wallpaper (kiosk fullscreen = 0).
    """
    W, H, u = g.W, g.H, g.u
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    CHROME_HITS.clear()
    CHROME_PAINT.clear()
    RAIL_BAND.clear()
    # Everything here rides the idle together (on=False draws nothing):
    # rail, corner dials, #ftCtl, fold carets, contracted bars, the oracle
    # dot and Advanced. the User 2026-09-06, later the same day: "make all
    # arrows and bars disappear in that 3s window all at once" — which
    # superseded the morning's "the way back stays on the glass", once
    # motion came from Mutter's idle monitor and the way back was the mouse.
    d = ImageDraw.Draw(im)
    hair = HAIR + (255,)
    ink3 = INK3 + (255,)
    ember = EMBER + (255,)
    # #arToggle / #ftCtl font-size:.42em of body; padding/left/bottom em of that.
    fs = 0.42 * u
    fctl = font("mono", fs)
    f_rail = font("mono", _RAIL_FS)
    f_fold = font("mono", 0.62 * u)
    f_lbl = font("mono", 0.58 * u)

    def hit(name, box):
        CHROME_HITS[name] = tuple(int(v) for v in box)
        if name not in PAINT_SKIP:
            CHROME_PAINT[name] = CHROME_HITS[name]

    def paint(name, box):
        CHROME_PAINT[name] = tuple(int(v) for v in box)

    def a(rgb, op):
        return rgb + (max(0, min(255, int(255 * op))),)

    def railbtn(x, y, w, h, label, name, kind="rest", hover=False):
        face = _railbtn_face(int(w), int(h), kind)
        if hover:
            face = _brighten(face, 1.16)      # .railbtn:hover { filter: brightness(1.16) }
        im.alpha_composite(face, (int(x), int(y)))
        sym = len(label) == 1
        ff = font("mono", 15) if sym else f_rail
        track = 0.0 if sym else _RAIL_TRACK
        tw = _text_w(d, label, ff, track)
        fg = _RAIL_FG_ON if kind == "current" else _RAIL_FG
        if hover:
            fg = tuple(min(255, int(round(v * 1.16))) for v in fg[:3]) + (255,)
        _text(d, (x + (w - tw) / 2, y + (h - ff.size) / 2 - 1), label, ff, fg, track)
        hit(name, (x, y, x + w, y + h))

    def rail_tip(cx, ytop, text, above=False, left=False):
        """#rail .tip: 8px under the key, 8/11 padding, #0A0C0F on a #3A3020
        hairline, radius 3, 11px mono in #99A0A9, max 240 wide, and the
        page's 0 6px 19px shadow. above=True hangs it over a control at the
        foot of the glass (the page's #arToggle .tip sits bottom:100%)."""
        f_tip = font("mono", 11)
        lines = _wrap_greedy(d, text, f_tip, 240 - 22)
        lh = int(round(11 * 1.4))
        tw = max(d.textlength(ln, font=f_tip) for ln in lines)
        bw, bh = int(tw + 22), int(lh * len(lines) + 16)
        if left:
            # beside a dock tile: cx is the tile's left edge, ytop its centre
            bx0 = int(max(4, cx - 8 - bw))
            by0 = int(max(4, min(H - bh - 4, ytop - bh / 2)))
        else:
            bx0 = int(max(4, min(W - bw - 4, cx - bw / 2)))
            by0 = int(ytop - 8 - bh) if above else int(ytop + 8)
        m = 30
        sh = Image.new("RGBA", (bw + 2 * m, bh + 2 * m), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((m, m + 6, m + bw, m + bh + 6), radius=3, fill=(0, 0, 0, 153))
        im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(9.5)), (bx0 - m, by0 - m))
        dd = ImageDraw.Draw(im)
        dd.rounded_rectangle((bx0, by0, bx0 + bw, by0 + bh), radius=3, fill=(10, 12, 15, 255),
                             outline=(0x3A, 0x30, 0x20, 255))
        for i, ln in enumerate(lines):
            dd.text((bx0 + 11, by0 + 8 + i * lh + (lh - 11) / 2 - 1), ln, font=f_tip, fill=(0x99, 0xA0, 0xA9, 255))
        paint(f"tip_{int(cx)}_{int(ytop)}", (bx0 - 10, by0 - 6, bx0 + bw + 10, by0 + bh + 18))   # box + its shadow

    def quiet_chip(x, y, label, name, lit=False, min_w=0.0, style="chip"):
        """#arToggle / #ftBlur / #ftZoom: hair border, no fill, opacity .4 / .7.
        Under the pointer the page lifts the word off the room — opacity 1,
        ember-2 ink on an ember hairline over an ember .12 wash (#arToggle:hover);
        the pan keys (.joykey:hover) go ember on ember-deep with no wash."""
        s = label.upper()
        tw = _text_w(d, s, fctl, 0.2)
        pad_x = 0.42 * fs
        pad_y = 0.18 * fs
        w = max(tw + 2 * pad_x, float(min_w))
        h = fs + 2 * pad_y
        hov = _inside(p.get("ptr"), (x, y, x + w, y + h))
        op = 0.7 if lit else 0.4
        fg = a(EMBER, op) if lit else a(INK3, op)
        bd = a(EMBER_DEEP, op) if lit else a(HAIR, op)
        fill = None
        if style == "joy" and name == p.get("pad_key"):
            # HELD / just clicked: the page's .joykey.held (ember-2 on ember)
            # plus a wash, so a press shows (the User 2026-09-09: "add a color
            # effect on click")
            fg, bd, fill = a(EMBER2, 1.0), a(EMBER, 1.0), a(EMBER, 0.22)
        elif hov and style == "joy":
            fg, bd = a(EMBER, 1.0), a(EMBER_DEEP, 1.0)
        elif hov:
            fg, bd, fill = a(EMBER2, 1.0), a(EMBER, 1.0), a(EMBER, 0.12)
        d.rounded_rectangle((x, y, x + w, y + h), radius=2, fill=fill, outline=bd)
        _text(d, (x + (w - tw) / 2, y + pad_y - 1), s, fctl, fg, 0.2)
        hit(name, (x, y, x + w, y + h))
        return w, h

    def step_size():
        gap = 0.25 * fs
        pad_x = 0.34 * fs
        pad_y = 0.16 * fs
        minus_w = _text_w(d, "−", fctl, 0.0) + 2 * pad_x
        plus_w = _text_w(d, "+", fctl, 0.0) + 2 * pad_x
        vw = 2.2 * fs
        return minus_w + gap + vw + gap + plus_w, fs + 2 * pad_y, minus_w, plus_w, vw, gap, pad_x, pad_y

    def quiet_step(x, y, val, name):
        """#arZoom / #ftStep: − 1.00 + in ink, not ember. The cluster lifts to
        .95 while the pointer is on it (#ftStep:hover) so the number you are
        changing is readable; the key under the pointer answers in ink on an
        ink-2 hairline over a pale wash (#ftStep button:hover)."""
        total, bh, w_lo, w_hi, vw, gap, pad_x, pad_y = step_size()
        ptr = p.get("ptr")
        op = 0.95 if _inside(ptr, (x, y, x + total, y + bh)) else 0.4
        fg = a(INK3, op)
        bd = a(HAIR, op)

        def key(xx, glyph, tag, ww):
            if _inside(ptr, (xx, y, xx + ww, y + bh)):
                d.rounded_rectangle((xx, y, xx + ww, y + bh), radius=2, fill=a(INK, 0.10), outline=a(INK2, 1.0))
                kfg = a(INK, 1.0)
            else:
                d.rounded_rectangle((xx, y, xx + ww, y + bh), radius=2, outline=bd)
                kfg = fg
            tw = _text_w(d, glyph, fctl, 0.0)
            d.text((xx + (ww - tw) / 2, y + pad_y - 1), glyph, font=fctl, fill=kfg)
            hit(tag, (xx, y, xx + ww, y + bh))

        key(x, "−", name + "_lo", w_lo)
        vs = f"{float(val):.2f}"
        tw = _text_w(d, vs, fctl, 0.14)
        d.text((x + w_lo + gap + (vw - tw) / 2, y + pad_y - 1), vs, font=fctl, fill=a(INK, op))
        key(x + w_lo + gap + vw + gap, "+", name + "_hi", w_hi)
        return total, bh

    # ONE caret size everywhere (the User 2026-09-06: "Size the collapse arrows to
    # the smaller size they are not uniform"): bars, strips and the vitals
    # fold all draw this triangle, 10 px wide, 8 tall, never a font glyph.
    CARET_S = 5

    def tri(cx, cy, up, col, s=CARET_S, d_=None):
        if up:
            pts = [(cx, cy - s), (cx - s, cy + s * 0.65), (cx + s, cy + s * 0.65)]
        else:
            pts = [(cx - s, cy - s * 0.65), (cx + s, cy - s * 0.65), (cx, cy + s)]
        (d_ or d).polygon(pts, fill=col)

    def tri_h(cx, cy, right, col, s=CARET_S, d_=None):
        """The same caret turned sideways: ▶ (right=True) or ◀."""
        if right:
            pts = [(cx + s, cy), (cx - s * 0.65, cy - s), (cx - s * 0.65, cy + s)]
        else:
            pts = [(cx - s, cy), (cx + s * 0.65, cy - s), (cx + s * 0.65, cy + s)]
        (d_ or d).polygon(pts, fill=col)

    FOLD_SQ = 24      # the fold squares: small, like the other carets' boxes

    def kiosk_bar(x, y, w, h, name, *, up, label=None, style="pip"):
        """The contracted corner, as the page lands it on the glass. Left
        (#pipwrap.bar) sits inside the .32 group every camera-corner rule
        inherits (body.chan-live #pipwrap), so its ink-3 border, its
        rgba(16,19,26,.42) plate, its label and its ▲ arrive at .32 — muted,
        still there ("muted but visible collapse bars", the User 2026-09-07).
        Right (#ftPop.bar) is its own .55 plate with the pop's rounded
        corners, amber hairline and 46px shadow, its ▲ at .32. Both are in
        the chrome image, so both fade with the rail."""
        hit(name, (x, y, x + w, y + h))
        # ONE look for every collapsed box (the User 2026-09-08: "very thin outlines
        # w pale white arrows, and amber on hover ... Camera square is the
        # example"): the Brave camera bar's 1 px ink-3 outline at the bars'
        # .32, a ghost of GROUND, the pale caret. The FT bar keeps its pop's
        # rounded corners and drops the amber hairline and the shadow.
        # the WHOLE box goes amber under the pointer (the User 2026-09-08)
        bhov = _inside(p.get("ptr"), (x, y, x + w, y + h))
        b_fill = (EMBER + (30,)) if bhov else (GROUND + (34,))
        b_line = (EMBER + (255,)) if bhov else (INK3 + (FOLD_INK_A,))   # the Brave camera bar's hairline
        if style == "ft":
            r = int(0.45 * u)
            dd = ImageDraw.Draw(im)
            dd.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=r, fill=b_fill, outline=b_line)
        else:
            d.rectangle((x, y, x + w - 1, y + h - 1), fill=b_fill, outline=b_line)
            if label:
                f_pl = font("mono", 0.5 * u)
                _text(d, (x + 0.45 * u, y + (h - f_pl.size) / 2 - 1), label, f_pl, INK3 + (FOLD_INK_A,), 0.22)
        dd = ImageDraw.Draw(im)
        col = (EMBER + (255,)) if _inside(p.get("ptr"), (x, y, x + w, y + h)) else (INK + (FOLD_INK_A,))
        tri(x + w / 2, y + h / 2, up, col, s=CARET_S, d_=dd)

    CARET_HIT_W = 44    # the fold's click target: the caret's own square, not the whole top band

    def caret_strip(x, y, w, h, name, *, up, box=None):
        """#pipfold / #ftFold: a top scrim and a centred triangle, ONLY while
        the pointer is on that tile — the page's `opacity:0` until
        `#pipwrap:hover` (the User 2026-09-07: "collapse arrows only on hover").
        Muted white (.32 on the glass, see FOLD_INK_A), ember once the pointer
        is on the strip itself (#pipfold:hover). The hit is registered either
        way, so the strip is reachable before it is drawn."""
        # THE CARET TAKES THE CLICK, NOT THE BAND (the User 2026-09-09: "it takes
        # two clicks now" — a click in the top band folded the window, the
        # next unfolded it, the third opened it). The scrim is the hover's
        # look; only the caret's square folds, the rest of the box opens.
        cxm = x + w / 2
        hit(name, (cxm - CARET_HIT_W / 2, y, cxm + CARET_HIT_W / 2, y + h))
        ptr = p.get("ptr")
        if not _inside(ptr, box):
            return
        hh = max(1, int(h))
        for i in range(hh):
            aa = int(FOLD_SCRIM_A * (1.0 - i / hh))
            if aa < 3:
                break
            d.line((x, y + i, x + w, y + i), fill=(13, 16, 22, aa))
        col = (EMBER + (255,)) if _inside(ptr, (x, y, x + w, y + h)) else (INK + (FOLD_INK_A,))   # full amber under the pointer
        tri(x + w / 2, y + h / 2, up, col, s=CARET_S)

    # geometry the persistent carets share with the idle corners
    pip = ANCHORS.get("pip") or [g.page_left, H - g.pip_edge - g.pip_h, g.pip_w, g.pip_h]
    px, py, pw, ph = (float(v) for v in pip[:4])
    chip_h = fs + 2 * (0.18 * fs)
    ly = int(round(H - 0.85 * fs - chip_h))
    min_chip = 4.6 * fs
    rail_right = W
    tips = []                    # (cx, y, text, above) — drawn last, over everything
    hover_border = None
    if on:
        # ── #rail: VO | WALL · KREW · COMP · CP · ⋯  (11.5px keys, px padding)
        # SYMBOLS, not words (the User 2026-09-06: "Change the icons to symbols vs CP
        # COMP and on that same bar offer the advanced menu"). DejaVu Sans Mono
        # carries all of these. ◐ voice only · ⌂ wall · ⚒ krew · ▶ composer ·
        # ⚙ control panel · ⋯ advanced.
        # VO is gone (the User 2026-09-06: "Toss VO button though. It is no more
        # than a minimize button now"). The wallpaper IS the glass; the ZOS
        # browser is a toggle in Advanced.
        # ⌂ (WALL) did nothing on the wall itself (the User 2026-09-06: "what does
        # Home button do? Delete if nothing").
        keys = [
            ("krew", "⚒", "rest"),
            ("composer", "▶", "rest"),
            ("panel", "⚙", "rest"),
            ("menu", "⋯", "current" if menu else "rest"),
        ]
        # a key with no page in this edition is not drawn (a pack has no Krew, 2026-09-28)
        import house_wall_dock as _dk
        keys = [k for k in keys if k[0] not in _dk.DOCK_KEYS or _dk.DOCK_KEYS[k[0]][3] != ""]
        keys = [k for k in keys if k[0] != "menu" or _dk.advanced_on()]   # the old panel is the house's (2026-10-29)
        bh = int(round(f_rail.size + 2 * _RAIL_PAD_Y + 2))  # content + pad + border-box
        pad_l, pad_r, pad_t = 14, 14, 8
        bar_h = pad_t + bh + pad_t
        # ONE BAND AT THE BOTTOM, SPLIT WITH THE DOCK (the User 2026-09-08: "cede
        # the top to Ubuntu and split the lower rail with the folders bar"):
        # the keys sit in the dock's row, left of its first tile, on the
        # tiles' centre line. The top of the glass is GNOME's — rail_top is
        # ignored, there is no plate hung from the bar and no slide from
        # under it; the row rides the chrome fade like the dock does.
        # NO FOLD ON THE RAIL (the User 2026-09-08: "menu auto-hides and so needs
        # no collapse arrow. You can remove that").
        widths = []
        f_sym = font("mono", 15)   # symbols read at 15px where the 11.5px words did
        for _, lab, _ in keys:
            track = 0.0 if len(lab) == 1 else _RAIL_TRACK
            tw = _text_w(d, lab, f_sym if len(lab) == 1 else f_rail, track)
            widths.append(max(int(math.ceil(tw + 2 * _RAIL_PAD_X)), _RAIL_MIN_W))
        import house_wall_dock as _dock
        n_dock = len(list(p.get("dock") or [])[:_dock.MAX_TILES])
        total = sum(widths) + _RAIL_GAP * (len(keys) - 1) if keys else 0
        dx0, _dy0, hy0, _tw = dock_row(W, H, n_dock, rail_w=total if p.get("dock") else 0, span=band_span(W, H),
                                       n_extra=len(_dock_places(p)) + 1)
        yk = hy0 + (DOCK_TILE - bh) // 2
        y0 = yk - pad_t
        if keys:
            x = BAND["rail_x"] if p.get("dock") else (W - total) // 2
            x = max(pad_l, int(x))
            d = ImageDraw.Draw(im)
            xk = x
            # dock_ubuntu: the keys are launchers in Ubuntu's dock
            # (house_wall_dock.DOCK_KEYS). Laid out, not drawn: rail_right
            # still hangs Advanced where ⋯ sat.
            draw_keys = not p.get("dock_ubuntu")
            for i, ((name, lab, kind), ww) in enumerate(zip(keys, widths)):
                if draw_keys:
                    hov = _inside(p.get("ptr"), (xk, yk, xk + ww, yk + bh))
                    railbtn(xk, yk, ww, bh, lab, name, kind, hover=hov)
                    if hov and RAIL_TIPS.get(name):
                        tips.append((xk + ww / 2, yk, RAIL_TIPS[name], True))   # above the key
                xk += ww + _RAIL_GAP
            rail_right = xk - _RAIL_GAP
            plate_box = (x - pad_l, y0, x + total + pad_r, y0 + bar_h)
            if draw_keys:
                hit("rail_plate", plate_box)      # the whole bar hovers (inert on click)

        # ── corners. em is the .42em chip font, so left:.9em == --page-left.

        # AR at left:.9em (kiosk), then the hole-width dial, then RES and BRT
        # (the User 2026-09-06: "Missing my adjustment buttons under the lower
        # corner tiles"). Joystick moved onto the camera box 09-07; the corner
        # is free. Same hits Advanced already posts (density_lo / brightness_lo).
        # AR is the KIOSK's Hybrid hole (screen.crissa_hole from the house service), mirrored:
        # the window copies that flag into p["hole_ar"] and the click POSTs
        # crissa_hole_on/off, so the wall and Brave show one state (the User
        # 2026-09-06: "Keep modes linked between Brave and Wallpaper").
        lx = int(round(0.9 * fs))
        hole_on = bool(p.get("hole_ar", False))
        aw, ah = quiet_chip(lx, ly, "AR", "ar", lit=hole_on)
        if _inside(p.get("ptr"), (lx, ly, lx + aw, ly + ah)):
            tips.append((lx + aw / 2, ly, ("Window is scanning her slice. Click for her own framing."
                                            if hole_on else
                                            "Let the Brave window scan her fixed frame. The dial sets how wide."), True))
        zx = max(int(round(3.55 * fs)), int(lx + aw + 0.25 * fs))
        # THE HOLE-WIDTH DIAL, right of AR — the page's #arZoom, mirrored (the User
        # 2026-09-07: "add that dial to the wallpaper. Missed it"). − widens the
        # slice the Brave window scans of her poster, + pushes in; 1.00 is the
        # wall's own scale; .25 to 4 in quarter steps, the server clamps the
        # same. Same ink as AR: one switch, one knob. A shared action now.
        zw, zh = quiet_step(zx, ly, p.get("hole_zoom", 1.0), "ar_zoom")
        if _inside(p.get("ptr"), (zx, ly, zx + zw, ly + zh)):
            tips.append((zx + zw / 2, ly, ("Zoom on her. 1.00 is her whole film; + comes in on her face, − goes back out. Same number on Brave."
                                            if p.get("zoom_is_frame") else
                                            "Hole width — how much of her poster the Brave window scans. − widens, + pushes in. 1.00 is the wall's own scale."), True))
        dx = int(zx + zw + 0.25 * fs)
        dw, dh = quiet_step(dx, ly, p.get("density", 1.0), "density")
        if _inside(p.get("ptr"), (dx, ly, dx + dw, ly + dh)):
            tips.append((dx + dw / 2, ly, "Characters per inch of glass. Higher is finer. Wheel it. Same number on Brave.", True))
        bx = int(dx + dw + 0.25 * fs)
        bw, bh = quiet_step(bx, ly, p.get("brightness", 0.75), "brightness")
        if _inside(p.get("ptr"), (bx, ly, bx + bw, ly + bh)):
            tips.append((bx + bw / 2, ly, "How hard the room burns. Only the ASCII surface moves. Same number on Brave.", True))
        zx_after = int(bx + bw + 0.25 * fs)
        # CAMERA JOYSTICK (the User 2026-09-06): "aim camera joystick for User to
        # SHOW Crissa something". ◀ ▲ ▼ ▶ nudge the gimbal, ⌂ gives it back.
        # Needs AUTH (presence refuses otherwise); Secure drops on a nudge
        # unless the keyboard hold is on. On the camera box when it is up;
        # otherwise after BRT so the corner row stays one reach.
        # THE JOYSTICK LIVES ON THE CAMERA BOX (the User 2026-09-07: "put the
        # joystick on the box. Just along the bottom right corner and keep it
        # there if expanded"): four arrows you HOLD to pan, then ⌂, laid along
        # the box's bottom-right inside corner in both sizes.
        cam_box = HOTSPOTS.get("camera")
        authed_cam = bool(p.get("cam_authed", False))
        # the keys are a ghost on the resting picture and come up when the
        # pointer is on the box, like the kiosk's #pipjoy
        ptr_c = p.get("ptr")
        cam_hover = bool(ptr_c and cam_box and cam_box[0] <= ptr_c[0] <= cam_box[2]
                         and cam_box[1] <= ptr_c[1] <= cam_box[3])
        big_cam = bool(cam_box) and (cam_box[2] - cam_box[0]) > 0.5 * W
        gap_k = 0.16 * fs
        kh = fs + 2 * 0.18 * fs
        cell = max(kh, min_chip * (0.62 if big_cam else 0.5))
        pad_w = cell * 3 + gap_k * 2
        hover_border = cam_box if (cam_box and cam_hover) else None    # drawn after the caret scrim, below
        if cam_box:
            pad_in = (0.55 if big_cam else 0.30) * g.u
            gx = cam_box[2] - pad_in - pad_w
            gy = cam_box[3] - pad_in - pad_w
            plate = Image.new("RGBA", (int(pad_w + 2 * gap_k), int(pad_w + 2 * gap_k)),
                              GROUND + (120 if cam_hover else 40,))
            im.alpha_composite(plate, (int(gx - gap_k), int(gy - gap_k)))
            d = ImageDraw.Draw(im)
        else:
            # camera folded to a bar: the pad falls back beside AR so the User
            # never loses the pan
            gx, gy = zx_after, ly + kh - pad_w
        for glyph, name, _sx, _sy, col, row in CAM_KEYS:
            kx = gx + col * (cell + gap_k)
            ky = gy + row * (cell + gap_k) + (cell - kh) / 2
            quiet_chip(kx, ky, glyph, name, lit=False, min_w=cell, style="joy")
            # the dims are COMPOSITED, never drawn: ImageDraw.rectangle
            # replaces pixels, so the wash wiped the key and left only its
            # square (the User 2026-09-09: "Joystick not even visible now on
            # background version, only the hit box")
            if not authed_cam:
                # dim: drawn, not offered (the hit stays so the click can say why)
                _wash(im, (kx, ky, cell, kh), 140)
            elif cam_box and not cam_hover and name != p.get("pad_key"):
                # the resting wash; never over the key that is down (its glow
                # is the click's answer — the User 2026-09-09)
                _wash(im, (kx, ky, cell, kh), 118)
        # #ftCtl: right:pip-edge, width:pip-w, space-between: step · ROOM · Blur
        ctl_x = int(round(W - g.pip_edge - g.pip_w))
        ctl_w = int(round(g.pip_w))
        mode_lab = "ROOM" if p.get("ft_mode") == "room" else "FT"
        sw = step_size()[0]
        mw = max(_text_w(d, mode_lab, fctl, 0.2) + 2 * 0.42 * fs, min_chip)
        bw = max(_text_w(d, "BLUR", fctl, 0.2) + 2 * 0.42 * fs, min_chip)
        # centred under the pop (the User 2026-09-06: "Could we center those
        # knobs too?"), one chip gap between the three
        gap2 = 0.55 * fs
        total = sw + gap2 + mw + gap2 + bw
        ww = max(_text_w(d, "WALL", fctl, 0.2) + 2 * 0.42 * fs, min_chip) if p.get("wall_key") else 0.0
        if ww:
            total += gap2 + ww                     # round 26: the WALL key after BLUR (house_wall_ascii.WALL_KEY_ON)
        cx0 = ctl_x + (ctl_w - total) / 2.0
        quiet_step(cx0, ly, p.get("ft_size", 1), "ft_size")
        quiet_chip(cx0 + sw + gap2, ly, mode_lab, "ft_mode",
                   lit=p.get("ft_mode") == "room", min_w=min_chip)
        # one chip, three veils (the User 2026-09-09): off → Blur → Fade → off
        quiet_chip(cx0 + sw + gap2 + mw + gap2, ly, "Fade" if p.get("ft_fade") else "Blur", "blur",
                   lit=bool(p.get("blur") or p.get("ft_fade")), min_w=min_chip)
        if ww:
            quiet_chip(cx0 + sw + gap2 + mw + gap2 + bw + gap2, ly, "WALL", "wall_off", lit=True, min_w=min_chip)

    # dock_ubuntu (the User 2026-09-16 "make ubuntu our dock"): the row still
    # PLACES the rail above (dock_row sets BAND.rail_x, so the keys sit where
    # they sat, left of Ubuntu's dock) but none of it is drawn or hit.
    if p.get("dock") and on and not p.get("dock_ubuntu"):
        # the dock rides the chrome both ways (the User 2026-09-09 pm: "make the
        # dock auto hide with the rest on that same timing … make it hide
        # when opened like it is now" — the morning's open-dock-stays is gone)
        # THE DOCK STAYS THROUGH THE IDLE (the User 2026-09-09: "all my icons are
        # disappearing. I only want that if they are collapsed"): open, the
        # tiles are drawn on the idle image too and the window lays the
        # fading chrome over them; folded, the handle is chrome and fades.
        # THE WALL'S OWN DOCK (the User 2026-09-08): GNOME's favourites as a row of
        # icons floating on the glass — no tiles, no plate ("just icon on
        # transparent") — at the bottom centre, riding the chrome. A HANDLE at
        # the row's left end folds it: the corner bars' fold box made square,
        # one icon tall (pale ink-3 outline on a ghost of GROUND, the caret
        # muted white, ember under the pointer): ◀ while open, drawn while the
        # pointer is on the row; folded, the same square with ▶ stays where
        # the row's left end was ("closes to the leftmost extent"). Click on
        # an icon = gtk-launch. dash-to-dock's reveal zone fought the wall's
        # corner knobs; this has no reveal zone at all.
        import house_wall_dock as _dock
        dock = list(p.get("dock"))[:_dock.MAX_TILES]
        n_ = len(dock)
        sq = DOCK_TILE
        row_h = DOCK_TILE + 2 * DOCK_PAD
        placed = dict(BAND)                 # the rail block placed the group this frame
        places_ = _dock_places(p)
        n_x = len(places_) + 1               # the folders, then Clear desktop
        dx0, dy0, hy0, total_w = dock_row(W, H, n_, span=band_span(W, H), n_extra=n_x)
        if placed.get("n") == n_ and placed.get("dx0") is not None:
            dx0 = int(placed["dx0"])
            BAND.update(placed)
        # the handle: ▶ at the row's RIGHT end while open, and the same square
        # with a white ▶ at the row's LEFT end when folded (the User 2026-09-08:
        # "the right arrow to appear at the right side and turn into the
        # white arrow pointing right on the left when collapsed")
        icons_w = (n_ + 1) * DOCK_TILE + n_ * DOCK_GAP + dock_extras_w(n_x)     # the favourites, APPS, the seam, the desk's tiles
        handle_open = (dx0 + icons_w + DOCK_GAP, hy0, dx0 + icons_w + DOCK_GAP + sq, hy0 + sq)
        handle_folded = (dx0, hy0, dx0 + sq, hy0 + sq)

        def dock_handle(handle, right):
            hh = _inside(p.get("ptr"), handle)
            d.rectangle((handle[0], handle[1], handle[2] - 1, handle[3] - 1),
                        fill=(EMBER + (30,)) if hh else (GROUND + (34,)), outline=(EMBER + (255,)) if hh else (INK3 + (FOLD_INK_A,)))
            # amber, full, under the pointer — like every fold on the glass
            col = (EMBER + (255,)) if hh else (INK + (FOLD_INK_A,))
            tri_h(handle[0] + sq / 2, handle[1] + sq / 2, right, col, s=CARET_S)

        if p.get("dock_bar"):
            dock_handle(handle_folded, True)      # folded: a white ▶ at the left end
            hit("dock_fold", handle_folded)
        else:
            handle = handle_open
            icon_px = 30            # the icon: about the nav keys beside it (the User 2026-09-09: "maybe just 20% smaller")
            for i, it in enumerate(dock):
                tx = dx0 + i * (DOCK_TILE + DOCK_GAP)
                ty = hy0
                box = (tx, ty, tx + DOCK_TILE, ty + DOCK_TILE)
                hov = _inside(p.get("ptr"), box)
                icon = _dock.tile(it.get("png"), icon_px)
                if icon is not None:
                    if hov:
                        icon = _brighten(icon, 1.16)
                    im.alpha_composite(icon, (tx + (DOCK_TILE - icon_px) // 2, ty + (DOCK_TILE - icon_px) // 2))
                else:
                    # no icon file: the app's initial in the rail's ink
                    f_i = font("mono", 18)
                    ch_ = (str(it.get("name") or it.get("id") or "?")[:1]).upper()
                    tw = d.textlength(ch_, font=f_i)
                    d.text((tx + (DOCK_TILE - tw) / 2, ty + (DOCK_TILE - 18) / 2 - 1), ch_, font=f_i, fill=_RAIL_FG)
                hit(f"dock_{i}", box)
                # GNOME's dots: one per live instance, up to three, under the icon
                nrun = int(it.get("running") or 0)
                if nrun:
                    cy_ = ty + DOCK_TILE - 2
                    for k in range(min(3, nrun)):
                        cx_ = tx + DOCK_TILE / 2 + (k - (min(3, nrun) - 1) / 2) * 8
                        d.ellipse((cx_ - 2.5, cy_ - 2.5, cx_ + 2.5, cy_ + 2.5), fill=INK2 + (230,))
                pick = p.get("dock_pick") or {}
                if pick.get("i") == i and pick.get("wins"):
                    # the stack is up for this tile: an ember bar under it
                    d.rounded_rectangle((tx + 6, ty + DOCK_TILE + 3, tx + DOCK_TILE - 6, ty + DOCK_TILE + 5), radius=1, fill=EMBER + (255,))
                if hov and it.get("name") and pick.get("i") != i:
                    tips.append((tx + DOCK_TILE / 2, ty, str(it["name"]), True))   # above the icon
            # APPS (the User 2026-09-08: "How would I see my Apps screen now …
            # toss that in the dock. Trapped a bit in the wallpaper"): GNOME's
            # nine dots after the last favourite; a click asks the shell for
            # its applications grid (org.gnome.Shell.ShowApplications).
            ax = dx0 + n_ * (DOCK_TILE + DOCK_GAP)
            abox = (ax, hy0, ax + DOCK_TILE, hy0 + DOCK_TILE)
            ahov = _inside(p.get("ptr"), abox)
            acol = (EMBER + (255,)) if ahov else (INK2 + (230,))
            for r_ in range(3):
                for c_ in range(3):
                    cx_ = ax + DOCK_TILE / 2 + (c_ - 1) * 9
                    cy_ = hy0 + DOCK_TILE / 2 + (r_ - 1) * 9
                    d.ellipse((cx_ - 2.5, cy_ - 2.5, cx_ + 2.5, cy_ + 2.5), fill=acol)
            hit("dock_apps", abox)
            if ahov:
                tips.append((ax + DOCK_TILE / 2, hy0, "Apps — GNOME's applications grid", True))
            # THE DESK'S OWN TILES, past a seam (Desktop + Deliverables folders).
            # The seam says launchers end here; no divider line.
            plx = ax + DOCK_TILE + DOCK_SEAM        # NOT `px`: that is the camera box's x, read by its caret below (2026-09-09: the caret walked off to x≈1900)
            for k, pl in enumerate(places_):
                pbox = (plx, hy0, plx + DOCK_TILE, hy0 + DOCK_TILE)
                phov = _inside(p.get("ptr"), pbox)
                icon = _dock.tile(pl.get("png"), icon_px)
                if icon is not None:
                    if phov:
                        icon = _brighten(icon, 1.16)
                    im.alpha_composite(icon, (plx + (DOCK_TILE - icon_px) // 2, hy0 + (DOCK_TILE - icon_px) // 2))
                else:
                    # no theme PNG yet: a folder outline in the dots' ink
                    fc = (EMBER + (255,)) if phov else (INK2 + (230,))
                    fx, fy = plx + DOCK_TILE / 2, hy0 + DOCK_TILE / 2
                    d.rounded_rectangle((fx - 11, fy - 6, fx + 11, fy + 8), radius=2, outline=fc, width=2)
                    d.rectangle((fx - 11, fy - 9, fx - 2, fy - 6), fill=fc)      # the tab
                hit(f"dock_place_{k}", pbox)
                if phov and pl.get("name"):
                    tips.append((plx + DOCK_TILE / 2, hy0, str(pl["name"]), True))
                plx += DOCK_TILE + DOCK_GAP
            # CLEAR DESKTOP: a window sinking to a baseline. Mutter's own
            # show-desktop, so it is a toggle: while the desk is clear the
            # caret turns up and the tile says so — one press brings every
            # window back (the hint clears itself when the User opens or
            # raises a window, so the glyph follows the desk, not our click).
            clear_ = bool(p.get("desk_clear"))
            cbox = (plx, hy0, plx + DOCK_TILE, hy0 + DOCK_TILE)
            chov = _inside(p.get("ptr"), cbox)
            cc = (EMBER + (255,)) if chov else (INK2 + (230,))
            cx_, cy_ = plx + DOCK_TILE / 2, hy0 + DOCK_TILE / 2
            if clear_:
                # sunk: a short bar on the baseline, the caret pointing up
                d.rectangle((cx_ - 9, cy_ + 6, cx_ + 9, cy_ + 8), fill=cc)
                d.polygon([(cx_, cy_ - 8), (cx_ - 5, cy_ - 1), (cx_ + 5, cy_ - 1)], fill=cc)
            else:
                # up: a window outline with its title band, the caret pointing down to the bar
                d.rectangle((cx_ - 9, cy_ - 11, cx_ + 9, cy_ + 1), outline=cc, width=2)
                d.rectangle((cx_ - 9, cy_ - 11, cx_ + 9, cy_ - 8), fill=cc)
                d.polygon([(cx_, cy_ + 9), (cx_ - 5, cy_ + 3), (cx_ + 5, cy_ + 3)], fill=cc)
            hit("dock_clear", cbox)
            if chov:
                tips.append((px + DOCK_TILE / 2, hy0, "Bring the windows back" if clear_ else "Clear desktop — minimise every window", True))
            # THE WINDOW CHOOSER (the User 2026-09-08: "if two dots can I hover to
            # display choice of windows?"): over a tile with two or more
            # instances, its windows stack above the icon — one row a title,
            # ember under the pointer; a click raises that window. The wall
            # hands the list in p["dock_pick"] = {i, wins:[{id,title,via}]}
            # and keeps it while the pointer is on the tile or the stack.
            # no window stack under an open Advanced panel: the two pop from
            # the same band and the stack's cards sat over the panel's rows
            # (2026-09-13: a press for Folders resolved to dockwin_0_0)
            pick = {} if menu else (p.get("dock_pick") or {})
            wins = list(pick.get("wins") or [])
            pi = pick.get("i")
            if wins and isinstance(pi, int) and 0 <= pi < n_:
                # THE STACK, RESTYLED (the User 2026-09-11: "can we beautify them
                # at all?"): a card per window — its picture with rounded
                # corners in a hairline frame, the title under it, the
                # hovered card lit ember; "new window" is a hollow card with
                # a plus. A header names the app and counts its windows; the
                # box wears a soft shadow and a caret down to its tile, and
                # the tile itself carries an ember bar while the stack is up.
                # THE STACK WEARS THE RAIL'S CLOTHES (the User 2026-09-11: "match
                # the font on the wall and colors there? Background dusk?"):
                # the tip's dusk (#0A0C0F) on the tip's #3A3020 hairline,
                # radius 3, the tip's shadow, 11px mono in the rail's
                # #99A0A9 — the lit key's #DEE2E7 for the app's name, ember
                # under the pointer as on every key. The new-window card is a
                # narrow box, "+" over New / Window stacked, so a stack of
                # many windows stays small (the User: "so multi pop-ups stay
                # smaller overall").
                app = dock[pi] if pi < len(dock) else {}
                f_w = font("mono", 11)
                f_h = font("mono", 11)
                f_s = font("mono", 10)
                DUSK = (10, 12, 15, 255)
                HAIRLINE = (0x3A, 0x30, 0x20, 255)
                SEP = (0x2B, 0x2F, 0x36, 255)
                FG = (0x99, 0xA0, 0xA9, 255)
                FG_ON = (0xDE, 0xE2, 0xE7, 255)
                tw_ = int(_dock.THUMB_W)
                th_ = int(round(tw_ * 9 / 16))
                PAD, CARD_PAD, LAB_H, NEW_W = 10, 7, 20, 62
                HEAD_H = 26 if DOCK_STACK_PLATE else 0     # bare: no header row
                thumbs = []
                for w in wins:
                    tp = str(w.get("thumb") or "")
                    im_t = None
                    if tp:
                        try:
                            im_t = Image.open(tp).convert("RGBA")
                            im_t.thumbnail((tw_, th_))
                        except Exception:
                            im_t = None
                    thumbs.append(im_t)
                any_pic = any(t is not None for t in thumbs) or any(w.get("via") == "shell" for w in wins)
                real = [w for w in wins if w.get("via") != "new"]
                title_of = lambda w: str(w.get("title") or "untitled")
                nm = str(app.get("name") or "")[:28]
                cnt = "%d window%s" % (len(real), "" if len(real) == 1 else "s")
                if any_pic:
                    cell_w = tw_ + 2 * CARD_PAD
                    cell_h = th_ + 2 * CARD_PAD + LAB_H
                    widths = [NEW_W if w.get("via") == "new" else cell_w for w in wins]
                    bw_ = sum(widths) + (len(wins) - 1) * 6 + 2 * PAD
                    bh_ = HEAD_H + cell_h + 2 * PAD
                else:
                    cell_w = int(min(380, max(200, max(_text_w(d, title_of(w), f_w, 0.0) for w in wins) + 44)))
                    cell_h = 22
                    widths = [cell_w] * len(wins)
                    bw_ = cell_w + 2 * PAD
                    bh_ = HEAD_H + cell_h * len(wins) + 2 * PAD
                # the header never overruns its count
                head_w = int(22 + _text_w(d, nm, f_h, 0.0) + 24 + _text_w(d, cnt, f_h, 0.0) + 2 * PAD)
                if head_w > bw_:
                    if not any_pic:
                        cell_w += head_w - bw_
                        widths = [cell_w] * len(wins)
                    bw_ = head_w
                cx_t = dx0 + pi * (DOCK_TILE + DOCK_GAP) + DOCK_TILE / 2
                bx0 = int(max(4, min(W - bw_ - 4, cx_t - bw_ / 2)))
                by1 = hy0 - 12
                by0 = by1 - bh_
                if DOCK_STACK_PLATE:
                    # the tip's shadow: 0 6px 19px, .6
                    m = 30
                    sh = Image.new("RGBA", (bw_ + 2 * m, bh_ + 2 * m), (0, 0, 0, 0))
                    ImageDraw.Draw(sh).rounded_rectangle((m, m + 6, m + bw_, m + bh_ + 6), radius=3, fill=(0, 0, 0, 153))
                    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(9.5)), (bx0 - m, by0 - m))
                    d.rounded_rectangle((bx0, by0, bx0 + bw_ - 1, by1 - 1), radius=3, fill=DUSK, outline=HAIRLINE)
                    # the caret down to the tile
                    cxi = int(cx_t)
                    d.polygon([(cxi - 6, by1 - 1), (cxi + 6, by1 - 1), (cxi, by1 + 6)], fill=DUSK)
                    d.line([(cxi - 6, by1 - 1), (cxi, by1 + 6), (cxi + 6, by1 - 1)], fill=HAIRLINE, width=1)
                    # header: the app's icon, its name, how many windows
                    hx = bx0 + PAD
                    hy = by0 + PAD - 2
                    ic = _dock.tile(app.get("png"), 16) if app else None
                    if ic is not None:
                        im.alpha_composite(ic, (hx, hy + 1))
                        hx += 22
                    d.text((hx, hy + 2), nm, font=f_h, fill=FG_ON)
                    d.text((bx0 + bw_ - PAD - _text_w(d, cnt, f_h, 0.0), hy + 2), cnt, font=f_h, fill=FG)
                    d.line([(bx0 + PAD, by0 + HEAD_H), (bx0 + bw_ - PAD, by0 + HEAD_H)], fill=SEP, width=1)
                else:
                    # bare: a soft halo under the row of cards so titles read
                    # on any wallpaper; no plate, no header, no caret
                    m = 26
                    ry_top = by0 + HEAD_H + PAD
                    sh = Image.new("RGBA", (bw_ + 2 * m, (by1 - ry_top) + 2 * m), (0, 0, 0, 0))
                    ImageDraw.Draw(sh).rounded_rectangle((m + 6, m + 6, m + bw_ - 6, m + (by1 - ry_top) - 2), radius=10, fill=(0, 0, 0, 120))
                    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(11)), (bx0 - m, ry_top - m))
                    # the halo is NOT registered as paint: over a window the
                    # band would show a rectangle of wallpaper around the
                    # cards (the User 09:2x, "kind of works better but not fully");
                    # the cards register themselves as hits

                def _ellipsis(t, f, max_w):
                    if _text_w(d, t, f, 0.0) <= max_w:
                        return t
                    while len(t) > 1 and _text_w(d, t + "…", f, 0.0) > max_w:
                        t = t[:-1]
                    return t.rstrip() + "…"

                rx = bx0 + PAD
                for k, (w, im_t) in enumerate(zip(wins, thumbs)):
                    is_new = w.get("via") == "new"
                    cw = widths[k]
                    if any_pic:
                        rbox = (rx, by0 + HEAD_H + PAD, rx + cw, by0 + HEAD_H + PAD + cell_h)
                        rx += cw + 6
                    else:
                        ry0 = by0 + HEAD_H + PAD + k * cell_h
                        rbox = (bx0 + PAD, ry0, bx0 + bw_ - PAD, ry0 + cell_h)
                    rh = _inside(p.get("ptr"), rbox)
                    if rh:
                        # #arToggle:hover: ember .12 wash on an ember hairline
                        d.rounded_rectangle((rbox[0], rbox[1], rbox[2] - 1, rbox[3] - 1), radius=3,
                                            fill=EMBER + (31,), outline=EMBER + (255,))
                    ink = (EMBER2 + (255,)) if rh else FG
                    if any_pic:
                        px_ = rbox[0] + CARD_PAD
                        py_ = rbox[1] + CARD_PAD
                        if is_new:
                            # the narrow card: a hairline box, "+" over New / Window
                            bx_ = (rbox[0] + 5, rbox[1] + 5, rbox[2] - 6, rbox[3] - 6)
                            d.rounded_rectangle(bx_, radius=3, outline=(EMBER + (255,)) if rh else SEP, width=1)
                            mx_ = (bx_[0] + bx_[2]) / 2
                            my_ = bx_[1] + (bx_[3] - bx_[1]) * 0.36
                            d.rectangle((mx_ - 9, my_ - 1, mx_ + 9, my_ + 1), fill=ink)
                            d.rectangle((mx_ - 1, my_ - 9, mx_ + 1, my_ + 9), fill=ink)
                            for j, word in enumerate(("New", "Window")):
                                ww_ = _text_w(d, word, f_s, 0.0)
                                d.text((mx_ - ww_ / 2, my_ + 18 + j * 13), word, font=f_s, fill=ink)
                            hit(f"dockwin_{pi}_{k}", rbox)
                            continue
                        if im_t is not None:
                            ox = px_ + (tw_ - im_t.width) // 2
                            oy = py_ + (th_ - im_t.height) // 2
                            mask = Image.new("L", im_t.size, 0)
                            ImageDraw.Draw(mask).rounded_rectangle((0, 0, im_t.width - 1, im_t.height - 1), radius=3, fill=255)
                            pic = (_brighten(im_t, 1.08) if rh else im_t).copy()
                            pic.putalpha(mask)          # window captures are opaque; the mask is the corner
                            im.alpha_composite(pic, (ox, oy))
                            d.rounded_rectangle((ox - 1, oy - 1, ox + im_t.width, oy + im_t.height), radius=4,
                                                outline=(EMBER + (255,)) if rh else SEP, width=1)
                        else:
                            # no picture yet: a window glyph on the dusk
                            d.rounded_rectangle((px_, py_, px_ + tw_ - 1, py_ + th_ - 1), radius=3,
                                                fill=(0x0E, 0x10, 0x14, 255), outline=SEP)
                            gx, gy = px_ + tw_ / 2, py_ + th_ / 2
                            d.rectangle((gx - 20, gy - 13, gx + 20, gy + 13), outline=ink, width=1)
                            d.rectangle((gx - 20, gy - 13, gx + 20, gy - 9), fill=ink)
                        lab = _ellipsis(title_of(w), f_w, tw_)
                        lw = _text_w(d, lab, f_w, 0.0)
                        d.text((rbox[0] + (cw - lw) / 2, rbox[3] - LAB_H + 3), lab, font=f_w, fill=ink)
                    else:
                        # no pictures on this desk: a row a window, a glyph before the title
                        gx, gy = rbox[0] + 13, rbox[1] + cell_h / 2
                        if is_new:
                            d.rectangle((gx - 5, gy - 0.5, gx + 5, gy + 0.5), fill=ink)
                            d.rectangle((gx - 0.5, gy - 5, gx + 0.5, gy + 5), fill=ink)
                            lab = "New window"
                        else:
                            d.rectangle((gx - 6, gy - 4, gx + 6, gy + 4), outline=ink, width=1)
                            d.rectangle((gx - 6, gy - 4, gx + 6, gy - 2), fill=ink)
                            lab = _ellipsis(title_of(w), f_w, cell_w - 40)
                        d.text((rbox[0] + 28, rbox[1] + 4), lab, font=f_w, fill=ink)
                    hit(f"dockwin_{pi}_{k}", rbox)
                hit("dock_pick_box", (bx0 - 8, by0 - 8, bx0 + bw_ + 8, hy0))   # the stack hovers, down to the tile (inert on click)
            hit("dock_fold", handle)
            plate_box = (dx0, dy0, dx0 + total_w, dy0 + row_h)
            hit("dock_plate", plate_box)          # the whole row hovers (inert on click)
            if _inside(p.get("ptr"), plate_box):
                dock_handle(handle, False)        # open: ◀ at the right end ("the left arrow on the right")
        d = ImageDraw.Draw(im)

    if not on:
        # IDLE IS BARE GLASS — but for the open dock above (2026-09-09). the User
        # 2026-09-06: "make all arrows and bars disappear in that 3s window
        # all at once". Motion (Mutter's idle monitor) brings the rail,
        # carets, bars and dot back together; only the dock's tiles take
        # clicks, the rest of the desk clicks through to GNOME.
        return im

    # ── fold carets (chrome, mouse-move). triangles, not filled keys.
    vt = ANCHORS.get("vitals_tog") or [g.page_left, 380, 28, 22]
    # same triangle as the bars, at the caret's .62 ink, ember under the
    # pointer (#vitalsTog:hover); the hit box keeps the old glyph's footprint
    # so the click target does not shrink. Drawn ONLY while the pointer is on
    # the group it folds (band + rows), like the corners' carets (the User
    # 2026-09-08: "only show when you hover - just like the other corners");
    # folded, it stays, as the bars do — a bare gap gives nothing to aim at.
    vt_box = (vt[0], vt[1], vt[0] + 28, vt[1] + f_fold.size + 6)
    hit("vitals_fold", vt_box)
    vb = ANCHORS.get("vitals_box")
    v_group = (vb[0], vb[1], vb[0] + vb[2], vb[1] + vb[3]) if vb else vt_box
    # the whole block of text is the hover surface (the User 2026-09-08: "make
    # its hit box all the text that collapses") — only hits get the pointer
    # on this glass, so the group itself must be one; inert on click
    hit("vitals_hover", v_group)
    if p.get("vitals_folded") or _inside(p.get("ptr"), v_group):
        tri(float(vt[0]) + 8, float(vt[1]) + f_fold.size * 0.55, not p.get("vitals_folded"),
            (EMBER + (255,)) if _inside(p.get("ptr"), vt_box) else (INK + (158,)), s=CARET_S)

    pip_ch = FOLD_BAR_H_U * u
    bar_y = H - g.pip_edge - pip_ch
    if p.get("pip_bar"):
        kiosk_bar(px, bar_y, pw, pip_ch, "pip_fold", up=True, label="camera")
    elif not p.get("cam_big"):
        # not while the camera is the whole glass (kiosk #pipwrap.big #pipfold{display:none})
        caret_strip(px, py, pw, pip_ch, "pip_fold", up=False, box=(px, py, px + pw, py + ph))
    if on and hover_border:
        # #pipwrap:hover border, HERE as well as in the HUD: the chrome
        # repaints on every pointer event, the HUD on its own worker a beat
        # later (the User 2026-09-07: "the other two on the left take a beat to
        # light up"). AFTER the caret scrim — ImageDraw lines replace pixels,
        # and the scrim's first row wiped the top edge, so the frame came up
        # in stages ("doesn't quite complete"). Opaque, and so is the HUD's
        # copy under it, so the two stacking changes nothing; the HUD's holds
        # the hover after the chrome's idle fade.
        d.rectangle((hover_border[0], hover_border[1], hover_border[2] - 1, hover_border[3] - 1), outline=EMBER + (255,))

    od = ANCHORS.get("oracle") or [g.oracle_center()[0], g.oracle_center()[1], g.pip_w]
    ocx, ocy, osz = float(od[0]), float(od[1]), float(od[2])
    ox0, oy0 = ocx - osz / 2, ocy - osz / 2
    def oracle_dot(reach):
        """The small white dot, pale like the carets (the User 2026-09-06: "small
        like the arrows and matching white"); under the pointer it comes up
        full with the page's 12px ember glow (#oracleDot:hover)."""
        rr = ORACLE_DOT_PX / 2
        box = (ocx - rr - reach, ocy - rr - reach, ocx + rr + reach, ocy + rr + reach)
        if _inside(p.get("ptr"), box):
            gs = int(rr * 2 + 40)
            glow = Image.new("RGBA", (gs, gs), (0, 0, 0, 0))
            ImageDraw.Draw(glow).ellipse((gs / 2 - rr * 1.5, gs / 2 - rr * 1.5, gs / 2 + rr * 1.5, gs / 2 + rr * 1.5),
                                         fill=EMBER + (178,))
            im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(6)), (int(ocx - gs / 2), int(ocy - gs / 2)))
            ImageDraw.Draw(im).ellipse((ocx - rr, ocy - rr, ocx + rr, ocy + rr), fill=EMBER + (255,))   # amber under the pointer (the User 2026-09-08)
        else:
            d.ellipse((ocx - rr, ocy - rr, ocx + rr, ocy + rr), fill=INK + (158,))
        hit("oracle_fold", box)

    if p.get("oracle_dot"):
        # Collapsed oracle: a small WHITE disc, the size of the fold carets,
        # chrome only. The kiosk's ember .72em dot was tried and overruled.
        oracle_dot(4)
    else:
        # .clockwrap:hover, the wall's way: a soft white glow along the rings
        # on THIS layer over the standing dial, at once, and nothing else
        # changes — no second dial, no lines (the User 2026-09-07: "maybe just the
        # white glow then vs lines on hover"). The window holds the chrome up
        # while the pointer rests here, so the hover does not fade on the idle.
        if p.get("dial_hover"):
            hg = _dial_hover_glow(int(round(osz)))
            im.alpha_composite(hg, (int(round(ocx - hg.width / 2)), int(round(ocy - hg.height / 2))))
            d = ImageDraw.Draw(im)
        # The open oracle's fold control is the same small white dot, at her
        # core, not a triangle riding the ring (the User 2026-09-06: "that
        # triangle on a circle seems like the enemy coming in on radar").
        # It comes and goes with the rest of the chrome.
        oracle_dot(6)
        # THE DIAL IS A HOVER SURFACE. Only chrome hits take pointer input on
        # this glass (the window's input shape), so without this the pointer
        # over the ring never reaches us and the kiosk's .clockwrap:hover
        # could not fire (the User 2026-09-07: "Oracle not brightening"). It is
        # registered after the fold dot, so the dot still wins a click; this
        # one is deliberately inert.
        hit("oracle_hover", (ox0, oy0, ox0 + osz, oy0 + osz))

    # No minimize on the PAUSED chip (the User 2026-09-06: "just remove that
    # minimize arrow"). The mode / Secure chips are information; they stay.
    # THE SECURE CHIP IS A BUTTON, persistent like the carets: one click
    # toggles Secure, a double click asks for the password and holds it
    # (the User 2026-09-06: "click is still toggling folders and makes my
    # Secure button unclickable"). The box is the HUD's own hotspot.
    sec = HOTSPOTS.get("secure")
    if sec:
        hit("secure", tuple(float(v) for v in sec))
    hold = HOTSPOTS.get("comms_hold")
    if hold:
        hit("comms_hold", tuple(float(v) for v in hold))
    # THE MODE CHIP IS A BUTTON, as on Brave (the User 2026-09-09: "import that
    # element as is"): click toggles LISTENING <-> PAUSED, the chip
    # equivalent of Hey Crissa. Voice off = Away = not armed (on_click decides).
    mode = HOTSPOTS.get("mode")
    if mode:
        hit("mode", tuple(float(v) for v in mode))
    # THE FOLD WINS THE CLICK. The FT bar used to be registered AFTER the
    # HUD's "ft" hotspot, and on_click takes the first hit, so a click on the
    # collapsed FT bar enlarged the pop instead of opening it (the User
    # 2026-09-08: "The collapse button expands then switches to open frame").
    # The pip's fold was already first; the FT's is now, and a folded corner
    # registers no enlarge hotspot at all — there is nothing to enlarge.
    ftx, fty, ftw_, fth_ = ft_box(g, p)
    ft_ch = 1.5 * u             # the OPEN pop's hover scrim, below
    ft_bar_h = FOLD_BAR_H_U * u  # the COLLAPSED bar — the camera bar's height
    if p.get("ft_bar"):
        kiosk_bar(ftx, H - g.pip_edge - ft_bar_h, ftw_, ft_bar_h, "ft_fold", up=True, style="ft")
    elif not p.get("ft_big"):
        # not while the pop is held up big (kiosk #ftPop.big #ftFold{display:none})
        caret_strip(ftx, fty, ftw_, ft_ch, "ft_fold", up=False, box=(ftx, fty, ftx + ftw_, fty + fth_))
    if on and not p.get("ft_big") and not p.get("ft_bar") and _inside(p.get("ptr"), (ftx, fty, ftx + ftw_, fty + fth_)):
        # #ftPop:hover — the pale hairline goes ember under the pointer, the
        # camera box's rule on this side too (the User 2026-09-09). AFTER the
        # caret scrim, which replaces pixels, like the pip's hover frame.
        ImageDraw.Draw(im).rounded_rectangle((ftx, fty, ftx + ftw_ - 1, fty + fth_ - 1), radius=int(0.45 * u), outline=EMBER + (255,))

    for n in ("camera", "ft", "log_board", "log_room", "log_tools"):   # HUD buttons that take clicks
        if (n == "camera" and p.get("pip_bar")) or (n == "ft" and p.get("ft_bar")):
            continue
        box = HOTSPOTS.get(n)
        if box:
            hit(n, tuple(float(v) for v in box))

    if menu and on:
        # ADVANCED, hung under ⋯ (the User 2026-09-06), in the page's #menu
        # language: the .97 plate on a HAIR hairline with the 0 1em 3em
        # shadow, ember h4, ink-3 row labels, GROUND2 mono keys that go ember
        # under the pointer (#menu .mrow button), hairline dividers. ONE
        # control size: every key is the corner stepper's height and font, so
        # ON / SHOW and the − 0.75 + dials read as one kit (the User 2026-09-07:
        # "smaller more consistent look"). No ✕ CLOSE — ⋯ toggles it and a
        # click anywhere off the panel closes it.
        f_key = fctl
        f_note = font("mono", 0.34 * u)
        pad_kx, pad_ky = 0.8 * fs, 0.16 * fs
        key_h = fs + 2 * pad_ky                      # == step_size()[1]
        row_h = key_h + 0.38 * u
        note_h = f_note.size + 0.25 * u
        gap_div = 0.35 * u
        mw_ = int(12.5 * u)
        # exactly the rows below (h4, brightness, resolution+note, divider,
        # zos+note, folders+note, divider, flourish+note, pad)
        mh_ = int(2.7 * u + row_h + 4 * (key_h + 0.12 * u) + 3 * note_h + 4 * gap_div + 2 + f_note.size
                  + 3 * (key_h + 0.12 * u) + note_h + 2 * gap_div)      # + her working day (2026-10-04)
        # under Ubuntu's dock, over its ⋯ launcher (house_wall_dock.ubuntu_key_right)
        anchor_r = p.get("menu_right") if p.get("dock_ubuntu") and p.get("menu_right") else rail_right
        mx0 = max(8, min(W - mw_ - 8, int(anchor_r - mw_)))
        my0 = max(8, y0 - int(0.2 * u) - mh_)     # opens UP from the bottom rail
        sm = int(3 * u)
        sh = Image.new("RGBA", (mw_ + 2 * sm, mh_ + 2 * sm), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((sm, sm + int(u), sm + mw_, sm + mh_ + int(u)), radius=3, fill=(0, 0, 0, 140))
        im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(1.5 * u)), (mx0 - sm, my0 - sm))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((mx0, my0, mx0 + mw_, my0 + mh_), radius=3,
                            fill=GROUND + (247,), outline=hair)
        lx0 = mx0 + 0.9 * u
        rx1 = mx0 + mw_ - 0.9 * u

        def menu_key(x_right, yy, label, name, lit=False):
            """#menu .mrow button at the stepper's size, right-aligned, one width."""
            s_ = label.upper()
            tw = _text_w(d, s_, f_key, 0.16)
            w_ = max(tw + 2 * pad_kx, min_chip)
            x_ = x_right - w_
            box = (x_, yy, x_ + w_, yy + key_h)
            hov = _inside(p.get("ptr"), box)
            bd = (EMBER_DEEP + (255,)) if hov else ((SAGE + (128,)) if lit else hair)
            fg = (EMBER + (255,)) if hov else ((SAGE + (255,)) if lit else (INK2 + (255,)))
            d.rounded_rectangle(box, radius=2, fill=GROUND2 + (255,), outline=bd)
            _text(d, (x_ + (w_ - tw) / 2, yy + pad_ky - 1), s_, f_key, fg, 0.16)
            hit(name, box)
            return w_

        def row_label(yy, label):
            _text(d, (lx0, yy + pad_ky - 1), label.upper(), f_key, ink3, 0.16)

        def note(yy, text, col=None):
            # A NOTE FITS THE PANEL (the User 2026-10-20, "menu overrun off
            # edges"): two notes ran 13-14 u wide on a 10.7 u inner width at
            # every size. The font steps down to fit, then the line is cut
            # with an ellipsis; nothing paints past rx1.
            f_, t_ = fit_note(d, text, f_note, rx1 - lx0, u)
            _text(d, (lx0, yy), t_, f_, col or a(INK3, 0.8), 0.06)

        def divider(yy):
            d.line((mx0 + 1, yy, mx0 + mw_ - 1, yy), fill=hair)

        ry = my0 + 0.8 * u
        _text(d, (lx0, ry), "ADVANCED", f_lbl, ember, 0.22)
        ry += 1.0 * u + 0.1 * u
        sw_ = step_size()[0]
        row_label(ry, "Brightness")
        # adv_ names: the corner draws the same dials under their own names,
        # and one dict cannot hold both; the window strips the prefix.
        quiet_step(rx1 - sw_, ry, p.get("brightness", 1), "adv_brightness")
        ry += row_h
        row_label(ry, "Resolution")
        quiet_step(rx1 - sw_, ry, p.get("density", 1), "adv_density")
        ry += key_h + 0.12 * u
        # This note promised the wheel since 09-06 with nothing bound to it
        # (flagged unfixed in WRAP-20260908-WALL-CONTROLS). the User 2026-09-16:
        # "We want that scroll to be chars/cm" — so the binding landed
        # (house_wall_ascii on_wheel, Button-4/5 + MouseWheel) and the promise
        # is true. Brave's glass does the same gesture (glassWheel), so the
        # wheel means one thing on both.
        note(ry, "the wheel over the glass steps resolution too")
        ry += note_h + gap_div
        divider(ry)
        ry += gap_div + 1
        # ZOS DESKTOP: this wallpaper IS the ZOS glass, so the key reads ON.
        # Off = wallpaper off: enable+start crissa-display autostart. On only
        # disables that autostart — it never closes a live Brave window.
        row_label(ry, "Wall desktop")
        menu_key(rx1, ry, "on", "zos", lit=not p.get("zos_armed"))
        ry += key_h + 0.12 * u
        if p.get("zos_armed"):
            # ARMED (the User 2026-09-08, "I am trapped with no menus"): the first
            # press only arms; the second within 4 s hands the glass to Brave
            # — a fullscreen kiosk with no menus. Say so before it happens.
            note(ry, "press again within 4 s → Brave takes the glass", RUST + (255,))
        else:
            note(ry, "off = Brave takes the glass · two presses")
        ry += note_h + gap_div
        # DESKTOP FOLDERS — GNOME's own icons (DING) over the glass. The key
        # left on the morning of 2026-09-08 ("remove the hide folders
        # button") and came back that afternoon (the User: "a smooth beautiful
        # interface that can show or hide desktop folders and essentially
        # every element on the screen"). One key, both directions.
        row_label(ry, "Desktop folders")
        menu_key(rx1, ry, "shown" if p.get("folders") else "hidden", "folders", lit=bool(p.get("folders")))
        # THE WHOLE ROW IS THE KEY (the User 2026-09-13 "hide/show folders do not
        # toggle"): the chip was 63×16 px at the row's right end and his
        # press landed on the label 300 px left of it.
        hit("folders", (lx0, ry - 2, rx1, ry + key_h + 0.12 * u + note_h + 2))   # label, chip and the note line
        ry += key_h + 0.12 * u
        note(ry, "GNOME's desktop icons · click to hide or show")
        ry += note_h + gap_div
        divider(ry)
        ry += gap_div + 1
        # Non-speech mix: Conductor rest-loop wraps before a bag fidget.
        row_label(ry, "Flourish %")
        fl = int(p.get("flourish_pct", 35))
        total, bh_, w_lo, w_hi, vw, gap, pad_x, pad_y = step_size()
        sx0 = rx1 - total
        for tag, glyph, xx, ww in (("flourish_pct_lo", "−", sx0, w_lo), ("flourish_pct_hi", "+", sx0 + w_lo + gap + vw + gap, w_hi)):
            hov = _inside(p.get("ptr"), (xx, ry, xx + ww, ry + bh_))
            d.rounded_rectangle((xx, ry, xx + ww, ry + bh_), radius=2,
                                fill=a(INK, 0.10) if hov else None, outline=a(INK2, 1.0) if hov else a(HAIR, 0.4))
            tw = _text_w(d, glyph, fctl, 0.0)
            d.text((xx + (ww - tw) / 2, ry + pad_y - 1), glyph, font=fctl, fill=a(INK, 1.0) if hov else a(INK3, 0.4))
            hit(tag, (xx, ry, xx + ww, ry + bh_))
        vs = "%d%%" % fl
        tw = _text_w(d, vs, fctl, 0.14)
        d.text((sx0 + w_lo + gap + (vw - tw) / 2, ry + pad_y - 1), vs, font=fctl, fill=a(INK, 1.0))
        ry += key_h + 0.12 * u
        n = wall_prefs.wraps_before_fidget(fl)
        if n is None:
            mix = "0% — rest only, no bag fidgets (speech already frozen)"
        else:
            mix = "non-speech · fidget after %d rest wrap%s" % (n, "" if n == 1 else "s")
        note(ry, mix)
        ry += note_h + gap_div
        # HER WORKING DAY (the User 2026-10-04): beside the flourish knob, because both say how she spends the day
        # at her desk. Hour steppers that wrap round the clock, and whether she leaves at the end of it.
        for tag, label, val in (("work_start", "Day starts", p.get("work_start", 9)),
                                ("work_end", "Day ends", p.get("work_end", 17))):
            row_label(ry, label)
            for tag_, glyph, xx, ww in ((tag + "_lo", "−", sx0, w_lo), (tag + "_hi", "+", sx0 + w_lo + gap + vw + gap, w_hi)):
                hov = _inside(p.get("ptr"), (xx, ry, xx + ww, ry + bh_))
                d.rounded_rectangle((xx, ry, xx + ww, ry + bh_), radius=2,
                                    fill=a(INK, 0.10) if hov else None, outline=a(INK2, 1.0) if hov else a(HAIR, 0.4))
                tw = _text_w(d, glyph, fctl, 0.0)
                d.text((xx + (ww - tw) / 2, ry + pad_y - 1), glyph, font=fctl, fill=a(INK, 1.0) if hov else a(INK3, 0.4))
                hit(tag_, (xx, ry, xx + ww, ry + bh_))
            vs = "%02dh" % (int(val) % 24)          # three glyphs, like "35%": the stepper's value slot
            tw = _text_w(d, vs, fctl, 0.14)
            d.text((sx0 + w_lo + gap + (vw - tw) / 2, ry + pad_y - 1), vs, font=fctl, fill=a(INK, 1.0))
            ry += key_h + 0.12 * u
        row_label(ry, "Leaves at day end")
        menu_key(rx1, ry, "on" if p.get("leaves_at_end") else "off", "leaves_at_end", lit=bool(p.get("leaves_at_end")))
        ry += key_h + 0.12 * u
        note(ry, "she leaves at day end, back at day start")
        hit("menu_panel", (mx0, my0, mx0 + mw_, my0 + mh_))

    note = str(p.get("cam_note") or "").strip()
    if note and on:
        # 1 s glass note: sage dropped because door-watch can't run
        # (joystick off the door). Keyboard hold never sets this.
        d = ImageDraw.Draw(im)
        f_n = font("mono", 0.62 * fs)
        tw = d.textlength(note, font=f_n)
        pad_x, pad_y = 0.9 * fs, 0.5 * fs
        bw, bh = tw + 2 * pad_x, f_n.size + 2 * pad_y
        bx = (W - bw) / 2.0
        by = H - 4.4 * fs - bh
        d.rectangle((bx, by, bx + bw, by + bh), fill=GROUND2 + (230,), outline=EMBER_DEEP + (255,))
        d.text((bx + pad_x, by + pad_y * 0.7), note, font=f_n, fill=EMBER + (255,))
    if on:
        for cx_, y_, text_, where_ in tips:
            rail_tip(cx_, y_, text_, above=(where_ is True), left=(where_ == "left"))
    return im


# ── THE REGISTER ON THE FILM (the User 2026-09-09: "finish wiring it with the
# standard formal face up talking loop"). The film is the picture — the
# conductor's face-on loop with her painted mouth; this layer paints only
# what the coach's phase needs: LOOK HERE (mono, the stage direction), the
# take sentence lower-third on
# one ember hairline (the count line takes the same slot on `turn`), and a
# CANCEL key. Idle: nothing. Hits land in REGISTER_HITS (the wall merges
# them with the chrome's for the input shape and the click).
REGISTER_HITS: dict = {}
# thanks (2026-09-11): the wall host's last beat — "That's it. Thanks!" on
# the lower third while Lila's thanks clip and Crissa's handoff play; the
# wizard advances on `done`, never on `thanks`
REGISTER_PHASES = ("watch", "read", "turn", "thanks", "ended")
# ended (round 21, a pack's one Voice ID routine): a menu run that ended without the person - its card could not be shown,
# nobody answered it, or the person was removed - says why in its one small plate for a few seconds (lib/crissa_init
# _say_ended). Only a pack's Init sets it; the house's Register never does.
# the take bar under the hairline drains over reg["seconds"] from the beep
# (reg["beep_ts"]); the hairline flashes ember for REGISTER_FLASH_S after a beep
REGISTER_FLASH_S = 0.25


def _wrap(d, text: str, f, max_w: float) -> list[str]:
    words = (text or "").split()
    lines: list[str] = []
    cur = ""
    for w in words:
        t = (cur + " " + w).strip()
        if cur and d.textlength(t, font=f) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _render_register_spoken(g, reg: dict, phase: str, im, d, W: int, H: int, u: float, left: float):
    """THE SPOKEN VOICE ID'S ONE SMALL PLATE (the User 2026-10-12, .101: three plates with the same words, one closed by
    hand): a single line low in the middle - "Voice ID 2 of 4: At the beep, count to six." (the glass's own cue, words
    with no device in them), the take bar under it while it records, and CANCEL. No LOOK HERE, no microphone name, no
    count in large type: the announcer says what to do. It goes when the routine's phase does (done, idle, error)."""
    try:
        take, takes = int(reg.get("take") or 0), int(reg.get("takes") or 0)
    except (TypeError, ValueError):
        take, takes = 0, 0
    cue = str(reg.get("cue") or "").strip()
    if phase == "thanks":
        line = "Voice ID: that's it. Thanks!"
    elif phase == "ended":
        line = "Voice ID: " + (str(reg.get("text") or "").strip() or "it stopped.")
    elif take and takes:
        line = "Voice ID %d of %d: %s" % (take, takes, cue or "listen, then count to six at the beep.")
    else:
        line = "Voice ID: " + (cue or "listen, then count to six at the beep.")
    now = time.time()
    try:
        beep_ts = float(reg.get("beep_ts") or 0.0)
        seconds = float(reg.get("seconds") or 0.0)
    except (TypeError, ValueError):
        beep_ts, seconds = 0.0, 0.0
    flash = beep_ts > 0 and 0.0 <= now - beep_ts < REGISTER_FLASH_S
    f_ln = font("mono", 0.8 * u)
    tw = d.textlength(line, font=f_ln)
    pad = 0.7 * u
    y0 = H - 0.075 * H - f_ln.size
    x0 = (W - tw) / 2.0
    d.rectangle((x0 - pad, y0 - pad, x0 + tw + pad, y0 + f_ln.size + 1.2 * pad), fill=GROUND + (200,),
                outline=EMBER_DEEP + (255,))
    d.text((x0, y0), line, font=f_ln, fill=(INK if flash or "now" in line else EMBER2) + (255,))
    if phase == "turn" and seconds > 0 and beep_ts > 0:
        k = (now - beep_ts) / seconds
        if 0.0 <= k < 1.0:
            by = y0 + f_ln.size + 0.45 * pad
            d.line((x0, by, x0 + tw, by), fill=INK3 + (140,), width=2)
            d.line((x0, by, x0 + tw * (1.0 - k), by), fill=EMBER + (255,), width=2)
    if phase == "ended":
        return im                               # nothing left to cancel: the line alone, until it goes
    f_k = font("mono", 0.66 * u)
    lab = "CANCEL"
    kw = _text_w(d, lab, f_k, 0.14) + 1.6 * u
    kh = f_k.size + 0.9 * u
    kx, ky = left, H - 2.6 * u - kh
    d.rectangle((kx, ky, kx + kw, ky + kh), fill=GROUND + (120,), outline=EMBER_DEEP + (255,))
    _text(d, (kx + 0.8 * u, ky + 0.45 * u), lab, f_k, INK2 + (255,), 0.14)
    REGISTER_HITS["register_cancel"] = (kx, ky, kx + kw, ky + kh)
    return im


def render_register(g, reg: dict | None, ptr=None, top_y: float | None = None):
    """One RGBA glass for the coach's phase, or None when nothing is live.
    top_y: where LOOK HERE sits (the wizard's eyebrow + dots own the top
    left while the chapter runs, so the register layer drops under them)."""
    REGISTER_HITS.clear()
    reg = reg if isinstance(reg, dict) else {}
    phase = str(reg.get("phase") or "idle")
    if phase not in REGISTER_PHASES:
        return None
    W, H = int(g.W), int(g.H)
    u = float(getattr(g, "u", W / 88.0))
    left = float(getattr(g, "page_left", 0.378 * u))
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if reg.get("verbal") or phase == "ended":
        return _render_register_spoken(g, reg, phase, im, d, W, H, u, left)
    # LOOK HERE — mono, ember, the stage direction (the camera wants the face)
    f_dir = font("mono", 0.72 * u)
    _text(d, (left, (top_y if top_y is not None else 1.4 * u)), "LOOK HERE.", f_dir, EMBER2 + (255,), 0.22)
    # no face-frame corners on the wall: they sat on the PAUSED / HOLD chips
    # (the User 2026-09-09: "what are those yellow bars?") and the camera box at
    # the bottom left already shows the face live
    # the line — the take sentence on read, the count on turn
    text = str(reg.get("text") or "") if phase in ("read", "turn", "thanks") else ""
    if phase == "read" and not text.strip():
        text = str(reg.get("line") or "")
    if phase == "thanks" and not text.strip():
        text = "That's it. Thanks!"
    now = time.time()
    try:
        beep_ts = float(reg.get("beep_ts") or 0.0)
        seconds = float(reg.get("seconds") or 0.0)
    except (TypeError, ValueError):
        beep_ts, seconds = 0.0, 0.0
    flash = beep_ts > 0 and 0.0 <= now - beep_ts < REGISTER_FLASH_S
    # THE CUE AND THE TAKE (the User 2026-10-10, .101 V): what to do now over the sentence (the glass's own `cue`, one
    # rule in lib/crissa_init.glass_cue), "RECORDING 1 OF 2" beside LOOK HERE, the microphone under it
    cue = str(reg.get("cue") or "").strip() if phase in ("read", "turn", "watch") else ""
    try:
        take, takes = int(reg.get("take") or 0), int(reg.get("takes") or 0)
    except (TypeError, ValueError):
        take, takes = 0, 0
    if take and takes:
        f_tk = font("mono", 0.6 * u)
        tk = "RECORDING %d OF %d" % (take, takes)
        _text(d, (left, (top_y if top_y is not None else 1.4 * u) + 1.2 * u), tk, f_tk, INK2 + (255,), 0.18)
        if reg.get("mic"):
            _text(d, (left, (top_y if top_y is not None else 1.4 * u) + 2.2 * u), str(reg.get("mic"))[:80],
                  font("mono", 0.5 * u), INK3 + (255,), 0.04)
    if text.strip():
        f_line = font("serif", 0.0205 * W)
        max_w = min(42 * d.textlength("n", font=f_line), 0.88 * W)
        lines = _wrap(d, text.strip(), f_line, max_w)
        lh = f_line.size * 1.3
        block_h = lh * len(lines)
        y0 = H - 0.055 * H - block_h
        if cue:
            f_cue = font("mono", 0.75 * u)
            cw = d.textlength(cue, font=f_cue)
            d.text(((W - cw) / 2.0, y0 - 2.6 * f_line.size), cue, font=f_cue,
                   fill=(INK if flash or "now" in cue else EMBER2) + (255,))
        # the pointer: one hairline, ember; it FLASHES for a beat on each beep
        hx = W / 2.0
        hw = (2.6 if flash else 1.6) * f_line.size
        d.line((hx - hw, y0 - 1.1 * f_line.size, hx + hw, y0 - 1.1 * f_line.size),
               fill=(EMBER2 + (255,)) if not flash else (INK + (255,)), width=(3 if flash else 1))
        for i, ln in enumerate(lines):
            tw = d.textlength(ln, font=f_line)
            x = (W - tw) / 2.0
            y = y0 + i * lh
            d.text((x + 1, y + 2), ln, font=f_line, fill=GROUND + (170,))   # the shadow the page's text-shadow gives
            d.text((x, y), ln, font=f_line, fill=INK + (255,))
        # THE TAKE BAR (the page's read.seconds, now on the wall): a thin
        # ember bar under the sentence that drains from the beep over the
        # take; nothing before the beep, gone when the window closes
        if phase in ("read", "turn") and seconds > 0 and beep_ts > 0:
            k = (now - beep_ts) / seconds
            if 0.0 <= k < 1.0:
                bw = min(0.36 * W, max_w)
                bx0 = (W - bw) / 2.0
                by = y0 + block_h + 0.55 * f_line.size
                d.line((bx0, by, bx0 + bw, by), fill=INK3 + (140,), width=2)   # the track
                d.line((bx0, by, bx0 + bw * (1.0 - k), by), fill=EMBER + (255,), width=2)
    # CANCEL — a small mono key, bottom-left, always reachable while live
    f_k = font("mono", 0.66 * u)
    lab = "CANCEL"
    kw = _text_w(d, lab, f_k, 0.14) + 1.6 * u
    kh = f_k.size + 0.9 * u
    kx, ky = left, H - 2.6 * u - kh
    d.rectangle((kx, ky, kx + kw, ky + kh), fill=GROUND + (120,), outline=EMBER_DEEP + (255,))
    _text(d, (kx + 0.8 * u, ky + 0.45 * u), lab, f_k, INK2 + (255,), 0.14)
    REGISTER_HITS["register_cancel"] = (kx, ky, kx + kw, ky + kh)
    return im



# ── THE FIRST-BOOT WIZARD ON THE FILM (the User 2026-09-09: New User on the wall
# is "all collectable metrics for the existing Init routine", Register ID an
# extractable subsection). The walker (lib/init_wizard.py) owns the steps,
# The voice and the ear; this layer is its second renderer: eyebrow + dots
# top-left, the plate's title on the sentence's hairline lower-third, the
# coach line and the hint under it, the step's pills as mono keys, a
# CANCEL. Hits land in INIT_HITS. While the Register chapter is live the
# register layer owns the lower third and this one keeps only the eyebrow.
INIT_HITS: dict = {}
# CANCEL ONLY (the User 2026-09-13: "get rid of all buttons but for cancel"): every
# other choice is spoken; the pills and the dial's hour keys are not drawn as keys.
CANCEL_ONLY = __import__("os").environ.get("HOUSE_INIT_CANCEL_ONLY", "1").strip() not in ("0", "false", "no")
INIT_SKIPPABLE = ("present_regulars_enroll", "training_window", "news_topics", "media_ghost_mask")
# THE SCREENS WITH A PICTURE (the User 2026-09-12 pm: "a few more screens with
# graphics for clock etc. Some of that is designed"). The page's init-wizard.js
# drew a clock dial (#initDial), the door still (#initStill), card and topic
# toggles (#initCards, #initTopics) and the overnight range (#initRange); the
# wall paints the same set on the right of the film, out of Lila's face.
INIT_STILL_STEPS = ("camera_orientation", "media_ghost_mask")
INIT_DIAL_STEPS = ("room_scan", "media_speakers", "seat_map", "room_clock")
INIT_USER_DESK = 6      # the User 2026-09-13: "User desk will likely always be 6" — the seat map starts there
INIT_TOGGLE_STEPS = ("brief_interview", "news_topics")
INIT_DEFAULT_CARDS = ("weather", "headline", "house")
INIT_RANGE_DEFAULT = "18:00 – 02:00"


def init_people(snap: dict) -> list[tuple[str, str]]:
    """(person id, display) — the User first, then the regulars, as the roster
    step recorded them (seat_map asks where each one sits)."""
    roster = snap.get("roster") if isinstance(snap.get("roster"), dict) else {}
    out: list[tuple[str, str]] = []
    user = str(roster.get("user") or "").strip()
    if user:
        out.append((str(roster.get("user_id") or user.lower().replace(" ", "_")), user))
    for r in roster.get("regulars") or []:
        if isinstance(r, dict):
            pid = str(r.get("id") or "").strip()
            disp = str(r.get("display") or pid).strip()
        else:
            disp = str(r).strip(); pid = disp.lower().replace(" ", "_")
        if pid and pid not in [p for p, _ in out]:
            out.append((pid, disp))
    return out


def init_toggles(snap: dict) -> dict:
    """The wall's own toggle state for the step (cards / topics on, the seat
    picks, the selected person) — `_toggles` on the snapshot the wall paints.
    seat_map starts with the User at the desk (seat 6) when that hour is
    legal; a tap moves it."""
    sid = str(snap.get("step_id") or "")
    tg = snap.get("_toggles") if isinstance(snap.get("_toggles"), dict) else {}
    t = dict(tg.get(sid) if isinstance(tg.get(sid), dict) else {})
    if sid == "seat_map":
        people = init_people(snap)
        legal = [int(h) for h in (snap.get("legal_hours") or range(1, 13))]
        if people and not t.get(people[0][0]) and INIT_USER_DESK in legal:
            t[people[0][0]] = INIT_USER_DESK
    return t


def init_seat_who(snap: dict) -> str:
    """seat_map: the person the next hour tap assigns — the selected one, else
    the first without a seat, else the last."""
    tg = init_toggles(snap)
    people = init_people(snap)
    who = str(tg.get("_who") or "")
    if who and who in [p for p, _ in people]:
        return who
    for pid, _ in people:
        if not tg.get(pid):
            return pid
    return people[-1][0] if people else ""


def init_on(snap: dict) -> set:
    """The pill names that paint lit."""
    sid = str(snap.get("step_id") or "")
    tg = init_toggles(snap)
    if sid == "brief_interview":
        cards = tg if tg else {c: True for c in INIT_DEFAULT_CARDS}
        return {"init_tog_" + c for c, v in cards.items() if v and not c.startswith("_")}
    if sid == "news_topics":
        return {"init_tog_" + c for c, v in tg.items() if v and not c.startswith("_")}
    if sid == "seat_map":
        who = init_seat_who(snap)
        return {"init_who_" + who} if who else set()
    return set()


def init_pills(snap: dict) -> list[tuple[str, str]]:
    """(hit name, label) per step — the page's pills, as keys on the wall."""
    sid = str(snap.get("step_id") or "")
    ans = (snap.get("answers") or {}).get(sid) or {}
    pills: list[tuple[str, str]] = []
    if snap.get("frozen"):
        # THE RESTART MENU (the User 2026-09-13): Lila paused the setup
        return [("init_restart", "Start over"), ("init_resume", "Carry on"), ("init_later", "Later")]
    if str(snap.get("kind") or "") == "machine":
        # a pack's machine card (the User 2026-10-10): its keys are its only answers, even under CANCEL_ONLY
        plate = snap.get("plate") if isinstance(snap.get("plate"), dict) else {}
        return [("init_pill_" + str(v), str(lab)) for v, lab in (plate.get("keys") or [])]
    if sid == "welcome":
        return [("init_go", "Let's go")]
    if str(snap.get("kind") or "") == "dialogue":
        pills = [("init_go", "That's enough")]
        if snap.get("optional"):
            pills.append(("init_skip", "Later"))
        return pills
    if sid == "camera_orientation":
        return [("init_yes", "Yes, on the right"), ("init_no", "No, on the left")]
    if sid == "media_speakers":
        if isinstance(ans, dict) and ans.get("need_hour"):
            return []          # the dial carries the twelve hours (init_hour_N)
        return [("init_internal", "This computer"), ("init_speakers", "Speakers in the room")]
    if sid == "brief_interview":
        cards = [str(c) for c in (snap.get("cards") or INIT_DEFAULT_CARDS)]
        return [("init_tog_" + c, c.replace("_", " ")) for c in cards] + [("init_go", "Continue")]
    if sid == "news_topics":
        tops = []
        for t in snap.get("topics") or []:
            if isinstance(t, dict) and t.get("id"):
                tops.append((str(t["id"]), str(t.get("label") or t["id"])))
            elif isinstance(t, str):
                tops.append((t, t))
        return [("init_tog_" + tid, lab) for tid, lab in tops] + [("init_go", "Continue"), ("init_skip", "Later")]
    if sid == "seat_map":
        return [("init_who_" + pid, disp) for pid, disp in init_people(snap)] + [("init_go", "Continue")]
    if sid == "present_regulars_enroll":
        who = str(snap.get("enroll_who") or "them")
        return [("init_register_who", "Register " + who), ("init_skip", "Later")]
    if sid == "user_identity":
        return []
    pills.append(("init_go", "Continue"))
    if snap.get("optional") or sid in INIT_SKIPPABLE:
        pills.append(("init_skip", "Later"))
    return pills


def init_pills_with_again(snap: dict) -> list[tuple[str, str]]:
    """The step's pills plus AGAIN on every plate Lila speaks (the User
    2026-09-12: more time for the elderly — one key replays her line)."""
    pills = init_pills(snap)
    sid = str(snap.get("step_id") or "")
    if (sid and sid not in ("user_identity", "present_regulars_enroll") and not snap.get("frozen")
            and str(snap.get("kind") or "") != "machine"):       # the machine card says nothing to say again
        pills.append(("init_again", "Again"))
    return pills


def render_init(g, snap: dict | None, register_live: bool = False):
    """One RGBA glass for the wizard's step, or None when it is not running."""
    INIT_HITS.clear()
    snap = snap if isinstance(snap, dict) else {}
    if snap.get("v2"):
        # INIT V2 (the User 2026-10-04): its own glass, same hits dict, same CANCEL name
        try:
            import init_v2_render
        except ImportError:          # a pack: no Init v2 here (its walker is the house's)
            return None
        return init_v2_render.render(g, snap, INIT_HITS, register_live=register_live, ptr=snap.get("_ptr"))
    if str(snap.get("status") or "") != "running":
        return None
    W, H = int(g.W), int(g.H)
    u = float(getattr(g, "u", W / 88.0))
    left = float(getattr(g, "page_left", 0.378 * u))
    plate = snap.get("plate") if isinstance(snap.get("plate"), dict) else {}
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # eyebrow + the dots: where we are in the routine
    f_eye = font("mono", 0.72 * u)
    eyebrow = str(plate.get("eyebrow") or snap.get("step_id") or "").upper()
    _text(d, (left, 1.4 * u), eyebrow, f_eye, EMBER + (255,), 0.3)
    total = int(snap.get("total") or 0)
    on = int(snap.get("index") or 0) + 1
    marks = set(int(x) for x in (snap.get("optional_marks") or []) if isinstance(x, int))
    r_ = 0.24 * u
    for i in range(total):
        cx = left + r_ + i * (r_ * 2 + 0.36 * u)
        cy = 2.75 * u
        if i in marks:
            d.ellipse((cx - r_ * 0.6, cy - r_ * 0.6, cx + r_ * 0.6, cy + r_ * 0.6), outline=INK3 + (255,), width=1)
        else:
            d.ellipse((cx - r_, cy - r_, cx + r_, cy + r_), fill=(EMBER if i < on else INK3) + (255,))
    # CANCEL, bottom-left (the register layer draws its own while live)
    if not register_live:
        f_k = font("mono", 0.66 * u)
        lab = "CANCEL"
        kw = _text_w(d, lab, f_k, 0.14) + 1.6 * u
        kh = f_k.size + 0.9 * u
        kx, ky = left, H - 2.6 * u - kh
        d.rectangle((kx, ky, kx + kw, ky + kh), fill=GROUND + (120,), outline=EMBER_DEEP + (255,))
        _text(d, (kx + 0.8 * u, ky + 0.45 * u), lab, f_k, INK2 + (255,), 0.14)
        INIT_HITS["init_cancel"] = (kx, ky, kx + kw, ky + kh)
    if register_live:
        return im
    # THE MODE NOTE (the User 2026-09-14: "flash the 'leaving Render...' notice"): "Switching to Render…" before
    # the opener, "Leaving Render…" before Crissa's walk-in — the whole glass says it, nothing else
    note_m = str(snap.get("mode_note") or "").strip()
    if note_m:
        f_m = font("serif", 0.03 * W)
        tw = d.textlength(note_m, font=f_m)
        x0, y0 = (W - tw) / 2.0, 0.42 * H
        pulse = 0.65 + 0.35 * abs(math.sin(float(snap.get("_t") or 0.0) * 2.0))
        d.rectangle((x0 - 1.6 * u, y0 - 1.0 * u, x0 + tw + 1.6 * u, y0 + f_m.size + 1.0 * u), fill=GROUND + (235,), outline=EMBER_DEEP + (255,))
        d.text((x0, y0), note_m, font=f_m, fill=EMBER + (int(255 * pulse),))
        return im
    # the step's picture (dial / still / range), right of the film
    try:
        _init_graphic(d, im, snap, W, H, u)
    except Exception:  # noqa: BLE001
        pass
    # the plate: title on the hairline, coach and hint under it, pills below
    f_t = font("serif", 0.0205 * W)
    f_c = font("serif", 0.0135 * W)
    f_h = font("serif-italic", 0.011 * W)
    max_w = min(42 * d.textlength("n", font=f_t), 0.88 * W)
    title = _wrap(d, str(plate.get("title") or "").strip(), f_t, max_w)
    coach = _wrap(d, str(plate.get("coach") or "").strip(), f_c, max_w)
    hint = _wrap(d, str(snap.get("processing") or plate.get("hint") or "").strip(), f_h, max_w)   # "Wait while we process…" wins
    note = str(snap.get("note") or "").strip()
    pills = [] if (CANCEL_ONLY and str(snap.get("kind") or "") != "machine") else init_pills_with_again(snap)
    lit = init_on(snap)
    lh_t, lh_c, lh_h = f_t.size * 1.3, f_c.size * 1.4, f_h.size * 1.4
    f_p = font("mono", 0.66 * u)
    pill_h = f_p.size + 0.9 * u if pills else 0
    block_h = lh_t * len(title) + (lh_c * len(coach) + 0.4 * u if coach else 0) + (lh_h * len(hint) + 0.3 * u if hint else 0) + (pill_h + 0.9 * u if pills else 0)
    y = H - 0.055 * H - block_h
    hx = W / 2.0
    d.line((hx - 1.6 * f_t.size, y - 1.1 * f_t.size, hx + 1.6 * f_t.size, y - 1.1 * f_t.size), fill=EMBER + (255,), width=1)
    if note:
        f_n = font("mono", 0.6 * u)
        nw = _text_w(d, note.upper(), f_n, 0.18)
        _text(d, ((W - nw) / 2.0, y - 2.6 * f_t.size), note.upper(), f_n, EMBER + (255,), 0.18)
    for ln in title:
        tw = d.textlength(ln, font=f_t)
        d.text(((W - tw) / 2.0 + 1, y + 2), ln, font=f_t, fill=GROUND + (170,))
        d.text(((W - tw) / 2.0, y), ln, font=f_t, fill=INK + (255,))
        y += lh_t
    if coach:
        y += 0.4 * u
        for ln in coach:
            tw = d.textlength(ln, font=f_c)
            d.text(((W - tw) / 2.0, y), ln, font=f_c, fill=INK2 + (255,))
            y += lh_c
    if hint:
        y += 0.3 * u
        for ln in hint:
            tw = d.textlength(ln, font=f_h)
            d.text(((W - tw) / 2.0, y), ln, font=f_h, fill=INK3 + (255,))
            y += lh_h
    if pills:
        y += 0.9 * u
        widths = [_text_w(d, lab, f_p, 0.14) + 1.6 * u for _, lab in pills]
        gap = 0.6 * u
        # the twelve hours wrap to two rows; everything else is one row
        rows: list[list[int]] = [[]]
        run = 0.0
        for i, w_ in enumerate(widths):
            if rows[-1] and run + w_ > 0.8 * W:
                rows.append([]); run = 0.0
            rows[-1].append(i); run += w_ + gap
        for row in rows:
            tw_all = sum(widths[i] for i in row) + gap * (len(row) - 1)
            x = (W - tw_all) / 2.0
            for i in row:
                name, lab = pills[i]
                box = (x, y, x + widths[i], y + pill_h)
                if name in lit:
                    d.rectangle((box[0], box[1], box[2], box[3]), fill=EMBER_DEEP + (220,), outline=EMBER + (255,))
                    _text(d, (x + 0.8 * u, y + 0.45 * u), lab, f_p, INK + (255,), 0.14)
                else:
                    d.rectangle((box[0], box[1], box[2], box[3]), fill=GROUND + (140,), outline=EMBER_DEEP + (255,))
                    _text(d, (x + 0.8 * u, y + 0.45 * u), lab, f_p, EMBER2 + (255,), 0.14)
                INIT_HITS[name] = box
                x += widths[i] + gap
            y += pill_h + 0.4 * u
    return im


def init_dial(d, cx: float, cy: float, r: float, *, legal=None, marks=None, sweep=None, hits: bool = False, door_hour: int = 12) -> None:
    """The room clock the page drew (init-wizard.js drawDial): a ring, twelve
    dots, twelve gold (the door), a mark per hour when asked; `sweep` is a
    hand angle (radians, 0 = twelve) while a rail step pans the camera;
    `hits` registers init_hour_N on every legal hour."""
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=INK3 + (255,), width=max(1, int(r * 0.03)))
    legal_set = set(int(h) for h in legal) if legal else set(range(1, 13))
    if sweep is not None:
        a = float(sweep) - math.pi / 2.0
        d.line((cx, cy, cx + math.cos(a) * r * 0.92, cy + math.sin(a) * r * 0.92), fill=EMBER + (200,), width=max(1, int(r * 0.03)))
        d.pieslice((cx - r * 0.92, cy - r * 0.92, cx + r * 0.92, cy + r * 0.92), math.degrees(a) - 40, math.degrees(a), fill=EMBER + (36,))
    for h in range(1, 13):
        a = (h / 12.0) * math.pi * 2.0 - math.pi / 2.0
        x, y = cx + math.cos(a) * r * 0.86, cy + math.sin(a) * r * 0.86
        rr = r * (0.055 if h == door_hour else 0.038)
        ok = h in legal_set
        col = EMBER if h == door_hour else (INK2 if ok else INK3)
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=col + (255 if ok or h == door_hour else 110,))
        if hits and ok:
            hb = r * 0.16
            INIT_HITS["init_hour_%d" % h] = (x - hb, y - hb, x + hb, y + hb)
    for h, (text, col) in (marks or {}).items():
        try:
            hh = int(h)
        except (TypeError, ValueError):
            continue
        a = (hh / 12.0) * math.pi * 2.0 - math.pi / 2.0
        x, y = cx + math.cos(a) * r * 0.62, cy + math.sin(a) * r * 0.62
        f_m = font("mono", max(8.0, r * 0.22))
        tw = d.textlength(str(text), font=f_m)
        d.text((x - tw / 2.0, y - f_m.size / 2.0), str(text), font=f_m, fill=tuple(col) + (255,))


# THE CUES RIDE HER WORDS (the User 2026-09-14, his script: "You are now facing 12 o'clock … See that
# middle dot? That is Crissa's seat … You then are sitting at 6 o'clock, as I have highlighted on
# the dial with your initials"). The voice's first byte, then her pace at the house rate (1.2).
ROOM_CLOCK_FIRST_BYTE_S = 1.5
ROOM_CLOCK_WPS = 2.9
ROOM_CLOCK_CUES = (("facing", "facing 12 o'clock"), ("crissa", "middle dot"), ("user", "sitting at 6"))
OFFICE_PLATE = Path(__file__).resolve().parents[1] / "display" / "crissa-poses" / "hub-down.png"
OFFICE_LABEL = "%s'S OFFICE" % crissa_home.her_name().upper()   # the house's name there (the Crissa merge)


def room_clock_cue_s(say: str, phrase: str) -> float:
    """Seconds into her line when `phrase` is spoken: its word index at her pace, after the first
    byte. 0 when the phrase is not in the line (the cue shows at once)."""
    words = re.findall(r"[a-z0-9']+", (say or "").lower())
    target = re.findall(r"[a-z0-9']+", phrase.lower())
    for k in range(len(words) - len(target) + 1):
        if words[k:k + len(target)] == target:
            return ROOM_CLOCK_FIRST_BYTE_S + k / ROOM_CLOCK_WPS
    return 0.0


def _room_clock_graphic(d, im, snap: dict, W: int, H: int, u: float, cx: float, cy: float, f_lab) -> None:
    """THE ORACLE CLOCK, HIGHLIGHTED (the User 2026-09-14): 12 lights as Lila says the User faces it,
    the middle dot (Crissa's seat) as she names it, the User's initials at 6 as she says so; the
    door, the sound and each regular land on their hours as the model hears them. Stage direction
    "crissa_seat": the live picture of the spot behind Crissa's seat beside her rendered office."""
    dlg = snap.get("dialogue") if isinstance(snap.get("dialogue"), dict) else {}
    fields = dlg.get("fields") if isinstance(dlg.get("fields"), dict) else {}
    if dlg.get("show") == "crissa_seat":
        bw = 0.16 * W
        bh = bw * 9.0 / 16.0
        gap = 0.8 * u
        for x0, lab in ((cx - bw - gap / 2.0, "THE ROOM, LIVE"), (cx + gap / 2.0, OFFICE_LABEL)):
            y0 = cy - bh / 2.0
            d.rectangle((x0 - 2, y0 - 2, x0 + bw + 2, y0 + bh + 2), fill=GROUND + (200,), outline=EMBER_DEEP + (255,))
            pic = None
            try:
                if lab == OFFICE_LABEL:
                    pic = Image.open(OFFICE_PLATE).convert("RGB")
                elif snap.get("_camera"):
                    pic = Image.open(io.BytesIO(snap["_camera"])).convert("RGB")
            except Exception:  # noqa: BLE001
                pic = None
            if pic is not None:
                pic.thumbnail((int(bw), int(bh)))
                im.paste(pic, (int(x0 + (bw - pic.width) / 2.0), int(y0 + (bh - pic.height) / 2.0)))
            tw = _text_w(d, lab, f_lab, 0.18)
            _text(d, (x0 + bw / 2.0 - tw / 2.0, y0 + bh + 0.8 * u), lab, f_lab, INK2 + (255,), 0.18)
        return
    r = 0.12 * H
    beat = int(snap.get("beat") or 0)
    since = float(snap.get("_t") or 0.0) - float(snap.get("asked_at") or 0.0)
    say = str((snap.get("plate") or {}).get("say") or "") if isinstance(snap.get("plate"), dict) else ""
    on = {cue: beat > 0 or since >= room_clock_cue_s(say, phrase) for cue, phrase in ROOM_CLOCK_CUES}
    # the clock she has highlighted: an ember halo while she walks them through it
    if beat == 0:
        for k_, a_ in ((1.10, 70), (1.05, 130)):
            d.ellipse((cx - r * k_, cy - r * k_, cx + r * k_, cy + r * k_), outline=EMBER + (a_,), width=max(2, int(r * 0.025)))
    marks: dict = {}
    if on["user"]:
        roster = snap.get("roster") if isinstance(snap.get("roster"), dict) else {}
        marks[6] = (user_mark(roster.get("user")) if roster.get("user") else "You", EMBER2)
    for name, hour in (fields.get("seats") or {}).items():
        try:
            h = int(hour)
        except (TypeError, ValueError):
            continue
        if 1 <= h <= 12 and h != 6:
            marks[h] = (str(name)[:2], EMBER2)
    try:
        if fields.get("media_hour"):
            marks[int(fields["media_hour"])] = ("TV", (0x9A, 0xB8, 0xC8))
        elif str(fields.get("media_mode") or "") == "internal":
            marks[12] = ("SRC", (0x9A, 0xB8, 0xC8))          # the monitor speakers: the screen they face is the source
        door = int(fields["door_hour"]) if fields.get("door_hour") else 0
    except (TypeError, ValueError):
        door = 0
    if door:
        marks[door] = ("DOOR", EMBER)
    init_dial(d, cx, cy, r, marks=marks, door_hour=door)
    if on["facing"]:
        a12 = -math.pi / 2.0
        x12, y12 = cx + math.cos(a12) * r * 0.86, cy + math.sin(a12) * r * 0.86
        rr = r * 0.11
        d.ellipse((x12 - rr, y12 - rr, x12 + rr, y12 + rr), outline=EMBER + (255,), width=max(2, int(r * 0.03)))
        f_f = font("mono", max(8.0, r * 0.13))
        tw = d.textlength("FACING", font=f_f)
        d.text((x12 - tw / 2.0, y12 - rr - f_f.size * 1.2), "FACING", font=f_f, fill=EMBER + (255,))
    if on["crissa"]:
        rz = r * 0.07
        d.ellipse((cx - rz, cy - rz, cx + rz, cy + rz), fill=EMBER + (255,))
        f_z = font("mono", max(8.0, r * 0.17))
        nm = crissa_home.her_name()
        tw = d.textlength(nm, font=f_z)
        d.text((cx - tw / 2.0, cy + rz + 0.2 * f_z.size), nm, font=f_z, fill=EMBER + (255,))
    label = "FACING 12 · %s'S SEAT IN THE MIDDLE · YOU AT 6" % crissa_home.her_name().upper()
    tw = _text_w(d, label, f_lab, 0.18)
    _text(d, (cx - tw / 2.0, cy + r * 1.12 + 0.8 * u), label, f_lab, INK2 + (255,), 0.18)


def _crissa_seat_popup(d, im, snap: dict, W: int, H: int, u: float, f_lab) -> None:
    """CENTER STAGE (the User 2026-09-14: "Lila Shows it in a popup at center stage"): the renderer's picture of
    Crissa in her seat, shot off at the room scan; the live photo until it lands."""
    seat = snap.get("crissa_seat") if isinstance(snap.get("crissa_seat"), dict) else {}
    path, lab = "", ""
    if seat.get("hd") and Path(str(seat["hd"])).is_file():
        path, lab = str(seat["hd"]), "%s IN HER SEAT" % crissa_home.her_name().upper()
    elif seat.get("live") and Path(str(seat["live"])).is_file():
        path, lab = str(seat["live"]), "HER SEAT, LIVE"
    bw = 0.44 * W
    bh = bw * 9.0 / 16.0
    x0, y0 = (W - bw) / 2.0, 0.08 * H
    d.rectangle((x0 - 3, y0 - 3, x0 + bw + 3, y0 + bh + 3), fill=GROUND + (240,), outline=EMBER + (255,), width=2)
    pic = None
    try:
        pic = Image.open(path).convert("RGB") if path else None
    except Exception:  # noqa: BLE001
        pic = None
    if pic is not None:
        pic.thumbnail((int(bw), int(bh)))
        im.paste(pic, (int(x0 + (bw - pic.width) / 2.0), int(y0 + (bh - pic.height) / 2.0)))
    else:
        lab = "WAIT WHILE WE PROCESS…"
    tw = _text_w(d, lab, f_lab, 0.18)
    _text(d, (W / 2.0 - tw / 2.0, y0 + bh + 0.8 * u), lab, f_lab, INK2 + (255,), 0.18)


def _init_graphic(d, im, snap: dict, W: int, H: int, u: float) -> None:
    """The step's picture, right of the film: the dial, the door still, the
    overnight range. Nothing for the steps that are only words."""
    sid = str(snap.get("step_id") or "")
    ans = (snap.get("answers") or {}).get(sid) or {}
    cx, cy = 0.80 * W, 0.42 * H
    f_lab = font("mono", 0.62 * u)
    if sid == "room_clock":
        _room_clock_graphic(d, im, snap, W, H, u, cx, cy, f_lab)
        return
    if sid == "crissa_seat":
        _crissa_seat_popup(d, im, snap, W, H, u, f_lab)
        return
    if sid in INIT_DIAL_STEPS:
        r = 0.085 * H
        legal = snap.get("legal_hours") or list(range(1, 13))
        marks: dict = {}
        sweep = None
        hits = False
        label = "THE ROOM · TWELVE IS THE DOOR"
        if sid == "media_speakers":
            if not (isinstance(ans, dict) and ans.get("need_hour")):
                return
            hits = True
            label = "SAY THE HOUR THE SPEAKERS SIT ON"
            mh = snap.get("media_hour")
            if mh:
                marks[int(mh)] = ("×", (0xB5, 0x60, 0x54))
        elif sid == "seat_map":
            hits = bool(init_people(snap))
            tg = init_toggles(snap)
            people = dict(init_people(snap))
            for pid, h in tg.items():
                if pid.startswith("_") or not h:
                    continue
                marks[int(h)] = ((people.get(pid) or pid)[:1].upper(), EMBER2)
            who = init_seat_who(snap)
            label = ("WHERE DOES %s SIT?" % (people.get(who) or who).upper()) if who else "THE ROOM"
            if not legal:
                legal = list(range(1, 13))
        elif snap.get("rail_busy"):
            sweep = (float(snap.get("_t") or 0.0) % 4.0) / 4.0 * math.pi * 2.0
            label = "LOOKING AROUND"
        init_dial(d, cx, cy, r, legal=legal, marks=marks, sweep=sweep, hits=hits and not CANCEL_ONLY)
        tw = _text_w(d, label, f_lab, 0.18)
        _text(d, (cx - tw / 2.0, cy + r + 0.8 * u), label, f_lab, INK2 + (255,), 0.18)
        return
    if sid in INIT_STILL_STEPS:
        raw = snap.get("_camera")
        bw = 0.20 * W
        bh = bw * 9.0 / 16.0
        x0, y0 = cx - bw / 2.0, cy - bh / 2.0
        d.rectangle((x0 - 2, y0 - 2, x0 + bw + 2, y0 + bh + 2), fill=GROUND + (200,), outline=EMBER_DEEP + (255,))
        if raw:
            try:
                pic = Image.open(io.BytesIO(raw)).convert("RGB")
                pic.thumbnail((int(bw), int(bh)))
                im.paste(pic, (int(x0 + (bw - pic.width) / 2.0), int(y0 + (bh - pic.height) / 2.0)))
            except Exception:  # noqa: BLE001
                raw = None
        if not raw:
            f_w = font("mono", 0.7 * u)
            msg = "THE DOOR CAMERA"
            tw = d.textlength(msg, font=f_w)
            d.text((cx - tw / 2.0, cy - f_w.size / 2.0), msg, font=f_w, fill=INK3 + (255,))
        label = "THE PICTURE, LIVE" if sid == "camera_orientation" else "THE DOOR, CLOSED"
        tw = _text_w(d, label, f_lab, 0.18)
        _text(d, (cx - tw / 2.0, y0 + bh + 0.8 * u), label, f_lab, INK2 + (255,), 0.18)
        return
    if sid == "training_window":
        win = str((ans.get("window") if isinstance(ans, dict) else "") or INIT_RANGE_DEFAULT).replace("-", " – ")
        f_r = font("serif", 0.028 * W)
        tw = d.textlength(win, font=f_r)
        d.rectangle((cx - tw / 2.0 - 1.2 * u, cy - f_r.size * 0.9, cx + tw / 2.0 + 1.2 * u, cy + f_r.size * 0.9), fill=GROUND + (140,), outline=EMBER_DEEP + (255,))
        d.text((cx - tw / 2.0, cy - f_r.size / 2.0), win, font=f_r, fill=INK + (255,))
        label = "OVERNIGHT, THE HOUSE PRACTISES"
        tw = _text_w(d, label, f_lab, 0.18)
        _text(d, (cx - tw / 2.0, cy + f_r.size * 0.9 + 0.8 * u), label, f_lab, INK2 + (255,), 0.18)


ANCHORS_PATH = crissa_home.HOME / "house-wall-ascii.anchors.json"


def write_anchors() -> None:
    try:
        ANCHORS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = ANCHORS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(ANCHORS), encoding="utf-8")
        tmp.replace(ANCHORS_PATH)
    except OSError:
        pass

ROLL_CROSS_S = 55.0     # the page: seconds for a line to cross the frame


# THE LOG COLUMN IS LEFT-JUSTIFIED, AND RUNS UNDER THE DIAL (the User 2026-10-12, .101 round 14, final: "Scoot it to
# left justify for same reasons as on regular monitors, to keep off the ascii image of agent", "better under dials than
# over ascii"). The house always did this: its roll starts at the page's left edge and passes behind the oracle dial
# (the roll's layer sits under the HUD's). A pack does the same now, on every glass. The 10-10 rule (every line right
# of the dial's column) and round 13's glide are gone; HOUSE_WALL_ROLL_CLEAR_DIAL=1 brings back the 10-10 rule.
def _roll_clear_dial() -> bool:
    return os.environ.get("HOUSE_WALL_ROLL_CLEAR_DIAL", "").strip() == "1"


ROLL_CLEAR_DIAL = _roll_clear_dial()
# HER NAME ON HER LINES (the User 2026-10-12, .101: "Crissa is popping up in logs from other machine"). Her lines carry
# her face's profile key, `crissa`, on the wire (the tts gate, the mouth's air, the tool roll; the ear and the vetoes
# read it), and the roll printed the key. The label is her name now (crissa_home.her_name(): the house's own config
# says Crissa, so the house reads exactly as before; a pack says Crissa). The wire keeps the key.
ROLL_HER_KEYS = ("crissa",)


@lru_cache(maxsize=1)
def _her_label() -> str:
    try:
        return str(crissa_home.her_name() or "").strip().upper() or "CRISSA"
    except Exception:  # noqa: BLE001 - a label never stops the roll
        return "CRISSA"


# HER LINES ALWAYS LOG (the User 2026-10-12, .101 round 14: "Crissa lines SHOULD log", her own lines missing from the
# desk Wall's roll). The desk Wall filed her lines under the room's talk (class "other"), so the LOGS ROOM chip hid
# them; the browser page never counts her lines as the room's. They are class "her" now, shown whatever the ROOM chip
# says, as on the page (a pack; the house keeps today's chips, HOUSE_WALL_ROLL_HER_CLASS=1 matches its page). In a
# pack, an echo of her own line (the ear's copy of what she just said, logged as a stranger
# or with no name) is not a second line: a line from nobody whose words are all inside one of her own lines of the last
# ROLL_ECHO_S seconds is dropped (a line that carries anyone else's words as well is kept) (the ear drops these at the source since round 13; this is the glass's belt).
# HOUSE_WALL_ROLL_HER_CLASS=0 files her lines as the room's again; HOUSE_WALL_ROLL_ECHO=0 keeps the echoes.
ROLL_ECHO_S = 30.0
ROLL_NOBODY = ("stranger", "unknown", "", "?")


def _roll_her_class() -> bool:
    raw = os.environ.get("HOUSE_WALL_ROLL_HER_CLASS", "").strip()
    if raw:
        return raw != "0"
    try:
        return not crissa_home.is_house()        # the house's desk Wall keeps today's chips until the User says
    except Exception:  # noqa: BLE001
        return True


def _roll_drop_echo() -> bool:
    # HOUSE_WALL_ROLL_ECHO=1 drops her echoes, =0 keeps them, as the comment above says (2026-10-21: the code read
    # `raw == "0"`, so a drop-in meaning "drop" kept them and one meaning "keep" dropped them; nothing had set it).
    raw = os.environ.get("HOUSE_WALL_ROLL_ECHO", "").strip()
    if raw:
        return raw != "0"
    try:
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001
        return False


ROLL_HER_CLASS = _roll_her_class()
ROLL_DROP_ECHO = _roll_drop_echo()
# ONE SPEAKER FOR ONE LINE (round 17, .101 2026-10-13 05:55: "Keep reading, please." was a stranger line from the ear and
# a User line from the bridge). The feed's heard row names the ear's row it stands for (`replaces`, VO_CHAT_ONE_ROW);
# when that row is already on the roll it is rewritten in place, never a second line. No house row carries `replaces`.
# Kill: HOUSE_WALL_ROLL_REPLACES=0.
ROLL_REPLACES = os.environ.get("HOUSE_WALL_ROLL_REPLACES", "1").strip().lower() not in ("0", "false", "no", "off")


def _roll_replaced(item: dict) -> set:
    """The (who, text, ts) keys of the rows this chat item stands for (`replaces`), malformed entries skipped."""
    out = set()
    for r in item.get("replaces") or ():
        try:
            if isinstance(r, (list, tuple)) and len(r) == 3:
                out.add((str(r[0]), str(r[1]).strip(), round(float(r[2] or 0), 3)))
        except (TypeError, ValueError):
            continue
    return out


# A NAME BEFORE THE ROW SCROLLS OFF, AND HER WORDS AS THEY STREAM (round 18, the User 2026-10-13: "names on the rows
# before they scroll off; her words streamed live, before synthesis if possible, with any unspoken rest marked when she
# is cut"). Three things a pack's feed (crissa/overlay/scripts/voiceonly-home-server.py) now sends, none of which a
# house row carries:
#   ROLL_ANY_WHO   a `replaces` key whose speaker is "*" stands for its line under whatever name it was drawn (a row
#                  first drawn as "probably <name>" and then named by a recogniser is rewritten in place)
#   guess          the speaker is the feed's best guess ("probably <name>": the person last named in this conversation,
#                  or the install's only person), drawn as such in the speaker's column; a later name rewrites it
#   rid            her reply as ONE line that grows in place while she streams it (`open`: it holds the floor, newest
#                  words at the bottom), the part her voice has not reached dimmed (`voice_n`), and after a stop or a
#                  reply she did not finish, the part she never said struck through with "(not said)" (`said_n`). A
#                  line that has left the roll never comes back.
ROLL_ANY_WHO = "*"
ROLL_NOT_SAID = "(not said)"


def _roll_any(old: set) -> set:
    return {(t, s) for w, t, s in old if w == ROLL_ANY_WHO}


def _roll_words(text: str, ln: dict) -> list:
    """(word, style) for a line of hers: said | ahead (her voice has not reached it) | unsaid (never said) | mark."""
    voice_n, said_n = ln.get("voice_n"), ln.get("said_n")
    out = []
    for m in re.finditer(r"\S+", text or ""):
        st = "said"
        if ln.get("open") and voice_n is not None and m.start() >= int(voice_n):
            st = "ahead"
        elif not ln.get("open") and said_n is not None and m.start() >= int(said_n):
            st = "unsaid"
        out.append((m.group(0), st))
    if not ln.get("open") and said_n is not None and int(said_n) < len(text or ""):
        out.append((ROLL_NOT_SAID, "mark"))
    return out


def _wrap_words(d, words: list, f, width) -> list:
    """_wrap_greedy over (word, style) pairs: lines of pairs."""
    lines, cur, cur_s = [], [], ""
    for w_, st in words:
        cand = (cur_s + " " + w_).strip()
        if cur and d.textlength(cand, font=f) > width:
            lines.append(cur)
            cur, cur_s = [(w_, st)], w_
        else:
            cur.append((w_, st))
            cur_s = cand
    if cur:
        lines.append(cur)
    return lines


def _echo_words(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower()).split())


# HER ECHO IS HEARD WITH SLIPS (the 10-21 teaching take, 09:18:27-09:18:47 ET): the ear's copy of her line read "Sides
# by three ... The is necessary ... the inverse operation that does multiplication ... putting five back", so it was
# never an exact piece of what she said and the belt kept it as a STRANGER row and a "?" row. A line from nobody is
# also her echo when at least ROLL_ECHO_SHARE of its words are words of one of her recent lines and it has at least
# ROLL_ECHO_MIN_WORDS words (a short line like "stop" is never taken as an echo). Dial HOUSE_WALL_ROLL_ECHO_SHARE
# (0..1; 0 = the exact match only, as before). Measured on that take: her echo shares 0.93 and 0.94 of its words;
# "My mum made fung jam. Hey Crissa, stop!" against her fung-jam line shares 0.62 and stays.
def _echo_share_dial() -> float:
    try:
        v = float(os.environ.get("HOUSE_WALL_ROLL_ECHO_SHARE", "0.85"))
    except ValueError:
        v = 0.85
    return min(1.0, max(0.0, v))


ROLL_ECHO_SHARE = _echo_share_dial()
ROLL_ECHO_MIN_WORDS = 6


def _echo_share(heard: str, hers: str) -> float:
    w = heard.split()
    if len(w) < ROLL_ECHO_MIN_WORDS:
        return 0.0
    mine = set(hers.split())
    return sum(1 for x in w if x in mine) / len(w)


# SPEAKER NAMES HER WHILE SHE TALKS (the User 2026-10-12, .101 round 14: "Speaking should say her"). A pack's HUD
# row says her name while her line is on the air (a live line of hers in the house's chat, or the ear bridge's
# `speaking`), then goes back to the person. The house keeps its 09-07 rule (her turn never rewrites the row);
# HOUSE_WALL_SPEAKER_NAMES_HER=1 / 0 forces either way.
def speaker_names_her() -> bool:
    raw = os.environ.get("HOUSE_WALL_SPEAKER_NAMES_HER", "").strip()
    if raw:
        return raw != "0"
    try:
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001
        return False


def she_is_speaking(house: dict | None) -> bool:
    h = house or {}
    if (h.get("hear") or {}).get("speaking") is True:
        return True
    return any(isinstance(c, dict) and c.get("live") and str(c.get("who") or "").lower() in ROLL_HER_KEYS
               for c in (h.get("chat") or []))


# A NOTE IS NOT HER LINE (round 19, .101 2026-10-14): a row the feed marks `note` (the session's "Listening off ..." and
# a cancel's summary) is drawn under this label, dimmed - never under her name. The feed's CRISSA_LOG_NOTES=0 is the kill.
NOTE_LABEL = "Note:"


def roll_label(who: str) -> str:
    """The roll's label for a line's speaker key: her name for her own face's key, else the key itself."""
    w = str(who or "")
    return _her_label() if w in ROLL_HER_KEYS else w.upper()


# THE ROLL SCROLLS AND FADES LINE BY LINE (round 27, .101 2026-10-16, asked in round 25 and again: "scroll smoothly up from
# the bottom, fade toward the top"). A pack's default; the house keeps its roll until the User says (=1 turns it on there).
# Kill: HOUSE_WALL_ROLL_SMOOTH=0 (a block fades by its top and grows a line at once).
ROLL_SMOOTH = os.environ.get("HOUSE_WALL_ROLL_SMOOTH", "0" if crissa_home.is_house() else "1").strip().lower() not in (
    "0", "false", "no", "off")
ROLL_RISE_S = 0.45                 # one wrapped line's rise from the floor


def roll_third_on() -> bool:
    """Round 38 (.101 round 37, the User: "wrap the Wall's log lines narrower: to the left third of the screen"). A pack
    only; kill CRISSA_ROLL_THIRD=0."""
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_ROLL_THIRD", "1").strip().lower() not in ("0", "off", "false", "no")


_SHIFT_SEEN = {"at": 0.0, "on": False}


def roll_clear_of_face(now: float | None = None) -> bool:
    """The roll keeps off her face while a crop slide is up (the User 2026-10-23, the demo lap: "her lips not moving!").
    A document on the letter slides her picture left (crissa-render-shift.json, why "doc"), which put her mouth under the
    full-width roll: the mouth was painted on 84 % of her spoken frames and hidden by her own transcript. While a
    slide is on, the roll wraps in the left third, as the pack's roll already does. No slide = today's roll, byte for
    byte. The file is read at most once a second. Kill HOUSE_WALL_ROLL_CLEAR_FACE=0."""
    if os.environ.get("HOUSE_WALL_ROLL_CLEAR_FACE", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    now = time.time() if now is None else now
    if now - _SHIFT_SEEN["at"] >= 1.0:
        _SHIFT_SEEN["at"] = now
        _SHIFT_SEEN["on"] = render_shift() is not None
    return bool(_SHIFT_SEEN["on"])


class Roll:
    """The credits roll. A line enters at the floor and drifts up at H/55 px/s,
    dissolving into the mask at the top. Speech before this object was made
    does not enter (the page's rollSince). Its own layer: cheap to redraw."""

    def __init__(self, since: float | None = None):
        self.since = time.time() if since is None else since
        self.seen: set = set()
        self.lines: list = []      # dicts: who, text, at (entered)
        self.her_said: list = []   # (when, words) of her own lines, for the echo test
        self.rids_gone: set = set()   # round 18: her grown lines that have left the roll (never back)

    def _feed_rid(self, item: dict, rid: str, text: str, ts: float, now: float) -> None:
        """Her reply's one line (round 18, `rid`): grown in place; an open one holds the floor."""
        fields = {"text": text, "ts": ts, "open": bool(item.get("open")), "voice_n": item.get("voice_n"),
                  "said_n": item.get("said_n"), "cut": bool(item.get("cut"))}
        for ln in self.lines:
            if ln.get("rid") == rid:
                was_open = bool(ln.get("open"))
                ln.update(fields)
                if was_open:
                    ln["at"] = now               # open: it stays at the floor; closed now: its climb starts there
                return
        if rid in self.rids_gone:
            return
        who = str(item.get("who") or "")
        cls = "her" if (who.lower() in ROLL_HER_KEYS and ROLL_HER_CLASS) else "other"
        self.lines.append(dict(fields, who=who, at=now, cls=cls, rid=rid))

    def feed(self, chat, now: float | None = None):
        now = time.time() if now is None else now
        # her own words first, from the whole batch: an echo can come before her line in the same poll
        for item in (chat or []):
            if item.get("note"):
                continue                         # round 19: a note is not her words
            if str(item.get("who") or "").lower() in ROLL_HER_KEYS and str(item.get("text") or "").strip():
                w_ = _echo_words(item.get("text"))
                if not any(hs == w_ for _at, hs in self.her_said):
                    self.her_said.append((now, w_))
        for item in (chat or []):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            ts = float(item.get("ts") or 0)
            if ts < self.since:
                continue
            if item.get("rid"):
                self._feed_rid(item, str(item["rid"]), text, ts, now)    # round 18: her words as they stream
                continue
            key = (str(item.get("who") or ""), text, round(ts, 3))
            if key in self.seen:
                for ln in self.lines:            # round 18: the same name, now a recogniser's and not a guess
                    if ln.get("guess") and not item.get("guess") and \
                            (str(ln["who"]), ln["text"], round(float(ln["ts"]), 3)) == key:
                        ln["guess"] = False
                continue
            self.seen.add(key)
            # the kiosk's line classes: board / tool / speech
            # from-other (room talk) / the User's own (log_user) / hers (2026-10-12)
            kind = str(item.get("kind") or "")
            src = str(item.get("source") or "")
            her = key[0].lower() in ROLL_HER_KEYS
            if not her and ROLL_DROP_ECHO and (key[0].lower() in ROLL_NOBODY or item.get("guess")) \
                    and kind not in ("board", "tool"):
                w_ = _echo_words(text)
                if w_ and any(now - at < ROLL_ECHO_S and (w_ == hs or (len(w_) >= 12 and w_ in hs)
                                                         or (ROLL_ECHO_SHARE > 0 and _echo_share(w_, hs) >= ROLL_ECHO_SHARE))
                              for at, hs in self.her_said):
                    continue                     # her own words, heard back: not a line of its own
            if item.get("note"):
                cls = "note"                     # round 19: the system's line (status, cancel), never under her name
            elif kind == "board":
                cls = "board"
            elif kind == "tool" or src == "tool_callout":
                cls = "tool"
            elif item.get("log_user") is True:
                cls = "user"
            elif her and ROLL_HER_CLASS:
                cls = "her"
            else:
                cls = "other"
            line = {"who": key[0], "text": text, "ts": ts, "at": now, "cls": cls}
            if item.get("guess"):
                line["guess"] = True             # round 18: "probably <name>"
            old = _roll_replaced(item) if ROLL_REPLACES else set()
            if old:
                anyk = _roll_any(old)            # round 18: "*" - whatever name it was drawn under
                self.seen.update(k for k in old if k[0] != ROLL_ANY_WHO)   # the ear's own row never comes back
                hit = [ln for ln in self.lines if not ln.get("rid") and (
                    (str(ln["who"]), ln["text"], round(float(ln["ts"]), 3)) in old
                    or (ln["text"], round(float(ln["ts"]), 3)) in anyk)]
                if hit:
                    line["at"] = hit[0]["at"]    # rewritten where it stands: it does not jump back to the floor
                    self.lines = [line if ln is hit[0] else ln for ln in self.lines
                                  if ln is hit[0] or all(ln is not h for h in hit)]
                    continue
            self.lines.append(line)
        # drop what has crossed the frame and then some
        gone = [ln for ln in self.lines if (now - ln["at"]) >= ROLL_CROSS_S * 1.2]
        self.rids_gone.update(str(ln["rid"]) for ln in gone if ln.get("rid"))
        self.lines = [ln for ln in self.lines if (now - ln["at"]) < ROLL_CROSS_S * 1.2]
        self.her_said = [hs for hs in self.her_said if now - hs[0] < ROLL_ECHO_S]

    def floor(self, g: Geom) -> float:
        """Entry lip. Clears the camera PiP (the page parks over it and drifts;
        with a still frame that reads as text on the camera)."""
        return g.H - g.pip_edge - g.pip_h - 0.45 * g.u

    @staticmethod
    def shown(ln: dict, prefs: dict | None) -> bool:
        """The LOGS chips (the User 2026-09-07): body:not(.log-room) hides the
        room's speech (USER ONLY keeps the User's), the board toggle the board
        chatter, .log-tools the tool callouts. No prefs = everything."""
        if not prefs:
            return True
        cls = ln.get("cls") or "other"
        if cls == "board":
            return bool(prefs.get("log_board", True))
        if cls == "tool":
            return bool(prefs.get("log_tools", True))
        if cls == "other":
            return bool(prefs.get("log_room", True))
        return True

    def draw(self, g: Geom, now: float | None = None, prefs: dict | None = None) -> Image.Image:
        now = time.time() if now is None else now
        W, H, u = g.W, g.H, g.u
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        self.placed = []                           # (line, top, bottom, alpha) drawn this frame (round 18's bench)
        if not self.lines:
            return layer
        lines_shown = [ln for ln in self.lines if self.shown(ln, prefs)]
        if not lines_shown:
            return layer
        d = ImageDraw.Draw(layer)
        f_who = font("mono", 0.85 * u)
        f_said = font("mono", 1.0 * u)
        left = g.page_left
        if ROLL_CLEAR_DIAL:
            # the 10-10 rule, opt-in since 2026-10-12: every line right of the oracle dial's column
            left = g.page_left + g.oracle_box()[0] + 1.2 * u
        right = g.rail_w - 2.4 * u
        if roll_third_on() or roll_clear_of_face():
            right = min(right, g.W / 3.0)          # round 38: her log wraps in the left third of the glass
        who_col = 5.4 * u + 1.0 * u
        maxw = right - (left + who_col)
        speed = H / ROLL_CROSS_S
        floor = self.floor(g)
        gap = 0.5 * u
        prev_top = None
        smooth = ROLL_SMOOTH
        dt = max(0.0, min(0.5, now - float(getattr(self, "_drawn_at", now) or now)))
        self._drawn_at = now
        vt = ANCHORS.get("vitals_tog")
        end_y = (float(vt[1]) + float(vt[3]) + 0.6 * u) if vt else 0.22 * H

        def fade(y_top: float) -> float:
            # the ribbon's mask, floor to just under the LOGS row: opaque for the first 18 % of the climb, .55 at 45 %,
            # ZERO at the caret under LOGS (the User 2026-09-06). Without the vitals anchor the end is 22 % from the top.
            fr = (floor - y_top) / max(1.0, floor - end_y)
            if fr <= 0.18:
                return 1.0
            if fr <= 0.45:
                return 1.0 - 0.45 * (fr - 0.18) / 0.27
            if fr <= 1.0:
                return 0.55 * (1 - (fr - 0.45) / 0.55)
            return 0.0
        for ln in reversed(lines_shown):          # newest first, from the floor up
            styled = _wrap_words(d, _roll_words(ln["text"], ln), f_said, maxw) if ln.get("rid") else None
            lines = styled if styled is not None else _wrap_greedy(d, ln["text"], f_said, maxw)
            block_h = len(lines) * f_said.size * 1.32
            bottom = floor - (0.0 if ln.get("open") else (now - ln["at"])) * speed
            if smooth:
                # ROUND 27 (.101 2026-10-16: her reading "sits as a fixed block at the bottom, older text just drops
                # off the top"): a block that grew is let up to its new height over ROLL_RISE_S, so each new line
                # rises in from the floor instead of the block jumping a whole line.
                shown_h = float(ln.get("_shown_h") or 0.0) if ln.get("_shown_h") is not None else block_h
                if shown_h < block_h:
                    shown_h = min(block_h, shown_h + (f_said.size * 1.32) * dt / ROLL_RISE_S)
                else:
                    shown_h = block_h
                ln["_shown_h"] = shown_h
                bottom += block_h - shown_h
            if prev_top is not None:
                bottom = min(bottom, prev_top - gap)   # stack: never under the newer line
            top = bottom - block_h
            prev_top = top
            if bottom < 0:
                continue
            a = fade(top)
            # ROUND 27: each wrapped line fades by its own height (a tall reading's first lines go dim toward the top
            # while its newest stay full), not the whole block by its top. Kill: HOUSE_WALL_ROLL_SMOOTH=0.
            a_last = fade(bottom - f_said.size * 1.32) if smooth else a
            self.placed.append((ln, top, bottom, a))
            if max(a, a_last) <= 0.01:
                continue
            a8 = int(255 * a)                     # the speaker's label goes with the block's first line
            who = ln["who"]
            whocol = EMBER if who not in ("stranger", "unknown", "") else INK3
            if who == "oracle":
                whocol = INK2
            lx = left
            note = ln.get("cls") == "note"
            if note:
                _text(d, (lx, top + (f_said.size - f_who.size)), NOTE_LABEL, f_who, INK3 + (a8,), 0.16)
            elif ln.get("guess"):
                # round 18: the feed's guess, "probably <name>", fitted to the speaker's column, in the unknown's ink
                lab = "probably " + roll_label(who)
                fit = max(0.5, min(1.0, (who_col - 0.6 * u) / max(1.0, _text_w(d, lab, f_who, 0.16))))
                f_g = font("mono", 0.85 * u * fit) if fit < 1.0 else f_who
                _text(d, (lx, top + (f_said.size - f_g.size)), lab, f_g, INK3 + (a8,), 0.16)
            else:
                _text(d, (lx, top + (f_said.size - f_who.size)), roll_label(who), f_who, whocol + (a8,), 0.16)
            ly = top
            for t_ in lines:
                if smooth:
                    lh = f_said.size * 1.32
                    rise = max(0.0, min(1.0, (floor - ly) / lh))          # a line still rising from the floor fades in
                    a8 = int(255 * fade(ly) * rise)
                    if a8 <= 2:
                        ly += f_said.size * 1.32
                        continue
                if styled is None:
                    d.text((lx + who_col, ly), t_, font=f_said, fill=(INK2 if note else INK) + (a8,))
                else:
                    x_ = lx + who_col
                    sp_ = d.textlength(" ", font=f_said)
                    for w_, st in t_:
                        col = INK2 if st == "ahead" else (INK3 if st in ("unsaid", "mark") else INK)
                        d.text((x_, ly), w_, font=f_said, fill=col + (a8,))
                        ww = d.textlength(w_, font=f_said)
                        if st == "unsaid":     # never said: struck through
                            ym = ly + f_said.size * 0.55
                            d.line((x_, ym, x_ + ww, ym), fill=INK3 + (a8,), width=max(1, int(f_said.size * 0.07)))
                        x_ += ww + sp_
                ly += f_said.size * 1.32
        return layer


def _wrap_greedy(d, text, f, width):
    line, lines = "", []
    for w_ in text.split():
        cand = (line + " " + w_).strip()
        if d.textlength(cand, font=f) > width and line:
            lines.append(line)
            line = w_
        else:
            line = cand
    if line:
        lines.append(line)
    return lines


def _wrap_balanced(d, text, f, maxw):
    """CSS text-wrap:balance — same line count as greedy at maxw, narrowest width."""
    def greedy(width):
        return _wrap_greedy(d, text, f, width)
    best = greedy(maxw)
    n = len(best)
    lo, hi = maxw * 0.5, maxw
    for _ in range(14):
        mid = (lo + hi) / 2
        cand = greedy(mid)
        if len(cand) <= n:
            best, hi = cand, mid
        else:
            lo = mid
    return best


def render(frame: bytes | None, camera: bytes | None, house: dict | None,
           presence: dict | None, W: int = 2560, H: int = 1440,
           t: float | None = None, stale: bool = False, roll: "Roll | None" = None) -> Image.Image:
    """One complete frame (used by --preview and the smoke test)."""
    t = time.time() if t is None else t
    g = Geom(W, H)
    crissa, cam = _open(frame), _open(camera)
    src, crissa_own = (crissa, True) if crissa is not None else (cam, False)
    hz = 1.0 if crissa is not None else None
    wall = render_background(src, g, crissa_own, t, hz).convert("RGBA")
    if roll is None:
        roll = Roll(since=0.0)                # preview: show what the house has
        roll.feed((house or {}).get("chat"), now=t)
    wall.alpha_composite(roll.draw(g, t))
    wall.alpha_composite(render_hud(house or {}, presence or {}, cam, g, t, crissa is not None, stale))
    if crissa is not None:
        ft, pos = render_ft(crissa, g)
        wall.alpha_composite(ft, pos)
    return wall.convert("RGB")


# ---- demo CLI ----------------------------------------------------------------
if __name__ == "__main__":
    import argparse, json, sys
    ap = argparse.ArgumentParser(description="Render one wall frame from captured files.")
    ap.add_argument("--frame", required=True, help="frame.jpg from :8100")
    ap.add_argument("--camera", help="door camera jpeg (optional)")
    ap.add_argument("--house", required=True, help="house.json from :8099/v1/house")
    ap.add_argument("--presence", help="presence.json from :8191/v1/presence")
    ap.add_argument("--out", default="wall-preview.png")
    ap.add_argument("--size", default="2560x1440")
    a = ap.parse_args()
    W, H = (int(v) for v in a.size.lower().split("x"))
    frame = open(a.frame, "rb").read()
    camera = open(a.camera, "rb").read() if a.camera else None
    house = json.load(open(a.house))
    presence = json.load(open(a.presence)) if a.presence else {}
    t0 = time.time()
    im = render(frame, camera, house, presence, W, H)
    im.save(a.out)
    print(f"wrote {a.out} {W}x{H} in {time.time() - t0:.2f}s", file=sys.stderr)


# ── THE CINEMA LAYER (first boot, 2026-09-12 pm) ────────────────────────────
# Crissa's HD intro is painted BY the wall — never a second window (mpv over
# the Tk wall broke the stacking). One W×H RGBA image per frame: a dark
# ground with the video inside a rect that eases from full screen to the
# centre "room-view" box (k: 0 = full, 1 = box) as she introduces Lila; at
# the end the box shrinks to nothing while the ground fades out (fold), and
# the film is under it. docs/designs/FIRST-BOOT-INTRO-20260912.md.
CINEMA_BOX_W = 0.42          # the room-view box, as a fraction of W (16:9)
CINEMA_BOX_CY = 0.42         # its centre, as a fraction of H
CINEMA_GROUND = (0x0B, 0x0D, 0x11)


def cinema_rect(W: int, H: int, k: float, fold: float = 0.0) -> tuple[int, int, int, int]:
    """Where the video sits. k eases full → box; fold (0..1) shrinks the box
    to a point at its centre. Integer x0, y0, w, h (w, h ≥ 2)."""
    k = max(0.0, min(1.0, float(k)))
    bw = CINEMA_BOX_W * W
    bh = bw * 9.0 / 16.0
    bx = (W - bw) / 2.0
    by = CINEMA_BOX_CY * H - bh / 2.0
    x0 = bx * k
    y0 = by * k
    w = W + (bw - W) * k
    h = H + (bh - H) * k
    if fold > 0:
        f = 1.0 - max(0.0, min(1.0, float(fold)))
        cx, cy = x0 + w / 2.0, y0 + h / 2.0
        w, h = w * f, h * f
        x0, y0 = cx - w / 2.0, cy - h / 2.0
    return int(round(x0)), int(round(y0)), max(2, int(round(w))), max(2, int(round(h)))


def cinema_frame(frame_rgb: Image.Image, W: int, H: int, k: float, fold: float = 0.0,
                 ground_alpha: int = 255) -> Image.Image:
    """The layer for one video frame: ground + the frame fitted into
    cinema_rect (aspect kept, letterboxed inside the rect if needed)."""
    x0, y0, w, h = cinema_rect(W, H, k, fold)
    im = _cinema_ground(W, H, int(ground_alpha))
    if w < 4 or h < 4:
        return im
    fw, fh = frame_rgb.size
    s = min(w / fw, h / fh)
    tw, th = max(1, int(fw * s)), max(1, int(fh * s))
    # 1080p at 24 fps (the User's HD intro, 2026-09-13): a fresh ground + an RGBA
    # convert per frame cost 44 ms, over the 41.7 ms budget; the ground is
    # cached and an RGB paste keeps the alpha opaque without a convert.
    fit = frame_rgb if (tw, th) == (fw, fh) else frame_rgb.resize((tw, th), Image.Resampling.BILINEAR)
    if fit.mode != "RGB":
        fit = fit.convert("RGB")
    im.paste(fit, (x0 + (w - tw) // 2, y0 + (h - th) // 2))
    return im


_CINEMA_GROUND_MEMO: dict = {}


def _cinema_ground(W: int, H: int, alpha: int) -> Image.Image:
    key = (W, H, alpha)
    g = _CINEMA_GROUND_MEMO.get(key)
    if g is None:
        g = Image.new("RGBA", (W, H), CINEMA_GROUND + (alpha,))
        _CINEMA_GROUND_MEMO.clear()
        _CINEMA_GROUND_MEMO[key] = g
    return g.copy()


def cinema_box_at(wav_path, lo: float = 0.25, hi: float = 0.75) -> float | None:
    """When Crissa steps down to the box: the middle of the longest silence in
    the [lo, hi] stretch of her audio (the pause between "…my new boss." and
    "Here's what happens next"). None when the file will not read."""
    try:
        import wave
        import numpy as np
        with wave.open(str(wav_path)) as w:
            sr = w.getframerate()
            n = w.getnframes()
            a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64) / 32768.0
            if w.getnchannels() > 1:
                a = a.reshape(-1, w.getnchannels()).mean(axis=1)
    except Exception:
        return None
    hop = max(1, int(sr * 0.02))
    e = np.array([np.sqrt((a[i:i + hop] ** 2).mean()) for i in range(0, len(a) - hop, hop)])
    if not len(e) or e.max() <= 0:
        return None
    quiet = e < e.max() * 0.06
    total = len(e) * 0.02
    best = (0.0, None)
    run = 0
    for i, q in enumerate(quiet):
        if q:
            run += 1
        else:
            if run:
                start = (i - run) * 0.02
                mid = start + run * 0.01
                if lo * total <= mid <= hi * total and run * 0.02 > best[0]:
                    best = (run * 0.02, mid)
            run = 0
    return best[1]


# THE PORTRAIT SLOT (the User 2026-09-12 pm): after her teaser, Crissa's portrait
# loop swings from the centre box to a slot at the bottom-left — above
# CANCEL, clear of the wizard's text block — and stays there through
# Lila's part ("so she is in the room with Lila"), fading at the wrap as
# the BAU wall brings her up at her desk.
CINEMA_SLOT_W = 0.12          # fraction of W; height follows the portrait's aspect
CINEMA_SLOT_BOTTOM = 0.74     # the slot's bottom edge, fraction of H (above the text block)


def cinema_slot_rect(W: int, H: int, aspect: float = 480 / 488) -> tuple[int, int, int, int]:
    u = W / 88.0
    w = CINEMA_SLOT_W * W
    h = w / aspect
    x0 = 0.378 * u
    y0 = CINEMA_SLOT_BOTTOM * H - h
    return int(round(x0)), int(round(y0)), max(2, int(round(w))), max(2, int(round(h)))


def rect_between(a: tuple[int, int, int, int], b: tuple[int, int, int, int], k: float) -> tuple[int, int, int, int]:
    k = max(0.0, min(1.0, float(k)))
    return tuple(int(round(a[i] + (b[i] - a[i]) * k)) for i in range(4))  # type: ignore[return-value]


def cinema_frame_rect(frame_rgb: Image.Image, W: int, H: int, rect: tuple[int, int, int, int],
                      ground_alpha: int = 255, frame_alpha: int = 255) -> Image.Image:
    """The layer for one frame fitted into an arbitrary rect (aspect kept)."""
    x0, y0, w, h = rect
    im = Image.new("RGBA", (W, H), CINEMA_GROUND + (int(ground_alpha),))
    if w < 4 or h < 4:
        return im
    fw, fh = frame_rgb.size
    s = min(w / fw, h / fh)
    tw, th = max(1, int(fw * s)), max(1, int(fh * s))
    fit = frame_rgb.resize((tw, th), Image.Resampling.BILINEAR).convert("RGBA")
    if frame_alpha < 255:
        fit.putalpha(fit.getchannel("A").point(lambda a, k=frame_alpha / 255.0: int(a * k)))
    im.alpha_composite(fit, (x0 + (w - tw) // 2, y0 + (h - th) // 2))
    return im
