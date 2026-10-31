"""house_wall_dock — the wall's own dock: GNOME's favourites as tiles on the
chrome (the User 2026-09-08: "Can we make our own retractable panel from our end
that unhides with mouse move?"). dash-to-dock's reveal zone fought the wall's
corner controls on the left, then the right; the wall's chrome already comes
up on a mouse move and folds on the idle, so the dock rides it.

The list and the icons come from scripts/house-dock-icons.py (system python,
PyGObject: favourites + icon theme + GdkPixbuf) into <home>/house-wall-dock/
manifest.json; this module just reads that and keeps the PIL tiles warm.
"""
from __future__ import annotations

import json
import os
import subprocess
import shutil
import re
import sys
import time
from pathlib import Path

import crissa_home  # the state directory, resolved once (2026-09-26)

DOCK_DIR = Path(os.environ.get("HOUSE_WALL_DOCK_DIR", str(crissa_home.HOME / "house-wall-dock")))
MANIFEST = DOCK_DIR / "manifest.json"
HELPER = Path(__file__).resolve().parent.parent / "scripts" / "house-dock-icons.py"
SYSTEM_PY = "/usr/bin/python3"          # PyGObject lives here, not in the wall's env
MAX_TILES = 12                          # dock_0 .. dock_11 in the glass contract
REFRESH_S = 600.0

_cache: dict = {"items": [], "mtime": 0.0, "tiles": {}, "running": {}, "running_at": 0.0}
RUNNING_POLL_S = 2.0
CRISSA_PROFILE = str(crissa_home.BROWSER_PROFILE)   # Crissa's Brave; the User's is every other Brave


def own_dock() -> bool:
    """UBUNTU'S DOCK IS THE DOCK (the User 2026-09-16, ZOS Wallpaper ON: "I like
    Ubuntu dock with our buttons on its left. Please get rid of ours and make
    ubuntu our dock"). dash-to-dock draws over the wall, so the wall stops
    painting its own favourites / APPS / folders / Clear desktop row and keeps
    only the rail keys, placed where they sat beside that row. The house then
    leaves dash-to-dock's settings alone in every mode — they are the User's.
    HOUSE_WALL_OWN_DOCK=1 brings the wall's own dock back (and the house's
    intellihide writes with it)."""
    return os.environ.get("HOUSE_WALL_OWN_DOCK", "0").strip().lower() in ("1", "true", "yes", "on")


# THE RAIL KEYS ARE UBUNTU DOCK LAUNCHERS (the User 2026-09-16: "size and align
# buttons with dock icons please make the dock hide when they do. one piece if
# poss"). Each is a .desktop pinned at the LEFT end of favorite-apps
# (scripts/house-dock-keys.py), so dash-to-dock sizes, aligns and hides them
# with its own icons. The launcher (scripts/house-dock-key) writes its name to
# PRESS; the wall polls it and runs the same code a click on the key ran.
# name: (desktop id, dock label, glyph, the page it fronts — None = the wall's own)
DOCK_KEYS = {
    "krew": ("house-key-krew.desktop", "Krew", "⚒", ""),
    "composer": ("house-key-composer.desktop", "Store — packs of new loops for Crissa", "▶", "https://store.zoeyos.dev/store/"),
    "panel": ("house-key-panel.desktop", "Control Panel", "⚙", "http://127.0.0.1:8097/"),
    "menu": ("house-key-menu.desktop", "Advanced", "⋯", None),
}
KEY_IDS = frozenset(v[0] for v in DOCK_KEYS.values())


