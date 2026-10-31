"""Baked clip audio for the conductor.

Host oneshots carry the line in the mp4 (`host.speech` is the window).
The wizard and the register paplay that wav themselves next to `/v1/play`.
Composer / house-mode / the bag only POST play — without this, the
picture rolls and the line is silent (the User 2026-09-13, `lila-ob-sound`
on the Composer live stage).
"""
from __future__ import annotations

import os
import sys
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
WAV_DIR = Path(
    os.environ.get(
        "HOUSE_CLIP_WAV_DIR",
        str(Path.home() / ".cache" / "house-conductor" / "clip-wav"),
    )
)

_proc: Optional[subprocess.Popen] = None


from audio_sink import _za  # one shared shim gate (was duplicated here)


def _fallback_sink() -> str:
    """When audio_sink cannot answer: this machine's canceller sink (house config aec_sink), else the
    system default (a pack names none - 2026-10-06)."""
    try:
        import crissa_home
        return crissa_home.house_value("aec_sink") or "@DEFAULT_SINK@"
    except (ImportError, AttributeError):
        return "@DEFAULT_SINK@"


def _shim_sink(za) -> str | None:
    """The sink under the shim, or None = stay silent. A real VOICE_SINK that
    is not loaded refuses (the film plays without its track) instead of
    falling through to the room; empty / the canceller sink / auto keep
    speak_sink's order."""
    w = (os.environ.get("VOICE_SINK") or "").strip()
    try:
        if w and w not in (_fallback_sink(), "auto"):
            return za.resolve_sink(w)
        from audio_sink import speak_sink
        return speak_sink()
    except Exception:  # noqa: BLE001 — SinkNotFound included: silence, never the room
        return None


def _shim_play(za, wav: Path):
    """start_file in its OWN session (as the Popen always was, so stop() can
    take the group), then the boost pinned to its pid. None when refused."""
    sink = _shim_sink(za)
    if sink is None:
        return None
    try:
        h = za.start_file(wav, sink, own_session=True)
    except Exception:  # noqa: BLE001
        return None
    if h.pid is not None:
        threading.Thread(target=_shim_boost, args=(za, sink, h), daemon=True).start()
    return h


def _shim_boost(za, sink: str, h) -> None:
    try:
        za.boost_player(sink, pct=100, timeout_s=2.0, stream=h)
    except Exception:  # noqa: BLE001
        pass


def speech_window(row: dict[str, Any] | None) -> tuple[float, float] | None:
    """The baked line, or None when the file is silent / not a host take."""
    host = row.get("host") if isinstance(row, dict) else None
    if not isinstance(host, dict):
        return None
    sp = host.get("speech")
    if not (isinstance(sp, (list, tuple)) and len(sp) == 2):
        return None
    try:
        a, b = float(sp[0]), float(sp[1])
    except (TypeError, ValueError):
        return None
    if b <= a:
        return None
    return a, b


