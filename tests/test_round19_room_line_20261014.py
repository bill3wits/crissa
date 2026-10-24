"""Your room in one line, two clicks (the User 2026-10-14, .101 round 18): "'now label the main sound source and door
location based on this diagram' should be enough for that screen. I found she fell behind ... Just those two clicks for
that screen so make it short and all up front. If they struggle maybe give one more prompt to clarify camera placement
is Crissa and sound source is for filtering out media"."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_init as zi  # noqa: E402

SAY = "Now label the main sound source and door location based on this diagram."


def _room_io():
    st = {"room": {}}

    def get():
        return dict(st["room"])

    def put(**kw):
        st["room"].update(kw)
        return True
    done = []
    io = zi.CardIO(get, put, lambda retry=False: {}, lambda: done.append(1))
    io.run_id, io.sid = "r", "room"
    return io, st, done


@pytest.fixture
def card(monkeypatch, tmp_path):
    said = []
    monkeypatch.setattr(zi, "SYNC", True)
    monkeypatch.setattr(zi, "host_say", lambda run_id, sid, keys, wanted=None: said.append(list(keys)) or True)
    monkeypatch.setattr(zi, "room_saved", lambda: {"marks": {}, "picture": ""})
    monkeypatch.setattr(zi, "room_save", lambda marks, picture="": tmp_path / "site.json")
    monkeypatch.setattr(zi, "team_end_on", lambda: True)
    monkeypatch.setenv("CRISSA_ROOM_CAMERA", "fixed")
    monkeypatch.delenv("CRISSA_ROOM_ONE_LINE", raising=False)
    io, st, done = _room_io()
    return io, st, done, said


def test_a_fixed_camera_hears_one_line_up_front_and_nothing_per_mark(card):
    io, st, done, said = card
    zi.room_enter(io)
    assert said == [[zi.SAY_PREFIX + SAY]], "one line, his words, no intro, no speakers film"
    assert st["room"]["one_line"] is True and st["room"]["order"] == ["speakers", "door"]
    assert zi.room_act(io, "internal")["ok"]                 # click 1: the sound comes from this computer
    assert st["room"]["now"] == "door" and said == [[zi.SAY_PREFIX + SAY]], "no line for the door"
    assert not done


def test_the_two_clicks_finish_the_card_by_themselves(card):
    io, st, done, said = card
    zi.room_enter(io)
    zi.room_act(io, "hour", {"hour": 3})                     # click 1: the TV at 3
    assert not done and st["room"]["state"] == "marks"
    zi.room_act(io, "hour", {"hour": 9})                     # click 2: the door at 9
    assert st["room"]["marks"]["speakers"] == 3 and st["room"]["marks"]["door"] == 9
    assert done == [1] and st["room"]["state"] == "saved", "no Save key needed"


def test_a_save_first_is_not_saved_twice(card):
    io, st, done, said = card
    zi.room_enter(io)
    zi.room_act(io, "internal")
    st["room"]["state"] = "saved"                            # a Save landed while the last mark showed lit
    st["room"]["changed"] = ["door", "speakers"]
    zi._room_one_done(io, "r")
    assert not done


def test_one_more_line_once_while_a_mark_is_unset(card, monkeypatch):
    io, st, done, said = card
    zi.room_enter(io)
    monkeypatch.setattr(zi, "ROOM_NUDGE_S", 0.01)
    monkeypatch.setattr(zi, "_card_now", lambda rid, key: dict(st["room"]))
    zi.room_act(io, "internal")                              # the sound set, the door not
    zi._room_nudge(io, "r", "room")
    nudge = said[-1][0]
    assert nudge.startswith(zi.SAY_PREFIX) and "Crissa" in nudge and "filter" in nudge and "door" in nudge
    assert "nudge" in st["room"]["said"]
    n = len(said)
    zi.room_act(io, "hour", {"hour": 9})
    zi._room_nudge(io, "r", "room")
    assert len(said) == n, "nothing more once both are set"


def test_no_nudge_timer_when_switched_off_or_inline(card, monkeypatch):
    io, st, done, said = card
    started = []
    monkeypatch.setattr(zi.threading, "Thread", lambda *a, **k: started.append(k.get("name")) or type(
        "T", (), {"start": lambda self: None})())
    zi.room_enter(io)                                        # SYNC: tests call _room_nudge themselves
    monkeypatch.setattr(zi, "SYNC", False)
    monkeypatch.setattr(zi, "_spawn", lambda name, fn, *a: None)
    zi.room_enter(io)
    assert started == ["init-room-nudge"]
    monkeypatch.setattr(zi, "ROOM_NUDGE_S", 0.0)
    zi.room_enter(io)
    assert started == ["init-room-nudge"], "CRISSA_ROOM_NUDGE_S=0: no second line"


def test_a_turning_camera_and_the_kill_switch_keep_round_18s_lines(card, monkeypatch):
    io, st, done, said = card
    monkeypatch.setenv("CRISSA_ROOM_CAMERA", "turns")
    zi.room_enter(io)
    assert said[-1] == [zi.SAY_PREFIX + zi.ROOM_INTRO["many"], "lila-ob-seats"] and not st["room"]["one_line"]
    monkeypatch.setenv("CRISSA_ROOM_CAMERA", "fixed")
    monkeypatch.setenv("CRISSA_ROOM_ONE_LINE", "0")
    io2, st2, done2 = _room_io()
    zi.room_enter(io2)
    assert said[-1] == [zi.SAY_PREFIX + zi.ROOM_INTRO["many"], "lila-ob-sound-hour"]
    zi.room_act(io2, "internal")
    zi.room_act(io2, "hour", {"hour": 9})
    assert not done2, "the old card waits for Save"


def test_the_words_are_his_and_both_walk_copies_carry_them():
    rows = []
    for rel, key in (("config/init-sequence-pack.json", "walk"), ("modules/id/config/init-sequence.json", "steps")):
        d = json.loads((ROOT / rel).read_text())
        rows.append(next(s for s in d[key] if s.get("id") == "room")["one_line"])
    assert rows[0] == rows[1]
    assert rows[0]["say"] == SAY == zi.ROOM_ONE_LINE["say"]
    assert rows[0]["nudge"] == zi.ROOM_ONE_LINE["nudge"]