def advanced_on(env: dict | None = None) -> bool:
    """THE OLD ADVANCED PANEL IS THE HOUSE'S (the User 2026-10-29: "i think we can delete more display settings if all
    have been transferred into popup menu"): the MENU carries every row of it a kit has (brightness, resolution, flourish
    %, the day's hours, desktop folders, leaves at day end); the one row left, ZOS desktop, hands the glass to the
    house's Brave (glass_mode.hand_to_brave), which a kit does not have - two presses there stopped the kit's own Wall.
    So in a pack the rail draws no ⋯, its hover and click open nothing, and the MENU has no "More display settings".
    The house keeps all of it until ZOS desktop has a MENU row. CRISSA_OLD_ADVANCED=1 brings it back in a pack."""
    if crissa_home.is_house():
        return True
    e = os.environ if env is None else env
    return str(e.get("CRISSA_OLD_ADVANCED", "0")).strip().lower() in ("1", "on", "true", "yes")
PRESS = DOCK_DIR / "press"
PRESS_FRESH_S = 5.0     # an older press is a leftover, not a click


UBUNTU = DOCK_DIR / "ubuntu.json"     # scripts/house-dock-icons.py counts the dock
UBUNTU_PAD = 16                       # a dock item is the icon + 16 px (44 at 28, measured 2026-09-16)
UBUNTU_SEP = 4                        # the separator before the drives and the trash


def ubuntu_key_right(name: str, W: int) -> float | None:
    """Screen x of the RIGHT edge of a key's launcher in Ubuntu's dock, so the
    wall can hang Advanced over ⋯ (the User 2026-09-16: "menu farr off button").
    dash-to-dock centres the row on the glass; the keys are the first items.
    Measured on his dock: 19 items at 44 px, ⋯ centred at x 1015 on 2560.
    A running app that is not a favourite widens the row by one item (the
    anchor drifts 22 px left per app); None without the helper's count."""
    try:
        u = json.loads(UBUNTU.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if name not in DOCK_KEYS:
        return None
    pitch = int(u.get("icon") or 28) + UBUNTU_PAD
    rest = int(u.get("locations") or 0) + (1 if u.get("trash") else 0)
    n = int(u.get("favorites") or 0) + rest + (1 if u.get("show_apps") else 0)
    width = n * pitch + (UBUNTU_SEP if rest else 0)
    left = W / 2.0 - width / 2.0
    return left + (list(DOCK_KEYS).index(name) + 1) * pitch


def write_press(name: str) -> bool:
    """The launcher's half: one line, the key's name, atomically."""
    if name not in DOCK_KEYS:
        return False
    try:
        DOCK_DIR.mkdir(parents=True, exist_ok=True)
        tmp = PRESS.with_suffix(".tmp")
        tmp.write_text(name + "\n", encoding="utf-8")
        tmp.replace(PRESS)
        return True
    except OSError:
        return False


def read_press(since_mtime: float) -> tuple[float, str | None]:
    """The wall's half: (mtime, name) when PRESS changed after since_mtime and
    is fresh, else (the mtime seen, None). One stat per poll."""
    try:
        m = PRESS.stat().st_mtime
    except OSError:
        return since_mtime, None
    if m == since_mtime:
        return m, None
    if time.time() - m > PRESS_FRESH_S:
        return m, None
    try:
        name = PRESS.read_text(encoding="utf-8").strip()
    except OSError:
        return m, None
    return m, (name if name in DOCK_KEYS else None)


def clear_geometry() -> bool:
    """No wall dock, no sweep targets: house-shell then flies a closing window
    into its own icon geometry, which dash-to-dock sets to Ubuntu's icon. A
    stale geometry.json would sweep windows into tiles nobody draws."""
    _geo_last["key"] = None
    try:
        GEOMETRY.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def refresh(force: bool = False, timeout: float = 15.0) -> bool:
    """Run the helper when the manifest is missing or older than REFRESH_S."""
    try:
        age = time.time() - MANIFEST.stat().st_mtime
    except OSError:
        age = float("inf")
    if not force and age < REFRESH_S:
        return True
    if not (Path(SYSTEM_PY).is_file() and HELPER.is_file()):
        return False
    try:
        r = subprocess.run([SYSTEM_PY, str(HELPER)], capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False


def items() -> list[dict]:
    """[{id, name, png}] from the manifest, capped at MAX_TILES; [] without one."""
    try:
        m = MANIFEST.stat().st_mtime
    except OSError:
        return []
    if m != _cache["mtime"]:
        try:
            data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            return _cache["items"]
        # the rail keys pinned in Ubuntu's dock are not favourites of the wall's own row
        _cache["items"] = [d for d in data if isinstance(d, dict) and d.get("id") and d["id"] not in KEY_IDS][:MAX_TILES]
        _cache["mtime"] = m
        _cache["tiles"] = {}
    return list(_cache["items"])


GEOMETRY = DOCK_DIR / "geometry.json"
_geo_last = {"key": None}


def write_geometry(tiles: dict, hub) -> bool:
    """The dock's tiles on the glass, screen px, for gnome-shell: every
    window CLOSE sweeps into its app's tile, else into the hub (the plate
    while open, the handle while folded) — the wall's shell extension reads
    this on each close. tiles: {desktop id: (x0, y0, x1, y1)}; hub: a box.
    Written only when it changes. True when a write happened."""
    def rect(b):
        x0, y0, x1, y1 = (int(round(float(v))) for v in b[:4])
        return [x0, y0, max(1, x1 - x0), max(1, y1 - y0)]
    body = {"tiles": {str(k): rect(v) for k, v in tiles.items()}, "hub": rect(hub)}
    key = json.dumps(body, sort_keys=True)
    if key == _geo_last["key"]:
        return False
    try:
        DOCK_DIR.mkdir(parents=True, exist_ok=True)
        tmp = GEOMETRY.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(body, at=time.time()), indent=1), encoding="utf-8")
        tmp.replace(GEOMETRY)
    except OSError:
        return False
    _geo_last["key"] = key
    return True


