# Crissa voice module (optional)

Lets people talk to the avatar out loud. The light Wall install skips it; nothing here is needed to watch
her, type to her, or connect an agent.

- `scripts/crissa-stt.py`: listens on the microphone, finds where speech starts and stops, sends each
  utterance to a speech-to-text service, and serves the results at `/v1/turns`.
- `scripts/crissa-asr.py`: a speech-to-text service on this machine's CPU: ARK-ASR 0.6B INT8
  (Apache-2.0, Hugging Face `Edge0/ark-asr-0.6b-int8-onnx`). Installed on first use with `--install`
  into `~/.crissa/asr` (about 1.1 GB of Python packages and 1.7 GB of model, from PyPI, the PyTorch CPU
  index and Hugging Face). Any service with the same `/stt` contract can stand in for it.

- `scripts/crissa-tts.py`: a speech service on this machine's CPU: Kokoro-82M (Apache-2.0, Hugging Face
  `onnx-community/Kokoro-82M-v1.0-ONNX`), served on loopback as an OpenAI speech endpoint
  (`POST /v1/audio/speech`, port 8189). Words become sounds through misaki (Apache-2.0) and its own small
  model for unknown words; espeak is never installed. Installed on first use with `--install` into
  `~/.crissa/tts` (about 1 GB of Python packages and 0.4 GB of model). On a weak machine and an 8 GB Mac,
  Init installs it as Pocket's backup and Crissa speaks `af_heart`; it is not her voice when Turbo or Audio8
  is. You can still name it (`CRISSA_TTS=kokoro`, or `"engine": "kokoro"` in the `tts` block of
  `~/.crissa/agent.json`, or `--pick kokoro`). Each face's voice comes from
  its `avatars/<face>/manifest.json` (`tts.kokoro`).
- `scripts/crissa-turbo.py`: the top voice for a machine with an NVIDIA GPU: Chatterbox-Turbo by Resemble AI (350M;
  code and weights MIT, `ResembleAI/chatterbox-turbo`, every file at one pinned revision and checked against its
  sha256). Opt-in: `--install` puts about 5 GB of Python packages (PyTorch 2.11 with CUDA, from a hash lock) and
  3 GB of model in `~/.crissa/turbo`; it needs a GPU with 6 GB free as `nvidia-smi` counts it. It serves the house's
  streaming contract (`POST /stream`, port 8186): each line is cut into ~80-character sentence chunks, each chunk is
  rendered whole while the next one renders, and `/abort` stops within one token and drops every queued clip.
  Measured on the reference machine's GPU (Linux x86-64, Blackwell): 3.7 GiB on the card at peak, first clip in
  0.16 to 0.21 s, each next clip 0.36 to 0.43 s behind the last (always ahead of playback), real-time factor about
  0.11, barge-in ends the stream within 5 ms. Linux only: it has no Apple GPU path and is far too slow on a CPU to
  talk, so Pocket speaks there.
  **Arm with an NVIDIA GPU (not yet verified on Arm hardware).** Grace, GH200 and other SBSA servers install from
  an Arm hash lock with PyTorch's cu128 wheels; a DGX Spark / GB10 (sm_121) from one with cu130 wheels
  (`CRISSA_TURBO_TORCH_CUDA=cu128|cu130` overrides the guess). The Arm install runs in stages from the lock, each
  with `--no-deps`: torch and torchaudio first, then chatterbox-tts, then its runtime dependencies, then
  resemble-perth (the watermark stays on). Turbo never calls `torchaudio.save`/`load` (torchcodec is broken on
  GB10); it streams raw PCM. A Jetson needs NVIDIA's JetPack torch wheels (Jetson AI Lab), not these: Turbo says so
  and Pocket speaks. Every install ends with a self-test in the env (CUDA visible, kernels for this GPU, one GPU
  op, chatterbox and Perth import; `--self-test` runs it again). While it fails, Init passes Turbo over with the
  reason on screen and in the log, and Pocket speaks.
  **Watermark.** Every clip carries Resemble's Perth watermark (`resemble-perth`), as upstream ships it. It is on by
  default; `turbo.settings.watermark: false` in the tier table (or `CRISSA_TURBO_WATERMARK=0`) turns it off, and
  `/health` and the start-up log always say which. If Perth cannot load while the setting is on, Turbo refuses to
  start rather than speak unmarked.
  Crissa speaks from her own clip, which ships here (`faces/crissa-imagine.wav`, cut from her Grok Imagine films' sound; "Created with Grok"; where it came from is in `faces/crissa-imagine.json`), and Lila from hers (`faces/lila-imagine.wav`, cut from a Grok Imagine film's sound the same way; `faces/lila-imagine.json`). The announcer speaks from a stock clip fetched at install; a clip you have the rights to (`~/.crissa/turbo/voices/<face>.wav`) wins; else Turbo's built-in voice, and the log says which.
