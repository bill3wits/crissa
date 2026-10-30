"""Better mouths: an optional word aligner, downloaded on first use (the User 2026-10-01).

WHY. Her mouth follows the words she says. Without an aligner the player spreads the words over the
line by their letters (the letter clock); with one, every word sits where it is actually heard. On the
house's grading against hand marks: letter clock 21.6 %, aligned (MFA) 43.6 % at lead 0 / 54.4 % best.
The aligner is light to RUN (about 0.2 s and 0.65 GB of memory per line on a desktop CPU, no GPU) and
heavy to INSTALL (0.5-2 GB), so the pack ships without it and the letter clock is the default.

WHAT "ON" DOES, on the user's own machine, from the projects' own servers (nothing is redistributed):
  1. micromamba (BSD-3) from micro.mamba.pm,
  2. a CPU-only Montreal Forced Aligner env (MFA: MIT; Kaldi, OpenFst, pynini: Apache-2.0) from
     conda-forge into <crissa home>/aligner/env,
  3. the MFA english_us_arpa model (CC BY 4.0) at a pinned revision from Hugging Face,
then runs scripts/crissa-align.py on loopback. Each spoken line is pointed at it; a cold, slow or
missing aligner leaves that line on the letter clock (house-speak's own fail-open).

THE ALIGNER INSTALLS DURING INIT, AFTER THE VOICE, ON EVERY MACHINE (the User 2026-10-10). With no choice saved yet,
better() is default_on():
    allowed   Init's machine check ran (lib/crissa_machine, the person's Allow) and timed the aligner: on when its
              align stage clears align_s_per_line_max, off when it misses (crissa_machine.align_default). This is
              the only thing that turns the default off.
    else      skipped, never run, or the aligner was not running to be timed: ON, on every machine, no RAM threshold
Init's machine step installs it right after the voice (lib/crissa_init.aligner_after_voice: set_better(True,
by="init"), the download in the background); a restart before it finished carries the install on (boot()). Nothing
else downloads it by itself: a pack that never walks Init gets it from the Connect page's tick. The person's tick or
untick on the Connect page is saved as theirs and always wins. CRISSA_ALIGNER_RAM_RULE=1 puts back the rule of
2026-10-07 ("keep it opt-in, but tick it by default on machines with plenty of RAM": at least
config/crissa-machine-tiers.json better_mouths.default_on_ram_gb, 16 GB; the aligner measured 0.62 GB peak), boot()
installing it as it did then. Kill every default: CRISSA_BETTER_MOUTHS_DEFAULT=0.

ONE ALIGNER, THE LAUNCHER'S (round 17, 2026-10-13, .101 08:28): when the launcher runs the aligner as a lane of its
own (scripts/crissa.py plans it when better mouths are on and installed, and tells the player CRISSA_ALIGNER_LANE=1),
this player never starts one beside it - start() is a no-op and the lane serves every line. A player that booted while
Close Agent held the lane started its own on :8195, Open Agent then started crissa-aligner.service into the taken port,
and the unit failed "Address already in use" every 2 s: 3,453 restarts by 08:28. With no lane (a player run by hand,
or a first boot whose Init installs the aligner after the launcher planned its lanes) the player still starts its own.
Kill CRISSA_ALIGNER_ONE_OWNER=0 (round 16: the player starts its own whenever none answers).

The house is never touched: it runs rail-align as a service, so here `applicable()` is False and
nothing installs, starts or changes a line's env. Kill CRISSA_BETTER_MOUTHS=0.
"""
from __future__ import annotations

import json
import os
import signal
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import crissa_egress
import crissa_home

ROOT = crissa_home.HOME / "aligner"
ENV = ROOT / "env"
MODELS = ROOT / "english_us_arpa"
MICROMAMBA = ROOT / "bin" / "micromamba"
SETTINGS = crissa_home.HOME / "mouths.json"
DONE = ROOT / "installed.json"
MACHINE_CONFIG = Path(__file__).resolve().parent.parent / "config" / "crissa-machine-tiers.json"
DEFAULT_ON_RAM_GB = 16.0       # the code's copy of better_mouths.default_on_ram_gb; the file wins
SERVER = Path(__file__).resolve().parent.parent / "scripts" / "crissa-align.py"
PORT = int(os.environ.get("CRISSA_ALIGN_PORT", "8195"))

