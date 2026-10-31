"""Audio8's ONNX thread count is sized to the machine (the User 2026-10-08), measured: 5 is the top on a 16-core box."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    import sys
    sys.path.insert(0, str(ROOT / "modules" / "voice"))
    import crissa_voice.audio8_setup as m
    return m


def test_threads_follow_the_cores_up_to_the_measured_top(monkeypatch):
    m = _mod()
    assert [m.audio8_threads(c) for c in (1, 2, 4, 6, 8, 16, 32)] == [1, 1, 3, 5, 5, 5, 5]
    monkeypatch.delenv("CRISSA_AUDIO8_THREADS", raising=False)
    monkeypatch.setattr(m.os, "cpu_count", lambda: 32)
    assert m.server_env({})["ARKTTS_THREADS"] == "5"
    assert m.server_env({"CRISSA_AUDIO8_THREADS": "8"})["ARKTTS_THREADS"] == "8"


def test_the_tiers_file_carries_the_stream_measurement():
    import json
    t = json.loads((ROOT / "config/crissa-voice-tiers.json").read_text())["tiers"]["audio8"]["measured"]
    s = t["stream_20261008"]
    assert s["ar_rtf_by_threads"]["5"] > 1.0 > s["buffered_rtf_5_threads"] > s["stream_rate_by_threads"]["5"]
    assert s["pipelined_pack_server"]["in_process_rate_ar5_codec3"] >= 1.0
