"""Camera capture — ONE owner inside this process (mutex).

Backends (env RAIL_PRESENCE_CAMERA):
  http  — GET JPEG from URL (default: external snapshot feed)
  v4l   — /dev/videoN exclusive open
  dir   — newest frames in a directory (multi-stop sweep offline / patrol copies)
  auto  — try v4l, then http, then dir
  off   — no camera (CRISSA_NO_CAMERA=1 or RAIL_PRESENCE_CAMERA=off): every grab is None, nothing is opened
          and nothing is asked (2026-10-10)

Does not import Crissa. Optional HTTP feed may point at any snapshot URL.
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths

import os
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

_ROOT = _paths.ROOT
_lock = threading.RLock()  # single owner of capture ops in this process


@dataclass
class Frame:
    bgr: np.ndarray
    ts: float
    source: str
    stop_id: str | None = None  # e.g. "0" from stop0 in filename
    meta: dict | None = None


class Camera:
    def __init__(
        self,
        mode: str | None = None,
        device: int | str | None = None,
        snapshot_url: str | None = None,
        frames_dir: Path | str | None = None,
    ):
        self.mode = (mode or os.environ.get("RAIL_PRESENCE_CAMERA", "auto")).lower()
        if _ports.camera_off() or self.mode == "none":
            self.mode = "off"
        self.device = device if device is not None else os.environ.get("RAIL_PRESENCE_V4L", "0")
        self.snapshot_url = snapshot_url or os.environ.get(
            "RAIL_PRESENCE_SNAPSHOT_URL",
            _ports.camera("/snapshot"),
        )
        self.frames_dir = Path(
            frames_dir
            or os.environ.get("RAIL_PRESENCE_FRAMES_DIR", str(_ROOT / "data" / "frames"))
        )
        self._cap = None
        self._last: Frame | None = None
        self._last_error: str | None = None

    def close(self) -> None:
        with _lock:
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def grab(self) -> Optional[Frame]:
        """Grab one frame under the process-wide camera lock."""
        with _lock:
            mode = self.mode
            if mode == "auto":
                for m in ("v4l", "http", "dir"):
                    fr = self._grab_mode(m)
                    if fr is not None:
                        self._last = fr
                        self._last_error = None
                        return fr
                self._last_error = self._last_error or "auto: all backends failed"
                return None
            fr = self._grab_mode(mode)
            if fr is not None:
                self._last = fr
                self._last_error = None
            return fr

    def grab_buffer(
        self,
        *,
        since_seq: int | None = None,
        max_frames: int | None = None,
        stride: int = 1,
    ) -> list[Frame]:
        """Door ring buffer from sole-owner grabber (5s @ 15–30fps for face ID).

        Prefer since_seq for delta pulls so we don't re-download 150 frames every tick.
        """
        if self.mode not in ("http", "auto"):
            fr = self.grab()
            return [fr] if fr is not None else []
        import base64
        import json
        import cv2

        base = self.snapshot_url.rsplit("/", 1)[0]
        buf_url = os.environ.get("RAIL_PRESENCE_BUFFER_URL", base + "/v1/buffer")
        payload = {
            "since_seq": since_seq,
            "max": max_frames,
            "stride": max(1, int(stride)),
            "include_jpeg": True,
        }
        # drop nulls
        payload = {k: v for k, v in payload.items() if v is not None}
        try:
            req = urllib.request.Request(
                buf_url,
                data=json.dumps(payload).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode())
        except Exception as e:
            self._last_error = f"buffer: {type(e).__name__}: {e}"[:300]
            # fallback single snapshot
            fr = self.grab()
            return [fr] if fr is not None else []
        out: list[Frame] = []
        for fr in body.get("frames") or []:
            b64 = fr.get("jpeg_b64")
            if not b64:
                continue
            raw = base64.b64decode(b64)
            arr = np.frombuffer(raw, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            ts = float(fr.get("capture_ts") or time.time())
            out.append(
                Frame(
                    bgr=img,
                    ts=ts,
                    source="buffer",
                    stop_id="door",
                    meta={
                        "seq": fr.get("seq"),
                        "sha": fr.get("sha"),
                        "capture_ok": True,
                        "from_buffer": True,
                    },
                )
            )
        if out:
            self._last = out[-1]
            self._last_error = None
        elif not body.get("n") and since_seq is not None:
            # no new frames — not an error
            self._last_error = None
        return out

    def grab_sweep(self) -> list[Frame]:
        """
        Multi-frame sweep for Rule 3/4.
        - http: POST grabber /sweep (PTZ multi-stop) when RAIL_PRESENCE_USE_PTZ_SWEEP=1
        - dir: newest file per stop*
        - else: single grab
        """
        use_ptz = os.environ.get("RAIL_PRESENCE_USE_PTZ_SWEEP", "1") not in (
            "0",
            "false",
            "no",
        )
        ptz_required = os.environ.get("RAIL_PRESENCE_PTZ_REQUIRED", "1") not in (
            "0",
            "false",
            "no",
        )
        if use_ptz and self.mode in ("http", "auto"):
            frames = self._http_ptz_sweep()
            if frames:
                self._last = frames[-1]
                self._last_error = None
                return frames
            # Body-count mode: do NOT fall back to a single door FOV (undercounts the room)
            if ptz_required:
                return []
        with _lock:
            if self.mode in ("dir", "auto") and self.frames_dir.is_dir():
                frames = self._dir_sweep()
                if frames:
                    self._last = frames[-1]
                    self._last_error = None
                    return frames
            fr = self._grab_mode("http" if self.mode == "auto" else self.mode)
            if fr is None and self.mode == "auto":
                fr = self._grab_mode("v4l")
            if fr is None:
                return []
            self._last = fr
            return [fr]

    def _http_ptz_sweep(self) -> list[Frame]:
        """Ask sole-owner grabber to PTZ multi-stop and return decoded frames."""
        import base64
        import json
        import cv2

        sweep_url = os.environ.get(
            "RAIL_PRESENCE_SWEEP_URL",
            self.snapshot_url.replace("/snapshot", "/sweep"),
        )
        if not sweep_url.endswith("/sweep"):
            # e.g. http://127.0.0.1:8192/snapshot -> .../sweep
            base = self.snapshot_url.rsplit("/", 1)[0]
            sweep_url = base + "/sweep"
        try:
            req = urllib.request.Request(
                sweep_url,
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                body = json.loads(r.read().decode())
        except Exception as e:
            self._last_error = f"ptz sweep failed: {type(e).__name__}: {e}"[:300]
            return []
        if not body.get("ok") and not body.get("frames"):
            self._last_error = f"ptz sweep empty: {body.get('door_return')}"
            return []
        out: list[Frame] = []
        for fr in body.get("frames") or []:
            if not fr.get("ok") or not fr.get("jpeg_b64"):
                continue
            raw = base64.b64decode(fr["jpeg_b64"])
            arr = np.frombuffer(raw, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            ts = float(fr.get("capture_ts") or time.time())
            out.append(
                Frame(
                    bgr=img,
                    ts=ts,
                    source=f"sweep:{fr.get('id')}",
                    stop_id=str(fr.get("id")),
                    meta={
                        "seq": fr.get("seq"),
                        "capture_ok": True,
                        "pan_cmd": fr.get("pan_cmd"),
                        "pan_actual": fr.get("pan_actual"),
                        "sha": fr.get("sha"),
                    },
                )
            )
        if not out:
            self._last_error = "ptz sweep: no ok frames"
        return out

    def _grab_mode(self, mode: str) -> Optional[Frame]:
        if mode == "off":
            self._last_error = "camera: off"
            return None
        try:
            if mode == "v4l":
                return self._v4l()
            if mode == "http":
                return self._http()
            if mode == "dir":
                frames = self._dir_sweep()
                return frames[-1] if frames else None
            self._last_error = f"unknown camera mode {mode}"
            return None
        except Exception as e:
            self._last_error = f"{mode}: {e}"[:200]
            return None

    def _v4l(self) -> Optional[Frame]:
        import cv2

        dev = self.device
        idx = int(dev) if str(dev).isdigit() else dev
        if self._cap is None or not self._cap.isOpened():
            self._cap = cv2.VideoCapture(idx)
            if not self._cap.isOpened():
                self._last_error = f"v4l cannot open {idx}"
                return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            self._last_error = "v4l read failed"
            return None
        return Frame(bgr=frame, ts=time.time(), source=f"v4l:{idx}", stop_id="live")

    def _http(self) -> Optional[Frame]:
        import cv2

        req = urllib.request.Request(self.snapshot_url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            # Prefer grabber's real capture time — NOT "now" (that always yields frame_age=0)
            cap_ts = r.headers.get("X-Capture-Ts")
            seq = r.headers.get("X-Frame-Seq")
            age_hdr = r.headers.get("X-Frame-Age-Sec")
            cap_ok = r.headers.get("X-Capture-Ok", "1")
            sha = r.headers.get("X-Frame-Sha")
            raw = r.read()
        arr = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            self._last_error = "http jpeg decode failed"
            return None
        try:
            ts = float(cap_ts) if cap_ts else time.time()
        except ValueError:
            ts = time.time()
        meta = {
            "seq": int(seq) if seq and str(seq).isdigit() else None,
            "capture_ok": cap_ok == "1",
            "sha": sha,
            "reported_age": float(age_hdr) if age_hdr not in (None, "") else None,
        }
        if not meta["capture_ok"]:
            self._last_error = "grabber reports capture not ok (stale/frozen/error)"
            # still return frame so callers can see pixels, but mark via meta
        return Frame(
            bgr=frame,
            ts=ts,
            source=f"http:{self.snapshot_url}",
            stop_id="live",
            meta=meta,
        )

    def _dir_sweep(self) -> list[Frame]:
        import cv2

        if not self.frames_dir.is_dir():
            self._last_error = f"frames dir missing: {self.frames_dir}"
            return []
        newest: dict[str, Path] = {}
        plain: list[Path] = []
        for p in self.frames_dir.glob("*.jpg"):
            m = re.search(r"stop(\d+)", p.name, re.I)
            if m:
                k = m.group(1)
                if k not in newest or p.stat().st_mtime > newest[k].stat().st_mtime:
                    newest[k] = p
            else:
                plain.append(p)
        paths: list[tuple[str | None, Path]] = []
        if newest:
            for k in sorted(newest, key=lambda x: int(x)):
                paths.append((k, newest[k]))
        elif plain:
            # most recent single
            p = max(plain, key=lambda q: q.stat().st_mtime)
            paths.append((None, p))
        out: list[Frame] = []
        for stop, p in paths:
            img = cv2.imread(str(p))
            if img is None:
                continue
            out.append(
                Frame(
                    bgr=img,
                    ts=p.stat().st_mtime,
                    source=f"dir:{p.name}",
                    stop_id=stop if stop is not None else "file",
                )
            )
        if not out:
            self._last_error = "dir: no readable jpg"
        return out
