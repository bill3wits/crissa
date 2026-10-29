"""Her line, cut by the first word the ear heard over it; who said it is worked out after (round 17, the User
2026-10-13: "for barge, can we send signal DIRECTLY from ASR on word penetration? She can always start up again. Also,
be sure barge streams to her. No check on voice signature before barge makes it more natural. Be sure not to interfere
with Auth but do allow the cutoff as close as is possible to the barge words." - "people do not continue speaking until
they figure out who interrupted you know?").

The ear (crissa_ear/barge.py) looks at the first words of anything said over one of her conversation lines and POSTs
/v1/barge {"phase": "cut"} the moment one of them is not hers. Here:

  CUT     in scope (her_scope in ear_bridge: this bridge's own line is on the air - her replies, her readings, her
          callouts - and Setup is not walking), she stops at once (abort_mouth: the cancel record keeps what she had
          said and where, for "go on"), and her session stays open for the turn. Nothing else: the cut reads no voice
          print, grants nothing, changes no identity and is not a command.
  TURN    the utterance that cut her arrives whole on the ring (`barge_id`) and goes through the normal turn path, the
          same as any turn - wake, hear gate, own voice, session owner, auth - but is sent at once (no gather wait:
          the ear's hangover already ended it). Her name with a stop or a hold, or a stop or a hold alone, ends there:
          she stays stopped and nothing is asked (round 14's name-interrupt rule).
  RESUME  when that utterance gave her no turn - no words left after the ear's echo check (the ear's end report), her
          own voice by the bridge's own_voice, or words the normal path set aside (not to her, another voice outside
          her conversation, a listener's "mm-hmm") - she goes on by herself after RESUME_S, through the same "go on" a
          person would say (the cancel record and the reading's place), at the level of the turn she was answering and
          with no tools (no voice was heard this turn). Any other turn heard after the cut, or her talking again,
          cancels it; "go on" stays.

Kill: CRISSA_BARGE_FROM_ASR=0 (both sides: round 17 before this, the over-line ask). Knobs: EAR_BRIDGE_BARGE_RESUME_S,
EAR_BRIDGE_BARGE_RESUME=0 (a false cut leaves her stopped; "go on" resumes).

SIDE TALK (round 18, the User 2026-10-13: "Id like to talk with someone else while she speaks without interrupting
her"). In a pack, only an interrupt word or her name cuts her (crissa_ear/barge.py, words only). Anything else said
while she answers a turn - from her answer's first line on the air until that turn's dispatch is over, the gaps between
her pieces and a tool's wait included - is side talk: no cut, never a turn for her (not queued, not answered after, not
put to the over-line ask), one row on the Wall's log as room speech (heard_roll lane "room"), and nowhere else. Two
edges stay his turn, because they are not talk over her: speech that began before her answer's first line went on the
air or within START_GRACE_S of it (he was still finishing his own sentence as she began), and speech that began in the
last END_GRACE_S of her answer (an answer to her closing question overlaps its last word). After her answer the
session's rules apply unchanged. Kill: CRISSA_SIDE_TALK=0 (words over her answer go to the over-line ask, as before
round 17); CRISSA_BARGE_WORDS_ONLY=0 (round 17: any word cuts, so there is no side talk).
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any


def on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_BARGE_FROM_ASR", "1")).strip().lower() not in ("0", "false", "no", "off")


def resume_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("EAR_BRIDGE_BARGE_RESUME", "1")).strip().lower() not in ("0", "false", "no", "off")


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


# THE PAUSE BEFORE SHE GOES ON BY HERSELF. The false cut is known only when the utterance has ended: the ear's hangover
# (2.0 s of quiet in a session) and its decode are already behind it, and her "go on" waits for SPEAK_QUIET_SEC of
# quiet and her model's first clause on top. Half a second more is a speaker's ordinary gap at a turn's end (the
# 0.2-0.5 s people leave), so she does not cut in on someone drawing breath, and adds little to the 3-5 s she has
# already been quiet.
RESUME_S = _f("EAR_BRIDGE_BARGE_RESUME_S", 0.5)
# No outcome from the ear this long after the cut (an utterance is at most 30 s, then its decode): she stays stopped.
END_WAIT_S = _f("EAR_BRIDGE_BARGE_END_WAIT_S", 40.0)
# A resume that keeps finding her busy (talking, thinking, the ear hearing) for this long is dropped.
RESUME_GIVE_UP_S = 10.0
RESUME_TEXT = "Go on."
RESUME_NOTE = (" What stopped you just now was not said to you (no words for you came of it), so go on from where you "
               "stopped, without remarking on it.")


class Barge:
    """The one cut in flight: {"id", "at", "state", "partial", "resume", "resume_at", "why"}.

    states: cut (waiting for the utterance) -> heard (its turn is on the normal path) -> took (it was a turn for her,
    or a stop: no resume) | resume (no turn for her: she goes on at resume_at) -> resumed | dropped; lost (no outcome
    within END_WAIT_S)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cur: dict[str, Any] = {}
        self._turn: dict[str, Any] | None = None          # the turn she is answering (dispatch notes it)

    # -- the turn she is answering ------------------------------------------------------------------------------------
    def note_turn(self, meta: dict, level: str) -> None:
        """dispatch(): the turn she is about to answer, and the level it was given (a resume answers at that level)."""
        keep = {k: v for k, v in (meta or {}).items() if k not in ("barge_resume", "resume_level")}
        with self._lock:
            self._turn = {"meta": keep, "level": str(level or ""), "at": time.time()}

    # -- the cut ------------------------------------------------------------------------------------------------------
    def cut(self, bid: str, *, partial: str = "", via: str = "asr", answering: bool = True,
            now: float | None = None, **extra) -> dict:
        """She was stopped for `bid`. `answering`: the line was part of a turn's answer (dispatch was running), so a
        false cut can go on with that turn; a cut of a line outside a turn (a wake's "Yeah?") never resumes."""
        now = time.time() if now is None else float(now)
        with self._lock:
            self.cur = {"id": str(bid), "at": now, "state": "cut", "partial": (partial or "")[:200], "via": via,
                        "resume": dict(self._turn) if (answering and self._turn) else None, "resume_at": 0.0,
                        "why": "", **extra}
            return dict(self.cur)

    def same(self, bid: str | None) -> bool:
        with self._lock:
            return bool(bid) and self.cur.get("id") == str(bid) and self.cur.get("state") in ("cut", "heard")

    def is_turn(self, t: dict) -> bool:
        """Is this ring turn the utterance that cut her (still waiting for it)? Marks it heard."""
        bid = str((t or {}).get("barge_id") or "")
        with self._lock:
            if bid and self.cur.get("id") == bid and self.cur.get("state") == "cut":
                self.cur["state"] = "heard"
                return True
        return False

    def took(self, why: str) -> None:
        with self._lock:
            if self.cur.get("state") in ("cut", "heard"):
                self.cur.update(state="took", why=why)

    def aside(self, why: str, now: float | None = None, wait_s: float | None = None) -> bool:
        """The cut gave her no turn: she goes on at now + RESUME_S (when there is a turn to go on with). True when a
        resume is now due."""
        now = time.time() if now is None else float(now)
        with self._lock:
            if self.cur.get("state") not in ("cut", "heard"):
                return False
            if not resume_on() or not self.cur.get("resume"):
                self.cur.update(state="took", why="%s, nothing to go on with" % why if resume_on() else why)
                return False
            w = RESUME_S if wait_s is None else float(wait_s)      # .101 r28: three-word cuts wait for 3 s of quiet
            self.cur.update(state="resume", why=why, resume_at=now + w, first_due=now + w)
            return True

    def ended(self, bid: str, *, turn: bool, why: str = "", cut_why: str = "", now: float | None = None) -> str:
        """The ear's end report. No words: aside - unless the look that cut her heard a stop or her name: then she was
        asked to stop, and a whole decode that lost the words (her echo louder than a short "Stop.") does not make her
        go on over the person (she stays stopped; "go on" resumes). Words: their turn is on the ring (is_turn marks it)."""
        if not self.same(bid):
            return "unknown"
        if turn:
            return "turn_coming"
        if cut_why in ("stop", "named"):
            self.took("asked to stop (%s), the words lost in the decode" % cut_why)
            return "stopped"
        self.aside("no words: %s" % (why or "her echo or a noise"), now)
        return "resume" if self.cur.get("state") == "resume" else str(self.cur.get("state"))

    def other_turn(self) -> None:
        """Another turn was heard after the cut: she is in a conversation again; no resume ("go on" stays)."""
        with self._lock:
            if self.cur.get("state") == "resume":
                self.cur.update(state="dropped", why="another turn")

    def due(self, now: float, busy: bool) -> str | None:
        """"resume" when she should go on now (the caller dispatches), "lost" when no outcome came; else None."""
        with self._lock:
            st = self.cur.get("state")
            if st == "cut" and now - float(self.cur.get("at") or now) > END_WAIT_S:
                self.cur.update(state="lost", why="no outcome from the ear")
                return "lost"
            if st != "resume" or now < float(self.cur.get("resume_at") or 0.0):
                return None
            if busy:
                if now - float(self.cur.get("first_due") or now) > RESUME_GIVE_UP_S:
                    self.cur.update(state="dropped", why="she stayed busy")
                    return "dropped"
                self.cur["resume_at"] = now + 0.3
                return None
            self.cur["state"] = "resumed"
            return "resume"

    def resume_meta(self) -> dict | None:
        """The meta of the turn she goes on with: its own meta, its level, and barge_resume."""
        with self._lock:
            r = self.cur.get("resume") or {}
        if not r:
            return None
        return {**dict(r.get("meta") or {}), "barge_resume": True, "resume_level": r.get("level") or "",
                "text": RESUME_TEXT}

    def snapshot(self) -> dict:
        with self._lock:
            return {k: v for k, v in self.cur.items() if k != "resume"}


