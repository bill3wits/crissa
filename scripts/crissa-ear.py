#!/usr/bin/env python3
"""The pack's ear: what a person says becomes a question to the connected agent.

The plan's Phase 1 (2026-09-28, landed 2026-10-01): Connect lets the player answer through any
OpenAI-compatible agent, and `POST /v1/ask` is the typed path. This is the spoken one. It reads
turns from any speech-to-text service that serves them as JSON and hands each new one to the
player's own `/v1/ask`, so the agent, the voice and the mouth are all the player's - this file
holds no key and talks to no agent.

Turns: `GET <turns url>` answers `{"turns": [{"text": ..., "ts": ...}, ...]}` (or a bare list).
Order does not matter; a turn is new once, by its ts + text. What is already there when the ear
starts is history, never a question.

Her own voice: a turn that arrives while she is speaking, or while the agent is still thinking,
is dropped - otherwise the microphone hears her reply and she answers herself. `--tail` keeps
the ear shut a little after her line ends (a room echoes).

Wake word: none by default (the desk is hers). `--wake crissa` answers only turns that name her;
`--variants` adds what a speech-to-text service hears instead (measured on the pack's ARK-ASR, 2026-10-10:
"chrissa, chrisa, krisa, prisa, chrysa"; modules/ear/crissa_bridge/wake.py CRISSA_HEARD has the whole set).

Barge-in (2026-10-06): a turn that STARTS with her wake word while she is speaking or thinking is a
person cutting in, not her echo: the ear stops her (`POST /v1/stop`, or `/v1/say {"stop": true}` on an
older player) and asks it. A turn that only repeats her own reply is still her echo.

Follow-up (2026-10-06): after she answers, a follow-up needs no wake word for `follow_s` seconds once
her line ends. While that window is open the ear tells the player the conversation is on
(`POST /v1/comms {"on": true}`, off when it closes); naming her calls `POST /v1/vocative`. Both are
body cues, off with `--no-body-cues`.

Timing: config/crissa-talk.json "ear", under <crissa home>/talk.json (Init writes it), under the env
and flags below.

    scripts/crissa-ear.py --turns http://127.0.0.1:8190/v1/turns [--player http://127.0.0.1:8100]

Env: CRISSA_EAR_TURNS, CRISSA_PLAYER, CRISSA_EAR_WAKE (comma list), CRISSA_EAR_VARIANTS (comma list),
CRISSA_EAR_POLL_S, CRISSA_EAR_FOLLOW_S, CRISSA_EAR_BARGE (0/1).
Stop with Ctrl-C. Exit 2 when no turns url is given.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import crissa_egress  # noqa: E402
import crissa_talk  # noqa: E402  - the ear's timing and words (config/crissa-talk.json "ear", 2026-10-06)

# A MAC'S SYSTEM PROXY NEVER CARRIES A LOOPBACK CALL (2026-10-04): urllib applies macOS's proxy settings to
# 127.0.0.1. Any *_proxy in the environment replaces the system settings outright on macOS (urllib reads the
# environment first), so a Mac that needs a proxy to go out sets HTTPS_PROXY beside this. Kill CRISSA_NO_PROXY=0.
if sys.platform == "darwin" and os.environ.get("CRISSA_NO_PROXY", "1").strip() != "0":
    for _k in ("NO_PROXY", "no_proxy"):
        os.environ.setdefault(_k, "127.0.0.1,localhost")


GATHER_S = 1.2     # turns this close together are one question (people pause mid-sentence)
SEEN_CAP = 2048    # bound Ear.seen so a long-running ear cannot grow forever


def _get(url: str, timeout: float = 4.0):
    with crissa_egress.urlopen(url, timeout, "the ear") as r:
        return json.loads(r.read().decode("utf-8") or "null")


def _post(url: str, body: dict, timeout: float = 8.0) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with crissa_egress.urlopen(req, timeout, "the ear") as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            return exc.code, {}


def turns_of(data) -> list[dict]:
    """The turns in a feed, oldest first. A feed is {"turns": [...]} or a bare list."""
    rows = data.get("turns") if isinstance(data, dict) else data
    rows = [t for t in (rows or []) if isinstance(t, dict)]
    return sorted(rows, key=lambda t: float(t.get("ts") or 0.0))


def turn_key(t: dict) -> str:
    return "%s|%s" % (t.get("ts"), t.get("text"))


def wake_ok(text: str, wake: list[str]) -> bool:
    if not wake:
        return True
    low = text.lower()
    return any(re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(w.lower()), low) for w in wake)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def wake_first(text: str, wake: list[str], within: int = 3) -> bool:
    """Does the turn START by naming her (within its first few words)? That is a person cutting in."""
    if not wake:
        return False
    return wake_ok(" ".join(_words(text)[:within]), wake)


def echo_of(text: str, said: str) -> bool:
    """Is this turn her own line coming back through the microphone? Most of its words are in what she said."""
    heard, hers = _words(text), set(_words(said))
    if not heard or not hers:
        return False
    return sum(1 for w in heard if w in hers) >= max(2, int(len(heard) * 0.7 + 0.5))


def _env_float(name: str):
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return None


def settings(env: dict | None = None) -> dict:
    """The ear's settings: config/crissa-talk.json "ear" under <crissa home>/talk.json under the env."""
    env = os.environ if env is None else env
    try:
        ear = dict(crissa_talk.load()["ear"])
    except (OSError, ValueError, KeyError, TypeError):
        ear = dict(crissa_talk.FALLBACK["ear"])
    for key, name in (("wake", "CRISSA_EAR_WAKE"), ("variants", "CRISSA_EAR_VARIANTS")):
        if env.get(name, "").strip():
            ear[key] = [w.strip() for w in env[name].split(",") if w.strip()]
    for key, name in (("poll_s", "CRISSA_EAR_POLL_S"), ("follow_s", "CRISSA_EAR_FOLLOW_S")):
        try:
            ear[key] = float(env[name])
        except (KeyError, ValueError):
            pass
    if env.get("CRISSA_EAR_BARGE", "").strip():
        ear["barge"] = env["CRISSA_EAR_BARGE"].strip().lower() not in ("0", "off", "false", "no")
    return ear


