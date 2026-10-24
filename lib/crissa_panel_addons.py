"""The Control Panel's add-on slot: the bottom section, where an installed add-on registers its own tiles.

The User 2026-10-06: one Control Panel, in the Wall; an agent framework adds only its few extra tiles at the
bottom, and the Wall says so when its plugin is not installed, the way the clock and the door feed show a small
mark when their camera is missing. So:

  * The panel names no add-on in code. Where add-ons drop their manifests, and which add-ons the bottom section
    expects, are settings (lib/crissa_panel_conf.py, key "addons"):

        "addons": {
          "section": "Add-ons",                       # the bottom section's heading
          "paths": ["{state}/panel.d"],               # every *.json here is one add-on's manifest
          "slots": [{"id": "...", "title": "...", "missing": "... not installed"}]
        }

  * A manifest (one JSON file, written by the add-on's own installer, removed by its uninstaller):

        {"schema": "crissa-panel-addon/v1", "id": "<slot id>", "title": "<heading words>", "by": "<package>",
         "tiles": [
           {"unit": "<name>.service", "label": "Name - what it is", "port": 1234, "href": "http://127.0.0.1:1234/"},
           {"match": "<prefix>-*-<suffix>.service", "label": "{unit}"},          # one tile per unit that matches
           {"label": "Name - a page", "href": "http://127.0.0.1:1234/"},          # a link, no unit
           {"label": "Name - a report", "status": "/path/to/report.json", "stale_s": 900, "unit": "x.timer"}
         ]}

    A unit tile is a full tile: its state, journal, script and Restart/Start/Stop, and All OS Off covers it (its
    units count as this install's, "add-on <id>"). A status tile paints the `level` (ok|warn|bad) and `headline`
    of a JSON file the add-on writes, judged stale here (a reporter that stopped cannot say so itself).

  * A slot with no manifest is a placeholder tile: the off mark and a few plain words (the same marks as
    lib/crissa_status.py: ● ok, ◐ fallback, ○ off). Like the clock's "no beam sig": no key, no error, nothing lit,
    and it is not counted as a service.

A manifest that cannot be read, or a tile that breaks a rule, is named in `errors` and skipped; the panel never
fails because of an add-on.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import time
from pathlib import Path

SCHEMA = "crissa-panel-addon/v1"
GLYPH = {"ok": "●", "fallback": "◐", "off": "○"}      # lib/crissa_status.py's marks, the Wall's one vocabulary
DEFAULT_SECTION = "Add-ons"
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
_UNIT = re.compile(r"[A-Za-z0-9@._:-]{1,120}")
_MATCH = re.compile(r"[A-Za-z0-9@._:*?-]{1,120}")
_KINDS = (".service", ".timer", ".path", ".socket")


def settings(cfg: dict | None) -> dict:
    a = dict((cfg or {}).get("addons") or {})
    slots = []
    for s in a.get("slots") or []:
        if isinstance(s, dict) and _ID.fullmatch(str(s.get("id") or "")):
            slots.append({"id": str(s["id"]), "title": str(s.get("title") or s["id"])[:80],
                          "missing": str(s.get("missing") or "not installed")[:160]})
    paths = [str(p) for p in a.get("paths") or [] if str(p or "").strip()]
    return {"section": str(a.get("section") or DEFAULT_SECTION)[:60], "paths": paths, "slots": slots,
            "home_env": str(a.get("home_env") or "")[:40]}


def _full(u: str) -> str:
    return u if u.endswith(_KINDS) else u + ".service"


def _href(v) -> str | None:
    v = str(v or "").strip()
    return v if re.fullmatch(r"https?://[A-Za-z0-9.\-\[\]:]+(/[^\s\"'<>]*)?", v) else None


def checked_unit(name) -> str | None:
    """A unit name the panel may start or disable, or None."""
    name = str(name or "")
    if not _UNIT.fullmatch(name):
        return None
    return _full(name)


def off_file_ok(raw, home_env: str = "") -> str | None:
    """An absolute off-switch file under the add-on's home, or None.

    The home is the directory named by the add-on settings' home_env. A missing home, a relative path,
    a path outside that home, or a symlink anywhere along the path is refused. The file itself does
    not have to exist yet."""
    home = (os.environ.get(home_env) or "").strip() if home_env else ""
    path = str(raw or "").strip()
    if not home or not os.path.isabs(path):
        return None
    try:
        home_r = os.path.realpath(home)
    except OSError:
        return None
    if not os.path.isdir(home_r) or home_r == os.path.sep:
        return None
    abs_path = os.path.abspath(path)
    try:
        if os.path.commonpath([abs_path, home_r]) != home_r:
            return None
    except ValueError:
        return None
    rel = os.path.relpath(abs_path, home_r)
    if rel.startswith(".."):
        return None
    cur = home_r
    for part in rel.split(os.sep):
        if not part or part == ".":
            continue
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            return None
    return abs_path


def _extras(t: dict, i: int, errors: list, where: str, home_env: str = "") -> dict:
    """id, the section the manifest asked for, and run / off keys. A bad key is an errors row and is not drawn.
    The tile itself still draws."""
    extra: dict = {}
    raw_id = t.get("id")
    if raw_id not in (None, ""):
        if _ID.fullmatch(str(raw_id)):
            extra["id"] = str(raw_id)
        else:
            errors.append({"file": where, "why": f"tile {i}: bad id"})
    sec = str(t.get("section") or "").strip()
    if sec:
        extra["want_section"] = sec[:60]
    run = t.get("run")
    if run not in (None, "", {}):
        unit = run.get("unit") if isinstance(run, dict) else None
        full = checked_unit(unit)
        if full:
            extra["run"] = {"unit": full}
        else:
            errors.append({"file": where, "why": f"tile {i}: run needs a unit name"})
    off = t.get("off")
    if off not in (None, "", {}):
        if isinstance(off, dict) and off.get("file"):
            path = off_file_ok(off.get("file"), home_env)
            if path:
                extra["off"] = {"file": path}
                extra["off_now"] = os.path.isfile(path)
            else:
                errors.append({"file": where, "why": f"tile {i}: off file must be an absolute path under the add-on's home"})
        elif isinstance(off, dict) and off.get("unit"):
            full = checked_unit(off.get("unit"))
            if full:
                extra["off"] = {"unit": full}
            else:
                errors.append({"file": where, "why": f"tile {i}: off needs a unit name"})
        else:
            errors.append({"file": where, "why": f"tile {i}: off needs a file or a unit"})
    return extra


def _tile(t, i: int, errors: list, where: str, home_env: str = "") -> dict | None:
    if not isinstance(t, dict):
        errors.append({"file": where, "why": f"tile {i}: not an object"})
        return None
    label = str(t.get("label") or "").strip()[:120]
    out: dict = {"label": label}
    if t.get("href") not in (None, ""):
        h = _href(t.get("href"))
        if h is None:
            errors.append({"file": where, "why": f"tile {i}: href must be an http(s) address"})
            return None
        out["href"] = h
    if t.get("port") not in (None, ""):
        try:
            p = int(t["port"])
            if 0 < p < 65536:
                out["port"] = p
        except (TypeError, ValueError):
            pass
    scope = str(t.get("scope") or "")
    out["scope"] = scope if scope in ("user", "system") else ""
    extra = _extras(t, i, errors, where, home_env)
    if t.get("status"):
        out.update(kind="status", status=str(t["status"]), stale_s=float(t.get("stale_s") or 900))
        if t.get("unit"):
            if not _UNIT.fullmatch(str(t["unit"])):
                errors.append({"file": where, "why": f"tile {i}: bad unit name"})
                return None
            out["unit"] = _full(str(t["unit"]))
        out["label"] = label or "status"
        out.update(extra)
        return out
    if t.get("match"):
        m = str(t["match"])
        if not _MATCH.fullmatch(m) or m.strip("*?") == "":
            errors.append({"file": where, "why": f"tile {i}: bad match pattern"})
            return None
        out.update(kind="match", match=_full(m), label=label or "{unit}")
        out.update(extra)
        return out
    if t.get("unit"):
        u = str(t["unit"])
        if not _UNIT.fullmatch(u):
            errors.append({"file": where, "why": f"tile {i}: bad unit name"})
            return None
        out.update(kind="unit", unit=_full(u), label=label or u)
        out.update(extra)
        return out
    if out.get("href"):
        out.update(kind="link", label=label or out["href"])
        out.update(extra)
        return out
    errors.append({"file": where, "why": f"tile {i}: needs a unit, a match, a status file or an href"})
    return None


def manifests(cfg: dict | None) -> "tuple[list, list]":
    """(manifests, errors). Files are read in name order; the first manifest for an id wins."""
    st = settings(cfg)
    found, errors, seen = [], [], set()
    for d in st["paths"]:
        p = Path(d).expanduser()
        if not p.is_dir():
            continue
        for f in sorted(p.glob("*.json")):
            where = str(f)
            try:
                m = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                errors.append({"file": where, "why": f"cannot read it: {type(e).__name__}"})
                continue
            if not isinstance(m, dict) or m.get("schema") != SCHEMA:
                errors.append({"file": where, "why": f"not a {SCHEMA} manifest"})
                continue
            aid = str(m.get("id") or "")
            if not _ID.fullmatch(aid):
                errors.append({"file": where, "why": "bad or missing id"})
                continue
            if aid in seen:
                errors.append({"file": where, "why": f"a second manifest for {aid} (the first one wins)"})
                continue
            seen.add(aid)
            tiles = [x for x in (_tile(t, i, errors, where, st["home_env"])
                                 for i, t in enumerate(m.get("tiles") or [])) if x]
            sections = []
            for s in m.get("sections") or []:
                name = str(s or "").strip()[:60]
                if name and name not in sections:
                    sections.append(name)
            found.append({"id": aid, "title": str(m.get("title") or aid)[:80], "by": str(m.get("by") or "")[:80],
                          "file": where, "sections": sections, "tiles": tiles})
    return found, errors


def unit_patterns(cfg: dict | None) -> tuple:
    """(pattern, add-on id) for every unit an installed add-on names: they count as this install's units."""
    out = []
    for m in manifests(cfg)[0]:
        for t in m["tiles"]:
            pat = t.get("match") or t.get("unit")
            if pat:
                out.append((pat, m["id"]))
    return tuple(out)


