"""The voice module's own Python: one micromamba env per engine, from conda-forge, never the system Python (2026-10-07).

the User 2026-10-07 (the fresh-install test on Ubuntu): every install line said `python3.12`, and that Ubuntu ships
3.14 only. Now the installer runs under any Python 3 (it needs the standard library alone), and the engine's env
gets the Python pinned in locks/python-env.json, made by micromamba from conda-forge:

    micromamba    a user's own `micromamba` on PATH (when the file allows it), else the pinned release fetched once
                  into <crissa home>/micromamba/bin and checked against its sha256; the download goes through the
                  kill-search switch like every other installer (egress.urlopen), and micromamba itself is not run
                  while the switch refuses installs
    env           `micromamba create -p <crissa home>/<engine>/env -c conda-forge --override-channels python=<pin> pip`;
                  an env whose Python is not the pinned minor is made again
    packages      the engine's hash lock for that Python (crissa_voice/locks.py), unchanged

Why micromamba and not uv: conda-forge is the source the User named, its Python builds carry pip and venv, and
micromamba is one static file per platform with a published sha256, so it is fetched and checked like a model file.

One env per engine (not one shared voice env): the locks disagree on torch (2.6 speech-in/Kokoro, 2.11 Turbo, 2.14.1
Pocket) and numpy (1.26 / 2.x). The package cache is shared under <crissa home>/micromamba/pkgs.

Kill switch: CRISSA_VOICE_PYENV=0 builds the env from the running Python as before (python -m venv). The house never
installs a voice through this module (it runs its own voices), so nothing here reaches it.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import egress

CONFIG = Path(__file__).resolve().parent.parent / "locks" / "python-env.json"
SWITCH = "CRISSA_VOICE_PYENV"
# Said when a Python version is wrong. It names no command and no variable: the person never has to find a Python.
ADVICE = ("The voice installer makes its own Python for this; it is switched off on this machine, so turn it back "
          "on and run the install again.")


def _home() -> Path:
    try:
        from . import crissa_home as H              # the copy a pack puts beside this file
    except ImportError:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
            import crissa_home as H                 # noqa: E402  the source tree's
        except ImportError:
            H = None
    env = os.environ.get("CRISSA_HOME")
    if env:
        return Path(env).expanduser()
    return Path(H.HOME) if H is not None else Path.home() / ".crissa"


def on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get(SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def config(home: Path | None = None) -> dict:
    """locks/python-env.json, then <crissa home>/voice-python.json key by key (a dict key merges one level)."""
    out: dict = {}
    for p in (CONFIG, (home or _home()) / "voice-python.json"):
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k, v in (d or {}).items():
            out[k] = dict(out.get(k) or {}, **v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def version(cfg: dict | None = None) -> tuple[int, int]:
    """(major, minor) of the pinned Python: the version the locks are chosen for."""
    m = re.match(r"(\d+)\.(\d+)", str((cfg or config()).get("python") or ""))
    if not m:
        raise RuntimeError("the voice module's Python pin is missing from %s" % CONFIG.name)
    return int(m.group(1)), int(m.group(2))


def target_version(env: dict | None = None) -> tuple[int, int]:
    """The Python the engine's env will run: the pin when the switch is on, else the running one."""
    return version() if on(env) else tuple(sys.version_info[:2])


def mamba_platform(plat: str | None = None, machine: str | None = None) -> str:
    p = plat or sys.platform
    m = (machine or platform.machine() or "").lower()
    arch = {"x86_64": "64", "amd64": "64", "aarch64": "aarch64", "arm64": "arm64" if p == "darwin" else "aarch64"}.get(m, m)
    if p == "darwin":
        return "osx-" + ("arm64" if arch in ("arm64", "aarch64") else "64")
    if p.startswith("linux"):
        return "linux-" + arch
    return p


def root(home: Path | None = None) -> Path:
    return (home or _home()) / "micromamba"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with egress.urlopen(url, 300.0, "the micromamba download") as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(tmp, dest)


