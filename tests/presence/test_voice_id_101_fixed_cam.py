"""2c (the User 2026-10-11, .101 round 9): on a fixed camera, ONE face that is the User's enrolled face is the User
alone at the seat - presence publishes it as a certain, flagged slot (sole_present for the ear) and as the live face
block (the ear-bridge's "the face decides"). A camera that moves, two faces, a stranger, a weak match, a stale look,
the dark and the house all keep today's answers. Checkpoint docs/checkpoints/voice-id-101-20261011/README.md."""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence import fixed_cam  # noqa: E402
from crissa_presence.service import PresenceService  # noqa: E402


class _FakeCam:
    mode = "dir"
    last_error = None

    def grab(self):
        return None

    def grab_buffer(self, **kw):
        return []


def _jpeg(shift: int = 0, seed: int = 5) -> bytes:
    import cv2
    rng = np.random.default_rng(seed)
    room = (rng.random((360, 1200)) * 255).astype("uint8")
    room = cv2.GaussianBlur(room, (0, 0), 6)
    view = room[:, 200 + shift:200 + shift + 640]
    ok, buf = cv2.imencode(".jpg", view)
    return bytes(buf)


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.delenv("RAIL_CAMERA_FIXED", raising=False)
    monkeypatch.delenv("RAIL_FIXED_CAM_SOLE", raising=False)
    monkeypatch.setattr(fixed_cam, "is_house", lambda: False)
    monkeypatch.setattr(fixed_cam, "kind_path", lambda home=None: tmp_path / "camera-kind.json")
    fixed_cam._LOOK.clear()
    yield
    fixed_cam._LOOK.clear()


def test_2c_which_cameras_are_fixed(monkeypatch):
    assert fixed_cam.is_fixed({"pan_seen": True}) is False             # a pan reported, nothing measured: it moves
    assert fixed_cam.is_fixed({"pan_seen": False}) is True             # never a pan control: one view
    monkeypatch.setenv("RAIL_CAMERA_FIXED", "1")
    assert fixed_cam.is_fixed({"pan_seen": True}) is True
    monkeypatch.setenv("RAIL_CAMERA_FIXED", "0")
    assert fixed_cam.is_fixed({"pan_seen": False}) is False
    monkeypatch.setattr(fixed_cam, "is_house", lambda: True)
    monkeypatch.delenv("RAIL_CAMERA_FIXED")
    assert fixed_cam.sole_on() is False                                 # the house keeps Law 1's door, as before
    monkeypatch.setenv("RAIL_FIXED_CAM_SOLE", "1")
    assert fixed_cam.sole_on() is True


def test_2c_a_pan_that_moves_nothing_is_measured_twice_before_it_counts():
    same_a, same_b = _jpeg(0), _jpeg(0, seed=5)
    door, desk = 450000, 450000 - 90 * 3600
    rec = fixed_cam.note_pan_check(same_a, same_b, door, desk)
    assert rec.get("same") == 1 and "fixed" not in rec                  # once is not proof
    rec = fixed_cam.note_pan_check(same_a, same_b, door, desk)
    assert rec["fixed"] is True and fixed_cam.is_fixed({"pan_seen": True}) is True
    rec = fixed_cam.note_pan_check(same_a, _jpeg(300), door, desk)      # a camera that does move: it moved
    assert rec["fixed"] is False and fixed_cam.is_fixed({"pan_seen": True}) is False
    before = fixed_cam.record()
    assert fixed_cam.note_pan_check(same_a, same_b, door, door - 5 * 3600) == before   # a small pan proves nothing


def test_2c_the_measure_switch(monkeypatch):
    monkeypatch.setenv("RAIL_FIXED_CAM_MEASURE", "0")
    assert fixed_cam.note_pan_check(_jpeg(0), _jpeg(0), 450000, 0) == {}


