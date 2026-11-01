"""The user's own demo lines. One wav per question, in the Crissa data folder.

Record your lines saves 16-bit mono PCM here. Silence is not a recording.
This module does not read a house tape.

A question with no recording is read in the user's cloned voice (the User
2026-10-09): `clone.voice` in config/crissa-demo.json names the voice on the
TTS server (`{USER}` = the User's id from the identity store, never a literal),
the line is synthesised once and cached under demo-lines/clone/ keyed on the
voice and the text. A recording always wins. DEMO_CLONE_TTS=0 skips a question
with no recording, as before. No clone voice on the server = skipped.
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
import wave
from pathlib import Path

import crissa_home
import crissa_ports

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "crissa-demo.json"
LINES_DIR = crissa_home.HOME / "demo-lines"
RATE = 16000
MIN_S = 0.30
QUIET_DB = -45.0
_SAFE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def lines_dir() -> Path:
    return LINES_DIR


def load_config(path: Path | None = None) -> dict:
    return json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))


def questions(cfg: dict | None = None) -> list[dict]:
    """Say beats the user can record, in lap order."""
    cfg = cfg if cfg is not None else load_config()
    out = []
    seen: set[str] = set()
    # `beats`, then every broadcast set's beats (2026-10-17): a question that only a set asks is still his to record.
    sets = cfg.get("sets") if isinstance(cfg.get("sets"), list) else []
    rows = list(cfg.get("beats") or []) + [b for s in sets if isinstance(s, dict)
                                           for b in (s.get("beats") or []) if isinstance(b, dict)]
    for beat in rows:
        if beat.get("kind") != "say":
            continue
        say = " ".join(str(beat.get("say") or "").split())
        beat_id = str(beat.get("take") or beat.get("id") or "")
        if not say or not _SAFE.fullmatch(beat_id) or beat_id in seen:
            continue
        seen.add(beat_id)
        out.append({"id": beat_id, "say": say})
    return out


def question_ids(cfg: dict | None = None) -> set[str]:
    return {row["id"] for row in questions(cfg)}


def path_for(beat_id: str) -> Path | None:
    if beat_id not in question_ids() or not _SAFE.fullmatch(beat_id):
        return None
    return lines_dir() / f"{beat_id}.wav"


def samples_of(pcm: bytes) -> array.array:
    out = array.array("h")
    out.frombytes(pcm[: len(pcm) // 2 * 2])
    return out


def level_db(pcm: bytes) -> float | None:
    samples = samples_of(pcm)
    if not samples:
        return None
    ms = sum(x * x for x in samples) / len(samples)
    if ms <= 1.0:
        return -90.0
    return 10.0 * math.log10(ms / (32768.0 ** 2))


def level_pcm(pcm: bytes, target_db: float = -26.0, cap_db: float = 12.0, *,
              allow_cut: bool = False) -> bytes:
    """Move a take toward target_db. Lifts by default. allow_cut also brings a hot take down."""
    samples = samples_of(pcm)
    if not samples:
        return pcm
    ms = sum(x * x for x in samples) / len(samples)
    if ms <= 1.0:
        return pcm
    db = 10.0 * math.log10(ms / (32768.0 ** 2))
    need = target_db - db
    if abs(need) <= 0.5:
        return pcm
    if need < 0 and not allow_cut:
        return pcm
    applied = min(need, cap_db) if need > 0 else max(need, -cap_db)
    gain = 10.0 ** (applied / 20.0)
    peak = max(abs(int(x)) for x in samples) or 1
    gain = min(gain, 32767.0 / peak)
    if 0.99 <= gain <= 1.01:
        return pcm
    out = array.array("h", (max(-32768, min(32767, int(x * gain))) for x in samples))
    return out.tobytes()


def to_rate(pcm: bytes, rate: int, target: int = RATE) -> bytes:
    if rate == target:
        return pcm
    samples = samples_of(pcm)
    if not samples or rate <= 0 or target <= 0:
        return b""
    out_n = max(1, int(len(samples) * target / rate))
    out = array.array("h")
    last = len(samples) - 1
    for i in range(out_n):
        src = i * rate / target
        j = int(src)
        frac = src - j
        a = int(samples[min(j, last)])
        b = int(samples[min(j + 1, last)])
        out.append(max(-32768, min(32767, int(a + (b - a) * frac))))
    return out.tobytes()


def write_wav(path: Path, pcm: bytes, rate: int = RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    tmp.replace(path)


def read_wav(path: Path) -> tuple[bytes, int] | None:
    """16-bit mono PCM and its rate. None when the file is missing or not that shape."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with wave.open(str(path)) as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None
            rate = w.getframerate()
            pcm = w.readframes(w.getnframes())
    except (OSError, wave.Error):
        return None
    if not pcm or rate <= 0:
        return None
    return pcm, rate


