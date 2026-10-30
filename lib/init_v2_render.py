"""Init v2's glass (the User 2026-10-04): one RGBA layer per step, drawn with the wall's own type and colours.

A PACK'S SHORT WALK (the User 2026-10-10, lib/crissa_init): Connect, the machine card, Voice ID (driver "walker": the
walk drives the Register coach; this layer draws the name card, a refusal and its keys, and steps aside while the
register layer is the glass), face photos (`face`), done. The retired walk's pack-only plates (the gold-Z splash, the
file of numbers) are gone; the house's own Init v2 plates (room, overlay, walk-in) stay for the house.

render() fills the wall's INIT_HITS with `init2_*` names; the wall turns a click on one into an action
(lib/house_wall_ascii.py `_init2_click`). The photo's rectangle is kept in PHOTO so a tap becomes x, y on the shot.
The opener and the walk-off are the cinema worker's (the film and the portrait slot); this layer adds only CANCEL.
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

from PIL import Image, ImageDraw

import house_wall_ascii_render as R

PHOTO: dict = {"rect": None}
_IMG_CACHE: dict = {"path": "", "mtime": 0.0, "im": None}
FADE_S = 1.0
HOLD_S = 1.2           # the still covers the cut from Lila's plate to her office before it fades


def _photo(path: str):
    try:
        p = Path(path)
        m = p.stat().st_mtime
    except (OSError, TypeError):
        return None
    if _IMG_CACHE["path"] != path or _IMG_CACHE["mtime"] != m:
        try:
            _IMG_CACHE.update(path=path, mtime=m, im=Image.open(p).convert("RGB"))
        except OSError:
            return None
    return _IMG_CACHE["im"]


def _key(d, hits, name, x, y, label, u, *, lit=True, ptr=None, wide=None):
    """A key in the wall's style; returns its width."""
    f = R.font("mono", 0.66 * u)
    tw = R._text_w(d, label, f, 0.14)
    w_ = max(wide or 0, tw + 1.8 * u)
    h_ = f.size + 0.9 * u
    box = (x, y, x + w_, y + h_)
    hov = R._inside(ptr, box) if ptr else False
    out = (R.EMBER if (lit or hov) else R.INK3) + (255,)
    d.rectangle(box, fill=R.GROUND2 + (230,), outline=out)
    R._text(d, (x + (w_ - tw) / 2, y + 0.45 * u), label, f, (R.INK if lit else R.INK3) + (255,), 0.14)
    if lit:
        hits[name] = box
    return w_


def _panel(d, W, H, u, w_u=34.0, h_u=15.0):
    pw, ph = w_u * u, h_u * u
    x0, y0 = (W - pw) / 2, (H - ph) / 2
    d.rectangle((x0, y0, x0 + pw, y0 + ph), fill=R.GROUND + (238,), outline=R.EMBER_DEEP + (255,))
    return x0, y0, pw, ph


