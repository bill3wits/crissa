"""THE DISPLAY TOOL (the User 2026-10-16: "let's give her that single tool; a display tool so she can show a document or
lookup page in browser in 8x11 size on right side to match house tool").

Her one tool, offered to whatever agent answers her (lib/crissa_agent, OpenAI function tools; an endpoint that refuses
tools is asked without them). It does what the house's display tool does with a page, in the kit's own parts:

    document   her words as a letter: the card beside her (lib/crissa_screen, `doc`) and the sheet window (the player's
               /letter page) in her own browser, 8.5 x 11 on the right of the screen (crissa_window.letter_rect, the
               house's letter_rect formula)
    page       a web address (http or https) in the same sheet window; the card carries the address
    lookup     words to look up: a web search for them (SEARCH_URL, CRISSA_DISPLAY_SEARCH) in the sheet window
    clear      the card comes down and the sheet window closes

The sheet window is kept above other windows (crissa_window, CRISSA_SHEET_ABOVE=0 turns that off). The card being up
is what the Walls read as "a page is up": the browser Wall moves her onto the camera tile, and the desk Wall slides her
to her kept spot when the person has dragged her there (doc_placement). The result she reads back says what is on the
screen now, or NOT DONE and why - never that a page went up when it did not.

A guest never gets the tool (the conductor offers it only to an owner's turn). A pack has it, the house does not
(is_house: the house's display tool is her hands'). Kill: CRISSA_DISPLAY_TOOL=0; =1 offers it on the house too.
"""
from __future__ import annotations

import os
import urllib.parse

import crissa_home

NAME = "display"
SEARCH_URL = "https://duckduckgo.com/?q={q}"
MAX_DOC_CHARS = 20000
WHATS = ("document", "page", "lookup", "clear")

TOOL = {
    "type": "function",
    "function": {
        "name": NAME,
        "description": (
            "Show the User something to read on the screen: a document (your own words, laid out as a letter page), a "
            "web page (an http or https address), or a web search for some words - in a letter-sized sheet on the right "
            "of the screen, beside you. Use it when the User asks to see, show, pull up, open or look up something, or "
            "when a list, steps, a recipe or a long answer is better read than heard. If the User has not said which one "
            "(\"a recipe\" with no dish named), ask which first and show nothing yet. 'clear' takes the sheet down. "
            "After it, say one short line about what is up; never read the whole page aloud. Say it is up only when "
            "the result says shown."),
        "parameters": {
            "type": "object",
            "properties": {
                "what": {"type": "string", "enum": list(WHATS),
                         "description": "document | page | lookup | clear"},
                "title": {"type": "string", "description": "a short heading for the sheet"},
                "text": {"type": "string",
                         "description": "what=document: the words, plain text; blank lines between paragraphs"},
                "url": {"type": "string", "description": "what=page: the address, starting http:// or https://"},
                "query": {"type": "string", "description": "what=lookup: the words to search the web for"},
            },
            "required": ["what"],
        },
    },
}


def on(env=None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("CRISSA_DISPLAY_TOOL", "")).strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    if raw in ("1", "true", "on", "yes"):
        return True
    try:
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001
        return False


def tools(env=None) -> list:
    return [TOOL] if on(env) else []


def search_url(query: str, env=None) -> str:
    env = os.environ if env is None else env
    tpl = str(env.get("CRISSA_DISPLAY_SEARCH") or SEARCH_URL)
    if "{q}" not in tpl or not tpl.lower().startswith(("http://", "https://")):
        tpl = SEARCH_URL
    return tpl.replace("{q}", urllib.parse.quote_plus(str(query).strip()))


def plan(args: dict, env=None) -> dict:
    """What one call asks for: {"what", "card" (the /v1/screen body), "url" (the sheet's address, None = /letter)} or
    {"error"}. Pure: nothing shown."""
    a = args if isinstance(args, dict) else {}
    what = str(a.get("what") or "").strip().lower()
    title = " ".join(str(a.get("title") or "").split())[:80]
    if not what:
        what = "page" if a.get("url") else "lookup" if a.get("query") else "document" if a.get("text") else ""
    if what not in WHATS:
        return {"error": "what must be one of %s" % ", ".join(WHATS)}
    if what == "clear":
        return {"what": what, "card": {"clear": "all", "by": NAME}, "url": None}
    if what == "document":
        text = str(a.get("text") or "").replace("\r", "").strip()
        if not text:
            return {"error": "a document needs its text"}
        return {"what": what, "url": None, "lines": len(text.splitlines()),
                "card": {"clear": "all", "by": NAME, "title": title or "Notes", "doc": text[:MAX_DOC_CHARS],
                         "doc_title": title or "Notes"}}
    if what == "lookup":
        q = " ".join(str(a.get("query") or a.get("text") or title).split())[:300]
        if not q:
            return {"error": "a lookup needs the words to search for"}
        u = search_url(q, env)
        return {"what": what, "url": u, "card": {"clear": "all", "by": NAME, "title": title or q[:80], "url": u}}
    u = str(a.get("url") or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        return {"error": "a page needs an address that starts with http:// or https://"}
    return {"what": what, "url": u, "card": {"clear": "all", "by": NAME, "title": title or u[:80], "url": u}}


def run(args: dict, *, apply_card, open_sheet, close_sheet, env=None) -> str:
    """One call, done: the card (apply_card(body) -> (status, reply)), then the sheet window (open_sheet(url) -> dict,
    close_sheet() on a clear). Returns the line she reads back: "shown: ...", "cleared", or "NOT DONE: ..."."""
    p = plan(args, env)
    if p.get("error"):
        return "NOT DONE: " + p["error"]
    code, out = apply_card(p["card"])
    if code != 200 or not (out or {}).get("ok"):
        return "NOT DONE: the screen refused it (%s)" % str((out or {}).get("error") or code)[:120]
    if p["what"] == "clear":
        close_sheet()
        return "cleared: the sheet is down"
    win = open_sheet(p["url"]) or {}
    title = p["card"].get("title") or ""
    if not win.get("ok"):
        return ("NOT DONE: the card is up beside you but the sheet window did not open (%s)"
                % str(win.get("message") or win.get("why") or win.get("error") or "no window")[:120])
    kind = {"document": "document, %d lines" % p.get("lines", 0), "page": "web page", "lookup": "web search"}[p["what"]]
    return "shown: %s (%s) in the sheet on the right of the screen" % (title, kind)