MODEL_REPO = "MontrealCorpusTools/english_us_arpa"
MODEL_REV = "9c8d5d8e1dd5d9cf188a5571203de0e09ca6bede"     # the revision the house graded
# One pin for both. MFA is noarch; its compiled parts (kaldi 5.5.1172 cpu, kalpy 0.10.5, python 3.14) solve
# for osx-arm64 exactly as for linux-64 (2026-10-05, a dry-run solve with CONDA_OVERRIDE_OSX=14.0; the
# 10-01 note read a solver that lacked the __osx virtual package as "no Mac build"). A Mac stays untested
# until it is measured there. Kill the Mac pin: CRISSA_ALIGN_MAC_PIN=0 (the old unpinned spec).
SPECS = {
    "linux": ["montreal-forced-aligner=3.4.2", "kaldi=*=cpu*"],
    "darwin": ["montreal-forced-aligner=3.4.2", "kaldi=*=cpu*"],
}
TESTED = {"linux"}
ENV_DONE = ".crissa-env-complete"      # written into the env only after micromamba created all of it


def _specs() -> list:
    if _os() == "darwin" and os.environ.get("CRISSA_ALIGN_MAC_PIN", "1").strip().lower() in ("0", "off", "false", "no"):
        return ["montreal-forced-aligner", "kaldi=*=cpu*"]
    return SPECS[_os()]

_LOCK = threading.Lock()
_STATE: dict = {"installing": False, "step": None, "error": None, "proc": None}


