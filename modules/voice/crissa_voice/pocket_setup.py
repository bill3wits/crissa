"""The voice module's Pocket TTS, installed on first use (2026-10-06; the User: the voice for lighter machines and Macs).

Kyutai Pocket TTS (100M parameters; code MIT, weights CC BY 4.0) speaks on the CPU, Apple Silicon included: about
1.2 GiB of memory, about 0.08 s to first sound and RTF about 0.3 on 3 threads (measured on the reference house). It is
the tier between the GPU voice (Turbo) and Kokoro (lib/crissa_voice_tiers).

    python3 modules/voice/scripts/crissa-pocket.py --install    # any Python 3: the env gets its own (pyenv.py)
    python3 modules/voice/scripts/crissa-pocket.py [--port 8188]

STOCK VOICES ONLY. Packs speak with Pocket's own stock voices (VOICES below: CC BY 4.0 or CC0 each, checked against
kyutai/tts-voices). This module never clones: it loads no reference clip and refuses any voice not in VOICES.
Cloning needs the gated kyutai/pocket-tts weights, which only a person can unlock with their own Hugging Face login
after accepting Kyutai's terms; Crissa never asks for that login and never uses those weights.

Fetched on the user's own machine, nothing redistributed:

    <crissa home>/pocket/env     the env (pyenv.py: micromamba, Python pinned); every package from locks/pocket-<target>-py312.txt (sha256-pinned)
    <crissa home>/pocket/model   kyutai/pocket-tts-without-voice-cloning at pinned revisions (the English model,
                                 its tokenizer and the allowed voices' embeddings), each file checked by sha256

No phonemizer and no espeak: Pocket reads text with its own tokenizer. Credit (CREDITS.md): "Voice: Pocket TTS by
Kyutai (kyutai.org), weights CC BY 4.0", and each stock voice's own line.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import egress, locks, pyenv


def _home() -> Path:
    env = os.environ.get("CRISSA_HOME")
    return Path(env).expanduser() if env else Path.home() / ".crissa"


ROOT = _home() / "pocket"
ENV = ROOT / "env"
MODEL = ROOT / "model"
DONE = ROOT / "installed.json"
REPO = "kyutai/pocket-tts-without-voice-cloning"
PACKAGE = "pocket-tts==3.3.0"
TORCH = "torch==2.14.1"
CREDIT = "Voice: Pocket TTS by Kyutai (kyutai.org), weights CC BY 4.0"
MODEL_FILES = {   # path in the repo at its revision -> (revision, sha256, bytes)
    "languages/english/model.safetensors": ("e7205b6ee50e654a5ea19f0e9df2b0813b05e921",
        "916ccd2686e9311cb40054893a3c4284393d658825ffc714a276f3e9b152344f", 219029196),
    "languages/english/tokenizer.json": ("00eac05ed3d16bdc3f6b5d598874019c34a89214",
        "f498428e1eafee50492f7be13dc9bfafcfc12e508cd0eb1b01c92ecd5d8c6687", 245020),
}
VOICE_REV = "4e1e0a3e611c51c0b4ed8174fc10f32a54644303"
# The stock voices a pack may name: (who recorded it, licence, sha256 of its embedding, bytes). Checked 2026-10-06
# against kyutai/tts-voices (the per-voice licences): CC BY 4.0 or CC0 only.
VOICES = {
    "alba": ("Alba MacKenna (kyutai/tts-voices alba-mackenna)", "CC BY 4.0",
               "d291428b416d6c36a1de7835e51dbe1e334b75e5af512bb18dd23a1047fe8f3b", 6195000),
    "marius": ("Unmute Voice Donation Project (kyutai/tts-voices voice-donations)", "CC0",
               "b05946f39371f8853902b0486231f2d401c8a9570da9978329120d4ecfd009e9", 6195000),
    "javert": ("Unmute Voice Donation Project (kyutai/tts-voices voice-donations)", "CC0",
               "580e4b72d4065e906c14e580f97da478c9707bead74b0ef9c049e0ca8de997d9", 6195000),
    "anna": ("VCTK corpus, CSTR, University of Edinburgh (kyutai/tts-voices vctk)", "CC BY 4.0",
               "80b3c36dc29aa1acd09ed522099142c5236789c8c5e87b2a30523175772a531c", 7817016),
    "vera": ("VCTK corpus, CSTR, University of Edinburgh (kyutai/tts-voices vctk)", "CC BY 4.0",
               "3a9d4a9237d8615b5c3d10610b16a3037edb163cc18df42f138bfbc1f2beb7a3", 6735672),
    "fantine": ("VCTK corpus, CSTR, University of Edinburgh (kyutai/tts-voices vctk)", "CC BY 4.0",
               "8b6a1c253d39c701e7b70e349796d10b4ce6f35bc58254f0e0caa572f1d41464", 6539064),
    "charles": ("VCTK corpus, CSTR, University of Edinburgh (kyutai/tts-voices vctk)", "CC BY 4.0",
               "f6c03e29e9b17708f24ef3aaab0af92d9c11565c0997ee8c8271cacc72cfe53a", 6195000),
    "paul": ("VCTK corpus, CSTR, University of Edinburgh (kyutai/tts-voices vctk)", "CC BY 4.0",
               "400ea75cecbe796e6adab3526264acd7ca3f3cb39f9785f531f194bdc3b26007", 6981432),
    "eponine": ("VCTK corpus, CSTR, University of Edinburgh (kyutai/tts-voices vctk)", "CC BY 4.0",
               "0bcc4878ad3183ef8bfdbaf9224c5a71f1d18fe944fb494818c37e1f8ea6c7d0", 6932280),
    "azelma": ("VCTK corpus, CSTR, University of Edinburgh (kyutai/tts-voices vctk)", "CC BY 4.0",
               "1592ba7eb6784302ccaa8d48a7304f14f6fd7503969fb0466efcab607827477c", 7964472),
    "george": ("VCTK corpus, CSTR, University of Edinburgh (kyutai/tts-voices vctk)", "CC BY 4.0",
               "9cee0daccb0eda1631b73f8b35f6f8cb195885d55d2e1e20fc711f9e4456ceb0", 6244152),
    "mary": ("VCTK corpus, CSTR, University of Edinburgh (kyutai/tts-voices vctk)", "CC BY 4.0",
               "8eaed3701d6ba5602393b50876f6639d3ed5d7aacc356a3ee6337957a7cbe9d2", 6195000),
    "jane": ("VCTK corpus, CSTR, University of Edinburgh (kyutai/tts-voices vctk)", "CC BY 4.0",
               "fe0e73c3a01f9d3b344ce19c912654df1464cb4077393e188d54f6cff990c05f", 7374648),
    "michael": ("VCTK corpus, CSTR, University of Edinburgh (kyutai/tts-voices vctk)", "CC BY 4.0",
               "401711f60394aa6085627f7050c1b3f97b31aa7138784811bc6c6ec7d7eaad0c", 7276344),
    "eve": ("VCTK corpus, CSTR, University of Edinburgh (kyutai/tts-voices vctk)", "CC BY 4.0",
               "ec2bf13d4ac4bcee200ee7aec1deae8132bb5bd60233f815e12a4d8efe56b8c6", 6539064),
    "peter_yearsley": ("Voice-Zero, from LibriVox (kyutai/tts-voices voice-zero)", "CC0",
               "bbaf032c5887c5620f46e3d9dd213d97c9d3277a687d2944107eaa535e1d3483", 3737392),
    "stuart_bell": ("Voice-Zero, from LibriVox (kyutai/tts-voices voice-zero)", "CC0",
               "c560ae214a3f5e52648839a01222da245b82d834dc02f611a04154319ad1434f", 5261112),
    "caro_davy": ("Voice-Zero, from LibriVox (kyutai/tts-voices voice-zero)", "CC0",
               "6361bef0343a04a1c9947d624c21b1ee3d0f430a332a50c2f32a992e55b2922b", 5261112),
}
# Not allowed in packs (checked 2026-10-06): cosette (Expresso) and jean (EARS) are CC BY-NC 4.0, non-commercial
# only. One Voice-Zero voice (CC0) is left out only because its name trips the export's household-name check; the
# other languages' voices are not checked here.
EXCLUDED = {"cosette": "CC BY-NC 4.0 (Expresso): non-commercial only", "jean": "CC BY-NC 4.0 (EARS): non-commercial only",
            # the User 2026-10-06: a native voice whose licence cannot be verified stays out, and that language's
            # faces speak with the stock voices above (alba, eve, anna, mary have embeddings in every language)
            "juergen": "German native voice: source file de-DE-juergen.mp3, no source or licence published; not verified",
            "rafael": "Portuguese native voice: source g-Vi8PgmSY0-enhanced-v2.wav (a video id), no licence published; "
                      "not verified"}
# Pocket's languages beyond English (the model card names en, fr, de, es, it, pt; dutch ships in pocket-tts 3.3.0 and
# its repo but is undocumented). Each language has its own model, tokenizer and embeddings, fetched only when Init's
# language is one of them.
LANG_DIRS = {"en": "english", "fr": "french", "de": "german", "es": "spanish", "it": "italian", "pt": "portuguese",
             "nl": "dutch"}
# Native stock voices of a language, verified 2026-10-06 (who recorded it, licence, language):
NATIVE_VOICES = {
    "estelle": ("Kyutai's own recording (unmute project voices)", "CC0", "fr"),
    "lola": ("Mozilla Common Voice", "CC0", "es"),
    "giovanni": ("Mozilla Common Voice", "CC0", "it"),
    "daan": ("CML-TTS (Multilingual LibriVox) corpus", "CC BY 4.0", "nl"),
}
FACE_VOICES = ("alba", "eve", "anna", "mary")   # the voices every language carries (crissa: mary there)
LANGUAGE_FILES = {   # Init's language -> {path in the repo: (revision, sha256, bytes)}; checked 2026-10-06
    "french": {
        "languages/french/model.safetensors": ("8843db76457a91db32077edf8dfcd1c0e3e755fd",
            "32e575398f06a5dbca16a7dfb8048e3cd3440502d80b07089adfb3318f79dec2", 219029196),
        "languages/french/tokenizer.json": ("8843db76457a91db32077edf8dfcd1c0e3e755fd",
            "6211acffe9cb95172f71f0051f18e26ea305b2b32ea00b331ee976551c70887f", 246403),
        "languages/french/embeddings/alba.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "4c80b1438668290542068bbc9515abd2c267f4cc86764c6633fcde395d1b2681", 6244152),
        "languages/french/embeddings/eve.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "b642a43b786fb60571656814cc66f9c24f8a457ea9f727903ea97d647d5ceedb", 6145848),
        "languages/french/embeddings/anna.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "7a7fae31e4b0e16102cb2514f96189cdb57c8403f5f7aba2b37aa942e2d03488", 7620408),
        "languages/french/embeddings/mary.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "8d5d4f03bce7660da0ea81d96f8a0ea0913685b21fbbbd7bba714242822368dc", 5949240),
        "languages/french/embeddings/estelle.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "d9efd55657f74ba9483caec23f9fd1d155e121b058feef86af3760d54ea8aa15", 7571256),
    },
    "spanish": {
        "languages/spanish/model.safetensors": ("4e1e0a3e611c51c0b4ed8174fc10f32a54644303",
            "cbb0606d5d6976147f3343960b195ff94fde1f952e13ab6d9e097741ce5091c7", 219029196),
        "languages/spanish/tokenizer.json": ("4e1e0a3e611c51c0b4ed8174fc10f32a54644303",
            "9ade09bbdc64ab2b59952b90773fa0890b780ece4fd5aef0d36a00c6ad5f0531", 246610),
        "languages/spanish/embeddings/alba.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "3054baa9d54a47667ff333a95f4d3ed9dc7d31ada1fe99cee334d2bb6f1a5029", 6244152),
        "languages/spanish/embeddings/eve.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "034a2fb66ce02cb19a246f64d0f5a1c4a3a04688b067fa6ba3c7e61b5c15dab0", 6145848),
        "languages/spanish/embeddings/anna.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "1a8b159d01e983321c8be53b9acfcf66fe069ac9b03e5432043972ebc8bd36f3", 7620408),
        "languages/spanish/embeddings/mary.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "b2175122cc79a6e54ff0b347bf6f942f59e6d50c20d8e60ad0baa708e030164f", 5949240),
        "languages/spanish/embeddings/lola.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "967b8f08639345ff5ad1a912a5542fe421518c3f80b87d52078c6726348ab1e7", 5457720),
    },
    "italian": {
        "languages/italian/model.safetensors": ("4e1e0a3e611c51c0b4ed8174fc10f32a54644303",
            "6af958c43372082b91af79d98d07d3fa3c2a2e41b628e5b119fdb46e19f0e49f", 219029196),
        "languages/italian/tokenizer.json": ("4e1e0a3e611c51c0b4ed8174fc10f32a54644303",
            "4bc1c0d4d54f9480183e94c94cfabf74fd89b0912537d4ef607d1982c4a381ec", 246586),
        "languages/italian/embeddings/alba.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "259cc88884106f291c7bbff3139da1cc55bd1cdf57e7c988dcbd35818b35e105", 6244152),
        "languages/italian/embeddings/eve.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "19bf7611b446437d8b134060f2f8256739f4c14b07dd289c531edc6811a4d330", 6145848),
        "languages/italian/embeddings/anna.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "d980145802244a61136c4221b3b9a97935ce914e23c18c866df9446b77b8dbde", 7620408),
        "languages/italian/embeddings/mary.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "1204c4898ccc02a91f9d1d560d99ff12617be985313583202c4e49c4cf0386be", 5949240),
        "languages/italian/embeddings/giovanni.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "5cede7852c7076f611750e4a8d3c95d19c6386852abc55940b3279f775fc5f9b", 4130608),
    },
    "dutch": {
        "languages/dutch/model.safetensors": ("4e1e0a3e611c51c0b4ed8174fc10f32a54644303",
            "0de1e5b074458de1330a264d0de39649a51eedf001cc30d181b80c7b0d00e61d", 219029196),
        "languages/dutch/tokenizer.json": ("4e1e0a3e611c51c0b4ed8174fc10f32a54644303",
            "e6f610b98c427641b4ab0e1024dca7843e3d6b620871a14b204932e42c5a1736", 245206),
        "languages/dutch/embeddings/alba.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "97382de7d9265cd3cbeb54dab54dd8b3db8e24bc12bdc540ac971be10f5e161a", 6244152),
        "languages/dutch/embeddings/eve.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "62ee832066e869fa890baa300270b3dcdd2d21e0e3aeba7da9335a3f61d0d7c9", 6145848),
        "languages/dutch/embeddings/anna.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "ed10d118e1f2518b035ba01e8c2ed587cd4e0463c391038a067414f85d530494", 7620408),
        "languages/dutch/embeddings/mary.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "735421530482aa9376185e2e303af8d50d58595e90f98d0788c0d910f8e60bcd", 5949240),
        "languages/dutch/embeddings/daan.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "be6b053e504c8345a9832c1e3676f539304a2921c416b7140ec5ebb5f95016c4", 3884848),
    },
    "german": {
        "languages/german/model.safetensors": ("4e1e0a3e611c51c0b4ed8174fc10f32a54644303",
            "d2fabbc2c383d30ee29fcd374f8281406cc501b1d963fe687691f8573de3252b", 219029196),
        "languages/german/tokenizer.json": ("4e1e0a3e611c51c0b4ed8174fc10f32a54644303",
            "2d77849811e3d1b6ae80e5b11b203f57e1847901104d8a0573b34f46f8dddb53", 245966),
        "languages/german/embeddings/alba.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "e34da39d613abe74a8a255426c816738372f085a83604c0c441845d7ae6c8192", 6244152),
        "languages/german/embeddings/eve.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "de30237de9ab96251ca7d73746f1ecc8184b91b8f025f8c06096f7a33afe7bbf", 6145848),
        "languages/german/embeddings/anna.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "54e036b70a09478ed11a5108c1e651c6d59ddd63db8191ad9b8264c0cc0d677e", 7620408),
        "languages/german/embeddings/mary.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "aa2f706eeed7ff410778b3e23070b8f6dd27c27460b76cdd1baef4f7d7d61da5", 5949240),
    },
    "portuguese": {
        "languages/portuguese/model.safetensors": ("4e1e0a3e611c51c0b4ed8174fc10f32a54644303",
            "982369b35c9fce8e8beb027f76c5d15dc0936851d8cb66f75d9678e0e6772b41", 219029196),
        "languages/portuguese/tokenizer.json": ("4e1e0a3e611c51c0b4ed8174fc10f32a54644303",
            "b33e269f7f042a763080d71ba087d74d2f6759674b238a723c028e436b1a25ab", 247014),
        "languages/portuguese/embeddings/alba.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "494d52b7f3b91ed4ff3dec90b44a7f985194485cf5c46cf903867ae8fdc6d2aa", 6244152),
        "languages/portuguese/embeddings/eve.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "86840ca3163b6050212ff37e9048575581aff75b74fb40d74ae2f52e6366fc7a", 6145848),
        "languages/portuguese/embeddings/anna.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "6933387edee7aee630834fe2e8bd40a4bf73b5e364da997388fd5de1cb8f5b5d", 7620408),
        "languages/portuguese/embeddings/mary.safetensors": ("1e08e6a23401048648a9fdcfde2f89348215c2a7",
            "148e07e6297293af34ea09bcbe2039eb703060c6650122f7de1dd116d4a94226", 5949240),
    },
}
# CRISSA IS NEVER A MAN'S VOICE (2026-10-07, .101: once its sound tools were installed she spoke, but in a man's voice).
# Her stock voice was alba, and alba (made from Alba MacKenna's "casual" clip) speaks low: a test line measured a median
# pitch of 135 Hz on Pocket and the clip itself 136 Hz, with the men's voices (paul 113, george 128) and well under every
# woman's voice here (vera 176 to azelma 254). alba stays an allowed stock voice anyone can name; it is no longer a
# face's voice or the default. FEMALE is the stock voices measured that day as a woman's voice (median pitch of the same
# test line on Pocket, all CC BY 4.0 VCTK or Voice-Zero CC0); a female face (SHE_FACES) speaks only with one of them.
# HER OWN VOICE SHIPS IN THE PACK (round 28, the User 2026-10-17: "our imagine voice must stick ... that is the TM and our
# brand"): a voice state made from her Grok Imagine clip with Pocket's own export (modules/voice/faces/crissa-pocket.json),
# loaded by the no-cloning model like a stock voice - nothing is cloned on the machine and nothing is downloaded. English
# only (its state is the English model's). {name: (pack file, sha256, bytes)}.
FACES_DIR = Path(__file__).resolve().parents[1] / "faces"
OWN_VOICES = {"crissa": (FACES_DIR / "crissa-pocket.safetensors",
                         "012eb0e3fbc0e67560dcacc52ca2be3afef11a2004b01d36c3f2e90e58d3698f", 5703480)}
def _house_voices() -> dict:
    """HOUSE-ONLY extra voice states (the User 2026-10-21: "let's clone from my audio8 and use pocket for it"): a JSON
    file named by CRISSA_POCKET_HOUSE_VOICES, {name: [path, sha256, bytes]}, checked like her own voice. Unset (every
    pack) = nothing added; a bad file adds nothing. These voices never ship: the file and the states live outside
    the repo."""
    path = os.environ.get("CRISSA_POCKET_HOUSE_VOICES", "").strip()
    if not path:
        return {}
    try:
        raw = json.loads(Path(path).read_text())
        return {str(k).lower(): (Path(v[0]), str(v[1]), int(v[2])) for k, v in raw.items()
                if not str(k).startswith("_") and str(k).lower() not in OWN_VOICES}
    except (OSError, ValueError, TypeError, IndexError, KeyError):
        return {}


HOUSE_VOICES = _house_voices()
OWN_VOICES.update(HOUSE_VOICES)
FEMALE = ("anna", "azelma", "caro_davy", "crissa", "eponine", "eve", "fantine", "jane", "mary", "vera") + tuple(HOUSE_VOICES)
SHE_FACES = ("crissa", "lila", "default")
DEFAULT_VOICE = "eve"         # VCTK p361 (F). Crissa's Pocket voice (the User 2026-10-09). eve is in every language.
LANG_DEFAULT_VOICE = "mary"   # used only if eve is missing from that language's set


def default_voice(code: str | None = None) -> str:
    """The voice an unknown name (and a female face whose own pick cannot speak here) gets: eve, else mary."""
    return DEFAULT_VOICE if DEFAULT_VOICE in (set(voices(code)) if code and code != "en" else set(VOICES)) \
        else LANG_DEFAULT_VOICE
ESPEAK_DISTS = ("espeakng-loader", "espeakng_loader", "phonemizer", "phonemizer-fork", "piper-phonemize")
MIN_MACOS = (14, 0)   # torch 2.14's Mac wheels


def env_python() -> Path:
    return ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def model_path(rel: str) -> Path:
    return MODEL / rel


def lang_dir(code: str | None = None) -> str:
    return LANG_DIRS.get((code or "en").lower(), "english")


def language_code() -> str:
    """Init's language for Pocket ("en" when Pocket does not speak it, or Init has not run)."""
    code = (os.environ.get("CRISSA_POCKET_LANGUAGE") or "").strip().lower()
    if not code:
        try:
            lib = Path(__file__).resolve().parents[3] / "lib"
            if str(lib) not in sys.path:
                sys.path.insert(0, str(lib))
            import crissa_voice_tiers as VT  # noqa: PLC0415
            code = str((((VT.read() or {}).get("facts") or {}).get("language") or {}).get("code") or "en")
        except Exception:  # noqa: BLE001
            code = "en"
    return code if code in LANG_DIRS else "en"