def _token(it: dict) -> str:
    """What a favourite's main process is called: the launch command's
    basename, or the binary it wraps (crissa-display-open runs Brave)."""
    exe = os.path.basename(str(it.get("exe") or ""))
    if exe in ("crissa-display-open", "brave-browser-beta", "brave-browser"):
        return "brave"
    return exe


def running(ps_lines: list[str] | None = None, xclasses: list[str] | None = None) -> dict:
    """{desktop id: n} for favourites with a live instance — GNOME's dots
    (the User 2026-09-08: "can we get the dots on the apps for instances open").
    gnome-shell's Introspect bus is closed to us (Access denied, no
    `introspect` key in Shell 50), so: MAIN processes (no --type= child)
    whose argv[0] basename is the favourite's command, Brave split by
    profile (Crissa's own vs the User's), the headless probe
    excluded; where the app is an X client its window count by WM_CLASS wins.
    Capped at 3, like the dots. ps_lines / xclasses are for tests."""
    if ps_lines is None:
        try:
            ps_lines = subprocess.run(["ps", "-eo", "args", "--no-headers"], capture_output=True, text=True, timeout=3).stdout.splitlines()
        except Exception:
            ps_lines = []
    if xclasses is None:
        xclasses = _x_classes()
    out: dict = {}
    for it in items():
        tok = _token(it)
        if not tok:
            continue
        n = 0
        for line in ps_lines:
            parts = line.split()
            if not parts:
                continue
            base = os.path.basename(parts[0])
            if base != tok or "--type=" in line or "--headless" in line:
                continue
            if tok == "brave":
                crissa = ("--user-data-dir=" + CRISSA_PROFILE) in line
                if crissa != (os.path.basename(str(it.get("exe") or "")) == "crissa-display-open"):
                    continue
            n += 1
        wm = str(it.get("wmclass") or "").lower()
        if wm:
            xn = sum(1 for c in xclasses if wm in c.lower() or c.lower().endswith(wm))
            if tok == "brave":
                xn = sum(1 for c in xclasses if ("crissa-display" in c.lower()) == (os.path.basename(str(it.get("exe") or "")) == "crissa-display-open") and "brave" in c.lower())
            n = max(n, xn)
        if n:
            out[it["id"]] = min(3, n)
    return out


