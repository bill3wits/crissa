"""The voice seam: any speech engine hands house-speak PCM, and MouthAir does the rest (2026-09-28).

WHY. house-speak was two things in one file: MouthAir (the air clock that turns
the audio into her mouth sheet — it must travel with the player) and ~350 lines
that fetch the audio from the house's GPU voice services, which no
pack carries. So a pack could move her mouth and say nothing. Nothing in the
mouth needs the house's GPU voice: `schedule()` pins the sheet to the voiced energy of whatever
PCM it is handed, and the word clock (rail-align when it answers, else the
letter clock) reads the same audio. This module is the swappable half.

ONE CHOOSER (2026-10-06): with no engine named, a pack's voice is Init's machine-capability pick
(lib/crissa_voice_tiers) and nothing else. Every stand-in is said on screen (voice-state.json, the status mark)
and in the log, and voice-state.json is rewritten for every line so a recovered voice clears the mark.
QUIET FALLBACK (2026-10-01, OFF: `CRISSA_TTS_FALLBACK=1` or `"fallback": true` in the tts block): only behind an
engine the person NAMED: when it does not answer, macOS `say`, else Piper where `piper_model`
(CRISSA_TTS_PIPER_MODEL) names a voice, speaks the line, said on screen and in the log like any stand-in. Off, a
named engine's error stands.

ENGINES (`CRISSA_TTS`, else the `tts` block of <crissa home>/agent.json):
  openai  any OpenAI-compatible POST {url}/audio/speech, response_format pcm
          (24 kHz s16le mono) — a hosted API or a local server
  say     macOS `say` (built in, offline)
  piper   Piper on the CPU (Linux/macOS, offline; `model` = the .onnx)
  kokoro  the voice module's Kokoro-82M server on loopback (modules/voice/scripts/crissa-tts.py, no
          espeak), spoken to as an OpenAI speech endpoint; each face's voice from its manifest's
          `tts.kokoro`.
  turbo   the voice module's Chatterbox-Turbo on loopback (modules/voice/scripts/crissa-turbo.py), the GPU voice;
          the house's /stream contract (house-speak streams it through the same path the house's own voice uses;
          synth() here takes the whole line), in ~80-character sentence chunks. Kokoro is its announcer.
  multilingual  the voice module's Chatterbox Multilingual V3 (modules/voice/scripts/crissa-multilingual.py): the
          optional GPU voice for a language other than English, the same /stream contract as Turbo.
  pocket  the voice module's Pocket TTS on the CPU (modules/voice/scripts/crissa-pocket.py), stock voices only;
          the same /stream contract, ~80-character chunks streamed frame by frame.
  unset   a pack: THE VOICE TIER (2026-10-06, the User: chosen by what the machine can handle; lib/crissa_voice_tiers).
          Init's pick and its chain, best first, each engine only when READY: Turbo and Pocket when their
          server answers (kill CRISSA_TTS_DEFAULT_TURBO=0, _POCKET=0), Kokoro when its server answers (kill CRISSA_TTS_DEFAULT_KOKORO=0), `say` on a
          Mac (kill CRISSA_TTS_DEFAULT_SAY=0), Piper when CRISSA_TTS_PIPER_MODEL names a voice, else {} - nothing
          speaks and house-speak says so (text only). Every engine passed over is named (cfg["passed"], printed by
          house-speak and kept in voice-state.json). The house: its own path, byte for byte (its house config names
          its own voices: tts_main, tts_backup and their urls); any engine name that is not one of ENGINES is the
          house's own and takes that path.

Config keys (env beats file): CRISSA_TTS, CRISSA_TTS_URL, CRISSA_TTS_KEY,
CRISSA_TTS_MODEL, CRISSA_TTS_VOICE. The file's block may also carry
`voices: {"crissa": "nova", "lila": "shimmer"}` so each face keeps its own voice;
the speaker name is used as the voice only by an engine that knows it.

house-speak is exec'd from disk for every line: on the house an unset CRISSA_TTS never imports anything
here; a pack always asks config().
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crissa_egress  # noqa: E402

try:
    from crissa_home import HOME
except ImportError:  # imported by path
    HOME = Path(os.environ.get("CRISSA_HOME") or (Path.home() / ".crissa")).expanduser()

ENGINES = ("openai", "say", "piper", "kokoro", "audio8", "multilingual", "turbo", "pocket")
# The voice module's streamed voices: the house's /stream contract, each on its own loopback port and script.
STREAMED = {"multilingual": ("Chatterbox Multilingual", "crissa-multilingual.py", 8185),
            "turbo": ("Chatterbox-Turbo", "crissa-turbo.py", 8186), "pocket": ("Pocket", "crissa-pocket.py", 8188)}
HOUSE_PATH = ("", "house", "off", "0")   # names that mean "not this seam": the house's own path, or nothing
ANNOUNCERS = ("announcer",)     # speakers the tier's announcer voice (Kokoro behind the GPU voice) says
OPENAI_SR = 24000               # response_format=pcm is 24 kHz s16le mono by the API's contract


class TTSError(RuntimeError):
    pass


def _platform() -> str:
    return sys.platform


def mac_default(env: dict | None = None) -> bool:
    """A Mac outside the house speaks with its own voice when nothing names one (the User 2026-10-04: the
    README's launch line was silent). Never the house (it is not a Mac, and is_house() is True there).
    Kill CRISSA_TTS_DEFAULT_SAY=0."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_TTS_DEFAULT_SAY", "1")).strip() == "0" or _platform() != "darwin":
        return False
    try:
        import crissa_home
        if crissa_home.is_house():
            return False
    except ImportError:
        pass
    return bool(shutil.which("say"))


def _house() -> bool:
    try:
        import crissa_home
        return crissa_home.is_house()
    except ImportError:
        return False


def kokoro_url(env: dict | None = None) -> str:
    """The voice module's own Kokoro server (modules/voice/scripts/crissa-tts.py): loopback, its port from the
    shared port table when the pack has one, else CRISSA_PORT_TTS / CRISSA_TTS_PORT, else 8189."""
    e = os.environ if env is None else env
    try:
        import crissa_ports
        return "http://127.0.0.1:%d/v1" % crissa_ports.port("tts", e)
    except (ImportError, KeyError, AttributeError):
        pass
    for k in ("CRISSA_PORT_TTS", "CRISSA_TTS_PORT"):
        v = str(e.get(k) or "").strip()
        if v.isdigit():
            return "http://127.0.0.1:%s/v1" % v
    return "http://127.0.0.1:%d/v1" % (8189 + int(str(e.get("CRISSA_PORT_OFFSET") or "0") or 0))


def kokoro_up(env: dict | None = None, timeout: float = 0.3) -> bool:
    try:
        with urllib.request.urlopen(kokoro_url(env)[:-len("/v1")] + "/health", timeout=timeout) as r:
            return bool(json.loads(r.read() or b"{}").get("ok"))
    except (OSError, ValueError):
        return False


def audio8_url(env: dict | None = None) -> str:
    """The voice module's Audio8 server (modules/voice/scripts/crissa-audio8.py): loopback, port 8187 by default."""
    e = os.environ if env is None else env
    try:
        import crissa_ports
        return "http://127.0.0.1:%d" % crissa_ports.port("audio8", e)
    except Exception:  # noqa: BLE001
        return "http://127.0.0.1:%s" % (str(e.get("CRISSA_PORT_AUDIO8") or "8187").strip())


def audio8_up(env: dict | None = None, timeout: float = 0.5, url: str | None = None) -> bool:
    """Audio8 answers /api/health ok and holds the pack's announcer voice."""
    base = (url or audio8_url(env)).rstrip("/")
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=timeout) as r:
            if not json.loads(r.read() or b"{}").get("ok"):
                return False
        with urllib.request.urlopen(base + "/api/voices", timeout=timeout) as r:
            names = {str(v.get("name")) for v in (json.loads(r.read() or b"{}").get("voices") or [])}
        return bool(names & (set(audio8_voices()) | {"anna"}))
    except (OSError, ValueError):
        return False


