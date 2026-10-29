"""The voice module's Chatterbox-Turbo, installed on first use, opt-in (2026-10-06; the User: the top GPU voice).

Resemble AI's Chatterbox-Turbo (350M; code and weights MIT, Hugging Face ResembleAI/chatterbox-turbo at a pinned
revision) on an NVIDIA GPU, ~80-character sentence chunks each rendered whole while the next one renders. Measured
on the reference house's GPU (x86_64, Blackwell, 2026-10-06): 3.7 GiB on the card at peak, first clip in 0.16-0.21 s,
each next clip 0.36-0.43 s behind, real-time factor about 0.11. It is the GPU tier (lib/crissa_voice_tiers); Pocket
is behind it. Linux with CUDA only: it has no Apple GPU path and is far too slow on a CPU to talk.

ARM + NVIDIA (2026-10-06, the User; NOT YET VERIFIED ON ARM HARDWARE): Grace / GH200 / SBSA servers install from
locks/turbo-linux-aarch64-cuda-py312.txt (torch 2.11 cu128 from download.pytorch.org), a DGX Spark / GB10 (sm_121)
from locks/turbo-linux-aarch64-cu130-py312.txt (cu130); CRISSA_TURBO_TORCH_CUDA=cu128|cu130 is the person's own
answer. The Arm install runs in stages, every stage from the hash lock with --no-deps: torch and torchaudio (and
their CUDA libraries) first, then chatterbox-tts, then its runtime deps, then resemble-perth (the watermark stays
on). Nothing here calls torchaudio.save or torchaudio.load (torchcodec is broken on GB10): the service streams raw
PCM and any WAV is written with the standard library's `wave`. A Jetson (Tegra) needs NVIDIA's JetPack torch wheels
(Jetson AI Lab), not these SBSA wheels: Turbo says so and Pocket speaks. ResembleAI/chatterbox-turbo-ONNX (MIT; Perth optional and OFF in the upstream sample) was evaluated as an Arm GPU install path and NOT adopted: onnxruntime-gpu now ships aarch64 CUDA-13 wheels, but the ONNX graphs are a different service (four sessions, hand-rolled loop), watermark is not on by default, Arm GPU timings are unverified, and ONNX on CPU measured too slow against Pocket — not a CPU tier, and not a replacement for this PyTorch --no-deps Arm path. After every install a self-test runs in
the env (CUDA visible, kernels for this GPU, one GPU op, chatterbox and Perth import); when it fails, Init passes
Turbo over with the reason, on screen and in the log, and Pocket speaks.

    python3 modules/voice/scripts/crissa-turbo.py --install
    python3 modules/voice/scripts/crissa-turbo.py [--port 8186]

Fetched on the user's own machine from PyPI, the PyTorch index and Hugging Face; nothing is redistributed:

    <crissa home>/turbo/env      the venv; every package from locks/turbo-linux-<arch>-<cuda>-py312.txt (sha256-pinned)
    <crissa home>/turbo/model    the model files at MODEL_REV, each checked by sha256 (about 3 GB)
    <crissa home>/turbo/voices   <face>.wav: a clip YOU have the rights to, cloned for that face. NONE SHIP. A face
                                 with no clip speaks with Turbo's own built-in voice (conds.pt in the model, MIT),
                                 and the log says so.

WATERMARK: Resemble's Perth watermarker (resemble-perth, in the lock) stamps every clip, as upstream does. It is ON
by default; turbo.settings.watermark in the tier table (or CRISSA_TURBO_WATERMARK=0) turns it off, and /health and
the start-up log always say which. If Perth cannot load, the service refuses to start rather than speak unmarked
while the setting says on.

No pykakasi (GPL-3), no gradio, no spacy-pkuseg: the lock's overrides drop them (locks/turbo-overrides.txt);
check_no_gpl refuses an env that holds one.
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


ROOT = _home() / "turbo"
ENV = ROOT / "env"
MODEL = ROOT / "model"
VOICES = ROOT / "voices"
DONE = ROOT / "installed.json"
MODEL_REPO = "ResembleAI/chatterbox-turbo"
MODEL_REV = "749d1c1a46eb10492095d68fbcf55691ccf137cd"   # 2025-12-15, MIT
CREDIT = "Voice: Chatterbox-Turbo by Resemble AI (resemble.ai), MIT"
# What ChatterboxTurboTTS.from_local loads (chatterbox-tts 0.1.7): path -> (sha256, bytes), from the Hub at MODEL_REV.
MODEL_FILES = {
    "t3_turbo_v1.safetensors": ("fcf1f8c1d651bb7e3acd69ee5be269b4ac10c02980b7708213d598bc9f7cdf87", 1915480052),
    "s3gen_meanflow.safetensors": ("d65cb687a2ed581ee6cc297e919ffefa63386944f42364ae13b78a594945514f", 1064875036),
    "ve.safetensors": ("f0921cab452fa278bc25cd23ffd59d36f816d7dc5181dd1bef9751a7fb61f63c", 5695784),
    "conds.pt": ("b1852099306fd6a7814eb9d0bd10186caba7249596cc23868f78a0eefbfa5033", 169454),
    "vocab.json": ("f6bd25a65e4e63ca31360e9fb11c7e4f9a391a78385d640acd814092dd6eee4f", 999186),
    "merges.txt": ("1ce1664773c50f3e0cc8842619a93edc4624525b728b188a9e0be33b7726adc5", 456318),
    "tokenizer_config.json": ("bca16a2ac1ddbd78b8d6228f0031884cc74b6ea54b967d6f6d2ebae9ccde23e6", 3878),
    "special_tokens_map.json": ("92ba8063bf40aa163eadebbfe0de07c2aebe44cf0d4a9e8726580b0781fd2640", 470),
    "added_tokens.json": ("72e4ab6acb0d9309ac3df4b526ae5fd80a2da5bc5ab7bb02d85096a374f69193", 418),
}
GPL_DISTS = ("pykakasi", "gradio", "spacy-pkuseg", "espeakng-loader", "phonemizer", "phonemizer-fork",
             "piper-phonemize")


def env_python() -> Path:
    return ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed() -> bool:
    if not (DONE.is_file() and env_python().exists()):
        return False
    return all((MODEL / r).is_file() and (MODEL / r).stat().st_size == n for r, (_, n) in MODEL_FILES.items())


def has_cuda(run=None) -> bool:
    """An NVIDIA GPU the driver can see (nvidia-smi answers). CRISSA_TURBO_CUDA=0 (or 1) is the person's own answer."""
    forced = os.environ.get("CRISSA_TURBO_CUDA", "").strip().lower()
    if forced in ("0", "1", "true", "false", "on", "off", "yes", "no"):
        return forced in ("1", "true", "on", "yes")
    if not shutil.which("nvidia-smi"):
        return False
    try:
        r = (run or subprocess.run)(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and "GPU" in (r.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return False


ARM = ("aarch64", "arm64")


def is_jetson(root: Path = Path("/")) -> bool:
    """A Jetson (Tegra) board: JetPack's release file, or the device tree naming it."""
    if (root / "etc" / "nv_tegra_release").exists():
        return True
    for f in ("proc/device-tree/model", "proc/device-tree/compatible"):
        try:
            if any(w in (root / f).read_bytes().lower() for w in (b"jetson", b"tegra")):
                return True
        except OSError:
            pass
    return False


def torch_cuda(run=None, env=None) -> str | bool:
    """Which CUDA build of torch this machine takes: True (CUDA 12.8, the x86_64 lock and Arm servers) or "cu130"
    (CUDA 13.0) on an sm_121 GPU (DGX Spark / GB10). CRISSA_TURBO_TORCH_CUDA=cu128|cu130 is the person's own answer."""
    e = os.environ if env is None else env
    forced = str(e.get("CRISSA_TURBO_TORCH_CUDA") or "").strip().lower()
    if forced in ("cu128", "cu130"):
        return "cu130" if forced == "cu130" else True
    try:
        r = (run or subprocess.run)(["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
                                    capture_output=True, text=True, timeout=10)
        caps = [c.strip() for c in (r.stdout or "").splitlines() if c.strip()]
    except (OSError, subprocess.SubprocessError):
        caps = []
    return "cu130" if "12.1" in caps else True


def lock_target(machine: str | None = None, run=None, env=None) -> str | bool:
    """The `cuda` argument for locks: x86_64 always CUDA 12.8; Arm by its GPU (torch_cuda)."""
    m = (machine or platform.machine()).lower()
    return torch_cuda(run, env) if m in ARM else True


def unsupported(version=None, plat: str | None = None, machine: str | None = None, cuda: bool | None = None,
                jetson: bool | None = None) -> str:
    """'' when this machine and Python can install Turbo, else exactly why not, said before anything downloads."""
    v = tuple(version or pyenv.target_version())
    p = plat or sys.platform
    m = (machine or platform.machine()).lower()
    if v != (3, 12):
        return ("Turbo's install is locked for Python 3.12 (its env would run %d.%d). %s" % (v[0], v[1], pyenv.ADVICE))
    if not p.startswith("linux"):
        return ("Turbo needs Linux with an NVIDIA GPU (no Apple GPU path; far too slow on a CPU to talk). Pocket is "
                "this machine's voice.")
    if m not in ("x86_64", "amd64") + ARM:
        return ("Turbo's locks are for x86_64 or Arm (aarch64) Linux with CUDA; this machine is %s. Pocket is this "
                "machine's voice." % m)
    if m in ARM and (is_jetson() if jetson is None else jetson):
        return ("this is a Jetson: Turbo on a Jetson needs NVIDIA's JetPack torch wheels (Jetson AI Lab, matched to "
                "your JetPack release), not the SBSA server wheels in Turbo's Arm lock, and Crissa does not install "
                "them. Pocket is this machine's voice.")
    if not (has_cuda() if cuda is None else cuda):
        return "Turbo needs an NVIDIA GPU the driver can see (nvidia-smi); none here. Pocket is this machine's voice."
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
    with egress.urlopen(url, timeout, "the Turbo download") as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 22)
    os.replace(tmp, dest)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(cmd[:4]), (r.stderr or r.stdout)[-400:]))
    return r


