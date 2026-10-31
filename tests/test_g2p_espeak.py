"""WHICH from espeak-ng, not the spelling (2026-09-23).

The letter rules read "beige" as MBP EH EE KG and "symphony" with an MBP and an
FV for its "ph". espeak-ng reads the phonemes. It is behind HOUSE_MOUTH_G2P,
default OFF, because house-speak is exec'd from disk for every line.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _load(engine: str | None, **env):
    env.setdefault("HOUSE_MOUTH_PHONES", "0")      # round 41: the speller is ON by default; this file is the G2P knob's
    env.setdefault("HOUSE_MOUTH_CLASSES_V2", "0")  # round 41: his classes are ON too; this file pins the 19
    old = {k: os.environ.get(k) for k in ("HOUSE_MOUTH_G2P", *env)}
    if engine is None:
        os.environ.pop("HOUSE_MOUTH_G2P", None)
    else:
        os.environ["HOUSE_MOUTH_G2P"] = engine
    os.environ.update(env)
    try:
        import mouth_viseme
        return importlib.reload(mouth_viseme)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(autouse=True)
def _restore():
    yield
    _load(None)


def test_default_is_the_letter_rules_with_the_speller_off():
    mv = _load(None)
    assert mv.G2P_ENGINE == "rules"
    # the spelling reading this landing exists to replace, unchanged by default
    assert mv._word_visemes("beige") == ["MBP", "EH", "EE", "KG"]


def test_parse_the_pangram_words_from_espeak_output():
    mv = _load(None)
    cls = lambda line: mv._collapse([mv.class_of_phone(p) for p in mv.espeak_parse(line)])
    assert cls("b_'eI_Z") == ["MBP", "EH", "SH/CH"]   # eI is one shape: b-eh-sh (User 2026-09-24)
    assert cls("h_j_'u:") == ["H", "Y", "WOO"]
    assert cls("s_'I_m_f_@_n_i") == ["S/Z", "EE", "MBP", "FV", "AH", "TDN", "EE"]
    assert cls("k_w_'i:_n") == ["KG", "WOO", "EE", "TDN"]
    # r-coloured vowels keep the R the lips make
    assert cls("'A@_T_3") == ["AH", "R", "TH", "ER"]
    assert cls("b_I#_f_,o@") == ["MBP", "EE", "FV", "OH", "R"]
    # variant suffixes (t#, O2, @2) strip; the digit that IS a phoneme stays
    assert cls("w_'O2_n_t_I#_d") == ["WOO", "OH", "TDN", "EE", "TDN"]
    assert cls("l_'0_x") == ["L", "OH", "KG"]   # the User chose l-oh-k over the la-ah-k A/B


def test_unknown_token_falls_back_rather_than_guessing():
    mv = _load(None)
    assert mv.espeak_parse("b_'Q!_Z") is None


def test_hand_calls_win_over_espeak():
    mv = _load("espeak")
    assert mv.g2p("okay") == ["AO", "UW", "K", "EH", "IY"]
    assert mv.g2p("variables") == ["V", "EH", "R", "IY", "EH", "B", "L", "S"]


def test_a_missing_espeak_falls_back_to_the_rules(tmp_path, monkeypatch):
    mv = _load("espeak", HOUSE_ESPEAK_DIR=str(tmp_path))
    import crissa_espeak                            # and no installed espeak-ng either (round 40 finds one there)
    monkeypatch.setattr(crissa_espeak, "found", lambda env=None, which=None: "")
    assert mv._word_visemes("beige") == ["MBP", "EH", "EE", "KG"]
    assert mv._ESPEAK_DEAD is True


@pytest.mark.skipif(not (Path.home() / ".local/piper/espeak-ng").exists(),
                    reason="piper's espeak-ng is not on this box")
def test_live_espeak_reads_the_pangram():
    mv = _load("espeak")
    got = dict(zip(["beige", "hue", "symphony", "impressed"],
                   mv.word_viseme_lists("beige hue symphony impressed")))
    assert got["beige"] == ["MBP", "EH", "SH/CH"]   # eI is one shape: b-eh-sh (User 2026-09-24)
    assert got["hue"] == ["H", "OH", "WOO"]   # the User: h-oh-woo (a word exception over espeak's Y)
    assert got["symphony"].count("MBP") == 1 and got["symphony"].count("FV") == 1
    assert got["impressed"].count("MBP") == 1
    assert mv._ESPEAK_DEAD is False


def test_a_hum_is_a_closed_mouth_whatever_espeak_says(monkeypatch):
    """The User 2026-09-26: "tune tosses random ah in there for MM". espeak reads 'hmm' as H AH M and
    spells 'hm' as letters; her lips are shut for the whole hum. WORD_G2P wins over espeak."""
    import importlib, os
    monkeypatch.setenv("HOUSE_MOUTH_G2P", "espeak")
    import mouth_viseme as mv
    mv = importlib.reload(mv)
    for w in ("hmm", "hm", "mm", "Hmm,", "MHM"):
        out = [c for c in mv.text_to_visemes(w) if c != "rest"]
        assert "AH" not in out and "EH" not in out and "SH/CH" not in out, (w, out)
        assert out and set(out) <= {"MBP"}, (w, out)
    monkeypatch.delenv("HOUSE_MOUTH_G2P")
    importlib.reload(mv)