def _wrap(d, text, f, max_w):
    words, lines, cur = (text or "").split(), [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if cur and d.textlength(t, font=f) > max_w:
            lines.append(cur)
            cur = wd
        else:
            cur = t
    return lines + ([cur] if cur else [])


def _lines(d, x, y, text, f, col, max_w, lead=1.35):
    for ln in _wrap(d, text, f, max_w):
        R._text(d, (x, y), ln, f, col, 0.0)          # the wrap measured without letter spacing: draw without it
        y += f.size * lead
    return y


def _cancel(d, hits, u, H, left, name="init_cancel"):
    """CANCEL at the bottom left. A pack's walk names it init2_cancel_ask: the first press asks (cancel_plate), so one
    stray click never resets the walk (the User 2026-10-11, .101 JJ); the house's keeps its own key."""
    f_k = R.font("mono", 0.66 * u)
    lab = "CANCEL"
    kw = R._text_w(d, lab, f_k, 0.14) + 1.6 * u
    kh = f_k.size + 0.9 * u
    kx, ky = left, H - 2.6 * u - kh
    d.rectangle((kx, ky, kx + kw, ky + kh), fill=R.GROUND + (120,), outline=R.EMBER_DEEP + (255,))
    R._text(d, (kx + 0.8 * u, ky + 0.45 * u), lab, f_k, R.INK2 + (255,), 0.14)
    hits[name] = (kx, ky, kx + kw, ky + kh)


def _pack_walk(snap: dict) -> bool:
    """A pack's short walk (lib/crissa_init), never the house's own Init."""
    return str(snap.get("walker") or "") == "crissa"


def _corner_tile_on() -> bool:
    return os.environ.get("CRISSA_SETUP_CORNER_TILE", "1").strip().lower() not in ("0", "off", "false", "no")


def _walkin_clear() -> bool:
    return os.environ.get("CRISSA_WALKIN_CLEAR", "1").strip().lower() not in ("0", "off", "false", "no")


def setup_tile_one_exit_on() -> bool:
    """Round 40 N21: Setup's corner tile is the one exit, one click. Kill CRISSA_SETUP_TILE_ONE_EXIT=0 (round 38's ask
    first, and the CANCEL key beside it)."""
    return os.environ.get("CRISSA_SETUP_TILE_ONE_EXIT", "1").strip().lower() not in ("0", "off", "false", "no")


def exit_says_cancel_on() -> bool:
    """Round 41 (.101 round 40 D8, the User: "name the Go to Wall button Cancel ... Just Cancel would be simplest"): the one
    exit of an IDs run and of the corner tile reads CANCEL (it cancels and returns to the Wall). Kill
    CRISSA_EXIT_SAYS_CANCEL=0 (round 40: BACK TO THE WALL)."""
    return os.environ.get("CRISSA_EXIT_SAYS_CANCEL", "1").strip().lower() not in ("0", "off", "false", "no")


def _tile_case(snap: dict, kind: str) -> bool:
    """Is the corner tile the plate now (her window not in front, mid-process)? The same test the tile branch makes."""
    br = snap.get("browser") if isinstance(snap.get("browser"), dict) else {}
    if not (_pack_walk(snap) and _corner_tile_on() and br.get("led") and br.get("own")
            and (kind in ("connect", "machine", "register", "face", "room", "hours") or snap.get("ids"))):
        return False
    win = br.get("window") if isinstance(br.get("window"), dict) else {}
    shown = bool(win.get("up") and win.get("setup") and win.get("visible", True))
    ending = snap.get("ending") if isinstance(snap.get("ending"), dict) else {}
    return not (_walkin_clear() and (shown or ending.get("on")))


def setup_in_her_window(snap: dict) -> bool:
    """A pack's walk led in her own window (lib/crissa_init.browser_led own): the desk Wall then shows only that, and no
    second Setup plate - neither a step's plate nor the Voice ID sentence (the Wall's register layer asks this too)."""
    br = snap.get("browser") if isinstance(snap.get("browser"), dict) else {}
    return (_pack_walk(snap) and str(snap.get("status") or "") == "running" and bool(br.get("led"))
            and bool(br.get("own")))


def cancel_plate(d, hits, W, H, u, ptr=None):
    """CANCEL SETUP? asked on the plate itself: YES, CANCEL SETUP (the walk's abort) or BACK."""
    f_t, f_b = R.font("serif", 1.5 * u), R.font("light", 0.9 * u)
    x0, y0, pw, ph = _panel(d, W, H, u, 32.0, 10.0)
    x, y = x0 + 1.6 * u, y0 + 1.4 * u
    y = _lines(d, x, y, "Cancel Setup?", f_t, R.INK + (255,), pw - 3.2 * u)
    _lines(d, x, y + 0.4 * u, "%s runs as she is. Nothing Setup saved is kept; it runs again at the next start, or from RUN SETUP."
           % R.crissa_home.her_name(), f_b, R.INK2 + (255,), pw - 3.2 * u)
    ky = y0 + ph - 2.6 * u
    kx = x + _key(d, hits, "init_cancel", x, ky, "YES, CANCEL SETUP", u, ptr=ptr) + 0.8 * u
    _key(d, hits, "init2_cancel_back", kx, ky, "BACK", u, ptr=ptr)


def _dots(d, snap, u, left):
    total, on = int(snap.get("total") or 0), int(snap.get("index") or 0) + 1
    r_ = 0.24 * u
    for i in range(total):
        cx, cy = left + r_ + i * (r_ * 2 + 0.36 * u), 2.75 * u
        d.ellipse((cx - r_, cy - r_, cx + r_, cy + r_), fill=(R.EMBER if i < on else R.INK3) + (255,))


def _still(im, snap, W, H, alpha):
    """The room shot over the whole wall (cover), at alpha 0..255."""
    room = ((snap.get("plate") or {}).get("room") or {})
    ph = _photo(str(room.get("image") or ""))
    if ph is None or alpha <= 0:
        PHOTO["rect"] = None
        return
    k = max(W / ph.width, H / ph.height)
    nw, nh = int(ph.width * k), int(ph.height * k)
    x0, y0 = (W - nw) // 2, (H - nh) // 2
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layer.paste(ph.resize((nw, nh)), (x0, y0))
    if alpha < 255:
        layer.putalpha(int(alpha))
    im.alpha_composite(layer)
    PHOTO["rect"] = (x0, y0, x0 + nw, y0 + nh)


def photo_point(x: float, y: float):
    """A click on the glass as x, y on the room shot (0..1), or None off the picture."""
    r = PHOTO.get("rect")
    if not r:
        return None
    x0, y0, x1, y1 = r
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return None
    return round((x - x0) / (x1 - x0), 4), round((y - y0) / (y1 - y0), 4)


def _clock(d, hits, cx, cy, r, *, marks, door_hour=None, pick_hours=False, initials="YOU", u=10.0,
           prefix="init2_hour_"):
    """The labelled clock over the room: the ring, twelve hours, YOU at the seat, CRISSA at the centre, DOOR. `prefix`
    names the hour keys (the house's Init v2: init2_hour_<h>; a pack's Your room: init2_rmhour_<h>)."""
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=R.GROUND + (120,), outline=R.INK2 + (220,), width=max(1, int(r * 0.02)))
    f_h = R.font("mono", max(9.0, r * 0.12))
    for h in range(1, 13):
        a = (h / 12.0) * math.pi * 2.0 - math.pi / 2.0
        x, y = cx + math.cos(a) * r * 0.84, cy + math.sin(a) * r * 0.84
        col = R.EMBER if door_hour == h else R.INK2
        lab = str(h)
        tw = d.textlength(lab, font=f_h)
        R._text(d, (x - tw / 2, y - f_h.size / 2), lab, f_h, col + (255,), 0.0)
        if pick_hours:
            hb = r * 0.14
            hits["%s%d" % (prefix, h)] = (x - hb, y - hb, x + hb, y + hb)
    f_m = R.font("mono", max(8.0, r * 0.11))
    for h, (text, col) in marks.items():
        a = (float(h) / 12.0) * math.pi * 2.0 - math.pi / 2.0
        x, y = cx + math.cos(a) * r * 1.12, cy + math.sin(a) * r * 1.12
        tw = d.textlength(text, font=f_m)
        d.rectangle((x - tw / 2 - 4, y - f_m.size / 2 - 3, x + tw / 2 + 4, y + f_m.size / 2 + 3), fill=R.GROUND + (220,), outline=col + (255,))
        R._text(d, (x - tw / 2, y - f_m.size / 2), text, f_m, col + (255,), 0.0)
    zr = r * 0.07
    d.ellipse((cx - zr, cy - zr, cx + zr, cy + zr), fill=R.INK + (255,))
    nm = R.crissa_home.her_name().upper()      # the house's own name for her there (the Crissa merge, 2026-10-11)
    tw = d.textlength(nm, font=f_m)
    R._text(d, (cx - tw / 2, cy + zr * 1.6), nm, f_m, R.INK + (255,), 0.0)


ROOM_TAGS = {"seat": "YOU", "speakers": "SPEAKERS", "camera": "CAMERA", "door": "DOOR"}


