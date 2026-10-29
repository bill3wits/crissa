"""Capture and audio I/O. PulseAudio/PipeWire (parec), else sounddevice, else frames the Wall page posts.

The house captured through parec from one named array. A pack runs on any machine: `resolve_mic("auto")`
is the sound server's default input, else the first input device that is not an echo-cancel node or a monitor (PulseAudio source names on Linux;
sounddevice names or indices elsewhere), a named device is matched by substring, and `mic == "page"` reads
the frames the player forwards from the Wall page's microphone (crissa_ear.capture.PushedFrames).
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# ONE mic watcher (the ear owns capture device choice). Other pieces call /v1/listen,
# never their own preference list. An echo-cancel SOURCE is never auto-picked: that node
# is for apps that listen while the voice plays on the echo-cancel SINK (sink != source).
# ---------------------------------------------------------------------------
from . import capture as _capture



def list_pulse_sources() -> list[str]:
    try:
        out = subprocess.check_output(
            ["pactl", "list", "short", "sources"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    names = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            names.append(parts[1])
    return names


def default_pulse_source() -> str:
    """The sound server's default input (`pactl get-default-source`): the microphone the person chose in the desktop's
    sound settings. '' when it cannot be read or is a monitor."""
    try:
        out = subprocess.check_output(["pactl", "get-default-source"], text=True, stderr=subprocess.DEVNULL,
                                      timeout=3).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    return "" if (not out or out.endswith(".monitor")) else out


def resolve_mic(preference: str = "auto") -> str:
    """The capture device. auto = the sound server's DEFAULT input (the User 2026-10-10, .101: auto took the first
    input listed - the laptop's empty analog jack - while the Shure was the desktop's default), else the first input
    that is not an echo-cancel node or a monitor.

    A named preference is taken as is when it is a source, else by substring (a PulseAudio source name
    such as alsa_input.usb-<vendor>_<model>-00.analog-stereo, or any part of it). "page" is the Wall
    page's microphone and never a device here. An explicit echo-cancel source is allowed when named.
    """
    pref = (preference or "auto").strip()
    if pref == _capture.PAGE:
        return pref
    if not _capture.pulse_available():
        return _capture.resolve_device(pref)         # sounddevice: a name, an index, or the default
    sources = list_pulse_sources()
    if pref and pref not in ("auto", "default", "house"):
        if pref in sources:
            return pref
        for s in sources:
            if pref.lower() in s.lower():
                return s
        return pref  # let parec fail loudly
    if not sources:
        raise RuntimeError("No audio sources found (is PipeWire or PulseAudio running?)")
    dflt = default_pulse_source()
    if dflt and dflt in sources:
        return dflt
    inputs = [s for s in sources if not s.endswith(".monitor")]
    non_aec_src = [
        s
        for s in inputs
        if "echocancel" not in s.lower() and "echo-cancel" not in s.lower()
    ]
    if non_aec_src:
        return non_aec_src[0]
    if inputs:
        return inputs[0]
    raise RuntimeError("no input device: every audio source is a monitor")


def _capture_from(device: str) -> str:
    """Where to record: the echo canceller's source when it runs for this microphone (round 14, aec.py), else the
    microphone. resolve_mic stays the person's microphone (what Init shows and Voice ID names)."""
    try:
        from . import aec

        return aec.capture_source(device)
    except Exception:  # noqa: BLE001 - never lose the microphone over the canceller
        return device