def applicable() -> bool:
    """A pack, with the switch not killed. The house has rail-align already."""
    if os.environ.get("CRISSA_BETTER_MOUTHS", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return not crissa_home.is_house()


LANE_ENV = "CRISSA_ALIGNER_LANE"            # the launcher's word on the player: the aligner runs as its lane
ONE_OWNER = "CRISSA_ALIGNER_ONE_OWNER"      # kill (=0): the player starts its own whenever none answers (round 16)


def lane_owned(env=None) -> bool:
    """The launcher runs the aligner as its own lane (it set CRISSA_ALIGNER_LANE=1 on this player), so this player
    never starts one (ONE ALIGNER, THE LAUNCHER'S, round 17)."""
    e = os.environ if env is None else env
    if str(e.get(ONE_OWNER, "1")).strip().lower() in ("0", "off", "false", "no"):
        return False
    return str(e.get(LANE_ENV) or "").strip().lower() not in ("", "0", "off", "false", "no")


def _os() -> str:
    return "darwin" if sys.platform == "darwin" else "linux"


def _conda_platform() -> str:
    m = platform.machine().lower()
    arm = m in ("arm64", "aarch64")
    if _os() == "darwin":
        return "osx-arm64" if arm else "osx-64"
    return "linux-aarch64" if arm else "linux-64"


def _saved() -> dict:
    try:
        d = json.loads(SETTINGS.read_text())
        return d if isinstance(d, dict) and "better" in d else {}
    except (OSError, ValueError, AttributeError):
        return {}


def default_on_ram_gb() -> float:
    """better_mouths.default_on_ram_gb: the shipped machine table, then the state home's copy."""
    got = DEFAULT_ON_RAM_GB
    for p in (MACHINE_CONFIG, crissa_home.HOME / "crissa-machine-tiers.json"):
        try:
            v = (json.loads(Path(p).read_text(encoding="utf-8")).get("better_mouths") or {}).get("default_on_ram_gb")
            got = float(v) if v is not None else got
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return got


def ram_gb() -> float | None:
    try:
        import crissa_voice_tiers
        return crissa_voice_tiers._ram_gb(_os())
    except Exception:  # noqa: BLE001
        return None


def ram_rule() -> bool:
    """CRISSA_ALIGNER_RAM_RULE=1: the default is the RAM rule of 2026-10-07 again (the kill switch for the check's rule)."""
    return os.environ.get("CRISSA_ALIGNER_RAM_RULE", "").strip().lower() in ("1", "on", "true", "yes")


def measured_default() -> bool | None:
    """The machine check's word on the aligner (crissa_machine.align_default): True / False when the person allowed the
    check and it timed the aligner against its bar, else None."""
    try:
        import crissa_machine
        return crissa_machine.align_default(crissa_home.HOME)
    except Exception:  # noqa: BLE001  a check that cannot be read says nothing
        return None


def default_by() -> str:
    """What sets the default here: "check" (the machine check timed it), "default" (on, every machine), "memory"
    (CRISSA_ALIGNER_RAM_RULE=1), or "off" (no default: the house, or CRISSA_BETTER_MOUTHS_DEFAULT=0)."""
    if not applicable() or os.environ.get("CRISSA_BETTER_MOUTHS_DEFAULT", "1").strip().lower() in ("0", "off", "false", "no"):
        return "off"
    if ram_rule():
        return "memory"
    return "check" if measured_default() is not None else "default"


def default_on(ram: float | None = None) -> bool:
    """On by default here: a pack, the default not switched off, and - only when the person allowed the machine check
    and it timed the aligner - its bar; otherwise on (CRISSA_ALIGNER_RAM_RULE=1: at least default_on_ram_gb of RAM)."""
    by = default_by()
    if by == "off":
        return False
    if by == "memory":
        r = ram_gb() if ram is None else ram
        return r is not None and float(r) >= default_on_ram_gb()
    if by == "check":
        return bool(measured_default())
    return True


SET_BY_DEFAULT = ("default", "init")     # saved by a default (the RAM rule's, Init's): never the person's own choice


def better() -> bool:
    """The person's saved choice; with none saved yet, the default for this machine (default_on). A choice a default
    saved (by "default" - the RAM rule before 2026-10-10 - or by "init", Init's install after the voice) is not
    theirs: the default is worked out again, unless the RAM rule is back (CRISSA_ALIGNER_RAM_RULE=1)."""
    saved = _saved()
    if saved and (saved.get("by") not in SET_BY_DEFAULT or ram_rule()):
        return bool(saved.get("better"))
    return default_on()


def installed() -> bool:
    """Every step finished: a half-built env (python present, MFA not) or a half-fetched model is NOT installed,
    so the line keeps the letter clock and the page never reads "aligned" off a partial install (2026-10-05)."""
    return (DONE.is_file() and (ENV / "bin" / "python").exists() and (ENV / ENV_DONE).is_file()
            and (MODELS / "acoustic").is_dir() and (MODELS / ".complete").is_file())


def running() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=0.3) as r:
            return bool(json.loads(r.read() or b"{}").get("ok"))
    except (OSError, ValueError):
        return False


def status() -> dict:
    out = {"applicable": applicable(), "better": better(), "by": _saved().get("by") or ("you" if _saved() else "default"),
           "default_by": default_by(),
           "default_on_ram_gb": default_on_ram_gb(), "installed": installed(),
           "installing": bool(_STATE["installing"]), "step": _STATE["step"], "error": _STATE["error"],
           "running": running() if applicable() else False, "platform": _conda_platform(),
           "tested_here": _os() in TESTED, "clock": "aligned" if (applicable() and better() and running()) else "letter"}
    if out["applicable"]:                    # who runs it here (round 17); the house's answer is unchanged
        p = _STATE.get("proc")
        out["owner"] = "launcher" if lane_owned() else ("player" if p is not None and p.poll() is None else "none")
    return out


