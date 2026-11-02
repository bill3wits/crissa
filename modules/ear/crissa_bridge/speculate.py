"""speculate: her turn starts before his is final, and her mouth waits until it is (round 17, 2026-10-13).

The User 2026-10-13: "yes cut the waits". Measured on one core with the kit's own engines (checkpoint
round17-id-off-20261013): end of his speech -> her first sound p50 5.06 s (ID-only), about 4 s of it two fixed waits:
the ear's 2.0 s hangover that closed his utterance (crissa_ear/turn_end.py now: 0.6 s) and the bridge's 2.0 s quiet
before her mouth opened (EAR_BRIDGE_SPEAK_QUIET_SEC, from his turn's arrival: SPEAK_QUIET_SEC in ear_bridge.py).

In a pack with speculation on (config/crissa-talk.json `turn`):
  - HER TURN STARTS AT ONCE: a turn the bridge would gather is dispatched as it lands (the gather's FLUSH_FAST 0.3 s
    is not waited), so the model call and her first clause run while the turn may still go on (`flush`);
  - HER MOUTH OPENS ONLY ON A FINAL TURN (`final_ok`, read by _wait_speak_quiet, the one gate every first line of hers
    passes): the ear is not in an utterance and not decoding, and has heard no speech for `turn.final_s` (its
    `speech_at`, a 0.3 s frame at a time); and `turn.gap_s` (0.3 s, not 2.0) has passed since his turn reached the
    bridge - one ear frame, so a word he began as the turn landed is on the ear's status before her mouth opens.
    FINAL IS DECIDED IN THE BRIDGE'S OWN POLL (`before_poll` / `after_poll`), never by the mouth's thread alone: the
    ear's status is read just BEFORE each fetch of the turns, and the poll's turns are handled before it counts, so a
    chunk the ear published before that read has already reached schedule_dispatch (and cancelled this turn) - the
    first bench run caught the mouth's own read landing between the ear's publish (`decoding` false) and the bridge's
    next fetch, and her answer to half a sentence went out 0.16 s before the other half arrived. It is re-decided
    every poll (a word he starts later takes it back) and goes stale after STALE_S without a poll;
  - IF HE GOES ON FIRST, IT IS CANCELLED SILENTLY (`restart`): his next words reach schedule_dispatch while her
    speculative turn has not opened her mouth; the cancel token moves (nothing was played, so nothing is cut, no
    notice, no cancel record), her mouth's gate refuses that turn whatever token it read (`refuse`), and the whole
    utterance - what she had heard plus the new words - is the next turn, started as the cancelled one ends. Nothing of
    the cancelled turn reaches her history, the Wall or the lips; the bridge's own log says `spec_restart`.
    One she had already started saying is not a speculation any more (`commit`): his words then wait behind her
    line, or cut it (round 17's barge-in), as before.
WHY final_s IS 2.0 (measured 2026-10-13, checkpoint round17-waits-20261013, sentences with one thinking pause inside):
the ear sees him start again 0.3-0.5 s after he does (a 0.3 s frame, then the poll), so her mouth is safe from a pause of
up to about final_s - 0.5. With a decode as fast as a multi-core machine's (0.34 s) and a 0.3 s model, final_s 1.0
answered the first half of 2 of 4 sentences paused 1.2 s and all 4 paused 1.5 s; 1.5 answered 2 of 4 paused 1.5 s;
2.0 none paused 1.5 s or less (2 of 4 paused 2.0 s), for +0.4 s on her answer (p50 2.48 s against 2.07). On one core
the decode itself keeps her later than final_s. A shorter final_s is the install's choice (talk.json `turn.final_s`);
a start over him is then stopped by round 17's barge-in on his first word.
A SPECULATIVE TURN NEVER RUNS A TOOL (the coordinator, round 17 merge, "a speculative turn must never run a tool"):
`tool_guard` wraps crissa_reply's on_tool. At the first tool event of a turn that is not final yet (or was cancelled) the
read stops (SpecTool): closing the stream is how the gateway learns, it interrupts its agent on the disconnect - the
contract the revise turn's no-tools rule and Security's face check at a tool already use ("the gateway has no
per-request way to withhold tools from its agent"). The turn is then cancelled silently as a restart is (`tool_restart`)
and asked again with the SAME words once it is final (`ask_at_final`; `before_ask` holds that ask in crissa_reply until
the poll calls the turn final, or he goes on), with tools: the tool runs once, after final. A tool event once the turn
is final is the turn's own (`tools_ok`; from then on his next words take the old path, never a silent cancel, since a
tool may have run). Why this and not a speculative ask without tools: the speculative ask is then the very request a
plain turn sends, so a turn that needs no tool gets the same answer, earlier, and a turn that does gets the same
answer it would have got, asked once final; an ask without tools cannot tell the two apart without asking again (an
answer without its tool, or every turn of an agent with tools asked twice), and the gateway may not honour it anyway.
Limit: an agent that RUNS a tool before its first tool frame reaches the bridge has started it by then (the same limit
Security's tool check states); `tool_before_final` on /health counts the tool events that reached a callout of a turn
that was not final (0 while the guard holds). A cancelled turn's stream is dropped at its next frame.

Kills: CRISSA_EAR_SPECULATE=0 (no speculation, the ear's 2.0 s hangover back; the gap stays), CRISSA_SPEAK_GAP_S=2
(round 16's 2.0 s quiet before her mouth). The house: never (is_house; its bridge is rail-voice).
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

from .home import HOME, PACK_ROOT

# what config/crissa-talk.json `turn` says when nothing can be read (lib/crissa_talk.FALLBACK holds the same)
DEFAULTS = {"speculate": True, "end_s": 0.6, "final_s": 2.0, "gap_s": 0.3}
STATUS_TIMEOUT_S = 0.35                     # as ear_still_hearing's /health read
_CACHE: dict = {"key": None, "v": None}
_LOCK = threading.Lock()
_CUR: dict = {"rec": None}
STATS = {"started": 0, "restarted": 0, "committed": 0, "refused": 0, "tool_restarted": 0, "tool_asked_at_final": 0,
         "tool_before_final": 0}
ASK_AT_FINAL_WAIT_S = 15.0                  # a tool turn waits this long for the room to go quiet, then is not asked


class SpecTool(Exception):
    """A tool event in a turn that is not final (or was cancelled): the read stops here, before the tool runs."""


def _zh():
    try:
        from . import crissa_home as H              # a copy beside this package
        return H
    except ImportError:
        import sys
        lib = str(PACK_ROOT / "lib")
        if lib not in sys.path:
            sys.path.append(lib)
        import crissa_home as H                     # noqa: E402  the source tree's / the pack's lib
        return H


def is_house() -> bool:
    try:
        return bool(_zh().is_house())
    except Exception:  # noqa: BLE001 - no state-home module: not the house
        return False


def _talk_turn() -> dict:
    try:
        try:
            from . import crissa_talk as T          # the pack's vendored copy
        except ImportError:
            import sys
            lib = str(PACK_ROOT / "lib")
            if lib not in sys.path:
                sys.path.append(lib)
            import crissa_talk as T                 # noqa: E402
        t = T.load(home=HOME, env={}).get("turn")   # the environment: settings(), per call
        if isinstance(t, dict):
            return dict(t)
    except Exception:  # noqa: BLE001 - the files themselves below
        pass
    out = dict(DEFAULTS)
    for p in (PACK_ROOT / "config" / "crissa-talk.json", HOME / "talk.json"):
        try:
            sec = (json.loads(p.read_text(encoding="utf-8")) or {}).get("turn")
        except (OSError, ValueError, AttributeError):
            sec = None
        if isinstance(sec, dict):
            out.update({k: v for k, v in sec.items() if k in DEFAULTS and v is not None})
    return out


def _file_key() -> tuple:
    out = []
    for p in (PACK_ROOT / "config" / "crissa-talk.json", HOME / "talk.json"):
        try:
            st = p.stat()
            out.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(p), None, None))
    return tuple(out)


def settings(now: float | None = None) -> dict:
    """{speculate, end_s, final_s, gap_s, house}: the talk settings' `turn` (re-read when config/crissa-talk.json or
    <state home>/talk.json changes), the environment on top (CRISSA_EAR_SPECULATE, CRISSA_TURN_FINAL_S,
    CRISSA_SPEAK_GAP_S) and whether this is the house, read on every call."""
    key = _file_key()
    with _LOCK:
        cached = _CACHE["v"] if _CACHE.get("key") == key else None
    if cached is None:
        cached = dict(DEFAULTS)
        cached.update({k: x for k, x in _talk_turn().items() if k in DEFAULTS})
        with _LOCK:
            _CACHE.update(key=key, v=dict(cached))
    v = dict(cached)
    v["house"] = is_house()
    raw = os.environ.get("CRISSA_EAR_SPECULATE")
    if raw is not None and raw.strip():
        v["speculate"] = raw.strip().lower() not in ("0", "false", "no", "off")
    for key, var in (("final_s", "CRISSA_TURN_FINAL_S"), ("gap_s", "CRISSA_SPEAK_GAP_S")):
        raw = os.environ.get(var)
        if raw is not None and raw.strip():
            try:
                v[key] = max(0.0, float(raw))
            except ValueError:
                pass
    return v


def reset() -> None:
    """Read the talk files again at the next call."""
    with _LOCK:
        _CACHE.update(key=None, v=None)


def on() -> bool:
    """Speculation here: a pack, and turn.speculate (CRISSA_EAR_SPECULATE=0 turns it off)."""
    try:
        v = settings()
        return bool(v.get("speculate")) and not v.get("house")
    except Exception:  # noqa: BLE001
        return False


def gap(default: float) -> float:
    """The quiet before her mouth opens, from his turn's arrival: turn.gap_s in a pack (CRISSA_SPEAK_GAP_S wins; 2 is
    round 16's), else `default` (EAR_BRIDGE_SPEAK_QUIET_SEC, the house's 2.0)."""
    try:
        v = settings()
        return float(default) if v.get("house") else float(v.get("gap_s"))
    except (TypeError, ValueError):
        return float(default)


def final_s() -> float:
    try:
        return max(0.0, float(settings().get("final_s")))
    except (TypeError, ValueError):
        return DEFAULTS["final_s"]


def flush(need: float, cut_mid_sentence: bool = False) -> float:
    """The gather's quiet before her turn is dispatched: none when speculating (her mouth waits for the final turn
    instead), else `need`. A block cut mid-sentence (the ear's utterance cap) still waits its full window."""
    if cut_mid_sentence or not on():
        return need
    return 0.0


# ---- the ear: is his turn final? ---------------------------------------------------------------------------------

def _status_url() -> str:
    """The ear loop's status beside the /health the bridge already reads (EAR_BRIDGE_STT_HEALTH, else the port table)."""
    h = os.environ.get("EAR_BRIDGE_STT_HEALTH", "")
    if not h:
        from .settings import url
        h = url("stt", path="/health")
    return h[: -len("/health")] + "/v1/ear/status" if h.endswith("/health") else h


def ear_status() -> dict | None:
    """The ear loop's own status (in_utterance, decoding, speech_at), or None when it does not answer."""
    try:
        with urllib.request.urlopen(_status_url(), timeout=STATUS_TIMEOUT_S) as r:   # (a missing table: no answer)
            d = json.loads(r.read().decode() or "{}")
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None


STALE_S = 0.8                               # a final older than this (the bridge's poll is 0.2 s) is not trusted


_POLL = {"thread": None}


def _on_poll() -> bool:
    """Is this the bridge's poll thread (main), saying a line of its own? It is never her turn in flight."""
    return _POLL["thread"] is not None and threading.current_thread() is _POLL["thread"]


def before_poll() -> dict | None:
    """The bridge's poll is about to fetch the turns: the ear's status as of NOW ({} when the ear does not answer), or
    None when speculation is off."""
    _POLL["thread"] = threading.current_thread()
    if not on():
        return None
    return ear_status() or {}


def after_poll(status: dict | None, now: float | None = None) -> None:
    """The poll's turns are handled (a continuation has reached restart): decide whether the turn in flight is final,
    from the status read before the fetch. The ear not answering never holds her (as ear_still_hearing)."""
    if status is None:
        return
    now = time.time() if now is None else float(now)
    rec = _CUR["rec"]
    if rec is None or rec["committed"] or rec["cancelled"]:
        return
    rec["final"] = (not status) or not held(now, status)
    rec["tick_at"] = now


def final_ok(now: float | None = None) -> bool:
    """May her mouth open as far as speculation goes: no turn in flight, or it is committed, or the bridge's latest
    poll (at most STALE_S ago) found it final. A cancelled one never (refuse)."""
    if not on():
        return True
    rec = _CUR["rec"]
    if rec is None or rec["committed"]:
        return True
    if rec["cancelled"]:
        return False
    if _on_poll():
        return True       # a line the poll itself says (a house command's answer) cannot wait for its own next poll
    now = time.time() if now is None else float(now)
    return bool(rec.get("final")) and now - float(rec.get("tick_at") or 0.0) < STALE_S


def held(now: float | None = None, status: dict | None = None) -> bool:
    """True while his turn may still go on: the ear is in an utterance or decoding, or heard speech less than
    turn.final_s ago. False when speculation is off, or the ear does not answer (its absence never holds her)."""
    if not on():
        return False
    st = ear_status() if status is None else status
    if not st:
        return False
    if st.get("in_utterance") or st.get("decoding"):
        return True
    now = time.time() if now is None else float(now)
    try:
        at = float(st.get("speech_at") or 0.0)
    except (TypeError, ValueError):
        at = 0.0
    return bool(at) and (now - at) < final_s()


# ---- one speculative turn at a time (the bridge dispatches one turn at a time) ----------------------------------

def begin(text: str, meta: dict | None, token: int) -> None:
    """schedule_dispatch is starting her turn on `text`; `token` is the cancel token as it starts. Her own "go on" after
    a false barge-in cut (meta `barge_resume`, crissa_bridge/barge.py) is not his turn: no speculation (its rules are
    the barge-in's; her mouth's gate still waits for the room to be quiet)."""
    m = dict(meta or {})
    if not on() or m.get("barge_resume"):
        _CUR["rec"] = None
        return
    with _LOCK:
        _CUR["rec"] = {"text": str(text or ""), "meta": m, "token0": int(token), "at": time.time(),
                       "committed": False, "cancelled": False, "bumped_to": None, "final": False, "tick_at": 0.0,
                       "ask_at_final": bool(m.get("spec_at_final")), "tools_ok": False}
        STATS["started"] += 1


def restart(text: str, meta: dict | None, *, vocative: bool, token: int) -> tuple[str, dict] | None:
    """His next words while her turn is in flight. If that turn has not opened her mouth: it is cancelled here (the
    caller moves the cancel token to token + 1) and (the whole utterance, its meta) is returned as the next turn; else
    None (the old rule: his words wait behind her line). A vocative is a new call, never a continuation."""
    if vocative or not on():
        return None
    with _LOCK:
        rec = _CUR["rec"]
        if rec is None or rec["committed"] or rec["cancelled"] or rec.get("tools_ok"):
            return None
        rec["cancelled"] = True
        rec["bumped_to"] = int(token) + 1
        STATS["restarted"] += 1
        joined = (" ".join(x for x in (rec["text"].strip(), str(text or "").strip()) if x)).strip()
        m = dict(rec["meta"])
        m.update(meta or {})
        m["text"] = joined
        # round 46: the joined utterance never takes a more trusted voice from its later part (hear_gate.join_speaker;
        # kill EAR_BRIDGE_JOIN_LEAST_TRUST=0)
        from .hear_gate import apply_join_speaker
        apply_join_speaker(m, rec["meta"], meta)
        m["spec_restart"] = int(m.get("spec_restart") or 0) + 1
        return joined, m


def refuse(token: int) -> bool:
    """Her mouth never opens for a cancelled speculation, whatever cancel token its turn read (one that read it after
    the cancel moved it holds token0 + 1)."""
    rec = _CUR["rec"]
    if not rec or not rec["cancelled"] or _on_poll():
        return False
    hit = rec["bumped_to"] is not None and int(token) <= int(rec["bumped_to"])
    if hit:
        STATS["refused"] += 1
    return hit


def commit(token: int) -> bool:
    """Her mouth's gate said go: from here her turn is no speculation (his next words wait behind her line). False
    for a refused turn."""
    if refuse(token):
        return False
    rec = _CUR["rec"]
    if rec is not None and not rec["committed"] and not _on_poll():   # the poll's own line is not her turn's
        rec["committed"] = True
        STATS["committed"] += 1
    return True


def finished() -> None:
    """Her turn's dispatch has ended (_finish_dispatch, before it drains the next turn): no turn is in flight. A cancelled
    one's refusals end here, so a line said for the whole utterance (a house command's answer) is not taken for it."""
    with _LOCK:
        _CUR["rec"] = None


def uncommitted() -> bool:
    rec = _CUR["rec"]
    return bool(rec and not rec["committed"] and not rec["cancelled"])


def note_tool() -> bool:
    """A tool event that reached a callout: True (and counted in `tool_before_final`) when its turn was not final -
    one the guard did not stop. 0 while tool_guard holds."""
    rec = _CUR["rec"]
    if on() and rec is not None and not rec["committed"] and not rec.get("tools_ok"):
        STATS["tool_before_final"] += 1
        return True
    return False


# ---- a speculative turn never runs a tool ------------------------------------------------------------------------

def tool_guard(on_tool):
    """crissa_reply's on_tool, wrapped: at a tool event of the turn in flight that is not final (or was cancelled) the
    read stops (SpecTool, raised before on_tool and before the tool's info frame); once final the tool is the turn's
    (`tools_ok`) and on_tool runs. Not speculating: on_tool unchanged."""
    if not on():
        return on_tool

    def _guard(n):
        rec = _CUR["rec"]
        if rec is not None and not rec["committed"] and not rec.get("tools_ok"):
            if rec["cancelled"]:
                raise SpecTool("cancelled")
            if not final_ok():
                with _LOCK:
                    rec["tool_hold"] = True
                raise SpecTool("not_final")
            rec["tools_ok"] = True
        return on_tool(n)
    return _guard


def tool_restart(token: int) -> tuple[str, dict] | None:
    """After SpecTool("not_final"): her turn is cancelled as a restart is (the caller moves the cancel token to
    token + 1) and its SAME words come back to be asked once final, with tools (meta `spec_at_final`). None when it
    is no speculation any more."""
    with _LOCK:
        rec = _CUR["rec"]
        if rec is None or rec["committed"] or rec["cancelled"] or rec.get("tools_ok"):
            return None
        rec["cancelled"] = True
        rec["bumped_to"] = int(token) + 1
        STATS["tool_restarted"] += 1
        m = dict(rec["meta"])
        m["spec_at_final"] = True
        m["text"] = rec["text"]
        return rec["text"], m


def before_ask(now_fn=time.time, sleep=time.sleep) -> bool:
    """crissa_reply, just before it asks: a turn that reached for a tool before it was final is asked only once final
    (True; its tools are then its own), never while he may still go on. False when it was cancelled meanwhile (his
    next words: the whole utterance is asked instead) or the room did not go quiet in ASK_AT_FINAL_WAIT_S."""
    rec = _CUR["rec"]
    if rec is None or not rec.get("ask_at_final") or rec["committed"]:
        return True
    end = now_fn() + ASK_AT_FINAL_WAIT_S
    while now_fn() < end:
        if rec["cancelled"] or _CUR["rec"] is not rec:
            return False
        if final_ok():
            with _LOCK:
                if rec["cancelled"]:
                    return False
                rec["tools_ok"] = True
                STATS["tool_asked_at_final"] += 1
            return True
        sleep(0.05)
    return False


def snapshot() -> dict:
    rec = _CUR["rec"] or {}
    return {"on": on(), **STATS, "current": {k: rec.get(k) for k in ("committed", "cancelled")} if rec else None}
