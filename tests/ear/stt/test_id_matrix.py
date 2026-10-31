"""isUser gate + paint guard (plan rev4 / D1 D2 D8)."""
from __future__ import annotations

import numpy as np
import pytest

from crissa_ear.id_matrix import (
    apply_is_user_paint_guard,
    build_columns,
    desk_silence_allows_user,
    is_user_gate,
    score_spoke,
)
from crissa_ear.stamp import stamp_turn


def test_is_user_true_requires_045_floor():
    """P1: impostor-class 0.40 must not be is_user true."""
    r = is_user_gate(
        user_sim=0.40,
        best_other_id="second",
        best_other_sim=0.10,
        voice_usable=True,
    )
    assert r.is_user is not True
    assert r.reason in ("uncertain", "voice_other_leads")


def test_is_user_true_at_045_with_margin():
    r = is_user_gate(
        user_sim=0.50,
        best_other_id="second",
        best_other_sim=0.20,
        voice_usable=True,
    )
    assert r.is_user is True
    assert r.reason == "voice_margin"


def test_is_user_false_when_other_leads():
    r = is_user_gate(
        user_sim=0.15,
        best_other_id="second",
        best_other_sim=0.35,
        voice_usable=True,
    )
    assert r.is_user is False


def test_is_user_false_on_media():
    r = is_user_gate(
        user_sim=0.60,
        best_other_id=None,
        best_other_sim=-1,
        media_on=True,
        voice_usable=True,
    )
    assert r.is_user is False
    assert r.reason == "media"


def test_d8_guard_never_invents_user():
    """D8: guard may strip the user, never create the user from second."""
    pid, src, note, sup = apply_is_user_paint_guard(
        "second",
        "voice_emb",
        is_user=True,
        voice_usable=True,
    )
    assert pid == "second"
    assert sup is False


def test_d8_guard_strips_user_when_false():
    pid, src, note, sup = apply_is_user_paint_guard(
        "user",
        "sole_present",
        is_user=False,
        voice_usable=True,
    )
    assert pid is None
    assert sup is True
    assert src == "is_user_block"


def test_d8_uncertain_sole_with_voice_strips_user():
    pid, src, note, sup = apply_is_user_paint_guard(
        "user",
        "sole_present",
        is_user=None,
        voice_usable=True,
    )
    assert pid is None
    assert sup is True


def test_desk_silence_tight_definition():
    assert desk_silence_allows_user(
        voice_usable=False,
        sole_name="user",
        sole_certain=True,
        sole_stale=False,
        face_auth_age_s=30.0,
        seat_beam=8,
        open_u=False,
    )
    # too old face
    assert not desk_silence_allows_user(
        voice_usable=False,
        sole_name="user",
        sole_certain=True,
        sole_stale=False,
        face_auth_age_s=500.0,
        seat_beam=8,
        open_u=False,
    )
    # open U
    assert not desk_silence_allows_user(
        voice_usable=False,
        sole_name="user",
        sole_certain=True,
        sole_stale=False,
        face_auth_age_s=30.0,
        seat_beam=8,
        open_u=True,
    )


def test_beam_does_not_change_spoke_score():
    """the User 2026-08-30: beam is logged, weight 0 — off-seat must not cut voice."""
    from crissa_ear.id_matrix_config import spoke_weights

    assert spoke_weights()["beam"] == 0.0
    at = score_spoke(
        ["user"],
        voice_sims={"user": 0.6},
        home_beams={"user": 6},
        seat_guess=6,
    )
    away = score_spoke(
        ["user"],
        voice_sims={"user": 0.6},
        home_beams={"user": 6},
        seat_guess=3,
    )
    assert at.scores["user"] == away.scores["user"]
    assert at.votes["user"]["beam"] == 0.7
    assert away.votes["user"]["beam"] == 0.0


def test_matrix_user_ineligible_when_is_user_false():
    mx = score_spoke(
        ["user", "second"],
        voice_sims={"user": 0.2, "second": 0.5},
        is_user=False,
    )
    assert "user" not in mx.eligible or mx.winner != "user"
    assert mx.winner in ("second", None)


def test_is_user_false_when_visitor_leads_in_columns():
    """2026-09-07 02:52:23 Hey Zoe: visitor 0.585 / the user 0.373. Regulars-only
    columns never saw visitor, so is_user was None (best_other=second). With
    visitor in extra, the same pair is voice_other_leads."""
    r = is_user_gate(
        user_sim=0.373,
        best_other_id="visitor",
        best_other_sim=0.585,
        voice_usable=True,
    )
    assert r.is_user is False
    assert r.reason == "voice_other_leads"
    assert r.best_other_id == "visitor"


def test_build_columns_extra_carries_bank_ids():
    cols = build_columns(["user"], ["user", "second"], extra=["visitor", "friend"])
    assert "visitor" in cols and "friend" in cols
    assert cols.index("user") < cols.index("visitor")


def test_stamp_is_user_fields_present():
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"slot_id": "s0", "name": "user", "certain": True}],
    }
    st = stamp_turn("hello", presence)
    assert "is_user" in st
    assert "is_user_guard_suppressed" in st
