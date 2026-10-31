"""Self-explaining room audio: tones + optional spoken coach.

Cue language (always the same across offices):

  LONG mid beep   = GO — start speaking when the tone *ends* (the same tone on every take)
  SHORT high chirp = STOP — record window closed
  TWO tones (low→high) = SESSION OVER

The GO beep follows the coach's last word by BEEP_GAP_S (0.30 s), whatever ran between them.

Spoken coach (Cosy when up) restates this once per session and names each take.
"""

from __future__ import annotations

import array
import json
import math
import os
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path
from typing import TYPE_CHECKING

# Cancel must stop enroll paplay. Cosy/Crissa playback does not use these prefixes.
_AUDIO_STOP = threading.Event()
_ENROLL_AUDIO_PATS = ("enroll-beep-", "enroll-ann-", "enroll-say-", "enroll-host-")

if TYPE_CHECKING:
    from .site import Site


def request_audio_stop() -> list[str]:
    """Kill enroll beep/announcer paplay. Does not touch Cosy."""
    _AUDIO_STOP.set()
    hit: list[str] = []
    for pat in _ENROLL_AUDIO_PATS:
        r = subprocess.run(["pgrep", "-af", pat], capture_output=True, text=True)
        for line in (r.stdout or "").splitlines():
            hit.append(line[:160])
        subprocess.run(["pkill", "-TERM", "-f", pat], capture_output=True)
    time.sleep(0.12)
    for pat in _ENROLL_AUDIO_PATS:
        subprocess.run(["pkill", "-KILL", "-f", pat], capture_output=True)
    return hit


def clear_audio_stop() -> None:
    _AUDIO_STOP.clear()


def audio_stopped() -> bool:
    return _AUDIO_STOP.is_set()


# SETUP'S CUES, SPOKEN OR TEXT ONLY (the User 2026-10-10, .101 round 4: the cues always went to the speakers, at
# night too). Init keeps the choice in <state home>/setup-cues.json ({"mode": "text"}, lib/crissa_init.set_cues);
# CRISSA_SETUP_CUES=text|spoken overrides it. Text only: no tone, no host line, no spoken coach line, and a muted
# output is never lifted - each beat waits as long as its sound would have, so the take keeps its timing; the words
# are on the screen. The house never reads it (its room keeps its sound).
def _state_home():
    env = os.environ.get("CRISSA_HOME")
    if env:
        return Path(env).expanduser()
    try:
        from .. import paths as _p
        got = getattr(_p, "STATE", None) or getattr(_p, "HOME", None)
        return Path(got) if got else None
    except Exception:  # noqa: BLE001
        return None


def text_only() -> bool:
    env = os.environ.get("CRISSA_SETUP_CUES", "").strip().lower()
    if env in ("text", "spoken"):
        return env == "text" and not _house()
    home = _state_home()
    if home is None or _house():
        return False
    try:
        return str(json.loads((home / "setup-cues.json").read_text(encoding="utf-8")).get("mode") or "") == "text"
    except (OSError, ValueError, AttributeError):
        return False


def _wav_seconds(path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:  # noqa: BLE001
        return 0.0


def _quiet_wait(seconds: float) -> None:
    """Text only: the beat's time, no sound (a cancel ends it)."""
    end = time.time() + max(0.0, min(float(seconds), 30.0))
    while time.time() < end and not audio_stopped():
        time.sleep(0.05)


def default_sink(site: Site | None = None) -> str:
    if site and site.sink:
        return site.sink
    # CRISSA_ROOM_SINK (2026-10-19): the test suite points every tone at its null sink; unset = the room's default.
    env_sink = os.environ.get("CRISSA_ROOM_SINK", "").strip()
    if env_sink:
        return env_sink
    r = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True)
    return (r.stdout or "").strip()


def _unmute_sink(sink: str) -> None:
    """The house's: unmute + 100 % (kept for its callers). A pack never calls it (see _sink_open)."""
    if not sink or not _house():
        return
    subprocess.run(["pactl", "set-sink-mute", sink, "0"], capture_output=True)
    subprocess.run(["pactl", "set-sink-volume", sink, "100%"], capture_output=True)


