"""Three independent Oracle candidates on :8191 — shared sensors, no control.

  CODE   = rail-presence inventory + rail-stt stamp
  GEMMA  = google/gemma-4-E4B-it  (default DRIVER)
  COSMOS = nvidia/Cosmos3-Edge reasoner

Shared tick pack (same for every candidate):
  - camera ring (~30fps) sampled into a time strip + latest still
  - low-res motion ghost + heading (trail of movers; snaps only for unknown face ID)
  - one mic clip → rail-stt ear note
  - face/voice emb catalog (ids + counts only)

None controls PTZ. None reads another candidate's answer.
Task: duplicate Oracle outputs. Do not write code.
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths
from .room_heading import door_home_pose

import base64
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .fsio import atomic_write_text

SNAPSHOT_URL = os.environ.get(
    "RAIL_ORACLE_SNAPSHOT", _ports.camera("/snapshot")
)
BUFFER_URL = os.environ.get(
    "RAIL_ORACLE_BUFFER", _ports.camera("/v1/buffer")
)
STT_URL = os.environ.get("RAIL_ORACLE_STT", _ports.url("stt", "/stt"))
PRESENCE_URL = os.environ.get(
    "RAIL_ORACLE_PRESENCE", os.environ.get("RAIL_PRESENCE", _ports.url("presence", "/v1/presence"))
)
GEMMA_CKPT = Path(
    os.environ.get("GEMMA_CKPT", str(_paths.HF / "hub-local" / "gemma-4-E4B-it"))
)
COSMOS_CKPT_NAME = os.environ.get("COSMOS_CKPT_NAME", "Cosmos3-Edge")
COSMOS_PYTHON = os.environ.get(
    "COSMOS_PYTHON", ""
)
GEMMA_PYTHON = os.environ.get(
    "GEMMA_PYTHON", ""
)
WORK = Path(
    os.environ.get(
        "RAIL_ORACLE_WORK",
        str(_paths.DATA / "work/oracle_live"),
    )
)
INTERVAL = float(os.environ.get("RAIL_ORACLE_INTERVAL_SEC", "45"))
AUDIO_SEC = float(os.environ.get("RAIL_ORACLE_AUDIO_SEC", "5"))
# Look-around always tries STT (even when live cycle has AUDIO_SEC=0)
LOOKAROUND_AUDIO_SEC = float(os.environ.get("RAIL_ORACLE_LOOKAROUND_AUDIO_SEC", "4"))
# Keep look-around grid on the board (don't let door time-strip overwrite immediately)
LOOKAROUND_HOLD_SEC = float(os.environ.get("RAIL_ORACLE_LOOKAROUND_HOLD_SEC", "120"))
# Pack ~2s of the 30fps ring for VLMs (stride 5 → ~6 fps → ~12 frames)
BUF_SEC = float(os.environ.get("RAIL_ORACLE_BUF_SEC", "2.0"))
BUF_STRIDE = int(os.environ.get("RAIL_ORACLE_BUF_STRIDE", "5"))
BUF_MAX = int(os.environ.get("RAIL_ORACLE_BUF_MAX", "12"))
ENABLE = os.environ.get("RAIL_ORACLE_BENCH", "1") not in ("0", "false", "no")
GPU_ON = os.environ.get("RAIL_ORACLE_GPU", "1") not in ("0", "false", "no")
# auto = continuous live on shared pack (default). shadow = operator button only.
# operator / cosmos_operator = chat-driven tools only (no scripted PTZ probes).
MODE = os.environ.get("RAIL_ORACLE_MODE", "auto").strip().lower()
try:
    from .cosmos_operator import operator_mode_enabled as _op_mode

    if _op_mode() and MODE not in ("operator", "cosmos_operator", "shadow", "manual"):
        MODE = "operator"
except Exception:
    pass
DRIVER = os.environ.get("RAIL_ORACLE_DRIVER", "gemma").strip().lower()
if DRIVER not in ("code", "gemma", "cosmos"):
    DRIVER = "gemma"
_ONLY_RAW = os.environ.get("RAIL_ORACLE_ONLY", "all").strip().lower()
if _ONLY_RAW in ("", "all", "*"):
    ACTIVE = {"code", "gemma", "cosmos"}
else:
    ACTIVE = {p.strip() for p in _ONLY_RAW.replace(";", ",").split(",") if p.strip()}
    if not ACTIVE:
        ACTIVE = {"code", "gemma", "cosmos"}
# Operator mode: Cosmos only; no multi-model thrash
try:
    from .cosmos_operator import operator_mode_enabled as _op_mode2

    if _op_mode2():
        DRIVER = "cosmos"
        ACTIVE = {"cosmos"}
except Exception:
    pass
HISTORY_PATH = Path(
    os.environ.get(
        "RAIL_ORACLE_HISTORY",
        str(_paths.DATA / "results/shadow_history.jsonl"),
    )
)
HISTORY_MAX = int(os.environ.get("RAIL_ORACLE_HISTORY_MAX", "50"))
# Mic capture choice lives ONLY in rail-stt (rail_stt.audio.resolve_mic → Link).
# Oracle does not list Pulse sources. TTS may still play on echocancel_sink (broker).
STT_LISTEN_URL = os.environ.get(
    "RAIL_ORACLE_STT_LISTEN",
    os.environ.get("RAIL_ORACLE_STT", _ports.url("stt", "/stt")).replace(
        "/stt", "/v1/listen"
    ),
)

HOUSE_ID_ROOT = Path(
    os.environ.get("HOUSE_ID_ROOT", str(_paths.IDENTITY))
).expanduser()
# Permanence (door story — NOT "strip empty"):
#   ENTER face → present + entry grace (walk into room; FOV empty is normal)
#   STAY while not exit-armed (seated at desk, doorway empty)
#   EXIT-ARM when already-present person re-seen at door after grace
#   LEAVE only after exit-armed AND FOV miss for leave_confirm_sec
# LEAVE_SEC is leave *confirm after exit arm*, not "miss FOV while seated".
LEAVE_SEC = float(os.environ.get("RAIL_ORACLE_LEAVE_SEC", "20"))
# Align with live door_watch: entry grace deleted (the User 2026-08-11).
ENTRY_GRACE_SEC = float(os.environ.get("RAIL_ORACLE_ENTRY_GRACE_SEC", "0"))
# When memory empty, occasionally facefind so re-enter without door-only FOV works
REFIND_SEC = float(os.environ.get("RAIL_ORACLE_REFIND_SEC", "60"))
# Door FOV untrustworthy this long with people still sticky → look-around-facefind
OBSCURE_LOOKAROUND_SEC = float(os.environ.get("RAIL_ORACLE_OBSCURE_SEC", "25"))
# Cap immortal sticky: no face re-sight for this long → drop (was 24h daily only).
# Prevents another person lingering 10h while presence is empty and Gemma invents "memory".
STICKY_MAX_SEC = float(os.environ.get("RAIL_ORACLE_STICKY_MAX_SEC", str(2 * 3600)))
# Gemma sticky-merge only if last face/voice within this window (else trust vision/rails).
STICKY_MERGE_MAX_SEC = float(os.environ.get("RAIL_ORACLE_STICKY_MERGE_MAX_SEC", "900"))
LOOKAROUND_COOLDOWN_SEC = float(os.environ.get("RAIL_ORACLE_LOOKAROUND_COOLDOWN", "90"))
DOOR_HOME_PAN = int(door_home_pose()["pan"])   # measured in Setup, else the camera's forward (room_heading)
DOOR_PAN_TOL = int(os.environ.get("RAIL_ORACLE_DOOR_PAN_TOL", "12000"))
MEMORY_PATH = Path(
    os.environ.get(
        "RAIL_ORACLE_MEMORY",
        str(WORK / "room_memory.json"),
    )
)

# House door law (the User) — inject into every Oracle prompt / memory note.
DOOR_LAW = """
HOUSE DOOR LAW — the child's match game (camera holds the only door):
1) STAY ON THE DOOR. Brief video scan only when count is uncertain — not station stills.
2) ENTER: name them if face is clear; always CACHE their enter look (hair/shirt/crop).
   After enter they may leave the FOV (sit down). Empty door ≠ empty room. Sticky count.
3) THE ONLY HARD TRICK IS WHO LEFT. When a body is at the door on the way out, it is a
   matching game: compare that body to the cached ENTER pics/appearance of people still
   marked present. Best match = that person left. Prefer honest unknown over a wrong name.
4) Never invent a leave because the strip is empty while sticky people are in memory.
5) Never invent a crowd from multi-frame blur / motion ghosts (camera motion ≠ multi-body).
""".strip()

ASK_HOW = """You are being considered as the only Oracle for a smart home living room.

Target outcome (the *what*, not the *how*):
- Always know who is physically in the room (names when known, honest unknown otherwise).
- Always know who is speaking right now (or that speech is media / unknown).
- Be accurate enough that a house agent feels sentient and grounded in the real room — not a chatbot guessing.

""" + DOOR_LAW + """

Question: How can YOU best duplicate that Oracle target state?
Describe your own best approach end-to-end. Do not assume our existing software.
If you need sensors, storage, tools, or loops, invent what you need and say so.
If you cannot do part of it alone, say what is missing.
Be concrete. No marketing. Do not write code."""

def _identity_pack_card(max_chars: int = 1800) -> str:
    """Short family roster for Gemma system side of ASK_NOW."""
    pack = _paths.DATA / "identity-pack"
    roster = pack / "ROSTER.md"
    people = pack / "PEOPLE.json"
    if people.is_file():
        try:
            data = json.loads(people.read_text())
            lines = ["HOUSE IDENTITY PACK (enrolled faces on disk — name only if match):"]
            for p in data.get("people") or []:
                app = p.get("appearance") or {}
                lines.append(
                    f"- {p.get('id')}: {p.get('display')} gender={p.get('gender')} "
                    f"hair={app.get('hair_tone') or '?'} "
                    f"voice_embs={p.get('voice_emb_count', 0)} "
                    f"face_refs={p.get('face_ref_count', 0)}"
                )
            lines.append(
                "Prefer unknown + hair=dark/shirt=dark over a wrong name. "
                "the User=M dark hair; another person=F dark brown hair (NOT green)."
            )
            text = "\n".join(lines)
            return text[:max_chars]
        except Exception:
            pass
    if roster.is_file():
        try:
            return roster.read_text()[:max_chars]
        except Exception:
            pass
    return "HOUSE IDENTITY: the user(M), another person(F), a guest(F), a guest(F), will(M) — identity-store enrollments."


def _gemma_home_card(max_chars: int = 3200) -> str:
    """Load SOUL + AGENT orders + DRILLS (worked JSON) — Fable pattern for Gemma.

    Small models copy worked examples more reliably than abstract rules.
    Budget ~3k tokens-ish of text; full kit lives on disk for humans.
    """
    roots = [
        _paths.DATA / "gemma-home",
        _paths.DATA / "gemma-home",
    ]
    chunks: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        soul = root / "SOUL.md"
        if soul.is_file():
            try:
                chunks.append(soul.read_text()[:1100])
            except Exception:
                pass
        agent = root / "AGENT.md"
        if agent.is_file():
            try:
                t = agent.read_text()
                # standing orders + timing (compact)
                if "## Standing orders" in t:
                    chunks.append(t[t.find("## Standing orders") : t.find("## Standing orders") + 900])
                else:
                    chunks.append(t[:600])
            except Exception:
                pass
        drills = root / "drills" / "DRILLS.md"
        if drills.is_file():
            try:
                # full drills are the reliability lever
                chunks.append(drills.read_text()[:2200])
            except Exception:
                pass
        break
    text = "\n\n".join(chunks).strip()
    return text[:max_chars] if text else ""


def build_ask_now(*, ear_note: str | None = None) -> str:
    """Gemma NOW: home SOUL + door law + identity + CODE gold JSON."""
    home = _gemma_home_card()
    parts = [
        "You are the Gemma Room Oracle (Grok channel). Good soldier. Fast CODE-shaped JSON.",
        "",
        DOOR_LAW.strip(),
        "",
    ]
    if home:
        parts.extend(["=== SOUL / ORDERS ===", home, ""])
    parts.extend(
        [
            _identity_pack_card(),
            "",
            "INPUTS:",
            "- TRIGGER FRAMES from 30fps door ring (seq timeline) and/or short strip.",
            "  Multi-frame = time motion, NOT multi-body. Same person twice in two seats = count 1.",
            "- face_hits (faceid) + speaking stamp (voice_emb beats visual name).",
            "- room_memory sticky + enter appearance / entry_cache for leave match.",
            "",
            "You do NOT control PTZ. Speed matters (auth/leave in seconds).",
            "",
            "GAME: enter→cache; sticky empty door; leave→match entry crop; joint=same-frame.",
            "Person test: frame+posture or omit. Prefer unknown over wrong name.",
            "",
            "Answer ONLY this JSON (CODE gold standard — no prose outside it):",
            "{",
            '  "in_room": {',
            '    "headcount": 0,',
            '    "people": [{"id": "the user|another person|a guest|a guest|will|unknown", "certain": true,',
            '      "evidence": "face|voice|memory|appearance_match|vision",',
            '      "location": "door|in_room|desk|chair|unknown",',
            '      "appearance": "hair=dark shirt=dark", "authed": false}],',
            '    "unidentified": 0,',
            '    "count_trust": "fact|stale|unsure",',
            '    "note": "short measured"',
            "  },",
            '  "speaking": {"id": null, "text": "", "source": "voice_emb|silence|unsure|media", "conf": 0.0},',
            '  "cannot_do": []',
            "}",
        ]
    )
    text = "\n".join(parts)
    if ear_note:
        text = text + "\n\n" + ear_note
    return text


# Back-compat for meta previews / tests — prefer build_ask_now() at call sites
ASK_NOW = build_ask_now()

# NVIDIA Cosmos3-Edge reasoner template (model card + cosmos-framework inputs/reasoner/*).
# Official offline CLI: model_mode=reasoner, prompt + vision_path (image or video).
# Thinking is ON by default offline; do NOT fight it with "JSON only / no thinking".
ASK_NOW_COSMOS = """This image is a time-ordered strip from a fixed door camera in a home
(left = older frames, right = newer). The camera HOLDS the only door by default.

""" + DOOR_LAW + """

PARSE LAW: several frames / motion ghosts = camera or body MOTION (heading), NOT
multiple bodies. Do not invent headcount from blur, double-image, or trails.
Same-frame distinct people only (or stable co-presence after ego-motion).

Generate a short plan of observations for this door FOV:
1) Enter / leave / pass-through motion across left→right?
2) Anyone visible now? Match clothes/hair to room_memory enter appearance if leaving.
3) Doorway empty-but-people-sticky (normal) vs door OBSCURED (cannot trust leave)?
4) Any TV/poster/media that could look like a person?

Be concrete. Prefer honest uncertainty over invented names.
House ids if clear: the user, another person, a guest, a guest, will; else unknown.

After reasoning, end with a one-paragraph presence judgment for this FOV
(and note body locations if known). JSON optional — house reads your text either way.
"""

# Reasoner sampling defaults from cosmos_framework/inference/defaults/reasoner/sample_args.json
COSMOS_MAX_NEW_TOKENS = int(os.environ.get("RAIL_ORACLE_COSMOS_MAX_NEW", "1024"))
COSMOS_DO_SAMPLE = os.environ.get("RAIL_ORACLE_COSMOS_DO_SAMPLE", "0") not in (
    "0",
    "false",
    "no",
)
LOOK_AROUND_URL = os.environ.get(
    "RAIL_ORACLE_LOOK_AROUND", _ports.camera("/v1/look_around")
)

# Look-around tool vision (multi-stop room, not door-hold ring). Still NVIDIA reasoner style.
ASK_LOOK_AROUND_COSMOS = """This image is a ONE-SHOT look-around-facefind (TOOL, not a patrol routine).

TOP row: ARCHIVE REFERENCES labeled REF — empty room / closed door baselines from this house.
BOTTOM row: LIVE stops labeled LIVE — real captures NOW (firmware facefinder).

CRITICAL: REF panels are EMPTY baselines for comparison ONLY.
If a LIVE panel shows a person (face/body), the room is OCCUPIED — do NOT say empty
just because a REF panel is empty. Empty REF + person on LIVE = someone is here.

""" + DOOR_LAW + """

Answer:
1) Which LIVE stops show people (vs empty like REF)?
2) Headcount and locations (desk/chair/…).
3) Who is the likely SPEAKER if EAR/STT note is attached (voice id / transcript)?
4) TV/poster risk on any LIVE panel?

House ids only when clear: the user, another person, a guest, a guest, will; else unknown man/woman.
"""


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d %H:%M:%S")


def _http_get(url: str, timeout: float = 5.0) -> bytes:
    if _ports.camera_off() and _ports.is_camera_request(url):   # no camera: the bench reads none (2026-10-10)
        _ports.say_once("the oracle bench reads no camera (snapshot, buffer, triggers)")
        raise OSError("camera: off")
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def _http_json(url: str, timeout: float = 5.0) -> dict:
    return json.loads(_http_get(url, timeout).decode())


def _http_post_raw(url: str, body: bytes, headers: dict, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _pcm16_stats(pcm: bytes) -> dict[str, Any]:
    """RMS / peak for operator sensor strip."""
    import array
    import math

    if not pcm or len(pcm) < 4:
        return {"samples": 0, "rms_db": -99.0, "peak": 0, "silent": True}
    # even length
    n = len(pcm) // 2
    arr = array.array("h")
    arr.frombytes(pcm[: n * 2])
    if not arr:
        return {"samples": 0, "rms_db": -99.0, "peak": 0, "silent": True}
    peak = max(abs(x) for x in arr)
    acc = 0.0
    for x in arr:
        acc += float(x) * float(x)
    rms = math.sqrt(acc / len(arr))
    db = 20.0 * math.log10(rms / 32768.0) if rms > 0 else -99.0
    return {
        "samples": len(arr),
        "rms_db": round(db, 1),
        "peak": int(peak),
        "silent": peak < 80 or db < -55.0,
    }


def _ear_listen(seconds: float = AUDIO_SEC) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask rail-stt (sole mic watcher) to capture+STT. No local Pulse device list."""
    sec = max(1.0, min(15.0, float(seconds)))
    meta: dict[str, Any] = {
        "seconds": sec,
        "backend": "rail-stt/v1/listen",
        "device": "rail-stt-owned",
        "ok": False,
    }
    speaking: dict[str, Any] = {"id": None, "text": None, "source": None}
    body = json.dumps({"seconds": sec}).encode()
    try:
        req = urllib.request.Request(
            STT_LISTEN_URL,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=max(30.0, sec + 60.0)) as r:
            stt = json.loads(r.read().decode())
    except Exception as e:
        meta["error"] = str(e)[:200]
        return speaking, meta
    if not isinstance(stt, dict):
        meta["error"] = "bad stt response"
        return speaking, meta
    text = str(stt.get("text") or "").strip()
    pid = stt.get("person_id")
    if not pid and isinstance(stt.get("stamp"), dict):
        pid = stt["stamp"].get("person_id")
    reason = stt.get("reason")
    rms = stt.get("rms_db")
    silent = (not text) and (
        reason == "silence_rms" or (isinstance(rms, (int, float)) and rms < -50)
    )
    meta.update(
        {
            "ok": True,
            "silent": bool(silent),
            "rms_db": rms,
            "reason": reason,
            "engine": stt.get("engine"),
            "peak": stt.get("peak"),
        }
    )
    speaking = {
        "id": str(pid).strip().lower() if pid else None,
        "text": text or None,
        "source": "voice" if text else ("silence" if silent else "unsure"),
        "conf": stt.get("confidence"),
        "engine": stt.get("engine"),
    }
    return speaking, meta


def _ear_note_from_stt(
    speaking: dict[str, Any],
    mic_meta: dict[str, Any],
    emb: dict[str, Any] | None = None,
    buf_meta: dict[str, Any] | None = None,
) -> str:
    """Shared evidence pack text for models (not CODE's filled inventory)."""
    lines = [
        "=== SHARED SENSOR PACK (same for every Oracle; no control) ===",
    ]
    if buf_meta:
        lines.append(
            f"- camera: ~{buf_meta.get('buffer_hz') or 30}fps ring → time strip "
            f"n_frames={buf_meta.get('n_frames')} stride={buf_meta.get('stride')} "
            f"secs≈{buf_meta.get('buf_sec')} (left=older, right=newer)"
        )
        lines.append(f"- strip path attached as image; latest still also available")
    lines.append("Ear note (shared short mic → rail-stt):")
    if mic_meta:
        lines.append(
            f"- mic: backend={mic_meta.get('backend')} device={mic_meta.get('device')} "
            f"rms_db={mic_meta.get('rms_db')} peak={mic_meta.get('peak')} "
            f"silent={mic_meta.get('silent')}"
        )
    text = (speaking or {}).get("text") or ""
    pid = (speaking or {}).get("id")
    if text:
        lines.append(f"- transcript: {text!r}")
    else:
        lines.append("- transcript: (empty / silence / fail)")
    if pid:
        lines.append(f"- voice stamp person_id: {pid}")
    else:
        lines.append("- voice stamp person_id: (none)")
    src = (speaking or {}).get("source")
    if src:
        lines.append(f"- stamp source: {src}")
    if mic_meta.get("silent"):
        lines.append(
            "- WARNING: mic looks digitally silent this tick — do not invent speech."
        )
    if emb:
        lines.append(
            f"- face enrolled: {emb.get('face_enrolled')}"
        )
        lines.append(
            f"- voice enrolled: {emb.get('voice_enrolled')}"
        )
        lines.append(f"- feeds: {emb.get('camera_feed')}; {emb.get('mic_feed')}")
    lines.append("Duplicate Oracle outputs only. Do not write code. Do not control the camera.")
    return "\n".join(lines)


