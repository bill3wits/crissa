"""Which glass owns the desk: wallpaper, Crissa's Brave, or neither.

Two processes, one SoT for the picture (:8100 frames). They are not the same
script:

  wallpaper  scripts/house-wall-ascii  →  house-wall-ascii.service  (Tk ASCII)
  brave      scripts/crissa-display-run  →  crissa-display.service      (Brave kiosk)

Law (the User 2026-09-08 am): wallpaper ON disables crissa-display autostart;
wallpaper OFF enables+starts crissa-display. Mode off = neither autostarts.

THE TOGGLE IS EITHER-OR (the User 2026-09-08 pm, superseding the morning's
"never stop crissa-display from this module"): "make that [Brave] the window
that opens the Wall page when this ZOS Wall is toggled off. Not both at once
unless User does it." Wall off → Brave up with the Wall page (hand_to_brave).
Wall on → Brave goes, IF Brave was the glass (mode was `brave`, or the hand
back came from Brave's own menu: hand_to_wallpaper). A Brave the User opened
as a tool window while the wallpaper was up (Krew / Composer / Panel keys,
ensure_brave_for_tools — mode stays `wallpaper`) is the User's and stays.

Oracle is not a third glass. Both clients read :8191 presence; the VL oracle
lives in rail-presence, not in these units. oracle_dot is the clock collapse,
a shared pref, not a second feed.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import crissa_home  # the state directory, resolved once (2026-09-26)
import crissa_egress  # LAN-only when kill-search is on

WALL = "house-wall-ascii.service"
BRAVE = "crissa-display.service"
MODES = ("wallpaper", "brave", "off")
PATH = crissa_home.HOME / "house-glass-mode"
CDP = "http://127.0.0.1:9222/json/version"


def _ctl(*args: str, timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _pack_lacks(unit: str) -> bool:
    """A PACK HAS NO HOUSE GLASS UNITS (round 15, 2026-10-12, .101: `systemd --user` at 50 % with her silent). With no
    mode file - a pack never writes one - read_mode() asked systemd four times (is-active and is-enabled on
    house-wall-ascii.service and crissa-display.service) on every /v1/house the desk Wall polls, and a pack has neither
    unit, so the manager loaded each missing name and dropped it again: 44 + 44 times in 25 s (`busctl monitor`,
    UnitNew / UnitRemoved), after round 14 stopped the feed's own two questions. In a pack both answers were always
    "no"; they are given without asking. The house asks as before. CRISSA_GLASS_ASK_UNITS=1 asks in a pack too."""
    if unit not in (WALL, BRAVE) or crissa_home.is_house():
        return False
    return str(os.environ.get("CRISSA_GLASS_ASK_UNITS", "")).strip().lower() not in ("1", "on", "true", "yes")


def is_active(unit: str) -> bool:
    if _pack_lacks(unit):
        return False
    try:
        return _ctl("is-active", unit, timeout=5).stdout.strip() == "active"
    except Exception:
        return False


def is_enabled(unit: str) -> bool:
    if _pack_lacks(unit):
        return False
    try:
        return _ctl("is-enabled", unit, timeout=5).stdout.strip() == "enabled"
    except Exception:
        return False


def read_mode() -> str:
    try:
        raw = PATH.read_text(encoding="utf-8").strip().split()[0]
    except (OSError, IndexError):
        raw = ""
    if raw in MODES:
        return raw
    if is_active(WALL) or is_enabled(WALL):
        return "wallpaper"
    if is_active(BRAVE) or is_enabled(BRAVE):
        return "brave"
    return "off"


def write_mode(mode: str, note: str = "") -> None:
    """The mode file: one word, plus an optional note the next reader acts
    on once (read_mode only ever sees the first word)."""
    if mode not in MODES:
        raise ValueError(mode)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text((mode + (" " + note if note else "")) + "\n", encoding="utf-8")
    tmp.replace(PATH)


def read_raw() -> str:
    try:
        return PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def status() -> dict:
    return {
        "mode": read_mode(),
        "wall": {"unit": WALL, "active": is_active(WALL), "enabled": is_enabled(WALL)},
        "brave": {"unit": BRAVE, "active": is_active(BRAVE), "enabled": is_enabled(BRAVE)},
    }


def _zos_background() -> bool:
    """lib/zos_brave.py's switch (OFF by default): ZOS Brave stays in the background under the wallpaper."""
    try:
        import zos_brave
        return zos_brave.enabled()
    except Exception:  # noqa: BLE001
        return False


