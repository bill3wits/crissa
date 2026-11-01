"""Argus describe lines stay on omni — they must not wake Crissa."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "modules" / "ear"))

from crissa_bridge.ear_bridge import is_oracle_feed, is_spoken_via


def test_argus_via_is_oracle_feed():
    assert is_oracle_feed(
        {
            "text": "No person crosses the doorway; the clip only shows an empty room with a black dog",
            "person_id": "argus",
            "via": "argus",
            "stamp": {"person_id": "argus", "source": "argus_describe", "certain": False},
        }
    )


def test_human_ear_is_not_oracle_feed():
    assert not is_oracle_feed(
        {
            "text": "There it is. That's it.",
            "person_id": "user",
            "via": "ear",
            "stamp": {"person_id": "user", "source": "voice"},
        }
    )


def test_argus_source_without_via():
    assert is_oracle_feed(
        {"text": "empty doorway", "stamp": {"source": "argus_describe"}}
    )


def test_spoken_via_is_ear_not_argus():
    assert is_spoken_via({"via": "ear", "text": "Hey Zoe."})
    assert is_spoken_via({"via": "stt", "text": "ok"})
    assert not is_spoken_via({"via": "argus", "text": "crossed the doorway"})
    assert not is_spoken_via(
        {"via": "argus", "person_id": "argus", "stamp": {"source": "argus_describe"}}
    )
    assert is_oracle_feed({"via": "argus"}) or not is_spoken_via({"via": "argus"})
