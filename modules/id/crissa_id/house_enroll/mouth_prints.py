"""HER OWN VOICE, AS THIS ROOM HEARS IT (the User 2026-10-13, .101 round 15: "record her voice through the user's
speakers"; the User's lines were dropped as hers, `said_it_herself` / `on_air_not_user`, and the ear had no print of her
voice as this room's microphone hears it from this room's speakers).

During Setup, after the machine scan has picked her voice and Your room has the speaker answer, Crissa (her tier voice)
and the announcer each say a few short lines out loud while the microphone records; each line becomes one WeSpeaker
print, saved where the ear's own-voice check reads them (crissa_ear/stamp.py `_load_tts_fingerprints`):

    <state home>/ear/mouths/crissa_her_tts/voice/setup_<time>_<n>.json     her lines
    <state home>/ear/mouths/announcer/voice/setup_<time>_<n>.json    the announcer's

Each print is written by the ear's own writer, `crissa_ear.her_prints.write_print` (round16-ear): <mouths>/<id>/voice/
<id>-<ms>.json with person_id, role "tts_filter", the embedding, dim, the model, and the `context` it was made in (her
voice tier, the announcer, the speaker), so the veto takes it on its next turn and skips it, with `ask: true` in
<home>/ear/her-prints.json, once the tier or the speaker changes. The embedding is the coach's own encoder, which is the
ear's: the same wespeaker_onnx.py, byte for byte, on the same pinned model file under the state home. An id module
installed without the ear writes the same record itself, with no context (a print that never goes stale). Nothing of
the person is kept, and no audio (the takes are deleted once embedded). A run sets aside the earlier prints Setup made,
and any print stale against the new context, under voice/replaced-<time>/ (the veto reads voice/*.json only), so
`ask` clears once she has been recorded again. A line too quiet to be heard (the speakers off, the volume down) is no
print, and the run says so. Kill: CRISSA_SETUP_MOUTHS=0 (Init never starts it).

The same six lines also time this machine's speakers for her lip sync (round 17, the User 2026-10-13: "yes have setup
measure the lip sync delay"; see LIP SYNC FROM THESE LINES): nothing more is played.

Setup's end (round 18) asks for one voice at a time with its own words, and says her lines through the player so her
mouth moves on them: see THE TEAM INTRODUCES ITSELF.
"""
from __future__ import annotations

import array
import importlib
import inspect
import json
import os
import shutil
import sys
import threading
import types
import time
import wave
from pathlib import Path
from typing import Any

from . import cues
from .coach import coach_emit
from .voice_session import (
    embed_wav,
    live_encoder_meta,
    peak_pcm,
    record_fixed,
    release_ear_for_enroll,
    reopen_ear_after_enroll,
    voiced_fraction,
)

LINES = {
    "crissa_her_tts": ("crissa", ("Hi, it's Crissa. This is how I sound in your room.",
                          "Now I'll know my own voice when I hear it.",
                          "One more line, and that's this part done.")),
    "announcer": ("announcer", ("This is the announcer's voice.",
                                "Reminders and announcements sound like this.",
                                "That's all for this part.")),
}
MIN_ACTIVE = 0.20          # a line the microphone barely heard is no print
MIN_PEAK = 400
LEAD_S = 0.25              # the recording starts a beat before the line plays
TAIL_S = 0.6               # and keeps a beat after it (the room's own tail)


SETUP_LABEL = "setup_mouths"

# THE MICROPHONE THE PRINTS ARE RECORDED ON (round 17, .101 2026-10-13 on round 16: six lines played and none was recorded,
# "site.audio.mic is empty - set in site.json", so `her_prints` stayed n 0, ask true). A fresh install's site.json names
# no microphone; Voice ID records through room_mic_for (site.json enroll.mic, the desk hint, audio.mic, and in a pack the
# ear's own microphone, else the default input), and this step called record_fixed, which reads site.mic alone. It takes
# the same answer now, RAW: never the echo canceller's source (crissa_ear/aec.py NODE_SOURCE), which would cancel the
# very voice being printed - an answer that is the canceller (by name, or the default input when the canceller is the
# default) becomes the microphone under it (<state home>/aec.json `mic`), and with none to be found nothing is recorded
# and the run says why. The house never comes here (Init's mouths are a pack's) and keeps site.mic.
# Kill: CRISSA_MOUTHS_ROOM_MIC=0 (site.mic alone, as round 16).
AEC_SOURCE = "crissa_aec_source"
DEFAULT_SOURCE = "@DEFAULT_SOURCE@"


def room_mic_on() -> bool:
    return os.environ.get("CRISSA_MOUTHS_ROOM_MIC", "1").strip().lower() not in ("0", "false", "no", "off")


def _is_house() -> bool:
    try:
        from .. import paths as _p
        return bool(_p.is_house())
    except Exception:  # noqa: BLE001
        return False


def _cancels(name: str) -> bool:
    n = (name or "").strip().lower()
    return bool(n) and (n == AEC_SOURCE or "echocancel" in n or "echo-cancel" in n or "echo_cancel" in n)