def line_env() -> dict:
    """What a spoken line's house-speak gets. {} on the house (its env is never changed here)."""
    if not applicable():
        return {}
    if better() and running():
        return {"HOUSE_ALIGN_URL": "http://127.0.0.1:%d/align" % PORT, "HOUSE_MOUTH_ALIGN": "auto"}
    return {"HOUSE_MOUTH_ALIGN": "0"}      # the letter clock, and no probe of a port nobody serves


# ---- first-use install ---------------------------------------------------------------------------

def _fetch(url: str, dest: Path, timeout: float = 120.0) -> None:
    """Download to dest atomically. Tests replace this."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with crissa_egress.urlopen(url, timeout, "the aligner download") as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(tmp, dest)


def _get_micromamba(dest: Path | None = None, scratch: Path | None = None) -> None:
    dest, scratch = dest or MICROMAMBA, scratch or ROOT
    if dest.is_file():
        return
    arc = scratch / "micromamba.tar.bz2"
    _fetch("https://micro.mamba.pm/api/micromamba/%s/latest" % _conda_platform(), arc)
    with tarfile.open(arc, "r:bz2") as t:
        member = t.getmember("bin/micromamba")
        member.name = dest.name
        t.extract(member, dest.parent, filter="data")
    arc.unlink()
    dest.chmod(0o755)


def _make_env(run=subprocess.run, *, env: Path | None = None, mamba_root: Path | None = None,
              micromamba: Path | None = None, keep_pkgs: bool = False) -> None:
    env, mamba_root, micromamba = env or ENV, mamba_root or (ROOT / "mamba"), micromamba or MICROMAMBA
    if (env / ENV_DONE).is_file():
        return
    if env.exists():                       # an earlier create stopped part way: build it again from nothing
        shutil.rmtree(env, ignore_errors=True)
    cmd = [str(micromamba), "create", "-y", "-q", "-p", str(env), "-c", "conda-forge", *_specs()]
    e = dict(os.environ, MAMBA_ROOT_PREFIX=str(mamba_root))
    if keep_pkgs:       # a bundle: every package downloaded into OUR cache, none borrowed from the machine's
        cmd[1:1] = ["--no-rc"]
        e["CONDA_PKGS_DIRS"] = str(mamba_root / "pkgs")
    r = run(cmd, env=e, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError("the aligner env did not build: %s" % (r.stderr or r.stdout or "")[-300:])
    (env / ENV_DONE).write_text(json.dumps({"spec": _specs()}) + "\n")
    if not keep_pkgs:
        shutil.rmtree(mamba_root / "pkgs", ignore_errors=True)     # the download cache, not needed after


def _get_models(dest: Path | None = None, scratch: Path | None = None) -> None:
    dest, scratch = dest or MODELS, scratch or ROOT
    if (dest / "acoustic").is_dir() and (dest / ".complete").is_file():
        return
    api = "https://huggingface.co/api/models/%s/tree/%s?recursive=1" % (MODEL_REPO, MODEL_REV)
    listing = scratch / "model-tree.json"
    _fetch(api, listing)
    files = [e["path"] for e in json.loads(listing.read_text()) if e.get("type") == "file"
             and e["path"].split("/")[0] in ("acoustic", "dictionary", "g2p")]
    if not files:
        raise RuntimeError("the model listing named no files")
    for rel in files:
        _fetch("https://huggingface.co/%s/resolve/%s/%s" % (MODEL_REPO, MODEL_REV, rel), dest / rel)
    (dest / ".complete").write_text(MODEL_REV + "\n")
    listing.unlink()


# ---- the offline bundle (lib/crissa_bundle.py; the User 2026-10-05) --------------------------------------
# Made on a connected machine like this one: micromamba, the exact conda packages the env was solved to (an
# explicit lock + their files, so the offline machine builds the same env with --offline) and the model.

def build_bundle(out_dir: Path, run=subprocess.run, log=print) -> Path:
    import tempfile
    import crissa_bundle
    crissa_egress.refuse_install("making the aligner bundle")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="aligner-bundle-", dir=out_dir))
    try:
        scratch, folder = work / "scratch", work / "aligner"
        scratch.mkdir()
        log("aligner bundle: micromamba …")
        _get_micromamba(folder / "bin" / "micromamba", scratch)
        log("aligner bundle: solving and fetching the env's packages …")
        env, mamba_root = scratch / "env", scratch / "mamba"
        _make_env(run, env=env, mamba_root=mamba_root, micromamba=folder / "bin" / "micromamba", keep_pkgs=True)
        r = run([str(folder / "bin" / "micromamba"), "--no-rc", "env", "export", "-p", str(env), "--explicit", "--md5"],
                env=dict(os.environ, MAMBA_ROOT_PREFIX=str(mamba_root), CONDA_PKGS_DIRS=str(mamba_root / "pkgs")),
                capture_output=True, text=True, timeout=600)
        urls = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip().startswith(("http://", "https://"))]
        if r.returncode != 0 or not urls:
            raise RuntimeError("the env's package list could not be read: %s" % (r.stderr or "")[-300:])
        (folder / "pkgs").mkdir(parents=True)
        cache = mamba_root / "pkgs"
        for u in urls:
            bare = urllib.parse.urlsplit(u.split("#")[0])
            name = bare.path.rsplit("/", 1)[-1]
            # micromamba 2.x keeps a download under its URL (pkgs/https/<host>/<channel>/<subdir>/<file>); older
            # ones flat (pkgs/<file>). The bundle keeps whichever layout its own micromamba wrote.
            for src in (cache / bare.scheme / bare.netloc / bare.path.lstrip("/"), cache / name):
                if src.is_file():
                    dest = folder / "pkgs" / src.relative_to(cache)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    break
            else:
                raise RuntimeError("a locked package is not in the download cache: %s" % name)
        (folder / "lock.txt").write_text("@EXPLICIT\n" + "\n".join(urls) + "\n")
        log("aligner bundle: the model …")
        _get_models(folder / "english_us_arpa", scratch)
        shutil.rmtree(scratch)
        tar = crissa_bundle.write(folder, "aligner", pins={"model": MODEL_REPO, "model_rev": MODEL_REV,
                                  "spec": _specs(), "packages": len(urls)},
                                  out=out_dir / ("aligner-%s%s" % (crissa_bundle.platform_tag(), crissa_bundle.SUFFIX)))
        log("aligner bundle: %s" % tar)
        return tar
    finally:
        shutil.rmtree(work, ignore_errors=True)


def install_bundle(path: Path, run=subprocess.run, log=print) -> dict:
    """Install from a bundle with no network: the env from the bundle's own package files (--offline), the model
    copied. Refused before anything is written when the bundle is not this machine's."""
    import crissa_bundle
    folder, man = crissa_bundle.open_bundle(Path(path), "aligner")
    try:
        if (man.get("pins") or {}).get("model_rev") != MODEL_REV:
            raise crissa_bundle.BundleRefused("the bundle's model is not the one this player expects (%s)"
                                              % (man.get("pins") or {}).get("model_rev"))
        ROOT.mkdir(parents=True, exist_ok=True)
        MICROMAMBA.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / "bin" / "micromamba", MICROMAMBA)
        MICROMAMBA.chmod(0o755)
        mamba_root = ROOT / "mamba"
        crissa_bundle.copy_tree(folder / "pkgs", mamba_root / "pkgs")
        if not (ENV / ENV_DONE).is_file():
            if ENV.exists():               # an earlier create stopped part way: build it again from nothing
                shutil.rmtree(ENV, ignore_errors=True)
            log("aligner: building the env from the bundle (offline) …")
            r = run([str(MICROMAMBA), "--no-rc", "create", "-y", "-q", "--offline", "-p", str(ENV), "--file",
                     str(folder / "lock.txt")],
                    env=dict(os.environ, MAMBA_ROOT_PREFIX=str(mamba_root), CONDA_PKGS_DIRS=str(mamba_root / "pkgs")),
                    capture_output=True, text=True, timeout=3600)
            if r.returncode != 0:
                raise RuntimeError("the aligner env did not build from the bundle: %s"
                                   % (r.stderr or r.stdout or "")[-300:])
            (ENV / ENV_DONE).write_text(json.dumps({"spec": (man.get("pins") or {}).get("spec"), "from": "bundle"}) + "\n")
        shutil.rmtree(mamba_root / "pkgs", ignore_errors=True)
        crissa_bundle.copy_tree(folder / "english_us_arpa", MODELS)
        DONE.write_text(json.dumps({"at": time.time(), "platform": _conda_platform(), "spec": _specs(),
                                    "model_rev": MODEL_REV, "from": "bundle", "built_at": man.get("built_at")}) + "\n")
        log("aligner: installed from the bundle")
        return {"ok": True, "installed": installed()}
    finally:
        if not Path(path).is_dir():
            shutil.rmtree(folder, ignore_errors=True)


