"""A test pack's stamp audit, written the way the store's builder writes it (store runner/stamp_pack.py: step 8's audit,
gaps none, bound to the pack's files by pack.stamp_pack_sha). Every clip gets intake (the User 2026-10-30): the store lists
and sells only a pack that carries a clean one, so a fixture pack meant to list is certified here."""
from __future__ import annotations

import json
from pathlib import Path


def certify(pdir: Path, gaps: dict | None = None) -> Path:
    from imagine_package import pack
    p = Path(pdir) / pack.STAMP_AUDIT
    p.write_text(json.dumps({"schema": pack.STAMP_SCHEMA, "at": "2026-10-30T00:00:00", "pack_sha": pack.stamp_pack_sha(pdir),
                             "gaps": gaps or {}, "exempt": [], "by": "test fixture"}, indent=1) + "\n")
    return p