# THE PERSON'S VOLUME IS THEIRS (the User 2026-10-10, .101 U: "the speakers jumped loud at every step"). The house sets
# its own room sink to 100 % for a tone and 120 % for a spoken cue (HOUSE_ENROLL_SPEAK_VOL) and never puts it back,
# which is right for its room and wrong on anyone's computer. In a pack nothing here touches the system volume: the cue
# audio itself is levelled (CUE_PEAK of full scale, HOUSE_ENROLL_CUE_PEAK), and a muted output is lifted only for the
# cue and muted again after it.
CUE_PEAK = float(os.environ.get("HOUSE_ENROLL_CUE_PEAK", "0.5") or 0.5)


def _house() -> bool:
    try:
        from .. import paths as _p
        return bool(_p.is_house())
    except Exception:  # noqa: BLE001
        return False


def _sink_muted(sink: str) -> bool:
    try:
        out = subprocess.run(["pactl", "get-sink-mute", sink], capture_output=True, text=True, timeout=3).stdout
    except Exception:  # noqa: BLE001
        return False
    return "yes" in (out or "").lower()


class _sink_open:
    """with _sink_open(sink): the cue is audible. House: today's unmute + 100 %. Pack: the volume is never set; a muted
    output is unmuted for the cue and muted again after it."""

    def __init__(self, sink: str | None):
        self.sink, self.lifted = str(sink or ""), False

    def __enter__(self):
        if not self.sink:
            return self
        if _house():
            subprocess.run(["pactl", "set-sink-mute", self.sink, "0"], capture_output=True)
            subprocess.run(["pactl", "set-sink-volume", self.sink, "100%"], capture_output=True)
            return self
        if _sink_muted(self.sink):
            subprocess.run(["pactl", "set-sink-mute", self.sink, "0"], capture_output=True)
            self.lifted = True
        return self

    def __exit__(self, *exc):
        if self.lifted:
            subprocess.run(["pactl", "set-sink-mute", self.sink, "1"], capture_output=True)
        return False


def _room_volume(sink: str | None) -> None:
    """The house's spoken-cue volume (HOUSE_ENROLL_SPEAK_VOL, 120 %); nothing at all in a pack."""
    if not sink or not _house():
        return
    subprocess.run(["pactl", "set-sink-mute", sink, "0"], capture_output=True)
    vol = os.environ.get("HOUSE_ENROLL_SPEAK_VOL", "120%")
    subprocess.run(["pactl", "set-sink-volume", sink, vol], capture_output=True)


def _level_pcm(pcm: bytes, sr: int | None = None, speaker: str | None = None) -> bytes:   # sr, speaker: the id copy's
    """A pack's spoken cue at CUE_PEAK of full scale (never above it, at most four times louder); the house's unchanged."""
    if _house() or not pcm:
        return pcm
    import array
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    peak = max((abs(x) for x in a), default=0)
    if peak < 64:
        return pcm
    gain = min(4.0, (CUE_PEAK * 32767.0) / peak)
    return array.array("h", (max(-32768, min(32767, int(x * gain))) for x in a)).tobytes()


# THE BEEP FOLLOWS HER LAST WORD (the User 2026-10-11, after `house-enroll voice`: "less time from her saying her last
# word and the beep would prevent false starts"). Measured on a private null sink through this module's own paplay
# path (docs/checkpoints/enroll-cues-20261011): her last sound to the GO beep was 0.31-0.32 s on the
# coach's TTS lines (0.15 s of the TTS's own trailing silence, the 0.12 s pause, ~0.04 s of playback) and 3.2 s on
# Lila's Register lines (her rendered clips carry 3.15-3.19 s of silence after the last word). A line before a beep
# is now cut TAIL_KEEP_S after its last sound (trim_tail), its end is stamped (mark_spoken), and the GO tone starts
# BEEP_GAP_S after that last sound (wait_for_beep): measured 0.31 s on both. All that is cut is 40-46 dB under the
# line's peak on the TTS lines and digital zero on Lila's, and a room's echo decays 60 dB per RT60, so at 0.30 s it
# is 36 dB down even at RT60 0.5 s (an estimate: nothing was played aloud or recorded). The record window opens the
# same 0.62 s after the beep's start as before.
# Text only keeps the same timing (each beat waits as its sound would have).
# Kill: HOUSE_ENROLL_BEEP_GAP=0 (no trim, no wait: the old timing). HOUSE_ENROLL_BEEP_GAP_S sets the gap.
BEEP_GAP_S = 0.30
TONE_START_S = 0.015       # paplay start -> the tone's audible onset on the sink (measured 2026-10-11: 10-20 ms)
TAIL_KEEP_S = 0.03         # what is kept of a line after its last sound, faded out (never a click)
TAIL_FLOOR_DB = 45.0       # "her last sound": the last 5 ms frame within 45 dB of the line's loudest sample
_SPOKE_END = [0.0]         # time.monotonic() at which the last trimmed line's last sound was played


