"""Control Panel operator recount — tell Oracle, then one walk.

the User 2026-08-18: one button. Tell Oracle the count is wrong, then the
same Who-is-here walk (wide + FaceFinder zoom + the face ID). A body with
no name stays S#. User is always kept.

    POST http://127.0.0.1:8191/v1/error_check   → who_is_here

Argus is told first, then again with the result. He must not sweep
again — this is a desk note + a short chat.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ERROR_CHECK_URL = os.environ.get(
    "VO_ERROR_CHECK_URL", "http://127.0.0.1:8191/v1/error_check"
)
PRESENCE_URL = os.environ.get(
    "VO_PRESENCE_URL", "http://127.0.0.1:8191/v1/presence"
)
# The Argus agent desk is retired (2026-08-28). Do not POST :8650 and do not
# make its profile folder — that resurrected the profile home on
# every Control Panel recount. Operator note lands in the inbox.
ARGUS_NOTE = Path(
    os.environ.get(
        "VO_ARGUS_NOTE",
        str(Path.home() / "Desktop/Deliverables/OPERATOR-COUNT-ERROR.md"),
    )
).expanduser()
# Same aliases the panel and any leftover scripts may still send.
ERROR_CHECK_ACTIONS = frozenset(
    {
        "error_check",
        "scan_room",
        "body_count",
        "room_scan",
        "error-check",
        "who_is_here",
        "whos_here",
        "rescan_room",
    }
)
ERROR_CHECK_TIMEOUT = float(os.environ.get("VO_ERROR_CHECK_TIMEOUT", "90"))

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_lock = threading.Lock()


def snapshot_presence(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """Named people + headcount from the one SoT. Never invents a roster."""
    d = raw if isinstance(raw, dict) else {}
    people: list[str] = []
    for p in d.get("people") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or p.get("person_id") or p.get("slot_id") or "").strip()
        if name:
            people.append(name)
    try:
        hc = int(d.get("headcount")) if d.get("headcount") is not None else len(people)
    except (TypeError, ValueError):
        hc = len(people)
    return {"headcount": hc, "people": people}


def fetch_presence(timeout: float = 2.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(PRESENCE_URL, timeout=timeout) as r:
            raw = json.loads(r.read().decode())
    except Exception:
        return {"headcount": None, "people": [], "error": "presence_unreadable"}
    if not isinstance(raw, dict):
        return {"headcount": None, "people": [], "error": "presence_unreadable"}
    return snapshot_presence(raw)


def _face_id_name() -> str:
    """The note names the face ID the way the house always wrote it (lib/presence_house, house only; the Crissa merge
    2026-10-11); a pack says "the face ID"."""
    try:
        import presence_house
        return presence_house.FACE_NAMERS[0].capitalize()
    except ImportError:
        return "the face ID"


_FACE_ID = _face_id_name()


def format_argus_log(*, before: dict[str, Any], result: dict[str, Any]) -> str:
    """What Argus is asked to remember. Sweep already ran. Do not re-aim."""
    sot = result.get("sot") if isinstance(result.get("sot"), dict) else {}
    after_hc = sot.get("headcount")
    before_hc = before.get("headcount")
    ok = bool(result.get("ok"))
    err = result.get("error") or ""
    return (
        "OPERATOR COUNT CORRECTION (Control Panel).\n"
        "One walk already wrote the one presence object (wide + FaceFinder "
        "zoom + " + _FACE_ID + "). Do not run error-check. Do not invent a third "
        "tour. Do not aim the camera. Log this miss so you keep better track.\n\n"
        f"ok={ok} facefinder=wide+zoom\n"
        f"before: hc={before_hc} people={before.get('people')}\n"
        f"after:  hc={after_hc} people={sot.get('people')} seen={result.get('seen')}\n"
        f"bodies_seen={result.get('bodies_seen')} unnamed={result.get('unnamed')} "
        f"error={err or 'none'}\n"
        f"camera_home={result.get('camera_returned_home')}\n"
    )


def write_argus_desk(text: str) -> bool:
    """Inbox note. Must not create the Argus profile folder."""
    try:
        ARGUS_NOTE.parent.mkdir(parents=True, exist_ok=True)
        ARGUS_NOTE.write_text(text.rstrip() + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def append_house_memory(text: str, meta: dict[str, Any]) -> bool:
    try:
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        from house_memory import append_event  # type: ignore

        append_event(
            "error_check",
            text.replace("\n", " ")[:1200],
            source="voiceonly_panel",
            meta=meta,
        )
        return True
    except Exception:
        return False


def notify_argus(*, before: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Inbox + day log. No Argus agent chat — that desk is retired."""
    text = format_argus_log(before=before, result=result)
    sot = result.get("sot") if isinstance(result.get("sot"), dict) else {}
    meta = {
        "before": before,
        "after_headcount": sot.get("headcount"),
        "bodies_seen": result.get("bodies_seen"),
        "ok": bool(result.get("ok")),
        "facefinder": True,
    }
    return {
        "desk": write_argus_desk(text),
        "memory": append_house_memory(text, meta),
        "chat": "skipped_retired",
    }


def post_error_check(timeout: float | None = None) -> dict[str, Any]:
    """HTTP only — same endpoint Argus `error-check` uses."""
    req = urllib.request.Request(
        ERROR_CHECK_URL,
        data=json.dumps({"source": "voiceonly_panel"}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or ERROR_CHECK_TIMEOUT) as r:
            raw = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read(240).decode("utf-8", "replace")
        except Exception:
            detail = ""
        return {"ok": False, "error": f"http_{e.code}", "detail": detail[:240]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    return raw if isinstance(raw, dict) else {"ok": False, "error": "bad_payload"}


def run_operator_error_check() -> dict[str, Any]:
    """Tell Oracle first, then the one walk, then the result note."""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "action": "error_check", "error": "already_running"}
    t0 = time.time()
    try:
        before = fetch_presence()
        write_argus_desk(
            "OPERATOR COUNT CORRECTION starting. "
            "User is in the room. Do not aim the camera yourself.\n"
            f"before: hc={before.get('headcount')} people={before.get('people')}\n"
        )
        result = post_error_check()
        argus = notify_argus(before=before, result=result)
        ok = bool(result.get("ok"))
        err = None if ok else (result.get("error") or "error_check_failed")
        return {
            "ok": ok,
            "action": "error_check",
            "before": before,
            "result": result,
            "argus": argus,
            "error": err,
            "wall_sec": round(time.time() - t0, 3),
        }
    finally:
        _lock.release()
