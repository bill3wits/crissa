"""A long line is read in pieces on the house too, when its caller asks (2026-10-23, the demo lap: the User wants a
2 1/4 to 3 1/2 minute brief "without overflow"; the 902-character case brief aired as 704). house-speak splits a house
line only under HOUSE_SPEAK_HOUSE_PIECES=1; the lap sets it for her lines from its `speak_pieces` dial. Every other house
line is one line, cut at the cap, as before."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

TEXT = ("In Gideon versus Wainwright the Court held that counsel is a fundamental right. " * 30).strip()


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def test_the_house_splits_only_when_its_caller_asks(monkeypatch):
    hs = _load("hs_house_pieces", "scripts/house-speak.py")
    for k in ("HOUSE_SPEAK_PIECE", "HOUSE_SPEAK_HOUSE_PIECES", "CRISSA_SPEAK_PIECE_CHARS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(hs, "_HOUSE", True)
    assert hs.line_pieces(TEXT) == [TEXT]                         # the house's default: one line
    monkeypatch.setenv("HOUSE_SPEAK_HOUSE_PIECES", "1")
    ps = hs.line_pieces(TEXT)
    assert len(ps) > 1 and all(len(p) <= hs.VOICE_TEXT_CAP for p in ps) and _norm(" ".join(ps)) == _norm(TEXT)
    assert all(p.endswith(".") for p in ps)                        # each piece ends on a sentence
    assert hs.line_pieces("A short line.") == ["A short line."]
    monkeypatch.setenv("HOUSE_SPEAK_PIECE", "2/4")                 # a piece never splits again
    assert hs.line_pieces(TEXT) == [TEXT]
    monkeypatch.delenv("HOUSE_SPEAK_PIECE")
    monkeypatch.setenv("CRISSA_SPEAK_PIECE_CHARS", "0")            # the talk dial's kill still holds
    assert hs.line_pieces(TEXT) == [TEXT]


def _speak_env(monkeypatch, pieces):
    m = _load("xspaces_live_pieces", "scripts/xspaces-live.py")
    real = m.live.dial
    monkeypatch.setattr(m.live, "dial", lambda name, cfg=None: pieces if name == "speak_pieces" else real(name, cfg))
    seen = {}

    def run(argv, **kw):
        seen["env"] = kw.get("env")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(m.subprocess, "run", run)
    assert m.speak_crissa(TEXT)["ok"]
    return seen["env"]


def test_the_lap_asks_for_pieces_from_its_dial(monkeypatch):
    on = _speak_env(monkeypatch, 1)
    assert on is not None and on.get("HOUSE_SPEAK_HOUSE_PIECES") == "1"
    assert _speak_env(monkeypatch, 0) is None                     # off: house-speak's own environment, as before


def test_the_dial_is_off_in_code_and_on_in_the_demo_config():
    import xspaces_live
    assert xspaces_live.LIMIT_DEFAULTS["speak_pieces"] == 0
    cfg = json.loads((ROOT / "config" / "crissa-demo.json").read_text(encoding="utf-8"))
    assert cfg["limits"]["speak_pieces"] == 1 and "_speak_pieces" in cfg["limits"]
