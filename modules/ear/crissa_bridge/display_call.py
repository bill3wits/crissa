"""Her display tool on a spoken turn (round 26).

.101 2026-10-16, check 4, three tries: "no display although she says she did". The tool (lib/crissa_display) was only
offered on the conductor's own ask path; a spoken turn goes through this bridge to the connected agent, which was never
told of it, so she said "pulling it up now" with nothing to pull it up with.

Here: on an owner's turn whose tools are allowed (the same rule as every action: Security On = this turn's voice,
ID-only = the User's face, Off = yes), the payload carries the tool; a call the agent makes is collected from the stream
(`Calls`), run by the conductor (`POST /v1/display-tool`, where the card and the letter window live), and its result
goes back for her short line about it. One round: a call in the answer to a result is not run again.

When the tool is not offered she is told she cannot show anything this turn (`NO_SCREEN_LINE`), so she says so instead
of claiming a page is up.

Kill: CRISSA_BRIDGE_DISPLAY=0 (no tool offered, no line added).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

ON_ENV = "CRISSA_BRIDGE_DISPLAY"
NO_SCREEN_LINE = (" You cannot put anything on the screen this turn (no display tool): never say you are showing, "
                  "opening or pulling something up; answer in words, or say the User's face needs to be at the camera "
                  "for you to show it.")
# THE SCREEN IS THE USER'S (the User 2026-10-30: "I want her to clear it as a habit (so in prompt to maintain User
# screen at all times) at her discretion"). What she leaves up clears itself 30 s after she pauses (ear_bridge
# BOARD_CLEAR_AFTER_S). Kill: EAR_BRIDGE_SCREEN_HABIT=0 (no line).
SCREEN_HABIT_LINE = ("" if os.environ.get("EAR_BRIDGE_SCREEN_HABIT", "1") == "0" else
                     " The screen is the User's: keep it tidy. When what you put up has done its job, take it down "
                     "yourself (display what=clear); leave it up only while it still helps him, or when he asks you to "
                     "leave it up.")
_REFUSED: set = set()            # endpoints that answered 400 to `tools`: never offered again this run


# "TAKE IT DOWN" IS HEARD AND DONE, NOT ASKED (round 27, .101 2026-10-16): "Good. Right now, take it down." over her
# reading was side talk (no interrupt word), and later, as a Guest turn with no tool, she twice said "It's gone now" with
# the page still up. A short ask to take the page down is a house action: it stops her, clears the sheet at the
# conductor itself and says what happened - over her answer too, with no tool round and no face needed (taking a page
# down is as safe as "cancel"). Kill: EAR_BRIDGE_TAKE_DOWN=0.
_THING = r"(?:it|that|this|them|the\s+(?:document|doc|page|recipe|sheet|card|letter|list|notes?|window|screen|display))"
TAKE_DOWN = re.compile(
    r"\b(?:(?:take|put|bring|pull)\s+%s\s+(?:down|away|off)|(?:take|put|bring|pull)\s+(?:down|away|off)\s+%s"
    r"|(?:remove|close|hide|clear|dismiss)\s+%s|clear\s+(?:the\s+)?(?:screen|display|sheet)"
    r"|(?:get\s+rid\s+of)\s+%s)\b" % (_THING, _THING, _THING, _THING), re.I)
TAKE_DOWN_MAX_WORDS = 12
DOWN_LINE = "It's down."
NOT_DOWN_LINE = "I couldn't take it down."


def take_down_on() -> bool:
    return on() and str(os.environ.get("EAR_BRIDGE_TAKE_DOWN", "1")).strip().lower() not in ("0", "off", "false", "no")


def take_down_heard(text: str) -> bool:
    """A short ask to take the page down (TAKE_DOWN), not a longer sentence that happens to hold the words."""
    t = " ".join(str(text or "").split())
    return bool(t) and take_down_on() and len(t.split()) <= TAKE_DOWN_MAX_WORDS and bool(TAKE_DOWN.search(t))


# "LEAVE IT UP" KEEPS IT (the User 2026-10-30: "make it so I would have to request she leaves it posted is all"): what is
# on the sheet then skips the 30 s clear after her pause, until anything is shown or cleared again.
KEEP_UP = re.compile(
    r"\b(?:leave|keep)\s+%s\s+(?:up|posted|on(?:\s+(?:the\s+)?(?:screen|display))?|there|showing|open)\b" % _THING, re.I)


def keep_heard(text: str) -> bool:
    """The User asks her to leave what she showed up (KEEP_UP), in a short line."""
    t = " ".join(str(text or "").split())
    return bool(t) and on() and len(t.split()) <= TAKE_DOWN_MAX_WORDS + 4 and bool(KEEP_UP.search(t))


def take_down(conductor_url: str, *, post=None) -> tuple[bool, str]:
    """Clear the sheet at the conductor (the tool's own clear): (cleared, result)."""
    c = Calls()
    c.fold([{"index": 0, "id": "take_down", "function": {"name": "display", "arguments": '{"what": "clear"}'}}])
    ran = run(c, conductor_url, post=post)
    res = str(ran[0].get("result") if ran else "NOT DONE")
    return res.startswith("cleared"), res


def on() -> bool:
    return str(os.environ.get(ON_ENV, "1")).strip().lower() not in ("0", "off", "false", "no")


def _lib():
    try:
        from .home import PACK_ROOT
        import sys
        lib = str(PACK_ROOT / "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import crissa_display
        return crissa_display
    except Exception:  # noqa: BLE001 - no display module here: no tool
        return None


def tools(url: str = "") -> list:
    """The tool list for this endpoint: [] when off, on the house, or refused before."""
    if not on() or (url and url in _REFUSED):
        return []
    zd = _lib()
    out = list(zd.tools()) if zd is not None else []
    from . import own_tools                             # cut 15: her own tools beside display (own_tools.py)
    return out + own_tools.tools()


def refused(url: str) -> None:
    _REFUSED.add(url)


class Calls:
    """Tool calls folded from an OpenAI-style stream (pieces) or a whole message: {index: {id, name, args}}."""

    def __init__(self) -> None:
        self.calls: dict = {}

    def __bool__(self) -> bool:
        return any(c.get("name") for c in self.calls.values())

    def fold(self, parts) -> None:
        if not isinstance(parts, list):
            return
        for n, tc in enumerate(parts):
            if not isinstance(tc, dict):
                continue
            i = tc.get("index", n)
            c = self.calls.setdefault(i, {"id": "", "name": "", "args": ""})
            if tc.get("id"):
                c["id"] = str(tc["id"])
            fn = tc.get("function") or {}
            if fn.get("name"):
                c["name"] = str(fn["name"])
            if fn.get("arguments"):
                a = fn["arguments"]
                c["args"] += a if isinstance(a, str) else json.dumps(a)
            if not c["id"]:
                c["id"] = "call_%d" % i

    def line(self, raw: bytes | str) -> None:
        """One SSE line: folds `delta.tool_calls` when it carries any."""
        s = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        if '"tool_calls"' not in s or not s.strip().startswith("data:"):
            return
        try:
            obj = json.loads(s.strip()[5:].strip())
        except ValueError:
            return
        ch = ((obj or {}).get("choices") or [{}])[0] if isinstance(obj, dict) else {}
        self.fold(((ch or {}).get("delta") or {}).get("tool_calls"))

    def message(self, obj: dict) -> None:
        """A whole (non-streamed) reply's message.tool_calls."""
        try:
            self.fold(((obj.get("choices") or [{}])[0].get("message") or {}).get("tool_calls"))
        except Exception:  # noqa: BLE001
            pass

    def done(self) -> list:
        return [self.calls[k] for k in sorted(self.calls) if self.calls[k].get("name")]


def run(calls: Calls, conductor_url: str, *, timeout: float = 20.0, post=None) -> list[dict]:
    """Run each call at the conductor; [{id, name, args, result}]. A failure is a NOT DONE result, never raised."""
    from . import own_tools
    out = []
    for c in calls.done():
        try:
            args = json.loads(c["args"] or "{}")
        except ValueError:
            args = {}
        if own_tools.handles(c["name"]):               # cut 15: her own tools run here, not at the conductor
            out.append(dict(c, result=str(own_tools.run(c["name"], args if isinstance(args, dict) else {}))[:8000]))
            continue
        body = {"name": c["name"], "arguments": args if isinstance(args, dict) else {}}
        try:
            if post is not None:
                r = post(conductor_url, body)
            else:
                req = urllib.request.Request(conductor_url, data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    r = json.loads(resp.read().decode() or "{}")
            result = str((r or {}).get("result") or (r or {}).get("error") or "NOT DONE: no answer")
        except Exception as exc:  # noqa: BLE001
            result = "NOT DONE: %s" % type(exc).__name__
        out.append(dict(c, result=result[:4000]))
    return out


def follow_up(messages: list, said: str, ran: list[dict]) -> list:
    """The messages for the ask after the tool: hers with the calls, then each result."""
    msgs = list(messages) + [{"role": "assistant", "content": said or None, "tool_calls": [
        {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
        for c in ran]}]
    for c in ran:
        res = c["result"]
        if str(res).startswith("shown"):
            res += SHOWN_NOTE
        msgs.append({"role": "tool", "tool_call_id": c["id"], "content": res})
    return msgs


# THE PAGE IS READ, NOT HEARD (round 27, .101 2026-10-16: with the recipe up she read all 974 characters aloud, 71 s;
# the User: with the page up she should not read it unless asked). The result she answers carries the rule again, and
# unless his turn asked her to read it her answer after a shown page is held to SHOWN_MAX_TOKENS. Kill:
# EAR_BRIDGE_SHOWN_SHORT=0.
SHOWN_NOTE = (" - The User can read it on the screen now: say one short sentence about it (what it is), and do not read "
              "it aloud.")
SHOWN_MAX_TOKENS = 70
READ_ASK = re.compile(r"\b(?:read|say|tell)\b[^.?!]{0,30}\b(?:it|that|this|out|aloud|to me|the)\b|\bread\b", re.I)


def shown_short_on() -> bool:
    return str(os.environ.get("EAR_BRIDGE_SHOWN_SHORT", "1")).strip().lower() not in ("0", "off", "false", "no")


def short_after(ran: list[dict], user_text: str) -> int | None:
    """max_tokens for her answer after the tool: SHOWN_MAX_TOKENS when a page went up and he did not ask her to read."""
    if not shown_short_on() or not shown(ran) or READ_ASK.search(str(user_text or "")):
        return None
    return SHOWN_MAX_TOKENS


def shown(ran: list[dict]) -> bool:
    return any(str(c.get("result") or "").startswith("shown") for c in ran)
