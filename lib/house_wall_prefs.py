"""ASCII density + brightness prefs. Shared by the wall and the chrome sliders."""
from __future__ import annotations

import json
import os
from pathlib import Path

import crissa_home  # the state directory, resolved once (2026-09-26)

# HOUSE_WALL_PREFS moves the file for a public build (the startup split,
# 2026-09-22); unset, it is the house's file exactly as before. Read ONCE at
# import, and the wall, the float and the conductor all import this module —
# so they stay on one file as long as no unit sets the variable for only one.
PATH = Path(os.environ.get("HOUSE_WALL_PREFS") or (crissa_home.HOME / "house-wall-ascii.prefs.json"))
DEFAULTS = {
    "density": 1.0,
    "brightness": 0.75,        # #ascii opacity; .62 in CSS, .75 here matches the kiosk's lit pixels with PIL's glyphs
    "blur": False,
    "ft_fade": False,          # the FaceTime window's third veil: a faint picture (the User 2026-09-09: Blur → Fade → off)
    "ft_mode": "ft",          # ft = phone 4:6, room = landscape 16:9
    "ft_size": 1.0,
    "hole_ar": True,           # Hybrid AR: Brave/float samples the wallpaper plane
    "oracle_dot": False,       # kiosk .clockwrap.dot — collapsed to her core
    "folders": False,          # desktop folders (DING) hidden by default (the User 2026-09-12: "maybe default to hidden"); the Advanced menu shows them
    "vitals_folded": False,    # Speaker..Logs away; caret is chrome
    # LOGS chips (the kiosk's three log toggles; the User 2026-09-07). The key names are
    # the kiosk page's wire names and stay; a pack carries them renamed (crissa-export):
    # room=False is USER ONLY — the User's own speech, never the room's
    "log_board": True,
    "log_room": True,
    "log_tools": True,
    "pip_bar": False,          # camera contracted to a chrome bar
    "mode_bar": False,         # top-right mode/secure contracted to a chrome bar
    "dock_bar": False,         # the bottom dock folded to one expand square
    "ft_bar": False,            # FaceTime contracted to a chrome bar (#ftPop.bar)
    # Non-speech mix: rest-loop wraps before a bag fidget. 35 → 3 wraps
    # (the legacy metronome). Speech ignores this (hold_flourishes).
    "flourish_pct": 35,
    # HER WORKING DAY (the User 2026-10-04): hours she is at her desk, and whether she leaves at the end of it. Not
    # Init's: these live with the flourish knob. Settings only until the nightly leave/return lands (post-launch,
    # its own switch); nothing reads them yet. 9 to 5, leaving off.
    "work_start": 9,
    "work_end": 17,
    "leaves_at_end": False,
    # the small FaceTime tile's spot when the User dragged it (2026-10-09): [x, y] fractions of its free travel on
    # the glass; None = its corner
    "ft_tile": None,
    # HER FILM'S ZOOM under the whole frame (the User 2026-10-12, lib/wall_frame.py): 1.0 = the whole film, up to 3.0
    # about her face, in quarter steps. Shared by both glasses; the kiosk frame ignores it.
    "frame_zoom": 1.0,
}
# the keys that are ONE setting on both glasses (docs/glass-contract.yaml
# prefs.shared); the house holds the record, this file keeps engine extras
SHARED_KEYS = ("ft_mode", "ft_size", "blur", "ft_fade", "log_board", "log_room", "log_tools",
               "vitals_folded", "pip_bar", "ft_bar", "oracle_dot", "density",
               "brightness", "frame_zoom")


def merge_shared(p: dict, rec: dict | None) -> dict:
    """The file's prefs with the house record's shared keys laid over them
    (rev ≥ 1 only — a rev-0 record is nobody's truth)."""
    out = dict(p)
    if isinstance(rec, dict) and int(rec.get("rev") or 0) >= 1:
        for k in SHARED_KEYS:
            if k in rec:
                out[k] = rec[k]
    return _clip(out)