def beep_gap_on() -> bool:
    return os.environ.get("HOUSE_ENROLL_BEEP_GAP", "1").strip().lower() not in ("0", "off", "false", "no")


def beep_gap_s() -> float:
    try:
        v = float(os.environ.get("HOUSE_ENROLL_BEEP_GAP_S", "") or BEEP_GAP_S)
    except ValueError:
        v = BEEP_GAP_S
    return max(0.0, min(v, 2.0))


def trim_tail(pcm: bytes, sr: int, *, keep_s: float = TAIL_KEEP_S) -> tuple[bytes, float]:
    """s16le mono cut keep_s after its last sound, the kept part faded out. Returns (pcm, seconds kept after the last
    sound). Silence (or nothing loud) comes back unchanged with 0.0."""
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    n = len(a)
    if n == 0:
        return pcm, 0.0
    peak = max(max(a), -min(a))
    if peak < 64:
        return pcm, 0.0
    thr = peak * (10.0 ** (-TAIL_FLOOR_DB / 20.0))
    hop = max(1, int(sr * 0.005))
    end = 0
    i = n
    while i > 0:
        lo = max(0, i - hop)
        fr = a[lo:i]
        if max(fr) > thr or -min(fr) > thr:
            end = i
            break
        i = lo
    if end <= 0:
        return pcm, 0.0
    cut = min(n, end + int(sr * max(0.0, keep_s)))
    out = a[:cut]
    tail = cut - end
    for k in range(tail):
        out[end + k] = int(out[end + k] * (1.0 - (k + 1) / float(tail)))
    return out.tobytes(), tail / float(sr)


