"""Door rest FOV must not stay on seat stops after tours (user 2026-08-12)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))


def test_pose_off_door_ignores_nothing_but_axes():
    """Sticky XU frame=1 must not count as off-door (2026-08-16 fight)."""
    from crissa_presence.grabber import pose_off_door

    door = dict(want_pan=450000, want_tilt=18000, pan_tol=24000, tilt_tol=8000)
    assert pose_off_door(450000, 18000, **door) is False
    assert pose_off_door(450000, None, **door) is False
    # Claude's wander: head-framing dragged axes. Pose sees it.
    assert pose_off_door(374400, -18000, **door) is True
    assert pose_off_door(450000, -18000, **door) is True
    assert pose_off_door(300000, 18000, **door) is True


def test_recover_rest_not_keyed_on_frame_on():
    import inspect
    from crissa_presence import grabber as g

    src = inspect.getsource(g._recover_rest_to_door)
    assert "pose_off_door(" in src
    assert "frame_on" not in src
    assert "facelock_live" in src


def test_finish_at_door_home_releases_on_track_not_frame():
    """Sticky selector-19 GET must not 4× OFF every return_door (2026-08-26)."""
    import inspect
    from crissa_presence import grabber as g

    src = inspect.getsource(g._finish_at_door_home)
    assert "frame_on" not in src
    assert "facelock_live" in src


def test_facelock_live_ignores_sticky_frame_get():
    from crissa_presence.firmware_facelock import facelock_live

    assert facelock_live(None) is False
    assert facelock_live({}) is False
    assert facelock_live({"track_on": False, "frame_on": True}) is False
    assert facelock_live({"track_on": True, "frame_on": True}) is True
    assert facelock_live({"track_on": True, "frame_on": False}) is True


def test_release_and_hold_skips_when_track_off(monkeypatch):
    from crissa_presence import firmware_facelock as ff

    called: list = []
    monkeypatch.setattr(
        ff,
        "firmware_status",
        lambda device=None: {"track_on": False, "frame_on": True},
    )
    monkeypatch.setattr(
        ff, "firmware_track", lambda enable, device=None: called.append(enable) or True
    )
    r = ff.release_and_hold("/dev/null")
    assert r.get("skipped") == "track_off"
    assert r.get("ok") is True
    assert called == []


def test_release_and_hold_second_off_only_if_track_stays(monkeypatch):
    from crissa_presence import firmware_facelock as ff

    called: list = []
    states = iter(
        [
            {"track_on": True, "frame_on": True},
            {"track_on": False, "frame_on": True},
        ]
    )
    monkeypatch.setattr(ff, "firmware_status", lambda device=None: next(states))
    monkeypatch.setattr(
        ff, "firmware_track", lambda enable, device=None: called.append(enable) or True
    )
    r = ff.release_and_hold("/dev/null")
    assert called == [False]
    assert r.get("double_release") is False
    assert r.get("skipped") is None


def test_firmware_track_does_not_alarm_on_sticky_frame_get():
    import inspect
    from crissa_presence.firmware_facelock import firmware_track

    tr = inspect.getsource(firmware_track)
    assert 'after.get("frame_on")' not in tr
    assert "track_stuck_on" in tr


def test_rest_off_door_policy_constants():
    from crissa_presence import grabber as g

    # Door home comes from INIT, not a literal. Hardcoding it here is the same
    # mistake that cost 2026-08-16: the door pose lived in room_geometry.json,
    # camera-stops.json and a process constant, and when they drifted apart the
    # motion gate silently emitted nothing. The test's job is that they AGREE.
    from crissa_presence.seat_shot import door_pose

    assert int(g.DOOR_HOME_PAN) == int(door_pose()["pan"]), (
        "grabber DOOR_HOME_PAN (room_geometry heading) must match "
        "camera-stops door.pan — a mismatch empties the trigger ring silently"
    )
    assert g.REST_OFF_DOOR_SEC > 0
    assert g.DOOR_HOME_GUARD is True or g.DOOR_HOME_GUARD is False


def test_every_module_agrees_on_where_the_door_is():
    """The half of 2026-08-16 the grabber assertion above did not cover.

    camera-stops.json moved to 432000/200 and rail-camera followed, but
    service.py, room_sweep, error_peek and look_around each kept their own
    `int(os.environ.get("RAIL_PRESENCE_DOOR_PAN", "450000"))`, and the unit
    file set that env to the old value — so presence commanded the camera five
    degrees off the door while the rest guard pulled it back, and
    door_fanout's in-view check answered False for every trigger.

    One reader, one number. Assert it, so the next re-measure cannot leave a
    module behind.
    """
    from crissa_presence import error_peek, look_around, room_sweep, service
    from crissa_presence.seat_shot import door_pose

    want = int(door_pose()["pan"])
    for mod in (service, room_sweep, error_peek, look_around):
        assert int(mod.DOOR_PAN) == want, (
            f"{mod.__name__}.DOOR_PAN is {mod.DOOR_PAN}, camera-stops door.pan "
            f"is {want} — this drift is silent and empties the trigger ring"
        )
    assert int(service.DOOR_TILT) == int(door_pose()["tilt"])


def test_tool_hold_not_implied_by_off_door_only():
    """Regression: stuck chair pan used to force tool_hold forever.

    Presence paused door analysis while cam watched the wrong place.
    tool_hold must require sweep/lease/timed hold — not merely pan != door.
    """
    from crissa_presence import grabber as g

    # Source must not encode (not at_door) into tool_hold
    import inspect

    src = inspect.getsource(g.health)
    assert "not at_door" not in src.replace(" ", "")
    # still mentions tool_hold construction via sweeping / hold / lease
    assert "tool_hold" in src
    assert "_sweeping" in src or "hold_rem" in src


def test_presence_run_door_once_not_tool_hold_on_at_door_false_alone():
    """Door-hold law: at_door=false alone must not freeze door pipeline forever."""
    import inspect
    from crissa_presence import service as svc_mod

    src = inspect.getsource(svc_mod.PresenceService.run_door_once)
    # Must not OR at_door False into tool_hold (the overnight freeze)
    assert 'g.get("at_door") is False)' not in src.replace(" ", "")
    assert "door-hold" in src or "door_recover" in src or "_ensure_door_aim" in src
