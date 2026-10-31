"""Rolling raw camera feed on /data for security review (the User 2026-08-02).

The live grabber ring is only ~5s in RAM. Door event packs are ~5s / episode
subsamples. That is not enough for leave reconstruction or stranger-inject
review.

This module keeps a **disk** rolling window of raw JPEGs (default **1 hour**)
under ``<records>/raw/``, chunked as::

  raw/YYYYMMDD/HHMM/f_<ms>_<seq>.jpg   # one folder per minute (easy prune)
  incidents/incident_<ts>/             # frozen copies; never auto-pruned

Live grabber RAM ring is separate (~20s). Disk tape is time-travel only.

- Continuous write from grabber (``RAIL_SECURITY_RAW_FPS``, often 15–30).
- On door event: **pin** surrounding raw next to the event pack.
- On stranger inject: **freeze** last hour (default) into ``incidents/``.
- Maintenance: in-process prune every 30s + hourly cron ``security-raw-prune``.
- the User 2026-08-11: retain **1 hour** by default (was 1d / 7d in some drop-ins).

Env:
  RAIL_SECURITY_RAW=1|0          enable (default 1)
  RAIL_SECURITY_RAW_DIR          default <records>
  RAIL_SECURITY_RAW_SEC          retain seconds (default **3600** = 1 hour)
  RAIL_SECURITY_RAW_FPS          write rate (default 5)
  RAIL_SECURITY_EVENT_PAD_SEC    pre/post pin around event (default 120)
  RAIL_SECURITY_FREEZE_SEC       default freeze window on inject (default 3600)
"""

from __future__ import annotations

from . import paths as _paths

import json
import os
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

ENABLED = os.environ.get("RAIL_SECURITY_RAW", "1") not in ("0", "false", "no")
ROOT = Path(
    os.environ.get("RAIL_SECURITY_RAW_DIR", str(_paths.RECORDS))
).expanduser()
RAW_DIR = ROOT / "raw"
INCIDENTS_DIR = ROOT / "incidents"
# Default one hour of rolling raw; cron + in-process prune enforce the cap.
RETAIN_SEC = max(600.0, float(os.environ.get("RAIL_SECURITY_RAW_SEC", "3600")))
WRITE_FPS = max(1.0, float(os.environ.get("RAIL_SECURITY_RAW_FPS", "5")))
EVENT_PAD_SEC = float(os.environ.get("RAIL_SECURITY_EVENT_PAD_SEC", "120"))
# Stranger inject freeze: last hour of evidence by default (full day still in raw)
FREEZE_SEC = max(900.0, float(os.environ.get("RAIL_SECURITY_FREEZE_SEC", "3600")))
# soft cap queue so a slow disk never stalls V4L
_QUEUE_MAX = int(os.environ.get("RAIL_SECURITY_RAW_QUEUE", "120"))

_lock = threading.Lock()
_q: deque[tuple[int, float, bytes]] = deque()
_last_write_ts = 0.0
_period = 1.0 / WRITE_FPS
_thread: threading.Thread | None = None
_stop = threading.Event()
_LAST_FREEZE_TS = 0.0
_LAST_FREEZE_ID = ""
FREEZE_DEBOUNCE_SEC = float(os.environ.get("RAIL_SECURITY_FREEZE_DEBOUNCE", "120"))
_stats: dict[str, Any] = {
    "written": 0,
    "dropped_queue": 0,
    "dropped_rate": 0,
    "pruned_files": 0,
    "last_err": None,
    "last_path": None,
    "started_ts": None,
}


def _minute_dir(ts: float) -> Path:
    # <records>/raw/YYYYMMDD/HHMM/
    return RAW_DIR / time.strftime("%Y%m%d", time.localtime(ts)) / time.strftime(
        "%H%M", time.localtime(ts)
    )


def _frame_name(seq: int, ts: float) -> str:
    # sort-friendly: ms + seq
    ms = int(ts * 1000)
    return f"f_{ms}_{seq:09d}.jpg"


