"""Who is in the room for a Crissa install (2026-10-08).

A small presence service: Secure, Guest, and the owner's own enrollment. It does not open a camera
and it does not ship anyone's face. Init writes enroll.json (numbers only). This process reads it.

The lamp:

* Secure already on stays on when the reason starts with ``occupancy:`` (a mode switch is not a
  restart). Sage off returns the lamp as it is: no guest latch and no look.
* Any other reason uses a disk token. A matching look turns Secure on. A miss guests and burns
  the token. A skipped look (nothing posted, a lease, a cooldown) keeps the token.
* A password hold is this process only. A token may remember that a hold existed; the next
  process still needs a look, and the hold does not come back from the file.
* Boot does not restore a saved lamp. ``load_secure`` is always false.
* With Secure off, the owner's recognition drops after a few seconds unless a look refreshes it.

Loopback only. The default port is 8219 (lib/crissa_ports). Tests bind a free port.

What else this starts:

* The launcher starts this process. The watch brings it back if it stops. Gateways and the janitor stay as they are.
* A mode switch posts secure_reauth. Sage stays sage. With sage off, the lamp is left as it is.
* The ear adds a speaker only when CRISSA_PRESENCE_URL is set. Oracle is not started from here.
* Init may store number-lists for the owner, or skip. No picture is kept.
* The Control Panel lists one Presence row under Wall services.
* A guest gets a polite answer, no remembered turns, and no command routes.
* A Mac is not offered Secure. A click while held turns the hold and Secure off. A release keeps Secure.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

try:
    from crissa_home import HOME
except ImportError:
    HOME = Path(os.environ.get("CRISSA_HOME") or (Path.home() / ".crissa")).expanduser()

ENROLL_SCHEMA = "crissa-enroll/1"
TOKEN_SCHEMA = "crissa-secure-token/1"
HOLD_REASON = "keyboard_hold"
# Measured 2026-10-08 on the reference box (AuraFace glintr100 embeddings, MediaPipe detection; the enrolled user's
# 89 frames against a 13-frame impostor set): genuine median 0.881, min 0.540, p95 0.936; impostor max 0.354. The
# threshold sits in the middle of that gap, 0.19 under the weakest genuine frame and 0.10 over the strongest impostor
# (the User 2026-10-08: "pack's threshold should be set from that gap"). CRISSA_FACE_MATCH overrides it.
MATCH = float(os.environ.get("CRISSA_FACE_MATCH", "0.45"))
AUTH_OFF_S = 5.0
MAX_FACES = 8
DIM_MIN = 8
DIM_MAX = 4096
BODY_MAX = 1_000_000
DEFAULT_PORT = 8219
HOUSE_PORT = 8191

# A look that may mint recognition. A door still, a ring, or a guess may not.
FF_GRANT_SOURCES = (
    "visit_lamp_click", "secure_token", "restart_token", "beam_look", "desk_ff", "desk",
    "facefind", "door_chase_ff", "door_ff", "reauth_ladder_ff", "unlock_ff",
    "busy_auth_scan", "busy_off_recheck", "operator",
    "oracle",                       # the pack's own camera (modules/oracle), 2026-10-08
)
DEFERRED = ("leased", "pan_cooldown", "hunt_cooldown", "no_face", "error")
PICTURE_KEYS = {"image", "jpeg", "jpg", "png", "photo", "picture", "wav", "image_b64", "webp"}
PICTURE_SUFFIX = re.compile(r"\.(jpe?g|png|webp|gif|bmp|wav|mp4)$", re.I)
BAD_ID = re.compile(r"^(unknown|crowd)(?:[:_].*)?$|^__|^(?:u|f)\d+$", re.I)

GUEST_NOTE = (
    "The person speaking is a guest. Answer politely. "
    "Do not run commands, do not use tools, and do not share the owner's data."
)

_LOCK = threading.RLock()
_READER: Callable[[str], Any] | None = None
_GUEST = {"at": 0.0, "url": "", "value": False}


def secure_offered(env=None) -> bool:
    """A Mac is never offered Secure. Every other platform is."""
    env = os.environ if env is None else env
    plat = str(env.get("CRISSA_PLATFORM") or sys.platform)
    return plat != "darwin"


def port(env=None) -> int:
    """This install's presence port. Never the house service's port."""
    try:
        import crissa_ports
        n = int(crissa_ports.port("presence", env))
    except Exception:  # noqa: BLE001
        n = DEFAULT_PORT
    if n == HOUSE_PORT:
        raise RuntimeError("presence does not bind the house service's port")
    return n


def is_ff_source(source: str | None) -> bool:
    s = str(source or "").strip().lower()
    if not s:
        return False
    return any(s == k or s.startswith(k + ":") or s.startswith(k + "_") for k in FF_GRANT_SOURCES)


def owner_slug(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", str(name or ""))
    return "-".join(w.lower() for w in words)[:40].strip("-")


def bad_id(uid: str) -> bool:
    s = str(uid or "").strip().lower()
    return (not s) or bool(BAD_ID.match(s))


def mint(uid: str, *, now: float, reason: str, hold: bool = False, token_id: str | None = None) -> dict:
    tid = str(token_id or "").strip() or secrets.token_hex(16)
    return {"schema": TOKEN_SCHEMA, "id": tid, "uid": str(uid or "").strip().lower(),
            "hold": bool(hold), "ts": float(now), "reason": str(reason or "click")}


def valid(token: dict | None, uid: str) -> bool:
    if not isinstance(token, dict):
        return False
    tid = str(token.get("id") or "").strip()
    got = str(token.get("uid") or "").strip().lower()
    want = str(uid or "").strip().lower()
    return bool(tid and got and want and got == want)


def decide(token: dict | None, uid: str) -> dict:
    if not valid(token, uid):
        why = "no_token" if not token else "token_uid"
        return {"act": "guest", "why": why, "hold": False, "id": None}
    return {"act": "reauth", "why": "token", "hold": bool(token.get("hold")), "id": str(token.get("id") or "")}


def _finite_row(row: Any) -> list[float] | None:
    if not isinstance(row, list) or not (DIM_MIN <= len(row) <= DIM_MAX):
        return None
    try:
        nums = [float(x) for x in row]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(x) for x in nums):
        return None
    return nums


def vectors_from(raw: Any) -> list[list[float]] | None:
    """A list of number-lists, or one number-list. None when the shape is wrong."""
    if isinstance(raw, dict):
        raw = raw.get("faces", raw.get("vectors"))
    if not isinstance(raw, list) or not raw:
        return None
    if raw and isinstance(raw[0], (int, float)):
        raw = [raw]
    out: list[list[float]] = []
    for row in raw:
        nums = _finite_row(row)
        if nums is None:
            return None
        out.append(nums)
        if len(out) > MAX_FACES:
            return None
    return out


def accept_faces(body: Any) -> tuple[list[list[float]] | None, str]:
    """Numbers only. A picture key or a picture path is refused."""
    if not isinstance(body, dict):
        vecs = vectors_from(body)
        return (vecs, "") if vecs is not None else (None, "a list of numbers")
    if PICTURE_KEYS & {str(k).lower() for k in body}:
        return None, "a picture is not kept"
    path = str(body.get("path") or "")
    if path:
        if PICTURE_SUFFIX.search(path):
            return None, "a picture is not kept"
        if not path.lower().endswith(".json"):
            return None, "a json file of numbers"
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return None, "that file could not be read (%s)" % type(exc).__name__
        if isinstance(loaded, dict) and (PICTURE_KEYS & {str(k).lower() for k in loaded}):
            return None, "a picture is not kept"
        vecs = vectors_from(loaded)
        return (vecs, "") if vecs is not None else (None, "a list of numbers")
    vecs = vectors_from(body.get("faces") if "faces" in body else body.get("vectors"))
    return (vecs, "") if vecs is not None else (None, "a list of numbers")


def write_enroll(home: Path, owner: str, faces: list[list[float]]) -> dict:
    """enroll.json, mode 0600. The owner slug and number-lists. No picture."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    doc = {"schema": ENROLL_SCHEMA, "owner": owner_slug(owner) or str(owner or "").strip().lower(),
           "faces": faces, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    path = home / "enroll.json"
    tmp = path.with_name(".enroll.json.%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return doc


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def clear_guest_cache() -> None:
    _GUEST.update(at=0.0, url="", value=False)


def set_reader(fn: Callable[[str], Any] | None) -> None:
    """Tests hand a presence payload without opening a socket."""
    global _READER
    _READER = fn
    clear_guest_cache()


def _read_presence(url: str) -> Any:
    if _READER is not None:
        return _READER(url)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=0.4) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def guest_now(env=None) -> bool:
    """True when this person is not confirmed as the owner.

    No address: not a guest. An address that does not answer, or a payload that
    does not say the owner is here: a guest, so commands stay closed.
    """
    env = os.environ if env is None else env
    url = str(env.get("RAIL_PRESENCE") or "").strip()
    if not url:
        return False
    now = time.time()
    if _GUEST["url"] == url and now - float(_GUEST["at"]) < 1.0:
        return bool(_GUEST["value"])
    value = True
    try:
        data = _read_presence(url)
        if isinstance(data, dict) and data.get("user_auth") is True:
            value = False
    except (OSError, ValueError, urllib.error.URLError):
        value = True
    _GUEST.update(at=now, url=url, value=value)
    return value


