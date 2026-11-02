"""Connect: the player talks to any agent that speaks OpenAI chat-completions (2026-09-28).

WHY. The public packs could play her and move her mouth, but nothing in them
could ANSWER a person: the only thing that turned a heard turn into a reply was
the house's ear-bridge (rail-voice), which is not in any pack and is wired to
one agent framework by default (its ports, profile paths and session header).
Its wire, though, was always the generic one — OpenAI chat-completions, SSE when
the server streams, plain JSON when it does not — so the seam is a URL, a key
and a model name. This module is that seam; the conductor's `/v1/ask` and
`/v1/agent` routes and the Connect dialog are its only callers.

WHAT IS STORED. `<crissa home>/agent.json`, mode 0600:

    {"url": "http://127.0.0.1:8000/v1", "key": "...", "model": "...",
     "preset": "openai-compatible" | "local" | "claude" | "xai" | "none",
     "system_prompt": "...", "session_header": "X-...-Session-Id"}

`url` may be a base (`.../v1`) or the full `.../chat/completions`; both work.
The key is never returned by `redacted()` and never logged. Env beats the file
(`CRISSA_AGENT_URL`, `_KEY`, `_MODEL`, `_PRESET`) so a service unit can pin one.

A KEY NEVER FOLLOWS A NEW HOST. `save()` drops the stored key when the url's
host changes and the caller did not send a key with it: the Connect page is
reachable from the loopback, and "change the url, keep the key" is exactly how
a key gets sent somewhere its owner never chose.

An agent gateway that serves several agents under one host is just a url
(`http://host:port/p/<agent>/v1`); one that keeps its own memory per session
names its header in `session_header` and gets a stable id per conversation.
Nothing here imports an agent framework or anything from the house.

OFFERS (2026-10-06). Another program on this machine may offer an endpoint by writing
`<crissa home>/endpoints.d/<id>.json`: {"name", "url", "model"?, "session_header"?, "system_prompt"?,
"key_ref"?}. Connect and the Init list them as "Detected"; nothing changes until the user picks one
(`adopt()`). A key is never copied: `key_ref` names where it lives - {"file": "/abs/path", "name": "VAR"}
(a `VAR=value` line in that file) or {"env": "VAR"} - and it is read when a question is sent. An offer whose
`system_prompt` is "" sets `no_prompt`: the agent has its own personality and gets no system message.

NO HIDDEN LIMITS (2026-10-06). The reply is not cut here and the question has no timeout unless the talk
settings set one (lib/crissa_talk.py: reply.max_chars, reply.timeout_s; both 0 = none by default).

HER PERSONA (the User 2026-10-07). With no prompt of the user's own she gets the stock persona shipped beside
her manifest (`avatars/crissa/persona.md`: her voice and the "default short" coaching, nothing of any one
install), not a one-line starter. Precedence: an adopted offer that brings its own personality (krew's marry
offer, `system_prompt ""`) > the user's own text (Save on the Connect page) > the stock persona. `none`
(no_prompt by the user's own hand) sends no system message. A stored prompt equal to the stock persona or
to the old one-line starter (which the page used to prefill and Save stored) counts as stock, so the stock
text can improve under it. `persona_view()` is what a page sees: {source: offer|user|stock|none, by, stock}.

Kill: `CRISSA_CONNECT=0` (the conductor's routes answer 404).
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crissa_egress  # noqa: E402

try:
    from crissa_home import HOME
except ImportError:  # imported by path from a test or a tool
    HOME = Path(os.environ.get("CRISSA_HOME") or (Path.home() / ".crissa")).expanduser()

PRESETS = ("openai-compatible", "local", "claude", "xai", "none")
DEFAULT_PRESET = "openai-compatible"
# THE KINDS (the User 2026-10-10): Local, Claude API and xAI fill the Connect form's address and model; the key field
# is the user's and stays as it is. All three speak the same OpenAI chat wire. Claude: Anthropic's OpenAI-compatible
# endpoint (https://api.anthropic.com/v1, the key as `Authorization: Bearer`), claude-fable-5-1 then (round 17 moved
# the kind to claude-haiku-5-5, below), the most capable model its docs list on 2026-10-10 (claude-opus-5-5 is the
# docs' starting point; claude-sonnet-5-5 faster, claude-haiku-5-5 fastest and cheapest). xAI:
# grok-4.20-0309-non-reasoning (2026-10-12, the .101 demo): xAI's grok-4.5 to 4.7 always reason, at high effort by
# default, and their first clause took 4.7-27 s on .101; this model, on xAI's models page, answered in 0.44-0.69 s.
# grok-4.7 stays a model a person may type. Local: this machine's own engine as krew's engines preset runs it (ops on
# 8000). display/crissa-connect.html carries the same table (a test holds the two equal).
# THE CLAUDE PRESET (the User 2026-10-13, round 17: "yes do 1, add the Claude preset"): Claude as her brain through
# Anthropic's OpenAI-compatible layer (base https://api.anthropic.com/v1/, the key as `Authorization: Bearer`, chat
# completions streamed - Anthropic's "OpenAI SDK compatibility" page, 2026-10-13), beside the xAI kind, which stays as
# it was. The kind is named "Claude (Anthropic)" and fills claude-haiku-5-5: the fastest Claude, the one for a voice
# avatar (a spoken reply waits for the whole first clause). The person pastes their own key; it is stored like any key
# (the same agent.json, 0600). The pick list is Anthropic's own model list when it answers (its native GET /v1/models
# reads `x-api-key` + `anthropic-version`; the Bearer header is asked once more if that is refused), else CLAUDE_MODELS,
# Haiku first. A reply Claude declines (finish_reason "refusal", or OpenAI's "content_filter") is said as a refusal,
# never as a connection error. Kill: CRISSA_CONNECT_CLAUDE_PRESET=0 hides the kind from Connect, Setup and the Wall's
# dialog (a stored Claude address still works as any OpenAI-compatible one) and puts back round 15's one-ask model list.
CLAUDE_VOICE_MODEL = "claude-haiku-5-5"
# Anthropic's current models (its docs, 2026-10-13), Haiku first: the list when Anthropic's own does not answer
CLAUDE_MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1")
CLAUDE_LABELS = {"claude-haiku-5-5": "Claude Haiku 5.5 - the fastest, the one for a voice avatar",
                 "claude-sonnet-5-5": "Claude Sonnet 5.5", "claude-opus-5-5": "Claude Opus 5.5",
                 "claude-fable-5-1": "Claude Fable 5.1 - the most capable, slower to answer"}
VOICE_MARK = " - the one for a voice avatar"
ANTHROPIC_VERSION = "2023-06-01"
KINDS = {
    "local": {
        "label": "Local (this machine's own engine)",
        "url": "http://127.0.0.1:8000/v1",
        "model": "ops"
    },
    "claude": {
        "label": "Claude (Anthropic)",
        "url": "https://api.anthropic.com/v1",
        "model": CLAUDE_VOICE_MODEL,
        "models": list(CLAUDE_MODELS),
        "labels": dict(CLAUDE_LABELS),
    },
    "xai": {
        "label": "xAI",
        "url": "https://api.x.ai/v1",
        "model": "grok-4.20-0309-non-reasoning"
    },
}


def claude_preset_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_CONNECT_CLAUDE_PRESET", "1")).strip().lower() not in ("0", "false", "no", "off")


def kinds(env: dict | None = None) -> dict:
    """The kinds a page offers (a copy): the Claude kind only while CRISSA_CONNECT_CLAUDE_PRESET is on."""
    on = claude_preset_on(env)
    return {k: json.loads(json.dumps(v)) for k, v in KINDS.items() if k != "claude" or on}


def presets(env: dict | None = None) -> list[str]:
    """The presets a page offers. `validate()` still takes every one in PRESETS: a stored config keeps working."""
    on = claude_preset_on(env)
    return [p for p in PRESETS if p != "claude" or on]
# Claude 4.7 and later return a 400 for any temperature below 1 on this wire (the OpenAI-compatibility page,
# 2026-10-10): a Claude model is asked with the server's own temperature.
NO_TEMPERATURE_HOSTS = ("api.anthropic.com",)
# REASONING MODELS (2026-10-10, the .101 demo): OpenAI's o-series and gpt-5 refuse `max_tokens` (they take
# `max_completion_tokens`, which also counts their thinking) and any temperature but the default. The rule, two parts:
#   1. a model of those families (a provider prefix like "openai/" ignored) is asked with no temperature, and its cap,
#      when a caller sets one, goes in `max_completion_tokens`;
#   2. any other model that answers HTTP 400 naming `temperature`, `max_tokens` or `max_completion_tokens` is asked
#      ONCE more without what it named (a 400 that names max_completion_tokens as the replacement moves the cap there),
#      and that endpoint + model is remembered for this process, so the next question is sent right the first time.
REASONING_MODEL = re.compile(r"^(o\d+([-_.].*)?|gpt-5([-_.].*)?)$", re.I)
SAMPLING_PARAMS = ("temperature", "max_tokens", "max_completion_tokens", "top_p")
_REFUSED: dict[tuple[str, str], set] = {}     # (chat url, model) -> the body keys that endpoint refused
# THE TEST'S BUDGET (2026-10-10): a thinking model spends tokens thinking before its answer, so 48 tokens was a test it
# could never pass. The test asks with TEST_MAX_TOKENS; a reply that is all thinking (an open <think>, or only
# reasoning frames) is "still thinking", asked once more with no cap of ours, inside the same time limit.
TEST_MAX_TOKENS = 1024
TEST_TIMEOUT_S = 60.0
STILL_THINKING = ("The agent is still thinking: it used the whole test on thinking and gave no answer yet. "
                  "Test again, or give it more time.")
DEFAULT_PROMPT = ("You are Crissa, an avatar on the User's screen. Your words are spoken aloud: "
                  "answer in one to three short sentences, plain text, no lists, no markdown.")
# what older pages prefilled and stored (the 2026-09 starter carried her old name): read as "no text of the user's own"
LEGACY_PROMPTS = (DEFAULT_PROMPT, DEFAULT_PROMPT.replace("Crissa", "Z" + "oey"))
PERSONA_FILE = Path(__file__).resolve().parent.parent / "avatars" / "crissa" / "persona.md"
FIELDS = ("url", "key", "model", "preset", "system_prompt", "session_header", "no_prompt", "key_ref", "prompt_from")
MAX_TOOL_ROUNDS = 2            # a question, then (when she called a tool) its results once: never a loop
MAX_REPLY_CHARS = 0            # 0 = no cut; a limit, when set, lives in the talk settings (reply.max_chars)
OFFER_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_CONNECT", "1")).strip().lower() not in ("0", "false", "no", "off")


def path(home: Path | None = None) -> Path:
    return (home or HOME) / "agent.json"


def load(home: Path | None = None, env: dict | None = None) -> dict:
    """The stored config with env overrides on top. Missing file = {}."""
    e = os.environ if env is None else env
    try:
        cfg = json.loads(path(home).read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            cfg = {}
    except (OSError, ValueError):
        cfg = {}
    cfg = {k: cfg[k] for k in FIELDS if k in cfg}
    for k in ("url", "key", "model", "preset"):
        v = str(e.get("CRISSA_AGENT_" + k.upper()) or "").strip()
        if v:
            cfg[k] = v
    if not cfg.get("key") and isinstance(cfg.get("key_ref"), dict):
        key = resolve_key(cfg["key_ref"], env=e)
        if key:
            cfg["key"] = key            # in memory only: the file keeps the reference, never the key
    return cfg


def resolve_key(ref, env: dict | None = None) -> str:
    """The key a `key_ref` names, read now; "" when it cannot be read. Never logged."""
    e = os.environ if env is None else env
    if not isinstance(ref, dict):
        return ""
    if ref.get("env"):
        return str(e.get(str(ref["env"])) or "").strip()
    f, name = str(ref.get("file") or ""), str(ref.get("name") or "")
    if not f or not ENV_NAME.match(name):
        return ""
    try:
        for line in Path(f).expanduser().read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            if line.startswith(name + "="):
                return line[len(name) + 1:].strip().strip("'\"")
    except (OSError, UnicodeDecodeError):
        return ""
    return ""


def _ref_error(ref) -> str | None:
    if ref is None:
        return None
    if not isinstance(ref, dict):
        return "key_ref must be {\"file\", \"name\"} or {\"env\"}"
    if ref.get("env") is not None:
        return None if ENV_NAME.match(str(ref["env"])) else "key_ref.env must be a variable name"
    f = str(ref.get("file") or "")
    if not f.startswith(("/", "~")) or "\n" in f or len(f) > 500:
        return "key_ref.file must be an absolute path"
    if not ENV_NAME.match(str(ref.get("name") or "")):
        return "key_ref.name must be a variable name"
    return None


def redacted_ref(ref) -> dict | None:
    """Where a key is read from (no value): what a page may show."""
    if not isinstance(ref, dict):
        return None
    return {"env": ref["env"]} if ref.get("env") else {"file": ref.get("file"), "name": ref.get("name")}


def stock_persona(path_: Path | None = None) -> str:
    """The shipped persona (avatars/crissa/persona.md), read each call; the one-line starter when it is missing."""
    try:
        t = (path_ or PERSONA_FILE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        t = ""
    return t or DEFAULT_PROMPT


def _norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def own_prompt(cfg: dict) -> str:
    """The user's own persona text, or "" when what is stored is empty, the stock persona or the old starter."""
    t = str(cfg.get("system_prompt") or "")
    if not t.strip() or _norm(t) in {_norm(stock_persona())} | {_norm(x) for x in LEGACY_PROMPTS}:
        return ""
    return t


