"""What Register's start may carry from a page (the User 2026-10-04, Init v2).

The :8099 `enroll_start` action forwarded the page's payload to the coach as sent, so a caller could choose a mode
(face / voice / full) or `replace` that never reaches Register's User-mode gate. Every real caller - the wall's Init,
the Control Panel's Register an ID, the Brave glass - sends voice-register with the keys below. Kill:
HOUSE_ENROLL_PAYLOAD_WHITELIST=0 (the payload is forwarded as sent, as before).
"""
from __future__ import annotations

import os

KEYS = ("mode", "display", "person_id", "station", "create", "first_boot")
MODE = "voice-register"


def enabled() -> bool:
    return os.environ.get("HOUSE_ENROLL_PAYLOAD_WHITELIST", "1").strip().lower() not in ("0", "false", "no", "off")


def clean(payload, default: dict) -> tuple[dict | None, str]:
    """(payload to forward, "") or (None, why). The server's own default fills what the page did not say."""
    if not enabled():
        return (payload or default), ""
    got = payload if isinstance(payload, dict) else {}
    if str(got.get("mode") or MODE) != MODE:
        return None, "Register runs voice-register only from here"
    return dict(default, **{k: got[k] for k in KEYS if k in got}), ""