def _tiers_table() -> dict:
    try:
        import crissa_voice_tiers
        return crissa_voice_tiers.table()
    except Exception:  # noqa: BLE001
        return {"tiers": {}, "announcer": {}}


def audio8_voices(today=None) -> list[str]:
    """Today's Audio8 announcer voice first, then the other one (the User 2026-10-07: the house's alternating
    announcer, every other day, kept in a pack with voices it can ship). The tier table's announcer.audio8: `voice`
    (A), `voice_b` (B, empty = A every day), `alternate` "day" (A on even days by date.toordinal(), B on odd ones,
    the house's own rule) or "off". A name the voice module does not register never reaches the server (the
    installer registers pack voices only), and a refused B falls back to A in _audio8."""
    from datetime import date
    cfg = (_tiers_table().get("announcer") or {}).get("audio8") or {}
    a = str(cfg.get("voice") or "anna").strip().lower()
    b = str(cfg.get("voice_b") or "").strip().lower()
    if not b or b == a or str(cfg.get("alternate") or "day").strip().lower() != "day":
        return [a]
    d = today or date.today()
    return [a, b] if d.toordinal() % 2 == 0 else [b, a]


def audio8_voice(today=None) -> str:
    """The Audio8 announcer voice for today (audio8_voices; default anna)."""
    return audio8_voices(today)[0]


AUDIO8_FACES = {"lila": "lila_imagine", "crissa": "crissa_imagine"}   # the faces Audio8 registers besides the announcer
# CRISSA'S OWN VOICE (the User 2026-10-08): on Audio8 she is crissa_imagine, registered from her clip that ships in the pack
# (modules/voice/faces/crissa-imagine.wav, cut from her Grok Imagine films' sound; "Created with Grok"). She was jane
# (VCTK p339) from 2026-10-07; jane is registered too and speaks for her whenever her own voice is not there.
# LILA'S OWN VOICE (the User 2026-10-12) is lila_imagine the same way (modules/voice/faces/lila-imagine.wav, a Grok
# Imagine film's sound); she was eve (VCTK p361), which is her backup now.
AUDIO8_BACKUP = {"crissa": "jane", "lila": "eve"}

# CRISSA IS NEVER A MAN'S VOICE (2026-10-07, .101: she spoke in a man's voice). Her Pocket voice was alba, which measured in
# a man's pitch range (modules/voice/crissa_voice/pocket_setup.py FEMALE says how); she is eve on Pocket (the User
# 2026-10-09). Audio8 speaks her own clip, and jane is that clip's backup. A female face (SHE_FACES) is held to a
# woman's voice on every engine whatever names one for her (a saved map, the tts block, a face manifest): she_voice()
# swaps a man's voice for her own and says so in the log.
# The announcer's own voices (Kokoro bm_george, the Mac's Daniel) stay the announcer's; they never reach her lines.
SHE_FACES = ("crissa", "lila", "default")
SHE_DEFAULT = {"kokoro": {"crissa": "af_heart", "lila": "bf_emma"}, "say": {"crissa": "Samantha", "lila": "Karen"},
               "audio8": AUDIO8_FACES}
AUDIO8_WOMEN = ("anna", "eve", "jane", "vctk_p283", "crissa_imagine", "lila_imagine")   # audio8_setup PACK_VOICES: the
# VCTK women (p228, p361, p339, p283) and Crissa's and Lila's own
# The Mac's own English women, best first, for a face whose own say voice is not installed (the Mac's default voice
# may be a man's); and the Mac's men, never hers.
SAY_WOMEN = ("Samantha", "Karen", "Moira", "Tessa", "Victoria", "Allison", "Ava", "Susan", "Fiona", "Kate", "Serena",
             "Veena", "Zoe")
SAY_MEN = ("Daniel", "Alex", "Fred", "Tom", "Oliver", "Aaron", "Arthur", "Gordon", "Rishi", "Reed", "Eddy", "Ralph",
           "Albert", "Bruce", "Junior", "Rocko", "Grandpa", "Lee", "Evan", "Nathan", "Thomas", "Jorge", "Diego",
           "Juan", "Luca", "Xander", "Yuri", "Maged")


def she_voice(engine: str, speaker: str, voice: str) -> str:
    """`voice` for this face on `engine`, unless the face is a woman's (SHE_FACES) and `voice` is a man's: then her own
    voice on that engine (SHE_DEFAULT), with a line in the log. Anyone else's voice is returned as it is."""
    who = "crissa" if speaker == "default" else speaker
    if speaker not in SHE_FACES or not voice:
        return voice
    man = ((engine == "kokoro" and len(voice) > 1 and voice[1].lower() == "m")
           or (engine == "say" and voice in SAY_MEN)
           or (engine == "audio8" and voice not in AUDIO8_WOMEN))
    if not man:
        return voice
    mine = SHE_DEFAULT.get(engine, {}).get(who, "")
    print("voice: %s is not a woman's voice on %s; %s speaks for %s" % (voice, engine, mine or "her own", who),
          file=sys.stderr, flush=True)
    return mine


def face_default(engine: str, speaker: str) -> str:
    """A face's voice on an engine from the tier table: settings.voices for the engine; for the announcer on Pocket,
    the stand-in for the Audio8 voice (announcer.pocket: anna -> anna); on Kokoro and say, announcer.kokoro / .say."""
    tab = _tiers_table()
    ann = tab.get("announcer") or {}
    if speaker in ANNOUNCERS:
        if engine == "audio8":
            return audio8_voice()
        if engine == "pocket":
            m = ann.get("pocket") or {}
            if isinstance(m, dict) and m.get(audio8_voice()):
                return str(m[audio8_voice()])
        if engine in ("kokoro", "say") and isinstance(ann.get(engine), str):
            return str(ann[engine])
    if engine == "audio8":
        return AUDIO8_FACES.get(speaker, audio8_voice())
    v = (((tab.get("tiers") or {}).get(engine) or {}).get("settings") or {}).get("voices") or {}
    return str(v.get(speaker) or "") if isinstance(v, dict) else ""


def kokoro_default(env: dict | None = None) -> bool:
    """Kokoro speaks a pack's lines when nothing names an engine and its server answers (it answers only when the
    person installed it: the launcher starts it then). ON since 2026-10-06 (crissatest: with no voice named a Linux
    install fell through to the house's GPU ports; the order is Kokoro, the platform voice, text). Kill
    CRISSA_TTS_DEFAULT_KOKORO=0. Never the house. Otherwise the next default decides."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_TTS_DEFAULT_KOKORO", "1")).strip().lower() in ("0", "off", "false", "no"):
        return False
    try:
        import crissa_home
        if crissa_home.is_house():
            return False
    except ImportError:
        pass
    return kokoro_up(e)


def stream_url(name: str, env: dict | None = None) -> str:
    """A streamed voice's server (STREAMED): loopback, the port table's entry for it (CRISSA_PORT_<NAME>, else its
    default + CRISSA_PORT_OFFSET). The /stream address house-speak's streamed path speaks to."""
    e = os.environ if env is None else env
    try:
        import crissa_ports
        return "http://127.0.0.1:%d/stream" % crissa_ports.port(name, e)
    except (ImportError, KeyError, AttributeError):
        pass
    v = str(e.get("CRISSA_PORT_" + name.upper()) or "").strip()
    port = int(v) if v.isdigit() else STREAMED[name][2] + int(str(e.get("CRISSA_PORT_OFFSET") or "0") or 0)
    return "http://127.0.0.1:%d/stream" % port