def persona_source(cfg: dict) -> str:
    """offer (an adopted offer's own personality or prompt) > user > stock; none = the user ticked None."""
    if cfg.get("prompt_from"):
        return "offer"
    if cfg.get("no_prompt"):
        return "none"
    return "user" if own_prompt(cfg) else "stock"


def system_message(cfg: dict) -> str | None:
    """The system message one question carries; None = none (the agent brings its own personality)."""
    if cfg.get("no_prompt"):
        return None
    return own_prompt(cfg) or (str(cfg.get("system_prompt") or "").strip() if cfg.get("prompt_from") else "") \
        or stock_persona()


def persona_view(cfg: dict) -> dict:
    return {"source": persona_source(cfg), "by": str(cfg.get("prompt_from") or ""),
            "agent_own": bool(cfg.get("prompt_from") and cfg.get("no_prompt")), "stock": stock_persona()}


def offers_dir(home: Path | None = None) -> Path:
    return (home or HOME) / "endpoints.d"


def offers(home: Path | None = None, env: dict | None = None) -> list[dict]:
    """Every endpoint another program offered, as a page may see it (never a key). Bad files are listed with
    the reason so the user sees why one is not offered."""
    out = []
    d = offers_dir(home)
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        files = []
    for f in files:
        oid = f.stem
        row = {"id": oid, "file": str(f)}
        try:
            o = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(o, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as exc:
            out.append(dict(row, ok=False, error="unreadable: %s" % exc))
            continue
        why = (None if OFFER_ID.match(oid) else "bad file name") or validate(
            {k: o.get(k) for k in ("url", "model", "session_header", "system_prompt") if o.get(k) is not None}) \
            or (None if str(o.get("url") or "").strip() else "no url") or _ref_error(o.get("key_ref"))
        row.update(name=str(o.get("name") or oid)[:120], url=str(o.get("url") or ""), model=str(o.get("model") or ""),
                   session_header=str(o.get("session_header") or ""), by=str(o.get("by") or "")[:80],
                   no_prompt=o.get("system_prompt") == "", key_ref=redacted_ref(o.get("key_ref")),
                   has_key=bool(resolve_key(o.get("key_ref"), env=env)) if o.get("key_ref") else False,
                   ok=why is None)
        if why:
            row["error"] = why
        out.append(row)
    return out


def adopt(offer_id: str, home: Path | None = None) -> dict:
    """The user picked an offer: store its address, model, header and key reference (never the key).
    Raises ValueError when the offer is missing or bad."""
    if not OFFER_ID.match(str(offer_id or "")):
        raise ValueError("no such offer")
    f = offers_dir(home) / (str(offer_id) + ".json")
    try:
        o = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("no such offer") from None
    if not isinstance(o, dict):
        raise ValueError("no such offer")
    upd = {"preset": DEFAULT_PRESET, "url": str(o.get("url") or ""), "model": str(o.get("model") or ""),
           "session_header": str(o.get("session_header") or ""), "no_prompt": o.get("system_prompt") == "",
           "key_ref": o.get("key_ref"), "clear_key": True}
    if o.get("system_prompt") == "" or (isinstance(o.get("system_prompt"), str) and o["system_prompt"]):
        # its personality outranks the user's text and the stock persona until the user changes the persona
        upd["prompt_from"] = str(o.get("by") or offer_id)[:80]
    if isinstance(o.get("system_prompt"), str) and o["system_prompt"]:
        upd["system_prompt"] = o["system_prompt"]
    if not upd["url"]:
        raise ValueError("the offer has no url")
    why = _ref_error(upd["key_ref"])
    if why:
        raise ValueError(why)
    return save(upd, home, adopting=True)


def configured(cfg: dict) -> bool:
    return bool(str(cfg.get("url") or "").strip()) and cfg.get("preset") != "none"


def redacted(cfg: dict) -> dict:
    """What a page may see: everything but the key, plus whether one is set."""
    out = {k: cfg.get(k) for k in FIELDS if k not in ("key", "key_ref") and k in cfg}
    if cfg.get("key_ref"):
        out["key_ref"] = redacted_ref(cfg["key_ref"])
    out.setdefault("preset", DEFAULT_PRESET)
    out["has_key"] = bool(cfg.get("key"))
    out["configured"] = configured(cfg)
    out["persona"] = persona_view(cfg)
    return out


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).netloc or "").lower()
    except ValueError:
        return ""


