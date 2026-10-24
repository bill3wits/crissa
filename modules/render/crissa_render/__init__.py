"""The render module (modules/render, 2026-10-09): local picture and clip generation on the user's own
ComfyUI, as the house does it, with no agent framework in the loop.

    settings   <state>/render.json: where Comfy is (dir, python, url), its workflows / models / input / output,
               the recipes file and the orders folder. Nothing here names any one machine's paths.
    comfy      the GPU mode flag (render | normal | off), free VRAM, Comfy up / busy, start / stop (cut from the
               house's mode switch: the parts that are about Comfy and the card, not about parking the house's brains)
    still      one still from a recipe + prompt (cut from the house's render-still: no card, no window gate)
    orders     a written list of renders that empties as it is processed (the house's lib/render_orders.py,
               copied beside this file by the export)
    wip        what Comfy draws right now, for the feed's stage (the house's lib/render_wip.py, copied the same way)

Every optional piece says how it stands as a mark (ok / fallback / off), never an error: no GPU, no Comfy
configured, Comfy not running.
"""
