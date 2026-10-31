"""One sweep: auth the User and take the room at once — DESIGN-ROOM-SWEEP-20260803.

the User: "combine them to be the User auth and room body check all at once since no one
can do the one without the other but User."

One camera lease covers the whole arc (desk sit-beam → legal stations → door
home), so auth and headcount are read from the same room and land in ONE
RoomPresence publish. No path where the count and the auth disagree.

Stops come from room_geometry.json; wall 4–7 and media 9 are never aimed.
Same-frame law holds: pans do not sum bodies — unnamed count is the max seen
in any single frame, and per-name liveness is judged only within a stop
(across stops the whole frame moves, which is exactly what the print-vs-person
gate must ignore).
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths

import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

CAMERA = os.environ.get("RAIL_CAMERA_URL", _ports.camera()).rstrip("/")
# Door pose comes from camera-stops.json via seat_shot.door_pose() — the
# one reader. A literal here goes stale the moment the door is re-measured
# (the old door pan -> the re-measured door pan on 2026-08-16) and is a portability bug besides: the
# pan is per-house init data, not a constant of the software.
from .seat_shot import door_pose as _door_pose

DOOR_PAN = _door_pose()["pan"]
LEASE_TTL = float(os.environ.get("RAIL_SWEEP_LEASE_TTL_SEC", "60"))
SETTLE = float(os.environ.get("RAIL_SWEEP_SETTLE_SEC", "0.25"))
# Desk gets extra frames: that is the auth stop and liveness needs >1 frame.
DESK_SNAPS = int(os.environ.get("RAIL_SWEEP_DESK_SNAPS", "4"))
STOP_SNAPS = int(os.environ.get("RAIL_SWEEP_STOP_SNAPS", "2"))
# Spacing between snaps at one stop. 0.12 s was too tight: a seated person is
# pixel-still over a quarter second and the liveness gate read the User as STATIC
# (measured 2026-08-03, sim 0.71 rejected; still STATIC at 0.45 s). A typing
# human accumulates readable micro-motion over ~2 s; a print never does.
SNAP_GAP = float(os.environ.get("RAIL_SWEEP_SNAP_GAP_SEC", "0.6"))
# Facefinder engage at each seat (matches enroll/look_around pin, the User 2026-08-06)
FACEFINDER_ENGAGE_SEC = float(
    os.environ.get("RAIL_PRESENCE_FACEFINDER_ENGAGE_SEC", "2.5")
)
# Reject clock-hour pans that wrap into wall territory (hours 1–3 used to map to
# ~−481k/−369k/−256k — "inverse wall" peeks). Keep pans near desk/chair/door only.
PAN_ABS_MAX = int(os.environ.get("RAIL_SWEEP_PAN_ABS_MAX", "520000"))
SCAN_ROOT = Path(
    os.environ.get(
        "RAIL_SWEEP_SCAN_ROOT",
        str(_paths.DATA / "room_sweep"),
    )
)
CAMERA_STOPS_PATH = Path(
    os.environ.get(
        "RAIL_CAMERA_STOPS",
        str(_paths.DATA / "camera-stops.json"),
    )
)


def _post(url: str, body: dict, timeout: float = 25.0) -> dict[str, Any]:
    if _ports.camera_off():                      # no camera: a no-op that says so (2026-10-10)
        _ports.say_once("camera calls are no-ops")
        return dict(_ports.OFF)
    req = urllib.request.Request(
        f"{CAMERA}{url}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _snapshot(timeout: float = 5.0) -> bytes:
    with urllib.request.urlopen(f"{CAMERA}/snapshot", timeout=timeout) as r:
        return r.read()


def _load_camera_stops() -> dict[str, Any]:
    try:
        raw = json.loads(CAMERA_STOPS_PATH.read_text())
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _stops(geom: dict[str, Any]) -> list[tuple[str, int, int, int | None]]:
    """(name, pan, tilt, station) from init hours + measured camera-stops.

    Never formula hours. Same pan + different tilt is two stops.
    """
    from .error_peek import _seat_patrol_stops
    from .room_heading import door_home_pose

    cfg = _load_camera_stops()
    by_id: dict[str, dict] = {}
    for s in cfg.get("stops") or []:
        if isinstance(s, dict) and s.get("id"):
            by_id[str(s["id"])] = s

    def _station(sid: str, default: int | None) -> int | None:
        s = by_id.get(sid) or {}
        try:
            h = int(s.get("station"))
            if 1 <= h <= 12:
                return h
        except (TypeError, ValueError):
            pass
        return default

    try:
        user_h = int(geom.get("user_seat") or 6)
    except (TypeError, ValueError):
        user_h = 6

    stops: list[tuple[str, int, int, int | None]] = []
    for name, pan, tilt in _seat_patrol_stops():
        default_h = user_h if name in ("desk", "desk_auth") else None
        stops.append((name, pan, tilt, _station(name, default_h)))
    home = door_home_pose()
    stops.append(
        (
            "door_home",
            int(home["pan"]),
            int(home["tilt"]),
            _station("door", 10),
        )
    )
    return stops


def run_room_sweep(
    svc, *, reason: str = "on_demand", source: str = "manual"
) -> dict[str, Any]:
    """Full-arc sweep under one lease → one atomic auth+headcount publish."""
    import cv2
    import numpy as np  # noqa: F401 — cv2 imdecode path

    from .enrollments import match
    from .service import _crop_sig, _frame_sig, _moved
    from .sweep import MATCH_SIM
    from .vlm_live import is_live

    refused = svc._auth_refused_lights_out()
    if refused:
        return {"ok": False, "error": "lights_out", "refusal": refused}

    from . import camera_token as _ct

    with svc._sweep_lock:
        lease_id = _ct.borrowed()        # under the register token: its lease, never our own take/release
        borrowed_lease = lease_id is not None
        if not borrowed_lease:
            lease = _post(
                "/v1/lease", {"owner": "room_sweep", "ttl_sec": LEASE_TTL}, timeout=8.0
            )
            if not lease.get("ok"):
                return {"ok": False, "error": "camera_leased", "lease": lease}
            lease_id = lease["lease_id"]

        t0 = time.time()
        geom_stops: list[tuple[str, int, int, int | None]] = []
        frames: list[dict[str, Any]] = []  # {idx, stop, station, path}
        scan_dir = SCAN_ROOT / time.strftime("%Y%m%d_%H%M%S")
        try:
            from .look_around import load_geometry

            geom = load_geometry()
            geom_stops = _stops(geom)
            scan_dir.mkdir(parents=True, exist_ok=True)
            idx = 0
            for stop_name, pan, tilt, station in geom_stops:
                is_door = stop_name == "door_home" or pan == DOOR_PAN
                # Free facefinder before absolute aim (FF holds pan otherwise)
                try:
                    _post(
                        "/v1/facefinder",
                        {"on": False, "lease_id": lease_id},
                        timeout=5.0,
                    )
                except Exception:
                    pass
                aim = _post(
                    "/v1/aim",
                    {
                        "pan": pan,
                        "tilt": int(tilt),
                        "zoom": 150 if is_door else 100,
                        "lease_id": lease_id,
                    },
                )
                if not aim.get("ok"):
                    continue
                time.sleep(SETTLE)
                # Facefinder pulse on seat stops only (door is hold, not FF hunt)
                if not is_door:
                    try:
                        _post(
                            "/v1/facefinder",
                            {"on": True, "lease_id": lease_id},
                            timeout=5.0,
                        )
                        time.sleep(FACEFINDER_ENGAGE_SEC)
                    except Exception:
                        time.sleep(min(0.6, FACEFINDER_ENGAGE_SEC))
                n_snaps = DESK_SNAPS if stop_name == "desk_auth" else STOP_SNAPS
                if is_door:
                    n_snaps = max(1, STOP_SNAPS)
                for _ in range(n_snaps):
                    try:
                        jpeg = _snapshot()
                    except Exception:
                        continue
                    p = scan_dir / f"kf_{idx:03d}.jpg"
                    p.write_bytes(jpeg)
                    frames.append(
                        {
                            "idx": idx,
                            "stop": stop_name,
                            "station": station,
                            "path": p,
                            "pan": pan,
                            "tilt": int(tilt),
                            "facefinder": not is_door,
                        }
                    )
                    idx += 1
                    time.sleep(SNAP_GAP)
                # Always release FF before next stop / door
                try:
                    _post(
                        "/v1/facefinder",
                        {"on": False, "lease_id": lease_id},
                        timeout=5.0,
                    )
                except Exception:
                    pass
        finally:
            # Home BEFORE the release, and with an explicit zoom.
            #
            # Two faults here, both of which the User hit on 2026-08-16 ("be sure
            # that tool deZOOMs"; "it has been pointing at me for over a
            # minute"):
            #
            #   order — the lease went back first, so the homing aim was sent
            #     unleased and could be refused or fought by whoever grabbed
            #     the camera next. Give the camera back pointed at the door,
            #     not pointed at a person.
            #   zoom — _ensure_door_aim only sets zoom on its FALLBACK path.
            #     When the grabber's return_door succeeds, which is the normal
            #     case, zoom was never restored and the camera stayed tight on
            #     whatever the last seat framed.
            #
            # go_home is the one shared contract: facefinder off, de-zoom to
            # the door's own zoom from init, door home.
            try:
                from .seat_shot import go_home

                go_home(sys.modules[__name__], lease_id)
            except Exception:
                try:
                    _post("/v1/facefinder", {"on": False}, timeout=5.0)
                except Exception:
                    pass
            if not borrowed_lease:          # the token's holder gives a borrowed lease back
                try:
                    _post("/v1/lease/release", {"lease_id": lease_id}, timeout=8.0)
                except Exception:
                    pass
            try:
                svc._ensure_door_aim(release_facelock=True)
            except Exception:
                try:
                    svc._ensure_door_aim()
                except Exception:
                    pass

        # ---- match faces (same engine + liveness gate as the scan-dir path) ----
        svc.engine.ensure()
        face = svc.engine.face
        enrolled = svc.engine.enrolled
        if face is None:
            return {"ok": False, "error": "face engine unavailable"}

        frame_sig: dict[int, Any] = {}
        # name → stop → [(idx, sim, crop_sig)]
        named_obs: dict[str, dict[str, list]] = {}
        names_best: dict[str, float] = {}
        name_station: dict[str, int | None] = {}
        unnamed_best_frame = (0, None)  # (count, station)
        detail: list[str] = []
        n_frames = 0
        for fr in frames:
            im = cv2.imread(str(fr["path"]))
            if im is None:
                continue
            n_frames += 1
            frame_sig[fr["idx"]] = _frame_sig(im)
            try:
                found = face.detect(im)
            except Exception as e:
                detail.append(f"{fr['stop']}: detect {e}"[:80])
                continue
            frame_unknowns = 0
            for fobj in found:
                emb = face.embed(fobj)
                if emb is None:
                    continue
                bbox = face.bbox(fobj)
                # Zone 9 screen mask (the User 08-07): faces playing on the desk
                # screens are neither people nor killbook-able statics.
                # Size gate (the User 08-09): the hallway portraits are ~20 px
                # across; nobody in this room has a face that small.
                try:
                    from .static_faces import in_masked_zone, too_small_to_name

                    if in_masked_zone(fr.get("stop"), bbox, im.shape):
                        continue
                    if too_small_to_name(bbox):
                        continue
                    if fr.get("stop") in ("door_home", "door") and in_masked_zone(
                        "door_home", bbox, im.shape
                    ):
                        continue
                except Exception:
                    pass
                name, sim = match(emb, enrolled, threshold=MATCH_SIM)
                if name:
                    named_obs.setdefault(name, {}).setdefault(fr["stop"], []).append(
                        (fr["idx"], float(sim), _crop_sig(im, bbox))
                    )
                    if name not in names_best or sim > names_best[name]:
                        names_best[name] = float(sim)
                        name_station[name] = fr["station"]
                elif bbox is not None and is_live(im, bbox):
                    # same gate as the scan-dir path: a poster is not a body
                    frame_unknowns += 1
            if frame_unknowns > unnamed_best_frame[0]:
                unnamed_best_frame = (frame_unknowns, fr["station"])

        # Liveness: judge within stops only (across stops the frame itself moves).
        # STATIC blocks AUTH and blocks a NEW mint (photo law, d739f50) — but it
        # must not drain someone the inventory already believes present: a
        # seated person can be pixel-still across two snaps, a print cannot
        # walk in. Measured 2026-08-03: the User at his desk, sim 0.71, STATIC —
        # the first cut of this code set the room to 0 with him in it.
        static_present: set[str] = set()
        for nm, by_stop in list(named_obs.items()):
            verdicts = [
                _moved(obs, frame_sig) for obs in by_stop.values() if len(obs) >= 1
            ]
            moved_any = any(v is True for v, _ in verdicts)
            static_all = verdicts and all(v is False for v, _ in verdicts)
            best = names_best.get(nm, 0.0)
            if moved_any:
                ev = max(e for v, e in verdicts if v is True)
                detail.append(f"{nm}: LIVE moved={ev:.2f} ({best:.2f})")
            elif static_all:
                names_best.pop(nm, None)
                ev = max((e for _v, e in verdicts), default=0.0)
                if nm in getattr(svc.inventory, "present", {}):
                    static_present.add(nm)
                    detail.append(
                        f"{nm}: STATIC kept-no-auth moved={ev:.2f} ({best:.2f})"
                    )
                else:
                    name_station.pop(nm, None)
                    detail.append(
                        f"{nm}: STATIC rejected moved={ev:.2f} ({best:.2f})"
                    )
            else:
                detail.append(f"{nm}: UNVERIFIED single-frame ({best:.2f})")

        # Same-frame law: unnamed is the max in one frame, never a sum. Cap 2.
        unnamed = min(int(unnamed_best_frame[0]), 2)

        # Tombstones: a swept face may not resurrect someone door physics
        # just walked out (face must agree with presence, not override it).
        now = time.time()
        auth_names = [
            n
            for n in sorted(names_best.keys())
            if float(svc._left_block_until.get(n) or 0) <= now
        ]
        count_names = auth_names + [
            n
            for n in sorted(static_present)
            if float(svc._left_block_until.get(n) or 0) <= now
        ]

        # ---- the one atomic write ----
        # Auth (face_sim + door_names) only for LIVE faces; STATIC keeps the
        # body counted and seated but earns nothing.
        for n in auth_names:
            svc._id_certain[n] = True
            src = dict(svc._id_sources.get(n) or {})
            src["face"] = True
            src["room_sweep"] = True
            src["face_sim"] = float(names_best[n])
            svc._id_sources[n] = src
        for n in count_names:
            st_h = name_station.get(n)
            if st_h is not None:
                try:
                    svc.inventory.station_guess[str(n)] = int(st_h)
                except Exception:
                    pass

        # Vision-first rescan (the User 2026-08-11): product Rescan room / look_around
        # re-measures the room. Sticky must not re-mint ghosts (another person stayed
        # on the wall after leave because apply_reconcile always unions sticky).
        # Door-path sticky still holds for normal watch; only this path clears.
        src_l = f"{source}|{reason}".lower()
        vision_first = any(
            k in src_l
            for k in (
                "rescan",
                "look_around",
                "lookaround",
                "voiceonly",
                "panel_rescan",
                "ghost",
                "manual",
                "on_demand",
            )
        )
        dropped_ghosts: list[str] = []
        if vision_first and n_frames >= 2:
            keep = {str(n).lower() for n in count_names}
            ghosts = sorted(
                {
                    str(n).lower()
                    for n in (
                        set(svc.inventory.present) | set(svc.inventory.sticky)
                    )
                    if str(n).lower() not in keep
                }
            )
            if ghosts:
                dropped_ghosts = [
                    e.get("name")
                    for e in svc.inventory.force_leave(
                        ghosts, source=f"rescan_vision:{source}"
                    )
                    if e.get("name")
                ]
                detail.append(f"rescan_drop_ghosts:{','.join(dropped_ghosts)}")

        # union, never replace. A camera sweep is corroboration, not a leave
        # path — the door is the only way out, so a seat tour that fails to
        # find someone has found nothing at all. They may be behind the camera,
        # off the tour, or simply not looking up.
        #
        # apply_reconcile's own docstring records this exact incident ("the User
        # 2026-08-11: body_count found the User and wiped another person still in the
        # room") and `union` was added to prevent it — but this call site kept
        # taking the default, which is `replace`. 2026-08-16, three people in
        # the room: the sweep saw two and a guest was dropped from the roster
        # seconds after being asserted, repeatedly.
        #
        # Deletion is already handled deliberately a few lines above, where
        # confident ghosts go through force_leave. That is the leave path; this
        # one only ever needs to ADD what the camera found.
        ev = svc.inventory.apply_reconcile(count_names, unnamed, mode="union")
        if dropped_ghosts:
            ev["dropped_ghosts"] = dropped_ghosts
            ev["vision_first"] = True

        bodies: list[dict[str, Any]] = [
            {
                "at": name_station.get(n),
                "who": n,
                "face": True,
                "sim": round(float(names_best[n]), 3),
            }
            for n in auth_names
        ] + [
            {"at": name_station.get(n), "who": n, "face": True, "live": False}
            for n in sorted(static_present)
            if n in count_names
        ]
        for k in range(unnamed):
            bodies.append(
                {"at": unnamed_best_frame[1], "who": f"unknown-{k + 1}", "face": False}
            )
        wall = round(time.time() - t0, 3)
        stops_pub = [s for _, _, _, s in geom_stops if s is not None]

        st = svc._presence_from_inventory(
            frame_age=wall,
            camera_open=True,
            camera_blind=False,
            last_event={
                "kind": "room_sweep",
                "reason": reason,
                "source": source,
                "mode": "one_lease_one_write",
                "bodies": bodies,
                "swept_at": now,
                "stops": stops_pub,
                "entered": ev.get("entered"),
                "left": ev.get("left"),
                "names": count_names,
                "unnamed": unnamed,
                "n_frames": n_frames,
                "wall_sec": wall,
                "scan_dir": str(scan_dir),
                "detail": detail[:40],
            },
            count_trust="fact",
            camera_mode="holding",
            door_names=auth_names,
        )
        st.count_certain = st.unidentified == 0 and bool(
            count_names or st.headcount == 0
        )
        svc._mode = "holding"
        svc._away_reason = None
        svc.sweep_cycles += 1
        svc.last_cycle_at = time.time()
        svc._set(st)

    return {
        "ok": True,
        "schema": "room_sweep/v1",
        "bodies": bodies,
        "swept_at": now,
        "stops": stops_pub,
        "wall_sec": wall,
        "detail": detail[:40],
        "presence": st.to_dict(),
    }
