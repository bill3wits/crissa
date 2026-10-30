"""TTS-fingerprint veto — prevent Crissa TTS echo from stamping as real people."""
import json
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crissa_ear.stamp import (
    _cosine,
    _load_tts_fingerprints,
    _reset_tts_fp_cache,
    tts_veto_check,
    stamp_turn,
    _TTS_VETO_SIM,
)

# --- Helpers --------------------------------------------------------------------

# THE LIVE ENCODER DECIDES THE FIXTURE (2026-09-20). These tests wrote ECAPA
# prints (speechbrain, dim 192) and had been red since the WeSpeaker flip on
# 2026-08-04: voice_bank._encoder_filter() loads only prints of the LIVE family,
# so every fixture below was correctly ignored and tts_veto_check returned None.
# Six tests, permanently red, in the file that guards the echo veto.
#
# Pinning 256 here would just re-rot on the next flip, so the model string and
# the dimension come from the same helpers the writer uses.
from crissa_ear.voice_bank import live_print_dim, live_print_model

LIVE_DIM = live_print_dim()
LIVE_MODEL = live_print_model()


def _unit_vec(dim=None):
    """Create a unit vector with value 1.0 in the first dimension."""
    v = [0.0] * (LIVE_DIM if dim is None else dim)
    v[0] = 1.0
    return v


def _shifted_vec(orig, shift_idx=1, magnitude=0.3):
    """Create a vector shifted from orig by adding magnitude at shift_idx."""
    v = list(orig)
    v[shift_idx] = magnitude
    # Re-normalize to unit
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


def test_cosine_identical():
    """Cosine of identical unit vectors is 1.0."""
    v = _unit_vec()
    assert _cosine(v, v) == 1.0


def test_cosine_orthogonal():
    """Cosine of orthogonal vectors is ~0.0."""
    v1 = _unit_vec()
    v2 = _unit_vec()
    v1[0] = 0.0
    v1[1] = 1.0
    assert abs(_cosine(v1, v2)) < 1e-12


def test_cosine_negative():
    """Cosine of opposite vectors is -1.0."""
    v = _unit_vec()
    nv = [-x for x in v]
    assert _cosine(v, nv) == -1.0


def test_tts_veto_no_fingerprints(monkeypatch, tmp_path):
    """No fingerprints loaded → no veto."""
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    monkeypatch.setenv("HOUSE_MOUTHS_ROOT", str(tmp_path / "mouths"))
    _reset_tts_fp_cache()
    result = tts_veto_check(_unit_vec())
    assert result is None


def test_tts_veto_below_threshold():
    """Embedding scores below threshold → no veto."""
    _reset_tts_fp_cache()
    # Create temp directory with a Crissa TTS fingerprint
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HOUSE_ID_ROOT"] = tmp
        voice_dir = Path(tmp) / "people" / "crissa_her_tts" / "voice"
        voice_dir.mkdir(parents=True)

        fp = {
            "person_id": "crissa_her_tts",
            "modality": "voice",
            "model": LIVE_MODEL,
            "dim": LIVE_DIM,
            "encoding": _unit_vec(),
        }
        with open(voice_dir / "001.json", "w") as f:
            json.dump(fp, f)

        _reset_tts_fp_cache()
        # Fingerprint is e0; query e1 is orthogonal → sim 0
        query = _unit_vec()
        query = list(query)
        query[0] = 0.0
        query[1] = 1.0
        sim = _cosine(fp["encoding"], query)
        assert sim < _TTS_VETO_SIM, f"sim={sim:.4f} should be below threshold {_TTS_VETO_SIM}"
        result = tts_veto_check(query)
        assert result is None


def test_tts_veto_above_threshold():
    """Embedding scores above threshold → veto fires."""
    _reset_tts_fp_cache()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HOUSE_ID_ROOT"] = tmp
        voice_dir = Path(tmp) / "people" / "crissa_her_tts" / "voice"
        voice_dir.mkdir(parents=True)

        fp = {
            "person_id": "crissa_her_tts",
            "modality": "voice",
            "model": LIVE_MODEL,
            "dim": LIVE_DIM,
            "encoding": _unit_vec(),
        }
        with open(voice_dir / "001.json", "w") as f:
            json.dump(fp, f)

        _reset_tts_fp_cache()
        # Query nearly identical to fingerprint → should score ~1.0
        query = _shifted_vec(_unit_vec(), shift_idx=1, magnitude=0.01)
        result = tts_veto_check(query)
        assert result is not None
        assert result["veto"] is True
        assert result["tts_fp_sim"] > 0.9  # nearly identical
        assert result["tts_fp_count"] == 1


