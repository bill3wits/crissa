# Crissa wall pack

Created with Grok.

## Licences and credits

- `LICENSE`: the MIT licence for the source code in this pack.
- `MEDIA-LICENSE.md`: the films, images, audio, voices and characters are all rights reserved.
- `CREDITS.md`: who and what made her, starting with Grok Imagine.
- `THIRD-PARTY-NOTICES.md`: third-party code and the tools used to build the data.

It carries the Linux wall, the avatar player it drives, and two faces:

- **Crissa** (`display/crissa-poses`) — her kit loops, flourishes, crossings and
  curtain leaves, with the playlists and the mouth tiles they name.
- **Lila** (`display/lila-poses`) — her whole library: films, playlists, the
  map-best files and the tile bank.

## One command

    python3 scripts/crissa.py [--face crissa|lila]

starts the full desk in one process group and stops it on Ctrl-C. The screen is the ASCII Wall
(`scripts/house-wall-ascii --fullscreen`), with her picture docked in it. The Control Panel
(`http://127.0.0.1:8097/`) is that Wall's panel: Krew's tiles, or the words "plugin not
installed" when krew is not there. On Linux the Wall is full-screen. On a Mac the same flag is Tk's
full-screen window: it covers the screen and hides the menu bar and Dock while the Wall is in front.
A weak machine gets this same Wall. The command does not open a browser. Her picture is the player on
`http://127.0.0.1:8100` (the voice module's speech-to-text and the ear start when they are installed;
with better mouths on, the aligner). `--audio-at page` lets a browser page own the microphone and the
speakers; the default, `host`, uses this computer's own. `--port-offset N` moves every port;
`--no-open` is kept for older commands and changes nothing; `--plan` prints what would start.

On Linux, run it from a virtual environment (most systems refuse a bare `pip install`, and some ship no
pip for the system Python at all):

    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    python3 scripts/crissa.py

The launcher checks OpenCV (cv2) before it starts anything. It uses `.venv` when that already has it, and
otherwise installs it there and checks again. If the install fails, one plain line names OpenCV (cv2) and
nothing starts. `CRISSA_PACKAGE_INSTALL=0` skips the download and prints the one command above (on Ubuntu,
`python3 -m venv` needs `sudo apt install python3-venv`). `.venv/bin/python scripts/crissa.py` works too.

The voice module needs Python 3.9 to 3.12. When the system Python is newer, `uv` brings a 3.12 without
touching it: `uv venv --python 3.12 .venv`, then `uv pip install --python .venv/bin/python -r requirements.txt`.

**First run (Init).** The guided first boot - your name, a look at the room - is off unless you ask for it:
`CRISSA_INIT=1 python3 scripts/crissa.py`. It runs once; when it is done it writes `~/.crissa/init.json`
and does not run again (delete that file to walk it again). Without the switch she starts straight away.
During that walk you can store a list of numbers for your own face (`~/.crissa/enroll.json`), or skip.
No picture is kept. The launcher also starts a presence service on 127.0.0.1:8219: Secure is one click
and cannot mint itself from an empty desk; a password holds it for this run; with Secure off, recognition
drops after 5 seconds unless a look refreshes it. A guest gets a polite conversation and no commands.

