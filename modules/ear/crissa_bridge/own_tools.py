"""Her own tools, without krew (the User 2026-10-29: "the standalone needs to be light and have capabilities, which is
what the indexer was meant for"; "work by subtraction": the house Crissa's kit minus the board verbs).

On a plain chat endpoint (Claude, xAI, a local server) she had one tool, `display`. These run inside the bridge, need
nothing installed, and keep her light: she carries a short note and looks up the rest.

- recall(query)          one keyword index (SQLite FTS5, the house-index / krew-docs design without the vectors, which
                         wait for v2): her README and the kit's docs, her memories, documents the User gave her, her
                         day summaries, and her places (the User's usual folders, one row each: what is in it).
- remember(note)         a fact or decision the User wants kept: one line in <home>/memory/memories.jsonl, indexed.
- add_document(...)      a file under the User's home, a web page, or pasted text, copied into <home>/memory/docs/.
- web_read(url)          a public web page as plain text. This machine and private networks are refused, so she
                         cannot reach the local services.

recent_days_note() is the mid-term memory: the last RECENT_DAYS days, each summarised from her own log (what he asked,
what she showed, what she was asked to remember), cached as <home>/memory/days/<date>.json once the day is over. No
model call: the summary is the log's own lines.

On by default in a pack, off on the house (its agent keeps its own). Kill CRISSA_OWN_TOOLS=0.
"""
from __future__ import annotations

import datetime as _dt
import html
import ipaddress
import json
import os
import re
import socket
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from .home import HOME, PACK_ROOT
from .speculate import is_house

ON_ENV = "CRISSA_OWN_TOOLS"
MEM = HOME / "memory"
DB = MEM / "index.db"
MEMORIES = MEM / "memories.jsonl"
DOCS = MEM / "docs"
DAYS = MEM / "days"
LOG = HOME / "logs" / "bridge.log"
CHUNK = 1200
RECALL_N = 5
RECALL_CHARS = 2400
READ_CHARS = 6000
FETCH_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_S = 15.0
DOC_BYTES = 2 * 1024 * 1024
DOC_EXT = {".md", ".txt", ".text", ".rst", ".html", ".htm", ".json", ".csv", ".log", ".yaml", ".yml"}
RECENT_DAYS = int(os.environ.get("CRISSA_RECENT_DAYS", "3") or 0)
RECENT_CHARS = 900
DAY_ASKS = 8
_LOCK = threading.Lock()

NAMES = ("recall", "remember", "add_document", "web_read")


def _fn(name: str, desc: str, props: dict, required: list) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


TOOLS = [
    _fn("recall", "Look something up in what you know: your own README and docs (how you work, your settings, your "
        "camera, krew), your memories, documents the User gave you, and your summaries of recent days. Use it before "
        "saying you do not know something about yourself, the User's notes or earlier days.",
        {"query": {"type": "string", "description": "the words to look for"}}, ["query"]),
    _fn("remember", "Keep a fact, preference or decision the User asks you to remember (or agrees you should keep). "
        "One short sentence in your own words.",
        {"note": {"type": "string", "description": "the thing to keep, one sentence"}}, ["note"]),
    _fn("add_document", "Add a document to what you can recall: a file in the User's home folder (path), a web page "
        "(url), or text the User gives you (text). Give it a short title.",
        {"title": {"type": "string"}, "path": {"type": "string"}, "url": {"type": "string"},
         "text": {"type": "string"}}, ["title"]),
    _fn("web_read", "Read a public web page (an article, a book's text, documentation) by its address and get its "
        "text. Use it when the User asks about something on the web you do not have.",
        {"url": {"type": "string", "description": "the page's address, http:// or https://"},
         "find": {"type": "string", "description": "optional: start reading where this text first appears (a "
                  "chapter title, a heading)"}}, ["url"]),
]

PROMPT_LINE = (" You have your own tools: recall (your README and docs, your memories, documents and your recent days), "
               "remember (keep what the User asks you to keep), add_document, and web_read (read a web page by its "
               "address). When the User asks for something you do not have, look it up with them before saying you "
               "cannot.")


