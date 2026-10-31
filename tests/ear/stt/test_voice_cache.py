import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crissa_ear import voice_cache as vc  # noqa: E402


def setup_function() -> None:
    vc.clear()


def test_offer_ignored_until_arm():
    wav = np.zeros(16000, dtype=np.int16)
    vc.offer(wav, 16000)
    st = vc.status()
    assert st["seconds"] == 0
    assert st["open"] is False


def test_arm_then_offer_then_release(tmp_path, monkeypatch):
    log = tmp_path / "exchanges.jsonl"
    monkeypatch.setattr(vc, "_exchanges_path", lambda: log)
    wav = (np.ones(8000, dtype=np.int16) * 100)
    vc.arm()
    vc.offer(wav, 16000)
    st = vc.status()
    assert st["open"] is True
    assert st["seconds"] > 0
    out = vc.release({"heard": "hello", "reply": "hi", "who": "user"})
    assert out["ok"] is True
    assert out["cleared_sec"] > 0
    idle = vc.status()
    assert idle["open"] is False
    assert idle["seconds"] == 0
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "hello" in lines[0]


def test_release_empty_is_ok():
    out = vc.release({"heard": ""})
    assert out["ok"] is True
    assert out["cleared_sec"] == 0