def internal_tick(d, hits, cx, cy, r, it: dict, *, ptr=None):
    """THE SPEAKERS' INTERNAL TICK (round 17, the User 2026-10-13, .101: "This computer" was taken and the card showed
    nothing): under her name at the clock's centre, a checkbox labelled INTERNAL - ticked when the sound comes from this
    computer, sage once clicked (as a set mark is), ember-ringed while the speakers are asked; a tap is
    init2_rm_internal (THIS COMPUTER). Drawn only when the view sends `internal` (kill CRISSA_ROOM_INTERNAL_TICK=0)."""
    f = R.font("mono", max(8.0, r * 0.10))
    label = str(it.get("label") or "Internal").upper()
    s = f.size * 0.95
    tw = d.textlength(label, font=f)
    w_ = s + 0.5 * s + tw
    x0, y0 = cx - w_ / 2.0, cy + r * 0.34
    box = (x0 - 0.35 * s, y0 - 0.3 * s, x0 + w_ + 0.35 * s, y0 + s + 0.3 * s)
    lit, checked = bool(it.get("lit")), bool(it.get("checked"))
    hov = R._inside(ptr, box) if ptr else False
    col = R.SAGE if lit else (R.EMBER if (it.get("asking") or hov) else R.INK2)
    d.rectangle(box, fill=R.GROUND2 + (230,), outline=col + (255,), width=2 if lit else 1)
    d.rectangle((x0, y0, x0 + s, y0 + s), outline=col + (255,), width=max(1, int(s * 0.12)))
    if checked:
        d.line((x0 + s * 0.18, y0 + s * 0.55, x0 + s * 0.42, y0 + s * 0.8, x0 + s * 0.85, y0 + s * 0.2),
               fill=col + (255,), width=max(2, int(s * 0.16)))
    R._text(d, (x0 + 1.5 * s, y0 + (s - f.size) / 2.0), label, f, col + (255,), 0.0)
    hits["init2_rm_internal"] = box


def room_marks(rm: dict) -> dict:
    """{hour: (label, colour)} for the clock: every mark at its hour (the speakers only when they have one), two marks
    on one hour share its label; the mark being set is ember."""
    marks: dict = {}
    now = str(rm.get("now") or "")
    for mk in rm.get("order") or ("seat", "speakers", "camera", "door"):
        try:
            h = int((rm.get("marks") or {}).get(mk) or 0)
        except (TypeError, ValueError):
            h = 0
        if not 1 <= h <= 12:
            continue
        col = R.EMBER if mk == now else (R.SAGE if mk in (rm.get("changed") or []) else R.INK2)
        if float(h) in marks:
            text, col0 = marks[float(h)]
            marks[float(h)] = (text + "+" + ROOM_TAGS.get(mk, mk.upper()), R.EMBER if R.EMBER in (col, col0) else col0)
        else:
            marks[float(h)] = (ROOM_TAGS.get(mk, mk.upper()), col)
    return marks


def room_plate(d, hits, W, H, u, plate, ptr=None):
    """YOUR ROOM on the desk Wall (the User 2026-10-11): the clock (tap an hour: init2_rmhour_<h>) with every mark,
    one key per mark to choose which the next tap sets (init2_rm_pick_<mark>), THIS COMPUTER for the speakers, then the
    wave's YES / NO; NEXT, SAVE and SKIP (init2_rm_<verb>)."""
    rm = plate.get("room") or {}
    state = str(rm.get("state") or "")
    f_t, f_b, f_n = R.font("serif", 1.5 * u), R.font("light", 0.9 * u), R.font("mono", 0.66 * u)
    x0, y0, pw, ph = _panel(d, W, H, u, 44.0, 22.0)
    x, y = x0 + 1.6 * u, y0 + 1.4 * u
    y = _lines(d, x, y, str(plate.get("title") or "Your room"), f_t, R.INK + (255,), pw - 3.2 * u)
    if state == "marks":
        r = 6.2 * u
        cx, cy = x0 + pw - r * 1.45, y0 + ph / 2.0 + 0.6 * u
        _clock(d, hits, cx, cy, r, marks=room_marks(rm), pick_hours=True, u=u, prefix="init2_rmhour_")
        if isinstance(rm.get("internal"), dict):
            internal_tick(d, hits, cx, cy, r, rm["internal"], ptr=ptr)
        tw = pw - 3.2 * u - r * 2.7
        y = _lines(d, x, y + 0.4 * u, str(plate.get("body") or ""), f_n, R.INK2 + (255,), tw)
        y = _lines(d, x, y + 0.5 * u, str(plate.get("hint") or ""), f_b, R.EMBER + (255,), tw) + 0.4 * u
        labels = rm.get("labels") or {}
        for mk in rm.get("order") or ():
            v = (rm.get("marks") or {}).get(mk)
            val = "this computer" if (mk == "speakers" and not v) else str(v)
            w_ = _key(d, hits, "init2_rm_pick_" + mk, x, y, "%s: %s" % (str(labels.get(mk) or mk).upper(), val.upper()),
                      u, lit=True, ptr=ptr, wide=tw * 0.62)
            if mk == str(rm.get("now") or ""):
                d.rectangle((x - 0.5 * u, y, x - 0.25 * u, y + 1.5 * u), fill=R.EMBER + (255,))
            if mk == "speakers":
                _key(d, hits, "init2_rm_computer", x + w_ + 0.6 * u, y, "THIS COMPUTER", u, ptr=ptr)
            y += 2.0 * u
    elif state in ("wave", "wave_again"):
        y = _lines(d, x, y + 0.4 * u, str(plate.get("body") or ""), f_b, R.INK + (255,), pw - 3.2 * u)
        _lines(d, x, y + 0.4 * u, str(plate.get("hint") or ""), f_n, R.INK2 + (255,), pw - 3.2 * u)
    else:
        _lines(d, x, y + 0.4 * u, str(plate.get("body") or ""), f_b, R.INK2 + (255,), pw - 3.2 * u)
    ky = y0 + ph - 2.6 * u
    kx = x
    for verb, label in [(str(v_), str(l_)) for v_, l_ in (plate.get("keys") or [])]:
        kx += _key(d, hits, "init2_rm_" + verb, kx, ky, label.upper(), u, ptr=ptr) + 0.8 * u


