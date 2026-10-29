"""The Control Panel's settings: one machine's names, ports, units and GPU roster live in config, never in code.

The User 2026-10-06: anything specific to one machine lives in config or Init. The panel's code names no unit,
no port and no folder of any one install. It reads them here, first hit wins per key (dicts merge one level):

  1. the environment: CRISSA_PANEL_CONFIG (a JSON file that wins over everything below),
     CRISSA_PORT_PANEL (the port), CRISSA_HOME (where the state lives)
  2. the install's own settings: <state home>/panel.json (Init writes it; a person may edit it)
  3. this machine's file: config/house-machine.json, key "panel" (the house keeps its values there; no package
     ships that file, so a fresh install never reads it)
  4. the shipped defaults: config/crissa-panel.json (a fresh install's own services: units named crissa-*, or
     units whose program lives in this install's folder)

Strings may carry placeholders, expanded once when read:
  {root}    this install's folder (the one holding lib/ and scripts/)
  {state}   the state home (lib/crissa_home)
  {home}    the user's home folder
  {python}  the interpreter running the panel
so a machine's file names its own folders without any code spelling them.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "config" / "crissa-panel.json"
HOUSE_MACHINE = ROOT / "config" / "house-machine.json"

# What the code falls back to when even the shipped file is missing (a stripped install still starts).
BUILTIN: dict = {
    "host": "127.0.0.1",
    "port": 8097,
    "self_unit": "crissa-panel.service",
    "self_label": "Control Panel - this screen",
    "title": "Control Panel",
    "registry": {
        "scopes": ["user"],
        "name_prefixes": ["crissa-"],
        "exec_markers": ["{root}"],
        "unit_dir": "{home}/.config/systemd/user",
        "unit_dir_rule": False,
        "system_unit_dir": "/etc/systemd/system",
        "system_user_rule": False,
        "notes": "{state}/unit-notes.json",
        "state_dir": "{state}/units",
        "note_days": 7,
    },
    "tiles": [],
    "default_section": "Services",
    "new_section": "New · not yet sorted",
    "restarters": [],
    "os_power": {
        "state_dir": "{state}/panel",
        "snapshot": "os-off.snapshot.json",
        "report": "os-power.last.json",
        "log": "{state}/panel/os-power.log",
        "switch_name": "All OS Off",
        "on_name": "Turn On All Services",
    },
    "gpu": {},
    "refuse_env": [],
    "lock_file": "",
    "cron_exec_prefixes": ["{root}/"],
    "doc": {"ttl_s": 900, "window": True, "max_lines": 400, "max_chars": 40000},
    "journal_lines": 80,
    "script_max_bytes": 200000,
    "script_roots": ["{root}", "{home}/.config/systemd", "{home}/.local/bin", "/etc/systemd", "/usr/lib/systemd"],
    "addons": {"section": "Add-ons", "paths": ["{state}/panel.d"], "slots": []},   # the bottom section's slot
}


def _state_home(env) -> Path:
    try:
        sys.path.insert(0, str(ROOT / "lib")) if str(ROOT / "lib") not in sys.path else None
        import crissa_home
        if env is os.environ:
            return Path(crissa_home.HOME)
        return Path(crissa_home.home(env))
    except Exception:  # noqa: BLE001 - the panel must start even with a broken state module
        got = str(env.get("CRISSA_HOME") or "").strip()
        return Path(got).expanduser() if got else Path.home() / ".crissa"


def _read(path: Path, key: str | None = None) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if key is not None:
        d = d.get(key) if isinstance(d, dict) else None
    return d if isinstance(d, dict) else {}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            nv = dict(out[k])
            nv.update({kk: vv for kk, vv in v.items() if not str(kk).startswith("_")})
            out[k] = nv
        else:
            out[k] = copy.deepcopy(v)
    return out


def _expand(v, subs: dict):
    if isinstance(v, str):
        for k, s in subs.items():
            v = v.replace("{" + k + "}", s)
        return v
    if isinstance(v, list):
        return [_expand(x, subs) for x in v]
    if isinstance(v, dict):
        return {k: _expand(x, subs) for k, x in v.items()}
    return v


def load(env=None, *, house: Path | None = None, shipped: Path | None = None,
         install: Path | None = None) -> dict:
    """The merged, expanded settings. Arguments are for tests; callers use load()."""
    env = os.environ if env is None else env
    state = _state_home(env)
    layers = [BUILTIN, _read(shipped or SHIPPED), _read(house or HOUSE_MACHINE, "panel"),
              _read(install or (state / "panel.json"))]
    extra = str(env.get("CRISSA_PANEL_CONFIG") or "").strip()
    if extra:
        layers.append(_read(Path(extra).expanduser()))
    cfg: dict = {}
    for layer in layers:
        cfg = _merge(cfg, layer)
    sources = ["builtin"] + [n for n, layer in zip(("shipped", "house", "install", "env"), layers[1:]) if layer]
    port = str(env.get("CRISSA_PORT_PANEL") or "").strip()
    if port.isdigit() and 0 < int(port) < 65536:
        cfg["port"] = int(port)
    else:
        try:
            off = int(str(env.get("CRISSA_PORT_OFFSET") or "0").strip())
        except ValueError:
            off = 0
        if off:
            cfg["port"] = int(cfg.get("port") or BUILTIN["port"]) + off
    subs = {"root": str(ROOT), "state": str(state), "home": str(Path.home()), "python": sys.executable}
    cfg = _expand(cfg, subs)
    cfg["_sources"] = sources
    cfg["_state"] = str(state)
    cfg["_root"] = str(ROOT)
    return cfg


def bare(unit: str) -> str:
    u = str(unit or "")
    return u[:-8] if u.endswith(".service") else u


def full(unit: str) -> str:
    u = str(unit or "")
    return u if u.endswith((".service", ".timer", ".path", ".socket")) else u + ".service"
