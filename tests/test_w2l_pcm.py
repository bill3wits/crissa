"""Wav2Lip live PCM path — slice math, ingest ring, XOR, clock.

Claude 2026-08-30: buffered Cosy used to POST the whole clip and hit the
wall's 262144-byte cap. These tests pin the split and the remaining holes.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))

WALL = os.environ.get("VO_MOUTH_URL", "http://127.0.0.1:8099/v1/mouth")
POST_CAP = 262144
PCM_POST_BYTES = 48000  # house-speak MouthAir.PCM_POST_BYTES


def test_post_cap_is_262144_on_the_live_wall():
    """Measured 2026-08-30: n>262144 → 413; n=262144 is accepted then JSON-400."""
    try:
        urllib.request.urlopen(WALL, timeout=1).close()
    except Exception:
        pytest.skip("wall :8099 down")
    over = urllib.request.Request(
        WALL, data=b"x" * (POST_CAP + 1), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(over, timeout=2)
    assert ei.value.code == 413


def test_one_second_slice_fits_the_post_cap():
    raw = bytes(PCM_POST_BYTES)
    blob = base64.b64encode(raw).decode("ascii")
    body = json.dumps({
        "air_at": 1.0, "air_until": None, "sr": 24000, "hop_ms": 20.0,
        "pts0": 1.0, "pcm16": blob,
    }).encode()
    assert len(blob) == 64000
    assert len(body) < POST_CAP


def test_buffered_4_2s_clip_needs_five_slices_not_one():
    """Claude's measured 201,600-byte first hop. One POST is 413; five fit."""
    n = 201_600
    assert n > POST_CAP * 3 // 4, "raw already near the cap before base64"
    one = json.dumps({"pts0": 1.0, "pcm16": base64.b64encode(bytes(n)).decode()}).encode()
    assert len(one) > POST_CAP
    step = PCM_POST_BYTES - (PCM_POST_BYTES % 2)
    chunks = [bytes(n)[off:off + step] for off in range(0, n, step)]
    assert len(chunks) == 5
    for blk in chunks:
        body = json.dumps({
            "air_at": 1.0, "sr": 24000, "hop_ms": 20.0, "pts0": 1.0,
            "pcm16": base64.b64encode(blk).decode(),
        }).encode()
        assert len(body) < POST_CAP, len(body)


def test_pcm_post_bytes_is_an_attribute_not_a_bare_name():
    """The NameError that fail-open swallowed for an hour."""
    src = (ROOT / "scripts" / "house-speak.py").read_text()
    block = src.split("class MouthAir:")[1].split("def close(")[0]
    assert "PCM_POST_BYTES = 48000" in block
    assert "self.PCM_POST_BYTES" in block
    assert "step = PCM_POST_BYTES" not in block.replace("self.PCM_POST_BYTES", "")
    assert "print(\"mouth pcm slice failed:" in block


def test_w2l_xor_strip_and_skips_slides_like_the_strip():
    """XOR: neural first, strip on miss. BOTH mouths skip slides.

    Was a witness for the gap (Grok 2026-08-30): the strip skipped slides and
    the neural path, which runs first, did not — so `wake` / `rest` got a 96
    crop pasted onto a moving body. Flipped to pin the guard now that it is on
    both paths; a rule that only covers whichever mouth happens to be enabled
    is not a rule."""
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    comp = src.split("def _composite(")[1].split("def run(")[0]
    assert "W2L_MODE != \"off\"" in comp
    assert comp.index("W2L_MODE != \"off\"") < comp.index("if not VISEMES:")
    assert 'self.skip = "w2l: %s -> strip"' in comp
    # The guard is on the NEURAL path too, before the session is even made.
    # Split on the flag itself, not on `if W2L_MODE`: the guard is a multi-line
    # condition and pinning its line-wrapping is not the point of this test.
    w2l_block = comp.split("W2L_MODE != \"off\"")[1].split("if not VISEMES:")[0]
    assert "mouth_paints_on(role)" in w2l_block, (
        "the neural mouth must skip slides and flourishes the way the strip does"
    )
    # ...and still on the strip path below it.
    assert "not mouth_paints_on(role)" in comp
    assert "flourish keeps filmed mouth" in comp


def test_default_w2l_is_still_off_in_code():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'os.environ.get("HOUSE_W2L", "off")' in src


def test_live_places_audio_by_pts_not_arrival_order():
    import w2l

    class Dummy:
        ok = True
        err = ""
        provider = "CPUExecutionProvider"
        load_s = 0.0

        def infer(self, vid, mel):
            return np.zeros((vid.shape[0], 3, 96, 96), np.float32)

    real = w2l.Session
    w2l.Session = lambda device="gpu": Dummy()  # type: ignore[misc, assignment]
    try:
        live = w2l.Live(device="cpu")
    finally:
        w2l.Session = real
    sr = 24000
    # two 100 ms blocks, second posted first (late/out-of-order)
    def row(pts, n):
        pcm = np.full(n, 1000, dtype="<i2").tobytes()
        return [pts, base64.b64encode(pcm).decode()]

    live.feed([row(10.1, 2400)], sr)
    live.feed([row(10.0, 2400), row(10.1, 2400)], sr)
    # 200 ms at 16 kHz after resample
    assert live._wav.size == 3200
    assert abs(live._t0 - 10.0) < 1e-6
    live.feed([], sr)
    assert live._wav.size == 0
    assert live._mel is None


def test_new_air_at_clears_pcm_as_well_as_frames():
    """A new turn must not keep the previous line's samples in the W2L window."""
    spec_loader = pytest.importorskip("importlib.util")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "vo_home_srv_pcm", ROOT / "scripts" / "voiceonly-home-server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    blob = base64.b64encode(b"\x00\x01" * 100).decode()
    now = time.time()
    mod.mouth_ingest({
        "air_at": now, "pts0": now, "sr": 24000, "hop_ms": 20.0,
        "rms": [0.1, 0.2], "pcm16": blob,
    })
    snap = mod.mouth_snapshot()
    assert len(snap["pcm"]) == 1
    mod.mouth_ingest({
        "air_at": now + 1.0, "pts0": now + 1.0, "sr": 24000, "hop_ms": 20.0,
        "rms": [0.3], "pcm16": blob,
    })
    snap2 = mod.mouth_snapshot()
    assert snap2["frames"][-1][1] == 0.3
    assert len(snap2["pcm"]) == 1, (
        "pcm ring leaked the previous turn: %s rows" % len(snap2["pcm"])
    )