def trim_wav(src, dst, *, keep_s: float = TAIL_KEEP_S) -> float:
    """A WAV (s16 mono) written to dst cut keep_s after its last sound (trim_tail); returns the seconds kept after it."""
    with wave.open(str(src), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        pcm = w.readframes(w.getnframes())
    if ch != 1 or sw != 2:
        raise ValueError(f"trim_wav wants s16 mono, {src} is {ch} ch x {sw * 8} bit")
    out, kept = trim_tail(pcm, sr, keep_s=keep_s)
    tmp = Path(str(dst) + ".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(out)
    os.replace(tmp, dst)
    return kept


def mark_spoken(kept_s: float) -> None:
    """A trimmed line just finished playing: its last sound was kept_s ago."""
    _SPOKE_END[0] = time.monotonic() - max(0.0, float(kept_s))


def wait_for_beep(lead_s: float = 0.0) -> float:
    """Sleep until the GO beep is due: beep_gap_s() after her last sound, less lead_s (the work still to run before
    the tone is heard). Returns the seconds waited; nothing to wait for (no trimmed line, already late, switched
    off) returns 0.0 at once."""
    end = _SPOKE_END[0]
    if not end or not beep_gap_on():
        return 0.0
    gap = beep_gap_s()
    wait = end + gap - max(0.0, float(lead_s)) - time.monotonic()
    if wait <= 0 or wait > gap:
        return 0.0
    deadline = time.monotonic() + wait
    while not audio_stopped():
        left = deadline - time.monotonic()
        if left <= 0:
            break
        time.sleep(min(left, 0.05))
    return wait


def same_go() -> bool:
    """Every GO beep is go_beep (the User 2026-10-11: "her second beeps differ from first"); Register's count take
    used count_tick to open and done_chirp to close. Kill: HOUSE_ENROLL_SAME_GO=0 (tick + chirp on the count)."""
    return os.environ.get("HOUSE_ENROLL_SAME_GO", "1").strip().lower() not in ("0", "off", "false", "no")


def play_tone(
    seconds: float,
    freq: float,
    amp: float = 0.55,
    sink: str | None = None,
    *,
    after_voice: bool = False,
) -> None:
    """after_voice: a GO beep - it starts beep_gap_s() after the last trimmed line's last sound (wait_for_beep, after
    the tone is built so only paplay's start is left to run)."""
    if audio_stopped():
        return
    if text_only():
        if after_voice:
            wait_for_beep(TONE_START_S)
        _quiet_wait(seconds)
        return
    sink = sink or default_sink()
    if not _house():
        amp = min(float(amp), CUE_PEAK) / 1.4          # the tone's two partials peak at 1.4 x amp: CUE_PEAK at most
    sr = 24000
    n = int(sr * seconds)
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="enroll-beep-")
    os.close(fd)
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            for i in range(n):
                t = i / sr
                v = amp * math.sin(2 * math.pi * freq * t)
                v += (amp * 0.4) * math.sin(2 * math.pi * (freq * 1.5) * t)
                if i < sr * 0.03:
                    v *= i / (sr * 0.03)
                if i > n - sr * 0.05:
                    v *= max(0.0, (n - i) / (sr * 0.05))
                w.writeframes(struct.pack("<h", int(max(-1, min(1, v)) * 32000)))
        env = os.environ.copy()
        cmd = ["paplay", path]
        if sink:
            env["PULSE_SINK"] = sink
            cmd = ["paplay", f"--device={sink}", path]
        if after_voice:
            wait_for_beep(TONE_START_S)
        if audio_stopped():
            return
        with _sink_open(sink):
            subprocess.run(cmd, capture_output=True, env=env, timeout=15)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def speak_announcer(text: str) -> bool:
    """Audio8 announcer for voice-register. Not Cosy. Bypasses PA category gate (a refusal is never muted, held or
    deferred for a busy mic). It sounds like the house announcer (the User 2026-10-03: the deny came 'in announcer
    #2's voice'): the same clone, then the announcer's own level (boost_pa_wav) and tempo (ANNOUNCE_SPEED) - the
    raw synth skipped both. Kill: HOUSE_ENROLL_ANNOUNCER_RAW=1."""
    text = " ".join((text or "").split())
    if not text:
        return False
    if text_only():
        print(f"  (text only, not spoken) {text}", flush=True)
        return True
    try:
        import sys

        from .paths import rails_path

        lib = rails_path("lib")
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        import house_announce as ha
        from house_announce import _paplay, synth_audio8

        if audio_stopped():
            return False
        wav = synth_audio8(text[:200])
        raw = os.environ.get("HOUSE_ENROLL_ANNOUNCER_RAW", "").strip().lower() in ("1", "on", "true", "yes")
        if not raw and len(wav) >= 500:
            wav = ha.boost_pa_wav(wav)
        if audio_stopped():
            return False
        if len(wav) < 500:
            print(f"  announcer short wav {len(wav)}", flush=True)
            return False
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="enroll-ann-")
        os.close(fd)
        sped = path + ".sped.wav"
        try:
            Path(path).write_bytes(wav)
            play = path
            speed = float(getattr(ha, "SPEED", 1.0) or 1.0)
            if not raw and abs(speed - 1.0) > 0.01:
                r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-af", f"atempo={speed}", sped],
                                   capture_output=True, timeout=60)
                if r.returncode == 0:
                    play = sped
            if audio_stopped():
                return False
            ok = _paplay(play)
            return bool(ok[0] if isinstance(ok, tuple) else ok)
        finally:
            for f in (path, sped):
                try:
                    os.unlink(f)
                except OSError:
                    pass
    except Exception as e:
        print(f"  announcer failed: {e!r}", flush=True)
        return False


def play_wav(path, *, sink: str | None = None, timeout: float = 60.0, spoken_tail: float | None = None) -> bool:
    """A rendered host line (the wall host's Lila / Crissa clips, audio
    extracted to ~/.cache/house-enroll/host/enroll-host-*.wav): paplay to the
    room sink, blocking until it ends. Cancel kills it by the file prefix.
    spoken_tail: the file was cut that long after her last sound (trim_wav) -
    stamp it so the next GO beep keeps BEEP_GAP_S from her last word."""
    if audio_stopped():
        return False
    if text_only():
        _quiet_wait(_wav_seconds(path))
        if spoken_tail is not None and not audio_stopped():
            mark_spoken(spoken_tail)
        return True
    sink = sink or default_sink()
    env = os.environ.copy()
    cmd = ["paplay", str(path)]
    if sink:
        env["PULSE_SINK"] = sink
        cmd = ["paplay", f"--device={sink}", str(path)]
    try:
        with _sink_open(sink):
            r = subprocess.run(cmd, capture_output=True, env=env, timeout=timeout)
        if spoken_tail is not None and r.returncode == 0 and not audio_stopped():
            mark_spoken(spoken_tail)
        return r.returncode == 0
    except Exception as e:
        print(f"  host wav failed: {e!r}", flush=True)
        return False


