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
import math
import time
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    import numpy as np
except Exception:  # numpy comes with the wall's own env; fall back to slow path without it
    np = None

def _her_name() -> str:
    try:
        import crissa_home
        return crissa_home.her_name()
    except (ImportError, AttributeError):
        return "Crissa"


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
    def __init__(self, W: int, H: int):
        self.W, self.H = W, H
        self.u = W / 88.0                       # body font-size
        self.page_left = 0.378 * self.u
        self.pip_w = 0.16 * W
        self.pip_h = self.pip_w * 9 / 16
        self.ft_w = 0.095 * W
        self.ft_h = self.ft_w * 6 / 4
        self.pip_edge = 0.016 * W
        self.rail_w = 0.575 * W
        # ascii grid
        fs = self.u * 0.44
        self.ascii_fs = fs
        self.cell_w = fs * 0.6
        self.cell_h = fs * 0.98
        self.COLS = max(24, math.ceil(W / self.cell_w))
        self.ROWS = max(12, math.ceil(H / self.cell_h))


# ---- ascii layer -------------------------------------------------------------
@lru_cache(maxsize=4)
def _glyph_atlas(fs: float, cw: int, ch: int):
    """One tile per RAMP character, white on black, exactly cw x ch, with the
    page's text-shadow glow (0 0 14px at .30) baked into the tile."""
    from PIL import ImageChops
    f = font("mono", fs)
    tiles = []
    for chdr in RAMP:
        t = Image.new("L", (cw, ch), 0)
        d = ImageDraw.Draw(t)
        try:
            bx0, by0, bx1, by1 = f.getbbox(chdr)
            gw = bx1 - bx0
        except Exception:
            bx0, gw = 0, cw
        d.text(((cw - gw) / 2 - bx0, -1), chdr, font=f, fill=255)
        glow = t.filter(ImageFilter.GaussianBlur(2.5)).point(lambda p: int(p * 0.30))
        tiles.append(ImageChops.add(t, glow))
    if np is not None:
        return np.stack([np.asarray(t, dtype=np.uint8) for t in tiles])
    return tiles


def _luma_grid(src: Image.Image, g: Geom, crissa_own: bool, hole_zoom: float | None = None):
    """Return a float grid (ROWS x COLS) in 0..1 following renderAscii.

    crissa_own      : ZFRAME crop filling the grid, bokeh + face metering (crissaOwnFrame)
    hole_zoom     : fovSourceFromBounds() for a FULLSCREEN window: the clip is a
                    plane pinned to screen height, the window samples all of it,
                    z<1 widens. This is the kiosk's crissa_hole path (screen.crissa_hole
                    true, screen.fov_zoom_crissa). Letterboxed like the hybrid branch.
    neither       : CONTAIN fit of the whole JPEG (camera / Voice-Only)
    """
    COLS, ROWS = g.COLS, g.ROWS
    iw, ih = src.size
    if crissa_own and hole_zoom is None:
        sx, sy = ZFRAME["cx"] * iw, ZFRAME["cy"] * ih
        sw, sh = ZFRAME["cw"] * iw, ZFRAME["ch"] * ih
        crop = src.crop((int(sx), int(sy), int(sx + sw), int(sy + sh)))
        blurred = crop.filter(ImageFilter.GaussianBlur(2))
        crop = Image.composite(crop, blurred, _focus_mask(crop.size))
        small = crop.resize((COLS, ROWS), Image.BILINEAR).convert("L")
        dx, dy, dw, dh = 0, 0, COLS, ROWS
    elif hole_zoom is not None:
        crissa_own = False
        plane_h = float(g.H)
        plane_w = plane_h * (iw / max(1, ih))
        plane_x = (g.W - plane_w) / 2
        sx = (0 - plane_x) / plane_w * iw
        sy = 0.0
        cw = g.W / plane_w * iw
        ch = float(ih)
        z = hole_zoom if hole_zoom > 0 else 1.0
        if z != 1:
            cx, cy = sx + cw / 2, sy + ch / 2
            cw, ch = cw / z, ch / z
            sx, sy = cx - cw / 2, cy - ch / 2
        src_x, src_y = max(0.0, sx), max(0.0, sy)
        src_r, src_b = min(iw, sx + cw), min(ih, sy + ch)
        dx = int((src_x - sx) / cw * COLS)
        dy = int((src_y - sy) / ch * ROWS)
        dw = max(1, int((src_r - src_x) / cw * COLS))
        dh = max(1, int((src_b - src_y) / ch * ROWS))
        small = Image.new("L", (COLS, ROWS), 0)
        part = src.crop((int(src_x), int(src_y), int(src_r), int(src_b))).resize((dw, dh), Image.BILINEAR).convert("L")
        small.paste(part, (dx, dy))
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
        small.paste(src.resize((dw, dh), Image.BILINEAR).convert("L"), (dx, dy))

    if np is None:
        return None, (dx, dy, dw, dh)
    lv = np.asarray(small, dtype=np.float32) / 255.0

    r0, r1, c0, c1 = dy, dy + dh, dx, dx + dw
    if crissa_own:
        r0 = max(r0, int(dy + dh * (ZFOCUS["y"] - 0.22)))
        r1 = min(r1, int(math.ceil(dy + dh * (ZFOCUS["y"] + 0.22))))
        c0 = max(c0, int(dx + dw * (ZFOCUS["x"] - 0.16)))
        c1 = min(c1, int(math.ceil(dx + dw * (ZFOCUS["x"] + 0.16))))
    box = lv[r0:r1, c0:c1]
    if box.size == 0:
        box = lv
    if crissa_own:
        lo, hi = np.percentile(box, 2), np.percentile(box, 98)
        if hi - lo < 0.04:
            hi = lo + 0.04
    else:
        lo, hi = float(box.min()), float(box.max())
        if hi - lo <= 0.04:
            hi = lo + 0.04

    v = (lv - lo) / (hi - lo)
    FLOOR = 0.42
    v = np.where(v <= FLOOR, 0.0, (v - FLOOR) / (1 - FLOOR))
    v = np.clip(v, 0, 1) ** 0.80
    if crissa_own and ZFADE > 0:
        edge = ZFOCUS["x"] - 0.10
        xr = np.arange(COLS, dtype=np.float32) / max(1, COLS - 1)
        kf = np.clip((edge - xr) / (edge if edge > 0 else 1), 0, 1)
        fade = 1 - ZFADE * kf ** 1.25
        v = v * np.where(xr < edge, fade, 1.0)[None, :]
    return np.clip(v, 0, 1), (dx, dy, dw, dh)


