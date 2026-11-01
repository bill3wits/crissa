"""Entry-anchored session AUTH (user 2026-08-04): opened by face + strong
voice, HELD by presence continuity with no timer, dropped by exit or
lights-out. Short weak utterances must never flicker AUTH off — that was
the wrong_voice silencing user lived with."""
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
from crissa_presence.service import PresenceService


def make_svc(*, hold=None, voice=None, session=None, present=("user",)):
    svc = PresenceService.__new__(PresenceService)
    svc._face_auth_hold = dict(hold or {})
    svc._last_user_voice = dict(
        voice or {"match": False, "sim": None, "ts": 0.0, "person_id": None}
    )
    svc._user_session = dict(session) if session else None
    svc._left_block_until = {}
    svc._id_certain = {}
    svc._id_sources = {}
    svc._auth_levels = {}
    svc.inventory = SimpleNamespace(present={p: True for p in present})
    svc._user_id = PresenceService._user_id.__get__(svc, PresenceService)
    svc._note_left = PresenceService._note_left.__get__(svc, PresenceService)
    return svc


def user_slot(*, sticky=True):
    return SimpleNamespace(
        name="user",
        certain=True,
        auth={"level": "face", "authed": True, "sticky": sticky},
        sources={"face": True, "face_sim": 0.7, "face_sticky": sticky},
    )


def test_opens_on_face_plus_strong_voice():
    svc = make_svc(voice={"match": True, "sim": 0.59, "ts": time.time(),
                          "person_id": "user"})
    blk = svc._user_auth_from_slots([user_slot()], lights_out=False)
    assert blk["user_auth"] and blk["user_auth_reason"] == "ok"
    assert blk["user_session_held"] and svc._user_session


def test_holds_through_stale_and_weak_voice():
    """The flicker case: session open, then only a weak 'Anyway.' 90s later."""
    svc = make_svc(
        voice={"match": False, "sim": 0.165, "ts": time.time(),
               "person_id": "user"},
        session={"opened_ts": time.time() - 600,
                 "last_strong_voice_ts": time.time() - 90,
                 "last_strong_voice_sim": 0.59},
    )
    blk = svc._user_auth_from_slots([user_slot()], lights_out=False)
    assert blk["user_auth"], "weak utterance must not drop a held session"
    # a non-matching voice under 30 s old publishes the contested tier and keeps AUTH (the rail's 64258a1,
    # 2026-08-26: "Sticky unmatched voice publishes contested without dropping AUTH")
    assert blk["user_auth_reason"] == "wrong_voice"
    assert blk["user_strong_voice_age_s"] >= 89
    # once that weak voice is older than 30 s the held session is what the reason says
    svc._last_user_voice["ts"] = time.time() - 40
    blk = svc._user_auth_from_slots([user_slot()], lights_out=False)
    assert blk["user_auth"] and blk["user_auth_reason"] == "session_held"


def test_drops_on_exit():
    """Strong leave clears sticky → AUTH ends (inventory lag alone does not)."""
    svc = make_svc(
        hold={"user": {"sim": 0.7, "ts": time.time(), "source": "door_face"}},
        session={"opened_ts": time.time() - 60,
                 "last_strong_voice_ts": time.time() - 10,
                 "last_strong_voice_sim": 0.6},
        present=(),
    )
    # Still AUTH with sticky even if inventory empty
    blk0 = svc._user_auth_from_slots([], lights_out=False)
    assert blk0["user_auth"]
    # Tombstone leave clears sticky
    svc._note_left(["user"])
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert not blk["user_auth"] and blk["user_auth_reason"] == "user_absent"
    assert not blk["user_session_held"]


def test_lights_out_clears_session():
    svc = make_svc(
        hold={"user": {"sim": 0.7, "ts": time.time(), "source": "door_face"}},
        session={"opened_ts": time.time() - 60,
                 "last_strong_voice_ts": time.time() - 5,
                 "last_strong_voice_sim": 0.6},
    )
    blk = svc._user_auth_from_slots([user_slot()], lights_out=True)
    assert not blk["user_auth"] and blk["user_auth_reason"] == "lights_out"
    assert svc._user_session is None