def validate(new: dict) -> str | None:
    """None when the fields are acceptable, else the reason."""
    url = str(new.get("url") or "").strip()
    if url:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return "url must be http(s)://host[:port]/..."
    preset = str(new.get("preset") or DEFAULT_PRESET)
    if preset not in PRESETS:
        return "preset must be one of " + ", ".join(PRESETS)
    for k in ("model", "session_header"):
        v = str(new.get(k) or "")
        if len(v) > 200 or "\n" in v:
            return k + " is too long"
    hdr = str(new.get("session_header") or "")
    if hdr and (not re.fullmatch(r"[A-Za-z0-9-]+", hdr) or hdr.lower() in ("authorization", "host", "cookie",
                                                                            "content-type", "content-length")):
        return "session_header must be a plain header name"
    if len(str(new.get("system_prompt") or "")) > 8000:
        return "system_prompt is too long (8000 characters)"
    if "key_ref" in new:
        why = _ref_error(new.get("key_ref"))
        if why:
            return why
    return None


def save(update: dict, home: Path | None = None, *, adopting: bool = False) -> dict:
    """Merge `update` into the stored file (0600) and return the stored config.

    `key` absent or "" keeps the stored key — unless the host changed, then it
    is dropped (see the module note). `clear_key: true` drops it outright.
    Raises ValueError with the reason on a bad field.
    """
    why = validate(update)
    if why:
        raise ValueError(why)
    p = path(home)
    try:
        cur = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(cur, dict):
            cur = {}
    except (OSError, ValueError):
        cur = {}
    new = dict(cur)
    if not adopting and ("system_prompt" in update or "no_prompt" in update):
        new.pop("prompt_from", None)    # the user set the persona by hand: an offer's personality no longer rules
    for k in FIELDS:
        if k in ("key", "key_ref") or k not in update:
            continue
        if k == "prompt_from" and not adopting:
            continue
        v = update[k]
        if k == "no_prompt":
            new[k] = bool(v)
            continue
        new[k] = str(v).strip() if k != "system_prompt" else str(v)
    if not adopting and "system_prompt" in new and not own_prompt(new):
        new["system_prompt"] = ""      # the stock text (or the old starter) is not the user's own: keep it unstored
    if adopting and "prompt_from" not in update:
        new.pop("prompt_from", None)
    key_in = str(update.get("key") or "").strip()
    host_moved = "url" in update and _host(str(update.get("url") or "")) != _host(str(cur.get("url") or ""))
    if host_moved and not adopting and new.get("prompt_from"):
        # the offer's personality belonged to its address: off that address she wears her own persona again
        if "no_prompt" not in update:
            new.pop("no_prompt", None)
        new.pop("prompt_from", None)
    if key_in:
        new["key"] = key_in
        new.pop("key_ref", None)        # a typed key replaces a reference
    elif update.get("clear_key") or host_moved:
        new.pop("key", None)
    if update.get("key_ref"):
        new["key_ref"] = dict(update["key_ref"])
    elif update.get("clear_key") or host_moved or key_in or ("key_ref" in update and not update["key_ref"]):
        new.pop("key_ref", None)        # a reference never follows a new host either
    # A key this module does not own (the voice's `tts` block) is kept as stored; only the update's FIELDS are read.
    new = {k: v for k, v in new.items() if k not in FIELDS or v not in (None, "", False)}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(new, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return new


def chat_url(cfg: dict) -> str:
    """The chat-completions URL for this config. The two documented shapes: a base (`http://host:port/v1`, or a gateway's
    `.../p/<agent>/v1`) gets `/chat/completions`; the full `.../chat/completions` is used as it is. A bare host (no
    path at all) is the OpenAI-compatible default base `/v1`. A base with a path of its own is used as given, whatever
    its last segment (2026-10-10: `.../v1beta/openai` and `.../api` were sent to `.../v1/chat/completions`, a path the
    server never had)."""
    url = str(cfg.get("url") or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    try:
        path = urllib.parse.urlsplit(url).path if url else ""
    except ValueError:
        path = ""
    if not path.strip("/"):
        url += "/v1"
    return url + "/chat/completions"


def _headers(cfg: dict, session_id: str | None) -> dict:
    h = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if cfg.get("key"):
        h["Authorization"] = "Bearer " + str(cfg["key"])
    hdr = str(cfg.get("session_header") or "").strip()
    if hdr and session_id:
        h[hdr] = session_id
    return h


def sse_piece(line: str) -> str | None:
    """One SSE line → its text. None = nothing here; "" = [DONE]."""
    s = (line or "").strip()
    if not s.startswith("data:"):
        return None
    data = s[5:].strip()
    if data == "[DONE]":
        return ""
    try:
        obj = json.loads(data)
    except ValueError:
        return None
    ch = (obj.get("choices") or [{}])[0] if isinstance(obj, dict) else {}
    for part in ((ch.get("delta") or {}).get("content"), (ch.get("message") or {}).get("content")):
        if isinstance(part, str) and part:
            return part
    return None


def message_text(obj: dict) -> str:
    msg = ((obj.get("choices") or [{}])[0] or {}).get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        content = " ".join(str(p.get("text") if isinstance(p, dict) else p) for p in content
                           if (isinstance(p, dict) and p.get("text")) or isinstance(p, str))
    return str(content or "")


def speakable(text: str, limit: int = MAX_REPLY_CHARS) -> str:
    """What the mouth can say: no <think> block, no markdown marks, one line. `limit` 0 (the default) cuts
    nothing; a caller that sets one cuts at the last sentence end inside it and must say so (crissa_talk.keep)."""
    t = re.sub(r"<think>[\s\S]*?(</think>|$)", " ", text or "", flags=re.I)
    if re.search(r"</think>", t, re.I):          # a template that opened the block in the prompt: drop to its close
        t = re.split(r"</think>", t, flags=re.I)[-1]
    t = re.sub(r"```[\s\S]*?```", " ", t)
    t = re.sub(r"[*_#`>|]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not limit or len(t) <= limit:
        return t
    cut = t[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:end + 1] if end > limit // 3 else cut).strip()


class AgentError(RuntimeError):
    """str() is the plain headline a person reads; `detail` keeps the raw error for the log (never the key)."""

    def __init__(self, msg: str, detail: str = ""):
        super().__init__(msg)
        self.detail = detail or msg


# PLAIN ERRORS (the User 2026-10-07): the headline says what to fix on the Connect page; the raw error stays
# in `detail`. A 404 from a chat-completions server is almost always a model name it does not serve.
EMPTY_ADDRESS = "The Address is empty - enter your agent's address."
HTTP_PLAIN = {404: "Model not found - check the Model name.",
              401: "The agent refused the key - check the API key.",
              403: "The agent refused the key - check the API key."}
TIMEOUT_PLAIN = "The agent did not answer in time."
# A BAD KEY IS SAID AS ONE, WHATEVER THE STATUS (the User 2026-10-10, .101: xAI answers a wrong key with HTTP 400
# {"code": "invalid-argument", "error": "Incorrect API key provided ..."}, and the page said "The agent refused: HTTP
# 400"). The providers' documented bad-key answers: OpenAI and xAI "Incorrect API key provided" / invalid_api_key,
# Anthropic 401 authentication_error / "invalid x-api-key", xAI's 400 for a key it cannot use (a management key: "cannot
# be accessed ... via a management key"). xAI's own hint, because that is what cost the time on .101.
KEY_PLAIN = "The agent refused the key - check the API key."
XAI_KEY_HINT = " For xAI, use an API key (console.x.ai, API Keys), not a management key."
BAD_KEY_MARKS = ("incorrect api key", "invalid api key", "invalid_api_key", "invalid x-api-key", "authentication_error",
                 "management key", "api key not valid", "invalid authentication", "unauthorized")
REFUSED_PLAIN = "Cannot reach your agent at the Address - check it, and that the agent is running."
# OUT OF CREDITS IS NOT A BAD KEY (round 41, .101 round 40 C1): xAI answered a valid key with HTTP 403
# {"code": "permission-denied", "error": "Your team ... has either used all available credits or reached its monthly
# spending limit ..."} and the card said "check the API key": the User made a new key and tried Claude and Local for a
# billing problem. A refusal whose body names credits, a spending limit, a quota or billing (or HTTP 402) is said as
# that, with the provider's own message under it, and the test's record keeps the reason. Kill CRISSA_CONNECT_BILLING=0.
CREDITS_PLAIN = "Out of credits or over the spending limit - the key is fine; add credits or raise the limit."
XAI_BILLING_HINT = " For xAI: console.x.ai, Billing."
BILLING_MARKS = ("used all available credits", "spending limit", "credit balance", "insufficient_quota",
                 "exceeded your current quota", "insufficient credits", "out of credits", "billing", "payment required")


def billing_on() -> bool:
    return os.environ.get("CRISSA_CONNECT_BILLING", "1").strip().lower() not in ("0", "off", "false", "no")


def provider_message(body: str) -> str:
    """The provider's own words from an error body: JSON "error" (a string, or its "message"), else "message", else
    the body itself; one line."""
    said = ""
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        err = obj.get("error")
        if isinstance(err, dict):
            said = str(err.get("message") or "")
        elif err:
            said = str(err)
        said = said or str(obj.get("message") or "")
    said = said or str(body or "")
    return " ".join(said.split())[:300]
# THE MODEL DECLINED (round 17, the Claude preset, 2026-10-13): Anthropic's safety classifiers can decline a request
# with HTTP 200 and stop_reason "refusal"; its OpenAI-compatible layer documents no table for finish_reason, and the two
# readings in use are "refusal" and OpenAI's own "content_filter". Either one ends the reply as a refusal, said as one
# (never "no text", never a reach error), and the turn is not kept in the short memory (Conversation).
# Kill: CRISSA_CONNECT_CLAUDE_PRESET=0 (as before: the partial text, or "the agent answered with no text").
DECLINED_PLAIN = "The model declined that request - a refusal from the model, not a connection or key problem."
REFUSAL_FINISH = ("refusal", "content_filter")
_FINISH = re.compile(r'"(?:finish_reason|stop_reason)"\s*:\s*"(\w+)"')


def refusal_finish(reason) -> bool:
    """Does this finish_reason (or stop_reason) mean the model declined the request?"""
    return str(reason or "").strip().lower() in REFUSAL_FINISH


def _declined(finish, said: str):
    """Raise the refusal's AgentError when the reply ended as one (see DECLINED_PLAIN); else nothing."""
    if finish and refusal_finish(finish) and claude_preset_on():
        raise AgentError(DECLINED_PLAIN, "finish_reason %s after %d characters" % (finish, len(said or "")))


def plain_error(exc: BaseException, cfg: dict | None = None) -> AgentError:
    """One failed call -> an AgentError whose headline a person can act on, the raw reason kept in detail."""
    url = str((cfg or {}).get("url") or "").strip()
    if isinstance(exc, urllib.error.HTTPError):
        raw = "HTTP %d from %s" % (exc.code, chat_url(cfg or {}) if url else "the agent")
        body = getattr(exc, "crissa_body", None)
        if body is None:
            try:
                body = exc.read().decode("utf-8", errors="replace")[:4000]
            except Exception:  # noqa: BLE001
                body = ""
        low = str(body or "").lower()
        xai = "x.ai" in (_host(url) or "") or "management key" in low
        if billing_on() and exc.code in (400, 402, 403, 429) and (
                exc.code == 402 or any(m in low for m in BILLING_MARKS)):
            said = provider_message(body)
            return AgentError(CREDITS_PLAIN + (XAI_BILLING_HINT if "x.ai" in (_host(url) or "") else "")
                              + (" The provider said: " + said if said else ""), raw + (" - " + body[:300] if body else ""))
        if exc.code in (401, 403) or (exc.code == 400 and any(m in low for m in BAD_KEY_MARKS)):
            return AgentError(KEY_PLAIN + (XAI_KEY_HINT if xai else ""), raw + (" - " + body[:300] if body else ""))
        return AgentError(HTTP_PLAIN.get(exc.code, "The agent refused: HTTP %d" % exc.code), raw)
    reason = getattr(exc, "reason", exc)
    raw = "%s: %s" % (type(exc).__name__, reason)
    if not url:
        return AgentError(EMPTY_ADDRESS, raw)
    if isinstance(reason, (TimeoutError, socket.timeout)) or isinstance(exc, (TimeoutError, socket.timeout)) \
            or "timed out" in str(reason).lower():
        return AgentError(TIMEOUT_PLAIN, raw)
    if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower():
        return AgentError(REFUSED_PLAIN, raw)
    if isinstance(reason, socket.gaierror):
        return AgentError("The Address's host name was not found - check the Address.", raw)
    return AgentError("Cannot reach your agent: %s" % reason, raw)


def reply_timeout() -> float | None:
    """The talk settings' reply.timeout_s; 0 (the default) = None = wait as long as the agent takes."""
    try:
        import crissa_talk
        t = float(crissa_talk.load()["reply"].get("timeout_s") or 0)
    except Exception:  # noqa: BLE001  unreadable settings never add a limit
        t = 0.0
    return t if t > 0 else None


def ask(text: str, cfg: dict, *, history: list | None = None, timeout: float | None = None,
        max_tokens: int | None = None, session_id: str | None = None, on_piece=None, on_event=None,
        system_extra: str | None = None, tools: list | None = None, on_tool=None) -> str:
    """Send one user turn, return the reply text (raw — `speakable()` it before speech).

    Streams when the server streams; a plain JSON reply works too. Raises
    AgentError with a short reason (never the key) on any failure.

    `timeout` None = the talk settings (none by default). `max_tokens` None = the server's own.
    `on_piece(text)` sees each streamed piece as it lands; `on_event(name, data)` sees any streamed frame
    that is not chat text (a named SSE event, or data with no choices): the agent is working.

    TOOLS (2026-10-16, the display tool): `tools` (OpenAI function tools) go with the question and `on_tool(name,
    args) -> str` runs each call the agent makes; the results go back once and the agent's next words are streamed
    the same way (at most MAX_TOOL_ROUNDS asks). An endpoint that refuses `tools` is asked again without them and
    remembered, like the sampling parameters. Without on_tool no tool is offered.
    """
    if cfg.get("preset") != "none" and not str(cfg.get("url") or "").strip():
        raise AgentError(EMPTY_ADDRESS, "no url")
    if not configured(cfg):
        raise AgentError("not connected: set an agent url on the Connect page")
    if timeout is None:
        timeout = reply_timeout()
    sysmsg = system_message(cfg)
    extra = str(system_extra or "").strip()
    if extra:
        sysmsg = (sysmsg + "\n\n" + extra).strip() if sysmsg else extra
    msgs = [] if sysmsg is None else [{"role": "system", "content": sysmsg}]
    msgs += list(history or [])
    msgs.append({"role": "user", "content": str(text)})
    tools = list(tools or []) if on_tool is not None else []
    url = chat_url(cfg)
    said = ""
    for round_ in range(MAX_TOOL_ROUNDS):
        calls: dict = {}
        # the tools go with every ask (an endpoint may refuse a tool result whose tool it was not told of); a call in
        # the last round still runs, and its result is not sent back (no loop)
        got = _ask_once(url, cfg, msgs, max_tokens, tools, session_id, timeout, on_piece, on_event, calls)
        said = (said + " " + got).strip() if said and got else (said or got)
        if not calls:
            return said
        done = [calls[k] for k in sorted(calls)]
        msgs = msgs + [{"role": "assistant", "content": got or None, "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"]}} for c in done]}]
        for c in done:
            try:
                args = json.loads(c["args"] or "{}")
            except ValueError:
                args = {}
            try:
                result = str(on_tool(c["name"], args if isinstance(args, dict) else {}))
            except Exception as exc:  # noqa: BLE001  a tool failure is told to the agent, never raised into the turn
                result = "NOT DONE: %s" % type(exc).__name__
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": result[:4000]})
            _event(on_event, "tool", "%s %s" % (c["name"], result[:120]))
        if round_ == MAX_TOOL_ROUNDS - 1:
            break
    return said


