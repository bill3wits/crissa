"""Prints of her own voice, as this room's microphone hears it from this room's speakers (round 16 (e), .101 2026-10-13).

The ear's own-voice veto (stamp.tts_veto_check) compares each utterance with prints of her voices - `crissa_her_tts` (her
tier voice), `announcer`, `crew_tts` - under <home>/ear/mouths/<id>/voice/*.json (or identity/people/<id>/voice/). On
.101 there were none, so her echo had nothing to be told apart by, and the User's short lines were judged by text
alone. Setup now records her tier voice and the announcer through the User's speakers and saves them here with
`write_print`. A print is of one voice through one speaker: each carries the `context` it was made in (her tier, the
announcer, the speaker), and when the voice tier or the speaker changes it is STALE - not used, and listed in
<home>/ear/her-prints.json with `ask: true`, which the next Setup or Register visit, or the Control Panel's line, reads
to record them again.

Kill: CRISSA_HER_PRINTS_STALE=0 uses every print whatever it was made with (round 15).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .home import EAR, HOME

KINDS = ("crissa_her_tts", "announcer", "crew_tts")
_VOICE_KEY = {"crissa_her_tts": "tier", "crew_tts": "tier", "announcer": "announcer"}
_CTX_CACHE: dict[str, Any] = {"at": 0.0, "ctx": None}
CTX_TTL_S = 30.0


def stale_check_on() -> bool:
    return os.environ.get("CRISSA_HER_PRINTS_STALE", "1").strip().lower() not in ("0", "false", "no", "off")


def mouths_root() -> Path:
    env = (os.environ.get("HOUSE_MOUTHS_ROOT") or "").strip()
    return Path(env).expanduser() if env else EAR / "mouths"


def report_path() -> Path:
    return EAR / "her-prints.json"


def _speaker() -> str:
    """The speaker she plays on: the echo canceller's physical speaker while it runs, else the default sink."""
    try:
        from . import aec

        st = aec.read_state()
        if st.get("on") and st.get("speaker"):
            return str(st["speaker"])
    except Exception:  # noqa: BLE001
        pass
    try:
        out = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=3)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def context_now(*, fresh: bool = False, home: Path | None = None, speaker: str | None = None) -> dict[str, str]:
    """{"tier", "announcer", "speaker"} now: the voice tier record (voice-tier.json) and her speaker; read at most every
    CTX_TTL_S."""
    now = time.monotonic()
    if not fresh and home is None and speaker is None and _CTX_CACHE["ctx"] is not None \
            and now - _CTX_CACHE["at"] < CTX_TTL_S:
        return dict(_CTX_CACHE["ctx"])
    try:
        rec = json.loads(((home or HOME) / "voice-tier.json").read_text(encoding="utf-8"))
        rec = rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        rec = {}
    tier = str(rec.get("tier") or rec.get("main") or "")
    ctx = {"tier": tier, "announcer": str(rec.get("announcer") or tier), "speaker": _speaker() if speaker is None
           else speaker}
    if home is None and speaker is None:
        _CTX_CACHE.update(at=now, ctx=dict(ctx))
    return ctx


def stale_why(rec: dict, ctx: dict) -> str:
    """'' when the print still holds (or says nothing of how it was made), else why it is stale."""
    made = rec.get("context") if isinstance(rec.get("context"), dict) else None
    if not made:
        return ""
    key = _VOICE_KEY.get(str(rec.get("person_id") or "").strip().lower(), "tier")
    was, now = str(made.get(key) or ""), str(ctx.get(key) or "")
    if was and now and was != now:
        return "her voice changed (%s -> %s)" % (was, now)
    was, now = str(made.get("speaker") or ""), str(ctx.get("speaker") or "")
    if was and now and was != now:
        return "the speaker changed (%s -> %s)" % (was, now)
    return ""


def write_print(mouth_id: str, encoding, *, model: str, context: dict | None = None, label: str = "",
                root: Path | None = None) -> Path:
    """Save one print of her voice for the veto (the Setup step calls this): <mouths>/<id>/voice/<id>-<ms>.json, with
    the context it was made in. `encoding` is the unit-length embedding from the ear's encoder (voice_embed)."""
    mid = str(mouth_id or "").strip().lower()
    if mid not in KINDS:
        raise ValueError("a print of her voice is one of %s, not %r" % (", ".join(KINDS), mouth_id))
    vec = [float(x) for x in list(encoding)]
    d = (root or mouths_root()) / mid / "voice"
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%s-%d.json" % (mid, int(time.time() * 1000)))
    rec = {"person_id": mid, "role": "tts_filter", "encoding": vec, "dim": len(vec), "model": model,
           "context": dict(context if context is not None else context_now(fresh=True)), "label": label,
           "ts": round(time.time(), 3)}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def write_report(found: list[dict], used: list[dict], stale: list[dict], ctx: dict) -> dict:
    """<ear>/her-prints.json: how many prints, how many in use, which are stale and why; `ask` when they need
    recording again (any stale, or none at all)."""
    rep = {"at": round(time.time(), 3), "n": len(found), "used": len(used), "context": ctx,
           "stale": [{"person_id": r.get("person_id"), "file": r.get("_file"), "why": r.get("_stale")} for r in stale],
           "kinds_in_use": sorted({str(r.get("person_id")) for r in used}),
           "ask": bool(stale) or not used}
    try:
        p = report_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        old = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
        if not isinstance(old, dict) or {k: old.get(k) for k in ("n", "used", "stale", "ask")} != \
                {k: rep.get(k) for k in ("n", "used", "stale", "ask")}:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
            os.replace(tmp, p)
    except (OSError, ValueError):
        pass
    return rep


def status() -> dict:
    """The last report, for /health, Setup, Register and the Control Panel."""
    try:
        d = json.loads(report_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}