def on(env=None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get(ON_ENV, "")).strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    if raw in ("1", "true", "on", "yes"):
        return True
    try:
        return not is_house()
    except Exception:  # noqa: BLE001
        return False


def tools() -> list:
    return list(TOOLS) if on() else []


def handles(name: str) -> bool:
    return on() and str(name or "") in NAMES


def run(name: str, args: dict) -> str:
    """One call, done: the line she reads back. A failure is a NOT DONE line, never raised."""
    a = args if isinstance(args, dict) else {}
    try:
        if name == "recall":
            return recall(str(a.get("query") or ""))
        if name == "remember":
            return remember(str(a.get("note") or ""))
        if name == "add_document":
            return add_document(title=str(a.get("title") or ""), path=str(a.get("path") or ""),
                                url=str(a.get("url") or ""), text=str(a.get("text") or ""))
        if name == "web_read":
            return web_read(str(a.get("url") or ""), find=str(a.get("find") or ""))
    except Exception as e:  # noqa: BLE001
        return "NOT DONE: %s" % type(e).__name__
    return "NOT DONE: no such tool"


# ---- the index -------------------------------------------------------------------------------------------------------

def _db() -> sqlite3.Connection:
    MEM.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=5.0)
    c.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL, kind TEXT)")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(path UNINDEXED, kind UNINDEXED, title, body)")
    return c


def _chunks(text: str, title: str) -> list[tuple[str, str]]:
    """Heading-sized pieces of at most CHUNK characters, each with the nearest heading as its title."""
    out, head, buf = [], title, []

    def flush():
        body = "\n".join(buf).strip()
        while body:
            out.append((head, body[:CHUNK]))
            body = body[CHUNK:]

    for ln in (text or "").splitlines():
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*)", ln)
        if m:
            flush()
            buf, head = [], (m.group(1).strip() or title)
            continue
        buf.append(ln)
    flush()
    return out


def _sources() -> list[tuple[Path, str]]:
    """Every file the index reads: the pack's docs, her memories, documents and day summaries."""
    out = []
    for p in [PACK_ROOT / "README.md"] + sorted((PACK_ROOT / "docs").rglob("*.md")) + sorted(
            (PACK_ROOT / "modules").glob("*/**/*.md")):
        if p.is_file() and "/tests/" not in str(p) and "/node_modules/" not in str(p):
            out.append((p, "doc"))
    for p in sorted(DOCS.glob("*")) if DOCS.is_dir() else []:
        if p.is_file():
            out.append((p, "document"))
    for p in sorted(DAYS.glob("*.json")) if DAYS.is_dir() else []:
        out.append((p, "day"))
    if MEMORIES.is_file():
        out.append((MEMORIES, "memory"))
    out += [(d, "place") for d in _places()]
    return out


# HER PLACES (the house's lib/house_places, by subtraction; the User 2026-10-29: "Filesystem also was a quick lookup"):
# one row per folder under the User's usual folders - its path, the names of what is in it, a README's text - so recall
# finds a folder by what is in it. Names only, read-only, capped; a folder's own time says when to read it again.
# CRISSA_PLACES = folders separated by ':' (default below), empty = none.
PLACES_DEFAULT = ("Documents", "Desktop", "Downloads", "Pictures", "Videos", "Music")
PLACES_DEPTH = 2
PLACES_MAX_DIRS = 400
PLACES_MAX_NAMES = 60
_PLACE_SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", ".cache", ".thumbnails"}


def _places() -> list[Path]:
    raw = os.environ.get("CRISSA_PLACES")
    home = Path.home()
    roots = [Path(os.path.expanduser(x)) for x in raw.split(":") if x.strip()] if raw is not None else [
        home / n for n in PLACES_DEFAULT]
    out = []
    for r in roots:
        if not r.is_dir():
            continue
        base = len(r.parts)
        for dp, dns, _fns in os.walk(r):
            dns[:] = sorted(d for d in dns if d not in _PLACE_SKIP and not d.startswith("."))
            if len(Path(dp).parts) - base >= PLACES_DEPTH:
                dns[:] = []
            out.append(Path(dp))
            if len(out) >= PLACES_MAX_DIRS:
                return out
    return out


