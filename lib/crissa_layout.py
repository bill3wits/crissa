"""Where her render sits while a card is up (2026-10-06).

A card on /v1/screen (words, a sheet, a page) comes up on the right of the Wall. While it is up, her render
(the video avatar, img#face on display/crissa-wall.html) moves to sit exactly on top of the Oracle live tile,
so she is seen talking to the doc; when the card comes down she goes home. Nothing else moves: the card and
the art on it stay where they are, and the ASCII Wall is not touched (this only reads the anchors it already
publishes).

Where the tile is: config, never code. `config/crissa-wall-layout.json` "card_render", under
`<crissa home>/wall-layout.json`:
  anchor        the ASCII Wall's anchor for the Oracle live tile ("camera_box": the live feed tile)
  fallback      its placeholder's spot when the live tile is missing (no Link): "pip"
  anchors_file  the file the ASCII Wall writes its anchors to, under <crissa home>
  box_if_no_wall  used when no ASCII Wall has written anchors (fraction of the page by default)
  move          false keeps her home while a card is up

No timers: `decide()` is called when the card changes (POST /v1/screen) and when the page asks
(GET /v1/layout, which the page does when it sees the card change). The player logs each change.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

try:
    from crissa_home import HOME
except ImportError:  # imported by path from a test or a tool
    HOME = Path(os.environ.get("CRISSA_HOME") or (Path.home() / ".crissa")).expanduser()

SHIPPED = Path(__file__).resolve().parent.parent / "config" / "crissa-wall-layout.json"
FALLBACK = {"card_render": {"move": True, "anchor": "camera_box", "fallback": "pip",
                            "anchors_file": "house-wall-ascii.anchors.json", "unit": "screen_px",
                            "box_if_no_wall": [0.004, 0.811, 0.16, 0.16], "box_if_no_wall_unit": "fraction"}}


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load(home: Path | None = None, shipped: Path | None = None) -> dict:
    """The card_render settings: shipped file under the home file (later wins, key by key)."""
    out = copy.deepcopy(FALLBACK["card_render"])
    for d in (_read(shipped or SHIPPED), _read(Path(home or HOME) / "wall-layout.json")):
        part = d.get("card_render")
        if isinstance(part, dict):
            out.update({k: v for k, v in part.items() if k in out})
    return out


def _box(v) -> list | None:
    if isinstance(v, (list, tuple)) and len(v) >= 4:
        try:
            b = [float(x) for x in v[:4]]
        except (TypeError, ValueError):
            return None
        if b[2] > 0 and b[3] > 0:
            return b
    return None


def anchors(cfg: dict, home: Path | None = None) -> dict:
    """The ASCII Wall's published anchors (read only), {} when it has written none."""
    return _read(Path(home or HOME) / str(cfg.get("anchors_file") or ""))


# SHE STAYS IN VIEW BESIDE THE PAGE (round 27, the User 2026-10-16 on .101: "I do not see her render window during
# display (she is hidden?) - use the home build functionality where she goes to center or over Oracle but not hidden").
# The Oracle live tile there was 409 x 230 px in a corner. Anchor "center" puts her large and centred in the room the
# letter sheet leaves on the left (the sheet is crissa_window.letter_rect: 40 % of the width, 2 % from the right), as a
# 16:9 box CENTER_W of the screen's width. The shipped config names it; a home wall-layout.json can still name the tile.
CENTER_W = 0.46
SHEET_LEFT = 0.58                     # 1 - 0.40 - 0.02: where the letter sheet starts, as a fraction of the width


# TUCKED AGAINST THE PAGE (round 36, the User on .101 round 35: "can we tuck the render window more towards the display
# page?"): the box keeps its size and its vertical centre, and its right edge sits TUCK_GAP of the width before the
# sheet's left edge instead of centred in the room the sheet leaves. A pack only; kill CRISSA_LAYOUT_TUCK=0.
TUCK_GAP = 0.01


def tuck_on() -> bool:
    try:
        import crissa_home
        if crissa_home.is_house():
            return False
    except Exception:  # noqa: BLE001  imported by path from a test: the switch decides
        pass
    return os.environ.get("CRISSA_LAYOUT_TUCK", "1").strip().lower() not in ("0", "off", "false", "no")


# THE SHEET'S REAL LEFT EDGE (round 37, .101 round 36 A: "tuck her to the real edge"): SHEET_LEFT 0.58 holds only where
# the letter is 40 % of the width; on a wide glass it is held by the height (crissa_window.letter_rect: on 3440x1440 it
# is 1041 px wide from x 2330, 0.677), so the tuck left an 865 px gap. The edge is now letter_rect's own, for the real
# screen. Kill CRISSA_LAYOUT_SHEET_REAL=0 (0.58, round 36).
def sheet_left(screen: tuple | None) -> float:
    """Where the letter sheet starts, as a fraction of the width (crissa_window.letter_rect's formula)."""
    if not screen or os.environ.get("CRISSA_LAYOUT_SHEET_REAL", "1").strip().lower() in ("0", "off", "false", "no"):
        return SHEET_LEFT
    try:
        sw, sh = float(screen[0]), float(screen[1])
        w = min(0.40 * sw, 0.936 * sh * 8.5 / 11.0)
        return (sw - 0.02 * sw - w) / sw
    except (TypeError, ValueError, ZeroDivisionError, IndexError):
        return SHEET_LEFT