@lru_cache(maxsize=2)
def _focus_mask(size):
    mask = Image.new("L", size, 0)
    md = ImageDraw.Draw(mask)
    cx, cy = ZFOCUS["x"] * size[0], ZFOCUS["y"] * size[1]
    rx, ry = ZFOCUS["r"] * size[0] * 1.15, ZFOCUS["r"] * size[1] * 1.85
    md.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    return mask


def ascii_mask(src: Image.Image | None, g: Geom, crissa_own: bool, t: float, hole_zoom: float | None = None) -> Image.Image:
    """The #ascii <pre> as an L coverage image (0..255) at wall size."""
    COLS, ROWS = g.COLS, g.ROWS
    cw, ch = max(1, int(round(g.cell_w))), max(1, int(round(g.cell_h)))
    if np is None:
        return Image.new("L", (g.W, g.H), 0)
    v = None
    if src is not None:
        v, _ = _luma_grid(src, g, crissa_own, hole_zoom)
    if v is None:
        x = np.arange(COLS)[None, :] / max(1, COLS - 1)
        y = np.arange(ROWS)[:, None] / max(1, ROWS - 1)
        v = 0.12 + 0.07 * np.sin(x * 3.1 + t * 0.13) * np.cos(y * 2.3 - t * 0.09)
    idx = np.rint(np.clip(v, 0, 1) * (len(RAMP) - 1)).astype(np.int64)
    atlas = _glyph_atlas(g.ascii_fs, cw, ch)          # (10, ch, cw)
    sheet = atlas[idx].transpose(0, 2, 1, 3).reshape(ROWS * ch, COLS * cw)
    return Image.fromarray(np.ascontiguousarray(sheet), "L").crop((0, 0, g.W, g.H))


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


@lru_cache(maxsize=4)
def _ember_luts(opacity: float = ASCII_OPACITY):
    luts = []
    for c in range(3):
        lo, hi = GROUND[c], EMBER[c]
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


