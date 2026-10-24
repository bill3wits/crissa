"""FACE ONLY (the User 2026-10-15 on .101: "can we request a face only mode where her rendered talking image is a
draggable, resizable box on the screen? Like the mobile app will be?").

A small window of her own showing only her painted, talking face: the player's own picture (/frame.mjpg, the same
mouths, lead and lip sync), cut to the phone's layout - a vertical 9:16 slice of the film's full height, centred on her
nose from the head track (the phone thread's pose-vertical-window rule, live), so she stays in it as she moves.
The page is /face on the player; it asks /v1/face-point for where her face is. The window is a window of her own
browser (lib/crissa_window.show_face), so the person drags and resizes it as any window; its place and size are kept
in <state home>/face-window.json (the page posts them) and given back the next time it opens.

A pack only: the house has no face window (is_house). Kill CRISSA_FACE_ONLY=0.

THE WALL OFF (the User 2026-10-16: "persisted above all windows ... That is how I believe the ASCII wall off mode will
perform without the compute under it. Maybe this mode would work on even older cpus"): when the desk Wall is closed -
Close the Wall in the menu, or a start that finds it closed - her face window opens and is kept ABOVE every window
(crissa_window._keep_above, as the house's float and chips), so she stays on screen with no ASCII drawn under her; Open
the Wall closes it again. CRISSA_WALL_OFF_FACE=0: a closed Wall shows nothing of her, as before.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import crissa_home

ASPECT = 9.0 / 16.0                 # the phone's layout: a vertical slice, the film's full height
DEFAULT_H_SHARE = 0.55              # a first window: 55 % of the screen's height, at its right edge
FILE = "face-window.json"


def on(env=None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("CRISSA_FACE_ONLY", "")).strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    if raw in ("1", "true", "on", "yes"):
        return True                 # asked for by name (a bench trying it on the house)
    try:
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001
        return False


def point(head: dict | None, wh: tuple) -> dict:
    """Where her face is on the picture, as shares of the picture: cx, cy (her nose), s (her face size / height)."""
    W, H = float(wh[0] or 1), float(wh[1] or 1)
    try:
        nx, ny = float(head["nose"][0]), float(head["nose"][1])
        s = float(head.get("s") or 0.0)
    except (TypeError, KeyError, IndexError, ValueError):
        return {"ok": False, "cx": 0.5, "cy": 0.45, "s": 0.0, "w": int(W), "h": int(H)}
    return {"ok": True, "cx": round(nx / W, 4), "cy": round(ny / H, 4), "s": round(s / H, 4), "w": int(W), "h": int(H)}


def bounds(home: Path | None = None) -> dict | None:
    """The window's kept place and size {x, y, w, h}, or None."""
    try:
        d = json.loads((Path(home or crissa_home.HOME) / FILE).read_text(encoding="utf-8"))
        b = {k: int(d[k]) for k in ("x", "y", "w", "h")}
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return b if b["w"] >= 120 and b["h"] >= 120 else None