def stream_up(name: str, env: dict | None = None, timeout: float = 0.3, url: str | None = None) -> bool:
    """The server answers /health with ok and something to speak with: a voice clip, or its own built-in voice
    (Turbo with no clip reports voices [] and builtin_voice true; GPU run 2026-10-06)."""
    base = (url or stream_url(name, env)).rsplit("/stream", 1)[0]
    try:
        with urllib.request.urlopen(base + "/health", timeout=timeout) as r:
            d = json.loads(r.read() or b"{}")
            return bool(d.get("ok")) and (bool(d.get("voices")) or bool(d.get("builtin_voice")))
    except (OSError, ValueError):
        return False


def _default_on(e: dict, key: str) -> bool:
    return str(e.get(key, "1")).strip().lower() not in ("0", "off", "false", "no")


def _not_voice(name: str) -> bool:
    """Audio8 never speaks in a pack (crissa_voice_tiers.not_a_voice: the User 2026-10-17, it is her ear, not a voice)."""
    try:
        import crissa_voice_tiers
        return bool(crissa_voice_tiers.not_a_voice(name))
    except Exception:  # noqa: BLE001  without the tier module: the rule still holds in a pack
        return str(name or "").strip().lower() == "audio8" and not _house() and \
            str(os.environ.get("CRISSA_AUDIO8_TTS", "0")).strip().lower() not in ("1", "on", "true", "yes")


def _tier_record(home: Path | None = None) -> dict | None:
    try:
        import crissa_voice_tiers
        return crissa_voice_tiers.clean_record(crissa_voice_tiers.read(home or HOME))
    except Exception:  # noqa: BLE001  a missing or broken tier module is "no pick yet"
        return None


def _tier_chain(rec: dict | None) -> list[str]:
    try:
        import crissa_voice_tiers
        return with_installed(crissa_voice_tiers.chain(rec))
    except Exception:  # noqa: BLE001
        return [c for c in with_installed(["multilingual", "turbo", "pocket", "kokoro", "say", "piper", "text"])
                if not _not_voice(c)]


# WHAT IS INSTALLED STANDS IN TOO (2026-10-07, .101: Pocket was Crissa's only voice, Audio8 was installed and serving as the
# announcer, and her backup was a fixed list - Kokoro, never installed there - so a missed line fell to nothing).
# Crissa's chain and every streamed voice's backup now carry the voices this machine actually has that Init's pick did
# not name: Audio8 (installed as the announcer on a robust CPU machine) goes right after the streamed voices (Pocket
# first, then Audio8), before the later fallbacks (Kokoro, say, Piper, text). Nothing is dropped or reordered; an
# engine that is not installed is not added. Kill CRISSA_TTS_INSTALLED_BACKUP=0.
INSTALLED_BACKUPS = ("audio8",)


def _installed_here(name: str) -> bool:
    try:
        import crissa_voice_tiers
        return bool(crissa_voice_tiers.installed(name))
    except Exception:  # noqa: BLE001  a missing or broken module: not installed
        return False


def with_installed(seq: list[str], main: str = "", env: dict | None = None, installed=None) -> list[str]:
    """`seq` plus each installed voice in INSTALLED_BACKUPS it does not hold yet (never `main`), placed after the last
    streamed voice in it, else first. The house is never changed (its own config names its voices)."""
    e = os.environ if env is None else env
    out = [str(x) for x in (seq or []) if not _not_voice(str(x))]      # Audio8 is her ear, never a backup voice
    if str(e.get("CRISSA_TTS_INSTALLED_BACKUP", "1")).strip().lower() in ("0", "off", "false", "no") or _house():
        return out
    have = installed or _installed_here
    for name in INSTALLED_BACKUPS:
        if name in out or name == main or _not_voice(name) or not have(name):
            continue
        at = max((i + 1 for i, c in enumerate(out) if c in STREAMED), default=0)
        out.insert(at, name)
    return out


def face_voice(speaker: str, engine: str) -> str:
    """The face's own voice for an engine, from avatars/<face>/manifest.json `tts` (card t_1169441e). ''
    when the face or the key is absent; never raises (a line never fails on a manifest)."""
    try:
        import avatar
        block = avatar.load(speaker).get("tts") or {}
        return str(block.get(engine) or "").strip() if isinstance(block, dict) else ""
    except Exception:  # noqa: BLE001
        return ""


def config(env: dict | None = None, home: Path | None = None, demand: bool = False) -> dict:
    """The engine and its settings; {} when none is named (house-speak keeps its own path). `demand`: this is a line
    about to be spoken (house-speak), so a held engine the walk falls back to is started for it (round 17,
    lib/crissa_voice_demand); a status read leaves it False and starts nothing."""
    e = os.environ if env is None else env
    try:
        block = json.loads(((home or HOME) / "agent.json").read_text(encoding="utf-8")).get("tts") or {}
        if not isinstance(block, dict):
            block = {}
    except (OSError, ValueError, AttributeError):
        block = {}
    cfg = {k: block[k] for k in ("engine", "url", "key", "model", "voice", "voices", "fallback", "piper_model")
           if k in block}
    for k in ("url", "key", "model", "voice"):
        v = str(e.get("CRISSA_TTS_" + k.upper()) or "").strip()
        if v:
            cfg[k] = v
    engine = str(e.get("CRISSA_TTS") or cfg.get("engine") or "").strip().lower()
    if engine and _not_voice(engine):
        # a setting that names Audio8 as her voice (an old config): Audio8 is her ear now; the picked voice speaks
        got = _by_tier(cfg, e, home, demand)
        note = "%s: speech in only on this install, not a voice; the voice picked for this machine speaks" % engine
        if got:
            got["passed"] = [note] + list(got.get("passed") or [])
        return got or {"engine": "", "passed": [note]}
    if engine in STREAMED and not _house():
        # a pack that named a streamed voice: its own module server (or the url it named); the chain behind it
        rec = _tier_record(home) or {}
        cfg.update(engine=engine, url=str(cfg.get("url") or stream_url(engine, e)))
        cfg.setdefault("backup", _backup_of(engine, rec))
        if engine != "pocket":
            cfg.setdefault("announcer", "pocket")     # Init's announcer voice; Kokoro speaks only when named
        return cfg
    if engine and engine not in ENGINES and engine not in HOUSE_PATH and not _house():
        # a pack naming a voice it does not have (an old config, another install's name): Init's pick speaks, and
        # the screen and the log say why, in words (no setting names)
        got = _by_tier(cfg, e, home, demand)
        note = "%s: not one of this install's voices; the voice picked for this machine speaks instead" % engine
        if got:
            got["passed"] = [note] + list(got.get("passed") or [])
        return got or {"engine": "", "passed": [note]}
    if engine == "" and not _house():
        return _by_tier(cfg, e, home, demand)  # Init's pick is the only chooser (the quiet switch never overrides it)
    if engine in HOUSE_PATH or (engine not in ENGINES and _house()):     # the house's own names keep its path
        if engine == "" and fallback_on(cfg, e):
            # no engine named, the quiet-fallback switch on: this machine's own voice speaks
            own = fallback_engines(cfg, e)
            if own:
                cfg["engine"] = own[0]
                if own[0] == "piper":
                    cfg["model"] = _piper_model(cfg, e)
                return cfg
        return {}
    cfg["engine"] = engine
    return cfg


def _backup_of(main: str, rec: dict) -> list[str]:
    """The engines behind `main`: the tier record's chain after it, else the tier table's backup for it; either way
    with the voices installed here that neither names (with_installed: Audio8 after the streamed voices)."""
    chain = rec.get("chain") or []
    after = chain[chain.index(main) + 1:] if main in chain else chain
    after = [c for c in after if c in ENGINES and c != main]
    if after:
        return with_installed(after, main)
    try:                                       # no pick yet: the tier table's own backup for it, never a list here
        import crissa_voice_tiers
        return with_installed([b for b in (crissa_voice_tiers.table()["tiers"].get(main) or {}).get("backup") or []
                               if b in ENGINES], main)
    except Exception:  # noqa: BLE001
        return with_installed([], main)


