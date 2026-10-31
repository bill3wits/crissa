"""Is the other plugin live? Crissa's side of the watcher crossover (the User 2026-10-06).

"Every watcher and keeper must know whether the other plugin is live, or default to its home system only."

An add-on (one of the settings' add-on slots, or any that registered a manifest) counts as INSTALLED when it left a trace Crissa can read without
importing it: a panel manifest in an add-on folder (lib/crissa_panel_addons.py) or an endpoint offer in
`<crissa home>/endpoints.d/` (the offers format of lib/crissa_agent.py: {"name", "url", "by"?, ...}; an offer is
the add-on's when its file name is the add-on id, starts with "<id>-", or its "by" names it). It is LIVE when it
is installed AND one of its offered APIs answers (any HTTP status below 500: 401 is an API asking for its key).

- live: Crissa's watchers (the launcher's heartbeat, the panel) also watch the add-on's own pieces - each unit its
  manifest tiles name, and the endpoint itself - and say so.
- not live: they watch Crissa only, say "<id> not installed - watching Crissa only" (or "installed but its API
  is not answering"), and raise NO alarm for the add-on's pieces.

`link()` is re-run on every watcher pass (the launcher's every LINK_EVERY_S, the panel's snapshot): installing or
removing the add-on, or its API going quiet, shows on the next pass. Reads only; never a key (an offer's key_ref
is not read here). Kill: CRISSA_ADDON_LINK=0 (Crissa only, no probe).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

LINK_EVERY_S = float(os.environ.get("CRISSA_ADDON_LINK_EVERY_S", "10"))
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
DASH = "\u2014"


def enabled(env: dict | None = None) -> bool:
    return str((os.environ if env is None else env).get("CRISSA_ADDON_LINK", "1")).strip() != "0"


def offers_for(addon_id: str, home) -> list[dict]:
    """The add-on's endpoint offers (name, url, by, file); unreadable files are skipped, never raised."""
    d = Path(home) / "endpoints.d"
    out = []
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            o = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(o, dict):
            continue
        by = str(o.get("by") or "").strip().lower()
        stem = f.stem.lower()
        if stem == addon_id or stem.startswith(addon_id + "-") or by == addon_id or by.startswith(addon_id + " "):
            url = str(o.get("url") or "").strip()
            if url.startswith(("http://", "https://")):
                out.append({"id": f.stem, "name": str(o.get("name") or f.stem)[:120], "url": url, "file": str(f)})
    return out


def answers(url: str, timeout: float = 1.5) -> bool:
    """Any HTTP answer below 500 is an API that is up. Through Crissa's one egress switch: with kill-search on,
    an offer whose url is off the LAN is never probed (and so never live)."""
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        import crissa_egress
        if not crissa_egress.allowed(url):
            return False
        opener = lambda req, t: crissa_egress.urlopen(req, t, what="add-on link probe")  # noqa: E731
    except ImportError:
        opener = lambda req, t: urllib.request.urlopen(req, timeout=t)  # noqa: E731
    try:
        with opener(urllib.request.Request(url, method="GET"), timeout) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:  # noqa: BLE001
        return False


def unit_active(unit: str, scope: str = "user", run=subprocess.run) -> str:
    try:
        r = run(["systemctl"] + (["--user"] if scope != "system" else []) + ["is-active", unit],
                capture_output=True, text=True, timeout=5)
        return (r.stdout or "").strip() or "unknown"
    except Exception as e:  # noqa: BLE001
        return f"unknown ({type(e).__name__})"


def link(addon_id: str, home, manifests: list[dict], *, title: str = "", answer: Callable[[str], bool] = answers,
         active: Callable[..., str] = unit_active, now: float | None = None) -> dict:
    """One add-on: installed? live? and, only while live, the state of each of its pieces."""
    now = time.time() if now is None else now
    man = next((m for m in manifests or [] if m.get("id") == addon_id), None)
    offers = offers_for(addon_id, home)
    installed = bool(man or offers)
    live_url = next((o["url"] for o in offers if answer(o["url"])), "") if installed else ""
    live = bool(live_url)
    out = {"id": addon_id, "title": title or (man or {}).get("title") or addon_id, "installed": installed,
           "live": live, "checked": round(now, 1), "endpoint": live_url or (offers[0]["url"] if offers else ""),
           "manifest": (man or {}).get("file", ""), "offers": [o["id"] for o in offers], "pieces": [], "down": []}
    if live:
        n = len([t for t in (man or {}).get("tiles") or [] if t.get("unit")])
        out["note"] = (f"{addon_id} live {DASH} watching Crissa and {addon_id}'s "
                       f"{n} unit{'s' if n != 1 else ''} and its API")
        for t in (man or {}).get("tiles") or []:
            u = t.get("unit")
            if not u:
                continue
            st = active(u, t.get("scope") or "user")
            out["pieces"].append({"unit": u, "state": st})
            if st not in ("active", "activating", "reloading"):
                out["down"].append(f"{u} is {st}")
        out["pieces"].append({"endpoint": live_url, "state": "answering"})
    elif installed:
        out["note"] = (f"{addon_id} installed but its API is not answering {DASH} watching Crissa only"
                       if offers else f"{addon_id} installed but offers no API here {DASH} watching Crissa only")
    else:
        out["note"] = f"{addon_id} not installed {DASH} watching Crissa only"
    return out


