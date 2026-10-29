"""One speech lane for everything she says (2026-10-06): answers, plain lines and announcements.

WHY. Every line used to be "the newest wins": each new line stopped the one on the air. That is right for a
conversation (she never talks over herself) and wrong for anything else: a helper's announcement cut her
answer in half. This lane keeps the conversation rule and adds the other two:

  talk      `say(text, who)` stops what is on the air and speaks now (the old rule).
            `say(text, who, queue=True)` waits its turn: the next sentence of a streamed answer, or a plain
            line that must not cut anything. One line is spawned ahead so there is no gap between sentences
            (it waits on the speech lock), never more, so the order holds.
  announce  `announce(text, kind)` queues. A line goes out only when nothing is on the air, no answer is
            being asked or streamed, the conversation is not open, nobody spoke in the last `speech_pause_s`
            and her own last line ended at least that long ago. Same rules as the announcer this was cut
            from: at most `queue_max` wait (the oldest is dropped and logged), more than `roundup_over`
            waiting become one roundup line, a repeated `dedup_key` inside `dedup_s` is skipped, quiet hours
            and the mute file drop instead of queue, and an announcement that talk interrupts goes back to
            the front once. `max_wait_s` > 0 drops a line held that long (logged).
  stop      `stop()` is the hard stop: everything on the air and every queued talk line; queued
            announcements stay.

Every drop, skip, roundup and line said is a row in `<crissa home>/announce-log.jsonl`. Settings come from
lib/crissa_talk.py (`announce` block). Nothing here knows who sent a line.
"""
from __future__ import annotations

import collections
import json
import os
import re
import threading
import time
from pathlib import Path

import crissa_talk

LABEL = {"completion": "Done", "stuck": "Stuck", "protocol": "Protocol", "stage": "Staged"}
MUTE_FILE = "announce-mute"


class Sentences:
    """Cut a streamed reply into speakable sentences as it lands. A sentence shorter than `min_chars` waits
    for the next one; an open <think> block or code fence holds everything until it closes."""

    END = re.compile(r"([.!?\u2026]+[\"')\]]*)(\s+)|(\n\s*\n|\n)")

    def __init__(self, min_chars: int = 12):
        self.min = max(1, int(min_chars or 1))
        self.buf = ""

    def _held(self) -> bool:
        low = self.buf.lower()
        if low.rfind("<think>") > low.rfind("</think>"):
            return True
        return self.buf.count("```") % 2 == 1

    def feed(self, piece: str) -> list[str]:
        self.buf += piece or ""
        if self._held():
            return []
        self.buf = re.sub(r"```[\s\S]*?```", " ", re.sub(r"<think>[\s\S]*?</think>", " ", self.buf, flags=re.I))
        out, start = [], 0
        for m in self.END.finditer(self.buf):
            cut = m.end(1) if m.group(1) else m.start(3)
            cand = self.buf[start:cut].strip()
            if len(cand) >= self.min:
                out.append(cand)
                start = m.end()
        self.buf = self.buf[start:]
        return out

    def flush(self) -> str:
        rest, self.buf = self.buf.strip(), ""
        return rest


# THE LOCK IS NOT HELD ACROSS THE MICROPHONE'S ASK (round 46, the local review of kit crissa-063a2813): step() held the
# queue's lock through hold()'s mic_busy GET to the voice module (up to 0.4 s), so stop() and say() waited behind it and
# a stop landed late. The ask runs before the lock now and hold() reads its answer. Kill CRISSA_SPEECHQ_MIC_UNLOCKED=0.
def mic_unlocked_on() -> bool:
    return os.environ.get("CRISSA_SPEECHQ_MIC_UNLOCKED", "1").strip().lower() not in ("0", "off", "false", "no")


def roundup_line(items: list[dict], limit: int = 160) -> str:
    """One line for everything waiting: the count, then by kind, repeated titles counted once."""
    groups: dict[str, dict[str, int]] = {}
    for it in items:
        kind = LABEL.get(str(it.get("kind") or ""), "Other")
        words = " ".join(str(it.get("text") or "").replace(",", " ").split()).split(" ")
        if words and words[0].rstrip(".").lower() in ("done", "stuck", "protocol", "staged"):
            words = words[1:]
        title = " ".join(words[:6]) or "untitled"
        groups.setdefault(kind, {})
        groups[kind][title] = groups[kind].get(title, 0) + 1
    out, told = "Roundup, %d updates." % len(items), 0
    for kind in ("Stuck", "Done", "Protocol", "Staged", "Other"):
        if kind not in groups:
            continue
        part = " %s: " % kind
        for title, n in sorted(groups[kind].items(), key=lambda tn: -tn[1]):
            add = (part or "; ") + (("%s, times %d" % (title, n)) if n > 1 else title)
            if len(out) + len(add) + 1 > limit - 16:
                break
            out += add
            part = ""
            told += n
        if part == "":
            out += "."
    rest = len(items) - told
    return out + (" And %d more." % rest if rest > 0 else "")