def _chip(d, x, y, label, f, kind="", pad_x=None, pad_y=None):
    """.chip — uppercase, letter-spaced, 1px border, ground-2 fill. Returns width."""
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
def dial(house: dict, presence: dict, size: int) -> Image.Image:
    """The oracle clock, drawn from the SVG the page uses (viewBox 260)."""
    S = size / 260.0
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    sc = ((house.get("room_event") or {}).get("seat_clock")) or {}
    legal = sc.get("legal_sit") or [12, 1, 2, 3, 8, 10, 11]
    wall = sc.get("wall") or sc.get("wall_stations") or [4, 5, 6, 7]
    blank = sc.get("blanked_sit") or [9]
    door_h = int(sc.get("door_hour") or 12) % 12 or 12

    def P(h, r):
        x, y = _pos(h, r)
        return x * S, y * S

    def circle(cx, cy, r, fill=None, outline=None, width=1):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline, width=max(1, int(round(width))))

    c = 130 * S
    circle(c, c, 96 * S, outline=HAIR, width=S)
    circle(c, c, 72 * S, outline=(0x1B, 0x20, 0x2B), width=S)
    fl = font("mono", 6.5 * S)
    fd = font("mono", 7.5 * S)
    for h in range(1, 13):
        is_wall, is_blank, is_legal = h in wall, h in blank, h in legal
        a = P(h, 96)
        b = P(h, 86 if is_wall else 89)
        col = HAIR if is_wall else ((0x1B, 0x20, 0x2B) if is_blank else INK3)
        d.line([a, b], fill=col, width=max(1, int(round((3 if is_wall else 1) * S))))
        lx, ly = P(h, 110)
        label = "DOOR" if h == door_h else str(h)
        f = fd if h == door_h else fl
        fill = EMBER if h == door_h else (INK3 if is_legal else HAIR)
        tw = d.textlength(label, font=f)
        d.text((lx - tw / 2, ly + 2.6 * S - f.size * 0.8), label, font=f, fill=fill)
    # door halo (soft) at the door hour
    hx, hy = P(door_h, 96)
    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse((hx - 17 * S, hy - 17 * S, hx + 17 * S, hy + 17 * S), fill=EMBER + (70,))
    halo = halo.filter(ImageFilter.GaussianBlur(6 * S))
    im.alpha_composite(halo)
    d = ImageDraw.Draw(im)
    # centre: auth pip, core, ear ring, CRISSA
    authed, _ = who_auth(house, presence)
    circle(c, c, 4.6 * S, fill=SAGE if authed else HAIR)
    circle(c, c, 3.2 * S, fill=EMBER)
    circle(c, c, 10 * S, outline=(0x8C, 0x6A, 0x43), width=S)
    fz = font("mono", 7 * S)
    nm = _her_name().upper()                     # the house's own name for her there (the Crissa merge, 2026-10-11)
    tw = sum(d.textlength(ch_, font=fz) + 2.4 * S for ch_ in nm) - 2.4 * S
    _text(d, (c - tw / 2, 112 * S - fz.size * 0.8), nm, fz, INK3, 2.4 / 7)
    # sound lobe: 36 lamps at the sound azimuth (mic frame) when speech
    snd = house.get("sound") or {}
    if snd.get("speech") and snd.get("az_deg") is not None:
        az = float(snd["az_deg"])
        conf = float(snd.get("conf") or 0.3)
        for i in range(36):
            ang = i * 10.0
            dd = abs((ang - az + 180) % 360 - 180)
            k = math.exp(-(dd * dd) / (2 * 10.0 * 10.0)) * conf
            if k < 0.03:
                continue
            a = (ang - 90) * math.pi / 180
            lx, ly = c + math.cos(a) * 66 * S, c + math.sin(a) * 66 * S
            circle(lx, ly, 2.2 * S, fill=EMBER + (int(255 * k),))
    return im


# ---- mode / secure ------------------------------------------------------------
MOOD = {
    "away": ("AWAY", INK3),
    "speaking": ("SPEAKING", EMBER2),
    "alert": ("ALERT", RUST),
    "attending": ("LISTENING", SAGE),
    "paused": ("PAUSED", EMBER),
}


