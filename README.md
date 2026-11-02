# Crissa

Crissa is a character who lives on your desktop. She listens, answers in her own voice with her lips in time, and
watches the room through your camera if you let her. The thinking is done by the agent you connect.

Crissa works with any agent that speaks the OpenAI chat API, local or cloud. krew is a Hermes Agent plugin: it runs on its own as an operational crew, or plugs into Crissa.

Created with Grok. Made by Rocks Cry Out Labs, LLC.

## Install (Linux, x86-64)

Download `crissa-1.0.7-linux-x86_64.tar.gz` and its `.sha256` from this repository's Releases page. The download
carries the code and her library (films, mouth tiles, Crissa's and Lila's voice clips). The installer fetches
[uv](https://docs.astral.sh/uv/) into `~/.local/bin` when it is not there yet.

    sha256sum -c crissa-1.0.7-linux-x86_64.tar.gz.sha256
    tar -xzf crissa-1.0.7-linux-x86_64.tar.gz
    cd crissa-1.0.7
    ./install.sh
    .venv/bin/python scripts/crissa.py

The first start walks Setup with Lila: connect your agent, your name and microphone, a check of what this computer can
run, Voice ID (four short counts; her face photos are taken during them), your room and your hours. Cancel at any step
keeps nothing, and Setup runs again at the next start. After that, `.venv/bin/python scripts/crissa.py` starts her.
Your settings, voice prints and face data stay on this computer, in `~/.crissa`.

Her voice is picked for your computer: Chatterbox-Turbo on an NVIDIA GPU, else Audio8 or Pocket TTS on the CPU, and
so on down to text on screen. The Wall, the voices, the speech-to-text and the face check are described in
`docs/packs/`: start with `docs/packs/wall.md`.

## From a clone

The repository holds the code only. Her library is a release download:

    git clone <this repository> crissa && cd crissa
    scripts/get-library.sh 1.0.7
    ./install.sh
    .venv/bin/python scripts/crissa.py

The first start walks Setup, as above.

## Tests

    uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt pytest   # uv: ~/.local/bin
    .venv/bin/python scripts/run-tests.py

Each test file runs in its own process (some modules load under two names, and one process for everything lets one
file's import shadow another's); `.venv/bin/python -m pytest tests/<file>` runs one file.

Tests that need her library (or a file only the reference install carries) are listed in `tests/ci-skip.txt` and
skipped; with the library in place, `--no-ci-skip` runs them too.

## Before you push

    git config core.hooksPath .githooks

The pre-push hook runs `scripts/scrub.py`, which refuses keys, private paths and the private words this project
keeps out of its history. CI runs the same scrub and the tests on every push.

## Licences

- `LICENSE`: the MIT licence, for the source code.
- `MEDIA-LICENSE.md`: the films, images, audio, voices and the characters are all rights reserved.
- `CREDITS.md`: who and what made her, starting with Grok Imagine. `FILM-PROVENANCE.json` lists where each film came
  from.
- `THIRD-PARTY-NOTICES.md`: third-party code, models and data, each with its licence.

© 2026 Rocks Cry Out Labs, LLC