def _place_text(d: Path) -> str:
    try:
        names = sorted(x.name for x in d.iterdir() if not x.name.startswith("."))
    except OSError:
        names = []
    shown = names[:PLACES_MAX_NAMES]
    more = " (and %d more)" % (len(names) - len(shown)) if len(names) > len(shown) else ""
    body = "folder %s\nholds: %s%s" % (d, ", ".join(shown) or "nothing", more)
    for n in ("README.md", "README.txt", "NOTES.md"):
        f = d / n
        if f.is_file() and f.stat().st_size < 64 * 1024:
            body += "\n" + f.read_text(encoding="utf-8", errors="replace")[:2000]
            break
    return body


def _text_of(p: Path, kind: str) -> str:
    if kind == "place":
        return _place_text(p)
    raw = p.read_text(encoding="utf-8", errors="replace")
    if kind == "memory":
        lines = []
        for ln in raw.splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            lines.append("%s: %s" % (str(r.get("day") or ""), str(r.get("note") or "")))
        return "\n".join(lines)
    if kind == "day":
        try:
            return day_text(json.loads(raw))
        except ValueError:
            return ""
    return raw


def refresh() -> int:
    """Index what changed since the last look (by mtime); a file gone leaves the index. Returns files re-read."""
    n = 0
    with _LOCK:
        c = _db()
        try:
            seen = {}
            for p, kind in _sources():
                try:
                    seen[str(p)] = (p.stat().st_mtime, kind)
                except OSError:
                    continue
            have = {r[0]: r[1] for r in c.execute("SELECT path, mtime FROM files")}
            for path in set(have) - set(seen):
                c.execute("DELETE FROM chunks WHERE path = ?", (path,))
                c.execute("DELETE FROM files WHERE path = ?", (path,))
            for path, (mt, kind) in seen.items():
                if have.get(path) == mt:
                    continue
                p = Path(path)
                try:
                    text = _text_of(p, kind)
                except OSError:
                    continue
                c.execute("DELETE FROM chunks WHERE path = ?", (path,))
                title = (str(p) if kind == "place" else p.stem if kind != "doc"
                         else str(p.relative_to(PACK_ROOT)) if PACK_ROOT in p.parents else p.stem)
                for head, body in _chunks(text, title):
                    c.execute("INSERT INTO chunks(path, kind, title, body) VALUES (?,?,?,?)", (path, kind, head, body))
                c.execute("INSERT OR REPLACE INTO files(path, mtime, kind) VALUES (?,?,?)", (path, mt, kind))
                n += 1
            c.commit()
        finally:
            c.close()
    return n


def _match(query: str) -> str:
    words = [w for w in re.findall(r"[\w'-]+", (query or "").lower()) if len(w) > 1][:12]
    return " OR ".join('"%s"' % w.replace('"', "") for w in words)


def recall(query: str) -> str:
    q = _match(query)
    if not q:
        return "NOT DONE: say what to look for"
    refresh()
    with _LOCK:
        c = _db()
        try:
            rows = c.execute("SELECT kind, title, body FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?",
                             (q, RECALL_N)).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            c.close()
    if not rows:
        return "found nothing for: %s" % query.strip()[:120]
    out, left = [], RECALL_CHARS
    for kind, title, body in rows:
        piece = "[%s: %s] %s" % (kind, title, " ".join(body.split()))
        out.append(piece[:left])
        left -= len(out[-1])
        if left <= 0:
            break
    return "found:\n" + "\n".join(out)


def remember(note: str) -> str:
    n = " ".join((note or "").split())[:500]
    if not n:
        return "NOT DONE: nothing to keep"
    MEM.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with MEMORIES.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(), "day": _dt.date.today().isoformat(), "note": n}) + "\n")
    _note_turn("remembered", n)
    return "kept: %s" % n


