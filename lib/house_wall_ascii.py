#!/usr/bin/env python3
"""house_wall_ascii — the wall without a browser (ASCII engine).

Lands BESIDE lib/house_wall.py (the dim-JPEG blit prototype, 023ecb80) and
never replaces it. Entry: scripts/house-wall-ascii ; unit: house-wall-ascii.service.

A Tk window — a desktop-type wallpaper by default, fullscreen only with an
explicit --fullscreen — that shows what display/voiceonly-home.html shows,
drawn by house_wall_ascii_render.py from the same sources the page reads:

    :8100/frame.mjpg     Crissa (736x400, mouth already pasted)   conductor rate
    :8100/frame.jpg      fallback poll if the stream drops
    :8100/v1/state       frame_no — the health signal            2 Hz
    :8099/v1/house       chips / headline / roll / clock         0.5 Hz
    :8099/frame          door camera (PiP + fallback ASCII)      0.7 Hz
    :8191/v1/presence    AUTH / Secure                           0.5 Hz

No Chromium, no CDP, no :9222, no --user-data-dir. If :8100 stops
advancing the wall keeps the last frame and shows FEED STALE; it never
wedges the way a renderer main thread does, and systemd Restart=always
covers a crash. Wallpaper and crissa-display autostart are either/or
(lib/glass_mode.py): wallpaper boot disables Brave autostart and never
kills a live window. Wallpaper off enables+starts the ZOS browser.

AUTH / Secure come from :8191 presence.user (the SoT); house.auth is only an echo.

Kiosk chrome (VO|WALL|KREW|COMP|CP, AR + zoom, Blur/scale/FT-ROOM, fold
carets, ⋯ folders) is painted ON this wallpaper, matching
display/voiceonly-home.html. Empty-desk clicks pass through (GNOME folders). Folders SHOW/HIDE is ⋯ only. No xdg-open.

Wallpaper mode pins _NET_WM_WINDOW_TYPE_DESKTOP (+ BELOW, STICKY, SKIP_TASKBAR)
on the WM frame with xprop before the first map, reads it back after the map
and logs type/y so the journal shows whether Mutter honoured it.

Env:
    HOUSE_WALL_FPS      target frame rate (the house's default 60, matches conductor; a pack's 24, the film's own rate:
                        either way the Wall draws each DISTINCT frame once)
    HOUSE_WALL_REST     a pack's desk Wall rests while nobody watches it (default on in a pack, never on the house);
                        HOUSE_WALL_REST_FPS (2) and HOUSE_WALL_REST_AFTER_S (60) - see REST below
    HOUSE_WALL_SIZE     WxH override (default: the screen)
    HOUSE_WALL_FEED     default the player (house config wall_conductor, else the port table's player)
    HOUSE_WALL_HOUSE    default this machine's home server (house config panel_url); none in a pack
    HOUSE_WALL_INIT     where Init lives: default the house server here, the feed (the conductor) in a pack
    HOUSE_WALL_PRESENCE default this machine's presence server (house config presence_url); the launcher sets the pack lamp
    HOUSE_WALL_OPEN     command to open a URL (default scripts/user-brave)
    HOUSE_WALL_ZOOM     override screen.fov_zoom_crissa for the ASCII hole (e.g. 1.0);
                        unset = follow /v1/house like the kiosk does
    HOUSE_WALL_AR_PLANE off (default) | always | lease
                        write the Hybrid-AR wallpaper dump nobody reads.
                        off = skip (13.8 ms/frame). always = every blit.
                        lease = write only while :8219 /v1/hole.jpg has a taker.
    HOUSE_WALL_TURBO_FLAME  1 (default) | 0  hide the Standard-line pilot light

CLI:
    house-wall-ascii --wallpaper        desktop-type window, below everything (default)
    house-wall-ascii --fullscreen       the glass: dock and top bar hidden (after soak)
    house-wall-ascii --preview out.png  render one frame from the LIVE endpoints
                                        to a PNG and exit (no window)
"""
from __future__ import annotations

import argparse
import io
import collections
import json
import math
import os
import queue
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import crissa_home  # the state directory, resolved once (2026-09-26)
import crissa_egress  # LAN-only when kill-search is on (ultra wall)

import glass_mode
import house_ascii_shm as shm
import house_float as F
import house_wall_ascii_render as R
import wall_film_proc
import doc_placement  # where her picture sits while a document is up (the User 2026-10-09)
import wall_frame  # her film's frame, kiosk | whole, and the zoom about her face (the User 2026-10-12)
START_TS = time.time()   # this process's start: a command file older than this is not a new request
import house_wall_prefs as prefs
import house_wall_dock as dock

# This machine's own services from config/house-machine.json (the User 2026-10-06: machine values in config,
# never code). A pack has no home server or presence server: "" there, and every read of them fails quietly.
def _player_url() -> str:
    try:
        import crissa_ports
        return crissa_ports.url("player")
    except (ImportError, KeyError, AttributeError):
        return "http://127.0.0.1:8100"


FEED = os.environ.get("HOUSE_WALL_FEED") or crissa_home.house_value("wall_conductor") or _player_url()
# THE WALL KEY ON HER TILE (round 26, the User 2026-10-16: "wall on off on render window and no watcher necessary for
# Wall"): a pack's desk Wall draws WALL after BLUR on her tile's bar; it closes this Wall, and her face window carries
# the same key to open it again. Never on the house. Kill: CRISSA_TILE_WALL_KEY=0.
WALL_KEY_ON = (not crissa_home.is_house()
               and os.environ.get("CRISSA_TILE_WALL_KEY", "1").strip().lower() not in ("0", "false", "off", "no"))
HOUSE = os.environ.get("HOUSE_WALL_HOUSE") or crissa_home.house_value("panel_url")
PRESENCE = os.environ.get("HOUSE_WALL_PRESENCE") or crissa_home.house_value("presence_url")
# Init's walker: the house's home server here; in a pack the conductor serves it (lib/crissa_init, 2026-10-05).
INIT = os.environ.get("HOUSE_WALL_INIT") or (HOUSE if crissa_home.is_house() else FEED)
# Register's glass (the sentence to read, the beep, the take): the house's home server here; in a pack the routine
# writes it to the conductor (lib/crissa_init), so the desk Wall reads it there (the User 2026-10-10, .101 V: it read
# the feed's copy, which nothing writes in a pack, and the Voice ID plate never showed the sentence)
REGISTER_SRC = os.environ.get("HOUSE_WALL_REGISTER") or (HOUSE if crissa_home.is_house() else INIT)
# K (the User 2026-10-01): pulse Crissa's dot while she works on his turn - render.thinking_pulse. Default OFF.
THINKING_PULSE = os.environ.get("HOUSE_WALL_THINKING_PULSE", "0").strip().lower() in ("1", "true", "yes", "on")
THINKING_FILE = Path(os.environ.get("HOUSE_THINKING_FILE") or (crissa_home.HOME / "crissa-thinking.json"))
# THE FILM QUEUE (the User 2026-10-10, a test: "Let's test"). The clip's frames arrive in a 3:2 pattern, 14 % of them
# 17 ms after the one before; the film thread kept only the newest, so ~3 a second were overwritten before they were
# drawn (17.4 fps on the glass canary of a 20.8/s feed). With the queue on, the reader keeps the last FILM_QUEUE_N
# distinct frames and the film thread draws them in order on its steady clock: every frame, a frame of delay at
# most. A frame is the whole picture (the conductor has already pasted her mouth). Live switch, read every second:
# the file <state home>/wall-film-queue (live_switch: present = on unless it says off; absent = the default, ON in a
# pack since 2026-10-11, off on the house). HOUSE_WALL_FILM_QUEUE=0 forces it off.
FILM_QUEUE_FILE = crissa_home.HOME / "wall-film-queue"
# EVERY SEMIFRAME ON THE GLASS (the User 2026-10-10: "it is important we capture the two semiframes to one frame as
# that is how the Composer tunes"). The conductor paints her mouth on the semiframe grid (two slots a film frame); the
# glass painted a whole 2560x1440 picture each time (12.7 ms in Tk) and put ~17-19 a second on screen of the 26-29 the
# feed carries while she talks, so the in-between mouth steps were the ones lost. With patches on, the film child
# says what changed since its last picture; a small change (a mouth step) is copied into the glass's picture by
# region (1.1 ms for 360x240), every distinct picture is drawn, on arrival. Anything that might leave the glass out of
# step with the child (a dropped packet, a fallback frame, the AR plane) asks for the whole picture next.
# Live switch: <state home>/wall-film-patch (live_switch: present = on unless it says off; absent = the default, ON in
# a pack since 2026-10-11, off on the house). HOUSE_WALL_FILM_PATCH=0 forces it off.
FILM_PATCH_FILE = crissa_home.HOME / "wall-film-patch"
_FP = {"at": 0.0, "on": False}
# THE PACK DEFAULTS (2026-10-11, the .101 lips; measured under a .101-like CPU budget at 3440x1440,
# docs/checkpoints/wall-speed-101-20261011): a pack patches its mouth steps and queues its frames unless the file says
# off. The house keeps its own files exactly as they were (absent = off; its wall-film-patch says on).
FILM_PATCH_PACK = True
FILM_QUEUE_PACK = True
_OFF_WORDS = ("off", "0", "false", "no")


def live_switch(path: Path, default: bool) -> bool:
    """A live switch file: absent (or unreadable) = `default`; present = on unless its first word is off | 0 | false |
    no. Present-and-on is what these files always meant; the off word turns a pack's default off without a restart."""
    try:
        word = Path(path).read_text(encoding="utf-8").split()
    except OSError:
        return default
    return not (word and word[0].lower() in _OFF_WORDS)


def _pack_default(on_in_pack: bool) -> bool:
    return bool(on_in_pack) and not crissa_home.is_house()


def _film_patch_on() -> bool:
    now = time.time()
    if now - _FP["at"] >= 1.0:
        _FP["at"] = now
        _FP["on"] = (os.environ.get("HOUSE_WALL_FILM_PATCH", "1").strip().lower() not in _OFF_WORDS
                     and live_switch(FILM_PATCH_FILE, _pack_default(FILM_PATCH_PACK)) and AR_PLANE == "off")
    return _FP["on"]


# THE ASCII CACHE (the User 2026-10-11: "no need to paint cached loops ... Free up cores for other things and keeps
# the amazing AR effect"): the film child remembers each frame's coverage by its glyph grid and draws a mouth step
# as a region over a remembered picture, exact against the fresh render (the glow made for that frame), see
# lib/wall_ascii_cache.py. HOUSE_WALL_ASCII_CACHE = on | fallback | off (off is a kill); the live switch file
# <state home>/wall-ascii-cache names the mode while present. `fallback` engages only while the glass falls behind
# the clip (pictures made a second under 95 % of the feed's distinct frames) and lets go after ASCII_CACHE_HOLD_S
# once a whole render fits the clip's period again.
ASCII_CACHE_HOLD_S = 60.0

# round 41 (.101 round 40 F7): what the desk Wall says when an Add/Edit IDs run is cancelled (wizard_worker)
IDS_CANCELLED_NOTE = "Add/Edit IDs cancelled - nothing saved"
_AC = {"at": 0.0, "mode": "off"}


def _ascii_cache_mode() -> str:
    now = time.time()
    if now - _AC["at"] >= 1.0:
        _AC["at"] = now
        try:
            import wall_ascii_cache
            _AC["mode"] = wall_ascii_cache.mode()
        except Exception:  # noqa: BLE001 - no cache module, no numpy: the film is drawn as before
            _AC["mode"] = "off"
    return _AC["mode"]


FILM_QUEUE_N = 3
_FQ = {"at": 0.0, "on": False}
# A QUEUE THAT FALLS BEHIND DROPS, IT DOES NOT LAG (2026-10-11, measured under a .101-like CPU budget at 3440x1440
# d2.08): behind whole renders of 42 ms the queue held her lips 147 ms behind the feed (p50) where the newest-frame
# path held them 100 ms behind. A queued frame older than this gives way to the next one; the newest is always drawn.
# HOUSE_WALL_FILM_QUEUE_LAG_MS=0 = no bound (the queue as it was).
try:
    FILM_QUEUE_LAG_S = max(0.0, float(os.environ.get("HOUSE_WALL_FILM_QUEUE_LAG_MS", "75"))) / 1000.0
except ValueError:
    FILM_QUEUE_LAG_S = 0.075

# EVERY PICTURE IS DRAWN INTO THE WINDOW WHEN IT IS PASTED (round 15, 2026-10-12, .101's frozen build: the speed line
# said 21-22/s "on the glass" while the User saw one frame, then the next, then stuck). A paste only fills the layer's
# PhotoImage; Tk draws the canvas into the window later, as an IDLE callback, and Tcl runs idle callbacks only on a
# pass of its loop that finds no other event. The pump re-arms itself every 1 ms and every pass of tkinter's loop takes
# the GIL back from the Wall's other threads, so whenever the Wall is busy (its first lap, a build beside it, a line
# starting) the 1 ms timer has always fired again and the redraw waits - several pastes collapse into one picture, or
# none. Measured on a .101-like box (8 cores + SMT siblings, presence-like neighbours, Xvfb 3440x1440, density 5) with
# glasssample.py reading the screen: the Wall said 19-24/s and the screen had 4.5-13/s, stills up to 1 s, while the
# X server answered in under 2 ms. Now each paste is followed by update_idletasks(): the redraw is part of the paste.
# HOUSE_WALL_DRAW_NOW=0 is the old way (the speed line then counts the redraws idle actually ran).
# THESE ROUND-15 SWITCHES ARE A PACK'S DEFAULT: unset = on in a pack, off on the house (whose desk Wall stays exactly
# as it was until someone sets them); "1" / "0" decide anywhere.


def _pack_switch(name: str) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return not crissa_home.is_house()


DRAW_NOW = _pack_switch("HOUSE_WALL_DRAW_NOW")
# THE PERSON BEFORE THE PICTURE (round 21, .101 round 20: "The Crissa menu needs several clicks", the Wall at 100 % CPU
# at density 4). The pump drained up to four whole pictures a pass, each a 3440x1440 paste and a forced redraw, before
# Tk read anything else: the pointer's motion (a polled flag in wallpaper mode, turned into the hover band and the
# click region only by on_idle's 120 ms timer) and the clicks waited behind them, so the band and the region the clicks
# land in trailed the hand and a click went through to the desktop. Now, before each picture, the pump hands Tk its
# pending window events (clicks, keys, motion over the glass; never timers, so the pump cannot re-enter) and turns a
# polled motion into on_motion at once; while the hand is busy (input in the last INPUT_FIRST_S) it puts up one picture
# a pass. A pack's default; HOUSE_WALL_INPUT_FIRST=0 is the old pump.
INPUT_FIRST = _pack_switch("HOUSE_WALL_INPUT_FIRST")
INPUT_FIRST_S = float(os.environ.get("HOUSE_WALL_INPUT_FIRST_S", "0.5"))
INPUT_FIRST_MAX = 32                       # window events handed over before one picture, at most
# THE SPEED LINE SAYS WHAT WAS DRAWN (round 15): "N/s drawn" - the redraws that reached the window - and, once a
# second, how long the X server took to answer (x). Off = the round-12 line ("on the glass", the pastes) and no probe.
DRAWN_LINE = _pack_switch("HOUSE_WALL_DRAWN_LINE")

# THE SOFT LAYERS GO UP IN PIECES (round 15, 2026-10-12): the HUD, the roll and the chrome are whole-glass RGBA pictures
# with soft edges, and Tk draws a photo that has partial alpha by READING THE WINDOW BACK from the X server
# (XGetImage), blending in software and writing it again - for the whole item, on every redraw the film causes.
# Measured on Xvfb at 3440x1440 (redrawbench.py): a film-only redraw 5.1 ms, under three such layers 64.7 ms, the
# same layers cut into the pieces they draw 10.3 ms (on Xwayland the read-back comes from the GPU). Each of these
# layers is now one canvas item per drawn piece (apart where PIECE_GAP px of nothing runs across), each cropped to
# what it draws; the whole picture stays in last_im for the readers of it. HOUSE_WALL_LAYER_PIECES=0 is one item.
LAYER_PIECES = _pack_switch("HOUSE_WALL_LAYER_PIECES")
PIECE_KEYS = ("hud", "roll", "chrome")
# round 16 (2026-10-13, .101's Setup: the Wall drew 5-10/s with redraws of 60-130 ms while Setup's plate was up, and
# Lila's welcome lost its lips): Setup's plate and Voice ID's are whole-glass soft layers too, placed where the pump
# says; they go up in pieces the same way. Kill: HOUSE_WALL_LAYER_PIECES_SETUP=0.
PIECE_KEYS_PLACED = ("init", "register") if str(os.environ.get("HOUSE_WALL_LAYER_PIECES_SETUP", "1")).strip().lower() \
    not in ("0", "false", "no", "off") else ()
PIECE_CELL = 8                 # px: the alpha is looked at in cells this size
PIECE_GAP = 6                  # cells of nothing that part two pieces (48 px)
_ANY = [0] + [255] * 255


def _runs(v, gap: int) -> list:
    """Runs of True in v (1-D) as (start, end), merging runs that fewer than gap False cells part."""
    idx = [i for i, x in enumerate(v) if x]
    if not idx:
        return []
    out, s, last = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - last > gap:
            out.append((s, last + 1))
            s = i
        last = i
    out.append((s, last + 1))
    return out


def layer_pieces(im, cell: int = PIECE_CELL, gap: int = PIECE_GAP) -> list:
    """[(x, y, crop)]: the parts of an RGBA layer that draw anything, each cropped to its own alpha bbox. Bands of rows
    first, then columns inside each band, so four corners are four pieces, not one glass-sized box."""
    import numpy as np
    if im.mode != "RGBA":
        return [(0, 0, im)]
    W_, H_ = im.size
    a = im.getchannel("A").point(_ANY)
    m = np.asarray(a.reduce(cell)) > 0
    out = []
    for y0, y1 in _runs(m.any(axis=1), gap):
        for x0, x1 in _runs(m[y0:y1].any(axis=0), gap):
            box = (x0 * cell, y0 * cell, min(W_, x1 * cell), min(H_, y1 * cell))
            sub = im.crop(box)
            bb = sub.getbbox()
            if bb:
                out.append((box[0] + bb[0], box[1] + bb[1], sub.crop(bb)))
    return out


# THE MACHINE CARD'S DENSITY, TAKEN LIVE (round 16, 2026-10-13, .101's fresh install: the card picked 3.0 at 00:23 and
# the glass stayed at 1.0 - its post went to an address a pack does not have): a pick in <state home>/density-pick.json
# made after this Wall started is posted to the shared glass record by the Wall itself, once (lib/crissa_machine
# apply_density decided the person had not set one). The new density is a new look, so the disk tier stops the build of
# the old one and starts this one's at once. Pack only. Kill: HOUSE_WALL_TAKE_PICK=0.
TAKE_PICK = str(os.environ.get("HOUSE_WALL_TAKE_PICK", "1")).strip().lower() not in ("0", "false", "no", "off")
WALL_T0 = time.time()


def pick_to_take(home: Path, taken: float, t0: float = WALL_T0) -> tuple[float, float, str] | None:
    """(density, its ts, who picked it) of a pick newer than both this Wall's start and the last one taken, else None.
    Who: "the machine card", or "the first look" for this Wall's own (FIRST_LOOK below)."""
    try:
        d = json.loads((Path(home) / "density-pick.json").read_text(encoding="utf-8"))
        ts, den = float(d.get("ts") or 0.0), float(d["density"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if ts <= max(t0, taken):
        return None
    return den, ts, ("the first look" if d.get("by") == "wall" else "the machine card")


# THE FIRST GLASS AT THE MACHINE'S DENSITY (round 29, .101 2026-10-18: 334x86 at 1.0 for the first two minutes of Setup,
# "ASCII on Init (very first impression) not hi res"): before the first glass a pack's Wall asks the card's own rule for
# its width (lib/crissa_machine.first_look: density_floor one step finer, the person's density still wins, only while no
# pick is on file) and draws its first picture there; the card's measured pick replaces it live as before (TAKE_PICK),
# and the live take posts it to the shared record. Kill: CRISSA_DENSITY_FIRST_LOOK=0.
def first_look(W: int, H: int) -> float | None:
    """The density the first glass takes, else None (and why, logged)."""
    if crissa_home.is_house():
        return None
    try:
        import crissa_machine
        out = crissa_machine.first_look(W, H)
    except Exception as e:  # noqa: BLE001 - the Wall's own density stands
        log(f"density: first look failed: {type(e).__name__}: {e}"[:160])
        return None
    if not out.get("applied"):
        if out.get("why") not in (None, "off", "a pick is on file"):
            log("density: first look: %s" % out.get("why"))
        return None
    log("density: first look %.2f on %dx%d (%s)" % (float(out["density"]), W, H, out.get("why") or "the card's rule"))
    return float(out["density"])

# THE PAINT TRACE (round 12, 2026-10-12, the .101 mouth rate): HOUSE_WALL_PAINT_TRACE=<file> appends one JSON row per
# film picture (the film thread: arrival, start, end, what the child drew, the queue) and per glass paint (the Tk
# thread: whole or patch, its ms). Off when unset; nothing else reads it.
PAINT_TRACE = os.environ.get("HOUSE_WALL_PAINT_TRACE", "").strip()
_PT: dict = {"f": None, "lock": threading.Lock()}


# PER LAP, WHERE THE PICTURES CAME FROM (round 13, 2026-10-12): one line when a clip's frames wrap or the clip changes,
# from X-Clip: memory (the cache's bytes, grid or region), disk (the disk tier's picture, or its base for a region) or
# a fresh render. The first lap after a start is the one that writes the disk.
LAP_SRC = {"bytes": "mem", "grid": "mem", "region": "mem", "disk": "disk", "disk-region": "disk",
           "full": "fresh", "today": "fresh"}


# A LAP IS AT MOST TWO CLIP LENGTHS OF PICTURES (round 33, .101 round 31 06:15: "listen 13300 pictures in 6594.4 s" - one
# lap that did not close for 1.8 hours, because her frame never went back, and the Building plate read that lap's whole
# span as one jumpy lap: "196 of the last 13300 pictures drawn fresh"). A lap now also closes after LAP_CAP_X times
# the clip's frames in pictures. A lap she speaks through holds about 1.2 times its frames (the mouth's semiframe
# steps), so an ordinary lap never meets the cap. Unset = on in a pack, off on the house. Kill HOUSE_WALL_LAP_CAP=0.
LAP_CAP_X = 2


def lap_cap_on(env=None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_WALL_LAP_CAP", "")).strip().lower()
    if raw:
        return raw not in ("0", "false", "off", "no")
    return not crissa_home.is_house()


# round 40 N16: the film child's cache, as its last counts said (off and why; at its memory cap) - for the lap record
_CACHE_NOTE = {"dead": "", "full": False}


def _cache_note(cs: dict) -> None:
    try:
        cap = float(cs.get("cap") or 0)
        _CACHE_NOTE.update(dead=str(cs.get("dead") or ""),
                           full=bool(cap > 0 and float(cs.get("bytes_held") or 0) >= 0.97 * cap))
    except (TypeError, ValueError):
        pass


def _lap_count(lap: dict, clip, src: str, now: float) -> None:
    if not (isinstance(clip, tuple) and len(clip) == 4):
        return
    key, i = clip[0], int(clip[1])
    long_lap = lap["clip"] == key and lap.get("frames", 0) > 0 and lap["n"] >= LAP_CAP_X * lap["frames"] \
        and lap_cap_on()
    if lap["clip"] is not None and (key != lap["clip"] or i < lap["last"] or long_lap):
        if lap["n"]:
            log("ascii lap: %s %d pictures in %.1f s: memory %d, disk %d, fresh %d (%d frames in the clip)"
                % (lap["clip"], lap["n"], now - lap["t0"], lap["mem"], lap["disk"], lap["fresh"], lap.get("frames", 0)))
            try:                                  # round 29: the Building plate reads the newest lap (lib/crissa_build)
                import crissa_build
                crissa_build.note_lap(lap["clip"], lap["n"], lap["fresh"], now - lap["t0"],
                                      skipped=max(0, lap["last"] - lap.get("first", lap["last"]) + 1
                                                  - len(lap.get("seen") or ())),
                                      cache_off=_CACHE_NOTE["dead"], full=_CACHE_NOTE["full"])
            except Exception:  # noqa: BLE001
                pass
        lap.update(n=0, mem=0, disk=0, fresh=0, t0=now)
    if lap["clip"] != key or lap["n"] == 0:
        lap["t0"] = lap["t0"] if lap["n"] else now
        lap.update(first=i, seen=set())          # .101 10-19: which film frames the lap showed (its skips: the plate)
    lap.setdefault("seen", set()).add(i)
    lap.update(clip=key, last=i, frames=int(clip[2]))
    lap["n"] += 1
    lap[src] = lap.get(src, 0) + 1


def paint_trace(row: dict) -> None:
    if not PAINT_TRACE:
        return
    try:
        with _PT["lock"]:
            if _PT["f"] is None:
                _PT["f"] = open(PAINT_TRACE, "a", buffering=1)
            _PT["f"].write(json.dumps(row) + "\n")
    except (OSError, TypeError, ValueError):
        pass

# HER MOUTH DOES NOT SET THE EXPOSURE (2026-10-11, the .101 lips): the conductor names each frame's mouth box
# (X-Mouth-Box, the film frame's own sidecar box) and the film's exposure leaves that box's cells out
# (house_wall_ascii_render.norm_cells), so a mouth step changes only the cells around her mouth - a patch on the glass
# and a region in the ASCII cache on every loop, not only where the mouth sits below the focus box (on Lila's loops a
# mouth step today flips ~4 % of the pixels over ~83 % of the glass). The picture changes against today where her
# mouth sits in the focus box: none of Crissa's four core loops, 3.5-12 % of the pixels on Lila's three, median 1 level
# (the exposure is read without her mouth; docs/checkpoints/wall-speed-101-20261011).
# HOUSE_WALL_NORM_MOUTH = 1 | 0; unset = on in a pack, off on the house. The live switch file
# <state home>/wall-norm-mouth, while present, names it (on | off; empty = on). A frame without the header (an
# older conductor, a camera cut, a frame with no mouth in shot) is today's exposure either way.
NORM_MOUTH_FILE = crissa_home.HOME / "wall-norm-mouth"
_NM = {"at": 0.0, "on": False}


def norm_mouth_default(env=None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_WALL_NORM_MOUTH", "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return not crissa_home.is_house()


def _norm_mouth_on() -> bool:
    now = time.time()
    if now - _NM["at"] >= 1.0:
        _NM["at"] = now
        killed = str(os.environ.get("HOUSE_WALL_NORM_MOUTH", "")).strip().lower() in _OFF_WORDS
        _NM["on"] = False if killed else live_switch(NORM_MOUTH_FILE, norm_mouth_default())
    return _NM["on"]


def _film_queue_on() -> bool:
    now = time.time()
    if now - _FQ["at"] >= 1.0:
        _FQ["at"] = now
        _FQ["on"] = (os.environ.get("HOUSE_WALL_FILM_QUEUE", "1").strip().lower() not in _OFF_WORDS
                     and live_switch(FILM_QUEUE_FILE, _pack_default(FILM_QUEUE_PACK)))
    return _FQ["on"]
CHIP = os.environ.get("HOUSE_CHIP") or crissa_home.house_value("chip_url")
# Hybrid-AR plane: a full-frame RGB dump for house_chip /v1/hole.jpg.
# Default off — no unit, no journal hits (Claude 2026-09-15, 13.8 ms/frame).
AR_PLANE = os.environ.get("HOUSE_WALL_AR_PLANE", "off").strip().lower() or "off"
AR_PLANE_WANT = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "house-ascii.bg.want"


def _ar_plane_leased() -> bool:
    """house_chip (or a test) touches this file; 5 s TTL."""
    try:
        return (time.time() - AR_PLANE_WANT.stat().st_mtime) < 5.0
    except OSError:
        return False
# GNOME desktop icons = the wall's FOLDERS. Stock DING lowers itself under
# the wall at every click on the desktop; a house copy (HOUSE_WALL_DING_UUID,
# installed by Init) does not. The shell registers a new extension only when
# it starts, so the copy is usable only after the login that follows its
# install — until then stock it is.
DING_STOCK_UUID = "ding@rastersoft.com"
DING_HOUSE_UUID = ((os.environ.get("HOUSE_WALL_DING_UUID") or "").strip()
                   or crissa_home.house_value("wall_ding_uuid") or DING_STOCK_UUID)
DING_UUID = DING_STOCK_UUID     # resolved by ding_uuid() at each use


def ding_uuid() -> str:
    """The house copy when gnome-shell knows it, else stock. Never a uuid the
    shell has not registered: enabling that is "does not exist" and no icons."""
    global DING_UUID
    try:
        r = subprocess.run(["gnome-extensions", "info", DING_HOUSE_UUID], capture_output=True, text=True, timeout=6)
        DING_UUID = DING_HOUSE_UUID if (r.returncode == 0 and "State:" in r.stdout) else DING_STOCK_UUID
    except Exception:
        DING_UUID = DING_STOCK_UUID
    return DING_UUID
STALE_S = 8.0            # frame_no unchanged this long = stale (the doc's discriminator)


# THE PACK'S DESK WALL DRAWS ONLY DISTINCT FRAMES, AND RESTS WHILE NOBODY WATCHES (the User 2026-10-10, .101 round 4 AA:
# the Wall at 139 % with nobody at the camera, its glass-up line saying fps=60.0, against a 2-core idle budget).
# Watched, it draws each distinct film frame once, as the house's does (~21-24 a second, the clip's own rate; never 60):
# a pack's default HOUSE_WALL_FPS is the clip's 24. RESTING - nothing on air, no Init or Voice ID on the glass, no
# input on this desktop and no face or motion at the camera for REST_AFTER_S - it reads the player's newest frame
# REST_FPS times a second with ?rest=1 (a read that does not count as someone looking, so the player rests the house's
# way too, scripts/house-conductor.py look_idle) and draws only those. The first input, face, motion, her voice or an
# Init step brings the full rate back within half a second. The house's Wall never rests (it IS the glass).
# Kill: HOUSE_WALL_REST=0.
def _env_f(name: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(os.environ.get(name, "") or default)))
    except (TypeError, ValueError):
        return default


# ROUND 23 (the User 2026-10-15, .101: "let's try to paint at 48. we tune in semiframes for that reason"; kept as the
# pack default): a pack asks for 48, one picture per semiframe. Measured on .101 it still draws ~24 distinct a second
# (it draws a new film frame, not a new mouth row: owed). HOUSE_WALL_FPS=24 is round 22.
WALL_FPS_DEFAULT = "60" if crissa_home.is_house() else "48"
READ1 = os.environ.get("HOUSE_WALL_READ1", "1").strip().lower() not in ("0", "false", "no", "off")
REST_ON = (not crissa_home.is_house()
           and os.environ.get("HOUSE_WALL_REST", "1").strip().lower() not in ("0", "false", "no", "off"))
REST_FPS = _env_f("HOUSE_WALL_REST_FPS", 2.0, 0.5, 12.0)
# THE HUD'S ANIMATION RATE (2026-10-16, .101 round 22: the HUD costs 32-36 ms a paint there and repaints 30 times a second
# while her oracle core animates - in Python, beside the frame loop - just as she starts to speak, when the Wall fell to
# 9.5/s). A pack animates it at 12/s; the house keeps 30. HOUSE_WALL_HUD_ANIM_HZ sets it (2-30). The speed line counts
# the HUD's paints a second (`hud=<ms> x<n>/s`) so the cost is measured, not guessed.
HUD_ANIM_HZ = _env_f("HOUSE_WALL_HUD_ANIM_HZ", 30.0 if crissa_home.is_house() else 12.0, 2.0, 30.0)
REST_AFTER_S = _env_f("HOUSE_WALL_REST_AFTER_S", 60.0, 5.0, 3600.0)
REST: dict = {"on": False, "why": "starting", "since": 0.0, "input_at": time.time(), "door": {}, "still_at": 0.0}
# CLOSE AGENT SHOWS ON THE GLASS (round 15, .101 13:37: "just closed agent, nothing happened" - the film looped on):
# while her agent is closed (the Crissa key's view of the player's menu, lib/crissa_wall_key.agent_closed) this Wall
# rests on a still - one picture every 1/AGENT_STILL_FPS s - drawn at AGENT_DIM of its brightness, and the key's plate
# says "Agent closed · Open Agent". Never on the house. Kill: CRISSA_AGENT_CLOSED_LOOK=0.
AGENT_STILL_FPS = _env_f("HOUSE_WALL_AGENT_FPS", 0.2, 0.05, 2.0)
AGENT_DIM = _env_f("HOUSE_WALL_AGENT_DIM", 0.4, 0.1, 1.0)


def agent_closed_look() -> bool:
    """Her agent is closed and this Wall shows it (a pack, the look switch on)."""
    if crissa_home.is_house() or os.environ.get("CRISSA_AGENT_CLOSED_LOOK", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    try:
        import crissa_wall_key
        return bool(crissa_wall_key.agent_closed())
    except Exception:  # noqa: BLE001  no key here: nothing to show
        return False


def agent_dim() -> float:
    return AGENT_DIM if agent_closed_look() else 1.0


def mark_input() -> None:
    """Input on this desktop (a Tk event, or the idle monitor reset): the Wall is watched; a rest ends now."""
    REST["input_at"] = time.time()


def rest_verdict(now: float, input_at: float, door: dict | None, on_air: bool, busy: str,
                 after_s: float = REST_AFTER_S) -> tuple:
    """(resting, why). Pure: the tests read it. `door` is presence's door_rest ({since_face_s, since_motion_s}); none
    (no camera, presence not answering) leaves the input alone to decide."""
    if busy:
        return False, busy
    if on_air:
        return False, "she is on air"
    quiet = now - float(input_at or 0.0)
    if quiet < after_s:
        return False, "input %.0f s ago" % quiet
    d = door if isinstance(door, dict) else {}
    for key, what in (("since_face_s", "a face"), ("since_motion_s", "motion")):
        try:
            v = float(d[key]) if d.get(key) is not None else None
        except (TypeError, ValueError):
            v = None
        if v is not None and v < after_s:
            return False, "%s at the camera %.0f s ago" % (what, v)
    seen = "no face or motion at the camera" if d else "no camera to ask"
    return True, "nobody watching: no input for %.0f s, %s" % (quiet, seen)
LOG_EVERY_S = 60.0       # heartbeat line in the journal

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def log(msg: str):
    print(f"house-wall-ascii: {msg}", file=sys.stderr, flush=True)


# systemd's watchdog ping (sd_notify WATCHDOG=1). The drop-in's WatchdogSec=30
# only bites when systemd is watching; a manual run and the test bench leave
# NOTIFY_SOCKET unset, so this is a no-op there. The pump pings every
# WATCHDOG_PING_S of REAL time (not tick count): 15 s pings sit a 2x margin
# under the 30 s watchdog, and a pump that is stuck stops pinging, so systemd
# sees the hang instead of a healthy session being killed.
WATCHDOG_PING_S = 15.0


def _sd_watchdog_address() -> str | None:
    """NOTIFY_SOCKET as a connectable AF_UNIX address, or None when unset.

    A leading '@' is systemd's abstract socket namespace: the kernel name
    starts with a NUL byte, so '@foo' becomes '\\0foo'."""
    s = os.environ.get("NOTIFY_SOCKET")
    if not s:
        return None
    if s.startswith("@"):
        return "\0" + s[1:]
    return s


def _sd_watchdog() -> bool:
    """Ping systemd's watchdog (WATCHDOG=1). True when it was sent.

    A no-op (False) when NOTIFY_SOCKET is unset. A lost ping is a dropped
    frame, not a crash: the next pump pass pings again, so a one-off OSError
    never takes the wall down."""
    addr = _sd_watchdog_address()
    if addr is None:
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(b"WATCHDOG=1", addr)
        return True
    except OSError as e:
        log(f"watchdog ping: {type(e).__name__}: {e}"[:160])
        return False


def _watchdog_due(wdog: dict, now: float) -> bool:
    """True on the pass that should ping systemd: WATCHDOG_PING_S of REAL time
    (not tick count) since the last ping. Moves wdog['at'] to now when due, so
    the pump is the single clock. A pump stuck between passes stops tripping
    this, and that silence — not a healthy session — is what systemd catches."""
    if now - wdog["at"] >= WATCHDOG_PING_S:
        wdog["at"] = now
        return True
    return False


# Chrome idle. Kiosk CHROME_IDLE=2000; the User 2026-09-06 asked for the GNOME
# bar and this rail to fold in series, the bar at 3.0 s and the rail at 3.1.
CHROME_IDLE_S = 1.0       # 3.1 until 2026-09-08 ("Turn fade of all down to 1sec", the User)
CHROME_FADE_S = 0.45      # the collapse buttons, rail and dials fade as one (the User 2026-09-06)
# How long a pointer position stays "on" a control after the last Motion. The
# X server sends LeaveNotify when the pointer steps off the input shape (the
# chrome hits), and that clears it at once — this is the fallback for a Leave
# that never arrives, not the hover's clock. 2.0 s used to drop the dial's
# hover and the tile's caret while the hand was still resting on them.
PTR_HOLD_S = 12.0

# THE BAND OVER THE WINDOWS (the User 2026-09-11: "can we make the nav bar and
# folders popup when mouseover on every window? Only works when desktop
# clear"). The wall is a TYPE_DESKTOP window under everything, so with a
# window over the bottom band the pointer never reaches it (house-pointer
# says "window" and chrome_should_wake is right to say no). A DOCK-type
# strip HOT_STRIP_PX tall along the bottom edge stays above every normal
# window (Mutter keeps docks in the top layer; a fullscreen window still
# covers it). Entering the strip raises the POP: a second dock window over
# the band's rect, painted with the same pixels the wall would show there
# (film + HUD + chrome), so on a clear desktop it is invisible and over a
# window it is the rail and the folders. Its motion, clicks and drags
# route into the wall's own handlers with the coordinates translated.
HOT_STRIP = os.environ.get("HOUSE_WALL_HOT_STRIP", "1") != "0"
HOT_STRIP_PX = max(1, int(os.environ.get("HOUSE_WALL_HOT_STRIP_PX", "3")))
HOT_POP_LINGER_S = 1.2        # the band stays this long after the pointer leaves it (the bar's own linger)
HOT_POP_NAMES = ("rail_plate", "dock_plate", "dock_fold", "dock_pick_box", "menu", "menu_panel")
HOT_POP_MARGIN = (16, 30)     # x, y (the tips ride above the keys)


# THE PRESS THAT WOKE THE CHROME IS NOT A PRESS ON A KEY. The arm used to
# swallow EVERY click for 0.4 s after the chrome came up — silently. the User
# 2026-09-11: "i need to double click icon to front a window": he reached the
# dock and pressed inside that window, the press vanished with no log line,
# the second one raised. Now only a press that has not moved from where the
# chrome woke is the waking press; a press that travelled to a key is meant.
CLICK_ARM_S = 0.4
CLICK_ARM_SLOP_PX = 8


def click_is_wake(armed_until: float, armed_xy, x: float, y: float, now: float) -> bool:
    """True when this press is the one that woke the chrome (swallow it)."""
    if now >= float(armed_until or 0.0):
        return False
    if not armed_xy:
        return True          # woke without coordinates (Mutter's idle monitor): the old rule
    return abs(float(x) - float(armed_xy[0])) <= CLICK_ARM_SLOP_PX and abs(float(y) - float(armed_xy[1])) <= CLICK_ARM_SLOP_PX


# THE CHOOSER HOLDS A BEAT. The dock's window stack closed the instant the
# pointer was not seen on the tile or the stack — and "not seen" included a
# pointer the band had just lost to its own resize. PICK_HOLD_S is the beat
# for a pointer seen elsewhere (jitter across the seam), PICK_HOLD_LOST_S for
# a pointer we have no position for at all.
PICK_HOLD_S = 0.25
PICK_HOLD_LOST_S = 0.6


def hot_pop_rect(hits: dict, W: int, H: int, fallback_top: int) -> tuple[int, int, int, int]:
    """The pop's rect on the glass: the union of the band's chrome hits
    (rail plate, dock plate, fold handle, the window chooser, Advanced),
    padded, and always down to the bottom edge so it joins the strip. With
    no band drawn yet (chrome idle, no hits) the fallback band."""
    x0 = y0 = None
    x1 = y1 = None
    for n, box in (hits or {}).items():
        if n in HOT_POP_NAMES or n.startswith("dockwin_"):
            bx0, by0, bx1, by1 = (int(v) for v in box)
            x0 = bx0 if x0 is None else min(x0, bx0)
            y0 = by0 if y0 is None else min(y0, by0)
            x1 = bx1 if x1 is None else max(x1, bx1)
            y1 = by1 if y1 is None else max(y1, by1)
    if x0 is None:
        return (int(W * 0.25), int(fallback_top), int(W * 0.75), int(H))
    mx, my = HOT_POP_MARGIN
    return (max(0, x0 - mx), max(0, y0 - my), min(W, x1 + mx), int(H))


def zos_active() -> bool:
    """Is the ZOS browser (crissa-display.service) up?"""
    try:
        out = subprocess.run(["systemctl", "--user", "is-active", "crissa-display"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return False
    return out == "active"


def mutter_idle_ms() -> int | None:
    """User-input idle time from Mutter, in ms; None if the bus is not there."""
    try:
        out = subprocess.run(
            ["busctl", "--user", "call", "org.gnome.Mutter.IdleMonitor",
             "/org/gnome/Mutter/IdleMonitor/Core", "org.gnome.Mutter.IdleMonitor", "GetIdletime"],
            capture_output=True, text=True, timeout=0.6).stdout.split()
    except Exception:
        return None
    if len(out) == 2 and out[0] == "t":
        try:
            return int(out[1])
        except ValueError:
            return None
    return None


def pointer_under() -> str | None:
    """What the mouse is over, from the wall's pointer shell extension
    (HOUSE_WALL_POINTER_UUID / Init): "desktop", "window" or "shell"; None when
    the extension is not live (before its first login)."""
    try:
        out = subprocess.run(
            ["busctl", "--user", "call", "org.house.Pointer", "/org/house/Pointer",
             "org.house.Pointer", "Under"],
            capture_output=True, text=True, timeout=0.6)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return parse_under(out.stdout)


def parse_under(stdout: str) -> str | None:
    """busctl prints the reply's SIGNATURE once, then the values:
    `ss "window" "org.gnome.Ptyxis"`. The first cut looked for a lone "s"
    per value and never matched, so the wall took the extension for absent
    and woke on every motion (the User 2026-09-08: "I lost no mouse movement if
    on another layer") — the first day the extension was actually live."""
    try:
        parts = shlex.split(stdout or "")
    except ValueError:
        return None
    if len(parts) == 3 and parts[0] == "ss":
        return parts[1]
    return None


def chrome_should_wake(under: str | None, x_moved: bool = False) -> bool:
    """Does this motion wake the chrome? the User 2026-09-08: "unhide only when
    the mouse is moved over the desktop. Not the browser or any window other
    than desktop". With the shell extension live, only "desktop". Without it
    (before its first login) every motion wakes, as it always did. The X
    pointer was tried as a stand-in for one run (04:48-05:00) and it
    suppressed motion over the wall's own chrome and the enlarged camera:
    hover came up eight minutes after start instead of seconds, the bars fell
    asleep under the pointer and a click needed three tries. Not a stand-in."""
    if under is not None:
        return under == "desktop"
    return True


# The wall's own windows as the shell names them: the glass (desktop type, no
# class) and Tk's band pop over the windows ("!toplevel2", "Toplevel").
OWN_WM_CLASSES = ("", "toplevel", "tk")


def input_elsewhere(pl: dict | None) -> bool:
    """POPUPS GO ON ANY INPUT ELSEWHERE (the User 2026-09-14: "make the Wall Adv menu
    disappear on ANY click elsewhere … same with all the popups. They are
    annoying this way"). A click in another window never reaches the wall, so
    the house-shell Poll decides: input happened (idle reset, the caller's job)
    with the pointer over anything that is not the wall's own glass or band —
    another app's window, the desktop folders, the GNOME bar."""
    if not pl:
        return False
    if pl.get("under") == "shell":
        return True
    return str(pl.get("wm_class") or "").strip().lower() not in OWN_WM_CLASSES


def menu_one_close_on() -> bool:
    """Round 40 ("menu wont close"): one close for the menu and its band; the dock key closes what is open; Esc closes.
    Kill HOUSE_WALL_MENU_ONE_CLOSE=0 (round 39's rules)."""
    return os.environ.get("HOUSE_WALL_MENU_ONE_CLOSE", "1").strip().lower() not in ("0", "off", "false", "no")


def menu_pointer_away(pl: dict | None, at: float, now: float, max_age_s: float = 1.0) -> bool:
    """The dwell rule's second witness: the shell poll's last answer, if it is
    fresh, says the pointer is over another window, the folders or the bar
    (`input_elsewhere`). Stale or absent = no opinion (False). Kill
    HOUSE_WALL_MENU_AWAY=0."""
    if os.environ.get("HOUSE_WALL_MENU_AWAY", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    if not pl or now - float(at or 0.0) > max_age_s:
        return False
    return input_elsewhere(pl)


def _ease_page(t: float, x1: float = 0.2, y1: float = 0.8, x2: float = 0.2, y2: float = 1.0) -> float:
    """cubic-bezier(.2,.8,.2,1) — the page's curve for the rail slide and the
    .32 group (#rail transition, body:not(.chrome) opacity). t in 0..1."""
    t = min(1.0, max(0.0, float(t)))
    if t <= 0.0 or t >= 1.0:
        return t
    # solve x(u) = t for u (Newton, then bisection), return y(u)
    def bx(u):
        return 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u * u * x2 + u ** 3
    def by(u):
        return 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u * u * y2 + u ** 3
    u = t
    for _ in range(8):
        dx = 3 * (1 - u) ** 2 * x1 + 6 * (1 - u) * u * (x2 - x1) + 3 * u * u * (1 - x2)
        if abs(dx) < 1e-6:
            break
        u -= (bx(u) - t) / dx
        u = min(1.0, max(0.0, u))
    if abs(bx(u) - t) > 1e-4:
        lo, hi = 0.0, 1.0
        for _ in range(40):
            u = (lo + hi) / 2
            if bx(u) < t:
                lo = u
            else:
                hi = u
    return min(1.0, max(0.0, by(u)))


# THE CORNERS PEEL (the User 2026-09-09: "have the Crissa and Camera windows peel
# off like you now have the folders doing? Slower corner pull up and then
# back down to its corner"): the house-shell's window sweep — 400 ms
# EASE_OUT_EXPO, a fast lift out of the corner that settles — both ways,
# anchored at the corner. The page's #pipwrap / #ftPop carry the same
# .4s cubic-bezier(.16,1,.3,1). The rail and the fades keep the .32 curve.
BIG_ANIM_S = 0.70        # the User 2026-09-09 (after 0.40): "the peel is too fast, slow it down more"
REVEAL_S = 1.5           # first boot: the BAU wall fading in when Lila wraps (2026-09-12 pm)


def _ease_out_expo(t: float) -> float:
    """Clutter EASE_OUT_EXPO: 1 - 2^(-10t), pinned to 1 at t=1."""
    t = min(1.0, max(0.0, float(t)))
    return 1.0 if t >= 1.0 else 1.0 - 2.0 ** (-10.0 * t)
RAIL_SLIDE_S = 0.32      # #rail transform .32s cubic-bezier(.2,.8,.2,1)
RAIL_FOLLOW_S = 0.2      # the rail rides the GNOME bar's own autohide ease (hidetopbar .2)
PANEL_H = int(os.environ.get("HOUSE_WALL_PANEL_H", "32"))   # GNOME top bar, measured 32 on this desk
BAR_LINGER_S = 1.2       # hidetopbar keeps the bar a beat after the pointer leaves it


def bar_bottom_from_under(under: str | None, last_shell_at: float, now: float) -> int:
    """Where the GNOME bar's bottom edge is, read off house-pointer alone
    (before house-shell's Poll is live): the pointer over the SHELL at the
    top is the bar being out, and it stays out BAR_LINGER_S after. The rail
    rests at 0 and only the bar pushes it down (the User 2026-09-08: "only
    unhid normal depth unless you persist to open Ubuntu then it comes out
    and pushes menu further, for the accordion effect")."""
    if under == "shell" or (now - last_shell_at) < BAR_LINGER_S:
        return PANEL_H
    return 0


def parse_shell_poll(stdout: str) -> dict | None:
    """busctl's reply from org.house.Shell.Poll: the signature once, then
    the values — `tssi 1234 "desktop" "com.rastersoft.ding" 32` ->
    {idle_ms, under, wm_class, panel_bottom}; None when it is not that."""
    try:
        parts = shlex.split(stdout or "")
    except ValueError:
        return None
    if len(parts) != 5 or parts[0] != "tssi":
        return None
    try:
        return {"idle_ms": int(parts[1]), "under": parts[2], "wm_class": parts[3],
                "panel_bottom": int(parts[4])}
    except ValueError:
        return None


def shell_poll() -> dict | None:
    """One call for what the wall asked twice a tick: Mutter's idle time,
    what is under the pointer, and the GNOME top bar's visible bottom edge
    (the wall's shell extension, Init). None until its login."""
    try:
        out = subprocess.run(
            ["busctl", "--user", "call", "org.house.Shell", "/org/house/Shell",
             "org.house.Shell", "Poll"],
            capture_output=True, text=True, timeout=0.6)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return parse_shell_poll(out.stdout)


def rail_slide(im, k: float, band) -> "Image.Image":
    """The rail's way in and out, the page's: translate(-50%,-130%) -> 0 on
    the .32 curve while the rest of the chrome only fades (k = the fade's
    0..1). band = the rail's rect on the chrome image (R.RAIL_BAND at the
    time it was drawn). With rail_top > 0 the band comes out from UNDER the
    GNOME bar, which sits above the desktop layer — an accordion from the
    bar (the User 2026-09-08: "make ours flow out from its. Like accordion")."""
    if k >= 0.999 or not band:
        return im
    x0, y0, x1, y1 = (int(v) for v in band)
    h = y1 - y0
    if h <= 0 or x1 <= x0:
        return im
    e = _ease_page(min(1.0, k * CHROME_FADE_S / RAIL_SLIDE_S))
    dy = int(round((1.0 - e) * h * 1.3))
    if dy <= 0:
        return im
    out = im.copy()
    strip = out.crop((x0, y0, x1, y1))
    out.paste((0, 0, 0, 0), (x0, y0, x1, y1))
    ty = y0 - dy
    if ty + h <= 0:
        return out                    # wholly above the glass: the band is simply gone
    if ty < 0:
        strip = strip.crop((0, -ty, x1 - x0, h))
        ty = 0
    out.paste(strip, (x0, ty))
    return out


# pointer surfaces: they take hover, never a click of their own
HIT_SURFACES = ("oracle_hover", "vitals_hover", "rail_plate", "dock_plate", "dock_pick_box", "menu_panel")


def pad_refusal(reply) -> str | None:
    """What a refused nudge asks for. presence's 403 not_authed ("Only the
    User aims the camera. Look at it first.") → "auth_join": the wall asks
    presence to find the User (ensure_user_auth, force_facefind — the same
    call the PIP's Connecting… join makes). the User 2026-09-09: "wire the
    refused press to auth_join". A 409 camera_leased names its holder and
    asks nothing; an ok reply asks nothing.

    Noted against it the same morning (Grok, measured): under Secure the
    camera is on the door on purpose and a desk FaceFinder chase is what
    Secure was built to stop; the hole is presence's own user_auth bool
    dropping while sage holds. the User chose the wiring; the join fires ONCE
    per press, PAD_JOIN_COOL_S apart, and the pad stops repeating refusals."""
    if not isinstance(reply, dict) or reply.get("ok"):
        return None
    err = str(reply.get("error") or "")
    if reply.get("status") == 403 or "not_authed" in err:
        return "auth_join"
    return None


PAD_JOIN_COOL_S = 15.0      # one join a press, and not again for this long
PAD_HOME_S = 30.0           # no pad activity this long after a move → the camera goes home (the User 2026-09-09)
PAD_BANK_MAX = (200.0, 80.0)  # a banked nudge may carry 40 clicks (5°/2° each; the User 2026-09-09: "queue up to 40 clicks") — the pan wraps at 360, presence clamps the tilt


def pad_bank(acc, dx: float, dy: float, cap=PAD_BANK_MAX) -> tuple[float, float]:
    """Bank a click or a hold tick that arrives while the camera is still
    on its previous move (each aim takes it ~1.5–2 s, measured 2026-09-09;
    the wall dropped those — "release after 0 ticks", the User: "feels less").
    The next nudge carries the sum, capped, so three quick clicks are one
    15° move and a hold ramps by itself."""
    ax, ay = float(acc[0]) + float(dx), float(acc[1]) + float(dy)
    cx, cy = float(cap[0]), float(cap[1])
    return (max(-cx, min(cx, round(ax, 3))), max(-cy, min(cy, round(ay, 3))))


def pad_join_mode(house_auth: dict | None) -> str:
    """The join's hunt mode, picked from the User-mode signal (the User
    2026-09-09: "pick the mode and get token if User mode is signaled").
    User mode signalled (the house record's user_auth — Secure held, tier
    held/fresh) → `door_local`: presence re-proves on the door it is holding
    (ring + one door-local look, ≤ 5 s, return door), never a desk chase —
    Secure's job stays done. No User signal → `desk`: a FaceFinder look at
    the desk, then door home. After a grant the press is re-issued once, and
    that nudge takes the camera token (user_joystick) inside presence."""
    a = house_auth or {}
    signalled = bool(a.get("user_auth")) or str(a.get("user_auth_tier") or "") in ("held", "fresh")
    return "door_local" if signalled else "desk"


def ft_veil_next(blur: bool, fade: bool) -> tuple[bool, bool]:
    """The FT window's Blur chip cycles three veils (the User 2026-09-09: "add
    Fade as 3rd state … 2nd click, 3rd back"): off → Blur → Fade → off."""
    if not blur and not fade:
        return True, False
    if blur:
        return False, True
    return False, False


def dock_apps_commands() -> list[list[str]]:
    """The APPS tile's ways into GNOME's applications grid, in order: the
    house-shell extension's ShowApps (inside the shell), then gnome-shell's
    own org.gnome.Shell.ShowApplications (refused "not allowed" from outside
    since 2026-09-08; kept as the fallback for a shell without the method)."""
    return [
        ["busctl", "--user", "--timeout=3", "call", "org.house.Shell", "/org/house/Shell", "org.house.Shell", "ShowApps"],
        ["gdbus", "call", "--session", "--dest", "org.gnome.Shell", "--object-path", "/org/gnome/Shell",
         "--method", "org.gnome.Shell.ShowApplications"],
    ]


def pick_hit(hits, x, y) -> str | None:
    """The chrome hit under (x, y). A key drawn ON a surface wins over the
    surface: the Advanced panel hangs inside the vitals hover block, and the
    first-match walk gave every one of its keys to vitals_hover (the User
    2026-09-08: "brightness and resolution button are not working"). Among
    keys the SMALLEST rect wins: a fold caret or a pan key sits INSIDE the
    box it folds or aims, and "drawn last wins" handed both to the box — the
    caret enlarged the corner, the joystick enlarged the camera (the User
    2026-09-08 pm: "check all buttons hit boxes"). Same size: the later one.
    A surface only when nothing else is under the pointer."""
    key = surf = None
    key_area = 0.0
    for n, box in hits.items():
        x0, y0, x1, y1 = box
        if x0 <= x <= x1 and y0 <= y <= y1:
            if n in HIT_SURFACES:
                surf = n
            else:
                area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
                if key is None or area <= key_area:
                    key, key_area = n, area
    return key or surf


def film_hole_zoom(house: dict | None, own: bool) -> float | None:
    """The hole the film cuts for her: HOUSE_WALL_ZOOM pins it, else the
    house record's screen.crissa_hole / fov_zoom_crissa (R.hole_zoom_of) — the
    AR key and its +/- post there and the film follows (the User 2026-09-08:
    "the +/- next to AR appears to do nothing; nor does AR" — the film read
    only the env). None = not her frame, or the hole is off."""
    if not own:
        return None
    env = os.environ.get("HOUSE_WALL_ZOOM")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return R.hole_zoom_of(house)


def workarea_top() -> int:
    """GNOME top bar height from _NET_WORKAREA (measured 32 on this desk)."""
    try:
        r = subprocess.run(
            ["xprop", "-root", "_NET_WORKAREA"],
            capture_output=True, text=True, timeout=1,
        )
        rhs = (r.stdout or "").split("=", 1)[-1]
        nums = [int(x) for x in rhs.replace(",", " ").split() if x.lstrip("-").isdigit()]
        if len(nums) >= 2:
            return max(0, nums[1])
    except Exception:
        pass
    return 32


def _parse_xid(s) -> int:
    t = str(s).strip()
    return int(t, 16) if t.lower().startswith("0x") else int(t)


def _x_locked(fn):
    """One Xlib connection, many threads (paint loop, rail keys, _to_brave): Xlib without
    XInitThreads is not thread-safe, so every InputShape call holds the instance's lock."""
    def run(self, *a, **kw):
        with self._xlock:
            return fn(self, *a, **kw)
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


class InputShape:
    """X ShapeInput: only chrome pixels take clicks. Empty glass + GNOME folders pass through."""
    ShapeInput = 2
    ShapeSet = 0
    Unsorted = 0

    def __init__(self):
        import ctypes
        from ctypes import c_int, c_short, c_ushort, c_void_p, POINTER, Structure
        self.dpy = None
        self._ids = []
        self._xlock = threading.RLock()
        self.ct = ctypes
        try:
            self.libX11 = ctypes.CDLL("libX11.so.6")
            self.libXext = ctypes.CDLL("libXext.so.6")
            self.libX11.XOpenDisplay.restype = c_void_p
            self.dpy = self.libX11.XOpenDisplay(None)
        except Exception as e:
            log(f"input shape unavailable: {e}")
            return

        class XRectangle(Structure):
            _fields_ = [("x", c_short), ("y", c_short),
                        ("width", c_ushort), ("height", c_ushort)]
        self.XRectangle = XRectangle
        self.libXext.XShapeCombineRectangles.argtypes = [
            c_void_p, ctypes.c_ulong, c_int, c_int, c_int,
            POINTER(XRectangle), c_int, c_int, c_int,
        ]
        self.libX11.XFlush.argtypes = [c_void_p]

    @_x_locked
    def activate(self, xid, ts: int = 0):
        """_NET_ACTIVE_WINDOW client message to the root: raise + focus a
        window the way a pager does (source 2). Xwayland windows honour it.
        `ts`: the X time of the user action that asked (round 39, .101 N12): with 0 the window manager's focus-stealing
        prevention may refuse it while a newer window (her browser) holds the keyboard."""
        if not self.dpy:
            return
        from ctypes import Structure, Union, c_long, c_int, c_ulong, c_void_p, c_char_p, byref
        class XClientMessageEvent(Structure):
            _fields_ = [("type", c_int), ("serial", c_ulong), ("send_event", c_int), ("display", c_void_p),
                        ("window", c_ulong), ("message_type", c_ulong), ("format", c_int), ("data", c_long * 5)]
        class XEvent(Union):
            _fields_ = [("xclient", XClientMessageEvent), ("pad", c_long * 24)]
        self.libX11.XInternAtom.restype = c_ulong
        self.libX11.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]   # from the import above: the bare module name was never imported here, so every raise died on a NameError
        self.libX11.XDefaultRootWindow.restype = c_ulong
        self.libX11.XDefaultRootWindow.argtypes = [c_void_p]
        self.libX11.XSendEvent.argtypes = [c_void_p, c_ulong, c_int, c_long, c_void_p]
        atom = self.libX11.XInternAtom(self.dpy, b"_NET_ACTIVE_WINDOW", 0)
        root = self.libX11.XDefaultRootWindow(self.dpy)
        ev = XEvent()
        ev.xclient.type = 33            # ClientMessage
        ev.xclient.window = int(xid)
        ev.xclient.message_type = atom
        ev.xclient.format = 32
        ev.xclient.data[0] = 2          # source: pager / user action
        ev.xclient.data[1] = int(ts or 0)
        self.libX11.XSendEvent(self.dpy, root, 0, (1 << 20) | (1 << 19), byref(ev))
        self.libX11.XFlush(self.dpy)

    def no_focus(self, xid):
        """WM_HINTS input=False on the client window: a click on the chrome
        must not focus-raise this desktop window over the desktop icons
        (the User 2026-09-06: every button on the glass hid the folders). Tk
        writes its own WM_HINTS at map, so this runs after deiconify."""
        self.input_hint(xid, False)

    @_x_locked
    def input_hint(self, xid, on: bool):
        """WM_HINTS input on the window: False keeps the keyboard where it is (the wall's default), True lets the
        window manager give this window the keyboard (a Crissa pop-up he opened, round 16)."""
        if not self.dpy:
            return
        from ctypes import Structure, c_long, c_int, c_ulong, byref, c_void_p
        class XWMHints(Structure):
            _fields_ = [("flags", c_long), ("input", c_int), ("initial_state", c_int),
                        ("icon_pixmap", c_ulong), ("icon_window", c_ulong), ("icon_x", c_int),
                        ("icon_y", c_int), ("icon_mask", c_ulong), ("window_group", c_ulong)]
        h = XWMHints()
        h.flags = 1          # InputHint
        h.input = 1 if on else 0
        self.libX11.XSetWMHints.argtypes = [c_void_p, c_ulong, c_void_p]
        self.libX11.XSetWMHints(self.dpy, c_ulong(int(xid)), byref(h))
        self.libX11.XFlush(self.dpy)

    @_x_locked
    def add_xid(self, xid):
        try:
            n = _parse_xid(xid)
        except (TypeError, ValueError):
            return
        if n and n not in self._ids:
            self._ids.append(n)

    @_x_locked
    def set(self, boxes):
        """boxes: (x0,y0,x1,y1) in window pixels. Empty list = click-through."""
        if not getattr(self, "dpy", None) or not self._ids:
            return
        from ctypes import c_void_p, POINTER
        rects = []
        for box in boxes or ():
            x0, y0, x1, y1 = (int(v) for v in box)
            w, h = max(0, x1 - x0), max(0, y1 - y0)
            if w == 0 or h == 0:
                continue
            rects.append(self.XRectangle(x0, y0, w, h))
        n = len(rects)
        arr = (self.XRectangle * max(1, n))(*rects) if n else None
        try:
            for xid in self._ids:
                self.libXext.XShapeCombineRectangles(
                    c_void_p(self.dpy), xid, self.ShapeInput, 0, 0,
                    arr if n else POINTER(self.XRectangle)(), n,
                    self.ShapeSet, self.Unsorted,
                )
            self.libX11.XFlush(c_void_p(self.dpy))
        except Exception as e:
            log(f"input shape: {e}")


    @_x_locked
    def x_parent(self, xid) -> int:
        """XQueryTree: the window's parent. Tk's `wm frame` answered with the
        toplevel itself here (09:07, "24 rects on 0xa00015"), but Mutter
        composites Tk's WRAPPER above it (0xa00016, parent = root); a shape on
        the child left the wrapper's black ground on the glass."""
        if not getattr(self, "dpy", None):
            return 0
        from ctypes import c_void_p, c_ulong, c_uint, POINTER, byref
        self.libX11.XQueryTree.argtypes = [c_void_p, c_ulong, POINTER(c_ulong), POINTER(c_ulong),
                                           POINTER(POINTER(c_ulong)), POINTER(c_uint)]
        self.libX11.XFree.argtypes = [c_void_p]
        root, parent, kids, n = c_ulong(0), c_ulong(0), POINTER(c_ulong)(), c_uint(0)
        try:
            ok = self.libX11.XQueryTree(c_void_p(self.dpy), c_ulong(int(xid)), byref(root), byref(parent), byref(kids), byref(n))
            if kids:
                self.libX11.XFree(kids)
        except Exception:  # noqa: BLE001
            return 0
        if not ok or parent.value == root.value:
            return 0
        return int(parent.value)

    @_x_locked
    def bounding(self, xids, boxes, input_boxes):
        """X ShapeBounding + ShapeInput on the band pop. Bounding = only the
        painted chrome shows; the rest of the pop is not there at all, so
        the window under the band stays visible (the User 2026-09-11: the band
        "is penetrating the window below it"). Input = the live band rect,
        set separately on purpose: with no input shape X uses the bounding
        one, and the pointer over a hole would be outside the window — a
        Leave between two tiles and the stack flickering shut. Both in the
        pop's own pixels; an empty bounding list shows the input region
        (never an empty mask, which hides the window)."""
        if not getattr(self, "dpy", None):
            return "no display"
        from ctypes import c_void_p

        def _arr(bs):
            rs = []
            for box in bs or ():
                x0, y0, x1, y1 = (int(v) for v in box)
                w, h = max(0, x1 - x0), max(0, y1 - y0)
                if w and h:
                    rs.append(self.XRectangle(x0, y0, w, h))
            return (self.XRectangle * max(1, len(rs)))(*rs), len(rs)
        inp, ni = _arr(input_boxes)
        if ni == 0:
            inp, ni = _arr([(0, 0, 1, 1)])
        bnd, nb = _arr(boxes)
        if nb == 0:
            bnd, nb = inp, ni
        done = []
        try:
            for xid in xids:
                xid = _parse_xid(xid)
                if not xid:
                    continue
                self.libXext.XShapeCombineRectangles(
                    c_void_p(self.dpy), xid, 0, 0, 0, bnd, nb, self.ShapeSet, self.Unsorted)   # 0 = ShapeBounding
                self.libXext.XShapeCombineRectangles(
                    c_void_p(self.dpy), xid, self.ShapeInput, 0, 0, inp, ni, self.ShapeSet, self.Unsorted)
                done.append(hex(xid))
            self.libX11.XFlush(c_void_p(self.dpy))
        except Exception as e:  # noqa: BLE001
            log(f"bounding shape: {e}")
            return str(e)
        return f"{nb} rects, input {ni} on {','.join(done)}"


class Pointer:
    """One X connection; kiosk mouse-move has to work even when Nautilus owns the desk."""
    Button1Mask = 0x100

    def __init__(self):
        import ctypes
        from ctypes import c_int, c_uint, c_ulong, c_void_p, byref, POINTER
        self.ct = ctypes
        self.lib = ctypes.CDLL("libX11.so.6")
        self.lib.XOpenDisplay.restype = c_void_p
        self.dpy = self.lib.XOpenDisplay(None)
        if not self.dpy:
            return
        self.lib.XDefaultRootWindow.restype = c_ulong
        self.root = self.lib.XDefaultRootWindow(c_void_p(self.dpy))
        self.lib.XQueryPointer.argtypes = [
            c_void_p, c_ulong,
            POINTER(c_ulong), POINTER(c_ulong),
            POINTER(c_int), POINTER(c_int),
            POINTER(c_int), POINTER(c_int),
            POINTER(c_uint),
        ]
        self.lib.XQueryPointer.restype = c_int
        self._rx = c_int()
        self._ry = c_int()
        self._wx = c_int()
        self._wy = c_int()
        self._mask = c_uint()
        self._rr = c_ulong()
        self._ch = c_ulong()

    def query(self):
        if not getattr(self, "dpy", None):
            return None
        from ctypes import c_void_p, byref
        ok = self.lib.XQueryPointer(
            c_void_p(self.dpy), self.root, byref(self._rr), byref(self._ch),
            byref(self._rx), byref(self._ry), byref(self._wx), byref(self._wy),
            byref(self._mask),
        )
        if not ok:
            return None
        return self._rx.value, self._ry.value, self._mask.value


# the house's shared glass record (Connect Walls step 2): /v1/house["glass"],
# copied by t_house when its rev is not older; shared keys overlay the file
GLASS_REC: dict = {}
GLASS_LOCK = threading.Lock()   # t_house, _glass_post and the render loop all touch GLASS_REC
GLASS_PENDING = [0]             # our own POSTs in flight (under GLASS_LOCK)


def _glass_swap(new: dict, newer_only: bool = False) -> None:
    """Replace GLASS_REC under GLASS_LOCK; newer_only skips a record older than the one held.
    While one of our POSTs is in flight the held record carries an optimistic edit at the
    old rev, so only a strictly newer rev may replace it (an equal-rev poll would undo it)."""
    if not isinstance(new, dict):
        return
    with GLASS_LOCK:
        if newer_only:
            got, held = int(new.get("rev") or 0), int(GLASS_REC.get("rev") or 0)
            if got < held or (got == held and GLASS_PENDING[0] > 0):
                return
        GLASS_REC.clear()
        GLASS_REC.update(new)


def _glass_merge(partial: dict) -> None:
    """An optimistic local edit; the caller's POST must call _glass_done() when it ends."""
    with GLASS_LOCK:
        GLASS_REC.update(partial)
        GLASS_PENDING[0] += 1


def _glass_done() -> None:
    with GLASS_LOCK:
        GLASS_PENDING[0] = max(0, GLASS_PENDING[0] - 1)


def _prefs() -> dict:
    with GLASS_LOCK:
        rec = dict(GLASS_REC)
    return prefs.merge_shared(prefs.load(), rec)


def http_get(url: str, timeout: float = 2.0) -> bytes | None:
    try:
        with crissa_egress.urlopen(url, timeout, "the Wall") as r:
            return r.read()
    except Exception:
        return None


def http_json(url: str, timeout: float = 2.0):
    b = http_get(url, timeout)
    if not b:
        return None
    try:
        return json.loads(b.decode("utf-8", "replace"))
    except Exception:
        return None


class State:
    """Everything the fetch threads write and the render loop reads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.frame: bytes | None = None
        self.frame_at = 0.0
        self.frame_seq = 0          # every JPEG the feed delivered (repeats included)
        self.frame_stamp = 0.0      # conductor's X-Frame-T of the FIRST arrival of these bytes
        self.frame_mouth = None     # conductor's X-Mouth-Box for these bytes (her mouth, left out of the exposure)
        self.frame_clip = None      # conductor's X-Clip for these bytes (key, frame, frames, role), round 13
        self.lags: list = []        # ms, mouth picked -> the bg layer holds the picture
        self.distinct_at: list = []  # arrival times of the last 48 frames whose bytes changed
        self.fifo = collections.deque(maxlen=FILM_QUEUE_N)   # (jpeg, stamp, arrived, mouth) of the newest distinct frames
        self.frame_no = None
        self.frame_no_at = time.time()   # grace at boot: not stale until STALE_S with no frame_no
        self.house: dict = {}
        self.layout: dict = {}           # round 35: the player's own /v1/layout (her render's place for the card)
        self.layout_card = None          # round 36: that card's stamp (the player's SCREEN "at")
        self.presence: dict = {}
        self.speaker: dict = {}          # :8099/v1/speaker — the kiosk's camera name + face sim
        self.sound: dict = {}            # :8099/v1/sound at 5 Hz — the beam, not the 2 s house poll
        self.sound_crissa: dict = {}       # the same payload's crissa block: mouth_sink / air window
        self.thinking: dict = {}         # K: <state home>/crissa-thinking.json (HOUSE_WALL_THINKING_PULSE)
        self.crissa_line_ts: float = 0.0   # K: the newest Crissa line that entered the roll (its ts)
        self.crissa_line_at: float = 0.0   # K: ... and when it entered
        self.house_kick = threading.Event()   # K: fetch /v1/house now, not at the next 2 s tick
        self.zlevel: float = 0.0         # :8100/v1/state level — her mouth envelope, 10 Hz on the sink
        self.on_air: bool = False        # :8100/v1/state on_air — her voice is on air (the rest rule, REST)
        self.wall_frame: dict = {}       # :8100/v1/state wall_frame — the frame's mode and this clip's face point
        self.fp_by_clip: dict = {}       # round 45: clip key -> its face point (X-Face-Point, else /v1/state's `now`)
        self.register: dict = {}         # :8099/v1/register at 5 Hz — the coach's phase while Register runs
        self.init: dict = {}             # :8099/v1/init — the first-boot walker's snapshot (running: 2 Hz, else 0.2 Hz)
        self.init_tog: dict = {}         # the wizard's toggles on the glass (cards, topics, seat picks) — step_id → {name: value}
        self.camera: bytes | None = None
        self.stop = threading.Event()
        self.frame_event = threading.Event()
        self.toast_text = ""
        self.toast_until = 0.0

    def stale(self) -> bool:
        with self.lock:
            return (time.time() - self.frame_no_at) > STALE_S


_MJPG = {"t": 0.0, "mouth": None, "clip": None, "fp": None}   # X-Frame-T, X-Mouth-Box, X-Clip, X-Face-Point of the part


# HER FRAME IS DRAWN ABOUT THE FRAME'S OWN CLIP'S FACE POINT (round 45, .101: "2 of the last 130 pictures drawn fresh"
# at a clip's first play, an hour after the build): the point came from /v1/state, polled every 0.5 s, so for up to
# half a second after a clip change her new clip was drawn about the old clip's point - a picture the build never made
# (the house's own laps 10-21..23: 1.08 fresh a clip change, 0.1 a lap of the same clip). The point is now the frame's
# own clip's (X-Face-Point on the part, else the last point /v1/state named for that clip). Kill
# HOUSE_WALL_FRAME_FP_BY_CLIP=0 (the state's point, as before). On in a pack; the house's desk Wall keeps its way until
# the User says (=1 turns it on there).
def _fp_by_clip_default() -> str:
    try:
        import crissa_home
        return "0" if crissa_home.is_house() else "1"
    except Exception:  # noqa: BLE001
        return "0"


FRAME_FP_BY_CLIP = os.environ.get("HOUSE_WALL_FRAME_FP_BY_CLIP", _fp_by_clip_default()).strip().lower() not in (
    "0", "false", "off", "no")


def _face_point(raw) -> tuple | None:
    """X-Face-Point "x,y" (film fractions) -> (x, y), else None."""
    try:
        v = tuple(float(x) for x in raw.decode("ascii", "replace").split(","))
    except (ValueError, AttributeError):
        return None
    return v if len(v) == 2 and all(0.0 <= x <= 1.0 for x in v) else None


def _clip_of(raw) -> tuple | None:
    """X-Clip "key;frame;frames;role" (round 13) -> (key, frame, frames, role), else None."""
    try:
        parts = (raw.decode("ascii") if isinstance(raw, bytes) else str(raw)).strip().split(";")
        if len(parts) != 4 or not parts[0]:
            return None
        return (parts[0], int(parts[1]), int(parts[2]), parts[3])
    except (UnicodeDecodeError, ValueError, AttributeError):
        return None


def _mouth_box(raw) -> tuple | None:
    """X-Mouth-Box "x,y,w,h" (frame pixels) -> a tuple of four floats, else None."""
    try:
        v = tuple(float(x) for x in raw.decode("ascii", "replace").split(","))
    except (ValueError, AttributeError):
        return None
    if len(v) != 4 or not (v[2] > 0 and v[3] > 0) or any(x != x or abs(x) > 1e5 for x in v):
        return None
    return v


def _mjpg_take_one(buf: bytes) -> tuple[bytes | None, bytes]:
    """Pull one JPEG from a multipart x-mixed-replace buffer. Leftover second."""
    if not buf:
        return None, buf
    # Skip a leading boundary line.
    if buf.startswith(b"--"):
        nl = buf.find(b"\r\n")
        if nl < 0:
            return None, buf
        buf = buf[nl + 2:]
    sep = buf.find(b"\r\n\r\n")
    if sep < 0:
        return None, buf
    header, rest = buf[:sep], buf[sep + 4:]
    clen = None
    stamp = 0.0
    mouth = None
    clip = None
    fp = None
    for line in header.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            try:
                clen = int(line.split(b":", 1)[1].strip())
            except (TypeError, ValueError):
                clen = None
        elif line.lower().startswith(b"x-frame-t:"):
            try:
                stamp = float(line.split(b":", 1)[1].strip())
            except (TypeError, ValueError):
                stamp = 0.0
        elif line.lower().startswith(b"x-mouth-box:"):
            mouth = _mouth_box(line.split(b":", 1)[1].strip())
        elif line.lower().startswith(b"x-clip:"):
            clip = _clip_of(line.split(b":", 1)[1])
        elif line.lower().startswith(b"x-face-point:"):
            fp = _face_point(line.split(b":", 1)[1].strip())
    _MJPG["fp"] = fp
    _MJPG["t"] = stamp
    _MJPG["mouth"] = mouth
    _MJPG["clip"] = clip
    if clen is not None:
        if len(rest) < clen:
            return None, buf
        jpeg, rest = rest[:clen], rest[clen:]
        if rest.startswith(b"\r\n"):
            rest = rest[2:]
        return jpeg, rest
    # No Content-Length: JPEG SOI..EOI, then the next boundary.
    soi = rest.find(b"\xff\xd8")
    if soi < 0:
        return None, buf
    eoi = rest.find(b"\xff\xd9", soi + 2)
    if eoi < 0:
        return None, buf
    jpeg = rest[soi:eoi + 2]
    return jpeg, rest[eoi + 2:]


def t_frames(st: State, fps: float):
    """Brave's wire: the live mjpg. Poll frame.jpg only if the stream drops.

    Polling one JPEG per tick skipped short visemes (Okay's OH) at 22/s.
    The stream delivers every conductor frame; the draw loop still caps at
    HOUSE_WALL_FPS and keeps the latest.
    """
    period = 1.0 / max(1.0, fps)
    while not st.stop.is_set():
        if REST["on"]:
            closed = agent_closed_look()
            if not closed:
                REST["still_at"] = 0.0
            elif REST.get("still_at"):
                # CLOSE AGENT IS ONE STILL (round 16, .101 00:48: the resting Wall pasted a new picture every 4-8 s
                # while her agent was closed): the one frame taken after the close is the picture, dimmed, until
                # Open Agent - nothing more is read (the player holds its frame too, house-conductor agent_still)
                time.sleep(0.25)
                continue
            # RESTING (REST above): the newest frame, REST_FPS a second, read as a rest (not a look)
            t0 = time.time()
            b = http_get(f"{FEED}/frame.jpg?rest=1", timeout=2.0)
            if closed and b and len(b) > 500:
                REST["still_at"] = t0
                log("rest: her agent is closed - this frame is the still until Open Agent")
            if b and len(b) > 500:
                with st.lock:
                    if b != st.frame:
                        st.distinct_at.append(t0)
                        if len(st.distinct_at) > 48:
                            del st.distinct_at[:-48]
                    st.frame = b
                    st.frame_at = t0
                    st.frame_seq += 1
                    st.frame_event.set()
            end = t0 + (1.0 / AGENT_STILL_FPS if agent_closed_look() else 1.0 / REST_FPS)
            while REST["on"] and not st.stop.is_set() and time.time() < end:
                time.sleep(0.05)
            continue
        try:
            req = urllib.request.Request(
                f"{FEED}/frame.mjpg", headers={"Connection": "close"})
            with crissa_egress.urlopen(req, 10, "the Wall") as r:
                buf = b""
                while not st.stop.is_set():
                    if REST["on"]:
                        break                       # resting: drop the stream, the slow read takes over
                    # WHAT HAS ARRIVED, NOT A FULL 64 KB (2026-10-10, round 4): read() blocks until 65536 bytes are
                    # in, so a frame waited for the next one's bytes and two often landed in one read, the older lost
                    # - measured at 3440x1440 with the player sending its 24 distinct frames: 15.4 drawn a second;
                    # read1() hands over each frame as it lands. Kill: HOUSE_WALL_READ1=0.
                    chunk = r.read1(65536) if READ1 and hasattr(r, "read1") else r.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while True:
                        jpeg, buf = _mjpg_take_one(buf)
                        if jpeg is None:
                            break
                        if len(jpeg) > 500 and jpeg[:2] == b"\xff\xd8":
                            with st.lock:
                                now_f = time.time()
                                if jpeg != st.frame:
                                    st.distinct_at.append(now_f)
                                    if len(st.distinct_at) > 48:
                                        del st.distinct_at[:-48]
                                    st.frame_stamp = _MJPG["t"]   # a repeat keeps the first stamp
                                    st.fifo.append((jpeg, _MJPG["t"], now_f, _MJPG["mouth"], _MJPG["clip"]))
                                st.frame = jpeg
                                st.frame_mouth = _MJPG["mouth"]
                                st.frame_clip = _MJPG["clip"]
                                if _MJPG["clip"] and _MJPG["fp"]:
                                    st.fp_by_clip[_MJPG["clip"][0]] = _MJPG["fp"]
                                st.frame_at = now_f
                                st.frame_seq += 1
                                st.frame_event.set()
        except Exception:
            pass
        if st.stop.is_set():
            break
        if REST["on"]:
            continue
        t0 = time.time()
        b = http_get(f"{FEED}/frame.jpg", timeout=2.0)
        if b and len(b) > 500:
            with st.lock:
                if b != st.frame:
                    st.distinct_at.append(t0)
                    if len(st.distinct_at) > 48:
                        del st.distinct_at[:-48]
                    st.fifo.append((b, 0.0, t0, None, None))
                st.frame = b
                st.frame_mouth = None
                st.frame_clip = None
                st.frame_at = t0
                st.frame_seq += 1
                st.frame_event.set()
        time.sleep(max(0.0, period - (time.time() - t0)))


def t_glass(st: State):
    """THE GLASS CANARY (2026-09-17). The eye tunes lead against this glass; a
    match game measures inside the conductor and cannot see it. Every 2 s: how
    long a DISTINCT picture took from the conductor picking its mouth
    (X-Frame-T on each stream part, this box's clock) to the bg layer holding
    it — stream, decode, ASCII render, the presentation clock, the pump."""
    import statistics
    while not st.stop.is_set():
        time.sleep(2.0)
        with st.lock:
            lags, st.lags = st.lags, []
        if len(lags) < 5:
            continue
        lags.sort()
        body = {"who": "wall-tk", "median_ms": round(statistics.median(lags), 1),
                "p90_ms": round(lags[int(0.9 * (len(lags) - 1))], 1), "min_ms": round(lags[0], 1),
                "n": len(lags), "fps": round(len(lags) / 2.0, 1),
                "how": "X-Frame-T -> bg layer blit (ASCII wall)"}
        try:
            req = urllib.request.Request(f"{FEED}/v1/glass-lag", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            crissa_egress.urlopen(req, 1.5, "the Wall").read()
        except Exception:
            pass


def t_state(st: State):
    """Health: frame_no from :8100/v1/state. This is the only 'is it alive' signal.
    The same JSON carries `level`, the Conductor's own mouth envelope (0..1),
    which the star over her core beats to (the User 2026-09-07: "a separate red
    globe beats with her voice like sound sensitive lights in my tesla");
    polled at 10 Hz while the sink is hers, 2 Hz otherwise."""
    last = None
    while not st.stop.is_set():
        d = http_json(f"{FEED}/v1/state", timeout=2.0)
        hot = False
        if isinstance(d, dict):
            n = d.get("frame_no", d.get("frames", d.get("frame")))
            try:
                lv = max(0.0, min(1.0, float(d.get("level") or 0.0)))
            except (TypeError, ValueError):
                lv = 0.0
            with st.lock:
                st.zlevel = lv
                st.on_air = bool(d.get("on_air"))
                wf = d.get("wall_frame")
                st.wall_frame = wf if isinstance(wf, dict) else {}
                fp_s = st.wall_frame.get("face_point")
                if d.get("now") and isinstance(fp_s, list) and len(fp_s) == 2:
                    st.fp_by_clip.setdefault(str(d["now"]), tuple(float(v) for v in fp_s))
                hot = st.sound_crissa.get("mouth_sink") is True
                if n is not None and n != last:
                    last = n
                    st.frame_no = n
                    st.frame_no_at = time.time()
        time.sleep(0.1 if hot else 0.5)


def t_sound(st: State):
    """The beam at the mic array's own rate. The DoA loop publishes at 5 Hz
    (RAIL_DOA_HZ); riding it on the 2 s house poll left the lobe up to 3 s
    behind the room (the User 2026-09-07: "the dots aren't tracking the
    hardware"). /v1/sound is a small JSON; 5 Hz costs nothing."""
    while not st.stop.is_set():
        d = http_json(f"{HOUSE}/v1/sound", timeout=1.5)
        if isinstance(d, dict):
            snd = d.get("sound")
            with st.lock:
                if isinstance(snd, dict) and snd.get("az_deg") is not None:
                    st.sound = snd
                if isinstance(d.get("crissa"), dict):
                    st.sound_crissa = d["crissa"]
        if THINKING_PULSE:
            _think_tick(st)
        time.sleep(0.2)


def _think_tick(st: "State") -> None:
    """K, at the sound poll's 5 Hz: read the bridge's thinking file; while the pulse waits for her line and
    her mouth has gone live, kick the house poll so the line lands now instead of up to 2 s later."""
    try:
        th = json.loads(THINKING_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        th = {}
    now = time.time()
    with st.lock:
        st.thinking = th if isinstance(th, dict) else {}
        waiting = R.thinking_pulse(st.thinking, st.crissa_line_ts, now) is not None
        live = R.on_air(st.sound_crissa, now) or st.sound_crissa.get("mouth_sink") is True
    if waiting and live:
        st.house_kick.set()


def rest_busy(init: dict | None, register: dict | None) -> str:
    """Why the glass is busy (never rests): Init's walk running, or Register's routine live. "" when neither."""
    if str((init or {}).get("status") or "") == "running":
        return "Init is on the glass"
    phase = str((register or {}).get("phase") or "idle")
    if phase not in ("", "idle", "done"):
        return "Voice ID is on the glass"
    return ""


def t_rest(st: State):
    """REST (above): every half second, is anybody watching? Presence's door watch is asked every 2 s."""
    door_at = 0.0
    health = (PRESENCE.rstrip("/") + "/health") if PRESENCE else ""
    while not st.stop.is_set():
        now = time.time()
        if health and now - door_at >= 2.0:
            door_at = now
            h = http_json(health, timeout=1.0)
            REST["door"] = (h.get("door_rest") or {}) if isinstance(h, dict) else {}
        with st.lock:
            on_air = bool(st.on_air or st.zlevel > 0.02 or st.sound_crissa.get("mouth_sink") is True)
            busy = rest_busy(st.init, st.register)
        if agent_closed_look():                   # Close Agent: a still, whatever else (round 15)
            resting, why = True, "her agent is closed (Open Agent brings her back)"
        else:
            resting, why = rest_verdict(now, REST["input_at"], REST["door"], on_air, busy)
        if resting != REST["on"]:
            REST.update(on=resting, why=why, since=now)
            log(("rest: on - %s; drawing %.0f frame%s a second" % (why, REST_FPS, "" if REST_FPS == 1 else "s"))
                if resting else ("rest: off - %s; every distinct frame again" % why))
            st.frame_event.set()
        time.sleep(0.5)


def t_register(st: State):
    """The coach's phase (watch / read / turn / done) and the take line, at
    the same rate the Brave overlay polls (200 ms). Small JSON; idle it is
    {phase: idle}."""
    while not st.stop.is_set():
        d = http_json(f"{REGISTER_SRC}/v1/register", timeout=1.5)
        if isinstance(d, dict) and d.get("ok", True):
            with st.lock:
                st.register = d
        time.sleep(0.2)


INIT_RUN = {"id": ""}      # INIT V2: every action names its run (t_init keeps this current)


def _init_post(action: str, body: dict | None = None, timeout: float = 30.0) -> dict | None:
    """POST :8099/v1/init — the walker's actions (say / listen / next / skip /
    answer / enroll_done / abort). listen blocks up to ~10 s on the ear."""
    try:
        b = dict(body or {}, action=action)
        if INIT_RUN["id"]:
            b.setdefault("run_id", INIT_RUN["id"])
        data = json.dumps(b).encode()
        req = urllib.request.Request(f"{INIT}/v1/init", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with crissa_egress.urlopen(req, timeout, "Init") as r:
            return json.loads(r.read().decode() or "{}")
    except Exception as e:  # noqa: BLE001
        log(f"init {action}: {type(e).__name__}"[:120])
        return None


def t_init(st: State):
    """The first-boot walker's snapshot. ?glass=1 keeps the walker's mic hold
    alive, so it is sent only while a routine runs; idle, a plain read every
    5 s notices a start (Control Panel New User → init_apply start)."""
    while not st.stop.is_set():
        with st.lock:
            running = str((st.init or {}).get("status") or "") == "running"
        d = http_json(f"{INIT}/v1/init" + ("?glass=1" if running else ""), timeout=3.0)
        ending = False
        if isinstance(d, dict):
            with st.lock:
                st.init = d
            running = str(d.get("status") or "") == "running"
            ending = bool((d.get("ending") or {}).get("on"))   # round 37: her walk-in, read as fast as a running walk
            INIT_RUN["id"] = str(d.get("run_id") or "") if d.get("v2") else ""
        time.sleep(0.5 if (running or ending) else 5.0)


# THE PLAYER'S CARD IS A PAGE UP (2026-10-16, her display tool, lib/crissa_display): in a pack the panel's /v1/house
# carries no `screen`, so the card beside her on the player is read here as the house's page: a document is the letter
# on the right (as the house draws it), a web page puts its title there under the letter window, and doc_placement sees
# a page up (she slides to her kept spot). Never on the house (its glass server has its own screen). Kill
# CRISSA_WALL_PLAYER_CARD=0.
PLAYER_CARD = (not crissa_home.is_house()
               and os.environ.get("CRISSA_WALL_PLAYER_CARD", "1").strip().lower() not in ("0", "false", "no", "off"))


def player_card_screen(got) -> dict:
    """GET /v1/screen's card as the house's `screen` record: {notes, notes_title} while it is up, else empty."""
    c = (got or {}).get("screen") if isinstance(got, dict) else None
    if not isinstance(c, dict) or not c.get("up"):
        return {"notes": None, "notes_title": None}
    body = str(c.get("doc") or c.get("notes") or "").strip()
    title = str(c.get("doc_title") or c.get("notes_title") or c.get("title") or "").strip()
    if not body:
        body = title or str(c.get("url") or c.get("art_label") or "").strip()
    return {"notes": body or None, "notes_title": (title or None)}


# HER RENDER BESIDE THE CARD ON THE TK WALL (round 34, .101 round 33 on the full desk Wall: "render did not move window"):
# the player decides where her render goes while a card is up (lib/crissa_layout: "centred beside the page, in view")
# and only the browser Wall page read it. On a pack's desk Wall her render is the corner tile (BLUR / WALL on its bar):
# while the player's card is up (the page up this Wall already reads, PLAYER_CARD) the tile sits in that box, at its
# own shape, left of the card's sheet; when the card comes down it goes back to its corner. Never on the house. Kill
# CRISSA_WALL_RENDER_BESIDE_CARD=0.
def render_beside_card_on(env=None) -> bool:
    e = os.environ if env is None else env
    if crissa_home.is_house():
        return False
    return str(e.get("CRISSA_WALL_RENDER_BESIDE_CARD", "1")).strip().lower() not in ("0", "off", "false", "no")


# ROUND 35 (.101 round 34: wall.log had no "her tile beside the card" while player.log said `screen: card up` and
# `layout: render on center [0.06, 0.191, 0.46, 0.618] fraction`). Round 34 waited for a page in st.house, which on a pack
# comes from the feed's /v1/house - a second hop (the feed, then the player's card) that never brought the card to this
# Wall on .101. The player answers the question itself: GET /v1/layout is its decision and its box. The Wall now reads
# that, every house tick, and her tile follows the player's own box (layout_tile_box), not a box worked out here.
# HIS CLICK WINS (round 36, .101 round 35 FAIL 2: "clicking render window now cycle it through color and black and white
# but won't minimize it"): while her tile sits beside a card the card box held its place whatever the toggle said, so a
# click only flipped her picture in place. A click on her tile there sends it to its corner until the next card goes up
# (the player's card stamp changes). Kill CRISSA_WALL_BESIDE_CLICK=0.
FIRST_BOOT_DARK_CAP_S = 60.0     # round 38: the dark first frame never outlasts this


def home_fixed_on() -> bool:
    """Round 37 (.101 round 36 A, the User: "can we fix home as always home"): in a pack her tile's home is the kit's
    corner; a drag never writes ft_tile and holds her only until the next card or page-down. Kill
    CRISSA_WALL_HOME_FIXED=0 (a drag keeps its spot, round 36)."""
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_WALL_HOME_FIXED", "1").strip().lower() not in ("0", "off", "false", "no")


# A CLICK IS NOT A DRAG (round 37, .101 round 36 A: a tremble past 4 px - 0.9 mm on his 800 mm glass - set a new home):
# in a pack the slop is millimetres of glass, from the screen's own width in mm (DRAG_SLOP_MM, CRISSA_WALL_DRAG_MM),
# never under the old 4 px; the house keeps 4 px.
DRAG_SLOP_MM = 3.0


def _screen_mm(root) -> float:
    try:
        return float(root.winfo_screenmmwidth())
    except Exception:  # noqa: BLE001
        return 0.0


def drag_slop_px(width_px: int, width_mm: float, env: dict | None = None) -> int:
    e = os.environ if env is None else env
    if crissa_home.is_house() or not width_mm or width_mm <= 0:
        return 4
    try:
        mm = float(e.get("CRISSA_WALL_DRAG_MM", DRAG_SLOP_MM))
    except ValueError:
        mm = DRAG_SLOP_MM
    return max(4, int(round(mm * float(width_px) / float(width_mm))))


def beside_picture_right_on() -> bool:
    """Round 37 (.101 round 36 A, "tuck her to the real edge"): beside a card her picture (narrower than its 16:9 box)
    sits against the box's page side. A pack only; kill CRISSA_WALL_BESIDE_RIGHT=0 (centred, round 36)."""
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_WALL_BESIDE_RIGHT", "1").strip().lower() not in ("0", "off", "false", "no")


def click_drops_hold_on() -> bool:
    """Round 39 (.101 round 38 N3): a click on her tile drops a drag's hold. Kill CRISSA_WALL_CLICK_DROPS_HOLD=0."""
    return os.environ.get("CRISSA_WALL_CLICK_DROPS_HOLD", "1").strip().lower() not in ("0", "off", "false", "no")


def beside_click_wins_on() -> bool:
    return os.environ.get("CRISSA_WALL_BESIDE_CLICK", "1").strip().lower() not in ("0", "off", "false", "no")


def layout_tile_box(lay: dict | None, W: int, H: int) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) glass px of the player's layout box while her render is away from home, else None."""
    d = lay if isinstance(lay, dict) else {}
    if not d or str(d.get("render") or "home") == "home":
        return None
    b = d.get("box")
    if not isinstance(b, (list, tuple)) or len(b) < 4:
        return None
    try:
        x, y, w, h = (float(v) for v in b[:4])
    except (TypeError, ValueError):
        return None
    if str(d.get("unit") or "") == "fraction":
        x, y, w, h = x * W, y * H, w * W, h * H
    if w <= 0 or h <= 0:
        return None
    return int(round(x)), int(round(y)), int(round(w)), int(round(h))


def card_box(W: int, H: int, decide=None) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) glass px of the player's layout box for a card that is up, or None (her tile stays home)."""
    try:
        if decide is None:
            import crissa_layout
            decide = crissa_layout.decide
        d = decide(True, screen=(W, H))
    except Exception:  # noqa: BLE001  no layout: her tile where it is
        return None
    b = d.get("box") if isinstance(d, dict) and d.get("render") == "on_tile" else None
    if not isinstance(b, (list, tuple)) or len(b) < 4:
        return None
    x, y, w, h = (float(v) for v in b[:4])
    if str(d.get("unit") or "") == "fraction":
        x, y, w, h = x * W, y * H, w * W, h * H
    if w <= 0 or h <= 0:
        return None
    return int(round(x)), int(round(y)), int(round(w)), int(round(h))


def t_house(st: State):
    while not st.stop.is_set():
        h = http_json(f"{HOUSE}/v1/house", timeout=3.0)
        if isinstance(h, dict) and h.get("ok", True) and PLAYER_CARD and not isinstance(h.get("screen"), dict):
            h["screen"] = player_card_screen(http_json(f"{FEED}/v1/screen", timeout=2.0))
        if isinstance(h, dict) and h.get("ok", True):
            with st.lock:
                st.house = h
            gl = h.get("glass")
            if isinstance(gl, dict):
                _glass_swap(gl, newer_only=True)
        elif PLAYER_CARD:
            # round 35: no house feed answering (.101 round 34) - the player's card still reaches the house's own
            # "a page is up" paths (doc_placement.doc_up, the letter's clearance) through st.house's screen
            scr = player_card_screen(http_json(f"{FEED}/v1/screen", timeout=2.0))
            with st.lock:
                st.house = dict(st.house or {}, screen=scr)
        if render_beside_card_on():
            lay = http_json(f"{FEED}/v1/layout", timeout=2.0)          # round 35: the player's own decision
            if isinstance(lay, dict):
                with st.lock:
                    st.layout = lay.get("layout") if isinstance(lay.get("layout"), dict) else {}
                    st.layout_card = lay.get("card_at")      # round 36: which card the box is for
        p = http_json(f"{PRESENCE}/v1/presence", timeout=3.0)
        if isinstance(p, dict):
            with st.lock:
                st.presence = p
        # the kiosk's #pipLbl reads /v1/speaker (name + sim); same feed here so
        # the two camera labels agree (the User 2026-09-06: the wall "never
        # updated the Face score" while the browser did)
        sp = http_json(f"{HOUSE}/v1/speaker", timeout=3.0)
        if isinstance(sp, dict):
            with st.lock:
                st.speaker = sp
        if THINKING_PULSE:
            st.house_kick.wait(2.0)   # K: her mouth went live while the dot waits for her line
            st.house_kick.clear()
        else:
            time.sleep(2.0)


def t_camera(st: State):
    while not st.stop.is_set():
        b = http_get(f"{HOUSE}/frame?t={int(time.time() * 1000)}", timeout=3.0)
        if b and len(b) > 500:
            with st.lock:
                st.camera = b
        time.sleep(1.5)


def _setup_in_her_window(snap: dict) -> bool:
    try:
        import init_v2_render
        return bool(init_v2_render.setup_in_her_window(snap))
    except Exception:  # noqa: BLE001  no Init glass here: nothing to step aside for
        return False


def own_window_page(url: str) -> str:
    """'init' or 'wall' when `url` is her own Setup or Wall page on this install's player (a pack), else ''."""
    if crissa_home.is_house():
        return ""
    try:
        import urllib.parse as _up
        u, base = _up.urlsplit(str(url or "")), _up.urlsplit(INIT)
    except ValueError:
        return ""
    if (u.hostname, u.port) != (base.hostname, base.port):
        return ""
    return {"/init": "init", "/wall": "wall"}.get(u.path.rstrip("/") or "/", "")


def open_own_window(url: str):
    """HER PAGES OPEN IN HER OWN WINDOW (the User 2026-10-11): Setup (/init) and her Wall (/wall) on a pack open in
    Crissa's own browser instance (lib/crissa_window), brought to the front - Setup never as a tab in the person's own
    browser (.101 JJ). True / False when it was hers to open; None when `url` is not her page or her window is off here
    (the caller then opens it as before). A Setup the Wall opened there is recorded, so this glass shows only "Setup is
    open in Crissa's window"."""
    page = own_window_page(url)
    if not page:
        return None
    try:
        import crissa_window
        if not crissa_window.enabled():
            return None
        got = crissa_window.show(url, front=True)
    except Exception as e:  # noqa: BLE001
        log(f"her window: {type(e).__name__}: {e}"[:160])
        return False if page == "init" else None
    log(f"her window: {url} -> {'ok ' + ','.join(got.get('how') or []) if got.get('ok') else got.get('why')}"[:200])
    if (got.get("ok") or got.get("launched")) and page == "init":
        try:
            import crissa_init
            crissa_init.mark_browser_opened(url, by="wall")
        except Exception:  # noqa: BLE001
            pass
    if got.get("ok") or got.get("launched"):
        return True
    return False if page == "init" else None     # her Wall page: the person's browser as before; Setup: never


def open_her_page(url: str):
    """EVERY CRISSA PAGE IN HER OWN INSTANCE (the User 2026-10-12, .101: the Store key, Connect and the Control Panel
    opened in his own Brave, and after Stop a Crissa page stayed there frozen): in a pack, a page of this install's own
    (its local pages, the store) opens in Crissa's own browser instance (lib/crissa_window.open_page), which Stop and All
    OS Off close. True when it opened there; None to open it as before (not her page, her window off here, or no
    browser of her family on this computer - said in the log)."""
    try:
        import crissa_window
        if not crissa_window.enabled() or not crissa_window.is_her_page(url):
            return None
        # .101 10-19: the phone page opens phone-shaped in an app window of hers (crissa_window.open_phone)
        got = (crissa_window.open_phone(url) if crissa_window.is_phone_page(url)
               else crissa_window.open_page(url, front=True))
    except Exception as e:  # noqa: BLE001
        log(f"her window: {type(e).__name__}: {e}"[:160])
        return None
    if got.get("ok") or got.get("launched"):
        log(f"open {url} (her window: {','.join(got.get('how') or []) or 'started'})"[:200])
        return True
    if got.get("why") == "no browser":
        log(f"her window: this computer has no Brave, Chromium or Google Chrome, so {url} opens in your default "
            "browser (Stop cannot close it there)"[:240])
    else:
        log(f"her window did not open {url} ({got.get('why')}): your default browser"[:200])
    return None


def open_url(url: str) -> bool:
    """Open `url` in the person's browser. True when an opener took it (the pack's default browser said yes, or the
    house's opener started), False when nothing could open it. In a pack her own pages go to her own window first
    (open_own_window): Setup never opens in the person's browser."""
    cmd = os.environ.get("HOUSE_WALL_OPEN")
    if not cmd:
        ub = REPO / "scripts" / "user-brave"
        cmd = str(ub) if ub.exists() else ""
        if not cmd and not crissa_home.is_house():
            own = open_own_window(url)
            if own is not None:
                return bool(own)
            her = open_her_page(url)
            if her is not None:
                return her
            # A PACK HAS NO USER-BRAVE (2026-09-28): the house's opener is not shipped, so
            # every link on a pack's wall was a logged no-op. Outside the house the User's
            # own default browser is the right place. The house keeps its law: no system
            # opener, ever (a missing user-brave there is a fault, not a pack).
            try:
                import webbrowser
                ok = webbrowser.open(url)
                log(f"open {url} (default browser{'' if ok else ' refused'})")
                return bool(ok)
            except Exception as e:  # noqa: BLE001
                log(f"open failed: {e}")
            return False
    if not cmd or crissa_home.is_own_browser(cmd):
        log(f"no User Brave opener — not opening {url} (no xdg-open fallback by design)")
        return False
    try:
        subprocess.Popen(
            [cmd, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log(f"open {url}")
        return True
    except Exception as e:
        log(f"open failed: {e}")
        return False


# X (the User 2026-10-10, .101: the Tk Language box went behind the Wall's black plate on one stray click, and the name
# box sat over the plate): the desk Wall is a desktop-layer window, and a pop-up's stacking over it is the window
# manager's call. In a pack the Wall asks for no typing of its own: a key that needs words opens Setup (/init) in the
# browser, where the same field is a normal page field. Only when no browser opens does the Tk box come up, and then
# it keeps itself above (lib/init_v2_dialog: no transient parent, re-lifted while open). HOUSE_WALL_TK_INPUT=1 keeps
# the Tk boxes in a pack too.
TYPE_KEYS = ("init2_name", "init2_language", "init2_connect_edit", "init2_hr_change")


def types_in_browser(env=None) -> bool:
    e = os.environ if env is None else env
    return not crissa_home.is_house() and str(e.get("HOUSE_WALL_TK_INPUT", "")).strip() not in ("1", "on", "true")


def open_composer() -> None:
    """Oracle face / COMP: the chip if :8219 is up, else User Brave. Toggle parks."""
    try:
        crissa_egress.urlopen(f"{CHIP}/v1/health", 0.6, "the Wall")
        req = urllib.request.Request(
            f"{CHIP}/v1/chip/composer/toggle", data=b"", method="POST",
        )
        crissa_egress.urlopen(req, 2, "the Wall")
        log("composer toggle")
        return
    except Exception as e:
        log(f"composer chip: {e}")
    # the house's Composer; a pack's is the store or the user's CRISSA_COMPOSER_URL (2026-10-07)
    # the store's catalog, named with this Wall to come back to (2026-10-12, crissa_home.leave_url)
    # the house's key goes where config/house-machine.json composer_url says (the User 2026-10-26: "reset my composer
    # menu button to take me to my complete collection" - the roster of every library, ?roster=1), never a literal
    open_url(crissa_home.composer_url() if crissa_home.is_house() else crissa_home.leave_url())


def _wm_frame_id(root) -> str:
    """The X window the WM sees (Tk's wrapper), as xprop wants it."""
    try:
        return str(root.wm_frame())
    except Exception:
        return hex(root.winfo_id())


CRISSA_CDP = os.environ.get("HOUSE_WALL_CRISSA_CDP", "http://127.0.0.1:9222")
_LAST_FRONT: dict = {"id": None}     # the tab front_crissa_tab last activated (CDP target id)
_WIN_OF: dict = {}                    # CDP target id -> windowId, from the last _crissa_app_window()


def _crissa_app_window(tabs: list) -> int | None:
    """The windowId of Crissa's --app window: the first one Brave made (lowest
    id). Fills _WIN_OF for every page. None when CDP will not say."""
    try:
        import websocket
        with crissa_egress.urlopen(f"{CRISSA_CDP}/json/version", 2, "the Wall") as r:
            ver = json.loads(r.read().decode() or "{}")
        wsu = ver["webSocketDebuggerUrl"]
        ws = websocket.create_connection(wsu, timeout=4, suppress_origin=True,
                                         **crissa_egress.ws_opts(wsu, "the Wall"))
    except Exception:
        return None
    try:
        _WIN_OF.clear()
        for i, t in enumerate(t for t in tabs if t.get("type") == "page"):
            ws.send(json.dumps({"id": i + 1, "method": "Browser.getWindowForTarget", "params": {"targetId": t["id"]}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == i + 1:
                    break
            wid = (msg.get("result") or {}).get("windowId")
            if wid is not None:
                _WIN_OF[t["id"]] = wid
        return min(_WIN_OF.values()) if _WIN_OF else None
    except Exception:
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _crissa_window_xid() -> str | None:
    """The crissa-display Brave window to raise, by WM_CLASS — the one that is
    not HIDDEN when there are several (after a keeper restart Brave has had
    two: a minimised one and the live one), and never a cached id from a
    window that has since gone."""
    found: list[str] = []
    for w in re.findall(r"0x[0-9a-f]+", _xprop(["-root", "_NET_CLIENT_LIST"])):
        if "crissa-display" in _xprop(["-id", w, "WM_CLASS"]):
            found.append(w)
    for w in found:
        if "_NET_WM_STATE_HIDDEN" not in _xprop(["-id", w, "_NET_WM_STATE"]):
            return w
    return found[0] if found else None


def crissa_window_state(state: str = "fullscreen", target_id: str | None = None) -> str:
    """Crissa's Brave window, full screen like the wallpaper.

    the User 2026-09-07: "Make the tabbed CP and Composer open to full screen just
    like Wallpaper if you can … They can pull up browser as a separate thing
    if they want it." Same lever the kiosk uses (Browser.setWindowBounds, not
    --kiosk: User 2026-08-09, a Wayland kiosk window could not be minimised).
    Reversible: F11, or the same call with "normal".

    The window is the one holding the tab that was just fronted
    (target_id, or _LAST_FRONT) — not whichever page CDP lists first: with
    two Brave windows that was the minimised one, and the CP came up in a
    window nobody could see (the User 2026-09-08: "CP opens in Brave vs
    fullscreen").
    """
    try:
        import websocket  # in the vllm env, same as the :8099 server
    except Exception:
        return "no websocket"
    try:
        with crissa_egress.urlopen(f"{CRISSA_CDP}/json/list", 2, "the Wall") as r:
            tabs = json.loads(r.read().decode() or "[]")
        want_id = target_id or _LAST_FRONT.get("id")
        page = next((t for t in tabs if t.get("type") == "page" and t.get("id") == want_id), None) \
            or next((t for t in tabs if t.get("type") == "page"), None)
        if not page:
            return "no page"
        with crissa_egress.urlopen(f"{CRISSA_CDP}/json/version", 2, "the Wall") as r:
            ver = json.loads(r.read().decode() or "{}")
        wsu = ver["webSocketDebuggerUrl"]
        ws = websocket.create_connection(wsu, timeout=6, suppress_origin=True,
                                         **crissa_egress.ws_opts(wsu, "the Wall"))
    except Exception as e:  # noqa: BLE001
        return f"cdp: {type(e).__name__}"
    try:
        def send(i, method, params):
            ws.send(json.dumps({"id": i, "method": method, "params": params}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == i:
                    return msg
        got = send(1, "Browser.getWindowForTarget", {"targetId": page["id"]})
        win = (got.get("result") or {}).get("windowId")
        if win is None:
            return "no window"
        now = ((got.get("result") or {}).get("bounds") or {}).get("windowState")
        if now == state:
            return f"already {state}"
        if now in ("minimized", "maximized") and state == "fullscreen":
            send(2, "Browser.setWindowBounds", {"windowId": win, "bounds": {"windowState": "normal"}})
        send(3, "Browser.setWindowBounds", {"windowId": win, "bounds": {"windowState": state}})
        return state
    except Exception as e:  # noqa: BLE001
        return f"bounds: {type(e).__name__}"
    finally:
        try:
            ws.close()
        except Exception:
            pass


def front_crissa_tab(url: str, shape=None) -> str:
    """the User 2026-09-06: "have our buttons just front Crissa's windows with
    controls? prevent multiple window bloat". Crissa's Brave homes to Krew,
    Composer and the Control Panel as tabs; a rail key activates THAT tab
    (CDP /json/activate — no new tab unless the page is missing) and raises
    the window. If Crissa's browser is not up, the User's own Brave opens it."""
    try:
        with crissa_egress.urlopen(f"{CRISSA_CDP}/json/list", 2, "the Wall") as r:
            tabs = json.loads(r.read().decode() or "[]")
    except Exception:
        open_url(url)
        return "user-brave (crissa browser down)"
    key = url.rstrip("/")
    # The keeper's Brave is one tabbed window now (Wall tab 1, then the
    # User's startup pages — "Tabs, not --app"), so the page we want is
    # usually IN the first window. Excluding that window (the old --app rule)
    # made every press miss and open a fresh tab (the User 2026-09-08: "kept
    # opening new one"). Prefer the keeper's window; accept any window that
    # has the page; open one only when no window does.
    first_win = _crissa_app_window(tabs)
    hits = [t for t in tabs if t.get("type") == "page" and str(t.get("url") or "").rstrip("/").startswith(key)]
    hits.sort(key=lambda t: 0 if (first_win is not None and _WIN_OF.get(t.get("id")) == first_win) else 1)
    hit = hits[0] if hits else None
    how = "activated"
    try:
        if hit is None:
            req = urllib.request.Request(f"{CRISSA_CDP}/json/new?{url}", method="PUT")
            with crissa_egress.urlopen(req, 4, "the Wall") as r:
                hit = json.loads(r.read().decode() or "{}")
            how = "opened"
        else:
            crissa_egress.urlopen(f"{CRISSA_CDP}/json/activate/{hit['id']}", 2, "the Wall").read()
        _LAST_FRONT["id"] = hit.get("id")
    except Exception as e:  # noqa: BLE001
        return f"cdp failed: {type(e).__name__}"
    xid = _crissa_window_xid()
    if xid and shape is not None:
        try:
            shape.activate(int(xid, 16))
        except Exception as e:  # noqa: BLE001
            return f"{how}, raise failed: {type(e).__name__}"
    return f"{how}{' + raised' if xid else ' (window not found)'}"


def _xprop(args: list[str]) -> str:
    try:
        return subprocess.run(["xprop"] + args, capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception as e:
        return f"xprop failed: {e}"


def _ewmh_pin_desktop(frame_id: str):
    """Pin TYPE_DESKTOP (+BELOW, STICKY, SKIP_TASKBAR, SKIP_PAGER) on the frame
    BEFORE the first map. Mutter reads the type at map; the state hints are for
    any WM that keeps them (Mutter drops BELOW on TYPE_DESKTOP — measured)."""
    if not shutil.which("xprop"):
        log("xprop not installed — cannot pin _NET_WM_WINDOW_TYPE_DESKTOP (apt install x11-utils)")
        return
    _xprop(["-id", frame_id, "-f", "_NET_WM_WINDOW_TYPE", "32a", "-set", "_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_DESKTOP"])
    _xprop(["-id", frame_id, "-f", "_NET_WM_STATE", "32a", "-set", "_NET_WM_STATE",
            "_NET_WM_STATE_BELOW,_NET_WM_STATE_STICKY,_NET_WM_STATE_SKIP_TASKBAR,_NET_WM_STATE_SKIP_PAGER"])
    log(f"pinned on {frame_id}: {_xprop(['-id', frame_id, '_NET_WM_WINDOW_TYPE'])}")


def _ewmh_report(root, frame_id: str, W: int, H: int):
    """After the map: what did the WM do with us? One journal line, measured."""
    typ = _xprop(["-id", frame_id, "_NET_WM_WINDOW_TYPE"]) if shutil.which("xprop") else "xprop n/a"
    state = _xprop(["-id", frame_id, "_NET_WM_STATE"]) if shutil.which("xprop") else ""
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    ok = ("DESKTOP" in typ) and x == 0 and y == 0
    log(f"ewmh: {typ} | {state} | at {x},{y} {w}x{h} -> {'OK' if ok else 'NOT A DESKTOP WINDOW — do not soak this'}")
    if (x, y) != (0, 0):
        root.geometry(f"{W}x{H}+0+0")   # a WM that framed us placed us under its bar; push back once
    root.lower()


HOTSPOT_URL = {
    "ft": crissa_home.composer_url(),       # the Composer: this machine's (config), else the store (2026-10-10)
    "dial": crissa_home.composer_url(),
    "mode": f"{HOUSE}/panel",
    "secure": f"{HOUSE}/panel",
    "camera": f"{PRESENCE}/visor",
}

ORACLE_ANIM_S = 0.28   # kiosk .dial { transition: transform .25s }
# RENDER SHOW on the wall (the User 2026-09-13): a still holds this long in the
# centre box, a clip plays once (capped), both fade in and out, no sound.
SHOW_STILL_S = 12.0
SHOW_FADE_S = 0.5
SHOW_CLIP_MAX_S = 20.0


def run_preview(out: str, size: tuple[int, int] | None):
    """One frame from the live endpoints, no window. Verification without touching the glass."""
    W, H = size or (2560, 1440)
    frame = http_get(f"{FEED}/frame.jpg")
    cam = http_get(f"{HOUSE}/frame")
    house = http_json(f"{HOUSE}/v1/house") or {}
    pres = http_json(f"{PRESENCE}/v1/presence") or {}
    state = http_json(f"{FEED}/v1/state") or {}
    log(f"preview: frame={'ok' if frame else 'MISSING'} camera={'ok' if cam else 'none'} "
        f"house={'ok' if house else 'MISSING'} presence={'ok' if pres else 'none'} "
        f"frame_no={state.get('frame_no', state.get('frames'))}")
    im = R.render(frame, cam, house, pres, W, H, stale=not state)
    im.save(out)
    log(f"wrote {out} ({W}x{H})")
    return 0


# beside the code, never the house checkout by name: a pack has its own tree (2026-09-28)
PORTRAIT_DIR = REPO / "display" / "register"


def _portrait_preload(into: dict, cache_dir) -> None:
    """Decode the portrait loop and (when present) Crissa's intro into RGB images, and extract the
    intro's audio — so the film's hand-off starts on frames already in memory."""
    import cv2 as _cv2
    from PIL import Image as _Image

    def frames(path: Path) -> list:
        out = []
        try:
            cap = _cv2.VideoCapture(str(path))
            while True:
                ok, fr = cap.read()
                if not ok:
                    break
                out.append(_Image.fromarray(fr[:, :, ::-1]))
            cap.release()
        except Exception as e:  # noqa: BLE001
            log(f"portrait preload {path.name}: {type(e).__name__}: {e}"[:140])
        return out

    loop = frames(PORTRAIT_DIR / "crissa-portrait.mp4")
    if len(loop) > 12:      # dissolve the loop's wrap in memory (its seam is 0.94)
        for q in range(1, 7):
            loop[-7 + q] = _Image.blend(loop[-7 + q], loop[0], q / 7.0)
    into["loop"] = loop
    intro = PORTRAIT_DIR / "crissa-portrait-intro.mp4"
    if intro.is_file():
        into["intro"] = frames(intro)
        try:
            meta = json.loads(intro.with_suffix(".json").read_text(encoding="utf-8"))
            into["done_at_s"] = float(meta.get("done_at_s") or 0.0)
        except Exception:  # noqa: BLE001
            into["done_at_s"] = 0.0
        try:
            import subprocess as _sp
            wav = Path(cache_dir) / "crissa-portrait-intro.wav"
            if not wav.is_file() or wav.stat().st_mtime < intro.stat().st_mtime:
                _sp.run(["ffmpeg", "-v", "error", "-y", "-i", str(intro), "-vn", "-ac", "1", "-ar", "48000", str(wav)],
                        capture_output=True, timeout=60)
            into["intro_wav"] = wav if wav.is_file() else None
        except Exception:  # noqa: BLE001
            into["intro_wav"] = None
    into["ready"] = True


def _play_speech_wav(wav) -> None:
    """Crissa's intro line on the house speech sink at full volume (the Init players' rule)."""
    if not wav:
        return
    try:
        import subprocess as _sp
        from audio_sink import speak_sink, boost_paplay
        sink = speak_sink()
        _sp.Popen(["paplay", f"--device={sink}", str(wav)], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, start_new_session=True)
        threading.Thread(target=boost_paplay, args=(sink,), kwargs={"pct": 100, "wait_s": 2.0}, daemon=True).start()
    except Exception as e:  # noqa: BLE001
        log(f"portrait intro audio: {type(e).__name__}: {e}"[:120])


def run_glass(mode: str, fps: float, size: tuple[int, int] | None) -> int:
    import tkinter as tk
    import _tkinter                       # dooneevent's flags (INPUT_FIRST)
    from PIL import ImageTk

    if mode == "wallpaper" and crissa_home.is_house():
        # Disable crissa-display autostart. Do not stop a Brave the User or Crissa
        # already opened. Wallpaper off is what enables+starts it again.
        # THE HOUSE ONLY (2026-10-10): a pack's desktop-background Wall (its default since the .101 test) has no
        # house units to enable or stop and never touches the person's dock settings or browser.
        threading.Thread(target=glass_mode.claim_wallpaper, daemon=True).start()
    elif mode == "wallpaper":
        # a pack's desk Wall: Ubuntu's dock hides at the bottom and fits its keys while it runs, the person's own
        # values kept and put back when it closes (glass_mode.pack_dock_tidy, .101 test 13, 2026-10-10)
        threading.Thread(target=lambda: log("dock: %s" % glass_mode.pack_dock_tidy()), daemon=True).start()

    root = tk.Tk()
    root.title("house-wall")
    root.configure(bg="#10131A")
    W, H = size or (root.winfo_screenwidth(), root.winfo_screenheight())
    den0 = first_look(W, H)
    if den0 is not None:
        _glass_merge({"density": den0})     # this glass draws it now; the live take below posts it to the record
        _glass_done()
    g = R.Geom(W, H, density=_prefs()["density"])

    root.withdraw()
    root.geometry(f"{W}x{H}+0+0")
    if mode == "wallpaper":
        # Belt: Tk's own attribute. Braces: xprop on the WM frame before the
        # first map (below), read back after (see _ewmh_report).
        try:
            root.attributes("-type", "desktop")
        except tk.TclError as e:
            log(f"tk -type desktop not supported here ({e})")
        # NO re-lower on focus or map. Every root.lower() after the pin made
        # gnome-shell log `meta_window_set_stack_position_no_sync: assertion
        # 'window->stack_position >= 0' failed` (130 times on 2026-09-06, in
        # pairs, and at 12:54:26 exactly as the User pressed Secure and the desk
        # froze). A _NET_WM_WINDOW_TYPE_DESKTOP window is already kept at the
        # bottom by Mutter; the explicit lower is redundant and provokes it.
    else:
        root.attributes("-fullscreen", True)
        try:
            root.config(cursor="none")     # only the glass hides the cursor, never the wallpaper
        except tk.TclError:
            pass

    cv = tk.Canvas(root, width=W, height=H, highlightthickness=0, bd=0, bg="#10131A")
    cv.pack(fill="both", expand=True)
    bg_item = cv.create_image(0, 0, anchor="nw")
    roll_item = cv.create_image(0, 0, anchor="nw")   # under the vitals/dial/PiP, like .ribbon z2
    hud_item = cv.create_image(0, 0, anchor="nw")
    door_item = cv.create_image(0, 0, anchor="nw")   # #doorhalo, breathing at 5 Hz over the dial
    scan_item = cv.create_image(0, 0, anchor="nw")   # the visor sweep on the big camera, 5 Hz
    lobe_item = cv.create_image(0, 0, anchor="nw")   # the dial's lamps, 5 Hz, over the HUD
    beam_item = cv.create_image(0, 0, anchor="nw")   # the Speaker row's beam number, 5 Hz
    ft_item = cv.create_image(0, 0, anchor="nw")
    cinema_item = cv.create_image(0, 0, anchor="nw")    # first boot: Crissa's HD intro painted BY the wall (full → the centre box → folded into the film); under the init glass so CANCEL stays
    show_item = cv.create_image(0, 0, anchor="nw")      # Render: Lila's finished still / clip in the centre box, silent (show_worker)
    init_item = cv.create_image(0, 0, anchor="nw")      # the first-boot wizard's plate on the film (eyebrow, dots, title, pills)
    register_item = cv.create_image(0, 0, anchor="nw")  # Register on the film: LOOK HERE, the sentence, CANCEL (5 Hz, never fades)
    chrome_item = cv.create_image(0, 0, anchor="nw")  # on the glass, above HUD/FT
    roll = R.Roll()
    photos = {}   # keep PhotoImage refs alive

    st = State()
    for fn, args in ((t_frames, (st, fps)), (t_state, (st,)), (t_house, (st,)), (t_camera, (st,)), (t_sound, (st,)), (t_register, (st,)), (t_init, (st,)), (t_glass, (st,))):
        threading.Thread(target=fn, args=args, daemon=True).start()
    if REST_ON:
        threading.Thread(target=t_rest, args=(st,), daemon=True, name="wall-rest").start()

    out_q: queue.Queue = queue.Queue(maxsize=4)   # two producers: the film and the HUD
    collapsed0 = 0.05 if _prefs().get("oracle_dot") else 1.0
    oracle_anim = {"src": collapsed0, "dst": collapsed0, "t0": 0.0, "scale": collapsed0}
    # Where the pointer is, when this desktop gives us one. Its own box because
    # the workers below read it and `ui` is not bound until the glass is up.
    ptr_state = {"xy": None, "at": 0.0}
    # the band over the windows (HOT_STRIP): shown, its rect, the last full
    # layers (for its pixels) and the hooks the idle loop calls into
    hot = {"shown": False, "inside": False, "rect": None, "photo": None, "repaint": None, "hide": None}
    last_im: dict = {}

    speed = {"bg": [], "hud": [], "ft": [], "loop": [], "last": 0.0, "every": 8.0,
             "made_full": 0, "made_patch": 0, "made_same": 0, "bg_whole": [], "child": [],
             "glass": 0, "glass_patch": 0,      # what the Tk thread pasted (round 12)
             "drawn": 0, "draw": [], "xrt": [], "xrt_at": 0.0}   # what it drew into the window, how long a redraw
                                                                  # took, and the X server's answer (round 15)
    redraw = {"armed": False}
    pick = {"at": 0.0, "ts": 0.0}         # the machine card's density pick, taken once (TAKE_PICK)
    # which bottom window is enlarged: None | "camera" | "ft" (the User 2026-09-06:
    # "neither of the bottom windows enlarge when clicked")
    big = {"which": None, "dx": 0, "dy": 0}    # dx/dy: the opened FT pop's drag, glass px from centre
    # the User dragging the opened FT pop (the User 2026-09-10: "let us drag it
    # ... tucked up top corner"): press on it, B1-Motion moves it, release
    # without a move is the click that closes it
    drag = {"on": False, "x0": 0, "y0": 0, "dx0": 0, "dy0": 0, "moved": False}
    # the User dragging HER while a document is up (the User 2026-10-09): press on bare glass, move, release;
    # the spot is kept for the next document (doc_placement). o0 = origin at the press, live = origin now.
    docdrag = {"on": False, "x0": 0, "y0": 0, "o0": None, "live": None, "moved": False}

    def _her_origin(house_f):
        if docdrag["on"] and docdrag["live"] is not None:
            return docdrag["live"]
        return doc_placement.origin(house_f)

    frame_said = {"why": None}

    def _her_frame(house_f, own, hz, pref, clip_key=None):
        """(shift, frame) for this picture (lib/wall_frame.py, the User 2026-10-12). The kiosk frame: today's shift
        and no frame. The whole frame: no shift and the frame, AR on or off (round 14) - except under a page's slide,
        which keeps today's kiosk path. A crop slide (crissa-render-shift.json) has nothing to move in the whole film,
        so it is ignored there, said once."""
        shift = _her_origin(house_f) if own else None
        if not own or wall_frame.mode() != "whole":
            why = None
            frame = None
        elif shift is not None and (doc_placement.doc_up(house_f) or (docdrag["on"] and docdrag["live"] is not None)):
            why = "a page is up: her picture slides as today (kiosk frame)"
            frame = None
        elif shift is not None and doc_placement.home_placed(house_f):
            why = "dragged with no page up: her picture slides (kiosk frame)"
            frame = None
        else:
            why = "crop slide %s ignored under the whole frame" % (shift,) if shift is not None else None
            with st.lock:
                fp = st.fp_by_clip.get(clip_key) if (FRAME_FP_BY_CLIP and clip_key) else None
                if fp is None:
                    fp = (st.wall_frame or {}).get("face_point")
            frame = wall_frame.frame_for(own, pref.get("frame_zoom", 1.0),
                                         tuple(fp) if isinstance(fp, (list, tuple)) else fp)
            shift = None
        if why != frame_said["why"]:
            frame_said["why"] = why
            if why:
                log("frame: " + why)
        return shift, frame

    # the small FaceTime tile, dragged anywhere and remembered (the User 2026-10-09): press, move, release; a
    # release without a move is the click that opens it, as before. b = the tile's top-left at the press.
    tiledrag = {"on": False, "x0": 0, "y0": 0, "bx": 0, "by": 0, "live": None, "moved": False,
                "held": None}   # round 37: a pack's drop, (spot, card) - kept until the next card or page-down
    # the letter's scroll (the User 2026-10-09): the first wrapped line shown, reset when the page changes; Crissa's
    # tool asks through doc_placement's command file (seq = the last command applied); painted = what the HUD shows
    letter = {"key": None, "line": 0, "seq": None, "mtime": None, "painted": None}

    def _page_up(house_p) -> bool:
        return bool(R.letter_body((house_p or {}).get("screen") or {})[1])

    def _place_keys(pref: dict) -> dict:
        """The tile's spot (live while dragged, else the saved one) and, while a page is up, the letter's left edge
        that the opened tile must clear."""
        with st.lock:
            card_p0 = st.layout_card
        if home_fixed_on():
            # round 37 (the User: "can we fix home as always home"): her corner is the kit's; a drag holds her where she
            # was dropped only until the next card or page-down, and nothing is saved
            held = tiledrag.get("held")
            if held is not None and held[1] != card_p0:
                tiledrag["held"] = held = None
            out = {"ft_tile": tiledrag["live"] if (tiledrag["on"] and tiledrag["live"] is not None)
                   else (held[0] if held is not None else None)}
        else:
            out = {"ft_tile": tiledrag["live"] if (tiledrag["on"] and tiledrag["live"] is not None)
                   else pref.get("ft_tile")}
        with st.lock:
            house_p = st.house
        if os.environ.get("HOUSE_FT_BIG_CLEARS_PAGE", "1") != "0":
            if _page_up(house_p):
                out["page_left"] = R.letter_rect(R.Geom(W, H))[0]
        with st.lock:
            lay_p = dict(st.layout or {})
            card_p = st.layout_card
        cb = layout_tile_box(lay_p, W, H) if render_beside_card_on() else None   # round 35: the player's box
        if cb and home_fixed_on() and (tiledrag["on"] and tiledrag["moved"] or tiledrag.get("held") is not None):
            cb = None                                    # round 37: dragged beside this card: where he puts her, for it
        if cb and beside_click_wins_on() and beside.get("sent_home") is not None:
            if beside["sent_home"] == ("card", card_p):
                cb = None                                # round 36: his click sent her home for this card
            else:
                beside["sent_home"] = None               # the next card: beside it again
        if cb:
            if beside["key"] != (W, H) or beside["box"] != cb:
                beside.update(key=(W, H), box=cb)
                log("her tile beside the card: %s on a %dx%d glass (the player's layout %s)" % (cb, W, H, lay_p.get("box")))
            out["ft_card_box"] = cb                      # round 34: her render beside the card
            out["ft_card_right"] = beside_picture_right_on()   # round 37: her picture against the page
        elif beside["key"] is not None:
            beside.update(key=None, box=None)
            log("her tile back to its corner (%s)" % ("his click, until the next card" if beside.get("sent_home") is not None
                                                     else "the card is down"))
        return out
    beside = {"key": None, "box": None, "sent_home": None}   # round 34: the card box; round 36: the card his click sent her home from
    dock_pick = {"i": None, "wins": [], "hold": None}      # the dock's open window chooser (tile index, windows, close-after)
    # the corner's grow (camera / ft): k 0..1 on the page's .32 curve; t0=0 = settled at dst
    big_anim = {"src": 0.0, "dst": 0.0, "t0": 0.0}
    # the GNOME top bar's visible bottom edge (house-shell Poll) and the rail's ride on it
    panel = {"bottom": 0, "flag": threading.Event()}
    rail_anim = {"src": 0.0, "dst": 0.0, "t0": 0.0, "job": None}
    def _big_k(now: float) -> float:
        if not big_anim["t0"]:
            return float(big_anim["dst"])
        t = min(1.0, (now - big_anim["t0"]) / BIG_ANIM_S)
        return big_anim["src"] + (big_anim["dst"] - big_anim["src"]) * _ease_out_expo(t)

    def _big_prefs(pref: dict, now: float) -> dict:
        bk = _big_k(now)
        return dict(pref, ft_big=(big["which"] == "ft" and bk >= 0.999),
                    ft_k=(bk if big["which"] == "ft" else None),
                    ft_dx=big["dx"], ft_dy=big["dy"], **_place_keys(pref))

    dark_im: dict = {"size": None, "im": None}
    dark_state = {"seen_up": False, "t0": time.time()}

    def _first_boot_dark() -> bool:
        """Round 38: a pack's first boot (no init.json yet) holds the glass dark until her Setup window has been up once;
        never once Setup says it is neither needed nor running, never past FIRST_BOOT_DARK_CAP_S. Kill
        CRISSA_FIRST_BOOT_DARK=0."""
        if dark_state["seen_up"] or crissa_home.is_house():
            return False
        if os.environ.get("CRISSA_FIRST_BOOT_DARK", "1").strip().lower() in ("0", "off", "false", "no"):
            return False
        if time.time() - dark_state["t0"] > FIRST_BOOT_DARK_CAP_S or (crissa_home.HOME / "init.json").exists():
            dark_state["seen_up"] = True
            return False
        with st.lock:
            d_ = dict(st.init or {})
        if d_ and not d_.get("needed") and str(d_.get("status") or "") != "running":
            dark_state["seen_up"] = True
            return False
        if ((d_.get("browser") or {}).get("window") or {}).get("up"):
            dark_state["seen_up"] = True
            log("first boot: her Setup window is up; the glass shows the room")
            return False
        return True

    def _walkin_hides_tile() -> bool:
        """Round 37 (.101 round 36, the User: "on the walk in let's minimize the render window"): from Lila's handover
        until Crissa is seated, the Setup snapshot's `ending` says so (lib/crissa_init.ending_view)."""
        with st.lock:
            e_ = (st.init or {}).get("ending")
        return isinstance(e_, dict) and bool(e_.get("on")) and not e_.get("seated")

    def _init_running() -> bool:
        with st.lock:
            return str((st.init or {}).get("status") or "") == "running"

    def _portrait_leaving() -> bool:
        """INIT V2: the portrait stays docked through the opener and Lila's voice ID, and leaves after."""
        with st.lock:
            snap_ = dict(st.init or {})
        return bool(snap_.get("v2")) and str(snap_.get("step_id") or "") not in ("opener", "voice_id")

    def cinema_worker():
        """FIRST BOOT: Crissa's intro, painted by the wall (the User 2026-09-12 pm;
        docs/designs/FIRST-BOOT-INTRO-20260912.md). The welcome plate names
        the file; this thread decodes it, paints it full screen on a dark
        ground, eases it down to the centre room-view box at the pause
        before "Here's what happens next", and when the file ends hands the
        box to Lila (the live feed — her listening loop on the conductor)
        and posts `cinema_done`. When the wizard flips cinema_state to
        "min" (her first line starts) the box folds into the film. Never a
        second window: mpv over the Tk wall broke the stacking."""
        import subprocess as _sp
        import cv2 as _cv2
        cache = Path.home() / ".cache" / "house-wall" / "cinema"
        cache.mkdir(parents=True, exist_ok=True)
        played_for = None           # the file we already ran this wizard start
        while not st.stop.is_set():
            with st.lock:
                snap = dict(st.init or {})
            running = str(snap.get("status") or "") == "running"
            plate = snap.get("plate") if isinstance(snap.get("plate"), dict) else {}
            # v1's welcome, or v2's opener (the User 2026-10-04): the same film, the same portrait after it
            cfile = str(plate.get("cinema") or "") if running and str(snap.get("step_id") or "") in ("welcome", "opener") else ""
            cstate = str(plate.get("cinema_state") or "")
            if not running:
                played_for = None
            if not cfile or cstate not in ("playing",) or played_for == cfile:
                time.sleep(0.25)
                continue
            played_for = cfile
            src = Path(cfile)
            wav = cache / (src.stem + ".wav")
            try:
                if not wav.is_file() or wav.stat().st_mtime < src.stat().st_mtime:
                    _sp.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "48000", str(wav)],
                            capture_output=True, timeout=120)
            except Exception as e:  # noqa: BLE001
                log(f"cinema: wav {type(e).__name__}: {e}"[:140])
            box_at = None
            side = src.with_suffix(".json")          # cinema-join.py: box_at = the first seam
            try:
                if side.is_file():
                    box_at = float(json.loads(side.read_text()).get("box_at") or 0) or None
            except Exception:
                box_at = None
            if box_at is None:
                box_at = R.cinema_box_at(wav) if wav.is_file() else None
            cap = _cv2.VideoCapture(str(src))
            fps = float(cap.get(_cv2.CAP_PROP_FPS) or 24.0)
            n_frames = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT) or 0)
            dur = n_frames / fps if n_frames else 0.0
            if box_at is None:
                box_at = dur * 0.6 if dur else 10.0
            log(f"cinema: {src.name} {n_frames}f @{fps:.0f} box at {box_at:.1f}s")
            # THE PORTRAIT IS WARM BEFORE THE FILM ENDS (the User 2026-09-14: "preload those in a cache to
            # avoid the noticeable seams"): Crissa's "Here's Lila" intro and the portrait loop decode on
            # their own thread while her film plays, so the hand-off never waits on a decoder.
            portrait_cache: dict = {}
            threading.Thread(target=_portrait_preload, args=(portrait_cache, cache), name="portrait-preload", daemon=True).start()
            audio = None
            try:
                if wav.is_file():
                    # CRISSA'S FILM ON THE HOUSE SPEECH SINK, AT FULL VOLUME (the User 2026-09-14:
                    # "crissa's first word dropped again"): a bare paplay started at WirePlumber's
                    # remembered duck volume on the default sink, out of the echo canceller's
                    # hearing. speak_sink + boost_paplay, as house-speak and clip_audio do.
                    try:
                        from audio_sink import speak_sink as _speak_sink, boost_paplay as _boost
                        _sink = _speak_sink()
                    except Exception:  # noqa: BLE001
                        _sink, _boost = (crissa_home.house_value("aec_sink") or "@DEFAULT_SINK@"), None
                    audio = _sp.Popen(["paplay", f"--device={_sink}", str(wav)])
                    if _boost is not None:
                        threading.Thread(target=_boost, args=(_sink,), kwargs={"pct": 100, "wait_s": 2.0}, daemon=True).start()
            except Exception:
                audio = None
            t0 = time.time()
            idx = -1
            last = None
            last_fr = None
            ended = False
            while not st.stop.is_set() and _init_running():
                t = time.time() - t0
                want = int(t * fps)
                if n_frames and want >= n_frames:
                    ended = True
                    break
                # advance to the frame the clock wants with grab() (cheap:
                # no decode), then decode that one with retrieve(). A
                # retrieve() with no grab before it returns False — the
                # first cut did exactly that on frame 0 and the picture
                # "ended" under a second while her voice played on (the User
                # 2026-09-12: "cutting crissa to not even 1 sec clip").
                while idx < want:
                    if not cap.grab():
                        ended = True
                        break
                    idx += 1
                if ended:
                    break
                ok, fr = cap.retrieve()
                if not ok or fr is None:
                    ended = True
                    break
                k = 0.0 if t < box_at else min(1.0, (t - box_at) / BIG_ANIM_S)
                k = _ease_out_expo(k) if 0.0 < k < 1.0 else k
                im = R.cinema_frame(R.Image.fromarray(_cv2.cvtColor(fr, _cv2.COLOR_BGR2RGB)), W, H, k)
                try:
                    out_q.put({"cinema": (im, (0, 0))}, timeout=0.2)
                except queue.Full:
                    pass
                last_fr = fr           # her last frame: the box crossfades from it into Lila (converted once, after)
                # pace on the file's own clock
                nxt = t0 + (idx + 1) / fps
                d = nxt - time.time()
                if d > 0:
                    time.sleep(min(d, 0.05))
            cap.release()
            if last_fr is not None:
                last = R.Image.fromarray(_cv2.cvtColor(last_fr, _cv2.COLOR_BGR2RGB))
            if audio is not None:
                try:
                    if not ended:
                        audio.terminate()
                    else:
                        audio.wait(timeout=5)
                except Exception:
                    pass
            if not _init_running():
                try:
                    out_q.put({"cinema": None}, timeout=0.2)
                except queue.Full:
                    pass
                continue
            # the file ended: the box is Lila's now — the live feed (her
            # listening loop, cued by the wizard on cinema_done) — until the
            # wizard says "min"; then the box folds into the film
            # THE PORTRAIT (the User 2026-09-12 pm: "swing that to the bottom so
            # she is in the room with Lila until she appears in her office").
            # Her last frame crossfades to the portrait loop in the centre box,
            # the ground fades so Lila's plate is the film, the box swings
            # down to the bottom-left slot and loops there through Lila's
            # whole part; at the wrap it fades out.
            t_wait = time.time()
            while not portrait_cache.get("ready") and time.time() - t_wait < 3.0:
                time.sleep(0.02)
            if not portrait_cache.get("ready"):
                _portrait_preload(portrait_cache, cache)          # the thread did not finish: decode here
            port: list = list(portrait_cache.get("loop") or [])
            lead: list = list(portrait_cache.get("intro") or [])
            done_at = float(portrait_cache.get("done_at_s") or 0.0) if lead else 0.0
            cinema_posted = False
            if lead:
                log(f"cinema: ended -> {crissa_home.her_name()}'s portrait intro ({len(lead)}f, 'Here's Lila'), cinema_done at {done_at:.1f}s")
                _play_speech_wav(portrait_cache.get("intro_wav"))
            else:
                log("cinema: ended -> cinema_done; the portrait swings to the slot, Lila is the film")
                _init_post("cinema_done", {}, timeout=10)
                cinema_posted = True

            def _portrait_at(tsec: float):
                i = int(tsec * 24)
                if lead and i < len(lead):
                    return lead[i]
                if port:
                    return port[(i - len(lead)) % len(port)]
                return last if last is not None else R.Image.new("RGB", (480, 488), R.CINEMA_GROUND)
            box = R.cinema_rect(W, H, 1.0)
            aspect = (port[0].size[0] / port[0].size[1]) if port else (480 / 488)
            slot = R.cinema_slot_rect(W, H, aspect)
            X_S, TRAVEL_S, FADE_S = 0.3, 0.6, 0.5     # the User 2026-09-14: "it takes time to minimize" (was 0.6, 1.0)
            t_end = time.time()
            docked_logged = False
            while not st.stop.is_set() and _init_running() and not _portrait_leaving():
                tt = time.time() - t_end
                if not cinema_posted and tt >= done_at:
                    # her line has ended: Lila answers now ("Thanks, Crissa" after "Here's Lila")
                    _init_post("cinema_done", {}, timeout=10)
                    cinema_posted = True
                pfr = _portrait_at(tt)
                if tt < X_S:
                    kx = _ease_out_expo(tt / X_S)
                    a = R.cinema_frame_rect(last if last is not None else pfr, W, H, box, 255)
                    b = R.cinema_frame_rect(pfr, W, H, box, 255)
                    im = R.Image.blend(a, b, kx)
                elif tt < X_S + TRAVEL_S:
                    kt = _ease_out_expo((tt - X_S) / TRAVEL_S)
                    im = R.cinema_frame_rect(pfr, W, H, R.rect_between(box, slot, kt), int(255 * (1.0 - kt)))
                else:
                    if not docked_logged:
                        docked_logged = True
                        log("cinema: portrait docked bottom-left")
                    im = R.cinema_frame_rect(pfr, W, H, slot, 0)
                try:
                    out_q.put({"cinema": (im, (0, 0))}, timeout=0.2)
                except queue.Full:
                    pass
                time.sleep(1.0 / 24 if tt < X_S + TRAVEL_S else 1.0 / 12)
            # INIT V2: Crissa walks out of her portrait (the clip, in the slot, when it is filmed); its last frame is
            # the empty curtain, which the fade below takes away. Then the wizard hears walk_off_done.
            leave = _portrait_leaving()
            if leave and port:
                with st.lock:
                    pl = dict((st.init or {}).get("plate") or {})
                clip_ = str(pl.get("clip") or "") if pl.get("portrait") == "walk_off" else ""
                walk: list = []
                if clip_:
                    try:
                        cap_ = _cv2.VideoCapture(clip_)
                        while True:
                            ok_, fr_ = cap_.read()
                            if not ok_:
                                break
                            walk.append(R.Image.fromarray(_cv2.cvtColor(fr_, _cv2.COLOR_BGR2RGB)))
                        cap_.release()
                    except Exception as e:  # noqa: BLE001
                        log(f"cinema: walk-off decode {type(e).__name__}: {e}"[:140])
                        walk = []
                if walk:
                    log(f"cinema: {crissa_home.her_name()} walks out of her portrait ({len(walk)}f)")
                    for fr_ in walk:
                        if st.stop.is_set() or not _init_running():
                            break
                        try:
                            out_q.put({"cinema": (R.cinema_frame_rect(fr_, W, H, slot, 0), (0, 0))}, timeout=0.2)
                        except queue.Full:
                            pass
                        time.sleep(1.0 / 24)
                    port = [walk[-1]]
                    lead = []
                    t_end = time.time()
            # the wrap (or CANCEL): the portrait fades out of the slot
            t_f = time.time()
            while not st.stop.is_set() and port and time.time() - t_f < FADE_S:
                kf = (time.time() - t_f) / FADE_S
                pfr = _portrait_at(time.time() - t_end)
                im = R.cinema_frame_rect(pfr, W, H, slot, 0, int(255 * (1.0 - kf)))
                try:
                    out_q.put({"cinema": (im, (0, 0))}, timeout=0.2)
                except queue.Full:
                    pass
                time.sleep(1.0 / 24)
            try:
                out_q.put({"cinema": None}, timeout=0.2)
            except queue.Full:
                pass
            log(f"cinema: portrait faded — the wall is {crissa_home.her_name()}'s")
            if leave:
                _init_post("walk_off_done", {}, timeout=10)

    def _letter_scroll(house_s):
        """The letter's scroll for this paint (the User 2026-10-09): back to the top when the page changes, Crissa's
        newest command applied once, the line handed to the painter as screen._letter_line."""
        scr_ = (house_s or {}).get("screen") or {}
        title_, body_ = R.letter_body(scr_)
        if not body_ or not doc_placement.scroll_enabled():
            letter.update(key=None, line=0)
            return house_s
        key_ = hash((title_, body_))
        if key_ != letter["key"]:
            letter.update(key=key_, line=0)
        try:
            mt_ = doc_placement.scroll_path().stat().st_mtime
        except OSError:
            mt_ = None
        if mt_ is not None and mt_ != letter["mtime"]:
            letter["mtime"] = mt_
            cmd_ = doc_placement.read_scroll()
            if cmd_ and cmd_.get("seq") != letter["seq"]:
                first_seen = letter["seq"] is None and float(mt_) < START_TS
                letter["seq"] = cmd_.get("seq")
                if not first_seen:      # a command left from before this process started is not a new request
                    lay_ = R.LETTER
                    letter["line"] = doc_placement.apply_scroll(
                        letter["line"], cmd_["cmd"], int(lay_.get("total") or 0), int(lay_.get("per_page") or 1))
                    log(f"letter: crissa scroll {cmd_['cmd']} -> line {letter['line']}")
        return dict(house_s, screen=dict(scr_, _letter_line=letter["line"]))

    def hud_worker():
        """The HUD on its own thread (2026-09-06, on the way to 24 fps). It
        redraws once a second, on a house rev change, or at 30 Hz while the
        oracle animates — 26 ms under load that used to land inside the
        frame loop as a one-frame hitch every second."""
        last_hud = 0.0
        last_rev = None
        last_pref = None
        init_cleared = False        # the wizard owns the whole glass; the BAU HUD steps off
        reveal_t0 = 0.0             # the BAU wall fading in after the wizard's wrap
        gh = R.Geom(W, H, density=_prefs()["density"])
        while not st.stop.is_set():
            t0 = time.time()
            with st.lock:
                frame = st.frame
                house, pres, cam, spk, snd = st.house, st.presence, st.camera, st.speaker, st.sound
                toast = (st.toast_text, st.toast_until) if st.toast_until > t0 else ("", 0.0)
                init_running = str((st.init or {}).get("status") or "") == "running"
            # ONBOARDING OWNS THE GLASS. The band (top-left) and the mode chip
            # (top-right) are BAU furniture; over the first-boot film they clutter
            # the frame and land on the wall art (the User 2026-09-12: "the info on top
            # right overlaps our Wall art … thought we replaced that"). Clear the
            # HUD layer once when the wizard starts, repaint nothing until it ends.
            if init_running:
                if not init_cleared:
                    init_cleared = True
                    try:
                        out_q.put({"hud": R.Image.new("RGBA", (W, H), (0, 0, 0, 0))}, timeout=0.5)
                    except queue.Full:
                        pass
                time.sleep(0.25)
                continue
            if init_cleared:
                init_cleared = False
                last_hud = 0.0          # force a full repaint the instant the wizard is gone
                with st.lock:
                    done = str((st.init or {}).get("status") or "") == "done"
                # THE REVEAL (the User 2026-09-12 pm): when Lila wraps, the BAU wall
                # fades in over REVEAL_S — the first time the User sees it.
                # CANCEL/abort: instant, as before.
                reveal_t0 = time.time() if done else 0.0
            if reveal_t0 and (t0 - reveal_t0) < REVEAL_S:
                animating = True
            if snd or toast[0]:
                house = dict(house)
                if snd:
                    house["sound"] = snd          # the 5 Hz beam over the 2 s poll's copy
                if toast[0]:
                    house["_wall_toast"] = toast[0]
            pref = _prefs()
            # hover is the chrome's alone (white rings, ember tile border): the
            # HUD never repaints for the pointer, so nothing lands in stages
            pref_key = (pref, big["which"])
            if pref_key != last_pref:
                last_pref = pref_key
                gh = R.Geom(W, H, density=pref["density"])
                last_hud = 0.0
            rev = (house.get("screen") or {}).get("rev")
            hud_every = 0.1 if (big["which"] == "camera" or drag["on"] or tiledrag["on"]) else 1.0   # the big camera is live; the hotspot rides a drag
            house = _letter_scroll(house)
            if toast[0]:
                hud_every = min(hud_every, 0.2)
            elapsed = t0 - oracle_anim["t0"]
            k = min(1.0, elapsed / ORACLE_ANIM_S) if oracle_anim["t0"] else 1.0
            k = k * k * (3.0 - 2.0 * k)
            scale = oracle_anim["src"] + (oracle_anim["dst"] - oracle_anim["src"]) * k
            oracle_anim["scale"] = scale
            animating = abs(scale - oracle_anim["dst"]) > 0.012
            big_k = _big_k(t0)
            if big_anim["t0"] and (t0 - big_anim["t0"]) < BIG_ANIM_S + 0.1:
                animating = True            # the corner is on its way (paint every pass)
            if animating or (t0 - last_hud) >= hud_every or rev != last_rev or letter["line"] != letter["painted"]:
                letter["painted"] = letter["line"]
                if not animating:
                    last_hud = t0
                    last_rev = rev
                camim = R._open(cam) if cam else None
                t_hud = time.time()
                try:
                    im = R.render_hud(
                        house, pres, camim, gh, t0, frame is not None, st.stale(),
                        oracle_scale=scale,
                        vitals_folded=bool(pref.get("vitals_folded")),
                        pip_bar=bool(pref.get("pip_bar")),
                        mode_bar=bool(pref.get("mode_bar")),
                        speaker=spk,
                        ft_prefs=_big_prefs(pref, t0),
                        big=big["which"],
                        big_k=big_k,
                        ptr=None,
                    )
                except Exception as e:  # noqa: BLE001
                    log(f"hud render: {type(e).__name__}: {e}"[:160])
                    time.sleep(0.5)
                    continue
                speed["hud"].append((time.time() - t_hud) * 1000)
                speed["hud_n"] = speed.get("hud_n", 0) + 1
                if reveal_t0 and (t0 - reveal_t0) < REVEAL_S:
                    im = _with_alpha(im, _ease_out_expo(min(1.0, (t0 - reveal_t0) / REVEAL_S)))
                try:
                    out_q.put({"hud": im}, timeout=0.5)
                except queue.Full:
                    pass
            time.sleep(1.0 / HUD_ANIM_HZ if animating else 0.1)

    def lobe_worker():
        """The dots and the beam number at the beam's own rate (5 Hz), two
        tiny tiles; the HUD stays at 1 Hz (the User 2026-09-07)."""
        shown = {"lobe": False, "beam": False, "scan": False, "door": False}
        ease = {"az": None, "t": time.time()}
        think = {"on_since": None, "seq": None}   # K: for the one log line per pulse
        while not st.stop.is_set():
            t0 = time.time()
            with st.lock:
                snd, house, crissa, zlevel = st.sound, st.house, st.sound_crissa, st.zlevel
                th, zl_ts, zl_at = st.thinking, st.crissa_line_ts, st.crissa_line_at
            pulse = R.thinking_pulse(th, zl_ts, t0) if THINKING_PULSE else None
            if THINKING_PULSE:
                if pulse is not None and think["on_since"] is None:
                    think.update(on_since=t0, seq=th.get("seq"))
                elif pulse is None and think["on_since"] is not None:
                    # the measurement the User asked for: pulse stop vs her line entering the roll
                    by_line = zl_ts >= float(th.get("since") or 0.0)
                    log(f"thinking pulse seq={think['seq']} {t0 - think['on_since']:.1f}s, stopped by "
                        + (f"her line (entered {zl_at - t0:+.2f}s from the stop)" if by_line
                           else f"the bridge ({th.get('why') or 'off'}; no line)"))
                    think.update(on_since=None, seq=None)
            ear = (house.get("voice") or {}).get("ear")
            ear = ear if isinstance(ear, dict) else {}   # 2026-10-25: the feed's voice.ear is a bool ("the ear answered");
            #                                              read as a dict, every lamp pass died 'bool'.get (the stranger run)
            pref = _prefs()
            # the kiosk eases the bearing toward the target (tau .14 s) and the
            # wall snapped it — "one is sampling faster than the other" (the User
            # 2026-09-07). Same ease here, on the lamps only; the number is exact.
            snd_draw = snd
            try:
                az_t = float((snd or {}).get("az_deg"))
                dt = max(0.0, t0 - ease["t"]); ease["t"] = t0
                if ease["az"] is None:
                    ease["az"] = az_t
                else:
                    d = ((az_t - ease["az"] + 180.0) % 360.0) - 180.0
                    ease["az"] = (ease["az"] + d * (1.0 - math.exp(-dt / 0.14))) % 360.0
                snd_draw = dict(snd, az_deg=ease["az"])
            except (TypeError, ValueError):
                ease["az"] = None
            lobe = None
            try:
                lobe = R.render_lobe(snd_draw, ear, t0, oracle_scale=oracle_anim.get("scale", 1.0),
                                     collapsed=bool(pref.get("oracle_dot")), crissa=crissa, level=zlevel,
                                     pulse=pulse)
                door = R.render_door_globe(house, t0, oracle_scale=oracle_anim.get("scale", 1.0),
                                           collapsed=bool(pref.get("oracle_dot")))
                beam = R.render_beam_cell(snd, None) if not pref.get("vitals_folded") else None
                scan = R.render_scan_line(t0, g)
            except Exception as e:  # noqa: BLE001
                log(f"lobe render: {type(e).__name__}: {e}"[:160])
                time.sleep(0.5)
                continue
            if _init_running():
                # the wizard owns the glass: no lamps, no beam number, no scan
                # line (the User's screenshots 2026-09-12: a stray "9.8" — the
                # beam cell — floated on the emptied glass)
                lobe = door = beam = scan = None
            pkt = {}
            if lobe is not None or shown["lobe"]:
                pkt["lobe"] = lobe
                shown["lobe"] = lobe is not None
            if beam is not None or shown["beam"]:
                pkt["beam"] = beam
                shown["beam"] = beam is not None
            if scan is not None or shown["scan"]:
                pkt["scan"] = scan
                shown["scan"] = scan is not None
            if door is not None or shown["door"]:
                pkt["door"] = door
                shown["door"] = door is not None
            if pkt:
                try:
                    out_q.put(pkt, timeout=0.5)
                except queue.Full:
                    pass
            # 5 Hz whenever the beam tick is fresh (the SSE's own rate, what
            # Brave paints at), not only while the lobe is lit; 2 Hz otherwise
            try:
                fresh = (t0 - float((snd or {}).get("ts") or 0.0)) < 6.0
            except (TypeError, ValueError):
                fresh = False
            fresh = fresh or R.on_air(crissa, t0)      # her rings pulse at 5 Hz too
            fresh = fresh or door is not None         # and the door globe breathes
            tick = 0.1 if pulse is not None else (0.2 if (fresh or lobe is not None) else 0.5)  # K: a smooth breath
            time.sleep(max(0.02, tick - (time.time() - t0)))

    def register_worker():
        """Register on the film (the User 2026-09-09): the coach's phase from
        :8099/v1/register at 5 Hz, one glass painted only when the phase, the
        text or the line changes; its CANCEL key rides the input shape through
        REGISTER_HITS. Not chrome — it never fades on the idle."""
        last_sig = None
        shown = False
        while not st.stop.is_set():
            with st.lock:
                reg = dict(st.register or {})
            phase = str(reg.get("phase") or "idle")
            with st.lock:
                wiz_up = str((st.init or {}).get("status") or "") == "running"
                # SETUP IN HER OWN WINDOW (a pack, 2026-10-11): the Voice ID sentence is read there; this glass shows
                # only "Setup is open in Crissa's window" (init_v2_render), no second plate
                own_led = (not crissa_home.is_house()) and _setup_in_her_window(st.init or {})
            # the take bar and the beep flash move on their own clock: while
            # a take window is open the glass repaints at 5 Hz (the bar's
            # tick), and for a beat after each beep
            try:
                _bts = float(reg.get("beep_ts") or 0.0)
                _sec = float(reg.get("seconds") or 0.0)
            except (TypeError, ValueError):
                _bts, _sec = 0.0, 0.0
            _now = time.time()
            _tick = int((_now - _bts) * 5) if (_bts > 0 and _sec > 0 and 0 <= _now - _bts < _sec + 0.4) else -1
            if wiz_up and (st.init or {}).get("spoken_again"):
                wiz_up = False        # the walk draws no plate for a spoken Voice ID again: the coach's sits at the top
            sig = (phase, str(reg.get("text") or ""), str(reg.get("line") or ""), wiz_up, _tick, own_led)
            if sig != last_sig:
                last_sig = sig
                im = None
                try:
                    im = None if own_led else R.render_register(g, reg, top_y=(3.6 * g.u if wiz_up else None))
                except Exception as e:  # noqa: BLE001
                    log(f"register render: {type(e).__name__}: {e}"[:160])
                if im is not None or shown:
                    try:
                        out_q.put({"register": ((im, (0, 0)) if im is not None else None)}, timeout=0.5)
                    except queue.Full:
                        pass
                    if im is not None and not shown:
                        log(f"register: on the film ({phase})")
                    elif im is None and shown:
                        log("register: off")
                    shown = im is not None
            time.sleep(0.2 if phase != "idle" else 0.5)

    def wizard_worker():
        """The first-boot wizard's second renderer and driver (the User 2026-09-09:
        New User on the wall). The walker on :8099 owns the steps, the voice
        (house-speak → her painted mouth) and the ear; this thread paints the
        plate from the snapshot and mirrors the page's driver: per step|beat
        one `say`, wait for her mouth to close, then `listen` until the step
        moves (a click on a pill moves it too). The Register chapter is the
        coach's routine: enroll_start with the roster's name when the step
        arrives, enroll_done when the coach's phase says done."""
        last_key = None
        last_sig = None
        shown = False
        enroll_sent_for = None
        reg_seen_live = False
        init_logged: dict = {"line": ""}
        ids_open: dict = {"run": ""}          # round 41 (F7): the IDs run this glass saw open, for its end's notice
        boot_t = time.time()
        auto_started = False
        while not st.stop.is_set():
            with st.lock:
                snap = dict(st.init or {})
                reg = dict(st.register or {})
                tog = json.loads(json.dumps(st.init_tog)) if st.init_tog else {}
                cam = st.camera
            running = str(snap.get("status") or "") == "running"
            # ON BOOT the house introduces itself (first boot = the wizard is
            # `needed`): start it once the glass has been up a few seconds.
            # The User never presses anything to meet the house.
            if (not running and not auto_started and bool(snap.get("needed"))
                    and time.time() - boot_t > 8.0):
                auto_started = True
                log("init: first boot -> start")
                _init_post("start", {}, timeout=15)
            sid = str(snap.get("step_id") or "")
            key = (sid, int(snap.get("beat") or 0), int(snap.get("turn") or 0)) if running else None   # turn: her readback / live line on the same beat
            reg_phase = str(reg.get("phase") or "idle")
            reg_live = reg_phase in R.REGISTER_PHASES
            # paint on change — and on a clock for the screens with a picture:
            # the door still refreshes with the camera (1.5 s), the dial's hand
            # sweeps while a rail step pans (3 Hz)
            tick = 0
            if running and sid in R.INIT_STILL_STEPS:
                tick = int(time.time() / 1.5)
            elif running and sid == "room_clock":
                dlg_rc = snap.get("dialogue") if isinstance(snap.get("dialogue"), dict) else {}
                if dlg_rc.get("show") == "crissa_seat":
                    tick = int(time.time() / 1.5)          # the live picture of Crissa's seat refreshes
                elif time.time() - float(snap.get("asked_at") or 0.0) < 40.0:
                    tick = int(time.time() * 4)            # 12, Crissa's seat and the User land on her words (a ~33 s script)
            elif running and snap.get("rail_busy") and sid in R.INIT_DIAL_STEPS:
                tick = int(time.time() * 3)
            if running and snap.get("v2"):
                # INIT V2: the room still fades in/out over a second, the countdown counts, the overlay follows the pointer
                pl_ = snap.get("plate") if isinstance(snap.get("plate"), dict) else {}
                fresh = time.time() - float(snap.get("step_at") or 0) < 3.0      # covers the walk-in hold + fade (2.2 s)
                counting = ((pl_.get("room") or {}).get("state") == "countdown")
                tick = int(time.time() * 12) if (fresh or counting) else 0
            sig = (running, key, json.dumps(snap.get("plate") or {}, sort_keys=True), str(snap.get("note") or ""),
                   json.dumps((snap.get("answers") or {}).get(sid) or {}, sort_keys=True), reg_live, str(snap.get("enroll_who") or ""),
                   json.dumps(tog, sort_keys=True), tick, json.dumps(snap.get("frozen") or {}, sort_keys=True), json.dumps(snap.get("dialogue") or {}, sort_keys=True), str(snap.get("processing") or ""),
                   str(snap.get("mode_note") or ""), json.dumps(snap.get("crissa_seat") or {}, sort_keys=True),
                   int(time.time() * 2) if snap.get("mode_note") else 0,
                   # round 40 (.101 round 38 N15: no corner tile on a minimized IDs run): her window's state and the IDs
                   # run's stage change what the plate draws, so they repaint it (the window minimized changed nothing
                   # else in an IDs run, and the tile was never drawn). Kill HOUSE_WALL_INIT_SIG_WINDOW=0.
                   json.dumps([((snap.get("browser") or {}).get("window") or {}).get(k) for k in ("up", "setup", "visible")]
                              + [(snap.get("ids") or {}).get("stage")])
                   if os.environ.get("HOUSE_WALL_INIT_SIG_WINDOW", "1").strip().lower() not in ("0", "off", "false", "no")
                   else "")
            if sig != last_sig:
                last_sig = sig
                im = None
                try:
                    snap_p = dict(snap, _toggles=tog, _t=time.time())
                    if sid in R.INIT_STILL_STEPS or sid == "room_clock":
                        snap_p["_camera"] = cam
                    # a spoken Voice ID again (a pack, 2026-10-12): the coach's one small plate is the only one; the
                    # walk draws nothing, running or ended (lib/crissa_init snapshot "spoken_again")
                    im = None if snap.get("spoken_again") else R.render_init(g, snap_p, register_live=reg_live)
                except Exception as e:  # noqa: BLE001
                    log(f"init render: {type(e).__name__}: {e}"[:160])
                if im is not None or shown:
                    try:
                        out_q.put({"init": ((im, (0, 0)) if im is not None else None)}, timeout=0.5)
                    except queue.Full:
                        pass
                    if im is not None:
                        film_line = f"init: {sid} beat {int(snap.get('beat') or 0)} on the film" + (" (register live)" if reg_live else "")
                        if film_line != init_logged.get("line"):      # once per step/beat, not per repaint (478 lines at 13:01-13:05)
                            init_logged["line"] = film_line
                            log(film_line + (f" — {snap.get('mode_note')}" if snap.get("mode_note") else ""))
                    elif shown:
                        log("init: off")
                        init_logged["line"] = ""
                    shown = im is not None
            if running and snap.get("ids"):
                ids_open["run"] = str(snap.get("run_id") or "") or "?"
            if not running and ids_open["run"]:
                # round 41 (.101 round 40 F7, the User: "there was no way back"): an IDs run that ends by a cancel (its
                # window closed, Back to the Wall, Cancel) says so on the desk Wall for a few seconds - the Wall's own
                # toast and its own time. A run that ends done has its done plate. Kill CRISSA_IDS_END_NOTICE=0.
                ended_ = str(snap.get("status") or "")
                if (ended_ == "aborted" and os.environ.get("CRISSA_IDS_END_NOTICE", "1").strip().lower()
                        not in ("0", "off", "false", "no")):
                    log("init: Add/Edit IDs cancelled - nothing saved (the Wall says so)")
                    try:
                        _wall_toast(IDS_CANCELLED_NOTE)
                    except NameError:                      # the glass is not all up yet
                        pass
                ids_open["run"] = ""
            if not running:
                if last_key is not None:
                    # the routine ended (done, or CANCEL): the windows come
                    # back to the panel that sent him (the server's return)
                    if not snap.get("pack"):          # a pack has no Control Panel to go back to
                        log(f"init: ended ({snap.get('status')}) -> kiosk_return_cp")
                        threading.Thread(target=lambda: _house_action("kiosk_return_cp", {}), daemon=True).start()
                    else:
                        log(f"init: ended ({snap.get('status')})")
                last_key = None
                enroll_sent_for = None
                reg_seen_live = False
                with st.lock:
                    st.init_tog = {}
                time.sleep(0.5)
                continue
            if snap.get("v2"):
                # INIT V2 (the User 2026-10-04): the walker is the server's; the wall only runs the Register chapter
                # when the name card asks for it, and says when it left live. Every other answer is a click.
                pl_ = snap.get("plate") if isinstance(snap.get("plate"), dict) else {}
                regp = pl_.get("register") if isinstance(pl_.get("register"), dict) else {}
                run_ = str(snap.get("run_id") or "")
                if sid == "voice_id" and regp.get("driver") == "walker":
                    pass               # a pack's walk drives the Register coach itself (lib/crissa_init, 2026-10-10)
                elif sid == "voice_id":
                    if reg_live:
                        reg_seen_live = True
                    elif reg_seen_live and reg_phase in ("done", "idle"):
                        reg_seen_live = False
                        log("init v2: register left live -> enroll_done")
                        _init_post("enroll_done", {}, timeout=15)
                    elif regp.get("pending") and not regp.get("sent") and enroll_sent_for != run_ and reg_phase == "idle":
                        enroll_sent_for = run_
                        who_ = str(regp.get("display") or "")
                        log(f"init v2: register chapter -> enroll_start {who_!r}")
                        r = _house_action("enroll_start", {"mode": "voice-register", "display": who_,
                                                           "person_id": str(regp.get("person_id") or ""),
                                                           "station": "desk", "create": True,
                                                           "first_boot": bool(regp.get("first_boot"))})
                        if isinstance(r, dict) and (r.get("ok") or (r.get("result") or {}).get("ok")):
                            _init_post("enroll_sent", {}, timeout=15)
                        else:
                            log(f"init v2: enroll_start refused: {str(r)[:120]}")
                            _init_post("enroll_failed", {"error": str((r or {}).get("error") or "refused")[:160]
                                                         if isinstance(r, dict) else "no answer"}, timeout=15)
                            enroll_sent_for = None
                time.sleep(0.25)
                continue
            if sid == "user_identity" or (sid == "present_regulars_enroll" and snap.get("enroll_go")):
                # someone else is registered only after a spoken yes (the User 2026-09-13)
                # THE SUBSECTION: the coach's routine on the register layer
                who = str((snap.get("roster") or {}).get("user") or "") if sid == "user_identity" else str(snap.get("enroll_who") or "")
                if reg_live:
                    reg_seen_live = True
                elif reg_seen_live and reg_phase in ("done", "idle"):
                    # THE HANDSHAKE MUST NOT DEPEND ON CATCHING ONE FRAME. The
                    # register's "done" glass is momentary — teardown drops it
                    # to idle within a beat — and a 2 Hz poll that lands after
                    # that saw the chapter run and then sat for ever waiting for
                    # a "done" already gone (the User 2026-09-12: "bailed after ID
                    # sequence"). Any exit from live (done OR idle) after we
                    # saw it live is the chapter finishing.
                    reg_seen_live = False
                    log(f"init: register left live ({reg_phase}) for {who!r} -> enroll_done")
                    _init_post("enroll_done", {}, timeout=15)
                    # keep enroll_sent_for == key: if the advance somehow does
                    # not take, do NOT re-fire enroll_start and re-run the whole
                    # register on the same person
                elif (sid == "user_identity" or snap.get("enroll_go")) and who and enroll_sent_for != key and reg_phase == "idle" and not snap.get("rail_busy"):
                    # rail_busy: the room scan started under the roster (the
                    # camera is panning) — the register's own desk aim waits
                    enroll_sent_for = key
                    log(f"init: register chapter -> enroll_start {who!r}")
                    # first_boot: inside the wizard the register plays no Crissa
                    # handoff — the wizard's own last chapter is hers
                    r = _house_action("enroll_start", {"mode": "voice-register", "display": who, "person_id": who.strip().lower().replace(" ", "_"),
                                                       "station": "desk", "create": True, "first_boot": True})
                    if not (isinstance(r, dict) and (r.get("ok") or (r.get("result") or {}).get("ok"))):
                        log(f"init: enroll_start refused: {str(r)[:120]}")
                time.sleep(0.5)
                continue
            if key != last_key and snap.get("say_ready", True):
                # NOT BEFORE RENDER IS UP (the User 2026-09-14: the opener runs in Render) nor before the
                # seat picture has landed: the wall shows "Switching to Render…" / "Wait while we process…"
                last_key = key
                # one say per step|beat, then the ear while the step wants a voice
                said = _init_post("say", {"loop": True}, timeout=10)
                log(f"init: say {sid} -> {'ok' if isinstance(said, dict) else 'no answer'}")
                t0 = time.time()
                # THE LIVE EAR LISTENS BESIDE HER LINE (the User 2026-09-14: "operating in parallel. Not
                # taking turns"): with ear_live the wizard takes an answer over her voice and cuts it
                while not snap.get("ear_live") and time.time() - t0 < 40 and not st.stop.is_set():
                    m = http_json(f"{HOUSE}/v1/init-mouth", timeout=2.0)
                    if not (isinstance(m, dict) and m.get("speaking")):
                        break
                    time.sleep(0.3)
                wants_voice = (str(snap.get("kind") or "") in ("interview", "coach", "dialogue") and sid not in ("room_scan",)
                               and not (snap.get("dialogue") or {}).get("closed"))   # the pause menu is spoken too (the User 2026-09-13)
                # NOT WHILE THE CINEMA IS UP. The welcome's `say` returns at
                # once under cinema (the wall paints Crissa's file itself), so
                # the ear opened during her 50 s teaser and heard HER as the
                # User's answer (13:51 run: the roster advanced with nobody in
                # the room). Listen only once the wizard says the cinema is done.
                plate_v = snap.get("plate") if isinstance(snap.get("plate"), dict) else {}
                if sid == "welcome" and plate_v.get("cinema") and str(plate_v.get("cinema_state") or "") != "done":
                    wants_voice = False
                tries = 0
                if sid == "welcome" and plate_v.get("cinema") and not wants_voice:
                    # wait for the cinema (and Lila's first line) to finish, then listen
                    for _ in range(600):
                        with st.lock:
                            snap_w = dict(st.init or {})
                        pv = snap_w.get("plate") if isinstance(snap_w.get("plate"), dict) else {}
                        if str(snap_w.get("status") or "") != "running" or str(snap_w.get("step_id") or "") != sid:
                            break
                        if str(pv.get("cinema_state") or "") == "done":
                            wants_voice = True
                            break
                        time.sleep(0.25)
                    # and her mouth closed (the wizard's own gate), before the ear
                    t_m = time.time()
                    while wants_voice and not snap.get("ear_live") and time.time() - t_m < 40 and not st.stop.is_set():
                        m = http_json(f"{HOUSE}/v1/init-mouth", timeout=2.0)
                        if not (isinstance(m, dict) and m.get("speaking")):
                            break
                        time.sleep(0.3)
                while wants_voice and not st.stop.is_set() and tries < 40:
                    tries += 1
                    d = _init_post("listen", {}, timeout=60)   # the live listen waits out her line (cap 45 s)
                    if not isinstance(d, dict):
                        time.sleep(1.0)
                        continue
                    if tries == 1:
                        log(f"init: listening for {sid}")
                    with st.lock:
                        st.init = d
                    if str(d.get("status") or "") != "running" or (str(d.get("step_id") or ""), int(d.get("beat") or 0), int(d.get("turn") or 0)) != key:
                        break
                    time.sleep(0.4)
            time.sleep(0.5)

    def render_worker():
        """Draw off the Tk thread. Background + FT per new frame; HUD once a second."""
        nonlocal g
        last_frame_at = -1.0
        last_hud = 0.0
        last_roll = 0.0
        last_house_rev = None
        last_pref = None
        period = 1.0 / max(1.0, fps)
        # THE FILM PACES ON THE CLIP, ONE GLASS FRAME PER CLIP FRAME, ON A
        # STEADY CLOCK. Measured 2026-09-08: the feed pushes 60 JPEGs a
        # second, but the clip behind them is 24 fps (conductor CLIP_FPS), so
        # 6 in 10 are byte-identical repeats, and the 24 distinct ones arrive
        # 17/33/50/67 ms apart (3:2 pulldown plus the conductor's tick
        # jitter). "Render the latest, sleep the rest of 1/60" drew ~27 of
        # the 60 ticks — some clip frames twice, some never — on top of that
        # jitter: the hitch (the User: "Hitches on some frames"). Now a pass
        # renders only a frame whose bytes changed, and presents it on a
        # clock of the measured distinct-frame period (~41.7 ms), so each
        # clip frame is on the glass once and the frames are evenly spaced.
        # A held or dead feed times out so the roll and the camera keep
        # moving. Brave shows the same 24 distinct frames; this is the wall
        # drawing them the way the clip was shot.
        stride = 1
        present_period = 1.0 / 24.0     # re-measured from the feed reader's distinct arrivals
        last_present = 0.0
        crissa = None
        crissa_at = -1.0
        drawn = None                    # the JPEG bytes on the glass
        # THE FILM IN ITS OWN PROCESS (the User 2026-10-10): render_background runs in a child with its own
        # interpreter, so the painter stops sharing one lock with it. None, or a failed frame, draws here as before.
        need_full = True                # the glass and the child agree on the last picture only after a whole one
        patching = False
        film = None
        ac = {"engaged": False, "since": 0.0, "mode": None, "how": {}}   # the ASCII cache, this side
        lap = {"clip": None, "last": -1, "n": 0, "t0": 0.0, "mem": 0, "disk": 0, "fresh": 0}   # round 13: per lap
        if wall_film_proc.enabled():
            try:
                film = wall_film_proc.FilmProcess(W, H, log=log, feed=FEED)
            except Exception as e:  # noqa: BLE001
                log(f"film process: off ({type(e).__name__}: {e})"[:160])
        while not st.stop.is_set():
            t0 = time.time()
            queued = _film_queue_on()
            was_patching, patching = patching, _film_patch_on() and film is not None
            if patching and not was_patching:
                need_full = True
            with st.lock:
                frame, frame_at, frame_stamp, frame_mouth = st.frame, st.frame_at, st.frame_stamp, st.frame_mouth
                frame_clip = st.frame_clip
                quiet_now = not (st.on_air or st.zlevel > 0.02)
                q_drop = 0
                if queued and st.fifo:
                    while FILM_QUEUE_LAG_S and len(st.fifo) > 1 and t0 - st.fifo[0][2] > FILM_QUEUE_LAG_S:
                        st.fifo.popleft()                                         # too late: the next one
                        q_drop += 1
                    frame, frame_stamp, frame_at, frame_mouth, frame_clip = st.fifo.popleft()   # the oldest frame not drawn yet
                elif not queued:
                    st.fifo.clear()
                q_left = len(st.fifo)
                house, pres, cam = st.house, st.presence, st.camera
                distinct_at = list(st.distinct_at)
            stale = st.stale()
            # the clip's rate, from the reader (it sees every frame; this
            # loop does not while it sleeps): mean gap of the last ~2 s
            if len(distinct_at) >= 12 and t0 - distinct_at[-1] < 2.0:
                span = distinct_at[-1] - distinct_at[0]
                present_period = max(1 / 60.0, min(1 / 12.0, span / (len(distinct_at) - 1)))
            if frame and frame_at != crissa_at:       # decode each JPEG once, not once per pass
                crissa = R._open(frame)
                crissa_at = frame_at
            elif not frame:
                crissa = None
            # the camera JPEG is the picture only when there is no Crissa frame;
            # decoding it every frame otherwise was ~3 ms of the loop for
            # nothing (the HUD decodes its own copy once a second)
            camim = R._open(cam) if (cam and crissa is None) else None
            pkt = {}
            pref = _prefs()
            if pref != last_pref:
                last_pref = pref
                g = R.Geom(W, H, density=pref["density"])
                last_frame_at = -1.0
                last_hud = 0.0
            if (frame_at != last_frame_at and frame != drawn) or crissa is None:
                last_frame_at = frame_at
                drawn = frame
                src, own = (crissa, True) if crissa is not None else (camim, False)
                with st.lock:
                    house_f = st.house
                hz = film_hole_zoom(house_f, own)
                shift_f, frame_f = _her_frame(house_f, own, hz, pref,
                                              frame_clip[0] if isinstance(frame_clip, tuple) else None)
                mouth_f = frame_mouth if (own and _norm_mouth_on()) else None
                t_bg = time.time()
                bg_im = None
                got = None
                ac_mode = _ascii_cache_mode()
                if ac_mode != ac["mode"]:
                    log(f"ascii cache: {ac['mode'] or 'start'} -> {ac_mode}")
                    ac["mode"] = ac_mode
                if film is not None:
                    got = film.render_patch(frame if own else cam, g.density, own, t0, hole_zoom=hz,
                                            brightness=pref["brightness"] * (agent_dim() if own else 1.0),
                                            shift=shift_f, frame=frame_f,
                                            patch_max=(0.4 if (patching and not need_full) else 0.0),
                                            # her film only: the door camera never repeats a picture
                                            cache=own and (ac_mode == "on" or (ac_mode == "fallback" and ac["engaged"])),
                                            mouth=mouth_f, clip=frame_clip if own else None, quiet=quiet_now,
                                            norm=_norm_mouth_on() if own else None)
                    if got is not None:
                        how = film.how or "today"
                        ac["how"][how] = ac["how"].get(how, 0) + 1
                        if isinstance(film.child_ms, (int, float)):
                            speed["child"].append(float(film.child_ms))   # bg less the pipe and the copy-out
                        if how in ("today", "full"):
                            speed["bg_whole"].append((time.time() - t_bg) * 1000)
                    if own:
                        src_l = LAP_SRC.get(film.how or "today", "fresh") if got is not None else "fresh"
                        _lap_count(lap, frame_clip, src_l, time.time())
                        if src_l == "fresh" and (got is None or film.how in (None, "today")):
                            try:                       # round 45: the child's own line says why for its whole draws
                                import wall_ascii_cache
                                wall_ascii_cache.fresh_why(
                                    log, frame_clip,
                                    "the film child gave no picture" if got is None else
                                    "the cache was not asked (mode %s%s)" % (
                                        ac_mode, (", off: " + str((film.cache_stats or {}).get("dead") or ""))
                                        if (film.cache_stats or {}).get("dead") else ""))
                            except Exception:  # noqa: BLE001
                                pass
                    if got is None:
                        # the glow is reused every other frame; this side's copy is from before the child took over
                        R._GLOW.update(n=0, im=None, fp=None, out=None)
                if got is not None and got[0] is None:
                    speed["made_same"] += 1                      # the child's picture did not change
                elif got is not None and got[1] is not None:
                    pkt["bg_patch"] = got                        # a mouth step: only the part that changed
                    speed["made_patch"] += 1
                else:
                    bg_im = got[0] if got is not None else None
                    pkt["bg"] = bg_im if bg_im is not None else R.render_background(
                        src, g, own, t0,
                        hole_zoom=hz,
                        brightness=pref["brightness"] * (agent_dim() if own else 1.0),
                        shift=shift_f, frame=frame_f,
                        mouth=mouth_f,
                    )
                if _first_boot_dark():
                    # round 38 (.101 round 37, the User: "crissa at desk appeared, then empty room, then lila ... looks
                    # bad"): on a first boot the glass stays dark until Lila's window is up
                    pkt.pop("bg_patch", None)
                    if dark_im.get("size") != (g.W, g.H):
                        from PIL import Image as _Im
                        dark_im.update(size=(g.W, g.H), im=_Im.new("RGB", (int(g.W), int(g.H)), (0, 0, 0)))
                    pkt["bg"] = dark_im["im"]
                    speed["made_full"] += 1
                    # a picture the child did not draw leaves its "previous" behind the glass: whole next time
                    need_full = got is None
                speed["bg"].append((time.time() - t_bg) * 1000)
                if own and frame_stamp and ("bg" in pkt or "bg_patch" in pkt):
                    pkt["stamp"] = frame_stamp
                if PAINT_TRACE:
                    pkt["arr"] = frame_at
                    paint_trace({"w": "film", "arr": frame_at, "t0": t0, "t_bg": t_bg, "t1": time.time(),
                                 "kind": "patch" if "bg_patch" in pkt else ("whole" if "bg" in pkt else "same"),
                                 "how": film.how if (film is not None and got is not None) else "thread",
                                 "child": film.child_ms if (film is not None and got is not None) else None,
                                 "drop": q_drop, "left": q_left,
                                 "clip": list(frame_clip[:2]) if isinstance(frame_clip, tuple) else None})
                with st.lock:
                    reg_phase_f = str((st.register or {}).get("phase") or "idle")
                # the FaceTime tile steps off for the wizard too — except while
                # the register is live, where the User needs to see the camera
                ft_off = _init_running() and reg_phase_f not in R.REGISTER_PHASES
                ft_off = ft_off or _walkin_hides_tile()   # round 37: her walk-in is on this Wall, the tile waits
                # Hey Crissa is a full-glass webcam cut, not a wake-word popup.
                # The corner FaceTime tile is BAU (room crop) — do not hide it
                # unless ft_bar (folded) or the wizard owns the glass.
                if crissa is not None and not pref.get("ft_bar") and not ft_off:
                    t_ft = time.time()
                    pkt["ft"] = R.render_ft(crissa, g, _big_prefs(pref, t0))
                    speed["ft"].append((time.time() - t_ft) * 1000)
                elif pref.get("ft_bar") or ft_off:
                    pkt["ft_hide"] = True
            roll.feed(house.get("chat"), t0)
            if THINKING_PULSE:
                zl = [ln for ln in roll.lines if ln.get("who") == "crissa"]
                if zl:
                    newest = max(zl, key=lambda ln: ln["ts"])
                    with st.lock:
                        if newest["ts"] > st.crissa_line_ts:
                            st.crissa_line_ts, st.crissa_line_at = newest["ts"], newest["at"]
            if roll.lines and (t0 - last_roll) >= (1.0 / 6):     # drift at 6 Hz, text only
                last_roll = t0
                pkt["roll"] = roll.draw(g, t0, pref)
            elif not roll.lines and last_roll:
                last_roll = 0.0
                pkt["roll"] = roll.draw(g, t0, pref)              # clear
            # the HUD is hud_worker's now; this loop is the film: bg, ft, roll
            dt = time.time() - t0            # the pass: decode + render, NOT the presentation wait
            speed["loop"].append(dt * 1000)
            if patching:
                last_present = time.time()     # every picture on arrival: the conductor already spaced them
            elif "bg" in pkt:
                # present on the clock: the next tick of the measured period,
                # so the feed's 17/33/50/67 ms arrival jitter is not the
                # glass's; a frame that arrives late waits at most one tick
                tick = last_present + present_period * stride
                now_p = time.time()
                if last_present and tick > now_p and tick - now_p < present_period * 2:
                    time.sleep(tick - now_p)
                    last_present = tick
                else:
                    last_present = time.time()
            if pkt:
                try:
                    out_q.put(pkt, timeout=period)
                except queue.Full:
                    if "bg" in pkt or "bg_patch" in pkt:
                        need_full = True        # the glass missed a picture; a patch on top of it would be wrong
            now_s = time.time()
            if now_s - speed["last"] >= speed["every"]:
                speed["last"] = now_s
                def _ms(k):
                    xs = speed[k][-40:]
                    if not xs:
                        return "—"
                    xs = sorted(xs)
                    return f"{xs[len(xs)//2]:.1f}ms"
                xs = sorted(speed["loop"][-40:])
                if xs:
                    # the stride of clip frames the median pass fits, with a
                    # 10% margin: 1 at 24 fps while a pass is under 38 ms; a
                    # pass that grows (density up, camera enlarged) drops to
                    # every 2nd clip frame evenly instead of stuttering
                    stride = 1 if patching else max(1, int(math.ceil(xs[len(xs) // 2] / 1000 * 1.1 / present_period)))
                _hud_n, speed["hud_n"] = speed.get("hud_n", 0), 0
                log(f"speed bg={_ms('bg')} hud={_ms('hud')} x{_hud_n / max(1e-6, float(speed.get('every') or 8.0)):.1f}/s "
                    f"ft={_ms('ft')} loop={_ms('loop')} "
                    + (f"resting ({REST_FPS:g}/s on the glass)" if REST["on"] else
                       # ON THE GLASS IS COUNTED, NOT COMPUTED (round 12, .101: "(45/s on the glass)" was the clip's
                       # rate while 20.5/s were drawn): the pictures the Tk thread put up this window, whole + patches
                       # DRAWN, NOT MADE (round 15, .101: "21-22/s on the glass" while the screen froze): the pictures
                       # the Tk thread drew into the window this window, whole + patches; x = the X server's answer
                       (f"clip={1 / present_period:.1f}/s stride={stride}"
                        f" ({speed['drawn'] / speed['every']:.1f}/s drawn:"
                        f" {speed['glass']} whole + {speed['glass_patch']} patches"
                        + ("" if DRAW_NOW else f", {speed['drawn']} redraws")
                        + (f"; draw {_ms('draw')}" if speed["draw"] else "")
                        + (f"; x {_ms('xrt')}" if speed["xrt"] else "") + ")") if DRAWN_LINE else
                       # the round-12 line (the house): the pastes
                       (f"clip={1 / present_period:.1f}/s stride={stride}"
                        f" ({(speed['glass'] + speed['glass_patch']) / speed['every']:.1f}/s on the glass:"
                        f" {speed['glass']} whole + {speed['glass_patch']} patches)"))
                    + f" made full={speed['made_full']} patch={speed['made_patch']} same={speed['made_same']}"
                    f" in {speed['every']:.0f}s{' PATCH' if patching else ''}")
                # the ASCII cache: its own line, so the speed line above reads as it always has
                made = speed["made_full"] + speed["made_patch"] + speed["made_same"]
                behind = not REST["on"] and not stale and made < 0.95 * speed["every"] / present_period
                whole = sorted(speed["bg_whole"][-40:])
                if ac["mode"] == "fallback":
                    if behind and not ac["engaged"]:
                        ac["engaged"], ac["since"] = True, now_s
                        log(f"ascii cache: fallback engaged ({made} pictures in {speed['every']:.0f}s,"
                            f" the clip is {1 / present_period:.1f}/s)")
                    elif (ac["engaged"] and not behind and now_s - ac["since"] >= ASCII_CACHE_HOLD_S
                          and whole and whole[len(whole) // 2] * 1.1 < present_period * 1000):
                        ac["engaged"] = False
                        log(f"ascii cache: fallback let go (a whole render is {whole[len(whole) // 2]:.1f}ms)")
                elif ac["engaged"]:
                    ac["engaged"] = False
                if ac["mode"] != "off":
                    cs = (film.cache_stats if film is not None else None) or {}
                    _cache_note(cs)
                    hw = ac["how"]
                    log(f"ascii cache: {ac['mode']}{' (engaged)' if ac['engaged'] else ''}"
                        f" bytes={hw.get('bytes', 0)} grid={hw.get('grid', 0)} region={hw.get('region', 0)}"
                        f" full={hw.get('full', 0)} today={hw.get('today', 0)} in {speed['every']:.0f}s"
                        f" held={cs.get('bytes_held', 0) / 2 ** 20:.0f}MB of {cs.get('cap', 0) / 2 ** 20:.0f}MB"
                        f" pictures={cs.get('pictures', 0)} refused={cs.get('refused', 0)}"
                        f" verified={cs.get('verified', 0)}{' OFF: ' + cs['dead'] if cs.get('dead') else ''}"
                        # the memory tier held compressed (round 17, 2026-10-13): pictures unpacked so far and the
                        # last 512 unpacks' p50 / p90; `glass` = room taken for the clip on the glass
                        + (" zmem unpacks=%d (%.1f/%.1f ms) glass=%d" % (
                            cs["zmem"].get("unpacks", 0), cs["zmem"].get("unpack_p50") or 0.0,
                            cs["zmem"].get("unpack_p90") or 0.0, cs.get("glass", 0))
                           if isinstance(cs.get("zmem"), dict) else "")
                        + (" disk=%.1f/%.0fGB hits=%d bases=%d writes=%d replaced=%d%s%s" % (
                            (cs["disk"].get("used") or 0) / 2 ** 30, (cs["disk"].get("cap") or 0) / 2 ** 30,
                            cs["disk"].get("hits", 0), cs["disk"].get("bases", 0), cs["disk"].get("writes", 0),
                            cs["disk"].get("replaced", 0),
                            # round 33: pictures the read-ahead read from disk into memory before their frame
                            (" ahead=%d" % cs["disk"]["ahead"]) if "ahead" in cs["disk"] else "",
                            (" OFF: " + cs["disk"]["dead"]) if cs["disk"].get("dead") else "")
                           if isinstance(cs.get("disk"), dict) else "")
                        # the film child's own time a picture (2026-10-11): the speed line's bg less this is the pipe
                        # and the copy-out (that line stays as other readers parse it)
                        + (f" child={_ms('child')}" if speed["child"] else "")
                        # round 46 O3: the film child's whole resident size beside what the cache says it holds
                        + (" rss=%.0fMB" % (cs["rss"] / 2 ** 20) if cs.get("rss") else "")
                        # O3 (2026-10-24): what the allocator keeps beyond the count and the harness (0 is the goal)
                        + (" over=%.0fMB" % (cs["over"] / 2 ** 20) if cs.get("over") is not None else ""))
                ac["how"] = {}
                speed["made_full"] = speed["made_patch"] = speed["made_same"] = 0
                speed["glass"] = speed["glass_patch"] = speed["drawn"] = 0
                speed["bg_whole"] = speed["bg_whole"][-40:]
                for k in ("bg", "hud", "ft", "loop", "child"):
                    speed[k] = speed[k][-40:]
                speed["xrt"] = []
                speed["draw"] = speed["draw"][-40:]
            # wait for the next DISTINCT frame; a dead or held feed (stale, a
            # clip hold, the camera) times out at two periods so the roll and
            # the camera picture keep moving
            deadline = t0 + (1.0 / REST_FPS if REST["on"] else 2 * present_period * stride)
            while not st.stop.is_set():
                with st.lock:
                    nf, nf_at = st.frame, st.frame_at
                    backlog = queued and bool(st.fifo)
                if backlog or (nf_at != crissa_at and nf != drawn) or time.time() >= deadline:
                    break
                remain = deadline - time.time()
                if remain <= 0:
                    break
                st.frame_event.clear()
                with st.lock:
                    if st.frame_at != crissa_at and st.frame != drawn:
                        break
                st.frame_event.wait(timeout=remain)
        if film is not None:
            film.close()

    threading.Thread(target=render_worker, daemon=True, name="wall-film").start()
    threading.Thread(target=lobe_worker, daemon=True, name="wall-lobe").start()
    threading.Thread(target=register_worker, daemon=True, name="wall-register").start()
    threading.Thread(target=wizard_worker, daemon=True, name="wall-wizard").start()
    threading.Thread(target=hud_worker, daemon=True, name="wall-hud").start()
    threading.Thread(target=cinema_worker, daemon=True, name="wall-cinema").start()

    def show_worker():
        """RENDER SHOW (the User 2026-09-13: "show the renders from Wall OR CP
        whichever the User picks"). Lila's studio names a finished still or
        clip in /v1/house studio.show; when the pick is the wall this paints
        it in the centre box (a still for SHOW_STILL_S, a clip once) and
        fades it out. SILENT: the ear is open in Render and sound would be
        heard as the User. A sibling of the first-boot cinema, never that
        thread (it holds the ear closed and posts cinema_done). Never topmost."""
        import cv2 as _cv2
        cache = Path.home() / ".cache" / "house-wall" / "show"
        cache.mkdir(parents=True, exist_ok=True)
        seen = None
        while not st.stop.is_set():
            with st.lock:
                house = st.house or {}
            studio = house.get("studio") if isinstance(house.get("studio"), dict) else {}
            show = studio.get("show") if isinstance(studio.get("show"), dict) else {}
            ts = show.get("ts")
            if seen is None:
                seen = ts or 0          # a wall restart never replays an old show
            rendering = str((house.get("gpu") or {}).get("mode") or "") == "render"
            if not ts or ts == seen or show.get("on") != "wall" or not rendering or _init_running():
                time.sleep(0.5)
                continue
            seen = ts
            name = Path(str(show.get("name") or "")).name
            frames: list = []
            fps = 24.0
            try:
                data = http_get(HOUSE + str(show.get("url") or ""), timeout=20.0)
                if not data:
                    raise OSError("empty")
                if show.get("kind") == "video":
                    f = cache / name
                    f.write_bytes(data)
                    cap = _cv2.VideoCapture(str(f))
                    fps = float(cap.get(_cv2.CAP_PROP_FPS) or 24.0)
                    while len(frames) < int(fps * SHOW_CLIP_MAX_S):
                        ok, fr = cap.read()
                        if not ok:
                            break
                        frames.append(R.Image.fromarray(_cv2.cvtColor(fr, _cv2.COLOR_BGR2RGB)))
                    cap.release()
                else:
                    frames = [R.Image.open(io.BytesIO(data)).convert("RGB")]
            except Exception as e:  # noqa: BLE001
                log(f"show: {name} {type(e).__name__}: {e}"[:140])
                continue
            if not frames:
                continue
            log(f"show: {name} on the wall ({len(frames)} frame(s), silent)")
            box = R.cinema_rect(W, H, 1.0)
            moving = len(frames) > 1
            hold = len(frames) / fps if moving else SHOW_STILL_S
            t0 = time.time()
            while not st.stop.is_set() and not _init_running():
                t = time.time() - t0
                if t >= hold + SHOW_FADE_S:
                    break
                fr = frames[min(len(frames) - 1, int(t * fps))] if moving else frames[0]
                a = 255
                if t < SHOW_FADE_S:
                    a = int(255 * t / SHOW_FADE_S)
                elif t > hold:
                    a = int(255 * max(0.0, 1.0 - (t - hold) / SHOW_FADE_S))
                try:
                    out_q.put({"show": (R.cinema_frame_rect(fr, W, H, box, 0, a), (0, 0))}, timeout=0.2)
                except queue.Full:
                    pass
                time.sleep(1.0 / (fps if moving else 12.0))
            try:
                out_q.put({"show": None}, timeout=0.5)
            except queue.Full:
                pass

    threading.Thread(target=show_worker, daemon=True, name="wall-show").start()

    stats = {"frames": 0, "last_log": time.time(), "was_stale": None}

    def _dial_hot() -> bool:
        """The pointer on the clock's own circle — the dial's hover."""
        ptr_ = ptr_state["xy"] if (ptr_state["xy"] and time.time() - ptr_state["at"] < PTR_HOLD_S) else None
        ora_ = R.ANCHORS.get("oracle")
        if not ptr_ or not ora_:
            return False
        return math.hypot(ptr_[0] - float(ora_[0]), ptr_[1] - float(ora_[1])) <= float(ora_[2]) / 2.0

    pieces: dict = {}    # a PIECE_KEYS layer -> [[item, PhotoImage, size], ...] (LAYER_PIECES)

    def _hide_pieces(key):
        for rec in pieces.get(key, []):
            cv.itemconfig(rec[0], state="hidden")

    def _blit_pieces(key, item, im, origin=(0, 0)):
        """A soft layer as the pieces it draws (LAYER_PIECES): the layer's own item stays empty and keeps its place in
        the stack; each piece is an item just above it, reused while its size holds. `origin`: where the layer's top
        left goes (PIECE_KEYS_PLACED)."""
        got = [(x + int(origin[0]), y + int(origin[1]), crop) for x, y, crop in layer_pieces(im)]
        lst = pieces.setdefault(key, [])
        for j, (x, y, crop) in enumerate(got):
            if j < len(lst):
                it, ph_, size = lst[j]
                if size == crop.size:
                    ph_.paste(crop)
                else:
                    ph_ = ImageTk.PhotoImage(crop)
                    cv.itemconfig(it, image=ph_)
                    lst[j] = [it, ph_, crop.size]
                cv.coords(it, x, y)
                cv.itemconfig(it, state="normal")
            else:
                ph_ = ImageTk.PhotoImage(crop)
                it = cv.create_image(x, y, anchor="nw", image=ph_)
                cv.tag_raise(it, item)
                lst.append([it, ph_, crop.size])
        for rec in lst[len(got):]:
            cv.itemconfig(rec[0], state="hidden")

    def _blit(key, item, im):
        """Upload a layer. Paste into the layer's existing PhotoImage when the
        size matches: measured 9.9 ms against 19.5 ms for a fresh PhotoImage
        of a 2560x1440 frame, all of it on the Tk thread (2026-09-06, the
        24 fps chop). A new image only when the size changes."""
        if LAYER_PIECES and key in PIECE_KEYS:
            _blit_pieces(key, item, im)
            last_im[key] = im
            if hot["shown"] and hot["repaint"] is not None:
                hot["repaint"]()
            return
        ph = photos.get(key)
        if ph is not None and (ph.width(), ph.height()) == im.size:
            ph.paste(im)
        else:
            photos[key] = ImageTk.PhotoImage(im)
            cv.itemconfig(item, image=photos[key])
        if key in ("bg", "roll", "hud", "chrome"):
            last_im[key] = im
            if hot["shown"] and hot["repaint"] is not None:
                hot["repaint"]()

    ontop = {"on": False, "at": 0.0}

    def _redrawn():
        """HOUSE_WALL_DRAW_NOW=0 only: Tk's own redraw ran (an idle callback queued after the paste runs in the same idle
        pass as the canvas's): the pictures pasted since are one picture in the window."""
        redraw["armed"] = False
        speed["drawn"] += 1

    def _draw_now():
        """The pasted picture into the window now (DRAW_NOW), else count the redraw when Tk gets to it."""
        if DRAW_NOW:
            t_d = time.perf_counter()
            cv.update_idletasks()
            speed["draw"].append((time.perf_counter() - t_d) * 1000.0)
            speed["drawn"] += 1
        elif DRAWN_LINE and not redraw["armed"]:
            redraw["armed"] = True
            root.after_idle(_redrawn)

    hand = {"at": 0.0}                     # INPUT_FIRST: when the person's input last reached the pump
    wdog = {"at": time.time()}             # the last pump pass that pinged systemd's watchdog (by real time)

    def _input_first() -> None:
        """Before a picture: Tk's pending window events and the polled pointer's motion (INPUT_FIRST)."""
        try:
            n = 0
            while n < INPUT_FIRST_MAX and root.tk.dooneevent(_tkinter.WINDOW_EVENTS | _tkinter.DONT_WAIT):
                n += 1
            if motion_flag.is_set():
                motion_flag.clear()
                n += 1
                on_motion()
            if n:
                hand["at"] = time.time()
        except Exception as e:  # noqa: BLE001 - the picture goes up all the same
            if not hand.get("err"):
                hand["err"] = True
                log(f"input first: {type(e).__name__}: {e}"[:160])

    def pump():
        try:
            took = 0
            while True:
                if INPUT_FIRST:
                    _input_first()
                    if took and time.time() - hand["at"] < INPUT_FIRST_S:
                        break                      # the hand is busy: one picture this pass, Tk's turn before the next
                pkt = out_q.get_nowait()
                took += 1
                if "bg" in pkt:
                    if AR_PLANE == "always" or (
                        AR_PLANE == "lease" and _ar_plane_leased()
                    ):
                        try:
                            shm.write_bg(pkt["bg"])
                        except OSError:
                            pass
                    t_tk = time.time()
                    _blit("bg", bg_item, pkt["bg"])
                    stats["frames"] += 1
                    speed["glass"] += 1
                    _draw_now()
                    if PAINT_TRACE:
                        paint_trace({"w": "tk", "kind": "whole", "arr": pkt.get("arr"), "t": t_tk,
                                     "ms": (time.time() - t_tk) * 1000.0})
                    if pkt.get("stamp"):
                        lag = (time.time() - float(pkt["stamp"])) * 1000.0
                        if 0.0 <= lag < 5000.0:
                            with st.lock:
                                st.lags.append(lag)
                                del st.lags[:-600]
                if "bg_patch" in pkt and photos.get("bg") is not None:
                    im, (x, y) = pkt["bg_patch"]
                    t_tk = time.time()
                    pp = photos.get("bg_patch")
                    if pp is not None and (pp.width(), pp.height()) == im.size:
                        pp.paste(im)
                    else:
                        pp = photos["bg_patch"] = ImageTk.PhotoImage(im)
                    root.tk.call(str(photos["bg"]), "copy", str(pp), "-to", x, y)
                    full_bg = last_im.get("bg")
                    if full_bg is not None:
                        full_bg.paste(im, (x, y))      # the hover repaint and the AR plane read the whole picture
                    if hot["shown"] and hot["repaint"] is not None:
                        hot["repaint"]()
                    stats["frames"] += 1
                    speed["glass_patch"] += 1
                    _draw_now()
                    if PAINT_TRACE:
                        paint_trace({"w": "tk", "kind": "patch", "arr": pkt.get("arr"), "t": t_tk,
                                     "ms": (time.time() - t_tk) * 1000.0, "px": im.size[0] * im.size[1],
                                     "box": [x, y, im.size[0], im.size[1]]})
                    if pkt.get("stamp"):
                        lag = (time.time() - float(pkt["stamp"])) * 1000.0
                        if 0.0 <= lag < 5000.0:
                            with st.lock:
                                st.lags.append(lag)
                                del st.lags[:-600]
                if "ft" in pkt:
                    im, (x, y) = pkt["ft"]
                    _blit("ft", ft_item, im)
                    cv.itemconfig(ft_item, state="normal")
                    cv.coords(ft_item, x, y)
                for key, item in (("lobe", lobe_item), ("beam", beam_item), ("scan", scan_item), ("door", door_item), ("register", register_item), ("init", init_item), ("cinema", cinema_item), ("show", show_item)):
                    if key in pkt and LAYER_PIECES and key in PIECE_KEYS_PLACED:
                        got = pkt[key]
                        if got is None:
                            _hide_pieces(key)
                        else:
                            _blit_pieces(key, item, got[0], origin=got[1])
                        continue
                    if key in pkt:
                        got = pkt[key]
                        if got is None:
                            cv.itemconfig(item, state="hidden")
                        else:
                            im, (x, y) = got
                            if key in ("lobe", "door") and not _dial_hot():
                                # the dial's lamps rest at the clock's mute
                                # (the User 2026-09-09: "dim the lamps too"); the
                                # pointer on the dial brings them up with it
                                im = _with_alpha(im, R.DIAL_MUTE)
                            _blit(key, item, im)
                            cv.itemconfig(item, state="normal")
                            cv.coords(item, x, y)
                if "register" in pkt or "init" in pkt:
                    paint_ui()      # the keys join (or leave) the input shape
                if "hud" in pkt:
                    _blit("hud", hud_item, pkt["hud"])
                    if ui["on"]:
                        paint_ui()  # cache: only when anchors/prefs actually moved
                if pkt.get("ft_hide"):
                    cv.itemconfig(ft_item, state="hidden")
                if "roll" in pkt:
                    _blit("roll", roll_item, pkt["roll"])
        except queue.Empty:
            pass
        now = time.time()
        # Ping systemd's watchdog on a real-time clock (not a tick count), so a
        # busy 60 fps loop and a resting 2 fps loop both keep the 30 s watchdog
        # happy. A pump stuck between passes stops pinging and systemd sees it.
        if _watchdog_due(wdog, now):
            _sd_watchdog()
        if TAKE_PICK and now - pick["at"] >= 1.0 and not crissa_home.is_house():
            pick["at"] = now
            got_ = pick_to_take(crissa_home.HOME, pick["ts"])
            if got_ is not None:
                pick["ts"] = got_[1]
                log("density: %s's pick %.2f, taken" % (got_[2], got_[0]))
                _glass_post({"density": got_[0]})
        if DRAWN_LINE and now - speed["xrt_at"] >= 1.0 and not REST["on"]:
            # once a second, a round trip to the X server (XQueryPointer): it answers after every request before it,
            # so this is how far behind the pictures already sent the server is (Xwayland on a desk; a few ms)
            speed["xrt_at"] = now
            t_x = time.perf_counter()
            try:
                root.winfo_pointerxy()
                speed["xrt"].append((time.perf_counter() - t_x) * 1000.0)
            except Exception:  # noqa: BLE001
                pass
        # THE WALL ON TOP WHILE THE WIZARD RUNS (the User 2026-09-12 pm, from
        # his screenshots: the desktop folders and the dock sat over the
        # onboarding in every frame — a wallpaper-level window is below
        # them by design). Above for the routine, back down at the wrap:
        # that is also the "icons out" of the reveal.
        if now - ontop["at"] > 0.5:
            ontop["at"] = now
            with st.lock:
                wizard = str((st.init or {}).get("status") or "") == "running"
                # Render is NOT a reason (the User 2026-09-16: Render should "feel
                # like BAU just with Lila at the camera"). 15:41's render-host
                # lift put the fullscreen wall over every window, the CP too.
                want = wizard
            if want and not crissa_home.is_house():
                # A PACK'S SETUP IS IN HER WINDOW (round 15, .101 2026-10-12: "trapped in init screen ... nothing will
                # front the Wallpaper"): the Wall stays at the desktop layer for the walk, under her Setup window
                # (lib/crissa_window.wall_on_top_for_setup; CRISSA_WALL_WIZARD_TOP=1 = the old rule in a pack)
                try:
                    import crissa_window
                    want = crissa_window.wall_on_top_for_setup(
                        "running", house=False,
                        in_window=crissa_window.setup_in_own_window() or _setup_in_her_window(st.init or {}))
                except Exception as e:  # noqa: BLE001  no window module: the old rule
                    log(f"setup in front: {type(e).__name__}: {e}"[:120])
                if not want and not ontop.get("said_setup"):
                    ontop["said_setup"] = True
                    log("Setup runs in her window: the wall stays at the wallpaper level, under it")
            if want != ontop["on"]:
                ontop["on"] = want
                try:
                    root.attributes("-topmost", want)
                    if want:
                        root.lift()
                    why = "wizard" if wizard else "over"
                    log("wall %s (%s)" % ("on top" if want else "back to the wallpaper level", why))
                except Exception as e:  # noqa: BLE001
                    log(f"topmost: {type(e).__name__}: {e}"[:120])
                # THE FOLDERS (the User 2026-09-13 04:47 run: "no video. bars
                # folders never hid… buttons none worked"). DING and this
                # wall share Mutter's desktop layer and the newer map wins;
                # -topmost cannot lift a desktop-layer window over the icons,
                # so with Folders on they sat over the routine and took the
                # clicks. Put DING down for the wizard; cycle it back at the
                # wrap (the reveal's "icons out" and back in).
                if want and bool(_prefs().get("folders")):
                    ontop["ding_off"] = True
                    log("folders down for the wizard")
                    threading.Thread(target=_ding, args=(False,), daemon=True).start()
                elif not want and ontop.get("ding_off"):
                    ontop["ding_off"] = False
                    log("folders back after the wizard")
                    threading.Thread(target=_ding, args=(True,), daemon=True).start()
        stale = st.stale()
        if stale != stats["was_stale"]:
            stats["was_stale"] = stale
            log("feed STALE — frame_no not advancing" if stale else "feed live — frame_no advancing")
        if now - stats["last_log"] >= LOG_EVERY_S:
            with st.lock:
                fno = st.frame_no
            log(f"ok frames={stats['frames']} ({stats['frames'] / LOG_EVERY_S:.1f}/s) frame_no={fno} stale={stale}")
            stats["frames"] = 0
            stats["last_log"] = now
        # 15 ms idle after a drain capped us at ~22/s (one 10 ms blit + 15).
        # 1 ms lets Tk keep up with the film thread (Brave's mjpg is 48).
        # Resting (REST, a pack), the film comes REST_FPS a second: 25 ms is plenty.
        root.after(25 if REST["on"] else 1, pump)

    floater = {"win": None}

    def _jpeg():
        with st.lock:
            raw = st.frame
        return R._open(raw) if raw else None

    def open_float():
        cur = floater.get("win")
        if cur is not None and getattr(cur, "alive", False):
            cur.close()
            return
        box = R.HOTSPOTS.get("ft")
        if not box:
            return
        x0, y0, x1, y1 = box
        origin = (int(x0), int(y0), int(x1 - x0), int(y1 - y0))
        win = F.Float(root, origin, _jpeg, _prefs, (W, H))
        floater["win"] = win
        win.open()

    def toggle_oracle():
        p = _prefs()
        on = not p.get("oracle_dot", False)
        p["oracle_dot"] = on
        prefs.save(p)
        _glass_post({"oracle_dot": on})
        oracle_anim["src"] = oracle_anim.get("scale", oracle_anim["dst"])
        oracle_anim["dst"] = 0.05 if on else 1.0
        oracle_anim["t0"] = time.time()
        log("oracle collapsed" if on else "oracle restored")

    # the rail hangs from the GNOME bar's bottom edge. hidetopbar drops the
    # bar's strut, so _NET_WORKAREA says 0: the rail rests at the top and
    # the bar, when it comes out, pushes it down (the poll thread reads the
    # bar off house-pointer's "shell", or house-shell's Poll once live).
    rail_top = 0   # the top of the glass is GNOME's (the User 2026-09-08: "cede the top to Ubuntu"); the rail is in the dock's band
    # hold to pan (defined here so paint_ui can read it): key = the arrow
    # down, lit_until = a short glow after a tap so a click shows
    pad = {"key": None, "job": None, "busy": False, "n": 0, "last": None, "lit_until": 0.0, "note": None, "joined_at": 0.0,
           "acc": (0.0, 0.0), "moved_at": 0.0, "home_job": None}
    ui = {"on": True, "until": time.time() + 8.0, "menu": False, "armed": time.time() + 0.5, "armed_xy": None,
          "rail_top": rail_top}
    chrome_sig = {"k": None}
    click_gate = {"t": 0.0, "name": None}
    shape = InputShape()
    crissa_key: dict = {}          # the Crissa key (lib/crissa_wall_key): its box for the input shape, once it is up
    wall_keys: dict = {}           # what the menu key may ask of this Wall (2026-10-28): "dock_press" = a dock key's press

    def _kiosk_hole() -> bool:
        """screen.crissa_hole from :8099 — the kiosk's AR, the one state."""
        try:
            with st.lock:
                scr = (st.house or {}).get("screen") or {}
            return bool(scr.get("crissa_hole"))
        except Exception:
            return False

    def _kiosk_zoom() -> float:
        """screen.fov_zoom_crissa from :8099 — the page's #arZoom, the one number."""
        try:
            with st.lock:
                scr = (st.house or {}).get("screen") or {}
            z = float(scr.get("fov_zoom_crissa") or 1.0)
            return z if z > 0 else 1.0
        except Exception:
            return 1.0

    def _user_authed() -> bool:
        try:
            with st.lock:
                h, pr = st.house, st.presence
            return bool(R.user_authed(h, pr))
        except Exception:
            return False

    # THE CHROME FADES AS ONE. the User 2026-09-06: "We need those collapse
    # buttons to all fade together now". One image carries rail, carets,
    # bars, dot and dials, so one alpha ramp (CHROME_FADE_S) takes them all
    # out together at idle and brings them all back on motion. Hits switch at
    # once (bare glass clicks through the moment the fade starts).
    fade = {"k": 0.0, "on": False, "src": None, "t": 0.0, "job": None, "band": [], "dst": None}

    def _chrome_frame(im, k):
        # alpha for everything (CHROME_FADE_S), the rail's slide on top of it
        return _with_alpha(rail_slide(im, k, fade["band"]), k)

    def _with_alpha(im, k):
        if k >= 0.999:
            return im
        out = im.copy()
        out.putalpha(im.getchannel("A").point(lambda a, k=k: int(a * k)))
        return out

    def _fade_step():
        fade["job"] = None
        now = time.time()
        dt = now - fade["t"]
        fade["t"] = now
        target = 1.0 if fade["on"] else 0.0
        step = dt / CHROME_FADE_S
        k = fade["k"]
        k = min(target, k + step) if target > k else max(target, k - step)
        fade["k"] = k
        src = fade["src"]
        if src is None:
            return
        out = _chrome_frame(src, k)
        if fade["dst"] is not None and k < 0.999:
            # what the idle keeps (the open dock) sits under the fading chrome
            base = fade["dst"].copy()
            base.alpha_composite(out)
            out = base
        _blit("chrome", chrome_item, out)
        if abs(k - target) > 0.001:
            fade["job"] = root.after(33, _fade_step)

    def _chrome_show(im, on: bool):
        """im is the target frame (empty while idle). Ramp toward it."""
        try:                                   # the Crissa key dims with the idle chrome and never goes (2026-10-10)
            import crissa_wall_key
            if crissa_key.get("box"):
                crissa_wall_key.dim(not on)
        except Exception:  # noqa: BLE001
            pass
        if on:
            fade["src"] = im          # fade the new chrome in (or swap it at full)
            fade["band"] = list(R.RAIL_BAND)
        else:
            fade["dst"] = im          # the idle image: the open dock, else empty (2026-09-09)
        # while idle the source stays the last drawn chrome, fading out
        if on != fade["on"]:
            fade["on"] = on
            fade["t"] = time.time()
            if fade["job"] is None:
                fade["job"] = root.after(1, _fade_step)
        elif on:
            _blit("chrome", chrome_item, _chrome_frame(im, fade["k"]))
        elif fade["k"] <= 0.001:
            _blit("chrome", chrome_item, im)

    def _letter_input_box():
        """The letter's rect while it is up, for the input shape: empty glass passes the pointer through to the desktop,
        so without it the wheel over the letter reached the wall only where a chrome box overlapped it (the PAUSED
        chip, the small tile) (the User 2026-10-09). HOUSE_LETTER_TAKES_POINTER=0 = the old shape."""
        if os.environ.get("HOUSE_LETTER_TAKES_POINTER", "1").strip().lower() in ("0", "false", "no", "off"):
            return None
        lr = R.LETTER.get("rect")
        if not lr or not doc_placement.scroll_enabled():
            return None
        return tuple(int(round(v)) for v in lr)

    def paint_ui():
        p = dict(_prefs())
        p["hole_ar"] = _kiosk_hole()   # mirror, never the local pref
        p["hole_zoom"] = _kiosk_zoom()  # the page's hole-width dial, mirrored
        if wall_frame.mode() == "whole":
            # the same two keys zoom her film under the whole frame (2026-10-12), AR on or off (round 14)
            p["hole_zoom"] = float(p.get("frame_zoom", 1.0))
            p["zoom_is_frame"] = True
        bk_ = _big_k(time.time())
        p["zos_armed"] = (time.time() - float(ui.get("zos_armed_at") or 0.0)) <= 4.0
        p["cam_big"] = big["which"] == "camera" and bk_ >= 0.5   # no fold carets under an enlarged corner
        p["ft_big"] = big["which"] == "ft" and bk_ >= 0.5
        p.update(_place_keys(p))
        p["big_k"] = round(bk_, 2) if big["which"] else 0.0       # in the sig: repaint each step
        ubuntu_ = not dock.own_dock()          # Ubuntu's dock is the dock: no dots, no Clear desktop glyph
        p["dock_ubuntu"] = ubuntu_
        p["menu_right"] = dock.ubuntu_key_right("menu", W) if ubuntu_ else None
        run_ = {} if ubuntu_ else dock.poll_running()
        p["dock"] = tuple((it["id"], it.get("name") or "", it.get("png") or "", int(run_.get(it["id"], 0))) for it in dock.items())
        # the desk's own tiles after APPS: the folders, then Clear desktop
        # (its glyph follows Mutter's showing-desktop hint, polled with the dots)
        p["places"] = tuple((pl["id"], pl.get("name") or "", pl.get("png") or "", pl.get("path") or "") for pl in dock.places())
        p["desk_clear"] = False if ubuntu_ else dock.poll_showing()
        with st.lock:
            _pres = st.presence
        p["cam_authed"] = R.camera_authed(_pres)   # the joystick lights only when PRESENCE has the User (it answers the nudge)
        p["pad_key"] = pad["key"] or (pad["last"] if time.time() < pad["lit_until"] else None)
        _n = pad.get("note")
        p["cam_note"] = (_n.get("text") if _n and time.time() < float(_n.get("until") or 0) else None)
        # the dock's window chooser: over a tile with 2+ instances (or the
        # stack it opened), that tile's windows ride along for the renderer
        ptr_d = ptr_state["xy"] if (ptr_state["xy"] and time.time() - ptr_state["at"] < PTR_HOLD_S) else None
        pick_i = None
        if ptr_d:
            for n_, box_ in R.CHROME_HITS.items():
                if n_.startswith("dock_") and n_[5:].isdigit() and R._inside(ptr_d, box_):
                    pick_i = int(n_[5:])
            if pick_i is None and dock_pick.get("i") is not None and R._inside(ptr_d, R.CHROME_HITS.get("dock_pick_box")):
                pick_i = dock_pick["i"]
        now_p = time.time()
        if pick_i is not None and pick_i < len(p["dock"]) and p["dock"][pick_i][3] >= 1:
            it_ = dock.items()[pick_i]
            wins_ = list(dock.windows(it_)) + [{"id": "new", "title": "＋ new window", "via": "new"}]
            dock_pick.update(i=pick_i, wins=wins_, hold=None)
        elif dock_pick.get("i") is not None:
            # off the tile and the stack: hold a beat before closing, longer
            # when the pointer is simply unknown (see PICK_HOLD_S)
            if dock_pick.get("hold") is None:
                dock_pick["hold"] = now_p + (PICK_HOLD_LOST_S if ptr_d is None else PICK_HOLD_S)
            elif now_p >= dock_pick["hold"]:
                dock_pick.update(i=None, wins=[], hold=None)
        else:
            dock_pick.update(i=None, wins=[], hold=None)
        p["dock_pick"] = {"i": dock_pick["i"], "wins": tuple((w["id"], w["title"], w["via"], w.get("thumb") or "") for w in dock_pick["wins"])} if dock_pick["i"] is not None else None
        pip = tuple(R.ANCHORS.get("pip") or ())
        ora = tuple(R.ANCHORS.get("oracle") or ())
        vt = tuple(R.ANCHORS.get("vitals_tog") or ())
        md = tuple(R.ANCHORS.get("mode") or ())
        # the pointer (when this desktop gives one): the carets scope to the
        # box under it, like the kiosk's hover
        ptr = ptr_state["xy"] if (ptr_state["xy"] and time.time() - ptr_state["at"] < PTR_HOLD_S) else None
        p["ptr"] = ptr
        p["wall_key"] = WALL_KEY_ON                # round 26: the tile's WALL key (a pack's desk Wall)
        # the dial's hover is white rings on the chrome (fast, one layer);
        # only at full size, never the dot
        if ptr and ora and not p.get("oracle_dot") and abs(oracle_anim.get("scale", 1.0) - 1.0) < 0.02 \
                and math.hypot(ptr[0] - ora[0], ptr[1] - ora[1]) <= ora[2] / 2.0:
            p["dial_hover"] = True
        cam_hs = tuple(R.HOTSPOTS.get("camera") or ())
        ft_hs = tuple(R.HOTSPOTS.get("ft") or ())
        letter_box = _letter_input_box()
        sig = (ui["on"], ui["menu"], tuple(sorted((k, v) for k, v in p.items() if k != "ptr")), tuple(sorted(R.REGISTER_HITS.items())), tuple(sorted(R.INIT_HITS.items())),
               ptr, pip, ora, vt, md, ui["rail_top"], big["which"], cam_hs, ft_hs, letter_box)
        if sig == chrome_sig["k"] and "chrome" in photos:
            return
        chrome_sig["k"] = sig
        pr = dict(p, dock=[{"id": a_, "name": b_, "png": c_, "running": n_} for a_, b_, c_, n_ in p.get("dock", ())],
                  places=[{"id": a_, "name": b_, "png": c_, "path": d_} for a_, b_, c_, d_ in p.get("places", ())],
                  dock_pick=({"i": p["dock_pick"]["i"], "wins": [{"id": a_, "title": b_, "via": c_, "thumb": d_} for a_, b_, c_, d_ in p["dock_pick"]["wins"]]} if p.get("dock_pick") else None))
        im = R.render_chrome(g, pr, on=ui["on"], menu=ui["menu"], rail_top=ui["rail_top"])
        _chrome_show(im, bool(ui["on"]))
        # the dock's tiles in screen px, for the shell: a window CLOSE sweeps
        # into its tile, else the dock's handle (the wall's shell extension).
        # Ubuntu's dock: nothing drawn, nothing to sweep into (cleared at boot).
        if dock.own_dock():
            try:
                its = dock.items()
                tiles = {}
                for n_, box_ in R.CHROME_HITS.items():
                    if n_.startswith("dock_") and n_[5:].isdigit() and int(n_[5:]) < len(its):
                        tiles[its[int(n_[5:])]["id"]] = box_
                hub = R.CHROME_HITS.get("dock_plate") or R.CHROME_HITS.get("dock_fold")
                if hub:
                    dock.write_geometry(tiles, hub)
            except Exception as e:  # noqa: BLE001
                log(f"dock geometry: {e}")
        # Desk folders must receive clicks. Only chrome rects take input.
        # Whatever the chrome drew takes clicks — with the rail idle that is
        # the persistent set: carets, the dot, the contracted bars (the User
        # 2026-09-06: the way back has to be on the glass).
        boxes = []
        for n, box in list(R.CHROME_HITS.items()) + list(R.REGISTER_HITS.items()) + list(R.INIT_HITS.items()):
            if n == "menu_panel":
                continue
            boxes.append(box)
        if letter_box:
            boxes.append(letter_box)    # the whole letter takes the wheel (the User 2026-10-09)
        if crissa_key.get("box"):
            boxes.append(crissa_key["box"]())      # the Crissa key always takes its clicks (2026-10-10)
            if crissa_key.get("plate"):
                boxes.append(crissa_key["plate"]())   # and its "Agent closed · Open Agent" plate (round 15)
            boxes = [b for b in boxes if b]
        if ui["menu"] or (crissa_key.get("panel_open") and crissa_key["panel_open"]()):
            # Advanced open: the whole glass takes the click, so one press
            # anywhere off the panel closes it (the User 2026-09-07: "disappears
            # on click of anywhere else"). Folders get their clicks back the
            # moment it closes. A Crissa pop-up on the Wall is the same (round 15).
            boxes = [(0, 0, W, H)]
        if os.environ.get("HOUSE_WALL_SHAPE_LOG") == "1" and boxes != chrome_sig.get("shape"):
            chrome_sig["shape"] = list(boxes)            # a debug switch (off): the regions that take clicks, on a change
            log("input shape: %d boxes, ft %s: %s" % (len(boxes), tuple(R.HOTSPOTS.get("ft") or ()),
                                                     [tuple(int(v) for v in b) for b in boxes][:40]))
        shape.set(boxes)

    def _ding(on: bool) -> None:
        """GNOME desktop icons (ding@rastersoft.com) on or off. Both DING and
        this wallpaper sit in Mutter's desktop layer and the newer map wins,
        so SHOW re-enables the extension AFTER the wall is up: its icons come
        back above the glass. Empty-glass clicks still pass through (X input
        shape), so the icons keep their clicks either way.
        THE HOUSE ONLY (2026-10-10): a pack never turns the person's GNOME extensions on or off. A pack's FOLDERS key
        moves the Wall instead (THE WALL KEEPS ITS CLICKS, below): shown = the Wall under the icons, hidden = above."""
        if not crissa_home.is_house():
            pack_stack["folders"] = bool(on)
            pack_stack["due"] = True
            return
        try:
            uuid = ding_uuid()
            if on:
                # DING is a Wayland client (it never appears in the X client
                # list), and `enable` on an already-enabled extension is a
                # no-op — so after a wall restart the newer wall sat over the
                # icons and SHOW changed nothing (the User 2026-09-07: "Folders
                # do not appear at all"). Cycle it: fresh windows, mapped
                # after the wall. Two copies enabled would be two icon sets,
                # so stock is put down first.
                if uuid != DING_STOCK_UUID:
                    subprocess.run(["gnome-extensions", "disable", DING_STOCK_UUID], timeout=6, check=False)
                # the cycle is only needed when DING is already up (under the
                # wall); from HIDDEN it is off, and the 0.6 s wait is a wait
                # for nothing (the User 2026-09-13: pressed again 2.9 s after
                # SHOW, before the icons had mapped)
                try:
                    info = subprocess.run(["gnome-extensions", "info", uuid], capture_output=True, text=True, timeout=6).stdout
                except Exception:
                    info = ""
                if "State: ACTIVE" in info:
                    subprocess.run(["gnome-extensions", "disable", uuid], timeout=6, check=False)
                    time.sleep(0.6)
            subprocess.run(["gnome-extensions", "enable" if on else "disable", uuid], timeout=6, check=False)
            log(f"ding {'cycled + enabled' if on else 'disabled'} ({uuid})")
            if on:
                time.sleep(1.2)           # let DING map before the wall drops under it
                # DING maps and lowers itself (the house fork still lowers
                # at map, emulateX11WindowType.js:229/246) — and its process
                # takes a variable second or two to map. One lower of the
                # wall at 1.2 s raced it: whichever lowered LAST ended on
                # top, so SHOW "never worked" (the User 2026-09-13 05:14). Keep
                # dropping the wall every half second for a while after the
                # enable; the last lower is then always the wall's. Tk only
                # from the Tk thread.
                lower_until["t"] = time.time() + LOWER_AFTER_SHOW_S
                lower_flag.set()
        except Exception as e:
            log(f"ding: {e}")

    lower_flag = threading.Event()
    lower_until = {"t": 0.0, "last": 0.0, "n": 0}
    LOWER_AFTER_SHOW_S = 7.0
    # THE WALL KEEPS ITS CLICKS (the User 2026-10-25, the 1.0.0 stranger install: GNOME's desktop icons - stock Ubuntu
    # ships them on - mapped after the Wall, sat over it in the same desktop layer and took every click: the Crissa
    # menu's keys did nothing and Secure's double click went to a folder). A pack never touches the person's
    # extensions, so it keeps its place by stacking alone: while the folders are hidden the Wall raises itself within
    # the desktop layer every PACK_RESTACK_S (never above an ordinary window), and the FOLDERS key lowers it under the
    # icons until it is pressed again. Kill: CRISSA_WALL_KEEPS_CLICKS=0 (1.0.0: the Wall never restacks itself).
    pack_stack = {"folders": bool(_prefs().get("folders")), "due": True, "last": 0.0}
    PACK_RESTACK_S = 2.0
    pack_restack = (not crissa_home.is_house()
                    and os.environ.get("CRISSA_WALL_KEEPS_CLICKS", "1").strip().lower() not in ("0", "off", "false", "no"))

    def _lower_once():
        now = time.time()
        if pack_restack and (pack_stack["due"] or now - pack_stack["last"] >= PACK_RESTACK_S):
            pack_stack["due"], pack_stack["last"] = False, now
            try:
                if pack_stack["folders"]:
                    root.lower()                 # the person asked for their folders: the Wall goes under them
                else:
                    root.lift()                  # above the desktop icons, still under every ordinary window
            except Exception as e:  # noqa: BLE001
                log(f"restack: {e}")
        due = lower_flag.is_set() or (now < lower_until["t"] and now - lower_until["last"] >= 0.5)
        if due:
            lower_flag.clear()
            lower_until["last"] = now
            try:
                root.lower()
                lower_until["n"] += 1
                if lower_until["n"] == 1:
                    log("wall lowered under the desktop icons (and again every 0.5 s for %.0f s)" % LOWER_AFTER_SHOW_S)
            except Exception as e:  # noqa: BLE001
                log(f"lower: {e}")
        elif lower_until["n"] and now >= lower_until["t"]:
            log(f"wall lowered {lower_until['n']}× after the folders show")
            lower_until["n"] = 0
        root.after(250, _lower_once)
    root.after(1500, _lower_once)

    def _chip_post(path, body=None):
        try:
            data = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request(f"{CHIP}{path}", data=data, method="POST")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            crissa_egress.urlopen(req, 1.2, "the Wall")
        except Exception as e:
            log(f"chip {path}: {e}")

    def _chip_toggle(kind):
        _chip_post(f"/v1/chip/{kind}/toggle")

    def on_motion(_e=None):
        if _init_running() and not ui["on"]:
            # the wizard owns the glass: no rail, no dock, no band (the User
            # 2026-09-12 pm) — except a deliberate reach for the bottom edge,
            # the hot strip, which pops the rail over any window (his 13:16
            # clicks on the dock row landed on bare glass and nothing woke)
            try:
                at_edge = _e is not None and float(getattr(_e, "y", 0)) >= H - 24
            except (TypeError, ValueError):
                at_edge = False
            if not at_edge:
                return
        now = time.time()
        mark_input()
        # Mutter's idle monitor has no coordinates; a real Tk <Motion> does.
        # When one arrives we can scope the carets to the box under the
        # pointer, the way the kiosk does (the User 2026-09-07: "They only show
        # when you move mouse over it"). Without coordinates the carets ride
        # the chrome fade, which is this glass's version of reaching for it.
        x, y = getattr(_e, "x", None), getattr(_e, "y", None)
        if x is not None and y is not None:
            if not ptr_state.get("seen"):
                # once per process: <Leave> nulls xy on every exit now, so
                # keying this on None would print it at every re-entry
                ptr_state["seen"] = True
                log(f"pointer: Tk motion has coordinates ({int(x)},{int(y)}) — hover is live")
            ptr_state["xy"] = (int(x), int(y))
            ptr_state["at"] = now
        if not ui["on"]:
            ui["armed"] = now + CLICK_ARM_S
            ui["armed_xy"] = ptr_state["xy"] if (x is not None and y is not None) else None
            log("chrome on (pointer moved)")
        ui["on"] = True
        # ADVANCED POPS ON HOVER of ⋯ (the User 2026-09-08: "in lieu of the
        # instruction? One fewer clicks"); it goes when the pointer has been
        # off the rail and the panel for a beat (on_idle), or on a click away.
        if (not ui["menu"] and ptr_state["xy"] is not None and dock.advanced_on()
                and R._inside(ptr_state["xy"], R.CHROME_HITS.get("menu"))):
            ui["menu"] = True
            menu_dwell["left_at"] = None
            log("advanced open (hover)")
        if not ui["menu"]:
            ui["until"] = now + CHROME_IDLE_S
        paint_ui()

    def on_leave(_e=None):
        # LeaveNotify: the pointer stepped off the input shape — the page's
        # :hover ending. Drop the coordinates so nothing stays lit behind it.
        if ptr_state["xy"] is not None:
            ptr_state["xy"] = None
            ptr_state["at"] = 0.0
            paint_ui()

    motion_flag = threading.Event()
    # A WORKER THREAD NEVER CALLS root.after: it queues here and on_idle (the Tk
    # thread, every 120 ms) schedules it (poll_pointer's rule, 4433: two SEGVs)
    tk_q: queue.SimpleQueue = queue.SimpleQueue()

    def tk_soon(fn, ms: int = 0) -> None:
        tk_q.put((int(ms), fn))

    pops_away = threading.Event()     # input elsewhere while a popup is up (poll thread sets, Tk loop closes)
    shell_state = {"pl": None, "at": 0.0}   # the shell poll's last answer (poll thread writes, the dwell rule reads)
    menu_dwell = {"left_at": None, "grace_until": 0.0}

    def close_menu(why: str) -> bool:
        """ONE CLOSE FOR THE MENU AND ITS BAND (round 40, the User 2026-10-21 on the house Wall: "menu wont close"). The
        pointer-left rule closed Advanced but left the band up, so the next dock press toggled the menu OPEN again over
        the band. Every close now takes both down: Advanced, the band, the chooser. True when anything was open."""
        was = bool(ui["menu"] or hot.get("shown") or dock_pick["i"] is not None)
        ui["menu"] = False
        ui["until"] = time.time() + 2
        menu_dwell["left_at"] = None
        menu_dwell["grace_until"] = 0.0
        if dock_pick["i"] is not None:
            dock_pick.update(i=None, wins=[], hold=None)
        if menu_one_close_on() and hot.get("shown") and callable(hot.get("hide")):
            hot["hide"]()
        if was:
            log("menu closed (%s)" % why)
            paint_ui()
        return was
    MENU_LEAVE_S = 0.8
    # ⋯ pressed in Ubuntu's dock: the pointer is on the shell's dock, not the
    # glass, and the press itself reads as input elsewhere — hold Advanced
    # open this long for the pointer to reach it (then the dwell rules)
    # round 40 ("menu wont close"): 4 s held every click elsewhere away right after the press; 1.5 s is enough for the
    # press itself (the input it makes) and short enough that a click away closes it. HOUSE_WALL_MENU_DOCK_GRACE_S
    MENU_DOCK_GRACE_S = float(os.environ.get("HOUSE_WALL_MENU_DOCK_GRACE_S", "1.5") or 1.5)

    def on_idle():
        while True:
            try:
                ms, fn = tk_q.get_nowait()
            except queue.Empty:
                break
            root.after(ms, fn)
        if motion_flag.is_set():
            motion_flag.clear()
            on_motion()
        if pops_away.is_set():
            pops_away.clear()
            closed = []
            graced = time.time() < menu_dwell["grace_until"]
            if ui["menu"] and not graced:
                if menu_one_close_on():
                    close_menu("input elsewhere")
                    closed.append("advanced")
                else:
                    ui["menu"] = False
                    ui["until"] = time.time() + CHROME_IDLE_S
                    menu_dwell["left_at"] = None
                    closed.append("advanced")
            if dock_pick["i"] is not None:
                dock_pick.update(i=None, wins=[], hold=None)
                closed.append("chooser")
            if closed:
                paint_ui()
            if hot.get("shown") and callable(hot.get("hide")) and not graced:
                hot["hide"]()
                closed.append("band")
            if closed:
                log("popups closed (input elsewhere): " + ", ".join(closed))
        # the hover-opened Advanced closes once the pointer has left the
        # rail and the panel for MENU_LEAVE_S (a Leave, or empty glass)
        if ui["menu"]:
            now_m = time.time()
            ptr_m = ptr_state["xy"] if (ptr_state["xy"] and now_m - ptr_state["at"] < PTR_HOLD_S) else None
            region = [R.CHROME_HITS.get(n) for n in ("menu", "menu_panel", "rail_plate")]
            inside = bool(ptr_m) and any(R._inside(ptr_m, b) for b in region if b)
            # THE SHELL KNOWS WHERE THE POINTER IS (the User 2026-10-20, the
            # dock-opened Advanced "won't disappear on click elsewhere"): on
            # Wayland the X pointer freezes once he is over a native window,
            # so the pop's Leave read "still inside" and ptr_state stayed
            # fresh; the only close left was a click on our own glass. The
            # poll's last answer (0.15 s old) says "another window / the bar"
            # - that is the pointer gone, whatever X thinks.
            if inside and menu_pointer_away(shell_state["pl"], shell_state["at"], now_m):
                inside = False
            if inside or now_m < menu_dwell["grace_until"]:
                menu_dwell["left_at"] = None
            elif menu_dwell["left_at"] is None:
                menu_dwell["left_at"] = now_m
            elif now_m - menu_dwell["left_at"] > MENU_LEAVE_S:
                if menu_one_close_on():
                    close_menu("pointer left")          # the band with it (the desync behind "menu wont close")
                else:
                    ui["menu"] = False
                    ui["until"] = now_m + CHROME_IDLE_S
                    menu_dwell["left_at"] = None
                    log("advanced closed (pointer left)")
                    paint_ui()
        # KEEP UNDER THE ICONS. DING lowers ITSELF every time Mutter raises it
        # (emulateX11WindowType.js: connect_after('raised') → lower()), and a
        # click on a folder raises it — so the icons showed until the first
        # click and then sank under the wall ("worked only once", the User
        # 2026-09-07). While folders are shown and the User is active, drop
        # the wall once a second; six XLowerWindows a second apart drew no
        # stack_position assertion from gnome-shell (13:36:48, checked).
        # No re-lower beat here. Stock DING sinks itself under the wall on
        # every desktop click; the 150 ms re-lower that fought it was pulled
        # on the User's word (2026-09-07 pm: "remove that patch so we do not
        # forget it") because the house copy of DING
        # (scripts/ding-house-install.sh) ends the fight at the next login.
        # The one lower after the DING cycle stays: the copy still lowers
        # itself once when it maps.
        # a pointer RESTING on the dial or a corner tile is a hover, not idle:
        # the white rings and the ember border are chrome, so the chrome stays
        # up until the pointer leaves (the page's :hover does not time out)
        if ui["on"] and not ui["menu"] and time.time() > ui["until"]:
            ptr_h = ptr_state["xy"] if (ptr_state["xy"] and time.time() - ptr_state["at"] < PTR_HOLD_S) else None
            if hot["inside"] or (ptr_h and any(R._inside(ptr_h, R.CHROME_HITS.get(n)) for n in
                                 ("oracle_hover", "camera", "ft", "vitals_hover", "rail_plate", "dock_plate", "dock_pick_box"))):
                ui["until"] = time.time() + CHROME_IDLE_S
        if ui["on"] and not ui["menu"] and time.time() > ui["until"]:
            ui["on"] = False
            # the chrome is gone, so nothing is under the pointer any more:
            # drop the last Tk coordinates here. The input shape empties with
            # the chrome and X sends no Leave for that, so the old xy (held
            # PTR_HOLD_S) stayed inside the vitals block and the next wake —
            # any motion, anywhere — drew its ▲ (the User 2026-09-08: "one
            # collapse arrow needs to be on hover over that top left block,
            # not on menu mouse movement"). Hover is a fresh Tk motion.
            ptr_state["xy"] = None
            ptr_state["at"] = 0.0
            log("chrome off (idle %.1fs)" % CHROME_IDLE_S)
            paint_ui()
            if hot["shown"] and hot["hide"] is not None:
                hot["hide"]()
        root.after(120, on_idle)

    secure_timer = {"id": None, "xt": 0}

    def _big_step():
        # the corner on its way: repaint the chrome every 33 ms (the HUD and
        # the film read big_anim themselves); settle at dst, close = None
        now_s = time.time()
        paint_ui()
        if big_anim["t0"] and (now_s - big_anim["t0"]) < BIG_ANIM_S:
            root.after(33, _big_step)
            return
        if big_anim["t0"]:
            big_anim["t0"] = 0.0
            if big_anim["dst"] <= 0.0:
                big["which"] = None
            root.after(350, paint_ui)     # the HUD moves the hotspot first
            paint_ui()

    def _glass_post(partial: dict) -> None:
        """A shared-key change: optimistic locally, then the house record;
        the reply (with its new rev) replaces what we hold."""
        _glass_merge(partial)
        def _go():
            try:
                req = urllib.request.Request(f"{HOUSE}/v1/glass-prefs", data=json.dumps(partial).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                with crissa_egress.urlopen(req, 4.0, "glass prefs") as r:
                    rec = json.loads(r.read().decode() or "{}")
                if isinstance(rec, dict) and rec.get("rev"):
                    _glass_swap(rec, newer_only=True)   # replies can land out of order
            except Exception as e:  # noqa: BLE001
                log(f"glass prefs post failed: {type(e).__name__}")
            finally:
                _glass_done()
        threading.Thread(target=_go, daemon=True).start()

    def _house_action(name, payload=None, top=None):
        try:
            body = {"action": name}
            if payload:
                body["payload"] = payload
            if top:
                body.update(top)          # keys the page sends beside `action` (fov_zoom: value)
            req = urllib.request.Request(f"{HOUSE}/v1/action", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with crissa_egress.urlopen(req, 95, "the Wall") as r:
                out = json.loads(r.read().decode() or "{}")
            if isinstance(out, dict) and out.get("ok") is False:
                note = str(out.get("note") or out.get("error") or out.get("msg") or "").strip()
                if note:
                    _wall_toast(note)
            return out
        except Exception as e:  # noqa: BLE001
            log(f"house action {name} failed: {type(e).__name__}")
            _wall_toast(f"Could not {name.replace('_', ' ')}")
            return None

    def _wall_toast(text: str, ttl: float = 3.0) -> None:
        msg = str(text or "").strip()[:80]
        if not msg:
            return
        with st.lock:
            st.toast_text = msg
            st.toast_until = time.time() + max(0.4, float(ttl))

    def _pad_note_from(reply):
        """Sage dropped because door-watch can't run (joystick off the door)."""
        res = (reply or {}).get("result") if isinstance(reply, dict) else None
        if not isinstance(res, dict):
            return
        if not res.get("secure_dropped"):
            return
        text = str(res.get("note") or "SECURE OFF · CAMERA IS YOURS")
        try:
            ttl = float(res.get("note_ttl_s") or 1.0)
        except (TypeError, ValueError):
            ttl = 1.0
        pad["note"] = {"text": text, "until": time.time() + ttl}
        log(f"pad note: {text} ({ttl:.0f}s)")
        tk_soon(paint_ui)                         # called from _aim's thread
        tk_soon(paint_ui, int(ttl * 1000) + 40)

    # ── hold to pan: an arrow repeats while the button is down ──────────
    PAD_GLOW_S = 0.28           # a tap glows this long after the release

    def pad_press(name):
        pad["key"] = name
        pad["last"] = name
        pad["n"] = 0
        paint_ui()                # the key goes ember-2 the moment it is down
        if pad["job"] is None:
            log(f"pan: hold {name}")
            pad_tick()

    def pad_move(ev):
        # drag off the key you are holding = stop (a slip is not a pan)
        if pad["key"] is None:
            return
        box = R.CHROME_HITS.get(pad["key"])
        if not box or not (box[0] <= ev.x <= box[2] and box[1] <= ev.y <= box[3]):
            pad_release()

    def pad_release(_ev=None):
        if pad["key"] is None:
            return
        if pad["job"] is not None:
            try:
                root.after_cancel(pad["job"])
            except Exception:
                pass
            pad["job"] = None
        log(f"pan: release after {pad['n']} ticks")
        pad["key"] = None
        pad["lit_until"] = time.time() + PAD_GLOW_S
        paint_ui()
        root.after(int(PAD_GLOW_S * 1000) + 30, paint_ui)

    PAN_MAX_TICKS = 45          # ~9 s: a held key that never sees its release
                                # must not pan the gimbal forever

    def pad_tick():
        pad["job"] = None
        if pad["key"] is None:
            return
        if pad["n"] >= PAN_MAX_TICKS:
            log(f"pan: {pad['key']} capped at {PAN_MAX_TICKS} ticks — press again")
            pad_release()
            return
        dx, dy = R.pan_vector(pad["key"])          # 5° pan, 2° tilt a tick
        if (dx or dy) and pad["busy"]:
            pad["acc"] = pad_bank(pad["acc"], dx, dy)   # banked: the next move carries it
            pad["n"] += 1
        elif dx or dy:
            _send_nudge(dx, dy)
        pad["job"] = root.after(200, pad_tick)

    def _pad_home_arm():
        if pad["home_job"] is not None:
            try:
                root.after_cancel(pad["home_job"])
            except Exception:
                pass
        pad["home_job"] = root.after(int(PAD_HOME_S * 1000), _pad_home)

    def _pad_home():
        # the User 2026-09-09: "add a return to home after 30s" — no pad activity
        # PAD_HOME_S after the last move → op:home (presence gives the token
        # back and its watchdogs resume)
        pad["home_job"] = None
        if pad["key"] is not None or pad["busy"] or (time.time() - pad["moved_at"]) < PAD_HOME_S - 0.5:
            _pad_home_arm()          # still in use: look again later
            return
        def _go():
            r = _house_action("camera_aim", {"op": "home"})
            log(f"pad idle {PAD_HOME_S:.0f}s → camera home: {json.dumps(r)[:160] if r is not None else 'no reply'}")
        threading.Thread(target=_go, daemon=True).start()

    def _pad_flush():
        if pad["busy"] or pad["acc"] == (0.0, 0.0):
            return
        _send_nudge(0.0, 0.0)        # the banked clicks alone

    def _send_nudge(dx, dy):
        ax, ay = pad["acc"]          # the banked clicks ride along with this one
        pad["acc"] = (0.0, 0.0)
        if ax or ay:
            dx, dy = pad_bank((ax, ay), dx, dy)
        if True:
            pad["busy"] = True
            pad["n"] += 1

            def _aim(dx=dx, dy=dy):
                try:
                    r = _house_action("camera_aim", {"op": "nudge", "dx": dx, "dy": dy})
                    # the whole reply: the server answers {ok, status, error}
                    # on a refusal (403 not_authed, 409 leased) and presence's
                    # {ok, pan_deg, pan_actual_deg} on a move — "None" told
                    # nobody anything (2026-09-09: "no movement at all now")
                    if pad["n"] == 1 or not (isinstance(r, dict) and r.get("ok")):
                        log(f"pad nudge {dx},{dy}: {json.dumps(r)[:200] if r is not None else 'no reply'}")
                    _pad_note_from(r)
                    if isinstance(r, dict) and r.get("ok"):
                        pad["moved_at"] = time.time()
                        tk_soon(_pad_home_arm)                # PAD_HOME_S of quiet → home
                    if pad_refusal(r) == "auth_join":
                        # refused as not the User: stop repeating the refusal
                        # and ask presence to find the User (once a press)
                        tk_soon(pad_release)
                        if time.time() - float(pad.get("joined_at") or 0.0) >= PAD_JOIN_COOL_S:
                            pad["joined_at"] = time.time()
                            with st.lock:
                                _ha = (st.house or {}).get("auth") or {}
                            mode_j = pad_join_mode(_ha)
                            j = _house_action("auth_join", {"force_facefind": True, "reason": "joystick", "mode": mode_j})
                            log(f"pad refused (not the User) → auth_join mode={mode_j}: {json.dumps(j)[:200] if j is not None else 'no reply'}")
                            jr = (j or {}).get("result") if isinstance(j, dict) else None
                            granted = isinstance(jr, dict) and (jr.get("auth") is True or jr.get("user_auth") is True)
                            if granted:
                                # granted: the press again, once — this nudge takes
                                # the camera token (user_joystick) inside presence
                                r2 = _house_action("camera_aim", {"op": "nudge", "dx": dx, "dy": dy})
                                log(f"pad after join: {json.dumps(r2)[:200] if r2 is not None else 'no reply'}")
                                _pad_note_from(r2)
                                if isinstance(r2, dict) and r2.get("ok"):
                                    pad["moved_at"] = time.time()
                                    tk_soon(_pad_home_arm)    # this move goes home too
                        else:
                            log("pad refused (not the User) — join asked %.0fs ago, waiting" % (time.time() - float(pad["joined_at"])))
                finally:
                    pad["busy"] = False
                    if pad["acc"] != (0.0, 0.0):
                        tk_soon(_pad_flush)                    # clicks banked during the flight
            threading.Thread(target=_aim, daemon=True).start()

    def _lamp_post(path, body):
        """The pack lamp, when this wall was given one. Otherwise the glass action.
        THE HOUSE (the Crissa merge, 2026-10-11): PRESENCE is the house's own presence server there, and its Secure
        keys always went through the home server's visit_click / visit_hold (a FaceFinder run takes up to 90 s; this
        call gives up at 8 s and the fallback would click twice). So the house never takes the lamp path."""
        if not PRESENCE or crissa_home.is_house():
            return None
        try:
            req = urllib.request.Request(
                PRESENCE.rstrip("/") + path, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with crissa_egress.urlopen(req, 8.0, "presence") as r:
                out = json.loads(r.read().decode() or "{}")
            if not isinstance(out, dict):
                return {"ok": False, "error": "no answer"}
            return {"ok": out.get("ok", True) is not False, "result": out}
        except Exception as e:  # noqa: BLE001
            log("presence %s failed: %s" % (path, type(e).__name__))
            return None

    # SECURE ON A CAMERA THAT CANNOT TURN (round 17, 2026-10-13; the User 2026-10-11 and 2026-10-13, .101's fixed
    # Elgato: grey Secure and Double Secure out with the reason, keep him in User mode, and say once what Secure is).
    # Presence answers in the lamp's info (modules/presence fixed_cam.secure_offer; the renderer draws a hollow grey
    # ring). A click on it sends nothing to presence: the first one ever shows what Secure is and why it needs a camera
    # that can turn to the door, in the Crissa key's pop-up (lib/crissa_wall_key.popup, above the key, with the
    # keyboard - round 16's front path); the person's having seen it is kept in <state home>/secure-note.json, and every
    # later click says the reason on the glass's toast. Never on the house (presence sends no offer there). Kill:
    # RAIL_FIXED_CAM_SECURE=0 on presence (no offer: round 16's dot and clicks).
    def _secure_greyed() -> bool:
        with st.lock:
            off = R.secure_offer(st.house, st.presence)
        if not off or off.get("ok") is not False or crissa_home.is_house():
            return False
        if R.secure_state(st.house, st.presence) == "sage":
            return False                     # Secure already on (a password proof): the click turns it off, as ever
        if off.get("hide"):
            # ONE TOGGLE (round 17 follow-ups): no Secure on this camera at all; its place opens the Security toggle
            try:
                import crissa_wall_key
                if crissa_wall_key.open_item("security"):
                    log("secure: no Secure on this camera - the Security toggle")
                    return True
            except Exception as e:  # noqa: BLE001
                log(f"security toggle: {type(e).__name__}: {e}"[:160])
        why = str(off.get("why") or "Secure needs a camera that can turn to the door.")
        note = crissa_home.HOME / "secure-note.json"
        seen = note.exists()
        log("secure: not offered on this camera (%s)%s" % (off.get("kind") or "?", "" if seen else ": the note, once"))
        if not seen:
            try:
                import crissa_wall_key
                crissa_wall_key.popup(root, "Secure", [str(off.get("about") or why)])
                note.parent.mkdir(parents=True, exist_ok=True)
                note.write_text(json.dumps({"seen_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": off.get("kind")}),
                                encoding="utf-8")
                return True
            except Exception as e:  # noqa: BLE001  the note is a nicety: the toast still says why
                log(f"secure note: {type(e).__name__}: {e}"[:160])
        _wall_toast(why, 5.0)
        tk_soon(paint_ui)
        return True

    def secure_single():
        log("secure click -> visit_click")
        if not crissa_home.is_house():
            # ONE TOGGLE (round 17 follow-ups): a click out of Secure needs his face (presence checks it); say the ask
            with st.lock:
                sec = (st.house or {}).get("security") if isinstance((st.house or {}).get("security"), dict) else {}
                lamp_now = R.secure_state(st.house, st.presence)
            if lamp_now == "sage" and sec.get("lock"):
                _wall_toast(str(sec.get("ask") or "Look at the camera for a moment."), 5.0)

        def go():
            res = _lamp_post("/v1/visit_lamp/click", {})
            if res is None:
                res = _house_action("visit_click")
            r = (res or {}).get("result") or {}
            if r.get("face") == "refused":
                _wall_toast(str(r.get("error") or "I couldn't see your face at the camera, so nothing changed."), 5.0)
            log("visit_click -> %s" % (("lamp " + str(r.get("visit_lamp") or r.get("color"))) if r else ("no answer" if res is None else str(res)[:120])))
        threading.Thread(target=go, daemon=True).start()

    def secure_double(at=None):
        def hold(pw):
            res = _lamp_post("/v1/visit_lamp", {"op": "hold", "password": pw})
            if res is None:
                res = _house_action("visit_hold", {"op": "hold", "password": pw})
            pw = None
            r = (res or {}).get("result") or {}
            log("secure HELD (keyboard)" if r.get("held") else f"hold refused: {r.get('error') or (res or {}).get('error') or '?'}")

        def ask():
            try:
                out = subprocess.run(
                    ["zenity", "--password", "--title=Hold Secure",
                     "--text=Account password — holds Secure until the next click"],
                    capture_output=True, text=True, timeout=120)
            except Exception as e:  # noqa: BLE001
                log(f"hold dialog failed: {type(e).__name__}")
                return
            pw = (out.stdout or "").rstrip("\n")
            if out.returncode != 0 or not pw:
                log("hold dialog cancelled")
                return
            hold(pw)
            pw = None
        if not crissa_home.is_house():
            # round 17: a pack asks in the Crissa pop-up (lib/crissa_wall_key.password_popup, above the key, with the
            # keyboard), never a zenity window the window manager may stack under his terminal; the house keeps zenity
            try:
                import crissa_wall_key
                words = ("%s: holds Secure until the next click." % crissa_wall_key.LOGIN_PASSWORD_WORDS
                         if crissa_wall_key.popup_at_press_on() else "Account password: holds Secure until the next click.")
                if crissa_wall_key.enabled() and crissa_wall_key.password_popup(
                        root, "Hold Secure", [words],
                        lambda secret: threading.Thread(target=hold, args=(secret,), daemon=True).start(),
                        at=at) is not None:
                    log("secure double-click -> hold pop-up")
                    return
            except Exception as e:  # noqa: BLE001  the pop-up failed: zenity, as before
                log(f"hold pop-up: {type(e).__name__}: {e}"[:160])
        log("secure double-click -> hold dialog")
        threading.Thread(target=ask, daemon=True).start()

    def rail_key(name):
        """⚒ ▶ ⚙ ⋯ — a click on the rail key or a press of its launcher in
        Ubuntu's dock (house_wall_dock.DOCK_KEYS). One code path."""
        if name in ("krew", "composer", "panel"):
            # Front the tab Crissa's Brave already has for it (Krew, Composer,
            # Control Panel); open one only if it is missing; never a second
            # window (the User 2026-09-06: "prevent multiple window bloat").
            # each surface by its own /etc/hosts name, so the tab it fronts is
            # the one that remembers that surface's zoom (2026-09-16)
            url = dock.DOCK_KEYS[name][3]
            if not url:
                log(f"rail: {name} is not in this edition")
                return
            if not crissa_home.is_house():
                # a pack: no Crissa's Brave to front a tab in, so the default browser (2026-09-28); its Composer
                # key opens the store, or the user's own CRISSA_COMPOSER_URL (2026-10-07).
                # The desk's Control Panel is the Wall's panel (the launcher sets HOUSE_WALL_PANEL).
                # Krew's tiles, or the "plugin not installed" placeholder, are that page's add-on slot,
                # so the krew key opens the same panel. The house keys above are unchanged.
                if name == "composer":
                    url = crissa_home.leave_url()      # the store's catalog, with this Wall's way back (2026-10-12)
                elif name in ("panel", "krew"):
                    desk = os.environ.get("HOUSE_WALL_PANEL") or ""
                    if desk:
                        url = desk
                threading.Thread(target=open_url, args=(url,), daemon=True).start()
                return
            def _front(url=url, name=name):
                try:
                    import zos_brave
                    if zos_brave.enabled():
                        # ZOS Brave in the background (the User 2026-10-02): the press opens it through the
                        # unit, waits for its debug port and raises it - never the User's own browser.
                        got = zos_brave.press(url)
                        log(f"{name}: press {'front' if got.get('ok') else 'not front'} {got}")
                        return
                except Exception as e:  # noqa: BLE001
                    log(f"{name}: press failed ({type(e).__name__}), old path")
                glass_mode.ensure_brave_for_tools()
                # activate the tab, THEN full-screen the window that holds it,
                # THEN raise that window: the raise used to run first (and
                # fail on a NameError), and the full screen went to the first
                # window CDP listed — a minimised twin after a keeper restart.
                out = front_crissa_tab(url, None)
                # A WINDOW, NEVER FULL SCREEN (the User 2026-09-16: "We want it kept
                # open no maximized. Never!" — reversing 09-07's full-screen
                # tabs, which trapped the desktop under Brave). A key fronts the
                # tab and leaves Brave a normal window, un-fullscreening it if a
                # leftover state had it there.
                with st.lock:
                    rendering = str(((st.house or {}).get("gpu") or {}).get("mode") or "") == "render"
                if name == "panel" and rendering:
                    # Render: the CP is a choice beside Lila, never full screen
                    # over the dock (the User 2026-09-13: "unable to navigate folders")
                    out += ", windowed (Render)"
                elif name in ("panel", "composer", "krew"):
                    out += f", {crissa_window_state('normal')}"
                xid = _crissa_window_xid()
                if xid:
                    try:
                        shape.activate(int(xid, 16))
                        out += ", raised"
                    except Exception as e:  # noqa: BLE001
                        out += f", raise failed: {type(e).__name__}"
                log(f"{name}: {out}")
            threading.Thread(target=_front, daemon=True).start()
        elif name == "menu":
            if not dock.advanced_on() and not ui["menu"]:
                log("rail: the old Advanced panel is the house's; its rows are in the MENU")
                return
            ui["menu"] = not ui["menu"]
            ui["until"] = time.time() + (86400 if ui["menu"] else 2)

    # ── INIT V2's keys (the User 2026-10-04) ──────────────────────────────────────────────────────────────────
    # The glass has no text field of its own, so typing happens in one small dialog: the name on Lila's card and
    # (in a pack) the Connect endpoint. The phone photo comes through the system's file chooser. Every answer is a
    # POST to the walker with this run's id; nothing here moves a camera or writes a file.
    def _init2_ask(title, fields, on_ok, **kw):
        try:
            import init_v2_dialog
        except ImportError:          # a pack: no Init v2 here
            return
        init_v2_dialog.ask(root, title, fields, on_ok, **kw)

    def _init2_post(action, body=None):
        threading.Thread(target=lambda: _init_post(action, body or {}, timeout=90), name="init2-post", daemon=True).start()

    def _init2_click(name, x, y):
        if name == "init2_open_browser":              # Setup is in the browser: open it there again (never a dialog)
            with st.lock:
                url = str(((st.init or {}).get("browser") or {}).get("url") or "")
            if url:                                   # a pack: her own window, raised (open_url -> open_own_window)
                threading.Thread(target=lambda: open_url(url), name="init2-open", daemon=True).start()
            return
        if name == "init2_ids_back":                  # round 38: the corner tile's Back to the Wall on an IDs run
            log("init: Back to the Wall from the corner tile (the run ends in one click)")   # round 40: Setup's too
            def _back():                              # one action (the User): the run ends, her window steps aside
                _init_post("abort", {"why": "back to the wall", "by": "the Wall's Back to the Wall key"}, timeout=30)
                _init_post("to_wall", {}, timeout=30)
            threading.Thread(target=_back, name="init2-ids-back", daemon=True).start()
            return
        if name == "init2_run_setup":                 # round 38: Setup again after a cancelled first boot
            log("init: Run Setup from the Wall (Setup was cancelled)")
            # a second press while the first one's walk starts is no restart (the User 2026-10-26: three presses, three
            # walks): the restart is asked only if no walk runs (crissa_init: if_not_running)
            _init2_post("restart", {"if_not_running": True, "by": "the Wall's RUN SETUP key"})
            return
        if name in ("init2_cancel_ask", "init2_cancel_back"):   # a pack's Cancel asks first (2026-10-11, JJ)
            _init2_post("cancel_ask", {"back": name == "init2_cancel_back"})
            return
        with st.lock:
            snap_ = dict(st.init or {})
        pl_ = snap_.get("plate") if isinstance(snap_.get("plate"), dict) else {}
        log(f"init v2: {name}")
        if name in TYPE_KEYS and types_in_browser():
            url_ = str(((snap_.get("browser") or {}).get("url")) or (INIT.rstrip("/") + "/init"))

            def _open_or_ask(name_=name):
                if open_url(url_):
                    log(f"init v2: {name_} -> typed in the browser ({url_})")
                else:
                    log(f"init v2: {name_} -> no browser opened; the Wall's own box")
                    tk_soon(lambda: _init2_click_tk(name_, pl_))      # never root.after from this worker thread
            threading.Thread(target=_open_or_ask, name="init2-type", daemon=True).start()
            return
        _init2_click_tk(name, pl_, x, y)

    def _init2_click_tk(name, pl_, x=0, y=0):
        if name == "init2_connect_test":
            _init2_post("connect_test", {})
        elif name == "init2_connect_use":
            cv_ = pl_.get("connect") or {}
            offer_ = next((o for o in (cv_.get("offers") or []) if o.get("url") and o.get("url") != cv_.get("url")), None)
            if offer_:
                _init2_post("connect_adopt", {"id": offer_.get("id") or ""})
        elif name == "init2_connect_continue":
            _init2_post("connect_continue", {})
        elif name == "init2_connect_edit":
            cv_ = pl_.get("connect") or {}
            # THE SAME PRESETS AS THE BROWSER'S CONNECT PAGE (the User 2026-10-10, .101: this dialog had none): Local,
            # Claude API and xAI fill the address and the model; the key stays the person's to type
            kinds_ = cv_.get("kinds") or {}
            presets_ = [(k_, str((kinds_[k_] or {}).get("label") or k_),
                         {"url": (kinds_[k_] or {}).get("url") or "", "model": (kinds_[k_] or {}).get("model") or ""})
                        for k_ in ("local", "claude", "xai") if k_ in kinds_]
            _init2_ask("Connect an agent", [("url", "Address", cv_.get("url") or "", False),
                                                ("key", "Key", "", True),
                                                ("model", "Model", cv_.get("model") or "", False)],
                       lambda v: _init2_post("connect_test", {k_: v_ for k_, v_ in v.items() if v_}),
                       presets=presets_, preset=str(cv_.get("preset") or ""))
        elif name == "init2_name":
            _init2_ask("Your name", [("text", "What should %s call you?" % crissa_home.her_name(), pl_.get("name") or "", False)],
                       lambda v: _init2_post("name", {"text": v.get("text") or ""}) if v.get("text") else None)
        elif name == "init2_name_go":
            _init2_post("name", {"text": pl_.get("name") or ""})
        elif name.startswith("init2_reg_"):
            # a pack's Voice ID (the User 2026-10-10): retry | skip; the walker drives the Register coach itself
            _init2_post("register", {"verb": name[len("init2_reg_"):]})
        elif name.startswith("init2_face_"):
            # a pack's face photos: start | skip | retry | continue
            _init2_post("face", {"verb": name[len("init2_face_"):]})
        elif name.startswith("init2_rmhour_"):
            # a pack's Your room (2026-10-11): an hour tapped on the clock sets the mark being set
            _init2_post("room", {"verb": "hour", "hour": int(name.rsplit("_", 1)[1])})
        elif name.startswith("init2_rm_pick_"):
            _init2_post("room", {"verb": "pick", "mark": name[len("init2_rm_pick_"):]})
        elif name.startswith("init2_rm_"):
            # next | save | skip | computer | yes | no | back
            _init2_post("room", {"verb": name[len("init2_rm_"):]})
        elif name == "init2_hr_change":
            # a pack's Your hours, typed (only when her own window did not open): whole hours, "from-to"
            hv_ = pl_.get("hours") or {}
            over_ = hv_.get("overnight") or [18, 2]
            quiet_ = hv_.get("quiet")
            _init2_ask("Your hours", [("overnight", "Overnight (from-to, hours 0-23)", "%d-%d" % tuple(over_), False),
                                      ("quiet", "Quiet hours (from-to, or none)",
                                       ("%d-%d" % tuple(quiet_)) if quiet_ else "none", False)],
                       lambda v: _init2_post("hours", {"verb": "set", "overnight": v.get("overnight") or "",
                                                       "quiet": v.get("quiet") or "none"}))
        elif name.startswith("init2_hr_"):
            _init2_post("hours", {"verb": name[len("init2_hr_"):]})
        elif name in ("init2_room_room", "init2_room_office", "init2_room_hold", "init2_room_retry"):
            _init2_post("room_choice", {"choice": name.rsplit("_", 1)[1]})
        elif name == "init2_room_upload":
            from tkinter import filedialog as _fd
            path_ = _fd.askopenfilename(parent=root, title="A photo of the room behind your screen",
                                        filetypes=[("Photos", "*.jpg *.jpeg *.png *.webp"), ("All files", "*")])
            if path_:
                _init2_post("room_choice", {"choice": "upload", "path": path_})
        elif name == "init2_seat_ok":
            _init2_post("mark", {"what": "seat"})
        elif name.startswith("init2_machine_"):
            # the machine card's keys (the User 2026-10-10): allow | skip | use | decline | retry | continue
            _init2_post("machine", {"verb": name[len("init2_machine_"):]})
        elif name == "init2_connect_later_ask":     # Set up later asks first (the User 2026-10-10, .101)
            _init2_post("connect_later_ask", {})
        elif name == "init2_connect_later_back":
            _init2_post("connect_later_ask", {"back": True})
        elif name == "init2_connect_later":         # Set up later, confirmed: Init carries on with no agent (2026-10-10)
            _init2_post("connect_later", {"by": "wall"})
        elif name == "init2_voice_retry":          # a pack's Init: install Crissa's voice again (2026-10-07)
            _init2_post("voice_install", {})
        elif name == "init2_language":
            lg_ = pl_.get("language") or {}
            _init2_ask("Your language", [("language", "Which language should " + crissa_home.her_name() + " speak? (English, Deutsch, Français, "
                                          "日本語, or a code such as de)", lg_.get("code") or "en", False)],
                       lambda v: _init2_post("language", {"language": v.get("language") or ""}) if v.get("language") else None)
        elif name.startswith("init2_hour_"):
            _init2_post("mark", {"what": "door", "hour": int(name.rsplit("_", 1)[1])})
        elif name == "init2_door_none":
            _init2_post("mark", {"what": "door", "none": True})
        elif name == "init2_photo":
            try:
                import init_v2_render as _v2r
            except ImportError:      # a pack: no Init v2 here
                return
            pt_ = _v2r.photo_point(x, y)
            stage_ = str((pl_.get("overlay") or {}).get("stage") or "")
            if pt_ and stage_ in ("crissa", "door"):
                _init2_post("mark", {"what": stage_, "x": pt_[0], "y": pt_[1]})

    def _corner_toggle(name):
        """Open a bottom window, or close it if it is the open one."""
        if name == "ft" and home_fixed_on() and click_drops_hold_on() and tiledrag.get("held") is not None:
            # round 39 (.101 round 38 N3, the User: "Don't we want home to be fixed for return easily with one click to
            # center then one back to fixed home?"): any click on her tile drops a drag's hold, so the small tile is
            # always back in the kit's corner; big is the centre
            tiledrag["held"] = None
            log("ft: a click drops the drag hold; her home is the corner")
        if name == "ft" and beside.get("key") is not None and beside_click_wins_on():
            with st.lock:
                card_c = st.layout_card
            beside["sent_home"] = ("card", card_c)       # round 36: his click wins, until the next card
            log("ft: her tile home on his click (beside the card), until the next card")
            paint_ui()
            return
        now_b = time.time()
        if big["which"] == name:
            big_anim.update(src=_big_k(now_b), dst=0.0, t0=now_b)
            log(f"{name}: small")
        else:
            big["which"] = name
            big["dx"] = big["dy"] = 0        # opens in the same place every time; the drag is per opening
            big_anim.update(src=0.0, dst=1.0, t0=now_b)
            log(f"{name}: big")
        _big_step()
        root.after(1200, paint_ui)

    DIAL_STEPS = {"density": prefs.DENSITY_STEP, "brightness": prefs.BRIGHT_STEP, "ft_size": prefs.SIZE_STEP,
                  "flourish_pct": prefs.FLOURISH_STEP, "work_start": prefs.HOUR_STEP, "work_end": prefs.HOUR_STEP}

    def step_dial(key: str, direction: int) -> dict | None:
        """One step of a corner dial or an Advanced row (brightness, resolution, FT size, flourish %, the day's hours):
        the panel's click and, since 2026-10-28, the menu key's Display rows share this one path."""
        nonlocal g
        step = DIAL_STEPS.get(key)
        if not step:
            return None
        # step from what is on screen (the shared record), not the file's copy - a pack's fix; the house
        # steps from its file as before (the Crissa merge, 2026-10-11)
        out = prefs.nudge(key, -step if direction < 0 else step, base=None if crissa_home.is_house() else _prefs())
        if key == "ft_size":
            _glass_post({"ft_size": out["ft_size"]})
        if key == "density":
            if crissa_home.is_house():
                g = R.Geom(W, H, density=_prefs()["density"])    # the house's order (before the merge)
            # shared since 2026-09-15: Brave's RES dial is this number
            _glass_post({"density": out["density"]})
            if not crissa_home.is_house():
                g = R.Geom(W, H, density=_prefs()["density"])
        if key == "brightness":
            # shared since 2026-09-16: Brave's BRT dial is this number.
            # No Geom rebuild — brightness rides into the palette on
            # the next frame (render_background takes it per paint).
            _glass_post({"brightness": out["brightness"]})
        return out

    def toggle_display(name: str) -> bool:
        """The Advanced panel's two switches the menu key may flip (2026-10-28): desktop folders, leaves at day end.
        ZOS desktop stays on the panel (its two-press arming is the safety). Returns the new state."""
        p_ = _prefs()
        if name == "leaves_at_end":
            on_ = bool(prefs.toggle_leaves()["leaves_at_end"])
            log("leaves at end of day %s (menu)" % ("on" if on_ else "off"))
            return on_
        if name == "folders":
            p_["folders"] = not p_.get("folders", False)
            prefs.save(p_)
            threading.Thread(target=_ding, args=(p_["folders"],), daemon=True).start()
            ui["until"] = time.time() + 86400
            log("folders %s (menu)" % ("shown" if p_["folders"] else "hidden"))
            return bool(p_["folders"])
        return False
    wall_keys["display"] = {"view": _prefs, "step": step_dial, "toggle": toggle_display, "paint": lambda: paint_ui()}

    def on_click(ev):
        x, y = ev.x, ev.y
        if click_is_wake(ui["armed"], ui.get("armed_xy"), x, y, time.time()):
            log(f"click ({x},{y}) swallowed: the press that woke the chrome")
            return "break"
        name = pick_hit({**R.CHROME_HITS, **R.REGISTER_HITS, **R.INIT_HITS}, x, y)
        now = time.time()
        # X server ms stamp and our clock, so a doubled press shows its gap.
        log(f"click ({x},{y}) -> {name or 'glass'} t={getattr(ev, 'time', 0)} +{int((now - click_gate['t']) * 1000)}ms")
        if (ui["menu"] or (menu_one_close_on() and hot.get("shown"))) and not name:
            if menu_one_close_on():
                close_menu("click off the panel")
            else:
                ui["menu"] = False
                ui["until"] = now + 2
                log("advanced closed (click off the panel)")
                paint_ui()
            return "break"
        lb_ = _letter_input_box()
        if not name and lb_ and lb_[0] <= x <= lb_[2] and lb_[1] <= y <= lb_[3]:
            return "break"      # the letter takes the pointer for its wheel; a press on it moves nothing
        if not name and doc_placement.drag_enabled():
            with st.lock:
                house_d = st.house
            if doc_placement.doc_up(house_d):
                o0 = doc_placement.origin(house_d) or doc_placement.doc_origin()
                docdrag.update(on=True, x0=x, y0=y, o0=o0, live=None, moved=False, home=False)
                return "break"
            if doc_placement.free_drag_on():
                # no page up, a pack (the User 2026-10-16): the same drag, its own kept spot; from the middle
                o0 = doc_placement.origin(house_d) or (R.ZFRAME["cx"], R.ZFRAME["cy"])
                docdrag.update(on=True, x0=x, y0=y, o0=o0, live=None, moved=False, home=True)
                return "break"
        if not name or name == "menu_panel":
            return "break"
        if name in HIT_SURFACES:
            return "break"      # a surface for the pointer, not a button
        if name.startswith("dockwin_"):
            # the chooser: raise this window (house-shell for Wayland ids,
            # _NET_ACTIVE_WINDOW for X ids), then let the chrome go
            try:
                k_ = int(name.rsplit("_", 1)[1])
                w_ = dock_pick["wins"][k_]
            except (ValueError, IndexError):
                return "break"
            if w_.get("via") == "new":
                it_ = dock.items()[dock_pick["i"]] if dock_pick.get("i") is not None and dock_pick["i"] < len(dock.items()) else None
                log(f"dock: new window -> {dock.launch(it_['id'], 1) if it_ else 'no tile'}")
                ok_ = bool(it_)
            elif w_.get("via") == "shell":
                ok_ = dock.activate(w_)
            else:
                try:
                    shape.activate(int(str(w_["id"]), 16))
                    ok_ = True
                except Exception as e:  # noqa: BLE001
                    log(f"dock: raise failed: {e}")
                    ok_ = False
            log(f"dock: raise {w_.get('title')!r} -> {'ok' if ok_ else 'no'}")
            dock_pick.update(i=None, wins=[], hold=None)
            ui["until"] = time.time() + 0.6
            paint_ui()
            return "break"
        if name.startswith("dock_") and name[5:].isdigit():
            # the wall's own dock: gtk-launch the favourite, then fold the chrome
            # (dock_fold / dock_plate are not tiles — they fall through)
            try:
                it = dock.items()[int(name[5:])]
            except (ValueError, IndexError):
                return "break"
            # A CLICK ON A RUNNING APP RAISES IT (the User 2026-09-08: "It wouldn't
            # let me click open files, kept opening new one"): an X window we
            # can see is raised; a Wayland app we cannot see gets a plain
            # activation (its own window fronts). A NEW window is the
            # chooser's last row, never the tile itself.
            n_run = int((dock.poll_running() or {}).get(it["id"], 0))
            if n_run:
                wins_ = dock.windows(it)
                xw = next((w for w in wins_ if w.get("via") == "x"), None)
                if wins_ and wins_[0].get("via") == "shell":
                    log(f"dock: {it.get('name')} ({n_run} up) -> raise {'ok' if dock.activate(wins_[0]) else 'no'}")
                elif xw:
                    try:
                        shape.activate(int(str(xw["id"]), 16))
                        log(f"dock: {it.get('name')} ({n_run} up) -> raised {xw.get('title')!r}")
                    except Exception as e:  # noqa: BLE001
                        log(f"dock: raise failed: {e}")
                else:
                    log(f"dock: {it.get('name')} ({n_run} up, no window in sight) -> {dock.launch(it['id'], 0)}")
            else:
                log(f"dock: {it.get('name')} -> {dock.launch(it['id'], 0)}")
            ui["until"] = time.time() + 0.6
            return "break"
        if name in ("cam_l", "cam_u", "cam_d", "cam_r"):
            # HOLD TO PAN: the arrow repeats while the button is down
            pad_press(name)
            return "break"
        if name == "secure" and _secure_greyed():
            return "break"                       # round 17: no Secure on this camera; the click said why
        if name == "secure":
            # Single = Secure on/off, NOW. Double = keyboard hold. Decided by
            # the X server stamp of the previous press, not a Tk timer: the
            # 350 ms after() never fired on 2026-09-06 12:59:24 (Tk loop busy),
            # so the first press did nothing and the second read as a double.
            # A single that precedes a double only toggles Secure first; the
            # hold mints Secure on regardless, so nothing is lost.
            try:
                xt = int(getattr(ev, "time", 0) or 0)
                gap = xt - int(secure_timer.get("xt") or 0)
                secure_timer["xt"] = xt
                if 0 < gap < 450:
                    log(f"secure: double ({gap} ms)")
                    secure_double(at=(int(ev.x), int(ev.y)))   # the pop-up opens where he pressed (2026-10-26)
                else:
                    log("secure: single")
                    secure_single()
            except Exception as e:  # noqa: BLE001
                log(f"secure click failed: {type(e).__name__}: {e}"[:160])
            return "break"
        # DEBOUNCE. 12:51:27: one press on FOLDERS arrived as three events
        # inside a second, shown -> hidden -> (swallowed), so the key looked
        # dead. A toggle key takes one press per 450 ms; nothing on this
        # glass needs to be pressed faster than that except the Secure
        # double-click, which has its own timer.
        # FOLDERS: 14:06:55 the SHOW press came back 709 ms later at the SAME
        # pixel and hid what it had just shown (DING mapping under the
        # pointer re-delivers). One press per 1.5 s there; a person does not
        # re-press a toggle that fast without moving.
        gate_s = 1.5 if name == "folders" else 0.45
        if name == click_gate["name"] and now - click_gate["t"] < gate_s and name != "secure":
            log(f"  swallowed repeat {name}")
            return "break"
        click_gate["t"] = now
        click_gate["name"] = name
        p = _prefs()          # the shared truth seeds the toggle
        if name == "zos":
            # ZOS DESKTOP off = wallpaper off: enable+start crissa-display
            # (autostart lives again) and stop this wallpaper. A Brave already
            # open is left alone in wallpaper-on; this path is the one that
            # turns the service back on.
            # TWO PRESSES (the User 2026-09-08: "I turned off Wallpaper and
            # everything disappeared … I am trapped with no menus"): Brave
            # comes up as a fullscreen kiosk over the desktop with no menus.
            # The first press arms and the note says what the second does;
            # 4 s later it disarms by itself.
            if now - float(ui.get("zos_armed_at") or 0.0) > 4.0:
                ui["zos_armed_at"] = now
                ui["until"] = now + 86400 if ui["menu"] else now + CHROME_IDLE_S
                log("zos: armed — press again within 4 s to hand the glass to Brave")
                paint_ui()
                root.after(4300, paint_ui)
                return "break"
            ui["zos_armed_at"] = 0.0
            def _raise_crissa() -> bool:
                xid = _crissa_window_xid()
                if not xid:
                    return False
                shape.activate(int(xid, 16))
                return True
            def _to_brave():
                # Brave first, small (normal float) but faced (wall tab
                # active, raised), GNOME dock back; the wallpaper stops last
                # (the User 2026-09-08: "a normal desktop and the Hybrid wall …
                # small but faced ZOS Brave").
                try:
                    out = glass_mode.hand_to_brave(self_unit=glass_mode.WALL, raise_window=_raise_crissa)
                    f = out.get("face") or {}
                    log("Wall desktop off: Brave is the glass (hybrid, window "
                        f"{f.get('window', '?')}, {f.get('tabs', '?')} tabs, wall active="
                        f"{f.get('wall_active')}, raised={out.get('raised')}, dock={out.get('dock')}); "
                        "wallpaper stopping")
                except Exception as e:  # noqa: BLE001
                    log(f"zos desktop toggle failed: {type(e).__name__}")
            threading.Thread(target=_to_brave, daemon=True).start()
        elif name in ("krew", "composer", "panel", "menu"):
            rail_key(name)
        elif name in ("log_board", "log_room", "log_tools"):
            # the LOGS chips: BOARD / ROOM↔USERONLY / TOOLS (the User 2026-09-07)
            p[name] = not p.get(name, True)
            prefs.save(p)
            _glass_post({name: p[name]})
            log(f"{name}: {'on' if p[name] else 'off'}")
        elif name in ("camera", "ft"):
            # click enlarges the bottom window; the next click closes it —
            # on the page's .32 curve both ways, not a pop (the User 2026-09-08:
            # "duplicate that open and close action for the PIP and Camera")
            if name == "ft" and big["which"] == "ft" and not big_anim["t0"]:
                # the opened pop: a press arms a drag; the release decides
                # (no move = the closing click, see on_b1_release)
                drag.update(on=True, x0=x, y0=y, dx0=big["dx"], dy0=big["dy"], moved=False)
                return "break"
            if name == "ft" and big["which"] != "ft" and os.environ.get("HOUSE_FT_TILE_DRAG", "1") != "0":
                # the small tile: a press arms a drag anywhere on the glass (the User 2026-10-09); the release
                # decides (no move = the opening click, see on_b1_release)
                hs_ = R.HOTSPOTS.get("ft") or (x, y, x, y)
                tiledrag.update(on=True, x0=x, y0=y, bx=hs_[0], by=hs_[1], live=None, moved=False)
                return "break"
            _corner_toggle(name)
        elif name == "dock_apps":
            # GNOME's applications grid, the way the dash's nine dots open it.
            # house-shell's ShowApps first (inside the shell, always allowed);
            # gnome-shell refuses an outside org.gnome.Shell.ShowApplications
            # with "not allowed" since the 2026-09-08 reboot (the User 2026-09-09:
            # "my Apps box isn't sending me to App screen anymore") — kept as
            # the fallback until the extension's new method loads at the next
            # login, and its error is LOGGED, not swallowed.
            def _apps():
                for cmd in dock_apps_commands():
                    try:
                        out = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
                    except Exception as e:  # noqa: BLE001
                        log(f"dock: apps grid {cmd[0]} failed: {type(e).__name__}")
                        continue
                    if out.returncode == 0 and "false" not in out.stdout:
                        log(f"dock: apps grid via {cmd[0]}")
                        return
                    log(f"dock: apps grid {cmd[0]} refused: {(out.stderr or out.stdout).strip()[:120]}")
                log("dock: apps grid — no way in (house-shell ShowApps loads at the next login)")
            threading.Thread(target=_apps, daemon=True).start()
        elif name.startswith("dock_place_") and name[11:].isdigit():
            # a folder tile: the default folder handler opens it (Dolphin
            # since 2026-09-09), in its own scope
            try:
                pl = dock.places()[int(name[11:])]
            except (ValueError, IndexError):
                return "break"
            # open, then RAISE the folder's window: the handler is single-
            # instance and a second open lands behind (2026-09-09 pm)
            def _place(pl=pl):
                path_ = str(pl.get("path") or "")
                how = dock.open_place(path_)
                log(f"dock: {pl.get('name')} -> {how} ({dock.folder_handler() or 'no handler'})")
                if how == "opened":
                    time.sleep(0.5)
                    log(f"dock: {pl.get('name')} -> {dock.raise_place(path_)}")
            threading.Thread(target=_place, daemon=True).start()
            ui["until"] = time.time() + 0.6
        elif name == "dock_clear":
            # CLEAR DESKTOP (the User 2026-09-09): Mutter's own show-desktop
            # through the EWMH root message, a toggle — the second press
            # brings the windows back. The hint is re-read for the glyph.
            want = not dock.poll_showing(force=True)
            how = dock.show_desktop(want)
            def _reread(want=want, how=how):
                time.sleep(0.4)
                now_ = dock.poll_showing(force=True)
                log(f"dock: {'clear desktop' if want else 'windows back'} via {how} -> hint {'1' if now_ else '0'}"
                    + ("" if now_ == want else " (Mutter did not follow)"))
                tk_soon(paint_ui)
            threading.Thread(target=_reread, daemon=True).start()
            ui["until"] = time.time() + 0.6
        elif name.startswith("init2_"):
            _init2_click(name, x, y)
            ui["until"] = time.time() + 0.6
        elif name.startswith("init_"):
            # the wizard's keys: the walker's actions (a click is proof the glass is there)
            def _init_click(name=name):
                if name == "init_cancel":
                    r = _init_post("abort", {"by": "the Wall's YES, CANCEL SETUP key"}, timeout=15)
                elif name == "init_go":
                    with st.lock:
                        snap_c = dict(st.init or {})
                        tg = dict(st.init_tog.get(str(snap_c.get("step_id") or "")) or {})
                    sid_c = str(snap_c.get("step_id") or "")
                    if sid_c == "brief_interview":
                        cards = [c for c, v in tg.items() if v and not c.startswith("_")] if tg else list(R.INIT_DEFAULT_CARDS)
                        r = _init_post("answer", {"cards": cards}, timeout=15)
                    elif sid_c == "news_topics":
                        ids = [c for c, v in tg.items() if v and not c.startswith("_")]
                        r = _init_post("answer", {"topics": ids}, timeout=15) if ids else _init_post("skip", {}, timeout=15)
                    elif sid_c == "seat_map":
                        tg = R.init_toggles(dict(snap_c, _toggles={"seat_map": tg}))   # the User's desk 6 by default
                        sitters = {pid: int(h) for pid, h in tg.items() if not pid.startswith("_") and h}
                        r = _init_post("answer", {"sitters": sitters}, timeout=15) if sitters else _init_post("next", {}, timeout=15)
                    else:
                        r = _init_post("next", {}, timeout=15)
                elif name == "init_skip":
                    r = _init_post("skip", {}, timeout=15)
                elif name == "init_restart":
                    r = _init_post("restart", {}, timeout=20)
                elif name == "init_resume":
                    r = _init_post("resume", {}, timeout=15)
                elif name == "init_later":
                    r = _init_post("abort", {"by": "the Wall's Later key"}, timeout=15)
                elif name == "init_again":
                    r = _init_post("again", {"loop": True}, timeout=15)
                elif name == "init_yes":
                    r = _init_post("answer", {"pill": "yes"}, timeout=15)
                elif name == "init_no":
                    r = _init_post("answer", {"pill": "no"}, timeout=15)
                elif name == "init_internal":
                    r = _init_post("answer", {"pill": "internal"}, timeout=15)
                elif name.startswith("init_pill_"):
                    # a pack's machine card on the v1 glass (the User 2026-10-10): its keys are its only answers
                    r = _init_post("answer", {"pill": name[len("init_pill_"):]}, timeout=15)
                elif name == "init_speakers":
                    r = _init_post("answer", {"pill": "speakers"}, timeout=15)
                elif name.startswith("init_hour_"):
                    h = int(name[10:])
                    with st.lock:
                        snap_c = dict(st.init or {})
                    if str(snap_c.get("step_id") or "") == "seat_map":
                        # a seat pick on the dial: assign the selected person,
                        # then move the selection to the next without a seat
                        with st.lock:
                            tg = st.init_tog.setdefault("seat_map", {})
                            tg.update(R.init_toggles(dict(snap_c, _toggles={"seat_map": tg})))   # keeps the desk default explicit
                            who = R.init_seat_who(dict(snap_c, _toggles={"seat_map": tg}))
                            if who:
                                tg[who] = h
                                tg.pop("_who", None)
                        r = None
                    else:
                        r = _init_post("answer", {"hour": h, "text": str(h)}, timeout=15)
                elif name == "init_register_who":
                    with st.lock:
                        who = str((st.init or {}).get("enroll_who") or "")
                    r = _house_action("enroll_start", {"mode": "voice-register", "display": who, "person_id": who.strip().lower().replace(" ", "_"),
                                                       "station": "desk", "create": True}) if who else None
                elif name.startswith("init_tog_"):
                    # a card / topic toggle: the glass keeps it until Continue
                    tid = name[9:]
                    with st.lock:
                        sidn = str((st.init or {}).get("step_id") or "")
                        tg = st.init_tog.setdefault(sidn, {})
                        if sidn == "brief_interview" and not tg:
                            tg.update({c: True for c in R.INIT_DEFAULT_CARDS})
                        tg[tid] = not bool(tg.get(tid))
                    r = None
                elif name.startswith("init_who_"):
                    with st.lock:
                        sidn = str((st.init or {}).get("step_id") or "")
                        st.init_tog.setdefault(sidn, {})["_who"] = name[9:]
                    r = None
                else:
                    r = None
                if isinstance(r, dict):
                    with st.lock:
                        if "step_id" in r:
                            st.init = r
                log(f"init: {name} -> {str(r)[:80] if r is not None else 'no answer'}")
            threading.Thread(target=_init_click, daemon=True).start()
        elif name == "register_cancel":
            # the register layer's CANCEL: the coach cancels, the glass idles
            def _cancel():
                r = _house_action("enroll_cancel", {})
                log(f"register: cancel -> {'ok' if isinstance(r, dict) and r.get('ok') else r}")
                # CANCEL MEANS CANCEL (the User 2026-09-13): inside the Init routine the
                # register's key ends the routine, not just its chapter — the worker
                # used to read the idle register as "chapter done" and move on
                with st.lock:
                    snap_i = dict(st.init or {})
                if str(snap_i.get("status") or "") == "running" and str(snap_i.get("step_id") or "") in ("user_identity", "present_regulars_enroll"):
                    r2 = _init_post("abort", {"by": "the Wall's register Cancel"}, timeout=15)
                    log(f"init: register CANCEL -> abort the routine ({(r2 or {}).get('status') if isinstance(r2, dict) else r2})")
            threading.Thread(target=_cancel, daemon=True).start()
        elif name == "dock_fold":
            p["dock_bar"] = not p.get("dock_bar", False)
            prefs.save(p)
            ui["until"] = time.time() + CHROME_IDLE_S
            log(f"dock: {'folded' if p['dock_bar'] else 'open'}")
        elif name == "leaves_at_end":
            # her working day (the User 2026-10-04): a setting until the nightly leave/return lands
            log("leaves at end of day %s" % ("on" if prefs.toggle_leaves()["leaves_at_end"] else "off"))
        elif name == "folders":
            # ⋯ Advanced only (the key lives on the panel). Never a desk /
            # folder-icon click. FOLDERS are GNOME's own desktop icons
            # (DING), not the parked chips: the User 2026-09-06 "No folders ever
            # appeared" when the toggle rounded the chips up instead. The key
            # came back 2026-09-08 pm ("show or hide desktop folders").
            if not ui["menu"]:
                return "break"
            p["folders"] = not p.get("folders", False)
            prefs.save(p)
            threading.Thread(target=_ding, args=(p["folders"],), daemon=True).start()
            ui["until"] = time.time() + 86400
            log("folders %s" % ("shown" if p["folders"] else "hidden"))
        elif name in ("ar_zoom_lo", "ar_zoom_hi") and wall_frame.mode() == "whole":
            # HER FILM'S ZOOM (the User 2026-10-12): under the whole frame the same two keys zoom about her face,
            # 1.00 (the whole film) to 3.00, AR on or off (round 14, .101: with AR on they moved the hole only, so the
            # wallpaper had no whole-frame zoom); one shared number, both glasses, no restart
            z = wall_frame.clamp_zoom(float(p.get("frame_zoom", 1.0)) + (wall_frame.ZOOM_STEP if name == "ar_zoom_hi"
                                                                        else -wall_frame.ZOOM_STEP))
            p["frame_zoom"] = z
            prefs.save(p)
            _glass_post({"frame_zoom": z})
            log(f"frame zoom {z:.2f}")
        elif name in ("ar_zoom_lo", "ar_zoom_hi"):
            # The page's #arZoom, mirrored: quarter steps, .25 to 4, the same
            # clamp the server applies; the 2 s house poll repaints the number,
            # so set the mirror now for the eye.
            z = _kiosk_zoom() + (0.25 if name == "ar_zoom_hi" else -0.25)
            z = max(0.25, min(4.0, round(z * 100) / 100))
            def _zoom(z=z):
                r = _house_action("fov_zoom", top={"value": z})
                if isinstance(r, dict) and r.get("ok"):
                    with st.lock:
                        if isinstance(st.house, dict):
                            st.house.setdefault("screen", {})["fov_zoom_crissa"] = float(r.get("fov_zoom", z))
                    log(f"ar zoom: hole width {float(r.get('fov_zoom', z)):.2f}")
                    tk_soon(paint_ui)
            threading.Thread(target=_zoom, daemon=True).start()
        elif name == "ar":
            # The kiosk's Hybrid hole, toggled where the kiosk keeps it. The
            # 2 s house poll repaints the chip; flip the mirror now for the eye.
            on = not _kiosk_hole()
            def _flip(on=on):
                r = _house_action("crissa_hole_on" if on else "crissa_hole_off")
                if isinstance(r, dict) and r.get("ok"):
                    with st.lock:
                        if isinstance(st.house, dict):
                            st.house.setdefault("screen", {})["crissa_hole"] = bool(r.get("crissa_hole", on))
                    log(f"ar: kiosk hole {'on' if on else 'off'}")
            threading.Thread(target=_flip, daemon=True).start()
        elif name == "mode":
            # Brave's modechip, as is: LISTENING <-> PAUSED. Not the mic.
            # Clicking PAUSED is a bare wake (session_open, no extra words,
            # no spoken Hey Crissa). Voice off = Away/Alert = not armed.
            with st.lock:
                house_m = st.house if isinstance(st.house, dict) else {}
                mk = R.mood_of(house_m)
                sess = bool(((house_m.get("hear") or {}).get("session_open")))
            if mk not in ("attending", "speaking", "paused"):
                log(f"mode: not armed ({mk})")
                return "break"
            def _mode(sess=sess):
                # literal names: the glass contract test reads them here
                r = _house_action("voice_pause" if sess else "voice_listen")
                act_m = "voice_pause" if sess else "voice_listen"
                if isinstance(r, dict) and r.get("ok"):
                    with st.lock:
                        if isinstance(st.house, dict):
                            st.house.setdefault("hear", {})["session_open"] = not sess
                            st.house["hear"]["paused"] = sess
                    log(f"mode: {'Paused' if sess else 'Listening'}")
                    tk_soon(paint_ui)
                else:
                    log(f"mode: {act_m} refused {r!r}"[:160])
            threading.Thread(target=_mode, daemon=True).start()
        elif name == "comms_hold":
            # Stops only the ear's idle expiry. Not the LISTENING colour,
            # not Secure, not the mic. Arming with no session is lost.
            with st.lock:
                hear = ((st.house or {}).get("hear") or {})
                cur = bool(hear.get("comms_hold"))
                sess = bool(hear.get("session_open"))
            if not cur and not sess:
                log("comms_hold: ignored (no session)")
                return "break"
            on = not cur
            def _hold(on=on):
                r = _house_action("voice_hold_on" if on else "voice_hold_off")
                if isinstance(r, dict) and r.get("ok"):
                    with st.lock:
                        if isinstance(st.house, dict):
                            st.house.setdefault("hear", {})["comms_hold"] = on
                    log(f"comms_hold: {'on' if on else 'off'}")
                    tk_soon(paint_ui)
            threading.Thread(target=_hold, daemon=True).start()
        elif name == "wall_off":
            # round 26 (the User 2026-10-16: "wall on off on render window"): her tile's WALL key closes the desk Wall;
            # her face window comes up on top with the same key to bring it back (lib/crissa_face). One press ARMS,
            # the second within the window closes (lib/crissa_wall_key.wall_off_press, 2026-10-24; kill
            # CRISSA_WALL_KEY_CONFIRM=0)
            import crissa_wall_key
            close_now, words = crissa_wall_key.wall_off_press(ui)
            if not close_now:
                log("wall key: armed (%s)" % words)
                _wall_toast(words)
                return "break"

            def _wall_off():
                try:
                    import crissa_wall_key
                    r = crissa_wall_key._post(FEED, {"action": "wall_close", "by": "her tile"})
                    log("wall key: %s" % (r.get("note") or r.get("error") or ("closed" if r.get("ok") else r))[:160])
                except Exception as e:  # noqa: BLE001
                    log(f"wall key: {type(e).__name__}: {e}"[:160])
            threading.Thread(target=_wall_off, daemon=True, name="wall-key").start()
        elif name == "blur":
            p["blur"], p["ft_fade"] = ft_veil_next(bool(p.get("blur")), bool(p.get("ft_fade")))
            prefs.save(p)
            _glass_post({"blur": p["blur"], "ft_fade": p["ft_fade"]})
        elif name == "ft_mode":
            p["ft_mode"] = "room" if p.get("ft_mode") != "room" else "ft"
            prefs.save(p)
            _glass_post({"ft_mode": p["ft_mode"]})
        elif name == "oracle_fold":
            toggle_oracle()
        elif name == "pip_fold":
            p["pip_bar"] = not p.get("pip_bar", False)
            prefs.save(p)
            _glass_post({"pip_bar": p["pip_bar"]})
        elif name == "vitals_fold":
            p["vitals_folded"] = not p.get("vitals_folded", False)
            prefs.save(p)
            _glass_post({"vitals_folded": p["vitals_folded"]})
        elif name == "ft_fold":
            p["ft_bar"] = not p.get("ft_bar", False)
            prefs.save(p)
            _glass_post({"ft_bar": p["ft_bar"]})
            cv.itemconfig(ft_item, state="hidden" if p["ft_bar"] else "normal")
        elif name.startswith("cam_"):
            # camera joystick → presence via the wall server (auth checked there)
            step = 3.0   # degrees a press
            dx, dy = {"cam_l": (-step, 0), "cam_r": (step, 0), "cam_u": (0, step), "cam_d": (0, -step)}.get(name, (0, 0))
            def _aim(name=name, dx=dx, dy=dy):
                payload = {"op": "home"} if name == "cam_home" else {"op": "nudge", "dx": dx, "dy": dy}
                r = _house_action("camera_aim", payload)
                body = (r or {}).get("body") if isinstance(r, dict) else None
                log(f"camera {name}: {str(body)[:160]}")
            threading.Thread(target=_aim, daemon=True).start()
        elif name.endswith("_lo") or name.endswith("_hi"):
            key = name[:-3]
            if key.startswith("adv_"):
                key = key[4:]      # the Advanced panel's copy of a corner dial
            step_dial(key, -1 if name.endswith("_lo") else 1)
        paint_ui()
        return "break"

    def _tk_err(exc, val, tb):
        log(f"tk callback error: {exc.__name__}: {val}"[:200])
    root.report_callback_exception = _tk_err
    cv.bind("<Motion>", on_motion)
    cv.bind("<Enter>", on_motion)        # EnterNotify carries coordinates too
    cv.bind("<Leave>", on_leave)
    cv.bind("<Button-1>", on_click)
    root.bind_all("<Escape>", lambda _e: (close_menu("Esc") if menu_one_close_on() else None), add="+")   # round 40

    # THE DOCK'S LAUNCHERS (house_wall_dock.DOCK_KEYS; the User 2026-09-16 "one
    # piece"): scripts/house-dock-key writes the key's name, this runs what a
    # click on that key ran. One stat every 150 ms on the Tk thread.
    press_seen = {"m": dock.read_press(0.0)[0]}
    # the poll's pace (2026-10-28, the User: the dock keys are "less snappy" than the menu key): one stat a tick on
    # the Tk thread; 60 ms is under a frame of the glass. HOUSE_WALL_DOCK_POLL_MS sets it (150 was the 09-16 pace).
    DOCK_POLL_MS = max(20, int(os.environ.get("HOUSE_WALL_DOCK_POLL_MS", "60") or 60))

    def dock_press(name_: str, source: str = "dock key"):
        """What a press of a dock key does - and, since 2026-10-28, what the menu key's Display settings does for
        "menu": ONE code path for the Advanced panel whoever asks for it."""
        log(f"{source}: {name_}")
        closing = name_ == "menu" and menu_one_close_on() and (ui["menu"] or hot.get("shown"))
        if closing:
            # round 40: a press while the menu (or its band) is up closes it - never re-opens over it
            close_menu(source)
            name_ = ""
        if name_ == "menu":
            ui["on"] = True
        if name_:
            rail_key(name_)
        if name_ == "menu":
            menu_dwell["left_at"] = None
            menu_dwell["grace_until"] = (time.time() + MENU_DOCK_GRACE_S) if ui["menu"] else 0.0
        paint_ui()
        if name_ == "menu":
            # over any window: the band pop carries the panel
            if ui["menu"] and callable(hot.get("show")):
                hot["show"]()
            elif not ui["menu"] and hot.get("shown") and callable(hot.get("hide")):
                hot["hide"]()
    wall_keys["dock_press"] = dock_press

    def _press_poll():
        try:
            m_, name_ = dock.read_press(press_seen["m"])
            press_seen["m"] = m_
            if name_:
                dock_press(name_)
        except Exception as e:  # noqa: BLE001
            log(f"dock key: {type(e).__name__}: {e}")
        root.after(DOCK_POLL_MS, _press_poll)
    root.after(DOCK_POLL_MS, _press_poll)

    def on_wheel(ev):
        """The wheel over the glass steps chars/cm (the User 2026-09-16: "We want
        that scroll to be chars/cm").

        The ADVANCED panel has promised this since 09-06 and nothing was bound
        — flagged unfixed in WRAP-20260908-WALL-CONTROLS, and the honest fix is
        the binding, not deleting the promise. Brave's glass does the same
        (voiceonly-home glassWheel), so one gesture means one thing on both.
        X11 sends Button-4/5, Windows and some drivers send MouseWheel with a
        delta; take either. The step is the dial's own, proportional, so a
        wheel and a click on `+` cannot disagree.
        """
        up = getattr(ev, "num", None) == 4 or getattr(ev, "delta", 0) > 0
        # over the letter the wheel scrolls it (the User 2026-10-09); everywhere else it is the density dial
        lr = R.LETTER.get("rect")
        if lr and letter["key"] is not None and doc_placement.scroll_enabled() \
                and lr[0] <= ev.x <= lr[2] and lr[1] <= ev.y <= lr[3]:
            letter["line"] = doc_placement.wheel_scroll(letter["line"], up, int(R.LETTER.get("total") or 0),
                                                        int(R.LETTER.get("per_page") or 1))
            return "break"
        # THE BASE STEP, not nudge_step(): prefs.nudge already multiplies by
        # max(1, current), so pre-scaling it steps twice as far as the dial —
        # 1.2x out at density 1.2 and 3x at 3.0. Caught by
        # test_wall_prefs_roundtrip_and_the_proportional_step.
        step = prefs.DENSITY_STEP
        out = prefs.nudge("density", step if up else -step, base=None if crissa_home.is_house() else _prefs())
        nonlocal g
        if crissa_home.is_house():                  # the house's order (before the Crissa merge, 2026-10-11)
            g = R.Geom(W, H, density=_prefs()["density"])
            _glass_post({"density": out["density"]})
        else:
            _glass_post({"density": out["density"]})
            g = R.Geom(W, H, density=_prefs()["density"])
        paint_ui()
        return "break"

    cv.bind("<Button-4>", on_wheel)      # X11 wheel up
    cv.bind("<Button-5>", on_wheel)      # X11 wheel down
    cv.bind("<MouseWheel>", on_wheel)    # delta-carrying wheel
    DRAG_SLOP = drag_slop_px(W, _screen_mm(root))   # px before a press is a drag; round 37: millimetres of glass in a pack

    def on_b1_motion(ev):
        if tiledrag["on"]:
            dx, dy = ev.x - tiledrag["x0"], ev.y - tiledrag["y0"]
            if not tiledrag["moved"] and abs(dx) < DRAG_SLOP and abs(dy) < DRAG_SLOP:
                return "break"
            tiledrag["moved"] = True
            tiledrag["live"] = R.ft_tile_from_xy(R.Geom(W, H, density=_prefs()["density"]),
                                                 tiledrag["bx"] + dx, tiledrag["by"] + dy, _prefs())
            return "break"
        if docdrag["on"]:
            dx, dy = ev.x - docdrag["x0"], ev.y - docdrag["y0"]
            if not docdrag["moved"] and abs(dx) < DRAG_SLOP and abs(dy) < DRAG_SLOP:
                return "break"
            docdrag["moved"] = True
            with st.lock:
                house_m = st.house
            docdrag["live"] = doc_placement.dragged(docdrag["o0"], dx, dy, W, H, R.hole_zoom_of(house_m))
            return "break"
        if not drag["on"]:
            return pad_move(ev)
        dx, dy = ev.x - drag["x0"], ev.y - drag["y0"]
        if not drag["moved"] and abs(dx) < DRAG_SLOP and abs(dy) < DRAG_SLOP:
            return
        drag["moved"] = True
        big["dx"], big["dy"] = drag["dx0"] + dx, drag["dy0"] + dy   # the film thread places the tile each frame (ft_box clamps to the glass)

    def on_b1_release(ev):
        if tiledrag["on"]:
            tiledrag["on"] = False
            if tiledrag["moved"] and tiledrag["live"] is not None and home_fixed_on():
                with st.lock:
                    card_r = st.layout_card
                tiledrag["held"] = (list(tiledrag["live"]), card_r)
                log("ft: tile dragged to (%.3f,%.3f) until the next card or page-down; her home stays the corner"
                    % tuple(tiledrag["live"][:2]))
                tiledrag.update(live=None, moved=False)
                paint_ui()
                root.after(1200, paint_ui)
            elif tiledrag["moved"] and tiledrag["live"] is not None:
                p_ = prefs.load()
                p_["ft_tile"] = list(tiledrag["live"])
                prefs.save(p_)
                log(f"ft: tile dragged to ({p_['ft_tile'][0]:.3f},{p_['ft_tile'][1]:.3f}), kept")
                tiledrag.update(live=None, moved=False)
                paint_ui()
                root.after(1200, paint_ui)       # the HUD moves the hotspot first
            else:
                tiledrag.update(live=None, moved=False)
                _corner_toggle("ft")
            return "break"
        if docdrag["on"]:
            if docdrag["moved"] and docdrag["live"] is not None:
                try:
                    if docdrag.get("home"):
                        cx_, cy_ = doc_placement.save(*docdrag["live"], path=doc_placement.home_place_path())
                        log(f"home place: her picture dragged to origin ({cx_:.4f},{cy_:.4f}) with no page up, kept")
                    else:
                        cx_, cy_ = doc_placement.save(*docdrag["live"])
                        log(f"doc place: her picture dragged to origin ({cx_:.4f},{cy_:.4f}), kept for the next document")
                except OSError as e:
                    log(f"doc place: not saved ({e})")
            docdrag.update(on=False, live=None, moved=False)
            return "break"
        if not drag["on"]:
            return pad_release(ev)
        drag["on"] = False
        if drag["moved"]:
            log(f"ft: dragged to ({big['dx']:+d},{big['dy']:+d})")
            paint_ui()
            root.after(1200, paint_ui)       # the HUD moves the hotspot first
            return "break"
        # a plain click on the opened pop closes it, as before
        now_b = time.time()
        big_anim.update(src=_big_k(now_b), dst=0.0, t0=now_b)
        log("ft: small")
        _big_step()
        root.after(1200, paint_ui)
        return "break"

    cv.bind("<B1-Motion>", on_b1_motion)
    cv.bind("<ButtonRelease-1>", on_b1_release)

    def on_double(ev):
        """A double-click on bare glass with no page up puts her back in the middle (a pack, 2026-10-16)."""
        if pick_hit({**R.CHROME_HITS, **R.REGISTER_HITS, **R.INIT_HITS}, ev.x, ev.y):
            # Tk sends a double's SECOND press to <Double-Button-1> only, never to <Button-1>: returning None here
            # threw it away, so Secure's double-click read as one single (the User 2026-10-26). A key gets its press.
            if os.environ.get("HOUSE_WALL_DOUBLE_TO_KEY", "1").strip().lower() not in ("0", "off", "false", "no"):
                return on_click(ev)
            return None
        with st.lock:
            house_d = st.house
        if doc_placement.doc_up(house_d) or not (doc_placement.drag_enabled() and doc_placement.free_drag_on()):
            return None
        if doc_placement.forget_home_place():
            log("home place: double-click on the glass, her picture back in the middle")
        return "break"

    cv.bind("<Double-Button-1>", on_double)
    def _close_wall(by: str = "Ctrl+Q"):
        """Closed on purpose: in a pack the close is recorded, so the launcher's watch never starts this Wall again
        until the person opens it (lib/crissa_watch CLOSED, 2026-10-10); then the window goes."""
        if not crissa_home.is_house():
            try:
                import crissa_watch
                crissa_watch.set_closed("wall", True, by=by, home=crissa_home.HOME)
                log(f"closed by you ({by}): the Wall stays closed until Open the Wall")
            except Exception as e:  # noqa: BLE001
                log(f"close record: {type(e).__name__}")
        root.destroy()
    root.bind("<Control-q>", lambda e: _close_wall("Ctrl+Q"))

    def _hot_strip_up():
        """The band over the windows: see HOT_STRIP at the top of the file."""
        from PIL import Image
        import types

        def _dock_win(title):
            w = tk.Toplevel(root)
            w.withdraw()
            w.title(title)
            w.configure(bg="#10131A")
            try:
                w.attributes("-type", "dock")
            except tk.TclError as e:
                log(f"hot strip: tk -type dock not supported ({e})")
            return w

        def _pin_above(w):
            # ABOVE + STICKY before the first map, on Tk's wrapper (where Tk
            # puts _NET_WM_WINDOW_TYPE); input=False after it, as the wall does
            w.update_idletasks()
            fid = _wm_frame_id(w)
            _xprop(["-id", fid, "-f", "_NET_WM_STATE", "32a", "-set", "_NET_WM_STATE",
                    "_NET_WM_STATE_ABOVE,_NET_WM_STATE_STICKY,_NET_WM_STATE_SKIP_TASKBAR,_NET_WM_STATE_SKIP_PAGER"])
            w.deiconify()
            w.update_idletasks()
            try:
                shape.no_focus(w.winfo_id())
            except Exception as e:  # noqa: BLE001
                log(f"hot strip: no_focus: {e}")

        fallback_top = H - R.DOCK_EDGE - R.DOCK_TILE - 2 * R.DOCK_PAD - HOT_POP_MARGIN[1]
        strip = _dock_win("house-wall-strip")
        strip.geometry(f"{W}x{HOT_STRIP_PX}+0+{H - HOT_STRIP_PX}")
        pop = _dock_win("house-wall-band")
        pop_cv = tk.Canvas(pop, width=10, height=10, highlightthickness=0, bd=0, bg="#10131A", takefocus=0)
        pop_cv.pack(fill="both", expand=True)
        pop_item = pop_cv.create_image(0, 0, anchor="nw")

        # THE POP NEVER MOVES. Driving a dock window's geometry through Mutter
        # lagged the wall by frames and sometimes landed half of it (09:09:06
        # "want 1081x91+716+1346, tk sees 1081x281+716+1346": the new height
        # with the old top) — the image and the shape were computed for a rect
        # the window did not have, and the User saw a slice of the stack floating
        # over his window. One fixed rect at the foot of the glass, tall enough
        # for the stack and its tips; the live band rect is only the INPUT
        # region, and the bounding shape shows the painted chrome alone.
        POP_H = min(H - HOT_STRIP_PX, 460)
        POP = (0, H - HOT_STRIP_PX - POP_H, W, H - HOT_STRIP_PX)
        pop.geometry(f"{POP[2] - POP[0]}x{POP[3] - POP[1]}+{POP[0]}+{POP[1]}")
        pop_cv.config(width=POP[2] - POP[0], height=POP[3] - POP[1])

        def hot_repaint():
            if not hot["shown"]:
                return
            rect = hot_pop_rect(R.CHROME_HITS, W, H - HOT_STRIP_PX, fallback_top)   # ends where the strip starts: no overlap
            if rect != hot["rect"]:
                hot["rect"] = rect
                if rect[1] != (hot.get("rect_log") or (None,))[0]:
                    log(f"band rect {rect}")
                hot["rect_log"] = (rect[1],)
            x0, y0, x1, y1 = POP
            # ONLY THE CHROME IS THERE. The pop's bounding shape is the union
            # of what the renderer painted (keys, tiles, tips, the stack's
            # cards), so over a window the band shows the chrome and nothing
            # else — the window keeps showing through the gaps. The input
            # region is the live band, so hover and leave are the band's.
            paint = getattr(R, "CHROME_PAINT", None) or {}
            r = hot["rect"]
            # clip the paint to the LIVE BAND, not the pop: the pop is full
            # width now, and the dial, the camera and the AR key at the left
            # foot of the glass fell inside it and painted over the Control
            # Panel (the User 10:2x, "same prob"). The band rect is where the
            # chrome may show over a window; nothing outside it is the band's.
            cx0, cy0 = max(x0, int(r[0])), max(y0, int(r[1]))
            cx1, cy1 = min(x1, int(r[2])), min(y1, int(r[3]))
            rects = []
            for box in paint.values():
                bx0, by0, bx1, by1 = (int(v) for v in box)
                bx0, by0 = max(bx0, cx0), max(by0, cy0)
                bx1, by1 = min(bx1, cx1), min(by1, cy1)
                if bx1 > bx0 and by1 > by0:
                    rects.append((bx0 - x0, by0 - y0, bx1 - x0, by1 - y0))
            inp = [(max(r[0], x0) - x0, max(r[1], y0) - y0, min(r[2], x1) - x0, min(r[3], y1) - y0)]
            key = (tuple(rects), tuple(inp))
            if key != hot.get("shape_key"):
                hot["shape_key"] = key
                # the WRAPPER is the window Mutter composites (Tk parents the
                # toplevel under it); shaping only the child left the wrapper opaque
                child = int(pop.winfo_id())
                wrapper = shape.x_parent(child)
                res = shape.bounding(tuple(x for x in (wrapper, child) if x), rects, inp)
                if hot.get("shape_n") != len(rects):
                    hot["shape_n"] = len(rects)
                    log(f"band shape: {res}; band {r}")
            base = last_im.get("bg")
            if base is not None and base.size == (W, H):
                im = base.crop((x0, y0, x1, y1)).convert("RGBA")
            else:
                im = Image.new("RGBA", (x1 - x0, y1 - y0), R.GROUND + (255,))
            for k in ("roll", "hud"):
                lay = last_im.get(k)
                if lay is not None and lay.size == (W, H) and lay.mode == "RGBA":
                    im.alpha_composite(lay.crop((x0, y0, x1, y1)))
            ch = fade.get("src")          # the chrome at full, whatever the wall's fade is at
            if ch is not None and ch.size == (W, H):
                im.alpha_composite(ch.crop((x0, y0, x1, y1)))
            ph = hot.get("photo")
            if ph is not None and (ph.width(), ph.height()) == im.size:
                ph.paste(im)
            else:
                hot["photo"] = ImageTk.PhotoImage(im)
                pop_cv.itemconfig(pop_item, image=hot["photo"])

        def _leave_cancel():
            if hot.get("leave_job") is not None:
                try:
                    root.after_cancel(hot["leave_job"])
                except Exception:  # noqa: BLE001
                    pass
                hot["leave_job"] = None

        def hot_hide():
            _leave_cancel()
            if not hot["shown"]:
                return
            hot["shown"] = False
            hot["inside"] = False
            try:
                pop.withdraw()
            except tk.TclError:
                pass
            log("band down (pointer left)")

        def _glass_ev(ev):
            # ROOT coordinates: the glass is the screen, so x_root/y_root are
            # glass px whatever the pop's geometry is doing. Translating by
            # hot["rect"] lagged the WM's move by a frame when the pop grew
            # for the dock chooser: the pointer read 164 px high, the chooser
            # closed, the pop shrank, and it flickered ten times a second
            # (05:27, "the hover not working well still").
            xr, yr = getattr(ev, "x_root", None), getattr(ev, "y_root", None)
            if xr is None or yr is None:
                xr, yr = ev.x + hot["rect"][0], ev.y + hot["rect"][1]
            return types.SimpleNamespace(x=int(xr), y=int(yr), time=getattr(ev, "time", 0))

        def strip_enter(ev):
            # the pointer is at the bottom edge: wake the chrome (paint_ui fills
            # the hits), then raise the band over it
            hot["inside"] = True
            _leave_cancel()
            on_motion(types.SimpleNamespace(x=int(getattr(ev, "x_root", ev.x)), y=int(getattr(ev, "y_root", H - HOT_STRIP_PX + ev.y))))
            if not hot["shown"]:
                hot["shown"] = True
                hot["rect"] = None
                hot_repaint()
                pop.deiconify()
                log(f"band up over the windows at {hot['rect']}")

        def pop_motion(ev):
            hot["inside"] = True
            _leave_cancel()
            on_motion(_glass_ev(ev))

        def pop_leave(ev=None):
            # A LEAVE WITH THE POINTER STILL IN THE BAND IS THE BAND'S OWN
            # RESIZE: the chooser opening or closing moves the pop's top edge
            # under the pointer and Tk reports Leave then Enter. Honouring it
            # dropped the pointer, closed the chooser, shrank the band, which
            # re-entered and reopened it — "band rect" toggling ten times a
            # second (06:04, the User: "they are just flashing").
            # A Leave across the top edge ALSO reports its last inside
            # position, so the coordinates alone cannot tell the two apart
            # (the User 09-11 later: the stack outlived the pointer). Ask X where
            # the pointer really is a beat later: still in the band → the
            # band's own resize, nothing to do; gone → the real leave.
            xr, yr = getattr(ev, "x_root", None), getattr(ev, "y_root", None)
            rect = hot.get("rect")
            if xr is not None and yr is not None and rect and rect[0] <= xr < rect[2] and rect[1] <= yr < rect[3]:
                def _recheck():
                    r_ = hot.get("rect")
                    try:
                        px_, py_ = root.winfo_pointerxy()
                    except Exception:  # noqa: BLE001
                        px_, py_ = -1, -1
                    if r_ and r_[0] <= px_ < r_[2] and r_[1] <= py_ < r_[3]:
                        return
                    pop_leave(None)
                root.after(120, _recheck)
                return
            # ONE linger timer, re-armed on every leave and cancelled by any
            # re-entry (strip or pop). The first cut armed a timer per leave
            # and a stale one took the band down 0.4 s after it came back
            # up (04:08, six flickers in ten seconds).
            hot["inside"] = False
            on_leave()
            _leave_cancel()
            hot["leave_job"] = root.after(int(HOT_POP_LINGER_S * 1000), lambda: (None if hot["inside"] else hot_hide()))

        def hot_show():
            # a dock launcher opened Advanced: the pop without a strip hover
            _leave_cancel()
            if hot["shown"]:
                hot_repaint()
                return
            hot["shown"] = True
            hot["rect"] = None
            hot_repaint()
            pop.deiconify()
            log(f"band up over the windows at {hot['rect']} (dock key)")

        # Ubuntu's dock is the dock: its own edge reveal, no strip of ours
        # under it (the strip would pop a blank band beside the dock)
        own_strip = dock.own_dock()
        if own_strip:
            strip.bind("<Enter>", strip_enter)
            strip.bind("<Motion>", strip_enter)
        pop_cv.bind("<Enter>", pop_motion)
        pop_cv.bind("<Motion>", pop_motion)
        pop_cv.bind("<Leave>", pop_leave)
        pop_cv.bind("<Button-1>", lambda ev: on_click(_glass_ev(ev)))
        pop_cv.bind("<B1-Motion>", lambda ev: on_b1_motion(_glass_ev(ev)))
        pop_cv.bind("<ButtonRelease-1>", lambda ev: on_b1_release(_glass_ev(ev)))
        hot["repaint"] = hot_repaint
        hot["hide"] = hot_hide
        hot["show"] = hot_show
        if own_strip:
            _pin_above(strip)
        _pin_above(pop)
        pop.withdraw()
        log((f"hot strip: {HOT_STRIP_PX}px dock strip at the bottom edge; the band pops over windows"
             if dock.own_dock() else
             "hot strip: none under Ubuntu's dock; the band pops over windows only for Advanced from its ⋯")
            + " (HOUSE_WALL_HOT_STRIP=0 turns it off)")
    root.after(80, paint_ui)
    root.after(120, on_idle)

    def poll_pointer():
        """Motion only — never synthesize clicks (those stole GNOME folder double-clicks).

        WAYLAND. XQueryPointer only moves when the pointer is over an X
        window, and GNOME's desktop-icons layer sits above this wallpaper,
        so it never is: measured 2026-09-06, zero motion events in 10 min of
        the User using the mouse, chrome never came up. Mutter's IdleMonitor
        (org.gnome.Mutter.IdleMonitor GetIdletime, ms since the last input
        on any device) resets to single digits on every move — that is the
        motion source now; the X query stays as the fallback.
        """
        ptr = Pointer()
        last_xy = None
        last_idle = None
        idle_ok = shutil.which("busctl") is not None
        under_retry = 0.0        # the extension is asked again after a while once it fails
        shell_retry = 0.0        # house-shell (Poll): idle + under + panel in one call
        shell_live = False
        last_shell_at = 0.0      # house-pointer said "shell": the GNOME bar is out
        while not st.stop.is_set():
            moved = False
            pl = None
            if time.time() >= shell_retry:
                pl = shell_poll()
                if pl is None:
                    if shell_live:
                        log("house-shell gone — back to the idle monitor + house-pointer")
                    shell_live = False
                    shell_retry = time.time() + 30.0
                elif not shell_live:
                    shell_live = True
                    log(f"house-shell live: one poll a tick (idle, under, panel bottom={pl['panel_bottom']})")
            if pl is not None:
                shell_state["pl"], shell_state["at"] = pl, time.time()
                ms = pl["idle_ms"]
                if last_idle is not None and ms < last_idle:
                    moved = True
                if (moved and (ui["menu"] or dock_pick["i"] is not None or hot.get("shown"))
                        and input_elsewhere(pl)):
                    pops_away.set()
                last_idle = ms
                if moved:
                    mark_input()  # REST: input anywhere on this desktop
                under = pl["under"]
                # panel_bottom is not read any more: the rail left the top
                # (it shares the dock's band); the GNOME bar is GNOME's.
                if moved and not chrome_should_wake(under):
                    moved = False
                if moved:
                    motion_flag.set()
                time.sleep(0.15)
                continue
            if idle_ok:
                ms = mutter_idle_ms()
                if ms is None:
                    idle_ok = False
                    log("idle monitor unavailable — falling back to XQueryPointer")
                else:
                    if last_idle is not None and ms < last_idle:
                        moved = True
                    last_idle = ms
            if not idle_ok:
                got = ptr.query()
                if got:
                    x, y, _mask = got
                    if (x, y) != last_xy:
                        last_xy = (x, y)
                        moved = True
            if moved:
                mark_input()      # REST: input anywhere on this desktop is someone here, whatever the chrome does
            under = None
            if idle_ok and time.time() >= under_retry:
                # DESKTOP ONLY, with house-pointer live: the idle monitor
                # resets on input anywhere; what is under the pointer decides
                # the wake (chrome_should_wake). Asked every tick, not only on
                # motion, because the GNOME bar's state rides on it too.
                under = pointer_under()
                if under is None:
                    under_retry = time.time() + 30.0
                else:
                    now_u = time.time()
                    if under == "shell":
                        last_shell_at = now_u
            if moved and idle_ok and not chrome_should_wake(under):
                moved = False
            if moved:
                # NEVER touch Tk from this thread. root.after from a worker
                # thread is a known segfault path (two SEGVs today, 11:26 and
                # 12:36, after motion events became frequent). Raise a flag;
                # the Tk loop (on_idle, every 120 ms) turns it into on_motion.
                motion_flag.set()
            time.sleep(0.15)

    if mode == "wallpaper":
        threading.Thread(target=poll_pointer, daemon=True, name="wall-pointer").start()
        log(f"chrome rail_top={rail_top}px; pointer motion poll; clicks pass through except chrome")
    if REST_ON:      # a key or a click on the Wall is someone here (REST); added beside every other binding
        root.bind_all("<KeyPress>", lambda _e: mark_input(), add="+")
        root.bind_all("<ButtonPress>", lambda _e: mark_input(), add="+")
    def _note_screen():
        """The menu's full-screen key follows this window (lib/crissa_menu.desk_screen, EE 2026-10-10). A pack only."""
        if crissa_home.is_house():
            return
        try:
            import crissa_menu
            crissa_menu.note_desk_screen(mode, bool(root.attributes("-fullscreen")))
        except Exception:  # noqa: BLE001
            pass

    def _f11(_e=None):
        root.attributes("-fullscreen", not root.attributes("-fullscreen"))
        _note_screen()

    root.after(1000, _note_screen)
    if mode != "wallpaper":
        root.bind("<F11>", _f11)

    root.update_idletasks()                       # create the X window, still withdrawn
    frame_id = _wm_frame_id(root)
    shape.add_xid(frame_id)
    try:
        shape.add_xid(root.winfo_id())
    except Exception:
        pass
    if mode == "wallpaper":
        _ewmh_pin_desktop(frame_id)
    root.deiconify()
    root.geometry(f"{W}x{H}+0+0")
    if mode == "wallpaper":
        root.lower()
        try:
            root.update_idletasks()
            shape.no_focus(root.winfo_id())
            log("WM_HINTS input=False: clicks do not focus-raise the wall")
        except Exception as e:  # noqa: BLE001
            log(f"no_focus: {e}")
        root.after(800, lambda: _ewmh_report(root, frame_id, W, H))
    if mode == "wallpaper" and HOT_STRIP:
        _hot_strip_up()
    log(f"glass up: mode={mode} {W}x{H} grid={g.COLS}x{g.ROWS} fps={fps} feed={FEED} house={HOUSE} frame={frame_id}"
        + (f" - draws each distinct film frame once; rests at {REST_FPS:g}/s after {REST_AFTER_S:.0f} s unwatched"
           if REST_ON else ""))
    if not crissa_home.is_house():
        # the glass's size, for Setup's machine card (round 15: it times this Wall's render at each density on THIS
        # size and picks the highest that holds 24/s; lib/wall_ascii_disk.density_probe)
        try:
            gn = crissa_home.HOME / "run" / "wall-glass.json"
            gn.parent.mkdir(parents=True, exist_ok=True)
            tmp_ = gn.with_suffix(".tmp")
            tmp_.write_text(json.dumps({"W": int(W), "H": int(H), "at": time.time()}), encoding="utf-8")
            os.replace(tmp_, gn)
        except OSError:
            pass
    # THE CRISSA KEY (the User 2026-10-10, .101: "looks great but lacks features to operate it"): a pack's Wall carries
    # one small key, always on screen, that opens the menu both Walls share (lib/crissa_wall_key, lib/crissa_menu)
    def _capability_loop():
        """What this computer can do (lib/crissa_capability): krew's tapes greyed without krew (2026-10-10)."""
        while not st.stop.is_set():
            try:
                import crissa_capability
                feats = crissa_capability.features().get("features") or {}
                na = set() if (feats.get("krew") or {}).get("ok", True) else {"log_board", "log_tools"}
                if na != R.NA_HUD:
                    R.NA_HUD.clear()
                    R.NA_HUD.update(na)
                    log("capability: %s" % (", ".join(sorted(na)) + " greyed (krew is not installed)" if na else "all keys live"))
            except Exception as e:  # noqa: BLE001  the house, or no module: nothing greyed
                log(f"capability: {type(e).__name__}"[:80]) if R.NA_HUD else None
            st.stop.wait(60.0)
    if not crissa_home.is_house():
        threading.Thread(target=_capability_loop, daemon=True, name="wall-capability").start()
    def _house_menu_hooks() -> dict:
        """THE HOUSE WALL ON THE MENU KEY (the User 2026-10-28, "Crissa Wall + Krew = house"; HOUSE_WALL_MENU_KEY=1):
        the key's view is read here, from what this Wall already knows - the places the dock keys open
        (house_wall_dock.DOCK_KEYS) and the house's voice lock (/v1/house voice.voice_on) - and its verbs are this
        Wall's own: a place is rail_key (front her Brave tab, never a second window), the microphone is voice_on /
        voice_off through the house's /v1/action."""
        def view() -> dict:
            with st.lock:
                h_ = dict(st.house or {})
            on = bool((h_.get("voice") or {}).get("voice_on"))
            v = {"mic": {"on": on, "ear": True,
                         "label": "Microphone: on - turn it off" if on else "Microphone: off - turn it on"}}
            for name in ("composer", "panel", "krew"):
                row = dock.DOCK_KEYS.get(name)
                if row:
                    v[name] = {"label": row[1], "url": row[3] or ""}
            return v

        def mic(want_on: bool) -> None:
            threading.Thread(target=_house_action, args=("voice_on" if want_on else "voice_off",),
                             daemon=True).start()
        return {"view": view, "place": rail_key, "mic": mic}

    try:
        import crissa_wall_key
        if crissa_wall_key.enabled():
            # THE KEYBOARD FOR A POP-UP HE OPENED (round 16, .101 2026-10-13: Return and Escape on the Close Agent box
            # went to his terminal). The desktop Wall never takes the keyboard (WM_HINTS input=False); while a Crissa
            # pop-up he opened is up it lets itself take input and asks the window manager to activate it, then
            # gives the keyboard back to the window that had it (an X window; a Wayland one is not named to us) and
            # goes back under the desktop icons. Kill: CRISSA_CRISSA_POPUP_FRONT=0.
            kbd_ = {"prev": None}

            def _wall_kbd_grant(ts: int = 0):
                me_ = int(str(frame_id), 16)
                m_ = re.search(r"0x[0-9a-fA-F]+", _xprop(["-root", "_NET_ACTIVE_WINDOW"]) if shutil.which("xprop") else "")
                prev_ = int(m_.group(0), 16) if m_ else 0
                kbd_["prev"] = prev_ if prev_ not in (0, me_, root.winfo_id()) else None
                for xid_ in {root.winfo_id(), me_}:
                    shape.input_hint(xid_, True)
                shape.activate(me_, ts)
                root.focus_force()

            def _wall_kbd_release():
                me_ = int(str(frame_id), 16)
                for xid_ in {root.winfo_id(), me_}:
                    shape.input_hint(xid_, False)
                root.lower()
                if kbd_["prev"]:
                    shape.activate(kbd_["prev"])
                kbd_["prev"] = None
            crissa_wall_key.install(root, feed=FEED, open_url=open_url, log=log, W=W, H=H, on_box=paint_ui,
                                    close=lambda: _close_wall("Close the Wall"), canvas=cv,
                                    # Display settings in the menu = the Advanced panel, the dock key's own path
                                    advanced=(lambda: wall_keys["dock_press"]("menu", "menu key")) if wall_keys.get("dock_press") else None,
                                    display=wall_keys.get("display"),     # the Advanced rows as menu rows (2026-10-28)
                                    house=_house_menu_hooks() if crissa_home.is_house() else None,
                                    keyboard=(_wall_kbd_grant, _wall_kbd_release) if mode == "wallpaper" else None)
            crissa_key["box"] = crissa_wall_key.box
            crissa_key["panel_open"] = getattr(crissa_wall_key, "panel_open", None)
            crissa_key["plate"] = crissa_wall_key.plate_box
    except ImportError:
        pass                                       # the house's Wall: its own rail
    except Exception as e:  # noqa: BLE001  the key never takes the Wall down
        log(f"crissa key: {type(e).__name__}: {e}"[:160])
    root.after(50, pump)
    def _dock_refresh():
        last_manifest = 0.0
        own_ = dock.own_dock()
        if not own_:
            dock.clear_geometry()
            log("dock: Ubuntu's dock is the dock; the rail keys are its launchers, pressed through "
                f"{dock.PRESS} (HOUSE_WALL_OWN_DOCK=1 = ours)")
        while not st.stop.is_set():
            if time.time() - last_manifest >= dock.REFRESH_S:
                dock.refresh()             # the favourites still size the rail's place
                last_manifest = time.time()
            if own_:
                dock.poll_running()        # the dots: every 2 s, one ps + a few xprops
                dock.poll_showing()        # Clear desktop's glyph: Mutter's root hint
            st.stop.wait(dock.RUNNING_POLL_S)
    threading.Thread(target=_dock_refresh, daemon=True, name="wall-dock").start()
    if mode == "wallpaper":
        # the wall just mapped over the icons; put them back on top — or,
        # with FOLDERS hidden, keep them down (both directions enforced,
        # the User 2026-09-08 pm: "show or hide desktop folders")
        _want_folders = bool(_prefs().get("folders"))
        root.after(2500, lambda: threading.Thread(target=_ding, args=(_want_folders,), daemon=True).start())
    # DING (desktop icons) lowers ITSELF whenever it is raised or re-mapped
    # (emulateX11WindowType.js: window.lower()), so re-enabling it after the
    # wall sinks it UNDER the wall — that was the "folders gone" of the
    # afternoon. The order that works: DING already up, the wall maps, and
    # the wall lowers itself once (root.lower() above). FOLDERS SHOW does the
    # same: enable DING, then lower the wall once more.
    if mode == "wallpaper" and not crissa_home.is_house():
        import signal as _signal

        def _on_term(_sig, _frm):            # a stop (Stop Crissa, systemctl stop) ends the Tk loop, so the dock goes back
            try:
                root.after(0, root.destroy)
            except Exception:  # noqa: BLE001
                raise SystemExit(0)
        _signal.signal(_signal.SIGTERM, _on_term)
    try:                                     # the Wall draws on its own core (lib/paint_cores; off on the house until set)
        import paint_cores
        paint_cores.pin_thread("wall")
    except Exception:  # noqa: BLE001
        pass
    try:
        root.mainloop()
    finally:
        st.stop.set()
        if mode == "wallpaper" and not crissa_home.is_house():
            try:
                log("dock: %s" % glass_mode.pack_dock_restore())
            except Exception as e:  # noqa: BLE001
                log(f"dock restore: {type(e).__name__}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="house-wall-ascii — the wall without a browser")
    ap.add_argument("--wallpaper", action="store_true", help="desktop-type window below everything (default)")
    ap.add_argument("--fullscreen", action="store_true", help="the glass: fullscreen, dock and top bar hidden")
    ap.add_argument("--fps", type=float, default=float(os.environ.get("HOUSE_WALL_FPS", WALL_FPS_DEFAULT)))
    ap.add_argument("--size", default=os.environ.get("HOUSE_WALL_SIZE", ""), help="WxH (default: the screen)")
    ap.add_argument("--preview", metavar="OUT.png", help="render one frame from the live endpoints and exit")
    a = ap.parse_args(argv)
    size = None
    if a.size:
        w, h = a.size.lower().split("x")
        size = (int(w), int(h))
    if R.np is None:
        log("numpy is missing in this python — the ASCII cannot be drawn. Refusing to start "
            "(run the wall with a Python that has numpy).")
        return 3
    if a.preview:
        return run_preview(a.preview, size)
    if a.fullscreen and a.wallpaper:
        log("--fullscreen and --wallpaper are exclusive")
        return 2
    mode = "fullscreen" if a.fullscreen else "wallpaper"
    return run_glass(mode, a.fps, size)


if __name__ == "__main__":
    raise SystemExit(main())
