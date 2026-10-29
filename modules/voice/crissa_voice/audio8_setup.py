"""The announcer's voice: Audio8 TTS on the CPU (2026-10-06, the User: Audio8 stays the announcer on robust machines).

Audio8 TTS (Edge0, formerly Audio8-AI; 0.6B DualAR, ONNX INT4; code and weights Apache-2.0, checked 2026-10-06 against
github.com/Edge0-AI/Audio8_TTS @ 7b6a5a15 and huggingface.co/Edge0/Audio8-TTS-Preview-0.6B-ONNX-INT4 @ 818569c6).
CPU only, no torch: onnxruntime, about 1.0-1.5 GiB of RAM while speaking, about 0.97 GB of model with the voice
registration encoder. It speaks yue, zh, nl, en, fr, de, it, ja, ko, pl and es. It ships NO stock voice: a voice is
registered from a reference clip and its transcript, here, at install.

The pack's announcer voices are config (the tier table's announcer.audio8): `voice` (A) and `voice_b` (B), and
`alternate` "day" makes them take turns, A on even days and B on odd ones (date.toordinal() % 2), as the house's own
announcer does; an empty voice_b (the shipped default) is A every day. Each must be a pack voice (PACK_VOICES):
    anna       A, DEFAULT. VCTK speaker p228 (English, Southern England; CSTR, University of Edinburgh), CC BY 4.0,
               via kyutai/tts-voices (faces.py: the same clip Turbo clones for the announcer and Pocket's anna is
               made from), with VCTK's own transcript of that sentence.
    vctk_p283  PROPOSED B (voice_b_proposed; the person sets voice_b). VCTK speaker p283 (Irish, Cork), CC BY 4.0, the
               same corpus, sentence and licence. Audio8 itself ships no stock voice at all (checked 2026-10-07:
               no voices in Edge0-AI/Audio8_TTS or the ONNX model repo; a voice is registered from a clip).
The faces' voices, registered too: jane (VCTK p339, Crissa's backup), eve (VCTK p361, Lila's backup; her voice until
2026-10-12) and
    crissa_imagine  CRISSA'S OWN VOICE (the User 2026-10-08): registered from her clip that ships in the pack
               (modules/voice/faces/crissa-imagine.wav, cut from the sound of two of her Grok Imagine films; "Created
               with Grok"; provenance in crissa-imagine.json) and its transcript (crissa-imagine.txt). Registration is
               Audio8's own runtime (arktts_runtime.registration, Apache-2.0), the same as for the VCTK voices.
               If it cannot be registered she speaks as jane, and the log and Init say why.
    lila_imagine  LILA'S OWN VOICE (the User 2026-10-12), the same way: modules/voice/faces/lila-imagine.wav, cut
               from the sound of one of the house's Grok Imagine films, and lila-imagine.txt. Without it she speaks
               as eve.
The house's own announcer voices (alternating by day) are the house's Audio8 service and config, never this
installer; a house clone (a real person's voice) never ships, and voice_name() refuses any name that is not a pack
voice.

Fetched on the person's machine, every file pinned and sha256-checked; nothing is redistributed:
    <crissa home>/audio8/env      the env (pyenv.py: micromamba, Python pinned); every package from locks/audio8-<target>-py312.txt
    <crissa home>/audio8/code     the runtime (onnx_runtime/arktts_runtime) at CODE_REV, with LICENSE and NOTICE
    <crissa home>/audio8/model    the ONNX model at MODEL_REV
    <crissa home>/audio8/voices   the registered voice profiles
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import egress, faces, locks, pyenv


def _home() -> Path:
    env = os.environ.get("CRISSA_HOME")
    return Path(env).expanduser() if env else Path.home() / ".crissa"


ROOT = _home() / "audio8"
ENV = ROOT / "env"
CODE = ROOT / "code"
MODEL = ROOT / "model"
VOICES = ROOT / "voices"
LOG = ROOT / "server.log"
PID = ROOT / "server.pid"
DONE = ROOT / "installed.json"
CODE_REPO = "Edge0-AI/Audio8_TTS"
CODE_REV = "7b6a5a15e4529da4e37463736de1d70753ceb2fb"   # 2026-08-05
MODEL_REPO = "Edge0/Audio8-TTS-Preview-0.6B-ONNX-INT4"
MODEL_REV = "818569c6b832118ad68d61bbd873abe250fcd68a"  # 2026-08-02
CREDIT = "Announcer: Audio8 TTS by Edge0 (formerly Audio8), Apache-2.0"
PACKAGES = ["numpy==2.4.3", "onnxruntime==1.24.4", "soundfile==0.13.1", "scipy==1.17.1", "fastapi==0.135.2",
            "uvicorn==0.42.0", "pydantic>=2.9,<3", "python-multipart==0.0.22"]
MIN_MACOS = (14, 0)    # onnxruntime 1.24's Mac wheels
CODE_FILES = {   # path in the repo at CODE_REV -> (sha256, bytes)
    "LICENSE": ("c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4", 11357),
    "NOTICE": ("819129644b28fb066221d782dd27a6fe1d35f33f46718d4e61570614fb38854e", 95),
    "onnx_runtime/arktts_runtime/__init__.py": ("d92cf94702c27a68f61f3c2e93229e61613fcbecb24d4845b97605cd9accf06c", 155),
    "onnx_runtime/arktts_runtime/cli.py": ("6b602e94f81792fef2083f6018627c09ee3d08c4fa9812976bd44bf9cc49970b", 1898),
    "onnx_runtime/arktts_runtime/prompt.py": ("62df836597d88d05c7879fee5c1d2b3d864db4331aa58fd6641f2df93f377a04", 3204),
    "onnx_runtime/arktts_runtime/registration.py": ("aadaffd6b3024408399af9de042afab312ba46892d7fd38af954ce182b7ec89f", 5919),
    "onnx_runtime/arktts_runtime/runtime.py": ("e0cab4f04d2138a5e49bcdb632aad777f270c1bc8dbbb810430d1b08843a917f", 12148),
    "onnx_runtime/arktts_runtime/service.py": ("345f4e8165584ae7237d6dc3a6b7209862f348f526fef5a869ba71813ca080e4", 27864),
    "onnx_runtime/arktts_runtime/voices.py": ("71dbde111ff2536f57f11099e3be2b20ee6807390b126ccfe39d22a022088277", 1515),
    "onnx_runtime/requirements.txt": ("22ee6bf2c61c9fe3f4fad6f82f21b108c1ea5bfbc7659c30eb127628417df9cb", 140),
}
MODEL_FILES = {  # path in the model repo at MODEL_REV -> (sha256, bytes)
    "codec_decoder_fp16.onnx": ("6e379be31db6c1b0c111e0e3d2aeb10717ee96b197462b926de411e75a1fd019", 594319),
    "codec_decoder_fp16.onnx.data": ("18838f686aa7c1528fb69ec11e1ab404fdc4dc823d13219abfd4b327988527c0", 260741440),
    "config.json": ("ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356", 3),
    "fast_ar_int4.onnx": ("808c5a0c95c28d90337d925a9a8f6075f7ff8eb7b3080d2b34c4133479a6dc94", 156318),
    "fast_ar_int4.onnx.data": ("183be0c9f26b27c605b92a0875beb93f8f98b771f27f65cab133c73610868325", 35055104),
    "registration/codec_encoder_fp16.onnx": ("e856d7999442cdc8f1f2ed0d2c055532cf359f0dd6d9a44fd4b98584c5d5dfa5", 940787),
    "registration/codec_encoder_fp16.onnx.data": ("19c740fcc4d45aa2546e9ab86e31c6200955c4b0a139758296fbf1064bf009cd", 414425088),
    "registration/registration_manifest.json": ("36ef9d2f435f0f7b5ab66dc78a44411a24c0ab9e3a2c63738babe575747a584f", 165),
    "runtime_manifest.json": ("6473ae7d0106a2e369e442c72a71d2d46d8fbd3fe18c80d80b1b46e4aa241930", 1080),
    "slow_ar_int4.onnx": ("0cf7701d6da81f888b49ba6e752445d9786a9915ba30dcf084f7743bdda96834", 900218),
    "slow_ar_int4.onnx.data": ("bb217f654039692204386b7e5b74d98e9268863bb664a849aa123a9053d6c824", 290267090),
    "tokenizer/tokenizer.json": ("f24e08099d45a8adf3f52f5f0b03276e433bb9d689bb15fcbcc48ce58744588b", 12217872),
}
# The voices a pack may register: name -> where its clip and transcript come from. Licences checked 2026-10-06.
VCTK_23 = ("If the red of the second bow falls upon the green of the first, the result is to give a bow with an "
           "abnormally wide yellow band, since red and green light when mixed form yellow.")
PACK_VOICES = {
    "anna": {"clip": "announcer", "text": VCTK_23, "licence": "CC BY 4.0", "face": "announcer",
             "who": "VCTK corpus speaker p228, CSTR, University of Edinburgh (kyutai/tts-voices vctk, enhanced)"},
    "eve": {"clip": "eve", "text": VCTK_23, "licence": "CC BY 4.0", "face": "lila",
            "who": "VCTK corpus speaker p361, CSTR, University of Edinburgh (kyutai/tts-voices vctk, enhanced)"},
    "jane": {"clip": "jane", "text": VCTK_23, "licence": "CC BY 4.0", "face": "crissa",
             "who": "VCTK corpus speaker p339, CSTR, University of Edinburgh (kyutai/tts-voices vctk, enhanced)"},
    "vctk_p283": {"clip": "vctk_p283", "text": VCTK_23, "licence": "CC BY 4.0", "face": "announcer",
                  "who": "VCTK corpus speaker p283 (Irish, Cork), CSTR, University of Edinburgh (kyutai/tts-voices "
                         "vctk, enhanced)"},
    # Crissa's own voice (2026-10-08): her clip ships in the pack (faces.STOCK["crissa"], SHIPPED); its transcript too.
    "crissa_imagine": {"clip": "crissa", "text_face": "crissa", "licence": faces.STOCK["crissa"]["licence"], "face": "crissa",
                     "who": faces.STOCK["crissa"]["who"]},
    # Lila's own voice (2026-10-12): her clip ships in the pack too (faces.STOCK["lila"], SHIPPED), with its transcript.
    "lila_imagine": {"clip": "lila", "text_face": "lila", "licence": faces.STOCK["lila"]["licence"], "face": "lila",
                     "who": faces.STOCK["lila"]["who"]},
}
FACE_VOICES = {"lila": "lila_imagine", "crissa": "crissa_imagine"}   # each face's own Audio8 voice
FACE_BACKUP = {"crissa": "jane", "lila": "eve"}            # registered too: each speaks as her backup if her own is not there
DEFAULT_VOICE = "anna"


def env_python() -> Path:
    return ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed() -> bool:
    if not (DONE.is_file() and env_python().exists()):
        return False
    files = [(CODE / r, n) for r, (_, n) in CODE_FILES.items()] + [(MODEL / r, n) for r, (_, n) in MODEL_FILES.items()]
    return all(p.is_file() and p.stat().st_size == n for p, n in files)


def record() -> dict:
    try:
        return json.loads(DONE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def unsupported(version=None, plat: str | None = None, mac=None) -> str:
    """'' when this machine can run it, else why not."""
    v = tuple(version or pyenv.target_version())
    plat = plat or sys.platform
    if tuple(v[:2]) < (3, 11):
        return "Audio8 needs Python 3.11 or later (its env would run %d.%d). %s" % (v[0], v[1], pyenv.ADVICE)
    if plat == "darwin":
        m = mac if mac is not None else platform.mac_ver()[0]
        try:
            if tuple(int(x) for x in (m or "0").split(".")[:2]) < MIN_MACOS:
                return "Audio8 needs macOS 14 or later on a Mac (onnxruntime 1.24's wheels); this is %s" % (m or "?")
        except ValueError:
            pass
        return ""
    if not plat.startswith("linux"):
        return "Audio8 here runs on Linux or a Mac (macOS 14+)"
    return ""


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _fetch(url: str, dest: Path, timeout: float = 600.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with egress.urlopen(url, timeout, "the announcer download") as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(tmp, dest)


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def wanted() -> dict:
    """{local path: (url, sha256)} for every file: the runtime code and the model."""
    out = {}
    for rel, (sha, _) in CODE_FILES.items():
        out[CODE / rel] = ("https://raw.githubusercontent.com/%s/%s/%s" % (CODE_REPO, CODE_REV, rel), sha)
    for rel, (sha, _) in MODEL_FILES.items():
        out[MODEL / rel] = ("https://huggingface.co/%s/resolve/%s/%s" % (MODEL_REPO, MODEL_REV, rel), sha)
    return out


def get_files(fetch=_fetch, log=print) -> None:
    for p, (url, sha) in wanted().items():
        if p.is_file() and _sha256(p) == sha:
            continue
        log("audio8: fetching %s …" % p.relative_to(ROOT))
        fetch(url, p)
        got = _sha256(p)
        if got != sha:
            p.unlink()
            raise RuntimeError("%s does not match its pinned sha256 (got %s); nothing was installed" % (p.name, got[:16]))


def _audio8_cfg(tab: dict | None = None) -> dict:
    if tab is None:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
            import crissa_voice_tiers as VT  # noqa: PLC0415
            tab = VT.table()
        except Exception:  # noqa: BLE001
            tab = {}
    return dict((((tab or {}).get("announcer") or {}).get("audio8") or {}))


def voice_name(tab: dict | None = None) -> str:
    """The pack's announcer voice A on Audio8: the tier table's announcer.audio8.voice (default anna). A name that
    is not a pack voice (a house clone, a typo) is refused: anna."""
    v = str(_audio8_cfg(tab).get("voice") or DEFAULT_VOICE).strip().lower()
    return v if v in PACK_VOICES else DEFAULT_VOICE


def voice_names(tab: dict | None = None) -> list[str]:
    """[A] or [A, B]: the announcer's voices this install registers. B is announcer.audio8.voice_b when it is a pack
    voice other than A and `alternate` is "day"; anything else is A alone."""
    cfg = _audio8_cfg(tab)
    a = voice_name(tab)
    b = str(cfg.get("voice_b") or "").strip().lower()
    if b and b != a and b in PACK_VOICES and str(cfg.get("alternate") or "day").strip().lower() == "day":
        return [a, b]
    return [a]


def voice_source(name: str) -> tuple[Path | None, str, str]:
    """(clip, transcript, why-not) for a pack voice."""
    v = PACK_VOICES.get(name)
    if v is None:
        return None, "", "%r is not a pack voice (%s)" % (name, ", ".join(PACK_VOICES))
    clip = faces.path(v["clip"])
    text = v.get("text") or ""
    if v.get("text_face"):
        text = faces.transcript(v["text_face"])
    if v.get("text_file"):
        try:
            text = (faces.folder() / v["text_file"]).read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
    if faces.switched_off(v["clip"]):
        return None, text, "%s's own clip is switched off (CRISSA_LILA_OWN_VOICE=0)" % v["clip"]
    if not clip.is_file():
        return None, text, "%s's clip is not in %s" % (name, clip.parent if faces.shipped(v["clip"]) else faces.folder())
    if faces.shipped(v["clip"]) and not faces.present(v["clip"]):
        return None, text, "%s's clip %s does not match its sha256" % (name, clip.name)
    if not text:
        return None, "", "%s has no transcript" % name
    return clip, text, ""


REGISTER = r'''
import json, sys
from pathlib import Path
code, model, voices, clip, name, text = sys.argv[1:7]
sys.path.insert(0, str(Path(code) / "onnx_runtime"))
from arktts_runtime.registration import VoiceRegistration
fp = json.loads((Path(model) / "runtime_manifest.json").read_text())["model_fingerprint"]
r = VoiceRegistration(Path(model) / "registration", Path(voices), str(fp))
out = r.register(Path(clip).read_bytes(), Path(clip).name, text, name, True)
print(json.dumps({"ok": True, "name": name}))
'''


def register(name: str, run=_run, log=print) -> str:
    """Register one pack voice from its clip and transcript. '' on success, else why it could not."""
    clip, text, why = voice_source(name)
    if why:
        return why
    VOICES.mkdir(parents=True, exist_ok=True)
    try:
        run([str(env_python()), "-c", REGISTER, str(CODE), str(MODEL), str(VOICES), str(clip), name, text])
    except (OSError, subprocess.SubprocessError) as e:
        err = getattr(e, "stderr", "") or str(e)
        return "registering %s failed: %s" % (name, str(err).strip().splitlines()[-1:] or e)
    log("audio8: registered %s's voice %r (%s, %s)" % ("the announcer" if PACK_VOICES[name]["face"] == "announcer"
                                                          else PACK_VOICES[name]["face"], name, PACK_VOICES[name]["who"],
                                                          PACK_VOICES[name]["licence"]))
    return ""


def registered(name: str) -> bool:
    return (VOICES / name).is_dir() and any((VOICES / name).iterdir())


def make_env(run=_run, log=print) -> str:
    log("audio8: " + pyenv.make(ENV, run, log))
    return locks.pip_install(env_python(), "audio8", run, log, fallback=[PACKAGES], version=pyenv.target_version())


def install(log=print) -> dict:
    why = unsupported()
    if why:
        raise RuntimeError(why)
    egress.refuse_install("the announcer (Audio8)")
    ROOT.mkdir(parents=True, exist_ok=True)
    log("audio8: building the Python env (onnxruntime 1.24, no torch) from %s …" % locks.target())
    how = make_env(log=log)
    log("audio8: runtime %s @ %s and model %s @ %s (about 0.97 GB) …" % (CODE_REPO, CODE_REV[:8], MODEL_REPO, MODEL_REV[:8]))
    get_files(log=log)
    names = voice_names()
    got = faces.fetch_all(_fetch, log, "audio8", extra=("jane",) + tuple(n for n in names if n in faces.EXTRA))
    rec = {"code": CODE_REPO, "code_rev": CODE_REV, "model": MODEL_REPO, "rev": MODEL_REV, "installed_by": how,
           "credit": CREDIT, "target": locks.target(), "clips": got}
    DONE.write_text(json.dumps(rec) + "\n")
    name = voice_name()
    why = register(name, log=log)
    if why and name != DEFAULT_VOICE:
        log("audio8: %s; the default voice %s is registered instead" % (why, DEFAULT_VOICE))
        why2 = register(DEFAULT_VOICE, log=log)
        why = why + ("; " + why2 if why2 else "")
    face_why, backup_why = {}, {}
    for face, extra in FACE_VOICES.items():    # lila (when named) and crissa (her own voice on a robust CPU machine)
        w = register(extra, log=log)
        if w:
            face_why[face] = w
            log("audio8: %s; %s speaks as %s" % (w, face, FACE_BACKUP.get(face, "her backup voice")))
    for face, extra in FACE_BACKUP.items():    # jane / eve: a face's voice whenever her own is not registered
        w = register(extra, log=log)
        if w:
            backup_why[face] = w
    voice_b, b_why = (names[1] if len(names) > 1 else ""), ""
    if voice_b:                                # the alternating day's voice; A speaks every day without it
        b_why = register(voice_b, log=log)
        if b_why:
            log("audio8: %s; voice A (%s) speaks every day until it is there" % (b_why, name))
    rec = dict(rec, voice=name, voice_b=voice_b, voice_b_why=b_why, voice_why=why, face_why=face_why,
               backup_why=backup_why,
               at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    DONE.write_text(json.dumps(rec) + "\n")
    if why:
        log("audio8: ANNOUNCER VOICE NOT READY: %s. Pocket says the announcer's lines until it is." % why)
    else:
        log("audio8: installed in %s; the announcer speaks as %r" % (ROOT, name))
    return rec


def audio8_threads(cores: int | None = None) -> int:
    """ONNX threads for the AR model, sized to THIS machine (the User 2026-10-08: "adjust to env, not default to
    Apple"). Measured 2026-10-08 on the reference 16-core/32-thread box, engine 7b6a5a1, the same 82-char line:
    the AR decode runs 1.49x real time at 3 threads, 1.42x at 4, 1.56x at 5, 1.08x at 6, 1.28x at 8 and 0.48x at
    16 (more threads than the int4 matmuls can feed, and they fight the Wall). So: every core but one up to 5, and
    never more; CRISSA_AUDIO8_THREADS overrides. Checked again 2026-10-10 for .101's 8-core / 16-thread CPU (the
    rule gives it 5): the pack's own Audio8, pinned to an 8-core / 16-thread slice of the reference CPU, one warm-up
    line then three timed streams of the same line, real-time factor (wall over audio) 1.09 at 5 threads, 1.28 at 8,
    1.77 at 12 and 2.29 at 15 - more threads are slower here too, so the rule stands. The codec is the slow half (2.4x real time at any thread count,
    fp32 no faster) and is why the stream path cannot keep real time on a CPU: see crissa-voice-tiers.json
    tiers.audio8.measured."""
    n = int(cores if cores is not None else (os.cpu_count() or 2))
    return max(1, min(5, n - 1))


def server_env(base: dict | None = None) -> dict:
    e = dict(os.environ if base is None else base)
    e.update(ARKTTS_MODEL_DIR=str(MODEL), ARKTTS_VOICES_DIR=str(VOICES), ARKTTS_REGISTRATION_DIR=str(MODEL / "registration"),
             ARKTTS_PRECISION="int4", ARKTTS_CODEC_PRECISION="fp16",
             ARKTTS_THREADS=str(e.get("CRISSA_AUDIO8_THREADS") or audio8_threads()),
             PYTHONPATH=os.pathsep.join([str(CODE / "onnx_runtime"), str(Path(__file__).resolve().parents[1])]))
    for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        e.pop(k, None)
    return e


def server_app(env: dict | None = None) -> str:
    """The pack's pipelined server (crissa_voice.audio8_serve, 2026-10-08: the announcer streams in real time on the
    reference box) unless CRISSA_AUDIO8_UPSTREAM=1 asks for upstream's app as it was."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_AUDIO8_UPSTREAM", "")).strip().lower() in ("1", "true", "yes", "on"):
        return "arktts_runtime.service:app"
    return "crissa_voice.audio8_serve:app"


def server_argv(port: int, env: dict | None = None) -> list[str]:
    return [str(env_python()), "-m", "uvicorn", server_app(env), "--host", "127.0.0.1", "--port", str(port)]


if __name__ == "__main__":
    try:
        install()
    except RuntimeError as e:
        print("audio8: " + str(e), file=sys.stderr)
        sys.exit(2)