def who_auth(house: dict, presence: dict) -> tuple[bool, bool]:
    """AUTH / Secure from presence SoT. Sage lamp holds Secure, not a second AUTH."""
    u = presence.get("user") if isinstance(presence.get("user"), dict) else {}
    auth = bool(u.get("auth") or presence.get("user_auth"))
    lamp = str(presence.get("visit_lamp") or house.get("visit_lamp") or "")
    secure = bool(u.get("secure")) or lamp.strip().lower() == "sage"
    return auth, secure


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
                      hole_zoom: float | None = None) -> Image.Image:
    """Ground + #ascii (.62 ember, chan-live) + .vignette, folded into one coverage map:
    out = GROUND + (EMBER-GROUND) * opacity * ascii * (1 - vignette). Per-frame layer."""
    from PIL import ImageChops
    cov = ImageChops.multiply(ascii_mask(src, g, crissa_own, t, hole_zoom), _vignette_inv(g.W, g.H))
    r, gg, b = _ember_luts()
    return Image.merge("RGB", (cov.point(r), cov.point(gg), cov.point(b)))


def render_ft(crissa: Image.Image, g: Geom) -> tuple[Image.Image, tuple[int, int]]:
    """The FaceTime pop: FT_SRC crop of the 736x400 clip at 4:6, bottom-right.
    Returns (RGBA image with shadow margin, (x, y) to paste it at)."""
    u = g.u
    fx, fy, fw, fh = FT_SRC["x"], FT_SRC["y"], FT_SRC["w"], FT_SRC["h"]
    crop = crissa.crop((fx, fy, min(crissa.width, fx + fw), min(crissa.height, fy + fh)))
    ftw, fth = int(g.ft_w), int(g.ft_h)
    crop = _ft_filter(crop.resize((ftw, fth), Image.BILINEAR)).convert("RGBA")
    right = g.pip_edge + (g.pip_w - g.ft_w) / 2
    bx, by = int(g.W - right - ftw), int(g.H - g.pip_edge - fth)
    M = 60
    out = _ft_shadow(ftw, fth, M, int(0.45 * u)).copy()
    _rounded_paste(out, crop, (M, M), int(0.45 * u))
    ImageDraw.Draw(out).rounded_rectangle((M, M, M + ftw - 1, M + fth - 1), radius=int(0.45 * u), outline=(0x4E, 0x3E, 0x2B))
    return out, (bx - M, by - M)


def _ft_filter(im: Image.Image) -> Image.Image:
    """#ftCan: sepia(.80) saturate(.72) hue-rotate(-14deg) brightness(.86) contrast(1.04)."""
    from PIL import ImageEnhance
    a = 0.80
    m = (0.393 + 0.607 * (1 - a), 0.769 - 0.769 * (1 - a), 0.189 - 0.189 * (1 - a), 0,
         0.349 - 0.349 * (1 - a), 0.686 + 0.314 * (1 - a), 0.168 - 0.168 * (1 - a), 0,
         0.272 - 0.272 * (1 - a), 0.534 - 0.534 * (1 - a), 0.131 + 0.869 * (1 - a), 0)
    out = im.convert("RGB").convert("RGB", m)
    out = ImageEnhance.Color(out).enhance(0.72)
    out = ImageEnhance.Brightness(out).enhance(0.86)
    return ImageEnhance.Contrast(out).enhance(1.04)