def voices(code: str | None = None) -> list[str]:
    """The stock voices Pocket may name in a language: every English one, else the four in every language plus the
    language's verified native voice."""
    if (code or "en") == "en":
        return sorted(set(VOICES) | set(OWN_VOICES))
    rel = LANGUAGE_FILES.get(lang_dir(code)) or {}
    have = {Path(r).stem for r in rel if "/embeddings/" in r}
    return sorted(v for v in have if v in FACE_VOICES or (v in NATIVE_VOICES and NATIVE_VOICES[v][2] == code))


def voice_path(name: str, code: str | None = None) -> Path:
    if name in OWN_VOICES and (code or "en") == "en":
        return OWN_VOICES[name][0]                # the pack's own file, checked by own_voice_ok()
    return MODEL / "languages" / lang_dir(code) / "embeddings" / (name + ".safetensors")


def installed() -> bool:
    if not (DONE.is_file() and env_python().exists()):
        return False
    files = [(model_path(r), n) for r, (_, _, n) in MODEL_FILES.items()]
    files += [(voice_path(v), n) for v, (_, _, _, n) in VOICES.items()]
    files += [(p, n) for p, _, n in OWN_VOICES.values()]
    try:
        langs = json.loads(DONE.read_text()).get("languages") or []
    except (OSError, ValueError):
        langs = []
    for code in langs:
        files += [(model_path(r), n) for r, (_, _, n) in (LANGUAGE_FILES.get(lang_dir(code)) or {}).items()]
    return all(p.is_file() and p.stat().st_size == n for p, n in files)


