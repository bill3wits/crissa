# Crissa ear module pack

Created with Grok.

## Licences and credits

- `LICENSE`: the MIT licence for the source code in this pack.
- `CREDITS.md`: who and what made her, starting with Grok Imagine.
- `THIRD-PARTY-NOTICES.md`: third-party code and the tools used to build the data.

It carries the optional ear module, `modules/ear`: the house's ear, whole (`modules/ear/README.md`). Who is
speaking, what they said, whether it was for her, and her reply: `scripts/crissa-stt.py` (the microphone's turns
with a speaker stamp: VAD, the onset hold, voice identity against the voice bank, the owner's floor, the media gate,
`/v1/turns`, `/stt`, `/v1/mic`, `/health`) and `scripts/crissa-bridge.py` (a turn becomes a question to the
connected agent: the wake grammar, the hear gate, voice-ID authorize, a guest's plain reply, her own voice coming
back, the thinking file the Wall's pulse reads, `/health`). The install builds its own env from a hash lock; the
speech-to-text model is the voice module's (`crissa-asr.py`). It carries no voice bank, no recording and nobody's voice.

Built from ref `705be4291d52caaa16e7846c4679e38936fe21ad` by `scripts/crissa-export.py`.
