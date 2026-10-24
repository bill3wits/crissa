"""The Wall's verbs for an agent: one definition, two front doors (2026-10-01).

Any agent drives the avatar through the player's own public HTTP routes. These verbs are the shared
definition: `scripts/crissa-mcp.py` serves them over MCP (Claude, Grok, OpenAI agents, anything that
speaks MCP) and the krew plugin registers the same ones in its agent framework. Each verb is a name, a
description the model reads, a JSON schema, and a function that makes one HTTP call.

The player is CRISSA_PLAYER (default http://127.0.0.1:8100). Standard library only. Requests carry no
Origin header, so the player treats them as this machine's own (loopback) callers.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request

try:                                            # kill-search (the User 2026-10-05): the player must be on the LAN
    import crissa_egress as _egress             # lib/, or the copy krew vendors beside this file (its own dir is on
except ImportError:                             # the path once krew's package loads it)
    try:
        from . import crissa_egress as _egress  # type: ignore[no-redef]
    except ImportError:
        _egress = None

def _player_default() -> str:
    """This install's player from the port table (2026-10-10: beside another install the old fixed default drove that
    install's Wall). A copy vendored without the table (krew's) repeats its one rule for the player."""
    try:
        import crissa_ports
        return crissa_ports.url("player")
    except ImportError:
        try:
            off = int(str(os.environ.get("CRISSA_PORT_OFFSET") or "0").strip())
        except ValueError:
            off = 0
        n = os.environ.get("CRISSA_PORT_PLAYER") or os.environ.get("HOUSE_CONDUCTOR_PORT") or str(8100 + off)
        return "http://127.0.0.1:%s" % n.strip()


PLAYER = (os.environ.get("CRISSA_PLAYER") or _player_default()).rstrip("/")


class WallError(RuntimeError):
    pass


def _num(args: dict, k: str) -> float:
    """A caller's number (ttl, max_wait_s, ...): a finite float, or a WallError that names the key."""
    try:
        v = float(args[k])
    except (TypeError, ValueError):
        raise WallError("%s: a number of seconds, not %r" % (k, args[k])) from None
    if not math.isfinite(v):
        raise WallError("%s: a finite number" % k)
    return v


def _call(method: str, path: str, body: dict | None = None, timeout: float = 15.0, base: str | None = None,
          soft: tuple = ()) -> dict:
    url = (base or PLAYER).rstrip("/") + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        opened = (_egress.urlopen(req, timeout, "the Wall's player") if _egress is not None
                  else urllib.request.urlopen(req, timeout=timeout))
        with opened as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read() or b"{}")
        except ValueError:
            out = {}
        if not isinstance(out, dict):              # a `null` or list error body
            out = {}
        if e.code in soft and isinstance(out, dict) and out.get("reply"):
            return dict(out, ok=False, spoke=False)      # answered, but not heard: the reply is still hers
        raise WallError(out.get("error") or "the player said %d on %s" % (e.code, path)) from None
    except PermissionError as e:                 # kill-search refused an address off the LAN
        raise WallError(str(e)) from None
    except (OSError, ValueError) as e:
        raise WallError("the player is not answering at %s (%s)" % (base or PLAYER, e)) from None


def state(args: dict, base: str | None = None) -> dict:
    s = _call("GET", "/v1/state", base=base)
    keep = ("now", "clip", "hub", "on_air", "comms", "reason", "speaker", "mouth_fps")
    return {k: s[k] for k in keep if k in s}


def keys(args: dict, base: str | None = None) -> dict:
    return _call("GET", "/v1/kit", base=base)


def cue(args: dict, base: str | None = None) -> dict:
    key = str(args.get("key") or "").strip()
    if not key:
        raise WallError("key required (see wall_keys)")
    body = {"key": key, "by": "agent"}
    if args.get("ttl") is not None:
        body["ttl"] = _num(args, "ttl")
    return _call("POST", "/v1/cue", body, base=base)


def home(args: dict, base: str | None = None) -> dict:
    return _call("POST", "/v1/home", {"by": "agent"}, base=base)


def comms(args: dict, base: str | None = None) -> dict:
    if not isinstance(args.get("on"), bool):
        raise WallError("on: true|false")
    return _call("POST", "/v1/comms", {"on": args["on"], "why": "agent"}, base=base)


def speak(args: dict, base: str | None = None) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        raise WallError("text required")
    body = {"text": text, "by": "agent"}
    if args.get("queue"):                        # wait for what she is saying instead of stopping it (2026-10-06)
        body["queue"] = True
    return _call("POST", "/v1/speak", body, base=base)


