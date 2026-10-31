"""Small status marks: every optional piece says how it stands, never an error (2026-10-01).

The Wall's optional pieces (an agent, a voice, an ear, better mouths, the oracle, a
beam microphone, the import folder, the library link) each fall back quietly when the hardware or the
service is missing. This is the one place that says which is which, as a mark:

    ok        ●   the piece is there and working
    fallback  ◐   it is working on its fallback
    off       ○   not set up, or switched off; nothing is wrong

`note` is a few plain words. No mark ever carries an error string, a path or a key.

OFF by default (`CRISSA_STATUS_MARKS=1` turns it on): off, the player has no
/v1/marks route and the Connect page shows no marks row.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request

import crissa_agent
import crissa_egress
import crissa_aligner
import crissa_home
import crissa_import
import crissa_library_sync
import crissa_tts

GLYPH = {"ok": "●", "fallback": "◐", "off": "○"}


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_STATUS_MARKS", "0")).strip().lower() in ("1", "on", "true", "yes")


def _mark(piece: str, state: str, note: str) -> dict:
    return {"piece": piece, "state": state, "mark": GLYPH[state], "note": note}


def _answers(url: str, timeout: float = 0.3) -> bool:
    try:
        with crissa_egress.urlopen(url, timeout, "a status probe") as r:
            return 200 <= r.status < 500
    except (OSError, ValueError):
        return False


def agent_mark() -> dict:
    if not crissa_agent.enabled() or not crissa_agent.configured(crissa_agent.load()):
        return _mark("agent", "off", "no agent connected")
    return _mark("agent", "ok", "connected")


def voice_out_mark(env: dict | None = None) -> dict:
    cfg = crissa_tts.config(env)
    if not cfg:
        if crissa_home.is_house():
            return _mark("voice", "ok", "house voice")
        return _mark("voice", "off", "no voice set")
    try:
        last = json.loads(crissa_tts.VOICE_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    if last.get("fallback") and last.get("named") == cfg.get("engine"):
        return _mark("voice", "fallback", "speaking with %s" % last.get("spoke"))
    if cfg.get("tier_main") and cfg["tier_main"] != cfg.get("engine"):
        # the machine's voice tier is not answering; a lower one speaks (2026-10-06: every fallback is named)
        return _mark("voice", "fallback", "%s is not answering; %s speaks" % (cfg["tier_main"], cfg.get("engine")))
    if cfg["engine"] in ("say", "piper", "kokoro", "multilingual", "turbo", "pocket"):
        return _mark("voice", "ok", "this machine's voice (%s)" % cfg["engine"])
    return _mark("voice", "ok", "speech address")


def ear_mark(env: dict | None = None) -> dict:
    e = os.environ if env is None else env
    turns = str(e.get("CRISSA_EAR_TURNS") or "").strip()
    if not turns:
        return _mark("ear", "off", "typing only")
    return _mark("ear", "ok", "listening") if _answers(turns) else _mark("ear", "fallback", "typing only for now")


def mouths_mark() -> dict:
    st = crissa_aligner.status()
    if not st.get("applicable"):
        return _mark("mouths", "ok", "word clock")
    if st.get("clock") == "aligned":
        return _mark("mouths", "ok", "follows the words")
    if st.get("better"):
        return _mark("mouths", "fallback", "follows the letters for now")
    return _mark("mouths", "off", "follows the letters")


def oracle_mark(env: dict | None = None) -> dict:
    """modules/oracle answers on its own port when it runs; absent or off is a mark, never an error."""
    e = os.environ if env is None else env
    url = str(e.get("CRISSA_ORACLE") or "").strip()
    if not url:
        return _mark("oracle", "off", "no face check")
    try:
        with crissa_egress.urlopen(url.rstrip("/") + "/v1/oracle", 0.3, "the oracle") as r:
            st = json.loads(r.read() or b"{}")
    except (OSError, ValueError):
        return _mark("oracle", "fallback", "no face check for now")
    if st.get("idle") == "security_off":          # round 17 follow-ups: Security Off idles the look (the oracle's note)
        return _mark("oracle", "off", "face check idle (Security Off)")
    if not st.get("camera"):
        return _mark("oracle", "fallback", "no camera")
    return _mark("oracle", "ok", "face check")


def beam_mark(env: dict | None = None) -> dict:
    """The beam microphone is decorative for now: present or 'no beam sig'."""
    e = os.environ if env is None else env
    url = str(e.get("CRISSA_ORACLE") or "").strip()
    if url:
        try:
            with crissa_egress.urlopen(url.rstrip("/") + "/v1/oracle", 0.3, "the oracle") as r:
                if json.loads(r.read() or b"{}").get("beam"):
                    return _mark("beam", "ok", "beam")
        except (OSError, ValueError):
            pass
    return _mark("beam", "off", "no beam sig")


def import_mark() -> dict:
    if not crissa_import.enabled():
        return _mark("import", "off", "import off")
    n = len(crissa_library_sync.waiting_now()["dropped"])     # dropped by hand; pulled packs are the library mark's
    return _mark("import", "ok", "%d dropped in" % n if n else "ready")


def library_mark() -> dict:
    st = crissa_library_sync.status()
    if not st["on"] or not st["linked"]:
        return _mark("library", "off", "library not linked")
    last = st["last"]
    if last.get("state") == "unreachable":
        return _mark("library", "fallback", "library not reachable")
    n = len(st["waiting_now"]["pulled"])              # what a pull brought and is in the folder NOW
    return _mark("library", "ok", "library: %d new" % n if n else "library linked")


def marks() -> list[dict]:
    out = []
    for fn in (agent_mark, voice_out_mark, ear_mark, mouths_mark, oracle_mark, beam_mark, import_mark, library_mark):
        try:
            out.append(fn())
        except Exception:               # a mark never becomes an error
            out.append(_mark(fn.__name__[:-5], "off", "not available"))
    return out
