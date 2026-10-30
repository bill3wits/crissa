"""The door picture verify and head-framing OFF (the User 2026-10-06). No camera."""
from pathlib import Path

from crissa_presence import door_picture as dp
from crissa_presence import firmware_facelock as ff

REFS = Path(__file__).resolve().parents[1] / "data" / "body_audit_refs"


def test_the_door_reference_is_the_door_and_a_seat_is_not():
    door = (REFS / "door_home_live.jpg").read_bytes()
    desk = (REFS / "st6_tilt-5.jpg").read_bytes()
    assert dp.check(door)["is_door"]
    assert not dp.check(desk)["is_door"]
    assert dp.check(b"not a jpeg")["ok"] is False


def test_switches(monkeypatch):
    monkeypatch.setenv("RAIL_DOOR_PICTURE_VERIFY", "0")
    assert not dp.enabled()
    monkeypatch.setenv("RAIL_FRAME_OFF_ALWAYS", "0")
    assert ff.frame_off("/dev/null-none") is False


def test_release_with_track_off_still_sends_frame_off(monkeypatch):
    sent = []
    monkeypatch.setattr(ff, "firmware_status", lambda device=None: {"track_on": False})
    monkeypatch.setattr(ff, "_TRACK_ARMED", False)
    monkeypatch.setattr(ff, "frame_off", lambda device=None: sent.append(device) or True)
    out = ff.release_and_hold("/dev/x")
    assert out["skipped"] == "track_off" and out["frame_off"] is True and sent == ["/dev/x"]


# ---- settle before judging (the User 2026-10-09) ---------------------------------
from types import SimpleNamespace  # noqa: E402

from crissa_presence import grabber as g  # noqa: E402


def _rig(monkeypatch, reads):
    """reads: is_door per check, in order. Returns the list of re-home aims."""
    seq = iter(reads)
    monkeypatch.setattr(dp, "enabled", lambda: True)
    monkeypatch.setattr(dp, "check", lambda jpg: {"ok": True, "door": 0, "best_other": 0, "is_door": next(seq)})
    monkeypatch.setattr(g, "get_jpeg", lambda: (b"j", {}))
    monkeypatch.setattr(g, "_capture_once", lambda: True)
    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    monkeypatch.setattr(ff, "frame_off", lambda device=None: True)
    aims = []
    monkeypatch.setattr(g, "ptz_aim", lambda *a, **k: aims.append("step"))
    monkeypatch.setattr(g, "aim_door_home", lambda *a, **k: aims.append("home") or SimpleNamespace(ok=True))
    return aims


def test_an_unsettled_frame_is_looked_at_again_before_a_rehome(monkeypatch):
    aims = _rig(monkeypatch, [False, False, True])
    out = g._verify_door_picture(432000, 0, 100, 3600, 1.6)
    assert aims == [] and out["settled"]["is_door"] and len(out["settle_reads"]) == 2


def test_three_more_not_door_reads_still_rehome(monkeypatch):
    aims = _rig(monkeypatch, [False, False, False, False, True])
    out = g._verify_door_picture(432000, 0, 100, 3600, 1.6)
    assert aims == ["step", "home"] and len(out["settle_reads"]) == 3 and out["second"]["is_door"]


def test_the_settle_wait_is_one_switch_away(monkeypatch):
    monkeypatch.setenv("RAIL_DOOR_PICTURE_SETTLE", "0")
    aims = _rig(monkeypatch, [False, True])
    out = g._verify_door_picture(432000, 0, 100, 3600, 1.6)
    assert aims == ["step", "home"] and "settle_reads" not in out
