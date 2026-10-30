"""The voice module's Chatterbox Multilingual V3: an optional GPU voice for languages other than English (2026-10-06,
the User: offered in Init and in the Control Panel, opt-in, the Perth watermark stays on).

Resemble AI's Chatterbox Multilingual V3 (0.5B; code and weights MIT, Hugging Face ResembleAI/chatterbox at a pinned
revision, every file checked by sha256), 23 languages. It runs in TURBO'S ENV (the same hash locks, the same Arm
stages, the same self-test; turbo_setup builds it if it is not there yet), with its own model and its own voices:

    python3 modules/voice/scripts/crissa-multilingual.py --install
    python3 modules/voice/scripts/crissa-multilingual.py [--port 8185]

    <crissa home>/turbo/env                the shared env (locks/turbo-linux-<arch>-<cuda>-py312.txt)
    <crissa home>/multilingual/model       the model files at MODEL_REV, each checked by sha256 (about 3.2 GB)
    <crissa home>/multilingual/voices      <face>.wav: a clip YOU have the rights to. NONE SHIP. A clip is cloned
                                           here on its own: the speaker embeddings of this model and Turbo's are
                                           not interchangeable, so a face cloned for Turbo is cloned again here
                                           (the same .wav may be copied in). No clip: its built-in voice.

Language: each line's `language` in the /stream request, else multilingual.settings.language in the tier table,
else the language Init detected (voice-tier.json facts.language), else English.

What this env leaves out, and what it costs (said in /health and the start-up log): pykakasi (GPL-3) is not
installed, so Japanese kanji are not turned into kana first; spacy-pkuseg, dicta-onnx and russian-text-stresser are
not in the lock, so Chinese is not word-segmented, Hebrew gets no diacritics and Russian no stress marks. Those four
languages speak, less well; the other nineteen are unaffected.

NOT YET VERIFIED ON ARM HARDWARE (the Arm path is Turbo's); and on the reference house's x86_64 GPU it was measured
by a bake-off only (process peak 4.4 GiB with the allocator capped at 4 GiB), not yet through this service.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import egress, turbo_setup as T

ROOT = T._home() / "multilingual"
MODEL = ROOT / "model"
VOICES = ROOT / "voices"
DONE = ROOT / "installed.json"
ENV = T.ENV
MODEL_REPO = "ResembleAI/chatterbox"
MODEL_REV = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"   # 2026-06-10, MIT: Multilingual V3
T3 = "t3_mtl23ls_v3.safetensors"
CREDIT = "Voice: Chatterbox Multilingual V3 by Resemble AI (resemble.ai), MIT"
MODEL_FILES = {
    T3: ("5abca8321ede76f8e61f1cc0d19aea6c946b28871017ce8726f8a69203f05953", 2143989928),
    "s3gen.pt": ("9b9ff07e60b20c136e2b1b3d7563a24604e8d2c4c267888d1ee929dd0151d2a3", 1057165844),
    "ve.pt": ("4b16d836bc598509860f6fa068165a8bb5e9ac84f05582dfcf278a5a372879f1", 5698626),
    "conds.pt": ("6552d70568833628ba019c6b03459e77fe71ca197d5c560cef9411bee9d87f4e", 107374),
    "grapheme_mtl_merged_expanded_v1.json": ("69632f47220a788a52ce2661d096453c5655e9bf25289d89a8d832c46ee07dbf", 69989),
    "Cangjie5_TC.json": ("7073fd9de919443ae88e0bd2449917a65fe54898a4413ed1edcc4b67f28bce8c", 1920163),
}
# chatterbox.mtl_tts.SUPPORTED_LANGUAGES (0.1.7), the codes the model takes
LANGUAGES = {"ar": "Arabic", "da": "Danish", "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
             "fi": "Finnish", "fr": "French", "he": "Hebrew", "hi": "Hindi", "it": "Italian", "ja": "Japanese",
             "ko": "Korean", "ms": "Malay", "nl": "Dutch", "no": "Norwegian", "pl": "Polish", "pt": "Portuguese",
             "ru": "Russian", "sv": "Swedish", "sw": "Swahili", "tr": "Turkish", "zh": "Chinese"}
REDUCED = {"ja": "no kanji-to-kana step (pykakasi is GPL-3 and is not installed)",
           "zh": "no word segmentation (spacy-pkuseg is not in the lock)",
           "he": "no diacritics (dicta-onnx is not in the lock)",
           "ru": "no stress marks (russian-text-stresser is not in the lock)"}


def env_python() -> Path:
    return T.env_python()


def installed() -> bool:
    if not (DONE.is_file() and env_python().exists()):
        return False
    return all((MODEL / r).is_file() and (MODEL / r).stat().st_size == n for r, (_, n) in MODEL_FILES.items())


def unsupported(**kw) -> str:
    why = T.unsupported(**kw)
    return why.replace("Turbo", "Chatterbox Multilingual") if why else ""


def get_model(fetch=T._fetch, dest: Path | None = None, log=print) -> None:
    dest = Path(dest or MODEL)
    for rel, (sha, _) in MODEL_FILES.items():
        p = dest / rel
        if p.is_file() and T._sha256(p) == sha:
            continue
        log("multilingual: fetching %s …" % rel)
        fetch("https://huggingface.co/%s/resolve/%s/%s" % (MODEL_REPO, MODEL_REV, rel), p)
        got = T._sha256(p)
        if got != sha:
            p.unlink()
            raise RuntimeError("%s from %s@%s does not match its pinned sha256 (got %s); nothing was installed"
                               % (rel, MODEL_REPO, MODEL_REV[:8], got[:16]))


SELF_TEST = T.SELF_TEST.replace("import chatterbox.tts_turbo  # noqa: F401", "import chatterbox.mtl_tts  # noqa: F401")


def self_test(run=None, watermark: bool = True) -> dict:
    keep = T.SELF_TEST
    try:
        T.SELF_TEST = SELF_TEST
        return T.self_test(run=run, watermark=watermark)
    finally:
        T.SELF_TEST = keep


def record() -> dict:
    try:
        return json.loads(DONE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_self_test(st: dict) -> dict:
    rec = record()
    rec["self_test"] = st
    DONE.write_text(json.dumps(rec) + "\n")
    return rec


def self_test_failed() -> str:
    st = record().get("self_test")
    if isinstance(st, dict) and st.get("ok") is False:
        return str(st.get("why") or "it failed")
    return ""


def install(log=print) -> dict:
    """Opt-in. Turbo's env first (built from its lock when missing), then this model, then the self-test."""
    why = unsupported()
    if why:
        raise RuntimeError(why)
    egress.refuse_install("the Chatterbox Multilingual voice")
    ROOT.mkdir(parents=True, exist_ok=True)
    VOICES.mkdir(parents=True, exist_ok=True)
    if not (T.DONE.is_file() and env_python().exists()):
        log("multilingual: it runs in Turbo's env, which is not built yet: building it from Turbo's lock …")
        T.ROOT.mkdir(parents=True, exist_ok=True)
        how = T.make_env(log=log)
        T.check_no_gpl()
    else:
        how = "turbo env (%s)" % (T.record().get("installed_by") or "already built")
    log("multilingual: model %s @ %s (about 3.2 GB): files already here with the right sha256 are kept, the rest "
        "are fetched …" % (MODEL_REPO, MODEL_REV[:8]))
    get_model(log=log)
    rec = {"model": MODEL_REPO, "rev": MODEL_REV, "t3": T3, "env": how, "credit": CREDIT,
           "verified_on_hardware": T.platform.machine().lower() not in T.ARM}
    DONE.write_text(json.dumps(rec) + "\n")
    st = self_test(watermark=T._watermark_on())
    rec = save_self_test(st)
    if st.get("ok"):
        log("multilingual: installed in %s; self-test passed on %s" % (ROOT, st.get("gpu")))
    else:
        log("multilingual: SELF-TEST FAILED: %s. Init passes it over: Turbo speaks English, else Pocket. Re-run it: "
            "python3 modules/voice/scripts/crissa-multilingual.py --self-test" % st.get("why"))
    from . import faces  # noqa: PLC0415
    got = faces.fetch_all(T._fetch, log, "multilingual")
    rec = dict(record(), faces=got)
    DONE.write_text(json.dumps(rec) + "\n")
    for f, p in face_plan().items():
        log("multilingual: %s speaks with %s%s (English speakers: in another language they keep an accent)"
            % (f, p["voice"], (" (%s)" % p["fallback"]) if p["fallback"] else ""))
    return rec


def voice_clips(dirs: list[Path] | None = None) -> dict:
    """{face: clip}: the faces' stock reference clips (faces.py; audio, so shared with Turbo, but cloned here on its
    own: the speaker embeddings are not interchangeable), then this voice's own folder, which wins. Never Turbo's
    own folder."""
    from . import faces  # noqa: PLC0415
    if dirs is not None:
        out = {}
        for d in dirs:
            for p in sorted(Path(d).glob("*.wav")):
                out[p.stem.lower()] = p
        return out
    return faces.clips(VOICES)


def face_plan() -> dict:
    from . import faces  # noqa: PLC0415
    return faces.plan(VOICES, "Multilingual's built-in voice")


def in_env() -> bool:
    return T.in_env()


def _home() -> Path:
    return T._home()


if __name__ == "__main__":
    try:
        install()
    except RuntimeError as e:
        print("multilingual: " + str(e), file=sys.stderr)
        sys.exit(2)
