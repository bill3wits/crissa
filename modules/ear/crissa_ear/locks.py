"""The ear module's hash-pinned install locks (2026-10-06, the Kokoro licence check's fix).

Every env this module builds (Kokoro, speech in, Pocket, Turbo) installs from `locks/<env>-<target>-py<maj><min>.txt`:
every package, every transitive one, by exact version and sha256, with pip --require-hashes --no-deps, so nothing
the lock does not name can come in (no espeak carrier, no surprise upgrade) and a file that changed on the index
is refused. Targets: linux-x86_64-cpu, linux-aarch64-cpu, linux-x86_64-cuda, linux-aarch64-cuda and linux-aarch64-cu130 (Turbo only), macos-arm64; Python 3.12
(the tested one). The locks are made on the dev box by scripts/crissa-voice-locks.py with uv.

A machine with no lock for its platform or Python is told so on screen and in the install record, and installs by
the module's exact version pins instead: no hidden fallback.
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path

LOCKS = Path(__file__).resolve().parent.parent / "locks"
PYPI = "https://pypi.org/simple"
TORCH_INDEX = {"cpu": "https://download.pytorch.org/whl/cpu", "cuda": "https://download.pytorch.org/whl/cu128",
               "cu130": "https://download.pytorch.org/whl/cu130"}


def target(plat: str | None = None, machine: str | None = None, cuda: bool | str = False) -> str:
    """cuda: False (the CPU build), True (CUDA 12.8) or "cu130" (CUDA 13.0: DGX Spark / GB10, sm_121)."""
    p = plat or sys.platform
    m = (machine or platform.machine() or "").lower()
    if p == "darwin":
        return "macos-arm64" if m in ("arm64", "aarch64") else "macos-" + (m or "unknown")
    if p.startswith("linux"):
        arch = "aarch64" if m in ("aarch64", "arm64") else ("x86_64" if m in ("x86_64", "amd64") else (m or "unknown"))
        return "linux-%s-%s" % (arch, "cu130" if cuda == "cu130" else ("cuda" if cuda else "cpu"))
    return p


def lock_path(env: str, plat: str | None = None, machine: str | None = None, version=None,
              cuda: bool | str = False) -> Path | None:
    v = tuple(version or sys.version_info[:2])
    p = LOCKS / ("%s-%s-py%d%d.txt" % (env, target(plat, machine, cuda), v[0], v[1]))
    return p if p.is_file() else None


def index_args(tgt: str) -> list[str]:
    """Linux takes torch from the PyTorch index (the CPU or CUDA build); everything else is PyPI."""
    if tgt.startswith("linux-"):
        kind = tgt.rsplit("-", 1)[1]         # cpu, cuda (CUDA 12.8) or cu130
        return ["--index-url", TORCH_INDEX.get(kind, TORCH_INDEX["cpu"]), "--extra-index-url", PYPI]
    return []


def pip_install(python, env: str, run, log, fallback: list[list[str]], *, cuda: bool | str = False,
                plat: str | None = None, machine: str | None = None, version=None) -> str:
    """Install the env's packages into `python`'s env. Returns how: 'lock:<file>' or 'pins (no lock for ...)'."""
    pip = [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    lp = lock_path(env, plat, machine, version, cuda)
    if lp is not None:
        run(pip + ["--require-hashes", "--no-deps"] + index_args(target(plat, machine, cuda)) + ["-r", str(lp)])
        return "lock:" + lp.name
    v = tuple(version or sys.version_info[:2])
    why = "no hash lock for %s on Python %d.%d" % (target(plat, machine, cuda), v[0], v[1])
    log("%s: %s; installing by exact version pins without a hash check (Python 3.12 has locks)" % (env, why))
    for cmd in fallback:
        run(pip + cmd)
    return "pins (%s)" % why


def lock_blocks(path: Path) -> list[tuple[str, str]]:
    """[(name, block)] for each package in a uv lock: its `name==version` line, its hash lines and its comments."""
    out: list[tuple[str, str]] = []
    for line in Path(path).read_text().splitlines(keepends=True):
        head = line.split("==", 1)[0].strip().lower()
        if line[:1].isalnum() and "==" in line:
            out.append((head.replace("_", "-"), line))
        elif out and not line.startswith("#"):
            out[-1] = (out[-1][0], out[-1][1] + line)
    return out


def pip_install_staged(python, lock: Path, stages: list[tuple[str, object]], tgt: str, run, log,
                       workdir: Path) -> str:
    """One lock, installed in named stages, each with pip --require-hashes --no-deps: so the order is the lock's
    own and nothing outside it comes in. stages: [(label, predicate(name) -> bool)]; the last stage may be
    (label, None) for "everything not yet installed". Returns 'lock:<file> (staged)'."""
    pip = [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--require-hashes", "--no-deps"]
    blocks = lock_blocks(lock)
    done: set[str] = set()
    workdir.mkdir(parents=True, exist_ok=True)
    for i, (label, pred) in enumerate(stages):
        part = [(n, b) for n, b in blocks if n not in done and (pred is None or pred(n))]
        if not part:
            continue
        f = workdir / ("stage-%d.txt" % (i + 1))
        f.write_text("".join(b for _, b in part))
        log("%s (%d package%s)" % (label, len(part), "" if len(part) == 1 else "s"))
        run(pip + index_args(tgt) + ["-r", str(f)])
        done.update(n for n, _ in part)
        f.unlink()
    left = [n for n, _ in blocks if n not in done]
    if left:
        raise RuntimeError("the staged install left %s out of %s" % (", ".join(left), lock.name))
    return "lock:%s (staged)" % lock.name