def _expected(name: str, rec: dict | None) -> bool:
    """Is passing over this engine a fallback worth saying? Yes when Init picked it for this machine (it is in the
    kept chain), or, before any pick, when it is installed here. An engine this machine never had is not a fallback:
    the pick's own line already said what is missing and how to get it."""
    if rec:
        return name in (rec.get("chain") or [rec.get("main")])
    try:
        import crissa_voice_tiers
        return bool(crissa_voice_tiers.installed(name))
    except Exception:  # noqa: BLE001
        return False


def _said(passed: list, rec: dict | None) -> list:
    """The passed-over lines worth saying (each starts with its engine's name and a colon)."""
    return [p for p in passed if _expected(p.split(":", 1)[0], rec)]


def _lean(got: dict) -> dict:
    """Only what says something: no empty passed-over list, no backup list that is empty, and no tier_main when
    the main is the one speaking (the shape a pack had before the tiers, 2026-10-05)."""
    if not got.get("passed"):
        got.pop("passed", None)
    if not got.get("backup"):
        got.pop("backup", None)
    if got.get("tier_main") == got.get("engine"):
        got.pop("tier_main", None)
    return got


def _by_tier(cfg: dict, e: dict, home: Path | None = None, demand: bool = False) -> dict:
    """Nothing named, a pack: the first READY engine in the voice tier's chain (Init's pick, best first; no pick =
    the tier table's order). Every engine passed over is named in cfg["passed"]; the tier's main is in
    cfg["tier_main"], so a backup speaking is a fallback the status mark and the log can say."""
    got = _by_tier_walk(cfg, e, home, demand)
    rec = _tier_record(home) or {}
    if got and rec.get("announcer_chain") and not got.get("announcer_chain"):
        got["announcer_chain"] = list(rec["announcer_chain"])
    return _lean(got)


def _after(chain: list, name: str, backup: list) -> list:
    """A buffered main's backup: its chain, less the streamed voices already passed over above it. A streamed voice
    BELOW it stays (2026-10-07: on a robust CPU machine Pocket is Audio8's backup for Crissa, and a line Audio8 fails
    goes to Pocket, not straight to the later fallbacks)."""
    above = set(chain[:chain.index(name)]) if name in chain else set(STREAMED)
    return [b for b in backup if not (b in STREAMED and b in above)]


# A HELD ENGINE IS STARTED WHEN THE LADDER FALLS BACK TO IT (round 17, 2026-10-13; lib/crissa_voice_demand): on .101 three
# voice engines sat resident (Pocket 0.9 GB in use, Kokoro 1.1 GB and Audio8 1.8 GB idle). A backup no role uses is held
# by the launcher ("installed, not running"); the walk for a line (demand=True), synth()'s order and speak_audio8 start
# it through the launcher and wait for it, as a slow engine is waited for. Off, the house, or not held: nothing changes.
# Kill CRISSA_VOICE_ENGINES_ON_DEMAND=0.
def _start_held(name: str, e: dict | None = None, home: Path | None = None) -> bool:
    """True when `name` was held, has been started for this line and answers; False otherwise (never raises)."""
    try:
        import crissa_voice_demand
        return bool(crissa_voice_demand.ensure(name, home, e))
    except Exception:  # noqa: BLE001  an on-demand start never breaks a line: the ladder goes on
        return False


def _used(name: str, home: Path | None = None) -> bool:
    """A line is about to ask `name`: stamped BEFORE it is asked, so the launcher's idle stop never takes an engine a
    line just chose. Always False (it only stamps; the walk's own test decides). Off or the house: nothing."""
    try:
        import crissa_voice_demand
        if crissa_voice_demand.on():
            crissa_voice_demand.touch(name, home)
    except Exception:  # noqa: BLE001
        pass
    return False


def _by_tier_walk(cfg: dict, e: dict, home: Path | None = None, demand: bool = False) -> dict:
    rec = _tier_record(home)
    chain = _tier_chain(rec)
    main = str((rec or {}).get("main") or "")   # no pick yet: whatever speaks is the main
    passed = []
    for name in chain:
        if name in STREAMED:
            label = STREAMED[name][0]
            kill = "CRISSA_TTS_DEFAULT_" + name.upper()
            if not _default_on(e, kill):
                passed.append("%s: switched off on this machine" % name)    # the setting's name is in the log
            elif (demand and _used(name, home)) or stream_up(name, e) or (demand and _start_held(name, e, home)):
                got = dict(cfg, engine=name, url=stream_url(name, e), tier_main=main or name,
                           passed=_said(passed, rec), backup=_backup_of(name, rec or {}))
                ann = str((rec or {}).get("announcer") or "")
                if ann and ann != name:
                    got["announcer"] = ann
                if (rec or {}).get("announcer_chain"):
                    got["announcer_chain"] = list(rec["announcer_chain"])
                return got
            else:
                passed.append("%s: no %s server with a voice answers at %s (once installed, the launcher starts it)"
                              % (name, label, stream_url(name, e).rsplit("/stream", 1)[0]))
        elif name == "audio8":
            if (demand and _used("audio8", home)) or audio8_up(e) or (demand and _start_held("audio8", e, home)):
                got = dict(cfg, engine="audio8", url=audio8_url(e), tier_main=main or "audio8",
                           passed=_said(passed, rec), backup=_after(chain, "audio8", _backup_of("audio8", rec or {})))
                if (rec or {}).get("announcer_chain"):
                    got["announcer_chain"] = list(rec["announcer_chain"])
                return got
            passed.append("audio8: no Audio8 server answers at %s (once installed, the launcher starts it)" % audio8_url(e))
        elif name == "kokoro":
            if ((demand and _used("kokoro", home)) or kokoro_default(e)
                    or (demand and _default_on(e, "CRISSA_TTS_DEFAULT_KOKORO") and _start_held("kokoro", e, home))):
                return dict(cfg, engine="kokoro", tier_main=main or "kokoro", passed=_said(passed, rec),
                            backup=_after(chain, "kokoro", _backup_of("kokoro", rec or {})))
            passed.append("kokoro: no Kokoro server answers at %s%s" % (kokoro_url(e)[:-len("/v1")], "" if _default_on(
                e, "CRISSA_TTS_DEFAULT_KOKORO") else " (switched off on this machine)"))
        elif name == "say":
            if mac_default(e):
                return dict(cfg, engine="say", tier_main=main or "say", passed=_said(passed, rec))
            if _platform() == "darwin":
                passed.append("say: switched off on this machine")
        elif name == "piper":
            if _piper_model(cfg, e):
                return dict(cfg, engine="piper", model=_piper_model(cfg, e), tier_main=main or "piper", passed=_said(passed, rec))
        elif name == "text":
            break
    return {}


def voice_for(cfg: dict, speaker: str) -> str:
    """The engine's voice for this speaker: a per-speaker map, else the one voice, else ''."""
    voices = cfg.get("voices") if isinstance(cfg.get("voices"), dict) else {}
    return str(voices.get(speaker) or cfg.get("voice") or "").strip()


def wav_pcm(blob: bytes) -> tuple[bytes, int]:
    """A WAV's 16-bit PCM, reduced to mono (first channel), and its rate."""
    try:
        with wave.open(io.BytesIO(blob), "rb") as w:
            ch, width, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise TTSError("the engine did not return a WAV: %s" % exc) from exc
    if width != 2:
        raise TTSError("the engine returned %d-bit audio; 16-bit is needed" % (8 * width))
    if ch > 1:
        step = 2 * ch
        frames = b"".join(frames[i:i + 2] for i in range(0, len(frames), step))
    return frames, sr


