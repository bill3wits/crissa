"""A FIXED CAMERA SEES THE USER AT HIS SEAT (the User 2026-10-11, .101 round 9: "sole_present False on every turn").

On .101 the camera is an Elgato that does not move. It reports a pan control, and reads back whatever pan it was
sent (450000, "the door home"; 130 deg after a nudge), so presence took its one view for the door: the User's face,
recognised there at 0.55-0.76, was refused as a door face ("refuse guest_needs_ff", the house's Law 1 for a camera
that can turn to the seat), the roster stayed empty, headcount 0, and the ear never had sole_present. On a camera that
cannot turn, its one view IS the seat.

What this module decides (the Service and the routes ask it; nothing here touches AUTH):

    is_fixed()    the camera does not move: RAIL_CAMERA_FIXED=1|0 says so outright; else a camera check's record
                  (<presence state>/camera-kind.json, written by note_pan_check below) when it decided; else the
                  grabber's camera_ptz: 'digital' (a pan that is a crop of one view) or 'none' is fixed, 'motor' is not;
                  else a camera that never reported a pan (the grabber's pan_seen False: most webcams, every Mac's)
    sole_on()     the rule is on: RAIL_FIXED_CAM_SOLE=1|0, else on in a pack and off on the house
    note_pan_check(before, after, pan_from, pan_to)
                  MEASURED, not believed: a pan of at least MIN_PAN_DEG that leaves the picture where it was is a camera
                  that does not move. Two such pans in a row record fixed; one pan that moves the picture records a
                  moving camera (and clears fixed). seat_shot.shot_at takes the two pictures around its own aim.
    look(...)     the oracle's last look, as /v1/see hands it here (every second while a face is in view): who, how
                  sure, and how many faces. The face block (faceid_hud) and the sole slot are built from it.
    sole(...)     the sole User from that look, or None: a fixed camera, the rule on, exactly ONE face in view, named
                  as the User at the live-face bar (0.60, the visor's and the ear's), fresh (SOLE_MAX_AGE_S).

Kill switches: RAIL_FIXED_CAM_SOLE=0 (the rule), RAIL_FIXED_CAM_MEASURE=0 (the measured check),
RAIL_CAMERA_PTZ_BY_MODEL=0 (the camera's own name no longer counts).

A CROP IS NOT A TURN (2026-10-12, .101 round 10: still guest most turns). .101's Elgato Facecam 4K reports pan, tilt
and zoom, but they move a crop inside one view (zoom 1.5x there: at most about a sixth of the width either way). The
measured check never decided: one 125 deg pan read dx 0.069, mean difference 29.4, response 0.038 - not the same
picture, not a moved one - so camera-kind.json kept no `fixed` and the face block stayed the door glance's (7-15 s old),
too old for the ear's "the face decides". A camera whose own name is not a motor gimbal's (MOTOR_PTZ) and that reports
a pan is 'digital': one view, fixed. A decided measurement still wins, and the switch over all of it.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

KIND_FILE = "camera-kind.json"
MIN_PAN_DEG = float(os.environ.get("RAIL_FIXED_CAM_MIN_PAN_DEG", "20"))
UNITS_PER_DEG = 3600.0                      # v4l2 pan_absolute units (the camera profile's, Insta360 Link and others)
SAME_DX = 0.03                              # under 3 % of the width: the picture did not slide
SAME_MAD = 24.0                             # mean absolute difference (0-255) of the small grey picture
SAME_RESPONSE = 0.15                        # phase correlation's peak: the two are the same scene
MOVED_DX = 0.08                             # over 8 % of the width: it moved
NEED_SAME = 2
SOLE_MAX_AGE_S = float(os.environ.get("RAIL_FIXED_SOLE_MAX_AGE_S", "8"))

_LOCK = threading.Lock()
_LOOK: dict[str, Any] = {}
_SEEN: dict[str, Any] = {}             # note_camera's last health (name, opened, kind) and the record it left


def _off(v) -> bool:
    return str(v or "").strip().lower() in ("0", "off", "false", "no")


def _on(v) -> bool:
    return str(v or "").strip().lower() in ("1", "on", "true", "yes")


def _root(home: Path | None = None) -> Path:
    if home is not None:
        return Path(home)
    from . import paths as _paths
    return Path(_paths.ROOT)


def kind_path(home: Path | None = None) -> Path:
    return _root(home) / KIND_FILE


def record(home: Path | None = None) -> dict:
    try:
        d = json.loads(kind_path(home).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(rec: dict, home: Path | None = None) -> None:
    p = kind_path(home)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def is_house() -> bool:
    try:
        from . import paths as _paths
        return bool(_paths.is_house())
    except Exception:  # noqa: BLE001
        return False


def sole_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    v = e.get("RAIL_FIXED_CAM_SOLE")
    if v is not None and str(v).strip():
        return _on(v)
    return not is_house()


# The webcams whose pan is a motor (a head that turns to another view). Any other camera that reports a pan moves a crop.
MOTOR_PTZ = re.compile(r"insta360\s*link|obsbot|tail\s*air|ptz|rally|meetup|\baver\b", re.I)


def ptz_kind(name: str, pan_seen: bool, env: dict | None = None) -> str:
    """'motor' | 'digital' | 'none' for a camera's own name and whether it ever reported a pan (see MOTOR_PTZ)."""
    e = os.environ if env is None else env
    if not pan_seen:
        return "none"
    if _off(e.get("RAIL_CAMERA_PTZ_BY_MODEL", "1")):
        return "motor"
    return "motor" if (not str(name or "").strip() or MOTOR_PTZ.search(str(name))) else "digital"