# CHARS PER CM², not zoom. 1.85 was the ceiling until 2026-09-15, and it was
# the ceiling twice — the pref clipped here and Geom clamped again, so raising
# one did nothing. Geom reads THESE now.
#
# 5.0 is set from what the User already drives on Brave. There, a WINDOWED tab
# pins the glyph to the monitor (`measure()`'s hole branch: fs = monitor/88*.44)
# while window.innerWidth grows as the tab zooms out, so ctrl-scroll adds
# COLUMNS and leaves the vw-sized HUD alone. Measured against his 2026-09-15
# screenshots on a 1234 px viewport: 25% zoom = 643 cols = 1.92 device px per
# char, which is wall density ~4.0. 5.0 (1.5x2.5 px cells) goes one notch past
# the finest he can reach in Brave.
# Cost on a 2560x1440 glass, measured: 3.0 = 16.9 ms, 4.2 = 24.0, 5.0 = 29.7,
# against a 41.7 ms frame budget — stride stays 1. Past 5 the ramp stops being
# glyphs and becomes dither; the dial does not need to go there.
DENSITY_MIN, DENSITY_MAX = 0.55, 5.0
# THE DIAL PAST 1.0 IS INK, NOT OPACITY (the User 2026-09-22: "do I have a greater
# brightness setting than 1? My dials stop there and it doesn't go very bright
# on the ascii screen at high res").
#
# 1.0 was a real ceiling, not an arbitrary one: the number IS the #ascii layer's
# opacity and opacity stops at opaque. At 1.0 the hottest pixel the screen can
# hold is exactly EMBER #D9A05B — luma 167 of 255 — because the palette
# interpolates GROUND -> EMBER. High density was never the dimmer: measured on a
# frame at 1.0, pixels over luma 100 go 3.06% at density 1.0 to 7.15% at 5.0 and
# the peak is 167 at every one of them. Fine texture, not less light.
#
# So above the point where a glass has no opacity left to give, the number buys
# INK instead: the hot end of the palette walks EMBER -> INK #E9E5DB (luma 167 ->
# 230), reaching INK at BRIGHT_MAX. At and below 1.0 every glass is byte-for-byte
# what it was before this existed.
BRIGHT_MIN, BRIGHT_MAX = 0.22, 1.5
SIZE_MIN, SIZE_MAX = 0.6, 1.9
DENSITY_STEP = 0.05     # AT 1.0 — the dial steps proportionally above it, see nudge()
BRIGHT_STEP = 0.05
# The dial's value IS the wallpaper's #ascii opacity (house_wall_ascii_render
# bakes it into the ember palette). The Brave page cannot use it raw: PIL's
# glyphs need more ink than the browser's to read the same, which is why the
# default sits at .75 here and the page's CSS rule says .62. So the page takes
# the shared number as a RATIO against this reference and scales its own four
# #ascii states by it — at BRIGHT_REF the page is byte-for-byte what it was
# before the dial existed. Shared 2026-09-16; docs/glass-contract.yaml prefs.
BRIGHT_REF = DEFAULTS["brightness"]

# Where each glass runs out of opacity — where ink takes over. They differ
# because the two are calibrated apart (see BRIGHT_REF): the wallpaper bakes the
# number straight into its palette and is opaque at 1.0, while the page scales
# its CSS states by b / BRIGHT_REF, so `body.chan-live #ascii` does not reach
# opacity 1 until 1.21. Starting the ink at 1.0 on both would strand that last
# fifth of the page's burn behind the dial forever.
ASCII_LIVE_OPACITY = 0.62      # body.chan-live #ascii == house_wall_ascii_render.ASCII_OPACITY
BRIGHT_FULL_WALL = 1.0
BRIGHT_FULL_PAGE = round(BRIGHT_REF / ASCII_LIVE_OPACITY, 4)


def ink_gain(brightness: float | None, full: float = BRIGHT_FULL_WALL) -> float:
    """How far the hottest glyph has walked from EMBER to INK: 0 at and below
    `full` (where the layer is already opaque), 1 at BRIGHT_MAX.

    One formula, both glasses — each passes its own `full`. 0.0 means "exactly
    what this glass drew before the dial went past 1"."""
    if brightness is None:
        return 0.0
    try:
        b = float(brightness)
    except (TypeError, ValueError):
        return 0.0
    if BRIGHT_MAX <= full:
        return 0.0
    return max(0.0, min(1.0, (b - full) / (BRIGHT_MAX - full)))
SIZE_STEP = 0.1         # kiosk FT_STEP_BY
FLOURISH_STEP = 5       # percent
HOUR_STEP = 1           # work_start / work_end, wrapping round the clock
# Cadence at the default mix. Keep in lockstep with conductor.WRAPS_BEFORE_FIDGET.
FLOURISH_WRAPS_AT_DEFAULT = 3


def wraps_before_fidget(pct: int | None = None) -> int | None:
    """Rest-loop wraps before a bag fidget. None = never (0%).

    35% → 3 wraps (~28 s on a 5 s loop, the legacy metronome). 100% → fidget
    after one wrap. Speech does not use this: Sequencer.hold_flourishes already
    freezes the bag while the voice is on the air.
    """
    if pct is None:
        pct = DEFAULTS["flourish_pct"]
    try:
        pct = int(round(float(pct)))
    except (TypeError, ValueError):
        pct = DEFAULTS["flourish_pct"]
    pct = max(0, min(100, pct))
    if pct <= 0:
        return None
    return max(1, int(round(FLOURISH_WRAPS_AT_DEFAULT * DEFAULTS["flourish_pct"] / pct)))


