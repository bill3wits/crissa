"""Which voice THIS machine can run: probed once, picked by Init, kept in the install's config (2026-10-06).

the User 2026-10-06: the repo must run on every system, Apple Macs included; limp along on an 8 GB M1/M2 and be better
than the house on a stronger machine. So the voice is chosen by what the machine can handle, not one fixed default.

    probe()        what is here: OS, chip, memory, an NVIDIA GPU and its free memory (nvidia-smi), the Python.
                   Cheap and offline; it never imports torch and never measures speed (lib/crissa_machine does that).
    pick(facts)    the tiers in config/crissa-voice-tiers.json, best first; the first this machine meets is the
                   main voice, the ones below it that it also meets are the backup chain, and every tier above it
                   says why not. A copy of the tier file in the state home wins key by key: a bigger machine adds or
                   loosens tiers there, and nothing in this code caps it.
    ensure()       Init's step (and the launcher's on a first start): probe once, write <state home>/voice-tier.json
                   (schema crissa-voice-tier/1), and return what to say. Already written = read, never re-probed;
                   `scripts/crissa-voice-tier.py --probe` probes again, `--pick ENGINE` is the person's override.
    chain(rec)     the engines lib/crissa_tts tries, in order, when nothing names one (CRISSA_TTS, agent.json's tts
                   engine and --tts all win over the pick).

What speaks at a given moment is the first engine in the chain that is READY (installed and answering); each one
passed over is named on screen and in the log (lib/crissa_tts writes voice-state.json; house-speak prints it).

THE HOUSE never probes and never writes: its voice is named in its own house config (tts_main, tts_backup), the
same seam with a different config. house_pick() reads it. Off the house, a machine whose voice names nothing still
gets Init's order, each only when ready.

THE ORDER (the User 2026-10-07, and 2026-10-08 for Pocket's backup): Chatterbox-Turbo on a strong NVIDIA GPU (Linux,
x86_64 or Arm), Audio8 on a robust CPU machine, Pocket TTS on a lighter CPU (Linux, or an Apple Silicon Mac on
macOS 14+), Kokoro only as Pocket's backup on those lighter machines and on an 8 GB Mac (af_heart; it stands back
for Turbo and Audio8), the Mac's `say`, on-screen text. Init picks the first this machine meets and installs it
(lib/crissa_voice_install.py); one that fails to install falls down the chain, and what was chosen is kept in the
record. Multilingual V3, Turbo MLX and Piper stay in the table and speak only when the person names one (--pick,
the tts block); Init never picks them. Kokoro is nameable the same way, and Init also installs it when Pocket is
her voice. probe() still reads the language (LC_ALL,
LC_MESSAGES, LANG, LANGUAGE; a Mac's first preferred language): a tier whose `languages` need does not hold it is
passed over, and the screen says so when no voice here speaks it.

SELF-TEST (2026-10-06): an installed engine whose install self-test failed (turbo_setup.self_test_failed(); the Arm
path is not yet verified on Arm hardware) is passed over like an unmet need, with its reason, on screen and in the
log, and the next tier (Pocket) speaks. A saved pick is re-made when its main fails; a person's own pick is kept
and the screen says it fails.
Every engine in the order ships in this release. A tier marked "slot": "v2" (in a state-home copy, for an engine
still to come) is never picked; it is passed over with that reason, named on screen and in the log like any other.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import crissa_home  # noqa: E402
except ImportError:  # pragma: no cover - the module always ships beside it
    crissa_home = None

SCHEMA = "crissa-voice-tier/1"
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "crissa-voice-tiers.json"
ENGINES = ("multilingual", "turbo", "turbo_mlx", "pocket", "audio8", "kokoro", "say", "piper", "text")
# Init's order (the User 2026-10-07, again that afternoon; 2026-10-08 Kokoro behind Pocket only): Turbo on a strong
# GPU, Audio8 on a robust CPU machine (its needs: 16 GB, 8 logical cores) with Pocket as her backup, Pocket on a
# lighter CPU, Kokoro only when Pocket is her voice (backup_only_for; stands back for Turbo and Audio8), the Mac's
# say, text. The other engines stay nameable (--pick, the tts block) and are never picked by Init.
DEFAULT_ORDER = ["turbo", "audio8", "pocket", "kokoro", "say", "text"]
V2 = "v2"   # a tier whose engine is not in this release (its "slot")
# The code's own copy of the shipped table, used only when the file is missing (a stripped install); the file wins.
DEFAULT_TIERS = {
    "multilingual": {"label": "Chatterbox Multilingual V3 (Resemble AI, a GPU voice for your language)",
                     "needs": {"gpu": "cuda", "gpu_free_gb": 6, "ram_gb": 8, "os": ["linux"],
                               "arch": ["x86_64", "aarch64", "arm64"], "languages": ["ar", "da", "de", "el", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh"]},
                     "backup": ["turbo", "pocket", "say"],
                     "install": "python3 modules/voice/scripts/crissa-multilingual.py --install",
                     "settings": {"watermark": True, "language": "auto", "vram_cap_gb": 4.0, "headroom_gb": 2.0}},
    "turbo": {"label": "Chatterbox-Turbo (Resemble AI, a GPU voice, sentence by sentence)",
              "needs": {"gpu": "cuda", "gpu_free_gb": 6, "ram_gb": 8, "os": ["linux"],
                        "arch": ["x86_64", "aarch64", "arm64"], "languages": ["en"]},
              "backup": ["pocket", "say"],
              "install": "python3 modules/voice/scripts/crissa-turbo.py --install",
              "settings": {"watermark": True, "chunk_first_min": 0, "chunk_min": 0, "chunk_max": 80,
                           "temperature": 0.8, "seed": 0, "vram_cap_gb": 4.5, "headroom_gb": 1.5}},
    "turbo_mlx": {"label": "Chatterbox-Turbo on Apple Silicon (MLX, unverified)",
                  "needs": {"gpu": "mps", "ram_gb": 16, "os": ["darwin"], "mac_chip": "arm64",
                            "macos_min": "14.0", "languages": ["en"]},
                  "backup": ["pocket", "say"],
                  "install": "python3 modules/voice/scripts/crissa-turbo-mlx.py --install",
                  "size": "about 0.5-0.8 GB of model (4-bit or 8-bit) plus mlx-audio",
                  "settings": {"watermark": True, "quant": "4bit", "chunk_max": 80}},
    "pocket": {"label": "Pocket TTS (Kyutai, 100M, on the CPU)",
               "needs": {"ram_gb": 4, "os": ["linux", "darwin"], "mac_chip": "arm64", "macos_min": "14.0",
                         "languages": ["en", "fr", "de", "es", "it", "pt", "nl"]},
               "backup": ["kokoro", "say"], "installs_backup": True,
               "install": "python3 modules/voice/scripts/crissa-pocket.py --install",
               "settings": {"threads": 3, "emit": "frames", "chunk_first_max": 40, "chunk_max": 80}},
    "audio8": {"label": "Audio8 TTS (Edge0, Crissa's voice and the announcer on a robust CPU machine)",
               "needs": {"ram_gb": 16, "cpu_cores": 8, "os": ["linux", "darwin"], "macos_min": "14.0",
                         "languages": ["en", "fr", "de", "es", "it", "nl", "ja", "zh", "ko", "pl", "yue"]},
               "stands_back_for": ["turbo", "multilingual", "turbo_mlx"], "installs_backup": True,
               "backup": ["pocket", "kokoro", "say"],
               "install": "python3 modules/voice/scripts/crissa-audio8.py --install",
               "settings": {"stream_replies": True, "piece_chars": 72}},
    "kokoro": {"label": "Kokoro (Kokoro-82M on the CPU)",
               "needs": {"ram_gb": 4, "os": ["linux", "darwin"], "mac_chip": "arm64", "languages": ["en"]},
               "backup_only_for": "pocket",
               "stands_back_for": ["turbo", "audio8", "multilingual", "turbo_mlx"],
               "backup": ["say", "piper"], "install": "python3 modules/voice/scripts/crissa-tts.py --install"},
    "say": {"label": "the Mac's own voice (say)", "needs": {"os": ["darwin"], "languages": ["*"]}, "backup": []},
    "piper": {"label": "Piper (a voice model you supply)", "needs": {"piper_model": True, "languages": ["en"]},
              "backup": []},
    "text": {"label": "on-screen text", "needs": {}, "backup": []},
}
DEFAULT_ANNOUNCER = {"order": ["audio8", "pocket", "kokoro", "say", "text"], "audio8": {"voice": "anna", "voice_b": "", "alternate": "day"},
                     "pocket": {"anna": "anna"}, "kokoro": "bm_george", "say": "Daniel"}


def _home(home: Path | None = None) -> Path:
    if home is not None:
        return Path(home)
    if crissa_home is not None:
        return crissa_home.HOME
    return Path(os.environ.get("CRISSA_HOME") or (Path.home() / ".crissa")).expanduser()


def record_path(home: Path | None = None) -> Path:
    return _home(home) / "voice-tier.json"


def is_house() -> bool:
    try:
        return bool(crissa_home and crissa_home.is_house())
    except Exception:  # noqa: BLE001
        return False


# AUDIO8 IS HER EAR, NEVER A VOICE (the User 2026-10-17, round 27 on .101: Setup picked Audio8 for her voice though its
# own speed test failed it - rtf 3.04, first audio 4.57 s - "slower and sounds terrible"; Pocket "amazing and quicker").
# In a pack Audio8 never speaks: not her voice, not her backup, not the announcer, whatever the table, a saved pick, a
# state-home copy or a setting names. It stays the ASR, installed and running as it is. The house is unchanged (its
# config names its own voices). Kill CRISSA_AUDIO8_TTS=1 (Audio8 may speak again).
NOT_VOICES = ("audio8",)


def not_a_voice(name: str, env: dict | None = None) -> bool:
    """True for an engine that may not speak in this pack (Audio8; the ASR is untouched)."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_AUDIO8_TTS", "0")).strip().lower() in ("1", "on", "true", "yes") or is_house():
        return False
    return str(name or "").strip().lower() in NOT_VOICES


