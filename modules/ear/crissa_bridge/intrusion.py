"""Intrusion attempt — evidence, not an argument.

the User 2026-08-14:

    If stranger asks for injection, check face, record face as intruder and
    store with a report as a security incident - intrusion attempt. Face shot,
    voice clip and polite conversation is all they get.

Trigger is deliberately narrow (the User's call, 2026-08-14): **an unknown voice
asking for something privileged**. Not a known regular who happens to be
unauthenticated, not the User before his face is re-proved, and not a stranger
asking the time. A report should mean something when you find one.

    stranger + house command      -> INCIDENT
    stranger + "what time is it"  -> nothing
    regular  + house command      -> refuse politely, file nothing
    User unauthed + command       -> hunt the face, file nothing

What lands on disk, under <crissa home>/ear/incidents/:

    incident_<ms>/report.json     what was asked, by whom-we-could-tell, when
    incident_<ms>/face.jpg        Argus's face crop, or a frame if none is recent
    incident_<ms>/raw/            the rolling window, frozen (never pruned)
    incident_<ms>/voice.wav       cut from the ear's rolling audio cache

The freeze path already existed (``rail_presence.security_raw.freeze_incident``)
and had **never fired** — measured 2026-08-14, ``incidents/`` was empty. This is
its first real caller.

**Voice clip** comes from ``crissa_ear.voice_cache`` — a rolling ~3 min ring in
the ear (the User 2026-08-14). The attempt is only recognised as one *after* decode,
so the clip is cut retroactively. Still best-effort: if the ring is cold or the
ear is down, the report records ``voice_clip: null`` rather than a path we did
not write.

Never speaks. Never explains. The caller stays polite — the whole point is that
the person on the other end learns nothing from the refusal.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .home import EAR, HOME

# A camera service with /v1/security/freeze ("" = none: no footage is frozen, the record still lands)
CAMERA = os.environ.get("RAIL_CAMERA_URL", "").rstrip("/")
if os.environ.get("CRISSA_NO_CAMERA", "").strip().lower() in ("1", "true", "yes", "on"):
    CAMERA = ""        # --no-camera (2026-10-10): the incident takes no snapshot and freezes nothing - no camera named
# Door event packs: the door feed writes entry_best_face.jpg here on each entry — a real
# face-sized crop, which is far better evidence than whatever the camera
# happened to be pointing at when someone spoke.
EVENTS_DIR = Path(os.environ.get("RAIL_EVENTS_DIR", str(EAR / "door-events")))
# How stale an entry crop may be and still describe the person now speaking.
CROP_MAX_AGE_SEC = float(os.environ.get("RAIL_INTRUSION_CROP_AGE", "900"))
from .settings import url as _port_url

EAR_URL = os.environ.get("RAIL_STT_URL", _port_url("stt")).rstrip("/")
# the face check lives in the presence process; the X# stamp goes there.
PRESENCE = os.environ.get("RAIL_PRESENCE_URL", _port_url("presence")).rstrip("/")
# the User 2026-08-14: "stash in what we create under the Inspector filetree." A pack keeps incidents
# under its state home, beside the identity records they relate to.
INCIDENTS = Path(os.environ.get("RAIL_SECURITY_INCIDENTS") or (EAR / "incidents"))
# Off switch. Present so a site can disable evidence capture without editing code.
ENABLED = os.environ.get("RAIL_INTRUSION_REPORT", "1") not in ("0", "false", "no")
TIMEOUT = float(os.environ.get("RAIL_INTRUSION_TIMEOUT", "3.0"))
# How much of the ear's rolling cache to keep with an incident.
CLIP_SEC = float(os.environ.get("RAIL_INTRUSION_CLIP_SEC", "60"))

_SELF = frozenset({"crissa", "crissa_her_tts", "announcer", "crew_tts"})
_ENROLLED: set[str] | None = None


def _enrolled_ids() -> set[str]:
    """People on the identity tree. Named regulars are never strangers.

    Live presence can drop a household name (mill off, empty SoT). Filing
    Second as an intrusion because she was not in `people[]` that second
    is how the archive filled with 128 of her command refusals.
    """
    global _ENROLLED
    if _ENROLLED is not None:
        return _ENROLLED
    root = Path(os.environ.get("CRISSA_IDENTITY") or os.environ.get("HOUSE_ID_ROOT") or str(HOME / "identity")) / "people"
    ids: set[str] = set()
    try:
        for p in root.iterdir():
            name = p.name.strip().lower()
            if p.is_dir() and name and not name.startswith(("_", ".")):
                ids.add(name)
    except OSError:
        pass
    _ENROLLED = ids
    return ids


def _known_ids(presence: dict | None) -> set[str]:
    """Everyone this house recognises right now: User, regulars, today's door.

    `visitor_today` is deliberately included. Someone the door saw today is a
    guest, not an intruder — they get refused, not filed.
    """
    p = presence if isinstance(presence, dict) else {}
    known: set[str] = set()
    uid = str(p.get("user_id") or "").strip().lower()
    if uid:
        known.add(uid)
    for key in ("visitor_today", "regulars", "known_ids"):
        v = p.get(key)
        if isinstance(v, dict):
            v = list(v.keys())
        if isinstance(v, list):
            for x in v:
                if isinstance(x, dict):
                    x = x.get("person_id") or x.get("name")
                s = str(x or "").strip().lower()
                if s:
                    known.add(s)
    for row in p.get("people") or []:
        if isinstance(row, dict):
            s = str(row.get("name") or row.get("person_id") or "").strip().lower()
            if s and not s.startswith(("unknown", "stranger", "crowd")):
                known.add(s)
    return known


def is_stranger(meta: dict | None, presence: dict | None) -> bool:
    """True only when we cannot place this voice at all.

    Unknown means unknown: no id, an opaque/scratch id, or a name this house
    does not recognise. A weak-but-named match is NOT a stranger — filing a
    report on a bad match is how a regular ends up in the incident folder.
    """
    m = meta if isinstance(meta, dict) else {}
    pid = str(m.get("person_id") or "").strip().lower()
    if pid in _SELF:
        return False  # our own mouth is never an intruder
    known = _known_ids(presence) | _enrolled_ids()
    if pid and pid in known:
        return False
    stamp = m.get("stamp") if isinstance(m.get("stamp"), dict) else {}
    sid = str(stamp.get("person_id") or "").strip().lower()
    if sid and sid in known:
        return False
    # A scratch slot (s1/s2) is an unplaced voice by definition.
    return True


def recent_entry_crop(max_age_sec: float = CROP_MAX_AGE_SEC) -> Path | None:
    """Newest Argus-selected face crop, if one is recent enough to be this person.

    Read-only, and deliberately NOT a camera call: `/snapshot` and this both
    stay out of `camera_call.MUTATE_PATHS`, so filing an incident never moves
    the lens or fights the door-watch for a lease.
    """
    try:
        now = time.time()
        best: tuple[float, Path] | None = None
        for d in EVENTS_DIR.glob("ev_*"):
            f = d / "entry_best_face.jpg"
            if not f.is_file():
                continue
            age = now - f.stat().st_mtime
            if age > max_age_sec:
                continue
            if best is None or f.stat().st_mtime > best[0]:
                best = (f.stat().st_mtime, f)
        return best[1] if best else None
    except OSError:
        return None


def _get(url: str, timeout: float = TIMEOUT) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if 200 <= getattr(r, "status", 200) < 300:
                return r.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return None


def _post(url: str, payload: dict, timeout: float = TIMEOUT) -> dict | None:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def report_intrusion(
    *,
    text: str,
    cmd: str | None = None,
    meta: dict | None = None,
    presence: dict | None = None,
    light: bool = False,
    strikes: int | None = None,
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict[str, Any]:
    """File an intrusion attempt. Best-effort: never raises into the caller.

    ``light`` is the two-refusals snap (the User 2026-08-31): a picture and a
    report, no hour-freeze and no voice clip. That matters for more than tone —
    the freeze copies the whole rolling window into incidents/ and incidents
    are NEVER auto-pruned. Firing that for any household member who asked twice
    would fill the disk; the raw tree already reached 130 GB once. The stranger
    path keeps the full bundle.

    The refusal must land whether or not evidence capture works, so every step
    here is independently guarded and the result records what actually happened
    rather than what was intended.
    """
    out: dict[str, Any] = {"filed": False}
    if not ENABLED:
        return {"filed": False, "reason": "disabled"}
    # Never write a live incident from a test run. This became load-bearing the
    # moment the stranger gate came off (2026-08-31): before, a test speaker was
    # a "known_speaker" and filed nothing, so denial-path tests were silent.
    # With the gate gone they file for real — six incidents with real camera
    # snapshots landed in the live incidents folder during one suite run before
    # this guard. stranger_x.record_event has the same check for the same reason.
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get(
        "RAIL_INTRUSION_ROOT"
    ):
        return {"filed": False, "reason": "pytest"}
    try:
        ts = time.time()
        iid = f"incident_{int(ts * 1000)}"
        dest = INCIDENTS / iid
        dest.mkdir(parents=True, exist_ok=True)

        # 1. Face. Prefer Argus's chosen crop from a recent entry — a padded
        #    the face check-sized face beats a hallway-wide frame as evidence of who
        #    tried this. Fall back to a plain snapshot when there is no recent
        #    entry (they may have been in the room a while).
        face_path = None
        face_source = None
        crop = recent_entry_crop()
        if crop is not None:
            try:
                face_path = dest / "face.jpg"
                face_path.write_bytes(crop.read_bytes())
                face_source = f"argus_entry_crop:{crop.parent.name}"
            except OSError:
                face_path = None
        if face_path is None:
            jpg = _get(f"{CAMERA}/snapshot") if CAMERA else None
            if jpg and len(jpg) > 512:
                face_path = dest / "face.jpg"
                face_path.write_bytes(jpg)
                face_source = "camera_snapshot"

        # 2. Stamp the face into its X# folder — SAME PATH AS DOOR MOTION.
        #    door_fanout records a body at the door with record_event(kind=
        #    "enter"); this is the same call with kind="speech", so whoever
        #    asked twice is grouped against the face that walked in rather than
        #    filed somewhere new. It goes over HTTP because record_event embeds
        #    with the face check, and the face check lives in the presence process.
        stranger = None
        if face_path is not None:
            try:
                import base64

                stranger = _post(
                    f"{PRESENCE}/v1/strangers/record",
                    {
                        "kind": "speech",
                        "source": "rail-voice.intrusion",
                        "jpeg_b64": base64.b64encode(
                            face_path.read_bytes()
                        ).decode("ascii"),
                    },
                )
            except Exception as e:
                stranger = {"ok": False, "error": f"{type(e).__name__}: {e}"[:120]}

        # 3a. Attempt events cut EXACTLY their own span out of the 1-hour
        #     cache: entry -> 2nd try, then 2nd -> 3rd, and so on. No 15-minute
        #     floor and no debounce, because these are contiguous and cannot
        #     overlap — see the t_start note in security_raw.freeze_incident.
        freeze = None
        clip_path = None
        if light and t_start is not None and CAMERA:
            freeze = _post(
                f"{CAMERA}/v1/security/freeze",
                {
                    "reason": "attempt_event",
                    "note": (text or "")[:200],
                    "source": "rail-voice.intrusion",
                    "t_start": float(t_start),
                    "t_end": float(t_end if t_end is not None else time.time()),
                },
            )

        # 3b. Full bundle — freeze the whole window plus a voice clip. Stranger
        #     path only; an attempt event has its exact span from 3a already.
        if not light:
            # Freeze the rolling window so prune cannot take the evidence.
            freeze = _post(
                f"{CAMERA}/v1/security/freeze",
                {"reason": "intrusion_attempt", "note": (text or "")[:200],
                 "source": "rail-voice.intrusion"},
            ) if CAMERA else None
            # Voice clip — best effort; the ear may not retain audio.
            clip = _post(
                f"{EAR_URL}/v1/security/voice_clip",
                {
                    "reason": "intrusion_attempt",
                    "dest": str(dest / "voice.wav"),
                    "seconds": CLIP_SEC,
                },
            )
            if isinstance(clip, dict) and clip.get("ok") and (dest / "voice.wav").is_file():
                clip_path = str(dest / "voice.wav")

        # SecurityTag: the stamped X# and when, in one readable token, so a row
        # in a log can name the incident and the folder can be found by eye.
        xid = None
        if isinstance(stranger, dict) and stranger.get("ok"):
            xid = stranger.get("xid") or stranger.get("person_id")
        stamp_ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))
        tag = f"SEC-{(xid or 'unknown').upper()}-{stamp_ts}"

        # File under the security name BEFORE writing the report, so the paths
        # recorded inside it are the paths that will still exist. Renaming
        # afterwards would leave report.json pointing at a directory that is
        # no longer there. Best effort: a failed rename leaves the incident
        # where it is rather than losing it.
        try:
            named = dest.parent / f"{tag}_{iid}"
            if not named.exists():
                dest.rename(named)
                if face_path is not None:
                    face_path = named / face_path.name
                if clip_path:
                    clip_path = str(named / Path(clip_path).name)
                dest = named
        except OSError:
            pass

        # Her own account of the room, first person, signed. Short-fused and
        # optional: None rather than a guess.
        observation = observe_room()

        m = meta if isinstance(meta, dict) else {}
        stamp = m.get("stamp") if isinstance(m.get("stamp"), dict) else {}
        report = {
            "schema": "security_incident/v1",
            "type": "intrusion_attempt",
            "ts": ts,
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)),
            "asked": (text or "")[:2000],
            "command": cmd,
            # What we could tell about the speaker — claims, not conclusions.
            "speaker": {
                "person_id": m.get("person_id"),
                "stamp_person_id": stamp.get("person_id"),
                "id_level": stamp.get("id_status") or stamp.get("id_level"),
                "certain": stamp.get("certain"),
                "voice_sim": stamp.get("voice_emb_sim"),
                "beam_angle": stamp.get("beam_angle"),
                "duration_s": stamp.get("speech_duration_s"),
            },
            "evidence": {
                "face": str(face_path) if face_path else None,
                "face_source": face_source,
                "voice_clip": clip_path,
                # freeze_incident returns {"id","path",...}. This read
                # "incident"/"dir", which it has never returned, so the link
                # from a record to its footage was always null and a dismissal
                # could not have found the frames to purge.
                "raw_freeze": (freeze or {}).get("path") or (freeze or {}).get("id"),
                "freeze_ok": bool(freeze),
            },
            # Stamped ID: whoever asked, grouped by the face check into the same X#
            # folder the door uses. Not an enrollment, not AUTH, not a count.
            "stranger_id": xid,
            "stranger_sim": (stranger or {}).get("sim"),
            "security_tag": tag,
            "strikes": strikes,
            "observation": observation,
            "observed_by": "crissa" if observation else None,
            "span": (
                {"t0": t_start, "t1": t_end,
                 "sec": round(float(t_end) - float(t_start), 1)}
                if (t_start is not None and t_end is not None) else None
            ),
            "note": (
                "Asked twice for something gated and was refused politely both "
                "times, with no explanation, per house law. The picture records "
                "that it happened. This is not an enrollment, not an "
                "identification, and must never be promoted into an identity "
                "bank."
                if light else
                "Unknown voice asked for something privileged. Refused politely "
                "with no explanation, per house law. Face/voice retained as "
                "evidence of the attempt — this is not an enrollment and must "
                "never be promoted into an identity bank."
            ),
        }
        (dest / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        # The Crissa task: timestamp, the picture, and the one command that
        # clears it. Threaded — the refusal must not wait on a subprocess.
        file_board_card(report)
        out = {"filed": True, "incident": str(dest), "face": bool(face_path),
               "security_tag": tag, "stranger_id": xid, "strikes": strikes,
               "voice_clip": bool(clip_path), "freeze": bool(freeze)}
    except Exception as e:  # never let evidence capture break the refusal
        out = {"filed": False, "error": f"{type(e).__name__}: {e}"[:200]}
    return out


# Two polite refusals, then a snap (the User 2026-08-31, verbatim):
#
#     "Anyone who tried two times to get Crissa to do something she cannot do.
#      Even questions that she says no, just a picture. Two i think is fine.
#      No arrests or anything just that someone tried twice to get Sudo."
#
# THE STRANGER TEST IS GONE. It used to be the whole gate — a known regular
# could ask for anything, any number of times, and nothing was filed. The rule
# is now about the ASKING, not about who is asking, so this fires for anyone
# including the User. Strangers at the door still get their own S# identity
# file; that is stranger_x/enrollments and is untouched by this.
#
# One refusal is a person misunderstanding what she does. Two is someone
# finding out. Only the second one is worth a picture.
_STRIKES: dict[str, list[float]] = {}
# Where each speaker's next span starts: the door entry for the first event,
# then the previous attempt for every one after it.
_MARKERS: dict[str, float] = {}
try:
    STRIKE_WINDOW_SEC = float(os.environ.get("RAIL_INTRUSION_WINDOW_SEC", "900"))
except (TypeError, ValueError):
    STRIKE_WINDOW_SEC = 900.0
try:
    STRIKES_TO_FILE = max(1, int(os.environ.get("RAIL_INTRUSION_STRIKES", "2")))
except (TypeError, ValueError):
    STRIKES_TO_FILE = 2


def _speaker_key(meta: dict | None, presence: dict | None) -> str:
    """Who is being counted. Falls back to one shared bucket when nobody is
    named, which is the safe direction: an unidentified asker still reaches two.
    """
    m = meta if isinstance(meta, dict) else {}
    for k in ("person_id", "pid", "speaker", "name"):
        v = m.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    stamp = m.get("stamp") if isinstance(m.get("stamp"), dict) else {}
    v = stamp.get("person_id")
    if isinstance(v, str) and v.strip():
        return v.strip().lower()
    return "_unnamed"


def strike_state() -> dict[str, Any]:
    """For the Control Panel / tests. Never mutates."""
    now = time.time()
    return {
        "window_sec": STRIKE_WINDOW_SEC,
        "strikes_to_file": STRIKES_TO_FILE,
        "pending": {
            k: len([t for t in v if now - t < STRIKE_WINDOW_SEC])
            for k, v in _STRIKES.items()
            if any(now - t < STRIKE_WINDOW_SEC for t in v)
        },
    }


def observe_room() -> str | None:
    """Crissa's own words for what she can see, first person, at the moment it
    happened. the User 2026-08-31: "picture and note and even Crissa observe room
    tool for room description signed by Crissa."

    Best effort and short-fused. A refusal is a person waiting for an answer,
    so this never delays it and never raises. Returns None rather than a
    guess — an unsigned blank is honest, an invented room is not.
    """
    try:
        req = urllib.request.Request(
            f"{PRESENCE}/v1/observe",
            data=json.dumps({"reason": "attempt_event", "first_person": True}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2.5) as r:
            d = json.loads(r.read().decode() or "{}")
        t = (d.get("text") or d.get("observation") or "").strip()
        return t[:1200] or None
    except Exception:
        return None


def file_board_card(report: dict) -> dict:
    """One Crissa task per attempt event: timestamp, the picture, and the way out.

    the User 2026-08-31: "Final piece is a crissa task on the board with timestamp and
    pic." The review is meant to be one glance -- "Crissa checks with one face
    pic. I say no worries, gone." -- so the card carries the dismiss command
    rather than making anyone go hunting for it.

    The house filed it with its board's CLI. A pack has no board of its own: with EAR_BRIDGE_BOARD_URL
    set (a board that takes POST {title, body, assignee, priority, status, idempotency_key} - the krew
    plugin's card endpoint) the card is posted there; otherwise the card's text is written beside the
    incident (card.md) and the Wall's record is the way to it. Runs in a daemon thread: the refusal it
    belongs to is a person waiting for Crissa to answer. Filing must never sit in front of that.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"ok": False, "reason": "pytest"}

    def _go() -> None:
        tag = report.get("security_tag") or "SEC-UNKNOWN"
        span = report.get("span") or {}
        ev = report.get("evidence") or {}
        lines = [
            "Someone asked twice for something I cannot do, so the house took a",
            "picture. This is a record, not an accusation.",
            "",
            "- when: %s" % report.get("ts_iso"),
            "- who: %s (the face check grouping, the same X# the door uses)" % (
                report.get("stranger_id") or "unidentified"),
            "- attempt: #%s" % report.get("strikes"),
            "- asked: %r" % (str(report.get("asked") or "")[:300]),
            "- command: %s" % report.get("command"),
            "- picture: %s" % ev.get("face"),
        ]
        if span:
            lines.append("- footage: %ss (%s to %s)" % (
                span.get("sec"), span.get("t0"), span.get("t1")))
        obs = report.get("observation")
        if obs:
            lines += ["", "### What I saw", str(obs), "", "-- Crissa"]
        lines += [
            "",
            "### Clear it",
            "One look at the picture. If it is nothing:",
            "",
            "    python3 -m crissa_bridge.intrusion dismiss %s" % tag,
            "",
            "That removes the picture, the footage and this record. There is no",
            "second copy.",
        ]
        card = {"title": "Security: needs the User -- %s" % tag, "assignee": "crissa", "priority": 3,
                # BLOCKED, and not just as a label: an assigned card in todo gets picked up by a
                # dispatcher, and there is nothing for a worker to do with a security event. Only
                # the User can clear this, and blocked is exactly "waiting on a human".
                "status": "blocked",
                # One card per event, even if the filing is retried.
                "idempotency_key": "security-%s" % tag, "created_by": "crissa",
                "body": "\n".join(lines)[:3500]}
        board = os.environ.get("EAR_BRIDGE_BOARD_URL", "").strip()
        if board:
            try:
                _post(board, card)
            except Exception:  # noqa: BLE001
                pass
        try:
            d = INCIDENTS / str(report.get("security_tag") or tag)
            d.mkdir(parents=True, exist_ok=True)
            (d / "card.md").write_text("# %s\n\n%s\n" % (card["title"], card["body"]), encoding="utf-8")
        except OSError:
            pass

    try:
        import threading

        threading.Thread(target=_go, name="intrusion-card", daemon=True).start()
        return {"ok": True, "queued": True}
    except Exception as e:
        return {"ok": False, "error": ("%s: %s" % (type(e).__name__, e))[:120]}