def test_tts_veto_top_sim_bypass():
    """If real-person match is stronger than TTS match → no veto."""
    _reset_tts_fp_cache()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HOUSE_ID_ROOT"] = tmp
        voice_dir = Path(tmp) / "people" / "crissa_her_tts" / "voice"
        voice_dir.mkdir(parents=True)

        fp = {
            "person_id": "crissa_her_tts",
            "modality": "voice",
            "model": LIVE_MODEL,
            "dim": LIVE_DIM,
            "encoding": _unit_vec(),
        }
        with open(voice_dir / "001.json", "w") as f:
            json.dump(fp, f)

        _reset_tts_fp_cache()
        # Query somewhat close to TTS but top_sim is even higher
        query = _shifted_vec(_unit_vec(), shift_idx=1, magnitude=0.2)
        tts_sim = _cosine(fp["encoding"], query)
        # Force top_sim to clearly lead the TTS match (barge-in)
        result = tts_veto_check(query, top_sim=tts_sim + 0.1)
        assert result is None  # veto should not fire


def test_tts_veto_close_human_still_fires():
    """Second-like leak (~0.05 above TTS) must still veto — not a mute."""
    _reset_tts_fp_cache()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HOUSE_ID_ROOT"] = tmp
        voice_dir = Path(tmp) / "people" / "crissa_her_tts" / "voice"
        voice_dir.mkdir(parents=True)

        fp = {
            "person_id": "crissa_her_tts",
            "modality": "voice",
            "model": LIVE_MODEL,
            "dim": LIVE_DIM,
            "encoding": _unit_vec(),
        }
        with open(voice_dir / "001.json", "w") as f:
            json.dump(fp, f)

        _reset_tts_fp_cache()
        query = _shifted_vec(_unit_vec(), shift_idx=1, magnitude=0.2)
        tts_sim = _cosine(fp["encoding"], query)
        result = tts_veto_check(query, top_sim=tts_sim + 0.05)
        assert result is not None
        assert result["veto"] is True


def test_tts_veto_multiple_fingerprints():
    """Best match across multiple fingerprints is used."""
    _reset_tts_fp_cache()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HOUSE_ID_ROOT"] = tmp
        voice_dir = Path(tmp) / "people" / "crissa_her_tts" / "voice"
        voice_dir.mkdir(parents=True)

        # Two fingerprints: one close, one far
        fp1 = {
            "person_id": "crissa_her_tts",
            "modality": "voice",
            "model": LIVE_MODEL,
            "dim": LIVE_DIM,
            "encoding": _unit_vec(),
        }
        with open(voice_dir / "001.json", "w") as f:
            json.dump(fp1, f)

        fp2 = {
            "person_id": "crissa_her_tts",
            "modality": "voice",
            "model": LIVE_MODEL,
            "dim": LIVE_DIM,
            "encoding": _shifted_vec(_unit_vec(), shift_idx=100, magnitude=0.8),
        }
        with open(voice_dir / "002.json", "w") as f:
            json.dump(fp2, f)

        _reset_tts_fp_cache()
        # Query close to fp1
        query = _shifted_vec(_unit_vec(), shift_idx=1, magnitude=0.01)
        result = tts_veto_check(query)
        assert result is not None
        assert result["tts_fp_count"] == 2
        assert result["tts_fp_sim"] > 0.9  # best match from fp1


def test_tts_veto_wrong_person_id_ignored():
    """Fingerprint with wrong person_id is ignored."""
    _reset_tts_fp_cache()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HOUSE_ID_ROOT"] = tmp
        voice_dir = Path(tmp) / "people" / "crissa_her_tts" / "voice"
        voice_dir.mkdir(parents=True)

        fp = {
            "person_id": "some_other_person",  # wrong ID
            "modality": "voice",
            "model": LIVE_MODEL,
            "dim": LIVE_DIM,
            "encoding": _unit_vec(),
        }
        with open(voice_dir / "001.json", "w") as f:
            json.dump(fp, f)

        _reset_tts_fp_cache()
        result = tts_veto_check(_unit_vec())
        assert result is None  # no valid fingerprints loaded