@lru_cache(maxsize=2)
def _ft_shadow(ftw, fth, M, radius):
    sh = Image.new("RGBA", (ftw + 2 * M, fth + 2 * M), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((M, M, M + ftw, M + fth), radius=radius, fill=(0, 0, 0, 168))
    return sh.filter(ImageFilter.GaussianBlur(23))


def render_hud(house: dict, presence: dict, cam: Image.Image | None, g: Geom,
               t: float, crissa_her_live: bool, stale: bool = False) -> Image.Image:
    """Everything that reads /v1/house: band, vitals, roll, dial, camera PiP.
    Transparent RGBA; refresh every second (the clock) or when house changes."""
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
    chip_w = sum(d.textlength(c_, font=f_mode) + f_mode.size * 0.2 for c_ in label) - f_mode.size * 0.2 \
        + f_mode.size * (0.7 * 2 + 0.42 + 0.5)
    chip_h = f_mode.size * (1.2 + 0.7)
    cx1 = W - 2.4 * u
    cx0 = cx1 - chip_w
    border = {"attending": (0x45, 0x63, 0x4F), "paused": EMBER_DEEP}.get(mk, EMBER_DEEP)
    tint = {"attending": (0x1A, 0x22, 0x22), "paused": (0x18, 0x1A, 0x1E)}.get(mk, GROUND2)
    d.rounded_rectangle((cx0, band_top, cx1, band_top + chip_h), radius=2, fill=tint, outline=border)
    dr = f_mode.size * 0.21
    d.ellipse((cx0 + f_mode.size * 0.7, band_top + chip_h / 2 - dr, cx0 + f_mode.size * 0.7 + 2 * dr, band_top + chip_h / 2 + dr), fill=dot)
    _text(d, (cx0 + f_mode.size * (0.7 + 0.42 + 0.5), band_top + chip_h / 2 - f_mode.size * 0.6), label, f_mode, dot, 0.2)
    # Secure lamp (visitDot): a bare dot under the chip (modewrap is a column)
    lamp_c = str(house.get("visit_lamp") or lamp.get("color") or "amber").lower()
    dot_c = SAGE if lamp_c == "sage" else (RUST if lamp_c == "rust" else EMBER)
    vr = f_mode.size * 0.21
    vx, vy = (cx0 + cx1) / 2, band_top + chip_h + 0.28 * u + f_mode.size * 0.35 + vr
    d.ellipse((vx - vr, vy - vr, vx + vr, vy + vr), fill=dot_c)
    lx = cx0
    HOTSPOTS["mode"] = (cx0, band_top, cx1, band_top + chip_h)
    HOTSPOTS["secure"] = (cx0, band_top + chip_h, cx1, vy + vr * 3)

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

    def v_spk(x, yy):
        spk = (house.get("speaker") or (presence.get("speaker") or {}))
        who = ((house.get("auth") or {}).get("speaker")) or "None"
        d.text((x, yy), str(who), font=f_v, fill=INK)
        xx = x + max(d.textlength(str(who), font=f_v), 4 * f_v.size) + 1.0 * u
        beam = spk.get("beam")
        beam_s = f"{float(beam):.1f}" if isinstance(beam, (int, float)) else "—"
        parts = [("beam ", INK2), (beam_s, INK), (" · cam ", INK2), (f"{int((spk.get('camera') or 0) * 100):02d}", EMBER),
                 (" · voice ", INK2), (f"{int((spk.get('voice') or 0) * 100):02d}", EMBER),
                 (" · stack ", INK2), (f"{int((spk.get('stack') or 0) * 100):02d}", EMBER)]
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
        gtxt = str(gpu.get("glass") or gpu.get("mode") or "—")
        _text(d, (xx + 2.4 * u * 0.82, yy), gtxt, f_g, INK2, 0.08)
        xx += 2.4 * u * 0.82 + d.textlength(gtxt, font=f_g) + 1.1 * u
        authed, _ = who_auth(house, presence)
        _text(d, (xx, yy), "User" if authed else "Guest", f_g, SAGE if authed else INK2, 0.08)
        return f_chip.size * 1.9
    row("Services", v_svc)

    def v_logs(x, yy):
        xx = x
        for name, on in (("Board", False), ("ROOM", True), ("Tools", False)):
            xx += _chip(d, xx, yy - f_chip.size * 0.2, name, f_chip, "on" if on else "") + 0.45 * u
        return f_chip.size * 1.9
    row("Logs", v_logs)
    # #vitalsTog — the fold arrow under the rows
    f_tog = font("mono", 0.62 * u)
    d.text((x0, y - rows_gap + 0.1 * u), "\u25b2", font=f_tog, fill=(0x9A, 0x98, 0x92))
    y += f_tog.size * 1.2

    # the roll — newest at the bottom of the left column, dissolving upward
    chat = [c_ for c_ in (house.get("chat") or [])
            if c_.get("text") and float(c_.get("ts") or 0) >= ROLL_SINCE]
    f_who = font("mono", 0.85 * u)
    f_said = font("mono", 1.0 * u)
    roll_bottom = H - (g.pip_edge + g.pip_h + 0.8 * u)
    roll_left = x0
    roll_right = g.rail_w - 2.4 * u
    who_col = 5.4 * u + 1.0 * u
    yy = roll_bottom
    top_limit = y + 0.5 * u
    for item in reversed(chat[-12:]):
        text = str(item.get("text") or "").strip()
        who = str(item.get("who") or "").strip()
        maxw = roll_right - (roll_left + who_col)
        words, line, lines = text.split(), "", []
        for w_ in words:
            cand = (line + " " + w_).strip()
            if d.textlength(cand, font=f_said) > maxw and line:
                lines.append(line)
                line = w_
            else:
                line = cand
        if line:
            lines.append(line)
        block_h = len(lines) * f_said.size * 1.32 + 0.5 * u
        yy -= block_h
        if yy < top_limit:
            break
        frac = (yy - top_limit) / max(1, roll_bottom - top_limit)
        alpha = 1.0 if frac > 0.58 else max(0.0, frac / 0.58) ** 0.9
        whocol = EMBER if who not in ("stranger", "unknown", "") else INK3
        if who == "oracle":
            whocol = INK2
        a8 = int(255 * alpha)
        _text(d, (roll_left, yy + (f_said.size - f_who.size)), who.upper(), f_who, whocol + (a8,), 0.16)
        ly = yy
        for ln in lines:
            d.text((roll_left + who_col, ly), ln, font=f_said, fill=INK + (a8,))
            ly += f_said.size * 1.32

    # the oracle dial (bottom-left, stacked above the camera PiP)
    dial_size = int(g.pip_w)
    dial_bottom = H - (g.pip_edge + g.pip_h + 0.45 * u)
    disc = _dial_disc(dial_size)
    dx_ = int(x0 + dial_size / 2 - disc.width / 2)
    dy_ = int(dial_bottom - dial_size / 2 - disc.height / 2)
    wall.alpha_composite(disc, (max(0, dx_), max(0, dy_)))
    wall.alpha_composite(dial(house, presence, dial_size), (int(x0), int(dial_bottom - dial_size)))
    HOTSPOTS["dial"] = (x0, dial_bottom - dial_size, x0 + dial_size, dial_bottom)
    d = ImageDraw.Draw(wall)

    # camera PiP (bottom-left) — dimmed to .32 while Crissa is live
    px, py = int(x0), int(H - g.pip_edge - g.pip_h)
    pw, ph = int(g.pip_w), int(g.pip_h)
    pip = Image.new("RGBA", (pw, ph), GROUND + (72,))
    if cam is not None:
        thumb = cam.resize((pw, ph), Image.BILINEAR).convert("RGBA")
        thumb.putalpha(int(255 * (0.32 if crissa_her_live else 1.0)))
        pip.alpha_composite(thumb)
    wall.alpha_composite(pip, (px, py))
    d = ImageDraw.Draw(wall)
    d.rectangle((px, py, px + pw - 1, py + ph - 1), outline=INK3)
    f_pl = font("mono", 0.6 * u)
    _text(d, (px + 0.5 * u, py + ph - f_pl.size * 1.9), "CAMERA", f_pl, INK3, 0.2)
    HOTSPOTS["camera"] = (px, py, px + pw, py + ph)
    right = g.pip_edge + (g.pip_w - g.ft_w) / 2
    HOTSPOTS["ft"] = (W - right - g.ft_w, H - g.pip_edge - g.ft_h, W - right, H - g.pip_edge)

    if stale:
        f_s = font("mono", 0.6 * u)
        _text(d, (W - 2.4 * u - d.textlength("FEED STALE", font=f_s) - 0.6 * u * 3, H - 2.6 * u - f_s.size), "FEED STALE", f_s, RUST, 0.2)
    return wall


@lru_cache(maxsize=2)
def _dial_disc(dial_size):
    disc = Image.new("RGBA", (int(dial_size * 1.7), int(dial_size * 1.7)), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((0, 0, disc.width - 1, disc.height - 1), fill=GROUND + (90,))
    return disc.filter(ImageFilter.GaussianBlur(dial_size * 0.18))


# click targets, in wall pixels, filled in by render_hud (measured, not guessed)
HOTSPOTS: dict = {}
# speech before this load does not enter the roll (the page's rollSince)
ROLL_SINCE = time.time()


def _wrap_balanced(d, text, f, maxw):
    """CSS text-wrap:balance — same line count as greedy at maxw, narrowest width."""
    def greedy(width):
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
           t: float | None = None, stale: bool = False) -> Image.Image:
    """One complete frame (used by --preview and the smoke test)."""
    t = time.time() if t is None else t
    g = Geom(W, H)
    crissa, cam = _open(frame), _open(camera)
    src, crissa_own = (crissa, True) if crissa is not None else (cam, False)
    hz = hole_zoom_of(house) if crissa is not None else None
    wall = render_background(src, g, crissa_own, t, hz).convert("RGBA")
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
