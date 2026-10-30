# Crissa oracle module pack

Created with Grok.

## Licences and credits

- `LICENSE`: the MIT licence for the source code in this pack.
- `CREDITS.md`: who and what made her, starting with Grok Imagine.
- `THIRD-PARTY-NOTICES.md`: third-party code and the tools used to build the data.

It carries the optional oracle module, `modules/oracle`: the clock's face check (FF) and the door feed
(`scripts/crissa-oracle.py`), on MediaPipe Face Landmarker and, optionally, the AuraFace recognition
model (both Apache-2.0, fetched on first use). `modules/oracle/README.md` says how to run it. It carries
no model, no picture and nobody's face. A skeleton: the identity check is not yet proved.

Built from ref `3f998077950f0a35d3c38afd5a833d378b43fc3c` by `scripts/crissa-export.py`.
