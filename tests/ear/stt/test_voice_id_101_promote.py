"""2b (the User 2026-10-11, .101 round 9): the ear adds a print when it is certain it is the User - the house's
maybe_promote_live_print with the house's guards, on in a pack, for the User Init enrolled (a first name, not "user").
Checkpoint docs/checkpoints/voice-id-101-20261011/README.md."""
import json

import numpy as np
import pytest

from crissa_ear import house_id, settings, voice_bank as vb


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The identity store Init writes: sam is the User (person.json role "user"), two prints; a second person too."""
    rng = np.random.default_rng(11)
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    monkeypatch.setenv("HOUSE_VOICE_ENCODER", "wespeaker")
    monkeypatch.delenv("HOUSE_USER_ID", raising=False)
    monkeypatch.delenv("RAIL_PRIMARY_USER", raising=False)
    house_id._USER_CACHE.clear()
    bases = {}
    for pid, role in (("sam", "user"), ("guest", "regular")):
        d = tmp_path / "people" / pid / "voice"
        d.mkdir(parents=True)
        (d.parent / "person.json").write_text(json.dumps({"id": pid, "display": pid.title(), "role": role,
                                                          "active": True}))
        base = rng.standard_normal(256).astype(np.float32)
        bases[pid] = base / np.linalg.norm(base)
        for i in range(2):
            v = base + 0.6 * rng.standard_normal(256).astype(np.float32)
            v /= np.linalg.norm(v)
            (d / ("ws_enroll_%d_1.json" % i)).write_text(json.dumps(
                {"person_id": pid, "model": "wespeaker/english_resnet221", "dim": 256, "encoding": [float(x) for x in v]}))
    monkeypatch.setattr(vb, "PROMOTE", True)
    return tmp_path, bases


def _turn(base, rng_seed=3, noise=0.5):
    rng = np.random.default_rng(rng_seed)
    v = base + noise * rng.standard_normal(256).astype(np.float32) / 16.0      # base is a unit vector
    return v / np.linalg.norm(v)


def _stamp(sim, **k):
    st = {"source": "voice_emb", "person_id": "sam", "voice_emb_sim": sim, "confidence": 0.55 + 0.4 * sim,
          "speech_duration_s": 3.2, "media_gate": {"promote_ok": True, "phase": "clear_human", "verdict": "human"}}
    st.update(k)
    return st


def test_2b_the_user_is_the_store_user_not_the_generic_one(store):
    assert vb._canonical_user_id() == "sam"
    assert vb.bank_write_allowed("sam", "live_promote") is True
    assert vb.bank_write_allowed("guest", "live_promote") is False         # only the User's bank grows by itself


def test_2b_a_certain_user_turn_adds_a_print(store):
    root, bases = store
    got = vb.maybe_promote_live_print(person_id="sam", emb=_turn(bases["sam"]), stamp=_stamp(0.62), text="what time is it")
    assert got and got["ok"] is True, got
    rec = json.loads((root / "people" / "sam" / "voice" / "001.json").read_text())
    assert rec["source"] == "live_promote" and rec["person_id"] == "sam" and rec["dim"] == 256
    assert len(list((root / "people" / "sam" / "voice").glob("*.json"))) == 3


def test_2b_the_houses_guards_hold(store):
    root, bases = store
    emb = _turn(bases["sam"])
    # media or an unproven room: no print
    got = vb.maybe_promote_live_print(person_id="sam", emb=emb, stamp=_stamp(0.62, media_gate={"promote_ok": False,
                                                                                              "reason": "char"}))
    assert got["ok"] is False and got["skipped"] == "await_clear_human"
    # too short
    got = vb.maybe_promote_live_print(person_id="sam", emb=emb, stamp=_stamp(0.62, speech_duration_s=0.8))
    assert got["ok"] is False and got["skipped"] == "dur"
    # under the emb lock's bar
    got = vb.maybe_promote_live_print(person_id="sam", emb=emb, stamp=_stamp(0.40))
    assert got["ok"] is False and got["skipped"] == "sim"
    # someone else's turn never writes anyone's bank
    got = vb.maybe_promote_live_print(person_id="guest", emb=_turn(bases["guest"]), stamp=_stamp(0.7, person_id="guest"))
    assert got["ok"] is False and got["skipped"] == "sealed"
    assert list((root / "people" / "sam" / "voice").glob("0*.json")) == []


def test_2b_the_turns_own_media_verdict_is_used_never_fed_twice(store, monkeypatch):
    root, bases = store
    import crissa_ear.media_gate as mg
    fed = []
    monkeypatch.setattr(mg, "observe_turn", lambda **k: fed.append(k) or {"promote_ok": True})
    vb.maybe_promote_live_print(person_id="sam", emb=_turn(bases["sam"]), stamp=_stamp(0.62))
    assert fed == []                                    # the server already fed this turn (st["media_gate"])
    st = _stamp(0.62)
    st.pop("media_gate")
    vb.maybe_promote_live_print(person_id="sam", emb=_turn(bases["sam"], rng_seed=9), stamp=st)
    assert len(fed) == 1                                # a stamp without it: fed here, as the house does


def test_2b_off_in_a_pack_by_its_settings():
    """The User 2026-10-16: live prints corrupt the bank - off again (2026-10-11..16 it was on)."""
    env = {}
    settings.apply(env)
    assert env["RAIL_VOICE_BANK_PROMOTE"] == "0"
    env = {"RAIL_VOICE_BANK_PROMOTE": "1"}
    settings.apply(env)
    assert env["RAIL_VOICE_BANK_PROMOTE"] == "1"        # a pinned value wins