def decode_upload(blob: bytes) -> tuple[bytes, int] | None:
    """A WAV upload, down to 16 kHz mono 16-bit. None when it is not that kind of file."""
    if len(blob) < 44 or blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        return None
    pos = 12
    fmt = None
    data = None
    while pos + 8 <= len(blob):
        chunk = blob[pos:pos + 4]
        size = int.from_bytes(blob[pos + 4:pos + 8], "little")
        start = pos + 8
        end = start + size
        if end > len(blob):
            return None
        body = blob[start:end]
        if chunk == b"fmt " and len(body) >= 16:
            fmt = body
        elif chunk == b"data":
            data = body
        pos = end + (size & 1)
    if fmt is None or data is None:
        return None
    audio_format = int.from_bytes(fmt[0:2], "little")
    channels = int.from_bytes(fmt[2:4], "little")
    rate = int.from_bytes(fmt[4:8], "little")
    width = int.from_bytes(fmt[14:16], "little")
    if audio_format != 1 or channels != 1 or width != 16 or rate <= 0:
        return None
    pcm = data[: len(data) // 2 * 2]
    if not pcm:
        return None
    return to_rate(pcm, rate, RATE), RATE


def _reject(error: str, db: float | None = None) -> dict:
    body = {"ok": False, "error": error, "recorded": False}
    if db is not None:
        body["level_db"] = round(db, 1)
    return body


def save_pcm(beat_id: str, pcm: bytes, rate: int = RATE) -> dict:
    """Store one question. Too short or too quiet is refused, and no file is written."""
    path = path_for(beat_id)
    if path is None:
        return _reject("unknown question")
    if rate != RATE:
        pcm = to_rate(pcm, rate, RATE)
    samples = samples_of(pcm)
    if len(samples) < int(MIN_S * RATE):
        return _reject("too short")
    db = level_db(samples.tobytes())
    if db is None or db <= QUIET_DB:
        return _reject("too quiet", db)
    write_wav(path, samples.tobytes(), RATE)
    return {"ok": True, "id": beat_id, "recorded": True, "level_db": round(db, 1)}


def save_upload(beat_id: str, blob: bytes) -> dict:
    decoded = decode_upload(blob)
    if decoded is None:
        return _reject("want a mono 16-bit wav")
    pcm, rate = decoded
    return save_pcm(beat_id, pcm, rate)


def take_pcm(take_id: str) -> bytes | None:
    """PCM for a recorded question. None when that beat has no file."""
    path = path_for(str(take_id or ""))
    if path is None:
        return None
    got = read_wav(path)
    if got is None:
        return None
    pcm, rate = got
    if rate != RATE:
        pcm = to_rate(pcm, rate, RATE)
    return pcm or None


_OFF = ("0", "off", "false", "no")
SYNTH_TIMEOUT_S = 90


def clone_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("DEMO_CLONE_TTS", "1")).strip().lower() not in _OFF