def clip_audio_on() -> bool:
    return os.environ.get("HOUSE_CONDUCTOR_CLIP_AUDIO", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


CLIP_MATCH_LOUD = os.environ.get("HOUSE_CLIP_MATCH_LOUD", "1").strip().lower() not in ("0", "false", "no", "off")


def clip_target_db() -> float:
    """The film's soundtrack lands on the house band (the User 2026-09-18: he
    asked for the CLIP to come up to the voices; 759651c8 pulled the voices
    down onto the clip instead). HOUSE_CLIP_TARGET_DB, else the house level."""
    return float(os.environ.get("HOUSE_CLIP_TARGET_DB", os.environ.get("HOUSE_LEVEL_TARGET_DB", "-26")))


def level_wav(wav: Path, target_db: float) -> float:
    """Raise a baked mono 16-bit wav's speech median to target_db (never cut,
    +12 dB cap, soft limiter). Returns the gain used; 1.0 = untouched."""
    import wave
    try:
        import sys as _sys
        if str(Path(__file__).resolve().parent) not in _sys.path:
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from loudness import gain_to, apply_gain
        with wave.open(str(wav), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                return 1.0
            sr = w.getframerate()
            pcm = w.readframes(w.getnframes())
        g = gain_to(pcm, sr, target_db, allow_cut=False)
        if 0.98 <= g <= 1.02:
            return 1.0
        out = apply_gain(pcm, g)
        tmp = wav.with_suffix(".tmp.wav")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(out)
        tmp.replace(wav)
        return g
    except Exception:
        return 1.0


def clip_wav(key: str, clip: Path) -> Path | None:
    """ffmpeg -vn once per clip mtime, then levelled to the house band
    (the target rides the cache name). None when there is no audio worth playing."""
    if not clip.is_file():
        return None
    WAV_DIR.mkdir(parents=True, exist_ok=True)
    target = clip_target_db()
    wav = WAV_DIR / (f"{key}.{int(round(target))}db.wav" if CLIP_MATCH_LOUD else f"{key}.wav")
    try:
        if wav.is_file() and wav.stat().st_mtime >= clip.stat().st_mtime and wav.stat().st_size > 4000:
            return wav
    except OSError:
        pass
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vn", "-ac", "1", "-ar", "48000",
         "-f", "wav", str(wav)],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0 or not wav.is_file() or wav.stat().st_size < 4000:
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    if CLIP_MATCH_LOUD:
        level_wav(wav, target)
    return wav


def stop() -> None:
    """Cut the clip's paplay. Safe if nothing is playing."""
    global _proc
    p = _proc
    _proc = None
    if p is None or p.poll() is not None:
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            p.terminate()
        except OSError:
            pass


def playing() -> bool:
    """A film's own baked line is playing now (start()'s player, alive). Read by /v1/state `film_sound` (round 15,
    2026-10-13: the desk Wall's build yields while a film speaks, as it does for her voice)."""
    p = _proc
    if p is None:
        return False
    try:
        return p.poll() is None
    except Exception:  # noqa: BLE001 - a handle that cannot say: nothing we know of is playing
        return False


def start(key: str, row: dict[str, Any] | None, clip: Path) -> bool:
    """Paplay the baked line. Stops any previous clip first. False = silent."""
    stop()
    if not clip_audio_on() or not speech_window(row):
        return False
    wav = clip_wav(key, clip)
    if wav is None:
        return False
    global _proc
    za = _za()
    if za is not None:
        _proc = _shim_play(za, wav)
        return _proc is not None
    try:
        from audio_sink import speak_sink
        sink = speak_sink()
    except Exception:
        sink = os.environ.get("VOICE_SINK") or _fallback_sink()
    try:
        _proc = subprocess.Popen(
            ["paplay", f"--device={sink}", str(wav)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        _proc = None
        return False
    try:
        from audio_sink import boost_paplay
        threading.Thread(target=boost_paplay, args=(sink,), kwargs={"pct": 100, "wait_s": 2.0}, daemon=True).start()
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# THE CALIBRATION PASSENGER (the User 2026-09-21: "play films track ... with sound")
#
# A calibration pass had no sound at all. The sheet is built from her own filmed
# mouth, so `drill_begin` returns before `drill_spawn` and no house-speak is ever
# spawned; the film is started with audio=False on top of that. The picture
# rolled in silence and there was nothing to judge it by.
#
# What plays here is the film's own track and nothing else. It is a PASSENGER:
# nothing reads it back, the sheet is still her filmed mouth, so it cannot move
# the pipeline number the pass exists to measure.
#
# Two things it must not do, both learned elsewhere:
#   - it must not be LEVELLED. house-speak's --wav branch stopped levelling the
#     drill's own voice on 2026-09-18 ("the volume boost hurt my ears"), and a
#     calibration pass has to sound like the drill passes beside it.
#   - it must not go through `start()`, whose `speech_window(row)` gate only
#     fires for host takes with a baked line. A harvest film has no such window.
# ---------------------------------------------------------------------------

def _atempo_chain(rate: float) -> str:
    """ffmpeg's atempo takes 0.5..100, so a slower rate is a chain of halves.

    0.25 is atempo=0.5,atempo=0.5 — the product is the rate. Empty string at 1x.
    """
    r = float(rate)
    if abs(r - 1.0) < 1e-6:
        return ""
    parts = []
    while r < 0.5 - 1e-9:
        parts.append(0.5)
        r *= 2.0
    parts.append(round(r, 6))
    return ",".join("atempo=%g" % p for p in parts)


def film_wav(key: str, clip: Path, rate: float = 1.0, level: bool = False) -> Path | None:
    """The film's own soundtrack as a wav, cached. None when the film is silent.

    Cached per clip mtime like `clip_wav`, with the rate and the levelling in the
    name so a re-rate cannot serve the wrong file. `rate` is the playback speed
    the film is running at, so the track is stretched to match it — the conductor
    forces 1x for an AutoTuner run, but a pacer pass by hand honours the box.
    """
    if not clip.is_file():
        return None
    WAV_DIR.mkdir(parents=True, exist_ok=True)
    tempo = _atempo_chain(rate)
    tag = "film" + (".%gx" % float(rate) if tempo else "") + (".%ddb" % int(round(clip_target_db())) if level else "")
    wav = WAV_DIR / f"{key}.{tag}.wav"
    try:
        if wav.is_file() and wav.stat().st_mtime >= clip.stat().st_mtime and wav.stat().st_size > 4000:
            return wav
    except OSError:
        pass
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vn", "-ac", "1", "-ar", "48000"]
    if tempo:
        cmd += ["-filter:a", tempo]
    cmd += ["-f", "wav", str(wav)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not wav.is_file() or wav.stat().st_size < 4000:
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            pass
        return None      # no audio stream: a silent loop, and the caller says so
    if level:
        level_wav(wav, clip_target_db())
    return wav


def play_wav(wav: Path) -> Optional[subprocess.Popen]:
    """Paplay a prepared wav to the speak sink. The Popen, so a caller can wait.

    Stops whatever this module was playing first, and parks the handle where
    `stop()` finds it — so every existing stop path (a play, say_stop_all, a
    drill stop) already reaches it.
    """
    global _proc
    stop()
    if not clip_audio_on() or wav is None or not Path(wav).is_file():
        return None
    za = _za()
    if za is not None:
        _proc = _shim_play(za, Path(wav))
        return _proc
    try:
        from audio_sink import speak_sink
        sink = speak_sink()
    except Exception:  # noqa: BLE001
        sink = os.environ.get("VOICE_SINK") or _fallback_sink()
    try:
        _proc = subprocess.Popen(
            ["paplay", f"--device={sink}", str(wav)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        _proc = None
        return None
    return _proc