def _ask_once(url, cfg, msgs, max_tokens, tools, session_id, timeout, on_piece, on_event, calls) -> str:
    body = request_body(cfg, msgs, max_tokens, tools)
    try:
        return _send(url, body, cfg, session_id, timeout, on_piece, on_event, calls)
    except _Refused400 as exc:
        drop = exc.named & set(body)
        if not drop:
            raise plain_error(exc.http, cfg) from exc.http
        _REFUSED.setdefault((url, str(cfg.get("model") or "")), set()).update(drop)
        if "max_completion_tokens" in exc.moves and body.get("max_tokens"):
            _REFUSED[(url, str(cfg.get("model") or ""))].add("max_tokens->max_completion_tokens")
        retry = request_body(cfg, msgs, max_tokens, tools)
        print("[agent] %s refused %s: asked once more without it" % (_host(url) or "the agent", ", ".join(sorted(drop))),
              flush=True)
        try:
            return _send(url, retry, cfg, session_id, timeout, on_piece, on_event, calls)
        except _Refused400 as exc2:
            raise plain_error(exc2.http, cfg) from exc2.http


def reasoning_model(model: str) -> bool:
    """OpenAI's o-series and gpt-5 families (a provider prefix like "openai/" ignored)."""
    name = str(model or "").strip().rsplit("/", 1)[-1]
    return bool(REASONING_MODEL.match(name))


