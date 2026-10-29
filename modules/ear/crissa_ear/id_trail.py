"""id_trail: the ear under the kit's Security setting - a turn is sent first, its speaker is named after (round 17).

The User 2026-10-13: "lets put in an off toggle for face and voice ID on the chat ... What then could our time between
speech be with no embeds check in the way. It can trail fine for agent reference just not a dependency for ID", then
"we want default to be hybrid ID mode", and for the log: "just run live and post label speaker in history when
available maybe on the way up it gets labeled by voice". .101 the same morning: his words dropped as `no_emb`, 5-10 s
to get a line through, a house command held `await_voice` at user_voice_sim 0.478.

The setting is lib/crissa_home.security_mode() (<state home>/security.json): on | id_only (the default) | off.

  on        today's ear, unchanged: the voice print runs beside the decode (voice_live.kick at the impulse and at the
            chunk's close), the decode's result waits for it (up to 4 s), and the turn is stamped with who spoke.
  id_only   the voice print does NOT run beside the decode (on one core it shares the CPU the decode needs) and the
            turn never waits for it: the turn is published at once with no speaker (`person_id` None, stamp source
            `id_trailing`, the media gate's own verdict kept, an identity hold dropped), then ONE background worker
            runs the same recognition the stamp would have run (voice_live.embed_rank_publish - the embedding, the
            rank, her own voice's veto, the Wall's live speaker - then stamp.stamp_turn with it, which also reads
            presence's face) and labels the SAME turn: `probably` on the ring's row (a key the turn already carries,
            so the row is never re-keyed) and one row in <state home>/ear/speaker-labels.jsonl. The feed relabels the
            Wall's line in place from that file, and the bridge hands the label to her as a reference note on the
            next turn. A label that is not ready is simply not there.
  off       as id_only, and no recognition at all (no embedding, no worker): the CPU is the decode's.

speaker-labels.jsonl rows: {"ts" (the turn's ring ts), "text", "who" (null = no speaker yet), "via" (voice | face |
voice+face | her_voice | scratch), "sim", "at" (when written), "mode", "speech_end_ts"}. A null row is written when
the turn is published, the label row when recognition answers.

THE FACE NAMES A LINE AT ONCE (round 18, the User 2026-10-13: "names on the rows before they scroll off"; .101 round 17:
his voice was never recognised, `pid null` throughout, so the "?" rows never got a name). Before the voice print, the
worker reads presence's face block once (face_label): exactly one named person's face at the camera, matched at
LABEL_FACE_SIM or better, seen no earlier than LABEL_FACE_SLACK_S before the line began, names the line `via` face at
once (a few ms after the row, inside the Wall's first read). The voice print runs after it, as before, and a voice that
names someone wins (a later row); a voice no one knows (a scratch slot) does not take the face's name away. A label
only: nothing decides by it (the User's face before an action is id_gate's own, fresher rule). ID-only only; Off runs
no recognition. Kill: CRISSA_LOG_NAME_FALLBACK=0 (round 17's labels: the voice print and the stamp's own rules).

THE TRAIL LEARNS HIS VOICE (round 19, .101 2026-10-14: `pid` null on every heard row though Setup took 4 prints). In
ID-only the turn is sent with no voice print, so the ear's live promote (voice_bank.maybe_promote_live_print, on in a
pack since round 9, when Setup's prints alone left his laptop voice at 0.27-0.63 against the 0.50 floor) never ran: it
sat in the stamp path the trail skips, and his bank stayed at Setup's prints for good. The worker now offers each
recognition to the same promote, with the turn's own media verdict and every guard unchanged (the User only, the media
gate's clear human, the sim / confidence / margin bars, the 40-print cap), after the turn is sent and after its label
is written, so the decode and the barge never wait for it. Off runs no recognition, so it learns nothing.
Kill: CRISSA_ID_TRAIL_PROMOTE=0. What each turn's voice came to (named, below the floor, too short, no embedding, her
voice, a scratch slot) is counted for the ear's /health (`id_trail`, stats()) and said on stderr, one line a turn.

Only the conversation's chunks (`via` ear) trail; /v1/listen and /v1/stt keep their stamps. The house never trails
(security_mode is `on` there). Kill: CRISSA_SECURITY_DEFAULT_IDONLY=0 makes the default `on`; a saved choice stands.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any

from .home import EAR

LABELS_FILE = "speaker-labels.jsonl"
KEEP_BYTES = 256 * 1024
KEEP_ROWS = 300
QUEUE_MAX = 4                         # turns waiting for a label; a fifth is not labelled (said once on stderr)
# THE LABEL WORKER RUNS AT THE EAR'S OWN PRIORITY (measured 2026-10-13 on one core, checkpoint round17-id-off-20261013):
# at nice 10 the label came 1.2-2.4 s after its row, three times the embedding's own 0.27-0.8 s: WeSpeaker's ONNX
# session runs 2 intra-op threads that spin while they wait, its pool thread was made at nice 0, and on one core the
# spinning thread took the niced caller's turn. Her first audio from Pocket with a nice-0 print running beside it:
# p50 237 ms against 193 ms alone (the label normally lands inside the 2 s quiet before her mouth opens, so they rarely
# meet). CRISSA_ID_TRAIL_NICE raises it (0..19).
NICE = int(os.environ.get("CRISSA_ID_TRAIL_NICE", "0") or 0)
_Q: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"worker": None, "dropped": 0, "said_full": False}
_TL = threading.local()                       # recognise()'s stamp + embedding, for the promote (see THE TRAIL LEARNS)
_STATS: dict[str, Any] = {"turns": 0, "why": {}, "promoted": 0, "promote_skips": {}, "last": []}
STATS_LAST = 8


def promote_on() -> bool:
    return os.environ.get("CRISSA_ID_TRAIL_PROMOTE", "1").strip().lower() not in ("0", "false", "no", "off")


def stats() -> dict:
    """What the trailing recognition came to since the ear started (the ear's /health `id_trail`)."""
    with _LOCK:
        return {"mode": mode(), "promote": promote_on(), "turns": _STATS["turns"], "why": dict(_STATS["why"]),
                "promoted": _STATS["promoted"], "promote_skips": dict(_STATS["promote_skips"]),
                "dropped": int(_STATE["dropped"]), "last": list(_STATS["last"])}


def _why(got: dict, full: dict | None) -> str:
    """One word for what the voice came to on this turn."""
    via = str(got.get("via") or "")
    if got.get("who") and via in ("voice", "voice+face"):
        return "named"
    if via == "her_voice":
        return "her_voice"
    if via == "registration":
        return "registration"                      # round 36: an Add/Edit IDs run had the camera
    if via == "scratch":
        return "scratch"
    st = (full or {}).get("st") or {}
    if full is not None and full.get("emb") is None:
        return "no_emb"
    if st.get("voice_skip") == "short_clip":
        return "short_clip"
    if got.get("who"):
        return "named_" + (via or "other")       # the stamp's face / sole rules named it, not the voice
    if got.get("sim") is not None:
        return "below_floor"
    return "no_match"


def _note(got: dict, full: dict | None, face: dict | None, dur: float) -> None:
    why = _why(got, full)
    thr = None
    try:
        from .voice_bank import _canonical_user_id, match_sim_for
        thr = round(float(match_sim_for(_canonical_user_id())), 3)
    except Exception:  # noqa: BLE001
        pass
    row = {"at": round(time.time(), 3), "why": why, "sim": got.get("sim"), "user_floor": thr,
           "dur_s": round(dur, 2), "face": (face or {}).get("who")}
    with _LOCK:
        _STATS["turns"] += 1
        _STATS["why"][why] = int(_STATS["why"].get(why, 0)) + 1
        _STATS["last"] = (_STATS["last"] + [row])[-STATS_LAST:]
    import sys
    print("[ear] speaker label: voice %s (sim %s, the User's floor %s, %.1f s)%s" % (
        why, got.get("sim"), thr, dur, (", face %s" % face.get("who")) if face else ""), file=sys.stderr, flush=True)


def _promote(entry: dict, full: dict, text: str, dur: float, pcm: bytes, sr: int) -> None:
    """Offer the trailing recognition to the ear's live promote (see THE TRAIL LEARNS HIS VOICE). Never raises."""
    try:
        st = dict(full.get("st") or {})
        emb = full.get("emb")
        if not promote_on() or emb is None or not st.get("person_id") or not st.get("source"):
            return
        est = entry.get("stamp") if isinstance(entry.get("stamp"), dict) else {}
        if isinstance(est.get("media_gate"), dict):
            st["media_gate"] = est["media_gate"]     # the turn's own verdict: the gate is not fed the same turn twice
        from .voice_bank import maybe_promote_live_print
        got = maybe_promote_live_print(person_id=str(st["person_id"]), emb=emb, stamp=st, text=text,
                                       speech_duration_s=dur, pcm=pcm, sr=sr)
        if got is None:
            return
        with _LOCK:
            if got.get("ok"):
                _STATS["promoted"] += 1
            else:
                k = str(got.get("skipped") or "skipped")
                _STATS["promote_skips"][k] = int(_STATS["promote_skips"].get(k, 0)) + 1
    except Exception:  # noqa: BLE001 - a print not added is the old behaviour
        pass


def _zh():
    try:
        from . import crissa_home as H              # the copy a pack puts beside this file
        return H
    except ImportError:
        import crissa_home as H                     # the source tree's (lib on the path, home.py put it there)
        return H


LABEL_FACE_SIM = float(os.environ.get("CRISSA_LABEL_FACE_SIM", "0.40") or 0.40)     # the live-face profile bar
LABEL_FACE_SLACK_S = float(os.environ.get("CRISSA_LABEL_FACE_SLACK_S", "2.0") or 2.0)
_HER = ("crissa", "zoe", "crissa_her_tts", "announcer", "crew_tts")


def face_on() -> bool:
    return os.environ.get("CRISSA_LOG_NAME_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off")


def face_label(presence: dict | None, *, speech_start: float | None = None, now: float | None = None) -> dict | None:
    """{"who", "via": "face", "sim", "face_age_s"} when presence's face block names exactly one person at the camera
    while the line was said (see THE FACE NAMES A LINE AT ONCE), else None."""
    p = presence if isinstance(presence, dict) else {}
    buf = p.get("face") if isinstance(p.get("face"), dict) else {}
    try:
        age = float(buf["age_s"]) if buf.get("age_s") is not None else None
    except (TypeError, ValueError):
        age = None
    if age is None or age < 0:
        return None
    now = time.time() if now is None else float(now)
    if speech_start is not None:
        try:
            if now - age < float(speech_start) - LABEL_FACE_SLACK_S:
                return None                        # the look is older than the line: not who said it
        except (TypeError, ValueError):
            pass
    try:
        from .stamp import is_named_person
    except Exception:  # noqa: BLE001
        def is_named_person(n):                    # noqa: E306 - the stamp's rule when it cannot load
            return bool(n) and n not in ("unknown", "unnamed", "none", "?")
    names: dict[str, float | None] = {}
    for f in buf.get("faces") or []:
        if not isinstance(f, dict):
            continue
        n = str(f.get("name") or "").strip().lower()
        try:
            sim = float(f["sim"]) if f.get("sim") is not None else None
        except (TypeError, ValueError):
            sim = None
        if not n or n in _HER or not is_named_person(n) or (n[0] == "s" and n[1:].isdigit()):
            continue
        if sim is not None and sim < LABEL_FACE_SIM:
            continue
        names[n] = max(sim, names.get(n) or 0.0) if sim is not None else names.get(n)
    locked = str(buf.get("locked") or "").strip().lower()
    if locked and locked not in _HER and is_named_person(locked):
        names.setdefault(locked, None)
    if len(names) != 1:
        return None                                # nobody, or two people at the camera: the face cannot say who
    who, sim = next(iter(names.items()))
    return {"who": who, "via": "face", "sim": round(float(sim), 3) if sim is not None else None,
            "face_age_s": round(age, 2)}


def mode() -> str:
    """on | id_only | off. Unreadable: on (today's ear)."""
    try:
        m = _zh().security_mode()
    except Exception:  # noqa: BLE001
        return "on"
    return m if m in ("on", "id_only", "off") else "on"


def parallel_ok(m: str | None = None) -> bool:
    """Run today's voice print beside the decode (the On mode)?"""
    return (m or mode()) == "on"


def labels_on(m: str | None = None) -> bool:
    """Name the speaker after the turn is sent (ID-only, and Off).
    .101 round 28 (the User 2026-10-17: "Should not have a problem IDing me right? same task whether secure on or off"):
    knowing who spoke is the same job in every mode; Security only decides what it unlocks. Off now labels like ID-only
    (the trailing worker, never in the turn's way) and gates nothing. Kill CRISSA_OFF_LABELS=0 (round 17: Off runs no
    recognition)."""
    m = m or mode()
    if m == "off":
        return os.environ.get("CRISSA_OFF_LABELS", "1").strip().lower() not in ("0", "false", "off", "no")
    return m == "id_only"


def labels_path() -> Path:
    return EAR / LABELS_FILE


def neutral(d: dict, m: str) -> dict:
    """The turn as ID-only / Off publish it: no speaker yet. The media gate's own verdict (TV-shaped sound) stays; an
    identity hold (`media_hold_sole`: "is the sole person really speaking?") is not media and goes."""
    st = d.get("stamp") if isinstance(d.get("stamp"), dict) else {}
    src = str(st.get("source") or "")
    tv = src.startswith("media_gate") or src == "tts_echo_veto"
    if not tv:
        for k in ("media_suppressed_id", "media_suppressed_source"):
            st.pop(k, None)
        st["source"] = "id_trailing" if m == "id_only" else "id_off"
    st["person_id"] = None
    st["certain"] = False
    st["id_status"] = None
    st.pop("display_name", None)
    d["stamp"] = st
    d["person_id"] = None
    d["id_trail"] = m
    d["probably"] = None                       # the key is there from the start: the label only fills it
    return d


def _write(row: dict) -> None:
    p = labels_path()
    with _LOCK:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            if p.stat().st_size > KEEP_BYTES:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-KEEP_ROWS:]
                tmp = p.with_suffix(".jsonl.tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                os.replace(tmp, p)
        except OSError:
            pass


def after_publish(entry: dict, pcm: bytes | None, sr: int, m: str | None = None) -> None:
    """The turn is on the ring: its null row now, its label when recognition answers (ID-only only). Never raises."""
    try:
        m = m or mode()
        text = str(entry.get("text") or "").strip()
        if not text:
            return
        base = {"ts": round(float(entry.get("ts") or time.time()), 3), "text": text[:220], "mode": m,
                "speech_end_ts": entry.get("speech_end_ts")}
        if entry.get("speech_start_ts") is not None:
            base["speech_start_ts"] = entry.get("speech_start_ts")         # round 18: the face's window
        _write(dict(base, who=None, via=None, sim=None, at=round(time.time(), 3)))
        if not labels_on(m) or not pcm:
            return
        _start()
        try:
            _Q.put_nowait((entry, bytes(pcm), int(sr or 16000), base))
        except queue.Full:
            _STATE["dropped"] = int(_STATE["dropped"]) + 1
            if not _STATE["said_full"]:
                _STATE["said_full"] = True
                import sys
                print("[ear] speaker labels: %d turns waiting, one left unlabelled" % QUEUE_MAX, file=sys.stderr,
                      flush=True)
    except Exception:  # noqa: BLE001 - the label is a courtesy; the turn is already sent
        pass


def _start() -> None:
    with _LOCK:
        w = _STATE.get("worker")
        if w is not None and w.is_alive():
            return
        w = threading.Thread(target=_work, name="id-trail", daemon=True)
        _STATE["worker"] = w
        w.start()


def _work() -> None:
    if NICE > 0:                               # a Linux thread is its own task (see NICE)
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), NICE)
        except (AttributeError, OSError, PermissionError):
            pass
    while True:
        entry, pcm, sr, base = _Q.get()
        try:
            presence, face = None, None
            if face_on():                          # round 18: the face first, at once (see THE FACE NAMES A LINE)
                try:
                    from .presence import fetch_presence
                    presence = fetch_presence(timeout=0.5)
                    face = face_label(presence, speech_start=base.get("speech_start_ts"))
                except Exception:  # noqa: BLE001
                    presence, face = None, None
                if face:
                    _write(dict(base, at=round(time.time(), 3), **face))
                    try:
                        entry["probably"] = {k: face.get(k) for k in ("who", "via", "sim")}
                    except Exception:  # noqa: BLE001
                        pass
            _TL.full = None
            try:
                if isinstance(presence, dict) and not presence.get("error"):
                    got = recognise(pcm, sr, base.get("text") or "", presence=presence)
                else:
                    got = recognise(pcm, sr, base.get("text") or "")
            except Exception as e:  # noqa: BLE001
                got = {"who": None, "via": None, "sim": None, "error": "%s" % type(e).__name__}
            full = getattr(_TL, "full", None)
            _TL.full = None
            dur = len(pcm) / (2.0 * float(sr or 16000))
            try:
                _note(got, full, face, dur)
            except Exception:  # noqa: BLE001
                pass
            if not (face and (not got.get("who") or got.get("via") == "scratch")):
                row = dict(base, at=round(time.time(), 3), **got)
                if got.get("who"):
                    try:
                        entry["probably"] = {k: got.get(k) for k in ("who", "via", "sim")}
                    except Exception:  # noqa: BLE001
                        pass
                _write(row)                        # (else the face's name stands: a voice no one knows does not take it)
            if full is not None:
                _promote(entry, full, base.get("text") or "", dur, pcm, sr)
        finally:
            _Q.task_done()


