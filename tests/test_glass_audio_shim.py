"""Phase 4 (2026-09-22): the glass's sink-input read reaches the shim, behind a
switch that is OFF.

scripts/voiceonly-home-server.py's mouth_sink_live() is how the wall hard-stops
the speak pose. Under CRISSA_AUDIO_SHIM=1 it reads lib/crissa_audio.inputs()
instead of running the tool itself — and must answer exactly what the direct
read answers. A naive swap would not have: the shim's is_player counts pacat
(and, on a box where paplay is its own binary, would drop a paplay started with
--client-name that the glass keeps by its binary), its read
waited 5 s where the glass waits 0.4, and a failed read returned [] where the
glass keeps its last answer. Every parity row below runs BOTH paths on the
same text. The long-lived subscribe is not the shim's and is pinned as is.
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "voiceonly-home-server.py"
sys.path.insert(0, str(ROOT / "lib"))


def _load():
    spec = importlib.util.spec_from_file_location("voiceonly_home_server_shim_t", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(name: str, binary: str, idx: int = 7) -> str:
    return (f"Sink Input #{idx}\n\tDriver: protocol-native.c\n\tSink: 38\n\tMute: no\n"
            "\tVolume: front-left: 65536 / 100% / 0.00 dB\n\tProperties:\n"
            f'\t\tapplication.name = "{name}"\n'
            f'\t\tapplication.process.id = "{4000 + idx}"\n'
            f'\t\tapplication.process.binary = "{binary}"\n')


# (label, pactl output, what the glass has always answered)
ROWS = [
    ("idle", "", False),
    ("plain paplay", _row("paplay", "paplay"), True),
    ("paplay named canary", _row("canary", "paplay"), True),
    # what THIS box reports: /usr/bin/paplay -> pacat, so the binary reads pacat
    # (cold room 2026-09-22) and a named paplay is not hers on either path
    ("paplay here, plain", _row("paplay", "pacat"), True),
    ("paplay here, named canary", _row("canary", "pacat"), False),
    ("pacat", _row("pacat", "pacat"), False),
    ("Echo-Cancel Playback", _row("Echo-Cancel Playback", "pipewire"), False),
    ("AEC then a named paplay", _row("Echo-Cancel Playback", "pipewire", 3)
     + _row("house-speak", "paplay", 9), True),
]


class _Done:
    def __init__(self, out: str):
        self.stdout, self.stderr, self.returncode = out, "", 0


def _read(g, monkeypatch, shim: bool, out: str | None, *, last: bool = False, exc=None):
    """One fresh mouth_sink_live() on `out` (or raising `exc`), shim on or off.
    Both paths exec through subprocess.run, so one fake serves both."""
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["timeout"] = list(argv), kw.get("timeout")
        if exc is not None:
            raise exc
        return _Done(out or "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("CRISSA_AUDIO_SHIM", "1" if shim else "0")
    monkeypatch.setenv("CRISSA_AUDIO_BACKEND", "pulse")
    import crissa_audio
    importlib.reload(crissa_audio)
    g._MOUTH_SINK.update({"ts": 0.0, "live": last, "dirty": True})
    return g.mouth_sink_live(), seen


@pytest.fixture(scope="module")
def glass():
    return _load()


@pytest.mark.parametrize("label,out,want", ROWS, ids=[r[0] for r in ROWS])
def test_the_shim_answers_what_the_glass_always_answered(glass, monkeypatch, label, out, want):
    off, _ = _read(glass, monkeypatch, False, out)
    on, seen = _read(glass, monkeypatch, True, out)
    assert off == want, "%s: the direct read changed" % label
    assert on == off, "%s: shim %s, direct %s" % (label, on, off)
    assert seen["argv"][:3] == ["pactl", "list", "sink-inputs"]


def test_the_shim_read_waits_the_glass_timeout_not_the_shims(glass, monkeypatch):
    _, seen = _read(glass, monkeypatch, True, "")
    assert seen["timeout"] == 0.4


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired(["pactl"], 0.4), OSError("no pactl")])
@pytest.mark.parametrize("last", [True, False])
def test_a_failed_read_keeps_the_last_answer_either_way(glass, monkeypatch, exc, last):
    """Known answer: set live, make the read fail, expect the SAME live back.
    The shim's plain inputs() returns [] here, which would have read as silence."""
    off, _ = _read(glass, monkeypatch, False, None, last=last, exc=exc)
    on, _ = _read(glass, monkeypatch, True, None, last=last, exc=exc)
    assert off is last and on is last


def test_dirty_is_cleared_before_a_failed_read(glass, monkeypatch):
    """A read that times out must not retry on every state build: the next call
    inside the max age answers from the cache without running the tool."""
    _read(glass, monkeypatch, True, None, last=True, exc=subprocess.TimeoutExpired(["pactl"], 0.4))
    assert glass._MOUTH_SINK["dirty"] is False
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("re-read inside the max age"))
    assert glass.mouth_sink_live() is True


@pytest.mark.parametrize("who", ["announcer", "crissa@bench"])
def test_an_announcer_and_a_bench_line_stay_blank_under_the_shim(glass, monkeypatch, who):
    """The mouth_sink FIELD is the read AND the speaker filter; the shim must not
    reach past the read."""
    _read(glass, monkeypatch, True, _row("paplay", "paplay"))
    assert glass.mouth_sink_live() is True
    src = SERVER.read_text()
    line = 'out["mouth_sink"] = mouth_sink_live() and not (who == "announcer" or who.endswith("@bench"))'
    assert src.count(line) == 1, "the speaker filter moved"
    blank = eval(line.split("= ", 1)[1], {"mouth_sink_live": glass.mouth_sink_live, "who": who})
    assert blank is False


def test_the_switch_is_off_by_default(glass, monkeypatch):
    """:8099 is resident, but its unit is Restart=always — a crash loads what is
    on disk, so an ON default would reach the glass without the window."""
    monkeypatch.delenv("CRISSA_AUDIO_SHIM", raising=False)
    assert glass._za() is None
    src = SERVER.read_text()
    assert src.count('os.environ.get("CRISSA_AUDIO_SHIM", "0").strip() != "1"') == 1


def test_the_duck_still_skips_pacat():
    """The glass's predicate is its own; is_player was NOT widened to the binary."""
    import crissa_audio as A
    A = importlib.reload(A)
    rows = A._parse_inputs(_row("pacat", "pacat") + _row("canary", "paplay", 8))
    assert [r["is_player"] for r in rows] == [True, False]
    assert [r["binary"] for r in rows] == ["pacat", "paplay"]


def test_the_subscribe_stays_one_long_lived_child_of_main():
    """Not the shim's: a subscribe per read is the 09-19 leak (31 of 64 slots)."""
    src = SERVER.read_text()
    body = src.split("def _mouth_sink_subscribe", 1)[1].split("\ndef ", 1)[0]
    assert 'argv = ["pactl", "subscribe"]' in body
    assert '"/usr/bin/setpriv", "--pdeathsig", "TERM"' in body
    assert "crissa_audio" not in body and "_za(" not in body
    starts = re.findall(r"target=_mouth_sink_subscribe", src)
    assert len(starts) == 1
    main = src.split("\ndef main(", 1)[1]
    assert "target=_mouth_sink_subscribe" in main