def install() -> None:
    """Runs on a background thread; progress in status()['step']."""
    steps = (("micromamba", _get_micromamba), ("aligner env", _make_env), ("model", _get_models))
    try:
        crissa_egress.refuse_install("Better mouths (the aligner)")
        ROOT.mkdir(parents=True, exist_ok=True)
        for name, fn in steps:
            _STATE["step"] = name
            fn()
        DONE.write_text(json.dumps({"at": time.time(), "platform": _conda_platform(), "spec": _specs(),
                                    "model_rev": MODEL_REV}) + "\n")
        _STATE.update(step="done", error=None)
        if better():
            start()
    except Exception as e:  # noqa: BLE001  any failure leaves the letter clock, and says why
        _STATE.update(error="%s: %s" % (type(e).__name__, str(e)[:300]))
    finally:
        _STATE["installing"] = False


# ---- the switch and the server -------------------------------------------------------------------

def set_better(on: bool, by: str = "you") -> dict:
    if not applicable():
        return dict(status(), ok=False, error="not here: this install runs its own aligner")
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps({"better": bool(on), "by": by}) + "\n")
    with _LOCK:
        if on and not installed() and not _STATE["installing"]:
            _STATE.update(installing=True, error=None, step="starting")
            threading.Thread(target=install, name="better-mouths-install", daemon=True).start()
        elif on and installed():
            start()
        elif not on:
            stop()
    return dict(status(), ok=True)