def is_fixed(health: dict | None = None, *, env: dict | None = None, home: Path | None = None) -> bool:
    """The camera does not move (see the module's note): the switch, else a decided measurement, else the camera's own
    pan kind (a crop or no pan is fixed), else no pan ever."""
    e = os.environ if env is None else env
    v = e.get("RAIL_CAMERA_FIXED")
    if v is not None and str(v).strip():
        return _on(v)
    rec = record(home)
    if digital_fixed(rec, health, e):
        return True                        # round 27: a crop pan never turns, whatever a pan check measured
    if "fixed" in rec:
        if rec.get("fixed") is False and crop_only(rec, e):
            return True                    # round 26: a crop that "moved" by its pixels alone did not turn
        return bool(rec.get("fixed"))
    h = health if isinstance(health, dict) else {}
    kind = str(h.get("camera_ptz") or "").strip().lower()
    if kind and not _off(e.get("RAIL_CAMERA_PTZ_BY_MODEL", "1")):
        return kind in ("digital", "none")
    return bool(h.get("pan_seen") is False)


# A RE-PLUGGED CAMERA IS MEASURED AGAIN (2026-10-12, .101: a USB hub reset took the Elgato away 04:33-06:13; after it
# came back camera-kind.json was still the 03:29 record, with no kind in it). note_camera() runs on every grabber
# health the service reads: when the device was opened again (opened_at) or is another camera (camera_name), the
# record starts over - the name, the pan kind and when, and no measured pans - so the next desk looks measure the
# camera that is there now. Written only on a change. Kill: RAIL_FIXED_CAM_REMEASURE=0.
def note_camera(health: dict | None, *, env: dict | None = None, home: Path | None = None) -> dict:
    e = os.environ if env is None else env
    if _off(e.get("RAIL_FIXED_CAM_REMEASURE", "1")) or not isinstance(health, dict) or health.get("ok") is False:
        return record(home)
    name = str(health.get("camera_name") or "")
    opened = health.get("opened_at")
    kind = str(health.get("camera_ptz") or "")
    if not (name or opened or kind):
        return record(home)          # an older grabber says none of it: the record stays as it is
    key = (str(home or ""), name, opened, kind)
    if _SEEN.get("key") == key:
        return _SEEN.get("rec") or {}  # nothing changed since the last health: no disk read
    with _LOCK:
        rec = record(home)
        same_cam = rec.get("camera_name") == name and rec.get("opened_at") == opened
        if same_cam and rec.get("kind") == kind:
            _SEEN.update(key=key, rec=rec)
            return rec
        if same_cam:
            rec["kind"] = kind       # the kind alone changed (the switch): keep what was measured
        else:
            prev = {k: rec.get(k) for k in ("camera_name", "opened_at", "kind", "fixed", "same", "at") if k in rec}
            rec = {"camera_name": name, "opened_at": opened, "kind": kind, "noted_at": time.time(),
                   "how": "the camera was opened%s; pans are measured again" % (" again" if prev else "")}
            if prev:
                rec["previous"] = prev
        _write(rec, home)
        _SEEN.update(key=key, rec=rec)
        return rec


