"""Crash-safe small-file IO for rails state (2026-07-29 review finding).

A bare ``path.write_text`` corrupts JSON state on crash mid-write; history
jsonl appends grow forever. One shared helper for both, no new deps.
"""
from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write via same-dir tmp + os.replace so readers never see a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def append_jsonl_capped(
    path: Path,
    line: str,
    *,
    max_bytes: int = 5 * 1024 * 1024,
    keep_lines: int = 500,
) -> None:
    """Append one jsonl line; when the file outgrows max_bytes, atomically
    rewrite it down to the newest keep_lines. Size check is O(1) per append;
    the rewrite is rare and bounded by the cap itself."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(line.rstrip("\n") + "\n")
    try:
        if path.stat().st_size <= max_bytes:
            return
        tail = path.read_text().splitlines()[-keep_lines:]
        atomic_write_text(path, "\n".join(tail) + "\n")
    except OSError:
        pass
