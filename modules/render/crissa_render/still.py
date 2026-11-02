"""One still from a recipe and a prompt, on the user's Comfy (2026-10-09).

Cut from the house's scripts/render-still.py (load_recipe, patch_graph, stage_source, submit, the aspect table) with
the house's own two steps left behind: the board card (the window gate, the card's comment and block) and the house
mode switch that parks its brains. Here the caller enters Render through crissa_render.comfy, and a render order
(crissa_render.orders) is the written record instead of a card.

The recipes file (<state>/render/recipes.json by default, `recipes` in render.json) has the house's shape:

    {"recipes": [{"id": "flux2-turbo", "kind": "t2i", "workflow": "flux2-turbo-txt2img.json",
                  "prompt_node": "6", "size_nodes": ["5"]},
                 {"id": "qwen-edit", "kind": "i2i", "workflow": "...", "image_node": "10"}]}

A workflow is a saved ComfyUI API graph in the `workflows` folder named in render.json.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.request
from pathlib import Path

from . import settings

ASPECT = {
    "landscape": (1024, 768),
    "portrait": (768, 1024),
    "square": (1024, 1024),
}
TIMEOUT_S = float(os.environ.get("HOUSE_COMFY_TIMEOUT", "900"))
CLIENT_ID = os.environ.get("HOUSE_COMFY_CLIENT_ID", "house-wip")   # the stage watches this id (lib/render_wip)
PREVIEW_METHOD = os.environ.get("HOUSE_COMFY_PREVIEW", "auto")


class StillError(RuntimeError):
    """A refusal before anything is drawn, in plain words."""


def recipes(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or settings.load()
    p = Path(cfg["recipes"])
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError:
        return []
    except ValueError as e:
        raise StillError(f"recipes file unreadable: {p}: {e}")
    return [r for r in (data.get("recipes") or []) if isinstance(r, dict) and r.get("id")]


def load_recipe(model: str, source: str | None = None, cfg: dict | None = None) -> dict:
    """A t2i id, or an i2i id when `source` is given."""
    rows = recipes(cfg)
    if not rows:
        raise StillError("no recipes: write %s (see modules/render/README.md)" % (cfg or settings.load())["recipes"])
    kinds = {"t2i", "i2i"} if source else {"t2i"}
    for rec in rows:
        if rec.get("id") == model and rec.get("kind") in kinds:
            if source and rec.get("kind") != "i2i":
                raise StillError("%s is t2i; --source needs an i2i id" % model)
            return rec
    for rec in rows:
        if rec.get("id") == model and rec.get("kind") == "i2i":
            raise StillError("%s is i2i; pass --source <still.png>" % model)
    raise StillError("unknown or non-t2i recipe: %s (ids: %s)" % (model, ", ".join(r["id"] for r in rows)))


def patch_graph(rec: dict, prompt: str, w: int, h: int, source_basename: str | None = None,
                cfg: dict | None = None) -> dict:
    cfg = cfg or settings.load()
    wf_path = Path(cfg["workflows"]) / rec["workflow"]
    if not wf_path.is_file():
        raise StillError("workflow missing: %s" % wf_path)
    graph = json.loads(wf_path.read_text())

    def apply(node: dict) -> None:
        inp = node.setdefault("inputs", {})
        ct = str(node.get("class_type") or "")
        if ct.startswith("TextEncodeQwen") or "prompt" in inp:
            inp["prompt"] = prompt
        else:
            inp["text"] = prompt

    node_id = rec.get("prompt_node")
    if node_id and node_id in graph:
        apply(graph[node_id])
    else:
        for node in graph.values():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type") or ""
            if ct == "CLIPTextEncode" or ct.startswith("TextEncodeQwen"):
                apply(node)
                break
    for nid in rec.get("size_nodes") or ():
        node = graph.get(nid)
        if not isinstance(node, dict):
            continue
        inp = node.setdefault("inputs", {})
        if "width" in inp:
            inp["width"] = w
        if "height" in inp:
            inp["height"] = h
    if rec.get("kind") == "i2i":
        if not source_basename:
            raise StillError("--model %s is i2i; pass --source <still.png>" % rec.get("id"))
        inode = rec.get("image_node")
        if not inode or str(inode) not in graph:
            raise StillError("i2i recipe %s names no image_node (or it is missing from the workflow)" % rec.get("id"))
        graph[str(inode)].setdefault("inputs", {})["image"] = source_basename
    return graph


def stage_source(source: str, cfg: dict | None = None) -> str:
    """Copy or download the source still into Comfy's input folder; returns its name there."""
    cfg = cfg or settings.load()
    in_dir = Path(cfg["input"])
    in_dir.mkdir(parents=True, exist_ok=True)
    src = Path(source).expanduser()
    if src.is_file():
        dest = in_dir / ("crissa_%d_%s" % (int(time.time()), src.name or "in.png"))
        shutil.copyfile(src, dest)
        return dest.name
    if source.startswith(("http://", "https://")):
        dest = in_dir / ("crissa_%d_src" % int(time.time()))
        with urllib.request.urlopen(source, timeout=30) as r:
            dest.write_bytes(r.read())
        return dest.name
    raise StillError("source still not found: %s" % source)


def submit(graph: dict, cfg: dict | None = None, *, timeout_s: float = TIMEOUT_S, sleep=time.sleep) -> str:
    """POST the graph as the stage's client id; wait for the first output file; return its path."""
    cfg = cfg or settings.load()
    comfy = cfg["comfy_url"]
    body = json.dumps({"prompt": graph, "client_id": CLIENT_ID,
                       "extra_data": {"preview_method": PREVIEW_METHOD}}).encode()
    req = urllib.request.Request(comfy + "/prompt", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        pid = json.loads(r.read().decode()).get("prompt_id")
    if not pid:
        raise RuntimeError("Comfy returned no prompt_id")
    out_dir = Path(cfg["output"])
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        sleep(2)
        with urllib.request.urlopen(f"{comfy}/history/{pid}", timeout=10) as r:
            hist = json.loads(r.read().decode())
        rec = hist.get(pid)
        if not rec:
            continue
        for node in (rec.get("outputs") or {}).values():
            for item in node.get("images") or []:
                name = item.get("filename")
                sub = item.get("subfolder") or ""
                path = out_dir / sub / name if name else None
                if path and path.is_file():
                    return str(path)
        if rec.get("status", {}).get("status_str") == "error":
            raise RuntimeError(f"Comfy error: {rec.get('status')}")
    raise TimeoutError(f"Comfy job {pid} timed out after {timeout_s:.0f}s")


def size_of(aspect: str, size: str | None) -> tuple[int, int]:
    if size:
        w, h = (int(v) for v in size.lower().split("x"))
        return w, h
    if aspect not in ASPECT:
        raise StillError("aspect must be one of %s" % ", ".join(sorted(ASPECT)))
    return ASPECT[aspect]