def _macos_version() -> tuple:
    try:
        return tuple(int(x) for x in platform.mac_ver()[0].split(".")[:2])
    except ValueError:
        return ()


def unsupported(version=None, plat: str | None = None, machine: str | None = None, mac_ver=None) -> str:
    """'' when this machine and Python can install Pocket, else exactly why not, said before anything downloads."""
    v = tuple(version or pyenv.target_version())
    p = plat or sys.platform
    m = (machine or platform.machine()).lower()
    if not ((3, 10) <= v <= (3, 12)):
        return ("Pocket needs Python 3.10 to 3.12 (its env would run %d.%d). %s" % (v[0], v[1], pyenv.ADVICE))
    if p.startswith("win"):
        return "Pocket's installer is for Linux and macOS; on Windows, run it under WSL2."
    if p == "darwin":
        if m not in ("arm64", "aarch64"):
            return "Pocket needs an Apple Silicon Mac (torch has no Intel Mac build); `say` will speak."
        mv = tuple(mac_ver) if mac_ver is not None else _macos_version()
        if mv and mv < MIN_MACOS:
            return ("Pocket needs macOS %d or later (torch's Mac build); this Mac runs %s. Kokoro or `say` will "
                    "speak." % (MIN_MACOS[0], ".".join(map(str, mv))))
    return ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(url: str, dest: Path, timeout: float = 600.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with egress.urlopen(url, timeout, "the Pocket download") as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 22)
    os.replace(tmp, dest)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(cmd[:4]), (r.stderr or r.stdout)[-400:]))
    return r


