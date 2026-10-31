#!/usr/bin/env python3
"""Conductor — Crissa's body, on its own port.

the User, 2026-08-30: "Give Composer its own port whatever you need to play the
tracks smooth and render with greatest accuracy."

WHAT THIS FIXES BY CONSTRUCTION. The wall used to decode, sequence, composite
and downsample in one browser thread, and every class of bug we chased on
2026-08-29/30 came out of that: the decode gap at every swap (readyState 1 for
~80-100 ms), the hold frame that stuck for 25 s, the seek storm from a park
against a clip of the wrong length, the shared-element pause that pinned her on
frame 0. None of those are possible here:

  * THE WHOLE LIBRARY IS DECODED IN RAM. Measured: 7794 frames over 51 clips is
    6.9 GB of RGB against 54 GB free. A cut is an index change. There is no
    decode at a switch, so there is no gap to bridge and no hold frame to hold.
  * ONE CLOCK, AND IT IS OURS. The playhead is a frame counter, not a media
    element's opinion. A wrap is `i == 0`, not "the playhead went backwards".
  * ONE COMPOSITE, AT SOURCE RESOLUTION. The mouth lands on the 736x400 clip,
    the same buffer both planes read. Compositing onto the ASCII cell grid was
    the patch and the bundle; that cannot happen from here.

MEASURED HEADROOM (2026-08-30, this box, CPU only): decode 3600 fps, composite
plus JPEG encode 2100 fps, against 24 clip / 48 mouth needed. The GPU is not
required for any of this and is left for the models. Live dental paste is
~16 ms (2026-09-04), so 48 fps mouth still fits the 20.8 ms period.

WHAT IT DOES NOT OWN. The ASCII ramp, the AR window and the FaceTime corner
stay in the glass. This serves frames and answers questions; it does not draw
the wall.

  GET  /             302 → unified Composer :8199 (house-clip-doctor)
  GET  /composer     same redirect. /composer-classic too. API stays here.
  GET  /sheet/       mouth stills / GIFs / 3-up series (picks with index.html)
  GET  /sheet/<name> one sheet — the User 2026-09-02: do not hunt visemes/picks/
  GET  /frame.mjpg   live composited stream (multipart, for an <img>)
  GET  /frame.jpg    one frame — the eye-check that needs no browser
  GET  /frame-pip.jpg  the same frame for the PIP surface, which may
                     carry a different mouth (HOUSE_PIP_W2L)
  GET  /v1/state     what is playing, which frame, which hub, why
  GET  /v1/health    is she moving
  POST /v1/cue       insert at the next seam; the attach law still vetoes
                     {key, ttl, next, at, by}  ttl<=0 never expires
  POST /v1/abort-hub vocative cut on the JPEG playhead (Playhead.i).
                     Work loop: seek 0 unless remaining ≤ 0.25 s.
                     Flourish/slide wait the tail. insert() still does not cut.
  POST /v1/vocative  Hey Crissa from work: cue FaceTime close-up, abort-hub.
                     Sit-up vocative-up is parked.
  POST /v1/queue     {op: delete|move|clear, id, to}
  GET  /v1/queue     the playlist (keys + row ids)
  GET  /v1/kit       live clips grouped for Composer
  POST /v1/play      {"key": "..."} force a clip; the attach law still vetoes
                     (casual auto-force only while Secure / sage). A host
                     oneshot with host.speech paplays the baked line (the
                     wizard/register already paplay next to play; they send
                     by=init|register so this does not double).
  POST /v1/brain     {"on": true|false} hand the choosing to Crissa, or take it back
  POST /v1/say         {"text":"..."} voice line from Composer (User). 409 if
                     voice is off on the panel.
  POST /v1/mouth-tune  viseme dials; {"on": false} mutes the paint (strip,
                     punch-out, /v1/mouth stay). HOUSE_CONDUCTOR_VISEMES=0
                     boots muted. {"level": 0.8} holds a tile without the voice;
                     {"class":"EE"} holds that viseme class (playlist/head).
                     {"w2l": "off"|"gpu"|"cpu"} picks the mouth — "off" is the
                     viseme strip, which is prod. {"w2l_fps": 8} is the neural
                     grid. Clip playhead stays 24; mouth JPEG is
                     HOUSE_MOUTH_FPS (default 60).
"""

from __future__ import annotations

import collections
import copy
import json
import os
import re
import signal
import subprocess
import shutil
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_home  # the state directory, resolved once (2026-09-26)
# ONE DEFAULT, TWO INSTALLS (2026-10-04): the house's own install keeps every default it always had; a pack
# (the exporter drops `state_home`, so is_house() is False) gets its own. An env var still wins over both.
IS_HOUSE = crissa_home.is_house()


def house_default(house: str, pack: str) -> str:
    return house if IS_HOUSE else pack


def wake_greet_line(home: Path | None = None, named: bool | None = None) -> str:
    """What she says when the close-up lands after a name-call from work ("Yeah, {name}?"; the ear defers its own ack
    to this). The name is the User's own (the User 2026-10-10: a pack spoke the exporter's rewrite of the house's name,
    "Yeah, the User?"): the name Init kept (user.json in the state home: the name typed at Voice ID, first word), else
    the mode file's user id (the house's own house-mode.json names its User), never the generic "user". No name: the
    bare ack the ear would have said ("Yeah?", EAR_BRIDGE_BARE_WAKE_TEXT). Read at each greet, so Init's name counts
    without a restart.

    `named` is the ear's word on THIS wake (the User 2026-10-10, .101 round 4 BB: a guest who said "Hey Crissa" was
    greeted with the User's name): False = the ear did not name the User, so the greet is the bare ack whatever Init
    kept; None = the caller did not say (the house's rail-voice), the name as before."""
    if named is False:
        return os.environ.get("EAR_BRIDGE_BARE_WAKE_TEXT", "Yeah?").strip() or "Yeah?"
    base = Path(home) if home is not None else Path(crissa_home.HOME)
    name = ""
    try:
        name = str(json.loads((base / "user.json").read_text(encoding="utf-8")).get("name") or "").strip()
    except (OSError, ValueError, AttributeError):
        name = ""
    if not name:
        try:
            m = json.loads((base / "house-mode.json").read_text(encoding="utf-8"))
            uid = str(((m.get("auth") or {}) if isinstance(m.get("auth"), dict) else {}).get("user_id")
                      or m.get("user_id") or "").strip()
            name = "" if uid.lower() in ("", "user") else uid.capitalize()
        except (OSError, ValueError, AttributeError):
            name = ""
    name = name.split()[0] if name else ""
    if name:
        line = os.environ.get("EAR_BRIDGE_WAKE_ACK_NAMED", "Yeah, {name}?").strip() or "Yeah, {name}?"
        return line.replace("{name}", name)
    return os.environ.get("EAR_BRIDGE_BARE_WAKE_TEXT", "Yeah?").strip() or "Yeah?"


# A MAC'S SYSTEM PROXY NEVER CARRIES A LOOPBACK CALL (2026-10-04): urllib applies macOS's proxy settings to
# 127.0.0.1. Any *_proxy in the environment replaces the system settings outright on macOS (urllib reads the
# environment first), so a Mac that needs a proxy to go out sets HTTPS_PROXY beside this. Kill CRISSA_NO_PROXY=0.
if sys.platform == "darwin" and os.environ.get("CRISSA_NO_PROXY", "1").strip() != "0":
    for _k in ("NO_PROXY", "no_proxy"):
        os.environ.setdefault(_k, "127.0.0.1,localhost")


# HER VOICE'S ERRORS ARE KEPT (2026-10-04): outside the house a line's output goes to <crissa home>/logs/house-speak.err
# (1 MB, then one .1 copy), and an ask waits up to CRISSA_ASK_SPEAK_CHECK_S for a line that fails at once.
# The house keeps /dev/null and no wait. Kill CRISSA_SPEAK_LOG=0, CRISSA_ASK_SPEAK_CHECK_S=0.
SPEAK_LOG_ON = os.environ.get("CRISSA_SPEAK_LOG", house_default("0", "1")).strip() != "0"
SPEAK_LOG = crissa_home.HOME / "logs" / "house-speak.err"
SPEAK_LOG_MAX = 1_000_000


def env_seconds(name: str, default: str) -> float:
    """A seconds value from the environment; a bad, negative or non-finite one falls back to `default` (2026-10-04:
    CRISSA_ASK_SPEAK_CHECK_S=abc stopped the player at import)."""
    raw = os.environ.get(name, default)
    try:
        v = float(raw or 0)
    except (TypeError, ValueError):
        v = float(default)
    if v != v or v in (float("inf"), float("-inf")) or v < 0:
        v = float(default)
    return v


ASK_SPEAK_CHECK_S = env_seconds("CRISSA_ASK_SPEAK_CHECK_S", house_default("0", "3"))
_SPEAK_LOG_AT: dict[int, int] = {}


import crissa_agent  # Connect: any OpenAI-compatible agent answers her (2026-09-28)
import crissa_aligner  # better mouths: the word aligner, downloaded on first use; packs only (2026-10-01)
import crissa_import  # the import path: a returned loop pack adds to the library; OFF by default (2026-10-01)
import crissa_init  # a pack's first-boot walk on /v1/init; OFF by default, never on the house (2026-10-05)
import crissa_menu  # the Crissa key's menu on both Walls, /v1/menu; a pack only, never the house (2026-10-10)
import crissa_lipsync  # this machine's lip sync, <state home>/lip-sync.json (2026-10-11); no file adds nothing
import wall_frame  # her film's frame on both glasses, kiosk | whole, and each clip's face point (2026-10-12)
import crissa_library_sync  # the Wall pulls finished packs from the user's account; OFF by default (2026-10-01)
import crissa_status  # small status marks for the optional pieces; OFF by default (2026-10-01)
import crissa_page_audio  # her voice in the Wall page (CRISSA_AUDIO_AT=page); OFF by default (2026-10-05)
import crissa_ports  # the loopback port table (2026-10-05)
import crissa_speechq  # one speech lane: talk preempts, queued talk waits, announcements queue (2026-10-06)
import crissa_screen  # the card a helper puts beside her: GET/POST /v1/screen (2026-10-06)
import crissa_rails  # the services she may sit beside, through the player: /v1/ear, /v1/rail/<name>/ (2026-10-06)
import crissa_layout  # where her render sits while a card is up: config/crissa-wall-layout.json (2026-10-06)
import crissa_talk  # how she talks, waits and listens: config/crissa-talk.json + <home>/talk.json (2026-10-06)
# The speaker's tempo and buffering are read once below (MOUTH_TEMPO, MOUTH_BUFFER) from the environment: the talk
# settings fill them in only when the unit did not pin them. Shipped settings are null, so nothing changes.
for _k, _v in crissa_talk.speak_env().items():
    if _k in ("HOUSE_SPEAK_TEMPO", "HOUSE_SPEAK_BUFFER"):
        os.environ.setdefault(_k, _v)
import mouth_mailbox  # noqa: E402   (the viseme sheet store; a bench serves its own)

from conductor import (  # noqa: E402
    comms_cores_with, listen_cores,
    REGISTERS, Graph, Sequencer, envelope_at, viseme_at, mouth_painting,
    mouth_on_air, mouth_paints_on, MOUTH_TAIL_S, HOME, USER_TALK_KEY,
    USER_TALK_ENTER, USER_TALK_LEAVE, USER_TALK_CUT_S, USER_TALK_FADE_S,
    WORK_GLANCE_KEY, VOCATIVE_AT_SEAM,
    VOCATIVE_KEY, VOCATIVE_FAMILIAR,
    DOOR_GLANCE_KEY,
    abort_to_hub_action, ABORT_FPS, ABORT_TAIL_S,
    CLIP_FPS, mouth_render_fps, clip_frames_to_advance,
    auth_already_held, CONV_HOLD,
)
from camera_switch import fade_frame, switch_frame  # noqa: E402
from house_wall_prefs import load as load_wall_prefs, wraps_before_fidget  # noqa: E402


def closeup_dest(key: str, start: str | None,
                 pose_class: str | None = None) -> bool:
    """FaceTime / Casual Comms close-up. Not heads-down, even at pitch=down.

    Every `down_close*` hub is the close-up framing (down_close_user joined
    2026-09-10 as the close-up user comms core); without this the new core
    refused its own close-up tiles and fell through to "no strip" (09-11).

    Harvest rows have no attach hubs (`start` is null). Their `pose_class`
    is the bank — close-3 / mouthzoom / close-shapes are down_close, so
    mouthzoom tiles must paste there (the User 2026-09-16: Drill/Say on the
    speaking harvests painted nothing).
    """
    if key == USER_TALK_KEY or str(start or "").startswith("down_close"):
        return True
    pc = str(pose_class or "")
    return pc.startswith("down_close") or pc == "close"


def closeup_tile(rel: str | None) -> bool:
    """Close-station harvest tile. Own class; never a desk / listen donor.

    The 2026-09-08 guard keyed on `mouthzoom` in the path. Those films were
    culled; the live close bank is `up-close-harvest-*` / `up-close-listening`
    (`_CLOSE_HARVEST` in clip-doctor-shell). Dest is still down_close (FT
    loop, harvest pose_class). A close dest + a non-mouthzoom close tile
    skipped as `tile class` and painted 0 while the sheet walked (the User
    2026-09-16, bench: on_air True, pl_class TDN, painted 0, pin_match None).
    Do not revert the harvest names to the culled films.
    """
    s = str(rel or "").lower()
    if "mouthzoom" in s:
        return True
    if "up-close-harvest" in s or "up-close-listening" in s:
        return True
    if "harvest-shapes-close" in s or "crissa-close" in s:
        return True
    # station bin in the harvested filename (close/0 → …_close_0.png)
    return "_close_0" in Path(s).name
import mouth_pins as MP  # noqa: E402
from mouth_playlist import (  # noqa: E402
    PAD as PLAYLIST_PAD,
    FACE_CROP_TAG,
    REST_DWELL_MAX_S,
    REST_DWELL_MIN_S,
    SERIES_ONCE_S,
    SERIES_SLICE_S,
    as_tiles,
    tile_take,
    lookup as playlist_lookup,
    turn_band,
    TURN_FRONT_MAX,
    PITCH_FOLD,
    PITCH_FOLD_FROM,
    PITCH_FOLD_TO,
    pitch_fold,
    station_lookup,
    HUB_STATION,
    turn_lookup,
    TURN_LIBRARIES,
    TURN_LIBRARIES_OTHER,
    MIRROR_SUFFIX,
    note_span,
    pick_series_tile,
    rest_step,
    playlist_path_for,
    health as mouth_bank_health,
    load_playlist,
    viseme_lead_ms,
    viseme_lead_s,
)
from mouth_viseme import load_class_index, pick_tile, viseme_of  # noqa: E402
import maps_ahead  # noqa: E402
import mouth_patch  # noqa: E402   finished mouth patches, kept and built ahead (2026-10-13, 2026-10-24)
import paint_cores  # noqa: E402   her paint on cores of its own (2026-10-14)
import crissa_face  # noqa: E402   face only (2026-10-16)
import crissa_display  # noqa: E402   her display tool: a document or a page in the letter on the right (2026-10-16)
import crissa_phone_room  # noqa: E402   her room empty while she is on the phone (round 36, 2026-10-20)
import crissa_phone_card  # noqa: E402   the menu's Your phone as a QR card (round 36, 2026-10-20)
import crissa_phone_firewall  # noqa: E402   the card's "Allow my phone on this Wi-Fi" key (2026-10-26)
from kit_labels import WHAT_THEY_ARE  # noqa: E402   clip data; the brain is optional (_make_brain)
from pose_inventory import load as _inv_load_file  # noqa: E402


# SETUP'S FILMS WHATEVER FACE SHE WEARS (the User 2026-10-29, the first-time walk of 1.0.1: with Crissa desk v2 worn the
# player refused Lila's Setup films - 409 "does not attach to down" - so the Setup page waited on a film that never
# finished, the mic check and Voice ID stalled, and Crissa sat on the Wall before her entrance). Lila's films and her
# hub live in the kit's own Setup library (display/crissa-poses); a worn library that lacks them borrows them from there,
# with absolute paths, so the worn library's files are never touched (a change there restarts the mouth prebake). Pack
# only: the house wears that library itself. Kill: CRISSA_SETUP_FILMS_FROM_KIT=0.
def _setup_library() -> "Path | None":
    if IS_HOUSE or os.environ.get("CRISSA_SETUP_FILMS_FROM_KIT", "1").strip().lower() in ("0", "false", "off", "no"):
        return None
    base = ROOT / "display" / "crissa-poses"
    try:
        if not (base / "inventory.json").is_file() or base.resolve() == POSE_DIR.resolve():
            return None
    except OSError:
        return None
    return base


def with_setup_films(inv: dict) -> dict:
    """The worn inventory plus Lila's Setup rows (key lila-*) and the hubs they stand on, from the kit's Setup library,
    for whatever the worn one lacks. A new dict; the worn inventory is not changed."""
    base = _setup_library()
    if base is None or not isinstance(inv, dict):
        return inv
    try:
        b = json.loads((base / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return inv
    clips = dict(inv.get("clips") or {})
    hubs = dict(inv.get("hubs") or {})
    need_hubs = set()
    for k, row in (b.get("clips") or {}).items():
        if not str(k).startswith("lila-") or k in clips or not isinstance(row, dict) or not row.get("file"):
            continue
        r = dict(row)
        for f in ("file", "playlist", "mouth_sidecar"):
            if r.get(f) and not Path(str(r[f])).is_absolute():
                r[f] = str(base / str(r[f]))
        clips[str(k)] = r
        need_hubs.update(h for h in (r.get("start"), r.get("end")) if h)
    for h, plate in (b.get("hubs") or {}).items():
        if h in need_hubs and h not in hubs and plate:
            hubs[h] = plate if Path(str(plate)).is_absolute() else str(base / str(plate))
    if len(clips) == len(inv.get("clips") or {}):
        return inv
    out = dict(inv)
    out["clips"], out["hubs"] = clips, hubs
    return out


def inv_load(path=None):
    """pose_inventory.load, plus Setup's films for a worn library that lacks them (with_setup_films)."""
    inv = _inv_load_file(path)
    if path is None or Path(path).resolve() == (POSE_DIR / "inventory.json").resolve():
        return with_setup_films(inv)
    return inv
# The Clip Doctor's port and the takes it loads live in ONE file, the same
# one scripts/house-nav-sync.py generates the house bar from. The Composer
# must not grow a second hardcoded 127.0.0.1:8213.
#
# OPTIONAL (the startup split, 2026-09-22): the public player ships without the
# house nav rail. Absent, the two /v1/kit fields are empty and /composer (an
# authoring GET, refused in that edition anyway) has nowhere to go. An UNSET
# HOUSE_DOCTOR_URL is "" here, never house_nav's own :8213 default — a player
# with no Doctor must not advertise one. house_nav.py itself is not edited: its
# strings are pinned byte-equal across eight surfaces.
try:
    from house_nav import DOCTOR as DOCTOR_BASE, TAKES as DOCTOR_TAKES  # noqa: E402
    from house_nav import COMPOSER as COMPOSER_URL  # noqa: E402
except ImportError:
    DOCTOR_BASE = os.environ.get("HOUSE_DOCTOR_URL", "")
    DOCTOR_TAKES = ()
    COMPOSER_URL = ""


# A PACK'S COMPOSER IS A LINK OUT (the User 2026-10-07: store.zoeyos.dev). /composer (and its old names) goes there;
# `/` never does: COMPOSER_URL stays "" in a pack, so the player's own root keeps opening the Wall or Connect.
COMPOSER_LINK = crissa_home.composer_url()
COMPOSER_PATHS = ("/composer", "/composer.html", "/composer-classic", "/composer-classic.html")


def composer_location(composer_url: str, link: str, connect_on: bool, wall_page: bool = False) -> str:
    """Where /composer sends a browser: the house's Composer when this install has one (as always), else the
    Composer's link out (crissa_home.composer_url: the store unless overridden), else wherever `/` goes."""
    if composer_url:
        return composer_url + "/"
    if link:
        return link
    return root_location("", connect_on, wall_page)


def root_location(composer_url: str, connect_on: bool, wall_page: bool = False) -> str:
    """Where `/` sends a browser: the Composer when this install has one, else the Wall page when it is
    on (CRISSA_WALL_PAGE, 2026-10-05), else the Connect page (a pack has no Composer, and "" + "/" sent
    `/` to itself forever - 2026-10-04)."""
    if composer_url:
        return composer_url + "/"
    if wall_page:
        return "/wall"
    return "/connect" if connect_on else "/frame.jpg"


import fast_reduce  # noqa: E402  bit-identical paint reductions in a pack (round 11, 2026-10-12)
from pose_mouth import (  # noqa: E402
    composite_stamp,
    hide_clip_jaw,
    strip_lip_alpha,
    tone_b as _tone_b,
    xfade_mix, xfade_ease,
    square_alpha, band_alpha, live_bbox,
)
from head_frame import (  # noqa: E402
    mirror_head,
    cheeks_maps_from_solve,
    shift_maps,
    TAKE_WIDTH_CLASSES,
    apply_width_k,
    arch_mask,
    fit_arch,
    fit_mouth_width,
    inpaint_plane,
    mouth_quad,
    ray_deg,
    take_width_k,
    unpack as unpack_head,
    shift_nose, ema_nose, turn_correct, apply_uniform_k, pin_upper_lip, pin_nose_and_lip, pin_nose_pixel,
    pin_chin_pixel, stretch_affine_axis, stretch_maps_axis,
    warp_affine_2x3,
    warp_dental_plane_maps,
    warp_diamond_maps,
    warp_triad_3x3,
    frame_wh,
    frame_from_library,
)
import head_frame as _hf_mod  # noqa: E402  (live dials set module attributes)
from poly_cache import (  # noqa: E402
    build_scene, live_lookup, dental_r0_cached, dental_maps_cached,
    warm_dental_r0, loop_version, p_key_from_dental,
    dental_cache_on, dental_cache_stats, dental_maps_limit,
    cheeks_solve_cached, cheeks_maps_cached, pin_shift_cached,
    mouth_cache_stats, reset_mouth_caches, reset_tile_caches, diamond_maps_cached, m_key_of, drop_loop, tri_cached,
    nose_ema_track,
)
from avatar import head_geom as _avatar_head_geom, current_name as _avatar_current_name  # noqa: E402
from avatar import listen_loop as _avatar_listen_loop, boot_loop as _avatar_boot_loop, roles as _avatar_roles  # noqa: E402
from avatar import rest_loop as _avatar_rest_loop  # noqa: E402
import clip_audio  # noqa: E402  baked host-line paplay (Composer /v1/play is silent without it)

HOST, PORT = "127.0.0.1", int(os.environ.get("HOUSE_CONDUCTOR_PORT", "8100"))   # a second face is a second conductor (Lila :8101), 2026-09-11


# ── the glass canary ────────────────────────────────────────────────────────
# What the eye tunes against is the GLASS, and the glass is late: the Tk wall
# polls /frame.jpg, decodes, renders ASCII and hands Tk a PhotoImage; the
# Composer stage goes through a browser. A drill measures tiles against the
# film INSIDE this process and cannot see any of that. Each glass reports its
# own measured lag here (median / p90 over its last draws); nothing pads from
# it until the numbers have been looked at (the User 2026-09-17).
GLASS_LAG: dict = {}
GLASS_LAG_FRESH_S = 15.0


def glass_lag_report(body: dict) -> dict:
    who = str(body.get("who") or "glass")[:40]
    try:
        med = float(body.get("median_ms"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "median_ms is a number"}
    if not (0.0 <= med <= 5000.0):
        return {"ok": False, "error": "median_ms out of range"}
    rec = {"median_ms": round(med, 1), "at": time.time()}
    for k in ("p90_ms", "min_ms", "fps"):
        try:
            rec[k] = round(float(body.get(k)), 1)
        except (TypeError, ValueError):
            pass
    try:
        rec["n"] = int(body.get("n"))
    except (TypeError, ValueError):
        pass
    if body.get("how"):
        rec["how"] = str(body["how"])[:80]
    GLASS_LAG[who] = rec
    if who == crissa_lipsync.GLASS_WHO and not IS_BENCH:
        try:                                           # round 45: the lead follows the glass (lib/crissa_lipsync)
            crissa_lipsync.note_glass(who, rec, crissa_lipsync.glass_follow_on() and _glass_warm())
        except Exception:  # noqa: BLE001  a courtesy: the report stands without it
            pass
    return {"ok": True, "who": who, "glass_lag": glass_lag_state()}


def _glass_warm() -> bool:
    """The desk Wall is warm: its build done and its newest lap drew nothing fresh (lib/crissa_build)."""
    import crissa_build
    b = crissa_build.build_view()
    lap = b.get("lap") or {}
    return bool(b.get("done")) and bool(lap) and int(lap.get("fresh") or 0) == 0 and not lap.get("cache_off")


def wall_frame_state(lib, key) -> dict:
    """The glasses' frame (lib/wall_frame.py, the User 2026-10-12): the mode the switch file says, and the face point of
    the clip on the glass (the median nose of its head track, film fractions; None = no track, the glass uses the
    kiosk's point), so the browser Wall frames her as the desk Wall does without reading the library."""
    fp = None
    try:
        row = (getattr(lib, "rows", None) or {}).get(key) or {}
        stem = Path(str(row.get("file") or key)).stem
        if key:
            fp = wall_frame.face_point_file(POSE_DIR / (stem + ".head.json"))
    except Exception:  # noqa: BLE001 - the frame's point is a nicety; the payload never fails on it
        fp = None
    return {"mode": wall_frame.mode(), "face_point": list(fp) if fp else None}


def glass_lag_state() -> dict:
    now = time.time()
    out = {}
    for who, rec in list(GLASS_LAG.items()):
        age = now - float(rec.get("at") or 0.0)
        out[who] = dict({k: v for k, v in rec.items() if k != "at"},
                        age_s=round(age, 1), fresh=age <= GLASS_LAG_FRESH_S)
    return out


# THE BENCH IS CALIBRATED TO THE GLASS (the User 2026-09-18: "calibrate for 0 to
# mean it hits same here as on the glass. Put a delay in here to mimic the
# time to glass"). The Composer stage shows a frame ~35 ms after it is picked;
# the Tk wall ~117 ms (the canary, /v1/glass-lag). A bench line is therefore
# stamped LATER by that difference, so the same sheet-lead number looks the
# same on this stage as on the wall, and "set as default" carries it over
# unchanged. The pad is the live canary difference (wall − stage), refreshed
# every 10 s, else the last measured, else HOUSE_BENCH_GLASS_PAD_FALLBACK_MS
# (82, measured 09-18). Pin it: HOUSE_BENCH_GLASS_PAD_MS=<n>. Off: =0.
WALL_CONDUCTOR = os.environ.get("HOUSE_WALL_CONDUCTOR", "http://127.0.0.1:8100")
_GLASS_PAD = {"ms": None, "how": "", "at": 0.0}


def glass_pad_ms() -> float:
    """What a bench line is delayed by to mimic the wall's glass. 0 on the wall."""
    if not IS_BENCH:
        return 0.0
    raw = (os.environ.get("HOUSE_BENCH_GLASS_PAD_MS") or "auto").strip().lower()
    if raw not in ("", "auto"):
        try:
            return max(0.0, min(400.0, float(raw)))
        except ValueError:
            pass
    if _GLASS_PAD["ms"] is not None:
        return float(_GLASS_PAD["ms"])
    try:
        return max(0.0, min(400.0, float(os.environ.get("HOUSE_BENCH_GLASS_PAD_FALLBACK_MS", "82"))))
    except ValueError:
        return 82.0


def _glass_pad_measure() -> None:
    """wall-tk median − composer-stage median, both fresh, else keep what we had."""
    try:
        mine = (glass_lag_state().get("composer-stage") or {})
        with urllib.request.urlopen(WALL_CONDUCTOR + "/v1/state", timeout=2.0) as r:
            w = json.load(r)
        wall = ((w.get("glass_lag") or {}).get("wall-tk") or {})
        if mine.get("fresh") and wall.get("fresh"):
            pad = max(0.0, min(400.0, float(wall["median_ms"]) - float(mine["median_ms"])))
            _GLASS_PAD.update(ms=round(pad, 1), how="wall %d − stage %d" % (round(wall["median_ms"]), round(mine["median_ms"])), at=time.time())
    except Exception:
        pass


def _glass_pad_thread() -> None:
    while True:
        _glass_pad_measure()
        time.sleep(10.0)


def speak_timing_env() -> dict:
    """The four timing dials as house-speak / mouth_timing env for one line."""
    return {
        # A FRAME IS A FRAME AT ANY SPEED (the User 2026-09-21: "time virus").
        # The holds are stored in slots, which stretch with the film. These
        # were stored in MILLISECONDS, which do not — so the same dial spanned
        # HALF as many film frames at 1/2 speed, and the page carried a note
        # apologising for it. The dial means "N frames at 1x"; at rate R a film
        # frame lasts 1/R longer, so it is handed over as N frames of THIS
        # rate's wall time. Tune at any speed and it transfers.
        # The glass pad is NOT scaled: it is how long this machine's glass
        # takes, which no playback rate changes.
        "HOUSE_MOUTH_SHEET_LEAD_MS": str(int(round(
            effective_sheet_lead_ms() / max(0.05, float(PLAY_RATE)) - glass_pad_ms()))),
        # this conductor's own live dial, handed over on purpose: house-speak
        # keeps it (a bench tunes with it) instead of asking the wall (2026-09-17)
        "HOUSE_MOUTH_LEAD_SRC": "explicit",
        "HOUSE_MOUTH_TIMING_LEAD_MS": str(int(round(
            MOUTH_TIMING_LEAD_MS / max(0.05, float(PLAY_RATE))))),
        "HOUSE_MOUTH_MIN_HOLD": str(int(MOUTH_MIN_HOLD)),
        "HOUSE_MOUTH_CONS_HOLD": str(int(MOUTH_CONS_HOLD)),
        "HOUSE_MOUTH_SENT_PAD": str(int(MOUTH_SENT_PAD)),
        "HOUSE_MOUTH_WORD_PAD": str(int(MOUTH_WORD_PAD)),
        "HOUSE_MOUTH_PAUSE_WORDS": "%.2f" % float(MOUTH_PAUSE_WORDS),
        "HOUSE_MOUTH_BLIP_WORDS": "%.2f" % float(MOUTH_BLIP_WORDS),
        "HOUSE_MOUTH_TAIL_WORDS": "%.2f" % float(MOUTH_TAIL_WORDS),
        "HOUSE_SPEAK_TEMPO": "%.3f" % (float(MOUTH_TEMPO) * float(PLAY_RATE)),
        # F1a/F1b (2026-09-20): house-speak sizes one sheet slot as half a FILM
        # frame, so it needs the film's rate and this playhead's speed. Without
        # them the slot falls back to a flat 20 ms and the dwell dials mean less
        # film time the slower you play — which is what forced the AutoTuner to
        # 1x. Both are read per line (house-speak is spawned per line).
        "HOUSE_CLIP_FPS": "%.3f" % float(CLIP_FPS),
        "HOUSE_PLAY_RATE": "%.3f" % float(PLAY_RATE),
        "HOUSE_SPEAK_BUFFER": "1" if MOUTH_BUFFER else "0",
        # the Composer's audio ducks the gateways' TTS to half while it plays
        "HOUSE_SPEAK_DUCK_PCT": os.environ.get("HOUSE_SPEAK_DUCK_PCT", "50"),
        # THE SHEET GOES WHERE THIS CONDUCTOR READS IT (2026-09-15). On the
        # wall that is :8099, exactly as before. On a bench it is the bench's
        # own /v1/mouth, so a Say or a drill on the Composer paints the bench
        # and the glass never sees the hops. The audio still goes to
        # speak_sink — the User hears the line either way.
        "VO_MOUTH_URL": MOUTH_URL,
        # a pack's line asks THIS conductor for its lead and tune (there is no :8100 house wall)
        **({"HOUSE_CONDUCTOR": "http://127.0.0.1:%d" % PORT} if OWN_MAILBOX else {}),
        # and the wall's caption is not a bench's to write (the ear gate is
        # untouched: the room goes deaf for a tuning line by design)
        **({"HOUSE_SPEAK_WHO_TAG": "bench"} if IS_BENCH else {}),
        # BETTER MOUTHS (2026-10-01): a pack's line asks its own aligner when the switch is on, else
        # takes the letter clock without probing. {} on the house, so its lines are unchanged.
        **crissa_aligner.line_env(),
    }


_SPEAKERS: list = []   # Popen of every house-speak this conductor spawned; state `say_pending`
_SPEAK_AT: dict = {}   # pid -> spawn time, for the wedge watchdog below
# A SPEAKER THAT NEVER EXITS WEDGES EVERY LATER LINE (the User 2026-09-15: "Tune
# not tiling at all"). house-speak serialises on the audio lock, so one stuck
# process makes every following Say queue behind it forever: no air, no sheet,
# no tiles, and nothing in the log to say why — `say_pending` just sits at 2.
# Measured that day on the bench: a Say from the Tune say-storm had been alive
# 11 min 47 s and was holding everything behind it.
#
# Nothing legitimate takes this long. The longest line the Composer sends is
# the phoneme staple (~430 chars, ~35 s spoken, and it is paced in pieces), so
# 150 s is a wide margin over the worst honest case.
try:
    SPEAK_WEDGE_S = float(os.environ.get("HOUSE_SPEAK_WEDGE_S") or 150.0)
except (TypeError, ValueError):
    SPEAK_WEDGE_S = 150.0


def reap_wedged_speakers() -> int:
    """Kill any house-speak older than SPEAK_WEDGE_S. Returns how many."""
    if SPEAK_WEDGE_S <= 0:
        return 0
    now = time.time()
    n = 0
    for proc in list(_SPEAKERS):
        if proc.poll() is not None:
            _SPEAK_AT.pop(proc.pid, None)
            continue
        born = _SPEAK_AT.get(proc.pid)
        if born is None or (now - born) < SPEAK_WEDGE_S:
            continue
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                pass
        _SPEAK_AT.pop(proc.pid, None)
        n += 1
        action("say", "killed a wedged speaker (%.0f s on the lock)" % (now - born), "wedge-watchdog")
    return n


def say_pending() -> int:
    """How many spawned house-speaks are still alive (buffering, waiting on
    the speech lock, or on the air). The Composer's line loop waits on this:
    with the buffered path, air drops for the length of the voice's generation
    between lines, and a loop keyed on air alone stacked four speakers on
    the lock (2026-09-13 03:5x)."""
    alive = [p for p in _SPEAKERS if p.poll() is None]
    for gone in [q for q in _SPEAKERS if q not in alive]:
        _SPEAK_AT.pop(gone.pid, None)
    _SPEAKERS[:] = alive
    return len(alive)


def say_stop_all() -> int:
    """SIGTERM every live house-speak this conductor spawned (own groups: the
    paplay goes too; house-speak's SIGTERM handler runs its finally) and cut
    the baked clip line. Returns how many speakers were stopped."""
    n = 0
    for p in list(_SPEAKERS):
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                n += 1
            except (ProcessLookupError, PermissionError, OSError):
                pass
    try:
        clip_audio.stop()
    except Exception:  # noqa: BLE001
        pass
    if PAGE_AUDIO is not None:      # a line playing in the Wall page stops on the page's next poll
        PAGE_AUDIO.cancel()
    say_pending()
    return n


# HER OWN BODY NEVER CUTS HER LINE (the User 2026-10-11, "Stop cut off her lines - yes"). play() stopped every
# speaker on ANY clip change, so a move the sequencer made by itself killed the line on the air: on .101 a covering
# flourish cut to the hub's rest so her mouth could paint (rest_while_speaking) SIGTERMed the very line it was cut
# for (05:23:07 test line, rc 143 in 0.96 s, "flourish-glance left" logged between the two speaks). A seam, a route
# home, a flourish ending or a camera cut landing now leaves the line alone; a play someone asked for (/v1/play) or
# a mode switch (Secure lamp, Render host) passes deliberate=True and stops it as before, and a clip that speaks
# for itself (a baked host line) still takes the air - one voice. The Stop, a new line, barge-in and the bench's
# follow-clip have their own stops and do not come through here. Kill HOUSE_LINE_NO_SELF_CUT=0.
LINE_NO_SELF_CUT = os.environ.get("HOUSE_LINE_NO_SELF_CUT", "1").strip().lower() not in ("0", "false", "no", "off")


def clip_change_cuts_line(deliberate: bool, enters_a_voice: bool) -> bool:
    """Does this clip change stop the line on the air? A deliberate play or a clip with its own voice: yes."""
    return bool(deliberate or enters_a_voice or not LINE_NO_SELF_CUT)


def _speak_log_open():
    """(target, offset) for one line's house-speak output: (DEVNULL, -1) when off or on any error -
    a log must never cost her a line."""
    if not SPEAK_LOG_ON:
        return subprocess.DEVNULL, -1
    try:
        SPEAK_LOG.parent.mkdir(parents=True, exist_ok=True)
        if SPEAK_LOG.exists() and SPEAK_LOG.stat().st_size > SPEAK_LOG_MAX:
            os.replace(SPEAK_LOG, SPEAK_LOG.with_name(SPEAK_LOG.name + ".1"))
        f = open(SPEAK_LOG, "ab")
        at = f.tell()
        f.write(("--- %s line\n" % time.strftime("%Y-%m-%d %H:%M:%S")).encode())
        f.flush()
        return f, at
    except OSError:
        return subprocess.DEVNULL, -1


def speak_log_tail(pid: int) -> str:
    """The last non-empty line this pid's house-speak wrote to the log (300 chars at most), else ''."""
    try:
        at = _SPEAK_LOG_AT.get(pid, -1)
        if at < 0:
            return ""
        with open(SPEAK_LOG, "rb") as f:
            f.seek(at)
            rows = [ln.strip() for ln in f.read().decode(errors="replace").splitlines() if ln.strip()]
        rows = [r for r in rows if not r.startswith("--- ")]
        return rows[-1][:300] if rows else ""
    except Exception:  # noqa: BLE001
        return ""


def _speak_failed_fast(proc, check_s: float) -> str:
    """'' unless house-speak exits non-zero within `check_s` (then why, from its log). poll() is race-safe
    with the SIGCHLD reaper."""
    if proc is None or check_s <= 0:
        return ""
    t_end = time.time() + check_s
    while True:
        rc = proc.poll()
        if rc is not None:
            if rc == 0:
                return ""
            return "she could not speak (exit %d): %s" % (rc, speak_log_tail(proc.pid) or "see " + str(SPEAK_LOG))
        if time.time() >= t_end:
            return ""
        time.sleep(0.1)


# THE LIP-SYNC TEST LINE IS ALIGNED BEFORE HER FIRST WORD (NN, .101 2026-10-11). The line exists so the person can
# judge her lips, and streamed it spoke on the letter clock until the voice had finished it (the whole-line alignment
# needs the whole line): on .101 every cut test line was the letter clock end to end. A pack's /v1/speak `by: "lip
# sync"` line takes house-speak's buffered path instead (HOUSE_SPEAK_BUFFER=1: the whole line made, aligned, then
# played), so its mouth is the aligned clock from the first word; it starts once the voice has made it (Pocket on
# .101: about 4 s for the 7.5 s line). Never on the house. Kill CRISSA_LIPSYNC_TEST_ALIGNED=0.
_ALIGN_FIRST: dict = {}     # text -> when it was asked for; read once by _spawn_speak
INIT_HOLD: dict = {"key": None}   # round 34: Setup holds this clip on its first frame (THE OFFICE WAITS, lib/crissa_init)


def lipsync_test_aligned_on() -> bool:
    return (not IS_HOUSE and os.environ.get("CRISSA_LIPSYNC_TEST_ALIGNED", "1").strip().lower()
            not in ("0", "off", "false", "no"))


# A TIMED LINE HAS NO STAMP HOLD (round 21, .101 2026-10-14 round 20: Setup's lip sync read her voice 163.3 ms after
# its play call against round 19's 55.3 ms on the same speakers, a 108 ms jump). Round 20 timed Crissa's own lines
# through this player (the coach's HER PLAYER LINES ARE TIMED TOO) and, the same round, a pack's house-speak began holding
# a line's first piece for its stamp (THE FIRST PIECE WAITS FOR ITS STAMP), writing silence once the line's own lead-in is
# spent - and the coach cuts that lead-in to ~0.1 s. The silence went into the room after the air stamp the timing is
# read from, so the lip sync took it for the speakers' delay. A line the coach times (`timed` on /v1/speak) now plays
# with no hold: its air stamp is its first sample again. Her mouth on those lines may open on the letter guess for the
# few hundred ms the stamp takes; during Setup's ending they ride her walk-in, where no mouth is painted. A pack only.
# Kill CRISSA_SETUP_TIMED_NO_HOLD=0.
TIMED_NO_HOLD = (not crissa_home.is_house()
                 and os.environ.get("CRISSA_SETUP_TIMED_NO_HOLD", "1").strip().lower() not in ("0", "false", "no", "off"))


def _spawn_speak(text: str, who: str = "crissa", wav: str = "", no_hold: bool = False, level: bool = False):
    """Own process group. abort_mouth killpg's the speaker, not Composer. `wav` (round 18): a line already rendered
    (speak_wav_ok), played through house-speak's own --wav path with its sheet - Setup's coach lines, her lips on.

    Popen without start_new_session put house-speak in the conductor's
    group; a name-interrupt then SIGTERM'd the wall body. stdin is DEVNULL so a systemd service has no TTY to
    inherit; stdout/err are DEVNULL on the house and the speak log outside it (2026-10-04).
    """
    cmd = [sys.executable, str(ROOT / "scripts" / "house-speak.py"),
           "--voice", who, "--text", text] + (["--wav", str(wav), "--out", str(wav) + ".played.wav"] if wav else [])
    # The timing dials ride this line's house-speak (it reads them once at
    # import): sheet lead, first-class lead, min/consonant hold.
    env = dict(os.environ)
    # the talk settings' speaker dials (Init writes them); a variable the unit pinned wins, and the
    # conductor's own live dials below win over both. Shipped: all null, so nothing changes.
    env.update(crissa_talk.speak_env())
    env.update(speak_timing_env())
    if _ALIGN_FIRST.pop(text, None) is not None:
        env["HOUSE_SPEAK_BUFFER"] = "1"         # the lip-sync test line: aligned before her first word (NN)
    if no_hold:
        env["HOUSE_SPEAK_STAMP_HOLD"] = "0"     # A TIMED LINE HAS NO STAMP HOLD (round 21)
    if wav and level:
        env["HOUSE_SPEAK_WAV_LEVEL"] = "1"      # round 40 N22: her own voice rendered ahead, levelled as her lines
    reap_wedged_speakers()          # a wedged predecessor would swallow this line
    out, at = _speak_log_open()
    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT if out is not subprocess.DEVNULL else subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        if out is not subprocess.DEVNULL:
            out.close()             # the child has its own copy
    _SPEAKERS.append(proc)
    _SPEAK_AT[proc.pid] = time.time()
    if at >= 0:                     # the house logs nothing, so it keeps nothing here either
        _SPEAK_LOG_AT[proc.pid] = at
        while len(_SPEAK_LOG_AT) > 256:
            _SPEAK_LOG_AT.pop(next(iter(_SPEAK_LOG_AT)))
    return proc


# CONNECT (2026-09-28): the player answers a person through any agent that speaks
# OpenAI chat-completions — the Connect page stores url/key/model in
# <crissa home>/agent.json (lib/crissa_agent.py). Public routes, not authoring:
# a pack with no agent cannot say anything at all. Kill CRISSA_CONNECT=0.
CONNECT = {"conv": crissa_agent.Conversation(), "last": None, "busy": 0,
           "lock": threading.Lock(), "asking": None}

# STILL WAITING ON YOUR ENDPOINT (2026-10-06, crissatest): one question at a time goes to the agent, so a slow
# endpoint held every later ask for ~45 s and nothing said so. Now the wait is visible - /v1/state carries
# `agent_waiting` and an ask that queues behind it answers with a `note` - and nothing is cut off: no timeout
# is added here (the User's rule: no hidden caps). The note starts after ASK_WAIT_NOTE_S of waiting.
ASK_WAIT_NOTE_S = float(os.environ.get("CRISSA_ASK_WAIT_NOTE_S", "5") or 5)


# A LINE ALREADY RENDERED (round 18, lib/crissa_init LILA'S LIPS ON HER LINES): Setup's coach renders its own lines (the
# counts' cues, the team's prints), and /v1/speak plays one through house-speak's --wav path, so the line is the coach's
# own audio with her mouth drawn on it. Only a file under the state home's cache, off the house, with ffmpeg here (the
# --wav path decodes with it); anything else is refused and the coach plays the line itself, as before.
def speak_wav_ok(wav: str) -> str:
    """'' when /v1/speak may play `wav`, else why not."""
    if IS_HOUSE:
        return "not on the house"
    try:
        p = Path(str(wav)).resolve()
        root = (crissa_home.HOME / "cache").resolve()
    except (OSError, ValueError):
        return "not a path"
    if root not in p.parents or not p.is_file():
        return "not a rendered line of this install"
    if shutil.which("ffmpeg") is None:
        return "no ffmpeg here"
    return ""


def connect_waiting(now: float | None = None, after_s: float | None = None) -> dict | None:
    """{"s", "asked", "note"} while the agent has had a question longer than the note threshold, else None."""
    cur = CONNECT.get("asking")
    if not cur:
        return None
    s = (time.time() if now is None else now) - float(cur.get("since") or 0)
    if s < (ASK_WAIT_NOTE_S if after_s is None else after_s):
        return None
    return {"s": int(s), "asked": cur.get("asked") or "",
            "note": "Still waiting on your endpoint (%d s on the last question)." % int(s)}


def connect_origin_ok(headers) -> bool:
    """Writes to the agent config and asks come from this machine's own pages.

    Every response carries `Access-Control-Allow-Origin: *` (the glass needs
    it), so any web page the User opens could POST here. A browser always sends
    Origin on a cross-origin POST; only a loopback origin (or none — curl, the
    ear, a test) may change who she talks to or put words in her mouth.
    """
    origin = str(headers.get("Origin") or "").strip()
    if not origin or origin == "null":
        return not origin
    host = origin.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].strip("[]").lower()
    return host in ("127.0.0.1", "localhost", "::1")


_LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")


def _loopback_url(url: str) -> bool:
    """`scheme://host[:port]/...` (an Origin or a Referer) names this machine."""
    hostport = str(url or "").split("://", 1)[-1].split("/", 1)[0]
    return _loopback_hostport(hostport)


def _loopback_hostport(hostport: str) -> bool:
    """`host`, `host:port`, `[::1]` or `[::1]:port` names this machine."""
    h = str(hostport or "").strip().lower()
    if h.startswith("["):
        h = h[1:].split("]", 1)[0]
    elif h.count(":") == 1:
        h = h.rsplit(":", 1)[0]
    return h in _LOOPBACK_NAMES


# EVERY ROUTE IS THIS MACHINE'S, NOT THE WEB'S (ship review 2026-10-07). `Access-Control-Allow-Origin: *` let any
# page the User opened read /v1/state (her asked and answered lines), /v1/agent and /v1/screen, and a form POST drove
# /v1/play, /v1/say, /v1/mouth and the rest; a page that rebinds its own name to 127.0.0.1 is same-origin and sends
# no Origin at all. So a request is refused before any route reads it when its Host is not loopback (rebinding), its
# Origin is not loopback, or the browser marks it cross-site with no loopback Referer (a no-cors GET carries no
# Origin). curl, house-speak, the ear and the glass on :8099 send none of those and pass. On by default in a pack;
# the house keeps today's behaviour until it is flipped there (CRISSA_LOOPBACK_GUARD=1); =0 is the kill.
LOOPBACK_GUARD = os.environ.get("CRISSA_LOOPBACK_GUARD", house_default("0", "1")).strip().lower() not in (
    "0", "false", "no", "off")


def _peer_is_loopback(addr) -> bool:
    """The socket's own peer is this machine (a header can be written by anyone; the peer cannot)."""
    try:
        return str(addr[0]) in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
    except (TypeError, IndexError):
        return False


def _phone_firewall_plan(view=None) -> dict:
    """The firewall keys for the phone port on this computer's MEASURED home network (lib/crissa_phone_firewall.plan,
    lib/crissa_homenet): Allow, and Remove for the rule the key added."""
    try:
        port = int(crissa_ports.port("phone"))
    except Exception:  # noqa: BLE001  no port table: nothing to allow
        return {"offer": False, "why": "no phone port here", "rule": "", "net": "", "dev": "", "where": "", "port": 0,
                "argv": [], "remove": {"offer": False, "rule": "", "argv": [], "where": "", "here": False}}
    return crissa_phone_firewall.plan(port)


def foreign_request(headers) -> str:
    """Why a request did not come from this machine ('' = it did)."""
    host = str(headers.get("Host") or "").strip()
    if host and not _loopback_hostport(host):
        return "host"
    origin = str(headers.get("Origin") or "").strip()
    if origin:
        return "" if origin != "null" and _loopback_url(origin) else "origin"
    if str(headers.get("Sec-Fetch-Site") or "").strip().lower() == "cross-site":
        ref = str(headers.get("Referer") or "").strip()
        return "" if ref and _loopback_url(ref) else "cross-site"
    return ""


# THE WALL PAGE (2026-10-05): display/crissa-wall.html at /wall - the frame, talk, mute, Connect, the
# on-air mark and the status marks, in any browser. OFF by default (CRISSA_WALL_PAGE=1; the launcher,
# scripts/crissa, turns it on). With CRISSA_AUDIO_AT=page as well, the page owns the microphone and
# the speakers: her lines play in the page (lib/crissa_page_audio) and its microphone feeds the voice
# module's turns through /v1/mic. The house never serves either (is_house() reads `host`).
WALL_PAGE = (os.environ.get("CRISSA_WALL_PAGE", "0").strip() == "1"
             and not crissa_page_audio._is_house())
PAGE_AUDIO = crissa_page_audio.Store() if (WALL_PAGE and crissa_page_audio.enabled()) else None
MIC_CHUNK_MAX = 256 * 1024          # one POST of browser microphone PCM (16 kHz int16: ~8 s)


def page_mic_forward(raw: bytes, sr: str) -> tuple[int, dict]:
    """Hand the page's microphone PCM to the voice module (crissa-stt --mic page)."""
    import crissa_egress          # the one switch for what leaves; imported here, never on the house (no page there)
    req = urllib.request.Request(crissa_ports.url("stt") + "/v1/mic", data=raw, method="POST",
                                 headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": sr or "16000"})
    try:
        with crissa_egress.urlopen(req, 2.0, "the voice module") as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            return exc.code, {"ok": False}
    except (OSError, ValueError) as exc:
        return 503, {"ok": False, "error": "the voice module is not listening (%s)" % type(exc).__name__}


def _mic_busy(pause_s: float) -> str | None:
    """Someone spoke in the last `pause_s` (a turn the voice module heard that was not her own)? The
    module not running = nobody to hear = not busy."""
    if not pause_s or pause_s <= 0:
        return None
    try:
        with urllib.request.urlopen(crissa_ports.url("stt") + "/v1/turns?n=8", timeout=0.4) as r:
            rows = json.loads(r.read().decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return None
    rows = rows.get("turns") if isinstance(rows, dict) else rows
    now = time.time()
    for t in rows or []:
        if isinstance(t, dict) and not t.get("her_voice") and str(t.get("text") or "").strip():
            try:
                age = now - float(t.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if 0 <= age < pause_s:
                return "a voice %.0f s ago" % age
    return None


# THE SPEECH LANE (2026-10-06, lib/crissa_speechq): every line goes through one lane. A plain line or the
# first words of an answer still stop what is on the air; the rest of a streamed answer and `queue: true`
# lines wait their turn; /v1/announce lines queue and wait for quiet. Its thread starts on first use, so a
# player that never queues or announces runs exactly as before.
SCREEN = crissa_screen.Screen()
LAYOUT = {"last": ""}  # the last render placement logged, so each change is logged once


def setup_window_again(act: str, body: dict, got: dict) -> None:
    """A whole Setup started again here brings her window forward on /init (lib/crissa_window.setup_again), on a thread.
    A pack only; kill CRISSA_SETUP_AGAIN_WINDOW=0."""
    if crissa_home.is_house() or str(act or "").strip().lower() not in ("start", "restart"):
        return
    import crissa_window
    if not crissa_window.setup_again_window_on():
        return

    def run():
        try:
            did = crissa_window.setup_again("http://127.0.0.1:%d" % PORT, act, body, got)
        except Exception as e:  # noqa: BLE001  her window never costs the walk
            did = "%s: %s" % (type(e).__name__, str(e)[:120])
        if did:
            action("setup", "her window: %s" % did, "setup again")
    threading.Thread(target=run, name="setup-window-again", daemon=True).start()


def display_card(body: dict) -> tuple:
    """THE DISPLAY TOOL's card (lib/crissa_display): the same card a helper posts, logged and laid out the same way."""
    code, out = SCREEN.apply(body)
    if code == 200:
        action("screen", "cleared" if not out["screen"]["up"] else "card up (%s)" % ", ".join(
            k for k in crissa_screen.FIELDS if k in out["screen"]), str(body.get("by") or "display"))
        out["layout"] = layout_now(str(body.get("by") or "display"))
    return code, out


def display_tool(name: str, args: dict) -> str:
    """Run one call of her display tool: the card, then the letter window in her own browser on the right."""
    if name != crissa_display.NAME:
        return "NOT DONE: no tool named %s" % name
    import crissa_window

    def open_sheet(u):
        got = crissa_window.show_sheet("http://127.0.0.1:%d" % PORT, u,
                                       crissa_window.letter_rect(crissa_window.screen_size()))
        if got.get("ok"):
            face_beside_card(True)                   # round 33: her face window comes beside the sheet, in view
        return got

    out = crissa_display.run(args, apply_card=display_card, open_sheet=open_sheet, close_sheet=crissa_window.close_sheet)
    action("display", "%s -> %s" % (str((args or {}).get("what") or "?"), out[:120]), "her tool")
    return out


def layout_now(who: str = "") -> dict:
    """Her render's place for the card state now (lib/crissa_layout.py); a change is logged. No timer."""
    d = crissa_layout.decide(crissa_layout.page_up(SCREEN.view()))    # round 36: a title-only card sends her home
    ln = crissa_layout.line(d)
    if ln != LAYOUT["last"]:
        LAYOUT["last"] = ln
        action("layout", ln, who or "wall")
        if d.get("render") != "on_tile":
            face_beside_card(False)                  # round 33: the card is down, her face window goes back
    return d


# HER ROOM IS EMPTY WHILE SHE IS ON THE PHONE (round 36, the User on .101 2026-10-20: "phone popup did not halt the second
# crissa renders"; lib/crissa_phone_room.py decides). The phone server's session file says on or off; unpaused she walks
# out on her walk-off and the empty office is held, paused it is a cut to the empty office; when the session ends or goes
# idle she walks back in on her entrance and is put on her boot loop. Never during Setup, never on the house or a bench.
# Kill CRISSA_PHONE_EMPTY_ROOM=0.
def _phone_role_key(ph, key: str) -> str:
    """The worn library's own clip for a classic moment key (lib/conductor.MOMENT_ROLES; the User 2026-10-29: "mobile
    flashed to empty room vs walk out transition" - v2 has no crissa-walk-off, so the phone's walk-out fell to a cut)."""
    try:
        return ph.seq.graph.resolve(key)
    except Exception:  # noqa: BLE001
        return key


def _phone_office_key(ph) -> str:
    clips = getattr(ph.lib, "rows", None) or {}
    for k in crissa_init._office_keys():
        k = _phone_role_key(ph, k)
        if (clips.get(k) or {}).get("file"):
            return k
    return ""


def _phone_hold_office(ph, key: str) -> bool:
    INIT_HOLD["key"] = None
    ph.seq.camp = key
    ok = ph.play(key, force=True, audio=False, deliberate=True)
    # the walk-off's end often lands the sequencer ON the office already (her entrance follows it in the graph), and
    # play() of the clip that is current changes nothing: hold it all the same (the cut 5 walk, 2026-10-20)
    ok = ok or ph.key == key
    if ok:
        INIT_HOLD["key"] = key                     # held on its first frame: the empty office
    return ok


def _phone_paused() -> bool:
    """Paused, in a pack: her microphone is off (the voice lock the menu and the Control Panel write; a pack has no
    :8099, so house_paused() never answers here)."""
    try:
        return not bool(crissa_init.mic_state().get("on"))
    except Exception:  # noqa: BLE001
        return False


def _phone_room_loop(home_key: str) -> None:
    room = crissa_phone_room.Room()
    path = crissa_phone_room.session_path()
    leaving = {"key": "", "last": -1}
    while True:
        # while she walks out, 0.1 s: the walk-off's last frames are 125 ms, and camped she may loop it (cut 6's walk)
        time.sleep(0.1 if leaving["key"] else crissa_phone_room.TICK_S)
        ph = STATE.get("playhead")
        if ph is None:
            continue
        try:
            if leaving["key"]:                     # the walk-off ends on the empty office: held before it lands anywhere
                fc = ph.frame_clip or ()
                on_it = len(fc) >= 3 and fc[0] == leaving["key"]
                wrapped = on_it and leaving["last"] >= 0 and int(fc[1]) < leaving["last"]   # it looped: it ended
                if ph.key != leaving["key"] or wrapped or (on_it and fc[2] and fc[1] >= fc[2] - 3):
                    office = _phone_office_key(ph)
                    if office and room.out:
                        _phone_hold_office(ph, office)
                    leaving.update(key="", last=-1)
                elif on_it:
                    leaving["last"] = int(fc[1])
            act = room.step(crissa_phone_room.active(path), _phone_paused(), crissa_init.needed())
            if act is None:
                continue
            office = _phone_office_key(ph)
            action("phone", crissa_phone_room.line(act), "phone")   # the decision, before the walk (it takes ~45 s)
            if act[0] == "out":
                clips = getattr(ph.lib, "rows", None) or {}
                walk_off = _phone_role_key(ph, crissa_phone_room.WALK_OFF)     # the worn library's walk-out
                if act[1] == "walked out" and (clips.get(walk_off) or {}).get("file"):
                    INIT_HOLD["key"] = None
                    ph.seq.camp = walk_off
                    if ph.play(walk_off, force=True, audio=False, deliberate=True):
                        leaving.update(key=walk_off, last=-1)
                    elif office:
                        _phone_hold_office(ph, office)
                elif office:
                    _phone_hold_office(ph, office)
            else:
                leaving.update(key="", last=-1)
                INIT_HOLD["key"] = None
                if office:
                    ph.seq.camp = office
                    ph.play(office, force=True, audio=False, deliberate=True)
                    t0, last_f = time.time(), -1
                    while time.time() - t0 < 45.0:     # her entrance to its end, then her boot loop
                        fc = ph.frame_clip or ()
                        on_it = len(fc) >= 3 and fc[0] == office
                        # round 37 (.101 round 36 B: the walk-in played twice): 0.1 s, and a wrap is its end - never a
                        # second play ("put a no repeat")
                        if ph.key != office or (on_it and last_f >= 0 and int(fc[1]) < last_f) or \
                                (on_it and fc[2] and fc[1] >= fc[2] - 2):
                            break
                        if on_it:
                            last_f = int(fc[1])
                        time.sleep(0.1)
                if home_key:
                    ph.seq.camp = home_key
                    ph.play(home_key, force=True, audio=False, deliberate=True)
        except Exception as e:  # noqa: BLE001  the phone never costs the player
            print("[conductor] phone room: %s: %s" % (type(e).__name__, e), flush=True)
            time.sleep(5.0)


def face_beside_card(card_up: bool) -> None:
    """Her face window (Face only / the Wall off) beside the card while it is up (lib/crissa_face.beside_card), on a
    thread: the browser's calls never hold a route. A pack only; kill CRISSA_FACE_BESIDE_CARD=0."""
    if crissa_home.is_house() or not crissa_face.beside_card_on():
        return

    def run():
        try:
            lay = crissa_layout.decide(bool(SCREEN.view().get("up"))) if card_up else None
            did = crissa_face.beside_card("http://127.0.0.1:%d" % PORT, card_up, lay)
            if did not in ("not moved", "no face window", "off"):
                action("face window", did, "layout")
        except Exception as e:  # noqa: BLE001  her window's place never costs the card
            action("face window", "%s: %s" % (type(e).__name__, str(e)[:120]), "layout")
    threading.Thread(target=run, name="face-beside-card", daemon=True).start()
LANE = crissa_speechq.Lane(lambda text, who: _spawn_speak(text, who), say_stop_all, say_pending,
                           comms_open=lambda: bool(getattr(STATE.get("seq"), "comms", False)),
                           mic_busy=_mic_busy, home=crissa_home.HOME, log=lambda *a: action(*a))
CUTS: dict = {"last": None}          # the last cut line: shown on the glass with the whole text a click away


# /v1/ft-desk left this list 2026-10-16 (.101 round 22: the bridge's move to the multitasking view, on the owner's own
# request with his face OK, got 403 because ID-only presence never sets user_auth): it changes only which view she
# is drawn in and reads or keeps nothing.
_GUEST_COMMANDS = {"/v1/import", "/v1/import/upload", "/v1/library", "/v1/library/sync",
                   "/v1/shelve", "/v1/freelance",
                   "/v1/skin/stage", "/v1/skin/answer"}    # installing a skin from the Store is the owner's (2026-10-31)


def _guest_refused() -> bool:
    """True when presence does not confirm the owner. No address is not a guest. Unread is."""
    import crissa_presence
    return crissa_presence.commands_blocked()


def note_cut(note: dict | None, what: str) -> None:
    if note:
        # the glass gets the numbers, the words and the file NAME (/v1/cut/<name>), never a path on this machine
        CUTS["last"] = {"what": what, "kept": note.get("kept"), "total": note.get("total"), "at": note.get("at"),
                        "note": "Cut at %s of %s characters." % (note.get("kept"), note.get("total")),
                        "name": Path(note.get("full_text") or "").name}
        action("cut", "%s: %s" % (what, note.get("note")), "talk")


def connect_ask(text: str, who: str, by: str, check_s: float = 0.0, speak: bool = True) -> dict:
    """One turn: ask the connected agent, speak the reply as `who`. Never raises.

    ONE CONVERSATION (the User 2026-10-18, the phone and the Wall): speak=False asks through the same Conversation
    (its history and session id) and returns the words without saying them here - the phone says them itself.

    STREAMED (2026-10-06): sentences are spoken as they arrive (talk.reply.stream_sentences): the first stops
    what is on the air, the rest queue behind it. A frame that is not her words (the agent is working) says
    a short filler at most once per working_every_s while nothing has been said. reply.max_chars, when set,
    stops speaking at the limit and says so on the glass with the whole text a click away."""
    t0 = time.time()
    # Inlined: the spoke tests lift this function alone, so a module helper is not in scope.
    import crissa_presence
    guest = crissa_presence.commands_blocked()
    cfg = crissa_presence.guest_cfg(crissa_agent.load())
    guest_note = crissa_presence.GUEST_NOTE if guest else None
    talk = crissa_talk.load()
    rcfg = talk["reply"]
    limit = int(float(rcfg.get("max_chars") or 0))
    stream = bool(rcfg.get("stream_sentences", True))
    rec = {"asked": text[:200], "by": by, "at": t0, "reply": None, "error": None, "ms": None}
    turn = {"n": 0, "chars": 0, "first": None, "filler_at": 0.0, "capped": False}
    split = crissa_speechq.Sentences(int(rcfg.get("min_sentence_chars") or 12))
    hush0 = getattr(LANE, "hush", 0)

    def emit(sentence: str, filler: bool = False) -> None:
        line = crissa_agent.speakable(sentence)
        if not speak or not line or turn["capped"] or getattr(LANE, "hush", 0) != hush0:   # stopped mid-reply: no more
            return
        if limit and not filler and turn["chars"] + len(line) > limit:
            turn["capped"] = True
            return
        proc = LANE.say(line, who, queue=turn["n"] > 0)
        if turn["first"] is None and proc is not None:
            turn["first"] = proc
        turn["n"] += 1
        if not filler:
            turn["chars"] += len(line) + 1

    def on_piece(piece: str) -> None:
        for sentence in split.feed(piece):
            emit(sentence)

    def on_event(name: str, data: str) -> None:
        words = [w for w in (rcfg.get("working_filler") or []) if str(w).strip()]
        every = float(rcfg.get("working_every_s") or 0)
        now = time.time()
        if not words or turn["chars"] or (turn["filler_at"] and (every <= 0 or now - turn["filler_at"] < every)):
            return
        turn["filler_at"] = now
        emit(words[int(now) % len(words)], filler=True)

    if speak:
        LANE.begin_turn()
    try:
        with CONNECT["lock"]:
            CONNECT["asking"] = {"since": time.time(), "asked": text[:80]}
            try:
                # HER DISPLAY TOOL (2026-10-16, lib/crissa_display): an owner's turn only, a pack only; inlined like
                # crissa_presence above (the spoke tests lift this function alone)
                import crissa_display as _zd
                tool_kw = ({"tools": _zd.tools(), "on_tool": globals().get("display_tool")}
                           if (not guest and _zd.tools() and globals().get("display_tool")) else {})
                reply = CONNECT["conv"].ask(text, cfg, on_piece=on_piece if stream else None, on_event=on_event,
                                            remember=not guest, system_extra=guest_note, **tool_kw)
            finally:
                CONNECT["asking"] = None
        line = crissa_agent.speakable(reply)
        rec["reply"] = line
        if line:
            rest = split.flush()
            if turn["chars"] == 0:          # not streamed (plain JSON, or streaming off): the whole line
                emit(line)
            elif rest:
                emit(rest)
            if turn["capped"] or (limit and len(line) > limit):
                _kept, note = crissa_talk.keep(line, limit, "reply")
                note_cut(note, "reply")
                rec["cut"] = note
            proc = turn["first"]
            action("ask", "%r → %r as %s (%d line%s)" % (text[:60], line[:60], who, turn["n"],
                                                          "" if turn["n"] == 1 else "s"), by)
            if check_s > 0 and proc is not None:  # no check ran = nothing known: leave `spoke` out (2026-10-04)
                failed = _speak_failed_fast(proc, check_s)
                rec["spoke"] = not failed
                if failed:
                    rec["error"] = failed
        else:
            rec["error"] = "the agent answered with no text"
    except crissa_agent.AgentError as exc:
        rec["error"] = str(exc)             # the plain headline; the raw reason goes to the log (2026-10-07)
        action("ask", "failed: %s [%s]" % (exc, getattr(exc, "detail", "")), by)
    except Exception as exc:  # noqa: BLE001  a turn must never take the render loop with it
        rec["error"] = "ask failed: %s" % type(exc).__name__
        action("ask", "failed: %r" % exc, by)
    finally:
        if speak:
            LANE.end_turn()
    rec["ms"] = round((time.time() - t0) * 1000)
    CONNECT["last"] = rec
    return rec


def _reap_children(*_a) -> None:
    """SIGCHLD: reap only the children we know about (2026-09-15).

    This used to be `os.waitpid(-1, WNOHANG)` in a loop, which raced every
    watcher's own `Popen.wait()`. When the handler won, Popen's wait got
    ECHILD and Python fills in returncode 0 — so a fitter that CRASHED read
    as a clean exit, and the repin watcher dropped the caches and reported
    success on a broken track. Popen.poll() reaps through the Popen object
    itself, so the real status always reaches whoever is waiting.
    """
    for bag in (_SPEAKERS,):
        for proc in list(bag):
            try:
                if proc.poll() is not None:
                    bag.remove(proc)
            except (OSError, ValueError):
                pass
    for slot in (REPIN, AUTOTUNE, DRILL):
        proc = slot.get("proc")
        if proc is not None:
            try:
                proc.poll()
            except OSError:
                pass
POSE_DIR = Path(os.environ.get("HOUSE_POSE_DIR", str(ROOT / "display" / "crissa-poses")))
# PER-FACE MOUTH BANKS (the User 2026-09-12, Init routine): a clip of another
# face can roll on THIS conductor (Lila's register listen loop lives in
# Crissa's library, rack host-lila, key lila-*), and live speech over it must
# wear HER tiles. A bank is another library; a tile rel or a clip key whose
# basename starts with "<face>-" resolves into that library for its tiles,
# tile heads (map.json) and, when the clip has no playlist of its own, the
# bank's playlist for the same hub side. Crissa's own path is untouched: a
# rel that names no face resolves in POSE_DIR as before.
# HOUSE_FACE_BANKS="lila=display/lila-poses,bash=display/bash-poses".
FACE_BANKS: dict[str, Path] = {}
for _part in os.environ.get("HOUSE_FACE_BANKS", house_default("lila=display/lila-poses", "")).split(","):
    _n, _, _d = _part.strip().partition("=")
    if _n and _d:
        _p = Path(_d) if Path(_d).is_absolute() else ROOT / _d
        if _p.is_dir() and _p.resolve() != POSE_DIR.resolve():
            FACE_BANKS[_n.strip().lower()] = _p
# LILA'S LIPS IN A PACK'S SETUP (round 18, .101 round 17: "lila on screen no lips moving"; lib/crissa_init LILA'S LIPS ON
# HER LINES): a pack names no face bank, so her host loop (lila-reg-listen, no playlist of its own) had no tiles. A pack
# that ships her bank names it here for her tiles, her bank's playlist and her own dials ONLY: her rows are never merged
# into the library (merge_face_bank_clips skips a LIPS_ONLY bank), so nothing of hers reaches the graph, the build's
# decoder or a drill. A named HOUSE_FACE_BANKS wins. Kill: CRISSA_LILA_LIPS=0.
LIPS_ONLY_BANKS: set = set()
# HER TURN FILM WEARS HER LIPS (round 21, the User 2026-10-14: "Lila's 'turn your head' clip fits there"). A lips-only
# bank lends its playlist to her LOOPS only (a film of hers keeps its own talking mouth); the films named here are
# played muted under a line she says live (Voice ID's first turn, voice_register.turn_film_under_cue), so they borrow
# it too, and their turn frames take her profile tiles from <stem>.mouth.turnfill.json. Empty = round 20 (none).
LIPS_BORROW_FILMS = {k.strip() for k in os.environ.get("CRISSA_LILA_PAINT_FILMS", "lila-reg-turn").split(",") if k.strip()}
_LILA_BANK = ROOT / "display" / "lila-poses"
if (not IS_HOUSE and "HOUSE_FACE_BANKS" not in os.environ and "lila" not in FACE_BANKS
        and os.environ.get("CRISSA_LILA_LIPS", "1").strip().lower() not in ("0", "off", "false", "no")
        and (_LILA_BANK / "inventory.json").is_file() and _LILA_BANK.resolve() != POSE_DIR.resolve()):
    FACE_BANKS["lila"] = _LILA_BANK
    LIPS_ONLY_BANKS.add("lila")


def face_of(name: str | None) -> str | None:
    """The face a tile rel / clip key belongs to, by basename prefix."""
    base = Path(str(name or "")).name.lower()
    for f in FACE_BANKS:
        if base.startswith(f + "-"):
            return f
    return None


def bank_dir(name: str | None) -> Path:
    f = face_of(name)
    return FACE_BANKS[f] if f else POSE_DIR   # HOUSE_POSE_DIR: a second face is a second library (Lila, 2026-09-11)


def film_path(row: dict | None, key: str = "") -> Path | None:
    """Crissa POSE_DIR first, then FACE_BANKS (Lila harvests on :8100)."""
    fname = str((row or {}).get("file") or "")
    if not fname:
        return None
    p = POSE_DIR / fname
    if p.is_file():
        return p
    face = face_of(key)
    if face in FACE_BANKS:
        q = FACE_BANKS[face] / fname
        if q.is_file():
            return q
    for bank in FACE_BANKS.values():
        q = bank / fname
        if q.is_file():
            return q
    return None


def sidecar_path(row: dict | None, key: str = "") -> Path | None:
    """Mouth track next to the film. Same bank walk as film_path.

    Library.track used to read POSE_DIR only, so Lila on :8100 (Render host)
    kept Crissa's static inventory box — the moving sidecar lives in her bank
    (the User 2026-09-16: mouth box did not move).
    """
    fname = str((row or {}).get("mouth_sidecar") or "")
    if not fname:
        f = str((row or {}).get("file") or "")
        fname = f[:-4] + ".mouth.json" if f.endswith(".mp4") else ""
    if not fname:
        return None
    if MOUTH_TURN_FILL and fname.endswith(".mouth.json"):
        fill = fname[:-len(".mouth.json")] + ".mouth.turnfill.json"
        for bank in [POSE_DIR] + list(FACE_BANKS.values()):
            if (bank / fill).is_file():
                return bank / fill
    if MOUTH_TURN_FIX and fname.endswith(".mouth.json"):
        turn = fname[:-len(".mouth.json")] + ".mouth.turn.json"
        for bank in [POSE_DIR] + list(FACE_BANKS.values()):
            if (bank / turn).is_file():
                return bank / turn
    p = POSE_DIR / fname
    if p.is_file():
        return p
    face = face_of(key)
    if face in FACE_BANKS:
        q = FACE_BANKS[face] / fname
        if q.is_file():
            return q
    for bank in FACE_BANKS.values():
        q = bank / fname
        if q.is_file():
            return q
    return None


def merge_face_bank_clips(clips: dict) -> dict:
    """Guest rows from Lila (etc.) so drill/autotune can find their films. A lips-only bank (a pack's Lila) adds none."""
    out = dict(clips)
    for bank in [b for f, b in FACE_BANKS.items() if f not in LIPS_ONLY_BANKS]:
        try:
            extra = json.loads((bank / "inventory.json").read_text()).get("clips") or {}
        except (OSError, ValueError):
            continue
        if not isinstance(extra, dict):
            continue
        for k, row in extra.items():
            if k not in out and isinstance(row, dict) and row.get("file"):
                out[str(k)] = row
    return out
# Mouth stills / GIFs / 3-up series. A folder under here with index.html is
# a Composer page at /sheet/<name>/. Do not leave the User hunting this tree.
SHEET_ROOT = POSE_DIR / "visemes" / "picks"
SHEET_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".gif": "image/gif",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


def sheet_path(rel: str) -> Path | None:
    """A file under SHEET_ROOT, or None. No parent traversal, no other trees."""
    rel = unquote(rel or "").replace("\\", "/").strip("/")
    if not rel:
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    root = SHEET_ROOT.resolve()
    path = (SHEET_ROOT.joinpath(*parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _load_freelance():
    """The nightly mint's store, shared with scripts/pose-freelance.py. One
    file, two readers: the cron files the cards off it and the Composer shows
    and edits the same list. A second copy of the queue is how the board and
    the glass would start disagreeing about what gets built tonight."""
    import importlib.util
    path = ROOT / "scripts" / "pose-freelance.py"
    try:
        spec = importlib.util.spec_from_file_location("pose_freelance", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # noqa: BLE001 - the wall must not die for the mint queue
        return None


FREELANCE = _load_freelance()
# This machine's home server (the glass's mailbox), from config/house-machine.json (2026-10-06); "" in a pack.
WALL = crissa_home.house_value("panel_url")

# THE BENCH ROLE (the User 2026-09-15: "ideally one conductor"). One conductor is
# the wall (:8100 — the Tk wallpaper and Brave pull its /frame.jpg|.mjpg);
# every other one is a BENCH: a tuning stage for the Composer that is never
# on the glass.
#
# Two processes are unavoidable. The mouth dials are module globals
# (apply_dials / MOUTH_WARP / _hf_mod.SAG_K) booted once from
# POSE_DIR/mouth-dials.json, so one process cannot hold the wall's dials and
# a trial set at the same time without rebuilding this file.
#
# A bench differs from the wall in exactly three ways:
#   * it runs no house threads — no tour, no brain, no door poll, no
#     user-talk, no visit lamp, and it never rewrites the render host;
#   * it owns its own mouth mailbox, so a Say on the Composer is posted to
#     the bench and the wall's store never sees the sheet (the audio still
#     goes to speak_sink — the User hears the line);
#   * with nobody reading its /frame.mjpg it drops to IDLE_FPS instead of
#     painting 60 frames a second for an empty room.
# HOUSE_CONDUCTOR_ROLE unset = wall, exactly as before.
ROLE = (os.environ.get("HOUSE_CONDUCTOR_ROLE") or "wall").strip().lower()
IS_BENCH = ROLE == "bench"

# ---- THE AUTHORING GATE (Crissa Stage 1, 2026-09-22) -----------------------
# This one file is both the shipping player and the authoring bench. Gate, do
# not fork: ~287 of its lines touch authoring state and every authoring import
# is already lazy (pose_intake, mouth_drill, clip_words), so a fork would
# diverge within weeks while a flag costs one branch.
#
# NOT `IS_BENCH and ...`. That was the first design and it is wrong twice over.
# IS_BENCH gates no route today — every one of these is live on the wall — so it
# would be a behaviour change dressed as a no-op; and the Composer POSTS THREE
# THINGS TO THE WALL (/v1/shelve, /v1/player-snd, /v1/mouth-tune {"default":
# true}, pinned by tests/test_composer_bench_routing.py), which it would 404.
# Default ON is byte-for-byte today on both glasses; the shipped core sets 0.
# A pack defaults OFF (ship review 2026-10-07): a pack conductor started by anything but scripts/crissa.py opened
# /v1/say to every page; the house keeps ON. An env value still wins.
AUTHORING = os.environ.get("CRISSA_AUTHORING", house_default("1", "0")).strip().lower() not in (
    "0", "false", "no", "off")

# Authoring POSTs. Deliberately NOT here: /v1/play, /v1/cue, /v1/mouth,
# /v1/state, /v1/kit, /v1/home, /v1/reload, /v1/tour, /v1/comms, /v1/register,
# /v1/vocative, /v1/ft-desk, /v1/brain, /v1/queue — the player needs them.
AUTHORING_POST = frozenset({
    "/v1/drill", "/v1/autotune", "/v1/repin", "/v1/shelve",
    "/v1/combos", "/v1/freelance", "/v1/mouth-presets", "/v1/shifts",
    "/v1/abort-hub", "/v1/say", "/v1/mouth-tune", "/v1/transport", "/v1/scrub", "/v1/mouth-pins",
})
# Authoring GETs, by prefix: the sheet inspector and the old Composer redirects.
AUTHORING_GET = ("/sheet", "/composer")
# NAMED CONSTANTS, so the comparisons below do not spell out the route literals
# in the `head ==` form. Eight tests reach the real /v1/say and /v1/mouth-tune
# handlers by slicing this file on that exact phrase, and a second copy of it
# ABOVE the handler silently hands them the wrong block — which is what the
# first version of this gate did, and then what the comment EXPLAINING it did,
# by quoting the phrase it was warning about. Source-slicing tests make string
# literals load-bearing; same family as "pins must not match prose".
_TUNE_ROUTE, _SAY_ROUTE = "/v1/mouth-tune", "/v1/say"
# A pack's Init (lib/crissa_init): served here only when crissa_init.serves() - a pack with CRISSA_INIT=1. The
# house's Init stays on its home server; this conductor answers the route there exactly as before (404).
_INIT_ROUTE = "/v1/init"
# THE TRANSPORT ROUTE (2026-09-25): every verb but the hard stop is authoring.
_TRANSPORT_ROUTE = "/v1/transport"


def mouth_pins_where(ph) -> tuple:
    """(cell, yaw) on screen: the cell the painter resolved last, the frame's own head yaw."""
    if ph is None:
        return None, None
    cell = getattr(ph, "_pl_cell", None)
    yaw = None
    try:
        rec = ph.lib.head(ph.key, int(ph.i)) or {}
        yaw = float((rec.get("pose") or [None, None])[1])
    except (TypeError, ValueError, AttributeError):
        yaw = None
    return cell, yaw


def mouth_pins_state(ph) -> dict:
    cell, yaw = mouth_pins_where(ph)
    return {"ok": True, "on": MOUTH_PIN_CELLS, "turn_place": MOUTH_TURN_PLACE,
            "cell": cell, "yaw": None if yaw is None else round(yaw, 2),
            "pins": MP.load(POSE_DIR), "file": str(POSE_DIR / MP.PINS_NAME),
            "lip_place": getattr(ph, "_lip_dbg", None) if ph is not None else None}


def mouth_pins_post(ph, body: dict, who: str) -> tuple[int, dict]:
    cell0, yaw0 = mouth_pins_where(ph)
    cell = str(body.get("cell") or cell0 or "")
    yaw = body.get("yaw", yaw0)
    if not cell:
        return 409, {"ok": False, "error": "no cell on screen: hold a class or name {cell}"}
    try:
        yaw = None if yaw is None else float(yaw)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "yaw must be a number"}
    if body.get("drop"):
        got = MP.drop_row(POSE_DIR, cell, None if body.get("all") else yaw)
        action("mouth-pins", "drop %s @%s" % (cell, yaw), who)
        return 200, {"ok": True, "cell": cell, "rows": got.get("rows") or []}
    if yaw is None:
        return 409, {"ok": False, "error": "no yaw on this frame (no head) - name {yaw}"}
    try:
        u = max(-40.0, min(40.0, float(body.get("u", body.get("nose_u")))))
        v = max(-40.0, min(40.0, float(body.get("v", body.get("nose_v")))))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "u and v are numbers (nose_u / nose_v units)"}
    got = MP.save_row(POSE_DIR, cell, yaw, u, v, by=who)
    action("mouth-pins", "%s @%.1f = %.1f,%.1f" % (cell, yaw, u, v), who)
    return 200, {"ok": True, "cell": cell, "yaw": round(yaw, 2), "rows": got["rows"]}


def authoring_refused(head: str, body: dict | None = None) -> bool:
    """True when this request is authoring and authoring is off.

    Two routes are NOT all-or-nothing, and both would fail silently:

    /v1/mouth-tune with an EMPTY body is the SPEECH PATH. Every house-speak
    line POSTs b"{}" to it to read sheet_lead_effective_ms (_ask_wall_lead) —
    that is CLAUDE.md's "one sheet lead for every speaker". Gating the route
    would drop every line to LEAD_FALLBACK_MS with no error at all, because the
    caller catches by design ("a conductor that is down must not cost her the
    line"). The READ is never gated; the WRITE is.

    /v1/say {"stop": true} is say_stop_all() — the hard stop behind the User's
    "when I stop it it plays twenty more times". A core with no way to stop a
    running line is worse than one that cannot start one.
    """
    if AUTHORING:
        return False
    if head == _TUNE_ROUTE:
        return bool(body)          # empty body = the read
    if head == _SAY_ROUTE:
        return not (body or {}).get("stop")
    if head == _TRANSPORT_ROUTE:
        # stop_all is the same hard stop as /v1/say {stop}; every other verb is the bench's
        return str((body or {}).get("verb") or "") != "stop_all"
    return head in AUTHORING_POST


def _make_brain():
    """The clip picker, or None when this edition has no brain.

    The startup split (2026-09-22): the public player boots with the bag and no
    brain, which is what HOUSE_CONDUCTOR_BRAIN=0 has always been. Imported here,
    not at module level, so a tree without conductor_brain starts; on the house
    the module is present and this is Brain() exactly as before.
    """
    try:
        from conductor_brain import Brain
    except ImportError:
        print("[conductor] no brain in this edition — the bag picks", flush=True)
        return None
    return Brain()


# Kill: HOUSE_MOUTH_CACHE_DROP_ON_LEAVE=0 keeps a bench's left clips in the LRU (as before 2026-09-18).
CACHE_DROP_ON_LEAVE = os.environ.get("HOUSE_MOUTH_CACHE_DROP_ON_LEAVE", "1").strip().lower() not in ("0", "false", "no", "off")
# Where the viseme sheet lives for THIS process: the wall reads :8099's
# store, a bench reads its own. house-speak writes whichever it is handed in
# VO_MOUTH_URL, and _spawn_speak / drill_spawn hand it this one.
# A PACK IS ITS OWN MAILBOX (2026-09-28, the public loops pack): a launched player has no :8099 glass
# server behind it, so in wall role it keeps the sheet itself, the way a bench does. Default OFF - the
# house wall reads :8099 exactly as before; the pack's launch line sets HOUSE_CONDUCTOR_OWN_MAILBOX=1.
OWN_MAILBOX = os.environ.get("HOUSE_CONDUCTOR_OWN_MAILBOX", "0").strip().lower() in ("1", "true", "yes", "on")
MOUTH_URL = os.environ.get("HOUSE_CONDUCTOR_MOUTH_URL") or (
    ("http://127.0.0.1:%d/v1/mouth" % PORT) if (IS_BENCH or OWN_MAILBOX or not WALL) else (WALL + "/v1/mouth"))
def _early_float(name: str, default: float) -> float:
    """_env_float lives further down the file with the dials; these two are
    read before it exists."""
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return float(default)


IDLE_FPS = max(0.5, min(30.0, _early_float("HOUSE_CONDUCTOR_IDLE_FPS", 2.0)))
IDLE_AFTER_S = max(1.0, min(600.0, _early_float("HOUSE_CONDUCTOR_IDLE_AFTER_S", 10.0)))
MAILBOX = mouth_mailbox.MouthMailbox() if (IS_BENCH or OWN_MAILBOX) else None
# Kill switch for the Standard-mode boot-rest guard in main() (2026-09-15).
BOOT_CRISSA_REST = (os.environ.get("HOUSE_BOOT_CRISSA_REST", "1").strip().lower()
                  not in ("0", "false", "no", "off"))
# The nudge applies on triad frames, which land no pixel pin (2026-09-15).
TRIAD_NUDGE = (os.environ.get("HOUSE_MOUTH_TRIAD_NUDGE", "1").strip().lower()
               not in ("0", "false", "no", "off"))
# When a bench was last LOOKED AT: every /frame.jpg and every frame written
# down a /frame.mjpg stamps it. With nobody watching, run() paints at
# IDLE_FPS — health's frame_no still climbs, so the janitor sees a live
# unit, and the first reader brings it straight back up to RENDER_FPS.
# The wall never idles: it IS the glass, whether or not anybody is in the room.
_LOOK: dict = {"at": 0.0}
# A PACK'S PLAYER RESTS WHEN NOBODY WATCHES (the User 2026-10-10, .101 idle load: the conductor at 116 % with the desk
# Wall closed and no page open): outside the house the player's frames have readers only while a Wall shows them (the
# desk Wall and the browser Wall both read /frame.jpg or /frame.mjpg, which stamp looked_at()), so with none for
# IDLE_AFTER_S it paints at IDLE_FPS like an unwatched bench, and the first reader brings it back at once. The house's
# wall never idles (it IS the glass). Kill: HOUSE_CONDUCTOR_PACK_IDLE=0.
PACK_IDLE = (not crissa_home.is_house()
             and os.environ.get("HOUSE_CONDUCTOR_PACK_IDLE", "1").strip().lower() not in ("0", "false", "no", "off"))
# A PACK'S PLAYER PAINTS ONLY DISTINCT FRAMES WHILE NOTHING IS ON AIR (the User 2026-10-10, .101 round 4 AA: the player
# at 110 % idle). RENDER_FPS (60) is for the mouth: a viseme hop lands between clip frames only while she speaks. With
# no voice, no camera cut, no held class and no drill, a tick on the same clip frame composites and encodes the same
# picture again (the nose EMA is keyed by frame index, so a repaint changes nothing), so the tick keeps the last JPEG and
# advances the film only: 24 paints a second instead of 60, the film's own rate. A repaint at least every
# DISTINCT_REFRESH_S keeps a dial change or a reloaded tile from waiting on the next frame. The house paints every tick
# as before. Kill: HOUSE_CONDUCTOR_DISTINCT_ONLY=0.
DISTINCT_ONLY = (not crissa_home.is_house()
                 and os.environ.get("HOUSE_CONDUCTOR_DISTINCT_ONLY", "1").strip().lower() not in ("0", "false", "no", "off"))
DISTINCT_REFRESH_S = 0.5


def rest_read(path: str) -> bool:
    """A frame read that says it is resting (`?rest=1`, the desk Wall while nobody watches it, lib/house_wall_ascii
    WALL_REST): it takes a picture without counting as someone looking, so the player can rest the house's way."""
    q = path.split("?", 1)[1] if "?" in path else ""
    return any(part in ("rest=1", "rest=true") for part in q.split("&"))


def may_reuse(painted: tuple | None, key, i, *, air: bool, now: float, at: float,
              refresh_s: float = DISTINCT_REFRESH_S, on: bool | None = None) -> bool:
    """True when this tick may keep the last JPEG: nothing on air and the same clip frame painted under refresh_s ago."""
    if not (DISTINCT_ONLY if on is None else on) or air or painted is None:
        return False
    return painted == (key, i) and (now - at) < refresh_s


def looked_at() -> None:
    _LOOK["at"] = time.time()


# CLOSE AGENT HOLDS HER STILL (round 16, .101 00:48: "renders did not close and are cycling at .2fps or so" - while her
# agent was closed the player went on dealing her films, flourish-bun-wobble-think-work among them, and the resting
# Wall pasted each new picture): in a pack, while her agent is closed (lib/crissa_watch.agent_closed, read at most once
# a second), the playhead holds the frame it is on - no frame advance, no bag deal, no flourish, no queued play taken -
# and paints at IDLE_FPS. Open Agent lets it go on from that frame. Never on the house. Kill:
# HOUSE_CONDUCTOR_AGENT_STILL=0.
AGENT_STILL_ON = (not crissa_home.is_house()
                  and os.environ.get("HOUSE_CONDUCTOR_AGENT_STILL", "1").strip().lower() not in ("0", "false", "no", "off"))
_AGENT_STILL: dict = {"at": 0.0, "on": False}


def agent_still(now: float | None = None, read=None) -> bool:
    """Her agent is closed here, so the picture holds (AGENT_STILL_ON). `read`: tests."""
    if not AGENT_STILL_ON and read is None:
        return False
    now = time.time() if now is None else now
    if now - _AGENT_STILL["at"] >= 1.0:
        _AGENT_STILL["at"] = now
        try:
            if read is not None:
                on = bool(read())
            else:
                import crissa_watch
                on = bool(crissa_watch.agent_closed(crissa_home.HOME))
        except Exception:  # noqa: BLE001  unreadable: she plays as before
            on = False
        if on != _AGENT_STILL["on"]:
            print("[agent] %s" % ("closed: the picture holds this frame until Open Agent" if on
                                  else "open: the picture moves again"), flush=True)
        _AGENT_STILL["on"] = on
    return _AGENT_STILL["on"]


def look_idle() -> bool:
    # A DRILL OR AN AUTOTUNER PASS IS A READER (2026-09-16): measured, an
    # unwatched bench painted 15 frames of a 241-frame pass at 2 fps, so the
    # tuner's passes read "not a measurement" and the drill's lag was noise
    # whenever the Composer tab was hidden. The film's own voice is the clock;
    # the paint must keep up with it whether or not anyone is looking.
    try:
        if DRILL.get("on"):
            return False
        _p = AUTOTUNE.get("proc")
        if _p is not None and _p.poll() is None:
            return False
    except NameError:
        pass
    return (IS_BENCH or PACK_IDLE) and (time.time() - _LOOK["at"]) > IDLE_AFTER_S
def _house_url(key: str, path: str) -> str:
    """This machine's own service + path (config/house-machine.json, 2026-10-06), only on the house."""
    base = crissa_home.house_value(key)
    return house_default(base.rstrip("/") + path if base else "", "")


LAMP_URL = os.environ.get("RAIL_VISIT_LAMP", _house_url("presence_url", "/v1/visit_lamp"))
# A KIT READS ITS OWN SECURE LAMP (the User 2026-10-29: "Be sure Conductor gets Secure flag so it knows which
# transitions/flourishes"): the address above is the house's alone, so a pack's player never saw sage and its casual
# takes (v2's casual walk-in / walk-out) could never play. The kit's presence serves the same /v1/visit_lamp on its
# own port. Kill CRISSA_SECURE_LAMP=0.
if not LAMP_URL and not crissa_home.is_house() and os.environ.get("CRISSA_SECURE_LAMP", "1").strip().lower() not in (
        "0", "off", "false", "no"):
    try:
        LAMP_URL = crissa_ports.url("presence") + "/v1/visit_lamp"
    except Exception:  # noqa: BLE001  no port table: no lamp, as before
        LAMP_URL = ""
PRESENCE_URL = os.environ.get("RAIL_PRESENCE", _house_url("presence_url", "/v1/presence"))
HOUSE_URL = os.environ.get("RAIL_HOUSE", _house_url("panel_url", "/v1/house"))
# THE KIT HOLDS STILL WHILE YOU TALK (the User 2026-10-14): a pack has no :8099, so HOUSE_URL is empty and the
# conversation hold never saw a conversation - she started flourishes while he talked. In a pack the ear bridge's own
# /health answers instead: `paused` false = a session is open (listening, thinking or speaking), the meaning :8099's
# hear.paused carries on the house. Read only where the hold is armed (on by default in a pack); RAIL_HOUSE still
# wins, and house_paused() stays the house's alone. Kill HOUSE_CONV_FROM_BRIDGE=0 (no conversation in a pack, as before).
# The bridge's address is the one the launcher hands this child (VO_HEAR_HEALTH_URL, from the address keys in
# lib/crissa_ports), never the port table's default: a player started without a launcher asked the default bridge
# port, the house's own bridge (the round-19 cut's acceptance, 286 calls on a per-face boot).
CONV_FROM_BRIDGE = os.environ.get("HOUSE_CONV_FROM_BRIDGE", "1").strip().lower() not in ("0", "false", "no", "off")
# A camp on her BOOT LOOP lifts at a quiet wrap (the User 2026-10-27; the wrap branch in Playhead._advance says why).
BOOT_CAMP_LIFT = os.environ.get("CRISSA_BOOT_CAMP_LIFT", "1").strip().lower() not in ("0", "false", "no", "off")
_BOOT_CAMP: dict = {"key": None}
CONV_URL = HOUSE_URL or ((os.environ.get("VO_HEAR_HEALTH_URL") or "").strip()
                         if (CONV_HOLD and CONV_FROM_BRIDGE and not IS_HOUSE) else "")
FPS = CLIP_FPS
RENDER_FPS = mouth_render_fps()


def _http_json_get(url: str, timeout: float = 1.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None
CLIP_W, CLIP_H = 736, 400


def paint_wh() -> tuple[int, int]:
    """The painter's (w, h): every paint, decode and mask in this process reads it, never CLIP_W/CLIP_H (the User
    2026-10-15, Crissa phone S0.1). OFF = (CLIP_W, CLIP_H) verbatim; HOUSE_FRAME_FROM_LIBRARY=1 = this library's own
    size (head_frame.frame_wh: inventory `size`, else the manifest `frame`). One process paints one size: a bench on
    a library of another size runs with HOUSE_FACE_BANKS= (no guest bank) and feeds no glass."""
    if not frame_from_library():
        return (CLIP_W, CLIP_H)
    return frame_wh(POSE_DIR)


JPEG_Q = 86
# EACH FRAME NAMES ITS MOUTH (2026-10-11, the .101 lips): /frame.jpg and every /frame.mjpg part carry X-Mouth-Box
# "x,y,w,h", the film frame's own mouth box (Library.box: the sidecar's, fixed per film frame whatever is painted),
# so the desk Wall can read her exposure without her mouth (lib/house_wall_ascii_render.norm_cells) and a mouth step
# stays a region. Metadata only: no pixel here changes. No box (a camera cut, a frame with no mouth in shot): no
# header. Kill: HOUSE_CONDUCTOR_MOUTH_BOX=0.
MOUTH_BOX_HEADER = os.environ.get("HOUSE_CONDUCTOR_MOUTH_BOX", "1").strip().lower() not in ("0", "false", "no", "off")


def mouth_box_header(box) -> bytes:
    """b"X-Mouth-Box: x,y,w,h\r\n" for a box, b"" for none."""
    if not MOUTH_BOX_HEADER or not box:
        return b""
    try:
        x, y, w, h = (float(v) for v in box)
    except (TypeError, ValueError):
        return b""
    if not (w > 0 and h > 0):
        return b""
    return ("X-Mouth-Box: %.1f,%.1f,%.1f,%.1f\r\n" % (x, y, w, h)).encode("ascii")

# EACH FRAME NAMES ITS CLIP (round 13, 2026-10-12, the desk Wall's cache by clip): every /frame.mjpg part and /frame.jpg
# carry X-Clip "<key>;<frame>;<frames>;<role>" for a clip frame (none for a camera cut), so the desk Wall's ASCII cache
# can keep a clip's pictures on disk, find a speaking frame's own idle picture by its clip frame, and keep the loops she
# returns to in memory first (lib/wall_ascii_disk.py). Metadata only: no pixel here changes.
# Kill: HOUSE_CONDUCTOR_CLIP_HEADER=0.
CLIP_HEADER = os.environ.get("HOUSE_CONDUCTOR_CLIP_HEADER", "1").strip().lower() not in ("0", "false", "no", "off")
_CLIP_SAFE = re.compile(r"[^A-Za-z0-9._-]")
# A CLIP FRAME ON ASK (round 14, 2026-10-12: the desk Wall's pre-fill builds a fresh install's loops at idle): GET
# /v1/clip-frame?key=K&i=N answers that clip frame as this player would publish it with NO mouth painted (the Library's
# own frame, JPEG_Q), with its X-Mouth-Box, X-Clip and X-Face-Point - so the Wall's ASCII picture of it is the one a live
# lap draws everywhere but her mouth, and the live frame is a region over it. Read only; the playhead never moves.
# Kill: HOUSE_CONDUCTOR_CLIP_FRAME=0.
CLIP_FRAME_ROUTE = os.environ.get("HOUSE_CONDUCTOR_CLIP_FRAME", "1").strip().lower() not in ("0", "false", "no", "off")
# EACH FRAME NAMES ITS FACE POINT (round 45, .101: "2 of the last 130 pictures drawn fresh" on a clip's first play after
# the build): the desk Wall's whole frame is drawn about the clip's face point, and the Wall read it from /v1/state,
# polled every 0.5 s (0.1 s while she speaks) - so for up to half a second after a clip change her new clip's frames were
# drawn about the OLD clip's point: another picture than the build's, too different for a region, drawn whole. Every
# /frame.mjpg part and /frame.jpg now carry X-Face-Point for the clip the frame is from (X-Clip's key; the same value
# /v1/clip-frame sends the build). Metadata only. Kill: HOUSE_CONDUCTOR_FACE_HEADER=0.
FACE_HEADER = os.environ.get("HOUSE_CONDUCTOR_FACE_HEADER", "1").strip().lower() not in ("0", "false", "no", "off")


def face_point_value(lib, clip) -> str:
    """X-Face-Point "x,y" (film fractions) of the clip this frame is from, '' for none (no clip, no head track)."""
    if not FACE_HEADER or not (isinstance(clip, tuple) and clip and clip[0]):
        return ""
    try:
        fp = wall_frame_state(lib, clip[0]).get("face_point")
        return "%r,%r" % (float(fp[0]), float(fp[1])) if fp else ""
    except Exception:  # noqa: BLE001 - a header is a nicety; the frame never fails on it
        return ""


def clip_frame_reply(lib, graph, ck: str, ci: int):
    """(jpeg bytes, [(header, value)]) for clip ck's frame ci as this player publishes it unpainted, else an error
    word. The one body of /v1/clip-frame, on the player and in clip_frames_main."""
    arr = lib.frames(ck) if ck else None
    if arr is None or not (0 <= ci < len(arr)):
        return "no such clip frame"
    ok_, buf_ = cv2.imencode(".jpg", arr[ci], [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_Q])
    if not ok_:
        return "encode"
    hdrs = []
    try:
        box_ = lib.box(ck, ci)
    except Exception:  # noqa: BLE001
        box_ = None
    try:
        role_ = graph.role(ck)
    except Exception:  # noqa: BLE001
        role_ = ""
    fp_ = wall_frame_state(lib, ck).get("face_point")
    mb_ = mouth_box_header(box_)
    if mb_:
        hdrs.append(("X-Mouth-Box", mb_.split(b":", 1)[1].strip().decode("ascii")))
    cv_ = clip_header_value((ck, ci, len(arr), role_))
    if cv_:
        hdrs.append(("X-Clip", cv_))
    if fp_:
        hdrs.append(("X-Face-Point", "%r,%r" % (float(fp_[0]), float(fp_[1]))))
    return buf_.tobytes(), hdrs


# THE BUILD DECODES ITS OWN FRAMES (round 15, 2026-10-12, .101's frozen build): `house-conductor.py --clip-frames` is
# this player's library and nothing else - no playhead, no feed, no mouth, no sound - serving only /v1/clip-frame (and
# /health) on 127.0.0.1 at a port of its own, which it prints as one JSON line. The desk Wall's pre-fill starts one
# at its own idle priority and reads its frames from it, so a build never asks the live player to decode a library
# while she plays (the bytes are clip_frame_reply's either way). It exits when its parent's stdin pipe closes.
def clip_frames_main(port: int = 0) -> None:
    inv = inv_load()
    clips, pools = inv["clips"], (inv.get("pools") or {})
    lib = Library(merge_face_bank_clips(clips))
    motion, face = survey(clips, lib)
    graph = Graph(clips, pools, motion=motion, face=face, child_paths=inv.get("child_paths") or {},
                  kit=inv.get("kit") or {})

    class Frames(Handler):
        def log_message(self, *a):                     # noqa: D401 - quiet
            pass

        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                pass                                   # the build stopped asking mid-reply: nothing to say

        # (the GET and POST verbs are bound by name below, never spelled as the Handler's are: tests find the
        # player's own handlers by those phrases, the first in the file)
        def _frames_get(self):
            path_ = self.path.split("?", 1)[0]
            if path_.endswith("/clip-frame") and path_.startswith("/v1/"):
                return self._clip_frame(lib, graph)
            if path_ == "/health":
                return self._json(200, {"ok": True, "frames": True, "pid": os.getpid()})
            return self._json(404, {"ok": False})

        def _frames_post(self):
            return self._json(404, {"ok": False})

        do_GET = _frames_get                           # noqa: N815
        do_POST = _frames_post                         # noqa: N815

    srv = ThreadingHTTPServer(("127.0.0.1", int(port)), Frames)
    srv.daemon_threads = True
    print(json.dumps({"clip_frames": srv.server_address[1], "pid": os.getpid()}), flush=True)
    try:
        os.dup2(2, 1)              # nobody reads the pipe after this line: anything said later goes to the log
    except OSError:
        pass

    def _watch_stdin():
        try:
            while sys.stdin.buffer.read(1):
                pass
        except Exception:  # noqa: BLE001
            pass
        srv.shutdown()

    threading.Thread(target=_watch_stdin, daemon=True, name="parent").start()
    srv.serve_forever()
    if CLEAN_EXIT_ON:
        clean_exit(None, None, srv, 0)     # round 17: its frame routes may still be inside OpenCV (clean_exit below)


def clip_header_value(clip) -> str:
    """"key;frame;frames;role" for a (key, frame, frames, role), "" for none."""
    if not CLIP_HEADER or not clip:
        return ""
    try:
        key, i, n, role = clip
        return "%s;%d;%d;%s" % (_CLIP_SAFE.sub("_", str(key)), int(i), int(n), _CLIP_SAFE.sub("_", str(role or "")))
    except (TypeError, ValueError):
        return ""


# Mouth composite — the same geometry the glass uses, so the two agree:
# the clip's own closed-mouth width is the ruler, the upper lip is the anchor.
# HOW MUCH OF THE PATCH IS ACTUALLY REPLACED, as a fraction of the ellipse.
# At 0.34 only the inner third was opaque and the outer two thirds was a blend —
# so her REAL closed jaw showed through the same pixels the OPEN jaw was drawn
# on, and you saw both edges at once. the User, 2026-08-30: "the jaw rendered a bit
# too small so looks doubled." It was not too small; it was too transparent.
#
# Measured on clip-listen-back: the patch spans y 198..254 against a chin at
# 249, so it reaches far enough — the problem was never extent. 0.62 keeps a
# soft edge for the seam while making the middle genuinely solid.
# Live: MOUTH_TUNE({"core": 0.7}).
MOUTH_CORE = 0.62
MOUTH_FLOOR = 0.28         # normalised envelope below this reads as closed
VEL_LO, VEL_HI = 1.2, 3.0  # box-widths/sec; fade out while the head moves
# WHY A CLIP WENT WITHOUT A MOUTH, ONE LINE A CLIP (round 21, .101 round 20: "no lips on flourish looking up again"): the
# kit paints flourish-stretch-shoulders-work on every frame here (a bench and the wall role, a stub sheet on air: no
# skip, the mouth region 15-21 levels off the film on the look-up frames), and no flourish's sidecar or turn fill has a
# blank frame, so what the glass showed needs the player's own account. While her voice is on air each clip's paint
# passes are counted painted / unpainted with the skip reason, and one line is printed when the clip or the line ends:
# "mouth on <clip> while she spoke: N passes, painted P, unpainted U (<reason>: k; frames a-b)". Kill
# HOUSE_MOUTH_CLIP_TALLY=0.
MOUTH_CLIP_TALLY = os.environ.get("HOUSE_MOUTH_CLIP_TALLY", "1").strip().lower() not in ("0", "false", "no", "off")
MOUTH_SCALE = 1.00         # patch size against the strip's ruler
# WHICH WARP PUTS THE TILE ON HER FACE. dental = pixels sag inside the
# still-rectangle (2D tiles). plane = dest vertices drop along v (U
# footprint) — gated competitor, not default. affine is the flat rollback.
# lips/ellipse paste is retired. the User 2026-09-04.
MOUTH_WARP = os.environ.get("HOUSE_MOUTH_WARP", "dental").strip().lower()
# CHEEKS + TRIANGLE (the User 2026-10-02): one warp choice. Front shots keep the regular Bezier cheek warp, unchanged; a
# side turn lands the tile on a triangle of her own points (nose tip, chin, near cheek off her outline), with the
# whole-mouth erase and the standard soft edge. It needs NO setup: the hand-over is the face's existing
# HOUSE_TURN_FRONT_MAX, the 45/90 split is read off the two turn libraries' own tiles, the other side is mirrored
# where the face has no takes of it, and the change of tile at the hand-over rides the xfade dial like any other.
# Optional per-face overrides live in <pose-dir>/mouth-turn.json {"from": deg, "blend": deg, "p90_from": deg,
# "mirror_left": bool, "lip_margin": x head size} (blend = degrees, ending at `from`, over which the two landings
# are mixed; lip_margin = how far past her tracked outline the tile and the erase reach, default HOUSE_MOUTH_LIPS_GROW_S).
# Chosen as warp "cheeks-tri"; HOUSE_MOUTH_TURN_TRI=0 makes that choice paint as plain cheeks.
MOUTH_TURN_TRI = MOUTH_WARP == "cheeks-tri"
if MOUTH_TURN_TRI:
    MOUTH_WARP = "cheeks"
TURN_TRI_ON = os.environ.get("HOUSE_MOUTH_TURN_TRI", "1").strip().lower() not in ("0", "false", "off", "no")
TURN_EDGE_ON = os.environ.get("HOUSE_MOUTH_TURN_EDGE", "1").strip().lower() not in ("0", "false", "off", "no")
# THE BEND on a side turn (the User 2026-10-02; lib/mouth_pins.arch_reproject): the tile is re-projected across her
# face along the dental arch from the angle it was filmed at to the angle she is at, and drawn along her face at the
# head-size ratio - not squeezed between her nose and a chin its own open jaw has dropped. OFF by default:
# HOUSE_MOUTH_TURN_BEND=1, or the face's mouth-turn.json {"bend": true}.
TURN_BEND_ON = os.environ.get("HOUSE_MOUTH_TURN_BEND", "0").strip().lower() in ("1", "true", "yes", "on")
if MOUTH_WARP not in ("auto", "affine", "triad", "cheeks", "dental", "plane", "diamond"):
    MOUTH_WARP = "dental"
# SECOND PIN: the mouth's WIDTH. The nose pin and the axes hold (drawn and
# checked 2026-09-04, results/pins.png) but the rigid s cannot know that the
# desk-zoom take was shot closer -- its mouth lands 1.23x the width of the one
# it replaces, profile-45 1.21x, harvest-a 0.94x. Fitting width collapses that
# spread to nothing (0.43 -> 0.00 over 399 pairs) and leaves HEIGHT alone,
# which is where the vowels live (tiles run 1.45x the dest height because an
# open mouth is taller; flattening that would undo the bank).
# none = head scale only. width = per-TILE (collapses EE/WOO onto dest_w).
# take = one k per source take, median of dest_w/landed_w — the lens, not
# the articulation. docs/designs/SIMPLE-VISEME-SCHEME-REVIEW-20260904.md.
# take-uni (2026-09-10): the take's k on BOTH axes, per frame — bounded by
# her mouth width every frame, so a bad s ratio between takes cannot grow
# the mouth as she tilts (flourish-listen-close: 1.15× height, max 1.30×,
# with width-only take; 0.96× with take-uni). Default stays take.
MOUTH_FIT = os.environ.get("HOUSE_MOUTH_FIT", "take").strip().lower()
if MOUTH_FIT not in ("none", "width", "take", "take-uni"):
    MOUTH_FIT = "take"
# Pin: nose (rigid frame, live) or lip — the tile's upper-lip top-centre
# lands on this frame's head-track mouth box top-centre. HOUSE_MOUTH_PIN.
# Live: POST /v1/mouth-tune {"pin": "lip"}.
# nose+lip: keep the nose, scale along v about the nose row so the upper
# lip lands — the 2-D tilt (the User 2026-09-10).
MOUTH_PIN = os.environ.get("HOUSE_MOUTH_PIN", "nose").strip().lower()
# nose+chin-pixel (the User 2026-10-26, Bash's double chin on a lift: "pin chin to pixel of chin extent" / "two pins
# to nose and chin tip"): nose-pixel as it is, then the tile is stretched along her nose->chin axis about the pinned
# nose so its chin lands on HER chin as matched by pixels (lib/head_frame.pin_chin_pixel), plus the tile's own jaw
# past hers at the head scale (the class baseline: an open AH stays open). Default OFF; bench only. It is a flag
# beside the pin (MOUTH_CHIN_PIN), so a lock's pin=nose-pixel keeps it; POST /v1/mouth-tune {"chin_pin": true|false}
# or {"pin": "nose+chin-pixel"} turns it on live. HOUSE_MOUTH_CHIN_PIN=1 is the same switch.
MOUTH_CHIN_PIN = (MOUTH_PIN == "nose+chin-pixel"
                  or os.environ.get("HOUSE_MOUTH_CHIN_PIN", "1").strip().lower() in ("1", "true", "on", "yes"))   # ON by default (the User 2026-10-27: "we solved a double chin on ups with a chin pin. We need that always"); HOUSE_MOUTH_CHIN_PIN=0 is the kill
if MOUTH_PIN == "nose+chin-pixel":
    MOUTH_PIN = "nose-pixel"
# the chin stretch is bounded (a wrong match must not throw the jaw): the same band jaw_stretch uses
MOUTH_CHIN_PIN_LO = float(os.environ.get("HOUSE_MOUTH_CHIN_PIN_LO", "0.85"))
MOUTH_CHIN_PIN_HI = float(os.environ.get("HOUSE_MOUTH_CHIN_PIN_HI", "1.2"))
# THE CHIN PIN STRETCHES A STILL MOUTH ONLY (the User 2026-10-30: "analyze that pin and see if it was aimed imprecisely.
# Correct that"; nose_u / nose_v back to 0). The stretch aims a tile's chin at her chin + rest_below, the tile's drop on
# the clip's rest-pose frames - a model of a filmed jaw that does not move. On a clip whose filmed lips part (a talking
# take, a speaking flourish) her chin has already dropped with her jaw and the baseline was added on top: every tile
# landed low on its OWN source frame (kit bench at nose 0, 19 classes: rel2.0 upper lip +3.48 px / chin +2.95 median,
# Aa's chin +15.4; Lite Friday +0.93 / +4.25). Her own tiles carry her own jaw, and with no stretch they land on
# themselves (rel2.0 0.01 / -0.44, Lite Friday 0.16 / 0.21, Lite Bash -0.02 / 0.08). Correcting the baseline by her jaw
# instead was measured and dropped: lipmarks and the pixel chin disagree by several px on a ~70 px mouth (Lite Friday's
# Ah chin -4.9 px). A still mouth (a hub loop she speaks over: the lift that made the double chin) keeps the stretch,
# unchanged byte for byte. Kill: HOUSE_MOUTH_CHIN_STILL_ONLY=0 (the stretch on every clip, as before).
MOUTH_CHIN_STILL_ONLY = os.environ.get("HOUSE_MOUTH_CHIN_STILL_ONLY", "1").strip().lower() not in ("0", "false", "off", "no")
# A CLIP TALKS when BOTH: its own row says it speaks (a harvest: the row carries a pose_class; or a words sidecar,
# visemes/<stem>.timeline.json or .phon.json), AND her inner lips (lipmarks 13-14, / s) part past twice the picker's own
# OPEN_FLOOR (scripts/pose-bow-same-spot.py; a test pins them equal) on some frame. The role keeps a still loop that
# wobbles near the line from ever flipping (Bash's listen-screen, the double chin this pin was built for, peaks at 0.0077);
# her hub loops are still by law, so on air the stretch is unchanged. Measured: the still listen loops of rel2.0 / Lite
# Friday / Lite Bash peak at 0.0024 / 0.0016 / 0.0052; their talking takes at 0.14-0.21.
MOUTH_CHIN_TALK_OPEN = float(os.environ.get("HOUSE_MOUTH_CHIN_TALK_OPEN", "0.005"))


def pin_dial() -> str:
    """The pin as ONE control: nose+chin-pixel when the chin pin rides on the pixel pin, else the pin itself."""
    return "nose+chin-pixel" if (MOUTH_CHIN_PIN and MOUTH_PIN == "nose-pixel") else MOUTH_PIN
# nose-pixel: template-match the tile's nose patch into the frame and
# shift the whole tile so the two noses coincide — pixels, not landmarks
# (the User 2026-09-10 "pin the pixel from the nose on the other").
if MOUTH_PIN not in ("nose", "lip", "nose+lip", "nose-pixel"):
    MOUTH_PIN = "nose"
# Neck cut (the User 2026-09-10 "transparent background on affines"): the
# tile's alpha dies below its landed jaw line and beyond the face
# half-width, feathered, so her own neck and the room stay hers. Live:
# POST /v1/mouth-tune {"neck_cut": true}. Margin / half-width in units of s.
# Post-match nudge (pin=nose-pixel only): NOSE_U/NOSE_V are px as tuned on
# the close-up (head s≈357) and scale with s, so −8 px there is −2.6 px on
# the desk (s≈117). Only after a successful match — an unmatched frame
# keeps the plain nose pin, un-nudged (heads-down stays as it was).
MOUTH_NUDGE_REF_S = float(os.environ.get("HOUSE_MOUTH_NUDGE_REF_S", "357"))
# Idle rest paste (hub f0 onto the loop's frame while she is not on air).
# the User 2026-09-11: "can we link it to Pause/Listening? so talkies can also
# play as rendered clips without mouthbox" — off = the filmed mouth shows
# whenever she is not speaking (+0.4 s tail); the paste is speech only.
# Live: POST /v1/mouth-tune {"idle_rest": false}. Default keeps the law.
MOUTH_IDLE_REST = os.environ.get("HOUSE_MOUTH_IDLE_REST", "1").strip().lower() not in ("0", "false", "off", "no")
# A CLIP WITH A PLAYLIST NEVER WEARS THE STRIP (R44-1, round 45, .101 round 44: "a tiny teeth-only frame during a dip",
# "first time in forever", on work-multitask or a head-bow flourish). Every frame of every painting clip has its stamp
# (checked frame by frame, the repo and the round 44 kit); the flash is the air window: on air with no hop under `now`
# (the window opens before the sheet's first hop lands, or a stalled batch - the kit holds no stamp, HOUSE_SPEAK_STAMP_HOLD
# =0) Mouth.viseme() is None, the playlist looks up no class, and the frame fell through to the legacy Strip - the old
# 96 px mouth in an ellipse. Such a frame takes the closed rest (her filmed mouth when idle_rest is off), and nothing on
# a playlist clip reaches the Strip. On in a pack; the house keeps its mouth law until the User says (=1 turns it on).
# Kill HOUSE_MOUTH_PL_NO_STRIP=0.
MOUTH_PL_NO_STRIP = os.environ.get("HOUSE_MOUTH_PL_NO_STRIP", "0" if crissa_home.is_house() else "1").strip().lower() \
    not in ("0", "false", "off", "no")
# … and never while comms are PAUSED (the User 2026-09-11 "still painting rests
# while on Paused comms"): :8099 /v1/house hear.paused, polled 1 Hz, fail open.
# THE ENV WINS OVER EVERY DIAL SET (round 36, .101 round 35 FAIL 1; the User's "off everywhere"): a set
# HOUSE_MOUTH_IDLE_REST was honoured only at load, so a clip's own dials (flourish-glance, both bun-wobbles carry
# idle_rest true), a preset, a restore or a tune turned the rest tile back on. With the variable set, nothing but the
# variable decides it: idle, the closed rests inside a line, and paused (the paused rule only ever adds the filmed
# mouth). Unset (the house) = the dials decide, as before. Kill: HOUSE_MOUTH_IDLE_REST_ENV_WINS=0.
def idle_rest_pinned(env=None):
    """The env's idle_rest when it is set and wins, else None (the dials decide)."""
    env = os.environ if env is None else env
    if env.get("HOUSE_MOUTH_IDLE_REST_ENV_WINS", "1").strip().lower() in ("0", "false", "off", "no"):
        return None
    v = env.get("HOUSE_MOUTH_IDLE_REST")
    if v is None or not v.strip():
        return None
    return v.strip().lower() not in ("0", "false", "off", "no")


MOUTH_IDLE_REST_PAUSED = os.environ.get("HOUSE_MOUTH_IDLE_REST_PAUSED", "0").strip().lower() in ("1", "true", "on", "yes")
# The idle rest tick also covers a talking TAKE at 1x off air (2026-09-26); 0 = the plan/harvest class paints there as before.
IDLE_REST_ON_TAKES = os.environ.get("HOUSE_IDLE_REST_ON_TAKES", "1").strip().lower() not in ("0", "false", "off", "no")
# THE REST CLASS USES ITS PICKED TILE (the User 2026-09-20: "Rest paste on
# square-the law (not AUTO) is painting the Rest frame as small mouth circle").
# It was painting the LOOP'S OWN FRAME 0 through _paste_hub_rest, cropped to the
# arch band because that path reads `hole` and never `alpha` — measured on
# crissa-close-harvest f100: 148x149 px against 372x246 for the picked tile, which
# is the circle. The bypass was right on 2026-09-10, when the playlist rest was
# harvest-b f0 and read as a held-jaw underbite; it predates the re-picked,
# face-scaled rest tiles. Now rest goes through the same path as every other
# class, so it honours alpha/hole/pin/fit and the User's own pick is what lands.
# No switch and no dead path: the hub rest is DELETED, not flagged off. the User
# 2026-09-20: "all patches act as hidden viruses that eventually come back like
# this. I hate them but they are everywhere" — this bug WAS such a patch, nine
# days invisible. The rollback is git (tag auto-alpha-face-square-20260920-before
# and the checkpoint), not a lever in the source that nobody remembers to flip.
_HOUSE_PAUSED = {"t": 0.0, "on": False}


# A BENCH IS NOT PAUSED WITH THE HOUSE (the User 2026-09-26: "Paste Rest tiles not working to
# cover talking clips. I dont think it works at all"): the 09-11 rule reads the wall's comms pause
# from :8099 and holds the idle rest paste while it is on — right for the wall, and on a bench it
# meant the tick never pasted while he worked, because the house IS paused then. The bench is a
# tuning glass; it paints what the dials say. Kill HOUSE_BENCH_IGNORES_PAUSE=0 (the old read).
BENCH_IGNORES_PAUSE = os.environ.get("HOUSE_BENCH_IGNORES_PAUSE", "1").strip().lower() not in ("0", "false", "off", "no")


_HOUSE_CONV = {"t": 0.0, "on": False}


# THE PAINT NEVER WAITS ON :8099 (the User 2026-10-12, the house readings). house_in_conversation() and house_paused()
# each did a blocking GET of /v1/house from inside _composite, once a second each (two separate 1 s caches), and that
# answer took 60-500 ms: twice a second a speaking pass stalled that long (the stall watch's 60 ms look caught the paint
# thread in client.py readinto on every one), a 3-25 row hole in her mouth. One background thread now reads /v1/house
# once a second and both answers come from it; the paint reads two booleans. Same meaning, same <= 1 s staleness, the
# same "unreadable = no conversation, not paused". Kill HOUSE_CONDUCTOR_HOUSE_POLL_BG=0 (the in-paint GETs as before).
HOUSE_POLL_BG = os.environ.get("HOUSE_CONDUCTOR_HOUSE_POLL_BG", "1").strip().lower() not in ("0", "false", "no", "off")
_HOUSE_BG = {"run": False, "conv": False, "paused": False, "t": 0.0}


def house_poll_start() -> None:
    """Start the /v1/house reader (wall only: a bench never asks); in a pack, the ear bridge's /health (CONV_URL)."""
    if not HOUSE_POLL_BG or IS_BENCH or not CONV_URL or _HOUSE_BG["run"]:
        return

    def _loop():
        while True:
            conv = paused = False
            try:
                with urllib.request.urlopen(CONV_URL, timeout=0.5) as r:
                    h = json.load(r)
                hear = (h.get("hear") or {}) if HOUSE_URL else h
                conv = ("paused" in hear) and not bool(hear.get("paused"))
                paused = bool(hear.get("paused")) if HOUSE_URL else False
            except Exception:  # noqa: BLE001 - unreadable = no conversation, not paused (as the in-paint reads were)
                pass
            _HOUSE_BG.update(conv=conv, paused=paused, t=time.time())
            time.sleep(1.0)

    _HOUSE_BG["run"] = True
    threading.Thread(target=_loop, name="house-poll", daemon=True).start()


def house_in_conversation() -> bool:
    """A conversation is on: :8099 /v1/house hear.paused is false (listening or speaking). 1 s cache. Unreadable
    = NOT in conversation, so a dead glass never holds her flourishes; a bench never is (2026-10-01, CONV_HOLD)."""
    if IS_BENCH:
        return False
    if not CONV_URL:
        return False
    if _HOUSE_BG["run"]:
        return bool(_HOUSE_BG["conv"])
    now = time.time()
    if now - _HOUSE_CONV["t"] < 1.0:
        return _HOUSE_CONV["on"]
    on = False
    try:
        with urllib.request.urlopen(CONV_URL, timeout=0.5) as r:
            h = json.load(r)
        hear = (h.get("hear") or {}) if HOUSE_URL else h
        on = ("paused" in hear) and not bool(hear.get("paused"))
    except Exception:  # noqa: BLE001
        on = False
    _HOUSE_CONV["t"] = now
    _HOUSE_CONV["on"] = on
    return on


def house_paused() -> bool:
    if IS_BENCH and BENCH_IGNORES_PAUSE:
        return False
    if not HOUSE_URL:
        return False
    if _HOUSE_BG["run"]:
        return bool(_HOUSE_BG["paused"])
    now = time.time()
    if now - _HOUSE_PAUSED["t"] < 1.0:
        return _HOUSE_PAUSED["on"]
    on = False
    try:
        with urllib.request.urlopen(HOUSE_URL, timeout=0.5) as r:
            h = json.load(r)
        on = bool(((h.get("hear") or {}).get("paused")))
    except Exception:  # noqa: BLE001
        on = False
    _HOUSE_PAUSED["t"] = now
    _HOUSE_PAUSED["on"] = on
    return on
MOUTH_NECK_CUT = os.environ.get("HOUSE_MOUTH_NECK_CUT", "0").strip().lower() in ("1", "true", "on", "yes")
MOUTH_NECK_MARGIN = float(os.environ.get("HOUSE_MOUTH_NECK_MARGIN", "0.03"))
MOUTH_FACE_HALF = float(os.environ.get("HOUSE_MOUTH_FACE_HALF", "0.52"))
# THE DENTAL CURVE. the User's parabola + normal power series, docs/designs/
# CURVED-ARCH-PLANE-20260903.md. Measured against the .mouth.json sidecar it
# erases 80-93% of the filmed mouth where the live ellipse manages 50-64%, and
# spares the dest nose on all 394 samples. Off until his eye says otherwise.
MOUTH_HOLE = os.environ.get("HOUSE_MOUTH_HOLE", "arch").strip().lower()
# the library's deep-bow hole (Playhead._hole, mouth-turn.json deep_hole; the User 2026-10-28). Kill: =0.
MOUTH_DEEP_HOLE = os.environ.get("HOUSE_MOUTH_DEEP_HOLE", "1").strip().lower() not in ("0", "off", "false", "no")
if MOUTH_HOLE not in ("ellipse", "arch"):
    MOUTH_HOLE = "arch"
# WHAT GETS PASTED. Square only. The lip-ellipse cut is retired — it
# shortened her chin and the User asked to never see it again, including on
# example sheets. A tune of "lips" is 409, not a silent rollback.
# THE TILE ALPHA (2026-09-16, the close bank): `square` is the whole harvested
# rectangle, nose-cut and feathered (the law since 09-04, "jaw stays"). On a
# 1280x720 close-up the rectangle is her face from the nostrils to the collar,
# so its top edge cut through her nose and its cooler skin ghosted hers from
# nose to neck on every tile (measured: 34k changed px, bbox 345x380 of a
# 736x400 frame). `jaw` keeps the square only around the landed mouth and
# down the jaw to the landed chin — the tile's mouth and its jaw drop, not
# its nose, cheeks or neck. `auto` = jaw when the landed tile is taller than
# a third of the frame (a close-up), else square. Kill: HOUSE_MOUTH_ALPHA=square.
MOUTH_ALPHA = os.environ.get("HOUSE_MOUTH_ALPHA", "square").strip().lower()
if MOUTH_ALPHA not in ("square", "jaw", "auto"):
    MOUTH_ALPHA = "square"
JAW_ALPHA_TALL = 0.35   # auto: landed tile height / frame height above this is a close-up


def jaw_alpha(a: np.ndarray, dst: dict, rec: dict | None, M, scale: float = 1.0, maps=None) -> np.ndarray:
    """square_alpha × a soft window around the LANDED mouth and its jaw.

    Window = an ellipse around the tile's mouth box as landed (mouth_quad of
    `mouth_in_tile` under M) with a margin of the head size, joined to a band
    the mouth's width running down the face axis (dst v) to the landed chin.
    Feathered by a distance transform at 0.06·s like the square. Without a
    matrix or a tile mouth box the film's own mouth box stands in."""
    try:
        H, W = a.shape[:2]
        s = float((dst or {}).get("s") or 100.0) * float(scale or 1.0)
        mit = (rec or {}).get("mouth_in_tile") if rec else None
        lc = None
        if maps is not None and mit is not None:
            # a map warp (cheeks / dental / plane): the dest pixels whose
            # source lies in the tile's mouth box are where its mouth landed
            bx, by, bw, bh = [float(v) for v in list(mit)[:4]]
            mx_, my_ = maps[0], maps[1]
            sel = (mx_ >= bx) & (mx_ < bx + bw) & (my_ >= by) & (my_ < by + bh)
            ys, xs = np.nonzero(sel)
            if xs.size < 4:
                return a
            lo = np.array([float(xs.min()), float(ys.min())])
            hi = np.array([float(xs.max()) + 1.0, float(ys.max()) + 1.0])
            chin = (rec or {}).get("chin") if rec else None
            if chin is not None:
                near = (np.abs(mx_ - float(chin[0])) < 6.0) & (np.abs(my_ - float(chin[1])) < 6.0)
                cy_, cx_ = np.nonzero(near)
                if cx_.size:
                    lc = np.array([float(cx_.mean()), float(cy_.mean())])
        elif M is not None and mit is not None:
            q = mouth_quad(mit, M)
            lo, hi = q.min(axis=0), q.max(axis=0)
        else:
            mx, my, mw, mh = [float(v) for v in list(dst["mouth"])[:4]]
            lo, hi = np.array([mx, my]), np.array([mx + mw, my + mh])
        # THE FILM'S OWN MOUTH IS INSIDE THE WINDOW (2026-09-17). The window
        # followed the TILE's landed mouth only; on a frame where she smiles
        # wider than the tile, her own mouth corners stood outside it, un-erased
        # and un-covered — two pairs of corners beside the pasted lips.
        fm = (dst or {}).get("mouth")
        if isinstance(fm, (list, tuple, np.ndarray)) and len(fm) == 4:
            fx, fy, fw, fh = [float(v_) for v_ in list(fm)[:4]]
            lo = np.minimum(np.asarray(lo, dtype=np.float64), np.array([fx, fy]))
            hi = np.maximum(np.asarray(hi, dtype=np.float64), np.array([fx + fw, fy + fh]))
        cx, cy = float((lo[0] + hi[0]) / 2.0), float((lo[1] + hi[1]) / 2.0)
        wm, hm = float(hi[0] - lo[0]), float(hi[1] - lo[1])
        v = np.asarray(dst.get("v", [0.0, 1.0]), dtype=np.float64).ravel()[:2]
        vh = v / max(1e-6, float(np.linalg.norm(v)))
        uh = np.array([vh[1], -vh[0]])
        chin = (rec or {}).get("chin") if rec else None
        if lc is None and chin is not None and M is not None:
            lc = np.asarray(M, dtype=np.float64) @ np.array([float(chin[0]), float(chin[1]), 1.0])
        elif lc is None:
            lc = np.asarray(dst.get("chin", [cx, cy + hm]), dtype=np.float64).ravel()[:2]
        t_chin = float(np.dot(lc - np.array([cx, cy]), vh))
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        px, py = xx - cx, yy - cy
        t_u = px * uh[0] + py * uh[1]
        t_v = px * vh[0] + py * vh[1]
        # margins in head units: 0.03·s (~11 px on a 360 px head) beyond the
        # landed mouth box, and the band the mouth's width to the landed chin;
        # the feather (0.04·s) runs INWARD from that edge so the nostrils,
        # 34 px above the mouth box on the close-up, stay hers
        rx = 0.55 * wm + 0.03 * s
        ry = 0.50 * hm + 0.04 * s
        ell = (t_u / max(1.0, rx)) ** 2 + (t_v / max(1.0, ry)) ** 2 <= 1.0
        half = 0.45 * wm + 0.03 * s
        band = (np.abs(t_u) <= half) & (t_v >= 0.0) & (t_v <= max(0.0, t_chin) + 0.02 * s)
        win = (ell | band).astype(np.uint8)
        feather = max(2.0, 0.04 * s)
        win_d = cv2.dilate(win, np.ones((7, 7), np.uint8), 1)
        dist = cv2.distanceTransform(win_d, cv2.DIST_L2, 3)
        soft = np.clip(dist / feather, 0.0, 1.0).astype(np.float32)
        return (a * soft).astype(np.float32)
    except Exception:  # noqa: BLE001
        return a


def _landed_tall(warped: np.ndarray) -> bool:
    try:
        live = warped.max(axis=2) > 0 if warped.ndim == 3 else warped > 0
        rows = np.flatnonzero(live.any(axis=1))
        if rows.size == 0:
            return False
        return (float(rows[-1] - rows[0] + 1) / float(warped.shape[0])) > JAW_ALPHA_TALL
    except Exception:  # noqa: BLE001
        return False


# A FACE-SCALED TILE NEEDS NO JAW WINDOW (the User 2026-09-20: "postage stamp size
# mouth not the neck sized ones i selected"). jaw_alpha was written for a
# mouth-crop tile: a raw rectangle of lips landing on a close-up face had to be
# clipped to an ellipse + chin band or it read as a pasted patch. Since the
# 09-19 face crop a tile carries her whole cheek chord down past the chin and
# feathers out on the neck cut, so the window only throws her face away again.
#
# MEASURED on the bench, /frame.jpg differenced against the raw film frame
# (crissa-close-harvest f240, holding AH): alpha auto and alpha jaw both paint
# 144x120 px, alpha square paints 249x162 — 1.7x wider, 1.35x taller. auto had
# resolved to jaw on EVERY close-up frame of both libraries (landed tile height
# 0.53-0.64 of the frame against JAW_ALPHA_TALL 0.35), before and after the
# re-cut, so a day of re-clipping to neck-sized tiles could not reach the glass.
#
# The test is physical, not a filename or a `recut` tag: does this tile carry
# her cheek chord? Measured over both maps — 3724 of 3739 face-crop tiles hold
# both cheeks inside the picture, against 2525 of 3739 mouth-crop tiles that do
# not. The 1214 old tiles that pass are wide enough that square was always safe
# on them. A tile with no cheek marks (Lila's pre-09-04 takes) reads False and
# keeps the old behaviour. Kill: HOUSE_MOUTH_AUTO_FACE_SQUARE=0.
AUTO_FACE_SQUARE = os.environ.get("HOUSE_MOUTH_AUTO_FACE_SQUARE", "1") != "0"
JAW_CHEEK_INSET = 0.02   # of tile width, so a cheek ON the edge is not "inside"


def _tile_carries_cheeks(rec: dict | None) -> bool:
    """True when both cheek marks lie inside this tile's own picture."""
    if not AUTO_FACE_SQUARE or not isinstance(rec, dict):
        return False
    try:
        wh = rec.get("src_wh")
        cl, cr = rec.get("cheek_l"), rec.get("cheek_r")
        if not wh or cl is None or cr is None:
            return False
        w, h = float(wh[0]), float(wh[1])
        if w < 8.0 or h < 8.0:
            return False
        m = JAW_CHEEK_INSET * w
        return all(m <= float(c[0]) <= w - m and 0.0 <= float(c[1]) <= h
                   for c in (cl, cr))
    except (TypeError, ValueError, IndexError):
        return False
# Vertical pad for the playlist paste. MUST MATCH THE HARVEST PAD by default.
# the User asked for taller to fix a double nose and I pasted a 0.55-cut tile into
# a 0.85 box — which does not show more of her face, it STRETCHES the crop
# 1.55x vertically. Her mouth came out tall and thin ("mouth too small"). The
# double nose needs a taller HARVEST; the paste must keep the harvest aspect.
PLAYLIST_PAD_Y = float(os.environ.get("HOUSE_PLAYLIST_PAD_Y", str(PLAYLIST_PAD)))
# Match the pasted mouth's b* to the face it lands on. Off with =0.
PLAYLIST_TONE = os.environ.get("HOUSE_PLAYLIST_TONE", "1") != "0"


def tone_b(patch, dst, w):
    """b* match, honouring HOUSE_PLAYLIST_TONE. The measurement and the maths
    moved to lib/pose_mouth.py on 2026-09-04 so the benches paint what she
    paints; this keeps the flag with the service that owns it."""
    return _tone_b(patch, dst, w, on=PLAYLIST_TONE)
# Paste the tile at ITS OWN aspect rather than the target box's. See
# _paste_playlist — the mouth box aspect varies 1.70..3.28 across clips.
PLAYLIST_KEEP_ASPECT = os.environ.get("HOUSE_PLAYLIST_ASPECT", "1") != "0"
# Clone: nose-latched locked x,y. Haar is the rollback.
# Unknown values 409. HOUSE_MOUTH_ANCHOR=haar to undo.
MOUTH_ANCHOR = os.environ.get("HOUSE_MOUTH_ANCHOR", "head").strip().lower() or "head"
# Paste-pin offset in face UV, in PIXELS along the unit axes (the User
# 2026-09-10: the old 0.03·s fraction was 3.5 px on the desk and 11 px on
# the close-up — "always off to the right"). +u = her left (viewer's right
# on work). Her-left nostril tear: small +u. 0 is the geometric nose.
# Default 3.5 px = the desk value it replaces.
# Live: POST /v1/mouth-tune {"nose_u": 3.5, "nose_v": 0} (px, clamp ±40).
MOUTH_NOSE_U = float(os.environ.get("HOUSE_MOUTH_NOSE_U", "3.5"))
MOUTH_NOSE_V = float(os.environ.get("HOUSE_MOUTH_NOSE_V", "0"))
# THE TURN HAS ITS OWN NUDGE (the User 2026-10-02: a u of -11.5 that seated the 45 tile threw the front tile two
# frames later off her face - "one or the other needs adj"). nose u / v stay the FRONT landing's; these two slide the
# side-turn landing only, on the profile's own axes: turn_u + = forward, out of her face toward where she looks,
# turn_v + = down toward her chin. Same unit as nose u / v (pixels on the close-up, scaled with her head). 0 = the data.
# THE TURN RULES A FACE GETS WITHOUT A CONFIG (the User 2026-10-03, "if you can automate that is perfect for intake"):
# the nearest take wins the hand-over, the head landing, every profile library and the front tile bent (out, never in),
# the width cap, the front tile tilted where she has no tile, the lip reading off (marks are for position), and the
# nose attached as well as the lips. A face's mouth-turn.json names only what it wants otherwise.
TURN_DEFAULTS = {"from": "nearest", "landing": "head", "bend": True, "cap": "front", "no_tile": "tilt",
                 "gap": "front", "lip_size": False, "attach": "nose+lip", "lip_margin": 0.05}
# THE NECK CUT TICK WEARS THE JAW CUT (the User 2026-10-25, Bash on the library Composer: "reprogram neck cut toggle with
# your version. I have it checked but seem to get none"): the 09-10 neck cut stops the tile a margin under the chin and at
# the face's half-width, which on a desk shot cuts nothing he can see. Ticked, a face whose mouth-turn.json does not set
# them wears the kit's chin cut and jaw cut (lib/pack_turns' template, his "magical keystone" of 10-20); a face that sets
# its own keeps its own, so Crissa and Lila paint byte for byte as before. Kill: HOUSE_NECK_CUT_JAW=0.
NECK_CUT_JAW = os.environ.get("HOUSE_NECK_CUT_JAW", "1").strip().lower() not in ("0", "false", "off", "no")
NECK_CUT_JAW_RULES = {"chin_cut": {"lip_half": 0.5, "chin_half": 0.22, "fade": 0.06}, "front_cut": {"from": 20, "jaw": True}}
MOUTH_TURN_U = float(os.environ.get("HOUSE_MOUTH_TURN_U", "0"))
MOUTH_TURN_V = float(os.environ.get("HOUSE_MOUTH_TURN_V", "0"))
# SAY PAINTS A PAUSED FRAME (the User 2026-10-02: "Say needs to stamp ... even when paused or frame by frame"): on a
# bench, a line on air paints its shapes on the held frame, so a turn frame can be judged shape by shape. The 09-26
# rule (a held film takes no sheet) stays the default; a Stop still ends the painting with the line.
HELD_SAY_PAINTS = os.environ.get("HOUSE_HELD_SAY_PAINTS", "0").strip().lower() in ("1", "true", "yes", "on")
# A DRILL PASS ON A LOOP GOES ROUND UNTIL THE VOICE ENDS (the User 2026-10-03, the tuner on `work`: "was stopping
# halfway 10 times" - a 120-frame loop under a 9 s line ended every pass at "clip over" half way through the sentence).
# On, a LOOP film (inventory role loop) wraps to frame 0 at its end and the pass ends when the sheet goes off air;
# the drill log keeps a virtual frame (laps x n) so the score still lines up, and the answer key repeats per lap.
# A harvest take still ends at clip over (its words are the take's). Off = today, byte for byte; a bench drop-in.
DRILL_LOOP_WRAP = os.environ.get("HOUSE_DRILL_LOOP_WRAP", "0").strip().lower() in ("1", "true", "yes", "on")
# Dest-nose EMA per clip frame. Hopping pin: 0.35 ≈ 3 frames. 0 = off.
MOUTH_NOSE_EMA = float(os.environ.get("HOUSE_MOUTH_NOSE_EMA", "0.35"))
# One EMA'd nose per loop frame, looked up by index (2026-09-19); 0 = the live EMA that
# snaps to the raw nose on every dropped frame (poly_cache.nose_ema_track)
NOSE_EMA_TRACK = os.environ.get("HOUSE_NOSE_EMA_TRACK", "1").strip().lower() not in ("0", "false", "no", "off")
# THE MOUTH KEEPS UP WITH A FAST HEAD (2026-10-02, OFF by default: HOUSE_MOUTH_FAST_TRACK=1). Measured on a private
# player with the wall's settings, the same landmarker on the film frame and on the painted frame:
#   1. the nose EMA trails a moving head (k 0.35 = 1.86 frames of travel; 6-9 px on a 3-5 px/frame move), and the
#      pixel pin then corrects it only on the frames its match clears 0.90, so the tile jumps ~10 px in and out;
#   2. a cross-dissolve mixed in the outgoing tile where it stood on an EARLIER film frame, so through a turn a
#      second mouth trails behind hers for the length of the dissolve.
# On: the nose is smoothed with no trail (poly_cache.nose_ema_track zero_lag) and the outgoing picture of a
# dissolve travels with her own points frame by frame. No constant is fitted to a face or a clip.
# Each half has its own kill: HOUSE_MOUTH_FAST_TRACK_NOSE=0, HOUSE_MOUTH_FAST_TRACK_XFADE=0.
def _off(name: str) -> bool:
    return os.environ.get(name, "1").strip().lower() in ("0", "false", "no", "off")
MOUTH_FAST_TRACK = os.environ.get("HOUSE_MOUTH_FAST_TRACK", "0").strip().lower() in ("1", "true", "yes", "on")
NOSE_ZERO_LAG = MOUTH_FAST_TRACK and not _off("HOUSE_MOUTH_FAST_TRACK_NOSE")
XFADE_FOLLOW = MOUTH_FAST_TRACK and not _off("HOUSE_MOUTH_FAST_TRACK_XFADE")
# A TURNED HEAD IS LANDED BY GEOMETRY (the User 2026-09-27; head_frame.turn_correct). Default OFF,
# a bench drop-in first: the depth is fitted on Lila (0.09 pooled over six films), and a process with it on
# reads <stem>.mouth.turn.json before <stem>.mouth.json - the boxes on the frames turned under
# MAX_YAW, which stay blank in the sidecar every other process reads.
# ON by default since the User judged it on both faces (2026-09-27, "really great on the turn"), so a
# pack carries it. The depth is the FACE's, fitted on her own turning films: Lila 0.09, Crissa 0.06.
MOUTH_TURN_FIX = os.environ.get("HOUSE_MOUTH_TURN_FIX", "1").strip().lower() in ("1", "true", "yes", "on")
# THE TURN IS FILLED FROM THE FACE'S PROFILE LIBRARIES (the User 2026-09-29: "a back pocket way to have
# these always at the ready ... for any clip"). A frame's own head-track yaw picks its band
# (mouth_playlist.turn_band: HOUSE_TURN_FRONT_MAX / HOUSE_TURN_P45_MAX / HOUSE_TURN_FILL_SIDE /
# HOUSE_TURN_FILL_BANDS), and a frame in an allowed band takes its tile from the library named for that
# band (TURN_LIBRARIES, e.g. profile-45-r) instead of the clip's own index - no per-clip preparation.
# The box comes from <stem>.mouth.turnfill.json (scripts/pose-mouth-turnfill.py), a SEPARATE output read
# before the turn/mouth sidecars; a clip without one keeps its blank turn. Default OFF, a bench drop-in.
MOUTH_TURN_FILL = os.environ.get("HOUSE_MOUTH_TURN_FILL", "0").strip().lower() in ("1", "true", "yes", "on")
# A mirrored turn tile is its rel + MIRROR_TAG (HOUSE_TURN_FILL_MIRROR, band "45m"): the picture is flipped
# left-right on load and its head block by head_frame.mirror_head, so every painter reads one consistent tile.
MIRROR_TAG = "#mirror"
# GRID-CELL PINS BY ANGLE and LIPS ON A TURN (the User 2026-09-29: "save per-tile mouth pins ... keyed by
# turn angle", "per cell, by angle", "when the 45d flag triggers the nose pin switches to mouth").
# HOUSE_MOUTH_PIN_CELLS=1: a cell's rows in <pose-dir>/mouth-pins.json (lib/mouth_pins) shift the landed
# tile last, on every warp path, at the frame's yaw. HOUSE_MOUTH_TURN_PLACE=lips: a frame turned past
# TURN_FRONT_MAX with lip marks on both sides (<stem>.lipmarks.json) lands the tile by a similarity from
# its lips to hers instead of the nose. Both OFF by default; both report into /v1/state `lip_place`.
MOUTH_PIN_CELLS = os.environ.get("HOUSE_MOUTH_PIN_CELLS", "0").strip().lower() in ("1", "true", "yes", "on")
MOUTH_TURN_PLACE = os.environ.get("HOUSE_MOUTH_TURN_PLACE", "nose").strip().lower()
# THE FACE MASK ON A LIPS LANDING (the User 2026-09-29, "can't we make that background transparent?" ...
# "the oval is perfect there"): a tile placed by lips paints only inside BOTH face outlines - its own
# source frame's and hers (lipmarks/2 `hull`) - feathered by HOUSE_MOUTH_LIPS_FEATHER_S * s. The square
# alpha had carried the wall behind a deep profile into her frame as a pale rectangle. Only under
# HOUSE_MOUTH_TURN_PLACE=lips; kill HOUSE_MOUTH_LIPS_MASK=0.
MOUTH_LIPS_MASK = os.environ.get("HOUSE_MOUTH_LIPS_MASK", "1").strip().lower() not in ("0", "false", "off", "no")
MOUTH_LIPS_FEATHER_S = float(os.environ.get("HOUSE_MOUTH_LIPS_FEATHER_S", "0.02"))
# The jaw-line cut (front_cut {"jaw": true}, 2026-10-19): kill switch for every face at once.
MOUTH_JAW_CUT = os.environ.get("HOUSE_MOUTH_JAW_CUT", "1").strip().lower() not in ("0", "false", "off", "no")
# THE CUTS RUN ON THE PATCH'S OWN BOX (2026-10-23, the v2 desk bench at 1280x720: the chin and jaw cuts made ~10 whole-frame
# passes a paint, c_alpha_blend 5 -> 29 ms, and 62 % of the mouth steps were skipped). Each cut only multiplies the patch's
# alpha, so nothing outside its live box (grown by the feather's reach) can change: same pixels, a fraction of the work.
MOUTH_CUT_ROI = os.environ.get("HOUSE_MOUTH_CUT_ROI", "1").strip().lower() not in ("0", "false", "off", "no")
# A tile point's landing is searched in its maps' own box (Painter._landed, 2026-10-27); kill HOUSE_MOUTH_LANDED_ROI=0.
MOUTH_LANDED_ROI = os.environ.get("HOUSE_MOUTH_LANDED_ROI", "1").strip().lower() not in ("0", "false", "off", "no")
MOUTH_LIPS_GROW_S = float(os.environ.get("HOUSE_MOUTH_LIPS_GROW_S", "0.03"))
# The lips landing's SIZE: "head" = dest s / tile s (the painter's own ruler; turn and mouth shape do not move
# it), "lips" = fitted to the corners (inflated the AH on her left turn, the User 2026-09-29).
MOUTH_LIPS_SCALE = os.environ.get("HOUSE_MOUTH_LIPS_SCALE", "take").strip().lower()
# NOSE, CHIN AND THE JAW, AND HOLD THROUGH THE TURN (the User 2026-09-29, bun-wobble "lags ... jaw falls behind",
# "the chin and nose even? This way it could hold through the turn for face on", "check out that tearing"):
# the landing's rotation is fitted on lips + nose tip + chin, and the tile is stretched along her nose->chin
# axis to her own lip-to-chin (HOUSE_MOUTH_LIPS_JAW). With HOUSE_MOUTH_LIPS_ALL a clip that carries lip marks
# (the turning clips only) lands EVERY frame by them, facing us too - no hand-over pop at 12 degrees, no nose
# smoothing trailing a fast head - and the head-moving fade that let her filmed mouth tear through on fast
# motion is skipped on those frames (the marks are per frame; there is nothing to fade from).
MOUTH_LIPS_JAW = os.environ.get("HOUSE_MOUTH_LIPS_JAW", "1").strip().lower() not in ("0", "false", "off", "no")
MOUTH_LIPS_ALL = os.environ.get("HOUSE_MOUTH_LIPS_ALL", "0").strip().lower() in ("1", "true", "yes", "on")
# A lips landing paints the MOUTH, not the tile's lower face: her outer lip loop grown by this x s
# (HOUSE_MOUTH_LIPS_REGION_S; 0 = the whole face-mask area as before). Her own jaw and chin stay filmed.
MOUTH_LIPS_REGION_S = float(os.environ.get("HOUSE_MOUTH_LIPS_REGION_S", "0.10"))
# Tone the landed tile's skin to hers (lib/mouth_pins.match_skin; bun-wobble's heads-down shadow). Kill =0.
MOUTH_LIPS_SKIN = os.environ.get("HOUSE_MOUTH_LIPS_SKIN", "1").strip().lower() not in ("0", "false", "off", "no")
# ...on the painted box grown by 3 sigma, not the whole frame (the User 2026-10-18, the phone turn at under 4 fps: the
# full-frame tone match was 150 of a turn paint's 225 ms at 720x1280; match_skin_roi is the pitch fold's, 10-16). Kill =0.
MOUTH_SKIN_ROI = os.environ.get("HOUSE_MOUTH_SKIN_ROI", "1").strip().lower() not in ("0", "false", "off", "no")
# "take" (default): dest s / the take's OWN head track s at the tile's source frame x (painter px / film px) -
# one engine on both sides. Crissa's tile head blocks are an older fit that reads 8-10 % large against her
# MediaPipe frames (0.52 where the same image is 0.568), so "head" drew her tiles small; Lila's read 0.569.
# A profile take's frames under TURN_FRONT_MAX keep the filmed mouth (its index is side views only).
# the head-back fade (2026-10-16): degrees of head-back pitch (the head track's pitch, + = back) where the tile starts to
# fade and where it is gone. Kill HOUSE_MOUTH_HEADBACK_FADE=0.
MOUTH_HEADBACK_FADE = os.environ.get("HOUSE_MOUTH_HEADBACK_FADE", "1").strip().lower() not in ("0", "false", "off", "no")
HEADBACK_FROM = float(os.environ.get("HOUSE_MOUTH_HEADBACK_FROM", "10"))
HEADBACK_TO = float(os.environ.get("HOUSE_MOUTH_HEADBACK_TO", "20"))


def headback_k(pitch: float, lo: float | None = None, hi: float | None = None) -> float:
    """The tile's alpha factor at this head pitch: 1 up to lo, 0 from hi, a straight ramp between."""
    lo = HEADBACK_FROM if lo is None else float(lo)
    hi = HEADBACK_TO if hi is None else float(hi)
    if pitch <= lo:
        return 1.0
    if pitch >= hi or hi <= lo:
        return 0.0
    return (hi - float(pitch)) / (hi - lo)


# the pitch fold's skin match (2026-10-16): a desk tile folded onto a forward frame wears her light. Kill HOUSE_PITCH_FOLD_SKIN=0.
# round 28 (.101 2026-10-17, built in as-is): the fold hold, the turn band hold and the lift are a pack's defaults; the
# house keeps its mouths until it sets them (HOUSE_PITCH_FOLD_HOLD_S=0.5, HOUSE_TURN_BAND_HOLD=3, HOUSE_PITCH_LIFT=1).
# Round 29 (the User 2026-10-17: "smoothing, delays, hard coded anything ALWAYS comes back to bite us"): no hold in a
# pack either. Measured on listen (403 f): the track has no lag (playlist pitch vs the head track r 0.988 at offset 0),
# and at -16 the fold swaps 4 times a lap with the 0.5 s hold on or off at the same frames (f113, f181); the hold's only
# effect was a blip's second swap up to 12 f late. Round 28's holds by hand: HOUSE_PITCH_FOLD_HOLD_S=0.5,
# HOUSE_TURN_BAND_HOLD=3.
PITCH_FOLD_HOLD_S = float(os.environ.get("HOUSE_PITCH_FOLD_HOLD_S", "0") or 0)
PITCH_FOLD_RAW = os.environ.get("HOUSE_PITCH_FOLD_RAW", house_default("0", "1")).strip().lower() not in (
    "0", "false", "off", "no")
TURN_BAND_HOLD = float(os.environ.get("HOUSE_TURN_BAND_HOLD", "0") or 0)
PITCH_LIFT = os.environ.get("HOUSE_PITCH_LIFT", house_default("0", "1")).strip().lower() not in ("0", "false", "off", "no")
# .101 round 29 HER TILT MEASURED AGAIN (the User 2026-10-18: "0 tilt is face forward"; "if she tilts more it should get
# more negative"; "Your heads are not measuring correctly. Check your axes calcs"). <stem>.tilt.json drifts while her
# head is still (listen f201-f209 -16.8 -> -24.5, one pose by eye; MediaPipe 26.0-26.7) and jumps on a blink (f31-f33).
# The fold reads <stem>.pitch.json when the film has one: MediaPipe's facial transformation matrix per frame,
# -atan2(R21, R22), as measured (scripts/pose-pitch-track.py); else the tilt as before. A pack's default, the house
# off (like HOUSE_PITCH_FOLD_RAW). Kill HOUSE_PITCH_FOLD_MP=0.
PITCH_FOLD_MP = os.environ.get("HOUSE_PITCH_FOLD_MP", house_default("0", "1")).strip().lower() not in ("0", "false", "off", "no")
# The angle on that scale, both ways: the User's pick on .101's pick page (the same listen frames in the up and down
# jaw, one row per degree): "-14 up jaw", "-15 up", "-16 up", "-23 is first down" -> -22.5. Her desk tiles were filmed
# at -20..-26 on this scale (talk-down-template).
PITCH_FOLD_MP_AT = float(os.environ.get("HOUSE_PITCH_FOLD_MP_AT", "-22.5") or -22.5)
# NO "YEAH?" OVER HIS ASK (round 28, .101 2026-10-17 05:31: "Hey, Crissa." then "Help me plan dinner." a second later; her
# "Yeah?" went on the air while he was still talking). The close-up greet waits WAKE_GREET_HOLD_S after the close-up
# starts and is not said when the ear has posted /v1/greet-cancel since the wake (he went on speaking). A pack's
# default; the house says it at once. Kill: HOUSE_WAKE_GREET_HOLD_S=0.
WAKE_GREET_HOLD_S = float(os.environ.get("HOUSE_WAKE_GREET_HOLD_S", house_default("0", "0.8")) or 0)
PITCH_FOLD_REPICK = os.environ.get("HOUSE_PITCH_FOLD_REPICK", house_default("0", "1")).strip().lower() not in ("0", "false", "off", "no")
PITCH_FOLD_SKIN = os.environ.get("HOUSE_PITCH_FOLD_SKIN", "1").strip().lower() not in ("0", "false", "off", "no")
# THE BOW MAP (.101's bowed-harvest ask, the User 2026-10-24): no talking tile was filmed deeper than -28.8, so a bow
# past it (bun-wobble -37, the walk-ins -36) wore a stretched desk tile that detached, and a bowed turn wore a frontal
# tile warped 27-61 deg. scripts/pose-bow-map.py writes <stem>.bow.json per painting clip: per frame null (today's
# tiles reach it), a bow band's key (a harvest filmed bowed at that turn: its playlist is the fold source), or
# "filmed" (no tile reaches it: too deep, too high, turned past every band) = her filmed mouth. OFF by default; .101
# walks it with HOUSE_MOUTH_BOW=1. Rollback: the switch, or delete the *.bow.json files.
MOUTH_BOW = os.environ.get("HOUSE_MOUTH_BOW", "0").strip().lower() in ("1", "true", "yes", "on")
# NO REST TILE ON A BOWED FRAME (the User 2026-10-25 at :8251, the dip: "seeing double image on the turn. we need rest
# tile off"): a frame the bow map names (a band or "filmed") keeps her filmed mouth whenever the class is rest, idle or
# in a line. Follows HOUSE_MOUTH_BOW unless set; HOUSE_MOUTH_BOW_REST_FILMED=1 with BOW=0 is the front-tile A/B.
MOUTH_BOW_REST_FILMED = os.environ.get("HOUSE_MOUTH_BOW_REST_FILMED", "1" if MOUTH_BOW else "0").strip().lower() in (
    "1", "true", "yes", "on")
# TWO-TIER HITS (the User 2026-10-24: "the windup shape to a loud"): scripts/pose-windup-pairs.py makes a loud vowel's
# slot [windup, apex]. A live note lands at its own start (land = t0), so the series shows only its landing; with this
# on, a pair's note walks in from the head instead (mouth_playlist.series_index's live-note branch: the windup for a
# step, then the apex holds). OFF by default: a pair then paints its apex, as today.
MOUTH_WINDUP = os.environ.get("HOUSE_MOUTH_WINDUP", "0").strip().lower() in ("1", "true", "yes", "on")
PROFILE_FRONT_FILMED = os.environ.get("HOUSE_PROFILE_FRONT_FILMED", "1").strip().lower() not in ("0", "false", "off", "no")


def _unmirror(rel):
    s = str(rel or "")
    return (s[: -len(MIRROR_TAG)], True) if s.endswith(MIRROR_TAG) else (s, False)
MOUTH_TURN_DEPTH = float(os.environ.get("HOUSE_MOUTH_TURN_DEPTH", "") or (0.09 if "lila" in POSE_DIR.name.lower() else 0.06))
MOUTH_TURN_CHORD_DEG = float(os.environ.get("HOUSE_MOUTH_TURN_CHORD_DEG", "1.55"))
MOUTH_TURN_MAX_YAW = float(os.environ.get("HOUSE_MOUTH_TURN_MAX_YAW", "20"))

# THE LIBRARY CARRIES ITS DIALS (the User 2026-09-12: "make that a default …
# lock in new default somehow"). <POSE_DIR>/mouth-dials.json is the locked
# setup for that face; it boots here, under the env (an explicit env var is
# the operator's hand and still wins). /v1/mouth-tune {"lock": true} writes
# the live dials to it — the Composer's "winner" click — and {"default":
# true} puts the file's dials back live. Crissa's best (09-12) seeded both
# libraries: affine · take · nose-pixel · arch · nose 0/-8 · ema 0.35 · 1.0.
DIALS_FILE = POSE_DIR / "mouth-dials.json"
# fit_under_pixel (2026-09-12, the User: "rest matches frame and is the only
# performer … a chin or mouth pin ghost. Now it nose pixel only"): with the
# pixel pin, the rest tile is a translation on the SOURCE axes + the match;
# every other class was scaled by the dest track and the take-width fit
# first, then pinned — a second, invisible pin. False makes every class
# take the rest's path. Crissa's library keeps True (her takes are shot at
# different distances and fit=take is what collapsed that); Lila's is False.
MOUTH_FIT_UNDER_PIXEL = os.environ.get("HOUSE_MOUTH_FIT_UNDER_PIXEL", "1").strip().lower() not in ("0", "false", "off", "no")
MOUTH_PIN_HOLD = os.environ.get("HOUSE_MOUTH_PIN_HOLD", "0").strip().lower() in ("1", "true", "on", "yes")
# U / V AT EVERY ANGLE (the User 2026-10-03, frame 129 at -15 deg: "i cant scoot the tile like i could f155 ... almost all
# these angles should accept u, v adjustments"): on, the nose u / v nudge rides the landing whether or not the pixel pin
# took (the 09-11 rule rides a taken pin only, which is dead on exactly the frames that need a hand - the hand-over zone).
# A turn frame keeps turn fwd / down as its own pair (the 10-02 ruling: one u / v for both threw the front tile off her
# face two frames after it seated the 45). Off = the 09-11 rule, byte for byte.
MOUTH_NUDGE_ALL = os.environ.get("HOUSE_MOUTH_NUDGE_ALL", "0").strip().lower() in ("1", "true", "on", "yes")
# A FLAGGED LIP-MARK FILE IS NO LIP-MARK FILE (the User 2026-10-03, the lessons as rules): Intake writes the known-answer
# check into <stem>.lipmarks.json (`check.ok`; lib/lip_marks_qc); on, a take whose marks failed it is landed as if it had
# none (the pre-09-29 path), instead of by marks that read a closed mouth 1.4x thicker on one band. Off = report only.
LIP_MARKS_HONOUR_FLAG = os.environ.get("HOUSE_LIP_MARKS_HONOUR_FLAG", "0").strip().lower() in ("1", "true", "on", "yes")
# THE READINGS LOG (the same ask: "these could be fed back to aligner or tile picker and placer"): a bench writes every
# dial change it is sent, with the frame it was sent on (clip, frame, cell, yaw, pitch, how it landed, pin), to
# logs/tune-readings-<port>.jsonl - a hand's corrections per angle, the raw material for a placer that learns them.
MOUTH_TUNE_READINGS = os.environ.get("HOUSE_TUNE_READINGS", "1").strip().lower() not in ("0", "false", "off", "no")
# THE PIXEL PIN ON THE BÉZIER (the User 2026-09-13: "reverted to pre fix. Had to do with
# proper pinning"): pin=nose-pixel ran only on the affine family, so warp=cheeks
# landed by landmarks alone — Crissa's nose residual median 2 px, p90 5.9 (Lila 1.0 /
# 2.2). 0 = the unpinned Bézier as before.
MOUTH_CHEEKS_PIN = os.environ.get("HOUSE_MOUTH_CHEEKS_PIN", "1").strip().lower() not in ("0", "false", "off", "no")
# A PLAYLIST PARSE IS ONLY GOOD WHILE ITS FILE HAS NOT MOVED (2026-09-19).
# 0 = the old cache-for-the-life-of-the-process behaviour. See Library.playlist().
PL_MTIME = os.environ.get("HOUSE_PL_MTIME", "1").strip().lower() not in ("0", "false", "off", "no")


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(os.environ.get(name, "") or default)))
    except (TypeError, ValueError):
        return float(default)


# THE TILE-TUNING DIALS (the User 2026-09-12, "perfecting these tiles for Prod";
# audit docs/MOUTH-TILE-DIALS-20260913.md). Every one is live through
# /v1/mouth-tune, read back in /v1/state, locked with the winner, and boots
# from the library's mouth-dials.json under the env like the others.
#   xfade_ms      tile-to-tile cross-dissolve on every switch (0 = the hard
#                 cut that was live until today; 60 ≈ 1.5 clip frames).
#   edge_s        alpha ramp from the tile's have-edge inward, × dest s
#                 (head_frame.STAMP_EDGE_S; 0.10 was a restart-only constant).
#   sag_k         strength of the dental sag on warp=dental / plane
#                 (head_frame.SAG_K; 1.0 = as fitted, 0 = flat).
#   sheet_lead_ms whole-sheet lead at the sink (HOUSE_MOUTH_SHEET_LEAD_MS on
#                 the next Say — the landing lead, not the 100 ms prep window).
#   timing_lead_ms first-class pre-open onto pad REST (HOUSE_MOUTH_TIMING_LEAD_MS).
#   min_hold / cons_hold  hops a viseme / a consonant stays (mouth_timing).
# The four timing dials ride the env of the house-speak the Say spawns, so
# they take effect on the NEXT line, never mid-line.
MOUTH_XFADE_MS = _env_float("HOUSE_MOUTH_XFADE_MS", 0.0, 0.0, 400.0)
MOUTH_EDGE_S = _env_float("HOUSE_MOUTH_EDGE_S", 0.10, 0.0, 1.0)   # a fraction of her head size, 1.0 = the whole head (the User 2026-10-27: no guessed ceilings; 0.40 was one and his v2 lock sat on it)
MOUTH_SAG_K = _env_float("HOUSE_MOUTH_SAG_K", 1.0, 0.0, 2.0)
MOUTH_SHEET_LEAD_MS = _env_float("HOUSE_MOUTH_SHEET_LEAD_MS", 0.0, -200.0, 600.0)
# THE DIAL ALREADY MEANT FRAMES; NOW IT IS STORED AS FRAMES (the User 2026-09-22:
# "we want an oroborous in frames and no real time"). speak_timing_env has said
# so for days — "The dial means 'N frames at 1x'" — and then handed it over as
# effective_sheet_lead_ms() / PLAY_RATE, i.e. a frame count ENCODED in
# milliseconds-at-1x and decoded on the way out. A wall duration that is only
# true at one playback rate is a fiction, and it is the fiction that five of the
# six category-D bugs in MOUTH-CLOCK-REVIEW-20260922 are corrections for: every
# one of them is a missing or wrong `/ PLAY_RATE` on a value that should never
# have been in wall time.
#
# Stored in semiframes, the number means the same thing at every rate, and the
# single remaining conversion — frames to this rate's wall seconds, at the sink
# where the sheet meets audio — is an honest unit change rather than undoing an
# encoding.
#
# ON A FLAG, because snapping an existing lock to the grid MOVES HER LEAD: the
# locked 200 ms is 9.6 semiframes, and the nearest legal step is 10 (208.33 ms).
# That is a real change to a live dial, so HOUSE_MOUTH_LEAD_SEMIS defaults OFF
# and prod keeps the exact millisecond until it is flipped by hand.
LEAD_SEMIS = os.environ.get("HOUSE_MOUTH_LEAD_SEMIS", "0").strip().lower() not in ("0", "false", "no", "off")


def semi_ms() -> float:
    """One semiframe of the FILM, in milliseconds. The only place the clip rate
    touches the lead, and it carries no playback rate: a frame is a frame."""
    from mouth_timing import SLOTS_PER_FRAME
    return (1000.0 / float(CLIP_FPS)) / SLOTS_PER_FRAME


def lead_semis() -> float:
    """The dial as what it has always meant: a count of half-frames."""
    return float(MOUTH_SHEET_LEAD_MS) / semi_ms()


def snap_lead_ms(v: float) -> float:
    """A millisecond lead onto the grid the box can actually show. Off the flag
    this is identity, so nothing moves until the User asks for it."""
    if not LEAD_SEMIS:
        return float(v)
    st = semi_ms()
    return round(float(v) / st) * st
# THE ZERO IS CALIBRATED (the User 2026-09-18: "standard times … 10 ms lead for
# sound and 100 more for hard consonants … make them real though magic of
# calibration"): sync_zero_ms is the whole pipeline's offset — the lead at
# which a drill reads lag 0 — kept UNDER the dial in the library file, so the
# dial is the artistic lead only and 0 means in sync on the glass. Measured
# by "calibrate zero" (mouth-drill-optimize --calibrate-zero), refused below
# r 0.35 (the law). Not a Best's key; the lock writes it. HOUSE_MOUTH_SYNC_ZERO_MS boots it.
SYNC_ZERO_MS = _env_float("HOUSE_MOUTH_SYNC_ZERO_MS", 0.0, -400.0, 600.0)


def lead_base_ms() -> float:
    """dial + the calibrated zero: the library's own lead, before this machine's lip sync."""
    return float(MOUTH_SHEET_LEAD_MS) + float(SYNC_ZERO_MS)


_GLASS_FOLLOW_SAID: dict = {}


def effective_sheet_lead_ms() -> float:
    """dial + the calibrated zero: what a line is actually stamped with.

    THIS MACHINE'S LIP SYNC (the User 2026-10-11, before the .101 demo): a player adds the offset the Crissa menu keeps
    in <state home>/lip-sync.json (lib/crissa_lipsync, mtime-cached, so her next line uses it with no restart), held
    inside house-speak's clamp on a line it asks the player for, so her replies and the player's own lines agree. No
    file (the house) adds nothing and the number is exactly what it was. A bench never adds it: a bench measures the
    library's timing, and a speaker's delay is not the library's."""
    v = lead_base_ms()
    off = 0.0 if IS_BENCH else crissa_lipsync.offset_ms()
    if not IS_BENCH:
        # round 45 (.101 round 44, "+500 while the screens load, then the 270"): the desk Wall's live lag past the lag
        # the value was set on, in half frames (lib/crissa_lipsync THE LEAD FOLLOWS THE GLASS)
        try:
            gf = crissa_lipsync.glass_follow(glass_lag_state())
            off += float(gf.get("ms") or 0.0)
            if gf.get("halves") != _GLASS_FOLLOW_SAID.get("halves"):
                _GLASS_FOLLOW_SAID["halves"] = gf.get("halves")
                print("[lip sync] follows the desk Wall's glass: %+.1f ms (%s %s ms now, %s ms %s)" % (
                    float(gf.get("ms") or 0.0), str(gf.get("stat") or "").replace("_ms", ""), gf.get("live"),
                    gf.get("ref"), gf.get("how")), flush=True)
        except Exception:  # noqa: BLE001  never a line without its lead
            pass
    if off:
        return crissa_lipsync.total_ms(v, off)     # held to house-speak's clamp (round 36: down to -625)
    return max(-400.0, min(800.0, v))
MOUTH_TIMING_LEAD_MS = _env_float("HOUSE_MOUTH_TIMING_LEAD_MS", 120.0, 0.0, 400.0)
# A hold may be ZERO hops (the User 2026-10-25: "make them all accept zero frames as a minimum ... no hard coded anythings"):
# 0 = no merge at all, every scheduled hit paints for its own slots; the ceiling is the Composer's HOP_CEIL (36 hops).
MOUTH_MIN_HOLD = int(_env_float("HOUSE_MOUTH_MIN_HOLD", 3, 0, 36))
MOUTH_CONS_HOLD = int(_env_float("HOUSE_MOUTH_CONS_HOLD", 5, 0, 36))
# THE EDGE PAD (the User 2026-09-22): a word's FIRST shape may start early, into
# the silence in front of it, and only it. Her own closures dwell 3.23 f inside
# a phrase and 5.50 f sentence-initially while the release sits 1.56 f before
# the burst in both — so the extra is on the front, and the pad grows backwards.
# In hops like the holds; the Composer shows frames. 0 = off, which is the
# default: nothing changes until they are turned.
MOUTH_SENT_PAD = int(_env_float("HOUSE_MOUTH_SENT_PAD", 0, 0, 36))
MOUTH_WORD_PAD = int(_env_float("HOUSE_MOUTH_WORD_PAD", 0, 0, 36))
# THE ELASTIC PAUSE (the User 2026-09-24): a gap of at least pause_words x the line's own
# median word takes the closed rest, a shorter one the open rest, whatever the punctuation;
# a gap under blip_words median words holds the shape before it. In WORDS, not frames, so
# it stretches with speech rate (1.7 = 12 f at a normal 7 f word). 0 = off, the default.
MOUTH_PAUSE_WORDS = _env_float("HOUSE_MOUTH_PAUSE_WORDS", 0, 0, 1e9)   # floor 0, no ceiling (the User 2026-10-27: "dials have those silly limits")
MOUTH_BLIP_WORDS = _env_float("HOUSE_MOUTH_BLIP_WORDS", 0, 0, 1e9)
# a word-final vowel's tail, in the line's median words (0.4 = the User's value)
MOUTH_TAIL_WORDS = _env_float("HOUSE_MOUTH_TAIL_WORDS", 0, 0, 1e9)
# tempo: the atempo the room hears. The house pins 1.0 on the conductor
# (the User 2026-10-08). A named HOUSE_SPEAK_TEMPO still wins. Slow-motion
# (PLAY_RATE) multiplies it where each line is handed over (speak_timing_env).
MOUTH_TEMPO = _env_float("HOUSE_SPEAK_TEMPO", 1.0, 0.6, 1.6)
# PLAYBACK RATE (the User 2026-09-13 02:0x: "wire the playback speed buttons to
# slow this playback"): one multiplier on the body clip's advance AND on the
# next Say's atempo (tempo × rate; house-speak chains atempo stages below
# 0.5). 1.0 is live; ½ ¼ ⅛ are the Composer's speed buttons. Not a dial to
# lock — an inspection glass. /v1/mouth-tune {"rate": 0.5}; state play_rate.
PLAY_RATE = 1.0
# THE SCRUB HOLD (2026-09-24): POST /v1/scrub {"frame": n} holds this playhead on frame n
# (painted from the plan); {"frame": null} lets it run again at PLAY_RATE.
SCRUB: dict = {"key": None, "frame": None}
# TILES DRILL — the pacer car (the User 2026-09-13 05:2x: "letting spoken video
# play with mouth tiles is very revealing … practice on the clip on which
# mouths were cut … it could measure its own accuracy"). /v1/drill {"key",
# "text"}: the harvest film plays on this conductor, locked frame-for-frame
# to its OWN voice (house-speak --wav, aligned whole-line, tempo 1), tiles
# paint over her filmed mouth, and every painted frame is logged. At the end
# the tiles' openness is correlated against the film's per-frame openness
# from map.json: the best lag is how early or late the tiles run, in ms.
DRILL: dict = {"on": False, "key": None, "text": "", "armed": 0.0, "t0": None,
               "log": [], "report": None, "proc": None, "n": 0,
               "loop": False, "passes": 0, "path": None, "who": None,
               # THE FILM'S WORDS COME FROM THE FILM (the User 2026-09-16: "it still
               # reads the textbox"): reading = a transcription is in flight,
               # line_src = override | timeline | stt, needs = what this film has
               "reading": False, "line_src": None, "words_error": None, "needs": None}
# Kill switch: off = both /v1/drill and /v1/say {src:clip} require `text` again.
DRILL_FILM_WORDS = os.environ.get("HOUSE_DRILL_FILM_WORDS", "1") != "0"
DRILL_STAPLE = ("Lips pressed shut for m. Upper teeth on the lower lip for f. Tongue tip between the teeth for th. "
                "Tongue up behind the teeth for d. Tongue tip up jaw a little open for l. Lips slightly rounded jaw nearly shut for r. "
                "Jaw a little open tongue back for k. Teeth nearly together lips drawn back for s. Lips pushed forward and squared for sh. "
                "Wide smile teeth showing for ee. Half open and relaxed for eh. Lips rounded jaw mid for er. Jaw open relaxed tongue low for ah. "
                "Lips rounded and forward for oh. A small tight round opening for oo. Narrow smile jaw nearly shut for y. "
                "Jaw open relaxed a breath out for h. Then lips shut again.")
DRILL_WORDS_FROM_TIMELINE = os.environ.get("HOUSE_DRILL_TIMELINE_WORDS", "1") != "0"   # kill: the aligner path as before
DRILL_TEXT_CAP = 800   # house-speak's own MOUTH_TEXT_CAP; a 50 s film's transcript is ~600 chars


AUTOTUNE: dict = {"proc": None, "log": None, "started": None, "key": None, "mode": None,
                  "rate_was": None}

# ONE OWNER FOR THE TRANSPORT (the User 2026-09-25). Between 09-24 and 09-25 the
# Composer's player took eleven one-symptom patches (7c102fad e52b7c03 608141ab
# 7a7e362a 702fe908 0ffd896f ebfc84d3 adeba211 13669dde e5e23fa5 a1338e52), each
# answering one complaint on the bench and each leaving the others. One cause:
# this conductor holds the truth (SCRUB, the pass's park, DRILL, the AutoTuner
# process, PLAY_RATE, the held class) and the page kept copies (LIVE, PSCRUB,
# DRILLLIVE, DRILLING, AT, HOLDING) refreshed by four polls, so every control
# guessed from a copy.
#
# The bench publishes ONE block, /v1/state.transport (transport_state), and takes
# ONE intent route, POST /v1/transport {verb} (transport_verb, the pure resolver in
# lib/bench_transport.py). Under the same switch the internal law fixes below go
# live: SCRUB and the park are released TOGETHER and per clip (transport_release,
# hold_key), a pass frees a held class, the tuner is stopped WHOLE and waited for,
# the tuner owns the dials while it runs, a loop-default write is refused while a
# guest's dials are live, a winner is offered only for the run that made it, and
# the tick that ends a pass does not advance the film.
# Default OFF = byte-for-byte today's behaviour. Kill: HOUSE_COMPOSER_TRANSPORT=0.
COMPOSER_TRANSPORT = os.environ.get("HOUSE_COMPOSER_TRANSPORT", "0").strip().lower() in ("1", "true", "yes", "on")
# THE TUNER'S FILM SOLVE (landing B, 2026-09-25): ON spawns scripts/mouth-drill-optimize-film.py
# (the known slope in film frames at the drill's pace; a stop restores the dials), OFF spawns
# scripts/mouth-drill-optimize.py — today's tuner, untouched. Two files, because a spawned
# script takes a commit at once: the tuner is run from disk per run, so the fixes could not
# sit in the old path without going live under a bench nobody restarted or locked.
TUNER_FILM_SOLVE = os.environ.get("HOUSE_TUNER_FILM_SOLVE", "0").strip().lower() in ("1", "true", "yes", "on")
TUNER_FILM = ROOT / "scripts" / "mouth-drill-optimize-film.py"
BOOT_T = time.time()          # the page reads a change as "the bench restarted"
TUNER_WHO = "drill-optimizer"  # the one caller that may move a dial mid-run (Bench.set)
# THE DIALS A RUN TURNS. Snapshotted when /v1/autotune starts and put back by the bench
# itself when the run ends without reporting (no `applied:` / `restored` line): the first
# acceptance run (e158960d) stopped a run of today's tuner, which has no stop handler, and
# left sheet_lead -125 ms on the bench until a hand restored the default — every graded
# pass for twenty minutes ran 3 f late. A stop restores under EITHER tuner now.
TUNER_DIALS = ("sheet_lead_ms", "min_hold", "cons_hold", "xfade_ms", "sent_pad", "word_pad")


def tuner_alive() -> bool:
    p = AUTOTUNE.get("proc")
    return p is not None and p.poll() is None


def tuner_script() -> Path:
    if TUNER_FILM_SOLVE and TUNER_FILM.is_file():
        return TUNER_FILM
    return ROOT / "scripts" / "mouth-drill-optimize.py"


def tuner_owns_dials(body: dict | None, who: str) -> bool:
    """While the AutoTuner runs the dials are its (faults 11 and 18: a speed click or a
    lead-fix apply mid-run measured the rest of the run at the wrong setting). The empty
    body is the speech path's READ and is never refused; a body carrying only `who` is a
    read too. Mirrors bench_transport.dials_owned, kept inline because this is called
    from the mouth-tune write path, which the package gate does not count as authoring."""
    if not tuner_alive() or not body:
        return False
    if str(who or "") == TUNER_WHO or str(body.get("who") or "") == TUNER_WHO:
        return False
    return any(k != "who" for k in body)


def transport_release(why: str, who: str = "transport") -> None:
    """SCRUB and the pass's park go together, always (faults 4, 5, 13, 14: five callers
    cleared one, some the other, none both, and a hold outlived its clip)."""
    had = SCRUB.get("frame") is not None or bool(DRILL.get("hold_frame"))
    SCRUB.update({"key": None, "frame": None})
    DRILL["hold_frame"] = False
    DRILL["hold_key"] = None
    if had:
        action("transport", "released — " + why, who)


def _transport_mode(tuner_on: bool, drill_on: bool, scrub, hold: bool) -> str:
    """bench_transport.mode_of, inline: /v1/state is not an authoring route, so the
    module is not imported here (tests/test_bench_transport.py pins the two equal)."""
    if tuner_on:
        return "tuning"
    if drill_on:
        return "drilling"
    if scrub is not None:
        return "held"
    if hold:
        return "paused"
    return "playing"


def transport_state() -> dict | None:
    """The one block the page renders from."""
    ph = STATE.get("playhead")
    if ph is None:
        return None
    key = ph.key
    arr = ph.lib.frames(key) if key else None
    of = len(arr) if arr is not None else 0
    tuner_on = tuner_alive()
    drill_on = bool(DRILL.get("on"))
    scrub = SCRUB.get("frame") if (SCRUB.get("frame") is not None and SCRUB.get("key") == key) else None
    hold = bool(DRILL.get("hold_frame")) and (DRILL.get("hold_key") in (None, key)) and not drill_on
    m = STATE.get("mouth")
    force = getattr(m, "force", None)
    fclass = getattr(m, "force_class", None)
    frame = int(ph.i)
    return {
        "boot": BOOT_T, "clip": key, "frame": frame, "of": of, "at_end": bool(of and frame >= of - 1),
        "rate": PLAY_RATE, "rate_locked": tuner_on,
        "mode": _transport_mode(tuner_on, drill_on, scrub, hold),
        "hold_frame": hold, "scrub": scrub,
        # "a held class is not a line", and neither is a held film: decided here, once, not on the page
        "on_air": bool(getattr(m, "speaking", False)) and force is None and fclass is None and scrub is None and not hold,
        "speaker": str(getattr(m, "speaker", "") or "") or None,
        "hold_class": fclass,
        "drill": {"on": drill_on, "clip": DRILL.get("key"), "loop": bool(DRILL.get("loop")),
                  "passes": int(DRILL.get("passes") or 0), "pacer": bool(DRILL.get("pacer_on")),
                  "mute": bool(DRILL.get("mute"))},
        "tuner": {"on": tuner_on, "clip": AUTOTUNE.get("key"), "mode": AUTOTUNE.get("mode"),
                  "run_id": AUTOTUNE.get("run_id"), "started": AUTOTUNE.get("started"),
                  "film": bool(AUTOTUNE.get("film"))},
    }


def _winner_is_this_runs(d: dict) -> bool:
    """A winner is offered only for the run that made it (fault 9: after a restart
    any film's newest result was offered, and after a calibrate 'apply' overwrote
    the sheet_lead 0 the calibrate had just set)."""
    if d.get("stopped") or AUTOTUNE.get("mode") == "calibrate":
        return False
    if d.get("key") != AUTOTUNE.get("key"):
        return False
    rid = AUTOTUNE.get("run_id")
    return (not rid) or (not d.get("run_id")) or d.get("run_id") == rid


def autotune_reported() -> bool:
    """Did the run's own log say what it did with the dials? (`applied:` / `restored` / a
    calibrate's `ZERO CALIBRATED` / `NOT CALIBRATED` / `LEAD NOT WRITTEN`, leading spaces
    stripped — review 2026-09-26.)"""
    try:
        lines = Path(AUTOTUNE["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
    except (OSError, TypeError, KeyError):
        return False
    return any(l.strip().startswith(("applied:", "restored", "ZERO CALIBRATED", "NOT CALIBRATED", "LEAD NOT WRITTEN"))
               for l in lines)


def autotune_restore_if_unreported(who: str = "autotune") -> bool:
    """A run that ended without reporting leaves the dials where its last pass put them.
    The bench puts its own snapshot back, once.

    NEVER AFTER A CALIBRATE (review, 2026-09-26): a calibrate writes its own
    two dials — sync_zero_ms to the measured zero and sheet_lead_ms to 0 — and printed neither
    word this looked for, so the bench put the old lead back under the new zero and the lead
    was counted twice. A calibrate reports in its own words and is never restored."""
    base = AUTOTUNE.get("base")
    if not isinstance(base, dict) or not base:
        return False
    AUTOTUNE["base"] = None
    if AUTOTUNE.get("mode") == "calibrate" or autotune_reported():
        return False
    apply_dials({k: v for k, v in base.items() if v is not None}, honour_env=False)
    action("autotune", "the run did not report — the starting dials are back (%s)" % ", ".join(
        "%s %s" % (k, base.get(k)) for k in TUNER_DIALS if base.get(k) is not None), who)
    return True


def autotune_stop(who: str = "composer") -> None:
    """Stop the AutoTuner WHOLE and wait for it (fault 7: the stop answered 'still
    running' because the process had not been reaped, and the page re-armed itself)."""
    global PLAY_RATE
    p = AUTOTUNE.get("proc")
    if p is not None and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass
        try:
            p.wait(timeout=4.0)      # the optimizer's SIGTERM handler puts the dials back in here
        except Exception:  # noqa: BLE001
            try:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=1.0)
            except Exception:  # noqa: BLE001
                pass
    if DRILL.get("on"):
        drill_stop("autotune stopped")
    if AUTOTUNE.get("rate_was") is not None:
        PLAY_RATE = float(AUTOTUNE["rate_was"])
        AUTOTUNE["rate_was"] = None
        action("autotune", "rate back to %g" % PLAY_RATE, who)
    autotune_restore_if_unreported(who)
    action("autotune", "stopped whole", who)


def transport_apply(ph, act: str, arg, who: str) -> None:
    """One action of a transport plan, against the globals."""
    global PLAY_RATE
    if act == "stop_tuner":
        autotune_stop(who)
    elif act == "stop_drill":
        if DRILL.get("on"):
            drill_stop(str(arg or "transport"))
    elif act == "stop_say":
        say_stop_all()
    elif act == "release":
        transport_release(str(arg or "transport"), who)
    elif act == "hold":
        SCRUB.update({"key": ph.key, "frame": int(arg)})
        DRILL["hold_frame"] = False
        DRILL["hold_key"] = None
        # AT ONCE, not on the next tick (e158960d: a read straight after the seek answered
        # still showed the old frame — the render loop pins to SCRUB one tick later)
        arr = ph.lib.frames(ph.key) if ph.key else None
        if arr:
            ph.i = max(0, min(len(arr) - 1, int(arg)))
            ph.i_at = time.time()
            ph._clip_accum = 0.0
    elif act == "rewind":
        # the pacer's own three lines: a fresh lap from frame 0 on the next tick
        ph.i = 0
        ph.i_at = time.time()
        ph._clip_accum = 0.0
    elif act == "rate_back":
        if AUTOTUNE.get("rate_was") is not None:
            PLAY_RATE = float(AUTOTUNE["rate_was"])
            AUTOTUNE["rate_was"] = None
            action("autotune", "rate back to %g" % PLAY_RATE, who)
    elif act == "hand_back":
        AUTOTUNE["hand_back"] = False


def transport_verb(ph, body: dict, who: str) -> tuple[int, dict]:
    """POST /v1/transport: snapshot first, plan, apply in order, answer the truth."""
    import bench_transport
    snap = transport_state() or {}
    # the flat flags beside the block's own (plan() reads either; e158960d read neither)
    snap["tuner_on"] = bool((snap.get("tuner") or {}).get("on"))
    snap["drill_on"] = bool((snap.get("drill") or {}).get("on"))
    code, acts, out = bench_transport.plan(str(body.get("verb") or ""), body, snap)
    seen = "saw %s tuner=%s drill=%s scrub=%s park=%s f%s%s" % (
        snap.get("mode"), snap["tuner_on"], snap["drill_on"], snap.get("scrub"), snap.get("hold_frame"),
        snap.get("frame"), " force" if body.get("force") else "")
    if code != 200:
        out["transport"] = snap
        action("transport", "%s refused %d — %s" % (body.get("verb"), code, seen), who)
        return code, out
    for act, arg in acts:
        transport_apply(ph, act, arg, who)
    action("transport", "%s → %s — %s" % (out.get("verb"), ", ".join(a for a, _ in acts) or "nothing", seen), who)
    out["transport"] = transport_state()
    if out.get("scrub") is not None and ph.key:
        out["class"] = plan_class_at(ph.key, int(out["scrub"]))
    return 200, out


def autotune_state() -> dict:
    global PLAY_RATE
    p = AUTOTUNE.get("proc")
    on = p is not None and p.poll() is None
    if not on and AUTOTUNE.get("rate_was") is not None:
        # the run is over (finished, crashed or was killed): his inspection
        # glass comes back. Once — rate_was is cleared with it.
        PLAY_RATE = float(AUTOTUNE["rate_was"])
        AUTOTUNE["rate_was"] = None
        action("autotune", "rate back to %g" % PLAY_RATE, "autotune")
    if not on and AUTOTUNE.get("hand_back"):
        # THE FILM IS THE PLAYER'S AGAIN (the User 2026-09-22: "clips strip not
        # moving after tune. Does it hand control back to player when done? I
        # can't inspect the stamping").
        #
        # It did not. Every pass ends in drill_stop, which parks the film on its
        # last frame (the 09-13 law, so the tile pasted on that frame can be
        # seen) and is released only by the next play, cue or home. A RUN is
        # dozens of passes, so the last park had no inspection value and nothing
        # came after it: the bench sat on 240 of 241 with the render loop pinning
        # `i` there on every iteration, and the Composer's strip — which follows
        # the bench's frame while Live is on — was pinned with it. Dragging it
        # snapped straight back.
        #
        # Releasing the hold is the whole fix: a bench loops a camped clip from
        # frame 0 by itself ("holding means looping from frame 0, which is what a
        # tuning stage wants"), so the next advance starts a fresh lap. The
        # per-pass hold is untouched — a single match game still parks.
        # Kill: HOUSE_TUNER_HANDS_BACK=0.
        AUTOTUNE["hand_back"] = False
        if TUNER_HANDS_BACK and DRILL.get("hold_frame") and not DRILL.get("on"):
            DRILL["hold_frame"] = False
            DRILL["hold_key"] = None
            action("autotune", "run over — the film is the player's again", "autotune")
        if COMPOSER_TRANSPORT:
            autotune_restore_if_unreported("autotune")
    lines: list = []
    try:
        if AUTOTUNE.get("log"):
            lines = Path(AUTOTUNE["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-60:]
    except OSError:
        lines = []
    applied = next((l for l in reversed(lines) if l.startswith("applied:") or l.startswith("restored")), None)
    # THE WINNER, AS DIALS (the User 2026-09-16: "I need a button to apply the
    # AutoTuner settings"): the tuner applies them itself at the end, but a
    # best saved mid-run or a later dial change loses them. Read from the
    # result file the tuner names ("log: <path>"), so the page can re-apply.
    winner, winner_key = None, None
    res = next((l[4:].strip() for l in reversed(lines) if l.startswith("log: ")), None)
    if not res and not on and not COMPOSER_TRANSPORT:
        # across a restart the tuner's own log is gone; the newest result file
        # is still the last run (the timing dials are global, not per film)
        # -- under the transport a winner is this run's or nobody's (fault 9)
        files = sorted((ROOT / "logs").glob("drill-opt-*.json"))
        res = str(files[-1]) if files else None
    if res:
        try:
            d = json.loads(Path(res).read_text(encoding="utf-8"))
            if isinstance(d.get("winner"), dict) and (
                    _winner_is_this_runs(d) if COMPOSER_TRANSPORT else (AUTOTUNE.get("key") in (None, d.get("key")))):
                winner = {k: v for k, v in d["winner"].items() if v is not None}
                winner_key = d.get("key")
        except (OSError, ValueError):
            winner = None
    return {"on": on, "key": AUTOTUNE.get("key"), "mode": AUTOTUNE.get("mode"), "started": AUTOTUNE.get("started"),
            "run_id": AUTOTUNE.get("run_id"),
            "lines": lines, "applied": applied, "winner": winner, "winner_key": winner_key,
            "rc": (None if on or p is None else p.returncode)}


# REPIN FRAMES FROM THE TUNE BOX (the User 2026-09-14: "Could I add that edit feature
# to do in the Tune box? Repin frames?"): re-fit the on-air film's head track —
# nose, chin, u/v, 2d106 cheeks, the pins every warp lands on — with
# scripts/pose-head-frame.py --clips <stem>, the old track checkpointed under
# display/museum/repin-<date>/, then the loop's track + every mouth cache dropped
# so the next paint reads the new pins. Tiles (map.json heads) stay the Takes
# page's job: that pass needs the marking staff stopped.
REPIN_CPU_QUOTA = (os.environ.get("HOUSE_REPIN_CPU_QUOTA") or "600%").strip()
REPIN: dict = {"proc": None, "log": None, "started": None, "key": None, "stem": None,
               "note": None, "checkpoint": None, "before": None, "stopped": False}


def _repin_progress(lines: list) -> tuple[int, int] | None:
    """Last `PROGRESS i/n` line from pose-head-frame (Composer countdown)."""
    for line in reversed(lines or []):
        s = str(line).strip()
        if not s.startswith("PROGRESS "):
            continue
        try:
            a, b = s.split()[1].split("/")
            done, total = int(a), int(b)
            if total > 0 and 0 <= done <= total:
                return done, total
        except (IndexError, ValueError):
            continue
    return None


def repin_state() -> dict:
    p = REPIN.get("proc")
    on = p is not None and p.poll() is None
    lines: list = []
    try:
        if REPIN.get("log"):
            lines = Path(REPIN["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-24:]
    except OSError:
        lines = []
    prog = _repin_progress(lines)
    n = int(REPIN.get("n") or 0) or (prog[1] if prog else 0)
    done = prog[0] if prog else None
    eta_s = None
    started_at = REPIN.get("started_at")
    if on and done and n and started_at and done > 0:
        elapsed = max(0.0, time.time() - float(started_at))
        eta_s = round(max(0.0, (elapsed / done) * (n - done)))
    return {"on": on, "key": REPIN.get("key"), "stem": REPIN.get("stem"), "started": REPIN.get("started"),
            "started_at": started_at, "done": done, "n": n or None, "eta_s": eta_s,
            "rc": (None if on or p is None else p.returncode), "note": REPIN.get("note"),
            "stopped": bool(REPIN.get("stopped")),
            "checkpoint": REPIN.get("checkpoint"), "lines": lines}


def _repin_diff(before: list | None, after: list | None) -> str:
    """One line for the box: frames, and how far the nose moved."""
    try:
        if not after:
            return "no track written"
        if not before:
            return "%d frames fit (no earlier track)" % len(after)
        d = []
        for a, b in zip(before, after):
            if a and b and a.get("nose") is not None and b.get("nose") is not None:
                na = np.asarray(a["nose"], dtype=np.float64).ravel()[:2]
                nb = np.asarray(b["nose"], dtype=np.float64).ravel()[:2]
                d.append(float(np.linalg.norm(na - nb)))
        if not d:
            return "%d frames fit" % len(after)
        return "%d frames re-fit · nose moved median %.1f px, p90 %.1f" % (
            len(after), float(np.median(d)), float(np.percentile(d, 90)))
    except Exception:  # noqa: BLE001
        return "%d frames" % len(after or [])


REPIN_FOUND_MIN = 0.90   # head on at least this share of frames, or roll back


def repin_ok(before: list | None, after: list | None, rc: int | None) -> tuple[bool, str]:
    """Did the fitter actually produce a usable track? (2026-09-15)

    `rc == 0 and after` was not enough. A list of nulls is truthy, so a pass
    that found her face on NO frame read as success, dropped the caches and
    printed "N frames re-fit" — and the loop then painted by the Haar box
    with the box saying it had just been re-pinned. Three conditions now:
    the fitter exited clean, the track is the same length as the one it
    replaced (a short track means it stopped early), and she was found on
    REPIN_FOUND_MIN of the frames.
    """
    if rc != 0:
        return False, "fitter failed (rc %s) — the old track stands" % rc
    if not after:
        return False, "fitter wrote no track — the old one stands"
    if before and len(after) != len(before):
        return False, ("fitter wrote %d frames where the old track had %d — "
                       "stopped early; old track restored" % (len(after), len(before)))
    got = sum(1 for r in after if r)
    share = got / float(len(after))
    if share < REPIN_FOUND_MIN:
        return False, ("fitter lost her head on %d of %d frames (%.0f%% found) — "
                       "old track restored" % (len(after) - got, len(after), 100.0 * share))
    return True, ""


def repin_watch(ph, bank: Path, stem: str) -> None:
    """Wait for the fitter, then hand the new pins to the painter."""
    p = REPIN.get("proc")
    if p is None:
        return
    p.wait()
    rc = p.returncode
    if REPIN.get("stopped"):
        rc = rc if rc == 0 else None
    track = bank / (stem + ".head.json")
    after = None
    try:
        after = json.loads(track.read_text()).get("frames") or []
    except (OSError, ValueError):
        after = None
    good, why = repin_ok(REPIN.get("before"), after, p.returncode)
    if REPIN.get("stopped"):
        good, why = False, "repin stopped — the old track stands"
    if good:
        lib = ph.lib
        with lib._lock:
            lib._tracks.pop("hf:" + stem, None)
            lib._track_bad.pop("hf:" + stem, None)
        try:
            ph._loop_ver_val = None      # the head.json version is in every cache key
            ph._take_k = {}
            # the scene, the pin hysteresis and the dest-nose EMA were all
            # measured against the OLD pins (2026-09-15)
            ph._scene = None
            # a DICT, not None: _pixel_pin indexes it, and None killed the
            # paint thread on the next nose-pixel pin (bench 2026-09-16 15:25)
            ph._pin_last = {}
            ph._dst_nose_ema = None
        except Exception:  # noqa: BLE001
            pass
        reset_mouth_caches()
        REPIN["note"] = _repin_diff(REPIN.get("before"), after)
        action("repin", "%s: %s" % (stem, REPIN["note"]), "fitter")
    else:
        # PUT THE OLD TRACK BACK (2026-09-15). The checkpoint under
        # display/museum/repin-<date>/ was taken before the fitter started,
        # so a refusal is recoverable — and it must be recovered, because
        # the fitter has already overwritten the file it was re-fitting.
        ck = REPIN.get("checkpoint")
        restored = False
        if ck:
            try:
                shutil.copy2(ROOT / ck, track)
                restored = True
            except OSError as ex:
                why += " (could not restore the checkpoint: %s)" % ex
        if restored:
            lib = ph.lib
            with lib._lock:
                lib._tracks.pop("hf:" + stem, None)
                lib._track_bad.pop("hf:" + stem, None)
            reset_mouth_caches()
        REPIN["note"] = why
        action("repin", "%s: %s" % (stem, REPIN["note"]), "fitter")


_VOICE_OF: dict = {}


def film_has_voice(path: Path) -> bool:
    """True when the mp4 carries an audio stream (once per path + mtime; a probe
    failure counts as a voice, so the drill keeps its old behaviour)."""
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return True
    if key not in _VOICE_OF:
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=10)
            _VOICE_OF[key] = bool(r.stdout.strip()) if r.returncode == 0 else True
        except Exception:  # noqa: BLE001
            _VOICE_OF[key] = True
    return _VOICE_OF[key]


_HARVEST_SHEET: dict[str, list] = {}


# THE PLAN PAINTS THE SCRUB (the User 2026-09-24: "pause Say and go frame by frame with the
# painted tiles for corrections … play 1/4 speed during tiles painting"). A harvest take with
# a plan (lib/mouth_plan: the waveform's bands + his ticks) paints the PLAN's class at each
# frame whenever she is not speaking; rebuilt when his ticks or the hits change on disk.
_PLAN_FRAMES: dict = {}


def plan_class_at(key: str, frame: int) -> str | None:
    stem = str(key) if str(key).startswith("clip-") else "clip-" + str(key)
    hp = POSE_DIR / (stem + ".hits.json")
    if not hp.is_file():
        return None
    try:
        import mouth_plan
        tp = mouth_plan.ticks_path(POSE_DIR, stem)
        sig = (hp.stat().st_mtime, tp.stat().st_mtime if tp.is_file() else 0.0)
        got = _PLAN_FRAMES.get(stem)
        if not got or got[0] != sig:
            got = (sig, mouth_plan.frame_classes(mouth_plan.build(POSE_DIR, stem)))
            _PLAN_FRAMES[stem] = got
        fc = got[1]
    except Exception:  # noqa: BLE001
        return None
    return fc[int(frame)] if 0 <= int(frame) < len(fc) else None


def harvest_class_at(key: str, frame: int, plan: bool = True) -> str | None:
    """Viseme the rendered clip was saying at this film frame (phon.json).

    Drill / clip-audio paste the bank tile for THAT class — not the voice restating
    the Say box. Missing sheet → None (filmed mouth). The plan wins when the take
    has one (2026-09-24).
    """
    got = plan_class_at(key, frame) if plan else None
    if got is not None:
        return got
    if key not in _HARVEST_SHEET:
        names = [str(key)]
        if str(key).startswith("clip-"):
            names.append(str(key)[5:])
        else:
            names.append("clip-" + str(key))
        sheet: list | None = None
        for name in names:
            p = POSE_DIR / "visemes" / (name + ".phon.json")
            if not p.is_file():
                continue
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(raw, list):
                sheet = raw
                break
        _HARVEST_SHEET[key] = sheet if isinstance(sheet, list) else []
    i = int(frame)
    for rec in _HARVEST_SHEET.get(key) or ():
        if not isinstance(rec, dict):
            continue
        try:
            a, b = int(rec.get("a")), int(rec.get("b"))
        except (TypeError, ValueError):
            continue
        if a <= i <= b:
            k = str(rec.get("k") or rec.get("viseme") or "").strip()
            return k or None
    return None


# The mute sink for a silent painted run: a PipeWire null sink made on demand (a reboot drops it).
# Kill: HOUSE_DRILL_MUTE=0 (a mute:true pass then plays aloud, as before).
DRILL_MUTE = os.environ.get("HOUSE_DRILL_MUTE", "1").strip().lower() not in ("0", "false", "no", "off")
MUTE_SINK = os.environ.get("HOUSE_MUTE_SINK", "house-mute").strip() or "house-mute"


def _ensure_mute_sink() -> bool:
    """True when the null sink exists (made here if it does not)."""
    try:
        out = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=4).stdout
        if any(line.split("\t")[1:2] == [MUTE_SINK] for line in out.splitlines()):
            return True
        r = subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=%s" % MUTE_SINK,
                            "sink_properties=device.description=%s" % MUTE_SINK],
                           capture_output=True, text=True, timeout=4)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def drill_spawn() -> bool:
    """(Re)start the film's own voice for one pass; the run loop locks the clip to it.

    Never on an empty line: house-speak exits rc 2 ("missing text") before its
    wav branch, so nothing played and the run loop waited 40 s for a voice
    (the User 2026-09-16: follow-clip "not painting")."""
    text = str(DRILL.get("text") or "").strip()
    if not text:
        DRILL.update({"on": False, "words_error": "no words"})
        action("drill", "no words for %s — not spawning" % DRILL.get("key"), "")
        return False
    # A PASS TAKES THE FILM, SO IT RELEASES THE SCRUB HOLD (the User 2026-09-25: "f172 everytime the
    # autotuner gets to end it flashes on that slide before starting over"): a frame held from the
    # strip stayed in SCRUB across a tuner run, and the run loop honours it whenever no pass is on —
    # so between two passes the playhead snapped to that frame for a tick. A scrub during a pass
    # already stops the pass (the /v1/scrub route); the converse was missing.
    if SCRUB.get("frame") is not None:
        SCRUB.update({"key": None, "frame": None})
        action("scrub", "released — a pass takes the film", "drill")
    cmd = [sys.executable, str(ROOT / "scripts" / "house-speak.py"), "--voice", str(DRILL.get("who") or "crissa"),
           "--text", text[:DRILL_TEXT_CAP]]
    if DRILL.get("voiced", True):
        cmd += ["--wav", str(DRILL.get("path"))]       # the pacer car: the film's own voice
    # A SILENT PAINTED RUN (the User 2026-09-25: "I need to see the strip with the tiles on the
    # player to tell. Can we make the player paint. no sound on my player in slow mo no loop, no
    # Say button"): the sheet is clocked by the voice, so the voice still plays — into a null sink
    # nobody hears. Same pass, same sheet, same rate (film_wav at PLAY_RATE); only the room is quiet.
    if DRILL.get("mute") and DRILL_MUTE:
        if _ensure_mute_sink():
            cmd += ["--sink", MUTE_SINK]
        else:
            action("drill", "mute sink unavailable — the pass plays aloud", "")
    # THE SHEET WALKS THE FILM'S OWN WORD TIMES (2026-09-18). When the line came off the
    # timeline, its words carry their film frames: hand them to house-speak (--words) and
    # no aligner runs in the pass. Re-aligning every pass had an 8 s HTTP timeout and a
    # budget of 8 s + audio/8; under the afternoon's load (a repin on 8 cores, crew at
    # 100 %) the aligner ran 2x realtime, the 42 s up-close-harvest-4 could not make it,
    # and the LETTER CLOCK stood: the same words far faster than the film — "detached
    # from script" (the User). And nothing said so: stderr went to /dev/null. It now goes to
    # logs/drill-<port>.log and the clock line reaches the report.
    # a silent loop with a timeline (its harvest's words, shifted — 2026-09-18) walks it too:
    # The voice says the words, the sheet lands them on the loop's frames, never the letter clock
    if str(DRILL.get("line_src") or "") == "timeline" and DRILL_WORDS_FROM_TIMELINE:
        try:
            import pose_intake as _pi
            tlp = _pi.timeline_path(str(DRILL.get("key") or ""))
            if tlp.is_file():
                cmd += ["--words", str(tlp)]
        except Exception as e:  # noqa: BLE001
            action("drill", "timeline words unavailable (%s) — the aligner stands in" % e, "")
    # the drill honours tempo x rate (the User 2026-09-13 06:0x "tempo is not being
    # honored"): the film's voice is stretched like a Say and the film is
    # locked at the same pace, so a slow drill is slow motion, not a mismatch
    env = dict(os.environ); env.update(speak_timing_env())
    DRILL["pace"] = float(MOUTH_TEMPO) * float(PLAY_RATE)
    # WHICH DIALS THIS LAP IS RUNNING (the User 2026-09-22: "Match Game while dials
    # move. If a script could do that (AutoTuner) while adjusting dials could it
    # not circle in on optimals?").
    #
    # A looping drill already re-spawns "again, with whatever the dials say now",
    # so a lap always runs the dials as they stand at ITS spawn. What nothing
    # recorded was WHICH those were: the optimizer reads them back at REPORT
    # time, which is correct only because a batch run stops between passes. The
    # moment dials move under a running loop, that read belongs to the next lap
    # and every score is attributed to the wrong setting.
    #
    # Stamped here, at the one instant they are handed to house-speak, so a live
    # tuner can change a dial whenever it likes and still know what it measured.
    DRILL["dials"] = live_dials()
    DRILL.update({"armed": time.time(), "t0": None, "log": [], "place": [], "clock": None})
    logp = ROOT / "logs" / ("drill-%d.log" % PORT)
    try:
        logp.parent.mkdir(exist_ok=True)
        errf = logp.open("a")
        errf.write("--- %s %s pass %d %s\n" % (time.strftime("%H:%M:%S"), DRILL.get("key"), int(DRILL.get("passes") or 0) + 1,
                                              "--words" if "--words" in cmd else "aligner"))
        errf.flush()
    except OSError:
        errf = subprocess.DEVNULL
    DRILL["log_path"] = str(logp)
    DRILL["proc"] = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=errf,
                                     stderr=subprocess.STDOUT, start_new_session=True)
    if errf is not subprocess.DEVNULL:
        errf.close()
    _SPEAKERS.append(DRILL["proc"])
    return True


_ANSWER_KEY: dict = {}   # (path, mtime_ns, film) -> {frame: open}, like _VOICE_OF


def _answer_frames(path: Path, pick, film: str = "") -> dict:
    """Cached per file AND film: map.json is shared by every take, so a key on
    the path alone handed harvest-2 harvest-a's 241 frames (caught 2026-09-16
    17:12 on the bench)."""
    try:
        sig = (str(path), path.stat().st_mtime_ns, str(film))
    except OSError:
        return {}
    hit = _ANSWER_KEY.get(sig)
    if hit is None:
        hit = {}
        try:
            hit = pick(json.loads(path.read_text())) or {}
        except (OSError, ValueError, TypeError):
            hit = {}
        _ANSWER_KEY[sig] = hit
    return hit


def drill_answer_key(key: str) -> dict:
    """frame -> mouth openness of the filmed harvest: map.json `open` stamps
    first, else the per-frame `open` in the take's playlist FILE.

    The file, not `Library.playlist()`: that returns None for a playlist
    without an `index` (the four half-intaken up-close films), and their
    frames DO carry `open` — the answer key was there and the drill said
    "no filmed openness" (2026-09-16). Returns {"frames", "from", "path"}."""
    stem = str(key)
    names = {stem, stem[5:] if stem.startswith("clip-") else "clip-" + stem}
    banks = list(dict.fromkeys([POSE_DIR, *[Path(b) for b in FACE_BANKS.values()]]))
    for bank in banks:
        mp = Path(bank) / "visemes" / "phoneme-map" / "map.json"

        def _from_map(d, names=names):
            out = {}
            for e in d.get("entries") or []:
                if str(e.get("clip")) in names and e.get("open") is not None:
                    out[int(e.get("frame"))] = float(e["open"])
            return out
        fr = _answer_frames(mp, _from_map, stem) if mp.is_file() else {}
        if fr:
            best_map = {"frames": fr, "from": "map.json", "path": str(mp)}
            break
    else:
        best_map = None
    # THE FULLER KEY WINS (2026-09-18, the User "not getting a good score on down-desk"):
    # talk-down-harvest-736 has 19 map stamps (its picked frames) on a 240-frame
    # film, and the drill scored 19 points — closures 17 %, 14.8 changes/s. A
    # sparse stamp set is class truth, not the film's openness; the playlist's
    # per-frame `open` (the head track) covers every frame.
    ph = STATE.get("playhead")
    row = (ph.lib.rows.get(key) if ph is not None else None) or {}
    if row.get("file"):
        for bank in banks:
            pl = (Path(bank) / str(row["playlist"])) if row.get("playlist") else playlist_path_for(str(row["file"]), Path(bank))
            if not pl.is_file():
                continue

            def _from_pl(d):
                out = {}
                for f in d.get("frames") or []:
                    if isinstance(f, dict) and f.get("open") is not None and f.get("i") is not None:
                        out[int(f["i"])] = float(f["open"])
                return out
            fr = _answer_frames(pl, _from_pl, stem)
            if fr and _answer_is_flat(fr):
                # A FLAT KEY IS NO KEY (2026-09-18 14:3x, measured on the bench): the close-up
                # take's playlist rows carry `open` 0.4 on all 1205 frames (a stub row writer),
                # so "the fuller key wins" took 1205 flat rows over 19 real stamps and every
                # slow pass scored r -9 with closures "100 %" against a constant. The head
                # track holds the film's real openness; it is the fallback below.
                fr = {}
            if fr and (best_map is None or len(fr) > len(best_map["frames"])):
                return {"frames": fr, "from": "playlist", "path": str(pl)}
            if fr:
                break
    if best_map is not None and not _answer_is_flat(best_map["frames"]):
        return best_map
    # THE HEAD TRACK IS THE TRUTH: per-frame `open` from <stem>.head.json, when the map
    # stamps and the playlist rows are absent or flat.
    head = getattr(getattr(ph, "lib", None), "head", None) if ph is not None else None
    if callable(head) and row.get("file"):
        out = {}
        i = 0
        while True:
            rec = head(key, i)
            if rec is None:
                break
            if isinstance(rec, dict) and rec.get("open") is not None:
                out[i] = float(rec["open"])
            i += 1
        if out and not _answer_is_flat(out):
            return {"frames": out, "from": "head track", "path": str(POSE_DIR / (Path(str(row["file"])).stem + ".head.json"))}
    if best_map is not None:
        return best_map
    return {"frames": {}, "from": None, "path": None}


def _answer_is_flat(frames: dict) -> bool:
    """No variance = nothing to correlate against (r stays -9 in mouth_drill.score)."""
    vals = [float(v) for v in (frames or {}).values()]
    return len(vals) > 1 and (max(vals) - min(vals)) < 1e-4


def drill_filmed_open(key: str) -> dict:
    """frame -> mouth openness of the filmed harvest (see drill_answer_key)."""
    return drill_answer_key(key)["frames"]


# THE FARMED LIP KEY (the User 2026-09-21: "farm the PIXLE MINs" ... "I say we test
# both with a clean rollback"). `open` is the JAW; scripts/pose-lip-track.py
# farms what her LIPS do, per frame, into <stem>.lips.json, and the decode key
# (visemes/class-geometry.json) says what each class measures on her own face.
# Both are read here and handed to score() ALONGSIDE the old key, never instead
# of it: one pass, two numbers. No farm on disk = nothing changes.
# Kill: HOUSE_DRILL_LIP_KEY=0.
DRILL_LIP_KEY = os.environ.get("HOUSE_DRILL_LIP_KEY", "1").strip() not in ("0", "false", "no")
# A tuner run hands the film back to the player when it ends (2026-09-22).
# 0 = the old behaviour: parked on the last frame until something plays.
TUNER_HANDS_BACK = os.environ.get("HOUSE_TUNER_HANDS_BACK", "1").strip() not in ("0", "false", "no")
# A BENCH REPLAYS ITS OWN CLIP (the User 2026-09-21: "i want it perfect").
# A harvest take is a oneshot: it ends on its last frame and holds there, and a
# drill camps the sequencer on it — but a harvest take has no hub, so `here` is
# None and the transport's next play is refused with "does not attach to None".
# The only way back to a moving picture was another Say or drill. Measured after
# the solo autotune: thirty-odd passes, every one ending parked on frame 240 of
# 241 with /frame.jpg byte-identical for as long as you left it.
# Replaying the clip the bench is ALREADY on is not travel, it is a rewind, and
# a bench's law is that it HOLDS its clip — so this cannot move it anywhere it
# was not already. The wall never takes the branch. Kill: HOUSE_BENCH_REPLAY=0.
BENCH_REPLAY = os.environ.get("HOUSE_BENCH_REPLAY", "1").strip() not in ("0", "false", "no")
# A PASS WAITS ON FRAME 0 FOR ITS VOICE (the User 2026-09-27: "hold on frame 0
# until the voice arrives"). drill_begin plays the film at once and the voice
# lands seconds later (the voice's first hop, the aligner, a first read of the film's
# words), so the film rolled with the idle mouth and was then set back to frame 0
# when the sheet's air stamp arrived — measured on lila-work under the AutoTuner,
# four passes in a row: frame 82-89 of 241 back to 0, every pass. It read as a
# second clip cut in halfway. The score was never touched (it is taken from the
# locked play); this is the picture only. Not the calibration pass: that one has
# no voice to wait for and posts its sheet from the playhead one frame after its
# own rewind. Kill: HOUSE_DRILL_HOLD_ZERO=0.
DRILL_HOLD_ZERO = os.environ.get("HOUSE_DRILL_HOLD_ZERO", "1").strip() not in ("0", "false", "no")


def drill_lip_key(key: str) -> dict:
    """{"lips": [...], "shut": [...], "class": {...}} — whatever is farmed."""
    if not DRILL_LIP_KEY:
        return {}
    # THE CLIP KEY IS NOT THE FILM'S STEM. `harvest-talk-back` plays
    # `clip-talk-back-harvest.mp4` -- the words are reordered, not prefixed --
    # so a name rule finds nothing (it found nothing, live, first try). The
    # drill already carries the resolved path; use it, and keep the name rule
    # only as the fallback for a caller that has no path.
    names = []
    dp = str(DRILL.get("path") or "")
    if dp:
        names.append(Path(dp).stem)
    stem = str(key)
    names += [stem, stem[5:] if stem.startswith("clip-") else "clip-" + stem]
    banks = list(dict.fromkeys([POSE_DIR, *[Path(b) for b in FACE_BANKS.values()]]))
    if dp:
        banks.insert(0, Path(dp).parent)
    for bank in banks:
        for nm in names:
            fp = Path(bank) / (nm + ".lips.json")
            if not fp.is_file():
                continue
            try:
                d = json.loads(fp.read_text())
            except Exception:  # noqa: BLE001
                continue
            out = {"lips": d.get("lip") or [], "shut": d.get("shut") or [],
                   "release": d.get("release") or [],
                   # the audio envelope the bursts were found in. It costs
                   # nothing to carry and it is the only per-frame audio the
                   # drill has -- without it the trace can draw the frame a
                   # burst landed on but not the spike itself.
                   "rms": d.get("rms") or []}
            kp = Path(bank) / "visemes" / "class-geometry.json"
            if kp.is_file():
                try:
                    out["class"] = (json.loads(kp.read_text()) or {}).get("class") or {}
                except Exception:  # noqa: BLE001
                    pass
            return out
    return {}


def drill_needs(ph, key: str, line_src) -> dict:
    """What this film has for a graded run — the report names the first thing
    missing, so a half-intaken take says "run intake", not "no match"."""
    ak = drill_answer_key(key)
    try:
        bank = bool(ph.lib.playlist(key))
    except Exception:  # noqa: BLE001
        bank = False
    return {"voice": bool(DRILL.get("voiced", True)), "words": line_src or "reading", "tile_bank": bank,
            "answer_key": len(ak["frames"]), "answer_from": ak["from"]}


def _drill_read_words(key: str, by: str = "composer") -> None:
    """The drill-words thread: read the film once (clip_words), then spawn."""
    try:
        import clip_words
        r = clip_words.words_for(key)
    except Exception as e:  # noqa: BLE001
        r = {"ok": False, "error": "clip_words failed: %s" % e}
    if DRILL.get("key") != key or not DRILL.get("on") or not DRILL.get("reading"):
        return   # stopped or replaced while we read
    if r.get("ok") and str(r.get("line") or "").strip():
        DRILL.update({"text": str(r["line"]).strip()[:DRILL_TEXT_CAP], "line_src": r.get("source"),
                      "reading": False, "words_error": None})
        if isinstance(DRILL.get("needs"), dict):
            DRILL["needs"]["words"] = r.get("source")
        drill_spawn()
        nw = len(r.get("words") or []) or len(str(r["line"]).split())
        msg = "read the film: %d words (%s)" % (nw, r.get("source"))
        action("drill", msg, by)
        journal_add("drill", by, msg, {"key": key, "source": r.get("source"), "stt_ms": r.get("stt_ms")})
        return
    err = str(r.get("error") or "no words")
    DRILL.update({"on": False, "reading": False, "words_error": err, "report": {"why": err, "key": key}})
    action("drill", "could not read %s: %s" % (key, err), by)


def drill_begin(ph, key: str, row: dict, path: Path, *, who: str, loop: bool, override: str = "",
                graded: bool = True, by: str = "composer", voiced: bool = True,
                pacer: bool = False, mute: bool = False) -> tuple[int, dict]:
    """The one sequence behind /v1/drill and /v1/say {src:clip} (they were
    duplicates): stop a running drill, arm DRILL, play the film camped, then
    the words — the body's `text` (override) → the take's timeline → else a
    drill-words thread reads the film and spawns when the line lands (the
    Composer shell proxies with a 6 s timeout; a first read is 3–15 s)."""
    if DRILL.get("on"):
        drill_stop("restart")
    # ONE VOICE (the User 2026-09-17: "they both play"). play() below cuts a
    # speaker only when the clip CHANGES; a textbox line still speaking on
    # this same film ran on under the film's own voice.
    say_stop_all()
    DRILL.update({"on": True, "key": key, "text": "", "report": None, "passes": 0, "log": [], "place": [], "t0": None,
                  "reading": False, "line_src": None, "words_error": None, "needs": None, "armed": time.time(),
                  "n": len(ph.lib.frames(key) or []), "loop": bool(loop), "path": str(path), "who": who,
                  "graded": bool(graded), "voiced": bool(voiced), "mute": bool(mute)})
    DRILL["hold_frame"] = False
    if COMPOSER_TRANSPORT:
        # BEFORE the pacer branch: a calibration pass returns before drill_spawn, the
        # one place that released the strip's hold, so the f172 flash lived on between
        # calibration passes (fault 13). And a pass needs a free mouth: viseme() returns
        # the held class and `speaking` reads true under any hold, so a match game under
        # a hold painted and graded the held class every frame (fault 10).
        transport_release("a pass takes the film", by)
        _m = STATE.get("mouth")
        if _m is not None and (getattr(_m, "force_class", None) is not None or getattr(_m, "force", None) is not None):
            _m.force_class = None
            _m.force = None
            action("drill", "released the held class — a pass needs a free mouth", by)
    ph.play(key, force=True, audio=False)
    ph.seq.camp = key
    out: dict = {"ok": True, "key": key, "frames": DRILL["n"], "film": str(path), "graded": bool(graded)}
    if pacer:
        # THE CALIBRATION SIGNAL, not a line. No voice, no words, no aligner —
        # the sheet is built from her own filmed mouth, so the only thing the
        # report can measure is the pipeline. Marked on DRILL so a pacer pass
        # can never be mistaken for a speech score.
        DRILL.update({"pacer_on": True, "text": "", "line_src": "calibration signal"})
        # TWO CLOCKS, AND ONLY ONE OF THEM KNEW THE SPEED (the User 2026-09-21: "you
        # say we still can't autotune on slow motion?"). The pacer returns before
        # drill_spawn, which is the only place that sets DRILL["pace"] — so the
        # clip lock below ran the film at CLIP_FPS x 1.0 (a stale or absent pace)
        # no matter what the rate box said, while drill_pacer_post stamped every
        # row at CLIP_FPS x PLAY_RATE. The film ran at 24 fps and the sheet was
        # written for 6. Traced on this bench (logs/pacer-trace-8110.jsonl):
        #
        #   rate    film ran at   rows due vs the film   reported pipeline
        #   1.00     21.8 f/s          +23 ms                  +2 f
        #   0.50     24.0 f/s          -62 ms                  +4 f
        #   0.25     24.0 f/s         -226 ms                  +8 f
        #
        # The playhead reached the frame BEFORE its row was due, further ahead
        # the slower the rate, and the report read that gap as pipeline lag —
        # which is why it looked like a phantom that scaled with 1/rate.
        # There is no voice here, so there is no tempo either: a calibration
        # signal runs the film at exactly the speed on the box.
        # Kill: HOUSE_PACER_PACE_RATE=0 puts the old (unset) pace back.
        if PACER_PACE_RATE:
            DRILL["pace"] = float(PLAY_RATE)
        # REWIND FIRST (2026-09-21). The sheet is scheduled from the playhead
        # FORWARD, so a pacer started where the last one ended has no film left
        # to schedule and the post refuses. Measured: pass 1 read r 0.990 and
        # passes 2-5 returned nothing at all, which looked like an unstable
        # instrument and was a playhead sitting on the last frame. An
        # instrument that works once is not an instrument.
        # THE TRACK IS BUILT BEFORE THE REWIND. A cold ffmpeg extract is seconds;
        # paying it after t0 would land the sound in the middle of the pass.
        # A film with no audio stream returns None and the pass runs as it always
        # did -- 27 of the 38 films in the kit are silent, and that is not a fault.
        DRILL["pacer_sound"] = None
        PACER_SOUND_GO.clear()
        if PACER_SOUND:
            _wav = None
            try:
                _wav = clip_audio.film_wav(key, path, float(PLAY_RATE))
            except Exception as _e:  # noqa: BLE001
                action("drill", "no calibration sound (%s)" % str(_e)[:80], by)
            if _wav is None:
                DRILL["pacer_sound"] = "silent"
            else:
                DRILL["pacer_sound"] = str(_wav)
                threading.Thread(target=_pacer_sound_play, args=(str(_wav), str(DRILL.get("who") or "crissa")),
                                 daemon=True, name="pacer-sound").start()
        # rewind, then ARM: the render loop posts the sheet on its next frame
        # boundary, when `i` and `i_at` are its own and cannot be stale
        ph.i = 0
        ph.i_at = time.time()
        ph._clip_accum = 0.0
        DRILL["pacer"] = None
        DRILL["pacer_result"] = None
        DRILL["pacer_arm"] = True
        _t0 = time.time()
        while DRILL.get("pacer_result") is None and time.time() - _t0 < 5.0:
            time.sleep(0.02)
        res = DRILL.get("pacer_result") or {"ok": False, "error": "the playhead did not post the signal"}
        if not res.get("ok"):
            DRILL.update({"on": False, "pacer_on": False})
            clip_audio.stop()          # the passenger never outlives the pass
            return 409, {"ok": False, "error": res.get("error") or "no calibration signal"}
        _snd = str(DRILL.get("pacer_sound") or "")
        action("drill", "%s — calibration signal (%d rows from her own mouth)%s" % (
            key, res.get("rows") or 0,
            "" if _snd and _snd != "silent" else " — this film is silent, no sound on the pass"), by)
        out.update({"pacer": res, "line_src": "calibration signal",
                    "sound": (None if not _snd else ("silent" if _snd == "silent" else "film"))})
        return 200, out
    DRILL["pacer_on"] = False
    text = str(override or "").strip()
    src = "override" if text else None
    if not text and DRILL_FILM_WORDS:
        try:
            import clip_words
            r = clip_words.words_for(key, allow_stt=False)   # disk only; never a network call here
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "error": str(e)}
        if r.get("ok") and str(r.get("line") or "").strip():
            text, src = str(r["line"]).strip(), "timeline"
    if text:
        DRILL.update({"text": text[:DRILL_TEXT_CAP], "line_src": src})
        DRILL["needs"] = drill_needs(ph, key, src)
        drill_spawn()
        action("drill", "%s %s — line from %s, %d chars" % (key, "with its own voice" if voiced else "silent: the voice says the line over it", src, len(text)), by)
        out.update({"line": DRILL["text"], "line_src": src, "needs": DRILL["needs"]})
        return 200, out
    if not DRILL_FILM_WORDS:
        DRILL["on"] = False
        return 400, {"ok": False, "error": "text required — film words are off (HOUSE_DRILL_FILM_WORDS=0)"}
    DRILL.update({"reading": True, "line_src": None})
    DRILL["needs"] = drill_needs(ph, key, None)
    threading.Thread(target=_drill_read_words, args=(key, by), daemon=True, name="drill-words").start()
    action("drill", "%s — reading the film's words" % key, by)
    out.update({"reading": True, "needs": DRILL["needs"]})
    return 200, out


# ---------------------------------------------------------------------------
# THE CALIBRATION SIGNAL (the User 2026-09-21: "think of another way to get good r
# for inital r score to pass. I do not want to overfit here")
#
# The zero is a property of the MACHINE — how long from a sheet row's timestamp
# to that tile on her face. It has nothing to do with consonants. We were
# inferring it from speech only because speech is what plays, and then demanding
# the speech correlate well enough (r >= 0.35) for the inference to hold. On a
# film that does not correlate the only way to pass was to move the dials until
# it did, which is fitting the gate, not measuring the pipeline.
#
# So stop calibrating on speech. Build the sheet FROM THE FILM'S OWN MOUTH: one
# class per frame, chosen so its CLASS_OPEN is nearest what her filmed mouth is
# doing on that frame, stamped with the wall time that frame will be shown.
# The tile track is then a copy of the answer key by construction, so r is high
# by design and the only thing the lag can measure is the pipeline. No dial can
# distort it and there is nothing to overfit, because nothing is being fitted.
#
# It rides the ordinary sheet path (the mailbox, viseme_at, the painter), so it
# is timed through exactly the machinery a spoken line uses.
# Kill: HOUSE_DRILL_PACER=0 (calibrate falls back to speech).
DRILL_PACER = os.environ.get("HOUSE_DRILL_PACER", "1").strip() not in ("0", "false", "no")
# A COPY OF THE ANSWER KEY SHOULD CORRELATE NEAR 1. Offline on her own track it
# reads 0.995, and a one-frame shift drops it to 0.908 — so anything under this
# means the signal never landed, and its lag is measuring the miss. Nothing
# like the 0.35 speech law, which is about whether SPEECH correlates at all.
PACER_MIN_R = float(os.environ.get("HOUSE_DRILL_PACER_MIN_R", "0.80"))
# How far ahead one window reaches, and how early the next is posted. Short
# enough that the playhead's real rate cannot drift a frame inside it (at
# 22.6 vs 24 fps that is ~6 %, so 24 frames drifts 1.4 — hence re-anchoring
# with 12 still in hand).
# How far ahead each row is stamped: enough that the painter has it before it
# composites that frame, few enough that the film cannot move underneath it.
PACER_LEAD_F = int(os.environ.get("HOUSE_DRILL_PACER_LEAD_F", "2"))

# WHY A SLOW PASS MEASURES LATE (2026-09-21, chasing the User's "we still can't
# autotune on slow motion"): the pacer reads +2 f at 1x, +5 f at 1/2 and +10 f
# at 1/4 on the same film — so the delay GROWS in film frames as the film
# slows, which no fixed wall-clock pipeline can do. Nothing in the arithmetic
# explains it, so this writes the two clocks down and lets the numbers say it:
# every row posted (when, where the playhead was, which frame it is for, when
# it is stamped to start) and every frame painted (when, where, which class).
# Off unless HOUSE_PACER_TRACE=1 — it is a debugging instrument, not a feature.
PACER_TRACE = os.environ.get("HOUSE_PACER_TRACE", "0").strip() in ("1", "true", "yes")
# The film plays at the rate on the box during a calibration pass, so the
# sheet's clock and the film's clock are the same one. Kill = the old drift.
PACER_PACE_RATE = os.environ.get("HOUSE_PACER_PACE_RATE", "1").strip() not in ("0", "false", "no")
PACER_TRACE_PATH = ROOT / "logs" / ("pacer-trace-%d.jsonl" % PORT)
# THE CALIBRATION PASS PLAYS THE FILM'S OWN TRACK (the User 2026-09-21: "play films
# track ... with sound"). It was the one pass with no sound in it: the sheet is
# built from her filmed mouth, so this branch returns before drill_spawn and no
# house-speak is ever spawned, and the film itself is started audio=False. The
# picture rolled in silence and there was nothing to judge it by.
#
# The track is a PASSENGER. Nothing reads it back, so it cannot move the number
# the pass exists to measure -- which is the one thing to check when it lands:
# pipeline_ms with the sound on must equal pipeline_ms with it off.
#
# It starts on the instant the sheet's FIRST ROW is stamped (pc["air_at"] in
# drill_pacer_post), which is the same instant DRILL["t0"] is taken from and the
# playhead is driven from -- so sound and picture share one t0. The render loop
# only sets an Event; the fork happens on the helper thread, because this loop's
# timing is the instrument. Kill: HOUSE_DRILL_PACER_SOUND=0.
PACER_SOUND = os.environ.get("HOUSE_DRILL_PACER_SOUND", "1").strip() not in ("0", "false", "no")
PACER_SOUND_GO = threading.Event()



def _pacer_gate(on: bool, who: str) -> None:
    """Tell the ear a house mouth is open, so rail-stt vetoes this echo.

    Tagged "<voice>@bench" exactly as house-speak tags a bench line
    (HOUSE_SPEAK_WHO_TAG): the room SHOULD go deaf while a tuning track plays out
    of the same speakers, but the wall's caption is not the bench's business and
    both readers already filter on that suffix. No air_at / air_until -- the
    mailbox's air clock belongs to the calibration signal, not to this passenger.
    """
    try:
        from audio_sink import mark_tts_gate
        mark_tts_gate(speaking=bool(on), last_reply="", who="%s@bench" % (who or "crissa"),
                      extra_sec=0.4 if on else 0.0, air_off=True)
    except Exception:  # noqa: BLE001
        pass


def _pacer_sound_play(wav: str, who: str) -> None:
    """Wait for the sheet's first row, then play the film's own track beside it."""
    if not PACER_SOUND_GO.wait(8.0):
        return
    if not DRILL.get("pacer_on"):
        return
    _pacer_gate(True, who)
    try:
        p = clip_audio.play_wav(Path(wav))
        while p is not None and p.poll() is None and DRILL.get("pacer_on"):
            time.sleep(0.1)
    finally:
        clip_audio.stop()
        _pacer_gate(False, who)

def pacer_trace(kind: str, **kw) -> None:
    """One line per event, appended. Silent on any error — a probe never bites."""
    if not PACER_TRACE:
        return
    try:
        kw["k"] = kind
        kw["t"] = round(time.time(), 4)
        with open(PACER_TRACE_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(kw) + "\n")
    except Exception:  # noqa: BLE001
        pass


PACER_SHUT_X = float(os.environ.get("HOUSE_PACER_SHUT_X", "0.08"))
# THE LADDER IS READ THROUGH A CENTRED MEDIAN (the User 2026-09-26, calibrate: "random tiles",
# rest flashing): the nearest class per frame flips on every step boundary where her jaw sits.
# A hold or a dwell would cure that but can only DELAY a change, so it biases the very number
# this signal measures (the zero read +3.0 f on the first pass after a dwell landed, against
# +2.0/+2.5 the day before). The film's whole track is known ahead, so a CENTRED median over
# PACER_MEDIAN_F frames is zero-phase by construction: a single- or double-frame blip goes,
# a real step changes class on the frame it happens. Measured on close-pangram-a: changes
# 79 -> 56 over 360 frames, r 0.983 -> 0.982, best lag +0 f before and after.
# HOUSE_PACER_MEDIAN_F=0 is the old ladder.
PACER_MEDIAN_F = int(os.environ.get("HOUSE_PACER_MEDIAN_F", "5"))


def _pacer_classes(filmed: dict, n: int) -> list:
    """frame -> the class whose CLASS_OPEN is nearest her filmed openness.

    Her own track is normalised against its own 2nd/98th percentile first: the
    raw `open` is a jaw ratio, CLASS_OPEN is 0..1 sealed-to-wide, and the two
    do not share a scale. Without it every frame picks the same middle class
    and the signal is flat — which correlates with nothing.
    """
    from mouth_drill import CLASS_OPEN
    vals = [float(v) for v in filmed.values() if v is not None]
    if len(vals) < 8:
        return []
    lo, hi = float(np.percentile(vals, 2)), float(np.percentile(vals, 98))
    if hi - lo < 1e-6:
        return []
    # rest is the floor; skip rest-open so a closed mouth cannot read as ajar
    table = [(k, v) for k, v in CLASS_OPEN.items() if k != "rest-open"]
    xs: dict = {}
    for i in range(int(n)):
        raw = filmed.get(i)
        if raw is not None:
            xs[i] = min(1.0, max(0.0, (float(raw) - lo) / (hi - lo)))
    if PACER_MEDIAN_F > 1:
        # centred, over the frames that exist: zero-phase, so the zero it feeds is not moved
        r_ = PACER_MEDIAN_F // 2
        xs = {i: float(np.median([xs[j] for j in range(i - r_, i + r_ + 1) if j in xs])) for i in xs}
    out = []
    for i in range(int(n)):
        if i not in xs:
            out.append(None)
            continue
        x = xs[i]
        # NEAR-SHUT IS REST (the User 2026-09-24: "still twitches rest in autotune calibrate
        # zero"): rest is 0.00 and MBP 0.02, so jaw noise inside a pause flipped the tile rest ->
        # MBP -> rest every frame. Both are the one closed family; the zero is read from the
        # openings and closings, not from noise inside a closure.
        if x < PACER_SHUT_X:
            out.append("rest")
            continue
        out.append(min(table, key=lambda kv: abs(kv[1] - x))[0])
    return out


def drill_pacer_post(key: str, ph, first: bool = False) -> dict:
    """Post the class for the frame that is about to be shown. One row.

    RE-ANCHORED EVERY FRAME, because the film does not run at CLIP_FPS.
    MEASURED 2026-09-21: this bench's playhead advances at 22.6 fps, not 24 —
    it moves with the render loop, not with a clock. A sheet stamped once at an
    exact 24 fps drifted ~14 frames past the film across one take and reported
    that drift as pipeline lag (+2 f one pass, +14 f the next, r still 0.99
    because the SHAPE matched all along). Posting a window and re-anchoring it
    was no better: overlapping windows gave the painter two answers for the
    same instant.

    So there is no schedule to drift. On every frame the playhead posts the
    class for frame i + PACER_LEAD_F, stamped PACER_LEAD_F frames ahead. The
    only thing left for the report to measure is how long the sheet takes to
    reach her face — which is the whole point.

    Called from the render loop, which is the only thread that knows where the
    film actually is.
    """
    if MAILBOX is None:
        return {"ok": False, "error": "no mailbox on this conductor (the wall reads :8099)"}
    pc = DRILL.get("pacer") or {}
    classes = pc.get("classes")
    if first or not classes:
        frames = drill_filmed_open(str(key))      # {frame: open}, the answer key
        if not frames:
            return {"ok": False, "error": "no filmed openness for %s — the answer key is missing" % key}
        n = int(DRILL.get("n") or (max(frames) + 1))
        classes = _pacer_classes(frames, n)
        if not classes:
            return {"ok": False, "error": "her filmed mouth is flat on %s — nothing to pace against" % key}
        pc = {"classes": classes, "rows": 0, "want": {}, "air_at": time.time()}
        DRILL["pacer"] = pc
        # THE FILM'S TRACK STARTS HERE, on the sheet's own t0. Only an Event is
        # set: the fork runs on the helper thread, never on this one.
        PACER_SOUND_GO.set()
    from mouth_viseme import class_index
    rate = max(0.05, float(PLAY_RATE or 1.0))
    fps = float(CLIP_FPS) * rate
    f = int(getattr(ph, "i", 0) or 0) + PACER_LEAD_F
    if f >= len(classes) or classes[f] is None:
        return {"ok": True, "rows": pc["rows"], "done": True}
    c = classes[f]
    now = time.time()
    MAILBOX.ingest({"air_at": pc["air_at"], "air_until": now + 2.0, "sr": 16000,
                    "hop_ms": round(1000.0 / fps, 4), "pts0": round(now + PACER_LEAD_F / fps, 4),
                    "rms": [1.0 if c != "rest" else 0.0], "visemes": [int(class_index(c))],
                    "speaker": "crissa"})
    pc["rows"] += 1
    pc["want"][f] = c
    pc["last_i"] = int(getattr(ph, "i", 0) or 0)
    pacer_trace("post", i=int(getattr(ph, "i", 0) or 0), f=f, c=c,
                pts0=round(now + PACER_LEAD_F / fps, 4), hop=round(1000.0 / fps, 4), rate=rate)
    return {"ok": True, "rows": pc["rows"], "frame": f, "classes": len({x for x in classes if x})}


def film_head_truncated(key: str) -> dict | None:
    """Does this film start MID-UTTERANCE? {frames, rms} when it does.

    the User 2026-09-21, on crissa-close-harvest: "Mind the first word is dropped on
    this one i think. Starts with '...beige' in the middle of 'b' and no The."
    He was right, and it is worse than a dropped word: the audio is at full
    speech volume on frame 0 — no attack, no silence — so the first sound's
    own mouth shape is not in the film at all. The ASR transcribed what it
    heard and the aligner had nowhere to put that word but frame 0, which
    shifts every word after it.

    That is not a small error. Solving the lead on such a film gave +4.5 f
    where a film with a clean head gave -1.0 f, and the difference went
    straight into a library-wide dial. So say it, loudly, on every pass.
    """
    ph = STATE.get("playhead")
    row = (ph.lib.rows.get(key) if ph is not None else None) or {}
    path = film_path(row, key) if row else None
    if path is None:
        return None
    try:
        import pose_intake
        pcm = pose_intake.decode_pcm16(path, 16000)
    except Exception:  # noqa: BLE001
        return None
    if not pcm:
        return None
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    hop = max(1, int(16000 / float(CLIP_FPS)))
    n = min(int(CLIP_FPS), len(a) // hop)
    if n < 4:
        return None
    head = [float(np.sqrt(np.mean(a[i * hop:(i + 1) * hop] ** 2))) for i in range(n)]
    whole = float(np.percentile(np.abs(a), 90))
    if whole <= 1e-6:
        return None
    # loud from the very first frame, and no quiet frame in the first half
    # second, is a cut into the middle of a sound
    loud = [x for x in head[:max(2, n // 2)] if x > whole * 0.5]
    if head[0] > whole * 0.5 and len(loud) >= max(2, n // 4):
        return {"frames": len(loud), "rms0": round(head[0], 3), "ref": round(whole, 3)}
    return None


def drill_report() -> dict:
    """Tiles vs film (lib/mouth_drill.score): best lag (ms, + = tiles EARLY), r, coverage, changes/s."""
    from mouth_drill import SEMIS_PROPOSE, score
    from mouth_timing import SLOTS_PER_FRAME
    filmed = drill_filmed_open(str(DRILL.get("key") or ""))
    n = int(DRILL.get("n") or (max(filmed) + 1 if filmed else 0))
    _vmax = max((int(fi) for fi, _k, _o in (DRILL.get("log") or [])), default=-1)
    if DRILL_LOOP_WRAP and n and _vmax >= n and filmed:            # a pass that went round the loop (DRILL_LOOP_WRAP)
        laps = _vmax // n + 1
        filmed = {f + lap * n: v for lap in range(laps) for f, v in filmed.items() if isinstance(f, int)}
        n = laps * n
    # lag and changes/s in HEARD time: at pace p (tempo x rate) a film frame
    # lasts 1/(24 p) s, so the tuner's lead (heard ms) and the lag agree
    pace = float(DRILL.get("pace") or 1.0)
    lk = drill_lip_key(str(DRILL.get("key") or ""))
    try:                                   # his plan, when the take has one (2026-09-24)
        import mouth_plan
        _k = str(DRILL.get("key") or "")
        _plan = mouth_plan.build(POSE_DIR, _k if _k.startswith("clip-") else "clip-" + _k)
    except Exception:  # noqa: BLE001
        _plan = None
    rep = score(list(DRILL.get("log") or []), filmed, n, fps=CLIP_FPS * pace, plan=_plan,
                lips=(lk.get("lips") or None), class_lip=(lk.get("class") or None),
                shut=(lk.get("shut") or None), release=(lk.get("release") or None),
                rms=(lk.get("rms") or None))
    rep["key"] = DRILL.get("key"); rep["pace"] = pace
    rep["rate"] = float(PLAY_RATE)      # with pace, the two numbers a lead<->frame conversion needs (2026-09-25)
    # THE SIZE OF ONE LEGAL STEP OF THE DIAL, AND IT IS THE FILM'S, NOT THE
    # PACE'S (2026-09-22). The sheet-lead box shows frames at `clip_fps` and
    # steps by 0.5, so the only value that displays as a clean half-frame is a
    # multiple of one FILM semiframe. score() is handed CLIP_FPS x pace on
    # purpose — that is what puts lag_ms in heard time beside a heard lead — so
    # a grid minted in there came out 17.361 ms at tempo 1.2 against the box's
    # own 20.833. Minted here, where CLIP_FPS is the film's own rate, it is
    # pace-free. Kill: HOUSE_MOUTH_SEMIS_PROPOSE=0 drops it with the rest.
    if SEMIS_PROPOSE:
        rep["semi_ms"] = (1000.0 / float(CLIP_FPS)) / SLOTS_PER_FRAME
    rep["clock"] = drill_clock()
    # the dials this pass actually ran with, not the ones standing now
    if isinstance(DRILL.get("dials"), dict):
        rep["dials"] = dict(DRILL["dials"])
    # A TRUNCATED HEAD POISONS THE LEAD, so it is said on every pass.
    try:
        _tr = film_head_truncated(str(DRILL.get("key") or ""))
    except Exception:  # noqa: BLE001
        _tr = None
    if _tr:
        rep["head_truncated"] = _tr
        rep["read"] = (rep.get("read") or "") + (
            " · HEAD CUT: this film opens at full speech volume (no attack), so its first "
            "sound's mouth is not in it and every word span is anchored to a partial word "
            "— a lead solved here is this film's cut, not your pipeline")
    if DRILL.get("pacer_on"):
        # DID IT PAINT WHAT IT ASKED FOR? (2026-09-21) The whole claim of a
        # calibration signal is that the tile track is a COPY of the answer
        # key, so r is high by construction. If it is not high, the signal did
        # not arrive — a bad anchor, a dropped sheet, the painter refusing —
        # and the lag is measuring that instead of the pipeline. So compare the
        # classes asked for against the classes logged, frame by frame, and say
        # so. A pacer that cannot verify itself is worth less than no pacer.
        want = (DRILL.get("pacer") or {}).get("want") or {}
        if want:
            got = {}
            for fi, klass, _op in (DRILL.get("log") or []):
                got.setdefault(int(fi), klass)
            # COMPARE OPENNESS, NOT CLASS NAMES. r correlates CLASS_OPEN, and
            # several classes share a value — TDN and SH/CH are both 0.30 — so
            # the openness track can match perfectly while the names differ.
            # Checking names read 19 % on a pass whose r was 0.95, which says
            # the check was wrong twice over: first at the wrong lag, then on
            # the wrong quantity. r IS the verification; this only says how
            # much of the signal got painted at all.
            from mouth_drill import CLASS_OPEN as _CO
            pairs = [(want[f], got[f]) for f in want if f in got]
            diffs = [abs(_CO.get(a, 0.0) - _CO.get(b, 0.0)) for a, b in pairs]
            rep["pacer_paint"] = {
                "asked": len(want), "painted": len(pairs),
                "open_err": round(sum(diffs) / len(diffs), 3) if diffs else None}
        rep["pacer"] = True
        # A CALIBRATION SIGNAL HAS NO CONSONANTS. It is one class per frame
        # chosen by openness, so "consonants seen" and "closures hit" are
        # counting shapes that were never spoken — real numbers over the wrong
        # population. Only r, the lag and coverage mean anything here.
        rep["pacer"] = True
        for k in ("cons_seen", "closure_hit", "cons_runs", "closures"):
            rep.pop(k, None)
        if rep.get("r") is not None and rep.get("lag_frames") is not None:
            pp = rep.get("pacer_paint") or {}
            ok = float(rep["r"]) >= PACER_MIN_R
            rep["trustworthy"] = bool(ok)
            # WHAT THE PIPELINE COSTS. Each row is stamped PACER_LEAD_F frames
            # ahead of the frame it belongs to. If it lands at lag 0 the path
            # consumed exactly that lead; if it lands early the path is faster
            # than the lead given it. So the pipeline's own delay is the lead
            # MINUS the lag it measured, and that — not the raw lag — is the
            # number a zero should cancel.
            rep["pipeline_f"] = PACER_LEAD_F - int(rep["lag_frames"])
            rep["pipeline_ms"] = round(rep["pipeline_f"] * 1000.0 / float(CLIP_FPS), 1)
            head = ("CALIBRATION SIGNAL: the sheet takes %+d f to reach her face "
                    "(stamped %+d f ahead, landed %+d f; r %.2f, coverage %d%%)" % (
                        rep["pipeline_f"], PACER_LEAD_F, int(rep["lag_frames"]),
                        float(rep["r"]), round(100 * float(rep.get("coverage") or 0))))
            if pp.get("painted") is not None:
                head += " · %d of %d rows painted" % (pp.get("painted") or 0, pp.get("asked") or 0)
                if pp.get("open_err") is not None:
                    head += ", mean openness error %.2f" % pp["open_err"]
            if not ok:
                head += (" · NOT A MEASUREMENT: this signal is a copy of the answer key, so r "
                         "below %.2f means it did not arrive — the lag is measuring the signal, "
                         "not the pipeline" % PACER_MIN_R)
            # WHAT WAS HEARD, said on every pass. The track is a passenger: if
            # this number ever moves with the sound, the passenger is in the
            # measurement loop and the design is wrong.
            _snd = str(DRILL.get("pacer_sound") or "")
            rep["sound"] = ("film" if (_snd and _snd != "silent")
                            else ("silent" if _snd == "silent" else None))
            if rep["sound"] == "film":
                head += " · the film's own track played over it"
            elif rep["sound"] == "silent":
                head += " · this film is silent, so the pass ran without sound"
            rep["read"] = head + (" — her own mouth paced through the sheet, so nothing here is a speech score"
                                  if ok else "")
    if rep.get("read"):
        rep["read"] += " · sheet: " + rep["clock"]
    if rep.get("why"):
        # NAME THE MISSING SIDE (2026-09-16): "no match" on a half-intaken film
        # was read as a dial problem. Say which side is empty and what mints it.
        needs = DRILL.get("needs") or {}
        ph = STATE.get("playhead")
        stem = str(DRILL.get("key") or "")
        parts = []
        for w in str(rep["why"]).split(" · "):
            if w == "no tiles painted":
                if needs.get("tile_bank") is False:
                    w += " — this film has no tile bank (%s.playlist.json has no index): run intake" % stem
                elif ph is not None:
                    w += " — the bank exists; check skip (%s)" % getattr(ph, "skip", None)
            elif w == "no filmed openness":
                w += " — no map.json stamps and no per-frame open in the playlist: run the head track on this film"
            parts.append(w)
        rep["why"] = " · ".join(parts)
        rep["needs"] = needs
    pl = list(DRILL.get("place") or [])
    if pl:
        dxs = np.array([p["dx"] for p in pl]); dys = np.array([p["dy"] for p in pl]); wrs = np.array([p["w_ratio"] for p in pl])
        rep["place"] = {"n": len(pl), "dx_px": round(float(np.median(dxs)), 1), "dy_px": round(float(np.median(dys)), 1),
                        "spread_px": round(float(np.hypot(dxs.std(), dys.std())), 1), "w_ratio": round(float(np.median(wrs)), 3)}
        # the worst landings by distance, and how many sat within a tenth of
        # the head (s) of her mouth — the number the formula work moves
        dist = np.hypot(dxs, dys)
        ss = np.array([abs(float(p.get("dx_s") or 0)) + abs(float(p.get("dy_s") or 0)) for p in pl])
        order = np.argsort(-dist)[:8]
        rep["place"]["worst"] = [{"frame": pl[i].get("frame"), "klass": pl[i].get("klass"), "rel": pl[i].get("rel"),
                                  "dx": pl[i]["dx"], "dy": pl[i]["dy"], "w_ratio": pl[i]["w_ratio"],
                                  "pin": pl[i].get("pin"), "path": pl[i].get("path")} for i in order]
        rep["place"]["within_s10"] = round(float(np.mean(ss <= 0.1)), 2)
        rep["place"]["p90_px"] = round(float(np.percentile(dist, 90)), 1)
        rep["read"] = rep.get("read", "") + " · landed %+.0f/%+.0f px of her mouth, width ×%.2f" % (rep["place"]["dx_px"], rep["place"]["dy_px"], rep["place"]["w_ratio"])
    return rep


def drill_clock() -> str:
    """What clock this pass's sheet walked, read off house-speak's own line in the drill log:
    the film's timeline words, an alignment, or the letter clock (not a measurement)."""
    if DRILL.get("pacer_on"):
        # NAME IT (2026-09-21). A pacer pass is not speech and must never be
        # read as a speech score — its r is high by construction.
        return "calibration signal (her own mouth)"
    try:
        lines = Path(str(DRILL.get("log_path") or "")).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "unknown"
    seg = []
    for ln in reversed(lines):
        seg.append(ln)
        if ln.startswith("--- "):
            break
    for ln in seg:
        if ln.startswith("mouth clock: film words"):
            return "film words (timeline)"
        if ln.startswith("mouth clock: aligned"):
            return "aligned " + ln.split("aligned", 1)[1].strip()
        if ln.startswith("mouth clock: align failed"):
            return "LETTER CLOCK (align failed) — not a measurement"
    if any(ln.startswith("ok drill") for ln in seg):
        return "LETTER CLOCK (no alignment landed) — not a measurement"
    if not DRILL.get("voiced", True):
        return "Voice line over a silent loop (streamed, aligned as she speaks)"
    return "unknown"


def drill_stop(why: str = "") -> dict:
    # A STOP KEEPS THE LAST WHOLE PASS (2026-09-18): with loop on, the pass that
    # had just respawned held 24 frames when the User pressed Stop, its grade was
    # "r -9.00 · 0 changes/s", and it replaced pass 1's real score on the page.
    # A partial pass that cannot correlate (no r) never overwrites a graded one.
    if DRILL.get("log"):
        fresh = drill_report()
        prev = DRILL.get("report") or {}
        if fresh.get("r", -9.0) > -9.0 or not prev.get("read") or prev.get("r", -9.0) <= -9.0:
            DRILL["report"] = fresh
        else:
            fresh_n = int(fresh.get("painted") or 0)
            prev["note"] = "stopped %d frame(s) into the next pass — this is the last whole pass" % fresh_n
            DRILL["report"] = prev
    DRILL["vframe"] = None
    DRILL["on"] = False
    # STOP FEEDING THE SIGNAL (2026-09-21). pacer_on stayed true after a pass
    # ended, so the render loop went on posting rows into the mailbox between
    # passes and the NEXT pass opened on a sheet that was already running. That
    # is why the first pacer pass of a run read r 0.98 and every one after it
    # read r ~0 — it looked like an unstable instrument and was a flag nobody
    # cleared.
    DRILL["pacer_on"] = False
    DRILL["pacer_arm"] = False
    if DRILL.get("pacer_sound") not in (None, "silent"):
        clip_audio.stop()      # the film's track never outlives its pass
    p = DRILL.get("proc")
    if p is not None and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass
    try:
        ph = STATE.get("playhead")
        if ph is not None and ph.seq.camp == DRILL.get("key"):
            # BETWEEN PASSES SHE PARKS ON HER REST LOOP, NOT ON A SLIDE (the User
            # 2026-09-13 07:1x "why is it playing (bad) transitions?"): a free
            # one-shot end sent the sequencer home through whatever slide it
            # found while the tuner waited for a quiet air. Camp the rest loop
            # until the next play, cue or home.
            # 07:2x: parking on the rest loop still walked a slide (lila-sit-up);
            # the film holds its LAST FRAME until the next play, cue, home or pass
            DRILL["hold_frame"] = True
            DRILL["hold_key"] = DRILL.get("key")     # the park is this clip's (fault 14)
            ph.seq.camp = DRILL.get("key")
    except Exception:  # noqa: BLE001
        pass
    action("drill", "stop%s — %s" % ((" (%s)" % why) if why else "", (DRILL["report"] or {}).get("read", "no report")), "")
    return DRILL["report"] or {}
# buffer (the User 2026-09-13 02:5x, "she detaches from the script very early"):
# measured on the sentence, the first 16 s were painted from the letter clock
# and never corrected — the aligner lands only when the voice finishes generating.
# On, house-speak drains the voice first and aligns the WHOLE line before the first
# sample (HOUSE_SPEAK_BUFFER=1): exact classes end to end, at the cost of a
# pause before she speaks (~half the line). A tuning glass; off is the law.
MOUTH_BUFFER = os.environ.get("HOUSE_SPEAK_BUFFER", "0").strip().lower() in ("1", "true", "yes", "on")
# paint_all (the User 2026-09-13 05:5x: "mouth stamps on everything now since these
# are so good"): off = the law, tiles on rest loops only (mouth_paints_on);
# on = every role that has a mouth box — slides, flourishes, one-shots — so a
# talking clip is not a hostage loop. A flourish with a hand over the mouth
# still needs its own opt-out (`mouth: false` on the inventory row).
MOUTH_PAINT_ALL = os.environ.get("HOUSE_MOUTH_PAINT_ALL", "0").strip().lower() in ("1", "true", "yes", "on")
# A SLIDE PAINTS WHEN ITS OWN SIDECAR SAYS SO (the User 2026-09-26: the stamping — "the per-clip
# paint gate and rows from the head track for all sixteen slides"; Lila: "her transitions are
# her flourishes"). Paste was loops only (mouth_paints_on); a slide kept the filmed jaw and a
# flourish was cut to the hub's rest on air. The gate is per clip and lives in the clip's
# playlist (`paint: true`, written by scripts/pose-slide-playlist.py with the rows from the head
# track and the loop it paints from), read live through the playlist cache — a reload, not a
# restart. The covering-hand rule stays (`mouth: false` on the row). Kill HOUSE_SLIDE_PAINT=0.
SLIDE_PAINT = os.environ.get("HOUSE_SLIDE_PAINT", "1").strip().lower() not in ("0", "false", "no", "off")
_hf_mod.STAMP_EDGE_S = MOUTH_EDGE_S
_hf_mod.SAG_K = MOUTH_SAG_K
# NO VOICE RATE HERE (the User 2026-09-14: "make composer have nothing to do with voice tuning";
# "it should NOT change prod rate"): tempo is not a dial. The Composer's test Say speaks at the
# house's own HOUSE_SPEAK_TEMPO (1.0); only the slow-motion rate (½ ¼) multiplies it.
DIAL_ENV = {"buffer": "HOUSE_SPEAK_BUFFER", "paint_all": "HOUSE_MOUTH_PAINT_ALL", "xfade_ms": "HOUSE_MOUTH_XFADE_MS", "edge_s": "HOUSE_MOUTH_EDGE_S", "sag_k": "HOUSE_MOUTH_SAG_K",
            "sync_zero_ms": "HOUSE_MOUTH_SYNC_ZERO_MS", "sheet_lead_ms": "HOUSE_MOUTH_SHEET_LEAD_MS", "timing_lead_ms": "HOUSE_MOUTH_TIMING_LEAD_MS",
            "min_hold": "HOUSE_MOUTH_MIN_HOLD", "cons_hold": "HOUSE_MOUTH_CONS_HOLD",
            "sent_pad": "HOUSE_MOUTH_SENT_PAD", "word_pad": "HOUSE_MOUTH_WORD_PAD",
            "pause_words": "HOUSE_MOUTH_PAUSE_WORDS", "blip_words": "HOUSE_MOUTH_BLIP_WORDS",
            "tail_words": "HOUSE_MOUTH_TAIL_WORDS",
            "fit_under_pixel": "HOUSE_MOUTH_FIT_UNDER_PIXEL", "pin_hold": "HOUSE_MOUTH_PIN_HOLD", "warp": "HOUSE_MOUTH_WARP", "fit": "HOUSE_MOUTH_FIT", "pin": "HOUSE_MOUTH_PIN", "chin_pin": "HOUSE_MOUTH_CHIN_PIN",
            "hole": "HOUSE_MOUTH_HOLE", "anchor": "HOUSE_MOUTH_ANCHOR", "scale": "HOUSE_MOUTH_SCALE",
            "nose_u": "HOUSE_MOUTH_NOSE_U", "nose_v": "HOUSE_MOUTH_NOSE_V", "nose_ema": "HOUSE_MOUTH_NOSE_EMA",
            "turn_u": "HOUSE_MOUTH_TURN_U", "turn_v": "HOUSE_MOUTH_TURN_V",
            "neck_cut": "HOUSE_MOUTH_NECK_CUT", "idle_rest": "HOUSE_MOUTH_IDLE_REST", "alpha": "HOUSE_MOUTH_ALPHA"}
DIAL_CHOICES = {"warp": ("auto", "affine", "triad", "cheeks", "cheeks-tri", "dental", "plane", "diamond"),
                "fit": ("none", "width", "take", "take-uni"),
                "pin": ("nose", "lip", "nose+lip", "nose-pixel", "nose+chin-pixel"),   # nose+chin-pixel = a real choice (the User 2026-10-27)
                "hole": ("ellipse", "arch"), "anchor": ("haar", "head"), "alpha": ("square", "jaw", "auto")}


_EDGE_LOCK = {"mtime": None, "edge": None}

# THE FINISHED PATCH (lib/mouth_patch; the User 2026-10-13, and 2026-10-24: "every painted patch for each loop frame
# and tile could be built ahead"): a head paint whose inputs this process (or the install's build, lib/mouth_patch_disk)
# has painted before is copied back. HOUSE_MOUTH_PATCH_CACHE=1|0; HOUSE_MOUTH_PATCH_VERIFY=1 paints every hit as well
# and counts byte mismatches. Only with no dissolve (xfade_ms 0): a dissolve mixes the picture before, which no key holds.
_MOUTH_PATCH_ON = mouth_patch.enabled()
_MOUTH_PATCH_VERIFY = mouth_patch.verify_on()
_PATCH_DIAL_NAMES: list = []
# plain globals that are not settings: the process's start time (2026-10-24, measured: the only name the glass and the
# install's build disagreed on; HOUSE_MOUTH_PATCH_DEBUG_DIR=<dir> dumps every name each process keys on, to diff)
# LILA_PAINTS and MOUTH_HUSH are hooks Setup installs at boot (functions, 13902-13903): the glass's name list, taken
# after that, left them out, the build child's (taken before) carried them as None, and every key the build wrote
# missed on the glass (disk read 0 of 1175 on the 10-27 Kipling take; the prebake had never served a frame). Named here
# so both processes key on the same names whatever is installed when the list is first taken (2026-10-27).
_PATCH_NOT_DIALS = ("BOOT_T", "MOUTH_XFADE_MS", "MOUTH_FAST_TRACK", "XFADE_FOLLOW", "LILA_PAINTS", "MOUTH_HUSH")
_PATCH_TRACE = os.environ.get("HOUSE_MOUTH_PATCH_TRACE", "").strip()   # a file: one line per store take (diagnostic)   # the dissolve is blended from kept frames
# what a paint leaves on the playhead for the rest of the frame and /v1/state; a hit puts them back
_PATCH_SIDE = ("skip", "_pin_dbg", "_place_dbg", "_lip_dbg", "_lip_turned", "_tri_mode")


def patch_dials_print() -> str:
    """The plain settings a patch key reads, as one short print: /v1/state and the build's progress.json both carry it,
    so a build painted under other settings than the glass shows at a glance."""
    g = globals()
    if os.environ.get("HOUSE_MOUTH_PATCH_DEBUG_DIR"):
        try:
            (Path(os.environ["HOUSE_MOUTH_PATCH_DEBUG_DIR"]) / ("%d.json" % os.getpid())).write_text(
                json.dumps({n: repr(g.get(n)) for n in _patch_dial_names()}, indent=0))
        except OSError:
            pass
    return mouth_patch.digest(tuple((n, g.get(n)) for n in _patch_dial_names())).hex()[:12]


def _patch_dial_names() -> list:
    """Every UPPERCASE setting of this module with a plain value (the MOUTH_* dials and every other switch a paint may
    read), named once; their values are read into every key."""
    if not _PATCH_DIAL_NAMES:
        g = globals()

        def plain(v):
            if isinstance(v, (bool, int, float, str)) or v is None:
                return True
            return isinstance(v, tuple) and all(isinstance(x, (bool, int, float, str)) or x is None for x in v)

        _PATCH_DIAL_NAMES.extend(sorted(k for k, v in g.items() if k.isupper() and not k.startswith("_") and plain(v)
                                        and k not in _PATCH_NOT_DIALS))
    return _PATCH_DIAL_NAMES



def _edge_ratio() -> float:
    """The live edge dial over the library lock's (1 when the lock has none, or at the lock)."""
    try:
        mt = DIALS_FILE.stat().st_mtime
    except OSError:
        mt = None
    if _EDGE_LOCK["mtime"] != mt:
        _EDGE_LOCK["mtime"] = mt
        v = library_dials().get("edge_s")
        _EDGE_LOCK["edge"] = float(v) if isinstance(v, (int, float)) and float(v) > 0 else None
    lk = _EDGE_LOCK["edge"]
    return float(MOUTH_EDGE_S) / lk if lk else 1.0


def library_dials(path=None) -> dict:
    """The locked dials of this library, {} when there is no file."""
    try:
        d = json.loads((path or DIALS_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def apply_dials(d: dict, env: dict | None = None, honour_env: bool = True) -> dict:
    """Set the mouth globals from a dial dict. With honour_env, a key whose
    env var is set is left alone (boot); the tune handler passes False."""
    global MOUTH_WARP, MOUTH_FIT, MOUTH_PIN, MOUTH_HOLE, MOUTH_ANCHOR, MOUTH_SCALE, MOUTH_ALPHA, MOUTH_TURN_TRI
    global MOUTH_NOSE_U, MOUTH_NOSE_V, MOUTH_NOSE_EMA, MOUTH_NECK_CUT, MOUTH_IDLE_REST, MOUTH_FIT_UNDER_PIXEL, MOUTH_PIN_HOLD
    global MOUTH_CHIN_PIN
    global MOUTH_TURN_U, MOUTH_TURN_V
    global MOUTH_XFADE_MS, MOUTH_EDGE_S, MOUTH_SAG_K, MOUTH_SHEET_LEAD_MS, MOUTH_TIMING_LEAD_MS, MOUTH_MIN_HOLD, MOUTH_CONS_HOLD, MOUTH_TEMPO, MOUTH_BUFFER, MOUTH_PAINT_ALL, SYNC_ZERO_MS, MOUTH_SENT_PAD, MOUTH_WORD_PAD, MOUTH_PAUSE_WORDS, MOUTH_BLIP_WORDS, MOUTH_TAIL_WORDS
    env = os.environ if env is None else env
    applied = {}
    for k, v in (d or {}).items():
        if k not in DIAL_ENV or (honour_env and DIAL_ENV[k] in env):
            continue
        if k == "idle_rest" and idle_rest_pinned(env) is not None:
            continue                                  # round 36: the set env wins over every dial set
        if k in DIAL_CHOICES:
            v = str(v).strip().lower()
            if v not in DIAL_CHOICES[k]:
                continue
        try:
            if k == "scale": MOUTH_SCALE = max(0.5, min(2.0, float(v)))
            elif k == "nose_u": MOUTH_NOSE_U = max(-40.0, min(40.0, float(v)))
            elif k == "nose_v": MOUTH_NOSE_V = max(-40.0, min(40.0, float(v)))
            elif k == "turn_u": MOUTH_TURN_U = max(-40.0, min(40.0, float(v)))
            elif k == "turn_v": MOUTH_TURN_V = max(-40.0, min(40.0, float(v)))
            elif k == "nose_ema": MOUTH_NOSE_EMA = max(0.0, min(1.0, float(v)))
            elif k == "neck_cut": MOUTH_NECK_CUT = bool(v)
            elif k == "idle_rest": MOUTH_IDLE_REST = bool(v)
            elif k == "chin_pin": MOUTH_CHIN_PIN = bool(v)      # A REAL DIAL (the User 2026-10-27): the lock saves and restores it
            elif k == "pin":
                # ONE CONTROL, NOT A HIDDEN FLAG (the User 2026-10-27 "maybe add a real control"): nose+chin-pixel is a pin choice
                # a lock saves and restores; it is the pixel pin with the chin pin on, nose-pixel alone turns the chin pin off
                if v == "nose+chin-pixel": MOUTH_PIN, MOUTH_CHIN_PIN = "nose-pixel", True
                else: MOUTH_PIN, MOUTH_CHIN_PIN = v, (False if v == "nose-pixel" else MOUTH_CHIN_PIN)
            elif k == "fit_under_pixel": MOUTH_FIT_UNDER_PIXEL = bool(v)
            elif k == "pin_hold": MOUTH_PIN_HOLD = bool(v)
            elif k == "warp":
                MOUTH_TURN_TRI = v == "cheeks-tri"
                MOUTH_WARP = "cheeks" if MOUTH_TURN_TRI else v
            elif k == "fit": MOUTH_FIT = v
            elif k == "hole": MOUTH_HOLE = v
            elif k == "alpha": MOUTH_ALPHA = v
            elif k == "anchor": MOUTH_ANCHOR = v
            elif k == "xfade_ms": MOUTH_XFADE_MS = max(0.0, min(400.0, float(v)))
            elif k == "edge_s":
                MOUTH_EDGE_S = max(0.0, min(1.0, float(v)))
                _hf_mod.STAMP_EDGE_S = MOUTH_EDGE_S
            elif k == "sag_k":
                MOUTH_SAG_K = max(0.0, min(2.0, float(v)))
                _hf_mod.SAG_K = MOUTH_SAG_K
            elif k == "sheet_lead_ms": MOUTH_SHEET_LEAD_MS = max(-200.0, min(600.0, snap_lead_ms(v)))
            elif k == "sync_zero_ms": SYNC_ZERO_MS = max(-400.0, min(600.0, float(v)))
            elif k == "timing_lead_ms": MOUTH_TIMING_LEAD_MS = max(0.0, min(400.0, float(v)))
            # 36 hops = 720 ms = 17.3 frames at 24 fps; was 12 (5.8 f), which put
            # half a second out of reach (the User 2026-09-20)
            elif k == "min_hold": MOUTH_MIN_HOLD = int(max(0, min(36, int(float(v)))))      # 0 hops = no merge (2026-10-25)
            elif k == "cons_hold": MOUTH_CONS_HOLD = int(max(0, min(36, int(float(v)))))
            # 0 is meaningful here and is the default: no pad at all
            elif k == "sent_pad": MOUTH_SENT_PAD = int(max(0, min(36, int(float(v)))))
            elif k == "word_pad": MOUTH_WORD_PAD = int(max(0, min(36, int(float(v)))))
            elif k == "pause_words": MOUTH_PAUSE_WORDS = round(max(0.0, float(v)), 2)      # floor 0, no ceiling (the User 2026-10-27)
            elif k == "blip_words": MOUTH_BLIP_WORDS = round(max(0.0, float(v)), 2)
            elif k == "tail_words": MOUTH_TAIL_WORDS = round(max(0.0, float(v)), 2)
            elif k == "tempo": MOUTH_TEMPO = max(0.6, min(1.6, float(v)))
            elif k == "buffer": MOUTH_BUFFER = (v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on"))
            elif k == "paint_all": MOUTH_PAINT_ALL = (v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on"))
        except (TypeError, ValueError):
            continue
        applied[k] = v
    return applied


def _row_pitch(ph):
    """The playlist row's pitch for the frame the playhead is on (None when there is no row)."""
    try:
        pl = ph.lib.playlist(ph.key) or {}
        frs = pl.get("frames") or []
        fr = frs[min(max(int(ph.i), 0), len(frs) - 1)]
        return fr.get("pitch") if isinstance(fr, dict) else None
    except Exception:  # noqa: BLE001
        return None


def tune_reading(body: dict) -> None:
    """One row per dial change the bench is sent, with the frame it was sent on (MOUTH_TUNE_READINGS). Never raises."""
    try:
        dials = {k: body[k] for k in ("nose_u", "nose_v", "turn_u", "turn_v", "scale", "nose_ema", "edge_s", "xfade_ms",
                                      "warp", "pin", "fit", "hole", "alpha", "neck_cut") if k in body}
        if not dials:
            return
        ph = STATE.get("playhead")
        lp = dict(getattr(ph, "_lip_dbg", None) or {})
        row = {"t": round(time.time(), 3), "clip": getattr(ph, "key", None), "frame": int(getattr(ph, "i", -1)),
               "cell": lp.get("cell"), "yaw": lp.get("yaw"), "pitch": _row_pitch(ph),
               "placed": lp.get("placed"), "pin": getattr(ph, "_pin_dbg", None), "dials": dials,
               "who": str(body.get("who") or "")}
        path = ROOT / "logs" / ("tune-readings-%d.jsonl" % PORT)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001 - a log never costs a dial
        pass


def warp_dial() -> str:
    """The warp as the dial names it: cheeks + triangle is one choice."""
    return "cheeks-tri" if MOUTH_TURN_TRI else MOUTH_WARP


def live_dials() -> dict:
    return {"warp": warp_dial(), "fit": MOUTH_FIT, "pin": pin_dial(), "hole": MOUTH_HOLE, "alpha": MOUTH_ALPHA, "anchor": MOUTH_ANCHOR,
            "scale": MOUTH_SCALE, "nose_u": MOUTH_NOSE_U, "nose_v": MOUTH_NOSE_V, "nose_ema": MOUTH_NOSE_EMA,
            "turn_u": MOUTH_TURN_U, "turn_v": MOUTH_TURN_V,
            "neck_cut": MOUTH_NECK_CUT, "idle_rest": MOUTH_IDLE_REST, "fit_under_pixel": MOUTH_FIT_UNDER_PIXEL, "pin_hold": MOUTH_PIN_HOLD, "chin_pin": MOUTH_CHIN_PIN,
            "xfade_ms": MOUTH_XFADE_MS, "edge_s": MOUTH_EDGE_S, "sag_k": MOUTH_SAG_K,
            "sheet_lead_ms": MOUTH_SHEET_LEAD_MS, "sheet_lead_semis": round(lead_semis(), 4), "semi_ms": semi_ms(), "timing_lead_ms": MOUTH_TIMING_LEAD_MS,
            "min_hold": MOUTH_MIN_HOLD, "cons_hold": MOUTH_CONS_HOLD,
            "sent_pad": MOUTH_SENT_PAD, "word_pad": MOUTH_WORD_PAD,
            "pause_words": MOUTH_PAUSE_WORDS, "blip_words": MOUTH_BLIP_WORDS, "tail_words": MOUTH_TAIL_WORDS,
            "buffer": MOUTH_BUFFER, "paint_all": MOUTH_PAINT_ALL}


HISTORY_FILE = POSE_DIR / "mouth-dials.history.jsonl"


JOURNAL_FILE = POSE_DIR / "mouth-dials.journal.jsonl"
_JOURNAL_LAST = {"at": 0.0, "who": None}


def journal_rows(n: int = 60) -> list:
    """Every dial change and every drill pass, newest first, with ref ticks
    (the User 2026-09-13: "logging here will be key … tick a row as ref")."""
    try:
        rows = [json.loads(l) for l in JOURNAL_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return []
    return list(reversed(rows))[:n]


def _journal_write_all(rows_oldest_first: list) -> None:
    JOURNAL_FILE.write_text("".join(json.dumps(r) + "\n" for r in rows_oldest_first), encoding="utf-8")


def journal_add(kind: str, who: str = "", note: str = "", extra: dict | None = None) -> None:
    """One row per change; dial turns by the same hand within 2 s coalesce
    into the last row (a slider drag is one row, not twenty)."""
    now = time.time()
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "t": round(now, 2), "kind": kind, "who": who,
           "note": note, "dials": {**live_dials(), "velLo": VEL_LO, "velHi": VEL_HI}, "ref": False}
    if extra:
        row.update(extra)
    try:
        rows = journal_rows(400)[::-1]
        if kind == "dial" and rows and rows[-1].get("kind") == "dial" and rows[-1].get("who") == who \
                and now - float(rows[-1].get("t") or 0) < 2.0:
            rows[-1].update(row, ref=rows[-1].get("ref", False))
        else:
            rows.append(row)
        _journal_write_all(rows[-400:])
    except OSError:
        pass


def journal_ref(at: str, on: bool) -> bool:
    rows = journal_rows(400)[::-1]
    hit = False
    for r in rows:
        if str(r.get("at")) == str(at) or str(r.get("t")) == str(at):
            r["ref"] = bool(on); hit = True
    if hit:
        _journal_write_all(rows)
    return hit


def journal_row(at: str) -> dict | None:
    for r in journal_rows(400):
        if str(r.get("at")) == str(at) or str(r.get("t")) == str(at):
            return r
    return None


def dial_history(n: int = 12) -> list:
    """Past defaults, newest first (the User 2026-09-12: "have composer log
    changes for rollbacks"). Each line is what the file held BEFORE a lock,
    plus the lock's own stamp, so any of them can be locked again."""
    try:
        rows = [json.loads(l) for l in HISTORY_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return []
    return list(reversed(rows))[:n]


# BESTS — NAMED DIAL SETS (the User 2026-09-14: "I need a way to store many bests as the
# combos are impossible to remember … store combinations to A/B test against each
# other in a single elim pick winner test"). <pose-dir>/mouth-presets.json holds
# them; the Composer's Bests box saves / applies / brackets them; the last one
# standing (or any one) locks as the library default through lock_dials.
PRESETS_FILE = POSE_DIR / "mouth-presets.json"


def load_presets() -> list:
    try:
        d = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        rows = d.get("presets") if isinstance(d, dict) else d
        return [r for r in (rows or []) if isinstance(r, dict) and r.get("name")]
    except (OSError, ValueError):
        return []


def save_presets(rows: list) -> None:
    PRESETS_FILE.write_text(json.dumps({"presets": rows}, indent=1), encoding="utf-8")


# NAMED COMBOS, THE SAME PATTERN AS THE BESTS (the User 2026-09-16: "Named combos
# go on disk next to the library, same pattern as Bests: <pose-dir>/combos.json
# and /v1/combos on the bench. The open staging list stays in the browser").
# They were localStorage — per origin and per browser, so a hostname change hid
# them and Lila's page could never see Crissa's. A named chain of her clips is a
# fact about the library, and the conductor is the thing that plays it.
# inventory.json is not touched: a combo is not a clip.
COMBOS_FILE = POSE_DIR / "combos.json"
_combos_lock = threading.Lock()


def load_combos() -> dict:
    try:
        d = json.loads(COMBOS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {str(n)[:60]: [str(k) for k in v if isinstance(k, str)][:64]
            for n, v in d.items() if isinstance(n, str) and isinstance(v, list)}


def save_combo(name: str, keys: list | None) -> tuple[int, dict]:
    """One row written, or dropped when keys is None. Whole file under a lock
    through a temp file: a half-written combos.json would take every saved
    chain with it."""
    name = str(name or "").strip()[:60]
    if not name:
        return 400, {"ok": False, "error": "name it first"}
    with _combos_lock:
        all_ = load_combos()
        if keys is None:
            all_.pop(name, None)
        else:
            all_[name] = [str(k) for k in keys][:64]
        tmp = COMBOS_FILE.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(all_, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(COMBOS_FILE)
        except OSError as e:
            return 500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        return 200, {"ok": True, "combos": all_}


def preset_dials() -> dict:
    """The live dials plus the two motion-fade numbers the journal carries."""
    return dict(live_dials(), velLo=float(VEL_LO), velHi=float(VEL_HI))


def apply_preset(row: dict) -> None:
    global VEL_LO, VEL_HI
    d = dict(row.get("dials") or {})
    apply_dials({k: v for k, v in d.items() if k in DIAL_ENV}, honour_env=False)
    if isinstance(d.get("velLo"), (int, float)):
        VEL_LO = float(d["velLo"])
    if isinstance(d.get("velHi"), (int, float)):
        VEL_HI = float(d["velHi"])


# CLIP-SPECIFIC DIALS (the User 2026-09-14: "What if one clip is a talking profile and the
# other is face on? One is triad and the other may be bezier. Can it be clip-specific?").
# <pose-dir>/mouth-dials.clips.json: {clips: {key: {dials, at, by, best}}}. When the
# playhead enters a clip that has a set, the live dials are snapshotted and the set
# applied; leaving for a clip without one restores the snapshot. The library default
# (mouth-dials.json) is untouched — a clip set is a Bests "lock … this clip only".
CLIP_DIALS_FILE = POSE_DIR / "mouth-dials.clips.json"
CLIP_DIALS: dict = {"on": None, "prev": None}


def load_clip_dials() -> dict:
    try:
        d = json.loads(CLIP_DIALS_FILE.read_text(encoding="utf-8"))
        c = d.get("clips") if isinstance(d, dict) else None
        return {str(k): v for k, v in (c or {}).items() if isinstance(v, dict) and v.get("dials")}
    except (OSError, ValueError):
        return {}


def save_clip_dials(clips: dict) -> None:
    CLIP_DIALS_FILE.write_text(json.dumps({"clips": clips}, indent=1), encoding="utf-8")


# EACH FACE WEARS ITS OWN DIALS (2026-09-17, the User: "merge those services to keep each
# agent settings distinct"). Dials are module globals booted once from POSE_DIR, while
# FACE_BANKS resolves a guest's films, tiles, map and playlists — so :8100 painted
# Lila's rows with CRISSA's dials, and her lock reached the wall only through a second
# process. Now, when the playhead enters a clip that belongs to a guest face, the home
# dials are snapshotted and that face's locked file (<bank>/mouth-dials.json, read
# fresh at every entry — her lock needs no push and no restart) goes live; the first
# home clip after it restores the snapshot. A clip's own set still goes on top. Only
# the keys her file carries change; the rest stay the home's. Off: HOUSE_FACE_DIALS=0.
FACE_DIALS_ON = os.environ.get("HOUSE_FACE_DIALS", "1").strip().lower() not in ("0", "false", "no", "off")
FACE_DIALS: dict = {"on": None, "prev": None}


def face_dials_enter(key: str) -> None:
    """Before clip_dials_enter: the face layer sits UNDER a clip's own set."""
    if not FACE_DIALS_ON:
        return
    face = face_of(key)
    if face == FACE_DIALS.get("on"):
        return
    # a clip set from the face we are leaving comes off first, so the snapshot
    # we restore (or take) is never a clip's
    if CLIP_DIALS.get("on") is not None:
        prev = CLIP_DIALS.get("prev") or {}
        if prev:
            apply_preset({"dials": prev})
        CLIP_DIALS["on"] = None
        CLIP_DIALS["prev"] = None
    if FACE_DIALS.get("on") is not None:
        home = FACE_DIALS.get("prev") or {}
        if home:
            apply_preset({"dials": home})
        action("face-dials", "%s left — home dials back (%s)" % (FACE_DIALS["on"], dial_brief(home)), "playhead")
        FACE_DIALS["on"] = None
        FACE_DIALS["prev"] = None
    if face:
        theirs = library_dials(FACE_BANKS[face] / "mouth-dials.json")
        if theirs:
            FACE_DIALS["prev"] = preset_dials()
            apply_preset({"dials": theirs})
            FACE_DIALS["on"] = face
            action("face-dials", "%s ← %s" % (face, dial_brief(theirs)), "playhead")


def face_dials_refresh(face: str | None = None) -> bool:
    """A default re-read while a guest is on the glass: the HOME file becomes the
    snapshot (what comes back when she leaves) and her own file is re-applied."""
    on = FACE_DIALS.get("on")
    if not on or (face and face != on):
        return False
    home = dict(FACE_DIALS.get("prev") or {})
    home.update({k: v for k, v in library_dials().items() if k in home})
    FACE_DIALS["prev"] = home
    theirs = library_dials(FACE_BANKS[on] / "mouth-dials.json")
    if theirs:
        apply_preset({"dials": theirs})
    return True


# A CLIP WITH NO SET WEARS ITS HUB LOOP'S (the User 2026-09-26: "We used to have a library
# fallback default in case not set. Any main hub would do for that. Failsafe setting to
# common loop from hub"). The chain is own set -> the hub's common loop's set -> the library
# lock. A slide or flourish carries no set of its own, so until now it fell straight to the
# lock while the loop it paints from had his tune: the loop named by its sidecar's `library`
# (what pose-slide-playlist stamps), else the rest loop of the hub it lands on.
# Kill HOUSE_CLIP_DIALS_HUB_FALLBACK=0.
HUB_SET_FALLBACK_ON = os.environ.get("HOUSE_CLIP_DIALS_HUB_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off")


def hub_loop_for(key: str) -> str | None:
    """The loop whose set a set-less clip wears: its sidecar's `library`, else the
    rest loop of the hub it lands on. None when neither is known."""
    try:
        ph = STATE.get("playhead")
        if ph is None:
            return None
        pl = ph.lib.playlist(key) or {}
        lib = str(pl.get("library") or "").strip()
        if lib:
            return lib
        g = ph.seq.graph
        return g.rest_for(g.hub_after(key))
    except Exception:  # noqa: BLE001 — a fallback that fails is just no fallback
        return None


def clip_dials_enter(key: str) -> None:
    """The playhead is on `key` now: put its set live, or restore what was live
    before the last set. Called from Playhead.play after the key changes."""
    try:
        face_dials_enter(key)
        clips = load_clip_dials()
        row = clips.get(str(key))
        src = str(key)
        if not row and HUB_SET_FALLBACK_ON:
            loop = hub_loop_for(str(key))
            if loop and loop != str(key) and clips.get(loop):
                row, src = clips[loop], loop
        if row:
            if CLIP_DIALS.get("on") is None:
                CLIP_DIALS["prev"] = preset_dials()
            if CLIP_DIALS.get("on") != src:
                apply_preset(row)
                CLIP_DIALS["on"] = src
                action("clip-dials", "%s ← %s%s" % (key, dial_brief(row.get("dials") or {}),
                                                    "" if src == str(key) else " (hub loop %s)" % src), "playhead")
        elif CLIP_DIALS.get("on") is not None:
            prev = CLIP_DIALS.get("prev") or {}
            if prev:
                apply_preset({"dials": prev})
            action("clip-dials", "%s left — dials restored (%s)" % (CLIP_DIALS["on"], dial_brief(prev)), "playhead")
            CLIP_DIALS["on"] = None
            CLIP_DIALS["prev"] = None
    except Exception as ex:  # noqa: BLE001 — a bad clips file must not stop the playhead
        print("[conductor] clip-dials: %s" % ex, flush=True)


def lock_dials(who: str = "composer") -> dict:
    """Write the live dials to the library file.

    THE LOCK SAVES WHAT A BEST SAVES (the User 2026-09-15, before a day of
    per-loop bests). This used to write live_dials(), which omits velLo and
    velHi — so the motion-fade thresholds were captured by a Best
    (preset_dials adds them) and by the journal, but silently dropped by
    "lock as library default", and dropped again at boot because DIAL_ENV had
    no entry for them. Tune the fade, save a best, lock it, restart: the fade
    was back to 1.2/3.0 with nothing saying so. preset_dials() is the one
    definition of "every dial", so both paths use it.
    """
    prev = library_dials()
    if FACE_DIALS.get("on"):
        # the handlers answer 409 before this; if any path still gets here, the
        # home file is NOT written with a guest's dials (it happened once, in
        # the first test of this feature, 2026-09-17)
        action("lock", "REFUSED — %s's dials are live, not %s's" % (FACE_DIALS["on"], POSE_DIR.name), who)
        return prev
    d = dict(preset_dials(), locked=time.strftime("%Y-%m-%dT%H:%M:%S"), by=who)
    d["sync_zero_ms"] = SYNC_ZERO_MS      # the calibration rides the lock, never a Best (2026-09-18)
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": d["locked"], "by": who, "was": prev, "now": {k: d[k] for k in preset_dials()}}) + "\n")
    except OSError:
        pass
    DIALS_FILE.write_text(json.dumps(d, indent=1), encoding="utf-8")
    action("lock", "default ← %s (was %s)" % (dial_brief(d), dial_brief(prev)), who)
    return d


# A CUE OUTLIVES THE LONGEST LOOP (the User 2026-09-28): a cue waits for a seam, and a loop's seam is its
# wrap. The old 12 s was shorter than Crissa's listen loop (403 f, 16.8 s), so a cue made just after it
# wrapped expired before the next one and silently did nothing (found by the pack acceptance). The
# default is the longest element loop in this library plus 2 s, never under 12. Kill
# HOUSE_CUE_TTL_FROM_LOOPS=0 = the flat 12 s.
_CUE_TTL: dict = {}


def cue_ttl_default(ph) -> float:
    if os.environ.get("HOUSE_CUE_TTL_FROM_LOOPS", "1").strip().lower() in ("0", "false", "no", "off"):
        return 12.0
    if "s" not in _CUE_TTL:
        longest = 0
        try:
            for k, r in (ph.lib.rows or {}).items():
                if r.get("role") == "loop" and r.get("element") is not False:
                    longest = max(longest, int(ph.lib.head_track_len(k) or 0))
        except Exception:  # noqa: BLE001 - a library that cannot be read keeps the old default
            longest = 0
        _CUE_TTL["s"] = max(12.0, longest / float(CLIP_FPS or 24.0) + 2.0)
    return _CUE_TTL["s"]


def dial_brief(d: dict) -> str:
    return " ".join("%s=%s" % (k, d.get(k)) for k in ("warp", "fit", "pin", "scale", "nose_u", "nose_v", "fit_under_pixel", "pin_hold", "xfade_ms", "edge_s", "sag_k", "sheet_lead_ms") if k in d)


# THE LAST FEW THINGS IT DID, for the Composer's own Queue box (the User
# 2026-09-16: "Queue: Keep this box and show what Composer is doing. Like its
# logs … its thinking scroll … just a few scrolled lines under its queing and
# history"). The journal already has every line and the CP has the tile; what
# was missing was the operator being able to see it on the page they are
# working on, without leaving it. A short ring, not a log file: 40 lines is
# more than the strip shows and small enough to ride /v1/state.
ACTION_LOG: collections.deque = collections.deque(maxlen=40)


def action(kind: str, what: str, who: str = "") -> None:
    """ONE LINE PER ACTION on stdout → the unit's journal → the CP's log
    tile for this conductor (the User 2026-09-12: "have all logs unify from
    Composer/conductor actions into their logs tiles"). Say, play, cue,
    hold, dial, lock, default."""
    print("[conductor] %s: %s%s" % (kind, what, (" (%s)" % who) if who else ""), flush=True)
    ACTION_LOG.append({"ts": round(time.time(), 2), "kind": str(kind),
                       "what": str(what)[:160], "who": str(who or "")[:40]})


_booted = apply_dials(library_dials())
# velLo / velHi are not DIAL_ENV entries (they are plain module floats, not
# env-backed), so apply_dials cannot carry them. The library file holds them
# since 2026-09-15; restore them here the same way apply_preset does, or a
# locked fade would not survive the restart that locking is for.
_boot_vel = library_dials()
for _k, _g in (("velLo", "VEL_LO"), ("velHi", "VEL_HI")):
    if isinstance(_boot_vel.get(_k), (int, float)):
        globals()[_g] = float(_boot_vel[_k])
        _booted[_k] = float(_boot_vel[_k])
if _booted:
    print("[conductor] mouth dials from %s: %s" % (DIALS_FILE, _booted), flush=True)
# Dest-frame dental cache (poly_cache Layer C). Read at call time.
# HOUSE_MOUTH_DENTAL_CACHE=0 rolls back to fit+maps every paste.
# HOUSE_MOUTH_DENTAL_MAPS bounds the maps LRU (default 256).
HEAD_GEOM = _avatar_head_geom()


# Roll per frame per clip, built offline by scripts/pose-roll-table.py. Absent
# file or absent frame = no rotation; tilt matching must fail open.
_ROLL_PATH = POSE_DIR / "visemes" / "phoneme-map" / "roll.json"
try:
    ROLL_TABLE = json.loads(_ROLL_PATH.read_text()) if _ROLL_PATH.is_file() else {}
except (OSError, ValueError):
    ROLL_TABLE = {}


def _roll_of(clip_stem: str, i: int) -> float | None:
    r = ROLL_TABLE.get(clip_stem)
    if not r or not (0 <= i < len(r)):
        return None
    v = r[i]
    return float(v) if v is not None else None


def _tile_roll(rel: str) -> float | None:
    """Roll the tile was filmed at, from its name: <clip>_f0074_level_0.png (a mirrored tile: negated)."""
    rel, mirrored = _unmirror(rel)
    if mirrored:
        r = _tile_roll(rel)
        return None if r is None else -r
    try:
        base = str(rel).rsplit("/", 1)[-1]
        pre, rest = base.split("_f", 1)
        n = int(rest.split("_", 1)[0])
    except (ValueError, IndexError):
        return None
    for stem in ROLL_TABLE:
        if stem.endswith(pre):
            return _roll_of(stem, n)
    return None
# Mute the viseme paint without deleting the system. Strip load, punch-out,
# /v1/mouth poll and the glass everLive latch stay. {"level": 0} still pastes
# the closed tile — that is not off. HOUSE_CONDUCTOR_VISEMES=0 boots muted;
# POST /v1/mouth-tune {"on": false} is the live switch.
VISEMES = os.environ.get("HOUSE_CONDUCTOR_VISEMES", "1") != "0"
# Wav2Lip keep-up. gpu|cpu|off. The PCM hops it needs are live (house-speak
# posts samples beside the envelope in the same /v1/mouth body), so this is a
# real switch now rather than a plan.
#
# THE VISEME STRIP IS PROD, AND IT STAYS PROD. the User, 2026-08-31: "we have a
# composer function that is an alternative that uses visemes so please preserve
# that state as our fallback prod mode." Every honest miss below — no box, no
# audio window, held jaw stale, session down, GPU taken by Comfy — falls
# THROUGH to the strip rather than showing nothing, and `w2l: off` is the whole
# rollback. The neural mouth is a tenant on that path, never a replacement for
# it. POST /v1/mouth-tune {"w2l": "off"} flips back live, no restart.
W2L_MODE = os.environ.get("HOUSE_W2L", "off")
# THE RENDERER IS NOT THE PLAYER. She plays at 24; the model generates at 8 and
# the two frames between re-use that jaw over the live face (lib/w2l.Live).
# Measured 2026-08-30, full pipeline: GPU 10.6 ms, CPU 45 ms a frame. Against a
# 41 ms player budget the CPU limp cannot keep up and the GPU has no room for
# Comfy beside it; against 8 fps's 125 ms both fit with the card mostly idle.
# 0 = generate every frame (the 2026-08-30 behaviour).
# MATCH THE WALL. the User's eye picked matched-24 on 2026-09-01 — "holds are a
# staircase" — and the doc default of 8 was overturned there, but the code kept
# shipping 8, so every mouth was still being HELD for three frames.
#
# A held mouth is drawn where her jaw WAS up to 125 ms ago while the filmed jaw
# has moved on, at full opacity — which is the two-jaws artefact. On a matched
# grid there are no holds at all: measured live 21 holds against 9 gens at 8,
# and generation costs 3.99 ms, so 24 fps is 96 ms of GPU per wall-second,
# under 10%. The cheaper grid was never buying anything the eye wanted.
#
# The CPU limp still coarsens to W2L_CPU_GRID rather than falling over.
W2L_FPS = float(os.environ.get("HOUSE_W2L_FPS", "24") or 24.0)
# Jaw prepares for the sound. 100–200 ms; 0 disables. Not MOUTH_LATENCY_MS.
VISEME_LEAD_MS = viseme_lead_ms()
W2L_MS = 0.0
W2L_MISSES = 0
W2L_GENS = 0            # frames the model actually drew
W2L_HOLDS = 0           # frames served from the held jaw
# WHY THE STRIP GOT THE FRAME, counted. A fall-through is designed behaviour,
# but "23% of frames went to the strip" is only a shape until you can see WHICH
# honest miss did it — the reason used to be written into `skip` and then
# overwritten by the strip's own success on the very same frame.
W2L_WHY: dict = {}
_W2L_LIVE = None
_W2L_TRIED = 0.0        # wall time before which we do not retry the load
W2L_RETRY_S = 60.0
GPU_MODE_FILE = Path(
    os.environ.get("HOUSE_GPU_MODE", str(crissa_home.HOME / "house-gpu-mode"))
)


def _occupancy_mode() -> str:
    try:
        raw = GPU_MODE_FILE.read_text(encoding="utf-8").strip().split()
    except OSError:
        return ""
    return (raw[0] if raw else "").strip().lower()


_CRISSA_GW = {"t": 0.0, "up": True}


def _crissa_gateway_up() -> bool:
    """Cached. Occupancy park or systemd down → Lila hosts the Wall."""
    now = time.time()
    if now - float(_CRISSA_GW["t"]) < 2.0:
        return bool(_CRISSA_GW["up"])
    try:
        from crissa_gateway import crissa_gateway_up
        up = crissa_gateway_up()
    except Exception:
        up = True
    _CRISSA_GW["t"] = now
    _CRISSA_GW["up"] = up
    return up


def _lila_host_key(key: str) -> bool:
    return str(key or "").startswith("lila-")


def _house_parked() -> bool:
    """Occupancy is in a park mode (render / beast / off)."""
    try:
        from crissa_gateway import occupancy_mode, PARK_MODES
        return occupancy_mode() in PARK_MODES
    except Exception:  # noqa: BLE001
        return False


def _crissa_idle_steal(key: str) -> bool:
    # IN STANDARD, CRISSA'S WALL STAYS CRISSA'S (the User 2026-09-15: "fix the wall
    # booting onto lila-work when in Standard only ... Conductor clips should
    # be playing there").
    #
    # The steal is a RENDER affordance: Lila hosts the glass while Crissa's
    # gateway is parked. But crissa_gateway_up() falls back to
    # `systemctl --user is-active` on her gateway unit with a 1.5 s timeout,
    # and during a restart that reads "activating" — not "active" — so the
    # steal fired on the first seam after every boot and dealt lila-work on a
    # normal-flag house with her gateway perfectly healthy. Measured: boot
    # rest `work`, then lila-work at the first seam, then Crissa's loops again
    # once the unit settled.
    #
    # Outside a park mode there is no host to hand to, so do not hand over.
    if BOOT_CRISSA_REST and not _house_parked():
        return False
    try:
        from crissa_gateway import crissa_idle_steal
        return crissa_idle_steal(key)
    except Exception:
        return str(key or "") in ("work",)


# The grid the CPU limp can actually hold. Measured 2026-08-30, full pipeline
# on CPU: 45 ms a frame against a 41 ms player budget — 24 MISSES there, which
# is the whole reason the grid exists. GPU is 10.6 ms and holds any grid.
W2L_CPU_GRID = 8.0


def _w2l_grid(live) -> float:
    """What this session can actually render at, not what was asked for.

    "Crank it to 24" is a GPU sentence. When Comfy takes the card and
    `_w2l_device()` limps her to CPU, 24 is a promise the box cannot keep and
    the frames would go to the strip one at a time. Drop to the grid the CPU
    holds instead — the mouth coarsens, it does not fall over.
    """
    want = W2L_FPS
    if "CUDA" in (getattr(live, "provider", "") or ""):
        return want
    return W2L_CPU_GRID if want <= 0 or want > W2L_CPU_GRID else want


# ── PIP SURFACE ──────────────────────────────────────────────────────────────
# The FaceTime window and the ASCII wall drew the SAME composite (FEED.img,
# voiceonly-home.html:4449), so turning the neural mouth on for one turned it
# on for both. the User wants them split: a live PIP that can carry Muse while the
# wall stays on the strip.
#
# This does NOT re-run _composite. That function ticks the mouth, advances the
# velocity fade and moves last_box; calling it twice would double-advance all
# of it. Instead the PIP gets its own w2l paint on the RAW base frame, which is
# already a clean side-effect-free call.
#
#   ""      mirror the wall exactly (default — nothing changes)
#   off     PIP carries no neural mouth
#   gpu|cpu PIP carries the neural mouth at that device
PIP_W2L = os.environ.get("HOUSE_PIP_W2L", "").strip().lower()
_PIP_LIVE = None
_PIP_LOCK = threading.Lock()


def _pip_live():
    """A SECOND w2l session for the PIP. Separate from the wall's so the two
    surfaces can differ; costs one more ORT context (~950 MiB measured)."""
    global _PIP_LIVE
    if PIP_W2L in ("", "off"):
        return None
    with _PIP_LOCK:
        if _PIP_LIVE is None:
            try:
                # w2l is imported inside _w2l_live(), so the name is local to
                # THAT function — it was never in scope here. Since 6305466 the
                # PIP has raised NameError on every attempt and silently carried
                # no neural mouth, because the handler below swallows it.
                import w2l
                _PIP_LIVE = w2l.Live(device=PIP_W2L, fps=W2L_FPS)
            except Exception as e:
                print("[pip] w2l session failed: %r" % (e,), flush=True)
                _PIP_LIVE = False
    return _PIP_LIVE or None


def _w2l_device() -> str:
    """Render/Beast limp on CPU even if the unit drop-in says gpu.

    Do not bounce the conductor: dropping a CUDA session here is enough.
    """
    if W2L_MODE == "off":
        return "off"
    if _occupancy_mode() in ("render", "beast"):
        return "cpu"
    return W2L_MODE


def _w2l_live():
    """One session, made on first paint. Never at import: a wall that boots
    without a GPU must still come up, and a model load must not sit in the
    render loop's first frame."""
    global _W2L_LIVE, _W2L_TRIED
    want = _w2l_device()
    if want == "off":
        return None
    if _W2L_LIVE is not None:
        have = getattr(_W2L_LIVE, "provider", "") or ""
        if want == "cpu" and "CUDA" in have:
            print("[w2l] drop CUDA for occupancy cpu limp", flush=True)
            _W2L_LIVE = None
        else:
            return _W2L_LIVE
    # RETRY, DO NOT STRAND. A sticky "tried" meant one failed load — Comfy
    # holding the card during a render window — left her on the strip until
    # someone restarted the conductor, long after the VRAM came back. Back off
    # instead: retry on a slow cadence so a transient full card costs a minute,
    # not the rest of the day.
    now = time.time()
    if now < _W2L_TRIED:
        return None
    _W2L_TRIED = now + W2L_RETRY_S
    try:
        import w2l
        live = w2l.Live(device=want, fps=W2L_FPS)
        print("[w2l] %s provider=%s fps=%s load=%.2fs%s" % (
            want, live.provider, live.fps, live.sess.load_s,
            (" err=" + live.err) if live.err else ""), flush=True)
        _W2L_LIVE = live if live.ok else None
    except Exception as e:  # noqa: BLE001
        print("[w2l] unavailable: %s: %s" % (type(e).__name__, e), flush=True)
        _W2L_LIVE = None
    return _W2L_LIVE

# Composer kit — DJ deck. Home is the laptop (eyes on the top bevel). Every
# flourish request starts there; Composer brings her home first if she is away.
# THE KIT IS TWO SETS, AND THE SET IS THE HUB. the User, 2026-08-31: "let's make
# two sections and show the tiny thumbnail headers to show the anchor slide for
# each. This way the sections are clear as refs. Sitting up vs working
# flourishes."
#
# The old grouping was five hand-named labels ("From home", "Away") that did not
# say which reference frame a clip was filmed against, so the operator could not
# see that two sections were two camera positions. Sections are now DERIVED from
# the start hub and headed by that hub's plate, which is the same PNG the attach
# law checks every clip against — the picture at the top of a section is
# literally the frame every clip under it begins and ends on.
# ONE BUTTON PER PICTURE. The kit is derived from the inventory, not hand-listed:
# a clip Grok mints and wires shows up on its own — the three back grabbers of
# 2026-08-31 arrived this way with no edit here. Two rules keep it honest:
#
#   compose_ok  — both hubs named and element true, so it can be reasoned about
#   one file    — `listen`, `listen-guest`, `speak` and `speak-live` are FIVE
#                 names for clip-listen-back.mp4. They are conversation states,
#                 not five pictures, and five identical buttons is the same
#                 vocabulary problem measured at the back hub: one file, one
#                 button, named by whichever key the operator was shown before.
#   casual      — spectacular takes, element false, no hubs. Formal is the
#                 office register. Casual is visible and queueable only
#                 while the wall visit lamp is sage (Secure). Not sage:
#                 invisible, ineligible, cancelled from the queue, and
#                 aborted on screen with no wind-down.
KIT_PREFERRED = (
    "work", "flourish-shift", "flourish-tuck-work", "flourish-glance",
    "flourish-nod", "flourish-notes", "flourish-makeup-am",
    "emote-surprise", "emote-woe", "work-door", "peek",
    "wake", "rest",
    "vocative-up", "vocative-down",
    "listen-user",   # listen-alert / bridge-alert / bridge-calm / soften retired 2026-09-14 (the User: bad video)
    "talking-head",
    "laptop-closeup-eyes-down",
    "laptop-closeup-enter",
    "laptop-closeup-leave",
)


def kit_keys(graph) -> list[str]:
    """Every distinct wall picture, preferred name first, curated order first."""
    by_file: dict[str, str] = {}
    for key in graph.clips:
        if not graph.compose_ok(key):
            continue
        if graph.casual(key):
            continue
        f = str((graph.row(key) or {}).get("file") or key)
        cur = by_file.get(f)
        if cur is None:
            by_file[f] = key
        elif key in KIT_PREFERRED and cur not in KIT_PREFERRED:
            by_file[f] = key
        elif key in KIT_PREFERRED and cur in KIT_PREFERRED:
            if KIT_PREFERRED.index(key) < KIT_PREFERRED.index(cur):
                by_file[f] = key
    keys = list(by_file.values())
    if USER_TALK_KEY in graph.clips and USER_TALK_KEY not in keys:
        # Always offer User-talk even if a kit filter dropped it.
        keys.append(USER_TALK_KEY)
    order = {k: i for i, k in enumerate(KIT_PREFERRED)}
    keys.sort(key=lambda k: (order.get(k, len(order)), k))
    return keys


def casual_kit_keys(graph) -> list[str]:
    """One button per picture, Casual only. listen/speak share a file."""
    by_file: dict[str, str] = {}
    for key in graph.casuals():
        f = str((graph.row(key) or {}).get("file") or key)
        cur = by_file.get(f)
        if cur is None:
            by_file[f] = key
        elif key in KIT_PREFERRED and cur not in KIT_PREFERRED:
            by_file[f] = key
        elif key in KIT_PREFERRED and cur in KIT_PREFERRED:
            if KIT_PREFERRED.index(key) < KIT_PREFERRED.index(cur):
                by_file[f] = key
    keys = list(by_file.values())
    order = {k: i for i, k in enumerate(KIT_PREFERRED)}
    keys.sort(key=lambda k: (order.get(k, len(order)), k))
    return keys
# back_b is the alert variant of the same seated-forward angle, not a third set.
HUB_FOLD = {"back_b": "back"}
HUB_PLATE = {"down": "hub-down.png", "back": "hub-back.png",
             "back_b": "hub-back-b.png", "up": "hub-up.png",
             "down_close": "hub-down-close.png"}
# THE INVENTORY OWNS THE HUBS. A hub added to inventory.json (down_talk,
# 2026-09-06) 404'd on /plate/ because this dict was the allowlist. Fold the
# inventory's `hubs` in at import so a new anchor's plate serves without an
# edit here; the literals above stay as the floor for a missing file.
try:
    HUB_PLATE.update({str(k): str(v) for k, v in
                      (inv_load().get("hubs") or {}).items()
                      if v})
except (OSError, ValueError, AttributeError):
    pass
KIT_SECTIONS = (
    ("down",   "Working", "heads down at the laptop"),
    ("down_close", "Laptop-talking", "User talk from the laptop — most common"),
    ("back",   "Formal / Conference", "webcam meeting · sit up"),
    ("travel", "Travel", "the crossings — a cross-set pick brings its own"),
    ("up",     "Close-face", "presentation"),
)
CASUAL_SECTIONS = (
    ("down_close", "Up close", "Casual Comms — lean-in talking"),
    ("down",   "At the desk", "playful — not for a guest"),
    ("back",   "Sat back", "scooted back, knees up"),
    ("travel", "The scoot", "push back / lean in"),
    ("up",     "Close-face", "intimate"),
    ("takes",  "Takes", "Take1, wide, and other off-wall keeps"),
)
KIT_LABELS = {
    **WHAT_THEY_ARE,
    "work": "heads down · desk",
    "work-door": "look at the door",
    "peek": "head up, earpiece",
    "talking-head": "heads up · close",
    "take1-look": "Take1 look",
    "take1-speak": "Take1 speak",
    "look-wide": "wide look",
    "speak-wide": "wide speak",
    "laptop-closeup-eyes-down": "webcam · User",
    "laptop-closeup-enter": "webcam view (library)",
    "laptop-closeup-leave": "fade to room (library)",
}


# The library's own shape, as a fallback. The tuples above stay where they are
# and stay authoritative for a box that has not declared `racks`: they cost
# nothing as a fallback, and the payload below is byte-identical to what it has
# always been when the inventory is silent.
DEFAULT_CATEGORIES = (
    {"id": "formal", "label": "Formal",
     "sub": "office — working and presentation"},
    {"id": "casual", "label": "Casual",
     "sub": "Secure off disables these flourishes",
     "note": ("Paused / Listening is unaffected. AUTH holds until a "
              "new door face or the Secure button. FaceFinder comms "
              "halt while Secure."),
     "secure": True},
)


def rack_category(c) -> dict | None:
    """One `rack_categories` entry as the menu's dict. A NEW LIBRARY WRITES BARE IDS (the User 2026-10-16, "fix the
    new library menu crash"): imagine-pack intake-house and the Composer's new-library write `["formal"]`, and
    `dict("formal")` raised on every /v1/kit - the bench logged a traceback every few seconds and the Composer's
    clip menu got nothing. A bare id is the default category of that id, else a plain one named after it."""
    if isinstance(c, dict):
        return dict(c) if c.get("id") else None
    if isinstance(c, str) and c.strip():
        cid = c.strip()
        for d in DEFAULT_CATEGORIES:
            if d["id"] == cid:
                return dict(d)
        return {"id": cid, "label": cid.replace("-", " ").replace("_", " ").capitalize(), "sub": ""}
    return None


def rack_plate(name) -> str | None:
    """A plate filename the /plate/ route may serve, or None.

    The taxonomy is USER-EDITABLE JSON feeding a file read, so this applies
    the discipline sheet_path() already applies to /sheet/: a bare filename,
    .png or .jpg, no separators, no traversal, and it must actually resolve
    inside POSE_DIR. A rack the User renames badly loses its picture; it never
    reads a file out of the tree.
    """
    n = str(name or "").strip()
    if not n or n in (".", ".."):
        return None
    if "/" in n or "\\" in n or ":" in n:
        return None
    if not n.lower().endswith((".png", ".jpg")):
        return None
    try:
        got = (POSE_DIR / n).resolve()
        got.relative_to(POSE_DIR.resolve())
    except (OSError, ValueError):
        return None
    return n if got.is_file() else None


def kit_taxonomy() -> tuple[list[dict], list[dict]]:
    """(racks, categories) — the shape of the library, as data.

    A RACK is a named place to put a clip. Note the word: pose-freelance
    already spends `shelve` / `_shelved/` / POST /v1/shelve on the OPPOSITE
    idea (a clip taken out of the kit), so calling this a shelf would make
    "shelved to Stretches" read as "deleted".

    Two things the old hub-derived sections could not do, and this can:

      * An EMPTY rack exists. Sections used to be computed from the clips in
        them, so a rack with nothing on it could not be expressed -- and a
        rack with nothing on it is exactly a "please film this" placeholder,
        which is the whole point of having the Mint next to the library.
      * A rack cannot silently swallow a clip. Casual Up close is
        `down_close` (Casual Comms). Without that row every casual clip on
        the hub bucketed to `c:down_close`, matched no section and vanished.

    Falls back to the tuples, so a box with no `racks` block is unaffected. A
    malformed block falls back too rather than 500-ing the menu: the library
    going blank is worse than the library being old.
    """
    try:
        inv = inv_load(POSE_DIR / "inventory.json")
        racks = inv.get("racks")
        cats = inv.get("rack_categories")
    except Exception:
        racks, cats = None, None
    if isinstance(racks, list) and racks:
        out = []
        for r in racks:
            if not isinstance(r, dict) or not r.get("id"):
                continue
            out.append({
                "id": str(r["id"]),
                "label": str(r.get("label") or r["id"]),
                "note": str(r.get("note") or ""),
                "category": str(r.get("category") or "formal"),
                "hub": r.get("hub") or None,
                "plate": rack_plate(r.get("plate")),
                "want": int(r.get("want") or 0),
                "wants_bank": bool(r.get("wants_bank")),
            })
        if out:
            if not (isinstance(cats, list) and cats):
                cats = [dict(c) for c in DEFAULT_CATEGORIES]
            return out, [rack_category(c) for c in cats if rack_category(c)]
    # No declaration: rebuild today's sections in the same shape.
    out = []
    for sec, title, sub in KIT_SECTIONS:
        out.append({"id": str(sec), "label": title, "note": sub,
                    "category": "formal",
                    "hub": None if sec == "travel" else sec,
                    "plate": rack_plate(HUB_PLATE.get(sec)),
                    "want": 0, "wants_bank": False})
    for sec, title, sub in CASUAL_SECTIONS:
        out.append({"id": "c:" + str(sec), "label": title, "note": sub,
                    "category": "casual",
                    "hub": None if sec in ("travel", "takes") else sec,
                    "plate": rack_plate(HUB_PLATE.get(sec)),
                    "want": 0, "wants_bank": False})
    return out, [dict(c) for c in DEFAULT_CATEGORIES]


class Reel:
    """A clip held as ENCODED frames, decoded one at a time on access.

    the User, 2026-09-04: "can that cache grow for just one of every clip so they
    stay hot? ... Why would it be designed to cache everything EVER played?"

    It was not designed to. It was designed to avoid a re-decode stall when she
    switches clips — a real concern — and it was written when the roster was
    short. Nobody weighed the cost of ONE entry against how many there would
    eventually be. `self.bytes` was even added to report the total, so someone
    saw that it mattered; the report was just never wired to a limit.

    The size was the whole problem, and it is not necessary. Measured on this
    roster, 57 distinct clips, 9442 frames:

        every clip hot, raw 736x400x3 :  7.8 GB
        every clip hot, JPEG q92      :  0.36 GB      (20-21x smaller)
        cost to decode one frame      :  0.54-0.60 ms of a 41.7 ms budget

    So the answer to "can they all stay hot" is yes, in about a third of a
    gigabyte — and then "purge it when it finishes playing" stops being a
    question worth asking, because nothing needs purging. The conductor already
    JPEG-encodes every composited frame to serve /frame.jpg, so this codec is
    in the hot path either way.

    Consumers only ever do len(reel) and reel[i], which is all this provides.
    A decoded frame is a fresh array every time, which is also safer than
    handing out a slice of a shared cache that a painter could scribble on.
    """

    __slots__ = ("_enc", "nbytes")

    def __init__(self, frames: list) -> None:
        self._enc = []
        for fr in frames:
            ok, buf = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if ok:
                self._enc.append(buf)
        self.nbytes = int(sum(int(b.nbytes) for b in self._enc))

    def __len__(self) -> int:
        return len(self._enc)

    def __getitem__(self, i: int):
        return cv2.imdecode(self._enc[int(i)], cv2.IMREAD_COLOR)


class Library:
    """Every clip, decoded, in RAM. Lazily on first ask, then kept — with the
    scheduled pool warmed in the background so the first cut is instant too."""

    def __init__(self, clips: dict) -> None:
        self.rows = clips
        self._frames: dict[str, np.ndarray] = {}
        self._tracks: dict[str, list] = {}
        # ck -> the head.json mtime_ns when we last read it and got nothing.
        # An empty track is only trusted while the file has not moved
        # (2026-09-15; see head()).
        self._track_bad: dict[str, int] = {}
        # Mouth-lamp summaries only, keyed (sidecar name -> (mtime, dict)).
        # Separate from _tracks because what it holds is five integers,
        # not a parsed sidecar: see mouth_health().
        self._health: dict[str, tuple[float, dict]] = {}
        self._pl_tiles: dict[str, tuple] = {}
        self._lock = threading.Lock()
        self.bytes = 0
        # LAST TOUCH PER CLIP, and the clip that must never be evicted.
        self._used: dict[str, float] = {}
        self.hot: str | None = None
        self.evicted = 0
        # key -> (film path, mtime_ns) at decode. A film swapped on disk
        # (intake --install, 2026-09-16 14:13) re-decodes on the next read.
        self._film_mt: dict[str, tuple] = {}
        # playlist name -> mtime_ns of the parse in _tracks["pl:<name>"].
        # A RE-PICK MUST REACH A RUNNING CONDUCTOR (the User 2026-09-19: "all my
        # tiles have been re-selected but the mouth tiles seem too small
        # again"): playlist() cached by FILE NAME alone and nothing ever
        # dropped it, so the bench held the pre-pick index for the life of
        # the process. The install did reload :8100 and never :8110, so the
        # very conductor the Composer shows painted the old tiles for three
        # hours while the grid read installed. Keyed like mouth_health() now
        # — see playlist(). HOUSE_PL_MTIME=0 is the old behaviour.
        self._pl_mt: dict[str, int] = {}

    def _decode(self, key: str) -> "Reel | np.ndarray | None":
        row = self.rows.get(key) or {}
        path = film_path(row, key)
        if path is None:
            return None
        try:
            self._film_mt[key] = (str(path), path.stat().st_mtime_ns)
        except OSError:
            self._film_mt[key] = (str(path), 0)
        cap = cv2.VideoCapture(str(path))
        out = []
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            if fr.shape[1] != paint_wh()[0] or fr.shape[0] != paint_wh()[1]:
                fr = cv2.resize(fr, paint_wh(), interpolation=cv2.INTER_AREA)
            out.append(fr)
        cap.release()
        if not out:
            return None
        if os.environ.get("HOUSE_CONDUCTOR_CACHE_RAW") == "1":
            return np.stack(out)          # rollback: the old raw hold
        return Reel(out)

    # A CACHE WITH NO CEILING IS A LEAK WITH AN ACCOUNTANT. the User, 2026-09-04,
    # reading `ram 2042 MB` off the Composer header: "it is filling never
    # dumping." He was right. `self.bytes` counted every clip ever decoded and
    # nothing ever removed one. A clip is held as EVERY frame at 736x400x3 --
    # 241 frames is 203 MB, the inventory has 84 clips, so the ceiling was
    # 16.7 GB of a 92 GB box. The conductor was measured at 3.5 GB RSS after
    # 24 minutes with ten clips decoded.
    #
    # Least-recently-used, and the clip on air is never a candidate: evicting
    # what the playhead is reading costs a full re-decode mid-frame.
    #
    # This is now a BACKSTOP, not the fix. Reel holds clips encoded, so the
    # whole 57-clip roster is 0.36 GB and this sweep should never fire — it
    # stays because a cache that cannot be bounded is the bug that was here,
    # and HOUSE_CONDUCTOR_CACHE_RAW=1 rolls back to the raw hold that needs it.
    CACHE_MB = float(os.environ.get("HOUSE_CONDUCTOR_CACHE_MB", "3000"))

    def _evict_locked(self, keep: str) -> None:
        """Drop least-recently-used clips until under budget. Caller holds the lock."""
        cap = self.CACHE_MB * 1e6
        if cap <= 0 or self.bytes <= cap:
            return
        order = sorted(self._frames, key=lambda k: self._used.get(k, 0.0))
        for k in order:
            if self.bytes <= cap:
                break
            if k == keep or k == self.hot:
                continue
            arr = self._frames.pop(k, None)
            if arr is None:
                continue
            self.bytes -= int(arr.nbytes)
            self._used.pop(k, None)
            self.evicted += 1

    def _film_swapped(self, key: str) -> bool:
        """True when the film on disk is not the one decoded (mtime_ns).

        2026-09-16: the bench decoded the 09-08 FT loop at 13:57, intake
        replaced the mp4 at 14:13, and every Composer load after that read
        this cache — the OLD film under the NEW head track (nose 31 px
        lower), so the tiles landed on her chin while the strip below showed
        the new film. A stat per read is the price of a truthful cache."""
        rec = self._film_mt.get(key)
        if not rec:
            return False
        try:
            return os.stat(rec[0]).st_mtime_ns != rec[1]
        except OSError:
            return False

    def frames(self, key: str) -> "Reel | np.ndarray | None":
        with self._lock:
            got = self._frames.get(key)
            if got is not None:
                self._used[key] = time.time()
        if got is not None and self._film_swapped(key):
            row = self.rows.get(key) or {}
            stem = Path(str(row.get("file") or key)).stem
            with self._lock:
                arr = self._frames.pop(key, None)
                if arr is not None:
                    self.bytes -= int(arr.nbytes)
                self._used.pop(key, None)
                # the swapped film brings its own head track and sidecar
                self._tracks.pop("hf:" + stem, None)
                self._track_bad.pop("hf:" + stem, None)
            reset_mouth_caches()
            action("film", "%s changed on disk — re-decoded, head track dropped" % key, "cache")
            got = None
        if got is not None:
            return got
        arr = self._decode(key)
        if arr is None:
            return None
        with self._lock:
            if key not in self._frames:
                self._frames[key] = arr
                self.bytes += int(arr.nbytes)
            self._used[key] = time.time()
            self._evict_locked(key)
        gc_refreeze_soon("clip " + str(key))
        return arr

    def track(self, key: str) -> list:
        """Per-frame mouth boxes. A clip whose mouth travels ships one; a clip
        that holds still has only the row's static box."""
        row = self.rows.get(key) or {}
        path = sidecar_path(row, key)
        if path is None:
            return []
        name = path.name
        with self._lock:
            got = self._tracks.get(name)
        if got is not None:
            return got
        try:
            d = json.loads(path.read_text())
            frames = d.get("frames") or []
        except (OSError, ValueError):
            frames = []
        with self._lock:
            self._tracks[name] = frames
        return frames

    def secs(self, key: str) -> float | None:
        """How long this clip runs, WITHOUT decoding it.

        The kit is a menu: asking it for durations must not pull twenty clips
        into RAM to answer. Decoded length if it happens to be resident, else
        the sidecar's frame count, else None — and None is fine, the UI simply
        omits the time. A clip Grok drops in before its sidecar lands still
        appears on the menu; it just has no clock yet.
        """
        with self._lock:
            got = self._frames.get(key)
        n = len(got) if got is not None else len(self.track(key))
        if not n:
            return None
        return round(n / float(FPS), 1)

    def pins(self) -> dict:
        """<pose-dir>/mouth-pins.json, re-read when it moves (lib/mouth_pins)."""
        p = POSE_DIR / MP.PINS_NAME
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            mt = None
        got = getattr(self, "_pins_cache", None)
        if got is None or got[0] != mt:
            got = (mt, MP.load(POSE_DIR) if mt is not None else {"cells": {}})
            self._pins_cache = got
        return got[1]

    def lipmarks(self, stem: str) -> dict | None:
        """<stem>.lipmarks.json from the stem's bank (scripts/pose-lip-marks.py), mtime-cached."""
        stem = Path(str(stem)).stem
        p = bank_dir(stem.removeprefix("clip-")) / (stem + ".lipmarks.json")
        if not p.is_file():
            p = POSE_DIR / (stem + ".lipmarks.json")
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            return None
        cache = self.__dict__.setdefault("_lipmarks", {})
        got = cache.get(stem)
        if got is None or got[0] != mt:
            try:
                got = (mt, json.loads(p.read_text()))
            except (OSError, ValueError):
                got = (mt, None)
            if LIP_MARKS_HONOUR_FLAG and isinstance(got[1], dict) and isinstance(got[1].get("check"), dict) \
                    and got[1]["check"].get("ok") is False:
                got = (mt, None)                      # flagged marks are no marks (LIP_MARKS_HONOUR_FLAG)
            cache[stem] = got
        return got[1]

    def tile_lips(self, rel: str) -> "np.ndarray | None":
        """The tile's lip points (corner, corner, lip-line centre) in TILE pixels: its source frame's
        marks minus crop_xy; a mirrored tile flips x about the tile width and swaps the corners."""
        base_rel, mirrored = _unmirror(rel)
        rec = self.tile_head(base_rel) or {}
        try:
            name = Path(base_rel).name
            pre, rest = name.split("_f", 1)
            n = int(rest.split("_", 1)[0])
        except (ValueError, IndexError):
            return None
        lm = self.lipmarks("clip-" + pre)
        cxy = rec.get("crop_xy")
        if not lm or cxy is None or n >= len(lm.get("frames") or []):
            return None
        P = MP.lip_points(lm["frames"][n], origin=cxy)
        if P is None:
            return None
        if mirrored:
            W = float((rec.get("src_wh") or [0])[0])
            P = P.copy()
            P[:, 0] = W - P[:, 0]
            P[[0, 1]] = P[[1, 0]]
        return P

    def tile_anchors(self, rel: str) -> "np.ndarray | None":
        """The tile's five anchors (lips, nose tip, chin) in TILE pixels, mirrored like tile_lips."""
        base_rel, mirrored = _unmirror(rel)
        rec = self.tile_head(base_rel) or {}
        try:
            pre, rest = Path(base_rel).name.split("_f", 1)
            n = int(rest.split("_", 1)[0])
        except (ValueError, IndexError):
            return None
        lm = self.lipmarks("clip-" + pre)
        cxy = rec.get("crop_xy")
        if not lm or cxy is None or n >= len(lm.get("frames") or []):
            return None
        P = MP.anchor_points(lm["frames"][n], origin=cxy)
        if P is not None and mirrored:
            P = P.copy()
            P[:, 0] = float((rec.get("src_wh") or [0])[0]) - P[:, 0]
            P[[0, 1]] = P[[1, 0]]
        return P

    def tile_mark(self, rel: str, name: str) -> "np.ndarray | None":
        """One named lip-mark point of the tile ("top" / "bot": the outer lip's upper and lower mid points) in TILE
        pixels, mirrored like tile_lips."""
        base_rel, mirrored = _unmirror(rel)
        rec = self.tile_head(base_rel) or {}
        try:
            pre, rest = Path(base_rel).name.split("_f", 1)
            n = int(rest.split("_", 1)[0])
        except (ValueError, IndexError):
            return None
        lm = self.lipmarks("clip-" + pre)
        cxy = rec.get("crop_xy")
        if not lm or cxy is None or n >= len(lm.get("frames") or []) or not lm["frames"][n]:
            return None
        try:
            p = np.asarray(lm["frames"][n][name], dtype=np.float64).ravel()[:2] - np.asarray(cxy, dtype=np.float64)
        except (KeyError, TypeError, ValueError):
            return None
        if mirrored:
            p[0] = float((rec.get("src_wh") or [0])[0]) - p[0]
        return p

    def lips_own(self, key: str, i: int) -> bool:
        """This frame is landed by the lips whatever the angle (HOUSE_MOUTH_LIPS_ALL + lip marks on it)."""
        if not (MOUTH_LIPS_ALL and MOUTH_TURN_PLACE == "lips"):
            return False
        row = self.rows.get(key) or {}
        lm = self.lipmarks(str(row.get("file") or key)) or {}
        if not lm.get("land"):
            return False          # a tile source's marks place its tiles elsewhere; they do not land the take itself
        fr = lm.get("frames") or []
        return 0 <= int(i) < len(fr) and bool(fr[int(i)])

    def tile_take_scale(self, rel: str) -> "float | None":
        """The tile's head scale in TILE pixels as the dest's own engine measures it: the take's head track s
        at the tile's source frame (painter px) x film width / painter width. None when that frame has no head."""
        base_rel, _ = _unmirror(rel)
        try:
            pre, rest = Path(base_rel).name.split("_f", 1)
            n = int(rest.split("_", 1)[0])
        except (ValueError, IndexError):
            return None
        rec = self.head(pre, n) or {}
        lm = self.lipmarks("clip-" + pre) or {}
        try:
            return float(rec["s"]) * float((lm.get("wh") or [0])[0]) / paint_wh()[0]
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None

    def tile_take_scale_any(self, rel: str) -> "float | None":
        """tile_take_scale, and where the tile's own source frame has no head (the 45 take's AH frame), the MEDIAN
        head scale of the take it was cut from - one camera, one seat, so the take's own size stands in."""
        got = self.tile_take_scale(rel)
        if got:
            return got
        base_rel, _ = _unmirror(rel)
        try:
            pre = Path(base_rel).name.split("_f", 1)[0]
        except (ValueError, IndexError):
            return None
        cache = self.__dict__.setdefault("_take_scale_med", {})
        if pre not in cache:
            lm = self.lipmarks("clip-" + pre) or {}
            w = float((lm.get("wh") or [0])[0])
            vals = []
            for n in range(len(lm.get("frames") or [])):
                rec = self.head(pre, n) or {}
                if rec.get("s"):
                    vals.append(float(rec["s"]))
            cache[pre] = (float(np.median(vals)) * w / paint_wh()[0]) if (vals and w) else None
        return cache[pre]

    def tile_hull(self, rel: str) -> "list | None":
        """The tile's face outline in TILE pixels (its source frame's lipmarks/2 hull minus crop_xy;
        mirrored like tile_lips)."""
        base_rel, mirrored = _unmirror(rel)
        rec = self.tile_head(base_rel) or {}
        try:
            pre, rest = Path(base_rel).name.split("_f", 1)
            n = int(rest.split("_", 1)[0])
        except (ValueError, IndexError):
            return None
        lm = self.lipmarks("clip-" + pre)
        cxy = rec.get("crop_xy")
        if not lm or cxy is None or n >= len(lm.get("frames") or []):
            return None
        polys = MP.face_polys(lm["frames"][n], origin=cxy)
        if polys and mirrored:
            W = float((rec.get("src_wh") or [0])[0])
            polys = [np.column_stack([W - q[:, 0], q[:, 1]]) for q in polys]
        return polys or None

    def turn_source(self, band: str | None, vis, face_key: str | None = None) -> tuple:
        """(tile hit, library name, mirrored) for a turn band. The other side ("45m") takes its own take's
        library first (TURN_LIBRARIES_OTHER) when it holds this class, else the mirror of this side's."""
        b = str(band or "")
        if b.endswith(MIRROR_SUFFIX):
            base = b.removesuffix(MIRROR_SUFFIX)
            other = TURN_LIBRARIES_OTHER.get(base)
            if other:
                k = self._turn_row(other, face_key)
                if k:
                    hit = turn_lookup(self.playlist(k), vis)
                    if hit:
                        return hit, other, False
            hit = turn_lookup(self.turn_playlist(base, face_key), vis)
            if hit:
                t = [x + MIRROR_TAG for x in as_tiles(hit)]
                hit = t if len(t) > 1 else t[0]
            return hit, TURN_LIBRARIES.get(base, base), True
        return turn_lookup(self.turn_playlist(b, face_key), vis), TURN_LIBRARIES.get(b, b), False

    def turn_playlist(self, band: str | None, face_key: str | None = None) -> dict | None:
        """The playlist of the take whose `library` is this turn band's (TURN_LIBRARIES), on the SAME face as
        `face_key` (the wall plays both faces from one conductor: a turn tile never crosses faces)."""
        lib = TURN_LIBRARIES.get(str(band or "").removesuffix(MIRROR_SUFFIX))
        if not lib:
            return None
        k = self._turn_row(lib, face_key)
        return self.playlist(k) if k else None

    def pitch_fold_source(self, face_key: str | None, station: str = PITCH_FOLD_TO) -> "str | None":
        """The loop whose desk tiles a forward frame tilted past the fold takes (mouth_playlist PITCH_FOLD): on the
        SAME face, a loop resting on a hub of the desk station whose playlist holds a desk bin. `idle` wins, else
        the first by key (Crissa: work, Lila: lila-work). HOUSE_PITCH_FOLD_LOOP names one by hand."""
        cache = self.__dict__.setdefault("_pitch_fold_src", {})
        want = face_of(face_key) if face_key else None
        ck = (want, station)
        if ck in cache:
            return cache[ck]
        named = os.environ.get("HOUSE_PITCH_FOLD_LOOP", "").strip()
        found = None
        cands = []
        for k, r in sorted(self.rows.items()):
            if not isinstance(r, dict) or face_of(k) != want:
                continue
            if named:
                if k == named:
                    cands = [(0, k)]
                    break
                continue
            if str(r.get("role") or "") != "loop" or (station == PITCH_FOLD_TO and HUB_STATION.get(str(r.get("start") or "")) != PITCH_FOLD_TO):
                continue
            cands.append((0 if r.get("idle") else 1, k))
        for _, k in sorted(cands):
            pl = self.playlist(k) or {}
            if any(str(x).startswith(station + "/") for x in (pl.get("index") or {})):
                found = k
                break
        cache[ck] = found
        print("[conductor] pitch fold source: face=%s station=%s -> %s" % (want, station, found), flush=True)
        return found

    def _turn_row(self, lib: str, face_key: str | None):
        want = face_of(face_key) if face_key else None
        for k, r in self.rows.items():
            if isinstance(r, dict) and str(r.get("library") or "") == lib:
                if face_key is not None and face_of(k) != want:
                    continue
                return k
        return None

    def playlist(self, key: str) -> dict | None:
        """Per-loop phoneme playlist. None if this clip has not been mapped."""
        row = self.rows.get(key) or {}
        named = row.get("playlist")
        path = (POSE_DIR / str(named)) if named else playlist_path_for(
            str(row.get("file") or ""), POSE_DIR)
        face = face_of(key) or (str(row.get("rack") or "")[5:] if str(row.get("rack") or "").startswith("host-") else None)
        self._pl_path_dbg = str(path)
        # a pack's lips-only bank (round 18) lends her LOOPS a loop's own playlist (no `paint`): a film of hers with a
        # painting playlist would wear a rest tile over its own talking mouth
        lips_only = face in LIPS_ONLY_BANKS
        if not path.is_file() and face in FACE_BANKS and (not lips_only or str(row.get("role") or "") == "loop"
                                                          or key in LIPS_BORROW_FILMS):
            # her bank's playlist for the same hub side (down → desk, else forward)
            want = "down" if str(row.get("start") or "").startswith("down") else "back"
            for cand in sorted(FACE_BANKS[face].glob("*.playlist.json")):
                try:
                    d0 = json.loads(cand.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if lips_only and d0.get("paint"):
                    continue
                if d0.get("index") and str(d0.get("pose") or "") == want and not str(d0.get("clip") or "").endswith(("talk-comms", "talk-comms-b", "talk-down")):
                    path = cand
                    break
        if not path.is_file():
            with self._lock:
                self._tracks["pl:" + (path.name or key)] = {}
                self._pl_mt.pop(path.name or key, None)
            return None
        # THE PARSE IS ONLY GOOD WHILE THE FILE HAS NOT MOVED (2026-09-19).
        # An install rewrites the sidecar in place; without this the cached
        # index outlives it until someone calls /v1/reload or restarts, and
        # the shell only ever reloaded :8100. One stat per call is the same
        # price mouth_health() already pays (90 stats = 0.083 ms).
        ck = "pl:" + path.name
        try:
            mt = path.stat().st_mtime_ns
        except OSError:
            mt = 0
        with self._lock:
            got = self._tracks.get(ck)
            fresh = (not PL_MTIME) or self._pl_mt.get(path.name) == mt
        if got is not None and fresh:
            return got or None
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(d, dict) or not d.get("index"):
                d = {}
        except (OSError, ValueError):
            d = {}
        with self._lock:
            self._tracks[ck] = d
            self._pl_mt[path.name] = mt
        if got is not None and d:
            # say it: a swap under a running playhead is worth a line
            action("playlist", "%s re-read (installed under the playhead)" % path.name, "mtime")
        return d or None

    def mouth_health(self, key: str) -> dict:
        """The menu's mouth lamp: five integers, never the sidecar itself.

        Holding the playlist for every clip to answer this would drag their
        `frames` arrays into RAM for films nobody is playing -- 662 KB across
        18 sidecars to report five numbers. So this keeps its OWN cache and
        keeps only the summary; `playlist()` stays lazy on the clip that is
        actually rolling.

        Keyed on (sidecar name, mtime), so a re-harvest or a merge_playlist
        run shows up on the next kit pull with no restart and a stale entry is
        impossible. Cheap by construction, measured 2026-09-05: 90 stat()
        calls is 0.083 ms, a cold parse of all 18 sidecars is 1.9 ms, and
        /v1/kit is pulled when the hub or the tail moves -- not on the 250 ms
        tick.

        Parsed with load_playlist(), not json.loads: the mouth_playlist/v1
        schema check applies, so a stale-schema sidecar reads `none` rather
        than a confidently wrong number.

        The join is the MP4 STEM, never the sidecar's own `clip` field.
        clip-listen-back.playlist.json says clip:"listen" and legitimately
        serves listen, listen-guest, listen-user, speak and speak-live;
        clip-talk-loop says "talking-head" while clip-talking-head says
        "talking-head-close". Trusting that field mislabels five clips.
        """
        row = self.rows.get(key) or {}
        named = row.get("playlist")
        path = (POSE_DIR / str(named)) if named else playlist_path_for(
            str(row.get("file") or ""), POSE_DIR)
        role = str(row.get("role") or "")
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return mouth_bank_health(None, role=role)
        ck = "mh:" + path.name
        with self._lock:
            got = self._health.get(ck)
        if got is not None and got[0] == stamp:
            return got[1]
        out = mouth_bank_health(load_playlist(path), role=role)
        with self._lock:
            self._health[ck] = (stamp, out)
        return out

    def tilt(self, key: str) -> list:
        """The clip's raw per-frame pitch (<stem>.tilt.json, never smoothed; round 29), [] when it has none. Re-read when
        the file's mtime changes, like the playlists."""
        row = self.rows.get(key) or {}
        stem = Path(str(row.get("file") or key)).stem
        path = POSE_DIR / (stem + ".tilt.json")
        try:
            mt = path.stat().st_mtime_ns
        except OSError:
            return []
        ck = "tilt:" + stem
        with self._lock:
            got = self._tracks.get(ck)
        if got is not None and got[0] == mt:
            return got[1]
        try:
            d = json.loads(path.read_text())
            vals = d.get("pitch") if d.get("schema") == "tilt/1" else None
            vals = list(vals) if isinstance(vals, list) else []
        except (OSError, ValueError, AttributeError):
            vals = []
        with self._lock:
            self._tracks[ck] = (mt, vals)
        return vals

    def mp_pitch(self, key: str) -> list:
        """The clip's head pitch per frame from MediaPipe (<stem>.pitch.json, round 29 .101), [] when it has none.
        Re-read on mtime, like tilt."""
        row = self.rows.get(key) or {}
        stem = Path(str(row.get("file") or key)).stem
        path = POSE_DIR / (stem + ".pitch.json")
        try:
            mt = path.stat().st_mtime_ns
        except OSError:
            return []
        ck = "mp_pitch:" + stem
        with self._lock:
            got = self._tracks.get(ck)
        if got is not None and got[0] == mt:
            return got[1]
        try:
            d = json.loads(path.read_text())
            vals = d.get("pitch") if d.get("schema") == "pitch/1" else None
            vals = list(vals) if isinstance(vals, list) else []
        except (OSError, ValueError, AttributeError):
            vals = []
        with self._lock:
            self._tracks[ck] = (mt, vals)
        return vals

    def bow_map(self, key: str) -> list:
        """The clip's per-frame bow answer (<stem>.bow.json: null | a bow band's key | "filmed"), [] when it has none.
        Re-read on mtime, like tilt."""
        row = self.rows.get(key) or {}
        stem = Path(str(row.get("file") or key)).stem
        path = POSE_DIR / (stem + ".bow.json")
        try:
            mt = path.stat().st_mtime_ns
        except OSError:
            return []
        ck = "bow:" + stem
        with self._lock:
            got = self._tracks.get(ck)
        if got is not None and got[0] == mt:
            return got[1]
        try:
            d = json.loads(path.read_text())
            vals = d.get("frames") if d.get("schema") == "bow-map/1" else None
            vals = list(vals) if isinstance(vals, list) else []
        except (OSError, ValueError, AttributeError):
            vals = []
        with self._lock:
            self._tracks[ck] = (mt, vals)
        return vals

    def head(self, key: str, i: int) -> dict | None:
        """Per-frame rigid frame in the painter space (paint_wh), or None. Null is an answer."""
        row = self.rows.get(key) or {}
        stem = Path(str(row.get("file") or key)).stem
        ck = "hf:" + stem
        with self._lock:
            got = self._tracks.get(ck)
        if got is None:
            path = POSE_DIR / (stem + ".head.json")
            # A BAD READ IS CACHED AGAINST THE FILE'S mtime (2026-09-15), not
            # for the life of the process. Before this, ONE unlucky read —
            # a repin's truncate window, a half-copied file — cached [] and
            # `if got is None` never looked again: the clip painted by the
            # Haar box until a restart. Caching nothing at all is not the
            # answer either: head() runs several times per painted frame and
            # the biggest track is 446 KB, so re-parsing a corrupt file would
            # stall the wall. So: remember the failure WITH the mtime, and
            # re-read the moment the file changes (a repair, or a repin
            # landing).
            frames: list = []
            try:
                mt = path.stat().st_mtime_ns
            except OSError:
                mt = 0
            try:
                d = json.loads(path.read_text())
                frames = d.get("frames") or []
            except (OSError, ValueError):
                frames = []
            with self._lock:
                self._tracks[ck] = frames
                if frames:
                    self._track_bad.pop(ck, None)
                else:
                    self._track_bad[ck] = mt
            got = frames
        elif not got:
            # an empty track is a REMEMBERED failure: look again only if the
            # file has moved since we looked
            path = POSE_DIR / (stem + ".head.json")
            try:
                mt = path.stat().st_mtime_ns
            except OSError:
                mt = 0
            with self._lock:
                stale = self._track_bad.get(ck) != mt
                if stale:
                    self._tracks.pop(ck, None)
                    self._track_bad.pop(ck, None)
            if stale:
                return self.head(key, i)
        if not got or i < 0 or i >= len(got):
            return None
        rec = got[i]
        return rec if isinstance(rec, dict) else None

    def head_track_len(self, key: str) -> int:
        """Rows in the loop's head track (0 when it has none); head() loads it."""
        self.head(key, 0)
        stem = Path(str((self.rows.get(key) or {}).get("file") or key)).stem
        with self._lock:
            return len(self._tracks.get("hf:" + stem) or ())

    def tile_head(self, rel: str) -> dict | None:
        """Harvest-time frame in TILE pixels, from map.json `head` block."""
        base_rel, mirrored = _unmirror(rel)
        if mirrored:
            return mirror_head(self.tile_head(base_rel))
        bank = bank_dir(rel)
        ck = "hf:map:" + str(bank)
        with self._lock:
            idx = self._tracks.get(ck)
        if idx is None:
            idx = {}
            mp = bank / "visemes" / "phoneme-map" / "map.json"
            try:
                for e in (json.loads(mp.read_text()).get("entries") or []):
                    t = str(e.get("tile") or "")
                    h = e.get("head")
                    if t and h:
                        idx[Path(t).name] = h
                        idx[t] = h
            except (OSError, ValueError):
                idx = {}
            with self._lock:
                self._tracks[ck] = idx
        return idx.get(Path(str(rel)).name) or idx.get(str(rel))

    def playlist_tile(self, rel: str) -> tuple | None:
        """Load a harvested mouth crop once. (bgr, lip-alpha) or None.

        `rel` must be a path string. A series list is unhashable — the
        painter must pick_series_tile first. Refuse a list rather than
        TypeError the composite.
        """
        if not isinstance(rel, str) or not rel:
            return None
        # THE CACHE IS KEYED ON THE FILE, NOT THE NAME (the User 2026-09-24: "we don't
        # manage our caches very well" / "purge caches on hub swap"): this store was keyed
        # by path alone, never checked the mtime and was not dropped by /v1/reload, so a
        # tile re-cut under the SAME filename (pose-face-recut, a hand re-crop) painted the
        # old picture until a restart, and a tile that arrived after the first ask stayed
        # "missing" for good. A stat per ask is microseconds; reload_now drops it whole.
        base_rel, mirrored = _unmirror(rel)
        path = bank_dir(base_rel) / base_rel
        try:
            mt = path.stat().st_mtime_ns
        except OSError:
            mt = None
        with self._lock:
            got = self._pl_tiles.get(rel)
        if got is not None and got[0] == mt:
            return got[1] or None
        img = cv2.imread(str(path))
        if img is not None and mirrored:
            img = np.ascontiguousarray(cv2.flip(img, 1))
        if img is not None:
            # FLOOR THE DARKS (the User 2026-09-12: "looks like chewing gum, her
            # right corner"). square_alpha keys "no tile" on R+G+B <= 12 —
            # the zeros the warp leaves outside the footprint. A Grok take
            # goes truly black inside an open mouth where a filmed take
            # never did, so cavity pixels read as "outside" and the skin
            # inpaint showed through them. Lift them just over the key.
            dark = img.astype(np.int32).sum(axis=2) <= 12
            if dark.any():
                img = img.copy()
                img[dark] = (6, 6, 6)
        pair = None
        if img is not None:
            rec = self.tile_head(rel)
            rect = None
            nose_y = None
            if rec:
                mit = rec.get("mouth_in_tile")
                if mit and len(mit) == 4:
                    rect = tuple(float(x) for x in mit)
                nose = rec.get("nose")
                if nose and len(nose) >= 2:
                    nose_y = float(nose[1])
            pair = (img, strip_lip_alpha(img, rect=rect, nose_y=nose_y))
        with self._lock:
            self._pl_tiles[rel] = (mt, pair or ())
        return pair

    def box(self, key: str, i: int):
        """Where her mouth is on this clip at this frame, or None if she has no
        mouth in shot — and she talks anyway.

        A NULL SIDECAR FRAME IS AN ANSWER, NOT A GAP. The harvest writing null
        is it saying "she is turned away on this frame"; falling through to the
        row's static box overwrites that with a stale rectangle and `face` can
        then never go false. Measured: clip-flourish-glance is 75 of 145 frames
        null — she spends 52% of that clip looking at the door — and every one
        of them reported a mouth. Grok removed the same fallback from the glass
        earlier today; this is the service's copy of it.

        The row box is the floor only when there is NO sidecar at all, which is
        a genuine absence of measurement rather than a measured absence."""
        tr = self.track(key)
        if tr:
            j = min(max(i, 0), len(tr) - 1)
            fr = tr[j]
            return tuple(fr) if (fr and len(fr) == 4) else None
        m = (self.rows.get(key) or {}).get("mouth")
        if m and len(m) == 4:
            return tuple(m)
        # No sidecar and no row box: the head track's own mouth rectangle
        # (Law 3 — a mouth on every clip that has a face). A source film has
        # a head track and nothing else; the tiles drill needs its box
        # (2026-09-13: pl_box False on lila-talk-down, no paint).
        try:
            fr = unpack_head(self.head(key, i))
            mm = fr.get("mouth") if fr else None
            if mm is not None:
                x, y, w, h = [float(v) for v in list(mm)[:4]]
                if w > 2 and h > 2:
                    return (int(round(x)), int(round(y)), int(round(w)), int(round(h)))
        except Exception:  # noqa: BLE001
            pass
        return None

    def face_pct(self, key: str) -> float:
        tr = self.track(key)
        if not tr:
            return 1.0
        return sum(1 for f in tr if f) / len(tr)

    def warm(self, keys: list[str]) -> None:
        def run():
            for k in keys:
                self.frames(k)
        threading.Thread(target=run, daemon=True).start()


def survey(clips: dict, lib) -> tuple[dict, dict]:
    """Movement envelope and face presence, measured off the per-frame mouth
    tracks. Registers are derived from this rather than hand-tagged, so a clip
    Grok drops in files itself the moment its sidecar lands."""
    motion, face = {}, {}
    for key, row in clips.items():
        tr = lib.track(key)
        if not tr:
            continue
        seen = [f for f in tr if f]
        face[key] = len(seen) / len(tr)
        vel = []
        for a, b in zip(tr, tr[1:]):
            if not a or not b:
                continue
            d = ((b[0] + b[2] / 2 - a[0] - a[2] / 2) ** 2 +
                 (b[1] + b[3] / 2 - a[1] - a[3] / 2) ** 2) ** 0.5
            vel.append((d / max(1.0, a[2])) * FPS)
        if vel:
            vel.sort()
            motion[key] = vel[int(len(vel) * 0.9)]
    return motion, face


class Strip:
    """Filmed mouths, one ladder per hub, indexed by viseme *class*.

    Voice text exists before the first PCM byte. house-speak G2Ps it into the
    13-class sequence and stamps each 20 ms hop. This picker takes that class
    and a labelled tile at this hub; loudness only ranks tiles *inside* the
    class, and is the whole index when the hub has no labels.json.

    Face-on (`mouth-strip.jpg`, pose back) does not fit a heads-down jaw —
    the User, 2026-08-30, "so now we are using a face on image for a face down
    jaw." The hair-tuck clip is the down talking take: she says "Okay" while
    looking at the laptop, and that jaw is flatter. Each spec names its pose;
    paint only the bank cut from the hub she is on. A hub we have not cut
    keeps the clip mouth.
    """

    def __init__(self) -> None:
        self.by_pose: dict[str, dict] = {}
        self.last_idx: int | None = None
        self.last_class: str | None = None
        vis = POSE_DIR / "visemes"
        for spec_path in sorted(vis.glob("mouth-strip*.json")):
            self._load(spec_path)
        self.ok = bool(self.by_pose)

    def _load(self, spec_path: Path) -> None:
        # glob("mouth-strip*.json") also hits labels/frames/marks/phonemes
        # sidecars (lists). Those are not strip specs — skip before .get.
        if spec_path.name.endswith((
            ".labels.json", ".frames.json", ".marks.json", ".phonemes.json",
        )):
            return
        try:
            spec = json.loads(spec_path.read_text())
            if not isinstance(spec, dict):
                return
            pose = spec.get("pose")
            if not pose:
                return
            sheet = cv2.imread(str(spec_path.with_suffix(".jpg")))
            if sheet is None:
                return
            n = int(spec["frames"])
            cols = int(spec["cols"])
            tw, th = spec["tile"]
            tiles = [
                sheet[(i // cols) * th:(i // cols + 1) * th,
                      (i % cols) * tw:(i % cols + 1) * tw]
                for i in range(n)
            ]
            opens = spec.get("open") or []
            try:
                opens = [float(x) for x in opens]
            except (TypeError, ValueError):
                opens = []
            labels = spec_path.with_name(spec_path.stem + ".labels.json")
            self.by_pose[str(pose)] = {
                "pose": str(pose),
                "n": n,
                "cols": cols,
                "src_w": float(spec["src_w_ruler"]),
                "src_h": float(spec["src_h_ruler"]),
                "mt": float(spec["margins"]["mt"]),
                "tiles": tiles,
                "alphas": [strip_lip_alpha(t) for t in tiles],
                "opens": opens,
                "by_class": load_class_index(labels),
            }
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return

    @property
    def poses(self) -> list[str]:
        return sorted(self.by_pose)

    def bank(self, pose: str | None) -> dict | None:
        if not pose:
            return None
        return self.by_pose.get(pose)

    def tile_for(
        self,
        pose: str | None,
        level: float,
        viseme: str | int | None = None,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        b = self.bank(pose)
        if not b:
            self.last_idx = None
            self.last_class = None
            return None
        i = pick_tile(
            b["n"], level, viseme,
            b.get("by_class") or None,
            b.get("opens") or None,
        )
        self.last_idx = i
        self.last_class = viseme_of(viseme) if viseme is not None else None
        return b["tiles"][i], b["alphas"][i]


# tiles follow the speaker on the sheet (2026-09-18); 0 = anyone on the sink paints, as before
MOUTH_SPEAKER_GATE = os.environ.get("HOUSE_MOUTH_SPEAKER_GATE", "1").strip().lower() not in ("0", "false", "off", "no")
MOUTH_HUSH = None         # a pack's player: crissa_init.mouth_hushed (Setup's timing lines, round 28); None = never
LILA_PAINTS = None        # a pack's player: crissa_init.lila_lips_now (Lila on her plate in Setup); None = the house's rule

# THE SEMIFRAME COUNTER (the User 2026-10-11, "yes on counter"). Her mouth sheet is one row per semiframe (two a
# film frame). The paint loop runs on its own clock (RENDER_FPS 60 asked, ~48/s measured), not locked to that grid,
# so a pass can paint one row twice and skip the next. While she speaks, every pass records which row it painted:
# per line, rows painted, rows SKIPPED (never painted) and rows doubled. /v1/state "semis"; one log line a reply.
# Measure only. Kill HOUSE_SEMI_COUNT=0.
SEMI_COUNT_ON = os.environ.get("HOUSE_SEMI_COUNT", "1").strip().lower() not in ("0", "false", "no", "off")


def hop_pts_at(frames: list, now: float, stale_s: float = 0.5) -> float | None:
    """The pts of the /v1/mouth row painted at `now` (viseme_at's row), or None before the first / when stale."""
    if not frames:
        return None
    try:
        if float(frames[-1][0]) < now - stale_s:
            return None
        for r in reversed(frames):
            if float(r[0]) <= now:
                return float(r[0])
    except (TypeError, ValueError, IndexError):
        return None
    return None


# THE PAINT ON THE SEMIFRAME GRID (the User 2026-10-12, "go ahead with 2 and 3"). While she speaks the paint loop ran
# free at RENDER_FPS: on her close-up ~40 % of passes repainted a mouth row already painted (the counter's "doubled")
# and that time pushed the next row out of its slot ("skipped"). On: a speaking pass starts at once when the row on the
# clock is still unpainted, else it sleeps to the next row's start; nothing else changes (the same composite, the same
# rows, the film on its own clock). HOUSE_MOUTH_SEMI_GRID=1; off by default (house and pack) until a reading says so.
# How often the painter fetches the sheet (2026-10-12): rows that reach it after their own time are painted as the last
# row it has (a repeat) and then jumped (a skip). 0.1 s = as before; HOUSE_MOUTH_POLL_S tightens it.
MOUTH_POLL_S = max(0.01, min(0.5, float(os.environ.get("HOUSE_MOUTH_POLL_S", "0.1") or 0.1)))
SPEECH_TAIL_HOLD_S = max(0.0, min(10.0, float(os.environ.get("HOUSE_SPEECH_TAIL_HOLD_S", "0") or 0)))
# The stall watch's per-pass log (2026-10-12): while she speaks, a gap of > 45 ms between two pass starts prints what
# the pass before it did (loop, composite, encode, steps, the grid's wait). On with HOUSE_CONDUCTOR_STALL_WATCH=1.
STALL_PASS_LOG = os.environ.get("HOUSE_CONDUCTOR_STALL_WATCH", "0").strip().lower() in ("1", "true", "yes", "on")
STALL_PASS = [None]          # perf_counter at the start of the paint pass in progress (stall watch)
SEMI_GRID = os.environ.get("HOUSE_MOUTH_SEMI_GRID", "0").strip().lower() in ("1", "true", "yes", "on")


def semi_grid_row(frames: list, now: float) -> float | None:
    """The row (pts) on the clock at `now`, on semi_grid_wait's grid: the sheet's row, or past the last fetched row the
    same hop on."""
    try:
        cur = hop_pts_at(frames, now)
        if cur is None:
            return None
        last = float(frames[-1][0])
        hop = (last - float(frames[-2][0])) if len(frames) >= 2 else 1.0 / 48.0
        if not (0.005 <= hop <= 0.1):
            hop = 1.0 / 48.0
        if now >= last + hop:
            cur = last + hop * int((now - last) / hop)
        return cur
    except (TypeError, ValueError, IndexError):
        return None


def semi_grid_wait(frames: list, now: float, painted: float | None, ahead: float = 0.0015,
                   stale_s: float = 0.5) -> float | None:
    """Seconds to sleep before the next speaking pass: 0.0 when the row on the clock at `now` is not the one this loop
    last painted (`painted`, a row pts from hop_pts_at), else until the next row starts (+`ahead`, so the pass lands
    inside it). The rows are the sheet's own (pts0 + n x hop); past the last row fetched so far the grid goes on at
    the same hop, since the next batch lands on it. None when there is no grid to follow (no rows, or the last row is
    older than `stale_s`): pace as before."""
    if not frames:
        return None
    try:
        last = float(frames[-1][0])
        if last < now - stale_s:
            return None
        if len(frames) >= 2:
            hop = last - float(frames[-2][0])
            if not (0.005 <= hop <= 0.1):
                hop = 1.0 / 48.0
        else:
            hop = 1.0 / 48.0
        cur = hop_pts_at(frames, now)
        if cur is None:
            return None
        if now >= last + hop:                                   # past the fetched rows: the grid goes on
            cur = last + hop * int((now - last) / hop)
        if painted is None or cur > painted + 1e-6:
            return 0.0
        nxt_row = cur + hop
        lo, hi = 0, len(frames)
        while lo < hi:
            mid = (lo + hi) // 2
            if float(frames[mid][0]) <= now:
                lo = mid + 1
            else:
                hi = mid
        if lo < len(frames):
            nxt_row = float(frames[lo][0])
        return max(0.0, nxt_row + ahead - now)
    except (TypeError, ValueError, IndexError):
        return None


def lip_clock_line(c: dict) -> str:
    """The line's lip clock as its speaker posted it (house-speak's last post, a pack's own mailbox; round 11, .101:
    "please log the clock per line (aligned/letter, offset ms)"), with the painter's share of its rows. '' when the
    sheet carried none (the house's speaker never sends one, and the wall's mailbox is :8099's)."""
    try:
        if MAILBOX is None:
            return ""
        snap = MAILBOX.snapshot()
        k = snap.get("clock") or {}
        if not k or snap.get("air_at") != c.get("air_at"):
            return ""
        parts = [str(k.get("how") or "letter")]
        if k.get("first_s") is not None:
            parts.append("first stamp %ss into the line" % k["first_s"])
        if k.get("after_s") is not None:
            parts.append("last stamp %ss" % k["after_s"])
        if k.get("lead_ms") is not None:
            # where the lead comes from (round 12, .101: "sheet lead 271 ms against the dials' 21-42"): the library's
            # dial + its calibrated zero + this machine's lip sync (the Crissa menu), as this player has them now
            try:
                ls = 0.0 if IS_BENCH else float(crissa_lipsync.offset_ms() or 0.0)
                parts.append("sheet lead %s ms (dial %.1f + zero %.1f + lip sync %.1f, now)"
                             % (k["lead_ms"], float(MOUTH_SHEET_LEAD_MS), float(SYNC_ZERO_MS), ls))
            except Exception:  # noqa: BLE001
                parts.append("sheet lead %s ms" % k["lead_ms"])
        parts.append("voice %s%s" % (k.get("engine") or "?", " (piece marks)" if k.get("marks") else ""))
        if k.get("pieces"):
            parts.append("%s of %s pieces stamped before they played%s" % (
                k.get("early_pieces"), k["pieces"],
                ", the latest spoke %ss unstamped (piece %s)" % (k["late_s"], k.get("late_piece"))
                if k.get("late_s") is not None else ""))
            # round 17 (.101: "0.78 s unstamped (piece 1)" was mostly Pocket's lead-in quiet): against her VOICE, and the
            # silence held for the stamps (house-speak's stamp hold); absent keys = a round-16 speaker, nothing said
            if k.get("voice_early") is not None:
                parts.append("%s of %s stamped before her voice in them%s" % (
                    k["voice_early"], k["pieces"],
                    ", her voice ran at most %ss unstamped" % k["voice_late_s"] if k.get("voice_late_s") else ""))
            if k.get("held_s") is not None:
                parts.append("%ss of silence held for stamps" % k["held_s"])
        if k.get("chars"):
            parts.append("%s chars" % k["chars"])
        rows = int(c.get("rows") or 0)
        parts.append("%d of %d rows on the feed" % (max(0, rows - int(c.get("skipped") or 0)), rows))
        return "; ".join(parts)
    except Exception:  # noqa: BLE001  a log line never stops a paint
        return ""


class SemiCount:
    def __init__(self) -> None:
        self._clock_said: dict = {}       # air_at -> the clock printed for it (one line at a time)
        self._clock_asked = 0.0
        self.cur: dict | None = None
        self.lines: list = []          # the last 20 finished lines
        self._last = None
        self._semi = None
        self._cost: dict = {}          # per pass while she speaks: ms of composite / encode / pip / the whole pass

    def cost(self, **ms) -> None:
        """The pass's step times (the User 2026-10-11, "then go"): where a speaking paint's time goes."""
        if self.cur is None:
            return
        for k, v in ms.items():
            self._cost.setdefault(k, []).append(float(v))

    def _cost_summary(self) -> dict:
        out = {}
        for k, xs in self._cost.items():
            xs = sorted(xs)
            if xs:
                out[k] = {"p50": round(xs[len(xs) // 2], 1), "p90": round(xs[int(0.9 * (len(xs) - 1))], 1),
                          "n": len(xs)}
        return out

    def _close(self) -> None:
        if self.cur and self.cur["painted"]:
            c = self.cur
            c["cost_ms"] = self._cost_summary()
            c["closed"] = time.time()
            self.lines = (self.lines + [c])[-20:]
            cost = " ".join("%s %s/%s" % (k, v["p50"], v["p90"]) for k, v in sorted(c["cost_ms"].items()))
            c.pop("_p_last", None)
            byp = (" by paint: skipped %d doubled %d" % (c.get("p_skipped", 0), c.get("p_doubled", 0))
                   if c.get("p_painted") else "")
            print("[conductor] semis: rows %d painted %d skipped %d doubled %d%s (semi %.2f ms) ms p50/p90: %s"
                  % (c["rows"], c["painted"], c["skipped"], c["doubled"], byp, c["semi_ms"], cost), flush=True)
            self._say_clock(c)
        self.cur, self._last = None, None
        self._cost = {}

    def _say_clock(self, c: dict) -> None:
        """Print the line's lip clock once per change (round 12, .101: her answers logged none). A reply with pauses
        closes several counter lines, and its speaker's last post (the final clock) can land after the last close:
        tick() asks again for a while after a close, so the clock is printed when it arrives."""
        same = [x for x in self.lines if x.get("air_at") == c.get("air_at")] or [c]
        if c not in same:
            same.append(c)
        whole = dict(c, rows=sum(int(x.get("rows") or 0) for x in same),
                     skipped=sum(int(x.get("skipped") or 0) for x in same))     # the reply's rows, all its pieces
        clk = lip_clock_line(whole)
        key = clk.split("; ")[0:3] if clk else None
        if clk and self._clock_said.get(c.get("air_at")) != key:
            self._clock_said = {c.get("air_at"): key}
            c["clock"] = clk
            print("[conductor] lip clock: " + clk, flush=True)

    def tick_paint(self, mouth, vis_t: float | None) -> None:
        """The same count by the row the painter LOOKED UP (Mouth.vis_t), not the pass's end (2026-10-12): a pass of
        17-20 ms ends in the next slot and tick() counts it there, so near the budget tick() reads doubles and skips
        that were never painted. Reported beside tick()'s numbers as "by paint"."""
        c = self.cur
        if c is None or vis_t is None:
            return
        pts = hop_pts_at(list(getattr(mouth, "frames", None) or []), float(vis_t))
        if pts is None:
            return
        last = c.get("_p_last")
        c.setdefault("p_painted", 0)
        c["p_painted"] += 1
        if last is not None:
            if pts == last:
                c["p_doubled"] = c.get("p_doubled", 0) + 1
            elif pts > last:
                # rows the SHEET has strictly between the two (a pause in her voice has none: house-speak's sheet gaps)
                fr = list(getattr(mouth, "frames", None) or [])
                missed = sum(1 for r in fr if last < float(r[0]) < pts)
                if missed:
                    c["p_skipped"] = c.get("p_skipped", 0) + missed
        c["_p_last"] = pts

    def tick(self, mouth, now: float) -> None:
        if not getattr(mouth, "speaking", False):
            if self.cur is not None:
                self._close()
            elif self.lines and now - self._clock_asked >= 0.5 and now - float(self.lines[-1].get("closed") or 0) < 60.0:
                self._clock_asked = now
                self._say_clock(self.lines[-1])
            return
        frames = list(getattr(mouth, "frames", None) or [])
        air = getattr(mouth, "air_at", None)
        if self.cur is not None and self.cur["air_at"] != air:
            self._close()
        pts = hop_pts_at(frames, now)
        if pts is None:
            return
        if self.cur is None:
            gaps = sorted(float(b[0]) - float(a[0]) for a, b in zip(frames[-40:], frames[-39:])
                          if float(b[0]) > float(a[0]))
            self._semi = gaps[len(gaps) // 2] if gaps else 1.0 / 48.0
            self.cur = {"air_at": air, "rows": 1, "painted": 0, "skipped": 0, "doubled": 0,
                        "semi_ms": round(self._semi * 1000.0, 3), "started": now}
        c = self.cur
        c["painted"] += 1
        if self._last is not None:
            if pts == self._last:
                c["doubled"] += 1
            elif pts > self._last:
                step = int(round((pts - self._last) / max(1e-6, self._semi)))
                c["rows"] += max(1, step)
                if step > 1:
                    c["skipped"] += step - 1
        self._last = pts

    def state(self) -> dict:
        done = self.lines
        tot = {k: sum(int(x[k]) for x in done) for k in ("rows", "painted", "skipped", "doubled")}
        now_ = dict(self.cur, cost_ms=self._cost_summary()) if self.cur else None
        return {"on": SEMI_COUNT_ON, "now": now_, "last": done[-1] if done else None,
                "lines": len(done), "total": tot}


SEMIS = SemiCount()


class Mouth:
    """The voice envelope — the REAL one, per 20 ms hop.

    THE BUG THIS REPLACES. This used to read `crissa.level` off /v1/sound, which
    is a SPEAKING FLAG: measured 2026-08-30 across a whole utterance it is
    exactly 1.0, never anything else. So the mouth sat at the widest filmed
    frame for the entire line — wide open, unmoving, for as long as she talked.
    That is the bad mouth; the strip and its registration were fine.

    /v1/mouth carries what the glass has always used: rows of
    [timestamp, level 0..1, viseme id] at hop_ms. Measured on one line: 165
    rows, 56 distinct levels, median 0.122. Fetch the array a few times a
    second and index it by wall clock on every rendered frame — that keeps the
    full 20 ms resolution without polling at 50 Hz.

    Never blocks a frame: the poller is its own thread and the render loop only
    ever reads what is already in memory.
    """

    STALE_S = 0.5
    TAIL_S = MOUTH_TAIL_S

    def __init__(self) -> None:
        self.frames: list = []
        # Samples for the neural mouth, same POST as the envelope.
        # [[pts0, b64 s16le], ...] at `sr`; empty whenever nobody is speaking.
        self.pcm_rows: list = []
        self.sr = 24000
        self.on_air = False
        self.air_at = None
        self.air_until = None
        self.smoothed = 0.0
        self.hop_ms = 20.0
        # Composer QC: POST /v1/mouth-tune {"level": 0.8} holds a viseme
        # without the voice on the air. {"class":"EE"} holds that class so the
        # playlist/head paste has a note to look up. {"level": null} releases
        # both. Default None.
        self.force: float | None = None
        self.force_class: str | None = None
        # WHO IS ON THE AIR (2026-09-18, the User: "Crissas mouth moves on board
        # chatter"): the sheet carries house-speak's voice; tiles paint for
        # crissa, for lila only while she hosts (Render / gateway down), never
        # for the crew or the announcer. Kill HOUSE_MOUTH_SPEAKER_GATE=0.
        self.speaker = ""
        # 0 = never spoken. tick() stamps this while speaking; painting()
        # holds the strip for TAIL_S after so the jaw closes on the strip
        # instead of popping back to film mid-word.
        self.last_air = 0.0
        self._stop = False
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self) -> None:
        while not self._stop:
            try:
                with urllib.request.urlopen(MOUTH_URL, timeout=2.0) as r:
                    d = json.load(r)
                self.frames = d.get("frames") or []
                self.hop_ms = float(d.get("hop_ms") or 20.0)
                self.sr = int(d.get("sr") or 24000)
                # pcm is only for neural mouths. Prod is HOUSE_W2L=off and
                # PIP_W2L unset; decoding 10 Hz of b64 for nothing.
                need_pcm = (W2L_MODE != "off") or (PIP_W2L not in ("", "off"))
                self.pcm_rows = (d.get("pcm") or []) if need_pcm else []
                self.air_at = d.get("air_at")
                self.air_until = d.get("air_until")
                self.speaker = str(d.get("speaker") or "")
            except Exception:
                pass
            # speaking() uses hops + air_at/air_until from /v1/mouth.
            # /v1/sound crissa.speaking is a lamp; filling self.on_air from it
            # was unread (level/speaking already do not use it) and each GET
            # kicked TTS_GATE health ~20/s from this poller alone.
            time.sleep(MOUTH_POLL_S)

    def level(self) -> float:
        """Where the envelope is right now. A missing or STALE reading must mean
        "her mouth moves normally", never "her mouth freezes" — a frozen mouth
        on the air reads as broken rather than as quiet."""
        if self.force is not None:
            return self.force
        got = envelope_at(self.frames, time.time(), self.STALE_S)
        if got is None:
            # NEVER 1.0. /v1/sound crissa.level is a lamp: 1 for the whole
            # turn. Scoring that pegs the widest tile and holds it — fish
            # lips, unmoving — which is what a stuck speaking flag does
            # when hops never arrive. Filmed rest until hops exist.
            return 0.0
        return got

    def viseme(self) -> str | None:
        """Class at this hop. None = strip falls back to loudness.

        Composer `force` is a level hold; `force_class` is a class hold so
        the playlist can pick an /ee/ or /oo/ without the voice on the air.
        Off-air closes on rest so the tail shuts the jaw on a labelled
        closed tile. Prep window (air_at − lead) reads the hop, not rest
        — that is the jaw forming the first sound.
        """
        if self.force_class is not None:
            return self.force_class
        if self.force is not None:
            return None
        if not self.speaking and not self.preparing():
            return "rest"
        self.vis_t = _vt = time.time()      # the counter's "by paint" row (2026-10-12)
        return viseme_at(self.frames, _vt, self.STALE_S)

    def preparing(self, now: float | None = None) -> bool:
        """True in the 100–200 ms before air_at, when hops already exist."""
        lead = viseme_lead_s(VISEME_LEAD_MS)
        if lead <= 0 or self.air_at is None:
            return False
        if now is None:
            now = time.time()
        at = float(self.air_at)
        return (at - lead) <= now < at

    @property
    def speaking(self) -> bool:
        """On the air, or a Composer hold. Force is a viseme hold, not a line.

        The wall speaking FLAG is not enough: it sticks when house-speak
        dies without a close, and then last_air never ages out. Hops (or a
        still-open air window) are the picture's air.
        """
        if self.force is not None:
            return True
        if not self.speaker_may_paint():
            return False
        return mouth_on_air(
            self.frames, time.time(), self.air_at, self.air_until, self.STALE_S)

    def speaker_may_paint(self) -> bool:
        """crissa paints; lila paints while she hosts; the crew and the
        announcer never move her mouth. An unlabelled sheet fails open."""
        if not MOUTH_SPEAKER_GATE:
            return True
        if (globals().get("MOUTH_HUSH") or (lambda: False))():
            return False    # round 28: her timing lines in Setup are a voice over Lila's silent loop (crissa_init)
        who = str(self.speaker or "").split("@")[0].strip().lower()
        if not who or who == "crissa":
            return True
        if who == "lila":
            # A BENCH PAINTS HER (the User 2026-09-25: "I am on Lila and she is not painting tiles.
            # All selected properly"): "lila while hosting" was read as "Crissa's gateway is down",
            # which is true in Render and never on a tuning bench — so Lila's own conductor (:8101,
            # HOUSE_CONDUCTOR_ROLE=bench) painted nothing for every Say and every drill pass, and
            # the drill read r -9 / "a change every 361 f". The wall keeps the hosting rule.
            # A PACK'S SETUP PAINTS HER WHILE SHE IS ON HER PLATE (round 18, lib/crissa_init LILA'S LIPS ON HER
            # LINES): LILA_PAINTS is crissa_init.lila_lips_now in a pack's player, None on the house
            return IS_BENCH or not _crissa_gateway_up() or bool((globals().get("LILA_PAINTS") or (lambda: False))())
        return False

    def painting(self, now: float | None = None) -> bool:
        """Strip on this frame? Air, a hold, the prep window, or the close after."""
        if now is None:
            now = time.time()
        if self.force is None and not self.speaker_may_paint():
            return False
        if self.preparing(now):
            return True
        return mouth_painting(self.speaking, self.last_air, now, self.TAIL_S)

    def tick(self, dt: float) -> float:
        now = time.time()
        if self.speaking or self.preparing(now):
            self.last_air = now
        want = self.level() if self.speaking else 0.0
        self.smoothed += (want - self.smoothed) * min(1.0, dt * 18.0)
        return self.smoothed


class Playhead:
    """One clock, and it is ours. A wrap is `i == 0`; an end is `i == n-1`.
    No heuristics, because there is nothing to be uncertain about."""

    def __init__(self, lib: Library, seq: Sequencer, strip: Strip, mouth: Mouth) -> None:
        self.lib, self.seq, self.strip, self.mouth = lib, seq, strip, mouth
        self.key = ""
        self.i = 0
        self.i_at = time.time()
        self.wraps = 0
        # ONE live JPEG, overwritten every frame. Not Oracle's door ring
        # (rail-camera BUFFER_LEN=600, a different path). The previous
        # bytes are dropped the moment a new frame is encoded.
        self.frame_jpeg: bytes | None = None
        self.frame_pip_jpeg: bytes | None = None
        self.frame_no = 0
        self.frame_t = 0.0                # when this frame's mouth was picked (the glass canary)
        self.frame_mouth = None           # this frame's film mouth box (X-Mouth-Box), None = no header
        self.frame_clip = None            # this frame's (key, frame, frames, role) (X-Clip), None = no header
        self.started = time.time()
        self.last_box = None
        self._alpha_at: tuple | None = None
        self._alpha = 1.0
        self.vel = 0.0
        self.painted = 0
        self.skip = ""
        # ONE SERIES PER NOTE. the User, 2026-09-01, on the refit: *"it was really
        # good just flashing."* Measured cause: yaw_bin flips 3.8x/sec and
        # pitch_bin 1.2x/sec as noise crosses a bin edge, sending the lookup to
        # a different bin and a different tile MID-SOUND — 142 tile swaps in a
        # 10 s take against 118 actual notes. Resolve the index value once
        # when the class changes (bin flips must not re-lookup). A string
        # still holds for the whole note. A 3-list (vowels: half → full →
        # half) steps once over the note span, then holds the last half.
        self._pl_class = None
        self._pl_hit = None
        self._pl_bin = None
        self._pl_rel = None
        self._pl_t0 = 0.0
        # Where the LANDING tile starts. [t0, land) is the mouth arriving.
        self._pl_land = None
        self._pl_t1 = 0.0
        self._pl_loop = False
        self._pl_swaps = 0
        # Rest runs on its own clock (dwell, not slice) — see rest_step.
        self._rest_i = None
        self._rest_until = 0.0
        # Per (source take, dest loop) width k. Invalidated when the loop
        # changes — dest mouth AABB is a property of this clip.
        self._take_k: dict[tuple, float] = {}
        self._pin_last: dict = {}      # loop key -> last matched (dx, dy); hysteresis for the pixel pin
        self._pin_dbg: dict | None = None
        self._paint_errs = 0           # pastes that raised; the thread paints the raw frame and goes on
        # Tile cross-dissolve (xfade_ms): what is on the glass now, and the
        # outgoing tile + the moment it was replaced.
        self._xf_now: dict | None = None
        self._xf_from: tuple | None = None
        self._xf_t0 = 0.0
        self._scene = None
        self._cv = threading.Condition()
        self._stop = False
        # Casual abort: lamp left sage. Cut this frame, no wind-down.
        self._revoke_casual = False
        self._return_key: str | None = None
        # Mouth JPEG outruns the clip: leftover seconds toward the next body
        # frame. Reset on play() so a cut does not inherit the old remainder.
        self._clip_accum = 0.0
        self._painted = None             # (key, i) of the last composited frame (DISTINCT_ONLY)
        self._painted_at = 0.0
        self.paints = 0                  # composites since start (DISTINCT_ONLY's own count, /v1/state)
        # Dest-nose EMA. Reset on play / wrap jump so the pin does not smear.
        self._dst_nose_ema = None
        self._dst_ema_i = None
        # head.json identity for the dental cache. Re-stat on play().
        self._loop_ver_key = None
        self._loop_ver_val = None
        # Vocative HTTP must not warm dental on the request thread (ear-bridge
        # timeout  used to miss the cue and still say Yeah, the User?).
        self._pending_play: str | None = None
        # ½ s analog-switch onto the webcam loop, or a fade back to the room.
        self._cam: dict | None = None
        self._ft_release = False

    # ---- movement -------------------------------------------------------
    def play(self, key: str, force: bool = False, audio: bool = True, deliberate: bool = False) -> bool:
        key = self.seq.graph.resolve(key)          # a named moment as the worn library's own clip (MOMENT_ROLES)
        if DRILL.get("hold_frame") and not DRILL.get("on"):
            # ANY play releases the held last frame (2026-09-18). "until the next play" (09-13)
            # was coded as a play of a DIFFERENT key, so clicking the same harvest clip after a
            # match game left the bench frozen on its last frame with the tile pasted on the
            # still (the User: "this stops the video and pastes on freeze frame") — 1204/1205 all
            # afternoon. A drill in progress keeps its own lock; it is not a hold.
            DRILL["hold_frame"] = False
        if COMPOSER_TRANSPORT and not DRILL.get("on"):
            transport_release("play %s" % key, "play")     # the scrub hold too (fault 4)
        """Enter a clip. The attach law still vetoes, even a forced play — a
        cut is a cut whoever asked for it.

        Casual takes have no hubs. They play only while Secure (sage);
        `here` stays the hub she left so the bag does not empty.
        """
        casual = self.seq.graph.casual(key)
        if casual:
            if not self.seq.private:
                return False
        elif not force and not self.seq.graph.may_follow(key, self.seq.here):
            return False
        if self.lib.frames(key) is None:
            return False
        if casual:
            if not self._return_key:
                here = self.seq.here
                self._return_key = self.seq.graph.rest_for(here) or self.key
        else:
            self._return_key = None
        # The clip we are leaving is the one that belongs in History.
        prev = self.key
        if self.key:
            self.seq.finished(at=time.time())
        kept = False
        if prev and prev != key:
            # A harvest Say / follow-clip paplay used to keep talking after
            # the operator loaded another film (the User 2026-09-16). A move of her own does not (2026-10-11).
            if clip_change_cuts_line(deliberate, bool(audio and clip_audio.speech_window(self.lib.rows.get(key)))):
                say_stop_all()
            else:
                kept = True
                if say_pending():
                    action("say", "her line goes on: %s -> %s is a move of her own" % (prev, key), "playhead")
            if DRILL.get("on") and DRILL.get("key") != key:
                drill_stop("new clip")
        if key != self.key:
            self._take_k = {}
            # Playlist series is per dest loop. A held viseme class (H, EE)
            # used to keep the previous clip's tile — FaceTime mouthzoom
            # stamped onto listen/work (the User 2026-09-08, boxed close-up).
            self._pl_class = None
            self._pl_hit = None
            self._pl_bin = None
            self._pl_rel = None
            self._rest_i = None
            self._rest_until = 0.0
            rest = unpack_head(self.lib.head(key, 0))
            self._scene = build_scene(rest) if rest is not None else None
            self._loop_ver_key = None
            # Slides keep the filmed jaw. Warming dental for all 145 enter
            # frames blocked the render loop; dt then skipped the scoot and
            # she popped to close-up (the User 2026-09-08: no Connecting, snap).
            if mouth_paints_on(self.seq.graph.role(key)):
                ver = self._loop_ver(key)
                kw = float(HEAD_GEOM.get("kw") or 0.28)
                kh = float(HEAD_GEOM.get("kh") or 0.18)
                heads = []
                i = 0
                while True:
                    rec = self.lib.head(key, i)
                    if rec is None:
                        break
                    heads.append(unpack_head(rec))
                    i += 1
                warm_dental_r0(key, ver, heads, kw=kw, kh=kh, scale=MOUTH_SCALE)
        if IS_BENCH and CACHE_DROP_ON_LEAVE and self.key and self.key != key:
            # A BENCH LEAVING A CLIP DROPS ITS CACHE (the User 2026-09-18 "can we clear it at clip
            # end or change"): one clip at a time, no way back on its own — the left clip's
            # solves / maps / pins would only crowd the LRU. The wall never drops (work <->
            # flourish all day); a wrap never drops (it is where the hits come from).
            dropped = drop_loop(self.key)
            if dropped:
                action("mouth-cache", "%s left — %d cached entries dropped" % (self.key, dropped), "playhead")
        self.key, self.i, self.wraps = key, 0, 0
        clip_dials_enter(key)   # a clip with its own dial set wears it (2026-09-14)
        self._clip_accum = 0.0
        self._dst_nose_ema = None
        self._dst_ema_i = None
        # A NEW CLIP LOOKS ITS TILE UP AFRESH (the User 2026-10-03, "it jumps off and on the attachment": measured, the
        # first paint after a clip switch landed 49 px off by the previous clip's tile / band / turn state and the
        # second frame sat on her mouth) - the playlist hit, band and cell, and the turn landing's smoothing, are
        # the left clip's and must not survive into the first frame of this one.
        self._pl_hit = None
        self._pl_band = None
        self._pl_cell = None
        self._pl_class = None
        self._tri_ema_i = None
        self._tri_ema_a = None
        self._tri_ema_key = None
        # Box-velocity fade is per clip. Leaving last_box from the previous
        # film makes the first paste look like a 6–12 w/s head snap and skip
        # as `head moving` (bench 2026-09-16: work painted 2 then Say died).
        self.last_box = None
        self.vel = 0.0
        self._alpha_at = None
        # The clip on air is never an eviction candidate: dropping what the
        # playhead is reading costs a full re-decode in the middle of a frame.
        self.lib.hot = key
        self.seq.entered(key, at=time.time())
        if self.seq.take_ft_greet(time.time(), key, prev):
            # Ear deferred Yeah? here so it is not a second mouth on the
            # connection. Speak after the 0.6 s switch, on the close-up loop.
            # The name only when the ear named the User on that wake (BB, 2026-10-10).
            line = wake_greet_line(named=STATE.get("vocative_named"))

            def _greet(line=line, kept=kept, hold=WAKE_GREET_HOLD_S, woke=float(STATE.get("vocative_ts") or 0.0)):
                if hold > 0:
                    time.sleep(hold)
                    if float(STATE.get("greet_cancel_ts") or 0.0) >= woke > 0:
                        action("ft-greet-skipped", line, "he went on speaking")
                        return
                action("ft-greet", line, "vocative")
                if kept:
                    say_stop_all()      # the greet is a new line: the newest line wins, as it always did here
                _spawn_speak(line, "crissa")
            if WAKE_GREET_HOLD_S > 0:
                threading.Thread(target=_greet, name="ft-greet", daemon=True).start()
            else:
                _greet()
        # Baked host line: Composer live / the wall only show JPEGs. Skip when
        # the caller paplays (init, register) or the tiles drill owns --wav.
        if audio and not DRILL.get("on"):
            row = self.lib.rows.get(key) or {}
            clip = POSE_DIR / str(row.get("file") or "")
            if not clip.is_file():
                for bank in FACE_BANKS.values():
                    cand = bank / str(row.get("file") or "")
                    if cand.is_file():
                        clip = cand
                        break
            clip_audio.start(key, row, clip)
        else:
            clip_audio.stop()
        return True

    def apply_privacy(self, private: bool, color: str = "amber") -> None:
        """Sage = Secure. Casual only while true."""
        STATE["visit_lamp"] = color
        was = self.seq.private
        self.seq.private = bool(private)
        self.seq.graph.secure = bool(private)       # named moments wear their casual take under Secure (Graph.resolve)
        if was and not self.seq.private:
            self._revoke_casual = True
            # Secure timeout while FaceTime is up: fade the webcam back to
            # the room. Same as ear pause. Do not play push-back leave.
            if self.key == USER_TALK_KEY or self._cam:
                self._ft_release = True

    def revoke_casual(self) -> None:
        """Lamp left sage. Casual vanishes: queue rows go, and if she is
        in one, cut NOW to the next remaining cue or her rest. No slide."""
        self.seq.drop_casual()
        if not self.seq.graph.casual(self.key):
            return
        nxt = self.seq.cut_next()
        back = (
            self._return_key
            or self.seq.graph.rest_for(self.seq._casual_from)
            or self.seq.graph.rest_for(HOME)
            or "work"
        )
        self._return_key = None
        if nxt and self.play(nxt, force=True, deliberate=True):     # a mode switch (Secure): her line stops
            return
        self.play(back, force=True, deliberate=True)

    def abort_to_hub(self) -> dict:
        """Vocative cut on this JPEG playhead. Law 1: land on a hub PNG.

        Cue the vocative slide first (``insert``), then call this so a queued
        ``wake`` can start. ``insert()`` does not cut.
        """
        arr = self.lib.frames(self.key)
        n = 0 if arr is None else len(arr)
        role = self.seq.graph.role(self.key) if self.key else ""
        i = int(self.i)
        fps = float(ABORT_FPS)
        remaining_s = 0.0 if n <= 0 else max(0.0, (n - 1 - i) / fps)
        action = abort_to_hub_action(role, i, n, fps=fps, tail_s=ABORT_TAIL_S)
        played = None
        want_ft = (USER_TALK_KEY in self.seq.queued() and self.key != USER_TALK_KEY)
        if want_ft:
            # Camera-cut is the crossing. wait_end on a slide left the voice on a
            # filmed jaw (tiles paste on loops only). Do not paste rest.
            if action == "seek0":
                self.i = 0
                self.i_at = time.time()
                self._clip_accum = 0.0
            cued = self.seq.take_cue(time.time())
            self._pending_play = USER_TALK_KEY
            played = USER_TALK_KEY
            if cued and cued != USER_TALK_KEY:
                self.seq._q = [r for r in self.seq._q if r.get("key") != USER_TALK_KEY]
        elif action == "seek0":
            self.i = 0
            self.i_at = time.time()
            self._clip_accum = 0.0
            if self.key == USER_TALK_ENTER or self._pending_play == USER_TALK_ENTER:
                # Already scooting. take_cue would pop the close-up and snap.
                played = USER_TALK_ENTER
            else:
                cued = self.seq.take_cue(time.time())
                if cued:
                    # Do not play() here: dental warmup of enter blocked
                    # /v1/vocative past ear-bridge's 1 s timeout, so Yeah, the User?
                    # still spoke and Hey the User fired later on the leftover flag.
                    self._pending_play = cued
                    played = cued
        return {
            "ok": True,
            "action": action,
            "key": self.key,
            "i": self.i,
            "n": n,
            "role": role or None,
            "remaining_s": round(remaining_s, 3),
            "played": played,
        }

    def _home_rest(self) -> str:
        return self.seq.graph.rest_for(HOME) or "work"

    def _ft_hub(self) -> str | None:
        return self.seq.graph.start(USER_TALK_KEY)

    def _arm_cam(self, to_key: str, *, kind: str) -> None:
        """0.6 s connection (cut) or fade, then play ``to_key``. Connection
        first; the close-up loop starts after. Same JPEG feeds the wall and
        the FaceTime tile — the pip is not hidden."""
        arr = self.lib.frames(self.key)
        src = None if arr is None or not len(arr) else arr[int(self.i) % len(arr)].copy()
        dest_arr = self.lib.frames(to_key)
        dest = None if dest_arr is None or not len(dest_arr) else dest_arr[0].copy()
        dur = USER_TALK_CUT_S if kind == "cut" else USER_TALK_FADE_S
        self._cam = {
            "kind": kind, "to": to_key, "t0": time.time(), "dur": float(dur),
            "src": src, "dest": dest,
        }

    def _cam_paint(self, now: float) -> np.ndarray | None:
        """Overlay frame, or None once the cut/fade has handed off."""
        cam = self._cam
        if not cam:
            return None
        t = now - float(cam["t0"])
        if t >= float(cam["dur"]):
            to = cam["to"]
            self._cam = None
            self.play(to, force=True)
            return None
        src = cam.get("src")
        if src is None:
            arr = self.lib.frames(self.key)
            src = None if arr is None or not len(arr) else arr[0]
        if src is None:
            self._cam = None
            self.play(cam["to"], force=True)
            return None
        if cam["kind"] == "fade":
            dest = cam.get("dest")
            if dest is None:
                dest = src
            return fade_frame(src, dest, t, cam["dur"])
        return switch_frame(src, t, cam["dur"])

    def _poll_ft_release(self) -> None:
        """Pause or Secure timeout fades the webcam window back to the room.

        NOT ON A BENCH (the User 2026-09-15: "I loaded upclose, then I press Say
        and it reverted to Heads down working again. We need to edit all clips
        not only that one").

        USER_TALK_KEY is `laptop-closeup-eyes-down` — the webcam loop. Loading
        it to TUNE it looked, to this method, exactly like being in a call: it
        is the only test. So while the house sat paused, every ~20 s it ran
        bring_home(by="pause"), which clears seq.camp, and with the camp gone
        the next seam let the bag deal — back to `work`, the heads-down desk
        loop. The journal said it plainly once it was asked:
        "camp: cleared by bring_home (pause)".

        Ending a call and walking her back to the desk is a ROOM affordance.
        A bench has no call, no room and nobody watching but the operator, so
        it stands aside — and the fix is general, not a special case for this
        one clip: no house-pause path moves a bench's playhead.
        """
        if IS_BENCH:
            return
        # Hey Crissa arms a 0.6 s cut onto the webcam. house_paused() is a 1 s
        # cache of hear.paused — still true the same frame vocative unpauses.
        # Killing that cut left the chip on Listening, the body on work, and
        # no Yeah, the User? (the User 2026-09-16). Let the connection land; pause
        # fade is after she is on the webcam loop.
        if (self._cam is not None
                and self._cam.get("kind") == "cut"
                and self._cam.get("to") == USER_TALK_KEY
                and self.key != USER_TALK_KEY):
            return
        paused = False
        try:
            paused = house_paused()
        except Exception:
            paused = False
        want = bool(self._ft_release or paused)
        on_webcam = (
            self.key == USER_TALK_KEY
            or (self._cam is not None and self._cam.get("to") == USER_TALK_KEY)
        )
        if not want or not on_webcam:
            return
        if self._cam is not None and self._cam.get("kind") == "fade":
            return                      # already dissolving back to the room
        mid_cut = self._cam is not None and self._cam.get("kind") == "cut"
        self._ft_release = False
        self._cam = None
        self.seq.abandon_ft()
        self.seq.end_comms("pause" if paused else "secure")
        if mid_cut and self.key != USER_TALK_KEY:
            # Squiggle never landed: stay on the desk.
            return
        r = self.seq.bring_home(time.time(), by="pause" if paused else "secure")
        if r.get("taken") and r.get("when") != "already home":
            cued = self.seq.take_cue(time.time())
            if cued:
                self._arm_cam(cued, kind="fade")

    def _advance(self) -> None:
        arr = self.lib.frames(self.key)
        if arr is None:
            return
        n = len(arr)
        self.i += 1
        # WHEN THIS FRAME BEGAN (2026-09-21). The playhead knew WHICH frame it
        # was on and never when it got there, so anything that had to say "frame
        # f will be shown at T" — the calibration signal — had to guess from a
        # ph.i read that was already stale by the time it was used. Measured:
        # the same pacer pass read lag +2 f once and +14 f later, which is
        # startup jitter being reported as pipeline latency.
        self.i_at = time.time()
        if self.i < n:
            return
        # the clip is over: a loop comes round, anything else hands back
        self.i = 0
        self.i_at = time.time()
        now = time.time()
        role = self.seq.graph.role(self.key)
        # A BENCH NEVER DEALS (the User 2026-09-15: "I want it to say the sentence
        # with the clip I have selected … those two things are not supposed to
        # switch the clip being played in the player are they?").
        #
        # They are not, and the fix belongs here rather than in the page. A
        # bench is a tuning stage: whatever the User loaded stays up until he loads
        # something else. The wall deals because a room needs a body that moves;
        # a bench has nobody watching but the person turning the dial.
        #
        # Measured on :8110 before this: load `listen-close` (role oneshot) with
        # stay:true, camp reads listen-close, and the playhead still walked
        # listen-close -> work -> flourish-bun-wobble-think-work within eight
        # seconds, because the non-loop tail below calls seq.home() and the
        # loop branch deals from the bag. `work` on the down hub IS the
        # head-down clip he kept seeing. The 09-14 camp guard under this only
        # covers the case where camp is set AND still matches, and bring_home
        # clears camp, so it leaked.
        #
        # Holding means looping from frame 0, which is what a tuning stage
        # wants: the Say, the drill and the tiles all read a clip that is
        # still there. A queued Composer chain is the exception: take_cue
        # at the seam so Queue/Combo play on this bench, not the live wall
        # (the User 2026-09-16). The bag still does not deal here.
        if IS_BENCH:
            if role == "loop":
                self.wraps += 1
                self.seq.wrapped()
            cued = self.seq.take_cue(now)
            if cued:
                self.seq.camp = None
                if self.play(cued, force=True):
                    return
            hold = self.seq.camp or self.key
            if hold != self.key:
                self.play(hold, force=True)
            return
        # Tune stay on a harvest oneshot (speak-phoneme): replay, do not
        # home to work then Lila (the User 2026-09-14 Composer Say/tiles).
        if self.seq.camp and role != "loop":
            if self.key != self.seq.camp:
                self.play(self.seq.camp, force=True)
            return
        if role == "loop":
            self.wraps += 1
            self.seq.wrapped()
            if (not IS_BENCH and not _crissa_gateway_up() and not _lila_host_key(self.key)
                    and _crissa_idle_steal(self.key)):
                # Gateway went down mid-clip (Render sleep). Do not deal
                # Crissa's bag — host Lila's work loop on this seam.
                try:
                    from crissa_gateway import lila_host_default
                    host = lila_host_default(comms=bool(self.seq.comms))
                except Exception:
                    host = "lila-work"
                self.play(host, force=True, deliberate=True)       # a mode switch (Render): her line stops
                self.seq.camp = host
                return
            # A seam. An insert gets first refusal here — that is what "on
            # demand" means without ever cutting a gesture.
            cued = self.seq.take_cue(now)
            if cued:
                if (cued in (self._home_rest(), WORK_GLANCE_KEY)
                        and self.key == USER_TALK_KEY
                        and self.seq.here == self._ft_hub()):
                    self._arm_cam(cued, kind="fade")
                    return
                if self.play(cued):
                    return
            try:
                self.seq.flourish_pct = int(
                    load_wall_prefs().get("flourish_pct", 35))
            except Exception:
                pass
            if self.seq.due():
                nxt = self.seq.next_key()
                if nxt and nxt != self.key:
                    self.play(nxt)
                    return
                # FaceTime holds until the session homes her. play(same)
                # seek0'd the close-up (why=brain, snap to face, no Connecting).
                # A call holds on whichever core she is on (2026-09-10).
                if self.key == USER_TALK_KEY or self.seq.comms:
                    return
                # Render / Init camp: stay on Lila's listen. Wrap used to
                # bring_home(by=wrap) because at_home is Crissa's `down`, so
                # two wraps of lila-reg-listen dumped Crissa's work loop on
                # the Wall (the User 2026-09-14).
                if self.seq.camp:
                    # HEADS DOWN WHEN NOBODY IS TALKING TO HER (the User 2026-10-27, the crissatest walk: "the only
                    # loop we have is the up smiler ... we want heads down and flourishes for paused and after a bit
                    # of F2F talking"): a pack boots, lands after Setup and comes back from the phone room CAMPED on
                    # her boot loop, and in a pack nothing ever cleared that camp (the pause release below is the
                    # webcam loop's alone), so she looped her listening loop for good. The camp on HER BOOT LOOP is
                    # soft: it holds her through a session (the bridge's /health says one is open) and lifts at the
                    # first wrap with no session open, and the ordinary way home takes her to the desk, where the
                    # bag deals her flourishes. Setup's holds (the host's loop, the office) are other keys and stay
                    # hard; a bench never lifts. Kill CRISSA_BOOT_CAMP_LIFT=0.
                    if (BOOT_CAMP_LIFT and not IS_BENCH and self.seq.camp == _BOOT_CAMP["key"]
                            and not house_in_conversation()):
                        print("[conductor] camp: %s lifted at a quiet wrap (no session open): heads down"
                              % self.seq.camp, flush=True)
                        action("camp", "%s lifted at a quiet wrap (no session open): heads down" % self.seq.camp,
                               "wrap")
                        self.seq.camp = None
                    else:
                        if self.key != self.seq.camp:
                            self.play(self.seq.camp)
                        return
                # A VISIT WITH NOTHING TO DO IS OVER. `pool()` already warns
                # that standing frozen because the bag came up empty is "a bug
                # wearing a choice's clothes" -- measured on `back`, 2026-08-30.
                # It recurred 2026-09-02 on `down_close`, which has one loop and
                # no flourishes at all: due() went true, next_key() dealt None,
                # and she wrapped that close-up for ever with the leave slide
                # sitting unused in the kit. HOME has nine flourishes and can
                # hold her; a destination is somewhere she visits.
                if not self.seq.at_home():
                    r = self.seq.bring_home(now, by="wrap")
                    if r.get("taken"):
                        cued = self.seq.take_cue(now)
                        if cued:
                            self.play(cued)
                    else:
                        # An unreachable hub must SAY so. `up` is one today:
                        # sits-up and rest-close are both element:false, so
                        # compose_ok excludes them and path(up, down) is empty
                        # in both directions. Silence here is how she sits on a
                        # dead end looking like a decision.
                        self.skip = str(r.get("why") or "no way home")
            return
        # Casual keeps `here` on the hub she left, so take_cue and home()
        # still attach. Privacy abort is revoke_casual(), not this seam.
        cued = self.seq.take_cue(now)
        if cued and self.play(cued):
            return
        nxt = self.seq.home() or self.seq.next_key()
        if nxt:
            self.play(nxt)

    # ---- the picture ----------------------------------------------------
    def _take_width_k(self, rel: str, dst: dict) -> float:
        """One k for this tile's harvest take onto the playing loop."""
        take = tile_take(rel)
        # take-uni is per FRAME: her mouth width on this frame is the ruler
        key = (take, self.key)
        if MOUTH_FIT == "take-uni":
            key = (take, self.key, int(self.i))
        if key in self._take_k:
            return self._take_k[key]
        if MOUTH_FIT == "take-uni" and len(self._take_k) > 4096:
            self._take_k.clear()
        dest_m = dst.get("mouth")
        dest_w = 0.0
        if dest_m is not None:
            d = np.asarray(dest_m, dtype=np.float64).ravel()
            if d.size >= 3:
                dest_w = float(d[2])
        widths: list[float] = []
        preferred: list[float] = []
        pl = self.lib.playlist(self.key) or {}
        for pkey, hit in (pl.get("index") or {}).items():
            klass = str(pkey).rsplit("/", 1)[-1]
            for t in as_tiles(hit):
                if tile_take(t) != take:
                    continue
                rec = self.lib.tile_head(t) or {}
                src = unpack_head(rec)
                box = rec.get("mouth_in_tile")
                if src is None or box is None:
                    continue
                M0 = warp_affine_2x3(src, dst, scale=MOUTH_SCALE)
                q = mouth_quad(box, M0)
                w = float(q[:, 0].max() - q[:, 0].min())
                if w > 2.0:
                    widths.append(w)
                    if klass in TAKE_WIDTH_CLASSES:
                        preferred.append(w)
        k = take_width_k(preferred or widths, dest_w)
        self._take_k[key] = k
        return k

    def _loop_ver(self, key: str | None = None):
        """head.json mtime_ns+size. A re-landmark misses the dental cache."""
        k = self.key if key is None else key
        if getattr(self, "_loop_ver_key", None) == k and self._loop_ver_val is not None:
            return self._loop_ver_val
        row = self.lib.rows.get(k) or {}
        stem = Path(str(row.get("file") or k)).stem
        val = loop_version(POSE_DIR / (stem + ".head.json"))
        self._loop_ver_key = k
        self._loop_ver_val = val
        return val

    def _dental_paste_maps(self, src, dst, M, tile, *, raw=None,
                           viseme=None, rel=None):
        """Tier 1 fit on RAW dest (corners+chin). Maps LRU includes pin, not EMA.

        Dest-nose EMA is a function of the loop — do not bake random
        liveliness jitter into A,B,C. Pin offsets are in the maps key so
        a tune of HOUSE_MOUTH_NOSE_U misses instead of serving stale maps.
        """
        kw = float(HEAD_GEOM.get("kw") or 0.28)
        kh = float(HEAD_GEOM.get("kh") or 0.18)
        ver = self._loop_ver()
        dental = dental_r0_cached(
            self.key, int(self.i), ver, raw if raw is not None else dst,
            kw=kw, kh=kh, scale=MOUTH_SCALE)
        return dental_maps_cached(
            loop_id=self.key, frame_i=int(self.i), version=ver,
            viseme=viseme if viseme is not None else getattr(self, "_pl_class", None),
            rel=rel if rel is not None else getattr(self, "_pl_rel", None),
            p_key=tuple(p_key_from_dental(dental)) + (round(float(MOUTH_SAG_K), 4),),
            src=src, dst=dst, wh=paint_wh(), M=M,
            scale=MOUTH_SCALE, tile_hw=tile.shape[:2], dental=dental,
            pin=(MOUTH_NOSE_U, MOUTH_NOSE_V, MOUTH_FIT, MOUTH_PIN), kw=kw, kh=kh)

    def _dest_for_paste(self, dst: dict | None) -> dict | None:
        """EMA the dest nose, then shift the pin in face UV.

        Hopping independent of her nose is dest-pin chatter (skill: 3–5
        frame EMA). Her-left nostril tear is a small +u (HOUSE_MOUTH_NOSE_U).
        Same clip-frame is not blended twice (paste + hole share this).
        """
        if dst is None:
            return None
        i = int(self.i)
        nose = dst["nose"]
        got = self._nose_tracked(i, nose) if NOSE_EMA_TRACK else None
        if got is not None:
            # ONE NOSE PER FRAME (2026-09-19): the loop's EMA as contiguous play
            # makes it, by frame index — a dropped frame no longer snaps the pin
            # to the raw nose and re-keys the solve cache (poly_cache.nose_ema_track)
            pinned = got
            self._dst_nose_ema = pinned
            self._dst_ema_i = i
        elif self._dst_ema_i == i and self._dst_nose_ema is not None:
            pinned = self._dst_nose_ema
        elif self._dst_ema_i is None or abs(i - int(self._dst_ema_i)) > 1:
            pinned = ema_nose(None, nose, 0.0)
            self._dst_nose_ema = pinned
            self._dst_ema_i = i
        else:
            pinned = ema_nose(self._dst_nose_ema, nose, MOUTH_NOSE_EMA)
            self._dst_nose_ema = pinned
            self._dst_ema_i = i
        out = dict(dst)
        out["nose"] = pinned
        if MOUTH_TURN_FIX:
            out = turn_correct(out, MOUTH_TURN_DEPTH, MOUTH_TURN_CHORD_DEG, MOUTH_TURN_MAX_YAW)
        if MOUTH_PIN == "nose-pixel" and not (TRIAD_NUDGE and self._triad_frame(dst)):
            # the pixel match would re-centre over a pre-shift; the nudge is
            # applied AFTER the match in _paste_head (the User 06:4x "a tiny bit higher")
            return out
        # TRIAD GETS THE NUDGE UP FRONT (the User 2026-09-15: "ensure we give each
        # mode in TUNE its correct attach points"). Under pin=nose-pixel this
        # method defers the nudge to the post-match shift — but the triad path
        # never lands a pin (0/2742 matched past 35° yaw, measured
        # bench/triad-pin-20260915), so nose_u/nose_v were dropped on every
        # triad frame: the dial read 0/−8 and moved nothing. There is no match
        # to ride there, so it rides the landmarks like the other pins do.
        # HOUSE_MOUTH_TRIAD_NUDGE=0 restores the old drop.
        return shift_nose(out, MOUTH_NOSE_U, MOUTH_NOSE_V, px=True)

    def _nose_tracked(self, i: int, nose):
        """The loop's EMA'd nose at frame i, or None (no track, or the dest handed in
        is not this loop's own frame i — then the live EMA runs as before). Built once
        per (loop, head.json version, nose_ema dial) on first use."""
        try:
            ck = (self.key, self._loop_ver(), float(MOUTH_NOSE_EMA), NOSE_ZERO_LAG)
            trk = getattr(self, "_nose_trk", None)
            if trk is None or trk[0] != ck:
                n = self.lib.head_track_len(self.key)
                dests = [unpack_head(self.lib.head(self.key, j)) for j in range(n)]
                trk = (ck, nose_ema_track(dests, float(MOUTH_NOSE_EMA), zero_lag=NOSE_ZERO_LAG))
                self._nose_trk = trk
            rows = trk[1]
            if not (0 <= i < len(rows)) or rows[i] is None:
                return None
            raw, pinned = rows[i]
            if not np.array_equal(raw, np.asarray(nose, dtype=np.float64).ravel()[:2]):
                return None
            return pinned.copy()
        except Exception:  # noqa: BLE001 — never block a paste on the track
            return None

    def _triad_frame(self, dst: dict | None) -> bool:
        """Would this frame take the triad path? (the nudge attach point)"""
        try:
            return live_lookup(self._scene, getattr(self, "_pl_class", None),
                               dst, warp=MOUTH_WARP) == "triad"
        except Exception:  # noqa: BLE001 — never block a paste on a lookup
            return False

    def _pin_shift(self, M, tile, src, dst, base, rel=None):
        """pin_nose_pixel's answer (dx, dy, ncc) for this frame × tile ×
        landing, cached (poly_cache pin_shift_cached, 2026-09-14): the
        template match is the dearest step of a paint (3–5 ms) and a wrap
        asks the same question again."""
        nudge = (round(float(MOUTH_NOSE_U), 3), round(float(MOUTH_NOSE_V), 3))

        def compute():
            _M, (dx, dy), ncc = pin_nose_pixel(M, tile, src, dst, base)
            return (float(dx), float(dy), float(ncc))

        return pin_shift_cached(self.key, int(self.i), self._loop_ver(), rel, M, nudge, compute)

    def _pixel_pin(self, M, tile, src, dst, base, rel=None):
        """pin=nose-pixel: match the tile's nose into the frame, translate
        the landing by that shift ONCE, then the post-match nudge (px at
        s 357, scaled by s). Returns (M', dst').

        2026-09-14: the 09-10 16:56 refactor applied the measured shift
        twice — pin_nose_pixel had already moved M, and this method moved
        it again with the nudge — so every affine tile (and the idle rest)
        landed at 2× the match. The 06:13 "that's it!" version moved it
        once; measured on work / talk-loop / talking-head the tile mouth
        sits closer to her own at 1× (median 4.9 / 1.2 / 3.2 px vs 5.2 /
        2.4 / 3.4). Once again."""
        pdx, pdy, ncc = self._pin_shift(M, tile, src, dst, base, rel)
        if not isinstance(self._pin_last, dict):
            self._pin_last = {}
        # HYSTERESIS, DEFAULT OFF (pin_hold dial). Written 2026-09-12 for a
        # jitter that turned out to be the Haar-box fallback (Lila's tiles
        # had no head record), so it has no evidence behind it. Off, a miss
        # leaves the landmark pin as before. On, a miss keeps the last good
        # shift for this loop. Ship guards default-OFF until the User's eye
        # says otherwise.
        took = ncc >= 0.90
        if not took and MOUTH_PIN_HOLD:
            last = self._pin_last.get(self.key)
            if last is not None:
                pdx, pdy = last
        else:
            self._pin_last[self.key] = (pdx, pdy)
        self._pin_dbg = {"ncc": round(float(ncc), 3), "took": bool(took),
                         "pixel": [round(float(pdx), 1), round(float(pdy), 1)], "frame": int(getattr(self, "i", -1))}
        ndx = ndy = 0.0
        if took or MOUTH_NUDGE_ALL:   # the nudge rides a real match (min_ncc 0.90 since 09-11), or every landing (NUDGE_ALL)
            ndx, ndy = self._nudge_px(dst)
        sx, sy = pdx + ndx, pdy + ndy
        M = np.asarray(M, dtype=np.float64)
        if sx or sy:
            T = np.array([[1.0, 0.0, sx], [0.0, 1.0, sy], [0.0, 0.0, 1.0]])
            M = (T @ np.vstack([M, [0.0, 0.0, 1.0]]))[:2]
            dst = dict(dst)
            dst["nose"] = np.asarray(dst["nose"], dtype=np.float64).ravel()[:2] + np.array([sx, sy])
        if MOUTH_CHIN_PIN and tile is not None and base is not None:
            H, W = base.shape[:2]
            warped = cv2.warpAffine(tile, M.astype(np.float32), (W, H), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            st, cdbg = self._chin_stretch(rel, src, dst, base, warped, lambda p: MP.apply(M, p)[0],
                                          tile=tile, cheeks=False)
            if st is not None:
                M = np.asarray(stretch_affine_axis(M, st[0], st[1], st[2]), dtype=np.float64)
            self._pin_dbg["chin"] = cdbg
        return M.astype(np.float32), dst

    def _her_anchors(self, i: "int | None" = None) -> "np.ndarray | None":
        """Her five anchors on a film frame (lipmarks: lip corners, lip-line centre, nose tip, chin), paint px."""
        i = int(self.i) if i is None else int(i)
        row = self.lib.rows.get(self.key) or {}
        lm = self.lib.lipmarks(str(row.get("file") or self.key))
        fr = (lm or {}).get("frames") or []
        if not lm or not (0 <= i < len(fr)):
            return None
        k = paint_wh()[0] / float((lm.get("wh") or [paint_wh()[0]])[0])
        return MP.anchor_points(fr[i], scale=k)

    @staticmethod
    def _chin_gap(ta, da, land, warped, base, s, box=None):
        """(tile nose landed, her axis, landed nose->chin length d, her chin, how, ncc, tile chin) for one landing."""
        tn, tc = land(ta[3]), land(ta[4])
        if tn is None or tc is None:
            return None
        tn, tc = np.asarray(tn, dtype=np.float64), np.asarray(tc, dtype=np.float64)
        ax = np.asarray(da[4], dtype=np.float64) - np.asarray(da[3], dtype=np.float64)
        lh = float(np.linalg.norm(ax))
        if lh < 1.0:
            return None
        a = ax / lh
        mv, ncc = pin_chin_pixel(warped, base, tc, s, box=box)
        if mv is not None:
            her, how = tc + np.asarray(mv, dtype=np.float64), "pixel"
        else:
            her, how = np.asarray(da[4], dtype=np.float64), "landmark"
        return tn, a, float(np.dot(tc - tn, a)), her, how, float(ncc), tc

    def _chin_ref_frames(self, n_ref: int = 5) -> list:
        """The loop's REST POSE for the chin baseline: the n_ref frames whose head pitch is nearest the loop's median
        pitch (measured on Bash's listen-screen: frame 0, the anchor, is itself lifted 7 deg off the loop's median, and
        its AH chin read 17.1 px below hers where the median frames read 13.6 - a baseline on it would keep the lift;
        one frame alone read 1 px off the level mean, so the baseline is the median of five)."""
        try:
            n = int(self.lib.head_track_len(self.key) or 0)
            ps = []
            for j in range(n):
                h = self.lib.head(self.key, j)
                if h and h.get("pose") is not None:
                    ps.append((float(h["pose"][0]), j))
            if not ps:
                return []
            med = float(np.median([p for p, _ in ps]))
            return [j for _, j in sorted(ps, key=lambda pj: (abs(pj[0] - med), pj[1]))[:max(1, int(n_ref))]]
        except Exception:  # noqa: BLE001
            return []

    def _chin_ref_frame(self) -> "int | None":
        """The single frame nearest the loop's median pitch (the record names it)."""
        try:
            n = int(self.lib.head_track_len(self.key) or 0)
            ps = []
            for j in range(n):
                h = self.lib.head(self.key, j)
                if h and h.get("pose") is not None:
                    ps.append((float(h["pose"][0]), j))
            if not ps:
                return None
            med = float(np.median([p for p, _ in ps]))
            return min(ps, key=lambda pj: (abs(pj[0] - med), pj[1]))[1]
        except Exception:  # noqa: BLE001
            return None

    def _clip_talks(self, key: "str | None" = None) -> "bool | None":
        """Does this clip speak (its row: a harvest's pose_class, or a words sidecar) AND do her filmed lips part on it
        (past twice MOUTH_CHIN_TALK_OPEN on some frame, her lipmarks over the head track's s)? Cached per (clip, loop
        version). False / None = a still clip, or not measurable: the stretch stays."""
        key = self.key if key is None else key
        ck = (key, self._loop_ver())
        memo = getattr(self, "_talks_memo", None)
        if memo is None:
            memo = self._talks_memo = {}
        if ck in memo:
            return memo[ck]
        out = None
        try:
            row = self.lib.rows.get(key) or {}
            stem = Path(str(row.get("file") or ("clip-" + str(key)))).stem
            speaks = bool(row.get("pose_class")) or any(
                (Path(POSE_DIR) / "visemes" / (stem + ext)).is_file() for ext in (".timeline.json", ".phon.json"))
            if not speaks:
                memo[ck] = False
                return False
            lm = self.lib.lipmarks(str(row.get("file") or key))
            fr = (lm or {}).get("frames") or []
            k = paint_wh()[0] / float((lm.get("wh") or [paint_wh()[0]])[0]) if lm else 1.0
            ap = []
            for i, m in enumerate(fr):
                if not m or m.get("up") is None or m.get("lo") is None:
                    continue
                sc = float((unpack_head(self.lib.head(key, i)) or {}).get("s") or 0.0)
                if sc > 0.0:
                    ap.append(float(np.linalg.norm(np.subtract(m["lo"], m["up"]))) * k / sc)
            if ap:
                out = max(ap) >= 2.0 * MOUTH_CHIN_TALK_OPEN
        except Exception:  # noqa: BLE001 - not measurable is an answer: the stretch stays as it was
            out = None
        memo[ck] = out
        return out

    def _chin_anchor_below(self, rel, src, tile, cheeks: bool):
        """THE CLASS BASELINE: how far below her chin this tile's chin lands on the loop's REST POSE frames
        (_chin_ref_frames, the median of five), by the same two pixel measures. That is the tile's own open jaw as it lands at rest; only
        what a lift adds on top of it is taken out. Cached per (loop, version, tile, landing)."""
        ver = self._loop_ver()
        ck = (self.key, ver, rel, bool(cheeks), round(float(MOUTH_SCALE), 4),
              round(float(MOUTH_NOSE_U), 3), round(float(MOUTH_NOSE_V), 3))
        memo = getattr(self, "_chin_base", None)
        if memo is None:
            memo = self._chin_base = {}
        if ck in memo:
            return memo[ck]
        reads = []
        for j0 in self._chin_ref_frames():
            try:
                ta = self.lib.tile_anchors(rel)
                da = self._her_anchors(j0)
                fr = self.lib.frames(self.key)
                raw = unpack_head(self.lib.head(self.key, j0))
                if ta is None or da is None or fr is None or not len(fr) or raw is None or len(ta) != 5 or len(da) != 5:
                    continue
                base0 = fr[j0]
                d0 = dict(raw)
                if MOUTH_TURN_FIX:
                    d0 = turn_correct(d0, MOUTH_TURN_DEPTH, MOUTH_TURN_CHORD_DEG, MOUTH_TURN_MAX_YAW)
                Maff0 = warp_affine_2x3(src, d0, scale=MOUTH_SCALE)
                _M, (pdx, pdy), ncc = pin_nose_pixel(Maff0, tile, src, d0, base0)
                ndx = ndy = 0.0
                if ncc >= 0.90 or MOUTH_NUDGE_ALL:
                    ndx, ndy = self._nudge_px(d0)
                sx, sy = (pdx if ncc >= 0.90 else 0.0) + ndx, (pdy if ncc >= 0.90 else 0.0) + ndy
                H, W = base0.shape[:2]
                g = None
                if cheeks:
                    sol = cheeks_solve_cached(self.key, j0, ver, d0, paint_wh(), MOUTH_SCALE)
                    maps0 = cheeks_maps_from_solve(sol, src, d0) if sol is not None else None
                    maps0 = shift_maps(maps0, sx, sy) if maps0 is not None else None
                    if maps0 is not None:
                        warped0, box0 = self._remap_in_box(tile, maps0)
                        if warped0 is not None:
                            g = self._chin_gap(ta, da, lambda p, m0=maps0: self._landed(m0, None, p), warped0, base0,
                                               float(d0.get("s") or 100.0), box=box0)
                else:
                    T = np.array([[1.0, 0.0, sx], [0.0, 1.0, sy], [0.0, 0.0, 1.0]])
                    M0 = (T @ np.vstack([np.asarray(Maff0, dtype=np.float64), [0.0, 0.0, 1.0]]))[:2]
                    warped0 = cv2.warpAffine(tile, M0.astype(np.float32), (W, H), flags=cv2.INTER_LINEAR,
                                             borderMode=cv2.BORDER_CONSTANT, borderValue=0)
                    g = self._chin_gap(ta, da, lambda p, m0=M0: MP.apply(m0, p)[0], warped0, base0,
                                       float(d0.get("s") or 100.0))
                if g is not None:
                    tn0, a0, dd0, her0, _how, _ncc, _tc = g
                    reads.append(dd0 - float(np.dot(her0 - tn0, a0)))
            except Exception:  # noqa: BLE001 - a frame that cannot be built is left out of the baseline
                continue
        below0 = float(np.median(reads)) if reads else None
        memo[ck] = below0
        return below0

    @staticmethod
    def _remap_in_box(tile, maps):
        """(warped, box): the tile remapped through dest-sized maps, computed inside the maps' ROI only (crop_maps; every
        pixel outside it is the -1 sentinel, black either way, so the picture is the whole-frame remap's, byte for byte),
        and that ROI as (x0, y0, x1, y1) for pin_chin_pixel. 2026-10-27: at 1280x720 the chin pin read whole frames for
        a box a few hundred pixels wide. (None, None) when nothing maps."""
        H, W = maps[0].shape[:2]
        roi = _hf_mod.crop_maps(maps)
        if roi is None:
            return None, None
        x0, y0, mxr, myr = roi[0], roi[1], roi[2], roi[3]
        h, w = mxr.shape[:2]
        warped = np.zeros((H, W) + tile.shape[2:], dtype=tile.dtype)
        warped[y0:y0 + h, x0:x0 + w] = cv2.remap(tile, mxr, myr, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                                                 borderValue=0)
        return warped, (x0, y0, x0 + w, y0 + h)

    def _chin_stretch(self, rel, src, dst, base, warped, land, tile=None, cheeks=False, box=None):
        """TWO PINS, NOSE AND CHIN TIP (the User 2026-10-26). With the nose already pinned by pixels: where must the
        tile's chin land, and the stretch along HER nose->chin axis about the tile's landed nose that puts it there.
        Target = her chin by pixels (pin_chin_pixel on the landed tile's own chin patch; her lipmarks chin when the
        match is weak, named in the record) + the class baseline: how far below her chin this tile lands on the loop's
        rest-pose frame (_chin_anchor_below), so an open AH keeps the open jaw it has at rest and only the drift a lift adds
        is taken out. Returns ((centre, axis, f) | None, dbg)."""
        if MOUTH_CHIN_STILL_ONLY and self._clip_talks():
            return None, {"how": "a clip that talks: her own tile's jaw stands (no stretch)"}
        ta = self.lib.tile_anchors(rel) if rel else None
        da = self._her_anchors()
        if ta is None or da is None or len(ta) < 5 or len(da) < 5:
            return None, {"how": "no anchors"}
        g = self._chin_gap(ta, da, land, warped, base, float(dst.get("s") or 100.0), box=box)
        if g is None:
            return None, {"how": "the tile's chin is off its landing"}
        tn, a, d, her, how, ncc, tc = g
        below = d - float(np.dot(her - tn, a))
        base_below = self._chin_anchor_below(rel, src, tile, cheeks) if tile is not None else None
        dbg = {"how": how, "ncc": round(ncc, 3), "her_chin": [round(float(v), 1) for v in her],
               "tile_chin": [round(float(v), 1) for v in tc], "d": round(d, 2),
               "below_before": round(below, 2),
               "rest_below": None if base_below is None else round(float(base_below), 2),
               "rest_frame": self._chin_ref_frame()}
        if base_below is None:
            dbg["how"] += " (no rest baseline: no stretch)"
            return None, dbg
        if d < 1.0:
            dbg["how"] += " (tile chin above its nose: no stretch)"
            return None, dbg
        want = float(np.dot(her - tn, a)) + float(base_below)
        f = max(MOUTH_CHIN_PIN_LO, min(MOUTH_CHIN_PIN_HI, want / d))
        dbg["f"] = round(f, 4)
        dbg["below_after"] = round(f * d - float(np.dot(her - tn, a)), 2)
        return (tn, a, f), dbg

    def _nudge_px(self, dst) -> tuple[float, float]:
        """The post-match nudge in dest px: nose_u/nose_v along her unit
        axes, scaled by s / 357 (the User 2026-09-10 06:4x "a tiny bit higher")."""
        uu = np.asarray(dst["u"], dtype=np.float64).ravel()[:2]; uu = uu / max(1e-6, float(np.linalg.norm(uu)))
        vv = np.asarray(dst["v"], dtype=np.float64).ravel()[:2]; vv = vv / max(1e-6, float(np.linalg.norm(vv)))
        ks = float(dst.get("s") or MOUTH_NUDGE_REF_S) / max(1.0, MOUTH_NUDGE_REF_S)
        nd = (MOUTH_NOSE_U * uu + MOUTH_NOSE_V * vv) * ks
        return float(nd[0]), float(nd[1])

    def _cheeks_pin(self, maps, tile, src, dst, base, rel):
        """The pin dial on warp=cheeks (2026-09-14). The Bézier lands the
        tile by landmarks; the pin is measured on the AFFINE landing of the
        same tile — the law's own match, same patch, thresholds and
        take-rate — and the maps are translated by it once, nudge included.
        The 09-13 match through the curved maps refused on most frames
        (work 0–6 of 96: the strip's curved top edge left empty pixels in
        the nose patch), so warp=cheeks painted by landmarks alone while the
        dial read nose-pixel. lip = pin_upper_lip's translation; nose+lip
        scales a card, not a strip (landmarks). HOUSE_MOUTH_CHEEKS_PIN=0 =
        landmarks. Returns (maps', dst', placement)."""
        Maff = warp_affine_2x3(src, dst, scale=MOUTH_SCALE)
        rec = self.lib.tile_head(rel) or {}
        tbox = rec.get("mouth_in_tile")
        pdx = pdy = ndx = ndy = 0.0
        ncc = 0.0
        took = False
        how = "landmarks"
        if not MOUTH_CHEEKS_PIN:
            how = "landmarks (HOUSE_MOUTH_CHEEKS_PIN=0)"
        elif MOUTH_PIN == "nose-pixel":
            pdx, pdy, ncc = self._pin_shift(Maff, tile, src, dst, base, rel)
            took = ncc >= 0.90
            how = "pixel"
            if took or MOUTH_NUDGE_ALL:
                ndx, ndy = self._nudge_px(dst)
        elif MOUTH_PIN == "lip":
            _M, (pdx, pdy) = pin_upper_lip(Maff, tbox, dst)
            took = bool(pdx or pdy)
            how = "lip"
        elif MOUTH_PIN == "nose+lip":
            how = "landmarks (nose+lip scales a card, not a strip)"
        self._pin_dbg = {"ncc": round(float(ncc), 3), "took": bool(took),
                         "pixel": [round(float(pdx), 1), round(float(pdy), 1)],
                         "frame": int(getattr(self, "i", -1)), "path": "cheeks", "how": how}
        sx, sy = pdx + ndx, pdy + ndy
        if sx or sy:
            maps = shift_maps(maps, sx, sy)
            dst = dict(dst)
            dst["nose"] = np.asarray(dst["nose"], dtype=np.float64).ravel()[:2] + np.array([sx, sy])
        if MOUTH_CHIN_PIN and MOUTH_PIN == "nose-pixel" and MOUTH_CHEEKS_PIN and maps is not None:
            warped, box = self._remap_in_box(tile, maps)
            st, cdbg = (self._chin_stretch(rel, src, dst, base, warped, lambda p: self._landed(maps, None, p),
                                           tile=tile, cheeks=True, box=box)
                        if warped is not None else (None, {"how": "nothing maps"}))
            if st is not None:
                maps = stretch_maps_axis(maps, st[0], st[1], st[2])
            self._pin_dbg["chin"] = cdbg
        return maps, dst, self._placement_maps(maps, tbox, dst)

    def _cheeks_maps(self, src, dst, rel, tile, base):
        """warp=cheeks for this frame × tile: the frozen dest solve, the
        tile's maps on it, the pin — every step cached by dest-frame identity
        (poly_cache cheeks_solve_cached / cheeks_maps_cached, 2026-09-14,
        the User: "freeze those coefficients in cache"). A hit is the pinned maps
        back from their ROI plus the pin record; a miss builds them (7–8 ms).
        Returns (maps, dst') or (None, dst) when the tile refuses this frame."""
        ver = self._loop_ver()
        pin_key = (MOUTH_PIN if MOUTH_CHEEKS_PIN else "off",
                   round(float(MOUTH_NOSE_U), 3), round(float(MOUTH_NOSE_V), 3))
        if MOUTH_CHIN_PIN:
            pin_key = pin_key + ("chin-rest5", MOUTH_CHIN_PIN_LO, MOUTH_CHIN_PIN_HI) + (
                ("still-only", MOUTH_CHIN_TALK_OPEN) if MOUTH_CHIN_STILL_ONLY else ())
        nose0 = np.asarray(dst["nose"], dtype=np.float64).ravel()[:2].copy()

        def build():
            sol = cheeks_solve_cached(self.key, int(self.i), ver, dst, paint_wh(), MOUTH_SCALE)
            maps = cheeks_maps_from_solve(sol, src, dst) if sol is not None else None
            if maps is None:
                return None, None
            maps, dst2, place = self._cheeks_pin(maps, tile, src, dst, base, rel)
            n2 = np.asarray(dst2["nose"], dtype=np.float64).ravel()[:2]
            return maps, {"delta": (float(n2[0] - nose0[0]), float(n2[1] - nose0[1])),
                          "pin": dict(self._pin_dbg or {}), "place": place}

        self._ahead_plan()
        maps, extra = cheeks_maps_cached(
            loop_id=self.key, frame_i=int(self.i), version=ver, rel=rel, dst=dst,
            wh=paint_wh(), scale=MOUTH_SCALE, pin_key=pin_key,
            tile_hw=tile.shape[:2], build=build, tag=getattr(self, "_pl_class", None))
        if maps is None:
            return None, dst
        if extra:
            d = extra.get("delta") or (0.0, 0.0)
            if d[0] or d[1]:
                dst = dict(dst)
                dst["nose"] = nose0 + np.array([float(d[0]), float(d[1])])
            pin = dict(extra.get("pin") or {})
            pin["frame"] = int(getattr(self, "i", -1))
            self._pin_dbg = pin
            self._place_dbg = extra.get("place")
        return maps, dst

    def _ahead_plan(self) -> None:
        """THE MAPS RUN AHEAD (the User 2026-10-13, "pull from existing map ... some frames in advance"): once a film
        frame, hand lib/maps_ahead the next frames and the class the sheet holds at each of their two semiframes, so
        their maps are in memory before this paint reaches them. Reads only; never builds or changes a map."""
        stage = _MAPS_AHEAD.get("stage")
        if stage is None:
            return
        try:
            mark = (self.key, int(self.i))
            if getattr(self, "_ahead_mark", None) == mark:
                return
            self._ahead_mark = mark
            n = int(self.lib.head_track_len(self.key) or 0)
            if n <= 0:
                return
            now = time.time()
            fs = 1.0 / float(CLIP_FPS)
            out, seen = [], set()
            for j in range(1, maps_ahead.frames_ahead() + 1):
                f = (int(self.i) + j) % n
                for t in (now + j * fs, now + (j + 0.5) * fs):
                    tag = self._class_at(t)
                    if tag is not None and (f, tag) not in seen:
                        seen.add((f, tag))
                        out.append((f, tag))
            stage.plan(self.key, out)
        except Exception:  # noqa: BLE001 - the ahead stage never breaks a paint
            pass

    def _class_at(self, t: float) -> str | None:
        """The class the paint will look up at wall time t (Mouth.viseme's rule, read ahead): a held class, else the
        sheet's row at t, else rest."""
        m = self.mouth
        if m.force_class is not None:
            return viseme_of(m.force_class)
        if m.force is not None:
            return None
        v = viseme_at(m.frames, t, m.STALE_S) if m.frames else None
        k = viseme_of(v) if v is not None else None
        return k or "rest"

    def _placement_maps(self, maps, tbox, dst) -> dict | None:
        """_placement for a map warp: the dest pixels whose source lies in
        the tile's mouth box are where the tile's mouth landed (the drill's
        meter read nothing under warp=cheeks before 2026-09-14)."""
        try:
            if maps is None or tbox is None or dst is None or dst.get("mouth") is None:
                return None
            bx, by, bw, bh = [float(v) for v in list(tbox)[:4]]
            mx, my = maps[0], maps[1]
            sel = (mx >= bx) & (mx < bx + bw) & (my >= by) & (my < by + bh)
            ys, xs = np.nonzero(sel)
            if xs.size < 4:
                return None
            lo = np.array([float(xs.min()), float(ys.min())])
            hi = np.array([float(xs.max()) + 1.0, float(ys.max()) + 1.0])
            mx_, my_, mw, mh = [float(v) for v in list(dst["mouth"])[:4]]
            s = float(dst.get("s") or 100.0)
            dx = float((lo[0] + hi[0]) / 2.0 - (mx_ + mw / 2.0))
            dy = float((lo[1] + hi[1]) / 2.0 - (my_ + mh / 2.0))
            wr = float(hi[0] - lo[0]) / max(2.0, mw)
            return {"dx": round(dx, 1), "dy": round(dy, 1), "dx_s": round(dx / s, 3), "dy_s": round(dy / s, 3), "w_ratio": round(wr, 3)}
        except Exception:  # noqa: BLE001
            return None

    def _placement(self, M, tbox, dst) -> dict | None:
        """Where the tile's mouth LANDED against the film's own mouth box, in
        px and in units of head size s: the placement meter the drill logs
        (2026-09-13). A paste that does nothing has no quad and no score."""
        try:
            if M is None or tbox is None or dst is None or dst.get("mouth") is None:
                return None
            q = mouth_quad(tbox, M)
            lo, hi = q.min(axis=0), q.max(axis=0)
            mx, my, mw, mh = [float(v) for v in list(dst["mouth"])[:4]]
            s = float(dst.get("s") or 100.0)
            dx = float((lo[0] + hi[0]) / 2.0 - (mx + mw / 2.0))
            dy = float((lo[1] + hi[1]) / 2.0 - (my + mh / 2.0))
            wr = float(hi[0] - lo[0]) / max(2.0, mw)
            return {"dx": round(dx, 1), "dy": round(dy, 1), "dx_s": round(dx / s, 3), "dy_s": round(dy / s, 3), "w_ratio": round(wr, 3)}
        except Exception:  # noqa: BLE001
            return None

    def _neck_cut_alpha(self, warped, dst, rec, M, path, kw, kh):
        """square_alpha × (above the landed jaw line) × (inside the face half-width)."""
        from pose_mouth import square_alpha
        from head_frame import fit_arch, inpaint_plane
        plane = inpaint_plane(dst, kw, kh, MOUTH_SCALE)
        arch = fit_arch(dst, kw, kh, MOUTH_SCALE)
        a = square_alpha(warped, dst, plane=plane, arch=arch, nose_keep=(path != "affine"))
        H_, W_ = a.shape[:2]
        s_ = float(dst.get("s") or 100.0) * MOUTH_SCALE
        nose = np.asarray(dst["nose"], dtype=np.float64).ravel()[:2]
        v = np.asarray(dst["v"], dtype=np.float64).ravel()[:2]; vh = v / max(1e-6, float(np.linalg.norm(v)))
        u = np.asarray(dst["u"], dtype=np.float64).ravel()[:2]; uh = u / max(1e-6, float(np.linalg.norm(u)))
        # ONLY WHERE THE SQUARE PAINTS (round 11, the .101 lips, 2026-10-12): outside a's nonzero extent the product
        # is 0 whatever the jaw and side terms are, so they are worked out on that window alone - the same numbers,
        # a fifth of the pixels (the full-frame grid was ~10 % of a speaking paint on .101). lib/fast_reduce.ON.
        win = None
        if fast_reduce.ON and getattr(a, "ndim", 0) == 2:
            win = fast_reduce.nonzero_extent((a != 0).view(np.uint8))
            if win is None:
                return np.zeros((H_, W_), np.float32)
        if win is not None:
            yy, xx = np.mgrid[win[0]:win[1] + 1, win[2]:win[3] + 1].astype(np.float32)
        else:
            yy, xx = np.mgrid[0:H_, 0:W_].astype(np.float32)
        px, py = xx - float(nose[0]), yy - float(nose[1])
        t_v = px * vh[0] + py * vh[1]
        t_u = px * uh[0] + py * uh[1]
        chin = rec.get("chin") if rec else None
        if chin is not None and M is not None:
            lc = np.asarray(M, dtype=np.float64) @ np.array([float(chin[0]), float(chin[1]), 1.0])
            t_chin = float(np.dot(lc - nose, vh))
        else:
            t_chin = float(np.dot(np.asarray(dst["chin"], dtype=np.float64).ravel()[:2] - nose, vh))
        feather = max(2.0, 0.06 * s_)
        jaw = np.clip(1.0 - (t_v - t_chin - MOUTH_NECK_MARGIN * s_) / feather, 0.0, 1.0)
        side = np.clip(1.0 - (np.abs(t_u) - MOUTH_FACE_HALF * s_) / feather, 0.0, 1.0)
        if win is not None:
            out = np.zeros((H_, W_), np.float32)
            out[win[0]:win[1] + 1, win[2]:win[3] + 1] = (a[win[0]:win[1] + 1, win[2]:win[3] + 1] * jaw * side
                                                       ).astype(np.float32)
            return out
        return (a * jaw * side).astype(np.float32)

    def _follow_pts(self):
        """Her own points on this film frame, (kind, clip, frame, Nx2): the clip's lip marks where it has them
        (corners, lip line, nose, chin), else nose, nose+u, nose+v from its head track. What a dissolve's
        outgoing picture is carried by; None = nothing to carry with."""
        try:
            i = int(self.i)
            row = self.lib.rows.get(self.key) or {}
            lm = self.lib.lipmarks(str(row.get("file") or self.key))
            fr = (lm or {}).get("frames") or []
            if lm and 0 <= i < len(fr) and fr[i]:
                pts = MP.anchor_points(fr[i], scale=paint_wh()[0] / float((lm.get("wh") or [paint_wh()[0]])[0]))
                if pts is not None:
                    return ("lips%d" % len(pts), self.key, i, pts)
            h = unpack_head(self.lib.head(self.key, i))
            if h is None:
                return None
            n = np.asarray(h["nose"], dtype=np.float64).ravel()[:2]
            u = np.asarray(h["u"], dtype=np.float64).ravel()[:2]
            v = np.asarray(h["v"], dtype=np.float64).ravel()[:2]
            return ("head", self.key, i, np.vstack([n, n + u, n + v]))
        except Exception:  # noqa: BLE001  never block a paste on this
            return None

    _carry = staticmethod(MP.carry)

    def _xfade(self, rel, warped, a):
        """Cross-dissolve on a tile switch (xfade_ms). Returns (warped, a) to
        paste. The outgoing picture is what was on the glass last frame — a
        switch mid-dissolve fades from the mix, never pops. Off at 0."""
        if getattr(self, "_xf_bypass", False):
            return warped, a            # a steady paint for the finished-patch store: the wrapper blends the dissolve
        now = time.time()
        pts = self._follow_pts() if XFADE_FOLLOW else None
        if MOUTH_XFADE_MS <= 0.0:
            self._xf_now = {"rel": rel, "warped": warped, "a": a, "t": now, "pts": pts}
            self._xf_from = None
            return warped, a
        prev = self._xf_now
        if prev is not None and prev.get("rel") != rel and (now - float(prev.get("t") or now)) < 0.5:
            self._xf_from = (prev["warped"], prev["a"])
            self._xf_from_pts = prev.get("pts")
            self._xf_t0 = now
        elif prev is None or (now - float(prev.get("t") or now)) >= 0.5:
            self._xf_from = None      # nothing on the glass to fade from
        out_w, out_a = warped, a
        if self._xf_from is not None and XFADE_FOLLOW and pts is not None:
            # the outgoing picture travels with her head (HOUSE_MOUTH_FAST_TRACK): it was landed on an earlier frame
            self._xf_from = self._carry(getattr(self, "_xf_from_pts", None), pts, self._xf_from[0], self._xf_from[1])
            self._xf_from_pts = pts
        if self._xf_from is not None:
            # a frame at any speed (2026-09-21): the dissolve is a paint-time
            # duration, so at rate R it must last 1/R longer in wall time to
            # cover the same film frames
            t = (now - self._xf_t0) / (MOUTH_XFADE_MS / 1000.0 / max(0.05, float(PLAY_RATE)))
            if t >= 1.0:
                self._xf_from = None
            else:
                out_w, out_a = xfade_mix(self._xf_from[0], self._xf_from[1], warped, a, xfade_ease(t))
        self._xf_now = {"rel": rel, "warped": out_w, "a": out_a, "t": now, "pts": pts}
        return out_w, out_a

    def _bow_frame(self) -> bool:
        """this frame is named by the clip's bow map (a bow band or "filmed")"""
        bm = self.lib.bow_map(self.key)
        return bool(bm) and bool(bm[min(max(int(self.i), 0), len(bm) - 1)])

    def _fade_out(self, base: np.ndarray) -> np.ndarray:
        """No tile this frame: dissolve the last pasted tile out over the
        FILMED frame (no hole — a hole under a fading tile is the pale
        patch). xfade_ms 0 = a plain cut, as before."""
        prev = self._xf_now
        if prev is None or MOUTH_XFADE_MS <= 0.0:
            self._xf_now = None
            self._xf_from = None
            return base
        now = time.time()
        t0 = float(prev.get("t") or now)
        if prev.get("out_t0") is None:
            prev["out_t0"] = now
            if now - t0 > 0.5:
                self._xf_now = None
                return base
        t = (now - float(prev["out_t0"])) / (MOUTH_XFADE_MS / 1000.0)
        if t >= 1.0:
            self._xf_now = None
            self._xf_from = None
            return base
        if prev.get("warped") is None:
            # a kept paint holds no landed tile (lib/mouth_patch): the outgoing tile is painted on THIS frame from the
            # store and fades over the filmed frame - pixels fade, the tile follows the pin (2026-10-25)
            pair = self.lib.playlist_tile(prev.get("rel")) if prev.get("rel") else None
            box = self.lib.box(self.key, self.i)
            _skip = self.skip
            f_out = (self._kept_frame_as(base, box, pair[0], pair[1], prev["rel"], prev.get("klass"), prev.get("cell"))
                     if (pair and box) else None)
            self.skip = _skip
            if f_out is None:
                self._xf_now = None
                return base
            return mouth_patch.lerp_frames(base, f_out, base, xfade_ease(t))
        if XFADE_FOLLOW:
            pts = self._follow_pts()
            if pts is not None:
                prev["warped"], prev["a"] = self._carry(prev.get("pts"), pts, prev["warped"], prev["a"])
                prev["pts"] = pts
        a = np.asarray(prev["a"], dtype=np.float32) * (1.0 - xfade_ease(t))
        if a.ndim == 2:
            a = a[..., None]
        return (base.astype(np.float32) * (1.0 - a)
                + prev["warped"].astype(np.float32) * a).astype(np.uint8)

    def _landed(self, maps, Maff, p) -> "np.ndarray | None":
        """Where tile point p lands in the frame under this landing (affine: M p; maps: the dest pixel
        whose source is nearest p)."""
        p = np.asarray(p, dtype=np.float64).ravel()[:2]
        if maps is not None:
            mx, my = maps[0], maps[1]
            # IN THE MAPS' OWN BOX (2026-10-27, Crissa desk v2 at 1280x720: a whole-frame distance per point was 23 % of
            # the paint). Outside crop_maps' box every pixel is the -1 sentinel, so the box's first minimum is the frame's
            # unless the sentinel ties or beats it; then the whole frame is searched as before. Same answer either way.
            if MOUTH_LANDED_ROI:
                memo = getattr(self, "_landed_box", None)
                if memo is None or memo[0] is not mx or memo[1] is not my:
                    roi = _hf_mod.crop_maps((mx, my))
                    box = None if roi is None else (roi[0], roi[1], roi[0] + roi[2].shape[1], roi[1] + roi[2].shape[0])
                    memo = self._landed_box = (mx, my, box)
                box = memo[2]
                if box is not None:
                    x0, y0, x1, y1 = box
                    d = (mx[y0:y1, x0:x1] - p[0]) ** 2 + (my[y0:y1, x0:x1] - p[1]) ** 2
                    j = int(np.argmin(d))
                    dj = d.flat[j]
                    ds = (-1.0 - p[0]) ** 2 + (-1.0 - p[1]) ** 2
                    if np.isfinite(dj) and dj < ds:
                        if dj > 4.0:
                            return None
                        yy, xx = np.unravel_index(j, d.shape)
                        return np.array([float(xx + x0), float(yy + y0)])
            d = (mx - p[0]) ** 2 + (my - p[1]) ** 2
            j = int(np.argmin(d))
            if not np.isfinite(d.flat[j]) or d.flat[j] > 4.0:
                return None
            yy, xx = np.unravel_index(j, d.shape)
            return np.array([float(xx), float(yy)])
        if Maff is not None:
            return MP.apply(Maff, p)[0]
        return None

    def _lips_and_pins(self, maps, Maff, src, dst, path, rel):
        """LIPS ON A TURN, then THE CELL'S PIN (2026-09-29). Returns (maps, Maff, dst, path, shift|None).
        The debug record carries where the tile's lip-line centre lands under the nose landing and under
        the lips, both before the pin - the measure the User asked for."""
        dbg = {"frame": int(getattr(self, "i", -1)), "cell": getattr(self, "_pl_cell", None)}
        self._lip_mask = None
        self._lip_turned = False
        self._lip_hole = None
        self._lip_lips = None
        yaw = getattr(self, "_pl_yaw", None)          # the row's angle: the same one that chose the tile
        if yaw is None:
            try:
                yaw = float(dst["pose"][1])
            except (KeyError, TypeError, ValueError, IndexError):
                pass
        dbg["yaw"] = None if yaw is None else round(yaw, 2)
        tp = self.lib.tile_lips(rel) if (MOUTH_PIN_CELLS or MOUTH_TURN_PLACE == "lips") else None
        ta = self.lib.tile_anchors(rel) if tp is not None else None
        da = None
        dp = None
        if tp is not None:
            row = self.lib.rows.get(self.key) or {}
            lm = self.lib.lipmarks(str(row.get("file") or self.key))
            fr = (lm or {}).get("frames") or []
            if lm and 0 <= int(self.i) < len(fr):
                k = paint_wh()[0] / float((lm.get("wh") or [paint_wh()[0]])[0])
                dp = MP.lip_points(fr[int(self.i)], scale=k)
                da = MP.anchor_points(fr[int(self.i)], scale=k)
        if tp is not None and dp is not None:
            here = self._landed(maps, Maff, tp[2])
            dbg["lipc_nose"] = None if here is None else [round(float(v), 1) for v in here]
            dbg["lipc_her"] = [round(float(v), 1) for v in dp[2]]
            # THE CHIN, MEASURED (the User 2026-10-25 night, Bash: "double chin when his head lifts ... maybe pin to chin
            # also?"): where the tile's own chin lands under the same landing, against hers, and the gap along her
            # nose->chin axis (+ = the tile's chin lands BELOW hers). A measurement only: nothing pins to it.
            if ta is not None and da is not None and len(ta) == 5 and len(da) == 5:
                _tc = self._landed(maps, Maff, ta[4])
                if _tc is not None:
                    _ax = np.asarray(da[4], dtype=np.float64) - np.asarray(da[3], dtype=np.float64)
                    _ax = _ax / max(1e-6, float(np.linalg.norm(_ax)))
                    dbg["chin_her"] = [round(float(v), 1) for v in da[4]]
                    dbg["chin_tile"] = [round(float(v), 1) for v in _tc]
                    dbg["chin_below"] = round(float(np.dot(np.asarray(_tc, dtype=np.float64) - np.asarray(da[4], dtype=np.float64), _ax)), 2)
            _ks, _kfrom = None, "lips"
            if MOUTH_LIPS_SCALE == "take":
                _ts = self.lib.tile_take_scale(rel)
                if _ts:
                    _ks, _kfrom = float(dst["s"]) / _ts, "take"
            if _ks is None and MOUTH_LIPS_SCALE in ("take", "head"):
                try:
                    _ks = float(dst["s"]) / float(src["s"])
                    _kfrom = "head"
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    _ks = None
            if ta is not None and da is not None and len(ta) == 5 and len(da) == 5:
                # lips weigh twice: the mouth is what must land; nose and chin hold the angle through a turn
                S = MP.similarity(np.vstack([ta[:3], ta]), np.vstack([da[:3], da]), scale=_ks)
                if S is not None:
                    S = MP.pin_to(S, ta[2], da[2])     # the lip line lands on hers; nose + chin only turn it
                if S is not None and MOUTH_LIPS_JAW:
                    S = MP.jaw_stretch(S, ta, da)
                    dbg["jaw"] = True
            else:
                S = MP.similarity(tp, dp, scale=_ks)
            if S is not None:
                dbg["scale"] = round(float(np.hypot(S[0, 0], S[1, 0])), 4)
                dbg["scale_from"] = _kfrom if _ks else "lips"
                dbg["lipc_lips"] = [round(float(v), 1) for v in MP.apply(S, tp[2])[0]]
                if (MOUTH_TURN_PLACE == "lips" and not getattr(self, "_tri_mode", False)
                        and ((MOUTH_LIPS_ALL and bool((lm or {}).get("land")))
                             or (yaw is not None and abs(yaw) >= TURN_FRONT_MAX))):
                    # THE SAME RULE UNDER THE LIPS LANDING (the User 2026-10-03, seen on his bench with the warp dial
                    # on plain cheeks): a tile cut from THIS frame of THIS film goes back at its crop origin, unbent;
                    # the oval mask around it then covers identical pixels and the painted frame is the raw frame.
                    _own = self._own_frame_origin(rel)
                    if _own is not None:
                        S = np.array([[_own[2], 0.0, float(_own[0])], [0.0, _own[2], float(_own[1])]], dtype=np.float64)
                        dbg["scale"], dbg["scale_from"] = round(float(_own[2]), 4), "own"
                        dbg["placed"] = "own"
                    else:
                        dbg["placed"] = "lips"
                    maps, Maff, path = None, S, "lips"
                    # THE MASK IS FOR A TURN (the User 2026-09-30, "mouth circles" on lila-fl-chin's front frames): a
                    # frame facing us is landed by the lips but keeps the tuned square tile; the face/mouth mask and
                    # the skin match only past TURN_FRONT_MAX, where a tile's background and jaw stop matching hers
                    # ... and the FACE mask on every lips frame (the User 2026-09-30, lila-fl-chin turning under
                    # 12 deg: the square's corner carried the tile's wall past her cheek). Facing us: face mask only,
                    # the square look kept inside her face; turned: + the mouth region and her skin.
                    _turned = yaw is not None and abs(yaw) >= TURN_FRONT_MAX
                    self._lip_turned = _turned
                    if MOUTH_LIPS_MASK:
                        th = self.lib.tile_hull(rel)
                        fr_i = fr[int(self.i)] if 0 <= int(self.i) < len(fr) else None
                        dh = MP.face_polys(fr_i, scale=k)
                        if th and dh:
                            _s = float(dst.get("s") or 100.0)
                            _reg = None
                            if _turned and MOUTH_LIPS_REGION_S > 0 and fr_i and fr_i.get("lips_outer"):
                                _lp = [np.asarray(fr_i["lips_outer"], dtype=np.float64) * k]
                                if len(th) >= 2:          # the tile's own lips as landed (an open AH reaches below hers)
                                    _lp.append(MP.apply(S, th[-1]))
                                _reg = MP.mouth_region(paint_wh()[::-1], _lp, MOUTH_LIPS_REGION_S * _s)
                            self._lip_mask = MP.face_mask(paint_wh()[::-1], [MP.apply(S, q) for q in th], dh,
                                                          MOUTH_LIPS_FEATHER_S * _s, MOUTH_LIPS_GROW_S * _s,
                                                          region=_reg)
                            dbg["region"] = _reg is not None
                            self._lip_hole = MP.fill(paint_wh()[::-1], dh)
                            if fr_i and fr_i.get("lips_outer"):
                                self._lip_lips = MP.fill(paint_wh()[::-1], np.asarray(fr_i["lips_outer"], dtype=np.float64) * k)
                        dbg["mask"] = self._lip_mask is not None
        dbg.setdefault("placed", path)
        shift = None
        if MOUTH_PIN_CELLS and dbg["cell"] and dbg.get("placed") != "own":   # its own frame takes no seating
            rows = ((self.lib.pins().get("cells") or {}).get(dbg["cell"]) or {}).get("rows")
            uv = MP.pin_at(rows, yaw)
            if uv is not None and dst.get("u") is not None and dst.get("v") is not None:
                shift = MP.nudge_px(uv[0], uv[1], dst["u"], dst["v"], float(dst.get("s") or MP.NUDGE_REF_S))
                sx, sy = float(shift[0]), float(shift[1])
                if maps is not None:
                    maps = shift_maps(maps, sx, sy)
                if Maff is not None:
                    Maff = np.array(Maff, dtype=np.float64).copy()
                    Maff[:, 2] += (sx, sy)
                dst = dict(dst)
                dst["nose"] = np.asarray(dst["nose"], dtype=np.float64).ravel()[:2] + np.array([sx, sy])
                dbg["pin"] = [round(float(uv[0]), 2), round(float(uv[1]), 2)]
                dbg["pin_px"] = [round(sx, 1), round(sy, 1)]
        self._lip_dbg = dbg
        return maps, Maff, dst, path, shift

    def _turn_cfg(self) -> dict:
        """<pose-dir>/mouth-turn.json of the face on screen (mtime-cached) over TURN_DEFAULTS: a face says only what it
        wants otherwise. The defaults are the 2026-10-03 rules (the User: "if you can automate that is perfect for
        intake"), so a face Intake makes lands its turns with no config written for it."""
        p = bank_dir(self.key) / "mouth-turn.json"
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            return self._neck_cut_rules(dict(TURN_DEFAULTS))
        got = self.__dict__.get("_turn_cfg_c")
        if got is None or got[0] != (str(p), mt):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                d = {}
            got = ((str(p), mt), {**TURN_DEFAULTS, **d} if isinstance(d, dict) else dict(TURN_DEFAULTS))
            self._turn_cfg_c = got
        return self._neck_cut_rules(got[1])

    @staticmethod
    def _neck_cut_rules(cfg: dict) -> dict:
        """The neck cut tick's jaw + chin cut (NECK_CUT_JAW) for a face that sets neither key itself."""
        if not (MOUTH_NECK_CUT and NECK_CUT_JAW) or all(k in cfg for k in NECK_CUT_JAW_RULES):
            return cfg
        return {**NECK_CUT_JAW_RULES, **cfg}

    def _tri_lib_angle(self, side: str) -> float | None:
        """The 45 library's own filmed angle on this side (median |yaw| of the rows its tiles were cut from): the
        face's own take for that side when it has one (TURN_LIBRARIES_OTHER for the left), else the base take that
        the mirror would use. None when the face has no 45 library. Cached per face and side."""
        cache = self.__dict__.setdefault("_tri_lib_angle_c", {})
        face = face_of(self.key) or ""
        if (face, side) not in cache:
            lib = TURN_LIBRARIES.get("45")
            if side == "left" and TURN_LIBRARIES_OTHER.get("45"):
                k = self.lib._turn_row(TURN_LIBRARIES_OTHER["45"], self.key)
                pl = self.lib.playlist(k) if k else None
            else:
                pl = self.lib.turn_playlist("45", self.key) if lib else None
            rows = (pl or {}).get("frames") or []
            ys = []
            for hit in ((pl or {}).get("index") or {}).values():
                for relp in as_tiles(hit):
                    try:
                        n = int(Path(str(relp)).name.split("_f", 1)[1].split("_", 1)[0])
                        ys.append(abs(float(rows[n]["yaw"])))
                    except (IndexError, KeyError, TypeError, ValueError):
                        pass
            cache[(face, side)] = float(np.median(ys)) if ys else None
        return cache[(face, side)]

    def _tile_yaw(self, rel: str) -> float | None:
        """|yaw| of the row the tile's PNG was cut from (its film's playlist by stem, the frame in its name), cached
        per tile; None when the stem is not a clip with rows."""
        cache = self.__dict__.setdefault("_tile_yaw_c", {})
        if rel not in cache:
            got = None
            try:
                m = re.search(r"^(.*)_f(\d+)_", Path(str(rel)).name)
                if m:
                    rows = (self.lib.playlist(m.group(1)) or {}).get("frames") or []
                    y = rows[int(m.group(2))].get("yaw") if int(m.group(2)) < len(rows) and isinstance(rows[int(m.group(2))], dict) else None
                    got = abs(float(y)) if y is not None else None
            except (AttributeError, TypeError, ValueError, IndexError):
                got = None
            cache[rel] = got
        return cache[rel]

    def _tri_from(self) -> float:
        try:
            cfg = self._turn_cfg()
            yaw = getattr(self, "_pl_yaw", None)
            side = "left" if (yaw is not None and float(yaw) > 0) else "right"
            # HER LEFT MAY HAND OVER LATER (the User 2026-10-03, frame 83 at +21 deg: "try stretching front tiles a bit
            # more"): `from_left` is the hand-over for yaw > 0 (a side with no takes of its own keeps the front tile,
            # bent, further into the turn before the mirror takes it); absent = `from` on both sides.
            got = cfg.get("from_left") if (side == "left" and cfg.get("from_left") is not None) else cfg.get("from")
            # HER RIGHT TOO (the User 2026-10-25, the figure-8: "if right looks like left it may be a good flex"): her
            # right-turn takes are filmed deep, so a half-turned head under `from` wore a turn tile far from its own
            # angle and landed small and off. `from_right` is the same hand-over for yaw < 0; absent = `from`, as today.
            if side == "right" and cfg.get("from_right") is not None:
                got = cfg.get("from_right")
            if got == "nearest":
                # THE NEAREST TAKE WINS (the User 2026-10-03, "be great if we got best of both worlds"): the front tile
                # is re-projected from 0 deg, the 45 from its own filmed angle, so the hand-over sits halfway - 29.5 on
                # a face whose 45s are at 59 (his thirty by eye), sixteen / 21.5 on one whose 45s sit at 32 / 43 (his twenty).
                ang = self._tri_lib_angle(side)
                if ang:
                    return float(min(40.0, max(10.0, ang / 2.0)))
                got = None
            return float(got)
        except (TypeError, ValueError):
            # Under the bend a turn tile is re-projected to ANY angle, so it takes over where this face's own front
            # loops stop (the range the front landing is used and accepted on) - no gap left for a front tile whose
            # pin does not take on a turning face.
            if self._bend_on():
                return float(self._front_to())
            return float(TURN_FRONT_MAX)

    def _front_to(self) -> float:
        """How far a head may turn and still take the FRONT landing: config `front_to`, else the largest turn any of
        this face's own loops reaches - the range the Bezier landing and its front tiles are actually used and
        accepted on. Past it, and short of the hand-over to the triangle, neither landing is at home."""
        v = self._turn_cfg().get("front_to")
        if isinstance(v, (int, float)):
            return float(v)
        cache = self.__dict__.setdefault("_front_to_c", {})
        face = face_of(self.key) or ""
        if face not in cache:
            top = 0.0
            for k in list(self.lib.rows):
                if (face_of(k) or "") != face or self.seq.graph.role(k) != "loop":
                    continue
                for r in ((self.lib.playlist(k) or {}).get("frames") or []):
                    try:
                        top = max(top, abs(float(r.get("yaw"))))
                    except (TypeError, ValueError, AttributeError):
                        pass
            cache[face] = top
        return cache[face]

    def _tri_weight(self) -> float:
        cfg = self._turn_cfg()
        try:
            blend = float(cfg.get("blend") or 0.0)
        except (TypeError, ValueError):
            blend = 0.0
        return MP.turn_weight(getattr(self, "_pl_yaw", None), self._tri_from(), blend)

    def _tri_p90(self) -> float | None:
        """Where the 90 tiles take over from the 45s: config `p90_from`, else the 45 library's own tile angle (the
        median of the rows its tiles were cut from). None = no 90 library for this face."""
        cfg = self._turn_cfg()
        if isinstance(cfg.get("p90_from"), (int, float)):
            return float(cfg["p90_from"])
        cache = self.__dict__.setdefault("_tri_p90_c", {})
        face = face_of(self.key) or ""
        if face not in cache:
            med = []
            for band in ("45", "90"):
                pl = self.lib.turn_playlist(band, self.key) or {}
                rows = pl.get("frames") or []
                ys = []
                for hit in (pl.get("index") or {}).values():
                    for relp in as_tiles(hit):
                        try:
                            n = int(Path(str(relp)).name.split("_f", 1)[1].split("_", 1)[0])
                            ys.append(abs(float(rows[n]["yaw"])))
                        except (IndexError, KeyError, TypeError, ValueError):
                            pass
                med.append(float(np.median(ys)) if ys else None)
            # the 45 tiles carry the turn up to the angle they were themselves filmed at; past it the 90s (the User
            # 2026-10-02, by eye: the 90s too early look wrong, half-way between the two libraries came too late)
            cache[face] = med[0] if (med[0] is not None and med[1] is not None) else None
        return cache[face]

    def _tri_band(self, yaw, front: float | None = None) -> str | None:
        """The turn band under the triangle warp: its own hand-over angle, the 90s where the face has them, and the
        other side from the mirror when the face's config says so (`mirror_left`)."""
        p90 = self._tri_p90()
        # her other side: its own takes when the face has them (turn_source tries those first), else the mirror of
        # this side's - a triangle cannot be built on a front tile, so a side with no takes is mirrored by default
        mir = self._turn_cfg().get("mirror_left")
        return turn_band(yaw, bands=(("45", "90") if p90 is not None else None),
                         front=(self._tri_from() if front is None else front), p45=p90,
                         mirror=(bool(mir) if mir is not None else True))

    def turn_map(self, key: str | None = None) -> dict:
        """ANGLE MAP OF A CLIP (the User 2026-10-03, "circle those sections for angled ... group them by angle"): per
        frame the cell the live warp would land - "front", "45" / "90" on her own-take side, "45m" / "90m" = the other
        side (its own left take when the face has one, else the mirror) - with the hand-over angles it used, so the
        Composer can colour the strip and the scrub bar. Reads the playlist rows' yaw only; nothing is painted."""
        key = str(key or self.key or "")
        pl = self.lib.playlist(key) or {}
        rows = pl.get("frames") or []
        tri = bool(MOUTH_TURN_TRI and TURN_TRI_ON)
        keep_key, keep_yaw = self.key, getattr(self, "_pl_yaw", None)
        out, froms = [], {}
        try:
            self.key = key
            for fr in rows:
                yaw = fr.get("yaw") if isinstance(fr, dict) else None
                if yaw is None:
                    out.append(None); continue
                self._pl_yaw = float(yaw)
                side = "left" if float(yaw) > 0 else "right"
                if tri:
                    f0 = self._tri_from()
                    froms[side] = round(f0, 1)
                    b = self._tri_band(float(yaw))
                else:
                    f0 = float(TURN_FRONT_MAX); froms[side] = f0
                    b = turn_band(float(yaw), front=f0)
                out.append(b or "front")
        finally:
            self.key, self._pl_yaw = keep_key, keep_yaw
        p90 = self._tri_p90() if tri else None
        return {"ok": True, "key": key, "n": len(out), "bands": out, "from": froms, "p90": (round(p90, 1) if p90 else None),
                "warp": ("cheeks-tri" if tri else MOUTH_WARP), "own_left": bool(TURN_LIBRARIES_OTHER.get("45"))}

    def _tri_tile(self):
        """(tile picture, rel) the triangle lands on this frame: the angle-matched turn tile. Past the hand-over
        that is the tile already on the playhead; inside the blend it is fetched (the playhead still holds the
        loop's own front tile, which a triangle cannot be built on)."""
        yaw = getattr(self, "_pl_yaw", None)
        band = self._tri_band(yaw, front=0.0)
        if not band:
            return None, None
        hit, _lib, _mir = self.lib.turn_source(band, getattr(self, "_pl_class", None), self.key)
        tiles = as_tiles(hit)
        if not tiles:
            return None, None
        pair = self.lib.playlist_tile(tiles[0])
        return (pair[0], tiles[0]) if pair else (None, None)

    def _edge_stat(self, key, samples) -> tuple:
        """(nose, chin, upper lip): the MEDIAN distance, in head sizes, from the tracked point out to the real skin edge over
        every sample whose point lies on the outline's front. One steady number per film and per tile source take -
        a single frame's reading jumps by several pixels (measured), the median over a take does not. `samples`
        yields (picture, nose, chin, outline, head size in that picture's pixels, upper-lip point or None)."""
        cache = self.__dict__.setdefault("_tri_edge_c", {})
        if key not in cache:
            acc = ([], [], [])
            for img, nose, chin, outline, s_px, top in samples():
                uf = MP.front_dir(nose, chin, outline)
                if uf is None or not s_px:
                    continue
                tol = MOUTH_LIPS_GROW_S * s_px
                for n, p in enumerate((nose, chin, top)):
                    if p is not None and MP.front_weight(p, uf, outline, tol) >= 0.5:
                        acc[n].append(MP.edge_ahead(img, p, uf, s_px) / s_px)
            cache[key] = tuple(float(np.median(a)) if len(a) >= 3 else 0.0 for a in acc)
        return cache[key]

    def _film_edge_samples(self, key: str):
        row = self.lib.rows.get(key) or {}
        lm = self.lib.lipmarks(str(row.get("file") or key)) or {}
        fr = lm.get("frames") or []
        k = paint_wh()[0] / float((lm.get("wh") or [paint_wh()[0]])[0])
        arr = self.lib.frames(key) or []
        for i in range(min(len(fr), len(arr))):
            h = unpack_head(self.lib.head(key, i))
            if not fr[i] or h is None:
                continue
            a = MP.anchor_points(fr[i], scale=k)
            polys = MP.face_polys(fr[i], scale=k)
            if a is not None and len(a) == 5 and polys:
                top = np.asarray(fr[i]["top"], dtype=np.float64) * k if fr[i].get("top") is not None else None
                yield arr[i], a[3], a[4], polys[0], float(h.get("s") or 0.0), top

    def _take_edge_samples(self, pre: str):
        """Every tile cut from the source take `pre` that the turn libraries index."""
        seen = set()
        for lib in list(TURN_LIBRARIES.values()) + list(TURN_LIBRARIES_OTHER.values()):
            k = self.lib._turn_row(lib, self.key) if lib else None
            pl = self.lib.playlist(k) if k else None
            for hit in ((pl or {}).get("index") or {}).values():
                for relp in as_tiles(hit):
                    if relp in seen or not Path(str(relp)).name.startswith(pre + "_f"):
                        continue
                    seen.add(relp)
                    pair = self.lib.playlist_tile(relp)
                    a = self.lib.tile_anchors(relp)
                    polys = self.lib.tile_hull(relp)
                    ts = self.lib.tile_take_scale(relp)
                    if pair and a is not None and len(a) == 5 and polys and ts:
                        yield pair[0], a[3], a[4], polys[0], float(ts), self.lib.tile_mark(relp, "top")

    def _tri_edges(self, nose, chin, outline, s_px: float, stat: tuple) -> tuple:
        """(nose, chin) moved out toward the real skin edge by the take's median reading, as far as each point is on
        the outline's front this frame (mouth_pins.front_weight)."""
        uf = MP.front_dir(nose, chin, outline)
        if uf is None:
            return nose, chin
        tol = MOUTH_LIPS_GROW_S * s_px
        return tuple(p + MP.front_weight(p, uf, outline, tol) * m * s_px * uf for p, m in ((nose, stat[0]), (chin, stat[1])))

    def _tri_M(self, i: int, rel: str) -> dict | None:
        """The triangle of tile `rel` on film frame `i` of the clip on screen: nose tip, chin (moved to the real
        profile edge where they lie on it) and the near-cheek point, the same way on both sides. None when either
        side lacks the points."""
        row = self.lib.rows.get(self.key) or {}
        lm = self.lib.lipmarks(str(row.get("file") or self.key))
        fr = (lm or {}).get("frames") or []
        raw = unpack_head(self.lib.head(self.key, int(i)))
        if not lm or raw is None or not (0 <= int(i) < len(fr)) or not fr[int(i)]:
            return None
        k = paint_wh()[0] / float((lm.get("wh") or [paint_wh()[0]])[0])
        f = fr[int(i)]
        da = MP.anchor_points(f, scale=k)
        dh = MP.face_polys(f, scale=k)
        ta = self.lib.tile_anchors(rel)
        th = self.lib.tile_hull(rel)
        if da is None or ta is None or len(da) != 5 or len(ta) != 5 or not dh or not th:
            return None
        cd, side = MP.cheek_point(da[3], da[4], dh[0])
        ct, _ = MP.cheek_point(ta[3], ta[4], th[0], side=side)
        if cd is None or ct is None:
            return None
        dn, dc, tn, tc = da[3], da[4], ta[3], ta[4]
        s0 = float(raw.get("s") or 100.0)
        ts0 = float(self.lib.tile_take_scale(rel) or s0)
        edge = None
        # THE REAL PROFILE EDGE: her tracked nose tip and chin sit inside her real edge on a deep profile, and by more
        # than the tile's do, which left the tile's mouth and jaw a few pixels too far back ("sunken jaw", the User
        # 2026-10-02). Off: HOUSE_MOUTH_TURN_EDGE=0 or the face's mouth-turn.json {"front_edge": false}.
        if TURN_EDGE_ON and self._turn_cfg().get("front_edge") is not False:
            pre = Path(_unmirror(rel)[0]).name.split("_f", 1)[0]
            edge = (self._edge_stat(("film", self.key), lambda: self._film_edge_samples(self.key)),
                    self._edge_stat(("take", pre), lambda: self._take_edge_samples(pre)))
            dn, dc = self._tri_edges(da[3], da[4], dh[0], s0, edge[0])
            tn, tc = self._tri_edges(ta[3], ta[4], th[0], ts0, edge[1])
        M = MP.tri_affine([tn, tc, ct], [dn, dc, cd])
        if M is None:
            return None
        return {"M": M, "da": da, "dh": dh, "ta": ta, "th": th, "f": f, "k": k, "raw": raw, "s": s0, "ts": ts0, "edge": edge}

    def _lip_ratio(self, i: int, rest_rel: str) -> tuple | None:
        """(along, across): HER closed outer lip's height and width on frame `i` over the take's REST tile's as the
        triangle lands it - how much too big or small the tile's mouth is drawn on this frame. Read on the rest tile
        because a speaking tile's lips are open and hers are not. Cached per frame and tile."""
        cache = self.__dict__.setdefault("_lip_ratio_c", {})
        key = (self.key, int(i), rest_rel)
        if key not in cache:
            out = None
            t = self._tri_M(i, rest_rel)
            # her lip's own two measures - upper-lip point to lower-lip point, corner to corner - against the same two
            # on the rest tile as landed. (The loop's extent along her nose->chin line was tried first and misread a
            # tilted lip: it drew the shallow-turn mouths a third too tall.)
            t_top, t_bot = self.lib.tile_mark(rest_rel, "top"), self.lib.tile_mark(rest_rel, "bot")
            f = t["f"] if t is not None else None
            if t is not None and t_top is not None and t_bot is not None and f.get("top") is not None and f.get("bot") is not None:
                D = lambda a, b: float(np.linalg.norm(np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)))
                L = lambda p: MP.apply(t["M"], p)[0]
                h_her = D(f["top"], f["bot"]) * t["k"]
                w_her = D(t["da"][0], t["da"][1])
                h_tile, w_tile = D(L(t_top), L(t_bot)), D(L(t["ta"][0]), L(t["ta"][1]))
                if h_tile > 1e-6 and w_tile > 1e-6:
                    out = (h_her / h_tile, w_her / w_tile)
            if len(cache) > 8192:
                cache.clear()
            cache[key] = out
        return cache[key]

    def _hole(self) -> str:
        """THE HOLE GOES TO AN ELLIPSE AT THE DEEPEST BOW (the User 2026-10-28, Crissa desk v2's bow: "at the deepest angle
        go to ellipse and it should be fine as bobbing lip dots likely"): the arch hole follows her lip and chin marks,
        and at a deep bow those marks bob frame to frame, so the arch smeared under her chin. A library's mouth-turn.json
        {"deep_hole": {"below": deg, "hole": "ellipse"}} cuts that hole on every frame whose playlist pitch is below
        `below`; above it, and with no such key, the dial (MOUTH_HOLE) as before. Kill HOUSE_MOUTH_DEEP_HOLE=0."""
        base = "arch" if MOUTH_HOLE == "arch" else "ellipse"
        if not MOUTH_DEEP_HOLE:
            return base
        try:
            dh = self._turn_cfg().get("deep_hole")
            if not isinstance(dh, dict) or dh.get("below") is None:
                return base
            frs = (self.lib.playlist(self.key) or {}).get("frames") or []
            if not frs:
                return base
            fr = frs[min(max(int(self.i), 0), len(frs) - 1)]
            pitch = fr.get("pitch") if isinstance(fr, dict) else None
            if pitch is not None and float(pitch) < float(dh["below"]):
                want = str(dh.get("hole") or "ellipse").strip().lower()
                return want if want in ("ellipse", "arch") else base
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            pass
        return base

    def _level_reading(self):
        """(weight, nudge, scale) of the face's HEAD-LEVEL reading for this frame's turn library (the User 2026-10-03:
        the 45 cell at 147-167 'looks perfect' at fwd 12 / down -10.5 / scale 0.72, and ONLY there - the rest of the
        cell, her head down 18-28 deg, was right as it was): mouth-turn.json {"level": {"<library>": {"pitch_above":
        -19, "fade": 3, "nudge": [fwd, down], "scale": k}}}. Weight 1 with the row's pitch above `pitch_above` (level),
        0 at `pitch_above - fade` and below (down), linear between; None when the library has no level reading."""
        try:
            lv = (self._turn_cfg().get("level") or {}).get(str(getattr(self, "_pl_cell", None) or "").split("/", 1)[0])
            if not isinstance(lv, dict):
                return None
            pl = self.lib.playlist(self.key) or {}
            frs = pl.get("frames") or []
            fr = frs[min(max(int(self.i), 0), len(frs) - 1)]
            pitch = float(fr["pitch"]) if isinstance(fr, dict) and fr.get("pitch") is not None else None
            if pitch is None:
                return None
            top = float(lv.get("pitch_above", -19.0)); fade = max(0.01, float(lv.get("fade", 3.0)))
            w = min(1.0, max(0.0, (pitch - (top - fade)) / fade))
            nd = lv.get("nudge"); sc = lv.get("scale")
            nd = (float(nd[0]), float(nd[1])) if isinstance(nd, (list, tuple)) and len(nd) == 2 else None
            sc = float(sc) if isinstance(sc, (int, float)) and 0.1 <= float(sc) <= 3.0 else None
            return w, nd, sc
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return None

    def _lip_size(self, band) -> tuple:
        """(along, across) to draw the tile's mouth at HER lip size on this frame (the User 2026-10-02: "make the
        lips smaller but the circle the same size ... the same size mouth/lips"): the median of _lip_ratio over this
        frame and its two neighbours either side, so one frame's marks cannot make the mouth pulse. (1, 1) when it
        cannot be read, or when mouth-turn.json says {"lip_size": false}."""
        if self._turn_cfg().get("lip_size") is not True or not band:
            # MARKS ARE FOR POSITION, NOT SIZE (the User 2026-10-03): the outer-lip height in the marks read +65 % between
            # head-down and head-level on one turn with her mouth unchanged. Size is the head similarity x one constant
            # (mouth-turn.json `scale`); a face must say {"lip_size": true} to read its marks for size again.
            return 1.0, 1.0
        hit, _lib, _mir = self.lib.turn_source(band, "rest", self.key)
        tiles = as_tiles(hit)
        if not tiles:
            return 1.0, 1.0
        got = [r for r in (self._lip_ratio(int(self.i) + d, tiles[0]) for d in (-2, -1, 0, 1, 2)) if r is not None]
        if not got:
            return 1.0, 1.0
        a = np.asarray(got, dtype=np.float64)
        # ONE size, from the lip's HEIGHT: upper-lip point to lower-lip point are both on the front of her profile and
        # always in view. Her mouth's width is not: on a deep turn the far corner is hidden and the corner-to-corner
        # reading swung between 0.5 and 1.3 on neighbouring angle bands (measured), so it is read but not used.
        # A tile is never drawn at less than half or more than twice its landed size: marks that say so have lost her lips.
        kk = float(np.clip(np.median(a[:, 0]), 0.5, 2.0))
        # ...EXCEPT while she is turned LESS than the tile was filmed at (the User 2026-10-02, the 45 tile at 25 deg:
        # "sunken", narrow): there both her corners are in view and the tile's mouth is the foreshortened one, so the
        # width is read too and the mouth is widened to hers. Off unless the face's mouth-turn.json says {"lip_width": true}.
        yh, yt = getattr(self, "_pl_yaw", None), self._tile_yaw(tiles[0])
        if self._turn_cfg().get("lip_width") is True and yh is not None and yt is not None and abs(float(yh)) < float(yt):
            return kk, float(np.clip(np.median(a[:, 1]), 0.5, 2.0))
        return kk, kk

    def _bend_on(self) -> bool:
        """mouth-turn.json "bend": true / false, or a LIST of the tile sets it applies to - library names, "front"
        for a loop's own tile (the User 2026-10-03: a 90 tile opened out to 60 deg read doubled; the 45s read right)."""
        v = self._turn_cfg().get("bend")
        if isinstance(v, (list, tuple)):
            cell = str(getattr(self, "_pl_cell", None) or "")
            lib = cell.split("/", 1)[0]
            return (lib in v) or ("front" in v and not lib.startswith("profile-"))
        return bool(v) if isinstance(v, bool) else TURN_BEND_ON

    def _half_chord(self) -> float | None:
        """Half her cheek-to-cheek chord in head sizes, read on this face's own front loops (the median over their
        head tracks): the width of the arch the bend turns. Cached per face."""
        cache = self.__dict__.setdefault("_half_chord_c", {})
        face = face_of(self.key) or ""
        if face not in cache:
            vals = []
            for k in list(self.lib.rows):
                if (face_of(k) or "") != face or self.seq.graph.role(k) != "loop":
                    continue
                n = len((self.lib.playlist(k) or {}).get("frames") or [])
                for i in range(0, n, 5):
                    h = unpack_head(self.lib.head(k, i))
                    if h is None or h.get("cheek_l") is None or h.get("cheek_r") is None or not h.get("s"):
                        continue
                    L, _n, R = _hf_mod.cheeks_of(h)
                    vals.append(float(np.linalg.norm(R - L)) / 2.0 / float(h["s"]))
            cache[face] = float(np.median(vals)) if len(vals) >= 3 else None
        return cache[face]

    def _tile_yaw(self, rel: str) -> float | None:
        """The turn angle the tile was filmed at: its source frame's row in its take's playlist."""
        base_rel, _m = _unmirror(rel)
        cache = self.__dict__.setdefault("_tile_yaw_c", {})
        if base_rel not in cache:
            out = None
            try:
                n = int(Path(base_rel).name.split("_f", 1)[1].split("_", 1)[0])
            except (IndexError, ValueError):
                n = None
            for lib in list(TURN_LIBRARIES.values()) + list(TURN_LIBRARIES_OTHER.values()):
                k = self.lib._turn_row(lib, self.key) if lib else None
                pl = self.lib.playlist(k) if k else None
                if not pl or n is None:
                    continue
                if any(base_rel in as_tiles(hit) for hit in (pl.get("index") or {}).values()):
                    rows = pl.get("frames") or []
                    try:
                        out = abs(float(rows[n]["yaw"]))
                    except (IndexError, KeyError, TypeError, ValueError):
                        out = None
                    break
            cache[base_rel] = out
        return cache[base_rel]

    def _tile_pose(self, rel: str) -> tuple | None:
        """(pitch, yaw) of the head track on the frame a tile was cut from (yaw mirrored with the tile); None when
        that frame has no head."""
        base_rel, _m = _unmirror(rel)
        try:
            pre, rest = Path(base_rel).name.split("_f", 1)
            rec = self.lib.head(pre, int(rest.split("_", 1)[0])) or {}
            p, y = float(rec["pose"][0]), float(rec["pose"][1])
        except (IndexError, KeyError, TypeError, ValueError):
            return None
        return p, (-y if _m else y)

    def _tile_yaw_signed(self, rel: str) -> float | None:
        """The head track's own turn angle (signed) of the frame a tile was cut from; None when it has no head."""
        got = self._tile_pose(rel)
        return None if got is None else got[1]

    def _lip_size_bend(self, band) -> float:
        """Her closed lip's height over the rest tile's AT THE HEAD-SIZE RATIO (what the bend draws it at), median
        over this frame and two neighbours either side. 1 when it cannot be read or lip_size is off."""
        if self._turn_cfg().get("lip_size") is not True or not band:      # marks are for position, not size (see _lip_size)
            return 1.0
        hit, _lib, _mir = self.lib.turn_source(band, "rest", self.key)
        tiles = as_tiles(hit)
        if not tiles:
            return 1.0
        t_top, t_bot = self.lib.tile_mark(tiles[0], "top"), self.lib.tile_mark(tiles[0], "bot")
        if t_top is None or t_bot is None:
            return 1.0
        h_tile = float(np.linalg.norm(t_top - t_bot))
        # NOT FOR THE DOWN TILT (the User 2026-10-02, a turn with the head 30 deg down: "so tiny"): her lips look
        # shorter from above, and the step was shrinking the tile's whole mouth to imitate that. The height is
        # compared at ONE tilt: each side's reading is divided by the cosine of its own head-track pitch. Off:
        # mouth-turn.json {"lip_tilt": false}.
        _tilt = self._turn_cfg().get("lip_tilt") is not False
        _pt = self._tile_pose(tiles[0])
        got = []
        for d in (-2, -1, 0, 1, 2):
            t = self._tri_M(int(self.i) + d, tiles[0])
            if t is None or t["f"].get("top") is None or t["f"].get("bot") is None or h_tile <= 1e-6:
                continue
            h_her = float(np.linalg.norm(np.asarray(t["f"]["top"], dtype=np.float64) - np.asarray(t["f"]["bot"], dtype=np.float64))) * t["k"]
            r = h_her / (h_tile * t["s"] / t["ts"])
            if _tilt and _pt is not None:
                try:
                    _ph = float((self.lib.head(self.key, int(self.i) + d) or {})["pose"][0])
                    _c = float(np.cos(np.radians(_pt[0]))) / max(0.5, float(np.cos(np.radians(_ph))))
                    r *= _c
                except (KeyError, IndexError, TypeError, ValueError):
                    pass
            got.append(r)
        return float(np.clip(np.median(got), 0.5, 2.0)) if got else 1.0

    def _front_cap(self, rel: str, M, k_al: float, k_ac: float, s: float) -> tuple | None:
        """(along, across) factors <= 1 that keep this turn tile's drawn mouth no taller / wider than the loop's own
        FRONT tile of the same class would be at head size `s` (the forward mouth at the same distance). The front
        tile's mouth is read from its own take's marks over the take's head size; the turn tile's from its marks
        through the landing `M` and the lip-size factors. None when either side lacks its marks."""
        cls = str(getattr(self, "_pl_class", None) or "rest")
        pl = self.lib.playlist(self.key) or {}
        front = None
        for want in (cls, "rest"):
            for k, hit in (pl.get("index") or {}).items():
                if str(k).split("/")[-1] == want and as_tiles(hit):
                    front = as_tiles(hit)[0]
                    break
            if front:
                break
        if not front or _unmirror(front)[0] == _unmirror(rel)[0]:
            return None
        fa, fts = self.lib.tile_anchors(front), self.lib.tile_take_scale_any(front)
        ta = self.lib.tile_anchors(rel)
        if fa is None or not fts or ta is None:
            return None
        D = lambda a, b: float(np.linalg.norm(np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)))
        w_front = D(fa[0], fa[1]) / fts * s                                          # painter px at her head size
        L = lambda q: MP.apply(M, q)[0]
        w_turn = D(L(ta[0]), L(ta[1])) * k_ac
        if w_turn <= 1e-6:
            return None
        # WIDTH only: the opened-out axis. The height is already matched to her own lips (lip_size), and a heads-down
        # front tile's lip height is foreshortened by its own pitch, so capping on it shrank every turn mouth to 0.4.
        return 1.0, min(1.0, w_front / w_turn)

    def _rest_open_k(self, band, rel: str) -> float:
        """Along-the-face factor for the BETWEEN-WORDS tile (the User 2026-10-02: "open rest that is too big"): with
        mouth-turn.json {"rest_open": "rest"} its lips are drawn at the height of the same take's closed rest tile, so
        a parted mouth sits inside her own closed one. 1 for every other class, or when either tile lacks its marks."""
        if self._turn_cfg().get("rest_open") != "rest" or not band:
            return 1.0
        if not str(getattr(self, "_pl_cell", None) or "").endswith("/rest-open"):
            return 1.0
        hit, _lib, _mir = self.lib.turn_source(band, "rest", self.key)
        tiles = as_tiles(hit)
        if not tiles:
            return 1.0
        a, b = self.lib.tile_mark(tiles[0], "top"), self.lib.tile_mark(tiles[0], "bot")
        c, e = self.lib.tile_mark(rel, "top"), self.lib.tile_mark(rel, "bot")
        if a is None or b is None or c is None or e is None:
            return 1.0
        h_rest, h_open = float(np.linalg.norm(a - b)), float(np.linalg.norm(c - e))
        return float(np.clip(h_rest / h_open, 0.5, 1.0)) if h_open > 1e-6 else 1.0

    def _own_frame_origin(self, rel: str):
        """(x, y, k) when the tile was cut from THIS frame of THIS film: its crop origin in painter pixels and the
        native -> painter scale, else None. The tile's name carries its film stem and frame; the map's head block the
        crop origin in the film's native pixels (verified on the PNG by pose_intake.tile_marks); the film's native size
        is the lip-mark file's `wh`."""
        try:
            name = Path(str(rel)).name
            m = re.search(r"^(.*)_f(\d+)_", name)
            if not m:
                return None
            stem, fr = m.group(1), int(m.group(2))
            row = self.lib.rows.get(self.key) or {}
            film = str(row.get("file") or "")
            film = film[:-4] if film.endswith(".mp4") else film
            if film.startswith("clip-"):
                film = film[5:]
            if stem != film or fr != int(self.i):
                return None
            h = self.lib.tile_head(rel) or {}
            cxy = h.get("crop_xy")
            if not (isinstance(cxy, (list, tuple)) and len(cxy) == 2) or h.get("origin") != "png":
                return None
            lm = self.lib.lipmarks("clip-" + film) or {}
            wh = lm.get("wh")
            if not (isinstance(wh, (list, tuple)) and len(wh) == 2 and wh[0]):
                return None
            kk = paint_wh()[0] / float(wh[0])
            return (float(cxy[0]) * kk, float(cxy[1]) * kk, kk)
        except (AttributeError, TypeError, ValueError):
            return None

    def _tri_own(self, t: dict, M, tile, rel: str, raw: dict | None, dbg: dict) -> dict | None:
        """The landing of a tile on its own frame: the picture back at its origin (scaled with the frame), the usual
        cover mask / hole / lips around it. Identical pixels under any mask = the raw frame."""
        dh, th, f, k = t["dh"], t["th"], t["f"], t["k"]
        s = float((raw or {}).get("s") or 100.0)
        _lm = self._turn_cfg().get("lip_margin")
        g = (float(_lm) if isinstance(_lm, (int, float)) else MOUTH_LIPS_GROW_S) * s
        warped = cv2.warpAffine(tile, M, paint_wh(), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        _fe = MOUTH_LIPS_FEATHER_S * s * _edge_ratio()
        mask = MP.cover_mask(paint_wh()[::-1], [MP.apply(M, q) for q in th], dh, g, _fe)
        hole = MP.fill(paint_wh()[::-1], dh)
        if mask is None or hole is None:
            return None
        gi = int(round(g))
        if gi:
            hole = cv2.dilate(hole, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gi + 1, 2 * gi + 1)))
        lips = MP.fill(paint_wh()[::-1], np.asarray(f["lips_outer"], dtype=np.float64) * k) if f.get("lips_outer") else None
        return {"warped": warped, "M": M, "dst": raw, "mask": mask, "hole": hole, "lips": lips, "dbg": dbg, "rel": rel, "core": None}

    def _tri_sig(self) -> tuple:
        """Everything a turn landing and its paste read besides the frame and the tile: the face's turn file, every
        live mouth dial, the head geometry, the paint size, the frame's yaw / cell / band. A dial move is a new key,
        so nothing needs flushing; a timing dial in here only costs a miss."""
        g = globals()
        dials = tuple(sorted((k, g[k]) for k in g if k.startswith(("MOUTH_", "TURN_", "PLAYLIST_TONE", "XFADE"))
                             and isinstance(g[k], (int, float, str, bool))))
        return (json.dumps(self._turn_cfg(), sort_keys=True, default=str), dials, repr(HEAD_GEOM), tuple(paint_wh()),
                float(_edge_ratio()), getattr(self, "_pl_yaw", None), getattr(self, "_pl_cell", None),
                repr(getattr(self, "_pl_band", None)))

    def _tri_key(self, kind: str, rel: str, band) -> "tuple | None":
        """The (frame, tile) key of a turn landing, or None where it must not be cached: with a nose EMA the landing
        follows the frame before it (a held frame and a played one differ), and with no frame there is no key."""
        if float(MOUTH_NOSE_EMA) > 0.0 or getattr(self, "i", None) is None:
            return None
        row = self.lib.rows.get(self.key) or {}
        stem = Path(str(row.get("file") or self.key)).stem
        return (str(self.key), int(self.i), kind, self._loop_ver(), loop_version(POSE_DIR / (stem + ".lipmarks.json")),
                str(rel), repr(band), self._tri_sig())

    def _tri_landing(self, tile, rel: str, raw: dict | None, base=None, band=None) -> dict | None:
        """_tri_landing_build, cached by (frame, tile) (poly_cache.tri_cached; the User 2026-10-18, the phone turn at
        under 4 fps). A hit restores the nose state the build leaves, so the next frame lands as it would have."""
        self._patch_unsafe = True        # a turn carries a nose state from the frame before: never a kept patch
        key = self._tri_key("land", rel, band) if raw is not None else None
        if key is None:
            return self._tri_landing_build(tile, rel, raw, base, band)

        def build():
            got = self._tri_landing_build(tile, rel, raw, base, band)
            if got is not None:
                got = dict(got, _ema=(self._tri_ema_i, self._tri_ema_a, self._tri_ema_key), _ck=key)
            return got

        got = tri_cached(key, paint_wh()[::-1], build)
        if got is not None:
            self._tri_ema_i, self._tri_ema_a, self._tri_ema_key = got["_ema"]
        return got

    def _tri_landing_build(self, tile, rel: str, raw: dict | None, base=None, band=None) -> dict | None:
        """The triangle landing of this tile on this frame, or None when either side lacks the points (the frame
        then paints as it did before). Nose tip, chin and the near-cheek point come from the lip-mark files, the
        tile's from its own source frame; her head for the hole and the soft edge is THIS frame's, unsmoothed,
        so the erase and the tile ride one track."""
        if raw is None:
            return None
        t = self._tri_M(int(self.i), rel)
        if t is None:
            return None
        M, da, dh, ta, th, f, k = t["M"], t["da"], t["dh"], t["ta"], t["th"], t["f"], t["k"]
        # A FRAME THAT MATCHES ITS OWN PICK PAINTS IT UNBENT (the User 2026-10-03: "a frame that matches a library pick
        # should use that pick unbent"): a tile cut from THIS frame of THIS film is the film's own pixels - it goes back at
        # its crop origin, no landing, no bend, no seating, no attach, so the painted frame is the raw frame.
        _own = self._own_frame_origin(rel)
        if _own is not None:
            M = np.array([[_own[2], 0.0, float(_own[0])], [0.0, _own[2], float(_own[1])]], dtype=np.float64)
            dbg = {"frame": int(self.i), "cell": getattr(self, "_pl_cell", None), "placed": "own", "yaw": getattr(self, "_pl_yaw", None),
                   "lip_size": [1.0, 1.0], "nudge_px": [0.0, 0.0], "bend": None, "cap": None, "attach": None}
            return self._tri_own(t, M, tile, rel, raw, dbg)
        # A LIP TO MATCH TO, AT HER SIZE. The triangle gives the tile its place on her face; the mouth itself is then
        # (1) brought forward so the tile's real upper-lip edge sits on hers - both read as the take's steady median,
        # as far as her lip is on the profile edge this frame - and (2) drawn at her own lip size about that front
        # point, so the lips shrink toward the front of her face and never back into it. ONLY the mouth changes size
        # (mouth_pins.lip_size_maps: full over her lip area, fading out by her lip-to-chin distance); the jaw, the
        # cheek and what the patch covers and erases stay where the triangle put them.
        uf = MP.front_dir(da[3], da[4], dh[0])
        vv = da[4] - da[3]
        vv = vv / max(1e-6, float(np.linalg.norm(vv)))
        anchor = da[2]
        t_anchor = None                     # the tile's own lip anchor (tile pixels), for the bend
        if uf is not None and f.get("top") is not None:
            d_top = np.asarray(f["top"], dtype=np.float64).ravel()[:2] * k
            t_top = self.lib.tile_mark(rel, "top")
            anchor = d_top
            t_anchor = t_top
            if t["edge"] is not None and t_top is not None:
                w = MP.front_weight(d_top, uf, dh[0], MOUTH_LIPS_GROW_S * t["s"])
                tuf = MP.front_dir(ta[3], ta[4], th[0])
                d_real = d_top + w * t["edge"][0][2] * t["s"] * uf
                t_real = t_top + (w * t["edge"][1][2] * t["ts"] * tuf if tuf is not None else 0.0)
                move = float((d_real - MP.apply(M, t_real)[0]) @ uf) * w
                M = np.asarray(M, dtype=np.float64).copy()
                M[:, 2] += move * uf
                anchor = d_real
                t_anchor = t_real
        k_al, k_ac = self._lip_size(band)
        # THE TUNE PANEL'S NUDGES ACT HERE TOO (the User 2026-10-02: "in Composer I have nudge controls"): `scale`
        # multiplies the mouth's size, turn u / v slide the landing (forward out of her face / down to her chin -
        # the FRONT landing's nose u / v do not reach here, see MOUTH_TURN_U), and a Grid cell's pin for this
        # angle slides it again - the mask and the erase move with it. All are 1 / 0 / none on a lock that never
        # touched them, so an untouched library paints exactly what the data says.
        k_al, k_ac = k_al * MOUTH_SCALE, k_ac * MOUTH_SCALE
        _sh = np.zeros(2)
        _sgw = None
        try:
            if MOUTH_TURN_U or MOUTH_TURN_V:
                _sh = _sh + MP.nudge_px(MOUTH_TURN_U, MOUTH_TURN_V, (uf if uf is not None else raw["u"]), vv, t["s"])
            # THE FACE'S OWN SEATING PER TURN LIBRARY: mouth-turn.json {"nudge": {"<library>": [fwd, down]}}, the
            # reading the User took on the Composer, in the same unit and on the same axes as turn u / v.
            _nd = self._turn_cfg().get("nudge") or {}
            _lib_n = _nd.get(str(getattr(self, "_pl_cell", None) or "").split("/", 1)[0], _nd.get("*"))    # "*": every other
            _lib_n = (float(_lib_n[0]), float(_lib_n[1])) if isinstance(_lib_n, (list, tuple)) and len(_lib_n) == 2 else None
            _lvl = self._level_reading()
            if _lvl is not None and _lvl[1] is not None and _lvl[0] > 0:        # her head level: his reading, blended in
                _w = _lvl[0]; _b = _lib_n or (0.0, 0.0)
                _lib_n = (_b[0] * (1 - _w) + _lvl[1][0] * _w, _b[1] * (1 - _w) + _lvl[1][1] * _w)
            # THE SEATING IS FOR THE YAW GAP, NOT THE FRAME (measured 2026-10-04 on the take the tiles were cut from,
            # where tile and frame share an angle: his seating, right where it was read - the flourish's level
            # frames wear 60 deg tiles on a 38 deg head and sat 6 px back without it - pushed that take's own mouth
            # 6 px off its own lips). mouth-turn.json {"seat_by_gap": <deg>} scales the seating by
            # |frame yaw - tile yaw| / deg, capped at 1: full where it was read, zero where the tile is the frame's own
            # angle. Absent = the constant seating, as before.
            _sg = self._turn_cfg().get("seat_by_gap")
            _sgw = None
            if _lib_n is not None and isinstance(_sg, (int, float)) and float(_sg) > 0:
                _ty = self._tile_yaw(rel)
                _fy = getattr(self, "_pl_yaw", None)
                if _ty is not None and _fy is not None:
                    _sgw = min(1.0, abs(abs(float(_fy)) - _ty) / float(_sg))
                    _lib_n = (_lib_n[0] * _sgw, _lib_n[1] * _sgw)
            if _lib_n is not None:
                _sh = _sh + MP.nudge_px(_lib_n[0], _lib_n[1], (uf if uf is not None else raw["u"]), vv, t["s"])
            if MOUTH_PIN_CELLS and getattr(self, "_pl_cell", None):
                _rows = ((self.lib.pins().get("cells") or {}).get(self._pl_cell) or {}).get("rows")
                _uv = MP.pin_at(_rows, getattr(self, "_pl_yaw", None))
                if _uv is not None:
                    _sh = _sh + MP.nudge_px(_uv[0], _uv[1], raw["u"], raw["v"], t["s"])
        except (KeyError, TypeError, ValueError):
            _sh = np.zeros(2)
        if float(np.abs(_sh).max()) > 0:
            M = np.asarray(M, dtype=np.float64).copy()
            M[:, 2] += _sh
            anchor = np.asarray(anchor, dtype=np.float64) + _sh
        # THE EMA DIAL WORKS HERE TOO (the User 2026-10-03: "only a few of these dials work ... make them work"): the
        # landing's anchor is smoothed across contiguous frames exactly as the front landing smooths its nose
        # (ema_nose: k 0.35 ~ 3 frames, 1 = raw, 0 = copy through); a jump in the frame index restarts it.
        _ei, _ea = getattr(self, "_tri_ema_i", None), getattr(self, "_tri_ema_a", None)
        # ...AND ONLY ON THE SAME CLIP (the User 2026-10-03, "it jumps off and on the attachment": the first frame after
        # a clip change blended in the previous clip's anchor from hundreds of px away, then corrected on the next)
        if _ei is not None and getattr(self, "_tri_ema_key", None) != self.key:
            _ei = _ea = None
        _sm = None
        if _ei is not None and _ea is not None and int(self.i) == int(_ei) and _ea[0] == rel:
            # the same frame painted again (a hold): the answer it already has, LESS the nudge it was painted with
            # and PLUS the nudge the dials say now - so turn fwd / down move a frame held on the Composer at once
            # (the User 2026-10-03, frame-by-frame: "the forward button up high and tile never budged").
            _sm = np.asarray(_ea[1], dtype=np.float64) - np.asarray(_ea[2], dtype=np.float64) + _sh
        elif _ei is not None and _ea is not None and abs(int(self.i) - int(_ei)) == 1:
            _sm = ema_nose(_ea[1], anchor, MOUTH_NOSE_EMA)
        if _sm is not None:
            _d = np.asarray(_sm, dtype=np.float64) - np.asarray(anchor, dtype=np.float64)
            if float(np.abs(_d).max()) > 0:
                M = np.asarray(M, dtype=np.float64).copy()
                M[:, 2] += _d
                anchor = np.asarray(_sm, dtype=np.float64)
        self._tri_ema_i, self._tri_ema_a = int(self.i), (rel, np.asarray(anchor, dtype=np.float64).copy(),
                                                        np.asarray(_sh, dtype=np.float64).copy())
        self._tri_ema_key = self.key
        # THE HEAD LANDING (the User 2026-10-02, a turn with the head pitched down too: a smeared mouth, a profile mouth
        # where a three-quarter one was painted): the nose-chin-cheek triangle is a full affine, and with her head pitched or rolled as well
        # as turned it SKEWS the tile (measured: squeezed to two-thirds across) and squeezes a dropped jaw up to her
        # closed chin. With mouth-turn.json {"landing": "head"} the tile is landed by a similarity instead - her
        # head size over the take's, turned to her nose->chin line, on the same lip anchor: it cannot skew, and an
        # open jaw keeps its length. Default "triangle" = as before.
        _head = None
        _ts_any = self.lib.tile_take_scale_any(rel) if self._turn_cfg().get("landing") == "head" else None
        if _ts_any and t_anchor is not None and t["s"]:
            vt_ = ta[4] - ta[3]
            if float(np.linalg.norm(vt_)) > 1e-6:
                ang = float(np.arctan2(vv[1], vv[0]) - np.arctan2(vt_[1], vt_[0]))
                ks = float(t["s"]) / float(_ts_any)
                lin = ks * np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]], dtype=np.float64)
                _head = np.hstack([lin, (np.asarray(anchor, dtype=np.float64) - lin @ np.asarray(t_anchor, dtype=np.float64)).reshape(2, 1)])
                M = _head
                kk = self._lip_size_bend(band) * MOUTH_SCALE
                k_al, k_ac = kk * self._rest_open_k(band, rel), kk
        # THE BEND: the same anchors and nudges, but the tile is turned along the arch to her angle and drawn at the
        # head-size ratio (see TURN_BEND_ON). The triangle above still answers "is this a face" and stays the landing
        # whenever the bend cannot be built.
        rp = None
        if self._bend_on() and t_anchor is not None and uf is not None:
            vt = ta[4] - ta[3]
            yt, hc, yh = self._tile_yaw(rel), self._half_chord(), getattr(self, "_pl_yaw", None)
            if yt is None and yh is not None:
                # a FRONT tile (the loop's own, on a shallow turn): its take's own head angle, counted only when it
                # is turned the same way she is - a tile turned the other way is taken as straight on
                _ys = self._tile_yaw_signed(rel)
                yt = None if _ys is None else (abs(_ys) if (_ys < 0) == (float(yh) < 0) else 0.0)
            # A PROFILE TILE BENDS OUT, NOT IN (the User 2026-10-03, a 45 take at f290: 'looks like a 90d on 45d head' - a
            # 32 deg tile of her own take closed to her 43 deg head read 15 % too narrow). Opening out (tile turned more
            # than her head, every 45 tile of the first face is at 59 deg) is what the bend was measured on; closing in over-foreshortens, so a
            # profile tile less turned than her head takes the head landing as it is. {"bend_in": true} re-allows it;
            # the front tile (tile_yaw None) always bends - that is her left turns on a face with no takes.
            # ...OR BENDS IN BY A FRACTION (the User 2026-10-03, the same frame rest vs a speech tile: no bend-in reads
            # "not turned enough", a full one read "90 on a 45"): `bend_in` 0 = none (the default), 1 = the full
            # closing re-projection, between = that share of the angle the tile lacks.
            _bi = self._turn_cfg().get("bend_in")
            _bi = 1.0 if _bi is True else (float(_bi) if isinstance(_bi, (int, float)) and not isinstance(_bi, bool) else 0.0)
            _bi = min(1.0, max(0.0, _bi))
            _closing = (self._tile_yaw(rel) is not None and yh is not None and yt is not None
                        and abs(float(yh)) > float(yt) + 1.0)
            _yh = yh
            if _closing and _bi <= 0.0:
                yt = None
            elif _closing and _bi < 1.0:
                _yh = float(np.sign(yh)) * (float(yt) + _bi * (abs(float(yh)) - float(yt)))      # part of the way in
            if float(np.linalg.norm(vt)) > 1e-6 and yt is not None and hc and yh is not None:
                vt = vt / float(np.linalg.norm(vt))
                rp = MP.arch_reproject(_yh, yt, hc, 0.5 / max(1.0, t["s"]))    # the cubic unless it misses by half a pixel
                # the turn is about her own up-down line: across is the PERPENDICULAR of nose->chin on each side
                # (toward the front), never the outline's front direction, so the re-projection cannot skew
                _bf = np.array([-vv[1], vv[0]], dtype=np.float64)
                _bf = _bf if float(_bf @ uf) >= 0 else -_bf
                _a = float(np.arctan2(vt[1], vt[0]) - np.arctan2(vv[1], vv[0]))       # her frame -> the tile's
                _btf = np.array([[np.cos(_a), -np.sin(_a)], [np.sin(_a), np.cos(_a)]], dtype=np.float64) @ _bf
                _bts = self.lib.tile_take_scale_any(rel) or t["ts"]
        if rp is not None:
            kk = self._lip_size_bend(band) * MOUTH_SCALE
            k_al, k_ac = kk * self._rest_open_k(band, rel), kk
            M = MP.bend_affine(rp, anchor, vv, _bf, t["s"], t_anchor, vt, _btf, _bts)
        # NEVER BIGGER THAN HER FORWARD MOUTH (the User 2026-10-03, frame 155: the 45 tile opened out to 34 deg reads
        # too big): with mouth-turn.json {"cap": "front"} the turn tile's mouth, as it will be drawn, is measured
        # against the loop's own front tile of the same class at this head size, and scaled down to it where it is
        # wider or taller. It only ever shrinks, so the global scale dial stays what it is.
        # THE FACE'S OWN SIZE PER TURN LIBRARY (the User 2026-10-03, the 45 cell "looks perfect" at scale 0.72 while the
        # forward mouth must stay as locked): mouth-turn.json {"scale": {"<library>": k}} multiplies a TURN frame's
        # mouth only, "*" = every other library. Absent = 1, nothing changes. AFTER the head and bend landings, which
        # rebuild the size from the lip reading (a first copy sat above them and was thrown away on every 45 frame).
        try:
            _sc = self._turn_cfg().get("scale") or {}
            _k = _sc.get(str(getattr(self, "_pl_cell", None) or "").split("/", 1)[0], _sc.get("*"))
            _k = float(_k) if isinstance(_k, (int, float)) and 0.1 <= float(_k) <= 3.0 else 1.0
            _lvl = self._level_reading()
            if _lvl is not None and _lvl[2] is not None and _lvl[0] > 0:        # her head level: his size, blended in
                _k = _k * (1 - _lvl[0]) + _lvl[2] * _lvl[0]
            if _k != 1.0:
                k_al, k_ac = k_al * _k, k_ac * _k
        except (AttributeError, TypeError, ValueError):
            pass
        # THE NOSE ATTACHES TOO (the User 2026-10-03, "nose pixel i keep saying. Neither attaches to her nose"): every turn
        # landing above is anchored at the LIP line with the nose only setting the angle, so the tile's nose lands where
        # the head-size ratio puts it - beside hers. With mouth-turn.json {"attach": "nose+lip"} the landed tile is
        # turned and scaled ABOUT the lip point so its nose tip lands on her nose tip as well: two points, both exact,
        # the jaw following. Off unless the face says so.
        _attach = None
        if self._turn_cfg().get("attach") == "nose+lip" and t_anchor is not None:
            try:
                _tn = MP.apply(M, ta[3])[0]                               # the tile's nose, as landed
                _dn = np.asarray(da[3], dtype=np.float64)                 # her nose on this frame
                _L = np.asarray(anchor, dtype=np.float64)
                _v1, _v2 = _tn - _L, _dn - _L
                _n1, _n2 = float(np.linalg.norm(_v1)), float(np.linalg.norm(_v2))
                if _n1 > 1e-6 and _n2 > 1e-6:
                    _sc = min(1.6, max(0.6, _n2 / _n1))
                    _an = float(np.arctan2(_v2[1], _v2[0]) - np.arctan2(_v1[1], _v1[0]))
                    _R = _sc * np.array([[np.cos(_an), -np.sin(_an)], [np.sin(_an), np.cos(_an)]], dtype=np.float64)
                    _C = np.hstack([_R, (_L - _R @ _L).reshape(2, 1)])
                    _M3 = np.vstack([np.asarray(M, dtype=np.float64), [0.0, 0.0, 1.0]])
                    M = (np.vstack([_C, [0.0, 0.0, 1.0]]) @ _M3)[:2]
                    _attach = {"scale": round(_sc, 3), "deg": round(float(np.degrees(_an)), 1),
                               "nose_was_off_px": [round(float(v), 1) for v in (_tn - _dn)]}
            except (IndexError, TypeError, ValueError):
                _attach = None
        _cap = None
        if self._turn_cfg().get("cap") == "front":
            _cap = self._front_cap(rel, M, k_al, k_ac, float(t["s"]))
            if _cap is not None:
                k_al, k_ac = k_al * _cap[0], k_ac * _cap[1]
        # THE JAW REACHES HER CHIN ON A TURN (the User 2026-10-18, a front tile through a 43 deg turn: "too short on
        # the turn her jaw is bigger"): measured, her lip-to-chin grows against her head size as she turns (0.28 s facing
        # us, 0.32 s at 40 deg), so a tile landed by head size ends above her chin and leaves a second jawline. With
        # mouth-turn.json {"jaw": true} the landed picture is stretched along her nose->chin line about her lip line to
        # her own lip-to-chin, as the lips landing does (MP.jaw_stretch, clamped 0.85-1.2), whichever landing above
        # drew it; the cover follows. Off unless the face says so.
        _jawS = None
        if self._turn_cfg().get("jaw") is True:
            try:
                _M3 = np.vstack([np.asarray(M, dtype=np.float64), [0.0, 0.0, 1.0]])
                _Mj = np.vstack([MP.jaw_stretch(M, ta, da), [0.0, 0.0, 1.0]])
                _jawS = (_Mj @ np.linalg.inv(_M3))[:2]
                if np.allclose(_jawS, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]):
                    _jawS = None
            except (np.linalg.LinAlgError, IndexError, TypeError, ValueError):
                _jawS = None
        # A HAND TURN AT THE APEX (the User 2026-10-18, a front tile through a 43 deg turn: right up to the apex, then
        # tilted - "rotate the tile clockwise a bit ... make that window as tiny ... as non obtrusive"): mouth-turn.json
        # {"rot": {"deg": d, "from": a, "full": b}} turns the landed picture d deg clockwise on screen about her lip
        # line, by |yaw| from a (0) to b (all), so only the apex frames move. Off unless the face says so.
        _rotS = None
        _rc = self._turn_cfg().get("rot")
        if isinstance(_rc, dict) and getattr(self, "_pl_yaw", None) is not None:
            try:
                _ra, _rb, _rd = float(_rc.get("from", 0.0)), float(_rc.get("full", 0.0)), float(_rc.get("deg", 0.0))
                _rw = min(1.0, max(0.0, (abs(float(self._pl_yaw)) - _ra) / max(0.01, _rb - _ra)))
                if _rw > 0.0 and _rd != 0.0:
                    _an = np.radians(_rd * _rw)                  # y runs down: a positive angle turns clockwise
                    _R = np.array([[np.cos(_an), -np.sin(_an)], [np.sin(_an), np.cos(_an)]], dtype=np.float64)
                    _L = np.asarray(anchor, dtype=np.float64).ravel()[:2]
                    _rotS = np.hstack([_R, (_L - _R @ _L).reshape(2, 1)])
            except (IndexError, TypeError, ValueError):
                _rotS = None
        s = float(raw.get("s") or 100.0)
        ts = self.lib.tile_take_scale(rel)
        sv = np.linalg.svd(np.asarray(M, dtype=np.float64)[:, :2], compute_uv=False)
        dbg = {"frame": int(self.i), "cell": getattr(self, "_pl_cell", None), "placed": ("bend" if rp is not None else "head" if _head is not None else "tri"), "attach": _attach,
               "yaw": None if getattr(self, "_pl_yaw", None) is None else round(float(self._pl_yaw), 2),
               "scale": [round(float(sv.max()), 4), round(float(sv.min()), 4)],
               "lip_size": [round(k_al, 3), round(k_ac, 3)], "nudge_px": [round(float(_sh[0]), 1), round(float(_sh[1]), 1)],
               "seat_gap": None if _sgw is None else round(float(_sgw), 3)}
        if rp is not None:
            dbg["bend"] = {"deg": rp["deg"], "err_px": round(rp["err"] * t["s"], 2), "c1": round(float(rp["c"][0]), 3),
                           "tile_yaw": round(float(yt), 1)}
        if _cap is not None:
            dbg["cap"] = [round(_cap[0], 3), round(_cap[1], 3)]
        if ts:
            ks = s / float(ts)
            if not (0.5 * ks <= float(sv.min()) and float(sv.max()) <= 2.0 * ks):
                self._tri_refused = dict(dbg, why="triangle off the head's size by more than 2x")
                return None                 # an outline that lost the face: not a landing
        _lm = self._turn_cfg().get("lip_margin")
        g = (float(_lm) if isinstance(_lm, (int, float)) else MOUTH_LIPS_GROW_S) * s
        _lipreg = (MP.mouth_region(paint_wh()[::-1], [np.asarray(f["lips_outer"], dtype=np.float64) * k], g)
                   if f.get("lips_outer") else None)
        if rp is not None:
            mx, my = MP.bend_maps(paint_wh()[::-1], rp, anchor, vv, _bf, t["s"], t_anchor, vt, _btf, _bts,
                                  k_al, k_ac, _lipreg, float(np.linalg.norm(da[4] - da[2])))
            warped = cv2.remap(tile, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        elif (k_al, k_ac) != (1.0, 1.0) and _lipreg is not None:
            mx, my = MP.lip_size_maps(paint_wh()[::-1], M, anchor, vv, k_al, k_ac, _lipreg,
                                      float(np.linalg.norm(da[4] - da[2])))
            warped = cv2.remap(tile, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        else:
            warped = cv2.warpAffine(tile, M, paint_wh(), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        if _jawS is not None:
            warped = cv2.warpAffine(warped, _jawS, paint_wh(), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            M = (np.vstack([_jawS, [0.0, 0.0, 1.0]]) @ np.vstack([np.asarray(M, dtype=np.float64), [0.0, 0.0, 1.0]]))[:2]
            _jsv = np.linalg.svd(_jawS[:, :2], compute_uv=False)
            dbg["jaw"] = round(float(_jsv.max() if _jsv.max() > 1.0 + 1e-6 else _jsv.min()), 3)    # the stretch, either way
        if _rotS is not None:
            warped = cv2.warpAffine(warped, _rotS, paint_wh(), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            M = (np.vstack([_rotS, [0.0, 0.0, 1.0]]) @ np.vstack([np.asarray(M, dtype=np.float64), [0.0, 0.0, 1.0]]))[:2]
            dbg["rot"] = round(float(np.degrees(np.arctan2(_rotS[1, 0], _rotS[0, 0]))), 2)
        # THE EDGE DIAL WORKS HERE TOO: the cover's soft edge follows `edge` RELATIVE to the library lock, so the
        # locked value paints exactly what it did and the dial fades the patch from there.
        _fe = MOUTH_LIPS_FEATHER_S * s * _edge_ratio()
        mask = MP.cover_mask(paint_wh()[::-1], [MP.apply(M, q) for q in th], dh, g, _fe)
        hole = MP.fill(paint_wh()[::-1], dh)
        if mask is None or hole is None:
            return None
        # NOTHING IN FRONT OF HER PROFILE (the User 2026-10-18, a front tile at 43 deg: a pale glow in front of her lips
        # and chin): measured, her face outline reaches 23 px past her nose tip into the wall on a profile frame, so the
        # cover let the tile's cheek through there. With mouth-turn.json {"profile_cut": true} the cover and the erase
        # stop at her own profile line (nose tip, upper lip, lower lip, chin, from the lip marks), her own lips always
        # kept whole; the lip core (her lips grown by the margin) is cut at it too. Off unless the face says so.
        if self._turn_cfg().get("profile_cut") is True and uf is not None:
            try:
                _pts = [np.asarray(f[q], dtype=np.float64).ravel()[:2] * k for q in ("nose", "top", "bot", "chin")
                        if f.get(q) is not None]
                if len(_pts) == 4:
                    _far = 4.0 * float(max(paint_wh()))
                    _poly = np.array(_pts + [_pts[3] - _far * uf, _pts[0] - _far * uf], dtype=np.float32)
                    _keep = np.zeros(paint_wh()[::-1], dtype=np.uint8)
                    cv2.fillPoly(_keep, [np.round(_poly).astype(np.int32)], 255)
                    if f.get("lips_outer"):                 # her lips themselves, never grown: the margin is the glow
                        _lo = MP.fill(paint_wh()[::-1], np.asarray(f["lips_outer"], dtype=np.float64) * k)
                        if _lo is not None:
                            _keep = cv2.bitwise_or(_keep, np.asarray(_lo, dtype=np.uint8))
                    hole = cv2.bitwise_and(hole, _keep)
                    _kf = cv2.GaussianBlur(_keep.astype(np.float32) / 255.0, (0, 0), max(0.5, _fe))
                    mask = (np.asarray(mask, dtype=np.float32) * _kf).astype(np.float32)
                    dbg["profile_cut"] = True
            except (IndexError, KeyError, TypeError, ValueError, cv2.error):
                pass
        gi = int(round(g))
        if gi:
            hole = cv2.dilate(hole, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gi + 1, 2 * gi + 1)))
        lips = MP.fill(paint_wh()[::-1], np.asarray(f["lips_outer"], dtype=np.float64) * k) if f.get("lips_outer") else None
        # HER LIPS ARE FULLY REPLACED (the User 2026-10-02: "the tile must cover her filmed lips completely"). The soft
        # edge ramps in from the tile's own border, and on a head pitched down that border sits at her upper lip, so
        # the ramp left her lips half showing. Over her outer lip loop grown by the margin the tile is opaque -
        # wherever the tile has picture and both face outlines allow it.
        core = None
        if _lipreg is not None:
            core = _lipreg
            if core is not None:
                have = (warped.astype(np.float32).sum(axis=2) > 12.0).astype(np.float32)
                core = cv2.GaussianBlur(core.astype(np.float32) / 255.0 * have, (0, 0),
                                        max(0.5, _fe)) * mask
        return {"warped": warped, "M": M, "dst": raw, "mask": mask, "hole": hole, "lips": lips, "dbg": dbg, "rel": rel,
                "core": core}

    def _chin_keep(self, rel: str, dst: dict, rb=None):
        """THE PATCH'S BOTTOM CORNERS COME OFF (the User 2026-10-18, "that little skirt" ... "maybe just narrow the neck
        leave length?" ... "clip bottom corners"): the square patch is as wide below her mouth as across it, so its lower
        corners hung past her jaw like a skirt. With mouth-turn.json {"chin_cut": {"lip_half": a, "chin_half": b}} (x head
        size) the patch narrows from a either side of her lip line to b at her chin, on her own nose->chin axis, and keeps
        narrowing past it at the same slope (floored at half of b); its length is untouched, so an open jaw still draws.
        Cutting across under her chin, and along her whole outline, were tried the same day (the outline showed as a pale
        band by her hand). None = paint as before. rb = (y0, y1, x0, x1): the taper on that box only (MOUTH_CUT_ROI)."""
        cc = self._turn_cfg().get("chin_cut")
        if not isinstance(cc, dict):
            return None
        try:
            t = self._tri_M(int(self.i), rel)
            if t is None:
                return None
            da, s = t["da"], float(t["s"])
            hw = paint_wh()[::-1]
            lip = np.asarray(da[2], dtype=np.float64)
            chin = np.asarray(da[4], dtype=np.float64)
            vv = chin - np.asarray(da[3], dtype=np.float64)
            vv = vv / max(1e-6, float(np.linalg.norm(vv)))
            pp = np.array([-vv[1], vv[0]])
            tc = max(1.0, float(np.dot(chin - lip, vv)))
            wl = float(cc.get("lip_half", 0.50)) * s
            wc = float(cc.get("chin_half", 0.22)) * s
            sg = max(1.0, float(cc.get("fade", 0.06)) * s)             # a wide fade: the corners never read as a line
            y0, y1, x0, x1 = rb if rb is not None else (0, hw[0], 0, hw[1])
            yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            dx, dy = xx - float(lip[0]), yy - float(lip[1])
            tt = dx * float(vv[0]) + dy * float(vv[1])                 # down her face from her lip line
            uu = np.abs(dx * float(pp[0]) + dy * float(pp[1]))         # across it
            half = np.where(tt <= 0, wl, np.maximum(0.5 * wc, wl - (wl - wc) * tt / tc))
            return np.clip(0.5 - (uu - half) / (2.0 * sg), 0.0, 1.0).astype(np.float32)
        except (IndexError, KeyError, TypeError, ValueError):
            return None

    def _front_keep(self, rel: str):
        """THE FRONT LANDING STOPS AT HER JAW AND HER PROFILE (the User 2026-10-18, a phone flourish on Bezier past 17 deg: the tile's
        square striped her jaw and spilled onto the wall - "if we could stretch to that lower jaw it would be great mask").
        With mouth-turn.json {"front_cut": {"from": deg}} and |yaw| >= deg: (alpha 0..1, erase 0/255) - her own face
        outline (it follows her jaw) cut at her profile line (nose, upper lip, lower lip, chin), her lips always whole,
        feathered like the turn's cover. None = paint as before. One per (clip, frame, version)."""
        fc = self._turn_cfg().get("front_cut")
        yw = getattr(self, "_pl_yaw", None)
        if not isinstance(fc, dict) or yw is None:
            return None
        # THE TILE STOPS AT HER JAW, AT ANY PITCH (the User 2026-10-19, round 31's desk on a bow: "neck skirt bad on the up
        # angles", "the lower shoulder comes from the tile shoulders lowering over her chest", and on the phone "a small strip
        # of tile just below her jaw. Seems to be flawless at these angles using just the rendered neck"). Under front_cut's
        # angle, {"jaw": true} keeps her own face outline WITHOUT the profile-line cut (that cut is for a turned head; on a
        # frontal one it sliced a side off, "paralyzed", .101 10-19), so below her jaw the film's own neck shows. Kill:
        # HOUSE_MOUTH_JAW_CUT=0.
        jaw = abs(float(yw)) < float(fc.get("from", 12.0))
        if jaw and not (MOUTH_JAW_CUT and fc.get("jaw") is True):
            return None
        ck = (self.key, int(self.i), self._loop_ver(), json.dumps(fc, sort_keys=True), float(_edge_ratio()), jaw)
        got = self.__dict__.get("_front_keep_c")
        if got is not None and got[0] == ck:
            return got[1]
        out = None
        try:
            t = self._tri_M(int(self.i), rel)
            if t is not None:
                da, dh, f, k = t["da"], t["dh"], t["f"], t["k"]
                hw = paint_wh()[::-1]
                keep = MP.fill(hw, dh)
                uf = MP.front_dir(da[3], da[4], dh[0])
                pts = [np.asarray(f[q], dtype=np.float64).ravel()[:2] * k for q in ("nose", "top", "bot", "chin") if f.get(q) is not None]
                if keep is not None and uf is not None and len(pts) == 4:
                    if not jaw:
                        far = 4.0 * float(max(paint_wh()))
                        side = np.zeros(hw, dtype=np.uint8)
                        cv2.fillPoly(side, [np.round(np.array(pts + [pts[3] - far * uf, pts[0] - far * uf])).astype(np.int32)], 255)
                        keep = cv2.bitwise_and(np.asarray(keep, dtype=np.uint8), side)
                    if f.get("lips_outer"):
                        lo = MP.fill(hw, np.asarray(f["lips_outer"], dtype=np.float64) * k)
                        if lo is not None:
                            keep = cv2.bitwise_or(keep, np.asarray(lo, dtype=np.uint8))
                    fe = max(0.5, MOUTH_LIPS_FEATHER_S * float(t["s"]) * _edge_ratio())
                    ys, xs = np.nonzero(keep)
                    a = np.zeros(hw, dtype=np.float32)
                    if len(ys):                         # the feather on the face's own box, not the whole frame
                        g = int(np.ceil(4.0 * fe)) + 1
                        y0, y1 = max(0, ys.min() - g), min(hw[0], ys.max() + 1 + g)
                        x0, x1 = max(0, xs.min() - g), min(hw[1], xs.max() + 1 + g)
                        a[y0:y1, x0:x1] = cv2.GaussianBlur(keep[y0:y1, x0:x1].astype(np.float32) / 255.0, (0, 0), fe)
                    vv = np.asarray(da[4], dtype=np.float64) - np.asarray(da[3], dtype=np.float64)   # nose -> chin
                    vv = vv / max(1e-6, float(np.linalg.norm(vv)))
                    out = (a, keep, {"top": pts[1], "bot": pts[2], "chin": pts[3], "front": np.asarray(uf, dtype=np.float64),
                                     "down": vv, "s": float(t["s"]), "fe": fe, "jaw": jaw})
        except (IndexError, KeyError, TypeError, ValueError, cv2.error):
            out = None
        self._front_keep_c = (ck, out)
        return out

    def _paste_tri(self, base: np.ndarray, box, tile, rel: str, tri: dict) -> np.ndarray:
        """A full side turn: the triangle's picture with everything a front shot gets after the landing - the square
        alpha's soft edge, the jaw and side cuts, her skin's shading, the cross-dissolve, the arch erased under it."""
        warped, Maff, dst = tri["warped"], tri["M"], tri["dst"]
        self._pin_dbg = {"ncc": 0.0, "took": False, "pixel": [0.0, 0.0], "frame": int(getattr(self, "i", -1)),
                         "path": "tri", "how": "triangle (nose tip, chin, near cheek)"}
        self._place_dbg = None
        self._lip_mask = None
        self._lip_dbg = dict(tri["dbg"])
        kw = float(HEAD_GEOM.get("kw") or 0.28)
        kh = float(HEAD_GEOM.get("kh") or 0.18)
        ck = tri.get("_ck")
        if ck is not None:
            got = tri_cached(ck[:2] + ("paste",) + ck[3:], paint_wh()[::-1],
                             lambda: dict(zip(("warped", "a"), self._paste_tri_picture(base, warped, Maff, dst, rel, tri, kw, kh))))
            warped, a_cut = got["warped"], got["a"]
        else:
            warped, a_cut = self._paste_tri_picture(base, warped, Maff, dst, rel, tri, kw, kh)
        warped, a_cut = self._xfade(rel, warped, a_cut)
        # erased where the tile covers it and nowhere else (an erased strip the tile's outline does not reach would
        # show the inpaint as a grey smear on her cheek), and never past her own face grown by the lip margin
        return composite_stamp(
            base, box, warped, dst, a=a_cut, hole=self._hole(), tone=PLAYLIST_TONE,
            nose_keep=True, kw=kw, kh=kh, scale=MOUTH_SCALE, cover_alpha=True, hole_within=tri["hole"])

    def _paste_tri_picture(self, base, warped, Maff, dst, rel: str, tri: dict, kw: float, kh: float):
        """The landed picture as it is pasted, before the dissolve: (warped toned to her skin, alpha). A function of the
        frame, the tile and the dials only, so _paste_tri caches it beside the landing."""
        rec = self.lib.tile_head(rel) or {}
        if MOUTH_NECK_CUT:
            a_cut = self._neck_cut_alpha(warped, dst, rec, Maff, "tri", kw, kh)
        else:
            a_cut = square_alpha(warped, dst, nose_keep=True)
        if MOUTH_ALPHA == "jaw" or (MOUTH_ALPHA == "auto" and not _tile_carries_cheeks(rec) and _landed_tall(warped)):
            a_cut = jaw_alpha(a_cut, dst, rec, Maff, MOUTH_SCALE, maps=None)
        a_cut = (np.asarray(a_cut, dtype=np.float32) * tri["mask"]).astype(np.float32)
        if tri["core"] is not None:
            a_cut = np.maximum(a_cut, tri["core"]).astype(np.float32)
        if MOUTH_LIPS_SKIN:
            warped = (MP.match_skin_roi if MOUTH_SKIN_ROI else MP.match_skin)(warped, base, a_cut, tri["lips"],
                                   sigma_px=max(3.0, 0.08 * float(dst.get("s") or 100.0)))
        return warped, a_cut

    def _fold_lips(self, hw) -> "np.ndarray | None":
        """Her outer lip loop on this frame as a 0/255 mask at paint size, from the clip's lip marks (pitch fold)."""
        row = self.lib.rows.get(self.key) or {}
        lm = self.lib.lipmarks(str(row.get("file") or self.key))
        fr = (lm or {}).get("frames") or []
        if not (0 <= int(self.i) < len(fr)) or not isinstance(fr[int(self.i)], dict):
            return None
        lo = fr[int(self.i)].get("lips_outer")
        if not lo:
            return None
        k = float(hw[1]) / float((lm.get("wh") or [hw[1]])[0])
        return MP.fill(tuple(hw), np.asarray(lo, dtype=np.float64) * k)

    def _paste_head(self, base: np.ndarray, box, tile, lip, rel: str
                    ) -> np.ndarray | None:
        """Frame-to-frame warp. Path from clip-open prechoice; maps per frame."""
        raw = unpack_head(self.lib.head(self.key, self.i))
        dst = self._dest_for_paste(raw)
        src = unpack_head(self.lib.tile_head(rel))
        if dst is None or src is None:
            # SAY IT (the User 2026-09-12: "rest matches frame and is the only
            # performer … a chin or mouth pin ghost"): Lila's map had no
            # `head` on any tile, so this returned None and every class
            # fell through to the Haar-box paste while the dials read
            # nose-pixel. The fallback still runs, but the state names it.
            self.skip = ("no tile head (%s) — box paste" % Path(str(rel)).name) if src is None else "no dest head — box paste"
            return None
        cap = float(HEAD_GEOM.get("warp_cap_deg") or 0)
        if cap > 0 and ray_deg(dst["R"], src["R"]) > cap:
            self.skip = "warp cap %.0f° — box paste" % cap
            return None
        # CHEEKS + TRIANGLE: how far into a side turn this frame is. 0 = everything below, untouched.
        tri, tri_w = None, 0.0
        self._tri_mode = bool(MOUTH_TURN_TRI and TURN_TRI_ON)
        if self._tri_mode:
            tri_w = self._tri_weight()
            _yw = getattr(self, "_pl_yaw", None)
            if tri_w <= 0.0 and _yw is not None and abs(float(_yw)) > self._front_to() > 0.0:
                # BETWEEN THE TWO LANDINGS (the User 2026-10-02: "simply go no frame ... where the duck lips flash"):
                # turned further than any front loop goes and not yet at the hand-over, the Bezier landing puts a
                # front tile on a turning face. mouth-turn.json "gap": "filmed" (default) shows her filmed mouth
                # there; "lips" lands the loop's tile by her lips as before this warp existed; "front" leaves the
                # front landing on every frame, for tuning it by hand.
                _gap = str(self._turn_cfg().get("gap") or "filmed")
                if _gap == "lips":
                    self._tri_mode = False
                elif _gap == "tilt":
                    # THE LOOP'S OWN TILE, LANDED LIKE A TURN TILE (the User 2026-10-02, a shallow turn with the head
                    # down: "should be a frontal tilted"): by her lips and head size and turned to her angle, where
                    # the nose-pixel pin no longer takes. A frame it cannot be built on keeps the front landing.
                    _tl = self._tri_landing(tile, rel, raw, base, None)
                    if _tl is not None and _tl["dbg"].get("placed") in ("head", "bend"):
                        return self._paste_tri(base, box, tile, rel, _tl)
                elif _gap == "front":
                    pass                    # no patch: the front landing paints every frame up to the hand-over
                else:
                    self.skip = "between the front landing and the turn - her filmed mouth"
                    self._lip_dbg = {"frame": int(self.i), "placed": "filmed", "yaw": round(float(_yw), 2),
                                     "cell": getattr(self, "_pl_cell", None)}
                    return self._fade_out(base)
            if tri_w > 0.0:
                if getattr(self, "_pl_band", None):
                    tri = self._tri_landing(tile, rel, raw, base, self._pl_band)    # the playhead's tile IS the turn tile
                    if tri is not None and tri_w >= 1.0:
                        return self._paste_tri(base, box, tile, rel, tri)
                else:
                    _tt, _tr = self._tri_tile()
                    tri = (self._tri_landing(_tt, _tr, raw, base, self._tri_band(getattr(self, "_pl_yaw", None), front=0.0))
                           if _tt is not None else None)
                    if tri is not None and tri_w >= 1.0:
                        return self._paste_tri(base, box, _tt, _tr, tri)
                    if _tt is None and "tilt" in (self._turn_cfg().get("no_tile"), self._turn_cfg().get("gap")):
                        # no turn tile for this side (a face with no takes of it): the tilted front tile again
                        _tl = self._tri_landing(tile, rel, raw, base, None)
                        if _tl is not None and _tl["dbg"].get("placed") in ("head", "bend"):
                            return self._paste_tri(base, box, tile, rel, _tl)
        self._step("head_prep")               # heads, dest, the turn (tri) checks
        path = live_lookup(self._scene, getattr(self, "_pl_class", None), dst,
                           warp=MOUTH_WARP)
        maps = None
        Maff = None
        diamond_a = None
        if path == "cheeks":
            # solve + tile + pin, cached by dest frame (2026-09-14)
            maps, dst = self._cheeks_maps(src, dst, rel, tile, base)
            if maps is None:
                # REFUSED CHEEKS PAINT AS AFFINE, NOT THE TRIAD (the User 2026-09-13,
                # "Bezier on Crissa is how lila looked before"): a tile without
                # 2d106 cheeks (Crissa's pre-09-04 takes) cannot share the dest's
                # ruler, and the old fall-through was warp_triad_3x3 — a
                # perspective homography with no pixel pin that smears a
                # heads-down desk mouth down the chin. The law is the fallback.
                path = "affine"
                self.skip = "cheeks refused (tile cheeks off another ruler) — affine"
        if path in ("affine", "dental", "plane", "diamond"):
            if MOUTH_PIN == "nose-pixel" and not MOUTH_FIT_UNDER_PIXEL:
                # the rest tile's map: source axes kept, a translation the
                # pixel match places — no dest-track scale, no width fit
                dst_t = dict(dst)
                dst_t["u"], dst_t["v"], dst_t["s"] = src["u"], src["v"], src["s"]
                Maff = warp_affine_2x3(src, dst_t, scale=MOUTH_SCALE)
            else:
                Maff = warp_affine_2x3(src, dst, scale=MOUTH_SCALE)
            rec = self.lib.tile_head(rel) or {}
            tbox = rec.get("mouth_in_tile")
            if MOUTH_PIN == "nose-pixel" and not MOUTH_FIT_UNDER_PIXEL:
                pass
            elif MOUTH_FIT == "width":
                Maff = fit_mouth_width(Maff, tbox, dst.get("mouth"))
            elif MOUTH_FIT == "take":
                Maff = apply_width_k(Maff, self._take_width_k(rel, dst), tbox)
            elif MOUTH_FIT == "take-uni":
                Maff = apply_uniform_k(Maff, self._take_width_k(rel, dst), tbox)
            if MOUTH_PIN == "nose-pixel":
                Maff, dst = self._pixel_pin(Maff, tile, src, dst, base, rel)
                self._place_dbg = self._placement(Maff, tbox, dst)
            elif MOUTH_PIN == "nose+lip":
                Maff, _k = pin_nose_and_lip(Maff, tbox, dst)
                self._pin_dbg = {"ncc": 0.0, "took": False, "pixel": [0.0, 0.0],
                                 "frame": int(getattr(self, "i", -1)), "path": path,
                                 "how": "nose+lip (landmarks, scaled along v)"}
                self._place_dbg = None
            elif MOUTH_PIN == "lip":
                Maff, (pdx, pdy) = pin_upper_lip(Maff, tbox, dst)
                if pdx or pdy:
                    # hole + alpha + dental follow the same shift
                    dst = dict(dst)
                    dst["nose"] = np.asarray(dst["nose"], dtype=np.float64).ravel()[:2] + np.array([pdx, pdy])
                # SAY WHICH PAINT THIS RECORD IS (2026-09-16): the affine lip
                # / nose pins wrote no record, so pin_match kept the last
                # cheeks or pixel match ("pin ncc 0 on cheeks" under affine)
                self._pin_dbg = {"ncc": 0.0, "took": bool(pdx or pdy),
                                 "pixel": [round(float(pdx), 1), round(float(pdy), 1)],
                                 "frame": int(getattr(self, "i", -1)), "path": path, "how": "lip"}
                self._place_dbg = None
            else:
                self._pin_dbg = {"ncc": 0.0, "took": False, "pixel": [0.0, 0.0],
                                 "frame": int(getattr(self, "i", -1)), "path": path,
                                 "how": "landmarks (pin=%s)" % MOUTH_PIN}
                self._place_dbg = None
            if path == "dental":
                maps = self._dental_paste_maps(src, dst, Maff, tile, raw=raw)
            elif path == "plane":
                maps = warp_dental_plane_maps(src, dst, paint_wh(), M=Maff,
                                              scale=MOUTH_SCALE,
                                              tile_hw=tile.shape[:2])
            elif path == "diamond":
                # CACHED (2026-09-15): the builder is 61.3 ms a paint against a
                # 16 ms budget at 60 fps — it was the one warp with no cache, so
                # the mode was selectable but could not hold frame rate. A loop
                # under tuning replays the same frames, so this warms in a pass.
                _vis = getattr(self, "_pl_class", None)
                _rest = self._scene.rest if self._scene is not None else None

                def _build_diamond():
                    return warp_diamond_maps(src, dst, paint_wh(), M=Maff,
                                             scale=MOUTH_SCALE,
                                             tile_hw=tile.shape[:2],
                                             viseme=_vis, dst_rest=_rest)

                maps = diamond_maps_cached(
                    loop_id=self.key, frame_i=int(self.i), version=self._loop_ver(),
                    rel=rel, dst=dst, wh=paint_wh(), scale=MOUTH_SCALE,
                    tile_hw=tile.shape[:2], viseme=_vis, m_key=m_key_of(Maff),
                    build=_build_diamond)
        self._step("maps")                    # cheeks / dental / diamond warp maps (solve or cache)
        maps, Maff, dst, path, _pin_shift = self._lips_and_pins(maps, Maff, src, dst, path, rel)
        self._step("pins")
        if maps is not None:
            mx, my = maps[0], maps[1]
            warped = cv2.remap(tile, mx, my, cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            if path == "diamond" and len(maps) > 2:
                diamond_a = maps[2]
        elif Maff is not None:
            warped = cv2.warpAffine(tile, Maff, paint_wh(), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        else:
            # THE TRIAD PATH HAS NO ATTACHMENTS, MEASURED AND LEFT THAT WAY
            # (2026-09-15). choose_path sends every mode except cheeks here at
            # |yaw| >= 35° or a dest mouth box under 8 px, and there is no
            # pixel pin, no fit, no lip and no sag on it.
            #
            # The plan was to give it the pin the cheeks path got on 09-14:
            # measure on the affine landing of the same tile and translate H
            # once. bench/triad-pin-20260915/measure.py measured that first,
            # because the 09-14 take-rate evidence is all from low-yaw cheeks
            # frames: 0 of 2742 attempts over 457 high-yaw frames reached the
            # 0.90 bar (ncc median 0.199, p90 0.476) against 64 % at low yaw
            # (median 0.918, shift median 1.0 px). Past 35° the nose patch is
            # not in the frame to be matched, and translating a homography by
            # a 0.2-ncc "match" is worse than not translating it.
            #
            # So: no pin here. But the state must SAY no pin, or pin_match
            # keeps reporting the last non-triad value — the same lie the
            # 09-14 cheeks fix closed.
            self._pin_dbg = {"ncc": 0.0, "took": False, "pixel": [0.0, 0.0],
                             "frame": int(getattr(self, "i", -1)), "path": "triad",
                             "how": "landmarks (no pin on triad: 0/2742 matched past 35°)"}
            self._place_dbg = None
            H = warp_triad_3x3(src, dst, scale=MOUTH_SCALE)
            if _pin_shift is not None:
                H = np.array([[1, 0, _pin_shift[0]], [0, 1, _pin_shift[1]], [0, 0, 1]], dtype=np.float64) @ H
            warped = cv2.warpPerspective(tile, H, paint_wh(), flags=cv2.INTER_LINEAR,
                                         borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        kw = float(HEAD_GEOM.get("kw") or 0.28)
        kh = float(HEAD_GEOM.get("kh") or 0.18)
        if diamond_a is not None:
            return composite_stamp(
                base, box, warped, dst, a=diamond_a, hole="strip",
                tone=PLAYLIST_TONE, kw=kw, kh=kh, scale=MOUTH_SCALE)
        hole = self._hole()
        self._step("remap")
        a_cut = None
        if MOUTH_NECK_CUT:
            a_cut = self._neck_cut_alpha(warped, dst, self.lib.tile_head(rel) or {},
                                         Maff, path, kw, kh)
        else:
            a_cut = square_alpha(warped, dst, nose_keep=(path != "affine"))
        # `jaw` is still the hand setting and still means jaw. `auto` asks the
        # tile first: a face-scaled one paints square however tall it lands.
        # CHEAP TEST FIRST (profiled 2026-09-20: this was 19% of the whole
        # composite). _landed_tall does a full-frame max over 736x400x3 on every
        # paint; _tile_carries_cheeks is a dict lookup that returns True for all
        # 114 tiles the live playlists pick, which discarded the expensive answer
        # anyway. Ordered this way the max never runs on a face-scaled tile.
        jaw_on = MOUTH_ALPHA == "jaw" or (
            MOUTH_ALPHA == "auto"
            and not _tile_carries_cheeks(self.lib.tile_head(rel))
            and _landed_tall(warped))
        if jaw_on:
            a_cut = jaw_alpha(a_cut, dst, self.lib.tile_head(rel) or {}, Maff, MOUTH_SCALE,
                              maps=(maps if path in ("cheeks", "dental", "plane") else None))
        _fk = self._front_keep(rel) if (a_cut is not None and tri is None and path != "lips") else None
        _fk_hole = None
        _cc_on = a_cut is not None and tri is None and path != "lips" and (_fk is None or _fk[2].get("jaw"))
        # the cuts' box (MOUTH_CUT_ROI): the patch's live alpha, grown past the jaw feather's reach so its blur is exact
        _rb = None
        if MOUTH_CUT_ROI and a_cut is not None and (_fk is not None or (_cc_on and isinstance(self._turn_cfg().get("chin_cut"), dict))):
            _pad = (int(np.ceil(4.0 * float(_fk[2]["fe"]))) + 2) if _fk is not None else 0
            _lb = live_bbox(np.asarray(a_cut) > 0, pad=_pad)
            if _lb is not None:
                _rb = (_lb[1], _lb[3], _lb[0], _lb[2])
        _R = (slice(_rb[0], _rb[1]), slice(_rb[2], _rb[3])) if _rb is not None else (slice(None), slice(None))
        _ox, _oy = (_rb[2], _rb[0]) if _rb is not None else (0, 0)
        # under the jaw cut the chin cut's taper still narrows the patch toward her chin (the tile's own cheek, lit by
        # another take, stays off hers); past front_cut's angle the profile cut replaces it as before
        _cc = self._chin_keep(rel, dst, _rb) if _cc_on else None
        if _cc is not None:
            if _rb is not None:
                a_cut = np.array(a_cut, dtype=np.float32)
                a_cut[_R] *= _cc
            else:
                a_cut = (np.asarray(a_cut, dtype=np.float32) * _cc).astype(np.float32)
        if _fk is not None:
            _afull = np.array(a_cut, dtype=np.float32)
            _a0 = _afull[_R].copy()
            _afull[_R] = _a0 * _fk[0][_R]
            a_cut = _afull
            # ...and inside the TILE's own face outline, landed through the same map as its picture: the edge of the
            # tile's crop otherwise lands inside her cheek as a bright slash (the same flourish, the same day)
            try:
                _th = self.lib.tile_hull(rel)
                if _th:
                    _tm = MP.fill(tuple(tile.shape[:2]), _th)
                    if _tm is not None:
                        _e = max(1, int(round(MOUTH_LIPS_FEATHER_S * float(dst.get("s") or 100.0) * _edge_ratio())))
                        _tm = cv2.erode(np.asarray(_tm, dtype=np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * _e + 1, 2 * _e + 1)))
                        _tm = cv2.GaussianBlur(_tm.astype(np.float32) / 255.0, (0, 0), max(0.5, _e / 2.0))
                        if maps is not None:
                            _tl = cv2.remap(_tm, np.ascontiguousarray(maps[0][_R]), np.ascontiguousarray(maps[1][_R]), cv2.INTER_LINEAR,
                                            borderMode=cv2.BORDER_CONSTANT, borderValue=0)
                        elif _rb is not None:
                            _Mr = np.array(Maff, dtype=np.float64).copy()
                            _Mr[0, 2] -= _ox
                            _Mr[1, 2] -= _oy
                            _tl = cv2.warpAffine(_tm, _Mr, (_rb[3] - _rb[2], _rb[1] - _rb[0]), flags=cv2.INTER_LINEAR,
                                                 borderMode=cv2.BORDER_CONSTANT, borderValue=0)
                        else:
                            _tl = cv2.warpAffine(_tm, Maff, paint_wh(), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
                        _tl = np.asarray(_tl, dtype=np.float32)
                        # AN OPEN MOUTH DRAWS ITS WHOLE CHIN (the User 2026-10-18: "can we get that bottom left drawn?" - her
                        # profile line is her filmed, closed chin, and it cut an open jaw off in a hard corner). Below her upper
                        # lip the patch may reach a little past her profile (front_cut "reach", x head size) and a jaw-drop
                        # past her chin ("drop"), and only where the tile has face. The tile's own outline is NOT the limit
                        # there: a front tile's jaw is wider than her profile and landed on the wall.
                        _ex = _fk[2]
                        _fcfg = self._turn_cfg().get("front_cut") or {}
                        _rch = float(_fcfg.get("reach", 0.04)) * _ex["s"]
                        _drp = float(_fcfg.get("drop", 0.15)) * _ex["s"]
                        _fr, _dn = _ex["front"], _ex["down"]
                        _back = 0.6 * _ex["s"]
                        if _ex.get("jaw"):
                            # a frontal face drops its jaw straight down, both sides alike: from her lower lip (the chin cut's
                            # lip half-width) to a jaw-drop under her chin (its chin half-width)
                            _cc0 = self._turn_cfg().get("chin_cut") or {}
                            _pp = np.array([-_dn[1], _dn[0]], dtype=np.float64)
                            _wl = float(_cc0.get("lip_half", 0.5)) * _ex["s"]
                            _wc = float(_cc0.get("chin_half", 0.22)) * _ex["s"]
                            _lo = _ex["chin"] + _drp * _dn
                            _al = np.array([_ex["bot"] + _wl * _pp, _lo + _wc * _pp, _lo - _wc * _pp, _ex["bot"] - _wl * _pp],
                                           dtype=np.float64)
                        else:
                            _al = np.array([_ex["top"], _ex["bot"] + _rch * _fr, _ex["chin"] + _rch * _fr + _drp * _dn,
                                            _ex["chin"] + _drp * _dn - _back * _fr, _ex["top"] - _back * _fr], dtype=np.float64)
                        _am = np.zeros(_tl.shape[:2], dtype=np.uint8)
                        cv2.fillPoly(_am, [np.round(_al - np.array([_ox, _oy], dtype=np.float64)).astype(np.int32)], 255)
                        _ys, _xs = np.nonzero(_am)
                        _af = np.zeros(_tl.shape[:2], dtype=np.float32)
                        if len(_ys):
                            _g = int(np.ceil(4.0 * _ex["fe"])) + 1
                            _y0, _y1 = max(0, _ys.min() - _g), min(_am.shape[0], _ys.max() + 1 + _g)
                            _x0, _x1 = max(0, _xs.min() - _g), min(_am.shape[1], _xs.max() + 1 + _g)
                            _af[_y0:_y1, _x0:_x1] = cv2.GaussianBlur(_am[_y0:_y1, _x0:_x1].astype(np.float32) / 255.0, (0, 0), _ex["fe"])
                        _afull = np.array(a_cut, dtype=np.float32)
                        _afull[_R] = (_a0 * np.maximum(_fk[0][_R], _af) * _tl).astype(np.float32)
                        a_cut = _afull
                        _fk_hole = np.array(_fk[1], dtype=np.uint8)
                        _fk_hole[_R] = cv2.bitwise_or(_fk_hole[_R], cv2.bitwise_and(_am, ((_tl > 0.5) * 255).astype(np.uint8)))
            except (AttributeError, IndexError, TypeError, ValueError, cv2.error):
                pass
        if tri is not None and a_cut is not None:
            # the short blend into the turn: the front landing and the triangle's, pictures and alphas mixed by the angle
            rec_t = self.lib.tile_head(tri["rel"]) or {}
            if MOUTH_NECK_CUT:
                a_t = self._neck_cut_alpha(tri["warped"], tri["dst"], rec_t, tri["M"], "tri", kw, kh)
            else:
                a_t = square_alpha(tri["warped"], tri["dst"], nose_keep=True)
            a_t = np.asarray(a_t, dtype=np.float32) * tri["mask"]
            if tri["core"] is not None:
                a_t = np.maximum(a_t, tri["core"])
            a_s = np.asarray(a_cut, dtype=np.float32)
            both = a_s[..., None] * (1.0 - tri_w) + a_t[..., None] * tri_w
            warped = np.clip((warped.astype(np.float32) * a_s[..., None] * (1.0 - tri_w)
                              + tri["warped"].astype(np.float32) * a_t[..., None] * tri_w)
                             / np.maximum(both, 1e-6), 0, 255).astype(np.uint8)
            a_cut = both[..., 0].astype(np.float32)
            _ld = dict(getattr(self, "_lip_dbg", None) or {})
            _ld.update(tri_w=round(float(tri_w), 3), placed="%s+tri" % path)
            self._lip_dbg = _ld
            _late = tri_w >= 0.5
            if _late:
                dst = tri["dst"]
            if MOUTH_LIPS_SKIN:
                warped = (MP.match_skin_roi if MOUTH_SKIN_ROI else MP.match_skin)(warped, base, a_cut, tri["lips"],
                                       sigma_px=max(3.0, 0.08 * float(dst.get("s") or 100.0)))
            warped, a_cut = self._xfade(rel, warped, a_cut)
            _hb = float(getattr(self, "_headback_k", 1.0))
            if _hb < 1.0 and a_cut is not None:     # the head-back fade: see-through, the hole only where it paints
                a_cut = (np.asarray(a_cut, dtype=np.float32) * _hb).astype(np.float32)
            self._step("alpha_blend")
            _out = composite_stamp(
                base, box, warped, dst, a=a_cut, hole=hole, tone=PLAYLIST_TONE,
                nose_keep=(path != "affine"), kw=kw, kh=kh, scale=MOUTH_SCALE,
                cover_alpha=(jaw_on or _late or _hb < 1.0), hole_within=(tri["hole"] if _late else None))
            self._step("stamp")
            return _out
        _mask = getattr(self, "_lip_mask", None) if path == "lips" else None
        _turn_skin = _mask is not None and MOUTH_LIPS_SKIN and getattr(self, "_lip_turned", False)
        if _turn_skin:
            warped = (MP.match_skin_roi if MOUTH_SKIN_ROI else MP.match_skin)(warped, base, _mask, getattr(self, "_lip_lips", None),
                                   sigma_px=max(3.0, 0.08 * float(dst.get("s") or 100.0)))
        elif self._turn_cfg().get("front_skin") is True and a_cut is not None and tri is None:
            # THE FRONT PATCH TAKES HER LIGHT (the User 2026-10-18, a front tile from another take on a flourish: lighter
            # and softer than her skin, so wherever its edge fell - the neck cut, a clipped corner - it showed as a band,
            # and its jaw shading read as a fuller jaw). mouth-turn.json {"front_skin": true}: the pitch fold's match,
            # measured outside her own lip loop so her lips keep their red.
            _fl = self._fold_lips(base.shape[:2])
            if _fl is not None:
                # cached by (frame, tile, dials) like a turn landing (the User 2026-10-18: it cost 40 ms a paint on the
                # bench, below the semiframe rate); a function of exactly those, so a hit is the same picture
                _w0, _a0s = warped, a_cut
                _sk = self._tri_key("skin", rel, None)
                _sigma = max(3.0, 0.08 * float(dst.get("s") or 100.0))
                if _sk is not None:
                    warped = tri_cached(_sk, paint_wh()[::-1], lambda: {"w": MP.match_skin_roi(_w0, base, _a0s, _fl, sigma_px=_sigma)})["w"]
                else:
                    warped = MP.match_skin_roi(warped, base, a_cut, _fl, sigma_px=_sigma)
        elif PITCH_FOLD_SKIN and getattr(self, "_pl_fold", False) and a_cut is not None:
            # A FOLDED TILE TAKES HER LIGHT (the User 2026-10-16, "build the colour match for the pitch fold"): the desk
            # take's warm light rode the tile onto her chin on listen-back. Her skin's per-channel light, measured outside
            # her own lip loop, is carried onto the tile (the turn's match_skin); her lips keep their red. No lip marks =
            # no match (a lip loop measured as skin would drain her lips). Kill HOUSE_PITCH_FOLD_SKIN=0.
            _fl = self._fold_lips(base.shape[:2])
            if _fl is not None:
                warped = MP.match_skin_roi(warped, base, a_cut, _fl,
                                           sigma_px=max(3.0, 0.08 * float(dst.get("s") or 100.0)))
        if _mask is not None and a_cut is not None:
            # the face mask: the tile paints inside both face outlines only, and the hole under it is
            # erased only where it paints (cover_alpha), so no inpaint shows on the wall past her edge
            a_cut = (np.asarray(a_cut, dtype=np.float32) * _mask).astype(np.float32)
        warped, a_cut = self._xfade(rel, warped, a_cut)
        _hb = float(getattr(self, "_headback_k", 1.0))
        if _hb < 1.0 and a_cut is not None:     # the head-back fade: see-through, the hole only where it paints
            a_cut = (np.asarray(a_cut, dtype=np.float32) * _hb).astype(np.float32)
        self._step("alpha_blend")
        _out = composite_stamp(
            base, box, warped, dst, a=a_cut, hole=hole, tone=PLAYLIST_TONE,
            nose_keep=(path != "affine"), kw=kw, kh=kh, scale=MOUTH_SCALE,
            cover_alpha=(jaw_on or _mask is not None or _hb < 1.0 or _fk is not None or _cc is not None),
            hole_within=(getattr(self, "_lip_hole", None) if _mask is not None else
                         (_fk_hole if _fk_hole is not None else (_fk[1] if _fk is not None else None))))
        self._step("stamp")
        return _out

    def _patch_epoch(self, rel: str):
        """The library files a paint reads, as stats: the clip's own sidecars and the tile's, the face's mouth-turn.json
        and its lock. Re-listed only when the face's folder or its visemes folder changes (an atomic write renames)."""
        bd = bank_dir(self.key)
        try:
            dm = (bd.stat().st_mtime_ns, (bd / "visemes").stat().st_mtime_ns)
        except OSError:
            dm = None
        ck = (self.key, rel, dm)
        got = self.__dict__.get("_patch_epoch_c")
        if got is not None and got[0] == ck:
            return got[1]
        row = self.lib.rows.get(self.key) or {}
        stem = Path(str(row.get("file") or self.key)).stem
        out = []
        for f in sorted(bd.glob(stem + ".*")) + [bd / "mouth-turn.json", bd / "mouth-dials.json", bd / str(rel),
                                                 POSE_DIR / str(rel)]:
            try:
                st = f.stat()
                out.append((f.name, int(st.st_mtime_ns), int(st.st_size)))
            except OSError:
                out.append((f.name,))
        self._patch_epoch_c = (ck, tuple(out))
        return self._patch_epoch_c[1]

    def _patch_key(self, base, box, tile, lip, rel) -> bytes:
        """Everything a head paint reads, as one key: the film frame and its version, the tile, its record, the library
        files (_patch_epoch), the frame's playlist row (class, angle, cell, band, fold), the head-back fade, the turn
        config and every plain setting of this module (the MOUTH_* dials with the rest)."""
        g = globals()
        dials = tuple((n, g.get(n)) for n in _patch_dial_names())
        return mouth_patch.digest(
            "v2", mouth_patch.code_print(), self.key, int(self.i), self._loop_ver(), str(rel), tuple(tile.shape),
            tuple(base.shape), self.lib.tile_head(rel) or {}, self.lib.head(self.key, self.i) or {}, box, lip,
            self._patch_epoch(rel), self._turn_cfg(), dials, PLAYLIST_TONE, HEAD_GEOM,
            getattr(self, "_pl_class", None), getattr(self, "_pl_yaw", None), getattr(self, "_pl_cell", None),
            getattr(self, "_pl_band", None), getattr(self, "_pl_fold", None), float(getattr(self, "_headback_k", 1.0)))

    def _paste_head_kept(self, base: np.ndarray, box, tile, lip, rel: str
                         ) -> np.ndarray | None:
        """The head paint through the finished-patch store, dissolve included (2026-10-25, .101's paint-budget ask
        for Tuesday: the kit's lock has xfade_ms 20.833 and the store was bypassed under any dissolve, so on an install
        it did nothing). A tile switch is blended from TWO kept frames: the outgoing tile and the incoming one, each
        painted on THIS film frame (the User's rule: pixels fade, the tile follows the pin - no carried landing). The
        premultiplied mix the live dissolve stamps (pose_mouth.xfade_mix + alpha-over) IS the per-pixel lerp of the two
        finished frames, so the blend is the same picture at a copy's cost. The fade-out of a tile with no successor is
        _fade_out's, from the same kept frame. Kill: the store's own (HOUSE_MOUTH_PATCH_CACHE=0)."""
        if not _MOUTH_PATCH_ON:
            return self._paste_head(base, box, tile, lip, rel)
        now = time.time()
        prev = self._xf_now
        out = None
        if MOUTH_XFADE_MS > 0.0:
            # the same switch rule as _xfade: a new tile within 0.5 s of the last paint fades from it
            if prev is not None and prev.get("rel") != rel and (now - float(prev.get("t") or now)) < 0.5:
                self._xfk = (prev.get("rel"), now, prev.get("klass"), prev.get("cell"))
            elif prev is None or (now - float(prev.get("t") or now)) >= 0.5:
                self._xfk = None
            xfk = getattr(self, "_xfk", None)
            if xfk is not None and xfk[0] and xfk[0] != rel:
                t = (now - float(xfk[1])) / (MOUTH_XFADE_MS / 1000.0 / max(0.05, float(PLAY_RATE)))
                if t >= 1.0:
                    self._xfk = None
                else:
                    pair = self.lib.playlist_tile(xfk[0])
                    # the outgoing tile is painted under ITS OWN class: the key carries _pl_class, and the build keyed
                    # that tile under the class it serves (measured 10-25: 13 of 13 misses on Close were this)
                    f_out = (self._kept_frame_as(base, box, pair[0], pair[1], xfk[0], xfk[2] if len(xfk) > 2 else None,
                                                 xfk[3] if len(xfk) > 3 else None) if pair else None)
                    f_in = self._kept_frame(base, box, tile, lip, rel)       # painted last: its side record stands
                    if f_out is not None and f_in is not None:
                        out = mouth_patch.lerp_frames(base, f_out, f_in, xfade_ease(t))
                    else:
                        out = f_in
        if out is None:
            out = self._kept_frame(base, box, tile, lip, rel)
        if out is not None:
            self._xf_now = {"rel": rel, "warped": None, "a": None, "t": now, "klass": getattr(self, "_pl_class", None),
                            "cell": getattr(self, "_pl_cell", None), "pts": self._follow_pts() if XFADE_FOLLOW else None}
            self._xf_from = None
        return out

    def _kept_frame_as(self, base, box, tile, lip, rel, klass, cell=None):
        """_kept_frame with the playhead's class AND cell set to the tile's own for the paint (put back after): the key
        carries both, and the per-cell pin table reads the cell (measured 10-25: with the class alone carried, 134 of
        134 misses on Close were the cell)."""
        _wc, _wcell = getattr(self, "_pl_class", None), getattr(self, "_pl_cell", None)
        if (klass is None or klass == _wc) and (cell is None or cell == _wcell):
            return self._kept_frame(base, box, tile, lip, rel)
        if klass is not None:
            self._pl_class = klass
        if cell is not None:
            self._pl_cell = cell
        try:
            return self._kept_frame(base, box, tile, lip, rel)
        finally:
            self._pl_class, self._pl_cell = _wc, _wcell

    def _kept_frame(self, base: np.ndarray, box, tile, lip, rel: str
                         ) -> np.ndarray | None:
        """ONE STEADY PAINT through the finished-patch store (lib/mouth_patch): a key this process or the install's
        build painted before is copied back; anything else is painted (_paste_head, with the dissolve helper stepped
        aside) and kept, unless it went through a stateful branch (a turn, a seek mid-paint). Never reads or writes
        the dissolve state: _paste_head_kept does."""
        try:
            pk = self._patch_key(base, box, tile, lip, rel)
            got = mouth_patch.take(pk, base)
        except Exception:  # noqa: BLE001 - the patch store never breaks a paint
            pk, got = None, None
        if _PATCH_TRACE and pk is not None:
            # one line per take (HOUSE_MOUTH_PATCH_TRACE=<file>, 2026-10-25): the glass's misses against the build's
            # enumeration, by (frame, class, tile); never on by default
            try:
                with open(_PATCH_TRACE, "a", encoding="utf-8") as _tf:
                    _tf.write("%s %d %s %s %d %s %s %s %s %s %s %s %.3f\n" % (
                        "hit" if got is not None else "miss", os.getpid(), pk.hex()[:12], self.key, int(self.i),
                        getattr(self, "_pl_class", None), rel, getattr(self, "_pl_yaw", None), getattr(self, "_pl_cell", None),
                        getattr(self, "_pl_band", None), getattr(self, "_pl_fold", None), self._loop_ver(),
                        float(getattr(self, "_headback_k", 1.0))))
            except OSError:
                pass
        if got is not None and not _MOUTH_PATCH_VERIFY:
            out, side = got
            for k, v in (side or {}).items():
                if not k.startswith("_dbg"):
                    setattr(self, k, v)
            self._lip_mask = self._lip_hole = self._lip_lips = None
            self._ahead_plan()
            self._step("patch")
            return out
        self._patch_unsafe = False
        at = (self.key, int(self.i))
        self._xf_bypass = True
        try:
            out = self._paste_head(base, box, tile, lip, rel)
        finally:
            self._xf_bypass = False
        if pk is not None and out is not None:
            try:
                # a play or seek from another thread moved the playhead mid-paint: that picture mixes two frames
                if self._patch_unsafe or (self.key, int(self.i)) != at:
                    mouth_patch.stats["unsafe"] += 1
                else:
                    if got is not None:
                        _same = np.array_equal(got[0], out)
                        mouth_patch.stats["verify_ok" if _same else "verify_bad"] += 1
                        if not _same:
                            _d = np.abs(got[0].astype(np.int16) - out.astype(np.int16))
                            _ys, _xs = np.nonzero(_d.max(axis=2) if _d.ndim == 3 else _d)
                            print("mouth patch VERIFY MISMATCH frame %s %s class %s: %d px, max %d, box y %d-%d x %d-%d, "
                                  "side then %s now %s" % (self.i, rel, getattr(self, "_pl_class", None), len(_ys), int(_d.max()),
                                  _ys.min(), _ys.max(), _xs.min(), _xs.max(), got[1], self._patch_side()), flush=True)
                            print("mouth patch VERIFY base same: %s" % ((got[1] or {}).get("_dbg_base") ==
                                  mouth_patch._base_digest(base, (0, base.shape[0], 0, base.shape[1]))), flush=True)
                    _sd = self._patch_side()
                    if _MOUTH_PATCH_VERIFY:
                        _sd["_dbg_base"] = mouth_patch._base_digest(base, (0, base.shape[0], 0, base.shape[1]))
                    mouth_patch.put(pk, base, out, _sd)
            except Exception:  # noqa: BLE001
                pass
        return out

    def _patch_side(self) -> dict:
        out = {}
        for k in _PATCH_SIDE:
            v = getattr(self, k, None)
            out[k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v
        return out

    def _paste_playlist(self, base: np.ndarray, box, tile, lip,
                        roll_deg: float = 0.0, rec: dict | None = None) -> np.ndarray | None:
        """Native harvested crop onto this frame's mouth. Pad matches harvest.

        TALLER THAN WIDE. the User, 2026-09-01: *"Double nose need taller."* The
        harvested crop reaches above the lip and carries the bottom of her
        nose; pasted at the harvest pad it lands BESIDE her real nose instead
        of over it, and you see both. PLAYLIST_PAD_Y covers hers.

        ROLL. Her head tilts 12.6 degrees across a take and the tile was filmed
        at whatever roll it was filmed at. Rotating by the DIFFERENCE costs one
        warpAffine and needs no extra bank — the same tile, turned. A missing
        roll on either side means no rotation, never a guessed one.
        """
        x, y, w, h = box
        if abs(roll_deg) >= 0.25:
            th, tw = tile.shape[:2]
            M = cv2.getRotationMatrix2D((tw / 2.0, th / 2.0), roll_deg, 1.0)
            tile = cv2.warpAffine(tile, M, (tw, th), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE)
            lip = cv2.warpAffine(lip, M, (tw, th), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        pw = int(round(w * (1.0 + 2.0 * PLAYLIST_PAD) * MOUTH_SCALE))
        # KEEP THE TILE'S OWN SHAPE. Taking BOTH dimensions from the target box
        # throws the tile's aspect away, and the mouth box aspect is different
        # on every clip: 1.70 on clip-speak-phoneme (where every tile the User
        # picked was cut), 2.15 on listen-back, 3.28 on work. A tile pasted onto
        # the desk was therefore squashed 1.93x horizontally — the User, 2026-09-01:
        # "still squat on her face. Tiny jaw stamp."
        #
        # Width is the anchor because mouth WIDTH is the stable anatomical
        # measure across takes; height then follows the tile. A taller patch is
        # safe: the lip matte decides what actually paints, and it covers the
        # inpainted hole better rather than leaving a smudge around it.
        th_, tw_ = tile.shape[:2]
        if PLAYLIST_KEEP_ASPECT and tw_ > 0 and th_ > 0:
            ph = int(round(pw * (float(th_) / float(tw_))))
        else:
            ph = int(round(h * (1.0 + 2.0 * PLAYLIST_PAD_Y) * MOUTH_SCALE))
        X = int(round(x + w / 2.0 - pw / 2.0))
        Y = int(round(y + h / 2.0 - ph / 2.0))
        # A FACE-SCALED TILE IS NOT MOUTH + PAD (2026-09-19). Its width is her
        # face's, so mouth×(1+2·PAD) would land its mouth ~0.6× too small. Size
        # it by its own mouth box (head.mouth_in_tile) and put that mouth on
        # this frame's box.
        mit = (rec or {}).get("mouth_in_tile")
        if mit is not None and float(mit[2]) > 1.0 and tw_ > 0:
            k = float(w) * MOUTH_SCALE / float(mit[2])
            pw, ph = int(round(tw_ * k)), int(round(th_ * k))
            X = int(round(x + w / 2.0 - (float(mit[0]) + float(mit[2]) / 2.0) * k))
            Y = int(round(y + h / 2.0 - (float(mit[1]) + float(mit[3]) / 2.0) * k))
        if pw < 2 or ph < 2:
            return None
        X0, Y0 = max(0, X), max(0, Y)
        X1, Y1 = min(paint_wh()[0], X + pw), min(paint_wh()[1], Y + ph)
        if X1 - X0 < 2 or Y1 - Y0 < 2:
            return None
        # Same dest hole as the live stamp when the frame has a head. Haar
        # alpha below is the rollback, not a second kit.
        dst_h = self._dest_for_paste(unpack_head(self.lib.head(self.key, self.i)))
        if dst_h is not None:
            kw = float(HEAD_GEOM.get("kw") or 0.28)
            kh = float(HEAD_GEOM.get("kh") or 0.18)
            plane = inpaint_plane(dst_h, kw, kh, MOUTH_SCALE)
            arch = (fit_arch(dst_h, kw, kh, MOUTH_SCALE)
                    if self._hole() == "arch" else None)
            out = hide_clip_jaw(base, (x, y, w, h), plane=plane,
                                hole=("arch" if arch is not None else "ellipse"),
                                arch=arch)
        else:
            out = hide_clip_jaw(base, (x, y, w, h))
        patch = cv2.resize(tile, (pw, ph), interpolation=cv2.INTER_LINEAR).astype(np.float32)
        a_lip = cv2.resize(lip, (pw, ph), interpolation=cv2.INTER_LINEAR)
        patch = patch[Y0 - Y:Y1 - Y, X0 - X:X1 - X]
        a_lip = a_lip[Y0 - Y:Y1 - Y, X0 - X:X1 - X]
        dst = out[Y0:Y1, X0:X1].astype(np.float32)
        bh, bw = dst.shape[:2]
        dm = float(dst.mean()) or 1.0
        pm = float(patch.mean()) or 1.0
        patch = np.clip(patch * max(0.5, min(2.0, dm / pm)), 0, 255)
        patch = tone_b(patch, dst, a_lip)
        yy, xx = np.mgrid[0:bh, 0:bw]
        r = np.sqrt(((xx - bw / 2) / max(bw / 2, 1)) ** 2 + ((yy - bh / 2) / max(bh / 2, 1)) ** 2)
        ell = np.clip((1.0 - r) / (1.0 - MOUTH_CORE), 0, 1)
        a = np.maximum(a_lip, ell * 0.35)
        a = (np.clip(a, 0, 1))[..., None]
        out[Y0:Y1, X0:X1] = (dst * (1 - a) + patch * a).astype(np.uint8)
        return out

    def head_alpha(self, box, dt: float) -> float:
        """How much of a pasted mouth survives while her head is moving, and
        the tracking that decides it. Called by BOTH painters — it used to live
        inside the strip path, below the neural branch, so W2L never consulted
        it and `self.vel` was a frame stale whenever W2L was the one painting.

        WHY IT MATTERS MORE FOR W2L. The neural mouth is generated on an 8 fps
        grid against a 24 fps wall, so each one is HELD for three frames —
        measured live, 21 holds against 9 gens, about 70% of painted frames.
        A held mouth is drawn where her jaw WAS up to 125 ms ago while her real
        jaw is where it is now, at full opacity. That is two jaws at once, which
        is what the User saw: "why are both jaws showing".
        """
        # ONCE PER FRAME, whoever asks. Three branches consult this now, and
        # on a W2L miss the code falls through to the strip and would ask
        # again — advancing the EMA twice against a last_box it had just set,
        # which reads as zero movement and quietly under-estimates the fade.
        stamp = (self.key, self.i)
        if self._alpha_at == stamp:
            return self._alpha
        x, y, w, h = box
        cx, cy = x + w / 2.0, y + h / 2.0
        if self.last_box is not None:
            d = ((cx - self.last_box[0]) ** 2 + (cy - self.last_box[1]) ** 2) ** 0.5
            # Per clip-frame, not per mouth JPEG. Holding a body frame at 48 Hz
            # would otherwise double jaw velocity and fade the paste.
            v = (d / max(1.0, w)) * CLIP_FPS
            self.vel += (v - self.vel) * 0.35
        self.last_box = (cx, cy)
        if self.vel <= VEL_LO:
            a = 1.0
        else:
            a = max(0.0, 1.0 - (self.vel - VEL_LO) / max(0.01, VEL_HI - VEL_LO))
        self._alpha_at, self._alpha = stamp, a
        return a

    def _composite(self, base: np.ndarray, dt: float) -> np.ndarray:
        global W2L_MS, W2L_MISSES, W2L_GENS, W2L_HOLDS
        lvl = self.mouth.tick(dt)
        # The voice on the air holds fidgets. A Composer viseme hold is not a line.
        on_air = bool(self.mouth.painting() and self.mouth.force is None)
        # A READING'S PAGES ARE ONE TURN (round 23, the User 2026-10-15, .101: the bag dealt a yawn and stretches in the
        # 0.9-1.4 s between her reading's pages): SPEECH_TAIL_HOLD_S after a line still counts as on air. 0 (the house)
        # = as before; a pack sets it in config/crissa-mouth.json.
        if on_air:
            self._air_last = time.time()
        elif SPEECH_TAIL_HOLD_S > 0 and time.time() - float(getattr(self, "_air_last", 0.0) or 0.0) < SPEECH_TAIL_HOLD_S:
            on_air = True
        self.seq.hold_flourishes = on_air
        # A conversation holds NEW flourishes (never cuts one); Paused comms let them start at once (2026-10-01).
        if CONV_HOLD:
            was = self.seq.conversation
            self.seq.conversation = house_in_conversation()
            if was and not self.seq.conversation:
                n = wraps_before_fidget(self.seq.flourish_pct)
                if n is not None:
                    self.seq.wraps = max(int(self.seq.wraps), int(n))
        # In-progress yawn/notes/wobble: cut to the hub rest so visemes
        # paint. Do not paste through a covering hand, and do not talk
        # on a filmed jaw. Slides (wake) keep the rise.
        row = self.lib.rows.get(self.key) or {}
        # the sidecar's own gate (2026-09-26): a slide or flourish whose playlist says paint
        _pl0 = self.lib.playlist(self.key) if (SLIDE_PAINT and self.key) else None
        paint_clip = bool(_pl0 and _pl0.get("paint") and row.get("mouth") is not False)
        # a painted flourish tagged `speech: false` (the yawn since 2026-10-14) is cut on air all the same (2026-09-27)
        paint_keeps = paint_clip and (row.get("speech") is not False or self.seq.graph.role(self.key) not in ("flourish", "glance"))
        cut = None if (IS_BENCH or (DRILL.get("on") and self.key == DRILL.get("key")) or paint_keeps) else self.seq.rest_while_speaking(self.key)
        if cut and self.play(cut, force=True):
            arr = self.lib.frames(self.key)
            if arr:
                base = arr[int(self.i) % len(arr)]
        role = self.seq.graph.role(self.key)
        if DRILL.get("on") and self.key == DRILL.get("key"):
            role = "loop"           # the pacer car: tiles paint over the harvest's own mouth
        elif IS_BENCH and str(row.get("rack") or "").lower() == "harvest":
            role = "loop"           # Tune Say/clip-audio: harvest exists to take paste
        elif paint_clip:
            role = "loop"           # its own sidecar says paint: a slide lands its hub's mouths
        elif MOUTH_PAINT_ALL and role and row.get("mouth") is not False:
            role = "loop"           # paint_all: every clip with a mouth box paints
        # PER-LOOP PLAYLIST FIRST. Filmed mouth at this yaw/twist × this
        # viseme class. W2L is the collapsed 96² interior; the playlist is
        # the talking-scene instrument. A clip with no playlist falls through.
        # Idle rest mouth is this loop's hub f0 (closed), not a harvest
        # underbite. Harvest rest on work read as OH/WOO (the User 2026-09-05).
        # Air / tail / prep still use the playlist. Flourishes keep the
        # filmed jaw (hand over the mouth).
        # published only once decided — publishing a half-built dict raced
        # the state read and always showed branch=None (2026-09-11)
        _dbg = {"painting": bool(self.mouth.painting()), "role": role,
                "paints_on": bool(mouth_paints_on(role)), "branch": None, "key": self.key}
        idle_rest_pick = False
        # A TALKING PASS NEVER SHOWS THE FILMED MOUTH IN A GAP (the User 2026-09-24: "the
        # flicker before she starts"). Between one pass's tail and the next line's air
        # ~0.2 s paints nothing; that fell to her FILMED mouth (tile -> her own moving
        # mouth -> tile). The gap belongs to the talking pass, so it takes the rest tile
        # whatever the idle tick says — that tick is for loops where she is NOT talking
        # (listening, waiting: "a perfect rest mouth"), and the paused rule is the wall's.
        drilling = bool(DRILL.get("on") and self.key == DRILL.get("key"))
        if VISEMES and mouth_paints_on(role) and not self.mouth.painting() and not drilling and (
                not MOUTH_IDLE_REST or (not MOUTH_IDLE_REST_PAUSED and house_paused())):
            _dbg["branch"] = "idle: filmed mouth (%s)" % ("idle_rest off" if not MOUTH_IDLE_REST else "paused")
            self._branch_dbg = _dbg
        elif VISEMES and mouth_paints_on(role) and not self.mouth.painting() and MOUTH_BOW_REST_FILMED and self._bow_frame():
            _dbg["branch"] = "idle: filmed mouth (bowed frame)"
            self._branch_dbg = _dbg
        elif VISEMES and mouth_paints_on(role) and not self.mouth.painting():
            box = self.lib.box(self.key, self.i)
            _dbg["box"] = box is not None
            if box is not None and (self.lib.lips_own(self.key, int(self.i)) or self.head_alpha(box, dt) > 0.02):
                # the picked rest tile, through the one paint path every other
                # class uses — so it honours alpha, hole, pin and fit like them
                idle_rest_pick = True
                _dbg["branch"] = "idle-rest: picked tile"
                self._branch_dbg = _dbg
            else:
                _dbg["branch"] = "idle-skip:" + ("no box" if box is None else "head moving")
                self._branch_dbg = _dbg
        elif not mouth_paints_on(role):
            _dbg["branch"] = "filmed mouth (%s)" % role
            self._branch_dbg = _dbg
        elif self.mouth.painting():
            _dbg["branch"] = "speech"
            self._branch_dbg = _dbg
        sheet_k = None
        # A HELD FILM TAKES NO SHEET (the User 2026-09-26: "Say continues painting tiles beyond
        # the pause frames on player" — lips moving on a frozen frame for the speaker's ~2 s
        # buffer tail after the stop). Under a hold the frame's own class paints (his plan, else
        # the harvest sheet, else the filmed mouth), whatever the tail does — like a flourish
        # freezing on air.
        held = bool(COMPOSER_TRANSPORT and SCRUB.get("frame") is not None and SCRUB.get("key") == self.key
                    and not DRILL.get("on"))
        # THE IDLE REST TICK COVERS A TALKING TAKE (the User 2026-09-26: "Paste Rest tiles not
        # working to cover talking clips. I dont think it works at all"): at 1x, off air, not held,
        # a harvest take painted the film's OWN class from the plan / harvest sheet — her filmed
        # talking mouth traced by tiles — and the tick did nothing there. With the tick on, plain
        # idle playback takes the rest tile like a loop; the plan still paints under a hold, a
        # scrub and slow motion (the audit), and always during a drill. Kill HOUSE_IDLE_REST_ON_TAKES=0.
        rest_covers_take = bool(IDLE_REST_ON_TAKES and MOUTH_IDLE_REST and idle_rest_pick
                                and not held and not drilling and PLAY_RATE >= 1.0)
        if (DRILL.get("on") and self.key == DRILL.get("key")) or (
                IS_BENCH and str(row.get("rack") or "").lower() == "harvest"
                and (held or (not self.mouth.painting() and not rest_covers_take))):
            # NEVER HIS PLAN DURING A PASS (the User 2026-09-24: "something is inserting noise
            # on the rendering" — "but NOT during the slow motion playing without any tuner").
            # A pass paints her voice's sheet; this is only the fallback for a frame the
            # painter thinks is off air, and the plan fills every frame on FILM time while
            # the sheet runs on the lead — so the other clock's shape slipped in (rest <-> OH
            # at f167). The plan paints the hold, the scrub and slow motion only.
            sheet_k = harvest_class_at(self.key, int(self.i),
                                       plan=not (DRILL.get("on") and self.key == DRILL.get("key")))
        if sheet_k is None and idle_rest_pick:
            # LAST, NEVER FIRST: a drill or a bench harvest rack reads the
            # film's own class at this frame, and idle rest must not take that
            # away from it — a silent loop still drills.
            sheet_k = "rest"
        if held and HELD_SAY_PAINTS and IS_BENCH and bool(getattr(self.mouth, "speaking", False)):
            held = False                    # a line on air paints the paused frame (see HELD_SAY_PAINTS); a Stop ends it
        if (VISEMES and mouth_paints_on(role)
                and (self.mouth.painting() or sheet_k)):
            pl = self.lib.playlist(self.key)
            _dbg["playlist"] = (str(pl.get("clip") or pl.get("file") or "?") if pl else None)
            self._branch_dbg = _dbg
            if pl:
                vis = self.mouth.viseme() if (self.mouth.painting() and not held) else sheet_k
                if vis is None:
                    vis = sheet_k
                if vis is None and MOUTH_PL_NO_STRIP and self.mouth.force is None:
                    vis = "rest"            # on air, no hop under now: closed rest, never "no class" (R44-1)
                klass = viseme_of(vis) if vis is not None else None
                # Resolve the series once per NOTE. Re-looking-up every frame
                # lets yaw_bin noise swap the tile mid-vowel (142 swaps / 10 s).
                # Rest is not a note: listen-back yaws 21° with class stuck on
                # rest, so the opening bin's tile rode the nose across the
                # turn — a floating mouth. Re-lookup rest when the dest bin
                # actually changes. Vowels stay frozen for the span.
                bins = None
                _frs = pl.get("frames") or []
                if _frs:
                    _fr = _frs[min(max(int(self.i), 0), len(_frs) - 1)]
                    if isinstance(_fr, dict):
                        bins = (str(_fr.get("pitch_bin") or ""),
                                str(_fr.get("yaw_bin") or ""))
                if (PROFILE_FRONT_FILMED and _frs and str(pl.get("pose") or "") == "profile"
                        and klass is not None):
                    # A PROFILE TAKE FACING FRONT KEEPS ITS FILMED MOUTH (the User 2026-09-29, bench
                    # screenshot: the 90's side-view tile on her frontal frame 0). Its index holds side
                    # views only; a frame under TURN_FRONT_MAX has no tile that fits.
                    _fr = _frs[min(max(int(self.i), 0), len(_frs) - 1)]
                    _y = _fr.get("yaw") if isinstance(_fr, dict) else None
                    if _y is not None and abs(float(_y)) < TURN_FRONT_MAX:
                        self.skip = "profile take facing front"
                        self._pl_rel = None
                        self._pl_hit = None
                        _dbg["branch"] = "speech: filmed (profile take facing front)"
                        self._branch_dbg = _dbg
                        return self._fade_out(base)
                # HEAD BACK FADES THE TILE (the User 2026-10-15 on .101, stretch-shoulders: "when she goes head up and back
                # we fade that tile to just small or see-through, as the shroud-shape tile flares out"): past
                # HEADBACK_FROM degrees of head-back pitch the tile's alpha ramps down, and past HEADBACK_TO she keeps
                # her filmed mouth; back up as she returns. Per frame, any clip. Kill HOUSE_MOUTH_HEADBACK_FADE=0.
                self._headback_k = 1.0
                if MOUTH_HEADBACK_FADE and _frs and klass is not None:
                    _frh = _frs[min(max(int(self.i), 0), len(_frs) - 1)]
                    _ph = _frh.get("pitch") if isinstance(_frh, dict) else None
                    _yh = _frh.get("yaw") if isinstance(_frh, dict) else None
                    if _ph is not None and (_yh is None or abs(float(_yh)) < TURN_FRONT_MAX):   # a side view's pitch is not hers
                        self._headback_k = headback_k(float(_ph))
                        if self._headback_k <= 0.0:
                            self.skip = "head back (%.0f deg)" % float(_ph)
                            self._pl_rel = None
                            _dbg["branch"] = "speech: filmed (head back)"
                            self._branch_dbg = _dbg
                            return self._fade_out(base)
                        if self._headback_k < 1.0:
                            _dbg["headback_k"] = round(self._headback_k, 2)
                # ONE ANGLE (the User 2026-09-29): the playlist row's yaw is the turn angle for the tile choice
                # AND the landing (_lips_and_pins); the head track read 14.99 where the row read 15.0 (chin f98)
                self._pl_yaw = None
                if _frs:
                    _fr0 = _frs[min(max(int(self.i), 0), len(_frs) - 1)]
                    if isinstance(_fr0, dict) and _fr0.get("yaw") is not None:
                        self._pl_yaw = float(_fr0["yaw"])
                band = None
                # A PROFILE TAKE LANDS LIKE ANY OTHER CLIP UNDER THE COMBO WARP (the User 2026-10-03: on talk-profile-45-c,
                # the clip its tiles were cut from, painting must give back the film; every frame fell to the lips oval
                # and wore its jaw line). Under plain cheeks the old rule stands (the lips landing).
                if MOUTH_TURN_FILL and _frs and (str(pl.get("pose") or "") != "profile" or (MOUTH_TURN_TRI and TURN_TRI_ON)):
                    _fr = _frs[min(max(int(self.i), 0), len(_frs) - 1)]
                    if MOUTH_TURN_TRI and TURN_TRI_ON:
                        _yw = _fr.get("yaw") if isinstance(_fr, dict) else None
                        band = self._tri_band(_yw)
                        # .101 2026-10-17 (the User: "it seems to pump switch between short downs and long ups ... at
                        # her upmost point", where she is also turned near the hand-over): the band keeps its last
                        # answer until the yaw is TURN_BAND_HOLD past the hand-over. Kill HOUSE_TURN_BAND_HOLD=0.
                        _prev = getattr(self, "_pl_band", None)
                        if TURN_BAND_HOLD and band != _prev and _yw is not None:
                            try:
                                _f = float(self._tri_from())
                                _held = self._tri_band(_yw, front=_f + (TURN_BAND_HOLD if _prev is None else -TURN_BAND_HOLD))
                                if _held == _prev:
                                    band = _prev
                            except (TypeError, ValueError):
                                pass
                    else:
                        band = turn_band(_fr.get("yaw") if isinstance(_fr, dict) else None)
                # THE PITCH FOLD (the User 2026-10-16): a forward frame tilted past the fold wears the face's desk
                # tiles. Rest follows it like a bin change; a sounding note keeps the tile it started with.
                # .101 2026-10-17 THE LIFT (the User: "jaw still keeping the short down version for the entire move
                # back up"): a DOWN clip (glance, nod, the bun wobble) is all desk tiles, yet she lifts to -10 in it
                # (glance: 93 of 145 frames above -17). Its frame lifted above the fold wears the face's FORWARD tiles,
                # the mirror of the fold, at the same one angle. Kill HOUSE_PITCH_LIFT=0.
                fold_key, fold_to = None, None
                if PITCH_FOLD and not band and _frs and bins and bins[0] in (
                        (PITCH_FOLD_FROM, PITCH_FOLD_TO) if PITCH_LIFT else (PITCH_FOLD_FROM,)):
                    _down_clip = bins[0] == PITCH_FOLD_TO
                    if getattr(self, "_pitch_fold_clip", None) != self.key:
                        self._pitch_fold_clip, self._pitch_folded = self.key, _down_clip
                    _frp = _frs[min(max(int(self.i), 0), len(_frs) - 1)]
                    _was = bool(getattr(self, "_pitch_folded", False))
                    _pp = _frp.get("pitch") if isinstance(_frp, dict) else None
                    # THE RAW TILT (round 29, the User 2026-10-17: "It uses a smoothed tilt value which is silly as
                    # photons do not do that. Hard threshold with most of the tilt going to the down jaws."): the fold
                    # reads each frame's own fitted pitch (<stem>.tilt.json), never the smoothed track; a clip with no
                    # tilt file keeps its playlist's pitch. Kill HOUSE_PITCH_FOLD_RAW=0.
                    if PITCH_FOLD_RAW:
                        _tl = self.lib.tilt(self.key)
                        if _tl:
                            _pp = _tl[min(max(int(self.i), 0), len(_tl) - 1)]
                    _mp = self.lib.mp_pitch(self.key) if PITCH_FOLD_MP else []
                    _mv = _mp[min(max(int(self.i), 0), len(_mp) - 1)] if _mp else None
                    if _mv is not None:
                        _now_f = pitch_fold(_was, _mv, PITCH_FOLD_MP_AT, PITCH_FOLD_MP_AT)
                    else:
                        _now_f = pitch_fold(_was, _pp)
                    # .101 2026-10-17 (the User: "you can see them flash over several seconds"): a slow lift hovers on
                    # the one angle and its track wobbles across it; after a swap there is no swap back for
                    # PITCH_FOLD_HOLD_S. Kill HOUSE_PITCH_FOLD_HOLD_S=0.
                    if _now_f != _was:
                        if time.time() - float(getattr(self, "_pitch_fold_t", 0.0)) < PITCH_FOLD_HOLD_S:
                            _now_f = _was
                        else:
                            self._pitch_fold_t = time.time()
                    self._pitch_folded = _now_f
                    if self._pitch_folded and not _down_clip:
                        fold_key, fold_to = self.lib.pitch_fold_source(self.key), PITCH_FOLD_TO
                    elif not self._pitch_folded and _down_clip:
                        fold_key, fold_to = self.lib.pitch_fold_source(self.key, PITCH_FOLD_FROM), PITCH_FOLD_FROM
                    if fold_key:
                        bins = (fold_to, bins[1])
                    _dbg["pitch_fold"] = fold_key
                # THE BOW MAP (MOUTH_BOW above): a bowed frame wears its bow band's tiles in place of the fold / turn
                # band, and a frame no tile reaches keeps her filmed mouth (the head-back rule's fade).
                if MOUTH_BOW_REST_FILMED and klass == "rest" and self._bow_frame():
                    self.skip = ""
                    self._pl_rel = None
                    _dbg["branch"] = "speech: filmed rest (bowed frame)"
                    self._branch_dbg = _dbg
                    return self._fade_out(base)
                _bow = None
                if MOUTH_BOW:
                    _bm = self.lib.bow_map(self.key)
                    if _bm:
                        _bow = _bm[min(max(int(self.i), 0), len(_bm) - 1)]
                    if _bow == "filmed":
                        self.skip = "bowed past every tile"
                        self._pl_rel = None
                        self._pl_bow = _bow
                        _dbg["branch"] = "speech: filmed (bow map)"
                        self._branch_dbg = _dbg
                        return self._fade_out(base)
                    if _bow:
                        _bpl = self.lib.playlist(_bow) or {}
                        _bbin = ((_bpl.get("bins") or [""])[0] or "").split("/")[0]
                        if _bbin:
                            band, fold_key, fold_to = None, _bow, _bbin
                            bins = (fold_to, bins[1] if bins else "0")
                        else:
                            _bow = None
                    _dbg["bow"] = _bow
                bow_moved = MOUTH_BOW and _bow != getattr(self, "_pl_bow", None)
                band_moved = band != getattr(self, "_pl_band", None)
                rest_follow = klass in ("rest", "rest-open") and bins != self._pl_bin
                # .101 2026-10-17 (the User: the down jaw "hits too late and stays for her entire up move"): crossing
                # the fold angle re-picks the tile at that frame, down or up, like a band move. Kill HOUSE_PITCH_FOLD_REPICK=0.
                fold_moved = PITCH_FOLD and PITCH_FOLD_REPICK and bool(fold_key) != bool(getattr(self, "_pl_fold", False))
                if klass != self._pl_class or self._pl_hit is None or rest_follow or band_moved or fold_moved or bow_moved:
                    self._pl_bow = _bow
                    self._pl_class = klass
                    self._pl_bin = bins
                    self._pl_band = band
                    _turn_lib = None
                    if band:
                        self._pl_hit, _turn_lib, _ = self.lib.turn_source(band, vis, self.key)
                    else:
                        self._pl_hit = None
                        if fold_key:
                            self._pl_hit = station_lookup(self.lib.playlist(fold_key), fold_to or PITCH_FOLD_TO, vis)
                        if self._pl_hit is None:
                            fold_key = None
                            self._pl_hit = playlist_lookup(pl, self.i, vis)
                    self._pl_fold = bool(fold_key)
                    if band:
                        self._pl_cell = MP.cell_key(str(_turn_lib or band), "profile", str(klass))
                    else:
                        _lib = fold_key or (pl.get("library") or (self.lib.rows.get(self.key) or {}).get("library")
                                            or self.key)
                        self._pl_cell = MP.cell_key(str(_lib), str((bins or ("",))[0]), str(klass))
                    self._rest_i, self._rest_until = None, 0.0
                    now = time.time()
                    span = None
                    if self.mouth.force_class is None:
                        span = note_span(self.mouth.frames, now)
                    if span:
                        # Live note: equal slices of THIS span. A 2-list on a
                        # 20-frame AH is 10+10, not 1+2 five times.
                        self._pl_t0, self._pl_t1 = span
                        self._pl_loop = False
                        # Hops are already shifted: rail-align 50 ms when
                        # :8195 is warm (HOUSE_ALIGN_LEAD_MS), else
                        # HOUSE_VISEME_LEAD_MS on the letter clock. Adding
                        # the product 100 ms AGAIN here delayed the landing
                        # tile 100 ms into the note — she looked late in the
                        # room (measured 2026-09-04: aligner warm, lead_ms
                        # 100, land = t0+100 ms → audio+50 ms). Land at
                        # class onset (already early). Couplet 10-frame
                        # early is morning (needs hops that far ahead).
                        self._pl_land = self._pl_t0
                        if MOUTH_WINDUP and len(as_tiles(self._pl_hit)) == 2:
                            self._pl_land = None      # TWO-TIER HITS: the windup at the head of the note, then the apex
                    else:
                        n = len(as_tiles(self._pl_hit))
                        if n == 2:
                            # Composer class hold has no note end. Loop 10+10
                            # so the first pick is not one frame then the
                            # second thirty times.
                            self._pl_t0 = now
                            self._pl_land = None
                            self._pl_t1 = now + 2.0 * SERIES_SLICE_S
                            self._pl_loop = True
                        else:
                            self._pl_t0, self._pl_t1 = now, now + SERIES_ONCE_S
                            self._pl_loop = False
                            self._pl_land = None
                    self._pl_swaps += 1
                # REST IS NOT A NOTE. Slicing a rest list every 10 frames is
                # the idle twitch. Two classes, two jobs (the User 2026-09-04):
                # rest-open is the mid-speech pause — one open mouth for the
                # hop span. rest is closed after .!? and at idle; several
                # closed tiles may cycle on rest_step, never mixed with open.
                _tiles = as_tiles(self._pl_hit)
                if klass == "rest-open":
                    self._rest_i, self._rest_until = None, 0.0
                    rel = _tiles[0] if _tiles else None
                elif klass == "rest" and len(_tiles) > 1:
                    self._rest_i, self._rest_until = rest_step(
                        len(_tiles), time.time(), self._rest_i, self._rest_until,
                        lo=REST_DWELL_MIN_S, hi=REST_DWELL_MAX_S)
                    rel = _tiles[self._rest_i]
                else:
                    # Leaving rest ends the dwell, so the next silence starts
                    # on a fresh draw instead of resuming mid-hold.
                    self._rest_i, self._rest_until = None, 0.0
                    rel = pick_series_tile(
                        self._pl_hit, self._pl_t0, self._pl_t1, time.time(),
                        loop=self._pl_loop, land=self._pl_land,
                    )
                self._pl_rel = rel
                if klass == "rest" and not MOUTH_IDLE_REST:
                    # IDLE REST OFF MEANS NO CLOSED-REST PASTE, IN A LINE TOO
                    # (the User 2026-09-13 01:5x: "we have a flag set to off for
                    # idle rest tile" — the closed rest after every word and
                    # after .!? was still pasted, a U plate popping on the
                    # first rest frame). The loop's own filmed mouth is the
                    # closed mouth; the last tile fades out over it.
                    self.skip = ""
                    self._pl_rel = None
                    _dbg["branch"] = "speech: filmed rest (idle_rest off)"
                    self._branch_dbg = _dbg
                    return self._fade_out(base)
                start = self.seq.graph.start(self.key)
                if rel and closeup_tile(rel) != closeup_dest(
                        self.key, start, row.get("pose_class")):
                    # Own tile class: close-up tiles stay on down_close.
                    # Heads-down harvest stays on work. One engine, one kit
                    # (composite_stamp); Haar rollback never gets this rel.
                    self.skip = "tile class"
                    self._pl_rel = None
                    self._pl_hit = None
                    rel = None
                pair = self.lib.playlist_tile(rel) if rel else None
                box = self.lib.box(self.key, self.i)
                _dbg["pl_rel"] = rel; _dbg["pl_pair"] = bool(pair); _dbg["pl_box"] = bool(box)
                self._branch_dbg = _dbg
                if pair and box:
                    here = _roll_of(str(pl.get("file", "")).replace(".mp4", ""), self.i)
                    there = _tile_roll(rel)
                    d = (here - there) if (here is not None and there is not None) else 0.0
                    # The playlist runs ahead of both other painters and
                    # returns, so without this guard the clips that matter most
                    # — `work` has a playlist, and she is at the desk most of
                    # the day — paste a mouth through a head turn unguarded.
                    if not self.lib.lips_own(self.key, int(self.i)) and self.head_alpha(box, dt) <= 0.02:
                        self.skip = "head moving (%.1f w/s)" % self.vel
                        return base
                    painted = None
                    if MOUTH_ANCHOR == "head":
                        # through the finished-patch store (lib/mouth_patch, 2026-10-24); it calls _paste_head
                        painted = self._paste_head_kept(base, box, pair[0], pair[1], rel)
                    if painted is None:
                        _fc = (self.lib.tile_head(rel) or None) if FACE_CROP_TAG in str(rel) else None
                        painted = self._paste_playlist(base, box, pair[0], pair[1], d, rec=_fc)
                    _dbg["pl_painted"] = painted is not None; _dbg["pl_skip"] = self.skip
                    self._branch_dbg = _dbg
                    if painted is not None:
                        if DRILL.get("on") and self.key == DRILL.get("key"):
                            _th = self.lib.tile_head(rel) or {}
                            DRILL["log"].append((int(DRILL.get("vframe") if DRILL.get("vframe") is not None else self.i), klass, _th.get("open")))
                            pacer_trace("paint", i=int(self.i), c=klass)
                            _pl = getattr(self, "_place_dbg", None)
                            if _pl:
                                # WHICH FRAME, WHICH TILE, WHICH PIN (the User 2026-09-16 goal: "fix
                                # mouth tile formulas and pin points"): a median of +2 px hid the
                                # frames that landed in the corner; the report names the worst
                                _pl = dict(_pl, frame=int(self.i), klass=klass, rel=str(rel or "").split("/")[-1],
                                           pin=dict(getattr(self, "_pin_dbg", None) or {}).get("how"),
                                           path=dict(getattr(self, "_pin_dbg", None) or {}).get("path"))
                                DRILL.setdefault("place", []).append(_pl); self._place_dbg = None
                        self.strip.last_class = viseme_of(vis) if vis is not None else None
                        self.strip.last_idx = None
                        self.painted += 1
                        self.skip = ""
                        return painted
        # NEURAL MOUTH FIRST, WHEN IT IS ON. Wav2Lip conditions on this frame
        # and this audio, so it needs no strip, no per-hub bank, no Haar box
        # beyond the sidecar, and no velocity fade — every constraint the paste
        # carries. XOR with the strip: one mouth, never two.
        # Any honest miss (no box, no audio window, session down) falls through
        # to the strip below rather than showing nothing.
        # A HUB WITH NO BANK IS A HUB WE DECIDED NOT TO PAINT — and that
        # decision must bind BOTH mouths, not just the strip. The strip is
        # gated on strip.bank(pose) further down; w2l was not, so on a hub like
        # down-slouch it would paint anyway.
        #
        # It is not a rare frame either. Measured 2026-09-01 on the slouch
        # loops: the face landmarker returns a mouth box on 100% of frames (241/241 and
        # 145/145) because it INFERS the mouth points from the face — it
        # places them whether or not a laptop lid is over them. So "a sidecar
        # box exists" never means "her mouth is visible", and w2l keyed on
        # exactly that. Result would have been a neural mouth painted on a lid
        # for 10 s of the talk loop.
        #
        # Same rule as the slide guard above: one decision, both mouths.
        _pose_now = self.seq.graph.hub_after(self.key)
        _hub_paints = bool(self.strip.bank(_pose_now)) if _pose_now else True
        if (W2L_MODE != "off" and self.mouth.painting()
                and mouth_paints_on(role)
                and _hub_paints):
            # SLIDES KEEP THE FILMED JAW — for W2L exactly as for the strip.
            # The neural path runs first, so without this `wake` / `rest` got a
            # 96 crop pasted onto a body that is mid-move. Same rule, both
            # mouths, or the guard only covers whichever one happens to be on.
            live = _w2l_live()
            if live is not None:
                # The grid is live-tunable, so re-assert it rather than only
                # setting it at load: /v1/mouth-tune must not need a restart.
                # Clamped to what the SESSION can hold, so a mid-day drop to
                # the CPU limp coarsens the mouth instead of dropping it.
                live.fps = _w2l_grid(live)
                # Same guard the strip has always had. Without it a mouth held
                # across a head move lands on the jaw's old position while the
                # filmed jaw is elsewhere, and both read at once.
                _wbox = self.lib.box(self.key, self.i)
                if _wbox is not None and self.head_alpha(_wbox, dt) <= 0.02:
                    self.skip = "head moving (%.1f w/s)" % self.vel
                    return base
                live.feed(self.mouth.pcm_rows, self.mouth.sr)
                out, why = live.paint(base, self.lib.box(self.key, self.i),
                                      time.time())
                W2L_MS, W2L_MISSES = round(live.ms, 2), live.misses
                W2L_GENS, W2L_HOLDS = live.gens, live.holds
                if not why:
                    # `painted` means "a mouth was painted this frame",
                    # whichever mouth. It used to count strip frames only, so a
                    # W2L turn read as painted:0 and looked like nothing was
                    # happening. w2l_ms tells you which one did it.
                    self.painted += 1
                    self.skip = ""
                    return out
                self.skip = "w2l: %s -> strip" % why
                # Bucket the reason, not the number: drift carries a measured
                # width, so key on the phrase and keep the count.
                bucket = why.split("(")[0].strip()
                W2L_WHY[bucket] = W2L_WHY.get(bucket, 0) + 1
        if not VISEMES:
            self.skip = "visemes off"
            return base
        if not (self.strip.ok and self.mouth.painting()):
            self.skip = "not on air"
            return base
        # Yeah the User rides `wake` (sit-up, down→back). hub_after is `back`, so
        # the face-on strip was pasted on a heads-down rise — the viseme the User
        # still saw on the greeting. Slides keep the filmed jaw; the voice rides
        # them. Flourishes keep the filmed mouth (yawn-work hand covering).
        # Rest loops (work, listen) still paint.
        if not mouth_paints_on(role):
            self.skip = (
                "slide keeps filmed mouth" if role == "slide"
                else "flourish keeps filmed mouth")
            return base
        if MOUTH_PL_NO_STRIP and self.mouth.force is None and self.lib.playlist(self.key):
            # R44-1: a playlist clip whose frame found no tile keeps her filmed mouth (the last tile fades), never the Strip
            self.skip = self.skip or "no tile this frame: filmed (playlist clip)"
            return self._fade_out(base)
        # EACH HUB HAS ITS OWN JAW. Face-on on heads-down was a smudge
        # (the User 2026-08-30). The hair-tuck "Okay" is the down talking take —
        # flatter, same person, looking at the laptop. Paint the bank cut
        # from this hub; if we have not cut one, leave the clip mouth.
        pose = self.seq.graph.hub_after(self.key)
        bank = self.strip.bank(pose)
        if not bank:
            self.skip = "no strip for %s" % (pose or "?")
            return base
        box = self.lib.box(self.key, self.i)
        if not box:
            self.skip = "no mouth box on " + self.key
            return base
        x, y, w, h = box
        alpha = self.head_alpha(box, dt)
        # Fade out while the head moves: the clip's own mouth is motion-blurred
        # and displaced there while a pasted one is sharp and on-centre.
        if self.vel > VEL_LO:
            alpha = 1.0 - (self.vel - VEL_LO) / max(0.01, VEL_HI - VEL_LO)
            if alpha <= 0.02:
                self.skip = "head moving (%.1f w/s)" % self.vel
                return base
        vis = self.mouth.viseme()
        got = self.strip.tile_for(pose, lvl, vis)
        if got is None:
            return base
        tile, lip = got
        pw = int(round(bank["src_w"] * w * MOUTH_SCALE))
        ph = int(round(bank["src_h"] * w * MOUTH_SCALE))
        if pw < 2 or ph < 2:
            return base
        X = int(round(x + w / 2.0 - pw / 2.0))
        Y = int(round(y - bank["mt"] * w * MOUTH_SCALE))
        X0, Y0 = max(0, X), max(0, Y)
        X1, Y1 = min(paint_wh()[0], X + pw), min(paint_wh()[1], Y + ph)
        if X1 - X0 < 2 or Y1 - Y0 < 2:
            self.skip = "mouth outside the frame"
            return base
        # 1. Hide the filmed jaw. 2. Cut the viseme mouth. 3. Lay it in.
        # Without (1) her closed clip-jaw sits under the open viseme (two
        # edges). Without (2) the tile's cheeks paste a second face onto hers.
        out = hide_clip_jaw(base, (x, y, w, h))
        patch = cv2.resize(tile, (pw, ph), interpolation=cv2.INTER_LINEAR).astype(np.float32)
        a_lip = cv2.resize(lip, (pw, ph), interpolation=cv2.INTER_LINEAR)
        patch = patch[Y0 - Y:Y1 - Y, X0 - X:X1 - X]
        a_lip = a_lip[Y0 - Y:Y1 - Y, X0 - X:X1 - X]
        dst = out[Y0:Y1, X0:X1].astype(np.float32)
        bh, bw = dst.shape[:2]
        dm = float(dst.mean()) or 1.0
        pm = float(patch.mean()) or 1.0
        patch = np.clip(patch * max(0.5, min(2.0, dm / pm)), 0, 255)
        patch = tone_b(patch, dst, a_lip)
        # Lip cut, with the old ellipse as a floor so a thin mask cannot leave
        # a punched hole. Core still sets how hard the middle is.
        yy, xx = np.mgrid[0:bh, 0:bw]
        r = np.sqrt(((xx - bw / 2) / max(bw / 2, 1)) ** 2 + ((yy - bh / 2) / max(bh / 2, 1)) ** 2)
        ell = np.clip((1.0 - r) / (1.0 - MOUTH_CORE), 0, 1)
        a = np.maximum(a_lip, ell * 0.35)
        a = (np.clip(a, 0, 1) * alpha)[..., None]
        out[Y0:Y1, X0:X1] = (dst * (1 - a) + patch * a).astype(np.uint8)
        self.painted += 1
        self.skip = ""
        return out

    def _step(self, name: str) -> None:
        """Close one stage of a paint for the semiframe counter (the User 2026-10-11). Costs a perf_counter call."""
        t = time.perf_counter()
        st = getattr(self, "_steps", None)
        if st is not None:
            st[name] = st.get(name, 0.0) + (t - getattr(self, "_step_t", t)) * 1000.0
        self._step_t = t

    def _composite_safe(self, raw, dt):
        """One bad paste must not end the paint thread.

        2026-09-16 15:25 on the bench: Repin nulled `_pin_last`, the next
        nose-pixel pin raised TypeError in `_pixel_pin`, and Thread-3 (run)
        died. /v1/state kept answering, `frame_no` froze, every dial
        landed in the journal and changed nothing on the glass. Paint the
        raw frame, name the fault in `skip`, log the first and every 200th.
        """
        self._steps = {}                       # the semiframe counter's stage times inside this paint (ms)
        self._step_t = time.perf_counter()
        p0 = self.painted
        try:
            out = self._composite(raw, dt)
        except Exception as ex:  # noqa: BLE001
            self._paint_errs += 1
            self.skip = "paint error: %s: %s" % (type(ex).__name__, ex)
            if self._paint_errs == 1 or self._paint_errs % 200 == 0:
                action("paint", "error #%d %s: %s" % (self._paint_errs, type(ex).__name__, ex), "run")
                import traceback
                traceback.print_exc()
            out = raw
        if MOUTH_CLIP_TALLY:
            self._tally(p0)
        return out

    def _tally(self, p0: int) -> None:
        """MOUTH_CLIP_TALLY: count this pass for the clip on air (painted, or unpainted and why); one line when the
        clip on air or the line ends. Never raises."""
        try:
            on = bool(self.mouth.painting() and self.mouth.force is None)
            key = self.key if on else None
            t = getattr(self, "_tally_st", None) or {}
            if t.get("key") and t["key"] != key:
                why = ", ".join("%s: %d" % kv for kv in sorted(t["why"].items(), key=lambda kv: -kv[1])[:4])
                print("[conductor] mouth on %s while she spoke: %d passes, painted %d, unpainted %d%s"
                      % (t["key"], t["n"], t["p"], t["n"] - t["p"],
                         (" (%s; frames %d-%d)" % (why, t["lo"], t["hi"])) if t["n"] > t["p"] else ""), flush=True)
                t = {}
            if on:
                if t.get("key") != key:
                    t = {"key": key, "n": 0, "p": 0, "why": {}, "lo": 10 ** 9, "hi": -1}
                t["n"] += 1
                if self.painted > p0:
                    t["p"] += 1
                else:
                    r = str(self.skip or "no paint")[:48]
                    t["why"][r] = t["why"].get(r, 0) + 1
                    t["lo"], t["hi"] = min(t["lo"], int(self.i)), max(t["hi"], int(self.i))
            self._tally_st = t
        except Exception:  # noqa: BLE001 - a count never costs a paint
            pass

    def _on_air(self) -> bool:
        """Something on air that needs the mouth's own rate: her voice (its prep and tail), a held class, a camera cut,
        a drill or a scrub. Nothing else repaints a clip frame differently."""
        try:
            if self.mouth.painting() or self.mouth.force is not None or self._cam:
                return True
            _p = AUTOTUNE.get("proc")
            return bool(DRILL.get("on") or SCRUB.get("frame") is not None or (_p is not None and _p.poll() is None))
        except Exception:  # noqa: BLE001  never let the rule stop a paint
            return True

    # ---- the loop -------------------------------------------------------
    def run(self) -> None:
        paint_cores.pin_thread("player")    # her paint on its own core (lib/paint_cores; off on the house until set)
        period = 1.0 / RENDER_FPS
        idle_period = 1.0 / IDLE_FPS
        nxt = time.perf_counter()
        last = time.time()
        while not self._stop:
            now = time.time()
            _t_pass = time.perf_counter()
            STALL_PASS[0] = _t_pass if STALL_PASS_LOG else None    # the stall watch's mid-pass look
            _t_comp = _t_enc = _t_pip = 0.0       # the semiframe counter's step times (0 = the step did not run)
            mouth_box = None                      # X-Mouth-Box of this pass's frame (a clip frame, not a cut)
            clip_id = None                        # X-Clip of this pass's frame (round 13)
            self._steps = {}
            if SEMI_GRID:
                # the row this pass paints (semi_grid_wait's "painted")
                self._grid_row = (semi_grid_row(list(getattr(self.mouth, "frames", None) or []), now)
                                  if getattr(self.mouth, "speaking", False) else None)
            dt, last = now - last, now
            if _PATCH_PREFILL.get("proc") is not None:
                # the install's mouth build waits while she speaks or a class is held, every pass (a held or paused
                # film paints nothing, and the build must go on under it)
                mouth_patch_prefill_hold(bool(self.mouth.painting() or self.mouth.force_class is not None))
            if self._revoke_casual:
                self._revoke_casual = False
                self.revoke_casual()
            held = agent_still(now)                 # Close Agent: no play taken, no frame advance (round 16)
            pending = None if held else self._pending_play
            if pending:
                self._pending_play = None
                if pending == USER_TALK_KEY and self.key != USER_TALK_KEY:
                    # ½ s analog-switch, then she is already leaned in.
                    self._arm_cam(pending, kind="cut")
                elif (pending == self._home_rest()
                      and self.key == USER_TALK_KEY):
                    self._arm_cam(pending, kind="fade")
                else:
                    self.play(pending)
                last = now
                dt = 0.0
                self._clip_accum = 0.0
            if not held:
                self._poll_ft_release()
            overlay = self._cam_paint(now) if self._cam else None
            arr = self.lib.frames(self.key)
            reuse = False
            if overlay is not None:
                raw = overlay
                # The voice hops on the connection frames too. Skip _composite only
                # when there is nothing to paste — never a rest tile for air.
                role = self.seq.graph.role(self.key) if self.key else ""
                if (self.mouth.painting()
                        and mouth_paints_on(role)):
                    frame = self._composite_safe(overlay, dt)
                else:
                    frame = overlay
            elif arr is not None:
                raw = arr[self.i % len(arr)]
                if MOUTH_BOX_HEADER:
                    try:
                        mouth_box = self.lib.box(self.key, self.i)
                    except Exception:  # noqa: BLE001 - a header is never worth a paint
                        mouth_box = None
                if CLIP_HEADER:
                    try:
                        clip_id = (self.key, self.i % len(arr), len(arr), self.seq.graph.role(self.key))
                    except Exception:  # noqa: BLE001 - a header is never worth a paint
                        clip_id = None
                air = self._on_air()
                if may_reuse(self._painted, self.key, self.i, air=air, now=now, at=self._painted_at):
                    frame = raw                  # the same frame, nothing on air: keep the last JPEG (DISTINCT_ONLY)
                    reuse = True
                else:
                    _tc = time.perf_counter()
                    frame = self._composite_safe(raw, dt)
                    _t_comp = (time.perf_counter() - _tc) * 1000.0
                    self._painted, self._painted_at = (self.key, self.i), now
            else:
                raw = None
                frame = None
            if frame is not None:
                if reuse:
                    ok, pip = False, None
                else:
                    self.paints += 1
                    _te = time.perf_counter()
                    ok, buf = cv2.imencode(".jpg", frame,
                                           [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_Q])
                    _t_enc = (time.perf_counter() - _te) * 1000.0
                    pip = None
                _tp = time.perf_counter()
                if PIP_W2L and not reuse:
                    # Own paint on the RAW frame, so the PIP is independent of
                    # whatever the wall got. No _composite re-entry.
                    pl = _pip_live()
                    box = self.lib.box(self.key, self.i)
                    _pp = self.seq.graph.hub_after(self.key)
                    _pip_ok = bool(self.strip.bank(_pp)) if _pp else True
                    if pl is not None and box and self.mouth.painting() \
                            and mouth_paints_on(self.seq.graph.role(self.key)) \
                            and _pip_ok:
                        pl.feed(self.mouth.pcm_rows, self.mouth.sr)
                        pout, pwhy = pl.paint(raw.copy(), box, time.time())
                        cand = raw if pwhy else pout
                    else:
                        cand = raw                       # PIP_W2L=off -> clean
                    pok, pbuf = cv2.imencode(".jpg", cand,
                                             [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_Q])
                    pip = pbuf.tobytes() if pok else None
                _t_pip = (time.perf_counter() - _tp) * 1000.0
                if ok:
                    jpeg = buf.tobytes()
                    with self._cv:
                        # Dump the last rendered JPEG as this one takes its
                        # slot. PIP keeps its own bytes only when it actually
                        # painted a different mouth; otherwise it aliases.
                        self.frame_jpeg = jpeg
                        self.frame_pip_jpeg = pip if PIP_W2L else None
                        self.frame_no += 1
                        self.frame_t = now
                        self.frame_mouth = mouth_box
                        self.frame_clip = clip_id
                        self._cv.notify_all()
                locked = False
                if COMPOSER_TRANSPORT and not DRILL.get("on"):
                    # a park or a strip hold belongs to ONE clip; on any other it is released
                    # (faults 4 and 14: a stale SCRUB re-pinned the old clip on return, and a
                    # park re-pinned its last frame whenever the sequencer re-entered the key)
                    if DRILL.get("hold_frame") and DRILL.get("hold_key") not in (None, self.key):
                        transport_release("left %s" % DRILL.get("hold_key"), "loop")
                    if SCRUB.get("frame") is not None and SCRUB.get("key") not in (None, self.key):
                        transport_release("left %s" % SCRUB.get("key"), "loop")
                if INIT_HOLD.get("key"):
                    if self.key == INIT_HOLD["key"]:
                        self.i = 0; locked = True    # round 34: THE OFFICE WAITS - her empty office until she walks in
                    else:
                        INIT_HOLD["key"] = None
                if (not DRILL.get("on")) and DRILL.get("hold_frame") and self.key == DRILL.get("key"):
                    nfr = len(self.lib.frames(self.key) or [])
                    if nfr:
                        self.i = nfr - 1; locked = True
                if DRILL.get("on") and self.key == DRILL.get("key"):
                    aa = self.mouth.air_at
                    if DRILL.get("t0") is None and aa and float(aa) > float(DRILL.get("armed") or 0):
                        DRILL["t0"] = float(aa)
                        DRILL["n"] = len(self.lib.frames(self.key) or [])
                        action("drill", "air at %.3f — clip locked to the voice" % DRILL["t0"], "")
                    if DRILL_HOLD_ZERO and DRILL.get("t0") is None and not DRILL.get("pacer_on"):
                        self.i = 0; locked = True      # armed, no voice yet: she waits on her first frame
                    if DRILL.get("t0") is not None:
                        target = int((time.time() - DRILL["t0"]) * CLIP_FPS * float(DRILL.get("pace") or 1.0))
                        nfr = int(DRILL.get("n") or 0)
                        _wrap = bool(DRILL_LOOP_WRAP and nfr and not DRILL.get("pacer_on")
                                     and str((self.lib.rows.get(self.key) or {}).get("role") or "") == "loop")
                        DRILL["vframe"] = target if _wrap else None
                        if target < 0:
                            self.i = 0; locked = True
                        elif target < nfr:
                            self.i = target; locked = True
                        elif _wrap and (bool(getattr(self.mouth, "speaking", False)) or target < nfr + 6):
                            self.i = target % nfr; locked = True          # round the loop again, the voice still on air
                        else:
                            DRILL["report"] = drill_report(); DRILL["passes"] = int(DRILL.get("passes") or 0) + 1
                            action("drill", "pass %d — %s" % (DRILL["passes"], (DRILL["report"] or {}).get("read", "no report")), "")
                            _r = DRILL["report"] or {}
                            journal_add("drill", "drill", _r.get("read", ""), {"key": DRILL.get("key"), "lag_frames": _r.get("lag_frames"), "lag_ms": _r.get("lag_ms"), "r": _r.get("r"), "changes_per_s": _r.get("changes_per_s")})
                            if DRILL.get("loop"):
                                drill_spawn()          # again, with whatever the dials say now
                            else:
                                drill_stop("clip over")
                            if COMPOSER_TRANSPORT:
                                locked = True      # the end tick does not advance the film (fault 15)
                    elif (not DRILL.get("reading")) and time.time() - float(DRILL.get("armed") or 0) > 40.0:
                        drill_stop("no voice within 40 s")
                elif DRILL.get("on") and (not DRILL.get("reading")) and time.time() - float(DRILL.get("armed") or 0) > 3.0:
                    drill_stop("left the film (%s)" % self.key)   # taken off before or after its voice arrived
                n_clip = 0          # THIS iteration's advance, never the last one's
                if held and overlay is None:
                    locked = True      # Close Agent: the frame she is on, until Open Agent
                # read the scrub ONCE (2026-10-28): a release on the HTTP thread cleared SCRUB["frame"] while frames()
                # loaded, the second read was None, and the playhead thread died - the bench froze on one picture
                _sf, _sk = SCRUB.get("frame"), SCRUB.get("key")
                if _sf is not None and _sk == self.key and not DRILL.get("on"):
                    _arr = self.lib.frames(self.key)
                    if _arr:
                        self.i = max(0, min(len(_arr) - 1, int(_sf)))
                    locked = True
                if not locked and overlay is None:
                    n_clip, self._clip_accum = clip_frames_to_advance(
                        self._clip_accum, dt * PLAY_RATE, CLIP_FPS)
                    for _ in range(n_clip):
                        self._advance()
                # THE SIGNAL IS POSTED BY THE THREAD THAT OWNS THE PLAYHEAD
                # (2026-09-21). Posting it from the HTTP thread raced this one:
                # the anchor read ph.i while this loop was free to advance it,
                # so the same pass measured +2 f once and +13 f later — the
                # jitter being reported as pipeline latency. Armed by
                # drill_begin, fired here, one frame boundary after a rewind,
                # so `i` and `i_at` are exactly what this loop just set.
                if DRILL.get("pacer_arm"):
                    DRILL["pacer_arm"] = False
                    try:
                        DRILL["pacer_result"] = drill_pacer_post(str(DRILL.get("key") or ""), self, first=True)
                    except Exception as _e:  # noqa: BLE001
                        DRILL["pacer_result"] = {"ok": False, "error": str(_e)[:200]}
                elif (DRILL.get("pacer_on") and DRILL.get("on")
                      and self.i != (DRILL.get("pacer") or {}).get("last_i")):
                    # ONE ROW PER FRAME, from the thread that knows where the
                    # film is. Nothing is scheduled ahead, so nothing can drift.
                    #
                    # TWO GUARDS, both found by a signal that landed 2 of 5
                    # times and read EXACTLY +15 f when it missed — too
                    # repeatable to be noise. `n_clip` was assigned only inside
                    # the `if not locked` branch above and read out here, so
                    # while the film was HELD (drill_stop parks it on its last
                    # frame between passes) this fed rows on the PREVIOUS
                    # iteration's count, for a film that was not moving. And
                    # the playhead can be on another clip entirely when a pass
                    # opens, so its frame index would not be this film's.
                    try:
                        drill_pacer_post(str(DRILL.get("key") or ""), self)
                    except Exception:  # noqa: BLE001
                        pass
            if SEMI_COUNT_ON:
                try:
                    SEMIS.cost(composite=_t_comp, encode=_t_enc, pip=_t_pip,
                               loop=(time.perf_counter() - _t_pass) * 1000.0,
                               **{"c_" + k: v for k, v in (getattr(self, "_steps", None) or {}).items()})
                    SEMIS.tick(self.mouth, time.time())
                    _vt = getattr(self.mouth, "vis_t", None)
                    if _vt is not None and _vt >= now and not reuse:
                        SEMIS.tick_paint(self.mouth, _vt)
                except Exception:  # noqa: BLE001  a counter never stops a paint
                    pass
            # An unwatched bench paints at IDLE_FPS (2026-09-15). frame_no
            # keeps climbing so /v1/health stays green; the next reader on
            # /frame.jpg or /frame.mjpg stamps looked_at() and the very next
            # pass is back at RENDER_FPS.
            # A pack rests the house's way only while nothing is on air (the User 2026-10-10, AA): she never paints
            # her own reply at 2 fps because the Wall was resting when it began.
            STALL_PASS[0] = None
            _idle = look_idle() and not (PACK_IDLE and self._on_air())
            if held and not self._on_air():
                _idle = True                       # Close Agent: one held frame needs no full rate (round 16)
            _gw = None
            if STALL_PASS_LOG and getattr(self.mouth, "speaking", False):
                _pl = self.__dict__.setdefault("_pass_log", {"prev": None})
                _rec = {"t": now, "loop": round((time.perf_counter() - _t_pass) * 1000.0, 1),
                        "comp": round(_t_comp, 1), "enc": round(_t_enc, 1), "reuse": bool(reuse),
                        "steps": {k: round(v, 1) for k, v in (getattr(self, "_steps", None) or {}).items()},
                        "key": self.key, "i": int(self.i)}
                _pv = _pl.get("prev")
                if _pv is not None and now - _pv["t"] > 0.045:
                    print("[stall] pass gap %.0f ms after %s (that pass: loop %s comp %s enc %s reuse %s steps %s, then "
                          "waited %s ms)" % ((now - _pv["t"]) * 1000.0, "%s#%d" % (_pv["key"], _pv["i"]), _pv["loop"],
                                             _pv["comp"], _pv["enc"], _pv["reuse"], _pv["steps"], _pv.get("wait")),
                          flush=True)
                _pl["prev"] = _rec
            if SEMI_GRID and not _idle and getattr(self.mouth, "speaking", False):
                _gw = semi_grid_wait(list(getattr(self.mouth, "frames", None) or []), time.time(),
                                     getattr(self, "_grid_row", None))
            if STALL_PASS_LOG and getattr(self, "_pass_log", None) and self._pass_log.get("prev"):
                self._pass_log["prev"]["wait"] = None if _gw is None else round(_gw * 1000.0, 1)
            if _gw is not None:
                if _gw > 0:
                    time.sleep(_gw)
                nxt = time.perf_counter()
                continue
            nxt += idle_period if _idle else period
            slp = nxt - time.perf_counter()
            if slp > 0:
                time.sleep(slp)
            else:
                nxt = time.perf_counter()      # fell behind; do not spiral

    def wait_frame(self, seen: int, timeout: float = 2.0, pip: bool = False, meta: bool = False):
        """(jpeg, frame_no), or with meta (jpeg, frame_no, (frame_t, frame_mouth, frame_clip)) read under the same
        lock."""
        with self._cv:
            if self.frame_no == seen:
                self._cv.wait(timeout)
            blob = self.frame_pip_jpeg if pip else self.frame_jpeg
            if meta:
                return (blob or self.frame_jpeg), self.frame_no, (self.frame_t, self.frame_mouth, self.frame_clip)
            return (blob or self.frame_jpeg), self.frame_no


# ── the service ─────────────────────────────────────────────────────────────
STATE: dict = {}


def reload_now(ph) -> int:
    """Drop the playlist / map-head tracks and every tile-keyed mouth store. Returns
    how many tracks went."""
    lib = ph.lib
    with lib._lock:
        # A MOUTH SIDECAR EDIT REACHES THE BENCH BY RELOAD TOO (2026-09-26: pose-mouth-fill-null
        # filled the null turn frames on lila-sit-down-chin and the bench kept the old track
        # until a restart — the mouth tracks are keyed by file name, not by a "pl:" prefix).
        # ...and a TURN sidecar (2026-09-28): the turn-fix painter caches <stem>.mouth.turn.json under its own
        # name, which never ends in ".mouth.json", so a re-run turn file stayed in memory until a restart.
        dropped = [k for k in lib._tracks if k.startswith("pl:") or k.startswith("hf:map")
                   or k.endswith(".mouth.json") or k.endswith(".mouth.turn.json")]
        for k in dropped:
            lib._tracks.pop(k, None)
        lib._pl_mt.clear()
        # the tile IMAGES too (2026-09-24): keyed by path, they outlived every reload
        getattr(lib, "_pl_tiles", {}).clear()
    try:
        ph._take_k = {}
    except Exception:
        pass
    # tile heads live in map.json, not in the loop's version key, so every
    # tile-keyed store goes; the dest-frame SOLVES stay (2026-09-19: 229
    # reloads a day wiped them) — poly_cache.reset_tile_caches
    reset_tile_caches()
    action("reload", "%d playlist/map caches dropped" % len(dropped), "takes page")
    return len(dropped)


# ONE RELOAD A BURST (the User 2026-09-19 "reload debounce fine"): the Takes page
# installs and reloads on every pen stroke — 78 reloads in one hour on 09-18,
# doubled since the install also reloads the bench. The first reload of a quiet
# spell runs at once (a single click is as live as before); any more inside the
# window fold into ONE trailing reload at its end, so the last stroke always
# lands. HOUSE_RELOAD_DEBOUNCE_S (default 2.0); 0 = every call reloads.
_RELOAD = {"last": 0.0, "timer": None, "folded": 0}
_RELOAD_LOCK = threading.Lock()


def _reload_window() -> float:
    try:
        return max(0.0, float(os.environ.get("HOUSE_RELOAD_DEBOUNCE_S", "2.0")))
    except (TypeError, ValueError):
        return 2.0


def reload_debounced(ph, now: float | None = None) -> dict:
    gc_refreeze_soon("a library reload")      # the reloaded library frozen once it settles (round 15; no-op unless quiet)
    win = _reload_window()
    now = time.monotonic() if now is None else now
    with _RELOAD_LOCK:
        quiet = win <= 0 or (_RELOAD["timer"] is None and now - _RELOAD["last"] >= win)
        if quiet:
            _RELOAD["last"] = now
        else:
            _RELOAD["folded"] += 1
            wait = max(0.0, win - (now - _RELOAD["last"]))
            if _RELOAD["timer"] is None:
                def fire():
                    with _RELOAD_LOCK:
                        _RELOAD["timer"] = None
                        _RELOAD["last"] = time.monotonic()
                        folded, _RELOAD["folded"] = _RELOAD["folded"], 0
                    n = reload_now(ph)
                    action("reload", "trailing reload for %d folded call(s), %d dropped" % (folded, n), "debounce")
                t = threading.Timer(wait, fire)
                t.daemon = True
                _RELOAD["timer"] = t
                t.start()
            return {"ok": True, "dropped": None, "deferred_s": round(wait, 2)}
    return {"ok": True, "dropped": reload_now(ph)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _clip_frame(self, lib, graph) -> None:
        """GET /v1/clip-frame?key=K&i=N: clip_frame_reply's answer (the player's route and the build's own decoder,
        clip_frames_main, answer from this one place, so their bytes are the same)."""
        from urllib.parse import parse_qs, urlparse
        q_ = parse_qs(urlparse(self.path).query)
        ck = (q_.get("key") or [""])[0]
        try:
            ci = int((q_.get("i") or ["-1"])[0])
        except ValueError:
            ci = -1
        got = clip_frame_reply(lib, graph, ck, ci)
        if isinstance(got, str):
            return self._json(404 if got == "no such clip frame" else 500, {"ok": False, "error": got})
        blob, hdrs = got
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        for k_, v_ in hdrs:
            self.send_header(k_, v_)
        self.end_headers()
        self.wfile.write(blob)

    def log_message(self, *a):        # the journal carries what matters
        return

    def _cors(self) -> None:
        """The wall is served from :8099 and reads this from :8100, so every
        response it touches is cross-origin. `renderAscii` samples the canvas
        with getImageData, and a cross-origin draw TAINTS that canvas — the
        read throws and the whole wall goes blank, not just the feed. A tainted
        canvas still draws, so this looks healthy right up until it isn't.

        On the error JSON too: the glass polls /v1/state to decide whether the
        feed is alive, and a 503 it cannot read is indistinguishable from a
        service that is down."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "X-Frame-T, X-Frame-No")

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _passthrough(self, code: int, ctype: str, raw: bytes) -> None:
        """A forwarded answer, whole, with the service's own status and type. No CORS header: what a service
        beside her says is for this machine's pages (crissa_rails)."""
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _page(self, name: str, ctype: str) -> None:
        path = ROOT / "display" / name
        try:
            raw = path.read_bytes()
        except OSError:
            return self._json(404, {"ok": False, "error": "no such page"})
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _sheet_send(self, path: Path, ctype: str) -> None:
        try:
            raw = path.read_bytes()
        except OSError:
            return self._json(404, {"ok": False, "error": "no such sheet"})
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _sheet_index(self) -> None:
        links: list[str] = []
        if SHEET_ROOT.is_dir():
            for p in sorted(SHEET_ROOT.iterdir()):
                if p.is_dir() and (p / "index.html").is_file():
                    links.append(
                        '<p><a href="/sheet/%s/">%s</a></p>' % (p.name, p.name)
                    )
        body = "\n".join(links) or "<p>no sheets yet</p>"
        html = (
            "<!doctype html><meta charset=utf-8><title>sheets</title>"
            "<style>body{background:#141416;color:#ddd;font:16px/1.4 system-ui;"
            "margin:24px}a{color:#e0c070}</style>"
            "<h1>sheets</h1>"
            "<p>Mouth stills and GIFs. Composer → sheets. "
            "A folder with index.html under visemes/picks/ is a page here.</p>"
            "%s"
        ) % body
        raw = html.encode()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _director_play(self) -> None:
        """After a store export, the next wall open plays the work loop. Off on the house:
        crissa_import.enabled() is false there, so this never cues the house conductor."""
        if not crissa_import.enabled():
            return
        key = crissa_import.pending_play()
        if not key:
            return
        ph = STATE.get("playhead")
        if ph is None:
            return
        try:
            ok = bool(ph.play(key, force=True))
        except Exception:  # noqa: BLE001  a missing loop leaves the pending file for the next start
            ok = False
        if not ok:
            return
        try:
            ph.seq.camp = key
        except Exception:  # noqa: BLE001
            pass
        crissa_import.clear_pending_play()
        action("play", "director work loop %s" % key, "director")

    def _wall_get(self, head: str) -> None:
        """The Wall page and its audio reads (2026-10-05; see WALL_PAGE)."""
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        if head in ("/wall", "/wall/"):
            self._director_play()
            return self._page("crissa-wall.html", "text/html; charset=utf-8")
        if head == "/v1/clock":
            return self._json(200, {"ok": True, "t": time.time(),
                                    "audio_at": "page" if PAGE_AUDIO is not None else "host"})
        pa = PAGE_AUDIO
        if not connect_origin_ok(self.headers):     # her lines are this machine's pages' only
            return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
        if head == "/v1/page-audio/status":
            return self._json(200, dict(pa.status(), ok=True))
        if head in ("/v1/page-audio", "/v1/page-audio/"):
            try:
                since = int((q.get("v") or ["0"])[0])
                wait = float((q.get("wait") or ["20"])[0])
            except ValueError:
                return self._json(400, {"ok": False, "error": "v is a number, wait is seconds"})
            ready = (q.get("ready") or ["1"])[0] == "1"
            got = pa.poll(since, wait, page=str((q.get("page") or [""])[0]), ready=ready)
            return self._json(200, dict(got, ok=True))
        rest = head[len("/v1/page-audio/"):]
        if rest.endswith(".wav"):
            raw = pa.wav(rest[:-4])
            if raw is None:
                return self._json(404, {"ok": False, "error": "no such line"})
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return
        if rest.endswith("/air"):
            try:
                wait = max(0.0, min(10.0, float((q.get("wait") or ["0"])[0])))
            except ValueError:
                wait = 0.0
            return self._json(200, {"ok": True, "start_at": pa.wait_air(rest[:-4], wait)})
        return self._json(404, {"ok": False, "error": "no such route"})

    def _wall_post(self, head: str, raw: bytes) -> None:
        """Writes from the page and from house-speak: loopback pages only (connect_origin_ok)."""
        if not connect_origin_ok(self.headers):
            return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
        if head == "/v1/mic":
            if PAGE_AUDIO is None:
                return self._json(404, {"ok": False, "error": "the microphone is the host's (CRISSA_AUDIO_AT=host)"})
            if len(raw) > MIC_CHUNK_MAX or len(raw) % 2:
                return self._json(400, {"ok": False, "error": "16-bit PCM, at most %d bytes a post" % MIC_CHUNK_MAX})
            code, got = page_mic_forward(raw, str(self.headers.get("X-Sample-Rate") or "16000"))
            return self._json(code, got)
        pa = PAGE_AUDIO
        if pa is None:
            return self._json(404, {"ok": False, "error": "her voice plays on the host (CRISSA_AUDIO_AT=host)"})
        if head in ("/v1/page-audio", "/v1/page-audio/"):
            try:
                lead = float(self.headers.get("X-Lead-S") or 0.0)
                meta = pa.push(raw, lead_s=lead, text=str(self.headers.get("X-Text") or ""))
            except ValueError as exc:
                return self._json(400, {"ok": False, "error": str(exc)})
            return self._json(200, dict(meta, ok=True))
        rest = head[len("/v1/page-audio/"):]
        lid, _, verb = rest.partition("/")
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        if verb == "air":
            try:
                until = body.get("until")
                meta = pa.ack(lid, float(body.get("start_at")), page=str(body.get("page") or ""),
                              until=None if until is None else float(until))
            except KeyError:
                return self._json(404, {"ok": False, "error": "no such line"})
            except (TypeError, ValueError) as exc:
                return self._json(400, {"ok": False, "error": str(exc)})
            return self._json(200, dict(meta, ok=True))
        if verb == "stop":
            return self._json(200, {"ok": True, "stopped": pa.cancel(lid)})
        if verb == "done":
            pa.done(lid)
            return self._json(200, {"ok": True})
        return self._json(404, {"ok": False, "error": "no such route"})

    def _foreign(self) -> bool:
        """Refuse a request that did not come from this machine (LOOPBACK_GUARD); True = refused and answered."""
        if not LOOPBACK_GUARD:
            return False
        why = foreign_request(self.headers)
        if not why:
            return False
        print("refused %s %s: not this machine (%s)" % (self.command, self.path.split("?")[0], why), flush=True)
        n = int(self.headers.get("Content-Length") or 0) if self.command == "POST" else 0
        if 0 < n <= 1 << 20:
            self.rfile.read(n)            # drain a small body so the keep-alive socket stays in step
        else:
            self.close_connection = True
        self._json(403, {"ok": False, "error": "this machine's pages only"})
        return True

    def do_GET(self) -> None:
        if self._foreign():
            return
        head = self.path.split("?")[0]
        if not AUTHORING and head.startswith(AUTHORING_GET) and head not in COMPOSER_PATHS:
            return self._json(404, {"ok": False, "error": "not in this edition"})
        ph: Playhead = STATE["playhead"]
        if head in _GUEST_COMMANDS and _guest_refused():
            return self._json(403, {"ok": False, "error": "a guest does not run commands"})
        if head in (
            "/",
            "/composer",
            "/composer.html",
            "/composer-classic",
            "/composer-classic.html",
        ):
            # THE COMPOSER MOVED. Unified page is house-clip-doctor :8199.
            # Old display/composer.html is museum-only. This process keeps
            # /v1/*, /frame*, /sheet*, /plate*.
            self.send_response(302)
            self._cors()
            on = crissa_agent.enabled()
            self.send_header("Location", composer_location(COMPOSER_URL, COMPOSER_LINK, on, WALL_PAGE)
                             if head in COMPOSER_PATHS else root_location(COMPOSER_URL, on, WALL_PAGE))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if head in ("/sheet", "/sheet/"):
            return self._sheet_index()
        if head.startswith("/sheet/"):
            path = sheet_path(head[len("/sheet/"):])
            if path is not None and path.is_dir():
                path = path / "index.html"
            ctype = SHEET_TYPES.get(path.suffix.lower()) if path else None
            if path is None or not path.is_file() or not ctype:
                return self._json(404, {"ok": False, "error": "no such sheet"})
            return self._sheet_send(path, ctype)
        if head.startswith("/plate/"):
            name = head[len("/plate/"):]
            # The hub plates, plus any a declared rack names. A rack's plate
            # is user-editable JSON, so it only counts once rack_plate() has
            # passed it: bare filename, .png/.jpg, resolving inside POSE_DIR.
            allowed = set(HUB_PLATE.values())
            allowed |= {r["plate"] for r in kit_taxonomy()[0] if r["plate"]}
            if name not in allowed:
                return self._json(404, {"ok": False, "error": "no such plate"})
            try:
                raw = (POSE_DIR / name).read_bytes()
            except OSError:
                return self._json(404, {"ok": False, "error": "no such plate"})
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(raw)))
            # The plates change only when the kit is re-shot; a thumbnail per
            # section per poll is not worth re-sending every 250 ms.
            self.send_header("Cache-Control", "public, max-age=300")
            self.end_headers()
            self.wfile.write(raw)
            return
        if head == "/v1/clip-frame":
            # round 14 (see CLIP_FRAME_ROUTE): one clip frame, unpainted, as this player would publish it
            if not CLIP_FRAME_ROUTE:
                return self._json(404, {"ok": False, "error": "off"})
            return self._clip_frame(ph.lib, ph.seq.graph)
        if head in ("/frame.jpg", "/frame-pip.jpg"):
            # The PIP is its own surface. /frame.jpg is the wall; /frame-pip.jpg
            # is the FaceTime window, which may carry a different mouth. With
            # HOUSE_PIP_W2L unset the two are byte-identical.
            if not rest_read(self.path):        # the resting desk Wall's slow read is not someone looking (2026-10-10)
                looked_at()
            blob, _, (f_t, f_mouth, f_clip) = ph.wait_frame(-1, pip=(head == "/frame-pip.jpg"), meta=True)
            if not blob:
                return self._json(503, {"ok": False, "error": "no frame yet"})
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Cache-Control", "no-store")
            # THE GLASS CANARY (2026-09-17): every frame says when its mouth
            # was picked, on this box's clock; a glass that shows it answers
            # POST /v1/glass-lag with how long that took. Same machine, same
            # clock — no sync to get wrong.
            self.send_header("X-Frame-T", "%.4f" % float(ph.frame_t or 0.0))
            self.send_header("X-Frame-No", str(int(ph.frame_no)))
            mb_ = mouth_box_header(f_mouth)
            if mb_:
                self.send_header("X-Mouth-Box", mb_.split(b":", 1)[1].strip().decode("ascii"))
            if clip_header_value(f_clip):
                self.send_header("X-Clip", clip_header_value(f_clip))
            fpv_ = face_point_value(ph.lib, f_clip)
            if fpv_:
                self.send_header("X-Face-Point", fpv_)
            self.end_headers()
            self.wfile.write(blob)
            return
        if head in ("/frame.mjpg", "/frame-pip.mjpg"):
            _pip = head.endswith("-pip.mjpg")
            self.send_response(200)
            self._cors()          # on the initial 200, before end_headers
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seen = -1
            try:
                while True:
                    looked_at()
                    blob, seen, (f_t, f_mouth, f_clip) = ph.wait_frame(seen, pip=_pip, meta=True)
                    _cv = clip_header_value(f_clip)
                    _fpv = face_point_value(ph.lib, f_clip)
                    if not blob:
                        continue
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                     b"X-Frame-T: " + ("%.4f" % float(f_t or 0.0)).encode() + b"\r\n"
                                     + mouth_box_header(f_mouth)
                                     + ((b"X-Clip: " + _cv.encode("ascii") + b"\r\n") if _cv else b"")
                                     + ((b"X-Face-Point: " + _fpv.encode("ascii") + b"\r\n") if _fpv else b"") +
                                     b"Content-Length: " + str(len(blob)).encode() +
                                     b"\r\n\r\n" + blob + b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                return
        if head in ("/phone-card", "/phone-card/") and not IS_HOUSE and crissa_phone_room.card_on():
            # YOUR PHONE AS A QR CARD (round 36, lib/crissa_phone_card): this machine's pages only (it carries a token)
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            if "state=1" in (self.path.partition("?")[2]):
                # round 38: the open card asks whether a phone is on now (it closes then); never mints a hand-off
                return self._json(200, {"ok": True, "active": bool(crissa_phone_room.active())})
            view = crissa_menu.phone_view()
            hand = crissa_phone_card.handoff(crissa_ports.url("phone")) if (view or {}).get("url") else {}
            c = crissa_phone_card.card(view, hand)
            c["firewall"] = _phone_firewall_plan()
            action("phone", "the QR card in the menu: %s" % ("QR for %s" % (view or {}).get("url") if c["ok"]
                                                              else c["why"]), "menu")
            if "json=1" in (self.path.partition("?")[2]):
                return self._json(200, dict(c, qr=bool(c["qr"])))
            raw = crissa_phone_card.page(c).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return
        if head in ("/connect", "/connect/") and crissa_agent.enabled():
            return self._page("crissa-connect.html", "text/html; charset=utf-8")
        if head in ("/init", "/init/") and crissa_init.page_served(self.path.split("?", 1)[1] if "?" in self.path else ""):
            # INIT IN A BROWSER (the User 2026-10-10): the short walk's page; /wall sends a first boot here. Also
            # Voice ID again's page (?voice_id=1, 2026-10-11) before its run exists: the name and the password
            return self._page("crissa-init.html", "text/html; charset=utf-8")
        if head.startswith("/init/film/") and head.endswith(".mp4") and crissa_init.serves():
            # ONE-PAGE SETUP (round 22): Lila's filmed lines, named in config/setup-films.json and nothing else
            f = crissa_init.film_file(head[len("/init/film/"):-len(".mp4")])
            if f is None:
                return self._json(404, {"ok": False, "error": "no such film"})
            return self._sheet_send(f, "video/mp4")
        if head == "/letter" and crissa_display.on():
            # THE LETTER (2026-10-16, lib/crissa_display): the document her display tool shows, 8.5 x 11 on the right
            return self._page("crissa-letter.html", "text/html; charset=utf-8")
        if head in ("/face", "/v1/face-point") and crissa_face.on():
            # FACE ONLY (the User 2026-10-15/16; lib/crissa_face): her face in a 9:16 window of its own, and where her
            # face is on the picture now (the playhead's frame, the head track's nose)
            if head == "/face":
                return self._page("crissa-face.html", "text/html; charset=utf-8")
            try:                                                     # round 29: does the page paint
                from urllib.parse import parse_qs as _pqs, urlparse as _pup
                _pq = _pqs(_pup(self.path).query).get("painted", [""])[0]
            except Exception:  # noqa: BLE001
                _pq = ""
            crissa_face.note_seen("http://127.0.0.1:%d" % PORT,     # round 28: a face page that dies comes back
                                  painted=(_pq == "1") if _pq in ("0", "1") else None)
            _ph = STATE.get("playhead")
            _hd = None
            try:
                _hd = unpack_head(_ph.lib.head(_ph.key, int(_ph.i))) if _ph is not None else None
            except Exception:  # noqa: BLE001 - no head: the middle of the picture
                _hd = None
            return self._json(200, dict(crissa_face.point(_hd, paint_wh()), clip=getattr(_ph, "key", None),
                                        shape=crissa_face.shape(),
                                        ptr=crissa_face.pointer_moved_at(),    # round 26: the bar wakes on any move
                                        veil=crissa_face.veil(),                # round 26: the Wall tile's BLUR
                                        close_key=crissa_face.close_key_on()))  # round 45: the tile's x
        if WALL_PAGE and head in ("/wall", "/wall/", "/v1/clock") or (PAGE_AUDIO is not None and head.startswith("/v1/page-audio")):
            return self._wall_get(head)
        if head == "/v1/mouths":
            # better mouths (2026-10-01): the switch and its install progress
            return self._json(200, dict(crissa_aligner.status(), ok=True))
        if head == "/v1/voice-install" and not crissa_home.is_house():
            # her voice's install as it runs or last ended (lib/crissa_voice_install, 2026-10-07): the Connect page's
            # "Her voice" box. Packs only; the house runs its own voices.
            import crissa_voice_install
            return self._json(200, crissa_voice_install.view())
        if head == "/v1/marks" and crissa_status.enabled():
            # status marks (2026-10-01): how each optional piece stands, never an error
            return self._json(200, {"ok": True, "marks": crissa_status.marks()})
        if head == "/v1/import" and crissa_import.enabled():
            return self._json(200, dict(crissa_import.status(POSE_DIR), ok=True))
        if head == "/v1/library" and crissa_library_sync.enabled():
            return self._json(200, dict(crissa_library_sync.status(), ok=True))
        if head == "/v1/agent" and crissa_agent.enabled():
            cfg = crissa_agent.load()
            return self._json(200, {"ok": True, "agent": crissa_agent.redacted(cfg),
                                    "last": CONNECT["last"], "busy": CONNECT["busy"],
                                    # round 17: the presets the page offers (CRISSA_CONNECT_CLAUDE_PRESET=0 hides Claude)
                                    "presets": crissa_agent.presets(), "offers": crissa_agent.offers()})
        if head == "/v1/announce":
            return self._json(200, dict(LANE.status(), ok=True))
        if head == "/v1/screen":
            # what a helper put beside her, read back so it can report the glass (lib/crissa_screen.py)
            return self._json(200, {"ok": True, "screen": SCREEN.view()})
        if head == "/v1/layout":
            # where her render sits now: on the Oracle live tile while a card is up, home otherwise (2026-10-06)
            # round 36: the card's stamp too, so a Wall whose tile the User sent home knows the next card from this one
            return self._json(200, {"ok": True, "layout": layout_now("wall"), "card_at": SCREEN.view().get("at")})
        if head == "/v1/ear":
            # The voice module's health, through the player: a helper asks here, never the module. 503 = a part
            # did not answer (its why is in the body); ?part=stt|asr reads one (lib/crissa_rails.py)
            q = urllib.parse.parse_qs(self.path.partition("?")[2])
            out = crissa_rails.ear(crissa_ports.url("stt"), crissa_ports.url("asr"), (q.get("part") or [""])[0])
            return self._json(200 if out["ok"] else 503, out)
        if head.startswith("/v1/rail/"):
            # PRESENCE, CAMERA, REGISTRATION through the player (2026-10-06): reads only, this machine's pages only
            # (who is home is not for any web page the User opens). Not set up here = said plainly.
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            name, sub = crissa_rails.split(head)
            return self._passthrough(*crissa_rails.forward(name, sub, query=self.path.partition("?")[2]))
        if head.startswith("/v1/cut/"):
            # THE WHOLE TEXT of a cut line (2026-10-06): what the glass links to. Loopback pages only.
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            name = head[len("/v1/cut/"):]
            f = crissa_home.HOME / "cut-text" / name
            if not re.fullmatch(r"[a-z]+-[0-9a-f]{12}\.txt", name) or not f.is_file():
                return self._json(404, {"ok": False, "error": "no such text"})
            blob = f.read_bytes()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return
        if head == crissa_menu.ROUTE and not crissa_home.is_house():
            # THE CRISSA KEY'S MENU (the User 2026-10-10, .101): what both Walls' menu shows now
            try:
                return self._json(200, crissa_menu.view(lead_base_ms=lead_base_ms()))
            except Exception as exc:  # noqa: BLE001  a menu that cannot be read never takes the player down
                return self._json(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
        if head == _INIT_ROUTE and crissa_init.serves():
            return self._json(200, crissa_init.apply("snapshot"))
        if head in crissa_init.AUX_GET_ROUTES and crissa_init.serves():
            # the Wall's other three reads beside Init (the register glass, her mouth, what the ear heard): the
            # house's home server answers them there; in a pack the id module's walker does, through crissa_init
            return self._json(200, crissa_init.get(head, self.path.split("?", 1)[1] if "?" in self.path else ""))
        if head == "/v1/state":
            seq: Sequencer = STATE["seq"]
            lib: Library = STATE["lib"]
            brain: "Brain | None" = STATE.get("brain")
            try:
                seq.flourish_pct = int(load_wall_prefs().get("flourish_pct", 35))
            except Exception:
                pass
            arr = lib.frames(ph.key)
            box = lib.box(ph.key, ph.i)
            return self._json(200, {
                "ok": True,
                "now": ph.key, "frame": ph.i, "of": (len(arr) if arr is not None else 0),
                # MONOTONIC, and the glass leans on it. `frame` is the index
                # inside the current clip and cycles, so it cannot tell a
                # running service from one wedged on the same index. This only
                # ever goes up, so "stalled" is unambiguous.
                "frame_no": ph.frame_no,
                # Is she in shot AT ALL, this frame. the User 2026-08-30: "facetime
                # screen just stays empty if she is on the other side of the
                # office or even out of it. She still comms unimpeded." An
                # empty window is the honest picture, not a failure — so the
                # glass is told, rather than left to crop a chair.
                "face": bool(box),
                "face_pct": round(100 * lib.face_pct(ph.key)),
                "register": seq.register,
                "cue": seq.cue,
                "queue": seq.queued(),
                # Same list, with the row ids and TTLs an operator UI needs.
                # `queue` stays keys-only: the glass and the tests read it.
                "queue_rows": seq.queue_rows(),
                "wraps": ph.wraps, "hub": seq.here, "role": seq.graph.role(ph.key),
                "why": seq.reason, "bag": seq.bag.depth, "pool": seq.pool(),
                "frames": ph.frame_no,  # alias of frame_no; glass may read either
                "fps": round(ph.frame_no / max(1e-6, time.time() - ph.started), 1),
                "clip_fps": CLIP_FPS,
                # conductor_role, NOT role: `role` in this payload is the
                # loaded CLIP's role (loop / slide / flourish) and the Composer
                # reads it. One wall conductor, benches beside it (2026-09-15).
                "conductor_role": "bench" if IS_BENCH else "wall",
                "glass_lag": glass_lag_state(),
                "wall_frame": wall_frame_state(lib, ph.key),
                "sync_zero_ms": SYNC_ZERO_MS, "sheet_lead_effective_ms": effective_sheet_lead_ms(),
                "glass_pad_ms": glass_pad_ms(), "glass_pad_how": (_GLASS_PAD["how"] or ("pinned" if (os.environ.get("HOUSE_BENCH_GLASS_PAD_MS") or "auto").strip().lower() not in ("", "auto") else "fallback")) if IS_BENCH else "",
                "mouth_url": MOUTH_URL,
                "looked_idle": look_idle(),
                "mouth_fps": RENDER_FPS,
                "semis": SEMIS.state(),
                "on_air": STATE["mouth"].speaking, "level": round(STATE["mouth"].smoothed, 3),
                # a host film's own baked line is playing (clip_audio, round 16: the desk Wall's build yields to it)
                "film_sound": clip_audio.playing(),
                # a pack's player keys (its paint count, Connect's wait, the speech lane, the helper's card, the last
                # cut); the house's payload carries exactly the keys it did before the Crissa merge (2026-10-11)
                **({} if IS_HOUSE else {
                    "paints": int(getattr(ph, "paints", 0)), "distinct_only": DISTINCT_ONLY,
                    "agent_waiting": connect_waiting(),
                    "speakers": say_pending(),     # lines in flight (round 18: Setup's coach waits for its own to end)
                    # the air time of the line on the mouth now (epoch s; round 20: Setup times her player lines by it)
                    "say_air_at": getattr(STATE["mouth"], "air_at", None),
                    "announce": {"depth": len(LANE.ann), "talk_waiting": len(LANE.talk), "turns": LANE.turns},
                    "screen_at": SCREEN.at,
                    "last_cut": CUTS["last"] if CUTS["last"] and time.time() - CUTS["last"].get("at", 0) < 900 else None,
                }),
                "hold_flourishes": bool(seq.hold_flourishes),
                "conversation_hold": {"armed": bool(CONV_HOLD), "conversation": bool(seq.conversation)},
                "flourish_pct": int(seq.flourish_pct),
                "wraps_before_fidget": wraps_before_fidget(seq.flourish_pct),
                # a call: the brain rotates the comms cores (docs/CRISSA-BODY-COMMS.md)
                "comms": bool(seq.comms),
                "comms_cores": list(seq.comms_cores),
                "comms_offer": seq.comms_offer() if seq.comms else [],
                "core_wraps": int(seq._core_wraps),
                "visemes": VISEMES,
                "pl_swaps": getattr(ph, "_pl_swaps", None),
                "pl_rel": getattr(ph, "_pl_rel", None),
                "pl_class": getattr(ph, "_pl_class", None),
                "pl_n": len(as_tiles(getattr(ph, "_pl_hit", None))),
                "strip_class": getattr(STATE.get("strip"), "last_class", None),
                "strip_idx": getattr(STATE.get("strip"), "last_idx", None),
                "lead_ms": VISEME_LEAD_MS,
                "playlist": bool(lib.playlist(ph.key)) if ph.key else False,
                # The same five integers the kit shows, for the clip that is
                # actually rolling -- so On air can say WHY the mouth looks
                # the way it does instead of only that a bank exists.
                "mouth_bank": (lib.mouth_health(ph.key) if ph.key else None),
                "anchor": MOUTH_ANCHOR,
                "nose_u": MOUTH_NOSE_U,
                "nose_v": MOUTH_NOSE_V,
                "turn_u": MOUTH_TURN_U,
                "turn_v": MOUTH_TURN_V,
                "nose_ema": MOUTH_NOSE_EMA,
                "warp": warp_dial(),
                "dental_cache": {
                    "on": dental_cache_on(),
                    "maps_limit": dental_maps_limit(),
                    **dental_cache_stats(),
                },
                # cheeks solve / cheeks maps / pin-shift LRUs (2026-09-14)
                "mouth_cache": mouth_cache_stats(),
                "maps_ahead": (_MAPS_AHEAD["stage"].state() if _MAPS_AHEAD.get("stage") is not None else None),
                "mouth_patch": dict(mouth_patch.state(), dials=patch_dials_print()),
                "paint_cores": paint_cores.state(),
                "fit": MOUTH_FIT,
                "pin": pin_dial(),
                "idle_pin": getattr(ph, "_idle_pin_dbg", None),
                # was a second "pin" key that shadowed the dial's name with
                # the match record (2026-09-12); the dial is "pin", this is the match
                "pin_match": getattr(ph, "_pin_dbg", None),
                "lip_place": getattr(ph, "_lip_dbg", None),
                "tri_refused": getattr(ph, "_tri_refused", None),
                "tri_band": getattr(ph, "_pl_band", None),
                "pin_cells": MOUTH_PIN_CELLS, "turn_place": MOUTH_TURN_PLACE,
                "paint_errs": int(getattr(ph, "_paint_errs", 0) or 0),
                "branch": getattr(ph, "_branch_dbg", None),
                "neck_cut": MOUTH_NECK_CUT,
                "idle_rest": MOUTH_IDLE_REST,
                "hole": MOUTH_HOLE,
                "alpha": MOUTH_ALPHA,
                "scale": MOUTH_SCALE,
                "xfade_ms": MOUTH_XFADE_MS, "edge_s": MOUTH_EDGE_S, "sag_k": MOUTH_SAG_K,
                "sheet_lead_ms": MOUTH_SHEET_LEAD_MS, "sheet_lead_semis": round(lead_semis(), 4), "semi_ms": semi_ms(), "timing_lead_ms": MOUTH_TIMING_LEAD_MS,
                "min_hold": MOUTH_MIN_HOLD, "cons_hold": MOUTH_CONS_HOLD, "tempo": MOUTH_TEMPO,
                "sent_pad": MOUTH_SENT_PAD, "word_pad": MOUTH_WORD_PAD,
            "pause_words": MOUTH_PAUSE_WORDS, "blip_words": MOUTH_BLIP_WORDS, "tail_words": MOUTH_TAIL_WORDS,
                "play_rate": PLAY_RATE, "buffer": MOUTH_BUFFER, "paint_all": MOUTH_PAINT_ALL,
                "scrub": dict(SCRUB),
                "say_pending": say_pending(),
                "autotune": autotune_state(),
                "transport": (transport_state() if (IS_BENCH and COMPOSER_TRANSPORT) else None),
                "repin": repin_state(),
                "clip_dials": {"on": CLIP_DIALS.get("on"), "clips": sorted(load_clip_dials())},
                "face_dials": {"on": FACE_DIALS.get("on"), "enabled": FACE_DIALS_ON, "faces": sorted(FACE_BANKS)},
                "drill": {"on": DRILL.get("on"), "key": DRILL.get("key"), "t0": DRILL.get("t0"), "loop": DRILL.get("loop"),
                          "passes": DRILL.get("passes"), "painted": len(DRILL.get("log") or []), "report": DRILL.get("report"),
                          "reading": bool(DRILL.get("reading")), "line": DRILL.get("text") or "", "line_src": DRILL.get("line_src"),
                          "words_error": DRILL.get("words_error"), "needs": DRILL.get("needs"), "graded": DRILL.get("graded")},
                "hold_class": getattr(STATE["mouth"], "force_class", None),
                "painted": ph.painted, "skip": ph.skip,
                "strip_pose": (seq.here if (STATE.get("strip") and STATE["strip"].bank(seq.here)) else None),
                "strips": (STATE["strip"].poses if STATE.get("strip") else []),
                "ram_mb": round(lib.bytes / 1e6),
                "ram_cap_mb": round(Library.CACHE_MB),
                "ram_evicted": lib.evicted,
                "ram_clips": len(lib._frames),
                "w2l": W2L_MODE, "w2l_ms": round(W2L_MS, 3),
                "w2l_misses": W2L_MISSES,
                # The render grid, and what it actually served. `w2l_ms` is the
                # cost of a GENERATE frame; a hold costs a paste. gens+holds is
                # every frame the neural mouth drew.
                # `w2l_fps` is what was ASKED for; `w2l_grid` is what the
                # session is actually rendering at after the CPU clamp.
                "w2l_fps": W2L_FPS, "w2l_gens": W2L_GENS, "w2l_holds": W2L_HOLDS,
                "w2l_grid": (_w2l_grid(_W2L_LIVE) if _W2L_LIVE is not None else None),
                "w2l_why": dict(W2L_WHY),
                "home": HOME, "at_home": seq.at_home(), "camp": seq.camp,
                # Where the queue ends. The kit greys against THIS, not `hub`,
                # so the Composer has to know when it moves or the sections go
                # stale the moment a crossing is queued.
                "tail": seq.hub_at_tail(),
                # THE WHOLE LOG, not a peek at it. 12 rows was a taste: the
                # Sequencer keeps HISTORY_CAP and the Composer was painting a
                # third of it, so "everything that played, in order" stopped
                # being true a minute into a session. The rows are ~80 bytes and
                # the glass repaints only when the signature changes.
                "history": seq.history_rows(),
                # what it has been DOING, for the Composer's Queue box. The
                # newest last, so the strip reads like a scroll rather than a
                # stack that jumps.
                "actions": list(ACTION_LOG),
                "brain": brain.status() if brain else None,
                "tour": seq.tour_status(),
                "private": bool(seq.private),
                "visit_lamp": STATE.get("visit_lamp") or "amber",
            })
        if head == "/v1/kit":
            seq: Sequencer = STATE["seq"]
            lib: Library = STATE["lib"]
            # The menu answers from WHERE THE QUEUE ENDS, not from where she is
            # standing: a pick lands after everything already queued, so that is
            # the hub it has to be legal from. `eligible` and `bridge` both come
            # from the Sequencer — the same calls insert() gates on — so the
            # Composer cannot offer a cue the queue will refuse.
            here, tail = seq.here, seq.hub_at_tail()
            racks, categories_src = kit_taxonomy()
            rack_by_id = {r["id"]: r for r in racks}
            buckets: dict[str, list] = {}
            orphans: list[str] = []
            unracked: list[str] = []
            keys = list(kit_keys(seq.graph))
            if seq.private:
                keys += casual_kit_keys(seq.graph)
            # FILM THAT EXISTS BUT IS NOT LIVE IS STILL IN THE LIBRARY.
            # kit_keys() answers "what may she be cued into" -- element and
            # both hubs -- which is the right question for a cue and the wrong
            # one for a library. It leaves out every harvest take (the mouth
            # bank's own source, and the only place a missing bank is a real
            # defect) and every unshipped second cut, so a rack could not say
            # "1 of 7" without lying and the Source films rack would be empty.
            # These are LISTED, never cueable: same disabled treatment a clip
            # she cannot reach already gets, carrying the mouth lamp and the
            # Doctor link, which is the whole reason to show them.
            #
            # Two rules are NOT relaxed. Casual stays hidden unless the room is
            # Secure -- listing it would leak what the gate exists to hide. And
            # one button per PICTURE still holds: listen/speak/speak-live share
            # clip-listen-back.mp4, and kit_keys already collapsed them, so the
            # extras dedup against the files already on the menu.
            seen = set(keys)
            seen_files = {str((seq.graph.row(k) or {}).get("file") or k)
                          for k in keys}
            listed: set[str] = set()
            for k in sorted(seq.graph.clips):
                if k in seen:
                    continue
                if seq.graph.casual(k) and not seq.private:
                    continue
                f = str((seq.graph.row(k) or {}).get("file") or k)
                if f in seen_files:
                    continue
                seen_files.add(f)
                listed.add(k)
                keys.append(k)
            for k in keys:
                start, end = seq.graph.start(k), seq.graph.end(k)
                role = seq.graph.role(k)
                row = seq.graph.row(k) or {}
                casual = seq.graph.casual(k)
                if role == "slide" and start != end:
                    sec = "travel"
                elif start or end:
                    sec = HUB_FOLD.get(start, start)
                else:
                    sec = "takes"
                v = seq.can_take(k, at_hub=tail)
                bridge = [] if v["ok"] else seq.bridge_to(k)
                if k in listed:
                    # Never cueable, and it says why in its own words rather
                    # than borrowing can_take's ("a face does not scoot the
                    # chair" is nonsense about a film that was never shipped).
                    v = {"ok": False, "why": (
                        "filmed, not shipped — it has no checked hubs, so the "
                        "Conductor cannot enter it")}
                    bridge = []
                # The clip's own rack wins; the hub bucket is the fallback,
                # so a box that has never set `rack` groups exactly as before.
                # A TYPO MUST NOT VANISH A CLIP: an id no rack declares falls
                # back and is reported in `orphans` rather than disappearing,
                # which is the failure this whole change is about.
                bucket = ("c:" + str(sec)) if casual else str(sec)
                named = str(row.get("rack") or "")
                if named:
                    if named in rack_by_id:
                        bucket = named
                    else:
                        orphans.append(k)
                if bucket not in rack_by_id:
                    unracked.append(k)
                buckets.setdefault(bucket, []).append({
                    "key": k,
                    # The User may rename what he reads. The override lives on
                    # the clip's own row so it travels with the clip and
                    # survives a shelve; the KEY is never renamed.
                    "label": row.get("label") or KIT_LABELS.get(k, k),
                    "hub": start,
                    "role": role,
                    "secs": lib.secs(k),
                    "from_here": start == tail,
                    "from_home": start == HOME,
                    # Reachable with no help. A false here is NOT a dead button:
                    # `bridge` says what the click will queue in front of it.
                    "eligible": bool(v["ok"]),
                    # Not a cue at all, as against a cue she cannot honour
                    # from here. The page draws these as a cut-out, and the
                    # rack counts them so "1 of 7" is the truth.
                    "listed_only": k in listed,
                    "why": v.get("why") or "",
                    "travels": list(v.get("hops") or []),
                    "travel_secs": round(sum(lib.secs(h) or 0.0
                                             for h in (v.get("hops") or [])), 2),
                    "bridge": bridge,
                    "bridge_secs": round(sum(lib.secs(h) or 0.0 for h in bridge), 2),
                    "keeper": bool(row.get("keeper")),
                    "casual": casual,
                    "category": seq.graph.category(k),
                    # THE MOUTH LAMP. Five integers off the playlist sidecar,
                    # never the sidecar (see Library.mouth_health). `matters`
                    # is the POLICY and it lives here, not in the summary: a
                    # loop is a clip she can talk through for minutes, so a
                    # missing bank shows on the wall; a 6 s flourish is what
                    # the hub strip is for, and lighting all 29 of them would
                    # make the lamp useless. Quiet is normal, lit is trouble.
                    "mouth": dict(
                        lib.mouth_health(k),
                        matters=(role == "loop"
                                 or bool((rack_by_id.get(bucket) or {})
                                         .get("wants_bank")))),
                    # The Clip Doctor take name -- the MP4 stem, which is what
                    # :8213 keys on. The page still has to check it against
                    # `doctor_takes` before offering a link.
                    "doctor": Path(str(row.get("file") or "")).stem,
                })
            # ONE LOOP, AND `if not items: continue` IS GONE. That line is
            # what made an empty rack inexpressible, and an empty rack is the
            # Mint's target: "Comes and goes -- 0 of 6, ask for one" is the
            # commissioning brief, not a gap to hide.
            by_cat: dict[str, list] = {}
            for r in racks:
                items = buckets.get(r["id"], [])
                by_cat.setdefault(r["category"], []).append({
                    "id": r["id"],
                    "label": r["label"],
                    # `sub` carries the note so the existing painter, which
                    # reads g.sub, needs no change to show it.
                    "sub": r["note"], "note": r["note"],
                    "hub": r["hub"],
                    "plate": ("/plate/" + r["plate"]) if r["plate"] else None,
                    "here": bool(r["hub"]) and r["hub"] == tail,
                    "want": r["want"], "wants_bank": r["wants_bank"],
                    # live = can be cued right now; total counts the film that
                    # exists. "1 of 7" needs both, and the difference is
                    # exactly the pile the Mint and the checker work through.
                    "live": sum(1 for it in items if not it["listed_only"]),
                    "total": len(items),
                    "empty": not items,
                    "category": r["category"],
                    "items": items,
                })
            categories = []
            for c in categories_src:
                rows = by_cat.get(c["id"], [])
                # The Secure gate, unchanged: a category that needs the room
                # sage ships no groups at all when it is not.
                if c.get("secure") and not seq.private:
                    rows = []
                categories.append(dict(c, groups=rows))
            groups = [g for c in categories for g in c["groups"]]
            return self._json(200, {
                "ok": True, "hub": here, "tail": tail, "home": HOME,
                "groups": groups, "categories": categories,
                "private": bool(seq.private),
                "visit_lamp": STATE.get("visit_lamp") or "amber",
                # A MENU THAT LIES IS WORSE THAN NO MENU. pose-map-build loads
                # a FIXED list of takes; ?take= a stem it never loaded opens a
                # different film with no warning, which is what
                # test_every_take_on_the_nav_menu_is_one_clip_doctor_loads was
                # written for. So ship the list and let the page grey the rest
                # rather than offer a link that misroutes.
                "doctor_base": DOCTOR_BASE,
                "doctor_takes": [t[0] for t in DOCTOR_TAKES],
                # The taxonomy, echoed, so the page can build a "move to
                # rack" menu without a second fetch.
                "racks": [{k: r[k] for k in
                           ("id", "label", "note", "category", "hub",
                            "want", "wants_bank")}
                          | {"count": len(buckets.get(r["id"], [])),
                             "empty": not buckets.get(r["id"])}
                          for r in racks],
                # Clips whose `rack` names nothing (a typo) and clips whose
                # bucket no rack declares. Both still render, both are said
                # out loud. Silence here is what lost four clips.
                "orphans": sorted(set(orphans)),
                "unracked": sorted(set(unracked)),
            })
        if head == "/v1/freelance":
            if FREELANCE is None:
                return self._json(503, {"ok": False, "error": "freelance store unavailable"})
            try:
                inv = json.loads((POSE_DIR / "inventory.json").read_text())
                plan = FREELANCE.tonight()
                return self._json(200, {
                    "ok": True,
                    "per_night": plan["per_night"],
                    "picks": plan["picks"],
                    "waiting": plan["waiting"],
                    "queued": plan["queued"],
                    # What the User may curate away. Core is computed, not
                    # tagged: pulling the only rest on a hub breaks the wall.
                    "core": sorted(FREELANCE.core_keys(inv)),
                    # Curating is two-way: what left the kit is listed so it can
                    # be clicked back in. Shelving is not deleting.
                    "shelved": FREELANCE.shelved(),
                })
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"ok": False, "error": str(exc)[:200]})
        if head == "/v1/queue":
            # ONE QUEUE, AND ANYONE MAY READ IT. /v1/state carries the same
            # rows, but the wall and the Control Panel want the playlist alone
            # without the playhead, the brain and the pool riding along. There
            # is exactly one list — this is a second view of it, never a second
            # ledger.
            seq: Sequencer = STATE["seq"]
            return self._json(200, {
                "ok": True, "now": ph.key, "hub": seq.here, "cue": seq.cue,
                "queue": seq.queued(), "queue_rows": seq.queue_rows(),
                # The queue is the future; this is where it went. Same list,
                # one step later — the Composer moves a row across rather than
                # dimming the button it came from.
                "history": seq.history_rows(),
            })
        if head == "/v1/shifts":
            # READ ONLY, AND OFF. A shift is a plan for a whole shift: a rack
            # and a cadence per block, never a clip at a time. lib/conductor.py
            # law 3 records what naming a clip in a scheduler path actually did
            # -- "the same tuck every ~28 s while the bag never decremented" --
            # and that scheduler was deleted. When the engine lands, a block
            # will narrow the pool the bag draws from for a window and nothing
            # more. Until then the Composer shows the plans and says plainly
            # that they are not driving her, because a band that looked live
            # and was not would be worse than no band.
            seq: Sequencer = STATE["seq"]
            racks, _cats = kit_taxonomy()
            by_id = {r["id"]: r for r in racks}
            live: dict[str, int] = {}
            for k in seq.graph.clips:
                rid = str((seq.graph.row(k) or {}).get("rack") or "")
                if rid and seq.graph.compose_ok(k):
                    live[rid] = live.get(rid, 0) + 1
            try:
                doc = json.loads(
                    (POSE_DIR / "shifts.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                doc = {}
            out = []
            for sid, row in (doc.get("shifts") or {}).items():
                blocks, need = [], {}
                for b in (row.get("blocks") or []):
                    rid = str(b.get("rack") or "")
                    r = by_id.get(rid) or {}
                    # WHAT IS IT SHORT OF. A rack's `want` is how much film it
                    # takes before it stops reading as a loop; counted once per
                    # rack however many blocks call it, because filming it once
                    # serves them all.
                    need[rid] = max(0, int(r.get("want") or 0) - live.get(rid, 0))
                    blocks.append({
                        "rack": rid,
                        "label": r.get("label") or rid,
                        "for_min": b.get("for_min"),
                        "once": bool(b.get("once")),
                        "secure": str(b.get("secure") or "any"),
                        "movable": bool(b.get("movable")),
                        "note": str(b.get("note") or ""),
                        "have": live.get(rid, 0),
                        "want": int(r.get("want") or 0),
                    })
                out.append({
                    "id": sid,
                    "label": row.get("label") or sid,
                    "note": row.get("note") or "",
                    "then": row.get("then") or "loop",
                    "blocks": blocks,
                    "mins": sum(int(b.get("for_min") or 0)
                                for b in (row.get("blocks") or [])),
                    "short": sum(need.values()),
                })
            out.sort(key=lambda r: r["short"])
            return self._json(200, {
                "ok": True,
                # Not a state, a fact: nothing here is wired to the Sequencer
                # yet. The page prints this rather than guessing.
                "on": False,
                "engine": "off — these do not drive her yet",
                "active": doc.get("active"),
                "seed": int(doc.get("seed") or 0),
                "shifts": out,
                # Reseeding buys order, not variety, and with a bag this small
                # it barely buys that. Measured 2026-09-05: 3 clips in the whole
                # inventory are scheduled:true, so the bag is 2 deep at `down`
                # and empty at every other hub, and Bag.draw already refuses the
                # same key twice running. Say so rather than sell it.
                "shuffle_buys": ("little — the bag here is %d clip(s)"
                                 % len(seq.pool())),
            })
        if head == "/v1/combos":
            return self._json(200, {"ok": True, "combos": load_combos(), "file": str(COMBOS_FILE)})
        if head == "/v1/glass-lag":
            return self._json(200, {"ok": True, "glass_lag": glass_lag_state()})
        if head == "/v1/turn-map":
            import urllib.parse as _upq
            _q = _upq.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            try:
                return self._json(200, ph.turn_map((_q.get("key") or [None])[0]))
            except Exception as e:  # noqa: BLE001 - a map never breaks the page
                return self._json(200, {"ok": False, "error": str(e)})
        if head == "/v1/mouth-pins":
            return self._json(200, mouth_pins_state(STATE.get("playhead")))
        if head == "/v1/mouth-presets":
            return self._json(200, {"ok": True, "presets": load_presets(), "default": library_dials(),
                                    "live": preset_dials(), "file": str(PRESETS_FILE),
                                    "clips": load_clip_dials(), "clip_on": CLIP_DIALS.get("on")})
        if head == "/v1/reload":
            # THE WALL FOLLOWS THE GRID (the User 2026-09-12: "how to make that
            # reactive after edit and reorder"): the Takes page installs the
            # picks and calls this; playlists and map heads re-read on the
            # next paint. No restart, no playhead change. A burst is ONE
            # reload now, then one more at the window's end (reload_debounced).
            return self._json(200, reload_debounced(ph))
        if head == "/v1/mouth":
            # A BENCH IS ITS OWN MAILBOX (2026-09-15). The wall's store lives
            # on :8099 and this route does not exist there; on a bench the
            # Composer's Say / drill post here and Mouth._poll reads here, so
            # the sheet never reaches the glass. Same contract, one
            # implementation (lib/mouth_mailbox.py).
            if MAILBOX is None:
                return self._json(404, {"ok": False, "error": "the wall's mouth store is :8099"})
            return self._json(200, MAILBOX.snapshot())
        if head == "/v1/health":
            moving = ph.frame_no > 0
            return self._json(200 if moving else 503,
                              {"ok": moving, "frames": ph.frame_no, "now": ph.key,
                               # round 43 (R41-7): her face window is up (the launcher's browser-Wall rule reads it)
                               **({"face_window": crissa_face.live()} if crissa_face.on() else {})})
        return self._json(404, {"ok": False, "error": "no such path"})

    def _import_upload(self) -> None:
        """THE UPLOAD (the User 2026-10-02): a loop file from the user's own computer, streamed into the drop
        folder; crissa_import.save_upload checks it the way Add does and the Add flow takes over. Local only:
        no outbound call, no library sync. Same switch as the import (CRISSA_IMPORT=1), same origin rule."""
        import urllib.parse as _up
        if not crissa_import.enabled():
            return self._json(404, {"ok": False, "error": "no such path"})
        if not connect_origin_ok(self.headers):
            return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n <= 0:
            return self._json(400, {"ok": False, "error": "choose a loop file (.zip) first"})
        if n > crissa_import.MAX_BYTES:
            return self._json(413, {"ok": False, "error": "that file is larger than a pack may be"})
        name = _up.unquote(str(self.headers.get("X-Pack-Name") or ""))
        part = crissa_import.upload_part()
        try:
            with part.open("wb") as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            if left:
                part.unlink(missing_ok=True)
                return self._json(400, {"ok": False, "error": "the upload stopped part way; try again"})
            out = crissa_import.save_upload(part, name, POSE_DIR)
        except crissa_import.ImportRefused as exc:
            return self._json(409, {"ok": False, "error": str(exc)})
        finally:
            part.unlink(missing_ok=True)
        action("import", "uploaded %s: %d files waiting to add" % (out["id"], out["files"]),
               str(self.headers.get("X-By") or "connect"))
        return self._json(200, out)

    def do_POST(self) -> None:
        if self._foreign():
            return
        head = self.path.split("?")[0]
        upload = head == "/v1/import/upload"    # a file, not JSON: its body is left for the upload (the User 2026-10-02)
        # the Wall page's bodies are audio, not JSON (2026-10-05); read here only when the page is on
        wall_raw = WALL_PAGE and (head == "/v1/mic" or head.startswith("/v1/page-audio"))
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if wall_raw:
                return self._wall_post(head, self.rfile.read(n) if n > 0 else b"")
            body = {} if upload else json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, TypeError):
            body = {}
        if authoring_refused(head, body):
            return self._json(404, {"ok": False, "error": "not in this edition"})
        if head in _GUEST_COMMANDS and _guest_refused():
            return self._json(403, {"ok": False, "error": "a guest does not run commands"})
        if upload:
            return self._import_upload()
        ph: Playhead = STATE["playhead"]
        if head.rstrip("/") in ("/v1/phone/allow", "/v1/phone/remove") and not IS_HOUSE and crissa_phone_room.card_on():
            # LET THE PHONE IN, ONE KEY, AND TAKE IT OUT WITH ANOTHER (the User 2026-10-26 / 10-28,
            # lib/crissa_phone_firewall): one ufw rule on the measured home network through pkexec. This machine only:
            # the socket's own peer AND a loopback page; {"dry": true} runs nothing.
            if not _peer_is_loopback(self.client_address) or not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            plan = _phone_firewall_plan()
            rm = head.rstrip("/").endswith("/remove")
            if bool(body.get("dry")):
                key = plan["remove"] if rm else plan
                return self._json(200, {"ok": True, "dry": True, "offer": key["offer"], "why": plan["why"],
                                        "rule": key["rule"], "argv": key["argv"], "where": key.get("where") or ""})
            done = crissa_phone_firewall.remove(plan) if rm else crissa_phone_firewall.allow(plan)
            action("phone", "the firewall %s key: %s" % ("remove" if rm else "allow",
                                                        done["said"] if done["ok"] else "not changed - " + done["said"]),
                   "menu")
            return self._json(200, dict(done, rule=(plan["remove"]["rule"] if rm else plan["rule"]),
                                        firewall=_phone_firewall_plan()))
        if head == "/v1/play":
            key = str(body.get("key") or "")
            who = str(body.get("by") or body.get("who") or "")
            # Composer off trigger: Crissa gateway down → Lila hosts. A
            # Composer stay on `work` was dumping Crissa on the Wall in
            # Render (2026-09-14 08:23). Auth/Secure/Listening stay on
            # this conductor (visit_lamp, private, comms).
            # A bench is never the glass, so the Render host rule does not
            # apply to it: a tuning click on Crissa's work loop must load Crissa's
            # work loop (2026-09-15).
            if (not IS_BENCH and key and not _crissa_gateway_up() and not _lila_host_key(key)
                    and _crissa_idle_steal(key)):
                action("play", "crissa gateway off → Lila host (was %s)" % key, who or "composer")
                try:
                    from crissa_gateway import lila_host_default
                    key = lila_host_default(comms=bool(ph.seq.comms))
                except Exception:
                    key = "lila-work"
                body["stay"] = True
                body["force"] = True
            # THE WORN LIBRARY'S OWN CLIP, BEFORE IT IS CAMPED OR HELD (the 10-29 review of cut 13, BLOCKER 1): play()
            # resolves a classic moment key, but camp and INIT_HOLD kept the raw one, so with v2 worn Setup's held
            # walk-in (crissa-walk-in -> v2-walk-in) lost its hold on the first frame and the camp replayed it
            key = ph.seq.graph.resolve(key)
            # Keepers have no hubs. Force is the only legal way in; a
            # Composer click that forgot the flag still previews.
            row = ph.seq.graph.row(key) or {}
            force = bool(body.get("force")) or bool(row.get("keeper")) \
                or ph.seq.graph.casual(key)
            # the rewind, above: same bench, same clip, no hub to travel to
            replay = bool(IS_BENCH and BENCH_REPLAY and key and key == ph.key and not force)
            if replay:
                force = True
            # Wizard and register paplay the wav beside this POST.
            audio = who not in ("init", "register")
            # Camp BEFORE play: a harvest oneshot can finish on the tick
            # thread before this handler sets camp, then home() dumps work
            # (Composer Tune → Lila, the User 2026-09-14).
            if bool(body.get("stay")):
                ph.seq.camp = key
            # .101 round 29: Setup's walk-in under her last sentence asks to keep her line (keep_line, init only).
            _keep = bool(body.get("keep_line")) and who == "init"
            INIT_HOLD["key"] = None                  # round 34: any load ends THE OFFICE WAITS' held first frame
            ok = ph.play(key, force=force, audio=audio, deliberate=not _keep)   # a load someone asked for stops her line
            if ok and bool(body.get("hold_first")) and who == "init" and not IS_HOUSE and not IS_BENCH:
                INIT_HOLD["key"] = key               # round 34: the clip held on its first frame (crissa_init.office_hold)
            if ok:
                if not bool(body.get("stay")):
                    ph.seq.camp = None
            elif bool(body.get("stay")):
                ph.seq.camp = None
            action("play", "%s%s%s → %s" % (key, " (replay)" if replay else (" (forced)" if force else ""), " (stay)" if body.get("stay") else "", "ok" if ok else "refused"),
                   str(body.get("who") or self.headers.get("X-House-Who") or "composer"))
            return self._json(200 if ok else 409, {
                "ok": ok, "now": ph.key,
                "error": None if ok else "%s does not attach to %s" % (key, STATE["seq"].here),
            })
        if head == "/v1/home":
            seq: Sequencer = STATE["seq"]
            # NOT HOME MID-INIT (the User 2026-09-14: "the Wall Crissa render of her working heads down loop
            # kept appearing between clips"): house-id's voice_register posts /v1/home by:register when
            # a register ends; during first boot that walked Lila out to Crissa's desk loop between host
            # lines. While Init runs the glass is Lila's; the routine's own home (by:init) still goes.
            by = str(body.get("by") or "")
            if by != "render" and not _crissa_gateway_up():
                why = house_default("Crissa gateway down", "her gateway is down") + " — Lila keeps the glass"
                action("home", "declined — " + why, by or "unknown")
                return self._json(200, {"ok": True, "taken": False, "why": why, "hub": seq.here})
            if by == "register" and _init_running():
                action("home", "declined — Init running, Lila keeps the glass", "register")
                return self._json(200, {"ok": True, "taken": False, "why": "Init running — Lila keeps the glass", "hub": seq.here})
            # UNATTRIBUTED IS NOT THE USER. The History names the hand on every
            # row, so defaulting a caller that did not say who it is to "user"
            # puts words in the User's mouth — a `wake` he never asked for reading
            # as his own pick. Three hands on one queue only stays legible if
            # the fourth case is called what it is.
            r = seq.bring_home(time.time(), by=str(body.get("by") or "unknown"))
            if str(body.get("by") or "") == "register":
                r["evicted"] = seq.graph.evict_guests()   # the host's guest leaves with the routine
            return self._json(200 if r.get("taken") else 409, r)
        if head == "/v1/cue":
            seq: Sequencer = STATE["seq"]
            ttl = body["ttl"] if "ttl" in body else cue_ttl_default(ph)
            try:
                ttl_f = float(ttl) if ttl is not None else 0.0
            except (TypeError, ValueError):
                ttl_f = 12.0
            # `by` is whoever is asking — the Composer, the wall, or Crissa
            # herself. One queue, three hands; the row carries the hand.
            who = str(body.get("by") or "unknown")
            admitted: list[str] = []
            if who == "register":
                # the Register host's loop (talk-angled) is parked off the
                # working kit; admit it and its slides for the routine
                admitted = seq.graph.admit(str(body.get("key") or ""))
            if "affect" in body:
                # Tool-carried persona. Cap, keyed on by, never errors.
                text = body["affect"]
                aff = "" if text is None else str(text).strip()[:600]
                br = STATE.get("brain")
                if br is not None and hasattr(br, "remember_affect"):
                    br.remember_affect(who, aff)
            action("cue", str(body.get("key") or ""), str(body.get("who") or self.headers.get("X-House-Who") or "composer"))
            if seq.camp:
                action("camp", "cleared by cue %s" % body.get("key"), "")
            seq.camp = None
            r = seq.insert(str(body.get("key") or ""), time.time(),
                           ttl_f, front=bool(body.get("next") or body.get("front")),
                           by=who,
                           at=body.get("at"),
                           # The Composer sends this on a cross-set pick: queue
                           # the crossing in front of the clip rather than
                           # refusing it. The rows are separate and deletable.
                           bridge=bool(body.get("bridge")))
            if admitted:
                r["admitted"] = admitted
            return self._json(200 if r.get("taken") else 409, r)
        if head == "/v1/abort-hub":
            # Vocative from work. JPEG Playhead.i, not glass <video>.
            # Cue first, then this, so take_cue can start the queued wake.
            r = ph.abort_to_hub()
            r["now"] = ph.key
            r["hub"] = STATE["seq"].here
            return self._json(200, r)
        if head == "/v1/comms":
            # The call's lifetime, for the ear / the session and for tests:
            # {"on": true|false}. On = the brain rotates the comms cores from
            # here; off = the bag deals again and home() may walk her back.
            seq: Sequencer = STATE["seq"]
            want = body.get("on")
            if want is None:
                return self._json(400, {"ok": False, "error": "on: true|false"})
            if bool(want):
                seq.comms = True
                seq._core_wraps = 0
                seq.reason = "comms on"
            else:
                seq.end_comms(str(body.get("why") or "off"))
            return self._json(200, {"ok": True, "comms": seq.comms, "offer": seq.comms_offer(), "hub": seq.here})
        if head == crissa_init.ACTION_ROUTE and crissa_init.serves():
            # the Register chapter's verbs (enroll_start / cancel / prearm to the coach, the glass write) and the
            # Wall's kiosk no-ops: the house's home server took them on its /v1/action; here the id module's bridge
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            try:
                return self._json(200, crissa_init.action(body if isinstance(body, dict) else {}))
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
        if head == crissa_menu.ROUTE and not crissa_home.is_house():
            # the Crissa key's menu: her microphone, Voice ID again, Stop Crissa - this machine's own pages only
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            try:
                b = body if isinstance(body, dict) else {}
                return self._json(200, crissa_menu.act(str(b.get("action") or ""), b, lead_base_ms=lead_base_ms()))
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
        if head == _INIT_ROUTE and crissa_init.serves():
            # the walk holds the Connect key on a test call: this machine's own pages only, like /connect
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            try:
                got = crissa_init.apply(str(body.get("action") or ""), body)
                setup_window_again(str(body.get("action") or ""), body, got)   # round 33: her window on a re-run
                return self._json(200, got)
            except Exception as exc:  # noqa: BLE001  a broken step never takes the player down
                return self._json(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
        if head == "/v1/speak" and os.environ.get("CRISSA_SPEAK_ROUTE", "1").strip().lower() not in ("0", "off", "false", "no"):
            # SPEAK THESE WORDS (2026-10-01): the verb every agent that drives her needs (MCP, krew). /v1/say
            # is authoring in a public build; this is the plain line: her voice, her mouth, the newest line
            # wins. Same loopback rule as Connect. Kill CRISSA_SPEAK_ROUTE=0.
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            # NO HIDDEN LIMIT (2026-10-06): the old [:2000] is talk.speak.max_chars, 0 = none; a set cut says so.
            text, cut = crissa_talk.keep(crissa_agent.speakable(str(body.get("text") or "")),
                                         crissa_talk.load()["speak"].get("max_chars"), "speak")
            if IS_HOUSE:
                text = text[:2000]        # the house's own cap, as before the Crissa merge (2026-10-11)
            if not text:
                return self._json(400, {"ok": False, "error": "text required"})
            note_cut(cut, "speak")
            who = str(body.get("voice") or os.environ.get("HOUSE_SAY_VOICE") or _avatar_current_name() or "crissa")
            queued = bool(body.get("queue"))    # queue: wait for what is on the air instead of stopping it
            if str(body.get("by") or "") == "lip sync" and lipsync_test_aligned_on():
                _ALIGN_FIRST.clear()            # one test line at a time: the newest press is the one that speaks
                _ALIGN_FIRST[text] = time.time()
            wav = str(body.get("wav") or "")
            if wav:
                why = speak_wav_ok(wav)
                if why:
                    return self._json(400, {"ok": False, "error": "wav refused: %s" % why})

            no_hold = bool(wav and body.get("timed") and TIMED_NO_HOLD)

            def _go():
                if not wav:
                    return LANE.say(text, who, queue=queued)
                if not queued:
                    say_stop_all()              # the newest line wins, as LANE.say's
                return _spawn_speak(text, who, wav=wav, no_hold=no_hold)
            if not IS_HOUSE and crissa_init.queue_takes():
                # SETUP'S ONE QUEUE (round 18, lib/crissa_init ONE VOICE AT A TIME): the coach's line waits for what is
                # in flight instead of stopping it; the caller says how long it may wait (its own request's timeout)
                try:
                    wait_s = max(0.5, min(25.0, float(body.get("wait_s") or 6.0)))
                except (TypeError, ValueError):
                    wait_s = 6.0
                crissa_init.speak_turn(_go, what="the coach's line %r" % text[:40], cap_s=wait_s)
            else:
                _go()
            action("speak", "%r as %s%s" % (text[:60], who, " (queued)" if queued else ""), str(body.get("by") or "speak"))
            out = {"ok": True, "spoken": text[:200], "voice": who, "queued": queued}
            if cut:
                out["cut"] = cut
            return self._json(202, out)
        if head == "/v1/announce":
            # ANNOUNCE (2026-10-06): a line from a helper that must never cut her: it queues and waits for quiet
            # (lib/crissa_speechq; rules and limits in the talk settings' `announce` block). Same loopback rule.
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            def _opt(k):
                try:
                    return None if body.get(k) is None else max(0.0, float(body[k]))
                except (TypeError, ValueError):
                    return None
            out = LANE.announce(crissa_agent.speakable(str(body.get("text") or "")), str(body.get("kind") or ""),
                                str(body.get("by") or "announce"), str(body.get("dedup_key") or "")[:200],
                                max_wait_s=_opt("max_wait_s"), speech_pause_s=_opt("speech_pause_s"),
                                voice=str(body.get("voice") or ""))
            return self._json(202 if out.get("queued") else (200 if out.get("ok") else 400), out)
        if head == "/v1/screen":
            # THE CARD BESIDE HER (2026-10-06): words, art, a page or a web address from a helper; same loopback rule
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            code, out = SCREEN.apply(body)
            if code == 200:
                action("screen", "cleared" if not out["screen"]["up"] else "card up (%s)" % ", ".join(
                    k for k in crissa_screen.FIELDS if k in out["screen"]), str(body.get("by") or "helper"))
                out["layout"] = layout_now(str(body.get("by") or "helper"))  # her render follows the card
                if not crissa_layout.page_up(out["screen"]) and crissa_display.on():
                    import crissa_window                # the card is down (round 36: or has no page left): the sheet closes
                    threading.Thread(target=crissa_window.close_sheet, daemon=True, name="letter-close").start()
            return self._json(code, out)
        if head == "/v1/display-tool":
            # HER DISPLAY TOOL ON A SPOKEN TURN (round 26, .101 10-16: "no display although she says she did"): the ear
            # bridge offers the tool and runs each call here, where the card and the letter window live
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            if not crissa_display.on():
                return self._json(404, {"ok": False, "error": "the display tool is off here"})
            args = body.get("arguments") if isinstance(body.get("arguments"), dict) else {}
            return self._json(200, {"ok": True, "result": display_tool(str(body.get("name") or ""), args)})
        if head == "/v1/ear/open":
            # keep the ear open: forwarded to the voice module's turns service; this machine only (2026-10-06)
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            return self._passthrough(*crissa_rails.forward_url(crissa_ports.url("stt"), "/v1/ear/open",
                                                               label="voice module's turns", method="POST",
                                                               body=body))
        if head.startswith("/v1/rail/"):
            # a write to a service she sits beside: only the paths crissa_rails.POSTS names, this machine only
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            name, sub = crissa_rails.split(head)
            out = crissa_rails.forward(name, sub, method="POST", body=body)
            if out[0] < 400:
                action("rail", "%s %s" % (name, sub), str(body.get("by") or "helper"))
            return self._passthrough(*out)
        if head == "/v1/cut":
            # A SPEAKER'S CUT (the User 2026-10-07): house-speak cut a line at its cap and kept the whole of it in
            # <home>/cut-text/; the glass says so with the whole text a click away, like a cut reply.
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            name = str(body.get("name") or "")
            if not re.fullmatch(r"[a-z]+-[0-9a-f]{12}\.txt", name) or not (crissa_home.HOME / "cut-text" / name).is_file():
                return self._json(404, {"ok": False, "error": "no such text"})
            try:
                kept, total = int(body.get("kept") or 0), int(body.get("total") or 0)
            except (TypeError, ValueError):
                return self._json(400, {"ok": False, "error": "kept and total are numbers"})
            what = re.sub(r"[^a-z ]", "", str(body.get("what") or "line").lower())[:20] or "line"
            note_cut({"kept": kept, "total": total, "at": time.time(), "full_text": name,
                      "note": "Cut at %d of %d characters." % (kept, total)}, what)
            return self._json(200, {"ok": True})
        if head == "/v1/stop":
            # THE HARD STOP, by name (2026-10-06): same as /v1/say {"stop": true} - everything on the air and every
            # queued line of talk; queued announcements stay. The ear's barge-in calls this. Loopback rule.
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            n = LANE.stop()
            action("stop", "%d speaker%s" % (n, "" if n == 1 else "s"), str(body.get("by") or "stop"))
            return self._json(200, {"ok": True, "stopped": n, "say_pending": say_pending()})
        if head == "/v1/voice-install" and not crissa_home.is_house():
            # "Retry voice install" on the Connect page (2026-10-07): the install again, in the background
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            refused = crissa_menu.face_gate("voice_install")   # Security On: his face first (round 17 follow-ups)
            if refused is not None:
                return self._json(200, refused)
            import crissa_voice_install
            out = crissa_voice_install.retry(log=lambda m: print(m, flush=True))
            action("voice", "voice install: %s" % (out.get("note") or out.get("error")), str(body.get("by") or "connect"))
            return self._json(200 if out.get("ok") else 409, out)
        if head == "/v1/mouths":
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            if not isinstance(body.get("better"), bool):
                return self._json(400, {"ok": False, "error": "better: true|false"})
            refused = crissa_menu.face_gate("mouths")       # Security On: his face first (round 17 follow-ups)
            if refused is not None:
                return self._json(200, refused)
            out = crissa_aligner.set_better(body["better"])
            action("mouths", "better mouths %s" % ("on" if body["better"] else "off"), str(body.get("by") or "connect"))
            return self._json(200 if out.get("ok") else 409, out)
        if head in ("/v1/skin/stage", "/v1/skin/answer"):
            # INSTALL FROM THE STORE AS A SKIN (the User 2026-10-31: "is there a path to install the full libraries into
            # theirs from Store?"; crissa-6a): the Store's Export returns to Connect with the sale link; the pack is
            # fetched (store.zoeyos.dev only), checked and converted, then HELD until the buyer answers "Install <name>?".
            # Installed whole into <state home>/libraries/, never worn (lib/crissa_skin_add). OFF: CRISSA_SKIN_STORE=1.
            import crissa_skin_add
            if not crissa_skin_add.store_enabled():
                return self._json(404, {"ok": False, "error": "no such path"})
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            try:
                if head == "/v1/skin/stage":
                    out = crissa_skin_add.stage_sale(str(body.get("url") or ""))
                    action("skin", "staged %s: asking" % out["key"], "connect")
                elif body.get("yes") is True:
                    out = crissa_skin_add.confirm(str(body.get("token") or ""))
                    action("skin", "installed %s (not worn)" % out["key"], "connect")
                else:
                    out = crissa_skin_add.discard(str(body.get("token") or ""))
            except crissa_skin_add.Refused as exc:
                return self._json(409, {"ok": False, "error": str(exc)})
            return self._json(200, out)
        if head == "/v1/import/fetch" and crissa_import.enabled():
            # Export to local library: one sale zip from the store, checked, added. The URL is not logged.
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            if STATE["mouth"].speaking:
                return self._json(409, {"ok": False, "error": "she is speaking; try again in a moment"})
            try:
                out = crissa_import.take_sale(str(body.get("url") or ""), str(body.get("play") or ""), POSE_DIR)
            except crissa_import.ImportRefused as exc:
                return self._json(409, {"ok": False, "error": str(exc)})
            action("import", "sale pack %s play %s" % (out["id"], out.get("play") or "-"),
                   str(body.get("by") or "director"))
            if not out["restart_needed"]:
                reload_debounced(ph)
            return self._json(200, out)
        if head == "/v1/import" and crissa_import.enabled():
            # THE IMPORT PATH (2026-10-01): a pack in the drop folder adds to this library. It only adds, the
            # whole pack is verified before a byte lands, and never while she is speaking. CRISSA_IMPORT=1.
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            if STATE["mouth"].speaking:
                return self._json(409, {"ok": False, "error": "she is speaking; try again in a moment"})
            try:
                out = crissa_import.apply(str(body.get("pack") or ""), POSE_DIR)
            except crissa_import.ImportRefused as exc:
                return self._json(409, {"ok": False, "error": str(exc)})
            action("import", "pack %s: %d files, %d clips" % (out["id"], out["files_added"], len(out["clips_added"])),
                   str(body.get("by") or "import"))
            if not out["restart_needed"]:
                reload_debounced(ph)
            return self._json(200, out)
        if head in ("/v1/library", "/v1/library/sync") and crissa_library_sync.enabled():
            # LIBRARY SYNC (2026-10-01): link the user's account (a pasted token) and pull their finished packs
            # into the import folder. The import's only-add rules decide what lands. CRISSA_LIBRARY_SYNC=1.
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            if head == "/v1/library":
                try:
                    stored = crissa_library_sync.save(body)
                except crissa_library_sync.SyncError as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})
                action("library", "linked %s" % (stored.get("url") or "nothing"), str(body.get("by") or "connect"))
                return self._json(200, dict(crissa_library_sync.status(), ok=True))
            got = crissa_library_sync.sync(POSE_DIR, speaking=lambda: bool(STATE["mouth"].speaking))
            if got.get("added"):
                reload_debounced(ph)
            return self._json(200, dict(got, ok=got.get("state") in ("ok", "not linked")))
        if head in ("/v1/agent", "/v1/agent/test", "/v1/agent/models", "/v1/ask") and crissa_agent.enabled():
            if not connect_origin_ok(self.headers):
                return self._json(403, {"ok": False, "error": "only this machine's pages may do that"})
            if head == "/v1/agent":
                refused = crissa_menu.face_gate("connect")   # Security On: his face first (round 17 follow-ups)
                if refused is not None:
                    return self._json(200, refused)
                try:
                    # adopt: the user picked a Detected endpoint; its key stays where it lives
                    stored = crissa_agent.adopt(str(body["adopt"])) if body.get("adopt") else crissa_agent.save(
                        {k: v for k, v in body.items() if k != "key_ref"})
                except ValueError as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})
                CONNECT["conv"].reset()
                action("connect", "agent %s (%s)" % (stored.get("url") or "cleared",
                                                     stored.get("preset") or crissa_agent.DEFAULT_PRESET),
                       str(body.get("by") or "connect"))
                # the reply reads back through load(), so an adopted key_ref shows has_key when it resolves
                return self._json(200, {"ok": True, "agent": crissa_agent.redacted(crissa_agent.load())})
            if head == "/v1/agent/models":
                # THE MODEL LIST (round 15): the provider's chat models for the form's pick list. The form's address
                # and key, else what is stored; a stored key goes only to its own host (as the test). Never logged.
                cfg = crissa_agent.load()
                if str(body.get("url") or "").strip():
                    why = crissa_agent.validate({"url": body.get("url"), "preset": body.get("preset") or None})
                    if why:
                        return self._json(400, {"ok": False, "error": why, "models": []})
                    same_host = crissa_agent._host(str(body["url"])) == crissa_agent._host(str(cfg.get("url") or ""))
                    trial = {"url": str(body["url"]).strip(), "preset": str(body.get("preset") or "")}
                    key = str(body.get("key") or "").strip()
                    trial["key"] = key or (cfg.get("key") if same_host else "")
                    cfg = trial
                return self._json(200, crissa_agent.list_models(cfg))
            if head == "/v1/agent/test":
                # test what is on the form when it carries a url, else what is stored;
                # a form with no key tests with the stored key only on the stored host
                cfg = crissa_agent.load()
                if str(body.get("url") or "").strip():
                    why = crissa_agent.validate(body)
                    if why:
                        return self._json(400, {"ok": False, "error": why})
                    same_host = crissa_agent._host(str(body["url"])) == crissa_agent._host(str(cfg.get("url") or ""))
                    # a page never names where a key is read from: only an adopted offer does (key_ref)
                    trial = {k: body[k] for k in crissa_agent.FIELDS if body.get(k) and k != "key_ref"}
                    if not trial.get("key") and same_host and cfg.get("key"):
                        trial["key"] = cfg["key"]
                    cfg = trial
                return self._json(200, crissa_agent.test(cfg))
            text = str(body.get("text") or "").strip()[:2000]
            if not text:
                return self._json(400, {"ok": False, "error": "text required"})
            who = str(body.get("voice") or os.environ.get("HOUSE_SAY_VOICE") or _avatar_current_name() or "crissa")
            if not crissa_agent.configured(crissa_agent.load()):
                # SET UP LATER (the User 2026-10-10): with no agent saved she says so, at most every 30 s, the way
                # every reply is said (text on the glass, aloud when a voice is installed); never on the house
                said = crissa_init.not_connected_line()
                if said:
                    LANE.say(said, who)
                    CONNECT["last"] = {"asked": text[:200], "by": str(body.get("by") or "ask"), "at": time.time(),
                                       "reply": said, "error": "not connected", "ms": 0}
                return self._json(409, {"ok": False, "connect": True, "said": said,
                                        "error": "no agent connected — open /connect"})
            by = str(body.get("by") or self.headers.get("X-House-Who") or "ask")
            if body.get("speak") is False:      # the phone (one conversation): the words back, nothing said here
                rec = connect_ask(text, who, by, speak=False)
                return self._json(200 if not rec["error"] else 502, dict(rec, ok=not rec["error"]))
            if body.get("wait"):
                rec = connect_ask(text, who, by, check_s=ASK_SPEAK_CHECK_S)
                return self._json(200 if not rec["error"] else 502, dict(rec, ok=not rec["error"]))

            def _turn() -> None:
                CONNECT["busy"] += 1
                try:
                    connect_ask(text, who, by, check_s=ASK_SPEAK_CHECK_S)
                finally:
                    CONNECT["busy"] -= 1
            behind = connect_waiting(after_s=0.0)
            threading.Thread(target=_turn, name="connect-ask", daemon=True).start()
            out = {"ok": True, "asked": text[:200], "voice": who}
            if behind:                      # queued behind a question the endpoint has not answered yet
                out.update(waiting_s=behind["s"],
                           note="Asked - still waiting on your endpoint (%d s on the last question)." % behind["s"])
            return self._json(202, out)
        if head == "/v1/face-window" and crissa_face.on():
            # FACE ONLY: {"save": {x, y, w, h}} keeps the window's place (the page posts it); {"open": true} opens it
            if isinstance(body.get("save"), dict):
                # round 26: the browser's own bounds, and a window let go near home snaps there (crissa_face.keep)
                return self._json(200, crissa_face.keep(body["save"], "http://127.0.0.1:%d" % PORT))
            if isinstance(body.get("resize"), dict):
                # THE TILE'S BAR (the User 2026-10-16): {"resize": {"b": {x, y, w, h}, "shape": face|room, "step": -1|0|1}}
                rz = body["resize"]
                try:
                    step = max(-3, min(3, int(rz.get("step") or 0)))
                except (TypeError, ValueError):
                    step = 0
                nb = crissa_face.resize_window("http://127.0.0.1:%d" % PORT, rz.get("b") or {},
                                               str(rz.get("shape") or ""), step)
                return self._json(200, {"ok": True, "bounds": nb, "shape": nb["shape"]})
            if body.get("where"):
                # .101 round 28: a drag starts from where the browser says the window is (window.screenX lies under Xwayland)
                try:
                    import crissa_window
                    wb = crissa_window.face_bounds("http://127.0.0.1:%d/face" % PORT)
                except Exception:  # noqa: BLE001  unknown: the page keeps its own guess
                    wb = None
                return self._json(200, {"ok": wb is not None, "bounds": wb})
            if isinstance(body.get("move"), dict):
                # round 27 (.101: "could not drag it"): the page drags its frameless window, {"move": {x, y}}
                mv = body["move"]
                try:
                    import crissa_window
                    ok = crissa_window.move_face("http://127.0.0.1:%d/face" % PORT, int(mv.get("x")), int(mv.get("y")))
                except (TypeError, ValueError):
                    ok = False
                return self._json(200, {"ok": bool(ok)})
            if isinstance(body.get("veil"), str):
                return self._json(200, {"ok": True, "veil": crissa_face.set_veil(body["veil"])})
            if body.get("close") and crissa_face.close_key_on():
                # round 45 (.101 round 43: "how do I close her face tile? no x or button"): the tile's x
                crissa_face.note_closed()
                import crissa_window
                ok = crissa_window.close_face("http://127.0.0.1:%d/face" % PORT)
                action("face only", "closed" if ok else "close asked (the page closes itself)", "the tile's x")
                return self._json(200, {"ok": bool(ok)})
            if body.get("closed"):
                crissa_face.note_closed()           # round 28: closed on purpose (the page's pagehide), never reopened
                return self._json(200, {"ok": True})
            if body.get("open"):
                crissa_face.open_window("http://127.0.0.1:%d" % PORT)
                action("face only", "opened", str(body.get("by") or "menu"))
                return self._json(200, {"ok": True, "opening": True})
            return self._json(400, {"ok": False, "error": "save or open"})
        if head == "/v1/ft-desk":
            # First Crissa tool call while FaceTime is up: she spoke on the
            # webcam until this. Fade to heads-down talking+glance (or
            # work until that Imagine lands). TV static is the way in;
            # there is no leave slide.
            seq: Sequencer = STATE["seq"]
            if (ph.key != USER_TALK_KEY
                    and not (getattr(ph, "_cam", None)
                             and ph._cam.get("to") == USER_TALK_KEY)):
                return self._json(200, {"ok": True, "skipped": True, "now": ph.key})
            dest = WORK_GLANCE_KEY
            if not seq.graph.compose_ok(dest):
                dest = seq.graph.rest_for(HOME) or "work"
            seq.abandon_ft()
            seq.end_comms("tool")
            rec = seq.insert(dest, time.time(), ttl=0, by="tool")
            cued = seq.take_cue(time.time()) if rec.get("taken") else None
            if cued:
                ph._arm_cam(cued, kind="fade")
            action("ft-desk", dest, "tool")
            return self._json(200, {
                "ok": True, "dest": dest, "cued": cued, "now": ph.key,
                "taken": bool(rec.get("taken")),
            })
        if head == "/v1/greet-cancel":
            # round 28: the ear heard him go on speaking after the wake; the held close-up greet is not said
            STATE["greet_cancel_ts"] = time.time()
            return self._json(200, {"ok": True, "pending": bool(STATE.get("vocative_ts"))})
        if head == "/v1/vocative":
            # Hey Crissa from work. Cue FaceTime close-up (insert does not
            # cut), then abort-hub. Sit-up vocative-up is parked.
            seq: Sequencer = STATE["seq"]
            held = auth_already_held(_http_json_get(PRESENCE_URL) if PRESENCE_URL else None)
            # who the ear says spoke (BB, 2026-10-10): "named" false = not the User, so the greet carries no name
            STATE["vocative_named"] = (bool(body.get("named")) if "named" in body else None)
            STATE["vocative_ts"] = time.time()          # round 28: a greet-cancel after this drops the greet
            rec = seq.cue_vocative_from_work(time.time(), auth_held=held)
            rec["auth_held"] = held
            rec["greet"] = wake_greet_line(named=STATE["vocative_named"])
            abort = None
            sitting = (USER_TALK_KEY, USER_TALK_ENTER)
            if rec.get("taken") or any(k in seq.queued() for k in sitting):
                abort = ph.abort_to_hub()
            cover = rec.get("cover")
            if rec.get("taken") and cover:
                _spawn_speak(str(cover), "crissa")
            if not rec.get("taken") and rec.get("listen_now"):
                # heads up on the wake (a library with no close-up), the ear says its ack at once where she is; the listen
                # loop waits for the seam (VOCATIVE_AT_SEAM, the User 2026-10-30), or plays now with the switch off
                if VOCATIVE_AT_SEAM:
                    q = seq.insert(str(rec["listen_now"]), time.time(), ttl=0, front=True, by="vocative")
                    rec["heads_up"] = "queued for the seam" if q.get("taken") else "not queued: %s" % q.get("why")
                else:
                    rec["heads_up"] = bool(ph.play(str(rec["listen_now"]), force=True, audio=False))
                action("vocative", "heads up: %s (%s)" % (rec["listen_now"], rec["heads_up"]), "wake")
            out = dict(rec)
            out["abort"] = abort
            out["now"] = ph.key
            out["hub"] = seq.here
            out["ok"] = bool(rec.get("taken") or (abort and abort.get("played")))
            return self._json(200 if rec.get("taken") else 409, out)
        if head == "/v1/freelance":
            if FREELANCE is None:
                return self._json(503, {"ok": False, "error": "freelance store unavailable"})
            op = str(body.get("op") or "add")
            try:
                if op == "drop":
                    FREELANCE.drop(str(body.get("id") or ""))
                else:
                    text = str(body.get("text") or "").strip()
                    if not text:
                        return self._json(400, {"ok": False, "error": "say what to build"})
                    FREELANCE.add(text, by=str(body.get("by") or "user"))
                plan = FREELANCE.tonight()
                return self._json(200, {"ok": True, "picks": plan["picks"],
                                        "waiting": plan["waiting"],
                                        "queued": plan["queued"]})
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"ok": False, "error": str(exc)[:200]})
        if head == "/v1/shelve":
            if FREELANCE is None:
                return self._json(503, {"ok": False, "error": "freelance store unavailable"})
            key = str(body.get("key") or "")
            op = str(body.get("op") or "shelve")
            try:
                if op == "restore":
                    out = FREELANCE.unshelve(key)
                elif op == "rename":
                    out = FREELANCE.rename(key, str(body.get("label") or ""))
                else:
                    out = FREELANCE.shelve(key)
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"ok": False, "error": str(exc)[:200]})
            if not out.get("ok"):
                return self._json(409, out)
            out["shelved"] = FREELANCE.shelved()
            if op == "rename":
                # A label is read at paint time, so this one CAN take effect now
                # — but only if the live row is updated too. The Graph was built
                # from the file at boot; writing the file alone would have the
                # Composer report a rename the kit never shows.
                row = STATE["seq"].graph.row(key)
                if row is not None:
                    row["label"] = out.get("label")
                return self._json(200, out)
            # NOTHING IS DELETED and nothing changes on the wall yet: the
            # Library holds decoded frames and the Graph was built at boot, so
            # say plainly that the roll keeps the clip until a restart rather
            # than reporting a success the glass will not show.
            out["restart_needed"] = True
            return self._json(200, out)
        if head == "/v1/queue":
            seq = STATE["seq"]
            op = str(body.get("op") or "")
            if op == "clear":
                return self._json(200, seq.clear_queue())
            if op in ("delete", "move"):
                # Id-addressed is the real path: the head pops at every seam, so
                # an index the operator saw a moment ago may already point at a
                # different clip. `at` stays for the original API and its test.
                item_id = body.get("id")
                if item_id:
                    if op == "delete":
                        r = seq.drop_id(str(item_id))
                    else:
                        r = seq.move(str(item_id), body.get("to"))
                    # A row that played while the click was in flight is a miss,
                    # not a failure — 200 with the fresh list, so the UI repaints
                    # to the truth instead of showing an error over a race.
                    return self._json(200, r)
                if op == "move":
                    return self._json(400, {"ok": False, "error": "move needs id and to"})
                try:
                    at = int(body.get("at"))
                except (TypeError, ValueError):
                    at = -1
                r = seq.drop(at)
                return self._json(200 if r.get("ok") else 409, r)
            return self._json(400, {"ok": False, "error": "op is move, delete or clear"})
        if head == "/v1/register":
            seq = STATE["seq"]
            want = str(body.get("name") or "")
            if want not in REGISTERS:
                return self._json(409, {"ok": False, "error": "no register %r" % want,
                                        "have": sorted(REGISTERS)})
            seq.register = want
            return self._json(200, {"ok": True, "register": want, "pool": seq.pool()})
        if head in ("/v1/mouth-tune", "/v1/mouth-presets") and FACE_DIALS.get("on") and (
                body.get("lock") or body.get("relock") is not None or body.get("lock_row") is not None
                # "set as default" writes the LIVE dials into this loop's set (2026-09-25)
                or (COMPOSER_TRANSPORT and body.get("clip") and isinstance(body.get("set"), dict))):
            # A LOCK WRITES THE LIVE DIALS TO THE HOME LIBRARY'S FILE, and right now
            # the live dials are a guest's (2026-09-17). Her lock is her own page's.
            return self._json(409, {"ok": False, "error": (
                "%s is on this conductor wearing her own dials — a lock here would write them into "
                "%s's file. Lock on %s's page, or load one of this library's clips first."
                % (FACE_DIALS["on"], POSE_DIR.name, FACE_DIALS["on"]))})
        if head == "/v1/mouth-tune":
            # THE LEAD IN ITS OWN UNIT, CONVERTED AT THE DOOR (2026-09-22).
            # `sheet_lead_semis` is an INPUT ALIAS, not a second dial: it is
            # turned into the one stored value here and never reaches
            # apply_dials. Adding it to DIAL_ENV instead would have worked —
            # that dict is the gate every dial must pass (line ~2398, `if k not
            # in DIAL_ENV: continue`, which is why the first attempt silently
            # did nothing) — but DIAL_ENV also drives what a lock PERSISTS, so
            # the dial would have been written to mouth-dials.json twice, in two
            # units, free to drift. One dial, one stored number, two ways in.
            if "sheet_lead_semis" in body and "sheet_lead_ms" not in body:
                try:
                    body["sheet_lead_ms"] = float(body.pop("sheet_lead_semis")) * semi_ms()
                except (TypeError, ValueError):
                    body.pop("sheet_lead_semis", None)
            global MOUTH_CORE, MOUTH_SCALE, VEL_LO, VEL_HI, VISEMES
            global W2L_MODE, W2L_FPS, VISEME_LEAD_MS, MOUTH_ANCHOR, MOUTH_WARP
            global MOUTH_FIT, MOUTH_HOLE, MOUTH_ALPHA, MOUTH_PIN, MOUTH_CHIN_PIN, MOUTH_NECK_CUT, MOUTH_NECK_MARGIN, MOUTH_FACE_HALF, MOUTH_IDLE_REST
            global MOUTH_NOSE_U, MOUTH_NOSE_V, MOUTH_NOSE_EMA, PLAY_RATE, MOUTH_TURN_U, MOUTH_TURN_V
            mouth: Mouth = STATE["mouth"]
            if COMPOSER_TRANSPORT and tuner_owns_dials(body, str(body.get("who") or self.headers.get("X-House-Who") or "")):
                return self._json(409, {"ok": False, "mode": "tuning",
                                        "error": "the AutoTuner owns the dials until it reports or is stopped"})
            # WHICH MOUTH, AND HOW OFTEN — live, no restart. {"w2l": "off"} is
            # the rollback to the viseme strip and it takes effect on the next
            # frame; the drop-in and HOUSE_W2L are only what she BOOTS with.
            # An unknown value is refused rather than silently read as off: a
            # typo must not quietly retire the neural mouth for the day.
            if "w2l" in body:
                want = str(body["w2l"] or "off").strip().lower()
                if want not in ("off", "gpu", "cpu"):
                    return self._json(409, {"ok": False,
                                            "error": "w2l must be off|gpu|cpu",
                                            "w2l": W2L_MODE})
                W2L_MODE = want
            if "anchor" in body:
                want = str(body["anchor"] or "haar").strip().lower()
                if want not in ("haar", "head"):
                    return self._json(409, {"ok": False,
                                            "error": "anchor must be haar|head",
                                            "anchor": MOUTH_ANCHOR})
                MOUTH_ANCHOR = want
            # Same refusal as anchor: a typo must not silently drop her back
            # to the flat paste while the User is looking for the curve.
            if "warp" in body:
                want = str(body["warp"] or "affine").strip().lower()
                if want not in ("auto", "affine", "triad", "cheeks", "cheeks-tri", "dental", "plane", "diamond"):
                    return self._json(409, {"ok": False,
                                            "error": "warp must be auto|affine|triad|cheeks|cheeks-tri|dental|plane|diamond",
                                            "warp": warp_dial()})
                globals()["MOUTH_TURN_TRI"] = want == "cheeks-tri"
                MOUTH_WARP = "cheeks" if want == "cheeks-tri" else want
            if "fit" in body:
                want = str(body["fit"] or "none").strip().lower()
                if want not in ("none", "width", "take", "take-uni"):
                    return self._json(409, {"ok": False,
                                            "error": "fit must be none|width|take|take-uni",
                                            "fit": MOUTH_FIT})
                MOUTH_FIT = want
            if "idle_rest" in body and idle_rest_pinned() is None:      # round 36: a set env wins over a tune too
                v = body["idle_rest"]
                MOUTH_IDLE_REST = (v if isinstance(v, bool) else str(v).strip().lower() not in ("0", "false", "off", "no"))
            if "neck_cut" in body:
                v = body["neck_cut"]
                MOUTH_NECK_CUT = (v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "on", "yes"))
            if isinstance(body.get("neck_margin"), (int, float)):
                MOUTH_NECK_MARGIN = max(-0.2, min(0.3, float(body["neck_margin"])))
            if isinstance(body.get("face_half"), (int, float)):
                MOUTH_FACE_HALF = max(0.2, min(1.0, float(body["face_half"])))
            if "chin_pin" in body:
                v = body["chin_pin"]
                MOUTH_CHIN_PIN = (v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "on", "yes"))
            if "pin" in body:
                want = str(body["pin"] or "nose").strip().lower()
                if want == "nose+chin-pixel":
                    want, MOUTH_CHIN_PIN = "nose-pixel", True
                elif want == "nose-pixel":
                    MOUTH_CHIN_PIN = False          # one control: nose-pixel alone is the pixel pin without the chin pin
                if want not in ("nose", "lip", "nose+lip", "nose-pixel"):
                    return self._json(409, {"ok": False,
                                            "error": "pin must be nose|lip|nose+lip|nose-pixel",
                                            "pin": MOUTH_PIN})
                MOUTH_PIN = want
            if "fit_under_pixel" in body:
                apply_dials({"fit_under_pixel": bool(body["fit_under_pixel"])}, honour_env=False)
            if "pin_hold" in body:
                apply_dials({"pin_hold": bool(body["pin_hold"])}, honour_env=False)
            if "buffer" in body:
                apply_dials({"buffer": bool(body["buffer"])}, honour_env=False)
            if "paint_all" in body:
                apply_dials({"paint_all": bool(body["paint_all"])}, honour_env=False)
            # sync_zero_ms IS APPLIED, NOT JUST LOGGED (the User 2026-09-20: "Did not
            # show calibrated after run/set as default. should so +20ms but does
            # not. Did it save?"). mouth-drill-optimize --calibrate-zero ends with
            # b.set({"sync_zero_ms": zero_new, "sheet_lead_ms": 0}); the dial half
            # landed and the zero half did not, because this list never named it.
            # It is in DIAL_ENV, so the handler LOGGED "dial: sync_zero_ms=20"
            # while dropping it — the journal said it saved and nothing had. The
            # lock then wrote the 0 still in memory, faithfully. apply_dials has
            # always known the key (the elif is right there); it was never asked.
            for k in ("xfade_ms", "edge_s", "sag_k", "sheet_lead_ms", "timing_lead_ms",
                      "min_hold", "cons_hold", "sync_zero_ms", "sent_pad", "word_pad",
                      # the elastic pause (2026-09-24) fell through here the same way:
                      # logged "dial: pause_words=1.7", applied nothing
                      "pause_words", "blip_words", "tail_words"):
                if isinstance(body.get(k), (int, float)):
                    apply_dials({k: body[k]}, honour_env=False)
            who = str(body.get("who") or self.headers.get("X-House-Who") or "composer")
            if body.get("lock"):
                # THE WINNER IS THE DEFAULT: write the live dials to the library.
                lock_dials(who)
            if body.get("default"):
                if FACE_DIALS.get("on"):
                    # a guest is on the glass: the home file is what comes back
                    # when she leaves; her own file is what is worn now
                    face_dials_refresh()
                    action("default", "home file re-read under %s; %s re-applied" % (FACE_DIALS["on"], FACE_DIALS["on"]), who)
                elif body.get("face") and str(body.get("face")).lower() in FACE_BANKS:
                    pass     # her file is read at her next clip entry; nothing to do now
                else:
                    # apply_preset, not apply_dials: the motion fade (velLo / velHi) is not a
                    # DIAL_ENV key, so a re-read left the wall on its boot motion (2026-09-27)
                    apply_preset({"dials": library_dials()})
                    action("default", dial_brief(library_dials()), who)
            if body.get("relock") is not None:
                # ROLLBACK: lock a past default again (an index into history)
                try:
                    past = dial_history(64)[int(body["relock"])]
                    apply_dials(past.get("was") or {}, honour_env=False)
                    lock_dials(who + " rollback→" + str(past.get("at")))
                except (IndexError, TypeError, ValueError):
                    return self._json(404, {"ok": False, "error": "no such history row"})
            if body.get("ref") is not None:
                ok_ref = journal_ref(str(body["ref"]), bool(body.get("on", True)))
                if not ok_ref:
                    return self._json(404, {"ok": False, "error": "no such journal row"})
            if body.get("apply_row") is not None:
                row = journal_row(str(body["apply_row"]))
                if not row:
                    return self._json(404, {"ok": False, "error": "no such journal row"})
                d_ = dict(row.get("dials") or {})
                apply_dials({k: v for k, v in d_.items() if k in DIAL_ENV}, honour_env=False)
                if isinstance(d_.get("velLo"), (int, float)): VEL_LO = float(d_["velLo"])
                if isinstance(d_.get("velHi"), (int, float)): VEL_HI = float(d_["velHi"])
                action("dial", "journal row %s applied" % row.get("at"), who)
                journal_add("apply", who, "from %s" % row.get("at"))
                if body.get("lock"):
                    lock_dials(who + " from journal " + str(row.get("at")))
            dial_keys = [k for k in body if k in DIAL_ENV or k in ("core", "velLo", "velHi", "lead_ms", "alpha", "w2l")]
            if dial_keys:
                action("dial", " ".join("%s=%s" % (k, body[k]) for k in dial_keys), who)
                journal_add("dial", who, " ".join("%s=%s" % (k, body[k]) for k in dial_keys))
            if "class" in body or "viseme" in body:
                action("hold", str(body.get("class", body.get("viseme"))), who)
            if "hole" in body:
                want = str(body["hole"] or "ellipse").strip().lower()
                if want not in ("ellipse", "arch"):
                    return self._json(409, {"ok": False,
                                            "error": "hole must be ellipse|arch",
                                            "hole": MOUTH_HOLE})
                MOUTH_HOLE = want
            if "alpha" in body:
                # `lips` (a colour-segmented lip mask) is retired since 09-04;
                # `jaw` (2026-09-16) is a geometric window: mouth + jaw drop
                want = str(body["alpha"] or "square").strip().lower()
                if want not in DIAL_CHOICES["alpha"]:
                    return self._json(409, {"ok": False,
                                            "error": "alpha must be square|jaw|auto (lips is retired)",
                                            "alpha": MOUTH_ALPHA})
                MOUTH_ALPHA = want
            # Filmed high-frequency added back onto the generated jaw. The
            # model's 96² output is measurably ~5x softer than the filmed
            # mouth it replaces (var-of-Laplacian 70 vs 374, clip-listen-back);
            # this buys some of it back for free. Judge it by eye, not by the
            # number: the frame underneath still holds her REAL mouth, so too
            # much of this pushes those edges through as a doubled jaw.
            if isinstance(body.get("w2l_detail"), (int, float)):
                w2l_mod = sys.modules.get("w2l")
                if w2l_mod is not None:
                    w2l_mod.DETAIL_K = max(0.0, min(1.0, float(body["w2l_detail"])))
            if isinstance(body.get("w2l_fps"), (int, float)):
                # 0 = generate every frame. Capped at the player: asking the
                # renderer for more frames than exist buys nothing and reads
                # like it did something.
                W2L_FPS = max(0.0, min(FPS, float(body["w2l_fps"])))
            if isinstance(body.get("core"), (int, float)):
                MOUTH_CORE = max(0.0, min(0.95, float(body["core"])))
            if isinstance(body.get("scale"), (int, float)):
                MOUTH_SCALE = max(0.5, min(2.0, float(body["scale"])))
            if isinstance(body.get("rate"), (int, float)):
                PLAY_RATE = max(0.1, min(1.0, float(body["rate"])))
                action("dial", "rate=%g" % PLAY_RATE, str(body.get("who") or self.headers.get("X-House-Who") or "composer"))
            if isinstance(body.get("velLo"), (int, float)):
                VEL_LO = float(body["velLo"])
            if isinstance(body.get("velHi"), (int, float)):
                VEL_HI = float(body["velHi"])
            # Mute the paint, keep the system. {"on": false}. Aliases enabled /
            # visemes. Absent key leaves the flag. {"level": 0} is NOT off —
            # it still inpaints the closed tile.
            for k in ("on", "enabled", "visemes"):
                if k not in body:
                    continue
                v = body[k]
                if isinstance(v, bool):
                    VISEMES = v
                elif isinstance(v, (int, float)):
                    VISEMES = bool(v)
                elif isinstance(v, str):
                    VISEMES = v.strip().lower() not in ("0", "false", "off", "no", "")
                break
            # Hold a viseme on Composer without the voice: {"level": 0.8}.
            # Release: {"level": null}. Absent key leaves the hold as-is.
            if "level" in body:
                if body["level"] is None:
                    mouth.force = None
                    mouth.force_class = None
                else:
                    mouth.force = max(0.0, min(1.0, float(body["level"])))
            if "class" in body or "viseme" in body:
                raw = body["class"] if "class" in body else body.get("viseme")
                if raw is None or raw == "":
                    # A RELEASE RELEASES EVERYTHING (the User 2026-09-12: "she
                    # does not perform words if I don't click a phoneme"):
                    # holding a class sets a fixed level of 0.8 too, and a
                    # release that cleared only the class left her at a
                    # forced level with no class — the sheet never walked.
                    mouth.force_class = None
                    mouth.force = None
                else:
                    klass = viseme_of(raw)
                    if klass is None:
                        return self._json(409, {"ok": False,
                                                "error": "class must be a viseme short name",
                                                "class": mouth.force_class})
                    mouth.force_class = klass
                    if mouth.force is None:
                        mouth.force = 0.8
            if "lead_ms" in body:
                try:
                    VISEME_LEAD_MS = viseme_lead_ms(float(body["lead_ms"]))
                except (TypeError, ValueError):
                    pass
            if isinstance(body.get("nose_u"), (int, float)):
                MOUTH_NOSE_U = max(-40.0, min(40.0, float(body["nose_u"])))   # px
            if isinstance(body.get("nose_v"), (int, float)):
                MOUTH_NOSE_V = max(-40.0, min(40.0, float(body["nose_v"])))   # px
            if isinstance(body.get("turn_u"), (int, float)):
                MOUTH_TURN_U = max(-40.0, min(40.0, float(body["turn_u"])))   # px, forward on a side turn
            if isinstance(body.get("turn_v"), (int, float)):
                MOUTH_TURN_V = max(-40.0, min(40.0, float(body["turn_v"])))   # px, down on a side turn
            if isinstance(body.get("nose_ema"), (int, float)):
                MOUTH_NOSE_EMA = max(0.0, min(1.0, float(body["nose_ema"])))
            if IS_BENCH and MOUTH_TUNE_READINGS:
                tune_reading(body)
            return self._json(200, {"ok": True, "core": MOUTH_CORE,
                                    "default": library_dials(), "dials_file": str(DIALS_FILE),
                                    "fit_under_pixel": MOUTH_FIT_UNDER_PIXEL, "pin_hold": MOUTH_PIN_HOLD,
                                    "xfade_ms": MOUTH_XFADE_MS, "edge_s": MOUTH_EDGE_S, "sag_k": MOUTH_SAG_K,
                                    "sheet_lead_ms": MOUTH_SHEET_LEAD_MS, "sheet_lead_semis": round(lead_semis(), 4), "semi_ms": semi_ms(), "timing_lead_ms": MOUTH_TIMING_LEAD_MS,
                                    "sync_zero_ms": SYNC_ZERO_MS, "sheet_lead_effective_ms": effective_sheet_lead_ms(),
                                    "min_hold": MOUTH_MIN_HOLD, "cons_hold": MOUTH_CONS_HOLD, "tempo": MOUTH_TEMPO,
                                    "sent_pad": MOUTH_SENT_PAD, "word_pad": MOUTH_WORD_PAD,
            "pause_words": MOUTH_PAUSE_WORDS, "blip_words": MOUTH_BLIP_WORDS, "tail_words": MOUTH_TAIL_WORDS,
                                    "rate": PLAY_RATE, "buffer": MOUTH_BUFFER, "paint_all": MOUTH_PAINT_ALL,
                                    "pin_match": getattr(STATE.get("playhead"), "_pin_dbg", None),
                                    "history": dial_history(),
                                    "journal": journal_rows(int(body.get("journal") or 40)) if body.get("journal") else None,
                                    "scale": MOUTH_SCALE,
                                    "anchor": MOUTH_ANCHOR,
                                    "warp": warp_dial(),
                                    "fit": MOUTH_FIT, "pin": pin_dial(), "neck_cut": MOUTH_NECK_CUT, "idle_rest": MOUTH_IDLE_REST, "hole": MOUTH_HOLE,
                                    "alpha": MOUTH_ALPHA, "chin_pin": MOUTH_CHIN_PIN,
                                    "velLo": VEL_LO, "velHi": VEL_HI,
                                    "on": VISEMES, "visemes": VISEMES,
                                    "lead_ms": VISEME_LEAD_MS,
                                    "nose_u": MOUTH_NOSE_U,
                                    "nose_v": MOUTH_NOSE_V,
                                    "turn_u": MOUTH_TURN_U,
                                    "turn_v": MOUTH_TURN_V,
                                    "nose_ema": MOUTH_NOSE_EMA,
                                    "w2l": W2L_MODE, "w2l_fps": W2L_FPS,
                                    "w2l_detail": getattr(
                                        sys.modules.get("w2l"), "DETAIL_K", None),
                                    "level": mouth.force,
                                    "class": mouth.force_class})
        if head == "/v1/transport":
            # ONE INTENT ROUTE (the User 2026-09-25): play | pause | seek | step | stop_all | grab.
            # The bench resolves the verb against ITS state (lib/bench_transport.plan) and
            # answers the transport block, so the page never guesses. Bench only.
            if not (IS_BENCH and COMPOSER_TRANSPORT):
                return self._json(404, {"ok": False, "error": "the transport is the bench's (HOUSE_COMPOSER_TRANSPORT)"})
            code, out = transport_verb(ph, body, str(body.get("who") or self.headers.get("X-House-Who") or "composer"))
            return self._json(code, out)
        if head == "/v1/tour":
            # THE TOUR (the User 2026-09-13): {"on": true|false, "dwell_s": 12}
            seq: Sequencer = STATE["seq"]
            if "on" not in body:
                return self._json(200, {"ok": True, "tour": seq.tour_status()})
            if bool(body.get("on")):
                st = seq.tour_start(keys=_tour_keys_on_disk(seq), dwell_s=body.get("dwell_s"))
            else:
                st = seq.tour_stop()
            action("tour", "on" if st["on"] else "off", str(body.get("by") or self.headers.get("X-House-Who") or "composer"))
            return self._json(200, {"ok": True, "tour": st})
        if head == "/v1/brain":
            seq: Sequencer = STATE["seq"]
            want = bool(body.get("on"))
            if want and STATE.get("brain") is None:
                STATE["brain"] = _make_brain()
            seq.brain = STATE["brain"].pick if (want and STATE.get("brain")) else None
            seq.brain_obj = STATE["brain"] if want else None
            return self._json(200, {"ok": True, "brain": bool(seq.brain)})
        if head == "/v1/drill":
            ph: Playhead = STATE["playhead"]
            if body.get("stop"):
                return self._json(200, {"ok": True, "drill": dict(DRILL, proc=None, log=len(DRILL["log"])), "report": drill_stop("stopped")})
            if COMPOSER_TRANSPORT and tuner_alive() and not body.get("force") and str(body.get("who") or "") != TUNER_WHO:
                return self._json(409, {"ok": False, "mode": "tuning", "confirm": True,
                                        "error": "the AutoTuner is mid-run — stop the whole run first"})
            key = str(body.get("key") or "")
            row = ph.lib.rows.get(key) if key else None
            if not row or not row.get("file"):
                return self._json(404, {"ok": False, "error": "no such clip with a film"})
            # `text` is an OVERRIDE now, not a requirement (the User 2026-09-16: "it
            # still reads the textbox"): the film's words come from the film.
            text = str(body.get("text") or "").strip()
            if not text and not DRILL_FILM_WORDS:
                return self._json(400, {"ok": False, "error": "text required — the words she says on this film (film words are off)"})
            path = film_path(row, key)
            if path is None:
                return self._json(404, {"ok": False, "error": "film not found for %s" % key})
            voiced = film_has_voice(path)
            if not voiced and not text:
                # A SILENT LOOP: SHE SAYS THE STAPLE OVER IT (restored 2026-09-18). The first
                # drill (7c13936c, 09-13 05:18) spoke the staple with the voice over ANY clip —
                # "painting random words on random movement" — and the optimizer's passes did
                # the same. 09-13 17:24 made a silent film a 409 (the --wav drill had waited
                # 40 s for a voice the file lacks) and 09-14 09:08 refused the AutoTuner on it.
                # the User 2026-09-18: "I am not able to start AutoTune on a non speaking clip -
                # just like Match Game now. Those were different." No --wav: the voice says the
                # line (the box, else the staple); the loop plays on under it.
                text = DRILL_STAPLE
            who = str(body.get("voice") or os.environ.get("HOUSE_SAY_VOICE") or _avatar_current_name() or "crissa")
            code, out = drill_begin(ph, key, row, path, who=who, loop=bool(body.get("loop")), override=text, graded=True,
                                    by=str(body.get("who") or self.headers.get("X-House-Who") or "composer"), voiced=voiced,
                                    pacer=bool(body.get("pacer")) and DRILL_PACER, mute=bool(body.get("mute")))
            return self._json(code, out)
        if head == "/v1/scrub":
            # FRAME BY FRAME WITH THE TILES (the User 2026-09-24). Holds the playhead on a
            # frame of the film on screen; null releases it. A running pass is stopped:
            # "pause Say and go frame by frame".
            f = body.get("frame")
            if f is None:
                SCRUB.update({"key": None, "frame": None})
                # A RELEASE IS A PLAY (the User 2026-09-25: "player wont play on 1/4 speed for audit
                # of tuned timing" after a match game): a finished pass parks the film on its last
                # frame (the 09-13 law) and only play() released the park — the transport's ▶ after
                # a pass sends scrubs, never a play, so the release left the film parked and ▶ read
                # as dead. Releasing the scrub hold releases the park too.
                DRILL["hold_frame"] = False
                return self._json(200, {"ok": True, "scrub": dict(SCRUB)})
            if DRILL.get("on"):
                drill_stop("scrubbed")
            try:
                SCRUB.update({"key": STATE["playhead"].key, "frame": int(f)})
            except (TypeError, ValueError):
                return self._json(400, {"ok": False, "error": "frame must be an integer or null"})
            return self._json(200, {"ok": True, "scrub": dict(SCRUB),
                                    "class": plan_class_at(STATE["playhead"].key, int(f))})
        if head == "/v1/autotune":
            # THE AUTOTUNER FROM THE TUNE BOX (the User 2026-09-13 06:5x): start / stop
            # scripts/mouth-drill-optimize.py against this conductor; its table is
            # the measurements log the box shows.
            p = AUTOTUNE.get("proc")
            if body.get("stop"):
                if COMPOSER_TRANSPORT:
                    autotune_stop(str(body.get("who") or "composer"))
                    return self._json(200, {"ok": True, "autotune": autotune_state()})
                if p is not None and p.poll() is None:
                    try:
                        os.killpg(p.pid, signal.SIGTERM)
                    except Exception:  # noqa: BLE001
                        pass
                drill_stop("autotune stopped")
                if AUTOTUNE.get("rate_was") is not None:
                    PLAY_RATE = float(AUTOTUNE["rate_was"])
                    AUTOTUNE["rate_was"] = None
                    action("autotune", "rate back to %g" % PLAY_RATE, str(body.get("who") or "composer"))
                action("autotune", "stopped", str(body.get("who") or "composer"))
                return self._json(200, {"ok": True, "autotune": autotune_state()})
            if p is not None and p.poll() is None:
                return self._json(409, {"ok": False, "error": "the autotuner is already running", "autotune": autotune_state()})
            key = str(body.get("key") or DRILL.get("key") or "")
            if not key:
                return self._json(400, {"ok": False, "error": "load a film first"})
            # A SILENT LOOP TUNES TOO (restored 2026-09-18; the 09-14 refusal is gone): its
            # passes are drills, and a drill on a silent film has the voice say the line over the
            # moving loop. The score's answer key is then the loop's own (idle) mouth — r
            # means little there; the consonant / closure / rhythm goals still read.
            row_ = ph.lib.rows.get(key) or {}
            path_ = film_path(row_, key)
            silent_ = path_ is None or not film_has_voice(path_)
            text = str(body.get("text") or "").strip()
            mode = "calibrate" if body.get("calibrate") else ("quick" if body.get("quick") else "full")
            log = ROOT / "logs" / ("autotune-%d.log" % PORT)
            log.parent.mkdir(exist_ok=True)
            run_id = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), key)
            film = bool(TUNER_FILM_SOLVE and TUNER_FILM.is_file())
            cmd = [sys.executable, str(tuner_script()),
                   "--conductor", "http://127.0.0.1:%d" % PORT, "--key", key]
            if film:
                cmd += ["--run-id", run_id]      # today's optimizer does not know the flag
            base = {k: live_dials().get(k) for k in TUNER_DIALS} if COMPOSER_TRANSPORT else None
            if text:
                cmd += ["--text", text[:400]]
            if mode == "quick":
                cmd.append("--quick")
            elif mode == "calibrate":
                cmd += ["--quick", "--calibrate-zero"]
            if body.get("target_lag") is not None:
                cmd += ["--target-lag", str(float(body["target_lag"]))]
            # MEASURE AT 1x, ALWAYS (the User 2026-09-20: "slowing helps me not them ...
            # just have them force it when running"). Slow playback is an
            # inspection glass for his eye and a lie to the tuner: measured on
            # crissa-close-harvest with one fixed dial, every pass r >= 0.35 —
            #
            #   rate 1.00  lag  -35, 0 ms    = -1, 0 film frames
            #   rate 0.50  lag +208,+139 ms  = +3, +2 film frames
            #   rate 0.25  lag +278,+417 ms  = +2, +3 film frames
            #
            # a FIXED ~2.5-frame phantom lag the moment rate leaves 1, not
            # proportional to how slow (quarter is no worse than half), so there
            # is no honest conversion — only a constant somebody would have to
            # keep true. The Up run of 05:08 spent itself cancelling that
            # phantom: lead +60 -> -130, a -190 ms shift against a +174 ms
            # average phantom lag. So the run forces 1x and puts his rate back.
            rate_was = None
            if float(PLAY_RATE) != 1.0:
                rate_was = float(PLAY_RATE)
                PLAY_RATE = 1.0
                action("autotune", "forced rate 1x for the run (was %g) — a measurement at %g is "
                       "~2.5 film frames late" % (rate_was, rate_was),
                       str(body.get("who") or "composer"))
            AUTOTUNE.update({"proc": subprocess.Popen(cmd, stdout=log.open("w"), stderr=subprocess.STDOUT,
                                                      stdin=subprocess.DEVNULL, start_new_session=True),
                             "log": str(log), "started": time.strftime("%H:%M:%S"), "key": key, "mode": mode,
                             "run_id": run_id, "rate_was": rate_was, "hand_back": True,
                             "film": film, "base": base})
            if silent_:
                action("autotune", "%s is silent — the voice says the %s over it" % (key, "box" if text else "staple"), str(body.get("who") or "composer"))
            action("autotune", "%s on %s" % (mode, key), str(body.get("who") or self.headers.get("X-House-Who") or "composer"))
            return self._json(200, {"ok": True, "autotune": autotune_state()})
        if head == "/v1/mouth":
            if MAILBOX is None:
                return self._json(404, {"ok": False, "error": "post the sheet to the wall (:8099)"})
            return self._json(200, MAILBOX.ingest(body if isinstance(body, dict) else {}))
        if head == "/v1/combos":
            keys = None if body.get("delete") else (body.get("keys") or [])
            if keys is not None and not isinstance(keys, list):
                return self._json(400, {"ok": False, "error": "keys must be a list"})
            code, out = save_combo(body.get("name") or "", keys)
            if code == 200:
                action("combo", "%s %s" % ("dropped" if keys is None else "saved",
                                           str(body.get("name"))[:40]),
                       str(body.get("who") or "composer"))
            return self._json(code, out)
        if head == "/v1/glass-lag":
            out = glass_lag_report(body)
            return self._json(200 if out.get("ok") else 400, out)
        if head == "/v1/mouth-pins":
            # GRID-CELL PINS BY ANGLE (the User 2026-09-29). {u, v} saves a row for the cell on screen at
            # this frame's yaw; {cell, yaw} name them; {drop: true} removes the row at that yaw (or the
            # whole cell with {drop: true, all: true}). Bench only; the wall reads the file.
            if not IS_BENCH:
                return self._json(409, {"ok": False, "error": "pins are saved on a bench"})
            code, out = mouth_pins_post(STATE.get("playhead"), body,
                                        str(body.get("by") or self.headers.get("X-House-Who") or "composer"))
            return self._json(code, out)
        if head == "/v1/mouth-presets":
            # BESTS (the User 2026-09-14): save the dials as shown under a name, apply one,
            # lock one as the library default, delete one. The bracket lives in the
            # page: it applies two bests in turn, grabs two stills, you click.
            who = str(body.get("who") or self.headers.get("X-House-Who") or "composer")
            rows = load_presets()
            if body.get("unlock_clip"):
                ck = str(body["unlock_clip"])
                clips = load_clip_dials()
                if ck not in clips:
                    return self._json(404, {"ok": False, "error": "no clip set for %r" % ck})
                clips.pop(ck, None)
                save_clip_dials(clips)
                if CLIP_DIALS.get("on") == ck:
                    clip_dials_enter(ck)     # no set any more → restores the snapshot
                action("clip-dials", "released " + ck, who)
                return self._json(200, {"ok": True, "presets": rows, "clips": clips, "clip_on": CLIP_DIALS.get("on"),
                                        "live": preset_dials(), "default": library_dials()})
            if body.get("clip") and (isinstance(body.get("set"), dict) or body.get("unset")):
                # A CLIP'S OWN KEYS, ONE AT A TIME (the User 2026-09-18: "We need idle mouth to be
                # stored clip by clip. That must stay flexible"): {clip, set: {idle_rest: false}}
                # merges those keys into the clip's set — a PARTIAL set; everything it does not
                # name follows the library lock, so a later lock still reaches this clip.
                # {clip, unset: ["idle_rest"]} takes a key back; an emptied set is dropped.
                ck = str(body["clip"])
                clips = load_clip_dials()
                cur = dict((clips.get(ck) or {}).get("dials") or {})
                for k, v in (body.get("set") or {}).items():
                    if k in DIAL_ENV or k in ("velLo", "velHi"):
                        cur[k] = v
                for k in (body.get("unset") or []):
                    cur.pop(str(k), None)
                if cur:
                    clips[ck] = {"dials": cur, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "by": who,
                                 "best": (clips.get(ck) or {}).get("best") or "own"}
                else:
                    clips.pop(ck, None)
                save_clip_dials(clips)
                action("clip-dials", "%s %s %s" % (ck, "←" if cur else "released", dial_brief(cur) if cur else ""), who)
                if ph.key == ck:
                    if CLIP_DIALS.get("on") == ck:
                        # wearing it: back to the snapshot underneath, then the set as it is now
                        # (a key taken back returns to the shared value; an emptied set is off)
                        prev = CLIP_DIALS.get("prev") or {}
                        if prev:
                            apply_preset({"dials": prev})
                        if cur:
                            apply_preset(clips[ck])
                        else:
                            CLIP_DIALS["on"] = None
                            CLIP_DIALS["prev"] = None
                    else:
                        clip_dials_enter(ck)
                return self._json(200, {"ok": True, "presets": rows, "clips": clips, "clip_on": CLIP_DIALS.get("on"),
                                        "live": preset_dials(), "default": library_dials()})
            name = str(body.get("save") or body.get("apply") or body.get("lock") or body.get("delete") or "").strip()[:48]
            if not name:
                return self._json(400, {"ok": False, "error": "name a best (save / apply / lock / delete)"})
            if body.get("save"):
                row = {"name": name, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "by": who, "dials": preset_dials()}
                # BESTS FOLLOW THE CLIP (the User 2026-09-18 "tie that to the clip's best like the grid
                # is"): the page sends the loaded clip; the Composer lists this clip's bests and the
                # library-wide ones (no clip) and only counts the rest. A row without `clip` is shared.
                if body.get("clip"):
                    row["clip"] = str(body["clip"])
                rows = [r for r in rows if r.get("name") != name] + [row]
                save_presets(rows)
                action("bests", "saved %s: %s" % (name, dial_brief(row["dials"])), who)
                journal_add("bests", who, "saved " + name)
            elif body.get("delete"):
                keep = [r for r in rows if r.get("name") != name]
                if len(keep) == len(rows):
                    return self._json(404, {"ok": False, "error": "no best named %r" % name, "presets": rows})
                rows = keep
                save_presets(rows)
                action("bests", "deleted " + name, who)
            else:
                row = next((r for r in rows if r.get("name") == name), None)
                if row is None:
                    return self._json(404, {"ok": False, "error": "no best named %r" % name, "presets": rows})
                if body.get("lock") and body.get("clip"):
                    # THIS CLIP ONLY: the set rides the clip, the library default stands
                    ck = str(body["clip"])
                    clips = load_clip_dials()
                    clips[ck] = {"dials": dict(row.get("dials") or {}), "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "by": who, "best": name}
                    save_clip_dials(clips)
                    action("clip-dials", "%s ← best %s (%s)" % (ck, name, dial_brief(row.get("dials") or {})), who)
                    journal_add("bests", who, "clip %s ← %s" % (ck, name))
                    if ph.key == ck:
                        CLIP_DIALS["on"] = None
                        clip_dials_enter(ck)
                    return self._json(200, {"ok": True, "presets": rows, "clips": clips, "clip_on": CLIP_DIALS.get("on"),
                                            "live": preset_dials(), "default": library_dials()})
                apply_preset(row)
                action("bests", "applied %s: %s" % (name, dial_brief(row.get("dials") or {})), who)
                journal_add("bests", who, "applied " + name)
                if body.get("lock"):
                    lock_dials(who + " bests " + name)
            return self._json(200, {"ok": True, "presets": rows, "clips": load_clip_dials(), "clip_on": CLIP_DIALS.get("on"),
                                    "live": preset_dials(), "default": library_dials()})
        if head == "/v1/repin":
            # REPIN FRAMES (the User 2026-09-14): the on-air film's head track, re-fit.
            p = REPIN.get("proc")
            if body.get("stop"):
                # R4 (2026-09-15): a repin is a detached scope that outlives a
                # restart of this conductor. It needs a stop.
                if p is None or p.poll() is not None:
                    return self._json(409, {"ok": False, "error": "no repin is running", "repin": repin_state()})
                REPIN["stopped"] = True
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    try:
                        p.terminate()
                    except OSError:
                        pass
                action("repin", "stopped %s" % REPIN.get("stem"),
                       str(body.get("who") or self.headers.get("X-House-Who") or "composer"))
                return self._json(200, {"ok": True, "repin": repin_state()})
            if p is not None and p.poll() is None:
                return self._json(409, {"ok": False, "error": "a repin is already running (%s)" % REPIN.get("stem"), "repin": repin_state()})
            key = str(body.get("key") or ph.key or "")
            row = ph.lib.rows.get(key) or {}
            if not key or not row.get("file"):
                return self._json(400, {"ok": False, "error": "load a film first (no row for %r)" % key})
            bank = bank_dir(key)
            stem = Path(str(row.get("file"))).stem
            # R5 (2026-09-15): A GUEST KEY REFITS THE WRONG FILE. bank_dir()
            # sends a lila-* row to Lila's library, but Library.head reads
            # POSE_DIR/<stem>.head.json — so on :8100 this would re-fit a
            # track this conductor never reads, and report success. Her own
            # page (:8198, bench :8101) is where her clips get re-pinned.
            if bank != POSE_DIR:
                return self._json(409, {
                    "ok": False,
                    "error": "%s is a guest row from %s — repin it on that face's own page (Lila: :8198)"
                             % (key, bank.name)})
            # R4: another fitter on the same stem, started by a hand or by a
            # conductor that has since restarted, would race this one's write.
            if shutil.which("pgrep"):
                try:
                    hit = subprocess.run(["pgrep", "-f", "pose-head-frame.py --clips %s" % stem],
                                         capture_output=True, text=True, timeout=5)
                    if (hit.stdout or "").strip():
                        return self._json(409, {
                            "ok": False,
                            "error": "a fitter is already running on %s (pid %s) — not started by this box"
                                     % (stem, (hit.stdout or "").split()[0])})
                except (OSError, subprocess.SubprocessError):
                    pass
            if not (bank / (stem + ".mp4")).is_file():
                return self._json(404, {"ok": False, "error": "no film on disk for %s" % stem})
            before = None
            track = bank / (stem + ".head.json")
            ck = None
            if track.is_file():
                try:
                    before = json.loads(track.read_text()).get("frames") or []
                except (OSError, ValueError):
                    before = None
                from pose_intake import museum_dir
                ckdir = museum_dir(POSE_DIR) / ("repin-" + time.strftime("%Y%m%d"))
                ckdir.mkdir(parents=True, exist_ok=True)
                ck = ckdir / (stem + ".head.json." + time.strftime("%H%M%S"))
                try:
                    shutil.copy2(track, ck)
                except OSError as ex:
                    return self._json(500, {"ok": False, "error": "could not checkpoint the old track: %s" % ex})
            log = ROOT / "logs" / ("repin-%d.log" % PORT)
            log.parent.mkdir(exist_ok=True)
            # the face script runs in the environment its face engine is installed in (pose_intake.face_python)
            # HOUSE_FACE_PY, else under mediapipe this machine's mediapipe env (config/house-machine.json
            # `face_python`, else <state home>/envs/mediapipe) when it exists, else this interpreter.
            face_py = os.environ.get("HOUSE_FACE_PY", "").strip()
            if not face_py and _hf_mod.face_engine() == "mediapipe":
                mp_py = (crissa_home.house_value("face_python")
                         or str(crissa_home.HOME / "envs" / "mediapipe" / "bin" / "python"))
                face_py = mp_py if os.path.exists(mp_py) else ""
            face_py = face_py or sys.executable
            cmd = [face_py, str(ROOT / "scripts" / "pose-head-frame.py"), "--clips", stem]
            if shutil.which("systemd-run"):
                # a quota scope: the fitter takes every core it can and the wall
                # keeps painting beside it (memory: heavy jobs in a quota scope).
                # 600 % since 2026-09-17 (the User: "could we give that task more
                # cores?"): at 300 % its ~350 threads were throttled in 93 % of
                # periods, 0.6 frames/s, 27 min for 1205 frames; raised live to
                # 800 % it ran 1.9 frames/s and the wall dipped from ~21/s to
                # 13–19/s. HOUSE_REPIN_CPU_QUOTA sets it (300% = the old number).
                cmd = ["systemd-run", "--user", "--scope", "--quiet", "-p", "CPUQuota=" + REPIN_CPU_QUOTA] + cmd
            env = dict(os.environ)
            env["HOUSE_POSE_DIR"] = str(bank)
            env["PYTHONUNBUFFERED"] = "1"   # PROGRESS lines reach the log as they happen
            try:
                proc = subprocess.Popen(cmd, env=env, stdout=log.open("w"), stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
            except OSError as ex:
                return self._json(500, {"ok": False, "error": "could not start the fitter: %s" % ex})
            n_before = len(before or [])
            REPIN.update({"proc": proc, "stopped": False, "log": str(log), "started": time.strftime("%H:%M:%S"),
                          "started_at": time.time(), "n": n_before, "key": key,
                          "stem": stem, "note": "fitting %s (%d frames)" % (stem, n_before),
                          "checkpoint": (str(ck.relative_to(ROOT)) if ck else None), "before": before})
            threading.Thread(target=repin_watch, args=(ph, bank, stem), daemon=True, name="repin-watch").start()
            action("repin", "%s (%s)" % (stem, "checkpoint " + str(ck.name) if ck else "no earlier track"),
                   str(body.get("who") or self.headers.get("X-House-Who") or "composer"))
            return self._json(200, {"ok": True, "repin": repin_state()})
        if head == "/v1/say":
            if body.get("stop"):
                # LOOP OFF IS A HARD STOP (the User 2026-09-13: "When I stop it it plays
                # twenty more times"): every spawned speaker's group (its paplay
                # with it), the baked clip line, and a running drill.
                n = say_stop_all()
                if DRILL.get("on"):
                    drill_stop("loop off")
                action("say", "stop (%d speaker%s)" % (n, "" if n == 1 else "s"), str(body.get("who") or self.headers.get("X-House-Who") or "composer"))
                return self._json(200, {"ok": True, "stopped": n, "say_pending": say_pending()})
            src = str(body.get("src") or body.get("audio") or "text").strip().lower()
            if src in ("clip", "film", "track"):
                # CLIP AUDIO (the User 2026-09-16): the harvest soundtrack, tiles
                # from phon.json — what the rendered clip was saying. Not the voice.
                ph: Playhead = STATE["playhead"]
                key = str(body.get("key") or ph.key or "")
                row = ph.lib.rows.get(key) if key else None
                if not row or not row.get("file"):
                    return self._json(404, {"ok": False, "error": "load a film first"})
                path = film_path(row, key)
                if path is None:
                    return self._json(404, {"ok": False, "error": "film not found for %s" % key})
                if not film_has_voice(path):
                    return self._json(409, {"ok": False, "no_voice": True,
                                            "error": "this film has no soundtrack — switch to text audio"})
                who = str(body.get("voice") or os.environ.get("HOUSE_SAY_VOICE") or _avatar_current_name() or "crissa")
                # the same sequence as the graded run, ungraded: the film's own
                # words (it used to send text "" and house-speak exited rc 2)
                code, out = drill_begin(ph, key, row, path, who=who, loop=bool(body.get("loop")),
                                        override=str(body.get("text") or ""), graded=False,
                                        by=str(body.get("who") or "composer"))
                out["src"] = "clip"
                action("say", "clip audio %s (%s)" % (key, "reading" if out.get("reading") else out.get("line_src")), str(body.get("who") or "composer"))
                return self._json(code, out)
            text = str(body.get("text") or "").strip()
            if not text:
                return self._json(400, {"ok": False, "error": "text required"})
            if COMPOSER_TRANSPORT and tuner_alive() and not body.get("force"):
                return self._json(409, {"ok": False, "mode": "tuning", "confirm": True,
                                        "error": "the AutoTuner is mid-run — stop the whole run first"})
            if len(text) > 400:
                text = text[:400]
            if HOUSE_URL:
                try:
                    with urllib.request.urlopen(HOUSE_URL, timeout=1.0) as r:
                        house = json.load(r)
                    voice = house.get("voice") if isinstance(house.get("voice"), dict) else house
                    if isinstance(voice, dict) and voice.get("voice_on") is False:
                        return self._json(409, {"ok": False,
                                                "error": "voice is off on the panel"})
                except Exception:
                    pass
            # THE VOICE MATCHES THE AGENT (the User 2026-09-12: "can we match
            # voice to agent here?"): the avatar this conductor runs
            # (HOUSE_AVATAR) names the voice unless the call says otherwise.
            who = str(body.get("voice") or os.environ.get("HOUSE_SAY_VOICE") or _avatar_current_name() or "crissa")
            if DRILL.get("on"):
                # ONE VOICE (the User 2026-09-17: "they both play"): a looping
                # follow-clip has no speaker in flight between passes, so
                # the busy guard below let the box line start and the drill
                # re-spawned the film's voice on top of it. The last ask wins.
                drill_stop("text say")
                DRILL["hold_frame"] = False   # the film keeps running under her line
                say_stop_all()
            pending = say_pending()
            if pending:
                # ONE SPEAKER (the User 2026-09-16: stacked staple every ~30 s).
                # A second spawn queues on the speech lock and the mailbox
                # new-turn PCM reset kills the sheet the first line was
                # painting. Loop-Say waits; the click-toggle is stop.
                action("say", "busy (%d pending) — not stacking" % pending,
                       str(body.get("who") or self.headers.get("X-House-Who") or "composer"))
                return self._json(200, {"ok": True, "busy": True, "say_pending": pending,
                                        "text": text, "voice": who})
            _spawn_speak(text, who)
            action("say", "%r as %s" % (text[:80], who), str(body.get("who") or self.headers.get("X-House-Who") or "composer"))
            return self._json(200, {"ok": True, "text": text, "voice": who, "say_pending": say_pending()})
        return self._json(404, {"ok": False, "error": "no such path"})


def _tour_keys_on_disk(seq) -> list[str]:
    """The tour's loops that have a film on disk (a row with no file is a gap
    in the library, not a stop on the tour)."""
    keys = []
    for k in seq.tour_keys():
        f = str((seq.graph.row(k) or {}).get("file") or "")
        if (POSE_DIR / f).is_file() or any((bank / f).is_file() for bank in FACE_BANKS.values()):
            keys.append(k)
    return keys


def _init_running() -> bool:
    try:
        import init_wizard
        return str(init_wizard.load_state().get("status") or "") == "running"
    except Exception:  # noqa: BLE001
        return False


def _tour_hold() -> str:
    """Render on Crissa's wall is Lila's, and Init is hers too: the tour waits."""
    try:
        import init_wizard
        if str(init_wizard.load_state().get("status") or "") == "running":
            return "Init running"
    except Exception:
        pass
    try:
        # GPU_MODE_FILE, not a second spelling of the path: it honours
        # HOUSE_GPU_MODE (unset on every unit = the same file), which is what
        # lets the out-of-tree proof keep this read off the live house.
        mode = GPU_MODE_FILE.read_text().split()[0].strip().lower()
    except (OSError, IndexError):
        mode = ""
    seq = STATE.get("seq")
    here = str(getattr(seq, "here", "") or "")
    if mode == "render" and PORT == 8100:
        return "Lila hosts Render" if here.startswith("lila_") else "Render (Lila's glass)"
    return ""


_MAPS_AHEAD: dict = {"stage": None}     # lib/maps_ahead.Ahead once maps_disk_setup starts it


def maps_disk_setup(ph) -> None:
    """The cheeks maps' disk tier (lib/maps_disk.py, the User 2026-10-12): a restart starts warm. The fingerprint
    names the code that builds a map (its modules + these methods), the salt the live mouth dials and the film's and
    tile's own files, so no entry outlives what made it. Off on the house unless HOUSE_MOUTH_MAPS_DISK=1."""
    try:
        import inspect
        import maps_disk
        import poly_cache
        if not maps_disk.enabled():
            return
        srcs = tuple(inspect.getsource(getattr(Playhead, m)) for m in (
            "_cheeks_maps", "_cheeks_pin", "_pin_shift", "_nudge_px", "_placement_maps", "_loop_ver"))
        tier = maps_disk.MapsDisk(maps_disk.code_fingerprint(srcs))
        g = globals()

        def _stat(path) -> tuple:
            try:
                st = os.stat(path)
                return (int(st.st_mtime_ns), int(st.st_size))
            except OSError:
                return ()

        def salt(loop_id: str, rel: str) -> bytes:
            dials = tuple(sorted((k, repr(v)) for k, v in g.items()
                                 if k.startswith("MOUTH_") and isinstance(v, (bool, int, float, str))))
            row = ph.lib.rows.get(loop_id) or {}
            film = film_path(row, loop_id) or (POSE_DIR / str(row.get("file") or loop_id))
            return repr((dials, str(film), _stat(film), rel, _stat(POSE_DIR / rel) if rel else ())).encode()

        poly_cache.set_maps_disk(tier, salt)
        print("[conductor] maps disk: on at %s (cap %.1f GB)" % (tier.root, tier.cap / maps_disk.GB), flush=True)
        if maps_ahead.enabled():
            _MAPS_AHEAD["stage"] = maps_ahead.Ahead(tier.root / "ahead-journal.pkl")
            poly_cache.set_ahead(_MAPS_AHEAD["stage"])
            print("[conductor] maps ahead: on, %d frames, %d keys known" % (
                maps_ahead.frames_ahead(), len(_MAPS_AHEAD["stage"].slots)), flush=True)
    except Exception as e:  # noqa: BLE001 - memory only, as before
        print("[conductor] maps disk: off (%s)" % e, flush=True)


_GC_FREEZE: dict = {"on": False, "due": 0.0, "why": ""}


def gc_quiet_on() -> bool:
    """HOUSE_CONDUCTOR_GC_QUIET=1|0 wins; unset: on in a pack, off on the house (round 15, 2026-10-12: a pack's player
    on .101 paints on a busy CPU, where a gen2 pass that collects nothing costs her mouth rows)."""
    raw = os.environ.get("HOUSE_CONDUCTOR_GC_QUIET", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return not IS_HOUSE


def gc_refreeze_soon(why: str, delay_s: float = 2.0) -> None:
    """Ask for a freeze once what just loaded has settled (a clip decoded, the library reloaded). No-op unless quiet."""
    if _GC_FREEZE.get("on"):
        _GC_FREEZE.update(due=time.time() + delay_s, why=why)


def gc_quiet_start() -> None:
    """Garbage collection off the paint's clock (the User 2026-10-12). The stall watch caught gc passes of 21-34 ms that
    collected nothing on a fresh bench; the conductor holds the clips' head tracks, playlists and caches for its whole
    life, and every full pass walks all of them while the paint waits. On: after startup gc.freeze() moves what exists
    into the permanent generation (never scanned again), and the young thresholds rise (HOUSE_CONDUCTOR_GC_THRESH,
    default 50000,50,100), so passes are rare and small. Objects are still freed by reference counting as always; only
    the cycle collector runs less. A second freeze runs after HOUSE_CONDUCTOR_GC_REFREEZE_S (120 s), when the first
    clips and caches have loaded, and again once a clip or a library reload settles. HOUSE_CONDUCTOR_GC_QUIET=1|0;
    unset: on in a pack, off on the house (gc_quiet_on)."""
    if not gc_quiet_on():
        return
    import gc
    try:
        th = [int(x) for x in os.environ.get("HOUSE_CONDUCTOR_GC_THRESH", "50000,50,100").split(",")][:3]
    except ValueError:
        th = [50000, 50, 100]
    gc.collect()
    gc.freeze()
    gc.set_threshold(*th)
    print("[conductor] gc quiet: froze %d objects, thresholds %s" % (gc.get_freeze_count(), th), flush=True)
    _GC_FREEZE.update(on=True, due=time.time() + float(os.environ.get("HOUSE_CONDUCTOR_GC_REFREEZE_S", "120")),
                      why="the startup timer")

    def _refreeze():
        # A FREEZE FOLLOWS WHAT LOADED (round 15, 2026-10-12): the timer, and 2 s after a clip is decoded or the library
        # reloads (gc_refreeze_soon), so a pack that loads its library after startup has it frozen too; never while she
        # speaks nor in a pause between her sentences (a full pass holds the paint): 3 s after she last spoke.
        spoke = 0.0
        while True:
            time.sleep(0.5)
            m = STATE.get("mouth")
            if m is not None and getattr(m, "speaking", False):
                spoke = time.time()
            due = _GC_FREEZE.get("due") or 0.0
            if not due or time.time() < due:
                continue
            if time.time() - spoke < 3.0:
                _GC_FREEZE["due"] = time.time() + 1.0
                continue
            _GC_FREEZE["due"] = 0.0
            gc.collect()
            gc.freeze()
            print("[conductor] gc quiet: refroze after %s, %d objects permanent"
                  % (_GC_FREEZE.get("why"), gc.get_freeze_count()), flush=True)

    threading.Thread(target=_refreeze, name="gc-refreeze", daemon=True).start()


def stall_watch_start() -> None:
    """Who holds the paint up (the User 2026-10-12, the house readings): during speech the paint runs on the semiframe
    grid but stalls 75-135 ms about once a second (4-5 mouth rows lost each time), while the paint thread uses a third
    of a core, the conductor takes ~15 major faults a reading and /v1/state answers in 15 ms; a bench under the same
    load does not stall. A watcher thread wakes every 5 ms; when it wakes > HOUSE_CONDUCTOR_STALL_MS late (it could not
    get the interpreter), it prints every thread's top frames, and every gc pass over 20 ms is printed with its
    generation. Measure only. HOUSE_CONDUCTOR_STALL_WATCH=1 (off by default)."""
    if os.environ.get("HOUSE_CONDUCTOR_STALL_WATCH", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return
    import gc
    import traceback
    limit = float(os.environ.get("HOUSE_CONDUCTOR_STALL_MS", "50")) / 1000.0
    gc_t = {}

    def _gc(phase, info):
        if phase == "start":
            gc_t["t"] = time.perf_counter()
        elif "t" in gc_t:
            ms = (time.perf_counter() - gc_t.pop("t")) * 1000.0
            if ms > 20.0:
                print("[stall] gc gen%s %.0f ms (collected %s)" % (info.get("generation"), ms, info.get("collected")),
                      flush=True)

    gc.callbacks.append(_gc)
    me = threading.get_ident()

    def _watch():
        last_print = 0.0
        seen_pass = None
        while True:
            t0 = time.perf_counter()
            time.sleep(0.005)
            late = time.perf_counter() - t0 - 0.005
            ps = STALL_PASS[0]
            if ps is not None and ps != seen_pass and time.perf_counter() - ps > 0.06:
                # a paint pass 60 ms in: where is it, and what is everyone else doing (a lock wait does not starve
                # this thread, so the late-wake test below never sees it)
                seen_pass = ps
                names = {t.ident: t.name for t in threading.enumerate()}
                lines = ["[stall] a pass is %.0f ms in" % ((time.perf_counter() - ps) * 1000.0)]
                for tid, fr in sys._current_frames().items():
                    if tid == me:
                        continue
                    st = traceback.extract_stack(fr)[-6:]
                    lines.append("[stall]   %s: %s" % (names.get(tid, tid), " <- ".join(
                        "%s:%d %s" % (os.path.basename(f.filename), f.lineno, f.name) for f in reversed(st))))
                print("\n".join(lines), flush=True)
            if late < limit:
                continue
            now = time.time()
            if now - last_print < 0.5:
                continue
            last_print = now
            names = {t.ident: t.name for t in threading.enumerate()}
            lines = ["[stall] watcher %.0f ms late" % (late * 1000.0)]
            for tid, fr in sys._current_frames().items():
                if tid == me:
                    continue
                st = traceback.extract_stack(fr)[-3:]
                lines.append("[stall]   %s: %s" % (names.get(tid, tid), " <- ".join(
                    "%s:%d %s" % (os.path.basename(f.filename), f.lineno, f.name) for f in reversed(st))))
            print("\n".join(lines), flush=True)

    threading.Thread(target=_watch, name="stall-watch", daemon=True).start()
    print("[conductor] stall watch on (> %.0f ms)" % (limit * 1000.0), flush=True)


# THE PLAYER ENDS WITHOUT A CRASH (round 17, 2026-10-13, .101 06:05 and 06:17: All OS Off after Open Agent left
# crissa-player.service `failed`, "Main process exited, code=dumped, status=6/ABRT"; player.log "[conductor] bye", then
# "terminate called without an active exception"). main() returned into the interpreter's shutdown while its daemon
# threads were still at work: the paint loop (a film frame read, the composite, cv2.imencode), a film warm, a frame
# route - all OpenCV, which lets go of the GIL inside C++. Python before 3.14 ends such a thread with pthread_exit when
# it comes back for the GIL, and that unwind through OpenCV's noexcept frames calls std::terminate ("FATAL: exception
# not rethrown" is the same fault). Nothing was restarted by Open Agent: it only lets the paint loop run at full rate
# again while the Wall reads her (Close Agent holds one frame at IDLE_FPS), so a stop then mostly lands inside OpenCV.
# The pack's player in a sandbox at 40 % of a core: 4 of 6 stops aborted (one more a SIGSEGV). Now the end stops the
# paint loop and waits for its pass (CLEAN_EXIT_JOIN_S at most), closes the server, runs the exit hooks, flushes, and
# leaves with os._exit: no interpreter shutdown under a running native thread, whatever ran before the stop. The
# house's units end by SIGTERM's default action, which never reaches this path; a Ctrl-C does. Kill:
# HOUSE_CONDUCTOR_CLEAN_EXIT=0 (round 16's return into the shutdown).
CLEAN_EXIT_ON = os.environ.get("HOUSE_CONDUCTOR_CLEAN_EXIT", "1").strip().lower() not in ("0", "false", "no", "off")
CLEAN_EXIT_JOIN_S = 2.0


def clean_exit(ph=None, ph_thread=None, srv=None, code: int = 0, *, exit_=None, join_s: float | None = None,
               log=None, hold_signals: bool = True) -> str:
    """Stop the paint loop and wait for its pass, close the server, run the exit hooks, flush, then os._exit(code).
    Returns what it did when `exit_` returns (tests); a step that fails never stops the next one."""
    did = []
    join_s = CLEAN_EXIT_JOIN_S if join_s is None else float(join_s)
    for sig in ((signal.SIGTERM, signal.SIGINT) if hold_signals else ()):
        try:
            signal.signal(sig, signal.SIG_IGN)     # a second stop (the launcher's, then its group's) never cuts it short
        except (ValueError, OSError):              # not the main thread
            pass
    try:
        if ph is not None:
            ph._stop = True
        if ph_thread is not None and ph_thread.is_alive():
            ph_thread.join(join_s)
            did.append("paint loop %s" % ("still in a pass" if ph_thread.is_alive() else "stopped"))
    except Exception as e:  # noqa: BLE001
        did.append("paint loop: %s" % type(e).__name__)
    try:
        if srv is not None:
            srv.server_close()
            did.append("server closed")
    except Exception as e:  # noqa: BLE001
        did.append("server: %s" % type(e).__name__)
    try:
        import atexit
        atexit._run_exitfuncs()            # what the shutdown would have run, on this thread, before the exit
    except Exception:  # noqa: BLE001
        pass
    (log or print)("[conductor] clean exit: %s" % (", ".join(did) or "nothing to stop"), flush=True)
    for f in (sys.stdout, sys.stderr):
        try:
            f.flush()
        except Exception:  # noqa: BLE001
            pass
    (exit_ or os._exit)(int(code))
    return ", ".join(did)


# THE INSTALL'S MOUTH BUILD (the User 2026-10-24: "every painted patch for each loop frame and tile could be built
# ahead <- we do this for all ascii frames too at first install too. Add it there"): at boot the conductor starts one
# child of itself (--mouth-patch-prefill, the same file, environment and library, so its keys are this process's) that
# paints every hub loop frame x every class x every tile the loop's playlist holds for it into lib/mouth_patch_disk,
# at the lowest CPU priority, stopped (SIGSTOP) while she is on air and continued after. What is on disk already is
# read, not painted, so a later boot finishes in seconds. Kill HOUSE_MOUTH_PATCH_PREFILL=0 (the disk tier still reads),
# HOUSE_MOUTH_PATCH_DISK=0 (neither).
_PATCH_PREFILL: dict = {"proc": None, "held": False}


def mouth_patch_setup(ph) -> None:
    try:
        import mouth_patch_disk
        if not (_MOUTH_PATCH_ON and mouth_patch_disk.enabled()):
            return
        mouth_patch.disk = mouth_patch_disk.DiskTier(mouth_patch.code_print(), log=lambda m: print(m, flush=True))
        print("[conductor] mouth patch disk: %s" % mouth_patch.disk.dir, flush=True)
        if os.environ.get("HOUSE_MOUTH_PATCH_CHILD") or not mouth_patch_disk.prefill_enabled():
            return
        import ctypes

        def _child_setup():
            try:                       # the build dies with its conductor; SIGKILL, since a build held on air is
                ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL)   # stopped and would sit on a TERM
            except OSError:
                pass
        env = dict(os.environ, HOUSE_MOUTH_PATCH_CHILD="1")
        _PATCH_PREFILL["proc"] = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--mouth-patch-prefill"],
                                                  env=env, cwd=str(ROOT), preexec_fn=_child_setup)
        print("[conductor] mouth patch build: pid %d" % _PATCH_PREFILL["proc"].pid, flush=True)
    except Exception as e:  # noqa: BLE001 - the build never costs the player its boot
        print("[conductor] mouth patch build: %s" % e, flush=True)


def mouth_patch_prefill_hold(on_air: bool) -> None:
    """Called by the paint every frame: the build stops while she is on air (her line's paint keeps every core it had)
    and goes on after."""
    p = _PATCH_PREFILL.get("proc")
    if p is None or bool(on_air) == _PATCH_PREFILL["held"]:
        return
    if p.poll() is not None:
        _PATCH_PREFILL["proc"] = None
        return
    try:
        os.kill(p.pid, signal.SIGSTOP if on_air else signal.SIGCONT)
        _PATCH_PREFILL["held"] = bool(on_air)
    except OSError:
        _PATCH_PREFILL["proc"] = None


def mouth_patch_prefill_main() -> None:
    """The child: this library's hub loops (her listen loop first), each frame x class x tile, through the paint itself
    (Playhead._composite under a class hold, then every other tile of the class the frame's playlist row holds)."""
    import mouth_patch_disk
    try:
        os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
    except (AttributeError, OSError):
        pass
    try:
        os.nice(19)
    except OSError:
        pass
    os.environ["HOUSE_MOUTH_PATCH_MB"] = "0"          # the child keeps nothing in memory: it writes the disk
    tag = "[mouth-patch build]"
    inv = inv_load()
    clips, pools = inv["clips"], (inv.get("pools") or {})
    lib = Library(merge_face_bank_clips(clips))
    motion, face = survey(clips, lib)
    graph = Graph(clips, pools, motion=motion, face=face, child_paths=inv.get("child_paths") or {}, kit=inv.get("kit") or {})
    seq = Sequencer(graph, pool_name="work")
    try:
        graph.listen_loop = _avatar_listen_loop(POSE_DIR)
        graph.roles = _avatar_roles(POSE_DIR)
    except Exception:  # noqa: BLE001
        pass
    mouth = Mouth()
    ph = Playhead(lib, seq, Strip(), mouth)
    for _k, _v in (("playhead", ph), ("seq", seq), ("lib", lib), ("mouth", mouth), ("strip", ph.strip), ("brain", None)):
        STATE[_k] = _v
    maps_disk_setup(ph)
    disk = mouth_patch.disk = mouth_patch_disk.DiskTier(mouth_patch.code_print(), log=lambda m: print(tag, m, flush=True))
    mouth_patch.disk_writes = True                    # only the build writes; the glass reads (its paint never waits)
    if not _MOUTH_PATCH_ON:
        print("%s nothing to build: the patch store is off" % tag, flush=True)
        return
    # the build paints STEADY frames only; on the glass a dissolve is blended from two of them (never built)
    globals()["MOUTH_XFADE_MS"] = 0.0
    # HER BOOT LOOP FIRST (2026-10-27): it is the loop she talks on in a pack, and the build stopped at the disk floor
    # on a full box with Setup's host loop built and her boot loop not (she spoke unpatched, 2 fps on the glass)
    try:
        boot = _avatar_boot_loop(POSE_DIR)
    except Exception:  # noqa: BLE001
        boot = None
    first = [k for k in (boot, getattr(graph, "listen_loop", None), graph.rest_for("down")) if k]
    loops = [k for k in dict.fromkeys(first + sorted(clips)) if k in clips and graph.role(k) == "loop"
             and lib.playlist(k) and (clips.get(k) or {}).get("file")]
    import mouth_viseme
    classes = list(mouth_viseme.CLASSES)
    totals = {}
    for k in loops:
        try:
            totals[k] = len(lib.frames(k) or []) * len(classes)
        except Exception:  # noqa: BLE001
            totals[k] = 0
    total = sum(totals.values())
    st = {"total": total, "jobs_done": 0, "painted": 0, "had": 0, "loops": loops, "loops_done": [], "done": False,
          "stopped": "", "since": time.time(), "code": mouth_patch.code_print(), "pid": os.getpid()}
    disk.progress(st)
    print("%s %d hub loops, %d frame x class jobs, into %s" % (tag, len(loops), total, disk.dir), flush=True)
    orig = ph._paste_head_kept
    seen: set = set()

    def every_tile(base, box, tile, lip, rel):
        out = orig(base, box, tile, lip, rel)
        for r2 in as_tiles(getattr(ph, "_pl_hit", None)):
            if r2 == rel or (ph.key, int(ph.i), getattr(ph, "_pl_class", None), r2) in seen:
                continue
            seen.add((ph.key, int(ph.i), getattr(ph, "_pl_class", None), r2))
            pr = lib.playlist_tile(r2)
            if pr:
                orig(base, box, pr[0], pr[1], r2)
        return out
    ph._paste_head_kept = every_tile
    dt = 1.0 / float(CLIP_FPS)
    for k in loops:
        arr = lib.frames(k) or []
        ph.play(k, force=True)
        globals()["MOUTH_XFADE_MS"] = 0.0            # a clip's own dials may set it back on entry
        for c in classes:
            mouth.force_class = c
            mouth.force = mouth.force if mouth.force is not None else 0.8     # a hold's level, as /v1/mouth sets it
            for i in range(len(arr)):
                ph.i = i
                h0, w0 = mouth_patch.stats["hit"], disk.stats["wrote"]
                try:
                    ph._composite(arr[i], dt)
                except Exception as e:  # noqa: BLE001 - one frame is the live paint's to make
                    print("%s %s f%d %s: %s" % (tag, k, i, c, e), flush=True)
                st["jobs_done"] += 1
                st["dials"] = patch_dials_print()
                st["painted"] += disk.stats["wrote"] - w0
                st["had"] += mouth_patch.stats["hit"] - h0
                if disk.stats["refused_budget"]:
                    st["stopped"] = "budget"
                    break
            seen.clear()
            disk.progress(st)
            if st["stopped"]:
                break
        if st["stopped"]:
            break
        st["loops_done"].append(k)
        disk.progress(st)
        print("%s %s done: %d written, %d already on disk, %.2f GB" % (tag, k, st["painted"], st["had"],
                                                                     disk.used / mouth_patch_disk.GB), flush=True)
    st["done"] = not st["stopped"]
    disk.progress(st)
    print("%s %s: %d written, %d on disk already" % (tag, "done" if st["done"] else "stopped on the disk budget",
                                                    st["painted"], st["had"]), flush=True)


def main() -> None:
    inv = inv_load()
    clips, pools = inv["clips"], (inv.get("pools") or {})
    lib0 = Library(merge_face_bank_clips(clips))
    motion, face = survey(clips, lib0)
    graph = Graph(clips, pools, motion=motion, face=face,
                  child_paths=inv.get("child_paths") or {},
                  kit=inv.get("kit") or {})
    if IS_BENCH:
        threading.Thread(target=_glass_pad_thread, daemon=True, name="glass-pad").start()
    try:                               # library sync: no thread at all unless its switch is on (2026-10-01)
        crissa_library_sync.start_timer(POSE_DIR, speaking=lambda: bool(STATE["mouth"].speaking),
                                        reloaded=lambda added: reload_debounced(STATE["playhead"]))
    except Exception as e:  # noqa: BLE001
        print("[conductor] library sync: %s" % e, flush=True)
    try:
        crissa_aligner.boot()          # a pack with better mouths on brings its aligner up; no-op on the house
    except Exception as e:  # noqa: BLE001  never costs the player its boot
        print("[conductor] better mouths: %s" % e, flush=True)
    if not IS_HOUSE:
        try:
            crissa_init.mic_at_start_later()   # round 23: a microphone never turned on comes on once the ear answers
        except Exception as e:  # noqa: BLE001
            print("[conductor] mic at start: %s" % e, flush=True)

    if not IS_HOUSE and os.environ.get("CRISSA_LILA_OUT_OF_IDLE", "1").strip().lower() not in ("0", "off", "false",
                                                                                          "no"):
        # Lila's clips only while Setup runs or she is on her plate (lib/conductor GUEST_GATE, 2026-10-13)
        import conductor as _conductor_mod
        _conductor_mod.GUEST_GATE = crissa_init.lila_may_play
    if not IS_HOUSE:
        # she holds her plate through Setup: no wrap walks her home, no other rack is queued over her (round 17,
        # 2026-10-13; lib/conductor GUEST_HOLD, kill CRISSA_LILA_HOLDS_PLATE=0)
        import conductor as _conductor_mod
        _conductor_mod.GUEST_HOLD = crissa_init.lila_holding
        # and her lines move her lips while she is on it (round 18, Mouth.speaker_may_paint; kill CRISSA_LILA_LIPS=0)
        globals()["LILA_PAINTS"] = crissa_init.lila_lips_now
        globals()["MOUTH_HUSH"] = crissa_init.mouth_hushed      # round 28: no mouth moves under her timing lines
    seq = Sequencer(graph, pool_name="work")
    try:                               # the face's declared listen loop is a core (manifest `listen_loop`)
        _ll = _avatar_listen_loop(POSE_DIR)
        seq.comms_cores = listen_cores(comms_cores_with(_ll), _ll, clips)   # round 38: the declared loop wins its hub
        graph.listen_loop = _ll
        graph.rest_loop = _avatar_rest_loop(POSE_DIR)        # round 39: where she sits between lines
        graph.roles = _avatar_roles(POSE_DIR)                # the named moments by role (MOMENT_ROLES, 2026-10-29)
    except Exception as e:  # noqa: BLE001  never costs the player its boot
        print("[conductor] listen loop: %s" % e, flush=True)
    try:
        seq.flourish_pct = int(load_wall_prefs().get("flourish_pct", 35))
    except Exception:
        pass
    lib = lib0
    strip = Strip()
    mouth = Mouth()
    ph = Playhead(lib, seq, strip, mouth)
    maps_disk_setup(ph)
    mouth_patch_setup(ph)
    stall_watch_start()
    gc_quiet_start()
    house_poll_start()

    rest = graph.rest_for("down") or "work"
    # CRISSA'S WALL BOOTS ON CRISSA, IN STANDARD (the User 2026-09-15: "fix the wall
    # booting onto lila-work when in Standard only").
    #
    # Lila's rows are merged into Crissa's library so she can host Render on
    # this glass (HOUSE_FACE_BANKS), and `lila-work` declares start/end
    # `down` — the same hub as Crissa's `work`. So rest_for("down") can hand
    # back HER loop, and every restart of :8100 came up with Lila on the
    # glass even with the flag on normal and Crissa's gateway up. The way off
    # was a hand call to /v1/home {"by": "render"}, which is all
    # house-mode.render_host_leave() does — and it never ran, because
    # nothing was leaving Render.
    #
    # In a park mode (render / beast / off) Lila hosting IS the design, so
    # this only bites in Standard. The steal rule further down is untouched:
    # if Crissa's gateway drops later, Lila still takes the glass at the seam.
    if not IS_BENCH and _lila_host_key(rest) and BOOT_CRISSA_REST:
        try:
            from crissa_gateway import occupancy_mode, PARK_MODES
            parked = occupancy_mode() in PARK_MODES
        except Exception:  # noqa: BLE001 — a bad read must not stop the boot
            parked = False
        if not parked:
            own = next((k for k in (["work"] + list(seq.pool()))
                        if not _lila_host_key(k) and (clips.get(k) or {}).get("file")), None)
            if own:
                print("[conductor] boot rest %s is a guest row and the flag is not parked"
                      " — booting on %s instead" % (rest, own), flush=True)
                rest = own
    # A PACK BOOTS ON HER LISTENING LOOP (the User 2026-10-05): the manifest's `boot_loop`, which the exporter
    # writes from the face's `listen_loop`; the house's manifests carry none, so the house boots as above. Camped,
    # so the bag does not walk her back to work at the first wrap. Kill CRISSA_BOOT_LOOP=0.
    boot_camp = None
    try:
        boot = _avatar_boot_loop(POSE_DIR)
    except Exception:  # noqa: BLE001  a bad manifest never costs the player its boot
        boot = None
    if boot and not IS_BENCH and (clips.get(boot) or {}).get("file"):
        print("[conductor] boot loop %s (manifest) instead of %s" % (boot, rest), flush=True)
        rest = boot_camp = boot
        _BOOT_CAMP["key"] = boot                  # the one camp a quiet wrap lifts (BOOT_CAMP_LIFT)
    # round 37 (.101 round 36 B: on a first boot she looped her walk-in): the phone room's home is her rest before a
    # first boot's opening clip (Lila's loop or the office), never that clip
    phone_home = rest
    if not IS_HOUSE and not IS_BENCH:
        # A FIRST BOOT OPENS ON LILA (.101 round 31, the User: "crissa loop flashed first again"): her loop, camped,
        # is the first picture, not Crissa's listen 6 s before Setup cues her (crissa_init.boot_host_loop)
        try:
            k0 = crissa_init.boot_host_loop()
            if k0 and (clips.get(k0) or {}).get("file"):
                print("[conductor] first boot: opening on %s (Lila hosts Setup) instead of %s" % (k0, rest), flush=True)
                rest = boot_camp = k0
            elif not k0:
                # round 34, THE OFFICE WAITS: her empty office (the entrance film's first frame), held, while Lila is in
                # Setup's window; never a second, still Lila on the Wall
                k1 = crissa_init.boot_office_key(clips, resolve=seq.graph.resolve)   # the worn library's walk-in
                if k1:
                    print("[conductor] first boot: her empty office (%s, first frame held) while Lila is in Setup's "
                          "window, instead of %s" % (k1, rest), flush=True)
                    rest = boot_camp = k1
                    INIT_HOLD["key"] = k1
        except Exception as e:  # noqa: BLE001  never costs the player its boot
            print("[conductor] first boot on Lila: %s" % e, flush=True)
    lib.frames(rest)
    ph.play(rest, force=True)
    if boot_camp:
        seq.camp = boot_camp
    # Enter first: a cold decode of the 1296×704 lean-in on the playhead
    # thread is the freeze on Hey Crissa. Warm does not block the rest loop.
    lib.warm(
        [k for k in (USER_TALK_ENTER, USER_TALK_KEY, USER_TALK_LEAVE,
                     "wake", "rest", "vocative-up", "vocative-down",
                     "listen-user", DOOR_GLANCE_KEY) if k in clips]
        + seq.pool()
    )

    STATE.update(playhead=ph, seq=seq, lib=lib, mouth=mouth, strip=strip, brain=None,
                 visit_lamp="amber")
    signal.signal(signal.SIGCHLD, _reap_children)

    def _privacy_loop() -> None:
        """Wall visit lamp is the SoT. Sage = Secure = Casual on.

        Fail closed: a dead presence looks amber, Casual stays hidden.
        AUTH is held while sage; the camera stays on the door. A new
        door face turns amber and kills confidential / casual comms.
        """
        while True:
            color = "amber"
            if LAMP_URL:
                try:
                    with urllib.request.urlopen(LAMP_URL, timeout=1.5) as r:
                        d = json.load(r)
                    color = str(d.get("visit_lamp") or d.get("color") or "amber").lower()
                except Exception:
                    color = "amber"
            head = STATE.get("playhead")
            if head is not None:
                head.apply_privacy(color == "sage", color)
            time.sleep(0.5)

    # A BENCH RUNS NO HOUSE THREADS (2026-09-15). The visit lamp, the door
    # poll, the user-talk watcher, the tour and the brain are the WALL's job:
    # a bench exists so the Composer can turn a dial without the room moving
    # underneath it. Nothing here is disabled on the wall.
    if not IS_BENCH:
        threading.Thread(target=_privacy_loop, daemon=True, name="visit-lamp").start()

    def _json_get(url: str):
        try:
            with urllib.request.urlopen(url, timeout=1.0) as r:
                return json.load(r)
        except Exception:
            return None

    def _user_talk_loop() -> None:
        """Door face only. The User's voice at beam 6 must not greet or FaceTime.

        Greeting the User by name is the FaceTime land after Hey Crissa (Playhead.play →
        take_ft_greet), never this poll. Door face (the camera's face count):
        cue work-door from heads-down. insert() does not cut; abort-hub
        seeks the work loop so the glance starts with the visor.
        """
        while True:
            pr = _json_get(PRESENCE_URL) if PRESENCE_URL else None
            seq = STATE.get("seq")
            if seq is not None:
                door_pr = pr if isinstance(pr, dict) else {}
                if "visit_lamp" not in door_pr:
                    door_pr = dict(door_pr)
                    door_pr["visit_lamp"] = STATE.get("visit_lamp") or "amber"
                door = seq.note_door_face(time.time(), presence=door_pr)
                if door.get("queued"):
                    head = STATE.get("playhead")
                    if head is not None:
                        head.abort_to_hub()
            time.sleep(0.3)

    if not IS_BENCH:
        threading.Thread(target=_user_talk_loop, daemon=True, name="user-talk").start()
    if not IS_BENCH and not IS_HOUSE and crissa_phone_room.on():
        threading.Thread(target=_phone_room_loop, args=(phone_home,), daemon=True, name="phone-room").start()
    # SHE COMPOSES BY DEFAULT. the User turned the brain on, I restarted the service
    # for an unrelated fix, and it came back off without saying so — the wall
    # kept moving, so nothing looked wrong. A setting that silently reverts on
    # restart is worse than one that cannot be changed.
    #
    # Safe to default on: an illegal pick is overruled by the attach law, she
    # answers off the frame path, and the bag draws whenever she is slow, busy
    # or unreachable. HOUSE_CONDUCTOR_BRAIN=0 turns it off for a box that wants
    # the bag.
    # THE TOUR (the User 2026-09-13: "cycle through every loop from now on to test
    # all boxes and pins"): on at boot unless HOUSE_CONDUCTOR_TOUR=0. It holds
    # while Lila hosts Render on this wall, and yields to any cue, camp or call.
    seq.tour_hold = _tour_hold
    if not IS_BENCH and os.environ.get("HOUSE_CONDUCTOR_TOUR", house_default("1", "0")) != "0":
        seq.tour_start(keys=_tour_keys_on_disk(seq))
        print("[conductor] tour on: %d loops, dwell %.0f s" % (len(seq.tour["keys"]), seq.tour["dwell_s"]), flush=True)
    if not IS_BENCH and os.environ.get("HOUSE_CONDUCTOR_BRAIN", "1") != "0":
        STATE["brain"] = _make_brain()
        if STATE["brain"] is not None:
            seq.brain = STATE["brain"].pick
            seq.brain_obj = STATE["brain"]
    ph_thread = threading.Thread(target=ph.run, daemon=True, name="playhead")
    ph_thread.start()

    crissa_init.configure(base_url="http://127.0.0.1:%d" % PORT, speak=_spawn_speak,
                          speaking=lambda: bool(STATE["mouth"].speaking),
                          busy=lambda: say_pending() > 0 or clip_audio.playing())
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print("[%s] http://%s:%d  resting on %s  strip=%s  pool=%s%s"
          % ("bench" if IS_BENCH else "conductor", HOST, PORT, ph.key,
             ",".join(strip.poses) or "MISSING", ",".join(seq.pool()),
             ("  mouth=%s  idle=%.0f fps after %.0f s" % (MOUTH_URL, IDLE_FPS, IDLE_AFTER_S))
             if IS_BENCH else ""),
          flush=True)
    if crissa_aligner.applicable():
        # A PACK'S PLAYER TAKES ITS ALIGNER WITH IT (2026-10-05): SIGTERM's default ended this process without a
        # word and the aligner it started (a session of its own) kept its port. Never on the house (applicable()
        # is False there), so the house's SIGTERM is the default exactly as before.
        def _term(signum, _frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, _term)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("[conductor] bye", flush=True)
    finally:
        crissa_aligner.stop()          # a no-op unless this player started one
    if CLEAN_EXIT_ON:
        clean_exit(ph, ph_thread, srv, 0)  # round 17: never return into the shutdown under a native thread


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--clip-frames":
        clip_frames_main(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
    elif len(sys.argv) >= 2 and sys.argv[1] == "--mouth-patch-prefill":
        mouth_patch_prefill_main()
    else:
        main()