def go_beep(site: Site, seconds: float | None = None, *, settle: bool = True) -> None:
    """Mid tone = GO after it ends. Short — enroll is pace-sensitive. The same
    tone on every take; it starts BEEP_GAP_S after a trimmed line's last sound.
    settle=False: no 0.12 s after the tone (a recorder already running, round 33)."""
    sec = seconds if seconds is not None else site.beep_sec
    if sec is None or sec <= 0:
        sec = 0.4
    play_tone(min(float(sec), 0.55), 880.0, amp=0.85, sink=site.sink, after_voice=True)
    if settle:
        time.sleep(0.12)


def done_chirp(site: Site) -> None:
    """SHORT high chirp = window closed."""
    play_tone(0.18, 1320.0, amp=0.75, sink=site.sink)


def count_tick(site: Site) -> None:
    """The old opener of Register's look-away count; since 2026-10-11 every GO is
    go_beep (same_go), and this plays only under HOUSE_ENROLL_SAME_GO=0."""
    play_tone(0.12, 523.0, amp=0.7, sink=site.sink)
    time.sleep(0.08)


def success_ding(site: Site) -> None:
    """One take is in — not another GO beep."""
    play_tone(0.10, 1760.0, amp=0.55, sink=site.sink)
    time.sleep(0.05)
    play_tone(0.16, 2340.0, amp=0.62, sink=site.sink)
    time.sleep(0.08)


def session_end(site: Site) -> None:
    """TWO tones = whole session finished."""
    play_tone(0.18, 660.0, amp=0.7, sink=site.sink)
    time.sleep(0.08)
    play_tone(0.28, 990.0, amp=0.8, sink=site.sink)


# The User 2026-08-06: enroll TTS sounded like connect/disconnect + multi-second
# gaps between "look at the camera and say" and the line — each speak_loud was
# a full Cosy RTT (~5–9s under load) + paplay open/close. Cache + join fixes it.
_PCM_CACHE: dict[tuple[str, str], tuple[bytes, int]] = {}


