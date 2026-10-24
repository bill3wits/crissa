"""Where Crissa keeps its own state: one directory, resolved in one place (2026-09-26).

Every shipped module used to build its flag, lock, pref and cache paths from a
private directory spelled inline, so the exporter's scrub reported the house's
home in ten files of the wall pack. Now they all ask here, and nothing shipped
spells a path.

Resolution, first match wins:
  1. `CRISSA_HOME` in the environment.
  2. `state_home` in the avatar manifest beside this code
     (`avatars/crissa/manifest.json`). The house's own manifest names the
     directory the house has always used, so the house runs byte-for-byte as
     before; the exporter drops the key from a pack's copy.
  3. `~/.crissa`.

Read ONCE at import, like `house_wall_prefs.PATH`: the wall, the float, the
conductor and house-speak all import this module, so they stay on one directory
as long as no unit sets the variable for only one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "avatars" / "crissa" / "manifest.json"
DEFAULT_DIRNAME = ".crissa"


def home(env: dict | None = None, manifest: Path | None = None) -> Path:
    """The state directory. `env` and `manifest` are for tests; callers use HOME."""
    e = os.environ if env is None else env
    got = str(e.get("CRISSA_HOME") or "").strip()
    if got:
        return Path(got).expanduser()
    try:
        cfg = json.loads((MANIFEST if manifest is None else manifest).read_text(encoding="utf-8"))
        got = str(cfg.get("state_home") or "").strip()
    except (OSError, ValueError, AttributeError):
        got = ""
    if got:
        return Path(got).expanduser()
    return Path.home() / DEFAULT_DIRNAME


HOME = home()


# THIS MACHINE'S OWN PATHS LIVE IN CONFIG, NEVER IN CODE (the User 2026-10-06): an interpreter, a model
# cache, a record file the house keeps outside its state directory. The house names them in
# `config/house-machine.json`; a pack carries no such file (the exporter ships named config files only),
# so every caller falls back to a default under this install's own state directory or the user's
# caches. An environment variable, where the caller reads one, still wins over both.
HOUSE_MACHINE = Path(__file__).resolve().parent.parent / "config" / "house-machine.json"


def house_value(key: str, paths: Path | None = None) -> str:
    """This machine's value for `key` from config/house-machine.json, or "" when the file or the key is absent."""
    try:
        cfg = json.loads((HOUSE_MACHINE if paths is None else paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    got = cfg.get(key) if isinstance(cfg, dict) else None
    return got.strip() if isinstance(got, str) else ""


# HER NAME ON THIS INSTALL (the Crissa merge, 2026-10-11): the product and the character are Crissa in a pack (the
# User 2026-10-10, trademark); the house keeps its own name for her, named in config/house-machine.json `her_name`,
# so the merged tree shows the house exactly what it showed before. A pack carries no such file.
PACK_NAME = "Crissa"


def her_name(paths: Path | None = None) -> str:
    """Her name as this install shows and says it: the house's own (config `her_name`), else Crissa."""
    return house_value("her_name", paths) or PACK_NAME


# THE COMPOSER'S ADDRESS (the User 2026-10-07: "the Composer button now links out to store.zoeyos.dev"). A pack
# has no Composer of its own; its button opens the Crissa store. The house keeps its own Composer
# (config/house-machine.json composer_url). Either can be overridden with CRISSA_COMPOSER_URL.
# TODO(crissa-rename 2026-10-09): the store moves to store.crissa.ai once that host exists; until then the
# working address stays store.zoeyos.dev.
COMPOSER_STORE = "https://store.zoeyos.dev"
# ONE NAME, ONE PAGE (the User 2026-10-12: "the Composer arrow takes everyone to a page called Director on
# store.zoeyos.dev ... says nothing about a Composer anywhere - should we call that link something else"). A pack has
# no Composer, so the key that leaves the Wall for the store is called Store and opens the store's catalog (its
# /store/ path), never the Director's portrait upload. The house keeps its own Composer and its name. Off:
# CRISSA_STORE_LINK=0 (the key says Composer again and opens the store's front door).
STORE_PAGE_PATH = "/store/"


def store_link_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_STORE_LINK", "1")).strip().lower() not in ("0", "false", "no", "off")


def composer_url(env: dict | None = None, paths: Path | None = None) -> str:
    """Where the Composer button goes: CRISSA_COMPOSER_URL when set, else this machine's composer_url
    (config/house-machine.json: the house's own Composer), else the Crissa store's catalog."""
    e = os.environ if env is None else env
    got = str(e.get("CRISSA_COMPOSER_URL") or "").strip()
    store = COMPOSER_STORE + STORE_PAGE_PATH if store_link_on(e) else COMPOSER_STORE
    return got or house_value("composer_url", paths) or store


def is_store(url: str) -> bool:
    """Is this address the Crissa store (any page of it)?"""
    u = str(url or "").strip()
    return u == COMPOSER_STORE or u.startswith(COMPOSER_STORE + "/")


def leave_label(env: dict | None = None, paths: Path | None = None) -> str:
    """The key's name: Store when it opens the Crissa store, Composer when it opens a Composer (the house's own,
    or the person's CRISSA_COMPOSER_URL)."""
    return "Store" if store_link_on(env) and is_store(composer_url(env, paths)) else "Composer"


def leave_url(wall_from: str = "", env: dict | None = None, paths: Path | None = None) -> str:
    """composer_url(), and when that is the store, its catalog named with the Wall to come back to (`from`: the
    store's "Try this loop on your wall" and its way back use it; the store takes a loopback or private origin only,
    store_page.safe_wall_origin)."""
    url = composer_url(env, paths)
    if not (store_link_on(env) and is_store(url)):
        return url
    origin = str(wall_from or "").strip()
    if not origin:
        try:
            import crissa_ports
            origin = crissa_ports.url("player", env)
        except Exception:  # noqa: BLE001  no port table: the catalog, without a way back
            origin = ""
    from urllib.parse import quote
    tail = ("?from=" + quote(origin.rstrip("/"), safe="")) if origin else ""
    return COMPOSER_STORE + STORE_PAGE_PATH + tail


def is_house(manifest: Path | None = None) -> bool:
    """Is this the house's own install? Its avatar manifest names `state_home`; the
    exporter drops that key from every pack (2026-09-28). The house keeps laws a pack
    must not: its wall never opens a link through the system opener, because the only
    browser besides the User's is the glass's own."""
    try:
        cfg = json.loads((MANIFEST if manifest is None else manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(str((cfg or {}).get("state_home") or "").strip())

# The glass's own browser profile (the player's Brave). Every other browser on the
# box is the User's: the wall never opens its URLs there, and a chip never spawns on it.
BROWSER_PROFILE = HOME / "display"


def is_own_browser(text: str | None) -> bool:
    """Does this command line, path or option string name the glass's own browser
    profile? Compared as spelled and as resolved, so a symlinked home still matches."""
    s = str(text or "")
    if not s:
        return False
    if str(BROWSER_PROFILE) in s:
        return True
    try:
        r = str(BROWSER_PROFILE.resolve())
    except OSError:
        return False
    return r != str(BROWSER_PROFILE) and r in s


# SECURITY: ON / ID-ONLY / OFF (round 17, the User 2026-10-13: "lets put in an off toggle for face and voice ID on the
# chat ... This is a chatbot which could perform way better with default security off", then "we want default to be
# hybrid ID mode and secure ON to be as is now with only User words and only User face to access User mode. Maybe then
# On, ID-only, Off"). .101 on 2026-10-13 dropped his own words as `no_emb`, held a house command `await_voice` at
# user_voice_sim 0.478, and took 5-10 s to get a line through. One setting, three positions, in <state home>/security.json:
#   on       today's behaviour exactly: User mode only from his voice AND his face, checked every turn; guest limits;
#            Secure and Double Secure as before
#   id_only  THE DEFAULT, also on an install that never chose: voice and face recognition run AFTER a turn is sent and
#            label it ("probably <name>") on the log and for her reference; nothing in the chat waits on them or is
#            decided by them; before a tool call or a house action his face must be at the camera
#   off      no identity check and no gate anywhere; the ear runs no voice recognition at all
# The house is always `on` (it keeps its own identity system). Kill: CRISSA_SECURITY_DEFAULT_IDONLY=0 makes the default
# `on` (round 16's behaviour on an install that never chose); a choice already saved stands.
SECURITY_FILE = "security.json"
SECURITY_MODES = ("on", "id_only", "off")
# ONE TOGGLE (round 17 follow-ups, the User 2026-10-13: "this is all on machines that do not have Secure mode handled.
# I fear this may wreck that logic if not included in the Security switch now. Secure goes away with no door watcher
# anyway. Then the question becomes FF always or FF only for session or none at all ... the camera connection at init
# can decide which logic tree it sits in but you need to handle both with a universal toggle"). Secure and Double
# Secure are positions of this same setting, above On, and only on a camera that turns (presence's camera kind,
# crissa_presence.fixed_cam: Setup's camera-kind.json, else the camera's own pan kind):
#   both trees     off < id_only (the default) < on
#   turning only   on < secure (today's Secure exactly: the camera on the door, AUTH held, the door tripwire)
#                     < double_secure (today's password hold: faces at the door do not end it)
# security_mode() still answers on | id_only | off - Secure is On plus the door - so every round-17 gate reads it as
# before. Presence keeps executing Secure (its own look to raise it, its tripwire, its password) and writes every edge
# of its flag back here (crissa_presence.service._security_sync), so the toggle and the lamp cannot disagree. A saved
# Secure on a camera that no longer turns reads On, with a note. Kill: CRISSA_SECURITY_ONE_TOGGLE=0 (round 17: three
# positions, Secure its own key, greyed outside On).
SECURITY_POSITIONS = ("off", "id_only", "on", "secure", "double_secure")       # low to high
SECURE_POSITIONS = ("secure", "double_secure")
SECURITY_LABELS = {"on": "On", "id_only": "ID-only", "off": "Off", "secure": "Secure", "double_secure": "Double Secure"}
SECURITY_ABOUT = {
    "on": "Only your voice and your face open User mode, checked on every turn; anyone else is a guest. Changing a "
          "setting needs your face at the camera.",
    "id_only": "Anyone at this computer talks to her; she learns who after each line. Tools and house actions need "
               "your face at the camera.",
    "off": "No identity checks at all; this computer's own lock is the security.",
    "secure": "On, and the camera watches the door: you stay the User for whoever is in the room until a known face "
              "comes to the door. Turning it on looks for you at your seat first.",
    "double_secure": "Secure held by this computer's login password: faces at the door do not end it. Lowering it "
                     "needs your face at the camera.",
}
SECURITY_FIXED_NOTE = "Secure needs a camera that can turn to the door; this camera does not turn, so Security is On."
# NO DOOR WATCH ON THIS CAMERA (round 26, the User 2026-10-16 on .101's fixed Elgato: "The dot should offer that no 360
# camera is connected so door watch not available"). Kill: CRISSA_SECURITY_NO_DOOR_NOTE=0.
SECURITY_NO_DOOR_NOTE = "No turning (360) camera is connected, so door watch (Secure) is not available."
SECURITY_NO_DOOR_ON = os.environ.get("CRISSA_SECURITY_NO_DOOR_NOTE", "1").strip().lower() not in ("0", "false", "no", "off")
_OFFS = ("0", "false", "no", "off")
_SEC_CACHE: dict = {}


# A LAPTOP DEFAULTS TO OFF (round 28, the User 2026-10-17 on .101: "I put security off so it was usable ... Maybe on a
# laptop we default to security off but keep the same init routine for if turned on"). An install that never chose, on
# a camera not known to turn (a laptop's, a webcam's crop-only pan, or none: presence's camera-kind.json, the record
# Setup's room step reads), starts Off; Setup still banks his face and voice, so On works the day he turns it on (a
# 30 s session there, crissa_presence.laptop_auth). A camera that turns keeps ID-only. Kill:
# CRISSA_LAPTOP_SECURITY_OFF=0 (round 27: ID-only on every camera).
def camera_kind_record(home_dir: Path | None = None) -> dict:
    try:
        rec = json.loads((Path(home_dir or HOME) / "presence" / "camera-kind.json").read_text(encoding="utf-8"))
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def kind_turns(rec: dict) -> bool:
    """Does a camera-kind record say the camera TURNS? A motor does; a measured move counts only on a camera whose pan is
    not a crop (round 39, .101 round 38 N14: the fixed Elgato Facecam 4K's digital pan "moved the picture", presence
    wrote fixed false, and Security flipped Off -> ID only with no one choosing). Kill CRISSA_DIGITAL_NEVER_TURNS=0."""
    kind = str((rec or {}).get("kind") or "").strip().lower()
    if kind == "motor":
        return True
    if (rec or {}).get("fixed") is not False:
        return False
    if kind in ("digital", "none") and str(os.environ.get("CRISSA_DIGITAL_NEVER_TURNS", "1")).strip().lower() not in _OFFS:
        return False
    return True


def camera_known_to_turn(home_dir: Path | None = None) -> bool:
    return kind_turns(camera_kind_record(home_dir))


def security_default(env: dict | None = None, home_dir: Path | None = None) -> str:
    """The mode on an install that never chose: off on a laptop (a camera not known to turn), else id_only; on with
    CRISSA_SECURITY_DEFAULT_IDONLY=0."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_SECURITY_DEFAULT_IDONLY", "1")).strip().lower() in _OFFS:
        return "on"
    if str(e.get("CRISSA_LAPTOP_SECURITY_OFF", "1")).strip().lower() not in _OFFS and not camera_known_to_turn(home_dir):
        return "off"
    return "id_only"


def one_toggle(env: dict | None = None) -> bool:
    """Secure and Double Secure are positions of the Security toggle (ONE TOGGLE above); CRISSA_SECURITY_ONE_TOGGLE=0
    is round 17 before it. Never on the house (its Secure is its own)."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_SECURITY_ONE_TOGGLE", "1")).strip().lower() not in _OFFS and not is_house()


def security_path(home_dir: Path | None = None) -> Path:
    return Path(home_dir or HOME) / SECURITY_FILE


def security_record(home_dir: Path | None = None) -> dict:
    """The saved record ({"mode", "by", "at", "note"}), {} when none or unreadable. One stat a call; re-read only when
    the file changed."""
    p = security_path(home_dir)
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    got = _SEC_CACHE.get(key)
    if got is None:
        try:
            got = json.loads(p.read_text(encoding="utf-8"))
            got = got if isinstance(got, dict) else {}
        except (OSError, ValueError):
            got = {}
        _SEC_CACHE.clear()
        _SEC_CACHE[key] = got
    return dict(got)


def _saved_mode(home_dir: Path | None = None) -> str:
    return str(security_record(home_dir).get("mode") or "").strip().lower().replace("-", "_")


def security_mode(env: dict | None = None, home_dir: Path | None = None, manifest: Path | None = None) -> str:
    """on | id_only | off for this install (see SECURITY_MODES above). The house: always on. Read on every call (one
    stat, the file re-read only when it changed), so a change on the menu reaches the next turn with no restart.
    Secure and Double Secure are On (ONE TOGGLE)."""
    if is_house(manifest):
        return "on"
    got = _saved_mode(home_dir)
    if got in SECURE_POSITIONS:
        return "on"
    if got in SECURITY_MODES:
        return got
    d = security_default(env, home_dir)
    _keep_default(d, env, home_dir)
    return d


def _keep_default(mode: str, env: dict | None = None, home_dir: Path | None = None) -> None:
    """THE MODE NEVER CHANGES UNLESS A PERSON CHOOSES IT (round 39, .101 round 38 N14). Once Setup is done, the default
    the install starts with is written to security.json (by "the install's default") and said in the log, so a later
    reading of the camera can never move it. Kill CRISSA_SECURITY_DEFAULT_KEEP=0 (the default follows the camera)."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_SECURITY_DEFAULT_KEEP", "1")).strip().lower() in _OFFS:
        return
    try:
        done = bool(json.loads((Path(home_dir or HOME) / "init.json").read_text(encoding="utf-8")).get("done"))
    except (OSError, ValueError, AttributeError):
        done = False
    if not done:
        return
    got = set_security_mode(mode, by="the install's default", home_dir=home_dir,
                            note="kept at Setup's end so it changes only when a person chooses")
    if got.get("ok"):
        print("[security] the install's default is kept: %s (Security changes only when a person chooses)" % mode,
              flush=True)


def camera_turns(kind: str | None) -> bool | None:
    """True for a camera that turns, False for a fixed camera or none, None when not known (presence not asked yet)."""
    k = str(kind or "").strip().lower()
    if k in ("turns", "turning", "motor"):
        return True
    if k in ("fixed", "digital", "none", "no_camera"):
        return False
    return None


def camera_kind_of(presence: dict | None) -> str:
    """'turning' | 'fixed' | 'none' | '' from presence's published state: its Secure offer (fixed_cam.secure_offer,
    `camera` or `kind`), else a fixed look's face block."""
    p = presence if isinstance(presence, dict) else {}
    for src in (p, p.get("presence") if isinstance(p.get("presence"), dict) else {}):
        info = src.get("visit_lamp_info") if isinstance(src.get("visit_lamp_info"), dict) else {}
        offer = info.get("secure_offer") if isinstance(info.get("secure_offer"), dict) else {}
        for k in (offer.get("camera"), offer.get("kind")):
            t = camera_turns(k)
            if t is not None:
                return "turning" if t else ("none" if str(k).strip().lower() in ("none", "no_camera") else "fixed")
    face = p.get("face") if isinstance(p.get("face"), dict) else {}
    return "fixed" if face.get("fixed") else ""


# A LAPTOP'S SPOT CHECK (round 27, the User 2026-10-16: "If no auth, a 'face check requested' spot check"): presence
# on a camera that does not turn publishes visit_lamp_info.face_check "spot" (crissa_presence.laptop_auth), and its
# look is one picture matched now, so the sequence asks for that look instead of waiting on the published face block.
# Kill: CRISSA_LAPTOP_SPOT_CHECK=0 (the 5 s wait on a fixed camera).
def spot_check_of(presence: dict | None, env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_LAPTOP_SPOT_CHECK", "1")).strip().lower() in _OFFS:
        return False
    p = presence if isinstance(presence, dict) else {}
    for src in (p, p.get("presence") if isinstance(p.get("presence"), dict) else {}):
        info = src.get("visit_lamp_info") if isinstance(src.get("visit_lamp_info"), dict) else {}
        if info.get("face_check") == "spot":
            return True
    return False


def security_positions(camera: str | None = "", env: dict | None = None) -> tuple:
    """The positions this install offers, as the menus list them (highest first, round 17's order): Secure and Double
    Secure only on a camera that turns."""
    if one_toggle(env) and camera_turns(camera) is True:
        return tuple(reversed(SECURITY_POSITIONS))
    return SECURITY_MODES


def security_position(env: dict | None = None, home_dir: Path | None = None, manifest: Path | None = None,
                      camera: str | None = "") -> str:
    """off | id_only | on | secure | double_secure (ONE TOGGLE). The house: on. A saved Secure on a camera known not to
    turn reads on (security_note says why); a camera not known yet keeps what was saved."""
    if is_house(manifest):
        return "on"
    got = _saved_mode(home_dir)
    if got in SECURE_POSITIONS:
        if not one_toggle(env) or camera_turns(camera) is False:
            return "on"
        return got
    return got if got in SECURITY_MODES else security_default(env, home_dir)


def security_note(home_dir: Path | None = None, camera: str | None = "") -> str:
    """One line when the toggle is not what was saved (a Secure on a camera that does not turn), or the note presence
    left (Secure ended by its own law); '' otherwise."""
    rec = security_record(home_dir)
    if str(rec.get("mode") or "") in SECURE_POSITIONS and camera_turns(camera) is False:
        return SECURITY_FIXED_NOTE
    if not rec.get("note") and str(camera or "").strip().lower() in ("fixed", "none") and SECURITY_NO_DOOR_ON:
        return SECURITY_NO_DOOR_NOTE        # round 26: why there is no Secure here, said where the toggle is
    return str(rec.get("note") or "")


def security_rank(position: str) -> int:
    p = str(position or "").strip().lower().replace("-", "_")
    return SECURITY_POSITIONS.index(p) if p in SECURITY_POSITIONS else SECURITY_POSITIONS.index("on")


def set_security_mode(mode: str, by: str = "", home_dir: Path | None = None, note: str = "") -> dict:
    """Save the position ({"ok", "mode"} or {"ok": False, "error"}). Never on the house. Secure and Double Secure are
    taken only under ONE TOGGLE; whether this camera offers them is the caller's (crissa_menu.set_security)."""
    m = str(mode or "").strip().lower().replace("-", "_")
    allowed = SECURITY_POSITIONS if one_toggle() else SECURITY_MODES
    if m not in allowed:
        return {"ok": False, "error": "unknown security mode %r (one of %s)" % (mode, ", ".join(allowed))}
    if is_house():
        return {"ok": False, "error": "the house keeps its own identity system"}
    p = security_path(home_dir)
    try:
        import time as _t
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        rec = {"mode": m, "by": str(by or "")[:40], "at": round(_t.time(), 3)}
        if note:
            rec["note"] = str(note)[:200]
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        return {"ok": False, "error": "could not save the setting (%s)" % type(e).__name__}
    return {"ok": True, "mode": m}


# A STRANGER IS LOCKED OUT OF ADJUSTING (the User 2026-10-13: "Switching security from off to on yes, anyone. But a
# stranger should then be locked out from any further adjusting. All tools or switches requesting auth gets face match
# sequence. Turning on Security should not require Auth"):
#   (a) raising Security - any step up, to Secure as well - needs no proof (Secure's own look still runs: that is
#       today's Secure)
#   (b) while Security is On or above, every other change of a setting or switch - from both Walls' menus, the kit's
#       Control Panel, presence's Secure keys, any route that saves one - needs the User's face first; lowering
#       Security from On or above is one of them
#   (c) one FACE-MATCH SEQUENCE (face_sequence below) for every switch and tool call that needs it, ID-only's
#       before-action check included
#   (d) what only stops or quiets her stays open to anyone and never asks for a face: OPEN_TO_ANYONE
# Kills: CRISSA_SECURITY_LOCK_ADJUST=0 ((b) and (c): no lock on settings; ID-only's check is round 17's again);
# CRISSA_SECURITY_LOWER_NEEDS_FACE=0 (lowering Security alone needs no face). Never on the house.
OPEN_TO_ANYONE = {
    "agent_close": "Close Agent",
    "stop": "All OS Off (the menu's Stop Crissa until 2026-10-12)",
    "mic_off": "Mic off (her ear stops listening)",
    "pause": "Pause (her ear paused)",
    "cancel": "Cancel (a spoken abort)",
    "wall_close": "Close the Wall",
    "unit_stop": "Stop on one of her lanes (the Control Panel)",
}


def lock_adjust_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_SECURITY_LOCK_ADJUST", "1")).strip().lower() not in _OFFS


def lower_needs_face_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_SECURITY_LOWER_NEEDS_FACE", "1")).strip().lower() not in _OFFS


def adjust_needs_face(action: str, *, current: str | None = None, target: str | None = None,
                      env: dict | None = None, home_dir: Path | None = None, camera: str | None = "") -> str:
    """Does this change need the User's face first? '' = no; 'lower' = lowering Security from On or above; 'lock' =
    a setting or switch while Security is On or above (the rules above). `action` names the change ('security' with
    `target` for the toggle itself); `current` is the position now (read when None)."""
    if is_house():
        return ""
    a = str(action or "").strip().lower()
    if a in OPEN_TO_ANYONE:
        return ""
    cur = str(current or security_position(env, home_dir, camera=camera))
    high = security_rank(cur) >= security_rank("on")
    if a == "security":
        if security_rank(target or cur) >= security_rank(cur):
            return ""                                        # (a) raising, or staying: anyone
        return "lower" if (high and lower_needs_face_on(env)) else ""
    return "lock" if (high and lock_adjust_on(env)) else ""


# THE FACE-MATCH SEQUENCE ((c) above). One read of presence: his face there now (the live-face rule below) goes at
# once. Else he is asked, plainly, to look at the camera for a moment (`ask`: she says it, or the menu shows it); on a
# camera that turns, presence's own FaceFinder look at his seat is the check (`look`: POST /v1/visit_lamp {op:
# "face_check"}); on a fixed camera presence's face block is read again every FACE_SEQ_POLL_S for up to FACE_SEQ_WAIT_S.
# Seen: the change goes on. Not seen: the plain line, and nothing changes.
# WHY 5 S (CRISSA_FACE_SEQ_WAIT_S): from the moment he turns to the camera, a fixed camera's face block needs the
# oracle's next look (1 s apart while a face is in view, 3 s while it rests on an empty view) plus presence's next tick
# to publish it (1.2-2 s): about 3 s, 5 s from a resting look. Under that a resting oracle fails a person who did look;
# much over it a person who is not there waits for nothing. A turning camera's look (aim, settle, two shots) is its own
# wait, ~2-4 s.
# THE LIVE-FACE RULE is presence's (and the ear's, modules/ear/crissa_bridge/user_mode._live_face_user; a test pins
# the two equal): the face block names the User at 0.60 (0.40 for a profile while the camera looks at him), FRESH by
# presence's own clock - 2.5 s on a camera that turns (one missed visor tick of 1.2 s), 6 s on a fixed camera (its
# look once a second inside presence's 1.2-2 s tick). Same environment knobs as the ear's.
FACE_SEQ_ASK = "Look at the camera for a moment."
FACE_SEQ_FAIL = "I couldn't see your face at the camera, so nothing changed."
FACE_SEQ_NO_CAMERA = "I couldn't see your face: I can't reach the camera right now, so nothing changed."
FACE_SEQ_POLL_S = 0.4


def face_seq_wait_s(env: dict | None = None) -> float:
    e = os.environ if env is None else env
    try:
        return max(0.0, float(e.get("CRISSA_FACE_SEQ_WAIT_S", "5") or 0))
    except (TypeError, ValueError):
        return 5.0


def _envf(name: str, default: float, env: dict | None = None) -> float:
    e = os.environ if env is None else env
    try:
        return float(e.get(name, default))
    except (TypeError, ValueError):
        return default


def presence_user(presence: dict | None) -> str:
    """The User's id as presence publishes it ('' when it names none)."""
    p = presence if isinstance(presence, dict) else {}
    u = p.get("user") if isinstance(p.get("user"), dict) else {}
    return str(p.get("user_id") or u.get("user_id") or "").strip().lower()


def live_face(presence: dict | None, uid: str | None = None, env: dict | None = None) -> tuple:
    """(ok, sim, why): is the User's face at the camera now? why: face | not_seen | stale | no_camera | no_user."""
    p = presence if isinstance(presence, dict) else {}
    if not p or p.get("_err") or (p.get("error") and not isinstance(p.get("face"), dict)):
        return False, None, "no_camera"
    u = str(uid or presence_user(p) or "").strip().lower()
    if not u:
        return False, None, "no_user"
    buf = p.get("face") if isinstance(p.get("face"), dict) else {}
    if not buf:
        return False, None, "not_seen"
    faces = buf.get("faces") if isinstance(buf.get("faces"), list) else []
    rec = next((f for f in faces if isinstance(f, dict) and str(f.get("name") or "").strip().lower() == u), None)
    locked = str(buf.get("locked") or "").strip().lower()
    sim = None
    if rec is not None and rec.get("sim") is not None:
        try:
            sim = float(rec["sim"])
        except (TypeError, ValueError):
            sim = None
    looking = bool(p.get("f2f") or buf.get("f2f"))
    pending = bool(buf.get("pending_look") or p.get("pending_look"))
    try:
        age = float(buf["age_s"]) if buf.get("age_s") is not None else None
    except (TypeError, ValueError):
        age = None
    e = os.environ if env is None else env
    not_pending = str(e.get("CRISSA_FIXED_LOOK_NOT_PENDING", "1")).strip().lower() not in _OFFS
    if pending and not (buf.get("fixed") and not_pending):
        return False, sim, "stale"                           # a look in flight: not seen yet
    src = str(buf.get("source") or "").strip().lower()
    if not (rec or locked or faces or buf.get("n_faces") or buf.get("ts") or (src and src not in ("idle", "none"))):
        return False, None, "not_seen"
    window = _envf("HOUSE_FIXED_FACE_AGE_S", 6.0, e) if buf.get("fixed") else _envf("HOUSE_LIVE_FACE_AGE_S", 2.5, e)
    fresh = bool(age is not None and age <= window)
    floor = _envf("HOUSE_LIVE_FACE_MIN_SIM", 0.60, e)
    if looking:
        floor = min(floor, _envf("HOUSE_LIVE_PROFILE_MIN_SIM", 0.40, e))
    hit = rec is not None or locked == u
    if hit and (sim is None or sim >= floor) and fresh:
        return True, sim, "face"
    return False, sim, "not_seen" if fresh else "stale"


def face_line(why: str) -> str:
    """The plain line for a face not seen ('' when it was)."""
    if why == "face":
        return ""
    return FACE_SEQ_NO_CAMERA if why == "no_camera" else FACE_SEQ_FAIL


def face_sequence(fetch=None, *, uid: str | None = None, presence: dict | None = None, ask=None, look=None,
                  wait_s: float | None = None, poll_s: float = FACE_SEQ_POLL_S, sleep=None, clock=None,
                  env: dict | None = None) -> dict:
    """THE FACE-MATCH SEQUENCE (above): {"ok", "why", "sim", "asked", "looked", "waited_s", "line", "presence"}.
    `fetch()` reads presence's /v1/presence; `presence` is a read already in hand (else fetch is read first); `ask(line)`
    says or shows FACE_SEQ_ASK; `look()` is a camera that turns' own look ({"found", "sim"}), used instead of waiting
    when the camera turns (camera_kind_of). `line` is the plain words for a failure ('' when seen)."""
    import time as _t
    sleep = _t.sleep if sleep is None else sleep
    clock = _t.monotonic if clock is None else clock
    p = presence if isinstance(presence, dict) else None
    if p is None:
        try:
            p = (fetch() if fetch is not None else {}) or {}
        except Exception:  # noqa: BLE001  presence down: no camera to ask
            p = {"_err": "unreachable"}
    ok, sim, why = live_face(p, uid, env)
    out = {"ok": ok, "why": why, "sim": sim, "asked": False, "looked": False, "waited_s": 0.0, "presence": p}
    if ok or why in ("no_user",):
        out["line"] = face_line(why) if not ok else ""
        return out
    t0 = clock()
    if ask is not None:
        try:
            ask(FACE_SEQ_ASK)
            out["asked"] = True
        except Exception:  # noqa: BLE001  an ask that cannot be said still waits for the face
            pass
    wait = face_seq_wait_s(env) if wait_s is None else max(0.0, float(wait_s))
    if look is not None and (camera_kind_of(p) == "turning" or spot_check_of(p, env)):
        out["looked"] = True
        try:
            got = look() or {}
        except Exception:  # noqa: BLE001
            got = {"error": "look failed"}
        if got.get("found"):
            ok, sim, why = True, got.get("sim"), "face"
        elif got.get("error") and not got.get("checked", True):
            why = "no_camera"
        else:
            why = "not_seen"
    else:
        while not ok and fetch is not None and clock() - t0 < wait:
            sleep(poll_s)
            try:
                p = fetch() or {}
            except Exception:  # noqa: BLE001
                p = {"_err": "unreachable"}
            ok, sim, why = live_face(p, uid, env)
        out["presence"] = p
    out.update(ok=ok, sim=sim, why=why, waited_s=round(clock() - t0, 2), line="" if ok else face_line(why))
    return out


def adjust_gate(action: str, *, fetch=None, ask=None, look=None, target: str | None = None, presence: dict | None = None,
                uid: str | None = None, env: dict | None = None, home_dir: Path | None = None,
                wait_s: float | None = None, sleep=None, clock=None) -> dict | None:
    """Before a change of a setting or switch: None = go on (no face needed, or his face seen); else the refusal
    {"ok": False, "face": "refused", "error": <the plain line>, "why", "needs"} and nothing may change. The camera tree
    comes from presence's own state (camera_kind_of)."""
    if is_house():
        return None
    a = str(action or "").strip().lower()
    if a in OPEN_TO_ANYONE:
        return None
    if presence is None and fetch is not None:
        try:
            presence = fetch() or {}
        except Exception:  # noqa: BLE001
            presence = {"_err": "unreachable"}
    cam = camera_kind_of(presence)
    needs = adjust_needs_face(a, target=target, env=env, home_dir=home_dir, camera=cam)
    if not needs:
        return None
    got = face_sequence(fetch, uid=uid, presence=presence, ask=ask, look=look, wait_s=wait_s, sleep=sleep, clock=clock,
                        env=env)
    if got["ok"]:
        return None
    return {"ok": False, "face": "refused", "needs": needs, "why": got["why"], "error": got["line"],
            "asked": got["asked"], "action": a}


def owner_id(home_dir: Path | None = None) -> str:
    """The User's id on this install (<state home>/user.json person_id), '' before Setup named one."""
    try:
        u = json.loads((Path(home_dir or HOME) / "user.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str((u or {}).get("person_id") or "").strip().lower() if isinstance(u, dict) else ""


def face_gate(action: str, *, fetch=None, look=None, target: str | None = None, presence: dict | None = None,
              home_dir: Path | None = None, owner: str | None = None, env: dict | None = None) -> dict | None:
    """THE ONE CALL every setting route of the kit makes before it changes anything (the Crissa menu, the Control
    Panel, the feed, Setup, Connect): None = go on; else the refusal {"ok": False, "face": "refused", "error", "ask",
    ...} and nothing may change. With no User on this install yet there is nothing to protect (Voice ID's rule)."""
    if is_house():
        return None
    own = owner_id(home_dir) if owner is None else str(owner or "").strip().lower()
    if not own:
        return None
    got = adjust_gate(action, fetch=fetch, look=look, target=target, presence=presence,
                      uid=presence_user(presence) or own, env=env, home_dir=home_dir)
    if got is not None:
        got["ask"] = FACE_SEQ_ASK
    return got


# THE GLASS'S OWN VIEW IS NOT A SETTING: which panels are folded, which log chips show, the face panel's mode, size and
# fade, the oracle dot - where a person at the screen looks - stay open; how the glass draws her (brightness, density,
# blur, her film's zoom) is a setting like lip sync and is locked while Security is On.
GLASS_VIEW_KEYS = frozenset(("ft_mode", "ft_size", "ft_fade", "vitals_folded", "pip_bar", "ft_bar", "oracle_dot", "rev"))


def glass_gate_name(body: dict | None) -> str:
    """'' for a glass prefs change that only moves the glass's own view (those keys, and every log_* chip of the LOGS
    roll), else "glass_prefs" (locked under On)."""
    keys = set((body or {}).keys()) if isinstance(body, dict) else set()
    return "" if all(k in GLASS_VIEW_KEYS or str(k).startswith("log_") for k in keys) else "glass_prefs"


SECURITY_ITEM_LABEL = "Security: %s\u2026"


def security_state(home_dir: Path | None = None, presence: dict | None = None) -> dict:
    """The Security item every surface draws: {"mode", "label", "chosen", "default", "camera", "note", "lock", "ask",
    "modes": [{"mode", "label", "about", "on", "face", "password"}]} - the positions this camera's tree offers, `lock`
    (every change but a stop needs his face now), a mode's `face` (choosing it needs his face) and `password` (Double
    Secure asks for the login password)."""
    cam = camera_kind_of(presence) if isinstance(presence, dict) else ""
    m = security_position(home_dir=home_dir, camera=cam)
    lock = bool(adjust_needs_face("lip_sync", current=m, home_dir=home_dir, camera=cam))
    return {"mode": m, "label": SECURITY_ITEM_LABEL % SECURITY_LABELS.get(m, m),
            "chosen": security_path(home_dir).is_file(), "default": security_default(home_dir=home_dir),
            "camera": cam, "note": security_note(home_dir, cam), "lock": lock, "ask": FACE_SEQ_ASK if lock else "",
            "modes": [{"mode": k, "label": SECURITY_LABELS[k], "about": SECURITY_ABOUT[k], "on": k == m,
                       "face": bool(adjust_needs_face("security", current=m, target=k, home_dir=home_dir, camera=cam)),
                       "password": k == "double_secure" and m != "double_secure"}
                      for k in security_positions(cam)]}
