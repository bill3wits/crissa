"""Round 14, the .101 reading (ASK-20261012-1105-from-101 item 2 + addendum 11:25, 2026-10-12).

A Moby-Dick reading was "Cut at 799 of 6667 characters" (house-speak's 800-character voice cap), began only after 45 s
of silence (the bridge waited for the whole answer), and "what book was that?" got another book (a plain chat API
keeps no conversation). Now: a long line is read in pieces of talk.speak.piece_chars with nothing dropped (house-speak,
in a pack; the bridge too), a long answer is read as it arrives, and the owner's last few exchanges ride with the next
question when the endpoint keeps none.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "modules" / "ear", ROOT / "lib"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import crissa_talk as T  # noqa: E402
from crissa_bridge import ear_bridge as eb  # noqa: E402

TEXT = ("Here is the opening of Moby-Dick by Herman Melville. " + "Call me Ishmael. Some years ago, never mind how "
        "long precisely, having little or no money in my purse, and nothing particular to interest me on shore, I "
        "thought I would sail about a little and see the watery part of the world. " * 30).strip()


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


# ---- the dial and the cut -------------------------------------------------------------------------------------------

def test_the_piece_dial_is_a_talk_setting_with_an_env_and_a_sane_range():
    cfg = T.load(env={})
    assert cfg["speak"]["piece_chars"] == 760 and T.piece_size(cfg) == 760
    assert T.ENV[("speak", "piece_chars")] == "CRISSA_SPEAK_PIECE_CHARS"
    assert T.piece_size(T.load(env={"CRISSA_SPEAK_PIECE_CHARS": "5000"})) == T.PIECE_MAX < 800
    assert T.piece_size(T.load(env={"CRISSA_SPEAK_PIECE_CHARS": "50"})) == T.PIECE_MIN
    assert T.piece_size({"speak": {"piece_chars": 0}}) == 0
    shipped = json.loads((ROOT / "config" / "crissa-talk.json").read_text(encoding="utf-8"))
    assert shipped["speak"]["piece_chars"] == 760
    assert T.validate({"speak": {"piece_chars": 600}}) == {"speak": {"piece_chars": 600.0}}


def test_split_pieces_drops_nothing_and_ends_on_sentences():
    ps = T.split_pieces(TEXT, 760)
    assert len(ps) > 5 and all(len(p) <= 760 for p in ps)
    assert _norm(" ".join(ps)) == _norm(TEXT)
    assert all(p.endswith(".") for p in ps)
    assert T.split_pieces("short line.", 760) == ["short line."] and T.split_pieces(TEXT, 0) == [" ".join(TEXT.split())]


def test_a_sentence_longer_than_a_piece_ends_on_a_word():
    long = "word " * 400
    ps = T.split_pieces(long, 300)
    assert all(len(p) <= 300 for p in ps) and _norm(" ".join(ps)) == _norm(long)
    assert all(not p.endswith("wor") for p in ps)


# ---- house-speak: a pack reads a long line in pieces; the house keeps one line ------------------------------------

def _house_speak():
    spec = importlib.util.spec_from_file_location("hs_r14", ROOT / "scripts" / "house-speak.py")
    hs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hs)
    return hs


def test_house_speak_splits_a_long_line_in_a_pack_only(monkeypatch):
    hs = _house_speak()
    monkeypatch.delenv("HOUSE_SPEAK_PIECE", raising=False)
    monkeypatch.setattr(hs, "_HOUSE", False)
    ps = hs.line_pieces(TEXT)
    assert len(ps) > 1 and all(len(p) <= hs.VOICE_TEXT_CAP for p in ps) and _norm(" ".join(ps)) == _norm(TEXT)
    assert hs.line_pieces("A short line.") == ["A short line."]
    monkeypatch.setenv("HOUSE_SPEAK_PIECE", "2/9")             # a piece never splits again
    assert hs.line_pieces(TEXT) == [TEXT]
    monkeypatch.delenv("HOUSE_SPEAK_PIECE")
    monkeypatch.setenv("CRISSA_SPEAK_PIECE_CHARS", "0")        # the dial off: one line, as before
    assert hs.line_pieces(TEXT) == [TEXT]
    monkeypatch.delenv("CRISSA_SPEAK_PIECE_CHARS")
    monkeypatch.setattr(hs, "_HOUSE", True)                    # the house: one line (its 800 cap and note), unchanged
    assert hs.line_pieces(TEXT) == [TEXT]


def test_house_speak_says_each_piece_in_turn_with_the_callers_flags(monkeypatch, tmp_path):
    hs = _house_speak()
    seen = []

    class FakePopen:
        def __init__(self, argv, env=None):
            self.argv = argv
            seen.append((argv, Path(argv[-1]).read_text(encoding="utf-8"), env.get("HOUSE_SPEAK_PIECE")))

        def wait(self):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(hs.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(hs.signal, "signal", lambda *a: None)
    pieces = T.split_pieces(TEXT, 760)
    rc = hs.speak_pieces(pieces, ["--voice", "crissa", "--text-file", "/x/whole.txt", "--not-before", "12.5"])
    assert rc == 0 and len(seen) == len(pieces)
    for i, (argv, said, tag) in enumerate(seen):
        assert argv[1].endswith("house-speak.py") and "--voice" in argv and "--not-before" in argv
        assert "/x/whole.txt" not in argv and argv[-2] == "--text-file"
        assert said == pieces[i] and tag == "%d/%d" % (i + 1, len(pieces))
    assert _norm(" ".join(s for _, s, _ in seen)) == _norm(TEXT)


def test_house_speak_main_hands_a_long_line_to_the_pieces_before_any_mouth():
    src = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")
    main = src.split("\ndef main() -> int:\n")[1]
    i_pieces, i_mouth = main.index("pieces = line_pieces(text)"), main.index("_AIR_TEXT = cap_text(")
    assert i_pieces < i_mouth and "return speak_pieces(pieces, sys.argv[1:])" in main


# ---- the bridge: pieces as it arrives ----------------------------------------------------------------------------

def test_next_reading_piece_waits_for_a_whole_sentence_and_skips_nothing():
    rest = TEXT
    out, pos, n = [], 0, 0
    # stream it in: the reader sees growing prefixes
    for k in range(40, len(rest) + 1, 37):
        while True:
            piece, npos = eb.next_reading_piece(rest[:k], pos, target=240 if not out else 700, chunk=700, done=False)
            if piece is None:
                break
            assert len(piece) <= 700 and piece.endswith(".")
            out.append(piece)
            pos = npos
    while True:
        piece, pos = eb.next_reading_piece(rest, pos, target=700, chunk=700, done=True)
        if piece is None:
            break
        out.append(piece)
    assert _norm(" ".join(out)) == _norm(rest)
    assert eb.next_reading_piece("Call me Ish", 0, target=5, chunk=700, done=False) == (None, 0)


def test_the_stream_reader_reads_while_it_arrives_and_stops_on_the_token():
    spoken, token = [], {"v": 0}
    r = eb.StreamReader(lambda p: spoken.append((time.time(), p)), lambda: token["v"] == 0,
                        engage=100, first=120, chunk=400, limit=16000)
    clause = "Here is the opening of Moby-Dick by Herman Melville."
    r.feed(TEXT[:200])
    assert not r.engaged                                       # not before her clause is said
    r.clause_said(clause)
    assert r.engaged
    for k in range(200, len(TEXT) + 1, 300):
        r.feed(TEXT[:k])
        time.sleep(0.01)
    assert spoken and spoken[0][1].startswith("Call me Ishmael")      # reading began before the stream ended
    r.finish(TEXT)
    r.join(5)
    assert _norm(clause + " " + r.said()) == _norm(TEXT) and all(len(p) <= 400 for _, p in spoken)
    # a stop between pieces ends it
    spoken.clear()
    r2 = eb.StreamReader(lambda p: (spoken.append(p), token.__setitem__("v", 1)), lambda: token["v"] == 0,
                         engage=100, first=120, chunk=400, limit=16000)
    token["v"] = 0
    r2.clause_said(clause)
    r2.feed(TEXT)
    r2.finish()
    r2.join(5)
    assert len(spoken) == 1 and r2.stopped


def test_the_stream_reader_never_reads_after_a_tool_event_or_once_closed():
    r = eb.StreamReader(lambda p: None, lambda: True, engage=10, first=10, chunk=400, limit=16000)
    r.feed(TEXT, tool_seen=True)
    r.clause_said("Here is the opening of Moby-Dick by Herman Melville.")
    assert not r.engaged
    r2 = eb.StreamReader(lambda p: None, lambda: True, engage=10, first=10, chunk=400, limit=16000)
    assert r2.close() is False
    r2.clause_said("Here is the opening of Moby-Dick by Herman Melville.")
    r2.feed(TEXT)
    assert not r2.engaged


def test_speak_long_hands_the_voice_pieces_and_drops_nothing(monkeypatch):
    got = []
    monkeypatch.setattr(eb, "speak_blocking", lambda t, **k: got.append((t, k.get("skip_quiet"))))
    monkeypatch.setattr(eb, "READ_CHUNK", 700)
    said = eb.speak_long(TEXT, voice="crissa", chalk_title="crissa")
    assert len(got) > 5 and all(len(t) <= 700 for t, _ in got) and _norm(said) == _norm(TEXT)
    assert got[0][1] is False and all(q is True for _, q in got[1:])
    got.clear()
    eb.speak_long("A short answer.", voice="crissa")
    assert got == [("A short answer.", False)]


# ---- what she said is in the next turn ---------------------------------------------------------------------------

def test_the_owner_history_keeps_the_last_turns_and_a_reading_as_its_opening_and_place():
    m = eb.ConvoMemory(turns=4, idle_s=900, msg_chars=600)
    m.add("Can you read me the Melville passage?", TEXT, reading=True, now=100.0)
    msgs = m.messages(now=110.0)
    assert [x["role"] for x in msgs] == ["user", "assistant"]
    a = msgs[1]["content"]
    assert a.startswith("Here is the opening of Moby-Dick by Herman Melville.") and "she stopped here" in a
    assert len(a) < 800 and a.rstrip().endswith("world.")
    for i in range(6):
        m.add("q%d" % i, "a%d" % i, now=120.0 + i)
    assert [x["content"] for x in m.messages(now=130.0) if x["role"] == "user"] == ["q2", "q3", "q4", "q5"]
    assert m.messages(now=130.0 + 901) == []                  # idle past the window: forgotten


def test_history_rides_only_on_an_owner_turn_to_an_endpoint_without_a_session(monkeypatch):
    monkeypatch.setattr(eb, "HISTORY", True)
    mem = eb.ConvoMemory(turns=4, idle_s=900, msg_chars=600)
    mem.add("read me Moby-Dick", "Here is the opening of Moby-Dick. Call me Ishmael.")
    monkeypatch.setattr(eb, "_CONVO", mem)
    plain = {"session_header": ""}
    assert len(eb.history_for("bau", plain)) == 2
    assert eb.history_for("guest", plain) == []
    assert eb.history_for("bau", {"session_header": "X-Session-Id"}) == []    # the agent keeps its own
    monkeypatch.setattr(eb, "HISTORY", False)
    assert eb.history_for("bau", plain) == []


def test_crissa_reply_sends_the_history_between_her_persona_and_his_question(monkeypatch):
    sent = {}

    def fake_stream(url, payload, **kw):
        sent["payload"] = payload
        if kw.get("on_delta"):
            kw["on_delta"]("It was Moby-Dick, by Herman Melville.")
        return "It was Moby-Dick, by Herman Melville."

    mem = eb.ConvoMemory(turns=4, idle_s=900, msg_chars=600)
    mem.add("Start reading from the Melville passage.", TEXT, reading=True)
    monkeypatch.setattr(eb, "_CONVO", mem)
    monkeypatch.setattr(eb, "HISTORY", True)
    monkeypatch.setattr(eb, "_stream_chat_text", fake_stream)
    monkeypatch.setattr(eb, "_gateway", lambda p: {"profile": "crissa", "url": "http://127.0.0.1:1/v1/chat/completions",
                                                   "key": "", "model": "m", "voice": "crissa", "session_header": ""})
    monkeypatch.setattr(eb, "presence_facts_line", lambda *a, **k: "")
    monkeypatch.setattr(eb, "_today_memory_bullets", lambda: "")
    monkeypatch.setattr(eb, "claim_checked", lambda content, **k: content)
    out = eb.crissa_reply("What book was that?", person_id="owner", presence={}, level="bau", user_voice=True)
    msgs = sent["payload"]["messages"]
    assert msgs[0]["role"] == "system" and msgs[-1] == {"role": "user", "content": "What book was that?"}
    assert msgs[1]["content"].startswith("Start reading") and "Moby-Dick" in msgs[2]["content"]
    assert "Moby-Dick" in out


def test_the_stock_persona_offers_the_reading_rule_as_an_editable_default():
    text = (ROOT / "avatars" / "crissa" / "persona.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "read about 600 characters" in flat and '"Shall I go on?"' in flat
    assert "carry on from where you stopped" in flat and "Name what you are reading" in flat


def test_dispatch_reads_a_long_answer_as_it_streams_and_keeps_it(monkeypatch):
    """The whole turn against a stub stream (the bench's harness, sped up): the reading starts before the stream ends,
    every piece fits the voice, nothing is dropped, and the reading lands in her history."""
    import http.server
    text = TEXT[:3000].rsplit(". ", 1)[0] + "."

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i in range(0, len(text), 15):
                self.wfile.write(("data: %s\n\n" % json.dumps({"choices": [{"delta": {"content": text[i:i + 15]}}]})).encode())
                self.wfile.flush()
                time.sleep(0.004)
            self.wfile.write(b"data: [DONE]\n\n")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/v1/chat/completions" % srv.server_address[1]
    calls = []
    mem = eb.ConvoMemory(turns=4, idle_s=900, msg_chars=600)
    for k, v in {"speak_blocking": lambda t, **kw: calls.append((time.time(), t)),
                 "_wait_speak_quiet": lambda token: True,
                 "_get": lambda u, timeout=1.5: {"headcount": 1, "people": []},
                 "authorize": lambda stamp, presence: "bau",
                 "evaluate_turn": lambda *a, **kw: {"user_voice": True},
                 "_gateway": lambda p: {"profile": "crissa", "url": url, "key": "", "model": "m", "voice": "crissa",
                                        "session_header": ""},
                 "_post_json": lambda *a, **kw: {}, "_memory_append": lambda *a, **kw: None,
                 "presence_facts_line": lambda *a, **kw: "", "_today_memory_bullets": lambda: "",
                 "claim_checked": lambda content, **kw: content, "_CONVO": mem,
                 "READ_STREAM": True, "READ_CHUNK": 700, "READ_ENGAGE": 200, "READ_FIRST": 240, "HISTORY": True}.items():
        monkeypatch.setattr(eb, k, v)
    t0 = time.time()
    eb.dispatch("Read me the Melville passage.", {"person_id": "owner", "stamp": {}})
    srv.shutdown()
    stream_s = len(text) / 15 * 0.004
    assert len(calls) >= 4 and calls[1][0] - t0 < stream_s           # the first piece before the stream's end
    assert all(len(t) <= 700 for _, t in calls)
    assert _norm(" ".join(t for _, t in calls)) == _norm(text)
    hist = mem.messages()
    assert hist and hist[0]["content"] == "Read me the Melville passage." and "Moby-Dick" in hist[1]["content"]