def _cosy_pcm(
    text: str,
    site: Site,
    timeout: float = 60.0,
    voice: str | None = None,
) -> tuple[bytes, int] | None:
    text = " ".join((text or "").split())
    if not text:
        return None
    voice = str(voice or getattr(site, "speak_voice", None) or "announcer")
    key = (voice, text)
    if key in _PCM_CACHE:
        return _PCM_CACHE[key]
    try:
        body = json.dumps({"text": text[:800], "voice": voice}).encode()
        req = urllib.request.Request(
            site.speak_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            sr = int(resp.headers.get("X-Sample-Rate") or 24000)
            raw = resp.read()
        if b"\r\n" in raw[:32]:
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
                i = i + n + 2
            raw = bytes(out)
        if len(raw) < 500:
            return None
        _PCM_CACHE[key] = (raw, sr)
        return raw, sr
    except Exception:
        return None


def _silence_pcm(sr: int, ms: float) -> bytes:
    n = max(0, int(sr * (ms / 1000.0)))
    return b"\x00\x00" * n


def _paplay_pcm(pcm: bytes, sr: int, sink: str | None, *, speaker: str | None = None,
                spoken_tail: float | None = None, on_play=None) -> None:
    """spoken_tail: the line was cut that long after its last sound (trim_tail); its end is stamped for the GO beep.
    on_play(t, pcm, sr) (round 17, 2026-10-13: Setup's lip sync times her six lines): called with time.monotonic() just
    before the player starts, and the exact samples it is given."""
    sink = sink or default_sink()
    pcm = _level_pcm(pcm)
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="enroll-say-")
    os.close(fd)
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm)
        env = os.environ.copy()
        cmd = ["paplay", path]
        if sink:
            env["PULSE_SINK"] = sink
            cmd = ["paplay", f"--device={sink}", path]
        if audio_stopped():
            return
        with _sink_open(sink):
            if on_play is not None:
                try:
                    on_play(time.monotonic(), pcm, sr)
                except Exception:  # noqa: BLE001  a timer never stops the line
                    pass
            r = subprocess.run(cmd, capture_output=True, env=env, timeout=120)
        if spoken_tail is not None and r.returncode == 0 and not audio_stopped():
            mark_spoken(spoken_tail)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def speak_joined(
    site: Site,
    parts: list[str],
    *,
    gap_ms: float = 220.0,
    also_print: bool = True,
) -> None:
    """One continuous paplay: synth each part (cached), stitch with short silence.

    Avoids multi-second dead air between enroll instruction and the sentence
    (was: two full Cosy + two paplay open/close cycles).
    """
    cleaned = [" ".join(str(p).split()) for p in parts if str(p).strip()]
    if not cleaned:
        return
    if also_print:
        for t in cleaned:
            print(f"  🔊 {t}", flush=True)
    if not site.speak_enabled:
        return
    if text_only():
        _quiet_wait(min(12.0, 0.9 + 0.06 * sum(len(t) for t in cleaned)))
        return
    _room_volume(site.sink or default_sink(site))

    # Prefetch all PCMs first so any Cosy wait happens before audio starts
    chunks: list[tuple[bytes, int]] = []
    for t in cleaned:
        pcm = _cosy_pcm(t, site)
        if pcm:
            chunks.append(pcm)
    if not chunks:
        # fallback: one spd-say blob
        _spd_say(" ".join(cleaned))
        return
    # the line ends on its last word: the TTS's own trailing silence (0.15-0.24 s measured) is cut, and the end is
    # stamped so the GO beep that follows keeps BEEP_GAP_S from it (the User 2026-10-11)
    tail: float | None = None
    if beep_gap_on():
        last, last_sr = chunks[-1]
        cut, tail = trim_tail(last, last_sr)
        chunks[-1] = (cut, last_sr)
    sr = chunks[0][1]
    buf = bytearray()
    for i, (raw, r) in enumerate(chunks):
        last_one = i == len(chunks) - 1
        if r != sr:
            # rare rate mismatch — play separately rather than corrupt
            if buf:
                _paplay_pcm(bytes(buf), sr, site.sink)
                buf = bytearray()
            _paplay_pcm(raw, r, site.sink, spoken_tail=(tail if last_one else None))
            sr = r
            continue
        if i and gap_ms > 0:
            buf.extend(_silence_pcm(sr, gap_ms))
        buf.extend(raw)
    if buf:
        _paplay_pcm(bytes(buf), sr, site.sink, spoken_tail=tail)


def _spd_say(text: str) -> bool:
    try:
        r = subprocess.run(
            ["spd-say", "-w", text[:500]],
            capture_output=True,
            timeout=60,
        )
        return r.returncode == 0
    except Exception:
        return False


def setup_cue_voice() -> str:
    """Lila's voice for every cue in Setup's first run (CRISSA_SETUP_CUE_VOICE, which the coach sets for it), else ''.
    CRISSA_SETUP_ONE_VOICE=0: '' (the announcer, as before)."""
    if os.environ.get("CRISSA_SETUP_ONE_VOICE", "1").strip().lower() in ("0", "off", "false", "no"):
        return ""
    return str(os.environ.get("CRISSA_SETUP_CUE_VOICE") or "").strip().lower()


def speak(
    site: Site, text: str, *, also_print: bool = True, voice: str | None = None
) -> None:
    """Coach line to the room. Cosy → spd-say → print only.

    voice=crissa is Register's intro — do not fall back to Audio8/spd-say. No voice named: Setup's one voice when the
    coach was started for Setup's first run (setup_cue_voice, the User 2026-10-13), else the site's (the announcer).
    """
    if voice is None:
        voice = setup_cue_voice() or None
    text = " ".join(text.split())
    if also_print:
        print(f"  🔊 {text}", flush=True)
    if not site.speak_enabled or not text:
        return
    if text_only():
        _quiet_wait(min(12.0, 0.9 + 0.06 * len(text)))
        return
    # Room volume up for door-distance / blind enroll
    _room_volume(site.sink or default_sink(site))
    who = str(voice or "").strip().lower()
    pcm = _cosy_pcm(text, site, voice=voice)
    if pcm:
        _paplay_pcm(pcm[0], pcm[1], site.sink)
        return
    if who in ("crissa", "zoe"):
        print("  cosy crissa failed — not falling back to announcer", flush=True)
        return
    if _spd_say(text):
        return
    # last resort: silent (already printed)


