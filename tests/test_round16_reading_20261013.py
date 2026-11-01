"""Round 16, the .101 reading (ASK-20261013-0030-from-101, addendum 00:47; 2026-10-13).

00:41:14 "Okay, you can stop for good. Tell me what model you're using." stopped her; 00:41:35 "what LLM are you?" was
answered "I'm Grok 4, built by xAI." (Connect says grok-4.20-0309-non-reasoning); 00:41:48 the ear heard "Rock four
chat." and she read the next Moby-Dick sentence. Now a stop for good, or a new subject answered, closes the reading; an
unclear line never resumes it; she is told her own model from Connect's config.
"""
from __future__ import annotations

import http.server
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "modules" / "ear", ROOT / "lib"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from crissa_bridge import ear_bridge as eb  # noqa: E402

SENT = ("Some leaning against the spiles, some seated upon the pier-heads, some looking over the bulwarks of ships from "
        "China, some high aloft in the rigging, as if striving to get a still better view. ")
READING = "Moby Dick, by Herman Melville. Call me Ishmael. " + SENT * 30


def test_a_stop_for_good_closes_and_a_plain_stop_does_not(monkeypatch):
    monkeypatch.setattr(eb, "_READING_STATE", {"closed_at": 0.0, "why": ""})
    monkeypatch.setattr(eb, "_CONVO", eb.ConvoMemory(turns=4, idle_s=900, msg_chars=600))
    assert eb.note_stop_words("Hey Crissa, stop.") is False
    assert eb.note_stop_words("Okay, you can stop for good. Tell me what model you're using.") is True
    assert eb._READING_STATE["why"] == "stop_for_good"
    for t in ("that's enough", "no more, thanks", "stop reading", "we're done"):
        assert eb._STOP_FOR_GOOD.search(t), t


def test_only_an_explicit_continue_resumes():
    for t in ("go on", "Okay. Continue.", "You can go ahead and start again where you left off.", "keep reading",
              "start it again", "pick it back up"):
        assert eb._GO_ON.search(t), t
    for t in ("Rock four chat.", "Grok 4 chat?", "what LLM are you?", "Tell me what model you're using."):
        assert not eb._GO_ON.search(t), t


def test_about_reading_tells_a_new_subject_from_one_about_the_book():
    rec = {"title": "Moby Dick, by Herman Melville. (continued) (continued)"}
    assert eb.about_reading("What book was that?", rec) and eb.about_reading("who wrote it?", rec)
    assert eb.about_reading("is Melville still read?", rec)
    assert not eb.about_reading("what LLM are you?", rec) and not eb.about_reading("Rock four chat.", rec)
    assert eb._bare_title(rec["title"]) == "Moby Dick, by Herman Melville."


def test_a_closed_reading_is_one_line_in_her_history_and_go_on_finds_nothing(monkeypatch):
    mem = eb.ConvoMemory(turns=4, idle_s=900, msg_chars=600)
    monkeypatch.setattr(eb, "_CONVO", mem)
    monkeypatch.setattr(eb, "_READING_STATE", {"closed_at": 0.0, "why": ""})
    monkeypatch.setattr(eb.cancels, "latest", lambda: {})
    mem.add("Okay. Continue.", "Moby Dick, by Herman Melville. " + SENT, reading=True, stopped=True)
    assert eb.reading_record(mem) is not None
    eb.close_reading("stop_for_good")
    assert eb.reading_record(mem) is None and mem.has_reading()
    said = mem.messages()[1]["content"]
    assert said == "[I was reading Moby Dick, by Herman Melville. aloud; that reading is closed.]"
    assert "spiles" not in json.dumps(mem.messages())
    assert eb.reading_turn("Rock four chat.", mem) == (False, "")
    # round 38 (the User via .101 round 37: "unless specified"): an explicit go-on reopens it where she stopped
    is_read, note = eb.reading_turn("go on", mem)
    assert is_read and "go on" in note.lower() and eb.reading_record(mem) is not None


def test_a_cancel_record_closed_after_it_is_not_offered(monkeypatch):
    now = time.time()
    rec = {"ts": now - 5, "title": "Moby Dick. (continued) (continued)", "last_sentence": SENT.strip(),
           "next_sentence": "But these are all landsmen.", "summary": "Cancelled 232 characters"}
    monkeypatch.setattr(eb.cancels, "latest", lambda: rec)
    monkeypatch.setattr(eb, "_READING_STATE", {"closed_at": 0.0, "why": ""})
    got = eb.reading_record(None)
    assert got and got["title"] == "Moby Dick."                        # the stacked marks are gone
    eb._READING_STATE["closed_at"] = now
    assert eb.reading_record(None) is None


def test_her_model_line_comes_from_connect():
    xai = eb.model_line({"model": "grok-4.20-0309-non-reasoning", "url": "https://api.x.ai/v1"})
    assert '"grok-4.20-0309-non-reasoning" from xAI' in xai and "say exactly that" in xai
    assert "from Anthropic" in eb.model_line({"model": "claude-fable-5-1", "url": "https://api.anthropic.com/v1"})
    assert "this computer" in eb.model_line({"model": "ops", "url": "http://127.0.0.1:8000/v1"})
    assert "from llm.example.org" in eb.model_line({"model": "m", "url": "https://llm.example.org/v1"})
    assert eb.model_line({"model": "", "url": "https://api.x.ai/v1"}) == ""
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text(encoding="utf-8")
    assert "grok-4.20-0309-non-reasoning" not in src.split("def model_line(")[1].split("\ndef ")[0]   # never a literal


# ---- .101's 00:41 sequence on the real dispatch path ---------------------------------------------------------------

