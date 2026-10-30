"""Argus names the guest from a labelled line-up. FaceID keeps AUTH.

the User 2026-08-16:

    "ask argus to look at my ref and another person's refs and tell us who 10:44:15 is.
     Can Argus take FaceID task with a labeled family portrait? Let FaceID be
     there for AUTH but let Argus try to name the guests with FaceID to serve
     as RL corrections for training."
    "Match game for entry and exits"

Measured before writing this, on four real door crops:

    faceid 0.7079 (the user)      -> argus "user"      high    1.2s
    faceid 0.7554 (the user)      -> argus "user"      medium  2.8s
    faceid 0.0931 (unnamed)   -> argus "another person"  high    2.1s
    faceid 0.5283 (unnamed)   -> argus "another person"  medium  3.5s

The two FaceID could not name were both another person, and both had minted a fresh
`s1` body instead. Argus discriminates rather than guessing — he does not answer
"another person" to a picture of the User — and does it from ONE composite image in a
couple of seconds, against FaceID's 107-262 frames on CPU.

Division of labour, unchanged where it matters:

  * FaceID remains the ONLY thing that can satisfy AUTH. A face bank match is
    a measurement; this is an opinion, and opinions do not unlock doors.
  * This only ever runs when FaceID has already declined to name the face, so
    it cannot override a real match — it fills the silence where the alternative
    is minting a stranger.
  * Nothing here writes to the face or voice bank. Per the provisional-scoring
    rule a single uncorroborated call does not change enrolment; it names THIS
    visit and is logged beside FaceID's score as correction data.

The judgement is entirely Argus's. This module builds a picture and checks that
the name he returns is one of the people who were actually on the board.
"""

from __future__ import annotations

from . import paths as _paths

import base64
import io
import json
import os
import pathlib
import time
import urllib.request
from typing import Any

def _default_people() -> pathlib.Path:
    explicit = os.environ.get("RAIL_FACE_PEOPLE", "").strip()
    if explicit:
        return pathlib.Path(explicit)
    root = os.environ.get("HOUSE_ID_ROOT", "").strip() or str(_paths.IDENTITY)
    return pathlib.Path(root) / "people"


PEOPLE_DIR = _default_people()
ENABLED = os.environ.get("RAIL_FACE_LINEUP", "1") not in ("0", "false", "no")
REF_PER = int(os.environ.get("RAIL_FACE_LINEUP_REFS", "3"))
CELL = int(os.environ.get("RAIL_FACE_LINEUP_CELL", "220"))
TIMEOUT = float(os.environ.get("RAIL_FACE_LINEUP_TIMEOUT", "45"))
# "high" is the strict setting; measured, medium was correct on 2 of 2.
MIN_CONF = os.environ.get("RAIL_FACE_LINEUP_MIN_CONF", "medium").strip().lower()
_RANK = {"low": 0, "medium": 1, "high": 2}

PROMPT = (
    "This single image is a face line-up. The rows labelled REFERENCE are known "
    "household members. The panel outlined in red and labelled QUERY is one person "
    "photographed at the door. Which reference row does the QUERY person belong to? "
    "Be brief. One short sentence, then ONE JSON object "
    '{"who":"<name from a REFERENCE row, or unknown>","confidence":"high|medium|low"}'
)


