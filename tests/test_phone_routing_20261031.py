"""The phone's routing (house-rails-6a 2026-10-31; the User: "Nearly all the time we drop to multitasking for conversation.
There you make the flourish loops for down working"): the page's own choose() run under node against stub shows.

slide_pick (CRISSA_PHONE_SLIDE_PICK): idle at the talk hub picks at random among the slides that leave it.
talk_down (CRISSA_PHONE_TALK_DOWN): a line plays where she is on a painted hub; the User speaking wakes her up; the first
tool call takes the conversation down (now when the way is painted, else when the line ends) and it stays down until it
goes quiet; an unpainted hub never hosts a line. Both OFF = the page as before."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "display" / "phone-app" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _fn(name: str) -> str:
    """One top-level function of app.js, by brace matching."""
    i = APP.index("\nfunction %s(" % name) + 1
    j = APP.index("{", i)
    depth = 0
    for k in range(j, len(APP)):
        depth += {"{": 1, "}": -1}.get(APP[k], 0)
        if depth == 0:
            return APP[i:k + 1]
    raise AssertionError(name)


HARNESS = r"""
let T = 0; const performance = { now: () => T };
let RND = [0.99]; Math.random = () => (RND.length > 1 ? RND.shift() : RND[0]);
const $ = () => ({ disabled: false });
const hasMouths = (m) => !!(m && m.cells && Object.keys(m.cells).length);
let IDLE_UP_S = 35; const DOWN_MIN_S = 18, DOWN_MAX_S = 45, FLOURISH_P = 0.35, UP_FLOURISH_MIN_S = 20, TALK_FLOURISH_P = 0.3;
let show, packs, clip, line = null, waiting = null, wantUp = false, toolDown = false, secure = false, lastFlourish = "";
let lastTalk = 0, enteredAt = 0, actx = { currentTime: 0 };
let routing = { slide_pick: false, talk_down: false }, convDown = false;
%(fns)s
const cases = JSON.parse(process.argv[process.argv.length - 1]), out = [];
for (const c of cases) {
  show = c.show; packs = c.packs; clip = c.clip; routing = Object.assign({ slide_pick: false, talk_down: false }, c.routing || {});
  line = c.line ? { start: 0, sheet: { dur: c.line_left || 10 } } : null; waiting = null;
  wantUp = !!c.wantUp; toolDown = !!c.toolDown; convDown = !!c.convDown; lastFlourish = "";
  T = (c.now_s || 1) * 1000; lastTalk = (c.last_talk_s || (c.now_s || 1)) * 1000; enteredAt = (c.entered_s || 0) * 1000;
  actx.currentTime = 0; RND = c.rnd === undefined ? [0.99] : [].concat(c.rnd);
  const k = choose();
  out.push({ next: k, convDown, wantUp, toolDown });
}
console.log(JSON.stringify(out));
"""

PAINT = {"cells": {"AH": [0, 0]}, "n": 120, "fps": 24}
FILM = {"cells": {}, "n": 120, "fps": 24}


def show3(down_painted: bool = True, settle_painted: bool = False) -> tuple[dict, dict]:
    """Lila's three hubs: up (talk), up-front (side), down (tool = multitask)."""
    clips = {"listen": {"role": "loop", "start": "up", "end": "up"},
             "settle": {"role": "slide", "start": "up", "end": "down"},
             "hair-back-to-front": {"role": "slide", "start": "up", "end": "up-front"},
             "listen-front": {"role": "loop", "start": "up-front", "end": "up-front"},
             "hair-front-to-back": {"role": "slide", "start": "up-front", "end": "up"},
             "wake": {"role": "slide", "start": "down", "end": "up"},
             "scroll": {"role": "loop", "start": "down", "end": "down"},
             "multitask": {"role": "flourish", "start": "down", "end": "down"},
             "fl-laugh-held": {"role": "flourish", "start": "down", "end": "down"}}
    packs = {k: dict(PAINT) for k in clips}
    for k in ("settle", "wake", "hair-back-to-front", "hair-front-to-back"):
        packs[k] = dict(FILM)
    if settle_painted:
        packs["settle"] = dict(PAINT)
    if not down_painted:
        for k in ("scroll", "multitask", "fl-laugh-held"):
            packs[k] = dict(FILM)
    return {"talk": "listen", "talk_hub": "up", "tool": "multitask", "clips": clips}, packs


