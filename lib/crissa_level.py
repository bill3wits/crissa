"""HER VOICE AT THIS COMPUTER'S OWN LOUDNESS (the User 2026-10-26: "Levels have been way off between system sounds and
speech. Could we just match their system sound level and do a comparison in the init sequence?").

Her lines are levelled DIGITALLY to one band (lib/loudness LEVEL_TARGET_DB, -26 dBFS speech median), but this computer's
own sounds - the desktop's alert, the beeps - ride another stream at another volume, so the same number is not the same
loudness in the room. Measured at the User's desk 2026-10-26 (Shure MV6, speakers at 41 %): the desktop alert peaked 5 dB
over one of her lines, both only 4-7 dB over the room's floor.

Setup's own-voice step already plays her lines through the speakers and records them on the raw microphone
(modules/id .. mouth_prints.run_mouths). The same step now also plays THIS COMPUTER'S alert sound once, records it the
same way, and compares the two as the microphone heard them: `offset_db` = alert - her lines (mean of the 50 ms windows
10 dB over the floor), clamped to +-CAP_DB. house-speak adds the offset to her target in a pack (play_target_for), so her
next line sits where the computer's own sounds sit. A measurement too close to the room's floor (either sound under
SNR_MIN_DB over it) is `quiet`: nothing changes and Setup's card says to turn the speakers up and run Voice ID again.

Never on the house (its voices keep their own yardstick). State: <state home>/run/level-match.json. Kill:
CRISSA_LEVEL_MATCH=0 (no measurement, offset 0). Pure stdlib (wave + array); numpy is not needed.
"""
from __future__ import annotations

import array
import json
import math
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

FILE_NAME = "level-match.json"
CAP_DB = 8.0                      # her voice moves at most this far either way to meet the computer's sounds
SNR_MIN_DB = 6.0                  # a sound under this over the room's floor is no measurement
WINDOW_S = 0.05
GATE_DB = 10.0                    # a window this far over the floor is the sound, not a gap
ALERT_PLAY_S = 3.0                # the alert's recording (the alert itself is about a second)
SYSTEM_SOUNDS = (                 # this computer's own alert, in the order looked for
    "~/.local/share/sounds/__custom/bell-window-system.ogg",      # GNOME's custom alert (Settings > Sound)
    "~/.local/share/sounds/__custom/bell-terminal.ogg",
    "/usr/share/sounds/freedesktop/stereo/bell.oga",
    "/usr/share/sounds/freedesktop/stereo/message.oga",
    "/usr/share/sounds/gnome/default/alerts/drip.ogg",
    "/System/Library/Sounds/Glass.aiff",                          # macOS
)


def on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_LEVEL_MATCH", "1")).strip().lower() in ("0", "off", "false", "no"):
        return False
    try:
        import crissa_home
        return not crissa_home.is_house()
    except ImportError:
        return True


def path(home: Path | str) -> Path:
    return Path(home) / "run" / FILE_NAME


def system_sound(env: dict | None = None) -> Path | None:
    """This computer's alert sound file, or None. CRISSA_LEVEL_MATCH_SOUND names one."""
    e = os.environ if env is None else env
    named = str(e.get("CRISSA_LEVEL_MATCH_SOUND") or "").strip()
    for cand in ((named,) if named else ()) + SYSTEM_SOUNDS:
        p = Path(cand).expanduser()
        if p.is_file():
            return p
    return None


def play_system_sound(sound: Path, sink: str | None = None, timeout: float = 10.0) -> bool:
    """The alert through the desktop's own event path when it has one (canberra), else the sink's player: the volume
    the person hears their computer's sounds at, not ours."""
    cmds = []
    if shutil.which("canberra-gtk-play"):
        cmds.append(["canberra-gtk-play", "-f", str(sound), "-d", "crissa-level-check"])
    if shutil.which("paplay"):
        cmds.append(["paplay"] + (["--device", sink] if sink else []) + [str(sound)])
    if sys.platform == "darwin" and shutil.which("afplay"):
        cmds.append(["afplay", str(sound)])
    for cmd in cmds:
        try:
            if subprocess.run(cmd, capture_output=True, timeout=timeout).returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    return False