def _aec_mic() -> str:
    """The microphone under the ear's echo canceller (<state home>/aec.json `mic`), or ''."""
    home = cues._state_home()
    if home is None:
        return ""
    try:
        d = json.loads((Path(home) / "aec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    mic = str((d or {}).get("mic") or "").strip() if isinstance(d, dict) else ""
    return "" if (not mic or _cancels(mic) or mic == "page" or mic.endswith(".monitor")) else mic


def _default_source() -> str:
    try:
        import subprocess
        out = subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True, timeout=3).stdout
    except Exception:  # noqa: BLE001
        return ""
    return (out or "").strip()


def raw_mic(name: str, *, aec_mic=None, default_source=None) -> str:
    """`name` as the microphone itself: the canceller's source (or the default input when the canceller is the default)
    becomes the microphone under it, '' when that is not known."""
    name = (name or "").strip()
    real = name
    if name == DEFAULT_SOURCE:
        real = (default_source or _default_source)() or name
    if not _cancels(real):
        return name
    return (aec_mic or _aec_mic)()


def print_mic(site, *, room_mic_for=None) -> str:
    """The microphone the prints are recorded on (see THE MICROPHONE THE PRINTS ARE RECORDED ON)."""
    mic = str(getattr(site, "mic", "") or "")
    if not room_mic_on() or _is_house():
        return mic
    if not mic:
        if room_mic_for is None:
            from . import voice_register as vr
            room_mic_for = vr.room_mic_for
        try:
            mic = str(room_mic_for(site, None, "desk") or "")
        except Exception as e:  # noqa: BLE001
            print("  mouths: no microphone answer (%s)" % repr(e)[:120], flush=True)
            mic = ""
    got = raw_mic(mic)
    if mic and got != mic:
        print("  mouths: %s cancels her voice; recording on the microphone under it, %s" % (mic, got or "(unknown)"),
              flush=True)
    return got


# LIP SYNC FROM THESE LINES (round 17, the User 2026-10-13: "yes have setup measure the lip sync delay"). A fresh
# install's Lip sync slider sits at 0; .101 chose -125 ms by eye in round 14 for its Razer speakers through the echo
# canceller's sink, where the sheet assumes the house's 90 ms from write to ear. Each of the six lines is already played
# and recorded here, so each is timed too: the recorder's start (record_fixed `started`) and the play call (cues
# _paplay_pcm `on_play`, with the exact samples sent) on one clock, the line found in its recording, and the slider set
# from the lines that agree, the way scripts/mouth-air-latency.py measured the house (lib/crissa_lipsync: line_delay,
# settle, from_setup). While timing, the lines play on the sink her voice plays on (lib/audio_sink.speak_sink: the
# canceller's sink in front of her speaker when the ear runs one), the same speaker, so the canceller's own delay is in
# the number. A value the person set by hand is kept and the measurement only logged; too few clear lines, or a step
# that does not run, leave the slider as it is and the log says so. Never on the house.
# Kill: CRISSA_SETUP_LIPSYNC_MEASURE=0 (round 17's lines, sink and log, unchanged).
_LS: dict[str, Any] = {"tried": False, "mod": None}


def lip_measure_on() -> bool:
    return os.environ.get("CRISSA_SETUP_LIPSYNC_MEASURE", "1").strip().lower() not in ("0", "false", "no", "off")


def _lib_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "lib"      # the install root's lib/ (a pack keeps this layout)


def lipsync():
    """The player's own lip sync (lib/crissa_lipsync), or None. lib/ goes on the END of sys.path, so nothing of this
    module's own is ever shadowed by it."""
    if _LS["tried"]:
        return _LS["mod"]
    _LS["tried"] = True
    mod = sys.modules.get("crissa_lipsync")
    if mod is None:
        lib = _lib_dir()
        if (lib / "crissa_lipsync.py").is_file() and str(lib) not in sys.path:
            sys.path.append(str(lib))
        try:
            mod = importlib.import_module("crissa_lipsync")
        except Exception as e:  # noqa: BLE001
            print("  lip sync: the player's lip sync did not load (%s)" % repr(e)[:120], flush=True)
            mod = None
    _LS["mod"] = mod
    return mod


def _takes(fn, name: str) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _lip_timing() -> bool:
    """Can these lines be timed here: on, a pack, and a recorder and player that report their start."""
    return (lip_measure_on() and not _is_house() and _takes(record_fixed, "started")
            and _takes(cues._paplay_pcm, "on_play"))


# HER PLAYER LINES ARE TIMED TOO (round 20, the User 2026-10-14: "just ship two voices" - with no announcer there is no
# plain voice left to time, and Crissa's introduction is said through the player so her lips move). The player stamps
# each line's air time (/v1/state `say_air_at`: when house-speak's sheet puts her first sample in the room, the write
# plus HOUSE_MOUTH_LATENCY_MS); the line is timed against that stamp less the same latency, which is the play call a
# plain line is timed from, so the slider means what it meant. A player that does not say its stamp: not timed, as
# before. Kill: CRISSA_SETUP_LIPSYNC_PLAYER=0 (only plain lines are timed).
_AIR: dict = {"before": None, "at": None}


def player_timing_on() -> bool:
    return (lip_measure_on() and not _is_house()
            and os.environ.get("CRISSA_SETUP_LIPSYNC_PLAYER", "1").strip().lower() not in ("0", "off", "false", "no"))


def _lip_line_player(wav: Path, started: list, pcm: bytes, sr: int, air_at) -> dict:
    """One player line's timing: the player's air stamp less her mouth's own latency is its play call."""
    LS = lipsync()
    if LS is None or not started or not air_at:
        return {"ok": False, "why": "not timed (the player gave no air time)"}
    try:
        play_mono = float(air_at) - (time.time() - time.monotonic()) - float(LS.assumed_ms()) / 1000.0
    except (TypeError, ValueError):
        return {"ok": False, "why": "not timed (an unreadable air time)"}
    return _lip_line(wav, started, {"pcm": pcm, "sr": int(sr), "t": play_mono})


def _her_sink(site) -> str | None:
    """The sink her voice plays on (lib/audio_sink.speak_sink, the order house-speak keeps), else site.sink."""
    want = getattr(site, "sink", None)
    try:
        lib = _lib_dir()
        if str(lib) not in sys.path:
            sys.path.append(str(lib))
        from audio_sink import speak_sink
        got = str(speak_sink(want) or "")
    except Exception as e:  # noqa: BLE001
        print("  lip sync: her voice's sink was not found (%s); timing on %s" % (repr(e)[:100], want or "the default"),
              flush=True)
        return want
    if got and got != want:
        print("  lip sync: the lines play on %s, the sink her voice plays on" % got, flush=True)
    return got or want


def _lip_line(wav: Path, started: list, played: dict) -> dict:
    """One line's timing (lib/crissa_lipsync.line_delay), read from its take before the take is deleted."""
    LS = lipsync()
    if LS is None or not started or not played:
        return {"ok": False, "why": "not timed"}
    try:
        with wave.open(str(wav), "rb") as w:
            rate, frames = w.getframerate(), w.readframes(w.getnframes())
        return LS.line_delay(played["pcm"], played["sr"], frames, rate, float(played["t"]) - float(started[0]))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": "not measured (%s)" % repr(e)[:100]}


def _lead_base() -> float | None:
    """The library's own lead as the running player has it (GET /v1/menu, lip_sync.base_ms), so the value lands on a
    step the slider reaches; None when the player does not answer (it still holds every line inside its clamp)."""
    try:
        import urllib.request

        import crissa_ports
        with urllib.request.urlopen(crissa_ports.url("player") + "/v1/menu", timeout=2.0) as r:
            v = json.loads(r.read().decode("utf-8", "replace") or "{}")
        return float(((v or {}).get("lip_sync") or {})["base_ms"])
    except Exception:  # noqa: BLE001
        return None


def _glass_lag() -> dict | None:
    """The player's glass lag now (GET /v1/glass-lag): the desk Wall's when it reports, else the freshest glass; None
    when no glass reports or the player does not answer. Noted beside the measurement (lib/crissa_lipsync THE GLASS,
    NOTED BESIDE THE EAR), counted in the value since round 26. A test never reads a live player."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    try:
        import urllib.request

        import crissa_ports
        with urllib.request.urlopen(crissa_ports.url("player") + "/v1/glass-lag", timeout=1.5) as r:
            g = (json.loads(r.read().decode("utf-8", "replace") or "{}") or {}).get("glass_lag") or {}
        fresh = {k: v for k, v in g.items() if isinstance(v, dict) and v.get("fresh") and v.get("median_ms") is not None}
        if not fresh:
            return None
        who = "wall-tk" if "wall-tk" in fresh else min(fresh, key=lambda k: float(fresh[k].get("age_s") or 0.0))
        return {"who": who, "median_ms": fresh[who]["median_ms"]}
    except Exception:  # noqa: BLE001
        return None


def _lip_sync_done(lines: list | None, why: str = "") -> dict[str, Any]:
    """Settle the lines, set (or keep, or leave) the slider, and say so once in this log and to the coach's listeners."""
    LS = lipsync()
    if LS is None:
        print("  lip sync: not measured (the player's lip sync is not here); the slider stays as it is", flush=True)
        return {"outcome": "none", "why": "no lip sync module"}
    home = cues._state_home()
    res = {"ok": False, "why": why} if why else LS.settle(lines or [])
    kw = {"glass": _glass_lag()} if res.get("ok") and _takes(LS.from_setup, "glass") else {}
    rep = LS.from_setup(res, home=Path(home) if home is not None else None,
                        base_ms=_lead_base() if res.get("ok") else None, **kw)
    print("  %s" % (rep.get("log") or "lip sync: %s" % rep.get("outcome")), flush=True)
    if rep.get("line"):
        coach_emit(type="lip_sync", text=rep["line"], outcome=rep.get("outcome"), lead_ms=rep.get("lead_ms"))
    return rep


_HP: dict[str, Any] = {"tried": False, "mod": None}
_LAST_MS = [0]


def her_prints():
    """The ear's print writer (modules/ear/crissa_ear/her_prints.py), or None when this machine has no ear module.
    Loaded from the ear's folder beside this module's (a pack keeps the source tree's modules/<name>/ layout) WITHOUT
    the ear package's __init__, which builds its speech engines; her_prints needs only its home.py beside it."""
    if _HP["tried"]:
        return _HP["mod"]
    _HP["tried"] = True
    mod = sys.modules.get("crissa_ear.her_prints")
    if mod is None:
        ear = Path(__file__).resolve().parents[3] / "ear" / "crissa_ear"
        if (ear / "her_prints.py").is_file():
            name = "_crissa_ear_prints"
            try:
                if name not in sys.modules:
                    pkg = types.ModuleType(name)
                    pkg.__path__ = [str(ear)]          # a package with the ear's files and none of its __init__
                    sys.modules[name] = pkg
                mod = importlib.import_module(name + ".her_prints")
            except Exception as e:  # noqa: BLE001
                print("  mouths: the ear's print writer did not load (%s); writing the prints here" % repr(e)[:120],
                      flush=True)
                mod = None
    _HP["mod"] = mod
    return mod


def mouths_root() -> Path:
    hp = her_prints()
    if hp is not None:
        return Path(hp.mouths_root())
    env = (os.environ.get("HOUSE_MOUTHS_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    home = cues._state_home()
    return (Path(home) if home is not None else Path.home() / ".crissa") / "ear" / "mouths"


def _write(pid: str, encoding: list, model: str, label: str, context: dict | None) -> Path:
    """One print through the ear's writer; the same record by hand without it (no context: it never goes stale)."""
    ms = int(time.time() * 1000)
    while ms <= _LAST_MS[0]:                   # a print's file is named by the millisecond: each gets its own
        ms = int(time.time() * 1000)
    hp = her_prints()
    if hp is not None:
        try:
            return Path(hp.write_print(pid, encoding, model=model, context=context, label=label))
        finally:
            _LAST_MS[0] = int(time.time() * 1000)     # at or past the millisecond it named the file by
    _LAST_MS[0] = ms
    d = mouths_root() / pid / "voice"
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%s-%d.json" % (pid, ms))
    vec = [float(x) for x in encoding]
    rec = {"person_id": pid, "role": "tts_filter", "encoding": vec, "dim": len(vec), "model": model,
           "label": label, "ts": round(time.time(), 3)}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def _context() -> dict | None:
    hp = her_prints()
    if hp is None:
        return None
    try:
        return dict(hp.context_now(fresh=True))
    except Exception:  # noqa: BLE001
        return None


# THE TEAM SPEAKS AT THE PACE OF SPEECH (round 19, the User 2026-10-14, .101 round 18: "Announcer needs to be faster
# on intro"; "seems halting when they speak with pauses between sentences"). Between two of a voice's lines the room
# heard the last take's 0.6 s tail, a 0.35 s sleep, the next take's 0.25 s lead, the next line's render (0.6-1.4 s on
# this machine's Pocket), the last line's print (the speaker model: seconds the first time) and the next line's own
# lead-in quiet (0.16-0.84 s): 2-3.5 s of silence between two sentences. Now a voice's lines are rendered in order on a
# thread of their own while the earlier ones are said (each line waits only for its own render), its lead-in quiet is
# cut to HEAD_KEEP_S (faded in), a plain take keeps PLAIN_TAIL_S of the room after it with no sleep, and the prints are
# made from the takes once the voice's last line is said (the takes are deleted after, as before). The lines, the prints
# and the lip sync's timing are what they were. In a pack's Setup the voice's one level (cues ONE LOUDNESS THROUGH
# SETUP) is read off its first LEVEL_LINES lines together, before the first is said: one line of Pocket's sits up to
# 3 dB off another of the same voice, so a first line alone left the announcer 4 dB under Lila (two lines: within
# about 1 dB of all four). Kill: CRISSA_MOUTHS_PACE=0.
HEAD_KEEP_S = 0.1
PLAIN_TAIL_S = 0.35
LEVEL_LINES = 2
RENDER_WAIT_S = 90.0          # a line's render, at most (the renderer's own synth timeout)


def pace_on() -> bool:
    return os.environ.get("CRISSA_MOUTHS_PACE", "1").strip().lower() not in ("0", "off", "false", "no")


def trim_head(pcm: bytes, sr: int, keep_s: float = HEAD_KEEP_S) -> bytes:
    """s16le mono with its lead-in quiet cut to keep_s before its first sound (cues.trim_tail's floor), faded in.
    Silence (or nothing loud) comes back unchanged."""
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    n = len(a)
    if n == 0:
        return pcm
    peak = max(max(a), -min(a))
    if peak < 64:
        return pcm
    thr = peak * (10.0 ** (-cues.TAIL_FLOOR_DB / 20.0))
    hop = max(1, int(sr * 0.005))
    start = None
    for i in range(0, n, hop):
        fr = a[i:i + hop]
        if max(fr) > thr or -min(fr) > thr:
            start = i
            break
    if start is None:
        return pcm
    cut = max(0, start - int(sr * max(0.0, keep_s)))
    if cut == 0:
        return pcm
    out = a[cut:]
    lead = start - cut
    for k in range(lead):
        out[k] = int(out[k] * (k / float(lead)))
    return out.tobytes()


def _render(text: str, speaker: str):
    """(pcm, sr, kept) of one line in `speaker`'s voice on this machine's tier (the verbal routine's own renderer), its
    lead-in quiet cut (THE TEAM SPEAKS AT THE PACE OF SPEECH)."""
    from . import voice_register as vr
    got = vr._ann_render(text, speaker=speaker)
    if got and pace_on():
        return (trim_head(got[0], int(got[1])), got[1], got[2])
    return got


def _superseded(voice_dir: Path, keep: set, ctx: dict | None) -> list:
    """The prints a new run replaces: those Setup made before (its label, or the first build's setup_* names), and any
    the ear would call stale against the context they were just made in (they keep `ask` true while they sit there)."""
    hp = her_prints()
    out = []
    for f in sorted(voice_dir.glob("*.json")) if voice_dir.is_dir() else []:
        if f.name in keep:
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        mine = f.name.startswith("setup_") or str(rec.get("label") or "").startswith(SETUP_LABEL)
        stale = False
        if not mine and hp is not None and ctx:
            try:
                stale = bool(hp.stale_why(rec, ctx))
            except Exception:  # noqa: BLE001
                stale = False
        if mine or stale:
            out.append(f)
    return out


def _set_aside(voice_dir: Path, stamp: str, files: list) -> int:
    if not files:
        return 0
    shelf = voice_dir / ("replaced-%s" % stamp)
    shelf.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.move(str(f), str(shelf / f.name))
    return len(files)


# THE TEAM INTRODUCES ITSELF (round 18, the User 2026-10-13: "the announcer introduces itself (role, when you hear it),
# which records its print"; "Crissa comes last and takes over while her prints are recorded: no formal voice check, one
# or two sentences each"). Setup's end (lib/crissa_init THE TEAM AT THE END) asks for one voice at a time with its own
# words: the coach hands this run CRISSA_MOUTHS_PLAN = {"ids": [...], "lines": {id: [...]}, "player": [...]}. An id in
# `player` is said through the player - the line rendered here, posted to /v1/speak with `wav`, which house-speak's
# --wav path plays with her mouth drawn on it (in Setup's turn: ONE VOICE AT A TIME) - and recorded on the raw
# microphone from just before the post until the player says its line is over (/v1/state `speakers` back to 0), then
# cut to its voice (the player's own start-up is silence in the take). The others play here, plain, as round 17's six
# lines (and are timed for the lip sync). A player that refuses the line (no ffmpeg, an older player) gets it played
# here. With no raw microphone the player's lines are still said (they are her introduction), unrecorded. No plan:
# round 17's six lines, unchanged.
PLAN_ENV = "CRISSA_MOUTHS_PLAN"
PLAYER_WAIT_S = 15.0          # how long the player may hold a line for its turn (its /v1/speak `wait_s`)
PLAYER_START_S = 8.0          # a line not in flight by then never started
PLAYER_POLL_S = 0.1
LEAD_KEEP_S = 0.15            # what a player take keeps before her first sound


def mouths_plan() -> dict[str, Any]:
    try:
        d = json.loads(os.environ.get(PLAN_ENV) or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def _player_base(site=None) -> str:
    raw = (os.environ.get("HOUSE_CONDUCTOR_URL") or "").strip()
    if raw:
        return raw.rstrip("/")
    su = str(getattr(site, "speak_url", "") or "")
    if su.endswith("/v1/speak"):
        return su[: -len("/v1/speak")].rstrip("/")
    try:
        lib = _lib_dir()
        if (lib / "crissa_ports.py").is_file() and str(lib) not in sys.path:
            sys.path.append(str(lib))
        import crissa_ports
        return str(crissa_ports.url("player")).rstrip("/")
    except Exception:  # noqa: BLE001
        return ""


def _player_json(url: str, body: dict | None = None, timeout: float = 3.0) -> dict | None:
    import urllib.request
    try:
        if body is None:
            req = urllib.request.Request(url, headers={"User-Agent": "crissa-coach"})
        else:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except Exception as e:  # noqa: BLE001  an HTTP refusal reads as one
        body_err = ""
        try:
            body_err = json.loads(e.read().decode("utf-8", "replace") or "{}").get("error") or ""  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": body_err or repr(e)[:120]}
    return got if isinstance(got, dict) else None


def _lips_wav(pcm: bytes, sr: int, text: str, voice: str) -> Path | None:
    """The line as the player will play it, under the state home's cache (the only place /v1/speak takes one from)."""
    import hashlib
    home = cues._state_home()
    if home is None:
        return None
    d = Path(home) / "cache" / "id" / "lips"
    try:
        d.mkdir(parents=True, exist_ok=True)
        p = d / ("%s.wav" % hashlib.sha256(("%s|%d|%s" % (voice, len(pcm), text)).encode()).hexdigest()[:24])
        tmp = p.with_name(p.name + ".part")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(sr))
            w.writeframes(pcm)
        os.replace(tmp, p)
        return p
    except (OSError, wave.Error):
        return None


def wait_player_line(base: str, *, started_s: float = PLAYER_START_S, cap_s: float = 30.0) -> bool:
    """Until the player's lines in flight (/v1/state `speakers`) rise and fall back to 0. False when the player does not
    say (no `speakers`: an older player), the line never started, or the cap."""
    seen = False
    t0 = time.time()
    while time.time() - t0 < cap_s:
        if cues.audio_stopped():
            return False
        st = _player_json(base + "/v1/state", None, 2.0)
        if not isinstance(st, dict) or "speakers" not in st:
            return False
        air = st.get("say_air_at")
        if air and air != _AIR.get("before"):
            _AIR["at"] = air                                   # this line's air time (round 20: its lip timing)
        if int(st.get("speakers") or 0) > 0:
            seen = True
        elif seen:
            return True
        elif time.time() - t0 > started_s:
            return False
        time.sleep(PLAYER_POLL_S)
    return False


def say_through_player(text: str, voice: str, pcm: bytes, sr: int, *, kept: float = 0.0, by: str = "setup",
                       site=None) -> bool:
    """One rendered line said by the player, her mouth drawn on it; returns once it has been said (its end stamped for
    the GO beep, cues.mark_spoken). False, nothing said: no player, it refused the line, or the cues were stopped."""
    if cues.audio_stopped() or not pcm or not sr:
        return False
    base = _player_base(site)
    if not base:
        return False
    wav = _lips_wav(cues._level_pcm(pcm, int(sr), voice), int(sr), text, voice)   # the level the coach plays at itself
    if wav is None:
        return False
    _AIR["at"] = None
    _AIR["before"] = ((_player_json(base + "/v1/state", None, 2.0) or {}).get("say_air_at")
                      if player_timing_on() else None)
    # a timed line (round 21): played with no stamp hold, so no silence is written into it after its air stamp
    got = _player_json(base + "/v1/speak", {"text": text[:800], "voice": voice, "wav": str(wav), "by": by,
                                            "wait_s": PLAYER_WAIT_S, "timed": bool(player_timing_on())},
                       PLAYER_WAIT_S + 5.0)
    if not isinstance(got, dict) or not got.get("ok"):
        print("  the player did not take the line (%s); it plays here" % str((got or {}).get("error") or "no answer")[:120],
              flush=True)
        return False
    secs = len(pcm) / 2.0 / sr
    if not wait_player_line(base, cap_s=secs + 20.0):
        deadline = time.time() + min(30.0, secs + 1.5)               # an older player: its length and a margin
        while time.time() < deadline and not cues.audio_stopped():
            time.sleep(0.1)
    cues.mark_spoken(kept)
    return True


# LILA'S CUES IN HER PAGE VOICE (round 37, .101 round 36: "wrong voice and too loud"): on the one page her cue was
# rendered here (this process's voice table: Pocket's eve, gain 0.83) and only played by the player, so it was not the
# Lila he hears on the page, whose lines the player renders itself (its voice engine, her level). Now the player is
# given the text and renders it the same way. Kill CRISSA_LILA_CUE_PLAYER_VOICE=0 (rendered here, as round 36).
def lila_cue_player_voice_on() -> bool:
    return os.environ.get("CRISSA_LILA_CUE_PLAYER_VOICE", "1").strip().lower() not in ("0", "off", "false", "no")


def say_text_through_player(text: str, voice: str, *, by: str = "setup", site=None) -> float | None:
    """The player renders and says `text` in `voice` (its own engine and level, her mouth on it); returns once it has
    been said (its end stamped for the GO beep) with the seconds the player gave, else None (nothing said here)."""
    if cues.audio_stopped() or not text:
        return None
    base = _player_base(site)
    if not base:
        return None
    got = _player_json(base + "/v1/speak", {"text": text[:800], "voice": voice, "by": by, "wait_s": PLAYER_WAIT_S},
                       PLAYER_WAIT_S + 5.0)
    if not isinstance(got, dict) or not got.get("ok"):
        print("  the player did not take the line (%s); it is rendered here" % str((got or {}).get("error")
                                                                                   or "no answer")[:120], flush=True)
        return None
    secs = float(got.get("seconds") or 0.0) or (0.9 + 0.065 * len(text))
    if not wait_player_line(base, cap_s=secs + 20.0):
        deadline = time.time() + min(30.0, secs + 1.5)               # an older player: its length and a margin
        while time.time() < deadline and not cues.audio_stopped():
            time.sleep(0.1)
    cues.mark_spoken(0.0)
    return secs


def _record_until(path: Path, site, stop: threading.Event, max_s: float, started: list | None = None) -> None:
    """record_fixed's capture (parec on site.mic, 16 kHz mono, its flags) until `stop` is set or max_s. started (round
    20): gets time.monotonic() just before parec starts, as record_fixed's."""
    import subprocess
    from . import voice_session as _vs
    path.parent.mkdir(parents=True, exist_ok=True)
    mic = site.mic
    if not mic:
        raise RuntimeError("site.audio.mic is empty")
    _vs.unmute_mic(site)
    lifted = _vs._pack() and _vs._source_muted(mic)
    in_finally = _vs.remute_in_finally()   # round 46 (2026-10-23): a Cancel mid-read mutes it again too
    if lifted:
        subprocess.run(["pactl", "set-source-mute", mic, "0"], capture_output=True)
    sr = 16000
    cap = int(max_s * sr) * 2
    raw = bytearray()
    try:
        print("  recording on %s (until her line is said, %.0f s at most)" % (mic, max_s), flush=True)
        if started is not None:
            started.append(time.monotonic())
        proc = subprocess.Popen(["parec", "--device", mic, "--format=s16le", "--rate=%d" % sr, "--channels=1",
                                 "--latency-msec=20"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while len(raw) < cap and not stop.is_set():
                chunk = proc.stdout.read(1600) if proc.stdout is not None else b""
                if not chunk:
                    break
                raw += chunk
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        if lifted and in_finally:
            _vs._remute(mic)
    if lifted and not in_finally:
        subprocess.run(["pactl", "set-source-mute", mic, "1"], capture_output=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(raw[: len(raw) - (len(raw) % 2)]))


def trim_to_voice(path: Path, keep_s: float = LEAD_KEEP_S) -> float:
    """Cut a take's lead-in silence: from keep_s before its first 20 ms frame above 8 % of its peak (voiced_fraction's
    own measure). Returns the seconds cut."""
    import array
    try:
        with wave.open(str(path), "rb") as w:
            sr, pcm = w.getframerate(), w.readframes(w.getnframes())
    except (OSError, wave.Error, EOFError):
        return 0.0
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    peak = max((abs(x) for x in a), default=0)
    if peak < 64:
        return 0.0
    hop = max(1, int(sr * 0.02))
    thr = peak * 0.08
    first = 0
    for i in range(0, len(a), hop):
        fr = a[i:i + hop]
        if max(fr) > thr or -min(fr) > thr:
            first = i
            break
    cut = max(0, first - int(sr * keep_s))
    if cut <= 0:
        return 0.0
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(a[cut:].tobytes())
    return cut / float(sr)


def _one_player(site, text: str, speaker: str, wav: Path | None, record: bool = True, embed: bool = True,
                got: tuple | None = None, lips: list | None = None):
    """One line through the player, recorded (record) on the raw microphone. The print's numbers as _one gives them,
    None when it was said and is no print, False when the player did not say it (the caller plays it here). embed False
    (round 19): the take's numbers and its file instead of the print, for _print_of once the voice's lines are said;
    got: the line rendered already."""
    got = got or _render(text, speaker)
    if not got:
        print("  mouths: no %s voice to say %r" % (speaker, text[:40]), flush=True)
        return None
    pcm, sr, kept = got
    secs = len(pcm) / 2.0 / sr
    if not record or wav is None:
        return None if say_through_player(text, speaker, pcm, sr, kept=kept, by="setup", site=site) else False
    stop = threading.Event()
    err: list[str] = []
    started: list = []

    def rec() -> None:
        try:
            if lips is None:
                _record_until(wav, site, stop, LEAD_S + PLAYER_WAIT_S + secs + 10.0)
            else:
                _record_until(wav, site, stop, LEAD_S + PLAYER_WAIT_S + secs + 10.0, started=started)
        except Exception as e:  # noqa: BLE001
            err.append(repr(e)[:160])
    th = threading.Thread(target=rec, name="mouths-rec-player", daemon=True)
    th.start()
    time.sleep(LEAD_S)
    said = say_through_player(text, speaker, pcm, sr, kept=kept, by="setup", site=site)
    if said:
        time.sleep(TAIL_S)
    stop.set()
    th.join(timeout=8.0)
    if not said:
        return False
    if err or not wav.is_file():
        print("  mouths: no recording (%s)" % (err[0] if err else "no file"), flush=True)
        return None
    if lips is not None:                     # timed before the lead-in is cut (round 20)
        lips.append(_lip_line_player(wav, started, cues._level_pcm(pcm, int(sr), speaker), sr, _AIR.get("at")))
    trim_to_voice(wav)
    active, pk = voiced_fraction(wav), peak_pcm(wav)
    if active < MIN_ACTIVE or pk < MIN_PEAK:
        print("  mouths: %s too quiet at the microphone (voiced %.0f%%, peak %d)" % (speaker, active * 100, pk),
              flush=True)
        return None
    take = {"wav": wav, "active": round(active, 3), "peak": int(pk), "seconds": round(secs, 2), "via": "player"}
    return take if not embed else _print_of(take)


def _print_of(take: dict) -> dict | None:
    """A take's print: its numbers with the encoding in place of its file, or None when the encoder made none."""
    emb = embed_wav(take["wav"])
    if emb is None:
        return None
    out = {k: v for k, v in take.items() if k != "wav"}
    out["encoding"] = [float(x) for x in emb.tolist()]
    return out


def _one(site, text: str, speaker: str, wav: Path, lips: list | None = None,
         sink: str | None = None, embed: bool = True, tail_s: float | None = None,
         got: tuple | None = None) -> dict[str, Any] | None:
    """lips (round 17): when a list, this line is timed too (its result appended) and played on `sink`. embed False,
    tail_s and got (round 19): the take for _print_of later, the room kept after the line, the line rendered already."""
    got = got or _render(text, speaker)
    if not got:
        print("  mouths: no %s voice to say %r" % (speaker, text[:40]), flush=True)
        return None
    pcm, sr, _kept = got
    secs = len(pcm) / 2.0 / sr
    rec_s = LEAD_S + secs + (TAIL_S if tail_s is None else max(0.0, float(tail_s)))
    err: list[str] = []
    started: list = []
    played: dict = {}

    def rec() -> None:
        try:
            if lips is None:
                record_fixed(wav, site, rec_s)
            else:
                record_fixed(wav, site, rec_s, started=started)
        except Exception as e:  # noqa: BLE001
            err.append(repr(e)[:160])
    th = threading.Thread(target=rec, name="mouths-rec", daemon=True)
    th.start()
    time.sleep(LEAD_S)
    if lips is None:
        cues._paplay_pcm(pcm, sr, site.sink, speaker=speaker)
    else:
        cues._paplay_pcm(pcm, sr, sink or site.sink, speaker=speaker,
                         on_play=lambda t, sent, ssr: played.update(t=t, pcm=sent, sr=ssr))
    th.join(timeout=rec_s + 8.0)
    if err or not wav.is_file():
        print("  mouths: no recording (%s)" % (err[0] if err else "no file"), flush=True)
        return None
    if lips is not None:
        lips.append(_lip_line(wav, started, played))
    active, pk = voiced_fraction(wav), peak_pcm(wav)
    if active < MIN_ACTIVE or pk < MIN_PEAK:
        print("  mouths: %s too quiet at the microphone (voiced %.0f%%, peak %d)" % (speaker, active * 100, pk),
              flush=True)
        return None
    take = {"wav": wav, "active": round(active, 3), "peak": int(pk), "seconds": round(secs, 2)}
    return take if not embed else _print_of(take)


# HER LINES AS ONE LINE, AT THE PACE OF SPEECH (.101 round 29, the User 2026-10-18: "does crissa need the long gaps
# between her sentences? Sound robotic"; "she should say the sentence ... no gaps"). Through the player each of her timing
# lines was its own line: the recorder's LEAD_S, the post, the player's start-up (its air stamp 0.55-0.6 s after the
# post), the line, the player's drain, TAIL_S, the recorder stopped and joined - .101 heard about 2 s of quiet between
# each (12:09:54 -> :56, 12:10:00 -> :02, 12:10:05 -> :07). Round 19's pace never reached this road (it is the plain
# road's). Now a voice's player lines are joined into ONE line, each sentence as rendered (its lead-in cut to
# HEAD_KEEP_S, round 19) with PLAIN_TAIL_S of quiet after it (round 19's plain pace), said once and recorded once; each
# sentence keeps its own lip timing (its place in the line is known to the sample) and its own print (its stretch of the
# one recording). A player that does not take the line: each line on its own, as before. The plan may hold the line
# until Setup says go (`go`: a file, Lila's handover still on the air) and asks where the line's air time is written
# (`air`: {air_at, starts, seconds}, for her entrance under her last sentence). Kill: CRISSA_MOUTHS_ONE_LINE=0.
def one_line_on() -> bool:
    return os.environ.get("CRISSA_MOUTHS_ONE_LINE", "1").strip().lower() not in ("0", "off", "false", "no")


def wait_go(plan: dict) -> bool:
    """Hold until Setup's go file (plan `go`) is there, at most plan `go_cap_s`; True at once with no `go`."""
    go = str(plan.get("go") or "")
    if not go:
        return True
    try:
        cap = max(0.0, float(plan.get("go_cap_s") or 0.0))
    except (TypeError, ValueError):
        cap = 0.0
    t0 = time.time()
    while not os.path.exists(go):
        if cues.audio_stopped() or time.time() - t0 >= cap:
            print("  mouths: no go from Setup in %.0f s; her line goes now" % cap, flush=True)
            return False
        time.sleep(PLAYER_POLL_S)
    return True


def _write_air(path: str, air_at: float, starts: list, secs: float) -> None:
    if not path:
        return
    try:
        p = Path(path)
        tmp = p.with_name(".%s.%d.tmp" % (p.name, os.getpid()))
        tmp.write_text(json.dumps({"air_at": float(air_at), "starts": starts, "seconds": round(secs, 3)}) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _slice_wav(src: Path, dst: Path, t0: float, t1: float | None) -> bool:
    """dst = src's stretch [t0, t1) in seconds (t1 None: to its end)."""
    try:
        with wave.open(str(src), "rb") as w:
            sr, raw = w.getframerate(), w.readframes(w.getnframes())
    except (OSError, wave.Error, EOFError):
        return False
    a = max(0, int(t0 * sr)) * 2
    b = len(raw) if t1 is None else min(len(raw), max(0, int(t1 * sr)) * 2)
    if b - a < sr * 2 // 10:                           # under 0.1 s: nothing of hers is in it
        return False
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(raw[a:b])
    return True


def _lines_player(site, lines: tuple, speaker: str, wavs: list, gots: list, lips: list | None,
                  air_file: str = "") -> list | bool:
    """Her lines joined and said through the player as one line (HER LINES AS ONE LINE). [(take, text), ...] of the
    sentences heard, or False when the player did not say it (the caller says them one by one)."""
    if not gots or not all(gots) or len({int(g[1]) for g in gots}) != 1:
        return False
    sr = int(gots[0][1])
    gap = b"\x00\x00" * int(sr * PLAIN_TAIL_S)
    pcm, starts = b"", []
    for i, g in enumerate(gots):
        starts.append(round(len(pcm) / 2.0 / sr, 4))
        pcm += g[0] + (gap if i < len(gots) - 1 else b"")
    secs = len(pcm) / 2.0 / sr
    whole = wavs[0].with_name(wavs[0].stem + "_line.wav")
    stop = threading.Event()
    err: list[str] = []
    started: list = []
    said: list = []

    def rec() -> None:
        try:
            _record_until(whole, site, stop, LEAD_S + PLAYER_WAIT_S + secs + 10.0, started=started)
        except Exception as e:  # noqa: BLE001
            err.append(repr(e)[:160])

    def say() -> None:
        said.append(say_through_player(" ".join(lines), speaker, pcm, sr, kept=float(gots[-1][2]), by="setup",
                                       site=site))
    th = threading.Thread(target=rec, name="mouths-rec-line", daemon=True)
    th.start()
    time.sleep(LEAD_S)
    _AIR["at"] = None
    sp = threading.Thread(target=say, name="mouths-say-line", daemon=True)
    sp.start()
    while sp.is_alive() and not _AIR.get("at"):          # its air time, for her entrance under her last sentence
        sp.join(PLAYER_POLL_S)
    if _AIR.get("at"):
        _write_air(air_file, float(_AIR["at"]), starts, secs)
    sp.join()
    if said and said[0]:
        time.sleep(TAIL_S)
    stop.set()
    th.join(timeout=8.0)
    try:
        if not (said and said[0]):
            return False
        if err or not whole.is_file():
            print("  mouths: no recording (%s)" % (err[0] if err else "no file"), flush=True)
            return []
        air = _AIR.get("at")
        if lips is not None:                 # each sentence timed at its own place in the one line
            for g, s in zip(gots, starts):
                lips.append(_lip_line_player(whole, started, cues._level_pcm(g[0], sr, speaker), sr,
                                             (float(air) + s) if air else None))
        # each sentence's take: its stretch of the recording, from its play to the next one's
        try:
            base = float(air) - (time.time() - time.monotonic()) - float(started[0]) if air and started else None
        except (TypeError, ValueError):
            base = None
        takes = []
        for i, (wav, text) in enumerate(zip(wavs, lines)):
            if base is None:
                break
            t1 = base + starts[i + 1] if i + 1 < len(starts) else None
            if not _slice_wav(whole, wav, base + starts[i], t1):
                continue
            trim_to_voice(wav)
            active, pk = voiced_fraction(wav), peak_pcm(wav)
            if active < MIN_ACTIVE or pk < MIN_PEAK:
                print("  mouths: %s too quiet at the microphone (voiced %.0f%%, peak %d)" % (speaker, active * 100, pk),
                      flush=True)
                continue
            takes.append(({"wav": wav, "active": round(active, 3), "peak": int(pk),
                           "seconds": round(len(gots[i][0]) / 2.0 / sr, 2), "via": "player"}, text))
        print("  mouths: %d lines said as one (%.1f s), %d takes" % (len(lines), secs, len(takes)), flush=True)
        return takes
    finally:
        try:
            whole.unlink(missing_ok=True)            # no audio is kept
        except OSError:
            pass


def run_mouths(site, *, ids: tuple | None = None) -> dict[str, Any]:
    """Say, record and print each line (see the module's note). {id: prints saved}; the ear is given back at the end.
    `ids` None: the plan's (THE TEAM INTRODUCES ITSELF), else both, as round 17."""
    plan = mouths_plan()
    if ids is None:
        ids = tuple(x for x in (plan.get("ids") or ()) if x in LINES) or ("crissa_her_tts", "announcer")
    by_player = set() if _is_house() else {x for x in (plan.get("player") or ()) if x in ids}
    words = plan.get("lines") if isinstance(plan.get("lines"), dict) else {}
    lines_of = {pid: tuple(str(t) for t in (words.get(pid) or ()) if str(t).strip()) or LINES[pid][1] for pid in ids}
    plain = [pid for pid in ids if pid not in by_player]
    out: dict[str, Any] = {"saved": {}, "quiet": {}}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    meta = live_encoder_meta()
    engine = _engine()
    ctx = _context()            # read as the ear sees it running (before it is released: the echo canceller's speaker)
    mic = print_mic(site)       # asked while the ear still answers: its microphone, raw (round 17)
    if not mic and room_mic_on() and not _is_house():
        # nothing that hears her raw: no line is played for a recording that cannot be made, and the earlier prints stay
        why = "no microphone that hears her raw (none chosen, or only the echo canceller's, which removes her voice)"
        print("  mouths: %s" % why, flush=True)
        if by_player:
            wait_go(plan)                  # .101 round 29: still after Lila's last word
        for pid in ids:                    # her introduction is still said through the player, unrecorded (round 18)
            for text in (lines_of[pid] if pid in by_player else ()):
                if _one_player(site, text, LINES[pid][0], None, record=False) is False:
                    break
        out.update(saved={pid: 0 for pid in ids}, quiet={pid: 0 for pid in ids}, why=why)
        # round 45 (.101 round 44: "Setup took no lip-sync measure on this first boot", with no reason): her lines
        # alone through the player (round 20) left `plain` empty, so no measurement was recorded at all; it is
        # recorded now with the reason, whichever way she was to be timed
        if lip_measure_on() and (plain or (player_timing_on() and bool(by_player))):
            out["lip_sync"] = _lip_sync_done(None, why="nothing was played: " + why)
        coach_emit(type="mouths_done", saved=out["saved"], quiet=out["quiet"], why=why)
        return out
    old_mic = os.environ.get("HOUSE_ENROLL_MIC")
    if mic and room_mic_on():
        os.environ["HOUSE_ENROLL_MIC"] = mic        # site.mic reads it: record_fixed records here, as Voice ID's take
    ptimed = player_timing_on() and bool(by_player) and not plain     # her lines alone, through the player (round 20)
    timed = (_lip_timing() and bool(plain)) or ptimed
    lips: list | None = [] if timed else None
    sink = _her_sink(site) if timed else None
    release_ear_for_enroll()
    tmp = Path(cues._state_home() or Path.home()) / "cache" / "id" / "mouths"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for pid in ids:
            speaker, lines = LINES[pid][0], lines_of[pid]
            coach_emit(type="mouths", who=pid, text="%s's voice through your speakers" % (
                "Crissa" if speaker == "crissa" else "The announcer"))
            voice_dir = mouths_root() / pid / "voice"
            recs = []
            paced = pace_on()
            ready: dict = {}
            evs = [threading.Event() for _ in lines]
            if paced:
                def renderer(lines=lines, speaker=speaker, evs=evs, ready=ready) -> None:
                    for i, t in enumerate(lines):            # in order, while the earlier lines are said
                        try:
                            ready[i] = _render(t, speaker)
                        except Exception:  # noqa: BLE001  that line renders again when its turn comes
                            ready[i] = None
                        evs[i].set()
                threading.Thread(target=renderer, name="mouths-render", daemon=True).start()
                for e in evs[:LEVEL_LINES]:
                    e.wait(RENDER_WAIT_S)
                parts = [ready.get(i) for i in range(min(LEVEL_LINES, len(lines))) if ready.get(i)]
                level = getattr(cues, "setup_gain_for", None)
                if level is not None and parts and all(p[1] == parts[0][1] for p in parts):
                    level(b"".join(p[0] for p in parts), int(parts[0][1]), speaker)   # its Setup level
            wavs = [tmp / ("%s_%d.wav" % (pid, i)) for i in range(1, len(lines) + 1)]
            try:
                takes = []
                joined = False
                if pid in by_player and paced and one_line_on() and len(lines) > 1:
                    for e in evs:
                        e.wait(RENDER_WAIT_S)          # every sentence rendered (warmed ahead in Setup: on disk)
                    wait_go(plan)
                    got_line = _lines_player(site, lines, speaker, wavs, [ready.get(i) for i in range(len(lines))],
                                             lips if ptimed else None, air_file=str(plan.get("air") or ""))
                    if got_line is not False:
                        takes, joined = list(got_line), True
                elif pid in by_player:
                    wait_go(plan)
                for i, (wav, text) in enumerate(zip(wavs, lines)):
                    if joined:
                        break
                    if paced:
                        evs[i].wait(RENDER_WAIT_S)     # its own render (made while the last line was said)
                    got = (_one_player(site, text, speaker, wav, embed=not paced, got=ready.get(i),
                                       lips=lips if ptimed else None)
                           if pid in by_player else False)
                    if got is False:                # not through the player: plain, here (round 17)
                        got = _one(site, text, speaker, wav, lips, sink, embed=not paced,
                                   tail_s=PLAIN_TAIL_S if paced else None, got=ready.get(i))
                        if not paced:
                            time.sleep(0.35)
                    if got:
                        takes.append((got, text))
                for got, text in takes:             # the prints, once the voice's last line is said
                    pr = _print_of(got) if "wav" in got else got
                    if pr:
                        recs.append(dict(pr, text=text))
            finally:
                for wav in wavs:
                    try:
                        wav.unlink(missing_ok=True)     # no audio is kept
                    except OSError:
                        pass
            out["quiet"][pid] = len(lines) - len(recs)
            if not recs:
                out["saved"][pid] = 0                   # nothing heard: the earlier prints stay as they are
                continue
            written = set()
            for n, r in enumerate(recs, 1):
                label = "%s %s #%d (%s, %s): %s" % (SETUP_LABEL, stamp, n, speaker, engine or "voice", r["text"])
                written.add(_write(pid, r["encoding"], str(meta["model"]), label, ctx).name)
            moved = _set_aside(voice_dir, stamp, _superseded(voice_dir, written, ctx))
            out["saved"][pid] = len(recs)
            print("  mouths: %s %d print%s (%d set aside)" % (pid, len(recs), "" if len(recs) == 1 else "s", moved),
                  flush=True)
    finally:
        if mic and room_mic_on():
            if old_mic is None:
                os.environ.pop("HOUSE_ENROLL_MIC", None)
            else:
                os.environ["HOUSE_ENROLL_MIC"] = old_mic
        reopen_ear_after_enroll()
    if lip_measure_on() and not _is_house() and (plain or ptimed):   # round 20: her player lines alone are timed too
        out["lip_sync"] = _lip_sync_done(lips, why="" if lips is not None else "the lines could not be timed here")
    coach_emit(type="mouths_done", saved=out["saved"], quiet=out["quiet"])
    return out


def _engine() -> str:
    try:
        from . import voice_register as vr
        cfg = vr._ann_lib().config()
        return str(cfg.get("engine") or "")
    except Exception:  # noqa: BLE001
        return ""