def request_body(cfg: dict, msgs: list, max_tokens: int | None = None, tools: list | None = None) -> dict:
    """The chat body for this endpoint and model: no temperature for Claude and the reasoning families, the cap in the
    parameter the model takes, and nothing this endpoint already refused (see REASONING MODELS above)."""
    model = str(cfg.get("model") or "")
    url = chat_url(cfg)
    refused = _REFUSED.get((url, model), set())
    body: dict = {"messages": msgs, "stream": True}
    no_temp = (model.lower().startswith("claude-") or _host(str(cfg.get("url") or "")) in NO_TEMPERATURE_HOSTS
               or reasoning_model(model))
    if not no_temp and "temperature" not in refused:
        body["temperature"] = 0.4
    if max_tokens:
        if reasoning_model(model) or "max_tokens->max_completion_tokens" in refused:
            if "max_completion_tokens" not in refused:
                body["max_completion_tokens"] = int(max_tokens)
        elif "max_tokens" not in refused:
            body["max_tokens"] = int(max_tokens)
    if model:
        body["model"] = model
    if tools and "tools" not in refused:
        body["tools"] = list(tools)
    return body


class _Refused400(Exception):
    """An HTTP 400 that names sampling parameters the body carried: `named` = which, `moves` = what it asks for instead."""

    def __init__(self, http: urllib.error.HTTPError, named: set, moves: set):
        super().__init__("refused %s" % sorted(named))
        self.http, self.named, self.moves = http, named, moves


