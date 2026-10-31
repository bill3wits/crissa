"""F2F is a named mode marker. Busy and door-pending keep the door."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.service import PresenceService


class _FakeCam:
    mode = "dir"
    last_error = None

    def grab(self):
        return None

    def grab_buffer(self, **kw):
        return []


def test_door_glance_skips_when_speech_look_owns_camera():
    svc = PresenceService(camera=_FakeCam())
    svc._f2f = True
    out = svc.door_glance()
    assert out.get("skipped") == "speech_look"


def test_f2f_marker_sets_mode_and_wall():
    svc = PresenceService(camera=_FakeCam())
    out = svc.set_f2f(True, reason="speech", look=False)
    assert out["f2f"] is True
    st = svc.get()
    assert st.f2f is True
    assert st.camera_mode == "f2f"
    d = st.to_dict()
    assert d["wall_count"] == "F2F"
    assert d["user"]["f2f"] is True
    off = svc.set_f2f(False, reason="look_done")
    assert off["f2f"] is False
    assert svc.get().f2f is False


def test_f2f_does_not_skip_busy_or_mill(monkeypatch):
    """Linger/Busy/mill queue are not a look veto."""
    svc = PresenceService(camera=_FakeCam())
    monkeypatch.setattr(svc, "_busy_off_recount", lambda: {"ok": True, "via": "test"})
    svc.set_room_busy(True, reason="linger")
    out = svc.set_f2f(True, reason="speech", look=False)
    assert out.get("skipped") is None
    assert svc.get().f2f is True
    monkeypatch.setattr(svc, "_door_work_pending", lambda: True)
    out2 = svc.set_f2f(True, reason="speech", look=False)
    assert out2.get("skipped") is None
    assert svc.get().f2f is True


def test_hour_from_az_picks_station_not_heading():
    from crissa_presence.seat_shot import hour_from_az, pose_for_az, pose_for_hour

    assert hour_from_az(180) == 6
    assert hour_from_az(181) == 6
    assert hour_from_az(0) == 12
    assert hour_from_az(240) == 8
    desk = pose_for_az(181)
    assert desk is not None and desk["id"] == "desk"
    assert pose_for_hour(hour_from_az(210)) is None  # media 7
    assert pose_for_az(None) is None


def test_f2f_beam_az_feeds_desk_even_for_scratch(monkeypatch):
    """Occupied s1 speech at az~180 looks at measured desk, not a heading slew."""

    class _NoStart:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr("crissa_presence.service.threading.Thread", _NoStart)
    svc = PresenceService(camera=_FakeCam())
    svc._sound_pub = lambda: {"az_deg": 181.0, "frame": "mic"}
    out = svc.set_f2f(True, reason="session_open", look=True, person_id="s1")
    assert out.get("skipped") is None
    assert out.get("stop") == "desk"
    assert out.get("hour") == 6


def test_pose_for_hour_uses_measured_stops():
    from crissa_presence.seat_shot import door_pose, pose_for_hour

    desk = pose_for_hour(6)
    assert desk is not None and desk["id"] == "desk" and desk["pan"] == 0
    mi = pose_for_hour(8)
    assert mi is not None and mi["id"] == "wanda" and mi["pan"] == 225000
    assert pose_for_hour(7) is None  # media
    assert pose_for_hour(4) is None  # wall
    assert pose_for_hour(9) is None  # no measured pan
    door = pose_for_hour(10)
    assert door is not None and door["home"] is True
    assert door["pan"] == door_pose()["pan"]
    assert door["pan"] != 450000
    plane = pose_for_hour(12)
    assert plane is not None and plane["home"] is True
    stand = pose_for_hour(11)
    assert stand is not None and stand["id"] == "stand_11" and stand["home"] is False


def test_f2f_session_open_skips_media_hour():
    """Vocative on the TV hour does not look. Desk vocative still can."""
    svc = PresenceService(camera=_FakeCam())
    out = svc.set_f2f(True, reason="session_open", look=True, hour=7)
    assert out.get("skipped") == "media_beam"
    assert svc.get().f2f is False


def test_f2f_vocative_beam_hour_eight_is_wanda(monkeypatch):
    svc = PresenceService(camera=_FakeCam())
    aimed: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true, "lease": null}'

    class _NoStart:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr("crissa_presence.service.threading.Thread", _NoStart)

    def fake_aim(pose, **kw):
        aimed["pan"] = int(pose["pan"])
        aimed["tilt"] = int(pose.get("tilt") or 0)
        aimed["stop"] = pose.get("id")
        return {"ok": True, "frames": [b"x"], "lease_id": "Ltest"}

    monkeypatch.setattr("crissa_presence.beam_look.aim_hold", fake_aim)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    out = svc.set_f2f(
        True, reason="session_open", look=True, hour=8, person_id="wanda"
    )
    assert out.get("skipped") is None
    assert out.get("stop") == "wanda"
    assert out.get("hour") == 8
    svc._f2f_looking = True
    svc._beam_aim_then_dwell()
    assert aimed.get("pan") == 225000
    assert aimed.get("stop") == "wanda"
    assert svc.get().f2f is True, "dwell — do not home at end of look"


def test_session_open_skips_desk_only_when_secure(monkeypatch):
    """Held AUTH is not a skip (Law 1). Sage is (Law 2)."""

    class _NoStart:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr("crissa_presence.service.threading.Thread", _NoStart)
    svc = PresenceService(camera=_FakeCam())
    monkeypatch.setattr(
        svc,
        "faceid_hud",
        lambda: {"locked": "user", "age_s": 0.3},
    )
    st = svc.get()
    st.user_auth = True
    out = svc.set_f2f(True, reason="session_open", look=True, hour=6)
    assert out.get("skipped") not in ("auth", "secure")
    assert out.get("look") is True
    beam = svc.request_beam_look(hour=6, reason="session_open")
    assert beam.get("skipped") not in ("auth", "secure")

    svc._visit_secure = True
    st = svc.get()
    st.visit_lamp = "sage"
    out = svc.set_f2f(True, reason="session_open", look=True, hour=6)
    assert out.get("skipped") == "secure"
    assert out.get("look") is False
    beam = svc.request_beam_look(hour=6, reason="session_open")
    assert beam.get("skipped") == "secure"


def test_f2f_user_without_hour_falls_back_to_desk(monkeypatch):
    class _NoStart:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr("crissa_presence.service.threading.Thread", _NoStart)
    svc = PresenceService(camera=_FakeCam())
    out = svc.set_f2f(True, reason="session_open", look=True, person_id="user")
    assert out.get("stop") == "desk"
    assert out.get("hour") == 6


def test_f2f_session_open_without_hour_aims_user_seat(monkeypatch):
    """Hey Crissa looks at 6 even when the vocative is not tagged User."""

    class _NoStart:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr("crissa_presence.service.threading.Thread", _NoStart)
    svc = PresenceService(camera=_FakeCam())
    out = svc.set_f2f(
        True, reason="session_open", look=True, hour=None, person_id="xena"
    )
    assert out.get("skipped") is None
    assert out.get("hour") == 6
    assert out.get("stop") == "desk"


def test_stt_voice_does_not_open_a_look(monkeypatch):
    """ASR-late stt_voice is hearing. Paused VAD is desk activity, not a look."""

    class _NoStart:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr("crissa_presence.service.threading.Thread", _NoStart)
    svc = PresenceService(camera=_FakeCam())
    out = svc.set_f2f(True, reason="stt_voice", look=True, person_id="user")
    assert out.get("skipped") == "need_session"
    assert svc.get().f2f is False
    puck = svc.set_f2f(True, reason="sound.heard", look=True, hour=6)
    assert puck.get("skipped") == "need_session"
    vad = svc.set_f2f(True, reason="vad", look=True, hour=6)
    assert vad.get("skipped") == "need_session"
    assert vad.get("desk_activity") is not True
    assert svc.get().f2f is False


def test_vad_while_looking_does_not_chase_az(monkeypatch):
    class _NoStart:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr("crissa_presence.service.threading.Thread", _NoStart)
    svc = PresenceService(camera=_FakeCam())
    open_ = svc.set_f2f(True, reason="session_open", look=True, hour=6)
    assert open_.get("hour") == 6
    assert open_.get("stop") == "desk"
    slew = svc.set_f2f(True, reason="vad", look=True, hour=8)
    assert slew.get("skipped") == "already_looking"
    assert svc._f2f_hour == 6


def test_session_open_does_not_keep_tv_vad_hour(monkeypatch):
    class _NoStart:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr("crissa_presence.service.threading.Thread", _NoStart)
    svc = PresenceService(camera=_FakeCam())
    vad = svc.set_f2f(True, reason="vad", look=True, hour=8)
    assert vad.get("skipped") == "need_session"
    open_ = svc.set_f2f(True, reason="session_open", look=True)
    assert open_.get("hour") == 6
    assert open_.get("stop") == "desk"


def test_unmapped_vocative_hour_falls_back_to_desk(monkeypatch):
    class _NoStart:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr("crissa_presence.service.threading.Thread", _NoStart)
    svc = PresenceService(camera=_FakeCam())
    out = svc.set_f2f(True, reason="session_open", look=True, hour=5)
    assert out.get("skipped") is None
    assert out.get("hour") == 6
    assert out.get("stop") == "desk"
