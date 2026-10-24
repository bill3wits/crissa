"""Round 12 (the User 2026-10-12, .101 round 11): with the Elgato's own health (it reports a pan, the pan is a crop) and
the oracle's look reaching /v1/see, presence publishes the live face block the ear's "the face decides" reads, and the
User alone at the seat. Checkpoint docs/checkpoints/round12-voice-20261012/README.md."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
sys.path.insert(0, os.path.dirname(__file__))

from crissa_presence import fixed_cam  # noqa: E402
from test_voice_id_101_fixed_cam import _FakeCam, _build  # noqa: E402
from crissa_presence.service import PresenceService  # noqa: E402

ELGATO = {"ok": True, "pan_seen": True, "camera_name": "Elgato Facecam 4K", "camera_ptz": "digital",
          "opened_at": 1791806000.0}


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.delenv("RAIL_CAMERA_FIXED", raising=False)
    monkeypatch.delenv("RAIL_FIXED_CAM_SOLE", raising=False)
    monkeypatch.setattr(fixed_cam, "is_house", lambda: False)
    monkeypatch.setattr(fixed_cam, "kind_path", lambda home=None: tmp_path / "camera-kind.json")
    fixed_cam._LOOK.clear()
    fixed_cam._SEEN.clear()
    yield
    fixed_cam._LOOK.clear()


def _svc(monkeypatch, health):
    monkeypatch.setenv("RAIL_OCCUPANCY", "0")
    svc = PresenceService(camera=_FakeCam())
    svc._set_last_grabber(dict(health))
    monkeypatch.setattr(svc, "_user_id", lambda: "user")
    monkeypatch.setattr(svc, "_lights_from_grabber", lambda: (False, {}))
    return svc


def test_the_elgato_with_a_look_is_the_live_face_and_the_user_alone(monkeypatch, tmp_path):
    svc = _svc(monkeypatch, ELGATO)
    assert (tmp_path / "camera-kind.json").is_file()                              # noted: the kind is on disk
    fixed_cam.note_look(name="user", sim=0.67, faces=1, owner="user")
    st = _build(svc)
    face = st.to_dict()["face"]
    assert face["source"] == "fixed_look" and face["fixed"] is True and face["age_s"] < 2.0
    assert face["faces"][0] == {"name": "user", "sim": 0.67} and face["locked"] == "user"
    assert st.headcount == 1 and st.people[0].sources["fixed_cam_sole"] is True


def test_without_a_look_the_block_is_what_it_was(monkeypatch):
    svc = _svc(monkeypatch, ELGATO)
    st = _build(svc)
    assert not st.to_dict()["face"].get("fixed") and st.headcount == 0


def test_a_motor_gimbal_is_not_fixed(monkeypatch):
    svc = _svc(monkeypatch, dict(ELGATO, camera_name="Insta360 Link 2", camera_ptz="motor"))
    fixed_cam.note_look(name="user", sim=0.67, faces=1, owner="user")
    st = _build(svc)
    assert not st.to_dict()["face"].get("fixed") and st.headcount == 0