# A WAY BACK INTO SETUP (round 38, .101 round 37: after a cancelled first boot only a restart brought Setup back): while
# the walk says Setup comes at the next start, the middle shows "Setup was cancelled" and one key that runs it now (the
# walk's restart, which a never-finished Setup takes). A pack only. Kill CRISSA_SETUP_RERUN_PLATE=0.
def _rerun_on() -> bool:
    return os.environ.get("CRISSA_SETUP_RERUN_PLATE", "1").strip().lower() not in ("0", "off", "false", "no")


def rerun_plate(g, snap: dict, hits: dict, ptr=None):
    W, H = int(g.W), int(g.H)
    u = float(getattr(g, "u", W / 88.0))
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x0, y0, pw, ph = _panel(d, W, H, u, 30.0, 9.0)
    x, y = x0 + 1.6 * u, y0 + 1.4 * u
    y = _lines(d, x, y, "Setup was cancelled", R.font("serif", 1.5 * u), R.INK + (255,), pw - 3.2 * u)
    _lines(d, x, y + 0.6 * u, "Nothing was kept. Run it whenever you are ready; it takes a few minutes.",
           R.font("light", 0.9 * u), R.INK2 + (255,), pw - 3.2 * u)
    _key(d, hits, "init2_run_setup", x, y0 + ph - 2.6 * u, "RUN SETUP", u, ptr=ptr)
    return im