def recognise(pcm: bytes, sr: int, text: str, presence: dict | None = None) -> dict:
    """Whoever today's stamp would have named: {"who", "via", "sim"}. The embedding, rank, her-voice veto and the
    Wall's live speaker are voice_live's (the On mode's parallel kick, here after the turn); the name is
    stamp.stamp_turn's with that embedding and presence as it is now (its face rules confirm or stand in; round 18:
    the presence the face label just read, when it read one)."""
    from .voice_live import embed_rank_publish
    info = embed_rank_publish(pcm, sr)
    if info.get("veto"):
        _TL.full = {"st": {}, "emb": info.get("emb")}
        return {"who": "crissa", "via": "her_voice", "sim": None}
    # ROUND 36 (the User 2026-10-19): while an Add/Edit IDs run has the camera, the voice names nobody - the guest
    # counting at the camera is never a stranger on the log and never the User's auth dropped (registration_hold)
    try:
        from .registration_hold import held as _reg_held
        if _reg_held():
            _TL.full = {"st": {}, "emb": info.get("emb")}
            return {"who": None, "via": "registration", "sim": None}
    except Exception:  # noqa: BLE001
        pass
    emb = info.get("emb")
    from .house_id import index_ids
    from .presence import fetch_presence
    from .stamp import stamp_turn
    if not isinstance(presence, dict) or presence.get("error"):
        presence = fetch_presence()
    st = stamp_turn(text, presence, extra_names=index_ids(), voice_emb=emb,
                    speech_duration_s=len(pcm) / (2.0 * float(sr or 16000)))
    _TL.full = {"st": st, "emb": emb}           # for the worker's promote and its count (never written to the file)
    who = str(st.get("person_id") or "").strip().lower() or None
    src = str(st.get("source") or "")
    if src == "tts_echo_veto":
        return {"who": "crissa", "via": "her_voice", "sim": None}
    sim = st.get("voice_emb_sim")
    if who is None and emb is not None:
        try:
            from .scratch_speakers import assign_scratch
            scr = assign_scratch(emb, stamp=st, text=text, duration_s=len(pcm) / (2.0 * float(sr or 16000)))
            if scr.get("ok") and st.get("person_id"):
                return {"who": str(st["person_id"]).lower(), "via": "scratch", "sim": sim}
        except Exception:  # noqa: BLE001
            pass
    if not who:
        return {"who": None, "via": None, "sim": sim}
    via = "voice" if src.startswith("voice_emb") else ("face" if src in ("sole_present",) else (src or "voice"))
    if via == "voice" and info.get("pid") and str(info.get("pid")) == who and _face_says(presence, who):
        via = "voice+face"
    return {"who": who, "via": via, "sim": round(float(sim), 3) if sim is not None else None}


def _face_says(presence: dict, who: str) -> bool:
    """Does presence's face block name `who` now (a fresh face at the camera)?"""
    buf = presence.get("face") if isinstance(presence.get("face"), dict) else {}
    if str(buf.get("locked") or "").strip().lower() == who:
        return True
    for f in buf.get("faces") or []:
        if isinstance(f, dict) and str(f.get("name") or "").strip().lower() == who:
            return True
    return False


def wait_idle(timeout: float = 10.0) -> bool:
    """Tests and benches: True when every queued turn has its label."""
    end = time.time() + timeout
    while time.time() < end:
        if _Q.unfinished_tasks <= 0:
            return True
        time.sleep(0.01)
    return False