def _grab_frame() -> Path | None:
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / f"frame_{int(time.time())}.jpg"
    try:
        path.write_bytes(_http_get(SNAPSHOT_URL, timeout=5))
        for old in sorted(WORK.glob("frame_*.jpg"))[:-5]:
            old.unlink(missing_ok=True)
        return path
    except Exception:
        return None


def _emb_catalog() -> dict[str, Any]:
    """Read-only face/voice enrollment catalog (ids + counts). Not control."""
    faces: dict[str, int] = {}
    people = HOUSE_ID_ROOT / "people"
    if people.is_dir():
        for person in sorted(people.iterdir()):
            if not person.is_dir() or person.name.startswith((".", "_")):
                continue
            n = len(list((person / "face").glob("*.json"))) if (person / "face").is_dir() else 0
            if n:
                faces[person.name] = n
    voice: dict[str, int] = {}
    try:
        stt = _http_json(STT_URL.replace("/stt", "/health"), timeout=2)
        voice = dict(stt.get("voice_bank") or {})
    except Exception:
        # fall back to counting voice files
        if people.is_dir():
            for person in people.iterdir():
                if not person.is_dir():
                    continue
                vdir = person / "voice"
                if vdir.is_dir():
                    n = len([x for x in vdir.iterdir() if x.suffix.lower() in (".json", ".npy", ".wav")])
                    if n:
                        voice[person.name] = n
    return {
        "face_enrolled": faces,
        "voice_enrolled": voice,
        "note": "catalog only — sims computed by CODE/STT when they have a match; VLMs use names as candidates",
        "camera_feed": _ports.camera("/snapshot") + " + /v1/buffer ~30fps ring",
        "mic_feed": "shared short PCM → rail-stt :8190/stt",
    }


