"""Minimal person_id stamp — no camera, no STT engines."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crissa_ear.stamp import (
    _presence_user_id,
    canonical_name,
    load_name_aliases,
    stamp_turn,
    vocative_hits,
)


def test_vocative_second():
    hits = vocative_hits("hey second can you hear me", ["user", "second"])
    assert hits == ["second"]


def _identity_root(tmp_path, monkeypatch):
    """A scratch identity tree: two people, the second with an alias in its person.json (the house kept
    its own household's alias in code; a pack reads aliases from the records alone)."""
    import json
    import crissa_ear.stamp as _stamp
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    for pid, rec in (("user", {"id": "user", "display": "User", "active": True}),
                     ("second", {"id": "second", "display": "Second", "active": True, "aliases": ["shell"]})):
        (tmp_path / "people" / pid / "voice").mkdir(parents=True)
        (tmp_path / "people" / pid / "person.json").write_text(json.dumps(rec))
    # the site's regulars (the house kept its household in code; a pack reads site.json stations.regulars)
    (tmp_path / "site.json").write_text(json.dumps({"stations": {"regulars": ["user", "second"]}}))
    monkeypatch.setattr(_stamp, "_REGULARS_CACHE", None)
    load_name_aliases(force=True)
    return tmp_path


def test_vocative_shell_alias(tmp_path, monkeypatch):
    """An alias from a person.json → the person: "Shell" → second."""
    _identity_root(tmp_path, monkeypatch)
    assert canonical_name("shell") == "second"
    hits = vocative_hits("hey Shell are you there", ["user", "second"])
    assert hits == ["second"]


def test_vocative_will_modal_verb_ignored():
    """Bare 'will' is the modal verb — media dialogue, not an address."""
    assert vocative_hits("will you turn that up", ["user", "will"]) == []
    assert vocative_hits("I will be back tomorrow", ["user", "will"]) == []


def test_vocative_will_addressed_hits():
    """Address cue or self-ID still reaches Will."""
    assert vocative_hits("hey will can you hear me", ["user", "will"]) == ["will"]
    assert vocative_hits("this is will at the door", ["user", "will"]) == ["will"]


def test_beam_fields_pass_through():
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"slot_id": "s0", "name": "user", "certain": True}],
    }
    st = stamp_turn(
        "hello",
        presence,
        beam_angle=210.4,
        beam_conf=0.9,
    )
    assert st["beam_angle"] == 210.4
    assert st["beam_conf"] == 0.9
    assert st["beam_hour"] is None  # residual unset; look hour is separate
    from crissa_ear.stamp import station_hour

    assert station_hour(180.0) == 6
    assert station_hour(181.0) == 6
    assert station_hour(0.0) == 12
    assert station_hour(None) is None
    st2 = stamp_turn("hello", presence)
    assert st2["beam_angle"] is None
    assert st2["beam_conf"] is None
    assert st2["beam_hour"] is None


def test_presence_user_id_no_hardcoded_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("HOUSE_USER_ID", raising=False)
    monkeypatch.delenv("RAIL_PRIMARY_USER", raising=False)
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    (tmp_path / "people").mkdir()
    assert _presence_user_id({}) == ""
    assert _presence_user_id({"user": {"user_id": "user"}}) == "user"
    assert _presence_user_id({"user_id": "user"}) == "user"


def test_visitor_today_can_name_turn():
    """Guest face today → ear may stamp the turn; not occupancy/sole."""
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"slot_id": "s0", "name": "user", "certain": True}],
        "visitor_today": [{"person_id": "visitor", "day": "2026-08-13"}],
        "user": {"user_id": "user", "auth": True, "user_present": True},
    }
    from crissa_ear.stamp import visitor_today_ids

    assert visitor_today_ids(presence) == ["visitor"]
    st = stamp_turn("hello there", presence)
    # without emb, sole still the user (occupancy)
    assert st["person_id"] == "user"
    assert st["source"] == "sole_present"


