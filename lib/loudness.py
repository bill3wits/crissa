"""One yardstick for the house's voices (2026-09-18).

Every voice is levelled on the same measure: the MEDIAN RMS of its voiced
20 ms hops, in dBFS. `HOUSE_LEVEL_TARGET_DB` (−26) is the band Crissa and
Lila speak in and the band the film's own soundtrack is raised to;
`HOUSE_SPEAK_CHATTER_TARGET_DB` (−33) is the crew's board talk and the
announcer, which the room should hear under her, and which drop a further
`HOUSE_SPEAK_CHATTER_DUCK_DB` (12) while the mic is live. the User 2026-09-18:
"Can you level them. Then only quieted voice should be [the board] and announcer
when Crissa or I am on the mic or not paused."
"""
from __future__ import annotations

import array
import json
import math
import os
import time
from pathlib import Path

# −17: Crissa's natural voice level, the band the Composer's film sits at as
# the browser plays it (the User 2026-09-18 pm: "profiles are set too loudly
# compared to clips and system vol … turn profiles down to the clips level").
# Profile voices are levelled BOTH ways onto it (Lila comes down too); the
# film's paplay bake is raised to it, never cut.
# −20 (pm, after "the volume boost hurt my ears"): the harvest films sit at −26 to
# −12 median and the browser plays them as they are, so the voices sit LOW in that
# band; raise HOUSE_LEVEL_TARGET_DB to taste. The drill's film voice is never levelled.
# −26 (2026-09-18 12:55, the User after the power cut: "sound levels playing clips native track
# way below announcer and profiles … better trim the announcer and crissa/lila voices … when I
# press Say it blasts my ears"): measured, the Composer's drill film (clip-talk-back-harvest)
# sits at −26.3 median / −6 peak and Brave plays it as it is, so −20 voices were 6 dB over
# it and −27 chatter at its level. The band is now the film's floor: voices −26, chatter −33.
LEVEL_TARGET_DB = float(os.environ.get("HOUSE_LEVEL_TARGET_DB", "-26"))
CHATTER_TARGET_DB = float(os.environ.get("HOUSE_SPEAK_CHATTER_TARGET_DB", "-33"))
CHATTER_DUCK_DB = float(os.environ.get("HOUSE_SPEAK_CHATTER_DUCK_DB", "12"))
CHATTER_WHO = frozenset({"ada", "bash", "steward", "vera", "announcer"})
LIMIT = 0.89            # −1.1 dBFS after makeup
DEADBAND_DB = 1.0
MAX_GAIN = 2.0          # +6 dB cap on a raise (a bigger lift crushes peaks into the limiter)
MIN_GAIN = 0.1          # −20 dB floor on a cut