def load_wav_mono16k(path: str | Path) -> tuple[np.ndarray, int]:
    """Load any common audio file → float32 mono @ 16 kHz in [-1, 1]."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    # Prefer soundfile when format is simple wav; ffmpeg for everything else.
    try:
        import soundfile as sf

        data, sr = sf.read(str(path), always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        data = data.astype(np.float32)
        if data.max() > 1.5:  # int pcm accidentally
            data = data / 32768.0
    except Exception:
        data, sr = _ffmpeg_load(path)

    if sr != 16000:
        data = resample_linear(data, sr, 16000)
        sr = 16000
    return data.astype(np.float32), sr


def _ffmpeg_load(path: Path) -> tuple[np.ndarray, int]:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-ac",
        "1",
        "-ar",
        "16000",
        "pipe:1",
    ]
    raw = subprocess.check_output(cmd)
    return np.frombuffer(raw, dtype=np.float32), 16000


def resample_linear(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or len(x) == 0:
        return x.astype(np.float32)
    n_out = int(round(len(x) * sr_out / sr_in))
    if n_out <= 1:
        return np.zeros(0, dtype=np.float32)
    xp = np.linspace(0, len(x) - 1, num=len(x), dtype=np.float64)
    xout = np.linspace(0, len(x) - 1, num=n_out, dtype=np.float64)
    return np.interp(xout, xp, x.astype(np.float64)).astype(np.float32)


def float_to_pcm16(x: np.ndarray) -> np.ndarray:
    x = np.clip(x.astype(np.float32), -1.0, 1.0)
    return (x * 32767.0).astype(np.int16)


def pcm16_to_float(pcm: np.ndarray) -> np.ndarray:
    return pcm.astype(np.float32) / 32768.0


def write_wav_pcm16(path: str | Path, pcm: np.ndarray, sr: int = 16000) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.ascontiguousarray(pcm, dtype=np.int16).tobytes())


# A DEAD RECORDER IS AN ERROR, NOT SILENCE (round 46, the local review of kit crissa-063a2813, 2026-10-23): when parec
# ends before its window is full - a named microphone unplugged, a source that does not exist - record_seconds padded
# the rest with zeros and returned it, so the ear loop read silence forever with no error anywhere. A read that ends
# early raises DeadRecorder (the ear loop's capture frame turns it into `last_error`, /v1/listen into a 500 with the
# reason). record_until_silence does the same when the recorder ends before a full second came back. Kill:
# CRISSA_EAR_DEAD_MIC_RAISES=0 (pad with zeros, as before).
class DeadRecorder(RuntimeError):
    """The recorder ended before its window was full."""


def dead_mic_raises() -> bool:
    import os

    return os.environ.get("CRISSA_EAR_DEAD_MIC_RAISES", "1").strip().lower() not in ("0", "false", "no", "off")


def _dead(proc, source: str, got: int, want: int) -> DeadRecorder:
    rc = proc.poll()
    return DeadRecorder("the recorder stopped on %s (parec exit %s) after %d of %d bytes" % (source, rc, got, want))


def record_seconds(
    seconds: float,
    mic: str = "auto",
    sample_rate: int = 16000,
) -> np.ndarray:
    """Record mono float32 @ sample_rate: parec on PulseAudio, else sounddevice, else the page's frames."""
    source = _capture_from(resolve_mic(mic))
    n_samples = int(seconds * sample_rate)
    if source == _capture.PAGE or not _capture.pulse_available():
        return pcm16_to_float(_capture.read_frames(source, n_samples, sample_rate))
    cmd = [
        "parec",
        "--device",
        source,
        "--format=s16le",
        f"--rate={sample_rate}",
        "--channels=1",
        f"--latency-msec=20",
    ]
    # Read exact byte count then kill
    nbytes = n_samples * 2
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    raw = b""
    eof = False
    while len(raw) < nbytes:
        chunk = proc.stdout.read(min(65536, nbytes - len(raw)))
        if not chunk:
            eof = True
            break
        raw += chunk
    proc.terminate()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()
    if eof and dead_mic_raises():
        raise _dead(proc, source, len(raw), nbytes)
    pcm = np.frombuffer(raw[:nbytes], dtype=np.int16)
    if len(pcm) < n_samples:
        pad = np.zeros(n_samples - len(pcm), dtype=np.int16)
        pcm = np.concatenate([pcm, pad])
    return pcm16_to_float(pcm)


