"""Round 41 (.101 round 40 F4, the User: "Crissa came on so she was listening and responding with Lila listen loop
still"): while any walk runs (<home>/init-run.json running: Setup, an Add/Edit IDs run, a Voice ID again) the ear bridge
holds her answers - the turns heard are dropped, never answered later - and lets go the moment the walk ends.
Kill CRISSA_WALK_HOLDS_ANSWERS=0."""
import json

from test_round17_talk_20261013 import _loop, eb  # noqa: F401  (the main-loop stub harness)


def test_the_hold_reads_the_walk(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "HOME", tmp_path)
    monkeypatch.delenv("CRISSA_WALK_HOLDS_ANSWERS", raising=False)
    assert eb.walk_holds_answers() is False
    (tmp_path / "init-run.json").write_text(json.dumps({"status": "running", "ids": {"who": "new"}}))
    assert eb.walk_holds_answers() is True
    monkeypatch.setenv("CRISSA_WALK_HOLDS_ANSWERS", "0")
    assert eb.walk_holds_answers() is False, "the kill"
    monkeypatch.delenv("CRISSA_WALK_HOLDS_ANSWERS")
    (tmp_path / "init-run.json").write_text(json.dumps({"status": "aborted"}))
    assert eb.walk_holds_answers() is False, "the walk ended: she answers again"


def test_a_line_she_would_answer_is_held_while_a_walk_is_open(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "walk_holds_answers", lambda: False)
    events, sent = _loop(monkeypatch, tmp_path)
    assert sent == ["Let's read something together."], "no walk: she answers (round 17's case)"
    monkeypatch.setattr(eb, "walk_holds_answers", lambda: True)
    events, sent = _loop(monkeypatch, tmp_path)
    assert sent == [], "a walk is open: held"
    holds = [e for e in events if e.get("event") == "walk_hold"]
    assert holds and holds[0]["on"] is True and len(holds) == 1, "said once"
