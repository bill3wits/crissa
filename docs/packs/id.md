# Crissa id module pack

Created with Grok.

## Licences and credits

- `LICENSE`: the MIT licence for the source code in this pack.
- `CREDITS.md`: who and what made her, starting with Grok Imagine.
- `THIRD-PARTY-NOTICES.md`: third-party code and the tools used to build the data.

It carries the optional id module, `modules/id`: Init, the first-boot routine the Wall walks (the house's 13 steps,
`config/init-sequence.json`, served by the player on `/v1/init` with `CRISSA_INIT=1`; Init v2 behind
`HOUSE_INIT_V2`), and enrolment: the `house-enroll` CLI, the Register routine, face capture and the voice session,
the coach page (`modules/id/README.md`). Faces are MediaPipe Face Landmarker + AuraFace (both Apache-2.0, the
oracle module's files), voices WeSpeaker on the CPU; the install builds its own env from a hash lock. The identity
store is minted empty under `~/.crissa/identity`. It carries no model, no picture, no record and nobody's face or voice.

Built from ref `6c4d50c21770a1ed15980db4bdef4041d471bafe` by `scripts/crissa-export.py`.