class _Stub(http.server.BaseHTTPRequestHandler):
    bodies: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        self.bodies.append(body)
        sysm, last = body["messages"][0]["content"], body["messages"][-1]["content"]
        # a model that leans on her history when nothing stops it: an unclear line continues the reading
        hist = " ".join(m["content"] for m in body["messages"][1:-1])
        if "Read me" in last or "They asked you to go on" in sysm:
            text = READING
        elif "LLM" in last:
            text = "I run on the model Connect names."
        elif "spiles" in hist and "Do not continue it" not in sysm:
            text = SENT                                   # what .101 heard at 00:41:48
        else:
            text = "Sorry, I didn't catch that. Did you ask about my model?"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for i in range(0, len(text), 12):
                self.wfile.write(("data: %s\n\n" % json.dumps({"choices": [{"delta": {"content": text[i:i + 12]},
                                                                             "finish_reason": None}]})).encode())
                self.wfile.flush()
                time.sleep(0.003)
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def test_the_real_path_never_resumes_a_reading_on_an_unclear_line(monkeypatch, tmp_path):
    H = type("H", (_Stub,), {"bodies": []})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/v1/chat/completions" % srv.server_address[1]
    calls = []
    stop = {"done": False}

    def speak(text, **kw):
        # DETERMINISTIC (round 16 follow-up): no stopper thread. The first piece of the reading is stopped the moment
        # it is on the air, in this same thread: over_line's STOP order - abort_mouth (the cancel record), then his words.
        calls.append(text)
        eb._speaking = True
        eb._SAY.begin(text)
        if "spiles" in text and not stop["done"]:
            stop["done"] = True
            eb.abort_mouth("over_line_stop")
            eb.note_stop_words("Okay, you can stop for good. Tell me what model you're using.")
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
    # the cancel record in a folder of this test's own (cancel.json is read back, never the machine's)
    monkeypatch.setattr(eb.cancels, "path", lambda: tmp_path / "cancel.json")
    monkeypatch.setattr(eb.cancels, "log_path", lambda: tmp_path / "cancels.jsonl")
    eb.dispatch("Read me the Melville passage.", {"person_id": "o", "stamp": {}})
    assert stop["done"] and (tmp_path / "cancel.json").is_file()
    assert eb._READING_STATE["why"] == "stop_for_good" and eb.reading_record(mem) is None
    assert eb.cancels.context_note(consume=False) == ""                       # the close retired the pending note
    n = len(H.bodies)
    eb.dispatch("Yeah, that's what I said. What LLM are you?", {"person_id": "o", "stamp": {}})
    sys_llm = H.bodies[n]["messages"][0]["content"]
    assert 'You run on the language model "grok-test-model"' in sys_llm
    assert "[Context from your interrupted spoken reply]" not in sys_llm      # ended for good: no cancel note
    n = len(H.bodies)
    calls.clear()
    eb.dispatch("Rock four chat.", {"person_id": "o", "stamp": {}})
    body = H.bodies[n]
    srv.shutdown()
    assert "Don't wander back into it on your own" in body["messages"][0]["content"]
    assert "They asked you to go on" not in body["messages"][0]["content"]
    assert "spiles" not in json.dumps(body["messages"][1:])                   # no reading text to carry on from
    assert not any("spiles" in c for c in calls)                              # she did not read on


def test_a_new_subject_answered_after_a_plain_stop_closes_it_and_a_book_question_does_not(monkeypatch):
    mem = eb.ConvoMemory(turns=4, idle_s=900, msg_chars=600)
    monkeypatch.setattr(eb, "_READING_STATE", {"closed_at": 0.0, "why": ""})
    monkeypatch.setattr(eb.cancels, "latest", lambda: {})
    mem.add("read me Moby-Dick", "Moby Dick, by Herman Melville. " + SENT, reading=True, stopped=True)
    rec = eb.reading_record(mem)
    assert not eb.about_reading("what LLM are you?", rec)
    assert eb.about_reading("What book was that?", rec)


def test_a_note_pending_after_a_for_good_close_is_never_offered(monkeypatch, tmp_path):
    """Whichever came first - the close or the cancel record - and with cancel.json unreadable, the next turn gets no
    cancel note after a stop for good (the race seen on live)."""
    monkeypatch.setattr(eb, "_READING_STATE", {"closed_at": 0.0, "why": ""})
    monkeypatch.setattr(eb, "_CONVO", eb.ConvoMemory(turns=4, idle_s=900, msg_chars=600))
    monkeypatch.setattr(eb.cancels, "latest", lambda: {})                     # the record cannot be read back
    eb.note_stop_words("stop for good")                                       # the close first...
    with eb.cancels._LOCK:                                                    # ...then a record arms the note
        eb.cancels._NOTE.update(pending=True, rec={"ts": time.time(), "request": "read", "said": "Call me Ishmael.",
                                                   "last_sentence": "Call me Ishmael."})
    sent = {}
    monkeypatch.setattr(eb, "_stream_chat_text", lambda url, payload, **kw: sent.update(p=payload) or "I run on it.")
    monkeypatch.setattr(eb, "_gateway", lambda p: {"profile": "crissa", "url": "http://127.0.0.1:1/v1/chat/completions",
                                                   "key": "", "model": "m", "voice": "crissa", "session_header": ""})
    monkeypatch.setattr(eb, "presence_facts_line", lambda *a, **k: "")
    monkeypatch.setattr(eb, "_today_memory_bullets", lambda: "")
    monkeypatch.setattr(eb, "claim_checked", lambda content, **k: content)
    eb.crissa_reply("what LLM are you?", person_id="o", presence={}, level="bau", user_voice=True)
    assert "[Context from your interrupted spoken reply]" not in sent["p"]["messages"][0]["content"]
    assert eb._READING_STATE["drop_note"] is False                            # used once, then gone