def _port_free() -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) != 0


def start() -> bool:
    if not (applicable() and installed()) or running():
        return running()
    if lane_owned():
        # ONE ALIGNER, THE LAUNCHER'S (round 17): its lane serves the lines, or is held on purpose (Close Agent, a Stop
        # from the Control Panel); a second copy here took its port and left its unit failing every 2 s on .101
        return False
    if not _port_free():
        _STATE["error"] = "port %d is taken by something else" % PORT
        return False
    log = open(ROOT / "aligner.log", "a")
    _STATE["proc"] = subprocess.Popen(
        [str(ENV / "bin" / "python"), str(SERVER), "--port", str(PORT), "--parent", str(os.getpid())],
        env=dict(os.environ, HOUSE_ALIGN_MFA_BUNDLE=str(MODELS), CUDA_VISIBLE_DEVICES=""),
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    return True


def stop() -> None:
    """The aligner and its MFA child: start() made it a session (and group) leader, so the group goes, not only
    the server (2026-10-05: terminating the server alone left its child)."""
    p = _STATE.get("proc")
    if p is not None and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                p.kill()
    _STATE["proc"] = None


def boot() -> None:
    """At player start: bring the aligner up if it is switched on and installed. An install a default started (Init's,
    by "init") that a restart cut short is carried on. Nothing else downloads by itself (2026-10-10: Init installs it,
    after the voice); with CRISSA_ALIGNER_RAM_RULE=1 a default that is on and not installed is switched on and installed
    in the background, the 2026-10-07 way."""
    if not applicable():
        return
    saved = _saved()
    if ram_rule() and not saved and default_on() and not installed():
        set_better(True, by="default")
        return
    if saved.get("better") and saved.get("by") in SET_BY_DEFAULT and better() and not installed():
        set_better(True, by=str(saved.get("by")))
        return
    if startup_install_due(saved):
        print("[aligner] better mouths are on by default and the aligner is not installed: installing it now",
              flush=True)
        set_better(True, by="init")
        warm_later()
        return
    if better() and installed():
        start()
        warm_later()


# ROUND 23 (the User 2026-10-15, .101 round 22: "yes to adding it at startup"): every start checks the aligner. Setup
# installs it after her voice; a Setup that never reached that step (the one-page walk moved on first) or an install a
# restart cut short is started here once Setup is done (never during it: Setup's own voice install is downloading then).
# A choice the person saved on the Connect page is theirs and is never overridden. Kill: CRISSA_ALIGNER_AT_START=0.
def _setup_done() -> bool:
    try:
        return bool(json.loads((crissa_home.HOME / "init.json").read_text(encoding="utf-8")).get("done"))
    except (OSError, ValueError, AttributeError):
        return False


def startup_install_due(saved: dict | None = None, env=None) -> bool:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_ALIGNER_AT_START", "1")).strip().lower() in ("0", "off", "false", "no"):
        return False
    saved = _saved() if saved is None else saved
    if saved and saved.get("by") not in SET_BY_DEFAULT:
        return False
    return _setup_done() and default_on() and not installed()


# WARM BEFORE HER FIRST LINE (round 23, .101: the first sentence after a start aligned in 554-577 ms and went on the
# letter clock, "0 of 1 pieces stamped"; warm, 232-250 ms): once the aligner answers, one short line of quiet is
# aligned and thrown away. Waits up to WARM_WAIT_S for the lane (the launcher's, or ours). Kill: CRISSA_ALIGNER_WARM=0.
WARM_WAIT_S = 180.0
_WARMED = {"done": False}


def _align_url() -> str:
    try:
        import crissa_ports
        return crissa_ports.url("align") + "/align"
    except Exception:  # noqa: BLE001
        return "http://127.0.0.1:%d/align" % PORT


def warm_wav_b64(seconds: float = 1.0, rate: int = 16000) -> str:
    """A second of very low noise as a 16-bit mono wav, base64: something to align, nothing to hear."""
    import base64
    import io
    import random
    import struct
    import wave
    rnd = random.Random(7)
    n = int(seconds * rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % n, *(rnd.randint(-40, 40) for _ in range(n))))
    return base64.b64encode(buf.getvalue()).decode("ascii")