def _openai(text: str, cfg: dict, speaker: str, timeout: float) -> tuple[bytes, int]:
    url = str(cfg.get("url") or "").strip().rstrip("/")
    if not url:
        raise TTSError("the openai voice needs its address in the voice settings (e.g. https://api.openai.com/v1)")
    if not url.endswith("/audio/speech"):
        url += "/audio/speech"
    body = {"input": text, "response_format": "pcm",
            "model": str(cfg.get("model") or "tts-1"),
            "voice": voice_for(cfg, speaker) or "alloy"}
    headers = {"Content-Type": "application/json"}
    if cfg.get("key"):
        headers["Authorization"] = "Bearer " + str(cfg["key"])
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with crissa_egress.urlopen(req, timeout, "the speech server") as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            data = r.read()
    except crissa_egress.Refused as exc:
        raise TTSError(str(exc)) from exc
    except urllib.error.HTTPError as exc:
        raise TTSError("the speech server refused: HTTP %d" % exc.code) from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise TTSError("cannot reach the speech server: %s" % getattr(exc, "reason", exc)) from exc
    if data[:4] == b"RIFF" or "wav" in ctype:
        return wav_pcm(data)       # a server that ignores response_format and sends a WAV
    return data, OPENAI_SR


def _kokoro(text: str, cfg: dict, speaker: str, timeout: float) -> tuple[bytes, int]:
    """The voice module's Kokoro server, spoken to as an OpenAI speech endpoint. The voice: the config's map or
    voice, else the face's manifest, else af_heart."""
    voice = voice_for(cfg, speaker) or face_default("kokoro", speaker) or face_voice(speaker, "kokoro") or "af_heart"
    voice = she_voice("kokoro", speaker, voice) or "af_heart"
    print("voice: kokoro speaks this line as %s (for %s)" % (voice, speaker), file=sys.stderr, flush=True)
    return _openai(text, {"url": str(cfg.get("url") or kokoro_url()), "model": "kokoro", "voice": voice},
                   speaker, timeout)


def _streamed(text: str, cfg: dict, speaker: str, timeout: float) -> tuple[bytes, int]:
    """The whole line from a streamed voice's /stream (house-speak streams it itself; this is for the machine
    check and anything else that wants the line at once). The voice: the config's map, else the face's own name."""
    url = str(cfg.get("url") or stream_url(cfg.get("engine") if cfg.get("engine") in STREAMED else "turbo")).strip()
    eng = cfg.get("engine") if cfg.get("engine") in STREAMED else ""
    body = json.dumps({"text": text, "voice": voice_for(cfg, speaker) or (face_default(eng, speaker) if
                                                                        speaker in ANNOUNCERS and eng == "pocket"
                                                                        else "") or speaker}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with crissa_egress.urlopen(req, timeout, "the voice server") as r:
            sr = int(r.headers.get("X-Sample-Rate") or 24000)
            if r.headers.get("X-Voice"):
                print("voice: %s speaks this line as %s (for %s)" % (eng or "the voice", r.headers.get("X-Voice"),
                                                                    speaker), file=sys.stderr, flush=True)
            data = r.read()
    except crissa_egress.Refused as exc:
        raise TTSError(str(exc)) from exc
    except urllib.error.HTTPError as exc:
        try:
            why = json.loads(exc.read() or b"{}").get("error") or ""
        except (ValueError, OSError):
            why = ""
        raise TTSError("the voice server refused: HTTP %d %s" % (exc.code, why)) from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise TTSError("cannot reach the voice server: %s" % getattr(exc, "reason", exc)) from exc
    return data, sr


def _audio8(text: str, cfg: dict, speaker: str, timeout: float) -> tuple[bytes, int]:
    """The voice module's Audio8: Crissa's own voice (crissa_imagine, else jane) and the announcer (anna) on a robust CPU machine (the User
    2026-10-07), Lila's (lila_imagine, else eve) when named. One server speaks every line, one request at a time. A buffered line. The announcer speaks with today's voice (audio8_voices); a day's voice the server refuses
    (not registered) falls back to voice A once."""
    base = str(cfg.get("url") or audio8_url()).rstrip("/")
    named = voice_for(cfg, speaker)
    voices = [named] if named else ([v for v in audio8_voices()] if speaker in ANNOUNCERS
                                    else [face_default("audio8", speaker)])
    voices = [she_voice("audio8", speaker, v) for v in voices]   # crissa and lila: a woman's voice only (their own)
    if speaker not in ANNOUNCERS:              # her own voice not registered here (a 4xx): her backup, jane / eve
        b = AUDIO8_BACKUP.get("crissa" if speaker == "default" else speaker)
        if b and b not in voices:
            voices.append(b)
    # Every word is spoken (2026-10-07): the old request cut the text at 800 characters and the audio at 512 tokens
    # (about 24 s: a 474-character line lost its last 4 s). A long line now goes in pieces at sentence ends, each
    # with room for all of its words; the pieces are joined, nothing is dropped.
    pieces = audio8_pieces(audio8_line(text, speaker), int(cfg.get("piece_chars") or AUDIO8_PIECE_CHARS))
    last: Exception | None = None
    for voice in voices:
        out, sr, refused = [], 0, False
        for piece in pieces:
            body = json.dumps({"text": piece, "voice_name": voice, "max_new_tokens": audio8_tokens(piece),
                               "temperature": 0.3}).encode()
            req = urllib.request.Request(base + "/api/tts", data=body, headers={"Content-Type": "application/json"},
                                         method="POST")
            try:
                with crissa_egress.urlopen(req, timeout, "the announcer") as r:
                    data = r.read()
            except crissa_egress.Refused as exc:
                raise TTSError(str(exc)) from exc
            except urllib.error.HTTPError as exc:
                last = TTSError("Audio8 refused: HTTP %d" % exc.code)
                if 400 <= exc.code < 500:
                    refused = True              # that voice is not registered here: the other one
                    break
                raise last from exc
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                raise TTSError("cannot reach Audio8: %s" % getattr(exc, "reason", exc)) from exc
            try:
                pcm, sr = wav_pcm(data)
            except Exception as exc:  # noqa: BLE001
                raise TTSError("Audio8 sent no WAV: %s" % exc) from exc
            out.append(pcm)
        if refused:
            continue
        print("voice: audio8 speaks this line as %s (for %s)" % (voice, speaker), file=sys.stderr, flush=True)
        return b"".join(out), sr
    raise last or TTSError("Audio8 has no voice to speak with")


# Audio8 streams (the User 2026-10-08): the line goes to /api/tts/stream whole, with no character cap of ours,
# and plays from its first chunk; /api/tts/cancel cuts it. Only when the stream fails before its first chunk does
# the line fall back to the house's own pieces: 72 characters a request (PA_PIECE_MAX_CHARS) under a 160-character
# line cap for the announcer (PA_MAX_CHARS), cut at sentence ends, every piece joined, nothing dropped. The 400-
# character split that stood here is gone. Audio8's server takes up to 1000 characters and 2048 new tokens a
# request (its service.py); it speaks about 1.3 tokens a character.
AUDIO8_PIECE_CHARS = 72
AUDIO8_LINE_CHARS = 160
AUDIO8_SERVER_CHARS = 1000
AUDIO8_SERVER_TOKENS = 2048
AUDIO8_STREAM_CHECK_S = float(os.environ.get("CRISSA_AUDIO8_STREAM_CHECK_S", "0.5"))   # audio AFTER the first chunk before judging
AUDIO8_STREAM_MIN_RATE = float(os.environ.get("CRISSA_AUDIO8_STREAM_MIN_RATE", "0.9"))   # steady audio seconds per second to stream


def _stream_note_path() -> Path:
    """Where the last measured stream rate is kept: <crissa home>/audio8-stream.json."""
    try:
        import crissa_home
        home = Path(crissa_home.HOME)
    except Exception:  # noqa: BLE001
        home = Path(os.path.expanduser(os.environ.get("CRISSA_HOME") or "~/.crissa"))
    return home / "audio8-stream.json"


def audio8_stream_note(rate: float | None = None) -> dict:
    """Read, or with `rate` write, the last measured stream rate (audio seconds per second) and its time."""
    p = _stream_note_path()
    if rate is not None:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"rate": round(float(rate), 3), "ts": int(__import__("time").time())}))
            os.replace(tmp, p)
        except OSError:
            pass
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def audio8_stream_wanted(speaker: str, env: dict | None = None) -> bool:
    """The announcer streams (the User 2026-10-08: Audio8 is the announcer's voice; it can stream); a spoken
    reply does not unless CRISSA_AUDIO8_STREAM=1 (0 = never). After a line measured under real time, the next
    announcer lines take the pieces until the note is cleared or the switch forces the stream."""
    e = os.environ if env is None else env
    v = str(e.get("CRISSA_AUDIO8_STREAM", "")).strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    if speaker not in ANNOUNCERS and not audio8_streams_replies():
        return False
    rate = audio8_stream_note().get("rate")
    return rate is None or float(rate) >= AUDIO8_STREAM_MIN_RATE


