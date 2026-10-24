# Crissa id module (optional)

Init - the first-boot routine the Wall walks - and enrolment: the identity store a Crissa install learns its
people into. The light Wall install skips it; nothing here is needed to watch her, talk to her, or connect an
agent. It is the house's own `house-id` tools and its two Init walkers, brought across whole (the User
2026-10-09: "extract, don't rewrite"), with the seams cut that a pack must cut.

- **Init** (`crissa_id/init_wizard.py`, `config/init-sequence.json`): a pack has ONE first-boot walk (the User
  2026-10-10), the player's `lib/crissa_init.py`: Connect, the machine card, **Voice ID** (the Register coach's
  routine: the host's clips, the sentence read at the desk, the turn and the count - the voice prints, and face stills
  when there is a camera), **face photos** (the coach's face section; with no camera the step says why and skips),
  done, landing on her listening loop. Served by the player on `/v1/init` (`CRISSA_INIT=1`), drawn by the Wall and
  by the browser page `/init`. This module's Init v1 and v2 entries hand over to it; it supplies the identity store
  (the person, the User's role, `site.json`, `user.json`, `enroll.json`).
- **Enrolment** (`crissa_id/house_enroll`): the `house-enroll` CLI, the Register routine (`voice-register`), face
  capture, the voice session, the empty-room references, the coach page (SSE) and its HTTP surface
  (`/health /session /people /events`, `POST /start /prearm /cancel /emit`).
- **Faces**: MediaPipe Face Landmarker + AuraFace (Apache-2.0), the engine `modules/oracle` already runs, so one
  enrolment serves the oracle, the presence service and Register; match bar from `lib/crissa_presence.MATCH`.
  The house's detector family does not ship.
- **Voices**: WeSpeaker ResNet221 (ONNX on the CPU; kaldi fbank features) as the live print, ECAPA (speechbrain)
  as the museum option. Prints are `people/<id>/voice/ws_enroll_*.json`; the ear's matcher reads them from the same
  store with the same engine file (`crissa_id/wespeaker_onnx.py`, byte for byte the ear's and the presence
  module's), so a print Init writes is a print the ear can match.

## What it writes, and where

Everything under the state home (`CRISSA_HOME`, default `~/.crissa`):

    identity/people/<id>/person.json          who (id, display, role: user|regular|guest)
    identity/people/<id>/face/*.json          face prints: {encoding (512), model: auraface-v1, dim, bbox, yaw, bin, kps}
    identity/people/<id>/face/refs/*.jpg      the enrolment stills (yours, on this machine)
    identity/people/<id>/voice/*.json         voice prints: {encoding (256), model: wespeaker/english, dim}
    identity/people/_index.json               the roster
    identity/site.json                        this room: mic, sink, service addresses, stations (from config/site.example.json)
    identity/site/init-sequence.json          a copy of the routine once walked (the shipped one in config/ otherwise)
    init.json, user.json, enroll.json         the player's own records (NOTE-presence-format.md)

Nothing ships in `people/`: a pack mints the store empty. Delete `identity/` to forget everyone.

## Run

    python3 modules/id/scripts/crissa-id.py --install      # the id env (hash lock, ~2 GB with torch CPU) + the face models
    python3 modules/id/scripts/crissa-id.py --status
    modules/id/scripts/house-enroll doctor                 # the CLI (status, person add, voice, face, room-refs, init ...)
    python3 modules/id/scripts/crissa-id.py --run          # the coach on the enroll port, when no presence module runs one

The coach is normally the presence module's child on port 8196 (`modules/presence/scripts/crissa-enroll.py`);
the player's `/v1/action enroll_start|enroll_cancel|enroll_prearm` reach it, `/v1/register` carries its glass.

## Locks

`locks/id.in` names the exact versions; `locks/id-<target>-py312.txt` pins every package by sha256 for
linux-x86_64-cpu, linux-aarch64-cpu and macos-arm64 (made with `uv pip compile --generate-hashes`, torch from the
PyTorch CPU index). A machine with no lock for its platform installs the same versions without hashes and says so.
The voice-ID model is fetched by `--install` (2026-10-10): the ONNX file WeSpeaker publishes for the house's
ResNet221-LM checkpoint (`Wespeaker/wespeaker-voxceleb-resnet221-LM` @ 26732f43, sha256 e6b8c14c...), into
`<state home>/models/voice-id/` (one file for every module), checked by sha256; the features are computed in numpy,
so neither torch nor the `wespeaker` package runs for a voice print. Without the file voice prints wait and
`crissa_id.voice_embed.status()` says where it looks. The torch and torchaudio pins stay in this lock for now; the
encoder no longer needs them.