def get_model(fetch=_fetch, dest: Path | None = None, log=print) -> None:
    """Each file at MODEL_REV, checked by sha256. A file already there with the right hash is kept; a wrong download
    is removed and stops the install."""
    dest = Path(dest or MODEL)
    for rel, (sha, _) in MODEL_FILES.items():
        p = dest / rel
        if p.is_file() and _sha256(p) == sha:
            continue
        log("turbo: fetching %s …" % rel)
        fetch("https://huggingface.co/%s/resolve/%s/%s" % (MODEL_REPO, MODEL_REV, rel), p)
        got = _sha256(p)
        if got != sha:
            p.unlink()
            raise RuntimeError("%s from %s@%s does not match its pinned sha256 (got %s); nothing was installed"
                               % (rel, MODEL_REPO, MODEL_REV[:8], got[:16]))


def _torch_stage(name: str) -> bool:
    return name in ("torch", "torchaudio", "triton") or name.startswith(("nvidia-", "cuda-"))


ARM_STAGES = [
    ("turbo: 1/4 torch and torchaudio with CUDA, from download.pytorch.org", _torch_stage),
    ("turbo: 2/4 chatterbox-tts, without its own dependencies", lambda n: n == "chatterbox-tts"),
    ("turbo: 3/4 its runtime dependencies, each pinned", lambda n: n != "resemble-perth"),
    ("turbo: 4/4 resemble-perth, the watermark", None),
]


