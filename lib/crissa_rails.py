"""The services a Crissa install may sit beside, reached only through the player (2026-10-06).

An agent on this machine never calls a presence feed, a camera, a registration panel or the voice module itself:
it asks the player, and the player forwards. Crissa ships the presence service (scripts/crissa-presence.py) and
does not ship a camera or a separate registration panel. An install names each one in its settings, and one it
has not named answers plainly that it is not part of this Crissa:

    CRISSA_PRESENCE_URL   who is in the room           GET /v1/rail/presence/<path>
    CRISSA_CAMERA_URL     the room camera              GET /v1/rail/camera/<path>
    CRISSA_ENROLL_URL     face and voice registration  GET /v1/rail/enroll/<path>

One rail is Crissa's own: the aligner (better mouths), GET /v1/rail/align/<path> (its /health for a watcher). It
needs no setting: its address is the port table's (lib/crissa_ports, "align"); CRISSA_ALIGN_URL names another.
It is read only. When better mouths are off it is not running, and the read says it is not answering.

Reads (GET) go through on any path under the service; a live stream is not forwarded (it never finishes, so it
answers 504 after the read time and says so). Writes (POST) go through only on the paths in POSTS, and the player
lets them in only from this machine (the same Origin rule as /v1/speak). `ear()` reads the voice module's health
(speech to text and the microphone's turns) for GET /v1/ear; POST /v1/ear/open is forwarded to the turns module.
Nothing is shortened: a body comes back whole, with the service's own status and content type.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

RAILS = {"presence": "CRISSA_PRESENCE_URL", "camera": "CRISSA_CAMERA_URL", "enroll": "CRISSA_ENROLL_URL",
         "align": "CRISSA_ALIGN_URL"}
WHAT = {"presence": "presence feed", "camera": "camera", "enroll": "registration panel", "align": "aligner"}
POSTS = {"presence": ("/v1/vl_delta", "/v1/inspector"), "camera": ("/v1/pause",), "enroll": ("/start",), "align": ()}
OWN = ("align",)    # Crissa's own services: the port table's address when no setting names one
READ_S = 10.0       # a read that has not finished by then is a stream or a stuck service; said so, never cut
WRITE_S = 60.0      # a look (presence /v1/inspector) takes tens of seconds


def _err(code: int, error: str, why: str) -> tuple[int, str, bytes]:
    return code, "application/json", json.dumps({"ok": False, "error": error, "why": why}).encode()


def base(name: str, env=None) -> str:
    env = os.environ if env is None else env
    got = str(env.get(RAILS.get(name, ""), "") or "").strip().rstrip("/")
    if not got and name in OWN:
        import crissa_ports
        got = crissa_ports.url(name, env)
    return got


def split(head: str) -> tuple[str, str]:
    """'/v1/rail/presence/v1/presence' -> ('presence', '/v1/presence'). ('', '') when it is not that shape."""
    rest = head[len("/v1/rail/"):] if head.startswith("/v1/rail/") else ""
    name, _, path = rest.partition("/")
    return (name, "/" + path) if name else ("", "")


def forward_url(root: str, path: str, *, label: str, method: str = "GET", body: dict | None = None,
                query: str = "", timeout: float | None = None) -> tuple[int, str, bytes]:
    """(status, content type, body) from root+path. Down or slow says which, in the body's `why`."""
    if not path.startswith("/") or ".." in path.split("/"):
        return _err(400, "bad_path", "a path under the service, starting with /")
    url = root + path + ("?" + query if query else "")
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout or (WRITE_S if method == "POST" else READ_S)) as r:
            return r.status, r.headers.get("Content-Type") or "application/octet-stream", r.read()
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp is not None else b""
        return e.code, (e.headers.get("Content-Type") if e.headers else None) or "application/json", raw
    except TimeoutError:
        return _err(504, "slow", "the %s at %s did not finish answering in %g s (a live stream is not forwarded)"
                    % (label, root, timeout or (WRITE_S if method == "POST" else READ_S)))
    except (OSError, ValueError) as e:
        return _err(502, "unreachable", "the %s is not answering at %s (%s)" % (label, root, type(e).__name__))


def forward(name: str, path: str, *, method: str = "GET", body: dict | None = None, query: str = "",
            env=None) -> tuple[int, str, bytes]:
    if name not in RAILS:
        return _err(404, "unknown", "no service called %r here; the player forwards to: %s"
                    % (name, ", ".join(sorted(RAILS))))
    root = base(name, env)
    if not root:
        return _err(503, "not_configured", "this Crissa has no %s (set %s to one to reach it through the player)"
                    % (WHAT[name], RAILS[name]))
    if method == "POST" and path not in POSTS[name]:
        return _err(405, "read_only", "the player only reads the %s; it forwards writes to %s"
                    % (WHAT[name], ", ".join(POSTS[name]) or "nothing"))
    return forward_url(root, path, label=WHAT[name], method=method, body=body, query=query)


def _health(root: str, label: str, timeout: float) -> dict:
    code, _, raw = forward_url(root, "/health", label=label, timeout=timeout)
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        data = {}
    if code == 200:
        return {"ok": True, "health": data if isinstance(data, dict) else {"answer": data}}
    why = data.get("why") if isinstance(data, dict) and data.get("why") else "the %s at %s said %s" % (label, root, code)
    return {"ok": False, "health": None, "why": why}


def ear(stt_url: str, asr_url: str = "", part: str = "", timeout: float = 2.0) -> dict:
    """The voice module's health for GET /v1/ear: `stt` (the microphone's turns) and `asr` (speech to text).
    part='stt' or 'asr' reads just that one. ok is True only when every part read answered."""
    parts = {"stt": (stt_url, "voice module's turns"), "asr": (asr_url, "speech to text")}
    want = [part] if part in parts else [p for p in ("stt", "asr") if parts[p][0]]
    out: dict = {}
    whys = []
    for p in want:
        root, label = parts[p]
        h = _health(root.rstrip("/"), label, timeout) if root else {"ok": False, "health": None,
                                                                    "why": "no %s address" % label}
        out[p] = h["health"]
        if not h["ok"]:
            whys.append(h["why"])
    out["ok"] = not whys
    if whys:
        out["why"] = "; ".join(whys)
    return out
