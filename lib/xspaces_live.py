"""Live room lap (the User 2026-10-08).

Plays the wav the user recorded for each question, on the speakers, and
grades what Crissa says. The announcer speaks Pass or Fail. A say beat with
no file is skipped. Display cues are watched, and each one is measured
against the oracle clock.

OFF unless CRISSA_DEMO (or HOUSE_XSPACES_LIVE) is on and the kill file is
absent. Nothing here posts to X or reads a house tape. The crew's demo cards
are the drill key's (lib/demo_krew_drill, house-only); the lap files them only
when its runner passes cards=True, through that module, and an install without
it files none. The 90 s ear-quiet gap is a restart rule; this lap holds
for a real conversation, not for that gap.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import demo_hold
import crissa_home
try:
    import xspaces_drill as drill
except ImportError:  # a pack carries no drill grader: the local grade below
    drill = None

ROOT = Path(__file__).resolve().parent.parent
HOME = crissa_home.HOME
CONFIG = ROOT / "config" / "crissa-demo.json"
OUT_DIR = HOME / "xspaces-drill"
OFF_FILE = HOME / "xspaces-live.off"
ACTIVE_FILE = HOME / "xspaces-live.active"
POSE_DIR = ROOT / "display" / "crissa-poses"

_QUIET_HOLD = "quiet for"


def _flag_on(env: dict | None, *names: str) -> bool:
    e = os.environ if env is None else env
    for name in names:
        if str(e.get(name, "0")).strip().lower() in ("1", "true", "yes", "on"):
            return True
    return False


def enabled(env: dict | None = None) -> bool:
    if OFF_FILE.exists():
        return False
    return _flag_on(env, "CRISSA_DEMO", "HOUSE_XSPACES_LIVE")


def broadcast_on(env: dict | None = None) -> bool:
    """Empty-room path. The recorded line plays. Hands does the job. The ear is not posted.

    Secure holds auth with no face in frame. It does not make a recording the user.
    Off unless the env is set, so a lap with the user in the chair stays on the ear.
    """
    return _flag_on(env, "CRISSA_DEMO_BROADCAST", "HOUSE_XSPACES_BROADCAST")


# Broadcast beat SETS (the live loop, docs/plans/LIVE-LOOP-20261027.md, 2026-10-17): `sets` in the config is a
# list of {id, beats}; a BROADCAST lap plays set (lap mod n) so a viewer who stays does not hear the same lap
# twice. A lap with the user in the chair (broadcast off), a config with no `sets`, or CRISSA_DEMO_SETS=0 plays
# `beats`, today's lap row for row. The lap counter is a file beside the ledgers, so a restarted runner goes on
# rotating instead of starting over.
SETS_KILL = "CRISSA_DEMO_SETS"
LAP_CURSOR = "lap-cursor.json"
QUESTIONS_DEFAULT = "live-questions.jsonl"      # under the state home unless the config names a path

CONFIG_HOUSE = ROOT / "config" / "crissa-demo.house.json"


# THE LAP CUES HER OWN LISTEN LOOP (2026-10-27, cut 9 on +150: every `listen` cue answered "listen is not on the wall";
# `listen` is the house's key, and Crissa desk v2 listens on v2-listen-screen). A beat's cue `listen` (and the config's
# `home`) means "her listen loop": on a pack it is the loop the worn library declares (avatar.listen_loop: a face
# manifest or the library's own library.json), and `listen` only when it declares none. The house cues `listen` as
# before. Kill CRISSA_DEMO_LISTEN_KEY=0.
HOUSE_LISTEN = "listen"


def listen_key(env: dict | None = None) -> str:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_DEMO_LISTEN_KEY", "1")).strip().lower() in ("0", "false", "no", "off"):
        return HOUSE_LISTEN
    try:
        if crissa_home.is_house():
            return HOUSE_LISTEN
        import avatar
        pd = str(e.get("HOUSE_POSE_DIR") or "").strip()
        if not pd:
            import crissa_avatar
            pd = crissa_avatar.pose_dir(ROOT, env=e)[1]
        return avatar.listen_loop(pd) or HOUSE_LISTEN
    except Exception:  # noqa: BLE001 - an unreadable library keeps the house key, as before
        return HOUSE_LISTEN


def _cue_listen(beat: dict, key: str) -> dict:
    return {**beat, "cue": key} if key != HOUSE_LISTEN and beat.get("cue") == HOUSE_LISTEN else beat


# A HOUSE-ONLY BEAT IS NOT IN A PACK'S LAP (2026-10-27, +150: `daily` asked the house's hands for the House Daily and
# died on "can't open file .../scripts/crissa-hands.py"; a pack has no House Daily). A beat marked `house_only: true`
# plays on the house and is left out of the lap anywhere else. Kill CRISSA_DEMO_HOUSE_ONLY=0 (it plays everywhere).
def plays_here(beat: dict, env: dict | None = None) -> bool:
    if not beat.get("house_only"):
        return True
    e = os.environ if env is None else env
    if str(e.get("CRISSA_DEMO_HOUSE_ONLY", "1")).strip().lower() in ("0", "false", "no", "off"):
        return True
    try:
        return crissa_home.is_house()
    except Exception:  # noqa: BLE001
        return True


def sets_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get(SETS_KILL, "1")).strip().lower() not in ("0", "false", "no", "off")


def lap_sets(cfg: dict) -> list[dict]:
    """The config's beat sets: every row a dict with a non-empty `beats` list; anything else = no sets."""
    sets = cfg.get("sets")
    if not isinstance(sets, list):
        return []
    out = [s for s in sets if isinstance(s, dict) and isinstance(s.get("beats"), list) and s["beats"]]
    return out if out and len(out) == len(sets) else []


def pick_set(cfg: dict, lap: int) -> dict | None:
    sets = lap_sets(cfg)
    if not sets:
        return None
    return sets[int(lap) % len(sets)]


