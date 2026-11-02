# Crissa presence module pack

Created with Grok.

## Licences and credits

- `LICENSE`: the MIT licence for the source code in this pack.
- `CREDITS.md`: who and what made her, starting with Grok Imagine.
- `THIRD-PARTY-NOTICES.md`: third-party code and the tools used to build the data.

It carries the optional presence module, `modules/presence`: the house's presence rail, extracted whole
(`modules/presence/README.md`): who is in the room (`scripts/crissa-presence.py`: /v1/presence, the visit lamp,
Secure and Guest, the door watch, visitors, Register's camera token, the room sweep), the room camera's one owner
(`scripts/crissa-camera.py`: snapshot, mjpeg, ring buffer, PTZ) and the registration panel (`scripts/crissa-enroll.py`).
Faces are MediaPipe Face Landmarker + AuraFace (both Apache-2.0), fetched on first use; the install builds its own
env from a hash lock. It carries no model, no picture, no record and nobody's face.

Built from ref `716beb4d8a995f084b663af56d27ea08cb17c96e` by `scripts/crissa-export.py`.