def _samples(wav_path: Path) -> tuple[array.array, int]:
    with wave.open(str(wav_path), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError("16-bit PCM only")
    x = array.array("h")
    x.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if ch > 1:
        x = array.array("h", x[::ch])
    return x, sr


def _db(x) -> float:
    if not len(x):
        return -120.0
    s = 0.0
    for v in x:
        s += float(v) * float(v)
    return 20.0 * math.log10(math.sqrt(s / len(x)) / 32768.0 + 1e-9)


def window_dbs(wav_path: Path, window_s: float = WINDOW_S) -> list[float]:
    x, sr = _samples(wav_path)
    n = max(1, int(window_s * sr))
    return [_db(x[i:i + n]) for i in range(0, len(x) - n + 1, n)]


def floor_db(dbs: list[float]) -> float:
    """The room's floor: the 20th percentile of the windows (the gaps around and inside a sound)."""
    if not dbs:
        return -120.0
    s = sorted(dbs)
    return s[min(len(s) - 1, int(0.2 * len(s)))]


def active_db(dbs: list[float], floor: float) -> tuple[float | None, int]:
    """(mean dB of the windows GATE_DB over the floor, their count): the sound itself, not the gaps."""
    hot = [v for v in dbs if v > floor + GATE_DB]
    return ((sum(hot) / len(hot)) if hot else None), len(hot)


def measure(alert_wav: Path | None, line_wavs: list[Path]) -> dict:
    """Compare the alert and her lines as the microphone heard them. Pure numbers; nothing played here."""
    if alert_wav is None or not Path(alert_wav).is_file():
        return measure_dbs(None, [], lines=len(line_wavs))
    try:
        a = window_dbs(Path(alert_wav))
        ls = [w for p in line_wavs for w in window_dbs(Path(p))]
    except (OSError, wave.Error, ValueError) as e:
        return {"schema": "level-match/1", "at": time.time(), "lines": len(line_wavs), "offset_db": 0.0,
                "outcome": "unread", "why": "a recording could not be read (%s)" % type(e).__name__}
    return measure_dbs(a, ls, lines=len(line_wavs))


def measure_dbs(alert_dbs: list[float] | None, line_dbs: list[float], *, lines: int = 0) -> dict:
    """The same on the windows already read (Setup reads her lines' windows before their takes are deleted)."""
    rec: dict = {"schema": "level-match/1", "at": time.time(), "lines": lines, "offset_db": 0.0}
    if not alert_dbs:
        rec.update(outcome="no_sound", why="this computer has no alert sound to compare with")
        return rec
    if not line_dbs:
        rec.update(outcome="no_lines", why="none of her lines was heard at the microphone")
        return rec
    a, ls = list(alert_dbs), list(line_dbs)
    fl = floor_db(a + ls)
    adb, an = active_db(a, fl)
    ldb, ln = active_db(ls, fl)
    rec.update(floor_db=round(fl, 1), alert_db=None if adb is None else round(adb, 1),
               line_db=None if ldb is None else round(ldb, 1), alert_windows=an, line_windows=ln)
    if adb is None or ldb is None or min(adb, ldb) - fl < SNR_MIN_DB:
        rec.update(outcome="quiet", why="too close to the room's floor to compare (turn the speakers up and run Voice ID again)")
        return rec
    off = max(-CAP_DB, min(CAP_DB, adb - ldb))
    rec.update(outcome="measured", raw_db=round(adb - ldb, 1), offset_db=round(off, 1))
    return rec


def write(home: Path | str, rec: dict) -> Path | None:
    p = path(home)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        return p
    except OSError:
        return None


def read(home: Path | str) -> dict:
    try:
        d = json.loads(path(home).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def offset_db(home: Path | str, env: dict | None = None) -> float:
    """What house-speak adds to her target: the measured offset, or 0 (none, quiet, off, the house)."""
    if not on(env):
        return 0.0
    rec = read(home)
    if rec.get("outcome") != "measured":
        return 0.0
    try:
        return max(-CAP_DB, min(CAP_DB, float(rec.get("offset_db") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def said(rec: dict) -> str:
    """One line for Setup's card."""
    o = rec.get("outcome")
    if o == "measured":
        off = float(rec.get("offset_db") or 0.0)
        if abs(off) < 0.5:
            return "Her voice: already at this computer's own sound level."
        return "Her voice: set %s dB %s to match this computer's sounds." % (("%.0f" % abs(off)),
                                                                            "louder" if off > 0 else "softer")
    if o == "quiet":
        return "Her voice level: too quiet to compare with this computer's sounds; turn the speakers up and run Voice ID again."
    if o == "no_sound":
        return "Her voice level: this computer has no alert sound to compare with; it stays at her own level."
    return ""


def record_alert(site, record_until, sink: str | None, tmp_dir: Path, env: dict | None = None) -> Path | None:
    """Play this computer's alert and record it on the raw microphone the way her lines were (record_until is
    mouth_prints._record_until). The wav, or None when nothing could be played or recorded."""
    import threading
    sound = system_sound(env)
    if sound is None:
        return None
    wav = Path(tmp_dir) / "level-alert.wav"
    stop, err = threading.Event(), []

    def rec() -> None:
        try:
            record_until(wav, site, stop, ALERT_PLAY_S + 4.0)
        except Exception as e:  # noqa: BLE001
            err.append(repr(e)[:160])
    th = threading.Thread(target=rec, name="level-alert-rec", daemon=True)
    th.start()
    time.sleep(0.4)
    played = play_system_sound(sound, sink)
    time.sleep(ALERT_PLAY_S - 0.4 if played else 0.2)
    stop.set()
    th.join(timeout=8.0)
    if not played or err or not wav.is_file():
        print("  level: the alert was not %s (%s)" % ("played" if not played else "recorded",
                                                      err[0] if err else sound), flush=True)
        return None
    return wav