def make_env(run=_run, log=print, machine: str | None = None, cuda=None) -> str:
    """x86_64: the lock in one pip run (as measured on the reference house). Arm: the same kind of lock in the
    four stages above (the recipe proven on the DGX Spark: torch first, chatterbox-tts with no deps, then the rest)."""
    m = (machine or platform.machine()).lower()
    cuda = lock_target(m) if cuda is None else cuda
    log("turbo: " + pyenv.make(ENV, run, log))
    if m in ARM:
        lp = locks.lock_path("turbo", machine=m, cuda=cuda, version=pyenv.target_version())
        if lp is None:
            raise RuntimeError("no Turbo lock for %s; it installs only from its hash lock" % locks.target(machine=m, cuda=cuda))
        return locks.pip_install_staged(env_python(), lp, ARM_STAGES, locks.target(machine=m, cuda=cuda), run, log,
                                        ROOT / "stages")
    return locks.pip_install(env_python(), "turbo", run, log, cuda=cuda, fallback=[], version=pyenv.target_version())


SELF_TEST = r'''
import json, sys
out = {"ok": False}
try:
    import torch
    out["torch"] = torch.__version__
    if not torch.cuda.is_available():
        raise RuntimeError("torch cannot see the GPU (torch.cuda.is_available() is False; driver %s)" % torch.version.cuda)
    maj, mnr = torch.cuda.get_device_capability(0)
    out["gpu"] = torch.cuda.get_device_name(0)
    out["cap"] = "%d.%d" % (maj, mnr)
    archs = torch.cuda.get_arch_list()
    have = [a for a in archs if a.startswith(("sm_", "compute_"))]
    want = maj * 10 + mnr
    def num(a):
        d = "".join(ch for ch in a.split("_", 1)[1] if ch.isdigit())
        return int(d) if d else 0
    if "sm_%d%d" % (maj, mnr) not in archs and not any(a.startswith("compute_") and num(a) <= want for a in have)             and not any(a.startswith("sm_") and num(a) // 10 == maj and num(a) <= want for a in have):
        raise RuntimeError("this torch has no kernels for sm_%d%d (it has %s)" % (maj, mnr, " ".join(archs)))
    x = torch.randn(256, 256, device="cuda")
    y = (x @ x).sum().item()
    torch.cuda.synchronize()
    import chatterbox.tts_turbo  # noqa: F401
    import perth
    out["perth"] = getattr(perth, "PerthImplicitWatermarker", None) is not None
    if not out["perth"] and "--watermark" in sys.argv:
        raise RuntimeError("the Perth watermarker cannot load here and the watermark is on")
    out["ok"] = True
except Exception as e:
    out["why"] = "%s: %s" % (type(e).__name__, str(e)[:300])
print(json.dumps(out))
'''


