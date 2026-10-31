"""Import Fable-prepped room-mic WAVs → house-id voice bank (ECAPA re-enroll).

Fable drops clips; Grok wire embeds + writes bank JSON. No live coach required.

Inbox layout (default under HOUSE_ID_ROOT):

  site/reenroll-inbox/
    README.md
    <regular_id>/
      01_on.wav      # or any *.wav — sorted by name
      02_off.wav
    <user_id>/
      01_on.wav
      02_off.wav

Also accepts flat files: inbox/<regular_id>_on.wav, <regular_id>_off.wav

CLI:
  house-enroll import-clips
  house-enroll import-clips --person <regular_id> --replace
  house-enroll import-clips --dir /path/to/inbox --verify
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import wave
from pathlib import Path
from typing import Any

from .person import display_name, ensure_person
from .site import Site, load_site

_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
import ecapa_embed  # noqa: E402


def default_inbox(root: Path | None = None) -> Path:
    from .paths import house_id_root

    r = root or house_id_root()
    return r / "site" / "reenroll-inbox"


def ensure_inbox_scaffold(inbox: Path) -> None:
    """Create inbox + README for Fable (idempotent)."""
    inbox.mkdir(parents=True, exist_ok=True)
    people = inbox_people()
    for person in people:
        (inbox / person["id"]).mkdir(exist_ok=True)
    readme = inbox / "README.md"
    # Always refresh so angle order stays SoT (the User 2026-08-12: on→off per person).
    readme.write_text(
        inbox_readme(people),
        encoding="utf-8",
    )


def inbox_people() -> list[dict[str, str]]:
    """Who the re-enrol inbox is laid out for: ``inbox_people`` in house-local.json.

    Each row is ``{"id": ..., "display": ...}``. Generic (switch on, or no file):
    nobody - the inbox gets its README and no person folders.
    """
    from .local_values import local

    rows = local("inbox_people", [])
    out: list[dict[str, str]] = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and str(r.get("id") or "").strip():
            pid = str(r["id"]).strip().lower()
            out.append({"id": pid, "display": str(r.get("display") or pid)})
    return out


def inbox_readme(people: list[dict[str, str]]) -> str:
    """The inbox README, its record order and table drawn from ``people``."""
    if not people:
        people = [{"id": "<person_id>", "display": "Person"}]
    order = " → ".join(
        f"{p['display'][:1]} {angle}" for p in people for angle in ("on", "off")
    )
    rows = []
    n = 0
    for p in people:
        for angle, stem in (("on", "01_on"), ("off", "02_off")):
            n += 1
            rows.append(
                f"| {n} | {p['display']:<8} | {angle:<5} | `{p['id']}/{stem}.wav` |"
            )
    flat = [f"`{people[0]['id']}_on.wav`", f"`{people[0]['id']}_off.wav`"]
    if len(people) > 1:
        flat.append(f"`{people[1]['id']}_on.wav`")
    example = people[1]["id"] if len(people) > 1 else people[0]["id"]
    return (
        _README_TEMPLATE.replace("{order}", order)
        .replace("{rows}", "\n".join(rows))
        .replace("{flat}", ", ".join(flat))
        .replace("{example}", example)
    )


_README_TEMPLATE = """# Re-enroll inbox (Fable prep → Grok wire)

Drop **room / beam mic** WAVs here (not Cosy clones). ECAPA only.

## Record order (SoT)

**{order}**

| # | Speaker  | Angle | Drop path |
|---|----------|-------|-----------|
{rows}

Same paragraph / natural speech for every clip. Live room / beam mic.

Or flat: {flat}, …

## Specs

| Field | Prefer |
|-------|--------|
| Format | WAV mono 16 kHz (other rates resampled) |
| Duration | ≥ **2 s** each (P0 min-duration); full paragraph better |
| Content | Same house paragraph / natural speech — **live room mic** |
| Names | Sortable prefixes (`01_`, `02_`) |

## Import (Grok)

```bash
<identity>/modules/id/scripts/house-enroll import-clips --replace --verify
<identity>/modules/id/scripts/house-enroll import-clips --person {example} --replace
```

`--replace` overwrites `people/<id>/voice/001.json`, `002.json`, …  
Without replace, appends next free slot.

