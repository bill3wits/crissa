"""Turbo tag split: keep for Turbo TTS, strip for mouth / non-Turbo."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from speakable import (  # noqa: E402
    for_mouth,
    for_speech,
    for_turbo_tts,
    strip_bracket_tags,
)


SAMPLE = "Oh, that's hilarious! [laugh] Anyway, how are you?"


def test_turbo_tts_keeps_known_tag():
    tts, known, unknown = for_turbo_tts(SAMPLE)
    assert "[laugh]" in tts
    assert "laugh" in known
    assert unknown == []
    # Must not leave the bare word as the only remnant of the tag.
    assert "hilarious! [laugh] Anyway" in tts or "[laugh]" in tts


def test_mouth_strips_known_tag():
    mouth = for_mouth(SAMPLE)
    assert "[laugh]" not in mouth
    assert "laugh" not in mouth.lower().split()  # whole word gone
    assert "hilarious" in mouth.lower()
    assert "anyway" in mouth.lower()


def test_strip_bracket_tags_reports_unknown():
    clean, known, unknown = strip_bracket_tags("Hi [laugh] and [sneeze] there")
    assert "[laugh]" not in clean and "[sneeze]" not in clean
    assert "laugh" in known
    assert "sneeze" in unknown
    assert "sneeze" not in clean.lower().split()


def test_unknown_tag_left_for_turbo_tts():
    tts, known, unknown = for_turbo_tts("Wait [sneeze] a second")
    assert "[sneeze]" in tts  # not silently dropped
    assert unknown == ["sneeze"]
    assert known == []


def test_non_turbo_for_speech_strips_whole_tag():
    # Default path (Cosy / Audio8 / PA): no brackets, no spoken "laugh"
    out = for_speech(SAMPLE)
    assert "[laugh]" not in out
    assert "laugh" not in out.lower().split()


def test_keep_turbo_tags_flag():
    out = for_speech(SAMPLE, keep_turbo_tags=True)
    assert "[laugh]" in out


def test_chuckle_and_cough():
    line = "Ahem [cough] — funny [chuckle] right?"
    tts, known, unknown = for_turbo_tts(line)
    assert "[cough]" in tts and "[chuckle]" in tts
    assert set(known) == {"cough", "chuckle"}
    mouth = for_mouth(line)
    assert "[cough]" not in mouth and "[chuckle]" not in mouth
    assert "cough" not in mouth.lower().split()
    assert "chuckle" not in mouth.lower().split()
