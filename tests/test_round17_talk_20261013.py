"""Round 17, the conversation (.101 RESULTS-20261013-0630 + ASK-20261013-0405 addenda 05:56-06:03; 2026-10-13).

1. "Keep reading" was refused by xAI (403 permission-denied, "I'm sorry, I can't help with that request.") and she said
   "I couldn't reach my chat service": a continue now sends only where she is, and a refusal is said and logged as one.
2. "What model are you?" -> "grok-4.": a dot between digits never ends a clause, and her model line has a spoken form.
3. The Wall's log printed one line 6 times, out of order, with two speakers: one heard line is one row, by time.
4. After Setup's "Hello! What can we do together?" his first words needed "Hey Crissa": her question opens the ear.

No model, network service or sound: every endpoint is a stub on 127.0.0.1 or a monkeypatch.
"""
from __future__ import annotations

import http.server
import importlib
import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "modules" / "ear", ROOT / "lib"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from crissa_bridge import ear_bridge as eb  # noqa: E402
from crissa_bridge import heard_roll, ttfs  # noqa: E402

from _kit import kit, load as load_kit  # noqa: E402  - the kit's copy of a shipped file (the edition overlay)

# The feed's round-17 change is the kit's: scripts/voiceonly-home-server.py is prod-guarded on the house, so the kit's
# version is crissa/overlay/scripts/voiceonly-home-server.py (config/crissa-packages.json `edition_overlay`), laid
# over the path in every pack. These tests read that copy; the house's file stays as it was.
FEED = "scripts/voiceonly-home-server.py"

SENT = ("Some leaning against the spiles, some seated upon the pier-heads, some looking over the bulwarks of ships from "
        "China, some high aloft in the rigging, as if striving to get a still better view. ")
READING = "Moby Dick, by Herman Melville. Call me Ishmael. " + SENT * 30
XAI_REFUSAL = b'{"code": "permission-denied", "error": "I\'m sorry, I can\'t help with that request."}'


class _HTTPErr(Exception):
    def __init__(self, code, body=b""):
        super().__init__("HTTP Error %d" % code)
        self.code, self._b = code, body

    def read(self):
        return self._b


# ---- 1 (b): a refusal is its own line, logged as one, never asked again --------------------------------------------

def test_1b_a_403_refusal_is_not_a_refused_key_nor_a_reach_error():
    e = _HTTPErr(403, XAI_REFUSAL)
    assert eb.model_refused(e) is True and eb.key_refused(e) is False
    assert eb.quiet_retry_ok(_HTTPErr(403, XAI_REFUSAL), spoken=False, elapsed_s=0.4) is False   # refused again anyway
    assert eb.model_refused(_HTTPErr(403, b'{"code":"permission-denied","error":"content flagged"}')) is True
    assert eb.model_refused(_HTTPErr(400, b'{"error":"This request violates our usage policy"}')) is True
    # not refusals: a bare 403 (round 13's quiet retry stands), a refused key, an account or model permission, a 5xx
    assert eb.model_refused(_HTTPErr(403, b"Forbidden")) is False
    assert eb.quiet_retry_ok(_HTTPErr(403, b"Forbidden"), spoken=False, elapsed_s=0.4) is True
    assert eb.model_refused(_HTTPErr(403, b'{"error":"Invalid API key"}')) is False
    assert eb.model_refused(_HTTPErr(401, XAI_REFUSAL)) is False
    assert eb.model_refused(_HTTPErr(403, b'{"code":"permission-denied","error":"Your team has no access to the model"}')) is False
    assert eb.model_refused(_HTTPErr(503, XAI_REFUSAL)) is False and eb.model_refused(TimeoutError()) is False


def test_1b_the_kill_switch_is_round_16(monkeypatch):
    monkeypatch.setenv("EAR_BRIDGE_REFUSAL_SAY", "0")
    assert eb.model_refused(_HTTPErr(403, XAI_REFUSAL)) is False
    assert eb.quiet_retry_ok(_HTTPErr(403, XAI_REFUSAL), spoken=False, elapsed_s=0.4) is True