def _named_params(text: str) -> tuple[set, set]:
    """(the sampling parameters an error body names as refused, the ones it names as the way instead)."""
    t = str(text or "")
    named = {p for p in SAMPLING_PARAMS if re.search(r"\b%s\b" % p, t)}
    if re.search(r"\btools?\b|\bfunction(s| calling)?\b|tool_choice", t, re.I):
        named.add("tools")              # an endpoint without tool calls: asked again without them (2026-10-16)
    moves = set()
    if "max_tokens" in named and "max_completion_tokens" in named:
        named.discard("max_completion_tokens")      # "use max_completion_tokens instead": the way, not the refusal
        moves.add("max_completion_tokens")
    return named, moves


def _tool_delta(calls: dict | None, parts) -> bool:
    """Fold one frame's tool_calls (streamed pieces, or a whole message's) into `calls` {index: {id, name, args}}."""
    if calls is None or not isinstance(parts, list) or not parts:
        return False
    for n, tc in enumerate(parts):
        if not isinstance(tc, dict):
            continue
        i = tc.get("index", n)
        c = calls.setdefault(i, {"id": "", "name": "", "args": ""})
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
    return True


def _tool_data(calls: dict | None, line: str) -> bool:
    if calls is None or '"tool_calls"' not in line:
        return False
    try:
        obj = json.loads(line[5:].strip())
    except ValueError:
        return False
    ch = (obj.get("choices") or [{}])[0] if isinstance(obj, dict) else {}
    return _tool_delta(calls, ((ch or {}).get("delta") or {}).get("tool_calls"))


def _send(url: str, body: dict, cfg: dict, session_id, timeout, on_piece, on_event, calls: dict | None = None) -> str:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=_headers(cfg, session_id), method="POST")
    acc = ""
    try:
        with crissa_egress.urlopen(req, timeout, "the agent") as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "event-stream" not in ctype:
                raw = r.read().decode("utf-8", errors="replace")
                try:
                    obj = json.loads(raw)
                except ValueError as exc:
                    raise AgentError("the agent answered something that is not chat JSON") from exc
                text = message_text(obj)
                ch0 = ((obj.get("choices") or [{}])[0] or {}) if isinstance(obj, dict) else {}
                if _tool_delta(calls, (ch0.get("message") or {}).get("tool_calls")):
                    return text
                _declined(ch0.get("finish_reason") or (obj.get("stop_reason") if isinstance(obj, dict) else None), text)
                if not text.strip() and _reasoning_of(ch0.get("message") or {}):
                    _event(on_event, "reasoning", "")       # all thinking, no answer yet
                return text
            buf = b""
            event = ""
            finish = None
            while True:
                chunk = r.read1(4096) if hasattr(r, "read1") else r.read(256)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    s = line.decode("utf-8", errors="replace")
                    st = s.strip()
                    if not st:
                        event = ""
                        continue
                    if st.startswith("event:"):
                        event = st[6:].strip()
                        continue
                    if event and event != "message":
                        _event(on_event, event, st)
                        continue
                    fm = _FINISH.search(st) if st.startswith("data:") and "_reason" in st else None
                    if fm:
                        finish = fm.group(1)
                    if st.startswith("data:") and _tool_data(calls, st):
                        continue
                    piece = sse_piece(s)
                    if piece is None:
                        if st.startswith("data:") and _working_data(st):
                            _event(on_event, "working", st)
                        elif st.startswith("data:") and _reasoning_data(st):
                            _event(on_event, "reasoning", "")
                        continue
                    if piece == "":
                        if not calls:
                            _declined(finish, acc)
                        return acc
                    acc += piece
                    if on_piece is not None:
                        try:
                            on_piece(piece)
                        except Exception:  # noqa: BLE001  a listener never breaks the answer
                            pass
            if not calls:
                _declined(finish, acc)
    except crissa_egress.Refused as exc:
        raise AgentError(str(exc)) from exc
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            try:
                text = exc.read().decode("utf-8", errors="replace")[:4000]
            except Exception:  # noqa: BLE001
                text = ""
            exc.crissa_body = text                   # read once: the bad-key check needs it too
            named, moves = _named_params(text)
            if named & set(body):
                raise _Refused400(exc, named, moves) from exc
        raise plain_error(exc, cfg) from exc
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        raise plain_error(exc, cfg) from exc
    return acc


def _reasoning_of(part: dict) -> bool:
    """A delta or message that carries thinking (reasoning_content, reasoning) and no answer text."""
    if not isinstance(part, dict):
        return False
    return bool(part.get("reasoning_content") or part.get("reasoning")) and not part.get("content")


def _reasoning_data(line: str) -> bool:
    try:
        obj = json.loads(line[5:].strip())
    except ValueError:
        return False
    ch = ((obj.get("choices") or [{}])[0] or {}) if isinstance(obj, dict) else {}
    return _reasoning_of(ch.get("delta") or {}) or _reasoning_of(ch.get("message") or {})


def _working_data(line: str) -> bool:
    """A data frame that carries no chat choices (a progress note, not her words)."""
    try:
        obj = json.loads(line[5:].strip())
    except ValueError:
        return False
    return isinstance(obj, dict) and "choices" not in obj


def _event(cb, name: str, data: str) -> None:
    if cb is None:
        return
    try:
        cb(name, data[5:].strip() if data.startswith("data:") else data)
    except Exception:  # noqa: BLE001
        pass


def thinking_only(raw: str, saw_reasoning: bool = False) -> bool:
    """A reply that is all thinking: an open or closed <think> block and nothing after it, or reasoning frames and no
    answer text. Not "no text": the model is still thinking."""
    t = str(raw or "")
    if speakable(t, 0):
        return False
    return saw_reasoning or bool(re.search(r"<think>", t, re.I))


