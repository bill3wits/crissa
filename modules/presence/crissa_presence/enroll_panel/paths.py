"""Resolve HOUSE_ID_ROOT and standard subpaths."""

from __future__ import annotations

from .. import paths as _paths

import os
from pathlib import Path

# tools/house_enroll/paths.py — enroll CLI lives next to optional backup copy.
_PKG = Path(__file__).resolve().parent
_TOOLS = _PKG.parent
_LEGACY_ROOT = _TOOLS.parent
_SECURITY_ROOT = Path(str(_paths.IDENTITY))


def _local_value(key: str):
    """A value from this tree's house-local.json (gitignored), read directly: local_values imports this module."""
    try:
        import json as _json

        return _json.loads((_LEGACY_ROOT / "house-local.json").read_text()).get(key)
    except Exception:  # noqa: BLE001
        return None


def _on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "on", "true", "yes")


def rails_root() -> Path | None:
    """Where the Wall repo lives (the User 2026-10-02, Init step 5): HOUSE_RAILS_ROOT, else house-local.json
    "house_rails_root". Off (HOUSE_ID_RAILS_CONFIG unset), today's ~/projects/the Wall repo stands in; on, there is no
    guess - None, and every caller degrades instead of crashing (Register 500'd without it)."""
    env = os.environ.get("HOUSE_RAILS_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    v = _local_value("house_rails_root")
    if v:
        return Path(str(v)).expanduser()
    if _on("HOUSE_ID_RAILS_CONFIG"):
        return None
    return _paths.PACK


def rails_path(*parts: str) -> Path:
    """A path inside the Wall repo; under a missing root, a path that does not exist (callers check is_file)."""
    r = rails_root()
    return (r if r is not None else Path("/nonexistent-pack")).joinpath(*parts)


def house_id_root() -> Path:
    """Runtime identity is <records>/identity (init writes it).

    ~/projects/identity-store is tools + optional backup, not a required git repo.
    """
    env = os.environ.get("HOUSE_ID_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if _on("HOUSE_ID_ONE_ROOT"):
        # ONE identity root (the User 2026-10-02, Init step 5): the CLI and the services read the same folder - the
        # configured one, else the services' - never this code tree (which holds tools, not people).
        v = _local_value("identity_root")
        return (Path(str(v)).expanduser() if v else _SECURITY_ROOT).resolve()
    if (_SECURITY_ROOT / "people").is_dir() or (_SECURITY_ROOT / "site.json").is_file():
        return _SECURITY_ROOT.resolve()
    if _LEGACY_ROOT.is_dir() and (_LEGACY_ROOT / "people").is_dir():
        return _LEGACY_ROOT.resolve()
    return _SECURITY_ROOT


def people_dir(root: Path | None = None) -> Path:
    return (root or house_id_root()) / "people"


def person_dir(person_id: str, root: Path | None = None) -> Path:
    return people_dir(root) / person_id.strip().lower()


def index_path(root: Path | None = None) -> Path:
    return people_dir(root) / "_index.json"


def site_config_path(root: Path | None = None) -> Path:
    return (root or house_id_root()) / "site.json"


def site_example_path(root: Path | None = None) -> Path:
    return (root or house_id_root()) / "site.example.json"


def mouths_root() -> Path:
    env = os.environ.get("HOUSE_MOUTHS_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _paths.RECORDS / "mouths"