def record_until_silence(
    seconds_max: float,
    mic: str = "auto",
    sample_rate: int = 16000,
    hangover_sec: float = 0.8,
    frame_sec: float = 0.3,
    min_speech_s: float = 0.25,
) -> np.ndarray:
    """One-shot capture that ENDS WHEN THE SPEAKER STOPS (2026-09-12, the User:
    "no awkward thinking time waiting turns"). record_seconds() always ran
    the full window — a wizard answer of one second still cost the 4–5 s
    window before decode. Reads parec in frame_sec frames; once the VAD sees
    a speech span and `hangover_sec` of silence has followed it, returns.
    seconds_max is the cap when nothing is said."""
    from .vad import detect_speech_spans

    source = _capture_from(resolve_mic(mic))
    if source == _capture.PAGE or not _capture.pulse_available():
        return _capture.record_until_silence(source, seconds_max, sample_rate, hangover_sec, frame_sec,
                                             min_speech_s, detect_speech_spans, pcm16_to_float)
    cmd = ["parec", "--device", source, "--format=s16le", f"--rate={sample_rate}",
           "--channels=1", "--latency-msec=20"]
    frame_bytes = int(frame_sec * sample_rate) * 2
    max_bytes = int(seconds_max * sample_rate) * 2
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    raw = b""
    eof = False
    try:
        while len(raw) < max_bytes:
            chunk = proc.stdout.read(min(frame_bytes, max_bytes - len(raw)))
            if not chunk:
                eof = True
                break
            raw += chunk
            if len(raw) < int(1.0 * sample_rate) * 2:
                continue                      # under a second: nothing to judge yet
            pcm = np.frombuffer(raw, dtype=np.int16)
            audio = pcm16_to_float(pcm)
            try:
                spans = detect_speech_spans(audio, sample_rate, min_speech_s=min_speech_s)
            except Exception:
                spans = []
            if spans:
                last_end = max(float(getattr(sp, "end", 0.0) or (sp[1] if isinstance(sp, (tuple, list)) else 0.0)) for sp in spans)
                if len(audio) / sample_rate - last_end >= hangover_sec:
                    break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
    if eof and len(raw) < min(max_bytes, sample_rate * 2) and dead_mic_raises():
        raise _dead(proc, source, len(raw), max_bytes)     # round 46: ended before a second came back
    pcm = np.frombuffer(raw, dtype=np.int16)
    if len(pcm) < sample_rate:
        pcm = np.concatenate([pcm, np.zeros(sample_rate - len(pcm), dtype=np.int16)])
    return pcm16_to_float(pcm)


def record_until_enter(
    mic: str = "auto",
    sample_rate: int = 16000,
    max_seconds: float = 120.0,
) -> np.ndarray:
    """Record until stdin newline (or max_seconds)."""
    import select
    import sys
    import threading

    source = _capture_from(resolve_mic(mic))
    cmd = [
        "parec",
        "--device",
        source,
        "--format=s16le",
        f"--rate={sample_rate}",
        "--channels=1",
        "--latency-msec=20",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    chunks: list[bytes] = []
    stop = threading.Event()

    def reader():
        max_bytes = int(max_seconds * sample_rate * 2)
        total = 0
        while not stop.is_set() and total < max_bytes:
            # small reads so stop is responsive
            b = proc.stdout.read(3200)  # 100ms @16k
            if not b:
                break
            chunks.append(b)
            total += len(b)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    print(f"[rail-stt] recording from {source} — press Enter to stop…", flush=True)
    try:
        sys.stdin.readline()
    except Exception:
        pass
    stop.set()
    proc.terminate()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()
    t.join(timeout=1)
    raw = b"".join(chunks)
    if not raw:
        return np.zeros(0, dtype=np.float32)
    return pcm16_to_float(np.frombuffer(raw, dtype=np.int16))


def rms_db(x: np.ndarray) -> float:
    if len(x) == 0:
        return -120.0
    r = float(np.sqrt(np.mean(np.square(x.astype(np.float64))) + 1e-12))
    return float(20.0 * np.log10(r + 1e-12))