def _reply_stubs(monkeypatch, raise_err):
    events, calls = [], []

    def stream(url, payload, **kw):
        calls.append(payload)
        raise raise_err()

    for k, v in {"_stream_chat_text": stream, "presence_facts_line": lambda *a, **k: "",
                 "_today_memory_bullets": lambda: "", "claim_checked": lambda content, **k: content,
                 "_gateway": lambda p: {"profile": "crissa", "url": "https://api.x.ai/v1/chat/completions", "key": "",
                                        "model": "grok-test", "voice": "crissa", "session_header": ""},
                 "_log": events.append}.items():
        monkeypatch.setattr(eb, k, v)
    return events, calls


def test_1b_she_says_her_model_declined_and_the_log_says_refusal(monkeypatch):
    events, calls = _reply_stubs(monkeypatch, lambda: _HTTPErr(403, XAI_REFUSAL))
    got = eb.crissa_reply("Keep reading", person_id="o", presence={}, level="bau", user_voice=True)
    assert got == eb.REFUSAL_LINE == "My model declined that one."
    names = [e.get("event") for e in events]
    assert "gateway_refusal" in names and "gateway_error" not in names and "gateway_retry" not in names
    assert len(calls) == 1                                                    # never asked again
    ev = next(e for e in events if e.get("event") == "gateway_refusal")
    assert ev["code"] == 403 and "can't help" in ev["body"] and ev["key_refused"] is False


def test_1b_a_real_reach_error_keeps_the_reach_line(monkeypatch):
    events, _ = _reply_stubs(monkeypatch, lambda: ConnectionRefusedError("refused"))
    got = eb.crissa_reply("Keep reading", person_id="o", presence={}, level="bau", user_voice=True)
    assert got == eb.GATEWAY_DOWN_LINE
    assert [e.get("event") for e in events if str(e.get("event", "")).startswith("gateway_")] == ["gateway_error"]


# ---- 1 (a): a continue sends where she is, never the text -----------------------------------------------------------

def _longest_run(text: str, source: str) -> int:
    """The longest run of consecutive words of `source` that appears in `text` (case and spacing as they are)."""
    words = source.split()
    best = 0
    for n in (40, 24, 16, 12, 10, 8, 6, 4, 2):
        if any(" ".join(words[i:i + n]) in text for i in range(0, len(words) - n + 1)):
            best = n
            break
    return best


class _Stub(http.server.BaseHTTPRequestHandler):
    bodies: list = []
    refuse_continue = False

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        self.bodies.append(body)
        sysm = body["messages"][0]["content"]
        if self.refuse_continue and "They asked you to go on" in sysm:       # what api.x.ai answered .101
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(XAI_REFUSAL)))
            self.end_headers()
            self.wfile.write(XAI_REFUSAL)
            return
        text = ("Picking up where I stopped. " + SENT) if "They asked you to go on" in sysm else READING
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for i in range(0, len(text), 12):
                self.wfile.write(("data: %s\n\n" % json.dumps({"choices": [{"delta": {"content": text[i:i + 12]},
                                                                             "finish_reason": None}]})).encode())
                self.wfile.flush()
                time.sleep(0.002)
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def _real_path(monkeypatch, tmp_path, refuse=False, said=None):
    H = type("H", (_Stub,), {"bodies": [], "refuse_continue": refuse})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/v1/chat/completions" % srv.server_address[1]
    stop = {"done": False}

    def speak(text, **kw):
        if said is not None:
            said.append(text)
        eb._speaking = True
        eb._SAY.begin(text)
        if "spiles" in text and not stop["done"]:
            stop["done"] = True
            eb.abort_mouth("name_interrupt")             # "Hey Crissa, stop" on the first piece of the reading
        eb._SAY.end()
        eb._speaking = False

    mem = eb.ConvoMemory(turns=4, idle_s=900, msg_chars=600)
    for k, v in {"speak_blocking": speak, "_wait_speak_quiet": lambda token: True,
                 "_get": lambda u, timeout=1.5: {"headcount": 1, "people": []},
                 "authorize": lambda stamp, presence: "bau", "evaluate_turn": lambda *x, **kw: {"user_voice": True},
                 "_gateway": lambda p: {"profile": "crissa", "url": url, "key": "", "model": "grok-test-model",
                                        "voice": "crissa", "session_header": ""},
                 "_post_json": lambda *x, **kw: {}, "_memory_append": lambda *x, **kw: None,
                 "presence_facts_line": lambda *x, **kw: "", "_today_memory_bullets": lambda: "",
                 "claim_checked": lambda content, **kw: content, "_CONVO": mem, "HISTORY": True,
                 "READ_STREAM": True, "READ_CHUNK": 300, "READ_ENGAGE": 100, "READ_FIRST": 120,
                 "_READING_STATE": {"closed_at": 0.0, "why": ""}, "_pending": None}.items():
        monkeypatch.setattr(eb, k, v)
    monkeypatch.setattr(eb.cancels, "path", lambda: tmp_path / "cancel.json")
    monkeypatch.setattr(eb.cancels, "log_path", lambda: tmp_path / "cancels.jsonl")
    eb.dispatch("Read me the Melville passage.", {"person_id": "o", "stamp": {}})
    assert stop["done"] and (tmp_path / "cancel.json").is_file()
    n = len(H.bodies)
    eb.dispatch("Keep reading, please.", {"person_id": "o", "stamp": {}})
    srv.shutdown()
    if refuse:
        assert len(H.bodies) == n + 1                                         # refused once, never asked again
    return H.bodies[n], mem