def center_box(screen: tuple | None = None, tuck: bool | None = None) -> list:
    """[x, y, w, h] as fractions of the page: a 16:9 box CENTER_W wide left of the sheet, tucked against it (a pack)
    or centred in the room it leaves."""
    sw, sh = screen or (16, 9)
    try:
        ratio = float(sw) / float(sh)
    except (TypeError, ValueError, ZeroDivisionError):
        ratio = 16 / 9
    w = CENTER_W
    h = min(0.9, w * ratio * 9.0 / 16.0)
    tucked = tuck_on() if tuck is None else tuck
    left = sheet_left(screen) if (tucked and screen) else SHEET_LEFT
    w = min(w, max(0.05, left - TUCK_GAP))             # a narrow room: the box fits in it
    x = left - TUCK_GAP - w if tucked else (SHEET_LEFT - w) / 2.0
    return [round(max(0.0, x), 4), round((1.0 - h) / 2.0, 4), w, round(h, 4)]


# A CARD WITH NO PAGE BODY SENDS HER HOME (round 36, .101 round 35 FAIL 2: "when she removed the page she stayed up"):
# the removal left the title, the player logged `card up (title)`, and the layout stayed `render on center`. Her render
# sits beside a card only while it carries something to read or see (PAGE_BODY). A pack only; kill
# CRISSA_LAYOUT_PAGE_BODY=0 (any card, round 35).
PAGE_BODY = ("notes", "doc", "art", "url", "html")


def page_up(view: dict | None) -> bool:
    """Whether her render moves for this card (the player's SCREEN.view())."""
    v = view if isinstance(view, dict) else {}
    if not v.get("up"):
        return False
    try:
        import crissa_home
        if crissa_home.is_house():
            return True
    except Exception:  # noqa: BLE001
        pass
    if os.environ.get("CRISSA_LAYOUT_PAGE_BODY", "1").strip().lower() in ("0", "off", "false", "no"):
        return True
    return any(v.get(k) not in (None, "", [], {}) for k in PAGE_BODY)


def _screen() -> tuple | None:
    try:
        import crissa_window
        return crissa_window.screen_size()
    except Exception:  # noqa: BLE001
        return None


def decide(card_up: bool, cfg: dict | None = None, marks: dict | None = None, home: Path | None = None,
           screen: tuple | None = None) -> dict:
    """{"render": "home"|"on_tile", "box", "unit", "spot", "why"} for the card state."""
    cfg = cfg if cfg is not None else load(home)
    if not card_up:
        return {"render": "home", "box": None, "unit": None, "spot": "home", "why": "no card up"}
    if not cfg.get("move", True):
        return {"render": "home", "box": None, "unit": None, "spot": "home",
                "why": "card_render.move is false in the wall layout config"}
    if str(cfg.get("anchor") or "") == "center":
        return {"render": "on_tile", "box": center_box(screen or _screen()), "unit": "fraction", "spot": "center",
                "why": "centred beside the page, in view"}
    marks = marks if marks is not None else anchors(cfg, home)
    live, spare = str(cfg.get("anchor") or ""), str(cfg.get("fallback") or "")
    b = _box(marks.get(live)) if live else None
    if b:
        return {"render": "on_tile", "box": b, "unit": str(cfg.get("unit") or "screen_px"), "spot": live,
                "why": "on the Oracle live tile"}
    b = _box(marks.get(spare)) if spare else None
    if b:
        return {"render": "on_tile", "box": b, "unit": str(cfg.get("unit") or "screen_px"), "spot": spare,
                "why": "Oracle live tile missing (no Link): on its placeholder's spot"}
    b = _box(cfg.get("box_if_no_wall"))
    if b:
        return {"render": "on_tile", "box": b, "unit": str(cfg.get("box_if_no_wall_unit") or "fraction"),
                "spot": "box_if_no_wall", "why": "no ASCII Wall anchors written: the config's box_if_no_wall"}
    return {"render": "home", "box": None, "unit": None, "spot": "home",
            "why": "no tile, no placeholder and no box_if_no_wall in the wall layout config"}


def line(d: dict) -> str:
    """One log line for the player's action log."""
    if d.get("render") != "on_tile":
        return "render home (%s)" % d.get("why")
    return "render on %s %s %s (%s)" % (d.get("spot"), [round(x, 3) for x in d["box"]], d.get("unit"), d.get("why"))
