"""The agent the bridge talks to: the pack's Connect endpoint (lib/crissa_agent), 2026-10-09.

The house's bridge posted every turn to one agent framework's gateway (a profile path, a profile key read
from the framework's own folder, a persona file from the same folder, a framework-specific session header,
and framework-named tool events in the stream). A pack has none of that: the person connects ANY
OpenAI-compatible agent on the Wall's Connect page, and `<crissa home>/agent.json` holds the url, the key
(or where it lives), the model, the session header name and the persona. This module reads that file
through lib/crissa_agent (a pack puts the module beside this file; the source tree reads lib/) and answers
the five questions the bridge used to answer from the framework:

    endpoint(profile)   {"profile", "url", "key", "model", "voice", "session_header"} - every profile the
                        wake grammar knows (crissa, ada, the crew) is the SAME endpoint: who was addressed
                        travels in the prompt overlay, as it did when the house summoned a crew member
    system_prompt()     her persona (the user's own text, else the stock persona beside her manifest, else
                        nothing when the agent brings its own)
    session_header()    the header that keeps the agent's memory per conversation ("" = none)
    tool_event()        the SSE event-name suffix that marks a tool call ("tool.progress")
    guest_endpoint()    the same endpoint for a guest: no persona, no tools, no session

Nothing here imports an agent framework. With no agent connected, `endpoint()` has an empty url and the
bridge says so instead of calling anything.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# crissa_agent imports crissa_egress / crissa_net / crissa_home by name: a pack carries those beside the ear package
# (modules/ear/crissa_ear, the manifest's `vendor`), the source tree keeps them in lib/. Both are put on the path.
for _p in (Path(__file__).resolve().parents[1] / "crissa_ear", Path(__file__).resolve().parents[3] / "lib"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))
try:
    from . import crissa_agent as A              # the copy a pack puts beside this file
except ImportError:
    import crissa_agent as A                     # noqa: E402  the source tree's


def config() -> dict:
    try:
        return A.load()
    except Exception:  # noqa: BLE001  an unreadable file is "nothing connected"
        return {}


def connected(cfg: dict | None = None) -> bool:
    cfg = config() if cfg is None else cfg
    try:
        return bool(A.configured(cfg))
    except Exception:  # noqa: BLE001
        return bool((cfg or {}).get("url"))


def endpoint(profile: str = "crissa", cfg: dict | None = None) -> dict:
    cfg = config() if cfg is None else cfg
    url = A.chat_url(cfg) if cfg.get("url") else ""
    p = (profile or "crissa").strip().lower() or "crissa"
    return {
        "profile": p,
        "url": url,
        "key": str(cfg.get("key") or ""),
        "model": str(cfg.get("model") or os.environ.get("EAR_BRIDGE_MODEL") or "default"),
        "voice": os.environ.get("EAR_BRIDGE_%s_VOICE" % p.upper(), "").strip() or ("crissa" if p == "crissa" else p),
        "session_header": session_header(cfg),
    }


def guest_endpoint(cfg: dict | None = None) -> tuple[str, str]:
    """(url, model) for a guest turn: the same agent, called with no persona and no tools."""
    e = endpoint("crissa", cfg)
    return e["url"], e["model"]


def session_header(cfg: dict | None = None) -> str:
    cfg = config() if cfg is None else cfg
    return str(cfg.get("session_header") or os.environ.get("EAR_BRIDGE_SESSION_HEADER") or "").strip()


def system_prompt(cfg: dict | None = None) -> str:
    """Her persona for a system message; "" when the agent brings its own (`no_prompt`)."""
    cfg = config() if cfg is None else cfg
    try:
        msg = A.system_message(cfg)
    except Exception:  # noqa: BLE001
        msg = None
    return (msg or "")[:8000]


def tool_event() -> str:
    """The SSE event-name suffix that marks a tool call in the agent's stream."""
    return os.environ.get("EAR_BRIDGE_TOOL_EVENT", "tool.progress").strip() or "tool.progress"


def is_tool_event_line(line: bytes) -> bool:
    """An `event:` line of the stream naming a tool call (`event: <anything>.tool.progress`)."""
    if not line.startswith(b"event:"):
        return False
    name = line[6:].strip().decode("utf-8", "replace")
    suffix = tool_event()
    return name == suffix or name.endswith("." + suffix) or name.endswith(suffix)
