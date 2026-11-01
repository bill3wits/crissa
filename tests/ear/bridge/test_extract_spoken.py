"""Unit tests for spoken content extraction (thinking models — never speak CoT)."""

from crissa_bridge.pipeline import extract_spoken_content


def test_extract_prefers_content():
    assert extract_spoken_content({"content": "Hi the User.", "reasoning": "long"}) == "Hi the User."


def test_strip_think_tags_from_content():
    assert extract_spoken_content({"content": "Hi <think>secret plan</think> the User."}) == "Hi the User."


def test_never_speak_reasoning_only():
    """the User: thinking is internal only — empty content means silence, not mine CoT."""
    msg = {
        "content": "",
        "reasoning": "Thinking hard...\nFINAL: Hello the User, I hear you.\nmore",
    }
    assert extract_spoken_content(msg) == ""
    assert extract_spoken_content({"content": "", "reasoning_content": "long"}) == ""


def test_extract_empty():
    assert extract_spoken_content({}) == ""
    assert extract_spoken_content(None) == ""