STATE = Barge()


# ── round 18: side talk ───────────────────────────────────────────────────────────────────────────────────────────────

def words_only_on() -> bool:
    """The ear's rule (crissa_ear.barge.words_only_on): only an interrupt word or her name cuts her. Off when the
    ear's module is not beside the bridge (round 17's any-word rule)."""
    try:
        from crissa_ear.barge import words_only_on as _w
        return bool(_w())
    except Exception:  # noqa: BLE001
        return False


def side_talk_on() -> bool:
    try:
        from crissa_ear.barge import side_talk_on as _s
        return bool(_s())
    except Exception:  # noqa: BLE001
        return False


# He was still finishing his own sentence as she began: the ear sees speech start again 0.3-0.5 s late (turn_end.py,
# round 17), and a pause of about turn.final_s inside his sentence can still be answered early (speculate.py), so his
# words that start within this of her answer's first line are his turn going on, not talk over her.
START_GRACE_S = _f("EAR_BRIDGE_SIDE_TALK_START_GRACE_S", 0.6)
# An answer to her closing question overlaps its last word (people start a turn 0-0.5 s before the other ends): speech
# that began in her answer's last second is to her.
END_GRACE_S = _f("EAR_BRIDGE_SIDE_TALK_END_GRACE_S", 1.0)


