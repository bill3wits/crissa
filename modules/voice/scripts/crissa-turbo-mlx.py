#!/usr/bin/env python3
"""Chatterbox-Turbo on Apple Silicon via MLX — Init wiring only (NOT YET VERIFIED ON APPLE SILICON HARDWARE).

    python3 crissa-turbo-mlx.py --install   refuse with the reason (nothing downloads)
    python3 crissa-turbo-mlx.py --why       print whether this Mac meets the tier's needs
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from crissa_voice import turbo_mlx_setup as S  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--why", action="store_true", help="print unmet needs for this Mac (JSON)")
    ap.add_argument("--quant", choices=list(S.QUANTS), default=S.DEFAULT_QUANT)
    a = ap.parse_args(argv)
    if a.why:
        why = S.unsupported()
        print(json.dumps({"ok": not why, "why": why or "", "min_ram_gb": S.MIN_RAM_GB,
                          "models": S.MODELS, "verified_on_hardware": False, "credit": S.CREDIT}))
        return 0 if not why else 1
    if a.install:
        try:
            S.install(quant=a.quant)
        except RuntimeError as e:
            print("crissa-turbo-mlx: %s" % e, file=sys.stderr)
            return 2
        return 0
    print("crissa-turbo-mlx: NOT YET VERIFIED ON APPLE SILICON HARDWARE; use --why or --install", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