- `scripts/crissa-pocket.py`: a voice on the CPU for lighter machines and Macs: Kyutai Pocket TTS (100M; code
  MIT, weights CC BY 4.0, `kyutai/pocket-tts-without-voice-cloning`, every file at a pinned revision and checked
  against its sha256). `--install` puts about 1 GB of Python packages (PyTorch for the CPU) and 0.33 GB of model
  and voices in `~/.crissa/pocket`. Measured on 3 threads: about 1.2 GiB of memory, about 0.1 s to first sound,
  RTF about 0.3. It serves the same streaming contract as Turbo (`POST /stream`, port 8188): each line is cut into
  ~80-character sentence chunks and each chunk's 80 ms frames are sent as they are made (`emit: "chunk"` in the
  tier settings, or `POCKET_EMIT=chunk`, sends whole chunks instead). `/abort` stops within one frame and drops
  every queued chunk. On a Mac it needs Apple Silicon and macOS 14 or later.
  **Stock voices only.** Packs speak with Pocket's own stock voices and it never clones: it loads no reference
  clip, and a voice not in its list speaks with the default. The allowed voices, each CC BY 4.0 or CC0 (checked
  against `kyutai/tts-voices`): alba, anna, azelma, caro_davy, charles, eponine, eve, fantine, george, jane,
  javert, marius, mary, michael, paul, peter_yearsley, stuart_bell, vera. Not allowed: cosette and jean (CC BY-NC,
  non-commercial). A face maps to one in the tier table (`pocket.settings.voices`: crissa azelma, lila eve, announcer
  anna). Crissa and Lila only ever speak with a woman's voice: alba is still allowed by name, but it measured in a
  man's pitch range (2026-10-07), so it is no face's voice, and a saved map that names a man's voice for her is
  overruled (with a line in the log). Cloning a voice with Pocket needs Kyutai's gated weights, which you can only unlock with your own Hugging
  Face login after accepting their terms; Crissa never asks for that login and never clones.

## Which voice speaks (the voice tier)