def test_2c_one_face_the_users_at_the_bar_is_the_user_alone():
    now = time.time()
    fixed_cam.note_look(name="user", sim=0.71, faces=1, owner="user", now=now)
    assert fixed_cam.sole("user", now=now + 1)["sim"] == 0.71
    assert fixed_cam.sole("user", now=now + 1, fixed=False) is None                 # a camera that moves
    assert fixed_cam.sole("user", now=now + 1, on=False) is None                    # the rule off
    assert fixed_cam.sole("user", now=now + fixed_cam.SOLE_MAX_AGE_S + 1) is None   # stale
    fixed_cam.note_look(name="user", sim=0.71, faces=2, owner="user", now=now)
    assert fixed_cam.sole("user", now=now) is None                                  # two faces: not alone
    fixed_cam.note_look(name="user", sim=0.55, faces=1, owner="user", now=now)
    assert fixed_cam.sole("user", now=now) is None                                  # under the live-face bar
    fixed_cam.note_look(name="", sim=0.30, faces=1, owner="user", now=now)
    assert fixed_cam.sole("user", now=now) is None                                  # a stranger


def _svc(monkeypatch, *, pan_seen=False, lights_out=False):
    monkeypatch.setenv("RAIL_OCCUPANCY", "0")
    svc = PresenceService(camera=_FakeCam())
    svc.last_grabber = {"pan_seen": pan_seen}
    monkeypatch.setattr(svc, "_user_id", lambda: "user")
    monkeypatch.setattr(svc, "_lights_from_grabber", lambda: (lights_out, {}))
    return svc


def _build(svc):
    cur = svc.get()
    return svc._presence_from_inventory(frame_age=0.1, camera_open=True, camera_blind=False,
                                        last_event=cur.last_event, count_trust="fact", camera_mode="holding",
                                        door_names=[])


def test_2c_the_fixed_slot_and_the_live_face_block(monkeypatch):
    svc = _svc(monkeypatch)
    fixed_cam.note_look(name="user", sim=0.71, faces=1, owner="user")
    st = _build(svc)
    assert st.headcount == 1 and [p.name for p in st.people] == ["user"]
    p = st.people[0]
    assert p.certain is True and p.sources["fixed_cam_sole"] is True and p.sources.get("last_seen")
    assert not (p.auth or {}).get("authed")                                         # the roster grants no AUTH
    assert st.user_auth is False
    d = st.to_dict()
    face = d["face"]                                                                 # the key the ear reads
    assert face["fixed"] is True and face["locked"] == "user" and face["faces"][0] == {"name": "user", "sim": 0.71}
    assert face["age_s"] is not None and face["age_s"] < 2.0


def test_2c_nothing_changes_on_a_camera_that_moves_or_in_the_dark_or_with_two_faces(monkeypatch):
    svc = _svc(monkeypatch, pan_seen=True)
    fixed_cam.note_look(name="user", sim=0.71, faces=1, owner="user")
    st = _build(svc)
    assert st.headcount == 0 and st.people == [] and not st.faceid.get("fixed")
    svc = _svc(monkeypatch, lights_out=True)
    assert _build(svc).headcount == 0
    svc = _svc(monkeypatch)
    fixed_cam.note_look(name="user", sim=0.71, faces=2, owner="user")
    st = _build(svc)
    assert st.headcount == 0 and st.faceid["locked"] is None and st.faceid["n_faces"] == 2


def test_2c_the_oracle_sends_how_many_faces_and_see_records_it(monkeypatch):
    from crissa_presence import server as srv
    import crissa_presence.enrollments as en
    monkeypatch.setattr(en, "load_enrolled", lambda: {"user": [np.ones(8, dtype=np.float32)]})
    monkeypatch.setattr(en, "match", lambda vec, bank, threshold=0.45: ("user", 0.66))
    import crissa_presence.visit_lamp as vl
    monkeypatch.setattr(vl, "user_id", lambda: "user")

    class _Svc:
        def assert_present(self, names, source=""):
            pass
    code, out = srv.see_route(_Svc(), {"embedding": [0.1] * 8, "source": "oracle", "faces": 1})
    assert code == 200 and out["matched"] is True
    lk = fixed_cam.look()
    assert lk["name"] == "user" and lk["n_faces"] == 1 and lk["sim"] == 0.66
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "modules", "oracle", "crissa_oracle",
                            "service.py"), encoding="utf-8").read()
    assert 'body["faces"] = int(faces)' in src and 'self.post(emb, faces=st["faces"]) if _takes_faces(self.post)' in src