def _x_classes() -> list[str]:
    """WM_CLASS of every X client (XWayland apps only; Wayland-native ones are invisible here)."""
    try:
        root = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST"], capture_output=True, text=True, timeout=2).stdout
        ids = [w for w in root.replace(",", " ").split() if w.startswith("0x")]
        out = []
        for w in ids:
            c = subprocess.run(["xprop", "-id", w, "WM_CLASS"], capture_output=True, text=True, timeout=2).stdout
            out.append(",".join(p.strip().strip('"') for p in c.split("=", 1)[-1].split(",")) if "=" in c else "")
        return out
    except Exception:
        return []


def parse_windows(stdout: str) -> list[dict]:
    """busctl's `a(ts) N id "title" id "title" …` from house-shell Windows."""
    import shlex
    try:
        parts = shlex.split(stdout or "")
    except ValueError:
        return []
    if len(parts) < 2 or parts[0] != "a(ts)":
        return []
    try:
        n = int(parts[1])
    except ValueError:
        return []
    out = []
    vals = parts[2:]
    for k in range(n):
        if 2 * k + 1 >= len(vals):
            break
        try:
            out.append({"id": int(vals[2 * k]), "title": vals[2 * k + 1], "via": "shell"})
        except ValueError:
            break
    return out


def _shell_windows(app_id: str) -> list[dict] | None:
    """Every window of the app, Wayland and X, from house-shell (after its
    first login); None while the extension is not live."""
    # --json=short: busctl's plain form octal-escapes every non-ASCII byte
    # ("Krew \342\200\224 Crissa OS"), and shlex hands the escapes through;
    # JSON carries the em dash whole (the User 2026-09-11, the stack's title).
    try:
        out = subprocess.run(["busctl", "--user", "--json=short", "call", "org.house.Shell", "/org/house/Shell",
                              "org.house.Shell", "Windows", "s", app_id],
                             capture_output=True, text=True, timeout=1.5)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    wins = parse_windows_json(out.stdout)
    return wins if wins is not None else parse_windows(out.stdout)


