"""Round 18: the Wall's log (the User 2026-10-13, RESULTS-20261013-1530 request 3, ASK-20261013-1450 15:08: "logs are
not getting names on there before scrolls off. Also, her long ones are not streaming to logs. I want her words to print
live too"; "we need it to stream to the logs before synth if possible").

1. A name before the row scrolls off: the ear names a line by the face at the camera at once when the voice cannot
   (crissa_ear/id_trail.face_label), and the feed draws a row nobody has named yet as "probably <name>" - the person
   last named in this conversation, else the install's only person - never "?" (name_fallback); a later name rewrites
   the same line in place on both Walls ("*" in `replaces`). Kill: CRISSA_LOG_NAME_FALLBACK=0.
2. Her words as they stream, before her voice: the bridge writes each reply as one row while the model streams it
   (crissa_bridge/her_words.py), with the pieces handed to her voice and the cancel record's cut; the feed shows a
   window of it around her voice (her_words_rows), dimming what she has not reached and striking what she never said;
   both Walls grow the one line in place (`rid`). Kill: CRISSA_LOG_LIVE_WORDS=0.
3. The house: none of it reaches the house's feed (prod-guarded, its own bytes), and the house's roll draws its rows
   byte for byte as before.

No model, network, microphone, camera or sound: every service is a stub on 127.0.0.1 or a monkeypatch; the voice is a
stub script that sleeps and prints house-speak's status line.
"""
from __future__ import annotations

import http.server
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "modules" / "ear", ROOT / "lib", ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import crissa_home  # noqa: E402
from _kit import kit, load as load_kit  # noqa: E402
from crissa_bridge import ear_bridge as eb  # noqa: E402
from crissa_bridge import her_words  # noqa: E402
from crissa_ear import id_trail  # noqa: E402

UID = "user"
FEED = "scripts/voiceonly-home-server.py"
GLASS = "display/voiceonly-home.html"
R = importlib.import_module("house_wall_ascii_render")
_F: dict = {}


def feed():
    if "m" not in _F:
        _F["m"] = load_kit(FEED, "vo_feed_r18_wall_log")
    return _F["m"]