def dismiss(tag_or_id: str, *, by: str = "user") -> dict:
    """"No worries, gone." Purge the VIDEO. Keep the picture and the log row.

    the User 2026-08-31: "purge on a dismissal by user to keep us lean" then "not
    the log just the video. Sorry keep pics on event log."

    The footage is the weight -- frozen frames are exempt from the pruner by
    design, and they do not live beside the record: freeze_incident writes
    under the camera service's own tree while this record is under
    <crissa home>/ear/incidents. So a dismissal has to follow the link into the
    other tree, or the frames sit there forever.

    The picture and report.json stay. They cost kilobytes, they are the event
    log, and an honest count needs to know this happened and was cleared.
    """
    key = (tag_or_id or "").strip()
    if not key:
        return {"ok": False, "reason": "need_tag"}
    hits = [d for d in INCIDENTS.glob("*") if d.is_dir() and key in d.name]
    if not hits:
        return {"ok": False, "reason": "not_found", "looked_in": str(INCIDENTS)}

    import shutil

    cleared, footage, freed = [], [], 0
    for d in hits:
        rp_path = d / "report.json"
        try:
            r = json.loads(rp_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            r = {}
        raw = ((r.get("evidence") or {}).get("raw_freeze")) or ""
        if raw:
            rp = Path(raw)
            if rp.is_dir() and "incident" in rp.name:
                try:
                    freed += sum(f.stat().st_size for f in rp.rglob("*") if f.is_file())
                except OSError:
                    pass
                try:
                    shutil.rmtree(rp)
                    footage.append(rp.name)
                except OSError:
                    pass
        # a local raw/ copy, if the full-bundle path made one
        local_raw = d / "raw"
        if local_raw.is_dir():
            try:
                freed += sum(f.stat().st_size for f in local_raw.rglob("*") if f.is_file())
                shutil.rmtree(local_raw)
                footage.append(str(local_raw))
            except OSError:
                pass
        r["dismissed"] = {
            "by": by,
            "ts": time.time(),
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "footage_purged": True,
        }
        try:
            rp_path.write_text(
                json.dumps(r, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except OSError:
            pass
        cleared.append(d.name)
    return {"ok": True, "dismissed": cleared, "footage_purged": footage,
            "freed_mb": round(freed / 1048576.0, 1), "n": len(cleared)}


def list_open() -> list:
    """What is still sitting there, newest first. One row per incident."""
    out = []
    for d in sorted(INCIDENTS.glob("*"), key=lambda q: q.name, reverse=True):
        if not d.is_dir():
            continue
        row = {"id": d.name, "face": (d / "face.jpg").is_file()}
        try:
            r = json.loads((d / "report.json").read_text(encoding="utf-8"))
            for k in ("ts_iso", "security_tag", "stranger_id", "strikes", "command"):
                row[k] = r.get(k)
            if r.get("dismissed"):
                # cleared by the User: still on the event log, no longer open
                continue
        except (OSError, ValueError):
            pass
        out.append(row)
    return out


def maybe_report(
    *,
    text: str,
    cmd: str | None,
    meta: dict | None,
    presence: dict | None,
) -> dict[str, Any]:
    """Call at a denial site. Files on the SECOND refusal, whoever asked."""
    now = time.time()
    key = _speaker_key(meta, presence)
    # prune every bucket so a long-running ear does not accumulate names
    for k in list(_STRIKES):
        kept = [t for t in _STRIKES[k] if now - t < STRIKE_WINDOW_SEC]
        if kept:
            _STRIKES[k] = kept
        else:
            del _STRIKES[k]
            _MARKERS.pop(k, None)
    hits = _STRIKES.setdefault(key, [])
    hits.append(now)
    if len(hits) < STRIKES_TO_FILE:
        # First try is a person misunderstanding what she does. Nothing filed,
        # but remember when it was — the first event's footage starts here.
        return {"filed": False, "reason": "first_refusal",
                "strikes": len(hits), "of": STRIKES_TO_FILE}
    # EVERY attempt from the second on is its own event, and the counter does
    # NOT reset (the User 2026-08-31): "3x keep it logging from the 2x to the 3x as
    # another event. Keep logging each next event and store from entry log to
    # then from the 1hr cache each time."
    #
    # So the span is [previous marker -> now]: entry to the 2nd try, then 2nd
    # to 3rd, then 3rd to 4th. Contiguous and non-overlapping, which is what
    # makes exact-span freezing safe on disk.
    marker = _MARKERS.get(key) or hits[0]
    _MARKERS[key] = now
    return report_intrusion(text=text, cmd=cmd, meta=meta, presence=presence,
                            light=True, strikes=len(hits),
                            t_start=marker, t_end=now)


def _main(argv: list[str]) -> int:
    """Tiny CLI so the card's "Clear it" line is a real command.

        python3 -m crissa_bridge.intrusion list
        python3 -m crissa_bridge.intrusion dismiss SEC-X3-20260831-153012
    """
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(_main.__doc__ or "")
        return 0
    cmd = argv[1]
    if cmd == "list":
        rows = list_open()
        if not rows:
            print("no open security events")
            return 0
        for r in rows:
            print("%-46s %s  %s  attempt #%s  %s" % (
                r.get("security_tag") or r.get("id"),
                r.get("ts_iso") or "?",
                r.get("stranger_id") or "unidentified",
                r.get("strikes"),
                "pic" if r.get("face") else "no pic",
            ))
        print("\n%d open" % len(rows))
        return 0
    if cmd == "dismiss":
        if len(argv) < 3:
            print("need a tag: dismiss SEC-...")
            return 2
        out = dismiss(argv[2])
        print(json.dumps(out, indent=1))
        return 0 if out.get("ok") else 1
    print("unknown command: %s" % cmd)
    return 2


if __name__ == "__main__":
    import sys as _sys

    raise SystemExit(_main(_sys.argv))