def audio8_streams_replies(home: Path | None = None) -> bool:
    """ROUND 23 (the User 2026-10-15, .101: Audio8 as her voice read "one sentence at a time"): the tier's own setting
    (settings.stream_replies, on in the table) streams her replies too. A record or table that cannot be read: off."""
    try:
        import crissa_voice_tiers
        return bool(crissa_voice_tiers.settings("audio8", home).get("stream_replies"))
    except Exception:  # noqa: BLE001
        return False
AUDIO8_STREAM_PATH = "/api/tts/stream"
AUDIO8_CANCEL_PATH = "/api/tts/cancel"
_AUDIO8_LIVE: dict = {"base": ""}        # the server a stream is open on, for cancel_audio8()


def audio8_line(text: str, speaker: str, cap: int = AUDIO8_LINE_CHARS) -> str:
    """The announcer's line under the house's 160-character PA cap (cut at a word); a spoken reply is never cut."""
    text = " ".join(str(text or "").split())
    if speaker not in ANNOUNCERS or len(text) <= cap:
        return text
    head = text[:cap]
    return (head.rsplit(" ", 1)[0] if " " in head else head).rstrip(",;:")


def _audio8_voices(cfg: dict, speaker: str) -> list[str]:
    named = voice_for(cfg, speaker)
    voices = [named] if named else ([v for v in audio8_voices()] if speaker in ANNOUNCERS
                                    else [face_default("audio8", speaker)])
    voices = [she_voice("audio8", speaker, v) for v in voices]
    if speaker not in ANNOUNCERS:
        b = AUDIO8_BACKUP.get("crissa" if speaker == "default" else speaker)
        if b and b not in voices:
            voices.append(b)
    return voices


