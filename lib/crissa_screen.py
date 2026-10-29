"""What a helper puts on the Wall beside her (2026-10-06): one card of words, art, a page or a web address.

An agent or a helper on this machine never touches the Wall's files or its browser; it posts here, the player keeps
the card, and the Wall page draws it next to her (display/crissa-wall.html). `POST /v1/screen` merges fields;
`{"clear": "all"}` (or a list of fields) takes them down; `GET /v1/screen` reads back what is up, so a helper can
report what the glass shows and never just that a post was accepted.

Fields (all text):
    title                    the card's heading
    notes, notes_title       a short note
    doc, doc_title           a longer page of words
    art, art_label           text art, drawn in a fixed-width font
    url                      a web address (http or https only), shown in a frame
    html                     a whole page, shown in a frame with no scripts and no access to the player

Nothing is shortened here: a long page stays whole and the card scrolls. Anything not in FIELDS is returned in
`ignored`, so a caller sees what did not land. The news line and the weather (OWNED) belong to their own jobs: a
clear that names them, or a null or empty value riding along on one, leaves them up and is answered in `kept` (the
house's own feed does the same: lib/vo_news_slot.py); a real value for one is not kept here, so it is `ignored`.
The voice module's health (GET /v1/ear) and the services she may sit beside are read the same way, through the
player: lib/crissa_rails.py.
"""
from __future__ import annotations

import threading
import time

FIELDS = ("title", "notes", "notes_title", "doc", "doc_title", "art", "art_label", "url", "html")
# News headline, its note, and the outdoor line. An older feature posted these
# onto the glass; clear of the card must not take them down.
OWNED = ("headline", "note", "weather")


class Screen:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.card: dict[str, str] = {}
        self.at = 0.0
        self.by = ""

    def view(self) -> dict:
        with self.lock:
            return dict(self.card, at=self.at, by=self.by, up=bool(self.card))

    def apply(self, body: dict) -> tuple[int, dict]:
        """(http status, reply). 400 names the field that was wrong; nothing changes then."""
        if not isinstance(body, dict):
            return 400, {"ok": False, "error": "send a JSON object"}
        clear = body.get("clear")
        if clear in (True, "all", "*"):
            clear = list(FIELDS)
        elif isinstance(clear, str):
            clear = [clear]
        elif clear is None or clear is False:
            clear = []
        if not isinstance(clear, list) or not all(isinstance(c, str) for c in clear):
            return 400, {"ok": False, "error": "clear is \"all\" or a list of field names"}
        kept = [k for k in clear if k in OWNED]
        clear = [k for k in clear if k not in OWNED]
        kept += [k for k in OWNED if k in body and body[k] in (None, "") and k not in kept]   # a clear riding along
        new: dict[str, str] = {}
        for k in FIELDS:
            if k not in body or body[k] is None:
                continue
            v = body[k]
            if not isinstance(v, (str, int, float)) or isinstance(v, bool):
                return 400, {"ok": False, "error": "%s must be text" % k}
            new[k] = str(v)
        url = new.get("url", "").strip()
        if "url" in new and url and not url.lower().startswith(("http://", "https://")):
            return 400, {"ok": False, "error": "url must start with http:// or https://"}
        ignored = sorted(k for k in body if k not in FIELDS and k not in ("clear", "by") and k not in kept)
        with self.lock:
            for k in clear:
                self.card.pop(k, None)
            for k, v in new.items():
                if v.strip():
                    self.card[k] = v
                else:
                    self.card.pop(k, None)
            self.at = self.clock()
            self.by = str(body.get("by") or "")[:80]
        out = {"ok": True, "screen": self.view()}
        if ignored:
            out["ignored"] = ignored
        if kept:
            out["kept"] = kept
        return 200, out