**Keeping her running.** The launcher restarts a voice, ear or aligner lane that stops, and says why on the
Control Panel. To bring the launcher itself back when her player dies, it runs as a service of your login:
Init installs it when it finishes, or `python3 scripts/crissa.py --service install` (with the flags you start
her with). On Linux that is a systemd user unit (`crissa-launcher.service`, restarted on failure); on a Mac a
launchd agent (`com.crissa.launcher`, kept alive; not yet tested on a Mac). `--service status` shows it, the
Control Panel's launcher tile shows its state and why it last restarted, and `python3 scripts/crissa.py
--uninstall` removes it (your `~/.crissa` stays).

## Her voice

Her voice is chosen by what this computer can carry. The first start probes it once, picks, and says what
it picked and why (kept in `~/.crissa/voice-tier.json`): Chatterbox-Turbo on Linux with an NVIDIA GPU
that has 6 GB free (x86-64; Arm too, not yet verified on Arm hardware; opt-in install, and its self-test must
pass; Kokoro then says the announcer's lines, Pocket stands in for it); else
Audio8 on a robust CPU machine (16 GB of RAM and 8 cores; Linux, or macOS 14 or later), with Pocket then Kokoro behind it; else
Pocket TTS on the CPU (64-bit Linux, or an Apple Silicon Mac on macOS 14 or later: an 8 GB M1 or M2 runs it;
stock voices only); else Kokoro; else the computer's own voice (`say` on a Mac); else a Piper model you name; else text on screen. What is
missing is one line with how to get it, and a computer with more room is told what more it could run.
`python3 scripts/crissa-voice-tier.py` shows the pick, `--pick ENGINE` overrides it and is kept, `--probe`
probes again. Whenever one voice stands in for another, the screen and the log (`~/.crissa/logs/`) say
which and why. Name an engine outright with `CRISSA_TTS=turbo|pocket|kokoro|openai|piper|say` (or `--tts` on the
launcher); `CRISSA_TTS_VOICE` picks a voice within it (`say -v '?'` lists a Mac's), and Piper needs
`CRISSA_TTS_PIPER_MODEL=<voice.onnx>`. She never reaches for a speech service you did not start.

## Launch

Each face is its own player. From the pack's root, with a Python that has cv2 and numpy:

    CRISSA_AUTHORING=0 HOUSE_CONDUCTOR_OWN_MAILBOX=1 HOUSE_CONDUCTOR_PORT=8100         HOUSE_POSE_DIR=display/crissa-poses python3 scripts/house-conductor.py
    CRISSA_AUTHORING=0 HOUSE_CONDUCTOR_OWN_MAILBOX=1 HOUSE_CONDUCTOR_PORT=8101         HOUSE_POSE_DIR=display/lila-poses python3 scripts/house-conductor.py

The picture is `http://127.0.0.1:<port>/frame.jpg` (a live stream at `/frame.mjpg`).

**Connect an agent** at `http://127.0.0.1:<port>/connect`: any server that speaks the OpenAI
chat-completions API (a local model server, a hosted API, an agent framework's gateway). The
address, key and model are kept in `~/.crissa/agent.json` (mode 0600). She answers what is sent to
`POST /v1/ask {"text": ...}`; the page has a box for it. To talk to her out loud, run
`python3 scripts/crissa-ear.py --turns <url>` beside the player: it reads the turns a speech-to-text
service heard (JSON) and asks through `/v1/ask`, and drops what it hears while she speaks. Her voice is
chosen as in "Her voice" above; the mouth sheet is kept by the player itself (`/v1/mouth`). When your
agent is slow to answer, the Wall page says "Still waiting on your endpoint" until it does; nothing is
cut off. Behaviour is driven by routes:
`/v1/comms` for a conversation, `/v1/cue` for a named clip, `/v1/home` on the first tool call,
`/v1/say {"stop": true}` to stop her mid-line.

The Composer is not in this pack. Its button links out to the Crissa store: `https://store.zoeyos.dev/store`. The player's
own address (`/`) still opens the Wall or the Connect page; only `/composer` and the Composer button go to the
store. To send the button somewhere else, set `CRISSA_COMPOSER_URL` before starting Crissa.

## macOS

The player runs on a Mac (Apple Silicon or Intel) on the CPU; you watch her in a browser at
`http://127.0.0.1:<port>/connect`. The Linux wall (the desktop wallpaper) is not for macOS.

    brew install python@3.12 ffmpeg
    $(brew --prefix)/bin/python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
    CRISSA_AUTHORING=0 HOUSE_CONDUCTOR_OWN_MAILBOX=1 HOUSE_CONDUCTOR_PORT=8100         HOUSE_POSE_DIR=display/crissa-poses .venv/bin/python scripts/house-conductor.py

Her voice is Kokoro when it is installed, else the Mac's own (`say`); a Mac cannot run Turbo. Set
`CRISSA_TTS_VOICE` before the launch line to pick a `say` voice.
Use Homebrew's Python, not `/usr/bin/python3`. `ffmpeg` brings `ffplay`, which plays her voice as it
is made; without it a line is played whole with the built-in `afplay`. Sound goes to the Mac's
default output. The audio switch (`CRISSA_AUDIO_SHIM`) defaults on for macOS.

Built from ref `42e6125826d374656264979a0b49daf712c3288e` by `scripts/crissa-export.py`. Authoring is off in the
player (`CRISSA_AUTHORING=0`); the audio shim switch (`CRISSA_AUDIO_SHIM`) is
left unset, which is off. State (flags, prefs, locks, caches) lives in
`~/.crissa`; `CRISSA_HOME` moves it.