def _clip(d: dict) -> dict:
    den = float(d.get("density", DEFAULTS["density"]))
    br = float(d.get("brightness", DEFAULTS["brightness"]))
    size = float(d.get("ft_size", DEFAULTS["ft_size"]))
    mode = str(d.get("ft_mode", "ft")).lower()
    if mode not in ("ft", "room"):
        mode = "ft"
    den = max(DENSITY_MIN, min(DENSITY_MAX, den))
    br = max(BRIGHT_MIN, min(BRIGHT_MAX, br))
    size = max(SIZE_MIN, min(SIZE_MAX, size))
    try:
        fl = int(round(float(d.get("flourish_pct", DEFAULTS["flourish_pct"]))))
    except (TypeError, ValueError):
        fl = DEFAULTS["flourish_pct"]
    fl = max(0, min(100, fl))

    def _hour(k):
        try:
            return int(round(float(d.get(k, DEFAULTS[k])))) % 24
        except (TypeError, ValueError):
            return DEFAULTS[k]
    return {
        "density": round(den, 3),
        "brightness": round(br, 3),
        "blur": bool(d.get("blur", False)),
        "ft_fade": bool(d.get("ft_fade", False)),
        "ft_mode": mode,
        "ft_size": round(size, 2),
        "hole_ar": bool(d.get("hole_ar", True)),
        "oracle_dot": bool(d.get("oracle_dot", False)),
        "folders": bool(d.get("folders", False)),
        "vitals_folded": bool(d.get("vitals_folded", False)),
        "log_board": bool(d.get("log_board", True)),
        "log_room": bool(d.get("log_room", True)),
        "log_tools": bool(d.get("log_tools", True)),
        "pip_bar": bool(d.get("pip_bar", False)),
        "mode_bar": bool(d.get("mode_bar", False)),
        "ft_bar": bool(d.get("ft_bar", False)),
        "dock_bar": bool(d.get("dock_bar", False)),
        "flourish_pct": fl,
        "work_start": _hour("work_start"),
        "work_end": _hour("work_end"),
        "leaves_at_end": bool(d.get("leaves_at_end", False)),
        "ft_tile": _tile(d.get("ft_tile")),
        "frame_zoom": _frame_zoom(d.get("frame_zoom", DEFAULTS["frame_zoom"])),
    }


def _frame_zoom(v) -> float:
    """1.0 to 3.0 in quarter steps (lib/wall_frame.py's own clamp)."""
    import wall_frame
    return wall_frame.clamp_zoom(v)


def _tile(v):
    """[x, y] in 0..1, else None (the corner)."""
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    try:
        return [round(min(1.0, max(0.0, float(v[0]))), 4), round(min(1.0, max(0.0, float(v[1]))), 4)]
    except (TypeError, ValueError):
        return None


def load() -> dict:
    try:
        raw = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return dict(DEFAULTS)
    if not isinstance(raw, dict):
        return dict(DEFAULTS)
    return _clip(raw)


def save(d: dict) -> dict:
    out = _clip(d)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(out), encoding="utf-8")
    tmp.replace(PATH)
    return out


def nudge_step(density: float) -> float:
    """What one click of the RESOLUTION dial is worth at this density."""
    return DENSITY_STEP * max(1.0, float(density))


def nudge(key: str, delta: float, base: dict | None = None) -> dict:
    """Step `key` and save. `base` is what is on screen (merge_shared over the
    house record): a shared key steps from THAT, not from this file's stale copy."""
    if key not in ("density", "brightness", "ft_size", "flourish_pct", "work_start", "work_end"):
        raise KeyError(key)
    p = load()
    if isinstance(base, dict) and key in SHARED_KEYS and key in base:
        p[key] = base[key]
    if key in ("work_start", "work_end"):
        p[key] = (int(p[key]) + (1 if delta >= 0 else -1) * max(1, int(round(abs(float(delta)))))) % 24
        return save(p)
    if key == "density":
        # The range is 9x wide now, so a fixed 0.05 is ~89 clicks end to end and
        # is finest exactly where the eye sees the least — one step at 4.5 is a
        # tenth of the change one step at 0.6 is. Scale the step with where the
        # dial is (2026-09-15): identical to the old behaviour at and below 1.0.
        cur = float(p[key])
        p[key] = cur + (1.0 if delta >= 0 else -1.0) * abs(float(delta)) * max(1.0, cur)
    else:
        p[key] = float(p[key]) + float(delta)
    return save(p)


def nudge_density(delta: float) -> dict:
    return nudge("density", delta)


def nudge_brightness(delta: float) -> dict:
    return nudge("brightness", delta)


def toggle_leaves() -> dict:
    p = load()
    p["leaves_at_end"] = not bool(p.get("leaves_at_end"))
    return save(p)


def work_day(p: dict | None = None) -> dict:
    """{start, end, leaves}: her working day (hours 0-23; an end before the start runs past midnight)."""
    p = load() if p is None else p
    return {"start": int(p.get("work_start", DEFAULTS["work_start"])), "end": int(p.get("work_end", DEFAULTS["work_end"])),
            "leaves": bool(p.get("leaves_at_end", False))}
