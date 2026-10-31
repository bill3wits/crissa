"""Pairing a phone with Crissa on the computer (the User 2026-10-18: the iPhone app pairs with Crissa on your Mac or
PC; plan https://claude.ai/artifact/WPLy6PGawWHZZgZANq3YAg, M1).

The computer shows a 6-digit code (good for CODE_TTL_S, used once). The phone sends it once with a name and gets a
long random token back, which the app keeps in its Keychain; this file keeps only the token's SHA-256, so a copy of
the state file cannot be replayed. With pairing on, the phone server answers another device only with a valid
token; the computer itself (loopback) never needs one. Five wrong codes lock pairing for LOCK_S.

State: <phone home>/pairing.json  {"code": {"sha": ..., "at": epoch}, "devices": {sha: {"name", "paired_at", "seen"}}}
Pure functions over that file, so the server and its tests share one implementation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

CODE_TTL_S = 600
MAX_TRIES = 5
LOCK_S = 300
NAME_MAX = 60
_LOCK = threading.Lock()
_TRIES = {"n": 0, "until": 0.0}


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _load(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, d: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def new_code(path: Path, now: float | None = None) -> dict:
    """A fresh 6-digit code for the computer to show: {"code", "expires_in"}. Any earlier code stops working."""
    now = time.time() if now is None else now
    code = "%06d" % secrets.randbelow(1_000_000)
    with _LOCK:
        d = _load(path)
        d["code"] = {"sha": _sha(code), "at": now}
        _save(path, d)
    return {"code": code, "expires_in": CODE_TTL_S}


def pair(path: Path, code: str, name: str, now: float | None = None) -> dict:
    """The phone's half: {"ok": True, "token"} for the right code inside its time, else {"ok": False, "error"}."""
    now = time.time() if now is None else now
    with _LOCK:
        if _TRIES["until"] > now:
            return {"ok": False, "error": "too many wrong codes; try again in a few minutes", "locked": True}
        d = _load(path)
        c = d.get("code") or {}
        good = (isinstance(c, dict) and c.get("sha") and now - float(c.get("at") or 0) <= CODE_TTL_S
                and hmac.compare_digest(str(c["sha"]), _sha(str(code or "").strip())))
        if not good:
            _TRIES["n"] += 1
            if _TRIES["n"] >= MAX_TRIES:
                _TRIES["n"], _TRIES["until"] = 0, now + LOCK_S
            return {"ok": False, "error": "that code is wrong or has expired; get a new one on your computer"}
        _TRIES["n"] = 0
        token = secrets.token_urlsafe(32)
        d.pop("code", None)                                   # a code pairs one phone, once
        d.setdefault("devices", {})[_sha(token)] = {"name": (str(name or "").strip() or "phone")[:NAME_MAX],
                                                     "paired_at": now, "seen": now}
        _save(path, d)
    return {"ok": True, "token": token}


def grant(path: Path, name: str, via: str = "qr", now: float | None = None) -> str:
    """Pair a phone without a code, for a proof the server already checked (the desk Wall's single-use QR, the User
    2026-10-20: the QR is the whole story for a phone). Returns the new device token."""
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    with _LOCK:
        d = _load(path)
        d.setdefault("devices", {})[_sha(token)] = {"name": (str(name or "").strip() or "phone")[:NAME_MAX],
                                                     "paired_at": now, "seen": now, "via": str(via)[:20]}
        _save(path, d)
    return token


def forget(path: Path, token: str) -> bool:
    """A phone gives its own pairing back (Disconnect). Only the holder of the token can; True when it was paired."""
    if not token:
        return False
    sha = _sha(str(token))
    with _LOCK:
        d = _load(path)
        devs = d.get("devices") or {}
        hit = next((k for k in devs if hmac.compare_digest(k, sha)), None)
        if hit is None:
            return False
        devs.pop(hit)
        d["devices"] = devs
        _save(path, d)
    return True


def check(path: Path, token: str, now: float | None = None) -> bool:
    """Is this token one of the paired phones? Marks it seen (at most once a minute, to keep writes rare)."""
    if not token:
        return False
    now = time.time() if now is None else now
    sha = _sha(str(token))
    with _LOCK:
        d = _load(path)
        devs = d.get("devices") or {}
        hit = next((k for k in devs if hmac.compare_digest(k, sha)), None)
        if hit is None:
            return False
        if now - float(devs[hit].get("seen") or 0) > 60:
            devs[hit]["seen"] = now
            _save(path, d)
    return True


def devices(path: Path) -> list[dict]:
    """The paired phones, for the computer's list: name, paired_at, seen, and an id to unpair by (never the token)."""
    d = _load(path)
    return [dict(v, id=k[:12]) for k, v in sorted((d.get("devices") or {}).items(), key=lambda kv: kv[1].get("paired_at", 0))]


def unpair(path: Path, dev_id: str = "") -> int:
    """Forget one phone (by the id `devices` shows) or, with no id, all of them. Returns how many were forgotten."""
    with _LOCK:
        d = _load(path)
        devs = d.get("devices") or {}
        gone = [k for k in devs if not dev_id or k.startswith(str(dev_id))]
        for k in gone:
            devs.pop(k)
        d["devices"] = devs
        _save(path, d)
    return len(gone)