Init picks the voice this machine can run and INSTALLS it during setup, as a visible step on the Machine plate
with its progress (no terminal needed). The order is Turbo, Audio8, Pocket, Kokoro, the Mac's `say`, text. Audio8
is her voice only on a robust CPU machine with no GPU voice, and then Pocket is installed too as her backup.
Kokoro (af_heart) is installed only when Pocket is her voice: a weak machine, and an 8 GB Mac (then `say` on a
Mac). Turbo and Audio8 orders do not include Kokoro. An install that
fails falls to the next one, and the screen says so in plain words ("Crissa's voice could not be installed: <reason>.
She will speak in text for now; you can retry from the Connect page."). What was chosen is kept in
`~/.crissa/voice-tier.json` (`voice_install`), and a voice installed while the Wall runs is started by the launcher
without a restart. `CRISSA_INIT_VOICE_INSTALL=0` (developers) makes Init only pick, as before.

| Tier | Needs | Speaks with |
|---|---|---|
| turbo | English; Linux (x86-64, or Arm: not yet verified on Arm hardware), an NVIDIA GPU with 6 GB free, 8 GB RAM, its install self-test passed | Chatterbox-Turbo, sentence by sentence; then Pocket |
| audio8 | Linux or a Mac on macOS 14+, a robust CPU machine: 16 GB RAM and 8 cores; not when Turbo is her voice (on a GPU machine it follows Pocket instead, where installed) | Audio8 TTS with her own clip (jane, a woman's stock voice, VCTK p339, is its backup); then Pocket, then `say` on a Mac. Audio8 is the announcer there too (anna); one Audio8 server speaks both, one line at a time |
| pocket | English, French, German, Spanish, Italian, Portuguese or Dutch; 4 GB RAM; on a Mac, Apple Silicon and macOS 14+ | Pocket TTS with eve (an 8 GB M1 or M2 runs it); then Kokoro (af_heart) where Pocket is her voice; then `say` on a Mac |
| kokoro | English; 4 GB RAM; Linux or Apple Silicon. Joins her chain only when Pocket is her voice | Kokoro, af_heart. Not next after Turbo or Audio8 |
| say | macOS | the system voice |
| text | nothing | her words on screen |

These speak when you name them (`--pick`, or the `tts` block of agent.json). Kokoro is also the Pocket backup in the table above; Init does not pick it as her voice anywhere else:

| Voice | Needs | Speaks with |
|---|---|---|
| multilingual | an NVIDIA GPU (opt-in install) | Chatterbox Multilingual V3, for a language other than English |
| turbo_mlx | Apple Silicon, macOS 14+, 16 GB unified RAM (NOT YET VERIFIED ON APPLE SILICON HARDWARE) | Chatterbox-Turbo via MLX |
| piper | a Piper voice model you supply | Piper |
| audio8 (as the announcer) | Linux or a Mac on macOS 14+, a robust CPU machine: 16 GB RAM and 8 cores (measured: 1.7 GiB peak, near real time only with about 8 cores free) | Audio8 TTS on the CPU: the ANNOUNCER's first voice there, installed by Init; Crissa's own voice too there unless a GPU voice is hers (the order above) |

Init asks for your language (`--language fr`, or the Wall's Init) and shows each language's order; a language with
no voice at a tier says so and her words go on screen. The announcer has its own order: Audio8 on a robust CPU
machine (see the audio8 row above), then Pocket, then Kokoro, then `say`, then text; Init installs the first it can run. Each face (Crissa, Lila, the announcer) has a
stock voice at every tier (Pocket: azelma, eve, anna; Audio8: her own clip with jane as its backup, eve, anna; Turbo and Multilingual: stock clips fetched and checked by
sha256 into `~/.crissa/voice-faces/`; Kokoro: af_heart, bf_emma, bm_george; `say`: Samantha, Karen, Daniel), and
Init checks that all three speak and says plainly when one falls back.

On a GPU tier a keep-warm sends a short line to the voice on start and every `keep_warm.interval_s` seconds (600 by
default, in the tier table) so the first line after an idle spell is quick; `keep_warm.on: false` turns it off, and
the Control Panel shows it (last warm, its log, Run now).

Arm GPU notes: ResembleAI/chatterbox-turbo-ONNX was evaluated and not adopted (not a CPU tier; ONNX on CPU measured too slow against Pocket; the PyTorch --no-deps Arm path stays). CoreML (ebrinz/iliasaz) and GGUF (cstr) ports were noted and are not adopted.

A tier that is not installed is named, and Init installs it; a machine with more room is told
what more it could run (the RL-tuned model, full precision, vLLM) and is never capped. Every time a voice
stands in for another, the console and the log say which and why.

    python3 scripts/crissa-voice-tier.py              # what was picked, why, and what is missing
    python3 scripts/crissa-voice-tier.py --pick kokoro  # your choice wins, and is kept
    python3 scripts/crissa-voice-tier.py --probe      # probe again (new GPU, more memory)
    python3 scripts/crissa-voice-tier.py --install    # install the voice it can run, as Init does (the retry)


The table is `config/crissa-voice-tiers.json`; a copy in `~/.crissa/` changes it key by key.
`CRISSA_TTS` and agent.json's `tts` block still name an engine outright.

Run:

    pip install -r requirements.txt
    python3 modules/voice/scripts/crissa-asr.py --install     # once
    python3 modules/voice/scripts/crissa-asr.py &
    python3 modules/voice/scripts/crissa-stt.py &
    python3 scripts/crissa-ear.py --turns http://127.0.0.1:8190/v1/turns

Her own voice on this machine (optional):

    python3 modules/voice/scripts/crissa-tts.py --install     # once
    python3 modules/voice/scripts/crissa-tts.py &
    CRISSA_TTS=kokoro python3 scripts/house-conductor.py      # or the tts block in agent.json

Pocket, the CPU voice (Linux, or an Apple Silicon Mac on macOS 14+):

    python3 modules/voice/scripts/crissa-pocket.py --install  # once
    python3 scripts/crissa.py                                 # starts Pocket when it is in this machine's voice chain

Audio8, the announcer (16 GB RAM and 8 cores; about 1 GB; Init installs it on such a machine):

    python3 modules/voice/scripts/crissa-audio8.py --install  # once, then its self-test
    python3 modules/voice/scripts/crissa-audio8.py --restart  # the Control Panel's Restart key

The announcer can take turns between two voices, one each day (`announcer.audio8` in the tier table: `voice` A,
`voice_b` B, `alternate` "day"). A is anna (VCTK p228, the stock English voice); B is empty until you set it
(proposed: `vctk_p283`, VCTK's speaker from Cork). Audio8 ships no stock voice: each is registered from a
CC BY 4.0 corpus clip fetched at install, and no other name is accepted.

Turbo, the GPU voice (Linux with an NVIDIA GPU, optional):

    python3 modules/voice/scripts/crissa-turbo.py --install   # once
    python3 scripts/crissa.py                                 # starts Turbo when it is this machine's tier

Every install is pinned: each package by version and sha256 from a lock file in `crissa_voice/locks/`
(Linux x86-64 and ARM, macOS on Apple Silicon, Python 3.12), installed with `pip --require-hashes`. A
machine with no lock file installs the same versions without hashes and says so.

Each voice gets its own Python, never the system's (an Ubuntu that ships only 3.14 is fine): `--install` runs
under any Python 3 and makes `~/.crissa/<voice>/env` with micromamba from conda-forge, Python pinned in
`locks/python-env.json` (3.12.15 today, with micromamba 2.9.0-0 checked by sha256 and fetched once into
`~/.crissa/micromamba`; your own `micromamba` on PATH is used when there is one). One env per voice, because their
locks disagree on torch and numpy; the package cache is shared, so Python downloads once. Every download goes
through the same kill-search switch as the models. `CRISSA_VOICE_PYENV=0` (developers) builds the env from the
Python that runs the installer instead, as before. On a Mac the voices need Apple Silicon; `--install` says so and
stops before it downloads anything.

Ports: each service takes `--port N`, and by default follows the shared table (`CRISSA_PORT_ASR`,
`CRISSA_PORT_STT`, `CRISSA_PORT_TTS`, or `CRISSA_PORT_OFFSET=N` to move them all, as the launcher's
`--port-offset` does). A port that is already taken is one line on stderr and exit 2, never a traceback.

For a machine without the internet, make a bundle on a connected machine of the same kind (same OS, chip and
Python minor version), carry the file over, and install from it; the install downloads nothing:

    cd modules/voice
    python3 -m crissa_voice.asr_setup --bundle-build DIR        # speech in, on the connected machine
    python3 -m crissa_voice.asr_setup --bundle-install FILE     # on the other one
    python3 -m crissa_voice.kokoro_setup --bundle-build DIR     # the Kokoro voice, the same way
    python3 -m crissa_voice.kokoro_setup --bundle-install FILE

A bundle holds other projects' software under their own licences; you make your own, nobody hands one out.

What she says is marked as her own (`her_voice`) and never answered. Two voice clips ship in this module:
Crissa's own (`faces/crissa-imagine.wav`) and Lila's (`faces/lila-imagine.wav`), both created with Grok, which the
voices that clone (Chatterbox, Audio8) speak them with; Pocket speaks them with its stock azelma and eve. Everything
else is fetched by `--install`.

Licences of what `--install` fetches (none ships in this module): Kokoro-82M by hexgrad (Apache-2.0; the ONNX
build by onnx-community); misaki by hexgrad (Apache-2.0, its word lists included); PeterReid's
graphemes_to_phonemes_en_us and _en_gb (Apache-2.0); spaCy and en_core_web_sm (MIT); num2words (LGPL-2.1,
installed unmodified from PyPI); Chatterbox-Turbo and chatterbox-tts by Resemble AI (MIT) with its Perth
watermarker (MIT); Pocket TTS by
Kyutai (code MIT, weights CC BY 4.0: "Voice: Pocket TTS by Kyutai (kyutai.org), weights CC BY 4.0") and its stock
voices (CC BY 4.0 or CC0 each, credited in CREDITS.md); ARK-ASR
(Apache-2.0); PyTorch (BSD-3-Clause) and the rest under their own licences. No espeak, no GPL package.

