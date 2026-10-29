"""Per-loop phoneme playlist — one mouth for every yaw/twist the loop hits.

A talking loop is body motion (often silent). The voice owns the words. The
playlist joins them: this frame's pitch/yaw bin × this hop's viseme class
→ a native filmed mouth crop harvested at that pose. Never upscale.

Headshot + office still mint the loop (pose-flourish). This module is the
step that MUST run on every new PIP talking loop before the renderer
sees it. Authority: docs/MOUTH-INSTRUMENTS.md.

The renderer leads the broadcast by 100–200 ms (default 150): the jaw
prepares for the sound, the way a real mouth does. That dial is
HOUSE_VISEME_LEAD_MS. 0 disables. Do not reuse MOUTH_LATENCY_MS — that
is sink alignment (measured 36 ms), the opposite direction.
"""
from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

from mouth_viseme import (
    CLASS_FALLBACK,
    CLASSES,
    CLOSED,
    CLOSED_OPEN,
    REST,
    REST_OPEN,
    viseme_of,
)

# A head pose is [pitch, yaw, roll] in degrees. Same bins as
# scripts/pose-phoneme-map.py — do not fork.
YAW_EDGES = (-40, -12, -4, 4, 12, 40)
YAW_NAMES = ("L", "l", "0", "r", "R")
PITCH_EDGES = (-40, -8, 8, 40)
PITCH_NAMES = ("down", "level", "up")

SCHEMA = "mouth_playlist/v1"
# the User, 2026-09-01: *"we want to beat voice by 100ms."* Visual speech leads
# audio in real talkers, and we can afford the full lead because the TEXT is
# known before the first PCM byte — the sheet is written ahead, not reacted to.
# 100 ms is exactly 5 hops at the 20 ms envelope hop, so the stamp shift is a
# whole number and nothing rounds. 150 rounded to 8 hops = 160 ms actual.
LEAD_MS_DEFAULT = 100
LEAD_MS_MIN, LEAD_MS_MAX = 100, 200
PAD = 0.55  # harvest and paste share this; the crop is mouth + pad
# Extra below the chin so a profile /ah/ still has a lower jaw.
# Measured 2026-09-03: profile-90 AH chin sat 56 px PAST the tile.
PAD_CHIN = 0.20
# FACE-SCALED WIDTH (the User 2026-09-19: "The ZOOM was not accounted for... an
# equation WITHIN the Intake for conversion of box size based on face% of
# frame"). The width was mouth-relative: every talking film's tile stopped at
# ~±0.37 s of her nose while her cheeks (2d106 16/32, the Bezier chord's ends)
# sit at ±0.40 s and the painter's neck cut paints to ±0.52 s. Under edge_s
# 0.22 the tile was opaque only within ±0.15 s — the mouth corners were in the
# feather. The crop now holds the chord plus MX·s, never past nose ± CAP·s:
#   MX  = FACE_HALF 0.52 − cheek half 0.40 (the band the neck cut paints past
#         the outline);
#   CAP = FACE_HALF 0.52 + the neck cut's 0.06 feather (past it alpha is 0).
# Every term is × s, so a 2× zoom is a 2× crop. Kill HOUSE_HARVEST_FACE_CROP=0.
FACE_CROP_MX = 0.12
FACE_CROP_CAP = 0.58
FACE_CROP_U_GUESS = 0.45
FACE_CROP_TAG = "_fc1"     # tile name suffix: <clip>_f<n>_<bin>_<yaw>_fc1.png   # head_frame.cheeks_of: nose ± 0.45·u when unrecorded


def face_crop_on() -> bool:
    return os.environ.get("HOUSE_HARVEST_FACE_CROP", "1").strip().lower() not in ("0", "false", "off", "no")


def cheeks_for_crop(face=None, fit_rec: dict | None = None):
    """(cheek_l, cheek_r) in image px for face_crop, or None. From a head_frame
    fit record (cheek_l/cheek_r, any ruler; else nose ± 0.45·u as cheeks_of)."""
    fr = fit_rec or {}
    cl, cr = fr.get("cheek_l"), fr.get("cheek_r")
    if cl is not None and cr is not None:
        return (float(cl[0]), float(cl[1])), (float(cr[0]), float(cr[1]))
    u, n = fr.get("u"), fr.get("nose")
    if u is not None and n is not None:
        k = FACE_CROP_U_GUESS
        return ((float(n[0]) - k * float(u[0]), float(n[1]) - k * float(u[1])),
                (float(n[0]) + k * float(u[0]), float(n[1]) + k * float(u[1])))
    return None


def face_crop(shape, mouth, nose, chin, s, cheeks):
    """jaw_crop's box widened to her face: the cheek chord + FACE_CROP_MX·s,
    the margin (never the chord) capped at nose ± FACE_CROP_CAP·s. Height is
    jaw_crop's own with the real s. Returns (x0,y0,x1,y1) or None."""
    H, W = int(shape[0]), int(shape[1])
    base = _jaw_crop_mouth(shape, mouth, nose, chin, s)
    if base is None:
        return None
    bx0, by0, bx1, by1 = base
    s = float(s)
    (lx, _ly), (rx, _ry) = cheeks
    cx0 = min(float(bx0), float(lx), float(rx))
    cx1 = max(float(bx1), float(lx), float(rx))
    nx = float(nose[0]) if nose is not None else 0.5 * (cx0 + cx1)
    x0 = min(cx0, max(cx0 - FACE_CROP_MX * s, nx - FACE_CROP_CAP * s))
    x1 = max(cx1, min(cx1 + FACE_CROP_MX * s, nx + FACE_CROP_CAP * s))
    x0, x1 = int(max(0, x0)), int(min(W, x1))
    if x1 - x0 < 8 or by1 - by0 < 8:
        return None
    return x0, int(by0), x1, int(by1)


FACE_REFUSED: list = []   # (reason) per refused cut this process; a cutter reports the count


def _report_refused() -> None:
    if FACE_REFUSED:
        import collections, sys as _sys
        why = ", ".join("%s %d" % kv for kv in collections.Counter(FACE_REFUSED).items())
        print("FACE CROP REFUSED: %d tile(s) not cut — the head track has no face data "
              "for them (%s). Fix the head track (pose-head-frame) before picking; "
              "never cut a mouth-sized tile instead." % (len(FACE_REFUSED), why),
              file=_sys.stderr, flush=True)


import atexit as _atexit  # noqa: E402
_atexit.register(_report_refused)


def face_required() -> bool:
    """Kill: HOUSE_FACE_CROP_REQUIRED=0 restores the silent mouth-relative fallback."""
    return os.environ.get("HOUSE_FACE_CROP_REQUIRED", "1").strip() != "0"


def jaw_crop(shape, mouth, nose=None, chin=None, s=None, cheeks=None, require_face=False):
    """AABB: mouth+pad, plus the nose pin and the chin (wide-open lower jaw).

    Lip-only 2d106 boxes on profile clips cropped the gonion off. Pass
    68-pt nose/chin. Returns (x0,y0,x1,y1) or None if degenerate.
    With s and cheeks (and HOUSE_HARVEST_FACE_CROP on) the width is her
    face's, not her mouth's — face_crop. Switch off ignores cheeks; a call
    site that did not pass s before 2026-09-19 passes crop_scale(s), which is
    None under the switch, so off is byte-identical to the old harvest.
    """
    if cheeks is not None and s and float(s) > 1 and face_crop_on():
        return face_crop(shape, mouth, nose, chin, s, cheeks)
    if require_face and face_crop_on() and face_required():
        # A TILE cutter asked for her face and the head data is missing: cut
        # nothing. The silent fallback below is how a new face got small circle
        # mouths while every step "worked" (2026-09-19). A gap is loud: the
        # cutter prints the count and hub-check --strict names the pick.
        FACE_REFUSED.append("no cheeks" if cheeks is None else "no head scale")
        return None
    return _jaw_crop_mouth(shape, mouth, nose, chin, s)


def crop_scale(s):
    """s for a call site that dropped it before 2026-09-19; None when the
    face crop is killed (the old harvest had no face scale there)."""
    return s if face_crop_on() else None


