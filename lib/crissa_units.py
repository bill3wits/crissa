"""This install's own services: one list of every unit the panel watches, discovered from the machine, never typed
by hand. Lifted from the house's unit registry (2026-10-06, "If it's worth setting up, it's worth watching"): the
panel's tiles and the All OS Off / Turn On All Services switch read this list, so a unit cannot be on one and
missing from the other.

WHICH UNITS ARE THIS INSTALL'S is a set of rules in the panel's settings (lib/crissa_panel_conf.py, key
"registry"), not a list. Each row says which rule hit:
  name      the unit's name starts with one of `name_prefixes` (a fresh install: crissa-*)
  exec      its ExecStart points into one of `exec_markers` (a fresh install: this install's own folder)
  folder    (off unless `unit_dir_rule`) the unit file is installed in `unit_dir`
  agent     (off unless `system_user_rule`) a system unit in `system_unit_dir` that runs as this user
  notes     named in the notes file (a unit with no other mark, e.g. a container)
  starts    a timer / path / socket that starts one of the above
A fresh install therefore discovers ITS OWN services only: nothing of another machine's is named anywhere. A
machine that wants more (the house) widens the rules in its own config file.

Templates (foo@.service), masked units and not-found names stay OUT of the watched set; masked ones are listed.

ROLE: service (enabled, should run; or a oneshot that remains), timer / path / socket, job (something triggers it:
red only when its last run failed), manual (installed, nothing starts it), masked.

WITHOUT A SYSTEMD USER SESSION (a fresh account over ssh, a container) systemctl cannot answer. Discovery then reads
the unit files in `unit_dir` by the same rules and says so (`systemd: unreachable` on every row and in the
registry), so the panel shows what is installed and why it cannot read its state, instead of an empty page.

Notes only DESCRIBE units (label, what, feeds, port, section, expect). They never decide membership except by the
`notes` rule above, and a note for a unit that is not on the machine is reported, not obeyed.
"""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent)) if str(Path(__file__).resolve().parent) not in sys.path else None
import crissa_panel_conf as conf  # noqa: E402

KINDS = (".service", ".timer", ".path", ".socket")
SHOW_PROPS = ("Id", "LoadState", "ActiveState", "SubState", "Type", "FragmentPath", "User", "ExecStart",
              "TriggeredBy", "Triggers", "UnitFileState", "RemainAfterExit", "Description", "Result")
ENABLED_STATES = ("enabled", "enabled-runtime", "linked", "linked-runtime", "alias", "generated")
WATCHED_ROLES = ("service", "timer", "path", "socket", "job", "manual")

Runner = Callable[[list, float], "tuple[int, str, str]"]


@dataclass
class Rules:
    scopes: tuple = ("user",)
    name_prefixes: tuple = ("crissa-",)
    exec_markers: tuple = ()
    unit_dir: str = ""
    unit_dir_rule: bool = False
    system_unit_dir: str = "/etc/systemd/system"
    system_user_rule: bool = False
    notes: str = ""
    state_dir: str = ""
    note_days: int = 7
    addon_units: tuple = ()           # (pattern, add-on id): units an installed add-on's tiles name (crissa_panel_addons)
    user: str = field(default_factory=lambda: os.environ.get("USER") or Path.home().name)

    @classmethod
    def from_conf(cls, cfg: dict | None = None) -> "Rules":
        cfg = conf.load() if cfg is None else cfg
        r = dict(cfg.get("registry") or {})
        return cls(scopes=tuple(r.get("scopes") or ("user",)),
                   name_prefixes=tuple(x for x in r.get("name_prefixes") or () if x),
                   exec_markers=tuple(x for x in r.get("exec_markers") or () if x),
                   unit_dir=str(r.get("unit_dir") or ""),
                   unit_dir_rule=bool(r.get("unit_dir_rule")),
                   system_unit_dir=str(r.get("system_unit_dir") or "/etc/systemd/system"),
                   system_user_rule=bool(r.get("system_user_rule")),
                   notes=str(r.get("notes") or ""),
                   state_dir=str(r.get("state_dir") or ""),
                   note_days=int(r.get("note_days") or 7),
                   addon_units=_addon_units(cfg))

    @property
    def registry_file(self) -> Path:
        return Path(self.state_dir) / "registry.json"

    @property
    def changes_file(self) -> Path:
        return Path(self.state_dir) / "changes.jsonl"

    def describe(self) -> dict:
        return {"scopes": list(self.scopes), "name": list(self.name_prefixes), "exec": list(self.exec_markers),
                "folder": self.unit_dir if self.unit_dir_rule else "",
                "agent": f"system units in {self.system_unit_dir} with User={self.user}" if self.system_user_rule else "",
                "addons": [f"{pat} (add-on {aid})" for pat, aid in self.addon_units]}


