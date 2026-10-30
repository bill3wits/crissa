"""tests/ear: the ear module's two packages (modules/ear/crissa_ear, crissa_bridge), ported from the house's
rail-stt (22 files) and rail-voice (34 files) suites on 2026-10-09.

The packages are imported from modules/ear, and every test runs against a scratch state home (CRISSA_HOME): the
ear writes its logs, banks and flags there, never into the tree or the developer's own home. The house's
parked occupancy tests stay skipped under the same names.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "modules" / "ear"
if str(MOD) not in sys.path:
    sys.path.insert(0, str(MOD))
if str(ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib"))
_HOME = Path(tempfile.mkdtemp(prefix="crissa-ear-tests-"))
os.environ["CRISSA_HOME"] = str(_HOME)
os.environ.setdefault("RAIL_STT_TURN_JSONL", "0")

_OCCUPANCY_PARKED = {
    "test_visitor_today_can_name_turn",
    "test_sole_present",
    "test_sole_present_stale_but_certain",
    "test_sole_present_stale_uncertain_provisional",
    "test_sole_present_beats_off_roster_mention",
    "test_in_room_name_when_multi",
    "test_ambiguous_two_names",
    "test_voice_emb_fills_unknown_slot",
    "test_vocative_fills_unknown_slot",
    "test_voice_off_named_room_still_allows_sole",
    "test_short_clip_skips_voice_stamp",
    "test_voice_emb_vs_sole_among_present",
    "test_empty_room_no_world_voice_stamp",
    "test_asnorm_shadow_fields_present_when_enabled",
    "test_asnorm_does_not_affect_live_gating",
    "test_asnorm_would_stamp_uses_threshold",
    "test_face_speaker_skipped_on_scenery_lock",
}


def pytest_collection_modifyitems(items):
    skip = pytest.mark.skip(reason="occupancy roster parked; stamp is bank+visor")
    for item in items:
        if item.name in _OCCUPANCY_PARKED:
            item.add_marker(skip)
