"""Which check this machine runs (2026-10-01).

FF is the face check: is the person at the screen the enrolled user. Secure mode
is a stricter hold a Linux install may offer. A Mac always runs FF and is never
offered Secure mode. Voice is only a backup for FF: it may confirm the user when
the face check cannot see (no camera, dark room), never overrule a face that
does not match.
"""
from __future__ import annotations

import sys


def platform_name(platform: str | None = None) -> str:
    p = sys.platform if platform is None else platform
    return "mac" if p == "darwin" else "linux" if p.startswith("linux") else "other"


def secure_offered(platform: str | None = None) -> bool:
    """Only a Linux install may be offered Secure mode. Never a Mac."""
    return platform_name(platform) == "linux"


def mode(wanted: str | None = None, platform: str | None = None) -> str:
    """'ff' or 'secure'. A request for Secure where it is not offered is FF, quietly."""
    return "secure" if (str(wanted or "").lower() == "secure" and secure_offered(platform)) else "ff"


MATCH = 0.45     # the measured gap: genuine min 0.540 / impostor max 0.354 on AuraFace (lib/crissa_presence.MATCH)


def decide(face: dict | None, voice_ok: bool | None = None, threshold: float = MATCH) -> dict:
    """The FF answer for one look.

    face: {"seen": bool, "sim": float | None} from the face check, or None when it
    could not look at all. voice_ok: the voice backup's answer, or None.
    """
    if face and face.get("seen"):
        sim = face.get("sim")
        if sim is None:                       # a face, no identity engine: presence only
            return {"user": None, "by": "presence", "sim": None}
        return {"user": bool(sim >= threshold), "by": "face", "sim": round(float(sim), 3)}
    if voice_ok is True:                      # the backup: only when the face check saw nobody
        return {"user": True, "by": "voice", "sim": None}
    return {"user": False if face is not None else None, "by": "none", "sim": None}