def test_voice_alone_never_opens():
    """Face at entry always required (user): strong voice with no face
    evidence stays user_absent — voice sustains, never opens."""
    svc = make_svc(voice={"match": True, "sim": 0.7, "ts": time.time(),
                          "person_id": "user"}, present=())
    blk = svc._user_auth_from_slots([], lights_out=False)
    assert not blk["user_auth"] and blk["user_auth_reason"] == "user_absent"
    assert not blk["user_session_held"]


def test_wanda_can_hold_a_sticky_badge():
    """Ultra defect 13: the old prefix guard ate every name starting u/f/m."""
    svc = make_svc()
    svc._note_face_auth("wanda", sim=0.8, source="door_face")
    assert "wanda" in svc._face_auth_hold
    svc._note_face_auth("u1", sim=0.8)
    svc._note_face_auth("__u2", sim=0.8)
    svc._note_face_auth("unknown-3", sim=0.8)
    assert set(svc._face_auth_hold) == {"wanda"}


def test_surprise_has_no_cooldown_timer():
    import inspect
    from crissa_presence.service import PresenceService as S

    body = inspect.getsource(S.surprise_from_voice)
    assert "SURPRISE_COOLDOWN" not in body
    assert "cooldown_sec" not in body
    q = inspect.getsource(S.enqueue_surprise)
    # a flood is reported, not Busy (the rail's bcc92b6, 2026-08-26: the mill no longer posts queue_flood Busy)
    assert '"flood": flood' in q and "set_room_busy" not in q
    assert "SURPRISE_QUEUE_MAX" in q


def test_surprise_skips_parade_when_already_authed():
    import inspect
    from crissa_presence.service import PresenceService as S

    src = inspect.getsource(S.surprise_from_voice)
    assert "already_authed" in src
    assert "reauth_facefind_all" not in src or "already_authed" in src


def test_auth_law_does_not_mint_occupancy():
    import inspect
    from crissa_presence.service import PresenceService as S

    src = inspect.getsource(S.run_door_once)
    assert "no occupancy mint" in src
    assert "self.inventory.present[uid_auth] = now" not in src
    grant = inspect.getsource(S.grant_user_face_auth)
    assert "inv.present[uid]" not in grant


def test_room_check_face_does_not_mint_absent_user():
    """AUTH match still does not +1; empty-room surprise may union seat faces."""
    import inspect
    from crissa_presence.service import PresenceService as S

    src = inspect.getsource(S._match_user_in_jpegs)
    assert "mark_sticky" in src
    assert "if uid not in" not in src
    assert "if uid in present" in src
    body = inspect.getsource(S.surprise_from_voice)
    assert "_new_unnamed_key" not in body
    assert "empty_voice_scan_add" in body
    assert "apply_reconcile" in body
    assert "found_at_seats" in inspect.getsource(S._reauth_facefind_all_stations)
    assert "_surprise_from_media" in body or "media" in body
    assert "not_empty" in body
    assert "any in-room voice" in body
    seat = inspect.getsource(S._seat_face_may_add_occupancy)
    assert "h == 8" in seat
    assert "media" in seat
    media_fn = inspect.getsource(S._surprise_from_media)
    assert "h == 8" not in media_fn


def test_surprise_skips_parade_only_on_user_auth():
    """already_authed must not block empty-room occupancy add."""
    import inspect
    from crissa_presence.service import PresenceService as S

    body = inspect.getsource(S.surprise_from_voice)
    auth = body.split("if mode_l == \"user_auth\":", 1)[1]
    assert "already_authed" in auth
    before_auth = body.split("if mode_l == \"user_auth\":", 1)[0]
    assert "reason\": \"already_authed\"" not in before_auth


def test_uncounted_body_does_not_hunt_after_clip_leave():
    """10:32:57 zero, then 10:33:09 look_around on empty door ate the walk-in."""
    import inspect
    from crissa_presence.service import PresenceService as S

    ub = inspect.getsource(S._uncounted_body_check)
    assert "_last_clip_leave_ts" in ub
    assert "60.0" in ub
    assert "_room_busy" in ub
    vis = inspect.getsource(S.apply_oracle_visit)
    assert "_last_clip_leave_ts" in vis
    orient = inspect.getsource(S.run_smooth_orient_once)
    assert "uncounted" in orient


def test_oracle_html_does_not_say_35b():
    from crissa_presence import server as srv

    assert "35B" not in srv.COMPARE_HTML
    assert "35B" not in srv.HTML
    assert "ops 35B" not in (srv.build_prod_feed.__doc__ or "")
