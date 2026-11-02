"""EACH DAY: a skin per weekday, switched at the first quiet moment of the day (the User 2026-10-31: "say a user buys
one, how do THEY choose which Crissa to run that day?"; crissa-6a, MP store "a buyer's choice of skin").

Choose skin (lib/crissa_avatar) keeps its one pick. This adds a week beside it: one library per weekday, blank = keep
whatever she wears. The Control Panel's ticker calls tick(); on a new day it looks at today's entry and, when it names
another library, switches through the SAME pick path a person's pick takes (crissa_avatar.choose + the player lane's
restart) - but only once she is quiet: no line on air, no conversation in progress, not inside Setup. Busy, it waits and
asks again on the next tick; it never cuts her mid-line. A day is done once: switched, nothing to do (blank, already
worn, the library gone), or a person picked by hand that day - a manual pick works exactly as today and is never undone.

The week is ONE file, <state home>/avatar-days.json. Every number is config/crissa-skin.json's.
Switch CRISSA_SKIN_DAYS: ON in a pack, never on the house (the User 2026-10-31: "On, whatever passes"); =0 = no week in
/v1/avatar, no ticker.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import crissa_home

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "crissa-skin.json"
SWITCH = "CRISSA_SKIN_DAYS"
STATE_FILE = "avatar-days.json"
WEEK = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
BY = "each day"
EAR_BUSY_KEYS = ("speaking", "dispatch_busy", "session_open")     # the ear bridge's /health (lib/restart_window's set)


def enabled(env: dict | None = None) -> bool:
    """ON in a pack, OFF on the house (the User 2026-10-31: "On, whatever passes"); CRISSA_SKIN_DAYS=0 turns it off."""
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get(SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))["days"]


def state_path(home: Path | None = None) -> Path:
    return (home or crissa_home.HOME) / STATE_FILE


def load(home: Path | None = None) -> dict:
    try:
        d = json.loads(state_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    days = d.get("days") if isinstance(d.get("days"), dict) else {}
    d["days"] = {w: str(days.get(w) or "") for w in WEEK}
    return d


def _save(d: dict, home: Path | None = None) -> None:
    p = state_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".avatar-days-", suffix=".json", dir=str(p.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, p)


def save_week(days: dict, libraries: list[dict], home: Path | None = None) -> dict:
    """Save the week. Each weekday names an installed library or is blank (keep the current skin)."""
    if not isinstance(days, dict) or set(days) - set(WEEK):
        return {"ok": False, "error": "a week is mon..sun"}
    have = {l["key"] for l in libraries}
    clean = {}
    for w in WEEK:
        k = str(days.get(w) or "").strip().lower()
        if k and k not in have:
            return {"ok": False, "error": "%s names %r, which is not installed" % (w, k)}
        clean[w] = k
    d = load(home)
    d["days"] = clean
    d["saved"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save(d, home)
    return {"ok": True, "days": clean}


def view(home: Path | None = None) -> dict:
    """What the panel draws beside Choose skin: the week, today's weekday, what the switch did or waits on."""
    d = load(home)
    return {"days": d["days"], "today": WEEK[time.localtime().tm_wday], "applied": d.get("applied", ""),
            "last": d.get("last") or {}, "waiting": d.get("waiting") or ""}


# ── is she busy? the kit's own signals ───────────────────────────────────────────

def _get(url: str, timeout: float):
    """(answered, body): (False, None) when nothing listens there (not running: holds nothing); raises on anything
    else (a late or broken answer is 'cannot tell')."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return True, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.URLError as e:
        if isinstance(e.reason, ConnectionRefusedError):
            return False, None
        raise
    except ConnectionRefusedError:
        return False, None


def busy_reason(home: Path | None = None, env: dict | None = None) -> str:
    """Why a skin switch must wait now; '' = she is quiet. Setup running, a line on air, a conversation in progress
    (the ear speaking, a turn with her brain, a hear session open) - or a piece that answers but cannot say."""
    try:
        run = json.loads(((home or crissa_home.HOME) / "init-run.json").read_text(encoding="utf-8"))
        if isinstance(run, dict) and run.get("status") == "running":
            return "Setup is running"
    except (OSError, ValueError):
        pass
    import crissa_ports
    t = float(config()["probe_timeout_s"])
    try:
        up, st = _get(crissa_ports.url("player", env) + "/v1/state", t)
    except (OSError, ValueError, socket.timeout) as e:
        return "cannot tell: the player did not answer (%s)" % type(e).__name__
    if up and isinstance(st, dict) and (st.get("on_air") or (st.get("transport") or {}).get("on_air")):
        return "a line is on air"
    try:
        up, ear = _get(crissa_ports.url("bridge", env) + "/health", t)
    except (OSError, ValueError, socket.timeout) as e:
        return "cannot tell: the ear did not answer (%s)" % type(e).__name__
    if up:
        if not isinstance(ear, dict) or not any(k in ear for k in EAR_BUSY_KEYS):
            return "cannot tell: the ear's health carries none of " + "/".join(EAR_BUSY_KEYS)
        for key, word in (("speaking", "she is speaking"), ("dispatch_busy", "a turn is with her brain"),
                          ("session_open", "a conversation is open")):
            if ear.get(key):
                return word
    return ""


# ── the day ──────────────────────────────────────────────────────────────────────

def tick(home: Path | None = None, *, now: float | None = None, busy=None, wear=None, libraries=None,
         choice=None) -> dict:
    """One look at today. `wear(key)` switches (the panel's own pick path), `busy()` says why to wait ('' = quiet);
    `libraries()` / `choice()` default to crissa_avatar's. Answers what happened."""
    import crissa_avatar
    t = time.time() if now is None else now
    lt = time.localtime(t)
    today, wd = time.strftime("%Y-%m-%d", lt), WEEK[lt.tm_wday]
    d = load(home)
    if d.get("applied") == today:
        return {"done": today}

    def close(what: str, **more) -> dict:
        d.update(applied=today, waiting="", last=dict({"day": today, "weekday": wd, "what": what,
                                                        "at": time.strftime("%Y-%m-%dT%H:%M:%S", lt)}, **more))
        _save(d, home)
        return dict(d["last"], done=today)

    want = d["days"].get(wd) or ""
    if not want:
        return close("blank: keep the current skin")
    rule = crissa_avatar.offer_rule()
    libs = {l["key"] for l in (libraries() if libraries else
                               [x for x in crissa_avatar.libraries(home=home) if crissa_avatar.offered(x, rule)])}
    if want not in libs:
        return close("%s is not installed (or not offered) any more: kept the current skin" % want, key=want)
    c = (choice() if choice else crissa_avatar.choice(home)) or {}
    if c.get("key") == want:
        return close("already wearing %s" % want, key=want)
    if c.get("at", "").startswith(today) and c.get("by") != BY:
        return close("picked by hand today (%s): kept" % c.get("key"), key=want)
    why = (busy or (lambda: busy_reason(home)))()
    if why:
        if d.get("waiting") != why:
            d["waiting"] = why
            _save(d, home)
        return {"waiting": why, "key": want, "day": today}
    res = wear(want) if wear else {"ok": False, "error": "no pick path"}
    return close("switched to %s" % want if (res or {}).get("ok") else "the switch was refused: %s" % (res or {}).get("error"),
                 key=want, ok=bool((res or {}).get("ok")))