def test_visitor_today_ids_helper():
    from crissa_ear.stamp import visitor_today_ids

    assert visitor_today_ids({"visitor_today": [{"person_id": "visitor"}]}) == ["visitor"]
    assert visitor_today_ids({}) == []


def test_sole_present():
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"slot_id": "s0", "name": "user", "certain": True}],
    }
    st = stamp_turn("what's the weather", presence)
    assert st["person_id"] == "user"
    assert st["source"] == "sole_present"
    assert st["certain"] is True
    assert st["display_name"] == "user"


def test_sole_present_stale_but_certain():
    """Stale sole + face-certain: still stamp name but provisional (the User 2026-08-06).

    count_trust=stale alone demotes sole so multi-person speech is not locked to
    a ghost face; source is sole_present_stale (not confirmed).
    """
    presence = {
        "headcount": 1,
        "count_certain": False,
        "count_trust": "stale",
        "people": [{"slot_id": "s0", "name": "user", "certain": True}],
    }
    st = stamp_turn("Voice off, I think.", presence)
    assert st["person_id"] == "user"
    assert st["source"] == "sole_present_stale"
    assert st["certain"] is False
    assert st["id_status"] == "provisional"


def test_sole_present_stale_uncertain_provisional():
    """Stale + uncertain sole name → provisional person_id for omni/ear (soft-ID)."""
    presence = {
        "headcount": 1,
        "count_certain": False,
        "count_trust": "stale",
        "people": [{"name": "user", "certain": False}],
    }
    st = stamp_turn("hello", presence)
    assert st["person_id"] == "user"
    # stale trust → sole_present_stale when certain face; uncertain still sole_present
    assert st["source"] in ("sole_present", "sole_present_stale")
    assert st["certain"] is False
    assert st["id_status"] == "provisional"
    assert st["display_name"] == "the user?"


def test_sole_present_beats_off_roster_mention():
    """'the user left' while only second in room → speaker prior is second, the user is mention."""
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "second", "certain": True}],
    }
    st = stamp_turn("the user left already", presence, extra_names=["user", "second"])
    assert st["person_id"] == "second"
    assert st["source"] == "sole_present"
    assert "user" in st["mentioned"]


def test_in_room_name_when_multi():
    presence = {
        "headcount": 2,
        "count_trust": "fact",
        "people": [
            {"name": "user", "certain": True},
            {"name": "second", "certain": True},
        ],
    }
    st = stamp_turn("second left her keys", presence)
    assert st["person_id"] == "second"
    assert st["source"] == "vocative_in_room"
    # vocative alone is soft until confirmed
    assert st["certain"] is False
    assert st["display_name"] == "second?"


def test_provisional_shell_soft(tmp_path, monkeypatch):
    """An alias vocative with its person already provisional on the roster."""
    _identity_root(tmp_path, monkeypatch)
    presence = {
        "headcount": 2,
        "count_trust": "fact",
        "people": [
            {"name": "user", "certain": True},
            {"name": "second", "certain": False, "id_status": "provisional"},
        ],
    }
    st = stamp_turn("Shell said she will be late", presence)
    # confirm_mention: naming provisional second drops ?
    assert st["person_id"] == "second"
    assert st["confirm"] is True
    assert st["certain"] is True
    assert st["display_name"] == "second"
    assert st["id_status"] == "confirmed"


def test_ambiguous_two_names():
    presence = {
        "headcount": 2,
        "count_trust": "fact",
        "people": [{"name": "user"}, {"name": "second"}],
    }
    st = stamp_turn("the user and second are here", presence)
    assert st["person_id"] is None
    assert st["source"] == "vocative_ambiguous"


def test_empty_roster_no_guess():
    st = stamp_turn("hello there", {"headcount": 0, "people": [], "count_trust": "fact"})
    assert st["person_id"] is None