def _grey(jpeg: bytes):
    import cv2
    import numpy as np
    im = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if im is None:
        return None
    return cv2.resize(im, (128, 72), interpolation=cv2.INTER_AREA).astype("float32")


def compare(before: bytes, after: bytes) -> dict | None:
    """{"dx", "mad", "response", "same", "moved"} for two pictures of the camera's view; None when one cannot be read."""
    try:
        import cv2
        import numpy as np
        a, b = _grey(before), _grey(after)
        if a is None or b is None:
            return None
        win = cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F)
        (dx, _dy), response = cv2.phaseCorrelate(a - a.mean(), b - b.mean(), win)
        mad = float(np.mean(np.abs(a - b)))
    except Exception:  # noqa: BLE001  no OpenCV, or a broken picture: nothing measured
        return None
    dxf = abs(float(dx)) / float(a.shape[1])
    same = dxf < SAME_DX and mad < SAME_MAD and float(response) >= SAME_RESPONSE
    moved = dxf > MOVED_DX or (mad >= SAME_MAD * 1.5 and float(response) < SAME_RESPONSE)
    return {"dx": round(dxf, 4), "mad": round(mad, 2), "response": round(float(response), 3), "same": bool(same),
            "moved": bool(moved and not same)}


# A CROP CANNOT TURN TO THE DOOR (round 26, .101 2026-10-16: Secure and Double Secure offered, and presence emptied the
# room - "door_empty_after", headcount 0, guest mode while the User sat in front). The Facecam 4K is 'digital' by its name,
# but one pan read dx 0.0001 with a mean difference of 40.3 (the crop zoomed, nothing slid) and the pixel rule recorded
# "moved", fixed False - a measurement outranks the kind, so his fixed camera was judged a turning one. For a camera
# whose pan is a crop ('digital') or that has none, only a SLIDE (dx over MOVED_DX) is a turn; and a record already
# written by the pixel rule alone is read as fixed. A motor camera keeps both rules. Kill: RAIL_FIXED_CAM_CROP=0.
def crop_only(rec: dict, env: dict | None = None) -> bool:
    """True when this record's camera only crops and no measured pan slid its picture."""
    e = os.environ if env is None else env
    if _off(e.get("RAIL_FIXED_CAM_CROP", "1")):
        return False
    if str(rec.get("kind") or "").strip().lower() not in ("digital", "none"):
        return False
    last = rec.get("last") if isinstance(rec.get("last"), dict) else {}
    try:
        return float(last.get("dx")) <= MOVED_DX
    except (TypeError, ValueError):
        return False


