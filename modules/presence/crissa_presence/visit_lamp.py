"""Visit lamp — Secure / not Secure.

Secure is a FLAG, not a projection. Exactly two things move it:
  * User click  → toggles (insecure→Secure after an Auth-request at the desk;
                  Secure→insecure immediately).
  * A qualified face in a door frame → Secure→insecure (the tripwire).
Nothing else: no hourly expiry, no census math, no boot proof. While Secure
the camera sits on the door and AUTH is held for whoever is in the room —
only the User can say that, and we do not re-check until the flag drops.

Colour is a display of the flag: sage = Secure, amber = not Secure, rust =
camera cannot see (display only; rust never moves the flag or AUTH).

SPEAKING does not consume the lamp. Pause-mic idle is not this flag.
"""
from __future__ import annotations

from . import paths as _paths

import json
import os
import time
from pathlib import Path
from typing import Any

from .fsio import append_jsonl_capped, atomic_write_text
from .guests import visitor_day

SAGE = "sage"
AMBER = "amber"
RUST = "rust"
HOVER = {
    SAGE: "Secure",
    AMBER: "not Secure",
    RUST: "Faulty",
}
# KEYBOARD HOLD. the User, 2026-09-06: "a hold auth button for only sudo keyboard
# pw entry on a double click of that Secure button. One click toggle on/off,
# double click makes permanent provided sudo pw and disarms with one click.
# IOW no trigger on face just stays AUTH until clicked then BAU."
# The hold is a second bit beside the Secure flag, in the same object. While
# it is set, only a click moves the flag: door faces and the tripwire are
# ignored. It is minted only by the user's own login password, checked
# through PAM (sudo is NOPASSWD on this box, so "sudo password" means the
# account password), never stored, never logged. Boot clears it like Secure.
HOLD_REASON = "keyboard_hold"
HOVER_HELD = "Secure · held"
HOLD_AUTH_KIND = "hold_auth"
HOLD_AUTH_LABEL = "Hold Auth req failed"
SPEECH_LABEL = "repeated verbal attempts"

DATA = _paths.DATA
LAMP_PATH = Path(os.environ.get("RAIL_VISIT_LAMP", str(DATA / "visit_lamp.json")))
TOKEN_PATH = Path(
    os.environ.get("RAIL_SECURE_TOKEN", str(DATA / "secure_token.json"))
)
SECURITY_AUDIT = Path(
    os.environ.get(
        "RAIL_STORE_FACE_AUDIT",
        str(DATA / "store_face.jsonl"),
    )
)


def user_id() -> str:
    """Same definition as PresenceService._user_id. Stage 2 gates AUTH on this."""
    from .house_user import user_id as _record_user

    uid = _record_user()     # env, else the identity record's User, never a name in code (Init step 5)
    try:
        p = _paths.MODE_FILE
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            auth = data.get("auth") if isinstance(data, dict) else {}
            if isinstance(auth, dict) and auth.get("user_id"):
                uid = str(auth["user_id"]).strip().lower() or uid
    except Exception:
        pass
    return uid or _record_user()


def hold_sec() -> float:
    """RETIRED 2026-09-05: Secure no longer expires on a timer. Kept for old callers."""
    try:
        return max(60.0, float(os.environ.get("RAIL_LAMP_HOLD_SEC") or "3600"))
    except (TypeError, ValueError):
        return 3600.0


def lamp_green_ids() -> frozenset[str]:
    raw = (os.environ.get("RAIL_LAMP_GREEN_IDS") or "").strip()
    if raw:
        return frozenset(x.strip().lower() for x in raw.split(",") if x.strip())
    return frozenset({user_id()})


def is_secure(color: str | None) -> bool:
    """Sage = Secure: door watch + AUTH held. Anything else is not."""
    return str(color or "").strip().lower() == SAGE


def drives_auth() -> bool:
    """Always. Law 1 (BAU is FF-for-AUTH) is not an environment variable.
    RAIL_LAMP_DRIVES_AUTH is no longer read (was default "0")."""
    return True


def sticky_counts(color: str | None, *, force_facefind: bool = False) -> bool:
    """A sticky face hold counts as AUTH only while Secure (Law 2/4) or on a
    forced hunt. Amber/rust: FF must see the User."""
    if force_facefind:
        return True
    return str(color or "").strip().lower() == SAGE


def continual_auth(color: str | None) -> bool:
    """Amber/rust: FaceFinder every command; voice is not enough (Law 1, 3).
    Sage: AUTH is held; do not leave the door (Law 2)."""
    return str(color or "").strip().lower() in (AMBER, RUST)


def is_placeholder_id(pid: str) -> bool:
    n = (pid or "").strip().lower()
    if not n:
        return True
    if n.startswith(("unknown", "crowd", "__")):
        return True
    if len(n) >= 2 and n[0] in ("u", "f", "m") and n[1:].isdigit():
        return True
    return False