def wanted(code: str | None = None) -> dict:
    """{local path: (url, sha256)} for every file Pocket loads: the model, its tokenizer, the allowed voices; and,
    for a language other than English, that language's model, tokenizer and allowed voices."""
    out = {}
    for rel, (rev, sha, _) in MODEL_FILES.items():
        out[model_path(rel)] = ("https://huggingface.co/%s/resolve/%s/%s" % (REPO, rev, rel), sha)
    for name, (_, _, sha, _) in VOICES.items():
        rel = "languages/english/embeddings/%s.safetensors" % name
        out[voice_path(name)] = ("https://huggingface.co/%s/resolve/%s/%s" % (REPO, VOICE_REV, rel), sha)
    if code and code != "en":
        keep = set(voices(code))
        for rel, (rev, sha, _) in (LANGUAGE_FILES.get(lang_dir(code)) or {}).items():
            if "/embeddings/" in rel and Path(rel).stem not in keep:
                continue
            out[model_path(rel)] = ("https://huggingface.co/%s/resolve/%s/%s" % (REPO, rev, rel), sha)
    return out


def get_model(fetch=_fetch, log=print, code: str | None = None) -> None:
    """Each file at its pinned revision, checked by sha256. A file already there with the right hash is kept; a
    wrong download stops the install and is removed."""
    for p, (url, sha) in wanted(code).items():
        if p.is_file() and _sha256(p) == sha:
            continue
        log("pocket: fetching %s …" % p.relative_to(MODEL))
        fetch(url, p)
        got = _sha256(p)
        if got != sha:
            p.unlink()
            raise RuntimeError("%s does not match its pinned sha256 (got %s); nothing was installed" % (url, got[:16]))