class Ear:
    """One poll at a time, so a test can drive it without threads or sleeps."""

    def __init__(self, turns_url: str, player: str, *, wake: list[str] | None = None,
                 variants: list[str] | None = None, tail_s: float = 1.5, gather_s: float = GATHER_S,
                 follow_s: float = 0.0, barge: bool = False, body_cues: bool = False, log=print):
        self.turns_url = turns_url
        self.player = player.rstrip("/")
        self.wake = [w for w in (wake or []) if w]
        self.names = self.wake + [w for w in (variants or []) if w and w not in self.wake]
        self.tail_s = tail_s
        self.gather_s = gather_s
        self.follow_s = max(0.0, float(follow_s or 0.0))
        self.barge = bool(barge)
        self.body_cues = bool(body_cues)
        self.log = log
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.buf: list[str] = []
        self.last_heard = 0.0
        self.quiet_from = 0.0      # the ear opens again at this time
        self.follow_until = 0.0    # a follow-up needs no wake word until this time
        self.waiting = False       # asked; the follow-up window runs from the end of her answer
        self.comms = False         # told the player the conversation is on
        self.said = ""             # her last reply, so her echo is not a barge-in

    def _cue(self, path: str, body: dict) -> None:
        if not self.body_cues:
            return
        try:
            code, out = _post(self.player + path, dict(body, by="ear"), timeout=3.0)
        except (OSError, ValueError) as exc:
            self.log("ear: %s not reachable (%s)" % (path, exc))
            return
        if code >= 300:
            self.log("ear: %s refused (%s): %s" % (path, code, out.get("error") or out))

    def _comms(self, on: bool, why: str) -> None:
        if on != self.comms:
            self.comms = on
            self._cue("/v1/comms", {"on": on, "why": why})

    def stop_her(self) -> int:
        """Barge-in: stop what she is saying (and the rest of her reply). Returns the HTTP code."""
        code, _ = _post(self.player + "/v1/stop", {"by": "ear"}, timeout=3.0)
        if code == 404:            # a player from before /v1/stop
            code, _ = _post(self.player + "/v1/say", {"stop": True, "who": "ear"}, timeout=3.0)
        self.log("ear: barge-in - stopped her (%s)" % code)
        return code

    def following(self, now: float) -> bool:
        return bool(self.follow_s) and (self.waiting or now < self.follow_until)

    def _note(self, key: str) -> bool:
        """Remember a turn key. True when new. Oldest keys drop once SEEN_CAP is hit."""
        if key in self.seen:
            return False
        self.seen[key] = None
        while len(self.seen) > SEEN_CAP:
            self.seen.popitem(last=False)
        return True

    def seed(self) -> int:
        """Everything already in the feed is history."""
        for t in turns_of(_get(self.turns_url)):
            self._note(turn_key(t))
        return len(self.seen)

    def her_turn(self, now: float) -> bool:
        """Is she speaking or thinking? An unreadable player counts as busy (never talk over her)."""
        try:
            on_air = bool(_get(self.player + "/v1/state", timeout=2.0).get("on_air"))
            agent = _get(self.player + "/v1/agent", timeout=2.0)
            busy = int(agent.get("busy") or 0) > 0
        except (OSError, ValueError, AttributeError):
            return True
        last = agent.get("last") if isinstance(agent, dict) else None
        if isinstance(last, dict) and last.get("reply"):
            self.said = str(last["reply"])
        if on_air or busy:
            self.quiet_from = now + self.tail_s
            if self.waiting:           # the follow-up window runs from the end of her answer
                self.follow_until = self.quiet_from + self.follow_s
            return True
        if self.waiting and now >= self.quiet_from:
            self.waiting = False
            self.follow_until = max(self.follow_until, self.quiet_from + self.follow_s)
        return now < self.quiet_from

    def poll(self, now: float | None = None) -> dict | None:
        """Read the feed once; ask when a gathered question has gone quiet. Returns what was sent."""
        now = time.time() if now is None else now
        fresh, cut_in = [], []
        for t in turns_of(_get(self.turns_url)):
            k = turn_key(t)
            if not self._note(k):
                continue
            text = str(t.get("text") or "").strip()
            if not text:
                continue
            if self.barge and wake_first(text, self.names):
                cut_in.append(text)                 # naming her first: may cut in, even over her voice
            elif not t.get("her_voice"):            # the voice module marks what it heard while she spoke
                fresh.append(text)
        if cut_in or fresh:
            if self.her_turn(now):
                barge = [t for t in cut_in if not echo_of(t, self.said)]
                if not barge:
                    self.log("ear: dropped while she speaks: %r" % " ".join(cut_in + fresh)[:120])
                    self.buf = []
                    return None
                self.stop_her()
                self.quiet_from = 0.0
                self.buf = barge + fresh
            else:
                self.buf += cut_in + fresh
            self.last_heard = now
        elif self.waiting:
            self.her_turn(now)                      # has her answer ended? the follow-up window starts then
        if self.comms and not self.following(now) and not self.buf:
            self._comms(False, "follow-up window over")
        if not self.buf or now - self.last_heard < self.gather_s:
            return None
        text = " ".join(self.buf)
        self.buf = []
        follow = self.following(now)
        if not follow and not wake_ok(text, self.names):
            self.log("ear: not for her (no wake word): %r" % text[:120])
            return None
        if self.names and not follow:
            self._cue("/v1/vocative", {})           # she was named: look up
        if self.follow_s:
            self._comms(True, "asked by voice")
        code, body = _post(self.player + "/v1/ask", {"text": text, "by": "ear"})
        if code == 409 and body.get("connect"):
            self.log("ear: no agent connected - open %s/connect" % self.player)
        elif code >= 300:
            self.log("ear: ask refused (%s): %s" % (code, body.get("error") or body))
        else:
            self.log("ear: asked %r" % text[:120])
        self.quiet_from = now + self.tail_s
        self.waiting = bool(self.follow_s) and code < 300
        self.follow_until = now + self.follow_s if self.waiting else self.follow_until
        sent = {"text": text, "code": code}
        if self.follow_s:
            sent["follow"] = follow
        return sent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--turns", default=os.environ.get("CRISSA_EAR_TURNS", ""),
                    help="the speech-to-text service's turns url (JSON)")
    ap.add_argument("--player", default=os.environ.get("CRISSA_PLAYER", "http://127.0.0.1:8100"))
    cfg = settings()
    ap.add_argument("--wake", default=",".join(cfg.get("wake") or []),
                    help="comma list; answer only turns that name one (default: every turn)")
    ap.add_argument("--variants", default=",".join(cfg.get("variants") or []),
                    help="comma list of what speech-to-text hears for her name")
    ap.add_argument("--poll", type=float, default=float(cfg.get("poll_s") or 0.3))
    ap.add_argument("--tail", type=float, default=float(cfg.get("tail_s") or 1.5),
                    help="seconds the ear stays shut after her line")
    ap.add_argument("--gather", type=float, default=float(cfg.get("gather_s") or GATHER_S),
                    help="turns this close together are one question")
    ap.add_argument("--follow", type=float, default=float(cfg.get("follow_s") or 0.0),
                    help="seconds after her answer a follow-up needs no wake word (0 = off)")
    ap.add_argument("--no-barge", dest="barge", action="store_false", default=bool(cfg.get("barge", True)),
                    help="never stop her when someone names her mid-line")
    ap.add_argument("--no-body-cues", dest="body_cues", action="store_false",
                    default=bool(cfg.get("body_cues", True)), help="no /v1/comms or /v1/vocative calls")
    a = ap.parse_args(argv)
    if not a.turns:
        print("crissa-ear: give --turns <url> (or CRISSA_EAR_TURNS): a speech-to-text service's turns",
              file=sys.stderr)
        return 2
    for url in (a.turns, a.player):
        if not crissa_egress.allowed(url):
            print("crissa-ear: kill-search is on and %s is outside your network" % url, file=sys.stderr)
            return 2
    ear = Ear(a.turns, a.player, wake=[w.strip() for w in a.wake.split(",")],
              variants=[w.strip() for w in a.variants.split(",")], tail_s=a.tail, gather_s=a.gather,
              follow_s=a.follow, barge=a.barge, body_cues=a.body_cues)
    while True:
        try:
            print("crissa-ear: %d turns already heard are history; listening" % ear.seed(), flush=True)
            break
        except (OSError, ValueError) as exc:
            print("crissa-ear: turns not readable yet (%s)" % exc, file=sys.stderr, flush=True)
            time.sleep(2.0)
    try:
        while True:
            try:
                ear.poll()
            except (OSError, ValueError) as exc:
                print("crissa-ear: %s" % exc, file=sys.stderr, flush=True)
                time.sleep(1.0)
            time.sleep(a.poll)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