def render(g, snap: dict, hits: dict, *, register_live: bool = False, ptr=None):
    hits.clear()
    if (str(snap.get("status") or "") == "aborted" and str(snap.get("next_start") or "") == "setup"
            and _pack_walk(snap) and _rerun_on()):
        PHOTO["rect"] = None
        return rerun_plate(g, snap, hits, ptr)
    if str(snap.get("status") or "") != "running":
        PHOTO["rect"] = None
        return None
    W, H = int(g.W), int(g.H)
    u = float(getattr(g, "u", W / 88.0))
    left = float(getattr(g, "page_left", 0.378 * u))
    plate = snap.get("plate") if isinstance(snap.get("plate"), dict) else {}
    kind = str(plate.get("kind") or snap.get("kind") or "")
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    now = time.time()
    since = now - float(snap.get("step_at") or now)

    # the room still: fades in over Lila and the empty portrait slot, stays through the overlay, and fades out once
    # she walks in (the conductor's film underneath has had a second to cut to her office)
    if kind in ("room_shot", "overlay") and ((plate.get("room") or {}).get("state") == "ok" or kind == "overlay"):
        _still(im, snap, W, H, 255 if kind == "overlay" else int(255 * min(1.0, since / FADE_S)))
    elif kind == "walk_in":
        # held a beat while the conductor cuts to her office underneath, then it fades and she walks in
        _still(im, snap, W, H, int(255 * max(0.0, min(1.0, 1.0 - (since - HOLD_S) / FADE_S))))
    else:
        PHOTO["rect"] = None

    if register_live and not setup_in_her_window(snap):
        return im
    f_eye = R.font("mono", 0.72 * u)
    R._text(d, (left, 1.4 * u), str(plate.get("eyebrow") or "").upper(), f_eye, R.EMBER + (255,), 0.3)
    _dots(d, snap, u, left)
    if kind not in ("walk_in", "camera_home") and not (setup_tile_one_exit_on() and _tile_case(snap, kind)):
        _cancel(d, hits, u, H, left, "init2_cancel_ask" if _pack_walk(snap) else "init_cancel")
    f_t = R.font("serif", 1.5 * u)
    f_b = R.font("light", 0.9 * u)
    f_n = R.font("mono", 0.66 * u)
    if _pack_walk(snap) and snap.get("cancel_ask"):
        cancel_plate(d, hits, W, H, u, ptr)
        return im

    br = snap.get("browser") if isinstance(snap.get("browser"), dict) else {}
    if br.get("led") and br.get("own") and (kind in ("connect", "machine", "register", "face", "room", "hours")
                                             or snap.get("ids")):
        # SETUP IS IN HER OWN WINDOW (the User 2026-10-11, .101 JJ): Crissa's own browser instance holds the walk and
        # was brought to the front; this glass shows only that, and never a second Setup plate. While her window is
        # closed it says so, and its one key opens the window again (raised); CANCEL asks first.
        name = R.crissa_home.her_name()
        win = br.get("window") if isinstance(br.get("window"), dict) else {}
        shown = bool(win.get("up") and win.get("setup") and win.get("visible", True))   # round 38: minimized = not shown
        # round 37 (.101 round 36, the User: "clear otherwise so ascii can be viewed"; "it blocks the door in the walk
        # in"): while her window is up, and over her walk-in, the middle stays clear; the plate is the way back only
        # when her window is closed. Kill CRISSA_WALKIN_CLEAR=0 (lib/crissa_init.walkin_clear_on).
        ending = snap.get("ending") if isinstance(snap.get("ending"), dict) else {}
        if _walkin_clear() and (shown or ending.get("on")):
            return im
        if _corner_tile_on():
            # round 38 (the User: "smaller more subtle but had a Return to Wall ... to indicate mid-process"): her window
            # is not in front (closed, minimized), so a small tile low on the left says so, with Open and Back to the
            # Wall; the room stays in view, never the big plate over the door
            what = "Add/Edit IDs" if snap.get("ids") else ("Voice ID" if snap.get("again") else "Setup")
            tw, th = 25.0 * u, 6.2 * u
            tx, ty = left, H - th - 6.4 * u
            d.rounded_rectangle((tx, ty, tx + tw, ty + th), radius=int(0.6 * u), fill=(16, 19, 26, 230),
                                outline=R.EMBER + (200,))
            R._text(d, (tx + 1.0 * u, ty + 0.8 * u), "%s in progress" % what, R.font("light", 0.9 * u), R.INK + (255,), 0.0)
            kx = tx + 1.0 * u
            kx += _key(d, hits, "init2_open_browser", kx, ty + th - 2.6 * u, "OPEN", u, ptr=ptr) + 0.8 * u
            # round 40 (.101 round 38 N21: cancelling Setup from the tile took 4 clicks; N11: "x = back to wall =
            # cancel, all brings you back in one step"): the tile is the one exit, and its key ends the walk in one click
            # - Setup's like an IDs run's (abort, then the Wall)
            back_key = ("init2_ids_back" if (snap.get("ids") or setup_tile_one_exit_on()) else "init2_cancel_ask")
            _key(d, hits, back_key, kx, ty + th - 2.6 * u, "CANCEL" if exit_says_cancel_on() else "BACK TO THE WALL", u,
                 ptr=ptr)
            return im
        now_ = str(plate.get("title") or plate.get("eyebrow") or "the next step")
        what = "Voice ID" if snap.get("again") else "Setup"     # Voice ID again (2026-10-11): named, not "Setup"
        x0, y0, pw, ph = _panel(d, W, H, u, 34.0, 11.0)
        x, y = x0 + 1.6 * u, y0 + 1.4 * u
        y = _lines(d, x, y, ("%s is open in %s's window" % (what, name)) if shown else ("%s's window is closed" % name),
                   f_t, R.INK + (255,), pw - 3.2 * u)
        y = _lines(d, x, y + 0.6 * u, ("Finish it there; this Wall follows each step. Now: %s." if shown else
                                       what + " waits there. Open the window again to carry on. Now: %s.") % now_,
                   f_b, R.INK2 + (255,), pw - 3.2 * u)
        _lines(d, x, y + 0.4 * u, "The %s icon in the dock brings it forward too." % name, f_n, R.INK3 + (255,),
               pw - 3.2 * u)
        _key(d, hits, "init2_open_browser", x, y0 + ph - 2.6 * u,
             ("SHOW %s'S WINDOW" if shown else "OPEN %s'S WINDOW") % name.upper(), u, ptr=ptr)
        return im
    if br.get("led") and kind in ("connect", "machine", "register", "face", "room", "hours"):
        # SETUP IS IN THE BROWSER (the User 2026-10-10, .101 finding F): the launcher opened /init there once; this
        # glass follows the steps and asks nothing itself - no keys of its own, no Tk dialogs, one key to open it again
        x0, y0, pw, ph = _panel(d, W, H, u, 34.0, 13.0)
        x, y = x0 + 1.6 * u, y0 + 1.4 * u
        R._text(d, (x, y), "%s is open in your web browser" % ("Voice ID" if snap.get("again") else "Setup"), f_t,
                R.INK + (255,), 0.0)
        y += 2.4 * u
        y = _lines(d, x, y, "Finish it there. This Wall follows each step. Now: %s." % (
            str(plate.get("title") or plate.get("eyebrow") or "the next step")), f_b, R.INK2 + (255,), pw - 3.2 * u)
        y = _lines(d, x, y + 0.6 * u, "If it is not open, open this address in a browser: %s" % br.get("url"), f_n,
                   R.INK3 + (255,), pw - 3.2 * u)
        _key(d, hits, "init2_open_browser", x, y0 + ph - 2.6 * u, "OPEN IT AGAIN", u, ptr=ptr)
        return im

    if kind == "connect":
        # tall enough for its rows on a 16:9 screen (2026-10-10: at 1920x1080 the address, model and key rows ran
        # under the keys): the body's wrapped lines are counted before the panel is drawn
        body_n = len(_wrap(d, str(plate.get("body") or ""), f_b, 34.0 * u - 3.2 * u)) if plate.get("body") else 0
        x0, y0, pw, ph = _panel(d, W, H, u, 34.0, max(15.0, 12.6 + 1.25 * body_n
                                                     + (2.0 if plate.get("last") else 0.0)))
        x, y = x0 + 1.6 * u, y0 + 1.4 * u
        R._text(d, (x, y), str(plate.get("title") or ""), f_t, R.INK + (255,), 0.0)
        y += 2.4 * u
        y = _lines(d, x, y, str(plate.get("body") or ""), f_b, R.INK2 + (255,), pw - 3.2 * u)
        cv = plate.get("connect") or {}
        y += 0.6 * u
        for lab, val in (("ADDRESS", cv.get("url") or "—"), ("MODEL", cv.get("model") or "—"),
                         ("KEY", ("•••• " + ("read where it lives" if cv.get("key_ref") else
                                     "set" if cv.get("source") == "connect" else "from her profile"))
                          if cv.get("has_key") else "not set")):
            R._text(d, (x, y), lab, f_n, R.INK3 + (255,), 0.14)
            R._text(d, (x + 6 * u, y), str(val)[:60], f_n, R.INK + (255,), 0.02)
            y += 1.3 * u
        # Detected (2026-10-06): an endpoint another program offered; USE IT stores it and tests it
        offer = next((o for o in (cv.get("offers") or []) if o.get("url") and o.get("url") != cv.get("url")), None)
        if offer:
            R._text(d, (x, y), "DETECTED", f_n, R.INK3 + (255,), 0.14)
            R._text(d, (x + 6 * u, y), (str(offer.get("name") or "") + " — " + str(offer.get("url")))[:60], f_n,
                    R.INK + (255,), 0.02)
            y += 1.3 * u
        last = plate.get("last") or {}
        if last:
            ok = bool(last.get("ok"))
            where = " (on the Connect page)" if last.get("where") == "connect page" else ""
            # round 33 (.101: "Test passed in None ms: “”"): a pass on the Connect page carried no time or reply; say
            # only what the record holds, as the page does
            msg = ("Test passed" + where + (f" in {last.get('ms')} ms" if last.get("ms") is not None else "")
                   + (f": “{last.get('reply')}”" if last.get("reply") else "") if ok
                   else f"Test failed{where}: {last.get('error') or 'no answer'}")
            y = _lines(d, x, y + 0.3 * u, msg, f_n, (R.SAGE if ok else R.RUST) + (255,), pw - 3.2 * u)
        ky = y0 + ph - 2.6 * u
        kx = x
        if plate.get("later") and not plate.get("tested") and plate.get("later_ask"):
            # SET UP LATER asks first (the User 2026-10-10, .101: one click on this plate recorded it): the question
            # and its two keys, nothing else on the row
            _lines(d, x, ky - 2.6 * u, "Set up later? " + R.crissa_home.her_name() + " will not answer until an agent is connected. The Connect "
                   "page sets one up any time.", f_n, R.EMBER + (255,), pw - 3.2 * u)
            kx += _key(d, hits, "init2_connect_later", kx, ky, "YES, SET UP LATER", u, ptr=ptr) + 0.8 * u
            _key(d, hits, "init2_connect_later_back", kx, ky, "BACK", u, ptr=ptr)
        else:
            if offer:
                kx += _key(d, hits, "init2_connect_use", kx, ky, "USE IT", u, lit=not plate.get("tested"), ptr=ptr) + 0.8 * u
            if cv.get("editable"):
                kx += _key(d, hits, "init2_connect_edit", kx, ky, "EDIT", u, ptr=ptr) + 0.8 * u
            kx += _key(d, hits, "init2_connect_test", kx, ky, "TEST", u, ptr=ptr) + 0.8 * u
            kx += _key(d, hits, "init2_connect_continue", kx, ky, "CONTINUE", u, lit=bool(plate.get("tested")),
                       ptr=ptr) + 0.8 * u
            if plate.get("later") and not plate.get("tested"):
                # SET UP LATER (the User 2026-10-10): Init carries on with no agent; she says she is not connected yet
                _key(d, hits, "init2_connect_later_ask", kx, ky, "SET UP LATER", u, ptr=ptr)

    elif kind == "machine":
        # a pack's machine card (the User 2026-10-10, lib/crissa_init.card_view): ask (ALLOW / SKIP), measuring,
        # the recommendation (USE THIS / NO THANKS), then the voice installed (RETRY / CONTINUE when it failed)
        m = plate.get("machine") or {}
        state = str(m.get("state") or "")
        keys = [(str(v_), str(l_)) for v_, l_ in (m.get("keys") or [])]
        lines = [str(x) for x in (m.get("lines") or []) if x]
        x0, y0, pw, ph = _panel(d, W, H, u, 36.0, 16.0 if state in ("ask", "ready") else 14.0)
        x, y = x0 + 1.6 * u, y0 + 1.4 * u
        y = _lines(d, x, y, str(m.get("title") or plate.get("title") or ""), f_t, R.INK + (255,), pw - 3.2 * u)
        y += 0.5 * u
        if m.get("body"):
            y = _lines(d, x, y, str(m["body"]), f_b, R.INK2 + (255,), pw - 3.2 * u)
        if m.get("hint"):          # the facts (how long, no sound, nothing leaves), or what to press
            y = _lines(d, x, y + 0.4 * u, str(m["hint"]), f_n, (R.RUST if state == "voice_failed" else R.INK2) + (255,),
                       pw - 3.2 * u)
        for ln in lines[:4]:       # the skip line, the reasons, the install's progress
            y = _lines(d, x, y + 0.2 * u, ln, f_n, R.INK3 + (255,), pw - 3.2 * u)
        ky = y0 + ph - 2.6 * u
        kx = x
        for verb, label in keys:
            kx += _key(d, hits, "init2_machine_" + verb, kx, ky, label.upper(), u, ptr=ptr) + 0.8 * u
        if state in ("ask", "ready") and isinstance(plate.get("language"), dict):    # the stand-in walk asks it
            lg = plate.get("language") or {}
            _key(d, hits, "init2_language", kx, ky, "LANGUAGE: %s" % str(lg.get("code") or "en").upper(), u, ptr=ptr)

    elif kind == "register" and (plate.get("register") or {}).get("driver") == "walker":
        # A PACK'S VOICE ID (the User 2026-10-10, lib/crissa_init): the name, then the Register coach's routine, which
        # the walker drives; while it runs the register layer is the glass (register_live above), and the coach's
        # phases are its own. A refusal or a coach that is not there: RETRY / SKIP.
        reg = plate.get("register") or {}
        state = str(reg.get("state") or "")
        keys = [(str(v_), str(l_)) for v_, l_ in (plate.get("keys") or [])]
        if state == "name":
            x0, y0, pw, ph = _panel(d, W, H, u, 32.0, 11.0)
            x, y = x0 + 1.6 * u, y0 + 1.4 * u
            R._text(d, (x, y), str(plate.get("ask") or ""), f_t, R.INK + (255,), 0.0)
            y += 2.6 * u
            y = _lines(d, x, y, str(plate.get("body") or ""), f_n, R.INK2 + (255,), pw - 3.2 * u)
            if plate.get("name_error"):
                _lines(d, x, y + 0.4 * u, str(plate["name_error"]), f_n, R.RUST + (255,), pw - 3.2 * u)
            ky = y0 + ph - 2.6 * u
            kx = x + _key(d, hits, "init2_name", x, ky, "TYPE YOUR NAME", u, ptr=ptr) + 0.8 * u
            for verb, label in keys:
                kx += _key(d, hits, "init2_reg_" + verb, kx, ky, label.upper(), u, ptr=ptr) + 0.8 * u
        elif state in ("failed", "no_coach"):
            x0, y0, pw, ph = _panel(d, W, H, u, 34.0, 11.0)
            x, y = x0 + 1.6 * u, y0 + 1.4 * u
            y = _lines(d, x, y, str(plate.get("title") or "Voice ID"), f_t, R.INK + (255,), pw - 3.2 * u)
            y = _lines(d, x, y + 0.4 * u, str(plate.get("body") or ""), f_b, R.RUST + (255,), pw - 3.2 * u)
            _lines(d, x, y + 0.3 * u, str(reg.get("hint") or ""), f_n, R.INK2 + (255,), pw - 3.2 * u)
            ky = y0 + ph - 2.6 * u
            kx = x
            for verb, label in keys:
                kx += _key(d, hits, "init2_reg_" + verb, kx, ky, label.upper(), u, ptr=ptr) + 0.8 * u
        elif state == "live":
            # THE SENTENCE ON THE PLATE (the User 2026-10-10, .101 V): while the coach's own layer is not drawing (its
            # glass not read yet, or read from elsewhere) the plate carries the glass - the cue, the sentence, the take
            g = reg.get("glass") if isinstance(reg.get("glass"), dict) else {}
            x0, y0, pw, ph = _panel(d, W, H, u, 40.0, 12.0)
            x, y = x0 + 1.6 * u, y0 + 1.4 * u
            take, takes = int(g.get("take") or 0), int(g.get("takes") or 0)
            R._text(d, (x, y), ("RECORDING %d OF %d" % (take, takes)) if take and takes else "VOICE ID", f_n,
                    R.INK2 + (255,), 0.18)
            y += 1.4 * u
            cue = str(g.get("cue") or str(plate.get("body") or ""))
            y = _lines(d, x, y, cue, f_b, R.EMBER + (255,), pw - 3.2 * u)
            words = str(g.get("text") or g.get("line") or "")
            if g.get("phase") in ("watch", "thanks") and g.get("line"):
                words = str(g.get("line"))
            y = _lines(d, x, y + 0.5 * u, words, f_t, R.INK + (255,), pw - 3.2 * u)
            if g.get("mic"):
                _lines(d, x, y + 0.3 * u, "Recording on: %s" % g.get("mic"), f_n, R.INK3 + (255,), pw - 3.2 * u)
        elif state in ("starting", "done"):
            msg = str(plate.get("body") or "")
            tw = R._text_w(d, msg, f_b, 0.02)
            R._text(d, ((W - tw) / 2, H * 0.82), msg, f_b, R.INK + (255,), 0.02)

    elif kind == "face":
        # A PACK'S FACE PHOTOS (the User 2026-10-10): the coach's face section, its progress on the plate; with no
        # camera the step says why and goes on by itself (CONTINUE goes now)
        fc = plate.get("face") or {}
        keys = [(str(v_), str(l_)) for v_, l_ in (plate.get("keys") or [])]
        x0, y0, pw, ph = _panel(d, W, H, u, 34.0, 11.0)
        x, y = x0 + 1.6 * u, y0 + 1.4 * u
        y = _lines(d, x, y, str(plate.get("title") or "Face photos"), f_t, R.INK + (255,), pw - 3.2 * u)
        y = _lines(d, x, y + 0.4 * u, str(plate.get("body") or ""), f_b,
                   (R.RUST if fc.get("state") == "failed" else R.INK2) + (255,), pw - 3.2 * u)
        if fc.get("hint"):
            y = _lines(d, x, y + 0.3 * u, str(fc["hint"]), f_n, R.INK3 + (255,), pw - 3.2 * u)
        pr = fc.get("progress") or {}
        if pr.get("total"):
            bw = pw - 3.2 * u
            frac = max(0.0, min(1.0, float(pr.get("done") or 0) / float(pr["total"])))
            d.rectangle((x, y + 0.6 * u, x + bw, y + 0.9 * u), fill=R.INK3 + (160,))
            d.rectangle((x, y + 0.6 * u, x + bw * frac, y + 0.9 * u), fill=R.EMBER + (255,))
        ky = y0 + ph - 2.6 * u
        kx = x
        for verb, label in keys:
            kx += _key(d, hits, "init2_face_" + verb, kx, ky, label.upper(), u, ptr=ptr) + 0.8 * u

    elif kind == "room" and _pack_walk(snap):
        room_plate(d, hits, W, H, u, plate, ptr)

    elif kind == "hours" and _pack_walk(snap):
        # YOUR HOURS (the User 2026-10-11): the two windows, SAVE / CHANGE (typed in her window, or a small box) / SKIP
        hv = plate.get("hours") or {}
        x0, y0, pw, ph = _panel(d, W, H, u, 36.0, 14.0)
        x, y = x0 + 1.6 * u, y0 + 1.4 * u
        y = _lines(d, x, y, str(plate.get("title") or "Your hours"), f_t, R.INK + (255,), pw - 3.2 * u)
        y = _lines(d, x, y + 0.4 * u, str(plate.get("body") or ""), f_b, R.INK2 + (255,), pw - 3.2 * u)
        for ln in hv.get("lines") or []:
            y = _lines(d, x, y + 0.3 * u, str(ln), f_n, R.INK + (255,), pw - 3.2 * u)
        if plate.get("hint"):
            _lines(d, x, y + 0.3 * u, str(plate["hint"]), f_n, R.RUST + (255,), pw - 3.2 * u)
        ky = y0 + ph - 2.6 * u
        kx = x
        for verb, label in [(str(v_), str(l_)) for v_, l_ in (plate.get("keys") or [])]:
            kx += _key(d, hits, ("init2_hr_change" if verb == "change" else "init2_hr_" + verb), kx, ky, label.upper(),
                       u, ptr=ptr) + 0.8 * u

    elif kind == "register":
        reg = plate.get("register") or {}
        if not reg.get("pending") and not reg.get("sent"):
            x0, y0, pw, ph = _panel(d, W, H, u, 30.0, 10.0)
            x, y = x0 + 1.6 * u, y0 + 1.4 * u
            R._text(d, (x, y), str(plate.get("ask") or ""), f_t, R.INK + (255,), 0.0)
            y += 2.6 * u
            name = str(plate.get("name") or "")
            R._text(d, (x, y), name or "—", R.font("light", 1.3 * u), (R.INK if name else R.INK3) + (255,), 0.0)
            if plate.get("name_error"):
                _lines(d, x, y + 1.9 * u, str(plate["name_error"]), f_n, R.RUST + (255,), pw - 3.2 * u)
            ky = y0 + ph - 2.6 * u
            if plate.get("name_fixed"):
                _key(d, hits, "init2_name_go", x, ky, "THAT'S ME", u, ptr=ptr)
            else:
                _key(d, hits, "init2_name", x, ky, "TYPE YOUR NAME", u, ptr=ptr)

    elif kind == "room_shot":
        room = plate.get("room") or {}
        state = str(room.get("state") or "")
        if state in ("aiming", "checking", "picking"):
            msg = {"aiming": "Looking at the room behind your screen…", "checking": "Checking the picture…",
                   "picking": "Choose a photo of the room behind your screen…"}[state]
            tw = R._text_w(d, msg, f_b, 0.02)
            R._text(d, ((W - tw) / 2, H * 0.82), msg, f_b, R.INK + (255,), 0.02)
        elif state == "countdown":
            n = max(0, math.ceil(float(room.get("countdown_until") or now) - now))
            big = R.font("serif", 6.0 * u)
            s_ = str(n) if n else "•"
            tw = d.textlength(s_, font=big)
            R._text(d, ((W - tw) / 2, H * 0.36), s_, big, R.EMBER + (255,), 0.0)
            tw = R._text_w(d, str(plate.get("hold_line") or ""), f_b, 0.02)
            R._text(d, ((W - tw) / 2, H * 0.62), str(plate.get("hold_line") or ""), f_b, R.INK + (255,), 0.02)
        elif state == "choose":
            x0, y0, pw, ph = _panel(d, W, H, u, 34.0, 12.0)
            x, y = x0 + 1.6 * u, y0 + 1.4 * u
            laptop = room.get("form") == "laptop"
            if laptop and not room.get("choice"):
                y = _lines(d, x, y, str(plate.get("laptop_ask") or ""), f_b, R.INK + (255,), pw - 3.2 * u)
                ky = y0 + ph - 2.6 * u
                kx = x + _key(d, hits, "init2_room_room", x, ky, "YOUR ROOM", u, ptr=ptr) + 0.8 * u
                _key(d, hits, "init2_room_office", kx, ky, "HER OFFICE", u, ptr=ptr)
            else:
                if room.get("error"):
                    y = _lines(d, x, y, str(room["error"]), f_b, R.RUST + (255,), pw - 3.2 * u) + 0.4 * u
                y = _lines(d, x, y, R.crissa_home.her_name() + " sits in the room behind your screen, across the desk from you. "
                                    "Take that picture now, or use a photo from your phone.", f_b, R.INK2 + (255,), pw - 3.2 * u)
                ky = y0 + ph - 2.6 * u
                kx = x
                if laptop:
                    kx += _key(d, hits, "init2_room_hold", kx, ky, "HOLD STILL", u, ptr=ptr) + 0.8 * u
                else:
                    kx += _key(d, hits, "init2_room_retry", kx, ky, "TRY THE CAMERA", u, ptr=ptr) + 0.8 * u
                kx += _key(d, hits, "init2_room_upload", kx, ky, "UPLOAD A PHOTO", u, ptr=ptr) + 0.8 * u
                _key(d, hits, "init2_room_office", kx, ky, "HER OFFICE", u, ptr=ptr)

    elif kind == "overlay":
        ov = plate.get("overlay") or {}
        lines = plate.get("lines") or {}
        stage = str(ov.get("stage") or "")
        marks_in = ov.get("marks") or {}
        clk = plate.get("clock") or {}
        user_hour = float(clk.get("user_hour") or 6)
        door = marks_in.get("door") or {}
        marks = {user_hour: ("YOU", R.SAGE if "seat" in marks_in else R.EMBER)}
        if door.get("hour"):
            marks[float(door["hour"])] = ("DOOR", R.EMBER)
        r = min(W, H) * 0.17
        cx, cy = W - r * 1.45, r * 1.45
        _clock(d, hits, cx, cy, r, marks=marks, door_hour=int(door["hour"]) if door.get("hour") else None,
               pick_hours=(stage == "door" and not door.get("hour")), u=u)
        rect = PHOTO.get("rect")
        for key, col in (("crissa", R.INK), ("door", R.EMBER)):
            pt = (marks_in.get(key) or {}).get("point")
            if pt and rect:
                px = rect[0] + pt[0] * (rect[2] - rect[0])
                py = rect[1] + pt[1] * (rect[3] - rect[1])
                rr = 0.7 * u
                d.ellipse((px - rr, py - rr, px + rr, py + rr), outline=col + (255,), width=3)
                lab = R.crissa_home.her_name().upper() if key == "crissa" else "DOOR"
                R._text(d, (px + rr * 1.4, py - rr), lab, f_n, col + (255,), 0.1)
        prompt = {"seat": lines.get("seat"), "crissa": lines.get("crissa"),
                  "door": (lines.get("door") if not door.get("hour") else "Now tap the door in the picture, or say it isn't in it.")
                  }.get(stage, "")
        bw = min(W * 0.62, 60 * u)
        bx, by = (W - bw) / 2, H - 9.2 * u
        d.rectangle((bx, by, bx + bw, by + 6.2 * u), fill=R.GROUND + (225,), outline=R.EMBER_DEEP + (255,))
        y = _lines(d, bx + 1.4 * u, by + 1.0 * u, str(prompt or ""), f_b, R.INK + (255,), bw - 2.8 * u)
        if stage == "seat":
            y = _lines(d, bx + 1.4 * u, y, str(lines.get("ar") or ""), f_n, R.INK2 + (255,), bw - 2.8 * u)
            _key(d, hits, "init2_seat_ok", bx + bw - 9 * u, by + 6.2 * u - 2.6 * u, "OK", u, ptr=ptr, wide=7.5 * u)
        elif stage in ("crissa", "door") and rect and (stage == "crissa" or door.get("hour")):
            hits["init2_photo"] = rect
            if stage == "door":
                _key(d, hits, "init2_door_none", bx + bw - 17 * u, by + 6.2 * u - 2.6 * u, "NOT IN THE PICTURE", u, ptr=ptr)
    return im