def _grab_buffer_pack() -> dict[str, Any]:
    """Pull ~BUF_SEC of the 30fps ring, stride, build a left→right time strip.

    Returns paths + meta. Same pack goes to every Oracle candidate.
    """
    WORK.mkdir(parents=True, exist_ok=True)
    # need ~ BUF_SEC * 30 / stride frames; request enough raw frames
    need = max(BUF_MAX * BUF_STRIDE, int(BUF_SEC * 30) + BUF_STRIDE)
    need = min(need, 150)
    url = f"{BUFFER_URL}?max={need}&full=1&stride=1"
    meta: dict[str, Any] = {
        "ok": False,
        "hz_target": 30,
        "buf_sec": BUF_SEC,
        "stride": BUF_STRIDE,
        "n_requested": need,
        "n_frames": 0,
        "strip": None,
        "latest": None,
        "frame_seqs": [],
    }
    latest = _grab_frame()
    meta["latest"] = str(latest) if latest else None
    try:
        body = _http_json(url, timeout=15)
    except Exception as e:
        meta["error"] = f"buffer: {e}"[:200]
        meta["strip"] = meta["latest"]
        return meta
    frames = body.get("frames") or []
    if not frames:
        meta["error"] = "buffer empty"
        meta["strip"] = meta["latest"]
        return meta
    # take newest portion covering BUF_SEC, then stride
    picked = frames[-(int(BUF_SEC * 30)) :] if len(frames) > int(BUF_SEC * 30) else frames
    picked = picked[:: max(1, BUF_STRIDE)][-BUF_MAX:]
    jpegs: list[bytes] = []
    seqs: list[int] = []
    for fr in picked:
        b64 = fr.get("jpeg_b64") or ""
        try:
            jpegs.append(base64.b64decode(b64))
            seqs.append(int(fr.get("seq") or 0))
        except Exception:
            continue
    meta["n_frames"] = len(jpegs)
    meta["frame_seqs"] = seqs
    meta["buffer_hz"] = body.get("hz")
    meta["buffer_n"] = body.get("n")
    if not jpegs:
        meta["strip"] = meta["latest"]
        return meta
    # Build horizontal strip (time →)
    try:
        import cv2
        import numpy as np

        imgs = []
        th = 180  # thumbnail height
        for raw in jpegs:
            arr = np.frombuffer(raw, dtype=np.uint8)
            im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if im is None:
                continue
            h, w = im.shape[:2]
            nw = max(1, int(w * th / h))
            imgs.append(cv2.resize(im, (nw, th)))
        if not imgs:
            meta["strip"] = meta["latest"]
            return meta
        strip = np.hstack(imgs)
        # pan label (shows facefind/patrol motion)
        pan = None
        try:
            h = _http_json(
                SNAPSHOT_URL.replace("/snapshot", "/health"), timeout=1.5
            )
            pan = h.get("pan_actual")
            meta["pan_actual"] = pan
            meta["camera_sweeping"] = h.get("sweeping")
            meta["camera_seq"] = h.get("seq")
        except Exception:
            pass
        bar = np.zeros((28, strip.shape[1], 3), dtype=np.uint8)
        label = (
            f"live ring ~30fps  n={len(imgs)} stride={BUF_STRIDE}  "
            f"older->newer  pan={pan if pan is not None else '?'}  "
            f"seq={seqs[-1] if seqs else '?'}"
        )
        cv2.putText(
            bar,
            label[:110],
            (8, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        strip = np.vstack([bar, strip])
        # Always refresh the LIVE preview path (facefind/patrol must show here)
        live = WORK / "strip_live.jpg"
        cv2.imwrite(str(live), strip, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        # Stable path for model load (Gemma loads weights then opens file — must not vanish)
        pack_vision = WORK / "pack_vision.jpg"
        cv2.imwrite(str(pack_vision), strip, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        # Timestamped audit copy
        out = WORK / f"strip_{int(time.time())}.jpg"
        cv2.imwrite(str(out), strip, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        for old in sorted(WORK.glob("strip_*.jpg"))[:-8]:
            if old.name in ("strip_live.jpg", "pack_vision.jpg"):
                continue
            old.unlink(missing_ok=True)
        meta["ok"] = True
        meta["strip"] = str(pack_vision)  # durable for VLM worker
        meta["strip_audit"] = str(out)
        meta["strip_live"] = str(live)
        meta["strip_w"] = int(strip.shape[1])
        meta["strip_h"] = int(strip.shape[0])
        # also keep latest still from last jpeg
        if latest is None and jpegs:
            lp = WORK / f"frame_{int(time.time())}.jpg"
            lp.write_bytes(jpegs[-1])
            meta["latest"] = str(lp)
        # always refresh live still
        if jpegs:
            (WORK / "frame_live.jpg").write_bytes(jpegs[-1])
            meta["latest_live"] = str(WORK / "frame_live.jpg")
        # Motion ghosts for heading (low-res analysis product — not face ID)
        try:
            from .motion_vision import build_motion_pack

            g = build_motion_pack(
                jpegs,
                out_dir=WORK,
                scale=float(os.environ.get("RAIL_ORACLE_GHOST_SCALE", "0.35")),
                write_video=os.environ.get("RAIL_ORACLE_SCAN_VIDEO", "1")
                not in ("0", "false", "no"),
                fps=float(os.environ.get("RAIL_ORACLE_SCAN_FPS", "15")),
            )
            meta["motion"] = {
                "ok": g.get("ok"),
                "heading": g.get("heading"),
                "trail": g.get("trail"),
                "heat": g.get("heat"),
                "heading_img": g.get("heading_img"),
                "video": g.get("video"),
                "n_frames": g.get("n_frames"),
            }
            if g.get("heading_img"):
                meta["ghost_heading"] = g["heading_img"]
        except Exception as ge:
            meta["motion"] = {"ok": False, "error": f"ghost: {ge}"[:160]}
        return meta
    except Exception as e:
        meta["error"] = f"strip: {e}"[:200]
        # fallback: last single frame
        if jpegs and latest is None:
            lp = WORK / f"frame_{int(time.time())}.jpg"
            lp.write_bytes(jpegs[-1])
            meta["latest"] = str(lp)
            meta["strip"] = str(lp)
        else:
            meta["strip"] = meta["latest"]
        return meta



def refresh_live_strip() -> dict[str, Any]:
    """Cheap strip rebuild from the live ring — does NOT run models.

    Keeps /v1/oracle/strip.jpg moving during facefind / PTZ while VLMs are busy.
    """
    return _grab_buffer_pack()


def _looks_like_selftalk(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    needles = (
        "okay, let's",
        "okay let's",
        "let's try to figure",
        "first, i need",
        "first i need",
        "the user wants",
        "putting this all together",
        "looking at the time-strip",
    )
    return any(n in t for n in needles) or (
        t.startswith("okay") and "{" not in t[:80]
    )


def _parse_oracle_json(text: str) -> dict[str, Any] | None:
    """Extract schema JSON from model free text (tolerate reasoner monologue)."""
    if not text:
        return None
    s = text.strip()
    if "</think>" in s:
        s = s.split("</think>")[-1].strip()
    # strip markdown fences
    if "```" in s:
        parts = s.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                s = p
                break

    def _try_load(chunk: str) -> dict[str, Any] | None:
        try:
            obj = json.loads(chunk)
        except Exception:
            return None
        if not isinstance(obj, dict):
            return None
        # prefer objects that look like our schema
        if "in_room" in obj or ("headcount" in obj and "people" in obj):
            return obj
        # sometimes nested under "oracle" / "result"
        for k in ("oracle", "result", "output", "answer"):
            v = obj.get(k)
            if isinstance(v, dict) and ("in_room" in v or "people" in v):
                return v
        return None

    # 1) whole string
    got = _try_load(s)
    if got:
        return _normalize_oracle_obj(got)

    # 2) brace-balanced objects, prefer last with in_room
    best = None
    i = 0
    while i < len(s):
        if s[i] != "{":
            i += 1
            continue
        depth = 0
        for j in range(i, len(s)):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    cand = _try_load(s[i : j + 1])
                    if cand:
                        best = cand
                    i = j + 1
                    break
        else:
            break
    if best:
        return _normalize_oracle_obj(best)

    # 3) first { to last } fallback
    a, b = s.find("{"), s.rfind("}")
    if a >= 0 and b > a:
        got = _try_load(s[a : b + 1])
        if got:
            return _normalize_oracle_obj(got)
    return None


def _normalize_oracle_obj(obj: dict[str, Any]) -> dict[str, Any]:
    """Fix cosmos enum parroting (the user|another person, face|body|...)."""
    ir = obj.get("in_room")
    if not isinstance(ir, dict) and "headcount" in obj:
        ir = {
            "headcount": obj.get("headcount"),
            "people": obj.get("people") or [],
            "unidentified": obj.get("unidentified", 0),
            "count_trust": obj.get("count_trust"),
            "note": obj.get("note"),
        }
        obj = {
            "in_room": ir,
            "speaking": obj.get("speaking") or {},
            "cannot_do": obj.get("cannot_do") or [],
        }
    ir = obj.get("in_room") if isinstance(obj.get("in_room"), dict) else {}
    people = ir.get("people") or []
    clean = []
    allowed = {"user", "another person", "a guest", "a guest", "guest3", "unknown"}
    for p in people:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "unknown")
        if "|" in pid:
            # enum parrot — discard as unknown unless single token
            pid = "unknown"
        pid = pid.lower().strip()
        if pid not in allowed:
            pid = "unknown"
        ev = p.get("evidence")
        if isinstance(ev, str) and "|" in ev:
            ev = "face" if "face" in ev else "context"
        clean.append(
            {
                "id": pid,
                "certain": bool(p.get("certain")),
                "evidence": ev or "context",
            }
        )
    # drop unknown-only noise if headcount 0
    try:
        hc = int(ir.get("headcount") if ir.get("headcount") is not None else len(clean))
    except Exception:
        hc = len(clean)
    if hc == 0:
        clean = []
    else:
        # drop pure unknowns if we also have named
        named = [p for p in clean if p["id"] != "unknown"]
        if named:
            clean = named
    ir = dict(ir)
    ir["people"] = clean
    ir["headcount"] = len(clean) if clean else (0 if hc == 0 else hc)
    sp = obj.get("speaking") if isinstance(obj.get("speaking"), dict) else {}
    src = sp.get("source")
    if isinstance(src, str) and "|" in src:
        src = "silence"
    sp = {
        "id": sp.get("id"),
        "text": sp.get("text") or "",
        "source": src,
        "conf": sp.get("conf", 0.0),
    }
    if isinstance(sp.get("id"), str) and "|" in sp["id"]:
        sp["id"] = None
    return {"in_room": ir, "speaking": sp, "cannot_do": obj.get("cannot_do") or []}


def _reasoner_answer_text(text: str) -> str:
    """Post-thinking answer body (NVIDIA reasoner often thinks then answers)."""
    if not text:
        return ""
    s = text.strip()
    # HF / chat templates may emit </think> or similar
    for tag in ("</think>", "</thinking>", "</reasoning>"):
        if tag in s:
            s = s.split(tag)[-1].strip()
    # drop leading "Okay, let's see..." monologue if a blank line separates answer
    if s.lower().startswith("okay") and "\n\n" in s:
        parts = [p.strip() for p in s.split("\n\n") if p.strip()]
        if len(parts) >= 2:
            s = parts[-1]
    return s


def _format_line_from_schema(parsed: dict[str, Any] | None, raw: str | None) -> str:
    """Board line: schema when present, else post-think natural language (Cosmos path)."""
    if parsed:
        ir = parsed.get("in_room") or {}
        people = ir.get("people") or []
        ids = [p.get("id") for p in people if isinstance(p, dict)]
        sp = parsed.get("speaking") or {}
        return (
            f"IN ROOM: headcount={ir.get('headcount')} people={ids} "
            f"trust={ir.get('count_trust')} note={ir.get('note')!r}\n"
            f"SPEAKING: id={sp.get('id')} text={sp.get('text')!r} "
            f"source={sp.get('source')} conf={sp.get('conf')}"
        )
    ans = _reasoner_answer_text(raw or "")
    if not ans:
        return "NO_OUTPUT: empty reasoner text"
    # Usable vision judgment even without JSON (NVIDIA reasoner default)
    preview = ans.replace("\n", " ").strip()
    if len(preview) > 500:
        preview = preview[:497] + "…"
    return f"REASONER: {preview}"


def look_around_camera(
    *,
    stop_ids: list[str] | None = None,
    engage_sec: float = 1.8,
    use_facefinder: bool = True,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """POST on-demand look-around tool to rail-camera (facefinder + door home)."""
    body: dict[str, Any] = {
        "engage_sec": engage_sec,
        "facefinder": use_facefinder,
    }
    if stop_ids:
        body["stop_ids"] = list(stop_ids)
    raw = json.dumps(body).encode()
    try:
        return _http_post_raw(
            LOOK_AROUND_URL,
            raw,
            {"Content-Type": "application/json"},
            timeout=timeout,
        )
    except Exception as e:
        return {
            "ok": False,
            "tool": "look_around",
            "error": f"look_around camera: {e}"[:300],
            "frames": [],
            "door_ok": False,
        }


def room_reference_paths() -> dict[str, Path]:
    """Empty-room / closed-door archive stills for look-around comparison.

    Search order: env, rails live stills, identity-store site mirror.
    """
    from .refs import HOUSE_ID_MIRROR, LIVE_DIR

    roots: list[Path] = []
    env = os.environ.get("RAIL_ORACLE_REF_DIR", "").strip()
    if env:
        roots.append(Path(env).expanduser())
    roots.extend([LIVE_DIR, HOUSE_ID_MIRROR])
    wanted = {
        "closed_door": ("home-empty-closed.jpg",),
        "empty_open_door": ("home-empty-open.jpg",),
        "empty_stop0": ("room-empty-stop0.jpg",),
        "empty_stop1": ("room-empty-stop1.jpg",),
    }
    found: dict[str, Path] = {}
    for key, names in wanted.items():
        for root in roots:
            for name in names:
                p = root / name
                if p.is_file() and p.stat().st_size > 1000:
                    found[key] = p
                    break
            if key in found:
                break
    return found


def _panel_from_bytes(raw: bytes, label: str, th: int = 200, color=(40, 220, 40)):
    import cv2
    import numpy as np

    arr = np.frombuffer(raw, dtype=np.uint8)
    im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if im is None:
        return None
    h, w = im.shape[:2]
    nw = max(1, int(w * th / h))
    im = cv2.resize(im, (nw, th))
    cv2.rectangle(im, (0, 0), (nw - 1, 28), (0, 0, 0), -1)
    cv2.putText(
        im,
        label[:28],
        (6, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        color,
        2,
        cv2.LINE_AA,
    )
    return im


def _panel_from_path(path: Path | str, label: str, th: int = 200, color=(180, 180, 40)):
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return _panel_from_bytes(p.read_bytes(), label, th=th, color=color)
    except Exception:
        return None


def _chunk_panels(panels: list, cols: int) -> list:
    """Split panels into rows of ``cols`` (for a few-row grid, not one long strip)."""
    if not panels:
        return []
    cols = max(1, int(cols))
    return [panels[i : i + cols] for i in range(0, len(panels), cols)]


def _hstack_pad(panels: list, target_w: int | None = None):
    import numpy as np

    if not panels:
        return None
    row = np.hstack(panels)
    if target_w is not None and row.shape[1] < target_w:
        pad = np.zeros((row.shape[0], target_w - row.shape[1], 3), dtype=np.uint8)
        row = np.hstack([row, pad])
    return row


def stitch_look_around_strip(
    frames: list[dict[str, Any]],
    *,
    out_path: Path | None = None,
    include_refs: bool = True,
    max_live: int = 4,
    max_refs: int = 2,
    cols_per_row: int = 2,
    panel_h: int = 220,
) -> Path | None:
    """Build Cosmos vision: few rows of REF + LIVE panels (not 30-frame flood).

    Layout (default):
      row 0-1: REF closed door + REF empty room (archive)
      row 2+:  LIVE desk / chair / … (capped)
    Only the current look-around ``frames`` list — never glob old files.
    """
    try:
        import cv2
        import numpy as np
    except Exception:
        return None

    th = int(panel_h)
    cols = max(1, int(cols_per_row))
    max_live = max(1, int(max_live))
    max_refs = max(0, int(max_refs))

    ref_panels = []
    if include_refs and max_refs > 0:
        refs = room_reference_paths()
        # Prefer closed door + one empty room still (not every archive stop)
        order = (
            ("closed_door", "REF closed door"),
            ("empty_stop0", "REF empty room"),
            ("empty_open_door", "REF empty open"),
            ("empty_stop1", "REF empty stop1"),
        )
        for key, lab in order:
            if len(ref_panels) >= max_refs:
                break
            if key not in refs:
                continue
            p = _panel_from_path(refs[key], lab, th=th, color=(80, 180, 255))
            if p is not None:
                ref_panels.append(p)

    # Prefer named room stops; drop failures unless we have nothing
    prefer = ("desk", "chair", "wrap_a", "wrap_b", "wrap_c")
    by_id: dict[str, dict[str, Any]] = {}
    for fr in frames:
        sid = str(fr.get("id") or "").strip()
        if not sid or sid in ("look_around_vision", "look_around_pack_vision"):
            continue
        # keep best (ok/path preferred)
        prev = by_id.get(sid)
        if prev is None or (fr.get("ok") or fr.get("path")):
            by_id[sid] = fr
    ordered: list[dict[str, Any]] = []
    for sid in prefer:
        if sid in by_id:
            ordered.append(by_id.pop(sid))
    ordered.extend(by_id[k] for k in sorted(by_id.keys()))
    ordered = ordered[:max_live]

    live_panels = []
    for fr in ordered:
        sid = str(fr.get("id") or "?")
        im = None
        path = fr.get("path")
        if path and Path(path).is_file() and Path(path).stat().st_size > 500:
            im = _panel_from_path(path, f"LIVE {sid}", th=th, color=(40, 220, 40))
        if im is None and fr.get("jpeg_b64"):
            try:
                raw = base64.b64decode(fr["jpeg_b64"])
                if len(raw) > 500:
                    im = _panel_from_bytes(
                        raw, f"LIVE {sid}", th=th, color=(40, 220, 40)
                    )
            except Exception:
                im = None
        if im is None and fr.get("ok") is False:
            im = np.zeros((th, int(th * 16 / 9), 3), dtype=np.uint8)
            cv2.putText(
                im,
                f"FAIL {sid}",
                (20, th // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 0, 220),
                2,
                cv2.LINE_AA,
            )
        if im is not None:
            live_panels.append(im)

    all_rows_panels: list[list] = []
    if ref_panels:
        all_rows_panels.extend(_chunk_panels(ref_panels, cols))
    if live_panels:
        all_rows_panels.extend(_chunk_panels(live_panels, cols))
    if not all_rows_panels:
        return None

    # normalize panel widths within each row, then pad rows to same width
    built_rows = []
    for chunk in all_rows_panels:
        built_rows.append(np.hstack(chunk))
    max_w = max(r.shape[1] for r in built_rows)
    padded = []
    for r in built_rows:
        if r.shape[1] < max_w:
            pad = np.zeros((r.shape[0], max_w - r.shape[1], 3), dtype=np.uint8)
            r = np.hstack([r, pad])
        padded.append(r)
    body = np.vstack(padded)
    bar_h = 40
    bar = np.zeros((bar_h, body.shape[1], 3), dtype=np.uint8)
    title = (
        f"LOOK-AROUND  {len(padded)} rows x ~{cols}  "
        f"REF={len(ref_panels)} LIVE={len(live_panels)}  "
        f"(capped, not 30fps ring)  then door home"
    )
    cv2.putText(
        bar,
        title[:150],
        (8, 26),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (220, 220, 220),
        1,
        cv2.LINE_AA,
    )
    strip = np.vstack([bar, body])
    out = out_path or (WORK / "look_around_vision.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), strip, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    try:
        import shutil

        shutil.copy(out, WORK / "look_around_pack_vision.jpg")
    except Exception:
        pass
    return out


def build_cosmos_reasoner_sample(
    vision_path: Path | str,
    *,
    prompt: str | None = None,
    name: str = "oracle_now",
) -> dict[str, Any]:
    """NVIDIA-aligned reasoner sample JSON (testable pure builder).

    Matches cosmos-framework inputs/reasoner/reasoner_image.json shape plus
    defaults/reasoner/sample_args.json sampling fields.
    """
    vp = Path(vision_path)
    return {
        "name": name,
        "model_mode": "reasoner",
        "prompt": prompt if prompt is not None else ASK_NOW_COSMOS,
        "vision_path": str(vp.resolve() if vp.exists() else vp),
        "max_new_tokens": COSMOS_MAX_NEW_TOKENS,
        "do_sample": COSMOS_DO_SAMPLE,
        "temperature": 1.0,
        "top_k": None,
        "top_p": None,
        "repetition_penalty": 1.0,
        "presence_penalty": 0.0,
        "video_fps": None,
    }


def cosmos_result_from_raw(raw: str) -> dict[str, Any]:
    """Turn reasoner text into board fields (JSON if present, else NL)."""
    answer = _reasoner_answer_text(raw)
    parsed = _parse_oracle_json(raw) or _parse_oracle_json(answer)
    line = _format_line_from_schema(parsed, raw)
    usable = bool(answer.strip()) and not answer.strip().startswith("Traceback")
    out: dict[str, Any] = {
        "ok": usable,
        "parsed": parsed,
        "answer_text": answer[:4000],
        "in_room_and_speaking": line,
        "selftalk": bool(_looks_like_selftalk(raw) and not parsed),
    }
    if parsed:
        out["in_room"] = parsed.get("in_room")
        out["speaking"] = parsed.get("speaking")
    elif usable:
        # soft presence shell so API is not empty when only NL
        out["in_room"] = {
            "headcount": None,
            "people": [],
            "unidentified": None,
            "count_trust": "unsure",
            "note": answer[:400].replace("\n", " "),
        }
        out["speaking"] = {
            "id": None,
            "text": "",
            "source": "unsure",
            "conf": 0.0,
        }
    if not usable:
        out["error"] = "empty or failed reasoner output"
    return out


# Optional inject: local presence dict getter (avoids HTTP self-call)
_presence_getter = None
# Optional inject: apply VL/CODE into DoorInventory SoT (the User 2026-07-27)
_sot_apply_vl = None  # (entered, left, source, stamp_names, note, face_certain) -> Any
_sot_code_write = None  # (names, sims, source) -> Any


def set_presence_getter(fn) -> None:
    global _presence_getter
    _presence_getter = fn


def set_sot_hooks(*, apply_vl=None, code_write_through=None) -> None:
    """Wire presence SoT write path from oracle_bench (server startup)."""
    global _sot_apply_vl, _sot_code_write
    if apply_vl is not None:
        _sot_apply_vl = apply_vl
    if code_write_through is not None:
        _sot_code_write = code_write_through


def _env_apply_sot() -> bool:
    return os.environ.get("RAIL_ORACLE_APPLY_SOT", "1") not in ("0", "false", "no")


def _apply_gemma_event_to_sot(out: dict[str, Any]) -> None:
    """Push Gemma motion-event ±1 into :8191 inventory SoT."""
    if not _env_apply_sot() or _sot_apply_vl is None:
        return
    if not out or not out.get("ok"):
        return
    verdict = out.get("verdict") or {}
    entered = int(verdict.get("entered") or 0)
    left = int(verdict.get("left") or 0)
    if entered == 0 and left == 0:
        return
    # id_stamps from faceid after count — names only
    stamp_names: list[str] = []
    for note in out.get("id_stamps") or []:
        # "faceid stamped the user sim=0.62" or "faceid stamped unknown→the user"
        s = str(note)
        if "→" in s:
            stamp_names.append(s.split("→")[-1].split()[0])
        elif "stamped" in s:
            parts = s.replace("→", " ").split()
            for p in parts:
                pl = p.lower().strip(",")
                if pl in ("user", "another person", "a guest", "a guest", "guest3") or (
                    pl.isalpha() and pl not in ("faceid", "stamped", "sim", "new", "slot")
                ):
                    if pl not in ("faceid", "stamped", "sim", "new", "slot", "unknown"):
                        stamp_names.append(pl)
    # people after stamp
    for p in (out.get("in_room") or {}).get("people") or []:
        pid = str(p.get("id") or "")
        if pid and not pid.startswith("unknown") and p.get("certain"):
            stamp_names.append(pid.lower())
    stamp_names = list(dict.fromkeys(stamp_names))
    try:
        _sot_apply_vl(
            entered,
            left,
            source="gemma",
            stamp_names=stamp_names,
            note=(verdict.get("note") or "")[:200],
            face_certain=bool(stamp_names),
        )
    except Exception as e:
        out["_sot_apply_error"] = str(e)[:200]


def _apply_code_write_through(code_out: dict[str, Any]) -> None:
    """CODE → SoT: live door faces may enter; memory sticky must not resurrect leavers."""
    if not _env_apply_sot() or _sot_code_write is None:
        return
    if not code_out or not code_out.get("ok", True):
        return
    ir = code_out.get("in_room") or {}
    people = ir.get("people") or []
    rm = code_out.get("room_memory") or {}
    present = rm.get("present") or people
    names: list[str] = []
    sims: dict[str, float] = {}
    live_face: dict[str, bool] = {}
    rows = present if isinstance(present, list) else list((present or {}).values())
    if isinstance(present, dict) and present and not rows:
        rows = list(present.values())
    if isinstance(present, dict) and all(isinstance(v, dict) for v in present.values()):
        rows = list(present.values())
    # face_hits this pack = truly live at door
    fh = (code_out.get("face_hits") or {}).get("people_best") or {}
    for pid, sim in (fh or {}).items():
        pid = str(pid).lower()
        if pid and not pid.startswith("unknown") and float(sim or 0) >= 0.40:
            live_face[pid] = True
            sims[pid] = max(float(sims.get(pid) or 0), float(sim or 0))
            if pid not in names:
                names.append(pid)
    for p in rows:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or p.get("name") or "").lower()
        if not pid or pid.startswith("unknown"):
            continue
        # only real enrolled house IDs write through to SoT
        if pid not in ("user", "another person", "a guest", "a guest", "guest3") and not live_face.get(
            pid
        ):
            # still allow live face_hits path above to have filled live_face
            if not any(pid == k for k in live_face):
                continue
        sim = float(p.get("sim") or p.get("last_sim") or 0)
        in_strip = bool(p.get("in_this_strip"))
        evidence = str(p.get("evidence") or "")
        if in_strip and sim >= 0.40:
            live_face[pid] = True
        if sim:
            sims[pid] = max(float(sims.get(pid) or 0), sim)
        # Memory sticky alone must not invent SoT bodies (tombstone path);
        # only live door face or already-live hits list names for write-through.
        if live_face.get(pid) or evidence in ("face", "faceid") or in_strip:
            if pid not in names:
                names.append(pid)
        elif evidence == "memory" and sim >= 0.40:
            # refresh-only list: service refuses new add without live_face
            if pid not in names:
                names.append(pid)
    if not names:
        return
    try:
        _sot_code_write(names, source="code", sims=sims, live_face=live_face)
    except TypeError:
        # older hook signature
        try:
            _sot_code_write(names, source="code", sims=sims)
        except Exception as e:
            code_out["_sot_write_error"] = str(e)[:200]
    except Exception as e:
        code_out["_sot_write_error"] = str(e)[:200]


def _stt_from_audio(audio: bytes | None) -> dict[str, Any]:
    """POST short clip to rail-stt. Returns speaking dict — fair-pack sanitized.

    rail-stt often embeds live /v1/presence into stamp.presence and invents
    person_id=sole_present from the roster even when mic is silent. That
    leaks inventory to VLMs (Cosmos was "winning" by reading it). Strip it.
    """
    if not audio or len(audio) < 1000:
        return {"id": None, "text": None, "source": None, "note": "no audio"}
    try:
        pcm = audio
        if audio[:4] == b"RIFF":
            pcm = audio[44:]
        res = _http_post_raw(
            STT_URL,
            pcm,
            {
                "Content-Type": "application/octet-stream",
                "X-Sample-Rate": "16000",
            },
            timeout=60,
        )
        stamp = dict(res.get("stamp") or {})
        # REMOVE presence inventory leak
        stamp.pop("presence", None)
        stamp.pop("roster", None)
        src = stamp.get("source")
        text = (res.get("text") or "").strip()
        pid = res.get("person_id") or stamp.get("person_id")
        reason = res.get("reason")
        rms = res.get("rms_db")
        voice_sim = stamp.get("voice_emb_sim")
        # Silence / sole_present is NOT a fair sensor — it's inventory
        if src in ("sole_present", "roster", "presence") or reason in (
            "silence_rms",
            "both_empty_or_rejected",
        ):
            pid = None
            src = "silence" if not text else src
            stamp["person_id"] = None
            stamp["source"] = src
        # weak voice emb alone without text → don't name
        try:
            if voice_sim is not None and float(voice_sim) < 0.35 and not text:
                pid = None
        except Exception:
            pass
        return {
            "id": pid,
            "text": text or None,
            "source": src,
            "stamp": {
                "person_id": stamp.get("person_id"),
                "source": stamp.get("source"),
                "confidence": stamp.get("confidence"),
                "voice_emb_sim": voice_sim,
                "vocative_hits": stamp.get("vocative_hits") or [],
                # deliberately NO presence / roster
            },
            "confidence": res.get("confidence"),
            "rms_db_stt": rms,
            "reason": reason,
            "note": "rail-stt fair (inventory stripped)",
        }
    except Exception as e:
        return {"id": None, "text": None, "error": str(e)[:200], "note": "STT fail"}


def _color_name_hsv(h: float, s: float, v: float, *, hair: bool = False) -> str:
    """h in [0,180] OpenCV, s,v in [0,255].

    Hair mode refuses plant/wall false positives (green/cyan/blue) and maps
    low-sat warm dark tones to brown/black (enroll refs: another person brown, etc.).
    """
    if v < 45:
        return "black"
    if s < 40:
        if v > 200:
            return "white"
        if v > 140:
            return "gray"
        if hair and v < 110:
            return "brown"  # dark desaturated hair
        return "dark_gray"
    # brown hair / skin-adjacent: warm hue, moderate sat, not bright
    if hair and v < 170 and s < 120 and (h < 28 or h >= 160):
        return "brown"
    if h < 10 or h >= 160:
        return "red"
    if h < 22:
        return "orange" if not hair or v > 120 else "brown"
    if h < 35:
        # yellow on hair is almost always wall/lamp — treat as brown if darkish
        if hair and v < 180:
            return "brown"
        return "yellow"
    if h < 85:
        if hair:
            return "brown" if v < 140 else "gray"  # never "green" hair from estimator
        return "green"
    if h < 100:
        return "gray" if hair else "cyan"
    if h < 130:
        return "gray" if hair else "blue"
    if h < 160:
        return "brown" if hair else "purple"
    return "red"


def _dominant_colors(bgr_roi, k: int = 2, *, hair: bool = False) -> list[str]:
    """Return up to k named dominant colors from a BGR crop."""
    import cv2
    import numpy as np

    if bgr_roi is None or bgr_roi.size == 0:
        return []
    h, w = bgr_roi.shape[:2]
    if h < 4 or w < 4:
        return []
    # downsample
    small = cv2.resize(bgr_roi, (max(8, w // 4), max(8, h // 4)))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    pixels = hsv.reshape(-1, 3).astype(np.float32)
    if hair:
        # keep darker / moderate pixels; drop bright walls
        mask = pixels[:, 2] < 200
        if int(mask.sum()) >= 20:
            pixels = pixels[mask]
    if len(pixels) < 10:
        return []
    # k-means for dominant
    kk = min(k, max(1, len(pixels) // 20))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 12, 1.0)
    try:
        _, labels, centers = cv2.kmeans(
            pixels, kk, None, criteria, 3, cv2.KMEANS_PP_CENTERS
        )
    except Exception:
        mean = pixels.mean(axis=0)
        return [_color_name_hsv(mean[0], mean[1], mean[2], hair=hair)]
    # order by cluster size
    counts = np.bincount(labels.flatten(), minlength=kk)
    order = np.argsort(-counts)
    names = []
    for i in order:
        c = centers[i]
        names.append(
            _color_name_hsv(float(c[0]), float(c[1]), float(c[2]), hair=hair)
        )
    # unique preserve order
    out: list[str] = []
    for n in names:
        if n not in out:
            out.append(n)
    return out[:k]


def _appearance_from_face(img, face) -> dict[str, Any]:
    """Clothing / hair / height cues from face bbox — enter metadata for leave ID.

    End-state: identity may fail at leave (back turned) but appearance stays.
    Hair colors use a tighter ROI + hair-safe palette (refs on disk for baseline).
    """
    import cv2
    import numpy as np

    bbox = getattr(face, "bbox", None)
    if bbox is None:
        return {}
    x1, y1, x2, y2 = [int(v) for v in bbox[:4]]
    H, W = img.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(W - 1, x2), min(H - 1, y2)
    fh, fw = max(1, y2 - y1), max(1, x2 - x1)
    # hair: tight band above face, center-weighted (avoid walls left/right)
    hy1 = max(0, y1 - int(fh * 0.45))
    hy2 = max(hy1 + 1, y1 + int(fh * 0.08))
    hx1 = max(0, x1 + int(fw * 0.15))
    hx2 = min(W, x2 - int(fw * 0.15))
    if hx2 <= hx1:
        hx1, hx2 = max(0, x1), min(W, x2)
    hair_roi = img[hy1:hy2, hx1:hx2]
    # torso / top clothing: below face
    ty1 = min(H - 1, y2 + int(fh * 0.05))
    ty2 = min(H, y2 + int(fh * 1.8))
    tx1 = max(0, x1 - int(fw * 0.35))
    tx2 = min(W, x2 + int(fw * 0.35))
    top_roi = img[ty1:ty2, tx1:tx2]
    # lower body if room
    ly1 = min(H - 1, ty2)
    ly2 = min(H, ty2 + int(fh * 1.4))
    bot_roi = img[ly1:ly2, tx1:tx2] if ly2 > ly1 else None

    hair_colors = _dominant_colors(hair_roi, 2, hair=True)
    top_colors = _dominant_colors(top_roi, 2, hair=False)
    bot_colors = _dominant_colors(bot_roi, 2, hair=False) if bot_roi is not None else []

    # height cues (relative, door FOV): face vertical position & scale
    face_frac_h = fh / max(1, H)
    face_center_y = ((y1 + y2) / 2) / max(1, H)
    # larger face_frac ≈ closer; lower face_center_y with large face ≈ taller when same distance
    # crude ordinal bucket for metadata
    if face_frac_h > 0.22:
        dist = "near"
    elif face_frac_h > 0.12:
        dist = "mid"
    else:
        dist = "far"
    if face_center_y < 0.35:
        height_cue = "taller_or_near"
    elif face_center_y > 0.55:
        height_cue = "shorter_or_far"
    else:
        height_cue = "average"

    gender = None
    try:
        from .face_engine import FaceEngine

        g, _ = FaceEngine.gender(face)
        gender = g
    except Exception:
        sex = getattr(face, "sex", None)
        if sex is not None:
            try:
                gender = "M" if int(sex) == 1 else "F"
            except Exception:
                pass

    return {
        "hair_colors": hair_colors,
        "top_colors": top_colors,
        "bottom_colors": bot_colors,
        "height_cue": height_cue,
        "distance_cue": dist,
        "face_height_frac": round(face_frac_h, 3),
        "face_center_y": round(face_center_y, 3),
        "gender_soft": gender,
        "bbox": [x1, y1, x2, y2],
        "summary": (
            f"hair={'+'.join(hair_colors) or '?'} "
            f"top={'+'.join(top_colors) or '?'} "
            f"bottom={'+'.join(bot_colors) or '?'} "
            f"height={height_cue} dist={dist}"
            + (f" gender={gender}" if gender else "")
        ),
    }


def appearance_ref_dirs() -> list[Path]:
    """Where enroll / reference face shots live (identity-store first)."""
    roots: list[Path] = []
    hid = Path(os.environ.get("HOUSE_ID_ROOT", str(_paths.IDENTITY)))
    roots.append(hid / "people")
    return roots


def load_appearance_baseline(person_id: str) -> dict[str, Any] | None:
    """Cached baseline from enroll refs: people/<id>/appearance.json or rebuild."""
    pid = (person_id or "").strip().lower()
    if not pid:
        return None
    hid = Path(os.environ.get("HOUSE_ID_ROOT", str(_paths.IDENTITY)))
    path = hid / "people" / pid / "appearance.json"
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return None


def rebuild_appearance_baselines(*, write: bool = True) -> dict[str, dict[str, Any]]:
    """Compute hair/top baselines from face/refs or enroll-photos on disk."""
    import cv2

    from .face_engine import get_face_engine

    eng = get_face_engine()
    out: dict[str, dict[str, Any]] = {}
    hid = Path(os.environ.get("HOUSE_ID_ROOT", str(_paths.IDENTITY)))
    people = hid / "people"
    if not people.is_dir():
        return out
    for person_dir in sorted(people.iterdir()):
        if not person_dir.is_dir():
            continue
        pid = person_dir.name
        refs = list((person_dir / "face" / "refs").glob("*.jpg")) + list(
            (person_dir / "face" / "refs").glob("*.png")
        )
        # fallback museum flat names
        if not refs:
            for base in appearance_ref_dirs():
                if base.name == "enroll-photos" and base.is_dir():
                    refs = list(base.glob(f"{pid}_*.jpg")) + list(
                        base.glob(f"{pid}_*.png")
                    )
                    if refs:
                        break
        if not refs:
            continue
        hair_votes: dict[str, int] = {}
        top_votes: dict[str, int] = {}
        n_ok = 0
        for rp in refs[:8]:
            img = cv2.imread(str(rp))
            if img is None:
                continue
            faces = eng.detect(img)
            if not faces:
                continue
            face = max(
                faces,
                key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
            )
            app = _appearance_from_face(img, face)
            for c in app.get("hair_colors") or []:
                hair_votes[c] = hair_votes.get(c, 0) + 1
            for c in app.get("top_colors") or []:
                top_votes[c] = top_votes.get(c, 0) + 1
            n_ok += 1
        if n_ok == 0:
            continue
        hair = [c for c, _ in sorted(hair_votes.items(), key=lambda x: -x[1])[:2]]
        top = [c for c, _ in sorted(top_votes.items(), key=lambda x: -x[1])[:2]]
        base = {
            "person_id": pid,
            "source": "enroll_refs",
            "n_refs": n_ok,
            "hair_colors": hair,
            "top_colors": top,
            "summary": f"hair={'+'.join(hair) or '?'} top={'+'.join(top) or '?'} (ref)",
            "schema": "appearance_baseline/v1",
        }
        out[pid] = base
        if write:
            path = person_dir / "appearance.json"
            try:
                path.write_text(json.dumps(base, indent=2))
            except Exception:
                pass
    return out


def _coarse_tone(colors: list[str] | None) -> str:
    """VL-style coarse tone (CosmOS reports hair/shirt as dark|light|… — not junk HSV)."""
    cols = [c.lower() for c in (colors or []) if c]
    if not cols:
        return "?"
    dark = {"black", "brown", "dark_gray"}
    light = {"white", "gray", "yellow", "orange"}
    if any(c in dark for c in cols):
        return "dark"
    if any(c in light for c in cols):
        return "light"
    return cols[0]


def _appearance_summary(app: dict[str, Any]) -> str:
    """Human + CosmOS-comparable summary: hair/shirt coarse first, fine colors secondary."""
    hair_c = list(app.get("hair_colors") or [])
    top_c = list(app.get("top_colors") or [])
    bot_c = list(app.get("bottom_colors") or [])
    hair_t = app.get("hair_tone") or _coarse_tone(hair_c)
    shirt_t = app.get("shirt_tone") or _coarse_tone(top_c)
    fine_h = "+".join(hair_c) if hair_c else "?"
    fine_t = "+".join(top_c) if top_c else "?"
    bits = [
        f"hair={hair_t}",
        f"shirt={shirt_t}",
        f"fine_hair={fine_h}",
        f"fine_top={fine_t}",
        f"bottom={'+'.join(bot_c) or '?'}",
    ]
    if app.get("height_cue"):
        bits.append(f"height={app['height_cue']}")
    if app.get("distance_cue"):
        bits.append(f"dist={app['distance_cue']}")
    if app.get("gender_soft"):
        bits.append(f"gender={app['gender_soft']}")
    if app.get("hair_source") == "enroll_ref":
        bits.append("[hair:enroll_ref]")
    return " ".join(bits)


def apply_appearance_baseline(
    person_id: str | None, live: dict[str, Any] | None
) -> dict[str, Any]:
    """Prefer enroll-ref hair when live estimator is implausible; keep live top (today's clothes).

    CosmOS-style coarse tones (dark/light) are always filled for fair leave matching
    and human-readable Oracle lines — never green/yellow plant false positives on hair.
    """
    live = dict(live or {})
    pid = (person_id or "").strip().lower()
    base = load_appearance_baseline(pid) if pid else None
    hair = list(live.get("hair_colors") or [])
    bad_hair = {"green", "cyan", "blue", "purple", "yellow"}
    # if live hair is empty or mostly background junk, use ref hair
    if base and (not hair or any(c in bad_hair for c in hair)):
        live["hair_colors"] = list(base.get("hair_colors") or hair)
        live["hair_source"] = "enroll_ref"
    # optional: if live has no top, fill ref (weak — clothes change)
    if base and not live.get("top_colors") and base.get("top_colors"):
        live["top_colors"] = list(base["top_colors"])
        live["top_source"] = "enroll_ref"
    live["hair_tone"] = _coarse_tone(live.get("hair_colors"))
    live["shirt_tone"] = _coarse_tone(live.get("top_colors"))
    live["summary"] = _appearance_summary(live)
    return live


def _entry_cache_dir() -> Path:
    d = WORK / "entry_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_entry_crop(pid: str, img, face) -> str | None:
    """Cache enter crop for leave matching (child's game: who left?)."""
    import cv2

    bbox = getattr(face, "bbox", None)
    if bbox is None or img is None:
        return None
    try:
        x1, y1, x2, y2 = [int(v) for v in bbox[:4]]
        H, W = img.shape[:2]
        fh, fw = max(1, y2 - y1), max(1, x2 - x1)
        # face + hair + torso — what leave sighting will look like from the back/side too
        x1 = max(0, x1 - int(fw * 0.4))
        x2 = min(W, x2 + int(fw * 0.4))
        y1 = max(0, y1 - int(fh * 0.5))
        y2 = min(H, y2 + int(fh * 2.2))
        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        path = _entry_cache_dir() / f"{pid}.jpg"
        cv2.imwrite(str(path), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        return str(path)
    except Exception:
        return None


def _face_hits_on_jpegs(jpegs: list[bytes], thr: float | None = None) -> dict[str, Any]:
    """Face emb match + appearance profiles (once per pack, shared by all Oracles).

    Also writes entry_cache/<id>.jpg on best sighting — leave is a match game
    against these crops + color summary.
    """
    thr = float(thr if thr is not None else os.environ.get("RAIL_ORACLE_FACE_THR", "0.40"))
    out: dict[str, Any] = {
        "ok": False,
        "thr": thr,
        "n_jpegs": len(jpegs),
        "hits": [],
        "people_best": {},
        "appearances": {},  # id -> best appearance dict
        "game": "child_match_leave_to_enter_cache",
    }
    if not jpegs:
        out["error"] = "no jpegs"
        return out
    try:
        import cv2
        import numpy as np
        from .enrollments import load_enrolled
        from .face_engine import get_face_engine

        bank = load_enrolled()
        eng = get_face_engine()
        people_best: dict[str, float] = {}
        best_det: dict[str, float] = {}
        appearances: dict[str, Any] = {}
        hits = []
        sample = list(enumerate(jpegs))
        for idx, raw in sample:
            arr = np.frombuffer(raw, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            faces = eng.detect(img) or []
            for f in faces:
                emb = eng.embed(f)
                if emb is None:
                    continue
                # Static-face killbook (2026-08-07): this raw np.dot loop
                # bypassed enrollments.match(), so the hallway portraits /
                # door posters kept re-adding "another person" via code_write_through
                # even after the match()-level gate landed. Same gate here.
                try:
                    from .static_faces import (
                        in_masked_zone,
                        killbook,
                        too_small_to_name,
                    )

                    is_static, _s = killbook().is_static(emb)
                    if is_static:
                        continue
                    if too_small_to_name(getattr(f, "bbox", None)):
                        continue  # hallway portrait, not a person
                    # Door-view fixed-position mask: the hallway portraits sit
                    # at x .686-.705 / y .07-.32 in every door frame; cosine
                    # can't safely split portrait-of-another person from another person
                    # (adaptive-thr window), position can. A real transit is
                    # detected across many positions, so a masked sliver can't
                    # hide a person.
                    bb = getattr(f, "bbox", None)
                    if bb is not None and in_masked_zone(
                        "door_home", bb, img.shape
                    ):
                        continue
                except Exception:
                    pass
                best_name, best_sim = None, -1.0
                for name, embs in bank.items():
                    for e in embs:
                        sim = float(np.dot(emb, e))
                        if sim > best_sim:
                            best_sim, best_name = sim, name
                pid = best_name if best_sim >= thr else "unknown"
                det = round(float(getattr(f, "det_score", 0) or 0), 3)
                app = _appearance_from_face(img, f)
                if pid != "unknown":
                    app = apply_appearance_baseline(pid, app)
                hit = {
                    "frame_i": idx,
                    "id": pid,
                    "sim": round(best_sim, 3),
                    "det": det,
                    "appearance": app.get("summary"),
                }
                hits.append(hit)
                if pid != "unknown":
                    if best_sim >= people_best.get(pid, -1.0):
                        people_best[pid] = best_sim
                    # keep appearance from highest-det sighting + enter crop
                    if det >= best_det.get(pid, -1.0) and app:
                        best_det[pid] = det
                        crop = _save_entry_crop(pid, img, f)
                        appearances[pid] = {
                            **app,
                            "sim": round(best_sim, 3),
                            "det": det,
                            "frame_i": idx,
                            "captured_local": _iso(),
                            "entry_crop": crop,
                        }

                elif app and best_sim > 0.2:
                    # unknown body — match later to present enter cache (who left?)
                    appearances.setdefault(
                        "unknown_pool",
                        [],
                    )
                    if isinstance(appearances.get("unknown_pool"), list):
                        appearances["unknown_pool"].append(
                            {
                                **app,
                                "sim": round(best_sim, 3),
                                "frame_i": idx,
                                "role": "leave_or_stranger_candidate",
                            }
                        )
        out["ok"] = True
        out["hits"] = hits
        out["people_best"] = {k: round(v, 3) for k, v in people_best.items()}
        out["appearances"] = appearances
        out["bank_ids"] = sorted(bank.keys())
        out["entry_cache_dir"] = str(_entry_cache_dir())
        return out
    except Exception as e:
        out["error"] = str(e)[:300]
        return out


def _appearance_overlap(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float:
    """Child's match game score 0..1 — clothing/hair sets (no fancy ML)."""
    if not a or not b:
        return 0.0
    score = 0.0
    weight = 0.0
    for key, w in (("top_colors", 0.45), ("hair_colors", 0.25), ("bottom_colors", 0.2)):
        sa, sb = set(a.get(key) or []), set(b.get(key) or [])
        if not sa and not sb:
            continue
        weight += w
        if sa and sb:
            score += w * (len(sa & sb) / max(1, len(sa | sb)))
    # height/distance cues
    for key, w in (("height_cue", 0.05), ("distance_cue", 0.05)):
        weight += w
        if a.get(key) and a.get(key) == b.get(key):
            score += w
    if a.get("gender_soft") and b.get("gender_soft"):
        weight += 0.1
        if a.get("gender_soft") == b.get("gender_soft"):
            score += 0.1
    if weight <= 0:
        return 0.0
    return round(score / weight, 3)


def classify_door_view(
    face_hits: dict[str, Any] | None,
    *,
    pan_actual: int | None = None,
    camera_blind: bool = False,
    door_pan: int = DOOR_HOME_PAN,
    pan_tol: int = DOOR_PAN_TOL,
) -> dict[str, Any]:
    """Is the door FOV trustworthy for enter/leave?"""
    on_door = True
    if pan_actual is not None:
        on_door = abs(int(pan_actual) - int(door_pan)) <= int(pan_tol)
    if camera_blind:
        return {
            "state": "obscured",
            "reason": "camera_blind",
            "on_door_home": on_door,
            "pan_actual": pan_actual,
        }
    if not on_door:
        return {
            "state": "off_door",
            "reason": "pan_not_door_home",
            "on_door_home": False,
            "pan_actual": pan_actual,
        }
    fh = face_hits or {}
    people = dict(fh.get("people_best") or {})
    hits = list(fh.get("hits") or [])
    pool = (fh.get("appearances") or {}).get("unknown_pool") or []
    if people:
        return {
            "state": "person",
            "reason": "enrolled_face",
            "on_door_home": True,
            "pan_actual": pan_actual,
            "ids": sorted(people.keys()),
        }
    if hits or (isinstance(pool, list) and pool):
        # body/face signal without enrolled match — could be leave candidate or clutter
        return {
            "state": "unclear_body",
            "reason": "unmatched_face_or_body",
            "on_door_home": True,
            "pan_actual": pan_actual,
            "n_hits": len(hits),
        }
    return {
        "state": "clear_empty",
        "reason": "door_visible_no_person",
        "on_door_home": True,
        "pan_actual": pan_actual,
    }


class RoomMemory:
    """Enter/leave ledger — HOUSE DOOR LAW (the User).

    Camera holds the only door. After ENTER you stay present without look-around.
    LEAVE only: (A) body leaving matches enter appearance (clothes/hair), or
               (B) door FOV obscured long enough → need look-around-facefind.
    Track body location for speech. Empty doorway ≠ empty room.
    """

    def __init__(
        self,
        path: Path = MEMORY_PATH,
        leave_sec: float = LEAVE_SEC,
        entry_grace_sec: float = ENTRY_GRACE_SEC,
        obscure_sec: float = OBSCURE_LOOKAROUND_SEC,
    ):
        self.path = path
        self.leave_sec = leave_sec  # leave *confirm* after exit-armed
        self.entry_grace_sec = entry_grace_sec
        self.obscure_sec = obscure_sec
        # open stays: id -> meta including appearance + location
        self.present: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        # closed enter→leave pairs (scrubbed appearance optional keep summary)
        self.closed: list[dict[str, Any]] = []
        self._obscure_since: float | None = None
        self._last_door_view: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.path.is_file():
                data = json.loads(self.path.read_text())
                self.present = dict(data.get("present") or {})
                self.events = list(data.get("events") or [])[-80:]
                self.closed = list(data.get("closed") or [])[-40:]
        except Exception:
            self.present = {}
            self.events = []
            self.closed = []

    def _save(self) -> None:
        try:
            # atomic: a crash mid-write must never corrupt room_memory.json
            atomic_write_text(
                self.path,
                json.dumps(
                    {
                        "present": self.present,
                        "events": self.events[-80:],
                        "closed": self.closed[-40:],
                        "leave_sec": self.leave_sec,
                        "updated_local": _iso(),
                    },
                    indent=2,
                    default=str,
                ),
            )
        except Exception:
            pass

    def _daily_scrub(self, now: float) -> int:
        """Drop closed pairs older than 24h; drop orphan open stays older than 24h miss."""
        day = 86400.0
        n = 0
        kept = []
        for c in self.closed:
            ts = float(c.get("leave_ts") or c.get("ts") or 0)
            if now - ts < day:
                kept.append(c)
            else:
                n += 1
        self.closed = kept
        for pid in list(self.present.keys()):
            prev = self.present[pid]
            last = float(prev.get("last_seen") or prev.get("first_seen") or now)
            if now - last > day:
                self.present.pop(pid, None)
                n += 1
        return n

    def note_at(
        self,
        pid: str,
        *,
        location: str = "in_room",
        sim: float = 0.5,
        appearance: dict[str, Any] | None = None,
        source: str = "look_around",
        now: float | None = None,
    ) -> dict[str, Any]:
        """Ensure person is sticky-present with a body location (speech map)."""
        now = now if now is not None else time.time()
        pid = (pid or "").strip().lower()
        if not pid or pid == "unknown":
            return {"ok": False, "error": "need real id"}
        prev = self.present.get(pid)
        app = appearance
        if app and not isinstance(app, dict):
            app = {"summary": str(app)}
        if isinstance(app, dict):
            app = apply_appearance_baseline(pid, app)
        if not prev:
            self.present[pid] = {
                "id": pid,
                "first_seen": now,
                "last_seen": now,
                "last_sim": float(sim),
                "last_seen_local": _iso(now),
                "miss_since": None,
                "appearance": app,
                "enter_local": _iso(now),
                "entry_grace_until": now - 1.0,  # past grace — already in room
                "exit_armed": False,
                "location": location,
                "location_source": source,
            }
            ev = {
                "kind": "enter",
                "id": pid,
                "sim": float(sim),
                "ts": now,
                "ts_local": _iso(now),
                "source": source,
                "location": location,
                "appearance": (app or {}).get("summary") if isinstance(app, dict) else None,
            }
            self.events.append(ev)
            self._save()
            return {"ok": True, "kind": "enter", "id": pid, "location": location}
        prev["last_seen"] = now
        prev["last_sim"] = max(float(prev.get("last_sim") or 0), float(sim))
        prev["last_seen_local"] = _iso(now)
        prev["miss_since"] = None
        prev["location"] = location
        prev["location_source"] = source
        if app and (not prev.get("appearance") or float((app or {}).get("det") or 0) >= float(
            (prev.get("appearance") or {}).get("det") or 0
        )):
            prev["appearance"] = app
        self._save()
        return {"ok": True, "kind": "refresh", "id": pid, "location": location}

    def apply_look_around_evidence(
        self,
        *,
        stop_hits: list[dict[str, Any]],
        speaking: dict[str, Any] | None = None,
        rails_names: list[str] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Merge look-around face hits + STT + rails sticky into memory/locations."""
        now = now if now is not None else time.time()
        applied: list[dict[str, Any]] = []
        # 1) enrolled faces at stops
        for h in stop_hits:
            pid = str(h.get("id") or "").lower()
            loc = str(h.get("location") or "in_room")
            if not pid or pid == "unknown":
                continue
            applied.append(
                self.note_at(
                    pid,
                    location=loc,
                    sim=float(h.get("sim") or 0.5),
                    appearance=h.get("appearance")
                    if isinstance(h.get("appearance"), dict)
                    else {"summary": h.get("appearance")}
                    if h.get("appearance")
                    else None,
                    source="look_around_face",
                    now=now,
                )
            )
        # 2) voice id → in room (likely speaker); prefer last face location if same id
        sp = speaking or {}
        sp_id = sp.get("id")
        if sp_id and str(sp_id).lower() not in ("", "none", "unknown"):
            pid = str(sp_id).lower()
            loc = "in_room"
            for h in reversed(stop_hits):
                if str(h.get("id") or "").lower() == pid and h.get("location"):
                    loc = str(h["location"])
                    break
            # if only one occupied stop, speaker likely there
            locs = [str(h.get("location")) for h in stop_hits if h.get("id") and h.get("id") != "unknown"]
            if len(set(locs)) == 1 and locs:
                loc = locs[0]
            applied.append(
                self.note_at(
                    pid,
                    location=loc,
                    sim=float((sp.get("stamp") or {}).get("voice_emb_sim") or 0.55),
                    source="look_around_voice",
                    now=now,
                )
            )
            if pid in self.present:
                self.present[pid]["likely_speaker"] = True
                self.present[pid]["last_transcript"] = (sp.get("text") or "")[:200]
        # 3) rails still claims enrolled people → sticky sync (door law / speech map)
        enrolled = {"user", "another person", "a guest", "a guest", "guest3"}
        for name in rails_names or []:
            n = (name or "").strip().lower()
            if n not in enrolled:
                continue
            if n not in self.present:
                applied.append(
                    self.note_at(
                        n,
                        location="in_room",
                        sim=0.5,
                        source="rails_sticky_sync",
                        now=now,
                    )
                )
        self._save()
        return {
            "ok": True,
            "applied": applied,
            "present": [
                {
                    "id": p.get("id"),
                    "location": p.get("location"),
                    "likely_speaker": p.get("likely_speaker"),
                }
                for p in self.present.values()
            ],
        }

    def _attach_appearance(
        self, pid: str, appearances: dict[str, Any], *, force: bool = False
    ) -> dict[str, Any] | None:
        app = appearances.get(pid) if appearances else None
        if not app or not isinstance(app, dict):
            return None
        # strip unknown_pool noise
        if pid == "unknown_pool":
            return None
        prev = self.present.get(pid) or {}
        old = prev.get("appearance")
        if force or not old or float(app.get("det") or 0) >= float(
            (old or {}).get("det") or 0
        ):
            # store compact profile + enter crop path for leave match game
            profile = {
                "summary": app.get("summary"),
                "hair_colors": app.get("hair_colors") or [],
                "top_colors": app.get("top_colors") or [],
                "bottom_colors": app.get("bottom_colors") or [],
                "height_cue": app.get("height_cue"),
                "distance_cue": app.get("distance_cue"),
                "gender_soft": app.get("gender_soft"),
                "face_height_frac": app.get("face_height_frac"),
                "det": app.get("det"),
                "sim": app.get("sim"),
                "captured_local": app.get("captured_local") or _iso(),
                "entry_crop": app.get("entry_crop")
                or (old or {}).get("entry_crop"),
            }
            return profile
        return old  # type: ignore[return-value]

    def update(
        self,
        face_hits: dict[str, Any],
        speaking: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = now if now is not None else time.time()
        self._daily_scrub(now)
        # Phantom VL unknowns must not immortal-stick (measured 18 open
        # unknown-* slots → CODE headcount 17 while SoT empty, 2026-07-27).
        # Drop any unknown-* not refreshed in the last 2 minutes.
        for pid in list(self.present.keys()):
            if not str(pid).startswith("unknown"):
                continue
            prev = self.present.get(pid) or {}
            last = float(prev.get("last_seen") or prev.get("first_seen") or 0)
            if now - last > 120.0:
                self.present.pop(pid, None)
        thr = float((face_hits or {}).get("thr") or 0.4)
        people_best = dict((face_hits or {}).get("people_best") or {})
        appearances = dict((face_hits or {}).get("appearances") or {})
        seen = set(people_best.keys())
        sp = speaking or {}
        if sp.get("id") and sp.get("source") not in (None, "silence", "sole_present"):
            pid = str(sp["id"])
            if pid not in seen:
                people_best[pid] = float(
                    (sp.get("stamp") or {}).get("voice_emb_sim") or 0.5
                )
                seen.add(pid)

        new_events: list[dict[str, Any]] = []

        # ENTER / refresh — door story (the User):
        #   first sight = enter; empty FOV while sticky = still in room (desk).
        #   exit_armed ONLY after real absence from FOV then return to door —
        #   NOT continuous face after grace (that false-left the User who never left).
        min_absence_arm = float(
            os.environ.get("RAIL_ORACLE_EXIT_ARM_ABSENCE_SEC", "12")
        )
        for pid, sim in people_best.items():
            if pid == "unknown":
                continue
            prev = self.present.get(pid)
            app = self._attach_appearance(pid, appearances, force=not prev)
            if not prev:
                grace_until = now + self.entry_grace_sec
                self.present[pid] = {
                    "id": pid,
                    "first_seen": now,
                    "last_seen": now,
                    "last_sim": float(sim),
                    "last_seen_local": _iso(now),
                    "miss_since": None,
                    "appearance": app,
                    "enter_local": _iso(now),
                    "entry_grace_until": grace_until,
                    "exit_armed": False,
                }
                ev = {
                    "kind": "enter",
                    "id": pid,
                    "sim": float(sim),
                    "ts": now,
                    "ts_local": _iso(now),
                    "appearance": (app or {}).get("summary"),
                    "appearance_full": app,
                    "entry_grace_sec": self.entry_grace_sec,
                }
                self.events.append(ev)
                new_events.append(ev)
            else:
                grace_until = float(prev.get("entry_grace_until") or 0.0)
                miss_since = prev.get("miss_since")
                absence = (
                    (now - float(miss_since)) if miss_since is not None else 0.0
                )
                # Return to door FOV after being away (seated / out of frame) → exit path
                # the User 2026-07-24: sole occupant false-left repeatedly — re-sight at
                # door while alone is usually linger/pass, not leave. Need longer
                # absence (or multi-body room) before arming exit.
                sole = len(self.present) <= 1
                arm_need = min_absence_arm * (
                    float(os.environ.get("RAIL_ORACLE_SOLE_EXIT_ARM_MULT", "4"))
                    if sole
                    else 1.0
                )
                if (
                    miss_since is not None
                    and absence >= arm_need
                    and now >= grace_until
                    and not prev.get("exit_armed")
                ):
                    prev["exit_armed"] = True
                    ev = {
                        "kind": "exit_armed",
                        "id": pid,
                        "sim": float(sim),
                        "ts": now,
                        "ts_local": _iso(now),
                        "source": "return_to_door_after_absence",
                        "absence_sec": round(absence, 1),
                        "sole_occupant": sole,
                        "arm_need_sec": round(arm_need, 1),
                    }
                    self.events.append(ev)
                    new_events.append(ev)
                elif (
                    sole
                    and miss_since is not None
                    and absence >= min_absence_arm
                    and now >= grace_until
                    and not prev.get("exit_armed")
                ):
                    # Logged once-ish via event for debug — sticky hold
                    if not prev.get("_sole_hold_noted"):
                        prev["_sole_hold_noted"] = True
                        ev = {
                            "kind": "sole_hold",
                            "id": pid,
                            "ts": now,
                            "ts_local": _iso(now),
                            "source": "door_resight_sole_no_exit_arm",
                            "absence_sec": round(absence, 1),
                            "note": "sole occupant re-sight — stay sticky (no exit_arm)",
                        }
                        self.events.append(ev)
                        new_events.append(ev)
                # Desk-leave law (Grok break-pass E, 2026-07-28): a person
                # who STAYS in view after re-sight is INSIDE, not leaving —
                # sustained re-sight cancels the arm. A true walkout flashes
                # 1-2 ticks then vanishes, keeping its arm. Env-disengageable.
                if prev.get("exit_armed"):
                    import os as _os
                    if _os.environ.get("RAIL_CODE_DESK_LEAVE_LAW", "1")                             not in ("0", "false", "no"):
                        prev["_resight_streak"] = int(
                            prev.get("_resight_streak") or 0) + 1
                        if prev["_resight_streak"] >= 3:
                            prev["exit_armed"] = False
                            prev["_resight_streak"] = 0
                            ev = {
                                "kind": "exit_arm_suppressed",
                                "id": pid, "ts": now, "ts_local": _iso(now),
                                "reason": "sustained_resight_inside",
                                "source": "desk_leave_law",
                            }
                            self.events.append(ev)
                            new_events.append(ev)
                else:
                    prev["_resight_streak"] = 0
                prev["last_seen"] = now
                prev["last_sim"] = float(sim)
                prev["last_seen_local"] = _iso(now)
                prev["miss_since"] = None  # in FOV — clear leave/absence clock
                if app:
                    prev["appearance"] = app

        # LEAVE on exit path: exit_armed + FOV miss long enough.
        # Also: hard cap — no re-sight for STICKY_MAX_SEC → drop (stops immortal ghosts).
        leave_candidates: list[dict[str, Any]] = []
        for pid in list(self.present.keys()):
            if pid in seen:
                continue
            prev = self.present[pid]
            ago = now - float(prev.get("last_seen") or now)
            app = prev.get("appearance") or {}
            grace_until = float(prev.get("entry_grace_until") or 0.0)
            in_grace = now < grace_until
            exit_armed = bool(prev.get("exit_armed"))
            # Stale sticky timeout (no face/voice for hours): clear even if never exit_armed
            if ago >= STICKY_MAX_SEC and not in_grace:
                closed = {
                    "id": pid,
                    "enter_local": prev.get("enter_local"),
                    "leave_local": _iso(now),
                    "leave_ts": now,
                    "enter_ts": prev.get("first_seen"),
                    "last_sim": prev.get("last_sim"),
                    "miss_sec": ago,
                    "appearance_summary": (app or {}).get("summary")
                    if isinstance(app, dict)
                    else None,
                    "match": "stale_timeout",
                    "match_score": 0.0,
                    "path": "sticky_max_without_resight",
                }
                self.closed.append(closed)
                self.present.pop(pid, None)
                ev = {
                    "kind": "leave",
                    "id": pid,
                    "ts": now,
                    "ts_local": _iso(now),
                    "path": "sticky_max_without_resight",
                    "last_seen_ago_sec": round(ago, 1),
                }
                self.events.append(ev)
                new_events.append(ev)
                continue

            if in_grace or not exit_armed:
                # walk-in or still in room (desk) — FOV miss is NOT leave.
                # Track absence so a later door re-sight can arm exit (leave path).
                if prev.get("miss_since") is None:
                    prev["miss_since"] = now
                leave_candidates.append(
                    {
                        "id": pid,
                        "miss_sec": 0.0,
                        "last_seen_ago_sec": round(ago, 1),
                        "last_sim": prev.get("last_sim"),
                        "will_leave_in_sec": None,
                        "exit_armed": exit_armed,
                        "in_entry_grace": in_grace,
                        "status": "in_room_sticky"
                        if not in_grace
                        else "entry_grace_walk_in",
                        "appearance": app.get("summary"),
                        "top_colors": app.get("top_colors"),
                        "hair_colors": app.get("hair_colors"),
                    }
                )
                continue

            # exit-armed and not in this strip → confirm leave
            if prev.get("miss_since") is None:
                prev["miss_since"] = now
            miss = now - float(prev["miss_since"])
            leave_candidates.append(
                {
                    "id": pid,
                    "miss_sec": round(miss, 1),
                    "last_seen_ago_sec": round(ago, 1),
                    "last_sim": prev.get("last_sim"),
                    "will_leave_in_sec": max(0.0, round(self.leave_sec - miss, 1)),
                    "exit_armed": True,
                    "in_entry_grace": False,
                    "status": "exit_confirming",
                    "appearance": app.get("summary"),
                    "top_colors": app.get("top_colors"),
                    "hair_colors": app.get("hair_colors"),
                }
            )
            if miss >= self.leave_sec:
                closed = {
                    "id": pid,
                    "enter_local": prev.get("enter_local") or prev.get("last_seen_local"),
                    "leave_local": _iso(now),
                    "leave_ts": now,
                    "enter_ts": prev.get("first_seen"),
                    "last_sim": prev.get("last_sim"),
                    "miss_sec": round(miss, 1),
                    "appearance_summary": app.get("summary"),
                    "match": "id",
                    "match_score": 1.0,
                    "path": "exit_armed_then_empty",
                }
                ev = {
                    "kind": "leave",
                    "id": pid,
                    "ts": now,
                    "ts_local": _iso(now),
                    "miss_sec": round(miss, 1),
                    "appearance": app.get("summary"),
                    "who": pid,
                    "compare": "id_match_to_enter",
                    "path": "exit_armed_then_empty",
                }
                self.events.append(ev)
                new_events.append(ev)
                self.closed.append(closed)
                self.present.pop(pid, None)

        # Child's match game: body at door (unknown face / back turned) vs enter cache
        unknown_matches: list[dict[str, Any]] = []
        pool = appearances.get("unknown_pool") if isinstance(appearances, dict) else None
        if isinstance(pool, list):
            for uapp in pool[-5:]:
                best_id, best_sc = None, 0.0
                for pid, prev in self.present.items():
                    sc = _appearance_overlap(uapp, prev.get("appearance"))
                    if sc > best_sc:
                        best_sc, best_id = sc, pid
                if best_id and best_sc >= 0.35:
                    ent = (self.present[best_id].get("appearance") or {})
                    unknown_matches.append(
                        {
                            "guess_id": best_id,
                            "score": best_sc,
                            "appearance": uapp.get("summary"),
                            "enter_summary": ent.get("summary"),
                            "enter_crop": ent.get("entry_crop"),
                            "method": "child_match_leave_to_enter_cache",
                            "note": "who left? best enter-cache match among present",
                        }
                    )

        present_list = []
        for pid, prev in sorted(self.present.items()):
            ago = now - float(prev.get("last_seen") or now)
            app = prev.get("appearance") or {}
            present_list.append(
                {
                    "id": pid,
                    "certain": float(prev.get("last_sim") or 0) >= thr + 0.05,
                    "last_sim": prev.get("last_sim"),
                    "last_seen_ago_sec": round(ago, 1),
                    "last_seen_local": prev.get("last_seen_local"),
                    "in_this_strip": pid in seen,
                    "evidence": "face" if pid in seen else "memory",
                    "exit_armed": bool(prev.get("exit_armed")),
                    "in_entry_grace": now < float(prev.get("entry_grace_until") or 0),
                    "appearance": app.get("summary"),
                    "top_colors": app.get("top_colors"),
                    "hair_colors": app.get("hair_colors"),
                    "bottom_colors": app.get("bottom_colors"),
                    "height_cue": app.get("height_cue"),
                    "gender_soft": app.get("gender_soft"),
                    "enter_local": prev.get("enter_local"),
                }
            )

        snap = {
            "present": present_list,
            "headcount": len(present_list),
            "leave_sec": self.leave_sec,
            "entry_grace_sec": self.entry_grace_sec,
            "leave_candidates": leave_candidates,
            "unknown_matches": unknown_matches,
            "new_events": new_events,
            "recent_events": list(reversed(self.events[-12:])),
            "closed_recent": list(reversed(self.closed[-6:])),
            "this_strip_seen": sorted(seen),
            "note": (
                "door story permanence: enter+grace → sticky in room; leave only after "
                f"exit_armed (re-sight) then FOV miss>={self.leave_sec}s; daily scrub 24h"
            ),
        }
        self._save()
        return snap


_ROOM_MEMORY = RoomMemory()



def _build_shared_pack(*, with_audio: bool = True) -> dict[str, Any]:
    """ONE sensor pack for ALL Oracles. No presence inventory. No control."""
    buf = _grab_buffer_pack()
    # raw jpegs for face emb (decode from strip frames if we re-fetch buffer)
    jpegs: list[bytes] = []
    try:
        need = max(BUF_MAX * BUF_STRIDE, int(BUF_SEC * 30) + BUF_STRIDE)
        need = min(need, 150)
        body = _http_json(f"{BUFFER_URL}?max={need}&full=1&stride=1", timeout=15)
        frames = body.get("frames") or []
        picked = frames[-(int(BUF_SEC * 30)) :] if len(frames) > int(BUF_SEC * 30) else frames
        picked = picked[:: max(1, BUF_STRIDE)][-BUF_MAX:]
        for fr in picked:
            b64 = fr.get("jpeg_b64") or ""
            try:
                jpegs.append(base64.b64decode(b64))
            except Exception:
                pass
    except Exception:
        pass
    # if strip file exists, keep it as vision for VLMs
    strip = Path(buf["strip"]) if buf.get("strip") else None
    latest = Path(buf["latest"]) if buf.get("latest") else _grab_frame()

    mic_meta: dict[str, Any] = {"ok": False, "note": "audio skipped"}
    if with_audio:
        speaking, mic_meta = _ear_listen(AUDIO_SEC)
    else:
        speaking = {"id": None, "text": None, "note": "no audio"}
    emb = _emb_catalog()
    # include latest still (door snap) so we don't miss a face only on the newest frame
    try:
        if latest and Path(latest).is_file():
            jpegs = list(jpegs) + [Path(latest).read_bytes()]
    except Exception:
        pass
    face = _face_hits_on_jpegs(jpegs)
    room_memory = _ROOM_MEMORY.update(face, speaking)

    pack: dict[str, Any] = {
        "ts_local": _iso(),
        "fair": True,
        "note": "identical inputs for code/gemma/cosmos — no rails inventory, no control; room_memory = permanence",
        "room_memory": room_memory,
        "camera": {
            "snapshot_url": SNAPSHOT_URL,
            "buffer_url": BUFFER_URL,
            "hz": buf.get("buffer_hz") or 30,
            "buf_sec": BUF_SEC,
            "stride": BUF_STRIDE,
            "n_frames": buf.get("n_frames") or len(jpegs),
            "frame_seqs": buf.get("frame_seqs") or [],
            "strip_path": str(strip) if strip else None,
            "latest_path": str(latest) if latest else None,
            "strip_ok": bool(buf.get("ok")),
            "buffer_error": buf.get("error"),
            # Motion ghosts for heading (analysis). Multi-frame = ego-motion ≠ multi-body.
            "motion": buf.get("motion"),
            "ghost_heading": buf.get("ghost_heading"),
            "parse_rule": "frames_are_camera_motion_not_multiple_bodies",
        },
        "mic": mic_meta,
        "speaking": speaking,
        "emb_catalog": emb,
        "face_hits": face,
        "ports": {
            "camera": _ports.camera("/snapshot") + " + /v1/buffer",
            "stt": STT_URL,
            "presence_NOT_given": PRESENCE_URL,
        },
    }
    # persist for audit — every candidate sees this same file
    WORK.mkdir(parents=True, exist_ok=True)
    pack_path = WORK / "shared_pack_latest.json"
    pack_path.write_text(json.dumps(pack, indent=2, default=str))
    pack["pack_path"] = str(pack_path)
    return pack


def _shared_evidence_text(pack: dict[str, Any]) -> str:
    """Serialize the shared pack into the prompt text all models get."""
    cam = pack.get("camera") or {}
    mic = pack.get("mic") or {}
    sp = pack.get("speaking") or {}
    emb = pack.get("emb_catalog") or {}
    face = pack.get("face_hits") or {}
    lines = [
        "=== SHARED SENSOR PACK (identical for code, gemma, cosmos) ===",
        f"pack_path: {pack.get('pack_path')}",
        f"camera: {cam.get('snapshot_url')} + {cam.get('buffer_url')} ~{cam.get('hz')}fps",
        f"  time-strip: n_frames={cam.get('n_frames')} stride={cam.get('stride')} "
        f"secs≈{cam.get('buf_sec')} seqs={cam.get('frame_seqs')}",
        f"  strip_image attached (left=older → right=newer). strip_ok={cam.get('strip_ok')}",
        f"  motion_ghost: {cam.get('ghost_heading') or (cam.get('motion') or {}).get('heading_img')} "
        f"heading={(cam.get('motion') or {}).get('heading')} "
        f"PARSE: multi-frame strip/ghost = camera or body MOTION, not extra bodies "
        f"(do not invent crowds from blur/trails)",
        f"mic: backend={mic.get('backend')} device={mic.get('device')} "
        f"rms_db={mic.get('rms_db')} peak={mic.get('peak')} silent={mic.get('silent')}",
        f"stt: text={sp.get('text')!r} person_id={sp.get('id')} "
        f"source={sp.get('source')} conf={sp.get('confidence')} reason={sp.get('reason')}",
        f"voice stamp detail: {json.dumps(sp.get('stamp') or {}, default=str)[:400]}",
        f"face_enrolled: {emb.get('face_enrolled')}",
        f"voice_enrolled: {emb.get('voice_enrolled')}",
        f"face_hits thr={face.get('thr')} people_best={face.get('people_best')}",
        f"appearances (enter metadata): "
        f"{json.dumps({k:v for k,v in (face.get('appearances') or {}).items() if k!='unknown_pool'}, default=str)[:500]}",
        f"room_memory (PERMANENCE + enter appearance; leave matches then scrubs): "
        f"{json.dumps(pack.get('room_memory') or {}, default=str)[:1100]}",
        "On leave events, who left is the paired enter id (clothes/hair/height were logged at enter).",
        "You do NOT get rails inventory (/v1/presence). You do NOT control the camera.",
        "Duplicate Oracle outputs from THIS pack + room_memory. Do not write code.",
    ]
    return "\n".join(lines)


def provider_code_from_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """CODE candidate using shared pack + room_memory permanence (fair)."""
    out: dict[str, Any] = {
        "provider": "code",
        "label": "CODE (pack + room_memory permanence)",
        "ts_local": _iso(),
        "ok": True,
        "fair_pack": True,
        "pack_path": pack.get("pack_path"),
        "is_driver": DRIVER == "code",
    }
    face = pack.get("face_hits") or {}
    mem = pack.get("room_memory") or {}
    thr = float(face.get("thr") or 0.4)
    people = []
    for p in mem.get("present") or []:
        people.append(
            {
                "id": p.get("id"),
                "certain": bool(p.get("certain")),
                "evidence": p.get("evidence") or "memory",
                "sim": p.get("last_sim"),
                "last_seen_ago_sec": p.get("last_seen_ago_sec"),
                "in_this_strip": p.get("in_this_strip"),
                "appearance": p.get("appearance"),
                "top_colors": p.get("top_colors"),
                "hair_colors": p.get("hair_colors"),
                "height_cue": p.get("height_cue"),
            }
        )
    # fallback to this strip only if memory empty
    if not people:
        for pid, sim in sorted((face.get("people_best") or {}).items(), key=lambda x: -x[1]):
            people.append(
                {
                    "id": pid,
                    "certain": sim >= thr + 0.05,
                    "evidence": "face",
                    "sim": sim,
                    "in_this_strip": True,
                }
            )
    unk = sum(1 for h in (face.get("hits") or []) if h.get("id") == "unknown")
    speaking = pack.get("speaking") or {}
    mic = pack.get("mic") or {}
    any_in_strip = any(p.get("in_this_strip") for p in people)
    trust = "fact" if any_in_strip else ("stale" if people else "fact")
    out["in_room"] = {
        "headcount": len(people),
        "people": people,
        "unidentified": 0 if people else unk,
        "count_trust": trust,
        "note": (
            f"permanence leave_sec={mem.get('leave_sec')}; "
            f"strip_seen={mem.get('this_strip_seen')}; "
            f"leave_cand={mem.get('leave_candidates')}; "
            f"closed={mem.get('closed_recent')}"
        ),
        "events": mem.get("new_events") or [],
        "closed_recent": mem.get("closed_recent") or [],
    }
    out["room_memory"] = mem
    out["speaking"] = speaking
    out["mic"] = mic
    out["face_hits"] = face
    out["in_room_and_speaking"] = _format_line_from_schema(
        {"in_room": out["in_room"], "speaking": speaking}, None
    )
    out["how"] = None
    out["how_kind"] = "n/a"
    out["how_note"] = "CODE fair mode: same pack as VLMs (no /v1/presence)."
    out["vision"] = "buffer_time_strip+face_emb"
    # SoT write-through: face-backed CODE names merge into DoorInventory
    _apply_code_write_through(out)
    return out


# back-compat name used by idle loop — re-point later
def provider_code(
    frame: Path | None,
    audio: bytes | None,
    *,
    speaking: dict[str, Any] | None = None,
    mic_meta: dict[str, Any] | None = None,
    pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if pack is not None:
        return provider_code_from_pack(pack)
    # idle: do NOT leak inventory — empty note
    return {
        "provider": "code",
        "label": "CODE (shared pack only)",
        "ts_local": _iso(),
        "ok": False,
        "note": "idle — wait for Run all (fair pack)",
        "how": None,
        "how_kind": "n/a",
    }


# Warm Gemma daemon — load weights once. Cold subprocess was the ~2 min cycle.
_GEMMA_PROC: subprocess.Popen | None = None
_GEMMA_LOCK = threading.Lock()
GEMMA_TRIGGER_ONLY = os.environ.get("RAIL_ORACLE_GEMMA_TRIGGER_ONLY", "1") not in (
    "0",
    "false",
    "no",
)
# the User 2026-07-24: Gemma's life = door motion events → 5s@8fps clip → RL log
GEMMA_EVENT_LOOP = os.environ.get("RAIL_ORACLE_GEMMA_EVENT_LOOP", "1") not in (
    "0",
    "false",
    "no",
)
CAMERA_TRIGGERS = os.environ.get(
    "RAIL_ORACLE_TRIGGERS", _ports.camera("/v1/triggers")
)
CAMERA_FRAME = os.environ.get(
    "RAIL_ORACLE_FRAME", _ports.camera("/v1/frame")
)
CAMERA_TRIGGER_POST = os.environ.get(
    "RAIL_ORACLE_TRIGGER_POST", _ports.camera("/v1/trigger")
)


def _ensure_gemma_daemon() -> None:
    """Persistent worker: stdin JSON lines → stdout JSON (model stays hot)."""
    global _GEMMA_PROC
    with _GEMMA_LOCK:
        if _GEMMA_PROC is not None and _GEMMA_PROC.poll() is None:
            return
        script = WORK / "_gemma_daemon.py"
        script.write_text(
            r'''
import json, os, sys
from pathlib import Path
import torch
from PIL import Image
from transformers import AutoProcessor, AutoModelForImageTextToText

ckpt = sys.argv[1]
# Door count/ID does not need 720p. Long-edge cap cuts vision tokens hard.
MAX_SIDE = int(os.environ.get("RAIL_ORACLE_GEMMA_MAX_SIDE", "512"))
processor = AutoProcessor.from_pretrained(ckpt, trust_remote_code=True)
model = AutoModelForImageTextToText.from_pretrained(
    ckpt, torch_dtype=torch.bfloat16, device_map="auto", trust_remote_code=True
)
device = next(model.parameters()).device
sys.stdout.write(json.dumps({"ok": True, "ready": True, "device": str(device), "max_side": MAX_SIDE}) + "\n")
sys.stdout.flush()

def _load(path):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    m = max(w, h)
    if m > MAX_SIDE:
        s = MAX_SIDE / float(m)
        im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.Resampling.BILINEAR)
    return im

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
        if req.get("cmd") == "ping":
            sys.stdout.write(json.dumps({"ok": True, "pong": True}) + "\n")
            sys.stdout.flush()
            continue
        images = req.get("images") or ([req["image"]] if req.get("image") else [])
        questions = req.get("questions") or []
        max_new = int(req.get("max_new_tokens") or 256)
        max_imgs = int(req.get("max_images") or 2)
        pils = [_load(p) for p in images if Path(p).is_file()][:max_imgs]
        if not pils:
            sys.stdout.write(json.dumps({"ok": False, "error": "no images"}) + "\n")
            sys.stdout.flush()
            continue
        out = {}
        for key, q in questions:
            content = [{"type": "image", "image": im} for im in pils]
            content.append({"type": "text", "text": q})
            messages = [{"role": "user", "content": content}]
            try:
                inputs = processor.apply_chat_template(
                    messages, add_generation_prompt=True, tokenize=True,
                    return_dict=True, return_tensors="pt",
                )
            except Exception:
                inputs = processor(text=q, images=pils[0], return_tensors="pt")
            inputs = {k: v.to(device) if hasattr(v, "to") else v for k, v in inputs.items()}
            with torch.inference_mode():
                gen = model.generate(**inputs, max_new_tokens=max_new, do_sample=False)
            n = inputs["input_ids"].shape[-1] if "input_ids" in inputs else 0
            try:
                text = processor.decode(gen[0][n:], skip_special_tokens=True)
            except Exception:
                text = processor.decode(gen[0], skip_special_tokens=True)
            out[key] = text
        sys.stdout.write(json.dumps({"ok": True, "answers": out, "n_images": len(pils), "max_side": MAX_SIDE}) + "\n")
        sys.stdout.flush()
    except Exception as e:
        sys.stdout.write(json.dumps({"ok": False, "error": str(e)[:500]}) + "\n")
        sys.stdout.flush()
'''
        )
        _GEMMA_PROC = subprocess.Popen(
            [GEMMA_PYTHON, str(script), str(GEMMA_CKPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(WORK),
            bufsize=1,
        )
        # wait for ready line (model load)
        assert _GEMMA_PROC.stdout is not None
        ready = _GEMMA_PROC.stdout.readline()
        try:
            meta = json.loads(ready)
            if not meta.get("ready"):
                raise RuntimeError(f"gemma daemon not ready: {ready[:200]}")
        except Exception as e:
            err = (_GEMMA_PROC.stderr.read() if _GEMMA_PROC.stderr else "")[:800]
            raise RuntimeError(f"gemma daemon start failed: {e} {err}") from e


def _ask_gemma(
    frame: Path | list[Path],
    questions: list[tuple[str, str]],
) -> dict[str, str]:
    """Warm-daemon Gemma. frame = one path or list of timeline ref frames."""
    global _GEMMA_PROC
    _ensure_gemma_daemon()
    assert _GEMMA_PROC is not None and _GEMMA_PROC.stdin and _GEMMA_PROC.stdout
    images = frame if isinstance(frame, list) else [frame]
    images = [str(Path(p)) for p in images]
    req = {
        "images": images[: int(os.environ.get("RAIL_ORACLE_GEMMA_MAX_IMAGES", "2"))],
        "questions": questions,
        # Short JSON oracle answers — 480 was padding latency for no gain.
        "max_new_tokens": int(os.environ.get("RAIL_ORACLE_GEMMA_MAX_NEW", "256")),
        "max_images": int(os.environ.get("RAIL_ORACLE_GEMMA_MAX_IMAGES", "2")),
    }
    with _GEMMA_LOCK:
        if _GEMMA_PROC is None or _GEMMA_PROC.poll() is not None:
            _GEMMA_PROC = None
            _ensure_gemma_daemon()
        assert _GEMMA_PROC is not None and _GEMMA_PROC.stdin and _GEMMA_PROC.stdout
        _GEMMA_PROC.stdin.write(json.dumps(req) + "\n")
        _GEMMA_PROC.stdin.flush()
        line = _GEMMA_PROC.stdout.readline()
    if not line:
        raise RuntimeError("gemma daemon EOF")
    body = json.loads(line)
    if not body.get("ok"):
        raise RuntimeError(body.get("error") or "gemma fail")
    return body.get("answers") or {}


def _fetch_triggers(since_ts: float = 0.0) -> list[dict[str, Any]]:
    try:
        url = f"{CAMERA_TRIGGERS}?since_ts={since_ts}&max=16"
        body = _http_json(url, timeout=3)
        return list(body.get("triggers") or [])
    except Exception:
        return []


def _fetch_frames_by_seq(seqs: list[int]) -> list[Path]:
    """Materialize trigger frames from the running 30fps ring to disk."""
    WORK.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for seq in seqs:
        try:
            fr = _http_json(f"{CAMERA_FRAME}?seq={int(seq)}", timeout=3)
        except Exception:
            continue
        b64 = fr.get("jpeg_b64") if isinstance(fr, dict) else None
        if not b64:
            continue
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue
        p = WORK / f"trigger_seq{seq}.jpg"
        p.write_bytes(raw)
        paths.append(p)
    return paths


# Face trigger debounce: continuous door-face while seated/lingering was
# thrashing Gemma (chaotic entry → every pack tick stamped the user → re-ran).
# ALWAYS cool down per person_id — including first-sight bursts. "NEW" is
# only a label; it must not bypass the cooldown (that re-opened thrash on
# re-approach and multi-person door chaos).
_FACE_TRIGGER_LAST: dict[str, float] = {}
_FACE_TRIGGER_COOLDOWN = float(
    os.environ.get("RAIL_ORACLE_FACE_TRIGGER_COOLDOWN_SEC", "12")
)
# Hard floor between Gemma trigger invocations (motion storms after busy).
_GEMMA_MIN_INTERVAL = float(
    os.environ.get("RAIL_ORACLE_GEMMA_MIN_INTERVAL_SEC", "8")
)
_LAST_GEMMA_TRIGGER_AT = 0.0


def _post_face_triggers(face_hits: dict[str, Any] | None) -> None:
    """Stamp face triggers at most once per person per cooldown window.

    Chaotic entry fix: do not spam on every pack tick while someone lingers
    at the door or re-approaches mid-burst. Label NEW vs re-sight for logs.
    """
    if not face_hits:
        return
    people = (face_hits or {}).get("people_best") or {}
    if not people:
        return
    now = time.time()
    try:
        h = _http_json(
            SNAPSHOT_URL.replace("/snapshot", "/health"), timeout=1.5
        )
        seq = h.get("seq")
        present_ids = set((_ROOM_MEMORY.present or {}).keys())
        for pid, sim in list(people.items())[:3]:
            pid = str(pid).lower()
            last = float(_FACE_TRIGGER_LAST.get(pid) or 0)
            if (now - last) < _FACE_TRIGGER_COOLDOWN:
                continue
            is_new = pid not in present_ids
            _FACE_TRIGGER_LAST[pid] = now
            _http_post_raw(
                CAMERA_TRIGGER_POST,
                json.dumps(
                    {
                        "kind": "face",
                        "seq": seq,
                        "note": f"faceid {pid} sim={sim}"
                        + (" NEW" if is_new else " re-sight"),
                        "meta": {
                            "person_id": pid,
                            "sim": sim,
                            "new_enter": is_new,
                        },
                    }
                ).encode(),
                {"Content-Type": "application/json"},
                timeout=2,
            )
    except Exception:
        pass


def _ask_cosmos(frame: Path, questions: list[tuple[str, str]]) -> dict[str, str]:
    """Cosmos3-Edge reasoner via NVIDIA cosmos-framework offline CLI.

    Sample shape: model_mode=reasoner + vision_path + prompt (+ sampling defaults).
    Clears prior out/ so stale reasoner_text is never reused.
    """
    out: dict[str, str] = {}
    py = COSMOS_PYTHON
    if not py or not Path(py).is_file() or not _paths.cosmos_root():
        raise RuntimeError("the Cosmos describer is not installed here (COSMOS_PYTHON, COSMOS_FRAMEWORK_ROOT)")
    for key, q in questions:
        work = WORK / "cosmos" / key
        work.mkdir(parents=True, exist_ok=True)
        sample = build_cosmos_reasoner_sample(
            frame, prompt=q, name=f"oracle_{key}"
        )
        sp = work / "sample.json"
        sp.write_text(json.dumps(sample, indent=2))
        out_dir = work / "out"
        # drop stale outputs so rglob cannot pick an old success
        if out_dir.is_dir():
            for old in out_dir.rglob("*"):
                if old.is_file():
                    try:
                        old.unlink()
                    except OSError:
                        pass
        out_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONPATH"] = _paths.cosmos_root() + ":" + env.get("PYTHONPATH", "")
        env.setdefault("HF_HOME", str(_paths.HF))
        env.setdefault("HF_HUB_CACHE", str(_paths.HF / "hub"))
        proc = subprocess.run(
            [
                py,
                "-m",
                "cosmos_framework.scripts.inference",
                "--parallelism-preset=latency",
                "-i",
                str(sp),
                "-o",
                str(out_dir),
                "--checkpoint-path",
                COSMOS_CKPT_NAME,
                "--seed",
                "0",
                "--no-guardrails",
            ],
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
            cwd=_paths.cosmos_root() or None,
        )
        text = ""
        for p in sorted(out_dir.rglob("reasoner_text.txt"), key=lambda x: x.stat().st_mtime):
            text = p.read_text()
        if not text:
            text = (proc.stderr or proc.stdout or f"cosmos empty rc={proc.returncode}")[
                -2000:
            ]
        # Treat stack traces as failure so the board does not green-lie
        if "Traceback" in text or "download_checkpoint" in text or proc.returncode != 0:
            if not text or "Traceback" not in text:
                text = (proc.stderr or proc.stdout or text)[-2000:]
            raise RuntimeError(text[-1500:])
        out[key] = text
    return out


def _merge_sticky_into_parsed(
    parsed: dict[str, Any] | None,
    mem_view: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """If VLM zeros the room but *fresh* sticky memory has people, re-merge.

    Only merge people last seen within STICKY_MERGE_MAX_SEC. Stale ghosts
    (another person for 10h with no face) must NOT reappear as "memory, no visual".
    """
    if not parsed or not isinstance(parsed, dict):
        parsed = {}
    # Build sticky list from RoomMemory.present or a pack-style view
    sticky: list[dict[str, Any]] = []
    now = time.time()
    if mem_view and isinstance(mem_view.get("present"), list):
        for p in mem_view["present"]:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            ago = float(p.get("last_seen_ago_sec") or 0)
            if ago > STICKY_MERGE_MAX_SEC:
                continue
            sticky.append(p)
    else:
        for pid, prev in sorted((_ROOM_MEMORY.present or {}).items()):
            if not pid:
                continue
            app = prev.get("appearance") or {}
            if not isinstance(app, dict):
                app = {}
            ago = now - float(prev.get("last_seen") or now)
            if ago > STICKY_MERGE_MAX_SEC:
                continue
            sticky.append(
                {
                    "id": pid,
                    "certain": float(prev.get("last_sim") or 0) >= 0.45,
                    "evidence": "memory",
                    "location": prev.get("location") or "in_room",
                    "appearance": app.get("summary"),
                    "hair_colors": app.get("hair_colors"),
                    "last_seen_ago_sec": round(ago, 1),
                }
            )
    if not sticky:
        return parsed

    ir = parsed.get("in_room") if isinstance(parsed.get("in_room"), dict) else {}
    people = list(ir.get("people") or [])
    model_hc = int(ir.get("headcount") or len(people) or 0)

    def _is_unknown(pid: str) -> bool:
        p = str(pid or "").lower()
        return (not p) or p in ("unknown", "none") or p.startswith("unknown")

    model_named = [
        p
        for p in people
        if isinstance(p, dict) and p.get("id") and not _is_unknown(str(p.get("id")))
    ]
    have = {str(p.get("id") or "").lower() for p in model_named}

    # If the model only saw unknowns (or emptied FOV), prefer sticky names —
    # don't invent unknown+another person dual for one body.
    if sticky and not model_named:
        people = [
            {
                "id": p["id"],
                "certain": p.get("certain", True),
                "evidence": "memory" if model_hc == 0 else "memory|vision_body",
                "location": p.get("location") or "in_room",
                "appearance": p.get("appearance"),
            }
            for p in sticky
        ]
        ir = dict(ir)
        ir["people"] = people
        ir["headcount"] = len(people)
        ir["count_trust"] = "stale"
        note = (ir.get("note") or "").strip()
        add = "sticky-merge: model unnamed/empty FOV → room_memory roster"
        ir["note"] = f"{note} | {add}" if note else add
        out = dict(parsed)
        out["in_room"] = ir
        out["_sticky_merged"] = True
        return out

    # Model named someone at the door: ADD sticky ids not in FOV (desk).
    # Turn-taking fix — do not drop the user when only another person's face triggered.
    missing = [
        p
        for p in sticky
        if str(p.get("id") or "").lower() not in have
        and not _is_unknown(str(p.get("id") or ""))
    ]
    if not missing:
        if people and int(ir.get("headcount") or 0) != len(people):
            ir = dict(ir)
            ir["headcount"] = len([p for p in people if isinstance(p, dict)])
            out = dict(parsed)
            out["in_room"] = ir
            return out
        return parsed

    # Drop pure-unknown model rows once we have named sticky coverage
    people = list(model_named)
    for p in missing:
        people.append(
            {
                "id": p["id"],
                "certain": p.get("certain", True),
                "evidence": "memory",
                "location": p.get("location") or "in_room",
                "appearance": p.get("appearance"),
            }
        )
    ir = dict(ir)
    ir["people"] = people
    ir["headcount"] = len(people)
    ir["count_trust"] = "stale"
    note = (ir.get("note") or "").strip()
    add = (
        "sticky-merge: kept room_memory present not in this FOV "
        f"({', '.join(str(p.get('id')) for p in missing)})"
    )
    ir["note"] = f"{note} | {add}" if note else add
    out = dict(parsed)
    out["in_room"] = ir
    out["_sticky_merged"] = True
    return out


def provider_gemma(
    frame: Path | list[Path] | None,
    audio: bytes | None,
    *,
    ear_note: str | None = None,
    skip_how: bool = True,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "provider": "gemma",
        "label": "GEMMA-4-E4B-it",
        "ts_local": _iso(),
        "ok": False,
        "is_driver": DRIVER == "gemma",
    }
    if not GPU_ON:
        out["error"] = "RAIL_ORACLE_GPU=0"
        return out
    frames: list[Path]
    if frame is None:
        out["error"] = "no frame"
        return out
    if isinstance(frame, list):
        frames = [Path(p) for p in frame if p and Path(p).is_file()]
    else:
        frames = [Path(frame)] if Path(frame).is_file() else []
    if not frames:
        out["error"] = "no frame"
        return out
    if not GEMMA_CKPT.is_dir():
        out["error"] = f"missing {GEMMA_CKPT}"
        return out
    try:
        t0 = time.time()
        now_q = build_ask_now(ear_note=ear_note)
        qs = [("now", now_q)]
        if not skip_how:
            qs.append(("how", ASK_HOW))
        ans = _ask_gemma(frames if len(frames) > 1 else frames[0], qs)
        raw = ans.get("now") or ""
        parsed = _parse_oracle_json(raw)
        # Door-law assist: VLMs often zero headcount on empty door strip; re-merge sticky memory
        parsed = _merge_sticky_into_parsed(parsed, None)
        out["ok"] = True
        out["latency_sec"] = round(time.time() - t0, 1)
        out["raw_now"] = raw
        out["parsed"] = parsed
        out["in_room"] = (parsed or {}).get("in_room")
        out["speaking"] = (parsed or {}).get("speaking")
        out["in_room_and_speaking"] = _format_line_from_schema(parsed, raw)
        out["how"] = ans.get("how")
        out["how_kind"] = "model" if ans.get("how") else "skipped"
        out["audio_attached"] = bool(audio)
        out["ear_note_attached"] = bool(ear_note)
        out["vision"] = "trigger_frames" if isinstance(frame, list) else "buffer_time_strip"
        out["sticky_merge"] = bool((parsed or {}).get("_sticky_merged"))
        out["n_images"] = len(frame) if isinstance(frame, list) else 1
    except Exception as e:
        out["error"] = str(e)[:800]
    return out


def provider_gemma_trigger(
    frames: list[Path],
    *,
    triggers: list[dict[str, Any]],
    ear_note: str | None = None,
) -> dict[str, Any]:
    """Gemma only on timeline trigger frames (not the 2-min full strip cycle)."""
    out: dict[str, Any] = {
        "provider": "gemma",
        "label": "GEMMA-4-E4B-it",
        "ts_local": _iso(),
        "ok": False,
        "is_driver": DRIVER == "gemma",
        "mode": "trigger",
        "triggers": [
            {k: t.get(k) for k in ("seq", "kind", "capture_ts", "note")}
            for t in triggers
        ],
    }
    if not frames:
        out["error"] = "no trigger frames in ring (aged out?)"
        return out
    note = (ear_note or "") + (
        "\n\nTIMELINE: these image(s) are TRIGGER frames from the always-on 30fps "
        "door buffer (seq pointers). Judge enter/leave only from what you see; "
        "multi-frame = motion over time, not inventing extra people."
    )
    # provider_gemma expects Path|list via _ask_gemma
    try:
        t0 = time.time()
        now_q = build_ask_now(ear_note=note)
        ans = _ask_gemma(frames, [("now", now_q)])
        raw = ans.get("now") or ""
        parsed = _parse_oracle_json(raw)
        # Always sticky-union: empty FOV (desk) AND partial FOV (only door face
        # in frame). Prevents the user↔another person turn-taking when triggers alternate.
        parsed = _merge_sticky_into_parsed(parsed, None)
        out["ok"] = True
        out["latency_sec"] = round(time.time() - t0, 1)
        out["raw_now"] = raw
        out["parsed"] = parsed
        out["in_room"] = (parsed or {}).get("in_room")
        out["speaking"] = (parsed or {}).get("speaking")
        out["in_room_and_speaking"] = _format_line_from_schema(parsed, raw)
        out["vision"] = "trigger_timeline_frames"
        out["n_images"] = len(frames)
        out["frame_paths"] = [str(p) for p in frames]
        out["sticky_merge"] = bool((parsed or {}).get("_sticky_merged"))
    except Exception as e:
        out["error"] = str(e)[:800]
    return out


def provider_cosmos(
    frame: Path | None,
    audio: bytes | None,
    *,
    ear_note: str | None = None,
    skip_how: bool = True,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "provider": "cosmos",
        "label": "COSMOS3-Edge",
        "ts_local": _iso(),
        "ok": False,
        "is_driver": DRIVER == "cosmos",
    }
    if not GPU_ON:
        out["error"] = "RAIL_ORACLE_GPU=0"
        return out
    if frame is None or not frame.is_file():
        out["error"] = "no frame"
        return out
    try:
        t0 = time.time()
        # NVIDIA reasoner: vision + physical prompt only (no SENSOR_JSON dump).
        # ear_note intentionally unused on live path — it reintroduced inventory leaks.
        qs = [("now", ASK_NOW_COSMOS)]
        ans = _ask_cosmos(frame, qs)
        raw = ans.get("now") or ""
        fields = cosmos_result_from_raw(raw)
        out["latency_sec"] = round(time.time() - t0, 1)
        out["raw_now"] = raw[:4000]
        out["template"] = "nvidia_cosmos3_edge_reasoner"
        out["sample_path"] = str((WORK / "cosmos" / "now" / "sample.json"))
        out.update(fields)
        out["how"] = None
        out["how_kind"] = "skipped"
        out["audio_attached"] = bool(audio)
        out["ear_note_attached"] = False
        out["vision"] = "buffer_time_strip_or_frame"
        out["vision_path"] = str(frame.resolve())
    except Exception as e:
        out["error"] = str(e)[:800]
        out["ok"] = False
    return out


def _run_all_providers(frame: Path | None, audio: bytes | None) -> None:
    """Run active providers independently (no cross-reads). Shared sensors only."""
    # used by OracleBench methods via self._set_provider — set below on instance
    pass


class OracleBench:
    """Shadow or auto: three independent Oracles. Operator drives shadow mode."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: list[dict[str, Any]] = []
        self._state: dict[str, Any] = {
            "code": {"provider": "code", "ok": False, "note": "starting live feed…"},
            "gemma": {"provider": "gemma", "ok": False, "note": "starting live feed…"},
            "cosmos": {
                "provider": "cosmos",
                "ok": False,
                "note": "starting live feed…",
            },
            "meta": {
                "mode": MODE,
                "interval_sec": INTERVAL,
                "gpu": GPU_ON,
                "enabled": ENABLE,
                "active": sorted(ACTIVE),
                "side_by_side": _ports.url("presence", "/oracle"),
                "driver": DRIVER,
                "ask_how_preview": ASK_HOW[:200] + "…",
                "ask_now_preview": build_ask_now()[:200] + "…",
                "history_path": str(HISTORY_PATH),
                "scoreboard": {"gemma_beats_code": 0, "cosmos_beats_code": 0, "runs": 0},
            },
            "history": [],
        }
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = False
        self.cycles = 0
        self._preview_thread: threading.Thread | None = None
        self._live_strip_meta: dict[str, Any] = {}
        self._last_refind_at: float = 0.0
        self._cycle_n: int = 0
        # epoch until which board must show look-around grid (not door ring line)
        self._look_around_hold_until: float = 0.0
        self._look_around_vision_path: str | None = None
        self._load_history()

    def _load_history(self) -> None:
        if not HISTORY_PATH.is_file():
            return
        try:
            rows = []
            for line in HISTORY_PATH.read_text().splitlines()[-HISTORY_MAX:]:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
            self._history = rows
            self._recompute_scoreboard()
            self._state["history"] = list(reversed(self._history[-20:]))
        except Exception:
            pass

    def _recompute_scoreboard(self) -> None:
        sb = {"gemma_beats_code": 0, "cosmos_beats_code": 0, "runs": 0, "ties_or_code": 0, "driver": DRIVER}
        for row in self._history:
            vote = row.get("operator_vote")  # gemma | cosmos | code | tie | skip
            if not vote or vote == "skip":
                continue
            sb["runs"] += 1
            if vote == "gemma":
                sb["gemma_beats_code"] += 1
            elif vote == "cosmos":
                sb["cosmos_beats_code"] += 1
            else:
                sb["ties_or_code"] += 1
        self._state["meta"]["scoreboard"] = sb

    def start(self) -> None:
        if not ENABLE:
            return
        # Always refresh CODE lightly in shadow so driver column stays live
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="oracle-bench", daemon=True
        )
        self._thread.start()
        # continuous strip preview (facefind/patrol) independent of model cycle
        if self._preview_thread is None or not self._preview_thread.is_alive():
            self._preview_thread = threading.Thread(
                target=self._preview_loop, name="oracle-strip-live", daemon=True
            )
            self._preview_thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            out = json.loads(json.dumps(self._state, default=str))
        # Cosmos column: when the bench cosmos provider is off, mirror the
        # Claude-channel Nano Oracle report (kit publishes the file; fresh
        # within 5 min = live). Board display only — no bench logic reads it.
        if "cosmos" not in ACTIVE:
            rep = (_paths.DATA
                   / "cosmos-operator/room_speaker_report.json")
            try:
                age = time.time() - rep.stat().st_mtime
                # Door-locked runner publishes on motion or 600 s heartbeat —
                # quiet is healthy; only >900 s means the runner died.
                if age < 900:
                    r = json.loads(rep.read_text())
                    out["cosmos"] = {
                        "provider": "cosmos",
                        "label": "Cosmos3-Nano (Claude channel :8195)",
                        "ok": True,
                        "ts_local": r.get("ts_local"),
                        "age_sec": round(age, 1),
                        "raw_now": json.dumps(
                            {k: r.get(k) for k in
                             ("count", "count_confident", "in_room", "speaking", "note")}
                        ),
                    }
                else:
                    out["cosmos"] = {
                        "provider": "cosmos", "ok": False,
                        "note": f"nano report stale ({int(age)}s) — runner down?",
                    }
            except FileNotFoundError:
                out["cosmos"] = {
                    "provider": "cosmos", "ok": False,
                    "note": "nano channel off (no report file)",
                }
            except Exception as e:
                out["cosmos"] = {
                    "provider": "cosmos", "ok": False,
                    "note": f"nano report unreadable: {str(e)[:80]}",
                }
        return out

    def _set_provider(self, key: str, val: dict) -> None:
        with self._lock:
            self._state[key] = val
            self._state["meta"]["updated_local"] = _iso()
            self._state["meta"]["cycles"] = self.cycles
            self._state["meta"]["busy"] = self._busy
            self._state["meta"]["mode"] = MODE

    def _look_around_hold_active(self) -> bool:
        return time.time() < float(self._look_around_hold_until or 0)

    def board_strip_path(self) -> str | None:
        """What /v1/oracle/strip.jpg should show right now."""
        with self._lock:
            hold = self._look_around_hold_active()
            la = self._look_around_vision_path
        if hold:
            for p in (
                Path(la) if la else None,
                WORK / "look_around_vision.jpg",
                WORK / "look_around_pack_vision.jpg",
            ):
                if p is not None and p.is_file() and p.stat().st_size > 500:
                    return str(p)
        for p in (WORK / "strip_live.jpg", WORK / "pack_vision.jpg"):
            if p.is_file() and p.stat().st_size > 500:
                return str(p)
        return None

    def _preview_loop(self) -> None:
        """~1.5s strip refresh so UI shows facefind while models grind.

        While look-around hold is active, do NOT overwrite strip_live with the
        door time-strip (that snapped the board from grid back to a thin line).
        """
        time.sleep(0.8)
        while not self._stop.is_set():
            try:
                if self._look_around_hold_active():
                    with self._lock:
                        la = self._look_around_vision_path or str(
                            WORK / "look_around_vision.jpg"
                        )
                        remain = max(0.0, self._look_around_hold_until - time.time())
                        sens = self._state["meta"].setdefault("sensors", {})
                        sens["live_strip"] = {
                            "path": la,
                            "mode": "look_around_hold",
                            "hold_remain_sec": round(remain, 1),
                            "n_frames": None,
                            "updated_local": _iso(),
                            "ok": Path(la).is_file(),
                        }
                        self._state["meta"]["live_strip_path"] = la
                        self._state["meta"]["live_strip_mode"] = "look_around_hold"
                        self._state["meta"]["live_strip_updated"] = _iso()
                        self._state["meta"]["look_around_hold_remain_sec"] = round(
                            remain, 1
                        )
                else:
                    meta = refresh_live_strip()
                    with self._lock:
                        self._live_strip_meta = meta
                        sens = self._state["meta"].setdefault("sensors", {})
                        sens["live_strip"] = {
                            "path": meta.get("strip_live") or meta.get("strip"),
                            "mode": "door_ring",
                            "n_frames": meta.get("n_frames"),
                            "seqs": meta.get("frame_seqs"),
                            "pan_actual": meta.get("pan_actual"),
                            "camera_seq": meta.get("camera_seq"),
                            "sweeping": meta.get("camera_sweeping"),
                            "updated_local": _iso(),
                            "ok": meta.get("ok"),
                        }
                        self._state["meta"]["live_strip_path"] = meta.get(
                            "strip_live"
                        ) or meta.get("strip")
                        self._state["meta"]["live_strip_mode"] = "door_ring"
                        self._state["meta"]["live_strip_updated"] = _iso()
                        self._state["meta"]["look_around_hold_remain_sec"] = 0
            except Exception as e:
                with self._lock:
                    self._state["meta"]["live_strip_error"] = str(e)[:160]
            self._stop.wait(1.5)

    def _loop(self) -> None:
        """Live continuous: CODE on pack; Gemma on **door motion events only**.

        the User 2026-07-24 event-loop life:
          firmware/software motion at door → coalesce → 5s @ 8fps clip (~40 frames)
          → count delta JSON → peek on hard calls → lesson log (RL-style).
        Face triggers no longer thrash Gemma; faceid IDs ride in the pack line.
        """
        time.sleep(1.5)
        # ignore backlog of pre-boot triggers (chaotic flood on restart)
        last_trigger_ts = time.time()
        pack_every = float(os.environ.get("RAIL_ORACLE_PACK_SEC", "2.0"))
        last_pack_at = 0.0
        # warm gemma once in background thread so first event is fast
        if GPU_ON and "gemma" in ACTIVE:
            def _warm():
                try:
                    _ensure_gemma_daemon()
                    with self._lock:
                        self._state["meta"]["gemma_warm"] = True
                except Exception as e:
                    with self._lock:
                        self._state["meta"]["gemma_warm"] = False
                        self._state["meta"]["gemma_warm_error"] = str(e)[:200]

            threading.Thread(target=_warm, name="gemma-warm", daemon=True).start()

        while not self._stop.is_set():
            if MODE in ("shadow", "operator", "cosmos_operator", "manual"):
                if not self._busy:
                    with self._lock:
                        if MODE in ("operator", "cosmos_operator"):
                            self._state["meta"]["status"] = (
                                "Cosmos operator mode — no scripted PTZ; use /chat tools"
                            )
                            self._state["meta"]["operator_mode"] = True
                        else:
                            self._state["meta"]["status"] = (
                                "shadow paused — set RAIL_ORACLE_MODE=auto for live feed"
                            )
                        self._state["meta"]["mode"] = MODE
                        self._state["meta"]["updated_local"] = _iso()
                self._stop.wait(5.0)
                continue

            try:
                now = time.time()
                # 1) Cheap always-on pack + CODE (no Gemma load)
                if now - last_pack_at >= pack_every:
                    last_pack_at = now
                    self._run_pack_code_only()
                # 2) Gemma event-loop: door motion only (default)
                if GPU_ON and "gemma" in ACTIVE and not self._busy:
                    if GEMMA_EVENT_LOOP:
                        trigs = _fetch_triggers(since_ts=last_trigger_ts)
                        if trigs:
                            last_trigger_ts = max(
                                float(t.get("ts") or 0) for t in trigs
                            )
                            self._ingest_motion_events(trigs)
                        self._run_gemma_motion_event_if_ready()
                    elif GEMMA_TRIGGER_ONLY:
                        trigs = _fetch_triggers(since_ts=last_trigger_ts)
                        if trigs:
                            last_trigger_ts = max(
                                float(t.get("ts") or 0) for t in trigs
                            )
                            self._run_gemma_triggers(trigs)
                    else:
                        if not self._busy:
                            self._run_cycle(with_audio=False)
            except Exception as e:
                with self._lock:
                    self._state["meta"]["status"] = f"live error: {e}"[:200]
                    self._state["meta"]["updated_local"] = _iso()
            self._stop.wait(0.35)

    def _run_pack_code_only(self) -> None:
        """Rebuild pack + CODE without blocking on Gemma (always-on timeline)."""
        try:
            pack = _build_shared_pack(with_audio=False)
            _post_face_triggers(pack.get("face_hits"))
            cam = pack.get("camera") or {}
            sensors = {
                "fair": True,
                "pack_path": pack.get("pack_path"),
                "same_for": sorted(ACTIVE),
                "camera": cam,
                "face_hits": pack.get("face_hits"),
                "room_memory": pack.get("room_memory"),
                "speaking": pack.get("speaking"),
                "driver": DRIVER,
                "mode": "pack_code_always",
                "triggers_url": CAMERA_TRIGGERS,
                "mjpeg": _ports.camera("/v1/mjpeg"),
            }
            with self._lock:
                self._state["meta"]["sensors"] = sensors
                self._state["meta"]["pack_path"] = pack.get("pack_path")
                self._state["meta"]["status"] = (
                    "pack+code live (Gemma on door-motion events)"
                    if GEMMA_EVENT_LOOP
                    else "pack+code live (Gemma on triggers)"
                )
                self._state["meta"]["updated_local"] = _iso()
            if "code" in ACTIVE:
                self._set_provider("code", provider_code_from_pack(pack))
            self.cycles += 1
        except Exception as e:
            with self._lock:
                self._state["meta"]["status"] = f"pack error: {e}"[:200]

    def _ingest_motion_events(self, triggers: list[dict[str, Any]]) -> None:
        """Queue only door-motion stamps for the Gemma event loop."""
        from .gemma_event_loop import get_queue

        q = get_queue()
        for t in triggers:
            kind = str(t.get("kind") or "")
            if kind in ("motion", "firmware_motion", "door_motion"):
                q.offer_motion(t)

    def _run_gemma_motion_event_if_ready(self) -> None:
        """Process one coalesced door-motion event when the ring window is ready."""
        if self._busy:
            return
        from . import gemma_event_loop as gel

        q = gel.get_queue()
        trig = q.pop_ready()
        if not trig:
            return
        self._busy = True
        try:
            with self._lock:
                self._state["meta"]["busy"] = True
                self._state["meta"]["status"] = (
                    "Gemma motion-event: LIVE 5s@8fps capture…"
                )
            pack = None
            try:
                pp = WORK / "shared_pack_latest.json"
                if pp.is_file():
                    pack = json.loads(pp.read_text())
            except Exception:
                pack = None

            def _ask(paths: list[Path], question: str) -> dict[str, str]:
                # temporarily allow many frames for this event
                old = os.environ.get("RAIL_ORACLE_GEMMA_MAX_IMAGES")
                os.environ["RAIL_ORACLE_GEMMA_MAX_IMAGES"] = str(
                    max(len(paths), int(os.environ.get("RAIL_GEMMA_EVENT_VL_FRAMES", "16")))
                )
                try:
                    return _ask_gemma(paths, [("now", question)])
                finally:
                    if old is None:
                        os.environ.pop("RAIL_ORACLE_GEMMA_MAX_IMAGES", None)
                    else:
                        os.environ["RAIL_ORACLE_GEMMA_MAX_IMAGES"] = old

            out = gel.run_motion_event(
                trigger=trig,
                ask_gemma=_ask,
                pack=pack,
                room_memory=_ROOM_MEMORY,
            )
            q.done(out)
            # SoT write-through: VL ±1 → DoorInventory (DNA)
            _apply_gemma_event_to_sot(out)
            self._set_provider("gemma", out)
            with self._lock:
                self._state["meta"]["status"] = (
                    f"Gemma event {out.get('event_id')} "
                    f"+{((out.get('verdict') or {}).get('entered'))}/"
                    f"-{((out.get('verdict') or {}).get('left'))} "
                    f"lat={out.get('latency_sec')}s ok={out.get('ok')} "
                    f"peek={bool(out.get('peek'))}"
                    f" sot={not bool(out.get('_sot_apply_error'))}"
                )
                self._state["meta"]["last_gemma_event"] = {
                    "event_id": out.get("event_id"),
                    "latency_sec": out.get("latency_sec"),
                    "n_frames": out.get("n_frames"),
                    "n_vl": out.get("n_vl"),
                    "lesson": (out.get("lesson") or {}).get("lesson"),
                    "sot_error": out.get("_sot_apply_error"),
                }
                self._state["meta"]["updated_local"] = _iso()
            # refresh CODE from updated room_memory
            if "code" in ACTIVE:
                try:
                    self._run_pack_code_only()
                except Exception:
                    pass
        except Exception as e:
            q.done({"ok": False, "error": str(e)[:300]})
            with self._lock:
                self._state["meta"]["status"] = f"Gemma event error: {e}"[:200]
        finally:
            self._busy = False
            with self._lock:
                self._state["meta"]["busy"] = False

    def _run_gemma_triggers(self, triggers: list[dict[str, Any]]) -> None:
        """Pull only trigger seq frames from the 30fps ring → warm Gemma."""
        global _LAST_GEMMA_TRIGGER_AT
        if self._busy:
            return
        now = time.time()
        # Prefer face/operator/firmware over motion spam (chaotic door linger)
        ranked = [
            t
            for t in triggers
            if str(t.get("kind") or "") in ("face", "operator", "firmware_face", "mark")
        ]
        has_id_trigger = bool(ranked)
        if not ranked:
            # Pure motion: skip if we just ran Gemma (linger thrash) unless
            # the FOV has been quiet long enough to matter for leave/enter.
            if (now - _LAST_GEMMA_TRIGGER_AT) < max(_GEMMA_MIN_INTERVAL * 2, 16.0):
                return
            ranked = triggers[-2:]  # at most two pure-motion frames
        else:
            ranked = ranked[-4:]
            # Identity triggers still honor a hard floor so chaotic multi-face
            # bursts (joint enter) don't serialize 4 Gemma loads back-to-back.
            if (now - _LAST_GEMMA_TRIGGER_AT) < _GEMMA_MIN_INTERVAL:
                return
        self._busy = True
        try:
            with self._lock:
                self._state["meta"]["busy"] = True
                self._state["meta"]["status"] = (
                    f"Gemma on {len(ranked)} trigger frame(s)…"
                )
            # unique seqs, keep order, max 4 images
            seqs: list[int] = []
            for t in ranked:
                s = t.get("seq")
                if s is None:
                    continue
                si = int(s)
                if si not in seqs:
                    seqs.append(si)
            seqs = seqs[-4:]
            triggers = ranked
            frames = _fetch_frames_by_seq(seqs)
            # evidence from latest pack path if present
            ear = None
            try:
                pack_path = WORK / "shared_pack_latest.json"
                if pack_path.is_file():
                    pack = json.loads(pack_path.read_text())
                    ear = _shared_evidence_text(pack)[:1200]
                    # Chaotic entry: surface faceid names so Gemma doesn't
                    # report vision-only "unknown" when emb already has the user.
                    fh = (pack.get("face_hits") or {}).get("people_best") or {}
                    if fh:
                        emb_line = (
                            "EMB face_hits (faceid, same pack): "
                            + ", ".join(
                                f"{k} sim={v}"
                                for k, v in sorted(
                                    fh.items(), key=lambda x: -float(x[1] or 0)
                                )
                            )
                            + ". Prefer these ids over inventing unknown when "
                            "a body is visible and sim is strong."
                        )
                        ear = (ear + "\n" + emb_line) if ear else emb_line
            except Exception:
                pack = None
            out = provider_gemma_trigger(frames, triggers=triggers, ear_note=ear)
            _LAST_GEMMA_TRIGGER_AT = time.time()
            self._set_provider("gemma", out)
            with self._lock:
                self._state["meta"]["status"] = (
                    f"Gemma trigger done lat={out.get('latency_sec')}s "
                    f"seqs={seqs} ok={out.get('ok')} id_trig={has_id_trigger}"
                )
                self._state["meta"]["last_trigger_seqs"] = seqs
                self._state["meta"]["updated_local"] = _iso()
        finally:
            self._busy = False
            with self._lock:
                self._state["meta"]["busy"] = False

    def _run_cycle(self, *, with_audio: bool = True) -> dict[str, Any]:
        """One full shadow trial: shared sensors, independent brains."""
        self._busy = True
        self._set_provider("meta_busy", {})  # touch meta via lock
        with self._lock:
            self._state["meta"]["busy"] = True
            self._state["meta"]["status"] = "running full shadow cycle…"
        try:
            with self._lock:
                self._state["meta"]["status"] = "building ONE shared pack for all…"
                self._state["meta"]["driver"] = DRIVER
            pack = _build_shared_pack(with_audio=with_audio)
            cam = pack.get("camera") or {}
            # Prefer pack strip, then live strip, then latest still
            candidates = [
                str(WORK / "pack_vision.jpg"),  # stable — survives gemma load time
                cam.get("strip_path"),
                cam.get("strip_live"),
                str(WORK / "strip_live.jpg"),
                cam.get("latest_path"),
                cam.get("latest_live"),
            ]
            frame = None
            for c in candidates:
                if c and Path(c).is_file():
                    frame = Path(c)
                    break
            if frame is None:
                frame = _grab_frame()
            evidence = _shared_evidence_text(pack)
            sensors = {
                "fair": True,
                "pack_path": pack.get("pack_path"),
                "same_for": sorted(ACTIVE),
                "frame": str(frame) if frame else None,
                "camera": cam,
                "mic": pack.get("mic"),
                "speaking": pack.get("speaking"),
                "emb_catalog": pack.get("emb_catalog"),
                "face_hits": pack.get("face_hits"),
                "room_memory": pack.get("room_memory"),
                "evidence_preview": evidence[:700],
                "driver": DRIVER,
                "stt_text": (pack.get("speaking") or {}).get("text"),
                "stt_person_id": (pack.get("speaking") or {}).get("id"),
            }
            with self._lock:
                self._state["meta"]["sensors"] = sensors
                self._state["meta"]["pack_path"] = pack.get("pack_path")
                self._state["meta"]["status"] = "all oracles on identical pack…"
                self._state["meta"]["driver"] = DRIVER
            frame_note = str(frame) if frame else None
            audio = None  # already consumed into pack

            if "code" in ACTIVE:
                try:
                    self._set_provider("code", provider_code_from_pack(pack))
                except Exception as e:
                    self._set_provider(
                        "code",
                        {
                            "provider": "code",
                            "ok": False,
                            "error": str(e)[:300],
                            "ts_local": _iso(),
                        },
                    )

            if GPU_ON and frame is not None:
                if "gemma" in ACTIVE:
                    try:
                        self._set_provider(
                            "gemma",
                            provider_gemma(frame, audio, ear_note=evidence),
                        )
                    except Exception as e:
                        self._set_provider(
                            "gemma",
                            {
                                "provider": "gemma",
                                "ok": False,
                                "error": str(e)[:500],
                                "ts_local": _iso(),
                            },
                        )
                if "cosmos" in ACTIVE:
                    try:
                        self._set_provider(
                            "cosmos",
                            provider_cosmos(frame, audio, ear_note=evidence),
                        )
                    except Exception as e:
                        self._set_provider(
                            "cosmos",
                            {
                                "provider": "cosmos",
                                "ok": False,
                                "error": str(e)[:500],
                                "ts_local": _iso(),
                            },
                        )
            elif not GPU_ON:
                for k in ("gemma", "cosmos"):
                    if k in ACTIVE:
                        self._set_provider(
                            k,
                            {
                                "provider": k,
                                "ok": False,
                                "note": "RAIL_ORACLE_GPU=0 — park face :8005 first",
                                "ts_local": _iso(),
                            },
                        )

            self.cycles += 1
            snap = self.snapshot()
            # Append history row (awaiting operator vote)
            row = {
                "ts_local": _iso(),
                "cycle": self.cycles,
                "frame": frame_note,
                "sensors": sensors,
                "pack_path": pack.get("pack_path"),
                "driver_name": DRIVER,
                "driver": {
                    "in_room": (snap.get(DRIVER) or {}).get("in_room"),
                    "speaking": (snap.get(DRIVER) or {}).get("speaking"),
                    "line": (snap.get(DRIVER) or {}).get("in_room_and_speaking"),
                },
                "code": {
                    "ok": (snap.get("code") or {}).get("ok"),
                    "line": (snap.get("code") or {}).get("in_room_and_speaking"),
                    "in_room": (snap.get("code") or {}).get("in_room"),
                },
                "gemma": {
                    "ok": (snap.get("gemma") or {}).get("ok"),
                    "line": (snap.get("gemma") or {}).get("in_room_and_speaking"),
                    "latency_sec": (snap.get("gemma") or {}).get("latency_sec"),
                },
                "cosmos": {
                    "ok": (snap.get("cosmos") or {}).get("ok"),
                    "line": (snap.get("cosmos") or {}).get("in_room_and_speaking"),
                    "latency_sec": (snap.get("cosmos") or {}).get("latency_sec"),
                },
                "operator_vote": None,
                "operator_truth": None,
            }
            self._append_history(row)
            with self._lock:
                self._state["meta"]["status"] = "idle — vote who beat the driver"
                self._state["meta"]["last_run_id"] = len(self._history) - 1
                self._state["meta"]["sensors"] = sensors
            return snap
        finally:
            self._busy = False
            with self._lock:
                self._state["meta"]["busy"] = False

    def _append_history(self, row: dict) -> None:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._history.append(row)
            self._history = self._history[-HISTORY_MAX:]
            self._state["history"] = list(reversed(self._history[-20:]))
            self._recompute_scoreboard()
        try:
            with HISTORY_PATH.open("a") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        except Exception:
            pass

    def force_once(self) -> dict[str, Any]:
        """Operator refresh: pack+CODE now; Gemma on latest triggers (not 2‑min strip)."""
        if self._busy:
            return self.snapshot()
        self._run_pack_code_only()
        trigs = _fetch_triggers(since_ts=0.0)
        # take newest few so Force still exercises Gemma without full cycle
        if trigs and GPU_ON and "gemma" in ACTIVE:
            self._run_gemma_triggers(trigs[-4:])
        elif GPU_ON and "gemma" in ACTIVE and not GEMMA_TRIGGER_ONLY:
            return self._run_cycle(with_audio=False)
        return self.snapshot()

    def look_around_for_cosmos(
        self,
        *,
        stop_ids: list[str] | None = None,
        engage_sec: float = 1.8,
        run_cosmos: bool = True,
        with_audio: bool | None = None,
        audio_sec: float | None = None,
    ) -> dict[str, Any]:
        """On-demand look-around tool → optional Cosmos reasoner + STT ear note.

        Not a timer/routine. Camera: firmware facefinder per room stop, then
        door home. Mic → rail-stt (fair, no inventory leak). Cosmos gets
        REF+LIVE strip and ear/STT text with body locations for speech.
        """
        if self._busy:
            return {
                "ok": False,
                "tool": "look_around",
                "error": "oracle busy — try again when cycle finishes",
            }
        do_audio = True if with_audio is None else bool(with_audio)
        sec = float(audio_sec if audio_sec is not None else LOOKAROUND_AUDIO_SEC)
        self._busy = True
        with self._lock:
            self._state["meta"]["busy"] = True
            self._state["meta"]["status"] = (
                "look-around tool: facefinder stops + mic → door home…"
            )
        mic_meta: dict[str, Any] = {"ok": False, "note": "audio skipped"}
        speaking: dict[str, Any] = {
            "id": None,
            "text": None,
            "source": None,
            "note": "no audio",
        }
        ear_holder: dict[str, Any] = {"speaking": speaking, "meta": mic_meta}

        def _capture_ear() -> None:
            if not do_audio or sec <= 0:
                return
            try:
                sp, meta = _ear_listen(sec)
                ear_holder["speaking"] = sp
                ear_holder["meta"] = meta
            except Exception as e:
                ear_holder["meta"] = {"ok": False, "error": str(e)[:160]}

        try:
            # Mic via rail-stt (sole watcher) in parallel with PTZ
            ear_thread = None
            if do_audio and sec > 0:
                ear_thread = threading.Thread(
                    target=_capture_ear, name="look-around-ear", daemon=True
                )
                ear_thread.start()
            # Clear previous stop stills so we never stitch stale face shots
            frame_dir_pre = WORK / "look_around_frames"
            try:
                if frame_dir_pre.is_dir():
                    for old in frame_dir_pre.glob("*.jpg"):
                        old.unlink(missing_ok=True)
            except Exception:
                pass
            cam = look_around_camera(
                stop_ids=stop_ids or ["desk", "chair"],
                engage_sec=engage_sec,
                use_facefinder=True,
            )
            if ear_thread is not None:
                ear_thread.join(timeout=max(sec + 90.0, 30.0))
            mic_meta = ear_holder.get("meta") or mic_meta
            if do_audio:
                speaking = ear_holder.get("speaking") or speaking
                if not speaking.get("text") and not mic_meta.get("ok"):
                    speaking = {
                        "id": None,
                        "text": None,
                        "source": None,
                        "note": "rail-stt listen empty/fail",
                        "mic": mic_meta,
                    }
            frames = list(cam.get("frames") or [])
            # Recover path for THIS run's stop ids only — never re-glob old leftovers
            # (that used to dump dozens of jpgs into the vision pack).
            frame_dir = WORK / "look_around_frames"
            this_ids = {
                str(fr.get("id"))
                for fr in frames
                if fr.get("id") and str(fr.get("id")) not in ("", "None")
            }
            for fr in frames:
                if fr.get("path") and Path(fr["path"]).is_file():
                    continue
                sid = fr.get("id")
                if not sid:
                    continue
                cand = frame_dir / f"{sid}.jpg"
                if cand.is_file() and cand.stat().st_size > 500:
                    fr["path"] = str(cand)
                    fr["ok"] = True
            # Drop frames not in this response (safety)
            frames = [fr for fr in frames if str(fr.get("id") or "") in this_ids]

            # Face emb per LIVE stop + STT/rails → room_memory locations (speech map)
            stop_hits: list[dict[str, Any]] = []
            try:
                with self._lock:
                    self._state["meta"]["status"] = "look-around: face match on LIVE stops…"
                for fr in frames:
                    path = fr.get("path")
                    if not path or not Path(path).is_file():
                        continue
                    raw = Path(path).read_bytes()
                    if len(raw) < 500:
                        continue
                    # slightly softer thr for seated/side faces at desk
                    fh = _face_hits_on_jpegs([raw], thr=0.35)
                    loc = str(fr.get("id") or "in_room")
                    apps = fh.get("appearances") or {}
                    for pid, sim in (fh.get("people_best") or {}).items():
                        stop_hits.append(
                            {
                                "id": pid,
                                "sim": sim,
                                "location": loc,
                                "appearance": apps.get(pid),
                            }
                        )
                    # unknown face still marks occupied stop for speech map
                    if not (fh.get("people_best") or {}) and (fh.get("hits") or []):
                        stop_hits.append(
                            {
                                "id": "unknown",
                                "sim": 0.0,
                                "location": loc,
                                "n_faces": len(fh.get("hits") or []),
                            }
                        )
            except Exception as e:
                stop_hits = [{"error": str(e)[:160]}]

            rails_names: list[str] = []
            try:
                pr = _http_json(PRESENCE_URL, timeout=2.0)
                rails_names = [
                    str(p.get("name"))
                    for p in (pr.get("people") or [])
                    if p.get("name")
                ]
            except Exception:
                pass

            mem_apply = _ROOM_MEMORY.apply_look_around_evidence(
                stop_hits=[h for h in stop_hits if h.get("id") != "unknown"],
                speaking=speaking,
                rails_names=rails_names,
            )
            # unknown faces: if rails has exactly one man (the user) and we saw faces, soft-bind
            if any(h.get("id") == "unknown" for h in stop_hits) and not (
                mem_apply.get("present") or []
            ):
                if rails_names == ["user"] or (
                    len(rails_names) == 1 and rails_names[0] in ("user", "guest3")
                ):
                    loc = next(
                        (
                            h.get("location")
                            for h in stop_hits
                            if h.get("location") and h.get("id") == "unknown"
                        ),
                        "in_room",
                    )
                    _ROOM_MEMORY.note_at(
                        rails_names[0],
                        location=str(loc),
                        sim=0.45,
                        source="look_around_unknown_face_rails",
                    )
                    mem_apply = {
                        "ok": True,
                        "applied": [{"kind": "soft_bind", "id": rails_names[0]}],
                        "present": [
                            {"id": p, "location": ( _ROOM_MEMORY.present.get(p) or {}).get("location")}
                            for p in _ROOM_MEMORY.present
                        ],
                    }

            # Sole sticky person after look-around = default speaker candidate (mic may be silent)
            if (
                len(_ROOM_MEMORY.present) == 1
                and not (speaking.get("id") or speaking.get("text"))
            ):
                only = next(iter(_ROOM_MEMORY.present.values()))
                only["likely_speaker"] = True
                _ROOM_MEMORY._save()
            bodies = [
                {
                    "id": p.get("id"),
                    "location": p.get("location") or "in_room",
                    "likely_speaker": bool(p.get("likely_speaker")),
                    "appearance": (p.get("appearance") or {}).get("summary")
                    if isinstance(p.get("appearance"), dict)
                    else p.get("appearance"),
                }
                for p in _ROOM_MEMORY.present.values()
            ]
            ear_note = (
                "EAR/STT (look-around tool):\n"
                f"  mic: backend={mic_meta.get('backend')} silent={mic_meta.get('silent')} "
                f"rms_db={mic_meta.get('rms_db')} peak={mic_meta.get('peak')}\n"
                f"  stt_text={speaking.get('text')!r} person_id={speaking.get('id')!r} "
                f"source={speaking.get('source')} conf={speaking.get('confidence')}\n"
                f"  voice_stamp={json.dumps(speaking.get('stamp') or {}, default=str)[:300]}\n"
                f"BODY LOCATIONS (for speech — who might be talking):\n"
                f"  {json.dumps(bodies, default=str)}\n"
                f"FACE HITS this look-around: {json.dumps(stop_hits, default=str)[:500]}\n"
                f"RAILS sticky names: {rails_names}\n"
                "If LIVE shows a man and voice matches the user (or only the user is sticky), he is the likely speaker.\n"
            )

            refs = room_reference_paths()
            strip = stitch_look_around_strip(
                frames,
                out_path=WORK / "look_around_vision.jpg",
                include_refs=True,
                max_live=4,
                max_refs=2,
                cols_per_row=2,
                panel_h=220,
            )
            vision_path = str(strip) if strip else None
            if strip and strip.is_file():
                try:
                    import shutil

                    # Dedicated paths for look-around (stable). Do NOT overwrite
                    # strip_live every 1.5s from door ring while hold is active.
                    shutil.copy(strip, WORK / "look_around_pack_vision.jpg")
                    shutil.copy(strip, WORK / "look_around_vision.jpg")
                    # Snapshot for board during hold
                    shutil.copy(strip, WORK / "strip_live.jpg")
                except Exception:
                    pass
                # Pin board to grid so door time-strip doesn't snap layout back to a line
                self._look_around_vision_path = str(Path(strip).resolve())
                self._look_around_hold_until = time.time() + LOOKAROUND_HOLD_SEC
                with self._lock:
                    self._state["meta"]["live_strip_path"] = self._look_around_vision_path
                    self._state["meta"]["live_strip_mode"] = "look_around_hold"
                    self._state["meta"]["look_around_hold_until_local"] = _iso(
                        self._look_around_hold_until
                    )
                    self._state["meta"]["look_around_hold_sec"] = LOOKAROUND_HOLD_SEC
                    sens = self._state["meta"].setdefault("sensors", {})
                    sens["live_strip"] = {
                        "path": self._look_around_vision_path,
                        "mode": "look_around_hold",
                        "hold_remain_sec": LOOKAROUND_HOLD_SEC,
                        "updated_local": _iso(),
                        "ok": True,
                    }
            cosmos_out: dict[str, Any] | None = None
            vision_for_model = None
            for c in (
                strip,
                WORK / "look_around_pack_vision.jpg",
                WORK / "look_around_vision.jpg",
            ):
                if c and Path(c).is_file() and Path(c).stat().st_size > 1000:
                    vision_for_model = Path(c)
                    break
            if run_cosmos and GPU_ON and vision_for_model is not None:
                with self._lock:
                    self._state["meta"]["status"] = (
                        "look-around: Cosmos on REF+LIVE+ear…"
                    )
                try:
                    t0 = time.time()
                    prompt = ASK_LOOK_AROUND_COSMOS + "\n\n" + ear_note
                    ans = _ask_cosmos(vision_for_model, [("now", prompt)])
                    raw = ans.get("now") or ""
                    fields = cosmos_result_from_raw(raw)
                    # confirm sample.json pointed at look-around vision
                    sample_p = WORK / "cosmos" / "now" / "sample.json"
                    vision_path = str(vision_for_model.resolve())
                    cosmos_out = {
                        "provider": "cosmos",
                        "label": "COSMOS3-Edge (look-around tool)",
                        "ts_local": _iso(),
                        "ok": fields.get("ok"),
                        "is_driver": DRIVER == "cosmos",
                        "latency_sec": round(time.time() - t0, 1),
                        "raw_now": raw[:4000],
                        "template": "nvidia_cosmos3_edge_reasoner",
                        "tool": "look_around",
                        "vision": "look_around_refs_plus_live",
                        "vision_path": vision_path,
                        "refs": {k: str(v) for k, v in refs.items()},
                        "live_stops": [
                            f.get("id") for f in frames if f.get("ok") or f.get("path")
                        ],
                        "ear": {
                            "stt_text": speaking.get("text"),
                            "person_id": speaking.get("id"),
                            "source": speaking.get("source"),
                            "mic_silent": mic_meta.get("silent"),
                        },
                        "bodies": bodies,
                        "sample_path": str(sample_p),
                    }
                    cosmos_out.update(fields)
                    # prefer STT speaking if model didn't fill it
                    if not (cosmos_out.get("speaking") or {}).get("id") and speaking.get(
                        "id"
                    ):
                        cosmos_out["speaking"] = {
                            "id": speaking.get("id"),
                            "text": speaking.get("text") or "",
                            "source": speaking.get("source") or "voice",
                            "conf": speaking.get("confidence") or 0.0,
                        }
                    self._set_provider("cosmos", cosmos_out)
                except Exception as e:
                    cosmos_out = {
                        "provider": "cosmos",
                        "ok": False,
                        "tool": "look_around",
                        "error": str(e)[:500],
                        "ts_local": _iso(),
                        "vision_path": str(vision_for_model),
                        "refs": {k: str(v) for k, v in refs.items()},
                        "ear": {
                            "stt_text": speaking.get("text"),
                            "person_id": speaking.get("id"),
                        },
                        "bodies": bodies,
                    }
                    self._set_provider("cosmos", cosmos_out)
            elif run_cosmos and vision_for_model is None:
                cosmos_out = {
                    "provider": "cosmos",
                    "ok": False,
                    "tool": "look_around",
                    "error": "no look-around vision built (no live frames and no refs)",
                    "ts_local": _iso(),
                    "refs": {k: str(v) for k, v in refs.items()},
                }
                self._set_provider("cosmos", cosmos_out)
            with self._lock:
                self._state["meta"]["last_look_around"] = {
                    "ts_local": _iso(),
                    "n_ok": cam.get("n_ok"),
                    "ids": [
                        f.get("id")
                        for f in frames
                        if f.get("ok") or f.get("path")
                    ],
                    "door_ok": cam.get("door_ok"),
                    "door_pan": cam.get("door_pan"),
                    "use_facefinder": cam.get("use_facefinder"),
                    "vision_path": vision_path,
                    "refs": list(refs.keys()),
                    "cosmos_ok": (cosmos_out or {}).get("ok"),
                    "bodies": bodies,
                    "speaker": speaking.get("id"),
                    "stt_text": (speaking.get("text") or "")[:80],
                    "memory": mem_apply.get("present"),
                }
                self._state["meta"]["status"] = (
                    f"look-around done door_ok={cam.get('door_ok')} "
                    f"bodies={ [b.get('id') for b in bodies] } "
                    f"speaker={speaking.get('id')} cosmos={(cosmos_out or {}).get('ok')}"
                )
                self._state["meta"]["updated_local"] = _iso()
            frames_meta = [
                {k: v for k, v in fr.items() if k != "jpeg_b64"} for fr in frames
            ]
            return {
                "ok": bool(
                    cam.get("ok")
                    or cam.get("door_ok")
                    or vision_for_model is not None
                ),
                "tool": "look_around",
                "routine": False,
                "camera": {
                    "n_ok": cam.get("n_ok"),
                    "n_stops": cam.get("n_stops"),
                    "use_facefinder": cam.get("use_facefinder"),
                    "engage_sec": cam.get("engage_sec"),
                    "facelock": cam.get("facelock"),
                    "door_return": cam.get("door_return"),
                    "door_ok": cam.get("door_ok"),
                    "door_pan": cam.get("door_pan"),
                    "default_hold": "door",
                    "note": cam.get("note"),
                    "frames": frames_meta,
                    "facelock_events": cam.get("facelock_events"),
                },
                "refs": {k: str(v) for k, v in refs.items()},
                "vision_path": vision_path,
                "speaking": speaking,
                "mic": mic_meta,
                "bodies": bodies,
                "face_hits": stop_hits,
                "memory": mem_apply,
                "cosmos": cosmos_out,
            }
        finally:
            self._busy = False
            with self._lock:
                self._state["meta"]["busy"] = False

    def vote(
        self,
        *,
        winner: str,
        truth: str | None = None,
        run_id: int | None = None,
    ) -> dict[str, Any]:
        """Operator: who beat the driver? winner in code|gemma|cosmos|tie|skip."""
        winner = (winner or "").strip().lower()
        if winner not in ("code", "gemma", "cosmos", "tie", "skip"):
            return {"ok": False, "error": "winner must be code|gemma|cosmos|tie|skip"}
        with self._lock:
            if not self._history:
                return {"ok": False, "error": "no runs yet — Run all first"}
            idx = run_id if run_id is not None else len(self._history) - 1
            if idx < 0 or idx >= len(self._history):
                return {"ok": False, "error": f"bad run_id {idx}"}
            self._history[idx]["operator_vote"] = winner
            if truth:
                self._history[idx]["operator_truth"] = truth
            self._history[idx]["voted_local"] = _iso()
            self._recompute_scoreboard()
            self._state["history"] = list(reversed(self._history[-20:]))
            row = dict(self._history[idx])
            sb = dict(self._state["meta"]["scoreboard"])
        # rewrite last lines is hard; append vote audit
        try:
            audit = HISTORY_PATH.with_suffix(".votes.jsonl")
            with audit.open("a") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        except Exception:
            pass
        return {"ok": True, "run": row, "scoreboard": sb}


_bench: OracleBench | None = None


def get_bench() -> OracleBench:
    global _bench
    if _bench is None:
        _bench = OracleBench()
        _bench.start()
    return _bench