class Lane:
    def __init__(self, spawn, stop_all, pending, *, comms_open=None, mic_busy=None, home: Path | None = None,
                 settings=None, clock=time.time, log=None, autostart: bool = True):
        self.spawn, self.stop_all, self.pending = spawn, stop_all, pending
        self.comms_open = comms_open or (lambda: False)
        self.mic_busy = mic_busy or (lambda pause_s: None)
        self.home = home or crissa_talk.HOME
        self.settings = settings or (lambda: crissa_talk.load(self.home))
        self.clock, self.log = clock, log
        self.autostart = autostart
        self.lock = threading.RLock()
        self.talk: collections.deque = collections.deque()
        self.hush = 0                          # bumped by stop(): a streaming reply stops speaking
        self.ann: list[dict] = []
        self.procs: list[tuple] = []          # (proc, kind, item)
        self.turns = 0                        # answers being asked or streamed
        self.last_talk_end = 0.0
        self.seen: dict[str, float] = {}
        self.thread: threading.Thread | None = None
        self.said = 0

    # ---- bookkeeping --------------------------------------------------------------------------------------
    def _live(self, kind: str | None = None) -> list:
        alive = [(p, k, it) for (p, k, it) in self.procs if p is not None and p.poll() is None]
        if any(k == "talk" for (_p, k, _i) in self.procs) and not any(k == "talk" for (_p, k, _i) in alive):
            self.last_talk_end = self.clock()
        self.procs = alive
        return [x for x in alive if kind is None or x[1] == kind]

    def _row(self, event: str, text: str, **kw) -> None:
        row = {"ts": round(self.clock(), 3), "event": event, "text": str(text or "")[:400]}
        row.update({k: v for k, v in kw.items() if v not in (None, "")})
        try:
            p = Path(self.home) / "announce-log.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass
        if self.log and event in ("dropped", "said", "roundup"):
            try:
                self.log("announce", "%s %r%s" % (event, str(text or "")[:60],
                                                   (" (%s)" % kw.get("reason")) if kw.get("reason") else ""),
                         str(kw.get("by") or "announce"))
            except Exception:  # noqa: BLE001
                pass

    def _kick(self) -> None:
        if not self.autostart or (self.thread and self.thread.is_alive()):
            return
        self.thread = threading.Thread(target=self._run, name="speech-lane", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while True:
            try:
                self.step()
            except Exception:  # noqa: BLE001  the lane never takes the player down
                pass
            time.sleep(0.1)

    # ---- talk ---------------------------------------------------------------------------------------------
    def _requeue_announcement(self) -> None:
        for (_p, k, it) in self._live("announce"):
            if it and not it.get("requeued"):
                it["requeued"] = True
                self.ann.insert(0, dict(it))
                self._row("requeued", it.get("text"), kind=it.get("kind"), reason="talk came first")

    def say(self, text: str, who: str, queue: bool = False):
        """Speak now (stop what is on the air) or, with queue, after the talk ahead of it."""
        with self.lock:
            if queue:
                self.talk.append((text, who))
                self._kick()
                return None
            self._requeue_announcement()
            self.talk.clear()
            self.stop_all()
            proc = self.spawn(text, who)
            self.procs.append((proc, "talk", None))
            return proc

    def begin_turn(self) -> None:
        with self.lock:
            self.turns += 1

    def end_turn(self) -> None:
        with self.lock:
            self.turns = max(0, self.turns - 1)
            self.last_talk_end = max(self.last_talk_end, self.clock())

    def stop(self) -> int:
        """Everything on the air and all queued talk. `hush` moves on, so a reply still streaming in
        knows the person stopped it and says no more of it (the ear's barge-in)."""
        with self.lock:
            self.hush += 1
            self.talk.clear()
            self.procs = []
            return int(self.stop_all() or 0)

    # ---- announce -----------------------------------------------------------------------------------------
    def announce(self, text: str, kind: str = "", by: str = "", dedup_key: str = "", max_wait_s=None,
                 speech_pause_s=None, voice: str = "") -> dict:
        text = " ".join(str(text or "").split())
        if not text:
            return {"ok": False, "queued": False, "reason": "text required"}
        cfg = self.settings()["announce"]
        now = self.clock()
        if crissa_talk.quiet(self.settings(), now):
            self._row("dropped", text, kind=kind, by=by, reason="quiet hours")
            return {"ok": True, "queued": False, "reason": "quiet hours"}
        if (Path(self.home) / MUTE_FILE).exists():
            self._row("dropped", text, kind=kind, by=by, reason="muted")
            return {"ok": True, "queued": False, "reason": "muted"}
        with self.lock:
            dedup_s = float(cfg.get("dedup_s") or 0)
            self.seen = {k: t for k, t in self.seen.items() if now - t < max(dedup_s, 1.0) * 3}
            if dedup_key and dedup_s > 0 and now - self.seen.get(dedup_key, -1e18) < dedup_s:
                self._row("skipped", text, kind=kind, by=by, reason="duplicate")
                return {"ok": True, "queued": False, "reason": "duplicate"}
            if dedup_key:
                self.seen[dedup_key] = now
            item = {"ts": now, "text": text, "kind": str(kind or ""), "by": str(by or ""), "voice": voice,
                    "dedup_key": dedup_key or "", "max_wait_s": max_wait_s, "speech_pause_s": speech_pause_s}
            self.ann.append(item)
            dropped = 0
            cap = max(1, int(cfg.get("queue_max") or 20))
            while len(self.ann) > cap:
                old = self.ann.pop(0)
                self._row("dropped", old.get("text"), kind=old.get("kind"), by=old.get("by"), reason="overflow")
                dropped += 1
            self._row("queued", text, kind=kind, by=by, depth=len(self.ann))
            self._kick()
            return {"ok": True, "queued": True, "depth": len(self.ann), "dropped": dropped}

    def _pause(self) -> float:
        """The quiet an announcement waits for after anyone's speech (its own, else the lane's)."""
        pause = float(self.settings()["announce"].get("speech_pause_s") or 0)
        if self.ann and self.ann[0].get("speech_pause_s") is not None:
            pause = float(self.ann[0]["speech_pause_s"])
        return pause

    def hold(self, now: float | None = None, mic: tuple | None = None) -> str | None:
        """Why an announcement may not start now, else None. `mic`: the microphone's answer already asked, (why,)."""
        now = self.clock() if now is None else now
        if self.talk or self._live("talk") or self._live("announce"):
            return "she is speaking"
        if self.pending():
            return "a line is on the air"
        if self.turns:
            return "an answer is on its way"
        if self.comms_open():
            return "the conversation is open"
        pause = self._pause()
        if pause and now - self.last_talk_end < pause:
            return "she just spoke"
        why = mic[0] if mic is not None else self.mic_busy(pause)
        return ("someone is talking (%s)" % why) if why else None

    def step(self, now: float | None = None) -> str:
        """One pass: start queued talk, expire, drop for quiet hours, or start one announcement."""
        now = self.clock() if now is None else now
        mic = None
        if mic_unlocked_on() and self.ann and not self.talk:
            # round 46 (the local review): the microphone's GET (up to 0.4 s) is asked before the lock, so a stop or
            # a line waiting on the lock is not held behind it
            mic = (self.mic_busy(self._pause()) or None,)
        with self.lock:
            if self.talk:
                if self._live("announce"):
                    self._requeue_announcement()
                    self.stop_all()
                    self.procs = [x for x in self.procs if x[1] != "announce"]
                if len(self._live("talk")) < 2:
                    text, who = self.talk.popleft()
                    proc = self.spawn(text, who)
                    self.procs.append((proc, "talk", None))
                    return "talk"
                return "talk-wait"
            if not self.ann:
                return "idle"
            cfg = self.settings()
            if crissa_talk.quiet(cfg, now) or (Path(self.home) / MUTE_FILE).exists():
                why = "quiet hours" if crissa_talk.quiet(cfg, now) else "muted"
                for it in self.ann:
                    self._row("dropped", it.get("text"), kind=it.get("kind"), by=it.get("by"), reason=why)
                self.ann = []
                return "dropped"
            keep = []
            for it in self.ann:
                mw = it.get("max_wait_s")
                mw = float(cfg["announce"].get("max_wait_s") or 0) if mw is None else float(mw or 0)
                if mw > 0 and now - float(it["ts"]) > mw:
                    self._row("dropped", it.get("text"), kind=it.get("kind"), by=it.get("by"),
                              reason="held %d s" % (now - float(it["ts"])))
                else:
                    keep.append(it)
            self.ann = keep
            if not self.ann:
                return "dropped"
            why = self.hold(now, mic)
            if why:
                return "hold: " + why
            over = int(cfg["announce"].get("roundup_over") or 0)
            if over and len(self.ann) > over:
                items, self.ann = self.ann, []
                line = roundup_line(items)
                for it in items:
                    self._row("summarised", it.get("text"), kind=it.get("kind"), by=it.get("by"),
                              reason="folded into a roundup of %d" % len(items))
                item = {"ts": now, "text": line, "kind": "roundup", "by": "lane", "requeued": True}
            else:
                item = self.ann.pop(0)
            who = item.get("voice") or str(cfg["announce"].get("voice") or "") or "crissa"
            proc = self.spawn(item["text"], who)
            self.procs.append((proc, "announce", item))
            self.said += 1
            self._row("roundup" if item.get("kind") == "roundup" else "said", item["text"], kind=item.get("kind"),
                      by=item.get("by"))
            return "announce"

    def status(self) -> dict:
        with self.lock:
            live = self._live()
            return {"depth": len(self.ann), "talk_waiting": len(self.talk), "turns": self.turns,
                    "on_air": [k for (_p, k, _i) in live], "said": self.said, "hold": self.hold() if self.ann else None,
                    "waiting": [{"text": it["text"][:120], "kind": it.get("kind"), "age_s": round(self.clock() - it["ts"])}
                                for it in self.ann[:20]]}
