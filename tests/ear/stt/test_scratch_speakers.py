"""S1/S2 scratch voice slots — not occupancy, emptied on named stamp."""
from __future__ import annotations

import numpy as np

from crissa_ear.scratch_speakers import (
    assign_scratch,
    empty_slot,
    is_scratch_id,
    promote_slot_to_named,
)


def _emb(seed: int, dim: int = 32) -> np.ndarray:
    rng = np.random.default_rng(seed)
    e = rng.normal(size=dim).astype(np.float32)
    return e / (np.linalg.norm(e) + 1e-9)


def test_scratch_root_not_people(tmp_path, monkeypatch):
    from crissa_ear.scratch_speakers import assign_scratch, scratch_root

    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    scratch = tmp_path / "scratch"
    monkeypatch.setenv("RAIL_SCRATCH_ROOT", str(scratch))
    st: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    r = assign_scratch(_emb(1), stamp=st, text="guest", root=None)
    assert r["ok"] and r["slot"] == "s1"
    assert (scratch / "s1" / "voice").exists()
    assert list((scratch / "s1" / "voice").glob("*.json"))
    people = tmp_path / "people"
    assert not (people / "s1" / "voice").exists() or not list(
        (people / "s1" / "voice").glob("*.json")
    )
    assert scratch_root() == scratch


def test_is_scratch_id():
    assert is_scratch_id("s1") and is_scratch_id("S2")
    assert not is_scratch_id("visitor") and not is_scratch_id("user")


