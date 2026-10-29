"""A hub loop from one anchor still, as a render order (2026-10-20).

The reference install's recipe for "a loop from one anchor": the written spec (the words, the sizes, the recipe
knobs, the QC bar) is a JSON file the render module ships (`specs/<type>.json`, the reference install's
config/render-loop-specs); this module stages the anchor into Comfy's input folder, cut to the output's exact
aspect, and writes ONE render order with the spec's overrides on the recipe's workflow. `orders run` renders it
like any other order; nothing here enters Render.

OFF by default: CRISSA_RENDER_LOOPS=1 turns the verb on (the reference install's switch is
HOUSE_RENDER_LOOP_SPECS=1 for the same loader).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from . import settings

HERE = Path(__file__).resolve().parent


def _specs():
    try:
        from . import loop_specs as R                   # the copy the pack puts beside the package
    except ImportError:
        import sys
        sys.path.insert(0, str(HERE.parent.parent.parent / "lib"))
        import render_loop_specs as R                   # noqa: E402  the source tree's
    return R


def spec_dir() -> Path:
    d = HERE.parent / "specs"
    if d.is_dir():
        return d                                         # a pack
    return HERE.parent.parent.parent / "config" / "render-loop-specs"   # the source tree


def enabled(env: dict | None = None) -> bool:
    e = env if env is not None else os.environ
    return e.get("CRISSA_RENDER_LOOPS", "0") == "1" or e.get("HOUSE_RENDER_LOOP_SPECS", "0") == "1"


def cut_anchor(src: Path, size: str, out: Path) -> tuple[int, int]:
    """The anchor at the output's exact aspect (the guide node scales without cropping)."""
    from PIL import Image
    w, h = (int(v) for v in size.lower().split("x"))
    im = Image.open(src).convert("RGB")
    W, H = im.size
    if W / H > w / h:
        tw = round(H * w / h); x0 = (W - tw) // 2; box = (x0, 0, x0 + tw, H)
    else:
        th = round(W * h / w); y0 = (H - th) // 2; box = (0, y0, W, y0 + th)
    im.crop(box).resize((w, h), Image.LANCZOS).save(out)
    return w, h


def stage_anchor(anchor: str, size: str, cfg: dict | None = None, now: float | None = None) -> str:
    """The anchor cut to `size`, in Comfy's input folder; returns its name there."""
    cfg = cfg or settings.load()
    in_dir = Path(cfg["input"])
    in_dir.mkdir(parents=True, exist_ok=True)
    src = Path(anchor).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"anchor missing: {src}")
    dest = in_dir / ("crissa_loop_%d_%s_%s.png" % (int(now or time.time()), size, src.stem))
    cut_anchor(src, size, dest)
    return dest.name


def order(type_: str, *, frame: str, recipe: str, anchor: str, by: str, subject: str = "person", look: str | None = None,
          pronoun: str = "she", seed_index: int = 0, seconds: float | None = None, cfg: dict | None = None,
          ro=None, root: Path | None = None, workflows_dir: Path | None = None) -> dict:
    """Write one pending order for the loop. `ro` is the orders module (lib/render_orders or the pack's copy)."""
    R = _specs()
    if not enabled():
        raise R.SpecError("loops are off (CRISSA_RENDER_LOOPS=1 turns them on)")
    cfg = cfg or settings.load()
    spec = R.load(type_, spec_dir())
    size = spec["frames"][frame]["size_by_recipe"][recipe]
    staged = stage_anchor(anchor, size, cfg)
    ov = R.order_overrides(spec, frame=frame, recipe=recipe, anchor_basename=staged, subject=subject, look=look,
                           pronoun=pronoun, seed_index=seed_index, seconds=seconds)
    wf = spec["recipes"][recipe]["workflow"]
    o = ro.add(wf, overrides=ov, by=by, root=root, workflows_dir=workflows_dir or Path(cfg["workflows"]))
    o["loop"] = {"type": type_, "frame": frame, "recipe": recipe, "anchor": str(Path(anchor).expanduser()),
                 "staged": staged, "qc": R.qc_bar(spec), "anchor_tags": spec["anchor_tags"]}
    return o