def parse_windows_json(stdout: str) -> list[dict] | None:
    """busctl --json=short: {"type":"a(ts)","data":[[[id,"title"],…]]}. None
    when the text is not that shape (the plain parser then has a go)."""
    try:
        d = json.loads(stdout or "")
        rows = d["data"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    out = []
    for r in rows or []:
        try:
            out.append({"id": int(r[0]), "title": str(r[1]), "via": "shell"})
        except (ValueError, IndexError, TypeError):
            continue
    return out


def x_windows_of(it: dict, clients: list[tuple[str, str, str]]) -> list[dict]:
    """The favourite's X windows from (xid, WM_CLASS, title) triples — the
    same rule as running(): the manifest's wmclass, Brave split by profile
    (Crissa's window carries crissa-display). Wayland-native windows are not
    in this list; house-shell sees those."""
    wm = str(it.get("wmclass") or "").lower()
    crissa = os.path.basename(str(it.get("exe") or "")) == "crissa-display-open"
    tok = _token(it)
    out = []
    for xid, cls, title in clients:
        c = cls.lower()
        if tok == "brave":
            ok = "brave" in c and (("crissa-display" in c) == crissa)
        else:
            ok = bool(wm) and (wm in c or c.endswith(wm))
        if ok:
            out.append({"id": xid, "title": title, "via": "x"})
    return out


def _x_clients() -> list[tuple[str, str, str]]:
    try:
        root = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST"], capture_output=True, text=True, timeout=2).stdout
        ids = [w for w in root.replace(",", " ").split() if w.startswith("0x")]
        out = []
        for w in ids:
            r = subprocess.run(["xprop", "-id", w, "WM_CLASS", "_NET_WM_NAME", "_NET_WM_STATE"],
                               capture_output=True, text=True, timeout=2).stdout
            cls = title = ""
            skip = False
            for line in r.splitlines():
                if line.startswith("WM_CLASS") and "=" in line:
                    cls = ",".join(p.strip().strip('"') for p in line.split("=", 1)[-1].split(","))
                elif line.startswith("_NET_WM_NAME") and "=" in line:
                    title = line.split("=", 1)[-1].strip().strip('"')
                elif line.startswith("_NET_WM_STATE") and "SKIP_TASKBAR" in line:
                    skip = True
            if not skip:
                out.append((w, cls, title))
        return out
    except Exception:
        return []


_win_cache: dict = {}


def windows(it: dict, ttl: float = 2.0) -> list[dict]:
    """The favourite's open windows for the dock's chooser (the User 2026-09-08:
    "if two dots can I hover to display choice of windows?"): house-shell's
    list when it is live (every window), else the X clients. Cached ttl s."""
    key = str(it.get("id") or "")
    now = time.time()
    ent = _win_cache.get(key)
    if ent and now - ent[0] < ttl:
        return list(ent[1])
    wins = _shell_windows(key)
    if wins is None:
        wins = x_windows_of(it, _x_clients())
    else:
        for w in wins:
            w["thumb"] = thumb(w)
    _win_cache[key] = (now, wins)
    return list(wins)


THUMB_W = 220


def thumb(win: dict, width: int = THUMB_W) -> str:
    """A PNG of the window for the chooser: house-shell's Thumb (every
    window, after its login). X-only days have no capture tool on this box
    (no ImageMagick, no xwd): '' then, and the row shows its title."""
    if win.get("via") != "shell":
        return ""
    try:
        out = subprocess.run(["busctl", "--user", "call", "org.house.Shell", "/org/house/Shell",
                              "org.house.Shell", "Thumb", "ti", str(int(win["id"])), str(int(width))],
                             capture_output=True, text=True, timeout=2.5)
    except Exception:
        return ""
    if out.returncode != 0:
        return ""
    import shlex as _shlex
    try:
        parts = _shlex.split(out.stdout or "")
    except ValueError:
        return ""
    return parts[1] if len(parts) == 2 and parts[0] == "s" and parts[1] else ""


def activate(win: dict) -> bool:
    """Raise a window from windows(): house-shell's Activate for its ids.
    X ids are the wall's to raise (Shape.activate, _NET_ACTIVE_WINDOW)."""
    if win.get("via") != "shell":
        return False
    try:
        out = subprocess.run(["busctl", "--user", "call", "org.house.Shell", "/org/house/Shell",
                              "org.house.Shell", "Activate", "t", str(int(win["id"]))],
                             capture_output=True, text=True, timeout=1.5)
        return out.returncode == 0 and "true" in out.stdout
    except Exception:
        return False


def poll_running() -> dict:
    """Refresh the running map at most every RUNNING_POLL_S; cheap enough for the dock thread."""
    now = time.time()
    if now - _cache["running_at"] >= RUNNING_POLL_S:
        _cache["running"] = running()
        _cache["running_at"] = now
    return dict(_cache["running"])


def tile(png: str | None, size: int):
    """The icon as an RGBA PIL image at `size`, cached; None without a file."""
    from PIL import Image
    if not png:
        return None
    key = (png, int(size))
    im = _cache["tiles"].get(key)
    if im is not None:
        return im
    try:
        im = Image.open(png).convert("RGBA")
        im = im.resize((int(size), int(size)), Image.Resampling.LANCZOS)
    except Exception:
        return None
    _cache["tiles"][key] = im
    return im


def launch_plan(app_id: str, running: int, actions) -> tuple[str, str | None]:
    """What a dock click does. An app that is already up and offers a
    new-window action gets a NEW WINDOW; plain activation of a running
    single-instance app (Ptyxis, Brave) only asks the compositor to focus
    the old window, and Mutter's focus-stealing rule turns that into a
    "… is ready" toast — to the eye, nothing (the User 2026-09-08: "I cannot
    click my icons in dock"; the journal had every click and "launched").
    Everything else is a plain launch: a new process, a new window."""
    acts = [str(a) for a in (actions or [])]
    if int(running or 0) > 0:
        for name in ("new-window", "new-private-window-off", "NewWindow"):
            if name in acts:
                return "action", name
    return "launch", None


_LAUNCHER = r"""
import sys, gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio
app_id, mode, action = sys.argv[1], sys.argv[2], sys.argv[3]
a = Gio.DesktopAppInfo.new(app_id)
if a is None:
    sys.exit(3)
ok = a.launch_action(action, None) if mode == "action" else a.launch([], None)
print("action" if mode == "action" else "launch")
"""


def launch(app_id: str, running: int = 0) -> str:
    """Open the favourite the way a dock does, detached; the wall never
    waits on an app. Runs on the system python (PyGObject lives there)."""
    try:
        acts: list = []
        try:
            out = subprocess.run(
                [SYSTEM_PY, "-c", "import gi; gi.require_version('Gio','2.0'); from gi.repository import Gio; "
                 "a=Gio.DesktopAppInfo.new(%r); print(' '.join(a.list_actions()) if a else '')" % app_id],
                capture_output=True, text=True, timeout=3)
            acts = (out.stdout or "").split()
        except Exception:  # noqa: BLE001
            acts = []
        mode, action = launch_plan(app_id, running, acts)
        cmd = [SYSTEM_PY, "-c", _LAUNCHER, app_id, mode, action or ""]
        # ITS OWN CGROUP. Gio here has no app-glib scope support, so the app
        # stayed in house-wall-ascii.service's control group and every wall
        # restart (KillMode=control-group) took the User's dock-launched Brave
        # with it — a clean exit, no crash record, a vanished window
        # (2026-09-08: "Brave has been crashing"). A transient scope under
        # app.slice is what gnome-shell gives a launch; the wall gives the
        # same, so the app outlives the wall.
        scope = shutil.which("systemd-run")
        if scope:
            unit = "house-dock-%s-%d" % (re.sub(r"[^A-Za-z0-9]+", "-", app_id.rsplit(".desktop", 1)[0])[:40], int(time.time() * 1000) % 100000000)
            cmd = [scope, "--user", "--scope", "--quiet", "--collect", "--slice=app.slice", "--unit=" + unit, "--"] + cmd
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return "new window" if mode == "action" else "launched"
    except Exception as e:  # noqa: BLE001
        return f"launch failed: {type(e).__name__}"


# ── THE DESK'S OWN TILES: after the favourites and APPS, past a seam: two
# folders, then Clear desktop. Not favourites — the shell's close sweep does
# not target them, and MAX_TILES does not count them. Paths from Init /
# places.json (PLACES_FILE); defaults are the user's Desktop and Deliverables.
def _places_defaults() -> list:
    """Desktop + Deliverables. House Init may set deliverables_dir / deliverables_name in
    config/house-machine.json (never ships); else ~/Desktop/Deliverables."""
    try:
        import crissa_home
        dpath = (crissa_home.house_value("deliverables_dir") or "").strip()
        dname = (crissa_home.house_value("deliverables_name") or "").strip() or "Deliverables"
    except Exception:
        dpath, dname = "", "Deliverables"
    if not dpath:
        dpath = str(Path.home() / "Desktop" / "Deliverables")
    return [
        {"id": "place:desktop", "name": "Desktop", "path": str(Path.home() / "Desktop"), "icon": "user-desktop"},
        {"id": "place:deliverables", "name": dname, "path": dpath, "icon": "folder"},
    ]


PLACES = _places_defaults()
PLACES_FILE = DOCK_DIR / "places.json"
_places_cache: dict = {"mtime": 0.0, "items": None}


def places() -> list[dict]:
    """The folder tiles: PLACES with the theme's folder PNGs from places.json
    (scripts/house-dock-icons.py); the bare list, initials drawn, without one."""
    try:
        m = PLACES_FILE.stat().st_mtime
    except OSError:
        return [dict(d) for d in PLACES]
    if m != _places_cache["mtime"] or _places_cache["items"] is None:
        try:
            data = json.loads(PLACES_FILE.read_text(encoding="utf-8"))
            byid = {d.get("id"): d for d in data if isinstance(d, dict)}
        except Exception:
            byid = {}
        _places_cache["items"] = [dict(d, png=(byid.get(d["id"]) or {}).get("png")) for d in PLACES]
        _places_cache["mtime"] = m
    return [dict(d) for d in _places_cache["items"]]


def open_place(path: str) -> str:
    """Open a folder the way a double-click on the desk does: the default
    handler for inode/directory (Dolphin since 2026-09-09, the User: "set default
    to Dolphin"), in its own scope so a wall restart never takes it."""
    if not os.path.isdir(path):
        return "no such folder"
    cmd = ["gio", "open", path]
    scope = shutil.which("systemd-run")
    if scope:
        unit = "house-dock-place-%d" % (int(time.time() * 1000) % 100000000)
        cmd = [scope, "--user", "--scope", "--quiet", "--collect", "--slice=app.slice", "--unit=" + unit, "--"] + cmd
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return "opened"
    except Exception as e:  # noqa: BLE001
        return f"open failed: {type(e).__name__}"


def raise_place(path: str, app_id: str = "", timeout: float = 2.5) -> str:
    """After open_place: RAISE the folder's window. The handler (Dolphin) is
    single-instance — a second open lands in the window it already has, and
    Mutter's focus-stealing rule leaves that window wherever it was, so to
    the eye nothing happened (the User 2026-09-09: "folders not working from
    wall dock now"; the journal had every click and "opened"). house-shell
    lists the app's windows with titles; the one that now shows the folder
    (its path or its name) is activated. Returns how it went."""
    app = app_id or folder_handler() or "org.kde.dolphin.desktop"
    base = os.path.basename(path.rstrip("/")) or path
    t0 = time.time()
    seen: list[str] = []
    while time.time() - t0 < timeout:
        wins = _shell_windows(app)
        if wins is None:
            return "no house-shell (raise skipped)"
        seen = [str(w.get("title") or "") for w in wins]
        # the title's folder part, before " — Dolphin" (Dolphin shows a place
        # by its full path and a plain folder by its name); an EXACT match —
        # "Desktop" is a substring of ".../Desktop/Deliverables"
        def shown(t: str) -> str:
            t = t.replace("\u2014", "—")
            return t.rsplit(" — ", 1)[0].strip() if " — " in t else t.strip()
        hit = next((w for w in wins if shown(str(w.get("title") or "")) in (path, path.rstrip("/"), base)), None)
        if hit:
            return f"raised {hit.get('title')!r}" if activate(hit) else f"raise refused {hit.get('title')!r}"
        time.sleep(0.25)
    return f"no window shows {base!r} (saw {seen[:3]})"


def folder_handler() -> str:
    """The desktop id that opens folders (xdg-mime), '' when unknown."""
    try:
        out = subprocess.run(["xdg-mime", "query", "default", "inode/directory"], capture_output=True, text=True, timeout=3)
        return (out.stdout or "").strip()
    except Exception:
        return ""


# ── CLEAR DESKTOP: Mutter's own show-desktop, asked through Xwayland with
# the EWMH root message (_NET_SHOWING_DESKTOP is in the root's
# _NET_SUPPORTED; measured 2026-09-09 08:1x: the hint flips 0→1→0 and the
# windows come back on the unshow — Wayland windows too, it is a workspace
# op). house-shell's ShowDesktop is the fallback for a desk without X.
_x: dict = {"lib": None, "dpy": None}


def _xlib():
    from ctypes import CDLL, c_char_p, c_int, c_long, c_ulong, c_void_p
    if _x["lib"] is None:
        X = CDLL("libX11.so.6")
        X.XOpenDisplay.restype = c_void_p
        X.XOpenDisplay.argtypes = [c_char_p]
        X.XInternAtom.restype = c_ulong
        X.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
        X.XDefaultRootWindow.restype = c_ulong
        X.XDefaultRootWindow.argtypes = [c_void_p]
        X.XSendEvent.argtypes = [c_void_p, c_ulong, c_int, c_long, c_void_p]
        X.XFlush.argtypes = [c_void_p]
        _x["lib"] = X
    if _x["dpy"] is None:
        _x["dpy"] = _x["lib"].XOpenDisplay(None)
    return _x["lib"], _x["dpy"]


def show_desktop(show: bool) -> str:
    """Minimise every window (show=True) or bring them back. Returns how."""
    try:
        from ctypes import Structure, Union, byref, c_int, c_long, c_ulong, c_void_p
        X, dpy = _xlib()
        if not dpy:
            raise OSError("no X display")

        class XCM(Structure):
            _fields_ = [("type", c_int), ("serial", c_ulong), ("send_event", c_int), ("display", c_void_p),
                        ("window", c_ulong), ("message_type", c_ulong), ("format", c_int), ("data", c_long * 5)]

        class XEv(Union):
            _fields_ = [("xclient", XCM), ("pad", c_long * 24)]

        root = X.XDefaultRootWindow(dpy)
        ev = XEv()
        ev.xclient.type = 33            # ClientMessage
        ev.xclient.window = root
        ev.xclient.format = 32
        ev.xclient.message_type = X.XInternAtom(dpy, b"_NET_SHOWING_DESKTOP", 0)
        ev.xclient.data[0] = 1 if show else 0
        X.XSendEvent(dpy, root, 0, (1 << 20) | (1 << 19), byref(ev))   # SubstructureRedirect | SubstructureNotify
        X.XFlush(dpy)
        return "ewmh"
    except Exception as e:  # noqa: BLE001
        first = f"ewmh failed: {type(e).__name__}"
    try:
        out = subprocess.run(["busctl", "--user", "--timeout=3", "call", "org.house.Shell", "/org/house/Shell",
                              "org.house.Shell", "ShowDesktop", "b", "true" if show else "false"],
                             capture_output=True, text=True, timeout=4)
        if out.returncode == 0 and "true" in out.stdout:
            return "house-shell"
        return f"{first}; house-shell: {(out.stderr or out.stdout).strip()[:80] or 'no'}"
    except Exception as e:  # noqa: BLE001
        return f"{first}; house-shell: {type(e).__name__}"


def parse_showing(stdout: str) -> bool:
    """xprop -root _NET_SHOWING_DESKTOP → `_NET_SHOWING_DESKTOP(CARDINAL) = 1`."""
    tail = (stdout or "").strip().rsplit("=", 1)[-1].strip()
    return tail == "1"


def showing_desktop() -> bool:
    """Mutter keeps the root hint itself, and clears it when a window is
    activated or mapped — so this is the desk's truth, not our last click."""
    try:
        out = subprocess.run(["xprop", "-root", "_NET_SHOWING_DESKTOP"], capture_output=True, text=True, timeout=2)
        return parse_showing(out.stdout)
    except Exception:
        return False


_show_cache: dict = {"at": 0.0, "v": False}


def poll_showing(force: bool = False) -> bool:
    now = time.time()
    if force or now - _show_cache["at"] >= RUNNING_POLL_S:
        _show_cache["v"] = showing_desktop()
        _show_cache["at"] = now
    return bool(_show_cache["v"])


if __name__ == "__main__":
    refresh(force=True)
    for it in items():
        print(it["id"], "|", it["name"], "|", it.get("png"))
    sys.exit(0)