def _zos_quit() -> bool:
    try:
        import zos_brave
        return zos_brave.quit_requested()
    except Exception:  # noqa: BLE001
        return False


def _enable(unit: str) -> None:
    _ctl("enable", unit)


def _disable(unit: str) -> None:
    _ctl("disable", unit)


def _start(unit: str) -> None:
    _ctl("start", unit)


def _stop(unit: str, *, async_: bool = False) -> None:
    # Explicit stop: Restart=always does not bring the unit back.
    if async_:
        subprocess.Popen(["systemctl", "--user", "stop", unit])
        return
    _ctl("stop", unit)


# THE WINDOW STATE IS DERIVED, NOT CHOSEN (the User 2026-09-16: "Literally no such
# thing as Hybrid. Only Wallpaper ON or OFF").
#
# <home>/voice-only is what tells crissa-display-run and mode_orchestrate
# whether Brave is fullscreen-with-no-chrome or a windowed tools window. It
# used to be set by two buttons called Voice-Only and Hybrid, which offered a
# choice that does not exist: if Brave IS the glass it has to be fullscreen,
# and if the wallpaper is the glass then Brave is a tools window. So the flag
# follows the glass here, in the one function that changes the glass, and the
# buttons are gone. Nothing else needs to agree with anything.
VOICE_ONLY_FLAG = crissa_home.HOME / "voice-only"


def _window_state_follows(mode: str) -> None:
    """Brave is a WINDOW in every glass — never fullscreen, never maximized.

    the User 2026-09-16 15:4x: "Crissa OS Brave is trapping with auto-fullscreen grab
    insistance. I see nothing behind it and can't keep it down … We do not want
    that when Wall is off. We want it kept open no maximized. Never!" The 11:58
    derivation wrote the voice-only flag for mode `brave`, and voiceonly-home's
    poll re-asserts fullscreen whenever that flag exists — so every un-fullscreen
    snapped back. Every glass now clears it; nothing in the house writes it.
    """
    del mode
    try:
        VOICE_ONLY_FLAG.unlink(missing_ok=True)
    except OSError:
        pass      # the flag is a hint; a read-only home must not block the glass