def _slug(t: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (t or "").lower()).strip("-")[:60]
    return s or "document"


def add_document(*, title: str, path: str = "", url: str = "", text: str = "") -> str:
    t = " ".join((title or "").split())[:80] or "Document"
    if path:
        p = Path(os.path.expanduser(path)).resolve()
        home = Path.home().resolve()
        if home not in p.parents or not p.is_file():
            return "NOT DONE: a document must be a file in the User's home folder"
        if p.suffix.lower() not in DOC_EXT:
            return "NOT DONE: only text documents (%s)" % ", ".join(sorted(DOC_EXT))
        if p.stat().st_size > DOC_BYTES:
            return "NOT DONE: the file is over 2 MB"
        body = p.read_text(encoding="utf-8", errors="replace")
        if p.suffix.lower() in (".html", ".htm"):
            body = _html_text(body)[1]
    elif url:
        got = _fetch(url)
        if got.startswith("NOT DONE"):
            return got
        body = got
    else:
        body = (text or "").strip()
        if not body:
            return "NOT DONE: give a path, a url or the text"
    DOCS.mkdir(parents=True, exist_ok=True)
    dest = DOCS / (_slug(t) + ".md")
    dest.write_text("# %s\n\n%s\n" % (t, body[:DOC_BYTES]), encoding="utf-8")
    refresh()
    return "added: %s (%d characters) - recall finds it now" % (t, len(body))


# ---- the web ---------------------------------------------------------------------------------------------------------

class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def _html_text(raw: str) -> tuple[str, str]:
    p = _Text()
    try:
        p.feed(raw)
    except Exception:  # noqa: BLE001
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return " ".join(p.title.split()), html.unescape(text)


