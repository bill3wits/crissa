"""Round 46 (the local review): the speech lane held its lock through the microphone's 0.4 s GET, so a stop or a new
line waited behind it. The ask now runs before the lock and hold() reads its answer. Kill CRISSA_SPEECHQ_MIC_UNLOCKED=0."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))
import crissa_speechq as sq  # noqa: E402
from test_crissa_join import _lane  # noqa: E402


def _held(lane):
    """Is the lane's RLock held by anyone right now (tried from another thread)."""
    import threading
    got = {}

    def probe():
        ok = lane.lock.acquire(blocking=False)
        got["free"] = ok
        if ok:
            lane.lock.release()
    t = threading.Thread(target=probe)
    t.start()
    t.join()
    return not got["free"]


def test_the_microphone_is_asked_outside_the_lock(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_SPEECHQ_MIC_UNLOCKED", raising=False)
    lane, sp, _now, _s = _lane(tmp_path)
    seen = []
    lane.mic_busy = lambda pause: seen.append(_held(lane)) or "a voice"
    lane.announce("Done. Card one.", "completion")
    assert lane.step() == "hold: someone is talking (a voice)"
    assert seen == [False]
    lane.mic_busy = lambda pause: None
    assert lane.step() == "announce"


def test_the_kill_asks_under_the_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_SPEECHQ_MIC_UNLOCKED", "0")
    lane, sp, _now, _s = _lane(tmp_path)
    seen = []
    lane.mic_busy = lambda pause: seen.append(_held(lane)) or None
    lane.announce("Done. Card one.", "completion")
    assert lane.step() == "announce" and seen == [True]
