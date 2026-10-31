"""Best-effort notify Argus on door packs (describe / leave).

HTTP only — no Crissa. Failures are logged and never block the door tick.
Argus describes the clip; that sentence is posted to omni. Not SoT.
Geometry still decides {null,+1,−1}. Argus may look before kind=enter.
"""
from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths

import base64
import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from . import connect as _connect

# The describer is the agent the person connected (Connect page, <state home>/agent.json); RAIL_ARGUS_* still
# names another. No agent connected = no describer, and every call here fails quietly (2026-10-09).
ARGUS_URL = os.environ.get("RAIL_ARGUS_CHAT") or _connect.chat_url()
ARGUS_KEY = os.environ.get("RAIL_ARGUS_API_KEY") or _connect.key()
ARGUS_MODEL = os.environ.get("RAIL_ARGUS_MODEL") or _connect.model(default="argus")
OMNI_NOTE_URL = os.environ.get(
    "RAIL_STT_OMNI_NOTE", _ports.url("stt", "/v1/omni/note")
)
N_FRAMES = int(os.environ.get("RAIL_ARGUS_DESCRIBE_FRAMES", "5"))
ORACLE_VISIT_URL = os.environ.get(
    "RAIL_ORACLE_VISIT", _ports.url("presence", "/v1/oracle_visit")
)
AUDIT_LOG = Path(
    os.environ.get(
        "RAIL_ARGUS_DOOR_LOG",
        str(_paths.DATA / "argus_door_log.jsonl"),
    )
)
# Harvest packs keep every raw frame (CLIP_KEEP_ALL); n is not a motion
# signal. Energy is the floor. enter/leave/exit skip this (already classified).
MOTION_MIN_FRAMES = int(os.environ.get("RAIL_ARGUS_MOTION_MIN_FRAMES", "8"))
MOTION_MIN_ENERGY = float(os.environ.get("RAIL_ARGUS_MOTION_MIN_ENERGY", "8.0"))
CROSSING_KINDS = frozenset({"enter", "leave", "exit"})
LEAVE_KINDS = frozenset({"leave", "exit"})
MOTION_KINDS = frozenset({"", "none", "motion", "still", "still_empty"})

HOME = _paths.ROOT / "argus"
LEAVE_NOTIFY = HOME / "LEAVE-NOTIFY-GROK.md"
DESCRIBE_ORDER = HOME / "NEXT-ENTRY-DESCRIBE.md"

VISIT_JSON = (
    "Then ONE JSON object, no other keys: "
    '{"event":"enter|exit|sighting|unresolved","user_exit":true,'
    '"clothes":"…","hair":"…","sex":"M|F|null","who":"id-or-null"}. '
    "user_exit is whether the User left through this door: true, false, or null "
    "(unsure). Additive — keep event/clothes/hair/sex/who. "
    "On exit, who is a PRESENT id whose clothes and hair match, or null. "
    "Do not change headcount — rails apply the JSON."
)

# Live Oracle scoreboard. Crew :8005 does not inject the agent. Do not send
# the agent's SOUL.md or the 8B student add/sub/null file on this path — they
# fight this JSON (measured 2026-08-25: stuffing SOUL.md as system).
ARGUS_ORACLE_IDENTITY = (
    "You are Argus, the house door watchman. You judge this doorway clip.\n"
    "Eyes propose. Rails write occupancy and AUTH. You never change either.\n"
    "Also answer: did the User leave through this door? user_exit true / false / null.\n"
    "true = back (or body) room → hallway through the plane.\n"
    "false = still in the room, including face-toward-lens then empty doorway "
    "(they sat behind this camera at hour 6).\n"
    "null = unsure. Rails will look at seat 6.\n"
    "Keep event / clothes / hair / sex / who as well.\n"
)

DOOR_LAW = (
    "Oldest first. You are watching the only door.\n"
    "A crossing needs the person on BOTH sides of the door plane "
    "(room-side and hallway-side) or unambiguous mid-plane motion. "
    "Cut by the jamb (only hallway-side visible) is outside.\n"
    "ENTER: they cross the door plane hallway → room. Often a FACE coming "
    "toward you, then they walk past the lens and the doorway goes empty — "
    "they sat behind this camera (User desk, hour 6). That empty doorway is "
    "still an enter. Do not call that an exit.\n"
    "EXIT: they cross the door plane room → hallway. Usually a BACK, no face. "
    "Do not wait for a face.\n"
    "sighting: in-room motion (dresser, microwave) and they are still in view "
    "at the end of the CLIP, with no empty-door end still. They did not cross.\n"
    "A pet in the doorway is not a person.\n"
    "If rest_ts is set, the extra still is empty FOV: the walk is over. All "
    "action is BEFORE it. Empty at the end is expected after enter AND after "
    "exit — it does not mean nothing crossed.\n"
    "Storm: person in FOV, plane unclear, rest_ts null → unresolved or "
    "sighting. user_exit false unless you SAW them leave into the hall. "
    "Hall/jamb = still outside. Face toward the lens then empty doorway = "
    "sat behind this camera at hour 6, not a leave. Back / body room → "
    "hallway through the plane = leave. This is a door movie; do not aim.\n"
    "If you SAW the plane crossed, answer enter or exit even if they then "
    "leave the frame.\n"
    "unresolved only when you cannot tell whether a crossing happened: clip "
    "ends mid-approach; they never reach the door; or they are at the jamb "
    "and you did not see them cross. Do not promote frame-exit-near-the-door "
    "to enter or exit. Unresolved beats guessing.\n"
    "Write ONE or TWO short sentences: clothing, motion, direction. "
    "No names unless certain. A close-up face crop first is FaceID for "
    "clothing, not a hallway portrait.\n"
)


def _room() -> str:
    try:
        from .room_context import room_paragraph

        return room_paragraph()
    except Exception:
        return ""


def oracle_system() -> str:
    """System message for door clips on crew :8005.

    Argus identity + Oracle visit law. Not the agent SOUL.md, not 8B student.
    Call at ask time so ROOM geometry is not import-stale.
    """
    return ARGUS_ORACLE_IDENTITY + _room() + DOOR_LAW + VISIT_JSON


