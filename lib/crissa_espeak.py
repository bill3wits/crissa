"""eSpeak NG for her mouth: found, installed at start, said when it is not (round 40, the User 2026-10-21: "just bake it
in, it is so cheap. No one wants poor mouth placements if the fix is free.").

The mouth reads WHICH shape from a word's phonemes when eSpeak NG is on this machine (lib/mouth_viseme, the speller);
without it the letter rules stand. eSpeak NG is GPL-3.0: Crissa CALLS an installed one and never ships it (the export
refuses its files in a pack). So the launcher installs it at start through this machine's own package manager, like the
pip extras:

  macOS   `brew install espeak-ng` (no admin needed) when Homebrew is here
  Linux   `sudo -n apt-get install -y espeak-ng`: `-n` never asks for a password, so it installs only where this user may
          do it without one; anywhere else it says the command to run, once

  Linux, no admin   the UPSTREAM build, fetched on this machine (the User: "truly automatic and no prompt"): rhasspy/piper's
          release 2023.11.14-2 for this machine's CPU (x86_64 / aarch64), sha256-pinned below, unpacked into
          <state home>/espeak-ng/ - its espeak-ng, libespeak-ng and espeak-ng-data only. The same build the house runs
          (~/.local/piper; its espeak-ng and en_dict are byte-equal). Downloaded from upstream like the model
          downloads; nothing of it is in any pack. kill-search refuses it (crissa_egress.refuse_install).

A failed try is remembered in <state home>/espeak.json and not repeated for RETRY_S, so a machine without admin rights
is not asked again on every start; Setup's done plate names the command only where every path failed (offline). Never on the house (its
mouth uses Piper's own copy, HOUSE_ESPEAK_DIR). Kill CRISSA_ESPEAK_INSTALL=0 (find it, install nothing).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import crissa_home

RETRY_S = 7 * 86400
STATE = "espeak.json"
USER_DIR = "espeak-ng"
UPSTREAM_SOURCE = "https://github.com/rhasspy/espeak-ng (eSpeak NG, GPL-3.0); built into https://github.com/rhasspy/piper"
UPSTREAM = {
    "x86_64": ("https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz",
               "a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992"),
    "aarch64": ("https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz",
                "fea0fd2d87c54dbc7078d0f878289f404bd4d6eea6e7444a77835d1537ab88eb"),
}


def install_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_ESPEAK_INSTALL", "1")).strip().lower() not in ("0", "off", "false", "no")


def found(env=None, which=shutil.which, home: Path | None = None) -> str:
    """The espeak-ng this machine's mouth would call: HOUSE_ESPEAK_DIR's copy when set (the house's Piper), else the
    one on PATH. '' when there is none."""
    e = os.environ if env is None else env
    d = str(e.get("HOUSE_ESPEAK_DIR") or "").strip()
    if d and (Path(d) / "espeak-ng").is_file():
        return str(Path(d) / "espeak-ng")
    got = which("espeak-ng")
    if got:
        return str(got)
    for b in brew_dirs(env=e):                    # a Mac app opened from Finder has no Homebrew on its PATH
        if (b / "espeak-ng").is_file():
            return str(b / "espeak-ng")
    u = user_dir(home) / "espeak-ng"
    return str(u) if u.is_file() and (user_dir(home) / "espeak-ng-data").is_dir() else ""


# HOMEBREW OFF THE PATH (round 40, crissa-3e): an app opened from Finder starts with launchd's PATH
# (/usr/bin:/bin:/usr/sbin:/sbin), so neither brew nor what it installed is found by name. Homebrew's own prefixes:
# Apple silicon /opt/homebrew, Intel /usr/local. macOS only. Kill CRISSA_ESPEAK_BREW_DIRS=0.
BREW_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def brew_dirs(platform: str | None = None, env=None) -> list[Path]:
    e = os.environ if env is None else env
    if (platform or sys.platform) != "darwin" or str(e.get("CRISSA_ESPEAK_BREW_DIRS", "1")).strip().lower() in (
            "0", "off", "false", "no"):
        return []
    return [Path(d) for d in BREW_DIRS]


def user_dir(home: Path | None = None) -> Path:
    return Path(home or crissa_home.HOME) / USER_DIR


def invocation(binary: str) -> tuple[list[str], dict]:
    """How to run the espeak-ng found(): a system one as it is; a release copy (the house's Piper, the user-space
    fetch) with its own library and data, through the dynamic loader when its file is not executable."""
    b = Path(binary)
    if not binary or b.parent == Path(shutil.which("espeak-ng") or "/nonexistent").parent:
        return [binary], {}
    env = {"LD_LIBRARY_PATH": str(b.parent), "ESPEAK_DATA_PATH": str(b.parent / "espeak-ng-data")}
    if os.access(b, os.X_OK):
        return [str(b)], env
    loader = next((x for x in ("/lib64/ld-linux-x86-64.so.2", "/lib/ld-linux-aarch64.so.1") if Path(x).exists()), "")
    return ([loader, str(b)] if loader else [str(b)]), env


def command(platform: str | None = None, which=shutil.which) -> list[str] | None:
    """The one command that installs it here without asking anything, or None when there is none."""
    plat = platform or sys.platform
    if plat == "darwin":
        brew = which("brew") or next((str(b / "brew") for b in brew_dirs(plat) if (b / "brew").is_file()), None)
        return [brew, "install", "espeak-ng"] if brew else None
    if plat.startswith("linux") and which("apt-get") and which("sudo"):
        return ["sudo", "-n", "apt-get", "install", "-y", "espeak-ng"]
    return None


def how_to(platform: str | None = None) -> str:
    """What a person types to install it themselves."""
    plat = platform or sys.platform
    if plat == "darwin":
        return "brew install espeak-ng"
    return "sudo apt install espeak-ng"


def upstream_on(env=None) -> bool:
    """The user-space upstream fetch (Linux without admin). Kill CRISSA_ESPEAK_UPSTREAM=0 (the done plate's line)."""
    e = os.environ if env is None else env
    return install_on(e) and str(e.get("CRISSA_ESPEAK_UPSTREAM", "1")).strip().lower() not in ("0", "off", "false", "no")


def _arch() -> str:
    import platform as _p
    m = _p.machine().lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(m, m)


def _download(url: str, dst: Path) -> None:
    import crissa_egress
    crissa_egress.refuse_install("espeak-ng (the mouth's phonemes)")
    with crissa_egress.urlopen(url, 300.0, what="espeak-ng for the mouth") as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)


KEEP = ("piper/espeak-ng", "piper/libespeak-ng.so", "piper/espeak-ng-data/")


def fetch_upstream(home: Path | None = None, arch: str | None = None, log=print, download=None) -> dict:
    """The pinned upstream build into <state home>/espeak-ng/: download, check its sha256, unpack only espeak-ng's own
    files (never a path outside the folder), make the binary runnable. {"ok", "dir"} or {"ok": False, "why"}."""
    import hashlib
    import tarfile
    import tempfile
    a = arch or _arch()
    if a not in UPSTREAM:
        return {"ok": False, "why": "no upstream build for this CPU (%s)" % a}
    url, sha = UPSTREAM[a]
    dest = user_dir(home)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(dest.parent)) as td:
            tgz = Path(td) / "release.tar.gz"
            log("Crissa: fetching espeak-ng for better mouths (no admin here): %s" % url)
            (download or _download)(url, tgz)
            h = hashlib.sha256()
            with open(tgz, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != sha:
                return {"ok": False, "why": "the download's sha256 is not the pinned one"}
            stage = Path(td) / "out"
            stage.mkdir()
            with tarfile.open(tgz, "r:gz") as tf:
                for m in tf.getmembers():
                    name = m.name
                    if not name.startswith(KEEP) or ".." in Path(name).parts or name.startswith("/"):
                        continue
                    if not (m.isfile() or m.isdir() or m.issym()):
                        continue
                    if m.issym() and (Path(m.linkname).is_absolute() or ".." in Path(m.linkname).parts):
                        continue
                    tf.extract(m, stage, filter="data") if hasattr(tarfile, "data_filter") else tf.extract(m, stage)
            got = stage / "piper"
            if not (got / "espeak-ng").is_file() or not (got / "espeak-ng-data").is_dir():
                return {"ok": False, "why": "the release did not carry espeak-ng and its data"}
            (got / "espeak-ng").chmod(0o755)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(got), str(dest))
        log("Crissa: espeak-ng (GPL-3.0, %s) is in %s" % (UPSTREAM_SOURCE.split(" ")[0], dest))
        return {"ok": True, "dir": str(dest)}
    except Exception as ex:  # noqa: BLE001  offline, refused, a bad archive: the letter rules stand
        return {"ok": False, "why": "%s: %s" % (type(ex).__name__, ex)}


