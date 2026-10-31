"""the User-at-chair gate — pure logic (default OFF in config)."""
from __future__ import annotations

from crissa_bridge.pipeline import user_at_chair_ok


def test_sole_present_user_ok():
    stamp = {
        "person_id": "user",
        "source": "sole_present",
        "roster": ["user"],
    }
    assert user_at_chair_ok(stamp) is True


def test_voice_emb_user_ok():
    stamp = {
        "person_id": "user",
        "source": "voice_emb",
        "roster": ["user", "second"],
    }
    assert user_at_chair_ok(stamp) is True


def test_vocative_not_enough():
    stamp = {
        "person_id": "user",
        "source": "vocative_in_room",
        "roster": ["user", "second"],
    }
    assert user_at_chair_ok(stamp) is False


def test_null_speaker_blocked():
    stamp = {
        "person_id": None,
        "source": None,
        "roster": ["user", "second"],
    }
    assert user_at_chair_ok(stamp) is False


def test_second_blocked():
    stamp = {
        "person_id": "second",
        "source": "sole_present",
        "roster": ["second"],
    }
    assert user_at_chair_ok(stamp) is False


def test_user_not_on_roster():
    stamp = {
        "person_id": "user",
        "source": "sole_present",
        "roster": ["second"],
    }
    assert user_at_chair_ok(stamp) is False


def test_roster_from_presence():
    stamp = {"person_id": "user", "source": "sole_present"}
    presence = {"people": [{"name": "user"}, {"name": "second"}]}
    assert user_at_chair_ok(stamp, presence) is True