def shape(home: Path | None = None) -> str:
    """The tile's shape: "face" (the phone's 9:16, her nose in the middle) or "room" (the whole picture, 16:9)."""
    try:
        d = json.loads((Path(home or crissa_home.HOME) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "face"
    return "room" if isinstance(d, dict) and d.get("shape") == "room" else "face"


def save(b: dict, home: Path | None = None, shape_: str | None = None) -> bool:
    try:
        rec = {k: int(b[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        return False
    if rec["w"] < 120 or rec["h"] < 120:
        return False
    rec["shape"] = shape_ if shape_ in SHAPES else shape(home)
    rec["at"] = round(time.time(), 1)
    path = Path(home or crissa_home.HOME) / FILE
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return False
    return True


SHAPES = ("face", "room")
SIZE_STEP = 1.12                    # one press of - / + on the tile's bar


def resized(b: dict, shape_: str, step: int, screen: tuple | None) -> dict:
    """THE TILE'S BAR (the User 2026-10-16: "buttons show on tile on hover for that toggle and size"): the window's
    next bounds for a shape and a size step, about its centre and inside the screen. Changing shape keeps the long side:
    the 9:16 face's height becomes the 16:9 room's width, and back."""
    sw, sh = (screen or (1920, 1080))
    try:
        x, y, w, h = (int(b[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        x, y, w, h = (first_bounds(screen)[k] for k in ("x", "y", "w", "h"))
    long_ = max(w, h) * (SIZE_STEP ** int(step))
    long_ = max(200.0, min(long_, (sh if shape_ != "room" else sw) * 0.95))
    if shape_ == "room":
        nw, nh = long_, long_ * 9.0 / 16.0
        if nh > sh * 0.95:
            nh = sh * 0.95; nw = nh * 16.0 / 9.0
    else:
        nh, nw = long_, long_ * ASPECT
    cx, cy = x + w / 2.0, y + h / 2.0
    nx = int(min(max(0, cx - nw / 2.0), max(0, sw - nw)))
    ny = int(min(max(0, cy - nh / 2.0), max(0, sh - nh)))
    return {"x": nx, "y": ny, "w": int(round(nw)), "h": int(round(nh))}


def first_bounds(screen: tuple | None) -> dict:
    """A first window when none is kept: 9:16, 55 % of the screen's height, against its right edge."""
    sw, sh = (screen or (1920, 1080))
    h = int(sh * DEFAULT_H_SHARE)
    w = int(round(h * ASPECT))
    return {"x": max(0, int(sw) - w - 32), "y": max(0, (int(sh) - h) // 2), "w": w, "h": h}


def open_window(player_url: str, home: Path | None = None, above: bool = False) -> bool:
    """Open (or bring forward) the face window, on a thread: its kept place and size, else a first one. `above` keeps
    it over every other window (the Wall off)."""
    if not on():
        return False
    import threading
    import crissa_window
    b = bounds(home) or first_bounds(crissa_window.screen_size())
    threading.Thread(target=crissa_window.show_face, args=(player_url.rstrip("/") + "/face", b),
                     kwargs={"above": bool(above)}, daemon=True, name="face-window").start()
    return True


def resize_window(player_url: str, b: dict, shape_: str, step: int, home: Path | None = None) -> dict:
    """Apply a bar press: the new bounds kept (with the shape) and set on her face window, on a thread."""
    shape_ = shape_ if shape_ in SHAPES else shape(home)
    import threading
    import crissa_window
    b = crissa_window.face_bounds(player_url.rstrip("/") + "/face") or b      # round 26: the browser's own bounds
    nb = resized(b, shape_, step, crissa_window.screen_size())
    save(nb, home, shape_)
    threading.Thread(target=crissa_window.place_face, args=(player_url.rstrip("/") + "/face", nb), daemon=True,
                     name="face-window-size").start()
    return dict(nb, shape=shape_)


# ITS PLACE IS THE BROWSER'S, AND IT SNAPS HOME (round 26, .101 2026-10-16: "face only did NOT reopen where I dragged
# it", "gets bigger every time I reopen it"; the User: "The face window snaps into place when dragged close to its
# home"). keep() saves the browser's own window bounds (crissa_window.face_bounds; the page's numbers only when the
# browser cannot say), and a window let go within SNAP_PX of its home - against the screen's right edge, centred,
# where a first window opens - goes there. Kill: CRISSA_FACE_SNAP_PX=0.
SNAP_PX = 48
EDGE_PX = 32


def snap_px(env=None) -> int:
    env = os.environ if env is None else env
    try:
        return max(0, int(float(env.get("CRISSA_FACE_SNAP_PX", SNAP_PX))))
    except (TypeError, ValueError):
        return SNAP_PX


def home_xy(b: dict, screen: tuple | None) -> tuple[int, int]:
    sw, sh = (screen or (1920, 1080))
    return max(0, int(sw) - int(b["w"]) - EDGE_PX), max(0, (int(sh) - int(b["h"])) // 2)


def snapped(b: dict, screen: tuple | None, env=None) -> dict | None:
    """The home bounds when `b` was let go near home (and is not there already), else None."""
    px = snap_px(env)
    if px <= 0:
        return None
    hx, hy = home_xy(b, screen)
    if (int(b["x"]), int(b["y"])) == (hx, hy):
        return None
    if abs(int(b["x"]) - hx) <= px and abs(int(b["y"]) - hy) <= px:
        return dict(b, x=hx, y=hy)
    return None


# SHE COMES BESIDE THE CARD (round 33, .101 10-19, the User: "the render window is hidden still and on house repositions
# to the middle as she speaks"). The player decided "render on center ... (centred beside the page, in view)", but only
# the browser Wall's page ever read that (GET /v1/layout, its img#face). Her face window starts against the right edge
# (first_bounds), and the sheet she shows a card in is the right 40 % of the screen, brought forward and kept above.
# So a card covered her. Now, once her sheet is up, an open face window moves into the layout's box: 9:16 at the box's
# height, centred in it. It comes forward and is kept above, over the sheet's edge if they meet. When the card comes
# down it goes back to the place it had. Her kept place is never written with the middle. A pack only.
# Kill CRISSA_FACE_BESIDE_CARD=0.
_BESIDE: dict = {"back": None}


def beside_card_on(env=None) -> bool:
    env = os.environ if env is None else env
    return on(env) and str(env.get("CRISSA_FACE_BESIDE_CARD", "1")).strip().lower() not in ("0", "off", "false", "no")


def beside_bounds(box: list, screen: tuple | None) -> dict:
    """The layout's box (fractions of the screen) as her window: 9:16 at the box's height, centred in the box."""
    sw, sh = (screen or (1920, 1080))
    bx, by, bw, bh = (float(v) for v in box[:4])
    h = int(round(bh * float(sh)))
    w = int(round(h * ASPECT))
    x = int(round(bx * float(sw) + (bw * float(sw) - w) / 2.0))
    return {"x": max(0, x), "y": max(0, int(round(by * float(sh)))), "w": w, "h": h}


def beside_card(player_url: str, card_up: bool, layout: dict | None = None, home: Path | None = None, *,
                face=None, place=None, bring=None, screen=None, env=None) -> str:
    """Her face window beside the card while it is up, back to its place when it comes down. Returns what it did."""
    if not beside_card_on(env):
        return "off"
    import crissa_window
    url = player_url.rstrip("/") + "/face"
    face = face or crissa_window.face_bounds
    place = place or crissa_window.place_face
    bring = bring or (lambda u: crissa_window.show_face(u, None, above=True))
    if not card_up:
        back = _BESIDE.get("back")
        _BESIDE["back"] = None
        if not back:
            return "not moved"
        ok = place(url, back)
        save(back, home)                             # the place she had, whatever the page posted meanwhile
        return "back" if ok else "back refused"
    lay = layout or {}
    if lay.get("render") != "on_tile" or lay.get("unit") != "fraction" or not lay.get("box"):
        return "no box"
    now = face(url)
    if not now:
        return "no face window"
    if _BESIDE.get("back") is None:
        _BESIDE["back"] = bounds(home) or {k: int(now[k]) for k in ("x", "y", "w", "h")}
    b = beside_bounds(lay["box"], screen or crissa_window.screen_size())
    ok = place(url, b)
    bring(url)
    return "beside the card" if ok else "place refused"


def keep(page_b: dict | None, player_url: str, home: Path | None = None) -> dict:
    """The window's place, kept: {"ok", "bounds", "snapped", "by"}."""
    import crissa_window
    url = player_url.rstrip("/") + "/face"
    b = crissa_window.face_bounds(url)
    by = "browser"
    if b is None:
        b, by = page_b, "page"
    try:
        b = {k: int(b[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "why": "no bounds"}
    sn = snapped(b, crissa_window.screen_size())
    if sn is not None:
        import threading
        threading.Thread(target=crissa_window.place_face, args=(url, sn), daemon=True, name="face-snap").start()
        b = sn
    return {"ok": save(b, home), "bounds": b, "snapped": sn is not None, "by": by}


# THE BAR WAKES ON ANY MOVE OF THE POINTER (round 26, the User 2026-10-16: the bar shows "on ANY mouse move anywhere on
# screen, not only over her window"). A page sees the pointer only over itself, so the player reads the screen's pointer
# (XQueryPointer on the root of the X / Xwayland display) four times a second while the face page asks for it, and
# stops five seconds after the last ask. Under a Wayland session X sees the pointer only over X windows (her browser
# among them); elsewhere the bar wakes on her window as before. Kill: CRISSA_FACE_POINTER=0.
_PTR: dict = {"xy": None, "moved": 0.0, "asked": 0.0, "running": False}


def pointer_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("CRISSA_FACE_POINTER", "1")).strip().lower() not in ("0", "false", "off", "no")


def pointer_moved_at(now: float | None = None) -> float:
    """When the screen's pointer last moved (0.0 = not known); starts the reader while the page asks."""
    if not pointer_on():
        return 0.0
    _PTR["asked"] = time.time() if now is None else now
    if not _PTR["running"]:
        _PTR["running"] = True
        import threading
        threading.Thread(target=_ptr_loop, name="face-pointer", daemon=True).start()
    return float(_PTR["moved"])


def _ptr_loop(query=None, sleep=time.sleep) -> None:
    try:
        query = query or _x_pointer()
        if query is None:
            return
        while time.time() - float(_PTR["asked"]) < 5.0:
            xy = query()
            if xy is not None and xy != _PTR["xy"]:
                if _PTR["xy"] is not None:
                    _PTR["moved"] = time.time()
                _PTR["xy"] = xy
            sleep(0.25)
    finally:
        _PTR["running"] = False


def _x_pointer():
    """A function that returns the root pointer (x, y), or None when there is no X display here."""
    import ctypes
    from ctypes import byref, c_char_p, c_int, c_uint, c_ulong, c_void_p
    disp = os.environ.get("DISPLAY") or ""
    if not disp:
        try:
            import crissa_window
            disp = (crissa_window.session_x11(dict(os.environ))[0] or {}).get("DISPLAY") or ""
        except Exception:  # noqa: BLE001
            disp = ""
    if not disp:
        return None
    try:
        x = ctypes.CDLL("libX11.so.6")
    except OSError:
        return None
    x.XOpenDisplay.restype = c_void_p
    x.XOpenDisplay.argtypes = [c_char_p]
    dpy = x.XOpenDisplay(disp.encode())
    if not dpy:
        return None
    x.XDefaultRootWindow.restype = c_ulong
    x.XDefaultRootWindow.argtypes = [c_void_p]
    x.XQueryPointer.argtypes = [c_void_p, c_ulong] + [c_void_p] * 7
    root = x.XDefaultRootWindow(dpy)

    def q():
        r, c = c_ulong(), c_ulong()
        rx, ry, wx, wy = c_int(), c_int(), c_int(), c_int()
        m = c_uint()
        x.XQueryPointer(dpy, root, byref(r), byref(c), byref(rx), byref(ry), byref(wx), byref(wy), byref(m))
        return int(rx.value), int(ry.value)
    return q


# THE VEIL IS THE WALL TILE'S (round 26: "Give it the same buttons"): BLUR on her face window is the Wall's FaceTime
# tile chip - one chip, three veils (off -> Blur -> Fade -> off), the same two prefs (house_wall_prefs blur / ft_fade),
# so either glass shows what the other set.
VEILS = ("off", "blur", "fade")


def veil() -> str:
    try:
        import house_wall_prefs as _p
        d = _p.load()
    except Exception:  # noqa: BLE001
        return "off"
    return "fade" if d.get("ft_fade") else ("blur" if d.get("blur") else "off")


def set_veil(v: str) -> str:
    v = v if v in VEILS else "off"
    try:
        import house_wall_prefs as _p
        d = _p.load()
        d.update(blur=(v == "blur"), ft_fade=(v == "fade"))
        _p.save(d)
    except Exception:  # noqa: BLE001
        return veil()
    return v


def wall_off_on(env=None) -> bool:
    """Her face on top while the desk Wall is closed: wherever Face only is on, unless CRISSA_WALL_OFF_FACE=0."""
    env = os.environ if env is None else env
    if str(env.get("CRISSA_WALL_OFF_FACE", "")).strip().lower() in ("0", "false", "off", "no"):
        return False
    return on(env)


def wall_off(player_url: str, home: Path | None = None) -> str:
    """The Wall was closed: her face on top. Returns the line for the person ('' when off)."""
    if not wall_off_on():
        return ""
    return ("Her face stays on top in a window of its own." if open_window(player_url, home, above=True) else "")


def wall_on(player_url: str) -> bool:
    """The Wall is open again: the face window the Wall off opened goes (on a thread)."""
    if not wall_off_on():
        return False
    import threading
    import crissa_window
    note_closed()                                   # round 28: a close of ours is not a page that died
    threading.Thread(target=crissa_window.close_face, args=(player_url.rstrip("/") + "/face",), daemon=True,
                     name="face-window-close").start()
    return True


# A FACE PAGE THAT DIED COMES BACK (round 28, .101 2026-10-17 05:46: "Now the box has crashed grey" - her browser had no
# /face page any more while the player and its picture were fine). Her face page polls /v1/face-point every 250 ms
# (seen); closing it on purpose posts {"closed": true} as the page goes (pagehide), and the Wall's return closes it
# (wall_on). A face page that was seen and has gone silent for STALE_S without either is reopened at its kept place,
# at most once a REOPEN_GAP_S. Kill: CRISSA_FACE_REOPEN=0.
STALE_S = 10.0
REOPEN_GAP_S = 60.0
_LIVE = {"seen": 0.0, "closed": True, "reopened": 0.0, "url": "", "watch": False, "painted": 0.0, "tries": 0,
         "checking": False}
# A REOPEN IS DONE WHEN THE PAGE PAINTS (round 29, .101 2026-10-17 15:44: the restart reopened a grey page twice): the
# page's poll says whether its picture moved in the last 4 s (?painted=1). After a reopen the watch waits STALE_S for a
# poll that answers AND paints; a page that answers grey, or not at all, is reopened again, up to REOPEN_TRIES, then
# said once in the log. Kill CRISSA_FACE_REOPEN_CHECK=0 (round 28: a reopen is done when it is asked).
REOPEN_TRIES = 3


def reopen_check_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("CRISSA_FACE_REOPEN_CHECK", "1")).strip().lower() not in ("0", "false", "off", "no")


def reopen_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("CRISSA_FACE_REOPEN", "1")).strip().lower() not in ("0", "false", "off", "no") and on(env)


def note_seen(player_url: str = "", now: float | None = None, painted: bool | None = None) -> None:
    t = time.time() if now is None else now
    _LIVE.update(seen=t, closed=False)
    if painted:
        _LIVE["painted"] = t
    if player_url:
        _LIVE["url"] = player_url
    if reopen_on() and not _LIVE["watch"]:
        _LIVE["watch"] = True
        import threading
        threading.Thread(target=_watch, daemon=True, name="face-reopen").start()


def note_closed() -> None:
    _LIVE["closed"] = True


# HER FACE WINDOW'S CLOSE KEY (round 45, .101 round 43, the User: "how do I close her face tile? no x or button"): the
# window is frameless, so only the window manager's Alt+F4 closed it. The tile's bar gets an x at its end; it closes
# the window through the player (closed on purpose, as the page's pagehide says: never reopened). Kill
# CRISSA_FACE_CLOSE_KEY=0 (no key, as round 44).
def close_key_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("CRISSA_FACE_CLOSE_KEY", "1")).strip().lower() not in ("0", "false", "off", "no")


def live(now: float | None = None) -> bool:
    """Her face window is up: its page polled within STALE_S and was not closed on purpose (round 43: the launcher's
    browser-Wall rule reads it on /v1/health)."""
    t = time.time() if now is None else now
    return bool(not _LIVE["closed"] and _LIVE["seen"] > 0 and t - _LIVE["seen"] <= STALE_S)


def reopen_due(now: float | None = None) -> bool:
    t = time.time() if now is None else now
    return (not _LIVE["closed"] and _LIVE["seen"] > 0 and t - _LIVE["seen"] > STALE_S
            and t - _LIVE["reopened"] > REOPEN_GAP_S)


def reopen_result(now: float | None = None) -> str:
    """After a reopen: 'ok' (a poll since that paints), 'grey' (polls since, none painting), 'silent' (no poll since),
    '' while the check window is still open."""
    t = time.time() if now is None else now
    r = _LIVE["reopened"]
    if _LIVE["painted"] > r:
        return "ok"
    if t - r < STALE_S:
        return ""
    return "grey" if _LIVE["seen"] > r else "silent"


def _watch(sleep=time.sleep) -> None:
    while reopen_on():
        sleep(2.0)
        if _LIVE["checking"] and reopen_check_on():
            res = reopen_result()
            if res == "ok":
                print("[face] the reopened page answers and paints", flush=True)
                _LIVE.update(checking=False, tries=0)
            elif res and _LIVE["tries"] < REOPEN_TRIES and not _LIVE["closed"] and _LIVE["url"]:
                _LIVE["tries"] += 1
                _LIVE["reopened"] = time.time()
                print("[face] the reopened page is %s after %.0f s: reopened again (%d of %d)"
                      % (res, STALE_S, _LIVE["tries"], REOPEN_TRIES), flush=True)
                try:
                    open_window(_LIVE["url"])
                except Exception:  # noqa: BLE001
                    pass
            elif res:
                print("[face] the reopened page is still %s after %d tries: left as it is" % (res, _LIVE["tries"]),
                      flush=True)
                _LIVE.update(checking=False, tries=0)
            continue
        if reopen_due() and _LIVE["url"]:
            _LIVE["reopened"] = time.time()
            _LIVE["checking"] = reopen_check_on()
            print("[face] her face page went silent %.0f s without a close: reopened" % (time.time() - _LIVE["seen"]),
                  flush=True)
            try:
                open_window(_LIVE["url"])
            except Exception:  # noqa: BLE001  the next look tries again
                pass
