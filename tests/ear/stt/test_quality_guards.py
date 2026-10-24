import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crissa_ear.quality import is_prompt_echo  # noqa: E402

PROMPT = "Crissa. Clear American English conversation."


def test_full_prompt_echo_rejected():
    assert is_prompt_echo(PROMPT, PROMPT)


def test_prompt_tail_echo_rejected():
    assert is_prompt_echo("Clear American English conversation.", PROMPT)


def test_real_speech_passes():
    assert not is_prompt_echo("Voice off, I think. Can I do voice off?", PROMPT)


def test_short_and_empty_pass():
    assert not is_prompt_echo("", PROMPT)
    assert not is_prompt_echo("Crissa", PROMPT)  # short vocative must survive
    assert not is_prompt_echo("hello", None)


def test_name_mention_passes():
    # Mentioning household names inside a real sentence is not an echo
    assert not is_prompt_echo("tell Second the door is open", PROMPT)