def self_test(run=None, watermark: bool = True, timeout: float = 300.0) -> dict:
    """Run the self-test in the Turbo env: {"ok", "why"?, "gpu", "cap", "torch", "perth"}. Never raises."""
    cmd = [str(env_python()), "-c", SELF_TEST] + (["--watermark"] if watermark else [])
    try:
        r = (run or subprocess.run)(cmd, capture_output=True, text=True, timeout=timeout)
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("{")]
        got = json.loads(lines[-1]) if lines else {"ok": False, "why": "no answer (exit %s): %s"
                                                   % (r.returncode, (r.stderr or "")[-300:].strip())}
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        got = {"ok": False, "why": "%s: %s" % (type(e).__name__, e)}
    got["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return got


def record() -> dict:
    try:
        return json.loads(DONE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def self_test_failed() -> str:
    """'' when Turbo passed its last self-test (or none has run), else why it failed."""
    st = record().get("self_test")
    if isinstance(st, dict) and st.get("ok") is False:
        return str(st.get("why") or "it failed")
    return ""


def save_self_test(st: dict) -> dict:
    rec = record()
    rec["self_test"] = st
    DONE.write_text(json.dumps(rec) + "\n")
    return rec


def check_no_gpl(run=_run) -> None:
    r = run([str(env_python()), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"])
    names = {str(d.get("name", "")).lower().replace("_", "-") for d in json.loads(r.stdout or "[]")}
    bad = sorted(n for n in names if n in GPL_DISTS)
    if bad:
        raise RuntimeError("the Turbo env holds %s; remove it or rebuild %s" % (", ".join(bad), ENV))


def install(log=print) -> dict:
    why = unsupported()
    if why:
        raise RuntimeError(why)
    cuda = lock_target()
    if locks.lock_path("turbo", cuda=cuda) is None:
        raise RuntimeError("no Turbo lock for this machine (%s); it installs only from its hash lock"
                           % locks.target(cuda=cuda))
    arm = platform.machine().lower() in ARM
    egress.refuse_install("the Turbo voice")
    ROOT.mkdir(parents=True, exist_ok=True)
    VOICES.mkdir(parents=True, exist_ok=True)
    log("turbo: building the Python env (torch 2.11 with CUDA %s, chatterbox-tts 0.1.7, Perth; no pykakasi, no "
        "gradio) from %s …" % ("13.0" if cuda == "cu130" else "12.8", locks.target(cuda=cuda)))
    if arm:
        log("turbo: the Arm path is not yet verified on Arm hardware; the self-test after the install decides")
    how = make_env(log=log, cuda=cuda)
    check_no_gpl()
    log("turbo: model %s @ %s (about 3 GB): files already here with the right sha256 are kept, the rest are fetched …" % (MODEL_REPO, MODEL_REV[:8]))
    get_model(log=log)
    rec = {"model": MODEL_REPO, "rev": MODEL_REV, "installed_by": how, "credit": CREDIT,
           "target": locks.target(cuda=cuda), "verified_on_hardware": not arm}
    DONE.write_text(json.dumps(rec) + "\n")
    log("turbo: installed in %s; self-test …" % ROOT)
    st = self_test(watermark=_watermark_on())
    rec = save_self_test(st)
    if st.get("ok"):
        log("turbo: self-test passed on %s (sm_%s, torch %s)" % (st.get("gpu"), str(st.get("cap", "")).replace(".", ""),
                                                                 st.get("torch")))
    else:
        log("turbo: SELF-TEST FAILED: %s. Init passes Turbo over and Pocket speaks (install it: python3 "
            "modules/voice/scripts/crissa-pocket.py --install). Re-run it: python3 "
            "modules/voice/scripts/crissa-turbo.py --self-test" % st.get("why"))
    rec = save_faces(faces.fetch_all(_fetch, log, "turbo"))
    for f, p in faces.plan(VOICES, "Turbo's built-in voice").items():
        log("turbo: %s speaks with %s%s" % (f, p["voice"], (" (%s)" % p["fallback"]) if p["fallback"] else ""))
    log("turbo: to give a face a voice of your own, put a clip you have the rights to (5 to 15 s of one clean voice) "
        "in %s as <face>.wav; it wins over the stock clip." % VOICES)
    return rec


def save_faces(fetched: dict) -> dict:
    """Keep each face's clip state in installed.json: {face: ""} or {face: why it has no clip}."""
    rec = dict(record(), faces=fetched)
    DONE.write_text(json.dumps(rec) + "\n")
    return rec


def voice_clips(dirs: list[Path] | None = None) -> dict:
    """{face: clip}: each face's reference clip (faces.py: crissa and lila their own clips, created with Grok and shipped in
    the pack, backed by jane and eve; the announcer anna, CC BY 4.0, fetched at install), then the person's own
    <face>.wav in Turbo's voices folder, which wins."""
    if dirs is not None:
        out = {}
        for d in dirs:
            for p in sorted(Path(d).glob("*.wav")):
                out[p.stem.lower()] = p
        return out
    return faces.clips(VOICES)


def face_plan() -> dict:
    return faces.plan(VOICES, "Turbo's built-in voice")


def _watermark_on() -> bool:
    w = str(os.environ.get("CRISSA_TURBO_WATERMARK", "")).strip().lower()
    return w not in ("0", "false", "off", "no")


def in_env() -> bool:
    try:
        return Path(sys.prefix).resolve() == ENV.resolve()
    except OSError:
        return False


if __name__ == "__main__":
    try:
        install()
    except RuntimeError as e:
        print("turbo: " + str(e), file=sys.stderr)
        sys.exit(2)