def speak_loud(site: Site, text: str, *, pause: float = 0.45) -> None:
    """Loud room command — short sentences for people who cannot see the screen."""
    speak(site, text, also_print=True)
    if pause > 0:
        time.sleep(pause)


def speak_command(site: Site, *lines: str, gap: float = 0.55) -> None:
    """Several short LOUD lines with gaps (blind-at-door friendly)."""
    for line in lines:
        line = " ".join(str(line).split())
        if not line:
            continue
        speak_loud(site, line, pause=gap)


# Speaker embed is PCM only (WeSpeaker). The cue is never matched as text —
# it only has to be easy to say and fill the window with voiced audio.
#
# Facing (screen readable): vowel-complete, ~8 s. Measured 2026-08-24
# (COMMS-GROK-ENROLL-QUALITY): 15/15 English vowels.
# Turn / away: nobody recites a paragraph at 45°. Count 1–10 between beeps.
# 2026-09-11: the fifteen-second register — a six-second sentence (still
# vowel-complete, still "my name is {name}") and a four-second count.
DESK_LINE_FRONT = (
    "My name is {name}, and this is my ordinary voice. "
    "The house should know me now, even in the dark."
)
DESK_LINE_TURN = "One two three four five six."

# Door / blind: repeat-after, same count plus two short memorable lines.
BLIND_VOICE_LINES = [
    "One two three four five. Six seven eight nine ten.",
    "My name is {name}.",
    "The house knows my voice.",
]
BLIND_VOICE_LINE = BLIND_VOICE_LINES[0]
BLIND_LEGEND = "After the beep, say the sentence."

# Where the person should be, spoken (the User 2026-08-14). The User enrolls from
# his own seat because that is where the mic will hear him every day after
# this; a guest enrolls where they actually stand, for the same reason. An
# enrollment taken somewhere nobody sits is a bank print that never matches.
USER_SEAT_HOUR = 6


def role_and_seat(site, person_id: str) -> tuple[str, int | None]:
    """Is this the User or a guest, and where do they normally sit?

    Read from site.json rather than guessed, so the spoken placement matches the
    seat the mic will actually hear them from every day after enrollment.
    """
    try:
        raw = getattr(site, "raw", {}) or {}
        st = raw.get("stations") or {}
        uid = str((raw.get("enroll") or {}).get("user_id") or "").strip().lower()
        pid = str(person_id or "").strip().lower()
        if pid and pid == uid:
            return "user", int(st.get("user_beam") or USER_SEAT_HOUR)
        for row in st.get("regulars") or []:
            if str(row.get("person_id") or "").strip().lower() == pid:
                hb = row.get("home_beam")
                return "guest", (int(hb) if hb is not None else None)
        for hour, meta in (st.get("sitters") or {}).items():
            if str((meta or {}).get("usual") or "").strip().lower() == pid:
                return "guest", int(hour)
    except (AttributeError, TypeError, ValueError):
        pass
    return "guest", None


def placement_line(*, role: str = "user", seat: int | None = None) -> str:
    """One spoken sentence telling this person where to be."""
    r = str(role or "user").strip().lower()
    if r in ("user", "owner"):
        return "Take your usual seat, facing the screen."
    if seat:
        return f"Please stand where you usually are, around {seat} o'clock."
    return "Please stand where you usually are."


def precache_enroll_lines(site: Site, lines: list[str] | None = None) -> None:
    """Warm Cosy cache for fixed enroll phrases (call once at session start)."""
    fixed = lines or [
        "Please look at the camera.",
        "Please look to the left.",
        "Done with face.",
        "Now voice. Look at the camera and say:",
        "Look to the left and say:",
        "Got it.",
        "Microphone silent. Check the camera mic. Skipping this take.",
        "Too quiet. Skip.",
        BLIND_VOICE_LINE,
    ]
    for t in fixed:
        try:
            _cosy_pcm(t, site, timeout=90.0)
        except Exception:
            pass