def commands_blocked(env=None) -> bool:
    return guest_now(env)


def guest_cfg(cfg: dict, env=None) -> dict:
    """The ask config, plus a guest note when the room says this person is not the owner."""
    if not isinstance(cfg, dict) or not guest_now(env):
        return cfg
    import crissa_agent
    base = crissa_agent.system_message(cfg) or ""
    out = dict(cfg)
    out["system_prompt"] = (base + "\n\n" + GUEST_NOTE).strip()
    out["no_prompt"] = False
    return out


class Service:
    """The lamp and the recognition flag for one install. ``home`` is the state directory."""

    def __init__(self, home: Path | None = None, *, look: Callable[[str], dict] | None = None,
                 checker: Callable[[str], bool] | None = None, now: Callable[[], float] | None = None):
        self.home = Path(home or HOME)
        self.home.mkdir(parents=True, exist_ok=True)
        self._look = look
        self._checker = checker
        self._now = now or time.time
        self._visit_hold = False
        self._visit_secure = False
        self._guest_until_secure = False
        self._user_auth = False
        self._user_auth_reason = ""
        self._auth_at = 0.0
        self._face: dict = {}
        self._last_event: dict = {}
        self._hold_try_at = 0.0
        self._hold_fail = False
        self._lines: list[str] = []
        self._looks = 0

    def log_text(self) -> str:
        return "\n".join(self._lines)

    def _say(self, line: str) -> None:
        self._lines.append(line)

    def _read(self, name: str) -> dict:
        try:
            doc = json.loads((self.home / name).read_text(encoding="utf-8"))
            return doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, name: str, doc: dict) -> None:
        path = self.home / name
        tmp = path.with_name(".%s.%d.tmp" % (name, os.getpid()))
        tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    def _user_id(self) -> str:
        owner = str(self._read("enroll.json").get("owner") or "").strip().lower()
        if owner:
            return owner
        return owner_slug(str(self._read("user.json").get("name") or ""))

    def load_secure(self) -> bool:
        """A file never restores the lamp. Secure is this process."""
        return False

    def load_token(self) -> dict | None:
        doc = self._read("secure-token.json")
        return doc if doc.get("id") else None

    def save_token(self, token: dict) -> None:
        self._write("secure-token.json", token)

    def burn_token(self) -> None:
        try:
            (self.home / "secure-token.json").unlink()
        except OSError:
            pass

    def save_secure(self, on: bool) -> None:
        """Audit only. ``load_secure`` does not read it."""
        self._write("lamp.json", {"secure": bool(on)})

    def _expire_auth(self) -> None:
        if self._visit_secure or self._visit_hold or not self._user_auth:
            return
        try:
            limit = float(os.environ.get("CRISSA_AUTH_OFF_S") or AUTH_OFF_S)
        except ValueError:
            limit = AUTH_OFF_S
        if self._auth_at and self._now() - self._auth_at > limit:
            self._user_auth = False
            self._user_auth_reason = ""
            self._face = {}

    def visit_lamp_pub(self) -> dict:
        self._expire_auth()
        if self._visit_secure:
            color = "sage"
        elif self._visit_hold:
            color = "amber"
        else:
            color = "off"
        shown = "sage" if color == "sage" else "amber"
        return {"ok": True, "secure": self._visit_secure, "hold": self._visit_hold,
                "user_auth": self._user_auth, "user_id": self._user_id(),
                "guest_until_secure": self._guest_until_secure, "color": color,
                "visit_lamp": shown,
                "visit_lamp_info": {"hold": bool(self._visit_hold), "secure": bool(self._visit_secure),
                                    "color": shown}}

    def _visit_is_secure(self) -> bool:
        return bool(self._visit_secure)

    def drop_to_guest(self, reason: str = "guest_reset", burn_token: bool = False) -> dict:
        self._visit_hold = False
        self._visit_secure = False
        self._guest_until_secure = True
        self.save_secure(False)
        if burn_token:
            self.burn_token()
        self._user_auth = False
        self._user_auth_reason = ""
        self._auth_at = 0.0
        self._face = {}
        self._last_event = {"kind": "guest", "reason": str(reason or "guest_reset")}
        return {"ok": True, "user_auth": False, "secure": False, "hold": False, "reason": str(reason or "guest_reset")}

    def visit_secure_reauth(self, *, reason: str = "occupancy") -> dict:
        """Keep sage across a mode switch. A restart still uses the token."""
        why = str(reason or "").strip()
        if self._visit_is_secure():
            pub = self.visit_lamp_pub()
            pub.update({"ok": True, "inherited": True, "via": "live", "checked": True})
            self._say("presence lamp kept via=live reason=%s" % why)
            return pub
        if why.startswith("occupancy:"):
            pub = self.visit_lamp_pub()
            pub.update({"ok": True, "inherited": False, "via": "occupancy", "checked": False})
            self._say("presence occupancy leaves the lamp reason=%s" % why)
            return pub
        choice = decide(self.load_token(), self._user_id())
        if choice["act"] == "reauth":
            return self.visit_secure_from_token(reason=why or "restart_token")
        return self.drop_to_guest(reason=why or "guest_reset")

    def _look_once(self, source: str) -> dict:
        self._looks += 1
        if self._look is None:
            return {"found": False, "skipped": "no_face"}
        try:
            got = self._look(source) or {}
        except Exception:  # noqa: BLE001
            return {"found": False, "skipped": "error"}
        return got if isinstance(got, dict) else {"found": False}

    def _grant(self, uid: str, source: str, *, n_faces: int = 1) -> bool:
        who = str(uid or "").strip().lower()
        owner = self._user_id()
        if not is_ff_source(source) or bad_id(who) or (owner and who != owner):
            self._say("presence refuse guest_needs_ff %s src=%s" % (who or owner, source))
            return False
        self._user_auth = True
        self._user_auth_reason = source
        self._auth_at = self._now()
        self._face = {"locked": who, "n_faces": int(n_faces or 1), "source": source, "names": [who], "at": self._auth_at}
        self._say("presence AUTH grant %s src=%s" % (who, source))
        return True

    def note_face(self, source: str, *, found: bool = False, uid: str = "", skipped: str = "",
                  n_faces: int = 1) -> dict:
        """A posted look. From Guest, only a real look source mints recognition."""
        if skipped:
            return {"ok": True, "deferred": True, "looked": False}
        if not found:
            if not is_ff_source(source):
                self._say("presence refuse guest_needs_ff %s src=%s" % (uid or self._user_id(), source))
            return {"ok": False, "looked": True}
        ok = self._grant(uid or self._user_id(), source, n_faces=n_faces)
        return {"ok": ok, "looked": True}

    def _visit_secure_on(self, reason: str) -> None:
        self._visit_secure = True
        self._visit_hold = False
        self._guest_until_secure = False
        old = self.load_token() or {}
        uid = self._user_id()
        self.save_token(mint(uid, now=self._now(), reason=reason, hold=False, token_id=old.get("id")))
        self.save_secure(True)
        self._last_event = {"kind": "secure_on", "reason": reason}

    def _visit_secure_off(self, reason: str = "click", keep_auth: bool = False) -> dict:
        """Hold ignores every reason except a click. Already off does not clear recognition."""
        if self._visit_hold and reason != "click":
            return {"ok": True, "ignored": True, "reason": reason}
        if not self._visit_secure and not self._visit_hold:
            return {"ok": True, "noop": True, "reason": reason}
        self._visit_secure = False
        self._visit_hold = False
        self.burn_token()
        self.save_secure(False)
        if not keep_auth:
            self._user_auth = False
            self._user_auth_reason = ""
            self._auth_at = 0.0
            self._face = {}
        self._last_event = {"kind": "entry_face" if reason == "entry_face" else "secure_off", "reason": reason}
        return {"ok": True, "reason": reason}

    def _visit_door_tripwire(self, n_qualified: int) -> None:
        """A qualified face at the door turns Secure off. The reason is entry_face."""
        if int(n_qualified) < 1:
            return
        self._visit_secure_off(reason="entry_face")

    def visit_secure_from_token(self, *, reason: str = "restart_token") -> dict:
        token = self.load_token()
        choice = decide(token, self._user_id())
        if choice["act"] != "reauth":
            pub = self.visit_lamp_pub()
            pub.update({"ok": True, "inherited": False, "via": choice["why"], "secure": False, "checked": False})
            return pub
        desk = self._look_once("secure_token")
        skipped = str(desk.get("skipped") or "")
        if skipped in DEFERRED:
            pub = self.visit_lamp_pub()
            pub.update({"ok": False, "deferred": True, "via": skipped, "checked": False})
            return pub
        if desk.get("found"):
            uid = str(desk.get("uid") or self._user_id())
            self._grant(uid, "secure_token", n_faces=int(desk.get("n_faces") or 1))
            # A token's hold flag is a note. Hold itself needs the password again.
            self._visit_secure_on(reason or "restart_token")
            self._visit_hold = False
            pub = self.visit_lamp_pub()
            pub.update({"ok": True, "inherited": True, "via": "token_ff", "token_hold": bool(choice.get("hold")),
                        "hold": False, "checked": True})
            return pub
        self.drop_to_guest(reason="token_miss", burn_token=True)
        pub = self.visit_lamp_pub()
        pub.update({"ok": True, "inherited": False, "via": "token_miss", "secure": False, "checked": True})
        return pub

    def _run_reauth_ladder(self) -> dict:
        """The automatic ladder does not look while a restart has left the room in Guest."""
        if self._guest_until_secure:
            return {"ok": True, "looked": False, "skipped": "guest_until_secure"}
        before = self._looks
        desk = self._look_once("reauth_ladder_ff")
        if desk.get("found"):
            self._grant(str(desk.get("uid") or self._user_id()), "reauth_ladder_ff",
                        n_faces=int(desk.get("n_faces") or 1))
        return {"ok": True, "looked": self._looks > before}

    def visit_lamp_click(self) -> dict:
        if not secure_offered():
            pub = self.visit_lamp_pub()
            pub.update({"ok": False, "error": "secure_not_offered", "looked": False})
            return pub
        if self._visit_secure or self._visit_hold:
            self._visit_secure_off(reason="click")
            pub = self.visit_lamp_pub()
            pub.update(via="click_off", looked=False)
            return pub
        if self._user_auth:
            self._visit_secure_on("visit_lamp_click")
            pub = self.visit_lamp_pub()
            pub.update(via="auth", looked=False)
            return pub
        desk = self._look_once("desk_ff")
        if desk.get("found") and self._grant(str(desk.get("uid") or self._user_id()), "visit_lamp_click",
                                              n_faces=int(desk.get("n_faces") or 1)):
            self._visit_secure_on("visit_lamp_click")
            pub = self.visit_lamp_pub()
            pub.update(via="visit_lamp_click", looked=True)
            return pub
        pub = self.visit_lamp_pub()
        pub.update(via="empty", looked=True, secure=False)
        return pub

    def visit_lamp_scan(self) -> dict:
        """Retired. It does not look."""
        pub = self.visit_lamp_pub()
        pub.update({"ok": True, "skipped": "retired", "looked": False})
        return pub

    def visit_lamp_release(self) -> dict:
        self._visit_hold = False
        pub = self.visit_lamp_pub()
        pub.update(via="release")
        return pub

    def visit_lamp_hold(self, password: str) -> dict:
        """A password holds Secure for this process. An empty password is refused. The password is not kept."""
        if not secure_offered():
            return {"ok": False, "error": "secure_not_offered", "code": 403}
        secret = str(password or "")
        if not secret:
            return {"ok": False, "error": "a password is required", "code": 400}
        now = self._now()
        if self._hold_fail and now - self._hold_try_at < 2.0:
            return {"ok": False, "error": "wait", "code": 403}
        if self._checker is not None:
            ok = bool(self._checker(secret))
        else:
            ok = _pam_ok(secret)
        secret = ""
        if not ok:
            self._hold_try_at = now
            self._hold_fail = True
            return {"ok": False, "error": "refused", "code": 403}
        self._hold_fail = False
        self._visit_hold = True
        self._visit_secure = True
        self._guest_until_secure = False
        uid = self._user_id() or "owner"
        self.save_token(mint(uid, now=now, reason=HOLD_REASON, hold=True))
        self._last_event = {"kind": "hold", "reason": HOLD_REASON}
        pub = self.visit_lamp_pub()
        pub.update(via="keyboard_hold", code=200, held=True)
        return pub

    def see(self, embedding: Any, source: str) -> dict:
        """Store one posted look. A matching owner on a real look source grants recognition and refreshes the clock."""
        vec = _finite_row(embedding if isinstance(embedding, list) else None)
        if vec is None:
            return {"ok": False, "error": "a list of numbers"}
        owner = self._user_id()
        best = 0.0
        for face in self._read("enroll.json").get("faces") or []:
            if isinstance(face, list) and len(face) == len(vec):
                try:
                    row = [float(x) for x in face]
                except (TypeError, ValueError):
                    continue
                best = max(best, _cosine(vec, row))
        matched = bool(owner) and best >= MATCH and is_ff_source(source) and not bad_id(owner)
        if matched:
            self._grant(owner, source, n_faces=1)
        elif self._user_auth:
            self._auth_at = self._now()
            if self._face:
                self._face["at"] = self._auth_at
        return {"ok": True, "matched": matched, "sim": round(best, 4)}

    def presence(self) -> dict:
        self._expire_auth()
        face = dict(self._face)
        age = None
        if face.get("at"):
            age = max(0.0, self._now() - float(face["at"]))
        pub = self.visit_lamp_pub()
        return {
            "user_auth": self._user_auth,
            "user_id": self._user_id(),
            "user_auth_reason": self._user_auth_reason,
            "visit_lamp": pub["visit_lamp"],
            "visit_lamp_info": pub["visit_lamp_info"],
            "face_id": {"locked": face.get("locked") or "", "n_faces": int(face.get("n_faces") or 0),
                        "age_s": age, "source": face.get("source") or "", "names": list(face.get("names") or [])},
            "last_event": dict(self._last_event),
        }

    def boot(self) -> dict:
        """A start clears Secure and the hold, keeps a token, then looks once. No camera thread."""
        self.drop_to_guest(reason="restart", burn_token=False)
        return self.visit_secure_from_token(reason="restart")

    def handle(self, method: str, path: str, body: dict | None) -> tuple[int, dict]:
        body = body or {}
        if method == "GET" and path == "/health":
            return 200, {"ok": True, "service": "presence"}
        if method == "GET" and path == "/v1/presence":
            return 200, self.presence()
        if method == "GET" and path == "/v1/visit_lamp":
            return 200, self.visit_lamp_pub()
        if method == "POST" and path == "/v1/see":
            out = self.see(body.get("embedding"), str(body.get("source") or ""))
            return (200 if out.get("ok") else 400), out
        if method == "POST" and path == "/v1/enroll":
            vecs, err = accept_faces(body)
            if vecs is None:
                return 400, {"ok": False, "error": err}
            owner = owner_slug(str(body.get("owner") or self._user_id()))
            if not owner or bad_id(owner):
                return 400, {"ok": False, "error": "the owner is a name"}
            doc = write_enroll(self.home, owner, vecs)
            return 200, {"ok": True, "owner": doc["owner"], "faces": len(doc["faces"])}
        if method == "POST" and path == "/v1/visit_lamp/click":
            out = self.visit_lamp_click()
            return (403 if out.get("error") == "secure_not_offered" else 200), out
        if method == "POST" and path == "/v1/visit_lamp/release":
            return 200, self.visit_lamp_release()
        if method == "POST" and path == "/v1/visit_lamp":
            op = str(body.get("op") or "").strip()
            if op == "click":
                out = self.visit_lamp_click()
                return (403 if out.get("error") == "secure_not_offered" else 200), out
            if op == "scan":
                return 200, self.visit_lamp_scan()
            if op == "release":
                return 200, self.visit_lamp_release()
            if op == "guest_reset":
                return 200, self.drop_to_guest(reason="guest_reset")
            if op == "secure_reauth":
                reason = "occupancy" if "reason" not in body else body.get("reason")
                return 200, self.visit_secure_reauth(reason=str(reason if reason is not None else ""))
            if op == "entry_face":
                try:
                    n = int(body.get("n") or 1)
                except (TypeError, ValueError):
                    n = 1
                self._visit_door_tripwire(n)
                return 200, self.visit_lamp_pub()
            if op == "hold":
                # The password is used once inside visit_lamp_hold and is not copied into the answer.
                out = self.visit_lamp_hold(str(body.get("password") or ""))
                code = int(out.pop("code", 200 if out.get("ok") else 403))
                return code, out
            return 400, {"ok": False, "error": "unknown op"}
        return 404, {"ok": False, "error": "no such path"}


