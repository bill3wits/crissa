"""Resolve the identity store and its standard subpaths (the pack: crissa_id.paths decides, 2026-10-09).

The house read a records volume, a legacy repo clone and a `house-local.json`; a pack reads one state home.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import paths as _paths

_PKG = Path(__file__).resolve().parent
_TOOLS = _PKG.parent                      # crissa_id
_LEGACY_ROOT = _TOOLS.parent              # modules/id (holds config/site.example.json, never people)


def _local_value(key: str):
    """A value from the identity store's house-local.json (an installer's own fallbacks; absent in a pack)."""
    try:
        import json as _json

        return _json.loads((house_id_root() / "house-local.json").read_text()).get(key)
    except Exception:  # noqa: BLE001
        return None


def _on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "on", "true", "yes")


def rails_root() -> Path | None:
    """Where the pack lives: HOUSE_RAILS_ROOT, else this install (crissa_id.paths.PACK)."""
    return _paths.rails_root()


def rails_path(*parts: str) -> Path:
    return _paths.rails_path(*parts)


def house_id_root() -> Path:
    """The identity store: CRISSA_IDENTITY, else HOUSE_ID_ROOT, else <state home>/identity."""
    return _paths.identity_root().resolve() if _paths.identity_root().exists() else _paths.identity_root()


def people_dir(root: Path | None = None) -> Path:
    return (root or house_id_root()) / "people"


def person_dir(person_id: str, root: Path | None = None) -> Path:
    return people_dir(root) / person_id.strip().lower()


def index_path(root: Path | None = None) -> Path:
    return people_dir(root) / "_index.json"


def site_config_path(root: Path | None = None) -> Path:
    return (root or house_id_root()) / "site.json"


def site_example_path(root: Path | None = None) -> Path:
    """The template ships with the module (modules/id/config/site.example.json); a copy in the store wins."""
    own = (root or house_id_root()) / "site.example.json"
    return own if own.is_file() else _LEGACY_ROOT / "config" / "site.example.json"


def mouths_root() -> Path:
    env = os.environ.get("HOUSE_MOUTHS_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return house_id_root() / "mouths"
