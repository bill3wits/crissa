"""Where her picture sits on the wall: one rule for the document slide (the User 2026-10-09).

The wall cuts her picture out of the film at a crop origin. This module decides that origin:

1. A flourish's own slide (``crissa-render-shift.json`` without ``why: doc``) wins, as it always has.
2. While the glass has a page up (``/v1/house`` ``screen.doc`` or ``screen.notes``), she sits where the
   User dragged her (``crissa-doc-place.json``); never dragged, she does not move (the User 2026-10-09:
   "no movement unless draggable"). ``HOUSE_DOC_SLIDE_DEFAULT=1`` slides her to the measured default
   (DOC_CX, DOC_CY) instead, as on 10-08.
3. Otherwise no slide: today's crop, her in the middle.

A ``why: doc`` file no longer decides anything by itself. On 10-09 the glass's page timer took a sheet
down at ~07:51 and nothing removed the file, so she sat in the bottom-left corner for hours.
Kill ``HOUSE_DOC_SHIFT_FOLLOWS_DOC=0``: the file alone decides again (the old rule). A drag while a
document is up moves her and is kept for the next document; kill ``HOUSE_DOC_PLACE_DRAG=0``.

4. NO PAGE UP, A PACK (the User 2026-10-16: "if clicked AND in normal mode we should make the render window
   draggable - just like when in display mode"): a drag on bare glass with no page up moves her too, and that spot
   is kept apart from the document's (``crissa-home-place.json``), so each mode keeps its own. A press with no move
   does nothing, as before. On by default in a pack, off on the house (``is_house``); ``CRISSA_DRAG_HER=1|0`` sets it
   either way. A double-click on bare glass puts her back in the middle (the file goes).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import crissa_home

# The document slide, measured 2026-10-08 on clip-listen-back: origin (0.2591, 0) puts her right cheek left of
# the page's letter at fov 1.0 and 1.7 and keeps her nose on screen. doc_render_shift reads these.
WHY = "doc"
DOC_CX, DOC_CY = 0.2591, 0.0
DEFAULT = (DOC_CX, DOC_CY)
# ZFRAME's crop as film fractions (house_wall_ascii_render.ZFRAME); an origin past these leaves the film.
CROP_W, CROP_H = 0.675, 0.700


def _on(name: str) -> bool:
    return os.environ.get(name, "1").strip().lower() not in ("0", "false", "no", "off")


def follows_doc() -> bool:
    return _on("HOUSE_DOC_SHIFT_FOLLOWS_DOC")


def drag_enabled() -> bool:
    return follows_doc() and _on("HOUSE_DOC_PLACE_DRAG")


def _on_default(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off", "")


def doc_up(house: dict | None) -> bool:
    """A page is up: the letter (notes) or a document (the User 2026-10-09: one page style, the letter)."""
    scr = (house or {}).get("screen") if isinstance(house, dict) else None
    return bool(isinstance(scr, dict) and (scr.get("doc") or scr.get("notes")))


def shift_path() -> Path:
    return crissa_home.HOME / "crissa-render-shift.json"


def place_path() -> Path:
    return crissa_home.HOME / "crissa-doc-place.json"


def home_place_path() -> Path:
    """Her spot with no page up (a pack, 2026-10-16), kept apart from the document's."""
    return crissa_home.HOME / "crissa-home-place.json"


def free_drag_on(env=None) -> bool:
    """A drag moves her with no page up: a pack yes, the house no; CRISSA_DRAG_HER=1|0 decides either way."""
    env = os.environ if env is None else env
    raw = str(env.get("CRISSA_DRAG_HER", "")).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return not crissa_home.is_house()


def forget_home_place(path: Path | None = None) -> bool:
    """Back to the middle: the no-page spot goes. True when there was one."""
    try:
        (path or home_place_path()).unlink()
        return True
    except FileNotFoundError:
        return False


def _read(p: Path) -> dict | None:
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def _pair(raw: dict | None) -> tuple[float, float] | None:
    if not raw:
        return None
    try:
        cx, cy = float(raw.get("cx") or 0), float(raw.get("cy") or 0)
    except (TypeError, ValueError):
        return None
    return (cx, cy)


def clamp(cx: float, cy: float) -> tuple[float, float]:
    """Keep the crop inside the film."""
    return (round(min(max(0.0, cx), 1.0 - CROP_W), 4), round(min(max(0.0, cy), 1.0 - CROP_H), 4))


def saved(path: Path | None = None) -> tuple[float, float] | None:
    p = _pair(_read(path or place_path()))
    return clamp(*p) if p else None


def save(cx: float, cy: float, path: Path | None = None) -> tuple[float, float]:
    cx, cy = clamp(cx, cy)
    p = path or place_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"cx": cx, "cy": cy}) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return (cx, cy)


