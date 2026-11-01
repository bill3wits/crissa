"""Door visitors: a face is skipped as the User only when it scores like him, and a door check never
names a 0.40-0.60 face as him (the User 2026-10-09).

He has ~139 enrolled vectors to a guest's 1-5, so he was the CLOSEST match for most strangers and
`_record_door_visitors` skipped every one of them (`if nm == uid: continue`). The 0.40 profile keep
also published such a face as him at the door; it is for a seat look only.
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
sys.path.insert(0, os.path.dirname(__file__))

from test_visit_lamp import _svc  # noqa: E402


def _recorder(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "crissa_presence.stranger_x.record_event", lambda **kw: calls.append(kw) or {"id": "X1"}
    )
    return calls


def _unmatched(name, sim):
    return {"name": name, "sim": sim, "bbox": [0, 0, 120, 120], "jpeg": b"j", "crop": b"c", "emb": None}


def test_a_stranger_closest_to_the_user_is_logged(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    calls = _recorder(monkeypatch)
    svc._record_door_visitors({"unmatched": [_unmatched("user", 0.22)]}, uid="user")
    assert len(calls) == 1, "closest-is-the-User is not a reason to drop a stranger"
    c = svc._door_skips_pub()
    assert c["logged"] == 1 and c["skipped"] == 0


def test_a_face_that_scores_like_the_user_is_skipped_and_counted(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    calls = _recorder(monkeypatch)
    svc._record_door_visitors(
        {"unmatched": [_unmatched("user", 0.52), _unmatched("user", 0.40), _unmatched("wanda", 0.30)]},
        uid="user",
    )
    assert len(calls) == 1 and calls[0]["named_sim"] == 0.30
    c = svc._door_skips_pub()
    assert c["skipped"] == 2 and c["logged"] == 1
    assert c["last_skip_sim"] == 0.40 and c["skip_min_sim"] == 0.40


def test_the_old_closest_skip_is_one_switch_away(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    calls = _recorder(monkeypatch)
    monkeypatch.setenv("RAIL_DOOR_SKIP_CLOSEST", "1")
    svc._record_door_visitors({"unmatched": [_unmatched("user", 0.10)]}, uid="user")
    assert calls == [] and svc._door_skips_pub()["skipped"] == 1


class _Face:
    """One big face per frame; the matcher is patched, so the embedding is a token."""

    def detect(self, im):
        return [object()]

    def bbox(self, f):
        return [400.0, 200.0, 640.0, 480.0]

    def det_score(self, f):
        return 0.9

    def embed(self, f):
        return np.ones(512, dtype=np.float32)


class _Engine:
    face = _Face()
    enrolled = {}

    def ensure(self):
        return None


def _identify(svc, monkeypatch, sim, source):
    monkeypatch.setattr("crissa_presence.enrollments.match", lambda emb, enrolled, threshold=0.0: ("user", sim))
    monkeypatch.setattr(svc, "note_faceid_hud", lambda **kw: None)
    svc.engine = _Engine()
    ok, jpg = cv2.imencode(".jpg", np.zeros((720, 1280, 3), np.uint8))
    return svc._identify_all_in_jpegs([jpg.tobytes()], source=source)


def test_a_door_check_does_not_name_a_profile_score_as_the_user(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    out = _identify(svc, monkeypatch, 0.47, "door_glance")
    assert out["names"] == {} and out["faces"] == []
    assert len(out["unmatched"]) == 1, "it goes to the visitor recorder, which skips it as the User and counts it"


def test_a_seat_look_still_keeps_the_users_profile(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    out = _identify(svc, monkeypatch, 0.47, "beam_look")
    assert out["names"] == {"user": 0.47}
    assert out["faces"][0].get("pose") == "profile" and out["unmatched"] == []


def test_a_front_match_at_the_door_is_still_the_user(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    out = _identify(svc, monkeypatch, 0.82, "door_glance")
    assert out["names"] == {"user": 0.82} and out["unmatched"] == []


def test_the_door_profile_keep_is_one_switch_away(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setenv("RAIL_DOOR_PROFILE_KEEP", "1")
    out = _identify(svc, monkeypatch, 0.47, "door_glance")
    assert out["names"] == {"user": 0.47}


def test_secure_still_ends_on_an_unnamed_door_face(tmp_path, monkeypatch):
    """The relabel moves a 0.40-0.60 face from named to unmatched; both end Secure."""
    svc = _svc(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(svc, "_door_trip_allowed", lambda: (True, ""))
    monkeypatch.setattr(svc, "_visit_secure_off", lambda **kw: calls.append(kw) or True)
    svc._visit_lamp_on_door_faces({"names": {}, "unmatched": [_unmatched("user", 0.47)]})
    assert calls == [{"reason": "entry_face_unnamed"}]
