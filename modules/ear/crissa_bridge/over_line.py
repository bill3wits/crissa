"""Said over her line (round 15 (b'), the User 2026-10-12: "I think 'one second' should be transmitted with offer to
cancel. LLM can process.").

While she speaks, his words without her name ("One second.", or anything else) were queued behind her line and heard
only when she finished (.101 13:14:43 and 13:14:53, "One second." twice, nothing). Now they go to her chat model at once,
marked as said over her current line - what she was answering, what she has said, where she is - with three actions:

    PAUSE     she stops now and waits ("go on" picks up where she stopped: the cancel record)
    STOP      she stops now and drops the rest
    CONTINUE  she carries on; his words are answered after her line (the old queue)

The model answers the action word first, then at most one short line for her to say after stopping. A model that does
not answer in time: a stop or a hold ("one second", "wait") pauses her; anything else is answered after her line. Her
name with a stop word stays the hard stop, at once (wake.barge_match, the house's name-interrupt).

THE HOUSE'S WAY: the house's agent redirects its live turn with the new words (its active-turn redirect);
a pack's Connect endpoint has no live turn to redirect, so the bridge asks once with a structured reply.
Switch EAR_BRIDGE_OVER_LINE = model (default) | stop (a stop or a hold alone stops her, nothing asked) | off (queue, as
before).
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable

ACTIONS = ("PAUSE", "STOP", "CONTINUE", "IGNORE")
TIMEOUT_S = float(os.environ.get("EAR_BRIDGE_OVER_LINE_TIMEOUT_S", "6"))


def mode() -> str:
    v = os.environ.get("EAR_BRIDGE_OVER_LINE", "model").strip().lower()
    return v if v in ("model", "stop", "off") else "model"


def request_not_pause_on() -> bool:
    """Round 28: the model's PAUSE stands only for a stop or hold in his words. Kill EAR_BRIDGE_OVER_LINE_REQUEST_PAUSE=1."""
    return os.environ.get("EAR_BRIDGE_OVER_LINE_REQUEST_PAUSE", "0").strip().lower() in ("0", "false", "no", "off")


def messages(user: str, where: dict[str, Any], request: str = "") -> list[dict[str, str]]:
    said = str(where.get("said") or "")[-400:]
    nxt = str(where.get("next_sentence") or "")
    sys = ("You are speaking aloud right now, and the person you are talking to has just said something over you. "
           "Decide what happens to your speech. Reply with exactly one of these words on the first line: PAUSE (stop "
           "now and wait; you will pick up where you stopped if asked), STOP (stop now and drop the rest), CONTINUE "
           "(keep speaking; you will answer him after), IGNORE (it was not said to you - he is talking to someone "
           "else or to himself; keep speaking and do not answer it). PAUSE only when he asks you to wait or hold on; a question or "
           "a request is never PAUSE - it is CONTINUE, or STOP when it replaces what you are saying. On the second "
           "line, optionally, one very short sentence to say right after you stop; nothing for CONTINUE or IGNORE.")
    ctx = []
    if request:
        ctx.append('You were answering: "%s".' % request[:200])
    if said:
        ctx.append('You have said so far (its end): "%s".' % said)
    if nxt:
        ctx.append('You are in, or about to say: "%s".' % nxt[:200])
    ctx.append('Over your speech he said: "%s".' % (user or "").strip()[:200])
    return [{"role": "system", "content": sys}, {"role": "user", "content": " ".join(ctx)}]


def parse(reply: str | None) -> tuple[str | None, str]:
    """(PAUSE | STOP | CONTINUE | IGNORE | None, the short line) from the model's reply."""
    t = (reply or "").strip()
    if not t:
        return None, ""
    m = re.match(r"^\W*(PAUSE|STOP|CONTINUE|IGNORE)\b[\s:.,!-]*(.*)$", t, re.I | re.S)
    if not m:
        return None, ""
    say = " ".join(m.group(2).split())[:160]
    act = m.group(1).upper()
    return act, ("" if act in ("CONTINUE", "IGNORE") else say)


def decide(user: str, where: dict[str, Any], ask: Callable[[list], str | None], *, request: str = "",
           stop_only: Callable[[str], bool] | None = None) -> dict[str, Any]:
    """{"action", "say", "by"}: the model's choice, else the fallback (a stop or a hold pauses; else continue)."""
    if mode() == "stop":
        stops = bool(stop_only and stop_only(user))
        return {"action": "PAUSE" if stops else "CONTINUE", "say": "", "by": "stop words"}
    reply = None
    try:
        reply = ask(messages(user, where, request))
    except Exception:  # noqa: BLE001 - the fallback decides
        reply = None
    act, say = parse(reply)
    if act == "PAUSE" and request_not_pause_on() and stop_only is not None and not stop_only(user):
        # round 28 (.101 2026-10-17 05:14: "Could you display on the screen?" -> PAUSE, she went PAUSED and every
        # later ask was dropped): a request is never a pause; it is answered right after her line
        # .101 round 28 15:08-15:09 (three asks "put that up on the screen" over her reading: the model said PAUSE each
        # time, this made them CONTINUE, and "after her line" was 4,000 characters away - the User: "she did not respond
        # to the imperative"): the model wanted to stop for a request, so she STOPS and does it now (the bridge
        # dispatches a STOP's request once she has stopped)
        return {"action": "STOP", "say": "", "by": "model, a request stops her line and is done now"}
    if act:
        return {"action": act, "say": say, "by": "model"}
    stops = bool(stop_only and stop_only(user))
    return {"action": "PAUSE" if stops else "CONTINUE", "say": "", "by": "fallback"}
