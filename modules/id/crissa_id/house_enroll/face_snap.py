"""Door/desk camera face capture during enroll → people/<id>/face/.

HTTP only to rail-camera (:8192). Embed via the face engine in a child process
(HOUSE_ENROLL_FACE_PYTHON, default <the id env's python>) so the
jasper/ECAPA interpreter stays light.

Grabber already writes true-handed JPEGs (RAIL_CAMERA_HFLIP=1).
Never HFLIP a /snapshot — that records the room backwards.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

from .. import face_engine
from .. import paths as _paths      # the embed child's PYTHONPATH (the User 2026-10-10, .101: NameError on every snap)

if TYPE_CHECKING:
    from .site import Site

from .person import ensure_person



def _forward() -> dict[str, int]:
    """The camera's own forward (V4L2 pan/tilt 0, 1x): an untaught door or desk. No room's numbers (2026-10-25)."""
    return {"pan": 0, "tilt": 0, "zoom": 100}

def _post_json(url: str, body: dict, timeout: float = 20.0) -> Any:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    if not raw:
        return None
    try:
        return json.loads(raw.decode())
    except Exception:
        return raw


def _get_bytes(url: str, timeout: float = 15.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


# A CAMERA THAT CANNOT TURN IS ONLY ASKED FOR PICTURES (round 21, .101 2026-10-14 round 20: Face photos' first pass
# answered `HTTPError 503` on enroll_begin_fov, facefind and look_around - the gimbal choreography of the house's
# Insta360 (aim, the firmware's face finder, the door home) asked of a camera whose pan is a crop of one view, the
# Elgato Facecam on .101, or none at all). The camera says what it is (/health camera_ptz: motor | digital | none,
# lib crissa_presence.grabber.camera_ptz): only a motor is aimed or face-found; any other camera is a snapshot, taken
# as is. Unreadable = the old choreography. Kill: CRISSA_FIXED_CAM_SNAP_ONLY=0.
_CAM_KIND: dict[str, str] = {}


def camera_moves(site: "Site") -> bool:
    """True when the camera is a motor gimbal (or says nothing): the aim/face-finder routes apply."""
    if os.environ.get("CRISSA_FIXED_CAM_SNAP_ONLY", "1").strip().lower() in ("0", "off", "false", "no"):
        return True
    cam = site.camera_url.rstrip("/")
    if cam not in _CAM_KIND:
        kind = ""
        try:
            kind = str((json.loads(_get_bytes(f"{cam}/health", timeout=3.0).decode() or "{}") or {}).get(
                "camera_ptz") or "").strip().lower()
        except Exception:  # noqa: BLE001 - unreadable: keep the old choreography, ask again next time
            return True
        _CAM_KIND[cam] = kind
    return _CAM_KIND[cam] not in ("digital", "none")


# A PICTURE THE CAMERA CANNOT GIVE YET IS ASKED FOR AGAIN (round 21, .101 round 20: both turned stills of the counts
# read "no picture in 0 looks" and Face photos' first pass a 503: the camera answers 503 "no frame" until its first
# frame, and a refused or reset connection while it reopens). A snapshot retries quietly for SNAP_RETRY_S before its
# error reaches the caller. Kill: CRISSA_SNAPSHOT_RETRY_S=0.
SNAP_RETRY_S = float(os.environ.get("CRISSA_SNAPSHOT_RETRY_S", "2.5") or 0)


def _post_bytes_json(url: str, body: dict, timeout: float = 90.0) -> dict[str, Any]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def load_stops(site: Site) -> dict[str, Any]:
    p = site.stops_json()
    if p is None or not p.is_file():
        return {}
    return json.loads(p.read_text())


def station_pt(site: Site, station: str) -> tuple[int, int]:
    """pan, tilt for desk|door|chair from stops or site.enroll."""
    enroll = site.raw.get("enroll") or {}
    key = f"{station}_pan"
    if key in enroll:
        return int(enroll[key]), int(enroll.get(f"{station}_tilt") or 0)
    cfg = load_stops(site)
    if station == "desk":
        ff = cfg.get("factory_forward") or {}
        for s in cfg.get("stops") or []:
            if s.get("id") == "desk":
                return int(s["pan"]), int(s.get("tilt") or 0)
        return int(ff.get("pan") or 0), int(ff.get("tilt") or 0)
    if station == "door":
        door = cfg.get("door") or {}
        return int(door.get("pan") or _forward()["pan"]), int(door.get("tilt") or 0)
    for s in cfg.get("stops") or []:
        if s.get("id") == station:
            return int(s["pan"]), int(s.get("tilt") or 0)
    return 0, 0


def _facefinder(site: Site, enable: bool) -> bool:
    """Insta360 Link firmware facefinder / facelock via rail-camera HTTP."""
    cam = site.camera_url.rstrip("/")
    body = {"enable": bool(enable)}
    if not enable:
        body["action"] = "release"
    for path in ("/v1/facefinder", "/facefinder", "/v1/facelock", "/facelock"):
        try:
            _post_json(f"{cam}{path}", body, timeout=8.0)
            return True
        except Exception:
            continue
    return False


def _release_facelock(site: Site, *, times: int = 1) -> None:
    """Release facefinder. Default **once** — multi-release was spinning the gimbal."""
    if not camera_moves(site):                  # no face finder on a camera that cannot turn (round 21)
        return
    ok = False
    for i in range(max(1, times)):
        ok = _facefinder(site, False) or ok
        if times > 1:
            time.sleep(0.1)
    print(f"  facefinder release={'ok' if ok else 'FAIL'} x{times}", flush=True)


# Session FOV: aim desk once, then only snapshot (no re-aim per take)
_enroll_fov: dict[str, Any] = {"station": None, "aimed": False}


def hold_fov(station: str) -> None:
    """Caller already aimed. Next enroll_hold_snap is JPEG-only (no gimbal)."""
    global _enroll_fov
    _enroll_fov = {"station": station, "aimed": True}


def _invalidate_enroll_fov() -> None:
    """Facefind paths leave door home — next hold snap must re-aim sit station."""
    global _enroll_fov
    _enroll_fov = {"station": None, "aimed": False}


def enroll_begin_fov(site: Site, station: str = "desk") -> None:
    """One release + one desk aim for whole enroll session (hold 3 min)."""
    if not camera_moves(site):                  # a camera that cannot turn: nothing to aim (round 21)
        _enroll_fov.update(station=station, aimed=True, pan=None)
        print(f"  enroll FOV: the camera does not turn ({_CAM_KIND.get(site.camera_url.rstrip('/'))}); "
              f"pictures as it sees them", flush=True)
        return
    cam = site.camera_url.rstrip("/")
    pan, tilt = station_pt(site, station)
    hold = float((site.raw.get("enroll") or {}).get("camera_hold_sec") or 180)
    settle = float((site.raw.get("enroll") or {}).get("settle_sec") or 1.2)
    _release_facelock(site, times=1)
    _post_json(f"{cam}/aim", {"pan": pan, "tilt": tilt, "hold_sec": hold})
    time.sleep(settle)
    # one re-assert only if still wrong
    actual = _pan_actual(site)
    if actual is not None and not _pan_near(actual, pan, tol=15000):
        _post_json(f"{cam}/aim", {"pan": pan, "tilt": tilt, "hold_sec": hold})
        time.sleep(0.5)
    _enroll_fov["station"] = station
    _enroll_fov["aimed"] = True
    _enroll_fov["pan"] = pan
    print(
        f"  enroll FOV locked station={station} pan={pan} "
        f"actual={_pan_actual(site)} hold={hold}s",
        flush=True,
    )


def aim_station(site: Site, station: str, settle: float | None = None) -> None:
    """Aim once (used by restore / non-enroll). Prefer enroll_begin_fov for sessions."""
    cam = site.camera_url.rstrip("/")
    pan, tilt = station_pt(site, station)
    hold = float((site.raw.get("enroll") or {}).get("camera_hold_sec") or 120)
    _release_facelock(site, times=1)
    _post_json(f"{cam}/aim", {"pan": pan, "tilt": tilt, "hold_sec": hold})
    time.sleep(settle if settle is not None else 1.0)
    print(
        f"  camera aim station={station} pan={pan} actual={_pan_actual(site)}",
        flush=True,
    )


def enroll_hold_snap(
    site: Site,
    station: str,
    *,
    engage_sec: float | None = None,
) -> tuple[bytes | None, dict[str, Any]]:
    """Snapshot only while FOV held — **no** facefinder, **no** re-aim unless first call.

    Math that caused ~30 spins: 3 releases × 3 aims × 3 attempts × retries × 2 poses.
    Now: aim once per session; each take = one JPEG get.
    """
    _ = engage_sec  # unused — FF off during enroll (causes track fight)
    pan, tilt = station_pt(site, station)
    meta: dict[str, Any] = {
        "station": station,
        "pan_cmd": pan,
        "tilt": tilt,
        "tool": "enroll_snap_only",
        "order": "one_aim_session_snap_only",
        "facefinder": False,
    }
    if not _enroll_fov.get("aimed") or _enroll_fov.get("station") != station:
        enroll_begin_fov(site, station)
    try:
        jpeg = snapshot_jpeg(site)
        meta["pan_actual"] = _pan_actual(site)
        print(
            f"  enroll snap station={station} pan={meta['pan_actual']} "
            f"jpeg={len(jpeg) if jpeg else 0}",
            flush=True,
        )
        return jpeg, meta
    except Exception as e:
        meta["error"] = str(e)[:160]
        print(f"  enroll_hold_snap FAIL: {e!r}", flush=True)
        return None, meta


def facefinder_engage(
    site: Site,
    engage_sec: float | None = None,
    *,
    force: bool = False,
) -> bool:
    """Bounded facefinder ON so firmware frames a face, then caller snaps.

    Site enroll.use_facefinder=false blocks candid paths; pass force=True for
    look-and-say quality mapping (center/zoom) regardless of site default.
    """
    enroll = site.raw.get("enroll") or {}
    if not force and enroll.get("use_facefinder", True) is False:
        return False
    sec = float(
        engage_sec
        if engage_sec is not None
        else enroll.get("facefinder_engage_sec")
        or 1.8
    )
    ok = _facefinder(site, True)
    print(f"  facefinder ON ok={ok} engage={sec:.1f}s force={force}", flush=True)
    if ok:
        time.sleep(max(0.4, sec))
    return ok


def snapshot_jpeg(site: Site) -> bytes:
    cam = site.camera_url.rstrip("/")
    deadline = time.time() + max(0.0, SNAP_RETRY_S)
    while True:
        try:
            return _get_bytes(f"{cam}/snapshot")
        except Exception as e:  # noqa: BLE001 - 503 no frame yet, or the camera reopening
            code = getattr(e, "code", None)
            if (code not in (None, 502, 503, 504)) or time.time() >= deadline:
                raise
            time.sleep(0.25)


def _jpeg_from_rail_frame(fr: dict[str, Any]) -> bytes | None:
    """Prefer path on disk (look_around), else jpeg_b64 (facefind)."""
    import base64

    path = fr.get("path")
    if path and Path(path).is_file():
        return Path(path).read_bytes()
    b64 = fr.get("jpeg_b64")
    if b64:
        try:
            return base64.b64decode(b64)
        except Exception:
            return None
    return None


def _pan_actual(site: Site) -> int | None:
    try:
        cam = site.camera_url.rstrip("/")
        raw = _get_bytes(f"{cam}/health", timeout=4.0)
        h = json.loads(raw.decode())
        p = h.get("pan_actual")
        return int(p) if p is not None else None
    except Exception:
        return None


def _pan_near(actual: int | None, target: int, tol: int = 12000) -> bool:
    if actual is None:
        return False
    return abs(int(actual) - int(target)) <= tol


def point_then_facefinder_snap(
    site: Site,
    station: str,
    *,
    engage_sec: float | None = None,
    force_ff: bool = True,
) -> tuple[bytes | None, dict[str, Any]]:
    """
    Enrollee identity rule: **point at their station first, then call facefinder.**

    Order (fixed — do not invert):
      1. facefinder OFF (free gimbal)
      2. absolute aim → desk/door/… (person is in that FOV)
      3. confirm pan near target
      4. facefinder ON (locks *that* face, not a random room face)
      5. snapshot
      6. facefinder OFF
      7. door home (caller may also restore)

    Prefer rail /v1/facefind for sole-owner PTZ+FF (same sequence inside grabber).
    DIY mirrors it if HTTP facefind is unavailable.

    force_ff: look-and-say quality snaps always engage FF even if site enroll
    has use_facefinder=false (candid hold path stays FF-off).
    """
    cam = site.camera_url.rstrip("/")
    enroll = site.raw.get("enroll") or {}
    # Quality map needs enough time for firmware to center+zoom (mouth-up → full face)
    default_engage = 2.5 if force_ff else 1.8
    engage = float(
        engage_sec
        if engage_sec is not None
        else enroll.get("facefinder_engage_sec")
        or default_engage
    )
    settle = float(enroll.get("settle_sec") or 1.2)
    pan, tilt = station_pt(site, station)
    meta: dict[str, Any] = {
        "station": station,
        "pan_cmd": pan,
        "tilt": tilt,
        "engage_sec": engage,
        "order": "aim_then_facefinder",
    }

    # --- 1) Prefer rail facefind: one stop = aim that pan, then FF, then door ---
    try:
        print(
            f"  point→FF via /v1/facefind  station={station} pan={pan} "
            f"(aim first, then facefinder)",
            flush=True,
        )
        result = _post_bytes_json(
            f"{cam}/v1/facefind",
            {
                "stops": [{"id": station, "pan": pan, "tilt": tilt}],
                "engage_sec": engage,
                "settle_sec": settle,
            },
            timeout=90.0,
        )
        meta["tool"] = "facefind"
        meta["door_pan"] = result.get("door_pan")
        meta["door_ok"] = result.get("door_ok")
        for fr in result.get("frames") or []:
            if not isinstance(fr, dict) or fr.get("ok") is False:
                continue
            # Only accept the stop we asked for (which face = who is at station)
            if str(fr.get("id") or "") not in (station, str(pan)):
                # still ok if single-frame response
                if len(result.get("frames") or []) > 1:
                    continue
            jpeg = _jpeg_from_rail_frame(fr)
            if jpeg:
                meta["pan_actual"] = fr.get("pan_actual")
                meta["facefinder"] = True
                print(
                    f"  facefind ok id={fr.get('id')} pan_actual={fr.get('pan_actual')} "
                    f"bytes={len(jpeg)} door={result.get('door_pan')}",
                    flush=True,
                )
                _invalidate_enroll_fov()
                return jpeg, meta
        print(f"  facefind no usable frame n={result.get('n')}", flush=True)
    except Exception as e:
        meta["facefind_err"] = str(e)[:160]
        print(f"  facefind FAIL: {e!r}", flush=True)

    # --- 2) look_around single stop (door→aim station→FF→door) ---
    try:
        print(f"  point→FF via /v1/look_around stop_ids=[{station}]", flush=True)
        result = _post_bytes_json(
            f"{cam}/v1/look_around",
            {
                "stop_ids": [station],
                "engage_sec": engage,
                "settle_sec": settle,
                "facefinder": True,
                "include_jpeg": True,
            },
            timeout=120.0,
        )
        meta["tool"] = "look_around"
        meta["door_pan"] = result.get("door_pan")
        for fr in result.get("frames") or []:
            if not isinstance(fr, dict) or not fr.get("ok"):
                continue
            if str(fr.get("id") or "") != station and len(result.get("frames") or []) > 1:
                continue
            jpeg = _jpeg_from_rail_frame(fr)
            if jpeg:
                meta["pan_actual"] = fr.get("pan_actual")
                meta["facefinder"] = fr.get("facefinder", True)
                print(
                    f"  look_around ok id={fr.get('id')} pan_actual={fr.get('pan_actual')} "
                    f"bytes={len(jpeg)}",
                    flush=True,
                )
                _invalidate_enroll_fov()
                return jpeg, meta
    except Exception as e:
        meta["look_around_err"] = str(e)[:160]
        print(f"  look_around FAIL: {e!r}", flush=True)

    # --- 3) DIY: explicit aim → confirm pan → FF → snap → release ---
    print("  DIY: aim station → confirm pan → facefinder → snap", flush=True)
    meta["tool"] = "diy_aim_then_ff"
    aim_station(site, station, settle=settle)
    actual = _pan_actual(site)
    meta["pan_actual_pre_ff"] = actual
    if not _pan_near(actual, pan):
        # re-aim once more before trusting FF
        print(f"  pan not at station (actual={actual} want={pan}) — re-aim", flush=True)
        _release_facelock(site)
        try:
            _post_json(f"{cam}/aim", {"pan": pan, "tilt": tilt})
            time.sleep(settle)
        except Exception as e:
            print(f"  re-aim fail: {e!r}", flush=True)
        actual = _pan_actual(site)
        meta["pan_actual_pre_ff"] = actual
    if not _pan_near(actual, pan):
        print(
            f"  WARN pan still off (actual={actual} cmd={pan}) — FF may lock wrong face",
            flush=True,
        )
        meta["pan_warn"] = True
    else:
        print(f"  pan ok at station actual={actual} — calling facefinder", flush=True)

    try:
        facefinder_engage(site, engage_sec=engage, force=force_ff)
        jpeg = snapshot_jpeg(site)
        meta["pan_actual"] = _pan_actual(site)
        meta["facefinder"] = True
        return jpeg, meta
    finally:
        _release_facelock(site)
        _invalidate_enroll_fov()


# keep old name
def rail_facefind_snap(
    site: Site,
    station: str,
    *,
    engage_sec: float | None = None,
    force_ff: bool = True,
) -> tuple[bytes | None, dict[str, Any]]:
    return point_then_facefinder_snap(
        site, station, engage_sec=engage_sec, force_ff=force_ff
    )


def aim_facefinder_snap(
    site: Site,
    station: str,
    *,
    engage_sec: float | None = None,
) -> bytes:
    """Back-compat: one JPEG via rail facefind path."""
    jpeg, _meta = rail_facefind_snap(site, station, engage_sec=engage_sec)
    if not jpeg:
        raise RuntimeError("no jpeg from facefind/look_around/diy")
    return jpeg


def face_python() -> str:
    """The Python that carries the face engine: HOUSE_ENROLL_FACE_PYTHON, else the id env, else this one."""
    return os.environ.get("HOUSE_ENROLL_FACE_PYTHON") or face_engine.python()


# One warm FaceAnalysis per invocation. stdin = JSON list of JPEG paths.
# ctx_id=-1 CPU by default (do not steal the live GPU face lane).
# The embed child is `python -m crissa_id.face_engine` (stdin: JPEG paths, stdout: records); the house ran a
# detector family here that does not ship (2026-10-09).


def embed_jpegs(paths: list[Path], *, ctx_id: int | None = None) -> list[dict[str, Any]]:
    """One warm the face engine process for many JPEGs. CPU default."""
    py = face_python()
    if not Path(py).is_file() and py != "python3":
        print(f"  face embed python missing: {py}", flush=True)
        return [{"ok": False, "error": "no_python", "path": str(p)} for p in paths]
    if not paths:
        return []
    if ctx_id is None:
        ctx_id = int(os.environ.get("HOUSE_ENROLL_FACE_CTX", "-1"))
    try:
        env = dict(os.environ, PYTHONPATH=str(_paths.MODULE) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""))
        r = subprocess.run(
            [py, "-m", "crissa_id.face_engine"],
            input=json.dumps([str(p) for p in paths]),
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
    except Exception as e:
        print(f"  face embed subprocess fail: {e!r}", flush=True)
        return [{"ok": False, "error": str(e), "path": str(p)} for p in paths]
    line = (r.stdout or "").strip().splitlines()
    if not line:
        print(f"  face embed empty stdout stderr={(r.stderr or '')[:200]}", flush=True)
        return [{"ok": False, "error": "empty", "path": str(p)} for p in paths]
    try:
        recs = json.loads(line[-1])
    except json.JSONDecodeError:
        print(f"  face embed bad json: {line[-1][:200]}", flush=True)
        return [{"ok": False, "error": "bad_json", "path": str(p)} for p in paths]
    if not isinstance(recs, list):
        recs = [recs]
    for rec, p in zip(recs, paths):
        rec.setdefault("path", str(p))
    return recs


LAST_EMBED_ERROR: dict[str, str] = {"error": ""}     # the engine's last refusal, for the face record and the plate


def embed_jpeg(path: Path) -> dict[str, Any] | None:
    recs = embed_jpegs([path])
    if not recs:
        LAST_EMBED_ERROR["error"] = "no answer from the face engine"
        return None
    rec = recs[0]
    if not rec.get("ok"):
        LAST_EMBED_ERROR["error"] = str(rec.get("error") or "unknown")
        print(f"  face embed: {rec.get('error')}", flush=True)
        return None
    LAST_EMBED_ERROR["error"] = ""
    return rec


def next_enroll_slot(face_dir: Path, prefix: str = "enroll_desk") -> int:
    n = 1
    while (face_dir / f"{prefix}_{n:03d}.json").is_file() or (
        face_dir / "refs" / f"{prefix}_{n:03d}.jpg"
    ).is_file():
        n += 1
    return n


def capture_face(
    site: Site,
    person_id: str,
    *,
    station: str = "desk",
    pose: str = "front",
    take_plan_id: str | None = None,
    prefix: str | None = None,
    aim: bool = True,
    use_facefinder: bool | None = None,
    quality_map: bool = False,
    engage_sec: float | None = None,
) -> dict[str, Any]:
    """
    Aim (optional) → snapshot → refs JPG + face JSON (with emb when possible).

    Paths:
      - candid / face-only section: hold FOV + snap (default; no facefinder thrash)
      - look-and-say quality map: quality_map=True or use_facefinder=True →
        point→facefinder center/zoom for full-face emb (mouth-up desk cams)

    Returns meta dict (always has jpg path; encoding may be missing).
    """
    _ = aim  # kept for call-site compat; FOV handled inside snap helpers
    person_id = person_id.strip().lower()
    from .person import assert_not_frozen

    assert_not_frozen(person_id, root=site.root)
    face_dir = ensure_person(person_id, root=site.root) / "face"
    refs = face_dir / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    prefix = prefix or f"enroll_{station}"
    slot = next_enroll_slot(face_dir, prefix)
    stem = f"{prefix}_{slot:03d}"

    enroll_cfg = site.raw.get("enroll") or {}
    # hold = one FOV + snap only (default). facefind = old door-thrash path.
    mode = str(enroll_cfg.get("capture_mode") or "hold").strip().lower()
    site_ff = bool(enroll_cfg.get("use_facefinder", False))  # default OFF for candid
    # Explicit kwarg / quality_map wins over site (look-and-say needs FF)
    if use_facefinder is not None:
        want_ff = bool(use_facefinder)
    elif quality_map:
        want_ff = True
    else:
        want_ff = site_ff and mode == "facefind"
    if want_ff and not camera_moves(site):
        want_ff = False                         # a camera that cannot turn: a plain picture (round 21)
    best_emb: dict[str, Any] | None = None
    best_jpeg: bytes | None = None
    rail_meta: dict[str, Any] = {}
    last_err = None
    # Max 2 snaps total — quality fail does not re-spin the gimbal forever
    attempts = 2
    for attempt in range(1, attempts + 1):
        try:
            if want_ff:
                print(
                    f"  face quality_map FF center/zoom station={station} "
                    f"attempt={attempt}",
                    flush=True,
                )
                jpeg, rail_meta = point_then_facefinder_snap(
                    site,
                    station,
                    engage_sec=engage_sec
                    if engage_sec is not None
                    else float(enroll_cfg.get("facefinder_engage_sec") or 2.5),
                    force_ff=True,
                )
                if not jpeg:
                    raise RuntimeError("empty jpeg from facefinder quality path")
            else:
                jpeg, rail_meta = enroll_hold_snap(site, station)
                if not jpeg:
                    raise RuntimeError("empty jpeg from enroll hold path")
        except Exception as e:
            last_err = str(e)
            print(f"  snapshot FAIL attempt {attempt}: {e!r}", flush=True)
            time.sleep(0.25)
            continue
        tmp = refs / f".{stem}_try{attempt}.jpg"
        tmp.write_bytes(jpeg)
        emb = embed_jpeg(tmp)
        try:
            tmp.unlink(missing_ok=True)  # type: ignore[call-arg]
        except TypeError:
            try:
                tmp.unlink()
            except OSError:
                pass
        if emb and (
            best_emb is None
            or float(emb.get("det_score") or 0) > float(best_emb.get("det_score") or 0)
        ):
            best_emb, best_jpeg = emb, jpeg
        elif best_jpeg is None:
            best_jpeg = jpeg
        # Quality map wants a decent det + bbox; candid accepts first emb
        det_ok = emb and float(emb.get("det_score") or 0) >= (0.50 if want_ff else 0.45)
        if det_ok:
            break
        # second try: wait for person to face cam
        time.sleep(0.6 if not want_ff else 0.4)

    if best_jpeg is None:
        return {"ok": False, "error": last_err or "no_snapshot"}

    jpeg = best_jpeg
    emb = best_emb
    jpg_path = refs / f"{stem}.jpg"
    jpg_path.write_bytes(jpeg)
    # Orientation law is grabber true_handed (site.json camera.*) — no per-ref
    # orientation.json sidecars (removed 2026-08-11).

    rec: dict[str, Any] = {
        "name": person_id,
        "person_id": person_id,
        "modality": "face",
        "model": face_engine.MODEL,
        "dim": 512,
        "source": "house_enroll_facefind" if want_ff else "house_enroll_hold",
        "site_id": site.site_id,
        "station": station,
        "pose_asked": pose,
        "pose": (emb or {}).get("bin") or pose,
        "yaw": (emb or {}).get("yaw"),
        "bin": (emb or {}).get("bin"),
        "kps": (emb or {}).get("kps"),
        "take_plan_id": take_plan_id,
        "camera_tool": rail_meta.get("tool"),
        "pan_actual": rail_meta.get("pan_actual") or rail_meta.get("pan_actual_post"),
        "facefinder": rail_meta.get("facefinder", False),
        "quality_map": bool(want_ff),
        "ref": jpg_path.name,
        "ref_path": str(jpg_path),
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ok": True,
    }
    if emb:
        rec["encoding"] = emb["encoding"]
        rec["dim"] = emb.get("dim") or 512
        rec["det_score"] = emb.get("det_score")
        rec["bbox"] = emb.get("bbox")
    else:
        rec["encoding"] = None
        rec["embed_pending"] = True
        rec["embed_error"] = LAST_EMBED_ERROR.get("error") or "no print"

    jp = face_dir / f"{stem}.json"
    jp.write_text(json.dumps(rec, indent=2) + "\n")
    if emb and rec.get("encoding"):
        try:
            from .. import pack_store
            pack_store.sync_from_store(person_id, site.root)      # the User's prints also reach enroll.json
        except Exception as e:  # noqa: BLE001
            print(f"  enroll.json: {type(e).__name__}", flush=True)
    print(
        f"  face snap {jp.name} bytes={len(jpeg)} "
        f"emb={'yes' if emb else 'PENDING'} det={rec.get('det_score')}",
        flush=True,
    )
    rec["json_path"] = str(jp)
    rec["jpg_path"] = str(jpg_path)
    return rec


def rest_station_id(site: Site) -> str:
    """Ingress / door rest — camera home after enroll (never leave on desk)."""
    enroll = site.raw.get("enroll") or {}
    return str(enroll.get("rest_station") or "door").strip().lower() or "door"


def door_pose(site: Site) -> dict[str, int]:
    """Oracle door rest from camera-stops `door{}` (pan/tilt/zoom)."""
    d = (load_stops(site).get("door") or {})
    return {
        "pan": int(d.get("pan") or _forward()["pan"]),
        "tilt": int(d.get("tilt") or _forward()["tilt"]),
        "zoom": int(d.get("zoom") or _forward()["zoom"]),
    }


def sweep_zoom(site: Site) -> int:
    """Zoom for looking at a seat. Never invent a tighter door crop."""
    try:
        return int(load_stops(site).get("sweep_zoom") or 100)
    except (TypeError, ValueError):
        return 100


def go_home(site: Site, lease_id: str | None = None) -> dict[str, Any]:
    """Inspector contract: FF off → de-zoom → door pose WHILE still leased.

    the User 2026-08-16 (seat_shot) and Inspector who_is_here 2026-08-14: releasing
    the lease does not move the camera. The PTZ stays wherever the tool left
    it. Home first, with the lease_id on aim, then the caller POSTs
    `/v1/lease/release` so Oracle gets the door FOV.

    Same order as `rail_presence.seat_shot.go_home`.
    """
    global _enroll_fov
    _enroll_fov = {"station": None, "aimed": False}
    cam = site.camera_url.rstrip("/")
    out: dict[str, Any] = {}
    d = door_pose(site)
    try:
        body: dict[str, Any] = {"on": False}
        if lease_id:
            body["lease_id"] = lease_id
        r = _post_json(f"{cam}/v1/facefinder", body, timeout=5.0)
        out["ff_off"] = bool((r or {}).get("ok")) if isinstance(r, dict) else False
    except Exception as e:
        out["ff_off_err"] = type(e).__name__
    try:
        aim: dict[str, Any] = {"pan": d["pan"], "tilt": d["tilt"], "zoom": d["zoom"]}
        if lease_id:
            aim["lease_id"] = lease_id
        r = _post_json(f"{cam}/v1/aim", aim, timeout=25.0)
        out["home"] = bool((r or {}).get("ok")) if isinstance(r, dict) else False
        if isinstance(r, dict):
            out["pan_actual"] = r.get("pan_actual")
        print(
            f"  go_home pan={d['pan']} tilt={d['tilt']} zoom={d['zoom']} "
            f"actual={out.get('pan_actual')} lease={bool(lease_id)}",
            flush=True,
        )
    except Exception as e:
        out["home_err"] = type(e).__name__
        print(f"  go_home aim fail: {e!r}", flush=True)
    try:
        _post_json(
            f"{cam}/v1/return_door",
            {"force": True, "force_door": True},
            timeout=12.0,
        )
    except Exception:
        pass
    out["zoom"] = d["zoom"]
    return out


def sit_station_id(site: Site) -> str:
    """Sitting beam for desk enroll (this office: factory/desk pan≈0)."""
    enroll = site.raw.get("enroll") or {}
    return str(enroll.get("sit_station") or "desk").strip().lower() or "desk"


def face_quality(rec: dict[str, Any] | None, *, min_det: float | None = None) -> dict[str, Any]:
    """Score a face capture for enroll gate (no second SoT — just pass/fail + reasons).

    Pass when: jpg ok, the face engine emb present, det_score >= min, bbox large enough.
    """
    import os

    thr = float(
        min_det
        if min_det is not None
        else os.environ.get("HOUSE_ENROLL_FACE_MIN_DET", "0.50")
    )
    min_bbox = float(os.environ.get("HOUSE_ENROLL_FACE_MIN_BBOX", "80"))  # px width or height
    reasons: list[str] = []
    if not rec or not rec.get("ok"):
        return {
            "pass": False,
            "score": 0.0,
            "reasons": ["no_capture"],
            "det_score": None,
        }
    det = rec.get("det_score")
    try:
        det_f = float(det) if det is not None else None
    except (TypeError, ValueError):
        det_f = None
    emb = rec.get("encoding")
    if not emb:
        reasons.append("no_embedding")
    if det_f is None:
        reasons.append("no_det_score")
    elif det_f < thr:
        reasons.append(f"det_score_low:{det_f:.3f}<{thr:.2f}")
    bbox = rec.get("bbox") or []
    if len(bbox) >= 4:
        try:
            w = float(bbox[2]) - float(bbox[0])
            h = float(bbox[3]) - float(bbox[1])
            if min(w, h) < min_bbox:
                reasons.append(f"bbox_small:{min(w, h):.0f}px")
        except (TypeError, ValueError):
            reasons.append("bbox_bad")
    else:
        if emb:
            reasons.append("no_bbox")  # soft — emb alone may still pass below
    # Pass if emb + det ok (bbox small is soft warn if det strong)
    hard = [r for r in reasons if not r.startswith("bbox") and r != "no_bbox"]
    if "no_bbox" in reasons and emb and det_f is not None and det_f >= thr:
        hard = [r for r in hard if r != "no_bbox"]
        reasons = [r for r in reasons if r != "no_bbox"]
    if "bbox_small" in " ".join(reasons) and emb and det_f is not None and det_f >= thr + 0.1:
        hard = [r for r in hard if not str(r).startswith("bbox_small")]
    ok = not hard and bool(emb) and det_f is not None and det_f >= thr
    score = float(det_f or 0.0)
    return {
        "pass": bool(ok),
        "score": score,
        "reasons": reasons if not ok else ([] if not reasons else reasons),
        "det_score": det_f,
        "has_emb": bool(emb),
        "jpg_path": rec.get("jpg_path"),
        "json_path": rec.get("json_path"),
    }


def restore_door_home(site: Site) -> None:
    """Return camera to **ingress rest** after sit enroll (presence law). One shot."""
    global _enroll_fov
    cam = site.camera_url.rstrip("/")
    rest = rest_station_id(site)
    _enroll_fov = {"station": None, "aimed": False}
    try:
        _release_facelock(site, times=1)
        try:
            out = _post_json(f"{cam}/v1/return_door", {}, timeout=12.0)
            print(f"  camera rest via return_door → {out}", flush=True)
            return
        except Exception as e:
            print(f"  return_door fail ({e!r}) — plain aim {rest}", flush=True)
        pan, tilt = station_pt(site, rest)
        _post_json(f"{cam}/aim", {"pan": pan, "tilt": tilt, "hold_sec": 0})
        time.sleep(0.5)
        print(
            f"  camera rest → {rest} pan={pan} actual={_pan_actual(site)} (ingress)",
            flush=True,
        )
    except Exception as e:
        print(f"  restore door aim fail: {e!r}", flush=True)
