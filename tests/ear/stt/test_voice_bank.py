"""The voice bank. The house ran these against its live bank (its people's real prints); the pack has no
such bank, so `bank` below is a synthetic one: two people, five unit prints each, in the live encoder's
family (2026-10-09)."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from crissa_ear.voice_bank import load_voice_bank, match_voice

MOD = Path(__file__).resolve().parents[3] / "modules" / "ear"


@pytest.fixture(autouse=True)
def bank_root(tmp_path, monkeypatch):
    """A synthetic identity tree at HOUSE_ID_ROOT: people/<id>/voice/00n.json, WeSpeaker family, 256-d."""
    rng = np.random.default_rng(7)
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    monkeypatch.setenv("HOUSE_VOICE_ENCODER", "wespeaker")
    for pid in ("user", "second"):
        d = tmp_path / "people" / pid / "voice"
        d.mkdir(parents=True)
        (d.parent / "person.json").write_text(json.dumps({"id": pid, "display": pid.title(), "active": True}))
        base = rng.standard_normal(256).astype(np.float32)
        for i in range(1, 6):
            v = base + 0.05 * rng.standard_normal(256).astype(np.float32)
            v /= np.linalg.norm(v)
            (d / ("%03d.json" % i)).write_text(json.dumps({"person_id": pid, "model": "wespeaker/english_resnet221",
                                                           "dim": 256, "encoding": [float(x) for x in v]}))
    return tmp_path


def test_bank_has_user_second():
    bank = load_voice_bank()
    assert "user" in bank and len(bank["user"]) >= 5
    assert "second" in bank and len(bank["second"]) >= 5

def test_self_match():
    bank = load_voice_bank()
    name, sim = match_voice(bank["user"][0], bank)
    assert name == "user" and sim > 0.9

def test_cross_not_confused():
    bank = load_voice_bank()
    # the user emb should not match second above threshold with random noise
    # Take the dimension from the BANK, not a hardcoded 192. This was written
    # in the ECAPA era; WeSpeaker is 256-d and the literal made the test assert
    # against a shape the live bank no longer uses.
    #
    # Skip rather than crash on an empty bank: sibling tests point HOUSE_ID_ROOT
    # at a tmp_path and something caches it, so under random ordering this can
    # run against no bank at all. That is a test-hygiene bug, not a reason for
    # this test to raise StopIteration.
    if not bank:
        import pytest
        pytest.skip("no voice bank loaded (HOUSE_ID_ROOT leaked from a sibling test)")
    dim = len(next(iter(bank.values()))[0])
    noise = np.random.randn(dim).astype(np.float32)
    noise /= np.linalg.norm(noise)
    name, sim = match_voice(noise, bank, threshold=0.35)
    assert name is None or sim < 0.35


def test_bank_write_allowed_live_user_only(monkeypatch):
    from crissa_ear.voice_bank import bank_write_allowed

    monkeypatch.setenv("HOUSE_USER_ID", "user")
    assert bank_write_allowed("user", "live_promote") is True
    assert bank_write_allowed("second", "live_promote") is False
    assert bank_write_allowed("second", "enroll") is True
    assert bank_write_allowed("s1", "live_promote") is False
    assert bank_write_allowed("s1", "register") is False


def test_serve_rings_bank_health():
    src = (MOD / "crissa_ear" / "server.py").read_text()
    assert "bank_health" in src
    assert "voice_bank_ok" in src


def test_live_print_model_follows_encoder(monkeypatch):
    from crissa_ear.voice_bank import live_print_dim, live_print_model

    monkeypatch.setenv("HOUSE_VOICE_ENCODER", "wespeaker")
    assert "wespeaker" in live_print_model().lower()
    assert live_print_dim() == 256
    monkeypatch.setenv("HOUSE_VOICE_ENCODER", "ecapa")
    assert "ecapa" in live_print_model().lower() or "speechbrain" in live_print_model().lower()
    assert live_print_dim() == 192


def test_promote_skips_wrong_dim(monkeypatch):
    from crissa_ear import voice_bank as vb

    monkeypatch.setattr(vb, "PROMOTE", True)
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    monkeypatch.setenv("HOUSE_VOICE_ENCODER", "wespeaker")
    e = np.ones(192, dtype=np.float32)
    e /= np.linalg.norm(e)
    out = vb.maybe_promote_live_print(
        person_id="user",
        emb=e,
        stamp={"source": "voice_emb", "voice_emb_sim": 0.9, "speech_duration_s": 2.0},
        text="hi",
    )
    assert out and out.get("ok") is False
    assert out.get("skipped") == "encoder_dim_mismatch"


def test_bank_health_empty(monkeypatch, tmp_path):
    from crissa_ear import voice_bank as vb

    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    monkeypatch.setenv("HOUSE_VOICE_ENCODER", "wespeaker")
    vb._EMPTY_WARNED = False
    people = tmp_path / "people" / "user" / "voice"
    people.mkdir(parents=True, exist_ok=True)
    for f in people.glob("*.json"):           # the autouse bank is not this test's: one wrong-family print only
        f.unlink()
    import shutil
    shutil.rmtree(tmp_path / "people" / "second", ignore_errors=True)
    (people / "001.json").write_text(
        '{"person_id":"user","model":"speechbrain/spkrec-ecapa-voxceleb",'
        '"dim":192,"encoding":[0.1,0.2]}'
    )
    h = vb.bank_health()
    assert h["ok"] is False
    assert h["encoder"] == "wespeaker"
    assert h["counts"] == {}


def test_maybe_promote_seals_non_user(monkeypatch):
    from crissa_ear import voice_bank as vb

    monkeypatch.setattr(vb, "PROMOTE", True)
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    e = np.ones(32, dtype=np.float32)
    e /= np.linalg.norm(e)
    out = vb.maybe_promote_live_print(
        person_id="second",
        emb=e,
        stamp={"source": "voice_emb", "voice_emb_sim": 0.9, "speech_duration_s": 2.0},
        text="hi",
    )
    assert out and out.get("ok") is False
    assert out.get("skipped") == "sealed"