def _addon_units(cfg: dict) -> tuple:
    try:
        import crissa_panel_addons
        return crissa_panel_addons.unit_patterns(cfg)
    except Exception:  # noqa: BLE001 - a broken add-on never breaks discovery
        return ()


def default_run(argv: list, timeout: float = 60.0) -> "tuple[int, str, str]":
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:.0f}s"
    except OSError as e:
        return 127, "", f"{type(e).__name__}: {e}"


def sc(scope: str) -> list:
    return ["systemctl", "--user"] if scope == "user" else ["systemctl", "--no-ask-password"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ---- notes ---------------------------------------------------------------------------------------------
def load_notes(rules: Rules | None = None, path: Path | None = None) -> dict:
    rules = rules or Rules.from_conf()
    try:
        d = json.loads(Path(path or rules.notes).read_text(encoding="utf-8")) if (path or rules.notes) else {}
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("notes", {})
    return d


# ---- discovery -----------------------------------------------------------------------------------------
def parse_show(text: str) -> list:
    blocks, cur = [], {}
    for ln in text.splitlines():
        if not ln.strip():
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        k, _, v = ln.partition("=")
        cur[k] = v
    if cur:
        blocks.append(cur)
    return blocks


def show(scope: str, units, run: Runner = default_run, props=SHOW_PROPS) -> dict:
    units = list(dict.fromkeys(units))
    out = {}
    for i in range(0, len(units), 150):
        part = units[i:i + 150]
        rc, txt, _ = run(sc(scope) + ["show"] + [f"-p{p}" for p in props] + part, 60.0)
        for asked, b in zip(part, parse_show(txt)):
            out[asked] = b
    return out


def reachable(scope: str, run: Runner = default_run) -> "tuple[bool, str]":
    """Can systemctl answer in this scope? (False, why) for a session with no user manager."""
    rc, out, err = run(sc(scope) + ["list-unit-files", "--no-legend", "--plain", "--no-pager", "--type=timer"], 15.0)
    if rc == 0:
        return True, ""
    msg = (err or out).strip().splitlines()
    return False, (msg[-1] if msg else f"systemctl rc {rc}")[:200]


def unit_file_names(scope: str, run: Runner = default_run) -> list:
    rc, txt, _ = run(sc(scope) + ["list-unit-files", "--no-legend", "--plain", "--no-pager",
                                  "--type=service,timer,path,socket"], 30.0)
    names = []
    for ln in txt.splitlines():
        parts = ln.split()
        if parts and parts[0].endswith(KINDS) and "@." not in parts[0]:
            names.append(parts[0])
    return sorted(set(names))


# A PROGRAM THE PERSON LAUNCHED IS NOT A SERVICE (the User 2026-10-26, the crissatest walk of 1.0.1: the Control Panel's
# unit list carried one red row, `app-…install.sh@<id>.service` failed/failed - the desktop's transient unit for the
# ./install.sh he had clicked, claimed by the exec-marker rule because its program lives in the install's folder).
# systemd names such units `app-<launcher>-<program>@<id>.service` / `.scope` (the desktop, systemd-run, a terminal);
# they are one run, not this install's. Kill CRISSA_UNITS_SKIP_APP=0.
def transient_app_unit(name: str, env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_UNITS_SKIP_APP", "1")).strip().lower() in ("0", "off", "false", "no"):
        return False
    n = str(name or "")
    return n.startswith("app-") and ("@" in n or n.endswith(".scope"))


def install_reason(scope: str, name: str, p: dict, notes: dict, rules: Rules) -> str:
    """Why this unit is this install's, or '' when it is not. First matching rule wins."""
    if transient_app_unit(name):
        return ""
    for x in rules.name_prefixes:
        if name.startswith(x):
            return f"name {x}*"
    ex = p.get("ExecStart") or ""
    for m in rules.exec_markers:
        if not m:
            continue
        # Path boundary so /opt/crissa does not claim /opt/crissa-dev (same / rule as unit_dir below).
        # Markers that end with "-" or "_" stay intentional prefix matches.
        if m[-1] in "-_":
            hit = m in ex
        else:
            hit = (m.rstrip("/") + "/") in (ex.rstrip("/") + "/")
        if hit:
            return f"exec into {m}"
    frag = p.get("FragmentPath") or ""
    if rules.unit_dir_rule and scope == "user" and rules.unit_dir and frag.startswith(rules.unit_dir.rstrip("/") + "/"):
        return f"installed in {rules.unit_dir}"
    if rules.system_user_rule and scope == "system" and frag.startswith(rules.system_unit_dir.rstrip("/") + "/") \
            and (p.get("User") or "") == rules.user:
        return f"system unit run as {rules.user}"
    if name in notes:
        return "named in the unit notes"
    for pat, aid in rules.addon_units:
        if fnmatch.fnmatchcase(name, pat):
            return f"add-on {aid}"
    return ""


def _role(name: str, p: dict) -> str:
    if (p.get("LoadState") or "") == "masked":
        return "masked"
    for k in (".timer", ".path", ".socket"):
        if name.endswith(k):
            return k[1:]
    if (p.get("TriggeredBy") or "").strip():
        return "job"
    if (p.get("UnitFileState") or "") in ENABLED_STATES:
        return "service"
    if (p.get("ActiveState") or "") in ("active", "activating", "reloading"):
        return "service"
    return "manual"


def _expect(role: str, p: dict) -> str:
    enabled = (p.get("UnitFileState") or "") in ENABLED_STATES
    if role == "service":
        if p.get("Type") == "oneshot":
            return "exited" if p.get("RemainAfterExit") == "yes" else "any"
        return "running"
    if role in ("timer", "path", "socket"):
        return role if enabled else "off"
    return "any"


def _row(scope: str, uid: str, why: str, p: dict, notes: dict, **extra) -> dict:
    role = _role(uid, p)
    r = {"unit": uid, "scope": scope, "why": why, "role": role,
         "expect": (notes.get(uid) or {}).get("expect") or _expect(role, p),
         "active": p.get("ActiveState") or "unknown", "sub": p.get("SubState") or "",
         "type": p.get("Type") or "", "file_state": p.get("UnitFileState") or "",
         "triggered_by": (p.get("TriggeredBy") or "").split(),
         "description": (p.get("Description") or "")[:200]}
    r.update(extra)
    return r


def read_unit_file(path: Path) -> dict:
    """The few keys file-only discovery needs, from a unit file on disk (no systemd)."""
    p: dict = {"FragmentPath": str(path), "LoadState": "loaded", "ActiveState": "unknown"}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return p
    sec = ""
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith(("#", ";")):
            continue
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1]
            continue
        k, _, v = s.partition("=")
        k, v = k.strip(), v.strip()
        if sec == "Service" and k == "ExecStart":
            p["ExecStart"] = (p.get("ExecStart", "") + " " + v).strip()
        elif sec == "Service" and k in ("Type", "RemainAfterExit", "User"):
            p[k] = v
        elif sec == "Unit" and k == "Description":
            p["Description"] = v
        elif sec in ("Timer", "Path", "Socket") and k == "Unit":
            p["Triggers"] = v
    if path.name.endswith((".timer", ".path", ".socket")) and "Triggers" not in p:
        p["Triggers"] = path.name.rsplit(".", 1)[0] + ".service"
    return p


def discover_files(rules: Rules | None = None, notes: dict | None = None, why_not: str = "") -> list:
    """Discovery from the unit files in `unit_dir` alone, for a session systemctl cannot reach."""
    rules = rules or Rules.from_conf()
    nd = (load_notes(rules) if notes is None else notes).get("notes") or {}
    d = Path(rules.unit_dir) if rules.unit_dir else None
    if d is None or not d.is_dir():
        return []
    files = sorted(f for f in d.iterdir() if f.name.endswith(KINDS) and "@." not in f.name and f.is_file())
    props = {f.name: read_unit_file(f) for f in files}
    wanted = {w.name for w in d.glob("*.wants/*")}          # enabled = linked into a .wants folder
    for n, p in props.items():
        p["UnitFileState"] = "enabled" if n in wanted else "disabled"
    whys = {n: install_reason("user", n, p, nd, rules) for n, p in props.items()}
    for n, p in props.items():
        if not whys[n] and n.endswith((".timer", ".path", ".socket")) and whys.get(p.get("Triggers", "")):
            whys[n] = f"starts {p['Triggers']}"
    rows = []
    for n, p in props.items():
        if whys[n]:
            rows.append(_row("user", n, whys[n], p, nd, systemd="unreachable", systemd_why=why_not))
    return sorted(rows, key=lambda r: (r["scope"], r["unit"]))


def discover(run: Runner = default_run, notes: dict | None = None, rules: Rules | None = None) -> list:
    """Every unit of this install on the machine, read-only. One row each, sorted, aliases folded."""
    rules = rules or Rules.from_conf()
    nd = (load_notes(rules) if notes is None else notes).get("notes") or {}
    rows, seen = [], set()
    for scope in rules.scopes:
        ok, why_not = reachable(scope, run)
        if not ok:
            if scope == "user":
                rows += discover_files(rules, {"notes": nd}, why_not)
            continue
        names = unit_file_names(scope, run)
        noted = [n for n, v in nd.items() if (v.get("scope") or "user") == scope and n not in names]
        noted += [pat for pat, _ in rules.addon_units if scope == "user" and not any(c in pat for c in "*?[")
                  and pat not in names and pat not in noted]
        props = show(scope, names + noted, run)
        whys = {n: install_reason(scope, n, props.get(n) or {}, nd, rules) for n in names + noted}
        for n in names:
            if not whys[n] and n.endswith((".timer", ".path", ".socket")):
                hit = [t for t in (props.get(n) or {}).get("Triggers", "").split() if whys.get(t)]
                if hit:
                    whys[n] = f"starts {hit[0]}"
        for n in names + noted:
            p = props.get(n) or {}
            why = whys[n]
            if not why:
                continue
            uid = p.get("Id") or n
            if (p.get("LoadState") or "unknown") == "not-found":
                continue
            if (scope, uid) in seen:
                continue
            seen.add((scope, uid))
            rows.append(_row(scope, uid, why, p, nd))
    rows.sort(key=lambda r: (r["scope"], r["unit"]))
    return rows


# ---- the registry file + change notes ------------------------------------------------------------------
def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load(path: Path | None = None, rules: Rules | None = None) -> dict:
    rules = rules or (None if path else Rules.from_conf())
    d = _read(Path(path or rules.registry_file))
    d.setdefault("units", [])
    d.setdefault("changes", [])
    return d


def merge(old: dict, found: list, *, by: str, rules: Rules, now: Optional[str] = None) -> "tuple[dict, list]":
    """New registry from what was found. New units get first_seen + a `new` note; units that vanished are culled
    with a `removed` note. Returns (registry, changes made this run)."""
    now = now or _now()
    before = {(r["scope"], r["unit"]): r for r in old.get("units") or []}
    after, changes = {}, []
    for r in found:
        k = (r["scope"], r["unit"])
        prev = before.get(k)
        row = dict(r, first_seen=(prev or {}).get("first_seen") or now, last_seen=now)
        if prev is None and before:
            row["new_since"] = now
            changes.append({"ts": now, "change": "new", "unit": r["unit"], "scope": r["scope"],
                            "role": r["role"], "why": r["why"], "by": by})
        elif prev is not None and prev.get("new_since"):
            row["new_since"] = prev["new_since"]
        after[k] = row
    for k, prev in before.items():
        if k not in after:
            changes.append({"ts": now, "change": "removed", "unit": prev["unit"], "scope": prev["scope"],
                            "role": prev.get("role"), "last_seen": prev.get("last_seen"), "by": by})
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - rules.note_days * 86400))
    kept = [c for c in old.get("changes") or [] if str(c.get("ts") or "") >= cutoff]
    for r in after.values():
        if r.get("new_since") and r["new_since"] < cutoff:
            r.pop("new_since")
    reg = {"schema": "crissa_units/v1", "updated": now, "by": by, "rules": rules.describe(),
           "file_names": old.get("file_names") or {},
           "systemd": "unreachable" if any(r.get("systemd") == "unreachable" for r in found) else "ok",
           "units": sorted(after.values(), key=lambda r: (r["scope"], r["unit"])),
           "changes": kept + changes}
    return reg, changes