def _state_path(home: Path | None = None) -> Path:
    return Path(home or crissa_home.HOME) / STATE


def status(home: Path | None = None, env=None, which=shutil.which) -> dict:
    """{"found": path or "", "tried": {...} or {}, "how": the command to type}."""
    try:
        tried = json.loads(_state_path(home).read_text(encoding="utf-8"))
        tried = tried if isinstance(tried, dict) else {}
    except (OSError, ValueError):
        tried = {}
    return {"found": found(env, which, home), "tried": tried, "how": how_to()}


def ensure(home: Path | None = None, env=None, which=shutil.which, run=subprocess.run, log=print,
           now: float | None = None, platform: str | None = None, download=None) -> dict:
    """At start: installed already, installed now, or not (and why, once). Never raises, never stops Crissa."""
    e = os.environ if env is None else env
    t = time.time() if now is None else now
    if crissa_home.is_house():
        return {"ok": bool(found(e, which, home)), "why": "the house uses its own copy"}
    have = found(e, which, home)
    if have:
        return {"ok": True, "found": have}
    if not install_on(e):
        log("Crissa: better mouths read words by their sounds with espeak-ng, which is not here: %s" % how_to(platform))
        return {"ok": False, "why": "install off (CRISSA_ESPEAK_INSTALL=0)"}
    st = status(home, e, which).get("tried") or {}
    if st and not st.get("ok") and t - float(st.get("at") or 0) < RETRY_S:
        return {"ok": False, "why": "tried %s: %s" % (time.strftime("%Y-%m-%d", time.localtime(float(st.get("at") or 0))),
                                                      st.get("why") or "")}
    cmd = command(platform, which)
    if not cmd:
        rec = {"ok": False, "at": t, "why": "no package manager this user may use without a password"}
    else:
        log("Crissa: installing espeak-ng for better mouths: %s" % " ".join(cmd))
        try:
            r = run(cmd, capture_output=True, text=True, timeout=600)
            ok = r.returncode == 0 and bool(found(e, which, home))
            why = "" if ok else ((r.stderr or r.stdout or "").strip().splitlines() or ["exit %d" % r.returncode])[-1][:200]
        except Exception as ex:  # noqa: BLE001
            ok, why = False, "%s: %s" % (type(ex).__name__, ex)
        rec = {"ok": ok, "at": t, "cmd": " ".join(cmd), "why": why}
    if not rec["ok"] and (platform or sys.platform).startswith("linux") and upstream_on(e):
        # no admin here: the upstream build, fetched into the state home (never asks anything)
        got = fetch_upstream(home, log=log, download=download)
        if got.get("ok") and found(e, which, home):
            rec = {"ok": True, "at": t, "how": "upstream", "dir": got["dir"], "after": rec.get("why") or ""}
        else:
            rec = dict(rec, upstream=got.get("why") or "not found after unpacking")
    try:
        p = _state_path(home)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec), encoding="utf-8")
    except OSError:
        pass
    if rec["ok"]:
        log("Crissa: espeak-ng installed; her mouth reads words by their sounds")
    else:
        log("Crissa: espeak-ng is not installed (%s); her mouth uses the letter rules until it is: %s"
            % (rec["why"], how_to(platform)))
    return rec


def done_line(home: Path | None = None, env=None, which=shutil.which) -> str:
    """Setup's done plate: one line while it is missing, else nothing."""
    if crissa_home.is_house() or found(env, which, home):
        return ""
    return "Better mouths: install espeak-ng and she reads words by their sounds (%s)." % how_to()