def test_voice_emb_fills_unknown_slot(monkeypatch):
    """Face only has U1/U2; voice matches the user → provisional speaker stamp (omni).

    Soft-fill thr (RAIL_VOICE_SOFT_FILL_SIM, default 0.55) is higher than match
    thr — media lands ~0.40–0.48; open-room stamps need a real emb lock.
    """
    import numpy as np
    from crissa_ear import stamp as stamp_mod

    def fake_match(emb, bank=None, threshold=None):
        return "user", 0.58

    monkeypatch.setattr(stamp_mod, "match_voice", fake_match, raising=False)
    # stamp imports match_voice inside function from voice_bank
    import crissa_ear.voice_bank as vb

    monkeypatch.setattr(vb, "match_voice", fake_match)
    monkeypatch.setattr(vb, "rank_voice", lambda *a, **k: [("user", 0.58)])

    presence = {
        "headcount": 2,
        "count_trust": "fact",
        "people": [
            {"slot_id": "u0", "name": "U1", "certain": False},
            {"slot_id": "u1", "name": "U2", "certain": False},
        ],
    }
    st = stamp_turn(
        "Alright, let's see.",
        presence,
        voice_emb=np.ones(192),
        speech_duration_s=2.0,
    )
    assert st["person_id"] == "user"
    assert st["source"] == "voice_emb"
    assert st["certain"] is False
    assert st["id_status"] == "provisional"
    assert st["display_name"] == "the user?"
    assert st["voice_emb_sim"] == 0.58


def test_vocative_fills_unknown_slot():
    """'This is the User' with only U-slots → soft vocative stamp (no face name)."""
    presence = {
        "headcount": 2,
        "count_trust": "fact",
        "people": [
            {"name": "U1", "certain": False},
            {"name": "U2", "certain": False},
        ],
    }
    st = stamp_turn(
        "This is the User.",
        presence,
        extra_names=["user", "second"],
    )
    assert st["person_id"] == "user"
    assert st["source"] == "vocative_unknown_slot"
    assert st["certain"] is False


def test_voice_off_named_room_still_allows_sole(monkeypatch):
    """Only second here; closed rank weak on her emb → sole still paints second.

    Open-bank the user (not present) must not win person_id (2026-08-11 closed set).
    """
    import numpy as np
    import crissa_ear.voice_bank as vb

    def fake_rank(emb, roster=None, top_k=3):
        r = [str(x).lower() for x in (roster or [])]
        if r == ["second"]:
            return [("second", 0.12)]  # below thr → no voice lock
        if roster is None:
            return [("user", 0.5), ("second", 0.12)]
        return []

    monkeypatch.setattr(vb, "rank_voice", fake_rank)
    monkeypatch.setattr(vb, "match_voice", lambda *a, **k: ("user", 0.5))
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "second", "certain": True}],
    }
    st = stamp_turn(
        "hello there friend",
        presence,
        voice_emb=np.ones(192),
        speech_duration_s=2.5,
    )
    assert st["source"] == "sole_present"
    assert st["person_id"] == "second"
    assert st["person_id"] != "user"
    assert st.get("voice_emb_sim") == 0.12


def test_short_clip_skips_voice_stamp(monkeypatch):
    """P0: short duration must not stamp from emb (or override sole)."""
    import numpy as np
    import crissa_ear.voice_bank as vb

    monkeypatch.setattr(
        vb, "rank_voice", lambda *a, **k: [("second", 0.9), ("user", 0.1)]
    )
    monkeypatch.setattr(vb, "match_voice", lambda *a, **k: ("second", 0.9))
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "user", "certain": True}],
    }
    st = stamp_turn(
        "hi",
        presence,
        voice_emb=np.ones(192),
        speech_duration_s=0.6,
    )
    assert st.get("voice_skip") == "short_clip"
    assert st["person_id"] == "user"
    assert st["source"] == "sole_present"


