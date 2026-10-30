"""Speech look is a station pick, not a Busy/F2F mode."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.beam_look import decide, ff_at_beam_blocked, hour_for, look_ok
from crissa_presence.seat_shot import hour_from_az, pose_for_hour


def test_puck_vad_is_not_a_look():
    assert look_ok(impulse=False, reason="sound.heard") == "need_session"
    assert look_ok(impulse=False, reason="") == "need_session"
    assert look_ok(reason="stt_voice") == "need_session"
    assert look_ok(impulse=True) == "need_session"
    assert look_ok(reason="vad") == "need_session"
    assert look_ok(reason="impulse") == "need_session"
    assert look_ok(reason="wake") is None
    assert look_ok(reason="session_open") is None
    assert look_ok(reason="hey_crissa") is None
    assert look_ok(already_looking=True, reason="session_open") is None
    assert look_ok(already_looking=True, reason="vad") == "already_looking"
    assert look_ok(already_looking=True, reason="impulse") == "already_looking"
    assert look_ok(already_looking=True, impulse=False) == "need_session"


def test_az_181_is_desk():
    assert hour_from_az(181) == 6
    assert hour_for(az=181) == 6
    d = decide(az=181.0, person_id="s1")
    assert d.get("skipped") is None
    assert d["stop"] == "desk"
    assert d["hour"] == 6
    assert d["pose"]["pan"] == 0


def test_az_240_is_wanda():
    d = decide(az=240.0, person_id="s1")
    assert d.get("skipped") is None
    assert d["stop"] == "wanda"
    assert d["hour"] == 8


def test_media_beam_does_not_look():
    d = decide(hour=7, person_id="user")
    assert d.get("skipped") in ("media_beam", "media_on", "media_locus")
    assert d.get("pose") is None
    assert pose_for_hour(7) is None


def test_media_on_blocks_desk_look(monkeypatch):
    import crissa_presence.beam_look as bl

    monkeypatch.setattr(
        bl,
        "fetch_media_status",
        lambda: {"media_on": True, "verdict": "media", "phase": "media_hold"},
    )
    d = decide(az=181.0, person_id="s1")
    assert d.get("skipped") == "media_on"
    assert d.get("pose") is None


def test_media_characterizing_does_not_block_desk():
    import crissa_presence.beam_look as bl

    bl._MEDIA_SNAP[1] = {
        "media_on": False,
        "verdict": "unsure",
        "phase": "characterize",
    }
    try:
        d = decide(hour=6, person_id="user")
        assert d.get("skipped") not in ("media_locus", "media_beam", "media_on")
        assert d.get("stop") == "desk"
        d7 = decide(hour=7, person_id="user")
        assert d7.get("skipped") in ("media_beam", "media_locus")
    finally:
        bl._MEDIA_SNAP[1] = {}


def test_inspector_lease_is_not_stolen():
    d = decide(az=181.0, lease_owner="inspector")
    assert d.get("skipped") == "inspector"
    d2 = decide(az=181.0, lease_owner="who_is_here")
    assert d2.get("skipped") == "inspector"


def test_mill_lease_does_not_veto():
    d = decide(az=181.0, lease_owner="door_mill")
    assert d.get("skipped") is None
    assert d["stop"] == "desk"


def test_busy_is_not_an_input():
    import inspect

    from crissa_presence import beam_look as bl

    src = inspect.getsource(bl.decide)
    body = src.split('"""', 2)[-1]
    assert "room_busy" not in body
    assert "headcount" not in body
    assert "_ensure_busy" not in body


def test_same_station_does_not_reslew():
    d = decide(az=181.0, current_hour=6, looking=True)
    assert d.get("skipped") == "same_station"
    assert d["stop"] == "desk"


def test_user_without_hour_falls_back_to_desk():
    d = decide(hour=None, az=None, person_id="user", user_id="user")
    assert d.get("skipped") is None
    assert d["stop"] == "desk"


def test_scratch_without_lobe_does_not_invent_desk():
    d = decide(hour=None, az=None, person_id="s1", user_id="user")
    assert d.get("skipped") == "no_station"


def test_door_hours_are_rest_not_a_heading():
    d = decide(hour=12)
    assert d.get("skipped") is None
    assert d["pose"]["home"] is True
    assert d["stop"] == "door"


def test_ff_at_beam_blocked_before_station_pick():
    """Skip FaceFinder-at-beam only while Secure. Held AUTH is not a skip."""
    assert ff_at_beam_blocked() is None
    assert ff_at_beam_blocked(visit_lamp="sage") == "secure"
    assert ff_at_beam_blocked(user_auth=True) is None
    assert ff_at_beam_blocked(locked="user", user_id="user", age_s=0.4) is None
    assert ff_at_beam_blocked(locked="user", user_id="user", age_s=9.0) is None
    assert ff_at_beam_blocked(locked="xena", user_id="user", age_s=0.2) is None
    assert ff_at_beam_blocked(user_auth=True, visit_lamp="sage") == "secure"
    # Precheck is not decide() — beam vector never runs.
    d = decide(hour=6, az=181.0)
    assert d.get("stop") == "desk"