def micromamba(home: Path | None = None, fetch=_fetch, log=print, which=shutil.which, plat: str | None = None,
               machine: str | None = None) -> Path:
    """The micromamba to run: CRISSA_MICROMAMBA, else one on PATH (use_path), else the pinned release in the state
    home (fetched once, checked against its sha256; a wrong file is removed and nothing runs)."""
    cfg = config(home)
    mm = cfg.get("micromamba") or {}
    named = os.environ.get("CRISSA_MICROMAMBA", "").strip()
    if named:
        p = Path(named).expanduser()
        if not (p.is_file() and os.access(p, os.X_OK)):
            raise RuntimeError("the micromamba named for the voice install is not a program: %s" % p)
        return p
    if mm.get("use_path", True):
        got = which("micromamba")
        if got:
            return Path(got)
    plat_key = mamba_platform(plat, machine)
    sha = (mm.get("sha256") or {}).get(plat_key)
    if not sha:
        raise RuntimeError("no pinned micromamba for this machine (%s)" % plat_key)
    dest = root(home) / "bin" / "micromamba"
    if dest.is_file() and _sha256(dest) == sha:
        return dest
    url = str(mm.get("url") or "").format(version=mm.get("version"), platform=plat_key)
    log("voice: fetching micromamba %s (%s) ..." % (mm.get("version"), plat_key))
    fetch(url, dest)
    got = _sha256(dest)
    if got != sha:
        dest.unlink()
        raise RuntimeError("micromamba %s does not match its pinned sha256 (got %s); nothing was installed"
                           % (mm.get("version"), got[:16]))
    dest.chmod(0o755)
    return dest


def env_python(prefix: Path) -> Path:
    return Path(prefix) / ("python.exe" if os.name == "nt" else "bin/python")


def env_version(prefix: Path, run=subprocess.run) -> tuple[int, int] | None:
    py = env_python(prefix)
    if not py.exists():
        return None
    try:
        r = run([str(py), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], capture_output=True,
                text=True, timeout=60)
        a, b = (r.stdout or "").strip().split(".")
        return int(a), int(b)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def create_cmd(mm: Path, prefix: Path, cfg: dict | None = None, home: Path | None = None) -> list[str]:
    """--root-prefix keeps micromamba's package cache in the state home (shared by every engine's env)."""
    c = cfg or config(home)
    return [str(mm), "create", "--yes", "--quiet", "--root-prefix", str(root(home)), "--prefix", str(prefix),
            "--override-channels",
            "--channel", str(c.get("channel") or "conda-forge"), "python=%s" % c["python"]] + \
        [str(x) for x in (c.get("packages") or ["pip"])]


def make(prefix: Path, run, log=print, home: Path | None = None, mm_fn=None, ver_fn=None) -> str:
    """The engine's env at `prefix`, Python pinned. Returns how it was made (for the install record).
    Switch off: `python -m venv` from the running Python, as before."""
    prefix = Path(prefix)
    if not on():
        if not env_python(prefix).exists():
            run([sys.executable, "-m", "venv", str(prefix)])
        return "venv from %s (%s=0)" % (sys.executable, SWITCH)
    cfg = config(home)
    want = version(cfg)
    have = (ver_fn or env_version)(prefix)
    if have == want:
        return "micromamba env, Python %d.%d (already there)" % want
    egress.refuse_install("the voice's Python")
    if prefix.exists():
        log("voice: %s holds Python %s, not %d.%d; making it again" % (prefix, "%d.%d" % have if have else "none", *want))
        shutil.rmtree(prefix)
    mm = (mm_fn or micromamba)(home, log=log)
    log("voice: making a Python %s env from %s (micromamba) ..." % (cfg["python"], cfg.get("channel") or "conda-forge"))
    run(create_cmd(mm, prefix, cfg, home))
    return "micromamba env, Python %s from %s" % (cfg["python"], cfg.get("channel") or "conda-forge")
