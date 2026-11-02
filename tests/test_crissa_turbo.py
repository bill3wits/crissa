"""Chatterbox-Turbo, the GPU voice (2026-10-06, the User: Turbo, then Pocket on a capable GPU, x86_64 or Arm). Every
file pinned by sha256, the env from a hash lock with no GPL front end, the Perth watermark ON by default behind a
visible key, the house's /stream contract. No model, no GPU, no network: nothing here loads torch. The Arm path is
NOT YET VERIFIED ON ARM HARDWARE: these tests check its locks, its stages and its self-test plumbing only."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _credits() -> str:
    """CREDITS.md: config/crissa-licences/ in the source tree, the repo root in a clone."""
    for p in (ROOT / "config" / "crissa-licences" / "CREDITS.md", ROOT / "CREDITS.md"):
        if p.is_file():
            return p.read_text()
    raise FileNotFoundError("CREDITS.md")


VOICE = ROOT / "modules" / "voice"
sys.path.insert(0, str(VOICE))
from crissa_voice import turbo_setup as S  # noqa: E402

LINE = ("The quick brown fox jumps over the lazy dog. A rainbow appears when sunlight passes through drops of rain, "
        "and the light bends into many colours. Then the sky clears.")


def _server():
    spec = importlib.util.spec_from_file_location("crissa_turbo_server", VOICE / "scripts" / "crissa-turbo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_chunks_are_about_80_chars_and_every_word_kept():
    srv = _server()
    ch = srv.chunks_for(LINE, st=dict(srv.DEFAULTS))
    assert all(len(c) <= 80 for c in ch) and len(ch) > 2
    assert " ".join(ch).split() == LINE.split()


def test_every_model_file_is_pinned_to_one_revision_and_a_hash():
    assert re.fullmatch(r"[0-9a-f]{40}", S.MODEL_REV) and S.MODEL_REPO == "ResembleAI/chatterbox-turbo"
    for rel, (sha, size) in S.MODEL_FILES.items():
        assert re.fullmatch(r"[0-9a-f]{64}", sha) and size > 0, rel
    for need in ("t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "ve.safetensors", "conds.pt"):
        assert need in S.MODEL_FILES


def test_a_wrong_download_stops_the_install_and_is_removed(tmp_path):
    def fetch(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"not the model")
    with pytest.raises(RuntimeError, match="pinned sha256"):
        S.get_model(fetch=fetch, dest=tmp_path, log=lambda *_: None)
    assert not any(tmp_path.iterdir())


def test_it_says_why_before_anything_downloads():
    assert "Linux" in S.unsupported((3, 12), "darwin", "arm64", cuda=False)
    assert S.unsupported((3, 12), "linux", "aarch64", cuda=True, jetson=False) == "", "Arm + CUDA installs Turbo"
    assert "JetPack" in S.unsupported((3, 12), "linux", "aarch64", cuda=True, jetson=True), "a Jetson is told why"
    assert "riscv64" in S.unsupported((3, 12), "linux", "riscv64", cuda=True)
    assert "NVIDIA" in S.unsupported((3, 12), "linux", "x86_64", cuda=False)
    assert "3.12" in S.unsupported((3, 11), "linux", "x86_64", cuda=True)
    assert S.unsupported((3, 12), "linux", "x86_64", cuda=True) == ""


def test_the_hash_lock_holds_torch_with_cuda_perth_and_no_gpl_front_end():
    lock = (VOICE / "locks" / "turbo-linux-x86_64-cuda-py312.txt").read_text()
    names = {l.split("==")[0].lower() for l in lock.splitlines() if re.match(r"^[A-Za-z]", l)}
    assert "--hash=sha256:" in lock and "torch==2.11.0+cu128" in lock
    assert {"chatterbox-tts", "resemble-perth"} <= names
    assert not names & {"pykakasi", "gradio", "spacy-pkuseg", "espeakng-loader", "phonemizer", "piper-phonemize"}


def test_the_watermark_is_on_by_default_and_the_key_or_environment_turns_it_off(tmp_path):
    srv = _server()
    assert srv.settings(home=tmp_path, env={})["watermark"] is True
    assert srv.settings(home=tmp_path, env={"CRISSA_TURBO_WATERMARK": "0"})["watermark"] is False
    (tmp_path / "crissa-voice-tiers.json").write_text(json.dumps({"tiers": {"turbo": {"settings": {"watermark": False}}}}))
    assert srv.settings(home=tmp_path, env={})["watermark"] is False


def test_voices_are_the_persons_own_clips_and_none_ship(tmp_path):
    (tmp_path / "turbo").mkdir()
    (tmp_path / "turbo" / "Lila.wav").write_bytes(b"RIFF")
    got = S.voice_clips([tmp_path / "turbo"])
    assert got == {"lila": tmp_path / "turbo" / "Lila.wav"}
    assert sorted(p.relative_to(VOICE).as_posix() for p in VOICE.rglob("*.wav")) == \
        ["faces/lila-imagine.wav", "faces/crissa-imagine.wav"], \
        "no voice clip ships in the module but Crissa's and Lila's own (created with Grok, 2026-10-08 and 2026-10-12)"


def test_the_server_never_logs_the_words_and_has_no_hidden_cap():
    src = (VOICE / "scripts" / "crissa-turbo.py").read_text()
    assert not [l for l in src.splitlines() if "_log(" in l and "text" in l], "logs carry numbers only"
    assert "MAX_CHARS" not in src and "[:2000]" not in src
    assert "X-Sample-Rate" in src and "/abort" in src and "/reload_voices" in src


def test_the_credit_names_resemble_and_the_watermark():
    credits = _credits()
    assert "Chatterbox-Turbo" in credits and "Perth" in credits and "v2 slot" not in credits


def test_the_start_gate_counts_free_memory_as_init_does():
    """GPU run 2026-10-06: nvidia-smi said 6.03 GiB free, Init picked Turbo, and torch saw 5.5 after its own CUDA
    context, so the server refused. The context is added back; a card really short is still refused, and says why."""
    srv = _server()
    st = dict(srv.DEFAULTS)
    gib, mib = 1024 ** 3, 1024 ** 2
    ok, why = srv.room(int(5.5 * gib), 540 * mib, st)
    assert ok and "6.03 GiB" in why and "CUDA context" in why and "vram_cap_gb 4.5" in why
    ok, why = srv.room(int(5.0 * gib), 540 * mib, st)
    assert not ok and "needs 6.0" in why

    class Out:
        stdout = "4187224, 1812\n%d, 540\n" % 4242
    assert srv.own_gpu_bytes(4242, run=lambda *a, **k: Out()) == 540 * mib
    assert srv.own_gpu_bytes(1, run=lambda *a, **k: Out()) == 0

    def missing(*a, **k):
        raise FileNotFoundError("nvidia-smi")
    assert srv.own_gpu_bytes(4242, run=missing) == 0


def test_arm_has_its_own_locks_cu128_for_servers_and_cu130_for_gb10():
    from crissa_voice import locks as L
    for cuda, tag in ((True, "+cu128"), ("cu130", "+cu130")):
        lp = L.lock_path("turbo", "linux", "aarch64", (3, 12), cuda)
        assert lp is not None, cuda
        text = lp.read_text()
        assert "torch==2.11.0" + tag in text and "torchaudio==2.11.0" + tag in text and "--hash=sha256:" in text
        names = {n for n, _ in L.lock_blocks(lp)}
        assert {"chatterbox-tts", "resemble-perth", "transformers"} <= names
        assert not names & {"pykakasi", "gradio", "spacy-pkuseg", "torchcodec"}
    assert L.index_args("linux-aarch64-cu130")[1].endswith("/cu130")
    assert L.index_args("linux-aarch64-cuda")[1].endswith("/cu128")


def test_which_cuda_build_an_arm_machine_takes():
    class R:
        def __init__(self, out):
            self.stdout = out
    assert S.torch_cuda(run=lambda *a, **k: R("12.1\n"), env={}) == "cu130", "sm_121: DGX Spark / GB10"
    assert S.torch_cuda(run=lambda *a, **k: R("9.0\n"), env={}) is True, "GH200 (sm_90): cu128"
    assert S.torch_cuda(run=lambda *a, **k: R("12.1\n"), env={"CRISSA_TURBO_TORCH_CUDA": "cu128"}) is True
    assert S.lock_target("x86_64", run=lambda *a, **k: R("12.1\n"), env={}) is True, "x86_64 keeps its tested lock"


def test_the_arm_install_runs_in_stages_torch_first_then_chatterbox_no_deps_then_deps_then_perth(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("CRISSA_VOICE_PYENV", "0")   # the lock stages; the micromamba env is test_crissa_voice_pyenv's
    monkeypatch.setattr(S, "ROOT", tmp_path)
    monkeypatch.setattr(S, "ENV", tmp_path / "env")
    (tmp_path / "env" / "bin").mkdir(parents=True)
    (tmp_path / "env" / "bin" / "python").write_text("")

    def run(cmd):
        f = Path(cmd[cmd.index("-r") + 1])
        calls.append((cmd, [l.split("==")[0] for l in f.read_text().splitlines() if l[:1].isalnum()]))
    how = S.make_env(run=run, log=lambda *_: None, machine="aarch64", cuda="cu130")
    assert how == "lock:turbo-linux-aarch64-cu130-py312.txt (staged)"
    assert len(calls) == 4
    for cmd, _ in calls:
        assert "--require-hashes" in cmd and "--no-deps" in cmd and "https://download.pytorch.org/whl/cu130" in cmd
    first, second, third, fourth = (names for _, names in calls)
    assert {"torch", "torchaudio"} <= set(first) and all(n.startswith(("torch", "nvidia", "cuda", "triton")) for n in first)
    assert second == ["chatterbox-tts"] and fourth == ["resemble-perth"]
    assert "transformers" in third and "torch" not in third and "resemble-perth" not in third
    assert not list(tmp_path.glob("stages/*")), "the stage files are removed"


def test_nothing_in_turbo_writes_or_reads_audio_through_torchaudio():
    """torchcodec is broken on arm64 GB10: Turbo streams raw PCM; any WAV goes through the standard library."""
    for rel in ("scripts/crissa-turbo.py", "crissa_voice/turbo_setup.py"):
        src = (VOICE / rel).read_text()
        assert not re.search(r"torchaudio\.(save|load)\(|\bta\.(save|load)\(", src), rel


def test_a_failed_self_test_is_kept_and_named(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DONE", tmp_path / "installed.json")
    (tmp_path / "installed.json").write_text(json.dumps({"model": S.MODEL_REPO}))
    assert S.self_test_failed() == ""

    class R:
        returncode = 0
        stderr = ""
        stdout = 'noise\n{"ok": false, "why": "RuntimeError: torch cannot see the GPU"}\n'
    st = S.self_test(run=lambda *a, **k: R())
    S.save_self_test(st)
    assert S.self_test_failed() == "RuntimeError: torch cannot see the GPU"
    R.stdout = ""
    R.returncode = 1
    R.stderr = "ImportError: libnppicc.so.13"
    assert "libnppicc" in S.self_test(run=lambda *a, **k: R())["why"]
    assert "--watermark" in S.SELF_TEST and "get_arch_list" in S.SELF_TEST and "chatterbox.tts_turbo" in S.SELF_TEST
