"""Live ASR A/B: parakeet-onnx (:8094) vs Audio8 ark-asr (CPU).

Uses the **same capture path as house-init / enroll**:
  - release continuous ear (mic exclusive — measured: ear open → silent clips)
  - go_beep (GO when tone ends) → record_fixed (parec byte-count) → done_chirp
  - reopen ear when the session ends

No dead pre-record lag. After STOP, decode is labeled so the operator does not
speak during the ark CPU wall.

  modules/id/scripts/house-enroll asr-ab
  modules/id/scripts/house-enroll asr-ab --seconds 8
  <the pack>/scripts/house-init asr-ab   # via house-enroll
"""
from __future__ import annotations

from .. import paths as _paths

import json
import os
import re
import sys
import time
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .paths import rails_path
from typing import Any

from . import cues
from .site import Site, load_site
from .voice_session import (
    peak_pcm,
    record_fixed,
    release_ear_for_enroll,
    reopen_ear_after_enroll,
)

# the house A/B-ed a parakeet engine on its own port; a pack ships none, so the A/B runs only against one named here
PARAKEET_URL = os.environ.get("PARAKEET_URL", "")
ARK_ROOT = Path(os.environ.get("ARK_ASR_ROOT", str(_paths.STATE / "asr" / "model")))
os.environ.setdefault("ARK_ASR_CPU", "1")
os.environ.setdefault("HF_HOME", "/tmp/ark_asr_hf_home")
os.environ.setdefault("TRANSFORMERS_CACHE", "/tmp/ark_asr_hf_home/transformers")

DEFAULT_SCRIPT = rails_path("scripts", "asr-ab-readaloud.txt")
RESULTS_PARENT = rails_path("bench", "presence-abc", "results")
SR = 16000


def _ts_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _load_script(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"^\d+\.\s*", "", s)
        lines.append(s)
    return lines


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()


def _wav_to_pcm16(path: Path) -> tuple[bytes, int, float]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        n = w.getnframes()
        raw = w.readframes(n)
        sw = w.getsampwidth()
    if sw != 2:
        raise RuntimeError(f"need 16-bit wav, got sampwidth={sw}")
    if ch > 1:
        import array

        a = array.array("h")
        a.frombytes(raw)
        mono = array.array("h")
        for i in range(0, len(a), ch):
            mono.append(int(sum(a[i : i + ch]) / ch))
        raw = mono.tobytes()
    return raw, sr, n / float(sr)