def _user_id() -> str:
    try:
        import house_user
    except ImportError as exc:
        if exc.name != "house_user":
            raise
        return ""
    uid = house_user.user_id()
    return "" if uid == house_user.GENERIC_ID else uid


def clone_voice(cfg: dict | None = None) -> str:
    """The TTS voice that reads an unrecorded question. '' = none named or no User known."""
    cfg = cfg if cfg is not None else load_config()
    name = str((cfg.get("clone") or {}).get("voice") or "").strip()
    if "{USER}" in name:
        uid = _user_id()
        name = name.replace("{USER}", uid) if uid else ""
    return name


def default_tts_url(env: dict | None = None) -> str:
    """The voice that clones, when nothing names one: this machine's voice server (config/house-machine.json
    voice_url, the house's), else this install's Turbo service from the port table (Chatterbox-Turbo clones from a
    clip; the /stream contract). Never another install's port (2026-10-10)."""
    return crissa_home.house_value("voice_url") or crissa_ports.url("turbo", env) + "/stream"


def tts_url(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else load_config()
    return str((cfg.get("clone") or {}).get("url") or os.environ.get("COSY_URL") or default_tts_url())


def clone_path(beat_id: str, text: str, voice: str) -> Path:
    key = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()[:12]
    return lines_dir() / "clone" / f"{beat_id}-{key}.wav"


def synth_stream(text: str, voice: str, url: str) -> tuple[bytes, int] | None:
    """One line from a /stream TTS server: raw s16le mono, rate in X-Sample-Rate. None on any failure."""
    req = urllib.request.Request(url, data=json.dumps({"text": text, "voice": voice}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=SYNTH_TIMEOUT_S) as r:
            rate = int(r.headers.get("X-Sample-Rate") or 24000)
            pcm = r.read()
    except (OSError, urllib.error.URLError, ValueError):
        return None
    pcm = pcm[: len(pcm) // 2 * 2]
    return (pcm, rate) if pcm else None


def clone_pcm(take_id: str, cfg: dict | None = None, synth=None) -> bytes | None:
    """The question read in the clone voice, from the cache or made once. None when off or it cannot be made."""
    if not clone_on():
        return None
    cfg = cfg if cfg is not None else load_config()
    text = next((q["say"] for q in questions(cfg) if q["id"] == take_id), "")
    voice = clone_voice(cfg)
    if not text or not voice:
        return None
    path = clone_path(take_id, text, voice)
    got = read_wav(path)
    if got is not None:
        pcm, rate = got
        return to_rate(pcm, rate, RATE) or None
    made = (synth or synth_stream)(text, voice, tts_url(cfg))
    if not made:
        return None
    pcm = to_rate(made[0], made[1], RATE)
    db = level_db(pcm)
    if len(pcm) < int(MIN_S * RATE) * 2 or db is None or db <= QUIET_DB:
        return None
    write_wav(path, pcm, RATE)
    return pcm


def line_pcm(take_id: str) -> bytes | None:
    """What a say beat plays: the recording, else the clone voice reading the question."""
    return take_pcm(take_id) or clone_pcm(str(take_id or ""))


def wav_bytes(beat_id: str) -> bytes | None:
    path = path_for(beat_id)
    if path is None or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def list_lines() -> dict:
    rows = []
    n = 0
    voice = clone_voice() if clone_on() else ""
    for row in questions():
        path = lines_dir() / f"{row['id']}.wav"
        got = read_wav(path) if path.is_file() else None
        recorded = got is not None
        if recorded:
            n += 1
        db = level_db(got[0]) if got else None
        rows.append({
            "id": row["id"],
            "say": row["say"],
            "recorded": recorded,
            "skipped": not recorded and not voice,
            "clone": bool(voice) and not recorded,
            "level_db": None if db is None else round(db, 1),
        })
    return {"ok": True, "lines": rows, "recordings": n, "clone_voice": bool(voice)}