def refresh(run: Runner = default_run, *, by: str = "manual", rules: Rules | None = None,
            notes: dict | None = None) -> dict:
    rules = rules or Rules.from_conf()
    path = rules.registry_file
    old = load(path)
    found = discover(run, notes, rules)
    reg, changes = merge(old, found, by=by, rules=rules)
    reg["file_names"] = {s: unit_file_names(s, run) for s in rules.scopes}
    _write(path, reg)
    if changes:
        try:
            with rules.changes_file.open("a", encoding="utf-8") as fh:
                for c in changes:
                    fh.write(json.dumps(c) + "\n")
        except OSError:
            pass
    return {"ok": True, "n": len(reg["units"]), "systemd": reg["systemd"],
            "new": [c["unit"] for c in changes if c["change"] == "new"],
            "removed": [c["unit"] for c in changes if c["change"] == "removed"]}


def cheap_check(run: Runner = default_run, *, rules: Rules | None = None) -> dict:
    """A cheap check for a poll: list-unit-files per scope; full refresh only when the names changed."""
    rules = rules or Rules.from_conf()
    old = load(rules.registry_file)
    now = {s: unit_file_names(s, run) for s in rules.scopes}
    same_rules = (old.get("rules") or {}).get("addons", []) == rules.describe()["addons"]   # an add-on came or went
    if old.get("units") and old.get("file_names") == now and old.get("systemd") != "unreachable" and same_rules:
        return {"ok": True, "changed": False, "n": len(old["units"])}
    r = refresh(run, by="panel check", rules=rules)
    r["changed"] = True
    return r


