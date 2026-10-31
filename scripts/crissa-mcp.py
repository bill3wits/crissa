#!/usr/bin/env python3
"""The Wall as an MCP server (2026-10-01): any agent that speaks MCP can drive the avatar.

    {"mcpServers": {"crissa": {"command": "python3", "args": ["<pack>/scripts/crissa-mcp.py"],
                               "env": {"CRISSA_PLAYER": "http://127.0.0.1:8100"}}}}

Tools are lib/crissa_wall_verbs.VERBS: wall_state, wall_keys, wall_cue, wall_home, wall_comms, wall_speak,
wall_ask, wall_stop, wall_announce. Each makes one call to the player's public HTTP routes. Transport: stdio,
newline-delimited JSON-RPC 2.0. Standard library only. Logs go to stderr, never stdout.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# A MAC'S SYSTEM PROXY NEVER CARRIES A LOOPBACK CALL (2026-10-04): urllib applies macOS's proxy settings to
# 127.0.0.1. Any *_proxy in the environment replaces the system settings outright on macOS (urllib reads the
# environment first), so a Mac that needs a proxy to go out sets HTTPS_PROXY beside this. Kill CRISSA_NO_PROXY=0.
if sys.platform == "darwin" and os.environ.get("CRISSA_NO_PROXY", "1").strip() != "0":
    for _k in ("NO_PROXY", "no_proxy"):
        os.environ.setdefault(_k, "127.0.0.1,localhost")


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import crissa_wall_verbs as V  # noqa: E402

SERVER = {"name": "crissa-wall", "version": "1.0.0"}
DEFAULT_PROTOCOL = "2025-06-18"


def tools() -> list[dict]:
    return [{"name": n, "description": d, "inputSchema": s} for n, d, s, _ in V.VERBS]


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message in, one response out (None for a notification)."""
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if mid is None:                      # notifications (initialized, cancelled): no reply
        return None
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion") or DEFAULT_PROTOCOL,
                  "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER,
                  "instructions": "Drive the Crissa avatar on this machine: speak, gesture, open a conversation."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tools()}
    elif method == "tools/call":
        name = params.get("name")
        if name not in V.BY_NAME:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool: %s" % name}}
        out = V.call(name, params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": json.dumps(out)}], "isError": not out.get("ok", False)}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                         "error": {"code": -32700, "message": "parse error"}}) + "\n")
            sys.stdout.flush()
            continue
        out = handle(msg) if isinstance(msg, dict) else None
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
