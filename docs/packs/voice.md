# Crissa voice module pack

Created with Grok.

## Licences and credits

- `LICENSE`: the MIT licence for the source code in this pack.
- `CREDITS.md`: who and what made her, starting with Grok Imagine.
- `THIRD-PARTY-NOTICES.md`: third-party code and the tools used to build the data.

It carries the optional voice module, `modules/voice`: a listening loop and turns server
(`scripts/crissa-stt.py`), a speech-to-text service on the CPU (`scripts/crissa-asr.py`, ARK-ASR,
Apache-2.0), the CPU announcer (`scripts/crissa-audio8.py`, Audio8), two voices on the CPU (`scripts/crissa-pocket.py`, Kyutai Pocket TTS with its stock voices only,
code MIT and weights CC BY 4.0; `scripts/crissa-tts.py`, Kokoro-82M, Apache-2.0) and, for a computer with an
NVIDIA GPU, Chatterbox-Turbo (`scripts/crissa-turbo.py`, Resemble AI, MIT, Perth watermark on), each installed on first use
from hash-pinned locks, its model fetched on your machine and checked by sha256. `modules/voice/README.md` says how to run them. It carries no voice, no model
and no audio.

Built from ref `3827c939b0da73183097dc8bc36b675a5f452887` by `scripts/crissa-export.py`.
