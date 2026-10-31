"""CLI for rail-stt."""

from __future__ import annotations

from . import settings as _S  # this install's addresses (the shared port table, 2026-10-10)

import argparse
import json
import sys
from pathlib import Path

from . import audio as audio_mod
from .config import Config
from .pipeline import Transcriber
from .vad import detect_speech_spans, extract_spans


def _cfg_from_args(args: argparse.Namespace) -> Config:
    cfg = Config()
    if getattr(args, "engine", None):
        cfg.engine = args.engine
    if getattr(args, "mic", None):
        cfg.mic = args.mic
    if getattr(args, "whisper", None):
        cfg.whisper_model = args.whisper
    return cfg


def cmd_file(args: argparse.Namespace) -> int:
    cfg = _cfg_from_args(args)
    tr = Transcriber(cfg)
    if args.warm:
        print(json.dumps(tr.warm()), flush=True)
    result = tr.transcribe_file(args.path)
    _emit(result, args)
    return 0 if result.text or args.allow_empty else 1


def cmd_listen(args: argparse.Namespace) -> int:
    cfg = _cfg_from_args(args)
    tr = Transcriber(cfg)
    if args.warm or cfg.engine in ("whisper", "dual"):
        print(json.dumps({"warm": tr.warm()}), flush=True)
    if args.seconds and args.seconds > 0:
        wav = audio_mod.record_seconds(args.seconds, mic=cfg.mic, sample_rate=cfg.sample_rate)
    else:
        wav = audio_mod.record_until_enter(mic=cfg.mic, sample_rate=cfg.sample_rate)
    print(f"[rail-stt] captured {len(wav)/cfg.sample_rate:.2f}s  rms={audio_mod.rms_db(wav):.1f} dB", flush=True)
    result = tr.transcribe_array(wav, cfg.sample_rate)
    _emit(result, args)
    return 0 if result.text or args.allow_empty else 1


def cmd_utter(args: argparse.Namespace) -> int:
    """Record a few seconds past end-of-speech (simple endpointing)."""
    cfg = _cfg_from_args(args)
    tr = Transcriber(cfg)
    if cfg.engine in ("whisper", "dual"):
        tr.warm()
    # Chunked capture: 0.5s frames, stop after hangover silence following speech
    import time

    source = audio_mod.resolve_mic(cfg.mic)
    print(f"[rail-stt] utter mode on {source} — speak…", flush=True)
    chunks = []
    heard = False
    silence_run = 0.0
    max_s = float(args.max_seconds)
    hang = float(args.hangover)
    t_start = time.monotonic()
    while time.monotonic() - t_start < max_s:
        piece = audio_mod.record_seconds(0.4, mic=cfg.mic, sample_rate=cfg.sample_rate)
        chunks.append(piece)
        spans = detect_speech_spans(piece, cfg.sample_rate, min_speech_s=0.12, pad_s=0.05)
        if spans:
            heard = True
            silence_run = 0.0
        else:
            if heard:
                silence_run += 0.4
                if silence_run >= hang:
                    break
    import numpy as np

    wav = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    print(f"[rail-stt] utter captured {len(wav)/cfg.sample_rate:.2f}s", flush=True)
    result = tr.transcribe_array(wav, cfg.sample_rate)
    _emit(result, args)
    return 0 if result.text or args.allow_empty else 1


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    cfg = _cfg_from_args(args)
    serve(host=args.host, port=args.port, cfg=cfg)
    return 0


def cmd_mic(args: argparse.Namespace) -> int:
    print("sources:")
    for s in audio_mod.list_pulse_sources():
        mark = "  "
        print(f"{mark}{s}")
    print("resolved:", audio_mod.resolve_mic(args.mic or "auto"))
    return 0


def _emit(result, args: argparse.Namespace) -> None:
    if args.json:
        print(json.dumps(result.to_dict(), indent=2 if args.pretty else None))
    else:
        if result.error:
            print(f"[warn] {result.error}", file=sys.stderr)
        if result.text:
            print(result.text)
        else:
            print("", end="")
            print(
                f"[rail-stt] (no text) reason={result.reason} conf={result.confidence:.2f} rms={result.rms_db:.1f}dB",
                file=sys.stderr,
            )
        if args.verbose:
            print(
                f"# engine={result.engine} agreed={result.agreed} conf={result.confidence:.2f} "
                f"reason={result.reason} decode_ms={result.decode_ms:.0f}",
                file=sys.stderr,
            )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rail-stt",
        description="Standalone speech→text (live-ear HTTP ASR + Whisper consensus).",
    )
    p.add_argument(
        "--engine",
        choices=("live-ear", "parakeet", "audio8", "whisper", "dual"),
        default=None,
        help="ASR path (default: live-ear). parakeet/audio8 are aliases of live-ear.",
    )
    p.add_argument("--mic", default=None, help="Pulse source or 'auto'")
    p.add_argument("--whisper", default=None, help="faster-whisper model (default large-v3)")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    p.add_argument("--pretty", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--allow-empty", action="store_true", help="Exit 0 even if no text")
    p.add_argument("--warm", action="store_true", help="Preload models first")

    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("file", help="Transcribe an audio file")
    f.add_argument("path")
    f.set_defaults(func=cmd_file)

    l = sub.add_parser("listen", help="Record from mic then transcribe")
    l.add_argument("--seconds", type=float, default=0, help="Fixed length; 0 = until Enter")
    l.set_defaults(func=cmd_listen)

    u = sub.add_parser("utter", help="Record one utterance (endpoint on silence)")
    u.add_argument("--max-seconds", type=float, default=30.0)
    u.add_argument("--hangover", type=float, default=0.9, help="Silence after speech to stop")
    u.set_defaults(func=cmd_utter)

    s = sub.add_parser("serve", help="HTTP API on loopback")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=_S.port("stt"))
    s.set_defaults(func=cmd_serve)

    m = sub.add_parser("mic", help="List / resolve mic sources")
    m.set_defaults(func=cmd_mic)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
