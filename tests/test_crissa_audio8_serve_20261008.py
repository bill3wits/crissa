"""The pack's pipelined Audio8 server (2026-10-08): every sample once and in order, a cancel ends the AR within a
chunk, the launcher runs it by default, upstream's app on a switch."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "voice"))
os.environ["CRISSA_AUDIO8_SERVE_LAZY"] = "1"
import numpy as np  # noqa: E402

from crissa_voice import audio8_serve as P  # noqa: E402
from crissa_voice import audio8_setup as S  # noqa: E402


class FakeRuntime:
    """Frames are integers 0..n-1; decoding a window of frames gives hop samples per frame, each sample = frame index.
    A frame takes `ar_s` to make, so the codec thread sees them arrive while it works."""
    manifest = {"codec_hop_length": 4, "sample_rate": 100}

    def __init__(self, n=40, ar_s=0.002, hop=4):
        self.n, self.ar_s = n, ar_s
        self.manifest = dict(self.manifest, codec_hop_length=hop)
        self.decodes = []

    def iter_codes(self, stop_event=None, **kw):
        for i in range(self.n):
            if stop_event is not None and stop_event.is_set():
                return
            time.sleep(self.ar_s)
            yield np.array([i])

    def decode_codes(self, codes):
        codes = np.asarray(codes)
        frames = codes.reshape(-1)
        self.decodes.append(len(frames))
        return np.repeat(frames.astype(np.float32), self.manifest["codec_hop_length"])


def test_every_sample_once_and_in_order_with_a_short_context():
    rt = FakeRuntime(n=40)
    out, seqs = [], []
    evs = list(P.stream_pipelined(rt, chunk_frames=6, context=5, guard_frames=1, text="x", voice="v"))
    for ev in evs:
        if ev["type"] == "audio_chunk":
            out.append(ev["audio"])
            seqs.append(ev["seq"])
    assert evs[-1] == {"type": "complete", "frames": 40}
    audio = np.concatenate(out)
    want = np.repeat(np.arange(40, dtype=np.float32), 4)
    assert audio.shape == want.shape and np.array_equal(audio, want), "a sample was repeated or dropped"
    assert seqs == list(range(len(seqs)))
    assert max(rt.decodes) <= 40 and all(d <= 5 + 40 for d in rt.decodes)


def test_a_cancel_ends_the_ar_within_a_chunk():
    rt = FakeRuntime(n=400, ar_s=0.005)
    stop = threading.Event()
    t0 = time.monotonic()
    seen = 0
    for ev in P.stream_pipelined(rt, chunk_frames=6, context=5, stop_event=stop, text="x", voice="v"):
        seen += 1
        stop.set()
    assert seen == 1 and time.monotonic() - t0 < 1.0
    time.sleep(0.1)
    assert not [t for t in threading.enumerate() if t.name == "audio8-ar"], "the AR thread kept going"


def test_the_launcher_runs_the_pack_server_and_upstream_on_a_switch(monkeypatch):
    monkeypatch.setattr(S, "env_python", lambda: Path("/x/python"))
    assert S.server_argv(8187, {})[2:4] == ["uvicorn", "crissa_voice.audio8_serve:app"]
    assert S.server_argv(8187, {"CRISSA_AUDIO8_UPSTREAM": "1"})[3] == "arktts_runtime.service:app"
    monkeypatch.delenv("CRISSA_AUDIO8_THREADS", raising=False)
    pp = S.server_env({})["PYTHONPATH"].split(os.pathsep)
    assert pp[0].endswith("onnx_runtime") and Path(pp[1]).name == "voice"
    assert P.CODEC_THREADS == 3 and P.CONTEXT == 16