def doc_origin(path: Path | None = None) -> tuple[float, float]:
    return saved(path) or DEFAULT


def origin(house: dict | None, *, shift_file: Path | None = None,
           place_file: Path | None = None, home_file: Path | None = None,
           env=None) -> tuple[float, float] | None:
    """The crop origin for this frame, or None for today's crop."""
    if not _on("HOUSE_RENDER_SHIFT"):
        return None
    raw = _read(shift_file or shift_path())
    pair = _pair(raw)
    if pair == (0.0, 0.0):
        pair = None
    if not follows_doc():
        return pair
    if pair and raw.get("why") != WHY:
        return pair
    if doc_up(house):
        # no movement unless dragged (the User 2026-10-09): the kept spot, else where she is;
        # HOUSE_DOC_SLIDE_DEFAULT=1 brings back the measured default slide
        if _on_default("HOUSE_DOC_SLIDE_DEFAULT", "0"):
            return doc_origin(place_file)
        return saved(place_file)
    if drag_enabled() and free_drag_on(env):
        home = saved(home_file or home_place_path())
        return home if home and home != (0.0, 0.0) else None
    return None


def home_placed(house: dict | None, *, home_file: Path | None = None, env=None) -> bool:
    """She sits at a dragged spot with no page up (the frame then takes the kiosk path, as under a page)."""
    if doc_up(house) or not (follows_doc() and drag_enabled() and free_drag_on(env)):
        return False
    home = saved(home_file or home_place_path())
    return bool(home and home != (0.0, 0.0))


def dragged(origin0: tuple[float, float], dx: float, dy: float, W: int, H: int,
            zoom: float | None = None) -> tuple[float, float]:
    """The origin after the pointer moved (dx, dy) screen px from the press. Dragging right moves her right,
    so the crop origin moves left. One screen width is one crop width (wider when the hole is zoomed out)."""
    z = float(zoom) if zoom and zoom > 0 else 1.0
    cx = origin0[0] - dx / max(1, W) * (CROP_W / z)
    cy = origin0[1] - dy / max(1, H) * (CROP_H / z)
    return clamp(cx, cy)


# ---- the letter's scroll: Crissa's tool writes a command, the wall applies it once (the User 2026-10-09) ----------
SCROLL_CMDS = ("down", "up", "top", "bottom")


def scroll_path() -> Path:
    return crissa_home.HOME / "crissa-letter-scroll.json"


def scroll_enabled() -> bool:
    return _on("HOUSE_LETTER_SCROLL")


def ask_scroll(cmd: str, path: Path | None = None) -> dict:
    """Write one scroll command for the wall. `seq` makes each request distinct."""
    import time
    cmd = str(cmd or "").strip().lower()
    if cmd not in SCROLL_CMDS:
        raise ValueError(f"scroll takes one of {', '.join(SCROLL_CMDS)}")
    row = {"cmd": cmd, "seq": time.time_ns()}
    p = path or scroll_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(row) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return row


def read_scroll(path: Path | None = None) -> dict | None:
    raw = _read(path or scroll_path())
    if not raw or raw.get("cmd") not in SCROLL_CMDS:
        return None
    return raw


def apply_scroll(line: int, cmd: str, total: int, per_page: int) -> int:
    """The first line shown after one command. A page keeps one line of the last for context."""
    last = max(0, int(total) - max(1, int(per_page)))
    step = max(1, int(per_page) - 1)
    if cmd == "top":
        return 0
    if cmd == "bottom":
        return last
    if cmd == "down":
        return min(last, int(line) + step)
    if cmd == "up":
        return max(0, int(line) - step)
    return max(0, min(last, int(line)))


def wheel_scroll(line: int, up: bool, total: int, per_page: int, step: int = 3) -> int:
    last = max(0, int(total) - max(1, int(per_page)))
    return max(0, min(last, int(line) + (-step if up else step)))


# ---- one page style: the right-hand letter (the User 2026-10-09) ---------------------------------------------------
def page_as_letter() -> bool:
    """A page goes up as the letter (notes) and never as `doc`, which is what opens the middle pop-up window.
    HOUSE_PAGE_AS_LETTER=0 = the old fields."""
    return _on("HOUSE_PAGE_AS_LETTER")


def page_payload(title: str, body: str, ttl: float | None = None, letter: bool | None = None) -> dict:
    """The /v1/screen fields for one page. `letter` overrides the switch (a sender that must fall back)."""
    if page_as_letter() if letter is None else letter:
        out = {"notes": body, "notes_title": (title or "")[:60] or None}
        if ttl is not None:
            out["notes_ttl"] = ttl
        return out
    out = {"doc": body, "doc_title": title}
    if ttl is not None:
        out["doc_ttl"] = ttl
    return out


def page_field() -> str:
    return "notes" if page_as_letter() else "doc"
