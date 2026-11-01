"""Offline sweep using dir frames + enrollments (no live camera required)."""

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / "data" / "frames"
FACES = ROOT / "data" / "enrollments"


@pytest.mark.skipif(not any(FRAMES.glob("*.jpg")), reason="no fixture frames")
def test_dir_sweep_runs():
    os.environ["RAIL_PRESENCE_CAMERA"] = "dir"
    os.environ["RAIL_PRESENCE_FRAMES_DIR"] = str(FRAMES)
    os.environ["RAIL_PRESENCE_FACES"] = str(FACES)
    os.environ["RAIL_PRESENCE_MIN_STOPS"] = "1"
    # Avoid slow VLM in unit test: unmatched faces fail closed
    from crissa_presence.camera import Camera
    from crissa_presence.sweep import SweepEngine
    from crissa_presence.face_engine import FaceEngine

    cam = Camera(mode="dir", frames_dir=FRAMES)
    eng = SweepEngine(camera=cam, face=FaceEngine())
    eng.enrolled = __import__("crissa_presence.enrollments", fromlist=["load_enrolled"]).load_enrolled(
        FACES
    )
    st = eng.run_once()
    d = st.to_dict()
    assert d["schema"] == "room_presence/v1"
    assert d["camera_open"] is True
    assert "guards" in d
    # headcount may be 0 if fixtures are empty room — still valid structure
    assert isinstance(d["headcount"], int)
