"""Veto before decode — the house's own voice is not sent to the ASR.

2026-09-19: the ear listens to the raw array (AEC off since 09-09), so the
bench drill's line comes back through the speakers every ~22 s and was
decoded each time: 52 % of audio8-asr decode time, ~14 cores a decode. The
crissa_her_tts fingerprint veto already named it (269 of 281), but it ran beside
the ASR, so the decode was paid first.

Scope (the User 2026-09-19): the BENCH only. No talk-over turn exists on file,
and a barge-in is a mixed segment that can score high as the house and low
as the User; on the wall that would be the house ignoring him mid-sentence.
Widen only after recorded talk-overs pass (scripts/ear-bargein-score.py).

Rules, all required to skip the decode:
  - the TTS gate is on air (or dropped within the utterance) and `who` is a
    bench voice (``*@bench``);
  - the utterance is at least RAIL_STT_VETO_MIN_SEC long (short clips embed
    badly; 0 vetoes under 2 s in the last 3,000 turns);
  - the User scores below RAIL_STT_VETO_NEAR_USER. At or above it the turn
    decodes as today and a ``near`` line is written, so near misses are seen.

The crissa_her_tts fingerprint is NO LONGER required for a bench line (2026-09-20).
It agreed only 266 times in 876, so 3386 s of the house's own bench voice was
decoded at up to 16 cores and starved the wall to 14.5 fps. It was a second
opinion on a fact the gate already holds. RAIL_STT_VETO_BENCH_NEEDS_FP=1 puts
it back.

The announcer is never transcribed (the User 2026-09-19): an announcer line on
air (``who`` = announcer, wall or bench) skips the decode on the same
duration floor and User guard, without a fingerprint (no announcer prints
are in the TTS bank). Kill: RAIL_STT_ANNOUNCER_DUMP=0.

Her own line in a pack (round 13, 2026-10-12, .101): the bench and the announcer are the house's scope; in a pack her
replies come back through the room speakers and every piece was decoded - 6 of 11 turns between 08:29 and 08:32 on
.101, the 13.2 s Moby-Dick reading at 5.4 s and ~7 cores - which starved her own mouth. The player writes her air window
(<home>/house-tts-gate.json: air_at / air_until, house-speak's mark_tts_gate); an utterance that overlaps it, at least
MIN_SEC long, skips the decode unless the best ENROLLED person scores at or above NEAR_USER. Measured on .101's 690
turns: every turn of the User's that overlapped her air scored 0.43-0.68; her own echo has a median of 0.16. The guard
is the best enrolled person, not the roster name "user", which a pack never enrols (its person is named at Init, so the
old guard read nothing and passed everything). Kill: CRISSA_EAR_VETO_HER=0.

Kill: RAIL_STT_VETO_BEFORE_DECODE=0. Log: logs/veto-first.jsonl.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .home import HOME
from typing import Any

MIN_SEC = float(os.environ.get("RAIL_STT_VETO_MIN_SEC", "3.0"))
NEAR_USER = float(os.environ.get("RAIL_STT_VETO_NEAR_USER", "0.25"))
SCOPE = os.environ.get("RAIL_STT_VETO_SCOPE", "bench").strip().lower()
EMBED_WAIT_S = float(os.environ.get("RAIL_STT_VETO_EMBED_WAIT_SEC", "2.0"))
# utterance flush trails its speech by the hangover (2 s) plus the decode queue
OVERLAP_SLACK_S = float(os.environ.get("RAIL_STT_VETO_OVERLAP_SEC", "3.0"))


def her_scope_on() -> bool:
    return os.environ.get("CRISSA_EAR_VETO_HER", "1").strip().lower() not in ("0", "false", "no", "off")


def speak_gate_path() -> Path:
    """The player's own gate (house-speak's mark_tts_gate): her air window, written before and at the end of air."""
    return Path(os.environ.get("CRISSA_SPEAK_GATE") or str(HOME / "house-tts-gate.json")).expanduser()


def read_speak_gate() -> dict[str, Any]:
    try:
        d = json.loads(speak_gate_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def her_on_air(dur: float, now: float, gate: dict[str, Any] | None = None) -> bool:
    """Did her own line (not the announcer, not a bench) overlap this utterance, which ended now and lasted `dur`?"""
    g = read_speak_gate() if gate is None else gate
    if not g:
        return False
    who = str(g.get("who") or "").strip().lower()
    if not who or _is_announcer(who) or _is_bench(who):
        return False
    try:
        at = float(g.get("air_at") or 0.0)
        until = float(g.get("air_until") or 0.0)
        updated = float(g.get("updated_ts") or 0.0)
    except (TypeError, ValueError):
        return False
    start = now - float(dur)
    if g.get("speaking"):
        from .tts_gate import STUCK_GATE_S

        if until > now or (updated and now - updated <= STUCK_GATE_S):
            return not at or at <= now
        return False
    end = until or updated
    return bool(end and end >= start - OVERLAP_SLACK_S and (not at or at <= now))


# THE PHONE HAS THE FLOOR (the User 2026-10-18 18:2x, "one or the other from that button"): on .101 the desk answered
# while he talked to her on the phone. The phone server writes <state home>/phone-floor.json while the phone's talk
# button holds the floor and until her answer has played there (lib/phone_floor: {"on", "since", "until"}, every hold
# good for a few seconds unless refreshed); an utterance that overlaps it is the phone's, not a turn at the desk, and is
# dropped before the decode - no voice print, no guard: he is talking to the other screen. Kill: CRISSA_EAR_PHONE_FLOOR=0.
FLOOR_HANGOVER_S = float(os.environ.get("CRISSA_EAR_PHONE_FLOOR_HANGOVER_S", "2.0"))   # the ear's flush after speech


def phone_floor_path() -> Path:
    return Path(os.environ.get("CRISSA_PHONE_FLOOR") or str(HOME / "phone-floor.json")).expanduser()


def phone_floor_held(dur: float, now: float | None = None, floor: dict[str, Any] | None = None,
                     line: bool = False) -> bool:
    """Did the phone hold the floor at any time during this utterance (which lasted `dur` and was flushed `now`)?
    `line`: only her line playing on the phone counts (`line_since` / `line_until`), not the button (phone_mic.py)."""
    if os.environ.get("CRISSA_EAR_PHONE_FLOOR", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    t = time.time() if now is None else float(now)
    if floor is None:
        try:
            floor = json.loads(phone_floor_path().read_text(encoding="utf-8"))
        except Exception:
            return False
    if not isinstance(floor, dict) or not (floor.get("on") or line):
        return False
    try:
        since = float(floor.get("line_since" if line else "since") or 0.0)
        until = float(floor.get("line_until" if line else "until") or 0.0)
    except (TypeError, ValueError):
        return False
    start = t - float(dur) - FLOOR_HANGOVER_S
    return bool(since and since <= t and until >= start)


def enabled() -> bool:
    return os.environ.get("RAIL_STT_VETO_BEFORE_DECODE", "1").strip() not in ("0", "false", "no")


def announcer_dump() -> bool:
    return os.environ.get("RAIL_STT_ANNOUNCER_DUMP", "1").strip() not in ("0", "false", "no")


def bench_needs_fp() -> bool:
    """Does a bench line still have to match the crissa_her_tts fingerprint?

    NO since 2026-09-20 (the User: "yes do the veto fix"). Measured over 876 bench
    utterances: scope passed every time (`who` = *@bench, on air, over the
    duration floor) and the fingerprint fired on only 266 — so 610 decodes,
    3386 s of the house's own bench voice, were paid at up to 16 cores each.
    That starved the wall to 14.5 fps with half-second stalls.

    The fingerprint was a second opinion on a fact we already hold: the TTS
    gate says the bench is on air with this voice. The announcer has skipped
    without a fingerprint since 09-19 for the same reason. The User guard
    (NEAR_USER) is what protects a barge-in, and it stays.

    RAIL_STT_VETO_BENCH_NEEDS_FP=1 restores the fingerprint requirement.
    """
    return os.environ.get("RAIL_STT_VETO_BENCH_NEEDS_FP", "0").strip() not in ("0", "false", "no")


def _is_bench(who: str) -> bool:
    return who.endswith("@bench")


def _is_announcer(who: str) -> bool:
    return who.split("@", 1)[0] in ("announcer", "house")


def log_path() -> Path:
    return HOME / "logs" / "veto-first.jsonl"


def in_scope(gate: dict[str, Any], dur: float, now: float) -> bool:
    """On-air bench voice overlapping an utterance that just ended, or (a pack) her own line."""
    if her_scope_on() and her_on_air(dur, now):
        return True
    if not gate:
        return False
    who = str(gate.get("who") or "").strip().lower()
    if _is_announcer(who):
        if not announcer_dump():
            return False
    elif SCOPE == "bench" and not who.endswith("@bench"):
        return False
    try:
        updated = float(gate.get("updated_ts") or 0.0)
    except (TypeError, ValueError):
        updated = 0.0
    if gate.get("speaking"):
        from .tts_gate import STUCK_GATE_S

        try:
            until = float(gate.get("air_until") or 0.0)
        except (TypeError, ValueError):
            until = 0.0
        # A stuck gate is not a mouth (tts_gate.mouth_open, 2026-09-07).
        return bool(until > now or (updated and now - updated <= STUCK_GATE_S))
    # Off air now: did the air window overlap this utterance? house-speak
    # writes the gate BEFORE air (updated_ts) with air_until = end of air, so
    # air_until is the clock; updated_ts only when no air window was written
    # (2026-09-19: a 9 s bench line ended 12.5 s after updated_ts).
    try:
        end = float(gate.get("air_until") or 0.0) or updated
    except (TypeError, ValueError):
        end = updated
    return bool(end and end >= now - dur - OVERLAP_SLACK_S)


def decide(
    *,
    gate: dict[str, Any],
    dur: float,
    emb: Any,
    now: float | None = None,
    user_sim_fn=None,
    veto_fn=None,
    person_sim_fn=None,
) -> dict[str, Any]:
    """{'skip': bool, 'why': str, ...sims}. Pure given its callables."""
    t = time.time() if now is None else float(now)
    out: dict[str, Any] = {"skip": False, "why": "", "dur": round(float(dur), 2)}
    if not enabled():
        out["why"] = "off"
        return out
    her = her_scope_on() and her_on_air(dur, t)
    if not her and not in_scope(gate, dur, t):
        out["why"] = "not_on_air"
        return out
    out["who"] = gate.get("who")
    if her:
        if dur < MIN_SEC:
            out["why"] = "short"
            return out
        if emb is None:
            out["why"] = "no_embed"
            return out
        best = (person_sim_fn or _best_person_sim)(emb)
        out["who"] = "her"
        out["person_sim"] = None if best is None else round(float(best), 4)
        if best is not None and float(best) >= NEAR_USER:
            out["why"] = "near"
            return out
        out["skip"] = True
        out["why"] = "her_line"
        return out
    if dur < MIN_SEC:
        out["why"] = "short"
        return out
    if emb is None:
        out["why"] = "no_embed"
        return out
    vec = [float(x) for x in list(emb)]
    owner_sim = (user_sim_fn or _user_sim)(emb)
    out["user_sim"] = None if owner_sim is None else round(float(owner_sim), 4)
    if _is_announcer(str(gate.get("who") or "").strip().lower()):
        if owner_sim is not None and float(owner_sim) >= NEAR_USER:
            out["why"] = "near"
            return out
        out["skip"] = True
        out["why"] = "announcer"
        return out
    # A BENCH LINE NEEDS NO FINGERPRINT (2026-09-20). Same shape as the
    # announcer above: the gate already names the speaker and the air window,
    # so scope + the User guard decide. This is what deletes the drill echo.
    if _is_bench(str(gate.get("who") or "").strip().lower()) and not bench_needs_fp():
        if owner_sim is not None and float(owner_sim) >= NEAR_USER:
            out["why"] = "near"
            return out
        out["skip"] = True
        out["why"] = "bench_scope"
        return out
    veto = (veto_fn or _veto)(vec, emb)
    if not veto:
        out["why"] = "no_veto"
        return out
    out["tts_fp_sim"] = veto.get("tts_fp_sim")
    if owner_sim is not None and float(owner_sim) >= NEAR_USER:
        out["why"] = "near"
        return out
    out["skip"] = True
    out["why"] = "veto"
    return out


def _user_sim(emb: Any) -> float | None:
    """The owner's score: the person enrolled as "user" (the house), else the best enrolled person (a pack names its
    person at Init, so "user" is never in its bank and this read None - no guard at all; round 13, 2026-10-12)."""
    try:
        from .voice_bank import rank_voice

        for name, sim in rank_voice(emb, roster=["user"], top_k=1):
            if str(name).lower() == "user":
                return float(sim)
    except Exception:
        return None
    return _best_person_sim(emb)


def _best_person_sim(emb: Any) -> float | None:
    """The best score of any enrolled person (system and TTS voices are not people: voice_bank skips them)."""
    try:
        from .voice_bank import rank_voice

        ranked = rank_voice(emb, top_k=1)
        return float(ranked[0][1]) if ranked else None
    except Exception:
        return None


def _veto(vec: list[float], emb: Any) -> dict[str, Any] | None:
    """The live rule as voice_live runs it: TTS bank vs the best real person."""
    from .stamp import tts_veto_check

    top_sim = None
    try:
        from .voice_bank import rank_voice

        ranked = rank_voice(emb, top_k=1)
        top_sim = float(ranked[0][1]) if ranked else None
    except Exception:
        top_sim = None
    return tts_veto_check(vec, top_sim=top_sim)


def record(decision: dict[str, Any]) -> None:
    """One line per in-scope turn (veto / near / no_veto / short). Never raises."""
    if decision.get("why") in ("off", "not_on_air"):
        return
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        row = dict(decision, ts=round(time.time(), 3))
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass
