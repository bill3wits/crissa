"""Host desktop session unlock — Face-grab *trigger*, not AUTH.

Phase I (2026-08-24): unlock does not grant A. rail-presence watches
locked→unlocked and runs a 0.5 s face burst. This module only *reads*
the unlocked signal so evaluate_turn can report host_unlocked.

Resolution order (first decisive wins):
  1. Explicit presence / user block fields (SoT publish or test fixture)
  2. Env HOUSE_HOST_UNLOCKED / HOUSE_SESSION_UNLOCKED (ops + unit tests)
  3. Optional OS probe (loginctl LockedHint) when HOUSE_HOST_UNLOCK_PROBE=1

Pure readers stay free of side effects so evaluate_turn stays unit-testable.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any


def _truthy(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on", "unlocked", "active"):
        return True
    if s in ("0", "false", "no", "off", "locked", "inactive"):
        return False
    return None


def unlock_from_presence(presence: dict | None) -> bool | None:
    """Read unlock signal from presence SoT if published.

    Returns True / False when the field is present, else None (unknown).
    """
    presence = presence if isinstance(presence, dict) else {}
    user = presence.get("user") if isinstance(presence.get("user"), dict) else {}
    for key in (
        "host_unlocked",
        "session_unlocked",
        "desktop_unlocked",
        "host_session_unlocked",
    ):
        if key in user:
            t = _truthy(user.get(key))
            if t is not None:
                return t
        if key in presence:
            t = _truthy(presence.get(key))
            if t is not None:
                return t
    host = presence.get("host") if isinstance(presence.get("host"), dict) else {}
    if host:
        for key in ("unlocked", "session_unlocked", "desktop_unlocked"):
            if key in host:
                t = _truthy(host.get(key))
                if t is not None:
                    return t
    return None


def unlock_from_env() -> bool | None:
    for key in ("HOUSE_HOST_UNLOCKED", "HOUSE_SESSION_UNLOCKED"):
        if key in os.environ:
            t = _truthy(os.environ.get(key))
            if t is not None:
                return t
    return None


def unlock_from_os_probe() -> bool | None:
    """Best-effort: loginctl show-session $XDG_SESSION_ID -p LockedHint.

    Only runs when HOUSE_HOST_UNLOCK_PROBE is truthy. Fail-open as unknown
    (None) — never invent locked/unlocked on probe errors.
    """
    if os.environ.get("HOUSE_HOST_UNLOCK_PROBE", "0").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return None
    sid = (os.environ.get("XDG_SESSION_ID") or "").strip()
    try:
        if not sid:
            uid = os.environ.get("USER") or os.environ.get("LOGNAME") or "agent"
            sid = subprocess.check_output(
                ["loginctl", "show-user", uid, "-p", "Display", "--value"],
                stderr=subprocess.DEVNULL,
                timeout=1.5,
                text=True,
            ).strip()
        if not sid:
            return None
        out = subprocess.check_output(
            ["loginctl", "show-session", sid, "-p", "LockedHint", "--value"],
            stderr=subprocess.DEVNULL,
            timeout=1.5,
            text=True,
        ).strip().lower()
    except Exception:
        return None
    if out in ("yes", "true", "1"):
        return False  # LockedHint=yes → locked → not unlocked
    if out in ("no", "false", "0"):
        return True
    return None


def host_unlocked(
    presence: dict | None = None,
    *,
    probe: bool | None = None,
) -> bool:
    """True when the host session is unlocked (User-assumed present for A).

    Default: False when no signal (fail closed for A-from-unlock only;
    face sticky remains independent).
    """
    u = unlock_from_presence(presence)
    if u is not None:
        return bool(u)
    u = unlock_from_env()
    if u is not None:
        return bool(u)
    do_probe = (
        probe
        if probe is not None
        else os.environ.get("HOUSE_HOST_UNLOCK_PROBE", "0").strip().lower()
        in ("1", "true", "yes", "on")
    )
    if do_probe:
        u = unlock_from_os_probe()
        if u is not None:
            return bool(u)
    return False
