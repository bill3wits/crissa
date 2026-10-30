import os

from .occupancy_llm import chat as occupancy_chat
from .settings import url as _url  # this install's addresses (the shared port table, 2026-10-10)

STT_URL = os.environ.get("RAIL_VOICE_STT", _url("stt", path="/stt"))
# Light brain = Unsloth 27B. Normal: ops :8000. Render/Beast park ops → crew.
# Bearer key for a gated LLM endpoint; empty = no header
LLM_KEY = os.environ.get("RAIL_VOICE_LLM_KEY", "")
# Backup rail-voice LLM path (not free-air Crissa). Was 128 — choked thinking models.
LLM_MAX_TOKENS = int(os.environ.get("RAIL_VOICE_MAX_TOKENS", "1024"))
TTS_URL = os.environ.get("RAIL_VOICE_TTS", "")      # the CLI's own voice path; "" = house-speak
TTS_VOICE = os.environ.get("RAIL_VOICE_VOICE", "zoe")
SYSTEM = os.environ.get(
    "RAIL_VOICE_SYSTEM",
    "You are a concise house assistant. Answer in 1-2 short spoken sentences. "
    "No markdown, no bullet lists, no internal monologue — only what Crissa should say aloud.",
)

# the User-at-chair gate (default OFF). When "1"/"true"/"yes", only allow LLM when
# stamp says the user via voice_emb or sole_present and the user is on the roster.
def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def __getattr__(name: str):
    """LLM target follows occupancy at access time (ear-bridge lives across switches)."""
    if name == "LLM_URL":
        return occupancy_chat()[0]
    if name == "LLM_MODEL":
        return occupancy_chat()[1]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


AUTH_GATE = _env_bool("RAIL_VOICE_AUTH_GATE", False)
AUTH_GATE_REFUSE = os.environ.get(
    "RAIL_VOICE_AUTH_GATE_REFUSE",
    "I need the owner at the chair before I take that. Who is speaking?",
)