def enrolled() -> dict[str, list[pathlib.Path]]:
    """Everyone with face refs on disk, newest-looking first, capped at REF_PER."""
    out: dict[str, list[pathlib.Path]] = {}
    try:
        for d in sorted(PEOPLE_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith("_") or d.name.isupper():
                continue
            refs = sorted((d / "face" / "refs").glob("*.jpg"))[:REF_PER]
            if refs:
                out[d.name.lower()] = refs
    except OSError:
        return {}
    return out


def build_lineup(query: bytes | str | pathlib.Path,
                 people: dict[str, list[pathlib.Path]] | None = None) -> bytes | None:
    """One composite: labelled reference rows, then the QUERY panel in red.

    One image rather than several. Asked as "FIRST IMAGE... SECOND IMAGE..." the
    model spent its whole budget reasoning about which image was which and
    stopped on length without answering. Labels burned into the picture are
    unambiguous, so it answers in a sentence.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    people = people if people is not None else enrolled()
    if not people:
        return None
    pad, lbl = 14, 36
    cols = max(REF_PER, 1)
    w = pad + cols * (CELL + pad)
    h = pad + (len(people) + 1) * (CELL + lbl + pad)
    canvas = Image.new("RGB", (w, h), (18, 20, 22))
    dr = ImageDraw.Draw(canvas)
    y = pad
    for name, refs in people.items():
        dr.text((pad + 2, y + 10), f"REFERENCE: {name.upper()}", fill=(255, 230, 120))
        for i, p in enumerate(refs[:cols]):
            try:
                im = Image.open(p).convert("RGB").resize((CELL, CELL))
            except Exception:
                continue
            canvas.paste(im, (pad + i * (CELL + pad), y + lbl))
        y += CELL + lbl + pad
    try:
        qsrc = io.BytesIO(query) if isinstance(query, bytes) else str(query)
        q = Image.open(qsrc).convert("RGB").resize((CELL, CELL))
    except Exception:
        return None
    dr.rectangle([pad - 4, y + lbl - 4, pad + CELL + 4, y + lbl + CELL + 4],
                 outline=(255, 90, 60), width=3)
    dr.text((pad + 2, y + 10), "QUERY: WHO IS THIS PERSON?", fill=(255, 120, 90))
    canvas.paste(q, (pad, y + lbl))
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _parse(text: str, known: set[str]) -> dict[str, Any]:
    t = (text or "").strip()
    i, j = t.rfind("{"), t.rfind("}")
    if i < 0 or j <= i:
        return {"ok": False, "reason": "unparsed"}
    try:
        obj = json.loads(t[i:j + 1])
    except Exception:
        return {"ok": False, "reason": "unparsed"}
    who = str(obj.get("who") or "").strip().lower()
    conf = str(obj.get("confidence") or "").strip().lower()
    if who in ("", "unknown", "none", "null", "unclear"):
        return {"ok": True, "who": None, "confidence": conf, "reason": "unknown"}
    # Membership only — never a name he was not shown. Same rule as the exit
    # match game: he compares, this refuses anything off the board.
    if who not in known:
        return {"ok": True, "who": None, "confidence": conf, "reason": "off_board"}
    return {"ok": True, "who": who, "confidence": conf, "reason": "named"}


def name_face(query: bytes | str | pathlib.Path) -> dict[str, Any]:
    """Ask Argus which enrolled person this door face is. Never raises."""
    if not ENABLED:
        return {"ok": False, "reason": "disabled"}
    people = enrolled()
    if len(people) < 2:
        return {"ok": False, "reason": "too_few_enrolled"}
    img = build_lineup(query, people)
    if not img:
        return {"ok": False, "reason": "no_lineup"}
    from .argus_notify import VIDEO_MODEL, VIDEO_URL

    body = {
        "model": VIDEO_MODEL,
        "max_tokens": int(os.environ.get("RAIL_FACE_LINEUP_MAX_TOKENS", "1600")),
        "temperature": 0.0,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(img).decode()}},
            {"type": "text", "text": PROMPT},
        ]}],
    }
    t0 = time.time()
    try:
        req = urllib.request.Request(
            VIDEO_URL, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            j = json.load(r)
        msg = (j.get("choices") or [{}])[0].get("message") or {}
        txt = msg.get("content") or msg.get("reasoning") or ""
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}", "detail": str(e)[:120]}
    out = _parse(txt, set(people))
    out["latency"] = round(time.time() - t0, 2)
    out["candidates"] = sorted(people)
    return out


def confident_enough(res: dict[str, Any]) -> bool:
    """Does this call clear the confidence floor?"""
    if not res.get("ok") or not res.get("who"):
        return False
    return _RANK.get(str(res.get("confidence") or "").lower(), -1) >= _RANK.get(MIN_CONF, 1)
