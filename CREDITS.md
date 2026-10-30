# Credits

## Created with Grok

Crissa was created with Grok Imagine, the image and video generator made by SpaceXAI (xAI), the makers
of Grok.

- Her appearance: Crissa's face, look and style were designed in Grok Imagine.
- Her films: every film of her in Crissa was generated with Grok Imagine, and so were the stills,
  mouth tiles and sprite sheets cut from those films, except her warm listening loop (below, Wan).
- Her voice: Crissa's own voice was created with Grok too. Her reference clip,
  `modules/voice/faces/crissa-imagine.wav` (9.2 s), is cut from the sound of two of her Grok Imagine films
  (image-to-video with sound, 2026-08-27); it is no real person's voice. The voices that can clone speak with
  it (Chatterbox-Turbo, Chatterbox Multilingual and Audio8, credited under "Her voice" below); on Pocket, which
  does not clone on your machine, she speaks with her own Pocket voice made from the same clip (below). Where it came from, with the source films' checksums
  and prompts, is in `modules/voice/faces/crissa-imagine.json` (and in FILM-PROVENANCE.json with the films).
  It is Media under MEDIA-LICENSE.md. (Any other sound in her films was made with the films in Grok Imagine.)

- Lila: Lila's look and films were created with Grok Imagine in the same way, and so was her voice. Her reference
  clip, `modules/voice/faces/lila-imagine.wav` (9.5 s), is cut from the sound of one of the house's Grok Imagine
  films (image-to-video with sound, 2026-09-17); it is no real person's voice. The voices that can clone speak her
  with it; on Pocket she speaks with its stock voice eve, whose VCTK clip is also her backup. Where it came from is
  in `modules/voice/faces/lila-imagine.json` (and in FILM-PROVENANCE.json). It is Media under MEDIA-LICENSE.md.
  Her spoken Setup films (ten welcome, room, hours and Voice ID lines) speak in that same voice: each film's sound
  was turned into her clip's voice with Chatterbox's speech-to-speech (Resemble AI, MIT; below), keeping every word
  at its time so her filmed mouth still matches. Each film's `sound` row in FILM-PROVENANCE.json says how.

Without Grok Imagine there would be no Crissa. Thank you to the Grok and Grok Imagine teams.

Grok and Grok Imagine are trademarks of SpaceXAI. This project is not affiliated with or endorsed by
SpaceXAI.

## Made with Nano Banana and Veo (Google)

Crissa on your phone (the phone module and its packs in the library download) was made with Google's models, from
Crissa's own Grok Imagine portrait:

- Her phone pictures: the plates and stills were generated with Nano Banana, Google's Gemini image model, in the
  Gemini app.
- Her phone loops: the moving loops (listen, scroll, wake, settle, multitask, typing and the flourishes) and the
  talking takes her phone mouth tiles are cut from were generated with Veo, Google's video model, through Google AI
  Studio and the Gemini app (six of her phone flourishes were made with Wan, below).

They are AI-generated. Each carries Google's invisible SynthID watermark: nothing in Crissa removes it, and the
media licence asks you not to either. Thank you to the Google DeepMind and Gemini teams.

Nano Banana, Gemini and Veo are trademarks of Google LLC. This project is not affiliated with or endorsed by Google.

## Made with Wan (Alibaba)

Her warm listening loop at the desk (`clip-listen-warm.mp4`) was generated with Wan 3.0, Alibaba's video model,
on the hosted wan.video service, from Crissa's own Grok Imagine portrait. It is AI-generated. Wan marks its downloads
with an AI-generated-content label in the file's metadata; the player's copy is re-encoded and carries no tag, and the
label of the original download is recorded word for word in FILM-PROVENANCE.json. Six of her phone flourishes (the
`crissa-phone-fl-*` packs) were made the same way with Wan 3.0, from her Nano Banana phone stills, and carry no tag
either; their labels are in the same file. Thank you to the Wan team.

This project is not affiliated with or endorsed by Alibaba.

## Thanks

Crissa stands on the work of many people who chose to share it. Some of these projects ship inside Crissa,
some made her data, and some we used or tried along the way while we made her. Thank you, all of you.

**Her mouth and her words**

- Montreal Forced Aligner, by Michael McAuliffe, Morgan Sonderegger and contributors, and its English (US)
  ARPA models, which tell her mouth where each word falls:
  https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner ·
  https://huggingface.co/MontrealCorpusTools/english_us_arpa
- The pronunciation dictionary's roots in Prosodylab-Aligner, by Kyle Gorman, Jonathan Howell and Michael Wagner.
- Kaldi and kalpy, the speech toolkit underneath it: https://github.com/kaldi-asr/kaldi ·
  https://github.com/MontrealCorpusTools/kalpy
