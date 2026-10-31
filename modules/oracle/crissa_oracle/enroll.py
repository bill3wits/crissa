"""The enrolled user's face, kept on this machine only (2026-10-01).

<crissa home>/oracle/user-face.json, mode 0600: a short list of identity vectors from
the user's own enrolment looks. Never a picture. Deleting the file forgets the user.
"""
from __future__ import annotations

import json
import os

from . import setup

PATH = setup.ROOT / "user-face.json"
MAX = 12


INIT_PATH = setup.ROOT.parent / "enroll.json"       # Init's store (lib/crissa_presence): {"owner", "faces": [[...], ...]}


def load(path=None, init_path=None) -> list[list[float]]:
    """The owner's vectors: this module's own file plus the ones Init wrote (the same AuraFace numbers, so one
    enrollment serves the presence service and the oracle; 2026-10-08)."""
    out: list[list[float]] = []
    try:
        data = json.loads((path or PATH).read_text(encoding="utf-8"))
        out += [v for v in data.get("vectors") or [] if isinstance(v, list)]
    except (OSError, ValueError, AttributeError):
        pass
    try:
        data = json.loads((init_path or INIT_PATH).read_text(encoding="utf-8"))
        out += [v for v in data.get("faces") or [] if isinstance(v, list)]
    except (OSError, ValueError, AttributeError):
        pass
    return out


def add(vector: list[float], path=None) -> int:
    p = path or PATH
    vecs = (load(p) + [list(vector)])[-MAX:]
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"engine": "auraface-v1", "vectors": vecs}, f)
    os.replace(tmp, p)
    return len(vecs)
