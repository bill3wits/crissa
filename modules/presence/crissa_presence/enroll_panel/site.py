"""Load per-office site.json (devices + rails URLs)."""

from __future__ import annotations

from .. import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from .paths import house_id_root, site_config_path, site_example_path


@dataclass
class Site:
    """One physical office / room install."""

    root: Path
    site_id: str
    display: str
    raw: dict[str, Any] = field(repr=False)

    @property
    def mic(self) -> str:
        return str(
            os.environ.get("HOUSE_ENROLL_MIC")
            or (self.raw.get("audio") or {}).get("mic")
            or ""
        )

    @property
    def sink(self) -> str | None:
        env = os.environ.get("HOUSE_ENROLL_SINK") or os.environ.get("VOICE_SINK")
        if env:
            return env.strip() or None
        s = (self.raw.get("audio") or {}).get("sink")
        return str(s) if s else None

    @property
    def speak_url(self) -> str:
        return str(
            os.environ.get("COSY_URL")
            or (self.raw.get("audio") or {}).get("speak_url")
            or _ports.url("player", "/v1/speak")   # the player's own voice, as the id module's coach
        )

    @property
    def speak_voice(self) -> str:
        return str((self.raw.get("audio") or {}).get("speak_voice") or "announcer")

    @property
    def speak_enabled(self) -> bool:
        if os.environ.get("HOUSE_ENROLL_QUIET") == "1":
            return False
        return bool((self.raw.get("audio") or {}).get("speak_enabled", True))

    @property
    def presence_url(self) -> str:
        return str((self.raw.get("rails") or {}).get("presence") or _ports.url("presence"))

    @property
    def camera_url(self) -> str:
        return str((self.raw.get("rails") or {}).get("camera") or _ports.camera())

    @property
    def voice_seconds(self) -> float:
        return float((self.raw.get("voice") or {}).get("seconds") or 4.2)

    @property
    def beep_sec(self) -> float:
        return float((self.raw.get("voice") or {}).get("beep_sec") or 0.4)

    @property
    def min_peak(self) -> int:
        return int((self.raw.get("voice") or {}).get("min_peak") or 500)

    @property
    def voice_model(self) -> str:
        """Live encoder id — rail-stt match, not the museum ECAPA tag in site.json."""
        try:
            from .. import voice_embed

            return str(voice_embed.model_id())
        except Exception:
            return str(
                (self.raw.get("voice") or {}).get("model")
                or "wespeaker/english"
            )

    @property
    def voice_dim(self) -> int:
        try:
            from .. import voice_embed

            return int(voice_embed.dim())
        except Exception:
            return int((self.raw.get("voice") or {}).get("dim") or 256)

    def room_refs_dir(self) -> Path:
        rel = (self.raw.get("room_refs") or {}).get("dir") or "site/reference-frames"
        p = Path(str(rel))
        if p.is_absolute():
            return p
        return self.root / p

    def stops_json(self) -> Path | None:
        raw = (self.raw.get("room_refs") or {}).get("stops_json")
        if not raw:
            return None
        return Path(str(raw)).expanduser()

    @property
    def empty_polls(self) -> int:
        return int((self.raw.get("room_refs") or {}).get("empty_polls") or 6)

    @property
    def empty_interval_sec(self) -> float:
        return float((self.raw.get("room_refs") or {}).get("empty_interval_sec") or 5)


def load_site(root: Path | None = None) -> Site:
    root = (root or house_id_root()).resolve()
    path = site_config_path(root)
    if not path.is_file():
        raise FileNotFoundError(
            f"No site.json at {path}. Copy site.example.json → site.json and edit, "
            f"or run: python -m crissa_presence.enroll_panel init-site"
        )
    raw = json.loads(path.read_text())
    if raw.get("schema") not in (None, "house_site/v1"):
        raise ValueError(f"Unsupported site schema: {raw.get('schema')}")
    return Site(
        root=root,
        site_id=str(raw.get("site_id") or "unknown"),
        display=str(raw.get("display") or raw.get("site_id") or "unknown"),
        raw=raw,
    )


def init_site(root: Path | None = None, force: bool = False) -> Path:
    """Create site.json from example if missing."""
    root = (root or house_id_root()).resolve()
    dest = site_config_path(root)
    if dest.is_file() and not force:
        return dest
    src = site_example_path(root)
    if not src.is_file():
        raise FileNotFoundError(f"Missing template {src}")
    shutil.copy2(src, dest)
    return dest