- OpenFst and Pynini, from Kyle Gorman and the OpenGrm team: https://www.openfst.org · https://pynini.opengrm.org
- eSpeak NG, by Jonathan Duddington, Reece H. Dunn and contributors (GPL-3.0-or-later), which turned spelling
  into sounds when her mouth data was made, and which her mouth asks for a word's sounds. It is not part of Crissa and
  no pack carries it; when your machine lacks it, Crissa installs it at start from your system's packages or the
  upstream build (round 40), never shipping it:
  https://github.com/espeak-ng/espeak-ng
- "Comma Gets a Cure", by Douglas N. Honorof, Jill McCullough and Barbara Somerville, a passage we used to test
  speech: https://www.dialectsarchive.com/comma-gets-a-cure

**Her face**

- MediaPipe, by Google, which finds her face in every frame: https://github.com/google-ai-edge/mediapipe
- InsightFace, by Jiankang Deng, Jia Guo and contributors, for the head-pose math we adapted:
  https://github.com/deepinsight/insightface
- face3d, by Yao Feng, where that math began: https://github.com/YadiraF/face3d
- AuraFace, by fal, and dlib, by Davis King, which we tested for recognising faces:
  https://huggingface.co/fal/AuraFace-v1 · https://github.com/davisking/dlib

**Her voice**

- Kokoro-82M, by hexgrad (Apache-2.0), her voice on any other machine, in the ONNX build by onnx-community:
  https://huggingface.co/hexgrad/Kokoro-82M · https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX.
  Its training audio includes the Koniwa corpus by tnc (CC BY 3.0) and the SIWIS French speech corpus
  (CC BY 4.0).
- misaki, by hexgrad, which turns her words into sounds for Kokoro, its word lists included (Apache-2.0):
  https://github.com/hexgrad/misaki
- graphemes_to_phonemes_en_us and graphemes_to_phonemes_en_gb, by Peter Reid, for words misaki does not know
  (Apache-2.0): https://huggingface.co/PeterReid
- spaCy and its en_core_web_sm model, by Explosion (MIT), which misaki reads sentences with: https://spacy.io
- Voice: Pocket TTS by Kyutai (kyutai.org), weights CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/;
  code MIT). Her voice on the CPU, Macs included, fetched on your machine at install and checked by sha256:
  https://github.com/kyutai-labs/pocket-tts · https://huggingface.co/kyutai/pocket-tts-without-voice-cloning.
  Nothing is cloned on your machine. Crissa's own Pocket voice ships made: her Grok clip run once through Pocket's
  voice export with Kyutai's gated weights, whose terms we accepted (`modules/voice/faces/crissa-pocket.safetensors`;
  credit: Created with Grok; voice state made with Pocket TTS by Kyutai, weights CC BY 4.0). The kit needs no Hugging
  Face login for it. The stock voices Crissa may name, from
  https://huggingface.co/kyutai/tts-voices:
  - alba: voiced by Alba MacKenna (CC BY 4.0)
  - anna, azelma, charles, eponine, eve, fantine, george, jane, mary, michael, paul, vera: speakers of the VCTK
    corpus, CSTR, University of Edinburgh (CC BY 4.0)
  - marius, javert: volunteers of the Unmute Voice Donation Project (CC0)
  - caro_davy, peter_yearsley, stuart_bell: LibriVox readers, from Voice-Zero (CC0)
  Not used: cosette (Expresso) and jean (EARS), which are CC BY-NC 4.0, non-commercial only.
  In its other languages (French, German, Spanish, Italian, Portuguese, Dutch) alba, eve, anna and mary speak
  that language, and so may these native voices, checked 2026-10-06:
  - estelle (French): Kyutai's own recording (CC0)
  - lola (Spanish), giovanni (Italian): Mozilla Common Voice contributors (CC0)
  - daan (Dutch): the CML-TTS corpus, from LibriVox (CC BY 4.0)
  Not used: juergen (German) and rafael (Portuguese), whose source and licence are not published.
- Voice: Chatterbox-Turbo by Resemble AI (code and weights MIT), her voice on a machine with an NVIDIA GPU, an
  opt-in install fetched on your machine and checked by sha256: https://github.com/resemble-ai/chatterbox ·
  https://huggingface.co/ResembleAI/chatterbox-turbo. Every clip it speaks carries Resemble's Perth watermark
  (resemble-perth, MIT), as upstream ships it; it is on by default and `turbo.settings.watermark` in the voice tier
  table turns it off. Crissa and Lila speak from their own clips, created with Grok (above), which ship with Crissa.
  The announcer speaks from a stock clip fetched on your machine at a pinned revision of
  https://huggingface.co/kyutai/tts-voices and checked by sha256 (VCTK speakers, CSTR, University of Edinburgh,
  CC BY 4.0; Jane, VCTK p339, is Crissa's backup clip and Eve, VCTK p361, Lila's), or from a clip of your own that you
  have the rights to; if a clip cannot be had, the face speaks with Chatterbox's built-in voice.