def voices_only(seq, env: dict | None = None) -> list[str]:
    """`seq` less every engine that may not speak (not_a_voice), order kept."""
    return [str(x) for x in (seq or []) if not not_a_voice(str(x), env)]


def clean_record(rec: dict | None, env: dict | None = None) -> dict | None:
    """A saved pick with Audio8 taken out of her chain and the announcer's; a main that was Audio8 becomes the next
    voice in her chain (Pocket on the machines that had it). None stays None."""
    if not rec:
        return rec
    out = dict(rec)
    chain = voices_only(out.get("chain") or [], env)
    if "chain" in out:
        out["chain"] = chain
    if "announcer_chain" in out:
        out["announcer_chain"] = voices_only(out.get("announcer_chain") or [], env)
    if not_a_voice(str(out.get("main") or ""), env):
        out["main"] = out["tier"] = (chain[0] if chain else "text")
    if not_a_voice(str(out.get("announcer") or ""), env):
        ann = out.get("announcer_chain") or []
        out["announcer"] = ann[0] if ann and ann[0] != out.get("main") else ""
    return out


def names_a_non_voice(rec: dict | None, env: dict | None = None) -> bool:
    """A saved pick that names Audio8 anywhere as a voice (her main, her chain, the announcer's)."""
    if not rec:
        return False
    names = [rec.get("main"), rec.get("announcer")] + list(rec.get("chain") or []) + list(rec.get("announcer_chain") or [])
    return any(not_a_voice(str(n or ""), env) for n in names)


# ---- the tier table ---------------------------------------------------------------------------------------------

def table(home: Path | None = None, config: Path | None = None) -> dict:
    """{"order": [...], "tiers": {...}, "beyond": {...}}: the shipped file, then the state-home copy key by key."""
    out = {"order": list(DEFAULT_ORDER), "tiers": json.loads(json.dumps(DEFAULT_TIERS)), "beyond": {},
           "announcer": json.loads(json.dumps(DEFAULT_ANNOUNCER))}
    for p in ((config or CONFIG), _home(home) / "crissa-voice-tiers.json"):
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        if isinstance(d.get("order"), list):
            out["order"] = [str(x) for x in d["order"]]
        for name, t in (d.get("tiers") or {}).items():
            if isinstance(t, dict):
                merged = dict(out["tiers"].get(name) or {})
                for k, v in t.items():
                    if isinstance(v, dict) and isinstance(merged.get(k), dict):
                        merged[k] = dict(merged[k], **v)
                    else:
                        merged[k] = v
                out["tiers"][name] = merged
        if isinstance(d.get("beyond"), dict):
            out["beyond"] = dict(out["beyond"], **d["beyond"])
        if isinstance(d.get("keep_warm"), dict):
            out["keep_warm"] = dict(out.get("keep_warm") or {}, **d["keep_warm"])
        if isinstance(d.get("on_demand"), dict):      # a voice runs only while a role uses it (round 17)
            out["on_demand"] = dict(out.get("on_demand") or {}, **d["on_demand"])
        if isinstance(d.get("announcer"), dict):
            for k, v in d["announcer"].items():
                out["announcer"][k] = dict(out["announcer"].get(k) or {}, **v) if isinstance(v, dict) and \
                    isinstance(out["announcer"].get(k), dict) else v
    out["order"] = voices_only(out["order"])                      # Audio8 is her ear, never a voice (above)
    if isinstance(out["announcer"].get("order"), list):
        out["announcer"]["order"] = voices_only(out["announcer"]["order"])
    return out


# ---- the probe --------------------------------------------------------------------------------------------------