def announce(args: dict, base: str | None = None) -> dict:
    """A line for the room (2026-10-06): queued behind what she is saying, held while a conversation is open
    or someone is talking, dropped in quiet hours. The player keeps the rules; this only hands it over."""
    text = str(args.get("text") or "").strip()
    if not text:
        raise WallError("text required")
    body = {"text": text, "by": str(args.get("by") or "agent")}
    for k in ("kind", "dedup_key", "voice"):
        if str(args.get(k) or "").strip():
            body[k] = str(args[k]).strip()
    for k in ("max_wait_s", "speech_pause_s"):
        if args.get(k) is not None:
            body[k] = _num(args, k)
    return _call("POST", "/v1/announce", body, base=base)


def ask(args: dict, base: str | None = None) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        raise WallError("text required")
    # A 502 that carries a reply means the agent answered and her voice failed (the player checks since
    # 2026-10-04): the caller gets the reply with spoke false, never an error that hides what she said.
    return _call("POST", "/v1/ask", {"text": text, "wait": True, "by": "agent"}, timeout=120.0, base=base,
                 soft=(502,))


def stop(args: dict, base: str | None = None) -> dict:
    # /v1/stop also drops queued talk and the rest of a streaming reply (2026-10-06); an older player has
    # only /v1/say {"stop": true}
    try:
        return _call("POST", "/v1/stop", {"by": "agent"}, base=base)
    except WallError as e:
        if "not answering" in str(e):
            raise
        return _call("POST", "/v1/say", {"stop": True, "by": "agent"}, base=base)


_OBJ = {"type": "object", "properties": {}, "additionalProperties": False}
VERBS = [
    ("wall_state", "What the avatar is doing now: the clip on screen, its hub, whether she is speaking "
     "(on_air) and whether a conversation is open (comms).", _OBJ, state),
    ("wall_keys", "The named clips (keys) this avatar can play, for wall_cue.", _OBJ, keys),
    ("wall_cue", "Play a named clip (a gesture or pose change) at the next natural seam.",
     {"type": "object", "properties": {"key": {"type": "string", "description": "a key from wall_keys"},
                                       "ttl": {"type": "number", "description": "seconds the cue may wait"}},
      "required": ["key"], "additionalProperties": False}, cue),
    ("wall_home", "Send the avatar back to her resting loop.", _OBJ, home),
    ("wall_comms", "Open or close a conversation: on = she faces the person and listens between lines.",
     {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"],
      "additionalProperties": False}, comms),
    ("wall_speak", "Say these exact words in her voice, with her mouth moving. One to three short sentences, "
     "plain text. queue: wait for what she is saying instead of stopping it.",
     {"type": "object", "properties": {"text": {"type": "string"}, "queue": {"type": "boolean"}},
      "required": ["text"], "additionalProperties": False}, speak),
    ("wall_ask", "Ask the agent connected on her Connect page and have her speak its answer; returns the "
     "reply.", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                "additionalProperties": False}, ask),
    ("wall_stop", "Stop her speaking now.", _OBJ, stop),
    ("wall_announce", "Announce a line to the room: it waits behind what she is saying, holds while a "
     "conversation is open or someone is talking, and is dropped in quiet hours. kind: e.g. done, blocked; "
     "dedup_key: the same key again soon is skipped.",
     {"type": "object", "properties": {"text": {"type": "string"}, "kind": {"type": "string"},
                                       "dedup_key": {"type": "string"},
                                       "voice": {"type": "string", "description": "which voice speaks the line"},
                                       "max_wait_s": {"type": "number", "description": "drop it if it waits longer"},
                                       "speech_pause_s": {"type": "number",
                                                         "description": "hold after prior speech before this line"}},
      "required": ["text"], "additionalProperties": False}, announce),
]
BY_NAME = {name: (desc, schema, fn) for name, desc, schema, fn in VERBS}


def call(name: str, args: dict | None = None, base: str | None = None) -> dict:
    """Run one verb; never raises: {"ok": False, "error": ...} on any failure."""
    if name not in BY_NAME:
        return {"ok": False, "error": "no such verb: %s" % name}
    try:
        out = BY_NAME[name][2](dict(args or {}), base=base)
        return out if isinstance(out, dict) and "ok" in out else {"ok": True, "result": out}
    except WallError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001  the MCP loop has no try of its own: a bad call must not end it
        return {"ok": False, "error": "%s failed: %s: %s" % (name, type(e).__name__, e)}
