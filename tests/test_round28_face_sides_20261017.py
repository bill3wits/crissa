"""Round 28 (.101 2026-10-17, ASK 2): Setup asked "look to the left" twice and banked two right profiles (bins front +
three_quarter_R, left missing). The bins are the picture's: the face engine's first point is MediaPipe's EYE_R (the
subject's right eye, image-left in a true-handed picture), so a turn to HIS left moves the nose toward image-right, _R.
His left is _R, his right _L; a mirrored picture swaps them. Each turned photo is heard (ding + "Got it.")."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_init as zi  # noqa: E402


def test_his_left_is_the_pictures_right(monkeypatch):
    monkeypatch.delenv("CRISSA_FACE_SIDE_AS_SEEN", raising=False)
    monkeypatch.setattr(zi, "room_saved", lambda: {"picture": "true_handed"})
    assert zi.face_missing({"front": 1, "three_quarter_R": 1}) == ["right"], "101's two left turns: now ask his right"


def test_a_mirrored_picture_swaps_them(monkeypatch):
    monkeypatch.delenv("CRISSA_FACE_SIDE_AS_SEEN", raising=False)
    monkeypatch.setattr(zi, "room_saved", lambda: {"picture": "mirrored"})
    assert zi.face_missing({"front": 1, "three_quarter_R": 1}) == ["left"]


def test_no_room_record_is_true_handed(monkeypatch):
    monkeypatch.delenv("CRISSA_FACE_SIDE_AS_SEEN", raising=False)

    def boom():
        raise OSError("no site.json")
    monkeypatch.setattr(zi, "room_saved", boom)
    assert zi.face_side_bins() == ("_R", "_L")


def test_the_kill(monkeypatch):
    monkeypatch.setenv("CRISSA_FACE_SIDE_AS_SEEN", "0")
    assert zi.face_missing({"front": 1, "three_quarter_R": 1}) == ["left"], "round 27's reading"


def test_the_engine_order_the_rule_rests_on():
    src = (ROOT / "modules/presence/crissa_presence/face_engine.py").read_text()
    assert "return [mid(*EYE_R), mid(*EYE_L), px(NOSE), px(MOUTH_R), px(MOUTH_L)]" in src
    yaw = (ROOT / "modules/presence/crissa_presence/enroll_panel/face_yaw.py").read_text()
    assert 'side = "_L" if ratio < 0 else "_R"' in yaw


def test_a_turned_photo_is_heard():
    for rel in ("modules/id/crissa_id/house_enroll/sectioned_session.py",
                "modules/presence/crissa_presence/enroll_panel/sectioned_session.py"):
        src = (ROOT / rel).read_text()
        assert 'if pose == "profile" and photo_beep_on():' in src
        assert 'cues.success_ding(site)\n                    cues.speak_loud(site, "Got it.", pause=0.15)' in src