def test_voice_emb_vs_sole_when_face_wrong(monkeypatch):
    """Stale sole the user + world-bank second must NOT invent second (closed set).

    Second must be on presence to win vs_sole. World open-bank steal retired
    2026-08-11 ("who here spoke").
    """
    import numpy as np
    import crissa_ear.voice_bank as vb

    def fake_rank(emb, roster=None, top_k=3):
        # Closed sole [the user] only — weak the user; no open-bank second
        if roster is not None and list(roster) == ["user"]:
            return [("user", 0.07)]
        return [("second", 0.28), ("user", 0.07)]

    monkeypatch.setattr(vb, "rank_voice", fake_rank)

    presence = {
        "headcount": 1,
        "count_certain": False,
        "count_trust": "stale",
        "people": [{"name": "user", "certain": True}],
    }
    st = stamp_turn(
        "hello there, this is a full sentence for voice.",
        presence,
        voice_emb=np.ones(192),
        speech_duration_s=2.5,
    )
    assert st["person_id"] != "second"
    assert st["source"] != "voice_emb_vs_sole"
    # Stale sole + weak closed voice → unlabeled rather than ghost the user paint
    assert st["source"] in ("sole_stale_unlabeled", "sole_present_stale", None) or st[
        "person_id"
    ] in (None, "user")


def test_voice_emb_vs_sole_among_present(monkeypatch):
    """Two people here: weak the user emb + stronger second emb → second."""
    import numpy as np
    import crissa_ear.voice_bank as vb

    def fake_rank(emb, roster=None, top_k=3):
        r = set(str(x).lower() for x in (roster or []))
        if r == {"user", "second"} or r == {"second", "user"}:
            return [("second", 0.55), ("user", 0.08)]
        if r == {"user"}:
            return [("user", 0.08)]
        if r == {"second"}:
            return [("second", 0.55)]
        return []

    monkeypatch.setattr(vb, "rank_voice", fake_rank)

    presence = {
        "headcount": 2,
        "count_certain": True,
        "count_trust": "fact",
        "people": [
            {"name": "user", "certain": True},
            {"name": "second", "certain": True},
        ],
    }
    st = stamp_turn(
        "hello there, this is a full sentence for voice.",
        presence,
        voice_emb=np.ones(192),
        speech_duration_s=2.5,
    )
    assert st["person_id"] == "second"
    assert st["source"] == "voice_emb"
    assert st["voice_emb_sim"] == 0.55


def test_empty_room_no_world_voice_stamp(monkeypatch):
    """Empty inventory: never stamp Visitor/world identity from open bank."""
    import numpy as np
    import crissa_ear.voice_bank as vb

    monkeypatch.setattr(
        vb, "rank_voice", lambda *a, **k: [("visitor", 0.70), ("user", 0.20)]
    )
    monkeypatch.setattr(vb, "match_voice", lambda *a, **k: ("visitor", 0.70))

    presence = {"headcount": 0, "people": [], "count_trust": "fact"}
    st = stamp_turn(
        "hello there, this is a full sentence for voice.",
        presence,
        voice_emb=np.ones(192),
        speech_duration_s=2.5,
    )
    assert st["person_id"] is None
    assert st["source"] == "voice_emb_empty_room"


def test_empty_room_user_auth_includes_sudo_voice(monkeypatch):
    """Bank + visor, not occupancy people[]: the user voice stamps the user."""
    import numpy as np
    import crissa_ear.house_id as hid
    import crissa_ear.voice_bank as vb

    def fake_rank(emb, roster=None, top_k=3):
        r = [str(x).lower() for x in (roster or [])]
        if "user" in r:
            return [("user", 0.72)]
        return [("visitor", 0.90)]

    monkeypatch.setattr(vb, "rank_voice", fake_rank)
    monkeypatch.setattr(vb, "match_sim_for", lambda n=None: 0.48)
    monkeypatch.setattr(hid, "index_ids", lambda: ["user", "second"])
    monkeypatch.setenv("RAIL_VOICE_MATCH_SIM", "0.48")

    presence = {
        "headcount": 0,
        "people": [],
        "count_trust": "fact",
        "user_id": "user",
        "face": {"locked": None, "faces": [], "age_s": 0.4},
        "user": {"user_id": "user"},
    }
    st = stamp_turn(
        "hello there, this is a full sentence for voice.",
        presence,
        voice_emb=np.ones(192),
        speech_duration_s=2.5,
    )
    assert st["person_id"] == "user"
    assert st["source"] in ("voice_emb", "voice_emb_user_auth")


