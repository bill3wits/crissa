"""Brave's own background traffic off (2026-10-02): a module the wall pack ships.

Measured 10-02 on a normal window: a Brave launched with the wall's chip flags made 59 outbound requests in
5 minutes to Brave's own services (extension and component updates, the variations seed, the spell-check
dictionary), and none with the settings below.

  flags()          the launch flags, or [] with the switch off
  apply(profile)   the profile preferences, written while that Brave is DOWN (a running Brave writes its own
                   values back); only the listed keys are touched; "off" with the switch off

The switch is Crissa-in-the-background's (OFF by default): HOUSE_ZOS_BACKGROUND=1|0 wins, else the file
`<state home>/zos-background.on`. Off, every Brave launches exactly as before.

The house may add or override settings in config/zos-brave.json `quiet_network`, and an install in
`<state home>/zos-brave.json`; without either file these defaults stand.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crissa_home  # noqa: E402

FLAGS = [
    "--disable-background-networking",   # extension, component and variations fetches
    "--disable-component-update",
    "--disable-domain-reliability",
    "--no-pings",
    "--disable-sync",
    "--disable-breakpad",
    "--metrics-recording-only",          # metrics kept local, never sent
]
PREFS = {                                # <profile>/Default/Preferences
    "browser.enable_spellchecking": False,
    "spellcheck.dictionaries": [],
    "spellcheck.use_spelling_service": False,
    "safebrowsing.enabled": False,
    "safebrowsing.enhanced": False,
    "brave.stats.reporting_enabled": False,
}
LOCAL_STATE = {                          # <profile>/Local State
    "brave.p3a.enabled": False,
    "brave.stats.reporting_enabled": False,
    "user_experience_metrics.reporting_enabled": False,
}
CONFIG = Path(__file__).resolve().parent.parent / "config" / "zos-brave.json"


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    v = str(e.get("HOUSE_ZOS_BACKGROUND", "")).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return (crissa_home.HOME / "zos-background.on").exists()


def settings() -> dict:
    """{flags, prefs, local_state}: the defaults above, under config/zos-brave.json and then the state home's
    zos-brave.json (each `quiet_network` key replaces the one before)."""
    out = {"flags": list(FLAGS), "prefs": dict(PREFS), "local_state": dict(LOCAL_STATE)}
    for p in (CONFIG, crissa_home.HOME / "zos-brave.json"):
        try:
            q = (json.loads(p.read_text(encoding="utf-8")) or {}).get("quiet_network") or {}
        except (OSError, ValueError, AttributeError):
            continue
        out.update({k: q[k] for k in ("flags", "prefs", "local_state") if k in q})
    return out


def flags() -> list[str]:
    return [str(f) for f in settings()["flags"]] if enabled() else []


def _set_dotted(d: dict, dotted: str, value) -> None:
    cur = d
    keys = dotted.split(".")
    for k in keys[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = cur[k] = {}
        cur = nxt
    cur[keys[-1]] = value


def _merge_json(path: Path, values: dict) -> None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            d = {}
    except (OSError, ValueError):
        d = {}
    for k, v in values.items():
        _set_dotted(d, k, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".quiet-tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, path)


def apply(profile_dir: str | Path) -> str:
    """Write the preferences into a profile whose Brave is down. Only with the switch on."""
    if not enabled():
        return "off"
    s = settings()
    prof = Path(profile_dir)
    _merge_json(prof / "Default" / "Preferences", dict(s["prefs"]))
    _merge_json(prof / "Local State", dict(s["local_state"]))
    return "quiet"


if __name__ == "__main__":
    verb = sys.argv[1] if len(sys.argv) > 1 else "flags"
    print("\n".join(flags()) if verb == "flags" else apply(sys.argv[2]) if verb == "apply" else json.dumps(settings()))