def motion_prompt() -> str:
    """User-side door brief (pack notify). Clip POST uses oracle_system()."""
    return _room() + DOOR_LAW + VISIT_JSON


MOTION_PROMPT = motion_prompt()
ENTER_PROMPT = (
    "Door ENTRY, oldest first. If a close-up face crop is first, that is the "
    "best FaceID shot (min 60px) — use it for clothing/hair, not hallway "
    "portraits. "
    + DOOR_LAW
    + VISIT_JSON
)


def _sot_named_ids() -> set[str]:
    """Certain named people on Code SoT. Empty = filter off (tests / cold)."""
    try:
        from . import server as _server

        svc = getattr(_server, "_svc", None)
        st = svc.get() if svc is not None else None
    except Exception:
        return set()
    if st is None:
        return set()
    ids: set[str] = set()
    for p in getattr(st, "people", None) or []:
        n = str(getattr(p, "name", "") or "").strip().lower()
        if not n or n.startswith(("unknown", "crowd")):
            continue
        if len(n) >= 2 and n[0] in "ufm" and n[1:].isdigit():
            continue
        if getattr(p, "certain", True) is False:
            continue
        ids.add(n)
    return ids


def present_block() -> str:
    """The people already on open visits, described as Argus described them.

    the User 2026-08-16: "we need to finish the who left match game I think" /
    "should be easy to compare as Argus listed the clothes to begin with."

    He is right that the data is already there. Every entry stores the clothes
    and hair Argus reported at the time, so an exit only needs those same words
    handed back to him beside the clip. another person entered as "light gray cardigan
    over black top, bun, F" and walked out again; _person_for saw two open
    visits, could not choose, and fell through to the sole roster user — `the user`.
    A woman in a cardigan left and the User was marked gone.

    This is attribution among people ALREADY known to be present, not
    recognition: `who` may only be one of the ids offered here, or null. Argus
    still cannot name a stranger, so "never identity" holds — he is picking from
    a closed set, which is exactly the comparison he is good at.

    Silent below two candidates: with one open visit the existing path already
    answers, and offering a single id invites rubber-stamping it.
    """
    try:
        from .oracle_event import open_visits

        live = _sot_named_ids()
        by_pid: dict[str, str] = {}
        for r in open_visits() or []:
            pid = str(r.get("person_id") or "").strip().lower()
            if not pid or pid == "unknown":
                continue
            if live and pid not in live:
                continue
            bits = [str(r[k])[:80] for k in ("clothes", "hair") if r.get(k)]
            if r.get("sex"):
                bits.append(str(r["sex"])[:4])
            by_pid[pid] = f"{pid} = " + (", ".join(bits) if bits else "no description")
        rows = list(by_pid.values())
        if not rows:
            return ""
        return (
            "PRESENT, each as described when they entered: "
            + "; ".join(rows)
            + ". On an exit add \"who\" to the JSON — the id from this list whose "
            "clothing and hair match the person leaving. Use \"who\":null if the "
            "clip does not clearly match one of them. Never invent an id. "
        )
    except Exception:
        return ""


def _kind(manifest: dict[str, Any] | None) -> str:
    if not isinstance(manifest, dict):
        return ""
    return str(manifest.get("kind") or "").strip().lower()


