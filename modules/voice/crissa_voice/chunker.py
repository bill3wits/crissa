"""The sentence chunker for the chunked voices (Pocket, Turbo), 2026-10-06.

Split at sentence ends, a sentence longer than max_chars splits at a comma (else a space), short sentences merge up to
min_chars (the first chunk: first_min). 80 chars was the smallest size that still sounded natural in the
2026-10-06 Turbo sweep. first_max (Pocket only) caps the FIRST chunk so the first clip is short and she
starts sooner; later chunks use max_chars. No torch, no model: tests import it bare.
"""
from __future__ import annotations

import re

_ABBR = r"(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bvs\.)(?<!\be\.g\.)(?<!\bi\.e\.)"
_RE_SENT = re.compile(_ABBR + r"(?<=[.!?;])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")


def split_long(s: str, max_chars: int) -> list[str]:
    out = []
    while max_chars > 0 and len(s) > max_chars:
        cut = s.rfind(", ", 0, max_chars)
        if cut < max_chars // 3:
            cut = s.rfind(" ", 0, max_chars)
        if cut <= 0:
            break
        out.append(s[:cut + 1].strip())
        s = s[cut + 1:].strip()
    if s:
        out.append(s)
    return out


def chunk_text(text: str, first_min: int = 0, min_chars: int = 0, max_chars: int = 80,
               first_max: int = 0) -> list[str]:
    """Sentences, merged up to min_chars (first chunk: first_min), long ones split at commas.
    first_max > 0: the first chunk is cut again to at most first_max chars (a clause), the rest re-chunked."""
    sents: list[str] = []
    for s in _RE_SENT.split((text or "").strip()):
        sents.extend(split_long(s.strip(), max_chars))
    chunks: list[str] = []
    buf = ""
    for s in sents:
        if not s:
            continue
        buf = f"{buf} {s}".strip() if buf else s
        need = first_min if not chunks else min_chars
        if len(buf) >= need:
            chunks.append(buf)
            buf = ""
    if buf:
        if chunks and len(buf) < min_chars // 2 and len(chunks[-1]) + len(buf) <= max_chars:
            chunks[-1] = f"{chunks[-1]} {buf}"
        else:
            chunks.append(buf)
    if first_max > 0 and chunks and len(chunks[0]) > first_max:
        head = split_long(chunks[0], first_max)
        chunks = [head[0], " ".join(head[1:])] + chunks[1:] if len(head) > 1 else chunks
        chunks = [c for c in chunks if c]
    return chunks
