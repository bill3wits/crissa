"""Gemma one-job verdict normalize → add|sub|null."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.gemma_event_loop import normalize_verdict, _parse_event_json


def test_action_add():
    v = normalize_verdict({"action": "add", "n": 1, "note": "in"})
    assert v["action"] == "add"
    assert v["entered"] == 1 and v["left"] == 0


def test_action_sub():
    v = normalize_verdict({"action": "sub", "n": 1})
    assert v["action"] == "sub"
    assert v["entered"] == 0 and v["left"] == 1


def test_action_null():
    v = normalize_verdict({"action": "null"})
    assert v["action"] == "null"
    assert v["entered"] == 0 and v["left"] == 0


def test_legacy_entered():
    v = normalize_verdict({"entered": 1, "left": 0, "note": "legacy"})
    assert v["action"] == "add"
    assert v["entered"] == 1


def test_legacy_left():
    v = normalize_verdict({"entered": 0, "left": 1})
    assert v["action"] == "sub"


def test_simultaneous_to_null():
    v = normalize_verdict({"entered": 1, "left": 1})
    assert v["action"] == "null"
    assert v["need_peek"] is True


def test_parse_json():
    raw = 'Here:\n```json\n{"action":"add","n":1,"note":"crossed in"}\n```'
    p = _parse_event_json(raw)
    v = normalize_verdict(p)
    assert v["action"] == "add"
