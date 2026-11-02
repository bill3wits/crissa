"""The shared glass prefs — one record on :8099 for the settings a User expects to
be the same destination on the wallpaper and on Crissa's Brave page.

Connect Walls step 2 (2026-09-07). Shared keys (docs/glass-contract.yaml
prefs.shared): ft_mode, ft_size, blur, log_board, log_room, log_tools,
vitals_folded, pip_bar, ft_bar, oracle_dot. Wallpaper-only keys (folders,
flourish_pct, hole_ar, mode_bar) never land here; `density` joined the shared
set on 2026-09-15 and `brightness` on 2026-09-16 (the User sets the ASCII
texture, and how bright it burns, once).

Merge rule, last-write-wins: `rev` bumps once per POST that changes anything;
a POST is a partial dict merged over the current record, so a late POST never
resurrects stale values for keys it does not carry. Clients apply a record
only when its rev is newer than the one they hold and adopt the response to
their own POST. Boot: an empty store hydrates from the wallpaper's own file
(the desk wins) at rev 1 — Grok's boot hole: defaults at rev 0 would have
overlaid the wallpaper while Brave still showed its localStorage.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import house_wall_prefs as wall_prefs
import crissa_home

SHARED_KEYS: tuple[str, ...] = (
    "ft_mode", "ft_size", "blur", "ft_fade", "log_board", "log_room", "log_tools",
    "vitals_folded", "pip_bar", "ft_bar", "oracle_dot",
    # chars per cm² of glass. Shared since 2026-09-15: it is a look the User
    # sets once, and it is scale-invariant (a multiplier on a vw-derived cell),
    # so the same number is the same texture on a 2560 wall and in a window.
    "density",
    # how hard the ASCII layer burns — it IS the #ascii opacity, on both
    # glasses (the wallpaper bakes it into the ember palette, the page sets the
    # rule). Shared 2026-09-16, the User: "Can you add a brightness dial to the
    # Walls? We used to have one." The two renderers are calibrated
    # differently (PIL glyphs need more than the page's CSS), so the PAGE
    # divides by BRIGHT_REF — one number, same picture. Not scale-invariant
    # like density, which is why this needed a reference and density did not.
    "brightness",
    # her film's zoom under the whole frame (the User 2026-10-12, lib/wall_frame.py): one number, both glasses
    "frame_zoom",
)
DEFAULTS: dict = {k: wall_prefs.DEFAULTS[k] for k in SHARED_KEYS}
# VO_GLASS_STATE, else this machine's own record (config/house-machine.json `glass_prefs`), else the
# install's state directory (lib/crissa_home) - a machine's path lives in config, never here (2026-10-06).
PATH = Path(os.environ.get("VO_GLASS_STATE", "").strip() or crissa_home.house_value("glass_prefs")
            or str(crissa_home.HOME / "glass-prefs.json"))


def coerce(key: str, value):
    """A value the record will hold, or None to refuse it."""
    if key not in SHARED_KEYS:
        return None
    if key == "ft_mode":
        v = str(value or "").lower()
        return v if v in ("ft", "room") else None
    if key == "density":
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return round(max(wall_prefs.DENSITY_MIN, min(wall_prefs.DENSITY_MAX, f)), 3)
    if key == "brightness":
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return round(max(wall_prefs.BRIGHT_MIN, min(wall_prefs.BRIGHT_MAX, f)), 3)
    if key == "frame_zoom":
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        import wall_frame
        return wall_frame.clamp_zoom(f)
    if key == "ft_size":
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return round(max(wall_prefs.SIZE_MIN, min(wall_prefs.SIZE_MAX, f)), 2)
    if isinstance(value, str):
        if value.lower() in ("true", "1", "on", "yes"):
            return True
        if value.lower() in ("false", "0", "off", "no"):
            return False
        return None
    return bool(value)


class GlassPrefs:
    def __init__(self, path: Path | None = None, wall_file: Path | None = None):
        self.path = Path(path) if path else PATH
        self.wall_file = Path(wall_file) if wall_file else wall_prefs.PATH
        self.lock = threading.RLock()
        self.d: dict = dict(DEFAULTS)
        self.rev = 0
        self.ts = 0.0

    def load(self) -> dict:
        with self.lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = None
            if isinstance(raw, dict):
                for k in SHARED_KEYS:
                    if k in raw:
                        v = coerce(k, raw[k])
                        if v is not None:
                            self.d[k] = v
                try:
                    self.rev = int(raw.get("rev") or 0)
                    self.ts = float(raw.get("ts") or 0.0)
                except (TypeError, ValueError):
                    self.rev, self.ts = 0, 0.0
                if self.rev < 1:
                    self.rev = 1
                return self.record()
            # empty store: the desk's own file wins, at rev 1
            self._hydrate_from_wall()
            self.rev = 1
            self.ts = time.time()
            self.save()
            return self.record()

    def _hydrate_from_wall(self) -> None:
        try:
            raw = json.loads(self.wall_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for k in SHARED_KEYS:
            if k in raw:
                v = coerce(k, raw[k])
                if v is not None:
                    self.d[k] = v

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.record(), indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.path)

    def record(self) -> dict:
        with self.lock:
            out = {k: self.d[k] for k in SHARED_KEYS}
            out["rev"] = self.rev
            out["ts"] = self.ts
            return out

    def apply(self, partial: dict) -> dict:
        """Merge a partial over the record; bump rev only when something changed."""
        with self.lock:
            changed = False
            for k, v in (partial or {}).items():
                if k not in SHARED_KEYS:
                    continue
                cv = coerce(k, v)
                if cv is None:
                    continue
                if self.d[k] != cv:
                    self.d[k] = cv
                    changed = True
            if changed:
                self.rev += 1
                self.ts = time.time()
                self.save()
            return self.record()