def missing(found: list, reg: dict) -> list:
    listed = {(r.get("scope"), r.get("unit")) for r in reg.get("units") or []}
    return sorted(f"{r['scope']}:{r['unit']}" for r in found if (r["scope"], r["unit"]) not in listed)


# ---- views ---------------------------------------------------------------------------------------------
def units(reg: dict, *, roles=WATCHED_ROLES) -> list:
    return [r for r in reg.get("units") or [] if r.get("role") in roles]


def recent_changes(reg: dict) -> list:
    return list(reg.get("changes") or [])


def notes_without_units(reg: dict, notes: dict) -> list:
    have = {r["unit"] for r in reg.get("units") or []}
    return sorted(u for u in (notes.get("notes") or {}) if u not in have)


def panel_tiles(reg: dict, notes: dict, *, tiled: set = frozenset(), default_section: str = "Services",
                new_section: str = "New · not yet sorted") -> list:
    """Every long-running service of this install the curated tiles do not cover, as tiles. A unit found in
    the last few days sits under `new_section` with its find date; the rest under its note's section or
    `default_section`. Timers and jobs are listed on the registry card, not as tiles."""
    nd = notes.get("notes") or {}
    out = []
    for r in units(reg, roles=("service", "manual")):
        if r.get("role") == "manual" and r.get("active") not in ("active", "activating") \
                and r.get("systemd") != "unreachable":     # unread state: show it, never hide it
            continue
        b = conf.bare(r["unit"])
        if b in tiled or r["unit"] in tiled:
            continue
        n = nd.get(r["unit"]) or {}
        out.append({"name": b, "unit": r["unit"], "label": n.get("label") or b, "port": n.get("port"),
                    "section": new_section if r.get("new_since") else (n.get("section") or default_section),
                    "scope": r["scope"], "new_since": r.get("new_since"), "why": r.get("why"),
                    "systemd": r.get("systemd") or "ok"})
    return out


def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="crissa-units", description="this install's units, discovered")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="discover and print, write nothing")
    sub.add_parser("refresh", help="discover and write the registry (new/removed notes)")
    sub.add_parser("check", help="the cheap check")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for r in discover():
            print(f"{r['scope']:6} {r['role']:7} {r['unit']:46} {r['active']:9} {r['why']}")
        return 0
    print(json.dumps(refresh(by="cli") if a.cmd == "refresh" else cheap_check()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
