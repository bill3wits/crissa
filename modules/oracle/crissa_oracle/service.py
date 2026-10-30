"""The oracle's state, one look at a time (2026-10-01). What the clock and the status marks read."""
from __future__ import annotations

import json
import os
import threading
import time

from . import beam, camera, door, egress, enroll, face, modes, setup


# SECURITY OFF IDLES THE FACE LOOK (round 17 follow-ups, the User 2026-10-13): with Security Off nothing on this install
# is decided by a face, so the look - a frame from the grabber, the landmarker, AuraFace on each face, the numbers
# posted to presence's /v1/see to be matched - costs CPU for nothing. In Off the tick takes no frame and looks at
# nothing; the state says `idle: "security_off"` (the status mark says so) and keeps the camera's last known answer.
# What this look fed and Off gives up: presence's fixed-camera face block and sole slot (the HUD's "User (face)"), the
# door feed of this module (CRISSA_ORACLE_DOOR, unset by default). The Wall's rest does not read this look (it reads
# presence's door watch, which stays). The setting is read on every tick (one stat), so leaving Off resumes on the
# next look. Kill: CRISSA_SECURITY_OFF_IDLES_FACES=0 (the look runs in every position, as before).
def faces_idle(env=None) -> bool:
    """True while Security is Off and the idling is on (above). The house never idles (its Security is always on)."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_SECURITY_OFF_IDLES_FACES", "1")).strip().lower() in ("0", "off", "false", "no"):
        return False
    try:
        try:
            from . import crissa_home as H          # the copy a pack puts beside this file
        except ImportError:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
            import crissa_home as H                 # noqa: E402  the source tree's
        return H.security_mode() == "off"
    except Exception:  # noqa: BLE001  unreadable: look, as before
        return False


def presence_url(env=None) -> str:
    """Where the presence service listens (the launcher sets CRISSA_PRESENCE_URL); '' = no presence service."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PRESENCE_URL") or "").strip().rstrip("/")


def post_look(embedding, url: str | None = None, timeout: float = 1.5, faces: int | None = None) -> bool:
    """Hand one face's numbers to the presence service's /v1/see as source `oracle` (2026-10-08: the oracle is the
    pack's camera; before this nothing on a pack ever posted a look, so Secure by face never happened). The
    presence service decides whether the numbers are the owner's; nothing but numbers leaves this process.
    `faces`: how many faces the look saw (2026-10-11: on a fixed camera, one face that is the User's is the User
    alone at the seat - crissa_presence.fixed_cam - and two are not)."""
    base = presence_url() if url is None else url
    if not base or not isinstance(embedding, list):
        return False
    body = {"embedding": embedding, "source": "oracle"}
    if faces is not None:
        body["faces"] = int(faces)
    try:
        import urllib.request
        req = urllib.request.Request(base + "/v1/see", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with egress.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001
        return False


_NAMES: list = []


def _names_faces() -> str:
    if not _NAMES:
        try:
            _NAMES.append(setup.recogniser_state())
        except Exception as e:  # noqa: BLE001
            _NAMES.append("unknown (%s)" % type(e).__name__)
    return _NAMES[0]


def _takes_faces(fn) -> bool:
    """The poster takes the face count (post_look does; an older or a test's poster may take the numbers alone)."""
    try:
        import inspect
        ps = inspect.signature(fn).parameters
        return "faces" in ps or any(p.kind is p.VAR_KEYWORD for p in ps.values())
    except (TypeError, ValueError):
        return False


class Oracle:
    def __init__(self, cam=None, look=face.look, wanted_mode: str | None = None, period_s: float = 1.0, post=post_look):
        self.cam = cam if cam is not None else camera.Camera()
        self.look, self.period_s, self.post = look, period_s, post
        self.posted = 0
        self.mode = modes.mode(wanted_mode)
        self.door = door.DoorFeed(door.box())
        self.voice_ok: bool | None = None          # the voice backup posts here; None = it has not spoken
        self.state = self._blank()
        self._stop = threading.Event()

    def _blank(self) -> dict:
        return {"ok": True, "camera": False, "camera_off": bool(getattr(self.cam, "off", False)),
                "engine": False, "engine_why": face.engine_reason(),
                "face": False, "user": None, "by": "none",
                "sim": None, "mode": self.mode, "secure_offered": modes.secure_offered(),
                "beam": beam.present(), "beam_label": beam.label(), "door": [], "at": 0.0}

    def tick(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        st = self._blank()
        st["at"] = now
        if faces_idle():                          # Security Off: no frame, no look, no post (above)
            st.update(idle="security_off", camera=bool((self.state or {}).get("camera")), posted=self.posted)
            self.state = st
            return st
        frame = self.cam.read()
        st["camera"] = frame is not None
        seen = self.look(frame, enroll.load()) if frame is not None else None
        st["engine"] = seen is not None
        st["face"] = bool(seen and seen.get("seen"))
        st.update(modes.decide(seen, self.voice_ok))
        emb = (seen or {}).get("embedding")
        st["faces"] = int((seen or {}).get("faces") or 0)       # how many faces this look saw (2026-10-11)
        if isinstance(emb, list) and self.post is not None:
            try:
                sent = self.post(emb, faces=st["faces"]) if _takes_faces(self.post) else self.post(emb)
                if sent:
                    self.posted += 1
            except Exception:  # noqa: BLE001
                pass
        st["posted"] = self.posted
        st["names_faces"] = _names_faces()        # why posted stays 0: the recogniser's own state (2026-10-12)
        self.door.feed((seen or {}).get("centres") or [], now)
        st["door"] = list(self.door.events)
        self.state = st
        return st

    REST_AFTER_S = float(os.environ.get("CRISSA_ORACLE_REST_AFTER_S", "10"))
    REST_PERIOD_S = float(os.environ.get("CRISSA_ORACLE_REST_PERIOD_S", "3"))

    def next_wait(self, now: float | None = None) -> float:
        """The look's period: 1 s, and REST_PERIOD_S once no face has been seen for REST_AFTER_S (the User 2026-10-10,
        .101 idle load); a face brings it back at once. CRISSA_ORACLE_REST_AFTER_S=0 never rests."""
        now = time.time() if now is None else now
        if (self.state or {}).get("idle"):        # Security Off: the setting is read every REST_PERIOD_S
            return max(self.period_s, self.REST_PERIOD_S)
        if (self.state or {}).get("face"):
            self._last_face = now
        last = getattr(self, "_last_face", None)
        if last is None:
            self._last_face = last = now
        if self.REST_AFTER_S > 0 and now - last >= self.REST_AFTER_S:
            return max(self.period_s, self.REST_PERIOD_S)
        return self.period_s

    def run(self) -> None:
        while not self._stop.wait(self.next_wait()):
            try:
                self.tick()
            except Exception:
                self.state = dict(self._blank(), at=time.time())

    def stop(self) -> None:
        self._stop.set()
        self.cam.close()