def test_open_s1_then_same_voice_stays_s1(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    a = _emb(1)
    st: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    r1 = assign_scratch(a, stamp=st, text="hello from a guest", root=tmp_path / "people")
    assert r1["ok"] and r1["action"] == "open" and r1["slot"] == "s1"
    assert st["person_id"] == "s1"
    st2: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    r2 = assign_scratch(a, stamp=st2, text="same guest again", root=tmp_path / "people")
    assert r2["ok"] and r2["action"] == "match" and r2["slot"] == "s1"


def test_second_voice_opens_s2(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    root = tmp_path / "people"
    st: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    assign_scratch(_emb(1), stamp=st, text="one", root=root)
    st2: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    r = assign_scratch(_emb(99), stamp=st2, text="two", root=root)
    assert r["ok"] and r["slot"] == "s2"
    assert st2["person_id"] == "s2"


def test_promote_empties_s1(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    root = tmp_path / "people"
    e = _emb(3)
    st: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    assign_scratch(e, stamp=st, text="later second", root=root)
    assert (root / "s1" / "voice").exists()
    assert any((root / "s1" / "voice").glob("*.json"))
    out = promote_slot_to_named("s1", "second", root=root)
    assert out["ok"] and out["to"] == "second"
    assert list((root / "s1" / "voice").glob("*.json")) == []
    moved = list((root / "second" / "voice").glob("s1_*.json"))
    assert moved
    rec = __import__("json").loads(moved[0].read_text())
    assert rec["person_id"] == "second"
    assert rec["promoted_from"] == "s1"
    # next unknown reuses s1
    st3: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    r3 = assign_scratch(_emb(7), stamp=st3, text="new stranger", root=root)
    assert r3["slot"] == "s1"


def test_named_voice_emb_promotes_matching_s1(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    root = tmp_path / "people"
    e = _emb(5)
    st: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    assign_scratch(e, stamp=st, text="anon", root=root)
    st_n = {
        "source": "voice_emb",
        "person_id": "second",
        "speech_duration_s": 2.0,
        "voice_emb_sim": 0.7,
        # PR D (2026-09-09): a stamp with no User sim fails closed, so the
        # promote case carries one, far from the guest.
        "user_sim": 0.2,
        # Promote guard: the SoT must show the guest today.
        "presence": {"headcount": 1, "people": [{"name": "second"}]},
    }
    r = assign_scratch(e, stamp=st_n, text="it's second", root=root)
    assert r.get("action") == "promoted"
    assert list((root / "s1" / "voice").glob("*.json")) == []
    empty_slot("s1", root=root)


def test_weak_user_does_not_open_s1(tmp_path, monkeypatch):
    """0.344 vs User used to mint S1 (live s1/001.json). Must not."""
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    monkeypatch.setattr(
        "crissa_ear.scratch_speakers._named_best",
        lambda e: ("user", 0.344),
    )
    root = tmp_path / "people"
    st: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    r = assign_scratch(_emb(1), stamp=st, text="Say good job.", root=root)
    assert r.get("ok") is False
    assert r.get("skipped") == "maybe_user"
    voice = root / "s1" / "voice"
    assert not voice.exists() or list(voice.glob("*.json")) == []


def test_maybe_user_holds_on_sole_source(tmp_path, monkeypatch):
    """sole* + vsim 0.34 used to skip maybe_user and mint S1. Must not."""
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    monkeypatch.setattr(
        "crissa_ear.scratch_speakers._named_best",
        lambda e: ("user", 0.344),
    )
    root = tmp_path / "people"
    st: dict = {
        "source": "sole_stale_unlabeled",
        "person_id": None,
        "speech_duration_s": 2.0,
        "voice_emb_sim": 0.34,
    }
    r = assign_scratch(_emb(1), stamp=st, text="Say good job.", root=root)
    assert r.get("ok") is False
    assert r.get("skipped") == "maybe_user"
    voice = root / "s1" / "voice"
    assert not voice.exists() or list(voice.glob("*.json")) == []


def test_far_from_user_still_opens_s1(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    monkeypatch.setattr(
        "crissa_ear.scratch_speakers._named_best",
        lambda e: ("user", 0.11),
    )
    root = tmp_path / "people"
    st: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    r = assign_scratch(_emb(1), stamp=st, text="true stranger", root=root)
    assert r.get("action") == "open" and r.get("slot") == "s1"


def test_no_promote_into_user_bank(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    root = tmp_path / "people"
    e = _emb(5)
    st: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    assign_scratch(e, stamp=st, text="anon", root=root)
    st_n = {
        "source": "voice_emb",
        "person_id": "user",
        "speech_duration_s": 2.0,
        "voice_emb_sim": 0.7,
    }
    r = assign_scratch(e, stamp=st_n, text="it's the User", root=root)
    assert r.get("ok") is False
    assert r.get("skipped") == "no_promote_user"
    assert list((root / "s1" / "voice").glob("*.json"))
    assert list((root / "user" / "voice").glob("s1_*")) == []
    out = promote_slot_to_named("s1", "user", root=root, emb=e)
    assert out.get("ok") is False
    assert out.get("error") == "no_promote_user"


def test_promote_moves_only_matching_prints(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    root = tmp_path / "people"
    match = _emb(5)
    other = _emb(99)
    st: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    assign_scratch(match, stamp=st, text="anon", root=root)
    # Plant an unrelated print in the same folder (the folder-drag bug).
    other_path = root / "s1" / "voice" / "002.json"
    other_path.write_text(
        __import__("json").dumps(
            {
                "person_id": "s1",
                "encoding": [float(x) for x in other.tolist()],
            }
        ),
        encoding="utf-8",
    )
    st_n = {
        "source": "voice_emb",
        "person_id": "second",
        "speech_duration_s": 2.0,
        "voice_emb_sim": 0.7,
        "user_sim": 0.2,   # PR D: no User sim fails closed
        "presence": {"headcount": 1, "people": [{"name": "second"}]},
    }
    r = assign_scratch(match, stamp=st_n, text="it's second", root=root)
    assert r.get("action") == "promoted"
    assert r.get("dropped")
    assert list((root / "s1" / "voice").glob("*.json")) == []
    moved = list((root / "second" / "voice").glob("s1_*.json"))
    assert len(moved) == 1


def _visitor_turn(**over):
    """A turn already stamped visitor by open-bank rank (the 09-02 shape)."""
    st = {
        "source": "voice_emb",
        "person_id": "visitor",
        "speech_duration_s": 2.1,
        "voice_emb_sim": 0.585,
        "is_user": None,
        "user_sim": 0.373,
        "presence": {"headcount": 1, "people": [{"name": "user"}], "visitor_today": []},
    }
    st.update(over)
    return st


def _seed_s1(root, e):
    st: dict = {"source": None, "person_id": None, "speech_duration_s": 2.0}
    assign_scratch(e, stamp=st, text="anon", root=root)
    assert list((root / "s1" / "voice").glob("*.json"))


def _s1_intact(root):
    assert list((root / "s1" / "voice").glob("*.json"))
    assert list((root / "visitor" / "voice").glob("s1_*")) == []


def test_promote_guard_user_alone_no_guest_face(tmp_path, monkeypatch):
    """2026-09-02 02:58: hc=1 [the user], no visitor, turn stamped visitor 0.511 →
    the s1 folder (User's own talk) moved into people/visitor/voice/."""
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    root = tmp_path / "people"
    e = _emb(5)
    _seed_s1(root, e)
    st = _visitor_turn()
    r = assign_scratch(e, stamp=st, text="but it was the one that felt like a man", root=root)
    assert r.get("ok") is False
    assert r.get("skipped") == "no_guest_face"
    assert st["scratch_promote_hold"]["why"] == "no_guest_face"
    _s1_intact(root)


def test_promote_guard_user_voice_by_gate(tmp_path, monkeypatch):
    """09-02 04:18: is_user True (voice_margin) yet pid visitor — moved s2_008/9."""
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    root = tmp_path / "people"
    e = _emb(5)
    _seed_s1(root, e)
    st = _visitor_turn(
        is_user=True,
        user_sim=0.533,
        presence={"headcount": 1, "people": [{"name": "user"}], "visitor_today": ["visitor"]},
    )
    r = assign_scratch(e, stamp=st, text="do you tell the difference", root=root)
    assert r.get("skipped") == "user_voice"
    _s1_intact(root)


def test_promote_guard_near_user_margin(tmp_path, monkeypatch):
    """visitor 0.585 / the user 0.533 is a 0.05 stamp margin, not a bank write."""
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    root = tmp_path / "people"
    e = _emb(5)
    _seed_s1(root, e)
    st = _visitor_turn(
        user_sim=0.533,
        presence={"headcount": 1, "people": [], "visitor_today": ["visitor"]},
    )
    r = assign_scratch(e, stamp=st, text="some difference", root=root)
    assert r.get("skipped") == "near_user"
    assert st["scratch_promote_hold"]["margin"] < 0.15
    _s1_intact(root)


def test_promote_guard_fails_closed_without_a_user_sim(tmp_path, monkeypatch):
    """PR D (2026-09-09): an empty User bank (user_sim None) plus a guest
    face seen today must not move s1 into that guest's folder."""
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    root = tmp_path / "people"
    e = _emb(5)
    _seed_s1(root, e)
    st = _visitor_turn(
        user_sim=None,
        presence={"headcount": 0, "people": [], "visitor_today": [{"person_id": "visitor"}]},
    )
    r = assign_scratch(e, stamp=st, text="hi it's visitor", root=root)
    assert r.get("skipped") == "near_user"
    assert st["scratch_promote_hold"]["note"] == "no_user_sim"
    _s1_intact(root)


def test_promote_allowed_with_guest_face_today(tmp_path, monkeypatch):
    """Real Visitor visit: face today, voice far from the User → prints move."""
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    root = tmp_path / "people"
    e = _emb(5)
    _seed_s1(root, e)
    st = _visitor_turn(
        user_sim=0.21,
        presence={"headcount": 0, "people": [], "visitor_today": [{"person_id": "visitor"}]},
    )
    r = assign_scratch(e, stamp=st, text="hi it's visitor", root=root)
    assert r.get("action") == "promoted"
    assert list((root / "s1" / "voice").glob("*.json")) == []
    assert list((root / "visitor" / "voice").glob("s1_*"))


def test_promote_guard_off_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    import crissa_ear.scratch_speakers as ss

    monkeypatch.setattr(ss, "PROMOTE_GUARD", False)
    root = tmp_path / "people"
    e = _emb(5)
    _seed_s1(root, e)
    r = assign_scratch(e, stamp=_visitor_turn(), text="x", root=root)
    assert r.get("action") == "promoted"