@pytest.fixture()
def pack(tmp_path, monkeypatch):
    """A pack's state home in tmp_path, Security ID-only (the default)."""
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(crissa_home, "HOME", tmp_path)
    monkeypatch.setenv("HOUSE_USER_ID", UID)
    for k in ("CRISSA_LOG_NAME_FALLBACK", "CRISSA_LOG_LIVE_WORDS", "CRISSA_SECURITY_DEFAULT_IDONLY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(id_trail, "EAR", tmp_path / "ear")
    monkeypatch.setattr(her_words, "path", lambda: tmp_path / "her-words.json")
    crissa_home._SEC_CACHE.clear()
    her_words.reset()
    yield tmp_path
    her_words.reset()


def _face(name=UID, sim=0.71, age=0.6, n=1, locked=None):
    faces = [{"name": name, "sim": sim}] + [{"name": "other", "sim": 0.8}] * (n - 1)
    return {"headcount": n, "face": {"faces": faces, "locked": locked, "age_s": age, "n_faces": n, "source": "oracle"},
            "people": []}


def _labels(home: Path) -> list[dict]:
    return [json.loads(x) for x in (home / "ear" / "speaker-labels.jsonl").read_text().splitlines()]


# ---- 1: a name before the row scrolls off --------------------------------------------------------------------------

def test_1_the_face_at_the_camera_names_a_line():
    now = 1_800_000_010.0
    lab = id_trail.face_label(_face(), speech_start=now - 2.5, now=now)
    assert lab == {"who": UID, "via": "face", "sim": 0.71, "face_age_s": 0.6}
    assert id_trail.face_label(_face(locked=UID, sim=None), now=now)["who"] == UID       # a lock alone names
    assert id_trail.face_label(_face(n=2), now=now) is None                               # two people: who said it?
    assert id_trail.face_label(_face(sim=0.31), now=now) is None                          # under the profile bar
    assert id_trail.face_label(_face(name="unknown"), now=now) is None
    assert id_trail.face_label(_face(name="crissa"), now=now) is None                       # her own print is nobody
    assert id_trail.face_label(_face(name="s3"), now=now) is None                         # a scratch slot is nobody
    assert id_trail.face_label(_face(age=9.0), speech_start=now - 3.0, now=now) is None   # seen before the line began
    assert id_trail.face_label(_face(age=4.0), speech_start=now - 3.0, now=now)["who"] == UID   # inside the slack
    assert id_trail.face_label({"error": "presence_down"}, now=now) is None
    assert id_trail.face_label({"face": {"faces": [{"name": UID, "sim": 0.7}]}}, now=now) is None   # no age: no look


@pytest.mark.parametrize("voice,want_last", [
    ({"who": UID, "via": "voice", "sim": 0.55}, ("voice", UID)),     # a voice that names someone wins
    ({"who": None, "via": None, "sim": 0.21}, ("face", UID)),         # no voice: the face's name stands
    ({"who": "s2", "via": "scratch", "sim": 0.3}, ("face", UID)),     # a voice no one knows does not take it away
    ({"who": "crissa", "via": "her_voice", "sim": None}, ("her_voice", "crissa")),   # her own voice heard back: not his
])
def test_1_the_worker_writes_the_face_first_then_the_voice(pack, monkeypatch, voice, want_last):
    from crissa_ear import presence as pres
    seen = {}
    monkeypatch.setattr(pres, "fetch_presence", lambda *a, **k: _face())
    monkeypatch.setattr(id_trail, "recognise", lambda pcm, sr, text, presence=None: seen.update(p=presence) or dict(voice))
    t = time.time()
    entry = {"ts": t, "text": "Read me the opening of Moby Dick.", "speech_start_ts": t - 2.4, "speech_end_ts": t - 0.8}
    id_trail.after_publish(entry, b"\x01\x00" * 16000, 16000, "id_only")
    assert id_trail.wait_idle(5.0)
    rows = _labels(pack)
    assert rows[0]["who"] is None and rows[1]["via"] == "face" and rows[1]["who"] == UID
    assert rows[1]["at"] - rows[0]["at"] < 0.2                     # at once, not after the voice print
    assert (rows[-1]["via"], rows[-1]["who"]) == want_last
    assert seen["p"]["face"]["faces"][0]["name"] == UID            # the voice's stamp reads the same look, no 2nd fetch
    F = feed()
    labs = F.speaker_labels(pack / "ear" / "speaker-labels.jsonl")
    got = labs[(round(t, 3), entry["text"])]
    assert (got["via"], got["who"]) == want_last


def test_1_the_kill_switch_is_round_17s_labels(pack, monkeypatch):
    from crissa_ear import presence as pres
    monkeypatch.setenv("CRISSA_LOG_NAME_FALLBACK", "0")
    monkeypatch.setattr(pres, "fetch_presence", lambda *a, **k: pytest.fail("no face read with the kill switch"))
    monkeypatch.setattr(id_trail, "recognise", lambda pcm, sr, text: {"who": None, "via": None, "sim": 0.2})
    t = time.time()
    id_trail.after_publish({"ts": t, "text": "Hello there."}, b"\x01\x00" * 16000, 16000, "id_only")
    assert id_trail.wait_idle(5.0)
    assert [r["who"] for r in _labels(pack)] == [None, None]


def test_1_the_chunk_path_names_by_face_with_the_turn(pack, monkeypatch):
    """The ear's own chunk path (crissa_ear.server._process_listen_array, round 17's ID-only send): the turn goes on the
    ring with no speaker, and the face's label lands a few ms after it - inside the Wall's first read of the row."""
    from crissa_ear import media_gate, presence as pres, server, voice_live
    from crissa_ear.config import Config
    from crissa_ear.pipeline import Transcript
    monkeypatch.setenv("CRISSA_LAPTOP_SECURITY_OFF", "0")   # ID-only (round 28: a laptop with no choice saved is Off)
    monkeypatch.setattr(media_gate, "_state", media_gate.MediaGateState())
    monkeypatch.setattr(voice_live, "kick", lambda pcm, sr: pytest.fail("no print beside the decode in ID-only"))
    monkeypatch.setattr(server, "fetch_presence", lambda *a, **k: {"headcount": 1, "people": []})
    monkeypatch.setattr(server, "_maybe_post_sound", lambda st: None)
    monkeypatch.setattr(server, "stamp_turn", lambda text, presence, **k: {"person_id": None, "source": "no_emb"})
    monkeypatch.setattr(pres, "fetch_presence", lambda *a, **k: _face(age=0.3))
    gate = threading.Event()
    monkeypatch.setattr(id_trail, "recognise", lambda *a, **k: gate.wait(2.0) and {"who": None} or {"who": None})

    class _Tr:
        def transcribe_array(self, wav, sr):
            return Transcript(text="Chapter One.", confidence=0.9, engine="stub", duration_s=len(wav) / sr,
                              rms_db=-28.0)
    monkeypatch.setattr(server, "get_transcriber", lambda cfg=None: _Tr())
    H = server.make_handler(Config())
    h = H.__new__(H)
    d = h._process_listen_array(np.zeros(16000, np.float32) + 0.01, 16000, via="ear")
    assert d["person_id"] is None and d["stamp"]["source"] == "id_trailing"
    t_end = time.time() + 2.0
    while time.time() < t_end and len(_labels(pack)) < 2:
        time.sleep(0.005)
    rows = _labels(pack)
    gate.set()
    assert id_trail.wait_idle(5.0)
    assert rows[1]["via"] == "face" and rows[1]["who"] == UID and rows[1]["ts"] == round(d["ts"], 3)
    assert rows[1]["at"] - rows[0]["at"] < 0.25                    # while the voice print is still running


T0 = 1_800_000_000.0


def _ear(text, ts, src="id_trailing"):
    return {"who": "stranger", "text": text, "ts": ts, "source": src, "log_user": False}


def test_1_the_feed_draws_probably_name_never_question_mark(pack):
    F = feed()
    chat = [_ear("Hey, Crissa.", T0), _ear("Chapter One.", T0 + 20), _ear("Please.", T0 + 40)]
    labels = {(round(T0, 3), "Hey, Crissa."): {"who": UID, "via": "face"}}
    rows = F.name_fallback(F.label_rows(chat, labels=labels, user=UID), labels=labels, user=UID, mode="id_only",
                           people=[UID, "guest2"])
    assert rows[0]["who"] == UID and rows[0]["label_via"] == "face" and not rows[0].get("guess")
    for r in rows[1:]:                                              # the person last named in this conversation
        assert r["who"] == UID and r["guess"] is True and r["probably"] is True and r["label_via"] == "session"
        assert r["log_user"] is True and r["certain"] is False
        assert ["?", r["text"], round(r["ts"], 3)] in r["replaces"] and ["*", r["text"], round(r["ts"], 3)] in r["replaces"]
    # the first line of a conversation, named only later: the first name after it
    rows = F.name_fallback(F.label_rows([_ear("Hello?", T0 - 5)] + chat[:1], labels=labels, user=UID),
                           labels=labels, user=UID, mode="id_only", people=[UID, "guest2"])
    assert rows[0]["who"] == UID and rows[0]["label_via"] == "session"
    # nobody named yet, and this install knows one person: him
    rows = F.name_fallback(F.label_rows(chat, labels={}, user=UID), labels={}, user=UID, mode="id_only", people=[UID])
    assert all(r["who"] == UID and r["label_via"] == "only_user" for r in rows)
    # nobody named, two people known: "?" stays
    rows = F.name_fallback(F.label_rows(chat, labels={}, user=UID), labels={}, user=UID, mode="id_only",
                           people=[UID, "guest2"])
    assert all(r["who"] == "?" and not r.get("guess") for r in rows)
    # a name older than the window does not carry
    old = {(round(T0 - 400, 3), "Earlier."): {"who": UID, "via": "voice"}}
    rows = F.name_fallback(F.label_rows(chat[1:2], labels=old, user=UID), labels=old, user=UID, mode="id_only",
                           people=[UID, "guest2"])
    assert rows[0]["who"] == "?"


def test_1_no_guess_in_on_or_off_for_her_echo_or_with_the_kill(pack, monkeypatch):
    F = feed()
    chat = F.label_rows([_ear("Chapter One.", T0 + 20)], labels={}, user=UID)
    for mode in ("on", "off"):
        assert F.name_fallback(chat, labels={}, user=UID, mode=mode, people=[UID])[0]["who"] == "?"
    her = [(T0 + 5, F._chat_words("Sure. Chapter One. Call me Ishmael."))]
    assert F.name_fallback(chat, labels={}, user=UID, mode="id_only", people=[UID], her=her)[0]["who"] == "?"
    stamped = [{"who": "stranger", "text": "Who is that?", "ts": T0, "source": "voice_emb"}]   # On's own stamp
    assert F.name_fallback(stamped, labels={}, user=UID, mode="id_only", people=[UID])[0]["who"] == "stranger"
    monkeypatch.setattr(F, "LOG_NAME_FALLBACK", False)
    assert F.name_fallback(chat, labels={}, user=UID, mode="id_only", people=[UID])[0]["who"] == "?"
    lab = {(round(T0 + 20, 3), "Chapter One."): {"who": UID, "via": "voice"}}
    assert ["*", "Chapter One.", round(T0 + 20, 3)] not in F.label_rows(chat, labels=lab, user=UID)[0]["replaces"]
    src = kit(FEED).read_text(encoding="utf-8")
    assert 'LOG_NAME_FALLBACK = os.environ.get("CRISSA_LOG_NAME_FALLBACK", "1")' in src
    assert "chat = name_fallback(chat, her=her_lines(chat, her_rows))" in src


def test_1_a_heard_row_that_takes_a_guessed_rows_place_is_named_too(pack):
    F = feed()
    labels = {(round(T0, 3), "Hey, Crissa."): {"who": UID, "via": "face"}}
    chat = F.label_rows([_ear("Hey, Crissa.", T0), _ear("Keep reading, please.", T0 + 30)], labels=labels, user=UID)
    heard = [{"who": "?", "text": "Keep reading, please. · said over her line: answered after it",
              "said": "Keep reading, please.", "ts": T0 + 30.6, "kind": "heard", "source": "heard_roll"}]
    rows = F.name_fallback(F.one_row_chat(chat, heard), labels=labels, user=UID, mode="id_only", people=[UID, "x"])
    h = [r for r in rows if r.get("kind") == "heard"][0]
    assert h["who"] == UID and h["guess"] is True
    assert ["*", h["text"], round(h["ts"], 3)] in h["replaces"]
    assert ["?", "Keep reading, please.", round(T0 + 30, 3)] in h["replaces"]     # the ear's row it stands for


def test_1_the_tk_roll_rewrites_a_guess_in_place(pack, monkeypatch):
    monkeypatch.setattr(R, "ROLL_DROP_ECHO", True)                  # a pack's roll (set at import from is_house)
    roll = R.Roll(since=0.0)
    guess = {"who": UID, "text": "Chapter One.", "ts": T0, "guess": True, "probably": True, "log_user": True,
             "replaces": [["?", "Chapter One.", T0], ["*", "Chapter One.", T0]]}
    roll.feed([guess], now=T0 + 1)
    assert len(roll.lines) == 1 and roll.lines[0]["guess"] is True
    named = dict(guess, guess=False, label_via="voice")             # a recogniser: the same name
    roll.feed([named], now=T0 + 3)
    assert len(roll.lines) == 1 and roll.lines[0]["guess"] is False and roll.lines[0]["at"] == T0 + 1
    other = {"who": "guest2", "text": "Chapter One.", "ts": T0, "probably": True, "log_user": False,
             "replaces": [["?", "Chapter One.", T0], ["*", "Chapter One.", T0]]}
    roll.feed([other], now=T0 + 5)                                  # a recogniser: someone else - rewritten where it is
    assert len(roll.lines) == 1 and roll.lines[0]["who"] == "guest2" and roll.lines[0]["at"] == T0 + 1
    roll.feed([guess, other], now=T0 + 7)                           # neither comes back as a second line
    assert len(roll.lines) == 1
    # her echo with a guessed name is still her echo
    roll2 = R.Roll(since=0.0)
    roll2.feed([{"who": "crissa", "text": "Call me Ishmael. Some years ago.", "ts": T0},
                {"who": UID, "guess": True, "text": "Call me Ishmael.", "ts": T0 + 2}], now=T0 + 3)
    assert [ln["who"] for ln in roll2.lines] == ["crissa"]


def test_1_the_tk_roll_draws_probably_in_the_speakers_column(pack, monkeypatch):
    drawn = []
    real = R._text
    monkeypatch.setattr(R, "_text", lambda d, xy, s, f, fill, sp=0.0: drawn.append(s) or real(d, xy, s, f, fill, sp))
    roll = R.Roll(since=0.0)
    roll.feed([{"who": UID, "text": "Chapter One.", "ts": T0, "guess": True}], now=T0)
    g = R.Geom(1920, 1080)
    layer = roll.draw(g, now=T0 + 1)
    assert "probably " + UID.upper() in drawn and layer.getbbox() is not None


def test_1_the_browser_glass_rewrites_and_labels_a_guess():
    src = kit(GLASS).read_text(encoding="utf-8")
    assert "function whoLabel(wn, name, guess){" in src and 'm.textContent="probably"' in src
    assert 'rowNodes[ck]=row; rowNodes["*\\u241f"+t]=row;' in src            # "*" finds the line under any name
    assert "if(item.guess && kind===\"speech\") whoLabel(w, w.textContent, true);" in src
    assert "(/^(stranger|unknown|\\?|)$/i.test(who) || item.guess) && herEcho(t)" in src
    assert ".who.guess{color:var(--ink-3)}" in src


# ---- 2: her words as they stream, before her voice -----------------------------------------------------------------

def _hw(pack):
    d = json.loads((pack / "her-words.json").read_text())
    return d["rows"]


def test_2_her_row_grows_by_whole_clauses(pack, monkeypatch):
    monkeypatch.setattr(her_words, "WRITE_S", 0.0)
    her_words.set_cleaner(eb._piece_clean)
    rid = her_words.begin()
    her_words.grow("Sure")
    assert _hw(pack)[0]["text"] == "" and _hw(pack)[0]["rid"] == rid      # half a clause: not yet
    her_words.grow("Sure, I'm Claude Haiku")
    assert _hw(pack)[0]["text"] == "Sure,"                                # a clause, by its comma
    her_words.grow("Sure, I'm Claude Haiku 5.")
    assert _hw(pack)[0]["text"] == "Sure,"                                # "5." may be "5.5" still arriving
    her_words.grow("Sure, I'm Claude Haiku 5.5, made by **Anthropic**. Call")
    assert _hw(pack)[0]["text"] == "Sure, I'm Claude Haiku 5.5, made by Anthropic."
    assert _hw(pack)[0]["state"] == "open" and _hw(pack)[0]["said_n"] is None
    her_words.grow("Sure, I'm Claude Haiku 5.5, made by **Anthropic**. Call me Crissa. ")
    assert _hw(pack)[0]["text"] == "Sure, I'm Claude Haiku 5.5, made by Anthropic. Call me Crissa."
    her_words.done("Sure, I'm Claude Haiku 5.5, made by Anthropic. Call me Crissa.",
                   "Sure, I'm Claude Haiku 5.5, made by Anthropic. Call me Crissa.", False)
    her_words.begin()                                                     # a think block is never her words
    her_words.grow("<think>Plan: greet, then. Answer")
    assert _hw(pack)[-1]["text"] == ""
    her_words.grow("<think>Plan: greet, then. Answer</think>Hello there, friend. ")
    assert _hw(pack)[-1]["text"] == "Hello there, friend."


def test_2_a_growing_row_is_written_at_most_every_write_s_and_flushed_when_spoken(pack):
    her_words.begin(now=T0)
    her_words.grow("One. ", now=T0 + 0.1)
    assert _hw(pack)[0]["text"] == ""                                    # inside WRITE_S of the last write: waits
    her_words.grow("One. T", now=T0 + 0.5)                               # the stream's next token writes what waits
    assert _hw(pack)[0]["text"] == "One."
    her_words.grow("One. Two. ", now=T0 + 0.6)                           # inside WRITE_S: waits
    assert _hw(pack)[0]["text"] == "One."
    her_words.on_air("Two.", now=T0 + 0.65)                              # a piece on the air writes what waits
    row = _hw(pack)[0]
    assert row["text"] == "One. Two." and row["pieces"][0][:2] == [5, 9]


TEXT = ("Call me Ishmael. Some years ago, never mind how long precisely, having little or no money in my purse, "
        "I thought I would sail about a little. It is a way I have of driving off the spleen.")


def _spoken(pieces):
    for p in pieces:
        her_words.on_air(p)
        her_words.off_air()


def test_2_a_cut_marks_what_she_never_said(pack, monkeypatch):
    monkeypatch.setattr(her_words, "WRITE_S", 0.0)
    her_words.begin()
    her_words.grow(TEXT + " ")
    _spoken(["Call me Ishmael."])
    her_words.on_air("Some years ago, never mind how long precisely, having little or no money in my purse,")
    rec = {"said": "Call me Ishmael. Some years ago, never mind how lo", "spoken_chars": 51, "why": "stop"}
    her_words.cut(rec, "stop")
    row = _hw(pack)[0]
    assert row["state"] == "cut" and row["why"] == "stop"
    assert TEXT[:row["said_n"]] == "Call me Ishmael. Some years ago, never mind how"      # the record's own cut
    her_words.grow(TEXT + " And more words that came after the stop. ")
    assert _hw(pack)[0]["text"] == TEXT                                   # nothing grows after a cut
    her_words.done(TEXT, "Call me Ishmael. Some years ago, never mind how long precisely,", True)
    row = _hw(pack)[0]
    assert row["state"] == "cut" and TEXT[:row["said_n"]].endswith("never mind how")      # the cut stands
    # no record (cancels off): the piece on the air by the clock
    her_words.begin(now=T0)
    her_words.grow(TEXT + " ", now=T0)
    her_words.on_air("Call me Ishmael.", now=T0 + 1)
    her_words.off_air(now=T0 + 2)
    her_words.on_air("Some years ago, never mind how long precisely,", now=T0 + 2)
    her_words.cut(None, "barge", now=T0 + 3)
    row = _hw(pack)[-1]
    assert TEXT[:row["said_n"]] == "Call me Ishmael. Some years ago,"   # 1 s of the piece at CHARS_PER_S, a word end


@pytest.mark.parametrize("said,stopped,want", [
    (TEXT, False, None),                                                   # all of it
    (TEXT.split(" It is")[0], False, len(TEXT.split(" It is")[0])),       # a reply she did not finish (budget tail)
    ("", True, 0),                                                         # stopped before her first word
])
def test_2_done_says_what_she_said_of_it(pack, monkeypatch, said, stopped, want):
    monkeypatch.setattr(her_words, "WRITE_S", 0.0)
    her_words.begin()
    her_words.grow(TEXT + " ")
    her_words.done(TEXT, said, stopped)
    row = _hw(pack)[0]
    assert row["said_n"] == want and row["state"] == ("cut" if stopped else "done") and row["text"] == TEXT


def test_2_done_with_other_words_keeps_what_she_said_and_close_marks_an_unsaid_row(pack, monkeypatch):
    monkeypatch.setattr(her_words, "WRITE_S", 0.0)
    her_words.begin()
    her_words.grow("A draft answer. ")
    her_words.done("A draft answer.", "Something else entirely, revised.", False)
    assert _hw(pack)[-1]["text"] == "Something else entirely, revised." and _hw(pack)[-1]["said_n"] is None
    her_words.begin()                                                     # a reply cancelled before her first word
    her_words.grow("Never said. ")
    her_words.close()
    assert _hw(pack)[-1]["said_n"] == 0 and _hw(pack)[-1]["state"] == "cut"
    her_words.begin()                                                     # an empty one goes
    her_words.close()
    assert len(_hw(pack)) == 2


def test_2_a_revise_takes_the_same_row_and_the_kill_writes_nothing(pack, monkeypatch):
    monkeypatch.setattr(her_words, "WRITE_S", 0.0)
    rid = her_words.begin()
    her_words.grow("A draft before his last words. ")
    assert her_words.begin() == rid and _hw(pack)[0]["text"] == ""        # his words came: the same row, replaced
    her_words.grow("The answer to all of it. ")
    assert [r["text"] for r in _hw(pack)] == ["The answer to all of it."]
    her_words.done("The answer to all of it.", "The answer to all of it.", False)
    os.unlink(pack / "her-words.json")
    her_words.reset()
    monkeypatch.setenv("CRISSA_LOG_LIVE_WORDS", "0")
    assert her_words.begin() is None
    her_words.grow("Anything. ")
    her_words.done("Anything.", "Anything.", False)
    assert not (pack / "her-words.json").exists()


def _row(text, state="open", pieces=(), said_n=None, ts=T0, at=None, rid="h1"):
    return {"rid": rid, "ts": ts, "at": at or ts, "text": text, "state": state, "said_n": said_n,
            "pieces": [list(p) for p in pieces], "why": ""}


def _write_rows(pack, rows):
    (pack / "her-words.json").write_text(json.dumps({"v": 1, "rows": rows}))
    return pack / "her-words.json"


LONG = " ".join("Sentence %d of the reading, which goes on for a while." % i for i in range(80))


def test_2_the_feed_shows_a_window_around_her_voice(pack):
    F = feed()
    p = _write_rows(pack, [_row(TEXT)])
    r = F.her_words_rows(now=T0 + 1, path=p)[0]                      # nothing said yet: all of it still to come
    assert r["text"] == TEXT and r["voice_n"] == 0 and r["open"] and r["rid"] == "h1" and r["who"] == "crissa"
    assert r["source"] == "her_words" and "_full" in r
    n1 = len("Call me Ishmael.")
    p = _write_rows(pack, [_row(TEXT, pieces=[(0, n1, T0, T0 + 2), (n1 + 1, 104, T0 + 2, None)])])
    r = F.her_words_rows(now=T0 + 2 + F.HER_LEAD_S + 1.0, path=p)[0]          # 1 s into the second piece
    assert n1 < r["voice_n"] <= n1 + 1 + int(F.HER_CPS) + 1 and r["text"][r["voice_n"] - 1] != " "
    # a reading: the window follows her voice, "..." where text is left out
    v = LONG.rfind(" ", 0, 2000)
    p = _write_rows(pack, [_row(LONG, pieces=[(0, v, T0, T0 + 100)])])
    r = F.her_words_rows(now=T0 + 101, path=p)[0]
    assert r["text"].startswith("… ") and r["text"].endswith(" …")
    assert len(r["text"]) <= F.HER_BACK + F.HER_AHEAD + 8 and r["full_chars"] == len(LONG)
    assert r["text"][:r["voice_n"]].rstrip().endswith(LONG[:v].split()[-1])
    # cut: what she said, then what she never said
    p = _write_rows(pack, [_row(LONG, state="cut", said_n=v)])
    r = F.her_words_rows(now=T0 + 101, path=p)[0]
    assert r["cut"] is True and not r["open"] and "voice_n" not in r
    assert r["text"][:r["said_n"]].rstrip().endswith(LONG[:v].split()[-1])
    assert len(r["text"]) - r["said_n"] <= F.HER_CUT_AHEAD + 4
    # done: its end
    p = _write_rows(pack, [_row(LONG, state="done")])
    r = F.her_words_rows(now=T0 + 101, path=p)[0]
    assert r["text"].startswith("… ") and r["text"].endswith(LONG[-20:]) and "said_n" not in r
    # gone after HEARD_ROLL_S; nothing with the kill switch
    assert F.her_words_rows(now=T0 + 101 + F.HEARD_ROLL_S + 200, path=p) == []


def test_2_the_feed_merges_her_rows_and_drops_the_mouths_copy(pack, monkeypatch):
    F = feed()
    p = _write_rows(pack, [_row(TEXT, ts=T0 + 10)])
    rows = F.her_words_rows(now=T0 + 15, path=p)
    chat = [{"who": UID, "text": "Read me Moby Dick.", "ts": T0 + 5, "source": "voice_emb"},
            {"who": "crissa", "text": "Call me Ishmael.", "ts": T0 + 13, "source": "mouth_air", "live": True},
            {"who": "crissa", "text": "Pulling up the board.", "ts": T0 + 14, "source": "mouth_air"}]
    out = F.her_words_merge(chat, rows)
    assert [(r["who"], r.get("rid")) for r in out] == [(UID, None), ("crissa", "h1"), ("crissa", None)]
    assert out[2]["text"] == "Pulling up the board." and "_full" not in out[1]
    assert F.her_words_merge(chat, []) is chat
    monkeypatch.setattr(F, "LOG_LIVE_WORDS", False)
    assert F.her_words_rows(now=T0 + 15, path=p) == []
    src = kit(FEED).read_text(encoding="utf-8")
    assert 'LOG_LIVE_WORDS = os.environ.get("CRISSA_LOG_LIVE_WORDS", "1")' in src
    assert "chat = her_words_merge(chat, her_rows)" in src


def test_2_the_tk_roll_grows_one_line_holds_the_floor_and_strikes_the_unsaid(pack, monkeypatch):
    monkeypatch.setattr(R, "ROLL_HER_CLASS", True)                  # a pack's roll (set at import from is_house)
    roll = R.Roll(since=0.0)
    base = {"who": "crissa", "rid": "h1", "ts": T0, "kind": "speech", "source": "her_words"}
    roll.feed([dict(base, text="Call me Ishmael.", open=True, voice_n=0)], now=T0 + 1)
    roll.feed([dict(base, text=TEXT, open=True, voice_n=16)], now=T0 + 3)
    assert len(roll.lines) == 1 and roll.lines[0]["text"] == TEXT and roll.lines[0]["at"] == T0 + 3   # holds
    assert roll.lines[0]["cls"] == "her"
    roll.feed([{"who": UID, "text": "Stop.", "ts": T0 + 4, "log_user": True}], now=T0 + 5)
    cut = dict(base, text=TEXT, open=False, said_n=47, cut=True)
    roll.feed([cut], now=T0 + 6)
    assert [ln.get("rid") for ln in roll.lines] == ["h1", None] and roll.lines[0]["at"] == T0 + 6
    words = R._roll_words(TEXT, roll.lines[0])
    assert words[-1] == ("(not said)", "mark") and ("Ishmael.", "said") in words
    assert {st for w, st in words if w != "(not said)" and TEXT.index(w) >= 47} == {"unsaid"}
    assert {st for w, st in R._roll_words(TEXT, dict(open=True, voice_n=16))[3:]} == {"ahead"}
    g = R.Geom(1920, 1080)
    assert roll.draw(g, now=T0 + 7).getbbox() is not None
    # an open line stays at the floor however long she reads; a closed one climbs and, gone, never comes back
    roll.feed([dict(base, rid="h2", text=LONG[:300], open=True, voice_n=10, ts=T0 + 10)], now=T0 + 10)
    roll.feed([dict(base, rid="h2", text=LONG[:300], open=True, voice_n=200, ts=T0 + 10)], now=T0 + 200)
    assert [ln["rid"] for ln in roll.lines if ln.get("rid")] == ["h2"]
    roll.feed([dict(base, rid="h2", text=LONG[:300], open=False, ts=T0 + 10)], now=T0 + 210)
    roll.feed([], now=T0 + 210 + R.ROLL_CROSS_S * 1.3)
    roll.feed([dict(base, rid="h2", text=LONG[:300], open=False, ts=T0 + 10)], now=T0 + 290)
    assert not roll.lines and "h2" in roll.rids_gone


GLASS_JS = r"""
function El(tag){ this.tag=tag; this.children=[]; this.className=""; this._t=""; var self=this;
  this.classList={toggle:function(c,on){ var a=self.className.split(" ").filter(Boolean).filter(function(x){return x!==c;});
    if(on) a.push(c); self.className=a.join(" "); }}; }
Object.defineProperty(El.prototype,"firstChild",{get:function(){ return this.children[0]||null; }});
Object.defineProperty(El.prototype,"lastChild",{get:function(){ return this.children[this.children.length-1]||null; }});
Object.defineProperty(El.prototype,"textContent",{get:function(){ return this.tag==="#text"?this._t:
  this.children.map(function(c){return c.textContent;}).join("")+(this.children.length?"":this._t); },
  set:function(v){ this.children=[]; this._t=String(v); }});
El.prototype.appendChild=function(c){ this.children.push(c); return c; };
El.prototype.removeChild=function(c){ this.children.splice(this.children.indexOf(c),1); return c; };
var document={createElement:function(t){ return new El(t); },
              createTextNode:function(s){ var e=new El("#text"); e._t=String(s); return e; }};
"""


def _fn(src: str, name: str) -> str:
    i = src.index("function %s(" % name)
    depth, j = 0, src.index("{", i)
    for k in range(j, len(src)):
        depth += {"{": 1, "}": -1}.get(src[k], 0)
        if depth == 0:
            return src[i:k + 1]
    raise AssertionError(name)


def test_2_the_browser_glass_grows_her_row_in_place():
    src = kit(GLASS).read_text(encoding="utf-8")
    assert "ded.forEach(function(item){ if(item.rid && chat.indexOf(item)<0) ridRow(item); });" in src   # every read
    assert "if(item.rid){ ridRow(item); return; }" in src and "if(last && !c.rid && !last.rid){" in src
    assert "holdOpenRow();" in src and "function holdOpenRow(){" in src
    assert ".said .unsaid{color:var(--ink-3);text-decoration:line-through" in src
    node = shutil.which("node") or str(Path.home() / ".local" / "bin" / "node")
    if not Path(node).exists():
        pytest.skip("no node to run the glass's own functions")
    js = GLASS_JS + _fn(src, "ridSaid") + "\n" + _fn(src, "whoLabel") + r"""
var out={};
function said(item){ var sd=new El("div"); ridSaid(sd, item);
  return sd.children.map(function(c){ return [c.tag==="#text"?"":c.className, c.textContent]; }); }
out.open=said({text:"Call me Ishmael. Some years", open:true, voice_n:16});
out.cut=said({text:"Call me Ishmael. Some years", open:false, said_n:16});
out.done=said({text:"Call me Ishmael.", open:false});
var w=new El("div"); whoLabel(w, "user", true); out.guess=[w.className, w.textContent];
whoLabel(w, "user", false); out.named=[w.className, w.textContent];
console.log(JSON.stringify(out));
"""
    r = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["open"] == [["", "Call me Ishmael."], ["ahead", " Some years"]]
    assert out["cut"] == [["", "Call me Ishmael."], ["unsaid", " Some years"], ["notsaid", " (not said)"]]
    assert out["done"] == [["", "Call me Ishmael."]]
    assert out["guess"] == ["guess", "probablyuser"] and out["named"] == ["", "user"]


class _Stream(http.server.BaseHTTPRequestHandler):
    text = ""

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for i in range(0, len(self.text), 10):
                self.wfile.write(("data: %s\n\n" % json.dumps({"choices": [{"delta": {"content": self.text[i:i + 10]},
                                                                             "finish_reason": None}]})).encode())
                self.wfile.flush()
                time.sleep(0.03)
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


VOICE = r'''import sys, time, json, os
a = sys.argv; text = open(a[a.index("--text-file") + 1]).read()
with open(os.environ["R18_VOICE_LOG"], "a") as f:
    f.write(json.dumps({"start": time.time(), "text": text}) + "\n")
time.sleep(0.3 + len(text) / 100.0)
print("ok pocket-stream")
'''


def test_2_the_real_dispatch_prints_her_words_before_her_voice_and_marks_the_cut(pack, monkeypatch):
    """dispatch() as it runs: the model's stream (a stub on 127.0.0.1), the real speak_blocking (house-speak is a stub
    script that sleeps and prints its status line), a stop in the middle of her second piece (abort_mouth, the cancel
    record). Her row has words on the log before her voice gets its first piece, grows as the stream comes, and after
    the stop says what she never said."""
    answer = ("Sure, here it is. " + TEXT + " Whenever I find myself growing grim about the mouth, I account it high "
              "time to get to sea as soon as I can. ")
    H = type("H", (_Stream,), {"text": answer})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/v1/chat/completions" % srv.server_address[1]
    voice = pack / "voice.py"
    voice.write_text(VOICE)
    vlog = pack / "voice.jsonl"
    monkeypatch.setenv("R18_VOICE_LOG", str(vlog))
    seen: list = []
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            try:
                rows = json.loads((pack / "her-words.json").read_text())["rows"]
                seen.append((time.time(), json.loads(json.dumps(rows[-1]))))
            except (OSError, ValueError, IndexError):
                pass
            if vlog.exists() and len(vlog.read_text().splitlines()) >= 2 and not stop.is_set():
                time.sleep(0.4)
                eb.abort_mouth("stop")                         # "Wait one second." in her second piece
                stop.set()
            time.sleep(0.005)
    mem = eb.ConvoMemory(turns=4, idle_s=900, msg_chars=600)
    # her first clause waits for his quiet before her voice gets it (SPEAK_QUIET_SEC, 2 s; 0.3 s here)
    for k, v in {"HOUSE_SPEAK": voice, "TTS_GATE": pack / "tts-gate.json", "COSY_HEALTH": "",
                 "COSY_ABORT": "http://127.0.0.1:9/v1/stop",
                 "_wait_speak_quiet": lambda token, **kw: time.sleep(0.3) or True,
                 "_get": lambda u, timeout=1.5: {"headcount": 1, "people": []},
                 "authorize": lambda stamp, presence: "bau", "evaluate_turn": lambda *x, **kw: {"user_voice": True},
                 "_gateway": lambda p: {"profile": "crissa", "url": url, "key": "", "model": "stub-model",
                                        "voice": "crissa", "session_header": ""},
                 "_post_json": lambda *x, **kw: {}, "_memory_append": lambda *x, **kw: None,
                 "presence_facts_line": lambda *x, **kw: "", "_today_memory_bullets": lambda: "",
                 "claim_checked": lambda content, **kw: content, "_CONVO": mem, "HISTORY": True,
                 "READ_STREAM": True, "READ_CHUNK": 120, "READ_ENGAGE": 60, "READ_FIRST": 80, "_log": lambda ev: None,
                 "_READING_STATE": {"closed_at": 0.0, "why": ""}, "_pending": None, "_speak_proc": None,
                 "_speaking": False}.items():
        monkeypatch.setattr(eb, k, v)
    monkeypatch.setattr(eb.cancels, "path", lambda: pack / "cancel.json")
    monkeypatch.setattr(eb.cancels, "log_path", lambda: pack / "cancels.jsonl")
    monkeypatch.setattr(eb.heard_roll, "path", lambda: pack / "heard-roll.jsonl")
    monkeypatch.setattr(eb.thinking, "start", lambda *a, **k: None)
    monkeypatch.setattr(eb.thinking, "stop", lambda *a, **k: None)
    th = threading.Thread(target=watch, daemon=True)
    th.start()
    eb.dispatch("Read me the opening of Moby Dick.", {"person_id": UID, "stamp": {}})
    stop.set()
    th.join(2)
    srv.shutdown()
    starts = [json.loads(x) for x in vlog.read_text().splitlines()]
    first_text = min(float(r["at"]) for t, r in seen if r.get("text"))
    assert first_text < starts[0]["start"] - 0.05                 # her words were on the log before her voice
    texts = [r["text"] for t, r in seen if r.get("state") == "open"]
    assert len(set(texts)) >= 3 and all(b.startswith(a[:20]) for a, b in zip(texts, texts[1:]) if a and b)  # grew
    row = json.loads((pack / "her-words.json").read_text())["rows"][-1]
    assert row["state"] == "cut" and row["why"] == "stop"
    n = int(row["said_n"])
    assert 0 < n < len(row["text"]) and row["text"][n:].strip()
    assert row["text"][:n].strip().split()[-1] in starts[1]["text"]           # cut inside the piece on the air
    rec = json.loads((pack / "cancel.json").read_text())
    assert "Cancelled" in rec["summary"]
    F = feed()
    r = F.her_words_rows(path=pack / "her-words.json")[-1]
    assert r["cut"] is True and 0 < r["said_n"] < len(r["text"])


# ---- 3: the house --------------------------------------------------------------------------------------------------

def test_3_the_house_roll_draws_its_rows_as_before(tmp_path):
    """The house's rows carry no rid, guess or "*": its roll draws them byte for byte as at the base commit."""
    git = shutil.which("git")
    if not git:
        pytest.skip("no git")
    r = subprocess.run([git, "-C", str(ROOT), "show", "7e454944f:lib/house_wall_ascii_render.py"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("base commit not reachable")
    import importlib.util
    old_p = tmp_path / "house_wall_ascii_render_base.py"
    old_p.write_text(r.stdout)
    spec = importlib.util.spec_from_file_location("house_wall_ascii_render_base", old_p)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    rows = [{"who": UID, "text": "What time is it?", "ts": T0, "log_user": True},
            {"who": "crissa", "text": "It is a quarter past four, and the light is going.", "ts": T0 + 2,
             "source": "mouth_air"},
            {"who": "stranger", "text": "Is the kettle on?", "ts": T0 + 4},
            {"who": UID, "text": " ".join(["A much longer line that wraps over several rows of the roll"] * 4),
             "ts": T0 + 6, "log_user": True}]
    imgs = []
    for M in (old, R):
        roll = M.Roll(since=0.0)
        roll.feed(rows, now=T0 + 7)
        imgs.append(roll.draw(M.Geom(1920, 1080), now=T0 + 9).tobytes())
    assert imgs[0] == imgs[1]


def test_3_the_house_feed_and_page_keep_their_bytes():
    for rel in (FEED, GLASS):
        house = (ROOT / rel).read_text(encoding="utf-8")
        assert "her_words" not in house and "name_fallback" not in house and "ridRow" not in house
    assert "her_words_rows" in kit(FEED).read_text(encoding="utf-8")