# THE MODEL LIST (round 15, 2026-10-12, .101's fresh install: finding the exact model name took a while; the User:
# "was it loading that whole API lookup? It should be."). Once a key is entered, or the Kind changes, Connect asks the
# provider's own list - the OpenAI-compatible GET <base>/models with the key (xAI, Anthropic and local engines all serve
# it) - and offers the chat models in a pick list, the best for a voice avatar first (the Kind's own model, then the
# non-reasoning ones, then the fast ones; reasoning models last: a spoken reply waits for their thinking). The typed
# field stays for anything not listed. The key goes in the request header only: never logged, never in an answer.
NOT_CHAT = re.compile(r"(embed|image|imagine|video|tts|speech|whisper|transcri|audio|moderation|rerank|dall-?e)", re.I)
FAST_WORDS = ("fast", "mini", "haiku", "flash", "turbo", "instant", "lite")
MODELS_TIMEOUT_S = 8.0
MODELS_MAX = 60


def models_url(cfg: dict) -> str:
    """The model list's address: the chat-completions address with /models in place of /chat/completions."""
    url = chat_url(cfg)
    return url[: -len("/chat/completions")] + "/models"


def _models_headers(cfg: dict, style: str | None = None) -> dict:
    """The model list's headers. style None = round 15's one ask (the Bearer key, plus Anthropic's own key header and
    version on its host); "x-api-key" = Anthropic's native list (its key header and version, no Authorization);
    "bearer" = the OpenAI-compatible header alone (round 17, the Claude preset)."""
    h = {"Accept": "application/json"}
    key = str(cfg.get("key") or "")
    if style == "x-api-key":
        if key:
            h["x-api-key"] = key
        h["anthropic-version"] = ANTHROPIC_VERSION
        return h
    if style == "bearer":
        if key:
            h["Authorization"] = "Bearer " + key
        return h
    anthropic = _host(str(cfg.get("url") or "")).endswith("anthropic.com")
    if key:
        h["Authorization"] = "Bearer " + key
        if anthropic:
            h["x-api-key"] = key                  # Anthropic's own list reads its own header
    if anthropic:
        h["anthropic-version"] = ANTHROPIC_VERSION
    return h


def model_ids(obj) -> list[str]:
    """The ids in a model list: OpenAI's {"data": [{"id"}]}, Anthropic's {"data": [{"id", "display_name"}]}, a bare
    list, or {"models": [{"name"}]}."""
    data = obj
    if isinstance(obj, dict):
        data = obj.get("data") if obj.get("data") is not None else obj.get("models")
    out: list[str] = []
    for d in data if isinstance(data, list) else []:
        mid = d if isinstance(d, str) else ((d.get("id") or d.get("name") or d.get("model")) if isinstance(d, dict) else "")
        mid = str(mid or "").strip()
        if mid and mid not in out and len(mid) <= 200:
            out.append(mid)
    return out


def rank_models(ids: list[str], preset: str | None = None) -> list[str]:
    """The chat models, best for a voice avatar first: the Kind's own model, then non-reasoning, then fast ones,
    then the rest, reasoning ones last; the provider's order inside each group."""
    pm = (KINDS.get(str(preset or "")) or {}).get("model")

    def score(m: str) -> int:
        ml = m.lower()
        if pm and m == pm:
            return 0
        if "non-reasoning" in ml:
            return 1
        if any(w in ml for w in FAST_WORDS):
            return 2
        if "reasoning" in ml or "thinking" in ml or REASONING_MODEL.match(ml.split("/")[-1]):
            return 4
        return 3
    chat = [m for m in ids if not NOT_CHAT.search(m)]
    return sorted(chat, key=lambda m: (score(m), chat.index(m)))


def model_labels(obj) -> dict:
    """{id: display_name} where a model list names its models (Anthropic's {"data": [{"id", "display_name"}]})."""
    data = obj.get("data") if isinstance(obj, dict) else None
    out = {}
    for d in data if isinstance(data, list) else []:
        if isinstance(d, dict) and d.get("id") and isinstance(d.get("display_name"), str) and d["display_name"].strip():
            out[str(d["id"]).strip()] = d["display_name"].strip()[:120]
    return out


def _get_models(url: str, headers: dict, timeout: float):
    """One GET of a model list: (the parsed JSON or None, the HTTP status when it refused, True when it never answered).
    Never raises; the key is only in the request's header."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with crissa_egress.urlopen(req, timeout, "the agent's model list") as r:
            got = json.loads(r.read(2_000_000).decode("utf-8", errors="replace") or "null")
            return (got if got is not None else {}), 0, False          # a "null" list lists nothing, as before
    except urllib.error.HTTPError as exc:
        return None, int(getattr(exc, "code", 0) or 0), False
    except Exception:  # noqa: BLE001 - unreachable, refused by the egress switch, not JSON: the typed field stays
        return None, 0, True


def _anthropic_host(url: str) -> bool:
    try:
        h = (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return False
    return h == "anthropic.com" or h.endswith(".anthropic.com")


def _claude_list(cfg: dict) -> bool:
    """Is this the Claude preset's model list (Anthropic's host, or the Claude kind on any address)?"""
    return claude_preset_on() and (_anthropic_host(str(cfg.get("url") or ""))
                                   or str(cfg.get("preset") or "") == "claude")


def _claude_labels(ids: list[str], given: dict) -> dict:
    """Each listed model's name for the pick list: ours for the models we know (the voice mark on Haiku), else the
    provider's display name."""
    out = {}
    for m in ids:
        lab = CLAUDE_LABELS.get(m) or given.get(m) or ""
        if m == CLAUDE_VOICE_MODEL and "voice avatar" not in lab:
            lab = (lab or m) + VOICE_MARK
        if lab:
            out[m] = lab
    return out


def _claude_models(cfg: dict, timeout: float) -> dict:
    """The Claude preset's pick list (round 17): Anthropic's native list (x-api-key + anthropic-version), then the same
    list with the Bearer header if that header was refused, else CLAUDE_MODELS - Haiku first either way."""
    url = models_url(cfg)
    host = _host(url) or "the address"
    key = str(cfg.get("key") or "")

    def fixed(why: str, code: int = 0) -> dict:
        out = {"ok": False, "fallback": True, "models": list(CLAUDE_MODELS), "count": len(CLAUDE_MODELS),
               "labels": dict(CLAUDE_LABELS), "voice": CLAUDE_VOICE_MODEL, "host": host,
               "error": why + " These are Claude's current models; Claude Haiku 5.5 is the one for a voice avatar. "
                              "Pick one, or type the model's name."}
        if code:
            out["code"] = code
        return out
    if not key:
        return fixed("Enter your API key to load Anthropic's own model list.")
    code = 0
    for style in ("x-api-key", "bearer"):
        obj, code, unreachable = _get_models(url, _models_headers(cfg, style), timeout)
        if obj is not None:
            ranked = rank_models(model_ids(obj), "claude")
            if not ranked:
                return fixed("%s listed no chat models." % host)
            return {"ok": True, "models": ranked[:MODELS_MAX], "count": len(ranked), "host": host,
                    "labels": _claude_labels(ranked[:MODELS_MAX], model_labels(obj)), "voice": CLAUDE_VOICE_MODEL,
                    "auth": style}
        if unreachable:
            return fixed("Could not get the model list from %s." % host)
        if code not in (400, 401, 403):
            break                       # 404, 429, 5xx: the other header changes nothing
    if code in (401, 403):
        return fixed("The key was refused when listing the models - check the key.", code)
    if code == 404:
        return fixed("This address does not list its models.", code)
    return fixed("The model list did not load (HTTP %d)." % code, code)