def all_beats(cfg: dict) -> list[dict]:
    """`beats` plus every set's beats, one row per id (the recordable questions and the line matcher read this)."""
    out: list[dict] = []
    seen: set[str] = set()
    for b in list(cfg.get("beats") or []) + [b for s in lap_sets(cfg) for b in s["beats"]]:
        if not isinstance(b, dict):
            continue
        key = str(b.get("id") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def read_lap_cursor(d: Path) -> int:
    try:
        return int(json.loads((Path(d) / LAP_CURSOR).read_text(encoding="utf-8")).get("lap") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def write_lap_cursor(d: Path, lap: int) -> None:
    try:
        Path(d).mkdir(parents=True, exist_ok=True)
        (Path(d) / LAP_CURSOR).write_text(json.dumps({"lap": int(lap), "at": time.time()}), encoding="utf-8")
    except OSError:
        pass


def questions_path(cfg: dict | None = None) -> Path:
    """The vetted live questions file: `live_questions` in the config (the house sidecar names its own place),
    else <state home>/live-questions.jsonl. The lap only READS it, and appends one `aired` row per question it took."""
    cfg = cfg if cfg is not None else settings()
    raw = str(cfg.get("live_questions") or "").strip()
    path = Path(raw).expanduser() if raw else Path(QUESTIONS_DEFAULT)
    return path if path.is_absolute() else HOME / path


def next_question(path: Path) -> dict | None:
    """The oldest row a human APPROVED that no lap has aired. A row is {id, by, question, status}; an airing is
    {id, aired}. Unreadable = none (she never takes a question nobody read)."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    aired: set[str] = set()
    rows: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not row.get("id"):
            continue
        if row.get("aired"):
            aired.add(str(row["id"]))
        else:
            rows.append(row)
    for row in rows:
        if str(row.get("by") or "").lower() == "synth":
            continue          # a made question is a synth beat's (lib/demo_synth); this beat credits a viewer
        if str(row.get("status") or "").lower() == "approved" and str(row["id"]) not in aired \
                and " ".join(str(row.get("question") or "").split()):
            return row
    return None


def question_credit(row: dict) -> str:
    """How the announcer names the asker: an X handle as is, an email or nothing as "a viewer"."""
    by = " ".join(str(row.get("by") or "").split())
    if not by or "@" in by[1:] or "." in by:
        return "a viewer"
    return by if by.startswith("@") else "@" + by


def settings(path: Path | None = None) -> dict:
    """The demo config; on the house its sidecar (the crew's demo cards, house-only) lays its keys on top."""
    cfg = json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
    if path is None and CONFIG_HOUSE.is_file():
        try:
            cfg.update(json.loads(CONFIG_HOUSE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return cfg


def _crew_drill():
    """lib/demo_krew_drill when this install carries it (the house); None in a pack."""
    try:
        import demo_krew_drill
    except ImportError as exc:
        if exc.name != "demo_krew_drill":
            raise
        return None
    return demo_krew_drill


def _show_off():
    """lib/demo_drill_sets when this install carries it (the house's show-off beats, the User 2026-10-13); None in a
    pack. Off unless its own switch is on: then the announcer reads a drill task, she answers live, the announcer
    grades. Absent or off, the lap is the same lap."""
    try:
        import demo_drill_sets
    except ImportError as exc:
        if exc.name != "demo_drill_sets":
            raise
        return None
    return demo_drill_sets


def _synth():
    """lib/demo_synth when this install carries it (the synthetic-user questions and the still beats, the demo
    thread 2026-10-20); None in a pack. Off unless its own switches are on; absent or off, a synth beat plays its
    fallback say beat or is one skipped row, and a still beat is one skipped row."""
    try:
        import demo_synth
    except ImportError as exc:
        if exc.name != "demo_synth":
            raise
        return None
    return demo_synth


def _extras():
    """lib/demo_extras (the clock and casual-chat beats, the demo thread 2026-10-23) when this install carries it;
    None in a pack, and a clock or chat beat is then one skipped row."""
    try:
        import demo_extras
    except ImportError as exc:
        if exc.name != "demo_extras":
            raise
        return None
    return demo_extras


def start_hold(reason: str) -> str:
    """A restart-quiet timer is not a conversation. Speaking, a session, hands, and a line on air are."""
    if not reason:
        return ""
    if _QUIET_HOLD in reason and "speaking" not in reason and "session" not in reason and "hands" not in reason:
        return ""
    return reason


def beat_hold(ear: dict | None, *, hands: bool, on_air: bool, owning: bool, glass: str = "") -> str:
    """Between our own beats. Once we own the lap, her open session is ours. `glass`: a flourish on the glass (the
    User 2026-10-20, "she yawned but broadcast did not pause"): the next beat waits for her to finish it."""
    if hands:
        return "a hands run is in flight"
    if on_air:
        return "a line is being spoken"
    if glass:
        return glass
    if isinstance(ear, dict):
        if ear.get("speaking"):
            return "%s is speaking" % crissa_home.her_name()
        if ear.get("dispatch_busy"):
            return "a turn is with her brain"
        if ear.get("session_open") and not owning:
            return "a hear session is open"
    return ""


def control_of(text: str) -> str:
    """A live control word. Content lines do not match."""
    t = (text or "").lower().strip()
    if not t:
        return ""
    if "that's all" in t or "thats all" in t:
        return "thanks"
    if "go ahead" in t:
        return "yes"
    if re.search(r"\bstop\b", t):
        return "stop"
    return ""


def _phrase_tokens(text: str) -> list[str]:
    stop = {"the", "a", "an", "for", "on", "me", "my", "to", "of"}
    return [w for w in re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split() if w not in stop]


def matches_line(text: str, phrases: list[str] | None = None) -> bool:
    """True when a heard line is one of the recorded questions, not room talk.

    Used when the voice gate drops a short clip: the same PCM is posted once
    more only if the dropped text is one of those questions.
    """
    heard = _phrase_tokens(text)
    if not heard:
        return False
    if phrases is None:
        phrases = [str(b.get("say") or "") for b in all_beats(settings())
                   if b.get("kind") == "say" and b.get("say")]
    for phrase in phrases:
        toks = _phrase_tokens(phrase)
        if toks and sum(1 for w in toks if w in heard) / len(toks) >= 0.6:
            return True
    return False


def matches_tape(text: str) -> bool:
    """Ear-path name for matches_line. It does not read a tape."""
    return matches_line(text)


def _sentences(text: str) -> int:
    parts = [p for p in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if re.search(r"[A-Za-z0-9]", p)]
    return len(parts)


def _grade_local(beat: dict, spoken: str, ms: int) -> dict:
    """The pack's grade, from the question's expect block alone (no drill module, no house ledger)."""
    exp = beat.get("expect") or {}
    spoken = spoken or ""
    miss = [p for p in exp.get("answer", []) if not re.search(p, spoken, re.I | re.S)]
    n = _sentences(spoken)
    cap = exp.get("max_sentences")
    why = []
    if miss:
        why.append("answer missing " + ", ".join(miss))
    if cap and n > cap:
        why.append(f"{n} sentences > {cap}")
    if not spoken:
        why.append("silent")
    limit = float(beat.get("limit_s", 60)) * 1000
    in_time = int(ms) <= limit
    ok = not why
    if ok and not in_time:
        why.append(f"slow {int(ms) / 1000:.1f}s > {beat.get('limit_s', 60)}s")
    return {"ok": ok, "pass": ok and in_time, "why": "; ".join(why), "ms": int(ms), "sentences": n}


def grade_spoken(beat: dict, spoken: str, ms: int) -> dict:
    """The drill's grader when the house carries it (one grade for the drill and the lap); else the pack's own."""
    if drill is None:
        return _grade_local(beat, spoken, ms)
    turn = {"spoken": spoken or "", "tools": [], "ms": int(ms), "inner": []}
    return drill.grade(beat, turn, 0.0, files=lambda *a: [])


def verdict_line(grade: dict) -> str:
    if grade.get("pass"):
        return "Pass."
    if grade.get("ok"):
        return "Slow, but it landed."
    return "Fail."


def oracle_line(cover: dict, *, armed: bool, shift_live: bool | None) -> str:
    if not cover.get("frames"):
        return "No head track, so the oracle was not measured."
    if cover.get("clear"):
        base = "Oracle corner clear."
    elif cover.get("covered"):
        base = "Oracle corner covered."
    else:
        base = "Oracle corner not measured."
    if not armed:
        return base
    if shift_live is False:
        return base + " Slide is armed. This wall is still the old frame."
    if shift_live is True:
        return base + " Slide is on this frame."
    return base + " Slide is armed."


class Room:
    """What the lap may touch. Tests pass fakes; the script fills the live ones."""

    def __init__(self, **kw):
        self.play = kw.get("play") or (lambda pcm, rate: (False, "no player"))
        self.post = kw.get("post") or (lambda pcm, rate: None)
        self.say = kw.get("say") or (lambda text: None)
        self.cue = kw.get("cue") or (lambda key, ttl: {"taken": False, "why": "no conductor"})
        self.zoom_get = kw.get("zoom_get") or (lambda: (False, 1.0))
        self.zoom_set = kw.get("zoom_set") or (lambda hole, fov: None)
        self.shift_set = kw.get("shift_set") or (lambda origin: None)
        self.shift_live = kw.get("shift_live") or (lambda: None)
        self.hold_start = kw.get("hold_start") or (lambda: "")
        # is another hands job running? (lib/demo_lap_hold.hands_busy on the live room; None = never wait, as a fake
        # room in a test must never probe the house's real hands lock)
        self.hands_busy = kw.get("hands_busy")
        self.ear = kw.get("ear") or (lambda: {})
        self.hands = kw.get("hands") or (lambda: False)
        self.on_air = kw.get("on_air") or (lambda: False)
        # The glass: '' when she is on a loop, else what she is doing (a flourish's key); a beat waits for it.
        self.flourish = kw.get("flourish") or (lambda: "")
        self.wait_reply = kw.get("wait_reply") or (lambda timeout: ("", 0))
        self.pull_controls = kw.get("pull_controls") or (lambda: [])
        self.take = kw.get("take") or (lambda take_id: None)
        self.open_session = kw.get("open_session") or (lambda: None)
        self.ask_hands = kw.get("ask_hands") or (lambda ask: {"ok": False, "text": "", "ms": 0})
        # Dev beats never take the pause=True hands path. That pause homes the wall.
        self.ask_dev = kw.get("ask_dev") or (lambda ask: {"ok": False, "text": "", "ms": 0, "why": "no dev ask"})
        self.improving = kw.get("improving") or (lambda: "")
        self.speak_crissa = kw.get("speak_crissa") or (lambda text: None)
        self.close_session = kw.get("close_session") or (lambda: None)
        self.queue_note = kw.get("queue_note") or (lambda: "")
        self.cover = kw.get("cover") or (lambda key: {"frames": 0, "covered": 0, "clear": False})
        self.origin = kw.get("origin") or (lambda key: None)
        # The sheet from the beat she is leaving. A move forward takes it down.
        self.clear_pages = kw.get("clear_pages") or (lambda: None)
        # The wall's live mouth dial, read and put back. A read beat adds
        # lead for her passage only.
        self.mouth = kw.get("mouth") or (lambda: {})
        self.mouth_set = kw.get("mouth_set") or (lambda semis: None)
        self.show_note = kw.get("show_note") or (lambda title, text: None)
        # The glass's screen route whole (art, figure): a still beat paints through it and reads it back.
        self.screen = kw.get("screen") or (lambda body: {})
        self._sleep = kw.get("sleep") or time.sleep
        self.log = kw.get("log") or (lambda *a: None)
        self.now = kw.get("now") or time.time

    def sleep(self, seconds: float) -> None:
        self._sleep(seconds)


def _glass(room: Room) -> str:
    try:
        return str(room.flourish() or "")
    except Exception:  # noqa: BLE001 - an unreadable glass never holds a beat
        return ""


def _wait_clear(room: Room, owning: bool, limit: float) -> str:
    waited = 0.0
    why = beat_hold(room.ear(), hands=room.hands(), on_air=room.on_air(), owning=owning, glass=_glass(room))
    while why and waited < limit:
        room.log(f"hold: {why}")
        room.sleep(0.5)
        waited += 0.5
        why = beat_hold(room.ear(), hands=room.hands(), on_air=room.on_air(), owning=owning, glass=_glass(room))
    return why


def _watch(room: Room, seconds: float) -> str:
    """Sleep, and return the first live control that arrives."""
    left = float(seconds)
    while left > 0:
        for word in room.pull_controls():
            if word:
                return word
        step = min(0.25, left)
        room.sleep(step)
        left -= step
    for word in room.pull_controls():
        if word:
            return word
    return ""


def _apply_frame(room: Room, beat: dict, saved: tuple[bool, float]) -> None:
    if beat.get("hole"):
        room.zoom_set(True, float(beat.get("fov") or 1.7))
        return
    room.zoom_set(saved[0], saved[1])


def _timed_verdict(grade: dict, ms: int) -> str:
    line = verdict_line(grade)
    if not grade.get("pass") or not ms:
        return line
    n = max(1, int(round(int(ms) / 1000.0)))
    return f"Pass. {n} second." if n == 1 else f"Pass. {n} seconds."


def _shown(text: str, ok: bool) -> bool:
    low = (text or "").lower()
    return (bool(ok) and "nothing changed" not in low and "did not finish" not in low
            and re.search(r"confirmed:\s*shown\b", text or "", re.I) is not None)


# NO LITERAL LIMITS ON WHAT SHE SAYS (the User 2026-10-21: "why do we limit her to four sentences? Remove."; "hate
# those hard coded limits"). Every cap on her speech, and every hold on a beat, is a dial in the config's `limits`
# block with the measurement behind it; a speech dial defaults to 0 = off. SPEAK_CHARS stays as the old one-sentence
# path's clause length, used only when a caller asks for one sentence (a 220-character slice once ended her on "and
# whe"; the speech wait now scales with the text, so a long line is never cut by it).
SPEAK_CHARS = 480
LIMIT_DEFAULTS = {
    "speak_sentences": 0,      # 0 = every sentence before the confirmed: footer
    "speak_chars": 0,          # 0 = no length cap
    "hold_wait_s": 25,         # wait for the room to clear between beats; guards a stuck on-air flag, not her speech
    "speaker_wait_s": 16,      # retry a line while the speakers report mic_busy (the post-speech cooldown is 12 s)
    "hands_timeout_s": 0,      # 0 = no lap-side cap on a hands job: her hands' own run budget ends a hung one
    "speak_wait_floor_s": 90,  # house-speak wait floor; with the per-character term it never cuts a playing line
    "speak_wait_per_char_s": 0.1,
    # a line past the voice's 800-character cap is read in pieces on the house too (house-speak HOUSE_SPEAK_HOUSE_PIECES;
    # a pack always splits). 0 = the house's one line, cut at the cap (2026-10-23: the 902-character brief aired 704).
    "speak_pieces": 0,
    # her hands are taken by another job when a question beat starts: wait for them, saying so once on air. Measured
    # 10-21 11:07:50-11:14:51: her Decide watch's hands jobs ran 10-55 s each; with the lap hold no NEW job starts, so
    # only one already running is waited out. 0 = do not wait (the old skip-with-the-busy-line).
    "hands_wait_s": 60,
    "hands_wait_poll_s": 1,
    # a flourish on the glass when the lap starts: the lap cues her listen loop (it plays at the flourish's next seam,
    # instead of the next idle flourish) and waits this long for it before the opener. Measured 10-27 lap 1 on +150:
    # v2-fl-glance-door held the first beat ~35 polls, lap 2 none. 0 = do not wind down (the old wait per beat).
    "flourish_wind_s": 10,
}


def limits(cfg: dict | None = None) -> dict:
    """The config's `limits` block over LIMIT_DEFAULTS; unreadable = the defaults."""
    try:
        got = (cfg if cfg is not None else settings()).get("limits")
    except (OSError, ValueError):
        got = None
    out = dict(LIMIT_DEFAULTS)
    if isinstance(got, dict):
        out.update({k: v for k, v in got.items() if not str(k).startswith("_")})
    return out


def dial(name: str, cfg: dict | None = None):
    return limits(cfg).get(name, LIMIT_DEFAULTS.get(name))


def spoken_sentence(text: str, limit: int | None = None, sentences: int | None = None) -> str:
    """What she says from a result: `sentences` (0 = all) before the confirmed: footer, `limit` characters (0 = no cap),
    both from the dials unless given. With a cap, a long line ends on a clause, never mid-word."""
    sentences = int(dial("speak_sentences") if sentences is None else sentences)
    limit = int(dial("speak_chars") if limit is None else limit)
    body = " ".join((text or "").split("confirmed:")[0].split())
    if not body:
        return ""
    parts = [x.strip() for x in re.split(r"(?<=[.!?])\s+", body) if x.strip()]
    first = " ".join(parts if sentences <= 0 else parts[:sentences])
    if limit <= 0:
        return first
    if len(first) <= limit:
        return first
    window = first[:limit]
    cut = max(window.rfind(mark) for mark in (".", "!", "?", ";", ":", "—", "–", ","))
    if cut >= 40:
        return window[:cut + 1].rstrip()
    sp = window.rfind(" ")
    if sp >= 40:
        return window[:sp].rstrip()
    return window.rstrip()


def _first_sentence(text: str) -> str:
    """What she says from a result (the name is the old one: it is the whole answer unless a dial says otherwise)."""
    return spoken_sentence(text)


def _for_air(sentence: str) -> tuple[str, list[str]]:
    """What she may say on the air: the house's broadcast scrub (lib/demo_drill_sets.for_air, both layers) when this
    install carries it, else the sentence as it is (a pack has no house words to drop). Found 2026-10-20 on the first
    recorded lap: the say path spoke a house port number on the air while every other beat scrubbed."""
    show = _show_off()
    if not sentence or show is None:
        return sentence, []
    try:
        return show.for_air(sentence)
    except Exception:  # noqa: BLE001 - an unreadable scrub says nothing on air
        return "", ["scrub"]


def play_line(room: Room, pcm: bytes, text: str = "") -> tuple[bool, str]:
    """Play a clip, with its words when the player takes them (so the ear can subtract the heard copy of a line the
    speakers said); a player that takes (pcm, rate) alone still plays."""
    try:
        return room.play(pcm, 16000, text=text) if text else room.play(pcm, 16000)
    except TypeError:
        return room.play(pcm, 16000)


def _broadcast_say(room: Room, beat: dict) -> dict:
    """Speakers carry his line. Hands does the job. The ear is not posted, and the session is not paused."""
    take_id = beat.get("take")
    if not take_id:
        return {"beat": beat["id"], "kind": "say", "skipped": "no wav", "pass": False, "ok": False,
                "why": "no recording", "say": beat.get("say") or ""}
    pcm = room.take(take_id)
    if not pcm:
        return {"beat": beat["id"], "kind": "say", "skipped": "no wav", "pass": False, "ok": False,
                "why": "wav missing", "say": beat.get("say") or ""}
    ok, why = play_line(room, pcm, " ".join(str(beat.get("say") or "").split()))
    if not ok:
        return {"beat": beat["id"], "kind": "say", "pass": False, "ok": False,
                "why": f"speakers: {why}", "spoken": ""}
    job = " ".join(str(beat.get("job") or beat.get("say") or "").split())
    if beat.get("note"):
        try:
            note = room.queue_note() or ""
        except Exception:  # noqa: BLE001
            note = ""
        if note:
            job = f"{job} Order list: {note}".strip()
    t0 = room.now()
    try:
        result = room.ask_hands(job) or {}
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "text": "", "ms": 0, "why": type(exc).__name__}
    text = str(result.get("text") or "")
    ms = int(result.get("ms") or int((room.now() - t0) * 1000))
    screen = bool(beat.get("screen"))
    shown = _shown(text, bool(result.get("ok")))
    sentence = _first_sentence(text)
    refusal = re.search(r"nothing changed|did not (?:work|finish)|ran out of steps", sentence, re.I)
    spoke = ""
    dropped: list[str] = []
    if sentence and ((not screen) or shown or refusal):
        said, dropped = _for_air(sentence)
        if said:
            room.speak_crissa(said)
        spoke = sentence            # graded unscrubbed, spoken scrubbed (the show-off beats' rule)
    # A screen beat is graded on the sentence the room heard. The tool dump
    # stays in the row. A spoken beat keeps the dump so a list still counts.
    heard = spoke if screen else " ".join(x for x in (spoke, text) if x)
    spoken = (heard or text)[:500]
    grade = grade_spoken(beat, spoken, ms)
    if screen and not shown:
        grade = {**grade, "pass": False, "ok": False, "why": (grade.get("why") or "not on the glass")}
    _wait_clear(room, True, float(dial("hold_wait_s")))
    room.say(_timed_verdict(grade, ms))
    row = {"beat": beat["id"], "kind": "say", "spoken": (spoken or "")[:240], "broadcast": True, "dropped": dropped,
           **grade}
    if screen:
        row["shown"] = shown
    return row


def _say_beat(room: Room, beat: dict, *, broadcast: bool = False) -> dict:
    if broadcast:
        return _broadcast_say(room, beat)
    take_id = beat.get("take")
    if not take_id:
        return {"beat": beat["id"], "kind": "say", "skipped": "no wav", "pass": False, "ok": False,
                "why": "no recording", "say": beat.get("say") or ""}
    pcm = room.take(take_id)
    if not pcm:
        return {"beat": beat["id"], "kind": "say", "skipped": "no wav", "pass": False, "ok": False,
                "why": "wav missing", "say": beat.get("say") or ""}
    # Idle health says paused whenever no hear session is open. A command
    # with no wake word is dropped until LISTENING is on. The chip open is
    # that switch: no extra words, no "Here."
    try:
        room.open_session()
    except Exception as exc:  # noqa: BLE001
        return {"beat": beat["id"], "kind": "say", "pass": False, "ok": False,
                "why": f"session: {type(exc).__name__}", "spoken": ""}
    ok, why = play_line(room, pcm, " ".join(str(beat.get("say") or "").split()))
    if not ok:
        return {"beat": beat["id"], "kind": "say", "pass": False, "ok": False,
                "why": f"speakers: {why}", "spoken": ""}
    room.sleep(0.4)
    try:
        room.post(pcm, 16000)
    except Exception as exc:  # noqa: BLE001 — the ear post is the turn; a failure is the grade
        return {"beat": beat["id"], "kind": "say", "pass": False, "ok": False,
                "why": f"ear post: {type(exc).__name__}", "spoken": ""}
    t0 = room.now()
    spoken, ms = room.wait_reply(float(beat.get("limit_s") or 40))
    if not ms:
        ms = int((room.now() - t0) * 1000)
    grade = grade_spoken(beat, spoken, int(ms))
    _wait_clear(room, True, float(dial("hold_wait_s")))
    room.say(_timed_verdict(grade, int(ms)))
    return {"beat": beat["id"], "kind": "say", "spoken": (spoken or "")[:240], **grade}


def _dev_beat(room: Room, beat: dict) -> dict:
    """Optional beat. No wav, no ear post, no session pause. A fail stays in the row."""
    ask = " ".join(str(beat.get("ask") or beat.get("say") or "").split())
    t0 = room.now()
    try:
        result = room.ask_dev(ask) or {}
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "text": "", "ms": 0, "why": type(exc).__name__}
    text = str(result.get("text") or "")
    ms = int(result.get("ms") or int((room.now() - t0) * 1000))
    if result.get("why") and not text:
        text = str(result.get("why"))
    grade = grade_spoken(beat, text, ms)
    room.say(_timed_verdict(grade, ms))
    return {"beat": beat["id"], "kind": "dev", "dev": True, "required": False,
            "spoken": text[:240], **grade}


def reading_parts(text: str, breath_s) -> list[tuple[str, float]]:
    """The passage as (part, pause after) rows: whole with no pause, or split at the sentence boundary nearest its
    middle with `breath_s` between (the User 10-20: a breath mid-way so the bun-wobble has a pause to wait for)."""
    text = " ".join(str(text or "").split())
    if not text:
        return []
    try:
        gap = float(breath_s or 0)
    except (TypeError, ValueError):
        gap = 0.0
    sents = [p for p in re.split(r"(?<=[.!?])\s+", text) if p]
    if gap <= 0 or len(sents) < 2:
        return [(text, 0.0)]
    best, cut = None, 1
    for i in range(1, len(sents)):
        head = " ".join(sents[:i])
        d = abs(len(head) - len(text) / 2)
        if best is None or d < best:
            best, cut = d, i
    return [(" ".join(sents[:cut]), gap), (" ".join(sents[cut:]), 0.0)]


def _read_beat(room: Room, beat: dict) -> dict:
    """She reads a written passage in her own voice. No recording, no ear post.

    The cue lands first, then the lead. A clip entry replaces the live dial,
    so the add is measured on the dial she is actually wearing. It is put
    back before the announcer hands the lap to the next beat.

    The +4 semis on the Melville beat is ASSUMED. Measured 2026-10-10 05:20:48:
    the same opening used the wall's live dial, printed as sheet lead 188 ms,
    posted 197 mouth sheets, and the aligner returned in 252 ms. The lips were
    still late, and the filmed mouth was what moved. Four semis is the same
    step as the nudge that produced that 188 ms dial.
    """
    cue_key = str(beat.get("cue") or "")
    cue: dict = {}
    if cue_key:
        try:
            cue = room.cue(cue_key, float(beat.get("ttl") or 0)) or {}
        except Exception as exc:  # noqa: BLE001
            cue = {"taken": False, "why": type(exc).__name__}
    saved = None
    lead_to = None
    add = beat.get("lead_add_semis")
    if isinstance(add, (int, float)) and not isinstance(add, bool) and float(add) != 0.0:
        try:
            cur = room.mouth() or {}
            semis = cur.get("sheet_lead_semis")
            if isinstance(semis, (int, float)) and not isinstance(semis, bool):
                saved = float(semis)
                lead_to = saved + float(add)
                room.mouth_set(lead_to)
        except Exception:  # noqa: BLE001 — the passage still plays on the dial she has
            saved = None
            lead_to = None
    notice = " ".join(str(beat.get("notice") or "").split())
    if notice:
        room.say(notice)
    note = " ".join(str(beat.get("note") or "").split())
    if note:
        try:
            room.show_note(str(beat.get("note_title") or "").strip(), note)
        except Exception:  # noqa: BLE001
            room.log("note stayed down")
    text = " ".join(str(beat.get("text") or "").split())
    ok = bool(text)
    why = "" if text else "no passage"
    t0 = room.now()
    try:
        for part, gap in reading_parts(text, beat.get("breath_s")):
            got = room.speak_crissa(part)
            if isinstance(got, dict) and got.get("ok") is False:
                ok = False
                why = str(got.get("why") or "unheard")
                break
            if gap:
                room.sleep(gap)       # the breath: a rest gap the bag may fill with a fidget
    except Exception as exc:  # noqa: BLE001
        ok = False
        why = type(exc).__name__
    finally:
        if saved is not None:
            try:
                room.mouth_set(saved)
            except Exception:  # noqa: BLE001
                ok = False
                why = (why + " lead not restored").strip()
    after = " ".join(str(beat.get("after") or "").split())
    if after:
        room.say(after)
    return {
        "beat": beat["id"], "kind": "read", "pass": ok, "ok": ok, "why": why,
        "chars": len(text), "ms": int((room.now() - t0) * 1000),
        "spoken": text[:240], "lead_from": saved, "lead_to": lead_to,
        "cue": {"taken": bool(cue.get("taken")), "key": cue_key, "why": cue.get("why")},
    }


def _pull_beat(room: Room, beat: dict) -> dict:
    """She pulls a page herself. The announcer names it. This beat plays no recording."""
    room.say("House Daily.")
    try:
        result = room.ask_hands(beat.get("ask") or "") or {}
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "text": "", "ms": 0, "why": type(exc).__name__}
    text = str(result.get("text") or "")
    # confirmed: shown is the tool reply. A sentence that merely says the page
    # is up, with no tool behind it, is the smoke this beat exists to avoid.
    shown = _shown(text, bool(result.get("ok")))
    lamp = (room.improving() or "") if shown else ""
    sentence = spoken_sentence(text)
    spoke = ""
    dropped: list[str] = []
    if sentence and (shown or re.search(r"nothing changed|did not (?:work|finish)|ran out of steps", sentence, re.I)):
        said, dropped = _for_air(sentence)
        if said:
            room.speak_crissa(said)
        spoke = sentence
    if lamp:
        room.say(lamp)
    spoken = " ".join(x for x in (spoke, lamp, text) if x)[:500]
    ms = int(result.get("ms") or 0)
    grade = grade_spoken(beat, spoken, ms)
    if not shown:
        grade = {**grade, "pass": False, "ok": False, "why": grade.get("why") or "not on the glass"}
    _wait_clear(room, True, float(dial("hold_wait_s")))
    room.say(_timed_verdict(grade, ms))
    return {"beat": beat["id"], "kind": "pull", "spoken": spoken[:240], "shown": shown, "dropped": dropped, **grade}


def _show_beat(room: Room, beat: dict) -> dict:
    cue = {}
    try:
        cue = room.cue(beat.get("cue") or "", float(beat.get("ttl") or 0)) or {}
    except Exception as exc:  # noqa: BLE001
        cue = {"taken": False, "why": type(exc).__name__}
    # A show beat with a note is the lap's lower third (the disclosure chip at the top of a broadcast lap). The
    # glass's own note route carries it; the next beat's clear takes it down.
    note = " ".join(str(beat.get("note") or "").split())
    if note:
        try:
            room.show_note(str(beat.get("note_title") or "").strip(), note)
        except Exception:  # noqa: BLE001
            room.log("note stayed down")
    cover = {}
    armed = False
    if beat.get("oracle"):
        cover = room.cover(beat.get("cue") or "") or {}
        if beat.get("arm_shift"):
            origin = room.origin(beat.get("cue") or "")
            if origin:
                room.shift_set(origin)
                armed = True
        room.say(oracle_line(cover, armed=armed, shift_live=room.shift_live() if armed else None))
    word = _watch(room, float(beat.get("watch_s") or 8))
    if armed:
        room.shift_set(None)
    taken = bool(cue.get("taken"))
    why = "" if taken else ("cue: " + str(cue.get("why") or "refused"))
    return {"beat": beat["id"], "kind": beat.get("kind") or "show", "pass": taken, "ok": taken,
            "why": why, "cue": {"taken": taken, "why": cue.get("why"), "key": beat.get("cue")},
            "oracle": cover, "control": word}


def _question_beat(room: Room, beat: dict, cfg: dict, stamp: str) -> dict:
    """A vetted live question. The announcer credits the asker and reads it (a human approved it first; the lap
    never takes one nobody read), her hands answer, she says the first sentence through the broadcast scrub when
    the house carries one, and the question is marked aired. No approved row = skipped, quietly."""
    path = questions_path(cfg)
    row = next_question(path)
    if row is None:
        return {"beat": beat["id"], "kind": "question", "skipped": "no question", "pass": False, "ok": False,
                "why": "no approved question"}
    question = " ".join(str(row.get("question") or "").split())
    lead = " ".join(str(beat.get("lead") or cfg.get("question_lead") or "A question from the room.").split())
    room.say(f"{lead} From {question_credit(row)}: {question}")
    t0 = room.now()
    try:
        result = room.ask_hands(question) or {}
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "text": "", "ms": 0, "why": type(exc).__name__}
    text = str(result.get("text") or "")
    ms = int(result.get("ms") or int((room.now() - t0) * 1000))
    sentence = _first_sentence(text)
    dropped: list[str] = []
    show = _show_off()
    if sentence and show is not None:
        try:
            sentence, dropped = show.for_air(sentence)
        except Exception:  # noqa: BLE001 - an unreadable scrub says nothing on air
            sentence, dropped = "", ["scrub"]
    spoke = ""
    if sentence:
        room.speak_crissa(sentence)
        spoke = sentence
    try:
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": row["id"], "aired": time.time(), "lap": stamp}) + "\n")
    except OSError:
        room.log("question: aired row not written")
    grade = grade_spoken(beat, spoke or text, ms)
    if not spoke:
        grade = {**grade, "pass": False, "ok": False, "why": grade.get("why") or "nothing said on air"}
    _wait_clear(room, True, float(dial("hold_wait_s")))
    room.say(_timed_verdict(grade, ms))
    return {"beat": beat["id"], "kind": "question", "question_id": row["id"], "spoken": spoke[:240],
            "dropped": dropped, "broadcast": True, **grade}


def _place_krew(beats: list[dict], show, cfg: dict, log) -> tuple[list[dict], bool]:
    """A set's `krew` placeholder is where the show-off beats (lib/demo_drill_sets: a drill task read by the
    announcer, answered live with her tools, graded on air; only `broadcast: true` read-only tasks) play inside
    the lap. Returns the beats with the placeholder replaced, and whether one was there. Module absent or its switch
    off: the placeholder becomes one skipped row so the ledger says the krew beat did not air."""
    out: list[dict] = []
    placed = False
    for b in beats:
        if b.get("kind") != "krew":
            out.append(b)
            continue
        placed = True
        rows = show.lap_beats(cfg, log=log) if show is not None else []
        if rows:
            out.extend(rows)
        else:
            out.append({**b, "kind": "krew-off"})
    return out, placed


def _absent_fallback(room: Room, beat: dict, kind: str, broadcast) -> dict:
    """A synth or render beat on an install without lib/demo_synth plays its `fallback` say beat (a recorded line),
    as the module does when its switch is off (2026-10-27, +150: the four say beats were never heard on the kit,
    every one of them sat behind a synth beat); no fallback = one skipped row, as before."""
    fb = beat.get("fallback")
    if not isinstance(fb, dict) or not fb.get("id"):
        return {"beat": beat["id"], "kind": kind, "skipped": "no synth module", "pass": False,
                "ok": False, "why": "this install has no lib/demo_synth"}
    row = _say_beat(room, fb, broadcast=bool(broadcast))
    row["fallback_for"] = beat.get("id")
    row["why_fallback"] = "this install has no lib/demo_synth"
    return row


def _wind_down(room: Room, listen: str, limit: float) -> str:
    """A flourish on the glass before the first beat: cue her listen loop and wait up to `limit` s for the glass to
    come back to a loop. Returns what is still on the glass ('' = clear)."""
    up = _glass(room)
    if not up or limit <= 0:
        return up
    try:
        got = room.cue(listen, 0) or {}
        room.log(f"wind down: {up}; cue {listen} {'taken' if got.get('taken') else got.get('why') or 'refused'}")
    except Exception as exc:  # noqa: BLE001
        room.log(f"wind down: cue failed ({type(exc).__name__})")
    waited = 0.0
    while up and waited < limit:
        room.sleep(0.5)
        waited += 0.5
        up = _glass(room)
    return up


def run_lap(cfg: dict | None = None, room: Room | None = None, *, out_dir: Path | None = None,
            only: list[str] | None = None, broadcast: bool | None = None,
            cards: bool = False, cards_run=None, lap: int | None = None) -> dict:
    cfg = cfg or settings()
    room = room or Room()
    if broadcast is None:
        broadcast = bool(cfg.get("broadcast"))
    day = time.strftime("%Y-%m-%d")
    stamp = time.strftime("%H%M%S")
    base = Path(out_dir or OUT_DIR)
    d = base / day
    d.mkdir(parents=True, exist_ok=True)
    ledger = d / f"live-{stamp}.jsonl"
    chosen = None
    if broadcast and not only and sets_on():
        if lap is None:
            lap = read_lap_cursor(base)
        chosen = pick_set(cfg, lap)
    beats = list(chosen["beats"]) if chosen is not None else list(cfg.get("beats") or [])
    beats = [b for b in beats if not only or b.get("id") in only]
    show = _show_off()
    placed = False
    if chosen is not None:
        beats, placed = _place_krew(beats, show, cfg, room.log)
    if show is not None and not only and not placed:
        beats = beats + show.lap_beats(cfg, log=room.log)
    if demo_hold.dev_on() and not only:
        beats = beats + [b for b in (cfg.get("dev_beats") or []) if b.get("dev")]
    listen = listen_key()
    beats = [_cue_listen(b, listen) for b in beats if not isinstance(b, dict) or plays_here(b)]
    home = cfg.get("home") or HOUSE_LISTEN
    home = listen if home == HOUSE_LISTEN else home
    saved = room.zoom_get()
    if not (isinstance(saved, tuple) and len(saved) == 2):
        saved = (False, 1.0)
    rows: list[dict] = []
    stop = "complete"
    state = {"owning": False, "last_say": None}
    started = False

    def run_one(beat: dict) -> str:
        _wait_clear(room, state["owning"], float(dial("hold_wait_s")))
        # She is moving on. The notes and the doc from the last beat come down
        # before this one cues. A page beat puts its own sheet up after that.
        try:
            room.clear_pages()
        except Exception:  # noqa: BLE001
            room.log("page stayed")
        _apply_frame(room, beat, saved)
        if beat.get("kind") == "say":
            row = _say_beat(room, beat, broadcast=bool(broadcast))
            state["owning"] = True
            if not row.get("skipped"):
                state["last_say"] = beat
        elif beat.get("kind") == "read":
            row = _read_beat(room, beat)
            state["owning"] = True
        elif beat.get("kind") == "pull":
            row = _pull_beat(room, beat)
            state["owning"] = True
        elif beat.get("kind") == "dev":
            row = _dev_beat(room, beat)
        elif beat.get("kind") == "sets" and show is not None:
            row = show.run_beat(room, beat, broadcast=bool(broadcast))
            state["owning"] = True
        elif beat.get("kind") == "question":
            row = _question_beat(room, beat, cfg, stamp)
            state["owning"] = True
        elif beat.get("kind") == "synth":
            syn = _synth()
            if syn is None:
                row = _absent_fallback(room, beat, "synth", broadcast)
            else:
                row = syn.run_synth_beat(room, beat, cfg, stamp, broadcast=bool(broadcast))
            state["owning"] = True
        elif beat.get("kind") == "render":
            syn = _synth()
            if syn is None or not hasattr(syn, "run_render_beat"):
                row = _absent_fallback(room, beat, "render", broadcast)
            else:
                row = syn.run_render_beat(room, beat, cfg, stamp)
            state["owning"] = True
        elif beat.get("kind") == "still":
            syn = _synth()
            if syn is None:
                row = {"beat": beat["id"], "kind": "still", "skipped": "no synth module", "pass": False,
                       "ok": False, "why": "this install has no lib/demo_synth"}
            else:
                row = syn.run_still_beat(room, beat, cfg)
        elif beat.get("kind") in ("clock", "chat"):
            ext = _extras()
            if ext is None:
                row = {"beat": beat["id"], "kind": beat["kind"], "skipped": "no extras module", "pass": False,
                       "ok": False, "why": "this install has no lib/demo_extras"}
            elif beat["kind"] == "clock":
                row = ext.run_clock_beat(room, beat, cfg)
            else:
                row = ext.run_chat_beat(room, beat, cfg, stamp, recent=rows)
                state["owning"] = True
        elif beat.get("kind") in ("krew", "krew-off"):
            row = {"beat": beat["id"], "kind": "krew", "skipped": "show-off beats off", "pass": False,
                   "ok": False, "why": "no show-off beats (switch off or module absent)"}
        else:
            row = _show_beat(room, beat)
        rows.append(row)
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        room.log(f"{row.get('beat')}: {'pass' if row.get('pass') else row.get('skipped') or row.get('why') or 'fail'}")
        return str(row.get("control") or "")

    try:
        ACTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACTIVE_FILE.write_text(json.dumps({"day": day, "stamp": stamp, "at": time.time()}), encoding="utf-8")
        waited = 0.0
        why = start_hold(room.hold_start())
        while why and waited < float(cfg.get("hold_max_s", 90)):
            room.log(f"hold before lap: {why}")
            room.sleep(1.0)
            waited += 1.0
            why = start_hold(room.hold_start())
        started = False
        if why:
            stop = f"held: {why}"
        else:
            started = True
            syn_start = _synth()
            if syn_start is not None and hasattr(syn_start, "lap_start") and not only:
                try:
                    room.log(f"flair: {syn_start.lap_start(room, cfg)}")
                except Exception as exc:  # noqa: BLE001
                    room.log(f"flair start failed ({type(exc).__name__})")
            _wind_down(room, listen, float(dial("flourish_wind_s", cfg) or 0))
            room.say(str(cfg.get("opener") or "Live lap."))
            i = 0
            paused = False
            repeat = False
            hold_noted = False
            while i < len(beats):
                if OFF_FILE.exists():
                    stop = "kill file"
                    break
                # His button. The next beat waits. Listening is left as it is.
                if demo_hold.script_held():
                    if not hold_noted:
                        row = {"beat": beats[i].get("id"), "kind": "hold", "held": True,
                               "pass": True, "ok": True, "why": "waiting for you"}
                        rows.append(row)
                        with ledger.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps(row) + "\n")
                        room.log(f"script held before {beats[i].get('id')}")
                        hold_noted = True
                    room.sleep(0.4)
                    continue
                hold_noted = False
                if paused:
                    room.say("Paused.")
                    while paused and not OFF_FILE.exists():
                        heard = _watch(room, 1.0)
                        if heard == "yes":
                            paused = False
                            repeat = state["last_say"] is not None
                            break
                        if heard == "thanks":
                            stop = "thanks"
                            break
                    if stop == "thanks" or OFF_FILE.exists():
                        if OFF_FILE.exists():
                            stop = "kill file"
                        break
                else:
                    for heard in room.pull_controls():
                        if heard == "stop":
                            paused = True
                        elif heard == "thanks":
                            stop = "thanks"
                        elif heard == "yes" and state["last_say"]:
                            repeat = True
                    if stop == "thanks":
                        break
                    if paused:
                        continue
                if repeat and state["last_say"]:
                    repeat = False
                    run_one(state["last_say"])
                control = run_one(beats[i])
                i += 1
                if control == "stop":
                    paused = True
                elif control == "thanks":
                    stop = "thanks"
                    break
                elif control == "yes" and state["last_say"]:
                    repeat = True
            if cards and started and stop == "complete" and not OFF_FILE.exists():
                drill = _crew_drill()
                if drill is None:
                    room.log("cards: this install has no crew drill")
                else:
                    rows.extend(drill.file_in_lap(cfg, room, ledger, run=cards_run))
    finally:
        if cards and _crew_drill() is not None:
            _crew_drill().disarm()
        if started:
            syn_end = _synth()
            if syn_end is not None and hasattr(syn_end, "lap_end") and not only:
                try:
                    room.log(f"flair: {syn_end.lap_end(room, cfg)}")
                except Exception as exc:  # noqa: BLE001
                    room.log(f"flair end failed ({type(exc).__name__})")
            try:
                room.zoom_set(saved[0], saved[1])
            except Exception:  # noqa: BLE001 — restore is the point of the finally
                pass
            try:
                room.shift_set(None)
            except Exception:  # noqa: BLE001
                pass
            try:
                room.cue(home, 0)
            except Exception:  # noqa: BLE001
                pass
        ACTIVE_FILE.unlink(missing_ok=True)
        if chosen is not None and started:
            write_lap_cursor(base, int(lap) + 1)
    # The synthetic-user buffer is refilled after every lap (the maker runs on the ops lane, seconds; never during
    # a beat). Only when the synth switch is on; the lap is never broken by it.
    syn = _synth()
    if syn is not None and started and syn.on() and not only:
        try:
            res_buf = syn.top_up(log=room.log)
            room.log(f"synth buffer after lap: approved {res_buf.get('approved')} pending {res_buf.get('pending')}")
        except Exception as exc:  # noqa: BLE001
            room.log(f"synth top-up failed ({type(exc).__name__}: {exc})")
    res = {"day": day, "stamp": stamp, "stop": stop, "rows": rows, "ledger": str(ledger)}
    if chosen is not None:
        res["set"] = str(chosen.get("id") or lap_sets(cfg).index(chosen))
        res["lap"] = int(lap)
    (d / f"live-{stamp}.md").write_text(report_md(res), encoding="utf-8")
    return res


def report_md(res: dict) -> str:
    lines = [f"# Live lap {res.get('day')} {res.get('stamp')}", "", f"Stop: {res.get('stop')}", ""]
    passed = 0
    graded = 0
    for row in res.get("rows") or []:
        if row.get("skipped"):
            lines.append(f"- {row.get('beat')}: skipped ({row.get('why')})")
            continue
        if row.get("held"):
            lines.append(f"- {row.get('beat')}: held ({row.get('why') or 'waiting for you'})")
            continue
        graded += 1
        if row.get("pass"):
            passed += 1
        extra = ""
        if row.get("oracle"):
            oc = row["oracle"]
            extra = f" oracle {oc.get('covered')}/{oc.get('frames')}"
        mark = "PASS" if row.get("pass") else ("FAIL dev" if row.get("dev") else "FAIL")
        lines.append(f"- {row.get('beat')}: {mark} {row.get('why') or ''}{extra}".rstrip())
    fails = graded - passed
    lines.insert(3, f"Graded {passed}/{graded}. Fails {fails}.")
    lines.append("")
    return "\n".join(lines) + "\n"
