"""Her pictures for this screen, as Setup, the Control Panel and an import see them (round 14, 2026-10-12).

The desk Wall builds them at idle on this machine (lib/wall_ascii_disk.py, the Wall package) and writes
<state home>/cache/ascii/<framing>/progress.json; this module only reads that, and writes the one file an import uses to
ask for a build. It stays in the player package so Setup (lib/crissa_init.py), the import (lib/crissa_import.py) and the
Control Panel read it without reaching into the Wall's code.

The User 2026-10-12: "yes in background even better. If they fly through and not done then give that splash": the build
runs silently from the start of Setup; build_view()'s `line` (the splash, her name from crissa_home.her_name) is set only
while her listen and hub loops are not built yet, and Setup shows it only once it has ended; the Control Panel shows the
numbers (`note`).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import crissa_home

GB = 1 << 30


def root(home=None) -> Path:
    """The disk tier's root: HOUSE_WALL_ASCII_DISK_DIR when it is set (the Wall's own override), else the state home's."""
    if home is None and os.environ.get("HOUSE_WALL_ASCII_DISK_DIR"):
        return Path(os.environ["HOUSE_WALL_ASCII_DISK_DIR"])
    return Path(home or crissa_home.HOME) / "cache" / "ascii"


def build_view(home=None, now=None) -> dict:
    """The build for this screen, as people read it, from the newest <framing>/progress.json. The User 2026-10-12: "yes
    in background even better. If they fly through and not done then give that splash": it builds silently from the
    start of Setup; `line` (the splash) is set only while her listen and hub loops are not built yet, and the reader
    shows it only when Setup has ended (her first start); the Control Panel shows the numbers (`note`).
    {running, pct, built, total, disk_used, disk_cap, stopped, loops_done, line, note}."""
    now = time.time() if now is None else now
    root_ = root(home)
    best = None
    try:
        for d in os.scandir(root_):
            if d.is_dir():
                try:
                    st = json.loads((Path(d.path) / "progress.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if best is None or float(st.get("at") or 0) > float(best.get("at") or 0):
                    best = st
    except OSError:
        best = None
    mouths = mouths_view(home, now)
    if not best:
        return {"running": False, "pct": None, "line": "", "note": "", "lap": last_lap(home, now), "mouths": mouths}
    total, built = int(best.get("total") or 0), int(best.get("built") or 0)
    pct = int(100 * built / total) if total else 100
    running = not best.get("done") and not best.get("stopped") and now - float(best.get("at") or 0) < 30.0
    try:
        name = crissa_home.her_name()
    except Exception:  # noqa: BLE001
        name = "Crissa"
    loops_done = bool(best.get("loops_done")) or bool(best.get("done"))
    line = ("Give me a few minutes to build %s for your screen: %d %%." % (name, pct)) \
        if (running and not loops_done) else ""
    gb = lambda b: (float(b or 0) / GB)                                   # noqa: E731
    note = "%d %% - %d of %d pictures, disk %.1f of %.0f GB%s%s" % (
        pct, built, total, gb(best.get("disk_used")), gb(best.get("disk_cap")),
        " (compressed)" if str(best.get("codec") or "raw") != "raw" else "",
        " - limited by the disk budget; the rest build the first time they play" if best.get("stopped") else
        (" - done" if best.get("done") else ""))
    return {"running": running, "pct": pct, "built": built, "total": total, "disk_used": best.get("disk_used"),
            "disk_cap": best.get("disk_cap"), "stopped": best.get("stopped") or "", "line": line, "note": note,
            "done": bool(best.get("done")), "loops_done": loops_done, "name": name,
            "loops_total": best.get("loops_total"), "loops_built": best.get("loops_built"),
            "loops_counted": bool(best.get("loops_counted")), "rate": best.get("rate"), "rate_run": best.get("rate_run"),
            "rate_worked": best.get("rate_worked"),
            "workers_now": best.get("workers_now"), "cover": bool(best.get("cover")),
            "lap": last_lap(home, now), "mouths": mouths}


def _alive(pid) -> bool:
    """The build's own process is still there (it is held, not gone, while she speaks)."""
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def mouths_view(home=None, now=None) -> dict | None:
    """Her painted mouths for this library, built beside the pictures (the User 2026-10-24: "every painted patch for
    each loop frame and tile could be built ahead <- we do this for all ascii frames too at first install too. Add it
    there"): the conductor's own build (lib/mouth_patch_disk) writes <state home>/cache/mouth-patch/<code>/progress.json.
    {running, pct, done, stopped, jobs, of, note} or None when no build has run. Shown as numbers only; the splash and
    the plate stay the pictures' (their timing was tuned on .101)."""
    try:
        import mouth_patch_disk
        st = mouth_patch_disk.progress_view(home)
    except Exception:  # noqa: BLE001 - no mouth build here
        st = None
    if not st:
        return None
    of, jobs = int(st.get("total") or 0), int(st.get("jobs_done") or 0)
    pct = int(100 * jobs / of) if of else 100
    running = not st.get("done") and not st.get("stopped") and _alive(st.get("pid"))
    note = "mouths %d %% - %d of %d frame x class, %.2f GB%s" % (
        pct, jobs, of, float(st.get("disk_used") or 0) / GB,
        " - limited by the disk budget; the rest are painted the first time they play" if st.get("stopped") else
        (" - done" if st.get("done") else ""))
    return {"running": running, "pct": pct, "done": bool(st.get("done")), "stopped": st.get("stopped") or "",
            "jobs": jobs, "of": of, "note": note}


# THE PLATE STAYS WHILE SHE IS JUMPY (round 29, the User 2026-10-17 on .101: "I like to let them know if it is jumpy,
# that is why with that bar"; after a restart the plate said 1,182 of 1,183 and went at once while the Wall still drew
# fresh pictures - listen's lap fresh 14, then 7 - at 18-22 of 24 a second). The desk Wall writes each finished lap
# (<root>/lap.json: clip, pictures, fresh, seconds, at); the plate shows while the newest lap, under LAP_FRESH_S old,
# drew any picture fresh. Kill CRISSA_PLATE_WHILE_JUMPY=0 (round 28: the disk count alone).
LAP_FRESH_S = 30.0

# .101 10-19 (the User: "i am still getting the loading bar on her last one", 4 of the last 145 pictures drawn fresh at
# 97%, long after her loops were built): a built Wall still draws a few pictures fresh at each clip switch, yet shows
# every film frame on time (work laps 120 pictures in 5.0 s, 120 frames: fresh 1-11), so "any fresh" kept the plate up
# for good. Jumpy is a lap that SKIPPED film frames (cold laps: listen 72 pictures in 3.9 s; flourish-notes 141 of 145,
# fresh 46). The Wall writes each lap's skipped frames; the plate shows while the newest lap drew fresh AND skipped.
# A lap.json without `skipped` (an older Wall) reads as before. Kill CRISSA_PLATE_JUMPY_SKIPS=0 (any fresh, round 29).
def plate_jumpy_skips_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PLATE_JUMPY_SKIPS", "1")).strip().lower() not in ("0", "false", "no", "off")


def plate_jumpy_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PLATE_WHILE_JUMPY", "1")).strip().lower() not in ("0", "false", "no", "off")


# THE PLATE SAYS WHEN THE CACHE IS OFF OR FULL (round 40 N16; .101 round 38 ~10:36: "ascii cache: MISMATCH - a region
# answer differed from the fresh render at 3440x1440; the cache is off until the wall restarts", and from then every
# picture was drawn fresh, so the plate read "Building Crissa... 0 %" for good; before it the memory tier was full,
# 6.5 GB of 6.5 GB, evicting). A cache the Wall switched off is not a build: the plate says "Picture cache off" and that
# a restart of the Wall turns it back on, with no bar. A memory tier at its cap draws the pictures it evicted again;
# that is not a build either, so a full cache shows no jumpy plate (the build's own count still shows while her loops
# are being built). The lap record carries both from the Wall's cache counts. Kill CRISSA_PLATE_CACHE_STATE=0.
FULL_SHARE = 0.97


def plate_cache_state_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PLATE_CACHE_STATE", "1")).strip().lower() not in ("0", "false", "no", "off")


# A RESTART THAT DID NOT HELP IS NOT OFFERED AGAIN (.101 round 46 O4, 2026-10-24: the check failed again 2 min after the
# User restarted the Wall, and the plate still said "restart the Wall to turn it back on"). cache-off.json counts the
# Wall starts in a row whose cache went off (one per Wall process); a start whose laps run with the cache on clears it.
# From the second such start the plate says the Wall is fine and drawing every picture itself. Kill
# CRISSA_PLATE_OFF_STARTS=0 (the plate offers a restart every time, as before).
def _off_starts(d: Path, off: bool) -> int:
    f = d / "cache-off.json"
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = {}
    pids = [int(p) for p in rec.get("pids") or [] if str(p).isdigit()]
    if not off:
        if pids:
            try:
                f.unlink()
            except OSError:
                pass
        return 0
    if os.getpid() not in pids:
        pids = (pids + [os.getpid()])[-8:]
        tmp = d / "cache-off.json.tmp"
        tmp.write_text(json.dumps({"pids": pids, "at": time.time()}), encoding="utf-8")
        os.replace(tmp, f)
    return len(pids)


def note_lap(clip, n: int, fresh: int, secs: float, home=None, now=None, skipped=None, cache_off: str = "",
             full: bool = False) -> None:
    """The desk Wall's finished lap (a pack only; the house writes nothing)."""
    try:
        if crissa_home.is_house():
            return
        d = root(home)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "lap.json.tmp"
        rec = {"clip": str(clip), "n": int(n), "fresh": int(fresh), "secs": round(float(secs), 2),
               "at": time.time() if now is None else now}
        if skipped is not None:
            rec["skipped"] = int(skipped)
        if cache_off:
            rec["cache_off"] = str(cache_off)[:200]
        starts = _off_starts(d, bool(cache_off))
        if starts:
            rec["cache_off_starts"] = starts
        if full:
            rec["full"] = True
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        os.replace(tmp, d / "lap.json")
    except Exception:  # noqa: BLE001  the plate is a courtesy; the Wall never stops for it
        pass


def last_lap(home=None, now=None) -> dict:
    """The newest lap when it is under LAP_FRESH_S old, else {}."""
    try:
        d = json.loads((root(home) / "lap.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    now = time.time() if now is None else now
    return d if isinstance(d, dict) and now - float(d.get("at") or 0) < LAP_FRESH_S else {}


def under_cover(home=None) -> bool:
    """Setup is running (<state home>/init-run.json status "running"): its pages cover the glass, so the build may use
    every worker (round 15, 2026-10-12). Unreadable = not covered."""
    try:
        st = json.loads((Path(home or crissa_home.HOME) / "init-run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(st, dict) and st.get("status") == "running"


# THE FIRST BUILD WAITS FOR THE PICK, FROM THE START (round 17, .101 2026-10-13 on round 16: the build started at density 1
# at 04:00 under Setup's cover, "4 of 4 workers", wrote 14 GB, and was removed once the card picked 2.0). Two holes in
# round 16's wait: (1) the desk Wall's first picture starts the build before Setup's page has started the walk, and with
# no init-run.json yet nothing was pending; (2) Allow on the machine card writes machine.json {"permission": "allow",
# "measuring": true} before the check has measured anything, and a permission counted as an answer, so the build began
# at Allow while the density was still being timed (and ran beside the card's own timings). Now: a pack's first boot
# whose walk has not started yet (Init's switch CRISSA_INIT=1, no init.json marker) waits too, for at most
# START_GRACE_S (the prefill's own bound: a page that never opens builds after it); and a check still measuring is no
# answer (a measuring record older than MEASURING_MAX_S is: a check that broke never holds the build for good).
# Kill: HOUSE_WALL_ASCII_PREFILL_WAIT_FIRST_BOOT=0 (round 16: only a running walk with no card record waits).
START_GRACE_S = float(os.environ.get("HOUSE_WALL_ASCII_PREFILL_WAIT_START_S", "120") or 0)
MEASURING_MAX_S = 900.0


def first_boot_wait_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("HOUSE_WALL_ASCII_PREFILL_WAIT_FIRST_BOOT", "1")).strip().lower() not in ("0", "false", "no", "off")


def _card_picked(h: Path) -> bool:
    """A density-pick.json the machine card wrote (round 29, .101: the desk Wall's first look writes one too, `by`
    "wall", which is not the card's answer: the build still waits for the card)."""
    try:
        d = json.loads((h / "density-pick.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (h / "density-pick.json").exists()
    return not (isinstance(d, dict) and d.get("by") == "wall")


def density_wait(home=None, env=None, now=None) -> str:
    """Why the build waits for the density pick now: "card" (a first Setup is running and its machine card has not
    answered), "start" (a pack's first boot whose walk has not started yet, round 17), or "" (no wait)."""
    e = os.environ if env is None else env
    h = Path(home or crissa_home.HOME)
    strict = first_boot_wait_on(e)
    try:
        st = json.loads((h / "init-run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = None
    if not isinstance(st, dict) or st.get("status") != "running":
        if strict and not isinstance(st, dict) and str(e.get("CRISSA_INIT") or "").strip() == "1" \
                and not (h / "init.json").exists() and not _card_picked(h) \
                and not crissa_home.is_house():
            return "start"                     # Init leads this first boot, and its page has not started the walk
        return ""
    if st.get("only"):
        return ""
    if _card_picked(h):
        return ""
    try:
        m = json.loads((h / "machine.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "card"
    if not isinstance(m, dict):
        return "card"
    if strict and m.get("measuring") and not m.get("tier") and not m.get("skipped"):
        try:
            age = (time.time() if now is None else now) - (h / "machine.json").stat().st_mtime
        except OSError:
            age = 0.0
        return "card" if age < MEASURING_MAX_S else ""
    return "" if (m.get("permission") or m.get("skipped") or m.get("tier")) else "card"


def density_pending(home=None, env=None) -> bool:
    """A first Setup is running and its machine card has not answered yet (no machine.json with a permission, no
    density-pick.json): the density her pictures are built at is about to be picked, so the build waits for it rather
    than build a look the glass is about to leave (round 16, 2026-10-13: .101 built 13,976 pictures at 1.0 behind
    Setup and then picked 3.0). A Setup run again for one routine (`only`) never waits. Unreadable = not waiting.
    Round 17: also a first boot whose walk has not started yet, and a card still measuring (density_wait)."""
    return bool(density_wait(home, env))


# THE TIME LEFT FROM THE RUN'S OWN RATE (round 45, .101 round 44: "1,428 of 1,741 · less than a minute left", then 1,697
# of 1,741 still "less than a minute" - "does not seem to finish as fast as it claims"): the rate was the last 24 s
# only, and a window measured under Setup's cover (all workers) or between her lines (no pauses) promised too soon.
# The build also writes the run's rate since its workers last changed, pauses included (wall_ascii_disk progress
# `rate_run`); the plate takes the slower of the two. Kill CRISSA_PLATE_RUN_RATE=0 (the recent rate, round 44).
def plate_run_rate_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PLATE_RUN_RATE", "1")).strip().lower() not in ("0", "false", "no", "off")


# A STALL NEVER SETS THE TIME LEFT (round 46, .101 round 45: "about 374 min left" at 25 %, then 7 min): the slower of
# the two let a near-zero recent rate win whenever the build stood still under her lines. The recent rate counts only
# when its window held work (pictures finished, no pause - the build's `rate_worked`); otherwise the run's rate.
# Kill CRISSA_PLATE_RATE_WORK=0 (round 45: the slower of the two).
def plate_rate_work_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PLATE_RATE_WORK", "1")).strip().lower() not in ("0", "false", "no", "off")


def plate_rate(b: dict, env=None):
    """The rate the time left is read at (A STALL NEVER SETS THE TIME LEFT; round 45's rule under the kill)."""
    if plate_run_rate_on(env) and plate_rate_work_on(env) and b.get("rate_worked") is not None:
        for k in (("rate", "rate_run") if b.get("rate_worked") else ("rate_run",)):
            try:
                if b.get(k) is not None and float(b[k]) > 0:
                    return float(b[k])
            except (TypeError, ValueError):
                pass
        return None
    rates = []
    for k in (("rate", "rate_run") if plate_run_rate_on(env) else ("rate",)):
        try:
            if b.get(k) is not None and float(b[k]) > 0:
                rates.append(float(b[k]))
        except (TypeError, ValueError):
            pass
    return min(rates) if rates else None


# THE PLATE FOLLOWS DROPPED FRAMES (round 45, .101 round 44: an hour on, "2 of the last 130 pictures drawn fresh 98%" -
# the User: "not done loading an hour into it?"): a built Wall still drew a picture or two fresh on a clip's first play
# and skipped a frame, and any skip brought the plate back. It shows now only while a lap skipped at least
# PLATE_DROP_SHARE of its film frames (skipped / (pictures + skipped)). Measured on .101's laps: a cold lap skips
# about a quarter (listen 72 pictures of ~96 frames in 3.9 s), the cache-off lap 40 of 564 (7 %), a built one at most
# a frame or two of 120-145 (1-2 %, the glass at 23.5 of 24 a second); 5 % sits above the built laps and below every
# hurt one. Dial CRISSA_PLATE_DROP_SHARE (0 = any skip, round 44).
PLATE_DROP_SHARE = 0.05


def plate_drop_share(env=None) -> float:
    e = os.environ if env is None else env
    try:
        v = float(str(e.get("CRISSA_PLATE_DROP_SHARE", PLATE_DROP_SHARE)).strip())
    except ValueError:
        return PLATE_DROP_SHARE
    return max(0.0, min(1.0, v))


def _left(n: int, rate) -> str:
    """'about 3 min left' for n pictures at rate a second; '' with no rate yet."""
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return ""
    if r <= 0 or n <= 0:
        return ""
    s = n / r
    if s < 50:
        return "less than a minute left"
    return "about %d min left" % max(1, int(round(s / 60.0)))


def plate_view(b: dict) -> dict:
    """THE BUILDING PLATE ON THE GLASS (round 15, 2026-10-12, the User: "needs a popup building crissa"): while a build
    for this screen runs and her loops are not built yet, the desk Wall shows her name, the loops' pictures built of
    their total and a time left; it goes away when the loops are built (the rest goes on silently). The Wall does not
    show it while Setup runs (Setup's own splash rule stands). {show, title, line}."""
    lt, lb = int(b.get("loops_total") or 0), int(b.get("loops_built") or 0)
    name = b.get("name") or "Crissa"
    lap = b.get("lap") or {}
    if lap.get("cache_off") and plate_cache_state_on():
        again = (int(lap.get("cache_off_starts") or 0) >= 2
                 and str(os.environ.get("CRISSA_PLATE_OFF_STARTS", "1")).strip().lower() not in ("0", "false", "no", "off"))
        return {"show": True, "title": "Picture cache off", "off": True, "done": 0.0,
                "line": ("the Wall is fine and draws every picture itself; its log has the details for a report"
                         if again else "a check failed: restart the Wall to turn it back on")}
    if not b.get("running") or b.get("loops_done") or lb >= lt:
        jumpy = int(lap.get("fresh") or 0) > 0
        if jumpy and lap.get("full") and plate_cache_state_on():
            jumpy = False                    # a full memory tier draws again what it evicted: not a build
        if jumpy and plate_jumpy_skips_on() and lap.get("skipped") is not None:
            sk, share = int(lap["skipped"]), plate_drop_share()
            span = int(lap.get("n") or 0) + sk
            jumpy = sk > 0 and (share <= 0 or (span > 0 and sk / span >= share))
        if plate_jumpy_on() and jumpy:
            n = int(lap.get("n") or 0)
            return {"show": True, "title": "Building %s\u2026" % name, "jumpy": True,
                    "done": (max(0, n - int(lap["fresh"])) / n) if n else 0.0,
                    "line": "%d of the last %d pictures drawn fresh" % (int(lap["fresh"]), n)}
        return {"show": False}       # (a run with nothing to draw for her loops - the ten-minute re-ask - shows nothing)
    if lt and b.get("loops_counted"):
        left = _left(lt - lb, plate_rate(b))
        line = "%s of %s pictures%s" % (format(lb, ","), format(lt, ","), (" \u00b7 " + left) if left else "")
    else:
        line = "counting her pictures"
    return {"show": True, "title": "Building %s\u2026" % name, "line": line,
            "done": (lb / lt) if (lt and b.get("loops_counted")) else 0.0}


def request_prefill(clips=(), home=None) -> None:
    """Ask the Wall for a pre-fill run (a pack just landed: its clips are new on this screen). The Wall starts it at its
    next lap; existing pictures are skipped, so only the new clips are drawn."""
    d = root(home)
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / ".prefill.request.tmp"
        tmp.write_text(json.dumps({"at": time.time(), "clips": list(clips)}))
        os.replace(tmp, d / "prefill.request")
    except OSError:
        pass
