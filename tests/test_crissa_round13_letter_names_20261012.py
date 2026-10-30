"""Round 13, the .101 lips (2026-10-12, ASK-20261012-0845 item 2): on the lip-sync test line the User "missed the last"
words. The tail's rows were painted (the skipped rows are single rows spread over the line); what was wrong was the
sheet: a pack has no espeak-ng, its letter rules read "p", "b" and "m" as bare consonants, and "every p, b and m."
held her lips shut 0.79 s through "pee, bee" with no open for "em". A lone letter is now said by its name, in a pack."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import mouth_viseme as MV  # noqa: E402


@pytest.fixture(autouse=True)
def _letter_rules(monkeypatch):
    """Round 41: the speller is ON by default (.101 S4); this file pins the letter rules, so it runs with
    HOUSE_MOUTH_PHONES=0 (the speller's kill)."""
    import importlib
    import mouth_viseme
    monkeypatch.setenv("HOUSE_MOUTH_PHONES", "0")
    monkeypatch.setenv("HOUSE_MOUTH_CLASSES_V2", "0")   # and pins the 19 classes: his V2 shapes are pinned in test_mouth_classes_v2_20261021
    importlib.reload(mouth_viseme)
    yield
    monkeypatch.undo()
    importlib.reload(mouth_viseme)


@pytest.fixture
def names(monkeypatch):
    monkeypatch.setenv("HOUSE_MOUTH_LETTER_NAMES", "1")
    return MV


def test_the_test_lines_letters_open_her_mouth(names):
    got = names.word_viseme_lists("Watch my lips meet on every p, b and m.")
    assert got[-4:] == [["MBP", "EE"], ["MBP", "EE"], ["AH", "TDN"], ["EH", "MBP"]]
    assert got[:6] == [["WOO", "AH", "TDN", "SH/CH"], ["MBP", "AH", "EE"], ["L", "EE", "MBP", "S/Z"],
                       ["MBP", "EE", "TDN"], ["AH", "TDN"], ["EH", "FV", "EH", "R", "EE"]], "whole words as before"


def test_a_and_i_stay_words_and_letters_inside_words_are_unchanged(names):
    assert names._word_visemes("a") == ["AH"] and names._word_visemes("i") == ["AH", "EE"]
    assert names._word_visemes("my") == ["MBP", "AH", "EE"], "a stem is never read as a letter name"
    assert names._word_visemes("b's") != names._word_visemes("b"), "only a one-letter word"
    for ch, want in (("o", ["OH", "WOO"]), ("w", ["TDN", "AH", "MBP", "AH", "L", "Y", "WOO"]), ("x", ["EH", "KG", "S/Z"])):
        assert names._word_visemes(ch) == want


def test_letter_names_are_a_packs_and_the_switch_forces_them(monkeypatch):
    import crissa_home
    monkeypatch.delenv("HOUSE_MOUTH_LETTER_NAMES", raising=False)
    for house, want in ((True, ["MBP"]), (False, ["MBP", "EE"])):
        monkeypatch.setattr(MV, "_LETTER_NAMES", {})
        monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None, h=house: h)
        assert MV._word_visemes("p") == want, "the house's sheets do not change"
    monkeypatch.setenv("HOUSE_MOUTH_LETTER_NAMES", "0")
    assert MV._word_visemes("p") == ["MBP"]


def test_the_aligned_clock_reads_the_same_letter_names(names, monkeypatch):
    import mouth_clock
    words = [{"w": "every", "a": 0.0, "b": 0.4}, {"w": "p", "a": 0.45, "b": 0.7}, {"w": "b", "a": 0.75, "b": 1.0}]
    assert mouth_clock.spans_from_words(words, text="every p, b")[-5:] == [
        (0.45, 0.575, "MBP"), (0.575, 0.7, "EE"), (0.7, 0.75, "rest-open"), (0.75, 0.875, "MBP"), (0.875, 1.0, "EE")]
    monkeypatch.setenv("HOUSE_MOUTH_LETTER_NAMES", "0")
    assert mouth_clock.spans_from_words(words, text="every p, b")[-3:] == [
        (0.45, 0.7, "MBP"), (0.7, 0.75, "rest-open"), (0.75, 1.0, "MBP")], "the house's clock as before"