# A CROP PAN IS FIXED, FULL STOP (round 27, .101 2026-10-16: the Facecam 4K's crop pan slid its picture dx 0.44 at
# 1.5x zoom, the pixel rule recorded "moved", and the door watch ran on his seat: "door_empty_after" dropped his AUTH
# and every face after it was refused "guest_until_secure"; the Secure tiles came back). The User: "The Facecam 4K's pan
# is a crop: it can never see a door outside the lens." A camera whose pan kind is 'digital' (its name is not a motor
# gimbal's, MOTOR_PTZ) or 'none' is fixed whatever a pan check measures; only a 'motor' camera is measured.
# Kill: RAIL_FIXED_CAM_DIGITAL=0 (round 26: the measurement decides, crop_only above).
def digital_fixed(rec: dict | None, health: dict | None = None, env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    if _off(e.get("RAIL_FIXED_CAM_DIGITAL", "1")) or _off(e.get("RAIL_CAMERA_PTZ_BY_MODEL", "1")):
        return False
    h = health if isinstance(health, dict) else {}
    kind = str(h.get("camera_ptz") or (rec or {}).get("kind") or "").strip().lower()
    return kind in ("digital", "none")


# KNOWN FIXED MODELS (round 39, .101 round 38 N14, the User: "maybe just a flag"): cameras whose pan is a crop of a
# fixed sensor whatever they report. RAIL_FIXED_CAM_MODELS (a regex) replaces the list.
KNOWN_FIXED = re.compile(os.environ.get("RAIL_FIXED_CAM_MODELS", r"facecam"), re.I)


def never_turns(rec: dict, env: dict | None = None) -> bool:
    """A camera whose kind is set as fixed once: a digital (crop) pan, no pan, or a known fixed model. A measured move
    never makes it 'turns'. Kill RAIL_FIXED_CAM_KIND_ONCE=0."""
    e = os.environ if env is None else env
    if _off(e.get("RAIL_FIXED_CAM_KIND_ONCE", "1")):
        return False
    kind = str((rec or {}).get("kind") or "").strip().lower()
    return kind in ("digital", "none") or bool(KNOWN_FIXED.search(str((rec or {}).get("camera_name") or "")))


def note_pan_check(before: bytes | None, after: bytes | None, pan_from: int | None, pan_to: int | None, *,
                   env: dict | None = None, home: Path | None = None, now: float | None = None) -> dict:
    """One aim, measured: did a pan of MIN_PAN_DEG or more move the picture? Updates the record; returns it."""
    e = os.environ if env is None else env
    if _off(e.get("RAIL_FIXED_CAM_MEASURE", "1")) or not before or not after:
        return record(home)
    try:
        deg = abs(int(pan_to) - int(pan_from)) / UNITS_PER_DEG
    except (TypeError, ValueError):
        return record(home)
    if deg < MIN_PAN_DEG:
        return record(home)
    got = compare(before, after)
    if got is None:
        return record(home)
    t = time.time() if now is None else now
    with _LOCK:
        rec = record(home)
        if got["moved"] and got["dx"] <= MOVED_DX and crop_only(dict(rec, last=got), e):
            got = dict(got, moved=False, crop=True)      # round 26: zoomed, not turned (see crop_only)
        if got["moved"] and never_turns(rec, e):
            # round 39 (.101 round 38 N14): a digital pan is a crop of a FIXED sensor, and a known fixed model is fixed:
            # a measured move is the crop moving, never a camera that turns; "fixed" is never rewritten false for it
            got = dict(got, moved=False, crop=True)
        if got["moved"]:
            ident = {k: rec[k] for k in ("camera_name", "opened_at", "kind", "noted_at") if k in rec}
            rec = dict(ident, fixed=False, same=0, how="measured: a pan of %.0f deg moved the picture" % deg,
                       at=t, last=got)
        elif got["same"]:
            n = int(rec.get("same") or 0) + 1
            rec.update(same=n, at=t, last=got)
            if n >= NEED_SAME and rec.get("fixed") is not False:
                rec["fixed"] = True
                rec["how"] = "measured: %d pans of %.0f deg or more left the picture where it was" % (n, MIN_PAN_DEG)
        else:
            rec.update(at=t, last=got)                 # unclear (someone moved in front of it): counts for nothing
        _write(rec, home)
    return rec


def note_look(*, name: str | None, sim: float | None, faces: int | None, owner: str, now: float | None = None) -> dict:
    """The oracle's look as /v1/see hands it here: the best face's name and similarity, and how many faces it saw."""
    t = time.time() if now is None else now
    try:
        n = int(faces) if faces is not None else None
    except (TypeError, ValueError):
        n = None
    rec = {"ts": t, "name": str(name or "").strip().lower(), "sim": float(sim) if sim is not None else None,
           "n_faces": n, "owner": str(owner or "").strip().lower()}
    with _LOCK:
        _LOOK.clear()
        _LOOK.update(rec)
    return dict(rec)


def look() -> dict:
    with _LOCK:
        return dict(_LOOK)


def sole(uid: str, *, min_sim: float = 0.60, now: float | None = None, max_age_s: float | None = None,
         fixed: bool = True, on: bool | None = None) -> dict | None:
    """{"uid", "sim", "ts", "n_faces"} when the camera's one face is the User's (see the module's note), else None."""
    if not fixed or not (sole_on() if on is None else on):
        return None
    lk = look()
    t = time.time() if now is None else now
    age_cap = SOLE_MAX_AGE_S if max_age_s is None else max_age_s
    u = str(uid or "").strip().lower()
    if not lk or not u or t - float(lk.get("ts") or 0.0) > age_cap:
        return None
    if lk.get("n_faces") != 1 or lk.get("name") != u or lk.get("owner") != u:
        return None
    if lk.get("sim") is None or float(lk["sim"]) < float(min_sim):
        return None
    return {"uid": u, "sim": float(lk["sim"]), "ts": float(lk["ts"]), "n_faces": 1}


# SECURE NEEDS A CAMERA THAT TURNS (round 17, 2026-10-13; the User 2026-10-11 and 2026-10-13 on .101's fixed Elgato:
# "secure mode working but I am not sure we should without the correct camera ... I should always be in User mode with
# this fixed camera"; "may be confusing"). Secure means the camera sits on the door and AUTH is held for whoever is in
# the room, with a known face in the door frame as the tripwire (visit_lamp). A camera that cannot turn cannot sit on
# the door, and its one view is the User's seat, so his own face trips it. In a pack, on a fixed camera (is_fixed, or
# the camera-kind record's pan kind when no health is to hand) or with no camera, Secure is not offered: the service
# refuses the click without touching AUTH, the restart's guest latch (which waits for a Secure click) no longer holds
# his face ladder, and the lamp's info carries this answer so every Secure key greys with the reason and shows the
# explanation once. A camera of unknown kind (before Setup measured it) keeps Secure. Never on the house (a motor
# camera; is_house). Kill: RAIL_FIXED_CAM_SECURE=0 (round 16: Secure on every camera).
SECURE_WHY = {
    "fixed": ("No turning (360) camera is connected, so door watch is not available. "        # round 26, the User's words
              "Secure needs a camera that can turn to the door."),
    "none": "Secure needs a camera that can turn to the door. Crissa runs with no camera here.",
}
# SECURE IS SECURITY ON'S (round 17, the User 2026-10-13: "secure ON to be as is now ... Maybe then On, ID-only, Off").
# Secure holds AUTH for whoever is in the room and Double Secure holds it on a password; outside Security On no turn is
# decided by identity, so there is nothing for them to hold. On a camera that can turn, ID-only and Off grey them with
# this reason (a fixed camera keeps its own reason above: switching Security cannot make it turn). Kill: the Security
# setting itself (CRISSA_SECURITY_DEFAULT_IDONLY=0 makes On the default).
SECURE_WHY["security"] = "Secure needs Security set to On (the Crissa menu, Security)."
SECURE_ABOUT_SECURITY = ("Secure and its password hold (a double click) keep you signed in as the User while Security "
                         "is On, where only your voice and your face open User mode. Security is ID-only or Off here, "
                         "so no turn waits on who is talking and there is nothing for Secure to hold. Set Security to "
                         "On in the Crissa menu to use Secure.")
SECURE_ABOUT = ("Secure is for a camera that can turn. While it is on, the camera watches the door and keeps you "
                "signed in for whoever is in the room, and a known face at the door turns it off. This camera cannot "
                "turn to the door, so Secure and its password hold (a double click) are off here. You stay the User "
                "by your face at your seat.")


def secure_offer(health: dict | None = None, *, env: dict | None = None, home: Path | None = None) -> dict | None:
    """{"ok", "kind", "why", "about"}: may Secure be offered on this camera? None where the rule does not apply (the
    house, the switch off), so the lamp's info there is as before."""
    e = os.environ if env is None else env
    if is_house():
        return None
    cam_rule = not _off(e.get("RAIL_FIXED_CAM_SECURE", "1"))
    if cam_rule and _on(e.get("CRISSA_NO_CAMERA")):
        return _offer_camera({"ok": False, "kind": "none", "why": SECURE_WHY["none"], "about": SECURE_ABOUT})
    h = dict(health) if isinstance(health, dict) else {}
    if cam_rule and not str(h.get("camera_ptz") or "").strip():
        kind = str(record(home).get("kind") or "").strip().lower()
        if kind in ("motor", "digital", "none"):
            h["camera_ptz"] = kind               # the record's pan kind stands in for a health not to hand
    if cam_rule and is_fixed(h, env=e, home=home):
        return _offer_camera({"ok": False, "kind": "fixed", "why": SECURE_WHY["fixed"], "about": SECURE_ABOUT})
    if not one_toggle() and security_mode() != "on":    # round 17: Secure is Security On's (above)
        return {"ok": False, "kind": "security", "why": SECURE_WHY["security"], "about": SECURE_ABOUT_SECURITY}
    return {"ok": True, "kind": "turns", "camera": "turns", "why": "", "about": ""} if cam_rule else None


def _offer_camera(offer: dict) -> dict:
    """ONE TOGGLE (lib/crissa_home, round 17 follow-ups, the User 2026-10-13): Secure and Double Secure are positions of
    the Security toggle, offered only on a camera that turns. The offer names the camera (`camera`: turns | fixed |
    none) so every surface knows its tree, and on a camera that does not turn it says `hide`: the Secure key is not
    drawn at all there (not greyed) and the Security toggle stands in its place. CRISSA_SECURITY_ONE_TOGGLE=0: round 17's
    greyed key."""
    if isinstance(offer, dict) and offer.get("ok") is False and offer.get("kind") in ("fixed", "none"):
        offer = dict(offer, camera=offer["kind"])
        if one_toggle():
            offer["hide"] = True
    return offer


def _zh():
    try:
        from . import crissa_home as H              # the copy a pack puts beside this file
    except ImportError:
        import crissa_home as H                     # the source tree's (paths.py put lib on the path)
    return H


def security_mode() -> str:
    """The kit's Security setting (lib/crissa_home.security_mode): on | id_only | off; unreadable is on."""
    try:
        m = _zh().security_mode()
    except Exception:  # noqa: BLE001
        return "on"
    return m if m in ("on", "id_only", "off") else "on"


def one_toggle() -> bool:
    """Secure is a position of the Security toggle (lib/crissa_home.one_toggle); never on the house."""
    if is_house():
        return False
    try:
        return bool(_zh().one_toggle())
    except Exception:  # noqa: BLE001
        return False


def faces_idle(env: dict | None = None) -> bool:
    """SECURITY OFF IDLES FACE MATCHING (round 17 follow-ups, the User 2026-10-13): with Security Off no face decides
    anything, so a face scan that runs only to name the User for ID (the visor's scan during a session look) stands
    down; the door watch, which also counts faces and motion for the Wall's rest, keeps running. Kill:
    CRISSA_SECURITY_OFF_IDLES_FACES=0. Never on the house."""
    e = os.environ if env is None else env
    if _off(e.get("CRISSA_SECURITY_OFF_IDLES_FACES", "1")) or is_house():
        return False
    return security_mode() == "off"


def secure_sync(secure: bool, hold: bool, *, why: str = "", turns: bool = True, home: Path | None = None) -> str | None:
    """PRESENCE'S SECURE IS THE TOGGLE'S (ONE TOGGLE above): every publish of the lamp writes its flag back to the
    Security toggle - held: double_secure; Secure: secure; not Secure while the toggle says a Secure position: on, with
    a one-line note (the door tripwire, a click, a restart: Secure leaves to On). `turns` False (this camera does not
    turn, or no longer does): a saved Secure falls back to On with that note. Writes only on a change; returns the
    position written, else None. A pack only."""
    if not one_toggle():
        return None
    try:
        H = _zh()
        saved = str(H.security_record(home).get("mode") or "").strip().lower()
        want = "double_secure" if (secure and hold) else "secure" if secure else None
        if want is None:
            if saved not in H.SECURE_POSITIONS:
                return None
            want = "on"
        if want == saved:
            return None
        if not turns:
            note = H.SECURITY_FIXED_NOTE            # a saved Secure on a camera that does not turn (any more)
        else:
            note = "" if want != "on" else ("Secure ended (%s); Security is On." % why if why else
                                            "Secure ended; Security is On.")
        got = H.set_security_mode(want, by="presence", home_dir=home, note=note)
        return want if got.get("ok") else None
    except Exception:  # noqa: BLE001  the lamp never fails on the toggle
        return None