def make_env(run=_run, log=print) -> str:
    log("pocket: " + pyenv.make(ENV, run, log))
    idx = (["--index-url", locks.TORCH_INDEX["cpu"], "--extra-index-url", locks.PYPI]
           if sys.platform.startswith("linux") else [])
    return locks.pip_install(env_python(), "pocket", run, log, fallback=[idx + [TORCH], [PACKAGE]],
                             version=pyenv.target_version())


def check_no_espeak(run=_run) -> None:
    r = run([str(env_python()), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"])
    names = {str(d.get("name", "")).lower().replace("_", "-") for d in json.loads(r.stdout or "[]")}
    bad = sorted(n for n in names if n in {e.replace("_", "-") for e in ESPEAK_DISTS})
    if bad:
        raise RuntimeError("the Pocket env holds %s (GPL); remove it or rebuild %s" % (", ".join(bad), ENV))


def install(log=print) -> dict:
    why = unsupported()
    if why:
        raise RuntimeError(why)
    egress.refuse_install("the Pocket voice")
    ROOT.mkdir(parents=True, exist_ok=True)
    log("pocket: building the Python env (torch for the CPU, %s; no espeak) …" % PACKAGE)
    how = make_env(log=log)
    check_no_espeak()
    code = language_code()
    log("pocket: fetching the model and %d stock voices from %s (about 0.33 GB) …" % (len(VOICES), REPO))
    if code != "en":
        log("pocket: and the %s model with its voices %s (about 0.25 GB more)" % (lang_dir(code), ", ".join(voices(code))))
    get_model(log=log, code=code)
    rec = {"package": PACKAGE, "repo": REPO, "model": {r: v[0] for r, v in MODEL_FILES.items()},
           "voices": sorted(VOICES), "voice_rev": VOICE_REV, "installed_by": how, "credit": CREDIT,
           "languages": [] if code == "en" else [code], "language": code,
           "language_voices": {} if code == "en" else {code: voices(code)}}
    DONE.write_text(json.dumps(rec) + "\n")
    log("pocket: installed in %s (stock voices: %s)" % (ROOT, ", ".join(sorted(VOICES))))
    return rec


def own_voice_ok(name: str) -> bool:
    """Her own voice's file is in the pack and is the one the kit pinned (size, then sha256 once per process)."""
    p, sha, n = OWN_VOICES[name]
    if _OWN_OK.get(name) is None:
        try:
            _OWN_OK[name] = p.is_file() and p.stat().st_size == n and _sha256(p) == sha
        except OSError:
            _OWN_OK[name] = False
    return bool(_OWN_OK[name])


_OWN_OK: dict = {}


def voice_for(name: str, mapping: dict | None = None, code: str | None = None) -> tuple[str, str]:
    """(stock voice, note) for the voice a caller names: a stock voice by its own name, else the face's pick in
    `mapping` (the tier table's pocket.settings.voices), else default_voice(); the note says when it stood in.
    In another language only that language's allowed voices count. A female face (SHE_FACES) or a name that falls to
    the default never gets a man's voice: a pick outside FEMALE gives way to default_voice()."""
    allowed = set(voices(code)) if code and code != "en" else set(VOICES) | {v for v in OWN_VOICES if own_voice_ok(v)}
    n = (name or "").strip().lower()
    if n in allowed:
        return n, ""
    m = {str(k).lower(): str(v).lower() for k, v in (mapping or {}).items()}
    fallback = default_voice(code)
    pick = m.get(n) or m.get("default") or fallback
    if pick in OWN_VOICES and pick not in allowed and m.get("default") in allowed:
        pick = m["default"]                     # round 28: her own file missing or not the pinned one: the table's default
    she = n in SHE_FACES or n not in m          # a female face, or a name that falls to the default (crissa's)
    if pick not in allowed:
        return fallback, "%r is not an allowed stock voice; %s speaks" % (pick, fallback)
    if she and pick not in FEMALE:              # a saved or hand-made map naming a man's voice for her
        return fallback, "%r is not a woman's voice; %s speaks for %s" % (pick, fallback, n or "her")
    return pick, ("" if n in m else "no Pocket voice named %r; %s speaks" % (n, pick))


def local_config(env_root: Path | None = None, code: str | None = None) -> Path:
    """The package's config for the language with every hf:// path pointed at the checked local files (runs in the
    env)."""
    import yaml  # noqa: PLC0415  (in the Pocket env)
    import pocket_tts  # noqa: PLC0415
    lang = lang_dir(code)
    src = Path(pocket_tts.__file__).resolve().parent / "config" / ("%s.yaml" % lang)
    cfg = yaml.safe_load(src.read_text())
    cfg["weights_path"] = str(model_path("languages/%s/model.safetensors" % lang))
    cfg["weights_path_without_voice_cloning"] = None
    cfg["flow_lm"]["lookup_table"]["tokenizer_path"] = str(model_path("languages/%s/tokenizer.json" % lang))
    out = MODEL / ("%s-local.yaml" % lang)
    out.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return out


def in_env() -> bool:
    try:
        return Path(sys.prefix).resolve() == ENV.resolve()
    except OSError:
        return False


if __name__ == "__main__":
    try:
        install()
    except RuntimeError as e:
        print("pocket: " + str(e), file=sys.stderr)
        sys.exit(2)