def claims(name: str, patterns) -> str:
    """The add-on id whose tile names this unit, or ''."""
    for pat, aid in patterns or ():
        if fnmatch.fnmatchcase(name, pat):
            return aid
    return ""


def placeholder(slot: dict, section: str) -> dict:
    return {"slot": slot["id"], "label": slot["title"], "note": slot["missing"], "state": "off",
            "mark": GLYPH["off"], "section": section, "placeholder": True}


def read_status(path: str, stale_s: float, now: float | None = None) -> dict:
    """level + headline of an add-on's report file; stale or unreadable is said, never raised."""
    now = time.time() if now is None else now
    try:
        d = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"health": "warn", "note": "no report yet"}
    if not isinstance(d, dict):
        return {"health": "warn", "note": "no report yet"}
    level = str(d.get("level") or "").lower()
    health = level if level in ("ok", "warn", "bad") else "warn"
    note = str(d.get("headline") or "")[:200]
    try:
        age = now - float(d.get("epoch") or d.get("ts") or 0)
    except (TypeError, ValueError):
        age = 0
    if d.get("epoch") or d.get("ts"):
        if age > stale_s:
            return {"health": "bad", "note": f"has not reported for {int(age // 60)} min", "stale": True}
    return {"health": health, "note": note}


def view(cfg: dict | None, reg_units) -> dict:
    """The bottom section: tile specs in manifest order (a match expanded over the registry's units),
    which slots are filled, the placeholders for the rest, and what was refused."""
    st = settings(cfg)
    ms, errors = manifests(cfg)
    names = sorted({str(r.get("unit")) for r in reg_units or [] if r.get("role") not in ("timer", "path", "socket", "job")
                    or str(r.get("unit", "")).endswith(".timer")})
    scope_of = {str(r.get("unit")): r.get("scope") or "" for r in reg_units or []}
    role_of = {str(r.get("unit")): r.get("role") or "" for r in reg_units or []}
    tiles, claimed, sections = [], set(), []

    def _section_for(m, t) -> str:
        listed = m.get("sections") or []
        want = t.get("want_section") or ""
        if listed:
            return want if want in listed else m["title"]
        return st["section"]

    def _keep(name: str) -> None:
        if name and name not in sections:
            sections.append(name)

    for m in ms:
        for t in m["tiles"]:
            base = dict(t, addon=m["id"], section=_section_for(m, t))
            base.pop("want_section", None)
            _keep(base["section"])
            if t["kind"] == "match":
                for n in names:
                    if n in claimed or not fnmatch.fnmatchcase(n, t["match"]):
                        continue
                    claimed.add(n)
                    bare = n[:-8] if n.endswith(".service") else n
                    tiles.append(dict(base, kind="unit", unit=n, scope=scope_of.get(n) or t.get("scope") or "",
                                      masked=role_of.get(n) == "masked", label=t["label"].replace("{unit}", bare)))
                continue
            if t.get("unit"):
                if t["unit"] in claimed:
                    continue
                claimed.add(t["unit"])
                base.update(scope=scope_of.get(t["unit"]) or t.get("scope") or "",
                            masked=role_of.get(t["unit"]) == "masked")
            tiles.append(base)
    have = {m["id"] for m in ms}
    missing = [placeholder(s, st["section"]) for s in st["slots"] if s["id"] not in have]
    for p in missing:
        _keep(p["section"])
    return {"section": st["section"], "sections": sections,
            "installed": [{"id": m["id"], "title": m["title"], "by": m["by"], "tiles": len(m["tiles"])} for m in ms],
            "missing": missing,
            "errors": errors, "tiles": tiles, "claimed": sorted(claimed)}
