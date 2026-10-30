"""Times, money, dates and ports in the mouth cleanup (voice bake-off 2026-10-06).

Before: "3:45 PM" -> "3 a port PM", "$1,284.50" -> "1,284.50", "11/02/2026" -> "11 02 2026".
Display (wall, type, logs) keeps what was written; the mouth gets words.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from speakable import (  # noqa: E402
    for_display,
    for_speech,
    num_words,
    numbers_to_words,
    ordinal_words,
    strip_unspeakable,
    year_words,
)

BAKEOFF = ("Your dentist is on Thursday, October 15th at 3:45 PM. The invoice was $1,284.50, "
           "due 11/02/2026, and the gate code is 7092.")


# --- the reported bug -------------------------------------------------------
def test_bakeoff_line_no_longer_says_a_port():
    out = for_speech(BAKEOFF)
    assert "a port" not in out
    assert "three forty-five p.m." in out
    assert "one thousand two hundred eighty-four dollars and fifty cents" in out
    assert "November second, twenty twenty-six" in out


def test_display_keeps_what_was_written():
    out = strip_unspeakable(BAKEOFF)
    assert "3:45 PM" in out and "$1,284.50" in out and "11/02/2026" in out
    assert for_display(BAKEOFF) == out


# --- times ------------------------------------------------------------------
@pytest.mark.parametrize("text, said", [
    ("at 3:45 PM", "at three forty-five p.m."),
    ("at 3:45pm", "at three forty-five p.m."),
    ("at 9:05 a.m. sharp", "at nine oh five a.m. sharp"),
    ("at 3:00 PM", "at three p.m."),
    ("at 3:00", "at three o'clock"),
    ("at 15:30", "at fifteen thirty"),
    ("at 12:00 AM", "at twelve a.m."),
    ("at 10:05:30", "at ten oh five and thirty seconds"),
])
def test_times(text, said):
    assert for_speech(text) == said


def test_ratio_and_bad_minutes_are_not_times():
    assert "o'clock" not in for_speech("aspect 16:9")
    assert "sixty" not in for_speech("at 3:60")


# --- ports (the old rule still holds) ---------------------------------------
@pytest.mark.parametrize("text", [
    "the voice is on :5050",
    "ops on localhost:8005",
    "ssh 10.0.0.5:22 now",
    "curl 127.0.0.1:5050/health",
])
def test_ports_still_say_a_port(text):
    out = for_speech(text)
    assert "a port" in out
    assert "o'clock" not in out and "oh" not in out.split()


def test_dotted_host_with_two_digit_port_is_not_a_time():
    assert for_speech("ssh 10.0.0.5:22") == "ssh 10.0.0.5 a port"


# --- money ------------------------------------------------------------------
@pytest.mark.parametrize("text, said", [
    ("$1,284.50", "one thousand two hundred eighty-four dollars and fifty cents"),
    ("$1", "one dollar"),
    ("$5 each", "five dollars each"),
    ("$0.99", "ninety-nine cents"),
    ("$0.01", "one cent"),
    ("$12.5", "twelve dollars and fifty cents"),
    ("$2.5M", "two point five million dollars"),
    ("$3k", "three thousand dollars"),
    ("$40 billion", "forty billion dollars"),
    ("£3.50", "three pounds and fifty pence"),
    ("€20", "twenty euros"),
])
def test_money(text, said):
    assert for_speech(text) == said


def test_dollar_sign_is_kept_on_display():
    assert strip_unspeakable("cost $40") == "cost $40"


def test_other_symbols_still_swept():
    assert for_speech("a & b = c") == "a b c"


# --- dates ------------------------------------------------------------------
@pytest.mark.parametrize("text, said", [
    ("due 11/02/2026", "due November second, twenty twenty-six"),
    ("on 1/5/26", "on January fifth, twenty twenty-six"),
    ("on 2026-10-06", "on October sixth, twenty twenty-six"),
    ("on 12/31/1999", "on December thirty-first, nineteen ninety-nine"),
    ("on 3/21/2005", "on March twenty-first, two thousand five"),
])
def test_dates(text, said):
    assert for_speech(text) == said


def test_impossible_dates_are_left_alone():
    assert "Month" not in for_speech("ratio 13/45/2026")
    assert "thirteenth" not in for_speech("13/45/2026")


def test_ids_and_paths_still_dropped_next_to_numbers():
    out = for_speech("Done at 3:45 PM, card t_0e9d996b, file /tmp/x.py")
    assert "three forty-five p.m." in out
    assert "0e9d996b" not in out and "/tmp" not in out


# --- words helpers (used by the Chatterbox trial server) --------------------
def test_num_words():
    assert num_words(0) == "zero"
    assert num_words(21) == "twenty-one"
    assert num_words(100) == "one hundred"
    assert num_words(7092) == "seven thousand ninety-two"
    assert num_words(1_000_001) == "one million one"


def test_ordinals_and_years():
    assert [ordinal_words(n) for n in (1, 2, 3, 5, 12, 20, 21, 30, 101)] == [
        "first", "second", "third", "fifth", "twelfth", "twentieth", "twenty-first",
        "thirtieth", "one hundred first"]
    assert year_words(2026) == "twenty twenty-six"
    assert year_words(2000) == "two thousand"
    assert year_words(1900) == "nineteen hundred"
    assert year_words(1905) == "nineteen oh five"


def test_numbers_to_words_full_line():
    out = numbers_to_words(for_speech(BAKEOFF))
    assert "October fifteenth" in out
    assert "seven thousand ninety-two" in out
    assert not any(c.isdigit() for c in out)


def test_numbers_to_words_leaves_glued_and_dotted_tokens():
    assert numbers_to_words("GPT4 on x86 at 10.0.0.5") == "GPT4 on x86 at 10.0.0.5"
    assert numbers_to_words("40% of 2,500 in 2026") == "forty percent of two thousand five hundred in twenty twenty-six"
