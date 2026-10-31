"""The phone pack (phone-pack/1): a loop and every mouth the house paints on it, as one video the phone only blits
(the User 2026-10-16: "start the phone pack and see if you could serve the forward loop as an app").

The house paints her mouth per semiframe through a stack of caches (cheeks warp, pixel pin, square alpha, arch hole,
tone). A phone does none of that: the house's own painter (scripts/house-conductor.py `_composite`, run offline by
scripts/phone-pack.py) paints every class on every frame of the loop once, and the pack keeps only the changed box.
Each video frame carries the loop's picture AND all of that frame's class patches beside it, so the phone draws the
picture and the patch for the class at the voice's clock from the SAME decoded frame: the mouth can never be a frame
off the face it was painted for.

    +-----------+-----+-----+-----+
    |           | rest| MBP | FV  |   base: the loop frame, 1:1
    |   base    +-----+-----+-----+   cells: one patch per class, the box `box` of the base
    |           | TH  | ... |     |
    +-----------+-----+-----+-----+

The voice's sheet is house-speak's own (lib/phone_sheet.py): rows [t, class] on the line's audio clock.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

SCHEMA = "phone-pack/1"
PAD = 24            # px around the painted region: the feather on the phone sits in unchanged pixels
FEATHER = 16        # px the phone ramps a patch's edge over (codec noise at the box edge)
MAX_W = 4096        # a phone's H.264 decoder (level 5.1)
DIFF_T = 6          # summed |BGR| a pixel must change by to count as painted


def union_box(boxes: list[tuple[int, int, int, int]], w: int, h: int, pad: int = PAD) -> tuple[int, int, int, int]:
    """(x, y, bw, bh) holding every (x0, y0, x1, y1) plus pad, inside the frame, even sizes (yuv420)."""
    x0 = max(0, min(b[0] for b in boxes) - pad)
    y0 = max(0, min(b[1] for b in boxes) - pad)
    x1 = min(w, max(b[2] for b in boxes) + pad)
    y1 = min(h, max(b[3] for b in boxes) + pad)
    x0, y0 = x0 - x0 % 2, y0 - y0 % 2
    bw, bh = x1 - x0, y1 - y0
    bw, bh = bw - bw % 2, bh - bh % 2
    return x0, y0, bw, bh


def layout(n_cls: int, base_w: int, base_h: int, bw: int, bh: int, max_side: int = 0) -> dict:
    """Cells in a block to the right of the base; the rows and columns that make the smallest video inside a phone's
    decoder (max_side, MAX_W by default), the base height first. Even sizes."""
    max_side = max_side or MAX_W
    best = None
    for rows in range(1, n_cls + 1):
        cols = int(math.ceil(n_cls / float(rows)))
        W, H = base_w + cols * bw, max(base_h, rows * bh)
        W, H = W + W % 2, H + H % 2
        if W > max_side or H > max_side:
            continue
        key = (H > base_h, W * H, max(W, H))
        if best is None or key < best[0]:
            best = (key, rows, cols, W, H)
    if best is None:
        rows = max(1, min(n_cls, base_h // bh))
        cols = int(math.ceil(n_cls / float(rows)))
        best = (None, rows, cols, base_w + cols * bw, max(base_h, rows * bh))
    _, rows, cols, W, H = best
    cells = [(base_w + (i // rows) * bw, (i % rows) * bh) for i in range(n_cls)]
    return {"w": W + W % 2, "h": H + H % 2, "cols": cols, "rows": rows, "cells": cells}


def manifest(loop: str, classes: list[str], n: int, fps: float, base_wh: tuple[int, int], box: tuple, lay: dict,
             video: str, extra: dict | None = None) -> dict:
    x, y, bw, bh = box
    return dict({
        "schema": SCHEMA, "loop": loop, "fps": fps, "n": n, "video": video,
        "video_wh": [lay["w"], lay["h"]], "base": [0, 0, base_wh[0], base_wh[1]], "box": [x, y, bw, bh],
        "cells": {c: list(lay["cells"][i]) for i, c in enumerate(classes)}, "classes": classes,
        "idle": "rest", "feather": FEATHER,
    }, **(extra or {}))


def check(m: dict) -> list[str]:
    """What is wrong with a manifest, [] when it is a pack the app can play."""
    bad = []
    if m.get("schema") != SCHEMA:
        bad.append("schema is %r" % m.get("schema"))
    W, H = (m.get("video_wh") or [0, 0])[:2]
    x, y, bw, bh = (m.get("box") or [0, 0, 0, 0])[:4]
    bx, by, BW, BH = (m.get("base") or [0, 0, 0, 0])[:4]
    if not (0 <= x and 0 <= y and x + bw <= BW and y + bh <= BH):
        bad.append("box leaves the base")
    for c, (cx, cy) in (m.get("cells") or {}).items():
        if cx < BW or cx + bw > W or cy + bh > H:
            bad.append("cell %s leaves the video or overlaps the base" % c)
    if m.get("cells") and m.get("idle") not in m["cells"]:
        bad.append("no cell for the idle class %r" % m.get("idle"))
    return bad


SHOW_SCHEMA = "phone-show/1"


def show(rows: dict, packs: set[str], talk: str) -> dict:
    """The phone's show (phone-show/1): which clip she talks on, and every clip with a pack by its hubs, so the app
    walks them the way the Wall does - loops dwell on their hub, a slide carries her to the other hub, a flourish
    plays once from its loop and lands back on it."""
    clips = {}
    for k, r in rows.items():
        if k not in packs or str(r.get("rack") or "") == "harvest":
            continue
        role = str(r.get("role") or "")
        if role not in ("loop", "slide", "flourish"):
            continue
        clips[k] = {"role": role, "start": r.get("start"), "end": r.get("end"), "pack": k}
    if talk not in clips:
        raise ValueError("the talk loop %s has no pack" % talk)
    return {"schema": SHOW_SCHEMA, "talk": talk, "talk_hub": clips[talk]["start"], "clips": clips}


def check_show(sh: dict) -> list[str]:
    bad = []
    clips = sh.get("clips") or {}
    if sh.get("talk") not in clips:
        bad.append("no talk loop")
    hubs = {c["start"] for c in clips.values() if c["role"] == "loop"}
    for k, c in clips.items():
        if c["role"] == "slide" and not (c["start"] in hubs and c["end"] in hubs):
            bad.append("slide %s leaves the hubs that have a loop" % k)
        if c["role"] == "flourish" and c["start"] not in hubs:
            bad.append("flourish %s has no loop on its hub" % k)
    for h in hubs - {sh.get("talk_hub")}:
        if not any(c["role"] == "slide" and c["start"] == h and c["end"] == sh.get("talk_hub") for c in clips.values()):
            bad.append("no way back to talk from hub %s" % h)
    return bad


def write_json(p: Path, d: dict) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)