def audio8_stream(text: str, cfg: dict, speaker: str, timeout: float = 90.0):
    """Generator: (pcm, sr) chunks of the line from Audio8's /api/tts/stream (NDJSON: start, audio_chunk...,
    complete | cancelled). The first chunk is the first sound. Raises TTSError before the first chunk when the
    server does not answer, refuses, or the stream is not NDJSON; after it, a cancelled or broken stream ends
    the generator with what was spoken. Tries each voice in turn on a 4xx (the voice is not registered here)."""
    import base64
    base = str(cfg.get("url") or audio8_url()).rstrip("/")
    text = " ".join(str(text or "").split())
    if not text:
        return
    last: Exception | None = None
    for voice in _audio8_voices(cfg, speaker):
        body = json.dumps({"text": text, "voice_name": voice, "max_new_tokens": audio8_tokens(text),
                           "temperature": 0.3}).encode()
        req = urllib.request.Request(base + AUDIO8_STREAM_PATH, data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            r = crissa_egress.urlopen(req, timeout, "the announcer")
        except crissa_egress.Refused as exc:
            raise TTSError(str(exc)) from exc
        except urllib.error.HTTPError as exc:
            last = TTSError("Audio8 refused the stream: HTTP %d" % exc.code)
            if 400 <= exc.code < 500 and exc.code != 404:
                continue                      # that voice is not registered here: the other one
            raise last from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise TTSError("cannot reach Audio8: %s" % getattr(exc, "reason", exc)) from exc
        _AUDIO8_LIVE["base"] = base
        sr, first, chunks = 0, True, 0
        try:
            with r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError as exc:
                        if first:
                            raise TTSError("Audio8 sent no NDJSON stream") from exc
                        return
                    kind = ev.get("event")
                    if kind == "start":
                        sr = int(ev.get("sample_rate") or 24000)
                    elif kind == "audio_chunk":
                        pcm = base64.b64decode(ev.get("pcm_b64") or "")
                        if pcm:
                            if first:
                                print("voice: audio8 streams this line as %s (for %s)" % (voice, speaker),
                                      file=sys.stderr, flush=True)
                                first = False
                            chunks += 1
                            yield pcm, sr or 24000
                    elif kind == "cancelled":
                        return
                    elif kind == "complete":
                        if chunks == 0:
                            raise TTSError("Audio8 streamed no audio")
                        return
                    elif kind == "error":
                        raise TTSError("Audio8 stream error: %s" % (ev.get("message") or ev.get("error") or "?"))
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            if first:
                raise TTSError("Audio8 stream broke before its first chunk: %s" % exc) from exc
            return
        finally:
            _AUDIO8_LIVE["base"] = ""
        return
    raise last or TTSError("Audio8 has no voice to speak with")


def cancel_audio8(cfg: dict | None = None, timeout: float = 2.0) -> bool:
    """Cut the stream that is open (a stop, a barge-in, a SIGTERM): POST /api/tts/cancel. False = nothing to cut."""
    base = _AUDIO8_LIVE.get("base") or str((cfg or {}).get("url") or "").rstrip("/")
    if not base:
        return False
    req = urllib.request.Request(base + AUDIO8_CANCEL_PATH, data=b"{}",
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - loopback
            return bool(json.loads(r.read() or b"{}").get("cancelled"))
    except Exception:  # noqa: BLE001
        return False


def audio8_tokens(piece: str) -> int:
    """Room for every word of the piece: twice its characters (it uses ~1.3) plus a lead, up to the server's limit."""
    return max(16, min(AUDIO8_SERVER_TOKENS, 64 + 2 * len(piece)))


def audio8_pieces(text: str, size: int = AUDIO8_PIECE_CHARS) -> list[str]:
    """The text in pieces of up to `size` characters, cut at sentence ends, then at commas, then between words. Joined
    with single spaces they are the whole text (a word longer than a piece is split inside): nothing is dropped."""
    size = max(40, min(int(size or AUDIO8_PIECE_CHARS), (AUDIO8_SERVER_TOKENS - 64) // 2, AUDIO8_SERVER_CHARS))
    text = " ".join(str(text or "").split())
    if len(text) <= size:
        return [text] if text else []

    def cut(chunk: str, marks: list[str]) -> list[str]:
        if len(chunk) <= size:
            return [chunk]
        if not marks:                                   # one word longer than a piece: split it, keep every letter
            return [chunk[i:i + size] for i in range(0, len(chunk), size)]
        parts = re.split(marks[0], chunk)
        out, cur = [], ""
        for part in (x for x in parts if x):
            for bit in cut(part, marks[1:]):
                if cur and len(cur) + 1 + len(bit) > size:
                    out.append(cur)
                    cur = bit
                else:
                    cur = (cur + " " + bit) if cur else bit
        if cur:
            out.append(cur)
        return out

    return cut(text, [r"(?<=[.!?\u2026])\s+", r"(?<=[,;:])\s+", r"\s+"])


def audio8_sentences(text: str) -> list[str]:
    """Play units are sentences. A comma stays inside its sentence. `_audio8` still joins every piece
    and returns one wav; house-speak uses these units so the first sentence plays while the next renders."""
    text = " ".join(str(text or "").split())
    if not text:
        return []
    return [p for p in re.split(r"(?<=[.!?\u2026])\s+", text) if p]


def audio8_play_units(text: str, size: int = AUDIO8_PIECE_CHARS) -> list[str]:
    """One entry per sentence, split again only when a sentence is past the piece cap."""
    out = []
    for sent in audio8_sentences(text):
        out.extend(audio8_pieces(sent, size) or [sent])
    return out


def play_while_rendering(pieces, synth_one, play_one):
    """Play each piece while the next one renders.

    The first synth runs on this thread. The next synth is submitted before that piece's play returns,
    on one worker, so Audio8 still sees one request at a time. A play return other than 0 stops the line
    and cancels the synth that has not started."""
    from concurrent.futures import ThreadPoolExecutor
    pieces = [p for p in (pieces or []) if p]
    if not pieces:
        return 0
    pool = ThreadPoolExecutor(max_workers=1)
    pending = None
    rc = 0
    try:
        for i, piece in enumerate(pieces):
            got = synth_one(piece) if pending is None else pending.result()
            pcm, sr = got[0], got[1]
            if not pcm:
                raise TTSError("Audio8 returned no audio for a sentence")
            pending = pool.submit(synth_one, pieces[i + 1]) if i + 1 < len(pieces) else None
            played = play_one(pcm, sr)
            if isinstance(played, int) and played != 0:
                return played
            if isinstance(played, int):
                rc = played
        return rc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def speak_audio8(text, speaker, cfg, play, min_pcm, play_chunks=None, timeout: float = 90.0):
    """Audio8 speaks the line (the User 2026-10-08): the stream first, playing from its first chunk through
    `play_chunks(chunks, sr, tag)` (house-speak's raw player); when the stream fails before any sound, or no chunk
    player is given, the house's pieces: 72 characters a request, the announcer under 160, the first piece playing
    while the next renders. rc 0 spoken."""
    cfg = cfg or {}
    _start_held("audio8")                      # held (round 17): started for this line and waited for; else nothing
    # The announcer streams (the User 2026-10-08). The pack's own server (modules/voice/crissa_voice/audio8_serve.py)
    # pipelines the AR and the codec: measured on the reference box through HTTP, first sound 0.9 s and 0.92-0.98x
    # real time in the steady state (upstream's server: 0.31x). The head below (the first chunk + CHECK_S of audio
    # after it) is the prebuffer that covers that few percent over a long line, and the rate is judged on the audio
    # AFTER the first chunk (the first chunk's wait is start-up, not throughput). A stream under MIN_RATE is never
    # cancelled: it is read to its end, played whole, and its rate noted so the next announcer lines take the
    # pieces (first sound ~4 s). CRISSA_AUDIO8_STREAM=1 streams a reply too; 0 streams nothing.
    if play_chunks is not None and audio8_stream_wanted(speaker):
        import time as _time
        gen = audio8_stream(text, cfg, speaker, timeout)
        head: list = []
        sr = 24000
        t0 = _time.time()
        t_first = None
        first_len = 0
        try:
            for pcm, sr in gen:
                if t_first is None:
                    t_first, first_len = _time.time(), len(pcm)
                head.append(pcm)
                if (sum(len(x) for x in head) - first_len) >= 2 * sr * AUDIO8_STREAM_CHECK_S:
                    break
        except TTSError as e:
            print("voice: audio8 stream did not start (%s); pieces instead" % e, file=sys.stderr, flush=True)
            head, gen = [], None
        if head:
            after_first = (sum(len(x) for x in head) - first_len) / (2.0 * sr)
            if len(head) > 1:
                rate = after_first / max(1e-6, _time.time() - t_first)
            else:                                     # the whole line came in one chunk: as fast as it gets
                rate = (first_len / (2.0 * sr)) / max(1e-6, _time.time() - t0)
            audio8_stream_note(rate)
            if rate >= AUDIO8_STREAM_MIN_RATE:
                def chain():
                    for x in head:
                        yield x
                    for pcm, _sr in gen:
                        yield pcm
                rc = play_chunks(chain(), sr, "tts-audio8-stream")
                return rc if isinstance(rc, int) else 0
            print("voice: audio8 stream runs at %.2fx real time here; this line plays whole, the next take the pieces"
                  % rate, file=sys.stderr, flush=True)
            rest = list(head)
            for pcm, _sr in gen:          # read to the end: a cancel wedges the engine, a close leaves it running
                rest.append(pcm)
            whole = b"".join(rest)
            if len(whole) < int(min_pcm):
                raise TTSError("short (%d bytes)" % len(whole))
            played = play(whole, sr, "tts-audio8-stream-whole")
            return played if isinstance(played, int) else 0
    return speak_audio8_sentences(text, speaker, cfg, play, min_pcm, timeout)


def speak_audio8_sentences(text, speaker, cfg, play, min_pcm, timeout: float = 90.0):
    """The pieces path: the first sentence plays while later sentences render. One sentence stays one synth and
    one play, and that single play still honours min_pcm. A short sentence inside a longer line is played anyway."""
    cfg = cfg or {}
    text = audio8_line(text, speaker)
    units = audio8_play_units(text, int(cfg.get("piece_chars") or AUDIO8_PIECE_CHARS))
    if len(units) <= 1:
        pcm, sr = synth(text, speaker, cfg, timeout)
        if len(pcm) < int(min_pcm):
            raise TTSError("short (%d bytes)" % len(pcm))
        played = play(pcm, sr, "tts-audio8")
        return played if isinstance(played, int) else 0

    def synth_one(piece):
        pcm, sr = synth(piece, speaker, cfg, timeout)
        if not pcm:
            raise TTSError("Audio8 returned no audio for a sentence")
        return pcm, sr

    return play_while_rendering(units, synth_one, lambda pcm, sr: play(pcm, sr, "tts-audio8"))


def _run_to_wav(cmd: list[str], text_on_stdin: str | None, out: Path, timeout: float) -> tuple[bytes, int]:
    try:
        subprocess.run(cmd, input=(text_on_stdin.encode() if text_on_stdin is not None else None),
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout, check=True)
    except FileNotFoundError as exc:
        raise TTSError("%s is not installed" % cmd[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise TTSError("%s took longer than %.0f s" % (Path(cmd[0]).name, timeout)) from exc
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise TTSError("%s failed: %s" % (Path(cmd[0]).name, err[-1] if err else exc.returncode)) from exc
    try:
        return wav_pcm(out.read_bytes())
    except OSError as exc:
        raise TTSError("%s wrote no audio" % Path(cmd[0]).name) from exc


def _say(text: str, cfg: dict, speaker: str, timeout: float) -> tuple[bytes, int]:
    exe = shutil.which("say")
    if not exe:
        raise TTSError("say is macOS only")
    with tempfile.TemporaryDirectory(prefix="crissa-say-") as d:
        out = Path(d) / "line.wav"
        cmd = [exe, "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@24000"]
        v = she_voice("say", speaker, voice_for(cfg, speaker) or face_default("say", speaker))
        if v and v not in say_voices():
            # a face's voice that is not installed: the Mac's default, except that a woman's face takes the first
            # English woman's voice the Mac has (its default may be a man's)
            v = next((w for w in SAY_WOMEN if w in say_voices()), "") if speaker in SHE_FACES else ""
        if v:
            cmd += ["-v", v]
        print("voice: say speaks this line as %s (for %s)" % (v or "the Mac's default voice", speaker),
              file=sys.stderr, flush=True)
        cmd += ["--", text]
        return _run_to_wav(cmd, None, out, timeout)


_SAY_VOICES: list = []


def say_voices() -> dict:
    """{voice name: locale} from `say -v ?` (macOS); {} elsewhere. Read once."""
    if not _SAY_VOICES:
        out = {}
        exe = shutil.which("say")
        if exe:
            try:
                r = subprocess.run([exe, "-v", "?"], capture_output=True, text=True, timeout=10)
                for line in (r.stdout or "").splitlines():
                    parts = line.split("#")[0].split()
                    if len(parts) >= 2:
                        out[" ".join(parts[:-1])] = parts[-1]
            except (OSError, subprocess.SubprocessError):
                pass
        _SAY_VOICES.append(out)
    return _SAY_VOICES[0]


def _piper(text: str, cfg: dict, speaker: str, timeout: float) -> tuple[bytes, int]:
    exe = shutil.which("piper") or str(Path.home() / ".local" / "piper" / "piper")
    model = str(cfg.get("model") or "").strip()
    if not model or not Path(model).expanduser().is_file():
        raise TTSError("piper needs a voice model (a .onnx file) named in the voice settings")
    with tempfile.TemporaryDirectory(prefix="crissa-piper-") as d:
        out = Path(d) / "line.wav"
        cmd = [exe, "--model", str(Path(model).expanduser()), "--output_file", str(out)]
        return _run_to_wav(cmd, text, out, timeout)


HOUSE_SR = 24000                # every line the mouth was tuned on was the house voice's 24 kHz


def to_house_rate(pcm: bytes, sr: int) -> bytes:
    """16-bit mono PCM at `sr` -> 24 kHz. ffmpeg when it is installed (it is a
    player requirement), else linear interpolation in the standard library."""
    if sr <= 0:
        raise TTSError("sample rate %s is not a spoken line" % sr)
    if sr == HOUSE_SR or not pcm:
        return pcm
    ff = shutil.which("ffmpeg")
    if ff:
        try:
            out = subprocess.run([ff, "-v", "error", "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
                                  "-f", "s16le", "-ar", str(HOUSE_SR), "-ac", "1", "pipe:1"],
                                 input=pcm, capture_output=True, timeout=60, check=True).stdout
            if out:
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    import array
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    n = len(a)
    m = max(1, int(n * HOUSE_SR / sr))
    out = array.array("h", bytes(2 * m))
    step = (n - 1) / max(1, m - 1)
    for i in range(m):
        x = i * step
        j = int(x)
        f = x - j
        nxt = a[j + 1] if j + 1 < n else a[j]
        out[i] = int(a[j] + (nxt - a[j]) * f)
    return out.tobytes()


def synth(text: str, speaker: str, cfg: dict | None = None, timeout: float = 90.0) -> tuple[bytes, int]:
    """(16-bit mono PCM at 24 kHz, 24000) for one line. Raises TTSError with a short reason (never the key)."""
    cfg = config() if cfg is None else cfg
    engine = cfg.get("engine")
    if engine not in ENGINES:
        raise TTSError("unknown speech engine %r (one of %s)" % (engine, ", ".join(ENGINES)))
    fns = {"openai": _openai, "say": _say, "piper": _piper, "kokoro": _kokoro, "audio8": _audio8,
           "multilingual": _streamed, "turbo": _streamed, "pocket": _streamed}
    order = [engine]
    ann = str(cfg.get("announcer") or "")
    if speaker in ANNOUNCERS and cfg.get("announcer_chain"):
        # the announcer's own order (Audio8, Pocket, Kokoro, say), then Crissa's chain
        order = [a for a in cfg["announcer_chain"] if a in fns] + [engine]
        ann = order[0]
    elif speaker in ANNOUNCERS and ann in fns and ann != engine:
        order = [ann, engine]                  # the tier's announcer says the announcer's lines
    order += [b for b in (cfg.get("backup") or []) if b in fns and b not in order]   # the tier's backup chain
    order += [e for e in fallback_engines(cfg) if e not in order]
    order = [n for n in order if not _not_voice(n)]                 # Audio8 never speaks in a pack (her ear only)
    if not order:
        raise TTSError("%s is speech in only on this install, not a voice" % engine)
    first: TTSError | None = None
    for name in order:
        try:
            _start_held(name)                  # held (round 17): started for this line and waited for; else nothing
            pcm, sr = fns[name](text, _fallback_cfg(cfg, name) if name != engine else cfg, speaker, timeout)
            if sr <= 0:
                raise TTSError("the %s engine returned sample rate %s — not a spoken line" % (name, sr))
            if len(pcm) < 2 * sr // 10:
                raise TTSError("the %s engine returned %d bytes — not a spoken line" % (name, len(pcm)))
        except TTSError as exc:
            first = first or exc
            continue
        if cfg.get("engine"):               # every line: a recovered voice clears the status mark
            _note_spoke(name, str(cfg.get("tier_main") or engine), first, cfg.get("passed"))
            if name != engine and name != (ann if speaker in ANNOUNCERS else None):
                print("voice: %s did not speak (%s); %s spoke instead" % (engine, first, name),
                      file=sys.stderr, flush=True)
        return to_house_rate(pcm, sr), HOUSE_SR
    raise first  # type: ignore[misc]


def fallback_on(cfg: dict | None = None, env: dict | None = None) -> bool:
    """Quiet fallback (2026-10-01): OFF unless CRISSA_TTS_FALLBACK=1 or the tts block says fallback: true."""
    e = os.environ if env is None else env
    v = str(e.get("CRISSA_TTS_FALLBACK") or "").strip().lower()
    if v:
        return v in ("1", "on", "true", "yes")
    return bool((cfg or {}).get("fallback") is True)


def fallback_engines(cfg: dict, env: dict | None = None) -> list[str]:
    """The on-device engines this machine can fall back to when the named one does not answer:
    `say` where it exists (macOS), Piper where a voice model is named (`piper_model`,
    CRISSA_TTS_PIPER_MODEL). [] when the switch is off: the named engine's error stands."""
    if not fallback_on(cfg, env):
        return []
    out = []
    if shutil.which("say"):
        out.append("say")
    if _piper_model(cfg, env):
        out.append("piper")
    return out


def _piper_model(cfg: dict, env: dict | None = None) -> str:
    e = os.environ if env is None else env
    m = str(e.get("CRISSA_TTS_PIPER_MODEL") or cfg.get("piper_model") or "").strip()
    return m if m and Path(m).expanduser().is_file() else ""


def _fallback_cfg(cfg: dict, name: str) -> dict:
    """A fallback engine never inherits the named engine's voice or model (they are another engine's names)."""
    out = {"engine": name}
    if name == "piper":
        out["model"] = _piper_model(cfg)
    if name in STREAMED:
        out["url"] = stream_url(name)
    if name == "audio8":
        out["url"] = audio8_url()
    return out


def note_choice(cfg: dict) -> None:
    """house-speak's streamed line never passes through synth(): it records the tier's choice here, every line, so a
    voice that recovers clears the status mark."""
    if cfg and cfg.get("engine"):
        _note_spoke(str(cfg.get("engine")), str(cfg.get("tier_main") or cfg.get("engine")), None, cfg.get("passed"))


VOICE_STATE = HOME / "voice-state.json"


def _note_spoke(spoke: str, named: str, why: TTSError | None, passed: list | None = None) -> None:
    """Which engine spoke the last line, for the status mark. Best effort; never the key, never raises.
    `passed`: the tier's engines passed over before this one, each with why (lib/crissa_voice_tiers)."""
    try:
        tmp = VOICE_STATE.with_name(VOICE_STATE.name + ".tmp")
        tmp.write_text(json.dumps({"spoke": spoke, "named": named, "fallback": spoke != named,
                                   "why": str(why) if (why and spoke != named) else "",
                                   "passed": [str(x) for x in (passed or [])]}), encoding="utf-8")
        os.replace(tmp, VOICE_STATE)
    except OSError:
        pass
