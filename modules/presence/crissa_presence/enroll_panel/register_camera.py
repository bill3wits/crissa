"""Register's camera, through presence's `register` token (the User 2026-10-02, Init step 4). OFF by default.

With HOUSE_REGISTER_TOKEN=1 Register never touches Secure: it does not click or release anything, and it never runs
FaceFinder to authenticate. It starts only in User mode (if the User is already authenticated there is no second
check; otherwise it refuses and says why), takes presence's `register` token - which ranks below the User's
joystick and above Secure's door watch, so Secure no longer pulls the camera back to the door mid-take - aims with
the token's lease, and gives the token back at the end, which sends the camera home. Secure and its hold are exactly
as they were before and after. Off, Register runs the old way (secure_legacy).
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Callable

SWITCH = "HOUSE_REGISTER_TOKEN"
# The deny is SAID, not only logged (the User 2026-10-03: two refusals reached only the session log). Register never
# authenticates on its own: sign in as the User (Secure on), then press Register again.
REFUSE_TEXT = "Register needs User auth first. Sign in as the User, then press Register again."


def user_enrolled(root) -> bool:
    """A User is enrolled once the identity store holds a person whose role is "user". Before that (first boot) there
    is nobody to sign in as, so Register's User-mode rule cannot apply yet (the User 2026-10-03). Unreadable = enrolled."""
    from pathlib import Path

    try:
        for pj in sorted((Path(root) / "people").glob("*/person.json")):
            if (json.loads(pj.read_text(encoding="utf-8")) or {}).get("role") == "user":
                return True
        return False
    except Exception:  # noqa: BLE001 - never read a broken store as "no User yet"
        return True


def on() -> bool:
    """HOUSE_REGISTER_TOKEN when it is set; unset, ON in a pack and off on the house (the User 2026-10-10: a pack
    satisfies Register's start needs itself - nobody sets a variable for Init's Voice ID)."""
    got = os.environ.get(SWITCH, "").strip().lower()
    if got:
        return got in ("1", "on", "true", "yes")
    from .. import paths as _paths
    return not _paths.is_house()


def user_mode(presence: str) -> bool:
    """True when presence already holds the User's authentication. Read only; never starts a look."""
    try:
        with urllib.request.urlopen(f"{presence}/v1/presence", timeout=3) as r:
            d = json.loads(r.read().decode() or "{}")
    except Exception:  # noqa: BLE001 - unknown is not User mode
        return False
    return bool(d.get("user_auth") or (d.get("presence") or {}).get("user_auth"))


def take(presence: str, post: Callable[..., dict], why: str = "register", visitor: str = "") -> dict[str, Any]:
    """The token for Register: {ok, lease_id} or the refusal (not_user_mode / off / held). `visitor`: a visitor's own
    take (ADD A VISITOR below); presence checks it with the player before it lends the camera without User mode."""
    body = {"op": "take", "why": why}
    if visitor:
        body["visitor"] = str(visitor)
    return post(f"{presence}/v1/camera/register", body, timeout=8)


GIVE_BACK_TIMEOUT_SEC = 12.0   # presence homes the camera before it answers (round 46: named, the Cancel's wait sums it)


def give_back(presence: str, post: Callable[..., dict]) -> dict[str, Any]:
    """Give it back: presence sends the camera home and leaves Secure as it was."""
    return post(f"{presence}/v1/camera/register", {"op": "give_back"}, timeout=GIVE_BACK_TIMEOUT_SEC)


# WHY REGISTER COULD NOT TAKE THE CAMERA, IN WORDS THAT ARE TRUE (the User 2026-10-10, .101: every refusal said "The camera
# is busy", and there the token was switched off - the camera was free). Each refusal presence gives has its own line.
_HOLDERS = {"user_joystick": "you are steering it", "secure_glance": "the door watch has it",
            "ff_mode": "the face finder has it", "beam_look": "a look toward a voice has it",
            "f2f": "a face-to-face look has it", "seat_shot": "a seat picture has it", "door_mill": "the door watch has it",
            "external": "another program has it", "register": "another Register has it"}
BUSY_TEXT = "The camera is busy - press Retry in a moment."


def refusal_text(tok: dict | None) -> str:
    """The plate's line for a refused token ({ok: false, error, owner, why}); BUSY_TEXT only when it IS busy."""
    tok = tok if isinstance(tok, dict) else {}
    err = str(tok.get("error") or "")
    if err == "not_user_mode":
        return REFUSE_TEXT
    if err == "camera_leased":
        who = _HOLDERS.get(str(tok.get("owner") or tok.get("why") or ""), "")
        return ("The camera is busy: %s. Press Retry in a moment." % who) if who else BUSY_TEXT
    if err == "off":
        return ("Voice ID could not start: the presence service has Register switched off on this computer. "
                "Restart Crissa, then press Retry.")
    if err == "no_ttl":
        return ("Voice ID could not start: the presence service has no time limit for Register (its settings file "
                "is missing). Reinstall the presence module, then press Retry.")
    if err.startswith("camera:"):
        return ("Voice ID could not reach the camera: its service did not answer (%s). Press Retry; if it says this "
                "again, restart Crissa." % err.split(":", 1)[1])
    status = tok.get("status")
    if status == 404:
        return "Voice ID could not start: the presence service does not offer Register here. Press Retry."
    return ("Voice ID could not start: the presence service did not answer (%s). Press Retry; if it says this again, "
            "restart Crissa." % (err or "no answer")[:80])


# ADD A VISITOR (round 20, the User 2026-10-14: "We need to for added visitors"): a visitor's own take may run without
# User mode outside Security On, and only while THIS install's player says it is adding exactly that person now (GET
# /v1/init `visitor`: {person_id, display, security}). The coach keeps the visitor's id exactly with it, the routine passes
# its User-mode rule with it, and presence lends the camera with it. Kill: CRISSA_ADD_VISITOR=0 (no visitor is vouched).
VISITOR_ENV = "CRISSA_REGISTER_VISITOR"


def visitor_on() -> bool:
    return os.environ.get("CRISSA_ADD_VISITOR", "1").strip().lower() not in ("0", "off", "false", "no")


def visitor_run(init_url: str, person_id: str, timeout: float = 3.0) -> dict[str, Any]:
    """The player's live visitor run for exactly `person_id` ({person_id, display, security}), else {}."""
    pid = str(person_id or "").strip().lower()
    if not pid or not init_url or not visitor_on():
        return {}
    try:
        with urllib.request.urlopen(init_url, timeout=timeout) as r:
            snap = json.loads(r.read().decode() or "{}")
    except Exception:  # noqa: BLE001  no answer is no vouch
        return {}
    v = snap.get("visitor") if isinstance(snap, dict) else None
    if snap.get("status") != "running" or not isinstance(v, dict) or str(v.get("person_id") or "").lower() != pid:
        return {}
    return dict(v)


def visitor_vouched(init_url: str, person_id: str) -> bool:
    """A visitor's take may go without User mode: the player's run is live for exactly them and Security is not On."""
    v = visitor_run(init_url, person_id)
    return bool(v) and str(v.get("security") or "on") in ("id_only", "off")
