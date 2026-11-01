"""Lip sync for this machine (the User 2026-10-11, before the .101 demo: "the lip-sync slider ONLY").

Her mouth is stamped ahead of her sound by the library's own tune (`sheet_lead_ms` + `sync_zero_ms` in the library's
`mouth-dials.json`, the production lock). That tune was made on one machine's speakers. Another machine's sound can
come out later (a Bluetooth speaker most of all) or sooner, so her lips look early or late there. This is the one
dial for that: an offset per machine, kept in this machine's own state, never in the library.

    <state home>/lip-sync.json   {"lead_ms": N}     N > 0 moves her lips earlier, N < 0 later. No file = 0.

The player adds it in `effective_sheet_lead_ms()` (scripts/house-conductor.py), the one number every line she says
is stamped with: her replies (house-speak asks the player, /v1/mouth-tune {}) and the player's own lines (/v1/speak,
the test line). It is read through an mtime cache, so her next line uses a change with no restart. The library's
`mouth-dials.json` is never written here.

The steps are half film frames (the films run at 24 frames a second, two mouth slots a frame: 20.833 ms a step).
The total is held inside the clamp house-speak puts on a line it asks the player for (-200..+600 ms,
resolve_sheet_lead_ms), so the menu offers only the steps that reach her, and says so.

The Crissa menu carries it (lib/crissa_menu "lip_sync": both Walls, - / + in half frames, the value, a test line
through /v1/speak). CRISSA_LIPSYNC=0 ignores the file.

WHO SET IT (round 17, 2026-10-13): the file carries its provenance beside the value. Setup measures this machine (see
SETUP MEASURES THIS MACHINE below) and writes {"lead_ms": N, "by": "setup", "heard_ms", "ear_ms", "lines", "date", "at"};
the menu writes {"lead_ms": N, "by": "hand", "at"}. A file with no "by" (round 11-16) is the person's own. Only a value
Setup set is ever replaced by Setup; the menu says which it is.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from pathlib import Path

import crissa_home

FILE_NAME = "lip-sync.json"
FILM_FPS = 24.0                    # every film's container rate (24/1)
SLOTS_PER_FRAME = 2                # two mouth slots a film frame: lib/mouth_timing.SLOTS_PER_FRAME (a test pins both)
HALF_FRAME_MS = 1000.0 / FILM_FPS / SLOTS_PER_FRAME
# house-speak's clamp on a lead it asks the player for (scripts/house-speak.py resolve_sheet_lead_ms; a test pins it).
# ROUND 36 (the User 2026-10-20: "maybe give it a full second either way to adjust", from the +375 start): the floor
# went -200 -> -625 (lips after her sound: measured 2026-10-20 on the round 36 pack, a sheet at -600 ms is painted to its
# last row). The CEILING STAYS 600, measured the same day: the player cannot paint a row that is already due when the
# sheet arrives, and house-speak's sheet arrives with the sound's 400 ms pad, so a total lead past ~400 ms loses its
# first (lead - 400) ms of mouth (posted 0.4 s ahead: lead 600 first painted +336 ms late, 1000 +671, 1400 +1071). A
# full second earlier needs house-speak to hold the sound back by the same amount: the User's call (crissa-b3).
LINE_MIN_MS, LINE_MAX_MS = -625.0, 600.0
CAP_MS = 800.0                     # a hand-edited file never carries more than this either way
TEST_LINE = ("Pick up the big blue map, Bob. My mum made plum jam. Watch my lips meet on every p, b and m.")

_CACHE: dict = {}


def on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_LIPSYNC", "1")).strip().lower() not in ("0", "off", "false", "no")


# THE KIT STARTS AT +375 (the User 2026-10-20, via crissa-b3: "yes go with +375 as the start"). .101 set +375 by hand on
# rounds 33-35 after Setup measured 0, +104.2 and +208.3, and the User's "lips hit nicely" was a look at +375 without
# knowing the number. A machine with no lip-sync.json reads this start instead of 0. The code's own default stays 0 (the
# house, every test); the kit sets CRISSA_LIPSYNC_DEFAULT_MS in config/crissa-mouth.json. Never on the house.
# EVERY PROCESS OF THE KIT READS THEM (round 37, .101 round 36: Setup WROTE +250 on a kit that says it never writes):
# the launcher puts config/crissa-mouth.json's env on the player only, and Setup's first measurement runs in the enroll
# process (mouth_prints -> from_setup), which never had them. So a setting not in the environment is read from that
# file, in a kit tree only (no config/house-machine.json: the exporter never ships it): the house and the source
# tree's tests keep the code defaults. Kill CRISSA_LIPSYNC_KIT_FILE=0 (the environment alone, as round 36).
KIT_MOUTH = Path(__file__).resolve().parents[1] / "config" / "crissa-mouth.json"
_KIT_ENV: dict = {}


def _kit_tree() -> bool:
    return not crissa_home.is_house() and not Path(crissa_home.HOUSE_MACHINE).is_file()


def _kit_env() -> dict:
    try:
        st = KIT_MOUTH.stat()
    except OSError:
        return {}
    sig = (st.st_mtime_ns, st.st_size)
    if _KIT_ENV.get("sig") != sig:
        try:
            d = json.loads(KIT_MOUTH.read_text(encoding="utf-8"))
            env = d.get("env") if isinstance(d, dict) else None
        except (OSError, ValueError):
            env = None
        _KIT_ENV.update(sig=sig, env={k: str(v) for k, v in (env or {}).items() if v is not None})
    return dict(_KIT_ENV.get("env") or {})


def _setting(key: str, env: dict | None) -> str | None:
    """A lip-sync setting: the environment's (or the env dict given), else, read with no env dict in a kit tree, the
    kit's mouth file's."""
    e = os.environ if env is None else env
    v = e.get(key)
    if v is not None and str(v).strip() != "":
        return str(v)
    if env is None and _kit_tree() and str(os.environ.get("CRISSA_LIPSYNC_KIT_FILE", "1")).strip().lower() not in (
            "0", "off", "false", "no"):
        return _kit_env().get(key)
    return None


def default_ms(env: dict | None = None) -> float:
    if crissa_home.is_house():
        return 0.0
    try:
        v = float(str(_setting("CRISSA_LIPSYNC_DEFAULT_MS", env) or "0").strip())
    except ValueError:
        return 0.0
    return max(-CAP_MS, min(CAP_MS, v)) if math.isfinite(v) else 0.0


# SETUP REPORTS, IT DOES NOT SET (the same ruling): Setup's measurement read 167 ms short of his eye on round 35, so in
# 1.0.0 it logs its number ("lip sync: Setup measured +N, keeping +375") and the menu offers it as a suggestion, until it
# has matched his eye on two machines. The code default writes as before (round 17); the kit sets
# CRISSA_LIPSYNC_SETUP_WRITES=0 in config/crissa-mouth.json.
def setup_writes_on(env: dict | None = None) -> bool:
    return str(_setting("CRISSA_LIPSYNC_SETUP_WRITES", env) or "1").strip().lower() not in ("0", "off", "false", "no")


# THE GLASS MEASURE LANDS (round 42, .101's R41-3, the User 10-22: "perfect at 270 again"): Setup's own sample is taken
# with the Wall behind its window, so it only reports (above); the re-measure after her walk-in (reglass) reads the glass
# she is seen on, and .101 logged "measured +270.8 ms, keeping +375 ms" there, his own eye's value. That measurement is
# the one kept: it sets the slider unless the person set their own or the path is past the ceiling (said, left alone).
# The kit's start stays 375 until it lands. Kill CRISSA_LIPSYNC_GLASS_LANDS=0 (reglass reports only, round 41).
def glass_lands_on(env: dict | None = None) -> bool:
    return str(_setting("CRISSA_LIPSYNC_GLASS_LANDS", env) or "1").strip().lower() not in ("0", "off", "false", "no")


def path(home: Path | None = None) -> Path:
    return Path(home or crissa_home.HOME) / FILE_NAME


def _parse(raw: bytes) -> float:
    try:
        d = json.loads(raw.decode("utf-8", "replace") or "{}")
        v = float((d or {}).get("lead_ms") or 0.0) if isinstance(d, dict) else 0.0
    except (ValueError, TypeError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    return max(-CAP_MS, min(CAP_MS, v))


def offset_ms(home: Path | None = None, env: dict | None = None) -> float:
    """This machine's offset in ms; the kit's start (default_ms, 0 on the house) with no file, 0.0 with a broken file or
    the switch off. One stat a call: the file is re-read only when its mtime, size or inode changes (write_ms also drops
    the cache in the process that wrote)."""
    if not on(env):
        return 0.0
    p = path(home)
    try:
        st = p.stat()
    except OSError:
        _CACHE.pop(str(p), None)
        return default_ms(env)
    sig = (st.st_mtime_ns, st.st_size, st.st_ino)      # a write moves a new file into place: a new inode too
    hit = _CACHE.get(str(p))
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        v = _parse(p.read_bytes())
    except OSError:
        v = 0.0
    _CACHE[str(p)] = (sig, v)
    return v


def total_ms(base_ms: float, off_ms: float) -> float:
    """The library's lead plus this machine's offset, held inside what a line can carry (house-speak's clamp), so her
    replies and the player's own lines land on the same number."""
    return max(LINE_MIN_MS, min(LINE_MAX_MS, float(base_ms) + float(off_ms)))


def halves_of(ms: float) -> int:
    return int(round(float(ms) / HALF_FRAME_MS))


def ms_of(halves: int) -> float:
    return round(int(halves) * HALF_FRAME_MS, 3)


def reachable(base_ms: float) -> tuple:
    """(min_halves, max_halves): the offsets that still change what she says, given the library's own lead. Inward to
    whole half frames, so a step never lands past the clamp; 0 always stays in the range."""
    lo = math.ceil((LINE_MIN_MS - float(base_ms)) / HALF_FRAME_MS - 1e-9)
    hi = math.floor((LINE_MAX_MS - float(base_ms)) / HALF_FRAME_MS + 1e-9)
    cap = int(CAP_MS // HALF_FRAME_MS)
    lo, hi = max(lo, -cap), min(hi, cap)
    return min(lo, 0), max(hi, 0)


# THE LEAD FOLLOWS THE GLASS (round 45, .101 round 44, the User: "When the screens are loading she needs about +500
# lead; once complete we go to the 270"): the slider's value is right on the glass it was set on, but the desk Wall's
# own lag moves - 214 ms median (p90 365) while the picture cache filled at a first boot, 97-150 ms built - and her
# lips fall that much behind her voice. The player already measures the desk Wall live (wall-tk, /v1/glass-lag), so
# every line adds the glass's live lag less the lag the value was set on, in whole half frames:
#     follow = halves(live - ref)
# `ref` is kept with the value when it is set (the menu's own glass at the press, Setup's re-measure's glass), else the
# warm glass this machine measured (wall-tk while the newest lap drew nothing fresh: glass-warm.json, the median of its
# last GLASS_WARM_N readings), else there is none and nothing follows (a first boot before any warm reading keeps the
# kit's start, which was set cold). Measured on .101's first boot: +375 looked right at median 214 against +270 warm at
# ~110 - the median's difference (+104) is the eye's (+105), so the median is the default statistic (dial
# CRISSA_LIPSYNC_GLASS_STAT median | p90). Only the desk Wall reports wall-tk; a pack only. Kill
# CRISSA_LIPSYNC_GLASS_FOLLOW=0 (round 44: the value alone).
GLASS_WARM_FILE = "glass-warm.json"
GLASS_WHO = "wall-tk"
GLASS_WARM_N = 30                          # a minute of the Wall's 2 s reports: one clip switch does not move it
_GLASS: dict = {"warm": None, "warm_at": 0.0}


def glass_follow_on(env: dict | None = None) -> bool:
    if crissa_home.is_house():
        return False
    return str(_setting("CRISSA_LIPSYNC_GLASS_FOLLOW", env) or "1").strip().lower() not in ("0", "off", "false", "no")


def glass_stat(env: dict | None = None) -> str:
    v = str(_setting("CRISSA_LIPSYNC_GLASS_STAT", env) or "median").strip().lower()
    return "p90_ms" if v == "p90" else "median_ms"


def glass_warm_n(env: dict | None = None) -> int:
    try:
        return max(1, int(str(_setting("CRISSA_LIPSYNC_GLASS_WARM_N", env) or GLASS_WARM_N).strip()))
    except ValueError:
        return GLASS_WARM_N


def _stats(rec: dict | None) -> dict:
    out = {}
    for k in ("median_ms", "p90_ms"):
        try:
            if rec and rec.get(k) is not None:
                out[k] = round(float(rec[k]), 1)
        except (TypeError, ValueError):
            pass
    return out


def warm_path(home: Path | None = None) -> Path:
    return Path(home or crissa_home.HOME) / GLASS_WARM_FILE


def warm_glass(home: Path | None = None) -> dict:
    """{median_ms, p90_ms, n}: the median of this machine's warm wall-tk readings, or {}."""
    d = _GLASS["warm"]
    if d is None:
        try:
            d = json.loads(warm_path(home).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        _GLASS["warm"] = d if isinstance(d, dict) else {}
        d = _GLASS["warm"]
    rows = [r for r in (d.get("readings") or []) if isinstance(r, dict)]
    if not rows:
        return {}
    out = {"n": len(rows)}
    for k in ("median_ms", "p90_ms"):
        vals = [float(r[k]) for r in rows if r.get(k) is not None]
        if vals:
            out[k] = round(statistics.median(vals), 1)
    return out


# THE FOLLOW FROM HER FIRST LINE (hotfix after round 46, .101 round 46 "first lines while loading", the User: "Both lips
# were equally late during initial speech while loading"): on a first boot there is no value set on a glass and no warm
# glass yet, so nothing followed until Setup's re-measure - her opening lines and Lila's films played on the kit's +375
# through the heaviest part of the build. With neither known, the follow now keys to this process's first desk-Wall
# reading. Kill CRISSA_LIPSYNC_FOLLOW_FIRST=0 (round 46: nothing to follow from).
# THE SET-ON GLASS IS THE MEASURE'S WINDOW (the same row: the follow keyed to one 194 ms snapshot while the glass swung
# 152 -> 338 -> 198 ms): a value Setup's re-measure sets keeps the median of the desk-Wall readings over the window its
# measure ran in (from her timing lines' start, mark_measure_start, to the value's write), not the one reading at the
# write. Kill CRISSA_LIPSYNC_REF_WINDOW=0 (round 46: the snapshot). The readings kept are GLASS_RING_N (an hour of the
# Wall's 2 s reports covers any Setup), dial CRISSA_LIPSYNC_GLASS_RING_N.
GLASS_RING_N = 1800


def follow_first_on(env: dict | None = None) -> bool:
    return str(_setting("CRISSA_LIPSYNC_FOLLOW_FIRST", env) or "1").strip().lower() not in ("0", "off", "false", "no")


def ref_window_on(env: dict | None = None) -> bool:
    return str(_setting("CRISSA_LIPSYNC_REF_WINDOW", env) or "1").strip().lower() not in ("0", "off", "false", "no")


def _ring_n(env: dict | None = None) -> int:
    try:
        return max(1, int(str(_setting("CRISSA_LIPSYNC_GLASS_RING_N", env) or GLASS_RING_N).strip()))
    except ValueError:
        return GLASS_RING_N


def mark_measure_start(t: float | None = None) -> None:
    """Setup's timing lines start now: the window a re-measure's set-on glass is the median of."""
    _GLASS["measure_from"] = time.time() if t is None else float(t)


def glass_window(since: float | None, until: float | None = None) -> dict:
    """{median_ms, p90_ms, n}: the median of each over the desk-Wall readings in [since, until], or {}."""
    if since is None:
        return {}
    until = time.time() if until is None else until
    rows = [st for (t, st) in list(_GLASS.get("ring") or []) if since <= t <= until]
    if not rows:
        return {}
    out = {"n": len(rows)}
    for k in ("median_ms", "p90_ms"):
        vals = [float(r[k]) for r in rows if r.get(k) is not None]
        if vals:
            out[k] = round(statistics.median(vals), 1)
    return out


def note_glass(who: str, rec: dict, warm: bool, home: Path | None = None, env: dict | None = None,
               now: float | None = None) -> None:
    """The player's glass report: a warm desk-Wall reading joins glass-warm.json (written at most every 30 s)."""
    if who != GLASS_WHO:
        return
    _GLASS["live"] = dict(rec or {})                       # the menu's press keeps the glass it was set on
    st0 = _stats(rec)
    if "median_ms" in st0:
        if _GLASS.get("first") is None:
            _GLASS["first"] = st0                           # THE FOLLOW FROM HER FIRST LINE
        ring = _GLASS.setdefault("ring", [])
        ring.append((time.time() if now is None else now, st0))
        del ring[:-_ring_n(env)]
    if not warm or not glass_follow_on(env):
        return
    st = _stats(rec)
    if "median_ms" not in st:
        return
    warm_glass(home)                                       # loaded once
    d = _GLASS["warm"]
    rows = (list(d.get("readings") or []) + [st])[-glass_warm_n(env):]
    d["readings"] = rows
    now = time.time() if now is None else now
    if now - float(_GLASS["warm_at"]) >= 30.0:
        _GLASS["warm_at"] = now
        try:
            _write_json(warm_path(home), {"readings": rows, "at": _stamp(now)})
        except OSError:
            pass


def glass_ref(home: Path | None = None) -> dict:
    """The glass the value was set on: the file's own `glass_ref`, else the warm glass, else {}."""
    ref = record(home).get("glass_ref")
    if isinstance(ref, dict) and _stats(ref):
        return dict(_stats(ref), how="set on")
    w = warm_glass(home)
    if w:
        return dict(w, how="warm")
    first = _GLASS.get("first")
    if first and follow_first_on():
        return dict(first, how="its first reading")
    return {}


def glass_follow(glass_state: dict | None, home: Path | None = None, env: dict | None = None) -> dict:
    """{"ms", "live", "ref", "stat", "how"}: what this line adds for the glass's live lag (ms 0.0: nothing)."""
    out = {"ms": 0.0}
    if not on(env) or not glass_follow_on(env):
        return dict(out, how="off")
    rec = (glass_state or {}).get(GLASS_WHO) or {}
    stat = glass_stat(env)
    if not rec.get("fresh") or rec.get(stat) is None:
        return dict(out, how="no fresh desk Wall reading")
    ref = glass_ref(home)
    if ref.get(stat) is None:
        return dict(out, how="no glass to follow from yet", live=rec.get(stat))
    k = halves_of(float(rec[stat]) - float(ref[stat]))
    return {"ms": ms_of(k), "halves": k, "live": rec.get(stat), "ref": ref.get(stat), "stat": stat,
            "how": ref.get("how")}


def write_ms(ms: float, home: Path | None = None, meta: dict | None = None) -> Path | None:
    """Keep this machine's offset: {"lead_ms": N}, written to a temporary file and moved into place (an interrupted
    write never leaves half a file). 0 removes the file (no file = 0). Returns the file, or None when it was removed.
    `meta` (round 17): who set it, kept beside the value; a value with its provenance is kept even at 0, so a 0 the
    person chose (or Setup measured) is remembered as theirs."""
    p = path(home)
    v = max(-CAP_MS, min(CAP_MS, float(ms)))
    if halves_of(v) == halves_of(default_ms()) and not meta:      # no file = the start (0, or the kit's)
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        _CACHE.pop(str(p), None)
        return None
    _write_json(p, dict({"lead_ms": round(v, 3)}, **(meta or {})))
    _CACHE.pop(str(p), None)
    return p


def _write_json(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def record(home: Path | None = None) -> dict:
    """The file as written, with its provenance: {} when there is none; a file that cannot be read is the person's
    (by "hand"), so Setup never replaces what it cannot read."""
    p = path(home)
    try:
        raw = p.read_bytes()
    except OSError:
        return {}
    try:
        d = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        d = None
    if not isinstance(d, dict):
        return {"by": "hand", "unreadable": True}
    d = dict(d)
    d["by"] = "setup" if str(d.get("by") or "") == "setup" else "hand"
    return d


def _stamp(when: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() if when is None else when))


def _num(n) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "?"
    return ("%d" % round(v)) if abs(v - round(v)) < 0.05 else ("%.1f" % v)


def _signed(n: float, unit: str) -> str:
    n = float(n)
    txt = ("%d" % round(n)) if abs(n - round(n)) < 0.05 else ("%.1f" % n)
    return ("+" if n > 0 else "") + txt + unit


def view(base_ms: float = 0.0, home: Path | None = None, env: dict | None = None) -> dict:
    """What the menu shows: the value in half frames and ms, the reachable range said plainly, the test line."""
    off = offset_ms(home, env)
    k = halves_of(off)
    lo, hi = reachable(base_ms)
    if k == 0:
        value = "0: the library's own timing"
    else:
        value = "%s half frame%s (%s): her lips move %s" % (_signed(k, ""), "" if abs(k) == 1 else "s",
                                                            _signed(off, " ms"), "earlier" if k > 0 else "later")
    rec = record(home)
    if rec.get("by") == "setup":
        # round 17: say that Setup measured it, and what it heard
        value += ". Setup measured these speakers%s%s" % (
            " on %s" % rec["date"] if rec.get("date") else "",
            " (her voice reached the microphone in %s ms, %s lines)" % (_num(rec.get("ear_ms")), rec.get("lines"))
            if rec.get("ear_ms") is not None else "")
    rng = ("Reachable here: %s to %s half frames (%s to %s). Every line she says is held to %d to +%d ms in total, "
           "and this library's own lead is %s." % (_signed(lo, ""), _signed(hi, ""), _signed(ms_of(lo), " ms"),
                                                   _signed(ms_of(hi), " ms"), LINE_MIN_MS, LINE_MAX_MS,
                                                   _signed(float(base_ms), " ms")))
    out = {"on": on(env), "lead_ms": round(off, 3), "halves": k, "half_frame_ms": round(HALF_FRAME_MS, 3),
           "base_ms": round(float(base_ms), 3), "total_ms": round(total_ms(base_ms, off), 3),
           "min_halves": lo, "max_halves": hi, "min_ms": ms_of(lo), "max_ms": ms_of(hi),
           "line_min_ms": LINE_MIN_MS, "line_max_ms": LINE_MAX_MS,
           "label": "Lip sync…", "value": value, "range": rng,
           "how": ("+ moves her lips earlier, - later, half a film frame a step. Speakers that add delay (Bluetooth "
                   "most of all) usually need -. Say the test line and watch her lips meet on p, b and m."),
           "test_line": TEST_LINE, "file": str(path(home)), "by": str(rec.get("by") or "")}
    if rec.get("by") == "setup":
        out["setup"] = {k2: rec.get(k2) for k2 in ("heard_ms", "ear_ms", "lines", "date")}
    note = setup_note(home)
    if note.get("outcome") == "measured" and note.get("suggest_ms") is not None:
        # the kit (2026-10-20): Setup's number is a suggestion beside the value, never set by itself
        out["suggest_ms"] = note["suggest_ms"]
        out["suggest_halves"] = halves_of(float(note["suggest_ms"]))
        out["value"] += ". Setup measured %s for these speakers%s (a suggestion: + and - move it there)" % (
            said_ms(float(note["suggest_ms"])), " on %s" % note["date"] if note.get("date") else "")
        if note.get("slow") and note.get("slow_ms") is not None:
            out["slow"] = slow_words(float(note["slow_ms"]))
            out["value"] += ". " + out["slow"]
    if not rec and halves_of(off) and halves_of(off) == halves_of(default_ms(env)):
        out["value"] += ". This is the kit's start"
    if not out["on"]:
        out["value"] = "off (CRISSA_LIPSYNC=0): the library's own timing"
    return out


def act(body: dict | None, base_ms: float = 0.0, home: Path | None = None, env: dict | None = None) -> dict:
    """{"step": +1|-1} moves half a frame, {"halves": k} or {"lead_ms": N} sets it, {"reset": true} goes back to 0.
    Held inside the reachable range. Writes <state home>/lip-sync.json only."""
    b = body or {}
    if not on(env):
        return {"ok": False, "error": "Lip sync is off on this computer (CRISSA_LIPSYNC=0).",
                "lip_sync": view(base_ms, home, env)}
    cur = halves_of(offset_ms(home, env))
    try:
        if b.get("reset"):
            k = halves_of(default_ms(env))           # back to the start: 0, or the kit's +375 (round 36)
        elif b.get("step") is not None:
            k = cur + int(b.get("step"))
        elif b.get("halves") is not None:
            k = int(b.get("halves"))
        elif b.get("lead_ms") is not None:
            v = float(b.get("lead_ms"))
            if not math.isfinite(v):
                raise ValueError("lead_ms")
            k = halves_of(v)
        else:
            return {"ok": False, "error": "say step, halves, lead_ms or reset", "lip_sync": view(base_ms, home, env)}
    except (TypeError, ValueError):
        return {"ok": False, "error": "step, halves and lead_ms are numbers", "lip_sync": view(base_ms, home, env)}
    lo, hi = reachable(base_ms)
    start = halves_of(default_ms(env))
    held = max(lo, min(hi, k))
    try:
        # the person's own choice, said so (round 17): Setup never replaces it, a 0 included, once there was a value
        # to move from; the start with nothing there before writes nothing (no file = the start: 0, or the kit's)
        had = path(home).exists()
        meta = {"by": "hand", "at": _stamp()} if (held != start or had) else None
        if meta and _stats(_GLASS.get("live")) and glass_follow_on(env):
            meta["glass_ref"] = _stats(_GLASS["live"])      # round 45: the glass the person set it on
        write_ms(ms_of(held), home, meta=meta)
    except OSError as e:
        return {"ok": False, "error": "Lip sync was not saved: %s" % e, "lip_sync": view(base_ms, home, env)}
    out = {"ok": True, "lip_sync": view(base_ms, home, env)}
    if held != k:
        out["note"] = "That is as far as her lines can go here. " + out["lip_sync"]["range"]
    return out


# ---- SETUP MEASURES THIS MACHINE (round 17, the User 2026-10-13: "yes have setup measure the lip sync delay") ----------
# .101 chose -125 ms by eye in round 14 (Razer speakers through crissa_aec_sink); a fresh install starts at 0. Her sheet
# assumes her voice reaches the ear MOUTH_LATENCY_MS after house-speak writes it (scripts/house-speak.py, 90: the house's
# room, measured 2026-09-18 with scripts/mouth-air-latency.py - the room's array read 111 ms, the desk microphone 79 ms,
# and 90 is the array's 111 less ~20 ms of the instrument's own capture buffer, parec --latency-msec=20). Setup's
# her-voice step (mouth_prints in the id module) already plays six known lines through the speakers and records each on
# the room's raw microphone with those same parec flags, so it is the same instrument: the play call's time and the
# recorder's start on one clock, the exact samples sent found in the recording (cross-correlation, phase-weighted so a
# room's echo does not smear the peak), less the same 20 ms. A room like the house's reads 0; sound that reaches the ear
# later than 90 ms moves her lips later by the difference, in the slider's own sign (+ = lips earlier):
#     lead_ms = MOUTH_LATENCY_MS - (heard_ms - CAPTURE_MS)
# A line counts when its peak stands clear of the rest of the search: PEAK_MIN times their RMS (on made-up rooms, RT60
# 0.2-0.9 s, 30 to -10 dB: noise alone and another line read 3.9-4.5; the true line 7-27, exact to the sample, wherever
# it read 7 or more). The samples' plain agreement at that lag (corr) is reported, never a gate: a reverberant room took
# it to 0.05 on a line found exactly. The value is the median of the lines within AGREE_MS of their own median, and it
# takes MIN_LINES of them, else the slider is left as it is. Held to the steps the slider reaches and
# written with its provenance; a value the person set is never replaced, only logged, and a re-run of Setup replaces
# only its own. Nothing is played for it. Kill: CRISSA_SETUP_LIPSYNC_MEASURE=0.
SETUP_FILE = "lip-sync-setup.json"       # the last Setup measurement, whatever came of it (the player's log reads it)
CAPTURE_MS = 20.0
MIN_LINES = 3
AGREE_MS = 30.0
PEAK_MIN = 7.0
OVERLAP_MIN = 0.5                        # at least half the line inside its recording
SEARCH_MS = (-50.0, 800.0)               # play call to microphone; a Bluetooth speaker runs 150-300 ms
BAND_HZ = (200.0, 6000.0)


def measure_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_SETUP_LIPSYNC_MEASURE", "1")).strip().lower() not in ("0", "off", "false", "no")


def _env_ms(env: dict | None, key: str, default: float) -> float:
    e = os.environ if env is None else env
    try:
        v = float(str(e.get(key) or default))
    except ValueError:
        return default
    return v if math.isfinite(v) else default


def assumed_ms(env: dict | None = None) -> float:
    """What her sheet assumes from write to ear: house-speak's MOUTH_LATENCY_MS, the same variable and default."""
    return _env_ms(env, "HOUSE_MOUTH_LATENCY_MS", 90.0)


def capture_ms(env: dict | None = None) -> float:
    return _env_ms(env, "CRISSA_SETUP_LIPSYNC_CAPTURE_MS", CAPTURE_MS)


def _samples(x):
    import numpy as np
    if isinstance(x, (bytes, bytearray, memoryview)):
        b = bytes(x)
        return np.frombuffer(b[: len(b) - (len(b) % 2)], dtype="<i2").astype(np.float64)
    return np.asarray(x, dtype=np.float64).ravel()


def _resample(x, sr_from: int, sr_to: int):
    """To the recording's rate, in the frequency domain: everything above the lower Nyquist dropped, sample 0 kept in
    place (no delay added). No time-domain convolution: a long one runs one BLAS call a sample."""
    import numpy as np
    if sr_from == sr_to or x.size == 0:
        return x
    n_out = int(round(x.size * sr_to / float(sr_from)))
    X = np.fft.rfft(x)
    Y = np.zeros(n_out // 2 + 1, dtype=complex)
    k = min(Y.size, X.size)
    Y[:k] = X[:k]
    return np.fft.irfft(Y, n_out) * (n_out / float(x.size))


def line_delay(sent, sent_sr: int, heard, heard_sr: int, play_after_s: float) -> dict:
    """One line: how long after the play call the exact samples sent reached the microphone.
    sent: the PCM handed to the player (int16 bytes or samples) at sent_sr; heard: its recording at heard_sr;
    play_after_s: the play call's time less the recorder's start (one clock). Returns {"ok", "heard_ms", "peak", "corr",
    "overlap", "why"}; "heard_ms" is the raw reading, before CAPTURE_MS comes off."""
    import numpy as np
    out: dict = {"ok": False, "heard_ms": None, "peak": 0.0, "corr": 0.0, "overlap": 0.0, "why": ""}
    try:
        sent_sr, heard_sr, play_after_s = int(sent_sr), int(heard_sr), float(play_after_s)
        p, h = _samples(sent), _samples(heard)
    except (TypeError, ValueError) as e:
        out["why"] = "unreadable (%s)" % e
        return out
    if sent_sr <= 0 or heard_sr <= 0 or not math.isfinite(play_after_s):
        out["why"] = "no rate or no time"
        return out
    p = _resample(p, sent_sr, heard_sr)
    sr = heard_sr
    if p.size < 0.2 * sr or h.size < 0.2 * sr:
        out["why"] = "too short"
        return out
    p, h = p - p.mean(), h - h.mean()
    n = 1 << int(math.ceil(math.log2(p.size + h.size)))
    H, P = np.fft.rfft(h, n), np.fft.rfft(p, n)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    band = (f >= BAND_HZ[0]) & (f <= min(BAND_HZ[1], 0.45 * sr))
    X = H * np.conj(P)
    mag = np.abs(X)
    if not band.any() or float(mag[band].max()) <= 0.0:
        out["why"] = "silent"
        return out
    W = np.where(band, X / np.maximum(mag, 1e-30), 0.0)
    r = np.fft.irfft(W, n)                                     # r[k]: the sent line starting k samples into the take
    lo = max(int(math.floor((play_after_s + SEARCH_MS[0] / 1000.0) * sr)), -(p.size - 1))
    hi = min(int(math.ceil((play_after_s + SEARCH_MS[1] / 1000.0) * sr)), h.size - 1)
    if hi - lo < int(0.02 * sr):
        out["why"] = "the line is not inside its recording"
        return out
    lags = np.arange(lo, hi + 1)
    vals = np.abs(r[lags % n])
    i = int(np.argmax(vals))
    k = int(lags[i])
    guard = max(1, int(0.003 * sr))
    rest = np.concatenate([vals[: max(0, i - guard)], vals[i + guard + 1:]])
    noise = float(np.sqrt(np.mean(rest ** 2))) if rest.size else 0.0
    peak = float(vals[i]) / noise if noise > 0 else 0.0
    # the samples themselves at that lag, in the same band
    hb, pb = np.fft.irfft(np.where(band, H, 0.0), n)[: h.size], np.fft.irfft(np.where(band, P, 0.0), n)[: p.size]
    a0, b0 = max(0, k), max(0, -k)
    m = max(0, min(h.size - a0, p.size - b0))
    hh, pp = hb[a0:a0 + m], pb[b0:b0 + m]
    den = math.sqrt(float(np.dot(hh, hh)) * float(np.dot(pp, pp))) if m else 0.0
    corr = abs(float(np.dot(hh, pp))) / den if den > 0 else 0.0
    heard_ms = (k / float(sr) - play_after_s) * 1000.0
    out.update(heard_ms=round(heard_ms, 2), peak=round(peak, 2), corr=round(corr, 3), overlap=round(m / float(p.size), 3))
    if peak < PEAK_MIN:
        out["why"] = "no clear peak (%.1f, %.0f needed)" % (peak, PEAK_MIN)
    elif m < OVERLAP_MIN * p.size:
        out["why"] = "only %.0f%% of the line was recorded" % (100.0 * m / p.size)
    elif heard_ms < 0:
        out["why"] = "heard before it was played (%.0f ms): the clocks are off" % heard_ms
    else:
        out["ok"] = True
    return out


def settle(lines: list, env: dict | None = None) -> dict:
    """The lines' one answer: the median of the clear lines that agree, less CAPTURE_MS, or why there is none."""
    lines = [x for x in (lines or []) if isinstance(x, dict)]
    good = [float(x["heard_ms"]) for x in lines if x.get("ok") and x.get("heard_ms") is not None]
    out: dict = {"ok": False, "of": len(lines), "clear": len(good), "lines": 0, "heard_ms": None, "ear_ms": None,
                 "why": ""}
    if len(good) < MIN_LINES:
        whys = sorted({str(x.get("why") or "") for x in lines if not x.get("ok") and x.get("why")})
        out["why"] = "%d of %d lines came back clear (%d needed)%s" % (
            len(good), len(lines), MIN_LINES, ": " + "; ".join(whys)[:200] if whys else "")
        return out
    mid = statistics.median(good)
    kept = [d for d in good if abs(d - mid) <= AGREE_MS]
    if len(kept) < MIN_LINES:
        out["why"] = "the lines disagree (%s ms)" % ", ".join("%.0f" % d for d in sorted(good))
        return out
    heard = statistics.median(kept)
    out.update(ok=True, lines=len(kept), heard_ms=round(heard, 1), ear_ms=round(heard - capture_ms(env), 1),
               spread_ms=round(max(kept) - min(kept), 1))
    return out


def setup_path(home: Path | None = None) -> Path:
    return Path(home or crissa_home.HOME) / SETUP_FILE


def said_ms(ms: float) -> str:
    """A value in plain words: "0 ms", "+21 ms", "-125 ms"."""
    return _signed(ms, " ms") if halves_of(ms) else "0 ms"


# THE GLASS, NOTED BESIDE THE EAR (round 19, .101 2026-10-14: Setup set +20.8 ms, the User's eye wanted +62.5 on the
# same speakers). The measurement times her SOUND only: play call to microphone, against the 90 ms the sheet assumes.
# Her PICTURE's path is not in it - the paint, the Wall's draw and the screen (the glass canary, /v1/glass-lag: the house
# Wall 37.5 ms median on 2026-10-14) - and a slower glass shows her lips later, which wants more lead. Nor is the
# microphone's own capture delay (CAPTURE_MS is a guess) or the start of a new player (her voice's stream is already
# open). Report only: the glass lag the player has at Setup's time is kept in lip-sync-setup.json and said in the log
# line, so the next measurement can be held against the eye. The value set is unchanged.
# Kill: CRISSA_SETUP_LIPSYNC_GLASS=0 (not read, not noted).
# COUNTED SINCE ROUND 26 (.101 2026-10-16: Setup set -83.3 ms, "way late now. like half second"; his eye +291.7): the
# measured sound (ear 182.9) against the 90 assumed is -92.9, and her picture reached the glass 383.9 ms after it was
# picked; with the glass in, 90 - 182.9 + 383.9 = 291.0, 14 half frames = +291.7 - his own value to the step. Round 19's
# miss (+20.8 set, +62.5 by eye) is the same shape, about one glass delay. The glass lag now adds to the value;
# CRISSA_SETUP_LIPSYNC_GLASS_REF_MS takes off what the library's tune already carries (0: none, by .101's eye).
def glass_counts_on(env: dict | None = None) -> bool:
    """Round 26: the picture's own delay to the glass is part of Setup's lip sync (CRISSA_SETUP_LIPSYNC_GLASS_COUNT=0 = round
    17's sound-only value)."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_SETUP_LIPSYNC_GLASS_COUNT", "1")).strip().lower() not in ("0", "off", "false", "no")


def glass_ref_ms(env: dict | None = None) -> float:
    """The picture delay the library's tune already carries (0: .101's eye matched the whole measured delay)."""
    return _env_ms(env, "CRISSA_SETUP_LIPSYNC_GLASS_REF_MS", 0.0)


# THE DESK WALL'S PICTURE PATH THE READING DOES NOT SEE (round 36, .101 round 35 FAIL 3): after her walk-in the re-measure
# read the Tk Wall's glass at 275.1 ms and set +208.3; his eye on the same Wall wanted +375 ("hit well good at these
# timings"), 166.7 ms more. The wall-tk reading stops where the Wall takes the frame: the player's own mouth paint before
# the frame is stamped (composite p50 53 ms on .101), the Wall's film process, X and the screen are not in it. In half
# frames: 8 = 166.7 ms, FITTED to ONE reading of his eye on round 35's code - a number to confirm, not a measurement.
# Round 26's reading on the same reader (glass 383.9, his eye +291.7, older code) needed nothing added: the reading
# itself has moved since (384 -> 275 ms while his eye went +292 -> +375), so no constant fits both, and this one is for
# today's Wall. Only the wall-tk reader takes it. CRISSA_SETUP_LIPSYNC_WALL_TK_EXTRA_MS sets it; 0 = round 35.
WALL_TK_EXTRA_HALVES = 8


def wall_tk_extra_ms(env: dict | None = None) -> float:
    return _env_ms(env, "CRISSA_SETUP_LIPSYNC_WALL_TK_EXTRA_MS", WALL_TK_EXTRA_HALVES * HALF_FRAME_MS)


def glass_note_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_SETUP_LIPSYNC_GLASS", "1")).strip().lower() not in ("0", "off", "false", "no")


def slow_words(total_ms_: float) -> str:
    """What Setup's card and the menu say when the measured path is past the ceiling (the User 2026-10-20)."""
    return ("This display and speaker path is slow (measured %s). Her mouth may start a little late on each line. "
            "A wired speaker, or your TV's game mode, usually fixes it." % _signed(float(total_ms_), " ms"))


def from_setup(res: dict, home: Path | None = None, base_ms: float | None = None, env: dict | None = None,
               now: float | None = None, glass: dict | None = None, lands: bool = False) -> dict:
    """What Setup does with its measurement (settle()'s answer): set the slider when it is unset or Setup's own, keep
    the person's own, leave it when there is no answer. Writes <home>/lip-sync.json only to set it, and the outcome to
    <home>/lip-sync-setup.json every time. base_ms (the library's own lead, as the player has it) holds the value to the
    steps the slider reaches; without it, to the slider's cap. The house is never touched.
    Returns {"outcome": set|kept|none|off|house, "line": the card's words, "log": one sentence for Setup's log, ...}."""
    res = dict(res or {})
    when = time.time() if now is None else float(now)
    rep: dict = {"at": when, "date": time.strftime("%Y-%m-%d", time.localtime(when)), "outcome": "none",
                 "line": "", "log": "", "heard_ms": res.get("heard_ms"), "ear_ms": res.get("ear_ms"),
                 "lines": res.get("lines") or 0, "of": res.get("of") or 0, "assumed_ms": assumed_ms(env),
                 "capture_ms": capture_ms(env)}
    if isinstance(glass, dict) and glass.get("median_ms") is not None and glass_note_on(env):
        rep["glass"] = {"who": str(glass.get("who") or "glass")[:40], "median_ms": glass.get("median_ms")}
    if crissa_home.is_house():
        rep.update(outcome="house", log="Lip sync: the house keeps its own timing; nothing written.")
        return rep
    if not measure_on(env):
        rep.update(outcome="off", log="Lip sync not measured (CRISSA_SETUP_LIPSYNC_MEASURE=0).")
        return rep
    now_ms = offset_ms(home, env)
    rec = record(home)
    if not res.get("ok"):
        why = str(res.get("why") or "no measurement")
        rep.update(outcome="none", why=why, line="",
                   log="Lip sync not changed: Setup could not measure these speakers (%s); it stays at %s."
                   % (why, said_ms(now_ms)))
    else:
        want = assumed_ms(env) - float(res["ear_ms"])
        if rep.get("glass") and glass_counts_on(env):
            # round 26 (.101 10-16: Setup set -83.3, "way late now. like half second", his eye +291.7): her picture
            # reaches the glass glass_ms after it is picked, so her lips must be picked that much earlier;
            # 90 - 182.9 + 383.9 = 291.0 -> 14 half frames = +291.7, his own value to the step
            want += float(rep["glass"]["median_ms"]) - glass_ref_ms(env)
            rep["glass_counted"] = True
            if rep["glass"].get("who") == "wall-tk" and wall_tk_extra_ms(env):
                want += wall_tk_extra_ms(env)              # round 36: the desk Wall's path past its reading
                rep["wall_tk_extra_ms"] = round(wall_tk_extra_ms(env), 1)
        if base_ms is not None and math.isfinite(float(base_ms)):
            lo, hi = reachable(float(base_ms))
        else:
            lo, hi = -int(CAP_MS // HALF_FRAME_MS), int(CAP_MS // HALF_FRAME_MS)
        k = max(lo, min(hi, halves_of(want)))
        ms = ms_of(k)
        rep.update(measured_ms=round(want, 1), halves=k, lead_ms=ms)
        if base_ms is not None and math.isfinite(float(base_ms)):
            rep["base_ms"] = round(float(base_ms), 3)       # round 36: the walk-in's re-measure reuses it (reglass)
        how = ("her voice reached the microphone %s ms after it was played, on %d of %d lines; her mouth assumes %s ms"
               % (_num(res["ear_ms"]), rep["lines"], rep["of"], _num(assumed_ms(env))))
        if rep.get("glass"):
            how += "; her picture reached the glass %s ms after it was picked (%s)" % (
                _num(rep["glass"]["median_ms"]), rep["glass"]["who"])
            if rep.get("wall_tk_extra_ms"):
                how += ", and %s ms for the desk Wall's paint and screen" % _num(rep["wall_tk_extra_ms"])
        slow_ms = (float(base_ms) + want) if base_ms is not None and math.isfinite(float(base_ms)) else None
        landed = (lands and glass_lands_on(env) and not setup_writes_on(env) and on(env)
                  and not (rec and rec.get("by") != "setup") and not (slow_ms is not None and slow_ms > LINE_MAX_MS))
        if landed:
            rep["landed"] = True                            # round 42 (R41-3): the glass measure is the one kept
        if not setup_writes_on(env) and not landed:
            # the kit (2026-10-20): Setup reports, the slider keeps its value (the kit's start or the person's own)
            rep.update(outcome="measured", lead_ms=round(now_ms, 3), suggest_ms=ms,
                       line="Lip sync kept at %s; Setup measured %s for these speakers." % (said_ms(now_ms),
                                                                                           said_ms(ms)))
            rep["log"] = "lip sync: Setup measured %s, keeping %s (%s)" % (said_ms(ms), said_ms(now_ms), how)
            if base_ms is not None and math.isfinite(float(base_ms)) and float(base_ms) + want > LINE_MAX_MS:
                # the User 2026-10-20 ("keep the 600 ceiling"): a path slower than her lines can carry is said once,
                # plainly; nothing exits, nothing blocks Setup, the dial stays where it is
                rep["slow"] = True
                rep["slow_ms"] = round(float(base_ms) + want, 1)
                rep["line"] += " " + slow_words(rep["slow_ms"])
                rep["log"] += "; slow path: %s ms in total is past the %d ms her lines carry" % (
                    _num(rep["slow_ms"]), LINE_MAX_MS)
        elif rec and rec.get("by") != "setup":
            rep.update(outcome="kept", lead_ms=round(now_ms, 3),
                       line="Lip sync kept at your own %s; Setup measured %s for these speakers." % (
                           said_ms(now_ms), said_ms(ms)))
            rep["log"] = "%s (%s)" % (rep["line"][:-1], how)
        elif not on(env):
            rep.update(outcome="off", log="Lip sync is off on this computer (CRISSA_LIPSYNC=0); Setup measured %s for "
                                          "these speakers (%s)." % (said_ms(ms), how))
        else:
            try:
                meta = {"by": "setup", "heard_ms": res.get("heard_ms"), "ear_ms": res.get("ear_ms"),
                        "lines": rep["lines"], "date": rep["date"], "at": _stamp(when)}
                if rep.get("glass_counted") and rep["glass"].get("who") == GLASS_WHO and _stats(rep["glass"]):
                    meta["glass_ref"] = _stats(rep["glass"])    # round 45: the glass the value was measured on
                    win = glass_window(_GLASS.get("measure_from")) if ref_window_on(env) else {}
                    if _stats(win):                              # the hotfix: the measure's window, not one reading
                        meta["glass_ref"] = dict(_stats(win), n=win["n"], window=True)
                write_ms(ms, home, meta=meta)
            except OSError as e:
                rep.update(outcome="none", why=str(e),
                           log="Lip sync not changed: it could not be saved (%s); it stays at %s." % (e, said_ms(now_ms)))
            else:
                rep.update(outcome="set", line="Lip sync set to %s for these speakers." % said_ms(ms))
                rep["log"] = "%s (%s)" % (rep["line"][:-1], how)
    try:
        _write_json(setup_path(home), rep)
    except OSError:
        pass
    return rep


# THE GLASS AS SHE WILL BE SEEN (round 35, .101 round 33: Setup set 0 ms twice, his eye +375). Setup's own sample of the
# glass lag is taken while the Wall sits behind Setup's window: wall-tk 106.7 ms, where the full Wall measured 384 ms in
# round 26, so 90 - 195.2 + 106.7 rounded to 0. Once she has walked in and Setup's window has closed, Init asks again
# (reglass): the same ear measurement, the glass she is seen on now. It sets the slider only where Setup set it (the
# person's own value is kept, as from_setup keeps it). Kill CRISSA_LIPSYNC_FULL_WALL=0 (Setup's own sample stands).
def full_wall_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_LIPSYNC_FULL_WALL", "1")).strip().lower() not in ("0", "off", "false", "no")


def reglass(glass: dict | None, home: Path | None = None, base_ms: float | None = None, env: dict | None = None,
            now: float | None = None) -> dict:
    """Setup's value again with `glass` ({"who", "median_ms"}) for the ear Setup measured. {"outcome": ...} like
    from_setup, plus "reglass": {"was", "now"}; outcome "none" with no Setup measurement to redo or no glass."""
    last = setup_note(home)
    if not full_wall_on(env):
        return {"outcome": "off"}
    if not last or last.get("ear_ms") is None or last.get("outcome") not in ("set", "kept", "measured"):
        # round 45 (.101 round 44: "no Setup measurement to redo" said nothing of why): Setup's own outcome with it
        return {"outcome": "none", "why": "no Setup measurement to redo (%s)" % (
            "Setup recorded none: %s" % SETUP_FILE if not last else "Setup's was %s: %s" % (
                last.get("outcome") or "?", last.get("why") or last.get("log") or "no reason kept"))}
    if not isinstance(glass, dict) or glass.get("median_ms") is None:
        return {"outcome": "none", "why": "no glass reading now"}
    was = (last.get("glass") or {}).get("median_ms")
    res = {"ok": True, "ear_ms": last["ear_ms"], "heard_ms": last.get("heard_ms"), "lines": last.get("lines") or 0,
           "of": last.get("of") or 0}
    if base_ms is None and last.get("base_ms") is not None:
        base_ms = float(last["base_ms"])                    # round 36: the library lead Setup measured against
    rep = from_setup(res, home=home, base_ms=base_ms, env=env, now=now, glass=glass, lands=True)
    rep["reglass"] = {"was": was, "now": glass.get("median_ms"), "who": glass.get("who")}
    return rep


def setup_note(home: Path | None = None, since: float | None = None) -> dict:
    """The last Setup measurement (lip-sync-setup.json), or {} when there is none since `since`."""
    try:
        d = json.loads(setup_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    if since is not None:
        try:
            if float(d.get("at") or 0) < float(since):
                return {}
        except (TypeError, ValueError):
            return {}
    return d