After import, rail-stt reloads bank on next process start (or restart `rail-stt`).
"""


def _wav_duration_s(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


def _peak_pcm(path: Path) -> int:
    import array

    try:
        with wave.open(str(path), "rb") as w:
            a = array.array("h")
            a.frombytes(w.readframes(w.getnframes()))
        return max(abs(x) for x in a) if a else 0
    except Exception:
        return 0


def discover_person_wavs(inbox: Path, person_id: str) -> list[Path]:
    """Sorted WAVs for one person from subdir or flat name prefix."""
    pid = person_id.strip().lower()
    found: list[Path] = []
    sub = inbox / pid
    if sub.is_dir():
        found.extend(sorted(sub.glob("*.wav")))
        found.extend(sorted(sub.glob("*.WAV")))
    # flat: <id>_*.wav or <id>-*.wav
    for p in sorted(inbox.glob(f"{pid}_*.wav")) + sorted(inbox.glob(f"{pid}-*.wav")):
        if p not in found:
            found.append(p)
    for p in sorted(inbox.glob(f"{pid}_*.WAV")):
        if p not in found:
            found.append(p)
    # dedupe preserve order
    out: list[Path] = []
    seen = set()
    for p in found:
        k = str(p.resolve())
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def list_inbox_people(inbox: Path) -> list[str]:
    people: set[str] = set()
    if not inbox.is_dir():
        return []
    for p in inbox.iterdir():
        if p.is_dir() and not p.name.startswith((".", "_")) and p.name.lower() != "readme":
            if list(p.glob("*.wav")) or list(p.glob("*.WAV")):
                people.add(p.name.lower())
        elif p.is_file() and p.suffix.lower() == ".wav":
            stem = p.stem.lower()
            # <id>_on → <id>
            if "_" in stem:
                people.add(stem.split("_", 1)[0])
            elif "-" in stem:
                people.add(stem.split("-", 1)[0])
    return sorted(people)


def import_one_wav(
    site: Site,
    person_id: str,
    wav_src: Path,
    *,
    slot: int | None = None,
    angle: str | None = None,
    min_sec: float = 1.5,
    min_peak: int | None = None,
) -> dict[str, Any]:
    """Embed one WAV and write people/<id>/voice/NNN.json + copy wav."""
    person_id = person_id.strip().lower()
    voice_dir = ensure_person(person_id, root=site.root) / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)

    if not wav_src.is_file():
        return {"ok": False, "error": "missing_file", "path": str(wav_src)}

    dur = _wav_duration_s(wav_src)
    if dur + 1e-9 < min_sec:
        return {
            "ok": False,
            "error": "short_clip",
            "path": str(wav_src),
            "duration_s": round(dur, 3),
            "min_sec": min_sec,
        }

    pk = _peak_pcm(wav_src)
    thr_peak = site.min_peak if min_peak is None else min_peak
    if pk < thr_peak:
        return {
            "ok": False,
            "error": "weak_peak",
            "path": str(wav_src),
            "peak": pk,
            "min_peak": thr_peak,
        }

    with wave.open(str(wav_src), "rb") as w:
        raw = w.readframes(w.getnframes())
        sr = w.getframerate()
    emb = ecapa_embed.embed_pcm16(raw, sr=sr)
    if emb is None:
        return {"ok": False, "error": "embed_failed", "path": str(wav_src)}

    if slot is None:
        n = 1
        while (voice_dir / f"{n:03d}.json").is_file():
            n += 1
        slot = n

    stem = f"reenroll_{int(time.time())}_{slot}"
    dest_wav = voice_dir / f"{stem}.wav"
    shutil.copy2(wav_src, dest_wav)

    angle = angle or wav_src.stem
    rec = {
        "person_id": person_id,
        "modality": "voice",
        "model": site.voice_model,
        "dim": site.voice_dim,
        "encoding": [float(x) for x in emb.tolist()],
        "source": "fable_prep_import",
        "site_id": site.site_id,
        "cue": "fable_prepped_clip",
        "angle": angle,
        "station": "desk",
        "pose": "on" if "on" in wav_src.stem.lower() else (
            "off" if "off" in wav_src.stem.lower() else "unknown"
        ),
        "take_plan_id": f"import_{wav_src.stem}",
        "wav": str(dest_wav),
        "wav_src": str(wav_src.resolve()),
        "peak": pk,
        "duration_s": round(dur, 3),
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    jp = voice_dir / f"{slot:03d}.json"
    jp.write_text(json.dumps(rec, indent=2) + "\n")
    print(
        f"  OK {person_id} slot={slot:03d} dur={dur:.1f}s peak={pk} "
        f"← {wav_src.name} → {jp.name}",
        flush=True,
    )
    return {
        "ok": True,
        "person_id": person_id,
        "slot": slot,
        "json": str(jp),
        "wav": str(dest_wav),
        "duration_s": round(dur, 3),
        "peak": pk,
        "self_sim": 1.0,  # emb vs self after write
    }


def verify_person_bank(site: Site, person_id: str) -> dict[str, Any]:
    """Self-consistency: each emb matches person best open-set (approx via in-bank)."""
    import numpy as np

    person_id = person_id.strip().lower()
    voice_dir = ensure_person(person_id, root=site.root) / "voice"
    embs: list[tuple[str, np.ndarray]] = []
    for f in sorted(voice_dir.glob("0*.json")):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        enc = rec.get("encoding")
        if not enc:
            continue
        e = np.asarray(enc, dtype=np.float32)
        n = float(np.linalg.norm(e))
        if n < 1e-8:
            continue
        embs.append((f.name, e / n))
    if len(embs) < 1:
        return {"person_id": person_id, "n": 0, "pairwise_min": None, "ok": False}
    # pairwise min cosine within person
    mins = []
    for i in range(len(embs)):
        for j in range(i + 1, len(embs)):
            mins.append(float(np.dot(embs[i][1], embs[j][1])))
    pairwise_min = min(mins) if mins else 1.0
    return {
        "person_id": person_id,
        "n": len(embs),
        "files": [a for a, _ in embs],
        "pairwise_min": round(pairwise_min, 4) if mins else None,
        "ok": pairwise_min >= 0.25 if mins else True,  # soft: same speaker bank
    }


def import_clips(
    site: Site | None = None,
    *,
    inbox: Path | None = None,
    person_ids: list[str] | None = None,
    replace: bool = False,
    verify: bool = True,
    min_sec: float = 1.5,
) -> dict[str, Any]:
    site = site or load_site()
    inbox = inbox or default_inbox(site.root)
    ensure_inbox_scaffold(inbox)

    if not ecapa_embed.available():
        return {
            "ok": False,
            "error": f"ECAPA unavailable: {ecapa_embed._err}",
            "hint": "Use HOUSE_ENROLL_PYTHON=<the id env's python>",
        }

    people = person_ids or list_inbox_people(inbox)
    if not people:
        return {
            "ok": False,
            "error": "empty_inbox",
            "inbox": str(inbox),
            "hint": "Fable: drop WAVs under site/reenroll-inbox/<person>/",
        }

    results: dict[str, Any] = {
        "ok": True,
        "inbox": str(inbox),
        "people": {},
        "written": [],
        "failed": [],
    }

    for pid in people:
        wavs = discover_person_wavs(inbox, pid)
        if not wavs:
            results["failed"].append({"person_id": pid, "error": "no_wavs"})
            continue
        print(f"\n=== import {pid} ({len(wavs)} clips) ===", flush=True)
        person_written = []
        for i, wav in enumerate(wavs, 1):
            slot = i if replace else None
            r = import_one_wav(
                site,
                pid,
                wav,
                slot=slot,
                min_sec=min_sec,
            )
            if r.get("ok"):
                person_written.append(r)
                results["written"].append(r)
            else:
                results["failed"].append(r)
                print(f"  FAIL {wav.name}: {r.get('error')} {r}", flush=True)
        entry: dict[str, Any] = {"clips": len(wavs), "ok": len(person_written), "rows": person_written}
        if verify and person_written:
            entry["verify"] = verify_person_bank(site, pid)
            print(f"  verify {entry['verify']}", flush=True)
        results["people"][pid] = entry

    results["ok"] = bool(results["written"]) and not any(
        f.get("error") == "embed_failed" for f in results["failed"]
    )
    return results


def print_import_report(out: dict[str, Any]) -> int:
    print("\n── import-clips report ──")
    print(f"  inbox: {out.get('inbox')}")
    if out.get("error"):
        print(f"  ERROR: {out['error']}")
        if out.get("hint"):
            print(f"  hint: {out['hint']}")
        return 1
    for pid, info in (out.get("people") or {}).items():
        print(f"  {pid}: {info.get('ok')}/{info.get('clips')} written")
        v = info.get("verify") or {}
        if v:
            print(f"       pairwise_min={v.get('pairwise_min')} n={v.get('n')} ok={v.get('ok')}")
    print(f"  total written: {len(out.get('written') or [])}")
    print(f"  failed: {len(out.get('failed') or [])}")
    if out.get("failed"):
        for f in out["failed"][:12]:
            print(f"    - {f}")
    print("  Next: sudo systemctl restart rail-stt   # reload voice bank")
    return 0 if out.get("ok") and out.get("written") else 1