def test_stamp_turn_tts_veto_integration():
    """Full stamp_turn with TTS veto should null out person_id."""
    _reset_tts_fp_cache()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HOUSE_ID_ROOT"] = tmp

        # Set up voice bank with a real person (second)
        second_dir = Path(tmp) / "people" / "second"
        second_dir.mkdir(parents=True)
        with open(second_dir / "person.json", "w") as f:
            json.dump({"id": "second", "display": "Second", "active": True}, f)

        second_voice = second_dir / "voice"
        second_voice.mkdir(parents=True)
        second_emb = _shifted_vec(_unit_vec(), shift_idx=50, magnitude=0.5)
        with open(second_voice / "001.json", "w") as f:
            json.dump({
                "person_id": "second",
                "modality": "voice",
                "model": LIVE_MODEL,
                "dim": LIVE_DIM,
                "encoding": second_emb,
            }, f)

        # Set up Crissa TTS fingerprint (nearly identical to query)
        crissa_dir = Path(tmp) / "people" / "crissa_her_tts" / "voice"
        crissa_dir.mkdir(parents=True)
        crissa_emb = _unit_vec()
        with open(crissa_dir / "001.json", "w") as f:
            json.dump({
                "person_id": "crissa_her_tts",
                "modality": "voice",
                "model": LIVE_MODEL,
                "dim": LIVE_DIM,
                "encoding": crissa_emb,
            }, f)

        _reset_tts_fp_cache()

        # Query that matches Crissa TTS nearly perfectly
        # The voice bank should match "second" but TTS veto should fire
        query = _shifted_vec(_unit_vec(), shift_idx=1, magnitude=0.01)

        presence = {
            "headcount": 1,
            "count_certain": True,
            "count_trust": "fact",
            "people": [{"slot_id": "s0", "name": "user", "certain": True}],
        }

        st = stamp_turn(
            "Voice greeted. Good morning, the User.",
            presence,
            voice_emb=query,
            speech_duration_s=3.0,
        )

        # The TTS veto should have fired
        assert st["source"] == "tts_echo_veto", f"Expected tts_echo_veto, got {st['source']}"
        assert st["person_id"] is None
        assert st["tts_veto"] is not None
        assert st["tts_veto"]["tts_fp_sim"] > 0.9


def test_stamp_turn_tts_veto_without_person_id():
    """Echo with no human stamp still vetoes (empty room)."""
    _reset_tts_fp_cache()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HOUSE_ID_ROOT"] = tmp
        crissa_dir = Path(tmp) / "people" / "crissa_her_tts" / "voice"
        crissa_dir.mkdir(parents=True)
        with open(crissa_dir / "001.json", "w") as f:
            json.dump({
                "person_id": "crissa_her_tts",
                "modality": "voice",
                "model": LIVE_MODEL,
                "dim": LIVE_DIM,
                "encoding": _unit_vec(),
            }, f)
        _reset_tts_fp_cache()
        st = stamp_turn(
            "I'm here.",
            {"headcount": 0, "count_certain": True, "people": []},
            voice_emb=_shifted_vec(_unit_vec(), shift_idx=1, magnitude=0.01),
            speech_duration_s=2.0,
        )
        assert st["source"] == "tts_echo_veto"
        assert st["person_id"] is None
        assert st["tts_veto"] is not None


def test_presence_alone_no_longer_names_the_speaker():
    """REWRITTEN 2026-09-20. This test pinned `sole_present`: one certain
    person in the room => that person is the speaker. That inference was
    deliberately retired — being in the room is not the same as talking, and
    a stamp that names someone on headcount alone is exactly the false
    attribution AUTH Law 1 exists to stop.

    The stamp now answers `is_user_reason: no_emb` and names nobody. The test
    kept asserting the retired behaviour and had been red ever since."""
    _reset_tts_fp_cache()
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"slot_id": "s0", "name": "user", "certain": True}],
    }
    st = stamp_turn("what's the weather", presence)
    assert st["person_id"] is None, "presence must not name a speaker"
    assert st["is_user_reason"] == "no_emb"
    assert st["tts_veto"] is None          # and no echo veto on a clean turn