class Air:
    """Her answers on the air, for the side-talk rule: one row per turn she answers (dispatch's busy span), with the
    start of its first line and the end of its last."""

    def __init__(self, keep: int = 16) -> None:
        self._lock = threading.Lock()
        self._answers: deque = deque(maxlen=keep)

    def answer_begin(self, now: float | None = None) -> None:
        """schedule_dispatch: a turn is being answered (her mouth has not opened for it yet)."""
        now = time.time() if now is None else float(now)
        with self._lock:
            for a in self._answers:
                if a["end"] is None:
                    a["end"] = now
            self._answers.append({"begin": now, "first": None, "last_off": None, "open": 0, "end": None})

    def answer_end(self, now: float | None = None) -> None:
        """_finish_dispatch: the turn is answered (its last line has ended, or she was stopped)."""
        now = time.time() if now is None else float(now)
        with self._lock:
            for a in self._answers:
                if a["end"] is None:
                    a["end"] = now

    def line_on(self, now: float | None = None, *, answering: bool = True) -> None:
        """speak_blocking: one of her lines goes on the air (house-speak spawned). A line outside an answer (a wake's
        "Yeah?") is not an answer's."""
        now = time.time() if now is None else float(now)
        with self._lock:
            a = self._answers[-1] if self._answers else None
            if not answering or a is None or a["end"] is not None:
                return
            if a["first"] is None:
                a["first"] = now
            a["open"] += 1

    def line_off(self, now: float | None = None) -> None:
        now = time.time() if now is None else float(now)
        with self._lock:
            a = self._answers[-1] if self._answers else None
            if a is None or a["first"] is None:
                return
            a["open"] = max(0, a["open"] - 1)
            a["last_off"] = now

    def verdict(self, s0: float, now: float | None = None) -> tuple[bool, str]:
        """Is speech that began at `s0` side talk? (True, "over_her_answer"), or (False, why)."""
        now = time.time() if now is None else float(now)
        with self._lock:
            answers = [dict(a) for a in self._answers]
        for a in reversed(answers):
            if a["first"] is None:
                continue                                   # her mouth never opened for that turn
            end = a["end"]
            if end is not None:
                end = max(end, a["last_off"] or end)
                if s0 >= end:
                    return False, "after_her_answer"
            if s0 < a["first"]:
                continue                                   # before this answer: an older one, or his own turn
            if s0 < a["first"] + START_GRACE_S:
                return False, "his_turn_going_on"
            if end is not None and s0 >= end - END_GRACE_S:
                return False, "answering_her_end"
            return True, "over_her_answer"
        return False, "not_over_her_answer"

    def snapshot(self) -> list:
        with self._lock:
            return [dict(a) for a in self._answers]


AIR = Air()