def apply(mode: str, *, self_unit: str | None = None) -> dict:
    """Write autostart. Wallpaper off starts Brave. Never kill Brave.

    self_unit: the caller, if it is the wallpaper. Do not systemctl-start
    ourselves; stop wallpaper last and async so the process can return.
    crissa-display is enable/disable + start-on-wallpaper-off only.
    """
    if mode not in MODES:
        raise ValueError(mode)
    write_mode(mode)
    _window_state_follows(mode)
    wall_is_self = self_unit == WALL
    if mode == "wallpaper":
        _enable(WALL)
        if _zos_background():
            # ZOS Brave runs in the background under the wallpaper (the User 2026-10-02): it starts at
            # login and now, minimised by its wrapper; an explicit Quit keeps it down until a press.
            _enable(BRAVE)
            if not _zos_quit():
                _start(BRAVE)
        else:
            _disable(BRAVE)      # no login autostart; a live window stays
        if not wall_is_self:
            _start(WALL)
    elif mode == "brave":
        _enable(BRAVE)
        _disable(WALL)
        _start(BRAVE)            # wallpaper off → Brave autostart lives again
        _stop(WALL, async_=wall_is_self)
    else:
        # ZOS OFF IS A NORMAL UBUNTU DESKTOP (the User 2026-09-16: "I just need my
        # normal desktop back and dock bar gone in OFF mode" … "and the ubuntu
        # dock back too"). The "dock bar" that goes is OURS — the wallpaper's
        # own band at the bottom edge — and it leaves with the unit. What comes
        # BACK is everything of the User's that the wallpaper had taken:
        #
        #   desktop icons — the wallpaper disables GNOME's DING for its own
        #     FOLDERS key, and nothing undid that, so OFF handed back a bare
        #     desktop;
        #   Ubuntu's dock — claim_wallpaper turns dash-to-dock's intellihide
        #     off so its reveal zone stays out of the wall's corners. OFF
        #     RESETS the dock keys rather than setting them, so what comes back
        #     is Ubuntu's own arrangement and not a second coded opinion.
        #
        # Read it as: the house gives the machine back. The whole kit is still
        # one click away inside Brave.
        _disable(WALL)
        _disable(BRAVE)
        _stop(WALL, async_=wall_is_self)
        desktop_icons(True)
        # THE DOCK IS NOT OURS TO ARRANGE (the User 2026-09-16: "now you have our
        # dock on left side. Where is ubuntu classic doc? Just turn ours off
        # for this mode"). Two tries were both wrong: setting intellihide back
        # on was our opinion, and RESETTING the keys moved his dock from the
        # bottom to the left, which is the schema default and not his desk. So
        # OFF touches dash-to-dock not at all — the house's own dock band goes
        # with the wallpaper unit, and Ubuntu's stays exactly as the User left
        # it. dock_restore() is kept for a caller that explicitly wants stock.
    return status()


DOCK_SCHEMA = "org.gnome.shell.extensions.dash-to-dock"


def _gsettings(*args: str) -> bool:
    try:
        r = subprocess.run(["gsettings", "set", DOCK_SCHEMA, *args],
                           capture_output=True, text=True, timeout=8, check=False)
        return r.returncode == 0
    except Exception:
        return False


# THE KEYS THE HOUSE TOUCHES on Ubuntu's own dock. Only intellihide is ever
# written while the wall runs, but the arrangement has drifted (his dock came
# back on BOTTOM when he remembered it on the left), so OFF RESETS rather than
# setting: "it used to be on the left in hidden. They may want that back vs
# our coded version" (the User 2026-09-16). gsettings reset puts each key back to
# the schema default, which on Ubuntu is its own left-hand dock — the house
# undoing its edit, not replacing it with a second opinion.
DOCK_KEYS = ("intellihide", "dock-position", "dock-fixed", "autohide")


def dock_restore() -> bool:
    ok = True
    for key in DOCK_KEYS:
        try:
            r = subprocess.run(["gsettings", "reset", DOCK_SCHEMA, key],
                               capture_output=True, text=True, timeout=8)
            ok = ok and r.returncode == 0
        except Exception:  # noqa: BLE001
            ok = False
    return ok


# A PACK'S DOCK HIDES AND FITS ITS KEYS (the User 2026-10-10, .101 test 13: "the bottom dock never hides and spans the
# whole width", Ubuntu's stock dock fixed in panel mode over the desktop Wall). While a pack's desk Wall runs, Ubuntu's
# dock is the house's way: at the bottom, hidden until the pointer reaches the edge, as wide as its icons. The person's
# own values are kept first (<home>/dock-before.json, the first record wins so a crash never overwrites it) and put
# back when the Wall closes, when Crissa stops, or on CRISSA_DOCK_TIDY=0. Nothing on the house, and nothing where the
# dash-to-dock extension is not installed.
PACK_DOCK = (("dock-position", "'BOTTOM'"), ("dock-fixed", "false"), ("autohide", "true"), ("intellihide", "false"),
             ("extend-height", "false"))


def _dock_before() -> Path:
    return crissa_home.HOME / "dock-before.json"


