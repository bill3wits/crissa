"""Kiosk window on the monitor → crop of the camera FOV.

``window.screenX`` in this Brave --app is stuck (~2,5) even when the window
is on the right of the desktop. X11 (xwininfo) is the SoT for the hole.

A background sampler reads the window a few times a second. Voice-Only OS
fullscreen skips the crop (original contain-fit of the whole FOV).
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from typing import Any

# Client leaf, not the 200x200 stub and not the mutter frame.
# Match on the WM_CLASS *class* — "crissa-display" — and never on the instance.
# The instance is Brave's to choose and it has already changed once: it was
# "127.0.0.1", derived from the --app URL's host, and is now
# "brave-browser-beta (<the glass's own profile folder>)" — the binary plus the
# profile path. Pinning it meant this matched nothing, kiosk_bounds returned
# "no crissa window" forever, and the wall's AR button was disabled in EVERY
# mode because paintArToggle gates on bounds. The class is ours: the launcher
# sets it explicitly with --class=crissa-display, so it is the half worth
# matching. Quote-anchored, so a parenthesised profile path cannot break it.
_CLIENT_RE = re.compile(
    r'(0x[0-9a-fA-F]+)\s+"[^"]*":\s+\("[^"]*" "crissa-display"\)\s+'
    r"(\d+)x(\d+)[+-]\d+[+-]\d+\s+"
    r"([+-]\d+)([+-]\d+)"
)
HOUSE_CLASS = "crissa-display"

# A PACK FOLLOWS HER OWN WINDOW (.101, 2026-10-12: "AR on the wall page not working here"). In a pack the glass
# (display/voiceonly-home.html) fills her own browser window (lib/crissa_window: the /wall page's #glass frame), whose
# class is crissa_window.WM_CLASS, not the house glass launcher's "crissa-display", so a pack never matched a window,
# kiosk-bounds answered "no crissa window" and the AR key stayed grey. The house matches crissa-display alone, with the
# same pattern as before (_CLIENT_RE). Kill: CRISSA_AR_OWN_WINDOW=0 (a pack matches crissa-display alone again).
OWN_WINDOW_SWITCH = "CRISSA_AR_OWN_WINDOW"
_classes: tuple[str, ...] | None = None


def window_classes(env: dict | None = None) -> tuple[str, ...]:
    """The WM_CLASS classes kiosk_bounds follows: the house's ("crissa-display",); a pack's her own window's class
    (lib/crissa_window.WM_CLASS, read from there, never spelled here) and then crissa-display. Read once a process."""
    global _classes
    if env is None and _classes is not None:
        return _classes
    e = os.environ if env is None else env
    got: tuple[str, ...] = (HOUSE_CLASS,)
    if str(e.get(OWN_WINDOW_SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no"):
        try:
            import crissa_home
            if not crissa_home.is_house():
                import crissa_window
                own = str(getattr(crissa_window, "WM_CLASS", "") or "").strip()
                if own and own != HOUSE_CLASS:
                    got = (own, HOUSE_CLASS)
        except Exception:  # noqa: BLE001  (no window module in this edition: the house's class alone)
            got = (HOUSE_CLASS,)
    if env is None:
        _classes = got
    return got


def _client_re(classes: tuple[str, ...]) -> "re.Pattern[str]":
    if tuple(classes) == (HOUSE_CLASS,):
        return _CLIENT_RE
    alt = "|".join(re.escape(c) for c in classes)
    return re.compile(
        r'(0x[0-9a-fA-F]+)\s+"[^"]*":\s+\("[^"]*" "(?:' + alt + r')"\)\s+'
        r"(\d+)x(\d+)[+-]\d+[+-]\d+\s+"
        r"([+-]\d+)([+-]\d+)"
    )
_ROOT_W = re.compile(r"^\s*Width:\s+(\d+)\s*$", re.M)
_ROOT_H = re.compile(r"^\s*Height:\s+(\d+)\s*$", re.M)
_ABS_X = re.compile(r"^\s*Absolute upper-left X:\s+(-?\d+)\s*$", re.M)
_ABS_Y = re.compile(r"^\s*Absolute upper-left Y:\s+(-?\d+)\s*$", re.M)
_W = re.compile(r"^\s*Width:\s+(\d+)\s*$", re.M)
_H = re.compile(r"^\s*Height:\s+(\d+)\s*$", re.M)

_WINDOWED_SEC = 0.25
_FULLSCREEN_SEC = 1.0


def parse_root_size(text: str) -> tuple[int, int] | None:
    mw, mh = _ROOT_W.search(text or ""), _ROOT_H.search(text or "")
    if not mw or not mh:
        return None
    return int(mw.group(1)), int(mh.group(1))


def parse_crissa_client(tree: str, classes: tuple[str, ...] | None = None) -> tuple[int, int, int, int] | None:
    """Return (x, y, w, h) of the largest client of ours (window_classes), absolute px."""
    hit = parse_crissa_client_id(tree, classes)
    if not hit:
        return None
    return hit[1], hit[2], hit[3], hit[4]


def parse_crissa_client_id(tree: str, classes: tuple[str, ...] | None = None) -> tuple[str, int, int, int, int] | None:
    """Return (xid, x, y, w, h) of the largest client of ours (window_classes: the house's crissa-display; in a pack
    her own window too)."""
    rx = _client_re(window_classes() if classes is None else tuple(classes))
    best: tuple[int, str, int, int, int, int] | None = None
    for line in (tree or "").splitlines():
        m = rx.search(line)
        if not m:
            continue
        xid, w, h, x, y = (
            m.group(1),
            int(m.group(2)),
            int(m.group(3)),
            int(m.group(4)),
            int(m.group(5)),
        )
        if w < 400 or h < 300:
            continue
        area = w * h
        if best is None or area > best[0]:
            best = (area, xid, x, y, w, h)
    if not best:
        return None
    return best[1], best[2], best[3], best[4], best[5]


def is_fullscreen(x: int, y: int, w: int, h: int, sw: int, sh: int) -> bool:
    return w >= sw - 16 and h >= sh - 16 and abs(x) <= 16 and abs(y) <= 16


def fov_source_rect(
    img_w: int,
    img_h: int,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    sw: int,
    sh: int,
) -> dict[str, float] | None:
    """Image crop for this window. None → contain-fit the whole FOV.

    FOV is painted fill-height onto the monitor (camera aspect, centered).
    The window is a hole in that plane.
    """
    if img_w <= 0 or img_h <= 0 or sw <= 0 or sh <= 0:
        return None
    if is_fullscreen(x, y, w, h, sw, sh):
        return None
    plane_h = float(sh)
    plane_w = plane_h * (float(img_w) / float(img_h))
    plane_x = (float(sw) - plane_w) / 2.0
    plane_y = 0.0
    if plane_w < 1 or plane_h < 1:
        return None
    return {
        "sx": (float(x) - plane_x) / plane_w * img_w,
        "sy": (float(y) - plane_y) / plane_h * img_h,
        "sw": float(w) / plane_w * img_w,
        "sh": float(h) / plane_h * img_h,
    }


_screen: tuple[int, int] | None = None
_xid: str | None = None
_latest: dict[str, Any] = {"ok": False}
_lock = threading.Lock()
_sampler_started = False


def _env(display: str | None = None) -> dict[str, str]:
    return {**os.environ, "DISPLAY": display or os.environ.get("DISPLAY") or ":0"}


def _run_xwininfo(args: list[str], display: str | None = None) -> str:
    r = subprocess.run(
        ["xwininfo", *args],
        capture_output=True,
        text=True,
        timeout=1.5,
        env=_env(display),
    )
    return r.stdout or ""


def _screen_size(display: str | None = None) -> tuple[int, int]:
    global _screen
    if _screen:
        return _screen
    parsed = parse_root_size(_run_xwininfo(["-root"], display))
    _screen = parsed or (2560, 1440)
    return _screen


def _geom_from_id(xid: str, display: str | None = None) -> tuple[int, int, int, int] | None:
    text = _run_xwininfo(["-id", xid], display)
    mx, my, mw, mh = _ABS_X.search(text), _ABS_Y.search(text), _W.search(text), _H.search(text)
    if not (mx and my and mw and mh):
        return None
    w, h = int(mw.group(1)), int(mh.group(1))
    if w < 400 or h < 300:
        return None
    return int(mx.group(1)), int(my.group(1)), w, h


def kiosk_bounds(*, display: str | None = None) -> dict[str, Any]:
    """One X11 read. Prefer a cached window id (one xwininfo) over a full tree."""
    global _xid
    sw, sh = _screen_size(display)
    geom = None
    if _xid:
        try:
            geom = _geom_from_id(_xid, display)
        except (OSError, subprocess.TimeoutExpired):
            geom = None
        if geom is None:
            _xid = None
    if geom is None:
        try:
            tree = _run_xwininfo(["-root", "-tree"], display)
            hit = parse_crissa_client_id(tree)
        except (OSError, subprocess.TimeoutExpired):
            hit = None
        if hit:
            _xid, x, y, w, h = hit
            geom = (x, y, w, h)
    if not geom:
        return {"ok": False, "error": "no crissa window", "sw": sw, "sh": sh}
    x, y, w, h = geom
    return {
        "ok": True,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "sw": sw,
        "sh": sh,
        "fullscreen": is_fullscreen(x, y, w, h, sw, sh),
    }


def latest_bounds() -> dict[str, Any]:
    start_sampler()
    with _lock:
        data = dict(_latest)
    if not data.get("ok"):
        data = kiosk_bounds()
        with _lock:
            _latest.update(data)
    return data


def start_sampler() -> None:
    global _sampler_started
    if _sampler_started:
        return
    _sampler_started = True

    def loop() -> None:
        while True:
            try:
                data = kiosk_bounds()
            except Exception:
                data = {"ok": False, "error": "bounds failed"}
            with _lock:
                _latest.clear()
                _latest.update(data)
            time.sleep(_FULLSCREEN_SEC if data.get("fullscreen") else _WINDOWED_SEC)

    threading.Thread(target=loop, name="fov-bounds", daemon=True).start()