def _run(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _ram_gb(plat: str, run=_run) -> float | None:
    if plat == "darwin":
        out = run(["sysctl", "-n", "hw.memsize"]).strip()
        return round(int(out) / (1 << 30), 1) if out.isdigit() else None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / (1 << 20), 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def _nvidia(run=_run) -> list[dict]:
    """Every NVIDIA GPU nvidia-smi reports: name, total and free memory in GB. [] when there is none."""
    if not shutil.which("nvidia-smi"):
        return []
    out = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[1].replace(".", "").isdigit() and parts[2].replace(".", "").isdigit():
            gpus.append({"name": parts[0], "total_gb": round(float(parts[1]) / 1024, 1),
                         "free_gb": round(float(parts[2]) / 1024, 1)})
    return gpus


def _mac_chip(run=_run) -> str:
    brand = run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    return brand or ""


def probe(run=_run, plat: str | None = None, machine: str | None = None, env: dict | None = None) -> dict:
    """What this machine has. Never raises, never imports torch, never touches the network."""
    e = os.environ if env is None else env
    p = plat or sys.platform
    m = (machine or platform.machine() or "").lower()
    os_name = "darwin" if p == "darwin" else ("windows" if p.startswith("win") else ("linux" if p.startswith("linux") else p))
    facts = {"os": os_name, "arch": m, "cpu_cores": os.cpu_count() or 0, "ram_gb": _ram_gb(os_name, run),
             "python": "%d.%d" % sys.version_info[:2], "gpu": {"kind": "none"}}
    gpus = _nvidia(run) if os_name in ("linux", "windows") else []
    if gpus:
        best = max(gpus, key=lambda g: g["free_gb"])
        facts["gpu"] = {"kind": "cuda", "name": best["name"], "total_gb": best["total_gb"],
                        "free_gb": best["free_gb"], "count": len(gpus)}
    elif os_name == "darwin" and m in ("arm64", "aarch64"):
        facts["gpu"] = {"kind": "mps", "name": _mac_chip(run) or "Apple Silicon", "unified_gb": facts["ram_gb"]}
    facts["language"] = language(e, os_name, run)
    pm = str(e.get("CRISSA_TTS_PIPER_MODEL") or "").strip()
    facts["piper_model"] = bool(pm and Path(pm).expanduser().is_file())
    if os_name == "darwin":
        facts["macos"] = (platform.mac_ver()[0] if plat is None else "") or ""
    return facts


LANG_NAMES = {"ar": "Arabic", "da": "Danish", "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
              "fi": "Finnish", "fr": "French", "he": "Hebrew", "hi": "Hindi", "it": "Italian", "ja": "Japanese",
              "ko": "Korean", "ms": "Malay", "nl": "Dutch", "no": "Norwegian", "pl": "Polish", "pt": "Portuguese",
              "ru": "Russian", "sv": "Swedish", "sw": "Swahili", "tr": "Turkish", "zh": "Chinese"}


def _lang_code(v: str) -> str:
    """'de_DE.UTF-8' -> 'de'; 'nb_NO' -> 'no'; 'C' / 'POSIX' / '' -> ''."""
    v = (v or "").split(":")[0].split(".")[0].split("@")[0].strip().lower().replace("-", "_")
    if v in ("", "c", "posix"):
        return ""
    code = v.split("_")[0]
    return {"nb": "no", "nn": "no", "iw": "he"}.get(code, code)


def language(env: dict | None = None, os_name: str | None = None, run=_run) -> dict:
    """The person's language: {"code", "name", "from"}. LC_ALL, LC_MESSAGES, LANG, then LANGUAGE (the POSIX order);
    on a Mac, its first preferred language when the environment names none. CRISSA_LANGUAGE is the person's own."""
    e = os.environ if env is None else env
    saved = saved_language()
    if saved:
        return saved
    for key in ("CRISSA_LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        code = _lang_code(str(e.get(key) or ""))
        if code:
            return {"code": code, "name": LANG_NAMES.get(code, code), "from": key}
    if (os_name or "") == "darwin":
        out = run(["defaults", "read", "-g", "AppleLanguages"])
        m = re.search(r'"?([A-Za-z]{2,3})(?:[-_][A-Za-z]+)*"?\s*[,)]', out or "")
        if m:
            code = _lang_code(m.group(1))
            return {"code": code, "name": LANG_NAMES.get(code, code), "from": "the Mac's preferred languages"}
    return {"code": "en", "name": "English", "from": "nothing named (English)"}


# Names a person may type for a language (English and its own name), to its code.
LANG_WORDS = {"english": "en", "anglais": "en", "arabic": "ar", "العربية": "ar", "danish": "da", "dansk": "da",
              "german": "de", "deutsch": "de", "greek": "el", "ελληνικά": "el", "spanish": "es", "español": "es",
              "espanol": "es", "finnish": "fi", "suomi": "fi", "french": "fr", "français": "fr", "francais": "fr",
              "hebrew": "he", "עברית": "he", "hindi": "hi", "हिन्दी": "hi", "italian": "it", "italiano": "it",
              "japanese": "ja", "日本語": "ja", "korean": "ko", "한국어": "ko", "malay": "ms", "bahasa melayu": "ms",
              "dutch": "nl", "nederlands": "nl", "norwegian": "no", "norsk": "no", "polish": "pl", "polski": "pl",
              "portuguese": "pt", "português": "pt", "portugues": "pt", "russian": "ru", "русский": "ru",
              "swedish": "sv", "svenska": "sv", "swahili": "sw", "kiswahili": "sw", "turkish": "tr", "türkçe": "tr",
              "chinese": "zh", "mandarin": "zh", "中文": "zh", "cantonese": "yue", "粵語": "yue"}
LANG_NAMES.setdefault("yue", "Cantonese")


def parse_language(text: str) -> str:
    """What the person typed (a name in English or in the language, or a code such as de or pt-BR) to a code;
    '' when it names nothing we know."""
    t = (text or "").strip().lower()
    if not t:
        return ""
    if t in LANG_WORDS:
        return LANG_WORDS[t]
    code = _lang_code(t)
    return code if code in LANG_NAMES or (len(code) in (2, 3) and code.isalpha()) else ""


def language_path(home: Path | None = None) -> Path:
    return _home(home) / "language.json"


def saved_language(home: Path | None = None) -> dict:
    try:
        d = json.loads(language_path(home).read_text(encoding="utf-8"))
        if d.get("code"):
            return {"code": d["code"], "name": LANG_NAMES.get(d["code"], d["code"]), "from": "Init (your answer)"}
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def set_language(text: str, home: Path | None = None) -> dict:
    """Init's language answer: kept in <state home>/language.json, then the voice is picked again for it.
    {"ok", "code", "name", "rec"} or {"ok": False, "error"}."""
    code = parse_language(text)
    if not code:
        return {"ok": False, "error": "I do not know the language %r; type its name (Deutsch, Français, 日本語) or "
                                      "a code such as de, fr or ja" % text}
    lp = language_path(home)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps({"code": code, "typed": str(text)[:80], "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")
    # --pick lasts until --probe/--reset; a language answer must not wipe it (ultra voice-module)
    saved = read(home)
    user_engine = saved.get("main") if saved and saved.get("picked_by") == "user" else None
    rec = ensure(home, reprobe=True)
    if user_engine:
        try:
            override(user_engine, home)
            rec = ensure(home)                    # the dressed record: its `say` lines name the person's choice
        except ValueError:
            pass                                  # their engine is gone from this release: Init's pick stands
    return {"ok": True, "code": code, "name": LANG_NAMES.get(code, code), "rec": rec}


# HER VOICE OR AN ENGLISH ACCENT (round 28, the User 2026-10-17: "We could offer this voice as English Accent option ...
# Simple toggle at setup?"). One choice in <state home>/her-voice.json: "her" (the default and the brand: her Grok
# Imagine voice - Pocket's crissa on a CPU, the clip on the voices that clone) or "english_accent" (Pocket's azelma, the
# stock voice she had before round 28). Read on every line by the Pocket server; the phone follows the install's voice.
# Kill: CRISSA_HER_VOICE=english_accent (round 27's voice whatever was chosen).
# Labels, the User 2026-10-17: "Imagine voice is free but branded to crissa by us. We offer a toggle to render non-branded,
# UK english voice now."
HER_VOICE_STYLES = {"her": "Crissa's voice", "english_accent": "Non-branded voice (UK English)"}
HER_VOICE_POCKET = {"her": "crissa", "english_accent": "azelma"}


def her_voice_path(home: Path | None = None) -> Path:
    return _home(home) / "her-voice.json"


def her_voice(home: Path | None = None, env: dict | None = None) -> str:
    e = os.environ if env is None else env
    forced = str(e.get("CRISSA_HER_VOICE") or "").strip().lower()
    if forced in HER_VOICE_STYLES:
        return forced
    try:
        got = str(json.loads(her_voice_path(home).read_text(encoding="utf-8")).get("style") or "")
        return got if got in HER_VOICE_STYLES else "her"
    except (OSError, ValueError, AttributeError):
        return "her"


def set_her_voice(style: str, home: Path | None = None, by: str = "") -> dict:
    s = str(style or "").strip().lower().replace(" ", "_").replace("-", "_")
    if s not in HER_VOICE_STYLES:
        return {"ok": False, "error": "her voice is one of: %s" % ", ".join(HER_VOICE_STYLES.values())}
    p = her_voice_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"style": s, "by": by[:40], "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")
    os.replace(tmp, p)
    return {"ok": True, "style": s, "label": HER_VOICE_STYLES[s]}


def pocket_face_voice(face: str, mapping: dict | None, home: Path | None = None) -> str | None:
    """The Pocket voice her face speaks with under the chosen style, else None (another face: its own map)."""
    if str(face or "").strip().lower() not in ("crissa", "crissa"):
        return None
    return HER_VOICE_POCKET[her_voice(home)]


def describe(facts: dict) -> str:
    """One short line about the machine, for Init and the log."""
    g = facts.get("gpu") or {}
    os_label = {"darwin": "Mac", "linux": "Linux", "windows": "Windows"}.get(facts.get("os"), facts.get("os") or "?")
    ram = facts.get("ram_gb")
    if g.get("kind") == "cuda":
        gpu = "%s, %.0f GB GPU memory (%.0f GB free)" % (g.get("name"), g.get("total_gb") or 0, g.get("free_gb") or 0)
    elif g.get("kind") == "mps":
        gpu = "%s, unified memory" % (g.get("name") or "Apple Silicon")
    else:
        gpu = "no NVIDIA GPU"
    return "%s %s, %s RAM, %s" % (os_label, facts.get("arch") or "", ("%.0f GB" % ram) if ram else "unknown", gpu)


# ---- the pick ---------------------------------------------------------------------------------------------------

def _ver(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def unmet(tier: dict, facts: dict, role: str = "main") -> list[str]:
    """Why this machine does not meet the tier's needs ([] = it does). role "main" is Crissa's own voice (a tier's
    main_languages, when it has them, narrow its languages); "announcer" is the announcer's chain."""
    needs = tier.get("needs") or {}
    out = []
    if tier.get("slot") == V2:
        out.append("is a v2 slot: its engine is not in this release yet")
    oses = needs.get("os")
    if oses and facts.get("os") not in oses:
        names = {"darwin": "macOS", "linux": "Linux", "windows": "Windows"}
        out.append("needs %s (this is %s)" % (" or ".join(names.get(o, o) for o in oses),
                                              names.get(facts.get("os"), facts.get("os"))))
    arches = needs.get("arch")
    if arches and facts.get("arch") and str(facts["arch"]).lower() not in [str(a).lower() for a in arches]:
        out.append("needs %s (this machine is %s)" % (" or ".join(arches), facts.get("arch")))
    chip = needs.get("mac_chip")
    if chip and facts.get("os") == "darwin" and facts.get("arch") not in (chip, "aarch64" if chip == "arm64" else chip):
        out.append("needs an Apple Silicon Mac (this one is %s)" % facts.get("arch"))
    mv, want = str(facts.get("macos") or ""), str(needs.get("macos_min") or "")
    if want and facts.get("os") == "darwin" and mv and _ver(mv) < _ver(want):
        out.append("needs macOS %s or later (this Mac runs %s)" % (want, mv))
    g = facts.get("gpu") or {}
    want_gpu = needs.get("gpu")
    if want_gpu == "cuda" and g.get("kind") != "cuda":
        out.append("needs an NVIDIA GPU (CUDA)" + (" - an Apple GPU is not supported by it" if g.get("kind") == "mps" else ""))
    elif want_gpu == "mps" and g.get("kind") != "mps":
        out.append("needs Apple Silicon (MLX / Metal)" + (
            " - an NVIDIA GPU uses Turbo's CUDA path, not this one" if g.get("kind") == "cuda" else ""))
    elif want_gpu == "cuda" and needs.get("gpu_free_gb"):
        if float(g.get("free_gb") or 0) < float(needs["gpu_free_gb"]):
            if float(g.get("total_gb") or 0) >= float(needs["gpu_free_gb"]):
                out.append("needs %s GB of free GPU memory; %s has %.0f GB but only %.1f GB is free now (other "
                           "programs hold it). Free it and probe again" % (needs["gpu_free_gb"], g.get("name"),
                                                                           g.get("total_gb") or 0, g.get("free_gb") or 0))
            else:
                out.append("needs %s GB of free GPU memory (%s has %.0f GB)" % (needs["gpu_free_gb"], g.get("name"),
                                                                              g.get("total_gb") or 0))
    if needs.get("cpu_cores") and int(facts.get("cpu_cores") or 0) < int(needs["cpu_cores"]):
        out.append("needs %s CPU cores (this machine has %s)" % (needs["cpu_cores"], facts.get("cpu_cores")))
    if needs.get("ram_gb") and facts.get("ram_gb") is not None and float(facts["ram_gb"]) < float(needs["ram_gb"]):
        out.append("needs %s GB of RAM (this machine has %.0f GB)" % (needs["ram_gb"], facts["ram_gb"]))
    langs = needs.get("main_languages" if role == "main" else "languages") or needs.get("languages")
    if langs and "*" not in langs:
        lang = (facts.get("language") or {}).get("code") or "en"
        if lang not in langs:
            out.append(("does not speak %s" if role != "main" or lang not in (needs.get("languages") or [])
                        else "speaks %s only as the announcer here") % LANG_NAMES.get(lang, lang))
    if needs.get("piper_model") and not facts.get("piper_model"):
        out.append("needs a Piper voice model you download yourself and name in the voice settings")
    return out


def pick(facts: dict, tab: dict | None = None, broken: dict | None = None) -> dict:
    """{"tier", "main", "announcer", "chain", "why", "passed_over": {tier: [reasons]}, "settings", "beyond"}.
    broken: {engine: why} for installed engines that failed their self-test; each is passed over with its reason."""
    tab = tab or table()
    broken = broken or {}
    tiers = tab["tiers"]
    order = [t for t in tab["order"] if t in tiers]
    if "text" not in order:
        order.append("text")
        tiers.setdefault("text", {"needs": {}, "label": "on-screen text"})
    passed, main = {}, None
    capable = []
    for name in order:
        why = unmet(tiers[name], facts)
        if not why and name in broken:
            why = ["is installed but failed its self-test on this machine: %s" % broken[name]]
        only = str(tiers[name].get("backup_only_for") or "")
        if only and main != only:
            # Kokoro backs Pocket and is never the main voice (the User 2026-10-08). A machine Pocket cannot run
            # (macOS 13, an Intel Mac) keeps say or Audio8; Kokoro is not tried in their place.
            if main is None:
                passed[name] = why or ["backs %s only, and %s is not this machine's voice" % (only, only)]
            continue
        if why:
            if main is None:
                passed[name] = why
            continue
        capable.append(name)
        if main is None:
            main = name
    t = tiers[main]
    # every tier it meets stays reachable, best first (the table's order), and on-screen text is always last; a tier
    # that stands back for her main voice (Audio8 behind Turbo, Kokoro behind Turbo and Audio8) is left out, so a
    # GPU machine's chain and a robust CPU machine's chain stay Pocket next
    chain = [main] + [c for c in capable[1:] if c != "text" and main not in (tiers[c].get("stands_back_for") or [])]
    if main != "text":
        chain.append("text")
    left_out = _gpu_room(chain, tiers, facts)
    chain = [c for c in chain if c not in left_out]
    ann_chain, ann_passed = announcer_chain(facts, tab, broken)
    rec = {"tier": main, "main": main, "announcer": ann_chain[0] if ann_chain and ann_chain[0] != main else "",
           "announcer_chain": ann_chain, "announcer_passed": ann_passed, "chain": chain, "label": t.get("label") or main, "passed_over": passed,
           "settings": dict(t.get("settings") or {}), "install": t.get("install") or ""}
    if left_out:
        rec["left_out"] = left_out
    b = tab.get("beyond") or {}
    g = facts.get("gpu") or {}
    if main in ("turbo", "multilingual") and b.get("gpu_free_gb") and float(g.get("free_gb") or 0) >= float(b["gpu_free_gb"]):
        rec["beyond"] = str(b.get("note") or "")
    rec["why"] = why_line(rec, facts, tiers)
    rec["language"] = language_view(facts, tab, chain, ann_chain)
    return rec


def announcer_chain(facts: dict, tab: dict, broken: dict | None = None) -> tuple[list, dict]:
    """The announcer's order (the tier table's announcer.order: Audio8 on a robust CPU machine, then Pocket, Kokoro,
    say, text), each step this machine can run in the person's language. ([chain], {tier: [why not]})."""
    broken = broken or {}
    tiers = tab["tiers"]
    order = voices_only((tab.get("announcer") or {}).get("order") or ["pocket", "say", "text"])
    out, passed = [], {}
    for name in order:
        t = tiers.get(name) or ({"needs": {}} if name == "text" else None)
        if t is None:
            continue
        why = unmet(t, facts, role="announcer")
        if not why and name in broken:
            why = ["is installed but failed its self-test on this machine: %s" % broken[name]]
        if why:
            passed[name] = why
        else:
            out.append(name)
    if "text" not in out:
        out.append("text")
    return out, passed


def language_view(facts: dict, tab: dict, chain: list, ann_chain: list) -> dict:
    """The language and its fallback order, for the screen: {"code", "name", "from", "order", "here", "text_only",
    "line"}. order: every tier in the table that speaks it, best first; here: the ones this machine runs."""
    lang = facts.get("language") or {"code": "en", "name": "English", "from": "nothing named (English)"}
    code = lang.get("code") or "en"
    speaks = []
    for name in tab["order"]:
        needs = (tab["tiers"].get(name) or {}).get("needs") or {}
        langs = needs.get("main_languages") or needs.get("languages") or ["*"]
        if "*" in langs or code in langs:
            speaks.append(name)
    if "text" not in speaks:
        speaks.append("text")
    voiced = [c for c in chain if c != "text"]
    line = "language: %s (%s, from %s); her voice in it, best first: %s; on this machine: %s" % (
        lang.get("name") or code, code, lang.get("from"), " -> ".join(speaks), " -> ".join(chain) or "text")
    if not voiced:
        line += ". No voice on this machine speaks %s: she shows text only" % (lang.get("name") or code)
    if [c for c in ann_chain if c != "text"] == []:
        line += ". No announcer voice here speaks %s: the announcer's lines are text only" % (lang.get("name") or code)
    return {"code": code, "name": lang.get("name") or code, "from": lang.get("from"), "order": speaks,
            "here": chain, "announcer_here": ann_chain, "text_only": not voiced, "line": line}


def _gpu_room(chain: list, tiers: dict, facts: dict) -> dict:
    """{tier: why} for a GPU voice behind the main GPU voice that the card cannot also hold: both run at once (the
    one behind is the stand-in), so it stays in the chain only when the free GPU memory covers both start gates.
    Said on screen and in the log like any other choice; a bigger card (or the state-home table) keeps it."""
    g = facts.get("gpu") or {}
    free = float(g.get("free_gb") or 0)
    gpu = [c for c in chain if (tiers.get(c, {}).get("needs") or {}).get("gpu") == "cuda"]
    out, used = {}, 0.0
    for c in gpu:
        need = float((tiers[c].get("needs") or {}).get("gpu_free_gb") or 0)
        if used and used + need > free:
            out[c] = ("stays out of the chain behind %s: both on the GPU need %.0f GB free and %s has %.1f GB free"
                      % (gpu[0], used + need, g.get("name") or "the GPU", free))
            continue
        used += need
    return out


def why_line(rec: dict, facts: dict, tiers: dict) -> str:
    main = rec["main"]
    head = "%s: %s" % (tiers[main].get("label") or main, describe(facts))
    nots = ["%s %s" % (str(tiers[n].get("label") or n).split(" (")[0], "; ".join(r)) for n, r in rec["passed_over"].items()
            if not all(x.startswith(("does not speak", "speaks ")) for x in r)]
    return head + (". Not " + ". Not ".join(nots) if nots else "")


# ---- readiness (what can speak right now) -----------------------------------------------------------------------

def _voice_mod(name: str):
    """modules/voice/crissa_voice/<name>, when the voice module is beside this install."""
    vdir = Path(os.environ.get("CRISSA_VOICE_DIR") or (ROOT / "modules" / "voice"))
    if not (vdir / "crissa_voice" / (name + ".py")).is_file():
        return None
    if str(vdir) not in sys.path:
        sys.path.insert(0, str(vdir))
    try:
        import importlib
        return importlib.import_module("crissa_voice." + name)
    except Exception:  # noqa: BLE001
        return None


def installed(engine: str) -> bool | None:
    """Is the engine installed on this machine? None = this engine needs no install (say, text) or can't tell."""
    mod = {"multilingual": "multilingual_setup", "turbo": "turbo_setup", "turbo_mlx": "turbo_mlx_setup",
           "pocket": "pocket_setup", "kokoro": "kokoro_setup", "audio8": "audio8_setup"}.get(engine)
    if not mod:
        return None
    m = _voice_mod(mod)
    try:
        return bool(m and m.installed())
    except Exception:  # noqa: BLE001
        return False


def broken() -> dict:
    """{engine: why} for every installed engine whose install self-test failed (Turbo has one). Never raises."""
    out = {}
    for engine in ("multilingual", "turbo"):
        m = _voice_mod(engine + "_setup")
        try:
            why = m.self_test_failed() if m is not None and m.installed() else ""
        except Exception:  # noqa: BLE001
            why = ""
        if why:
            out[engine] = why
    return out


def missing_line(rec: dict) -> str:
    """One plain line: what is missing for the best voice this machine can run. '' = nothing. Never a command or a
    setting name (the User 2026-10-07: a new user got a terminal command and an env variable): Init installs the voice
    itself (lib/crissa_voice_install.py) and the Connect page can try again."""
    main = rec.get("main")
    tab = table()
    label = lambda c: str((tab["tiers"].get(c) or {}).get("label") or c).split(" (")[0]   # noqa: E731
    if main in ("multilingual", "turbo", "turbo_mlx", "pocket", "kokoro") and installed(main) is False:
        then = next((label(c) for c in rec.get("chain", [])[1:] if installed(c) is not False), "on-screen text")
        return ("%s is the best voice this machine can run and is not installed yet (%s); Init installs it, and "
                "the Connect page can try again. Until then: %s." % (label(main), (tab["tiers"].get(main) or {}).get("size")
                                                             or "a one-time download", then))
    if main == "text":
        return ("no voice this machine can run: she answers in text. Her voices need 64-bit Linux or an Apple "
                "Silicon Mac (Pocket, on the CPU), an NVIDIA GPU (Turbo), or a Mac (its own voice).")
    return ""


# ---- the record -------------------------------------------------------------------------------------------------

def read(home: Path | None = None) -> dict | None:
    try:
        d = json.loads(record_path(home).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("schema") == SCHEMA else None
    except (OSError, ValueError):
        return None


def _write(rec: dict, home: Path | None = None) -> None:
    p = record_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


# A SAVED PICK FROM AN EARLIER RELEASE IS MADE AGAIN (2026-10-07, .101): the pick is saved once and later starts read it,
# so a machine first started on an older release kept that release's chain ([pocket, kokoro, text] from the 10-07
# morning tag) under the new code - Crissa's backup was named Kokoro, which no install there has. A pick Init made
# (never the person's own, picked_by "user") is compared with what this release picks for the same machine; when they
# differ it is made again, and the old record is kept beside it (voice-tier.prev.json). Kill CRISSA_VOICE_REPICK=0.
PICK_KEYS = ("main", "chain", "announcer", "announcer_chain")
PREV_NAME = "voice-tier.prev.json"


def stale_pick(rec: dict | None, home: Path | None = None, bad: dict | None = None) -> str:
    """'' when the saved pick is what this release picks for the same machine (or is the person's own); else the old
    chain, in words, for the log."""
    if not rec or rec.get("picked_by") == "user":
        return ""
    if str(os.environ.get("CRISSA_VOICE_REPICK", "1")).strip().lower() in ("0", "off", "false", "no"):
        return ""
    try:
        now = pick(rec.get("facts") or probe(), table(home), bad if bad is not None else broken())
    except Exception:  # noqa: BLE001  a pick that cannot be made now leaves the saved one as it is
        return ""
    if all((rec.get(k) or None) == (now.get(k) or None) for k in PICK_KEYS):
        return ""
    return "her voice %s, the announcer %s" % (" -> ".join(rec.get("chain") or [str(rec.get("main") or "?")]),
                                              " -> ".join(rec.get("announcer_chain") or ["text"]))


def keep_previous(home: Path | None = None) -> None:
    p = record_path(home)
    try:
        p.with_name(PREV_NAME).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass


def ensure(home: Path | None = None, *, reprobe: bool = False, facts: dict | None = None, log=None,
           faces_live: bool = False) -> dict:
    """Init's voice step. The house: its config, nothing written. A pack: the saved pick, or probe once and save it.
    Returns the record plus `say`: the lines to show (what was picked and why, what is missing)."""
    if is_house():
        rec = house_pick()
        rec["say"] = ["voice: the house's own (%s, backup %s), from config/house-machine.json"
                      % (rec["main"], rec.get("backup") or "none")]
        return rec
    rec = None if reprobe else read(home)
    bad = broken()
    if rec is not None and rec.get("picked_by") != "user" and any(c in bad for c in (rec.get("chain") or [rec.get("main")])):
        rec = None                                # a saved pick whose voice now fails its self-test is made again
    earlier = stale_pick(rec, home, bad) if rec is not None else ""
    if rec is not None and not earlier and names_a_non_voice(rec):
        # Audio8 as her voice or the announcer's, from an earlier release or her own pick: picked again without it
        earlier = "her voice %s, the announcer %s" % (" -> ".join(rec.get("chain") or [str(rec.get("main") or "?")]),
                                                     " -> ".join(rec.get("announcer_chain") or ["text"]))
    if earlier:
        keep_previous(home)                       # kept beside it, never deleted
        rec = None
    if rec is None:
        f = facts or probe()
        rec = dict(pick(f, table(home), bad), schema=SCHEMA, at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   facts=f, picked_by="init")
        _write(rec, home)
        fresh = True
    else:
        fresh = False
    say = []
    if earlier:
        say.append("voice: the saved pick came from an earlier release (%s); picked again for this one (the old "
                   "pick is kept in %s)" % (earlier, record_path(home).with_name(PREV_NAME)))
    for name, why in bad.items():
        say.append("voice: %s failed its self-test on this machine (%s); %s"
                   % (name, why, "it stays your choice, so it may not speak" if rec.get("picked_by") == "user"
                      and rec.get("main") == name else "%s speaks instead" % rec.get("main")))
    if rec.get("picked_by") == "user":
        say.append("voice: %s (your choice; Init would pick %s)" % (rec["main"], rec.get("init_pick") or "?"))
    else:
        say.append("voice: %s%s - %s" % (rec["main"], " (picked now)" if fresh else "", rec.get("why") or ""))
    if rec.get("beyond"):
        say.append("voice: " + rec["beyond"])
    for name, why in (rec.get("left_out") or {}).items():
        say.append("voice: %s %s" % (name, why))
    lv = rec.get("language") or {}
    if lv.get("line"):
        say.append("voice: " + lv["line"])
    for name, why in (rec.get("announcer_passed") or {}).items():
        if not all(x.startswith(("does not speak", "speaks ")) for x in why):
            say.append("voice: announcer: not %s (%s)" % (name, "; ".join(why)))
    say.append("voice: the announcer: %s" % " -> ".join(rec.get("announcer_chain") or ["text"]))
    fc = face_check(rec, live=faces_live)   # Init asks for the live test; a plain start only plans
    rec["face_voices"] = fc   # which voice each face (crissa, lila, announcer) speaks with
    for line in fc.get("say") or []:
        say.append("voice: " + line)
    miss = missing_line(rec)
    if miss:
        say.append("voice: " + miss)
    rec = dict(rec, say=say)
    if log:
        for line in say:
            log(line)
    return rec


def override(engine: str, home: Path | None = None, by: str = "user") -> dict:
    """The person's own pick (scripts/crissa-voice-tier.py --pick). Kept until --probe or --reset; Init never
    overwrites it. The machine's own pick is kept beside it so the screen can say both.
    `by="machine"` (round 29, .101 2026-10-17): Setup's machine check keeps its measured voice as HER pick, never as
    the person's, so a later start is free to make it again (it had written Kokoro as "your choice")."""
    engine = engine.strip().lower()
    if engine not in ENGINES:
        raise ValueError("unknown voice %r (one of %s)" % (engine, ", ".join(ENGINES)))
    if not_a_voice(engine):
        raise ValueError("%s is her ear (speech in), not a voice: Pocket, Kokoro, say or text speak" % engine)
    if (table(home)["tiers"].get(engine) or {}).get("slot") == V2:
        raise ValueError("%s is a v2 slot: its engine is not in this release yet" % engine)
    facts = probe()
    base = read(home) or dict(pick(facts, table(home)), schema=SCHEMA, facts=facts, picked_by="init")
    init_pick = base.get("init_pick") or base.get("main")
    tab = table(home)
    capable = [c for c in tab["order"] if c in tab["tiers"] and not unmet(tab["tiers"][c], base.get("facts") or {})]
    chain = [engine] + [c for c in capable if c != engine]
    warn = unmet(tab["tiers"].get(engine) or {}, base.get("facts") or {})
    rec = dict(base, main=engine, tier=engine, chain=chain, picked_by=("machine" if by == "machine" else "user"),
               init_pick=init_pick,
               at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), warn=warn,
               announcer=(tab["tiers"].get(engine) or {}).get("announcer", "") if engine in ("turbo", "multilingual") else "",
               settings=dict((tab["tiers"].get(engine) or {}).get("settings") or {}))
    _write(rec, home)
    try:                                          # the Connect page's voice line says this pick now (LL, 2026-10-11)
        import crissa_voice_install
        crissa_voice_install.note_pick(rec, home)
    except Exception:  # noqa: BLE001  the pick is kept whatever the page's line says
        pass
    return rec


def chain(rec: dict | None = None, home: Path | None = None) -> list[str]:
    """The engines to try, best first, when nothing names one. No record (Init never ran): every engine in order;
    each is still tried only when ready, so an unmet one costs a refused loopback connect and no more."""
    r = rec if rec is not None else read(home)
    if not r:
        return voices_only(DEFAULT_ORDER)
    out = voices_only([str(x) for x in (r.get("chain") or []) if str(x) in ENGINES])
    if "text" not in out:
        out.append("text")
    return out


def settings(engine: str, home: Path | None = None) -> dict:
    """The tier's settings for an engine: the table's, with the record's on top. ROUND 23 (the User 2026-10-15, .101: a
    record Setup wrote with `settings: {}` hid every default): an empty or partial record never empties them."""
    r = read(home) or {}
    base = dict((table(home)["tiers"].get(engine) or {}).get("settings") or {})
    if r.get("main") == engine and isinstance(r.get("settings"), dict):
        base.update(r["settings"])
    return base


# ---- the house --------------------------------------------------------------------------------------------------

def house_pick(paths: Path | None = None) -> dict:
    """The house's voice from its house config: tts_main, tts_backup, tts_announcer.
    The same names a pack's record carries; nothing here changes what the house does (house-speak reads them)."""
    hv = (lambda k: crissa_home.house_value(k, paths)) if crissa_home else (lambda k: "")
    main = hv("tts_main") or "text"         # a house config that names no voice: nothing speaks but the screen
    backup = hv("tts_backup")
    return {"schema": SCHEMA, "tier": main, "main": main, "backup": backup, "announcer": hv("tts_announcer"),
            "chain": [main] + ([backup] if backup else []), "picked_by": "house config"}


# ---- the three faces (2026-10-06, the User: the Wall needs crissa, lila and the announcer to speak) ------------------

FACES = ("crissa", "lila", "announcer")


# HER OWN VOICE IS NOT A FALLBACK (round 33, .101 10-19: Setup logged "FACE FALLBACK: crissa - crissa's Pocket voice
# 'crissa' is not an allowed stock voice" and "faces: crissa: Pocket's eve" while Pocket spoke her lines as crissa). The
# plan checked her pick against Pocket's stock voices only; her own voice ships beside them (pocket_setup.OWN_VOICES,
# pinned by sha256) and Pocket loads it. The plan now names what Pocket loads for her face (her-voice.json's style),
# and her own voice counts when its file is the pinned one. Kill CRISSA_OWN_VOICE_REPORT=0 (the stock-only check).
def own_voice_report_on() -> bool:
    return os.environ.get("CRISSA_OWN_VOICE_REPORT", "1").strip().lower() not in ("0", "off", "false", "no")


def _own_ok(m, name: str) -> bool:
    try:
        return bool(m.own_voice_ok(name))
    except Exception:  # noqa: BLE001  an unreadable file is not her voice
        return False


def _plan_face(engine: str, face: str, lang: str) -> dict:
    """{"voice", "fallback"} for one face on one engine, from what is installed (no sound is made)."""
    builtin = {"turbo": "Turbo's built-in voice", "turbo_mlx": "Turbo MLX's built-in voice",
               "multilingual": "Multilingual's built-in voice"}
    try:
        import crissa_tts as T  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        T = None
    if engine in ("turbo", "turbo_mlx", "multilingual"):
        # turbo_mlx reuses the same stock face clips (faces.py); until its install is verified it falls to built-in
        mod_name = "turbo_setup" if engine == "turbo_mlx" else engine + "_setup"
        m = _voice_mod(mod_name)
        try:
            p = (m.face_plan() if m is not None and hasattr(m, "face_plan") else {}).get(face)
        except Exception:  # noqa: BLE001
            p = None
        return p or {"voice": builtin[engine], "fallback": "%s's face plan could not be read" % engine}
    if engine == "pocket":
        v = T.face_default("pocket", face) if T else ""
        m = _voice_mod("pocket_setup")
        allowed = set(getattr(m, "VOICES", {}) or {}) if m is not None else set()
        if own_voice_report_on() and m is not None:
            v = pocket_face_voice(face, None) or v         # round 33: the voice Pocket loads for her face
            allowed |= {n for n in (getattr(m, "OWN_VOICES", {}) or {}) if _own_ok(m, n)}
        if v and (not allowed or v in allowed):
            return {"voice": "Pocket's %s" % v, "fallback": ""}
        dv = str(getattr(m, "DEFAULT_VOICE", "") or "eve")
        return {"voice": "Pocket's %s" % dv, "fallback": "%s's Pocket voice %r is not an allowed stock voice" % (face, v)}
    if engine == "audio8":
        m = _voice_mod("audio8_setup")
        v = T.face_default("audio8", face) if T else ""
        if m is not None and hasattr(m, "registered") and m.installed() and not m.registered(v):
            why = ((m.record().get("face_why") or {}).get(face) or (m.record().get("voice_why") or "")
                   or "%r is not registered" % v)
            b = (getattr(T, "AUDIO8_BACKUP", {}) or {}).get(face)
            if b and m.registered(b):              # crissa without her own voice registered: jane (2026-10-08)
                return {"voice": "Audio8's %s" % b, "fallback": "%s's Audio8 voice %r is missing: %s" % (face, v, why)}
            if face in getattr(T, "SHE_FACES", ()):    # her line never takes the announcer's voice: her backup speaks it
                return {"voice": "her backup voice", "fallback": "%s's Audio8 voice %r is missing: %s" % (face, v, why)}
            return {"voice": "Audio8's anna", "fallback": "%s's Audio8 voice %r is missing: %s" % (face, v, why)}
        return {"voice": "Audio8's %s" % v, "fallback": ""}
    if engine == "kokoro":
        return {"voice": "Kokoro's %s" % ((T.face_default("kokoro", face) if T else "") or "af_heart"), "fallback": ""}
    if engine == "say":
        v = T.face_default("say", face) if T else ""
        have = T.say_voices() if T else {}
        if have and lang != "en":
            loc = [n for n, l in have.items() if l.lower().startswith(lang)]
            return ({"voice": "the Mac's %s" % loc[0], "fallback": ""} if loc else
                    {"voice": "on-screen text", "fallback": "the Mac has no %s voice installed" % LANG_NAMES.get(lang, lang)})
        if have and v not in have:
            return {"voice": "the Mac's default voice", "fallback": "%s's voice %s is not installed on this Mac" % (face, v)}
        return {"voice": "the Mac's %s" % (v or "default voice"), "fallback": ""}
    if engine == "piper":
        return {"voice": "your Piper model", "fallback": "a Piper model is one speaker: every face sounds the same"}
    return {"voice": "on-screen text", "fallback": "no voice speaks here"}


def face_check(rec: dict, speak=None, live: bool | None = None) -> dict:
    """Init's three-face check: which voice each face uses at the chosen tier, and whether it SPOKE.
    speak(face) -> (engine that spoke, seconds) is the live test (default: one short line through lib/crissa_tts
    when Crissa's voice answers); a face that spoke through another engine, or not at all, is a visible fallback.
    {"faces": {face: {"engine", "voice", "fallback", "spoke", "spoke_with"}}, "say": [lines], "ok": bool}"""
    lang = ((rec.get("facts") or {}).get("language") or {}).get("code") or "en"
    main = str(rec.get("main") or "text")
    ann = (rec.get("announcer_chain") or [main])[0]
    out, say = {}, []
    for face in FACES:
        engine = ann if face == "announcer" else main
        f = dict(_plan_face(engine, face, lang), engine=engine)
        out[face] = f
    if speak is None and live is not False:
        speak = _live_speaker()
    if speak is not None:
        for face, f in out.items():
            try:
                spoke_with, secs = speak(face)
                f.update(spoke=True, spoke_with=spoke_with, seconds=round(float(secs), 2))
                if spoke_with != f["engine"]:
                    f["fallback"] = (f.get("fallback") + "; " if f.get("fallback") else "") + \
                        "%s did not speak, %s spoke instead" % (f["engine"], spoke_with)
            except Exception as e:  # noqa: BLE001
                f.update(spoke=False, fallback=(f.get("fallback") + "; " if f.get("fallback") else "") +
                         "did not speak: %s" % str(e)[:160])
    words = []
    for face, f in out.items():
        w = "%s: %s on %s" % (face, f["voice"], f["engine"])
        if f.get("spoke") is True:
            w += " (spoke, %.1f s)" % f.get("seconds", 0)
        words.append(w)
    say.append("faces: " + "; ".join(words))
    for face, f in out.items():
        if f.get("fallback"):
            say.append("FACE FALLBACK: %s - %s" % (face, f["fallback"]))
    return {"faces": out, "say": say, "ok": not any(f.get("fallback") for f in out.values()),
            "tested_live": speak is not None}


def _live_speaker():
    """One short line per face through lib/crissa_tts, only when the pack's voice answers right now (Init runs on a
    live Wall); None when nothing answers (the plan above still says what each face will use)."""
    try:
        import crissa_tts as T  # noqa: PLC0415
        cfg = T.config()
    except Exception:  # noqa: BLE001
        return None
    if not cfg or not cfg.get("engine") or cfg.get("engine") == "text":
        return None

    def speak(face):
        import time as _t  # noqa: PLC0415
        t0 = _t.monotonic()
        spoke = {}
        orig = T._note_spoke

        def note(name, *a, **k):
            spoke["engine"] = name
            return orig(name, *a, **k)
        T._note_spoke = note
        try:
            T.synth("Hello.", face, cfg, timeout=60.0)
        finally:
            T._note_spoke = orig
        return spoke.get("engine") or cfg.get("engine"), _t.monotonic() - t0
    return speak