def test_named_room_no_open_bank_fallback(monkeypatch):
    """Bank roster is the user only — do not invent visitor off an open world rank."""
    import numpy as np
    import crissa_ear.house_id as hid
    import crissa_ear.voice_bank as vb

    def fake_rank(emb, roster=None, top_k=3):
        if roster is None:
            return [("visitor", 0.80), ("user", 0.40)]
        r = [str(x).lower() for x in roster]
        if r == ["user"]:
            return [("user", 0.40)]
        return [("visitor", 0.80)]

    monkeypatch.setattr(vb, "rank_voice", fake_rank)
    monkeypatch.setattr(vb, "match_voice", lambda *a, **k: ("visitor", 0.80))
    monkeypatch.setattr(hid, "index_ids", lambda: ["user"])
    monkeypatch.setenv("RAIL_VOICE_MATCH_SIM", "0.35")

    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "user", "certain": True}],
    }
    st = stamp_turn(
        "hello there, this is a full sentence for voice.",
        presence,
        voice_emb=np.ones(192),
        speech_duration_s=2.5,
    )
    assert st["person_id"] != "visitor"
    # the user 0.40 may fail is_user floor — never world visitor
    assert st.get("person_id") in ("user", None)


def test_voice_emb_vs_sole_blocked_when_sole_emb_strong(monkeypatch):
    """Gate 1 + isUser 0.45: the user sim 0.42 is under is_user floor — no the User paint.

    Impostor-class band (0.35–0.45) must not force person_id=the user (P0/P1).
    vs_sole must also not steal when sole emb healthy — here isUser wins first.
    """
    import numpy as np
    import crissa_ear.voice_bank as vb
    import crissa_ear.stamp as stamp_mod

    def fake_rank(emb, roster=None, top_k=3):
        if roster == ["user"]:
            return [("user", 0.42)]
        return [("second", 0.48), ("user", 0.42)]

    monkeypatch.setattr(vb, "rank_voice", fake_rank)
    monkeypatch.setattr(vb, "match_voice", lambda *a, **k: ("user", 0.42))
    monkeypatch.setattr(stamp_mod, "match_voice", lambda *a, **k: ("user", 0.42), raising=False)
    monkeypatch.setenv("RAIL_VOICE_MATCH_SIM", "0.35")

    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "user", "certain": True}],
    }
    st = stamp_turn(
        "hello there, this is a full sentence for voice.",
        presence,
        voice_emb=np.ones(192),
        speech_duration_s=2.5,
    )
    # 0.42 < is_user floor 0.45 → not eligible as the User; guard strips sole/emb the user
    assert st["person_id"] != "user" or st.get("is_user") is True
    assert st["person_id"] is None or st["person_id"] == "second"
    assert st["source"] != "voice_emb_vs_sole" or st["person_id"] == "second"


def test_voice_emb_vs_sole_blocked_when_open_noise_only(monkeypatch):
    """Weak emb noise: isUser uncertain → do not sole-paint the User when voice present (P2)."""
    import numpy as np
    import crissa_ear.voice_bank as vb
    import crissa_ear.stamp as stamp_mod

    def fake_rank(emb, roster=None, top_k=3):
        if roster == ["user"]:
            return [("user", 0.05)]
        return [("second", 0.12), ("user", 0.05)]

    monkeypatch.setattr(vb, "rank_voice", fake_rank)
    monkeypatch.setattr(vb, "match_voice", lambda *a, **k: ("second", 0.12))
    monkeypatch.setattr(stamp_mod, "match_voice", lambda *a, **k: ("second", 0.12), raising=False)

    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "user", "certain": True}],
    }
    st = stamp_turn(
        "hello there, this is a full sentence for voice.",
        presence,
        voice_emb=np.ones(192),
        speech_duration_s=2.5,
    )
    # Voice usable + weak sims → never force the user via sole (isUser live guard)
    assert st["person_id"] != "user"
    assert st.get("is_user_guard_suppressed") is True or st["person_id"] is None


