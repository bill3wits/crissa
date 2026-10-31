"""Which agent a guest's turn or the backup reply may call: the Connect endpoint, with no persona (2026-10-09).

The house switched this between two local engines by its GPU occupancy (one parked while the other
rendered). A pack has one agent, the one connected on the Wall's Connect page, so a guest is answered by
the same endpoint with no persona and no tools (crissa_bridge.connect.guest_endpoint). `RAIL_VOICE_LLM` /
`EAR_BRIDGE_OPS` (+ `RAIL_VOICE_MODEL` / `EAR_BRIDGE_OPS_MODEL`) still name another chat-completions URL for
guests when a site wants a lighter model there.
"""
from __future__ import annotations

import os

from . import connect


def gpu_mode() -> str:
    """Kept for callers that asked the house's GPU occupancy; a pack is always "normal"."""
    return "normal"


def chat() -> tuple[str, str]:
    """Return (completions_url, served_name) for a guest or backup reply."""
    url = os.environ.get("RAIL_VOICE_LLM") or os.environ.get("EAR_BRIDGE_OPS") or ""
    if url:
        model = os.environ.get("RAIL_VOICE_MODEL") or os.environ.get("EAR_BRIDGE_OPS_MODEL") or "default"
        return url, model
    return connect.guest_endpoint()
