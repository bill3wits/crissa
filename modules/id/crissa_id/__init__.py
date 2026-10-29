"""Crissa id module: Init (the first-boot routine) and enrolment (the identity store), ported from the house's
house-id tools and its two Init walkers (2026-10-09; the User: "extract, don't rewrite").

    crissa_id.init_wizard     Init's entries; they hand over to the pack's one walk, the player's lib/crissa_init.py
                              (Connect, the machine card, Voice ID, face photos, done - the User 2026-10-10);
                              config/init-sequence.json names those four steps
    crissa_id.init_v2         the same handover (Init v2's own steps are retired in a pack)
    crissa_id.house_enroll    the enrol CLI (python -m crissa_id.house_enroll ...), the Register routine, the coach
    crissa_id.face_engine     faces: MediaPipe Face Landmarker + AuraFace, as modules/oracle does
    crissa_id.voice_embed     voices: WeSpeaker (live), ECAPA (museum)
    crissa_id.pack_store      the pack's own records: init.json, user.json, enroll.json (what the player reads)

The tools import each other the way they did in the house (`import voice_embed`, `from house_enroll...`), so this
package puts its own directory on sys.path once; nothing else is renamed.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from . import paths  # noqa: E402,F401  exports HOUSE_ID_ROOT for every reader of the store

__version__ = "1.0.0"