def speech_med_rms(pcm: bytes, sr: int = 24000, floor: float = 0.02) -> float | None:
    """Median hop RMS (0..1) of voiced 20 ms hops. None when the buffer is pad."""
    n = len(pcm) // 2
    if n < int(sr * 0.04):
        return None
    a = array.array("h")
    a.frombytes(pcm[: n * 2])
    hop = max(1, int(sr * 0.02))
    voiced: list[float] = []
    for i in range(0, n - hop, hop):
        acc = 0
        for x in a[i: i + hop]:
            acc += x * x
        rms = math.sqrt(acc / hop) / 32768.0
        if rms >= floor:
            voiced.append(rms)
    if len(voiced) < 8:
        return None
    voiced.sort()
    return voiced[len(voiced) // 2]


def gain_to(pcm: bytes, sr: int, target_db: float, *, allow_cut: bool = False) -> float:
    """The linear gain that puts this buffer's speech median on target_db.
    1.0 inside the deadband; never below 1.0 unless allow_cut."""
    med = speech_med_rms(pcm, sr)
    if med is None or med <= 0:
        return 1.0
    med_db = 20.0 * math.log10(med)
    if abs(med_db - target_db) <= DEADBAND_DB:
        return 1.0
    if med_db > target_db and not allow_cut:
        return 1.0
    g = 10.0 ** ((target_db - med_db) / 20.0)
    return min(max(g, MIN_GAIN), MAX_GAIN)


def apply_gain(pcm: bytes, gain: float) -> bytes:
    """Scale int16 PCM; a soft limiter above LIMIT. Same sample count."""
    if 0.98 <= gain <= 1.02:
        return pcm
    n = len(pcm) // 2
    a = array.array("h")
    a.frombytes(pcm[: n * 2])
    lim = LIMIT * 32767.0
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
    return out.tobytes()


def who_of(air_who: str | None) -> str:
    """`crissa@bench` → `crissa`."""
    return str(air_who or "").split("@")[0].strip().lower()


def target_for(air_who: str | None, mic_live: bool = False) -> tuple[float, bool]:
    """(target_db, allow_cut) for a voice. Crissa and Lila: the house band,
    raise only. The crew and the announcer: the chatter band, cut allowed,
    a further duck while the mic is live."""
    who = who_of(air_who)
    if who in CHATTER_WHO:
        return (CHATTER_TARGET_DB - (CHATTER_DUCK_DB if mic_live else 0.0)), True
    return LEVEL_TARGET_DB, True     # a profile voice sits ON the band, up or down


# ONE LOUDNESS THROUGH SETUP (round 19, the User 2026-10-14, .101 round 18: "volumes were different from lila to the
# intros"). Setup's lines reach the speakers by two roads, and each levelled its own way:
#   - house-speak's streamed voice levels a line on the median of its first second or so (Pocket opens a line hotter
#     than the rest of it: measured on this machine's Pocket, its Setup lines sat 1.3-8.4 dB under the band);
#   - the coach's rendered lines (Lila's counted cues, the announcer's and Crissa's introductions) were scaled to a PEAK
#     of half of full scale, which is no loudness at all: the same lines sat 1.9 dB under to 7.7 dB over the band.
# One voice could step by up to 11 dB between the two roads, and the three voices by more. Both roads now keep ONE gain
# per voice per Setup run in <state home>/run/setup-level.json (round 17's file, the same keys: "who|engine|target"),
# on this module's one measure: a voice's first measure stands for the run, except that a WHOLE line's ("line", the
# coach renders its lines whole) replaces one read off a line's start ("start", house-speak's), never the reverse.
# Kill: CRISSA_SETUP_ONE_LOUDNESS=0 (the coach's peak level, house-speak's round-17 level).
SETUP_LEVEL_FILE = "setup-level.json"


def setup_one_loudness_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_SETUP_ONE_LOUDNESS", "1")).strip().lower() not in ("0", "off", "false", "no")


def setup_run_id(home) -> str:
    """The id of the Setup run going on now under this state home ("" when none): Init's own state file."""
    try:
        st = json.loads((Path(home) / "init-run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    if isinstance(st, dict) and st.get("status") == "running":
        return str(st.get("run_id") or "")
    return ""


def setup_level_key(who: str | None, engine: str | None, target_db: float) -> str:
    return "%s|%s|%.1f" % (who_of(who), engine or "", float(target_db))


def setup_level_path(home) -> Path:
    return Path(home) / "run" / SETUP_LEVEL_FILE


def setup_gain(home, run: str, key: str) -> tuple[float | None, str]:
    """(the gain this voice keeps through the run, how it was read: "line" | "start"), or (None, "")."""
    try:
        d = json.loads(setup_level_path(home).read_text(encoding="utf-8"))
        if d.get("run_id") != run:
            return None, ""
        g = float((d.get("gains") or {})[key])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, ""
    if not 0.05 <= g <= MAX_GAIN:
        return None, ""
    return g, str((d.get("how") or {}).get(key) or "start")


def setup_gain_keep(home, run: str, key: str, gain: float, how: str = "start") -> bool:
    """Keep `gain` for `key` through the run (one atomic write under a lock). The first stands, except that a "line"
    replaces a "start". True when written."""
    p = setup_level_path(home)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        lk = open(p.with_name(p.name + ".lock"), "a+")
    except OSError:
        return False
    try:
        try:
            import fcntl
            fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(d, dict) or d.get("run_id") != run:
                d = {}
        except (OSError, ValueError):
            d = {}
        gains, hows = dict(d.get("gains") or {}), dict(d.get("how") or {})
        if key in gains and not (how == "line" and hows.get(key, "start") != "line"):
            return False
        gains[key] = round(float(gain), 4)
        hows[key] = how
        tmp = p.with_name(".%s.%d.tmp" % (p.name, os.getpid()))
        tmp.write_text(json.dumps({"run_id": run, "gains": gains, "how": hows, "at": time.time()}) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)
        return True
    except OSError:
        return False
    finally:
        lk.close()


# ONE GAIN PER VOICE FOR THE SESSION (round 20, the User 2026-10-14, .101 round 19: "volume went lower and higher").
# Setup kept one gain a voice (above), but outside a Setup run every line was levelled on its own start again (Crissa
# 0.38, 0.33, ... in conversation), and Crissa had no Setup gain at all. Outside Setup a voice now keeps ONE gain in
# <state home>/run/voice-level.json under the same "who|engine|target" key: the newest WHOLE-line measure of it
# ("line": Setup's, any run, or one of her own conversation lines of at least VOICE_LINE_MIN_S of speech), else the
# first line's start ("start") until a whole line replaces it. Kill: CRISSA_VOICE_ONE_LEVEL=0 (each line on its own).
VOICE_LEVEL_FILE = "voice-level.json"
VOICE_LINE_MIN_S = 2.0          # a whole line measures a voice only with this much speech in it


def voice_one_level_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_VOICE_ONE_LEVEL", "1")).strip().lower() not in ("0", "off", "false", "no")


def voice_level_path(home) -> Path:
    return Path(home) / "run" / VOICE_LEVEL_FILE


def _entries(path: Path) -> tuple[dict, dict, float]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}, {}, 0.0
    if not isinstance(d, dict):
        return {}, {}, 0.0
    try:
        at = float(d.get("at") or 0.0)
    except (TypeError, ValueError):
        at = 0.0
    return dict(d.get("gains") or {}), dict(d.get("how") or {}), at


def voice_gain(home, key: str) -> tuple[float | None, str]:
    """(the gain this voice keeps outside Setup, "line" | "start"), or (None, ""): a whole line's measure beats a
    line's start; among two of a kind the newer stands (a Setup run again measures her again)."""
    cands = []
    gains, hows, at = _entries(voice_level_path(home))
    ats = {}
    try:
        ats = dict(json.loads(voice_level_path(home).read_text(encoding="utf-8")).get("ats") or {})
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if key in gains:
        cands.append((gains[key], str(hows.get(key) or "start"), float(ats.get(key) or at)))
    sg, sh, sat = _entries(setup_level_path(home))
    if key in sg:
        cands.append((sg[key], str(sh.get(key) or "start"), sat))
    best = None
    for g, how, t in cands:
        try:
            g = float(g)
        except (TypeError, ValueError):
            continue
        if not 0.05 <= g <= MAX_GAIN:
            continue
        rank = (how == "line", t)
        if best is None or rank > best[0]:
            best = (rank, g, how)
    return (best[1], best[2]) if best else (None, "")


def voice_gain_keep(home, key: str, gain: float, how: str = "start") -> bool:
    """Keep `gain` for `key` outside Setup (one atomic write under a lock): the first measure stands, except that a whole
    line ("line") replaces a line's start ("start"), never the reverse, and never another whole line (a gain that moved
    with every line is the fault this fixes). True when written."""
    p = voice_level_path(home)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        lk = open(p.with_name(p.name + ".lock"), "a+")
    except OSError:
        return False
    try:
        try:
            import fcntl
            fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                d = {}
        except (OSError, ValueError):
            d = {}
        gains, hows, ats = dict(d.get("gains") or {}), dict(d.get("how") or {}), dict(d.get("ats") or {})
        if key in gains and (str(hows.get(key) or "start") == "line" or how != "line"):
            return False
        now = time.time()
        gains[key], hows[key], ats[key] = round(float(gain), 4), how, round(now, 3)
        tmp = p.with_name(".%s.%d.tmp" % (p.name, os.getpid()))
        tmp.write_text(json.dumps({"gains": gains, "how": hows, "ats": ats, "at": now}) + "\n", encoding="utf-8")
        os.replace(tmp, p)
        return True
    except OSError:
        return False
    finally:
        lk.close()


def voiced_s(pcm: bytes, sr: int, floor: float = 0.02) -> float:
    """Seconds of speech in `pcm` (20 ms hops at or above `floor`, as speech_med_rms counts them)."""
    n = len(pcm) // 2
    if not n or not sr:
        return 0.0
    a = array.array("h")
    a.frombytes(pcm[: n * 2])
    hop = max(1, int(sr * 0.02))
    k = 0
    for i in range(0, n - hop, hop):
        acc = 0
        for x in a[i: i + hop]:
            acc += x * x
        if math.sqrt(acc / hop) / 32768.0 >= floor:
            k += 1
    return k * 0.02


def line_gain(pcm: bytes, sr: int, target_db: float = LEVEL_TARGET_DB) -> float | None:
    """The gain that puts a whole line's speech median on target_db (cut allowed), or None when it holds no speech."""
    if speech_med_rms(pcm, sr) is None:
        return None
    return gain_to(pcm, sr, target_db, allow_cut=True)