def _jaw_crop_mouth(shape, mouth, nose=None, chin=None, s=None):
    """The mouth-relative box (the equation before 2026-09-19)."""
    H, W = int(shape[0]), int(shape[1])
    x, y, w, h = (float(v) for v in mouth)
    pad = max(w, h) * PAD
    xs = [x - pad, x + w + pad]
    ys = [y - pad, y + h + pad]
    span = float(s) if s and s > 1 else max(w, h) * 2.0
    if nose is not None:
        nx, ny = float(nose[0]), float(nose[1])
        xs += [nx - 0.25 * pad, nx + 0.25 * pad]
        ys += [ny - 0.08 * span, ny + 0.25 * pad]
    if chin is not None:
        cx, cy = float(chin[0]), float(chin[1])
        xs += [cx - 0.35 * pad, cx + 0.35 * pad]
        ys += [cy - 0.25 * pad, cy + PAD_CHIN * span]
    x0, y0 = int(max(0, min(xs))), int(max(0, min(ys)))
    x1, y1 = int(min(W, max(xs))), int(min(H, max(ys)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return x0, y0, x1, y1

def _tries(klass: str) -> tuple:
    """The index keys a class may paint from, in order: itself; rest-open falls back to rest; a new class with no
    tile yet to its fallback (round 41: Ah and Aa paint today's AH tile until he picks theirs), never blank."""
    if klass == REST_OPEN:
        return (klass, REST)
    fb = CLASS_FALLBACK.get(klass)
    return (klass, fb) if fb else (klass,)


# ASSUMED Preston-Blair typical openness 0=shut 1=wide. Used only when a
# bin has no labelled tile for that class. Prefer labels.
CLASS_OPEN_RANK = {
    "rest": 0.00, "MBP": 0.05,
    "FV": 0.22, "S/Z": 0.28, "TH": 0.34,
    "TDN": 0.40, "L": 0.42, "SH/CH": 0.48, "R": 0.50, "KG": 0.52,
    "EE": 0.58, "EH": 0.62, "ER": 0.64,
    "WOO": 0.70, "OH": 0.80, "AH": 1.00,
    "Y": 0.50, "H": 0.12,
    "rest-open": 0.44,
    # his new shapes (round 41), biggest first: Ah (father) > Aa (cat) > Uh (today's AH)
    "Ah": 1.10, "Aa": 1.05,
}


def viseme_lead_ms(raw: float | None = None) -> int:
    """Product dial. 0 = off. Otherwise clamped to 100–200."""
    if raw is None:
        try:
            raw = float(os.environ.get("HOUSE_VISEME_LEAD_MS", str(LEAD_MS_DEFAULT)))
        except (TypeError, ValueError):
            raw = float(LEAD_MS_DEFAULT)
    if raw <= 0:
        return 0
    return int(round(max(LEAD_MS_MIN, min(LEAD_MS_MAX, float(raw)))))


def viseme_lead_s(raw: float | None = None) -> float:
    return viseme_lead_ms(raw) / 1000.0


def viseme_lead_hops(hop_ms: float = 20.0, raw: float | None = None) -> int:
    ms = viseme_lead_ms(raw)
    if ms <= 0:
        return 0
    return max(1, int(round(ms / max(1.0, hop_ms))))


# STATIONS (the User 2026-09-11: "up and down seem like different banks" /
# "combine the two profile dupes, delete the other and combine 0s"). The
# degree bins cut single takes in four (the phoneme drill straddled the -8
# pitch and +4 yaw edges), so a bank is now WHERE SHE WAS FILMED, one label
# per take and per loop, side-neutral so a mirrored room reads the same:
#   forward  level/up, facing the User (drill, phoneme-up, talking-head, listen-back)
#   desk     heads-down at the laptop (harvest-a/b/c, mouthzoom's family, work loops)
#   turn     angled toward the screen (the shapes-angled takes, the angled cores)
#   close    pushed-in close-ups (close takes, close-user loops)
#   profile  the 45/90 profile takes — and any FRAME past PROFILE_YAW
# Encoded as pitch_bin=<station>, yaw_bin="0" so every "pitch/yaw/class" key,
# the conductor's lookup and the grid keep working unchanged.
STATIONS = ("forward", "desk", "turn", "close", "profile")
STATION_YAW = "0"
PROFILE_YAW = 40.0
HUB_STATION = {
    "down": "desk", "down_dog": "desk",
    "back": "forward", "back_b": "forward", "back_casual": "forward", "up": "forward",
    "down_talk": "turn", "down_talk_user": "turn",
    "down_close": "close", "down_close_user": "close",
    # a profile take is a profile bank by its pose_class (2026-09-29: the left-turn takes, minted with no
    # hub, fell through to "forward" and labelled every frame under 40 degrees forward)
    "profile": "profile",
}
STATION_NAMES = (   # stem substring -> station, first match wins
    ("profile", "profile"), ("angled", "turn"), ("mouthzoom", "close"), ("close", "close"),
    ("down-harvest", "desk"), ("phoneme-work", "desk"), ("-work", "desk"), ("type", "desk"), ("dog", "desk"),
    ("phoneme", "forward"), ("talking-head", "forward"), ("talk-up", "forward"), ("listen", "forward"), ("back", "forward"),
)
DESK_PITCH = -14.0   # fallback only: a take with no hub and no telling name


def station_of(row: dict | None = None, stem: str = "", pitch: float | None = None) -> str:
    """The bank a take or loop belongs to. Inventory first (pose_class, then
    the start/end hub), then the name, then the median pitch."""
    row = row or {}
    # pose_class first; then the END hub before the start — a slide's mouths
    # live where it lands (laptop-closeup-enter: down -> down_close is close)
    for k in ("pose_class", "end", "start"):
        st = HUB_STATION.get(str(row.get(k) or ""))
        if st:
            return st
    name = str(stem or row.get("file") or row.get("key") or "").lower()
    for sub, st in STATION_NAMES:
        if sub in name:
            return st
    if pitch is not None:
        return "desk" if float(pitch) <= DESK_PITCH else "forward"
    return "forward"


# TURN BANDS (the User 2026-09-29, "make the band limits settings"). A frame's own measured yaw
# picks its tiles: under TURN_FRONT_MAX she is facing us, TURN_FRONT_MAX..TURN_P45_MAX is the 45
# library's band, past TURN_P45_MAX the 90's. TURN_FILL_SIDE is the sign of yaw the "-r" takes
# serve (her right = negative yaw); 0 serves both sides. TURN_FILL_BANDS names the bands the
# painter may fill at all ("45", "45,90"). Only read by the turn fill (conductor
# HOUSE_MOUTH_TURN_FILL, default OFF, and scripts/pose-mouth-turnfill.py) and the front fold below.
# 12, not 15 (the User 2026-09-29, one source of truth): the turn blanking (pose-mouth-null-turn --min-yaw)
# already hands a frame over at 12 degrees; between 12 and 15 neither rule painted it.
TURN_FRONT_MAX = float(os.environ.get("HOUSE_TURN_FRONT_MAX", "12"))
TURN_P45_MAX = float(os.environ.get("HOUSE_TURN_P45_MAX", "70"))
TURN_FILL_SIDE = int(float(os.environ.get("HOUSE_TURN_FILL_SIDE", "-1")))
TURN_FILL_BANDS = tuple(b.strip() for b in os.environ.get("HOUSE_TURN_FILL_BANDS", "45").split(",") if b.strip())
TURN_LIBRARIES = {"45": os.environ.get("HOUSE_TURN_LIB_45", "profile-45-r"),
                  "90": os.environ.get("HOUSE_TURN_LIB_90", "profile-90-r")}
# her OTHER side's own takes (the User 2026-09-29: the left-turn harvests). A "45m"/"90m" band paints
# these when the library has the class; the mirror of the right-hand tile only where it does not.
TURN_LIBRARIES_OTHER = {"45": os.environ.get("HOUSE_TURN_LIB_45_OTHER", "profile-45-l"),
                        "90": os.environ.get("HOUSE_TURN_LIB_90_OTHER", "profile-90-l")}
# A PROFILE TAKE'S FRONTAL FRAMES ARE NOT PROFILE FRAMES (2026-09-29): the 90 opens and closes facing
# us (yaw -1..-10 on its first ~32 and last ~40 frames), but every row said `profile`, so the bench
# pasted a 60 deg mouth on a frontal face. Under TURN_FRONT_MAX such a frame is `forward`; the take's
# index has no forward bin, so it keeps its filmed mouth. Kill HOUSE_PROFILE_FRONT_FOLD=0.
# her LEFT from the right-hand takes, flipped (the User 09-29: "mirror the left"): a frame turned the other way
# takes the same band's tile + head block mirrored. Band names carry MIRROR_SUFFIX ("45m").
TURN_FILL_MIRROR = os.environ.get("HOUSE_TURN_FILL_MIRROR", "0").strip().lower() in ("1", "true", "yes", "on")
MIRROR_SUFFIX = "m"
# OFF since 2026-09-29 10:4x (the User's Grid: a "forward" row of stand-ins on each profile take, his pick
# installed into both rows). The painter's HOUSE_PROFILE_FRONT_FILMED keeps the filmed mouth on a profile
# take's frontal frames, so a forward bin there is never painted; the fold only split the Grid.
PROFILE_FRONT_FOLD = os.environ.get("HOUSE_PROFILE_FRONT_FOLD", "0").strip().lower() not in ("0", "false", "off", "no")


def frame_station(station: str, yaw: float | None) -> str:
    """A frame turned past PROFILE_YAW is a profile frame whatever its loop; a profile take's
    frame facing us (under TURN_FRONT_MAX) is not."""
    try:
        if yaw is not None and abs(float(yaw)) >= PROFILE_YAW:
            return "profile"
        if (PROFILE_FRONT_FOLD and station == "profile" and yaw is not None
                and abs(float(yaw)) < TURN_FRONT_MAX):
            return "forward"
    except (TypeError, ValueError):
        pass
    return station or "forward"


# THE PITCH FOLD (the User 2026-10-16, from .101's 10-15 note "not getting the full down tier when she tilts lowest"):
# a forward loop's frame tilted below PITCH_FOLD_IN takes the face's DESK tiles, and goes back to its own above
# PITCH_FOLD_OUT, a 1-degree band (her head track is smoothed; frame-to-frame noise is well under it). Measured: forward tiles were cut at -15..-20,
# desk at -21..-32 (median -26); listen-back dips to -28 on frames 204-274 (71 of 403). The painter still resolves
# a tile once per NOTE, so the fold never swaps a tile mid-sound (the 09-01 / 09-11 flicker was per-frame bins).
# ON since 2026-10-16 with its skin match (the conductor's PITCH_FOLD_SKIN: the desk take's warm light rode the
# tile onto her chin; jaw a* gap -5.3 -> -0.5). Kill HOUSE_PITCH_FOLD=0. Checkpoints pitch-fold-20261016,
# pitch-fold-skin-20261016.
PITCH_FOLD = os.environ.get("HOUSE_PITCH_FOLD", "1").strip().lower() not in ("0", "false", "off", "no")
# round 28 (.101 2026-10-17, the User: "a consistent trigger angle"): a pack folds at ONE angle, -17 both ways (forward
# tiles stop fitting at -15..-20); the house keeps -22 / -21 until it sets HOUSE_PITCH_FOLD_IN/OUT.
def _pack_fold(house: str, pack: str) -> str:
    try:
        import crissa_home
        return house if crissa_home.is_house() else pack
    except Exception:  # noqa: BLE001  no state home module: the house's value
        return house


# Round 29 (the User 2026-10-17: "Hard threshold with most of the tilt going to the down jaws"): one number both ways
# (no band), read on the raw per-frame tilt (<stem>.tilt.json). -13 on listen's raw tilt: 4 swaps a lap, 341 of 403
# frames (85 %) down, the whole bow (f174-f330) down, forward only on the apex f114-f173 and a 2-frame lift at f32-33;
# the down switch at f174 (at -16 it was f183, 6 swaps a lap raw). Round 28: -17; .101's drop-in: -16.
PITCH_FOLD_IN = float(os.environ.get("HOUSE_PITCH_FOLD_IN", _pack_fold("-22", "-13")))
PITCH_FOLD_OUT = float(os.environ.get("HOUSE_PITCH_FOLD_OUT", _pack_fold("-21", "-13")))
PITCH_FOLD_FROM = "forward"
PITCH_FOLD_TO = "desk"


def pitch_fold(folded: bool, pitch: float | None, inn: float | None = None, out: float | None = None) -> bool:
    """Is this frame folded to the desk tiles, given whether the last one was? No pitch keeps the last answer."""
    try:
        p = float(pitch)
    except (TypeError, ValueError):
        return bool(folded)
    inn = PITCH_FOLD_IN if inn is None else float(inn)
    out = PITCH_FOLD_OUT if out is None else float(out)
    if folded:
        return p < out
    return p < inn


def station_lookup(playlist: dict | None, station: str, viseme: str | int | None) -> str | list[str] | None:
    """Tile(s) for a class from a playlist's `<station>/0` bin, whatever its own rows say (the pitch fold reads
    another loop's index; that loop's frame rows describe ITS film, not this one)."""
    klass = viseme_of(viseme) if viseme is not None else None
    if not klass or not isinstance(playlist, dict):
        return None
    idx = playlist.get("index") or {}
    for k in _tries(klass):
        tiles = as_tiles(idx.get(index_key(station, STATION_YAW, k)))
        if tiles:
            return tiles if len(tiles) > 1 else tiles[0]
    return None


def turn_band(yaw: float | None, bands: tuple[str, ...] | None = None, front: float | None = None,
              p45: float | None = None, mirror: bool | None = None) -> str | None:
    """The turn band this yaw falls in ("45" / "90", "45m" / "90m" = the other side, mirrored), or None: facing us, the wrong side for the
    turn libraries, no head, or a band the fill is not allowed to use."""
    try:
        y = float(yaw)
    except (TypeError, ValueError):
        return None
    other = False
    if TURN_FILL_SIDE and y * TURN_FILL_SIDE <= 0:
        if not (TURN_FILL_MIRROR if mirror is None else mirror) or y == 0:
            return None
        other = True
    a = abs(y)
    if a < (TURN_FRONT_MAX if front is None else float(front)):
        return None
    band = "45" if a < (TURN_P45_MAX if p45 is None else float(p45)) else "90"
    if band not in (TURN_FILL_BANDS if bands is None else bands):
        return None
    return band + MIRROR_SUFFIX if other else band


def turn_lookup(turn_pl: dict | None, viseme: str | int | None) -> str | list[str] | None:
    """The turn library's tile(s) for this class, off its profile bin (rest-open falls to rest)."""
    klass = viseme_of(viseme) if viseme is not None else None
    if not klass or not turn_pl:
        return None
    idx = turn_pl.get("index") or {}
    for k in _tries(klass):
        tiles = as_tiles(idx.get(index_key("profile", STATION_YAW, k)))
        if tiles:
            return tiles if len(tiles) > 1 else tiles[0]
    return None


def station_bin(station: str) -> str:
    return f"{station}/{STATION_YAW}"


def is_station_bin(b) -> bool:
    return str(b or "").split("/")[0] in STATIONS


def bin_yaw(deg: float) -> str:
    return _bin(deg, YAW_EDGES, YAW_NAMES)


def bin_pitch(deg: float) -> str:
    return _bin(deg, PITCH_EDGES, PITCH_NAMES)


def pose_bin(pitch_bin: str, yaw_bin: str) -> str:
    return f"{pitch_bin}/{yaw_bin}"


def index_key(pitch_bin: str, yaw_bin: str, klass: str) -> str:
    return f"{pitch_bin}/{yaw_bin}/{klass}"


def frames_from_head(head_frames: list, station: str, fps: float = 24.0, n: int | None = None) -> list[dict]:
    """The playlist's per-frame rows from the FILM'S OWN head track, by station (a frame
    turned past PROFILE_YAW is a profile frame). 2026-09-17: a loop whose film was swapped at
    intake kept its index and lost its rows — `lookup` returns None without them, every class
    fell to the legacy sticker ("small mouth circles") and coverage still read ready. The rows
    used to come only from the film's harvest entries in map.json, which describe whatever
    film was harvested last; the head track is re-fit with the film."""
    fps = float(fps or 24.0)
    rows = []
    count = len(head_frames or []) if n is None else int(n)
    for i in range(count):
        rec = head_frames[i] if i < len(head_frames or []) else None
        pose = (rec or {}).get("pose") if isinstance(rec, dict) else None
        pitch = yaw = None
        if isinstance(pose, (list, tuple)) and len(pose) >= 2:
            pitch, yaw = pose[0], pose[1]
        rows.append({"i": i, "t": round(i / fps, 4), "pitch": pitch, "yaw": yaw,
                     "pitch_bin": frame_station(station, yaw), "yaw_bin": STATION_YAW,
                     "open": (rec or {}).get("open") if isinstance(rec, dict) else None})
    return rows


# A SILENT FILM IS NOT A SPEECH SOURCE (the User 2026-10-16, "if the intake doesn't know to look for speech then i am
# surprised it marked anything"). A loop is installed before its talking takes, so build_playlist used to fill all 19
# classes from the loop's OWN frames by jaw-openness rank - on a still listen loop that is 19 closed mouths (Crissa's
# store test: every class painted shut). Now a film with no spoken words supplies `rest` only; the other classes come
# from labelled donors (the talking takes), and where none exists yet the slot is a SILENT_GUESS - painted, never a
# hole (a hole paints the legacy sticker), and replaced by the first real pick a talking take brings
# (install_playlist, pose_intake.need_playlist). Kill HOUSE_LOOP_SILENT_SUPPLY=0.
SILENT_GUESS = "silent-guess"
LOOP_SILENT_SUPPLY = os.environ.get("HOUSE_LOOP_SILENT_SUPPLY", "1").strip().lower() not in ("0", "false", "off", "no")


def film_speaks(pose_dir: Path, file_name: str) -> bool:
    """True when the film carries spoken words: its timeline (visemes/<stem>.timeline.json, minted by the
    talking chain's `words` step from the film's own audio) holds at least one word. No timeline = silent:
    a loop never reaches the talking chain, and a talking take always has one before its playlist step."""
    stem = Path(str(file_name)).stem
    tl = Path(pose_dir) / "visemes" / (stem + ".timeline.json")
    try:
        d = json.loads(tl.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    words = d.get("words") if isinstance(d, dict) else None
    return any(isinstance(w, dict) and str(w.get("w") or w.get("word") or "").strip() for w in (words or []))


def frames_stale(pl: dict | None, n_film: int | None) -> str:
    """Why this playlist's rows do not cover its film; "" = they do."""
    if not isinstance(pl, dict) or not pl.get("index"):
        return ""
    fr = pl.get("frames") or []
    if not fr:
        return "no frames"
    if n_film and len(fr) != int(n_film):
        return "frames %d, film %d" % (len(fr), int(n_film))
    # A FLAT `open` COLUMN IS A STUB (2026-09-18, measured on the bench): the close-up take's
    # 1205 rows all read 0.4 and the FT loop's 163 all 0.35 while their head tracks vary,
    # so the drill scored slow passes against a constant (r -9, closures "100 %"). Rows are
    # only ever written from the head track, so a rewrite gives the real column.
    opens = [float(r["open"]) for r in fr if isinstance(r, dict) and r.get("open") is not None]
    if len(opens) > 1 and (max(opens) - min(opens)) < 1e-4:
        return "open is flat (%.2f on %d rows)" % (opens[0], len(opens))
    return ""


def playlist_station(pl: dict, row: dict | None = None, stem: str = "") -> str:
    st = str(pl.get("station") or "")
    if st in STATIONS:
        return st
    for b in pl.get("bins") or []:
        if is_station_bin(b) and str(b).split("/")[0] != "profile":
            return str(b).split("/")[0]
    return station_of(row or {}, stem)


def rows_behind_head(pl: dict, head_frames: list) -> int:
    """Rows with no yaw where the head track has a head (2026-10-03: the crop rescue gave Lila's 90 takes 89 + 163
    heads the rows, written from an older track, did not have)."""
    rows = (pl or {}).get("frames") or []
    return sum(1 for i, r in enumerate(rows) if i < len(head_frames) and isinstance(head_frames[i], dict)
               and head_frames[i].get("s") and (not isinstance(r, dict) or r.get("yaw") is None))


def sync_frames(pl: dict, head_frames: list, n_film: int | None, station: str, why: str = "") -> str:
    """Give a playlist rows that cover its film AND its head track. Touches `frames` and `n` ONLY — the index and
    `how` are the User's eye and are never rebuilt. Returns what it did, "" = nothing."""
    why = why or frames_stale(pl, n_film)
    if not why and head_frames:
        gap = rows_behind_head(pl, head_frames)
        if gap:
            why = "%d row(s) without a head the track has" % gap
    if not why or not head_frames:
        return ""
    n = int(n_film) if n_film else len(head_frames)
    rows = frames_from_head(head_frames, station, float(pl.get("fps") or 24.0), n=n)
    # A row names only a bin this index HAS: a desk flourish that wobbles past PROFILE_YAW has
    # no profile tiles, and a profile row would resolve nothing (the sticker again).
    have = {"/".join(str(k).split("/")[:2]) for k in (pl.get("index") or {})}
    for r in rows:
        if index_key(r["pitch_bin"], r["yaw_bin"], "x").rsplit("/", 1)[0] not in have:
            r["pitch_bin"] = station
    pl["frames"] = rows
    pl["n"] = n
    return "%s -> %d rows from the head track (%s)" % (why, n, station)


def playlist_path_for(file_name: str, pose_dir: Path) -> Path:
    """Sidecar next to the mp4. Inventory may also name it; this is the default."""
    return (pose_dir / str(file_name)).with_suffix(".playlist.json")


def load_playlist(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("schema") != SCHEMA:
        return None
    return d


def lookup(
    playlist: dict,
    frame_i: int,
    viseme: str | int | None,
) -> str | list[str] | None:
    """Tile path(s) for this playhead frame + class.

    A string is one mouth for the whole note. A list is a series the
    conductor walks once over the note (vowels: half → full → half) and
    then holds the last. Never a GIF — the wall paints PNGs.
    """
    klass = viseme_of(viseme) if viseme is not None else None
    if not klass:
        return None
    frames = playlist.get("frames") or []
    if not frames:
        return None
    j = min(max(int(frame_i), 0), len(frames) - 1)
    fr = frames[j]
    if not isinstance(fr, dict):
        return None
    pb, yb = str(fr.get("pitch_bin") or ""), str(fr.get("yaw_bin") or "")
    if not pb or not yb:
        return None
    idx = playlist.get("index") or {}
    tries = _tries(klass)
    for k in tries:
        for key in (
            index_key(pb, yb, k),
            index_key(pb, "0", k),
            index_key("level", yb, k),
        ):
            tiles = as_tiles(idx.get(key))
            if not tiles:
                continue
            return tiles if len(tiles) > 1 else tiles[0]
    return None


def build_playlist(
    *,
    clip: str,
    file: str,
    pose: str,
    entries: list[dict],
    donors: list[dict] | None = None,
    fps: float = 24.0,
    lead_ms: int | None = None,
    station: str | None = None,
    own_speaks: bool = True,
) -> dict:
    """entries = this loop's harvested frames. donors = labelled mouths
    at the SAME hub pose (never a face-on /ah/ onto a heads-down jaw).
    `station` bins every frame by the loop's station (profile past
    PROFILE_YAW) instead of the degree edges. `own_speaks` False (a silent
    film, `film_speaks`): its own frames supply `rest` only; any other class
    with no donor is a SILENT_GUESS from them."""
    lead = viseme_lead_ms(lead_ms)
    mine = [e for e in entries if isinstance(e, dict)]
    mine.sort(key=lambda e: int(e.get("frame") or 0))
    pool = list(mine)
    for d in donors or []:
        if isinstance(d, dict) and str(d.get("pose") or "") == str(pose):
            pool.append(d)

    bins: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    frames_out: list[dict] = []
    for e in mine:
        if station:
            pb, yb = frame_station(station, e.get("yaw")), STATION_YAW
        else:
            pb = str(e.get("pitch_bin") or bin_pitch(float(e.get("pitch") or 0)))
            yb = str(e.get("yaw_bin") or bin_yaw(float(e.get("yaw") or 0)))
        frames_out.append({
            "i": int(e.get("frame") or 0),
            "t": round(float(e.get("t") or 0), 4),
            "pitch": e.get("pitch"),
            "yaw": e.get("yaw"),
            "pitch_bin": pb,
            "yaw_bin": yb,
            "open": e.get("open"),
        })
        pair = (pb, yb)
        if pair not in seen:
            seen.add(pair)
            bins.append(pair)

    index: dict[str, str] = {}
    how: dict[str, str] = {}
    missing: list[str] = []
    for pb, yb in bins:
        bin_pool = [
            e for e in pool
            if str(e.get("pitch_bin") or "") == pb
            and str(e.get("yaw_bin") or "") == yb
            and str(e.get("pose") or pose) == str(pose)
        ]
        pose_pool = [e for e in pool if str(e.get("pose") or pose) == str(pose)]
        silent = LOOP_SILENT_SUPPLY and not own_speaks
        for klass in CLASSES:
            if silent and klass != "rest":
                not_own = lambda p: [e for e in p if str(e.get("file") or "") != str(file)]  # noqa: E731
                hit, src = _pick(not_own(bin_pool), klass)
                if hit is None:
                    hit, src = _pick(not_own(pose_pool), klass)
                if hit is None or src == "open-rank":   # a donor's open-rank is no better than ours
                    own = [e for e in bin_pool if str(e.get("file") or "") == str(file)] \
                        or [e for e in pose_pool if str(e.get("file") or "") == str(file)]
                    own_hit, _ = _pick(own, klass, own_file=file)
                    if own_hit is not None:
                        hit, src = own_hit, SILENT_GUESS
            else:
                hit, src = _pick(bin_pool, klass, own_file=file)
                if hit is None:
                    hit, src = _pick(pose_pool, klass, own_file=file)
            key = index_key(pb, yb, klass)
            tile = (hit or {}).get("tile") if hit else None
            if tile:
                index[key] = str(tile)
                how[key] = src
            else:
                missing.append(key)

    n_need = max(1, len(bins) * len(CLASSES))
    covered = n_need - len(missing)
    return {
        "schema": SCHEMA,
        "clip": clip,
        "file": file,
        "pose": pose,
        "fps": fps,
        "n": len(frames_out),
        "lead_ms": lead,
        "pad": PAD,
        "bins": [pose_bin(pb, yb) for pb, yb in bins],
        "index": index,
        "how": how,
        "frames": frames_out,
        "coverage": {
            "bins": len(bins),
            "classes": len(CLASSES),
            "have": covered,
            "need": n_need,
            "missing": missing,
            "ready": not missing,
        },
    }


def _pick(pool: list[dict], klass: str, own_file: str | None = None
          ) -> tuple[dict | None, str]:
    labelled = []
    for e in pool:
        k = viseme_of(e.get("viseme") or "")
        if k == klass and e.get("tile"):
            labelled.append(e)
    if labelled:
        if klass in CLOSED:
            shut = [
                e for e in labelled
                if float(e.get("open") or 1) <= CLOSED_OPEN
            ]
            if shut:
                labelled = shut
            else:
                rest = [
                    e for e in pool
                    if viseme_of(e.get("viseme") or "") == "rest"
                    and float(e.get("open") or 1) <= CLOSED_OPEN
                    and e.get("tile")
                ]
                if rest:
                    return _median_open(rest), "label-rest-standin"
                # A CLOSED class whose only labels are OPEN mouths. Measured
                # 2026-09-02: every one of the 7 map-labelled MBP tiles is a
                # wide-open mouth (teeth and tongue), against 8 of 8 sealed in
                # the User's own picks — these labels are machine, not his eye.
                # Keeping one paints a dub. Fall through to open-rank, which
                # prefers this clip and at least lands on its most shut mouth.
                labelled = []
        elif klass == REST_OPEN:
            # Mid-speech pause is parted, not sealed. A shut rest-open tile
            # would be the idle mouth on a comma.
            opened = [
                e for e in labelled
                if float(e.get("open") or 0) > CLOSED_OPEN
            ]
            if opened:
                labelled = opened
            else:
                opened = [
                    e for e in pool
                    if viseme_of(e.get("viseme") or "") in (REST, REST_OPEN)
                    and float(e.get("open") or 0) > CLOSED_OPEN
                    and e.get("tile")
                ]
                if opened:
                    return _median_open(opened), "label-rest-open"
                labelled = []
        if labelled:
            return _median_open(labelled), "label"
    # no label at this pose: openness rank. Prefer THIS clip's tiles so a
    # 3% open edge from a 22%-larger donor cannot steal rest (AH reading as TH).
    ranked = [e for e in pool if e.get("tile") and e.get("open") is not None]
    if own_file:
        own = [e for e in ranked if str(e.get("file") or "") == str(own_file)]
        if own:
            ranked = own
    if not ranked:
        return None, ""
    ranked.sort(key=lambda e: float(e.get("open") or 0))
    want = CLASS_OPEN_RANK.get(klass, 0.5)
    # his new shapes rank past AH (Ah 1.10, Aa 1.05: his order, kept as numbers); a rank past the most open tile is
    # the most open tile (round 41, V2 ON: 1.10 x (n-1) ran off the list)
    j = min(len(ranked) - 1, max(0, int(round(want * (len(ranked) - 1)))))
    return ranked[j], "open-rank"


def _median_open(rows: list[dict]) -> dict:
    rows = sorted(rows, key=lambda e: float(e.get("open") or 0))
    return rows[len(rows) // 2]


def _bin(v: float, edges: tuple, names: tuple) -> str:
    for i in range(len(names)):
        if edges[i] <= v < edges[i + 1]:
            return names[i]
    return names[-1] if v >= edges[-1] else names[0]


# the User 2026-09-02: f135 is heads-down AH (standalone). f77 is heads-up
# AH — different tone and tilt, not a tuple with f135. Do not walk
# those two as a series. EE/OO/OH are one tile. FV/S/Z never a series.
VOWEL_SERIES_CLASSES = frozenset()
# When hops do not give a note span (Composer class hold), walk the series
# once over this window then hold the last half. 300 ms × 3 is readable at
# 24 fps; live voice notes use the hop span instead.
SERIES_ONCE_S = 0.90
# A 2-list (the User's 1+2 after watching) on a long note is half + half of
# THAT note — 10+10 on a 20-frame hold, 12+12 on 24, never 1+2 five times.
# Composer has no note end: loop 10 frames + 10 frames so the first pick
# is not a one-frame flash under a 30-frame hold of the second.
SERIES_SLICE_FRAMES = 10
SERIES_SLICE_S = SERIES_SLICE_FRAMES / 24.0

# THE FIRST TILE OF A DOUBLE IS THE SETUP, AND IT IS A FIXED FIVE FRAMES.
#
# the User, 2026-09-03: *"it takes a 1/4 second without sound for that setup to get
# there, like Aww"* then *"In a double, the first frame is set to 5frames
# always. No split needed."*
#
# An even split was arithmetic, not speech. A mouth does not spend half of
# "Aww" arriving at the shape -- it takes about a fifth of a second to get
# there and then holds. Five frames is 208 ms at 24 fps, which is that.
#
# Fixed rather than derived from the lead on purpose: a constant is one thing
# to reason about, and the setup should not change length every time the lead
# dial moves. The lead decides how much of the setup lands BEFORE the sound;
# this decides how long the setup is.
#
# Capped at half the note, so a short consonant cannot be all setup and never
# reach its own shape.
SERIES_SETUP_FRAMES = 5
SERIES_SETUP_S = SERIES_SETUP_FRAMES / 24.0
# the User 2026-09-04 night: harvest clock. Cemented — no custom windups.
# Mark the first SOUND frame (shape stable). Word-start marks are needless.
# Singles: 0 windup. Each setup tile of a couplet/triple is SERIES_SETUP_FRAMES.
# The usual 1→2→land walk is two setup tiles → ten frames early, then the
# landing tile holds (harvest film often ~5/1 land:setup). Rest-open between
# words. Playback still adds HOUSE_VISEME_LEAD_MS (100) on top. Morning:
# series_index triples = 5+5+land; Clip Doctor marks land only.
COUPLET_WINDUP_TILES = 2
COUPLET_EARLY_FRAMES = COUPLET_WINDUP_TILES * SERIES_SETUP_FRAMES  # 10
COUPLET_EARLY_S = COUPLET_EARLY_FRAMES / 24.0
# One frame at 24 fps. The floor the LANDING tile is never squeezed below.
SERIES_FRAME_S = 1 / 24.0

# THE WINDUP IS THREE FRAMES AT 60 FPS PER APPROACH TILE, SIX AT MOST.
# the User 2026-09-11: "never more than three frames at 60fps for first 2 mouths
# in a three slide progression. Makes the wind ups easy to calculate" and
# "we key off the consonant so 6 slide windup is most it will ever be".
# The mouth renders at 60; the approach tiles of a walk hold 50 ms each from
# the note's start, then the landing holds until the next consonant or rest.
# How many approach tiles a note affords comes from its span in clip frames
# (24 fps): 3 frames → landing only; 4-6 → the last approach + landing;
# 7 or more → all three. The five-frame setup above is retired for live notes.
SERIES_STEP_FRAMES_60 = 3
SERIES_STEP_S = SERIES_STEP_FRAMES_60 / 60.0
SERIES_SPAN_TWO_S = 4 / 24.0
SERIES_SPAN_THREE_S = 7 / 24.0


# THE SEMIFRAME LADDER (the User 2026-09-21: "wire the ladder into
# series_approach in semiframes. But we need the two steps to be longer if
# the space is there").
#
# A slot is half a film frame since the F1a/F1b landing, so a walk is counted
# in semiframes and the ladder is one semiframe wide:
#
#     budget < 1 frame          landing only
#     + 1 semiframe             approach-2 + landing          (1/1)
#     + 2 semiframes            approach-1 + approach-2 + landing  (1/1/2+)
#
# THE LANDING KEEPS ITS WHOLE FRAME. the User 2026-09-03: "The sound waits on the
# final frame." A half-frame landing can fall between two drawn frames of a
# 21/s wall and never appear — the same reason cons_seen counts runs of two
# frames — so the approach is only ever taken from what is left ABOVE one
# frame. Measured on 414 aligned vowels: at a 1-semiframe landing 82 % would
# show three tiles, at a one-frame landing 49 % — and all 49 % are visible.
#
# THE STEP GROWS INTO THE ROOM, capped at SERIES_STEP_MAX_SEMIS. Measured on
# her own head track: the whole travel to a shape is 4 film frames (median,
# 155 free-running climbs) and 6 on a wide one, but only ~2 frames of it
# falls inside the vowel's own aligned span (57 measured, p90 3 frames) —
# the rest happens in the consonant ahead, which is the coarticulation the
# marked warmup carries. So an approach tile never needs more than 3
# semiframes, and two of them cap the walk at 3 film frames before the
# landing dwells. Beyond that she is not travelling any more, she is held,
# and a held approach tile is the "transition mouth standing in for the shape
# it was approaching" that 09-03 named.
# The clip rate the walk is counted in. One name, so a 60 Hz source changes
# this and nothing else; callers may pass their own fps.
SERIES_FPS = 24.0
SERIES_STEP_MIN_SEMIS = 1
SERIES_STEP_MAX_SEMIS = 3
SERIES_LANDING_MIN_SEMIS = 2          # one film frame — the 09-03 law


def semi_s(fps: float = SERIES_FPS) -> float:
    """One semiframe in seconds. Derived from the clip rate, never a literal:
    a 60 Hz source must not inherit 24 fps's 20.83 ms."""
    return 1.0 / (max(1.0, float(fps or SERIES_FPS)) * 2.0)


def series_budget_semis(dur: float, fps: float = SERIES_FPS) -> int:
    """Semiframes a note of `dur` can spend on approach, landing kept whole."""
    if dur <= 0:
        return 0
    total = int(dur / semi_s(fps) + 1e-9)
    return max(0, total - SERIES_LANDING_MIN_SEMIS)


def series_approach(n: int, dur: float, fps: float = SERIES_FPS) -> int:
    """How many of a walk's n-1 approach tiles a note of `dur` seconds shows.

    The ladder: one approach per SERIES_STEP_MIN_SEMIS of budget, never more
    than the walk has tiles. A note that cannot keep a whole frame for its own
    shape shows the landing alone.
    """
    if n <= 1:
        return 0
    budget = series_budget_semis(dur, fps)
    if budget < SERIES_STEP_MIN_SEMIS:
        return 0
    return int(min(n - 1, budget // SERIES_STEP_MIN_SEMIS))


def series_step_s(n: int, dur: float, fps: float = SERIES_FPS) -> float:
    """Seconds per approach tile: the budget shared out, capped.

    Grows into the room so a roomy vowel walks in 3-semiframe stages instead
    of snapping through 1-semiframe ones and then holding, and never exceeds
    the cap, so the leftover always falls to the landing where the shape is.
    """
    k = series_approach(n, dur, fps)
    if k <= 0:
        return 0.0
    budget = series_budget_semis(dur, fps)
    step = max(SERIES_STEP_MIN_SEMIS, min(SERIES_STEP_MAX_SEMIS, budget // k))
    return step * semi_s(fps)

# REST IS NOT A NOTE, so it does not get the note sampler.
#
# Every other class is a sound with a start and an end, and series_index
# slices THAT span: a 2-list on a 20-frame AH is 10 frames then 10 frames.
# Silence has no span. Slicing it flips the mouth every 10 frames for as
# long as the silence lasts — a twitch, not a breath.
#
# Two rests, two jobs (the User 2026-09-04): rest-open during speech (the whole
# mid-sentence gap), rest closed after .!? and at idle. Do not walk a mixed
# open/closed rest list — that is the idle pump. rest_step may still cycle
# several CLOSED rest tiles on a hub that filed more than one; rest-open is
# one mouth for the hop span, not a dwell sampler.
REST_DWELL_MIN_S = 1.6
REST_DWELL_MAX_S = 4.2
_REST_RND = random.Random()


def rest_step(n: int, now: float, cur: int | None = None, until: float = 0.0,
              rnd: random.Random | None = None,
              lo: float = REST_DWELL_MIN_S, hi: float = REST_DWELL_MAX_S
              ) -> tuple[int, float]:
    """Which closed-rest tile to hold at `now`, and when to move off it.

    Idle only. rest-open is a speech pause and does not call this.
    O(1) and caller-cached: the painter keeps (cur, until) and calls this
    every frame. Pass `rnd` to make it deterministic.

    It never returns the tile it is already on. A change you cannot see is a
    dwell spent for nothing, and with two tiles a fair coin stands still half
    the time — which is the frozen face again, just intermittently.
    """
    if n <= 1:
        # One rest mouth is the old behaviour, exactly. Nothing to cycle, and
        # no timer worth running.
        return 0, float(now) + 3600.0
    r = rnd or _REST_RND
    if cur is not None and 0 <= cur < n and float(now) < float(until):
        return cur, float(until)
    if cur is None or not (0 <= cur < n):
        nxt = r.randrange(n)
    else:
        # Draw from the other n-1 and skip over the one we are on, so every
        # draw is a visible move.
        j = r.randrange(n - 1)
        nxt = j if j < cur else j + 1
    return nxt, float(now) + lo + (hi - lo) * r.random()


def as_tiles(hit) -> list[str]:
    """Index value → list of pose-relative tile paths. str or list in, list out."""
    if hit is None or hit is False:
        return []
    if isinstance(hit, (list, tuple)):
        return [str(x) for x in hit if x]
    s = str(hit).strip()
    return [s] if s else []


def tile_stems(clip: str) -> list[str]:
    """Tile-name stems a clip's harvest may carry, most likely first.

    The forward half of `tile_take`. Heads-down harvests are named by the
    clip (`clip-talk-down-harvest-a` → `talk-down-harvest-a_f0142_…`);
    the profile / talk-up / harvest-c racks were cut by the harvest tool
    and carry its prefix (`clip-talk-profile-45` → `harvest-talk-profile-45`,
    `clip-talk-up-harvest` → `harvest-talk-up`). Measured 2026-09-10: the
    Takes-page grid 404'd 65 of 165 pick cells on the bare stem alone.
    """
    stem = str(clip or "").strip()
    if stem.startswith("clip-"):
        stem = stem[len("clip-"):]
    if not stem:
        return []
    out = [stem]
    alt = "harvest-" + stem.replace("-harvest", "")
    if alt != stem:
        out.append(alt)
    return out


def find_tile(tiles_dir, clip: str, frame: int):
    """The harvested PNG for (clip, frame) under either naming, else None."""
    d = Path(tiles_dir)
    for stem in tile_stems(clip):
        hits = sorted(d.glob("%s_f%04d_*.png" % (stem, int(frame))))
        if hits:
            return hits[0]
    return None


def tile_take(rel: str) -> str:
    """Harvest stem from a tile path. talk-down-harvest-a_f0142_….png → that stem.

    Picks and map.json disagree on clip strings (clip-talk-up-harvest vs
    harvest-talk-up). The file stem is the join key.
    """
    name = Path(rel or "").name
    i = name.find("_f")
    return name[:i] if i > 0 else Path(name).stem


def series_index(n: int, t0: float, t1: float, now: float,
                 loop: bool = False, setup_s: float | None = None,
                 land: float | None = None) -> int:
    """Which of n tiles at `now`. The last tile is the LANDING; the rest approach.

    THE APPROACH BELONGS IN THE WARMUP, NOT IN THE SOUND. the User, 2026-09-03:
    *"you can see it in the mouths when they prep for an AH the mouth moves
    well before the sound comes. The sound waits on the final frame."* and
    *"the way we tell it to split all but the landing frame evenly into this
    many frames: the gap. Otherwise it takes fractional seconds, two frame
    stages and spreads it out evenly over the whole sound."*

    Pass `land` — the time the sound's own bar starts, with [t0, land) being
    the warmup in front of it — and tiles 0..n-2 divide THAT evenly while tile
    n-1 holds [land, t1). A 3-list on a 2-frame warmup into a 9-frame WOO is
    one frame each of travelling and then nine of the shape, which is what the
    film does. Without it the three stages get 5/2/2 inside the sound itself:
    the mouth arrives late and then twitches through the vowel.

    No `land` (nothing marked in front of the note): tile 0 holds for
    `setup_s` (5 frames) at the head of the note and the rest divide what is
    left, the old behaviour, because there is nowhere else to put the
    approach.

    Composer (loop=True): wrap [t0, t1) so a 2-list stays 10+10 while held —
    a class hold has no note end to measure a setup against.
    """
    if n <= 1:
        return 0
    dur = float(t1) - float(t0)
    if dur <= 0:
        return 0
    elapsed = float(now) - float(t0)
    if land is not None and not loop:
        # The warmup carries every tile but the last; the sound holds the last.
        #
        # THE LANDING IS NEVER SHORTER THAN A FRAME. the User, 2026-09-03: "The
        # sound waits on the final frame." Measured on a 68-word line at the
        # desk: the median live note is 34 ms — under one frame — so a warmup
        # taken out of the note left the LANDING with nothing. AH never once
        # showed all three of its tiles across 23 notes, and a one-frame note
        # painted a TRANSITION mouth and then moved on, which is the approach
        # standing in for the shape it was approaching.
        #
        # So the note keeps a frame for its own shape before the approach may
        # have any of it, and a note too short for both is all landing. Half
        # the note still caps the rest, for the same reason the setup is
        # capped: a short consonant must not be all approach.
        warm = min(float(land) - float(t0), dur * 0.5, dur - SERIES_FRAME_S)
        if warm <= 0:
            return n - 1
        if elapsed >= warm:
            return n - 1
        if elapsed <= 0:
            return 0
        # The approach tiles nearest the landing, one STEP each, ending at
        # `land`; whatever warmup is marked in front of that holds the first
        # of them. The step is sized from the marked window and capped
        # (SERIES_STEP_MAX_SEMIS), so a long warmup walks in 3-semiframe
        # stages and then holds the first tile, instead of stretching each
        # stage until the approach stops reading as travel. The 1e-9 decides
        # a stage boundary that lands EXACTLY on a frame (measured
        # 0.49999999996 once), nothing else.
        one = semi_s()
        wsemis = int(warm / one + 1e-9)
        k = max(1, min(n - 1, wsemis // SERIES_STEP_MIN_SEMIS if wsemis else 1))
        step = max(SERIES_STEP_MIN_SEMIS,
                   min(SERIES_STEP_MAX_SEMIS, (wsemis // k) if k else SERIES_STEP_MIN_SEMIS)) * one
        start = warm - k * step
        if elapsed < start:
            return n - 1 - k
        return (n - 1 - k) + min(k - 1, int((elapsed - start) / step + 1e-9))
    if loop:
        frac = (elapsed % dur) / dur if elapsed >= 0 else 0.0
        if frac < 0:
            frac = 0.0
        return min(n - 1, int(frac * n))
    # LIVE NOTES (nothing marked in front): the walk keys off the note's
    # start — the consonant before it — so the windup is at the head of the
    # note: k approach tiles (the ones nearest the landing) at SERIES_STEP_S
    # each, then the landing holds. Short notes show fewer (series_approach);
    # a note too short for any approach is all landing, even at its first tick.
    step = series_step_s(n, dur) if setup_s is None else float(setup_s)
    k = series_approach(n, dur)
    if step <= 0:
        step = SERIES_STEP_MIN_SEMIS * semi_s()
    if k <= 0 or elapsed >= min(dur, k * step):
        return n - 1
    if elapsed <= 0:
        return n - 1 - k
    return (n - 1 - k) + min(k - 1, int(elapsed / step + 1e-9))


def pick_series_tile(hit, t0: float, t1: float, now: float,
                     loop: bool = False, land: float | None = None) -> str | None:
    """Tile to paint now. Always a str (or None) — never a list.

    A list in `playlist_tile` is unhashable and TypeError's the composite.
    HEAD painter assigned lookup() straight to the cache key; this is the
    str the painter may hash.
    """
    tiles = as_tiles(hit)
    if not tiles:
        return None
    return tiles[series_index(len(tiles), t0, t1, now, loop=loop, land=land)]


# Staff bars: rest/warmup are the gap, not a sound. the User, 2026-09-03:
# "Word starts at mouth, phoneme starts at sound. This gives us the PRECISE
# number of frames to run 1 and or 2." ⇥ is that count — the blank before
# the sound, no warmup bar. Ranked picks are the mouths; the word bar is
# the clock. Do not use |rank2.f - rank1.f|.
_GAP_K = frozenset({"warmup", "rest", "rest-open"})


def strip_word_key(token: str) -> str:
    """'Ah.' and 'Ah' are the same word. Punctuation is not identity."""
    return re.sub(r"[^A-Za-z]+", "", token or "").lower()


def word_children(word: dict, phon: list) -> list[dict]:
    """Phones that belong to this word bar.

    Overlap plus a w-guard: a phone is a child when it overlaps the bar
    and (w is empty, or stripped w matches this word). Never a phone whose
    w names a different word. Harvest-c Bed must not steal Air's leftover
    MBP; harvest-a Look keeps its w:null L (phone 1 of 3, like FV in Free).
    """
    try:
        wa, wb = int(word["a"]), int(word["b"])
    except (KeyError, TypeError, ValueError):
        return []
    ww = strip_word_key(str(word.get("w") or ""))
    out: list[dict] = []
    for p in phon or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("k") or "") in _GAP_K:
            continue
        try:
            a, b = int(p["a"]), int(p["b"])
        except (KeyError, TypeError, ValueError):
            continue
        if b < wa or a > wb:
            continue
        pw = strip_word_key(str(p.get("w") or ""))
        if pw and pw != ww:
            continue
        out.append(p)
    out.sort(key=lambda x: (int(x["a"]), int(x["b"])))
    return out


def word_foreign_sounds(word: dict, phon: list) -> list[dict]:
    """Overlapping non-gap phones whose w names a different word (STALE-W)."""
    try:
        wa, wb = int(word["a"]), int(word["b"])
    except (KeyError, TypeError, ValueError):
        return []
    ww = strip_word_key(str(word.get("w") or ""))
    out: list[dict] = []
    for p in phon or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("k") or "") in _GAP_K:
            continue
        try:
            a, b = int(p["a"]), int(p["b"])
        except (KeyError, TypeError, ValueError):
            continue
        if b < wa or a > wb:
            continue
        pw = strip_word_key(str(p.get("w") or ""))
        if pw and pw != ww:
            out.append(p)
    out.sort(key=lambda x: (int(x["a"]), int(x["b"])))
    return out


def _sound_bars(phon: list) -> list[dict]:
    out: list[dict] = []
    for p in phon or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("k") or "") in _GAP_K:
            continue
        try:
            out.append({"k": str(p.get("k") or ""), "a": int(p["a"]),
                        "b": int(p["b"])})
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["a"])
    return out


def _word_span(words: list | None, frame: int) -> tuple[int, int] | None:
    for w in words or []:
        try:
            wa, wb = int(w["a"]), int(w["b"])
        except (KeyError, TypeError, ValueError):
            continue
        if wa <= frame <= wb:
            return wa, wb
    return None


def _approach_start(sounds: list[dict], words: list | None, sound: dict) -> int:
    """First frame of ⇥ for this sound: word start, or the frame after the last sound."""
    sa = sound["a"]
    word = _word_span(words, sa)
    prevs = [s for s in sounds if s["b"] < sa]
    prev = prevs[-1] if prevs else None
    if word is not None:
        wa, _wb = word
        if prev is None or prev["b"] < wa:
            return wa
        return prev["b"] + 1
    if prev is not None:
        return prev["b"] + 1
    return sa


def word_setup_release(word: dict, phon: list) -> tuple[int, int] | None:
    """(⇥, 0) in frames. Unclamped. None if orphan or stale-w.

    ⇤ is gone — word.b is last phoneme.b. ⇥ may be negative (first sound
    sticks out the left). Same pairing as Clip Doctor kidsOf.
    """
    kids = word_children(word, phon)
    if not kids:
        return None
    try:
        wa = int(word["a"])
    except (KeyError, TypeError, ValueError):
        return None
    return int(kids[0]["a"]) - wa, 0


def sound_window(
    phon: list,
    words: list | None,
    frame: int,
    fps: float = 24.0,
) -> tuple[float, float, float] | None:
    """(t0, land, t1) for the sound at `frame`.

    [t0, land) is ⇥ — leading tiles of a couplet/triple split that time.
    [land, t1) is the sound — the last tile holds it. Rest has no window.
    The blank before the sound is the differential; no warmup bar.
    """
    fps = float(fps) if fps else 24.0
    f = int(frame)
    sounds = _sound_bars(phon)
    if not sounds:
        return None
    covering = next((s for s in sounds if s["a"] <= f <= s["b"]), None)
    if covering:
        sound = covering
    else:
        nxt = next((s for s in sounds if s["a"] > f), None)
        if nxt is None:
            return None
        t0_f = _approach_start(sounds, words, nxt)
        if not (t0_f <= f < nxt["a"]):
            return None
        sound = nxt
    t0_f = _approach_start(sounds, words, sound)
    return t0_f / fps, sound["a"] / fps, (sound["b"] + 1) / fps


def staff_tile(
    hit,
    phon: list,
    words: list | None,
    frame: int,
    fps: float = 24.0,
    run: tuple[int, int] | None = None,
) -> str | None:
    """Couplet/triple tile at `frame`, timed from the staff ⇥ not a house 5."""
    fps = float(fps) if fps else 24.0
    win = sound_window(phon, words, frame, fps)
    now = int(frame) / fps
    if win:
        return pick_series_tile(hit, win[0], win[2], now, land=win[1])
    if run is not None:
        return pick_series_tile(hit, run[0] / fps, run[1] / fps, now)
    tiles = as_tiles(hit)
    return tiles[0] if tiles else None


def note_span(
    frames: list, now: float, stale_s: float = 0.5
) -> tuple[float, float] | None:
    """[t0, t1) of the consecutive same-class run containing `now`."""
    if not frames:
        return None
    try:
        last_t = float(frames[-1][0])
        if last_t < now - stale_s:
            return None
        cur_i = None
        for i in range(len(frames) - 1, -1, -1):
            if float(frames[i][0]) <= now:
                cur_i = i
                break
        if cur_i is None:
            cur_i = 0
        row = frames[cur_i]
        if len(row) < 3:
            return None
        klass = viseme_of(row[2])
        if not klass:
            return None
        t0_i = cur_i
        while t0_i > 0:
            prev = frames[t0_i - 1]
            if len(prev) < 3 or viseme_of(prev[2]) != klass:
                break
            t0_i -= 1
        t1_i = cur_i
        while t1_i + 1 < len(frames):
            nxt = frames[t1_i + 1]
            if len(nxt) < 3 or viseme_of(nxt[2]) != klass:
                break
            t1_i += 1
        t0 = float(frames[t0_i][0])
        if t1_i + 1 < len(frames):
            t1 = float(frames[t1_i + 1][0])
        else:
            if t1_i > t0_i:
                dt = (float(frames[t1_i][0]) - t0) / (t1_i - t0_i)
            else:
                dt = 0.02
            t1 = float(frames[t1_i][0]) + max(0.02, dt)
        return t0, t1
    except (TypeError, ValueError, IndexError):
        return None


def lookup_tile(
    playlist: dict,
    frame_i: int,
    viseme: str | int | None,
) -> str | None:
    """First tile of the lookup. Benches that still hold one mouth per note."""
    tiles = as_tiles(lookup(playlist, frame_i, viseme))
    return tiles[0] if tiles else None


# Slots a merge must never rewrite. `eye` is the User's hand-picked bank
# (2026-09-01) and `_pick` cannot re-emit it: it exists only in the playlist
# JSON. `vlm` is an ops-vision pick waiting for his promote — same protection
# or the next harvest overwrites it. `vlm-3` is a vowel series whose middle
# tile is that ops full; harvest must not flatten it back to one open-rank.
# Any writer that rebuilds a file instead of merging DESTROYS all three —
# which is exactly what a full `build_playlist` + `write_text` does.
PROTECTED_HOW = ("eye", "vlm", "vlm-3", "same-spot")   # same-spot = his pick moved to another take (2026-10-27): his hand still

# A TAG CARRIES ITS PROVENANCE NOW, AND EVERY TEST WAS EXACT (2026-09-19).
# pose-install-picks writes `eye:crissa-close-harvest#203` (and
# `… walk3` for a series), so `tag in PROTECTED_HOW` matched only the bare
# string: a whole playlist of hand picks read `by_eye 0` on the Composer's
# lamp, and — worse — merge_playlist, culled_slots, headless_slots and
# donor-fill all saw those slots as UNPROTECTED and would write over the User's
# own picks. The hand is the tag up to its suffix; compare hands.
# HOUSE_HOW_HAND=0 restores the exact-string test.
HOW_HAND = os.environ.get("HOUSE_HOW_HAND", "1").strip().lower() not in ("0", "false", "off", "no")


def how_hand(tag) -> str:
    """The hand that made a slot: `eye:crissa-close-harvest#203 walk3` →
    `eye`. A bare tag is its own hand."""
    s = str(tag or "").strip()
    if not HOW_HAND:
        return s
    return s.split(":", 1)[0].split(" ", 1)[0]


def is_protected_how(tag, protect: tuple[str, ...] = PROTECTED_HOW) -> bool:
    """True when this slot was chosen by a protected hand, suffix or not."""
    return how_hand(tag) in protect


MOUTH_STATES = ("banked", "thin", "none", "n/a")


def health(playlist: dict | None, *, role: str = "") -> dict:
    """Five numbers off a sidecar, for a menu — never the sidecar itself.

    The Composer's mouth lamp. `coverage.ready` is NOT the signal: measured
    2026-09-05 it is true on all 18 sidecars, and
    docs/CLIP-INTAKE-AND-CORE-MINTS-20260902.md says outright "coverage.ready
    is not stage 5". What separates a bank somebody PICKED from a bank that
    was merely ranked is `how`, so `by_eye` counts PROTECTED_HOW slots — the
    ones the User or the ops vision chose. harvest-talk-up is `ready` with 54
    slots and ZERO by eye, and that is exactly the clip the lamp is for.

    `n/a` is a role that never speaks. A slide is the one clip the painter
    refuses outright, so a crossing with no bank is not a gap.

    `have`/`need` come from the sidecar's own `coverage` when it has one and
    are derived when it does not — clip-hd-demo predates the block and must
    not read as broken.

    `slots` is len(index) and is reported SEPARATELY from `have` because the
    two disagree in the wild: clip-work says coverage.have 18 while its index
    holds 19 keys, all eye, so `by_eye > have` is a real measured case.
    Both are passed through as the file states them. Reconciling them here
    would be inventing a number neither file contains.

    Returns state/have/need/ready/by_eye/slots. It does NOT return a lamp
    colour: which of these is trouble is the page's call, not the bank's.
    """
    blank = {"state": "none", "have": 0, "need": 0,
             "ready": False, "by_eye": 0, "slots": 0}
    if str(role) == "slide":
        return dict(blank, state="n/a")
    if not isinstance(playlist, dict):
        return dict(blank)
    index = playlist.get("index") or {}
    hows = playlist.get("how") or {}
    cov = playlist.get("coverage") or {}
    slots = len(index)
    have = int(cov["have"]) if isinstance(cov.get("have"), int) else slots
    if isinstance(cov.get("need"), int):
        need = int(cov["need"])
    else:
        need = len(playlist.get("bins") or ()) * len(CLASSES)
    if isinstance(cov.get("ready"), bool):
        ready = bool(cov["ready"])
    else:
        ready = have >= need > 0
    by_eye = sum(1 for v in hows.values() if is_protected_how(v))
    if not slots and not have:
        state = "none"
    elif ready:
        state = "banked"
    else:
        state = "thin"
    return {"state": state, "have": have, "need": need,
            "ready": ready, "by_eye": by_eye, "slots": slots}


def merge_playlist(
    path: Path,
    picks: dict[str, str | list[str]],
    how_tag: str,
    protect: tuple[str, ...] = PROTECTED_HOW,
    hows: dict[str, str] | None = None,
    replace: bool = False,
) -> dict:
    """Overlay named keys onto an EXISTING playlist. Everything else is left alone.

    The live picker's source of truth is `<clip>.playlist.json`. Overlay files
    (`visemes/picks/`) and `map.json` labels are inputs to it, not copies of it —
    so a pick has to be merged in, never rebuilt around.

    Index values may be a string (one mouth) or a list of strings (a series).
    Do not stringify a list — the wall steps the PNGs; it does not decode a GIF.

    Returns {"changed": {...}, "skipped": {...}, "missing": [...]}.
    `skipped` are keys whose current `how` is protected: reported, not written.

    Compare index/how as MAPS to check an unchanged file — `json.dumps(indent=2)`
    reorders keys and rewrites whitespace, so bytes are not the invariant.
    """
    pl = load_playlist(path)
    if pl is None:
        raise SystemExit("no playlist at %s — build it before merging" % path)
    index = pl.setdefault("index", {})
    how = pl.setdefault("how", {})
    changed: dict[str, str | list[str]] = {}
    skipped: dict[str, str] = {}
    missing: list[str] = []
    for key, tile in picks.items():
        tiles = as_tiles(tile)
        if not tiles:
            missing.append(key)
            continue
        tag = str((hows or {}).get(key) or how_tag)
        cur_how = str(how.get(key) or "")
        # Protected from other hands, not from the same tag. eye may
        # extend eye (1 → 1+2). Same tag replacing a different tile
        # is the installer shredder (Claude 2026-09-04) unless `replace`
        # (the desk picker installing a new SoT). Harvest cannot.
        if is_protected_how(cur_how, protect) and how_hand(tag) != how_hand(cur_how):
            skipped[key] = cur_how
            continue
        if is_protected_how(cur_how, protect) and how_hand(tag) == how_hand(cur_how) and not replace:
            cur_tiles = as_tiles(index.get(key))
            if cur_tiles and not set(cur_tiles).issubset(set(tiles)):
                skipped[key] = cur_how
                continue
        stored: str | list[str] = tiles if len(tiles) > 1 else tiles[0]
        if as_tiles(index.get(key)) == tiles and how.get(key) == tag:
            continue
        index[key] = stored
        how[key] = tag
        changed[key] = stored
    if changed:
        path.write_text(json.dumps(pl, indent=2) + "\n")
    return {"changed": changed, "skipped": skipped, "missing": missing}


def strip_orphan_how(pl: dict) -> list[str]:
    """Drop `how` tags that name no tile. Returns the keys dropped.

    The up-close pack (2026-09-16) landed as copies of harvest-1's playlist
    with the index emptied and `how` still naming 18 close/0/* slots as
    eye: every hole was "protected" by a pick that did not exist, so the
    fill path wrote nothing and the films could never paint. A pick is a tile;
    a tag with no tile is not a pick."""
    index = pl.get("index") or {}
    how = pl.get("how") or {}
    dropped = [k for k in list(how) if not as_tiles(index.get(k))]
    for k in dropped:
        del how[k]
    return dropped


def inventory_files(inv: dict | None) -> set[str]:
    """The films the inventory names. map.json also carries tiles of culled
    films (speak-phoneme, the profiles, mouthzoom …); on the CLOSE station
    those are never donors — "never roll the bank back to culled films"
    (test_close3_shares_the_close_bank_playlist)."""
    return {str(r.get("file") or "") for r in ((inv or {}).get("clips") or {}).values() if r.get("file")}


def culled_slots(pl: dict, mmap: dict, inv_files: set[str], protect: tuple[str, ...] = PROTECTED_HOW) -> list[str]:
    """Index keys whose tile comes from a film the inventory no longer names,
    excluding protected picks."""
    file_of = {}
    for e in mmap.get("entries") or []:
        t = str(e.get("tile") or "")
        if t.startswith("tiles/"):
            t = "visemes/phoneme-map/" + t
        file_of[t] = str(e.get("file") or "")
    out = []
    how = pl.get("how") or {}
    for k, v in (pl.get("index") or {}).items():
        if is_protected_how(how.get(k), protect):
            continue
        for t in as_tiles(v):
            f = file_of.get(t)
            if f is not None and f not in inv_files:
                out.append(k)
                break
    return out


def headless_slots(pl: dict, mmap: dict, protect: tuple[str, ...] = PROTECTED_HOW) -> list[str]:
    """Index keys whose tile has no `head` block in map.json (unpinnable under
    anchor=head), excluding protected picks. 2026-09-16: four of harvest-2's
    slots came from the old mouthzoom bank and painted in the corner."""
    heads = {}
    for e in mmap.get("entries") or []:
        t = str(e.get("tile") or "")
        if t.startswith("tiles/"):
            t = "visemes/phoneme-map/" + t
        heads[t] = isinstance(e.get("head"), dict)
    out = []
    how = pl.get("how") or {}
    for k, v in (pl.get("index") or {}).items():
        if is_protected_how(how.get(k), protect):
            continue
        tiles = as_tiles(v)
        if tiles and any(heads.get(t) is False for t in tiles):
            out.append(k)
    return out


def drop_headless_slots(path: Path, mmap: dict, inv_files: set[str] | None = None) -> list[str]:
    """Remove the headless (and, with inv_files, culled-film) unprotected
    slots from a playlist on disk so install_playlist can refill them with
    headed, in-inventory tiles. Returns the keys."""
    pl = load_playlist(path)
    if pl is None:
        return []
    keys = headless_slots(pl, mmap)
    if inv_files is not None:
        keys = sorted(set(keys) | set(culled_slots(pl, mmap, inv_files)))
    if keys:
        for k in keys:
            pl["index"].pop(k, None)
            (pl.get("how") or {}).pop(k, None)
        path.write_text(json.dumps(pl, indent=2) + "\n")
    return keys


def install_playlist(dest: Path, pl: dict) -> dict:
    """Mint a playlist if none exists. If one does, fill only missing keys.

    Existing keys — including open-rank — stay put. Protected how-tags cannot
    be rewritten even if they appear in `pl`. That is the opposite of
    `build_playlist` + `write_text`, which drops `eye` / `vlm` because
    `_pick` cannot emit them. An orphan `how` (no tile) is not a pick and is
    stripped first, on disk, so the hole can be filled.
    """
    cur = load_playlist(dest)
    if cur is None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        strip_orphan_how(pl)
        dest.write_text(json.dumps(pl, indent=2) + "\n")
        return {
            "minted": True,
            "changed": dict(pl.get("index") or {}),
            "skipped": {},
            "missing": [],
        }
    orphans = strip_orphan_how(cur)
    if orphans:
        dest.write_text(json.dumps(cur, indent=2) + "\n")
    holes: dict[str, str | list[str]] = {}
    hows: dict[str, str] = {}
    have = cur.get("index") or {}
    fresh_how = pl.get("how") or {}
    cur_how = cur.get("how") or {}
    for key, tile in (pl.get("index") or {}).items():
        if not as_tiles(tile):
            continue
        # a SILENT_GUESS stands in until a real pick arrives (LOOP_SILENT_SUPPLY)
        upgrade = (LOOP_SILENT_SUPPLY and key in have and how_hand(cur_how.get(key)) == SILENT_GUESS
                   and how_hand(fresh_how.get(key)) not in (SILENT_GUESS, ""))
        if key in have and not upgrade:
            continue
        holes[key] = tile if isinstance(tile, list) else str(tile)
        hows[key] = str(fresh_how.get(key) or "open-rank")
    if not holes:
        return {"minted": False, "changed": {}, "skipped": {}, "missing": [], "orphans": orphans}
    rep = merge_playlist(dest, holes, how_tag="open-rank", hows=hows)
    rep["orphans"] = orphans
    return rep


def protected_map(pl: dict) -> dict[str, tuple]:
    """(key → (tile, how)) for every protected slot. The invariance, not a count.

    A series stores as a tuple of paths so list-vs-list compares cleanly.
    """
    index = pl.get("index") or {}
    how = pl.get("how") or {}
    out: dict[str, tuple] = {}
    for key, tag in how.items():
        if not is_protected_how(tag):
            continue
        tiles = as_tiles(index.get(key))
        if len(tiles) > 1:
            out[key] = (tuple(tiles), str(tag))
        else:
            out[key] = (tiles[0] if tiles else "", str(tag))
    return out
