"""A finished hub becomes its own avatar (the User 2026-10-26: "the store loop from portrait and backdrop to new loop in
library and a switch to pick which loop").

The store's hub factory (order.py steps 0-10) ends with a whole library folder: films, playlists, tiles, the phoneme
map, the tune and an inventory.json. The Avatar picker (lib/crissa_avatar) lists every folder under
<state home>/libraries/<key>/ that holds an inventory.json, and calls it a store hub when a pack.json sits beside it.
This module is the one step between them: it copies a finished library WHOLE into <state home>/libraries/<key>/ and
writes its pack.json receipt (source store, the order it came from, the day it was made). It never merges into the
library she is wearing (that is lib/crissa_import, which ADDS loops to a pose dir); a hub is a library of its own.

Refused before anything is written: a key the picker would not read, a source without inventory.json, a symlink
anywhere in the source, a source inside the destination (or the reverse), and a key that is already there unless
`replace` is asked for. The copy lands in a hidden part folder and is renamed into place, so the picker never lists
a half-copied library; a replaced library is moved aside first and removed only after the new one is in place.

OFF by default: CRISSA_HUB_INSTALL=1 turns it on, and never on the house's own install (its libraries are built by
intake and opened by the Composer's library picker).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

import crissa_home

SCHEMA = "crissa-hub/1"
INVENTORY = "inventory.json"
RECEIPT = "pack.json"
LIBRARIES_DIR = "libraries"           # the picker's folder (lib/crissa_avatar.LIBRARIES_DIR)
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class Refused(Exception):
    """Why a hub is not installed. One plain line."""


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_HUB_INSTALL", "0")).strip().lower() not in ("1", "on", "true", "yes"):
        return False
    return not crissa_home.is_house()


def libraries_dir(home: Path | None = None) -> Path:
    return (home or crissa_home.HOME) / LIBRARIES_DIR


def _check_source(src: Path) -> int:
    """Files in the source; refuses a symlink anywhere in it and a folder with no inventory.json."""
    if src.is_symlink():
        raise Refused("%s is a symlink: give the library folder itself" % src)
    if not src.is_dir():
        raise Refused("%s is not a folder" % src)
    if not (src / INVENTORY).is_file():
        raise Refused("%s has no inventory.json: not a finished library" % src)
    n = 0
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            if p.is_symlink():
                raise Refused("the library holds a symlink (%s): a hub ships its own files" % p.relative_to(src))
        n += len(filenames)
    return n


def install(src: str | os.PathLike, key: str, home: Path | None = None, *, order: str = "", name: str = "",
            replace: bool = False, now: float | None = None) -> dict:
    """Copy the finished library `src` to <state home>/libraries/<key>/ with its pack.json; answer what landed."""
    key = str(key or "").strip().lower()
    if not _KEY.match(key):
        raise Refused("key %r: lower-case letters, digits and dashes, 1-64 long" % key)
    src = Path(src).expanduser()
    files = _check_source(src)
    src = src.resolve()
    ld = libraries_dir(home)
    dest = ld / key
    ld_r = ld.resolve() if ld.exists() else ld.absolute()
    if src == ld_r or ld_r in src.parents or src in ld_r.parents:
        raise Refused("the library and the libraries folder overlap (%s, %s)" % (src, ld))
    if dest.exists() and not replace:
        raise Refused("%s is already installed: pass replace to put this one in its place" % key)
    ld.mkdir(parents=True, exist_ok=True)
    t = time.time() if now is None else float(now)
    part = ld / (".%s.part-%d" % (key, os.getpid()))
    if part.exists():
        shutil.rmtree(part)
    shutil.copytree(src, part, symlinks=True)
    receipt = {"schema": SCHEMA, "source": "store", "key": key, "order": str(order or ""),
               "made": time.strftime("%Y-%m-%d", time.localtime(t)), "files": files}
    old = part / RECEIPT
    if old.is_file():                     # a library that already carried a receipt keeps it under its own name
        receipt["previous"] = RECEIPT + ".orig"
        os.replace(old, part / receipt["previous"])
    (part / RECEIPT).write_text(json.dumps(receipt, indent=1) + "\n", encoding="utf-8")
    if name and not (part / "library.json").is_file():
        (part / "library.json").write_text(json.dumps({"name": str(name)}) + "\n", encoding="utf-8")
    aside = None
    if dest.exists():
        aside = ld / (".%s.old-%d" % (key, int(t)))
        os.replace(dest, aside)
    os.replace(part, dest)
    if aside is not None:
        shutil.rmtree(aside, ignore_errors=True)
    return {"ok": True, "key": key, "dir": str(dest), "files": files, "replaced": aside is not None,
            "receipt": receipt}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Install a finished hub library as its own avatar (<state home>/libraries/<key>/).")
    ap.add_argument("library", help="the finished library folder (holds inventory.json)")
    ap.add_argument("--key", required=True, help="the avatar's key in the picker (lower-case, dashes)")
    ap.add_argument("--order", default="", help="the store order it came from (recorded in pack.json)")
    ap.add_argument("--name", default="", help="the label the picker shows, when the library names none")
    ap.add_argument("--replace", action="store_true", help="put this library in place of one with the same key")
    a = ap.parse_args(argv)
    if not enabled():
        print("hub install is off (CRISSA_HUB_INSTALL=1 turns it on; never on the house)")
        return 2
    try:
        out = install(a.library, a.key, order=a.order, name=a.name, replace=a.replace)
    except Refused as e:
        print("refused: %s" % e)
        return 3
    print(json.dumps(out, indent=1))
    return 0