def test_vocative_common_word_may_ignored():
    """Bare modal/name collisions stay quiet without address cue."""
    assert vocative_hits("may I open the door", ["user", "may"]) == []
    assert vocative_hits("hey may are you home", ["user", "may"]) == ["may"]


def test_match_sim_for_per_person(monkeypatch):
    from crissa_ear.voice_bank import match_sim_for

    monkeypatch.setenv("RAIL_VOICE_MATCH_SIM", "0.35")
    monkeypatch.setenv("RAIL_VOICE_MATCH_SIM_SECOND", "0.30")
    assert abs(match_sim_for(None) - 0.35) < 1e-6
    assert abs(match_sim_for("second") - 0.30) < 1e-6
    assert abs(match_sim_for("user") - 0.35) < 1e-6


# ── Adaptive s-norm (asnorm) shadow fields ─────────────────────────


def test_asnorm_off_by_default(monkeypatch):
    """RAIL_VOICE_ASNORM not set → shadow fields absent."""
    import numpy as np
    import crissa_ear.voice_bank as vb

    monkeypatch.setattr(
        vb, "rank_voice", lambda *a, **k: [("user", 0.5)]
    )
    monkeypatch.setattr(vb, "match_voice", lambda *a, **k: ("user", 0.5))

    # Ensure env is unset
    monkeypatch.delenv("RAIL_VOICE_ASNORM", raising=False)

    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "user", "certain": True}],
    }
    st = stamp_turn("hello", presence, voice_emb=np.ones(192))
    assert "top_sim_asnorm" not in st
    assert "asnorm_calibrated_score" not in st
    assert "would_stamp_asnorm" not in st


def test_asnorm_shadow_fields_present_when_enabled(monkeypatch):
    """RAIL_VOICE_ASNORM=1 → shadow fields computed alongside normal stamp."""
    import numpy as np
    import crissa_ear.voice_bank as vb
    import crissa_ear.stamp as stamp_mod

    # Mock rank_voice / match_voice
    monkeypatch.setattr(
        vb, "rank_voice", lambda *a, **k: [("user", 0.5)]
    )
    monkeypatch.setattr(vb, "match_voice", lambda *a, **k: ("user", 0.5))

    # Mock asnorm_calibrated_score to return deterministic values
    def fake_asnorm(emb, target_person=None):
        return {
            "top_sim": 0.55,
            "top_name": "user",
            "calibrated_score": 0.62,
            "cohort_mu": 0.3,
            "cohort_sigma": 0.1,
            "z_score": 3.2,
            "cohort_size": 5,
        }

    monkeypatch.setattr(vb, "asnorm_calibrated_score", fake_asnorm)
    monkeypatch.setenv("RAIL_VOICE_ASNORM", "1")

    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "user", "certain": True}],
    }
    st = stamp_turn("hello", presence, voice_emb=np.ones(192))

    # Shadow fields present
    assert st["top_sim_asnorm"] == 0.55
    assert st["top_name_asnorm"] == "user"
    assert st["asnorm_calibrated_score"] == 0.62
    assert st["asnorm_cohort_mu"] == 0.3
    assert st["asnorm_cohort_sigma"] == 0.1
    assert st["asnorm_z_score"] == 3.2
    assert st["asnorm_cohort_size"] == 5

    # would_stamp_asnorm: calibrated_score 0.62 >= threshold 0.5, n_roster=1 → True
    assert st["would_stamp_asnorm"] is True

    # Live stamp unaffected
    assert st["person_id"] == "user"
    assert st["source"] == "sole_present"


