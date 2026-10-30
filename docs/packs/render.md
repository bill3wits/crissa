# Crissa render module pack

Created with Grok.

## Licences and credits

- `LICENSE`: the MIT licence for the source code in this pack.
- `CREDITS.md`: who and what made her, starting with Grok Imagine.
- `THIRD-PARTY-NOTICES.md`: third-party code and the tools used to build the data.

It carries the optional render module, `modules/render`: local generation on your own ComfyUI. The GPU mode
switch (`render` flags the card and starts Comfy, `normal` / `off` stop it), one still from a recipe and a
prompt, a written list of render orders, and what Comfy draws right now for the feed's stage.
`modules/render/README.md` says how to run it; `--configure --comfy-dir <folder>` names your Comfy, `--install`
makes its one-package env. With no NVIDIA GPU the module says so and every Render key stays greyed. It carries
no model, no workflow and no recipe.

Built from ref `3f998077950f0a35d3c38afd5a833d378b43fc3c` by `scripts/crissa-export.py`.
