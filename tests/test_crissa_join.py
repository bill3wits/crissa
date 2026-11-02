"""The join (2026-10-06): endpoint offers with the key referenced, never copied; no hidden limits on a reply;
the talk settings; one speech lane where an announcement queues behind an answer instead of cutting it.

Everything runs against the loopback (a stdlib server that streams like an agent gateway) and fakes for the
speaker, so the suite needs no voice, no model and no network.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_agent as za  # noqa: E402
import crissa_speechq as sq  # noqa: E402
import crissa_talk as zt  # noqa: E402


class _Gateway(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).seen.append({"headers": dict(self.headers), "body": body})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        frames = [
            'data: {"choices":[{"delta":{"content":"First sentence here. "}}]}',
            "event: tool.progress\ndata: {\"tool\":\"search\",\"status\":\"running\"}",
            'data: {"progress":"still working"}',
            'data: {"choices":[{"delta":{"content":"Second one follows!"}}]}',
            "data: [DONE]",
        ]
        for f in frames:
            self.wfile.write((f + "\n\n").encode())
            self.wfile.flush()


@pytest.fixture()
def gateway():
    _Gateway.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Gateway)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d/v1" % srv.server_address[1]
    srv.shutdown()


def _offer(home: Path, oid: str, **o) -> Path:
    d = home / "endpoints.d"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (oid + ".json")
    p.write_text(json.dumps(o))
    return p


# ---- offers -------------------------------------------------------------------------------------------------
def test_an_offer_is_listed_with_where_its_key_lives_and_never_the_key(tmp_path):
    keyfile = tmp_path / "agent.env"
    keyfile.write_text("OTHER=1\nGATEWAY_KEY=s3cret-value-123456\n")
    _offer(tmp_path, "agent-crissa", name="Agent Crissa", url="http://127.0.0.1:9/v1", session_header="X-Session-Id",
           system_prompt="", key_ref={"file": str(keyfile), "name": "GATEWAY_KEY"}, by="helper")
    (tmp_path / "endpoints.d" / "broken.json").write_text("{nope")
    rows = {r["id"]: r for r in za.offers(tmp_path)}
    good = rows["agent-crissa"]
    assert good["ok"] and good["has_key"] and good["no_prompt"] and good["name"] == "Agent Crissa"
    assert good["key_ref"] == {"file": str(keyfile), "name": "GATEWAY_KEY"}
    assert "s3cret" not in json.dumps(rows)
    assert rows["broken"]["ok"] is False and "unreadable" in rows["broken"]["error"]


def test_adopting_an_offer_stores_the_reference_and_the_key_is_read_when_asked(tmp_path, gateway):
    keyfile = tmp_path / "agent.env"
    keyfile.write_text("export GATEWAY_KEY='s3cret-value-123456'\n")
    _offer(tmp_path, "agent-crissa", name="Agent Crissa", url=gateway, session_header="X-Session-Id", system_prompt="",
           key_ref={"file": str(keyfile), "name": "GATEWAY_KEY"})
    za.save({"url": "http://other.example/v1", "key": "typed-old-key"}, tmp_path)
    za.adopt("agent-crissa", tmp_path)
    raw = (tmp_path / "agent.json").read_text()
    assert "s3cret" not in raw and "typed-old-key" not in raw and "GATEWAY_KEY" in raw
    assert stat.S_IMODE(os.stat(tmp_path / "agent.json").st_mode) == 0o600
    cfg = za.load(tmp_path, env={})
    assert cfg["key"] == "s3cret-value-123456" and cfg["no_prompt"] is True
    red = za.redacted(cfg)
    assert red["has_key"] and "key" not in red and red["key_ref"]["name"] == "GATEWAY_KEY"
    za.ask("hi", cfg, session_id="s-1")
    sent = _Gateway.seen[-1]
    assert sent["headers"]["Authorization"] == "Bearer s3cret-value-123456"
    assert sent["headers"]["X-Session-Id"] == "s-1"
    assert [m["role"] for m in sent["body"]["messages"]] == ["user"]      # no system prompt: the agent has its own
    assert "max_tokens" not in sent["body"]                               # no cap of ours on the answer


def test_a_reference_never_follows_a_new_host(tmp_path):
    _offer(tmp_path, "a", url="http://127.0.0.1:9/v1", key_ref={"env": "SOME_KEY"})
    za.adopt("a", tmp_path)
    za.save({"url": "http://elsewhere.example/v1"}, tmp_path)
    assert "key_ref" not in json.loads((tmp_path / "agent.json").read_text())


def test_bad_offers_are_refused(tmp_path):
    _offer(tmp_path, "rel", url="http://127.0.0.1:9/v1", key_ref={"file": "relative/path", "name": "K"})
    _offer(tmp_path, "nourl", name="x")
    rows = {r["id"]: r for r in za.offers(tmp_path)}
    assert not rows["rel"]["ok"] and "absolute" in rows["rel"]["error"]
    assert not rows["nourl"]["ok"]
    with pytest.raises(ValueError):
        za.adopt("rel", tmp_path)
    with pytest.raises(ValueError):
        za.adopt("../agent", tmp_path)


def test_the_stream_hands_over_pieces_and_working_frames(tmp_path, gateway):
    pieces, events = [], []
    out = za.ask("hi", {"url": gateway}, on_piece=pieces.append, on_event=lambda n, d: events.append(n))
    assert out == "First sentence here. Second one follows!"
    assert pieces == ["First sentence here. ", "Second one follows!"]
    assert events == ["tool.progress", "working"]


# ---- no hidden limits -----------------------------------------------------------------------------------------
def test_nothing_is_cut_and_nothing_times_out_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(zt, "HOME", tmp_path)
    long = "Word. " * 400
    assert za.speakable(long) == long.strip()
    assert za.MAX_REPLY_CHARS == 0
    assert zt.load(tmp_path, env={})["reply"]["timeout_s"] == 0
    monkeypatch.setattr(zt, "load", lambda *a, **k: {"reply": {"timeout_s": 0}})
    assert za.reply_timeout() is None


def test_a_set_limit_says_so_and_keeps_the_whole_text(tmp_path):
    text = "One sentence here. Two sentence here. Three sentence here."
    kept, note = zt.keep(text, 30, "reply", tmp_path)
    assert kept == "One sentence here."
    assert note["kept"] == len(kept) and note["total"] == len(text)
    assert Path(note["full_text"]).read_text() == text and "whole text" in note["note"]
    assert zt.keep(text, 0, "reply", tmp_path) == (text, None)


# ---- talk settings ------------------------------------------------------------------------------------------------
def test_settings_layer_shipped_then_home_then_environment(tmp_path):
    zt.save({"ear": {"wake": ["crissa"], "follow_s": 30}, "announce": {"quiet_hours": {"from": 22, "to": 8}}}, tmp_path)
    cfg = zt.load(tmp_path, env={"CRISSA_REPLY_MAX_CHARS": "500"})
    assert cfg["ear"]["wake"] == ["crissa"] and cfg["ear"]["follow_s"] == 30 and cfg["ear"]["gather_s"] == 1.2
    assert cfg["reply"]["max_chars"] == 500
    with pytest.raises(ValueError):
        zt.save({"ear": {"nope": 1}}, tmp_path)
    with pytest.raises(ValueError):
        zt.save({"speak_tuning": {"tempo": 9}}, tmp_path)


def test_quiet_hours_wrap_midnight():
    cfg = {"announce": {"quiet_hours": {"from": 22, "to": 8}}}
    at = lambda h: time.mktime((2026, 10, 6, h, 30, 0, 0, 0, -1))
    assert zt.quiet(cfg, at(23)) and zt.quiet(cfg, at(3)) and not zt.quiet(cfg, at(12))
    assert not zt.quiet({"announce": {"quiet_hours": None}}, at(3))


def test_speak_tuning_becomes_speaker_variables_unless_the_unit_pinned_one(tmp_path):
    cfg = zt.load(tmp_path, env={})
    assert zt.speak_env(cfg, env={}) == {}                                # shipped: all null, nothing set
    cfg["speak_tuning"].update(tempo=1.2, follow_line=True, tail_starve_ms=1200)
    got = zt.speak_env(cfg, env={"HOUSE_SPEAK_TEMPO": "1.0"})
    assert got == {"HOUSE_SPEAK_FOLLOW_LINE": "1", "HOUSE_SPEAK_TAIL_STARVE_MS": "1200"}


# ---- the speech lane ------------------------------------------------------------------------------------------------
class _Proc:
    def __init__(self, text):
        self.text, self.rc = text, None

    def poll(self):
        return self.rc


class _Speaker:
    def __init__(self):
        self.lines, self.stops = [], 0

    def spawn(self, text, who):
        p = _Proc(text)
        self.lines.append(p)
        return p

    def stop_all(self):
        n = 0
        for p in self.lines:
            if p.rc is None:
                p.rc = -15
                n += 1
        self.stops += 1
        return n

    def pending(self):
        return sum(1 for p in self.lines if p.rc is None)

    def finish_all(self):
        for p in self.lines:
            if p.rc is None:
                p.rc = 0


def _lane(tmp_path, **cfg):
    now = [1000.0]
    s = zt.load(tmp_path, env={})
    s["announce"].update(speech_pause_s=0, **cfg)
    sp = _Speaker()
    lane = sq.Lane(sp.spawn, sp.stop_all, sp.pending, home=tmp_path, settings=lambda: s, clock=lambda: now[0],
                   autostart=False)
    return lane, sp, now, s


def test_an_announcement_queues_behind_an_answer_and_never_cuts_it(tmp_path):
    lane, sp, now, _s = _lane(tmp_path)
    lane.begin_turn()
    lane.say("First sentence of the answer.", "crissa")
    assert lane.announce("Done. Card one.", "completion")["queued"]
    assert lane.step().startswith("hold")
    lane.say("Second sentence.", "crissa", queue=True)
    assert lane.step() == "talk"
    assert [p.text for p in sp.lines] == ["First sentence of the answer.", "Second sentence."]
    assert all(p.rc is None for p in sp.lines)                            # nothing was cut
    sp.finish_all()
    assert lane.step().startswith("hold")                                 # the answer is still streaming
    lane.end_turn()
    assert lane.step() == "announce" and sp.lines[-1].text == "Done. Card one."


def test_talk_interrupts_an_announcement_which_goes_back_to_the_front_once(tmp_path):
    lane, sp, now, _s = _lane(tmp_path)
    lane.announce("Done. Card one.", "completion")
    assert lane.step() == "announce"
    lane.say("An answer.", "crissa")
    assert sp.lines[0].rc == -15 and lane.ann[0]["text"] == "Done. Card one." and lane.ann[0]["requeued"]


def test_holds_for_the_conversation_and_for_someone_talking(tmp_path):
    lane, sp, now, s = _lane(tmp_path)
    comms = [True]
    lane.comms_open = lambda: comms[0]
    lane.announce("Done. A.", "completion")
    assert "conversation" in lane.step()
    comms[0] = False
    lane.mic_busy = lambda pause: "a voice 2 s ago"
    assert "someone is talking" in lane.step()
    lane.mic_busy = lambda pause: None
    s["announce"]["speech_pause_s"] = 5
    lane.last_talk_end = now[0] - 1
    assert "just spoke" in lane.step()
    now[0] += 10
    assert lane.step() == "announce"


def test_overflow_roundup_dedup_quiet_and_max_wait(tmp_path):
    lane, sp, now, s = _lane(tmp_path, queue_max=3, roundup_over=2)
    assert lane.announce("Done. A card.", "completion", dedup_key="c:a")["queued"]
    assert lane.announce("Done. A card again.", "completion", dedup_key="c:a")["reason"] == "duplicate"
    for t in ("Done. B.", "Stuck. C.", "Done. D."):
        lane.announce(t, "completion" if t.startswith("Done") else "stuck")
    assert [i["text"] for i in lane.ann] == ["Done. B.", "Stuck. C.", "Done. D."]   # A fell off the front
    assert lane.step() == "announce" and sp.lines[-1].text.startswith("Roundup, 3 updates.")
    log = [json.loads(x) for x in (tmp_path / "announce-log.jsonl").read_text().splitlines()]
    assert {r["event"] for r in log} >= {"queued", "skipped", "dropped", "summarised", "roundup"}
    s["announce"]["quiet_hours"] = {"from": 0, "to": 23}
    assert lane.announce("Done. Late.", "completion")["reason"] == "quiet hours"
    s["announce"]["quiet_hours"] = None
    sp.finish_all()
    lane.announce("Done. Old.", "completion", max_wait_s=5)
    lane.comms_open = lambda: True
    now[0] += 6
    assert lane.step() == "dropped" and not lane.ann


def test_stop_clears_talk_and_keeps_announcements(tmp_path):
    lane, sp, now, _s = _lane(tmp_path)
    lane.say("Talking.", "crissa")
    lane.say("More.", "crissa", queue=True)
    lane.announce("Done. Waits.", "completion")
    assert lane.stop() == 1 and not lane.talk and len(lane.ann) == 1


def test_sentences_stream_out_and_hold_open_blocks():
    s = sq.Sentences(12)
    assert s.feed("Hello there friend. This is a test! And") == ["Hello there friend.", "This is a test!"]
    assert s.feed(" more <think>plan. steps. ") == []
    assert s.feed("</think> then done. Tail") == ["And more   then done."]
    assert s.flush() == "Tail"


def test_stop_moves_hush_so_a_streaming_reply_says_no_more(tmp_path):
    lane, _sp, _now, _s = _lane(tmp_path)
    h = lane.hush
    lane.say("First line.", "crissa")
    lane.stop()
    assert lane.hush == h + 1 and not lane.talk
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    body = src[src.index("def connect_ask("):]
    assert 'hush0 = getattr(LANE, "hush", 0)' in body and '!= hush0' in body


def test_the_conductor_routes_and_the_glass_says_when_something_was_cut():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    for head in ('if head == "/v1/announce"', 'if head == "/v1/stop"', 'if head.startswith("/v1/cut/")'):
        block = src.split(head, 1)[1][:900]
        assert "connect_origin_ok" in block or head == 'if head == "/v1/announce"' and "LANE.status" in block, head
    assert src.count('if head == "/v1/announce"') == 2              # GET status, POST queue (loopback)
    note_cut = src.split("def note_cut", 1)[1].split("\ndef ", 1)[0]
    assert '"last_cut"' in src and '"full_text":' not in note_cut and '"name": Path(note.get("full_text")' in note_cut
    page = (ROOT / "display" / "crissa-wall.html").read_text()
    assert "s.last_cut" in page and '"/v1/cut/" + encodeURIComponent(cut.name)' in page and "Show all" in page


def test_connect_shows_detected_offers_with_one_click_use_then_test():
    page = (ROOT / "display" / "crissa-connect.html").read_text()
    assert 'id="offers-box"' in page and 'id="no_prompt"' in page
    use = page.split("async function useOffer", 1)[1].split("\n}\n", 1)[0]
    assert 'post("/v1/agent", { adopt: o.id' in use and 'post("/v1/agent/test", {})' in use
    assert "no_prompt: $(\"no_prompt\").checked" in page
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    test_block = src.split('if head == "/v1/agent/test":', 1)[1][:1200]
    assert 'k != "key_ref"' in test_block, "a page never names where a key is read from"
    save_block = src.split('if head == "/v1/agent":', 1)[1].split('if head == "/v1/agent/test":', 1)[0]
    assert '"agent": crissa_agent.redacted(crissa_agent.load())' in save_block, "Use shows has_key from key_ref"
    assert '{k: v for k, v in body.items() if k != "key_ref"}' in src