def links(cfg: dict | None, home, manifests: list[dict], env: dict | None = None, **kw) -> list[dict]:
    """Every add-on the settings expect (addons.slots) or that registered a manifest."""
    a = (cfg or {}).get("addons") or {}
    ids: list[tuple[str, str]] = []
    for s in a.get("slots") or []:
        if isinstance(s, dict) and _ID.fullmatch(str(s.get("id") or "")):
            ids.append((str(s["id"]), str(s.get("title") or "")))
    for m in manifests or []:
        if m.get("id") and m["id"] not in {i for i, _t in ids}:
            ids.append((m["id"], m.get("title") or ""))
    if not enabled(env):
        return [{"id": i, "title": t or i, "installed": None, "live": False, "pieces": [], "down": [],
                 "note": f"add-on link off (CRISSA_ADDON_LINK=0) {DASH} watching Crissa only"} for i, t in ids]
    return [link(i, home, manifests, title=t, **kw) for i, t in ids]


def expected(home, root: Path | None = None) -> "tuple[dict, list[dict]]":
    """(settings with addons.slots, manifests) as the launcher sees them without the panel package (the avatar
    package does not depend on it): the slots of the shipped config/crissa-panel.json and of <home>/panel.json
    (the install's own wins), and every crissa-panel-addon/v1 manifest in <home>/panel.d - only the id, title and
    the units its tiles name. The panel itself passes its own merged settings and lib/crissa_panel_addons'."""
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    slots: list = []
    for f in (root / "config" / "crissa-panel.json", Path(home) / "panel.json"):
        try:
            a = (json.loads(f.read_text(encoding="utf-8")).get("addons") or {})
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(a.get("slots"), list):
            slots = a["slots"]
    mans = []
    try:
        files = sorted((Path(home) / "panel.d").glob("*.json"))
    except OSError:
        files = []
    for f in files:
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict) or m.get("schema") != "crissa-panel-addon/v1" or not _ID.fullmatch(str(m.get("id") or "")):
            continue
        if any(x["id"] == m["id"] for x in mans):
            continue
        tiles = [{"unit": str(t["unit"]), "scope": str(t.get("scope") or "user")} for t in m.get("tiles") or []
                 if isinstance(t, dict) and isinstance(t.get("unit"), str) and re.fullmatch(r"[A-Za-z0-9@._:-]{1,120}", t["unit"])]
        mans.append({"id": m["id"], "title": str(m.get("title") or m["id"])[:80], "file": str(f), "tiles": tiles})
    return {"addons": {"slots": slots}}, mans


def note(ls: list[dict]) -> str:
    """One line for the panel: every add-on's note, or "watching Crissa only" when none is expected."""
    return " · ".join(x["note"] for x in ls) if ls else f"no add-on expected {DASH} watching Crissa only"


def watch_table(ls: list[dict], launcher_alive: bool, panel: bool = True) -> list[dict]:
    """Who watches what on the Crissa side, and who crosses over: only while BOTH sides are live."""
    live = [x for x in ls if x.get("live")]
    cross = [p.get("unit") or p.get("endpoint") for x in live for p in x.get("pieces") or []]
    rows = [{"watcher": "launcher heartbeat", "live": launcher_alive, "watches": "every Crissa piece it started",
             "watched_by": "the Control Panel (a stale beat is named)",
             "cross": cross if launcher_alive else [], "crosses": bool(cross) and launcher_alive}]
    if panel:
        rows.append({"watcher": "Control Panel", "live": True, "watches": "every tile and the launcher's beat",
                     "watched_by": "the launcher (it restarts the panel piece) or systemd (the house unit)",
                     "cross": cross, "crosses": bool(cross)})
    return rows