def test_1a_keep_reading_sends_the_place_not_the_book(monkeypatch, tmp_path):
    body, _mem = _real_path(monkeypatch, tmp_path)
    sysm = body["messages"][0]["content"]
    whole = json.dumps(body["messages"])
    assert "They asked you to go on" in sysm and "Moby Dick" in sysm
    assert "word for word" not in whole
    assert "[Context from your interrupted spoken reply]" not in sysm         # its "said" is not sent on a continue
    assert _longest_run(" ".join(m["content"] for m in body["messages"]), READING) <= 2 * eb.ANCHOR_WORDS
    hist = body["messages"][1:-1]
    assert hist and hist[1]["content"].startswith("[I was reading ") and "just after the words" in hist[1]["content"]
    assert body["messages"][-1]["content"] == "Keep reading, please."


def test_1a_the_kill_switch_sends_round_16s_continue(monkeypatch, tmp_path):
    monkeypatch.setattr(eb, "CONTINUE_POSITION", False)
    body, _mem = _real_path(monkeypatch, tmp_path)
    whole = " ".join(m["content"] for m in body["messages"])
    assert "word for word" in whole and _longest_run(whole, READING) > 2 * eb.ANCHOR_WORDS   # what .101 was refused


def test_1a_the_place_is_a_few_words_of_each_sentence():
    rec = {"title": "Moby Dick, by Herman Melville. (continued)", "last_sentence": SENT.strip(),
           "next_sentence": "But these are all landsmen; of week days pent up in lath and plaster."}
    note = eb.position_note(rec)
    assert '"...if striving to get a still better view."' in note                 # ANCHOR_WORDS (8) of it
    assert '"But these are all landsmen; of week days ..."' in note
    assert "(continued)" not in note and "spiles" not in note


def test_1a_a_refused_continue_says_so_and_the_reading_keeps_its_place(monkeypatch, tmp_path):
    said: list = []
    events: list = []
    real_log = eb._log
    monkeypatch.setattr(eb, "_log", lambda d: (events.append(d), real_log(d)))
    _body, mem = _real_path(monkeypatch, tmp_path, refuse=True, said=said)
    assert said[-1] == eb.REFUSAL_LINE and eb.GATEWAY_DOWN_LINE not in said
    assert any(e.get("event") == "gateway_refusal" for e in events)
    assert not any(e.get("event") in ("gateway_error", "gateway_retry") for e in events)
    rec = eb.reading_record(mem)                                              # "go on" still finds where she was
    assert rec and "declined" not in rec["last_sentence"] and "declined" not in json.dumps(mem.messages())


# ---- 2: a dot between digits never ends a clause; her model's name said aloud ---------------------------------------

def test_2_the_stream_never_takes_grok_4_dot_for_a_sentence():
    reply = 'I run on the language model "Grok 4.20, non-reasoning", from xAI.'
    first = None
    for i in range(1, len(reply) + 1):                       # every way the stream can be cut
        first = ttfs.first_speakable_clause(reply[:i])
        if first:
            break
    assert first == reply
    assert ttfs.first_speakable_clause("I'm grok-4.") is None
    assert ttfs.first_speakable_clause("Version 4.20 is out. More") == "Version 4.20 is out."
    assert ttfs.first_speakable_clause("I counted to 4. Then") == "I counted to 4."     # a real end, followed by more
    assert ttfs.first_speakable_clause("It is done.") == "It is done."


