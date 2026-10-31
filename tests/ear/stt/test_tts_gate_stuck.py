"""A speak run that dies with speaking=true must not keep the beam dark for hours."""
import json, time
from crissa_ear import tts_gate as tg


def _write(tmp_path, monkeypatch, **g):
    p = tmp_path / "gate.json"
    p.write_text(json.dumps(g))
    monkeypatch.setattr(tg, "GATE_PATH", p)


def test_live_gate_is_a_mouth(tmp_path, monkeypatch):
    now = time.time()
    _write(tmp_path, monkeypatch, speaking=True, updated_ts=now - 5, air_until=None, air_firm=True)
    assert tg.mouth_open(now=now) is True


def test_stuck_gate_is_not_a_mouth(tmp_path, monkeypatch):
    now = time.time()
    _write(tmp_path, monkeypatch, speaking=True, updated_ts=now - 3 * 3600, air_until=None, air_firm=True)
    assert tg.mouth_open(now=now) is False


def test_air_until_ahead_keeps_the_mouth_open(tmp_path, monkeypatch):
    now = time.time()
    _write(tmp_path, monkeypatch, speaking=True, updated_ts=now - 3600, air_until=now + 4, air_firm=True)
    assert tg.mouth_open(now=now) is True