def load_ack(path: Path | None = None) -> dict[str, Any]:
    p = path or LAMP_PATH
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"day": "", "ack_ts": 0.0}
    if not isinstance(d, dict):
        return {"day": "", "ack_ts": 0.0}
    day = str(d.get("day") or "")
    try:
        ack = float(d.get("ack_ts") or 0.0)
    except (TypeError, ValueError):
        ack = 0.0
    return {"day": day, "ack_ts": ack}


def save_ack(day: str, ack_ts: float, path: Path | None = None) -> None:
    p = path or LAMP_PATH
    atomic_write_text(
        p,
        json.dumps({"day": str(day), "ack_ts": float(ack_ts)}, indent=2) + "\n",
    )


def load_secure(path: Path | None = None) -> bool:
    """Boot is ALWAYS insecure — we cannot know who entered while down.
    The file is an audit trail, not a restore point."""
    return False


def verify_password(user: str, password: str) -> bool:
    """The account password, through PAM's `login` stack. pam_unix hands a
    non-root caller to unix_chkpwd (setgid shadow), so this works from the
    service user for the service user and nobody else. Returns False on any
    failure, including a missing python-pam; never raises, never logs."""
    if not password or not user:
        return False
    try:
        import pam  # python-pam, in the presence env
    except Exception:
        return False
    try:
        return bool(pam.pam().authenticate(str(user), str(password), service="login"))
    except Exception:
        return False


def save_secure(secure: bool, *, reason: str, path: Path | None = None,
                hold: bool = False) -> None:
    p = path or LAMP_PATH
    now = time.time()
    try:
        atomic_write_text(
            p,
            json.dumps(
                {
                    "secure": bool(secure),
                    "hold": bool(secure and hold),
                    "reason": str(reason),
                    "ts": now,
                    "ts_local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                    # legacy keys so old probes do not crash
                    "day": visitor_day(now),
                    "ack_ts": now if secure else 0.0,
                    "token": bool(load_token()),
                },
                indent=2,
            )
            + "\n",
        )
    except OSError:
        pass


def load_token(path: Path | None = None) -> dict | None:
    """Capability for the next process. Not the live flag (load_secure is False)."""
    p = path or TOKEN_PATH
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not str(d.get("id") or "").strip():
        return None
    return d


def save_token(token: dict | None, path: Path | None = None) -> None:
    p = path or TOKEN_PATH
    if not token:
        clear_token(p)
        return
    try:
        atomic_write_text(p, json.dumps(dict(token), indent=2) + "\n")
    except OSError:
        pass


def clear_token(path: Path | None = None) -> None:
    p = path or TOKEN_PATH
    try:
        p.unlink()
    except OSError:
        pass


def door_face_min_det() -> float:
    """Detector confidence a door face needs to trip Secure (detector det_score)."""
    try:
        return float(os.environ.get("RAIL_DOOR_FACE_MIN_DET") or "0.65")
    except (TypeError, ValueError):
        return 0.65


def door_face_min_px() -> float:
    """Shortest bbox side (px) a door face needs to trip Secure."""
    try:
        return float(os.environ.get("RAIL_DOOR_FACE_MIN_PX") or "60")
    except (TypeError, ValueError):
        return 60.0


def face_qualifies(det_score: float | None, bbox: Any) -> bool:
    """Quality gate for the tripwire: a cat, a passer-by blur, a wall poster
    at 20 px do not drop AUTH. Runs on the DETECT result, before embedding.

    Strict: a missing score or bbox FAILS. Use FaceEngine.det_score / .bbox.
    """
    try:
        if det_score is None or float(det_score) < door_face_min_det():
            return False
    except (TypeError, ValueError):
        return False
    try:
        if bbox is None or len(bbox) < 4:
            return False
        w = abs(float(bbox[2]) - float(bbox[0]))
        h = abs(float(bbox[3]) - float(bbox[1]))
        if min(w, h) < door_face_min_px():
            return False
    except (TypeError, ValueError):
        return False
    return True


def live_not_user_only(
    names: list[str] | tuple[str, ...] | None,
    green: frozenset[str] | None = None,
) -> list[str]:
    """Live IDs that are not User/green. Empty look is not a kill."""
    g = green if green is not None else lamp_green_ids()
    others: list[str] = []
    for raw in names or []:
        n = str(raw or "").strip().lower()
        if not n or n in g or is_placeholder_id(n):
            continue
        if n not in others:
            others.append(n)
    return others