def health_parakeet(url: str = PARAKEET_URL) -> dict:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def post_parakeet(pcm: bytes, sr: int = SR, url: str = PARAKEET_URL, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/stt",
        data=pcm,
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "X-Sample-Rate": str(sr),
            "Content-Length": str(len(pcm)),
        },
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode())
        body["_ok"] = True
        body["_wall_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return body
    except Exception as e:
        return {
            "_ok": False,
            "error": f"{type(e).__name__}: {e}"[:240],
            "_wall_ms": round((time.monotonic() - t0) * 1000, 1),
            "text": "",
        }


def _load_ark():
    if not ARK_ROOT.is_dir():
        raise RuntimeError(f"ark-asr root missing: {ARK_ROOT}")
    sys.path.insert(0, str(ARK_ROOT))
    from infer_ark_audio_onnx import ArkAsrOnnxRuntime  # type: ignore

    t0 = time.monotonic()
    rt = ArkAsrOnnxRuntime(ARK_ROOT)
    return rt, round(time.monotonic() - t0, 1)


def run_ark(rt: Any, wav: Path) -> dict:
    t0 = time.monotonic()
    try:
        text = rt.transcribe(
            audio_path=str(wav),
            max_new_tokens=128,
            max_audio_seconds=30,
            precision="int8",
            asr_block_token_id_from=151670,
        )
        wall = round((time.monotonic() - t0) * 1000, 1)
        return {
            "_ok": True,
            "text": (text or "").strip(),
            "decode_ms": wall,
            "_wall_ms": wall,
            "engine": "audio8-ark-asr-0.6b-int8-onnx",
            "provider": "CPU" if os.environ.get("ARK_ASR_CPU", "1") != "0" else "CUDA?",
        }
    except Exception as e:
        return {
            "_ok": False,
            "text": "",
            "error": f"{type(e).__name__}: {e}"[:240],
            "_wall_ms": round((time.monotonic() - t0) * 1000, 1),
            "decode_ms": None,
            "engine": "audio8-ark-asr-0.6b-int8-onnx",
        }


def panel(
    utt_id: str,
    ref: str,
    pk: dict,
    ark: dict,
    dur_s: float,
    peak: int,
) -> str:
    pk_text = (pk.get("text") or "").strip()
    ark_text = (ark.get("text") or "").strip()
    pk_ms = pk.get("decode_ms") or pk.get("_wall_ms")
    ark_ms = ark.get("decode_ms") or ark.get("_wall_ms")
    lines = [
        "",
        "=" * 78,
        f"  UTT {utt_id}   dur={dur_s:.2f}s   peak={peak}   {_ts_local()}",
        f"  REF: {ref[:72]}" if ref else "  REF: (free speech)",
    ]
    if peak < 500:
        lines.append(
            "  ⚠ SILENT/LOW peak — spoke too early, ear open, or wrong mic"
        )
    lines.extend(
        [
            "-" * 78,
            f"  PARAKEET-ONNX  wall/decode={pk.get('_wall_ms')}ms / {pk_ms}ms  ok={pk.get('_ok')}",
            f"    → {pk_text or '(empty)'}",
            f"  AUDIO8-ARK     wall/decode={ark.get('_wall_ms')}ms / {ark_ms}ms  ok={ark.get('_ok')}",
            f"    → {ark_text or '(empty)'}",
            "=" * 78,
            "",
        ]
    )
    return "\n".join(lines)


def run_asr_ab(
    *,
    site: Site | None = None,
    mode: str = "script",
    script_path: Path | None = None,
    seconds: float = 8.0,
    out: Path | None = None,
    max_takes: int = 0,
    keep_ear_closed: bool = False,
) -> int:
    site = site or load_site()
    script_path = script_path or DEFAULT_SCRIPT
    out = out or (RESULTS_PARENT / f"asr-ab-live-{_stamp()}")
    out.mkdir(parents=True, exist_ok=True)
    log_pk = out / "log_parakeet_onnx.jsonl"
    log_ark = out / "log_audio8_ark_asr.jsonl"
    log_panel = out / "live_panel.log"
    meta_path = out / "META.json"

    print("=== ASR A/B LIVE (house-enroll capture path) ===", flush=True)
    print(f"  A  parakeet-onnx  {PARAKEET_URL}", flush=True)
    print(
        f"  B  audio8 ark-asr {ARK_ROOT}  (ARK_ASR_CPU={os.environ.get('ARK_ASR_CPU')})",
        flush=True,
    )
    print(f"  mic={site.mic}  sink={site.sink}  window={seconds:.0f}s  mode={mode}", flush=True)
    print(f"  out={out}", flush=True)
    print(
        "  cues: LONG mid beep = GO (speak after tone ends) · SHORT high = STOP",
        flush=True,
    )
    print(
        "  lag note: after STOP, both engines decode (ark CPU can be 1–5s) — wait for panel",
        flush=True,
    )
    print(flush=True)

    h = health_parakeet()
    print(f"parakeet health: {json.dumps(h)[:200]}", flush=True)
    if not h.get("ok"):
        print("FATAL: parakeet-onnx not healthy on :8094", file=sys.stderr)
        return 2

    print("loading ark-asr (CPU) …", flush=True)
    try:
        ark_rt, load_s = _load_ark()
    except Exception as e:
        print(f"FATAL: ark load failed: {e}", file=sys.stderr)
        print(
            "  Use <the voice env's python> (torch+transformers+onnxruntime).",
            file=sys.stderr,
        )
        return 3
    print(f"ark loaded in {load_s}s", flush=True)

    # Own the mic for the whole session (same as enroll)
    release_ear_for_enroll()
    if keep_ear_closed:
        os.environ["HOUSE_ENROLL_KEEP_EAR_CLOSED"] = "1"

    script_lines = _load_script(script_path) if mode == "script" else []
    if mode == "script" and not script_lines:
        print(f"FATAL: empty script {script_path}", file=sys.stderr)
        return 4

    meta = {
        "started": _ts_local(),
        "parakeet_url": PARAKEET_URL,
        "parakeet_health": h,
        "ark_root": str(ARK_ROOT),
        "ark_cpu": os.environ.get("ARK_ASR_CPU"),
        "mic": site.mic,
        "seconds": seconds,
        "mode": mode,
        "script": str(script_path) if mode == "script" else None,
        "out": str(out),
        "capture": "house_enroll.record_fixed + go_beep/done_chirp + ear release",
        "note": (
            "Prior live session often silent/short: concurrent ear + unclear GO. "
            "This path matches house-init enroll."
        ),
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    # warm engines (fixture if present — not mic)
    warm = Path.home() / "projects/rail-stt/tests/fixtures/speech.wav"
    if warm.is_file():
        print("warm-up both engines (fixture) …", flush=True)
        pcm_w, sr_w, _ = _wav_to_pcm16(warm)
        pk_w = post_parakeet(pcm_w, sr_w)
        ark_w = run_ark(ark_rt, warm)
        print(
            f"  warm pk={pk_w.get('_wall_ms')}ms {((pk_w.get('text') or '')[:40])!r}",
            flush=True,
        )
        print(
            f"  warm ark={ark_w.get('_wall_ms')}ms {((ark_w.get('text') or '')[:40])!r}",
            flush=True,
        )

    take = 0
    rc = 0
    try:
        while True:
            if max_takes and take >= max_takes:
                break
            take += 1
            if mode == "script":
                if take > len(script_lines):
                    print("\nScript complete.", flush=True)
                    break
                ref = script_lines[take - 1]
                print(f"\n>>> LINE {take}/{len(script_lines)}", flush=True)
                print(f"    {ref}", flush=True)
                print(
                    "    Read it silently, then press ENTER when ready.\n"
                    "    DO NOT speak until the GO beep ends.\n"
                    "    (q + ENTER = quit)",
                    flush=True,
                )
                ans = sys.stdin.readline()
                if not ans or ans.strip().lower() in ("q", "quit", "exit"):
                    print("quit.", flush=True)
                    break
            else:
                ref = ""
                print(
                    f"\n>>> FREE take {take} — ENTER when ready (GO beep then speak {seconds:.0f}s)",
                    flush=True,
                )
                ans = sys.stdin.readline()
                if not ans or ans.strip().lower() in ("q", "quit", "exit"):
                    print("quit.", flush=True)
                    break

            utt_id = f"utt_{take:03d}"
            wav_path = out / "wav" / f"{utt_id}.wav"

            print(
                "\n"
                "    ┌────────────────────────────────────────────────┐\n"
                "    │  GO BEEP next — speak AFTER the tone ends      │\n"
                "    └────────────────────────────────────────────────┘",
                flush=True,
            )
            cues.go_beep(site)
            print(
                f"    ★ GO — SPEAK NOW  ({seconds:.0f}s window, mic={site.mic})",
                flush=True,
            )
            t_rec = time.monotonic()
            try:
                record_fixed(wav_path, site, seconds)
            except Exception as e:
                print(f"    record failed: {e}", flush=True)
                rc = 5
                continue
            cues.done_chirp(site)
            rec_wall = time.monotonic() - t_rec
            peak = peak_pcm(wav_path)
            print(
                f"    ■ STOP  rec_wall={rec_wall:.1f}s  peak={peak}"
                + ("  ⚠ LOW — likely silence" if peak < 500 else ""),
                flush=True,
            )
            if peak < 500:
                print(
                    "    (still decoding; re-take this line if empty hyps)",
                    flush=True,
                )

            print(
                "    … decoding both engines (ark CPU may lag 1–5s — do not speak) …",
                flush=True,
            )
            pcm, sr, dur = _wav_to_pcm16(wav_path)
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_pk = ex.submit(post_parakeet, pcm, sr)
                f_ark = ex.submit(run_ark, ark_rt, wav_path)
                pk = f_pk.result()
                ark = f_ark.result()

            rec_pk = {
                "ts": time.time(),
                "ts_local": _ts_local(),
                "id": utt_id,
                "engine": "parakeet-onnx-int8",
                "url": PARAKEET_URL,
                "wav": str(wav_path),
                "ref": ref,
                "hyp": (pk.get("text") or "").strip(),
                "decode_ms": pk.get("decode_ms"),
                "wall_ms": pk.get("_wall_ms"),
                "duration_s": round(dur, 3),
                "peak": peak,
                "score": pk.get("score"),
                "ok": bool(pk.get("_ok")),
                "error": pk.get("error"),
            }
            rec_ark = {
                "ts": time.time(),
                "ts_local": _ts_local(),
                "id": utt_id,
                "engine": "audio8-ark-asr-0.6b-int8-onnx",
                "runtime": str(ARK_ROOT),
                "wav": str(wav_path),
                "ref": ref,
                "hyp": (ark.get("text") or "").strip(),
                "decode_ms": ark.get("decode_ms"),
                "wall_ms": ark.get("_wall_ms"),
                "duration_s": round(dur, 3),
                "peak": peak,
                "provider": "CPU",
                "ok": bool(ark.get("_ok")),
                "error": ark.get("error"),
            }
            _append_jsonl(log_pk, rec_pk)
            _append_jsonl(log_ark, rec_ark)
            text = panel(utt_id, ref, pk, ark, dur, peak)
            print(text, flush=True)
            with log_panel.open("a", encoding="utf-8") as f:
                f.write(text + "\n")
                f.flush()

            print(
                "    ready for next line when you are (no recording until ENTER + GO).",
                flush=True,
            )
    except KeyboardInterrupt:
        print("\n^C stop.", flush=True)
    finally:
        reopen_ear_after_enroll()
        try:
            cues.session_end(site)
        except Exception:
            pass

    print(f"\nDone. Results in {out}", flush=True)
    print(f"  {log_pk}", flush=True)
    print(f"  {log_ark}", flush=True)
    print(f"  dual-tail: {rails_path('scripts', 'asr-ab-dual-tail.sh')} {out}", flush=True)
    return rc