- Voice: Chatterbox Multilingual V3 by Resemble AI (code and weights MIT), her voice in 23 languages on a machine
  with an NVIDIA GPU when your language is not English, an opt-in install fetched on your machine and checked by
  sha256: https://huggingface.co/ResembleAI/chatterbox. Perth-watermarked like Turbo, with the same clips.
- Voice: Chatterbox-Turbo on Apple Silicon, in the MLX builds by mlx-community (chatterbox-turbo-4bit and -8bit,
  conversion Apache-2.0; base model ResembleAI/chatterbox-turbo, MIT): an opt-in Init offers on Macs with 16 GB of
  unified memory, not yet verified on Apple Silicon hardware. mlx-audio does not apply the Perth watermark for
  Chatterbox.
- Announcer: Audio8 TTS by Edge0 (formerly Audio8-AI), code and weights Apache-2.0, the announcer's voice on a
  machine with the memory and CPU cores for it (16 GB and 8 cores), fetched on your machine and checked by sha256:
  https://github.com/Edge0-AI/Audio8_TTS · https://huggingface.co/Edge0/Audio8-TTS-Preview-0.6B-ONNX-INT4. Its
  architecture follows the DualAR design of Fish Audio S2 Pro (its NOTICE). It is Crissa's voice there too: it
  registers her own clip (created with Grok, above) on your machine as crissa_imagine, with jane (VCTK p339,
  CC BY 4.0) as her backup, and Lila's as lila_imagine, with eve (VCTK p361, CC BY 4.0) as hers. The announcer speaks with stock VCTK voices (anna, CC BY 4.0; if you choose a second,
  every-other-day voice, vctk_p283, CC BY 4.0) registered on your machine; when it is not there, Pocket, then
  Kokoro, says the announcer's lines.
- num2words, by Taro Ogawa and contributors, which says numbers as words (LGPL-2.1, installed unmodified
  from PyPI, never shipped with Crissa): https://github.com/savoirfairelinux/num2words
- Qwen3-TTS, by the Qwen team at Alibaba, which we used and tested for voices:
  https://github.com/QwenLM/Qwen3-TTS
- Piper, from the Open Home Foundation, for speech on any machine: https://github.com/OHF-Voice/piper1-gpl
- Fish Audio, for publishing the DualAR idea that newer voices build on: https://github.com/fishaudio/fish-speech

**Her ears**

- ARK-ASR, by AutoArk, and its open training recipe, which hears what you say:
  https://huggingface.co/AutoArk-AI/ARK-ASR-0.6B · https://github.com/AutoArk/open-audio-opd
- Whisper, by OpenAI, and faster-whisper, by SYSTRAN: https://github.com/openai/whisper ·
  https://github.com/SYSTRAN/faster-whisper
- Silero VAD and py-webrtcvad (with the WebRTC project's voice detector), which hear when you start and stop:
  https://github.com/snakers4/silero-vad · https://github.com/wiseman/py-webrtcvad

**The agents she talks with**

- Hermes Agent, by Nous Research, the agent framework Crissa grew up alongside: https://github.com/NousResearch/hermes-agent
- The Model Context Protocol, which lets any agent reach her: https://modelcontextprotocol.io
- Claude Code, by Anthropic, which helped write much of this: https://www.anthropic.com/claude-code
- vLLM, which served the language models she was made with: https://github.com/vllm-project/vllm

**The ground under all of it**

- Python, NumPy, OpenCV, Pillow, ONNX Runtime, PyTorch, Hugging Face Transformers, librosa,
  python-sounddevice, websocket-client, python-zstandard (with Meta's Zstandard) and FFmpeg: https://numpy.org ·
  https://github.com/opencv/opencv · https://github.com/python-pillow/Pillow · https://onnxruntime.ai ·
  https://pytorch.org · https://github.com/huggingface/transformers · https://librosa.org ·
  https://github.com/spatialaudio/python-sounddevice · https://github.com/websocket-client/websocket-client ·
  https://github.com/indygreg/python-zstandard · https://ffmpeg.org
- segno, by Lars Heuer (BSD-3-Clause), which draws the QR code on the Your phone card:
  https://github.com/heuer/segno
- ComfyUI, by comfyanonymous and contributors (GPL-3.0), which the render module talks to when you run your own:
  https://github.com/comfyanonymous/ComfyUI
- conda-forge, mamba and Hugging Face, which carry the models and packages to every machine:
  https://conda-forge.org · https://github.com/mamba-org/mamba · https://huggingface.co

**No longer used, still thanked**: InsightFace's face models (until 2026-09-28, MediaPipe since), the MMS forced
aligner (until 2026-09-28, MFA since). What each was and why it went is under "No longer used" in
THIRD-PARTY-NOTICES.md.

Licences and required notices are in LICENSE, MEDIA-LICENSE.md and THIRD-PARTY-NOTICES.md, not here.
