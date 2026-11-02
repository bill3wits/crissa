"""Round 38 (.101 round 37: Lila's films -44..-46 LUFS at the speakers against Crissa's -27; WirePlumber had kept her
browser's stream at 0.125). While a Setup run is on, her own browser's streams are set to 100 %."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import audio_sink as A  # noqa: E402
import crissa_init as zi  # noqa: E402


class _Run:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = json.dumps(self.rows) if cmd[:4] == ["pactl", "-f", "json", "list"] else ""
        return type("R", (), {"stdout": out, "returncode": 0})()


def test_only_her_streams_under_full_are_raised(monkeypatch):
    rows = [{"index": 7, "properties": {"application.process.id": "500"},
             "volume": {"front-left": {"value_percent": "12%"}, "front-right": {"value_percent": "12%"}}},
            {"index": 8, "properties": {"application.process.id": "900"},           # his Brave: never touched
             "volume": {"front-left": {"value_percent": "12%"}}},
            {"index": 9, "properties": {"application.process.id": "501"},
             "volume": {"mono": {"value_percent": "100%"}}}]                       # already full: left alone
    run = _Run(rows)
    monkeypatch.setattr(A.subprocess, "run", run)
    monkeypatch.setattr(A, "_ancestors", lambda pid: {pid, 500} if pid in (500, 501) else {pid})
    assert A.full_volume_of({500}) == [(7, 0.12)]
    assert ["pactl", "set-sink-input-volume", "7", "100%"] in run.calls
    assert not any(c[:3] == ["pactl", "set-sink-input-volume", "8"] for c in run.calls)


def test_setup_raises_her_window_and_the_switch(monkeypatch):
    got = []
    monkeypatch.setattr(zi.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setitem(zi._STREAM_FULL, "at", 0.0)
    import crissa_window
    monkeypatch.setattr(crissa_window, "her_procs", lambda *a, **k: [{"pid": 500}])
    monkeypatch.setattr(A, "full_volume_of", lambda pids: got.append(pids) or [(7, 0.125)])
    monkeypatch.delenv("CRISSA_LILA_STREAM_FULL", raising=False)
    assert zi._her_stream_full({"status": "running"}) == [(7, 0.125)] and got == [{500}]
    assert zi._her_stream_full({"status": "running"}) == [], "at most every 2 s"
    monkeypatch.setitem(zi._STREAM_FULL, "at", 0.0)
    assert zi._her_stream_full({"status": "done"}) == [], "only while Setup runs"
    monkeypatch.setenv("CRISSA_LILA_STREAM_FULL", "0")
    assert zi._her_stream_full({"status": "running"}) == []