def test_asnorm_does_not_affect_live_gating(monkeypatch):
    """Enabling asnorm must not change person_id, source, or certain."""
    import numpy as np
    import crissa_ear.voice_bank as vb

    monkeypatch.setattr(
        vb, "rank_voice", lambda *a, **k: [("user", 0.5)]
    )
    monkeypatch.setattr(vb, "match_voice", lambda *a, **k: ("user", 0.5))

    def fake_asnorm(emb, target_person=None):
        return {
            "top_sim": 0.9,
            "top_name": "second",  # deliberately different from live person_id
            "calibrated_score": 0.95,
            "cohort_mu": 0.3,
            "cohort_sigma": 0.1,
            "z_score": 6.5,
            "cohort_size": 8,
        }

    monkeypatch.setattr(vb, "asnorm_calibrated_score", fake_asnorm)
    monkeypatch.setenv("RAIL_VOICE_ASNORM", "1")

    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "user", "certain": True}],
    }
    st = stamp_turn("hello", presence, voice_emb=np.ones(192))
    # Sole present the user should be the live stamp, not second from asnorm
    assert st["person_id"] == "user"
    assert st["source"] == "sole_present"
    assert st["certain"] is True


def test_asnorm_would_stamp_uses_threshold(monkeypatch):
    """RAIL_VOICE_ASNORM_THR controls the threshold for would_stamp_asnorm."""
    import numpy as np
    import crissa_ear.voice_bank as vb

    monkeypatch.setattr(
        vb, "rank_voice", lambda *a, **k: [("user", 0.5)]
    )
    monkeypatch.setattr(vb, "match_voice", lambda *a, **k: ("user", 0.5))

    def fake_asnorm(emb, target_person=None):
        return {
            "top_sim": 0.55,
            "top_name": "user",
            "calibrated_score": 0.62,
            "cohort_mu": 0.3,
            "cohort_sigma": 0.1,
            "z_score": 3.2,
            "cohort_size": 5,
        }

    monkeypatch.setattr(vb, "asnorm_calibrated_score", fake_asnorm)
    monkeypatch.setenv("RAIL_VOICE_ASNORM", "1")

    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "user", "certain": True}],
    }

    # Default threshold 0.5 → 0.62 >= 0.5 → True
    st = stamp_turn("hello", presence, voice_emb=np.ones(192))
    assert st["would_stamp_asnorm"] is True

    # Raise threshold to 0.7 → 0.62 < 0.7 → False
    monkeypatch.setenv("RAIL_VOICE_ASNORM_THR", "0.7")
    st2 = stamp_turn("hello", presence, voice_emb=np.ones(192))
    assert st2["would_stamp_asnorm"] is False


def test_asnorm_no_effect_when_no_voice_emb(monkeypatch):
    """No voice_emb → asnorm shadow fields not computed."""
    monkeypatch.delenv("RAIL_VOICE_ASNORM", raising=False)
    monkeypatch.setenv("RAIL_VOICE_ASNORM", "1")

    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "user", "certain": True}],
    }
    st = stamp_turn("hello", presence)  # no voice_emb
    assert "top_sim_asnorm" not in st
    assert "asnorm_calibrated_score" not in st


def test_face_speaker_ids_voice_by_face():
    """F2F the face check lock names the turn. Occupancy chalkboard is not this path."""
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"slot_id": "s0", "name": "user", "certain": True}],
        "f2f": True,
        "face": {
            "f2f": True,
            "locked": "user",
            "faces": [{"name": "user", "sim": 0.81, "sex": "M"}],
            "age_s": 0.4,
        },
    }
    st = stamp_turn("hello there", presence)
    assert st["person_id"] == "user"
    assert st["source"] == "face_speaker"
    assert st["certain"] is True