def test_2_the_clause_kill_switch(monkeypatch):
    monkeypatch.setenv("EAR_BRIDGE_CLAUSE_NUMBER", "0")
    assert ttfs.first_speakable_clause("I'm grok-4.") == "I'm grok-4."


def test_2_a_stop_inside_a_number_is_not_a_sentence_end():
    piece = "I run on grok 4.20 now. Next one is here."
    n = piece.index("4.") + 2                                # the cut lands right after "4."
    got = eb.aired_part(piece, eb.SPEAK_LEAD_S + n / eb.SPEAK_CPS + 0.01)
    assert not got.endswith("4.")


def _speakable(monkeypatch, on: str):
    monkeypatch.setenv("HOUSE_SPEAKABLE_NUMBER_DOT", on)
    spec = importlib.util.spec_from_file_location("speakable_r17_%s" % on, ROOT / "lib" / "speakable.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_2_the_speech_cleaner_keeps_a_dotted_number(monkeypatch):
    sp = _speakable(monkeypatch, "1")
    assert sp.for_display("I am grok-4.20-0309-non-reasoning now.") == "I am grok-4.20 now."
    assert sp.for_display("Pi is 3.14159265 or so.") == "Pi is 3.14159265 or so."
    assert sp.for_display("Kernel 7.0.0-38-generic is up.") == "Kernel 7.0.0-38-generic is up."
    # machine keys still go, as before
    assert sp.for_display("see a1b2c3d4e5f6a7b8 there") == "see there"
    assert sp.for_display("card t_8ec7d397 parked") == "card parked"
    assert sp.for_display("I run on Grok 4.20, non-reasoning, from xAI.") == "I run on Grok 4.20, non-reasoning, from xAI."
    off = _speakable(monkeypatch, "0")
    assert off.for_display("I am grok-4.20-0309-non-reasoning now.") == "I am grok-4. now."   # the .101 cut


def test_2_her_model_line_has_a_spoken_name_and_the_exact_id():
    line = eb.model_line({"model": "grok-4.20-0309-non-reasoning", "url": "https://api.x.ai/v1"})
    assert '"grok-4.20-0309-non-reasoning" from xAI' in line and '"Grok 4.20, non-reasoning"' in line
    assert "exact id" in line
    for mid, said in (("grok-4.20-0309-non-reasoning", "Grok 4.20, non-reasoning"), ("grok-4", "Grok 4"),
                      ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5"), ("gpt-4o-2024-08-06", "GPT 4o"),
                      ("x-ai/grok-4-fast", "Grok 4, fast"), ("mistral-large-latest", "Mistral Large")):
        assert eb.spoken_model(mid) == said, mid
    # a plain name keeps round 16's line
    assert "said aloud" not in eb.model_line({"model": "ops", "url": "http://127.0.0.1:8000/v1"})


def test_2_the_model_line_kill_switch(monkeypatch):
    monkeypatch.setattr(eb, "MODEL_SPOKEN", False)
    line = eb.model_line({"model": "grok-4.20-0309-non-reasoning", "url": "https://api.x.ai/v1"})
    assert "said aloud" not in line and "say exactly that." in line


# ---- 3: one heard line, one row ------------------------------------------------------------------------------------

def test_3_said_over_her_line_is_decided_once_and_noted_once(monkeypatch, tmp_path):
    monkeypatch.setattr(eb.heard_roll, "HOME", tmp_path)
    monkeypatch.setattr(eb.cancels, "HOME", tmp_path)
    asks, sent = [], []
    done = threading.Event()
    monkeypatch.setattr(eb, "_over_line_ask", lambda msgs: asks.append(msgs) or None)   # .101: the 403, a fallback
    monkeypatch.setattr(eb, "_over_line_owner", lambda *a: True)
    monkeypatch.setattr(eb, "_speaking", True)                                # she is still on her line
    monkeypatch.setattr(eb, "_dispatch_busy", False)                          # and no turn is in flight
    monkeypatch.setattr(eb, "_get", lambda u, timeout=1.5: {})
    monkeypatch.setattr(eb, "is_user_auth_only", lambda: False)
    monkeypatch.setattr(eb, "try_handle_utterance", lambda *a, **k: False)
    monkeypatch.setattr(eb, "dispatch", lambda u, m: (sent.append((u, m)), done.set()))
    eb._over_line_run("Keep reading", {"person_id": "o"})
    assert done.wait(5)
    time.sleep(0.2)                                                           # a re-ask would have run by now
    rows = (tmp_path / "heard-roll.jsonl").read_text().splitlines()
    assert len(asks) == 1 and len(rows) == 1 and sent[0][0] == "Keep reading"
    assert json.loads(rows[0])["why"] == "said over her line: answered after it"
    eb._dispatch_busy = False


def test_3_the_heard_roll_writes_one_row_for_one_line(monkeypatch, tmp_path):
    monkeypatch.setattr(heard_roll, "HOME", tmp_path)
    monkeypatch.setattr(heard_roll, "_LAST", {})
    t0 = 1_800_000_000.0
    for i in range(6):                                                        # .101: 6 rows, ~0.5 s apart
        heard_roll.note("o", "Keep reading", "said over her line: answered after it", now=t0 + 0.5 * i)
    heard_roll.note("o", "Keep reading", "queued: she answers after this", now=t0 + 3)
    heard_roll.note("o", "Keep reading", "said over her line: answered after it", now=t0 + 40)
    rows = [json.loads(x) for x in (tmp_path / "heard-roll.jsonl").read_text().splitlines()]
    assert [r["ts"] for r in rows] == [t0, t0 + 3, t0 + 40]


def test_3_the_heard_roll_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("EAR_BRIDGE_HEARD_ROLL_ONCE", "0")
    monkeypatch.setattr(heard_roll, "HOME", tmp_path)
    for i in range(3):
        heard_roll.note("o", "Keep reading", "x", now=1_800_000_000.0 + i)
    assert len((tmp_path / "heard-roll.jsonl").read_text().splitlines()) == 3


def _feed(tmp_path, monkeypatch):
    F = load_kit(FEED, "vo_feed_r17")                 # the kit's feed, laid over its path as a pack runs it
    monkeypatch.setattr(F, "HEARD_ROLL", tmp_path / "heard-roll.jsonl")
    return F


def test_3_the_feed_gives_one_row_per_line_by_time_and_one_speaker(tmp_path, monkeypatch):
    F = _feed(tmp_path, monkeypatch)
    now = time.time()
    T = now - 60
    hello = {"who": "crissa", "text": "Hello! What can we do together?", "ts": T, "stamped": True,
             "source": "mouth_air", "certain": True, "live": False}
    # the face shell's chat, sorted (it includes the mouth's line)
    shell = [dict(hello),
             {"who": "o", "text": "Keep reading", "ts": T + 50.0, "source": "voice_emb", "log_user": True},
             {"who": "stranger", "text": "Keep reading, please.", "ts": T + 58.0, "source": "voice_emb",
              "stamped": False}]
    rows = [{"ts": T + 50.4 + 0.5 * i, "who": "o", "text": "Keep reading", "why": "said over her line: answered after it",
             "kind": "heard"} for i in range(6)]
    rows.append({"ts": T + 58.3, "who": "o", "text": "Keep reading, please.", "why": "queued: she answers after this",
                 "kind": "heard"})
    rows.append({"ts": T + 20, "who": "crissa", "text": "Listening off. Say Crissa to talk again.", "why": "timeout",
                 "kind": "status"})
    (tmp_path / "heard-roll.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    chat = F.one_row_chat(shell, F.recent_heard_roll(now=now), dict(hello))
    texts = [c["text"] for c in chat]
    assert texts.count("Hello! What can we do together?") == 1                 # not first AND last
    assert texts.count("Keep reading · said over her line: answered after it") == 1
    assert [c["ts"] for c in chat] == sorted(c["ts"] for c in chat)           # in time order
    please = [c for c in chat if "please" in c["text"]]
    assert len(please) == 1 and please[0]["who"] == "o"                       # the bridge's speaker, one row
    assert please[0]["replaces"] == [["stranger", "Keep reading, please.", round(T + 58.0, 3)]]
    assert not any(c.get("who") == "stranger" for c in chat)


def test_3_the_feed_with_no_heard_roll_only_drops_the_second_mouth_row(tmp_path, monkeypatch):
    F = _feed(tmp_path, monkeypatch)                                          # no heard roll file (a house-shaped bridge)
    hello = {"who": "crissa", "text": "On it.", "ts": 100.0, "source": "mouth_air", "live": False}
    shell = [{"who": "user", "text": "Show the board.", "ts": 90.0, "source": "voice_emb"}, dict(hello)]
    assert F.one_row_chat(shell, F.recent_heard_roll(), dict(hello)) == shell
    live = dict(hello, live=True)
    got = F.one_row_chat(shell, [], live)
    assert len(got) == 2 and got[1]["live"] is True                           # the same line, now on the air
    other = {"who": "crissa", "text": "Next line.", "ts": 101.0, "source": "mouth_air", "live": False}
    assert F.one_row_chat(shell, [], other)[-1] == other                      # a newer line is still added
    src = kit(FEED).read_text(encoding="utf-8")
    assert "if VO_CHAT_ONE_ROW:" in src and "chat = one_row_chat(chat, recent_heard_roll(), mouth)" in src


def test_3_the_wall_rewrites_the_ears_line_in_place():
    R = importlib.import_module("house_wall_ascii_render")
    roll = R.Roll(since=0.0)
    t = 1_800_000_000.0
    roll.feed([{"who": "stranger", "text": "Keep reading, please.", "ts": t}], now=t + 1)
    assert [ln["who"] for ln in roll.lines] == ["stranger"]
    heard = {"who": "o", "text": "Keep reading, please. · queued: she answers after this", "ts": t + 0.3,
             "log_user": True, "replaces": [["stranger", "Keep reading, please.", round(t, 3)]]}
    roll.feed([heard], now=t + 2)
    assert len(roll.lines) == 1 and roll.lines[0]["who"] == "o" and roll.lines[0]["cls"] == "user"
    assert roll.lines[0]["at"] == t + 1                                       # it stays where it was on the roll
    roll.feed([{"who": "stranger", "text": "Keep reading, please.", "ts": t}, heard], now=t + 3)
    assert len(roll.lines) == 1                                               # neither comes back as a line
    fresh = R.Roll(since=0.0)                                                 # the ear's row never drawn: one line
    fresh.feed([heard], now=t + 2)
    fresh.feed([{"who": "stranger", "text": "Keep reading, please.", "ts": t}], now=t + 3)
    assert [ln["who"] for ln in fresh.lines] == ["o"]


# ---- 4: her question opens the ear ---------------------------------------------------------------------------------

def _gate(tmp_path, **kw):
    g = {"schema": "house_tts_gate/v1", "speaking": False, "last_reply": "Hello! What can we do together?",
         "who": "crissa", "notice": "", "updated_ts": time.time() - 1.0, "air_at": time.time() - 3.0,
         "air_until": time.time() - 1.0}
    g.update(kw)
    p = tmp_path / ("gate-%d.json" % len(list(tmp_path.glob("gate-*.json"))))
    p.write_text(json.dumps(g))
    return p


def test_4_her_question_on_the_gate_is_found_only_when_it_is_hers_and_just_ended(tmp_path):
    now = time.time()
    got = eb.her_question_ended(now, [_gate(tmp_path)])
    assert got and got[1] == "Hello! What can we do together?"
    assert eb.her_question_ended(now, [_gate(tmp_path, who="lila")]) is None
    assert eb.her_question_ended(now, [_gate(tmp_path, who="announcer")]) is None
    assert eb.her_question_ended(now, [_gate(tmp_path, who="crissa@bench")]) is None
    assert eb.her_question_ended(now, [_gate(tmp_path, speaking=True)]) is None
    assert eb.her_question_ended(now, [_gate(tmp_path, notice="interrupted")]) is None
    assert eb.her_question_ended(now, [_gate(tmp_path, last_reply="Hello there.")]) is None
    assert eb.her_question_ended(now, [_gate(tmp_path, air_until=now - 30, updated_ts=now - 30)]) is None
    assert eb.her_question_ended(now, [tmp_path / "missing.json"]) is None


def test_4_setup_running_reads_the_walk(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "HOME", tmp_path)
    assert eb.setup_running() is False
    (tmp_path / "init-run.json").write_text(json.dumps({"status": "running"}))
    assert eb.setup_running() is True
    (tmp_path / "init-run.json").write_text(json.dumps({"status": "done"}))
    assert eb.setup_running() is False


class _Stop(BaseException):
    pass


def _stop(*a, **k):
    raise _Stop()


def _loop(monkeypatch, tmp_path, *, opens=True, setup="done"):
    """main() on stubs: tick 1 sees nothing (her question just ended), tick 2 hears his first words, no "Hey Crissa"."""
    monkeypatch.setattr(eb, "HOME", tmp_path)
    monkeypatch.setattr(eb.heard_roll, "HOME", tmp_path)
    monkeypatch.setattr(eb.cancels, "HOME", tmp_path)
    (tmp_path / "init-run.json").write_text(json.dumps({"status": setup}))
    gate = _gate(tmp_path)
    events, sent = [], []
    ticks = {"n": 0}
    uid = "o"
    turn = {"ts": time.time() + 1.0, "text": "Let's read something together.", "person_id": uid, "via": "ear",
            "stamp": {"person_id": uid, "voice_emb_sim": 0.9, "source": "voice_emb"}}

    def get(url, timeout=1.5):
        if url == eb.STT_TURNS:
            ticks["n"] += 1
            if ticks["n"] == 1:
                return {"turns": []}                                          # the seed
            if ticks["n"] == 2:
                return {"turns": []}
            if ticks["n"] <= 6:
                return {"turns": [turn]}
            raise _Stop()
        return {}

    for k, v in {"_get": get, "_log": events.append, "_reset_speech_logs": lambda: None,
                 "start_abort_listener": lambda: None, "voice_on_cached": lambda: True,
                 "_tool_hold_active": lambda: None, "request_session_look": lambda **kw: {},
                 "own_voice_gate_paths": lambda: [gate], "is_mouth_echo": lambda t: False,
                 "house_mouth_on_air": lambda *a, **k: False, "user_id_from_mode": lambda: uid,
                 "user_voice_prints": lambda u: 3, "resume_pending": lambda now=None: False,
                 "is_oracle_feed": lambda t: False, "is_spoken_via": lambda t: True,
                 "clear_session_board": lambda: None, "listening_off": lambda why: None,
                 "schedule_dispatch": lambda u, m: (sent.append(u), _stop()),
                 "pa_yield_for_hear": lambda *a, **k: None, "pending_phase": lambda: "",
                 "render_skips_crissa": lambda: False, "ear_still_hearing": lambda *a, **k: False,
                 "PA_PLAY": tmp_path / "announce-play.json",
                 "QUESTION_OPENS": opens, "POLL": 0.01, "FLUSH_FAST": 0.0, "SEED_WAIT_S": 5.0,
                 "_speaking": False, "_speak_proc": None, "_dispatch_busy": False}.items():
        monkeypatch.setattr(eb, k, v)
    monkeypatch.setenv("EAR_BRIDGE_OWN_VOICE", "off")
    monkeypatch.setattr(eb.own_voice, "mode", lambda: "off")
    with pytest.raises(_Stop):
        eb.main()
    return events, sent


def test_4_after_her_question_his_first_words_reach_her_without_her_name(monkeypatch, tmp_path):
    events, sent = _loop(monkeypatch, tmp_path)
    opened = [e for e in events if e.get("event") == "session_open"]
    assert opened and opened[0]["why"] == "her_question" and opened[0]["for_s"] == eb.QUESTION_OPEN_S
    assert sent == ["Let's read something together."]
    assert not any(e.get("event") == "crissa_skip" for e in events)


def test_4_not_while_setup_runs_and_not_with_the_switch_off(monkeypatch, tmp_path):
    for kw in ({"setup": "running"}, {"opens": False}):
        events, sent = _loop(monkeypatch, tmp_path, **kw)
        assert sent == [] and not any(e.get("why") == "her_question" for e in events), kw
        if kw.get("setup") == "running":
            # round 41 (F4): a walk holds every answer; the turns never reach the skip
            assert any(e.get("event") == "walk_hold" and e.get("on") for e in events), kw
        else:
            assert any(e.get("event") == "crissa_skip" and e.get("why") == "paused" for e in events), kw
    monkeypatch.setenv("CRISSA_WALK_HOLDS_ANSWERS", "0")             # round 41's kill: round 17's skip under Setup
    events, sent = _loop(monkeypatch, tmp_path, setup="running")
    assert sent == [] and any(e.get("event") == "crissa_skip" and e.get("why") == "paused" for e in events)
