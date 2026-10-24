"""Presence loop — door-first by default (STONE-SPINE).

Default: hold door, single-frame observe, inventory arithmetic.
Cold restart / orientation: **smooth video pan** (low-res 30fps path + ghosts),
not station stills. Facefind snaps only for unknown bodies.
Legacy multi-stop stills: auth/reconcile only if SMOOTH baseline disabled.
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths

import json
import os
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Optional

from .camera import Camera


# --- named-face liveness by keyframe motion (Fable 2026-08-03) ---------------
# A framed photo of an enrolled person is pixel-identical across a station's
# keyframes; a seated person is not. Compared against whole-frame motion so a
# drifting camera (which moves the photo too) cannot fake a live verdict.
FACE_MOVE_MIN = 2.0      # grey levels of mean abs diff on the face crop
FACE_OVER_FRAME = 1.5    # face must out-move the frame by this factor


def occupancy_on() -> bool:
    """Occupancy mill is parked. Default off for launch trim."""
    return os.environ.get("RAIL_OCCUPANCY", "0").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _frame_sig(im):
    import cv2

    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, (96, 72)).astype("float32")


def _crop_sig(im, bbox):
    import cv2

    if bbox is None:
        return None
    x1, y1, x2, y2 = [int(v) for v in bbox]
    h, w = im.shape[:2]
    m = 6
    c = im[max(0, y1 - m) : min(h, y2 + m), max(0, x1 - m) : min(w, x2 + m)]
    if c is None or c.size == 0:
        return None
    g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, (48, 48)).astype("float32")


def _moved(obs, frame_sig):
    """(True|False|None, evidence). None = not enough frames to judge."""
    import numpy as np

    seen = [(i, c) for (i, _sim, c) in obs if c is not None]
    if len({i for i, _ in seen}) < 2:
        return None, 0.0
    best = 0.0
    for a in range(len(seen)):
        for b in range(a + 1, len(seen)):
            ia, ca = seen[a]
            ib, cb = seen[b]
            if ia == ib:
                continue
            face = float(np.mean(np.abs(ca - cb)))
            fa, fb = frame_sig.get(ia), frame_sig.get(ib)
            glob = float(np.mean(np.abs(fa - fb))) if fa is not None and fb is not None else 0.0
            # face motion in excess of whatever the whole frame did
            best = max(best, face - max(glob * FACE_OVER_FRAME, 0.0))
    return (best >= FACE_MOVE_MIN), best
from . import door_watch as door_watch_module
from .door_watch import DoorInventory
from .schema import PersonSlot, RoomPresence, empty_room
from .sweep import SweepEngine
from .traits_leave import normalize_traits
from . import sot_proof

# Door poll interval (NOT multi-stop). Buffer is 30fps; poll often enough to drain delta.
DOOR_INTERVAL = float(os.environ.get("RAIL_PRESENCE_DOOR_INTERVAL", "0.5"))
try:
    BUSY_TTL_SEC = float(os.environ.get("RAIL_BUSY_TTL_SEC", "15"))
except (TypeError, ValueError):
    BUSY_TTL_SEC = 15.0
# If 1 (default): never timer-sweep the room. If 0: legacy continuous multi-stop.
DOOR_FIRST = os.environ.get("RAIL_PRESENCE_DOOR_FIRST", "1") not in ("0", "false", "no")
LEAVE_CONFIRM = float(os.environ.get("RAIL_PRESENCE_LEAVE_CONFIRM_SEC", "2.5"))
# After NEW enter, door-empty is walk-into-room — not leave (the User measured 2026-07-22)
# the User 2026-08-11: entry grace deleted (default 0). Was 45s freeze after enter.
ENTRY_GRACE = float(os.environ.get("RAIL_PRESENCE_ENTRY_GRACE_SEC", "0"))
# Face every Nth buffer frame (2 @ 30fps ≈ 15 face-fps — routine without melting CPU)
FACE_STRIDE = int(os.environ.get("RAIL_PRESENCE_FACE_STRIDE", "3"))
# Cap frames scored per door tick — CPU faceid cannot do 40/tick without stalling loop
FACE_MAX_PER_TICK = int(os.environ.get("RAIL_PRESENCE_FACE_MAX_PER_TICK", "12"))
# Cold baseline smooth_orient blocks the door loop for minutes on CPU face — skip by default
SKIP_BASELINE_ORIENT = os.environ.get(
    "RAIL_PRESENCE_SKIP_BASELINE_ORIENT", "1"
) not in ("0", "false", "no")
USE_BUFFER = os.environ.get("RAIL_PRESENCE_USE_BUFFER", "1") not in ("0", "false", "no")
# Ghost fix (the User 2026-07-23): inventory person without full face match AND without
# gender-consistent LIVE face for this long → multi-stop rescan (camera truth).
# "Full match" = enrolled face sim ≥ threshold. Gender match = LIVE soft M/F matching
# identity-store stamp. No voice full-match path yet (voice emb bank empty) — face is SoT.
UNCONFIRMED_RESCAN_SEC = float(
    os.environ.get("RAIL_PRESENCE_UNCONFIRMED_RESCAN_SEC", "120")
)
RESCAN_COOLDOWN_SEC = float(os.environ.get("RAIL_PRESENCE_RESCAN_COOLDOWN_SEC", "180"))
# Stale exit-arms decay (disarm, remove nobody) — docs/DESIGN-OCCUPANCY-STREAM.md
EXIT_ARM_TTL_SEC = float(os.environ.get("RAIL_PRESENCE_EXIT_ARM_TTL_SEC", "90"))
# Sticky inventory + media ghosts: empty door FOV this long → may clear *unconfirmed only*.
# Claude 12:16: ~90–130s drops of certain:true the user = this timer. Never timer-wipe
# face-certain / id_certain slots — seated out-of-FOV is normal. VL leave or exit_arm.
# Default still 90 for unconfirmed ghosts; certain names are exempt in the clear path.
STALE_EMPTY_LEAVE_SEC = float(
    os.environ.get("RAIL_PRESENCE_STALE_EMPTY_LEAVE_SEC", "90")
)
# Sole occupant empty-door leave (past grace) — long confirm so desk sit ≠ leave.
# Real outs without door re-arm still clear; short 8s was killing fair baselines (L11).
SOLE_EMPTY_LEAVE_SEC = float(
    # Ghost recovery only (the User law 2026-07-27) — desk-sit must never wipe;
    # real leaves = exit_armed / VL sub / leave-traits match.
    os.environ.get("RAIL_PRESENCE_SOLE_EMPTY_LEAVE_SEC", "1800")
)
# Empty-room voice surprise: queue, no cooldown. Depth ≥ this → Busy
# (linger shares that Busy path; linger does not arm the AUTH scan).
SURPRISE_QUEUE_MAX = int(os.environ.get("RAIL_PRESENCE_SURPRISE_QUEUE_MAX", "3"))
# Concurrent VL voters (Gemma/CosmOS/Qwen) on one transit — first non-null wins
VL_DEDUP_SEC = float(os.environ.get("RAIL_VL_DEDUP_SEC", "12"))
# Oracle-as-witness for pending_crossing (the User 2026-08-10 / Claude plan).
# Default OFF — VL voters are gone; Stage-1 gate still requires corroboration.
# Drop-in: Environment=RAIL_DOOR_ORACLE_WITNESS=1 under rail-presence.service.d/
DOOR_ORACLE_WITNESS = os.environ.get(
    "RAIL_DOOR_ORACLE_WITNESS", "0"
) not in ("0", "false", "no", "")
# Live path: 1 vote by default so the 12s window can still mint; research path
# uses RAIL_ORACLE_VOTES=3. Extra sec stretch pending.expires while oracle runs.
DOOR_ORACLE_WITNESS_VOTES = int(os.environ.get("RAIL_DOOR_ORACLE_WITNESS_VOTES", "1"))
DOOR_ORACLE_WITNESS_EXTRA_SEC = float(
    os.environ.get("RAIL_DOOR_ORACLE_WITNESS_EXTRA_SEC", "45")
)
# --- Object-governed writer modes (presence-perfect §3, D1) ---
# The write gate lives in the OBJECT: per-source mode shadow→subs_only→full
# resolved here (override > env RAIL_VL_MODE_<SOURCE> > RAIL_VL_MODE_DEFAULT).
# There is NO voter-supplied mode field on the wire — a buggy or compromised
# voter can never request its own promotion. Per-source env / default env are
# re-read at call time so ops can flip without code changes.
VL_MODES = ("shadow", "subs_only", "full")
VL_MODE_DEFAULT_FALLBACK = "full"  # operator/tests/internal keep today's flow
# Applied-delta budgets (measured honest rate ≈ 8/h); over-cap deltas are
# DEMOTED to witness (never queued, never dropped silently).
VL_APPLIES_PER_HOUR = int(os.environ.get("RAIL_VL_APPLIES_PER_HOUR", "12"))
VL_APPLIES_BURST = int(os.environ.get("RAIL_VL_APPLIES_BURST", "4"))
VL_BURST_WINDOW_SEC = 600.0
# Face-conflict veto window for full-mode −1 against a named slot
VL_FACE_VETO_SEC = float(os.environ.get("RAIL_VL_FACE_VETO_SEC", "10"))
# Error budget → auto-shadow (demote-only; never auto-clears)
VL_ERRBUDGET_N = int(os.environ.get("RAIL_VL_ERRBUDGET_N", "3"))
VL_ERRBUDGET_WINDOW_SEC = float(
    os.environ.get("RAIL_VL_ERRBUDGET_WINDOW_SEC", "1800")
)
# Allowed roots for enter_still_hint frames_dir (strict Path.resolve
# containment — without this any local process could plant jpg "evidence")
ENTER_STILL_HINT_ROOTS_DEFAULT = str(
    _paths.DATA / "work/oracle_live/qwen_events"
)
# After door/operator leave, refuse CODE memory re-add for this long unless
# a *live* door face is seen again (the User 2026-07-27 — another person resurrect bug).
CODE_RESURRECT_BLOCK_SEC = float(
    os.environ.get("RAIL_CODE_RESURRECT_BLOCK_SEC", "120")
)
# Session enter stills for visual leave match (CONTRACT-CLOTHING-LEAVE)
ENTER_STILL_DIR = Path(
    os.environ.get(
        "RAIL_PRESENCE_ENTER_STILL_DIR",
        str(_paths.DATA / "enter_stills"),
    )
)
ENTER_STILL_MAX = int(os.environ.get("RAIL_PRESENCE_ENTER_STILL_MAX", "3"))
# Auto-reconcile (the User 2026-08-04 gym-return incident): a room holding ONLY
# unknown slots self-heals with a sweep instead of waiting for a manual
# /v1/sweep. Recovery, not a remover — the sweep only re-IDs by face evidence.
AUTO_RECONCILE = os.environ.get("RAIL_AUTO_RECONCILE", "1") not in (
    "0",
    "false",
    "no",
)
AUTO_RECONCILE_AFTER_SEC = float(
    os.environ.get("RAIL_AUTO_RECONCILE_AFTER_SEC", "30")
)
AUTO_RECONCILE_COOLDOWN_SEC = float(
    os.environ.get("RAIL_AUTO_RECONCILE_COOLDOWN_SEC", "300")
)
# Wait for body stillness after enter before (re)locking stills
STILL_WAIT_SEC = float(os.environ.get("RAIL_PRESENCE_STILL_WAIT_SEC", "1.0"))
STILL_DEFER = os.environ.get("RAIL_PRESENCE_STILL_DEFER", "1") not in (
    "0",
    "false",
    "no",
)
LEAVE_PACKAGE_MAX = int(os.environ.get("RAIL_PRESENCE_LEAVE_PACKAGE_MAX", "4"))
GRABBER_HEALTH_URL = os.environ.get(
    "RAIL_PRESENCE_GRABBER_HEALTH", _ports.camera("/health")
)
GRABBER_AIM_URL = os.environ.get(
    "RAIL_PRESENCE_GRABBER_AIM", _ports.camera("/aim")
)
GRABBER_FACELOCK_URL = os.environ.get(
    "RAIL_PRESENCE_GRABBER_FACELOCK", _ports.camera("/v1/facelock")
)
GRABBER_FACEFIND_URL = os.environ.get(
    "RAIL_PRESENCE_GRABBER_FACEFIND", _ports.camera("/v1/facefind")
)
GRABBER_SMOOTH_SCAN_URL = os.environ.get(
    "RAIL_PRESENCE_GRABBER_SMOOTH_SCAN", _ports.camera("/v1/smooth_scan")
)
# Facefinder seat routine (the User 2026-08-06/07): aim → FF ON → engage → capture →
# FF OFF → door between stops. Prefer this over continuous smooth_scan patrol.
GRABBER_LOOK_AROUND_URL = os.environ.get(
    "RAIL_PRESENCE_GRABBER_LOOK_AROUND", _ports.camera("/v1/look_around")
)
FACEFINDER_ENGAGE_SEC = float(
    os.environ.get("RAIL_PRESENCE_FACEFINDER_ENGAGE_SEC", "2.5")
)
# the User, stated repeatedly and finally proved by his 2026-08-09 16:08 lap: the
# camera was zoomed on his face at the desk WHILE he walked through the door,
# so the pack held desk close-ups, no door frames existed, no enter minted, and
# the crossing was lost. While the PTZ is held off the door, zero door analysis
# runs and those frames can never be revisited.
#
# LAW: the door FOV wins. FaceFinder exists ONLY to establish the User's auth
# when it is not already established or has timed out — i.e. error correction
# or an explicit User request. It is never a patrol. Default OFF; the
# error-correction path opts in explicitly.
FACEFINDER_ALLOWED = os.environ.get("RAIL_PRESENCE_FACEFINDER", "0") not in (
    "0", "false", "no", "off", "",
)
# The door pose lives in camera-stops.json and nowhere else. It moved on
# 2026-08-16 (pan 450000 -> 432000, zoom 150 -> 200) and every module holding
# its own literal went stale in the same minute: grabber rested at 432000 while
# service kept aiming at 450000, five degrees off, and door_fanout's in-view
# check compared the live pan against the OLD door and returned False for every
# trigger — 204 triggers, 14 events fired. The number is also per-house init
# data, so a literal is a portability bug and not merely a stale one.
#
# These two are a boot-time snapshot for the cold paths. Anything that actually
# commands the camera calls door_pose() live, because the file can be re-measured
# while the service is up.
#
# Tilt: +5 deg (18000 = 5 x 3600/deg) — the User 2026-08-09, the door FOV was
# cutting the top of a tall crossing's head. Changing it invalidates
# door_home_live.jpg and the door_home rect in face_zone_masks.json — re-capture
# and re-measure both.
def _door_pose_now() -> dict[str, int]:
    from .seat_shot import door_pose

    return door_pose()


DOOR_PAN = _door_pose_now()["pan"]
DOOR_TILT = _door_pose_now()["tilt"]



def owner_own_bank_on() -> bool:
    """Round 41 (F1): the User's face check scores his own bank. Kill CRISSA_OWNER_OWN_BANK=0."""
    return os.environ.get("CRISSA_OWNER_OWN_BANK", "1").strip().lower() not in ("0", "off", "false", "no")


def seen_line(info: dict | None, uid: str, sim: float, found: bool) -> str:
    """What the face check saw, in words (round 41, F1: never "not seen sim=0.000" over a face): "seen sim=0.83",
    "seen as ted (0.86), not the user", "a face, not the user (sim=0.31)", "no face"."""
    d = info if isinstance(info, dict) else {}
    other, osim = str(d.get("other") or ""), float(d.get("other_sim") or 0.0)
    if found:
        return "seen sim=%.3f" % sim + (" (%s %.2f also)" % (other, osim) if other else "")
    if other:
        return "seen as %s (%.2f), not %s" % (other, osim, uid)
    if int(d.get("n_faces") or 0) > 0:
        return "a face, not %s (sim=%.3f)" % (uid, sim)
    return "no face (sim=%.3f)" % sim

class _SkipDoorFilter(Exception):
    """Hint frames bypass the door-view filter — see _capture_enter_stills."""


# The Inspector's automatic wake-ups are retired (see _inspector_auto). Set to
# "0" to bring them back for a diagnosis; nothing in normal operation should.
INSPECTOR_AUTO_OFF = os.environ.get("RAIL_INSPECTOR_AUTO_OFF", "1") not in (
    "0", "false", "no",
)


class PresenceService:
    def __init__(self, camera: Optional[Camera] = None):
        self.camera = camera or Camera()
        self.engine = SweepEngine(camera=self.camera)
        self.inventory = DoorInventory()
        # Ghost-recovery veto (the User 2026-08-04): the janitor may not remove a
        # name the wider rail has fresh evidence for (desk face, voice,
        # held session). Door watch calls back before any time-based drop.
        self.inventory.fresh_evidence_cb = self._fresh_evidence_for
        # EVERY leave path goes through _drop → tombstone; no path can skip it
        self.inventory.on_leave = lambda n, ev: self._note_left([n])
        self._lock = threading.Lock()
        self._state = empty_room(camera_open=False)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started_at = time.time()
        self.cycles = 0
        self.door_cycles = 0
        self.sweep_cycles = 0
        self.last_cycle_at: float | None = None
        self.last_error: str | None = None
        self.last_frame_seq: int | None = None
        self.seq_stall_cycles = 0
        self.last_grabber: dict | None = None
        self._last_grabber_ts: float = 0.0
        self._door_trip_logged: str | None = None
        self._sweep_lock = threading.Lock()
        self._mode = "holding"  # holding | away | reconciling
        self._away_reason: str | None = None
        self._baseline_done = False
        self._buffer_seq: int | None = None  # last processed grabber frame seq
        self.last_buffer_n: int = 0
        self.last_face_frames: int = 0
        self._last_genders: dict[str, str] = {}
        # name -> first epoch we failed full face match AND gender soft match
        self._unconfirmed_since: dict[str, float] = {}
        self._last_rescan_at: float = 0.0
        self._pending_ghost_rescan: bool = False
        # G2: door enter → queue Inspector auto live_snap after _set
        self._pending_inspector_auto: dict | None = None
        # G3: last inventory disk save ts
        self._inv_save_ts: float = 0.0
        self._inv_reload_info: dict | None = None
        # Rescan room: track headcount for empty-auto + last auto result
        self._prev_headcount_for_rescan: int | None = None
        self._last_auto_rescan: dict | None = None
        self._auto_rescan_thread: threading.Thread | None = None
        # Deferred stillness body stills: slot_key -> due_ts
        self._still_due: dict[str, float] = {}
        self._auto_reconcile_since: float | None = None
        self._auto_reconcile_last: float = 0.0
        # who-left shadow: sigs survive the slot drop (the leave deletes
        # traits_live before the capture thread's verdict callback runs, and
        # the just-dropped person is the prime suspect)
        self._recent_sigs: dict[str, dict] = {}
        # Soft ID certainty map: name -> True if face-confirmed / confirm_mention
        self._id_certain: dict[str, bool] = {}
        # Explicit auth grants from instruct (person_id -> {level, source, ts})
        self._auth_levels: dict[str, dict] = {}
        self._id_sources: dict[str, dict] = {}
        # Lights-out edge for Inspector snap on light return (None = cold start)
        self._lights_out_prev: bool | None = None
        # Last User voice stamp (ears POST instruct user.voice) for user_auth
        self._last_user_voice: dict = {
            "match": False,
            "sim": None,
            "ts": 0.0,
            "person_id": None,
        }
        # Ranked voice ID for Speaker/HUD — not occupancy AUTH.
        self._last_spoke_voice: dict = {
            "match": False,
            "sim": None,
            "ts": 0.0,
            "person_id": None,
        }
        # Mic-frame hearing only (sound.heard). Never a roster.
        self._sound: dict | None = None
        # Pan-only cooldown for absent desk recovery (separate from hunt gap).
        self._ensure_auth_pan_ts: float = 0.0
        # Oracle witness single-flight: crossing_ts already in flight
        self._oracle_witness_inflight: set[float] = set()
        self._capture_cancelled_no_pack: int = 0
        # Entry-anchored AUTH session (the User 2026-08-04): opened by face-at-
        # entry + one strong voice; HELD by presence continuity (no exit
        # event for the User) — no timer; cleared by exit / lights-out /
        # host lock (the User 2026-08-24). Risk-tiered re-auth is the ear's job
        # and reads the ages published from this state.
        self._user_session: dict | None = None
        self._prev_host_lock_state: bool | None = None
        # Sticky face-auth (the User/Fable 2026-08-03): badge-at-door model.
        # Camera watches door; User sits at seat 8 behind it — continuous face
        # is geometrically impossible. Once face is proven (entry / reauth /
        # door buffer), hold AUTH_FACE until leave/lights_out/host lock.
        # Voice is the continuous liveness check for user_auth_only BAU.
        # name -> {sim, ts, source}
        self._face_auth_hold: dict[str, dict] = {}
        # Door storm. Busy-on drops User AUTH; stays off until recheck.
        self._room_busy: bool = False
        self._busy_reason: str = ""
        self._busy_since: float | None = None
        self._busy_beat_ts: float | None = None
        self._busy_unknown: bool = False
        self._auth_blocked_until_recheck: bool = False
        self._sot_rev, self._sot_writer = sot_proof.load_rev()
        self._sot_material_last: tuple | None = None
        # Visit lamp. Secure is a FLAG (see visit_lamp.py). Boot is always
        # insecure — only a User click mints Secure this process.
        from . import visit_lamp as _vl

        self._visit_boot_unproven = False  # legacy attr; no longer gates colour
        self._visit_cannot_see = False
        self._visit_click_lock = threading.Lock()
        self._visit_secure: bool = _vl.load_secure()
        # Keyboard hold rides beside the flag; boot clears it like the flag.
        self._visit_hold: bool = False
        # After an Oracle restart guest: no automatic AUTH mint
        # (voice ladder) until Secure is sage again (click or token FF).
        # An occupancy: reason does not set this latch (the User 2026-10-08).
        self._guest_until_secure: bool = False
        self._visit_hold_last_try: float = 0.0
        self._visit_secure_reason: str = "boot"
        self._visit_secure_since: float | None = None
        self._visit_secure_lock = threading.Lock()
        self._visit_ff_saved: dict | None = None  # session look killed by Secure
        self._visit_ack: dict = {}  # legacy; unused
        self._visit_census_cache: tuple | None = None
        self._last_desk_snaps: list = []
        self._last_desk_unmatched: list = []
        # Oracle visit overlay (same object; Argus fills after FaceID)
        self._oracle_visits: dict[str, dict] = {}
        # Recent VL applies for multi-voter dedup (COORD 2026-07-27)
        self._vl_recent: list[dict] = []
        self._vl_dedup_lock = threading.Lock()
        # Writer-mode governance (presence-perfect §3): per-source runtime
        # override (operator vl.mode / auto-shadow; NOT restart-durable — env
        # is the durable config, deliberate) + per-source evidence stats.
        # Stats are metadata about verdict outcomes — never bodies or counts.
        self._vl_mode_override: dict[str, str] = {}
        self._vl_writer_stats: dict[str, dict] = {}
        # name -> unix until which CODE may not re-add (leave tombstone)
        self._left_block_until: dict[str, float] = {}
        # published doubt look-request (no-pan ghost path)
        self._last_look_request: dict | None = None
        # pet display state: name -> {dir: inside|outside, ts} — never counted
        self._pets: dict[str, dict] = {}
        self._surprise_lock = threading.Lock()
        self._surprise_q: deque[dict] = deque()
        self._surprise_busy = False
        # Busy AUTH scan: armed by queue_flood only. Linger uses the same
        # Busy flag and must not arm or fire this. One scan per storm.
        self._busy_auth_scan_armed: bool = False
        self._busy_auth_scanned: bool = False
        self._empty_fov_since: float | None = None
        # Face-to-face: looking at the speaker. Not occupancy.
        self._f2f: bool = False
        self._f2f_since: float | None = None
        self._f2f_reason: str = ""
        self._f2f_hour: int | None = None
        self._f2f_person: str | None = None
        self._f2f_pose: dict | None = None
        self._f2f_lock = threading.Lock()
        self._f2f_looking = False
        self._beam_lease_id: str | None = None
        self._beam_speech_ts: float | None = None
        # the camera token (camera_token.py): the one :8192 lease, who holds it
        self._cam_token: dict | None = None
        self._cam_token_lock = threading.Lock()
        # 1 s glass note when a camera-face move drops sage (not keyboard hold)
        self._camera_note: dict | None = None
        self._door_glance_last: tuple | None = None
        self._faceid_hud: dict = {}
        self._faceid_frame: bytes | None = None
        self._faceid_crop: bytes | None = None
        self._faceid_scan_ts: float = 0.0
        self._door_glance_ts: float = 0.0
        self._faceidog_last: dict[str, tuple[float, float]] = {}
        # Conversation AUTH last User lock. Not occupancy _face_auth_hold.
        self._conv_auth_lock_ts: float | None = None
        self._conv_auth_lock_sim: float | None = None
        self._conv_auth_from_look: bool = False
        self._desk_activity_ts: float | None = None
        # Speaker row latch: keep last green numbers and User name up.
        self._speaker_latch: dict = {
            "person_id": None,
            "beam": 0.0,
            "camera": 0.0,
            "embeds": 0.0,
            "voice": 0.0,
            "face": 0.0,
            "sex": 0.0,
            "stack": 0.0,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # G3: reload named inventory before loop (survive restarts)
        try:
            info = self._load_inventory_disk()
            if info.get("ok") and self.inventory.headcount() > 0:
                st = self._presence_from_inventory(
                    frame_age=999.0,
                    camera_open=False,
                    camera_blind=True,
                    last_event={
                        "kind": "inventory_reload",
                        "source": "disk",
                        "names": self.inventory.names(),
                        "unnamed": self.inventory.unnamed,
                        "age_sec": info.get("age_sec"),
                        "ts": time.time(),
                    },
                    count_trust="stale",
                    camera_mode="holding",
                    door_names=[],
                )
                if not occupancy_on():
                    self._face_auth_hold.clear()
                    self._user_session = None
                    st.user_auth = False
                    st.user_auth_reason = "user_absent"
                    st.user_face_sticky = False
                    st.user_session_held = False
                # don't thrash save on synthetic reload
                with self._lock:
                    self._state = st
        except Exception:
            pass
        # the User 2026-09-08: any presence restart is guest. Secure disk is
        # audit only; drop AUTH even when the lamp is already amber
        # (_visit_secure_off is a no-op then and would leave a sticky).
        # A Secure *token* (if they were sage) FaceFinds the desk: hit →
        # sage (button green), miss → stay guest and burn the token.
        try:
            self.drop_to_guest(reason="restart")
        except Exception:
            pass
        try:
            self._kick_secure_token_reauth()
        except Exception:
            pass
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="rail-presence-loop", daemon=True
        )
        self._thread.start()
        # the User's architecture: the camera's motion trigger fans out to Argus and
        # FaceID directly, instead of Argus being a downstream consequence of
        # FaceID finding a face first. Observation only for now — it stamps the
        # oracle_event row and does NOT count; apply_visit still owns headcount.
        try:
            from .door_fanout import start as _fanout_start

            from .door_fanout import set_glance as _set_glance

            _set_glance(self.door_glance)
            if occupancy_on() and _fanout_start():
                print("[presence] door trigger fan-out started", flush=True)
            elif not occupancy_on():
                print(
                    "[presence] occupancy mill off; motion glance without mill",
                    flush=True,
                )
                self._start_motion_glance()
        except Exception as e:
            print(f"[presence] fan-out not started: {e}", flush=True)
        try:
            self._beam_orphan_home()
        except Exception:
            pass
        try:
            self._start_pause_home()
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self._beam_rest()
        except Exception:
            pass
        try:
            self._save_inventory_disk(force=True)
        except Exception:
            pass
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)
        self.camera.close()

    def get(self) -> RoomPresence:
        self._expire_busy_if_stale()
        with self._lock:
            st = self._state
            # Always the TTL'd pub. Raw _sound can carry a name on a fresh
            # puck ts; house/glass must not see that.
            st.sound = self._sound_pub()
            try:
                st.speaker = self._speaker_pub()
            except Exception:
                pass
            return st

    def _stamp_sot(self, st: RoomPresence) -> None:
        """Bump rev only on material occupancy/AUTH/Busy change. Proof-of-actor."""
        mat = sot_proof.material(st)
        prev = self._sot_material_last
        st.rev = int(getattr(self, "_sot_rev", 0) or 0)
        st.writer = getattr(self, "_sot_writer", None)
        if mat != prev:
            now = time.time()
            prev_auth = False if prev is None else bool(prev[2])
            prev_reason = "" if prev is None else str(prev[3] or "")
            self._sot_rev = st.rev + 1
            who, why = sot_proof.who_why(st)
            self._sot_writer = {
                "who": who,
                "why": why,
                "rev": self._sot_rev,
                "ts": now,
            }
            st.rev = self._sot_rev
            st.writer = self._sot_writer
            self._sot_material_last = mat
            sot_proof.save_rev(self._sot_rev, self._sot_writer)
            if prev_auth != bool(st.user_auth) or prev_reason != str(st.user_auth_reason or ""):
                row = sot_proof.auth_row(
                    prev_auth=prev_auth,
                    prev_reason=prev_reason,
                    st=st,
                    writer=self._sot_writer,
                    rev=self._sot_rev,
                    now=now,
                )
                sot_proof.append_auth(row)
                print(
                    f"[auth] rev={self._sot_rev} {prev_auth}->{st.user_auth} "
                    f"reason={st.user_auth_reason} writer={who}/{why}",
                    flush=True,
                )
        vl = self._vl_writer_block()
        writers: dict = {}
        if st.writer:
            writers["last"] = st.writer
        if vl:
            writers["vl"] = vl
        st.writers = writers

    def _expire_busy_if_stale(self) -> None:
        """Hung mill + live HTTP must not leave the wall on Busy. AUTH stays off."""
        if not getattr(self, "_room_busy", False):
            return
        now = time.time()
        beat = getattr(self, "_busy_beat_ts", None) or getattr(self, "_busy_since", None) or 0.0
        try:
            beat_f = float(beat or 0.0)
        except (TypeError, ValueError):
            beat_f = 0.0
        if now - beat_f <= BUSY_TTL_SEC:
            return
        self._room_busy = False
        self._busy_reason = "ttl_expired"
        self._busy_since = None
        self._busy_beat_ts = None
        self._busy_unknown = True
        try:
            cur = self._state
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=getattr(cur, "camera_open", True),
                camera_blind=getattr(cur, "camera_blind", False),
                last_event={
                    "kind": "room_busy",
                    "source": "busy_ttl",
                    "on": False,
                    "reason": "ttl_expired",
                    "ts": now,
                },
                count_trust="unknown",
                camera_mode=getattr(cur, "camera_mode", None) or "holding",
                door_names=list(getattr(cur, "door_names", None) or []),
            )
            self._set(st)
        except Exception:
            pass

    def _sound_pub(self) -> dict | None:
        """Hearing field. Az follows the puck; identity expires on id_ts.

        RAIL_SOUND_TTL_SEC (default 6) ages the *name*, not the beam tick.
        Continuous XVF ticks refresh ``ts``; they must not keep a person
        strong. Ghost letter is client-side.
        """
        s = getattr(self, "_sound", None)
        if not isinstance(s, dict) or s.get("az_deg") is None:
            return None
        try:
            ttl = float(os.environ.get("RAIL_SOUND_TTL_SEC", "6"))
        except (TypeError, ValueError):
            ttl = 6.0
        out = dict(s)
        now = time.time()
        try:
            puck_ts = float(out.get("ts") or 0.0)
        except (TypeError, ValueError):
            puck_ts = 0.0
        id_ts = None
        try:
            if out.get("id_ts") is not None:
                id_ts = float(out["id_ts"])
        except (TypeError, ValueError):
            id_ts = None
        named = bool(out.get("person_id")) or str(out.get("id_level") or "") not in (
            "",
            "none",
        )
        if named:
            age = (now - id_ts) if id_ts else None
            if id_ts is None or (ttl > 0 and age is not None and age > ttl):
                for k in ("person_id", "id_level", "id_source", "id_ts"):
                    out.pop(k, None)
        puck_stale = bool(ttl > 0 and puck_ts and (now - puck_ts) > ttl)
        if puck_stale and not out.get("person_id"):
            # Dead puck, no live name. Keep _sound so a late id can merge.
            return None
        return out

    def _queue_auto_rescan(self, reason: str) -> None:
        """Background look-around (limited) — does not block door loop."""
        if self._auto_rescan_thread and self._auto_rescan_thread.is_alive():
            return

        def _run() -> None:
            try:
                from .look_around import maybe_auto_look_around

                out = maybe_auto_look_around(reason=reason)
                self._last_auto_rescan = out
            except Exception as e:
                self._last_auto_rescan = {"ok": False, "error": str(e)[:160]}

        self._auto_rescan_thread = threading.Thread(
            target=_run, name="auto-look-around", daemon=True
        )
        self._auto_rescan_thread.start()

    def _lights_from_grabber(self) -> tuple[bool, dict]:
        """Read lights_out from last grabber poll (same CLIP_DARK_LUM as clipper)."""
        try:
            from .luminance import DARK_LUM

            g = self.last_grabber if isinstance(self.last_grabber, dict) else {}
            if g.get("lights") and isinstance(g["lights"], dict):
                block = dict(g["lights"])
            else:
                lo = bool(g.get("lights_out"))
                block = {
                    "lights_out": lo,
                    "luminance": g.get("luminance"),
                    "threshold": g.get("luminance_threshold", DARK_LUM),
                    "auth_available": not lo,
                    "measure": "median_of_frame_mean_gray_160x90",
                    "refusal": (
                        "I can't verify who's there in the dark" if lo else None
                    ),
                }
            return bool(block.get("lights_out")), block
        except Exception as e:
            return False, {
                "lights_out": False,
                "luminance": None,
                "error": str(e)[:120],
                "auth_available": True,
            }

    def _auth_refused_lights_out(self) -> dict | None:
        """If room is dark, return refusal payload for face-auth endpoints."""
        st = self.get()
        if not getattr(st, "lights_out", False):
            return None
        lights = getattr(st, "lights", {}) or {}
        return {
            "ok": False,
            "refused": True,
            "reason": "lights_out",
            "lights_out": True,
            "luminance": lights.get("luminance"),
            "threshold": lights.get("threshold"),
            "error": lights.get("refusal")
            or "I can't verify who's there in the dark",
            "note": "Face auth unavailable without light. Ear stays open; try again when lights return.",
            "presence": st.to_dict() if hasattr(st, "to_dict") else {},
        }

    def _set(self, st: RoomPresence) -> None:
        try:
            st.camera_token = self._cam_token_pub()
        except Exception:
            st.camera_token = None
        try:
            st.camera_note = self._camera_note_pub()
        except Exception:
            st.camera_note = None
        self._apply_secure_auth(st)
        self._project_visit_lamp(st)
        self._stamp_sot(st)
        prev_dark = getattr(self, "_lights_out_prev", None)
        with self._lock:
            self._state = st
        # Light-return edge: was dark, now lit → Inspector snap-snap
        try:
            now_dark = bool(getattr(st, "lights_out", False))
            if prev_dark is True and now_dark is False:
                self._inspector_auto(st, trigger="light_return")
                st_ev = dict(st.last_event or {})
                # annotate last_event without inventing count
                try:
                    with self._lock:
                        if self._state is st or (
                            self._state and self._state.ts == st.ts
                        ):
                            le = dict(self._state.last_event or {})
                            le["light_return"] = True
                            le["lights"] = getattr(self._state, "lights", {})
                            # leave kind alone if already a real event
                            self._state.last_event = le
                except Exception:
                    pass
            self._lights_out_prev = now_dark
        except Exception:
            self._lights_out_prev = bool(getattr(st, "lights_out", False))
        # G1: room_event ring (rate-limited holds) for ops/Inspector partnership
        try:
            from . import room_events as _re

            beams = {}
            inv = getattr(self, "inventory", None)
            if inv is not None and hasattr(inv, "station_guess"):
                beams = {
                    str(k).lower(): int(v)
                    for k, v in (inv.station_guess or {}).items()
                    if v is not None
                }
            people = []
            for p in st.people or []:
                if hasattr(p, "to_dict"):
                    people.append(p.to_dict())
                elif isinstance(p, dict):
                    people.append(p)
                else:
                    people.append({"name": getattr(p, "name", None)})
            inv_still = {}
            if inv is not None and hasattr(inv, "enter_still"):
                inv_still = dict(getattr(inv, "enter_still", None) or {})
            _re.record_from_presence(
                st.last_event,
                station_guess=beams,
                headcount=st.headcount,
                people=people,
                count_trust=st.count_trust,
                enter_still=inv_still,
            )
        except Exception:
            pass
        # G3: throttle-save inventory after meaningful state
        try:
            le = (st.last_event or {}).get("kind") if st.last_event else None
            if le and le not in ("door_hold", "hold"):
                self._save_inventory_disk(force=False)
            elif st.headcount and st.headcount > 0:
                self._save_inventory_disk(force=False)
        except Exception:
            pass
        # Auto rescan on empty: OFF by default in door-first / user_auth_only.
        # Measured thrash: empty after reauth → look_around pan tour → junk clips
        # while tools also aim (the User 2026-08-03). Opt-in: RAIL_PRESENCE_AUTO_RESCAN=1.
        try:
            auto = os.environ.get("RAIL_PRESENCE_AUTO_RESCAN", "0").strip().lower()
            if auto in ("1", "true", "yes", "on"):
                hc = int(st.headcount or 0)
                prev = getattr(self, "_prev_headcount_for_rescan", None)
                self._prev_headcount_for_rescan = hc
                if hc == 0 and prev is not None and prev > 0:
                    self._queue_auto_rescan("empty_transition")
            else:
                self._prev_headcount_for_rescan = int(st.headcount or 0)
        except Exception:
            pass

    def _inspector_auto(
        self,
        st: RoomPresence,
        *,
        trigger: str,
        person_id_hint: str | None = None,
    ) -> None:
        """RETIRED 2026-08-16 — the Inspector no longer wakes on its own.

        the User: "Do we even need an inspector with argus gateway open" /
        "all Insp ever did was corrypt security files" / "good riddance i say".

        It was a machine for deciding identity when no one was there to say. It
        is superseded by two things that are both better and smaller: Argus
        naming a face from a labelled line-up (4/4 today, including two FaceID
        scored at 0.09 and 0.53 and could not name), and the User simply saying
        who it is while AUTH'd — see store_face.py and house_name_face.

        The module is left in place and callable by hand; only the automatic
        wake-ups are gone, because those were what spent camera time and wrote
        into banks unasked.
        """
        if INSPECTOR_AUTO_OFF:
            return
        try:
            from .inspector import maybe_auto_inspect
            from . import room_events as _re

            re_items = _re.last(1)
            re = re_items[0] if re_items else None
            # Build presence dict without inventing count
            presence = st.to_dict() if hasattr(st, "to_dict") else {}
            maybe_auto_inspect(
                presence,
                trigger=trigger,
                room_event=re if isinstance(re, dict) else (st.last_event or {}),
                person_id_hint=person_id_hint,
                background=True,
            )
        except Exception:
            pass

    def _save_inventory_disk(self, *, force: bool = False) -> None:
        """G3: persist named roster (throttled)."""
        now = time.time()
        if not force and (now - self._inv_save_ts) < 15.0:
            return
        try:
            from .inventory_persist import save_inventory

            if self.inventory.headcount() <= 0 and not force:
                # still save empty so reload doesn't resurrect zombies after clean leave
                if (now - self._inv_save_ts) < 60.0:
                    return
            save_inventory(self.inventory)
            # Face badges ride along (the User 2026-08-04: every service restart
            # was silently logging him out — the badge lived only in RAM).
            try:
                sidecar = _paths.DATA / "auth_hold.json"
                sidecar.write_text(json.dumps(
                    {"ts": now, "hold": self._face_auth_hold}))
            except Exception:
                pass
            self._inv_save_ts = now
        except Exception:
            pass

    def _uncounted_body_check(self, frame_bgr) -> None:
        """Roster says empty but a body fills the door-home view → conflict.

        the User 2026-08-08 (14:19 miss): a person can be in the room without ever
        crossing the door classifier — that pack landed kind=None and the
        roster stayed 0. This needs no camera movement: diff the live frame
        against an empty door-home ref. Three consecutive hits fire ONE
        bounded facefind (rate-limited); a verified-empty stretch refreshes
        the ref so light drift can't fake a body. Kill: RAIL_UNCOUNTED_BODY=0.
        """
        try:
            from .body_audit import (
                UNCOUNTED_MIN_FRAC,
                door_body_frac,
                refresh_door_ref,
            )

            now = time.time()
            if now - getattr(self, "_ub_last_check", 0.0) < 2.0:
                return
            self._ub_last_check = now
            if self.inventory.headcount() != 0:
                self._ub_hits = 0
                return
            # After a real clip leave the doorway can still diff vs the empty
            # ref (or be actually empty). Hunting seats then is the 10:33
            # look_around that ate the walk-in (the User: already in the hall,
            # the announcer said zero, no body to find).
            leave_age = now - float(getattr(self, "_last_clip_leave_ts", 0.0) or 0.0)
            if leave_age < 60.0:
                self._ub_hits = 0
                return
            if getattr(self, "_room_busy", False):
                self._ub_hits = 0
                return
            frac = door_body_frac(frame_bgr)
            if frac < 0:
                return
            if frac >= UNCOUNTED_MIN_FRAC:
                self._ub_hits = getattr(self, "_ub_hits", 0) + 1
                self._ub_empty_since = 0.0
                if self._ub_hits >= 3 and (
                    now - getattr(self, "_ub_last_fire", 0.0) > 300.0
                ):
                    self._ub_last_fire = now
                    self._ub_hits = 0
                    self.inventory.events.append(
                        {
                            "kind": "uncounted_body",
                            "source": "door_delta",
                            "blob_frac": round(frac, 3),
                            "ts": now,
                        }
                    )
                    import threading

                    if self._door_work_pending():
                        print(
                            "[presence] uncounted_body deferred "
                            "(door mill pending)",
                            flush=True,
                        )
                        return
                    print(
                        f"[presence] uncounted_body frac={frac:.3f} "
                        f"→ look_around (hc=0 door blob)",
                        flush=True,
                    )
                    threading.Thread(
                        target=lambda: self.run_smooth_orient_once(
                            reason="uncounted_body"
                        ),
                        name="uncounted-body-orient",
                        daemon=True,
                    ).start()
            else:
                self._ub_hits = 0
                # verified-empty stretch → keep the ref current with the light
                since = getattr(self, "_ub_empty_since", 0.0) or now
                self._ub_empty_since = since
                if now - since > 900 and (
                    now - getattr(self, "_ub_ref_refreshed", 0.0) > 2700
                ):
                    if refresh_door_ref(frame_bgr):
                        self._ub_ref_refreshed = now
        except Exception:
            pass

    def _live_judge_up(self, ttl: float = 30.0) -> bool:
        """Is the LIVE/SCREEN judge (ops) answering? Cached ~30s.

        vlm_live.is_live fails closed, so the static-print veto may only run
        while ops is reachable — a crashed engine must never keep a real
        person out of the room (the User 2026-08-08).
        """
        now = time.time()
        cached = getattr(self, "_live_judge_cache", None)
        if cached and (now - cached[0]) < ttl:
            return cached[1]
        ok = False
        try:
            import urllib.request

            from .vlm_live import VLM_URL  # .../v1/chat/completions

            base = str(VLM_URL).split("/v1/")[0]
            with urllib.request.urlopen(f"{base}/v1/models", timeout=2.0) as r:
                ok = r.status == 200
        except Exception:
            ok = False
        self._live_judge_cache = (now, ok)
        return ok

    def _load_inventory_disk(self) -> dict:
        try:
            from .inventory_persist import load_into_inventory

            info = load_into_inventory(self.inventory)
            self._inv_reload_info = info
            # Occupancy mill off: boot is guest. Do not restore AUTH from
            # disk — that was "stuck on Auth all day" across restarts.
            try:
                if occupancy_on():
                    sidecar = _paths.DATA / "auth_hold.json"
                    if info.get("ok") and sidecar.is_file():
                        d = json.loads(sidecar.read_text())
                        now = time.time()
                        for n, h in (d.get("hold") or {}).items():
                            if (n in self.inventory.present
                                    and now - float(h.get("ts") or 0) < 1800):
                                self._face_auth_hold[n] = h
                        if self._face_auth_hold:
                            info["auth_hold_restored"] = sorted(self._face_auth_hold)
            except Exception:
                pass
            # Downtime honesty (the User 2026-08-07): occupants dropped for being
            # unseen across the gap → the count is a BELIEF that must be
            # re-earned. Schedule one background re-acquisition sweep so the
            # room regains its count from evidence instead of from memory.
            try:
                # Mill off: boot is guest. A reacquire sweep FaceFinds the
                # desk and remints AUTH (measured 2026-09-08 desk_ff 0.782
                # 28 s after a bounce). Occupancy mill still re-earns count.
                if info.get("needs_reacquire") and occupancy_on():
                    import threading

                    def _reacquire() -> None:
                        try:
                            time.sleep(6.0)  # let the rail finish booting
                            self.run_sweep_once(reason="restart_reacquire")
                        except Exception:
                            pass

                    threading.Thread(
                        target=_reacquire, name="restart-reacquire", daemon=True
                    ).start()
            except Exception:
                pass
            return info
        except Exception as e:
            info = {"ok": False, "reason": str(e)[:120]}
            self._inv_reload_info = info
            return info

    def _poll_grabber(self) -> dict | None:
        if self.camera.mode not in ("http", "auto"):
            return None
        try:
            with urllib.request.urlopen(GRABBER_HEALTH_URL, timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            return {"ok": False, "error": f"grabber health unreachable: {e}"[:200]}

    def _set_last_grabber(self, g: dict | None) -> None:
        self.last_grabber = g
        self._last_grabber_ts = time.time() if isinstance(g, dict) else 0.0
        if isinstance(g, dict):
            try:                            # a re-plugged or another camera starts its record over (2026-10-12)
                from . import fixed_cam
                fixed_cam.note_camera(g)
            except Exception:  # noqa: BLE001
                pass

    def _grabber_fresh(self, max_age: float = 1.0) -> dict | None:
        """last_grabber if younger than max_age, else poll :8192 once."""
        now = time.time()
        g = self.last_grabber if isinstance(self.last_grabber, dict) else None
        ts = float(getattr(self, "_last_grabber_ts", 0.0) or 0.0)
        if g is not None and ts and (now - ts) < float(max_age):
            return g
        live = self._poll_grabber()
        self._set_last_grabber(live if isinstance(live, dict) else None)
        return live if isinstance(live, dict) else None

    def _ensure_door_aim(self, *, release_facelock: bool = True, force: bool = False) -> dict:
        """Release facefinder (if needed) and sit on door home.

        After auth/facefind the firmware often stays locked on a face even when
        pan_absolute already reads door home — always hard-release then aim.
        force: Secure click from an in-flight FaceFinder — do not skip because
        the grabber still shows that look's lease.
        """
        out: dict = {"ok": False}
        if _ports.camera_off():                  # no camera: the door aim is a no-op (2026-10-10)
            _ports.say_once("the door aim is a no-op")
            return dict(_ports.OFF)
        # The User's hand outranks the door rest (camera_token ladder): while
        # the joystick holds the token nothing homes the camera. Found
        # 2026-09-07 09:25 — under Secure the dwell loop hammered
        # facelock+return_door every 2 s and the nudges bounced back.
        if self.cam_holder() in self._door_spared():
            h = str(self.cam_holder())
            return {"ok": True, "skipped": "joystick" if h == getattr(self, "CAM_JOY_OWNER", "user_joystick") else h}
        if self.camera.mode not in ("http", "auto"):
            return out
        # AUTH / tools own the gimbal. Door rest is idle — do not yank.
        # Secure-from-FF must yank: the look's lease is what we are ending.
        if not force:
            try:
                g = self._poll_grabber() if hasattr(self, "_poll_grabber") else {}
                if isinstance(g, dict) and (
                    g.get("paused")
                    or (float(g.get("pause_remaining_sec") or 0) > 0)
                    or g.get("tool_hold")
                    or g.get("lease")
                ):
                    out["skipped"] = "paused"
                    out["ok"] = True
                    return out
            except Exception:
                pass
        if release_facelock:
            out["release"] = self.release_facefinder()
        # Prefer grabber return_door (release + aim). force=true so enroll
        # aim_hold cannot block door-hold recovery (the User 2026-08-12).
        door_url = os.environ.get(
            "RAIL_PRESENCE_GRABBER_RETURN_DOOR", _ports.camera("/v1/return_door")
        )
        res = self._grabber_post(
            door_url, {"force": True, "force_door": True}, timeout=20.0
        )
        if res.get("ok") and not res.get("skipped"):
            out.update(res)
            out["ok"] = True
            return out
        if res.get("skipped"):
            # Retry force already sent; fall through to plain aim
            out["return_skipped"] = res
        # Fallback: plain aim at the door pose FROM INIT.
        #
        # zoom was the literal 150 here, so every time this fallback ran it
        # quietly undid the configured door zoom (300 as of 2026-08-16) — the
        # same class of bug as the two stale door-pan copies that silently
        # emptied the trigger ring.
        try:
            # Whole pose from the file, not just the zoom. Taking zoom live
            # while pan stayed on the boot constant is what left this aiming
            # at the old door with the new magnification.
            _p = {"pan": DOOR_PAN, "tilt": DOOR_TILT, "zoom": 150}
            try:
                _p = _door_pose_now()
            except Exception:
                pass
            body = json.dumps(
                {"pan": _p["pan"], "tilt": _p["tilt"], "zoom": _p["zoom"]}
            ).encode()
            req = urllib.request.Request(
                GRABBER_AIM_URL,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                aim = json.loads(r.read().decode())
            out["aim"] = aim
            out["ok"] = bool(aim.get("ok"))
            try:
                # The firmware facefinder is NOT armed at rest.
                #
                # the User 2026-08-17: "its resting frame got messed up after FF
                # fired on media ... first it returned too far right then it
                # flicked a bit to the left."
                #
                # Armed, it hunts on its own: it panned off the door, locked a
                # face on the media screen — a screen face is neither a person
                # nor something the killbook can ever enrol — and left
                # head-framing engaged. The firmware then reports
                # `frame_stuck_on: frame still 1 after release`, and its own
                # audit warns head-framing may override pan, which is exactly
                # the drift-then-flick that follows.
                #
                # AUTH captures still engage it deliberately for one stop and
                # release in a finally (see grabber, seat_shot). Nothing needs
                # it standing armed at an empty doorway.
                out["entry_ff"] = {"armed": False,
                                   "why": "not armed at rest — hunts and sticks"}
                from .camera_call import post as camera_post

                camera_post("/v1/facefinder", {"on": False},
                            owner="entry", timeout=5.0)
            except Exception:
                pass
        except Exception as e:
            out["error"] = str(e)[:200]
        return out

    def mark_away(self, reason: str = "task") -> RoomPresence:
        """Camera borrowed — door continuous feed paused (outage → STALE)."""
        self._mode = "away"
        self._away_reason = reason
        st = self.get()
        st.camera_mode = "away"
        st.count_trust = "stale"
        st.count_certain = False
        st.last_event = {"kind": "away", "reason": reason, "ts": time.time()}
        self._set(st)
        return st

    def _count_trust_from_camera(
        self,
        *,
        frame_age: float,
        frame_ts_list: list[float] | None = None,
    ) -> str:
        """count_trust is CAMERA CONTINUITY only (the User 2026-08-11).

        Stale ONLY on outage:
          • camera_mode away / borrowed
          • latest frame older than gap (always-on feed lag)
          • two sequential buffer frames more than gap apart

        NOT stale: empty door FOV, voice mismatch, soft IDs, unconfirmed roster.
        Default gap matches grabber RAIL_CAMERA_STALE_SEC (2s).
        """
        if self._mode == "away":
            return "stale"
        gap = float(
            os.environ.get(
                "RAIL_COUNT_STALE_GAP_SEC",
                os.environ.get("RAIL_CAMERA_STALE_SEC", "2.0"),
            )
        )
        gap = max(0.5, gap)
        try:
            age = float(frame_age)
        except (TypeError, ValueError):
            age = 999.0
        if age > gap:
            return "stale"
        if frame_ts_list and len(frame_ts_list) >= 2:
            ts = sorted(float(t) for t in frame_ts_list if t is not None)
            for i in range(1, len(ts)):
                if (ts[i] - ts[i - 1]) > gap:
                    return "stale"
        return "fact"

    def _note_left(self, names: list[str], *, now: float | None = None) -> None:
        """Tombstone: block CODE memory write-through from resurrecting leavers."""
        now = now if now is not None else time.time()
        until = now + CODE_RESURRECT_BLOCK_SEC
        for n in names:
            n = (n or "").strip().lower()
            if not n or n.startswith("unknown"):
                continue
            self._left_block_until[n] = until
            self._id_certain.pop(n, None)
            self._id_sources.pop(n, None)
            if hasattr(self, "_auth_levels"):
                self._auth_levels.pop(n, None)
            # Leave clears sticky face badge
            if hasattr(self, "_face_auth_hold"):
                self._face_auth_hold.pop(n, None)
            if n == self._user_id():
                # voice sticky alone must not outlive leave
                self._last_user_voice = {
                    "match": False,
                    "sim": None,
                    "ts": 0.0,
                    "person_id": None,
                }
                self._user_session = None
        # prune old tombstones
        self._left_block_until = {
            k: v for k, v in self._left_block_until.items() if v > now
        }

    def _blocked_from_resurrect(self, name: str, *, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        n = (name or "").strip().lower()
        return float(self._left_block_until.get(n) or 0) > now

    def apply_oracle_visit(self, body: dict | None = None) -> dict:
        """Argus visit instruction. Rails apply; no AUTH grant."""
        from .oracle_visit import apply_visit

        body = body if isinstance(body, dict) else {}
        if not occupancy_on():
            return {
                "ok": False,
                "applied": False,
                "reason": "argus_id_off",
                "note": "identity is FaceID visor; Argus does not write visits",
            }
        if not hasattr(self, "_oracle_visits") or self._oracle_visits is None:
            self._oracle_visits = {}
        now = time.time()
        hc0 = int(self.inventory.headcount())
        lm = (
            body.get("leave_match")
            if isinstance(body.get("leave_match"), dict)
            else None
        )
        out = apply_visit(
            inventory=self.inventory,
            visits=self._oracle_visits,
            event=str(body.get("event") or ""),
            person_id=str(body.get("person_id") or body.get("name") or ""),
            now=now,
            clothes=body.get("clothes") or body.get("clothing"),
            hair=body.get("hair"),
            sex=body.get("sex"),
            faceid=body.get("faceid") if isinstance(body.get("faceid"), dict) else None,
            pack_id=str(body.get("pack_id") or body.get("pack") or "") or None,
            note=body.get("note"),
            event_id=str(body.get("event_id") or "") or None,
            leave_match=lm,
            # `out` is this call's own result: reading it here raised UnboundLocalError on every visit without
            # user_exit (found by the undefined-name scan, 2026-10-10)
            user_exit=body.get("user_exit") if "user_exit" in body else None,
            user_exit_set=bool(
                body.get("user_exit_set")
                or "user_exit" in body
            ),
        )
        if lm is None and isinstance(out.get("leave_match"), dict):
            lm = out["leave_match"]
        if out.get("reason") == "user_exit_needs_confirm":
            ue = out.get("user_exit") if "user_exit" in out else body.get("user_exit")
            ue_set = bool(
                out.get("user_exit_set")
                or "user_exit" in body
                or "user_exit" in out
            )
            try:
                conf = self.confirm_user_exit(
                    str(out.get("person_id") or ""),
                    user_exit=ue,
                    user_exit_set=ue_set,
                )
            except Exception as e:
                conf = {"confirmed": False, "reason": f"confirm_exc:{e}"[:160]}
            out["confirm"] = conf
            pid = str(out.get("person_id") or "")
            row = (self._oracle_visits or {}).get(pid) or {}
            if conf.get("confirmed") and pid:
                row["exit_ts"] = now
                row["pending_confirm"] = False
                row["event"] = "exit"
                if lm:
                    row["leave_match"] = dict(lm)
                self._oracle_visits[pid] = row
                try:
                    self.inventory.force_leave(
                        [pid], source="argus_exit_confirmed", leave_match=lm
                    )
                except Exception:
                    pass
                try:
                    from .oracle_event import record_leave

                    record_leave(
                        str(body.get("event_id") or body.get("pack_id") or ""),
                        pid,
                        now=now,
                        leave_match=lm,
                    )
                except Exception:
                    pass
                try:
                    self._note_left([pid], now=now)
                except Exception:
                    pass
                out["reason"] = "user_exit_confirmed"
                out["event"] = "exit"
                out["leave_match"] = lm
                self._last_clip_leave_ts = now
            else:
                row["event"] = "sighting"
                row["exit_ts"] = None
                row["pending_confirm"] = False
                self._oracle_visits[pid] = row
                out["event"] = "sighting"
                out["reason"] = str(conf.get("reason") or "user_still_here")
                out["applied"] = True
                if str(conf.get("reason") or "") == "user_in_seat" and pid:
                    try:
                        self._note_face_auth(pid, source="seat_6")
                    except Exception:
                        pass
        elif out.get("applied") and out.get("event") == "exit":
            pid = str(out.get("person_id") or "")
            if pid:
                try:
                    self._note_left([pid], now=now)
                except Exception:
                    pass
        try:
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=getattr(cur, "camera_open", True),
                camera_blind=getattr(cur, "camera_blind", False),
                last_event={
                    "kind": "oracle_visit",
                    "source": "argus",
                    "event": out.get("event"),
                    "person_id": out.get("person_id"),
                    "reason": out.get("reason"),
                    "headcount": out.get("headcount_after"),
                    "ts": now,
                    "leave_match": lm,
                    "clothes": body.get("clothes") or body.get("clothing"),
                    "hair": body.get("hair"),
                    "sex": body.get("sex"),
                },
                count_trust=getattr(cur, "count_trust", None) or "fact",
                camera_mode=self._mode if getattr(self, "_mode", "") != "reconciling" else "holding",
                door_names=list(getattr(cur, "door_names", None) or []),
            )
            self._set(st)
        except Exception:
            pass
        out["headcount_before"] = hc0
        out["headcount_after"] = int(self.inventory.headcount())
        return out

    def set_room_busy(self, on: bool, *, reason: str = "", now: float | None = None) -> dict:
        """Leftover Busy HTTP. Mill no longer POSTs linger or flood.

        Speech look ignores this flag. Occupancy is not wiped.
        """
        now = float(now if now is not None else time.time())
        on = bool(on)
        was = bool(getattr(self, "_room_busy", False))
        uid = self._user_id()
        out: dict[str, Any] = {
            "ok": True,
            "room_busy": on,
            "reason": str(reason or ""),
            "was": was,
        }
        why = str(reason or "")
        if on:
            if not was:
                self._busy_auth_scanned = False
            # Linger shares this flag. Only queue-flood arms the AUTH scan.
            if "queue_flood" in why:
                self._busy_auth_scan_armed = True
            self._room_busy = True
            self._busy_reason = why or "linger"
            if not self._busy_since:
                self._busy_since = now
            self._busy_beat_ts = now
            self._busy_unknown = False
            self._auth_blocked_until_recheck = True
            if uid in getattr(self, "_face_auth_hold", {}):
                self._face_auth_hold.pop(uid, None)
            self._user_session = None
            out["auth"] = False
            out["auth_reason"] = "room_busy"
            out["auth_scan_armed"] = bool(self._busy_auth_scan_armed)
        else:
            self._room_busy = False
            self._busy_reason = ""
            self._busy_since = None
            self._busy_beat_ts = None
            self._busy_unknown = False
            # AUTH stays off until the one Busy scan (if armed) or desk recheck.
            out["auth"] = False
            out["auth_reason"] = "busy_await_recheck"
            if was:
                try:
                    if self._busy_auth_scan_armed and not self._busy_auth_scanned:
                        out["recount"] = self._busy_auth_scan()
                    else:
                        out["recount"] = self._busy_off_recount()
                except Exception as e:
                    out["recount"] = {"ok": False, "error": f"{type(e).__name__}:{e}"[:160]}
        try:
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=getattr(cur, "camera_open", True),
                camera_blind=getattr(cur, "camera_blind", False),
                last_event={
                    "kind": "room_busy",
                    "source": "door_fanout",
                    "on": on,
                    "reason": str(reason or ""),
                    "ts": now,
                },
                count_trust=getattr(cur, "count_trust", None) or "fact",
                camera_mode=self._mode if getattr(self, "_mode", "") != "reconciling" else "holding",
                door_names=list(getattr(cur, "door_names", None) or []),
            )
            self._set(st)
            out["user_auth"] = bool(st.user_auth)
            out["user_auth_reason"] = st.user_auth_reason
            out["headcount"] = int(st.headcount)
        except Exception as e:
            out["publish_error"] = f"{type(e).__name__}:{e}"[:160]
        return out

    def set_f2f(
        self,
        on: bool,
        *,
        reason: str = "",
        look: bool = False,
        force: bool = False,
        hour: int | None = None,
        person_id: str | None = None,
    ) -> dict:
        """Alias for speech look. Not a mode. Busy / mill queue are not a veto.

        Media beam: do not look. Inspector lease is not stolen. Occupancy
        is unchanged. Camera dwells, then door rest on quiet.
        """
        now = time.time()
        on = bool(on)
        if on:
            try:
                self._idle_gate().turn(now)        # a session opened on her name: the door loop at full rate (round 16)
            except Exception:
                pass
        hour_i = None
        try:
            if hour is not None:
                hour_i = int(hour)
        except (TypeError, ValueError):
            hour_i = None
        pid = str(person_id or "").strip().lower() or None
        out: dict[str, Any] = {
            "ok": True,
            "f2f": on,
            "reason": str(reason or ""),
            "look": bool(look),
            "hour": hour_i,
        }
        pose = None
        click_lock = getattr(self, "_visit_click_lock", None)
        if on and click_lock is not None and click_lock.locked():
            out["skipped"] = "visit_click"
            out["look"] = False
            return out
        if on and look:
            blocked = self._ff_at_beam_blocked()
            if blocked:
                # Sage (plain or hold) — camera stays on the door (Law 2).
                # Hold is casual-unlock, not a look. Faces do not matter.
                out["skipped"] = blocked
                out["look"] = False
                if blocked == "secure":
                    out["f2f"] = False
                    # Secure: no look, but remember the session so a mid-comms
                    # Secure drop can resume FF on the User (_visit_resume_ff).
                    self._visit_ff_saved = {
                        "reason": str(reason or ""),
                        "hour": hour_i,
                        "person": pid,
                        "ts": now,
                    }
                    try:
                        self._visit_lamp_hold_door()
                    except Exception:
                        pass
                return out
        if on and look and self._paused_holds_home():
            # Paused: the camera stays home at the door (the User 2026-10-09). No look starts; one in flight ends.
            out["skipped"] = "paused"
            out["look"] = False
            out["f2f"] = False
            if getattr(self, "_f2f", False) or getattr(self, "_beam_lease_id", None):
                self._beam_rest()
            return out
        if on and look:
            from .beam_look import SESSION_LOOK, VAD_LOOK, look_ok
            from .seat_slots import media_station, user_seat

            already = bool(getattr(self, "_f2f", False))
            gate = look_ok(
                already_looking=already,
                reason=str(reason or ""),
            )
            if gate:
                out["ok"] = True
                out["f2f"] = already
                out["skipped"] = gate
                why = str(reason or "").strip().lower()
                if gate == "need_session" and why in VAD_LOOK:
                    if self._note_desk_activity():
                        out["desk_activity"] = True
                return out
            from .seat_shot import pose_for_hour

            why = str(reason or "").strip().lower()
            # Vocative beam station (usually 6, not always). Unmapped wall
            # hours fall back to desk. Media hour stays so media_beam can skip.
            # Caller hour on VAD_LOOK is station_hour residual-0 — ignore it.
            if why in VAD_LOOK:
                hour_i = None
                if already:
                    try:
                        hour_i = int(getattr(self, "_f2f_hour", None) or 0) or None
                    except (TypeError, ValueError):
                        hour_i = None
            if why in VAD_LOOK or why in SESSION_LOOK:
                # Vocative session_open without an hour goes to desk, not a
                # leftover TV lobe.
                ms = media_station()
                at_media = (
                    hour_i is not None
                    and ms is not None
                    and int(hour_i) == int(ms)
                )
                if hour_i is not None and pose_for_hour(hour_i) is None and not at_media:
                    hour_i = None
                if hour_i is None:
                    try:
                        hour_i = int(user_seat())
                    except (TypeError, ValueError):
                        hour_i = 6
            else:
                hour_i = None
            from .beam_look import media_blocks_look

            block = media_blocks_look(hour_i)
            if block:
                out["ok"] = True
                out["f2f"] = False
                out["skipped"] = block
                out["hour"] = hour_i
                if getattr(self, "_f2f", False) or getattr(self, "_beam_lease_id", None):
                    try:
                        self._beam_rest()
                    except Exception:
                        pass
                return out
            ms = media_station()
            if ms is not None and hour_i is not None and int(hour_i) == int(ms):
                out["ok"] = True
                out["f2f"] = False
                out["skipped"] = "media_beam"
                out["hour"] = hour_i
                return out
            if hour_i is not None:
                pose = pose_for_hour(hour_i)
            if pose is None:
                uid = self._user_id()
                if pid == uid:
                    try:
                        pose = pose_for_hour(int(user_seat()))
                    except Exception:
                        pose = pose_for_hour(6)
            if pose is None:
                out["ok"] = True
                out["f2f"] = False
                out["skipped"] = "no_station"
                out["hour"] = hour_i
                return out
            out["hour"] = pose.get("station")
            out["stop"] = pose.get("id")
        was = bool(self._f2f)
        prev_hour = getattr(self, "_f2f_hour", None)
        self._f2f_hour = hour_i if on else None
        self._f2f_person = pid if on else None
        self._f2f_pose = pose if on and look else None
        if on:
            self._f2f = True
            if not getattr(self, "_f2f_scan_ready", False) or not was:
                self._f2f_scan_ready = False
            if not self._f2f_since:
                self._f2f_since = now
            self._f2f_reason = str(reason or "speech")
        else:
            self._f2f = False
            self._f2f_scan_ready = False
            self._f2f_since = None
            self._f2f_reason = ""
            self._f2f_hour = None
            self._f2f_person = None
            self._f2f_pose = None
            # Comms over: nothing to resume if Secure drops later.
            self._visit_ff_saved = None
            try:
                self._beam_rest()
            except Exception:
                pass
        try:
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=getattr(cur, "camera_open", True),
                camera_blind=getattr(cur, "camera_blind", False),
                last_event={
                    "kind": "f2f",
                    "on": on,
                    "reason": str(reason or ""),
                    "was": was,
                    "hour": hour_i,
                    "stop": (pose or {}).get("id") if pose else None,
                    "ts": now,
                },
                count_trust=getattr(cur, "count_trust", None) or "fact",
                camera_mode="f2f" if on else (
                    self._mode if getattr(self, "_mode", "") != "reconciling" else "holding"
                ),
                door_names=list(getattr(cur, "door_names", None) or []),
            )
            self._set(st)
            out["camera_mode"] = st.camera_mode
            out["wall_count"] = "F2F" if st.f2f else (
                "Busy" if st.room_busy else None
            )
            out["headcount"] = int(st.headcount)
        except Exception as e:
            out["publish_error"] = f"{type(e).__name__}:{e}"[:160]
        if on and look:
            print(
                f"[beam-look] session reason={reason!s} hour={out.get('hour')} "
                f"stop={out.get('stop')} pid={pid}",
                flush=True,
            )
            self._beam_speech_ts = now
            if was and prev_hour == out.get("hour") and not self._f2f_looking:
                out["skipped_look"] = "same_station"
                return out
            with self._f2f_lock:
                if self._f2f_looking:
                    out["look"] = False
                    out["skipped_look"] = "in_flight"
                    return out
                self._f2f_looking = True
            threading.Thread(
                target=self._beam_aim_then_dwell,
                name="beam-look",
                daemon=True,
            ).start()
        return out

    def request_beam_look(
        self,
        *,
        az: float | None = None,
        hour: int | None = None,
        person_id: str | None = None,
        reason: str = "speech",
    ) -> dict:
        """VAD / sound.heard path. Does not write last_event. Not occupancy."""
        from .beam_look import decide

        now = time.time()
        if getattr(self, "_f2f_lock", None) is None:
            return {"ok": True, "skipped": "unready"}
        blocked = self._ff_at_beam_blocked()
        if blocked:
            return {"ok": True, "skipped": blocked}
        if self._paused_holds_home():
            return {"ok": True, "skipped": "paused"}
        click_lock = getattr(self, "_visit_click_lock", None)
        if click_lock is not None and click_lock.locked():
            return {"ok": True, "skipped": "visit_click"}
        d = decide(
            hour=hour,
            az=az,
            person_id=person_id,
            user_id=self._user_id(),
            current_hour=getattr(self, "_f2f_hour", None) if getattr(self, "_f2f", False) else None,
            looking=bool(getattr(self, "_f2f", False)),
        )
        if d.get("skipped"):
            print(
                f"[beam-look] skip={d.get('skipped')} hour={d.get('hour')} az={az}",
                flush=True,
            )
            if d.get("skipped") in ("media_beam", "media_on", "media_locus"):
                if getattr(self, "_f2f", False) or getattr(self, "_beam_lease_id", None):
                    try:
                        self._beam_rest()
                    except Exception:
                        pass
            return d
        self._beam_speech_ts = now
        self._f2f = True
        if not getattr(self, "_f2f_since", None):
            self._f2f_since = now
        self._f2f_reason = str(reason or "speech")
        self._f2f_hour = d.get("hour")
        self._f2f_person = str(person_id or "").strip().lower() or None
        self._f2f_pose = d.get("pose")
        if os.environ.get("PYTEST_CURRENT_TEST"):
            d["deferred"] = "pytest"
            return d
        with self._f2f_lock:
            if self._f2f_looking:
                d["skipped_look"] = "in_flight"
                return d
            self._f2f_looking = True
        print(
            f"[beam-look] aim stop={d.get('stop')} hour={d.get('hour')} az={az}",
            flush=True,
        )
        threading.Thread(
            target=self._beam_aim_then_dwell,
            name="beam-look",
            daemon=True,
        ).start()
        return d

    def _beam_aim_then_dwell(self) -> None:
        """Aim the station, FaceID ID, stay. Rest is quiet, not look_done."""
        try:
            if (not getattr(self, "_f2f", False)) or self._visit_is_secure():
                # Click stole the look, or Secure is watching the door.
                return
            from . import beam_look as bl

            pose = dict(getattr(self, "_f2f_pose", None) or {})
            if not pose:
                from .seat_shot import pose_for_hour

                pose = pose_for_hour(getattr(self, "_f2f_hour", None)) or {}
            if not pose:
                return
            tok = self.cam_take("beam_look", bl.LEASE_TTL, why=str(getattr(self, "_f2f_reason", "") or "look"))
            if not tok.get("ok"):
                print(f"[beam-look] camera held by {tok.get('owner')} — look skipped", flush=True)
                return
            self._beam_lease_id = tok.get("lease_id")
            held = bl.aim_hold(
                pose,
                lease_id=self._beam_lease_id,
                owner="beam_look",
            )
            if held.get("lease_id"):
                self._beam_lease_id = held.get("lease_id")
            if held.get("error"):
                print(
                    f"[beam-look] aim error={held.get('error')} "
                    f"stop={pose.get('id')} hour={pose.get('station')}",
                    flush=True,
                )
            if held.get("error") == "inspector":
                return
            frames = held.get("frames") or []
            if frames:
                seen = self._identify_all_in_jpegs(frames, source="beam_look")
                self._f2f_scan_ready = True
                self._grant_user_from_look(seen, source="beam_look")
        except Exception as e:
            print(f"[beam-look] dwell {type(e).__name__}:{e}"[:160], flush=True)
        finally:
            self._f2f_looking = False

    def _f2f_look_then_home(self) -> None:
        """Compat name for tests. Dwells; does not home at end."""
        self._beam_aim_then_dwell()

    def _beam_maybe_rest(self) -> None:
        """Door rest on quiet — unless this is a Crissa session look.

        Session looks hold until ear-bridge POSTs /v1/f2f on=false.
        Safety cap 180 s if the session-end POST never arrives.
        """
        from .beam_look import DWELL_SEC, SESSION_LOOK, renew

        if self._visit_is_secure():
            # Secure: the camera belongs on the door. Hold it there only when
            # something needs undoing — a live look, or the grabber off the
            # door — and never against the User's joystick. Not a 2 s hammer
            # on a camera already resting (the User 2026-09-07: "something fights
            # to return still every second or so").
            if self.cam_holder() in self._door_spared():
                return
            g = getattr(self, "last_grabber", None) or {}
            off_door = isinstance(g, dict) and g.get("at_door") is False
            if getattr(self, "_f2f", False) or getattr(self, "_f2f_looking", False) or off_door:
                try:
                    self._visit_lamp_hold_door()
                except Exception:
                    pass
            return
        if not getattr(self, "_f2f", False):
            return
        if getattr(self, "_f2f_looking", False):
            return
        why = str(getattr(self, "_f2f_reason", "") or "").strip().lower()
        now = time.time()
        if why in SESSION_LOOK:
            if not self.cam_renew("beam_look").get("ok"):
                # the token moved (joystick / Secure glance / FF chase): stop looking
                self._beam_rest()
                return
            since = float(getattr(self, "_f2f_since", None) or 0.0)
            if since and (now - since) > 180.0:
                try:
                    self.set_f2f(False, reason="session_cap")
                except Exception:
                    self._beam_rest()
            return
        ts = float(getattr(self, "_beam_speech_ts", None) or 0.0)
        if ts and (now - ts) < DWELL_SEC:
            if not self.cam_renew("beam_look").get("ok"):
                # the token moved (joystick / Secure glance / FF chase): stop looking
                self._beam_rest()
                return
            return
        self._beam_rest()

    def _beam_orphan_home(self) -> None:
        """A killed look must not leave the gimbal on a seat with a dead lease."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        from .beam_look import camera_health, lease_owner, rest

        owner = lease_owner(camera_health())
        if owner not in ("beam_look", "f2f"):
            return
        try:
            from . import room_sweep as _rs

            _rs._post(
                "/v1/pause",
                {
                    "owner": "beam_look",
                    "steal": True,
                    "ttl_sec": 8,
                    "reason": "restart_home",
                },
                timeout=2.0,
            )
        except Exception:
            pass
        rest(None)
        print("[beam-look] orphan lease homed", flush=True)

    def _ear_paused(self) -> bool:
        """The ear's own pause (rail-voice :8194/health `paused`), cached 1.5 s. Unreadable = not paused:
        an unknown never moves the camera or refuses a look."""
        if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("RAIL_EAR_HEALTH_URL"):
            return False   # a test never reads the live ear
        now = time.time()
        c = getattr(self, "_ear_paused_cache", None)
        if c and now - c[0] < 1.5:
            return c[1]
        val = False
        try:
            import urllib.request

            url = os.environ.get("RAIL_EAR_HEALTH_URL", _ports.url("bridge", "/health"))
            with urllib.request.urlopen(url, timeout=0.5) as r:
                val = json.loads(r.read() or b"{}").get("paused") is True
        except Exception:
            val = False
        self._ear_paused_cache = (now, val)
        return val

    def _paused_holds_home(self) -> bool:
        """Paused = the camera goes home to the door and stays (the User 2026-10-09). Only the automatic
        look (beam_look: speech / VAD / session) is refused or ended; the click, Secure, unlock, reauth and
        Register looks and the joystick are not looks of this kind and are untouched. Kill RAIL_PAUSE_HOME=0."""
        if os.environ.get("RAIL_PAUSE_HOME", "1") == "0":
            return False
        return self._ear_paused()

    def _start_pause_home(self) -> None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        t = getattr(self, "_pause_home_thread", None)
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=self._pause_home_loop, name="pause-home", daemon=True)
        self._pause_home_thread = t
        t.start()

    def _pause_home_tick(self) -> bool:
        """One check: Paused and an automatic look holds the camera -> end it and home. True when it acted."""
        if not self._paused_holds_home():
            return False
        if self.cam_holder() == "beam_look" or getattr(self, "_f2f", False):
            print("[pause-home] ear paused: ending the look, camera home to the door", flush=True)
            self._beam_rest()
            return True
        return False

    def _pause_home_loop(self) -> None:
        stop = getattr(self, "_stop", None)
        while True:
            try:
                self._pause_home_tick()
            except Exception as e:  # noqa: BLE001
                print(f"[pause-home] {type(e).__name__}:{e}"[:120], flush=True)
            if stop is not None:
                if stop.wait(2.0):
                    return
            else:
                time.sleep(2.0)

    def _beam_rest(self) -> None:
        try:
            self.cam_give_back("beam_look")
        except Exception:
            pass
        self._beam_lease_id = None
        self._f2f = False
        self._f2f_since = None
        self._f2f_reason = ""
        self._f2f_hour = None
        self._f2f_person = None
        self._f2f_pose = None

    def _crop_bbox_jpeg(self, im, bbox) -> bytes | None:
        if im is None or not bbox or len(bbox) < 4:
            return None
        import cv2

        h, w = im.shape[:2]
        x1, y1, x2, y2 = [int(float(v)) for v in bbox[:4]]
        pad = 12
        x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
        x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
        if x2 <= x1 or y2 <= y1:
            return None
        ok, buf = cv2.imencode(".jpg", im[y1:y2, x1:x2])
        return bytes(buf) if ok else None

    def note_faceid_hud(
        self,
        *,
        source: str,
        names: dict | None = None,
        faces: list | None = None,
        n_faces: int = 0,
        frame_w: int | None = None,
        frame_h: int | None = None,
        frame_jpeg: bytes | None = None,
        crop_jpeg: bytes | None = None,
        locked: str | None = None,
    ) -> None:
        """Last FaceID reading for the visor. Not occupancy."""
        now = time.time()
        faces = list(faces or [])
        names = dict(names or {})
        if not locked and faces:
            locked = str(faces[0].get("name") or "") or None
        self._faceid_hud = {
            "ok": True,
            "ts": now,
            "source": str(source or "faceid"),
            "n_faces": int(n_faces or 0),
            "names": names,
            "faces": faces,
            "locked": locked,
            "frame_w": frame_w,
            "frame_h": frame_h,
            "has_frame": bool(frame_jpeg),
            "has_crop": bool(crop_jpeg),
            "f2f": bool(self._f2f),
            "room_busy": bool(self._room_busy),
        }
        if frame_jpeg:
            self._faceid_frame = frame_jpeg
        if crop_jpeg:
            self._faceid_crop = crop_jpeg
        self._log_faceid_faces(
            source=str(source or "faceid"),
            faces=faces,
            n_faces=int(n_faces or 0),
            crop_jpeg=crop_jpeg,
            locked=locked,
        )
        self._stamp_speaker_face_if_f2f(
            source=str(source or "faceid"),
            faces=faces,
            locked=locked,
        )
        self._update_conv_auth_lock(
            locked=locked,
            faces=faces,
            n_faces=int(n_faces or 0),
            now=now,
        )

    def _conv_auth_min_sim(self) -> float:
        try:
            return float(
                os.environ.get("HOUSE_LIVE_FACE_MIN_SIM")
                or os.environ.get("RAIL_PRESENCE_MATCH_SIM")
                or "0.60"
            )
        except (TypeError, ValueError):
            return 0.60

    def _conv_auth_profile_min_sim(self) -> float:
        """Side/profile keep while at the look station. Front lock stays 0.60."""
        try:
            return float(os.environ.get("HOUSE_LIVE_PROFILE_MIN_SIM", "0.40"))
        except (TypeError, ValueError):
            return 0.40

    # A door check is not a look at the User (the User 2026-10-09).
    DOOR_SOURCES = ("door_glance", "blind_gap_door")

    def _profile_keep_ok(self, source: str | None) -> bool:
        """The 0.40 profile keep names a side-on User face on a SEAT look only. At the
        door a 0.40-0.60 face stays unnamed (it was published as the User). Secure is
        unchanged: a named or an unmatched door face both end it, and the tripwire fires
        on detect. Kill RAIL_DOOR_PROFILE_KEEP=1 (the door names it the User again)."""
        if str(source or "") in self.DOOR_SOURCES:
            return os.environ.get("RAIL_DOOR_PROFILE_KEEP", "0") == "1"
        return True

    def _door_skips_pub(self) -> dict:
        """Today's door visitor tally: faces logged (X#) and faces skipped as the User."""
        day = time.strftime("%Y-%m-%d")
        c = dict(getattr(self, "_door_skips", None) or {})
        if c.get("day") != day:
            c = {"day": day, "skipped": 0, "logged": 0}
        c["skip_min_sim"] = self._conv_auth_profile_min_sim()
        return c

    def _note_door_visitor(self, *, skipped: bool, sim: float | None = None) -> None:
        c = self._door_skips_pub()
        key = "skipped" if skipped else "logged"
        c[key] = int(c.get(key) or 0) + 1
        if skipped:
            c["last_skip_sim"] = None if sim is None else round(float(sim), 3)
            c["last_skip_ts"] = round(time.time(), 1)
        self._door_skips = c

    def _conv_auth_hold_s(self) -> float:
        try:
            return max(0.0, float(os.environ.get("HOUSE_AUTH_OFF_CAMERA_S", "5")))
        except (TypeError, ValueError):
            return 5.0

    def _update_conv_auth_lock(
        self,
        *,
        locked: str | None,
        faces: list,
        n_faces: int,
        now: float,
    ) -> None:
        """Refresh / clear conversation AUTH. Occupancy sticky is not this.

        Front lock ≥ 0.60. Profile/side ≥ PROFILE_MIN keeps the User. While
        FaceFinder is on, any det in FOV (unlabeled / back-of-head blob)
        refreshes presence. Empty FOV does not clear — 5s grace to turn.
        Another named face clears immediately.
        """
        uid = self._user_id()
        lock = str(locked or "").strip().lower()
        recs = [f for f in (faces or []) if isinstance(f, dict)]
        sim = None
        named = []
        for rec in recs:
            nm = str(rec.get("name") or "").strip().lower()
            if not nm:
                continue
            named.append(nm)
            if nm != uid:
                continue
            try:
                sim = float(rec["sim"]) if rec.get("sim") is not None else None
            except (TypeError, ValueError):
                sim = None
        profile_min = self._conv_auth_profile_min_sim()
        user_named = lock == uid or uid in named
        user_ok = user_named and (sim is None or sim >= profile_min)
        if user_ok:
            self._conv_auth_lock_ts = now
            self._conv_auth_lock_sim = sim
            if getattr(self, "_f2f", False):
                self._conv_auth_from_look = True
            return
        other = False
        if lock and lock != uid:
            other = True
        if any(nm != uid for nm in named):
            other = True
        if other:
            self._conv_auth_lock_ts = None
            self._conv_auth_lock_sim = None
            self._conv_auth_from_look = False
            self._desk_activity_ts = None
            return
        try:
            n = int(n_faces or 0)
        except (TypeError, ValueError):
            n = len(recs)
        # FF still sees a head/body — hold AUTH even without a name.
        if getattr(self, "_f2f", False) and n > 0 and getattr(
            self, "_conv_auth_from_look", False
        ):
            self._conv_auth_lock_ts = now

    def _note_desk_activity(self) -> bool:
        """Paused VAD / keyboard at the desk. Not a look. Not LISTENING."""
        if not getattr(self, "_conv_auth_from_look", False):
            return False
        if not getattr(self, "_conv_auth_lock_ts", None):
            return False
        self._desk_activity_ts = time.time()
        return True

    def _conv_auth_desk_s(self) -> float:
        try:
            return max(0.0, float(os.environ.get("HOUSE_AUTH_DESK_S", "45")))
        except (TypeError, ValueError):
            return 45.0

    def _attach_conv_auth_hold(self, h: dict) -> dict:
        """Publish AUTH hold.

        FF look + a det in FOV: hold (presence, including unlabeled).
        FF look + empty FOV: 5s grace to turn around, then Guest.
        After the look ends: 5s empty, or desk-click persist.
        """
        now = time.time()
        hold_s = self._conv_auth_hold_s()
        ts = getattr(self, "_conv_auth_lock_ts", None)
        looking = bool(getattr(self, "_f2f", False) or h.get("f2f"))
        try:
            n_faces = int(h.get("n_faces") or 0)
        except (TypeError, ValueError):
            n_faces = 0
        lock = str(h.get("locked") or "").strip().lower()
        faces = h.get("faces") if isinstance(h.get("faces"), list) else []
        named_other = bool(lock and lock != self._user_id())
        for rec in faces:
            if not isinstance(rec, dict):
                continue
            nm = str(rec.get("name") or "").strip().lower()
            if nm and nm != self._user_id():
                named_other = True
        empty = n_faces <= 0 and not lock and not faces
        age = None
        if ts:
            try:
                age = now - float(ts)
            except (TypeError, ValueError):
                age = None
        remaining = 0.0
        holding = False
        station = False
        user_live = lock == self._user_id()
        from_look = bool(getattr(self, "_conv_auth_from_look", False))
        if named_other:
            holding = False
        elif looking and ts and age is not None and not user_live:
            if n_faces > 0:
                holding = True
                station = True
            elif 0 <= age <= hold_s:
                remaining = max(0.0, hold_s - age)
                holding = remaining > 0
        else:
            desk_age = None
            dts = getattr(self, "_desk_activity_ts", None)
            if dts:
                try:
                    desk_age = now - float(dts)
                except (TypeError, ValueError):
                    desk_age = None
            desk_s = self._conv_auth_desk_s()
            if (
                not looking
                and not named_other
                and from_look
                and ts
                and desk_age is not None
                and 0 <= desk_age <= desk_s
            ):
                holding = True
                station = True
                h["auth_desk"] = True
            elif (
                not looking
                and empty
                and from_look
                and age is not None
                and 0 <= age <= hold_s
            ):
                remaining = max(0.0, hold_s - age)
                holding = remaining > 0
        h["auth_desk"] = bool(h.get("auth_desk"))
        sage = self._visit_is_secure()
        if named_other:
            holding = False
        elif sage and (empty or user_live or n_faces <= 0):
            # Secure pauses BAU AUTH expiry (5 s empty FOV / 45 s desk).
            holding = True
            remaining = None
            h["auth_secure"] = True
        h["auth_hold"] = holding
        if station or remaining is None:
            h["auth_hold_s"] = None
        else:
            h["auth_hold_s"] = round(remaining, 2)
        h["auth_station"] = station
        h["last_user_lock_age_s"] = None if age is None else round(age, 2)
        h["last_user_lock_sim"] = getattr(self, "_conv_auth_lock_sim", None)
        return h

    def _faceidog_root(self) -> Path:
        raw = os.environ.get("RAIL_FACEID_LOG")
        if raw:
            return Path(raw)
        return _paths.DATA / "faceidog"

    def _log_faceid_faces(
        self,
        *,
        source: str,
        faces: list,
        n_faces: int,
        crop_jpeg: bytes | None,
        locked: str | None,
    ) -> None:
        """File log of face snaps. No VLM. Empty scenery is not a row."""
        if not faces:
            return
        if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get(
            "RAIL_FACEID_LOG"
        ):
            return
        now = time.time()
        root = self._faceidog_root()
        day = time.strftime("%Y%m%d", time.localtime(now))
        last = getattr(self, "_faceidog_last", {}) or {}
        wrote = False
        for rec in faces:
            name = str(rec.get("name") or "").strip().lower()
            if not name:
                continue
            try:
                sim = float(rec.get("sim") or 0)
            except (TypeError, ValueError):
                sim = 0.0
            prev = last.get(name)
            if prev and (now - float(prev[0]) < 2.0) and abs(sim - float(prev[1])) < 0.03:
                continue
            crop_name = None
            if crop_jpeg and name == (locked or "").strip().lower():
                faces_dir = root / "faces"
                try:
                    faces_dir.mkdir(parents=True, exist_ok=True)
                    crop_name = f"{int(now)}_{name}.jpg"
                    (faces_dir / crop_name).write_bytes(crop_jpeg)
                except OSError:
                    crop_name = None
            row = {
                "ts": now,
                "ts_local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "source": source,
                "name": name,
                "sim": round(sim, 3),
                "bbox": rec.get("bbox"),
                "n_faces": n_faces,
                "f2f": bool(self._f2f),
                "crop": (f"faces/{crop_name}" if crop_name else None),
            }
            try:
                root.mkdir(parents=True, exist_ok=True)
                with (root / f"{day}.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, default=str) + "\n")
                last[name] = (now, sim)
                wrote = True
            except OSError:
                pass
        if wrote:
            self._faceidog_last = last

    def _stamp_speaker_face_if_f2f(
        self,
        *,
        source: str,
        faces: list,
        locked: str | None,
    ) -> None:
        """F2F FaceID look inits ev_speaker on the Oracle ring. Not a count."""
        if not locked or str(source or "") not in ("f2f", "beam_look"):
            return
        rec = next(
            (
                f
                for f in faces
                if str(f.get("name") or "").strip().lower()
                == str(locked).strip().lower()
            ),
            None,
        ) or {}
        try:
            sim = float(rec.get("sim") or 0)
        except (TypeError, ValueError):
            sim = 0.0
        if sim < 0.60:
            return
        try:
            from .oracle_event import stamp_speaker_face

            stamp_speaker_face(
                name=str(locked).strip().lower(),
                sim=sim,
                sex=rec.get("sex"),
                source=str(source or "faceid"),
                bbox=rec.get("bbox"),
            )
        except Exception:
            pass

    def _speaker_scratch(self, pid: str | None) -> bool:
        p = str(pid or "").strip().lower()
        return bool(p) and p[0] == "s" and p[1:].isdigit()

    def _speaker_reset_latch(self) -> None:
        self._speaker_latch = {
            "person_id": None,
            "beam": 0.0,
            "camera": 0.0,
            "embeds": 0.0,
            "voice": 0.0,
            "face": 0.0,
            "sex": 0.0,
            "stack": 0.0,
        }

    def _speaker_show(self, key: str, live: float | None, *, green: bool) -> tuple[float, bool]:
        """Serve the live numeral. Gold below thresh, green at thresh. Latch last."""
        latch = getattr(self, "_speaker_latch", None)
        if not isinstance(latch, dict):
            self._speaker_reset_latch()
            latch = self._speaker_latch
        if live is not None:
            try:
                val = float(live)
            except (TypeError, ValueError):
                val = float(latch.get(key) or 0.0)
            if green or abs(val) > 1e-9:
                latch[key] = val
                return val, bool(green)
            try:
                return float(latch.get(key) or 0.0), False
            except (TypeError, ValueError):
                return 0.0, False
        try:
            shown_f = float(latch.get(key) or 0.0)
        except (TypeError, ValueError):
            shown_f = 0.0
        return shown_f, False

    def _speaker_pub(self) -> dict:
        """Speaker row: User or None. Values stay up; gold at 0, green at thresh.

        Name stays the User through empty FOV during a look. A non-User voice
        signal reverts the name to None (Guest). Occupancy sticky is not this.
        """
        from .seat_slots import user_seat
        from .seat_shot import hour_from_az
        from .speaker_stack import stack_speaker

        now = time.time()
        fresh_s = 2.5
        snd = self._sound_pub() or {}
        hour = snd.get("beam_hour")
        if hour is None:
            hour = snd.get("hour") or snd.get("station")
        if hour is None:
            hour = hour_from_az(snd.get("az_deg"))
        lv = getattr(self, "_last_spoke_voice", None) or getattr(
            self, "_last_user_voice", {}
        ) or {}
        voice_sim = None
        voice_pid = None
        voice_age = None
        try:
            vts = float(lv.get("ts") or 0.0)
        except (TypeError, ValueError):
            vts = 0.0
        if vts > 0:
            voice_age = now - vts
        if voice_age is not None and voice_age <= fresh_s:
            try:
                voice_sim = float(lv.get("sim")) if lv.get("sim") is not None else None
            except (TypeError, ValueError):
                voice_sim = None
            voice_pid = str(lv.get("person_id") or "").strip().lower() or None
        uid = self._user_id()
        hud = getattr(self, "_faceid_hud", {}) or {}
        looking = bool(getattr(self, "_f2f", False))
        try:
            hud_ts = float(hud.get("ts") or 0.0)
        except (TypeError, ValueError):
            hud_ts = 0.0
        face_age = (now - hud_ts) if hud_ts > 0 else None
        faces_live = bool(
            looking
            or (face_age is not None and face_age <= fresh_s)
        )
        live_face = None
        sex_live = None
        if faces_live:
            for rec in hud.get("faces") or []:
                if str(rec.get("name") or "").strip().lower() != uid:
                    continue
                try:
                    live_face = float(rec.get("sim"))
                except (TypeError, ValueError):
                    live_face = None
                sx = rec.get("sex")
                if sx in ("M", "F"):
                    sex_live = sx
                break
        # Do not read _face_auth_hold. Sticky occupancy is not Speaker.
        face_sim = live_face
        stamps = getattr(self, "_person_stamps", None)
        if stamps is None:
            try:
                from .enrollments import load_person_stamps

                stamps = load_person_stamps()
                self._person_stamps = stamps
            except Exception:
                stamps = {}
        sex_user = None
        g = (stamps.get(uid) or {}).get("gender") if isinstance(stamps, dict) else None
        if g in ("M", "F"):
            sex_user = g
        try:
            seat = int(user_seat())
        except Exception:
            seat = 6
        # Camera cell: 0 gold until User face lock. No 0.5 looking flicker.
        cam = 0.0
        if live_face is not None and live_face >= 0.60:
            cam = 1.0
        from .speaker_stack import voice_bar
        voice_thr = voice_bar(uid)          # the ear's own bar for this person (the User 2026-10-03)
        live = stack_speaker(
            beam_hour=hour,
            az_deg=snd.get("az_deg"),
            user_seat=seat,
            voice_sim=voice_sim,
            face_sim=face_sim,
            sex_live=sex_live,
            sex_user=sex_user,
            camera=cam,
            voice_thr=voice_thr,
        )
        lock = str(hud.get("locked") or "").strip().lower() or None
        n_faces = 0
        try:
            n_faces = int(hud.get("n_faces") or len(hud.get("faces") or []) or 0)
        except (TypeError, ValueError):
            n_faces = len(hud.get("faces") or [])
        other_voice = bool(voice_pid) and voice_pid != uid
        user_voice = bool(voice_pid) and voice_pid == uid
        # Camera 100 is the User's face this look (live_face ≥ 0.60). Do not wait
        # for HUD `locked` — that lagged, so cam 100 / voice 49 stayed Guest.
        user_face = bool(
            looking
            and (
                (lock == uid and n_faces > 0)
                or (live_face is not None and live_face >= 0.60)
            )
        )
        other_face = bool(n_faces > 0 and lock and lock != uid)
        latch = getattr(self, "_speaker_latch", None)
        if not isinstance(latch, dict):
            self._speaker_reset_latch()
            latch = self._speaker_latch
        pid = None
        named_by = None
        if other_voice or other_face:
            latch["person_id"] = None
            named_by = "voice" if other_voice else "face"
        elif user_voice:
            pid = uid
            named_by = "voice"
            latch["person_id"] = uid
        elif user_face:
            pid = uid
            named_by = "face"
            latch["person_id"] = uid
        elif looking:
            held = str(latch.get("person_id") or "").strip().lower() or None
            if held == uid:
                pid = uid
                named_by = "hold"
            else:
                ts = getattr(self, "_conv_auth_lock_ts", None)
                age = None
                try:
                    if ts is not None:
                        age = now - float(ts)
                except (TypeError, ValueError):
                    age = None
                if age is not None and 0 <= age <= self._conv_auth_hold_s():
                    pid = uid
                    named_by = "hold"
                    latch["person_id"] = uid
        else:
            latch["person_id"] = None
            pid = None
            named_by = None
        out = dict(live)
        live_on = bool(looking or pid)
        # Gold latch is for a live look, not leftover AUTH. Empty + not looking
        # must drop the 100s — wall pause / door rest is Speaker None.
        if not live_on:
            self._speaker_reset_latch()
        beam_g = bool(live.get("beam_hour")) or float(live.get("beam") or 0) >= 1.0
        cam_g = live_on and float(live.get("camera") or 0) >= 1.0
        emb_g = live_on and bool(live.get("voice_ok"))
        face_g = live_on and bool(live.get("face_ok"))
        sex_g = live_on and live.get("sex_ok") is True
        stack_g = live_on and float(live.get("stack") or 0) >= 1.0
        out["beam"], out["beam_green"] = self._speaker_show(
            "beam", live.get("beam"), green=beam_g
        )
        out["camera"], out["camera_green"] = self._speaker_show(
            "camera", live.get("camera"), green=cam_g
        )
        out["embeds"], out["embeds_green"] = self._speaker_show(
            "embeds", live.get("embeds"), green=emb_g
        )
        out["voice"], out["voice_green"] = self._speaker_show(
            "voice", live.get("voice"), green=emb_g
        )
        out["face"], out["face_green"] = self._speaker_show(
            "face", live.get("face"), green=face_g
        )
        out["sex"], out["sex_green"] = self._speaker_show(
            "sex", live.get("sex") if live.get("sex") is not None else 0.0,
            green=sex_g,
        )
        out["stack"], out["stack_green"] = self._speaker_show(
            "stack", live.get("stack"), green=stack_g
        )
        if pid:
            out["person_id"] = pid
        else:
            out.pop("person_id", None)
        out["n_faces"] = n_faces
        out["named_by"] = named_by
        out["name_gold"] = bool(pid) and not bool(out.get("voice_green"))
        # User pops only for the User at thresh/green. Else Guest rails.
        ready = bool(pid == uid) and (
            bool(out.get("voice_green"))
            or bool(out.get("camera_green"))
            or bool(out.get("face_green"))
        )
        out["user"] = ready
        out["guest"] = not ready
        out["age_s"] = None if face_age is None else round(face_age, 2)
        out["voice_age_s"] = None if voice_age is None else round(voice_age, 2)
        return out

    def _fixed_cam(self) -> bool:
        """A camera that does not move, with its rule on (crissa_presence.fixed_cam, 2026-10-11): its one view is the
        User's seat, so the oracle's look there is a live face and one face that is the User's is the User alone."""
        try:
            from . import fixed_cam

            g = self.last_grabber if isinstance(self.last_grabber, dict) else None
            return bool(fixed_cam.sole_on() and fixed_cam.is_fixed(g))
        except Exception:
            return False

    def _secure_offer(self) -> dict | None:
        """May Secure be offered on this camera (crissa_presence.fixed_cam.secure_offer, round 17)? None where the rule
        does not apply (the house, RAIL_FIXED_CAM_SECURE=0)."""
        try:
            from . import fixed_cam

            g = self.last_grabber if isinstance(self.last_grabber, dict) else None
            return fixed_cam.secure_offer(g)
        except Exception:
            return None

    def _secure_not_offered(self) -> dict | None:
        """The offer when Secure is NOT offered here (a fixed camera, no camera), else None."""
        off = self._secure_offer()
        return off if (isinstance(off, dict) and off.get("ok") is False) else None

    def _fixed_look_hud(self, h: dict) -> dict:
        """ON A FIXED CAMERA THE ORACLE'S LOOK IS THE LIVE FACE (2026-10-11, .101 round 9: the face block carried no
        User face, so the ear-bridge's "the face decides" never could). The oracle looks every second while a face is
        in view and hands it to /v1/see (fixed_cam.note_look); when that look is newer than the visor's reading it is
        the face block: the face named as the User only when the matcher named him, locked to him only when he is
        the one face at the live-face bar. Only the published block changes: no AUTH, no crops, no conversation lock
        (note_faceid_hud's side effects stay the visor's and the door's)."""
        if not self._fixed_cam():
            return h
        from . import fixed_cam

        lk = fixed_cam.look()
        try:
            newer = bool(lk) and float(lk.get("ts") or 0) > float(h.get("ts") or 0)
        except (TypeError, ValueError):
            newer = False
        if not newer:
            return h
        uid = self._user_id()
        sim = lk.get("sim")
        name = str(lk.get("name") or "")
        is_user = bool(uid and name == uid)
        n = int(lk.get("n_faces") or 1)
        locked = uid if (is_user and n == 1 and sim is not None and float(sim) >= self._conv_auth_min_sim()) else None
        out = dict(h)
        out.update(
            ok=True,
            ts=float(lk["ts"]),
            source="fixed_look",
            n_faces=n,
            faces=[{"name": name if is_user else "", "sim": round(float(sim), 4) if sim is not None else None}],
            names=({name: round(float(sim), 4)} if (is_user and sim is not None) else {}),
            locked=locked,
            fixed=True,
        )
        return out

    def _fixed_sole(self) -> dict | None:
        """The User alone at a fixed camera's seat (fixed_cam.sole), or None: the camera's rule on, occupancy off (its
        own roster then), the lights on, the room not busy, ONE face, the User's, at the live-face bar, fresh."""
        if not self._fixed_cam() or occupancy_on():
            return None
        try:
            lights_out, _blk = self._lights_from_grabber()
        except Exception:
            lights_out = False
        if lights_out or getattr(self, "_room_busy", False):
            return None
        from . import fixed_cam

        return fixed_cam.sole(self._user_id(), min_sim=self._conv_auth_min_sim(), fixed=True, on=True)

    def faceid_hud(self) -> dict:
        h = dict(getattr(self, "_faceid_hud", None) or {})
        h = self._fixed_look_hud(h)
        looking = bool(getattr(self, "_f2f", False))
        busy = bool(getattr(self, "_room_busy", False))
        if not h:
            idle = {
                "ok": True,
                "faces": [],
                "n_faces": 0,
                "locked": None,
                "source": "idle",
                "cam_owner": self.cam_holder(),
                "f2f": looking,
                "pending_look": bool(
                    looking and not getattr(self, "_f2f_scan_ready", False)
                ),
                "room_busy": busy,
                "has_crop": False,
            }
            try:
                idle["speaker"] = self._speaker_pub()
            except Exception:
                pass
            return self._attach_conv_auth_hold(idle)
        ts = float(h.get("ts") or 0)
        h["age_s"] = round(time.time() - ts, 2) if ts else None
        h["f2f"] = looking
        h["pending_look"] = bool(
            looking and not getattr(self, "_f2f_scan_ready", False)
        )
        # A fixed camera has no look to wait for (round 13, .101 2026-10-12): the beam look that sets
        # _f2f_scan_ready never runs there, so a session look stayed "pending" for the whole conversation and the
        # ear never let the face decide. A block the oracle's fixed look wrote is fresh by its own age.
        if h.get("fixed") and os.environ.get("CRISSA_FIXED_LOOK_NOT_PENDING", "1").strip().lower() not in (
                "0", "false", "no", "off"):
            h["pending_look"] = False
        h["room_busy"] = busy
        h["cam_owner"] = self.cam_holder()
        h["has_crop"] = bool(getattr(self, "_faceid_crop", None))
        h["has_frame"] = bool(getattr(self, "_faceid_frame", None))
        try:
            h["speaker"] = self._speaker_pub()
        except Exception:
            pass
        return self._attach_conv_auth_hold(h)

    def door_glance(self) -> dict:
        """One door-FOV FaceID still. AUTH if the User is in it. Not a count.

        Walk-by size: one JPEG + crop. Speech look owns the camera — skip
        unless Secure, which must watch the door. Inspector lease is not stolen.
        """
        now = time.time()
        if now - float(getattr(self, "_door_glance_ts", 0.0) or 0.0) < 2.0:
            return {"ok": True, "skipped": "gap"}
        sage = self._visit_is_secure()
        if (not sage) and (
            getattr(self, "_f2f", False) or getattr(self, "_f2f_looking", False)
        ):
            return {"ok": True, "skipped": "speech_look"}
        if sage and (getattr(self, "_f2f", False) or getattr(self, "_f2f_looking", False)):
            try:
                self._visit_lamp_hold_door()
            except Exception:
                pass
        from . import camera_token as ct

        holder = self.cam_holder()
        if holder and holder != "secure_glance":
            if not ct.outranks("secure_glance", holder):
                # the User's joystick has the camera: the watch waits
                return {"ok": True, "skipped": f"held:{holder}"}
            if sage:
                # a look or the FF chase holds it: Secure's door watch outranks
                # them (Law 2) — take the token so they yield, camera to the door
                self.cam_take("secure_glance", 6.0, why="glance")
                try:
                    self._visit_lamp_hold_door()
                except Exception:
                    pass
        try:
            from .beam_look import YIELD_OWNERS, camera_health, lease_owner

            owner = lease_owner(camera_health())
            if owner and owner not in YIELD_OWNERS:
                return {"ok": True, "skipped": "inspector"}
        except Exception:
            pass
        g = getattr(self, "last_grabber", None) or {}
        if isinstance(g, dict) and g.get("at_door") is False:
            if sage:
                try:
                    self._visit_lamp_hold_door()
                except Exception:
                    pass
            else:
                return {"ok": True, "skipped": "off_door"}
        self._door_glance_ts = now
        jpeg = None
        try:
            from . import room_sweep as _rs

            jpeg = _rs._snapshot()
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}:{e}"[:120]}
        if not jpeg:
            return {"ok": False, "error": "no_snap"}
        seen = self._identify_all_in_jpegs([jpeg], source="door_glance", tripwire=True)
        uid = self._user_id()
        names = seen.get("names") or {}
        # User at the door is a visit log (faceid hud), not occupancy sticky.
        self._record_door_visitors(seen, uid=uid)
        # print on a change or a face — not "faces=0 names=[]" every 5 s
        # (the User 2026-09-07: "your journal will be nothing but that")
        sig = (int(seen.get("n_faces") or 0), tuple(sorted(names)))
        bg_note = ""
        if "n_faces_raw" in seen:
            bg_note = (
                f" raw={seen.get('n_faces_raw')} bg={seen.get('n_faces_bg')}"
                f" ({seen.get('door_bg_mode')})"
            )
        if (
            sig[0] or sig[1] or seen.get("n_faces_raw")
            or sig != getattr(self, "_door_glance_last", None)
        ):
            print(
                f"[door-glance] faces={seen.get('n_faces')} names={list(names)}{bg_note}",
                flush=True,
            )
        self._door_glance_last = sig
        try:
            self._visit_lamp_on_door_faces(seen)
        except Exception:
            pass
        if self.cam_holder() == "secure_glance":
            self.cam_give_back("secure_glance", home=False)
        return {
            "ok": True,
            "n_faces": seen.get("n_faces"),
            "n_faces_raw": seen.get("n_faces_raw"),
            "n_faces_bg": seen.get("n_faces_bg"),
            "names": names,
            "source": "door_glance",
        }

    def _start_motion_glance(self) -> None:
        """Camera motion at the door → FaceID glance. Not occupancy mill."""
        if _ports.camera_off():                  # it polls the camera's trigger ring (2026-10-10)
            _ports.say_once("the door motion glance does not start")
            return
        if getattr(self, "_motion_glance_thread", None) and self._motion_glance_thread.is_alive():
            return
        t = threading.Thread(
            target=self._motion_glance_loop,
            name="door-motion-glance",
            daemon=True,
        )
        self._motion_glance_thread = t
        t.start()

    def _motion_glance_loop(self) -> None:
        last = 0
        primed = False
        while not self._stop.wait(1.0):
            try:
                from .door_fanout import _fetch_triggers

                trigs = _fetch_triggers()
                seqs = []
                for row in trigs or []:
                    try:
                        seqs.append(int((row or {}).get("seq") or 0))
                    except (TypeError, ValueError):
                        continue
                mx = max(seqs) if seqs else 0
                if not primed:
                    last = mx
                    primed = True
                    continue
                if mx > last:
                    last = mx
                    self.door_glance()
            except Exception:
                pass

    def _record_door_visitors(self, seen: dict, *, uid: str) -> None:
        """Door FOV stills → X# / named guests. User is never a visitor.

        A face is skipped as the User only when it SCORES like him (>= the 0.40 profile
        floor), not because he is its closest enrolled person: he has ~139 vectors to a
        guest's 1-5, so he was the closest match for most strangers and they were never
        logged (the User 2026-10-09). Skips and logs are counted (`_door_skips_pub`).
        Kill RAIL_DOOR_SKIP_CLOSEST=1 (closest-is-the-User skips again)."""
        uid = (uid or "").strip().lower()
        named = seen.get("names") or {}
        try:
            from .stranger_x import record_event
        except Exception:
            return
        skip_min = self._conv_auth_profile_min_sim()
        closest = os.environ.get("RAIL_DOOR_SKIP_CLOSEST", "0") == "1"
        for rec in seen.get("unmatched") or []:
            if not isinstance(rec, dict):
                continue
            nm = str(rec.get("name") or "").strip().lower()
            try:
                sim = float(rec.get("sim"))
            except (TypeError, ValueError):
                sim = None
            if nm == uid and (closest or (sim is not None and sim >= skip_min)):
                self._note_door_visitor(skipped=True, sim=sim)
                continue
            try:
                record_event(
                    kind="enter",
                    jpeg=rec.get("jpeg"),
                    crop=rec.get("crop"),
                    emb=rec.get("emb"),
                    bbox=rec.get("bbox"),
                    sex=rec.get("sex"),
                    source="door_glance",
                    named_sim=rec.get("sim"),
                )
                self._note_door_visitor(skipped=False)
            except Exception:
                pass

    def _grant_user_from_look(
        self, seen: dict | None, *, source: str = "beam_look"
    ) -> bool:
        """Mint AUTH from a FaceFinder look that just saw the User.

        Guest = FF-for-AUTH (the User 2026-09-09). The visor heartbeat is that
        look's FOV, not a door mill. Sage skips — Secure paused FF tracking.
        Republishes so the wall User chip moves during the look, not after.
        """
        if self._visit_is_secure():
            return False
        if not (
            getattr(self, "_f2f", False) or getattr(self, "_f2f_looking", False)
        ):
            return False
        uid = self._user_id()
        names = (seen or {}).get("names") or {}
        if uid not in names:
            return False
        try:
            sim = float(names[uid]) if names.get(uid) is not None else None
        except (TypeError, ValueError):
            sim = None
        self.grant_user_face_auth(sim=sim, source=source, greet=False)
        return True

    def faceid_scan(self) -> dict:
        """ID the current FOV. No aim, no occupancy change.

        Do not treat Inspector / who_is_here FOV as conversation AUTH.
        While a session look owns the camera, a User lock here is the
        FaceFinder grant (wall User during FF, not after the look homes).
        """
        now = time.time()
        if now - float(self._faceid_scan_ts or 0) < 0.8:
            return self.faceid_hud()
        try:
            from .beam_look import camera_health, lease_owner

            owner = lease_owner(camera_health())
        except Exception:
            owner = None
        if owner and owner not in ("", "beam_look", "f2f", "door_mill"):
            h = self.faceid_hud()
            h["skipped"] = "inspector"
            return h
        self._faceid_scan_ts = now
        jpeg = None
        try:
            from . import room_sweep as _rs

            jpeg = _rs._snapshot()
        except Exception as e:
            h = self.faceid_hud()
            h["scan_error"] = f"{type(e).__name__}:{e}"[:120]
            return h
        if jpeg:
            seen = self._identify_all_in_jpegs([jpeg], source="visor")
            self._grant_user_from_look(seen, source="beam_look")
        return self.faceid_hud()

    def _faceid_heartbeat(self) -> None:
        """Server-side visor scan so AUTH is not a Brave tab.

        Only while a session look owns the camera. Idle door scenery must
        not grant conversation AUTH. Inspector FOV is skipped in faceid_scan.
        """
        if not getattr(self, "_f2f", False):
            return
        try:
            from . import fixed_cam

            if fixed_cam.faces_idle():                  # Security Off: no face is named for ID (round 17 follow-ups)
                return
        except Exception:
            pass
        now = time.time()
        last = float(getattr(self, "_faceid_hb_ts", 0.0) or 0.0)
        if now - last < 1.2:
            return
        self._faceid_hb_ts = now
        self.faceid_scan()

    def _door_work_pending(self) -> bool:
        try:
            from .door_fanout import pending_work

            return bool(pending_work())
        except Exception:
            return False

    def note_blind_gap(
        self,
        *,
        from_ts: float | None = None,
        to_ts: float | None = None,
        holder: str | None = None,
        why: str | None = None,
        seq: int | None = None,
    ) -> dict:
        """Flag the door was uncovered. No occupancy change. Door-buffer face only."""
        now = time.time()
        self._busy_unknown = True
        out: dict[str, Any] = {
            "ok": True,
            "count_changed": False,
            "holder": holder,
            "why": why,
            "seq": seq,
            "from_ts": from_ts,
            "to_ts": to_ts,
        }
        try:
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=getattr(cur, "camera_open", True),
                camera_blind=True,
                last_event={
                    "kind": "blind_gap",
                    "holder": holder,
                    "why": why,
                    "from_ts": from_ts,
                    "to_ts": to_ts,
                    "seq": seq,
                    "ts": now,
                    "note": "door uncovered; count unchanged",
                },
                count_trust="unknown",
                camera_mode=getattr(cur, "camera_mode", None) or "holding",
                door_names=list(getattr(cur, "door_names", None) or []),
            )
            self._set(st)
            out["count_trust"] = "unknown"
            out["headcount"] = int(st.headcount)
        except Exception as e:
            out["publish_error"] = f"{type(e).__name__}:{e}"[:160]
        # Recheck is FaceID on the door buffer. Camera already home. No slew.
        try:
            out["door_face"] = self._door_buffer_face_recheck()
        except Exception as e:
            out["door_face"] = {"ok": False, "error": f"{type(e).__name__}:{e}"[:120]}
        return out

    def _door_buffer_face_recheck(self) -> dict:
        """ID faces in the current door FOV. Does not aim. Does not +1."""
        try:
            with urllib.request.urlopen(
                _ports.camera("/v1/health"), timeout=0.4
            ) as r:
                h = json.loads(r.read().decode())
            if h.get("at_door") is False:
                return {"ok": True, "skipped": "not_at_door"}
        except Exception:
            return {"ok": False, "skipped": "cam"}
        from . import room_sweep as _rs

        jpegs: list[bytes] = []
        for _ in range(2):
            try:
                jpegs.append(_rs._snapshot())
            except Exception:
                break
        if not jpegs:
            return {"ok": False, "error": "no_snap"}
        seen = self._identify_all_in_jpegs(jpegs, source="blind_gap_door")
        uid = self._user_id()
        names = seen.get("names") or {}
        if uid in names:
            self._note_face_auth(
                uid, sim=names.get(uid), source="blind_gap_door"
            )
        return {"ok": True, "names": names, "n_faces": seen.get("n_faces")}

    def _busy_auth_scan(self) -> dict:
        """One room AUTH scan for this Busy storm. Linger does not call this.

        Two Busy-on callers (linger, queue_flood) share the flag. The scan
        fires once when Busy drops if queue_flood armed it. Face opens AUTH;
        voice keeps it (no timer).
        """
        if getattr(self, "_busy_auth_scanned", False):
            return {
                "ok": True,
                "via": "already_scanned",
                "auth": bool(getattr(self.get(), "user_auth", False)),
            }
        if self._door_work_pending():
            return {"ok": True, "via": "door_pending", "auth": False}
        uid = self._user_id()
        try:
            with urllib.request.urlopen(
                _ports.camera("/v1/health"), timeout=0.4
            ) as r:
                h = json.loads(r.read().decode())
            lease = h.get("lease") or h.get("lease_owner")
            if lease:
                return {"ok": True, "via": "skipped_lease", "auth": False}
        except Exception:
            return {"ok": True, "via": "skipped_cam", "auth": False}
        self._busy_auth_scanned = True
        self._busy_auth_scan_armed = False
        hunt = self.ensure_user_auth(
            person_id=uid,
            reason="busy_off_auth",
            force_facefind=True,
            mode="full",
        )
        found = bool(hunt.get("auth") or hunt.get("found"))
        if found:
            self._auth_blocked_until_recheck = False
            if uid not in (self._face_auth_hold or {}):
                self._note_face_auth(
                    uid,
                    sim=(hunt.get("hunt") or {}).get("best_sim"),
                    source="busy_auth_scan",
                )
            if not self._user_session:
                self._user_session = {
                    "opened_ts": time.time(),
                    "last_strong_voice_ts": None,
                    "last_strong_voice_sim": None,
                    "opened_via": "busy_auth_scan",
                }
        return {
            "ok": True,
            "via": "busy_auth_scan",
            "auth": found,
            "hunt": {
                k: hunt.get(k)
                for k in ("auth", "found", "via", "reason", "wall_sec")
            },
        }

    def _busy_off_recount(self) -> dict:
        """Linger Busy-off: desk snap only. Does not run the room AUTH scan.

        Skip if Inspector holds the camera. Finding the User is the recheck
        that may turn AUTH back on. Missing them leaves AUTH off.
        """
        uid = self._user_id()
        last = (getattr(self, "_oracle_visits", {}) or {}).get(uid) or {}
        if str(last.get("event") or "").lower() == "exit" and last.get("exit_ts"):
            return {"ok": True, "via": "clip_exit", "auth": False}
        if int(self.inventory.headcount() or 0) <= 0:
            return {"ok": True, "via": "empty_room", "auth": False}
        # Count is already the storm clip (fan_out applied before Busy-off).
        # Desk snap is AUTH recheck only, not a second count. Full sweep is
        # Inspector — skip unless the clip was unresolved.
        clip_ev = str(last.get("event") or "").lower()
        clip_reason = str(last.get("reason") or last.get("pending_confirm") or "")
        unresolved = clip_ev in ("unresolved", "") or "unresolved" in str(clip_reason)
        via = "unresolved_desk" if unresolved else "storm_clip_desk"
        if self._door_work_pending():
            return {"ok": True, "via": "door_pending", "auth": False}
        try:
            with urllib.request.urlopen(
                _ports.camera("/v1/health"), timeout=0.4
            ) as r:
                h = json.loads(r.read().decode())
            if h.get("lease"):
                return {"ok": True, "via": "skipped_lease", "auth": False}
        except Exception:
            return {"ok": True, "via": "skipped_cam", "auth": False}
        from .seat_shot import shot_at
        from .user_exit_confirm import user_face_on_bgr, user_seat_pose

        pose = user_seat_pose()
        shot = shot_at(
            int(pose["pan"]),
            int(pose["tilt"]),
            owner="busy-off-recount",
        )
        if shot.get("error") == "camera_leased":
            return {"ok": True, "via": "skipped_lease", "auth": False}
        seat = {"found": False}
        if shot.get("frames"):
            import cv2
            import numpy as np

            raw = shot["frames"][0]
            cur = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            seat = user_face_on_bgr(cur, uid)
        if seat.get("found"):
            self._note_face_auth(
                uid, sim=seat.get("sim"), source="busy_off_recheck"
            )
            return {
                "ok": True,
                "via": via,
                "found": True,
                "sim": seat.get("sim"),
                "auth": True,
            }
        return {
            "ok": True,
            "via": via,
            "found": False,
            "auth": False,
            "seat": {k: seat.get(k) for k in ("n_faces", "error", "name")},
        }

    def confirm_user_exit(
        self,
        pid: str,
        *,
        user_exit: bool | None = None,
        user_exit_set: bool = False,
    ) -> dict:
        """Seat snap. Only then may User AUTH drop. Real lease; fail closed.

        Clip vote rides along: null + empty seat keeps AUTH. False + AUTH
        already on skips the slew (door stays watched).
        """
        import cv2
        import numpy as np

        from .seat_shot import shot_at
        from .user_exit_confirm import (
            decide_user_exit,
            sudo_user_id,
            user_face_on_bgr,
            user_seat_pose,
        )

        uid = sudo_user_id()
        pid = (pid or uid).strip().lower()
        if pid != uid:
            return {"confirmed": False, "reason": "not_sudo_user"}
        held = uid in getattr(self, "_face_auth_hold", {})
        if user_exit_set and user_exit is False and held:
            return decide_user_exit(
                seat={"found": False, "skipped": "clip_said_stay"},
                user_exit=False,
                user_exit_set=True,
            )
        now = time.time()
        last_pan = float(getattr(self, "_ensure_auth_pan_ts", 0.0) or 0.0)
        try:
            pan_gap = float(os.environ.get("RAIL_ENSURE_AUTH_PAN_GAP_SEC", "30"))
        except (TypeError, ValueError):
            pan_gap = 30.0
        if now - last_pan < pan_gap:
            return decide_user_exit(
                seat={"found": False, "error": "pan_cooldown"},
                user_exit=user_exit,
                user_exit_set=user_exit_set,
            )
        pose = user_seat_pose()
        self._ensure_auth_pan_ts = now
        shot = shot_at(
            int(pose["pan"]),
            int(pose["tilt"]),
            owner="user-exit-confirm",
        )
        seat_info: dict = {"found": False, "error": "no_snap"}
        if shot.get("error") == "camera_leased":
            seat_info = {"found": False, "error": "camera_leased"}
        elif shot.get("frames"):
            raw = shot["frames"][0]
            cur = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            seat_info = user_face_on_bgr(cur, uid)
            seat_info["aim_ok"] = bool(shot.get("aim_ok") or shot.get("ok"))
            seat_info["pan"] = int(pose["pan"])
        elif shot.get("error"):
            seat_info = {"found": False, "error": str(shot.get("error"))[:160]}
        return decide_user_exit(
            seat=seat_info,
            user_exit=user_exit,
            user_exit_set=user_exit_set,
        )

    def assert_leave(
        self,
        names: list[str],
        *,
        source: str = "operator",
        clear_unnamed: bool = False,
    ) -> RoomPresence:
        """Voice/operator: person left. Clears sticky. Prefer over immortal assert.

        U1/U2/crowd_noise are display labels for inventory.unnamed — pass them
        (or clear_unnamed=True / names containing \"*\") to wipe unknown slots.
        """
        clean = [n.strip().lower() for n in names if n and str(n).strip()]
        wipe_u = clear_unnamed or any(
            n in ("*", "all", "empty", "u1", "u2", "u3", "crowd_noise", "unknown")
            or n.startswith("u") and n[1:].isdigit()
            for n in clean
        )
        # drop pseudo-names that are not real inventory keys
        named = [
            n
            for n in clean
            if n
            and n not in ("*", "all", "empty", "crowd_noise", "unknown")
            and not (n.startswith("u") and n[1:].isdigit())
            and not n.startswith("unknown")
        ]
        evs = self.inventory.force_leave(named, source=source) if named else []
        # `unnamed > 0` alone stranded the keys: assert_present(replace=True,
        # unnamed=0) zeroes the COUNT without dropping keys, and restore does
        # `unnamed = len(unnamed_keys)`, so four orphans came back as headcount
        # 5 on the next restart (the User alone, 2026-08-16). Cleanup has to be
        # reachable whenever either half is non-empty.
        if wipe_u and (self.inventory.unnamed > 0 or self.inventory.unnamed_keys):
            # §1.4: __uN slots leave ONLY via _drop_unnamed_key — the raw
            # unnamed=0 shortcut orphaned unnamed_keys/meta (invariant_breach
            # 2026-07-28 13:18) and left no audit row.
            for uk in list(self.inventory.unnamed_keys):
                evs.append(self.inventory._drop_unnamed_key(
                    uk, now=time.time(), source=source,
                    reason="operator_wipe"))
            if self.inventory.unnamed > 0:
                # count-without-keys orphans (pre-choke rows): zero + log
                n_u = self.inventory.unnamed
                self.inventory.unnamed = 0
                evs.append(
                    {
                        "kind": "leave",
                        "name": None,
                        "unnamed_delta": -n_u,
                        "ts": time.time(),
                        "source": source,
                        "reason": "operator_wipe_orphan_count",
                    }
                )
        now = time.time()
        if named:
            self._note_left(named, now=now)
        st = self.get()
        st = self._presence_from_inventory(
            frame_age=st.frame_age_sec if st.camera_open else 999.0,
            camera_open=st.camera_open,
            camera_blind=st.camera_blind,
            last_event={
                "kind": "leave",
                "source": source,
                "names": named,
                "clear_unnamed": wipe_u,
                "events": evs,
                "headcount": self.inventory.headcount(),
                "ts": now,
            },
            count_trust="fact" if self._mode != "away" else "stale",
            camera_mode=self._mode if self._mode != "reconciling" else "holding",
            door_names=list(st.door_names or []),
        )
        st.count_certain = st.unidentified == 0 and all(p.name for p in st.people)
        self._set(st)
        self.cycles += 1
        self.last_cycle_at = now
        try:
            self._save_inventory_disk(force=True)
        except Exception:
            pass
        self._sync_oracle_hand(reason=f"leave:{source}")
        return st

    def adopt_rest_empty(self) -> dict[str, Any]:
        """Current rest JPEG is empty. Faces in it are not bodies. Wipes U*."""
        from .door_background import adopt_empty_bgr

        g = self.last_grabber if isinstance(getattr(self, "last_grabber", None), dict) else {}
        if g.get("at_door") is False:
            return {"ok": False, "reason": "not_at_door", "pan": g.get("pan_actual")}
        fr = self.camera.grab()
        if fr is None or getattr(fr, "bgr", None) is None:
            return {"ok": False, "reason": "no_snapshot"}
        adopted = adopt_empty_bgr(fr.bgr)
        if not adopted.get("ok"):
            return adopted
        st = self.assert_leave(["U1"], source="operator", clear_unnamed=True)
        return {
            "ok": True,
            "adopted": adopted,
            "headcount": int(st.headcount),
            "people": [p.name for p in (st.people or [])],
        }

    def _sync_oracle_hand(self, *, reason: str = "inventory") -> None:
        """Keep clothing cards == inventory named people. Never touch count/FF."""
        try:
            from .oracle_hand_sync import sync_hand_from_inventory_obj

            sync_hand_from_inventory_obj(self.inventory, reason=reason)
        except Exception:
            pass

    # ---- writer-mode governance helpers (presence-perfect §3) ----
    @staticmethod
    def _vl_src(source: str) -> str:
        return (str(source or "").strip().lower() or "unknown")

    def _vl_mode(self, source: str) -> tuple[str, str]:
        """Resolve (mode, mode_source) for a writer: runtime override >
        env RAIL_VL_MODE_<SOURCE> > RAIL_VL_MODE_DEFAULT > full."""
        src = self._vl_src(source)
        ov = self._vl_mode_override.get(src)
        if ov in VL_MODES:
            return ov, "override"
        env = (os.environ.get(f"RAIL_VL_MODE_{src.upper()}") or "").strip().lower()
        if env in VL_MODES:
            return env, "env"
        d = (os.environ.get("RAIL_VL_MODE_DEFAULT") or "").strip().lower()
        if d in VL_MODES:
            return d, "default"
        return VL_MODE_DEFAULT_FALLBACK, "default"

    def _vl_writer_note(self, source: str, kind: str, now: float) -> dict:
        src = self._vl_src(source)
        st = self._vl_writer_stats.setdefault(
            src,
            {"apply": [], "witness": [], "disagree": [], "held": [],
             "veto": [], "budget": [], "last_apply_ts": 0.0,
             "last_witness_ts": 0.0, "first_ts": now},
        )
        if kind in st and isinstance(st[kind], list):
            st[kind] = [t for t in st[kind] if now - t < 3600.0][-63:] + [now]
        if kind == "apply":
            st["last_apply_ts"] = now
        elif kind == "witness":
            st["last_witness_ts"] = now
        return st

    def _vl_budget_ok(self, source: str, now: float) -> bool:
        st = self._vl_writer_stats.get(self._vl_src(source))
        if not st:
            return True
        hour = sum(1 for t in st["apply"] if now - t < 3600.0)
        burst = sum(1 for t in st["apply"] if now - t < VL_BURST_WINDOW_SEC)
        return hour < VL_APPLIES_PER_HOUR and burst < VL_APPLIES_BURST

    def _vl_auto_shadow(
        self, source: str, now: float, *, reason: str,
        counts: dict | None = None,
    ) -> None:
        """Demote-only: override→shadow; never auto-clears (recovery is
        operator vl.mode clear or restart with corrected envs — boring by
        design). The mirrored row carries the why."""
        src = self._vl_src(source)
        if self._vl_mode_override.get(src) == "shadow":
            return
        from_mode, _ = self._vl_mode(src)
        self._vl_mode_override[src] = "shadow"
        ev = {"kind": "vl_auto_shadow", "source": src, "from_mode": from_mode,
              "reason": reason, "counts": counts or {}, "ts": now}
        self.inventory.events.append(ev)
        DoorInventory._mirror_event(
            ev, history=door_watch_module.OCCUPANCY_HISTORY)

    def _vl_disagree(self, source: str, now: float, *, reason: str) -> None:
        """Per-source disagreement mark + error-budget auto-shadow check.
        Source A's verdicts never charge source B (per-source accounting)."""
        st = self._vl_writer_note(source, "disagree", now)
        n_dis = sum(
            1 for t in st["disagree"] if now - t < VL_ERRBUDGET_WINDOW_SEC)
        n_budget = sum(1 for t in st["budget"] if now - t < 3600.0)
        if n_dis >= VL_ERRBUDGET_N:
            self._vl_auto_shadow(
                source, now, reason="error_budget",
                counts={"disagreements": n_dis,
                        "window_sec": VL_ERRBUDGET_WINDOW_SEC,
                        "trigger": reason})
        elif n_budget >= 2:
            self._vl_auto_shadow(
                source, now, reason="budget_exhausted_2x",
                counts={"budget_exhausted_1h": n_budget, "trigger": reason})

    def _vl_writer_block(self, now: float | None = None) -> dict:
        """Publishable per-writer stats for /health and /v1/presence —
        additive monitoring surface (§3.7); the object only publishes."""
        now = now if now is not None else time.time()
        out: dict[str, dict] = {}
        for src, st in self._vl_writer_stats.items():
            mode, mode_source = self._vl_mode(src)
            applied_1h = sum(1 for t in st["apply"] if now - t < 3600.0)
            last_seen = max(
                st["last_apply_ts"], st["last_witness_ts"], st["first_ts"])
            out[src] = {
                "mode": mode,
                "mode_source": mode_source,
                "applied_1h": applied_1h,
                "witnessed_1h": sum(
                    1 for t in st["witness"] if now - t < 3600.0),
                "held_1h": sum(1 for t in st["held"] if now - t < 3600.0),
                "vetoed_1h": sum(1 for t in st["veto"] if now - t < 3600.0),
                "disagreements_30m": sum(
                    1 for t in st["disagree"] if now - t < 1800.0),
                "budget_left_1h": max(0, VL_APPLIES_PER_HOUR - applied_1h),
                "last_apply_ts": st["last_apply_ts"] or 0,
                "last_witness_ts": st["last_witness_ts"] or 0,
                "silent_sec": round(max(0.0, now - last_seen), 1),
                "auto_shadowed": self._vl_mode_override.get(src) == "shadow",
            }
        return out

    def _schedule_oracle_witness(self, crossing_ts: float) -> None:
        """Off-thread tape oracle → note_witness (one witness API).

        Stretches the matching pending's expires so a slow 35B call does not
        lose to uncorroborated_expiry while the answer is still in flight.
        Kill switch: RAIL_DOOR_ORACLE_WITNESS (default off).
        """
        if not DOOR_ORACLE_WITNESS or not crossing_ts:
            return
        # de-dupe by rounded ts (same crossing re-observed)
        key = round(float(crossing_ts), 3)
        if key in self._oracle_witness_inflight:
            return
        self._oracle_witness_inflight.add(key)
        # stretch pending window while oracle runs
        extra = DOOR_ORACLE_WITNESS_EXTRA_SEC
        for p in self.inventory.pending_crossings:
            if abs(float(p.get("ts") or 0) - float(crossing_ts)) <= 1.0:
                p["expires"] = max(
                    float(p.get("expires") or 0),
                    time.time() + extra,
                )
                p["oracle_witness_pending"] = True
        t = threading.Thread(
            target=self._oracle_witness_worker,
            args=(float(crossing_ts), key),
            name=f"oracle-witness-{key}",
            daemon=True,
        )
        t.start()

    def _oracle_witness_worker(self, crossing_ts: float, key: float) -> None:
        """Ask ops on the tape sequence; feed note_witness; resolve pending."""
        try:
            from .oracle_describe import describe_crossing

            verdict = describe_crossing(
                crossing_ts, votes=DOOR_ORACLE_WITNESS_VOTES
            )
            direction = (verdict or {}).get("direction")
            pick = (verdict or {}).get("pick") or {}
            if direction == "add":
                wit_dir = "enter"
            elif direction == "sub":
                wit_dir = "leave"
            else:
                # Always log NONE/fail so silent wrong-slides are visible.
                print(
                    f"[oracle_witness] no_dir ts={crossing_ts} "
                    f"ok={bool((verdict or {}).get('ok'))} "
                    f"reason={(verdict or {}).get('reason')} "
                    f"method={pick.get('method')} "
                    f"offsets={pick.get('offsets_sec')} "
                    f"people={len((verdict or {}).get('people') or [])}",
                    flush=True,
                )
                return
            inv = self.inventory
            inv.note_witness(
                wit_dir,
                ts=crossing_ts,
                source="oracle",
                window_sec=max(
                    VL_DEDUP_SEC, DOOR_ORACLE_WITNESS_EXTRA_SEC, 12.0
                ),
            )
            now = time.time()
            settled = inv._resolve_pending(
                now,
                transit_window_sec=max(
                    VL_DEDUP_SEC if VL_DEDUP_SEC > 0 else 12.0,
                    DOOR_ORACLE_WITNESS_EXTRA_SEC,
                ),
            )
            # Off-thread mint: main loop won't see these events for pack start
            try:
                from . import door_capture

                if occupancy_on() and door_capture.ENABLED:
                    for e in settled or []:
                        ek = e.get("kind")
                        kind = (
                            "enter"
                            if ek in ("enter", "u_mint")
                            else ("leave" if ek == "leave" else None)
                        )
                        if not kind:
                            continue
                        door_capture.get_capture().start_event(
                            kind=kind,
                            trigger_seq=self._buffer_seq,
                            trigger_ts=float(
                                e.get("ts") or crossing_ts or now
                            ),
                            slot_key=str(
                                e.get("name") or e.get("unnamed_key") or ""
                            )
                            or None,
                            on_done=self._register_v2_artifacts,
                        )
            except Exception:
                pass
            print(
                f"[oracle_witness] {wit_dir} ts={crossing_ts} "
                f"method={pick.get('method')} "
                f"offsets={pick.get('offsets_sec')} "
                f"→ {[e.get('kind') for e in (settled or [])]}",
                flush=True,
            )
        except Exception as ex:
            print(f"[oracle_witness] fail: {ex}", flush=True)
        finally:
            self._oracle_witness_inflight.discard(key)

    def _vl_witness(
        self,
        *,
        entered: int,
        left: int,
        source: str,
        mode: str,
        stamps: list[str],
        et: dict | None,
        lt: dict | None,
        note: str | None,
        hint: dict | None,
        leave_guest: int | None,
        leave_person_id: str | None,
        now: float,
        kind: str = "vl_witnessed",
    ) -> RoomPresence:
        """The witness branch (§3.4): EVERY evidence side-effect, ZERO
        arithmetic. No line here mutates present/unnamed/unnamed_keys or
        fires on_leave — headcount_unchanged is structural, not tested-in."""
        inv = self.inventory
        direction = "leave" if left > 0 else "enter"
        slot: str | None = None
        traits_landed = False
        still_captured = False
        if entered > 0:
            # D7 slot-resolution ladder: recent same-sign _vl_recent row →
            # stamps[0] → sole named occupant → newest in-grace U
            with self._vl_dedup_lock:
                for r in reversed(self._vl_recent):
                    if (
                        now - float(r.get("ts") or 0) < (VL_DEDUP_SEC or 12.0)
                        and int(r.get("bodies") or 1) <= 1
                        and (int(r.get("entered") or 0)
                             - int(r.get("left") or 0)) > 0
                        and r.get("slot_key")
                    ):
                        slot = str(r["slot_key"])
                        break
            if slot and slot not in inv.present and slot not in inv.unnamed_keys:
                slot = None
            if not slot and stamps and stamps[0] in inv.present:
                slot = stamps[0]
            if not slot and len(inv.present) == 1 and inv.unnamed == 0:
                slot = next(iter(inv.present))
            if not slot:
                for uk in reversed(inv.unnamed_keys):
                    if now < inv.entry_grace_until.get(uk, 0.0):
                        slot = uk
                        break
        else:
            # leave witness: audit linkage only (record-only — no pop, no
            # _note_left, no still prune)
            pid = (leave_person_id or "").strip().lower() or None
            if pid and (pid in inv.present or pid in inv.unnamed_keys):
                slot = pid
        # Finding C (2026-07-28): an enter-witness resolved to an ARMED slot
        # proves that body came back IN — the pending door_empty_after
        # confirm's premise (door stayed empty after the arm) is dead. Disarm
        # only the same slot; other armed exits still confirm. Law (a) re-arms
        # on the next true exit sighting.
        if (
            entered > 0
            and slot
            and slot in inv.exit_armed
            and os.environ.get("RAIL_PRESENCE_INWARD_DISARM", "1")
            not in ("0", "false", "no")
        ):
            inv.exit_armed.discard(slot)
            inv.armed_at.pop(slot, None)
            inv.leave_since.pop(slot, None)
            dev = {
                "kind": "exit_arm_disarmed",
                "name": slot,
                "ts": now,
                "reason": "inward_witness",
                "source": source,
            }
            inv.events.append(dev)
            DoorInventory._mirror_event(
                dev, history=door_watch_module.OCCUPANCY_HISTORY)
        # D6: the one witness API — feeds pending resolution, doubt, traits
        inv.note_witness(
            direction, ts=now, source=source,
            traits=(et or lt) if slot is None else None,
            window_sec=VL_DEDUP_SEC if VL_DEDUP_SEC > 0 else 12.0,
        )
        if entered > 0 and slot:
            inv.refresh_meta(slot, et, source=f"{source}_witness", now=now)
            traits_landed = bool(et)
            if slot not in inv.enter_still:
                self._capture_enter_stills(
                    slot, hint=hint, source=f"{source}_witness")
                still_captured = slot in inv.enter_still
        # D2: witnessed verdicts ARE recorded, flagged, and skipped by dedup
        with self._vl_dedup_lock:
            self._vl_recent.append(
                {"ts": now, "entered": entered, "left": left,
                 "source": source, "slot_key": slot, "bodies": 1,
                 "witness": True})
            if len(self._vl_recent) > 20:
                self._vl_recent = self._vl_recent[-12:]
        self._vl_writer_note(source, "witness", now)
        ev = {
            "kind": kind,
            "mode": mode,
            "source": source,
            "entered": entered,
            "left": left,
            "slot_key": slot,
            "slot_is_named": bool(slot and not str(slot).startswith("__u")),
            # voter lesson-linkage names (Phase 4 reads these)
            "shadow_slot": slot,
            "shadow_slot_named": bool(
                slot and not str(slot).startswith("__u")),
            "shadow_still_captured": still_captured,
            "traits_landed": traits_landed,
            "still_captured": still_captured,
            "headcount_unchanged": True,
            "leave_guest": leave_guest,
            "leave_traits": lt or None,
            "note": note,
            "headcount": inv.headcount(),
            "ts": now,
        }
        inv.events.append(ev)
        DoorInventory._mirror_event(
            ev, history=door_watch_module.OCCUPANCY_HISTORY)
        cur = self.get()
        st = self._presence_from_inventory(
            frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
            camera_open=cur.camera_open,
            camera_blind=cur.camera_blind,
            last_event=ev,
            count_trust=cur.count_trust or "fact",
            camera_mode=self._mode if self._mode != "reconciling" else "holding",
            door_names=list(cur.door_names or []),
        )
        self._set(st)
        self.cycles += 1
        self.last_cycle_at = now
        if entered > 0:
            self._inspector_auto(
                st,
                trigger="witness_enter",
                person_id_hint=str(slot).lower()
                if slot and not str(slot).startswith("__")
                else None,
            )
        return st

    def apply_vl_delta(
        self,
        entered: int = 0,
        left: int = 0,
        *,
        source: str = "gemma",
        stamp_names: list[str] | None = None,
        note: str | None = None,
        face_certain: bool = False,
        force: bool = False,
        enter_traits: dict | None = None,
        leave_traits: dict | None = None,
        leave_person_id: str | None = None,
        leave_match_conf: float | None = None,
        leave_match_method: str | None = None,
        enter_still_hint: dict | None = None,
        leave_guest: int | None = None,
        station_guess: int | None = None,
        pet: dict | None = None,
    ) -> RoomPresence:
        """Write VL {null|+1|−1} physics into DoorInventory SoT (the User 2026-07-27).

        Multi-voter dedup (COORD 2026-07-27): first non-null net change within
        RAIL_VL_DEDUP_SEC wins; same-sign later → no-op; opposite-sign → no thrash,
        count_trust=stale. force=True bypasses (operator/tests).
        Clothing leave: enter_traits / leave_traits / leave_person_id (CONTRACT-CLOTHING-LEAVE).
        """
        now = time.time()
        entered = max(0, int(entered or 0))
        left = max(0, int(left or 0))
        if isinstance(pet, dict) and pet.get("name"):
            # display only — pets never touch arithmetic (the User law)
            self._pets[str(pet["name"]).lower()[:20]] = {
                "dir": "inside" if str(pet.get("dir")) in ("in", "inside")
                else "outside",
                "ts": now, "source": str(source)[:20],
            }
        if entered == 0 and left == 0 and not force:
            return self.get()
        # Threshold Game: the voter says WHICH GUEST left — a number by
        # arrival order — and the OBJECT resolves it to a slot here. The
        # VLM never touches identity (the User 2026-07-28: keep the game simple).
        if leave_guest is not None and left > 0 and not leave_person_id:
            roster = self.inventory.guest_roster()
            try:
                g = int(leave_guest)
            except (TypeError, ValueError):
                g = 0
            if 1 <= g <= len(roster):
                leave_person_id = roster[g - 1]
                leave_match_method = leave_match_method or "guest"
        stamps = [n.strip().lower() for n in (stamp_names or []) if n and str(n).strip()]
        et = normalize_traits(enter_traits)
        lt = normalize_traits(leave_traits)
        # Small VL sometimes emits left=2 ("room empty") — one body per event max
        # unless operator force.
        if left > 1 and not force:
            note = ((note or "") + f" | cap_left_{left}→1").strip(" |")
            left = 1
        if entered > 1 and not force and not stamps:
            note = ((note or "") + f" | cap_enter_{entered}→1").strip(" |")
            entered = 1

        # --- writer-mode gate (presence-perfect §3.2/§3.4, D1) ---
        # Resolved in the OBJECT; the wire has no mode field. Shadow neither
        # consumes nor sets dedup state; witness rows are flagged (D2).
        mode, mode_source = self._vl_mode(source)
        if force and mode in ("shadow", "subs_only"):
            if mode_source in ("env", "override"):
                # D4: force never overrides an explicit demotion
                note = ((note or "") + " | witness_force_ignored").strip(" |")
            else:
                mode = "full"
        elif force:
            mode = "full"
        if (entered > 0 or left > 0) and (
            mode == "shadow" or (mode == "subs_only" and left == 0)
        ):
            # shadow: everything witnessed; subs_only: adds stay witnessed
            # (every measured overcount came from the add side)
            return self._vl_witness(
                entered=entered, left=left, source=source, mode=mode,
                stamps=stamps, et=et, lt=lt, note=note,
                hint=enter_still_hint, leave_guest=leave_guest,
                leave_person_id=leave_person_id, now=now)
        if mode == "subs_only" and entered > 0 and left > 0:
            # mixed verdict: witness the enter portion, apply the leave
            self.inventory.note_witness(
                "enter", ts=now, source=source, traits=et,
                window_sec=VL_DEDUP_SEC if VL_DEDUP_SEC > 0 else 12.0)
            self._vl_writer_note(source, "witness", now)
            with self._vl_dedup_lock:
                self._vl_recent.append(
                    {"ts": now, "entered": entered, "left": 0,
                     "source": source, "slot_key": None, "bodies": 1,
                     "witness": True})
            note = ((note or "") + " | subs_only_enter_witnessed").strip(" |")
            entered = 0
        unnamed_only = mode == "subs_only"
        if unnamed_only and left > 0:
            # §3.6: default named targets are stripped so VL cannot invent
            # identity. Exceptions (Finding B+, 2026-07-28):
            #   • __uN keys (U drain)
            #   • leave_guest → guest_roster resolution (method=guest) — the
            #     VLM only picked an arrival index; object maps to a slot
            #   • leave_person_id that is already on the named roster AND
            #     leave_guest was also supplied (same path)
            # Pure anonymous multi-named −1 still holds in door_watch.
            pid_su = (leave_person_id or "").strip().lower()
            guest_resolved = (
                (leave_match_method or "").startswith("guest")
                or leave_guest is not None
            )
            if pid_su and not pid_su.startswith("__u"):
                if not (
                    guest_resolved
                    and pid_su in self.inventory.present
                ):
                    leave_person_id = None
        # budget: only a delta that would MUTATE is charged/checked; over-cap
        # is demoted to witness — never queued, never dropped silently
        if (
            (entered > 0 or left > 0)
            and not force
            and not self._vl_budget_ok(source, now)
        ):
            self._vl_writer_note(source, "budget", now)
            self._vl_disagree(source, now, reason="budget_exhausted")
            note = ((note or "") + " | vl_budget_exhausted").strip(" |")
            return self._vl_witness(
                entered=entered, left=left, source=source, mode=mode,
                stamps=stamps, et=et, lt=lt, note=note,
                hint=enter_still_hint, leave_guest=leave_guest,
                leave_person_id=leave_person_id, now=now,
                kind="vl_budget_exhausted")
        # face veto (full mode, non-forced): a −1 against a named slot whose
        # face was at the door within the veto window and who is NOT armed
        # for exit is a conflict — hold the slot, mark disagreement. An
        # armed-at-door face SUPPORTS the leave (exempt).
        if mode == "full" and not force and left > 0:
            pid_v = (leave_person_id or "").strip().lower()
            if (
                pid_v
                and pid_v in self.inventory.present
                and not pid_v.startswith("__u")
                and pid_v not in self.inventory.exit_armed
                and now - float(self.inventory.last_door_seen.get(pid_v) or 0)
                <= VL_FACE_VETO_SEC
            ):
                self._vl_writer_note(source, "veto", now)
                self._vl_disagree(source, now, reason="face_veto")
                ev = {
                    "kind": "vl_face_veto",
                    "source": source,
                    "person_id": pid_v,
                    "entered": entered,
                    "left": left,
                    "face_age_sec": round(
                        now - float(
                            self.inventory.last_door_seen.get(pid_v) or 0), 2),
                    "veto_window_sec": VL_FACE_VETO_SEC,
                    "note": note,
                    "headcount": self.inventory.headcount(),
                    "ts": now,
                }
                self.inventory.events.append(ev)
                DoorInventory._mirror_event(
                    ev, history=door_watch_module.OCCUPANCY_HISTORY)
                cur = self.get()
                st = self._presence_from_inventory(
                    frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                    camera_open=cur.camera_open,
                    camera_blind=cur.camera_blind,
                    last_event=ev,
                    count_trust=cur.count_trust or "fact",
                    camera_mode=self._mode
                    if self._mode != "reconciling" else "holding",
                    door_names=list(cur.door_names or []),
                )
                self._set(st)
                self.cycles += 1
                self.last_cycle_at = now
                return st

        net = entered - left  # >0 enter pressure, <0 leave pressure
        dedup_action: str | None = None
        prior: dict | None = None

        if not force and net != 0 and VL_DEDUP_SEC > 0:
            with self._vl_dedup_lock:
                self._vl_recent = [
                    r for r in self._vl_recent if (now - float(r.get("ts") or 0)) < VL_DEDUP_SEC
                ]
                # Prefer same-sign match over opposite (cleanup wipes must not
                # poison a later voter enter as "opposite").
                same_prior = None
                opp_prior = None
                for r in self._vl_recent:
                    if r.get("witness"):
                        # D2: witnessed rows never influence live arithmetic —
                        # a witnessed +1 must not same_sign_noop a real writer
                        continue
                    rnet = int(r.get("entered") or 0) - int(r.get("left") or 0)
                    if rnet == 0:
                        continue
                    if int(r.get("bodies") or 1) > 1:
                        # two genuine simultaneous entrants must not collapse
                        continue
                    if (net > 0 and rnet > 0) or (net < 0 and rnet < 0):
                        same_prior = same_prior or r
                    else:
                        opp_prior = opp_prior or r
                if same_prior is not None:
                    dedup_action = "same_sign_noop"
                    prior = same_prior
                elif opp_prior is not None:
                    dedup_action = "opposite_stale"
                    prior = opp_prior

        if dedup_action == "same_sign_noop":
            # Phase A: a deduped +1 still lands its traits on the transit's
            # slot (count unchanged) — a silent noop here would drop enter_traits
            if net > 0 and et:
                slot = (prior or {}).get("slot_key")
                if not slot and stamps and stamps[0] in self.inventory.present:
                    slot = stamps[0]
                if not slot and self.inventory.headcount() == 1:
                    if len(self.inventory.present) == 1:
                        slot = next(iter(self.inventory.present))
                    elif self.inventory.unnamed_keys:
                        slot = self.inventory.unnamed_keys[-1]
                if slot:
                    self.inventory.refresh_meta(slot, et, source=source, now=now)
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=cur.camera_open,
                camera_blind=cur.camera_blind,
                last_event={
                    "kind": "vl_delta_deduped",
                    "source": source,
                    "entered": entered,
                    "left": left,
                    "action": "same_sign_noop",
                    "prior": {
                        "source": (prior or {}).get("source"),
                        "entered": (prior or {}).get("entered"),
                        "left": (prior or {}).get("left"),
                        "ts": (prior or {}).get("ts"),
                    },
                    "window_sec": VL_DEDUP_SEC,
                    "note": note,
                    "headcount": self.inventory.headcount(),
                    "ts": now,
                },
                count_trust=cur.count_trust or "fact",
                camera_mode=self._mode if self._mode != "reconciling" else "holding",
                door_names=list(cur.door_names or []),
            )
            self._set(st)
            self.cycles += 1
            self.last_cycle_at = now
            return st

        if dedup_action == "opposite_stale":
            self._vl_disagree(source, now, reason="opposite_stale")
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=cur.camera_open,
                camera_blind=cur.camera_blind,
                last_event={
                    "kind": "vl_delta_conflict",
                    "source": source,
                    "entered": entered,
                    "left": left,
                    "action": "opposite_no_thrash",
                    "prior": {
                        "source": (prior or {}).get("source"),
                        "entered": (prior or {}).get("entered"),
                        "left": (prior or {}).get("left"),
                        "ts": (prior or {}).get("ts"),
                    },
                    "window_sec": VL_DEDUP_SEC,
                    "note": note,
                    "headcount": self.inventory.headcount(),
                    "ts": now,
                },
                count_trust="stale",
                camera_mode=self._mode if self._mode != "reconciling" else "holding",
                door_names=list(cur.door_names or []),
            )
            st.count_certain = False
            self._set(st)
            self.cycles += 1
            self.last_cycle_at = now
            return st

        # Face-stamped names from faceid after count → certain if face_certain
        for n in stamps:
            if face_certain or n in self._id_certain:
                self._id_certain[n] = True
                src = dict(self._id_sources.get(n) or {})
                src["face"] = True
                src["vl_stamp"] = source
                self._id_sources[n] = src
            else:
                self._id_certain.setdefault(n, False)
                src = dict(self._id_sources.get(n) or {})
                src["vl_stamp"] = source
                self._id_sources[n] = src
        # VL clicker = count physics only (not Qwen/Gemma-specific).
        #
        # Allow unnamed +1 while named people are present — that is a real *join*
        # (T2 2026-07-27: another person home, the user enters → +1 must apply; old rule
        # unnamed_add_while_named_present blocked the second body).
        #
        # Cap: already have ≥1 unnamed slot → further +1 is almost always the
        # same walker double-click (U1+U2 alone, or named+U + phantom). Face
        # stamp / code absorb collapses U into a name; do not stack U forever.
        if (
            entered
            and not stamps
            and not force
            and self.inventory.unnamed >= 1
        ):
            note = ((note or "") + " | cap_unnamed_slot").strip(" |")
            # Distinct-body marking (presence-perfect §2.2): a suppressed
            # unstamped +1 carrying door_concurrent>1 evidence marks the
            # newest U un-absorbable (arithmetic-free). A plain suppressed
            # +1 within the transit window stays absorbable (same walker).
            win = VL_DEDUP_SEC if VL_DEDUP_SEC > 0 else 12.0
            second_body = any(
                (now - float(t.get("ts") or 0)) <= win
                and int(t.get("door_concurrent") or 1) > 1
                for t in self.inventory.recent_transits
            )
            if second_body and self.inventory.unnamed_keys:
                meta = self.inventory.unnamed_meta.get(
                    self.inventory.unnamed_keys[-1])
                if meta is not None:
                    cor = meta.setdefault("corroborations", [])
                    if "vl_second_body" not in cor:
                        cor.append("vl_second_body")
            entered = 0
            net = entered - left
            if left == 0:
                cur = self.get()
                st = self._presence_from_inventory(
                    frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                    camera_open=cur.camera_open,
                    camera_blind=cur.camera_blind,
                    last_event={
                        "kind": "vl_delta_suppressed",
                        "source": source,
                        "reason": "unnamed_slot_already",
                        "note": note,
                        "headcount": self.inventory.headcount(),
                        "ts": now,
                    },
                    count_trust=cur.count_trust or "fact",
                    camera_mode=self._mode if self._mode != "reconciling" else "holding",
                    door_names=list(cur.door_names or []),
                )
                self._set(st)
                self.cycles += 1
                self.last_cycle_at = now
                return st
        evs = self.inventory.apply_vl_delta(
            entered,
            left,
            source=source,
            stamp_names=stamps,
            note=note,
            entry_grace_sec=ENTRY_GRACE,
            now=now,
            enter_traits=et,
            leave_traits=lt,
            leave_person_id=leave_person_id,
            leave_match_conf=leave_match_conf,
            force=bool(force),
            protected_names={n for n, c in self._id_certain.items() if c},
            unnamed_only=unnamed_only,
        )
        # writer stats: charge only actual mutations; held drains are the
        # face-path-miss signal (§3.7 held_1h alert)
        if any(e.get("kind") in ("enter", "leave") for e in evs or []):
            if not force:
                self._vl_writer_note(source, "apply", now)
        if any(e.get("kind") == "vl_leave_held" for e in evs or []):
            self._vl_writer_note(source, "held", now)
        # VL seat guessing DELETED (the User 2026-08-16: "if we do not need a seat
        # detector or guess then delete that part. We have a seat detector, the
        # mic.").
        #
        # Nothing ever gated on it. Identity, AUTH, headcount and matching all
        # ignore seat entirely — every reference was an assignment or a display
        # field. For named people the hour was already an init lookup
        # (seat_named, below), not a guess. So this block asked a vision model
        # to infer a seat for UNNAMED bodies, resolved it against the seat
        # table, stored it, and no one read it.
        #
        # The mic beam is the real seat detector: it measures where a voice came
        # from instead of inferring where a body probably went.
        # Named regulars + User: home hour is init room_geometry, not a VL guess.
        if evs:
            from . import seat_slots as _seats

            for e in evs:
                if e.get("kind") not in ("enter", "transit_dedup_noop"):
                    continue
                key = str(
                    e.get("name") or e.get("unnamed_key") or e.get("slot_key") or ""
                )
                home = _seats.seat_named(key) if key else None
                if home is not None:
                    self.inventory.station_guess[key] = int(home)
        # Annotate leave_match with VL method (guest / visual_still) without
        # clobbering object ladder methods (subs_only_guest_named, sole, …).
        if leave_match_method and evs:
            for e in evs:
                if e.get("kind") == "leave" and isinstance(e.get("leave_match"), dict):
                    if leave_person_id and e["leave_match"].get("person_id") == str(
                        leave_person_id
                    ).strip().lower():
                        prev_m = str(e["leave_match"].get("method") or "")
                        if prev_m.startswith("subs_only_") or prev_m in (
                            "sole", "traits", "fallback_unnamed", "fallback_exit_armed",
                            "fallback_lru_unconfirmed",
                        ):
                            e["leave_match"]["vl_method"] = str(leave_match_method)[:40]
                        else:
                            e["leave_match"]["method"] = str(leave_match_method)[:40]
        left_names = [
            str(e.get("name")).lower()
            for e in (evs or [])
            if e.get("kind") == "leave" and e.get("name")
        ]
        if left_names:
            self._note_left(left_names, now=now)
        # Freeze enter stills for enter / traits_refresh slots
        still_keys: list[str] = []
        for e in evs or []:
            if e.get("kind") in ("enter", "traits_refresh"):
                still_keys.extend(e.get("enter_slot_keys") or [])
                if e.get("name"):
                    still_keys.append(str(e["name"]).lower())
                if e.get("unnamed_key"):
                    still_keys.append(str(e["unnamed_key"]))
        if still_keys or (entered > 0):
            seen_k: set[str] = set()
            uniq = []
            for k in still_keys:
                if k and k not in seen_k:
                    seen_k.add(k)
                    uniq.append(k)
            for k in uniq[: max(1, len(uniq) or 1)]:
                # refresh still if missing or traits_refresh
                if k not in self.inventory.enter_still or any(
                    e.get("kind") == "traits_refresh" for e in (evs or [])
                ):
                    self._capture_enter_stills(k, hint=enter_still_hint, source=source)
        # Prune stills for leavers
        for e in evs or []:
            if e.get("kind") != "leave":
                continue
            for key in (e.get("name"), e.get("unnamed_key")):
                if key:
                    self._prune_enter_stills(str(key))
        # Record real applies for multi-voter window. force=True (cleanup /
        # operator thrash) does not pollute the window.
        if net != 0 and not force:
            first_key = None
            for e in (evs or []):
                if e.get("kind") in ("enter", "leave", "transit_dedup_noop"):
                    first_key = (e.get("name") or e.get("unnamed_key")
                                 or e.get("slot_key"))
                    if first_key:
                        break
            with self._vl_dedup_lock:
                self._vl_recent.append(
                    {
                        "ts": now,
                        "entered": entered,
                        "left": left,
                        "source": source,
                        "slot_key": first_key,
                        "bodies": 1,
                    }
                )
                if len(self._vl_recent) > 20:
                    self._vl_recent = self._vl_recent[-12:]
        # prune certainty for people who left
        for n in list(self._id_certain):
            if n not in self.inventory.present:
                self._id_certain.pop(n, None)
                self._id_sources.pop(n, None)
        leave_matches = [
            e.get("leave_match")
            for e in (evs or [])
            if e.get("kind") == "leave" and e.get("leave_match")
        ]
        # Honest response label (finding A 2026-07-28): "vl_delta" means the
        # verdict MUTATED the count. A held/no-op sub answering "vl_delta"
        # poisons voter ledgers and auto_label R5 (applied_vl_delta flag).
        ev_kinds = {e.get("kind") for e in (evs or [])}
        if ev_kinds & {"enter", "leave"}:
            resp_kind = "vl_delta"
        elif "vl_leave_held" in ev_kinds:
            resp_kind = "vl_leave_held"
        elif "transit_dedup_noop" in ev_kinds:
            resp_kind = "vl_delta_deduped"
        elif left > 0:
            resp_kind = "vl_leave_held"
        elif entered > 0:
            resp_kind = "vl_witnessed"
        else:
            resp_kind = "vl_delta"  # null / pet-note posts keep the legacy label
        cur = self.get()
        st = self._presence_from_inventory(
            frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
            camera_open=cur.camera_open,
            camera_blind=cur.camera_blind,
            last_event={
                "kind": resp_kind,
                "source": source,
                "entered": entered,
                "left": left,
                "stamp_names": stamps,
                "events": evs,
                "note": note,
                "headcount": self.inventory.headcount(),
                "leave_match": leave_matches[0] if leave_matches else None,
                "leave_matches": leave_matches or None,
                "ts": now,
            },
            count_trust="fact" if self._mode != "away" else "stale",
            camera_mode=self._mode if self._mode != "reconciling" else "holding",
            door_names=list(cur.door_names or []),
        )
        st.count_certain = (
            st.unidentified == 0
            and all(p.certain for p in st.people if p.name)
            and st.headcount > 0
        ) or (st.headcount == 0)
        self._set(st)
        # post-apply sign-vs-face: this delta drove the board to 0 while
        # faces are AT the door → instant auto-shadow (§3.4d), not just a mark
        if (
            not force
            and left > 0
            and self.inventory.headcount() == 0
            and self.inventory.last_door_names
        ):
            self._vl_writer_note(source, "disagree", now)
            self._vl_auto_shadow(
                source, now, reason="sign_vs_face",
                counts={"last_door_names":
                        sorted(self.inventory.last_door_names)})
        self.cycles += 1
        self.last_cycle_at = now
        # Hand cards follow inventory only when membership actually moved
        if ev_kinds & {"enter", "leave"}:
            self._sync_oracle_hand(reason=f"vl:{source}")
        return st

    def _enter_still_root(self) -> Path:
        ENTER_STILL_DIR.mkdir(parents=True, exist_ok=True)
        return ENTER_STILL_DIR

    def _safe_still_key(self, key: str) -> str:
        k = (key or "").strip().lower().replace("/", "_")
        return "".join(c if c.isalnum() or c in "._-" else "_" for c in k)[:64] or "x"

    @staticmethod
    def _frames_from_hint(hint: dict | None) -> list[bytes]:
        """Read enter-still frames from a voter hint dir (§3.5).

        Path confinement is mandatory: frames_dir must resolve (symlinks
        followed) STRICTLY under a root in RAIL_ENTER_STILL_HINT_ROOTS, and
        each jpg re-resolves under a root too (symlink-escape safe). Any
        violation → [] and the caller falls back to the camera grab."""
        if not isinstance(hint, dict):
            return []
        d = hint.get("frames_dir")
        if not d:
            return []
        roots: list[Path] = []
        for r in (os.environ.get(
                "RAIL_ENTER_STILL_HINT_ROOTS",
                ENTER_STILL_HINT_ROOTS_DEFAULT) or "").split(":"):
            r = r.strip()
            if not r:
                continue
            try:
                roots.append(Path(r).expanduser().resolve())
            except Exception:
                continue
        if not roots:
            return []

        def _under(p: Path) -> bool:
            for rt in roots:
                try:
                    p.relative_to(rt)
                    return True
                except ValueError:
                    continue
            return False

        try:
            base = Path(str(d)).expanduser().resolve()
        except Exception:
            return []
        if not _under(base) or not base.is_dir():
            return []
        try:
            jpgs = sorted(
                (q for q in base.glob("*.jpg") if q.is_file()),
                key=lambda q: q.stat().st_mtime,
            )[-ENTER_STILL_MAX:]
        except Exception:
            return []
        out: list[bytes] = []
        for q in jpgs:
            try:
                rq = q.resolve()
                if not _under(rq):
                    continue  # symlink pointing outside the roots
                out.append(rq.read_bytes())
            except Exception:
                continue
        return out

    def _capture_enter_stills(
        self,
        slot_key: str,
        *,
        hint: dict | None = None,
        source: str = "vl",
        prefer_stillness: bool = True,
        overwrite: bool = False,
    ) -> None:
        """Best-effort: save 1–3 door frames for leave visual match.

        Prefer **stillness** (low inter-frame energy, body settled ~1s) over
        mid-motion blur. the User 2026-08-02: record body on stillness of frames.
        """
        if (
            not overwrite
            and slot_key in self.inventory.enter_still
            and (self.inventory.enter_still[slot_key] or {}).get("stillness")
        ):
            return
        key = self._safe_still_key(slot_key)
        root = self._enter_still_root() / key
        root.mkdir(parents=True, exist_ok=True)
        frames: list[bytes] = self._frames_from_hint(hint)
        # An explicit hint is the CALLER's choice of frames, already
        # path-validated against RAIL_ENTER_STILL_HINT_ROOTS. The door-view
        # filter below exists to reject frames a wandering PTZ happened to
        # grab; it must not second-guess a directory somebody handed us.
        from_hint = bool(frames)
        still_meta: dict[str, Any] = {}
        if prefer_stillness and not frames:
            try:
                from .stillness import pick_still_body_jpegs

                pick = pick_still_body_jpegs(max_out=ENTER_STILL_MAX)
                if pick.get("ok") and pick.get("jpegs"):
                    frames = list(pick["jpegs"])
                    still_meta = {
                        "stillness": True,
                        "still_reason": pick.get("reason"),
                        "span_sec": pick.get("span_sec"),
                        "energies": pick.get("energies"),
                        "seqs": pick.get("seqs"),
                    }
            except Exception as e:
                still_meta = {"stillness_error": str(e)[:80]}
        try:
            cam = self.camera
            if not frames and cam is not None and hasattr(cam, "grab_buffer"):
                buf = cam.grab_buffer(max_frames=12, stride=2) or []
                for item in buf[-ENTER_STILL_MAX:]:
                    if hasattr(item, "bgr") and item.bgr is not None:
                        import cv2

                        ok, enc = cv2.imencode(
                            ".jpg", item.bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 88]
                        )
                        if ok:
                            frames.append(enc.tobytes())
                    elif isinstance(item, (bytes, bytearray)):
                        frames.append(bytes(item))
                    elif isinstance(item, dict) and item.get("jpeg"):
                        frames.append(bytes(item["jpeg"]))
            if not frames and cam is not None and hasattr(cam, "grab"):
                g = cam.grab()
                if g is not None and hasattr(g, "bgr") and g.bgr is not None:
                    import cv2

                    ok, enc = cv2.imencode(
                        ".jpg", g.bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 88]
                    )
                    if ok:
                        frames.append(enc.tobytes())
                elif isinstance(g, (bytes, bytearray)):
                    frames.append(bytes(g))
                elif isinstance(g, dict) and g.get("jpeg"):
                    frames.append(bytes(g["jpeg"]))
        except Exception:
            pass
        if not frames:
            return
        # Entry stills must be door FOV — never seat facefinder zooms (the User 08-09)
        try:
            if from_hint:
                raise _SkipDoorFilter
            from .door_capture import _jpeg_is_door_view

            door_only = [
                j for j in frames if _jpeg_is_door_view(j) is not False
            ]
            if door_only:
                frames = door_only
            else:
                # all facefinder — do not poison enter_stills with chair zooms
                return
        except Exception:
            pass
        paths: list[str] = []
        for i, jpeg in enumerate(frames[:ENTER_STILL_MAX]):
            p = root / f"{i}.jpg"
            try:
                p.write_bytes(jpeg)
                paths.append(str(p))
            except Exception:
                continue
        if not paths:
            return
        meta = {
            "paths": paths,
            "count": len(paths),
            "source": source,
            "ts": time.time(),
            "hint": hint,
            "door_view_only": True,
            **still_meta,
        }
        self.inventory.enter_still[slot_key] = meta

    def _schedule_stillness_capture(self, slot_key: str) -> None:
        if not STILL_DEFER or not slot_key:
            return
        self._still_due[str(slot_key)] = time.time() + max(0.3, STILL_WAIT_SEC)

    def _drain_stillness_captures(self) -> None:
        if not self._still_due:
            return
        now = time.time()
        due = [k for k, t in self._still_due.items() if t <= now]
        for k in due:
            self._still_due.pop(k, None)
            try:
                self._capture_enter_stills(
                    k,
                    source="door_stillness",
                    prefer_stillness=True,
                    overwrite=True,
                )
            except Exception:
                pass

    def _capture_door_enter_stills(self, new_evs: list | None) -> None:
        """Door enter: immediate stillness pick + re-lock after ~1s settle."""
        for e in new_evs or []:
            if e.get("kind") == "enter":
                k = e.get("name") or e.get("unnamed_key")
                if not k:
                    continue
                k = str(k)
                if k not in self.inventory.enter_still:
                    self._capture_enter_stills(
                        k, source="door_buffer", prefer_stillness=True
                    )
                self._schedule_stillness_capture(k)

    def _write_motion_look(self, man: dict) -> None:
        """FaceID crop on a motion pack. No occupant, no SoT +1/−1."""
        try:
            from .entry_face import best_face_in_pack, write_entry_best

            pack = man.get("pack_dir")
            if not pack:
                return
            self.engine.ensure()
            v = best_face_in_pack(
                pack,
                engine=self.engine.face,
                enrolled=self.engine.enrolled,
                stamps=getattr(self, "_person_stamps", None),
            )
            if v:
                write_entry_best(pack, v)
            self.inventory.events.append(
                {
                    "kind": "motion_look",
                    "pack": man.get("id"),
                    "result": "face" if v else "no_usable_face",
                    "name": (v or {}).get("name"),
                    "sim": (v or {}).get("sim"),
                    "ts": time.time(),
                }
            )
        except Exception:
            pass

    def _best_face_from_pack(self, man: dict, slot_key: str) -> None:
        """Analyse the pack for its clearest face; store evidence + verdict.

        Evidence always lands (a real photo of who crossed). Identity only
        CORRECTS the minted name when the best look is unambiguous: a big
        face, a solid match, and a clear margin over the minted name. Anything
        weaker is logged as a disagreement for the eye-check habit — the
        provisional-scoring law (no bank changes from one uncorroborated
        sighting) applies to names too.
        """
        try:
            # the User 2026-08-09: "best clip FROM THE EVENT not best ID clip in
            # storage." best_face_recent widens the search to up to 4
            # NEIGHBOURING packs within 90s, so the frame written onto one
            # crossing can be a different person from an adjacent clip —
            # that is how another person's photo appeared under a guest's entry.
            # The entry photo IS the evidence for THIS crossing, so it must
            # come from this pack's own frames. The cross-pack lookback stays
            # available for identity-only questions, never for the artifact.
            from .entry_face import best_face_in_pack as best_face_recent
            from .entry_face import write_entry_best

            pack = man.get("pack_dir")
            if not pack:
                return
            self.engine.ensure()
            # Look across THIS pack and the ones just before it — the mint is
            # late, so the crossing frames usually sit in the preceding
            # kind=None motion pack (measured on the User's 04:25 entry).
            v = best_face_recent(
                pack,
                engine=self.engine.face,
                enrolled=self.engine.enrolled,
                stamps=getattr(self, "_person_stamps", None),
            )
            if not v:
                self.inventory.events.append(
                    {
                        "kind": "entry_best_face",
                        "slot_key": slot_key,
                        "result": "no_usable_face",
                        "pack": man.get("id"),
                        "ts": time.time(),
                    }
                )
                return
            write_entry_best(pack, v)
            # ADD -> the occupant cache (the User 2026-08-09). "Store the best
            # entry frame as an occupant in the same motion." The cached photo
            # is not a trophy: it is the wardrobe reference the next exit gets
            # matched against, so it is written here — at the moment we have a
            # named face and the frame that proves it — and retired when its
            # owner leaves. Identity comes from the analysed best face, never
            # from slot_key, which is the label this whole chain exists to
            # check. An unnamed crossing still becomes an occupant: a body we
            # failed to identify is still a body, and the count must not
            # depend on getting the name right.
            try:
                from . import occupant_cache

                who = str(v.get("name") or "").strip().lower() or None
                if who is None:
                    who = f"unknown-{str(man.get('id') or '')[-6:]}"
                # WARDROBE, not a portrait. best_face ranks by face AREA, so
                # v["frame"] is the biggest face in the window — routinely a
                # seat close-up with no doorway in it. the User 2026-08-09: the
                # cached photo must be him IN THE DOOR, because the next exit
                # is matched on clothing and the 4B is asked for height as a
                # % of the door opening. entry_body.jpg is the crossing frame;
                # the face frame is only the fallback identity shot.
                _pd = man.get("pack_dir") or ""
                _body = Path(_pd) / "entry_body.jpg" if _pd else None
                _photo = str(_body) if (_body and _body.is_file()) else v.get("frame")
                r = occupant_cache.add(
                    who,
                    photo=_photo,
                    trait_sig=man.get("trait_sig"),
                    sim=v.get("sim"),
                    face_px=v.get("face_px"),
                    event_id=str(man.get("id") or ""),
                    ts=float(man.get("trigger_ts") or time.time()),
                )
                self.events_log(
                    {
                        "kind": "occupant_cached",
                        "name": who,
                        "op": r.get("op"),
                        "headcount": r.get("headcount"),
                        "sim": v.get("sim"),
                        "pack": man.get("id"),
                        "ts": time.time(),
                    }
                ) if hasattr(self, "events_log") else None
            except Exception:
                pass
            # human-eye evidence: keep it beside the slot's stills
            try:
                if v.get("frame"):
                    d = self._enter_still_root() / self._safe_still_key(slot_key)
                    d.mkdir(parents=True, exist_ok=True)
                    (d / f"best_{man.get('id')}.jpg").write_bytes(
                        Path(v["frame"]).read_bytes()
                    )
            except Exception:
                pass
            ev = {
                "kind": "entry_best_face",
                "slot_key": slot_key,
                "pack": man.get("id"),
                "face_px": v.get("face_px"),
                "name": v.get("name"),
                "sim": v.get("sim"),
                "name_nogender": v.get("name_nogender"),
                "sim_nogender": v.get("sim_nogender"),
                "ts": time.time(),
            }
            ev["door_view"] = v.get("door_view")
            best_name = (v.get("name") or "").lower()
            minted = (slot_key or "").lower()
            # A face only re-names a CROSSING when the camera was actually on
            # the door. the User 08-09: a 'leave the user' pack held nothing but
            # facefinder close-ups of another person in her chair — renaming from
            # that would have said another person left while she sat there.
            _match_floor = float(os.environ.get("RAIL_PRESENCE_MATCH_SIM", "0.60"))
            # Prefer live floor; entry packs historically used 0.55 for U-rename
            _enter_floor = min(0.55, _match_floor)
            strong = (
                best_name
                and float(v.get("sim") or 0) >= _enter_floor
                and min(v.get("face_px") or [0, 0]) >= 60
                and v.get("door_view") is True
                and str(man.get("kind")) == "enter"
            )
            # User walk-in bar (2026-08-11): strong door face must mint named
            # present even when no __u slot was created (door miss / buffer lag).
            user_id = self._user_id()
            is_user_hit = bool(best_name) and best_name == user_id
            if strong and (
                float(v.get("sim") or 0) >= _match_floor or is_user_hit
            ):
                try:
                    now_e = time.time()
                    if best_name not in self.inventory.present:
                        self.inventory.present[best_name] = now_e
                        self.inventory.entry_grace_until[best_name] = (
                            now_e + float(ENTRY_GRACE)
                        )
                        self.inventory.seen_at_door.add(best_name)
                        self.inventory.last_door_seen[best_name] = now_e
                        self.inventory.events.append(
                            {
                                "kind": "enter",
                                "name": best_name,
                                "ts": now_e,
                                "source": "entry_best_face",
                                "sim": v.get("sim"),
                                "live_face": True,
                            }
                        )
                        ev["minted_named"] = best_name
                    else:
                        self.inventory.present[best_name] = now_e
                        self.inventory.seen_at_door.add(best_name)
                        self.inventory.last_door_seen[best_name] = now_e
                    self._id_certain[best_name] = True
                    src = dict(self._id_sources.get(best_name) or {})
                    src["face"] = True
                    src["live_face"] = True
                    src["at_door"] = True
                    src["face_sim"] = float(v.get("sim") or 0)
                    self._id_sources[best_name] = src
                    self._note_face_auth(
                        best_name, sim=v.get("sim"), source="entry_best_face"
                    )
                    # Open/hold User session from face at entry (auth until leave)
                    if best_name == user_id:
                        if not self._user_session:
                            self._user_session = {
                                "opened_ts": now_e,
                                "last_strong_voice_ts": now_e,
                                "last_strong_voice_sim": None,
                                "opened_via": "entry_best_face",
                            }
                        # Greeting also runs from _note_face_auth on first sticky;
                        # ensure session-open path still greets if sticky was
                        # already held from an earlier partial match.
                        try:
                            self._maybe_first_auth_greeting(user_id)
                        except Exception:
                            pass
                    try:
                        self._save_inventory_disk(force=True)
                    except Exception:
                        pass
                    try:
                        self._sync_oracle_hand(reason="entry_best_face")
                    except Exception:
                        pass
                except Exception as e:
                    ev["mint_error"] = str(e)[:120]
            # An UNNAMED entry (__uN) that the best door-view face identifies
            # is the cleanest win there is: the room gains a name it would
            # otherwise have carried as a ghost until someone swept.
            if strong and minted.startswith("__u"):
                try:
                    if minted in self.inventory.unnamed_keys:
                        self.inventory.unnamed_keys.remove(minted)
                        self.inventory.unnamed = len(self.inventory.unnamed_keys)
                    if best_name not in self.inventory.present:
                        self.inventory.present[best_name] = time.time()
                    for m in ("gender", "station_guess", "arrival_seq", "traits_live"):
                        d0 = getattr(self.inventory, m, None)
                        if isinstance(d0, dict) and minted in d0:
                            d0[best_name] = d0.pop(minted)
                    ev["named_unnamed"] = {"was": minted, "now": best_name}
                    self._note_face_auth(
                        best_name, sim=v.get("sim"), source="entry_best_face"
                    )
                except Exception:
                    pass
            elif strong and best_name != minted and minted and not minted.startswith("__"):
                ev["disagreement"] = {"minted": minted, "best_face": best_name}
                # correct the slot: same object, renamed from better evidence
                try:
                    if minted in self.inventory.present:
                        self.inventory.present.pop(minted, None)
                        self.inventory.present[best_name] = time.time()
                        for m in ("gender", "station_guess", "arrival_seq"):
                            d0 = getattr(self.inventory, m, None)
                            if isinstance(d0, dict) and minted in d0:
                                d0[best_name] = d0.pop(minted)
                        ev["corrected"] = True
                        self._note_face_auth(
                            best_name, sim=v.get("sim"), source="entry_best_face"
                        )
                except Exception:
                    ev["corrected"] = False
            self.inventory.events.append(ev)
            # Republish SoT when we minted/corrected a name
            if ev.get("minted_named") or ev.get("named_unnamed") or ev.get("corrected"):
                try:
                    cur = self.get()
                    st = self._presence_from_inventory(
                        frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                        camera_open=True,
                        camera_blind=False,
                        last_event={**ev, "ts": time.time()},
                        count_trust=cur.count_trust or "fact",
                        camera_mode=cur.camera_mode or "holding",
                        door_names=list(cur.door_names or []) or (
                            [best_name] if best_name else []
                        ),
                    )
                    self._set(st)
                except Exception:
                    pass
            # Accuracy trail: every entry verdict, so the eye-check habit has
            # a file and the exit game can be promoted on measured agreement
            # rather than hope (provisional-scoring law).
            try:
                p = (
                    _paths.DATA / "results"
                    / "entry_best_face.jsonl"
                )
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("a") as f:
                    f.write(
                        json.dumps(
                            {
                                **{k: ev.get(k) for k in ev if k != "kind"},
                                "kind": man.get("kind"),
                                "frame": v.get("frame"),
                                "from_pack": v.get("from_pack"),
                                "det_score": v.get("det_score"),
                                "soft_gender": v.get("soft_gender"),
                            }
                        )
                        + "\n"
                    )
            except Exception:
                pass
        except Exception:
            pass

    def _register_v2_artifacts(self, man: dict) -> None:
        """door_capture v2 on_done (capture thread): attach the pack's
        entry_face/entry_body to the slot's enter_still meta — artifacts live
        on the one inventory, never a second photo ledger. Enter sigs land on
        traits_live; leave sigs only WRITE A SHADOW VERDICT (no −1, no SoT
        change — provisional-scoring: prove accuracy vs the User-truth first)."""
        try:
            # the User 2026-08-09: "If no door then no event would be an easy rule."
            # a guest's face REGISTRATION drove the camera off the door and the
            # resulting clip was handed to Oracle as an action event. Face
            # registration is not a crossing and must never be judged as one.
            # A pack with no door-view frames is not evidence of a transit —
            # it is evidence the camera was somewhere else.
            try:
                import glob as _glob

                import cv2 as _cv2

                from .entry_face import is_door_view as _idv
                _pd = man.get("pack_dir") or ""
                if _pd:
                    _fs = sorted(_glob.glob(_pd + "/f*.jpg"))
                    _seen = 0
                    for _f in _fs[:: max(1, len(_fs) // 8)]:
                        _im = _cv2.imread(_f)
                        if _im is not None and _idv(_im) is not False:
                            _seen += 1
                    if _fs and _seen == 0:
                        # Person-in-doorway packs fail is_door_view on every
                        # busy frame. Still write the crop; do not mint SoT
                        # from this flag (AUTH still requires door_view=True).
                        self.inventory.events.append({
                            "kind": "pack_door_view_demoted",
                            "reason": "no_true_door_view",
                            "pack": man.get("id"), "ts": time.time(),
                        })
            except Exception:
                pass
            # Motion / still harvest packs: FaceID crop only. Do not run
            # oracle kind-flip or occupant/SoT mint — Argus looks, count
            # stays nameless {null,+1,−1}.
            if str(man.get("kind") or "").lower() in (
                "motion",
                "still",
                "still_empty",
            ):
                self._write_motion_look(man)
                return
            # direction_check already measures the in-vs-out
            # geometry of the crossing and writes suspect_reversed on every
            # pack — and nothing has ever read it. ev_1786306542569 was a clean
            # 37-frame ENTRY (the user, sim 0.727, door_view true) minted as a
            # LEAVE with suspect_reversed=true sitting in its manifest. Because
            # every branch below gates on kind=="enter", that one wrong bit
            # also suppressed best-face selection, the entry photo, and the
            # occupant add. The geometry is measured from the crossing itself;
            # the minted kind comes from a classifier that may not have been
            # looking at the door. Geometry wins, and it is corrected HERE,
            # before anything branches on it.
            # suspect_reversed is NOT a usable direction signal: door_capture
            # returns door_leads for kind=="leave", and the door ROI is where
            # all motion happens at this camera, so it is True on essentially
            # every leave. Auto-flipping on it turned every exit into an entry
            # (the user 16:42 walk-OUT filed as enter) and made SUB unreachable.
            # Trust the minted kind until direction is measured properly.
            # ORACLE: the crossing verdict comes from the TAPE as a time-
            # ordered sequence, judged by ops. A single still cannot encode
            # motion — that is why every geometric attempt broke when someone
            # stood in the doorway while another person walked past. The
            # sequence separates movers from standers, names the crosser, and
            # gives direction, sex, hair and garment in one call.
            # Glimmer owns event. Tape-ops may note the same event_id but
            # must not rewrite pack kind (the User / Claude 2026-08-15).
            try:
                from .oracle_event import event_id_from_ts, open_event

                trig = float(man.get("trigger_ts") or 0)
                eid = str(man.get("id") or "") or event_id_from_ts(trig)
                open_event(trig, pack_id=eid)
                _v: dict = {}
                if os.environ.get("RAIL_ORACLE_KIND_REWRITE", "0") not in (
                    "0",
                    "false",
                    "no",
                    "",
                ):
                    from .oracle_describe import describe_crossing

                    _v = describe_crossing(trig)
                if _v.get("ok"):
                    minted = man.get("kind")
                    man["oracle"] = {
                        "direction": _v.get("direction"),
                        "crosser": _v.get("crosser"),
                        "people": _v.get("people"),
                        "text": _v.get("text"),
                        "frames": _v.get("frames"),
                        "span_sec": _v.get("span_sec"),
                        "minted_kind": minted,
                        "event_id": eid,
                        "advisory": True,
                    }
                    _pd = man.get("pack_dir")
                    if _pd:
                        pdir = Path(_pd)
                        try:
                            (pdir / "oracle.json").write_text(
                                json.dumps(man["oracle"], indent=1),
                                encoding="utf-8",
                            )
                        except Exception:
                            pass
                        try:
                            primary = pdir / "manifest.json"
                            on_disk: dict = {}
                            if primary.is_file():
                                try:
                                    on_disk = json.loads(
                                        primary.read_text(encoding="utf-8")
                                    )
                                except Exception:
                                    on_disk = {}
                            on_disk["kind"] = man.get("kind")
                            if man.get("kind_corrected_from"):
                                on_disk["kind_corrected_from"] = man[
                                    "kind_corrected_from"
                                ]
                            on_disk["oracle"] = man.get("oracle")
                            primary.write_text(
                                json.dumps(on_disk, indent=2),
                                encoding="utf-8",
                            )
                            eid = str(man.get("id") or pdir.name)
                            twin = pdir / f"{eid}.json"
                            if twin.is_file():
                                try:
                                    side = json.loads(
                                        twin.read_text(encoding="utf-8")
                                    )
                                except Exception:
                                    side = {}
                                side["kind"] = man.get("kind")
                                if man.get("kind_corrected_from"):
                                    side["kind_corrected_from"] = man[
                                        "kind_corrected_from"
                                    ]
                                twin.write_text(
                                    json.dumps(side, indent=1),
                                    encoding="utf-8",
                                )
                        except Exception:
                            pass
                    self.inventory.events.append({
                        "kind": "oracle_verdict", "pack": man.get("id"),
                        "direction": _v.get("direction"),
                        "crosser": _v.get("crosser"),
                        "minted": man["oracle"]["minted_kind"],
                        "kind_now": man.get("kind"),
                        "ts": time.time(),
                    })
            except Exception:
                pass
            k = str(man.get("slot_key") or "")
            arts = man.get("artifacts") or {}
            sig = man.get("trait_sig")
            # SUB -> retire the occupant whose CLOTHING matches this exit.
            # Face answers ADD; an exit is a back walking away, so clothing
            # answers SUB. A SUB into an empty room is recorded as a missed
            # ADD, never a negative count (occupant_cache enforces that).
            if man.get("kind") == "leave":
                try:
                    from . import occupant_cache
                    r = occupant_cache.sub(
                        trait_sig=sig,
                        event_id=str(man.get("id") or ""),
                        ts=float(man.get("trigger_ts") or time.time()),
                    )
                    self.log.info("occupant_sub %s", r)
                except Exception:
                    pass
            if man.get("kind") == "enter" and k:
                if arts:
                    meta = dict(self.inventory.enter_still.get(k) or {})
                    meta["v2_pack"] = man.get("pack_dir")
                    for name in ("entry_face", "entry_body"):
                        if arts.get(name):
                            meta[name] = {**arts[name], "pack": man.get("pack_dir")}
                    self.inventory.enter_still[k] = meta
                    # Feed exit_match's text-trait extractor: drop the v2
                    # full-body artifact into the stills dir it reads
                    # (newest-first + mtime cache sig → picked up next call)
                    if arts.get("entry_body") and man.get("pack_dir"):
                        src = Path(man["pack_dir"]) / "entry_body.jpg"
                        if src.exists():
                            d = self._enter_still_root() / self._safe_still_key(k)
                            d.mkdir(parents=True, exist_ok=True)
                            (d / f"v2_body_{man['id']}.jpg").write_bytes(
                                src.read_bytes()
                            )
                # BEST-FACE over the whole pack (the User 2026-08-09): the stills
                # race grabbed a frame ~1s after the mint and stored empty
                # doorways. The pack holds the entire crossing — pick the frame
                # with the LARGEST usable face and use it as both the human-eye
                # evidence and the identity sample.
                self._best_face_from_pack(man, k)
                if sig:
                    stamped = {**sig, "ts": man.get("trigger_ts")}
                    tl = dict(self.inventory.traits_live.get(k) or {})
                    tl["sig_v1"] = stamped
                    self.inventory.traits_live[k] = tl
                    self._recent_sigs[k] = stamped
            elif man.get("kind") == "leave" and sig:
                self._who_left_shadow(man, sig)
        except Exception:
            pass

    def _who_left_shadow(self, man: dict, exit_sig: dict) -> None:
        """Shadow-only who-left verdict: rank known slots' entry sigs against
        the exit signature and log; headcount untouched."""
        try:
            from .door_capture import sig_dist

            now = time.time()
            self._recent_sigs = {
                k: s
                for k, s in self._recent_sigs.items()
                if now - float(s.get("ts") or 0) < 7200
            }
            candidates = dict(self._recent_sigs)  # survives the slot drop
            for slot, tl in list(self.inventory.traits_live.items()):
                s = (tl or {}).get("sig_v1")
                if s:
                    candidates[slot] = s
            if not candidates:
                return
            ranked = sorted(
                (sig_dist(exit_sig, s), slot) for slot, s in candidates.items()
            )
            row = {
                "ts": time.time(),
                "pack": man.get("id"),
                "kind": man.get("kind"),
                "door_named": man.get("slot_key") or None,
                "shadow_pick": ranked[0][1],
                "margin": round(ranked[1][0] - ranked[0][0], 4)
                if len(ranked) > 1
                else None,
                "ranking": [[slot, d] for d, slot in ranked],
                "occupants_now": sorted(self.inventory.present),
                "exit_body_frames": exit_sig.get("n_body_frames"),
            }
            p = (
                _paths.DATA / "results/who_left_shadow.jsonl"
            )
            with p.open("a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception:
            pass

    def _prune_enter_stills(self, slot_key: str) -> None:
        meta = self.inventory.enter_still.pop(slot_key, None)
        if not meta:
            # also try safe key folder
            pass
        key = self._safe_still_key(slot_key)
        root = self._enter_still_root() / key
        try:
            if root.is_dir():
                for p in root.glob("*.jpg"):
                    p.unlink(missing_ok=True)
                root.rmdir()
        except Exception:
            pass
        if meta:
            for p in meta.get("paths") or []:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass

    def get_enter_still_bytes(self, person_id: str, index: int = 0) -> bytes | None:
        pid = (person_id or "").strip().lower()
        meta = self.inventory.enter_still.get(pid)
        if not meta:
            # try safe folder directly
            p = self._enter_still_root() / self._safe_still_key(pid) / f"{int(index)}.jpg"
            if p.is_file():
                return p.read_bytes()
            return None
        paths = meta.get("paths") or []
        i = int(index)
        if i < 0 or i >= len(paths):
            return None
        p = Path(paths[i])
        if p.is_file():
            return p.read_bytes()
        return None


    def _station_guess_for(self, *keys: str | None) -> int | None:
        """Resolve inventory.station_guess with case-insensitive keys.

        Keys are stored lowercased on pin/persist; display names stay canonical
        (the user, U1). Also accepts unnamed_key (__uN).
        """
        sg = getattr(self.inventory, "station_guess", None) or {}
        if not sg:
            return None
        # exact first
        for k in keys:
            if k is None:
                continue
            if k in sg and sg[k] is not None:
                try:
                    return int(sg[k])
                except (TypeError, ValueError):
                    pass
        low = {str(k).lower(): v for k, v in sg.items()}
        for k in keys:
            if k is None:
                continue
            v = low.get(str(k).lower())
            if v is not None:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    pass
        return None

    def _identify_all_in_jpegs(
        self,
        jpegs: list[bytes],
        *,
        source: str,
        stop: str | None = None,
        tripwire: bool = False,
    ) -> dict[str, Any]:
        """Everyone recognisable in these frames — not just the User.

        the User 2026-08-16: "Surprised by voice should trigger a FF at every seat
        ... not FIRST FOUND FACE. That is the law." The seats half of that law
        was a short circuit (now removed); this is the other half.

        _match_user_in_jpegs already asks the bank "who is this?" for every face
        it finds — `_match` returns a name for ANY enrolled person — and then
        throws away every answer that is not the User. So a tour could look
        straight at another person, recognise her, and report only that the User was
        absent. That is why surprise-by-voice could never answer "who else is
        here", and why an unrecognised body got minted instead of named.

        Nothing new is computed here. The same embeddings, the same matcher —
        this keeps the answers instead of discarding them. Read-only: no sticky,
        no events, no bank writes. The caller decides what to do with it.
        """
        import cv2
        import numpy as np

        from . import room_sweep as _rs
        from . import visit_lamp as vl
        from .enrollments import match as _match
        from .static_faces import in_masked_zone, too_small_to_name

        out: dict[str, Any] = {
            "names": {},
            "n_faces": 0,
            "n_qualified": 0,
            "source": source,
            "stop": stop,
            "faces": [],
            "unmatched": [],
        }
        tripped = False
        try:
            self.engine.ensure()
            face = self.engine.face
            enrolled = self.engine.enrolled
        except Exception:
            return out
        if face is None:
            return out
        thr = float(getattr(_rs, "MATCH_SIM", 0.60) or 0.60)
        profile_keep = self._conv_auth_profile_min_sim()
        profile_ok = self._profile_keep_ok(source)
        uid = self._user_id()
        best: dict[str, float] = {}
        best_face: dict[str, Any] | None = None
        best_crop: bytes | None = None
        best_frame: bytes | None = None
        frame_wh: tuple[int, int] | None = None
        hits: list[dict[str, Any]] = []
        for j in jpegs or []:
            if not j:
                continue
            try:
                im = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)
                if im is None:
                    continue
                h, w = im.shape[:2]
                frame_wh = (w, h)
                dets = list(face.detect(im) or [])
                out["n_faces"] += len(dets)
                if source == "door_glance":
                    self._door_background_split(out, face, dets, im.shape)
                # --- Secure tripwire: fires on DETECT, before any embedding ---
                if tripwire and not tripped:
                    nq = 0
                    for fobj in dets:
                        try:
                            bb = face.bbox(fobj) if hasattr(face, "bbox") else None
                        except Exception:
                            bb = None
                        try:
                            ds = (
                                face.det_score(fobj)
                                if hasattr(face, "det_score")
                                else getattr(fobj, "det_score", None)
                            )
                        except Exception:
                            ds = None
                        if bb is None or too_small_to_name(bb):
                            continue
                        if vl.face_qualifies(ds, bb):
                            nq += 1
                    out["n_qualified"] += nq
                    if nq:
                        tripped = True
                        try:
                            self._visit_door_tripwire(nq)
                        except Exception:
                            pass
                for fobj in dets:
                    bbox = face.bbox(fobj) if hasattr(face, "bbox") else None
                    if stop and in_masked_zone(str(stop), bbox, im.shape):
                        continue
                    if too_small_to_name(bbox):
                        continue
                    emb = face.embed(fobj)
                    if emb is None:
                        continue
                    name, sim = _match(emb, enrolled, threshold=0.0)
                    sim_f = float(sim) if sim is not None else -1.0
                    rec = {
                        "name": name,
                        "sim": round(sim_f, 3),
                        "bbox": [round(float(x), 1) for x in bbox] if bbox else None,
                    }
                    try:
                        from .face_engine import FaceEngine as _FE

                        sx, _ = _FE.gender(fobj)
                        if sx in ("M", "F"):
                            rec["sex"] = sx
                    except Exception:
                        pass
                    hits.append(rec)
                    if name and sim_f >= thr and sim_f > best.get(name, 0.0):
                        best[name] = sim_f
                        if sim_f > float((best_face or {}).get("sim") or -1):
                            best_face = rec
                            best_frame = j
                            best_crop = self._crop_bbox_jpeg(im, bbox)
                    elif (
                        name == uid
                        and profile_ok
                        and sim_f >= profile_keep
                        and sim_f > best.get(name, 0.0)
                    ):
                        best[name] = sim_f
                        rec = dict(rec)
                        rec["pose"] = rec.get("pose") or "profile"
                        hits[-1] = rec
                        if best_face is None:
                            best_face = rec
                            best_frame = j
                            best_crop = self._crop_bbox_jpeg(im, bbox)
                    elif sim_f < thr and bbox:
                        crop = self._crop_bbox_jpeg(im, bbox)
                        out["unmatched"].append(
                            {
                                "name": name,
                                "sim": round(sim_f, 3),
                                "bbox": rec.get("bbox"),
                                "sex": rec.get("sex"),
                                "emb": emb,
                                "jpeg": j,
                                "crop": crop,
                            }
                        )
            except Exception:
                continue
        out["names"] = {k: round(v, 3) for k, v in sorted(best.items())}
        if "n_faces_fg" in out:
            out["n_faces_raw"] = out["n_faces"]
            if out.get("door_bg_mode") == "on":
                out["n_faces"] = out["n_faces_fg"]
        # Best per name for the visor.
        by: dict[str, dict] = {}
        for rec in hits:
            n = rec["name"]
            if not n:
                continue
            keep = rec["sim"] >= thr or (
                n == uid and profile_ok and rec["sim"] >= profile_keep
            )
            if keep and rec["sim"] >= float((by.get(n) or {}).get("sim") or -1):
                by[n] = rec
        out["faces"] = sorted(by.values(), key=lambda r: -float(r.get("sim") or 0))
        if frame_wh:
            out["frame_w"], out["frame_h"] = frame_wh
        self.note_faceid_hud(
            source=source,
            names=out["names"],
            faces=out["faces"],
            n_faces=out["n_faces"],
            frame_w=out.get("frame_w"),
            frame_h=out.get("frame_h"),
            frame_jpeg=best_frame,
            crop_jpeg=best_crop,
            locked=(best_face or {}).get("name"),
        )
        return out

    def _door_background_split(self, out: dict, face, dets: list, shape) -> None:
        """C (the User 2026-10-01): split a door frame's faces into background and foreground.

        Background = the resting reference set (killbook `static_at`) or a face that stayed at the same spot
        and size across minutes (`door_bg_filter`). Off by default (RAIL_DOOR_BG_FILTER); `report` learns and
        publishes the split, `on` also makes the published `n_faces` the foreground count. Naming, AUTH and the
        tripwire never read it.
        """
        from . import door_bg_filter as dbg

        m = dbg.mode()
        if m == "off":
            return
        try:
            from .static_faces import killbook

            kb = killbook()
            bbs = [face.bbox(f) if hasattr(face, "bbox") else None for f in dets]
            verdicts = dbg.door_background().observe(
                [b for b in bbs if b is not None],
                static_at=lambda bb: kb.static_at(bb, shape, None),
            )
        except Exception as e:
            out["door_bg_error"] = f"{type(e).__name__}:{e}"[:120]
            return
        n_bg = sum(1 for v in verdicts if v.get("background"))
        out["door_bg_mode"] = m
        out["n_faces_bg"] = int(out.get("n_faces_bg") or 0) + n_bg
        out["n_faces_fg"] = int(out.get("n_faces_fg") or 0) + (len(dets) - n_bg)
        out.setdefault("door_bg", []).extend(verdicts)

    def _match_user_in_jpegs(
        self, uid: str, jpegs: list[bytes], *, source: str
    ) -> float:
        """Best face sim for uid in jpegs; 0 if none. Side-effect: sticky badge.

        Zero-face / empty decode never clears existing sticky (the User 2026-08-11).
        """
        from . import room_sweep as _rs
        from .enrollments import match as _match

        import cv2
        import numpy as np

        uid = (uid or "").strip().lower()
        try:
            self.engine.ensure()
            face = self.engine.face
            enrolled = self.engine.enrolled
        except Exception:
            return 0.0
        if face is None:
            return 0.0
        best = 0.0
        n_faces = 0
        thr = float(getattr(_rs, "MATCH_SIM", 0.60) or 0.60)
        # HIS OWN BANK (round 41, .101 round 40 F1): the User's face filed under a second name ("ted") won every match,
        # so his best stayed 0.0 and every check read "not seen sim=0.000" with him square in the camera. His sim is
        # his own bank's, whoever else scores higher; the best other name is kept for the log. Kill
        # CRISSA_OWNER_OWN_BANK=0 (round 40: he counts only when he is the top name).
        own_bank = owner_own_bank_on()
        mine = {uid: enrolled[uid]} if (own_bank and uid in (enrolled or {})) else None
        others = {k: v for k, v in (enrolled or {}).items() if k != uid} if own_bank else {}
        other, other_sim = "", 0.0
        for j in jpegs:
            if not j:
                continue
            try:
                im = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)
                if im is None:
                    continue
                dets = list(face.detect(im) or [])
                n_faces += len(dets)
                for fobj in dets:
                    emb = face.embed(fobj)
                    if emb is None:
                        continue
                    if own_bank:
                        _n, sim = _match(emb, mine, threshold=0.0, min_margin=0) if mine else (None, -1.0)
                        on, osim = _match(emb, others, threshold=0.0, min_margin=0) if others else (None, -1.0)
                        if on and osim is not None and float(osim) > other_sim:
                            other, other_sim = str(on), float(osim)
                        name = uid
                    else:
                        name, sim = _match(emb, enrolled, threshold=0.0)
                    sim_f = float(sim) if sim is not None else -1.0
                    if name == uid and sim_f > best:
                        best = sim_f
            except Exception:
                continue
        self._last_seen = {"uid": uid, "own": round(max(best, 0.0), 3), "n_faces": n_faces,
                           "other": other if other_sim >= thr else "", "other_sim": round(other_sim, 3),
                           "at": time.time()}
        if n_faces == 0:
            return 0.0  # do not clear sticky
        if best >= thr:
            self._note_face_auth(uid, sim=best, source=source)
            # AUTH badge is allowed. Occupancy mint is not — voice/room-check
            # finding the User used to mark_sticky an absent id (0→2 with no
            # door crossing, 2026-08-25). Refresh only if already in the room.
            present = getattr(self.inventory, "present", {}) or {}
            if uid in present:
                try:
                    self.inventory.mark_sticky([uid], source=source)
                except Exception:
                    pass
            self.inventory.events.append(
                {
                    "kind": "reauth_face",
                    "name": uid,
                    "ts": time.time(),
                    "sim": round(best, 3),
                    "source": source,
                    "n_faces": n_faces,
                }
            )
            return best
        return best if best > 0 else 0.0

    def _reauth_facefind_all_stations(self, uid: str) -> dict:
        """Facefinder at every calibrated patrol stop until User face found.

        the User 2026-08-11: voice failed emb auth OR voice ≠ the User while Authed
        → hunt every seat stop with FF, then door home. Bounded (one lease).
        """
        from . import room_sweep as _rs
        from .look_around import load_geometry

        uid = (uid or "").strip().lower()
        t0 = time.time()
        out: dict = {
            "ok": False,
            "found": False,
            "uid": uid,
            "stops": [],
            "best_sim": 0.0,
            "via": None,
            # every enrolled person seen anywhere on the tour -> best sim
            "found_people": {},
            # occupancy credits: named faces at real seats, not door/media/8
            "found_at_seats": {},
        }
        if self._auth_refused_lights_out():
            out["error"] = "lights_out"
            return out

        thr = float(os.environ.get("RAIL_PRESENCE_MATCH_SIM", "0.60"))
        door_pan = _door_pose_now()["pan"]
        settle = float(os.environ.get("RAIL_SWEEP_SETTLE_SEC", "1.0"))
        had_sticky = uid in self._face_auth_hold

        # Rung 0: current FOV (no pan) — only if not parked at door-only empty
        snaps: list[bytes] = []
        for _ in range(2):
            try:
                snaps.append(_rs._snapshot())
            except Exception:
                break
            time.sleep(0.25)
        if snaps:
            sim = self._match_user_in_jpegs(uid, snaps, source="reauth_r0_live")
            seen0 = self._identify_all_in_jpegs(snaps, source="reauth_r0_live")
            for _n, _s in (seen0.get("names") or {}).items():
                if _s > float(out["found_people"].get(_n) or 0.0):
                    out["found_people"][_n] = _s
            out["stops"].append(
                {"id": "live", "sim": round(sim, 3), "credit": sim >= thr,
                 "names": seen0.get("names") or {},
                 "n_faces": seen0.get("n_faces") or 0}
            )
            # NO first-found short circuit. the User 2026-08-16: "Surprised by voice
            # should trigger a FF at every seat ... not FIRST FOUND FACE. That
            # is the law". The old code returned here the moment the current
            # view happened to contain the User, so the seats were never
            # visited and everyone else in the room stayed invisible — useless
            # for AUTH and worse for a count. Record the credit and carry on.
            if sim >= thr:
                out.update(ok=True, found=True, best_sim=sim, via="live")

        # Rung 1..N: every patrol stop with facefinder engage
        try:
            geom = load_geometry()
            stops = _rs._stops(geom)
        except Exception as e:
            out["error"] = f"stops:{e}"[:120]
            out["sticky_preserved"] = had_sticky or uid in self._face_auth_hold
            return out

        lease = _rs._post(
            "/v1/lease",
            {"owner": "reauth_facefind_all", "ttl_sec": 90},
            timeout=8.0,
        )
        if not lease.get("ok"):
            out["error"] = "camera_leased"
            out["lease"] = lease
            out["sticky_preserved"] = had_sticky or uid in self._face_auth_hold
            return out
        lease_id = lease["lease_id"]
        engage = float(os.environ.get("RAIL_PRESENCE_FACEFINDER_ENGAGE_SEC", "2.5"))
        try:
            for stop_name, pan, tilt, _station in stops:
                is_door = stop_name in ("door_home", "door") or int(pan) == door_pan
                try:
                    _rs._post(
                        "/v1/facefinder",
                        {"on": False, "lease_id": lease_id},
                        timeout=5.0,
                    )
                except Exception:
                    pass
                aim = _rs._post(
                    "/v1/aim",
                    {
                        "pan": int(pan),
                        "tilt": int(tilt),
                        "zoom": 150 if is_door else 100,
                        "lease_id": lease_id,
                    },
                    timeout=25.0,
                )
                if not aim.get("ok"):
                    out["stops"].append({"id": stop_name, "aim_ok": False})
                    continue
                # Settle after aim before snap (mis-timed snaps = door stills)
                time.sleep(max(0.6, settle))
                # Reject seat stop if camera still reports door pan
                try:
                    h = _rs._get("/health") if hasattr(_rs, "_get") else {}
                    pan_now = int(
                        (h or {}).get("pan_actual")
                        or (aim or {}).get("pan_actual")
                        or pan
                    )
                except Exception:
                    pan_now = int(pan)
                if (not is_door) and abs(pan_now - door_pan) < 8000:
                    out["stops"].append(
                        {
                            "id": stop_name,
                            "pan": pan_now,
                            "rejected": "still_at_door_pan",
                            "credit": False,
                        }
                    )
                    continue
                if not is_door:
                    try:
                        _rs._post(
                            "/v1/facefinder",
                            {"on": True, "lease_id": lease_id},
                            timeout=5.0,
                        )
                        time.sleep(max(0.8, engage))
                    except Exception:
                        time.sleep(0.6)
                jpegs: list[bytes] = []
                for _ in range(2):
                    try:
                        jpegs.append(_rs._snapshot())
                    except Exception:
                        break
                    time.sleep(0.35)
                try:
                    _rs._post(
                        "/v1/facefinder",
                        {"on": False, "lease_id": lease_id},
                        timeout=5.0,
                    )
                except Exception:
                    pass
                sim = self._match_user_in_jpegs(
                    uid, jpegs, source=f"reauth_ff_{stop_name}"
                )
                # Who ELSE is at this seat. The law is a tour of every seat, and
                # a tour that only ever reports the User has not answered the
                # question surprise-by-voice asked.
                seen = self._identify_all_in_jpegs(
                    jpegs, source=f"reauth_ff_{stop_name}"
                )
                for _n, _s in (seen.get("names") or {}).items():
                    if _s > float(out["found_people"].get(_n) or 0.0):
                        out["found_people"][_n] = _s
                    if self._seat_face_may_add_occupancy(stop_name, _station):
                        if _s > float(out["found_at_seats"].get(_n) or 0.0):
                            out["found_at_seats"][_n] = _s
                out["stops"].append(
                    {
                        "id": stop_name,
                        "pan": int(pan),
                        "tilt": int(tilt),
                        "sim": round(sim, 3),
                        "facefinder": not is_door,
                        "credit": sim >= thr,
                        "names": seen.get("names") or {},
                        "n_faces": seen.get("n_faces") or 0,
                    }
                )
                # Every seat, every time — see the law note above. Keep the
                # BEST credit rather than the FIRST, and never stop early: a
                # tour that ends at the first hit cannot answer "who else is
                # here", which is exactly what surprise-by-voice is asking.
                if sim >= thr and sim > float(out.get("best_sim") or 0.0):
                    out.update(ok=True, found=True, best_sim=sim, via=stop_name)
        finally:
            try:
                _rs._post("/v1/facefinder", {"on": False}, timeout=5.0)
            except Exception:
                pass
            try:
                _rs._post(
                    "/v1/lease/release", {"lease_id": lease_id}, timeout=8.0
                )
            except Exception:
                pass
            # Multi-stop ends on chair (~300k) or seats_1_2 (tilt −15° desk-down).
            # Always force door home twice so we never rest looking at the desk.
            try:
                self._ensure_door_aim(release_facelock=True)
            except Exception:
                pass
            try:
                _rs._post(
                    "/v1/return_door",
                    {"force": True, "force_door": True},
                    timeout=12.0,
                )
            except Exception:
                pass
            try:
                self._ensure_door_aim(release_facelock=True)
            except Exception:
                pass

        # If found, open / refresh session so user_auth publishes true
        if out.get("found"):
            lv = getattr(self, "_last_user_voice", {}) or {}
            # Session can open with face; voice may refresh later
            if not self._user_session:
                self._user_session = {
                    "opened_ts": time.time(),
                    "last_strong_voice_ts": float(lv.get("ts") or 0) or None,
                    "last_strong_voice_sim": lv.get("sim"),
                    "opened_via": "reauth_facefind_all",
                }
            # Publish presence with new sticky
            try:
                cur = self.get()
                st = self._presence_from_inventory(
                    frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                    camera_open=True,
                    camera_blind=False,
                    last_event={
                        "kind": "ensure_user_auth",
                        "source": "reauth_facefind_all",
                        "uid": uid,
                        "via": out.get("via"),
                        "sim": out.get("best_sim"),
                        "ts": time.time(),
                    },
                    count_trust=cur.count_trust or "fact",
                    camera_mode="holding",
                    door_names=list(cur.door_names or []),
                )
                self._set(st)
                out["presence"] = {
                    "user_auth": st.user_auth,
                    "user_present": st.user_present,
                    "user_auth_reason": st.user_auth_reason,
                }
            except Exception as e:
                out["publish_error"] = str(e)[:120]
            out["ok"] = True
            try:
                self._save_inventory_disk(force=True)
            except Exception:
                pass
        else:
            out["ok"] = True  # hunt completed; found=False
            out["error"] = out.get("error") or "user_face_not_found"
            # Never clear sticky on a failed/zero-face hunt
            out["sticky_preserved"] = had_sticky or uid in self._face_auth_hold
            if out["sticky_preserved"] and uid in (
                getattr(self.inventory, "present", {}) or {}
            ):
                # Keep publishing face-sticky auth while still present
                try:
                    cur = self.get()
                    st = self._presence_from_inventory(
                        frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                        camera_open=cur.camera_open,
                        camera_blind=cur.camera_blind,
                        last_event={
                            "kind": "ensure_user_auth",
                            "source": "reauth_facefind_all",
                            "uid": uid,
                            "found": False,
                            "sticky_preserved": True,
                            "ts": time.time(),
                        },
                        count_trust=cur.count_trust or "fact",
                        camera_mode=cur.camera_mode or "holding",
                        door_names=list(cur.door_names or []),
                    )
                    self._set(st)
                    out["presence"] = {
                        "user_auth": st.user_auth,
                        "user_present": st.user_present,
                        "user_auth_reason": st.user_auth_reason,
                    }
                except Exception:
                    pass
        out["wall_sec"] = round(time.time() - t0, 3)
        return out

    def _current_auth_tier(self, *, reason: str = "") -> str:
        """Live AUTH tier for hunt escalation. Does not mutate sticky."""
        from . import auth_first as af

        uid = self._user_id()
        hold = self._face_auth_hold.get(uid)
        lv = getattr(self, "_last_user_voice", {}) or {}
        r = (reason or "").strip().lower()
        if r in ("wrong_voice", "wrong_voice_reprove") or r.startswith("wrong"):
            reason_for_tier = "wrong_voice"
        else:
            try:
                cur = self.get()
                pub = str(getattr(cur, "user_auth_reason", "") or "")
            except Exception:
                pub = ""
            reason_for_tier = (
                "wrong_voice" if pub == "wrong_voice" else pub
            )
        user_auth = bool(hold) or bool(self._user_session)
        hold_ts = None
        if hold and hold.get("ts") is not None:
            try:
                hold_ts = float(hold["ts"])
            except (TypeError, ValueError):
                hold_ts = None
        lv_ts = None
        if lv.get("ts"):
            try:
                lv_ts = float(lv["ts"])
            except (TypeError, ValueError):
                lv_ts = None
        blk = af.compute_auth_tier(
            user_auth=user_auth,
            face_sticky=bool(hold),
            hold_ts=hold_ts,
            last_voice_match=bool(lv.get("match")),
            last_voice_ts=lv_ts,
            reason=reason_for_tier,
        )
        return str(blk.get("auth_tier") or "absent")

    def _someone_talking(self, *, reason: str = "") -> bool:
        """True if this ensure is from speech or a recent heard/voice stamp."""
        r = (reason or "").strip().lower()
        if r in (
            "house_command",
            "command",
            "wrong_voice",
            "wrong_voice_reprove",
            "reauth_ladder",
        ):
            return True
        now = time.time()
        snd = getattr(self, "_sound", None) or {}
        try:
            if snd.get("ts") and (now - float(snd["ts"])) < 8.0:
                return True
        except (TypeError, ValueError):
            pass
        lv = getattr(self, "_last_user_voice", {}) or {}
        try:
            if lv.get("ts") and (now - float(lv["ts"])) < 8.0:
                return True
        except (TypeError, ValueError):
            pass
        return False

    def _rung0_current_fov(self, uid: str) -> dict:
        """Current FOV match, no pan. Failed match never clears sticky."""
        uid = (uid or "").strip().lower()
        t0 = time.time()
        out: dict = {
            "ok": True,
            "found": False,
            "uid": uid,
            "via": "rung0",
            "best_sim": 0.0,
            "stops": [],
            "oracle_used": False,
        }
        thr = float(os.environ.get("RAIL_PRESENCE_MATCH_SIM", "0.60"))
        snaps: list[bytes] = []
        try:
            from . import room_sweep as _rs

            for _ in range(2):
                try:
                    snaps.append(_rs._snapshot())
                except Exception:
                    break
                time.sleep(0.25)
        except Exception:
            snaps = []
        if snaps:
            sim = self._match_user_in_jpegs(uid, snaps, source="reauth_r0_live")
            out["stops"].append(
                {"id": "live", "sim": round(sim, 3), "credit": sim >= thr}
            )
            if sim >= thr:
                out.update(found=True, best_sim=sim, via="live")
        out["wall_sec"] = round(time.time() - t0, 3)
        return out

    def _wait_pan_near(self, target: int, *, timeout_s: float = 6.0, tol: int = 25000) -> bool:
        """True when pan_actual is at target. Desk is 0; door is ~432000.

        Never cmd_pan: aim sets that to 0 before the motor moves, and the
        Secure snap was a door JPEG (0.9 s miss, gold lamp).
        """
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return True
        from .beam_look import camera_health

        deadline = time.time() + max(0.2, float(timeout_s))
        last = None
        while time.time() < deadline:
            h = camera_health() or {}
            raw = h.get("pan_actual")
            try:
                pan = int(raw)
            except (TypeError, ValueError):
                pan = None
            last = pan
            if pan is not None and abs(pan - int(target)) <= int(tol):
                return True
            time.sleep(0.12)
        print(
            f"[visit-lamp] pan wait timeout target={target} last={last}",
            flush=True,
        )
        return False

    def _desk_look_once(
        self, uid: str, *, force: bool = False, owner: str = "visit_lamp_click"
    ) -> dict:
        """FaceFinder at User desk (hour 6, pan 0), then door home.

        Tools / contended guest AUTH. Firmware FF reframes; we match stills.
        Grabber /v1/facefind always door-homes. Extra door aim in finally.
        ``force`` (lamp click / force_facefind) skips the 30 s pan cooldown.
        """
        uid = (uid or "").strip().lower()
        t0 = time.time()
        out: dict = {
            "ok": True,
            "found": False,
            "uid": uid,
            "via": "desk",
            "best_sim": 0.0,
            "stops": [],
            "returned_door": False,
            "oracle_used": False,
        }
        last_pan = float(getattr(self, "_ensure_auth_pan_ts", 0.0) or 0.0)
        try:
            pan_gap = float(os.environ.get("RAIL_ENSURE_AUTH_PAN_GAP_SEC", "30"))
        except (TypeError, ValueError):
            pan_gap = 30.0
        if (not force) and t0 - last_pan < pan_gap:
            out["skipped"] = "pan_cooldown"
            out["wall_sec"] = 0.0
            self._last_desk_snaps = []
            self._last_desk_unmatched = []
            return out
        self._ensure_auth_pan_ts = t0
        thr = float(os.environ.get("RAIL_PRESENCE_MATCH_SIM", "0.60"))
        try:
            desk_tilt = int(os.environ.get("RAIL_PRESENCE_DESK_TILT", "0"))
        except (TypeError, ValueError):
            desk_tilt = 0
        try:
            from . import room_sweep as _rs

            snaps: list[bytes] = []
            if force:
                # Auth-request. A 0.5 s still with FACEFINDER=0 is a 14 px
                # desk face (or n_faces=0 mid-slew). seat_shot.shot_at is the
                # path that actually grants AUTH: lease, settle, engage FF,
                # then FaceID. Measured: 272×415 @ det 0.86 vs 14×20 off.
                from .seat_shot import shot_at
                from .user_exit_confirm import user_seat_pose

                pose = user_seat_pose()
                try:
                    engage = float(os.environ.get("RAIL_LAMP_CLICK_ENGAGE_SEC") or "1.2")
                except (TypeError, ValueError):
                    engage = 1.2
                try:
                    n_shot = int(os.environ.get("RAIL_LAMP_CLICK_SHOTS") or "2")
                except (TypeError, ValueError):
                    n_shot = 2
                shot = shot_at(
                    int(pose.get("pan") or 0),
                    int(pose.get("tilt") or desk_tilt or 0),
                    owner=owner,
                    engage=max(1.2, engage),
                    n=max(2, n_shot),
                )
                out["aim"] = {
                    "ok": bool(shot.get("aim_ok") or shot.get("ok")),
                    "error": shot.get("error"),
                    "via": "seat_shot",
                }
                if shot.get("error") == "camera_leased":
                    out["skipped"] = "leased"
                    return out
                snaps = list(shot.get("frames") or [])
                out["returned_door"] = bool((shot.get("home") or {}).get("home"))
                print(
                    f"[{owner}] desk shot frames={len(snaps)} "
                    f"aim_ok={out['aim'].get('ok')} error={shot.get('error')}",
                    flush=True,
                )
            else:
                # FACEFINDER=0: /v1/facefind still pans, then door-homes. A
                # snapshot after that is the door, not the desk. Aim, snap
                # while there, home. Non-click AUTH desk looks keep 1.2+0.55.
                body = {"pan": 0, "tilt": desk_tilt, "zoom": 100}
                extra, gap = 0.55, 0.2
                aim = _rs._post("/v1/aim", body, timeout=20.0)
                out["aim"] = {k: aim.get(k) for k in ("ok", "pan", "error") if k in aim}
                if extra:
                    time.sleep(extra)
                for i in range(2):
                    try:
                        snaps.append(_rs._snapshot())
                    except Exception:
                        break
                    if i == 0 and gap:
                        time.sleep(gap)
            self._last_desk_snaps = list(snaps)
            self._last_desk_unmatched = []
            if snaps:
                try:
                    seen = self._identify_all_in_jpegs(snaps, source="visit_lamp")
                    self._last_desk_unmatched = list(seen.get("unmatched") or [])
                    out["names"] = sorted(
                        str(n).lower()
                        for n in (seen.get("names") or {})
                        if n
                    )
                    try:
                        out["n_faces"] = int(seen.get("n_faces") or 0)
                    except (TypeError, ValueError):
                        out["n_faces"] = 0
                except Exception:
                    self._last_desk_unmatched = []
                    out["names"] = []
                    out["n_faces"] = 0
                sim = self._match_user_in_jpegs(uid, snaps, source="desk_ff")
                out["stops"].append(
                    {
                        "id": "desk",
                        "pan": 0,
                        "station": 6,
                        "sim": round(sim, 3),
                        "credit": sim >= thr,
                        "facefinder": bool(force),
                    }
                )
                if sim >= thr:
                    out.update(found=True, best_sim=sim, via="desk_aim")
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"[:120]
        finally:
            try:
                ret = self._ensure_door_aim(release_facelock=True)
                out["returned_door"] = bool(
                    out.get("returned_door")
                    or ret.get("ok")
                    or ret.get("pan_cmd") is not None
                )
            except Exception as e:
                out["return_error"] = f"{type(e).__name__}: {e}"[:120]
            out["wall_sec"] = round(time.time() - t0, 3)
        return out

    def ensure_user_auth(
        self,
        *,
        person_id: str | None = None,
        voice_matched: bool = False,
        voice_sim: float | None = None,
        reason: str = "command",
        force_facefind: bool = False,
        mode: str | None = None,
    ) -> dict:
        """Command Auth gate (the User 2026-08-11 / AUTH-first 2026-08-12).

        mode:
          • door_local (default) — ring + optional one door-pan FF, ≤5 s, return_door
          • desk — FaceFinder at User desk (hour 6 / pan 0), then door home
          • full — multi-stop parade (opt-in deliberate reauth only)

        Policy:
          • lights_out → noAUTH
          • face sticky alone → AUTH (inventory optional; no Oracle)
          • no sticky → hunt per mode
          • face not found → noAUTH (caller must not execute)

        Safety (2026-08-13): the auth *tier* governs escalation to a pan,
        never whether a door-local re-prove runs. A failed hunt never
        clears sticky.
          fresh/held — no pan; force_facefind still door-local re-proves
          stale      — Rung 0 (current FOV, no pan) then usual door-local
          contested + tools — desk FaceFinder (hour 6); not door chase
          absent     — door-local; on miss + talking + pan-gap → one desk
                       look (pan 0) then door home
        """
        uid = (person_id or self._user_id()).strip().lower()
        t0 = time.time()
        mode_s = (mode or os.environ.get("RAIL_ENSURE_AUTH_MODE") or "door_local")
        mode_s = str(mode_s).strip().lower()
        if mode_s not in (
            "door_local",
            "full",
            "door",
            "local",
            "parade",
            "all",
            "desk",
        ):
            mode_s = "door_local"
        if mode_s in ("door", "local"):
            mode_s = "door_local"
        if mode_s in ("parade", "all"):
            mode_s = "full"
        if self._auth_refused_lights_out():
            return {
                "ok": True,
                "auth": False,
                "reason": "lights_out",
                "uid": uid,
                "mode": mode_s,
                "wall_sec": 0.0,
            }
        if self._auth_hunt_blocked_locked():
            # Lock guest: no FaceFinder. Re-AUTH waits for unlock burst.
            return {
                "ok": True,
                "auth": False,
                "reason": "host_locked",
                "uid": uid,
                "mode": mode_s,
                "via": "locked",
                "wall_sec": 0.0,
            }
        r = (reason or "").strip().lower()
        security = r.startswith("security") or r in (
            "wrong_voice",
            "wrong_voice_reprove",
        )
        if getattr(self, "_room_busy", False):
            # R2: hunt deferred until Busy-off unless this is a security event.
            if not security:
                return {
                    "ok": True,
                    "auth": False,
                    "reason": "room_busy",
                    "uid": uid,
                    "mode": mode_s,
                    "via": "busy_deferred",
                    "wall_sec": 0.0,
                }
        if self._door_work_pending() and not security and mode_s in (
            "full", "parade", "all", "desk"
        ):
            return {
                "ok": True,
                "auth": False,
                "reason": "door_pending",
                "uid": uid,
                "mode": mode_s,
                "via": "door_mill",
                "wall_sec": 0.0,
            }

        hold = self._face_auth_hold.get(uid)
        # AUTH-first: sticky alone is enough (inventory optional)
        face_sticky = bool(hold)
        from . import visit_lamp as vl

        lamp_color = str(getattr(self.get(), "visit_lamp", "") or "")
        sticky_ok = vl.sticky_counts(lamp_color, force_facefind=force_facefind)
        if vl.is_secure(lamp_color) or self._visit_is_secure() or getattr(self, "_visit_hold", False):
            # Secure / hold already is AUTH. Do not FaceFinder (hold is
            # permanent until click; camera moves do not hunt).
            face_sticky = True
            sticky_ok = True
            force_facefind = False
        if vl.continual_auth(lamp_color):
            sticky_ok = False
            face_sticky = False

        # Record this-turn voice for session math
        if voice_matched:
            self._last_user_voice = {
                "match": True,
                "sim": voice_sim,
                "ts": time.time(),
                "person_id": uid,
                "source": f"ensure:{reason}",
            }
        elif voice_sim is not None or reason:
            # explicit non-match this turn (optional)
            pass

        # Fast path: face sticky = AUTH (no inventory +1, no Oracle)
        # Stage 2 amber/rust: sticky is not enough — hunt.
        if face_sticky and sticky_ok and not force_facefind:
            if not self._user_session:
                self._user_session = {
                    "opened_ts": time.time(),
                    "last_strong_voice_ts": time.time() if voice_matched else None,
                    "last_strong_voice_sim": voice_sim if voice_matched else None,
                    "opened_via": "voice_plus_sticky"
                    if voice_matched
                    else "face_sticky",
                }
            elif voice_matched:
                self._user_session["last_strong_voice_ts"] = time.time()
                self._user_session["last_strong_voice_sim"] = voice_sim
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=cur.camera_open,
                camera_blind=cur.camera_blind,
                last_event={
                    "kind": "ensure_user_auth",
                    "source": reason,
                    "via": "sticky" if not voice_matched else "voice",
                    "mode": mode_s,
                    "uid": uid,
                    "ts": time.time(),
                },
                count_trust=cur.count_trust or "fact",
                camera_mode=cur.camera_mode or "holding",
                door_names=list(cur.door_names or []),
            )
            self._set(st)
            return {
                "ok": True,
                "auth": True,
                "reason": "ok" if voice_matched else "face_sticky",
                "via": "sticky" if not voice_matched else "voice",
                "uid": uid,
                "face_sticky": True,
                "mode": mode_s,
                "wall_sec": round(time.time() - t0, 3),
                "user_auth": True,
                "oracle_used": False,
            }

        # Need face: no sticky, or force re-prove
        need_face = force_facefind or not face_sticky
        hunt: dict = {}
        if need_face:
            # Coalesce concurrent hunts
            now = time.time()
            last = float(getattr(self, "_ensure_auth_hunt_ts", 0.0) or 0.0)
            min_gap = float(os.environ.get("RAIL_ENSURE_AUTH_MIN_GAP_SEC", "8"))
            if vl.continual_auth(lamp_color):
                min_gap = float(
                    os.environ.get("RAIL_LAMP_HUNT_MIN_GAP_SEC", str(min_gap))
                )
            if now - last < min_gap and not force_facefind:
                still = sticky_ok and uid in self._face_auth_hold
                return {
                    "ok": True,
                    "auth": bool(still),
                    "reason": "hunt_cooldown" if not still else "ok",
                    "via": "cooldown",
                    "uid": uid,
                    "mode": mode_s,
                    "wall_sec": round(time.time() - t0, 3),
                    "oracle_used": False,
                }
            self._ensure_auth_hunt_ts = now
            # Tier governs pan escalation only — never skip door-local re-prove.
            tier = self._current_auth_tier(reason=reason)
            hunt["auth_tier"] = tier
            if tier == "stale" and mode_s != "desk":
                try:
                    r0 = self._rung0_current_fov(uid)
                    hunt.update(r0)
                except Exception as e:
                    hunt.update(
                        {
                            "found": False,
                            "error": str(e)[:120],
                            "via": "rung0",
                        }
                    )
            if not hunt.get("found"):
                if mode_s == "desk":
                    # Tools / contended guest: FaceFinder at desk (hour 6).
                    try:
                        desk = self._desk_look_once(uid, force=force_facefind)
                        hunt = {
                            "found": bool(desk.get("found")),
                            "best_sim": desk.get("best_sim"),
                            "via": desk.get("via") or "desk_ff",
                            "wall_sec": desk.get("wall_sec"),
                            "stops": desk.get("stops"),
                            "returned_door": desk.get("returned_door"),
                            "skipped": desk.get("skipped"),
                            "oracle_used": False,
                            "mode": "desk",
                            "auth_tier": tier,
                        }
                    except Exception as e:
                        hunt = {
                            "found": False,
                            "error": str(e)[:120],
                            "oracle_used": False,
                            "mode": "desk",
                            "auth_tier": tier,
                        }
                elif mode_s == "full":
                    # Opt-in multi-stop parade (deliberate reauth only)
                    try:
                        parade = self._reauth_facefind_all_stations(uid)
                        hunt = {
                            "found": bool(parade.get("found")),
                            "best_sim": parade.get("best_sim"),
                            "via": parade.get("via") or "facefind_all",
                            "wall_sec": parade.get("wall_sec"),
                            "stops": parade.get("stops"),
                            "returned_door": True,
                            "oracle_used": False,
                            "mode": "full",
                            "auth_tier": tier,
                        }
                    except Exception as e:
                        hunt = {
                            "found": False,
                            "error": str(e)[:120],
                            "oracle_used": False,
                            "mode": "full",
                            "auth_tier": tier,
                        }
                else:
                    # AUTH-first default: door-local chase (not multi-stop parade)
                    try:
                        chase = self.chase_user_face_at_door(
                            budget_sec=float(
                                os.environ.get("RAIL_AUTH_DOOR_CHASE_SEC", "5")
                            ),
                            allow_door_ff=True,
                        )
                        hunt = {
                            "found": bool(chase.get("found")),
                            "best_sim": chase.get("best_sim"),
                            "via": chase.get("via") or "door_chase",
                            "wall_sec": chase.get("wall_sec"),
                            "stops": chase.get("stops"),
                            "returned_door": chase.get("returned_door"),
                            "oracle_used": False,
                            "mode": "door_local",
                            "auth_tier": tier,
                        }
                    except Exception as e:
                        hunt = {
                            "found": False,
                            "error": str(e)[:120],
                            "oracle_used": False,
                            "mode": "door_local",
                            "auth_tier": tier,
                        }
                    # Cold-start: door miss + someone talking → one desk look.
                    # Contested never leaves the door. Separate pan cooldown.
                    if (
                        not hunt.get("found")
                        and tier == "absent"
                        and self._someone_talking(reason=reason)
                    ):
                        try:
                            desk = self._desk_look_once(uid)
                        except Exception as e:
                            desk = {
                                "found": False,
                                "error": str(e)[:120],
                                "via": "desk",
                            }
                        if desk.get("found"):
                            hunt = desk
                            hunt["auth_tier"] = tier
                            hunt["mode"] = "door_local"
                        else:
                            hunt["desk_look"] = desk

        # AUTH-first: sticky alone proves found (no inventory conjunct)
        # Stage 2 amber/rust: live hunt only — sticky must not skip FaceFinder.
        # Law 1: found means the hold exists. A FaceFinder look that saw him
        # mints it here if the look did not; a snapshot match the minter
        # refused (Guest, no FaceFinder) is not AUTH.
        if hunt.get("found"):
            hv = str(hunt.get("via") or "")
            # A look that saw him REFRESHES the hold — the expiry clock reads
            # the hold's ts, so a stale hold plus a fresh sighting is fresh
            # again (PR B). From Guest the minter applies Law 1 itself: only
            # a FaceFinder source opens the hold, the rest is refused there.
            self._note_face_auth(uid, sim=hunt.get("best_sim"), source=hv)
        found = uid in self._face_auth_hold and (
            bool(hunt.get("found")) or sticky_ok
        )
        if found and voice_matched:
            # face + voice this path
            if not self._user_session:
                self._user_session = {
                    "opened_ts": time.time(),
                    "last_strong_voice_ts": time.time(),
                    "last_strong_voice_sim": voice_sim,
                    "opened_via": hunt.get("via") or "facefind",
                }
            auth = True
            why = "ok"
            via = "voice_after_facefind" if hunt.get("found") else "voice"
        elif found and not voice_matched:
            # Face proved the User; open sticky present. Session for BAU commands
            # still wants voice OR we allow face-proved reauth to execute once.
            # the User: facefind success → AUTH for this command (re-prove identity).
            if not self._user_session:
                self._user_session = {
                    "opened_ts": time.time(),
                    "last_strong_voice_ts": time.time(),
                    "last_strong_voice_sim": None,
                    "opened_via": f"facefind:{hunt.get('via')}",
                }
            auth = True
            why = "ok_facefind"
            via = hunt.get("via") or "facefind"
        else:
            auth = False
            why = "noauth_face_not_found" if hunt else "noauth"
            via = None

        # Refresh publish
        try:
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=True,
                camera_blind=False,
                last_event={
                    "kind": "ensure_user_auth",
                    "source": reason,
                    "uid": uid,
                    "auth": auth,
                    "why": why,
                    "via": via,
                    "voice_matched": voice_matched,
                    "hunt": {
                        k: hunt.get(k)
                        for k in ("found", "best_sim", "via", "wall_sec", "stops")
                        if hunt
                    },
                    "ts": time.time(),
                },
                count_trust=cur.count_trust or "fact",
                camera_mode="holding",
                door_names=list(cur.door_names or []),
            )
            self._set(st)
        except Exception:
            st = None

        if auth and uid not in self._face_auth_hold:
            # the publish ran _insecure_auth_expiry and popped the hold (an
            # empty FOV past the clock): the reply says the same thing —
            # never auth:true beside user_auth:false (PR B, review item 2)
            auth = False
            why = "expired"
            via = None
        if auth:
            try:
                self._maybe_first_auth_greeting(uid)
            except Exception:
                pass
        return {
            "ok": True,
            "auth": auth,
            "reason": why,
            "via": via,
            "uid": uid,
            "mode": mode_s,
            "voice_matched": voice_matched,
            "face_sticky": uid in self._face_auth_hold,
            "hunt": hunt or None,
            "wall_sec": round(time.time() - t0, 3),
            "user_auth": bool(st.user_auth) if st is not None else auth,
            "user_auth_reason": (
                getattr(st, "user_auth_reason", why) if st is not None else why
            ),
            "oracle_used": False,  # AUTH-first: never Oracle for grant
        }

    def _project_visit_lamp(self, st: RoomPresence) -> None:
        """Derive visit_lamp on every SoT publish. Census failure keeps last colour."""
        from . import visit_lamp as vl

        prev = getattr(self._state, "visit_lamp", None)
        prev_info = getattr(self._state, "visit_lamp_info", None) or {}
        try:
            cannot = bool(getattr(self, "_visit_cannot_see", False))
            if getattr(st, "lights_out", False):
                cannot = True
            # Pure display of the flag. No census, no edge detection here:
            # AUTH moves in _visit_secure_on/_off, never as a colour side effect.
            color, _why, info = vl.project_color(
                secure=bool(getattr(self, "_visit_secure", False)),
                hold=bool(getattr(self, "_visit_hold", False)),
                cannot_see=cannot,
                reason=str(getattr(self, "_visit_secure_reason", "") or ""),
                since=getattr(self, "_visit_secure_since", None),
            )
            info["token"] = bool(vl.load_token())
            offer = self._secure_offer()            # round 17: Secure on a fixed camera (None on the house)
            if offer is not None:
                info["secure_offer"] = offer
            if getattr(self, "_laptop_auth", None) is not None:
                info["face_check"] = "spot"         # round 27: a look on demand on this fixed camera (laptop_auth)
            vl.apply_to_state(st, color=color, info=info)
            self._security_sync(offer)              # ONE TOGGLE: the flag's every edge, written to the toggle
        except Exception:
            if prev:
                st.visit_lamp = prev
                st.visit_lamp_info = dict(prev_info)

    def _visit_is_secure(self) -> bool:
        """The FLAG, never the colour. Colour is sage whenever this is True."""
        return bool(getattr(self, "_visit_secure", False))

    # ONE TOGGLE (round 17 follow-ups, the User 2026-10-13: Secure and Double Secure are positions of the kit's
    # Security toggle, lib/crissa_home, only on a camera that turns). This service still runs Secure exactly as before
    # (its look to raise it, the door tripwire, the password hold); what changes: every publish of the lamp writes the
    # flag back to the toggle (fixed_cam.secure_sync), so the two cannot disagree; a step DOWN out of Secure through
    # this service's own keys needs his face (security_lower_refused: the one face-match sequence, its look this
    # service's FaceFinder at his seat, visit_lamp_face_check); and visit_lamp_face_check is that look for the kit's
    # other settings on a camera that turns. A pack only; CRISSA_SECURITY_ONE_TOGGLE=0 is round 17 before it.
    SECURE_END_WORDS = {"click": "a click", "entry_face_named": "a known face at the door",
                        "entry_face_unnamed": "a face at the door", "camera_face": "the camera came to you"}

    def _security_sync(self, offer: dict | None) -> None:
        try:
            from . import fixed_cam

            turns = isinstance(offer, dict) and offer.get("ok") is True
            if not turns and not (isinstance(offer, dict) and offer.get("kind") in ("fixed", "none")):
                return                              # no word on the camera (the house, an older rule): nothing to sync
            reason = str(getattr(self, "_visit_secure_reason", "") or "")
            fixed_cam.secure_sync(bool(getattr(self, "_visit_secure", False)) and turns,
                                  bool(getattr(self, "_visit_hold", False)) and turns,
                                  why=self.SECURE_END_WORDS.get(reason, reason.replace("_", " ")) if turns else "",
                                  turns=turns)
        except Exception:
            pass

    SECURE_PROOF_RELEASE_S = float(os.environ.get("CRISSA_SECURE_PROOF_RELEASE_S", "600"))

    def security_lower_refused(self, op: str) -> dict | None:
        """Before this service's click (Secure -> off) or release (held -> Secure) under ONE TOGGLE: None = go on (a
        raise, the house, a camera that does not turn, his face seen); else the lamp's answer with the refusal and
        nothing changed. A release within SECURE_PROOF_RELEASE_S of the password that made the hold is that same proof
        (Voice ID again's password proof holds and releases; CRISSA_SECURE_PROOF_RELEASE_S)."""
        from . import fixed_cam

        if not fixed_cam.one_toggle():
            return None
        offer = self._secure_offer()
        if not (isinstance(offer, dict) and offer.get("ok") is True):
            return None
        secure = bool(getattr(self, "_visit_secure", False))
        held = bool(getattr(self, "_visit_hold", False))
        if (op == "click" and not secure) or (op == "release" and not held):
            return None                             # raising, or nothing to lower
        if op == "release" and time.time() - float(getattr(self, "_visit_hold_at", 0.0) or 0.0) <= \
                self.SECURE_PROOF_RELEASE_S:
            return None
        H = fixed_cam._zh()
        cur = "double_secure" if held else "secure"
        if not H.adjust_needs_face("security", current=cur, target="secure" if op == "release" else "on",
                                   camera="turning"):
            return None
        got = H.face_sequence(lambda: self.get().to_dict(), uid=self._user_id(), look=self.visit_lamp_face_check)
        print("[visit-lamp] security: step down %s - face %s (%s)" % (op, "seen" if got["ok"] else "not seen",
                                                                       got["why"]), flush=True)
        if got["ok"]:
            return None
        pub = self.visit_lamp_pub()
        pub.update(ok=False, checked=False, face="refused", error=got["line"], why=got["why"])
        return pub

    def visit_lamp_face_check(self) -> dict:
        """The face-match sequence's look (ONE TOGGLE above): on a camera that turns, FaceFinder at his seat (the
        Secure click's own desk look, then the door again); else the published face block (the live-face rule).
        {"ok", "checked", "found", "sim", "via"}. Nothing else changes: no AUTH, no lamp."""
        from . import fixed_cam

        if fixed_cam.is_house():
            return {"ok": False, "checked": False, "error": "not on the house"}
        uid = self._user_id()
        la = self._laptop() if hasattr(self, "_laptop") else None
        if la is not None:                      # round 27: the spot check ("face check requested")
            got = la.check(self, why="face_check")
            return {"ok": bool(got.get("ok")), "checked": bool(got.get("checked")), "found": bool(got.get("found")),
                    "sim": got.get("sim"), "via": "laptop_check"}
        offer = self._secure_offer()
        if isinstance(offer, dict) and offer.get("ok") is True:
            try:
                got = self._desk_look_once(uid, force=True, owner="security_face_check")
            except Exception as e:
                return {"ok": False, "checked": False, "error": type(e).__name__}
            return {"ok": True, "checked": True, "found": bool(got.get("found")), "sim": got.get("best_sim"),
                    "via": "desk_look"}
        ok, sim, why = fixed_cam._zh().live_face(self.get().to_dict(), uid)
        return {"ok": True, "checked": True, "found": bool(ok), "sim": sim, "via": "face_block", "why": why}

    CAM_NOTE_TTL_S = 1.0
    CAM_NOTE_TEXT = "SECURE OFF · CAMERA IS YOURS"

    def _hold_auth_for_secure(self) -> dict:
        """Sage holds AUTH. Face minted sage; sudo password minted the hold.

        Empty-FOV / BAU expiry must not drop the User while the flag is up.
        Synthesizes the FaceID hold if sage outlived it (the 2026-09-09 leak).
        """
        uid = self._user_id()
        hold = (self._face_auth_hold or {}).get(uid)
        if not hold:
            src = "keyboard_hold" if getattr(self, "_visit_hold", False) else "secure_hold"
            hold = {
                "sim": None,
                "ts": float(getattr(self, "_visit_secure_since", None) or time.time()),
                "source": src,
            }
            if not hasattr(self, "_face_auth_hold") or self._face_auth_hold is None:
                self._face_auth_hold = {}
            self._face_auth_hold[uid] = hold
        if not self._user_session:
            self._user_session = {
                "opened_ts": time.time(),
                "last_strong_voice_ts": None,
                "last_strong_voice_sim": None,
                "opened_via": str(hold.get("source") or "secure_hold"),
            }
        return hold

    def _apply_secure_auth(self, st: RoomPresence) -> None:
        """Every SoT publish: sage ⇒ user_auth True (User mode). Host lock / dark excepted."""
        if not self._visit_is_secure():
            return
        if getattr(st, "lights_out", False):
            return
        if str(getattr(st, "user_auth_reason", "") or "") == "host_locked":
            return
        hold = self._hold_auth_for_secure()
        st.user_auth = True
        if str(st.user_auth_reason or "") in ("", "user_absent", "visit_lamp"):
            st.user_auth_reason = "secure"
        st.user_face_sticky = True
        st.user_present = True
        src = (hold or {}).get("source")
        if src and not st.user_face_auth_source:
            st.user_face_auth_source = src
        if str(st.user_auth_tier or "absent") in ("absent", "stale", "none", ""):
            st.user_auth_tier = "held"
        if st.user_auth_age_s is None:
            try:
                st.user_auth_age_s = max(0.0, time.time() - float(hold.get("ts") or time.time()))
            except (TypeError, ValueError):
                st.user_auth_age_s = 0.0

    def _arm_camera_note(self, text: str | None = None) -> dict:
        note = {
            "text": str(text or self.CAM_NOTE_TEXT),
            "ts": time.time(),
            "ttl_s": float(self.CAM_NOTE_TTL_S),
        }
        self._camera_note = note
        return note

    def _camera_note_pub(self) -> dict | None:
        note = getattr(self, "_camera_note", None)
        if not note:
            return None
        try:
            age = time.time() - float(note.get("ts") or 0)
            ttl = float(note.get("ttl_s") or self.CAM_NOTE_TTL_S)
        except (TypeError, ValueError):
            self._camera_note = None
            return None
        if age > ttl:
            self._camera_note = None
            return None
        return {
            "text": note.get("text") or self.CAM_NOTE_TEXT,
            "ttl_s": ttl,
            "remaining_s": round(max(0.0, ttl - age), 3),
        }

    def _insecure_auth_expiry(self, hold: dict | None) -> str | None:
        """Guest mode: the sticky hold is not immortal. Reason to pop it, or None.

        The BAU clocks (5 s empty FOV / 45 s desk) lived only on the FaceID
        HUD decorator and never touched _face_auth_hold — so amber + hold +
        camera on an empty door stayed User forever (2026-09-09 leak). This
        is the ONE place the hold expires. Sage / keyboard hold: never.
        """
        if not hold:
            return None
        if self._visit_is_secure() or getattr(self, "_visit_hold", False):
            return None
        if self.cam_holder() == getattr(self, "CAM_JOY_OWNER", "user_joystick"):
            return None  # manual aim: AUTH as if Secure until the token is back
        if getattr(self, "_f2f", False) or getattr(self, "_f2f_looking", False):
            return None  # a look is in flight; its frames decide
        la = getattr(self, "_laptop_auth", None)
        if la is not None and la.session_rule():
            return None  # round 28: a laptop's session (laptop_auth.session_tick) is the one clock under Security On
        src = str(hold.get("source") or "")
        if src in ("secure_hold", "keyboard_hold"):
            return "secure_ended"  # sage-minted, no face behind it
        try:
            hud = self.faceid_hud() or {}
        except Exception:
            return None
        if not isinstance(hud, dict):
            return None
        uid = self._user_id()
        lock = str(hud.get("locked") or "").strip().lower()
        if lock and lock != uid:
            return "named_other"
        for rec in hud.get("faces") or []:
            if isinstance(rec, dict):
                nm = str(rec.get("name") or "").strip().lower()
                if nm and nm != uid:
                    return "named_other"
        if hud.get("auth_hold"):
            # the HUD's conv clock (grace / desk persist) holds only while
            # the HUD itself is current: a quiet door never refreshes it, so
            # a stale auth_hold must not keep amber AUTH alive (review item 4)
            try:
                age = hud.get("age_s")
                fresh = age is not None and float(age) <= self._conv_auth_hold_s()
            except (TypeError, ValueError):
                fresh = False
            if fresh:
                return None
        try:
            n_faces = int(hud.get("n_faces") or 0)
        except (TypeError, ValueError):
            n_faces = 0
        g = self.last_grabber if isinstance(getattr(self, "last_grabber", None), dict) else {}
        at_door = g.get("at_door") is True
        empty = n_faces <= 0 and not lock
        if not (empty or (at_door and n_faces <= 0)):
            return None
        now = time.time()
        stamps = [hold.get("ts"), getattr(self, "_conv_auth_lock_ts", None),
                  getattr(self, "_desk_activity_ts", None)]
        last = 0.0
        for t in stamps:
            try:
                if t and float(t) > last:
                    last = float(t)
            except (TypeError, ValueError):
                continue
        if last and (now - last) < self._conv_auth_hold_s():
            return None
        return "user_absent"

    def _end_joystick_secure(self) -> bool:
        """Token returned to the door: Secure (not Hold) ends; AUTH starts BAU grace.

        During the pan sage stayed on so User AUTH held without FF. Hold is
        untouched. the User 2026-09-09: cancel Secure at the end of manual work.
        """
        if not self._visit_is_secure():
            return False
        if getattr(self, "_visit_hold", False):
            return False
        self._arm_camera_note()
        return bool(
            self._visit_secure_off(reason="camera_aim", keep_auth=True, resume=False)
        )

    def _visit_secure_yield_door_watch(self, *, reason: str) -> bool:
        """Compat name — Secure now ends when the joystick token is given back."""
        return self._end_joystick_secure()

    def _ff_at_beam_blocked(self) -> str | None:
        """Secure only → do not vector FaceFinder. Held AUTH is not a skip (Law 1)."""
        from .beam_look import ff_at_beam_blocked

        try:
            st = self.get()
        except Exception:
            st = None
        hud: dict = {}
        try:
            hud = self.faceid_hud() or {}
        except Exception:
            hud = {}
        return ff_at_beam_blocked(
            user_auth=bool(getattr(st, "user_auth", False)),
            visit_lamp=str(getattr(st, "visit_lamp", "") or ""),
            locked=hud.get("locked") if isinstance(hud, dict) else None,
            user_id=self._user_id(),
            age_s=hud.get("age_s") if isinstance(hud, dict) else None,
        )

    def _visit_lamp_hold_door(self) -> None:
        """Secure watches the door. Kill session look; FaceFinder off; door home.

        Remembers an in-flight session look so _visit_secure_off can resume
        FF on the User if Secure drops mid-comms.
        """
        if getattr(self, "_f2f", False):
            self._visit_ff_saved = {
                "reason": str(getattr(self, "_f2f_reason", "") or ""),
                "hour": getattr(self, "_f2f_hour", None),
                "person": getattr(self, "_f2f_person", None),
                "ts": time.time(),
            }
        self._f2f = False
        self._f2f_looking = False
        self._f2f_since = None
        self._f2f_reason = ""
        self._f2f_hour = None
        self._f2f_person = None
        self._f2f_pose = None
        self._f2f_scan_ready = False
        try:
            self._beam_rest()
        except Exception:
            pass
        if self.cam_holder() in self._door_spared():
            return   # the User is aiming, or Register holds it; the door rest waits for the token
        try:
            self._ensure_door_aim(release_facelock=True, force=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Secure flag state machine. ONLY these two methods move the flag.
    # ------------------------------------------------------------------
    def _visit_secure_on(self, *, reason: str = "click") -> None:
        """User click proved the User at the desk → Secure.

        Camera to the door, FaceFinder off, AUTH held for whoever is here
        until a qualified door face or the next click. No timer.
        """
        from . import visit_lamp as vl

        # Round 27 (.101 2026-10-16: "click refused: No turning (360) camera..." and then "SECURE on reason=click",
        # then his AUTH dropped): a look already in flight, or a token, still reached here. Where Secure is not offered
        # nothing turns it on, whatever the path. Kill: RAIL_FIXED_CAM_SECURE=0 (the offer rule itself).
        no = self._secure_not_offered()
        if no is not None:
            print("[visit-lamp] SECURE on refused reason=%s: %s" % (reason, no.get("why")), flush=True)
            return
        with self._visit_secure_lock:
            self._visit_secure = True
            self._visit_secure_reason = reason
            self._visit_secure_since = time.time()
            self._door_trip_logged = None
            self._visit_cannot_see = False
            self._guest_until_secure = False
            hold = bool(getattr(self, "_visit_hold", False))
            vl.save_secure(True, reason=reason, hold=hold)
            from . import secure_token as stok

            prev = vl.load_token() or {}
            vl.save_token(
                stok.mint(
                    self._user_id(),
                    now=time.time(),
                    reason=reason,
                    hold=hold,
                    token_id=str(prev.get("id") or "") or None,
                )
            )
        print(f"[visit-lamp] SECURE on reason={reason}", flush=True)
        try:
            self._hold_auth_for_secure()
        except Exception:
            pass
        try:
            self._visit_lamp_hold_door()
        except Exception:
            pass
        try:
            self._set(self.get())
        except Exception:
            pass
        self._start_secure_watch()

    def _start_secure_watch(self) -> None:
        """Watch for visitors is a door STILL on a clock, not motion-only.

        _motion_glance_loop only glances on a new trigger seq; a body already
        in the door FOV never makes one. While the flag is up, run door_glance
        (tripwire on) every RAIL_SECURE_DOOR_STILL_SEC. Exits when the flag drops.
        """
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        t = getattr(self, "_secure_watch_thread", None)
        if t is not None and t.is_alive():
            return
        t = threading.Thread(
            target=self._secure_watch_loop, name="visit-lamp-secure-watch", daemon=True
        )
        self._secure_watch_thread = t
        t.start()

    def _secure_watch_loop(self) -> None:
        try:
            period = max(2.0, float(os.environ.get("RAIL_SECURE_DOOR_STILL_SEC") or "5"))
        except (TypeError, ValueError):
            period = 5.0
        stop = getattr(self, "_stop", None)
        while getattr(self, "_visit_secure", False):
            try:
                self.door_glance()
            except Exception as e:
                print(f"[visit-lamp] secure still {type(e).__name__}:{e}"[:120], flush=True)
            if stop is not None:
                if stop.wait(period):
                    return
            else:
                time.sleep(period)

    def _visit_secure_off(
        self, *, reason: str, keep_auth: bool = False, resume: bool = True
    ) -> bool:
        """Secure → insecure. Click / entry face drop AUTH. A camera-face
        yield (`keep_auth=True`) keeps User AUTH and does not hunt.

        Keyboard hold is only ever released by a click.
        """
        from . import visit_lamp as vl

        with self._visit_secure_lock:
            if not getattr(self, "_visit_secure", False):
                return False
            # THE HOLD. A keyboard hold is only ever released by a click.
            # Door faces, the tripwire, camera-face, legacy drops: reported, not obeyed.
            if getattr(self, "_visit_hold", False) and reason != "click":
                print(f"[visit-lamp] SECURE held (keyboard) — ignoring drop reason={reason}", flush=True)
                return False
            self._visit_hold = False
            self._visit_secure = False
            self._visit_secure_reason = reason
            self._visit_secure_since = time.time()
            vl.save_secure(False, reason=reason)
            vl.clear_token()
        if keep_auth:
            print(f"[visit-lamp] SECURE off reason={reason} — AUTH held", flush=True)
        else:
            print(f"[visit-lamp] SECURE off reason={reason} — AUTH dropped", flush=True)
        # 1. AUTH drop. A camera yield (joystick) keeps AUTH only through the
        #    BAU grace: a FaceID hold gets the 5 s clock started here and the
        #    publish-path expiry (_insecure_auth_expiry) pops it; a hold that
        #    sage itself synthesized (secure_hold / keyboard_hold) has no face
        #    behind it and cannot outlive sage.
        try:
            st = self.get()
            uid = self._user_id()
            # ROUND 36: an Add/Edit IDs run has the camera: the User's auth is kept through this lamp change
            try:
                from .registration_hold import held as _reg_held
                if not keep_auth and _reg_held():
                    keep_auth = True
                    print("[visit-lamp] AUTH kept: registration in progress", flush=True)
            except Exception:
                pass
            if keep_auth:
                hold = (getattr(self, "_face_auth_hold", None) or {}).get(uid) or {}
                if str(hold.get("source") or "") in ("secure_hold", "keyboard_hold"):
                    keep_auth = False
                    print("[visit-lamp] yield: hold was sage-minted — dropping", flush=True)
                else:
                    now_ts = time.time()
                    self._conv_auth_lock_ts = now_ts
                    self._conv_auth_from_look = True
                    self._desk_activity_ts = None
            if not keep_auth:
                if hasattr(self, "_face_auth_hold"):
                    self._face_auth_hold.pop(uid, None)
                self._user_session = None
                if st is not None:
                    st.user_auth = False
                    st.user_auth_reason = "visit_lamp"
                    st.user_face_sticky = False
                    st.user_face_auth_source = None
            if st is not None:
                le = dict(getattr(st, "last_event", None) or {})
                le.update(
                    {
                        "kind": "visit_lamp",
                        "reason": (f"secure_yield:{reason}" if keep_auth else f"auth_drop:{reason}"),
                        "source": "visit_lamp",
                        "ts": time.time(),
                    }
                )
                st.last_event = le
                self._set(st)  # projects amber
        except Exception:
            pass
        # 2. Back to FF mode. Skip when the User already has the camera.
        if resume and not keep_auth:
            try:
                self._visit_resume_ff(reason=reason)
            except Exception as e:
                print(f"[visit-lamp] ff resume {type(e).__name__}:{e}"[:160], flush=True)
        return True

    def _visit_resume_ff(self, *, reason: str) -> None:
        """After a mid-comms drop: FaceFinder the User's seat, sweep if absent.

        Uses the session look Secure killed (saved in _visit_lamp_hold_door)
        or, failing that, a live-speech signal. Quiet room → stay on the door
        in normal insecure mode; the next VAD/beam event vectors FF as usual.
        """
        from .beam_look import SESSION_LOOK

        saved = dict(getattr(self, "_visit_ff_saved", None) or {})
        self._visit_ff_saved = None
        why = str(saved.get("reason") or "").strip().lower()
        talking = False
        try:
            talking = bool(self._someone_talking(reason="visit_lamp"))
        except Exception:
            talking = False
        if not (why in SESSION_LOOK or talking):
            # Quiet room → BAU: hand the lease back to firmware, which rests
            # on the door by itself. We stop holding it.
            try:
                self._beam_rest()
            except Exception:
                pass
            return
        print(
            f"[visit-lamp] comms in flight ({why or 'speech'}) — FF resume on User",
            flush=True,
        )
        # Seat first (set_f2f → _beam_aim_then_dwell → FaceID at the station).
        # If the User is not in the frame the regular ensure/reauth ladder
        # sweeps the seats; the Crissa "let me get a look at you" hail belongs
        # to that ladder (the agent/Oracle), not here.
        kw = {
            "reason": why or "stt_voice",
            "look": True,
            "force": True,
            "hour": saved.get("hour"),
            "person_id": saved.get("person") or self._user_id(),
        }

        def _go() -> None:
            # A click (re-prove) holds _visit_click_lock; set_f2f refuses while it
            # is held. Wait for the click to finish, then look.
            lock = getattr(self, "_visit_click_lock", None)
            deadline = time.time() + 3.0
            while lock is not None and lock.locked() and time.time() < deadline:
                time.sleep(0.05)
            try:
                self.set_f2f(True, **kw)
            except Exception as e:
                print(f"[visit-lamp] ff resume {type(e).__name__}:{e}"[:160], flush=True)

        threading.Thread(target=_go, name="visit-lamp-ff-resume", daemon=True).start()

    def _door_trip_settle_s(self) -> float:
        try:
            return max(0.0, float(os.environ.get("RAIL_SECURE_TRIP_SETTLE_S") or "8"))
        except (TypeError, ValueError):
            return 8.0

    def _door_trip_note(self, why: str, **extra: object) -> None:
        """Once per sage-on per reason. Fail-safe hold is cheap; a wrong drop is not."""
        if getattr(self, "_door_trip_logged", None) == why:
            return
        self._door_trip_logged = why
        if why == "sudo":
            print("[visit-lamp] door face under sudo — logged, never dropped", flush=True)
        elif why == "settle":
            age = float(extra.get("age") or 0.0)
            settle = float(extra.get("settle") or self._door_trip_settle_s())
            print(
                f"[visit-lamp] door face at {age:.1f}s < {settle:.0f}s settle — pan is not entry",
                flush=True,
            )
        elif why == "off_door":
            pan = extra.get("pan")
            print(
                f"[visit-lamp] door face off_door pan={pan} — camera not on the door, held",
                flush=True,
            )
        elif why == "unknown":
            err = extra.get("error") or "?"
            print(
                f"[visit-lamp] door face at_door unknown ({err}) — held",
                flush=True,
            )

    def _door_trip_allowed(self) -> tuple[bool, str]:
        """King = sudo. Then at_door. Then 8 s settle. Else the face may end sage.

        MEASURED 2026-09-09 07:37:54→56: first still after the pan was still
        the desk (lease-skip on _ensure_door_aim) and named the User →
        entry_face dropped sage + AUTH. Wrong drop costs AUTH; wrong hold
        waits the next 5 s still.
        """
        if not getattr(self, "_visit_secure", False):
            return False, ""
        if getattr(self, "_visit_hold", False):
            self._door_trip_note("sudo")
            return False, "sudo"
        g = self._grabber_fresh(max_age=1.0)
        if g is None:
            self._door_trip_note("unknown", error="no grabber")
            return False, "unknown"
        if g.get("ok") is False:
            self._door_trip_note("unknown", error=str(g.get("error") or "ok=false"))
            return False, "unknown"
        if g.get("at_door") is not True:
            self._door_trip_note("off_door", pan=g.get("pan_actual"))
            return False, "off_door"
        settle = self._door_trip_settle_s()
        since = float(getattr(self, "_visit_secure_since", 0.0) or 0.0)
        age = (time.time() - since) if since else 1e9
        if settle > 0 and since and age < settle:
            self._door_trip_note("settle", age=age, settle=settle)
            return False, "settle"
        return True, ""

    def _visit_door_tripwire(self, n_qualified: int) -> None:
        """Called by _identify_all_in_jpegs right after DETECT on a door frame,
        before embeddings. A qualified face (det_score + size) is enough — we
        do not wait to learn WHO it is. Includes the User re-entering.
        """
        if n_qualified < 1:
            return
        ok, _why = self._door_trip_allowed()
        if not ok:
            return
        # Off-thread so the FaceID embed loop keeps running in parallel.
        threading.Thread(
            target=self._visit_secure_off,
            kwargs={"reason": "entry_face"},
            name="visit-lamp-tripwire",
            daemon=True,
        ).start()

    # Legacy names — kept so nothing else in the tree breaks. Retired.
    def _visit_lamp_auth_edge(self, prev: str | None, color: str) -> None:
        """RETIRED: colour is no longer an AUTH trigger. See _visit_secure_off."""
        return

    def _visit_lamp_keep_auth(self) -> None:
        """RETIRED."""
        return

    def _visit_lamp_drop_auth(self) -> None:
        """RETIRED alias → _visit_secure_off."""
        self._visit_secure_off(reason="legacy_drop")

    def drop_to_guest(self, *, reason: str = "restart", burn_token: bool = False) -> dict:
        """Guest the User: Secure off, keyboard hold off, AUTH sticky gone.

        the User 2026-09-08: occupancy mode switch and any presence restart.
        A Secure token on disk is kept (``burn_token=False``) so the next
        process can FaceFinder and turn the button sage; a miss burns it.
        Keyboard hold never survives a restart: the token carries ``hold``
        as audit only, and Hold needs the password again (ruling 2026-09-09).
        """
        from . import visit_lamp as vl

        why = str(reason or "restart").strip() or "restart"
        # ROUND 36 (the User 2026-10-19): while an Add/Edit IDs run has the camera (Init's registration hold), the
        # continual auth stands down: the guest at the camera drops nobody to guest and shows no amber. A restart is
        # not a face check: it still drops (the hold file cannot vouch for a process that was not there).
        if why != "restart":
            try:
                from .registration_hold import held as _reg_held
                if _reg_held():
                    print(f"[faceid] AUTH drop {self._user_id()} src={why} held: registration in progress", flush=True)
                    return {"ok": True, "held": "registration", "reason": why}
            except Exception:
                pass
        with self._visit_secure_lock:
            self._visit_hold = False
            self._visit_secure = False
            self._visit_secure_reason = why
            self._visit_secure_since = time.time()
            self._guest_until_secure = True
            vl.save_secure(False, reason=why, hold=False)
            if burn_token:
                vl.clear_token()
        uid = self._user_id()
        had = uid in (self._face_auth_hold or {})
        if hasattr(self, "_face_auth_hold"):
            self._face_auth_hold.clear()
        self._user_session = None
        print(
            f"[faceid] AUTH drop {uid} src={why} sim=none "
            f"had_hold={had} — guest until FaceFinder",
            flush=True,
        )
        try:
            st = self.get()
            if st is not None:
                st.user_auth = False
                st.user_auth_reason = "user_absent"
                st.user_face_sticky = False
                st.user_face_auth_source = None
                st.user_session_held = False
                le = dict(getattr(st, "last_event", None) or {})
                le.update(
                    {
                        "kind": "guest_reset",
                        "reason": why,
                        "source": "drop_to_guest",
                        "ts": time.time(),
                    }
                )
                st.last_event = le
                self._set(st)
        except Exception:
            pass
        return {
            "ok": True,
            "user_auth": False,
            "secure": False,
            "hold": False,
            "reason": why,
        }

    def _kick_secure_token_reauth(self) -> None:
        """Boot: if a Secure token is on disk, FaceFinder after FaceID is up."""
        from . import visit_lamp as vl
        from . import secure_token as stok

        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        d = stok.decide(vl.load_token(), self._user_id())
        if d["act"] != "reauth":
            return

        def _go() -> None:
            time.sleep(6.0)
            out = self.visit_secure_from_token(reason="restart_token")
            if out.get("deferred"):
                time.sleep(2.0)
                self.visit_secure_from_token(reason="restart_token")

        threading.Thread(
            target=_go, name="secure-token-reauth", daemon=True
        ).start()

    def visit_secure_reauth(self, *, reason: str = "occupancy") -> dict:
        """Keep sage across a GPU mode switch. A restart still reauths.

        Live Secure → no look (Oracle did not go down). A reason that
        starts with ``occupancy:`` returns the current lamp when sage is
        off: no ``drop_to_guest``, no FaceFinder, no ``guest_until_secure``
        latch. AUTH stays on its own clocks. Any other reason: a disk
        token FaceFinds the desk; no token guests.
        """
        why = str(reason or "").strip()
        if self._visit_is_secure():
            pub = self.visit_lamp_pub()
            pub.update({"ok": True, "inherited": True, "via": "live", "checked": True})
            print(
                f"[visit-lamp] SECURE kept via=live reason={why} — Oracle did not go down",
                flush=True,
            )
            return pub
        if why.startswith("occupancy:"):
            pub = self.visit_lamp_pub()
            pub.update({"ok": True, "inherited": False, "via": "occupancy", "checked": False})
            print(
                f"[visit-lamp] occupancy leaves the lamp reason={why} — sage off, AUTH on its own clock",
                flush=True,
            )
            return pub
        from . import visit_lamp as vl
        from . import secure_token as stok

        d = stok.decide(vl.load_token(), self._user_id())
        if d["act"] == "reauth":
            return self.visit_secure_from_token(reason=why or "restart_token")
        return self.drop_to_guest(reason=why or "guest_reset")

    def visit_secure_from_token(self, *, reason: str = "restart_token") -> dict:
        """Inherit Secure: FaceFinder the desk. Hit → sage. Miss → off."""
        from . import visit_lamp as vl
        from . import secure_token as stok

        tok = vl.load_token()
        uid = self._user_id()
        d = stok.decide(tok, uid)
        if d["act"] != "reauth":
            pub = self.visit_lamp_pub()
            pub.update(
                {
                    "ok": True,
                    "inherited": False,
                    "via": d["why"],
                    "secure": False,
                    "checked": False,
                }
            )
            return pub
        try:
            desk = self._desk_look_once(uid, force=True, owner="secure_token")
        except Exception as e:
            print(
                f"[visit-lamp] token FF error {type(e).__name__}:{e}"[:160],
                flush=True,
            )
            pub = self.visit_lamp_pub()
            pub.update({"ok": False, "deferred": True, "via": "error", "checked": False})
            return pub
        skipped = str(desk.get("skipped") or "")
        if skipped in ("leased", "pan_cooldown", "hunt_cooldown"):
            print(f"[visit-lamp] token FF deferred skipped={skipped}", flush=True)
            pub = self.visit_lamp_pub()
            pub.update(
                {"ok": False, "deferred": True, "via": skipped, "checked": False}
            )
            return pub
        if desk.get("found"):
            try:
                sim = float(desk.get("best_sim") or 0.0)
            except (TypeError, ValueError):
                sim = 0.0
            try:
                self.grant_user_face_auth(
                    sim=sim or None, source="secure_token", greet=False
                )
            except Exception:
                pass
            # Keyboard Hold is this process, this click, this password
            # (BUTTON-MODE-MATRIX 2.4, ruling 2026-09-09): a disk token may
            # restore sage Secure after the desk FaceFinder hit, never Hold.
            # token.hold is audit only; _visit_hold stays False here.
            if d.get("hold"):
                print("[visit-lamp] token carried hold — audit only, Hold needs the password", flush=True)
            self._visit_secure_on(reason=reason or "restart_token")
            print(
                f"[visit-lamp] SECURE on reason={reason} via=token_ff "
                f"sim={desk.get('best_sim')} — button sage",
                flush=True,
            )
            pub = self.visit_lamp_pub()
            pub.update(
                {
                    "ok": True,
                    "inherited": True,
                    "via": "token_ff",
                    "token_hold": bool(d.get("hold")),
                    "hold": False,
                    "checked": True,
                    "returned_door": bool(desk.get("returned_door")),
                }
            )
            return pub
        print(
            f"[visit-lamp] token FF miss skipped={skipped!r} via={desk.get('via')} "
            f"sim={desk.get('best_sim')} n_faces={desk.get('n_faces')} — Secure off",
            flush=True,
        )
        self.drop_to_guest(reason="token_miss", burn_token=True)
        pub = self.visit_lamp_pub()
        pub.update(
            {
                "ok": True,
                "inherited": False,
                "via": "token_miss",
                "secure": False,
                "checked": True,
            }
        )
        return pub

    def _visit_camera_blocked(self, click: bool = False) -> str | None:
        """Observe the grabber. Never take a lease (C2).

        ``click`` is a User vouch: mic beam / f2f are not a broken camera.
        """
        if getattr(self, "_room_busy", False):
            return "room_busy"
        if not click and (
            getattr(self, "_f2f", False) or getattr(self, "_f2f_looking", False)
        ):
            return "f2f"
        try:
            if self._auth_hunt_blocked_locked():
                return "host_locked"
        except Exception:
            pass
        st = self.get()
        if getattr(st, "lights_out", False):
            return "lights_out"
        if not click and self._someone_talking(reason="visit_lamp"):
            return "speaking"
        if click:
            try:
                live = self._poll_grabber()
                if isinstance(live, dict):
                    self._set_last_grabber(live)
            except Exception:
                pass
        g = self.last_grabber if isinstance(self.last_grabber, dict) else {}
        hold_rem = 0.0
        try:
            hold_rem = float(g.get("hold_remaining_sec") or 0.0)
        except (TypeError, ValueError):
            hold_rem = 0.0
        lease = g.get("lease") if isinstance(g.get("lease"), dict) else None
        if (
            g.get("sweeping")
            or (g.get("tool_hold") and hold_rem > 0.05)
            or (g.get("tool_hold") and lease)
            or hold_rem > 0.05
        ):
            return "leased"
        return None

    def visit_lamp_pub(self) -> dict:
        from . import visit_lamp as vl

        st = self.get()
        info = dict(getattr(st, "visit_lamp_info", None) or {})
        return {
            "ok": True,
            "visit_lamp": getattr(st, "visit_lamp", "amber"),
            "visit_lamp_info": info,
            "color": getattr(st, "visit_lamp", "amber"),
            "why": info.get("why"),
            "hover": info.get("hover"),
            "hold": bool(getattr(self, "_visit_hold", False)),
            "token": bool(vl.load_token()),
        }

    def visit_lamp_hold(self, password: str) -> dict:
        """Double-click + the account password → Secure, held until a click.

        Only the password mints a hold (no look, no face). A wrong password
        is refused with no detail and a 2 s gap before the next try. The
        password is used once and dropped.
        """
        from . import visit_lamp as vl
        import getpass

        now = time.time()
        if now - float(getattr(self, "_visit_hold_last_try", 0.0) or 0.0) < 2.0:
            return {"ok": False, "held": False, "error": "try again in a moment"}
        self._visit_hold_last_try = now
        try:
            user = getpass.getuser()
        except Exception:
            user = ""
        ok = vl.verify_password(user, str(password or ""))
        del password
        if not ok:
            print("[visit-lamp] hold refused — password did not verify", flush=True)
            return {"ok": False, "held": False, "error": "password refused"}
        with self._visit_secure_lock:
            self._visit_hold = True
            self._visit_hold_at = time.time()      # ONE TOGGLE: a release soon after is this proof's (above)
        self._visit_secure_on(reason=vl.HOLD_REASON)
        pub = self.visit_lamp_pub()
        pub.update({"ok": True, "held": True, "via": "keyboard_hold"})
        print("[visit-lamp] SECURE held (keyboard) — faces do not matter; casuals stay; only a click releases it", flush=True)
        return pub

    # ── camera joystick (the User 2026-09-06: "aim camera joystick for User to
    # SHOW Crissa something"). AUTH required. Under Secure, sage STAYS for the
    # whole pan (docs/VISIT-LAMP.md, the Joystick row); ⌂ / the token going back
    # to the door drops plain sage (AUTH stays, 1 s note) and starts BAU 5 s.
    # Keyboard hold never drops — faces do not matter; casuals stay unlocked.
    CAM_JOY_OWNER = "user_joystick"
    # the User holds the camera as long as they are aiming it: the grabber's
    # rest-to-door watchdog fired at the 45 s lapse and snapped the camera
    # home mid-walk ("Camera joystick not working", the User 2026-09-07). Under
    # Secure the hold is short so the door watch is not blinded.
    try:
        CAM_JOY_TTL = float(os.environ.get("RAIL_CAM_JOY_TTL") or 300.0)
    except (TypeError, ValueError):
        CAM_JOY_TTL = 300.0
    CAM_JOY_TTL_SECURE = 45.0

    # ---- the camera token: one lease, one ladder, one broker (camera_token.py) ----
    def _cam_token_pub(self) -> dict | None:
        from . import camera_token as ct

        tok = getattr(self, "_cam_token", None)
        if not tok:
            return None
        if ct.expired(tok, time.time()):
            owner = str(tok.get("owner") or "")
            self._cam_token = None
            if owner == getattr(self, "CAM_JOY_OWNER", "user_joystick"):
                try:
                    self._end_joystick_secure()
                except Exception:
                    pass
            return None
        return {"owner": tok.get("owner"), "since": tok.get("since"), "why": tok.get("why"),
                "until": tok.get("until")}

    def cam_holder(self) -> str | None:
        pub = self._cam_token_pub()
        return str(pub.get("owner")) if pub else None

    def _cam_post(self, path: str, body: dict, timeout: float = 8.0) -> dict:
        """The grabber call for the broker. Under pytest with no stub on
        beam_look._cam_post the lease is simulated (no :8192)."""
        if _ports.camera_off():                  # no camera: a no-op that says so (2026-10-10)
            _ports.say_once("camera calls are no-ops")
            return dict(_ports.OFF)
        from . import beam_look as bl

        if os.environ.get("PYTEST_CURRENT_TEST") and getattr(bl._cam_post, "__module__", "") == "crissa_presence.beam_look":
            n = int(getattr(self, "_cam_sim_n", 0)) + 1
            self._cam_sim_n = n
            if path == "/v1/lease":
                return {"ok": True, "lease_id": body.get("lease_id") or f"Lsim{n}", "owner": body.get("owner")}
            return {"ok": True}
        return bl._cam_post(path, body, timeout=timeout)

    def cam_take(self, owner: str, ttl: float, *, why: str = "", steal: bool = False) -> dict:
        """Take the camera token for ``owner``: grant / renew / handoff / steal
        per camera_token.decide. Refusals name the holder — the tool's answer."""
        from . import camera_token as ct

        owner = str(owner or "").strip()
        ttl = max(1.0, float(ttl or 1.0))
        with self._cam_token_lock:
            now = time.time()
            tok = getattr(self, "_cam_token", None)
            if tok and ct.expired(tok, now):
                tok = None
            d = ct.decide(owner, tok, now, steal=steal)
            act = d["act"]
            if act == "refuse":
                return {"ok": False, "error": "camera_leased", "owner": d.get("holder"), "why": d.get("why")}
            lease_id = tok.get("lease_id") if (tok and act in ("renew", "handoff")) else None
            if act == "steal":
                try:
                    self._cam_post("/v1/pause", {"owner": owner, "ttl_sec": ttl, "reason": why or "steal", "steal": True}, timeout=2.0)
                except Exception as e:  # noqa: BLE001
                    return {"ok": False, "error": f"camera:{type(e).__name__}", "owner": d.get("holder")}
            try:
                lease = self._cam_post("/v1/lease", {"owner": owner, "ttl_sec": ttl, "lease_id": lease_id}, timeout=5.0)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"camera:{type(e).__name__}", "owner": d.get("holder")}
            if not lease.get("ok"):
                # the grabber knows a holder we do not (a tool pause, the inspector)
                ext = lease.get("owner")
                if steal and owner == "user_joystick":
                    try:
                        self._cam_post("/v1/pause", {"owner": owner, "ttl_sec": ttl, "reason": why or "steal", "steal": True}, timeout=2.0)
                        lease = self._cam_post("/v1/lease", {"owner": owner, "ttl_sec": ttl, "lease_id": None}, timeout=5.0)
                    except Exception:  # noqa: BLE001
                        lease = {}
                if not lease.get("ok"):
                    return {"ok": False, "error": "camera_leased", "owner": ext, "why": "external"}
                act = "steal"
            prev = tok.get("owner") if tok else None
            self._cam_token = {
                "owner": owner,
                "lease_id": lease.get("lease_id"),
                "until": now + ttl,
                "since": (tok.get("since") if (tok and prev == owner) else now),
                "why": why,
            }
            if act in ("grant", "handoff", "steal"):
                print(f"[cam-token] {prev or '-'} → {owner} why={why or act}", flush=True)
            return {"ok": True, "lease_id": lease.get("lease_id"), "act": act, "from": prev}

    def cam_renew(self, owner: str, ttl: float | None = None) -> dict:
        """Renew if ``owner`` still holds the token; else the loser's answer."""
        owner = str(owner or "").strip()
        holder = self.cam_holder()
        if holder != owner:
            return {"ok": False, "error": "lost", "owner": holder}
        tok = self._cam_token or {}
        t = max(1.0, float(ttl or (float(tok.get("until") or 0) - float(tok.get("since") or 0)) or 45.0))
        try:
            lease = self._cam_post("/v1/lease", {"owner": owner, "ttl_sec": t, "lease_id": tok.get("lease_id")}, timeout=2.0)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"camera:{type(e).__name__}", "owner": owner}
        if lease.get("ok"):
            with self._cam_token_lock:
                if self._cam_token and self._cam_token.get("owner") == owner:
                    self._cam_token["until"] = time.time() + t
        return {"ok": bool(lease.get("ok")), "lease_id": tok.get("lease_id"), "owner": owner}

    def cam_give_back(self, owner: str, *, home: bool = True, verify=None) -> dict:
        """Release the token if ``owner`` still holds it (a non-holder's give_back
        is a no-op) and, if asked, home the camera — never out from under a
        new holder."""
        from . import beam_look as bl

        owner = str(owner or "").strip()
        with self._cam_token_lock:
            holder = self.cam_holder()
            if holder != owner:
                return {"ok": True, "released": False, "owner": holder}
            lid = (self._cam_token or {}).get("lease_id")
            self._cam_token = None
        out: dict = {"ok": True, "released": True, "owner": None}
        try:
            if home:
                out["rest"] = bl.rest(lid, verify=verify) if verify is not None else bl.rest(lid)
            else:
                self._cam_post("/v1/lease/release", {"lease_id": lid}, timeout=8.0)
                self._cam_post("/v1/resume", {"force": True}, timeout=2.0)
        except Exception as e:  # noqa: BLE001
            out["error"] = f"{type(e).__name__}"
        print(f"[cam-token] {owner} → - why={'home' if home else 'release'}", flush=True)
        if out.get("released") and owner == getattr(self, "CAM_JOY_OWNER", "user_joystick"):
            dropped = self._end_joystick_secure()
            out["secure_dropped"] = bool(dropped)
            if dropped:
                out["note"] = self.CAM_NOTE_TEXT
                out["note_ttl_s"] = float(self.CAM_NOTE_TTL_S)
        return out
    # ---- Register's camera token (the User 2026-10-02, Init step 3) ----
    def _door_spared(self) -> set:
        """Holders the Secure door watch never pulls the camera from."""
        from . import camera_token as ct

        spared = {getattr(self, "CAM_JOY_OWNER", "user_joystick")}
        if ct.register_on():
            spared.add(ct.REGISTER)
        return spared

    @staticmethod
    def register_ttl() -> float | None:
        """Register's time limit, from config (config/register-token.json, or RAIL_REGISTER_CONFIG), never code.
        RAIL_REGISTER_TTL overrides. No config = no token (refused, says why)."""
        env = os.environ.get("RAIL_REGISTER_TTL", "").strip()
        if env:
            try:
                return max(1.0, float(env))
            except ValueError:
                return None
        cfg = Path(os.environ.get("RAIL_REGISTER_CONFIG") or _paths.CONFIG / "register-token.json")
        try:
            return max(1.0, float(json.loads(cfg.read_text())["ttl_sec"]))
        except Exception:  # noqa: BLE001
            return None

    def register_take(self, why: str = "register", visitor: str = "") -> dict:
        """Register takes the camera, only in User mode. Below the joystick, above Secure's door watch; never
        touches Secure or Hold. `visitor`: ADD A VISITOR's own take (_visitor_take_ok)."""
        from . import camera_token as ct

        if not ct.register_on():
            return {"ok": False, "error": "off", "why": "RAIL_REGISTER_TOKEN is off"}
        if not bool(getattr(self.get(), "user_auth", False)):
            # First boot (no User enrolled yet) has nobody to sign in as: its Register and Init sweep go (the User
            # 2026-10-03). Once a User exists, Register needs User auth first.
            from . import house_user

            if house_user.enrolled() and not self._visitor_take_ok(visitor):
                return {"ok": False, "error": "not_user_mode", "why": "Register needs User auth first."}
        ttl = self.register_ttl()
        if ttl is None:
            return {"ok": False, "error": "no_ttl", "why": "no register time limit in config/register-token.json"}
        out = self.cam_take(ct.REGISTER, ttl, why=why or "register")
        out["ttl"] = ttl
        return out

    @staticmethod
    def _visitor_take_ok(visitor: str) -> bool:
        """ADD A VISITOR (round 20, the User 2026-10-14): outside Security On, a visitor's own take borrows the camera
        without User mode when this install's player is adding exactly that person now (GET /v1/init `visitor`) - never
        the User's id. Kill: CRISSA_ADD_VISITOR=0. Any doubt is no."""
        pid = str(visitor or "").strip().lower()
        if not pid:
            return False
        try:
            from . import fixed_cam, house_user
            from . import ports as _ports
            from .enroll_panel import register_camera as rc

            if fixed_cam.is_house() or fixed_cam.security_mode() not in ("id_only", "off"):
                return False
            if pid == house_user.user_id():
                return False
            return rc.visitor_vouched(_ports.url("player", "/v1/init"), pid)
        except Exception:  # noqa: BLE001
            return False

    def register_give_back(self, verify=None) -> dict:
        """Give the token back: the camera goes home to the door. Secure and Hold stay exactly as they were.
        `verify` runs after the door rest and before the lease is released (beam_look.rest)."""
        from . import camera_token as ct

        if verify is not None:
            return self.cam_give_back(ct.REGISTER, home=True, verify=verify)
        return self.cam_give_back(ct.REGISTER, home=True)

    # ---- Register aim + renew + verified home (the User 2026-10-04, Init v2; RAIL_REGISTER_AIM, default off) ----
    _register_aim_lock = threading.Lock()

    def _register_cam_json(self, path: str, body: dict, timeout: float) -> dict:
        """The grabber call, keeping its JSON on an HTTP error (a 503 aim carries the readback)."""
        import urllib.error

        try:
            return self._cam_post(path, body, timeout=timeout)
        except urllib.error.HTTPError as e:
            try:
                out = json.loads(e.read().decode() or "{}")
            except Exception:  # noqa: BLE001
                out = {}
            out = out if isinstance(out, dict) else {}
            out.setdefault("ok", False)
            out.setdefault("error", f"http {e.code}")
            out["http_status"] = e.code
            return out

    @staticmethod
    def _ptz_read(name: str) -> int | None:
        """The camera's own position (v4l2 get-ctrl through ptz, as the joystick reads it). Never raises."""
        from . import ptz
        from .grabber import V4L_DEV

        try:
            v = ptz.get_ctrl(V4L_DEV, name)
            return int(v) if v is not None else None
        except Exception:  # noqa: BLE001
            return None

    def register_renew(self, why: str = "register") -> dict:
        """Same as take: the register owner taking again renews (camera_token.decide). Returns ttl."""
        from . import camera_token as ct

        if not ct.register_aim_on():
            return {"ok": False, "error": "off", "why": "RAIL_REGISTER_AIM is off"}
        out = self.register_take(why or "register")
        out["op"] = "renew"
        return out

    def register_aim(self, body: dict) -> dict:
        """Aim the camera under Register's token and bring back 1-3 frames. Does NOT home.

        body: {pan (units) | pan_deg, tilt_deg | tilt (units), zoom?, frames? (1-3)}. Refused unless Register holds
        the token. FaceFinder off for the token's lease, aim through rail-camera /v1/aim with the token's lease,
        then pan AND tilt are read back and must sit within one step of the command, else ok:false with the
        readback and no picture. Pan is clamped to the camera profile, never wrapped.
        """
        import base64

        from . import camera_profile as cp
        from . import camera_token as ct
        from . import room_sweep as rs
        from . import seat_shot

        if not ct.register_aim_on():
            return {"ok": False, "error": "off", "why": "RAIL_REGISTER_AIM is off"}
        body = body if isinstance(body, dict) else {}
        prof = cp.load()
        step = cp.step(prof)
        try:
            if body.get("pan_deg") is not None:
                pan_req = cp.deg_to_units(body["pan_deg"], prof)
            elif body.get("pan") is not None:
                pan_req = int(body["pan"])
            else:
                return {"ok": False, "error": "bad_request", "why": "pan or pan_deg is required"}
            if body.get("tilt_deg") is not None:
                tilt_req = cp.deg_to_units(body["tilt_deg"], prof)
            elif body.get("tilt") is not None:
                tilt_req = int(body["tilt"])
            else:
                tilt_req = 0
            zoom = int(body["zoom"]) if body.get("zoom") is not None else int(seat_shot.sweep_zoom())
            n = int(body.get("frames") if body.get("frames") is not None else 1)
        except (TypeError, ValueError, OverflowError) as e:
            return {"ok": False, "error": "bad_request", "why": str(e)[:120]}
        if not 1 <= n <= 3:
            return {"ok": False, "error": "bad_request", "why": "frames must be 1-3"}
        pan_cmd, pan_clamped = cp.clamp_pan(pan_req, prof)
        tilt_cmd = cp.clamp_tilt(tilt_req, prof)
        holder = self.cam_holder()
        if holder != ct.REGISTER:
            return {"ok": False, "error": "not_holder", "owner": holder,
                    "why": "Register does not hold the camera; take it first."}
        if not self._register_aim_lock.acquire(blocking=False):
            return {"ok": False, "error": "busy", "why": "another Register aim is in flight"}
        try:
            lid = (getattr(self, "_cam_token", None) or {}).get("lease_id")
            out: dict = {"ok": False, "op": "aim", "pan_cmd": pan_cmd, "pan_clamped": bool(pan_clamped),
                         "tilt_cmd": tilt_cmd, "zoom": zoom, "step": step,
                         "pan_actual": None, "tilt_actual": None, "images": [], "n_frames": 0}
            try:
                ff = self._register_cam_json("/v1/facefinder", {"on": False, "lease_id": lid}, timeout=5.0)
            except Exception as e:  # noqa: BLE001
                ff = {"ok": False, "error": type(e).__name__}
            out["ff_off"] = bool(ff.get("ok"))
            if not ff.get("ok"):
                out["error"] = "facefinder_off_failed"
                out["detail"] = str(ff.get("error") or "")[:120]
                return out
            try:
                aim = self._register_cam_json("/v1/aim", {"pan": pan_cmd, "tilt": tilt_cmd, "zoom": zoom,
                                                          "lease_id": lid}, timeout=25.0)
            except Exception as e:  # noqa: BLE001
                aim = {"ok": False, "error": type(e).__name__}
            pa = aim.get("pan_actual")
            ta = aim.get("tilt_actual")
            src = {"pan": "aim" if pa is not None else "v4l2", "tilt": "aim" if ta is not None else "v4l2"}
            if pa is None:
                pa = self._ptz_read("pan_absolute")
            if ta is None:
                ta = self._ptz_read("tilt_absolute")
            out.update(pan_actual=pa, tilt_actual=ta, readback=src)
            if not aim.get("ok"):
                out["error"] = "aim_refused" if aim.get("error") == "camera_leased" else "aim_failed"
                out["detail"] = str(aim.get("error") or "")[:160]
                return out
            # plain distance, never the wrap distance: +145 and -145 deg are different places
            pan_ok = pa is not None and abs(int(pa) - pan_cmd) <= step
            tilt_ok = ta is not None and abs(int(ta) - tilt_cmd) <= step
            out.update(pan_ok=pan_ok, tilt_ok=tilt_ok)
            if not (pan_ok and tilt_ok):
                out["error"] = "readback_mismatch"
                return out
            time.sleep(seat_shot.SETTLE_SEC)
            if self.cam_holder() != ct.REGISTER:          # the joystick took it mid-aim: not our picture
                out["error"] = "lost"
                out["owner"] = self.cam_holder()
                return out
            frames: list = []
            for i in range(n):
                try:
                    frames.append(rs._snapshot())
                except Exception as e:  # noqa: BLE001
                    out["snapshot_error"] = type(e).__name__
                    break
                if i + 1 < n:
                    time.sleep(0.5)
            out["images"] = [base64.b64encode(f).decode() for f in frames if f]
            out["n_frames"] = len(out["images"])
            out["ok"] = out["n_frames"] > 0
            if not out["ok"]:
                out["error"] = "no_frames"
            print(f"[register-aim] pan={pan_cmd}{' (clamped)' if pan_clamped else ''} tilt={tilt_cmd} zoom={zoom} "
                  f"actual pan={pa} tilt={ta} frames={out['n_frames']} ok={out['ok']}", flush=True)
            return out
        finally:
            self._register_aim_lock.release()

    def register_give_back_verified(self) -> dict:
        """give_back with (RAIL_REGISTER_AIM on) the camera's own pan and tilt read back against the door watch's own
        pose, room_heading.door_home_pose() - read AFTER the door rest and BEFORE the lease is released and the watchdogs
        resume (X-hi's review, 2026-10-04: read after the release, a watchdog's next move was what got checked). Best
        effort: the verification never raises and never stops the give-back."""
        from . import camera_token as ct

        if not ct.register_aim_on():
            return self.register_give_back()

        def _read_home() -> dict:
            from . import camera_profile as cp
            from .room_heading import door_home_pose

            want = door_home_pose()
            step = cp.step()
            pa = self._ptz_read("pan_absolute")
            ta = self._ptz_read("tilt_absolute")
            return {"pan_want": int(want["pan"]), "tilt_want": int(want["tilt"]),
                    "pan_actual": pa, "tilt_actual": ta, "read": "before_release",
                    "pan_ok": pa is not None and abs(int(pa) - int(want["pan"])) <= step,
                    "tilt_ok": ta is not None and abs(int(ta) - int(want["tilt"])) <= step}

        out = self.register_give_back(verify=_read_home)
        if not out.get("released"):
            out["home"] = {"skipped": "not_released"}
            return out
        rest = out.get("rest") if isinstance(out.get("rest"), dict) else {}
        if isinstance(rest.get("verify"), dict):
            out["home"] = rest["verify"]
        elif rest.get("skipped"):
            out["home"] = {"skipped": str(rest["skipped"])}
        else:
            out["home"] = {"error": "not_read"}
        return out

    CAM_TILT_MIN, CAM_TILT_MAX = -324000, 360000     # Insta360 Link 2, 3600 = 1°

    def camera_aim(self, op: str = "nudge", dx_deg: float = 0.0, dy_deg: float = 0.0) -> dict:
        from . import beam_look as bl
        from . import ptz
        from .grabber import V4L_DEV

        op = str(op or "nudge").lower()
        authed = bool(getattr(self.get(), "user_auth", False))
        # Already holding the token: the pan continues after the BAU grace
        # Guests you — AUTH gated the first nudge, the lease gates the rest.
        if not authed and self.cam_holder() == self.CAM_JOY_OWNER:
            authed = True
        if not authed:
            return {"ok": False, "error": "not_authed", "op": op,
                    "why": "Only the User aims the camera. Look at it first."}
        # Nudge keeps Secure (and AUTH) for the whole aim. Secure ends when
        # the token is given back (home / TTL) — not mid-pan. Hold never ends.
        if op == "home":
            out: dict = {"ok": True, "op": "home", "released": False}
            gb = self.cam_give_back(self.CAM_JOY_OWNER, home=False)
            out["released"] = bool(gb.get("released"))
            out["secure_dropped"] = bool(gb.get("secure_dropped"))
            if gb.get("note"):
                out["note"] = gb["note"]
                out["note_ttl_s"] = gb.get("note_ttl_s")
            self._cam_joy_lease = None
            if not gb.get("released"):
                try:
                    bl._cam_post("/v1/resume", {"force": True}, timeout=2.0)
                except Exception as e:  # noqa: BLE001
                    out["resume_error"] = type(e).__name__
            print("[camera-joy] home: token given back, watchdogs resumed", flush=True)
            return out
        if op != "nudge":
            return {"ok": False, "error": "op must be nudge|home"}
        # the token: the joystick outranks every presence-owned holder (a look,
        # the FF chase, the Secure glance); external holders are refused
        # Whole joystick session keeps AUTH; sage drops on give-back, not a
        # short TTL yank. Hold and Secure both use the long User hold.
        ttl = self.CAM_JOY_TTL
        tok = self.cam_take(self.CAM_JOY_OWNER, ttl, why="nudge")
        if not tok.get("ok"):
            return {"ok": False, "error": tok.get("error") or "camera_leased", "owner": tok.get("owner"),
                    "why": "Another tool has the camera; try again in a moment."}
        lid = tok.get("lease_id")
        self._cam_joy_lease = lid
        pan = ptz.get_ctrl(V4L_DEV, "pan_absolute")
        tilt = ptz.get_ctrl(V4L_DEV, "tilt_absolute")
        if pan is None:
            return {"ok": False, "error": "readback_failed", "lease_id": lid}
        new_pan = ptz.wrap_pan(int(pan) + int(round(float(dx_deg) * 3600)))
        new_tilt = max(self.CAM_TILT_MIN, min(self.CAM_TILT_MAX, int(tilt or 0) + int(round(float(dy_deg) * 3600))))
        try:
            aim = bl._cam_post("/v1/aim", {"pan": new_pan, "tilt": new_tilt, "lease_id": lid,
                                            "hold_sec": ttl}, timeout=25.0)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"aim:{type(e).__name__}", "lease_id": lid}
        pa = aim.get("pan_actual")
        out = {"ok": bool(aim.get("ok")), "op": "nudge", "lease_id": lid,
               "pan_deg": round(new_pan / 3600.0, 1), "tilt_deg": round(new_tilt / 3600.0, 1),
               "pan_actual_deg": (round(float(pa) / 3600.0, 1) if pa is not None else None),
               "at_door": aim.get("at_door"), "hold_sec": aim.get("hold_sec"),
               "secure": bool(getattr(self, "_visit_secure", False)),
               "held": bool(getattr(self, "_visit_hold", False)),
               "secure_dropped": False}
        if not aim.get("ok"):
            out["error"] = str(aim.get("error") or "aim_failed")
        # the camera's own readback beside the command: "no movement, maybe a
        # small skirmish bump" (the User 2026-09-06) needs the actual, not the ask
        print(f"[camera-joy] nudge dx={dx_deg} dy={dy_deg} → cmd pan={out['pan_deg']}° tilt={out['tilt_deg']}° "
              f"actual pan={out['pan_actual_deg']}° ok={out['ok']} at_door={out['at_door']} hold={out['hold_sec']} "
              f"err={out.get('error')}", flush=True)
        return out

    def visit_lamp_release(self) -> dict:
        """Drop the hold but keep Secure: back to the face-droppable flag."""
        with self._visit_secure_lock:
            was = bool(getattr(self, "_visit_hold", False))
            self._visit_hold = False
        try:
            self._set(self.get())
        except Exception:
            pass
        pub = self.visit_lamp_pub()
        pub.update({"ok": True, "held": False, "released": was})
        return pub

    def _visit_lamp_ack_now(self) -> dict:
        """Click found the User. Only path that mints Secure."""
        self._visit_secure_on(reason="click")
        pub = self.visit_lamp_pub()
        pub["ok"] = True
        pub["checked"] = True
        return pub

    def visit_lamp_click(self) -> dict:
        """Click on / click off.

        On: if AUTH is already held this process, mint Secure (no second
        look). Else FaceFinder / desk still. Visor lock is not a skip.
        Off (while Secure): immediate, no look. Busy gimbal is not rust.
        """
        silent = {"ok": False, "checked": False}
        no = None if getattr(self, "_visit_secure", False) else self._secure_not_offered()
        if no is not None:
            # round 17: a camera that cannot turn to the door is never put on Secure; AUTH and the lamp stay as they are
            print("[visit-lamp] click refused: %s" % no.get("why"), flush=True)
            pub = self.visit_lamp_pub()
            pub.update(ok=False, checked=False, error="secure_not_offered", why=no.get("why"), secure_offer=no)  # the lamp's word (lib/crissa_presence)
            return pub
        lock = getattr(self, "_visit_click_lock", None)
        if lock is None:
            self._visit_click_lock = threading.Lock()
            lock = self._visit_click_lock
        if not lock.acquire(blocking=False):
            print("[visit-lamp] click ignored — look already in flight", flush=True)
            return dict(silent)
        try:
            return self._visit_lamp_click_locked(silent)
        finally:
            lock.release()

    def _visit_lamp_click_locked(self, silent: dict) -> dict:
        from . import visit_lamp as vl
        from .stranger_x import record_event

        # Click on / click off. Off is immediate — no look, no proof.
        # A non-User click-off is fine; the next On still needs FF Auth.
        if getattr(self, "_visit_secure", False):
            self._visit_secure_off(reason="click")
            pub = self.visit_lamp_pub()
            pub["ok"] = True
            pub["checked"] = True
            pub["via"] = "toggle_off"
            return pub
        # AUTH already held: sage now. Do not wait out a FaceFinder lease —
        # measured 2026-09-09 06:41: click from FF (session look) sat in
        # blocked=leased for 6s, returned silent, lamp stayed amber.
        # Live AUTH this process is the proof. Boot does not restore it.
        try:
            if bool(getattr(self.get(), "user_auth", False)):
                print("[visit-lamp] click via=auth — already held, sage", flush=True)
                pub = self._visit_lamp_ack_now()
                pub["via"] = "auth"
                return pub
        except Exception:
            pass
        # Do NOT door-home first. Rest-then-snap was an empty JPEG
        # (names=[], sim=0, gold lamp) while FaceFinder was already on him.
        try:
            wait_s = max(0.0, float(os.environ.get("RAIL_LAMP_CLICK_WAIT_SEC") or "6"))
        except (TypeError, ValueError):
            wait_s = 6.0
        t_end = time.time() + wait_s
        while True:
            blocked = self._visit_camera_blocked(click=True)
            if not blocked:
                break
            if blocked in ("lights_out", "host_locked"):
                self._visit_cannot_see = True
                try:
                    self._set(self.get())
                except Exception:
                    pass
                print(f"[visit-lamp] click blocked={blocked}", flush=True)
                return dict(silent)
            # leased / room_busy: wait. Do not paint Faulty.
            if time.time() >= t_end:
                print(f"[visit-lamp] click deferred blocked={blocked}", flush=True)
                return dict(silent)
            time.sleep(0.2)
        uid = self._user_id()
        # AUTH may have landed while we waited for the gimbal (FF grant).
        try:
            if bool(getattr(self.get(), "user_auth", False)):
                print("[visit-lamp] click via=auth — already held, sage", flush=True)
                pub = self._visit_lamp_ack_now()
                pub["via"] = "auth"
                return pub
        except Exception:
            pass
        try:
            desk = self._desk_look_once(uid, force=True)
        except Exception:
            self._visit_cannot_see = True
            try:
                self._set(self.get())
            except Exception:
                pass
            return dict(silent)
        skipped = str(desk.get("skipped") or "")
        if skipped in ("pan_cooldown", "hunt_cooldown"):
            return dict(silent)
        if skipped in (
            "lights_out",
            "host_locked",
            "room_busy",
            "door_pending",
        ):
            self._visit_cannot_see = True
            try:
                self._set(self.get())
            except Exception:
                pass
            return dict(silent)
        if desk.get("found"):
            print(
                f"[visit-lamp] click found via={desk.get('via')} sim={desk.get('best_sim')} "
                f"wall_sec={desk.get('wall_sec')}",
                flush=True,
            )
            try:
                sim = float(desk.get("best_sim") or 0.0)
            except (TypeError, ValueError):
                sim = 0.0
            try:
                self.grant_user_face_auth(
                    sim=sim or None, source="visit_lamp_click", greet=False
                )
            except Exception:
                pass
            pub = self._visit_lamp_ack_now()
            pub["returned_door"] = bool(desk.get("returned_door"))
            return pub
        print(
            f"[visit-lamp] click miss skipped={skipped!r} via={desk.get('via')} "
            f"sim={desk.get('best_sim')} names={desk.get('names')} "
            f"n_faces={desk.get('n_faces')} wall_sec={desk.get('wall_sec')} "
            f"aim={desk.get('aim')}",
            flush=True,
        )
        try:
            if bool(getattr(self.get(), "user_auth", False)):
                print(
                    "[visit-lamp] click via=auth — desk empty, AUTH held, sage",
                    flush=True,
                )
                pub = self._visit_lamp_ack_now()
                pub["via"] = "auth"
                return pub
        except Exception:
            pass
        # Looked, not the User, and AUTH is not held. Silent. Hold Auth req failed.
        self._visit_cannot_see = False
        rec_out = None
        for u in list(getattr(self, "_last_desk_unmatched", None) or []):
            if not isinstance(u, dict):
                continue
            try:
                rec_out = record_event(
                    kind=vl.HOLD_AUTH_KIND,
                    jpeg=u.get("jpeg"),
                    crop=u.get("crop"),
                    emb=u.get("emb"),
                    bbox=u.get("bbox"),
                    sex=u.get("sex"),
                    source="visit_lamp",
                )
            except Exception:
                rec_out = None
            if rec_out and rec_out.get("ok"):
                break
        vl.note_hold_auth_failed(
            xid=(rec_out or {}).get("id"),
            display=(rec_out or {}).get("display"),
            photo=(rec_out or {}).get("photo"),
            source="visit_lamp",
        )
        try:
            self._set(self.get())
        except Exception:
            pass
        return dict(silent)

    def _visit_lamp_on_door_faces(self, seen: dict) -> None:
        """Belt-and-braces after FaceID: the tripwire already fired on
        DETECT. If the quality gate skipped a face that FaceID still
        named or cropped, that face ends Secure. Raw n_faces (hallway
        portraits, 20 px blobs) do not — that was the 5 s drop after
        click (door-glance faces=4 names=[])."""
        ok, _why = self._door_trip_allowed()
        if not ok:
            return
        named = list(((seen or {}).get("names") or {}).keys())
        unmatched = list((seen or {}).get("unmatched") or [])
        if named or unmatched:
            self._visit_secure_off(
                reason="entry_face_named" if named else "entry_face_unnamed"
            )

    def visit_lamp_scan(self) -> dict:
        """RETIRED 2026-09-05. Secure has no hourly expiry and no scan.
        Disable: systemctl --user disable --now visit-lamp-scan.timer
        Endpoint kept so the old timer, if still firing, is a harmless no-op.
        """
        return {"ok": True, "skipped": "retired", "looked": False, **self.visit_lamp_pub()}

    def _kick_unlock_face_grab(self) -> None:
        """Locked → unlocked: burst FaceID in a thread. No AUTH without a match.
        Secure / hold already is AUTH — do not FaceFinder (the User 2026-09-09)."""
        from .unlock_face import maybe_kick

        if self._visit_is_secure():
            return
        if not maybe_kick(False, True):
            return
        threading.Thread(
            target=self._unlock_face_grab,
            name="unlock-face-grab",
            daemon=True,
        ).start()

    def _unlock_face_grab(self) -> dict:
        from . import room_sweep as _rs
        from .unlock_face import capture_burst, write_burst

        uid = self._user_id()
        jpegs = capture_burst(snap=lambda: _rs._snapshot())
        sim = 0.0
        try:
            sim = float(self._match_user_in_jpegs(uid, jpegs, source="unlock_burst") or 0)
        except Exception:
            sim = 0.0
        thr = float(getattr(_rs, "MATCH_SIM", 0.60) or 0.60)
        match = bool(sim >= thr)
        granted = None
        if match and uid not in self._face_auth_hold:
            # Law 1: the burst is the record, the FaceFinder look is the
            # grant. Same seat look as the Secure click.
            try:
                desk = self._desk_look_once(uid, force=True, owner="unlock")
                if desk.get("found"):
                    grant = self.grant_user_face_auth(
                        sim=float(desk.get("best_sim") or sim),
                        source="unlock_ff",
                        greet=False,
                    )
                    granted = "face" if grant.get("auth") else None
                else:
                    match = False
            except Exception:
                granted = None
                match = False
        elif match:
            granted = "face"
        ev = write_burst(
            jpegs,
            uid=uid,
            sim=sim,
            match=bool(granted),
            granted_by=granted,
            reason="unlock",
        )
        return ev

    def grant_user_face_auth(
        self,
        *,
        sim: float | None = None,
        source: str = "face",
        greet: bool = True,
    ) -> dict:
        """AUTH-first grant for sudo User from face evidence — no inventory, no Oracle.

        Opens face sticky + session and republishes presence user_* fields.
        """
        from . import auth_first as af

        uid = self._user_id()
        t0 = time.time()
        # Law 1 (PR A, 2026-09-09): the explicit grant is not a way around the
        # FaceFinder gate. ``grant`` only vouches for an FF source; a ring /
        # FOV / door-still match may REFRESH a hold it finds, never mint one
        # (the door chase minted from Guest through here: review item 1).
        self._note_face_auth(uid, sim=sim, source=source, grant=af.is_ff_source(source))
        if not self._user_session:
            self._user_session = {
                "opened_ts": t0,
                "last_strong_voice_ts": None,
                "last_strong_voice_sim": None,
                "opened_via": source or "face",
            }
        # AUTH only. Do not mint occupancy — that put the user back on CP
        # after a real leave (2026-08-25 15:45:21).
        greet_out = None
        if greet:
            try:
                # Prefer emit captured when _note_face_auth opened sticky.
                # Consume cache so a second grant same-day reports no re-emit.
                cached = getattr(self, "_last_auth_greeting", None)
                if cached and cached.get("emitted"):
                    greet_out = cached
                    self._last_auth_greeting = {
                        **cached,
                        "emitted": False,
                        "reason": "already_today",
                    }
                else:
                    greet_out = self._maybe_first_auth_greeting(uid)
            except Exception as e:
                greet_out = {"emitted": False, "error": str(e)[:120]}
        cur = self.get()
        st = self._presence_from_inventory(
            frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
            camera_open=cur.camera_open,
            camera_blind=cur.camera_blind,
            last_event={
                "kind": "grant_user_face_auth",
                "source": source,
                "uid": uid,
                "sim": sim,
                "ts": t0,
                "oracle_used": False,
            },
            count_trust=cur.count_trust or "fact",
            camera_mode=cur.camera_mode or "holding",
            door_names=list(cur.door_names or []),
        )
        self._set(st)
        return {
            "ok": True,
            "auth": bool(st.user_auth),
            "user_auth": bool(st.user_auth),
            "user_auth_reason": st.user_auth_reason,
            "user_face_sticky": bool(st.user_face_sticky),
            "user_present": bool(st.user_present),
            "uid": uid,
            "source": source,
            "sim": sim,
            "greeting": greet_out,
            "oracle_used": False,
            "wall_sec": round(time.time() - t0, 3),
        }

    def _maybe_first_auth_greeting(self, uid: str | None = None) -> dict:
        from . import auth_first as af

        uid = (uid or self._user_id()).strip().lower()
        dry = os.environ.get("HOUSE_GOOD_MORNING_DRY", "0") in (
            "1",
            "true",
            "yes",
        )
        day_path = None
        env_path = os.environ.get("HOUSE_AUTH_DAY_FILE")
        if env_path:
            day_path = Path(env_path)
        out = af.maybe_good_morning(
            uid,
            day_path=day_path,
            speak=None if dry else af.default_speak_good_morning,
            dry_run=dry,
        )
        # Remember first emit of day so grant can report sticky-open greet.
        prev = getattr(self, "_last_auth_greeting", None)
        if out.get("emitted") or prev is None:
            self._last_auth_greeting = out
        return out

    def _auth_hunt_blocked_locked(self) -> bool:
        """True while the host is *known* locked. Unlocked hunt stays BAU."""
        from . import auth_first as af

        return af.host_lock_state() is False

    def chase_user_face_at_door(
        self,
        *,
        budget_sec: float | None = None,
        allow_door_ff: bool = True,
        jpegs: list[bytes] | None = None,
    ) -> dict:
        """Door-local User face chase (AUTH-first).

        Prefer matching faces in door FOV JPEGs (ring). Optional one firmware
        FF engage **at door pan only** — never multi-stop look_around.
        Always ends with return_door / door home.
        """
        uid = self._user_id()
        t0 = time.time()
        if self._auth_hunt_blocked_locked():
            return {
                "ok": True,
                "found": False,
                "uid": uid,
                "via": "locked",
                "best_sim": 0.0,
                "auth": False,
                "returned_door": False,
                "oracle_used": False,
                "reason": "host_locked",
                "stops": [],
                "wall_sec": 0.0,
            }
        budget = float(
            budget_sec
            if budget_sec is not None
            else os.environ.get("RAIL_AUTH_DOOR_CHASE_SEC", "5")
        )
        out: dict = {
            "ok": False,
            "found": False,
            "uid": uid,
            "via": None,
            "best_sim": 0.0,
            "auth": False,
            "returned_door": False,
            "oracle_used": False,
            "stops": [],
        }
        thr = float(os.environ.get("RAIL_PRESENCE_MATCH_SIM", "0.60"))
        try:
            # Ring / supplied door stills first (no pan)
            snaps: list[bytes] = list(jpegs or [])
            if not snaps:
                try:
                    from . import room_sweep as _rs

                    for _ in range(3):
                        try:
                            snaps.append(_rs._snapshot())
                        except Exception:
                            break
                        time.sleep(0.15)
                except Exception:
                    pass
            if snaps:
                sim = self._match_user_in_jpegs(
                    uid, snaps, source="door_chase_ring"
                )
                out["stops"].append(
                    {
                        "id": "door_ring",
                        "sim": round(sim, 3),
                        "credit": sim >= thr,
                        "elapsed_s": round(time.time() - t0, 3),
                    }
                )
                if sim >= thr:
                    out.update(ok=True, found=True, best_sim=sim, via="door_ring")
            # Optional one door-pan FF engage (not multi-stop). Hard budget:
            # skip if engage + settle + snaps would overrun remaining wall time
            # (Fable measured 6.4s empty doorway against ≤5s cap — 2026-08-12).
            # Prefer grabber door-local engage (1.8s) over room_sweep 2.5s.
            try:
                door_ff_engage = float(
                    os.environ.get("RAIL_AUTH_DOOR_FF_ENGAGE_SEC", "1.8")
                )
            except (TypeError, ValueError):
                door_ff_engage = 1.8
            try:
                settle = float(os.environ.get("RAIL_AUTH_DOOR_FF_SETTLE_SEC", "0.25"))
            except (TypeError, ValueError):
                settle = 0.25
            snap_budget = 0.35  # two snaps + small sleep headroom
            overhead = 0.35  # aim + facefinder on/off round-trips
            if (
                not out["found"]
                and allow_door_ff
            ):
                elapsed = time.time() - t0
                remaining = budget - elapsed
                # Need room for full engage path; if not, skip door_ff entirely
                min_cost = door_ff_engage + settle + snap_budget + overhead
                if remaining < min_cost:
                    out["stops"].append(
                        {
                            "id": "door_ff",
                            "skipped": True,
                            "reason": "budget",
                            "remaining_s": round(remaining, 3),
                            "need_s": round(min_cost, 3),
                            "elapsed_s": round(elapsed, 3),
                        }
                    )
                else:
                    try:
                        _dpose = _door_pose_now()
                        door_pan = _dpose["pan"]
                        from . import room_sweep as _rs

                        # Cap engage to leftover after settle/snaps/overhead
                        engage = min(
                            door_ff_engage,
                            max(0.4, remaining - settle - snap_budget - overhead),
                        )
                        _rs._post(
                            "/v1/aim",
                            {
                                "pan": door_pan,
                                "tilt": _dpose["tilt"],
                                "hold_sec": 0,
                            },
                            timeout=3.0,
                        )
                        if (time.time() - t0) >= budget:
                            out["stops"].append(
                                {
                                    "id": "door_ff",
                                    "skipped": True,
                                    "reason": "budget_after_aim",
                                    "elapsed_s": round(time.time() - t0, 3),
                                }
                            )
                        else:
                            # Re-cap engage after aim latency
                            rem2 = budget - (time.time() - t0)
                            engage = min(
                                engage,
                                max(0.3, rem2 - settle - snap_budget),
                            )
                            _rs._post(
                                "/v1/facefinder",
                                {"on": True, "engage_sec": engage},
                                timeout=5.0,
                            )
                            # Sleep only up to budget (hard deadline)
                            sleep_for = min(
                                engage + settle,
                                max(0.0, budget - (time.time() - t0) - snap_budget),
                            )
                            if sleep_for > 0:
                                time.sleep(sleep_for)
                            door_snaps: list[bytes] = []
                            for _ in range(2):
                                if (time.time() - t0) >= budget:
                                    break
                                try:
                                    door_snaps.append(_rs._snapshot())
                                except Exception:
                                    break
                                time.sleep(0.05)
                            try:
                                _rs._post(
                                    "/v1/facefinder", {"on": False}, timeout=3.0
                                )
                            except Exception:
                                pass
                            if door_snaps:
                                sim2 = self._match_user_in_jpegs(
                                    uid, door_snaps, source="door_chase_ff"
                                )
                                out["stops"].append(
                                    {
                                        "id": "door_ff",
                                        "sim": round(sim2, 3),
                                        "credit": sim2 >= thr,
                                        "engage_sec": round(engage, 3),
                                        "elapsed_s": round(time.time() - t0, 3),
                                    }
                                )
                                if sim2 >= thr:
                                    out.update(
                                        ok=True,
                                        found=True,
                                        best_sim=sim2,
                                        via="door_ff",
                                    )
                            else:
                                out["stops"].append(
                                    {
                                        "id": "door_ff",
                                        "sim": 0.0,
                                        "credit": False,
                                        "engage_sec": round(engage, 3),
                                        "elapsed_s": round(time.time() - t0, 3),
                                        "note": "no_snaps",
                                    }
                                )
                    except Exception as e:
                        out["ff_error"] = f"{type(e).__name__}: {e}"[:120]
            if out["found"]:
                via = str(out.get("via") or "")
                # the ledger's names: door_chase_ff is a FaceFinder look (Law 1
                # mints), door_chase_ring is three stills of the current FOV
                # (refreshes a held AUTH, never opens one from Guest)
                src = "door_chase_ff" if via == "door_ff" else "door_chase_ring"
                if via != "door_ff" and uid not in self._face_auth_hold:
                    out["auth"] = False
                    out["reason"] = "door_ring_needs_ff"
                    self._faceid_say(
                        "refuse guest_needs_ff", uid, src, out.get("best_sim"), rate_key=src
                    )
                else:
                    grant = self.grant_user_face_auth(
                        sim=out.get("best_sim"),
                        source=src,
                    )
                    out["auth"] = bool(grant.get("auth"))
                    out["grant"] = {
                        k: grant.get(k)
                        for k in ("user_auth", "user_auth_reason", "greeting", "uid")
                    }
        finally:
            # Door-hold law: always rest on door
            try:
                ret = self._ensure_door_aim(release_facelock=True)
                out["returned_door"] = bool(ret.get("ok") or ret.get("pan_cmd"))
                out["door_return"] = {
                    k: ret.get(k)
                    for k in ("pan_actual", "pan_cmd", "ok", "note")
                    if k in ret
                }
            except Exception as e:
                out["return_error"] = f"{type(e).__name__}: {e}"[:120]
            out["wall_sec"] = round(time.time() - t0, 3)
        return out

    def _run_reauth_ladder(self, uid: str) -> None:
        """Background face recovery from Guest: the User's voice is here, the
        hold is not. Law 1 — FaceFinder the seat, then FaceID. The same
        look the Secure click uses. No ring / current-FOV snapshots, no
        voice stamped as matched on the ladder's own say-so.
        """
        uid = (uid or "").strip().lower()
        if self._auth_hunt_blocked_locked():
            return
        if self._visit_is_secure():
            # Law 2: Secure pauses FF tracking; the camera stays on the door.
            return
        if getattr(self, "_guest_until_secure", False) and self._secure_not_offered() is None:
            # Restart / occupancy: do not mint AUTH without sage. The Secure
            # token path FaceFinds; a miss stays Guest until a click.
            # (Round 17: on a camera that cannot turn to the door there is no
            # Secure click to wait for, so the User's face ladder runs.)
            print("[faceid] ladder skip guest_until_secure", flush=True)
            return
        if uid in self._face_auth_hold:
            return
        try:
            desk = self._desk_look_once(uid, force=True, owner="reauth_ladder")
        except Exception as e:
            print(f"[faceid] ladder error {type(e).__name__}:{e}"[:160], flush=True)
            return
        if desk.get("found"):
            try:
                sim = float(desk.get("best_sim") or 0.0)
            except (TypeError, ValueError):
                sim = 0.0
            self.grant_user_face_auth(
                sim=sim or None, source="reauth_ladder_ff", greet=False
            )
            return
        print(
            f"[faceid] ladder miss {uid} via={desk.get('via')} "
            f"skipped={desk.get('skipped')!r} sim={desk.get('best_sim')} "
            f"n_faces={desk.get('n_faces')} wall_sec={desk.get('wall_sec')}",
            flush=True,
        )

    def _fresh_evidence_for(self, name: str) -> bool:
        """Ghost-recovery veto: True when the rail has recent NON-DOOR proof
        of this person — the janitor must not remove them (2026-08-04:
        another person's exit ghost-removed the User while he sat face-verified at 8)."""
        n = (name or "").strip().lower()
        now = time.time()
        hold = self._face_auth_hold.get(n)
        if hold and now - float(hold.get("ts") or 0) < 1800:
            return True
        if n == self._user_id():
            if self._user_session:
                return True
            lv = self._last_user_voice or {}
            if lv.get("match") and now - float(lv.get("ts") or 0) < 600:
                return True
        return False

    def seat_slots_view(self) -> dict:
        """seat_slots/v1 from live station_guess."""
        from . import seat_slots as _seats

        return _seats.from_station_guess(dict(self.inventory.station_guess or {}))

    def rescan_null_seats(self) -> dict:
        """Rescan room step: clear all seat allocations (not identity banks)."""
        from . import seat_slots as _seats

        before = dict(self.inventory.station_guess or {})
        self.inventory.station_guess.clear()
        slots = _seats.null_all()
        try:
            self._save_inventory_disk(force=True)
        except Exception:
            pass
        return {
            "ok": True,
            "action": "null_seats",
            "before": before,
            "station_guess": {},
            "slots": slots,
        }

    def pin_user_seat(self, user_id: str | None = None, seat: int | None = None) -> dict:
        """Re-pin primary User as full-frame desk seat reference after null."""
        from . import seat_slots as _seats

        uid = (user_id or _seats.primary_user()).strip().lower()
        h = int(seat if seat is not None else _seats.user_seat())
        # remove user from any seat then set
        for k in list(self.inventory.station_guess.keys()):
            if str(k).lower() == uid:
                self.inventory.station_guess.pop(k, None)
        # if exclusive seat taken by someone else, force (rescan context)
        for k, v in list(self.inventory.station_guess.items()):
            if int(v) == h and int(v) not in set(_seats.multi_ok_stations()):
                if str(k).lower() != uid:
                    self.inventory.station_guess.pop(k, None)
        self.inventory.station_guess[uid] = h
        slots = _seats.pin_user(
            _seats.from_station_guess(self.inventory.station_guess),
            user_id=uid,
            seat=h,
        )
        try:
            self._save_inventory_disk(force=True)
        except Exception:
            pass
        return {
            "ok": True,
            "action": "pin_user",
            "person_id": uid,
            "seat": h,
            "station_guess": dict(self.inventory.station_guess),
            "slots": slots,
            "note": "User is full-frame desk reference seat after room rescan",
        }

    def leave_package(self) -> dict[str, Any]:
        """Build leave_package/v1 from SoT only (CONTRACT-CLOTHING-LEAVE)."""
        inv = self.inventory
        # Drop orphan unnamed keys (stills left after count went to named-only)
        if inv.unnamed <= 0 and inv.unnamed_keys:
            for uk in list(inv.unnamed_keys):
                inv.traits_live.pop(uk, None)
                self._prune_enter_stills(uk)
            inv.unnamed_keys.clear()
        elif len(inv.unnamed_keys) > inv.unnamed:
            # keep newest keys matching count
            drop = inv.unnamed_keys[: len(inv.unnamed_keys) - inv.unnamed]
            inv.unnamed_keys = inv.unnamed_keys[len(drop) :]
            for uk in drop:
                inv.traits_live.pop(uk, None)
                self._prune_enter_stills(uk)
        occupants: list[dict[str, Any]] = []
        # Threshold Game: ARRIVAL order — guest1 is the first arrival. The
        # SAME roster resolves leave_guest, so numbers can never skew.
        for i, key in enumerate(inv.guest_roster()[:LEAVE_PACKAGE_MAX], start=1):
            display = key if not key.startswith("__u") else (key.replace("__", "") or "U")
            pkg = self._occupant_pkg(key, display=display)
            pkg["guest"] = i
            occupants.append(pkg)
        hc = inv.headcount()
        return {
            "schema": "leave_package/v1",
            "ts": time.time(),
            "headcount": hc,
            "sole": hc == 1,
            "occupants": occupants,
            "caps": {
                "max_occupants": LEAVE_PACKAGE_MAX,
                "max_stills_per_person": ENTER_STILL_MAX,
            },
            "note": (
                "Compare EXIT tape to enter stills + traits; "
                "pick person_id only from this list"
            ),
        }

    def _occupant_pkg(self, key: str, *, display: str) -> dict[str, Any]:
        traits = dict(self.inventory.traits_live.get(key) or {})
        still = self.inventory.enter_still.get(key) or {}
        n_still = int(still.get("count") or len(still.get("paths") or []))
        pid = key if not key.startswith("__u") else key
        urls = [
            f"/v1/enter_still/{pid}/{i}.jpg" for i in range(n_still)
        ]
        certain = bool(self._id_certain.get(key, key in self.inventory.present and not key.startswith("__")))
        return {
            "person_id": pid,
            "display": display if not key.startswith("__u") else f"U{key[3:]}" if key.startswith("__u") else display,
            "authed": certain and not key.startswith("__u"),
            "traits_live": traits or None,
            "enter_still_urls": urls,
            "enter_still_count": n_still,
        }

    def apply_soft_id(
        self,
        person_id: str,
        *,
        certain: bool = False,
        source: str = "voice",
        confirm: bool = False,
        create_if_missing: bool = True,
    ) -> RoomPresence:
        """Soft ID on SoT: provisional name? or promote confirm (no facefinder)."""
        pid = (person_id or "").strip().lower()
        if not pid:
            return self.get()
        from .guests import is_guest

        now = time.time()
        if is_guest(pid):
            ev = self.inventory.note_visitor(pid, now=now, source=source)
            if ev:
                self._visitor_day_log(pid)
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=cur.camera_open,
                camera_blind=cur.camera_blind,
                last_event={
                    "kind": "visitor",
                    "source": source,
                    "person_id": pid,
                    "ts": now,
                },
                count_trust=cur.count_trust or "stale",
                camera_mode=self._mode if self._mode != "reconciling" else "holding",
                door_names=list(cur.door_names or []),
            )
            self._set(st)
            return st
        if pid not in self.inventory.present:
            if not create_if_missing:
                return self.get()
            # Prefer re-id of an unnamed slot (U1) over inventing a 4th body.
            if self.inventory.unnamed > 0:
                # drop one unnamed slot key if present
                if getattr(self.inventory, "unnamed_keys", None):
                    uk = self.inventory.unnamed_keys[-1]
                    try:
                        self.inventory._drop_unnamed_key(
                            uk, now=now, source=f"soft_id:{source}"
                        )
                    except Exception:
                        self.inventory.unnamed = max(0, int(self.inventory.unnamed) - 1)
                else:
                    self.inventory.unnamed = max(0, int(self.inventory.unnamed) - 1)
                self.inventory.present[pid] = now
                self.inventory.entry_grace_until[pid] = now + ENTRY_GRACE
                self.inventory.events.append(
                    {
                        "kind": "enter",
                        "name": pid,
                        "ts": now,
                        "source": f"soft_id:{source}",
                        "note": "unnamed_to_named",
                    }
                )
            elif self.inventory.headcount() == 0:
                # empty room soft stamp — still only if create allowed
                self.inventory.present[pid] = now
                self.inventory.entry_grace_until[pid] = now + ENTRY_GRACE
                self.inventory.events.append(
                    {
                        "kind": "enter",
                        "name": pid,
                        "ts": now,
                        "source": f"soft_id:{source}",
                    }
                )
            else:
                # all bodies already named — sticky touch only (no +1)
                self.inventory.mark_sticky([pid], source=source)
        else:
            self.inventory.present[pid] = now
        if confirm or certain:
            self._id_certain[pid] = True
        else:
            self._id_certain.setdefault(pid, False)
            if not certain:
                self._id_certain[pid] = False
        src = dict(self._id_sources.get(pid) or {})
        src[source] = True
        if confirm:
            src["confirm_mention"] = True
        self._id_sources[pid] = src
        # House User: a certain soft_id (voice, Oracle) used to open face
        # sticky (the User 2026-08-12 walk-in). Law 1 (2026-09-06): it refreshes
        # a FaceFinder hold, never mints one — the voice-here-face-not
        # ladder goes and gets the face. Session and greeting follow the hold.
        if (confirm or certain) and pid == self._user_id():
            try:
                self._note_face_auth(pid, sim=None, source=f"soft_id:{source}")
            except Exception:
                pass
            if pid in self._face_auth_hold:
                if not self._user_session:
                    self._user_session = {
                        "opened_ts": now,
                        "last_strong_voice_ts": now if "voice" in str(source) else None,
                        "last_strong_voice_sim": None,
                        "opened_via": f"soft_id:{source}",
                    }
                # Session may open after sticky already existed — still greet once/day.
                try:
                    self._maybe_first_auth_greeting(pid)
                except Exception:
                    pass
        cur = self.get()
        st = self._presence_from_inventory(
            frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
            camera_open=cur.camera_open,
            camera_blind=cur.camera_blind,
            last_event={
                "kind": "soft_id",
                "source": source,
                "person_id": pid,
                "certain": bool(self._id_certain.get(pid)),
                "confirm": confirm,
                "ts": now,
            },
            count_trust=cur.count_trust or "stale",
            camera_mode=self._mode if self._mode != "reconciling" else "holding",
            door_names=list(cur.door_names or []),
        )
        self._set(st)
        self.cycles += 1
        self.last_cycle_at = now
        # soft promote / re-id → Inspector follow-up (shadow)
        if confirm or certain:
            self._inspector_auto(st, trigger="soft_id", person_id_hint=pid)
        return st

    def code_write_through(
        self,
        names: list[str],
        *,
        source: str = "code",
        sims: dict[str, float] | None = None,
        live_face: dict[str, bool] | None = None,
    ) -> RoomPresence | None:
        """CODE → SoT: refresh present people; re-add missing only on *live* door face.

        Never resurrect someone who just left (tombstone) unless live face again.
        Pure room_memory sticky without this-strip face must not re-add leavers
        (the User 2026-07-27 another person resurrect).
        """
        sims = sims or {}
        live_face = live_face or {}
        now = time.time()
        clean: list[str] = []
        blocked: list[str] = []
        from .guests import is_guest

        for n in names:
            n = (n or "").strip().lower()
            if not n or n.startswith("unknown"):
                continue
            if is_guest(n):
                ev = self.inventory.note_visitor(n, now=now, source=source)
                if ev:
                    self._visitor_day_log(n)
                continue
            sim = float(sims.get(n) or 0)
            on_sot = n in self.inventory.present
            live = bool(live_face.get(n)) or (
                # treat strong sim as live only if caller marked live_face;
                # default: sim alone is not enough for *new* add
                False
            )
            if on_sot:
                # already on SoT — refresh last_seen only
                clean.append(n)
                continue
            # --- missing from SoT: resurrection rules ---
            # Floor shared with RAIL_PRESENCE_MATCH_SIM (drop-in face-match-floor).
            # the User 2026-08-10: hardcoded 0.50 accepted another person@0.54 while only the User
            # present (portrait / weak cross-person).
            _add_sim = float(os.environ.get("RAIL_PRESENCE_MATCH_SIM", "0.60"))
            if self._blocked_from_resurrect(n, now=now):
                if not (live and sim >= _add_sim):
                    blocked.append(n)
                    continue
            # new add requires live door face + solid sim (cut poster ghosts;
            # real door face for the user measured ~0.57–0.70 on 2026-07-27 walks)
            if live and sim >= _add_sim:
                clean.append(n)
            # else: memory-only sticky — do NOT invent a body on SoT
        if not clean:
            if blocked:
                cur = self.get()
                st = self._presence_from_inventory(
                    frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                    camera_open=cur.camera_open,
                    camera_blind=cur.camera_blind,
                    last_event={
                        "kind": "code_write_blocked",
                        "source": source,
                        "blocked": blocked,
                        "reason": "recent_leave_tombstone",
                        "block_sec": CODE_RESURRECT_BLOCK_SEC,
                        "headcount": self.inventory.headcount(),
                        "ts": now,
                    },
                    count_trust=cur.count_trust or "fact",
                    camera_mode=self._mode if self._mode != "reconciling" else "holding",
                    door_names=list(cur.door_names or []),
                )
                self._set(st)
            return None
        missing = [n for n in clean if n not in self.inventory.present]

        def _absorb_unnamed_for_live(live_names: list[str]) -> int:
            """Eat same-transit U slots when a live face already accounts for
            the body — gated by the §2.1 same-body proof (presence-perfect
            §2.2): inventory.absorbable_u decides; a settled guest or a
            legacy keyless U is never eaten (fail toward overcount)."""
            if self.inventory.unnamed <= 0:
                return 0
            n_live = sum(1 for n in live_names if live_face.get(n))
            if n_live <= 0:
                # named present after VL add still collapses one U per named refresh
                n_live = len(live_names) if live_names else 0
            limit = min(self.inventory.unnamed, max(n_live, 1) if live_names else 0)
            target = next((n for n in live_names if live_face.get(n)), None) or (
                live_names[0] if live_names else None
            )
            absorbed = 0
            win = VL_DEDUP_SEC if VL_DEDUP_SEC > 0 else 12.0
            for _ in range(limit):
                uk = self.inventory.absorbable_u(now, window_sec=win)
                if uk is None or not target:
                    break
                self.inventory._drop_unnamed_key(
                    uk, now=now, source=source, kind="u_absorbed",
                    reason="live_face", transfer_to=target)
                absorbed += 1
            return absorbed

        if not missing and all(self._id_certain.get(n) for n in clean):
            # still refresh last_seen for present names
            for n in clean:
                if n in self.inventory.present:
                    self.inventory.present[n] = now
                    if live_face.get(n):
                        self.inventory.seen_at_door.add(n)
                        self.inventory.last_door_seen[n] = now
            # U1 stickiness: the user certain + VL unnamed still on inventory
            absorbed = _absorb_unnamed_for_live(clean)
            if absorbed:
                cur = self.get()
                st = self._presence_from_inventory(
                    frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                    camera_open=cur.camera_open,
                    camera_blind=cur.camera_blind,
                    last_event={
                        "kind": "code_absorb_unnamed",
                        "source": source,
                        "absorbed": absorbed,
                        "names": clean,
                        "headcount": self.inventory.headcount(),
                        "ts": now,
                    },
                    count_trust=cur.count_trust or "fact",
                    camera_mode=self._mode if self._mode != "reconciling" else "holding",
                    door_names=list(cur.door_names or []),
                )
                self._set(st)
                self.cycles += 1
                self.last_cycle_at = now
                return st
            return None
        added: list[str] = []
        for n in clean:
            sim = float(sims.get(n) or 0)
            if n not in self.inventory.present:
                self.inventory.present[n] = now
                self.inventory.entry_grace_until[n] = now + ENTRY_GRACE
                self.inventory.sticky.discard(n)  # live re-enter, not immortal sticky
                if live_face.get(n):
                    self.inventory.seen_at_door.add(n)
                    self.inventory.last_door_seen[n] = now
                # Prefer absorbing VL body (traits/stills) over bare re-add —
                # gated by the §2.1 same-body proof (presence-perfect §2.2)
                uk_abs = self.inventory.absorbable_u(
                    now, window_sec=VL_DEDUP_SEC if VL_DEDUP_SEC > 0 else 12.0)
                if uk_abs is not None:
                    self.inventory._drop_unnamed_key(
                        uk_abs, now=now, source=source, kind="u_absorbed",
                        reason="same_transit_code", transfer_to=n)
                self.inventory.events.append(
                    {
                        "kind": "enter",
                        "name": n,
                        "ts": now,
                        "source": source,
                        "sim": sim or None,
                        "live_face": bool(live_face.get(n)),
                        "traits_live": dict(self.inventory.traits_live.get(n) or {})
                        or None,
                    }
                )
                added.append(n)
                # clear tombstone on real re-enter
                self._left_block_until.pop(n, None)
            else:
                self.inventory.present[n] = now
                if live_face.get(n):
                    self.inventory.seen_at_door.add(n)
                    self.inventory.last_door_seen[n] = now
                # already present: never strip traits_live / enter_still (L11 wipe)
            # Certain only on a solid face hit. Weak matches stay provisional
            # so another person≠the user @0.54 can't lock (the User 2026-08-10).
            _certain_sim = float(os.environ.get("RAIL_PRESENCE_CERTAIN_SIM", "0.65"))
            _match_sim = float(os.environ.get("RAIL_PRESENCE_MATCH_SIM", "0.60"))
            if sim >= _certain_sim or live_face.get(n):
                self._id_certain[n] = True
            elif sim >= _match_sim:
                self._id_certain[n] = False
            else:
                self._id_certain.setdefault(n, False)
            src = dict(self._id_sources.get(n) or {})
            src["code"] = True
            if sim:
                src["face_sim"] = sim
            if live_face.get(n):
                src["live_face"] = True
            self._id_sources[n] = src
        if not added and not missing:
            # still absorb VL phantom unknowns when face just stamped a body
            absorbed = 0
            if self.inventory.unnamed > 0 and any(live_face.get(n) for n in clean):
                absorbed = _absorb_unnamed_for_live(clean)
            if absorbed:
                cur = self.get()
                st = self._presence_from_inventory(
                    frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                    camera_open=cur.camera_open,
                    camera_blind=cur.camera_blind,
                    last_event={
                        "kind": "code_absorb_unnamed",
                        "source": source,
                        "absorbed": absorbed,
                        "names": clean,
                        "headcount": self.inventory.headcount(),
                        "ts": now,
                    },
                    count_trust=cur.count_trust or "fact",
                    camera_mode=self._mode if self._mode != "reconciling" else "holding",
                    door_names=list(cur.door_names or []),
                )
                self._set(st)
                self.cycles += 1
                self.last_cycle_at = now
                return st
            return None
        self.inventory.baseline_set = True
        # Face stamp eats remaining same-transit U slots — §2.1-gated
        if self.inventory.unnamed > 0 and added:
            win = VL_DEDUP_SEC if VL_DEDUP_SEC > 0 else 12.0
            for _ in range(min(self.inventory.unnamed, len(added))):
                uk = self.inventory.absorbable_u(now, window_sec=win)
                if uk is None:
                    break
                self.inventory._drop_unnamed_key(
                    uk, now=now, source=source, kind="u_absorbed",
                    reason="same_transit_code", transfer_to=added[0])
        cur = self.get()
        st = self._presence_from_inventory(
            frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
            camera_open=cur.camera_open,
            camera_blind=cur.camera_blind,
            last_event={
                "kind": "code_write_through",
                "source": source,
                "names": clean,
                "missing_added": added,
                "blocked": blocked,
                "sims": {n: sims.get(n) for n in clean if n in sims},
                "headcount": self.inventory.headcount(),
                "ts": now,
            },
            count_trust="fact" if self.inventory.baseline_set else "stale",
            camera_mode=self._mode if self._mode != "reconciling" else "holding",
            door_names=list(cur.door_names or []),
        )
        st.count_certain = st.unidentified == 0 and all(
            p.certain for p in st.people if p.name and not str(p.name).startswith(("U", "F", "M"))
        )
        self._set(st)
        self.cycles += 1
        self.last_cycle_at = now
        return st

    def _grabber_post(self, url: str, body: dict, timeout: float = 30.0) -> dict:
        if _ports.camera_off():                  # no camera: a no-op that says so (2026-10-10)
            _ports.say_once("camera calls are no-ops")
            return dict(_ports.OFF)
        try:
            from .camera_call import post as camera_post

            owner = "presence"
            u = str(url or "")
            if "look_around" in u or "facefind" in u or "facelock" in u:
                owner = "auth"
            return camera_post(url, body, owner=owner, timeout=timeout)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}

    def release_facefinder(self) -> dict:
        """Release firmware AI face-lock so manual pan / multi-stop can own the gimbal."""
        return self._grabber_post(
            GRABBER_FACELOCK_URL, {"enable": False, "action": "release"}, timeout=5.0
        )

    def _surprise_from_media(self, flagged: bool | None = None) -> bool:
        """TV/podcast is not a body in the room. Do not slew for it.

        Hour 8 is a screen *face* (occupancy credit), not this skip. Media
        skip is the loudspeaker: explicit flag or beam on media_station (7).
        """
        if flagged:
            return True
        pub = self._sound_pub() or {}
        try:
            from .seat_slots import media_station

            ms = media_station()
        except Exception:
            ms = None
        if ms is None:
            return False
        for key in ("beam_hour", "hour", "station"):
            try:
                h = int(pub.get(key))
            except (TypeError, ValueError):
                continue
            if h == int(ms):
                return True
        return False

    def _seat_face_may_add_occupancy(self, stop_name: str, station: int | None) -> bool:
        """Named-face credits for empty-room surprise add.

        Not the door (off-angle plane). Not media. Hour 8 is a screen in this
        FOV (the User 2026-08-26) — a face there is not a person.
        """
        sid = str(stop_name or "").strip().lower()
        if sid in ("door", "door_home") or "media" in sid:
            return False
        try:
            h = int(station) if station is not None else None
        except (TypeError, ValueError):
            h = None
        if h == 8:
            return False
        try:
            from .seat_slots import media_station

            ms = media_station()
            if ms is not None and h == int(ms):
                return False
        except Exception:
            pass
        return True

    def enqueue_surprise(
        self,
        *,
        person_id: str | None = None,
        source: str = "voice_while_empty",
        force: bool = False,
        mode: str | None = None,
        media: bool | None = None,
    ) -> dict:
        """Queue a surprise. No cooldown. Flood is not Busy."""
        job = {
            "person_id": person_id,
            "source": source,
            "force": force,
            "mode": mode,
            "media": media,
        }
        flood = False
        with self._surprise_lock:
            self._surprise_q.append(job)
            while len(self._surprise_q) > 8:
                self._surprise_q.popleft()
            pending = len(self._surprise_q)
            if pending >= SURPRISE_QUEUE_MAX:
                flood = True
            start = not self._surprise_busy
            if start:
                self._surprise_busy = True
        if start:
            threading.Thread(
                target=self._drain_surprise,
                name="surprise-q",
                daemon=True,
            ).start()
        return {
            "ok": True,
            "queued": True,
            "async": True,
            "pending": pending,
            "flood": flood,
            "person_id": person_id,
            "source": source,
            "mode": mode or "body_count",
        }

    def _drain_surprise(self) -> None:
        while True:
            with self._surprise_lock:
                if not self._surprise_q:
                    self._surprise_busy = False
                    return
                job = self._surprise_q.popleft()
            try:
                self.surprise_from_voice(**job)
            except Exception:
                pass

    def surprise_from_voice(
        self,
        *,
        person_id: str | None = None,
        source: str = "voice_while_empty",
        force: bool = False,
        mode: str | None = None,
        media: bool | None = None,
    ) -> RoomPresence:
        """Voice evidence vs SoT — two modes (the User 2026-08-11 / 2026-08-26).

        **body_count** (default): any in-room voice while occupancy is 0 and
        not media → seat FF scan and union **named** faces at real seats.
        Never unnamed. Never media/hour-8 screen faces. Voice id is not the
        count (misID / off-angle do not add).

        **user_auth**: facefinder every stop until User face is recorded.
        """
        now = time.time()
        mode_l = (mode or "body_count").strip().lower()
        if mode_l in ("auth", "ensure_auth", "ensure_user_auth", "user", "ff"):
            mode_l = "user_auth"
        if mode_l in (
            "both",
            "body_then_auth",
            "count_then_auth",
            "body_count+user_auth",
        ):
            mode_l = "both"
        if mode_l not in ("body_count", "user_auth", "both"):
            mode_l = "body_count"

        pid = (person_id or "").strip().lower() or None
        uid = self._user_id()

        def _already_authed() -> bool:
            if not uid:
                return False
            hold = self._face_auth_hold.get(uid)
            try:
                present = uid in set(self.inventory.present or [])
            except Exception:
                present = False
            return bool(
                (hold or self._user_session)
                and (present or bool(getattr(self.get(), "user_auth", False)))
            )

        # TV/podcast: never slew. Does not burn a cooldown (there is none).
        if mode_l in ("body_count", "both") and self._surprise_from_media(media):
            st = self.get()
            st.last_event = {
                **(st.last_event or {}),
                "kind": "surprise_skipped",
                "reason": "media",
                "mode": mode_l,
                "person_id": pid,
                "ts": now,
            }
            self._set(st)
            return st

        # both = empty-room add, then AUTH hunt only if still not authed.
        # Occupancy 0 + sticky AUTH must still scan-to-add (already_authed
        # used to skip the whole call).
        if mode_l == "both":
            st = self.surprise_from_voice(
                person_id=pid,
                source=source,
                force=force,
                mode="body_count",
                media=media,
            )
            body_ev = dict(st.last_event or {})
            skip_auth = body_ev.get("kind") == "surprise_skipped" or _already_authed()
            if skip_auth:
                st.last_event = {
                    **body_ev,
                    "prior_body_count": body_ev,
                    "mode": "both",
                    "auth_skipped": (
                        str(body_ev.get("reason") or "already_authed")
                        if body_ev.get("kind") == "surprise_skipped"
                        else "already_authed"
                    ),
                }
                self._set(st)
                return st
            st2 = self.surprise_from_voice(
                person_id=pid or uid,
                source=f"{source}:user_auth",
                force=True,
                mode="user_auth",
                media=media,
            )
            st2.last_event = {
                **(st2.last_event or {}),
                "prior_body_count": (st.last_event or {}),
                "mode": "both",
            }
            self._set(st2)
            return st2

        # --- Mode 2: USER auth — facefinder every patrol stop until User ---
        if mode_l == "user_auth":
            # Sitting + face-authed: do not steal the door for a 90s
            # parade. Empty-room body_count is not this skip.
            if not force and _already_authed():
                st = self.get()
                st.last_event = {
                    **(st.last_event or {}),
                    "kind": "surprise_skipped",
                    "reason": "already_authed",
                    "mode": mode_l,
                    "person_id": pid or uid,
                    "ts": now,
                }
                self._set(st)
                return st
            if self._auth_hunt_blocked_locked():
                st = self.get()
                st.last_event = {
                    **(st.last_event or {}),
                    "kind": "surprise_skipped",
                    "reason": "host_locked",
                    "mode": mode_l,
                    "person_id": pid or uid,
                    "ts": now,
                }
                self._set(st)
                return st
            hunt = self.ensure_user_auth(
                person_id=pid or uid,
                voice_matched=bool(pid == uid or not pid),
                voice_sim=None,
                reason=f"surprise:{source}",
                force_facefind=True,
            )
            st = self.get()
            st.last_event = {
                **(st.last_event or {}),
                "kind": "surprise_user_auth",
                "mode": "user_auth",
                "source": source,
                "person_id": pid or uid,
                "auth": hunt.get("auth"),
                "via": hunt.get("via"),
                "reason": hunt.get("reason"),
                "hunt": {
                    k: hunt.get(k)
                    for k in ("found", "best_sim", "via", "wall_sec", "stops")
                    if isinstance(hunt, dict)
                },
                "ts": time.time(),
            }
            self._set(st)
            try:
                self._save_inventory_disk(force=True)
            except Exception:
                pass
            return st

        # --- Mode 1: empty-room any-voice scan (voice id is not the count) ---
        try:
            pre_hc = int(self.inventory.headcount())
        except Exception:
            pre_hc = 0
        if int(pre_hc) != 0:
            st = self.get()
            st.last_event = {
                **(st.last_event or {}),
                "kind": "surprise_skipped",
                "reason": "not_empty",
                "mode": "body_count",
                "person_id": pid,
                "pre_headcount": pre_hc,
                "ts": now,
            }
            self._set(st)
            return st

        if self._door_work_pending():
            st = self.get()
            st.last_event = {
                **(st.last_event or {}),
                "kind": "surprise_skipped",
                "reason": "door_pending",
                "mode": "body_count",
                "person_id": pid,
                "ts": now,
            }
            self._set(st)
            return st

        facelock = self.release_facefinder()

        try:
            # Occupancy 0 + any in-room voice, not media → FF every seat,
            # union named faces at real seats. Never unnamed. Never door /
            # media / hour-8 screen. MisID and off-angle do not add.
            #
            # the User 2026-08-16: FF at every seat, not first-found-face.
            tour = self._reauth_facefind_all_stations(uid)
            names_found = sorted((tour.get("found_people") or {}).keys())
            try:
                thr = float(os.environ.get("RAIL_PRESENCE_MATCH_SIM", "0.60"))
            except (TypeError, ValueError):
                thr = 0.60
            seat_names = sorted(
                n
                for n, s in (tour.get("found_at_seats") or {}).items()
                if float(s) >= thr
            )
            added: list[str] = []
            reason = "room_check_no_count"
            if int(pre_hc) == 0 and seat_names:
                ev = self.inventory.apply_reconcile(seat_names, 0, mode="union")
                added = list(ev.get("entered") or seat_names)
                reason = "empty_voice_scan_add"
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=getattr(cur, "camera_open", True),
                camera_blind=getattr(cur, "camera_blind", False),
                last_event={
                    "kind": "surprise_body_count",
                    "mode": "body_count",
                    "source": source,
                    "reason": reason,
                    "person_id_hint": pid,
                    "pre_headcount": pre_hc,
                    "post_headcount": int(self.inventory.headcount() or 0),
                    "names_found": names_found,
                    "seat_names": seat_names,
                    "added": added,
                    "facelock": facelock,
                    "note": (
                        "empty-room any-voice, not media: union named seat faces"
                        if reason == "empty_voice_scan_add"
                        else "room check only; no occupancy increment"
                    ),
                    "ts": time.time(),
                },
                count_trust=getattr(cur, "count_trust", None) or "fact",
                camera_mode=getattr(cur, "camera_mode", None) or "holding",
                door_names=list(getattr(cur, "door_names", None) or []),
            )
            self._set(st)
            try:
                self._save_inventory_disk(force=True)
            except Exception:
                pass
            self._sync_oracle_hand(reason=f"surprise_body_count:{source}")
            return st
        finally:
            self._ensure_door_aim(release_facelock=True)

    def reconcile_after_facefind(self, *, reason: str = "post_facefind") -> dict:
        """the User doctrine: after ANY facefinder, close sneak-in window with the
        facefinder seat routine (not continuous smooth_scan patrol).

        Per stop: release → aim → firmware FF ON → engage (2.5s) → capture →
        FF OFF → door home between seats. Always end door 450000, FF OFF.
        Soft ID only from stop frames (does not invent headcount).

        Replaced smooth_scan two-way (door→left→door→right continuous pan)
        2026-08-07 — that patrol looked wrong; look_around is the built routine.
        """
        self.release_facefinder()
        t0 = time.time()
        look = self._grabber_post(
            GRABBER_LOOK_AROUND_URL,
            {
                "facefinder": FACEFINDER_ALLOWED,
                "engage_sec": FACEFINDER_ENGAGE_SEC,
                "include_jpeg": False,
            },
            timeout=180.0,
        )
        # Force facefinder off + door home even if look path glitched
        door = self._ensure_door_aim(release_facelock=True)
        frame_paths = [
            fr.get("path")
            for fr in ((look or {}).get("frames") or [])
            if isinstance(fr, dict) and fr.get("path")
        ]
        names, unnamed, detail, n_frames, face_sims = self._match_faces_in_paths(
            frame_paths
        )
        # Merge faces seen on reconcile into inventory (named only); no VL fiction
        if names:
            _now_rc = time.time()
            for n in names:
                self._id_certain[n] = True
                src = dict(self._id_sources.get(n) or {})
                src["face"] = True
                src["post_facefind_reconcile"] = True
                if face_sims.get(n) is not None:
                    src["face_sim"] = float(face_sims[n])
                self._id_sources[n] = src
            # Insert via the normal enter path (present + grace + seen_at_door)
            # and honor leave tombstones — door physics apply afterwards
            # (DESIGN-OCCUPANCY-STREAM §4, reconcile row)
            for n in names:
                if float(self._left_block_until.get(n) or 0) > _now_rc:
                    continue  # tombstoned: needs a live door face to return
                if n not in self.inventory.present:
                    self.inventory.apply_vl_delta(
                        1, 0, source=reason, stamp_names=[n],
                        entry_grace_sec=ENTRY_GRACE, now=_now_rc, force=True,
                    )
                else:
                    self.inventory.present[n] = _now_rc
                self.inventory.clear_doubt(n)
            self.inventory.baseline_set = True
        out = {
            "ok": bool(look and (look.get("ok") or look.get("door_ok"))),
            "reason": reason,
            "tool": "look_around_facefinder",
            "path": "door→stop(FF)→door→…→door",
            "facefinder": FACEFINDER_ALLOWED,
            "engage_sec": FACEFINDER_ENGAGE_SEC,
            "names": names,
            "unnamed": unnamed,
            "n_frames": n_frames,
            "n_stops": (look or {}).get("n_stops"),
            "wall_sec": round(time.time() - t0, 2),
            "door_return": door,
            "look_ok": (look or {}).get("ok"),
            "door_ok": (look or {}).get("door_ok") or door.get("ok"),
            "detail": (detail or [])[:20],
            "stop_ids": [
                fr.get("id")
                for fr in ((look or {}).get("frames") or [])
                if isinstance(fr, dict)
            ][:12],
        }
        return out

    def reauth(
        self,
        *,
        person_id: str | None = "user",
        use_facefind: bool = False,
    ) -> RoomPresence:
        """Operator re-auth: brief video orient → door hold. Facefind only if unknown body.

        If facefind runs: always follow with reconcile_after_facefind (doctrine).
        """
        refused = self._auth_refused_lights_out()
        if refused:
            cur = self.get()
            lights = getattr(cur, "lights", {}) or {}
            out = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=cur.camera_open,
                camera_blind=cur.camera_blind,
                last_event={
                    "kind": "auth_refused",
                    "source": "lights_out",
                    "reason": "lights_out",
                    "person_id": person_id,
                    "refusal": refused.get("error"),
                    "luminance": lights.get("luminance"),
                    "threshold": lights.get("threshold"),
                    "ts": time.time(),
                },
                count_trust=cur.count_trust,
                camera_mode=cur.camera_mode,
                door_names=list(cur.door_names or []),
            )
            self._set(out)
            return out
        facelock = self.release_facefinder()
        facefind: dict = {}
        reconcile: dict = {}
        try:
            # Default: quick smooth orient (same pattern as baseline). Facefind snaps
            # only when caller opts in for an unknown body — not routine.
            if use_facefind:
                facefind = self._grabber_post(
                    GRABBER_FACEFIND_URL, {"engage_sec": 1.8}, timeout=60.0
                )
                # Doctrine: never leave sneak-in window after facefinder
                try:
                    reconcile = self.reconcile_after_facefind(reason="post_facefind_reauth")
                except Exception as e:
                    reconcile = {"ok": False, "error": str(e)[:200]}
                # FF already engaged above; orient without a second FF tour
                st = self.run_smooth_orient_once(
                    reason="reauth_facefind", facefinder=False
                )
            else:
                # Reauth without facefind: seat aims only (no FF pollution)
                st = self.run_smooth_orient_once(reason="auth", facefinder=False)
            names_found = [p.name for p in st.people if p.name]
            if reconcile.get("names"):
                names_found = list(
                    dict.fromkeys(list(names_found) + list(reconcile.get("names") or []))
                )
            pid = (person_id or "").strip().lower() or None
            if pid and st.headcount == 0 and not names_found:
                st = self.assert_present([pid], source="reauth_voice_hint", replace=False)
                st.count_certain = False  # soft ID; count_trust = camera only
            door = self._ensure_door_aim(release_facelock=True)
            st.last_event = {
                **(st.last_event or {}),
                "kind": "reauth",
                "facelock_release": facelock,
                "facefind_ok": facefind.get("ok"),
                "facefind_n": facefind.get("n"),
                "reconcile_after_facefind": reconcile or None,
                "found": names_found or self.inventory.names(),
                "person_hint": pid,
                "door_return": door,
                "facelock": False,
                "ts": time.time(),
            }
            self._set(st)
            return st
        finally:
            # Hard guarantee: never leave facefinder on the person after auth
            self._ensure_door_aim(release_facelock=True)

    def assert_present(
        self,
        names: list[str],
        *,
        unnamed: int | None = None,
        source: str = "voice",
        replace: bool = False,
    ) -> RoomPresence:
        """Voice / operator injects who is in the room (STONE: voice sorts identity).

        Camera may only see a back-of-head (another person at desk 2026-07-22) — face match
        returns zero; inventory must still accept a named body from voice or operator.
        replace=False merges names into inventory; replace=True sets exact roster.
        """
        now = time.time()
        clean = [n.strip().lower() for n in names if n and str(n).strip()]
        if replace:
            self.inventory.sticky = set(clean)
            self.inventory.apply_reconcile(clean, unnamed if unnamed is not None else 0)
            if self.inventory.events:
                self.inventory.events[-1]["source"] = source
                self.inventory.events[-1]["kind"] = "assert"
        else:
            self.inventory.mark_sticky(clean, source=source)
            for n in clean:
                if n not in self.inventory.present or True:
                    # enter event only when newly added
                    pass
            # emit enter for newly sticky
            for n in clean:
                self.inventory.events.append(
                    {"kind": "enter", "name": n, "ts": now, "source": source}
                )
            if unnamed is not None:
                self.inventory.unnamed = max(0, int(unnamed))
            self.inventory.baseline_set = True
        st = self._presence_from_inventory(
            frame_age=self.get().frame_age_sec if self.get().camera_open else 999.0,
            camera_open=self.get().camera_open,
            camera_blind=self.get().camera_blind,
            last_event={
                "kind": "assert",
                "source": source,
                "names": clean,
                "replace": replace,
                "headcount": self.inventory.headcount(),
                "ts": now,
            },
            count_trust="fact",
            camera_mode=self._mode if self._mode != "away" else "away",
            door_names=list(self.get().door_names or []),
        )
        if self._mode == "away":
            st.count_trust = "stale"
        st.count_certain = st.unidentified == 0 and all(p.name for p in st.people)
        self._set(st)
        self.cycles += 1
        self.last_cycle_at = now
        try:
            self._save_inventory_disk(force=True)
        except Exception:
            pass
        self._sync_oracle_hand(reason=f"assert:{source}")
        return st

    def apply_entry_face_mint(
        self,
        name: str,
        *,
        sim: float,
        source: str = "entry_best_face",
        face_px: list | tuple | None = None,
        door_view: bool = True,
        kind: str = "enter",
    ) -> dict:
        """Mint/refresh named present + face sticky from a door enter face match.

        Testable entry path (the User 2026-08-11): strong User face on enter always
        lands on SoT without operator assert.
        """
        best_name = (name or "").strip().lower()
        out: dict = {"ok": False, "name": best_name, "minted": False}
        if not best_name or kind != "enter" or not door_view:
            out["error"] = "need_named_enter_door_view"
            return out
        from .guests import is_guest

        if is_guest(best_name):
            now = time.time()
            ev = self.inventory.note_visitor(
                best_name, now=now, source=source
            )
            if ev:
                self._visitor_day_log(best_name)
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=cur.camera_open,
                camera_blind=cur.camera_blind,
                last_event={
                    "kind": "visitor",
                    "name": best_name,
                    "sim": float(sim),
                    "source": source,
                    "ts": now,
                },
                count_trust=cur.count_trust or "fact",
                camera_mode=cur.camera_mode or "holding",
                door_names=list(cur.door_names or []),
            )
            self._set(st)
            out["ok"] = True
            out["visitor"] = True
            out["minted"] = False
            out["headcount"] = self.inventory.headcount()
            return out
        thr = float(os.environ.get("RAIL_PRESENCE_MATCH_SIM", "0.60"))
        enter_floor = min(0.55, thr)
        if float(sim) < enter_floor:
            out["error"] = "sim_below_floor"
            out["sim"] = sim
            return out
        if min(face_px or [80, 80]) < 60:
            out["error"] = "face_px_small"
            return out
        now = time.time()
        was = best_name in self.inventory.present
        if not was:
            self.inventory.present[best_name] = now
            self.inventory.entry_grace_until[best_name] = now + float(ENTRY_GRACE)
            self.inventory.events.append(
                {
                    "kind": "enter",
                    "name": best_name,
                    "ts": now,
                    "source": source,
                    "sim": float(sim),
                    "live_face": True,
                }
            )
            out["minted"] = True
        else:
            self.inventory.present[best_name] = now
        self.inventory.seen_at_door.add(best_name)
        self.inventory.last_door_seen[best_name] = now
        self.inventory.baseline_set = True
        self._id_certain[best_name] = True
        src = dict(self._id_sources.get(best_name) or {})
        src.update(
            {
                "face": True,
                "live_face": True,
                "at_door": True,
                "face_sim": float(sim),
            }
        )
        self._id_sources[best_name] = src
        self._note_face_auth(best_name, sim=float(sim), source=source)
        if best_name == self._user_id():
            if not self._user_session:
                self._user_session = {
                    "opened_ts": now,
                    "last_strong_voice_ts": now,
                    "last_strong_voice_sim": None,
                    "opened_via": source,
                }
            try:
                self._maybe_first_auth_greeting(best_name)
            except Exception:
                pass
        try:
            self._save_inventory_disk(force=True)
        except Exception:
            pass
        cur = self.get()
        st = self._presence_from_inventory(
            frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
            camera_open=True,
            camera_blind=False,
            last_event={
                "kind": "entry_face_mint",
                "name": best_name,
                "sim": float(sim),
                "source": source,
                "minted": out["minted"],
                "ts": now,
            },
            count_trust="fact",
            camera_mode=cur.camera_mode or "holding",
            door_names=[best_name],
        )
        self._set(st)
        try:
            self._sync_oracle_hand(reason=f"entry_face:{source}")
        except Exception:
            pass
        out["ok"] = True
        out["headcount"] = self.inventory.headcount()
        out["user_auth"] = bool(st.user_auth)
        out["user_present"] = bool(st.user_present)
        out["user_auth_reason"] = st.user_auth_reason
        return out

    def run_smooth_orient_once(
        self, *, reason: str = "baseline", facefinder: bool | None = None
    ) -> RoomPresence:
        """Orient via look_around (not continuous smooth_scan patrol).

        POST grabber /v1/look_around: per seat aim → optional firmware FF →
        capture → FF OFF → door. FaceID match on stop frames. Always ends
        door home. (Name kept for call sites; tool is look_around since
        2026-08-07.)

        the User 2026-08-09: facefinder is reauth/error-correction only — when a
        sensitive request needs AUTH and certainty is missing, or after a
        lights/power outage. Routine surprise/orient uses seat aims WITHOUT
        facefinder so the ring buffer stays door-clean for entry packs.
        """
        # Default: no firmware facefinder unless reason is reauth/outage.
        r = str(reason or "").lower()
        if facefinder is None:
            facefinder = any(
                k in r
                for k in (
                    "reauth",
                    "facefind",
                    "outage",
                    "lights_out",
                    "power",
                    "auth_uncertain",
                    "sensitive",
                    "uncounted",
                )
            )
        with self._sweep_lock:
            self._mode = "reconciling"
            self._set_last_grabber(self._poll_grabber())
            t0 = time.time()
            look = self._grabber_post(
                GRABBER_LOOK_AROUND_URL,
                {
                    "facefinder": bool(facefinder) and FACEFINDER_ALLOWED,
                    "engage_sec": FACEFINDER_ENGAGE_SEC if facefinder else 0.5,
                    "include_jpeg": False,
                },
                timeout=180.0,
            )
            frame_paths = [
                fr.get("path")
                for fr in ((look or {}).get("frames") or [])
                if isinstance(fr, dict) and fr.get("path")
            ]
            names, unnamed, detail, n_frames, face_sims = self._match_faces_in_paths(
                frame_paths
            )
            # Sweep sims must land in _id_sources or the auth badge is minted
            # with sim=None and user_face_sim stays null (Ultra item 2).
            for n in names:
                if face_sims.get(n) is None:
                    continue
                src = dict(self._id_sources.get(n) or {})
                src["face"] = True
                src["face_sim"] = float(face_sims[n])
                self._id_sources[n] = src
            wall = round(time.time() - t0, 3)
            ok = bool(look and (look.get("ok") or look.get("door_ok")))
            if not ok and not names:
                st = empty_room(camera_open=True)
                st.camera_mode = "holding"
                # Orient failed mid-tour = camera not continuous door feed
                st.count_trust = "stale"
                st.last_event = {
                    "kind": "look_around_orient_failed",
                    "reason": reason,
                    "look": {
                        k: (look or {}).get(k)
                        for k in (
                            "ok",
                            "door_ok",
                            "n_stops",
                            "n_ok",
                            "error",
                            "note",
                        )
                    },
                    "why": (look or {}).get("error") or "look_around failed",
                }
                self.last_error = st.last_event["why"]
                self._set(st)
                self._mode = "holding"
                self._ensure_door_aim()
                return st

            # Best-sim per name across stops (NOT sum — ego-motion law).
            # body_count / error-correct → union only (partial tour ≠ leave).
            # Full baseline/auth reinit may still replace.
            rsn = str(reason or "").lower()
            recon_mode = (
                "union"
                if any(
                    k in rsn
                    for k in (
                        "body_count",
                        "error_correct",
                        "surprise",
                        "voice_while",
                        "uncounted",
                    )
                )
                else "replace"
            )
            ev = self.inventory.apply_reconcile(
                names, unnamed, mode=recon_mode
            )
            st = self._presence_from_inventory(
                frame_age=wall,
                camera_open=True,
                camera_blind=False,
                last_event={
                    "kind": reason if reason in ("auth", "baseline") else "reconcile",
                    "reason": reason,
                    "tool": (
                        "look_around_facefinder" if facefinder else "look_around"
                    ),
                    "mode": "seat_facefinder" if facefinder else "seat_aim",
                    "reconcile_mode": recon_mode,
                    "facefinder": bool(facefinder) and FACEFINDER_ALLOWED,
                    "path": "door→stop(FF)→door→…→door",
                    "engage_sec": FACEFINDER_ENGAGE_SEC,
                    "entered": ev.get("entered"),
                    "left": ev.get("left"),
                    "names": names,
                    "unnamed": unnamed,
                    "n_frames": n_frames,
                    "n_stops": (look or {}).get("n_stops"),
                    "n_ok": (look or {}).get("n_ok"),
                    "wall_sec": wall,
                    "door_ok": (look or {}).get("door_ok"),
                    "detail": (detail or [])[:40],
                    "parse_rule": "look_around_stop_frames",
                },
                count_trust="fact",
                camera_mode="holding",
                door_names=names,
            )
            st.count_certain = st.unidentified == 0 and bool(
                names or st.headcount == 0
            )
            self._baseline_done = True
            self.sweep_cycles += 1
            self.cycles += 1
            self.last_cycle_at = time.time()
            self._mode = "holding"
            self._away_reason = None
            self._set(st)
            self._ensure_door_aim()
            return st

    def _match_faces_in_scan_dir(
        self, scan_dir: str | None
    ) -> tuple[list[str], int, list[str], int, dict[str, float]]:
        """FaceID match on smooth-scan keyframes (legacy path)."""
        from pathlib import Path

        if not scan_dir:
            return [], 0, ["no scan_dir"], 0, {}
        root = Path(scan_dir)
        if not root.is_dir():
            return [], 0, [f"missing {scan_dir}"], 0, {}
        paths = sorted(root.glob("kf_*.jpg"))
        if not paths:
            paths = [
                p
                for p in sorted(root.glob("*.jpg"))
                if not p.name.startswith("ghost_")
            ]
        return self._match_faces_in_paths(paths)

    def _match_faces_in_paths(
        self, paths: list | None
    ) -> tuple[list[str], int, list[str], int, dict[str, float]]:
        """FaceID match on frame paths (look_around stop JPEGs or scan dir).

        Max-sim per id (not count per frame). Returns
        (names, unnamed, detail, n_frames, best_sim_by_name).
        """
        from pathlib import Path

        import cv2

        from .enrollments import match
        from .sweep import MATCH_SIM
        from .vlm_live import is_live

        names_best: dict[str, float] = {}
        unnamed = 0
        detail: list[str] = []
        n_frames = 0
        path_list = [Path(p) for p in (paths or []) if p]
        if not path_list:
            return [], 0, ["no frame paths"], 0, {}
        self.engine.ensure()
        face = self.engine.face
        enrolled = self.engine.enrolled
        if face is None:
            return [], 0, ["face engine unavailable"], 0, {}
        named_seen: dict[str, list] = {}
        frame_sig: dict[int, "np.ndarray"] = {}
        for p in path_list:
            im = cv2.imread(str(p))
            if im is None:
                continue
            n_frames += 1
            frame_sig[n_frames - 1] = _frame_sig(im)
            try:
                faces = face.detect(im)
            except Exception as e:
                detail.append(f"{p.name}: detect {e}"[:80])
                continue
            for fobj in faces:
                emb = face.embed(fobj)
                if emb is None:
                    continue
                name, sim = match(emb, enrolled, threshold=MATCH_SIM)
                bbox = face.bbox(fobj)
                if name:
                    # A recognised face used to be promoted on similarity alone,
                    # while an UNKNOWN one had to pass is_live() — so a framed
                    # photo of an enrolled person minted a present, face-authed
                    # body. The named path is the one that grants auth, so it
                    # needs corroboration too. is_live() is a VLM call and is
                    # documented for rare/unmatched faces only (and is off on
                    # this box), so the gate here is motion across the station's
                    # own keyframes: a print is pixel-identical frame to frame,
                    # a seated person never is. Deferred to after the loop,
                    # where every frame for this name is in hand.
                    named_seen.setdefault(name, []).append(
                        (n_frames - 1, float(sim), _crop_sig(im, bbox)))
                    if name not in names_best or sim > names_best[name]:
                        names_best[name] = float(sim)
                elif bbox is not None and is_live(im, bbox):
                    unnamed += 1
                    detail.append(f"{p.name}: unknown LIVE ({sim:.2f})")
        # Adjudicate named faces: keep only those that MOVED across keyframes.
        for nm, obs in list(named_seen.items()):
            best = max(o[1] for o in obs)
            verdict, ev = _moved(obs, frame_sig)
            if verdict is True:
                detail.append(f"{nm}: LIVE moved={ev:.2f} ({best:.2f})")
            elif verdict is False:
                names_best.pop(nm, None)
                detail.append(f"{nm}: STATIC rejected moved={ev:.2f} ({best:.2f})")
            else:
                # one usable frame only: no motion evidence either way. Keep the
                # body (presence must not go blind) but say so out loud — auth
                # upstream can refuse to trust an UNVERIFIED name.
                detail.append(f"{nm}: UNVERIFIED single-frame ({best:.2f})")
        # Cap unnamed: multi-frame of same stranger ≠ N people (ego-motion)
        unnamed = min(unnamed, 2)
        return sorted(names_best.keys()), unnamed, detail, n_frames, dict(names_best)

    def run_sweep_once(self, *, reason: str = "reconcile") -> RoomPresence:
        """Compat name → brief video orient (station multi-stop deleted from live path)."""
        if str(reason or "").lower() in ("auth", "reauth", "face_auth"):
            refused = self._auth_refused_lights_out()
            if refused:
                st = refused.get("presence")
                if isinstance(st, dict):
                    # Return live object with auth_refused event (not invent headcount)
                    cur = self.get()
                    lights = getattr(cur, "lights", {}) or {}
                    out = self._presence_from_inventory(
                        frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                        camera_open=cur.camera_open,
                        camera_blind=cur.camera_blind,
                        last_event={
                            "kind": "auth_refused",
                            "source": "lights_out",
                            "reason": "lights_out",
                            "refusal": refused.get("error"),
                            "luminance": lights.get("luminance"),
                            "threshold": lights.get("threshold"),
                            "ts": time.time(),
                        },
                        count_trust=cur.count_trust,
                        camera_mode=cur.camera_mode,
                        door_names=list(cur.door_names or []),
                    )
                    self._set(out)
                    return out
        return self.run_smooth_orient_once(reason=reason)

    def run_door_once(self) -> RoomPresence:
        """Door FOV from ring buffer (5s @ 15–30fps) — faces are routine, not a sweep.

        Tool pause (the User 2026-08-03): if rail-camera reports an *active* tool
        lease/sweep, do NOT face-match the ring. Mid-tour FOV is junk.

        the User 2026-08-12 (door-hold law, foundational since spring): do NOT treat
        ``at_door=false`` alone as a tool hold. That froze the door pipeline
        whenever the cam sat on chair pan≈300000 after a failed look_around
        return — walk-ins unseen for an entire night. Off-door without a live
        lease → recover door home and keep watching.
        """
        self._set_last_grabber(self._poll_grabber())
        try:
            self._beam_maybe_rest()
        except Exception:
            pass
        # Visitor feeder is motion glance (not this 2s door loop).
        if getattr(self, "_room_busy", False):
            self._busy_beat_ts = time.time()
        elif (
            getattr(self, "_busy_auth_scan_armed", False)
            and not getattr(self, "_busy_auth_scanned", False)
        ):
            # Busy already dropped (lease skip). One scan, still pending.
            try:
                self._busy_auth_scan()
            except Exception:
                pass
        if self._mode == "away":
            st = self.get()
            st.camera_mode = "away"
            st.count_trust = "stale"
            st.count_certain = False
            st.ts = time.time()
            self._set(st)
            self.cycles += 1
            self.last_cycle_at = time.time()
            return st

        g = self.last_grabber if isinstance(self.last_grabber, dict) else {}
        hold_rem = 0.0
        try:
            hold_rem = float(g.get("hold_remaining_sec") or 0.0)
        except (TypeError, ValueError):
            hold_rem = 0.0
        lease = g.get("lease") if isinstance(g.get("lease"), dict) else None
        # Active tool only — never "not at door" alone (door-hold law)
        tool_hold = bool(
            g.get("sweeping")
            or (g.get("tool_hold") and hold_rem > 0.05)
            or (g.get("tool_hold") and lease)
            or hold_rem > 0.05
        )
        if tool_hold:
            # Advance buffer cursor so mid-tour frames never become door events later
            try:
                frames_skip = []
                if USE_BUFFER and self.camera.mode in ("http", "auto"):
                    frames_skip = self.camera.grab_buffer(
                        since_seq=self._buffer_seq,
                        max_frames=FACE_MAX_PER_TICK * max(FACE_STRIDE, 1),
                        stride=FACE_STRIDE,
                    ) or []
                seqs = [
                    int(f.seq)
                    for f in frames_skip
                    if getattr(f, "seq", None) is not None
                ]
                if seqs:
                    self._buffer_seq = max(seqs)
                    self.last_frame_seq = self._buffer_seq
            except Exception:
                pass
            st = self._presence_from_inventory(
                frame_age=float(g.get("frame_age_sec") or 0.0),
                camera_open=bool(g.get("ok") or g.get("frames")),
                camera_blind=False,
                last_event={
                    "kind": "tool_hold",
                    "why": "camera leased to tool — door pipeline paused",
                    "pan_actual": g.get("pan_actual"),
                    "cmd_pan": g.get("cmd_pan"),
                    "hold_remaining_sec": g.get("hold_remaining_sec"),
                    "sweeping": g.get("sweeping"),
                    "at_door": g.get("at_door"),
                },
                count_trust="stale" if not self.inventory.baseline_set else "fact",
                camera_mode="away",
                door_names=[],
            )
            st.count_certain = bool(self.inventory.baseline_set)
            self._set(st)
            self.cycles += 1
            self.door_cycles += 1
            self.last_cycle_at = time.time()
            return st

        # Door-hold: off door without a live tool → yank home (rate-limited)
        if g.get("at_door") is False:
            now_r = time.time()
            last_r = float(getattr(self, "_door_recover_ts", 0.0) or 0.0)
            if now_r - last_r >= 3.0:
                self._door_recover_ts = now_r
                try:
                    rec = self._ensure_door_aim(release_facelock=True)
                    print(
                        f"[presence] door-hold recover pan was "
                        f"{g.get('pan_actual')} → {rec.get('pan_actual') or rec.get('pan_cmd')}",
                        flush=True,
                    )
                except Exception as e:
                    print(f"[presence] door-hold recover fail: {e}", flush=True)
                # Re-poll after aim
                self._set_last_grabber(self._poll_grabber())
                g = self.last_grabber if isinstance(self.last_grabber, dict) else g
            if g.get("at_door") is False:
                # Still off-door this tick — skip match (wrong FOV) but do not
                # mark tool_hold forever; next ticks keep recovering.
                st = self._presence_from_inventory(
                    frame_age=float(g.get("frame_age_sec") or 0.0),
                    camera_open=bool(g.get("ok") or g.get("frames")),
                    camera_blind=False,
                    last_event={
                        "kind": "door_recover",
                        "why": "rest FOV off door — recovering hold",
                        "pan_actual": g.get("pan_actual"),
                        "cmd_pan": g.get("cmd_pan"),
                        "at_door": False,
                    },
                    count_trust="stale" if not self.inventory.baseline_set else "fact",
                    camera_mode="holding",
                    door_names=[],
                )
                st.count_certain = bool(self.inventory.baseline_set)
                self._set(st)
                self.cycles += 1
                self.door_cycles += 1
                self.last_cycle_at = time.time()
                return st

        self.engine.ensure()
        now = time.time()
        frames = []
        if USE_BUFFER and self.camera.mode in ("http", "auto"):
            frames = self.camera.grab_buffer(
                since_seq=self._buffer_seq,
                max_frames=FACE_MAX_PER_TICK * max(FACE_STRIDE, 1),
                stride=FACE_STRIDE,
            )
            # first tick: no since_seq yet → grab recent window once
            if not frames and self._buffer_seq is None:
                frames = self.camera.grab_buffer(
                    since_seq=None,
                    max_frames=min(FACE_MAX_PER_TICK, 30),
                    stride=FACE_STRIDE,
                )
        if not frames:
            fr = self.camera.grab()
            frames = [fr] if fr is not None else []

        self.last_buffer_n = len(frames)
        if not frames:
            self.last_error = self.camera.last_error or "no door frames"
            st = self._presence_from_inventory(
                frame_age=999.0,
                camera_open=False,
                camera_blind=True,
                last_event={"kind": "door_hold", "why": self.last_error},
                count_trust="stale" if not self.inventory.baseline_set else "fact",
                camera_mode="holding",
                door_names=[],
            )
            if st.camera_blind and self.inventory.baseline_set:
                st.count_trust = "stale"
                st.count_certain = False
            self._set(st)
            self.cycles += 1
            self.door_cycles += 1
            self.last_cycle_at = now
            return st

        newest = max(frames, key=lambda f: f.ts)
        frame_age = max(0.0, now - newest.ts)
        if frame_age > 8.0:
            self.last_error = f"stale door capture {frame_age:.1f}s"
            st = self._presence_from_inventory(
                frame_age=frame_age,
                camera_open=True,
                camera_blind=True,
                last_event={"kind": "door_hold", "why": self.last_error},
                count_trust="stale",
                camera_mode="holding",
                door_names=[],
            )
            st.count_certain = False
            self._set(st)
            self.cycles += 1
            self.door_cycles += 1
            self.last_cycle_at = now
            return st

        assert self.engine.face is not None
        from .enrollments import assign_faces_same_frame, load_person_stamps
        from .vlm_live import is_live
        from .sweep import MATCH_SIM

        # Best face evidence across the buffer window (not just the latest still).
        # BODY COUNT LAW: two walked in ⇔ two faces in the SAME frame (max, not sum).
        # ID LAW (the User): M/F stamp splits the User from F; face+voice confirm; two F →
        # best unique match *of those two* in-frame; unmatched stay F1/F2 in logs.
        stamps = getattr(self, "_person_stamps", None)
        if stamps is None:
            stamps = load_person_stamps()
            self._person_stamps = stamps
        best: dict[str, float] = {}  # confirmed name -> best sim
        genders: dict[str, str] = {}  # name -> stamped/soft M|F
        provisional_labels: list[str] = []  # F1/F2/M1 seen this tick
        max_unnamed_same_frame = 0
        max_bodies_same_frame = 0
        max_unmatched_live_same_frame = 0
        best_sim_unmatched_to_present = 0.0
        present_now = {
            str(n).strip().lower()
            for n in (self.inventory.present or {})
            if n
        }
        live_genders_seen: set[str] = set()  # soft M/F from LIVE faces this window
        detail: list[str] = []
        scored = 0
        n_faces_total = 0
        # Uncounted-body conflict check on the freshest frame (no camera move)
        if frames:
            self._uncounted_body_check(frames[-1].bgr)
            try:
                from .door_background import note_rest_frames

                note_rest_frames(
                    [fr.bgr for fr in frames if getattr(fr, "bgr", None) is not None],
                    at_door=bool(g.get("at_door")),
                )
            except Exception:
                pass
        # THE ROOM IS EMPTY: the newest frame only, the loop at its resting interval (idle_gate; .101, 2026-10-10).
        # A SETTLED FACE (round 14): the newest frame only while nothing moves, the count holds and nobody speaks.
        # A TURN IS A MATCHED VOICE (round 15): the ear posts speaker.voice on every speech onset it hears, matched or
        # not (a blip, the room), and .101's loop re-armed on those too; only the enrolled person's voice
        # (_last_user_voice, user.voice) holds the full rate now.
        try:
            lv = getattr(self, "_last_user_voice", None) or {}
            if lv.get("ts") and lv.get("match", True):
                self._idle_gate().turn(float(lv["ts"]))
        except Exception:
            pass
        scan = self._idle_gate().pick(frames, now)
        for fr in scan:
            scored += 1
            if scored > FACE_MAX_PER_TICK:
                break
            # Must bind before user_auth_priority detail (UnboundLocalError
            # 2026-08-13: seq used at match log, assigned after live_flags).
            seq = (fr.meta or {}).get("seq")
            faces = self.engine.face.detect(fr.bgr)
            n_faces_total += len(faces or [])
            self._idle_gate().saw(len(faces or []))
            rows: list[dict] = []
            small_bodies = 0  # detected at the door but under the naming size
            # One diff against the empty room per frame, shared by every face.
            try:
                from .door_background import foreground_mask, note_rest_face_boxes

                _fg = foreground_mask(fr.bgr)
                note_rest_face_boxes(
                    [getattr(x, "bbox", None) for x in (faces or [])],
                    at_door=bool(g.get("at_door")),
                )
            except Exception:
                _fg = None
            for f in faces:
                # Door-view fixed-position mask (the User 08-07): the hallway
                # portraits occupy a fixed sliver of every buffer frame and
                # squeeze through the killbook's adaptive-thr window (the
                # family portrait CONTAINS another person). Position discriminates
                # where cosine can't; a real transit spans many positions.
                #
                # SUPERSEDED 2026-08-15. The mask is a rectangle, and a person
                # standing in the doorway lands in it: the User's own entry had its
                # face centre at cx 0.624 against a mask starting at 0.620 —
                # four thousandths of frame width — so every face was dropped
                # here, max_bodies_same_frame hit 0, door_names came back empty
                # and observe_door saw an empty doorway. Camera triggers fired
                # at energy 44.3 and 48.4 and NO door event minted, so no
                # door_v2 pack was ever cut for him.
                #
                # Subtraction answers the same question without coordinates:
                # measured on those frames, his face was 92.3% foreground and
                # the portrait 0.0%. The mask stays as the fallback for when no
                # background model is loaded.
                try:
                    from .door_background import is_furniture
                    from .static_faces import (
                        blanked_by_reference,
                        in_masked_zone,
                        too_small_to_name,
                    )

                    def _furniture(_bb) -> bool:
                        if _fg is not None:
                            return is_furniture(fr.bgr, _bb, mask=_fg)
                        return in_masked_zone("door_home", _bb, fr.bgr.shape)

                    def _print_on_the_wall(_bb, _emb) -> bool:
                        from .door_background import is_still_picture

                        hit, _why = blanked_by_reference(
                            _emb, _bb, fr.bgr.shape, stop="door"
                        )
                        return bool(hit) or is_still_picture(_bb)

                    bb = getattr(f, "bbox", None)
                    emb = self.engine.face.embed(f)
                    if bb is not None and (
                        _furniture(bb) or _print_on_the_wall(bb, emb)
                    ):
                        continue
                    # Too small to NAME (portraits across the hallway are
                    # ~20 px). the User 2026-08-09: too-small must not mean
                    # too-small-to-EXIST. Measured on his 16:24 walk-out —
                    # 103 faces detected across 98 frames, 100 discarded here,
                    # so door_names came back empty, max_bodies_same_frame hit
                    # 0, and NO leave event ever minted. Walking out you move
                    # AWAY from the camera, so an exit face is always smaller
                    # than the entry face that named you (his entry: 135x212;
                    # his exit: 35x49 at sim 0.116 — a back of a head carries
                    # no identity at any threshold). An exit can only ever be
                    # decided by clothing, and it can only get there if the
                    # event mints. So: not nameable, still a BODY.
                    # Prints on the closed door are in the nightly rest ref —
                    # those are not a crossing.
                    if too_small_to_name(bb):
                        try:
                            if not _furniture(bb) and not _print_on_the_wall(
                                bb, emb
                            ):
                                small_bodies += 1
                        except Exception:
                            pass
                        continue
                except Exception:
                    emb = self.engine.face.embed(f)
                if emb is None:
                    continue
                g_label, _g_conf = self.engine.face.gender(f)
                soft_g = g_label if g_label in ("M", "F") else None
                # Prefer house stamp when soft gender missing but we will assign later
                rows.append({"emb": emb, "soft_gender": soft_g, "face": f})
            if not rows:
                # Faces were seen at the door but none were large enough to
                # name. That is a crossing with no identity, not an empty
                # doorway — carry it as unnamed bodies so the event mints.
                if small_bodies:
                    max_unnamed_same_frame = max(max_unnamed_same_frame,
                                                 small_bodies)
                    max_bodies_same_frame = max(max_bodies_same_frame,
                                                small_bodies)
                continue
            assigned = assign_faces_same_frame(
                rows,
                self.engine.enrolled,
                stamps=stamps,
                threshold=MATCH_SIM,
            )
            # LIVE/SCREEN ops call ONLY for unmatched / provisional faces.
            # (Bug 2026-08-02: calling is_live on every face every buffer frame
            # hammered ops ~200/min while door oracle was off.)
            live_flags: list[bool] = []
            for f, asg in zip(
                [r["face"] for r in rows], assigned, strict=False
            ):
                named = bool(asg.get("name") and not asg.get("provisional"))
                if named:
                    # Liveness for ADDs only (the User 2026-08-07): an enrolled
                    # match for someone ALREADY on the roster skips the VLM
                    # (the 08-02 ops-hammering guard). But a name that is NOT
                    # on the roster would MINT a body — and the hallway family
                    # portrait CONTAINS another person, so cosine can never separate
                    # print from person (measured 0.55-0.80 either way). Pay
                    # for one liveness call in exactly that rare case.
                    nm = str(asg.get("name") or "").lower()
                    try:
                        sim_named = float(asg.get("sim") or 0)
                    except (TypeError, ValueError):
                        sim_named = 0.0
                    # AUTH priority: sudo User face ≥ MATCH_SIM is NEVER gated
                    # on is_live (present or not). Face is the control plane.
                    if nm == self._user_id() and sim_named >= float(MATCH_SIM):
                        live_flags.append(True)
                        detail.append(
                            f"buf: {nm} ({sim_named:.2f}) "
                            f"user_auth_priority seq={seq}"
                        )
                        continue
                    if nm and nm not in self.inventory.present:
                        if self._live_judge_up():
                            # judge reachable → its verdict decides. is_live
                            # FAILS CLOSED by design — only when ops answers.
                            bbox = self.engine.face.bbox(f)
                            live = bbox is not None and is_live(fr.bgr, bbox)
                            live_flags.append(live)
                            if not live:
                                detail.append(f"static-print veto: {nm}")
                            continue
                    live_flags.append(True)  # already present or judge down
                    continue
                bbox = self.engine.face.bbox(f)
                live = bbox is not None and is_live(fr.bgr, bbox)
                live_flags.append(live)
                if live:
                    soft_g = next(
                        (
                            r.get("soft_gender")
                            for r in rows
                            if r.get("face") is f
                        ),
                        None,
                    )
                    if soft_g in ("M", "F"):
                        live_genders_seen.add(soft_g)
            # pad if assign dropped rows
            while len(live_flags) < len(rows):
                live_flags.append(False)
            named_this_frame: set[str] = set()
            unnamed_this_frame = 0
            unmatched_live_this_frame = 0
            crowd_this_frame = any(asg.get("crowd_noise") for asg in assigned)
            if crowd_this_frame:
                # F3+: abandon F IDs — one crowd_noise token, keep M if any
                n_f = sum(1 for asg in assigned if asg.get("crowd_noise") or asg.get("gender") == "F")
                n_m_named = 0
                n_m_live = 0
                for row, asg, live in zip(rows, assigned, live_flags):
                    if asg.get("crowd_noise") or asg.get("gender") == "F":
                        continue
                    if (
                        asg.get("name")
                        and not asg.get("provisional")
                        and not live
                    ):
                        continue  # static-print veto (the User 08-07)
                    if asg.get("name") and not asg.get("provisional"):
                        nm = asg["name"]
                        sim = float(asg.get("sim") or 0)
                        named_this_frame.add(nm)
                        n_m_named += 1
                        if nm not in best or sim > best[nm]:
                            best[nm] = sim
                        g = (stamps.get(nm) or {}).get("gender") or asg.get("gender")
                        if g in ("M", "F"):
                            genders[nm] = g
                    elif live:
                        n_m_live += 1
                        lab = asg.get("label") or "M?"
                        if lab not in provisional_labels and lab != "crowd_noise":
                            provisional_labels.append(lab)
                # crowd counts as bodies but not IDs — one unnamed crowd unit for inventory
                unnamed_this_frame = 1 if n_f else 0
                if "crowd_noise" not in provisional_labels:
                    provisional_labels.append("crowd_noise")
                detail.append(
                    f"buf: crowd_noise F>={n_f} (no F ids) seq={seq}"
                )
                if named_this_frame:
                    detail.append(f"buf: M kept {sorted(named_this_frame)} seq={seq}")
                bodies_here = len(named_this_frame) + unnamed_this_frame + n_m_live
                # count concurrent F as max(1, n_f) for body truth? the User: abandon IDs not body void
                # Prefer same-frame face count for bodies, without IDs
                bodies_here = len(rows)  # all faces present; IDs wiped for F
                unnamed_this_frame = max(0, bodies_here - len(named_this_frame))
            else:
                for row, asg, live in zip(rows, assigned, live_flags):
                    if (
                        asg.get("name")
                        and not asg.get("provisional")
                        and not live
                    ):
                        continue  # static-print veto (the User 08-07)
                    if asg.get("name") and not asg.get("provisional"):
                        nm = asg["name"]
                        sim = float(asg.get("sim") or 0)
                        named_this_frame.add(nm)
                        if nm not in best or sim > best[nm]:
                            best[nm] = sim
                        g = (stamps.get(nm) or {}).get("gender") or asg.get("gender")
                        if g in ("M", "F"):
                            genders[nm] = g
                        detail.append(
                            f"buf: {nm} ({sim:.2f}) g={g or '?'} seq={seq}"
                        )
                    elif live:
                        unnamed_this_frame += 1
                        unmatched_live_this_frame += 1
                        lab = asg.get("label") or "U"
                        if lab not in provisional_labels:
                            provisional_labels.append(lab)
                        try:
                            u_sim = float(asg.get("sim") or 0)
                        except (TypeError, ValueError):
                            u_sim = 0.0
                        u_nm = str(asg.get("name") or "").strip().lower()
                        if u_nm and u_nm in present_now:
                            if u_sim > best_sim_unmatched_to_present:
                                best_sim_unmatched_to_present = u_sim
                        detail.append(
                            f"buf: {lab} LIVE ({u_sim:.2f})"
                            f" g={asg.get('gender') or '?'} provisional seq={seq}"
                        )
                    else:
                        detail.append(
                            f"buf: ignored ({float(asg.get('sim') or 0):.2f}) seq={seq}"
                        )
                bodies_here = len(named_this_frame) + unnamed_this_frame
            if unnamed_this_frame > max_unnamed_same_frame:
                max_unnamed_same_frame = unnamed_this_frame
            if unmatched_live_this_frame > max_unmatched_live_same_frame:
                max_unmatched_live_same_frame = unmatched_live_this_frame
            if bodies_here > max_bodies_same_frame:
                max_bodies_same_frame = bodies_here
            if bodies_here:
                detail.append(
                    f"frame_concurrent={bodies_here}"
                    f" named={sorted(named_this_frame)}"
                    f" provisional={[x for x in provisional_labels]}"
                    f" crowd={'yes' if crowd_this_frame else 'no'}"
                    f" seq={seq}"
                )
        # presence-perfect §1.3: an unmatched body is a distinct unknown only
        # if some SINGLE frame actually held more bodies than named matches —
        # cross-frame unions were the the user+U1+U2 echo factory.
        door_unnamed = max(
            0, min(max_unnamed_same_frame, max_bodies_same_frame - len(best))
        )
        self._last_genders = genders
        self._last_provisional = list(provisional_labels)
        for n, g in genders.items():
            self.inventory.note_gender(n, g)
        self.last_face_frames = scored
        door_names = sorted(best.keys())
        try:
            self._idle_gate().who(door_names, door_unnamed)     # a newcomer wakes a settled face (round 16)
        except Exception:
            pass
        # Fable/the User 2026-08-12: live rail silent 0.0 faces while offline FaceID
        # matched — if N consecutive ticks have body energy but zero detections,
        # re-init the face engine (cheap guard for stuck ORT/provider).
        try:
            body_energy = int(max_bodies_same_frame or 0) + int(
                max_unnamed_same_frame or 0
            )
            if body_energy > 0 and n_faces_total == 0:
                self._zero_face_body_ticks = (
                    int(getattr(self, "_zero_face_body_ticks", 0) or 0) + 1
                )
            else:
                self._zero_face_body_ticks = 0
            thr_z = int(os.environ.get("RAIL_FACE_ZERO_REINIT_TICKS", "8"))
            if self._zero_face_body_ticks >= thr_z:
                print(
                    f"[presence] face engine re-init: "
                    f"{self._zero_face_body_ticks} ticks body_energy>0 "
                    f"but n_faces=0",
                    flush=True,
                )
                try:
                    eng = getattr(self.engine, "face", None)
                    if eng is not None and hasattr(eng, "reset"):
                        eng.reset()
                    elif hasattr(self.engine, "ensure"):
                        # force re-ensure
                        try:
                            self.engine.face = None  # type: ignore[attr-defined]
                        except Exception:
                            pass
                        self.engine.ensure()
                except Exception as e:
                    print(f"[presence] face re-init fail: {e}", flush=True)
                self._zero_face_body_ticks = 0
                detail.append("face_engine_reinit")
        except Exception:
            pass

        # Sole-gender soft bind (the User walk 2026-07-27): live F/M provisional must
        # not invent a second body when exactly one confirmed same-gender person is
        # already in inventory (another person → F1 ghost while another person sticky).
        inv_names = set(self.inventory.names())
        from .enrollments import load_person_stamps as _lps

        stamps_bind = getattr(self, "_person_stamps", None) or _lps()
        self._person_stamps = stamps_bind
        for gcode, pref in (("F", "F"), ("M", "M")):
            has_prov = any(
                str(lab).upper().startswith(pref) for lab in provisional_labels
            ) or any(
                str(lab).upper().startswith(pref) for lab in (self._last_provisional or [])
            )
            # also if door_unnamed>0 and live gender seen
            live_g = gcode in set(genders.values()) or any(
                str(lab).upper().startswith(pref) for lab in provisional_labels
            )
            if not live_g and not has_prov:
                continue
            same = [
                n
                for n in inv_names
                if (stamps_bind.get(n) or {}).get("gender") == gcode
                or (self.inventory.gender or {}).get(n) == gcode
            ]
            if len(same) != 1:
                continue
            sole = same[0]
            if sole in door_names:
                # already matched — drop same-gender provisional bodies
                door_unnamed = 0
                provisional_labels[:] = [
                    lab
                    for lab in provisional_labels
                    if not str(lab).upper().startswith(pref)
                ]
                continue
            # bind keeps phantom suppression + presence refresh only — it no
            # longer writes door_names/best: gender-match is not arm/leave
            # evidence (DESIGN-OCCUPANCY-STREAM §5; arming needs a real face)
            if sole in self.inventory.present:
                self.inventory.present[sole] = now
            genders[sole] = gcode
            door_unnamed = 0
            provisional_labels[:] = [
                lab
                for lab in provisional_labels
                if not str(lab).upper().startswith(pref)
            ]
            detail.append(f"sole_gender_bind {gcode}→{sole} (no phantom provisional)")
        self._last_provisional = list(provisional_labels)

        # advance buffer cursor
        seqs = [
            int(f.meta["seq"])
            for f in frames
            if f.meta and f.meta.get("seq") is not None
        ]
        if seqs:
            self._buffer_seq = max(seqs)
            self.last_frame_seq = self._buffer_seq
            self.seq_stall_cycles = 0

        # No concurrent body this buffer → do not invent named/U from residual
        # emb noise (poster / last-frame ghost). Measured false the user@0.57 empty FOV.
        # AUTH LAW (the User 2026-08-12): never discard sudo User face that cleared
        # MATCH_SIM — AUTH is the control plane; body-count wipe must not kill it.
        uid_auth = self._user_id()
        thr_auth = float(MATCH_SIM)
        user_face_keep: float | None = None
        try:
            if uid_auth in best and float(best[uid_auth]) >= thr_auth:
                user_face_keep = float(best[uid_auth])
        except (TypeError, ValueError):
            user_face_keep = None
        if max_bodies_same_frame <= 0:
            door_names = []
            door_unnamed = 0
            best = {}
            provisional_labels = []
            # Empty FOV: do not put User on door_names. observe_door would
            # mill-enter them (CP 0 → the user back 15:45:21 while seated at 6).

        # AUTH LAW: User face ≥ MATCH_SIM opens sticky session AUTH.
        # Occupancy is Argus/door apply — not this badge.
        if user_face_keep is not None or (
            uid_auth in best and float(best.get(uid_auth) or 0) >= thr_auth
        ):
            usim = float(
                user_face_keep
                if user_face_keep is not None
                else best[uid_auth]
            )
            try:
                self._note_face_auth(uid_auth, sim=usim, source="door_buffer")
                if not self._user_session:
                    self._user_session = {
                        "opened_ts": now,
                        "last_strong_voice_ts": None,
                        "last_strong_voice_sim": None,
                        "opened_via": "door_face_auth_law",
                    }
                detail.append(
                    f"auth_law: sticky AUTH {uid_auth} sim={usim:.3f} "
                    f"(no occupancy mint)"
                )
            except Exception as e:
                detail.append(f"auth_law_error: {e}"[:80])

        # Door face matches are confirmed IDs (FaceID) — soft-ID law face path.
        # Guests: visitor_today only — never occupancy AUTH.
        from .guests import is_guest

        guest_door = [n for n in door_names if is_guest(n)]
        for g in guest_door:
            ev = self.inventory.note_visitor(g, now=now, source="door_buffer")
            if ev:
                self._visitor_day_log(g)
        door_names = [n for n in door_names if not is_guest(n)]
        for n in door_names:
            self._id_certain[n] = True
            src = dict(self._id_sources.get(n) or {})
            src["face"] = True
            src["door_buffer"] = True
            if n in best:
                src["face_sim"] = float(best[n])
            self._id_sources[n] = src
            # AUTH-first: buffer hit must open sticky (not only entry_best packs)
            try:
                self._note_face_auth(
                    n, sim=best.get(n), source="door_buffer"
                )
            except Exception:
                pass

        if getattr(self, "_room_busy", False):
            new_evs = []
            detail.append("busy: mill silent")
        elif not self.inventory.baseline_set:
            self.inventory.set_baseline(door_names, door_unnamed)
            new_evs = [{"kind": "baseline", "source": "door_buffer"}]
        else:
            new_evs = self.inventory.observe_door(
                door_names,
                door_unnamed,
                leave_confirm_sec=LEAVE_CONFIRM,
                entry_grace_sec=ENTRY_GRACE,
                now=now,
                ghost_recovery_sec=SOLE_EMPTY_LEAVE_SEC,
                exit_arm_ttl_sec=EXIT_ARM_TTL_SEC,
                transit_window_sec=VL_DEDUP_SEC if VL_DEDUP_SEC > 0 else 12.0,
                # §1.3: transits/pendings record true per-frame concurrency,
                # never the cross-frame union (the the user+U1+U2 echo factory)
                door_bodies_max=max_bodies_same_frame,
                unmatched_live=max_unmatched_live_same_frame,
                unmatched_sim_to_present=best_sim_unmatched_to_present,
            )
        # Cross-path dedup: door applies join the multi-voter window so a VL
        # ±1 within the window cannot double-count the same transit (3x today)
        if new_evs:
            # true per-frame concurrency (presence-perfect §1.3) — the
            # cross-frame sum poisoned dedup with phantom bodies=2
            bodies_now = max(1, int(max_bodies_same_frame or 0)) \
                if (door_names or door_unnamed) else 0
            with self._vl_dedup_lock:
                for e in new_evs:
                    if e.get("kind") == "enter":
                        self._vl_recent.append(
                            {"ts": now, "entered": 1, "left": 0,
                             "source": "door_face",
                             "slot_key": e.get("name") or e.get("unnamed_key"),
                             "bodies": max(1, bodies_now)})
                    elif (e.get("kind") == "leave"
                          and e.get("source") == "door_empty_after"):
                        self._vl_recent.append(
                            {"ts": now, "entered": 0, "left": 1,
                             "source": "door_face",
                             "slot_key": e.get("name"), "bodies": 1})
                if len(self._vl_recent) > 20:
                    self._vl_recent = self._vl_recent[-12:]
            # §3.5 door-path capture: the buffer is FRESH here (the crossing
            # just happened) — door_face/door_live mints finally get stills.
            self._capture_door_enter_stills(new_evs)
            # Oracle witness for pending_crossing (feed the corroboration gate
            # without VL voters). Env default OFF — RAIL_DOOR_ORACLE_WITNESS=1.
            if occupancy_on() and DOOR_ORACLE_WITNESS:
                for e in new_evs:
                    if e.get("kind") == "pending_crossing":
                        self._schedule_oracle_witness(
                            float(e.get("ts") or e.get("crossing_ts") or now)
                        )
            # Door capture v2 (design-door-capture-v2, RAIL_DOOR_CAPTURE_V2
            # default OFF): event-anchored ≤60-frame pack + entry_face /
            # entry_body artifacts, assembled off-thread.
            try:
                from . import door_capture

                if occupancy_on() and door_capture.ENABLED:
                    for e in new_evs:
                        if e.get("kind") == "crossing_cancelled":
                            # Silent before 2026-08-10: cancelled crossings
                            # never match enter|leave so no pack, no count.
                            self._capture_cancelled_no_pack = (
                                getattr(self, "_capture_cancelled_no_pack", 0)
                                + 1
                            )
                            print(
                                f"[door_capture] skip cancelled "
                                f"reason={e.get('reason')} "
                                f"crossing_ts={e.get('crossing_ts')} "
                                f"total_cancelled_no_pack="
                                f"{self._capture_cancelled_no_pack}",
                                flush=True,
                            )
                            continue
                        cap_kind = door_capture.capture_kind_for_event(e)
                        if not cap_kind:
                            continue
                        # the User 2026-08-09: "each event needs to impact only one
                        # ID ... +2, -2, or null all need double outputs."
                        # The old `break` here coalesced a multi-event tick
                        # into ONE capture, so a double entry could only ever
                        # produce a single add and a null (one in, one out)
                        # produced one artifact for two opposite decisions.
                        # Every event now gets its own start_event; the
                        # capture's own single-flight may still refuse the
                        # second, but that refusal is now COUNTED instead of
                        # being pre-empted here and lost silently.
                        # pending_crossing → kind=motion: Argus looks, SoT
                        # stays nameless (capture_kind_for_event).
                        started = door_capture.get_capture().start_event(
                            kind=cap_kind,
                            trigger_seq=self._buffer_seq,
                            trigger_ts=now,
                            slot_key=str(
                                e.get("name") or e.get("unnamed_key") or ""
                            )
                            or None,
                            on_done=self._register_v2_artifacts,
                        )
                        if started is False:
                            self._capture_coalesced = (
                                getattr(self, "_capture_coalesced", 0) + 1
                            )
            except Exception:
                pass
            # G2: enter / re_id at door → Inspector shadow live_snap (async)
            enter_names = []
            for e in new_evs:
                if e.get("kind") != "enter":
                    continue
                k = e.get("name") or e.get("unnamed_key")
                if k:
                    enter_names.append(str(k).lower())
            if enter_names:
                # fire after state is set below — stash for post-_set
                self._pending_inspector_auto = {
                    "trigger": "enter",
                    "person_id_hint": enter_names[0] if len(enter_names) == 1 else None,
                    "names": enter_names,
                }
            elif any(e.get("kind") in ("re_id", "traits_refresh") for e in new_evs):
                self._pending_inspector_auto = {
                    "trigger": "re_id",
                    "person_id_hint": (door_names[0] if len(door_names) == 1 else None),
                }
        # Tombstone named leaves so CODE memory cannot re-add them
        left_names = [
            str(e.get("name")).lower()
            for e in (new_evs or [])
            if e.get("kind") == "leave" and e.get("name")
        ]
        if left_names:
            self._note_left(left_names, now=now)

        last_ev: dict = {
            "kind": "door_hold",
            "source": "buffer",
            "door_names": door_names,
            "best_sim": {k: round(v, 3) for k, v in best.items()},
            "genders": genders,
            "door_unnamed": door_unnamed,
            # concurrent bodies in best single frame (never sum across buffer)
            "max_bodies_same_frame": max_bodies_same_frame,
            "count_rule": "same_frame_concurrent",
            "id_rule": "gender_split_then_best_of_bucket",
            "provisional": list(provisional_labels),
            "crowd_noise": "crowd_noise" in provisional_labels,
            "buffer_frames": len(frames),
            "face_frames": scored,
            "face_stride": FACE_STRIDE,
            "buffer_seq": self._buffer_seq,
            "detail": detail[:30],
            "frame_seq": self._buffer_seq,
            "capture_ts": newest.ts,
            "new_events": new_evs,
        }
        if new_evs:
            top = new_evs[-1]
            last_ev = {**last_ev, **top, "door_hold_meta": {k: last_ev[k] for k in last_ev}}

        # --- unconfirmed inventory → ghost rescan (minimal, the User 2026-07-23) ---
        # Full face match = name in door_names (best enrolled sim this buffer).
        # Gender match = LIVE soft M/F equals identity-store stamp for that person.
        # If neither for UNCONFIRMED_RESCAN_SEC (past entry grace) → multi-stop look.
        self._update_unconfirmed(
            now=now,
            full_matches=set(door_names),
            live_genders=live_genders_seen,
            stamps=stamps,
        )
        if self._should_ghost_rescan(now):
            self._pending_ghost_rescan = True
            last_ev["ghost_rescan_pending"] = True
            last_ev["unconfirmed"] = {
                n: round(now - t0, 1)
                for n, t0 in self._unconfirmed_since.items()
            }

        # Empty door FOV long enough → clear only *unconfirmed* ghosts (TV/poster).
        # Never timer-wipe id_certain people (Claude COORD 12:16 — ~90s certain drops).
        # Seated out-of-FOV is normal; leave via VL sub, exit_arm, or long sole_empty.
        live_face = bool(door_names) or bool(best)
        if live_face:
            self._empty_fov_since = None
        else:
            if self._empty_fov_since is None:
                self._empty_fov_since = now
            elif (
                STALE_EMPTY_LEAVE_SEC > 0
                and self.inventory.names()
                and (now - self._empty_fov_since) >= STALE_EMPTY_LEAVE_SEC
            ):
                doubted = [
                    n
                    for n in self.inventory.names()
                    if not self._id_certain.get(n, False)
                ]
                if doubted:
                    # DESIGN-OCCUPANCY-STREAM §5: timers output doubt, not
                    # deletion. Removal = evidence or ghost_recovery (law d).
                    for n in doubted:
                        self.inventory.raise_doubt(
                            n, now=now, reason="stale_empty_fov"
                        )
                    last_ev = {
                        **last_ev,
                        "kind": "doubt_raised",
                        "source": "stale_empty_fov",
                        "doubted": doubted,
                        "empty_sec": STALE_EMPTY_LEAVE_SEC,
                        "note": "trust demoted; ghost_recovery is the only "
                                "time-based remover",
                    }
                # reset timer so we don't spin
                self._empty_fov_since = None

        # the User 2026-08-11: count_trust tracks CAMERA CONTINUITY only.
        # Empty door while people sit at desk is NOT stale. Voice mismatch is
        # NOT stale. Stale = outage: frame age or sequential buffer gap.
        frame_ts_list = [
            float(getattr(f, "ts", 0) or 0)
            for f in frames
            if getattr(f, "ts", None) is not None
        ]
        trust = self._count_trust_from_camera(
            frame_age=frame_age, frame_ts_list=frame_ts_list
        )
        st = self._presence_from_inventory(
            frame_age=frame_age,
            camera_open=True,
            camera_blind=False,
            last_event=last_ev,
            count_trust=trust,
            camera_mode="holding",
            door_names=door_names,
        )
        st.count_certain = (
            trust == "fact"
            and st.unidentified == 0
            and all(p.name for p in st.people)
            and not self._unconfirmed_since
        )
        self.last_error = None
        self._set(st)
        self.cycles += 1
        self.door_cycles += 1
        self.last_cycle_at = now
        # Auto-reconcile: only-unknowns rooms sweep themselves back to names.
        # 2026-08-04: leave-on-return dropped the user's slot, the re-entry face
        # was never seen, and U1/U2 sat unauthed until a manual /v1/sweep.
        if AUTO_RECONCILE:
            stuck = bool(st.people) and not any(
                p.certain or p.id_status == "confirmed" for p in st.people
            )
            if not stuck:
                self._auto_reconcile_since = None
            elif self._auto_reconcile_since is None:
                self._auto_reconcile_since = now
            elif (
                now - self._auto_reconcile_since >= AUTO_RECONCILE_AFTER_SEC
                and now - self._auto_reconcile_last >= AUTO_RECONCILE_COOLDOWN_SEC
            ):
                self._auto_reconcile_last = now
                self._auto_reconcile_since = None
                try:
                    self.run_sweep_once(reason="auto_reconcile_unknowns")
                except Exception:
                    pass
        # G2 partnership: fire after room_event recorded by _set
        pend = self._pending_inspector_auto
        self._pending_inspector_auto = None
        if pend:
            self._inspector_auto(
                st,
                trigger=str(pend.get("trigger") or "enter"),
                person_id_hint=pend.get("person_id_hint"),
            )
        # Body stills: re-lock after ~1s of quiet frames
        try:
            self._drain_stillness_captures()
        except Exception:
            pass
        return st

    def _update_unconfirmed(
        self,
        *,
        now: float,
        full_matches: set[str],
        live_genders: set[str],
        stamps: dict,
    ) -> None:
        """Track inventory names lacking full face match and gender soft match."""
        present = set(self.inventory.names())
        # drop tracking for people who left
        for n in list(self._unconfirmed_since):
            if n not in present:
                self._unconfirmed_since.pop(n, None)
        for n in present:
            # still walking into room — empty door is expected
            if now < float(self.inventory.entry_grace_until.get(n, 0.0)):
                self._unconfirmed_since.pop(n, None)
                continue
            full = n in full_matches
            stamp_g = (stamps.get(n) or {}).get("gender")
            gender_ok = (
                stamp_g in ("M", "F") and stamp_g in live_genders
            )
            if full or gender_ok:
                self._unconfirmed_since.pop(n, None)
            else:
                self._unconfirmed_since.setdefault(n, now)

    def _should_ghost_rescan(self, now: float) -> bool:
        if not self._unconfirmed_since:
            return False
        if now - self._last_rescan_at < RESCAN_COOLDOWN_SEC:
            return False
        for n, t0 in self._unconfirmed_since.items():
            if now - t0 >= UNCONFIRMED_RESCAN_SEC:
                return True
        return False

    def _maybe_run_ghost_rescan(self) -> RoomPresence | None:
        """Doubt look-REQUEST — no pan, no removal (DESIGN-OCCUPANCY-STREAM §5).

        The old executor auto-panned the count path (sneak-in window) and
        force_left even id_certain people at ~2min (the 165s ghost-empty
        anatomy, F1). Now: raise doubt on the long-unconfirmed, demote trust,
        and publish a look request for VL voters / the operator. A voter
        answers through sanctioned POST /v1/vl_delta (law b); an operator
        reauth may pan (law c). Time-side removal is ghost_recovery only."""
        if not self._pending_ghost_rescan:
            return None
        self._pending_ghost_rescan = False
        now = time.time()
        if now - self._last_rescan_at < RESCAN_COOLDOWN_SEC:
            return None
        self._last_rescan_at = now
        doubted = [
            n
            for n, t0 in self._unconfirmed_since.items()
            if n in self.inventory.present
            and (now - t0) >= UNCONFIRMED_RESCAN_SEC
        ]
        for n in doubted:
            self.inventory.raise_doubt(n, now=now, reason="unconfirmed")
        if doubted:
            self._last_look_request = {
                "kind": "look_request",
                "reason": "unconfirmed",
                "names": doubted,
                "ages_sec": {
                    n: round(now - self._unconfirmed_since.get(n, now), 1)
                    for n in doubted
                },
                "ts": now,
                "note": "no pan on the count path — voters/operator answer "
                        "via /v1/vl_delta or reauth",
            }
        for n in list(self._unconfirmed_since):
            if n not in self.inventory.present:
                self._unconfirmed_since.pop(n, None)
        return None

    def _presence_from_inventory(
        self,
        *,
        frame_age: float,
        camera_open: bool,
        camera_blind: bool,
        last_event: dict,
        count_trust: str,
        camera_mode: str,
        door_names: list[str],
    ) -> RoomPresence:
        names = self.inventory.names()
        unnamed = self.inventory.unnamed
        # Occupancy mill off: do not publish mill people as live User.
        # The wall occupancyUser() treats certain the user in people[] as User
        # even when user_auth is false (guest boot).
        if not occupancy_on():
            names = []
            unnamed = 0
        # Gender/ID stamp: identity-store person.json is SoT for known people.
        # Live faceid is noise for known IDs — only fill gaps for unknowns.
        from .enrollments import load_person_stamps

        stamps = getattr(self, "_person_stamps", None)
        if stamps is None:
            stamps = load_person_stamps()
            self._person_stamps = stamps
        live_g = getattr(self, "_last_genders", {}) or {}
        sticky_g = getattr(self.inventory, "gender", {}) or {}
        slots: list[PersonSlot] = []
        for i, n in enumerate(names):
            at_door = n in door_names
            stamp = stamps.get(n) or {}
            # Stamped ID gender wins for enrolled household; live only if unstamped
            g = stamp.get("gender") or sticky_g.get(n) or live_g.get(n)
            if g in ("M", "F"):
                self.inventory.note_gender(n, g)
            else:
                g = None
            from .schema import format_last_seen

            _now_state = time.time()
            inv = self.inventory
            if _now_state < inv.entry_grace_until.get(n, 0.0):
                slot_state = "entering"
            elif n in inv.exit_armed and n in inv.leave_since:
                slot_state = "leaving"
            elif n in inv.exit_armed:
                slot_state = "armed"
            elif n in inv.doubt_since:
                slot_state = "doubted"
            else:
                slot_state = "present"
            seat_h = self._station_guess_for(n)
            sources: dict = {
                "inventory": True,
                "at_door": at_door,
                "gender": g,
                "state": slot_state,
                "station_guess": seat_h,
            }
            sources.update(format_last_seen(self.inventory.present.get(n)))
            if stamp.get("gender"):
                sources["gender_stamp"] = "house_id"
            if stamp.get("tier"):
                sources["tier"] = stamp["tier"]
            extra_src = self._id_sources.get(n) or {}
            sources.update({k: v for k, v in extra_src.items() if k not in sources})
            # Face at door this tick or prior confirmed → certain; soft ID may be ?
            face_now = at_door and n in (getattr(self, "_last_genders", {}) or {})
            if face_now or n in door_names:
                self._id_certain[n] = True
                sources["face"] = True
            certain = bool(self._id_certain.get(n, True))  # legacy named default certain
            # First soft-only introduce without face map entry stays provisional
            if n in self._id_certain:
                certain = bool(self._id_certain[n])
            id_status = "confirmed" if certain else "provisional"
            display = n if certain else f"{n}?"
            # Auth level for downstream (STT / voice / security) — from this object only
            auth = self._auth_for_person(n, sources=sources, certain=certain)
            tr = self.inventory.traits_live.get(n)
            visit = dict((getattr(self, "_oracle_visits", {}) or {}).get(n) or {})
            try:
                from .oracle_event import list_events

                # Only the still-open enter/sighting card. Merging a later
                # exit row overwrote robe/hair with the leave clip (s1's
                # cardigan became the man's gray shirt on 2026-08-17).
                open_cards = [
                    evr
                    for evr in list_events()
                    if str(evr.get("person_id") or "") == n
                    and evr.get("exit_ts") is None
                    and str(evr.get("event") or "").lower()
                    not in ("exit", "speaker", "security")
                    and str(evr.get("event_id") or "") != "ev_speaker"
                    and not str(evr.get("event_id") or "").startswith("ev_x")
                ]
                if open_cards:
                    evr = max(
                        open_cards,
                        key=lambda e: float(
                            e.get("enter_ts") or e.get("trigger_ts") or 0
                        ),
                    )
                    visit = {
                        **visit,
                        **{k: v for k, v in evr.items() if v is not None},
                    }
            except Exception:
                pass
            still_meta = self.inventory.enter_still.get(n) or {}
            n_still = int(still_meta.get("count") or len(still_meta.get("paths") or []))
            enter_still_pub = None
            if n_still > 0:
                enter_still_pub = {
                    "count": n_still,
                    "urls": [f"/v1/enter_still/{n}/{j}.jpg" for j in range(n_still)],
                }
            from .oracle_visit import short_id as _short_id

            slots.append(
                PersonSlot(
                    slot_id=f"s{i}",
                    name=n,
                    certain=certain,
                    id_status=id_status,
                    display=display,
                    gender=g or visit.get("sex"),
                    seat=seat_h,
                    sources=sources,
                    auth=auth,
                    traits_live=dict(tr) if tr else None,
                    enter_still=enter_still_pub,
                    short_id=str(visit.get("short_id") or _short_id(n)),
                    enter_ts=visit.get("enter_ts") or self.inventory.present.get(n),
                    exit_ts=visit.get("exit_ts"),
                    event=visit.get("event"),
                    visit=visit or None,
                )
            )
        # Provisional F1/F2/M1 — or single crowd_noise when F3+ (no certain F ids)
        prov = list(getattr(self, "_last_provisional", None) or [])
        if "crowd_noise" in prov:
            slots.append(
                PersonSlot(
                    slot_id="u0",
                    name="crowd_noise",
                    certain=False,
                    id_status="unknown",
                    display="crowd_noise",
                    gender=None,
                    sources={
                        "inventory": True,
                        "provisional": True,
                        "crowd_noise": True,
                        "label": "crowd_noise",
                        "note": "F3+ concurrent — abandon per-person F ids",
                    },
                )
            )
            # remaining unnamed beyond the crowd token (rare M provisional with crowd)
            extra = [p for p in prov if p != "crowd_noise"]
            for j, lab in enumerate(extra):
                g = lab[0] if lab and lab[0] in ("M", "F") else None
                slots.append(
                    PersonSlot(
                        slot_id=f"u{j+1}",
                        name=lab,
                        certain=False,
                        id_status="unknown",
                        display=lab,
                        gender=g,
                        sources={
                            "inventory": True,
                            "provisional": True,
                            "label": lab,
                            "gender": g,
                        },
                    )
                )
        else:
            for j in range(unnamed):
                lab = prov[j] if j < len(prov) else f"U{j+1}"
                g = lab[0] if lab and lab[0] in ("M", "F") else None
                uk = None
                if j < len(self.inventory.unnamed_keys):
                    uk = self.inventory.unnamed_keys[j]
                tr = self.inventory.traits_live.get(uk) if uk else None
                still_meta = (self.inventory.enter_still.get(uk) or {}) if uk else {}
                n_still = int(still_meta.get("count") or len(still_meta.get("paths") or []))
                enter_still_pub = None
                if uk and n_still > 0:
                    enter_still_pub = {
                        "count": n_still,
                        "urls": [
                            f"/v1/enter_still/{uk}/{k}.jpg" for k in range(n_still)
                        ],
                    }
                seat_h = self._station_guess_for(lab, uk, f"U{j+1}", f"u{j+1}")
                slots.append(
                    PersonSlot(
                        slot_id=f"u{j}",
                        name=lab,  # F1/F2 until real id emerges
                        certain=False,
                        id_status="unknown",
                        display=lab,
                        gender=g,
                        seat=seat_h,
                        sources={
                            "inventory": True,
                            "provisional": True,
                            "label": lab,
                            "gender": g,
                            "unnamed_key": uk,
                            "station_guess": seat_h,
                        },
                        traits_live=dict(tr) if tr else None,
                        enter_still=enter_still_pub,
                    )
                )
        # THE USER ALONE AT A FIXED CAMERA'S SEAT (2026-10-11, .101 round 9: headcount 0 and no sole_present on every
        # turn while he sat alone in view): one slot, certain, flagged fixed_cam_sole, so the ear's stamp may say
        # sole_present. Its auth record is the ordinary one (soft, or face only under a hold he already has): the
        # roster never grants AUTH (Law 1 unchanged). Off with RAIL_FIXED_CAM_SOLE=0; never on a camera that moves.
        fixed_sole = self._fixed_sole() if not names and not slots else None
        if fixed_sole:
            from .schema import format_last_seen

            uid = fixed_sole["uid"]
            fs_src: dict = {"fixed_cam_sole": True, "face_sim": round(float(fixed_sole["sim"]), 4),
                            "state": "present", "look": "oracle"}
            fs_src.update(format_last_seen(fixed_sole["ts"]))
            names = [uid]
            slots.append(
                PersonSlot(
                    slot_id="s0",
                    name=uid,
                    certain=True,
                    id_status="confirmed",
                    display=uid,
                    sources=fs_src,
                    auth=self._auth_for_person(uid, sources=dict(fs_src), certain=True),
                )
            )
        # Peaks always refreshed from inventory (LLM never owns headcount peak)
        self.inventory._touch_peaks()
        # Named occupancy only. Unnamed/provisional U-slots and crowd_noise
        # display; they are not a count vote (voice/buffer suggest, never count).
        hc = len(names)
        peaks = self.inventory.peaks_dict()
        auth_summary = self._auth_summary(slots)
        metrics = {
            "headcount": hc,
            "named": len(names),
            "unnamed": unnamed,
            "peak_headcount": peaks.get("headcount"),
            "session_peak_headcount": peaks.get("session_headcount"),
            "door_concurrent_peak": peaks.get("door_concurrent"),
            "count_trust": count_trust,
            "camera_mode": camera_mode,
        }
        lights_out, lights_block = self._lights_from_grabber()
        # Auth summary: while dark, face auth is unavailable (still soft/operator notes)
        if lights_out and isinstance(auth_summary, dict):
            auth_summary = dict(auth_summary)
            auth_summary["auth_available"] = False
            auth_summary["lights_out"] = True
            auth_summary["refusal"] = lights_block.get("refusal") or (
                "I can't verify who's there in the dark"
            )
        user_blk = self._user_auth_from_slots(slots, lights_out=lights_out)
        st = RoomPresence(
            camera_open=camera_open,
            camera_blind=camera_blind,
            frame_age_sec=round(frame_age, 3),
            headcount=hc,
            count_certain=False,
            people=slots,
            unidentified=unnamed,
            last_event=last_event,
            peaks=peaks,
            auth=auth_summary,
            metrics=metrics,
            writers=self._vl_writer_block(),
            pets=dict(self._pets),
            camera_mode=camera_mode,
            count_trust=count_trust,
            door_names=list(door_names),
            guards={
                "screen_body_veto": True,
                "vlm_no_think": True,
                "five_rules": True,
                "single_camera_mutex": True,
                "door_first": DOOR_FIRST,
            },
            lights_out=lights_out,
            lights=lights_block,
            user_id=user_blk["user_id"],
            user_present=user_blk["user_present"],
            user_face_sim=user_blk["user_face_sim"],
            user_face_sticky=bool(user_blk.get("user_face_sticky")),
            user_face_auth_source=user_blk.get("user_face_auth_source"),
            user_voice=user_blk["user_voice"],
            user_voice_sim=user_blk["user_voice_sim"],
            user_auth=user_blk["user_auth"],
            user_auth_reason=user_blk["user_auth_reason"],
            user_session_held=bool(user_blk.get("user_session_held")),
            user_session_opened=user_blk.get("user_session_opened"),
            user_voice_age_s=user_blk.get("user_voice_age_s"),
            user_strong_voice_age_s=user_blk.get("user_strong_voice_age_s"),
            user_auth_tier=str(user_blk.get("user_auth_tier") or "absent"),
            user_auth_age_s=user_blk.get("user_auth_age_s"),
            host_unlocked=bool(user_blk.get("host_unlocked")),
            user_granted_by=user_blk.get("granted_by"),
            user_product_tier=str(user_blk.get("product_tier") or "none"),
            user_face_age_s=user_blk.get("face_age_s"),
            sound=self._sound_pub(),
            visitor_today=self._visitor_today_pub(),
            seen_today=self._seen_today_pub(),
            strangers=self._strangers_pub(),
            visits=self._visits_pub(),
            room_busy=bool(getattr(self, "_room_busy", False)),
            busy_reason=str(getattr(self, "_busy_reason", "") or ""),
            busy_since=getattr(self, "_busy_since", None),
            ts=time.time(),
        )
        st.f2f = bool(getattr(self, "_f2f", False))
        st.f2f_since = getattr(self, "_f2f_since", None)
        st.f2f_reason = str(getattr(self, "_f2f_reason", "") or "")
        if st.f2f and not st.room_busy:
            st.camera_mode = "f2f"
        st.faceid = {
            k: v
            for k, v in (self.faceid_hud() or {}).items()
            if k not in ("has_frame",)
        }
        st.speaker = self._speaker_pub()
        if getattr(self, "_busy_unknown", False) and not st.room_busy:
            st.count_trust = "unknown"
        return st

    def _visits_pub(self) -> list[dict]:
        try:
            from .oracle_event import list_events

            return list_events()
        except Exception:
            return []

    def _strangers_pub(self) -> list[dict]:
        try:
            from .stranger_x import list_strangers

            return list_strangers()
        except Exception:
            return []

    def _visitor_today_pub(self) -> list[dict]:
        from .guests import visitor_day

        day = visitor_day()
        try:
            self.inventory._prune_visitor_today(day)
        except Exception:
            pass
        out: list[dict] = []
        for pid, meta in (getattr(self.inventory, "visitor_today", None) or {}).items():
            if not isinstance(meta, dict) or meta.get("day") != day:
                continue
            out.append(
                {
                    "person_id": str(pid),
                    "ts": meta.get("ts"),
                    "source": meta.get("source"),
                    "day": day,
                }
            )
        out.sort(key=lambda r: float(r.get("ts") or 0), reverse=True)
        return out

    def _visit_census(self, visitor_today: list | None = None) -> dict:
        """3 s memo shared with the lamp — one disk walk per publish, not two."""
        now = time.time()
        hit = getattr(self, "_visit_census_cache", None)
        if hit and (now - float(hit[0])) < 3.0:
            return hit[1]
        from .visitors_log import seen_since_midnight

        vt = visitor_today
        if vt is None:
            vt = self._visitor_today_pub()
        census = seen_since_midnight(visitor_today=vt)
        self._visit_census_cache = (now, census)
        return census

    def _seen_today_pub(self) -> dict:
        try:
            census = self._visit_census()
            return {
                "ok": True,
                "day": census.get("day"),
                "n": int(census.get("n") or 0),
                "user_seen": bool(census.get("user_seen")),
                "ids": list(census.get("ids") or []),
            }
        except Exception:
            return {
                "ok": False,
                "n": 0,
                "user_seen": False,
                "ids": [],
                "day": None,
            }

    def _visitor_day_log(self, pid: str) -> None:
        try:
            from .inspector import _memory_learn

            _memory_learn(
                "visitor",
                f"visitor today {pid} (voice label; do not call leave)",
                {"person_id": pid},
            )
        except Exception:
            pass

    def _faceid_say(
        self,
        what: str,
        uid: str,
        source: str,
        sim: float | None,
        *,
        rate_key: str | None = None,
    ) -> None:
        """One printed FaceID verdict per grant / refuse. Refusals are
        rate-limited per source (a door still repeats every tick)."""
        if rate_key:
            now = time.time()
            book = getattr(self, "_faceid_say_ts", None)
            if book is None:
                book = self._faceid_say_ts = {}
            if now - float(book.get(rate_key) or 0.0) < 10.0:
                return
            book[rate_key] = now
        try:
            sim_s = f"{float(sim):.3f}" if sim is not None else "none"
        except (TypeError, ValueError):
            sim_s = "none"
        print(f"[faceid] {what} {uid} src={source} sim={sim_s}", flush=True)

    def _note_face_auth(
        self,
        name: str,
        *,
        sim: float | None = None,
        source: str = "door_face",
        grant: bool = False,
    ) -> None:
        """Record sticky face badge (entry / reauth / buffer match).

        ``grant=True`` is the explicit grant API (``grant_user_face_auth``):
        its callers already did the FaceFinder look.

        AUTH-first: for the sudo User, first sticky open of a local day also
        runs Good Morning — covers entry_best_face, soft_id, chase, grant, etc.
        Day flag makes re-notes a no-op emit.
        """
        n = (name or "").strip().lower()
        # Reject PLACEHOLDERS only (u1/f2/m3, __uN, unknown-*, crowd*) — the
        # old first-letter guard ("u","f","m") silently ate every real name
        # starting with those letters, so another person could never hold a badge
        # (Ultra defect 13, fixed 2026-08-04).
        if not n or n.startswith(("unknown", "crowd", "__")):
            return
        from .guests import is_guest

        if is_guest(n):
            return
        if len(n) >= 2 and n[0] in ("u", "f", "m") and n[1:].isdigit():
            return
        prev = self._face_auth_hold.get(n) or {}
        was_held = bool(prev)
        src_s = str(source or "")
        if n == self._user_id() and not was_held and not grant:
            from . import auth_first as af

            # `_guest_until_secure` only skips the *auto* voice ladder
            # (restart must not desk-hunt). Guest = FF-for-AUTH: a real
            # FaceFinder look still mints. Door stills stay refused.
            if not af.is_ff_source(src_s):
                # Law 1 (VISIT-LAMP, the User 2026-09-05/06): all AUTH requires
                # FaceFinder + FaceID. From Guest, a door still, a ring or
                # FOV snapshot, a soft ID or an Oracle seat guess mints
                # nothing. Once FaceFinder minted the hold they may refresh
                # it (BAU FF tracking).
                why = (
                    "refuse guest_until_secure"
                    if getattr(self, "_guest_until_secure", False)
                    else "refuse guest_needs_ff"
                )
                self._faceid_say(why, n, src_s, sim, rate_key=src_s)
                return
        if n == self._user_id():
            self._visit_boot_unproven = False
        if n == self._user_id() and getattr(self, "_auth_blocked_until_recheck", False):
            # This call is the recheck (Busy-off desk snap or a later door face).
            self._auth_blocked_until_recheck = False
        keep_sim = sim
        if keep_sim is None:
            keep_sim = prev.get("sim")
        self._face_auth_hold[n] = {
            "sim": keep_sim,
            "ts": time.time(),
            # ``source`` is how AUTH was MINTED (the ledger's answer);
            # ``seen_via`` is the latest refresh.
            "source": prev.get("source") or source,
            "seen_via": source,
        }
        if n == self._user_id() and not was_held:
            self._faceid_say("AUTH grant", n, src_s, keep_sim)
        # First sticky transition for sudo User → first-of-day greeting.
        # Re-notes same day are suppressed by auth_first day flag.
        try:
            if n == self._user_id() and not was_held:
                self._maybe_first_auth_greeting(n)
        except Exception:
            pass

    def _auth_for_person(
        self,
        name: str,
        *,
        sources: dict | None = None,
        certain: bool = False,
    ) -> dict:
        """Build per-slot auth record — sole feed for other rails."""
        from .schema import AUTH_FACE, AUTH_NONE, AUTH_OPERATOR, AUTH_SOFT

        name = (name or "").strip().lower()
        sources = dict(sources or {})
        # Host lock drops User AUTH product (the User 2026-08-24). Occupancy
        # stays; do not refresh sticky from live_face or the ear would
        # stay BAU. Guest is evaluate_turn when A is false — one wire.
        if name == self._user_id():
            from . import auth_first as af

            if af.host_lock_state() is False:
                self._face_auth_hold.pop(name, None)
                self._user_session = None
                if hasattr(self, "_auth_levels"):
                    self._auth_levels.pop(name, None)
                return {
                    "level": AUTH_NONE,
                    "authed": False,
                    "sources": {
                        "face": False,
                        "voice": bool(
                            sources.get("voice")
                            or sources.get("soft_id")
                            or str(sources.get("vl_stamp") or "").startswith("soft")
                        ),
                        "operator": False,
                        "code": bool(sources.get("code")),
                        "face_sticky": False,
                    },
                    "person_id": name,
                    "sticky": False,
                }
        live_face = bool(
            sources.get("face")
            or sources.get("live_face")
            or sources.get("door_buffer")
        )
        # Live face this tick → refresh sticky badge
        if live_face or (
            certain
            and sources.get("face_sim") is not None
            and sources.get("at_door")
        ):
            try:
                sim = (
                    float(sources["face_sim"])
                    if sources.get("face_sim") is not None
                    else None
                )
            except (TypeError, ValueError):
                sim = None
            self._note_face_auth(
                name,
                sim=sim,
                source="live_face" if live_face else "at_door_certain",
            )
        hold = self._face_auth_hold.get(name)
        sticky_face = bool(hold)
        if sticky_face and sources.get("face_sim") is None and hold.get("sim") is not None:
            sources["face_sim"] = hold.get("sim")
            sources["face_sticky"] = True
            sources["face_auth_source"] = hold.get("source")
        src_flags = {
            # House User: the hold is the face flag (one object, Law 1).
            # Guests keep the live door face as their badge.
            "face": sticky_face if name == self._user_id() else (live_face or sticky_face),
            "voice": bool(
                sources.get("voice")
                or sources.get("soft_id")
                or str(sources.get("vl_stamp") or "").startswith("soft")
            ),
            "operator": bool(sources.get("operator") or sources.get("assert")),
            "code": bool(sources.get("code")),
            "face_sticky": sticky_face and not live_face,
        }
        # stored overrides from instruct
        stored = getattr(self, "_auth_levels", {}).get(name) or {}
        level = AUTH_NONE
        if stored.get("level") == AUTH_OPERATOR or src_flags["operator"]:
            level = AUTH_OPERATOR
        elif stored.get("level") == AUTH_FACE or src_flags["face"] or (
            certain and (src_flags["face"] or src_flags["code"])
        ):
            level = AUTH_FACE
        elif certain or src_flags["voice"] or src_flags["code"]:
            # named + certain without face = soft (not security)
            level = AUTH_FACE if src_flags["face"] else AUTH_SOFT
        if certain and src_flags["face"]:
            level = AUTH_FACE
        if sticky_face:
            level = AUTH_FACE
        if stored.get("level") == AUTH_OPERATOR:
            level = AUTH_OPERATOR
        return {
            "level": level,
            "authed": level in (AUTH_FACE, AUTH_OPERATOR),
            "sources": src_flags,
            "person_id": name,
            "sticky": sticky_face and not live_face,
        }

    def _auth_summary(self, slots: list) -> dict:
        """Top-level auth package for STT / voice / security consumers."""
        from .schema import AUTH_FACE, AUTH_OPERATOR

        authed = []
        soft = []
        for p in slots:
            a = p.auth if isinstance(p.auth, dict) else {}
            nm = p.name
            if not nm or str(nm).startswith(("U", "F", "M", "crowd", "unknown")):
                continue
            if a.get("authed") or a.get("level") in (AUTH_FACE, AUTH_OPERATOR):
                authed.append(nm)
            elif a.get("level") == "soft" or p.certain:
                soft.append(nm)
        return {
            "schema": "presence_auth/v1",
            "anyone_authed": bool(authed),
            "authed_ids": sorted(set(authed)),
            "soft_ids": sorted(set(soft)),
            "note": "All auths from this SoT object only — no side ledger",
            "for_downstream": True,
        }

    def _user_id(self) -> str:
        from .visit_lamp import user_id as _lamp_uid

        return _lamp_uid()

    def _user_auth_from_slots(
        self, slots: list, *, lights_out: bool
    ) -> dict:
        """Publish User face/voice/auth — AUTH-FIRST (2026-08-12).

        Control plane is face sticky / session for the sudo User, **not**
        inventory headcount or Oracle. Inventory may lag or be empty; AUTH
        still holds until strong leave (_note_left), lights_out, or host lock.
        Lock drops AUTH. Unlock is a Face grab, not a grant.
        """
        uid = self._user_id()
        from . import auth_first as af

        lock_state = af.host_lock_state()
        host_unlocked = lock_state is True
        prev_lock = getattr(self, "_prev_host_lock_state", None)
        self._prev_host_lock_state = lock_state
        host_locked = lock_state is False
        if (not lights_out) and prev_lock is False and lock_state is True:
            try:
                self._kick_unlock_face_grab()
            except Exception:
                pass

        face_ok = False
        face_sim: float | None = None
        face_sticky = False
        named_on_roster = False
        for p in slots:
            nm = (p.name or "").strip().lower()
            if nm != uid:
                continue
            named_on_roster = True
            a = p.auth if isinstance(p.auth, dict) else {}
            src = p.sources if isinstance(p.sources, dict) else {}
            try:
                if src.get("face_sim") is not None:
                    face_sim = float(src["face_sim"])
            except (TypeError, ValueError):
                pass
            if a.get("level") in ("face", "operator") or a.get("authed"):
                face_ok = True
                if a.get("sticky") or src.get("face_sticky"):
                    face_sticky = True
            # A certain door face on the roster is not AUTH for the User —
            # the hold is (Law 1). Guests are not this block.
        # AUTH-first: sticky hold alone is enough (no inventory +1 required)
        hold = self._face_auth_hold.get(uid)
        hold_src = str((hold or {}).get("source") or "")
        if host_locked:
            self._face_auth_hold.pop(uid, None)
            if hasattr(self, "_auth_levels"):
                self._auth_levels.pop(uid, None)
            hold = None
            face_sticky = False
        # AUTH SoT is this hold (FaceID / grant_user_face_auth). Sage HOLDS
        # it for everything (the User 2026-09-09) — empty door FOV must not Guest
        # the User while the lamp is green. Colour is not a second AUTH.
        if (not host_locked) and (not lights_out) and self._visit_is_secure():
            hold = self._hold_auth_for_secure()
            face_ok = True
            face_sticky = True
        elif hold:
            why = None
            try:
                why = self._insecure_auth_expiry(hold)
            except Exception:
                why = None
            if why:
                self._face_auth_hold.pop(uid, None)
                if hasattr(self, "_auth_levels"):
                    self._auth_levels.pop(uid, None)
                self._user_session = None
                hold = None
                face_ok = False
                face_sticky = False
                print(f"[auth] sticky hold expired reason={why} src={hold_src}", flush=True)
        if hold:
            face_ok = True
            face_sticky = True
            if face_sim is None and hold.get("sim") is not None:
                try:
                    face_sim = float(hold["sim"])
                except (TypeError, ValueError):
                    pass
        # Fresh-voice window: tracked for ear risk tiers; does not drop AUTH
        lv = getattr(self, "_last_user_voice", {}) or {}
        voice_age = time.time() - float(lv.get("ts") or 0)
        voice_ok = bool(lv.get("match")) and voice_age < 30.0
        voice_sim = lv.get("sim")
        try:
            voice_sim = float(voice_sim) if voice_sim is not None else None
        except (TypeError, ValueError):
            voice_sim = None

        # Session OPEN: face sticky (inventory optional)
        # HOLD until sticky cleared by strong leave / lights_out / host lock
        sess = self._user_session
        if host_locked or lights_out:
            self._user_session = None
            sess = None
        elif face_ok and not sess:
            sess = self._user_session = {
                "opened_ts": time.time(),
                "last_strong_voice_ts": float(lv.get("ts") or time.time())
                if voice_ok
                else None,
                "last_strong_voice_sim": voice_sim if voice_ok else None,
                "opened_via": "face_sticky" if face_sticky else "face",
            }
        elif sess and voice_ok:
            sess["last_strong_voice_ts"] = float(lv.get("ts") or time.time())
            sess["last_strong_voice_sim"] = voice_sim
        if sess and not face_ok:
            sess = self._user_session = None

        if lights_out:
            reason = "lights_out"
            self._face_auth_hold.pop(uid, None)
            self._user_session = None
            sess = None
            face_ok = False
            voice_ok = False
            user_auth = False
            face_sticky = False
        elif getattr(self, "_room_busy", False):
            reason = "room_busy"
            self._face_auth_hold.pop(uid, None)
            self._user_session = None
            sess = None
            face_ok = False
            voice_ok = False
            user_auth = False
            face_sticky = False
        elif (
            getattr(self, "_auth_blocked_until_recheck", False)
            and not hold
        ):
            reason = "busy_await_recheck"
            self._user_session = None
            sess = None
            face_ok = False
            user_auth = False
            face_sticky = False
        elif host_locked:
            # Occupancy stays. AUTH product off → ear guest (evaluate_turn
            # not present_a). Do not invent a second guest dispatcher.
            reason = "host_locked"
            self._face_auth_hold.pop(uid, None)
            self._user_session = None
            sess = None
            voice_ok = False
            user_auth = False
            face_sticky = False
            face_ok = False
        elif not face_ok:
            reason = "user_absent"
            user_auth = False
        elif sess or (face_ok and face_sticky):
            if not sess and face_ok and face_sticky:
                sess = self._user_session = {
                    "opened_ts": time.time(),
                    "last_strong_voice_ts": None,
                    "last_strong_voice_sim": None,
                    "opened_via": "face_sticky",
                }
            user_auth = True
            if voice_ok:
                reason = "ok"
            elif lv.get("match") is False and lv.get("ts") and voice_age < 30.0:
                # Sticky AUTH stays on (User in the chair). Tier becomes
                # contested so the sheet can drop without AUTH-off.
                reason = "wrong_voice"
            elif sess and (sess.get("opened_via") or "") != "face_sticky":
                reason = "session_held"
            else:
                reason = "face_sticky" if face_sticky else "session_held"
        elif face_ok and not voice_ok:
            if voice_age < 30.0 and lv.get("match") is False and lv.get("ts"):
                reason = "wrong_voice"
            else:
                reason = "await_voice"
            user_auth = False
        else:
            reason = "ok"
            user_auth = True

        strong_ts = float((sess or {}).get("last_strong_voice_ts") or 0)
        hold_ts = None
        if hold and hold.get("ts") is not None:
            try:
                hold_ts = float(hold["ts"])
            except (TypeError, ValueError):
                hold_ts = None
        lv_ts = None
        if lv.get("ts"):
            try:
                lv_ts = float(lv["ts"])
            except (TypeError, ValueError):
                lv_ts = None
        src = str((hold or {}).get("source") or "")
        granted_by = (
            "operator"
            if "operator" in src.lower()
            else ("face" if face_sticky else None)
        )
        tier_blk = af.compute_auth_tier(
            user_auth=bool(user_auth),
            face_sticky=bool(face_sticky),
            hold_ts=hold_ts,
            last_voice_match=bool(lv.get("match")),
            last_voice_ts=lv_ts,
            reason=reason,
        )
        prov = af.compute_auth_provenance(
            face_sticky=bool(face_sticky),
            granted_by=granted_by,
            hold_ts=hold_ts,
            last_voice_match=bool(lv.get("match")),
            last_voice_ts=lv_ts,
            this_turn_user_voice=bool(voice_ok),
        )
        return {
            "user_id": uid,
            "user_present": face_ok,
            "user_face_sim": face_sim,
            "user_voice": voice_ok,
            "user_voice_sim": voice_sim,
            "user_auth": user_auth,
            "user_auth_reason": reason,
            "user_face_sticky": face_sticky,
            "user_face_auth_source": (hold or {}).get("source") if face_sticky else None,
            "user_session_held": bool(sess),
            "user_session_opened": (sess or {}).get("opened_ts"),
            "user_voice_age_s": round(voice_age, 1) if lv.get("ts") else None,
            "user_strong_voice_age_s": (
                round(time.time() - strong_ts, 1) if strong_ts else None
            ),
            "named_on_roster": named_on_roster,
            "auth_plane": "face_sticky",  # AUTH-first control plane label
            "user_auth_tier": tier_blk.get("auth_tier") or "absent",
            "user_auth_age_s": tier_blk.get("auth_age_s"),
            "host_unlocked": host_unlocked,
            "granted_by": prov.get("granted_by"),
            "product_tier": prov.get("tier") or "none",
            "face_age_s": prov.get("face_age_s"),
            "auth_tier_meta": {
                k: tier_blk.get(k)
                for k in ("fresh_sec", "stale_sec", "enforce", "face_age_s", "voice_age_s")
            },
        }

    @staticmethod
    def sound_id_level(
        id_source: str | None,
        person_id: str | None,
        *,
        certain: bool | None = None,
        voice_sim: float | None = None,
    ) -> str:
        """Ring initial: strong or none. Tough to trick.

        Only a voice-emb stamp may name the lobe. sole_present / vocative /
        is_user_block never get a letter. Sim at/above RAIL_SOUND_INIT_SIM
        (default 0.55) — ``certain`` is not a bypass. AUTH-held User is the
        product; anyone else is a bonus on the same bar.
        """
        if not person_id:
            return "none"
        src = str(id_source or "").strip().lower()
        if src in ("scratch_s1", "scratch_s2", "scratch_voice") or src.startswith(
            "scratch_"
        ):
            # Same-unknown, fairly sure — flash S1/S2. Opening a new slot
            # has no prior sim; still flash once (guess if sure).
            if certain is True:
                return "strong"
            try:
                floor = float(os.environ.get("RAIL_SOUND_INIT_SIM", "0.55"))
            except (TypeError, ValueError):
                floor = 0.55
            try:
                sim = float(voice_sim) if voice_sim is not None else None
            except (TypeError, ValueError):
                sim = None
            if sim is None or sim >= min(0.50, floor):
                return "strong"
            return "none"
        if src not in ("voice_emb", "voice_emb_vs_sole"):
            return "none"
        # Voice guess may name the lobe before FaceID. Floor still applies
        # when certain=True — a ranked pid is not a strong letter.
        try:
            floor = float(os.environ.get("RAIL_SOUND_INIT_SIM", "0.55"))
        except (TypeError, ValueError):
            floor = 0.55
        try:
            sim = float(voice_sim) if voice_sim is not None else None
        except (TypeError, ValueError):
            sim = None
        if sim is not None and sim >= floor:
            return "strong"
        return "none"

    @staticmethod
    def _beam_hour_if_mapped(az_deg: float) -> tuple[int | None, int | None]:
        """Publish beam_hour only after door residual + desk check (R4 G5)."""
        raw = (os.environ.get("HOUSE_MIC_AZ_RESIDUAL_DEG") or "").strip()
        if not raw:
            return None, None
        try:
            residual = float(raw)
        except (TypeError, ValueError):
            return None, None
        try:
            desk_az = float(os.environ.get("HOUSE_MIC_AZ_DESK_DEG", "39"))
        except (TypeError, ValueError):
            desk_az = 39.0
        desk_cam = (desk_az - residual) % 360.0
        if not (225.0 <= desk_cam < 255.0):
            return None, None
        cam = (float(az_deg) - residual) % 360.0
        hour = int(round(cam / 30.0)) % 12
        hour = 12 if hour == 0 else hour
        legal = hour if hour not in {4, 5, 6, 7, 9} else None
        return hour, legal

    def _apply_sound_heard(self, body: dict) -> RoomPresence:
        """Write ``sound`` only. Never last_event, roster, session, sticky, VL."""
        now = time.time()
        last = getattr(self, "_sound", None) or {}
        try:
            last_ts = float(last.get("ts") or 0.0)
        except (TypeError, ValueError):
            last_ts = 0.0
        try:
            min_gap = float(os.environ.get("RAIL_SOUND_MIN_GAP_SEC", "0.5"))
        except (TypeError, ValueError):
            min_gap = 0.5
        # Live puck beam is a constant feed (Oracle lobe ~5 Hz). Don't eat it.
        if str(body.get("source") or "") == "xvf3800":
            min_gap = min(min_gap, 0.15)
        pid = body.get("person_id")
        pid_s = str(pid).strip().lower() if pid else None
        if pid_s in ("", "none", "null"):
            pid_s = None
        id_source = str(
            body.get("id_source") or body.get("stamp_source") or ""
        ).strip().lower() or None
        certain = body.get("certain")
        if certain is not None:
            certain = bool(certain)
        vsim = body.get("voice_emb_sim")
        if vsim is None:
            vsim = body.get("sim")
        try:
            vsim_f = float(vsim) if vsim is not None else None
        except (TypeError, ValueError):
            vsim_f = None
        # Identity-bearing posts must never be eaten by the az rate limit.
        # Only a real voice/scratch name — sole_present / vocative / stale
        # unlabeled are NOT identity and must not bump a last strong id.
        src_l = id_source or ""
        identity = bool(pid_s) and (
            src_l in ("voice_emb", "voice_emb_vs_sole") or src_l.startswith("scratch_")
        )
        az_raw = body.get("az_deg")
        if az_raw is None:
            az_raw = body.get("beam_angle")
        if az_raw is None and isinstance(last, dict):
            az_raw = last.get("az_deg")
        try:
            az_f = float(az_raw) % 360.0
        except (TypeError, ValueError):
            return self.get()
        conf_raw = body.get("conf")
        if conf_raw is None:
            conf_raw = body.get("beam_conf")
        try:
            conf_f = float(conf_raw) if conf_raw is not None else None
        except (TypeError, ValueError):
            conf_f = None
        try:
            ts = float(body["ts"]) if body.get("ts") is not None else now
        except (TypeError, ValueError):
            ts = now
        impulse = bool(body.get("impulse"))
        if last_ts and (now - last_ts) < min_gap and not identity and not impulse:
            if body.get("speech") and getattr(self, "_f2f", False):
                self._beam_speech_ts = now
            return self.get()
        sound = {
            "az_deg": round(az_f, 1),
            "conf": round(conf_f, 3) if conf_f is not None else None,
            "ts": ts,
            "source": str(body.get("source") or "xvf3800"),
            "frame": "mic",
        }
        id_level = self.sound_id_level(
            id_source, pid_s, certain=certain, voice_sim=vsim_f
        )
        sound["id_level"] = id_level
        if id_source:
            sound["id_source"] = id_source
        # Name the lobe only when id_level is not none. Identity keeps its
        # own id_ts — puck ticks must not refresh it.
        if pid_s and id_level != "none":
            sound["person_id"] = pid_s
            sound["id_ts"] = ts
        elif not pid_s and isinstance(last, dict) and last.get("id_ts") is not None:
            for k in ("person_id", "id_level", "id_source", "id_ts"):
                if last.get(k) is not None:
                    sound[k] = last[k]
        hour, legal = self._beam_hour_if_mapped(az_f)
        if hour is not None:
            sound["beam_hour"] = hour
        if legal is not None:
            sound["beam_legal"] = legal
        if body.get("speech") is not None:
            sound["speech"] = bool(body.get("speech"))
        self._sound = sound
        st = self.get()
        try:
            st.sound = self._sound_pub()
        except Exception:
            pass
        self._set(st)
        # Beam is FF look clue, not a Speaker-stack vote. Session look owns the station.
        # sound.heard used to re-aim from az during f2f and yank the desk look
        # back toward the door.
        if getattr(self, "_f2f", False) and (impulse or body.get("speech")):
            self._beam_speech_ts = now
        return st

    def apply_instruction(self, body: dict) -> RoomPresence:
        """Unified instruction port into the SoT object.

        Every instruction updates metrics / roster / auth on *this* object.
        Shape: {op, ...fields} — see docs/CONTRACT-SOT-INSTRUCT.md
        """
        op = str(body.get("op") or body.get("instruction") or "").strip().lower()
        source = str(body.get("source") or "instruct")
        if not op:
            raise ValueError("need op")

        # Mic-frame hearing — must return before roster / session / sticky / VL.
        if op in ("sound.heard", "sound_heard", "heard"):
            return self._apply_sound_heard(body)
        if op in ("sound.clear", "sound_clear"):
            self._sound = None
            st = self.get()
            try:
                st.sound = None
            except Exception:
                pass
            return st

        # --- count physics (VL / clicker) ---
        if op in ("count.add", "vl.add", "enter"):
            n = max(1, int(body.get("n") or body.get("entered") or 1))
            return self.apply_vl_delta(
                n,
                0,
                source=source,
                stamp_names=body.get("stamp_names") or body.get("names"),
                note=body.get("note"),
                face_certain=bool(body.get("face_certain")),
                force=bool(body.get("force")),
            )
        if op in ("count.sub", "vl.sub", "leave_count"):
            n = max(1, int(body.get("n") or body.get("left") or 1))
            return self.apply_vl_delta(
                0,
                n,
                source=source,
                note=body.get("note"),
                force=bool(body.get("force")),
            )
        if op in ("count.null", "vl.null"):
            return self.get()

        # Ranked voice ID for Speaker/HUD. Parallel with FaceID. Not occupancy.
        if op in ("speaker.voice", "voice.speaker"):
            pid = str(body.get("person_id") or body.get("name") or "").strip().lower() or None
            try:
                sim = (
                    float(body["voice_emb_sim"])
                    if body.get("voice_emb_sim") is not None
                    else (
                        float(body["sim"])
                        if body.get("sim") is not None
                        else None
                    )
                )
            except (TypeError, ValueError):
                sim = None
            match = body.get("match")
            if match is None:
                match = bool(pid and sim is not None)
            self._last_spoke_voice = {
                "match": bool(match),
                "sim": sim,
                "ts": time.time(),
                "person_id": pid,
                "source": source,
            }
            return self.get()

        # --- User voice stamp (ears → Oracle user_auth) ---
        # {op: "user.voice", person_id, voice_emb_sim, match?}
        if op in ("user.voice", "user_voice", "voice.user"):
            uid = self._user_id()
            pid = str(body.get("person_id") or body.get("name") or "").strip().lower()
            try:
                sim = (
                    float(body["voice_emb_sim"])
                    if body.get("voice_emb_sim") is not None
                    else (
                        float(body["sim"])
                        if body.get("sim") is not None
                        else None
                    )
                )
            except (TypeError, ValueError):
                sim = None
            thr = float(os.environ.get("HOUSE_USER_VOICE_MIN_SIM", "0.48"))
            if body.get("match") is not None:
                match = bool(body.get("match"))
            else:
                match = bool(pid == uid and sim is not None and sim >= thr)
            self._last_user_voice = {
                "match": match,
                "sim": sim,
                "ts": time.time(),
                "person_id": pid or None,
                "source": source,
            }
            # Missed-entry re-AUTH ladder (the User 2026-08-04): his voice is
            # here but his face badge isn't — go GET the face in the
            # background instead of answering user_absent forever.
            if (
                match
                and uid not in self._face_auth_hold
                and not self._auth_hunt_blocked_locked()
            ):
                nowt = time.time()
                if nowt - getattr(self, "_reauth_ladder_ts", 0.0) > 120.0:
                    self._reauth_ladder_ts = nowt
                    threading.Thread(
                        target=self._run_reauth_ladder, args=(uid,),
                        daemon=True,
                    ).start()
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=cur.camera_open,
                camera_blind=cur.camera_blind,
                last_event={
                    "kind": "user_voice",
                    "person_id": pid,
                    "match": match,
                    "sim": sim,
                    "source": source,
                    "ts": time.time(),
                },
                count_trust=cur.count_trust or "fact",
                camera_mode=cur.camera_mode or "holding",
                door_names=list(cur.door_names or []),
            )
            self._set(st)
            return st

        # --- writer-mode control (presence-perfect §3.4; no restart) ---
        # {op: "vl.mode", source: <writer>, mode: shadow|subs_only|full|clear}
        # Overrides do NOT survive restart — env is the durable config.
        if op in ("vl.mode", "vl_mode"):
            target = self._vl_src(
                body.get("source") or body.get("writer") or "")
            mode = str(body.get("mode") or "").strip().lower()
            if not target or target == "unknown":
                raise ValueError("need source (writer id)")
            if mode not in ("shadow", "subs_only", "full", "clear"):
                raise ValueError("mode must be shadow|subs_only|full|clear")
            now = time.time()
            if mode == "clear":
                self._vl_mode_override.pop(target, None)
            else:
                self._vl_mode_override[target] = mode
            eff_mode, eff_src = self._vl_mode(target)
            ev = {
                "kind": "vl_mode_set", "source": target, "mode": mode,
                "effective_mode": eff_mode, "effective_from": eff_src,
                "by": "operator", "ts": now,
            }
            self.inventory.events.append(ev)
            DoorInventory._mirror_event(
                ev, history=door_watch_module.OCCUPANCY_HISTORY)
            cur = self.get()
            st = self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=cur.camera_open,
                camera_blind=cur.camera_blind,
                last_event=ev,
                count_trust=cur.count_trust or "fact",
                camera_mode=cur.camera_mode or "holding",
                door_names=list(cur.door_names or []),
            )
            self._set(st)
            return st

        # --- roster ---
        if op in ("roster.assert", "assert", "present"):
            names = body.get("names") or body.get("people") or []
            if body.get("name"):
                names = list(names) + [body.get("name")]
            st = self.assert_present(
                list(names),
                unnamed=body.get("unnamed"),
                source=source,
                replace=bool(body.get("replace")),
            )
            # operator assert → operator auth on named
            if not hasattr(self, "_auth_levels"):
                self._auth_levels = {}
            for n in names:
                n = (n or "").strip().lower()
                if n:
                    self._auth_levels[n] = {"level": "operator", "source": source}
                    self._id_certain[n] = True
                    src = dict(self._id_sources.get(n) or {})
                    src["operator"] = True
                    src["assert"] = True
                    self._id_sources[n] = src
            return self._presence_from_inventory(
                frame_age=st.frame_age_sec,
                camera_open=st.camera_open,
                camera_blind=st.camera_blind,
                last_event={
                    **(st.last_event or {}),
                    "kind": "instruct",
                    "op": op,
                    "source": source,
                },
                count_trust=st.count_trust or "fact",
                camera_mode=st.camera_mode or "holding",
                door_names=list(st.door_names or []),
            )
        if op in ("roster.leave", "leave"):
            names = body.get("names") or body.get("people") or []
            if body.get("name"):
                names = list(names) + [body.get("name")]
            return self.assert_leave(list(names), source=source)

        # --- identity / auth ---
        if op in ("id.soft", "soft_id"):
            pid = body.get("person_id") or body.get("name")
            if not pid:
                raise ValueError("need person_id")
            return self.apply_soft_id(
                str(pid),
                certain=bool(body.get("certain")),
                source=source,
                confirm=bool(body.get("confirm")),
                create_if_missing=bool(body.get("create_if_missing", True)),
            )
        if op in ("auth.grant", "auth.face", "auth.operator"):
            pid = str(body.get("person_id") or body.get("name") or "").strip().lower()
            if not pid:
                raise ValueError("need person_id")
            level = "operator" if op == "auth.operator" or body.get("level") == "operator" else "face"
            if not hasattr(self, "_auth_levels"):
                self._auth_levels = {}
            self._auth_levels[pid] = {"level": level, "source": source, "ts": time.time()}
            self._id_certain[pid] = True
            src = dict(self._id_sources.get(pid) or {})
            src[level] = True
            if level == "face":
                src["face"] = True
            if level == "operator":
                src["operator"] = True
            self._id_sources[pid] = src
            # ensure on roster if missing and create — never mint a guest body
            from .guests import is_guest

            if is_guest(pid):
                ev = self.inventory.note_visitor(pid, now=time.time(), source=source)
                if ev:
                    self._visitor_day_log(pid)
            elif pid not in self.inventory.present and body.get("create_if_missing", True):
                self.inventory.present[pid] = time.time()
                self.inventory.baseline_set = True
                self.inventory._touch_peaks()
            cur = self.get()
            return self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=cur.camera_open,
                camera_blind=cur.camera_blind,
                last_event={
                    "kind": "instruct",
                    "op": op,
                    "person_id": pid,
                    "level": level,
                    "source": source,
                    "ts": time.time(),
                },
                count_trust=cur.count_trust or "fact",
                camera_mode=cur.camera_mode or "holding",
                door_names=list(cur.door_names or []),
            )
        if op in ("auth.revoke",):
            pid = str(body.get("person_id") or body.get("name") or "").strip().lower()
            if not pid:
                raise ValueError("need person_id")
            if hasattr(self, "_auth_levels"):
                self._auth_levels.pop(pid, None)
            self._id_certain[pid] = False
            cur = self.get()
            return self._presence_from_inventory(
                frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
                camera_open=cur.camera_open,
                camera_blind=cur.camera_blind,
                last_event={
                    "kind": "instruct",
                    "op": op,
                    "person_id": pid,
                    "source": source,
                    "ts": time.time(),
                },
                count_trust=cur.count_trust or "fact",
                camera_mode=cur.camera_mode or "holding",
                door_names=list(cur.door_names or []),
            )

        raise ValueError(f"unknown op: {op}")

    def _idle_gate(self):
        """The door watch's rest (crissa_presence.idle_gate), made on first use."""
        g = getattr(self, "_idle_gate_obj", None)
        if g is None:
            from .idle_gate import IdleGate
            g = self._idle_gate_obj = IdleGate()
        return g

    def _laptop(self):
        """The laptop face check (crissa_presence.laptop_auth, round 27) when it applies here, else None."""
        try:
            from . import laptop_auth

            if not laptop_auth.on(self):
                return None
            la = getattr(self, "_laptop_auth", None)
            if la is None:
                la = self._laptop_auth = laptop_auth.LaptopAuth()
                print("[laptop-auth] fixed camera: no door watch; a face check every 30-60 s", flush=True)
            return la
        except Exception as e:  # noqa: BLE001  round 28: said once, never silent (round 27 shipped without the file)
            if not getattr(self, "_laptop_auth_err", None):
                self._laptop_auth_err = repr(e)
                print("[laptop-auth] NOT LOADED, the door watch runs instead: %r" % (e,), flush=True)
            return None

    def _laptop_tick(self, la) -> None:
        """One pass of the presence loop on a laptop: a face check when due; the loop's own clocks kept."""
        if la.due():
            got = la.check(self)
            if got.get("checked"):
                try:
                    st = self.get()
                    st.camera_open = True
                    st.camera_blind = False
                    st.frame_age_sec = 0.0
                    st.camera_mode = "laptop"
                    self._set(st)
                except Exception:
                    pass
        if la.session_rule():                       # round 28: Security On on a laptop is a 30 s session
            la.session_tick(self)
        self.cycles += 1
        self.last_cycle_at = time.time()
        self.last_error = None

    def _laptop_republish(self, kind: str) -> None:
        """Publish presence again after the laptop session changed AUTH (as grant_user_face_auth does)."""
        cur = self.get()
        st = self._presence_from_inventory(
            frame_age=cur.frame_age_sec if cur.camera_open else 999.0,
            camera_open=cur.camera_open,
            camera_blind=cur.camera_blind,
            last_event={"kind": kind, "uid": self._user_id(), "ts": time.time(), "oracle_used": False},
            count_trust=cur.count_trust or "fact",
            camera_mode=cur.camera_mode or "laptop",
            door_names=list(cur.door_names or []),
        )
        self._set(st)

    def _laptop_loop(self) -> bool:
        """The presence loop on a laptop (round 27) while it applies; False when the camera turns (the door watch)."""
        ran = False
        while not self._stop.is_set():
            try:
                self._grabber_fresh(max_age=10.0)   # the camera's kind, for _fixed_cam
            except Exception:
                pass
            la = self._laptop()
            if la is None:
                return ran
            ran = True
            try:
                self._laptop_tick(la)
            except Exception as e:
                self.last_error = f"laptop: {e}"[:300]
            self._stop.wait(2.0)
        return ran

    def _loop(self) -> None:
        self._laptop_loop()
        if self._stop.is_set():
            return
        # Cold path: door home, then door buffer — do NOT block minutes on
        # baseline smooth_orient (CPU faceid + multi-stop hung the loop and
        # starved motion/VL for whole sessions). Optional orient via env=0.
        if DOOR_FIRST:
            try:
                self._ensure_door_aim()
                use_ptz = os.environ.get("RAIL_PRESENCE_USE_PTZ_SWEEP", "1") not in (
                    "0",
                    "false",
                    "no",
                )
                if (
                    use_ptz
                    and not SKIP_BASELINE_ORIENT
                    and self.camera.mode in ("http", "auto", "v4l")
                ):
                    self.run_smooth_orient_once(reason="baseline")
                else:
                    self.run_door_once()
            except Exception as e:
                self.last_error = f"baseline: {e}"[:300]

        while not self._stop.is_set():
            if self._laptop() is not None:          # the camera is known now not to turn (round 27)
                self._laptop_loop()
                continue
            t0 = time.time()
            try:
                if DOOR_FIRST:
                    if self._mode == "away":
                        self.run_door_once()  # marks stale
                    else:
                        self.run_door_once()
                        try:
                            self._faceid_heartbeat()
                        except Exception:
                            pass
                        # Ghost rescan is expensive; only if not mid-stall
                        if (time.time() - t0) < 5.0 and occupancy_on():
                            self._maybe_run_ghost_rescan()
                else:
                    self.run_smooth_orient_once(reason="legacy_timer")
            except Exception as e:
                self.last_error = str(e)[:300]
                try:
                    self._ensure_door_aim(release_facelock=True)
                except Exception:
                    pass
                st = empty_room(camera_open=False)
                st.last_event = {"kind": "error", "error": self.last_error}
                st.camera_mode = self._mode
                st.count_trust = "stale"
                self._set(st)
            elapsed = time.time() - t0
            interval = self._idle_gate().interval(DOOR_INTERVAL) if DOOR_FIRST else float(
                os.environ.get("RAIL_PRESENCE_INTERVAL", "20")
            )
            self._stop.wait(max(0.2, interval - elapsed))

    def health(self) -> dict:
        st = self.get()
        cycle_age = (
            round(time.time() - self.last_cycle_at, 1)
            if self.last_cycle_at
            else None
        )
        grabber_ok = True
        if self.camera.mode in ("http", "auto") and self.last_grabber is not None:
            grabber_ok = bool(self.last_grabber.get("ok"))
        capture_fresh = (not st.camera_blind) and st.frame_age_sec <= 8.0
        # door-first: seq may stall briefly; allow more room than 8s tour
        stall_limit = 20 if DOOR_FIRST else 5
        seq_ok = self.seq_stall_cycles < stall_limit
        max_cycle = 30 if DOOR_FIRST else 90
        ok = (
            self.cycles >= 1
            and cycle_age is not None
            and cycle_age < max_cycle
            and grabber_ok
            and (self.camera.mode == "dir" or capture_fresh or st.camera_blind)
            and seq_ok
            and self.last_error is None
        )
        if self.camera.mode == "dir":
            ok = (
                self.cycles >= 1
                and cycle_age is not None
                and cycle_age < max_cycle
                and self.last_error is None
            )
        from .schema import enrich_timestamps, epoch_to_iso, epoch_to_iso_utc

        payload = {
            "ok": ok,
            "service": "presence",
            "schema": st.schema,
            "door_first": DOOR_FIRST,
            "camera_mode": st.camera_mode,
            "count_trust": st.count_trust,
            "cycles": self.cycles,
            "door_cycles": self.door_cycles,
            "sweep_cycles": self.sweep_cycles,
            "buffer_seq": self._buffer_seq,
            "last_buffer_n": self.last_buffer_n,
            "door_rest": self._idle_gate().status(),
            "laptop_auth": (getattr(self, "_laptop_auth", None).status()
                            if getattr(self, "_laptop_auth", None) is not None else None),       # the door watch at rest when the room is empty (2026-10-10)
            "last_face_frames": self.last_face_frames,
            "face_stride": FACE_STRIDE,
            "use_buffer": USE_BUFFER,
            "uptime_sec": round(time.time() - self.started_at, 1),
            "started_at": self.started_at,
            "cycle_age_sec": cycle_age,
            "frame_age_sec": st.frame_age_sec,
            "capture_fresh": capture_fresh,
            "seq_stall_cycles": self.seq_stall_cycles,
            "last_frame_seq": self.last_frame_seq,
            "last_error": self.last_error,
            "camera_mode_backend": self.camera.mode,
            "grabber": self.last_grabber,
            "enrolled": sorted(self.engine.enrolled.keys()) if self.engine.enrolled else [],
            "headcount": st.headcount,
            "camera_open": st.camera_open,
            "camera_blind": st.camera_blind,
            "away_reason": self._away_reason,
            # per-writer mode + evidence stats (presence-perfect §3.7):
            # silent_sec>300 while headcount>0 → voter down; witnessed>0
            # with applied==0 on subs_only/full → gate wedged; auto_shadowed
            # → page (mirrored row carries the why)
            "writers": self._vl_writer_block(),
            "ts": time.time(),
        }
        out = enrich_timestamps(payload)
        out["ts_local"] = epoch_to_iso(out.get("ts"))
        out["ts_utc"] = epoch_to_iso_utc(out.get("ts"))
        return out
