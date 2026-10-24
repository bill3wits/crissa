#!/usr/bin/env python3
"""Her mouth: one spoken line, its audio and the mouth sheet that rides it.

Which engine speaks (2026-10-06, crissatest): a named one (CRISSA_TTS, or the `tts` block of
<crissa home>/agent.json) - else, with nothing named, Kokoro when the voice module's server answers, else
the platform voice (`say` on a Mac), else Piper when a model is named, else NOTHING: the line stays text and
this says why. A public install never falls back to a service it did not start. A machine with its own
streamed voice names it in its own config (config/house-machine.json: voice_url) or env (VOICE_URL); only then
does that path run, with the machine's named backup behind it. Then optional --ref-fallback.

  house-speak.py --voice crissa --text "Hello from Crissa"
  house-speak.py --voice lila --text "Hello from Lila" --sink <sink name>
"""
from __future__ import annotations

import argparse
import contextlib
import array
import io
import json
import math
import os
import base64
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path

# A MAC'S SYSTEM PROXY NEVER CARRIES A LOOPBACK CALL (2026-10-04): urllib applies macOS's proxy settings to
# 127.0.0.1. Any *_proxy in the environment replaces the system settings outright on macOS (urllib reads the
# environment first), so a Mac that needs a proxy to go out sets HTTPS_PROXY beside this. Kill CRISSA_NO_PROXY=0.
if sys.platform == "darwin" and os.environ.get("CRISSA_NO_PROXY", "1").strip() != "0":
    for _k in ("NO_PROXY", "no_proxy"):
        os.environ.setdefault(_k, "127.0.0.1,localhost")


def _crissa_home() -> Path:
    """The state directory (lib/crissa_home): CRISSA_HOME, else the avatar
    manifest's `state_home`, else ~/.crissa. lib goes on the path here because
    this is read at import, before the helpers below add it."""
    lib = Path(__file__).resolve().parent.parent / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        import crissa_home
        return crissa_home.HOME
    except ImportError:
        return Path.home() / ".crissa"


def _is_house() -> bool:
    """The house's own install (lib/crissa_home.is_house); a pack is not. Read at import, before anything else
    has put lib on sys.path, so it puts it there itself (as _crissa_home does): an ImportError here would read
    the house as a pack and turn its own ear, duck and tool hold off."""
    lib = Path(__file__).resolve().parent.parent / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        import crissa_home
        return crissa_home.is_house()
    except ImportError:
        return False


_HOUSE = _is_house()


def _house_value(key: str) -> str:
    """This machine's own service address (config/house-machine.json via lib/crissa_home.house_value); "" in a
    pack, which carries no such file (the User 2026-10-06: machine-specific values live in config, never code)."""
    lib = Path(__file__).resolve().parent.parent / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        import crissa_home
        return crissa_home.house_value(key)
    except (ImportError, AttributeError):
        return ""


def _player_url(path: str = "") -> str:
    """The player this line belongs to: HOUSE_CONDUCTOR, else this machine's wall (house config), else the pack's
    own player from the shared port table (offset-aware)."""
    base = (os.environ.get("HOUSE_CONDUCTOR", "").strip() or _house_value("wall_conductor"))
    if not base:
        try:
            import crissa_ports
            base = crissa_ports.url("player")
        except (ImportError, KeyError, AttributeError):
            base = "http://127.0.0.1:%d" % (8100 + int(os.environ.get("CRISSA_PORT_OFFSET", "0") or 0))
    return base.rstrip("/") + path


# The streamed voice (the /stream contract: Chatterbox Turbo, Multilingual, Pocket): only where this machine names it
# (env, else its own config). Empty = not here. MAIN is the name its lines carry in the log and in `ok <name>-stream`
# (this machine's tts_main, else "voice"; a pack's streamed voice names itself, _main_name()).
VOICE_URL = os.environ.get("VOICE_URL", "").strip() or _house_value("voice_url")
MAIN = _house_value("tts_main").lower() or "voice"
# WHAT SPEAKS WHEN THE MAIN VOICE DOES NOT, AND WHO SAYS THE ANNOUNCER'S LINES (2026-10-06, the voice tiers): one name each,
# from this machine's config (tts_backup, tts_announcer; '' announcer = the main voice says them). A pack whose voice
# tier is a streamed voice gets both from the seam (_crissa_tts_cfg). A name the seam does not own is a machine's own
# engine (lib/house_voices ENGINES, absent in a pack).
BACKUP = os.environ.get("HOUSE_SPEAK_BACKUP", "").strip().lower() or _house_value("tts_backup").lower()
ANNOUNCER = os.environ.get("HOUSE_SPEAK_ANNOUNCER", "").strip().lower() or _house_value("tts_announcer").lower()


# ONE RECORD PER LINE (the User 10-01). The wall spawns this with stdout/stderr on /dev/null, so on
# 09-28 two lines played with no mouth sheet and nothing said why. Every line now appends a `start`
# record before it synthesises and an `end` record when it returns, raises or is SIGTERM'd, with
# what the sheet did (sheets, POSTs ok / failed, the first failure) and which clock ran. A start
# with no end is a line that was SIGKILLed. Written only before the voice starts and after it
# ends, never from the audio path; any error here is swallowed. Kill HOUSE_SPEAK_LINE_LOG=0.
LINE_LOG = Path(os.environ.get("HOUSE_SPEAK_LINE_LOG_PATH", str(_crissa_home() / "logs" / "house-speak-lines.jsonl")))
LINE_LOG_MAX = 4_000_000          # bytes; then the file moves to .1 (one old copy)
_LINE: dict = {"sheets": 0, "posts_ok": 0, "posts_failed": 0, "post_error": None, "path": None}


def _line_log_on() -> bool:
    return os.environ.get("HOUSE_SPEAK_LINE_LOG", "1").strip().lower() not in ("0", "off", "false", "no")