def test_face_speaker_skipped_on_scenery_lock():
    """Door scenery face is not the speaker."""
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"slot_id": "s0", "name": "user", "certain": True}],
        "f2f": False,
        "face": {
            "f2f": False,
            "locked": "user",
            "faces": [{"name": "user", "sim": 0.81}],
        },
    }
    st = stamp_turn("hello there", presence)
    assert st["source"] == "sole_present"
    assert st["person_id"] == "user"


def test_face_speaker_weak_sim_ignored():
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"slot_id": "s0", "name": "user", "certain": True}],
        "f2f": True,
        "face": {
            "f2f": True,
            "locked": "user",
            "faces": [{"name": "user", "sim": 0.2}],
            "age_s": 0.2,
        },
    }
    st = stamp_turn("hello there", presence)
    assert st["source"] != "face_speaker"


def test_face_speaker_overrides_wrong_voice_emb(monkeypatch):
    """Voice bank says second; the face check looking at the User wins."""
    import numpy as np
    import crissa_ear.voice_bank as vb

    monkeypatch.setattr(
        vb, "rank_voice", lambda *a, **k: [("second", 0.9), ("user", 0.1)]
    )
    monkeypatch.setattr(vb, "match_voice", lambda *a, **k: ("second", 0.9))
    monkeypatch.setattr(vb, "match_sim_for", lambda name=None: 0.35)
    presence = {
        "headcount": 2,
        "count_certain": True,
        "count_trust": "fact",
        "people": [
            {"name": "user", "certain": True},
            {"name": "second", "certain": True},
        ],
        "f2f": True,
        "face": {
            "f2f": True,
            "locked": "user",
            "faces": [{"name": "user", "sim": 0.84}],
            "age_s": 0.3,
        },
    }
    st = stamp_turn(
        "hello there friend",
        presence,
        voice_emb=np.ones(192),
        speech_duration_s=2.5,
    )
    assert st["person_id"] == "user"
    assert st["source"] == "face_speaker"


def test_face_speaker_from_oracle_ring_when_beam_at_user():
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "user", "certain": True}],
        "f2f": False,
        "visits": [
            {
                "event_id": "ev_speaker",
                "event": "speaker",
                "person_id": "user",
                "counted": False,
                "trigger_ts": __import__("time").time(),
                "face": {"name": "user", "sim": 0.81, "source": "f2f"},
            }
        ],
    }
    import os

    os.environ["HOUSE_MIC_AZ_RESIDUAL_DEG"] = "0"
    os.environ["HOUSE_MIC_AZ_DESK_DEG"] = "240"
    try:
        st = stamp_turn(
            "hello there",
            presence,
            beam_angle=180.0,
            beam_conf=0.9,
        )
        # 180° with residual 0 → hour 6
        assert st["beam_hour"] == 6
        assert st["person_id"] == "user"
        assert st["source"] == "face_speaker"
    finally:
        os.environ.pop("HOUSE_MIC_AZ_RESIDUAL_DEG", None)
        os.environ.pop("HOUSE_MIC_AZ_DESK_DEG", None)


def test_face_speaker_still_tts_vetoed(monkeypatch):
    monkeypatch.setattr(
        "crissa_ear.stamp.tts_veto_check",
        lambda *a, **k: {
            "veto": True,
            "tts_fp_sim": 0.92,
            "tts_fp_count": 1,
            "threshold": 0.5,
        },
    )
    presence = {
        "headcount": 1,
        "count_certain": True,
        "count_trust": "fact",
        "people": [{"name": "user", "certain": True}],
        "f2f": True,
        "face": {
            "f2f": True,
            "locked": "user",
            "faces": [{"name": "user", "sim": 0.81}],
            "age_s": 0.2,
        },
    }
    st = stamp_turn(
        "Voice greeted. Good morning.",
        presence,
        voice_emb=[0.1] * 192,
        speech_duration_s=2.0,
    )
    assert st["source"] == "tts_echo_veto"
    assert st["person_id"] is None
