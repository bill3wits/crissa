"""Empty-room multi-stop reference capture for THIS office.

Writes under site.room_refs.dir (default house-id/site/reference-frames/).
Uses rail-presence empty gate + rail-camera aim/snapshot.
Snapshot is already true-handed (grabber HFLIP). Never flip again.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .site import Site


def _get_json(url: str, timeout: float = 8.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post_json(url: str, body: dict, timeout: float = 20.0) -> bytes:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_bytes(url: str, timeout: float = 15.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def presence_empty(site: Site) -> tuple[bool, str]:
    try:
        d = _get_json(f"{site.presence_url.rstrip('/')}/v1/presence")
    except Exception as e:
        return False, f"presence error: {e}"
    hc = int(d.get("headcount") or 0)
    blind = bool(d.get("camera_blind"))
    named = [
        p.get("name")
        for p in (d.get("people") or [])
        if p.get("name") and not (p.get("sources") or {}).get("provisional")
    ]
    if blind:
        return False, "camera_blind"
    if hc != 0 or named:
        return False, f"hc={hc} named={named}"
    return True, f"empty trust={d.get('count_trust')} certain={d.get('count_certain')}"


def camera_ok(site: Site) -> bool:
    try:
        _get_json(f"{site.camera_url.rstrip('/')}/health", timeout=4)
        return True
    except Exception:
        try:
            urllib.request.urlopen(
                f"{site.camera_url.rstrip('/')}/health", timeout=4
            ).read()
            return True
        except Exception:
            return False


def capture_room_refs(site: Site, *, force: bool = False) -> int:
    """
    Capture empty-room refs. Returns number of stop frames written.
    Raises RuntimeError on hard refuse.
    """
    if not force:
        print(
            f"Verifying room empty via {site.presence_url} "
            f"({site.empty_polls} × {site.empty_interval_sec}s)..."
        )
        for i in range(1, site.empty_polls + 1):
            ok, msg = presence_empty(site)
            if not ok:
                raise RuntimeError(f"REFUSING: poll {i} — {msg}")
            print(f"  poll {i}/{site.empty_polls}: {msg}")
            if i < site.empty_polls:
                time.sleep(site.empty_interval_sec)
    else:
        print("FORCE mode — empty gate skipped.")

    if not camera_ok(site):
        raise RuntimeError(f"REFUSING: camera unhealthy at {site.camera_url}")

    stops_path = site.stops_json()
    if stops_path is None or not stops_path.is_file():
        raise RuntimeError(
            "site.room_refs.stops_json missing or not found — set path in site.json"
        )
    cfg = json.loads(stops_path.read_text())
    stops = cfg.get("stops") or []
    door = cfg.get("door") or {"pan": 0, "tilt": 0}
    settle = float(cfg.get("settle_sec") or 1.2)
    cam = site.camera_url.rstrip("/")

    out = site.room_refs_dir()
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    supersede = out / f"_superseded-{stamp}"
    supersede.mkdir(parents=True, exist_ok=True)
    for pattern in ("room-empty-stop*.jpg", "home-empty-*.jpg"):
        for f in out.glob(pattern):
            f.rename(supersede / f.name)

    def aim(pan: int, tilt: int = 0) -> None:
        _post_json(f"{cam}/aim", {"pan": int(pan), "tilt": int(tilt)})

    def snap(path: Path) -> None:
        path.write_bytes(_get_bytes(f"{cam}/snapshot"))

    n_ok = 0
    print(f"Rails multi-stop capture → {out} ...")
    for i, s in enumerate(stops):
        sid = s.get("id") or i
        pan = int(s["pan"])
        tilt = int(s.get("tilt") or 0)
        try:
            aim(pan, tilt)
            time.sleep(settle)
            dest = out / f"room-empty-stop{i}.jpg"
            snap(dest)
            print(f"  ok stop{i} id={sid} pan={pan} -> {dest.name} ({dest.stat().st_size}b)")
            n_ok += 1
        except Exception as e:
            print(f"  FAIL stop{i} id={sid}: {e}")

    try:
        aim(int(door.get("pan", 0)), int(door.get("tilt", 0)))
        time.sleep(settle)
        snap(out / "home-empty-open.jpg")
        snap(out / "home-empty-closed.jpg")
        print("  ok home-empty-open/closed")
    except Exception as e:
        print(f"  FAIL home: {e}")

    try:
        aim(int(door.get("pan", 0)), int(door.get("tilt", 0)))
    except Exception:
        pass

    meta = {
        "schema": "house_room_refs/v1",
        "site_id": site.site_id,
        "ts_iso": datetime.now().isoformat(timespec="seconds"),
        "n_stops_ok": n_ok,
        "n_stops": len(stops),
        "dir": str(out),
        "stops_json": str(stops_path),
    }
    (out / "last_capture.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"DONE site={site.site_id} n_ok={n_ok}/{len(stops)} → {out}")
    if n_ok < 1:
        raise RuntimeError("no stop frames written")
    return n_ok
