"""Empty-room stills.

Nightly capture writes ``data/refs/live/``. Subtract, killbook, and the poster
filter read only that directory. Dated promotions go to ``data/refs/archive/``.
This-office mirror: ``<identity>/reference-frames``.
"""
from __future__ import annotations

from . import paths as _paths

import os
from pathlib import Path

_REPO = _paths.ROOT
_DEFAULT_LIVE = _REPO / "data" / "refs" / "live"
_DEFAULT_ARCHIVE = _REPO / "data" / "refs" / "archive"
_DEFAULT_BODY = _REPO / "data" / "body_audit_refs"


def _env_path(*keys: str, default: Path) -> Path:
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if v:
            return Path(v).expanduser()
    return default


LIVE_DIR = _env_path("RRC_REF_DIR", "RAIL_REF_DIR", "RAIL_DOOR_BG_REFS", default=_DEFAULT_LIVE)
ARCHIVE_DIR = _env_path("RRC_REF_ARCHIVE", "RAIL_REF_ARCHIVE", default=_DEFAULT_ARCHIVE)
BODY_AUDIT_DIR = _env_path("RRC_BODY_AUDIT_DIR", "RAIL_BODY_AUDIT_REFS", default=_DEFAULT_BODY)
HOUSE_ID_MIRROR = _env_path(
    "RRC_HOUSE_ID_REF",
    default=_paths.IDENTITY / "reference-frames",
)
