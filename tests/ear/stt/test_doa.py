"""XVF3800 DOA parse + optional live read."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crissa_ear.doa import find_xvf3800, parse_doa_payload, read_doa, stamp_beam


def test_parse_doa_payload():
    # status=0, az=39, vad=1  (measured 2026-08-13 usbfs)
    raw = bytes([0, 39, 0, 1, 0])
    d = parse_doa_payload(raw)
    assert d["az_deg"] == 39
    assert d["speech"] is True
    assert d["status"] == 0


def test_parse_doa_wraps_360():
    raw = bytes([0, 0x68, 0x01, 0, 0])  # 360
    d = parse_doa_payload(raw)
    assert d["az_deg"] == 0


def test_stamp_beam_none_when_not_speaking():
    az, conf, speech = stamp_beam(
        {"ok": True, "az_deg": 359, "conf": 0.25, "speech": False}
    )
    assert az is None and conf is None and speech is False


def test_stamp_beam_none_when_held():
    az, conf, speech = stamp_beam(
        {"ok": True, "az_deg": 41, "conf": 0.6, "speech": True, "held": True}
    )
    assert az is None and speech is False


def test_stamp_beam_live_speech():
    az, conf, speech = stamp_beam(
        {"ok": True, "az_deg": 233, "conf": 1.0, "speech": True}
    )
    assert speech is True
    assert az == 233
    assert conf == 1.0


def test_live_doa_optional():
    dev = find_xvf3800()
    if not dev:
        pytest.skip("no XVF3800")
    d = read_doa()
    assert d["ok"] is True
    assert d["az_deg"] is not None
    assert 0 <= d["az_deg"] <= 359
    assert d["source"] == "xvf3800"
