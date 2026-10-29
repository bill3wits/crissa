# Crissa render module (optional)

Local picture and clip generation on your own ComfyUI, the way the reference install does it. The light Wall
install skips it; nothing here is needed to watch her, talk to her, or connect an agent. With no NVIDIA GPU the
module says so (the mark `○ no GPU`) and every Render key on the Control Panel stays greyed with that note.

- `scripts/crissa-render.py`: the GPU mode switch (`render` flags the card and starts Comfy, `normal` / `off`
  stop it), one still from a recipe and a prompt, the written list of render orders, and what Comfy draws
  right now. `--install` makes its small env (`~/.crissa/render/env`, one hash-pinned package,
  websocket-client, for the work-in-progress watcher). `--configure --comfy-dir <folder>` writes
  `~/.crissa/render.json`; ComfyUI itself is yours, installed your way, never from here.
- `crissa_render/settings.py`: `render.json` (Comfy folder, interpreter, URL, workflows / models / input /
  output, the recipes file, the orders folder) and the mark.
- `crissa_render/comfy.py`: cut from the reference install's mode switch: the flag file, free VRAM, Comfy up
  and busy (with the unknown clock), start and stop. Its other jobs, parking and waking the reference
  install's own LLM engines and agent gateways, the card holds and the window gate, have no process to act on
  in a pack and are not here.
- `crissa_render/still.py`: cut from the reference install's render-still: the recipe, the patched graph, the
  source still for an edit, the submit and the wait. No board card, no gate: a render order is the record.
- `crissa_render/orders.py`, `crissa_render/wip.py`: the reference install's `lib/render_orders.py` and
  `lib/render_wip.py`, copied beside the package by the export.

The feed (`scripts/voiceonly-home-server.py`) shows the stage (work in progress, the newest output) and the
Render keys from these files; the Control Panel's Mode row calls this script when the reference install's own
switch is not there.

Recipes have the reference install's shape, in `~/.crissa/render/recipes.json`:

    {"recipes": [{"id": "flux2-turbo", "kind": "t2i", "workflow": "flux2-turbo-txt2img.json",
                  "prompt_node": "6", "size_nodes": ["5"]}]}

A workflow is a saved ComfyUI API graph in the `workflows` folder named in `render.json`. No model, no
workflow and no recipe ships with the module.
