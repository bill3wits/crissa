"""The mouth spelled by phonemes (round 40, the User 2026-10-21: "espeak id say ... At those timings could handle one
line a frame. Practically free"). HOUSE_MOUTH_PHONES=1: one espeak-ng call per line (weak forms survive), apostrophes
kept, espeak's words paired back to the line's by sound, every phone through config/phoneme-families.json, painted by
mouth_clock.hops_phoned with no holds. espeak-ng is found in this machine's config or on the system, never at a path
in code. Default OFF."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _load(**env):
    env.setdefault("HOUSE_MOUTH_CLASSES_V2", "0")   # round 41: his classes ship ON; this file pins the speller on the 19
    keys = ("HOUSE_MOUTH_PHONES", "HOUSE_MOUTH_G2P", "HOUSE_ESPEAK_DIR", *env)
    old = {k: os.environ.get(k) for k in keys}
    for k in ("HOUSE_MOUTH_PHONES", "HOUSE_MOUTH_G2P", "HOUSE_ESPEAK_DIR"):
        os.environ.pop(k, None)
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
def _back_to_default():
    yield
    _load()


def _have_espeak() -> bool:
    return _load(HOUSE_MOUTH_PHONES="1")._espeak_cmd() is not None


needs_espeak = pytest.mark.skipif(not _have_espeak(), reason="no espeak-ng on this machine")


def _words(mv, text):
    import re
    toks = [t for t in mv.word_tokens(text.lower()) if mv._word_visemes(re.sub(r"[^a-z']+", "", t))]
    return dict(zip(toks, mv.word_viseme_lists(text)))


def test_on_by_default_and_the_kill_puts_the_letter_rules_back():
    # round 41 (.101 S4, the User: "ship it on. No doubt")
    mv = _load()
    assert mv.PHONES_DEFAULT == "1" and mv.PHONES_ON is True and mv.G2P_ENGINE == "espeak-line"
    mv = _load(HOUSE_MOUTH_PHONES="0")
    assert mv.PHONES_ON is False and mv.G2P_ENGINE == "rules"
    assert mv.phones_status() == "phones: off"
    src = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")
    assert "if mouth_viseme.PHONES_ON and self._word_spans:" in src


@needs_espeak
def test_the_apostrophe_stays():
    got = _words(_load(HOUSE_MOUTH_PHONES="1"), "I'll go, we're here.")
    assert got["i'll"] == ["AH", "EE", "L"]                 # aI_l, never "ill" (EE L)
    assert got["we're"][0] == "WOO" and got["we're"] != ["WOO", "ER"]   # w_i@3, never "were"


@needs_espeak
def test_the_weak_forms_survive_a_whole_line_call():
    got = _words(_load(HOUSE_MOUTH_PHONES="1"), "It's a cat, to the end.")
    assert got["a"] == ["AH"]                                # a#: never the letter's name (EH, EE)
    assert got["to"] == ["TDN", "AH"]                        # t@, never "too"
    assert got["the"] == ["TH", "EE"]                        # before a vowel: thee


@needs_espeak
def test_joined_and_expanded_words_pair_back_by_sound():
    mv = _load(HOUSE_MOUTH_PHONES="1")
    got = _words(mv, "It is in the back room.")              # espeak joins "in the" into one word
    assert got["in"] == ["EE", "TDN"] and got["the"] == ["TH", "AH"]
    assert len(mv.word_viseme_lists("Meet me at 9:30, okay?")) == len(_words(mv, "Meet me at 9:30, okay?"))


@needs_espeak
def test_the_hand_calls_still_win():
    got = _words(_load(HOUSE_MOUTH_PHONES="1"), "Okay, let's go.")
    assert got["okay,"] == ["OH", "WOO", "KG", "EH", "EE"]


def test_a_system_espeak_is_found_with_no_house_dir(monkeypatch):
    mv = _load(HOUSE_MOUTH_PHONES="1", HOUSE_ESPEAK_DIR="")
    monkeypatch.setattr(mv, "ESPEAK_DIR", None)
    import crissa_espeak                              # the one lookup (round 40): PATH when there is no house copy
    assert crissa_espeak.found({}, which=lambda n: "/usr/bin/espeak-ng") == "/usr/bin/espeak-ng"
    monkeypatch.setattr(crissa_espeak, "found", lambda env=None, which=None: "/usr/bin/espeak-ng")
    cmd, env = mv._espeak_cmd()
    assert cmd[0] == "/usr/bin/espeak-ng" and "--sep=_" in cmd and "-x" in cmd
    assert mv.phones_status() == "phones: espeak-ng (/usr/bin/espeak-ng)"


def test_no_espeak_says_so(monkeypatch):
    mv = _load(HOUSE_MOUTH_PHONES="1")
    monkeypatch.setattr(mv, "ESPEAK_DIR", None)
    import crissa_espeak
    monkeypatch.setattr(crissa_espeak, "found", lambda env=None, which=None: "")
    assert mv.phones_status() == "phones: no espeak-ng, letter rules"
    assert mv.espeak_line(["hello"]) is None


def test_no_machine_path_in_code():
    src = (ROOT / "lib" / "mouth_viseme.py").read_text(encoding="utf-8")
    assert ".local" not in src and "piper\"" not in src
    import json
    assert json.loads((ROOT / "config" / "house-machine.json").read_text())["espeak_dir"] == "~/.local/piper"


def test_one_map_and_one_lookup():
    src = (ROOT / "lib" / "mouth_viseme.py").read_text(encoding="utf-8")
    at = [i for i in range(len(src)) if src.startswith("ESPEAK_PHONE = ", i) or src.startswith("    ESPEAK_PHONE = ", i)]
    assert at and all('FAMILIES["espeak"]' in src[i:i + 220] for i in at), "every ESPEAK_PHONE reads the one table"
    assert "crissa_espeak.found(" in src and "shutil.which" not in src


def test_the_phone_sheet_keeps_every_shape_with_no_holds():
    _load(HOUSE_MOUTH_PHONES="1")
    import mouth_clock
    importlib.reload(mouth_clock)
    sr, hop = 16000, 1000.0 / 48
    t = np.arange(int(1.2 * sr)) / sr
    audio = np.where((t > 0.2) & (t < 1.0), 0.4 * np.sin(2 * np.pi * 180 * t), 0.0).astype(np.float32)
    word = ["MBP", "AH", "FV", "EE"]                         # four phones in a short word: 4 hops
    span = 4 * hop / 1000.0
    ids = mouth_clock.hops_phoned([word], audio, sr, hop_ms=hop, word_spans=[(0.5, 0.5 + span)],
                                  text="word.", span_hop_ms=hop)
    names = [mouth_clock.class_index.__globals__["CLASSES"][i] for i in ids]
    runs = [c for k, c in enumerate(names) if c not in ("rest", "rest-open") and (k == 0 or names[k - 1] != c)]
    assert runs == word                                       # every phone painted, even at one hop each
    assert mouth_clock.hops_phoned([word], audio, sr, hop_ms=hop, word_spans=None) is None   # no spans: today's