def _public(host: str) -> bool:
    """Only the public internet: this machine and private networks are refused (her local services stay hers)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return bool(infos)


class _NoRedirectToPrivate(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        h = urllib.parse.urlparse(newurl).hostname or ""
        if not _public(h):
            raise urllib.error.HTTPError(newurl, 403, "redirect to a private address", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url: str) -> str:
    u = (url or "").strip()
    pr = urllib.parse.urlparse(u)
    if pr.scheme not in ("http", "https") or not pr.hostname:
        return "NOT DONE: an address must start with http:// or https://"
    if not _public(pr.hostname):
        return "NOT DONE: that address is not on the public internet"
    op = urllib.request.build_opener(_NoRedirectToPrivate)
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Crissa/1.0",
                                             "Accept": "text/html,text/plain;q=0.9,*/*;q=0.5"})
    try:
        with op.open(req, timeout=FETCH_TIMEOUT_S) as r:
            ctype = str(r.headers.get("Content-Type") or "").lower()
            raw = r.read(FETCH_BYTES + 1)[:FETCH_BYTES]
    except urllib.error.HTTPError as e:
        return "NOT DONE: the page answered %s" % e.code
    except Exception as e:  # noqa: BLE001
        return "NOT DONE: could not reach it (%s)" % type(e).__name__
    if not any(k in ctype for k in ("text/", "html", "json", "xml")) and ctype:
        return "NOT DONE: that is not a text page (%s)" % ctype.split(";")[0]
    cs = re.search(r"charset=([\w-]+)", ctype)
    s = raw.decode(cs.group(1) if cs else "utf-8", errors="replace")
    if "html" in ctype or s.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        title, text = _html_text(s)
        return ("%s\n\n%s" % (title, text)).strip() if title else text
    return s


def web_read(url: str, find: str = "") -> str:
    got = _fetch(url)
    if got.startswith("NOT DONE"):
        return got
    _note_turn("read", url.strip()[:160])
    body = got.strip()
    f = " ".join((find or "").split())
    if f:                                        # read from where the text appears; a first one in the page's top
        flat = " ".join(body.split())            # 15 % with another after it is a contents line (a book's chapter
        low = flat.lower()                       # title is listed first, then begins)
        hits = [m.start() for m in re.finditer(re.escape(f.lower()), low)]
        if hits:
            at = hits[1] if len(hits) > 1 and hits[0] < 0.15 * len(flat) else hits[0]
            body = flat[at:]
        else:
            return "NOT DONE: '%s' is not on that page" % f[:80]
    more = len(body) > READ_CHARS
    return "page text%s:\n%s%s" % (" (the first %d characters)" % READ_CHARS if more else "", body[:READ_CHARS],
                                   "\n[...the page goes on]" if more else "")


# ---- what this turn did (her history) and the recent days ------------------------------------------------------------

_TURN: list[str] = []


def turn_start() -> None:
    del _TURN[:]


def _note_turn(what: str, detail: str) -> None:
    _TURN.append("%s %s" % (what, " ".join((detail or "").split())[:160]))


def note_ran(ran: list[dict]) -> None:
    """Each call this turn ran, as one short line for her history (display's result names what went up)."""
    for c in ran or []:
        name, res = str(c.get("name") or ""), " ".join(str(c.get("result") or "").split())
        if name == "display":
            _note_turn("display:", res[:160])
        elif name in ("recall", "add_document") or res.startswith("NOT DONE"):
            _note_turn(name + ":", res[:120])


def turn_note() -> str:
    """'[What I did this turn: ...]' for her history, or ''. Kill CRISSA_HISTORY_TOOLS=0."""
    if not _TURN or str(os.environ.get("CRISSA_HISTORY_TOOLS", "1")).strip().lower() in ("0", "off", "false", "no"):
        return ""
    return " [What I did this turn: %s.]" % "; ".join(_TURN[-4:])


def day_text(d: dict) -> str:
    parts = []
    if d.get("asked"):
        parts.append("he asked: " + " | ".join(d["asked"]))
    if d.get("shown"):
        parts.append("I showed: " + " | ".join(d["shown"]))
    if d.get("remembered"):
        parts.append("I kept: " + " | ".join(d["remembered"]))
    return "%s - %s" % (d.get("day") or "", "; ".join(parts) or "a quiet day")


def _summarise(day: str) -> dict:
    """One day from her own log: his questions (owner turns), what she put on the screen, what she kept."""
    asked, shown = [], []
    try:
        with LOG.open(encoding="utf-8", errors="replace") as f:
            for ln in f:
                if ('"ts": "%s' % day) not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                ev = r.get("event")
                if ev == "dispatch" and r.get("level") in ("bau", "allow", "legacy") and len(asked) < DAY_ASKS:
                    u = " ".join(str(r.get("user") or "").split())
                    if len(u) > 12:
                        asked.append(u[:90])
                elif ev == "display_tool" and str(r.get("result") or "").startswith("shown"):
                    shown.append(str(r.get("result"))[7:90].strip())
    except OSError:
        pass
    kept = []
    if MEMORIES.is_file():
        for ln in MEMORIES.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("day") == day:
                kept.append(str(r.get("note") or "")[:120])
    return {"day": day, "asked": asked, "shown": shown[:6], "remembered": kept[:6]}


def recent_days_note(today: _dt.date | None = None) -> str:
    """The last RECENT_DAYS days before today, one line each (cached once the day is over), for her system prompt."""
    if RECENT_DAYS <= 0 or not on():
        return ""
    today = today or _dt.date.today()
    lines = []
    for k in range(RECENT_DAYS, 0, -1):
        day = (today - _dt.timedelta(days=k)).isoformat()
        f = DAYS / (day + ".json")
        d = None
        try:
            d = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
        except (OSError, ValueError):
            d = None
        if d is None:
            d = _summarise(day)
            try:
                DAYS.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        if d.get("asked") or d.get("shown") or d.get("remembered"):
            lines.append(day_text(d))
    if not lines:
        return ""
    note = "\nRecent days (from your own log; recall has more):\n" + "\n".join(lines)
    return note[:RECENT_CHARS]
