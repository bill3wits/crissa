"""The counter's whole job is not counting one person twice. These are the cases that actually
went wrong in Crissa on 2026-07-22, encoded so they cannot come back."""
import numpy as np
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
from crissa_presence.bodycount import count_bodies, summarise

def v(seed, jitter=0.0):
    rng = np.random.default_rng(seed)
    e = rng.normal(size=512).astype(np.float32)
    if jitter:
        e = e + rng.normal(size=512).astype(np.float32) * jitter
    return e / np.linalg.norm(e)

USER, WAND = v(1), v(2)
ENR = {"user": [USER], "wanda": [WAND]}

def test_one_person_seen_at_three_stops_is_one_body():
    dets = [("0", USER, None), ("1", USER, None), ("2", USER, None)]
    people, _ = count_bodies(dets, ENR)
    assert len(people) == 1 and people[0].name == "user"

def test_two_real_people_are_two():
    people, _ = count_bodies([("0", USER, None), ("2", WAND, None)], ENR)
    assert len(people) == 2 and sorted(p.name for p in people) == ["user", "wanda"]

def test_near_miss_on_the_same_person_is_not_a_stranger():
    """user at 0.68 from one angle and 0.43 from another is ONE user. This produced a phantom."""
    people, _ = count_bodies([("0", USER, None), ("1", v(1, 0.6), None)], ENR)
    assert len(people) == 1, f"same person counted {len(people)} times"

def test_unmatched_face_not_confirmed_live_is_not_a_body():
    """A face on a monitor matches nobody — exactly like a stranger. It must not count."""
    people, _ = count_bodies([("0", USER, None), ("1", v(99), "screen")], ENR,
                             is_live=lambda ctx: ctx != "screen")
    assert len(people) == 1

def test_unmatched_face_confirmed_live_IS_a_body():
    people, _ = count_bodies([("0", USER, None), ("1", v(99), "real")], ENR,
                             is_live=lambda ctx: ctx != "screen")
    assert len(people) == 2
    assert summarise(people, 4)["unidentified"] == 1

def test_one_stop_cannot_certify_a_room():
    """'I looked at one place and saw nothing, therefore empty' cost five weeks."""
    assert summarise([], stops_covered=1)["certain"] is False

def test_empty_room_is_not_certain_from_nothing():
    assert summarise([], stops_covered=4)["certain"] is False

def test_a_held_memory_is_never_a_body():
    """Nothing here can invent a person: no detections in, no bodies out."""
    people, _ = count_bodies([], ENR)
    assert people == [] and summarise(people, 4)["bodies"] == 0
