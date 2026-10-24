"""Argus names the guest; the code only checks he stayed on the board."""
from crissa_presence import face_lineup as fl


def test_refuses_a_name_that_was_never_on_the_board():
    """Membership is the whole guard — an invented name must not become a body."""
    r = fl._parse('sure. {"who":"gandalf","confidence":"high"}', {"user", "wanda"})
    assert r["ok"] and r["who"] is None and r["reason"] == "off_board"


def test_unknown_is_an_answer_not_a_failure():
    r = fl._parse('{"who":"unknown","confidence":"low"}', {"user", "wanda"})
    assert r["ok"] and r["who"] is None and r["reason"] == "unknown"


def test_names_someone_on_the_board():
    r = fl._parse('the query matches. {"who":"wanda","confidence":"high"}',
                  {"user", "wanda"})
    assert r["who"] == "wanda" and r["reason"] == "named"


def test_confidence_floor(monkeypatch):
    monkeypatch.setattr(fl, "MIN_CONF", "high")
    assert fl.confident_enough({"ok": True, "who": "user", "confidence": "high"})
    assert not fl.confident_enough({"ok": True, "who": "user", "confidence": "medium"})
    assert not fl.confident_enough({"ok": True, "who": None, "confidence": "high"})


def test_never_runs_without_a_choice_to_make(monkeypatch):
    """One enrolled person is not a line-up, it is a leading question."""
    monkeypatch.setattr(fl, "enrolled", lambda: {"user": ["x.jpg"]})
    assert fl.name_face(b"")["reason"] == "too_few_enrolled"