def _line_log(event: str, **kw) -> None:
    if not _line_log_on():
        return
    try:
        rec = {k: v for k, v in _LINE.items() if not k.startswith("_")}
        rec.update(event=event, ts=round(time.time(), 3), pid=os.getpid(), **kw)
        LINE_LOG.parent.mkdir(parents=True, exist_ok=True)
        try:
            if LINE_LOG.stat().st_size > LINE_LOG_MAX:
                os.replace(LINE_LOG, LINE_LOG.with_name(LINE_LOG.name + ".1"))
        except OSError:
            pass
        with open(LINE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:  # noqa: BLE001  a log must never cost her a line
        pass


def _parent_name() -> str:
    try:
        raw = Path("/proc/%d/cmdline" % os.getppid()).read_bytes().split(b"\0")
        args = [a.decode(errors="replace") for a in raw if a]
        for a in args[1:3] if len(args) > 1 else []:
            if a.endswith(".py") or "/" in a:
                return Path(a).name
        return Path(args[0]).name if args else ""
    except OSError:
        return ""


GPU_MODE_FILE = Path(
    os.environ.get("HOUSE_GPU_MODE", str(_crissa_home() / "house-gpu-mode"))
)
REFS = Path(os.environ.get("VOICE_REFS", _crissa_home() / "tts/qwen/refs"))
# Agent / caller voice names -> the speech engine's reference names. A pack speaks its two characters;
# the house's other speakers come from lib/house_voices (house only, absent in a pack).
VOICE_ALIASES = {
    "crissa": "crissa",
    "lila": "lila",
    "host-lila": "lila",  # the wizard's host tag: Lila hosts every line in her own voice
}


def _on_mac() -> bool:
    """macOS: lib/crissa_tts decides whether this Mac speaks with `say`. The house is Linux: never true there."""
    return sys.platform == "darwin"



def _unmute_line_sink(sink: str) -> None:
    """The house unmutes its room sink for every line (its room, its rule). A pack never touches the person's mute or
    volume: a muted computer stays muted (the User 2026-10-10, .101 U: "never change the person's system volume")."""
    if not _HOUSE:
        return
    za = _za()
    if za is not None:
        za.set_mute(False, sink)
    else:
        subprocess.run(["pactl", "set-sink-mute", sink, "0"], capture_output=True)


def _za():
    """lib/crissa_audio when CRISSA_AUDIO_SHIM=1, else None (today's direct calls).

    OFF BY DEFAULT, and that is deliberate (Phase 3, 2026-09-22): this script is
    not resident — the wall conductor execs it from disk for every line — so a
    default of on would put her next Say on the shim the moment a commit landed,
    and the restart window would never see it. The house turns it on through
    the window; the cold room that proved it sets 1."""
    if os.environ.get("CRISSA_AUDIO_SHIM", "1" if sys.platform == "darwin" else "0").strip() != "1":
        return None
    here = Path(__file__).resolve().parent.parent / "lib"
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import crissa_audio
    except ImportError:
        return None
    return crissa_audio


def resolve_sink(explicit: str | None) -> str:
    here = Path(__file__).resolve().parent.parent / "lib"
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from audio_sink import speak_sink

    za = _za()
    if za is not None:
        # A NAME THAT IS NOT LOADED DOES NOT PLAY. speak_sink honours a wanted
        # sink only if it exists and otherwise falls through to the room's
        # canceller sink silently. Here a real name must be present or the line
        # is refused (SinkNotFound). Empty, the canceller sink and auto keep
        # speak_sink's own order, which the wall, Init and the announcer share.
        w = (explicit or os.environ.get("VOICE_SINK") or "").strip()
        if w and w not in (_house_value("aec_sink") or "auto", "auto"):
            return za.resolve_sink(w)
    return speak_sink(explicit)


def _load_speakable():
    """Prefer lib/speakable.py (shared with the announcer and the agent runtime)."""
    import importlib.util
    import sys

    here = Path(__file__).resolve()
    cand = here.parents[1] / "lib" / "speakable.py"
    if cand.is_file():
        spec = importlib.util.spec_from_file_location("house_speakable", cand)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            sys.modules["house_speakable"] = mod
            spec.loader.exec_module(mod)
            return mod
    return None


_SP = _load_speakable()


def _speech_lock_cm(timeout: float = 180.0):
    """Share house-speech.lock with the announcer so two voices never overlap paplay."""
    lib = Path(__file__).resolve().parent.parent / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    from speech_lock import speech_lock

    return speech_lock(timeout=timeout)


def pa_yield() -> bool:
    """Cut a running PA line before the voice takes the shared speech lock.

    A player shipped without the house announcer has no PA line to cut, so the
    module's OWN absence answers False. Only that: a house_announce that is
    present and broken — a name missing inside it included — still raises and
    kills the line, as it always has; a wider catch would lose the cut in the
    house silently. HOUSE_PA_YIELD=0 stays the kill (read inside the module).
    """
    lib = Path(__file__).resolve().parent.parent / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        from house_announce import pa_yield as _yield
    except ModuleNotFoundError as e:
        if e.name != "house_announce":
            raise
        return False

    return _yield()


def strip_unspeakable(text: str) -> str:
    """Drop structures a mouth cannot say (User 2026-08-04 / 08-06).

    Tables, code, URLs, long IDs → prose. IDs become "… ending in ###".
    """
    if _SP is not None:
        return _SP.strip_unspeakable(text)
    # minimal fallback if lib missing
    import re
    t = text or ""
    t = re.sub(r"```[\s\S]*?(?:```|$)", " ", t)
    t = re.sub(r"(?:https?://|www\.)\S+", "a link", t)
    t = re.sub(r"[#*_~|]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def normalize_for_voice(text: str, *, keep_turbo_tags: bool | None = None) -> str:
    """Prep text for the voice engine (Turbo on the streamed voice port).

    the User 2026-08-12: keep contractions (casual speech). Older path expanded
    them because the voice sometimes collapsed "I'm" to a near-silent clip
    (measured 2026-07-31); set HOUSE_SPEAK_EXPAND_CONTRACTIONS=1 to restore.

    Name phonetics via speakable.for_speech. Crissa keeps the Y (not Zoh-ee).
    Logs and typed replies keep the real spelling — this is mouth-only.

    keep_turbo_tags: when True (or None and voice_is_turbo()), preserve Turbo
    tags like [laugh] for TTS; mouth sheet uses for_mouth separately. When
    False, whole [tag] tokens are stripped so the backups never say "laugh".
    Unknown [word] tags on Turbo stay in the TTS line and are logged.
    """
    import re

    if keep_turbo_tags is None:
        keep_turbo_tags = voice_is_turbo()

    unknown: list[str] = []
    # Prefer full for_speech (IDs + name phonetics) when lib present
    if _SP is not None and keep_turbo_tags and hasattr(_SP, "for_turbo_tts"):
        t, _known, unknown = _SP.for_turbo_tts(text)
    elif _SP is not None and hasattr(_SP, "for_speech"):
        t = _SP.for_speech(text, keep_turbo_tags=bool(keep_turbo_tags))
        if keep_turbo_tags and hasattr(_SP, "classify_bracket_tags"):
            _k, unknown = _SP.classify_bracket_tags(text or "")
    else:
        t = strip_unspeakable(text)
        t = re.sub(r"\bAda\b", "ā′də", t, flags=re.I)

    if unknown:
        print(
            "turbo-tags unknown (left for TTS): %s" % ", ".join("[%s]" % u for u in unknown),
            file=sys.stderr,
            flush=True,
        )

    expand = os.environ.get("HOUSE_SPEAK_EXPAND_CONTRACTIONS", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if expand:
        pairs = (
            (r"\bI'm\b", "I am"),
            (r"\bI've\b", "I have"),
            (r"\bI'll\b", "I will"),
            (r"\bI'd\b", "I would"),
            (r"\bdon't\b", "do not"),
            (r"\bdoesn't\b", "does not"),
            (r"\bcan't\b", "cannot"),
            (r"\bwon't\b", "will not"),
            (r"\bit's\b", "it is"),
            (r"\bthat's\b", "that is"),
            (r"\bwhat's\b", "what is"),
            (r"\bthere's\b", "there is"),
            (r"\byou're\b", "you are"),
            (r"\bwe're\b", "we are"),
            (r"\bthey're\b", "they are"),
            (r"\blet's\b", "let us"),
        )
        for pat, rep in pairs:
            t = re.sub(pat, rep, t, flags=re.IGNORECASE)
    if not t.strip() and (text or "").strip():
        # the whole reply was table/code — never silently speak nothing
        t = "That part is better on a screen than out loud."
    return t


def _voice_timeout(explicit: float | None = None, default: str = "0") -> float | None:
    """HOUSE_SPEAK_VOICE_TIMEOUT: seconds, or 0/empty for no limit. One variable for every streamed-voice path, but each path
    keeps its own default (2026-10-07): the stream 45 s (it always had it; one shared "0" left a wedged server
    holding a line forever), the buffered line untimed in the house (User: keep the warm path untimed) and 90 s in
    a pack (on a Mac with no ffplay every line is buffered)."""
    if explicit is not None:
        return explicit
    raw = os.environ.get("HOUSE_SPEAK_VOICE_TIMEOUT", default).strip()
    return None if raw in ("", "0", "inf", "none") else float(raw)


VOICE_TEXT_CAP = 800


def _voice_text(text: str, where: str = "voice") -> str:
    """Cap a streamed-voice line; never silent (no silent cuts): cap_text cuts at a sentence end or a space, says
    so on stderr, keeps the whole line and puts the cut on the glass (cut_note); the line log gets the event."""
    if len(text) <= VOICE_TEXT_CAP:
        return text
    cut = cap_text(text, VOICE_TEXT_CAP, where)
    _line_log("voice_text_truncated", chars=len(text), kept=len(cut))
    return cut


def voice_pcm(text: str, voice: str, timeout: float | None = None) -> tuple[bytes, int]:
    timeout = _voice_timeout(timeout, default="0" if _HOUSE else "90")
    if not VOICE_URL:
        raise RuntimeError("no streamed voice on this machine (VOICE_URL / house config voice_url)")
    body = json.dumps({"text": _voice_text(text, "voice"), "voice": voice}).encode()
    req = urllib.request.Request(
        VOICE_URL, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        sr = int(resp.headers.get("X-Sample-Rate") or 24000)
        # chunked L16 — read all body
        raw = resp.read()
    # may be raw s16le without chunk framing if some proxies strip TE;
    # The voice uses chunked transfer: strip chunk headers if present
    if b"\r\n" in raw[:32]:
        # decode chunked manually
        out = bytearray()
        i = 0
        while i < len(raw):
            j = raw.find(b"\r\n", i)
            if j < 0:
                break
            try:
                n = int(raw[i:j], 16)
            except ValueError:
                out.extend(raw[i:])
                break
            i = j + 2
            if n == 0:
                break
            out.extend(raw[i : i + n])
            i = i + n + 2  # skip data + CRLF
        raw = bytes(out)
    return raw, sr


# WHAT ACTUALLY FAILED (2026-10-07, .101): every failure on the streamed path ended in "no speech engine answered
# (voice http://127.0.0.1:8188/stream ...)", so a voice that WAS serving and a computer that could not PLAY the line
# (no `pactl` / `paplay`) read the same, and the reason sat only in the speak log. The last line - the one the Wall
# shows - now says which it was, in plain words. Nothing about what is tried, or in what order, changes here.
PLAYER_TOOLS = {"pactl": "pulseaudio-utils", "paplay": "pulseaudio-utils", "pacat": "pulseaudio-utils",
                "parec": "pulseaudio-utils", "ffplay": "ffmpeg", "ffmpeg": "ffmpeg", "afplay": "macOS"}


def why_failed(e: BaseException, url: str = "") -> tuple[str, bool]:
    """(plain reason, played): played=False when the voice answered and the computer could not play the line."""
    import urllib.error
    where = (url or VOICE_URL or "").rsplit("/stream", 1)[0].replace("http://", "")
    if isinstance(e, FileNotFoundError):
        tool = Path(str(e.filename or "")).name
        if tool in PLAYER_TOOLS:
            return ("this computer cannot play sound for her: the program '%s' is not installed (it comes with %s)"
                    % (tool, PLAYER_TOOLS[tool]), False)
        return ("this computer cannot play sound for her: %s is not installed" % (tool or e), False)
    if isinstance(e, urllib.error.HTTPError):
        return ("the voice at %s refused the line: HTTP %d" % (where, e.code), True)
    if isinstance(e, urllib.error.URLError):
        r = getattr(e, "reason", e)
        if isinstance(r, ConnectionRefusedError):
            return ("nothing answered at %s" % where, True)
        return ("the voice at %s did not answer: %s" % (where, r), True)
    if isinstance(e, (TimeoutError, OSError)) and "timed out" in str(e):
        return ("the voice at %s did not answer in time" % where, True)
    if isinstance(e, subprocess.CalledProcessError):
        return ("this computer cannot play sound for her: %s exited %s" % (
            Path(str(e.cmd[0] if e.cmd else "")).name, e.returncode), False)
    return ("%s: %s" % (type(e).__name__, e), True)


def voice_health(timeout: float = 0.5) -> dict:
    """Best-effort /health JSON from the voice server on VOICE_URL."""
    if not VOICE_URL:
        return {}
    try:
        with urllib.request.urlopen(VOICE_URL.rsplit("/", 1)[0] + "/health", timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception:
        return {}


_TURBO: dict = {}


def voice_is_turbo(health: dict | None = None, timeout: float = 0.3) -> bool:
    """True when the streamed voice is Chatterbox Turbo: [laugh]/[chuckle]/[cough] stay on its TTS line.

    A pack names it (_STREAMED, set by _crissa_tts_cfg); the house asks VOICE_URL's /health once a line."""
    force = str(os.environ.get("HOUSE_SPEAK_FORCE_TURBO_TAGS", "")).strip().lower()
    if force in ("1", "true", "yes", "on"):
        return True
    if force in ("0", "false", "no", "off"):
        return False
    if _STREAMED["name"]:
        return _STREAMED["name"] == "turbo"
    if health is None and "house" in _TURBO:
        return _TURBO["house"]
    h = health if health is not None else voice_health(timeout=timeout)
    _TURBO["house"] = str(h.get("engine") or "") == "chatterbox-turbo"
    return _TURBO["house"]


def cap_text(text: str, cap: int, where: str) -> str:
    """At most `cap` chars, cut at a sentence end (else a space) and SAID on stderr (ship review 2026-10-07: a
    long line was cut mid-word at 800 with no word of it, and the mouth and the audio ended in different places)."""
    t = text or ""
    if len(t) <= cap:
        return t
    head = t[:cap]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    cut = cut + 1 if cut >= cap // 2 else head.rfind(" ")
    out = (head[:cut] if cut >= cap // 2 else head).rstrip()
    print("line cut: %d -> %d chars (%s cap %d)" % (len(t), len(out), where, cap), file=sys.stderr, flush=True)
    cut_note(where, len(out), len(t))
    return out


_CUT: dict = {"text": "", "posted": False}   # the line as asked (main sets it) and whether the glass was told


def cut_note(where: str, kept: int, total: int) -> dict | None:
    """A cut is never lost (the User 2026-10-07): the whole line goes to <home>/cut-text/line-<sha12>.txt (the
    folder crissa_talk.keep writes and the player serves at /v1/cut/<name>), the line log gets `cuts`, and the
    player is told ONCE a line, so the glass shows "line: Cut at N of M characters" with the whole text a click
    away. Never raises; a player that does not know /v1/cut answers 404 and the file and the log still stand.
    Kill HOUSE_SPEAK_CUT_NOTE=0 (the stderr line stays)."""
    rec = {"where": where, "kept": int(kept), "total": int(total)}
    _LINE.setdefault("cuts", []).append(rec)
    if os.environ.get("HOUSE_SPEAK_CUT_NOTE", "1").strip().lower() in ("0", "off", "false", "no"):
        return None
    full = _CUT["text"] or ""
    if not full:
        return None
    try:
        import hashlib
        name = "line-%s.txt" % hashlib.sha256(full.encode()).hexdigest()[:12]
        f = _crissa_home() / "cut-text" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(full, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("cut note: the whole line was not kept (%s)" % e, file=sys.stderr, flush=True)
        return None
    rec["name"] = name
    if _CUT["posted"]:
        return rec
    _CUT["posted"] = True
    try:
        body = json.dumps({"what": "line", "kept": int(kept), "total": len(full), "name": name}).encode()
        req = urllib.request.Request(_player_url("/v1/cut"), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=0.5).close()
        rec["told"] = True
    except Exception as e:  # noqa: BLE001
        rec["told"] = "%s" % type(e).__name__
    return rec


def voice_pcm_robust(text: str, voice: str, timeout: float | None = None,
                    keep_turbo_tags: bool | None = None) -> tuple[bytes, int, str]:
    """Synthesize with contraction normalize + short-clip retry.

    Returns (pcm, sr, note). note is empty on first-try success.
    """
    # ~0.25s mono s16 @ 24k — below this is "ok" but not hearable as a reply
    min_pcm = int(os.environ.get("HOUSE_SPEAK_MIN_PCM", "12000"))
    primary = normalize_for_voice(text, keep_turbo_tags=keep_turbo_tags)
    pcm, sr = voice_pcm(primary, voice, timeout=timeout)
    note = "normalized" if primary != text else ""
    if len(pcm) >= min_pcm:
        return pcm, sr, note
    # Retry alternate phrasing once
    alt = normalize_for_voice(text.replace(",", " ").replace("  ", " ").strip(), keep_turbo_tags=keep_turbo_tags)
    if alt and alt != primary:
        pcm2, sr2 = voice_pcm(alt, voice, timeout=timeout)
        if len(pcm2) > len(pcm):
            pcm, sr, note = pcm2, sr2, "retry-alt"
    if len(pcm) < min_pcm:
        pcm3, sr3 = voice_pcm("Yes. I am listening.", voice, timeout=timeout)
        if len(pcm3) > len(pcm):
            return pcm3, sr3, "fallback-listen"
    return pcm, sr, note or "short"


def occupancy_mode() -> str:
    try:
        raw = GPU_MODE_FILE.read_text(encoding="utf-8").strip().split()
    except OSError:
        return ""
    return (raw[0] if raw else "").strip().lower()


def voice_healthy(timeout: float = 0.5) -> bool:
    if not VOICE_URL:
        return False
    try:
        with urllib.request.urlopen(VOICE_URL.rsplit("/", 1)[0] + "/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def pick_engine(explicit: str | None = None) -> str:
    """auto: Standard → the main voice (MAIN). Render|Beast → the main voice when it answers (Voice ON
    keeps it resident, so a line costs no VRAM), else this machine's render engine on the CPU (lib/house_voices
    RENDER_ENGINE; none in a pack). Measured 2026-09-13 (the User: "no sound works from Say"): the Composer's drill
    line on the CPU engine was 37 s of silence before the first sound; the voice streamed its first hop in 2.1 s.
    HOUSE_SPEAK_RENDER_VOICE=0 = the render engine first. Env HOUSE_SPEAK_ENGINE wins."""
    raw = (explicit or os.environ.get("HOUSE_SPEAK_ENGINE") or "auto").strip().lower()
    if raw in (MAIN, "voice"):
        return MAIN
    if _house_engine(raw) is not None:
        return raw
    render = str(getattr(_HV, "RENDER_ENGINE", "") or "")
    if render and occupancy_mode() in ("render", "beast"):
        render_main = os.environ.get("HOUSE_SPEAK_RENDER_VOICE", "1").strip().lower() not in ("0", "false", "no", "off")
        if render_main and voice_healthy():
            return MAIN
        return render
    return MAIN


def _house_engine(name: str):
    """This machine's own engine by that name (lib/house_voices ENGINES: fn(text, voice, cap_text) -> (pcm, sr)), or
    None: in a pack, and for any name the voice seam owns."""
    return (getattr(_HV, "ENGINES", None) or {}).get(name) if name and _HV is not None else None


LEAD_SILENCE_MS = int(os.environ.get("HOUSE_SPEAK_LEAD_MS", "400"))
# Vocative lean-in: the voice synths during the enter; paplay waits so Yeah?
# lands on the closeup loop, not mid-lean (mouth still desk-scale).
# 0 = play as soon as ready.
_NOT_BEFORE = 0.0


def hold_until_not_before() -> None:
    """Sleep until --not-before. Cap 8s so a bad stamp cannot mute her."""
    t = float(_NOT_BEFORE or 0.0)
    if t <= 0:
        return
    delay = t - time.time()
    if delay <= 0:
        return
    time.sleep(min(delay, 8.0))

# --- the air clock ------------------------------------------------------
# `speaking` is the EAR's gate and opens early on purpose: the lead pad is
# silence, so being deaf through it costs nothing. The GLASS needs a different
# answer — when the SINK actually starts her voice — because a mouth that
# opens before the first word reads as a dub, not as a person.
#
# MEASURED 2026-08-27, parec on the speaker dongle's monitor against t_pad_write,
# 10 runs, median 34.5 ms. Do NOT substitute prebuf_ms: `--latency-msec` is a
# buffer SIZE, not a pre-roll (100/500/2000 ms all measured ~34 ms), and using
# 500 here would open her mouth 460 ms LATE — worse than the bug it replaces.
# MEASURED AGAIN 2026-09-18 at the EAR (the User: "we are not measuring from speaker maybe? …
# or from mic would be more like ear"): scripts/mouth-air-latency.py, eight tones through
# paplay on the canceller sink with these flags, quiet room, speaker in place — reSpeaker (the
# room's ear) 111 ms, Shure 79 ms, the sink's own monitor −4 ms. The digital path adds
# nothing; the ~75 ms the old number missed is the sink's device buffer, the DAC and the
# air, minus ~20 ms of the instrument's own capture buffer. 90 is the ear. Re-run the
# script when the sink, the speaker or the mics move.
MOUTH_LATENCY_MS = float(os.environ.get("HOUSE_MOUTH_LATENCY_MS", "90"))


def sheet_gaps_on() -> bool:
    """A pause in the voice is a pause in the sheet (the User 2026-10-12, "I agree to all fixes"). Turbo streams a line
    sentence by sentence; while it makes the next sentence the player runs dry and the room hears silence, but the sheet
    stamped every row at air_at + the samples written, as if the audio never stopped. Every row after a pause landed
    that much early: on the 13:03 reading the wall's newest row sat 320-500 ms behind the clock at p10 and under 100 ms
    ahead 31 % of the time, so the painter held a stale row and then jumped. On: when written audio would play later
    than its stamp (the player ran dry), the rest of the line's rows move by the pause and one rest row covers it.
    A PACK TOO (round 15, 2026-10-12): Pocket in a pack runs dry the same way when it is slower than the player (a busy
    CPU at the start of an answer): replayed at 1.1x real time, rows after the pauses landed 230-330 ms early by the
    last pieces, about 95 ms with this on (docs/checkpoints/round15-gaps-pack-20261012). HOUSE_SPEAK_SHEET_GAPS=0, or
    the file <state home>/mouth-sheet-gaps.off (a pack's ~/.crissa), puts the old stamps back on the next line."""
    raw = os.environ.get("HOUSE_SPEAK_SHEET_GAPS", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    try:
        import crissa_home
        return not (crissa_home.HOME / "mouth-sheet-gaps.off").exists()
    except Exception:  # noqa: BLE001
        return True


SHEET_GAP_MIN_S = float(os.environ.get("HOUSE_SPEAK_SHEET_GAP_MIN_MS", "40")) / 1000.0
# WHOLE-SHEET LEAD (the User 2026-09-11 "tad late to hit the shape … 100 ms"):
# every hop's pts is stamped this much EARLIER than its audio, so the glass
# shows each class before the sound. Measured at the sink: the first class
# already sat −97 ms (the 120 ms pre-open); the words after it sat ON their
# sound, and HOUSE_ALIGN_LEAD_MS could not move them — schedule() re-anchors
# the aligned spans onto the voiced region, which cancels that shift. This
# one is applied at the stamp, after the clock, so nothing cancels it. The
# on-air window is not moved; the Conductor's prep window (lead_ms 100)
# covers the head of it. 0 = off.
MOUTH_SHEET_LEAD_S = float(os.environ.get("HOUSE_MOUTH_SHEET_LEAD_MS", "0")) / 1000.0   # the spawner's env; see sheet_lead_s()

# ONE LEAD FOR EVERY SPEAKER (the User 2026-09-17: "unify it"). This constant came
# from whoever spawned the line: 0 for callouts and the hello (nobody set it),
# 200 for every real reply (ear-bridge's drop-in), the library dial for the
# Composer's Says — so a lead tuned and locked in the Composer never reached
# her replies. Now a line whose spawner did not hand it the lead on purpose
# (HOUSE_MOUTH_LEAD_SRC=explicit: a conductor's own Say, which carries ITS live
# dial — that is what a bench tunes with) asks the WALL conductor for its live
# sheet lead, once, as it starts. No answer in 0.3 s = the env's value, else
# 200 (the 09-11 eye-set value). Off: HOUSE_SPEAK_LEAD_UNIFY=0.
LEAD_UNIFY = os.environ.get("HOUSE_SPEAK_LEAD_UNIFY", "1").strip().lower() not in ("0", "false", "no", "off")
LEAD_FALLBACK_MS = 200.0
_LEAD: dict = {}


def resolve_sheet_lead_ms(env: dict | None = None, ask=None) -> tuple[float, str]:
    """(lead ms, where it came from). `ask()` returns the wall's live dial."""
    env = os.environ if env is None else env
    raw = env.get("HOUSE_MOUTH_SHEET_LEAD_MS")
    mine = float(raw) if raw not in (None, "") else None
    unify = str(env.get("HOUSE_SPEAK_LEAD_UNIFY", "1")).strip().lower() not in ("0", "false", "no", "off")
    if not unify:
        return (mine or 0.0), "env (HOUSE_SPEAK_LEAD_UNIFY=0)"
    if env.get("HOUSE_MOUTH_LEAD_SRC") == "explicit":
        return (mine or 0.0), "explicit (the conductor's own Say)"
    try:
        v = float((ask or _ask_wall_lead)())
        # round 36 (the User 2026-10-20: "a full second either way"): the floor is -625 ms (her mouth after her sound
        # is safe: measured, the player paints every row at -600); the ceiling stays 600 (lib/crissa_lipsync.py says why)
        return max(-625.0, min(600.0, v)), "the wall's live dial"
    except Exception:  # noqa: BLE001 — a conductor that is down must not cost her the line
        return (mine if mine is not None else LEAD_FALLBACK_MS), "fallback (the wall did not answer)"


_WALL: dict = {}      # the wall's answer, asked ONCE a line: the lead and the tune ride one request


def _ask_wall() -> dict:
    if _WALL.get("d") is None:
        import urllib.request
        req = urllib.request.Request(_player_url("/v1/mouth-tune"), data=b"{}",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=0.3) as r:
            _WALL["d"] = json.loads(r.read().decode() or "{}")
    return _WALL["d"]


def _ask_wall_lead() -> float:
    d = _ask_wall()
    # dial + the calibrated zero (2026-09-18); an older conductor answers the dial alone
    return float(d["sheet_lead_effective_ms"] if d.get("sheet_lead_effective_ms") is not None else d["sheet_lead_ms"])


# THE WHOLE TUNE REACHES HER REPLIES, NOT THE LEAD ALONE (the User 2026-09-27: "it is not
# translating our tunes very well"). A bench hands house-speak every timing dial
# (speak_timing_env); a real reply is spawned by the ear, which hands it none, so it ran on
# this code's own defaults - min hold 1.5 f, consonant 2.5 f, no pads, no elastic pause, no
# blip, no tail - under a lead that WAS the tune's. The wall's answer to the lead question
# already carries them all, so they cost no second request. A spawner that handed its own
# (HOUSE_MOUTH_LEAD_SRC=explicit) keeps them; no answer = the env as it is.
# Off: HOUSE_SPEAK_TUNE_UNIFY=0, or the file <state home>/speak-tune-unify.off.
TUNE_KEYS = (("min_hold", "HOUSE_MOUTH_MIN_HOLD", int, 1, 36), ("cons_hold", "HOUSE_MOUTH_CONS_HOLD", int, 1, 36),
             ("sent_pad", "HOUSE_MOUTH_SENT_PAD", int, 0, 36), ("word_pad", "HOUSE_MOUTH_WORD_PAD", int, 0, 36),
             ("pause_words", "HOUSE_MOUTH_PAUSE_WORDS", float, 0.0, 6.0), ("blip_words", "HOUSE_MOUTH_BLIP_WORDS", float, 0.0, 2.0),
             ("tail_words", "HOUSE_MOUTH_TAIL_WORDS", float, 0.0, 2.0),
             ("timing_lead_ms", "HOUSE_MOUTH_TIMING_LEAD_MS", float, 0.0, 400.0))


def tune_unify_on(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    if str(env.get("HOUSE_SPEAK_TUNE_UNIFY", "1")).strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        lib = Path(__file__).resolve().parent.parent / "lib"
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        from crissa_home import HOME as _home
        return not (_home / "speak-tune-unify.off").is_file()
    except Exception:  # noqa: BLE001
        return True


def resolve_tune(env: dict | None = None, ask=None) -> tuple[dict, str]:
    """({ENV NAME: value as a string}, where it came from) - what to set before the clock loads."""
    env = os.environ if env is None else env
    if not tune_unify_on(env):
        return {}, "env (tune unify off)"
    if env.get("HOUSE_MOUTH_LEAD_SRC") == "explicit":
        return {}, "explicit (the spawner handed its own dials)"
    try:
        d = (ask or _ask_wall)() or {}
    except Exception:  # noqa: BLE001 - a conductor that is down must not cost her the line
        return {}, "fallback (the wall did not answer)"
    out = {}
    for key, name, kind, lo, hi in TUNE_KEYS:
        v = d.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        v = max(lo, min(hi, kind(round(v) if kind is int else v)))
        out[name] = str(v) if kind is int else ("%.3f" % v)
    return out, "the wall's live tune"


CALM_DEFAULT_HOPS = 3     # 1.5 f, approved by the User 2026-09-27 ("really nice now")


def resolve_calm(who: str, env: dict | None = None, read=None) -> tuple[int, str]:
    """(calm hops, where from). The calm merge is ON by default at CALM_DEFAULT_HOPS for every line
    (2026-09-27, so a pack carries it: its state home has no file). <state home>/mouth-calm.json =
    {"hops": N, "who": "bench" | "all"} overrides it - hops 0 turns it off; "bench" limits it to lines
    a bench spawned (its who ends @bench). An env value outranks the file."""
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_MOUTH_CALM_HOPS", "")).strip()
    if raw:
        return max(0, min(12, int(float(raw)))), "env"
    try:
        if read is None:
            lib = Path(__file__).resolve().parent.parent / "lib"
            if str(lib) not in sys.path:
                sys.path.insert(0, str(lib))
            from crissa_home import HOME as _home
            d = json.loads((_home / "mouth-calm.json").read_text(encoding="utf-8"))
        else:
            d = read()
        hops = max(0, min(12, int(float(d.get("hops") or 0))))
        scope = str(d.get("who") or "bench").strip().lower()
    except FileNotFoundError:
        return CALM_DEFAULT_HOPS, "default"
    except Exception:  # noqa: BLE001 - a bad file is off, and says so
        return 0, "off (mouth-calm.json unreadable)"
    if scope == "all" or (scope == "bench" and str(who or "").endswith("@bench")):
        return hops, "mouth-calm.json (%s)" % scope
    return 0, "off (the file is for %s)" % scope


# A VOICE'S SHAPE SWITCHES REACH EVERY LINE IT SPEAKS (the User 2026-09-27: "carry them to live").
# Crissa's bench runs espeak spelling, per-shape prep, split-to-next and no tail fade; the wall and
# the ear ran none, so a reply painted different SHAPES from the bench it was tuned on. They are
# env switches of a process, not dials, so they travel as a file: <state home>/speak-voice-env.json
# = {"crissa": {"HOUSE_MOUTH_G2P": "espeak", ...}}. A key the spawner already set wins. Read before
# any mouth module loads (they read these at import). Delete the file = off.
# The defaults travel in code so a pack carries them (2026-09-27); the file overrides per voice,
# and {"crissa": {}} in it turns Crissa's off. Lila's bench tuned with per-shape prep only.
VOICE_ENV_DEFAULT = {
    "crissa": {"HOUSE_MOUTH_G2P": "espeak", "HOUSE_MOUTH_CLASS_PREP": "1",
             "HOUSE_MOUTH_SPLIT_TO_NEXT": "1", "HOUSE_MOUTH_TAIL_FADE": "none"},
    "lila": {"HOUSE_MOUTH_CLASS_PREP": "1"},
}
VOICE_ENV_KEYS = ("HOUSE_MOUTH_G2P", "HOUSE_MOUTH_CLASS_PREP", "HOUSE_MOUTH_CLASS_PREP_TABLE",
                  "HOUSE_MOUTH_CLASS_PREP_PAUSE_F", "HOUSE_MOUTH_SPLIT_TO_NEXT", "HOUSE_MOUTH_TAIL_FADE")


def resolve_voice_env(voice: str, env: dict | None = None, read=None) -> dict:
    env = os.environ if env is None else env
    try:
        if read is None:
            lib = Path(__file__).resolve().parent.parent / "lib"
            if str(lib) not in sys.path:
                sys.path.insert(0, str(lib))
            from crissa_home import HOME as _home
            d = json.loads((_home / "speak-voice-env.json").read_text(encoding="utf-8"))
        else:
            d = read()
    except FileNotFoundError:
        d = {}
    except Exception:  # noqa: BLE001 - a bad file is off
        return {}
    name = str(voice or "").split("@")[0].strip().lower()
    row = d[name] if isinstance(d, dict) and name in d else VOICE_ENV_DEFAULT.get(name, {})
    return {k: str(v) for k, v in row.items() if k in VOICE_ENV_KEYS and not str(env.get(k, "")).strip()}


def prime_tune() -> dict:
    """main() calls this once, after the lead: the clock's modules read these at import."""
    got, src = resolve_tune()
    if got:
        os.environ.update(got)
        for mod, names in (("mouth_timing", {"HOUSE_MOUTH_MIN_HOLD": "MIN_HOLD", "HOUSE_MOUTH_CONS_HOLD": "CONS_HOLD",
                                             "HOUSE_MOUTH_SENT_PAD": "SENT_PAD", "HOUSE_MOUTH_WORD_PAD": "WORD_PAD"}),
                           ("mouth_clock", {"HOUSE_MOUTH_PAUSE_WORDS": "PAUSE_WORDS", "HOUSE_MOUTH_BLIP_WORDS": "BLIP_WORDS",
                                            "HOUSE_MOUTH_TAIL_WORDS": "TAIL_WORDS"})):
            m = sys.modules.get(mod)          # already loaded (a library import): set it there too
            for name, attr in names.items():
                if m is not None and name in got and hasattr(m, attr):
                    setattr(m, attr, type(getattr(m, attr))(float(got[name])))
        global MOUTH_TIMING_LEAD_MS
        if "HOUSE_MOUTH_TIMING_LEAD_MS" in got and "MOUTH_TIMING_LEAD_MS" in globals():
            MOUTH_TIMING_LEAD_MS = float(got["HOUSE_MOUTH_TIMING_LEAD_MS"])
    print("tune: %s%s" % (src, (" - " + " ".join("%s=%s" % (k.replace("HOUSE_MOUTH_", "").lower(), v)
                                                   for k, v in sorted(got.items()))) if got else ""),
          file=sys.stderr, flush=True)
    return got


def prime_sheet_lead() -> float:
    """main() calls this once, before any sound: this process IS one line.
    Imported as a library (tests, the cache builder) nothing is asked and the
    env's value stands — a test must never read a live wall."""
    ms, src = resolve_sheet_lead_ms()
    _LEAD["s"], _LEAD["src"] = ms / 1000.0, src
    print("sheet lead %d ms — %s" % (round(ms), src), file=sys.stderr, flush=True)
    return _LEAD["s"]


def sheet_lead_s() -> float:
    return _LEAD.get("s", MOUTH_SHEET_LEAD_S)
# One switch back to the old pre-paplay open.
MOUTH_AIR = os.environ.get("HOUSE_MOUTH_AIR", "1") not in ("0", "false", "no")
# Where the sheet goes: VO_MOUTH_URL (the player hands its own), else this machine's glass server (house
# config mouth_url), else the pack's own player, which keeps the sheet itself.
MOUTH_URL = (os.environ.get("VO_MOUTH_URL", "").strip() or _house_value("mouth_url") or _player_url("/v1/mouth"))
# F1a/F1b — A SHEET SLOT IS HALF A FILM FRAME (the User 2026-09-20: "F1a f1b,
# f2a f2b, like that"). Every row of the sheet is one semiframe of WALL time,
# so slot n belongs to film frame n // 2 at ANY playback rate.
#
# WHAT WAS WRONG. The slot was a flat 20 ms of playback. A film frame is
# 1000/fps/rate ms of wall, so slots-per-frame was 2.08 at 1x but 8.33 at
# quarter speed — the dwell dials silently meant a QUARTER as much film time
# down there. min_hold 1 read "0.48 f" on the Composer and was really 0.12 f,
# and `cons_seen` (consonant runs >= 2 FILM frames) collapsed to 30 %, which
# dragged r to 0.42. That is the whole reason the AutoTuner forced 1x: a tune
# found at 1/4 could not transfer, so the run was locked to the one speed
# where the grid happened to be honest.
#
# Dividing by the rate here makes the slot stretch with the film, so the
# analysis hop downstream (`MOUTH_HOP_MS * tempo`, and tempo already carries
# the rate) comes out CONSTANT at 1000/fps/2 x speech-tempo — the pre-atempo
# The voice bytes are read at the same content resolution at every speed, and the
# post-atempo sheet rows land on the film's own semiframes.
#
# At 24 fps 1x this is 20.83 ms, a 4 % shift from the old 20 — that is the
# whole behavioural risk, and it is why this landed with a checkpoint.
# Kill: HOUSE_SEMIFRAME_HOP=0 restores the flat 20 ms.
SLOTS_PER_FRAME = 2          # F<n>a, F<n>b — married to the frame, in order


def _semiframe_hop_ms() -> float:
    if os.environ.get("HOUSE_SEMIFRAME_HOP", "1").strip() in ("0", "false", "no"):
        return 20.0
    try:
        fps = float(os.environ.get("HOUSE_CLIP_FPS", "") or 24.0)
    except (TypeError, ValueError):
        fps = 24.0
    try:
        rate = float(os.environ.get("HOUSE_PLAY_RATE", "") or 1.0)
    except (TypeError, ValueError):
        rate = 1.0
    fps = max(1.0, min(240.0, fps))
    rate = max(0.05, min(4.0, rate))
    return (1000.0 / fps) / SLOTS_PER_FRAME / rate


MOUTH_HOP_MS = _semiframe_hop_ms()      # one semiframe of WALL time

# THE CLOCK. Default auto: hops use real word boundaries when rail-align
# (:8195) is already warm; a missing, cold or slow aligner leaves the
# letter-count clock in place. HOUSE_MOUTH_ALIGN=0 is the rollback.
# HOUSE_MOUTH_ALIGN=1 always tries (still fail-open inside arm_clock).
#
# expected_hops() is `letters x 0.08 s` spread evenly. Measured 2026-09-01 on
# clip-speak-phoneme against his hand-dragged word spans, the modelled TOTAL is
# 9.44 s against a true 9.33 s -- 1.01x, the rate is fine -- while mean |drift|
# is 0.27 s and the worst is 0.73 s. The distribution is what is wrong, so no
# better letters-per-second constant fixes it. rail-align returns real word
# boundaries; lib/mouth_clock turns them into a class per hop.
#
# Both clocks used to stamp hop 0 of the PCM as the first sound. The voice's pad
# (~0.5 s head, ~0.4 s tail) then stole 'Okay' and pushed 'variables' late.
# lib/mouth_timing.schedule pins the sheet to voiced energy on both the
# aligned and the letter path. PRE_OPEN is 20 ms. A small extra look-ahead
# (HOUSE_MOUTH_TIMING_LEAD_MS, default 120) beats the voice without the old
# 100-150 ms viseme lead, which reopened the early gap on an anchored clock.
#
# Graded against his 241 per-frame marks (bench/lipsync-keepup/clockfit.py):
# letter 22.4%, aligned 57.7%, his own spans 72.6%, still mouth 20.3%.
def _align_url() -> str:
    """HOUSE_ALIGN_URL, else this machine's aligner (house config align_url), else the pack's own port table."""
    got = os.environ.get("HOUSE_ALIGN_URL", "").strip() or _house_value("align_url")
    if got:
        return got
    try:
        import crissa_ports
        return crissa_ports.url("align") + "/align"
    except (ImportError, KeyError, AttributeError):
        return "http://127.0.0.1:%d/align" % (8195 + int(os.environ.get("CRISSA_PORT_OFFSET", "0") or 0))


MOUTH_ALIGN_URL = _align_url()


def _mouth_align_flag(raw: str | None) -> bool | None:
    """True/False forced; None means auto (probe :8195)."""
    s = (raw if raw is not None else "auto").strip().lower()
    if s in ("0", "false", "no", "off"):
        return False
    if s in ("1", "true", "yes", "on"):
        return True
    return None


MOUTH_ALIGN = _mouth_align_flag(os.environ.get("HOUSE_MOUTH_ALIGN", "auto"))
# SENTENCE STAMPS ON TURBO (the User 2026-10-10: "Can it stream so no lag?"). Turbo writes each sentence as one
# HTTP chunk; lib/turbo_stream.TurboReader pulls them at Turbo's pace on its own thread and each finished sentence
# is aligned at once, so the stamp lands while the sentence before it is still playing. Without it the read loop
# ran at PLAYBACK pace (tempo 1.0 has no ffmpeg stage) and the whole-line alignment started ~2 s before the line
# ended. HOUSE_ALIGN_FIRST_WAIT_S holds her first word up to that long for the first sentence's stamp (0 = no
# wait, the letter clock opens). Any mismatch falls back to the whole-line alignment. Default OFF:
# HOUSE_MOUTH_ALIGN_PIECES=1 arms it; this script is exec'd from disk for every line.
ALIGN_FIRST_WAIT_S = float(os.environ.get("HOUSE_ALIGN_FIRST_WAIT_S", "0.35"))


def align_pieces_on() -> bool:
    return os.environ.get("HOUSE_MOUTH_ALIGN_PIECES", "0").strip().lower() in ("1", "true", "yes", "on")


def _aligner_warm(timeout: float = 0.3) -> bool:
    url = MOUTH_ALIGN_URL.rsplit("/", 1)[0] + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            d = json.loads(r.read())
        return bool(d.get("ok") and d.get("warm"))
    except Exception:
        return False


def mouth_align_on() -> bool:
    """Use the aligned clock? Fail-open: never raise, never block first audio."""
    if MOUTH_ALIGN is False:
        return False
    if MOUTH_ALIGN is True:
        return True
    return _aligner_warm()
# Its own lead, NOT mouth_playlist's. Two clocks want different leads and the
# 100-200 ms clamp there was calibrated against a clock that was insensitive to
# it -- flat 20-23% at every lead. This one peaks sharply: 57.7% at 50 ms
# against 24.9% at 100 ms. Shipping the aligner at 100 would throw the gain away.
MOUTH_ALIGN_LEAD_MS = float(os.environ.get("HOUSE_ALIGN_LEAD_MS", "50"))
# Pre-open of the FIRST class into the pad, in ms. schedule() already
# pre-opens one hop (20 ms). the User 2026-09-07: 20 ms slightly behind; 40 ms
# still a frame or two late; 80 ms still the first Okay 1–2 frames late
# (two-step /oʊkeɪ/). 120 ms = 6 hops copied onto pad REST. Do NOT add
# this to the playhead index — that skipped Okay's OH on the heard vowel
# (Wall Background ~22 fps then missed it; Brave mjpg still caught it).
# 0 is Fable's stamp with no extra lead. Do not put HOUSE_VISEME_LEAD_MS
# (100-200) back on this path.
MOUTH_TIMING_LEAD_MS = float(os.environ.get("HOUSE_MOUTH_TIMING_LEAD_MS", "120"))
# Streaming only. Do not spend an alignment on a fragment that is mostly silence.
# How long the buffered path may wait for the alignment before giving up and
# speaking on the letter clock. Measured ~180 ms for a 3 s line warm; 0.6 s
# leaves room for a longer line without ever holding her voice noticeably.
MOUTH_ALIGN_WAIT_S = float(os.environ.get("HOUSE_ALIGN_WAIT_S", "0.6"))
_AIR_TEXT = ""
# THE FILM'S OWN WORD TIMES (2026-09-18). A tiles drill re-aligned the film's words
# against its stretched voice at every pass (rail-align, an 8 s HTTP timeout, a budget of
# 8 s + audio/8): warm and idle it runs 15x realtime and makes it; under the afternoon's
# load it ran 2x and the 42 s up-close-harvest-4 could not, so the LETTER CLOCK stood —
# the same words said far faster than the film ("detached from script", the User). The film's
# timeline already holds every word's frames; `--words <timeline.json>` uses them and asks
# no aligner. Heard seconds (film seconds / tempo). Empty = the aligner path as before.
_GIVEN_WORDS: list = []
_CLOCK_HOW = ""


def line_clock_note() -> dict:
    """This line's lip clock for the player's log (round 11, .101: "please log the clock per line"): which clock the
    sheet ended on, how far into the line its stamps landed, the sheet lead and the voice. Numbers and short words."""
    try:
        lead = round(sheet_lead_s() * 1000.0, 1)
    except Exception:  # noqa: BLE001
        lead = None
    out = {"how": _CLOCK_HOW or "letter", "after_s": _LINE.get("aligned_after_s"),
           "first_s": _LINE.get("first_stamp_after_s"), "lead_ms": lead, "engine": _LINE.get("engine"),
           "marks": bool(_LINE.get("piece_marks")), "early": bool(_LINE.get("align_early")),
           "chars": _LINE.get("chars")}
    pieces = _LINE.get("pieces") or []
    if pieces:
        # round 12: how many pieces were stamped before their audio began, and the most any one spoke unstamped
        late = [(round(p[1] - p[2], 2), p[0]) for p in pieces if p[1] > p[2]]
        out["pieces"] = len(pieces)
        out["early_pieces"] = len(pieces) - len(late)
        if late:
            out["late_s"], out["late_piece"] = max(late)
        # round 17: the same against her VOICE (a Pocket piece opens with 0.4-1.0 s of quiet, so "before they played"
        # counted silence as unstamped speech: .101's "0.78 s unstamped (piece 1)" was mostly her lead-in quiet), and
        # the silence the stamp hold added. Kill: HOUSE_SPEAK_VOICE_CLOCK=0 (the round-16 note).
        if os.environ.get("HOUSE_SPEAK_VOICE_CLOCK", "1").strip().lower() not in ("0", "false", "no", "off"):
            stamped = {p[0]: p[1] for p in pieces}
            voice = [(round(stamped[n] - v, 2), n) for n, v in (_LINE.get("piece_voice") or []) if n in stamped]
            if voice:
                out["voice_early"] = sum(1 for d, _n in voice if d <= 0)
                out["voice_late_s"] = max(0.0, max(d for d, _n in voice))
            if _LINE.get("stamp_hold_s") is not None:
                out["held_s"] = _LINE["stamp_hold_s"]
    return out
_AIR_WHO = "crissa"
_ROLL_WHO = {
    "crissa": "crissa",
    "lila": "lila",
}


def _house_voices():
    """lib/house_voices (the house's own speakers), or None in a pack: only its own absence answers None."""
    lib = Path(__file__).resolve().parent.parent / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        import house_voices
    except ModuleNotFoundError as e:
        if e.name != "house_voices":
            raise
        return None
    return house_voices


_HV = _house_voices()
if _HV is not None:
    VOICE_ALIASES.update(_HV.VOICE_ALIASES)
    _ROLL_WHO.update(_HV.ROLL_WHO)


MOUTH_TEXT_CAP = 800


def pcm_is_playable(pcm: bytes, min_pcm: int | None = None) -> bool:
    """True when the voice returned enough s16le to be a hearable reply (~0.25s)."""
    if min_pcm is None:
        min_pcm = int(os.environ.get("HOUSE_SPEAK_MIN_PCM", "12000"))
    return len(pcm) >= min_pcm


def tail_starve_ms() -> float:
    """Max gap between voice chunks once paplay is live. Chop is a starved tail."""
    return float(os.environ.get("HOUSE_SPEAK_TAIL_STARVE_MS", "800"))


def chunk_starved(gap_ms: float, limit_ms: float | None = None) -> bool:
    if limit_ms is None:
        limit_ms = tail_starve_ms()
    return gap_ms > limit_ms


def _http_read(resp, n: int, timeout_s: float | None = None) -> bytes:
    """Read up to n bytes; TimeoutError if the socket exceeds timeout_s."""
    import socket

    if getattr(resp, "is_turbo_reader", False):
        return resp.read(n, timeout_s)

    raw = getattr(getattr(resp, "fp", None), "raw", None)
    if timeout_s is not None and raw is not None and hasattr(raw, "settimeout"):
        try:
            raw.settimeout(timeout_s)
        except Exception:
            pass
    try:
        return resp.read(n)
    except socket.timeout as e:
        raise TimeoutError("voice tail starve") from e


# THE WALL KEEPS 512 HOPS PER POST AND 600 ROWS IN ALL (voiceonly-home-server
# mouth_ingest, prod-locked). The buffered path (slow first voice chunk) fed a
# whole 21 s line in one feed() → one 1065-hop batch → the wall kept 10.24 s
# and the mouth stopped at "shut for r" while the voice went on (the User
# 2026-09-13 01:4x). Posts are cut into ≤ POST_MAX_HOPS pieces and the drain
# paces them: a piece is posted once its first hop is within POST_AHEAD_S of
# now, so the wall's window never sees more than ~8 s of future.
# 200 hops (4 s) per piece, posted 4 s ahead: the wall holds at most
# 0.5 s back + 4 s ahead + one 4 s piece = 425 rows < 600 (a first cut at
# 400 / 8 s put 671 rows on the wall at once and lost the line's first 1.4 s).
POST_MAX_HOPS = 200
POST_AHEAD_S = 4.0
# RENDER ONLY (the User 2026-10-16, the phone app: "see if you could serve the forward loop as an app"): a line is made,
# aligned and its whole sheet posted as fast as the machine goes, and nothing plays - the caller keeps --out and plays it
# elsewhere (a phone). No player, no post pacing, no TTS gate (the room hears nothing, so the ear is not deafened), no
# speech lock (Crissa's lines never wait on it). Needs HOUSE_SPEAK_BUFFER=1 (the whole line before the sheet). Default
# off = every house line exactly as before.
RENDER_ONLY = os.environ.get("HOUSE_SPEAK_RENDER_ONLY", "0").strip().lower() in ("1", "true", "yes", "on")


def split_payload(payload: dict, max_hops: int = POST_MAX_HOPS) -> list[dict]:
    """One /v1/mouth payload → pieces of ≤ max_hops with pts0 advanced."""
    rms = payload.get("rms")
    if not isinstance(rms, list) or len(rms) <= max_hops:
        return [payload]
    hop = float(payload.get("hop_ms") or MOUTH_HOP_MS) / 1000.0
    vis = payload.get("visemes")
    pts0 = float(payload.get("pts0"))
    out = []
    for k in range(0, len(rms), max_hops):
        piece = dict(payload)
        piece["rms"] = rms[k:k + max_hops]
        piece["pts0"] = round(pts0 + k * hop, 4)
        if isinstance(vis, list):
            piece["visemes"] = vis[k:k + max_hops]
        if k > 0:
            piece.pop("pcm16", None)      # the samples ride the first piece
        out.append(piece)
    return out


class MouthAir:
    """The envelope of what paplay is about to play, as a schedule.

    Every frame's ``pts`` is ``air_at + samples/sr`` — fixed when the bytes are
    written, not when the POST lands, so a slow or dropped POST shifts nothing.
    We write into paplay ahead of playback, so the wall receives each frame
    before the room hears it and can ease into a syllable instead of chasing it.

    Feed AFTER atempo (the bytes going into ``player.stdin``): that is the audio
    the room actually hears, so no TEMPO divisor is needed or wanted.
    """

    def __init__(self, air_at: float, sr: int):
        self.air_at = air_at
        _LIVE_AIR["air"] = self                   # round 20: the sheet a signal cuts (_mouth_cut)
        _LINE["sheets"] = int(_LINE.get("sheets") or 0) + 1
        _LINE.setdefault("air_at", round(air_at, 3))
        self.sr = sr
        self.samples = 0
        self.gap_s = 0.0          # the pauses so far, seconds (sheet_gaps_on)
        self._gaps: list = []     # [(hop index, gap_s from that hop on)]
        self._gaps_on = sheet_gaps_on()
        self._armed = False
        self._gate_fixed = False
        self.hop = max(1, int(sr * MOUTH_HOP_MS / 1000.0))
        self._tail = b""
        self._lvl = 0.0
        # priors from the 2026-08-27 measurement (p10 / p90)
        self._lo = 0.054
        self._hi = 0.138
        self._q: "queue.Queue" = queue.Queue(maxsize=64)
        self._batch: list = []
        # THE SAMPLES, NOT JUST THE LOUDNESS. `rms` is an envelope and drives
        # the strip fine, but a neural mouth needs a mel spectrogram, which
        # needs the audio itself. Same batch, same pts0, one extra key — so the
        # envelope and the samples can never disagree about when a word was.
        # Sent at native sr; the Conductor has numpy and does the 16 kHz
        # resample Wav2Lip wants. Never blocks paplay: on any trouble the key
        # is simply absent and the strip carries on.
        self._pcm: bytearray = bytearray()
        # The clock. `_spans` is None until an alignment lands; every read is
        # a plain attribute load of an immutable list, so no lock is needed
        # between the aligner thread and the paplay write path.
        self._spans = None
        self._align_on = False
        self._n_heard = None  # hops in HEARD time, once the voice has the whole clip
        self._rms_all: list[float] = []
        self._rest_clock: dict | None = None      # the unstamped words' own clock after the last stamp (round 13)
        self._voiced = False  # first hop above VOICE_RMS this take
        self._seq_i = 0       # viseme index among voiced hops only
        # Fable mouth_timing.schedule: whole voice PCM, then one class per hop
        # pinned to voiced energy. None until arm_clock has the utterance.
        self._clock_pcm: bytes | None = None
        self._clock_tempo = 1.0
        self._word_spans = None   # [(a, b), ...] heard seconds, or None
        self._char_spans = None   # per word [(c, a, b)] letter spans, or None (consonant onsets)
        self._timed = None        # cached list[int] from hops_scheduled
        self._clock_text = None   # the text `_clock_pcm` speaks when it is a sentence prefix (Turbo pieces)
        self._clock_partial = False   # True while later sentences are not stamped yet: their hops take the letter clock
        self._post_warned = False
        self._lead_sent = False
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._drain, daemon=True)
        self._t.start()

    # One POST of samples. Under the wall's 256 KB body cap after base64
    # (48000 raw -> 64000 encoded) and one second of 24 kHz audio.
    PCM_POST_BYTES = 48000

    # attack fast, release slow — a mouth closes slower than it opens, and a
    # raw RMS on a 20ms hop flickers like a VU meter.
    _ATT = 0.030
    _REL = 0.120

    def feed(self, pcm: bytes) -> None:
        """Called on the paplay write path. Must never block or raise."""
        try:
            if not self._armed:
                self._armed = True
                # air_at was predicted as "pad write + pad + sink". That is
                # right only when the voice bytes beat the pad out of the
                # buffer. MEASURED 2026-08-27: on a long line ffmpeg's atempo
                # muxer holds its first output ~2s, paplay underruns after the
                # pad, and the first word lands that much later. These bytes
                # are in the buffer NOW, so the later of the two is the truth,
                # and this self-corrects without guessing ffmpeg's buffering.
                # "NOW" holds only for bytes the player has been handed: every
                # caller writes, then feeds, and since 2026-10-13 the write is
                # in the player's pipe when it returns (_pipe_player). Before,
                # a write under Python's pipe buffer (4096 bytes on 3.12, 131072
                # on 3.14) sat in this process, and a short quiet prefix the
                # stamp hold wrote first was stamped here before the player had it.
                # Kill HOUSE_SPEAK_PIPE_FLUSH=0 puts that back.
                self.air_at = max(self.air_at,
                                  time.time() + MOUTH_LATENCY_MS / 1000.0)
                self._post_lead_prefix()
            elif self._gaps_on and self.samples:
                self._mind_gap()
            buf = self._tail + pcm
            step = self.hop * 2
            n = len(buf) // step
            for i in range(n):
                blk = buf[i * step:(i + 1) * step]
                acc = 0
                for j in range(0, len(blk), 2):
                    v = blk[j] | (blk[j + 1] << 8)
                    if v >= 32768:
                        v -= 65536
                    acc += v * v
                rms = math.sqrt(acc / max(1, len(blk) // 2)) / 32768.0
                dt = MOUTH_HOP_MS / 1000.0
                k = dt / ((self._ATT if rms > self._lvl else self._REL) + dt)
                self._lvl += (rms - self._lvl) * k
                # Auto-gain. MEASURED 2026-08-27 on a real voice turn: speech
                # spans only ~0.033..0.183 RMS, so an ABSOLUTE floor put 20% of
                # frames under it and the playhead stuttered 1.0 -> 0.26 -> 0.97
                # across ordinary words. Publish 0..1 between this turn's own
                # quiet and loud instead, so the wall needs no gain constant and
                # a louder or quieter voice reads the same.
                if self._lvl < self._lo:
                    self._lo = self._lvl
                else:
                    self._lo += (self._lvl - self._lo) * 0.0005   # creep up
                if self._lvl > self._hi:
                    self._hi = self._lvl
                else:
                    self._hi += (self._lvl - self._hi) * 0.0008   # sag down
                span = self._hi - self._lo
                if span < 0.02:
                    span = 0.02
                norm = (self._lvl - self._lo) / span
                norm = 0.0 if norm < 0.0 else (1.0 if norm > 1.0 else norm)
                self._batch.append(round(norm, 4))
                self._rms_all.append(round(norm, 4))
                self.samples += self.hop
            # Exactly the samples the RMS loop consumed, so pcm and rms cover
            # the same span and pts0 addresses both.
            self._pcm += buf[:n * step]
            self._tail = buf[n * step:]
            if len(self._batch) >= 5:          # ~100ms, the chunk cadence
                self._flush()
        except Exception:
            pass

    def _flush(self, air_until=None) -> None:
        if not self._batch and air_until is None:
            return
        rms = self._batch
        self._batch = []
        pts0 = self.air_at + self.gap_s + (self.samples - len(rms) * self.hop) / float(self.sr) - sheet_lead_s()
        try:
            payload = {
                "air_at": self.air_at, "air_until": air_until,
                "sr": self.sr, "hop_ms": MOUTH_HOP_MS,
                "pts0": round(pts0, 4), "rms": rms,
                "speaker": _AIR_WHO,    # who is on the air (the painter gates on it, 2026-09-18)
            }
            if not _HOUSE and (air_until is not None or _CLOCK_HOW):
                # the player's log names this line's lip clock (round 11); on every post once a clock has landed, so a
                # player that closes the line before the last post still has it (round 12, .101's answers logged none)
                payload["clock"] = line_clock_note()
            vis = self._viseme_batch(len(rms), rms=rms)
            if vis:
                payload["visemes"] = vis
            # The samples for this same batch. Fail open — a mouth that loses
            # its audio falls back to the strip, it does not go silent.
            #
            # SPLIT, DO NOT DROP. The streaming path hands over ~100 ms at a
            # time, but the BUFFERED path (slow first hop, or
            # HOUSE_SPEAK_BUFFER=1) feeds the whole clip in one call — 4.2 s
            # measured, 201,600 bytes, which is over the wall's 256 KB POST
            # limit once base64'd. Dropping it there meant the neural mouth got
            # audio on fast turns and nothing on slow ones, which is exactly
            # backwards. Slice it and stamp each slice with its own pts.
            pcm_chunks: list = []
            try:
                pcm, self._pcm = bytes(self._pcm), bytearray()
                step = self.PCM_POST_BYTES - (self.PCM_POST_BYTES % 2)
                for off in range(0, len(pcm), step):
                    blk = pcm[off:off + step]
                    if not blk:
                        continue
                    pcm_chunks.append((
                        round(float(pts0) + (off / 2.0) / float(self.sr), 4),
                        base64.b64encode(blk).decode("ascii"),
                    ))
            except Exception as e:  # noqa: BLE001
                # Fail open, but LOUDLY. A silent except here swallowed a
                # NameError on this very block (2026-08-30) and the neural
                # mouth looked like it had no audio for an hour.
                print("mouth pcm slice failed: %s: %s" % (type(e).__name__, e),
                      file=sys.stderr, flush=True)
                self._pcm = bytearray()
                pcm_chunks = []
            if pcm_chunks:
                payload["pts0"], payload["pcm16"] = payload["pts0"], pcm_chunks[0][1]
            for _piece in split_payload(payload):
                self._q.put_nowait(_piece)
            # Remaining slices ride as audio-only payloads: same air_at, their
            # own pts, no rms (the envelope already went with the first).
            for pts, blob in pcm_chunks[1:]:
                self._q.put_nowait({
                    "air_at": self.air_at, "air_until": None,
                    "sr": self.sr, "hop_ms": MOUTH_HOP_MS,
                    "pts0": pts, "pcm16": blob, "speaker": _AIR_WHO,
                })
        except queue.Full:
            pass                                # drop the batch, never the audio

    def sheet_end(self) -> float:
        """When the last written sample is heard: air_at + the samples + the pauses."""
        return self.air_at + self.gap_s + self.samples / float(self.sr)

    def _gap_at(self, hop_i: int) -> float:
        g = 0.0
        for i, gs in self._gaps:
            if hop_i >= i:
                g = gs
        return g

    def _mind_gap(self) -> None:
        """The player ran dry: these bytes play at now + latency, later than their stamp. Post what is pending on the
        old clock, one rest row where the voice stopped, and move the rest of the line by the pause."""
        due = self.sheet_end()
        heard = time.time() + MOUTH_LATENCY_MS / 1000.0
        gap = heard - due
        if gap < SHEET_GAP_MIN_S:
            return
        self._flush()
        try:
            from mouth_viseme import REST, class_index
            self._q.put_nowait({"air_at": self.air_at, "air_until": None, "sr": self.sr, "hop_ms": MOUTH_HOP_MS,
                                "pts0": round(due - sheet_lead_s(), 4), "rms": [0.0],
                                "visemes": [class_index(REST)], "speaker": _AIR_WHO})
        except Exception:  # noqa: BLE001 - a missed rest row leaves the last row up for the pause, as before
            pass
        self.gap_s += gap
        self._gaps.append((self.samples // self.hop, self.gap_s))
        _LINE["sheet_gaps"] = int(_LINE.get("sheet_gaps") or 0) + 1
        _LINE["sheet_gap_s"] = round(self.gap_s, 3)

    def _post_lead_prefix(self) -> None:
        """Hops covering [air_at − lead, air_at) so the jaw forms before sound."""
        if self._lead_sent:
            return
        self._lead_sent = True
        try:
            lib = Path(__file__).resolve().parent.parent / "lib"
            if str(lib) not in sys.path:
                sys.path.insert(0, str(lib))
            from mouth_playlist import viseme_lead_hops
            n = viseme_lead_hops(MOUTH_HOP_MS)
            if n <= 0:
                return
            vis = self._viseme_batch(n, hop0=0, shift=False, rms=[0.0] * n)
            hop = MOUTH_HOP_MS / 1000.0
            payload = {
                "air_at": self.air_at, "air_until": None,
                "sr": self.sr, "hop_ms": MOUTH_HOP_MS,
                "pts0": round(self.air_at - n * hop - sheet_lead_s(), 4),
                "rms": [0.0] * n, "speaker": _AIR_WHO,
            }
            if vis:
                payload["visemes"] = vis
            for _piece in split_payload(payload):
                self._q.put_nowait(_piece)
        except Exception:
            return

    def arm_clock(self, pcm: bytes, text: str, wait_s: float = 0.0,
                  tempo: float = 1.0) -> None:
        """Ask rail-align for real word boundaries, off the audio path.

        THE WHOLE UTTERANCE, OR NOTHING. Forced alignment forces EVERY token
        into the audio it is handed, so aligning a half-arrived stream against
        the full text does not give half an answer — it squeezes all 34 words
        into the first two seconds and is worse than the guess it replaces.
        The buffered path calls this before the first sample. The streaming
        path (HOUSE_SPEAK_BUFFER=0, the wall) calls it when the voice finishes
        generating — `buf` is then the whole utterance while the pump is still
        feeding the rest of the line, so the two clocks compose: letter-count
        covers the opening, aligned hops take the middle where the guess
        collapses.

        NEVER raises. A missing, cold or slow aligner leaves `_spans` as None
        and the letter clock carries on — the same fail-open contract as every
        other mouth extra here. Armed once per turn.

        Even without the aligner this is when we first know the WHOLE clip, so
        the letter clock is restamped onto heard duration. `expected_hops` is
        letters×0.08 s and ignores HOUSE_SPEAK_TEMPO (1.2). Hops are post-
        atempo. That mismatch stretched the viseme sheet longer than the
        audio: mouth started early, fell behind the words, finished late.

        `wait_s` > 0 joins the aligner for that long. The buffered path needs
        it: it publishes the whole envelope in one go before playback, so an
        answer that lands afterwards has nothing left to correct. That wait is
        added latency before she speaks — measured ~180 ms for a 3 s line.

        `tempo` divides every returned boundary. The streaming path aligns the
        raw voice bytes, which have not been through ffmpeg atempo yet, while
        MouthAir's hops count the audio the room actually hears. atempo is a
        pure linear time scale, so a word at pre-atempo t lands at t / tempo
        and nothing else about the alignment changes.
        """
        if not text or not pcm:
            return
        tempo = float(tempo or TEMPO or 1.0)
        self._clock_pcm = bytes(pcm)
        self._clock_tempo = tempo
        self._timed = None
        heard_s = (len(pcm) / 2.0 / float(self.sr or 24000)) / max(0.01, tempo)
        self._n_heard = max(8, int(round(heard_s * 1000.0 / MOUTH_HOP_MS)))
        self._restamp()
        if _GIVEN_WORDS:
            # the film's timeline, already in heard seconds: no aligner, no wait
            global _CLOCK_HOW
            lib = Path(__file__).resolve().parent.parent / "lib"
            if str(lib) not in sys.path:
                sys.path.insert(0, str(lib))
            from mouth_clock import spans_from_words, word_spans_for_timing, char_spans_for_timing
            ws = [dict(w) for w in _GIVEN_WORDS]
            spans = spans_from_words(ws, text=text)
            if spans:
                self._spans = spans
                self._word_spans = word_spans_for_timing(ws, text=text)
                self._char_spans = char_spans_for_timing(ws, text=text) if char_spans_on() else None
                self._timed = None
                self._restamp()
                _CLOCK_HOW = "film words (%d, timeline)" % len(ws)
                print("mouth clock: film words — %d from the timeline, no aligner" % len(ws),
                      file=sys.stderr, flush=True)
                return
        if not mouth_align_on() or self._align_on:
            return
        self._align_on = True

        def _go():
            try:
                body = json.dumps({
                    "text": text, "sr": self.sr,
                    "pcm16_b64": base64.b64encode(bytes(pcm)).decode("ascii"),
                }).encode()
                req = urllib.request.Request(
                    MOUTH_ALIGN_URL, data=body,
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=8.0) as r:
                    out = json.loads(r.read())
                if not out.get("ok"):
                    raise RuntimeError(out.get("error") or "align refused")
                lib = Path(__file__).resolve().parent.parent / "lib"
                if str(lib) not in sys.path:
                    sys.path.insert(0, str(lib))
                from mouth_clock import spans_from_words, word_spans_for_timing, char_spans_for_timing
                ws = out["words"]
                if tempo and abs(tempo - 1.0) >= 0.01:
                    ws = [dict(w, a=w["a"] / tempo, b=w["b"] / tempo,
                               chars=[dict(c, a=c["a"] / tempo, b=c["b"] / tempo) for c in (w.get("chars") or [])] or None)
                          for w in ws]
                spans = spans_from_words(ws, text=text)
                if spans:
                    # One assignment, after the list is complete — a reader on
                    # the write path can only ever see None or a finished list.
                    self._spans = spans
                    self._word_spans = word_spans_for_timing(ws, text=text)
                    self._char_spans = char_spans_for_timing(ws, text=text) if char_spans_on() else None
                    self._timed = None
                    self._restamp()
                    global _CLOCK_HOW
                    _CLOCK_HOW = "aligned (%d words, %.0f ms)" % (len(out["words"]), out.get("ms", 0))
                    if not _HOUSE:
                        # how far into the line the stamp landed (NN: "aligned" alone named only the tail)
                        self._aligned_t = time.time()       # the buffered path re-reads it once it knows air_at
                        _LINE["aligned_after_s"] = round(self._aligned_t - float(self.air_at), 2)
                    print("mouth clock: aligned %d words in %.0f ms (%.0fx rt)"
                          % (len(out["words"]), out.get("ms", 0), out.get("rt", 0)),
                          file=sys.stderr, flush=True)
            except Exception as e:  # noqa: BLE001
                # Loud once. A silently absent clock looks like a model problem.
                print("mouth clock: align failed, letter clock stands (%s: %s)"
                      % (type(e).__name__, e), file=sys.stderr, flush=True)

        t = threading.Thread(target=_go, daemon=True)
        t.start()
        if wait_s > 0:
            # Scale with the line. The aligner runs at ~18x realtime, so a
            # flat budget that fits a 3 s line silently drops a 10 s one: this
            # exact case cost 571 ms against a 600 ms budget in testing. An
            # eighth of realtime is comfortable headroom over the measured
            # eighteenth, and it still cannot hold her voice for long.
            secs = len(pcm) / 2.0 / float(self.sr or 24000)
            t.join(timeout=wait_s + secs / 8.0)

    def _letter_nexp(self) -> int:
        """Letter-clock hop count for VOICED time, not the voice's pad.

        `expected_hops` is letters×0.08 s on the TEXT. Paplay hops are
        post-atempo (HOUSE_SPEAK_TEMPO, default 1.2). `_n_heard` is the
        whole clip including pad — do not use it as the viseme budget or
        silence steals 'Okay' and 'variables' never lands.
        """
        lib = Path(__file__).resolve().parent.parent / "lib"
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        from mouth_viseme import expected_hops

        n = expected_hops(_AIR_TEXT, MOUTH_HOP_MS)
        tempo = float(TEMPO or 1.0)
        if tempo >= 1.01:
            n = max(8, int(round(n / tempo)))
        return n

    def _make_rest_clock(self, pcm: bytes, text: str, tempo: float) -> dict | None:
        """THE REST OF THE LINE RESTARTS AT THE LAST STAMP (round 13, .101 under load: "lips falling behind on
        reading", a piece playing 2.2 s before its stamp landed). Until a piece is stamped its hops took the WHOLE
        line's letter clock, counted from the line's first hop: it knew nothing of Pocket's lead-in quiet or its pace, so
        by the second piece it pointed at the wrong words. Now the unstamped words (the line's words after the stamped
        ones) start at the stamped prefix's last hop, advance only on voiced hops (a quiet hop is an open rest), and are
        paced by the prefix's own voiced hops per letter. Never on the house (its pieces are Turbo's, off by default);
        HOUSE_MOUTH_REST_CLOCK=0 keeps the whole-line letter clock. None = no better guess."""
        try:
            if _HOUSE or os.environ.get("HOUSE_MOUTH_REST_CLOCK", "1").strip().lower() in ("0", "false", "no", "off"):
                return None
            from mouth_viseme import REST, REST_OPEN, VOICE_RMS, expected_hops, text_to_visemes, word_tokens
            sr = float(self.sr or 24000)
            start = int(round(len(pcm) / 2.0 / sr / max(0.01, float(tempo or 1.0)) * 1000.0 / MOUTH_HOP_MS))
            rest_words = word_tokens(_AIR_TEXT)[len(word_tokens(text)):]
            vis = [v for v in text_to_visemes(" ".join(rest_words)) if v not in (REST, REST_OPEN)]
            if not rest_words or not vis:
                return None
            voiced = sum(1 for x in self._rms_all[:start] if float(x) >= VOICE_RMS)
            exp = expected_hops(text, MOUTH_HOP_MS)
            pace = max(0.5, min(2.0, voiced / float(exp))) if voiced and exp else 1.0
            return {"from": start, "vis": vis, "nexp": max(1, int(round(expected_hops(" ".join(rest_words), MOUTH_HOP_MS)
                                                                         * pace))), "floor": float(VOICE_RMS)}
        except Exception as e:  # noqa: BLE001 - the letter clock as before
            print("mouth clock: no rest-of-line clock (%s: %s)" % (type(e).__name__, e), file=sys.stderr, flush=True)
            return None

    def _rest_class(self, rc: dict, i: int):
        """The rest-of-line clock's class name for hop i, or None to fall back (a hop not heard yet)."""
        if i >= len(self._rms_all):
            return None
        lv = self._rms_all
        if float(lv[i]) < rc["floor"]:
            return "rest-open"
        cum = rc.setdefault("cum", [0])           # voiced hops from rc["from"], counted once
        upto = rc.setdefault("upto", rc["from"])
        while upto <= i:
            cum.append(cum[-1] + (1 if float(lv[upto]) >= rc["floor"] else 0))
            upto += 1
        rc["upto"] = upto
        v = cum[i - rc["from"] + 1] - 1 if i >= rc["from"] else 0
        return rc["vis"][min(len(rc["vis"]) - 1, int(max(0, v) * len(rc["vis"]) / rc["nexp"]))]

    def set_piece_clock(self, pcm: bytes, text: str, words: list, partial: bool,
                        tempo: float = 1.0, note: str = "") -> bool:
        """The stamped sentences so far (Turbo pieces): `pcm` and `text` are the line's prefix, `words` its
        alignment in raw seconds from the line's first sample. Later sentences keep the letter clock while
        `partial`. Never raises."""
        try:
            lib = Path(__file__).resolve().parent.parent / "lib"
            if str(lib) not in sys.path:
                sys.path.insert(0, str(lib))
            from mouth_clock import spans_from_words, word_spans_for_timing, char_spans_for_timing
            tempo = float(tempo or 1.0)
            ws = words
            if abs(tempo - 1.0) >= 0.01:
                ws = [dict(w, a=w["a"] / tempo, b=w["b"] / tempo,
                           chars=[dict(c, a=c["a"] / tempo, b=c["b"] / tempo) for c in (w.get("chars") or [])] or None)
                      for w in ws]
            spans = spans_from_words(ws, text=text)
            if not spans:
                return False
            self._clock_pcm = bytes(pcm)
            self._clock_tempo = tempo
            self._clock_text = text
            self._clock_partial = bool(partial)
            self._spans = spans
            self._word_spans = word_spans_for_timing(ws, text=text)
            self._char_spans = char_spans_for_timing(ws, text=text) if char_spans_on() else None
            self._timed = None
            self._rest_clock = self._make_rest_clock(pcm, text, tempo) if partial else None
            self._restamp()
            global _CLOCK_HOW
            _CLOCK_HOW = "aligned by sentence (%s)" % note if note else "aligned by sentence"
            if not _HOUSE:
                # how far into the line the first and the latest stamp landed (round 11: Pocket's pieces)
                _t = round(time.time() - float(self.air_at), 2)
                _LINE.setdefault("first_stamp_after_s", _t)
                _LINE["aligned_after_s"] = _t
            return True
        except Exception as e:  # noqa: BLE001
            print("mouth clock: sentence stamp failed (%s: %s)" % (type(e).__name__, e), file=sys.stderr, flush=True)
            return False

    def _restamp(self) -> None:
        """Rewrite visemes already on the wire onto the real clock.

        mouth_ingest appends; viseme_at reads reversed, so a same-pts
        restamp wins without clearing the turn. Rebuild voiced-index from
        the full rms so a mid-line align does not double-count.
        """
        n = len(self._rms_all)
        if n < 1:
            return
        self._voiced = False
        self._seq_i = 0
        self._timed = None
        vis = self._viseme_batch(n, hop0=0, shift=True, rms=self._rms_all)
        if not vis:
            return
        if self._gaps:
            # one payload per stretch between pauses, each on its own clock (sheet_gaps_on)
            cuts = [0] + [i for i, _g in self._gaps if 0 < i < n] + [n]
            try:
                for a, b in zip(cuts, cuts[1:]):
                    if b <= a:
                        continue
                    seg = {"air_at": self.air_at, "air_until": None, "sr": self.sr, "hop_ms": MOUTH_HOP_MS,
                           "pts0": round(self.air_at + self._gap_at(a) + a * self.hop / float(self.sr)
                                         - sheet_lead_s(), 4),
                           "rms": list(self._rms_all[a:b]), "visemes": vis[a:b]}
                    for _piece in split_payload(seg):
                        self._q.put_nowait(_piece)
            except Exception:
                return
            return
        payload = {
            "air_at": self.air_at, "air_until": None,
            "sr": self.sr, "hop_ms": MOUTH_HOP_MS,
            "pts0": round(self.air_at - sheet_lead_s(), 4),
            "rms": list(self._rms_all),
            "visemes": vis,
        }
        try:
            for _piece in split_payload(payload):
                self._q.put_nowait(_piece)
        except Exception:
            return

    def _timed_hops(self) -> list[int] | None:
        """Full-utterance schedule pinned to voiced energy. None until PCM."""
        if self._timed is not None:
            return self._timed
        pcm = self._clock_pcm
        if not pcm:
            return None
        try:
            lib = Path(__file__).resolve().parent.parent / "lib"
            if str(lib) not in sys.path:
                sys.path.insert(0, str(lib))
            from mouth_clock import hops_scheduled
            from mouth_viseme import word_viseme_lists

            ctext = self._clock_text or _AIR_TEXT
            wvs = word_viseme_lists(ctext)
            if not wvs:
                return None
            # The voice bytes are pre-atempo; MouthAir hops are heard time.
            # hop_ms * tempo makes one schedule hop = 20 ms of playback.
            tempo = max(0.01, float(self._clock_tempo or 1.0))
            hop_ms = float(MOUTH_HOP_MS) * tempo
            import mouth_viseme
            if mouth_viseme.PHONES_ON and self._word_spans:
                # THE PHONE SHEET (round 40, HOUSE_MOUTH_PHONES=1): the whole-line espeak spelling, one shape per
                # phone inside the aligner's word spans, no holds (lib/mouth_clock.hops_phoned); else today's sheet
                from mouth_clock import hops_phoned
                phoned = hops_phoned(wvs, pcm, self.sr, hop_ms=hop_ms, word_spans=self._word_spans, text=ctext,
                                     span_hop_ms=float(MOUTH_HOP_MS))
                if phoned:
                    if not getattr(self, "_phones_said", False):
                        self._phones_said = True
                        print("mouth sheet: %s, phone sheet" % mouth_viseme.phones_status(), file=sys.stderr, flush=True)
                    self._timed = phoned
                    return self._timed
            self._timed = hops_scheduled(
                wvs, pcm, self.sr, hop_ms=hop_ms,
                word_spans=self._word_spans, char_spans=self._char_spans, text=ctext,
                span_hop_ms=float(MOUTH_HOP_MS))    # the spans are heard seconds; one hop of those
            return self._timed
        except Exception:
            return None

    def _viseme_batch(self, n: int, hop0: int | None = None,
                      shift: bool = True, rms: list | None = None
                      ) -> list[int] | None:
        """Class ids for this RMS batch. Fail open — never block paplay.

        The voice text is armed as `_AIR_TEXT` before the first PCM byte, so the
        13-class sequence exists before the envelope. The conductor indexes
        the strip by these ids (lib/mouth_viseme.CLASSES), not by loudness.

        Ids, not names: voiceonly-home-server mouth_ingest is chattr +i and
        only stores int(vis[i]). Names would be dropped.

        Once arm_clock has the whole voice PCM, mouth_timing.schedule pins
        the sheet to voiced energy (20 ms pre-open, min-hold, vowel weight).
        HOUSE_MOUTH_TIMING_LEAD_MS (default 120) copies the first class onto
        pad REST so the jaw beats the voice without skipping Okay's OH.
        Do not stack HOUSE_VISEME_LEAD_MS (100-200) on top. Streaming
        before the voice finishes still uses the quiet-gated letter clock, then
        restamps. Aligned word spans are re-anchored into the same voiced
        region when :8195 is warm.
        """
        if n <= 0:
            return None
        try:
            lib = Path(__file__).resolve().parent.parent / "lib"
            if str(lib) not in sys.path:
                sys.path.insert(0, str(lib))
            from mouth_playlist import viseme_lead_hops
            from mouth_viseme import (
                REST,
                REST_OPEN,
                VOICE_RMS,
                class_index,
                text_to_visemes,
                viseme_for_hop,
            )

            i0 = hop0 if hop0 is not None else max(0, (self.samples // self.hop) - n)
            timed = self._timed_hops()
            if timed:
                rest = class_index(REST)
                lead_h = 0
                if shift and MOUTH_TIMING_LEAD_MS > 0:
                    lead_h = max(0, int(round(MOUTH_TIMING_LEAD_MS / MOUTH_HOP_MS)))
                # Lead is a PRE-OPEN of the first class into the pad, not a
                # playhead skip. Skipping timed[i+lead] ate Okay's OH (6 hops
                # of OH + 6 hops of look-ahead = 0 OH on the heard vowel).
                # Wallpaper at ~22 fps then had nothing to sample. Brave's
                # mjpg still caught the early frames; Wall Background did not.
                first = next((j for j, c in enumerate(timed) if c != rest), None)
                letter = None
                chunk = []
                for k in range(n):
                    i = i0 + k
                    if (lead_h and first is not None
                            and (first - lead_h) <= i < first):
                        chunk.append(timed[first])
                    elif 0 <= i < len(timed):
                        chunk.append(timed[i])
                    elif self._clock_partial:
                        # a sentence not stamped yet (Turbo pieces): the letter clock until its stamp lands
                        rc = getattr(self, "_rest_clock", None)
                        if rc is not None and i >= rc["from"]:
                            got = self._rest_class(rc, i)
                            if got is not None:
                                chunk.append(class_index(got))
                                continue
                        if letter is None:
                            letter = (text_to_visemes(_AIR_TEXT), self._letter_nexp(),
                                      viseme_lead_hops(MOUTH_HOP_MS) if shift else 0)
                        chunk.append(class_index(viseme_for_hop(letter[0], i + letter[2], letter[1])))
                    else:
                        chunk.append(rest)
                return chunk
            spans = self._spans          # one load; may flip under us, harmlessly
            if spans:
                # Real boundaries. Hop i is at i * 20 ms from the first sample,
                # which is the same origin rail-align measured against.
                # Spans already REST before word 1 — the voice's pad does not walk.
                from mouth_clock import hops_from_spans
                return hops_from_spans(
                    spans, n, MOUTH_HOP_MS / 1000.0, t0=i0 * MOUTH_HOP_MS / 1000.0,
                    lead_s=(MOUTH_ALIGN_LEAD_MS / 1000.0) if shift else 0.0)
            vs = text_to_visemes(_AIR_TEXT)
            nexp = self._letter_nexp()
            lead = viseme_lead_hops(MOUTH_HOP_MS) if shift else 0
            if rms is None:
                return [
                    class_index(viseme_for_hop(vs, i0 + i + lead, nexp))
                    for i in range(n)
                ]
            # New take, the voice's pad, between-takes: quiet hops stay closed rest
            # and do not consume the viseme sheet. Measured 2026-09-07: 1.28 s
            # of rms≈0 still walked AH/KG/EE — every line started early.
            # Streaming only — the full clip restamps through schedule().
            out: list[int] = []
            for x in rms[:n]:
                lvl = float(x)
                if not self._voiced:
                    if lvl < VOICE_RMS:
                        out.append(class_index(REST))
                        continue
                    self._voiced = True
                    self._seq_i = 0
                if lvl < VOICE_RMS:
                    out.append(class_index(REST_OPEN))
                    continue
                out.append(class_index(
                    viseme_for_hop(vs, self._seq_i + lead, nexp)))
                self._seq_i += 1
            return out
        except Exception:
            return None

    def close(self, air_until: float) -> None:
        self._flush(air_until=air_until)
        self._stop.set()
        # Short join. Measured 2026-08-27 against a black-hole :8099 (accepts,
        # never answers): a 1.0s join added 0.85s to every turn. That is after
        # paplay has drained so it cannot chop her voice — the audio came back
        # byte-identical — but it holds the speech lock and delays the ear
        # reopening. Losing the final envelope batch costs nothing: air_until
        # also reaches the glass through the gate.
        self._t.join(timeout=30.0 if RENDER_ONLY else 0.25)   # render only: the sheet IS the product

    def _drain(self) -> None:
        """Sidecar. A stuck :8099 must never reach the paplay write path."""
        while True:
            try:
                item = self._q.get(timeout=0.2)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            # Correct the GATE too, once, off the audio path. Otherwise the
            # gate keeps advertising the pre-correction guess for the whole
            # turn — the wall ignores it, but a stale published field is a
            # trap for whoever reads it next.
            #
            # Never the last word (t_5db8051f): the gate is plain overwrites,
            # and this write races the line's own close (speaking=false,
            # air_until set) — a gate write slower than the 0.25 s join in
            # close() lands AFTER the close and leaves the gate
            # speaking=true (deaf_until refreshed, and nothing else re-closes
            # it until the next turn), which reads as on the air forever. So:
            # if close has landed by the time this write finishes — either
            # already set (Window A) or during the write (Window B) — the
            # write itself is stale, so re-close with the barge-in convention
            # (speaking=false, air_until=now) and the stale true is never the
            # gate's last word. The open write still happens either way: the
            # suite's gate-isolation test pins that the sidecar opens the
            # gate, and the line's own close follows immediately in practice.
            if not self._gate_fixed:
                self._gate_fixed = True
                try:
                    _mouth_air(True, air_at=self.air_at, air_firm=True)
                except Exception:
                    pass
                finally:
                    if self._stop.is_set():
                        try:
                            # Re-close THIS turn's gate only: a newer turn may
                            # already have opened one (a different air_at) —
                            # its gate is not ours to close. Residual: an open
                            # landing BETWEEN this read and the re-close is
                            # clobbered — bounded and self-healing (that turn
                            # re-opens and re-closes; our last word is always
                            # False, so the gate cannot stick open).
                            # Unreadable or windowless gate: still close — a
                            # stale speaking=true is the bigger trap.
                            _mine = True
                            try:
                                lib = Path(__file__).resolve().parent.parent / "lib"
                                if str(lib) not in sys.path:
                                    sys.path.insert(0, str(lib))
                                from audio_sink import TTS_GATE as _g
                                _d = json.loads(_g.read_text(encoding="utf-8"))
                                _aat = _d.get("air_at")
                                if _aat is not None and float(_aat) != self.air_at:
                                    _mine = False
                            except Exception:
                                pass
                            if _mine:
                                _mouth_air(False, air_until=time.time(), air_firm=True)
                        except Exception:
                            pass
            # pace: a piece of the future waits until it is near (the wall keeps
            # 600 rows; a whole line at once lost its head or its tail)
            try:
                p0 = float(item.get("pts0"))
                while not RENDER_ONLY and p0 - time.time() > POST_AHEAD_S and not self._stop.is_set():
                    time.sleep(0.1)
            except (TypeError, ValueError):
                pass
            try:
                req = urllib.request.Request(
                    MOUTH_URL, data=json.dumps(item).encode(),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                urllib.request.urlopen(req, timeout=0.5).close()
                _LINE["posts_ok"] = int(_LINE.get("posts_ok") or 0) + 1
            except Exception as e:  # noqa: BLE001
                _LINE["posts_failed"] = int(_LINE.get("posts_failed") or 0) + 1
                if _LINE.get("post_error") is None:
                    _LINE["post_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
                # Loud, like the slice path. A silent 413 or timeout here is
                # how the neural mouth loses its audio and looks like a model
                # problem — the sidecar may never block paplay, but it must
                # never fail invisibly either. Once per turn is enough.
                if not self._post_warned:
                    self._post_warned = True
                    print("mouth POST failed: %s: %s" % (type(e).__name__, e),
                          file=sys.stderr, flush=True)


_AIR_OPEN: float | None = None


def _air_arm(air_at: float) -> None:
    global _AIR_OPEN
    _AIR_OPEN = air_at


def _air_disarm() -> None:
    global _AIR_OPEN
    _AIR_OPEN = None


# HER MOUTH STOPS WITH HER VOICE (round 20, the User 2026-10-14, .101 round 19: "her lips moved on after the cut, to
# finish a chunk"; "Not worried about that but if easy would be nice"). A cut SIGTERMs this process group and her voice
# stops, but the sheet's rows already posted ahead (up to POST_AHEAD_S, a stamped piece) stayed in the mailbox, and fresh
# rows are "on the air" to the painter, so her mouth played them out over silence. On a signal a pack posts one last row,
# air_until = now with "cut": true; the mailbox (lib/mouth_mailbox) drops every row of this line after that instant,
# including any post still in flight, and her mouth closes on rest with her voice. The house never sends it (its
# :8099 shares the mailbox code). Kill: HOUSE_SPEAK_CUT_SHEET=0.
_LIVE_AIR: dict = {"air": None}


def cut_sheet_on() -> bool:
    return (not _HOUSE) and os.environ.get("HOUSE_SPEAK_CUT_SHEET", "1").strip().lower() not in (
        "0", "false", "no", "off")


def _mouth_cut() -> None:
    """Post the cut (see above). Never raises; at most 0.3 s."""
    air = _LIVE_AIR.get("air")
    if air is None or not cut_sheet_on() or not MOUTH_URL:
        return
    try:
        item = {"air_at": air.air_at, "air_until": round(time.time(), 4), "cut": True, "speaker": _AIR_WHO}
        req = urllib.request.Request(MOUTH_URL, data=json.dumps(item).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=0.3).close()
        _LINE["sheet_cut"] = item["air_until"]
    except Exception:  # noqa: BLE001 - a cut that cannot be posted leaves the rows to run out, as before
        pass


def _air_sigterm(signum, frame):
    """rail-voice abort_mouth() does killpg on a name-interrupt, and Python's
    default SIGTERM handler exits WITHOUT running `finally` — so without this
    the gate keeps saying she is on the air after her voice is gone. The wall
    would leave her mouth moving over silence until the next turn."""
    try:
        _page_line_stop()
        # An Audio8 stream on the air is cut at its server only with CRISSA_AUDIO8_CANCEL=1 (the User 2026-10-08):
        # measured, a cancel on engine 7b6a5a1 held its synthesis lock past two minutes, so the default lets the
        # server finish the line it was making while this process ends.
        if os.environ.get("CRISSA_AUDIO8_CANCEL", "0") in ("1", "true", "yes", "on"):
            try:
                import crissa_tts
                crissa_tts.cancel_audio8()
            except Exception:  # noqa: BLE001
                pass
        _mouth_air(False, air_at=_AIR_OPEN, air_until=time.time())
        _mouth_cut()                                 # round 20: her mouth stops with her voice
    finally:
        _line_end(143, "signal %d" % signum)
        os._exit(143)


def _mouth_air(on: bool, air_at: float | None = None,
               air_until: float | None = None, air_firm: bool = False) -> None:
    """UI + STT gate: true only while paplay is on the air, not during synthesis."""
    if RENDER_ONLY:
        return                          # nothing is on the air in this room
    try:
        lib = Path(__file__).resolve().parent.parent / "lib"
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        from audio_sink import mark_tts_gate

        mark_tts_gate(
            speaking=on,
            last_reply=(_AIR_TEXT or "")[:MOUTH_TEXT_CAP],
            extra_sec=0.4 if on else 0.0,
            who=_AIR_WHO,
            air_at=air_at if MOUTH_AIR else None,
            air_until=air_until if MOUTH_AIR else None,
            air_firm=bool(air_firm) if MOUTH_AIR else False,
            air_off=not MOUTH_AIR,
        )
    except Exception:
        pass
    if not on:
        _release_voice_cache()


VOICE_CACHE_URL = os.environ.get("HOUSE_VOICE_CACHE_URL",
                                 _house_value("voice_cache_release_url") if _HOUSE else "")


def _release_voice_cache() -> None:
    """Drop the ear's rolling PCM after this spoken turn. Best-effort. Outside the house there is no such ear
    (on a Mac :8190 is the voice pack's own speech-to-text, and every reply was posted to it - 2026-10-04)."""
    if not VOICE_CACHE_URL:
        return
    try:
        body = json.dumps(
            {"reply": (_AIR_TEXT or "")[:400], "who": _AIR_WHO or "crissa"}
        ).encode()
        req = urllib.request.Request(
            VOICE_CACHE_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1.5).read()
    except Exception:
        pass
# Pitch-preserving playback speed (User 2026-08-05: relaxed but FAST talking).
# The voice has no numeric pace control (instruct mode biases slower), so tempo is
# applied at playback via ffmpeg atempo. 1.0 = off. BAU sets a faster value
# via HOUSE_SPEAK_TEMPO on the ear-bridge unit.
# Default 1.2 BAU (the User 2026-08-05 / restore after 1.1 reject). Override via HOUSE_SPEAK_TEMPO.
def default_tempo(env: dict | None = None, health=None) -> float:
    """HOUSE_SPEAK_TEMPO when a spawner names it; else it follows the voice engine (the User 2026-10-06: every voice
    at 1.0x on Turbo): 1.0 when the voice server at VOICE_URL says it is Chatterbox Turbo, which already speaks at a
    natural pace, else the 1.2 law that was set for Cosy's slow one. Outside the house the conductor hands each
    line tempo 1.0 (the User 2026-10-08) and the fallback matches it. So a spawner that names no tempo changes on the
    swap and never before it. Kill: HOUSE_SPEAK_TEMPO_FOLLOWS_ENGINE=0 (always 1.2)."""
    e = os.environ if env is None else env
    v = str(e.get("HOUSE_SPEAK_TEMPO") or "").strip()
    if v:
        return float(v)
    if str(e.get("HOUSE_SPEAK_TEMPO_FOLLOWS_ENGINE", "1")).strip().lower() in ("0", "false", "no", "off"):
        return 1.2
    try:
        if health is None:
            health = voice_health(timeout=0.3)
        if str(health.get("engine") or "") == "chatterbox-turbo":
            return 1.0
    except Exception:  # noqa: BLE001  no answer: the old law
        pass
    return 1.2


TEMPO = default_tempo() if _HOUSE else float(os.environ.get("HOUSE_SPEAK_TEMPO", "1.0"))


def atempo_chain(tempo: float) -> str:
    """ffmpeg's atempo takes 0.5–100 per stage; chain stages for the
    Composer's ¼ / ⅛ playback (tempo × rate, 2026-09-13). Product == tempo."""
    tempo = float(tempo)
    stages = []
    while tempo < 0.5 - 1e-9:
        stages.append(0.5)
        tempo /= 0.5
    while tempo > 100.0 + 1e-9:
        stages.append(100.0)
        tempo /= 100.0
    stages.append(round(tempo, 6))
    return ",".join("atempo=%g" % s for s in stages)


# PLAY LOUDNESS (Composer 2026-09-17: "clip volume and Say textbox volume
# are so different … fix to louder version"). Measured on this box:
#   crissa (Say textbox)         I −18.5 LUFS, speech-median −17.2 dBFS
#   lila                          I −13.7 LUFS, speech-median −12.6 dBFS  (loud lila.wav)
#   harvest-1 follow-clip    I −11.0 LUFS, speech-median −13.9 dBFS
# Same paplay, same sink, same 100% boost. The gap is the PCM. Do not rewrite
# crissa.wav (locked take1). Boost only a QUIET buffer up to the harvest/lila
# band; never turn a loud clip down. Kill: HOUSE_SPEAK_MATCH_LOUD=0.
PLAY_TARGET_DB = float(os.environ.get("HOUSE_SPEAK_TARGET_DB", os.environ.get("HOUSE_LEVEL_TARGET_DB", "-26")))
# LEVEL BY ROLE (the User 2026-09-18 "level them … only quieted voice should be
# the board and announcer when Crissa or I am on the mic"): 759651c8 raised EVERY
# voice to the −13 band. Now Crissa/Lila keep the house band (raise only); the
# crew's board talk sits on the chatter band, cut allowed, and drops a further
# duck while the mic is live (lib/loudness). Kill HOUSE_SPEAK_CHATTER_DUCK=0.
PLAY_ALLOW_CUT = False
CHATTER_DUCK_ON = os.environ.get("HOUSE_SPEAK_CHATTER_DUCK", "1").strip().lower() not in ("0", "false", "off", "no")
HEAR_HEALTH_URL = os.environ.get("HOUSE_HEAR_HEALTH_URL", _house_value("hear_health_url") if _HOUSE else "")


def mic_live(timeout: float = 0.25) -> bool:
    """The room is on the mic: an ear session is open and not paused
    (:8194/health, the fields the glass polls). Fail closed = not live."""
    if not HEAR_HEALTH_URL:
        return False
    try:
        with urllib.request.urlopen(HEAR_HEALTH_URL, timeout=timeout) as r:
            h = json.load(r)
        return bool(h.get("session_open")) and not bool(h.get("paused"))
    except Exception:
        return False


def play_target_for(air_who: str) -> tuple[float, bool]:
    """(target_db, allow_cut) for this line's voice."""
    try:
        from loudness import target_for
    except ImportError:
        return PLAY_TARGET_DB, False
    live = CHATTER_DUCK_ON and mic_live()
    return target_for(air_who, mic_live=live)
PLAY_MATCH_LOUD = os.environ.get("HOUSE_SPEAK_MATCH_LOUD", "1").strip().lower() not in (
    "0", "false", "off", "no",
)
_PLAY_LIMIT = 0.89          # −1.1 dBFS after makeup
_PLAY_DEADBAND_DB = 1.0     # harvest −13.9 stays put
_PLAY_MAX_GAIN = 2.0        # +6 dB cap (was +12: a 7.5 dB lift crushed peaks to −1 dB, 2026-09-18)

# CRISSA'S GAIN FOLLOWS THE LINE (the User 2026-09-30, "Crissa's voice is louder or quieter between her first reply and
# her last sentence"). A streamed line took ONE gain from its first 0.5-2 s and kept it. Measured on two of her real
# replies: the voice holds her level to the end of a 17 s line (no fade, unlike the CPU announcer), but her openings run
# 2-4 dB hotter than the rest, so every line was cut too far (3-6 dB under -26 after the first second), and her
# first clause and the rest - two calls, two gains - stepped 1-2 dB apart. The rider starts where the old gain did
# (the first sound is no later), re-measures the median of every voiced hop heard so far, and moves the gain toward
# the target at most FOLLOW_RATE_DB_S. Crissa only. OFF by default: HOUSE_SPEAK_FOLLOW_LINE=1 turns it on (the User
# switches it after he has heard it).
FOLLOW_LINE = os.environ.get("HOUSE_SPEAK_FOLLOW_LINE", "0").strip().lower() in ("1", "true", "on", "yes")
FOLLOW_RATE_DB_S = float(os.environ.get("HOUSE_SPEAK_FOLLOW_RATE_DB_S", "1.5"))
FOLLOW_WHO = frozenset({"crissa"})


class GainRider:
    """A gain that follows the line: starts at the prefill's gain, walks toward the speech median of everything
    heard so far, never faster than rate_db_s, inside the same floor / cap the fixed gain obeys."""

    def __init__(self, gain0: float, sr: int, *, target_db: float, allow_cut: bool,
                 rate_db_s: float = FOLLOW_RATE_DB_S, prefill: bytes = b""):
        self.sr, self.target_db, self.rate = sr, target_db, rate_db_s
        self.lo_db = 20.0 * math.log10(0.1 if allow_cut else 1.0)
        self.hi_db = 20.0 * math.log10(_PLAY_MAX_GAIN)
        self.g_db = 20.0 * math.log10(max(gain0, 1e-6))
        self.voiced: list[float] = []
        self._hear(prefill)

    def _hear(self, pcm: bytes) -> None:
        n = len(pcm) // 2
        if not n:
            return
        a = array.array("h")
        a.frombytes(pcm[: n * 2])
        hop = max(1, int(self.sr * 0.02))
        for i in range(0, n - hop, hop):
            acc = 0
            for x in a[i: i + hop]:
                acc += x * x
            rms = math.sqrt(acc / hop) / 32768.0
            if rms >= 0.02:
                self.voiced.append(rms)

    def want_db(self) -> float | None:
        if len(self.voiced) < 8:
            return None
        v = sorted(self.voiced)
        med_db = 20.0 * math.log10(v[len(v) // 2])
        return min(max(self.target_db - med_db, self.lo_db), self.hi_db)

    def feed(self, chunk: bytes) -> float:
        """The linear gain for this chunk (the chunk is counted before it is levelled)."""
        self._hear(chunk)
        want = self.want_db()
        if want is not None:
            step = self.rate * (len(chunk) / 2) / float(self.sr)
            self.g_db += max(-step, min(step, want - self.g_db))
        return 10.0 ** (self.g_db / 20.0)


def follow_rider(prefill: bytes, sr: int, gain0: float) -> "GainRider | None":
    """The rider for this line, or None (switched off, or not Crissa's voice)."""
    try:
        from loudness import who_of
    except ImportError:
        def who_of(w):
            return str(w or "").split("@")[0].strip().lower()
    if not (FOLLOW_LINE and PLAY_MATCH_LOUD and who_of(_AIR_WHO) in FOLLOW_WHO):
        return None
    if _setup_run_id():
        return None                          # ONE LEVEL THROUGH SETUP: the kept gain, never a moving one
    return GainRider(gain0, sr, target_db=PLAY_TARGET_DB, allow_cut=PLAY_ALLOW_CUT, prefill=prefill)


def _speech_med_rms(pcm: bytes, sr: int = 24000, floor: float = 0.02) -> float | None:
    """Median hop RMS of voiced 20 ms hops. None when the buffer is pad."""
    n = len(pcm) // 2
    if n < int(sr * 0.04):
        return None
    a = array.array("h")
    a.frombytes(pcm[: n * 2])
    hop = max(1, int(sr * 0.02))
    voiced: list[float] = []
    for i in range(0, n - hop, hop):
        acc = 0
        for x in a[i : i + hop]:
            acc += x * x
        rms = math.sqrt(acc / hop) / 32768.0
        if rms >= floor:
            voiced.append(rms)
    if len(voiced) < 8:
        return None
    voiced.sort()
    return voiced[len(voiced) // 2]


# ONE LEVEL THROUGH SETUP (round 17, the User 2026-10-13, .101 round 16: "substantial volume bump in mid speech";
# house-speak.err had each of Lila's lines levelled on its own - play-loud gain 0.33, 0.26, 0.23, 0.28, 0.29, up to
# 3 dB line to line - each gain read off that line's own first second). In a pack, while Setup runs
# (<state home>/init-run.json status "running"), a voice keeps ONE gain: the first line it says in that run is measured
# as before, its gain is kept in <state home>/run/setup-level.json under the run's id and the voice (who, engine and
# target), and every later line, piece and chunk of that voice in the run plays at that gain, so nothing steps from line
# to line or at a piece seam. Another run, the house, or a file that cannot be read: each line is measured, as before.
# Kill: CRISSA_SETUP_ONE_LEVEL=0. Round 19 (lib/loudness ONE LOUDNESS THROUGH SETUP): the coach's lines keep their gain
# in the same file under the same keys, read off a whole line, and that replaces this one (read off a line's start).
SETUP_LEVEL_FILE = "setup-level.json"
_LEVEL: dict = {"run": None, "key": None, "gain": None}


def _setup_level_on() -> bool:
    return not _HOUSE and os.environ.get("CRISSA_SETUP_ONE_LEVEL", "1").strip().lower() not in (
        "0", "off", "false", "no")


def _setup_run_id() -> str:
    """The id of the Setup run going on now in this pack ("" when none): Init's own state file, read once a process."""
    if _LEVEL["run"] is None:
        _LEVEL["run"] = ""
        if _setup_level_on():
            try:
                st = json.loads((_crissa_home() / "init-run.json").read_text(encoding="utf-8"))
                if isinstance(st, dict) and st.get("status") == "running":
                    _LEVEL["run"] = str(st.get("run_id") or "")
            except (OSError, ValueError):
                pass
    return _LEVEL["run"]


def _setup_level_key() -> str:
    try:
        from loudness import who_of
    except ImportError:
        def who_of(w):
            return str(w or "").split("@")[0].strip().lower()
    return "%s|%s|%.1f" % (who_of(_AIR_WHO), _LINE.get("engine") or "", PLAY_TARGET_DB)


def _setup_level_path() -> Path:
    return _crissa_home() / "run" / SETUP_LEVEL_FILE


def setup_level_gain() -> float | None:
    """The gain this voice keeps through the Setup run going on now, or None (no run, not kept yet, switched off)."""
    run = _setup_run_id()
    if not run:
        return None
    key = _setup_level_key()
    if _LEVEL["key"] == key and _LEVEL["gain"] is not None:
        return _LEVEL["gain"]
    try:
        d = json.loads(_setup_level_path().read_text(encoding="utf-8"))
        g = float((d.get("gains") or {})[key]) if d.get("run_id") == run else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        g = None
    if g is not None and 0.05 <= g <= _PLAY_MAX_GAIN:
        _LEVEL.update(key=key, gain=g)
        return g
    return None


def setup_level_keep(gain: float) -> None:
    """Keep the gain this voice's first Setup line was measured at, for the rest of the run (atomic write)."""
    run = _setup_run_id()
    if not run:
        return
    key = _setup_level_key()
    _LEVEL.update(key=key, gain=float(gain))
    try:                                     # round 19: the shared, locked write; a coach's whole line may replace it
        from loudness import setup_gain_keep, setup_one_loudness_on
    except ImportError:
        setup_gain_keep = None
    if setup_gain_keep is not None and setup_one_loudness_on():
        if setup_gain_keep(_crissa_home(), run, key, float(gain), "start"):
            print("play-loud: %s keeps gain %.2f for the rest of Setup" % (key, gain), file=sys.stderr, flush=True)
        return
    p = _setup_level_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or d.get("run_id") != run:
            d = {}
    except (OSError, ValueError):
        d = {}
    gains = dict(d.get("gains") or {}) if d else {}
    if key in gains:
        return                               # another line of this voice kept it first: that one stands
    gains[key] = round(float(gain), 4)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(".%s.%d.tmp" % (p.name, os.getpid()))
        tmp.write_text(json.dumps({"run_id": run, "gains": gains, "at": time.time()}) + "\n", encoding="utf-8")
        os.replace(tmp, p)
        print("play-loud: %s keeps gain %.2f for the rest of Setup" % (key, gain), file=sys.stderr, flush=True)
    except OSError as e:
        print("play-loud: Setup's one level not kept (%s)" % type(e).__name__, file=sys.stderr, flush=True)


# ONE GAIN PER VOICE OUTSIDE SETUP (round 20, the User 2026-10-14, .101 round 19: "volume went lower and higher" -
# Setup's lines held one gain, conversation lines were levelled one by one: 0.38, 0.33, ...). In a pack, outside a Setup
# run, a voice plays at ONE gain (lib/loudness ONE GAIN PER VOICE FOR THE SESSION): Setup's whole-line measure where it
# has one, else her first line's start, replaced once by the first whole line of hers with VOICE_LINE_MIN_S of speech
# (measured on a thread at the stream's end, at most LEVEL_LINE_CAP_S of it, so the tail's alignment never waits).
# The house: unchanged. Kill: CRISSA_VOICE_ONE_LEVEL=0.
_VLEVEL: dict = {"key": None, "gain": None}
LEVEL_LINE_CAP_S = 12.0


def _voice_level_on() -> bool:
    if _HOUSE or _setup_run_id():
        return False
    try:
        from loudness import voice_one_level_on
    except ImportError:
        return False
    return voice_one_level_on()


def voice_level_gain() -> float | None:
    """The one gain this voice keeps outside Setup, or None (none kept yet, the house, switched off)."""
    if not _voice_level_on():
        return None
    key = _setup_level_key()
    if _VLEVEL["key"] == key and _VLEVEL["gain"] is not None:
        return _VLEVEL["gain"]
    try:
        from loudness import voice_gain
        g, _how = voice_gain(_crissa_home(), key)
    except Exception:  # noqa: BLE001 - an unreadable level measures the line, as before
        g = None
    if g is not None:
        _VLEVEL.update(key=key, gain=g)
    return g


def _level_keep(gain: float) -> None:
    """A line's first measure: Setup's one level in a Setup run, else the voice's one level (round 20)."""
    setup_level_keep(gain)
    if not _voice_level_on():
        return
    key = _setup_level_key()
    _VLEVEL.update(key=key, gain=float(gain))
    try:
        from loudness import voice_gain_keep
        if voice_gain_keep(_crissa_home(), key, float(gain), "start"):
            print("play-loud: %s keeps gain %.2f for the session" % (key, gain), file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass


def level_line(pcm: bytes, sr: int) -> None:
    """A whole line of hers just made: its measure becomes the voice's one gain where only a line's start stood
    (Setup's file in a Setup run, the session's outside one). Never on the house."""
    if _HOUSE or not pcm or not PLAY_MATCH_LOUD:
        return
    try:
        import loudness as L
        if L.voiced_s(pcm, sr) < L.VOICE_LINE_MIN_S:
            return
        g = L.line_gain(pcm, sr, PLAY_TARGET_DB)
        if g is None:
            return
        g = min(max(g, L.MIN_GAIN if PLAY_ALLOW_CUT else 1.0), L.MAX_GAIN)
        key, home, run = _setup_level_key(), _crissa_home(), _setup_run_id()
        if run:
            if (_setup_level_on() and L.setup_one_loudness_on() and L.setup_gain(home, run, key)[1] != "line"
                    and L.setup_gain_keep(home, run, key, g, "line")):
                print("play-loud: %s keeps gain %.2f for the rest of Setup (a whole line)" % (key, g),
                      file=sys.stderr, flush=True)
            return
        if _voice_level_on() and L.voice_gain(home, key)[1] != "line" and L.voice_gain_keep(home, key, g, "line"):
            print("play-loud: %s keeps gain %.2f for the session (a whole line)" % (key, g), file=sys.stderr, flush=True)
    except Exception as e:  # noqa: BLE001 - a level never stops her line
        print("play-loud: the whole line was not measured (%s)" % type(e).__name__, file=sys.stderr, flush=True)


def level_line_bg(pcm: bytes, sr: int) -> None:
    if _HOUSE or not pcm:
        return
    cap = int(LEVEL_LINE_CAP_S * sr) * 2
    threading.Thread(target=level_line, args=(bytes(pcm[:cap]), sr), name="level-line", daemon=True).start()


def boost_play_pcm(pcm: bytes, sr: int = 24000, gain: float | None = None
                   ) -> tuple[bytes, float]:
    """Raise quiet PCM to PLAY_TARGET_DB speech-median. Same sample count.

    `gain` set: apply that linear gain (stream chunks after the first measure).
    Returns (pcm, gain_used). gain 1.0 = unchanged bytes when possible.
    While a pack's Setup runs, a voice's first measured gain is kept for the run (ONE LEVEL THROUGH SETUP)."""
    if not pcm or not PLAY_MATCH_LOUD:
        return pcm, 1.0
    measured = gain is None
    kept = None
    session = None
    if gain is None:
        kept = gain = setup_level_gain()
    if gain is None:
        session = kept = gain = voice_level_gain()          # round 20: the voice's one gain outside Setup
    if gain is None:
        med = _speech_med_rms(pcm, sr)
        if med is None or med <= 0:
            return pcm, 1.0
        med_db = 20.0 * math.log10(med)
        if med_db >= PLAY_TARGET_DB - _PLAY_DEADBAND_DB and not (
                PLAY_ALLOW_CUT and med_db > PLAY_TARGET_DB + _PLAY_DEADBAND_DB):
            _level_keep(1.0)
            return pcm, 1.0
        gain = 10.0 ** ((PLAY_TARGET_DB - med_db) / 20.0)
        gain = min(max(gain, 0.1 if PLAY_ALLOW_CUT else 1.0), _PLAY_MAX_GAIN)
        _level_keep(gain)
    if 0.98 <= gain <= 1.02:
        return pcm, 1.0
    n = len(pcm) // 2
    a = array.array("h")
    a.frombytes(pcm[: n * 2])
    lim = _PLAY_LIMIT * 32767.0
    out = array.array("h")
    for x in a:
        y = float(x) * gain
        if y > lim or y < -lim:
            y = lim * math.tanh(y / lim)
        if y > 32767.0:
            y = 32767.0
        elif y < -32767.0:
            y = -32767.0
        out.append(int(round(y)))
    if measured:
        print("play-loud gain=%.2f target=%.1f dB%s" % (
            gain, PLAY_TARGET_DB, " (the session's one level)" if session is not None else
            (" (Setup's one level)" if kept else "")), flush=True)
    return out.tobytes(), gain


def stretch_pcm(pcm: bytes, sr: int, tempo: float) -> bytes:
    """The buffered path's atempo: the whole clip through ffmpeg once, before
    paplay, so tempo (and the Composer's rate) apply there too. Measured
    2026-09-13: "Bob is better" with align-whole-line-first on played at
    the voice's speed while the dial said 0.6 — the short consonants then fell
    under MIN_HOLD and merged away ("failed to articulate")."""
    if not pcm or abs(float(tempo) - 1.0) < 0.01:
        return pcm
    try:
        r = subprocess.run(
            ["ffmpeg", "-loglevel", "quiet", "-f", "s16le", "-ar", str(sr), "-ac", "1",
             "-i", "pipe:0", "-filter:a", atempo_chain(tempo), "-f", "s16le", "pipe:1"],
            input=pcm, capture_output=True, timeout=60,
        )
        return r.stdout if r.returncode == 0 and r.stdout else pcm
    except Exception:  # noqa: BLE001  — fail open: the raw clip still plays
        return pcm


# DUCK THE OTHERS (the User 2026-09-13 05:4x: "half mute the background when this
# player is playing"). House speakers already take turns on the speech lock;
# what can talk over the Composer's audio is the gateways' own TTS. While a
# Composer-spawned line (Say, tiles drill) plays, every sink input already on
# the speech sink is set to HOUSE_SPEAK_DUCK_PCT (the conductor passes 50;
# 0 = off) and restored on exit. Our own paplay arrives after the snapshot.
def sink_index(name: str) -> str | None:
    za = _za()
    if za is not None:
        return za.sink_index(name)
    try:
        out = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return None
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] == name:
            return parts[0]
    return None


def parse_sink_inputs(text: str) -> list[dict]:
    """[{index, sink, volume_pct, name}] from `pactl list sink-inputs`."""
    here = Path(__file__).resolve().parent.parent / "lib"
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from audio_sink import parse_sink_inputs as _parse
    return _parse(text)


def _boost_paplay(sink: str) -> None:
    """Own thread: WirePlumber restores paplay at the last duck (0.125)."""
    here = Path(__file__).resolve().parent.parent / "lib"
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from audio_sink import boost_paplay
    try:
        boost_paplay(sink, pct=100, wait_s=2.0)
    except Exception:  # noqa: BLE001
        pass


def _boost_stream(za, sink: str, stream) -> None:
    """Own thread: the shim's boost, pinned to THIS line's player by pid."""
    try:
        za.boost_player(sink, pct=100, timeout_s=2.0, stream=stream)
    except Exception:  # noqa: BLE001
        pass


class _ShimPlayer:
    """A crissa_audio Stream in the shape voice_stream_play / paplay() already
    drive (`.stdin.write/close`, `.wait`, `.poll`, `.kill`), so the air clock's
    loops are untouched. A write to a player that has gone raises
    BrokenPipeError, exactly as a Popen's stdin does — that is what stops
    MouthAir being fed for a line nobody hears."""

    class _In:
        def __init__(self, stream):
            self._s = stream

        def write(self, chunk: bytes) -> None:
            if not self._s.write(chunk):
                raise BrokenPipeError("player gone")

        def close(self) -> None:
            self._s.end()

    def __init__(self, stream):
        self._s = stream
        self.stdin = self._In(stream)
        self.pid = stream.pid

    def poll(self):
        return self._s.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._s.wait(timeout)

    def kill(self) -> None:
        self._s.kill()


class Ducked:
    def __init__(self, sink: str, pct: int):
        self.sink, self.pct, self.was = sink, int(pct), []
        self._za_duck = None

    def __enter__(self):
        if self.pct <= 0 or self.pct >= 100:
            return self
        za = _za()
        if za is not None:
            # duck_others keeps this class's rules: 0 and >= 100 are off, and
            # the player itself is never ducked (restoring it muted the next line).
            self._za_duck = za.duck_others(self.sink, self.pct).__enter__()
            n = len(self._za_duck._was)
            if n:
                print("ducked %d sink input(s) to %d%%" % (n, self.pct), flush=True)
            return self
        here = Path(__file__).resolve().parent.parent / "lib"
        if str(here) not in sys.path:
            sys.path.insert(0, str(here))
        from audio_sink import is_paplay_input, list_sink_inputs
        idx = sink_index(self.sink)
        try:
            lst = list_sink_inputs()
        except Exception:  # noqa: BLE001
            return self
        for si in lst:
            if idx is not None and si["sink"] != idx:
                continue
            if is_paplay_input(si):
                continue  # never duck the mouth — restore would mute the next line
            if si["volume_pct"] is None:
                continue
            self.was.append((si["index"], si["volume_pct"]))
            subprocess.run(["pactl", "set-sink-input-volume", si["index"], "%d%%" % max(0, min(100, self.pct))], capture_output=True, timeout=5)
        if self.was:
            print("ducked %d sink input(s) to %d%%" % (len(self.was), self.pct), flush=True)
        return self

    def __exit__(self, *exc):
        if self._za_duck is not None:
            d, self._za_duck = self._za_duck, None
            d.__exit__(*exc)
            return False
        for index, pct in self.was:
            subprocess.run(["pactl", "set-sink-input-volume", index, "%d%%" % pct], capture_output=True, timeout=5)
        self.was = []
        return False


# HER VOICE IS IN THE PLAYER'S PIPE WHEN ITS WRITE RETURNS (2026-10-13, round 17 on .101: in conversation her mouth ran
# ~0.5 s ahead of her voice, by a different amount on each line, while the tester's sentence was in sync). The player's
# stdin was a default Popen pipe, which Python buffers: a write smaller than the buffer stays in this process until the
# buffer fills. Measured on this machine (tests/test_voice_pipe_flush_20261013.py: 48 hops of 1000 bytes, 20.8 ms apart,
# into this pipe, a timestamping reader at the far end): Python 3.12.13 gives the pipe its st_blksize, 4096 bytes, and
# the hops arrived 4000 bytes at a time, the first 83.5 ms after its write (an 8192-byte buffer, 3.12's
# io.DEFAULT_BUFFER_SIZE: 8000 at a time, 167 ms); Python 3.14.4 gives it 131072 bytes, and the 1 s line arrived whole
# at its close, its first hop 979 ms after its write. Flushed: every hop within 0.1 ms. The sheet stamps each row by the
# bytes WRITTEN, and round 17's stamp hold kept the sink 80 ms from dry by that count, so the sink ran dry while the
# hold thought it fed it and every row after the hold led her voice (the same test, a held line through
# voice_stream_play against a simulated server: 43 ms under PipeWire's rule and 188 ms under PulseAudio's for a 0.42 s
# hold; 0.1 ms flushed). The drill and Setup play a whole file (no pipe): in sync. Every write into the player and into
# the tempo stage is flushed now; BufferedWriter.flush also finishes a short write, where a raw (bufsize=0) write could
# stop part-way. THE HOUSE TOO: rail-voice's ear bridge runs this script on /usr/bin/python3 (3.14 here since April), so
# the house's replies went to the player in pieces of up to 131072 bytes (2.7 s of 24 kHz audio), a shorter line whole
# at its close, while the Composer's own Says run on the conductor's 3.12 (4096). Both go as written now: on the wall
# her voice starts sooner after a reply opens and her rows sit on it. Audio8's chunks and the crissa_audio shim already
# flushed. Kill: HOUSE_SPEAK_PIPE_FLUSH=0 (round 17's buffering, on the next line).
def pipe_flush_on() -> bool:
    return os.environ.get("HOUSE_SPEAK_PIPE_FLUSH", "1").strip().lower() not in ("0", "false", "no", "off")


class _FlushedPipe:
    """A Popen stdin whose every write is in the reader's pipe before write() returns. BufferedWriter.flush finishes a
    short write; a raw (bufsize=0) write can stop part-way and lose half a sample, which turns her voice to noise."""

    def __init__(self, f):
        self._f = f

    def write(self, b) -> int:
        n = self._f.write(b)
        self._f.flush()
        return n

    def __getattr__(self, name):
        return getattr(self._f, name)


def _flushed(f):
    """`f` handing every write to its reader at once (the default); `f` as it was with HOUSE_SPEAK_PIPE_FLUSH=0."""
    if f is None or isinstance(f, _FlushedPipe) or not pipe_flush_on():
        return f
    return _FlushedPipe(f)


def _pipe_player(argv: list, env: dict | None = None) -> subprocess.Popen:
    """A raw player reading her voice on its stdin (the streamed path's player), every write handed over at once."""
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    p.stdin = _flushed(p.stdin)
    return p


def _tempo_filter(sr: int, tempo: float) -> subprocess.Popen | None:
    """ffmpeg atempo stage between the voice's PCM and paplay; None when tempo==1."""
    if abs(tempo - 1.0) < 0.01:
        return None
    p = subprocess.Popen(
        ["ffmpeg", "-loglevel", "quiet", "-f", "s16le", "-ar", str(sr), "-ac", "1",
         "-i", "pipe:0", "-filter:a", atempo_chain(tempo), "-f", "s16le", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    p.stdin = _flushed(p.stdin)          # her voice goes into the tempo stage as it is written too (2026-10-13)
    return p


# THE WHOLE LINE IS ALIGNED WHEN THE VOICE HAS MADE IT, NOT WHEN THE PLAYER HAS NEARLY PLAYED IT (NN, .101 2026-10-11).
# A streamed voice with no sentence stamps (Pocket, the pack's CPU voice) is read as fast as the player drains its
# pipe: at tempo 1.0 there is no ffmpeg stage, so the read loop runs at playback pace and the whole-line alignment
# was asked for only when the last bytes went into the player, about 2 s before the line ENDED. Every hop before
# that was the letter clock - the lip-sync test line (7.5 s) spoke on it for its first ~5.5 s, and a line cut short
# never got an alignment at all ("aligned" in the line log named only its tail). On .101 Pocket had made the 7.5 s
# test line 3.0 s into its air, while the paced read ends about 1.9 s before the line ends (a 64 KB pipe, 1.4 s of
# audio, plus paplay's 0.5 s buffer; an estimate, not a measurement), so the stamp lands about 2.5 s earlier there,
# and a line cut after the voice finished it still gets one. The stream is read on its own thread
# (lib/turbo_stream.TurboReader, the reader the Turbo stamps use) and the whole line is aligned the moment the
# voice's last chunk arrives. A body that is not chunked, a Turbo line with stamps, or the house: unchanged.
# On in a pack, off on the house (its replies carry Turbo's sentence stamps); HOUSE_MOUTH_ALIGN_EARLY=1|0 forces it.
def align_early_on() -> bool:
    raw = os.environ.get("HOUSE_MOUTH_ALIGN_EARLY", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return not _HOUSE


class _EarlyClock:
    """The whole streamed line, collected as it arrives on the reader thread; `attach(air)` aligns it once the
    voice has finished (the reader's EOF), off the audio path. Never raises into the line.

    With `marks` (Pocket's piece marks, round 11): each piece is aligned the moment its end mark arrives (a _MarkClock),
    so the sheet is stamped piece by piece while the voice is still making the rest; the whole line is aligned at the
    end only when a piece failed or no mark came (an older voice server)."""

    def __init__(self, resp, marks: bool = False):
        lib = Path(__file__).resolve().parent.parent / "lib"
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        import turbo_stream
        self.whole = bytearray()
        self.attached = False
        self.mc = _MarkClock(int(resp.headers.get("X-Sample-Rate") or 24000)) if marks else None
        self._last_end = 0
        self.reader = turbo_stream.TurboReader(resp, on_piece=self._got,
                                               on_mark=self._mark if self.mc is not None else None)

    def _got(self, index, start, end, data) -> None:
        first = not self.whole
        self.whole.extend(data)                      # the reader thread only; read after `done`
        if first:
            try:
                self.t_first = time.monotonic()      # Pocket's pace: audio made per second from its first audio
            except Exception:  # noqa: BLE001
                pass

    def _mark(self, index, end, text) -> None:
        start, self._last_end = self._last_end, int(end)
        self.mc.marked(int(index), start, int(end), bytes(self.whole[start:end]), text)

    def attach(self, air: "MouthAir") -> None:
        self.attached = True
        if self.mc is not None:
            self.mc.attach(air)

        def _go():
            try:
                self.reader.done.wait()
                if self.mc is not None and not self.reader.error and getattr(self, "t_first", None) and not _HOUSE:
                    secs = time.monotonic() - self.t_first
                    if secs > 0.5:
                        # round 19: what Pocket MADE, the quiet its seams dropped included (the stream's `cut`); its
                        # pace is how fast it makes, and cutting silence it made never made it slower
                        _made = len(self.whole) + int(getattr(self.reader, "cut", 0) or 0)
                        _pace = _made / 2.0 / float(self.mc.sr or 24000) / secs
                        _LINE["pocket_pace"] = round(_pace, 2)
                        self._save_after = _pace         # saved with the pieces' margins once they are all stamped
                if self.reader.error or not self.whole or not mouth_align_on():
                    return                       # the end of the line keeps its own (fail-open) path
                done = self.mc is not None and self.mc.finish(bytes(self.whole))
                if getattr(self, "_save_after", None) is not None:
                    late = [p[1] - p[2] for p in (_LINE.get("pieces") or [])[1:] if p[1] > p[2]]
                    save_pocket_pace(self._save_after, max(late) if late else 0.0)
                if done:
                    return                       # every piece stamped as it came; nothing left to align
                if self.mc is not None and self.mc._fell_back:
                    return                       # the mark clock already aligned the whole line
                air.arm_clock(bytes(self.whole), _AIR_TEXT, tempo=TEMPO)
            except Exception as e:  # noqa: BLE001
                print("mouth clock: early alignment off for this line (%s: %s)" % (type(e).__name__, e),
                      file=sys.stderr, flush=True)
        threading.Thread(target=_go, daemon=True, name="early-clock").start()


def _early_clock_for(resp, marks: bool = False):
    """An _EarlyClock reading `resp`, or None (switched off, or the body is not chunked). Never raises."""
    try:
        if not align_early_on():
            return None
        if str(resp.headers.get("Transfer-Encoding") or "").strip().lower() != "chunked":
            return None
        marks = bool(marks) and str(resp.headers.get("X-Voice-Engine") or "").strip().lower() == "pocket"
        ec = _EarlyClock(resp, marks=marks)
        _LINE["align_early"] = True
        if marks:
            _LINE["piece_marks"] = True
        return ec
    except Exception as e:  # noqa: BLE001
        print("mouth clock: early alignment off for this line (%s: %s)" % (type(e).__name__, e),
              file=sys.stderr, flush=True)
        return None


def _piece_clock_for(sent: str, resp, sr: int):
    """(clock, reader) when sentence stamps are on and the streamed voice is Turbo, else None. Never raises."""
    try:
        if not (align_pieces_on() and voice_is_turbo() and mouth_align_on()):
            return None
        lib = Path(__file__).resolve().parent.parent / "lib"
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        import turbo_stream
        cut = turbo_stream.pieces(sent.strip(), turbo_stream.min_piece_chars())
        if not cut:
            return None
        strip = getattr(_SP, "strip_bracket_tags", None) if _SP is not None else None
        texts = [(strip(t)[0] if strip else t) for t in cut]
        pc = _PieceClock(texts, sr)
        _LINE["pieces"] = len(texts)
        return pc, turbo_stream.TurboReader(resp, on_piece=pc.arrived)
    except Exception as e:  # noqa: BLE001
        print("mouth clock: sentence stamps off for this line (%s: %s)" % (type(e).__name__, e),
              file=sys.stderr, flush=True)
        return None


class _PieceClock:
    """Turbo's sentences, stamped one by one as they arrive (HOUSE_MOUTH_ALIGN_PIECES, the User 2026-10-10).

    `arrived` runs on the reader thread for each finished piece; one worker aligns them in order (rail-align
    takes one line at a time anyway) and hands the stamped prefix to the line's MouthAir. A piece count that
    does not match the cut, a failed alignment or a broken stream falls back to the whole-line alignment at
    the end, which is exactly the old path."""

    def __init__(self, texts: list[str], sr: int):
        self.texts = list(texts)
        self.sr = int(sr or 24000)
        self.raw = bytearray()
        self.ends: list[int] = []
        self.words: dict[int, list] = {}
        self.ms: list[float] = []
        self.air: "MouthAir | None" = None
        self.failed = False
        self.mismatch = False
        self.ended = False
        self.whole: bytes | None = None
        self._fell_back = False
        self._lock = threading.Lock()
        self.first = threading.Event()
        self._q: "queue.Queue" = queue.Queue()
        threading.Thread(target=self._work, daemon=True, name="piece-clock").start()

    def arrived(self, idx: int, start: int, end: int, pcm: bytes) -> None:
        self.raw.extend(pcm)
        self.ends.append(end)
        if idx >= len(self.texts):
            self.mismatch = True
            return
        self._q.put((idx, start, bytes(pcm)))

    def _work(self) -> None:
        while True:
            idx, start, pcm = self._q.get()
            if self.failed or self.mismatch:
                self.first.set()
                continue
            try:
                body = json.dumps({"text": self.texts[idx], "sr": self.sr,
                                   "pcm16_b64": base64.b64encode(pcm).decode("ascii")}).encode()
                req = urllib.request.Request(MOUTH_ALIGN_URL, data=body,
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=8.0) as r:
                    out = json.loads(r.read())
                if not out.get("ok"):
                    raise RuntimeError(out.get("error") or "align refused")
                off = start / 2.0 / float(self.sr)
                self.words[idx] = [dict(w, a=w["a"] + off, b=w["b"] + off,
                                        chars=[dict(c, a=c["a"] + off, b=c["b"] + off)
                                               for c in (w.get("chars") or [])] or None)
                                   for w in out["words"]]
                self.ms.append(float(out.get("ms") or 0.0))
                self.commit()
            except Exception as e:  # noqa: BLE001
                self.failed = True
                print("mouth clock: sentence %d align failed (%s: %s), the whole line will align"
                      % (idx + 1, type(e).__name__, e), file=sys.stderr, flush=True)
                self.fallback()
            finally:
                if idx == 0:
                    self.first.set()

    def commit(self) -> None:
        with self._lock:
            air = self.air
            if air is None or self.failed or self.mismatch or self._fell_back:
                return
            k = 0
            while k in self.words:
                k += 1
            if k == 0:
                return
            words = [w for i in range(k) for w in self.words[i]]
            air.set_piece_clock(bytes(self.raw[:self.ends[k - 1]]), " ".join(self.texts[:k]), words,
                                partial=k < len(self.texts) or bool(getattr(self, "more", False)), tempo=TEMPO,
                                note="%d of %d, %.0f ms last" % (k, len(self.texts), self.ms[-1] if self.ms else 0))

    def attach(self, air: "MouthAir") -> None:
        self.air = air
        self.commit()

    def end(self, whole: bytes, reader=None) -> None:
        """The stream is over. Fall back now when the cut and the chunks disagree or a sentence failed."""
        self.whole = whole
        self.ended = True
        got = len(reader.ends) if reader is not None else len(self.ends)
        if reader is not None and getattr(reader, "error", None):
            self.mismatch = True
        if got != len(self.texts):
            self.mismatch = True
        if self.mismatch or self.failed:
            self.fallback()

    def fallback(self) -> None:
        with self._lock:
            air, whole = self.air, self.whole
            if self._fell_back or air is None or whole is None:
                return
            self._fell_back = True
        air._clock_text = None
        air._clock_partial = False
        air.arm_clock(whole, _AIR_TEXT, tempo=TEMPO)


# POCKET'S PIECES ARE STAMPED AS THEY ARE MADE (round 11, the .101 lips, 2026-10-12: "lips are trying but not close").
# The early clock (NN) aligns a Pocket line only once Pocket has made all of it: on .101 a 375-character answer was
# aligned 13.75 s into its 25.7 s, so its first half spoke on the letter clock (22 % against his marks where the aligned
# clock scores 58 %). Pocket cuts the line into ~80-character chunks itself; asked for "marks", it names each chunk's
# end in the stream (lib/turbo_stream piece marks), and each chunk is aligned against its own text the moment it has
# all arrived - the same job as Turbo's sentence stamps, on Pocket's own cut. On in a pack (with the early clock), never
# on the house (Turbo); HOUSE_MOUTH_POCKET_MARKS=1|0 forces it. A server that sends no marks: today's early clock.
def pocket_marks_on() -> bool:
    raw = os.environ.get("HOUSE_MOUTH_POCKET_MARKS", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw not in ("1", "true", "yes", "on") and not align_early_on():
        return False
    return _main_name() == "pocket"


# ONE LINE PER PIECE, ONCE (round 17, 2026-10-13): the piece worker and the end of the stream (finish) both commit, and
# each read `_said` before either set it, so a one-piece line logged its piece twice ("pieces": [[1, 0.45, ...],
# [1, 0.46, ...]]: both one-piece lines of the round-17 bench at Pocket's free pace). The duplicate counted as a LATER
# piece that was late, so the next line's round-13 wait (_pocket_first_wait) held her first word 0.8 s for nothing: first
# sound 1.47 s on that line against 0.65-0.67 s on the others. The check and the note are one step now. Kill:
# HOUSE_SPEAK_PIECE_ONCE=0.
PIECE_ONCE = os.environ.get("HOUSE_SPEAK_PIECE_ONCE", "1").strip().lower() not in ("0", "false", "no", "off")


class _MarkClock(_PieceClock):
    """Pocket's chunks as pieces: the texts arrive with the marks, in order, so the cut is never guessed."""

    def __init__(self, sr: int):
        super().__init__([], sr)
        self.more = True                 # the stream is still open: later chunks keep the letter clock
        self._say_lock = threading.Lock()
        self.voice_at: dict = {}         # piece index -> seconds into its own audio where her voice begins (round 17)

    def marked(self, idx: int, start: int, end: int, pcm: bytes, text: str) -> None:
        if idx != len(self.texts) or not str(text).strip():
            self.mismatch = True         # out of order or empty: the whole line aligns at the end
            return
        self.texts.append(str(text))
        self.arrived(idx, start, end, pcm)       # to the aligner first: the onset below must not delay its stamp
        if not _HOUSE:
            self.voice_at[idx] = _voice_onset_s(pcm, self.sr)

    def commit(self) -> None:
        super().commit()
        if PIECE_ONCE:
            with self._say_lock:
                self._say()
        else:
            self._say()

    def _say(self) -> None:
        air = self.air
        k = 0
        while k in self.words:
            k += 1
        if air is None or k <= getattr(self, "_said", 0) or self.failed or self.mismatch:
            return
        # one line per stamped piece: when it landed against when its audio plays (heard seconds from the first sample)
        bps = 2.0 * float(self.sr) * float(TEMPO or 1.0)
        for i in range(getattr(self, "_said", 0), k):
            a = (self.ends[i - 1] if i else 0) / bps
            b = self.ends[i] / bps
            # when it is heard, the pauses before it included (sheet_gaps_on: a dry player moved it later)
            try:
                g = float(air._gap_at(int(a * 1000.0 / MOUTH_HOP_MS)))
            except Exception:  # noqa: BLE001
                g = 0.0
            a, b = a + g, b + g
            at = time.time() - float(air.air_at)
            print("mouth clock: piece %d stamped %.2fs into the line (its audio %.2f-%.2fs)"
                  % (i + 1, at, a, b), file=sys.stderr, flush=True)
            if not _HOUSE and len(_LINE.setdefault("pieces", [])) < 40:
                # the line log keeps each piece's margin (round 12): [piece, stamped s, its audio from, to]; a stamp
                # after its audio began spoke that much on the letter clock
                _LINE["pieces"].append([i + 1, round(at, 2), round(a, 2), round(b, 2)])
                v = self.voice_at.get(i)
                if v is not None:
                    # round 17: when her VOICE in this piece is heard (its own lead-in quiet and any silence held for its
                    # stamp included), so the line can say how long her voice, not the piece's first sample, ran unstamped
                    a0 = (self.ends[i - 1] if i else 0) / bps
                    try:
                        gv = float(air._gap_at(int((a0 + v / float(TEMPO or 1.0)) * 1000.0 / MOUTH_HOP_MS)))
                    except Exception:  # noqa: BLE001
                        gv = g
                    _LINE.setdefault("piece_voice", []).append([i + 1, round(a0 + v / float(TEMPO or 1.0) + gv, 2)])
        self._said = k

    def finish(self, whole: bytes, wait_s: float = 8.0) -> bool:
        """The stream is over. True when every chunk is stamped (the final commit says the line is whole)."""
        self.whole = whole
        self.ended = True
        t0 = time.time()
        while time.time() - t0 < wait_s and not (self.failed or self.mismatch) and len(self.words) < len(self.texts):
            time.sleep(0.02)
        if self.failed or self.mismatch or not self.texts or len(self.words) < len(self.texts) \
                or (self.ends and self.ends[-1] != len(whole)):
            return False
        self.more = False
        self.commit()
        return True


# A SLOW POCKET WAITS A BEAT FOR ITS FIRST STAMP (round 13, .101 under load: "lips falling behind on reading", 3 of a
# 5-piece reading's pieces playing before their stamps). A piece is stamped only once Pocket has made all of it; on a
# busy CPU Pocket runs barely ahead of the player, so every early piece plays unstamped. When her last Pocket line (kept
# in the logs folder, younger than POCKET_PACE_AGE_S) was slow (under POCKET_SLOW_PACE x real time) or had a piece after
# the first play more than POCKET_LATE_S before its stamp, her first word waits for the first piece's stamp, up to POCKET_FIRST_WAIT_S: Pocket keeps making while she waits,
# so every later piece gains that margin. A fast Pocket, or no line yet, never waits (no cost on a free CPU). The line
# log names `pocket_pace` and `first_wait_s`. Pack only; HOUSE_POCKET_FIRST_WAIT_S=0 is off.
POCKET_FIRST_WAIT_S = float(os.environ.get("HOUSE_POCKET_FIRST_WAIT_S", "0.8") or 0)
POCKET_SLOW_PACE = float(os.environ.get("HOUSE_POCKET_SLOW_PACE", "1.6") or 0)
POCKET_PACE_AGE_S = float(os.environ.get("HOUSE_POCKET_PACE_AGE_S", "600") or 0)
POCKET_LATE_S = float(os.environ.get("HOUSE_POCKET_LATE_S", "0.25") or 0)   # a later piece this late last time: wait


def _pocket_pace_file() -> Path:
    return LINE_LOG.parent / "pocket-pace.json"


def save_pocket_pace(pace: float, late_s: float = 0.0) -> None:
    """Her last Pocket line: its pace, and how long its latest piece after the first played before its stamp."""
    try:
        f = _pocket_pace_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps({"pace": round(float(pace), 3), "late_s": round(float(late_s), 2),
                                   "at": round(time.time(), 1)}))
        os.replace(tmp, f)
    except Exception:  # noqa: BLE001
        pass


def last_pocket_pace() -> dict | None:
    try:
        d = json.loads(_pocket_pace_file().read_text())
        if time.time() - float(d.get("at") or 0) > POCKET_PACE_AGE_S:
            return None
        return {"pace": float(d["pace"]), "late_s": float(d.get("late_s") or 0.0)}
    except Exception:  # noqa: BLE001
        return None


def _pocket_first_wait(mc, reader, t_open: float, first_hop_ms: float, sr: int) -> None:
    """Hold her first word for the first piece's stamp when Pocket was slow on her last line. Never raises."""
    try:
        if POCKET_FIRST_WAIT_S <= 0:
            return
        last = last_pocket_pace()
        if last is None or mc.first.is_set():
            return
        _LINE["pocket_before"] = last
        pace = last["pace"]
        if pace >= POCKET_SLOW_PACE and last["late_s"] <= POCKET_LATE_S:
            return
        t0 = time.time()
        mc.first.wait(POCKET_FIRST_WAIT_S)
        _LINE["first_wait_s"] = round(time.time() - t0, 2)
        print("mouth clock: Pocket was slow on her last line (%.2fx real time, a piece %.2fs late): her first word "
              "waited %.2fs for its stamp" % (pace, last["late_s"], time.time() - t0), file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 - never cost her the line
        pass


# HER VOICE WAITS FOR ITS STAMP, IN SILENCE SHE ALREADY HAS (round 17, 2026-10-13, .101: "her lips are saying
# something not in the reading. Detached fully"; "3 of 6 pieces stamped before they played"; every short line "0 of 1").
# A Pocket piece can be stamped only once Pocket has made all of it, its mark has come and the aligner has answered;
# until then the glass shows the letter guess, and measured on this machine with the kit's own Pocket and aligner
# (docs/checkpoints/round17-lips-20261013) the guess showed the wrong shape on 81 % of the open-mouth rows it covered
# (636 of 787 over five lines with Pocket at real time; 63-90 % a piece).
# Every piece opens with Pocket's own lead-in quiet (0.4-1.0 s), and the sink plays it while the stamp is made; only
# what the stamp needs BEYOND that quiet costs anything. So: before the first voiced hop of a piece that is not stamped
# yet is written, house-speak waits for its stamp; while the sink still has audio (the pad, her quiet) nothing is added;
# when it is about to run dry, one hop of silence at a time is written and the sheet moves by exactly that (a sheet gap
# that is known, not guessed: the sink never runs dry, so no sound server's restart buffering can move her voice). At
# most STAMP_HOLD_S of silence before her first piece's voice and STAMP_HOLD_NEXT_S before a later piece's (between
# sentences); a piece whose clock failed or fell back is not waited for. Pack only (Pocket's marks); the round-13 wait
# before the pad is not needed with it. The line log says `stamp_hold` [[piece, silence added s, waited s, at her
# audio's s], ...] and the player's lip clock says the total. Kill: HOUSE_SPEAK_STAMP_HOLD=0 (the round-16 path, with
# the round-13 wait).
# OFF BY DEFAULT, HOUSE_SPEAK_STAMP_HOLD=1 TURNS IT ON (2026-10-13: round 17 did not pass on .101, her mouth "~0.5 s
# early even at the slider's end" in conversation and by a different amount each line; round 16 was in sync at lip sync
# 0). Read from the code, the hold moves every row after it by exactly the silence it writes (_flush's pts0 carries
# gap_s; _restamp cuts at the hold's hop and reads _gap_at; the rows before it keep their clock; air_at moves only by
# the first-feed arm feed() also does), so the sheet is right. What the hold needs and this file cannot prove is that
# silence written is heard on time: it runs the sink near dry on purpose, and the buffered pipe ran it dry
# (tests/test_voice_pipe_flush_20261013.py). The pipe is flushed and the margin is 0.2 s now, but how a sound server
# recovers from an underrun and how late a busy CPU wakes this thread are measured on .101, not here: the kit plays
# round 16's path until a .101 reading with =1 says the hold keeps her mouth on her voice. The house never holds.
STAMP_HOLD_S = float(os.environ.get("HOUSE_SPEAK_STAMP_HOLD_S", "1.0") or 0)
STAMP_HOLD_NEXT_S = float(os.environ.get("HOUSE_SPEAK_STAMP_HOLD_NEXT_S", "0.5") or 0)
# The audio kept in the sink while she waits: HOUSE_SPEAK_STAMP_HOLD_LOW_S, 0.2 s (round 17: 0.08). The server takes
# one graph quantum a cycle, 1024 frames at 48 kHz (21.3 ms) by default and up to 2048 (42.7 ms, clock.max-quantum),
# and with less than a quantum queued it plays silence and keeps what it has (PipeWire 1.6.2's pulse server, read
# 2026-10-13; PulseAudio then waits to refill its prebuf). The player reads its pipe when the server asks, every ~20 ms
# (pulse.default.req). The hold looks every 5 ms, on a CPU Pocket keeps busy on .101. 80 ms covered a quantum and a
# request with ~17 ms to spare for a late wakeup; 0.2 s leaves ~137 ms and stays under the 0.5 s the player's
# --latency-msec lets the server queue. Its cost: when the hold writes silence, her voice after it starts up to 0.12 s
# later than at 0.08 s, and the sheet moves with it.
STAMP_HOLD_LOW_S = float(os.environ.get("HOUSE_SPEAK_STAMP_HOLD_LOW_S", "0.2") or 0)
STAMP_VOICE_RMS = 0.01      # her voice: Pocket's lead-in quiet reads ~0.0005, her speech 0.02-0.2 (full scale 1)
# THE FIRST PIECE WAITS FOR ITS STAMP (round 20, the User 2026-10-14, .101 round 19: "lips are amazing" at Setup's own
# +41.7, but "missed front of sentence a couple times"; her lip clock read "first stamp 0.57-0.91 s into the line" on the
# reading's pieces). A line's FIRST piece opens with Pocket's lead-in quiet (0.4-1.0 s), and its stamp came 0.57-0.91 s
# in: on the lines where the quiet was the shorter of the two, her first words played on the letter guess. Later pieces
# are made ahead of the voice (Pocket at 1.8-2.1x real time on .101) and were stamped before they played. So the default
# in a pack now holds ONLY a line's first piece, by the round-17 hold above (silence is written only once her own quiet
# is spent, the sheet moves by exactly what is written), capped at STAMP_HOLD_FIRST_S (0.6 s: the reported stamps less
# the shortest quiet). HOUSE_SPEAK_STAMP_HOLD=1 holds every piece (round 17); =0 is round 18-19's path (no hold).
STAMP_HOLD_FIRST_S = float(os.environ.get("HOUSE_SPEAK_STAMP_HOLD_FIRST_S", "0.6") or 0)


def stamp_hold_on() -> str:
    """The hold's mode: "all" (HOUSE_SPEAK_STAMP_HOLD=1, every piece), "first" (=first: a line's first piece, the
    pack's default rounds 20-27), "" (the default since round 28, or the house: no hold). Round 28 (.101 2026-10-17):
    round 22's "hold off, perfect" never shipped and round 27 held each first piece 0.52-0.58 s; with it off, "no
    pause". HOUSE_SPEAK_STAMP_HOLD=first brings round 27 back."""
    if _HOUSE:
        return ""
    v = os.environ.get("HOUSE_SPEAK_STAMP_HOLD", "0").strip().lower()
    if v in ("1", "true", "yes", "on", "all"):
        return "all" if (STAMP_HOLD_S > 0 or STAMP_HOLD_NEXT_S > 0) else ""
    if v == "first":
        return "first" if STAMP_HOLD_FIRST_S > 0 else ""
    return ""


def stamp_hold_pre_s() -> float:
    """How far before her voice her mouth is shown: the line's sheet lead + the tune's longest pad + 2 hops of per-class
    prep (the rows the stamp must reach before they are due). Clamped to 0.1-0.5 s."""
    try:
        pad = max(int(float(os.environ.get("HOUSE_MOUTH_SENT_PAD", "0") or 0)),
                  int(float(os.environ.get("HOUSE_MOUTH_WORD_PAD", "0") or 0))) + 2
        return max(0.1, min(0.5, max(0.0, sheet_lead_s()) + pad * MOUTH_HOP_MS / 1000.0))
    except Exception:  # noqa: BLE001
        return 0.3


def _hop_rms(pcm, a: int, b: int) -> float:
    """RMS (full scale 1) of s16le samples pcm[a:b] (byte offsets, even)."""
    seg = array.array("h")
    seg.frombytes(bytes(pcm[a:b - ((b - a) % 2)]))
    if not seg:
        return 0.0
    return math.sqrt(sum(map(int.__mul__, seg, seg)) / len(seg)) / 32768.0


def _voice_onset_s(pcm: bytes, sr: int) -> float | None:
    """Seconds into `pcm` where her voice begins (the first semiframe hop at STAMP_VOICE_RMS), None for none."""
    try:
        hb = max(2, int(int(sr) * MOUTH_HOP_MS / 1000.0) * 2)
        for a in range(0, len(pcm) - 1, hb):
            if _hop_rms(pcm, a, min(len(pcm), a + hb)) >= STAMP_VOICE_RMS:
                return a / 2.0 / float(sr)
    except Exception:  # noqa: BLE001
        pass
    return None


class _StampHold:
    """The writer's side of HER VOICE WAITS FOR ITS STAMP. `write(raw, out)` replaces `sink.write(out); air.feed(out)`
    (raw: the voice's own bytes, out: the same samples as played); `wpos` counts the voice bytes written."""

    def __init__(self, mc, air, sink, sr: int, mode: str = "all"):
        self.mc, self.air, self.sink, self.sr = mc, air, sink, int(sr or 24000)
        self.mode = mode or "all"
        self.hop_b = max(2, int(air.hop) * 2)
        self.pre_b = int(round(stamp_hold_pre_s() * self.sr / air.hop)) * self.hop_b
        self.wpos = 0
        self.decided: set = set()
        self.held: list = []

    def _piece_at(self, pos: int):
        ends = list(self.mc.ends)
        k = sum(1 for e in ends if e <= pos)
        return k, (ends[k] if k < len(ends) else None)

    def _free(self, k: int) -> bool:
        mc = self.mc
        return (getattr(mc, "_said", 0) > k or mc.failed or mc.mismatch or getattr(mc, "_fell_back", False)
                or self._cap(k) <= 0)

    def _cap(self, k: int) -> float:
        if self.mode == "first":
            return STAMP_HOLD_FIRST_S if k == 0 else 0.0           # round 20: a line's first piece only
        return STAMP_HOLD_S if k == 0 else STAMP_HOLD_NEXT_S

    def _emit(self, raw: bytes, out: bytes) -> None:
        if out:
            self.sink.write(out)        # handed to the player before feed() stamps it (_pipe_player, 2026-10-13)
            self.air.feed(out)
        self.wpos += len(raw)

    def _voice(self, raw: bytes, pos: int, stop: int):
        """The raw index (on the sheet's hop grid) of the first voiced hop in raw[pos:stop], or None."""
        here = self.wpos
        i = pos + ((-here) % self.hop_b)                   # the first hop boundary at or after `here`
        if i > pos and _hop_rms(raw, pos, min(stop, i)) >= STAMP_VOICE_RMS:
            return pos
        while i < stop:
            if _hop_rms(raw, i, min(stop, i + self.hop_b)) >= STAMP_VOICE_RMS:
                return i
            i += self.hop_b
        return None

    def write(self, raw: bytes, out: bytes) -> None:
        pos, n = 0, len(raw)
        while pos < n:
            k, end = self._piece_at(self.wpos)
            stop = n if end is None else min(n, pos + max(2, end - self.wpos))
            if k not in self.decided:
                if self._free(k):
                    self.decided.add(k)
                else:
                    v = self._voice(raw, pos, stop)
                    if v is not None:
                        cut = max(pos, v - self.pre_b)
                        self._emit(raw[pos:cut], out[pos:cut])
                        pos = cut
                        self._hold(k)
                        self.decided.add(k)
                        continue
            self._emit(raw[pos:stop], out[pos:stop])
            pos = stop

    def _hold(self, k: int) -> None:
        air = self.air
        cap = self._cap(k)
        lat = MOUTH_LATENCY_MS / 1000.0
        hop_s = air.hop / float(self.sr)
        zero = b"\x00" * self.hop_b
        t0, added, started = time.time(), 0.0, False
        try:
            while not self._free(k):
                if air.sheet_end() - (time.time() + lat) > STAMP_HOLD_LOW_S:
                    time.sleep(0.005)
                    continue
                if added + hop_s > cap + 1e-9:
                    break
                if not started:
                    started = True
                    if not air._armed:                  # nothing of hers fed yet: arm the clock as feed() would
                        air._armed = True
                        air.air_at = max(air.air_at, time.time() + lat)
                        air._post_lead_prefix()
                    air._flush()                        # the rows so far keep the clock they were made on
                    try:
                        from mouth_viseme import REST, class_index
                        air._q.put_nowait({"air_at": air.air_at, "air_until": None, "sr": air.sr,
                                           "hop_ms": MOUTH_HOP_MS, "pts0": round(air.sheet_end() - sheet_lead_s(), 4),
                                           "rms": [0.0], "visemes": [class_index(REST)], "speaker": _AIR_WHO})
                    except Exception:  # noqa: BLE001 - the last row stays up for the wait, as in a dry sink
                        pass
                self.sink.write(zero)
                air.gap_s += hop_s
                idx = air.samples // air.hop
                if air._gaps and air._gaps[-1][0] == idx:
                    air._gaps[-1] = (idx, air.gap_s)
                else:
                    air._gaps.append((idx, air.gap_s))
                added += hop_s
        except Exception as e:  # noqa: BLE001 - never cost her the line
            print("mouth clock: stamp hold off (%s: %s)" % (type(e).__name__, e), file=sys.stderr, flush=True)
        waited = time.time() - t0
        self.held.append([k + 1, round(added, 3), round(waited, 2), round(self.wpos / 2.0 / self.sr, 3)])
        _LINE["stamp_hold"] = self.held[:40]
        _LINE["stamp_hold_s"] = round(sum(h[1] for h in self.held), 3)
        if added > 0 or waited >= 0.05:
            print("mouth clock: piece %d's voice waited %.2fs for its stamp (%.2fs of silence added)"
                  % (k + 1, waited, added), file=sys.stderr, flush=True)


# A SHORT LINE IS ONE PIECE (round 27, .101 2026-10-16: "pause between phrases not sentences ... just one" - her first
# clause, 52-60 characters, came out as two Pocket pieces, cut at 40 so she starts sooner, and the second started 0.58 s
# after the first ended, twice: "after bake ... cookies"). Her first spoken line is already only a clause (the bridge
# speaks the rest as its own lines, prespoken), so cutting it again buys nothing and costs a pause mid-phrase. A line of
# ONE_PIECE_MAX characters or fewer is asked for whole ("chunk_first_max": 0). A pack only (the house's voice is not
# Pocket). Kill: HOUSE_SPEAK_ONE_PIECE_MAX=0.
ONE_PIECE_MAX = int(os.environ.get("HOUSE_SPEAK_ONE_PIECE_MAX", "80") or 0)


def one_piece(text: str) -> bool:
    return (not _HOUSE) and ONE_PIECE_MAX > 0 and 0 < len(str(text or "").strip()) <= ONE_PIECE_MAX


def voice_stream_play(text: str, voice: str, sink: str) -> tuple[int, int, bytes]:
    """Stream the voice's PCM straight into paplay as chunks arrive.

    Returns (bytes_played, sample_rate, full_pcm_buffer). First audible
    sound ~the voice's first hop (<1s) instead of full-clip synthesis (6-14s).
    The 400ms lead pad still fronts playback so the waking sink eats
    silence, not words.
    """
    if not VOICE_URL:
        raise RuntimeError("no streamed voice on this machine (VOICE_URL / house config voice_url)")
    marks = pocket_marks_on()
    said = _voice_text(text, "voice stream")
    body = json.dumps(dict({"text": said, "voice": voice},
                           **({"marks": True} if marks else {}),
                           **({"chunk_first_max": 0} if one_piece(said) else {}))).encode()
    req = urllib.request.Request(
        VOICE_URL, data=body, headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Prefer quality over chop: if first audio is late, abort stream so caller
    # (ear-bridge) can type on the wall — User 2026-08-06: degraded voice worse
    # than text. Never fall through to Qwen for free-air.
    timeout = _voice_timeout(default="45")
    first_ms = float(os.environ.get("HOUSE_SPEAK_FIRST_CHUNK_MS", "2500"))
    t_open = time.time()
    resp = urllib.request.urlopen(req, timeout=timeout)
    sr = int(resp.headers.get("X-Sample-Rate") or 24000)
    if resp.headers.get("X-Voice"):        # the voice server names the voice it speaks with (Pocket, 2026-10-07)
        print("voice: %s speaks this line as %s (for %s)" % (resp.headers.get("X-Voice-Engine") or "the voice",
                                                            resp.headers.get("X-Voice"), voice), flush=True)
    pc = _piece_clock_for(text[:800], resp, sr)
    if pc is not None:
        pc, resp = pc
    early = _early_clock_for(resp, marks) if pc is None else None    # NN: aligned as it is made (Pocket: by piece)
    if early is not None:
        resp = early.reader
    prebuf_ms = int(os.environ.get("HOUSE_SPEAK_PREBUF_MS", "500"))
    read_bytes = int(os.environ.get("HOUSE_SPEAK_READ_BYTES", "48000"))  # ~1s @24k
    # Drain the voice fully when first hop is slow OR HOUSE_SPEAK_BUFFER=1, then
    # single paplay — avoids tight write-loop spin under GPU starvation.
    force_buf = os.environ.get("HOUSE_SPEAK_BUFFER", "0") in ("1", "true", "yes")
    min_pcm = int(os.environ.get("HOUSE_SPEAK_MIN_PCM", "12000"))
    buf = bytearray()
    first = _http_read(resp, read_bytes)
    if not first:
        try:
            resp.close()
        except Exception:
            pass
        return 0, sr, b""
    buf.extend(first)
    first_hop_ms = (time.time() - t_open) * 1000.0
    slow = first_hop_ms > first_ms
    starve_s = tail_starve_ms() / 1000.0
    if slow or force_buf:
        # Finish download without starting paplay mid-starve
        while True:
            chunk = _http_read(resp, read_bytes)
            if not chunk:
                break
            buf.extend(chunk)
        try:
            resp.close()
        except Exception:
            pass
        # Hearable clip (incl. ~0.8s "I'm here.") always plays; skip was <1s.
        if not pcm_is_playable(bytes(buf), min_pcm=min_pcm):
            print(
                f"voice-degraded first_hop_ms={first_hop_ms:.0f} bytes={len(buf)} — skip play",
                flush=True,
            )
            return 0, sr, bytes(buf)
        # Play full clip once (no stream underrun)
        if not RENDER_ONLY:
            _unmute_line_sink(sink)
        env = os.environ.copy()
        env["PULSE_SINK"] = sink
        tmp = Path(tempfile.mkstemp(suffix=".wav", prefix="voice-buf-")[1])
        # tempo applies here too (it did not: the buffered clip played at the voice's own
        # speed). The clock then runs on the stretched bytes at tempo 1.
        buf = bytearray(stretch_pcm(bytes(buf), sr, TEMPO))
        buf = bytearray(boost_play_pcm(bytes(buf), sr)[0])
        try:
            pcm_to_wav(bytes(buf), tmp, sr=sr, tempo=1.0)   # stretched once, above
            # HOUSE_SPEAK_BUFFER=1 is "align the whole line first": wait for
            # the aligner as long as it takes (bounded). With the 0.6 s wait a
            # long line was posted from the raw letter clock — unaligned, no
            # min-hold, 1-hop flashes (the User 2026-09-13 03:4x, the sentence).
            paplay(tmp, sink, pcm=bytes(buf), sr=sr, clock_tempo=1.0,
                   align_wait_s=(float(os.environ.get("HOUSE_ALIGN_WAIT_BUFFERED_S", "8")) if force_buf else None))
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except TypeError:
                try:
                    tmp.unlink()
                except OSError:
                    pass
        print(f"ok {_main_name()}-buffered bytes={len(buf)} sr={sr} first_hop_ms={first_hop_ms:.0f}", flush=True)
        return len(buf), sr, bytes(buf)

    _unmute_line_sink(sink)
    env = os.environ.copy()
    env["PULSE_SINK"] = sink
    pa_yield()
    # Hold the shared PA/voice lock only while paplay occupies the sink.
    speech_cm = _speech_lock_cm(180.0)
    speech_cm.__enter__()
    # The ear's gate opens here as it always has (the pad is silence, so being
    # deaf through it is free). The value we hand it is the GLASS's clock: the
    # instant the sink will start her voice, which is still ~440ms away.
    if pc is not None and ALIGN_FIRST_WAIT_S > 0:
        pc.first.wait(ALIGN_FIRST_WAIT_S)          # her first sentence's stamp, before her first word
    mc = getattr(early, "mc", None) if early is not None else None
    hold_on = mc is not None and stamp_hold_on()            # round 17: her voice waits for its stamp, in her quiet
    if mc is not None and not _HOUSE and not hold_on:
        _pocket_first_wait(mc, early.reader, t_open, first_hop_ms, sr)
    air_at = time.time() + LEAD_SILENCE_MS / 1000.0 + MOUTH_LATENCY_MS / 1000.0
    _mouth_air(True, air_at=air_at)
    _air_arm(air_at)
    try:
        za = _za()
        if za is not None:
            # own_session=False IS THE LAW HERE, pinned by a test: say_stop_all
            # and abort_mouth SIGTERM this process's GROUP, and that is what
            # cuts her voice. A player in its own session would outlive the
            # stop and play out its latency buffer.
            stream = za.open_stream(sr, 1, sink, latency_ms=max(100, prebuf_ms),
                                    own_session=False)
            player = _ShimPlayer(stream)
            threading.Thread(target=_boost_stream, args=(za, sink, stream), daemon=True).start()
        else:
            # every write below (the pad, her voice, the hold's silence) is in the player's pipe when write() returns
            # (2026-10-13, HOUSE_SPEAK_PIPE_FLUSH; see _pipe_player)
            player = _pipe_player(
                ["paplay", f"--device={sink}", "--raw", f"--rate={sr}",
                 "--channels=1", "--format=s16le",
                 f"--latency-msec={max(100, prebuf_ms)}"],
                env=env,
            )
            threading.Thread(target=_boost_paplay, args=(sink,), daemon=True).start()
    except Exception:
        _mouth_air(False, air_at=air_at, air_until=time.time())
        _air_disarm()
        speech_cm.__exit__(None, None, None)
        raise
    played = len(buf)
    air: "MouthAir | None" = None
    filt = _tempo_filter(sr, TEMPO)
    pump = None
    if filt is not None:
        def _pump():
            try:
                while True:
                    out = filt.stdout.read(read_bytes)
                    if not out:
                        break
                    player.stdin.write(out)
                    if air is not None:
                        air.feed(out)
            except (BrokenPipeError, OSError):
                pass

        pump = threading.Thread(target=_pump, daemon=True)
    try:
        pad = b"\x00" * (2 * int(sr * LEAD_SILENCE_MS / 1000.0))
        # The pad goes STRAIGHT to paplay, bypassing atempo — so this stamp is
        # plain wall-clock and the first voice sample lands one pad later.
        t_pad = time.time()
        air_at = t_pad + LEAD_SILENCE_MS / 1000.0 + MOUTH_LATENCY_MS / 1000.0
        player.stdin.write(pad)
        _air_arm(air_at)
        _LINE["path"] = "stream"
        if MOUTH_AIR:
            air = MouthAir(air_at, sr)
            if pc is not None:
                pc.attach(air)
            elif early is not None:
                early.attach(air)
        if pump:
            pump.start()
        sink_in = filt.stdin if filt is not None else player.stdin
        hold = _StampHold(mc, air, sink_in, sr, hold_on) if (hold_on and air is not None and filt is None) else None
        # Prefill: first chunk already in buf
        prefill_need = 2 * int(sr * prebuf_ms / 1000.0)
        prefill = bytearray(buf)
        starved = False

        def _pull_chunk(phase: str) -> bytes | None:
            nonlocal starved, played
            t_chunk = time.time()
            try:
                chunk = _http_read(resp, read_bytes, timeout_s=starve_s)
            except TimeoutError:
                starved = True
                gap_ms = (time.time() - t_chunk) * 1000.0
                print(
                    f"voice-tail-starve gap_ms={gap_ms:.0f} bytes={len(buf)} phase={phase}",
                    flush=True,
                )
                return None
            if not chunk:
                return None
            prefill.extend(chunk)
            buf.extend(chunk)
            played += len(chunk)
            return chunk

        while len(prefill) < prefill_need:
            if _pull_chunk("prefill") is None:
                break
        # Crissa's voice pads ~1.6 s of near-silence; 500 ms prefill cannot
        # measure speech. Keep pulling until a voiced median exists or 2 s.
        loud_need = 2 * int(sr * 2.0)
        while (not starved and PLAY_MATCH_LOUD
               and _speech_med_rms(bytes(prefill), sr) is None
               and len(prefill) < loud_need):
            if _pull_chunk("loud") is None:
                break
        play_gain = 1.0
        rider = None
        if prefill:
            boosted, play_gain = boost_play_pcm(bytes(prefill), sr)
            if hold is not None:
                hold.write(bytes(prefill), boosted)
            else:
                sink_in.write(boosted)
                if air is not None and filt is None:
                    air.feed(boosted)
            rider = follow_rider(bytes(prefill), sr, play_gain)    # None unless HOUSE_SPEAK_FOLLOW_LINE=1, Crissa
        while not starved:
            t_chunk = time.time()
            try:
                chunk = _http_read(resp, read_bytes, timeout_s=starve_s)
            except TimeoutError:
                starved = True
                gap_ms = (time.time() - t_chunk) * 1000.0
                print(
                    f"voice-tail-starve gap_ms={gap_ms:.0f} bytes={len(buf)} phase=stream",
                    flush=True,
                )
                break
            if not chunk:
                break
            if rider is not None:                  # Crissa, HOUSE_SPEAK_FOLLOW_LINE=1: the gain follows the line
                play_gain = rider.feed(chunk)
            out, _ = boost_play_pcm(chunk, sr, gain=play_gain)
            if hold is not None:
                hold.write(chunk, out)
            else:
                sink_in.write(out)
                if air is not None and filt is None:
                    air.feed(out)
            buf.extend(chunk)
            played += len(chunk)
        level_line_bg(bytes(buf), sr)              # round 20: a whole line sets the voice's one gain (a thread)
        # THE VOICE HAS FINISHED GENERATING. `buf` is now the whole utterance, and
        # the pump is still feeding the player (and the mouth) for the rest of
        # the audio -- the voice runs faster than realtime, so that tail is most of
        # the line. Align it now and the remaining hops get real boundaries.
        #
        # The two clocks compose rather than fight, and this is measured, not
        # hoped for. Accuracy against the User's marks by fifth of the line:
        #
        #     fifth      0-20   20-40   40-60   60-80   80-100
        #     letter     39.6%   10.4%   10.4%   12.5%   38.8%
        #     aligned    62.5%   45.8%   68.8%   45.8%   65.3%
        #
        # The guess is at its best in the opening fifth, where no drift has
        # accumulated, COLLAPSES to 10% through the middle, then recovers at
        # the closing pause -- drift piling up and resetting, which is exactly
        # the User's "starts together but lips fall out of sync quick". The aligned
        # clock arrives for the later hops, so each covers the other's worst
        # stretch. Even arriving 40% in, the whole line scores 46.1% against
        # the letter clock's 22.4%.
        #
        # Raw voice bytes, so the alignment is pre-atempo -- hence tempo=.
        if air is not None and pc is not None and buf:
            pc.end(bytes(buf), resp)               # sentence stamps; the whole line only on a mismatch
        elif air is not None and early is not None and early.attached:
            pass                                   # aligned on the reader's EOF already (NN, _EarlyClock)
        elif air is not None and mouth_align_on() and buf:
            air.arm_clock(bytes(buf), _AIR_TEXT, tempo=TEMPO)
        try:
            sink_in.close()
        except (BrokenPipeError, OSError):
            pass
        if filt is not None:
            filt.wait(timeout=60)
            pump.join(timeout=60)
            player.stdin.close()
        player.wait(timeout=300)
    except (BrokenPipeError, OSError):
        pass
    finally:
        # air_until is the SCHEDULE end — when the last sample is heard — not
        # when paplay's process exits.
        air_until = time.time()
        if air is not None:
            air_at = air.air_at          # the self-corrected one
            air_until = air.sheet_end()          # + the pauses (sheet_gaps_on; 0 when off)
            air.close(air_until)
        _mouth_air(False, air_at=air_at, air_until=air_until, air_firm=True)
        _air_disarm()
        try:
            resp.close()
        except Exception:
            pass
        if player.poll() is None:
            try:
                player.stdin.close()
            except Exception:
                pass
            try:
                player.wait(timeout=10)
            except Exception:
                player.kill()
        try:
            speech_cm.__exit__(None, None, None)
        except Exception:
            pass
    print(
        f"ok {_main_name()}-stream bytes={played} sr={sr} first_hop_ms={first_hop_ms:.0f}",
        flush=True,
    )
    return played, sr, bytes(buf)


def char_spans_on() -> bool:
    """Use the aligner's per-character onsets, or spread a word by viseme weight?

    ON since 2026-09-21, when intake started keeping `chars`. It is a switch
    and not a law because the two paths schedule DIFFERENTLY and the better one
    is a measurement, not a deduction: with chars a word takes fill_word_timed
    (CONS_HOLD acts, consonants take their measured onsets), without it
    fill_word spreads the word by viseme weight.

    Off: HOUSE_MOUTH_CHAR_SPANS=0 — the pre-09-21 behaviour, with the chars
    left on disk. Flip it between two drill passes on the same film to see
    which your face prefers; the same-pass metrics disagree (chars raise
    `consonants seen`, which is DURATION, while `closures hit` is PLACEMENT),
    so judge on the glass and on r together, not on one number.
    """
    return os.environ.get("HOUSE_MOUTH_CHAR_SPANS", "1").strip() not in ("0", "false", "no")


def timeline_words_heard(path: Path, tempo: float) -> list:
    """[{w, a, b}] in HEARD seconds from a film timeline (a/b in film frames at its fps).
    The drill stretches the voice by `tempo` once, so film second t is heard at t / tempo."""
    try:
        tl = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    fps = float(tl.get("fps") or 24.0) or 24.0
    k = max(0.01, float(tempo or 1.0))
    out = []
    for w in tl.get("words") or []:
        try:
            a, b = float(w["a"]) / fps / k, float(w["b"]) / fps / k
        except (KeyError, TypeError, ValueError):
            continue
        if b < a:
            a, b = b, a
        row = {"w": str(w.get("w") or ""), "a": round(a, 4), "b": round(b, 4)}
        # CARRY THE CHARACTER SPANS (2026-09-21) — same frames -> heard
        # seconds conversion as the word. Without them char_spans_for_timing
        # hands schedule() a list of Nones and the whole word falls to
        # fill_word: no CONS_HOLD, no measured consonant onsets. A timeline
        # minted before 09-21 has no `chars` and simply keeps that behaviour.
        chars = []
        for c in (w.get("chars") or []):
            try:
                ca, cb = float(c["a"]) / fps / k, float(c["b"]) / fps / k
            except (KeyError, TypeError, ValueError):
                continue
            if cb < ca:
                ca, cb = cb, ca
            chars.append({"c": str(c.get("c") or ""), "a": round(ca, 4), "b": round(cb, 4)})
        if chars:
            row["chars"] = chars
        out.append(row)
    return out


def pack_plays_heard() -> bool:
    """A pack's buffered lines (say, piper, openai, kokoro) feed the mouth the bytes it hears (2026-10-05).
    The house keeps its own path byte for byte. Kill: CRISSA_MOUTH_STRETCHED=0."""
    if _HOUSE:
        return False
    return os.environ.get("CRISSA_MOUTH_STRETCHED", "1").strip().lower() not in ("0", "false", "no", "off")


def play_heard(pcm: bytes, sr: int, out: Path, sink: str, keep: bool = False) -> bytes:
    """Stretch ONCE and give paplay and the mouth the same bytes, as the streamed voice's buffered path does. pcm_to_wav
    stretched only the file, so MouthAir hopped the raw bytes and a pack's mouth ran ~20 % past the sound at
    tempo 1.2 (2026-10-05, the Mac). stretch_pcm returns the raw bytes when ffmpeg fails, and then those are what
    plays. The temp file goes when the line ends unless the caller asked for it (`keep`). Returns the bytes heard."""
    pcm = stretch_pcm(pcm, sr, TEMPO)
    pcm_to_wav(pcm, out, sr=sr, tempo=1.0)
    try:
        paplay(out, sink, pcm=pcm, sr=sr, clock_tempo=1.0)
    finally:
        if not keep:
            Path(out).unlink(missing_ok=True)
    return pcm


def pcm_to_wav(pcm: bytes, path: Path, sr: int = 24000, tempo: float | None = None) -> float:
    """`tempo` None = HOUSE_SPEAK_TEMPO on raw voice bytes; 1.0 = the bytes are
    already heard-time (the buffered path stretches once, up front, so the
    mouth clock and the file share the same bytes — 2026-09-13: stretching
    here AND there played 43 s of sound against a 26 s sheet).

    Returns the tempo it APPLIED: when ffmpeg is missing or fails, the line plays at normal speed and the
    mouth clock must be told 1.0, not the tempo it asked for (2026-10-04: a Mac with no ffmpeg crashed here)."""
    t = float(TEMPO if tempo is None else tempo)
    applied = 1.0
    if abs(t - 1.0) >= 0.01 and pcm:
        try:
            r = subprocess.run(
                ["ffmpeg", "-loglevel", "quiet", "-f", "s16le", "-ar", str(sr), "-ac", "1",
                 "-i", "pipe:0", "-filter:a", atempo_chain(t), "-f", "s16le", "pipe:1"],
                input=pcm, capture_output=True, timeout=60,
            )
            if r.returncode == 0 and r.stdout:
                pcm, applied = r.stdout, t
            else:
                print("tempo skipped (ffmpeg exit %s): played at normal speed" % r.returncode, file=sys.stderr, flush=True)
        except (OSError, subprocess.SubprocessError) as e:
            print("tempo skipped (ffmpeg: %s): played at normal speed" % type(e).__name__, file=sys.stderr, flush=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        # Startup-delay pad (User 2026-08-04): the sink wakes from suspend
        # and eats the first syllables — lead with silence so words survive.
        w.writeframes(b"\x00" * (2 * int(sr * LEAD_SILENCE_MS / 1000.0)))
        w.writeframes(pcm)
    return applied


# HER VOICE IN THE WALL PAGE (2026-10-05, lib/crissa_page_audio): with CRISSA_AUDIO_AT=page in a pack,
# a buffered line's WAV goes to the page that shows her, the page plays it and answers when its first
# sample is heard, on this machine's clock, and that answer is the line's air_at. No page that can play,
# or no answer within CRISSA_PAGE_ACK_S: the host plays it, as before. The house never takes this path
# (mode() is `host` whenever is_house()), so its lines are byte for byte what they were.
PAGE_ACK_S = float(os.environ.get("CRISSA_PAGE_ACK_S", "1.5"))
_PAGE_LINE: dict = {}


def _page_client():
    """A Client for the player's page audio, or None (host mode, the house, or no module)."""
    if _HOUSE:
        return None
    try:
        import crissa_page_audio as zpa
    except ImportError:
        return None
    if not zpa.enabled():
        return None
    return zpa.Client(_player_url())


def _page_line_stop() -> None:
    """A line that ends early (a stop, a signal) is cancelled in the page too."""
    c, lid = _PAGE_LINE.pop("client", None), _PAGE_LINE.pop("id", None)
    if c is not None and lid:
        c.cancel(lid)


def _page_send(path: Path, text: str) -> tuple[float, float] | None:
    """(start_at, seconds) when the page took the line, else None (the host plays it)."""
    c = _page_client()
    if c is None or not c.watching():
        return None
    try:
        wav = path.read_bytes()
        with wave.open(str(path), "rb") as w:
            secs = w.getnframes() / float(w.getframerate() or 1)
    except (OSError, wave.Error, EOFError):
        return None
    lid = c.push(wav, LEAD_SILENCE_MS / 1000.0, text)
    if not lid:
        return None
    _PAGE_LINE.update(client=c, id=lid)
    start_at = c.wait_air(lid, PAGE_ACK_S)
    if start_at is None:
        _page_line_stop()
        print("page did not answer in %.1f s: the host plays this line" % PAGE_ACK_S, file=sys.stderr, flush=True)
        return None
    return start_at, secs


def _page_wait_out(start_at: float, secs: float) -> None:
    """Hold the line (and the speech lock) until the page has played it out."""
    end = start_at + secs
    while True:
        left = end - time.time()
        if left <= 0:
            break
        time.sleep(min(left, 0.1))
    c, lid = _PAGE_LINE.pop("client", None), _PAGE_LINE.pop("id", None)
    if c is not None and lid:
        c.done(lid)


def paplay(path: Path, sink: str, pcm: bytes | None = None,
           sr: int = 24000, clock_tempo: float | None = None,
           align_wait_s: float | None = None) -> None:
    """Buffered mouth (HOUSE_SPEAK_BUFFER=1, or a slow first hop).

    Same air clock as the stream path. The whole envelope is known up front
    here, so it is published in one POST before a sample is heard.
    """
    env = os.environ.copy()
    env["PULSE_SINK"] = sink
    if not RENDER_ONLY:                 # render only: no sink, no announcer cut, no speech lock (nothing plays here)
        _unmute_line_sink(sink)
        pa_yield()
        hold_until_not_before()
    with (contextlib.nullcontext() if RENDER_ONLY else _speech_lock_cm(180.0)):
        # pcm_to_wav() writes the same LEAD_SILENCE pad into the file, so the
        # first voice sample is one pad past the moment paplay starts.
        air_at = time.time() + LEAD_SILENCE_MS / 1000.0 + MOUTH_LATENCY_MS / 1000.0
        air = None
        _LINE["path"] = "buffered"
        if MOUTH_AIR and pcm:
            air = MouthAir(air_at, sr)
            # Before feed(), because this path publishes the whole envelope in
            # one pass -- an alignment that lands after it has nothing to fix.
            # clock_tempo=1.0 when `pcm` is already stretched (the buffered voice)
            air.arm_clock(pcm, _AIR_TEXT,
                          wait_s=(MOUTH_ALIGN_WAIT_S if align_wait_s is None else align_wait_s),
                          tempo=clock_tempo)
        # The page path, after the aligner's wait for the same reason as the stamp below.
        page = _page_send(path, _AIR_TEXT) if not _HOUSE else None
        if air is not None:
            # STAMP THE AIR AFTER THE WAIT. air_at was taken before arm_clock,
            # which blocks for the aligner (1.3 s on a long line, up to 8 s on
            # the buffered path) — every hop then sat that far ahead of the
            # sound: "video talking to a different script" on the sentence,
            # invisible on Bob (2026-09-13 05:0x). Nothing has been posted yet.
            air_at = time.time() + LEAD_SILENCE_MS / 1000.0 + MOUTH_LATENCY_MS / 1000.0
            if page is not None:
                # the page's own answer: the file's first sample, plus the file's lead pad
                air_at = page[0] + LEAD_SILENCE_MS / 1000.0
            air.air_at = air_at
            if not _HOUSE and getattr(air, "_aligned_t", None):
                # THE BUFFERED LINE'S STAMP AGAINST ITS REAL FIRST SAMPLE (round 12, .101 read "aligned 0.02-0.08 s
                # after her first sample" off the test line): the landing was logged against the air time guessed
                # BEFORE the aligner's wait, so a line aligned 0.4 s before she spoke read as +0.05. Negative = the
                # whole line was stamped before her first sample.
                _LINE["aligned_after_s"] = round(float(air._aligned_t) - air_at, 2)
            air.feed(pcm)
        elif page is not None:
            air_at = page[0] + LEAD_SILENCE_MS / 1000.0
        if page is not None:
            _LINE["path"] = "page"
            _LINE["air_at"] = round(air_at, 3)          # the page's, not the guess MouthAir was built with
            _LINE["page_start_at"] = round(page[0], 3)
        _mouth_air(True, air_at=air_at)
        _air_arm(air_at)
        za = _za()
        try:
            if RENDER_ONLY:
                r = subprocess.CompletedProcess([], 0, "", "")   # the caller plays --out somewhere else
            elif page is not None:
                _page_wait_out(*page)
                r = subprocess.CompletedProcess([], 0, "", "")
            elif za is not None:
                # Same group as this process (own_session=False), for the same
                # reason as the stream: a Say-stop signals the group. And a
                # handle, not the blocking play_file, so the boost gets a pid.
                stream = za.start_file(path, sink, own_session=False)
                threading.Thread(target=_boost_stream, args=(za, sink, stream), daemon=True).start()
                try:
                    rc = stream.wait(300)
                except subprocess.TimeoutExpired:
                    stream.kill()
                    raise
                r = subprocess.CompletedProcess([], rc, "", stream.stderr_text() if rc else "")
            else:
                threading.Thread(target=_boost_paplay, args=(sink,), daemon=True).start()
                r = subprocess.run(
                    ["paplay", f"--device={sink}", str(path)],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=300,
                )
        finally:
            air_until = time.time()
            if air is not None:
                air_until = air.sheet_end()
                air.close(air_until)
            _mouth_air(False, air_at=air_at, air_until=air_until, air_firm=True)
            _air_disarm()
    if r.returncode != 0:
        raise RuntimeError(f"paplay failed ({sink}): {(r.stderr or r.stdout)[:300]}")


# Said to the person when a PACK has no voice (never on the house: _crissa_tts_cfg hands the house None first). Plain
# words only (the User 2026-10-07): no command, no setting name. lib/crissa_voice_install installs her voice at setup.
NO_VOICE = ("no voice on this machine: she answers in text. Crissa installs one when it starts; you can retry from "
            "the Connect page.")


_STREAMED = {"name": "", "announcer_chain": [], "backup_chain": []}   # the pack's streamed voice (the log names it)


def _main_name() -> str:
    """The streamed voice's name in the log and in `ok <name>-stream`: a pack's own (turbo, pocket), else MAIN."""
    return _STREAMED["name"] or MAIN


def _crissa_tts_cfg() -> dict | None:
    """The voice seam's choice for this line: a cfg dict, {} = nothing can speak (a pack: text only), or None = the
    house's own streamed voice -> backup path.

    The house keeps its streamed voice unless it names an engine (CRISSA_TTS, agent.json, a Mac). A pack ALWAYS asks the seam
    (2026-10-06, crissatest: an unnamed voice fell through to the house's GPU ports, which no install has): its voice
    tier's chain (lib/crissa_voice_tiers: Turbo or Multilingual on a GPU machine, Pocket on the CPU, else Kokoro, `say`, a
    named Piper model), else {}. A pack on a streamed voice (Turbo, Multilingual, Pocket: the same /stream contract) gets None
    too, with VOICE_URL pointed at its own module server and the log naming the voice. A pack that names its own streamed voice
    (VOICE_URL) gets that path back when the seam has nothing."""
    if _HOUSE and not (os.environ.get("CRISSA_TTS") or (_crissa_home_dir() / "agent.json").is_file() or _on_mac()):
        return None
    here = Path(__file__).resolve().parent.parent / "lib"
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import crissa_tts
    except ImportError:
        return None if _HOUSE else {}
    # demand=True: this is a line, so a held voice the ladder falls back to is started for it (round 17, 2026-10-13,
    # lib/crissa_voice_demand; a pack only, kill CRISSA_VOICE_ENGINES_ON_DEMAND=0)
    cfg = crissa_tts.config(demand=True)
    if cfg.get("engine") in getattr(crissa_tts, "STREAMED", ()):
        # A PACK WHOSE VOICE IS STREAMED (Turbo, Multilingual, Pocket) SPEAKS THROUGH THE HOUSE'S OWN STREAMED PATH (2026-10-06):
        # the module's server speaks the same /stream contract, so first sound comes after the first chunk, not the
        # whole line. The tier's backup and announcer come with it; what it passed over is said here and kept.
        global VOICE_URL, BACKUP, ANNOUNCER
        VOICE_URL = str(cfg.get("url") or VOICE_URL)
        _STREAMED["name"] = str(cfg["engine"])
        _STREAMED["announcer_chain"] = [str(a) for a in (cfg.get("announcer_chain") or [])]
        backup = [b for b in (cfg.get("backup") or []) if b in crissa_tts.ENGINES and b != cfg["engine"]]
        if backup and not os.environ.get("HOUSE_SPEAK_BACKUP"):
            BACKUP = backup[0]
            _STREAMED["backup_chain"] = backup     # every one is asked in order, not just the first (2026-10-07)
        if cfg.get("announcer") and not os.environ.get("HOUSE_SPEAK_ANNOUNCER"):
            ANNOUNCER = str(cfg["announcer"]).lower()
        for why in cfg.get("passed") or []:
            print("voice: passed over %s" % why, file=sys.stderr, flush=True)
        crissa_tts.note_choice(cfg)
        return None
    for why in cfg.get("passed") or []:
        print("voice: passed over %s; %s speaks" % (why, cfg.get("engine") or "nothing"), file=sys.stderr, flush=True)
    if cfg.get("passed"):
        crissa_tts.note_choice(cfg)
    if not cfg and (_HOUSE or VOICE_URL):
        return None
    return cfg


def _try_crissa_tts(spoken: str, voice: str, play, min_pcm: int, cfg: dict | None = None, play_chunks=None) -> int | None:
    """rc when the seam owns this line (0 spoken, 1 failed or nothing can speak), None = the voice path.

    Named and failed is rc 1, never a fall-through to the voice: an install that named an
    engine has no streamed voice, and a line that fails loudly beats one that waits 90 s on a
    port nobody serves."""
    if cfg is None:
        cfg = _crissa_tts_cfg()
    if cfg is None:
        return None
    if not cfg:
        print(NO_VOICE, file=sys.stderr, flush=True)
        print("text-only: " + NO_VOICE, flush=True)
        return 1
    import crissa_tts
    # Audio8 (the User 2026-10-08): the house's pieces by default (72 characters a request, the announcer under
    # 160, the first playing while the next renders: first sound ~4 s measured); with CRISSA_AUDIO8_STREAM=1,
    # /api/tts/stream plays from its first chunk through play_chunks (house-speak's raw player) when the stream
    # keeps real time, else it is cancelled and the pieces speak. Turbo, Multilingual and Pocket never reach this:
    # _crissa_tts_cfg returns None and the streamed path speaks them. Kill the pieces path: CRISSA_AUDIO8_SENTENCE=0.
    if (str(cfg.get("engine") or "") == "audio8"
            and os.environ.get("CRISSA_AUDIO8_SENTENCE", "1") != "0"):
        try:
            return crissa_tts.speak_audio8(spoken, voice, cfg, play, min_pcm, play_chunks=play_chunks)
        except crissa_tts.TTSError as e:
            print(f"tts fail (audio8): {e}", file=sys.stderr, flush=True)
            return 1
    try:
        pcm, sr = crissa_tts.synth(spoken, voice, cfg)
    except crissa_tts.TTSError as e:
        print(f"tts fail ({cfg.get('engine')}): {e}", file=sys.stderr, flush=True)
        return 1
    if len(pcm) < min_pcm:
        print(f"tts fail ({cfg.get('engine')}): short ({len(pcm)} bytes)", file=sys.stderr, flush=True)
        return 1
    return play(pcm, sr, "tts-" + str(cfg.get("engine")))


def _crissa_home_dir() -> Path:
    here = Path(__file__).resolve().parent.parent / "lib"
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import crissa_home
        return crissa_home.HOME
    except ImportError:
        return Path(os.environ.get("CRISSA_HOME") or (Path.home() / ".crissa")).expanduser()


def _tool_hold_blocks(voice: str) -> str | None:
    """If house-tool-hold is active and voice not allow-listed, return reason."""
    raw = os.environ.get("HOUSE_TOOL_HOLD", "/tmp/house-tool-hold.json" if _HOUSE else "")
    if not raw:
        return None
    hold = Path(raw)
    if not hold.is_file():
        return None
    try:
        # import sibling script as module path
        import importlib.util

        p = Path(__file__).resolve().parent / "house-tool-hold.py"
        spec = importlib.util.spec_from_file_location("house_tool_hold", p)
        if not spec or not spec.loader:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ok, why = mod.speak_allowed(voice)
        return None if ok else why
    except Exception:
        # fail-open only if hold file unreadable; prefer quiet
        try:
            d = json.loads(hold.read_text())
            until = float(d.get("until") or 0)
            if until and time.time() < until:
                return f"held:{d.get('reason') or 'tool'}"
        except Exception:
            return None
        return None


def line_pieces(text: str) -> list[str]:
    """A LONG LINE IS READ IN PIECES, in a pack (round 14, 2026-10-12, .101: a reading "Cut at 799 of 6667
    characters"): the streamed voice takes VOICE_TEXT_CAP characters a line, so a longer line is read as consecutive
    pieces of at most talk.speak.piece_chars (config/crissa-talk.json, default 760; lib/crissa_talk.split_pieces), each
    ending on a sentence and each its own line with its own mouth sheet. Nothing is dropped. One piece: as before.
    The house keeps one line (cut at the cap with a note). Kill: CRISSA_SPEAK_PIECE_CHARS=0 or piece_chars 0; a piece
    never splits again (HOUSE_SPEAK_PIECE)."""
    if _HOUSE or os.environ.get("HOUSE_SPEAK_PIECE") or len(text or "") <= VOICE_TEXT_CAP - 20:
        return [text]
    try:
        import crissa_talk
        size = crissa_talk.piece_size()
        return crissa_talk.split_pieces(text, size) if size > 0 else [text]
    except Exception as e:  # noqa: BLE001 - a missing module is one line, as before
        print("pieces: none (%s)" % type(e).__name__, file=sys.stderr, flush=True)
        return [text]


def _argv_without_text(argv: list[str]) -> list[str]:
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in ("--text", "--text-file"):
            skip = True
            continue
        if a.startswith("--text=") or a.startswith("--text-file="):
            continue
        out.append(a)
    return out


def speak_pieces(pieces: list[str], argv: list[str]) -> int:
    """Each piece is said by this script again, in turn, in this process group: a stop (the group's SIGTERM) ends the
    piece on the air and every piece after it. The pieces' own output reaches the caller, line by line. 0 when every
    piece spoke; else the last piece's failing code (later pieces still play: nothing is dropped)."""
    base = [sys.executable, str(Path(__file__).resolve())] + _argv_without_text(argv)
    total = sum(len(p) for p in pieces)
    print("line pieces: %d chars as %d pieces (%s)" % (total, len(pieces), ", ".join(str(len(p)) for p in pieces)),
          file=sys.stderr, flush=True)
    _line_log("pieces", chars=total, pieces=len(pieces), sizes=[len(p) for p in pieces])
    rc_all, cur = 0, {"p": None}

    def _stop(signum, frame):  # noqa: ARG001 - the group's stop: end the piece on the air, say no more
        p = cur["p"]
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
        sys.exit(128 + int(signum))

    try:
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    except (ValueError, OSError):
        pass
    for i, piece in enumerate(pieces):
        fd, tmp = tempfile.mkstemp(prefix="speak-piece-", suffix=".txt")
        os.close(fd)
        try:
            Path(tmp).write_text(piece, encoding="utf-8")
            env = dict(os.environ, HOUSE_SPEAK_PIECE="%d/%d" % (i + 1, len(pieces)))
            cur["p"] = subprocess.Popen(base + ["--text-file", tmp], env=env)
            rc = cur["p"].wait()
            cur["p"] = None
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if rc != 0:
            rc_all = rc
            print("line pieces: piece %d of %d ended %d" % (i + 1, len(pieces), rc), file=sys.stderr, flush=True)
    return rc_all


def main() -> int:
    import time  # tool-hold check

    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="")
    ap.add_argument("--text-file", type=Path, default=None)
    ap.add_argument("--voice", default="crissa")
    ap.add_argument(
        "--engine",
        default=None,
        help="auto|voice (the streamed voice) or a machine engine (default HOUSE_SPEAK_ENGINE or auto)",
    )
    ap.add_argument("--sink", default=None, help="Pulse sink (default: VOICE_SINK or pactl default)")
    ap.add_argument("--out", type=Path, default=None, help="optional wav keep path")
    ap.add_argument("--wav", type=Path, default=None,
                    help="TILES DRILL (2026-09-13): play this file's own audio (any media, "
                         "ffmpeg decodes it) instead of the voice — aligned whole-line, tempo 1 — "
                         "so the tiles walk a filmed harvest's real voice")
    ap.add_argument("--words", type=Path, default=None,
                    help="TILES DRILL: the film's timeline (visemes/<stem>.timeline.json: words with "
                         "a/b in film frames at fps) — the sheet walks these, no aligner")
    ap.add_argument("--tempo", type=float, default=None,
                    help="playback speed, pitch-preserving (default env HOUSE_SPEAK_TEMPO or 1.0)")
    ap.add_argument(
        "--ref-fallback",
        action="store_true",
        help="if the voice is down, play its ref wav (not the text)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="speak even during house-tool-hold (rare)",
    )
    ap.add_argument(
        "--pa-yield",
        action="store_true",
        help="cut a running PA line (SIGTERM + requeue) and exit",
    )
    ap.add_argument(
        "--not-before",
        type=float,
        default=0.0,
        help="unix time: synth now, paplay no earlier (vocative sit-up)",
    )
    args = ap.parse_args()
    _venv = resolve_voice_env(VOICE_ALIASES.get(args.voice.strip().lower(), args.voice.strip().lower()))
    if _venv:
        os.environ.update(_venv)
        print("voice switches: " + " ".join("%s=%s" % (k.replace("HOUSE_MOUTH_", "").lower(), v)
                                            for k, v in sorted(_venv.items())), file=sys.stderr, flush=True)
    prime_sheet_lead()   # resolve the lead before any sound: never a wait inside the hop stream
    prime_tune()         # and the rest of the tune, off the same answer (2026-09-27)
    if args.not_before and args.not_before > 0:
        global _NOT_BEFORE
        _NOT_BEFORE = float(args.not_before)
        # Drain the voice during the sit-up so the prefix is not added after land.
        os.environ["HOUSE_SPEAK_STREAM"] = "0"
        os.environ["HOUSE_SPEAK_BUFFER"] = "1"
    if args.pa_yield:
        print(f"pa_yield={int(bool(pa_yield()))}", flush=True)
        return 0
    if args.tempo is not None:
        global TEMPO
        TEMPO = args.tempo
    text = args.text
    if args.text_file:
        text = args.text_file.read_text(encoding="utf-8").strip()
    if not text:
        print("missing text", file=sys.stderr)
        return 2
    pieces = line_pieces(text)
    if len(pieces) > 1:
        return speak_pieces(pieces, sys.argv[1:])
    _LINE["chars"] = len(text)
    _CUT["text"] = text                  # a cut keeps the line as asked, whole (cut_note)

    global _AIR_TEXT, _AIR_WHO
    # Must be armed before any mouth opens: a name-interrupt killpg's this
    # process group, and the default handler would skip every `finally`.
    try:
        signal.signal(signal.SIGTERM, _air_sigterm)
        signal.signal(signal.SIGINT, _air_sigterm)
    except (ValueError, OSError):
        pass
    voice = VOICE_ALIASES.get(args.voice.strip().lower(), args.voice.strip().lower())
    # Mouth / aligner never sees [laugh] etc. — Turbo keeps tags only on the TTS line.
    if _SP is not None and hasattr(_SP, "strip_bracket_tags"):
        mouth_src, _k, _u = _SP.strip_bracket_tags(text)
    else:
        mouth_src = text
    _AIR_TEXT = cap_text(mouth_src, MOUTH_TEXT_CAP, "mouth")
    _AIR_WHO = _ROLL_WHO.get(voice, voice) or "crissa"
    # A BENCH SAY IS NOT THE HOUSE TALKING (2026-09-15). The Composer's bench
    # conductor spawns this with HOUSE_SPEAK_WHO_TAG=bench. The ear gate must
    # stay exactly as it is — speaking / deaf_until is how STT vetoes the echo,
    # and the room SHOULD go deaf while a tuning line plays out of the same
    # speakers — but the wall's caption is not the bench's business. So the
    # one writer keeps writing and the readers filter: who becomes
    # "crissa@bench" and the glass skips it.
    _tag = (os.environ.get("HOUSE_SPEAK_WHO_TAG") or "").strip().lower()
    if _tag:
        _AIR_WHO = "%s@%s" % (_AIR_WHO, _tag)
    _calm, _calm_src = resolve_calm(_AIR_WHO)
    if _calm > 0:
        os.environ["HOUSE_MOUTH_CALM_HOPS"] = str(_calm)
        print("calm merge %d hops - %s" % (_calm, _calm_src), file=sys.stderr, flush=True)
    global PLAY_TARGET_DB, PLAY_ALLOW_CUT
    if PLAY_MATCH_LOUD:
        PLAY_TARGET_DB, PLAY_ALLOW_CUT = play_target_for(_AIR_WHO)
    if not args.force:
        blocked = _tool_hold_blocks(voice)
        if blocked:
            print(f"skipped tool_hold voice={voice} ({blocked})", flush=True)
            return 0
    try:
        sink = resolve_sink(args.sink)
    except Exception as e:  # noqa: BLE001 — crissa_audio.SinkNotFound, shim on only
        if type(e).__name__ != "SinkNotFound":
            raise
        # Before the duck, the lock and any air stamp: nothing has moved.
        print("refused sink: %s" % e, flush=True)
        return 3
    try:
        _duck = Ducked(sink, int(float(os.environ.get("HOUSE_SPEAK_DUCK_PCT", "0") or 0)))
        _duck.__enter__()
        import atexit
        atexit.register(_duck.__exit__, None, None, None)
    except Exception:  # noqa: BLE001
        pass
    engine = pick_engine(args.engine)
    # THE LOG NAMES THE ENGINE THAT SPEAKS (2026-10-06, crissatest: Kokoro spoke and the line named the streamed voice).
    # The seam decides first; only the house's own path keeps pick_engine's choice.
    tts_cfg = _crissa_tts_cfg() if not args.wav else None
    if tts_cfg is not None:
        engine = str(tts_cfg.get("engine") or "none")
    _LINE["engine"] = _STREAMED["name"] or engine
    print(f"voice={voice} sink={sink} engine={_LINE['engine']}", flush=True)
    min_pcm = int(os.environ.get("HOUSE_SPEAK_MIN_PCM", "12000"))
    if args.wav:
        # the pacer car: the harvest's own voice through the same clock
        try:
            dec = subprocess.run(
                ["ffmpeg", "-loglevel", "quiet", "-i", str(args.wav), "-vn", "-f", "s16le",
                 "-ar", "24000", "-ac", "1", "pipe:1"], capture_output=True, timeout=120)
            pcm = dec.stdout
        except Exception as e:  # noqa: BLE001
            print(f"wav decode fail: {e}", file=sys.stderr)
            return 2
        if len(pcm) < min_pcm:
            print("wav decode fail: short", file=sys.stderr)
            return 2
        # the drill honours HOUSE_SPEAK_TEMPO (tempo x rate from the conductor,
        # which paces the film the same): stretch once here, clock at 1
        pcm = stretch_pcm(pcm, 24000, TEMPO)
        if args.words:
            global _GIVEN_WORDS
            _GIVEN_WORDS = timeline_words_heard(args.words, TEMPO)
            if not _GIVEN_WORDS:
                print("drill: --words %s has no usable words — the aligner stands in" % args.words,
                      file=sys.stderr, flush=True)
        # THE FILM'S OWN VOICE IS NOT LEVELLED (the User 2026-09-18 pm: "the volume boost hurt my
        # ears"): the drill plays the harvest exactly as the film plays it — measured, the
        # boost had lifted harvest-talk-back 7.5 dB (−25.8 → −18.3) with its peaks crushed to
        # −1 dB by the limiter. What the User heard from the clip is what the drill says.
        # Kill HOUSE_SPEAK_DRILL_MATCH_LOUD=1 restores the levelling on this path.
        if os.environ.get("HOUSE_SPEAK_DRILL_MATCH_LOUD", "0").strip().lower() in ("1", "true", "on", "yes"):
            pcm = boost_play_pcm(pcm, 24000)[0]
        elif os.environ.get("HOUSE_SPEAK_WAV_LEVEL", "0").strip().lower() in ("1", "true", "on", "yes"):
            # round 40 N22: a line of HER voice rendered ahead (her opener), not a film: levelled as her live lines are
            pcm = boost_play_pcm(pcm, 24000)[0]
        # ...BUT A FILM LOUDER THAN THE VOICE BAND MAY BE CUT DOWN TO IT (the User 2026-10-03, "say textbox not the clip
        # voice is so much softer ... can barely hear it"): the -26 band was read off clip-talk-back-harvest, the one film
        # at -26; every other take in both libraries sits at -11 to -14 median, 12-15 dB over a levelled Say. A cut
        # crushes no peak and hurts no ear. HOUSE_SPEAK_FILM_TO_BAND=1 (a bench drop-in; off = the film as it is).
        if os.environ.get("HOUSE_SPEAK_FILM_TO_BAND", "0").strip().lower() in ("1", "true", "on", "yes"):
            from loudness import apply_gain, gain_to
            _g = gain_to(pcm, 24000, PLAY_TARGET_DB, allow_cut=True)
            if _g < 1.0:
                pcm = apply_gain(pcm, _g)
                print("film track cut %.1f dB to the voice band" % (20.0 * math.log10(_g)), file=sys.stderr, flush=True)
        out = args.out or Path(tempfile.mkstemp(suffix=".wav", prefix="house-drill-")[1])
        pcm_to_wav(pcm, out, sr=24000, tempo=1.0)
        paplay(out, sink, pcm=pcm, sr=24000, clock_tempo=1.0,
               align_wait_s=float(os.environ.get("HOUSE_ALIGN_WAIT_BUFFERED_S", "8")))
        print(f"ok drill bytes={len(pcm)} sr=24000 src={args.wav} clock={_CLOCK_HOW or 'letter clock (no alignment landed)'}", flush=True)
        return 0
    turbo = voice_is_turbo()
    spoken = normalize_for_voice(text, keep_turbo_tags=turbo)
    # backups and the announcer are never Turbo: they get the line with no [tag] (else they say "laugh")
    spoken_plain = normalize_for_voice(text, keep_turbo_tags=False) if turbo else spoken
    if turbo and _SP is not None and hasattr(_SP, "classify_bracket_tags"):
        known, _unk = _SP.classify_bracket_tags(text or "")
        if known:
            print(
                "turbo-tags for TTS: %s" % ", ".join("[%s]" % k for k in known),
                file=sys.stderr,
                flush=True,
            )

    def _play(pcm: bytes, sr: int, tag: str, note: str = "") -> int:
        pcm = boost_play_pcm(pcm, sr)[0]
        out = args.out or Path(tempfile.mkstemp(suffix=".wav", prefix="house-speak-")[1])
        if pack_plays_heard():
            pcm = play_heard(pcm, sr, out, sink, keep=bool(args.out))
            extra = f" note={note}" if note else ""
            print(f"ok {tag} bytes={len(pcm)} sr={sr} out={out}{extra}", flush=True)
            return 0
        applied = pcm_to_wav(pcm, out, sr=sr)
        paplay(out, sink, pcm=pcm, sr=sr, clock_tempo=applied)
        extra = f" note={note}" if note else ""
        print(f"ok {tag} bytes={len(pcm)} sr={sr} out={out}{extra}", flush=True)
        return 0

    def _play_chunks(chunks, sr: int, tag: str) -> int:
        """Play PCM chunks as they arrive (Audio8's stream, the User 2026-10-08): one raw paplay on the sink, the
        first chunk is the first sound. Where the page plays the sound, the chunks are gathered and played whole."""
        if pack_plays_heard():
            return _play(b"".join(chunks), sr, tag)
        player = subprocess.Popen(
            ["paplay", f"--device={sink}", "--raw", f"--rate={sr}", "--channels=1", "--format=s16le",
             "--latency-msec=100"], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        total, t0, first_at = 0, time.time(), None
        try:
            for chunk in chunks:
                if not chunk:
                    continue
                if first_at is None:
                    first_at = time.time()
                player.stdin.write(boost_play_pcm(chunk, sr)[0])
                player.stdin.flush()
                total += len(chunk)
        finally:
            try:
                player.stdin.close()
            except OSError:
                pass
            player.wait(timeout=120)
        if first_at is None:
            print(f"tts fail (audio8): the stream carried no audio", file=sys.stderr, flush=True)
            return 1
        print(f"ok {tag} bytes={total} sr={sr} first_sound_ms={int((first_at - t0) * 1000)}", flush=True)
        return 0

    def _try_house(name: str) -> int | None:
        """This machine's own engine (lib/house_voices) says the line; None when it cannot."""
        fn = _house_engine(name)
        if fn is None:
            return None
        try:
            pcm, sr = fn(spoken_plain, voice, cap_text)
        except Exception as e:
            print(f"{name} fail: {e}", flush=True)
            return None
        if len(pcm) < min_pcm:
            print(f"{name} fail: short ({len(pcm)} bytes)", flush=True)
            return None
        return _play(pcm, sr, name)

    def _try_backup() -> int | None:
        """The main voice did not speak: the machine's named backup does, and the log says which.

        A pack's streamed voice asks its tier's WHOLE backup chain in order (ship review 2026-10-07: Turbo's
        [pocket, kokoro, say] was cut to Pocket alone, so a pack without Pocket fell silent with `say` right there)."""
        chain = list(_STREAMED.get("backup_chain") or []) if _STREAMED["name"] else []
        if not chain or os.environ.get("HOUSE_SPEAK_BACKUP"):
            chain = [BACKUP] if BACKUP else []
        if not chain:
            return None
        if len(chain) == 1 and not _STREAMED["name"] and _house_engine(chain[0]) is not None:
            return _try_house(chain[0])           # a machine's own backup, as before
        main = _main_name()                    # a pack names its streamed voice (Turbo, Pocket); else this machine's
        for b in chain:
            print(f"{main} miss — backup {b} speaks", flush=True)
            try:
                rc = (_try_house(b) if _house_engine(b) is not None
                      else _try_crissa_tts(spoken_plain, voice, _play, min_pcm, {"engine": b}))
            except Exception as e:  # noqa: BLE001  made but not played (2026-10-07): the next backup, and the log says why
                print(f"backup {b} fail: {e}", flush=True)
                rc = 1
            if rc != 0:
                continue
            print(f"voice: {main.capitalize()} did not speak this line; {b} spoke it", file=sys.stderr, flush=True)
            if _STREAMED["name"]:
                try:                              # on screen too: the status mark says who stood in and for whom
                    import crissa_tts
                    crissa_tts._note_spoke(b, main, "%s did not speak this line" % main)
                except Exception:  # noqa: BLE001
                    pass
            return 0
        return None

    # THE VOICE SEAM (2026-09-28): a named engine (CRISSA_TTS, or the `tts` block of
    # <crissa home>/agent.json) speaks the line and hands its PCM to _play, the same
    # buffered route a backup takes — MouthAir builds her mouth from whatever audio it
    # gets. Unset (the house) never imports the module: its streamed voice -> backup as before.
    tts_rc = _try_crissa_tts(spoken, voice, _play, min_pcm, tts_cfg, play_chunks=_play_chunks) if tts_cfg is not None else None
    if tts_rc is not None:
        return tts_rc

    if ANNOUNCER and ANNOUNCER not in (MAIN, "voice") and voice in ("announcer",):
        # the machine's announcer voice says the announcer's lines (a pack on a streamed voice: Kokoro); the house names none
        print(f"announcer line — {ANNOUNCER}", flush=True)
        chain = _STREAMED.get("announcer_chain") or []
        if not _HOUSE and chain:
            # a pack (2026-10-06): the announcer's own order through the seam (Pocket, Kokoro, say)
            rc = _try_crissa_tts(spoken_plain, voice, _play, min_pcm, {"engine": chain[0], "announcer_chain": chain})
        else:
            rc = (_try_house(ANNOUNCER) if _house_engine(ANNOUNCER) is not None
                  else _try_crissa_tts(spoken_plain, voice, _play, min_pcm, {"engine": ANNOUNCER}))
        if rc == 0:
            return rc
        print(f"announcer {ANNOUNCER} did not speak — {_main_name()}", flush=True)

    if engine != MAIN and _house_engine(engine) is not None:
        rc = _try_house(engine)
        if rc is not None:
            return rc
        print(f"{engine} miss — {_main_name()}", flush=True)

    try:
        # STREAMING playback (User 2026-08-04: 6-14s of buffered synthesis
        # read as "nothing" — first sound must land in ~1s). Chunks play as
        # The voice produces them; the lead-silence pad still wakes the sink.
        streamed = 0
        sr = 24000
        buf = b""
        stream_on = os.environ.get("HOUSE_SPEAK_STREAM", "1") != "0"
        _zs = _za() if stream_on else None
        if _zs is not None and not _zs.can_stream():
            # a Mac with no ffplay: afplay reads files, so the line is buffered whole (2026-09-28)
            stream_on = False
        if stream_on:
            streamed, sr, buf = voice_stream_play(spoken, voice, sink)
        if streamed >= min_pcm:
            out = args.out
            if out:
                pcm_to_wav(buf, out, sr=sr)
            print(f"ok {_main_name()}-stream bytes={streamed} sr={sr}"
                  + (f" out={out}" if out else ""), flush=True)
            return 0
        if pcm_is_playable(buf, min_pcm=min_pcm):
            # Stream path already downloaded a complete clip (skip-play leftover).
            # Play it — do not fight the voice a second time under a full GPU.
            return _play(buf, sr, _main_name())
        # short/failed stream → the robust buffered path (normalize + retry)
        pcm, sr, note = voice_pcm_robust(text, voice, keep_turbo_tags=turbo)
        if len(pcm) < min_pcm:
            raise RuntimeError(f"{_main_name()} returned short audio ({len(pcm)} bytes)")
        # THE ENVELOPE MUST BE THE AUDIO THE SINK PLAYS. This path is reached
        # only when the stream FAILED, so `buf` is the short/empty leftover that
        # failed `pcm_is_playable` — `pcm` is the good retry from
        # voice_pcm_robust. Passing buf wrote the right wav and then built her
        # mouth from the wrong bytes: MouthAir got a truncated envelope, so on
        # every slow-voice turn her mouth barely moved while the full line played.
        # The wav and the envelope have to come from the same buffer.
        return _play(pcm, sr, _main_name(), note)
    except Exception as e:
        print(f"{_main_name()} fail: {e}", flush=True)
        why, made = why_failed(e)
        if engine == MAIN or engine != BACKUP:      # a backup already tried first is not asked twice
            rc = _try_backup()
            if rc is not None:
                return rc
        if not args.ref_fallback:
            backups = " -> ".join(_STREAMED.get("backup_chain") or []) or BACKUP or "none"
            if made:
                print(
                    "no speech engine answered (voice %s, backup %s): %s. Start them, name another engine "
                    "(CRISSA_TTS), or pass --ref-fallback for ref-wav only." % (
                        VOICE_URL or "not configured", backups, why),
                    file=sys.stderr,
                )
            else:
                print(
                    "she could not be heard: %s (her voice: %s at %s; backup %s)" % (
                        why, _main_name().capitalize(),
                        (VOICE_URL or "not configured").rsplit("/stream", 1)[0].replace("http://", ""), backups),
                    file=sys.stderr,
                )
            return 1
        # ref fallback — audible identity, not the requested text.
        # The voice's own ref, then Crissa's; the house adds its own order (lib/house_voices).
        candidates = (_HV.ref_candidates(voice, REFS) if _HV is not None
                      else [REFS / f"{voice}.wav", REFS / "crissa.wav"])
        wav = next((p for p in candidates if p.is_file()), None)
        if not wav:
            print("no ref wav", file=sys.stderr)
            return 1
        paplay(wav, sink)
        print(f"ok ref-fallback {wav} (text NOT spoken)", flush=True)
        return 0


def _line_end(rc, ended: str) -> None:
    if _LINE.get("_ended"):
        return
    _LINE["_ended"] = True
    t0 = _LINE.get("_t0")
    _line_log("end", rc=rc, ended=ended, clock=_CLOCK_HOW or "letter",
              ms=round((time.time() - t0) * 1000) if t0 else None)


def _logged_main() -> int:
    _LINE["_t0"] = time.time()
    _LINE["by"] = _parent_name()
    _LINE["who_tag"] = os.environ.get("HOUSE_SPEAK_WHO_TAG") or None
    argv = sys.argv[1:]
    _LINE["voice"] = argv[argv.index("--voice") + 1] if "--voice" in argv[:-1] else None
    _LINE["drill"] = "--wav" in argv
    _line_log("start")
    try:
        rc = main()
    except SystemExit as e:
        _line_end(e.code if isinstance(e.code, int) else 1, "exit")
        raise
    except BaseException as e:
        _line_end(1, "exception %s" % type(e).__name__)
        raise
    _line_end(rc, "return")
    return rc


if __name__ == "__main__":
    raise SystemExit(_logged_main())