def _gs_get(key: str, run=None) -> str | None:
    run = run or subprocess.run
    try:
        r = run(["gsettings", "get", DOCK_SCHEMA, key], capture_output=True, text=True, timeout=8, check=False)
    except Exception:  # noqa: BLE001
        return None
    return (r.stdout or "").strip() if r.returncode == 0 else None


def pack_dock_tidy(run=None, env=None) -> dict:
    """Ubuntu's dock the house's way while a pack's Wall runs, the person's values kept first."""
    e = os.environ if env is None else env
    run = run or subprocess.run
    if crissa_home.is_house() or str(e.get("CRISSA_DOCK_TIDY", "1")).strip().lower() in ("0", "off", "false", "no"):
        return {"ok": False, "skipped": "house" if crissa_home.is_house() else "off"}
    before = {}
    for key, _want in PACK_DOCK:
        got = _gs_get(key, run)
        if got is None:
            return {"ok": False, "skipped": "no dash-to-dock here"}
        before[key] = got
    path = _dock_before()
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": DOCK_SCHEMA, "keys": before}), encoding="utf-8")
    done = []
    for key, want in PACK_DOCK:
        if before.get(key) != want:
            try:
                run(["gsettings", "set", DOCK_SCHEMA, key, want], capture_output=True, text=True, timeout=8, check=False)
                done.append(key)
            except Exception:  # noqa: BLE001
                pass
    return {"ok": True, "set": done, "kept": str(path)}


# THE DOCK GOES BACK AFTER THE SHELL HAS LET THE WINDOW GO (the User 2026-10-24, .101 on 1.0.0: one click on the WALL
# key closed the Wall, the Wall logged "dock: restored autohide, dock-fixed, dock-position, extend-height, intellihide"
# and GNOME Shell died on signal 11 right after - five dash-to-dock settings landing in a burst while the Shell was
# still unmanaging the desktop window). The Wall's mainloop has ended when this runs, so the window is gone; the writes
# wait CRISSA_DOCK_RESTORE_DELAY_S (the Shell's unmanage of a desktop-type window is a few frames; 1.5 s leaves a slow
# Shell room and stays well inside the unit's TimeoutStopSec of 10) and then land ONE AT A TIME, CRISSA_DOCK_RESTORE_GAP_S
# apart (each is a dconf change the extension re-lays its dock on; 0.2 s is a frame or two of ease each). Kill:
# CRISSA_DOCK_RESTORE_DEFER=0 writes them at once, as 1.0.0.
DOCK_RESTORE_DELAY_S = 1.5
DOCK_RESTORE_GAP_S = 0.2


def dock_restore_defer(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_DOCK_RESTORE_DEFER", "1")).strip().lower() not in ("0", "off", "false", "no")


def dock_restore_pace(env=None) -> "tuple[float, float]":
    """(delay before the first write, gap between writes), in seconds; (0, 0) with the deferral off."""
    e = os.environ if env is None else env
    if not dock_restore_defer(e):
        return 0.0, 0.0
    try:
        delay = max(0.0, float(e.get("CRISSA_DOCK_RESTORE_DELAY_S", DOCK_RESTORE_DELAY_S)))
    except (TypeError, ValueError):
        delay = DOCK_RESTORE_DELAY_S
    try:
        gap = max(0.0, float(e.get("CRISSA_DOCK_RESTORE_GAP_S", DOCK_RESTORE_GAP_S)))
    except (TypeError, ValueError):
        gap = DOCK_RESTORE_GAP_S
    return delay, gap


def pack_dock_restore(run=None, sleep=None, env=None) -> dict:
    """The person's own dock again (the values pack_dock_tidy kept), and the record removed. Deferred and paced
    (dock_restore_pace): nothing is written while the Shell is still closing the Wall's window."""
    run = run or subprocess.run
    sleep = time.sleep if sleep is None else sleep
    path = _dock_before()
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "skipped": "nothing kept"}
    delay, gap = dock_restore_pace(env)
    keys = list((rec.get("keys") or {}).items())
    if keys and delay > 0:
        sleep(delay)
    for i, (key, val) in enumerate(keys):
        if i and gap > 0:
            sleep(gap)
        try:
            run(["gsettings", "set", DOCK_SCHEMA, key, str(val)], capture_output=True, text=True, timeout=8, check=False)
        except Exception:  # noqa: BLE001
            pass
    try:
        path.unlink()
    except OSError:
        pass
    return {"ok": True, "restored": sorted((rec.get("keys") or {})), "deferred_s": delay, "gap_s": gap}