def entry_trips_amber(seen: dict | None, uid: str | None = None) -> bool:
    """Any door/scan face that is not solely the User. Unnamed is a stranger."""
    seen = seen if isinstance(seen, dict) else {}
    try:
        n_faces = int(seen.get("n_faces") or 0)
    except (TypeError, ValueError):
        n_faces = 0
    if n_faces < 1:
        return False
    uid = (uid or user_id()).strip().lower()
    raw = seen.get("names") or {}
    if isinstance(raw, dict):
        named = [str(n).strip().lower() for n in raw if str(n).strip()]
    else:
        named = [str(n).strip().lower() for n in raw if str(n).strip()]
    if live_not_user_only(named):
        return True
    if uid not in named:
        return True
    if n_faces > 1:
        return True
    return False


def latest_non_user_ts(census: dict[str, Any] | None, green: frozenset[str]) -> float:
    """Newest *first-seen* among non-User ids. Resight of another person must not move this."""
    latest = 0.0
    for row in (census or {}).get("people") or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "").strip().lower()
        if not pid or pid in green or is_placeholder_id(pid):
            continue
        try:
            ts = float(row.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts <= 0:
            continue
        if ts > latest:
            latest = ts
    return latest


def project_color(
    *,
    secure: bool,
    cannot_see: bool,
    reason: str = "",
    since: float | None = None,
    now: float | None = None,
    **_legacy: Any,
) -> tuple[str, str, dict[str, Any]]:
    """Return (color, why, info). Pure display of the Secure flag.

    Old kwargs (boot_unproven / ack / census) are accepted and ignored so a
    stale caller cannot crash the SoT publish.
    """
    now = time.time() if now is None else float(now)
    # Flag first. While Secure the colour is sage even if the camera cannot
    # see — rust would make every sage-reader think BAU while AUTH is still
    # held (mixed modes). cannot_see rides in info instead.
    hold = bool(_legacy.pop("hold", False))
    if secure:
        color, why = SAGE, ("secure_held" if hold else "secure")
    elif cannot_see:
        color, why = RUST, "cannot_see"
    else:
        color, why = AMBER, (reason or "insecure")
    age = (now - float(since)) if since else None
    info = {
        "color": color,
        "why": why,
        "hover": HOVER_HELD if (secure and hold) else HOVER[color],
        "secure": bool(secure),
        "hold": bool(secure and hold),
        "reason": str(reason or ""),
        "since": since,
        "age": None if age is None else round(age, 1),
        "cannot_see": bool(cannot_see),
        # legacy keys the probe page reads
        "day": visitor_day(now),
        "ack_ts": since if secure else 0.0,
        "ack_age": None if (age is None or not secure) else round(age, 1),
    }
    return color, why, info


def apply_to_state(st: Any, *, color: str, info: dict[str, Any]) -> None:
    st.visit_lamp = color
    st.visit_lamp_info = dict(info)


def note_hold_auth_failed(
    *,
    xid: str | None = None,
    display: str | None = None,
    photo: str | None = None,
    source: str = "visit_lamp",
) -> None:
    """Security-log line. Distinct from speech (repeated verbal attempts)."""
    now = time.time()
    row = {
        "ok": False,
        "kind": HOLD_AUTH_KIND,
        "label": HOLD_AUTH_LABEL,
        "source": source,
        "xid": xid,
        "display": display,
        "photo": photo,
        "ts": now,
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
    }
    try:
        append_jsonl_capped(SECURITY_AUDIT, json.dumps(row, default=str))
    except OSError:
        pass


PROBE_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<title>visit lamp</title>
<style>
:root{--sage:#6F9B7E;--ember:#D9A05B;--rust:#B56054;--bg:#0c0d10;--tx:#e8eaed}
body{font:14px/1.4 ui-monospace,Menlo,monospace;background:var(--bg);color:var(--tx);margin:24px}
.dot{width:14px;height:14px;border-radius:50%;border:1px solid #252833;display:inline-block;vertical-align:middle}
.sage{background:var(--sage)}.amber{background:var(--ember)}.rust{background:var(--rust)}
button{margin-left:12px}
#why{color:#8b93a7;margin-top:10px}
</style></head><body>
<p><span id="dot" class="dot amber" title="not Secure"></span>
<button id="go">click</button></p>
<pre id="why"></pre>
<script>
function paint(d){
  var c=(d.visit_lamp||d.color||"amber");
  var el=document.getElementById("dot");
  el.className="dot "+c;
  el.title=(d.visit_lamp_info&&d.visit_lamp_info.hover)||c;
  document.getElementById("why").textContent=JSON.stringify(d,null,2);
}
function load(){ fetch("/v1/visit_lamp",{cache:"no-store"}).then(r=>r.json()).then(paint); }
document.getElementById("go").onclick=function(){
  fetch("/v1/visit_lamp/click",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"}).then(r=>r.json()).then(function(){ load(); });
};
load(); setInterval(load, 2000);
</script>
</body></html>
"""