def status() -> dict[str, Any]:
    with _lock:
        st = dict(_stats)
        qn = len(_q)
    nbytes = 0
    nfiles = 0
    oldest = newest = None
    try:
        if RAW_DIR.is_dir():
            for p in RAW_DIR.rglob("f_*.jpg"):
                nfiles += 1
                try:
                    nbytes += p.stat().st_size
                    # parse ms from name
                    part = p.name.split("_")[1]
                    ms = int(part)
                    if oldest is None or ms < oldest:
                        oldest = ms
                    if newest is None or ms > newest:
                        newest = ms
                except Exception:
                    pass
    except Exception as e:
        st["scan_err"] = str(e)[:120]
    span = None
    if oldest is not None and newest is not None:
        span = round((newest - oldest) / 1000.0, 1)
    return {
        "ok": True,
        "enabled": ENABLED,
        "root": str(ROOT),
        "raw_dir": str(RAW_DIR),
        "incidents_dir": str(INCIDENTS_DIR),
        "retain_sec": RETAIN_SEC,
        "retain_hours": round(RETAIN_SEC / 3600.0, 2),
        "freeze_sec_default": FREEZE_SEC,
        "write_fps": WRITE_FPS,
        "event_pad_sec": EVENT_PAD_SEC,
        "chunk": "raw/YYYYMMDD/HHMM/ (minute folders)",
        "queue_n": qn,
        "files_on_disk": nfiles,
        "bytes_on_disk": nbytes,
        "span_sec_approx": span,
        "oldest_ms": oldest,
        "newest_ms": newest,
        "stats": st,
        "note": (
            "Rolling raw on /data for security. Live grabber ring remains ~5s RAM. "
            "Door events pin surrounding raw; stranger inject freezes full retain window."
        ),
    }


def offer_frame(seq: int, ts: float, jpeg: bytes) -> None:
    """Called from grabber hot path — non-blocking, rate-limited."""
    if not ENABLED or not jpeg:
        return
    global _last_write_ts
    now = ts or time.time()
    if now - _last_write_ts < _period * 0.95:
        with _lock:
            _stats["dropped_rate"] = int(_stats.get("dropped_rate") or 0) + 1
        return
    _last_write_ts = now
    with _lock:
        if len(_q) >= _QUEUE_MAX:
            try:
                _q.popleft()
            except IndexError:
                pass
            _stats["dropped_queue"] = int(_stats.get("dropped_queue") or 0) + 1
        _q.append((int(seq), float(now), jpeg))


def _write_one(seq: int, ts: float, jpeg: bytes) -> None:
    d = _minute_dir(ts)
    d.mkdir(parents=True, exist_ok=True)
    name = _frame_name(seq, ts)
    path = d / name
    # atomic-ish
    tmp = d / (name + ".tmp")
    tmp.write_bytes(jpeg)
    tmp.replace(path)
    # append index line (best effort)
    idx = d / "index.jsonl"
    try:
        with idx.open("a") as f:
            f.write(
                json.dumps({"seq": seq, "ts": ts, "file": name, "n": len(jpeg)})
                + "\n"
            )
    except Exception:
        pass
    with _lock:
        _stats["written"] = int(_stats.get("written") or 0) + 1
        _stats["last_path"] = str(path)


def prune_raw(*, retain_sec: float | None = None) -> dict[str, Any]:
    """Drop raw chunks older than retain (default 1 day). Never touches incidents/.

    Chunk unit = minute folder ``raw/YYYYMMDD/HHMM/``. Whole minutes older than
    cutoff are removed (faster than per-file). Returns counts for cron logs.
    """
    retain = float(RETAIN_SEC if retain_sec is None else retain_sec)
    cutoff = time.time() - retain
    if not RAW_DIR.is_dir():
        return {"ok": True, "pruned_files": 0, "pruned_dirs": 0, "cutoff": cutoff}
    pruned_files = 0
    pruned_dirs = 0
    try:
        # Prefer whole-minute dir drop when the folder name is older than cutoff
        for day in sorted(RAW_DIR.iterdir()):
            if not day.is_dir():
                continue
            for minute in sorted(day.iterdir()):
                if not minute.is_dir():
                    continue
                # dir name HHMM under YYYYMMDD
                try:
                    folder_ts = time.mktime(
                        time.strptime(day.name + minute.name, "%Y%m%d%H%M")
                    )
                except Exception:
                    folder_ts = None
                if folder_ts is not None and folder_ts + 60 < cutoff:
                    # entire minute chunk is stale
                    n = 0
                    for p in minute.glob("*"):
                        try:
                            if p.is_file():
                                p.unlink(missing_ok=True)
                                n += 1
                        except Exception:
                            pass
                    try:
                        shutil.rmtree(minute, ignore_errors=True)
                        pruned_dirs += 1
                        pruned_files += n
                    except Exception:
                        pass
                    continue
                # partial: drop old files inside current/near-cutoff minutes
                for p in minute.glob("f_*.jpg"):
                    try:
                        ms = int(p.name.split("_")[1])
                        ts = ms / 1000.0
                    except Exception:
                        ts = p.stat().st_mtime
                    if ts < cutoff:
                        try:
                            p.unlink(missing_ok=True)
                            pruned_files += 1
                        except Exception:
                            pass
                # empty minute
                try:
                    if minute.is_dir() and not any(minute.iterdir()):
                        minute.rmdir()
                        pruned_dirs += 1
                except Exception:
                    pass
            try:
                if day.is_dir() and not any(day.iterdir()):
                    day.rmdir()
                    pruned_dirs += 1
            except Exception:
                pass
    except Exception as e:
        with _lock:
            _stats["last_err"] = f"prune: {e}"[:160]
        return {
            "ok": False,
            "error": str(e)[:200],
            "pruned_files": pruned_files,
            "pruned_dirs": pruned_dirs,
            "cutoff": cutoff,
            "retain_sec": retain,
        }
    if pruned_files or pruned_dirs:
        with _lock:
            _stats["pruned_files"] = int(_stats.get("pruned_files") or 0) + pruned_files
    return {
        "ok": True,
        "pruned_files": pruned_files,
        "pruned_dirs": pruned_dirs,
        "cutoff": cutoff,
        "retain_sec": retain,
        "retain_hours": round(retain / 3600.0, 2),
        "raw_dir": str(RAW_DIR),
    }