def gnome_dock(show: bool) -> bool:
    """The GNOME dock is part of the normal desktop Brave sits on; the
    wallpaper paints its own (house_wall_dock) and wants dash-to-dock's
    reveal zone out of its corners. intellihide: shows unless a window
    covers it; off = never shown outside the overview.

    Since 2026-09-16 Ubuntu's dock IS the dock (house_wall_dock.own_dock):
    no write, its settings are the User's. Inline env read, not an import —
    this module runs inside house-mode without the wall's path."""
    if os.environ.get("HOUSE_WALL_OWN_DOCK", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return True
    return _gsettings("intellihide", "true" if show else "false")


# THE DESKTOP ICONS are the wall's FOLDERS key while it is running, and the
# wallpaper leaves them DISABLED when it stops — so OFF used to hand back a
# bare desktop with no icons (the User 2026-09-16: "I just need my normal desktop
# back and dock bar gone in OFF mode"). The uuid is resolved the same way
# house_wall_ascii resolves it — the house copy when gnome-shell knows it,
# else stock — because enabling a uuid the shell has not registered is "does
# not exist" and no icons at all. Not imported from there: that module pulls
# in cv2 and Tk, and this one runs inside house-mode.
DING_STOCK_UUID = "ding@rastersoft.com"
# House Init may set HOUSE_WALL_DING_UUID to its own copy; unset = stock DING.
DING_HOUSE_UUID = ((os.environ.get("HOUSE_WALL_DING_UUID") or "").strip()
                   or crissa_home.house_value("wall_ding_uuid") or DING_STOCK_UUID)


def _ding_uuid() -> str:
    try:
        r = subprocess.run(["gnome-extensions", "info", DING_HOUSE_UUID],
                           capture_output=True, text=True, timeout=6)
        if r.returncode == 0 and "State:" in r.stdout:
            return DING_HOUSE_UUID
    except Exception:  # noqa: BLE001
        pass
    return DING_STOCK_UUID


def desktop_icons(show: bool) -> bool:
    """GNOME's desktop icons on or off. A failure is not fatal: the glass
    switch must never be blocked by an extension that is not installed."""
    try:
        r = subprocess.run(["gnome-extensions", "enable" if show else "disable", _ding_uuid()],
                           capture_output=True, text=True, timeout=8)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


FROM_BRAVE = "from-brave"      # the note hand_to_wallpaper leaves for the wall's claim


def claim_wallpaper(settle_s: float = 1.5) -> dict:
    """Wallpaper boot: disable Brave autostart, and — when Brave WAS the
    glass (mode `brave`, or hand_to_wallpaper's note) — stop it, once this
    wall has had settle_s to paint (the User 2026-09-08 pm: "not both at once
    unless User does it"). A tool-window Brave (mode already `wallpaper`)
    stays: the User opened it."""
    raw = read_raw()
    prev = raw.split()[0] if raw.split() else ""
    brave_was_glass = prev == "brave" or FROM_BRAVE in raw.split()
    gnome_dock(False)
    out = apply("wallpaper", self_unit=WALL)
    out["brave_closed"] = False
    if brave_was_glass and is_active(BRAVE):
        time.sleep(max(0.0, float(settle_s)))
        if _zos_background():
            # not closed: put away, minimised, and the wall page let go (lib/zos_brave.py)
            try:
                import zos_brave
                out["brave_hidden"] = zos_brave.hide()
            except Exception as e:  # noqa: BLE001
                out["brave_hidden"] = {"ok": False, "error": type(e).__name__}
            return out
        _stop(BRAVE, async_=True)
        out["brave_closed"] = True
    return out


def hand_to_wallpaper() -> dict:
    """ZOS Wall ON from Brave's own Advanced menu (the User 2026-09-08 pm): the
    wallpaper comes up first; its claim reads the note and puts Brave away
    once it has painted. Never a gap of bare desktop, never both."""
    write_mode("wallpaper", FROM_BRAVE)
    _enable(WALL)
    if not _zos_background():
        _disable(BRAVE)
    _start(WALL)
    return dict(status(), handing="wallpaper", brave_closes="after the wall paints")


def hand_to_brave(self_unit: str | None = WALL, raise_window=None) -> dict:
    """ZOS desktop off (the User 2026-09-08): a normal desktop and the Hybrid
    wall in Crissa's Brave, small but faced.

    Order matters: Brave up and faced FIRST, the wallpaper stopped LAST —
    apply("brave") stops the caller async, so anything after it may not run.
      1. GNOME dock back (normal desktop).
      2. crissa-display up (start if it is not), CDP answering.
      3. Hybrid: VO flag gone, home tabs, wall tab active, window normal
         (mode_orchestrate.face_hybrid) — never fullscreen, never minimized.
      4. raise_window() — the caller's X raise (CDP cannot raise on Mutter).
      5. autostart flips (Brave on, wallpaper off) and the wallpaper stops.
    """
    out: dict = {"dock": gnome_dock(True)}
    out["brave_up"] = ensure_brave_for_tools()
    try:
        import mode_orchestrate
        out["face"] = mode_orchestrate.face_hybrid()
    except Exception as e:  # noqa: BLE001
        out["face"] = {"ok": False, "error": type(e).__name__}
    if raise_window is not None:
        try:
            out["raised"] = bool(raise_window())
        except Exception as e:  # noqa: BLE001
            out["raised"] = f"failed: {type(e).__name__}"
    out["mode"] = apply("brave", self_unit=self_unit)
    return out


def front_tab(url: str, timeout: float = 4.0) -> str:
    """Front `url` in Crissa's Brave as a tools tab (a Render show, or the
    Control Panel the User picked — the User 2026-09-13): activate the page when a
    tab already has it, else open one. Never navigates the wall tab and never
    sets full screen, so the dock and the folders stay reachable."""
    import json

    if not ensure_brave_for_tools():
        return "brave not up"
    base = CDP.rsplit("/json", 1)[0]
    key = url.rstrip("/")
    try:
        with crissa_egress.urlopen(f"{base}/json/list", 2, "glass mode") as r:
            tabs = json.loads(r.read().decode() or "[]")
        hit = next((t for t in tabs if t.get("type") == "page"
                    and str(t.get("url") or "").rstrip("/") == key), None)
        if hit:
            crissa_egress.urlopen(f"{base}/json/activate/{hit['id']}", 2, "glass mode").read()
            return "activated"
        req = urllib.request.Request(f"{base}/json/new?{url}", method="PUT")
        crissa_egress.urlopen(req, timeout, "glass mode").read()
        return "opened"
    except Exception as e:  # noqa: BLE001
        return f"cdp failed: {type(e).__name__}"


def ensure_brave_for_tools(timeout: float = 12.0) -> bool:
    """Start Crissa's Brave for Krew/Composer/Panel without flipping autostart.

    Wallpaper stays the glass. Next login still does not open Brave.
    A start is not a close — wallpaper-on never stops this unit.
    """
    if not is_active(BRAVE):
        want = crissa_home.HOME / "brave-tools-want"
        want.parent.mkdir(parents=True, exist_ok=True)
        want.write_text("tools\n", encoding="utf-8")
        _start(BRAVE)
    deadline = time.time() + max(0.5, float(timeout))
    while time.time() < deadline:
        try:
            crissa_egress.urlopen(CDP, 1.0, "glass mode").read(64)
            return True
        except crissa_egress.Refused:
            return False              # kill-search: an off-LAN CDP never comes up
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.35)
    return False
