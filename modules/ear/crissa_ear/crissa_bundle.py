"""Offline bundles: what a first-use installer downloads, carried in one file (the User 2026-10-05, W6).

WHY. The aligner, speech in and the face check each download hundreds of megabytes on first use. Under
kill-search those downloads are refused (lib/crissa_egress.py), so an offline machine needs another way in:
a bundle made on any machine that has the internet, carried over, and installed with no network at all.

WHO MAKES IT. The user, on their own connected machine: `build` runs the piece's own online install into a
scratch home and packs only what an offline install needs. Crissa distributes no bundle (each holds other
projects' software under their own licences, some GPL: see THIRD-PARTY-NOTICES.md); whether bundles are ever
offered for download is a separate decision (the User's and the lawyer's).

THE FILE. A plain tar (`<piece>-<platform>.crissa-bundle.tar`) of a folder holding `bundle.json`:

    {"schema": "crissa-bundle/1", "piece": "aligner"|"asr"|"oracle"|"kokoro", "platform": "<os>-<arch>" | "any",
     "python": "3.12" (only where wheels are tied to it), "pins": {...}, "built_at": <unix time>,
     "files": {"<relative path>": "<sha256>", ...}}

and the files it lists. `open_bundle` refuses the whole bundle, before anything is installed, on: another
schema or piece, another platform (or Python), a path that leaves the folder or a link, a file whose hash is
not the one listed, or a file not listed. Standard library only; the voice and oracle modules carry a copy.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path

SCHEMA = "crissa-bundle/1"
MANIFEST = "bundle.json"
SUFFIX = ".crissa-bundle.tar"


class BundleRefused(ValueError):
    """The bundle is not one this machine may install. The message says why."""


def platform_tag(plat: str | None = None, machine: str | None = None) -> str:
    p = plat or sys.platform
    m = (machine or _platform.machine()).lower()
    os_ = "darwin" if p == "darwin" else ("windows" if p.startswith("win") else "linux")
    arch = "arm64" if m in ("arm64", "aarch64") else ("x86_64" if m in ("x86_64", "amd64") else m)
    return "%s-%s" % (os_, arch)


def python_tag() -> str:
    return "%d.%d" % sys.version_info[:2]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write(folder: Path, piece: str, *, pins: dict, python: str | None = None, out: Path | None = None,
          platform: str | None = None) -> Path:
    """Seal `folder` (already holding the piece's files) as a bundle: manifest, then one tar beside it or at
    `out`. Returns the tar's path."""
    folder = Path(folder)
    files = {}
    for p in sorted(folder.rglob("*")):
        if p.is_symlink():
            raise BundleRefused("a bundle holds no links: %s" % p.relative_to(folder))
        if p.is_file() and p.name != MANIFEST:
            files[p.relative_to(folder).as_posix()] = sha256(p)
    man = {"schema": SCHEMA, "piece": piece, "platform": platform or platform_tag(), "pins": dict(pins),
           "built_at": int(time.time()), "files": files}
    if python:
        man["python"] = python
    (folder / MANIFEST).write_text(json.dumps(man, indent=1) + "\n")
    tar = Path(out) if out else folder.parent / ("%s-%s%s" % (piece, man["platform"], SUFFIX))
    tmp = tar.with_name(tar.name + ".part")
    with tarfile.open(tmp, "w") as t:
        for rel in [MANIFEST, *files]:
            t.add(folder / rel, arcname=rel, recursive=False)
    os.replace(tmp, tar)
    return tar


def _safe(rel: str) -> str:
    r = str(rel).replace("\\", "/")
    if not r or r.startswith("/") or any(part in ("", "..") for part in r.split("/")):
        raise BundleRefused("a path that leaves the bundle: %r" % rel)
    return r


def open_bundle(path: Path, piece: str, *, python: str | None = None, into: Path | None = None) -> tuple[Path, dict]:
    """Unpack and check a bundle; returns (folder, manifest). The caller installs from the folder and removes
    it. Refuses (BundleRefused) before anything is installed."""
    path = Path(path)
    dest = Path(into) if into else None
    try:
        if path.is_dir():
            src = path
        else:
            if dest is None:
                dest = Path(tempfile.mkdtemp(prefix="crissa-bundle-"))
            with tarfile.open(path, "r") as t:
                members = t.getmembers()
                for m in members:
                    _safe(m.name)
                    if not (m.isfile() or m.isdir()):
                        raise BundleRefused("a bundle holds files only: %s" % m.name)
                t.extractall(dest, members=members, filter="data")
            src = dest
        try:
            man = json.loads((src / MANIFEST).read_text())
        except (OSError, ValueError) as exc:
            raise BundleRefused("no readable %s in the bundle" % MANIFEST) from exc
        if man.get("schema") != SCHEMA:
            raise BundleRefused("not a Crissa bundle (%r)" % man.get("schema"))
        if man.get("piece") != piece:
            raise BundleRefused("this bundle is for %r, not %r" % (man.get("piece"), piece))
        if man.get("platform") not in ("any", platform_tag()):
            raise BundleRefused("this bundle was made on %s; this machine is %s. Make one on a machine like "
                                "this one." % (man.get("platform"), platform_tag()))
        if python and man.get("python") and man["python"] != python:
            raise BundleRefused("this bundle was made for Python %s; this is %s" % (man["python"], python))
        listed = {_safe(k): v for k, v in (man.get("files") or {}).items()}
        for p in src.rglob("*"):
            rel = p.relative_to(src).as_posix()
            if p.is_symlink():
                raise BundleRefused("a bundle holds no links: %s" % rel)
            if p.is_file() and rel != MANIFEST and rel not in listed:
                raise BundleRefused("a file the bundle does not list: %s" % rel)
        for rel, want in listed.items():
            f = src / rel
            if not f.is_file() or sha256(f) != want:
                raise BundleRefused("a file does not match its hash: %s" % rel)
        return src, man
    except BaseException:
        if not into and dest is not None:
            shutil.rmtree(dest, ignore_errors=True)
        raise


def copy_tree(src: Path, dest: Path) -> None:
    """Copy a checked folder into place (files only, as listed)."""
    for p in sorted(Path(src).rglob("*")):
        if p.is_file():
            d = Path(dest) / p.relative_to(src)
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, d)
