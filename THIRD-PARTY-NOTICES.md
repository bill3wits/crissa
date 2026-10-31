# Third-party notices

Crissa is built on work that other people chose to share. This file lists the third-party software, models and
data that Crissa contains or puts on your machine, what each one does here, and the licence it comes under.

Three kinds of use, named on every entry:

- **Shipped**: the material is inside this repository.
- **Installed on first use**: the material is not in this repository. When you turn the optional piece on, its
  installer fetches it from the project's own servers at a pinned revision, checks it, and installs it on your
  machine under its own licence. Turning that piece on is your choice.
- **Used to build the data**: a tool that made data this repository carries (its films' face and lip tracks).
  The tool itself is not shipped.

Everything else in the repository is covered by `LICENSE` (the code, MIT) and `MEDIA-LICENSE.md` (the films,
images, audio, voices and characters). The full MIT, Apache-2.0 and BSD texts are at the end of this file.


## InsightFace: head-pose functions in `lib/head_frame.py`

- **What and how**: the functions `estimate_affine_matrix_3d23d`, `P2sRt` and `matrix2angle` in
  `lib/head_frame.py` are adapted from InsightFace (`python-package/insightface/utils/transform.py`), which
  traces back to face3d (https://github.com/YadiraF/face3d). They place her mouth on her head. **Shipped.**
  No InsightFace model is used or shipped.
- **Licence**: MIT License (the InsightFace library code).
- **Copyright**: Copyright (c) 2022 Jiankang Deng and Jia Guo (the InsightFace contributors)
- **Source**: https://github.com/deepinsight/insightface

  Copyright (c) 2022 Jiankang Deng and Jia Guo (the InsightFace contributors)

  MIT License

  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
  associated documentation files (the "Software"), to deal in the Software without restriction,
  including without limitation the rights to use, copy, modify, merge, publish, distribute,
  sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all copies or
  substantial portions of the Software.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
  NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
  NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
  OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
  CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


## MediaPipe

- **What and how**: Google's MediaPipe Face Landmarker finds a face and its landmarks in a frame. It **was used
  to build the data**: the head and lip tracks of the films in the faces library. The optional face check
  (`modules/oracle`) **installs it on first use**: the `mediapipe` package from PyPI and the
  `face_landmarker.task` model from Google's model store.
- **Licence**: Apache License 2.0 (the package, and the BlazeFace, Face Mesh V2 and Blendshape V2 model cards).
- **Copyright**: Copyright 2019 The MediaPipe Authors.
- **Source**: https://github.com/google-ai-edge/mediapipe · licence: https://www.apache.org/licenses/LICENSE-2.0


## Montreal Forced Aligner (MFA)

- **What and how**: MFA lines each spoken word up with the sound, so her mouth moves on the word. The optional
  aligner (`lib/crissa_aligner.py`) **installs it on first use**: Montreal Forced Aligner 3.4.2, built from conda-forge with micromamba (BSD-3-Clause)
  into a private environment, and the `english_us_arpa` acoustic model and dictionary from Hugging Face
  (`MontrealCorpusTools/english_us_arpa`, revision 9c8d5d8e1dd5d9cf188a5571203de0e09ca6bede).
- **Licence**: MFA, MIT License. The `english_us_arpa` model and dictionary, CC BY 4.0. That environment also brings
  Kaldi, OpenFst and Pynini (Apache-2.0) and, as conda-forge dependencies, programs and libraries under the GPL or
  LGPL (among them sox, an ffmpeg built with x264/x265, readline, libsndfile, mpg123 and lame). conda installs them
  on your machine; none of them is in this repository, and Crissa runs the aligner as its own process and links
  none of them.
- **Copyright**: Copyright (c) 2016 Montreal Corpus Tools. The model and dictionary: the Montreal Forced Aligner
  authors.
- **CC BY 4.0 notice**: the `english_us_arpa` model and dictionary, by the Montreal Forced Aligner authors, used
  unmodified (fetched on first use at the pinned revision), under https://creativecommons.org/licenses/by/4.0/, as is,
  with no warranties (Section 5: https://creativecommons.org/licenses/by/4.0/legalcode#s5).
- **Source**: https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner ·
  https://huggingface.co/MontrealCorpusTools/english_us_arpa · licences: https://opensource.org/license/mit ·
  https://creativecommons.org/licenses/by/4.0/


## Kokoro

- **What and how**: Kokoro-82M is a small text-to-speech model that runs on the CPU, one of her voices. The voice
  module (`modules/voice`) **installs it on first use**: the ONNX build `onnx-community/Kokoro-82M-v1.0-ONNX`
  (revision 1939ad2a8e416c0acfeecc08a694d14ef25f2231) from Hugging Face, into a private environment.
- **Licence**: Apache License 2.0 (the model, upstream `hexgrad/Kokoro-82M`, and its ONNX build).
- **Copyright**: Copyright hexgrad (Kokoro-82M); the ONNX build, onnx-community.
- **Source**: https://huggingface.co/hexgrad/Kokoro-82M ·
  https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX · licence: https://www.apache.org/licenses/LICENSE-2.0


## misaki

- **What and how**: misaki turns her words into the sounds Kokoro speaks. **Installed on first use** with Kokoro:
  misaki 0.9.4 from PyPI, WITHOUT its `[en]` extra, so misaki itself never installs or loads espeak-ng. Words misaki does
  not know go to its out-of-dictionary models, `PeterReid/graphemes_to_phonemes_en_us` (revision
  a5631b285d18d59483c32c0c3379cb9fac924f4b) and `PeterReid/graphemes_to_phonemes_en_gb` (revision
  d8357d5067fa26a5c34134d6bbcf4bbf000c0ac8), Apache-2.0. misaki reads sentences with spaCy and `en_core_web_sm`
  (MIT) and says numbers with num2words 0.5.14 (LGPL-2.1, installed unmodified by pip from PyPI into the same
  private environment; it is not in this repository and you may replace it with any other version).
- **Licence**: Apache License 2.0 (misaki and the out-of-dictionary models); num2words, GNU LGPL 2.1.
- **Copyright**: Copyright hexgrad (misaki); Peter Reid (the out-of-dictionary models); Taro Ogawa and
  contributors (num2words).
- **Source**: https://github.com/hexgrad/misaki · https://huggingface.co/PeterReid ·
  https://github.com/savoirfairelinux/num2words · licences: https://www.apache.org/licenses/LICENSE-2.0 ·
  https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html


## Pocket TTS

- **What and how**: Kyutai's Pocket TTS, her voice on the CPU, Macs included. The voice module **installs it on
  first use**: the `pocket-tts` 3.3.0 package from PyPI and the weights `kyutai/pocket-tts-without-voice-cloning`
  from Hugging Face, checked by sha256. Nothing is cloned on your machine: Pocket speaks its stock voices, and
  Crissa's own Pocket voice ships made (below).
- **Crissa's Pocket voice (shipped since 2026-10-17)**: `modules/voice/faces/crissa-pocket.safetensors` (sha256
  012eb0e3fbc0e67560dcacc52ca2be3afef11a2004b01d36c3f2e90e58d3698f, with `crissa-pocket.json` beside it) is a Pocket
  voice state: her own clip (`crissa-imagine.wav`, created with Grok, no real person's voice) run once through
  Pocket TTS's `export_model_state` with Kyutai's gated `kyutai/pocket-tts` weights, after their access terms were
  accepted. The kit's no-cloning model loads it like a stock voice. It is a file made with CC BY 4.0 weights, so
  it carries the credit below; her voice in it is Media under MEDIA-LICENSE.md. Kyutai's access terms forbid voice
  impersonation or cloning without explicit and lawful consent and presenting generated audio as a real person's
  recording; this state is made from a synthetic voice that belongs to us.
- **Licence**: the code, MIT License; the weights, CC BY 4.0.
- **Copyright**: Kyutai and the Pocket TTS contributors (kyutai.org). The upstream MIT licence file names no
  holder.
- **Credit (CC BY 4.0)**: Voice: Pocket TTS by Kyutai (kyutai.org), weights CC BY 4.0.
- **CC BY 4.0 notice**: the weights, by Kyutai, used unmodified (fetched on first use and checked by sha256), under
  https://creativecommons.org/licenses/by/4.0/, as is, with no warranties (Section 5:
  https://creativecommons.org/licenses/by/4.0/legalcode#s5).
- **Source**: https://github.com/kyutai-labs/pocket-tts ·
  https://huggingface.co/kyutai/pocket-tts-without-voice-cloning · licences: https://opensource.org/license/mit ·
  https://creativecommons.org/licenses/by/4.0/


## Chatterbox (Chatterbox-Turbo and Chatterbox Multilingual) and Perth

- **What and how**: Resemble AI's Chatterbox-Turbo is her voice on a machine with an NVIDIA GPU; Chatterbox
  Multilingual speaks her in other languages. The voice module **installs them on first use**, as an opt-in: the
  `chatterbox-tts` 0.1.7 package from PyPI and the weights `ResembleAI/chatterbox-turbo` (revision
  749d1c1a46eb10492095d68fbcf55691ccf137cd) and `ResembleAI/chatterbox` from Hugging Face, checked by sha256.
  Every clip it speaks carries Resemble's Perth watermark (`resemble-perth` 1.0.1), as upstream ships it.
  Crissa and Lila speak from their own clips, which ship (`modules/voice/faces/crissa-imagine.wav` and
  `lila-imagine.wav`, created with Grok: first-party Media under MEDIA-LICENSE.md, not third-party material; their
  provenance is in `crissa-imagine.json` and `lila-imagine.json` beside them). The announcer speaks from a stock clip
  fetched from `kyutai/tts-voices` (below), or from a clip of your own that you have the rights to.
  Lila's ten spoken Setup films ship with their sound made by Chatterbox's speech-to-speech (`ChatterboxVC` in
  `chatterbox-tts` 0.1.7, with the S3Gen weights of `ResembleAI/chatterbox-turbo` at the revision above), in the
  voice of her own clip; they carry the Perth watermark. Nothing of this runs on your machine: the films ship made.
- **Licence**: MIT License (the `chatterbox-tts` code, the Turbo and Multilingual weights, and `resemble-perth`).
- **Copyright**: Copyright (c) 2025 Resemble AI.
- **Source**: https://github.com/resemble-ai/chatterbox · https://huggingface.co/ResembleAI/chatterbox-turbo ·
  https://huggingface.co/ResembleAI/chatterbox · https://github.com/resemble-ai/Perth · licence:
  https://opensource.org/license/mit


## Stock voices: `kyutai/tts-voices`

- **What and how**: the voice clips the faces speak with on Chatterbox, and the stock voices a pack may name on
  Pocket TTS. **Installed on first use**, fetched at a pinned revision and checked by sha256; none ships.
- **Licence and credit**: per voice, from the repository's card. Crissa (her own clip, created with Grok, ships
  instead; she was Alba MacKenna, "casual", then Jane until 2026-10-08). Lila (her own clip, created with Grok,
  ships instead since 2026-10-12; Eve is her backup clip), the announcer, Jane (Crissa's backup clip) and the
  announcer's proposed second voice (vctk_p283, VCTK's speaker from Cork): speakers of the VCTK corpus, CSTR, University of Edinburgh (CC BY 4.0). On Pocket:
  alba (Alba MacKenna, CC BY 4.0); anna, azelma, charles, eponine, eve, fantine, george, jane, mary, michael, paul,
  vera (VCTK corpus, CSTR, University of Edinburgh, CC BY 4.0); marius, javert (Unmute Voice Donation Project,
  CC0); caro_davy, peter_yearsley, stuart_bell (LibriVox readers, from Voice-Zero, CC0); estelle (Kyutai, CC0);
  lola, giovanni (Mozilla Common Voice contributors, CC0); daan (the CML-TTS corpus, from LibriVox, CC BY 4.0).
  The CC BY-NC voices (cosette, jean) are never used.
- **CC BY 4.0 notice**: each CC BY 4.0 clip above, by the speakers and corpus makers named with it, used unmodified
  (fetched on first use and checked by sha256; nothing made from a clip ships), under
  https://creativecommons.org/licenses/by/4.0/, as is, with no warranties (Section 5:
  https://creativecommons.org/licenses/by/4.0/legalcode#s5). Crissa's and Lila's own clips, and Lila's films re-voiced
  with hers, are not CC material: they are ours, created with Grok (MEDIA-LICENSE.md).
- **Source**: https://huggingface.co/kyutai/tts-voices · licences: https://creativecommons.org/licenses/by/4.0/ ·
  https://creativecommons.org/publicdomain/zero/1.0/


## Piper (piper1-gpl)

- **What and how**: Piper is a text-to-speech program for Linux and macOS that Crissa can speak through when you
  name a Piper voice model (`CRISSA_TTS_PIPER_MODEL`). It is **neither shipped nor installed by Crissa**: you
  install Piper (`piper-tts`) and its voice yourself, and Crissa runs it as a separate program and links none of
  it. Piper turns text into sounds with espeak-ng (GPL-3.0-or-later).
- **Voices**: each Piper voice carries its own dataset licence (its MODEL_CARD in `rhasspy/piper-voices`).
  `en_US-ljspeech-medium` (LJ Speech, public domain) and `en_GB-cori-medium` (LibriVox, public domain) are clear
  for any use. `en_US-lessac-medium` is not for a product: its Blizzard 2013 dataset licence is research-only.
  Check any other voice's card before you use it.
- **Licence**: GNU General Public License v3.0 (GPL-3.0-or-later).
- **Copyright**: the Piper authors and the Open Home Foundation.
- **Source**: https://github.com/OHF-Voice/piper1-gpl · https://huggingface.co/rhasspy/piper-voices · licence:
  https://www.gnu.org/licenses/gpl-3.0.html


## The announcer: Audio8 TTS

- **What and how**: Edge0's Audio8 TTS Preview 0.6B. Since 2026-10-17 (round 27) it does not speak in Crissa:
  Init never picks or installs it as her voice, her backup or the announcer (`lib/crissa_voice_tiers.py`
  `NOT_VOICES`), and it comes back only if you set `CRISSA_AUDIO8_TTS=1` and name it. When you do, the voice
  module **installs it on first use**: the
  runtime code (`onnx_runtime/arktts_runtime`, with its `LICENSE` and `NOTICE`) from `Edge0-AI/Audio8_TTS` at
  commit 7b6a5a15e4529da4e37463736de1d70753ceb2fb and the INT4 ONNX weights `Edge0/Audio8-TTS-Preview-0.6B-ONNX-INT4`
  at revision 818569c6b832118ad68d61bbd873abe250fcd68a, every file checked by sha256, with onnxruntime (MIT),
  tokenizers (Apache-2.0), numpy (BSD-3-Clause, with bundled parts under 0BSD, MIT, Zlib and CC0-1.0), scipy and
  soundfile (BSD-3-Clause), fastapi (MIT) and uvicorn (BSD-3-Clause) from PyPI into a private environment, each pinned by hash. None of it ships in this
  repository. Audio8 has no stock voice: it speaks with voices registered on your machine by its own runtime from
  a clip and its transcript: Crissa's own clip (created with Grok, shipped, above) and CC BY 4.0 corpus clips
  (`kyutai/tts-voices`, below), never from a clone of a real person's voice.
- **Licence**: Apache License 2.0, the code and the model weights (the model card's licence field, `apache-2.0`, and
  the repository's LICENSE; the base model `Edge0/Audio8-TTS-Preview-0.6b` is Apache-2.0 too). Checked 2026-10-07.
- **Copyright and NOTICE**: Edge0 (formerly Audio8-AI). The upstream NOTICE reads: "audio8_tts / The model
  architecture is inspired by the DualAR design used in Fish Audio S2 Pro."
- **Responsible use (from the model card)**: obtain consent before cloning a voice, and disclose synthetic audio
  where appropriate. Crissa registers corpus voices only.
- **Source**: https://github.com/Edge0-AI/Audio8_TTS ·
  https://huggingface.co/Edge0/Audio8-TTS-Preview-0.6B-ONNX-INT4 ·
  https://huggingface.co/Edge0/Audio8-TTS-Preview-0.6b · licence: https://www.apache.org/licenses/LICENSE-2.0


## Speech in: ARK-ASR

- **What and how**: ARK-ASR 0.6B hears what you say. The voice module's speech-in **installs it on first use**:
  the INT8 ONNX build `Edge0/ark-asr-0.6b-int8-onnx` (revision ced3e6c0cc45eda7f718b885e9fd9562ed3ec94d) from
  Hugging Face, with onnxruntime (MIT), transformers (Apache-2.0), numpy (BSD-3-Clause), torch CPU (BSD-3-Clause)
  and librosa (ISC) from PyPI into a private environment.
- **Licence**: Apache License 2.0 (upstream `AutoArk-AI/ARK-ASR-0.6B`, now `Edge0/ARK-ASR-0.6B`; both the
  INT8 build and the base model's licence field read `apache-2.0`, checked 2026-10-07).
- **Copyright**: AutoArk.
- **Source**: https://huggingface.co/Edge0/ARK-ASR-0.6B ·
  https://huggingface.co/Edge0/ark-asr-0.6b-int8-onnx · licence: https://www.apache.org/licenses/LICENSE-2.0


## Face identity: AuraFace (optional)

- **What and how**: only if you turn on face identity in the optional face check, its installer fetches AuraFace
  v1's recognition model `glintr100.onnx` from Hugging Face (`fal/AuraFace-v1`, revision
  af6d057c9b0ec4071d4c49c80e3539258798b609). **Installed on first use**; nothing else from that repository is
  fetched.
- **Licence**: Apache License 2.0.
- **Copyright**: fal.
- **Source**: https://huggingface.co/fal/AuraFace-v1 · licence: https://www.apache.org/licenses/LICENSE-2.0


## Voice ID: WeSpeaker ResNet221-LM (VoxCeleb)

- **What and how**: the voice print. Init's Voice ID records two short takes and keeps their voice prints (lists of
  numbers, no audio leaves the machine); the ear compares each spoken turn with them to know whether the person who
  set her up is speaking. The ear, id and presence modules **install it on first use**: the ONNX file
  `voxceleb_resnet221_LM.onnx` from `Wespeaker/wespeaker-voxceleb-resnet221-LM` (revision
  26732f43821f238bfb258c5ef54924a798e62118, sha256 e6b8c14c022288ca186de39f038a9bd9404be4f174ac8a6a3b8dbf229c8f607d),
  run with onnxruntime (MIT) and numpy (BSD-3-Clause). No WeSpeaker code runs or ships: the features are computed by
  the pack's own code.
- **Licence**: the model card says Apache License 2.0; WeSpeaker's own note on its pretrained models says a model
  trained on VoxCeleb follows the VoxCeleb dataset's licence, Creative Commons Attribution 4.0 International
  (CC BY 4.0). Both were read on 2026-10-10. VoxCeleb's page offers the dataset "for research purposes" under CC BY 4.0.
- **Copyright and attribution**: the WeSpeaker team (wenet-e2e). The model was trained on VoxCeleb2 (A. Nagrani,
  J. S. Chung, W. Xie, A. Zisserman, University of Oxford): J. S. Chung, A. Nagrani, A. Zisserman, "VoxCeleb2: Deep
  Speaker Recognition", Interspeech 2018. WeSpeaker: H. Wang et al., "WeSpeaker: A Research and Production oriented
  Speaker Embedding Learning Toolkit", ICASSP 2023.
- **CC BY 4.0 notice**: the model, by the WeSpeaker team, trained on VoxCeleb2 (University of Oxford), used unmodified
  (fetched on first use at the pinned revision and checked by sha256), under
  https://creativecommons.org/licenses/by/4.0/, as is, with no warranties (Section 5:
  https://creativecommons.org/licenses/by/4.0/legalcode#s5).
- **Source**: https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet221-LM ·
  https://github.com/wenet-e2e/wespeaker (code: Apache-2.0) ·
  https://github.com/wenet-e2e/wespeaker/blob/master/docs/pretrained.md (the model licence note) ·
  https://mm.kaist.ac.kr/datasets/voxceleb/ · licence: https://creativecommons.org/licenses/by/4.0/


## zstandard (python-zstandard) and the Zstandard library

- **What and how**: the desk Wall keeps the pictures it has drawn for your screen on disk (`lib/wall_ascii_disk.py`)
  and compresses them with Zstandard, about six times smaller. `zstandard` 0.25.0 is **installed with the Wall's
  packages** (`pip install -r requirements.txt`, the install step every README gives) from PyPI at that version; its
  wheel carries the Zstandard C library, compiled in. Without it the Wall keeps its pictures uncompressed.
- **Licence**: BSD-3-Clause (python-zstandard). The Zstandard library is offered under either a BSD licence or the
  GPLv2; Crissa uses it under the BSD licence. Both texts, unmodified from the 0.25.0 source distribution
  (`zstandard-0.25.0.tar.gz`, sha256 7713e1179d162cf5c7906da876ec2ccb9c3a9dcbdffef0cc7f70c3667a205f0b: its `LICENSE`
  and `zstd/LICENSE`), are at the end of this file.
- **Copyright**: Copyright (c) 2016, Gregory Szorc (python-zstandard). Copyright (c) Meta Platforms, Inc. and
  affiliates (Zstandard).
- **Source**: https://pypi.org/project/zstandard/0.25.0/ · https://github.com/indygreg/python-zstandard ·
  https://github.com/facebook/zstd


## segno

- **What and how**: the menu's "Your phone" card draws a QR code the phone scans (`lib/crissa_phone_card.py`, round
  36). `segno` 1.6.6 is **installed with the player's packages** (`pip install -r requirements.txt`) from PyPI at that
  version; it is pure Python, with no dependencies. Without it the card shows the address and no code.
- **Licence**: BSD-3-Clause. The text, unmodified from the 1.6.6 wheel (`segno-1.6.6-py3-none-any.whl`, sha256
  28c7d081ed0cf935e0411293a465efd4d500704072cdb039778a2ab8736190c7: its `LICENSE`), is at the end of this file.
- **Copyright**: Copyright (c) 2016 - 2025, Lars Heuer.
- **Source**: https://pypi.org/project/segno/1.6.6/ · https://github.com/heuer/segno


## eSpeak NG (called, never shipped; installed for you when it is missing)

- **What and how**: eSpeak NG reads a word's sounds instead of its spelling. Crissa's mouth (`lib/mouth_viseme.py`,
  when its phoneme speller is on: `HOUSE_MOUTH_G2P=espeak`) **calls an eSpeak NG program on your machine as a
  separate process**. With none, her mouth reads the spelling with its own letter rules.
- **Installed for you** (round 40, 2026-10-21, `lib/crissa_espeak.py`): when it is missing, Crissa installs it at
  start, never asking anything:
  - on a Mac, with Homebrew (`brew install espeak-ng`);
  - on Linux, with `sudo -n apt-get install -y espeak-ng` where you may install without a password;
  - else by fetching the **upstream** build from Piper's release 2023.11.14-2 (rhasspy/piper, the build that carries
    eSpeak NG), sha256-pinned, into Crissa's own folder (`<Crissa's state folder>/espeak-ng/`). Only eSpeak NG's
    program, library and voice data are kept.
  - Every download comes from upstream onto your machine. Kill-search refuses it. `CRISSA_ESPEAK_INSTALL=0` turns all
    of it off.
- **Never shipped**: Crissa links none of it and ships none of it. The export refuses eSpeak NG's program, library or
  voice data in any pack. The mouth's own table of eSpeak NG's phoneme names (`config/phoneme-families.json`) is
  Crissa's data. Kokoro's misaki is installed without its espeak extra.
- **Licence**: GNU General Public License v3.0 or later (GPL-3.0-or-later).
- **Copyright**: Jonathan Duddington, Reece H. Dunn and the eSpeak NG contributors.
- **Source**: https://github.com/espeak-ng/espeak-ng · licence: https://www.gnu.org/licenses/gpl-3.0.html


## The ear's voice detectors: Silero VAD and py-webrtcvad

- **What and how**: they hear when you start and stop speaking. The ear module **installs them on first use** from
  PyPI into its private environment, each pinned by hash in its lock (`modules/ear/locks/`): `silero-vad` 6.2.3
  (the default detector) and `webrtcvad` 2.0.10;
  the voice module's own speech-in carries `webrtcvad-wheels`. None of them is in this repository.
- **Licence**: Silero VAD, MIT License (Silero Team). py-webrtcvad and webrtcvad-wheels, MIT License (John Wiseman;
  the WebRTC voice detector inside them, BSD-3-Clause, the WebRTC project authors). TEN VAD is no longer installed
  or used (2026-10-22): an ear set to it uses Silero VAD.
- **Copyright**: Silero Team; John Wiseman.
- **Source**: https://github.com/snakers4/silero-vad · https://github.com/wiseman/py-webrtcvad


## Packages the installers bring (under their own licences)

None of these is in this repository. `pip install -r requirements.txt` (the player and the Wall) and the
modules' hash locks put them on your machine from PyPI, unmodified, each under its own licence (read from the
installed wheels' metadata on 2026-10-21): numpy (BSD-3-Clause, with bundled parts under 0BSD, MIT, Zlib and
CC0-1.0); opencv-python-headless and opencv-contrib-python (Apache-2.0; their wheels carry FFmpeg, under the LGPL,
as their own third-party notice says); Pillow (MIT-CMU); sounddevice (MIT); soundfile (BSD-3-Clause); PyYAML (MIT);
websocket-client (Apache-2.0); onnxruntime (MIT); torch (BSD-3-Clause; its newer wheels also list Apache-2.0,
BSD-2-Clause and BSL-1.0 parts) and torchaudio (BSD); mediapipe (Apache-2.0, above); python-pam (MIT, David Ford),
which the presence module uses to check your own login password on your machine. The voice module makes each
engine's private environment with micromamba (BSD-3-Clause, the mamba-org project), fetched at a pinned version
and checked by sha256, and Python 3.12 from conda-forge (Python Software Foundation License).


## Programs Crissa calls if they are there (never shipped, never installed)

Crissa runs these as separate programs when your system has them, and links none of them. Each is your system's,
under its own licence: FFmpeg (`ffmpeg`, `ffprobe`, `ffplay`: LGPL-2.1-or-later, or GPL when built with GPL parts;
Homebrew's and most Linux builds are GPL builds); PulseAudio's and PipeWire's tools (`pactl`, `paplay`, `parec`;
LGPL-2.1-or-later); Piper (above); eSpeak NG (above: the one of these Crissa installs for you when it is missing,
still never shipping it); on a Mac, `say`, `afplay`, `defaults`, `launchctl` and
`ioreg` (Apple, part of macOS); on Linux, the X tools (`xprop`, `xrandr`, `xprintidle`), GNOME's `gsettings`, `gio`,
`gdbus` and `gnome-extensions` (GPL-2.0-or-later and LGPL), systemd's `systemctl`, `systemd-run`, `loginctl` and
`busctl` (LGPL-2.1-or-later), `v4l2-ctl` (GPL-2.0), `notify-send` (libnotify, LGPL-2.1-or-later), NVIDIA's
`nvidia-smi` (NVIDIA's driver licence), and your web browser (Brave or Firefox, MPL-2.0). The render module talks to
your own ComfyUI over its local web port (ComfyUI is GPL-3.0; you install it and its models yourself, and each
model's licence is yours to check).


## Video models you bring (never shipped, never installed)

The render module's loops are off until you turn them on (`CRISSA_RENDER_LOOPS=1`), and no model, weight or workflow
ships with Crissa. Its loop recipes name two families of video models you would install in your own ComfyUI:

- Wan 2.2 (Wan-AI, https://huggingface.co/Wan-AI): Apache-2.0.
- LTX (Lightricks, https://huggingface.co/Lightricks), the loops' first recipe when they are on: Lightricks' own
  licence for its models, not an open-source licence. Read and accept it on the model's page before you download it;
  its terms, not Crissa's, govern what you make with it.


## No longer used

Each of these was part of Crissa or of how her data was made, and is gone. Nothing of it is in a pack, and no
installer of Crissa's fetches it.

- **InsightFace's face models** (detector, landmarks and recognition; the pretrained models are for non-commercial
  research only): replaced by MediaPipe for her films' head and lip tracks on 2026-09-28 and for the mouth tiles'
  head blocks on 2026-10-01; a pack's tile map keeps only the entries of tiles it ships, and the export refuses
  the model family's names. The head-pose functions adapted from InsightFace's MIT code stay (top of this file).
- **The MMS forced aligner** (`MMS_FA` in torchaudio, published under CC BY-NC 4.0): replaced by the Montreal
  Forced Aligner on 2026-09-28, for its licence; a film's word times come from MFA only.
- **Audio8 TTS as a voice**: Crissa's and the announcer's voice on a robust CPU machine from 2026-10-07 to
  2026-10-17; off since (its entry above says how to turn it back on). ARK-ASR, from the same makers, still hears.
- **Kokoro as her main voice**: since 2026-10-08 it is only Pocket TTS's backup, installed when Pocket is her
  voice (`backup_only_for` in the voice tier table), never in place of Turbo or a Mac's `say`.
- **Stock clips as her voice**: on the voices that clone, Crissa spoke with `kyutai/tts-voices` clips (Alba
  MacKenna's, then Jane's) until her own Grok clip on 2026-10-08, and Lila with Eve's until hers on 2026-10-12; on
  Pocket, Crissa spoke with a stock voice until her own Pocket voice on 2026-10-17. Those clips stay as backups.


## The MIT License (full text)

The MIT-licensed works above (InsightFace's library code, MFA, Pocket TTS's code, `chatterbox-tts`,
Chatterbox-Turbo and Multilingual, `resemble-perth`) are each under this text, with their own copyright line
as given above:

  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
  associated documentation files (the "Software"), to deal in the Software without restriction,
  including without limitation the rights to use, copy, modify, merge, publish, distribute,
  sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all copies or
  substantial portions of the Software.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
  NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
  NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
  OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
  CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


## Apache License 2.0 (full text)

The Apache-2.0 works above (MediaPipe, Kokoro-82M and its ONNX build, misaki, the out-of-dictionary models,
Audio8 TTS and tokenizers, ARK-ASR, AuraFace, Kaldi, OpenFst and Pynini) are each under this text:

                                   Apache License
                             Version 2.0, January 2004
                          http://www.apache.org/licenses/

     TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

     1. Definitions.

        "License" shall mean the terms and conditions for use, reproduction,
        and distribution as defined by Sections 1 through 9 of this document.

        "Licensor" shall mean the copyright owner or entity authorized by
        the copyright owner that is granting the License.

        "Legal Entity" shall mean the union of the acting entity and all
        other entities that control, are controlled by, or are under common
        control with that entity. For the purposes of this definition,
        "control" means (i) the power, direct or indirect, to cause the
        direction or management of such entity, whether by contract or
        otherwise, or (ii) ownership of fifty percent (50%) or more of the
        outstanding shares, or (iii) beneficial ownership of such entity.

        "You" (or "Your") shall mean an individual or Legal Entity
        exercising permissions granted by this License.

        "Source" form shall mean the preferred form for making modifications,
        including but not limited to software source code, documentation
        source, and configuration files.

        "Object" form shall mean any form resulting from mechanical
        transformation or translation of a Source form, including but
        not limited to compiled object code, generated documentation,
        and conversions to other media types.

        "Work" shall mean the work of authorship, whether in Source or
        Object form, made available under the License, as indicated by a
        copyright notice that is included in or attached to the work
        (an example is provided in the Appendix below).

        "Derivative Works" shall mean any work, whether in Source or Object
        form, that is based on (or derived from) the Work and for which the
        editorial revisions, annotations, elaborations, or other modifications
        represent, as a whole, an original work of authorship. For the purposes
        of this License, Derivative Works shall not include works that remain
        separable from, or merely link (or bind by name) to the interfaces of,
        the Work and Derivative Works thereof.

        "Contribution" shall mean any work of authorship, including
        the original version of the Work and any modifications or additions
        to that Work or Derivative Works thereof, that is intentionally
        submitted to Licensor for inclusion in the Work by the copyright owner
        or by an individual or Legal Entity authorized to submit on behalf of
        the copyright owner. For the purposes of this definition, "submitted"
        means any form of electronic, verbal, or written communication sent
        to the Licensor or its representatives, including but not limited to
        communication on electronic mailing lists, source code control systems,
        and issue tracking systems that are managed by, or on behalf of, the
        Licensor for the purpose of discussing and improving the Work, but
        excluding communication that is conspicuously marked or otherwise
        designated in writing by the copyright owner as "Not a Contribution."

        "Contributor" shall mean Licensor and any individual or Legal Entity
        on behalf of whom a Contribution has been received by Licensor and
        subsequently incorporated within the Work.

     2. Grant of Copyright License. Subject to the terms and conditions of
        this License, each Contributor hereby grants to You a perpetual,
        worldwide, non-exclusive, no-charge, royalty-free, irrevocable
        copyright license to reproduce, prepare Derivative Works of,
        publicly display, publicly perform, sublicense, and distribute the
        Work and such Derivative Works in Source or Object form.

     3. Grant of Patent License. Subject to the terms and conditions of
        this License, each Contributor hereby grants to You a perpetual,
        worldwide, non-exclusive, no-charge, royalty-free, irrevocable
        (except as stated in this section) patent license to make, have made,
        use, offer to sell, sell, import, and otherwise transfer the Work,
        where such license applies only to those patent claims licensable
        by such Contributor that are necessarily infringed by their
        Contribution(s) alone or by combination of their Contribution(s)
        with the Work to which such Contribution(s) was submitted. If You
        institute patent litigation against any entity (including a
        cross-claim or counterclaim in a lawsuit) alleging that the Work
        or a Contribution incorporated within the Work constitutes direct
        or contributory patent infringement, then any patent licenses
        granted to You under this License for that Work shall terminate
        as of the date such litigation is filed.

     4. Redistribution. You may reproduce and distribute copies of the
        Work or Derivative Works thereof in any medium, with or without
        modifications, and in Source or Object form, provided that You
        meet the following conditions:

        (a) You must give any other recipients of the Work or
            Derivative Works a copy of this License; and

        (b) You must cause any modified files to carry prominent notices
            stating that You changed the files; and

        (c) You must retain, in the Source form of any Derivative Works
            that You distribute, all copyright, patent, trademark, and
            attribution notices from the Source form of the Work,
            excluding those notices that do not pertain to any part of
            the Derivative Works; and

        (d) If the Work includes a "NOTICE" text file as part of its
            distribution, then any Derivative Works that You distribute must
            include a readable copy of the attribution notices contained
            within such NOTICE file, excluding those notices that do not
            pertain to any part of the Derivative Works, in at least one
            of the following places: within a NOTICE text file distributed
            as part of the Derivative Works; within the Source form or
            documentation, if provided along with the Derivative Works; or,
            within a display generated by the Derivative Works, if and
            wherever such third-party notices normally appear. The contents
            of the NOTICE file are for informational purposes only and
            do not modify the License. You may add Your own attribution
            notices within Derivative Works that You distribute, alongside
            or as an addendum to the NOTICE text from the Work, provided
            that such additional attribution notices cannot be construed
            as modifying the License.

        You may add Your own copyright statement to Your modifications and
        may provide additional or different license terms and conditions
        for use, reproduction, or distribution of Your modifications, or
        for any such Derivative Works as a whole, provided Your use,
        reproduction, and distribution of the Work otherwise complies with
        the conditions stated in this License.

     5. Submission of Contributions. Unless You explicitly state otherwise,
        any Contribution intentionally submitted for inclusion in the Work
        by You to the Licensor shall be under the terms and conditions of
        this License, without any additional terms or conditions.
        Notwithstanding the above, nothing herein shall supersede or modify
        the terms of any separate license agreement you may have executed
        with Licensor regarding such Contributions.

     6. Trademarks. This License does not grant permission to use the trade
        names, trademarks, service marks, or product names of the Licensor,
        except as required for reasonable and customary use in describing the
        origin of the Work and reproducing the content of the NOTICE file.

     7. Disclaimer of Warranty. Unless required by applicable law or
        agreed to in writing, Licensor provides the Work (and each
        Contributor provides its Contributions) on an "AS IS" BASIS,
        WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
        implied, including, without limitation, any warranties or conditions
        of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
        PARTICULAR PURPOSE. You are solely responsible for determining the
        appropriateness of using or redistributing the Work and assume any
        risks associated with Your exercise of permissions under this License.

     8. Limitation of Liability. In no event and under no legal theory,
        whether in tort (including negligence), contract, or otherwise,
        unless required by applicable law (such as deliberate and grossly
        negligent acts) or agreed to in writing, shall any Contributor be
        liable to You for damages, including any direct, indirect, special,
        incidental, or consequential damages of any character arising as a
        result of this License or out of the use or inability to use the
        Work (including but not limited to damages for loss of goodwill,
        work stoppage, computer failure or malfunction, or any and all
        other commercial damages or losses), even if such Contributor
        has been advised of the possibility of such damages.

     9. Accepting Warranty or Additional Liability. While redistributing
        the Work or Derivative Works thereof, You may choose to offer,
        and charge a fee for, acceptance of support, warranty, indemnity,
        or other liability obligations and/or rights consistent with this
        License. However, in accepting such obligations, You may act only
        on Your own behalf and on Your sole responsibility, not on behalf
        of any other Contributor, and only if You agree to indemnify,
        defend, and hold each Contributor harmless for any liability
        incurred by, or claims asserted against, such Contributor by reason
        of your accepting any such warranty or additional liability.

     END OF TERMS AND CONDITIONS

     APPENDIX: How to apply the Apache License to your work.

        To apply the Apache License to your work, attach the following
        boilerplate notice, with the fields enclosed by brackets "[]"
        replaced with your own identifying information. (Don't include
        the brackets!)  The text should be enclosed in the appropriate
        comment syntax for the file format. We also recommend that a
        file or class name and description of purpose be included on the
        same "printed page" as the copyright notice for easier
        identification within third-party archives.

     Copyright [yyyy] [name of copyright owner]

     Licensed under the Apache License, Version 2.0 (the "License");
     you may not use this file except in compliance with the License.
     You may obtain a copy of the License at

         http://www.apache.org/licenses/LICENSE-2.0

     Unless required by applicable law or agreed to in writing, software
     distributed under the License is distributed on an "AS IS" BASIS,
     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
     See the License for the specific language governing permissions and
     limitations under the License.


## BSD licences (full text)

python-zstandard 0.25.0 (`LICENSE`):

```
Copyright (c) 2016, Gregory Szorc
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
this list of conditions and the following disclaimer in the documentation
and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
may be used to endorse or promote products derived from this software without
specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

The Zstandard library, as bundled in python-zstandard 0.25.0 (`zstd/LICENSE`; used under this licence):

```
BSD License

For Zstandard software

Copyright (c) Meta Platforms, Inc. and affiliates. All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

 * Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

 * Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

 * Neither the name Facebook, nor Meta, nor the names of its contributors may
   be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

segno 1.6.6 (`LICENSE`):

```
Copyright (c) 2016 - 2025, Lars Heuer
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