def speak_legend(site: Site) -> None:
    """No spoken legend in fast path — print only."""
    print("\n=== AUDIO: beep = GO, chirp = stop ===\n", flush=True)


def announce_wave(site: Site) -> None:
    speak_loud(site, "Wave!", pause=0.3)


def announce_sit_place(site: Site, *, at_desk: bool) -> None:
    """Optional seat cue — skipped in fast quality session."""
    _ = site
    _ = at_desk


def announce_hold_still(site: Site, *, at_desk: bool = False) -> None:
    """Face snap #1 — look at camera."""
    _ = at_desk
    speak_loud(site, "Please look at the camera.", pause=0.25)


def announce_turn_profile(site: Site, *, at_desk: bool = False) -> None:
    """Face snap #2 — look left."""
    _ = at_desk
    speak_loud(site, "Please look to the left.", pause=0.25)


def announce_look_right(site: Site) -> None:
    speak_loud(site, "Please look to the right.", pause=0.25)


def announce_voice_line(
    site: Site,
    line: str | None = None,
    *,
    at_desk: bool = False,
    role: str = "user",
    seat: int | None = None,
    place: bool = True,
) -> None:
    """Voice ON-ANGLE — placement + instruction + sentence as ONE clip.

    Joined deliberately: Cosy leaves a gap between separate calls, and a gap in
    the middle of "say: the quick brown fox" is where people start talking over
    the prompt.
    """
    _ = at_desk
    line = line or BLIND_VOICE_LINE
    parts = []
    if place:
        parts.append(placement_line(role=role, seat=seat))
    parts += ["Now your voice. Look at the camera and say:", line]
    speak_joined(site, parts, gap_ms=180.0)
    time.sleep(0.12)


# SHE SAYS THE SENTENCE ONCE (the User 2026-10-11: "Except for her saying the sentence twice. that second time could
# go"). The turned-away take asks for it again without reading it again; the sentence stays on the coach screen (the
# take's read card). Kill: HOUSE_ENROLL_SAY_ONCE=0 (the sentence is read again on the second take).
OFF_ANGLE_PROMPT = "Now stay where you are and turn away from the camera. Say it again."
OFF_ANGLE_PROMPT_REREAD = "Now stay where you are and turn away from the camera. Say it again:"


def say_once() -> bool:
    return os.environ.get("HOUSE_ENROLL_SAY_ONCE", "1").strip().lower() not in ("0", "off", "false", "no")


def off_angle_parts(line: str | None = None) -> list[str]:
    """What the turned-away take says: the prompt alone, or (kill switch) the prompt and the sentence again."""
    if say_once():
        return [OFF_ANGLE_PROMPT]
    return [OFF_ANGLE_PROMPT_REREAD, line or BLIND_VOICE_LINE]


def announce_voice_off_angle(
    site: Site,
    line: str | None = None,
    *,
    at_desk: bool = False,
    role: str = "user",
    seat: int | None = None,
) -> None:
    """Voice OFF-ANGLE — the same sentence, said away from the camera.

    Two takes on purpose: every real turn afterwards is off-angle. A bank built
    only from someone speaking straight into the lens matches worst exactly when
    it is needed. The sentence is not read a second time (off_angle_parts).
    """
    _ = (at_desk, role, seat)
    speak_joined(site, off_angle_parts(line), gap_ms=180.0)
    time.sleep(0.12)


def teach_sentence_then_beep(site: Site, line: str | None = None) -> None:
    """Legacy: sentence alone. Prefer announce_voice_* which already includes it."""
    line = line or BLIND_VOICE_LINE
    # If already spoken as part of announce_voice_*, caller should skip this.
    # Kept for sectioned_session / other paths that only teach the line.
    speak_loud(site, line, pause=0.15)


def announce_stop(site: Site) -> None:
    """Silent end-of-window in fast path (chirp is enough)."""
    _ = site


def announce_section_voice(site: Site) -> None:
    speak_loud(site, "Done with face.", pause=0.25)


def announce_enroll_complete(site: Site, name: str = "") -> None:
    if name:
        speak_loud(site, f"ID initialization complete. Thanks, {name}.", pause=0.2)
    else:
        speak_loud(site, "ID initialization complete.", pause=0.2)
