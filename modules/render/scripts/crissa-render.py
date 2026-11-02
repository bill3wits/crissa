#!/usr/bin/env python3
"""The render module (modules/render, 2026-10-09): local generation on the user's own ComfyUI.

    python3 modules/render/scripts/crissa-render.py --install                 # once: the module's small env
    python3 modules/render/scripts/crissa-render.py --configure --comfy-dir ~/ComfyUI [--comfy-python ...]
    python3 modules/render/scripts/crissa-render.py status                    # mode, Comfy, free VRAM, the mark
    python3 modules/render/scripts/crissa-render.py render [--highvram]       # flag render, start Comfy
    python3 modules/render/scripts/crissa-render.py normal | off              # stop Comfy, flag normal | off
    python3 modules/render/scripts/crissa-render.py still --model flux2-turbo --prompt "..." [--aspect|--size] [--out]
    python3 modules/render/scripts/crissa-render.py orders add <workflow> [--set k=v ...] [--seed N] [--by who]
    python3 modules/render/scripts/crissa-render.py orders list|show <id>|cancel <id>|plan
    python3 modules/render/scripts/crissa-render.py orders run                # take the pending orders in turn
    python3 modules/render/scripts/crissa-render.py wip                       # what Comfy draws right now
    python3 modules/render/scripts/crissa-render.py mark                      # ok / fallback / off + a few words

The house's mode switch (scripts/house-mode) has three more jobs this module does not: parking and waking its two
LLM engines and agent gateways, the card holds, and the window gate on a worker's render. A pack has none of those
processes; its three modes flag the card and start or stop Comfy (crissa_render.comfy). The house itself keeps
running house-mode; the feed prefers it when it is there (scripts/voiceonly-home-server.py, HOUSE_MODE_BIN).

The orders list and the work-in-progress watcher are the house's lib/render_orders.py and lib/render_wip.py, copied
beside the package in a pack (the export's `vendor` map); in the source tree they are read from lib/.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from crissa_render import comfy, settings, still  # noqa: E402


def _orders():
    try:
        from crissa_render import orders as ro          # the copy the pack puts beside the package
    except ImportError:
        sys.path.insert(0, str(HERE.parent.parent / "lib"))
        import render_orders as ro                      # noqa: E402  the source tree's
    return ro


def _wip():
    try:
        from crissa_render import wip                   # the copy the pack puts beside the package
    except ImportError:
        sys.path.insert(0, str(HERE.parent.parent / "lib"))
        import render_wip as wip                        # noqa: E402  the source tree's
    return wip


def _kv(items: list[str]) -> dict:
    out: dict = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--set wants key=value, got {it!r}")
        k, v = it.split("=", 1)
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


def cmd_orders(a, cfg: dict) -> int:
    ro = _orders()
    root = Path(cfg["orders"])
    if a.verb == "add":
        o = ro.add(a.workflow, overrides=_kv(a.set), seed=a.seed, by=a.by or "crissa-render", root=root,
                   workflows_dir=Path(cfg["workflows"]), index=ro.model_index(Path(cfg["models"])))
        print(json.dumps(o, indent=1))
        return 0
    if a.verb == "list":
        for o in ro.orders(a.state, root=root):
            print(f"{o.get('state', '?'):8} {o['id']}  {o.get('family', '?'):12} {o.get('workflow', '')}")
        return 0
    if a.verb == "show":
        print(json.dumps(ro.show(a.id, root=root), indent=1))
        return 0
    if a.verb == "cancel":
        print(json.dumps(ro.cancel(a.id, by=a.by or "crissa-render", root=root), indent=1))
        return 0
    if a.verb == "plan":
        print(json.dumps(ro.plan(root=root), indent=1))
        return 0
    if a.verb == "run":
        if a.watch:
            return watch_orders(cfg, ro, root, every=a.watch)
        return run_orders(cfg, ro, root)
    raise SystemExit(f"unknown orders verb {a.verb!r}")


def run_orders(cfg: dict, ro, root: Path, log=print) -> int:
    """Take every pending order in turn: enter Render once, submit each graph, record what landed, leave."""
    pending = ro.orders("pending", root=root)
    if not pending:
        log("orders: nothing pending")
        return 0
    rc = comfy.enter_render(cfg, log=log)
    if rc:
        return rc
    failed = 0
    for o in pending:
        oid = o["id"]
        ro.move(oid, "running", root=root, pid=os.getpid())
        try:
            graph = o.get("graph") or ro.apply_overrides(
                ro.load_workflow(o["workflow"], workflows_dir=Path(cfg["workflows"]))[1],
                o.get("overrides") or {}, seed=o.get("seed"))
            path = still.submit(graph, cfg)
            ro.move(oid, "done", root=root, outputs=[path])
            log(f"orders: {oid} -> {path}")
        except Exception as e:  # noqa: BLE001  one bad order never stops the list
            failed += 1
            ro.move(oid, "failed", root=root, error=f"{type(e).__name__}: {e}"[:400])
            log(f"orders: {oid} FAILED {type(e).__name__}: {e}")
    comfy.leave_render(cfg, log=log)
    return 4 if failed else 0


def watch_orders(cfg: dict, ro, root: Path, every: float = 30.0, log=print, sleep=None, once: bool = False) -> int:
    """The launcher's render child (scripts/crissa.py --with-render): take the pending orders, then look again every
    `every` seconds until stopped. A quiet folder costs one directory read a tick; a failed order is logged on the
    order and never stops the watch."""
    import time
    sleep = time.sleep if sleep is None else sleep
    log(f"orders: watching {root} every {every:g} s")
    while True:
        try:
            if ro.orders("pending", root=root):
                run_orders(cfg, ro, root, log=log)
        except Exception as e:  # noqa: BLE001  the watch outlives one bad tick
            log(f"orders: watch tick failed {type(e).__name__}: {e}")
        if once:
            return 0
        sleep(max(1.0, float(every)))


def cmd_loop(a, cfg: dict) -> int:
    """A hub loop from one anchor still (crissa_render.loop): the spec's words and knobs, one order."""
    from crissa_render import loop
    if not loop.enabled():
        print("loop: off (CRISSA_RENDER_LOOPS=1 turns it on)", file=sys.stderr)
        return 3
    R = loop._specs()
    try:
        if a.words:
            spec = R.load(a.type, loop.spec_dir())
            print(R.render(spec, frame=a.frame, subject=a.subject, look=a.look, pronoun=a.pronoun))
            return 0
        o = loop.order(a.type, frame=a.frame, recipe=a.recipe, anchor=a.anchor, by=a.by, subject=a.subject, look=a.look,
                       pronoun=a.pronoun, seed_index=a.seed_index, seconds=a.seconds, cfg=cfg, ro=_orders(),
                       root=Path(cfg["orders"]))
    except (R.SpecError, FileNotFoundError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001  an order refusal (no workflow, a model file missing) in plain words
        print(f"refused: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(f"{o['id']}  pending  {o['workflow']}  family={o['family']}  need={o['need_gib']} GiB  "
          f"{a.type} {a.frame} {a.recipe}  anchor {o['loop']['staged']}")
    return 0


def cmd_still(a, cfg: dict, log=print) -> int:
    try:
        rec = still.load_recipe(a.model, a.source, cfg)
        w, h = still.size_of(a.aspect, a.size)
        prompt = a.prompt or (Path(a.prompt_file).expanduser().read_text().strip() if a.prompt_file else "")
        if not prompt:
            raise still.StillError("need --prompt or --prompt-file")
        src = still.stage_source(a.source, cfg) if rec.get("kind") == "i2i" else None
        graph = still.patch_graph(rec, prompt, w, h, src, cfg)
    except still.StillError as e:
        print(str(e), file=sys.stderr)
        return 2
    if a.dry_run:
        print(json.dumps({"dry_run": True, "model": rec["id"], "kind": rec.get("kind"), "size": f"{w}x{h}",
                          "prompt": prompt, "graph_nodes": len(graph), "workflow": rec["workflow"]}, indent=1))
        return 0
    before = comfy.read_mode(cfg=cfg)
    if before != "render":
        rc = comfy.enter_render(cfg, log=log)
        if rc:
            return 3
    try:
        path = still.submit(graph, cfg)
    except Exception as e:  # noqa: BLE001
        print(f"draw failed: {type(e).__name__}: {e}", file=sys.stderr)
        if before != "render":
            comfy.leave_render(cfg, log=log)
        return 4
    dst = Path(a.out).expanduser() if a.out else Path(cfg["state"]) / "stills" / Path(path).name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if Path(path) != dst:
        os.replace(path, dst)
    if before != "render":
        comfy.leave_render(cfg, log=log)
    print(str(dst))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="crissa-render", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="build the module's env from its lock, then exit")
    ap.add_argument("--configure", action="store_true", help="write render.json from the --comfy-* options, then exit")
    ap.add_argument("--comfy-dir")
    ap.add_argument("--comfy-python")
    ap.add_argument("--comfy-url")
    ap.add_argument("--json", action="store_true", help="status / mark as JSON")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("mark")
    r = sub.add_parser("render")
    r.add_argument("recipe", nargs="?", default="",
                   help="accepted for the feed's Mode row, which passes the house's recipe name; this module sizes nothing by it")
    r.add_argument("--highvram", action="store_true")
    sub.add_parser("normal")
    sub.add_parser("off")
    sub.add_parser("beast", help="the house's two-brain mode: not in this edition (exit 2, says so)")
    sub.add_parser("wip")
    st = sub.add_parser("still")
    st.add_argument("--model", default="flux2-turbo")
    st.add_argument("--prompt")
    st.add_argument("--prompt-file")
    st.add_argument("--aspect", default="landscape")
    st.add_argument("--size")
    st.add_argument("--source")
    st.add_argument("--out")
    st.add_argument("--dry-run", action="store_true")
    lp = sub.add_parser("loop", help="one hub loop from one anchor still, as a render order (CRISSA_RENDER_LOOPS=1)")
    lp.add_argument("--type", required=True, help="listen-loop | work-loop | work-loop-up | work-loop-multitask")
    lp.add_argument("--frame", choices=("desk", "portrait"), required=True)
    lp.add_argument("--recipe", default="ltx25-flf2v")
    lp.add_argument("--anchor", required=True, help="the one still the loop starts and ends on")
    lp.add_argument("--by", default="user")
    lp.add_argument("--subject", default="person")
    lp.add_argument("--look")
    lp.add_argument("--pronoun", default="she", choices=("she", "he", "they"))
    lp.add_argument("--seed-index", type=int, default=0)
    lp.add_argument("--seconds", type=float)
    lp.add_argument("--words", action="store_true", help="print the words the recipe is sent, order nothing")
    od = sub.add_parser("orders")
    od.add_argument("verb", choices=("add", "list", "show", "cancel", "plan", "run"))
    od.add_argument("workflow", nargs="?")
    od.add_argument("--id")
    od.add_argument("--set", action="append", default=[])
    od.add_argument("--seed", type=int)
    od.add_argument("--by")
    od.add_argument("--state", default=None)
    od.add_argument("--watch", type=float, default=0.0, metavar="S",
                    help="orders run: keep running, taking new pending orders every S seconds (the launcher's child)")
    a = ap.parse_args(argv)

    if a.configure:
        p = settings.write({"comfy_dir": a.comfy_dir, "comfy_python": a.comfy_python, "comfy_url": a.comfy_url})
        print(json.dumps(settings.load(p), indent=1))
        return 0
    cfg = settings.load()
    if a.install:
        from crissa_render import install
        py = install.install(cfg)
        print(f"render: env ready at {py}")
        return 0
    if a.cmd in (None, "status"):
        s = comfy.status(cfg)
        if a.json or a.cmd is None:
            print(json.dumps(s, indent=1))
        else:
            print(f"mode {s['mode']}  comfy {'up' if s['comfy'] else 'down'}"
                  f"{' busy' if s['comfy_busy'] else ''}  free VRAM {s['free_vram_gib']}  {s['mark']['mark']} {s['mark']['note']}")
        return 0
    if a.cmd == "mark":
        print(json.dumps(settings.mark(cfg)))
        return 0
    if a.cmd == "render":
        return comfy.enter_render(cfg, highvram=a.highvram or None)
    if a.cmd == "beast":
        print("no Beast in this edition: it is the house's two-brain mode (one LLM on the whole card); "
              "this module knows render, normal and off", file=sys.stderr)
        return 2
    if a.cmd in ("normal", "off"):
        return comfy.leave_render(cfg, to=a.cmd)
    if a.cmd == "wip":
        print(json.dumps(_wip().wip_payload(), indent=1))
        return 0
    if a.cmd == "still":
        return cmd_still(a, cfg)
    if a.cmd == "loop":
        return cmd_loop(a, cfg)
    if a.cmd == "orders":
        if a.verb in ("show", "cancel") and not a.id:
            a.id = a.workflow
        if a.verb == "add" and not a.workflow:
            raise SystemExit("orders add wants a workflow name or path")
        return cmd_orders(a, cfg)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
