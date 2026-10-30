"""Veto before decode (2026-09-19): bench only, duration floor, near misses decode."""
from __future__ import annotations

import importlib
import json
import os

import pytest

from crissa_ear import veto_first as vf

NOW = 1_789_811_100.0
EMB = [0.1] * 256


def _gate(who="crissa@bench", speaking=True, updated=NOW - 2.0, air_until=None):
    return {"speaking": speaking, "who": who, "updated_ts": updated, "air_until": air_until}


def _decide(gate, dur=9.0, owner_sim=0.10, veto=True, emb=EMB):
    return vf.decide(
        gate=gate,
        dur=dur,
        emb=emb,
        now=NOW,
        user_sim_fn=lambda e: owner_sim,
        veto_fn=lambda v, e: {"veto": True, "tts_fp_sim": 0.78} if veto else None,
    )


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv("RAIL_STT_VETO_BEFORE_DECODE", raising=False)


def test_bench_line_heard_back_skips_the_decode():
    """2026-09-20: on scope alone. It no longer consults the fingerprint at
    all for a bench voice, so `why` is bench_scope and there is no fp sim."""
    d = _decide(_gate())
    assert d["skip"] is True and d["why"] == "bench_scope"
    assert "tts_fp_sim" not in d


def test_a_bench_line_skips_even_when_the_fingerprint_misses():
    """THE FIX. 610 of 876 bench utterances were decoded because the
    fingerprint disagreed with a gate that already named the speaker —
    3386 s of the house's own voice at up to 16 cores a decode."""
    d = _decide(_gate(), veto=False)
    assert d["skip"] is True and d["why"] == "bench_scope"


def test_the_user_talking_over_the_bench_still_decodes():
    """The User guard is what protects a barge-in, and it is untouched."""
    d = _decide(_gate(), veto=False, owner_sim=vf.NEAR_USER + 0.1)
    assert d["skip"] is False and d["why"] == "near"


def test_a_short_bench_line_still_decodes():
    """The duration floor stays: short clips embed badly."""
    d = _decide(_gate(), dur=vf.MIN_SEC - 0.5, veto=False)
    assert d["skip"] is False and d["why"] == "short"


def test_bench_fingerprint_can_be_put_back(monkeypatch):
    monkeypatch.setenv("RAIL_STT_VETO_BENCH_NEEDS_FP", "1")
    assert vf.bench_needs_fp() is True
    assert _decide(_gate(), veto=False)["why"] == "no_veto"      # as before the fix
    d = _decide(_gate(), veto=True)
    assert d["skip"] is True and d["why"] == "veto" and d["tts_fp_sim"] == 0.78


def test_the_wall_is_still_out_of_scope_after_the_fix():
    """Widening to the wall is a separate call (barge-ins unrecorded)."""
    assert _decide(_gate(who="crissa"), veto=False)["why"] == "not_on_air"


def test_wall_line_is_out_of_scope():
    d = _decide(_gate(who="crissa"))
    assert d["skip"] is False and d["why"] == "not_on_air"


def test_off_air_decodes():
    d = _decide(_gate(speaking=False, updated=NOW - 60))
    assert d["skip"] is False and d["why"] == "not_on_air"


def test_gate_dropped_inside_the_utterance_is_on_air():
    assert vf.in_scope(_gate(speaking=False, updated=NOW - 5.0), 9.0, NOW)


def test_stuck_gate_is_not_a_mouth():
    assert not vf.in_scope(_gate(updated=NOW - 10_000), 9.0, NOW)


def test_short_clip_decodes_as_today():
    d = _decide(_gate(), dur=vf.MIN_SEC - 0.1)
    assert d["skip"] is False and d["why"] == "short"


def test_user_near_the_band_decodes_and_is_logged_near():
    d = _decide(_gate(), owner_sim=vf.NEAR_USER)
    assert d["skip"] is False and d["why"] == "near" and d["user_sim"] == vf.NEAR_USER


def test_no_veto_decodes():
    """Superseded for the BENCH by test_a_bench_line_skips_even_when_the_
    fingerprint_misses (2026-09-20). A non-bench, non-announcer voice that
    somehow reaches decide() still needs the fingerprint."""
    monkey = os.environ.get("RAIL_STT_VETO_SCOPE")
    os.environ["RAIL_STT_VETO_SCOPE"] = "all"
    try:
        importlib.reload(vf)
        d = vf.decide(gate=_gate(who="crissa"), dur=9.0, emb=EMB, now=NOW,
                      user_sim_fn=lambda e: 0.10, veto_fn=lambda v, e: None)
        assert d["skip"] is False and d["why"] == "no_veto"
    finally:
        if monkey is None:
            os.environ.pop("RAIL_STT_VETO_SCOPE", None)
        else:
            os.environ["RAIL_STT_VETO_SCOPE"] = monkey
        importlib.reload(vf)


def test_no_embedding_decodes():
    d = _decide(_gate(), emb=None)
    assert d["skip"] is False and d["why"] == "no_embed"


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("RAIL_STT_VETO_BEFORE_DECODE", "0")
    d = _decide(_gate())
    assert d["skip"] is False and d["why"] == "off"


def test_record_writes_in_scope_rows_only(tmp_path, monkeypatch):
    p = tmp_path / "veto-first.jsonl"
    monkeypatch.setattr(vf, "log_path", lambda: p)
    vf.record({"skip": False, "why": "not_on_air"})
    vf.record({"skip": True, "why": "veto", "user_sim": 0.1})
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    assert [r["why"] for r in rows] == ["veto"]


def test_announcer_on_the_wall_is_dumped_without_a_fingerprint():
    d = _decide(_gate(who="announcer"), veto=False)
    assert d["skip"] is True and d["why"] == "announcer"


def test_announcer_with_the_user_near_decodes():
    d = _decide(_gate(who="announcer"), owner_sim=vf.NEAR_USER + 0.1)
    assert d["skip"] is False and d["why"] == "near"


def test_announcer_dump_kill(monkeypatch):
    monkeypatch.setenv("RAIL_STT_ANNOUNCER_DUMP", "0")
    d = _decide(_gate(who="announcer"))
    assert d["skip"] is False and d["why"] == "not_on_air"


def test_air_until_is_the_clock_not_updated_ts():
    """06:39 09-19 live: gate written before air, 9 s line, turn flushed 12.5 s later."""
    g = {"speaking": False, "who": "crissa@bench", "updated_ts": NOW - 12.5,
         "air_at": NOW - 12.1, "air_until": NOW - 1.5}
    assert vf.in_scope(g, 9.0, NOW)


def test_air_long_over_is_not_on_air():
    g = {"speaking": False, "who": "crissa@bench", "updated_ts": NOW - 60,
         "air_at": NOW - 59, "air_until": NOW - 30}
    assert not vf.in_scope(g, 9.0, NOW)
