"""Empty-room surprise is any in-room voice, not wrong-voice / unrostered."""

import inspect

from crissa_ear.server import attach_stamp
from crissa_ear.presence import request_f2f, request_surprise


def test_empty_any_voice_not_unrostered():
    src = inspect.getsource(attach_stamp)
    assert "unrostered_speaker" not in src
    assert "stt_voice_unrostered" not in src
    assert "empty_room_voice" in src
    assert '"skipped": "media"' in src
    assert "mode = \"both\" if user_voice else \"body_count\"" in src


def test_occupied_voice_does_not_request_f2f():
    src = inspect.getsource(attach_stamp)
    assert 'st["f2f"] = request_f2f' not in src


def test_request_surprise_skips_media():
    src = inspect.getsource(request_surprise)
    assert "if media:" in src
    assert "skipped" in src