def _prune() -> None:
    """In-process maintenance (same as cron)."""
    prune_raw()


def _worker() -> None:
    last_prune = 0.0
    while not _stop.is_set():
        item = None
        with _lock:
            if _q:
                item = _q.popleft()
        if item is None:
            _stop.wait(0.05)
        else:
            seq, ts, jpeg = item
            try:
                _write_one(seq, ts, jpeg)
            except Exception as e:
                with _lock:
                    _stats["last_err"] = str(e)[:200]
        now = time.time()
        if now - last_prune > 30.0:
            last_prune = now
            try:
                _prune()
            except Exception as e:
                with _lock:
                    _stats["last_err"] = f"prune: {e}"[:160]


def start() -> None:
    global _thread
    if not ENABLED:
        return
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "README.txt").write_text(
        "House security raw feed (rolling).\n"
        f"retain_sec={RETAIN_SEC} ({RETAIN_SEC/3600:.1f}h) write_fps={WRITE_FPS}\n"
        f"freeze_sec_default={FREEZE_SEC}\n"
        "chunk: raw/YYYYMMDD/HHMM/  (minute folders; cron prunes to ≤1 day)\n"
        "raw/ = continuous rolling JPEGs\n"
        "incidents/ = frozen security events (do not auto-delete)\n"
        "Door packs: .../gemma_events/ev_*/raw_records/\n"
        "Maintenance: security-raw-prune (hourly cron) + in-process every ~30s\n"
    )
    with _lock:
        if _stats.get("started_ts"):
            return
        _stats["started_ts"] = time.time()
    _stop.clear()
    _thread = threading.Thread(target=_worker, name="security-raw", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()


def list_frames_in_range(t0: float, t1: float) -> list[Path]:
    """Return paths of raw frames with ts in [t0, t1]."""
    out: list[Path] = []
    if not RAW_DIR.is_dir():
        return out
    # walk minute dirs spanning range
    t = t0 - 60
    while t <= t1 + 60:
        d = _minute_dir(t)
        if d.is_dir():
            for p in sorted(d.glob("f_*.jpg")):
                try:
                    ms = int(p.name.split("_")[1])
                    ts = ms / 1000.0
                except Exception:
                    continue
                if t0 <= ts <= t1:
                    out.append(p)
        t += 60
    return out


def pin_for_event(
    event_dir: Path | str,
    *,
    t0: float | None = None,
    t1: float | None = None,
    pad_sec: float | None = None,
    reason: str = "door_event",
) -> dict[str, Any]:
    """Copy/hardlink surrounding raw next to a door event pack."""
    ed = Path(event_dir)
    ed.mkdir(parents=True, exist_ok=True)
    pad = float(EVENT_PAD_SEC if pad_sec is None else pad_sec)
    now = time.time()
    if t0 is None or t1 is None:
        # try manifest
        man = ed / "manifest.json"
        if man.is_file():
            try:
                m = json.loads(man.read_text())
                fr = m.get("frames") or []
                if fr:
                    t0 = float(fr[0].get("ts") or now)
                    t1 = float(fr[-1].get("ts") or now)
            except Exception:
                pass
    t0 = float(t0 if t0 is not None else now - 5)
    t1 = float(t1 if t1 is not None else now)
    a, b = t0 - pad, t1 + pad
    dest = ed / "raw_security"
    dest.mkdir(parents=True, exist_ok=True)
    paths = list_frames_in_range(a, b)
    linked = 0
    copied = 0
    for p in paths:
        target = dest / p.name
        if target.exists():
            continue
        try:
            os.link(p, target)
            linked += 1
        except Exception:
            try:
                shutil.copy2(p, target)
                copied += 1
            except Exception:
                pass
    meta = {
        "ok": True,
        "reason": reason,
        "event_dir": str(ed),
        "raw_dir": str(dest),
        "t0": a,
        "t1": b,
        "pad_sec": pad,
        "n_frames": linked + copied,
        "hardlinked": linked,
        "copied": copied,
        "source_span_request": [a, b],
        "note": "Raw security strip pinned next to door event for review",
    }
    (dest / "pin.json").write_text(json.dumps(meta, indent=2))
    # also stamp into event manifest if present
    man = ed / "manifest.json"
    if man.is_file():
        try:
            m = json.loads(man.read_text())
            m["raw_security"] = {
                "dir": str(dest),
                "n": meta["n_frames"],
                "t0": a,
                "t1": b,
            }
            man.write_text(json.dumps(m, indent=2))
        except Exception:
            pass
    return meta


def freeze_incident(
    *,
    reason: str = "security",
    seconds: float | None = None,
    t_end: float | None = None,
    t_start: float | None = None,
    extra: dict | None = None,
) -> dict[str, Any]:
    """Freeze the retain window (default last hour) for user review.

    Use on stranger / inject / untrusted presence. Copies (not only hardlink)
    so prune cannot delete the evidence.

    ``t_start`` is EXACT-SPAN mode (the User 2026-08-31): freeze precisely
    [t_start, t_end] with no 15-minute floor and no debounce. It exists for
    chained attempt events — entry to the 2nd try, then 2nd to 3rd, then 3rd to
    4th — where each span must be its own and must not swallow its neighbours.

    Both guards are skipped in that mode ON PURPOSE, and it is safe only
    because these spans are contiguous and non-overlapping by construction, so
    the total frozen can never exceed the retained hour. The guards exist for
    the opposite case: 13 freezes of the SAME hour in ~10s reached 131 GB
    (measured 2026-08-27). Do not reach for t_start to bypass them generally.
    """
    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    end = float(t_end if t_end is not None else time.time())
    exact = t_start is not None
    if exact:
        start = float(t_start)
        if start >= end:                       # nothing to cut
            return {"ok": False, "reason": "empty_span", "t0": start, "t1": end}
        start = max(start, end - (RETAIN_SEC + 60))   # cannot cut past the cache
        span = end - start
    else:
        # Default freeze = last hour; caller may request up to full retain
        span = float(seconds if seconds is not None else FREEZE_SEC)
        span = max(900.0, min(span, RETAIN_SEC + 60))
        start = end - span
    global _LAST_FREEZE_TS, _LAST_FREEZE_ID
    # Measured 2026-08-27: 13 copies of the same hour in ~10s (reason=security),
    # 4.8G each, never pruned → 131G. One freeze per debounce window.
    now = time.time()
    if (
        not exact
        and _LAST_FREEZE_ID
        and (now - float(_LAST_FREEZE_TS or 0.0)) < FREEZE_DEBOUNCE_SEC
    ):
        latest = INCIDENTS_DIR / _LAST_FREEZE_ID
        return {
            "ok": True,
            "skipped": "debounce",
            "id": _LAST_FREEZE_ID,
            "path": str(latest) if latest.is_dir() else None,
            "reason": reason,
            "debounce_sec": FREEZE_DEBOUNCE_SEC,
        }
    iid = f"incident_{int(end * 1000)}"
    dest = INCIDENTS_DIR / iid
    raw_dest = dest / "raw"
    raw_dest.mkdir(parents=True, exist_ok=True)
    paths = list_frames_in_range(start, end)
    n = 0
    for p in paths:
        target = raw_dest / p.name
        if target.exists():
            continue
        try:
            shutil.copy2(p, target)
            n += 1
        except Exception:
            pass
    meta = {
        "ok": True,
        "id": iid,
        "reason": reason,
        "t0": start,
        "t1": end,
        "span_sec": span,
        "n_frames": n,
        "path": str(dest),
        "raw_path": str(raw_dest),
        "created_ts": time.time(),
        "created_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "extra": extra or {},
        "review": "user — full security window frozen; not auto-pruned",
    }
    (dest / "incident.json").write_text(json.dumps(meta, indent=2))
    _LAST_FREEZE_TS = time.time()
    _LAST_FREEZE_ID = iid
    # pointer for latest
    try:
        (INCIDENTS_DIR / "LATEST").write_text(iid + "\n")
    except Exception:
        pass
    return meta