def _pack_n(manifest: dict[str, Any]) -> int:
    try:
        n = int(manifest.get("n") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        frames = manifest.get("frames")
        if isinstance(frames, list):
            n = len(frames)
        elif isinstance(frames, int):
            n = frames
    return n


def _pack_energy(manifest: dict[str, Any]) -> float:
    try:
        return float(manifest.get("max_energy") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def pack_worth_describe(manifest: dict[str, Any] | None) -> bool:
    """True when Argus should see this pack. Never a SoT vote.

    enter/leave/exit always (code already classified a crossing).
    Harvest still/still_empty and kind-less packs need a motion floor —
    CLIP_KEEP_ALL makes n huge even for junk, so energy is the gate.
    """
    if not isinstance(manifest, dict):
        return False
    kind = _kind(manifest)
    if kind in CROSSING_KINDS:
        return True
    # A door_v2 pack exists BECAUSE observe_door already minted a door event —
    # capture_kind_for_event had to return a kind for the pack to be cut at all.
    # Re-litigating that with an energy floor throws real crossings away:
    # ev_1786823114540 on 2026-08-15 holds a woman standing in the doorway with
    # the dog, and scored max_energy 3.748 against a floor of 8.0, so Argus was
    # never called. The floor cannot be repaired by moving it either — empty
    # harvest packs the same afternoon scored 4.41 and 11.71, both ABOVE this
    # real person. Energy does not separate the two populations; the door event
    # does, and that evidence is already in hand by this point.
    if str(manifest.get("capture_mode") or "") == "door_v2":
        return True
    resolve = str(manifest.get("resolve") or "").strip().lower()
    unlabeled = kind in MOTION_KINDS
    stillish = resolve in ("still", "still_empty", "timeout")
    if not (unlabeled or stillish):
        return False
    if _pack_n(manifest) < MOTION_MIN_FRAMES:
        return False
    if _pack_energy(manifest) < MOTION_MIN_ENERGY:
        return False
    return True


def _face_px_ok(meta: dict[str, Any] | None) -> bool:
    """Ship only FaceID-usable faces (min 60 px). Hallway portraits are ~20."""
    if not isinstance(meta, dict):
        return False
    px = meta.get("face_px")
    try:
        if isinstance(px, (list, tuple)) and px:
            return min(float(x) for x in px) >= 60
        return float(px) >= 60
    except (TypeError, ValueError):
        return False


def _onset_i(
    frames: list[dict[str, Any]],
    trigger_ts: Any,
    onset_seq: Any = None,
) -> int:
    """Gold: trigger time → find_onset (first delta). No new picker.

    Prefers the onset the CAPTURE recorded (`manifest.onset_seq`) over
    recomputing one here. Recomputation runs over the already-SELECTED frames,
    not the original ring, so it can land a few frames off the value the pack
    was actually built around — two answers to one question. The seq is also
    the join key Oracle rows on, so reading it here keeps Argus and Oracle
    pointed at the same first frame of motion.
    """
    from .door_capture import find_onset

    if onset_seq is not None:
        try:
            want = int(onset_seq)
            for i, fr in enumerate(frames):
                if int(fr.get("seq") or -1) == want:
                    return i
        except (TypeError, ValueError):
            pass

    rows: list[dict[str, Any]] = []
    for i, fr in enumerate(frames):
        try:
            ts = float(fr["ts"]) if fr.get("ts") is not None else float(i)
        except (TypeError, ValueError):
            ts = float(i)
        try:
            e = float(fr.get("energy") or 0.0)
        except (TypeError, ValueError):
            e = 0.0
        rows.append({"idx": i, "ts": ts, "energy": e})
    if not rows:
        return 0
    if trigger_ts is None:
        return 0
    try:
        return int(find_onset(rows, float(trigger_ts)))
    except Exception:
        return 0


def pick_pack_jpegs(manifest: dict[str, Any], *, n: int = N_FRAMES) -> list[Path]:
    """Crop (any kind) + even stills from find_onset(trigger_ts)."""
    root = Path(str(manifest.get("pack_dir") or ""))
    out: list[Path] = []
    for name in ("entry_best_face.jpg", "entry_face.jpg", "entry_best.jpg"):
        p = root / name
        if p.is_file():
            out.append(p)
            break
    arts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    face_meta = arts.get("entry_face") if isinstance(arts.get("entry_face"), dict) else {}
    if _face_px_ok(face_meta):
        fn = face_meta.get("file")
        if fn:
            p = root / str(fn)
            if p.is_file() and p not in out:
                out.append(p)
    for key in ("entry_body",):
        meta = arts.get(key) if isinstance(arts.get(key), dict) else {}
        fn = meta.get("file")
        if fn:
            p = root / str(fn)
            if p.is_file() and p not in out:
                out.append(p)
    frames = [f for f in (manifest.get("frames") or []) if isinstance(f, dict)]
    paths: list[Path] = []
    for fr in frames:
        p = Path(str(fr.get("path") or (root / str(fr.get("file") or ""))))
        if p.is_file():
            paths.append(p)
    if paths:
        onset = _onset_i(
            frames[: len(paths)],
            manifest.get("trigger_ts"),
            manifest.get("onset_seq"),
        )
        onset = max(0, min(onset, len(paths) - 1))
        win = paths[onset:]
        need = max(1, n - len(out))
        if len(win) <= need:
            pick = win
        else:
            step = (len(win) - 1) / max(1, need - 1)
            pick = [win[int(round(i * step))] for i in range(need)]
        for p in pick:
            if p not in out:
                out.append(p)
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        k = str(p)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    return uniq[: max(1, n)]


def _write_sent_sheet(jpegs: list[Path], dest: Path) -> None:
    """One strip of the exact stills posted to Glimmer — the User can open this."""
    try:
        import cv2

        ims = []
        for p in jpegs:
            im = cv2.imread(str(p))
            if im is None:
                continue
            ims.append(cv2.resize(im, (320, 180)))
        if not ims:
            return
        cv2.imwrite(str(dest), cv2.hconcat(ims), [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    except Exception:
        return


def _post_argus(content: Any, *, timeout: float = 90.0) -> dict[str, Any]:
    if not ARGUS_KEY:
        return {"ok": False, "error": "no_api_key"}
    body = {
        "model": ARGUS_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 220,
        "temperature": 0.2,
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        ARGUS_URL,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ARGUS_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


VIDEO_ON = os.environ.get("RAIL_ARGUS_VIDEO", "1") not in ("0", "false", "no")
# Always "crew" — the ROLE, never a checkpoint name (the User 2026-08-16: "clean up
# model name alias to crew. No specific so we never need to change the agent's code
# and config again for it").
#
# :8005 serves crew / muse-glimmer / qw3.6-27b as three names for one set of
# weights, and every agent already asks for crew. This line was the
# last place in the tree naming a checkpoint, so swapping the crew model meant
# editing code. Now it does not: whatever answers to crew is what Argus gets,
# and the serve script owns which weights that is.
VIDEO_URL = os.environ.get(
    "RAIL_ARGUS_VIDEO_CHAT", _ports.brain()[0]   # the house's crew engine; a pack's engine on this machine
)
VIDEO_MODEL = os.environ.get("RAIL_ARGUS_VIDEO_MODEL", "crew")
# Per-request sampling for crew :8005. Default N=20 ("20 slides") + fps so
# Qwen sees the encode. Measured 2026-08-26 on crew 0.27.1 / Qwen3-VL:
# num_frames alone is a no-op (Q2=N8=N24=1344 pads); fps is the lever
# (F15 → 4032 pads on the same 4.04 s / 23-frame mp4). Default fps =
# N/duration (else 15). RAIL_ARGUS_VIDEO_NUM_FRAMES=0 omits num_frames.
# RAIL_ARGUS_VIDEO_FPS=omit (and N=0) is the Q2 control. A numeric FPS
# env (e.g. 15) is the fps-kwargs A/B. Capture stays 15 Hz.
try:
    VIDEO_NUM_FRAMES = int(os.environ.get("RAIL_ARGUS_VIDEO_NUM_FRAMES", "20"))
except (TypeError, ValueError):
    VIDEO_NUM_FRAMES = 20
VIDEO_FPS_RAW = os.environ.get("RAIL_ARGUS_VIDEO_FPS", "").strip()


def _requested_video_fps(
    n_for_honest: int | None,
    duration_s: float | None,
    raw: str | None = None,
) -> float | None:
    """None = omit fps (Q2). Empty env = N/duration else 15."""
    s = (VIDEO_FPS_RAW if raw is None else raw).strip().lower()
    if s in ("omit", "q2", "none"):
        return None
    if s in ("", "honest", "auto"):
        try:
            dur = float(duration_s or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        n = int(n_for_honest or 0)
        if dur > 1e-3 and n > 0:
            return round(max(2.0, float(n) / dur), 4)
        return 15.0
    try:
        fps = float(s)
    except (TypeError, ValueError):
        fps = 15.0
    if fps <= 0:
        return None
    return round(fps, 4)


def video_sampling_kwargs(
    n_encoded: int | None = None,
    duration_s: float | None = None,
) -> dict[str, Any]:
    """Fields to merge into the :8005 chat body. Empty = Q2 control (omit).

    Default N=20 + fps=N/duration so the processor does not resample the
    20-frame clip down to HF's 2 fps. ``fps`` env overrides (F15 A/B).
    """
    n = VIDEO_NUM_FRAMES
    if n > 0 and n_encoded is not None:
        try:
            n = max(1, min(int(n), int(n_encoded)))
        except (TypeError, ValueError):
            pass
    if n >= 2 and n % 2:
        n -= 1  # patch_temporal=2
    n_for_honest = n if n > 0 else n_encoded
    fps = _requested_video_fps(n_for_honest, duration_s)
    if n <= 0 and fps is None:
        return {}
    video: dict[str, Any] = {}
    if n > 0:
        video["num_frames"] = n
    if fps is not None:
        video["fps"] = fps
    out: dict[str, Any] = {}
    if video:
        out["media_io_kwargs"] = {"video": video}
    if fps is not None:
        out["mm_processor_kwargs"] = {"fps": fps}
    return out


def _post_glimmer_video(
    mp4: bytes, prompt: str, *, timeout: float = 120.0,
    stills: list[bytes] | None = None,
    n_frames: int | None = None,
    duration_s: float | None = None,
    cache_salt: str | None = None,
) -> dict[str, Any]:
    """Ask crew :8005 as Argus Oracle: system brief + clip user."""
    content: list[dict[str, Any]] = []
    for jpeg in stills or []:
        if not jpeg:
            continue
        content.append({
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
            },
        })
    content.append({
        "type": "video_url",
        "video_url": {
            "url": "data:video/mp4;base64," + base64.b64encode(mp4).decode("ascii")
        },
    })
    content.append({"type": "text", "text": prompt})
    messages: list[dict[str, Any]] = []
    sys_p = oracle_system()
    if sys_p:
        messages.append({"role": "system", "content": sys_p})
    messages.append({"role": "user", "content": content})
    body = {
        "model": VIDEO_MODEL,
        "messages": messages,
        # Glimmer always emits hidden reasoning BEFORE the answer, so the budget
        # has to cover the thinking as well as the reply. Too tight and the call
        # returns an empty string with finish_reason "length" — it spent every
        # token reasoning and never reached the JSON. Measured 2026-08-16 on
        # the User's re-entry: 900 tokens produced completion_tokens=900 and an
        # empty body, while the same clip answers fine with room to finish.
        "max_tokens": int(os.environ.get("RAIL_ARGUS_VIDEO_MAX_TOKENS", "2500")),
        "temperature": 0.0,
    }
    body.update(video_sampling_kwargs(n_frames, duration_s))
    if cache_salt:
        body["cache_salt"] = str(cache_salt)
    req = urllib.request.Request(
        VIDEO_URL,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# One physical crossing, one apply.
#
# Two writers cut packs over the same doorway: event-clip harvest and
# door_capture v2. Measured 2026-08-15 12:01, they both cut another person's entry —
# ev_1786809685471 (harvest) and ev_1786809707941 (door_v2) — and both applied
# it, so one person walking in became another person 1->2 then s1 2->3 and the room
# read 4 people for 2.
#
# The packs cannot see each other: their own manifests disagree on the onset
# (87303 vs 87417) because each walks back inside its own window. But
# raw_clip.first_motion anchors on the same raw stream and returned seq 87426
# for BOTH — the same frame, exactly. That is why the claim is keyed on the raw
# onset and not on anything a pack says about itself.
#
# This is in-process state, deliberately: it guards a burst of near-simultaneous
# packs, not history. A restart forgetting an old claim is correct — nothing is
# arriving for it any more.
class _DuplicateCrossing(Exception):
    """A second pack over a crossing that has already been counted."""


_ONSET_CLAIMS: dict[int, tuple[str, float]] = {}
_ONSET_LOCK = threading.Lock()
# Frames apart that still count as the same crossing. The two writers agreed
# exactly here, so this is slack for a resample, not a fudge factor: 15 frames
# is half a second at the 30 fps burst rate.
ONSET_DEDUPE_FRAMES = int(os.environ.get("RAIL_ARGUS_ONSET_DEDUPE_FRAMES", "15"))
ONSET_CLAIM_TTL_SEC = float(os.environ.get("RAIL_ARGUS_ONSET_CLAIM_TTL", "600"))


def claim_crossing(onset_seq: Any, event_id: str) -> dict[str, Any]:
    """Claim a crossing for one event. Returns {"ok": True} for the winner.

    A loser gets {"ok": False, "owner": <event_id>} and must not apply. With no
    onset seq there is nothing to key on, so it is allowed through — failing
    open keeps a missing measurement from silently dropping real entries.
    """
    try:
        seq = int(onset_seq)
    except (TypeError, ValueError):
        return {"ok": True, "reason": "no_onset"}
    now = time.time()
    with _ONSET_LOCK:
        for s, (owner, ts) in list(_ONSET_CLAIMS.items()):
            if now - ts > ONSET_CLAIM_TTL_SEC:
                del _ONSET_CLAIMS[s]
                continue
            if abs(s - seq) <= ONSET_DEDUPE_FRAMES and owner != event_id:
                return {"ok": False, "owner": owner, "onset_seq": s}
        _ONSET_CLAIMS[seq] = (str(event_id), now)
    return {"ok": True, "onset_seq": seq}


def describe_by_video(manifest: dict[str, Any], prompt: str) -> dict[str, Any] | None:
    """Cut the first second of real motion from raw and ask about that.

    None means "no video answer" — no raw, no motion found, no ffmpeg, or the
    model refused — and the caller falls back to the stills path. It never
    means "nothing happened".
    """
    if not VIDEO_ON:
        return None
    trig = manifest.get("trigger_ts")
    if not trig:
        return None
    try:
        from .raw_clip import clip_for_event

        # The pack already walked back to rest; use its answer.
        clip = clip_for_event(
            float(trig),
            onset_ts=manifest.get("onset_ts"),
            onset_seq=manifest.get("onset_seq"),
        )
        if not clip:
            return None
        out = _post_glimmer_video(
            clip["mp4"], prompt,
            n_frames=clip.get("n_frames"),
            duration_s=clip.get("duration_s"),
        )
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}
    return {
        "ok": True,
        "out": out,
        "n_frames": clip["n_frames"],
        "onset": clip["onset"],
    }


def _reply_text(out: dict[str, Any]) -> str:
    msg = ((out.get("choices") or [{}])[0].get("message") or {})
    txt = (msg.get("content") or "").strip()
    if txt:
        return txt
    return ""


def parse_argus_visit(text: str) -> dict[str, Any] | None:
    """Pull {event, clothes, hair, sex} from Argus prose+JSON."""
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:].strip()
    # last JSON object in the reply
    i = t.rfind("{")
    j = t.rfind("}")
    blob = t[i : j + 1] if i >= 0 and j > i else t
    try:
        obj = json.loads(blob)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    ev = str(obj.get("event") or obj.get("kind") or "").strip().lower()
    if ev in ("add", "in"):
        ev = "enter"
    if ev in ("sub", "leave", "out"):
        ev = "exit"
    if ev in ("unknown", "unclear", "indeterminate"):
        ev = "unresolved"
    # unresolved is a VERDICT, not a parse failure. Added to the prompt and to
    # _apply on 2026-08-16 but not here, so the first time Argus used it —
    # ev_1786888948841, "briefly seen... then moves out of the frame, with no
    # entry or exit through the doorway shown" — the parser dropped it and the
    # event surfaced as `argus unparsed`. The model answered correctly and the
    # code discarded the answer.
    if ev not in ("enter", "exit", "sighting", "unresolved"):
        return None
    sex = obj.get("sex")
    if sex is not None:
        sex = str(sex).strip().upper()
        if sex not in ("M", "F"):
            sex = None
    clothes = obj.get("clothes") or obj.get("clothing") or obj.get("appearance")
    if isinstance(clothes, str) and not clothes.strip():
        clothes = None
    # `who` is a SELECTION from the ids offered in present_block(), never a name
    # Argus invented. door_fanout validates it against the open visits before it
    # is allowed to decide an exit.
    who = obj.get("who")
    if who is not None:
        who = str(who).strip().lower()
        if who in ("", "null", "none", "unknown", "unclear"):
            who = None
    # The one-or-two sentences before the JSON are the answer Argus was
    # asked for. Exits must keep them — clothes JSON alone dropped the
    # who-left match (the User 2026-08-18, second loop).
    prose = t[:i].strip() if i > 0 else ""
    if prose.startswith("```"):
        prose = ""
    if not clothes and prose:
        from .traits_leave import clothes_from_prose

        clothes = clothes_from_prose(prose)
    user_exit, user_exit_set = _parse_user_exit(obj, ev, who)
    return {
        "event": ev,
        "clothes": (str(clothes)[:240] if clothes else None),
        "hair": (str(obj.get("hair"))[:120] if obj.get("hair") else None),
        "sex": sex,
        "who": (who[:64] if who else None),
        "user_exit": user_exit,
        "user_exit_set": user_exit_set,
        "comment": (prose[:400] if prose else None),
    }


def _parse_user_exit(
    obj: dict[str, Any], ev: str, who: str | None
) -> tuple[bool | None, bool]:
    """Clip vote: (value, field_present).

    Legacy (no user_exit key): exit AND who==User → True. Any other exit →
    null (another person leaving must not de-AUTH the User). enter → False.
    """
    if "user_exit" in obj:
        v = obj.get("user_exit")
        if v is None:
            return None, True
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("null", "unsure", "unknown", "unclear", ""):
                return None, True
            if s in ("true", "yes", "1", "y"):
                return True, True
            if s in ("false", "no", "0", "n"):
                return False, True
            return None, True
        if v is True:
            return True, True
        if v is False:
            return False, True
        return None, True
    uid = (
        os.environ.get("HOUSE_USER_ID")
        or os.environ.get("RAIL_PRIMARY_USER")
        or "user"
    ).strip().lower() or "user"
    if ev == "exit" and who == uid:
        return True, True
    if ev == "exit":
        return None, True
    if ev == "enter":
        return False, True
    return None, False


def _roster_exit_pick(d: dict, uid: str) -> str:
    """Pick a non-User occupant from a presence payload. Never the User."""
    names = []
    for p in d.get("people") or []:
        n = str(p.get("name") or "").strip().lower()
        if not n or n == "unknown" or n.startswith("unknown") or n.startswith("__"):
            continue
        # U1-style ghosts are unnamed slots, not a second named body.
        if n.startswith("u") and n[1:].isdigit():
            continue
        names.append(n)
    authed = [
        str(x).strip().lower()
        for x in ((d.get("auth") or {}).get("authed_ids") or [])
        if x
    ]
    uid = (uid or "").strip().lower()
    names = [n for n in names if n != uid]
    authed = [n for n in authed if n != uid]
    if len(authed) == 1:
        return authed[0]
    if len(names) == 1:
        return names[0]
    return ""


def _roster_user_for_exit() -> str:
    """Sole named/AUTH person on SoT — leave has no face to name.

    WP-4: cannot pick User without a clothes win (clothes win happens first).
    """
    try:
        url = os.environ.get("RAIL_PRESENCE_URL", os.environ.get("RAIL_PRESENCE", _ports.url("presence", "/v1/presence")))
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=4) as r:
            d = json.loads(r.read().decode())
    except Exception:
        return ""
    uid = (os.environ.get("HOUSE_USER_ID") or "user").strip().lower()
    return _roster_exit_pick(d, uid)


def _faceid_from_pack(pack_dir: str | Path) -> dict[str, Any]:
    p = Path(str(pack_dir)) / "entry_best.json"
    if not p.is_file():
        return {}
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(v, dict):
        return {}
    return {
        "name": v.get("name"),
        "sim": v.get("sim"),
        "face_px": v.get("face_px"),
        "door_view": v.get("door_view"),
    }


def append_audit(row: dict[str, Any]) -> None:
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except OSError:
        pass


# An exit is not a normal write. apply_oracle_visit answers it synchronously and
# the User branch runs confirm_user_exit first — a camera hold (ttl 90 s), a
# facefinder toggle, a seat snap and a room sweep — because AUTH must not drop
# on a clip alone. Eight seconds in front of that meant EVERY Argus exit failed:
# 8 of 8 on 2026-08-15, all "TimeoutError: timed out", each one a verdict that
# was already correct ("exits into the hallway with his back to the camera").
#
# The exit heuristics elsewhere in this rail — LEAVE_CONFIRM_SEC,
# SOLE_EMPTY_LEAVE_SEC, and the three RAIL_USER_ALLOW_*_LEAVE escape hatches —
# were built to cover for exits that never landed. This is why they never did.
VISIT_TIMEOUT_SEC = float(os.environ.get("RAIL_ORACLE_VISIT_TIMEOUT", "8"))
EXIT_TIMEOUT_SEC = float(os.environ.get("RAIL_ORACLE_EXIT_TIMEOUT", "120"))


def post_oracle_visit(body: dict[str, Any]) -> dict[str, Any]:
    url = (ORACLE_VISIT_URL or "").strip()
    if not url or url.lower() in ("0", "false", "no", "off"):
        return {"ok": False, "skipped": "disabled"}
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    timeout = (
        EXIT_TIMEOUT_SEC
        if str(body.get("event") or "").strip().lower() == "exit"
        else VISIT_TIMEOUT_SEC
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _clean_desc(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:].strip()
    # Prose then a fenced visit JSON — keep the sentence, drop the blob.
    for sep in ("```json", "```"):
        i = t.find(sep)
        if i > 0:
            t = t[:i].strip()
            break
    # one/two sentences, no JSON blob
    if t.startswith("{") and "appearance" in t[:80]:
        try:
            obj = json.loads(t)
            t = str(obj.get("appearance") or obj.get("note") or obj.get("text") or t)
        except Exception:
            pass
    t = " ".join(t.split())
    return t[:400]


def post_omni_note(text: str, *, pack_id: str = "") -> dict[str, Any]:
    rec = {
        "text": _clean_desc(text),
        "via": "argus",
        "person_id": "argus",
        "pack_id": pack_id,
        "stamp": {"person_id": "argus", "source": "argus_describe", "certain": False},
    }
    if not rec["text"]:
        return {"ok": False, "error": "empty"}
    data = json.dumps(rec).encode()
    req = urllib.request.Request(
        OMNI_NOTE_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=4.0) as r:
        return json.loads(r.read().decode())


def _describe_content(
    manifest: dict[str, Any], jpegs: list[Path], prompt: str
) -> Any:
    mid = manifest.get("id") or ""
    trig = manifest.get("trigger_ts")
    try:
        from .oracle_event import event_id_from_pack, event_id_from_ts

        eid = event_id_from_pack(mid) or event_id_from_ts(trig)
    except Exception:
        eid = str(mid)
    parts: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                prompt
                + f"\npack_id={mid}\nevent_id={eid}\n"
                + (f"video_start_ts={trig}\n" if trig else "")
                + "Frames are time-ordered from that start. Do not name anyone.\n"
            ),
        }
    ]
    for p in jpegs:
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        if len(raw) < 80:
            continue
        b64 = base64.b64encode(raw).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )
    if len(parts) == 1:
        # no stills — still ask; Argus can open pack_dir
        parts[0]["text"] += f"pack_dir={manifest.get('pack_dir')}\nLook at the JPEGs there.\n"
        return parts[0]["text"]
    return parts