def list_models(cfg: dict, timeout: float = MODELS_TIMEOUT_S) -> dict:
    """The provider's chat models for the Connect form: {"ok", "models", "count", "host"} or {"ok": False, "error"}
    in plain words (and "models": []), so the page keeps the typed field. Never raises; the key is never in the
    answer. The Claude preset (round 17) answers with its own fixed list when Anthropic's does not load, and names
    each model ("labels") with Haiku marked as the one for a voice avatar ("voice")."""
    if _claude_list(cfg):
        return _claude_models(cfg, timeout)
    url = models_url(cfg)
    host = _host(url) or "the address"
    obj, code, unreachable = _get_models(url, _models_headers(cfg), timeout)
    if obj is None and not unreachable:
        if code in (401, 403):
            why = "The key was refused when listing the models. Check the key, or type the model's name."
        elif code == 404:
            why = "This address does not list its models. Type the model's name."
        else:
            why = "The model list did not load (HTTP %d). Type the model's name." % code
        return {"ok": False, "error": why, "code": code, "models": [], "host": host}
    if obj is None:
        return {"ok": False, "error": "Could not get the model list from %s. Type the model's name." % host,
                "models": [], "host": host}
    ranked = rank_models(model_ids(obj), cfg.get("preset"))
    if not ranked:
        return {"ok": False, "error": "%s listed no chat models. Type the model's name." % host, "models": [],
                "host": host}
    return {"ok": True, "models": ranked[:MODELS_MAX], "count": len(ranked), "host": host}


def test(cfg: dict, timeout: float = TEST_TIMEOUT_S) -> dict:
    """One short round trip for the Connect page's Test button (and Init's). Passes on a real reply only. A thinking
    model gets TEST_MAX_TOKENS; a reply that is all thinking is asked once more with no cap of ours, inside the same
    time limit, and if it is still all thinking the answer says so ("thinking": true) - never "no text"."""
    t0 = time.monotonic()
    out: dict = {}
    for budget in (TEST_MAX_TOKENS, None):
        left = max(1.0, float(timeout) - (time.monotonic() - t0))
        seen = {"reasoning": False}

        def ev(name, _data, seen=seen):
            if name == "reasoning":
                seen["reasoning"] = True
        try:
            reply = ask("Say hello in five words or fewer.", cfg, timeout=left, max_tokens=budget, on_event=ev,
                        session_id="crissa-connect-test-" + uuid.uuid4().hex[:8])
        except AgentError as exc:
            out = {"ok": False, "error": str(exc), "detail": exc.detail}
            if seen["reasoning"] and exc.detail and "timed out" in exc.detail.lower():
                out.update(error=STILL_THINKING, thinking=True)
            break
        said = speakable(reply, 200)
        if said:
            out = {"ok": True, "reply": said, "error": None}
            break
        if thinking_only(reply, seen["reasoning"]):
            out = {"ok": False, "thinking": True, "error": STILL_THINKING}
            if time.monotonic() - t0 < float(timeout) - 1.0:
                continue                               # once more, with no cap of ours
            break
        out = {"ok": False, "error": "the agent answered with no text"}
        break
    out["ms"] = round((time.monotonic() - t0) * 1000)
    record_test(cfg, out)
    return out


# THE LAST TEST (2026-10-10, the .101 demo): a pass on the Connect page must count for Init's Continue. Every test
# (the page's and Init's) is kept: the outcome on disk (<crissa home>/agent-test.json - no key, a fingerprint of the
# address, model and key), and the whole config of the last PASS in this process's memory, so Init's TEST can test
# what the page just passed even before it was saved.
_LAST_PASS: dict = {}


def fingerprint(cfg: dict) -> str:
    import hashlib
    raw = "\n".join(str(cfg.get(k) or "") for k in ("url", "model", "key"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def test_record_path(home: Path | None = None) -> Path:
    return (home or HOME) / "agent-test.json"


def _records_here() -> bool:
    """A pack keeps the record; the house's own state directory is never written by a test of the Connect page."""
    try:
        import crissa_home
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001
        return True


def record_test(cfg: dict, res: dict, home: Path | None = None) -> dict:
    p = test_record_path(home)
    if home is None and not _records_here():
        return {}
    try:
        prev = json.loads(p.read_text(encoding="utf-8"))
        n = int((prev or {}).get("n") or 0) + 1 if isinstance(prev, dict) else 1
    except (OSError, ValueError, TypeError):
        n = 1
    rec = {"n": n, "ok": bool(res.get("ok")), "at": time.time(), "fp": fingerprint(cfg),
           "host": _host(str(cfg.get("url") or "")), "model": str(cfg.get("model") or ""),
           "ms": res.get("ms"), "reply": str(res.get("reply") or "")[:120]}   # round 33: Setup's card shows them
    if not rec["ok"] and billing_on():        # round 41 (.101 C1): a failed test keeps its reason (never the key)
        rec["error"] = str(res.get("error") or "")[:400]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
        tmp.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass
    if rec["ok"]:
        _LAST_PASS.clear()
        _LAST_PASS.update(cfg={k: cfg[k] for k in ("url", "key", "model", "session_header", "no_prompt", "system_prompt",
                                                   "preset") if cfg.get(k) not in (None, "")}, fp=rec["fp"], n=n)
    return rec


def last_test(home: Path | None = None) -> dict:
    """The last test's record ({} when none): {n, ok, at, fp, host, model}."""
    try:
        d = json.loads(test_record_path(home).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def last_pass_cfg() -> dict:
    """The whole config of the last test that passed in this process (the key included, memory only), or {}."""
    return dict(_LAST_PASS.get("cfg") or {})


class Conversation:
    """A short rolling memory so a follow-up question has its context.

    The agent may keep its own memory too (keyed by `session_header`); this is what makes a stateless OpenAI-compatible server usable.
    """

    def __init__(self, turns: int = 6, idle_s: float = 300.0):
        self.turns, self.idle_s = turns, idle_s
        self.history: list[dict] = []
        self.session = "crissa-" + uuid.uuid4().hex[:12]
        self.at = 0.0

    def ask(self, text: str, cfg: dict, *, remember: bool = True, **kw) -> str:
        now = time.monotonic()
        if self.at and now - self.at > self.idle_s:
            self.reset()
        hist = self.history[-2 * self.turns:] if remember else []
        reply = ask(text, cfg, history=hist, session_id=self.session, **kw)
        if remember:
            self.history += [{"role": "user", "content": str(text)},
                             {"role": "assistant", "content": speakable(reply)}]
            del self.history[:-2 * self.turns]  # bound store, not only the slice on send
        self.at = time.monotonic()
        return reply

    def reset(self) -> None:
        self.history = []
        self.session = "crissa-" + uuid.uuid4().hex[:12]
        self.at = 0.0
