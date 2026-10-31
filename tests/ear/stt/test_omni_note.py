"""Argus describe lines land on the omni turn ring."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crissa_ear.omni_ui import OMNI_HTML  # noqa: E402
from crissa_ear.turn_log import TurnLog  # noqa: E402


def test_argus_via_appends_for_omni():
    tl = TurnLog(maxlen=20, jsonl=None)
    rec = tl.append(
        {
            "text": "person in a dark jacket walking left through the door",
            "person_id": "argus",
            "stamp": {
                "person_id": "argus",
                "source": "argus_describe",
                "certain": False,
            },
        },
        via="argus",
    )
    assert rec["via"] == "argus"
    assert rec["person_id"] == "argus"
    assert tl.list(n=1)[0]["text"].startswith("person in a dark")
    assert tl.list(n=10, via="ear,stt") == []
    assert tl.list(n=1, via=None)[0]["via"] == "argus"


def test_omni_ui_tags_argus_as_system():
    assert "via === 'argus'" in OMNI_HTML
    assert "argus_describe" in OMNI_HTML
