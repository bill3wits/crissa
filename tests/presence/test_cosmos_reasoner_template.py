"""Cosmos oracle must follow NVIDIA reasoner template — not the JSON-clerk path."""

from __future__ import annotations

import json
from pathlib import Path

import crissa_presence.oracle_bench as ob


def test_build_sample_is_nvidia_reasoner_shape(tmp_path: Path) -> None:
    img = tmp_path / "door.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")  # minimal jpeg-ish
    sample = ob.build_cosmos_reasoner_sample(img, name="oracle_now")
    assert sample["model_mode"] == "reasoner"
    assert sample["name"] == "oracle_now"
    assert "vision_path" in sample
    assert Path(sample["vision_path"]).name == "door.jpg"
    assert sample["max_new_tokens"] == ob.COSMOS_MAX_NEW_TOKENS
    assert sample["do_sample"] is False
    prompt = sample["prompt"]
    # must NOT be the old clerk prompt that fights offline thinking
    bad = (
        "First character must be {",
        "No thinking",
        "SENSOR_JSON",
        "Return ONE JSON object only",
    )
    for b in bad:
        assert b not in prompt, f"clerk phrase still present: {b!r}"
    # NVIDIA-style physical scene reasoning
    assert "door" in prompt.lower() or "people" in prompt.lower()
    assert "time" in prompt.lower() or "strip" in prompt.lower() or "left" in prompt.lower()


def test_cosmos_result_accepts_think_then_answer() -> None:
    raw = (
        "<think>\nOkay, let's see. Empty doorway maybe.\n</think>\n"
        "The doorway strip shows no people in the FOV; the room beyond is out of view.\n"
    )
    fields = ob.cosmos_result_from_raw(raw)
    assert fields["ok"] is True
    assert "error" not in fields or not fields.get("error")
    assert "doorway" in fields["in_room_and_speaking"].lower() or "REASONER" in fields[
        "in_room_and_speaking"
    ]
    # answer text is post-think
    assert "Okay, let's see" not in (fields.get("answer_text") or "")
    assert "no people" in (fields.get("answer_text") or "").lower() or "out of view" in (
        fields.get("answer_text") or ""
    ).lower()


def test_cosmos_result_parses_trailing_json() -> None:
    raw = (
        "I see one person at the door.\n"
        '{"in_room":{"headcount":1,"people":[{"id":"user","certain":true,"evidence":"face"}],'
        '"unidentified":0,"count_trust":"fact","note":"at door"},'
        '"speaking":{"id":null,"text":"","source":"silence","conf":0.0},"cannot_do":[]}'
    )
    fields = ob.cosmos_result_from_raw(raw)
    assert fields["ok"] is True
    assert fields["parsed"] is not None
    assert fields["in_room"]["headcount"] == 1
    assert fields["in_room"]["people"][0]["id"] == "user"
    assert "IN ROOM" in fields["in_room_and_speaking"]


def test_cosmos_result_empty_is_not_ok() -> None:
    fields = ob.cosmos_result_from_raw("")
    assert fields["ok"] is False
    assert fields.get("error")


def test_old_clerk_prompt_constant_removed() -> None:
    """Regression: ASK_NOW_COSMOS must not reintroduce clerk bans."""
    p = ob.ASK_NOW_COSMOS
    assert "SENSOR_JSON" not in p
    assert "No thinking" not in p
    assert "First character must be" not in p


def test_active_only_parsing_cosmos(monkeypatch) -> None:
    """Isolation: ONLY=cosmos must not invoke code/gemma in the cycle gate."""
    # Re-read ACTIVE logic by exercising the same env parse the module uses at import
    # is already done; verify exported ACTIVE can be set via env before import is hard.
    # Instead assert the cycle gate uses the ACTIVE set membership pattern in source.
    src = Path(ob.__file__).read_text()
    assert 'if "code" in ACTIVE:' in src
    assert 'if "gemma" in ACTIVE:' in src
    assert 'if "cosmos" in ACTIVE:' in src
    # sample builder is what gets written to disk for inference
    sample = ob.build_cosmos_reasoner_sample("/tmp/x.jpg")
    assert json.dumps(sample)  # serializable for sample.json