def _pam_ok(password: str) -> bool:
    """The login password of this process's user, when python-pam is installed. Otherwise refuse."""
    try:
        import getpass
        import pam
    except ImportError:
        return False
    try:
        return bool(pam.pam().authenticate(getpass.getuser(), password))
    except Exception:  # noqa: BLE001
        return False


def _handler(svc: Service):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def _send(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _body(self) -> dict | None:
            n = int(self.headers.get("Content-Length") or 0)
            if n > BODY_MAX:
                self._send(413, {"ok": False, "error": "body too large"})
                return None
            raw = self.rfile.read(n) if n else b"{}"
            try:
                doc = json.loads(raw.decode("utf-8") or "{}")
            except ValueError:
                self._send(400, {"ok": False, "error": "json"})
                return None
            if not isinstance(doc, dict):
                self._send(400, {"ok": False, "error": "json"})
                return None
            return doc

        def do_GET(self):  # noqa: N802
            code, payload = svc.handle("GET", self.path.split("?")[0], {})
            self._send(code, payload)

        def do_POST(self):  # noqa: N802
            doc = self._body()
            if doc is None:
                return
            code, payload = svc.handle("POST", self.path.split("?")[0], doc)
            self._send(code, payload)

    return Handler


def listen(svc: Service, port_n: int = 0) -> ThreadingHTTPServer:
    """Bind loopback. Port 0 picks a free port. The house service's port is refused."""
    if int(port_n) == HOUSE_PORT:
        raise RuntimeError("presence does not bind the house service's port")
    httpd = ThreadingHTTPServer(("127.0.0.1", int(port_n)), _handler(svc))
    threading.Thread(target=httpd.serve_forever, name="crissa-presence", daemon=True).start()
    return httpd