def warm_once(post=None, wait_s: float = WARM_WAIT_S, sleep=time.sleep) -> dict:
    """Wait for the aligner's port, then align one throwaway line. Never raises."""
    if _WARMED["done"]:
        return {"state": "already"}
    url = _align_url()
    host_port = urllib.parse.urlsplit(url)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        try:
            with socket.create_connection((host_port.hostname or "127.0.0.1", host_port.port or PORT), timeout=1.0):
                break
        except OSError:
            sleep(1.0)
    else:
        return {"state": "no aligner", "waited_s": round(time.time() - t0, 1)}
    body = json.dumps({"text": "hello there", "wav_b64": warm_wav_b64()}).encode()
    t1 = time.time()
    try:
        if post is not None:
            post(url, body)
        else:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:  # noqa: BLE001  a warm-up that fails changes nothing: the first line is cold, as before
        return {"state": "error", "why": "%s: %s" % (type(e).__name__, str(e)[:120])}
    _WARMED["done"] = True
    ms = int((time.time() - t1) * 1000)
    print("[aligner] warmed in %d ms" % ms, flush=True)
    return {"state": "warm", "ms": ms}


def warm_later(env=None) -> bool:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_ALIGNER_WARM", "1")).strip().lower() in ("0", "off", "false", "no") or _WARMED["done"]:
        return False
    threading.Thread(target=warm_once, name="aligner-warm", daemon=True).start()
    return True