def run(cases: list[dict]) -> list[dict]:
    fns = "\n".join(_fn(n) for n in ("clipsWhere", "loopOn", "slide", "painted", "clipS", "lineLeftS", "choose"))
    js = HARNESS % {"fns": fns}
    r = subprocess.run([NODE, "-e", js, json.dumps(cases)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def case(**kw) -> dict:
    sh, pk = show3(kw.pop("down_painted", True), kw.pop("settle_painted", False))
    return dict({"show": sh, "packs": pk}, **kw)


needs_node = pytest.mark.skipif(NODE is None, reason="no node on this machine")


@needs_node
def test_off_idle_at_the_talk_hub_takes_the_first_slide_as_before():
    got = run([case(clip="listen", now_s=100, last_talk_s=1, rnd=0.1), case(clip="listen", now_s=100, last_talk_s=1, rnd=0.99)])
    assert [g["next"] for g in got] == ["settle", "listen"], "rnd 0.1 < 0.5 leaves by the first slide; 0.99 stays"
    got = run([case(clip="listen", now_s=100, last_talk_s=1, rnd=0.45)])
    assert got[0]["next"] == "settle"


@needs_node
def test_slide_pick_reaches_every_slide_that_leaves_the_talk_hub():
    # two draws: the idle gate (< 0.5 leaves), then the pick among the slides
    picks = {run([case(clip="listen", now_s=100, last_talk_s=1, rnd=r, routing={"slide_pick": True})])[0]["next"]
             for r in ([0.1, 0.1], [0.1, 0.9])}
    assert picks == {"settle", "hair-back-to-front"}


@needs_node
def test_off_a_line_at_down_wakes_her_up_as_before():
    got = run([case(clip="scroll", line=True)])
    assert got[0]["next"] == "wake"


@needs_node
def test_talk_down_play_order_greeting_down_then_up_to_listen():
    # a new conversation from idle: her greeting plays where she is (down, painted)
    g1 = run([case(clip="scroll", line=True, routing={"talk_down": True})])[0]
    assert g1["next"] == "scroll" and not g1["convDown"]
    # the User speaks: she wakes up to listen formally
    g2 = run([case(clip="scroll", wantUp=True, routing={"talk_down": True})])[0]
    assert g2["next"] == "wake"


@needs_node
def test_talk_down_the_first_tool_call_takes_the_conversation_down_and_it_stays():
    # at up, a tool ran, no line: down now, and the conversation is down
    g = run([case(clip="listen", toolDown=True, routing={"talk_down": True})])[0]
    assert g["next"] == "settle" and g["convDown"]
    # down: the User speaks again and she answers - she stays down
    g = run([case(clip="scroll", wantUp=True, line=True, convDown=True, routing={"talk_down": True}, rnd=0.99)])[0]
    assert g["next"] == "scroll"
    # a painted down flourish may carry her line
    g = run([case(clip="scroll", line=True, line_left=30, convDown=True, routing={"talk_down": True}, rnd=0.01)])[0]
    assert g["next"] in ("multitask", "fl-laugh-held")
    # quiet past IDLE_UP_S: the conversation is over (and the stale wantUp with it); back up on the idle rule
    g = run([case(clip="scroll", convDown=True, wantUp=True, now_s=100, last_talk_s=1, entered_s=0, routing={"talk_down": True}, rnd=0.99)])[0]
    assert not g["convDown"] and not g["wantUp"] and g["next"] == "wake"
    # still inside the conversation (quiet for less than IDLE_UP_S): she stays down
    g = run([case(clip="scroll", convDown=True, wantUp=True, now_s=100, last_talk_s=90, entered_s=0, routing={"talk_down": True}, rnd=0.99)])[0]
    assert g["convDown"] and g["next"] == "scroll"


@needs_node
def test_talk_down_a_mid_line_tool_call_waits_for_an_unpainted_slide_and_goes_on_a_painted_one():
    g = run([case(clip="listen", toolDown=True, line=True, routing={"talk_down": True})])[0]
    assert g["next"] == "listen" and g["toolDown"] and not g["convDown"], "settle is film: wait for the line to end"
    g = run([case(clip="listen", toolDown=True, line=True, settle_painted=True, routing={"talk_down": True})])[0]
    assert g["next"] == "settle" and g["convDown"], "a painted way down: go now"
    g = run([case(clip="listen", toolDown=True, line=True)])[0]
    assert g["next"] == "listen", "off: never mid-line, as before"


@needs_node
def test_talk_down_an_unpainted_down_never_hosts_a_line():
    g = run([case(clip="scroll", line=True, down_painted=False, routing={"talk_down": True})])[0]
    assert g["next"] == "wake"
    g = run([case(clip="scroll", line=True, convDown=True, down_painted=False, routing={"talk_down": True})])[0]
    assert g["next"] == "wake"


def _srv():
    spec = importlib.util.spec_from_file_location("phone_app_server_routing", ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_server_hands_the_switches_and_the_dial(monkeypatch):
    """ON in a pack, never on the house (the User 2026-10-31: "put those on"); =0 kills each"""
    m = _srv()
    import crissa_home
    for k in ("CRISSA_PHONE_SLIDE_PICK", "CRISSA_PHONE_TALK_DOWN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    r = m._routing()
    assert r["slide_pick"] is True and r["talk_down"] is True, "ON in a pack"
    assert r["idle_up_s"] == json.loads((ROOT / "config" / "phone-routing.json").read_text())["idle_up_s"]
    monkeypatch.setenv("CRISSA_PHONE_SLIDE_PICK", "0")
    monkeypatch.setenv("CRISSA_PHONE_TALK_DOWN", "0")
    r = m._routing()
    assert not r["slide_pick"] and not r["talk_down"], "=0 kills each"
    monkeypatch.delenv("CRISSA_PHONE_SLIDE_PICK")
    monkeypatch.delenv("CRISSA_PHONE_TALK_DOWN")
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    r = m._routing()
    assert not r["slide_pick"] and not r["talk_down"], "never on the house"
    src = (ROOT / "scripts" / "phone-app-server.py").read_text()
    assert '"routing": _routing()})' in src


def test_the_page_reads_the_routing_and_keeps_its_test_overrides():
    assert "if (r.routing && typeof r.routing === \"object\") routing = Object.assign(routing, r.routing);" in APP
    assert 'if (!Q.has("idle") && typeof routing.idle_up_s === "number") IDLE_UP_S = routing.idle_up_s;' in APP