def notify_pack_async(manifest: dict[str, Any]) -> None:
    """Dead. Door Argus describe is not identity.

    the User 2026-08-27: FaceID only for ID. the agent at the Connect address
    stays for ad-hoc camera / video asks. Do not POST door packs to Glimmer.
    """
    return
    if os.environ.get("RAIL_ARGUS_PACK_NOTIFY", "0") in ("0", "false", "no"):
        return
    if not pack_worth_describe(manifest):
        return
    kind = _kind(manifest)

    def _run() -> None:
        try:
            pack_dir = manifest.get("pack_dir") or ""
            mid = manifest.get("id") or ""
            slot = manifest.get("slot_key") or ""
            if kind not in LEAVE_KINDS:
                prompt = ENTER_PROMPT if kind == "enter" else motion_prompt()
                jpegs = pick_pack_jpegs(manifest)
                # Video first. Stills spread across a whole pack cannot answer
                # a direction question: measured on ev_1786807037052, a real
                # entry, the pack ran 76.7 s and the crossing was 0.2 s of it,
                # so none of the 5 stills contained a moving frame and Argus
                # correctly reported no crossing. The same event as 14 raw
                # frames of the actual motion came back "enter".
                vid = describe_by_video(manifest, prompt)
                via = "stills"
                out = None
                if vid and vid.get("ok"):
                    out = vid["out"]
                    via = "video"
                if out is None:
                    out = _post_argus(_describe_content(manifest, jpegs, prompt))
                msg = _reply_text(out)
                desc = _clean_desc(msg)
                visit = parse_argus_visit(msg) or parse_argus_visit(desc)
                faceid = _faceid_from_pack(pack_dir) if pack_dir else {}
                applied: dict[str, Any] = {}
                # The fan-out is authoritative when it is applying (the User
                # 2026-08-16). It fires off the camera trigger seconds before a
                # pack exists, keyed on the trigger seq, so the pack path would
                # be a SECOND writer answering the same question on a different
                # key — and the two keys do not match, which means claim_crossing
                # could not dedupe between them. Exactly one writer counts.
                # The pack still gets described and persisted; it just does not
                # move the room — and must not post omni either (harvest was a
                # 10-minute second mouth on the same words).
                fanout_owns = False
                try:
                    from .door_fanout import APPLY as _FANOUT_APPLY
                    from .door_fanout import ENABLED as _FANOUT_ON

                    fanout_owns = bool(_FANOUT_ON and _FANOUT_APPLY)
                except Exception:
                    fanout_owns = False
                omni = {}
                if (
                    desc
                    and not fanout_owns
                    and os.environ.get("RAIL_ARGUS_OMNI", "1")
                    not in ("0", "false", "no")
                ):
                    try:
                        omni = post_omni_note(desc, pack_id=str(mid))
                    except Exception as e:
                        omni = {"ok": False, "error": f"{type(e).__name__}: {e}"[:120]}
                elif fanout_owns:
                    omni = {"ok": True, "skipped": "fanout_authoritative"}
                if visit and fanout_owns:
                    applied = {
                        "ok": True,
                        "applied": False,
                        "reason": "fanout_authoritative",
                    }
                elif visit and os.environ.get("RAIL_ORACLE_VISIT_APPLY", "1") not in (
                    "0",
                    "false",
                    "no",
                ):
                    try:
                        from .oracle_event import (
                            event_id_from_pack,
                            event_id_from_ts,
                            get_event,
                            mint_scratch,
                            open_event,
                            set_onset,
                            stamp_argus,
                        )

                        eid = (
                            event_id_from_pack(mid)
                            or event_id_from_ts(manifest.get("trigger_ts"))
                            or str(mid)
                        )
                        # Whoever describes the crossing first owns it. A second
                        # pack over the same motion still gets described and
                        # persisted — its words may be better — it just may not
                        # move the count a second time.
                        raw_onset = ((vid or {}).get("onset") or {}).get("seq")
                        claim = claim_crossing(raw_onset, eid)
                        if not claim.get("ok"):
                            raise _DuplicateCrossing(
                                f"already applied by {claim.get('owner')} "
                                f"at onset seq {claim.get('onset_seq')}"
                            )
                        if get_event(eid) is None:
                            open_event(
                                float(manifest.get("trigger_ts") or 0),
                                pack_id=eid,
                            )
                        # door_capture normally sets this in-process. It will not
                        # have if the pack outlived the rail that wrote it (a
                        # restart, or a replay) — the manifest is the durable copy.
                        set_onset(
                            eid,
                            seq=manifest.get("onset_seq"),
                            ts=manifest.get("onset_ts"),
                        )
                        stamped = stamp_argus(
                            eid,
                            event=visit.get("event"),
                            clothes=visit.get("clothes"),
                            hair=visit.get("hair"),
                            sex=visit.get("sex"),
                            note=desc,
                            pack_id=str(mid),
                        )
                        row = (stamped or {}).get("row") or get_event(eid) or {}
                        ev_kind = str(visit.get("event") or "").strip().lower()
                        if ev_kind == "exit":
                            # Backs fail FaceID. Who-left is robe/hair/sex on
                            # the enter card (apply_visit). Argus `who` is a
                            # clothing guess, not a face stamp.
                            who = str(visit.get("who") or "").strip().lower()
                            face = str(
                                (faceid or {}).get("name")
                                or (row.get("faceid") or {}).get("name")
                                or ""
                            ).strip().lower()
                            pid = who if who and who != "unknown" and who != face else ""
                        else:
                            pid = (
                                str(
                                    (row.get("faceid") or {}).get("name")
                                    or row.get("person_id")
                                    or faceid.get("name")
                                    or slot
                                    or ""
                                )
                                .strip()
                                .lower()
                            )
                            if not pid or pid == "unknown":
                                scratch = mint_scratch(eid)
                                pid = str(
                                    ((scratch or {}).get("row") or {}).get("person_id")
                                    or ""
                                )
                        applied = post_oracle_visit(
                            {
                                "event": visit["event"],
                                "person_id": pid,
                                "event_id": eid,
                                "pack_id": str(mid),
                                "clothes": visit.get("clothes"),
                                "hair": visit.get("hair"),
                                "sex": visit.get("sex"),
                                "faceid": faceid or None,
                                "note": desc,
                            }
                        )
                        applied["event_id"] = eid
                        if not stamped.get("ok"):
                            applied["stamp_reason"] = stamped.get("reason")
                    except _DuplicateCrossing as e:
                        # Not an error: the room was already told about this
                        # person walking in. Recorded so the second pack still
                        # shows why it did not count.
                        applied = {
                            "ok": True,
                            "applied": False,
                            "reason": "duplicate_crossing",
                            "detail": str(e)[:160],
                        }
                    except Exception as e:
                        applied = {
                            "ok": False,
                            "error": f"{type(e).__name__}: {e}"[:160],
                        }
                append_audit(
                    {
                        "ts": time.time(),
                        "pack": str(mid),
                        "faceid_id": (faceid or {}).get("name"),
                        "argus_kind": (visit or {}).get("event"),
                        "applied": bool(applied.get("applied")),
                        "reason": applied.get("reason") or applied.get("error"),
                        "headcount_after": applied.get("headcount_after"),
                    }
                )
                try:
                    pd = manifest.get("pack_dir")
                    # A pack that got no usable answer still has to say so.
                    # Writing argus.json only when desc is non-empty meant every
                    # failure erased its own evidence: on 2026-08-15 two door_v2
                    # enters logged reply_len=0 and left nothing on disk, so why
                    # the video path declined and fell back to stills could not
                    # be recovered afterwards.
                    if pd and not desc:
                        try:
                            Path(str(pd), "argus.json").write_text(
                                json.dumps({
                                    "schema": "argus_describe/v1",
                                    "pack": str(mid),
                                    "kind": kind or "motion",
                                    "description": "",
                                    "empty_reply": True,
                                    "via": via,
                                    "reply_len": len(msg or ""),
                                    "n_jpegs": len(jpegs),
                                    "video": {
                                        "n_frames": (vid or {}).get("n_frames"),
                                        "onset": (vid or {}).get("onset"),
                                        "error": (vid or {}).get("error"),
                                    } if vid else None,
                                    "post_error": (out or {}).get("error"),
                                    "ts": time.time(),
                                    "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
                                }, indent=1) + "\n",
                                encoding="utf-8",
                            )
                        except OSError:
                            pass
                    if pd and desc:
                        Path(str(pd), "argus.json").write_text(
                            json.dumps({
                                "schema": "argus_describe/v1",
                                "pack": str(mid),
                                "kind": kind or "motion",
                                "description": desc,
                                "visit": visit,
                                "applied": applied,
                                "n_jpegs": len(jpegs),
                                "jpegs": [p.name for p in jpegs],
                                # Which evidence actually produced this answer.
                                # Without it a reviewer cannot tell a verdict
                                # read off motion from one read off stills.
                                "via": via,
                                "video": {
                                    "n_frames": (vid or {}).get("n_frames"),
                                    "onset": (vid or {}).get("onset"),
                                    "error": (vid or {}).get("error"),
                                } if vid else None,
                                "ts": time.time(),
                                "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
                            }, indent=1) + "\n",
                            encoding="utf-8",
                        )
                        _write_sent_sheet(jpegs, Path(str(pd)) / "argus_sent.jpg")
                except OSError:
                    pass
                print(
                    f"[argus_notify] pack={mid} kind={kind or 'motion'} "
                    f"jpegs={len(jpegs)} reply_len={len(msg)} "
                    f"omni={omni.get('ok')} err={out.get('error')}",
                    flush=True,
                )
                try:
                    HOME.mkdir(parents=True, exist_ok=True)
                    (HOME / "LAST-PACK-REPLY.md").write_text(
                        f"# {mid} {kind or 'motion'} "
                        f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\n\n"
                        f"{desc or msg[:4000]}\n",
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                return

            prompt = (
                "Door pack ready (leave/exit candidate).\n"
                f"pack_id={mid}\npack_dir={pack_dir}\nslot_key={slot}\n"
                "If this is a true door-plane leave for the sole AUTH User "
                "(the user), write one line to "
                f"{LEAVE_NOTIFY} with ISO time + confidence, and reply with "
                "that line. Do not fire on approach-return alone.\n"
            )
            out = _post_argus(prompt)
            msg = _reply_text(out)
            print(
                f"[argus_notify] pack={mid} kind={kind} "
                f"reply_len={len(msg)} err={out.get('error')}",
                flush=True,
            )
            try:
                HOME.mkdir(parents=True, exist_ok=True)
                (HOME / "LAST-PACK-REPLY.md").write_text(
                    f"# {mid} {kind} {time.strftime('%Y-%m-%dT%H:%M:%S')}\n\n"
                    f"{msg[:4000]}\n",
                    encoding="utf-8",
                )
            except Exception:
                pass
        except Exception as e:
            print(f"[argus_notify] fail: {type(e).__name__}: {e}"[:200], flush=True)

    threading.Thread(target=_run, name="argus-pack-notify", daemon=True).start()


def _load_pack_manifest(pack_dir: Path) -> dict[str, Any]:
    for name in ("manifest.json", f"{pack_dir.name}.json"):
        p = pack_dir / name
        if not p.is_file():
            continue
        try:
            man = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(man, dict):
            return man
    return {}


def _persist_pack_manifest(pack_dir: Path, man: dict[str, Any]) -> None:
    payload = json.dumps(man, indent=2)
    (pack_dir / "manifest.json").write_text(payload, encoding="utf-8")
    eid = str(man.get("id") or pack_dir.name)
    (pack_dir / f"{eid}.json").write_text(payload, encoding="utf-8")


def consider_pack(
    pack_dir: str | Path,
    *,
    engine: Any = None,
    enrolled: dict | None = None,
    stamps: dict | None = None,
    notify: bool = True,
    write_face: bool = True,
    max_scan: int = 48,
) -> dict[str, Any]:
    """FaceID crop + Argus describe for a pack. No SoT write.

    Used by harvest (POST /v1/pack_consider) so kind=None still/still_empty
    clips get a look before they are binned. Face scan is capped (max_scan)
    because CLIP_KEEP_ALL packs can be hundreds of frames.
    """
    root = Path(pack_dir)
    out: dict[str, Any] = {
        "ok": False,
        "pack": root.name,
        "described": False,
        "face": False,
    }
    if not root.is_dir():
        out["error"] = "no_pack"
        return out
    man = _load_pack_manifest(root)
    man["pack_dir"] = str(root)
    if not man.get("id"):
        man["id"] = root.name
    if not man.get("n"):
        man["n"] = len(list(root.glob("f*.jpg")))
    if not pack_worth_describe(man):
        out["ok"] = True
        out["skip"] = "floor"
        return out
    if man.get("argus_queued") and notify:
        out["ok"] = True
        out["skip"] = "already_queued"
        out["described"] = True
        out["kind"] = man.get("kind")
        return out
    if _kind(man) not in CROSSING_KINDS and _kind(man) in ("", "none"):
        man["kind"] = "motion"
        man["kind_source"] = "argus_gate"
    # Identity is FaceID. Never queue Argus describe from harvest/consider.
    notify = False
    if write_face and not (root / "entry_best.json").is_file():
        try:
            from .entry_face import best_face_in_pack, write_entry_best

            face_eng = engine.face if engine is not None and hasattr(engine, "face") else engine
            v = best_face_in_pack(
                root,
                engine=face_eng,
                enrolled=enrolled,
                stamps=stamps,
                max_scan=max_scan,
            )
            # Second look, from raw, in the window where the face actually is.
            # The pack glob walks a 300-frame harvest pack at STRIDE and can
            # step straight over the ~10 frames where someone is square to the
            # camera. Measured on the User's 13:19 lap: the pack scan found NO face,
            # while the raw window found him at seq 107584 — the frame he
            # picked by eye — 73x114 px, sim 0.746.
            if not v:
                try:
                    from .raw_clip import best_face_window, first_motion

                    trig = float(man.get("trigger_ts") or 0)
                    onset = first_motion(trig - 8, trig + 90) if trig else None
                    win = best_face_window(float(onset["ts"])) if onset else []
                    if win:
                        v = best_face_in_pack(
                            root,
                            engine=face_eng,
                            enrolled=enrolled,
                            stamps=stamps,
                            frames=win,
                        )
                        if v:
                            out["face_via"] = "raw_window"
                except Exception as e:
                    out["face_window_error"] = f"{type(e).__name__}: {e}"[:120]
            if v:
                write_entry_best(root, v)
                out["face"] = True
                out["face_name"] = v.get("name")
                out["face_sim"] = v.get("sim")
            else:
                out["face_skip"] = "no_usable_face"
        except Exception as e:
            out["face_error"] = f"{type(e).__name__}: {e}"[:160]
    if notify:
        notify_pack_async(man)
        man["argus_queued"] = True
        out["described"] = True
    try:
        _persist_pack_manifest(root, man)
    except Exception:
        pass
    out["ok"] = True
    out["kind"] = man.get("kind")
    return out
