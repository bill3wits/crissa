"""sound.heard is a hearing field only — never a roster or last_event write."""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from test_auth_first import _svc
from crissa_presence.service import PresenceService


def test_sound_heard_does_not_touch_roster_or_event():
    svc = _svc(
        present=("user",),
        hold={"user": {"sim": 0.7, "ts": time.time(), "source": "entry"}},
    )
    svc._user_session = {"opened_ts": time.time() - 5}
    before_hold = dict(svc._face_auth_hold)
    before_hc = svc.inventory.headcount()
    before_event = dict(svc.get().last_event or {})
    before_session = dict(svc._user_session)
    st = svc.apply_instruction(
        {
            "op": "sound.heard",
            "az_deg": 210.4,
            "conf": 1.0,
            "source": "xvf3800",
            "person_id": "user",
            "id_source": "voice_emb",
            "certain": True,
            "voice_emb_sim": 0.70,
            "speech": True,
        }
    )
    assert st.sound["az_deg"] == 210.4
    assert st.sound["person_id"] == "user"
    assert st.sound["id_source"] == "voice_emb"
    assert st.sound["id_level"] == "strong"
    assert st.sound["source"] == "xvf3800"
    assert st.sound["frame"] == "mic"
    assert "kind" not in (st.last_event or {}) or st.last_event == before_event
    assert (svc.get().last_event or {}) == before_event
    assert svc._face_auth_hold == before_hold
    assert svc.inventory.headcount() == before_hc
    assert svc._user_session == before_session
    d = st.to_dict()
    assert d["sound"]["az_deg"] == 210.4


def test_sound_heard_rate_limit():
    svc = _svc()
    svc.apply_instruction({"op": "sound.heard", "az_deg": 10, "conf": 1})
    st2 = svc.apply_instruction({"op": "sound.heard", "az_deg": 90, "conf": 1})
    assert st2.sound["az_deg"] == 10


def test_sound_survives_republish():
    svc = _svc(
        present=("user",),
        hold={"user": {"sim": 0.7, "ts": time.time(), "source": "entry"}},
    )
    svc.apply_instruction({"op": "sound.heard", "az_deg": 40, "conf": 0.9})
    cur = svc.get()
    st = svc._presence_from_inventory(
        frame_age=0.1,
        camera_open=True,
        camera_blind=False,
        last_event=cur.last_event,
        count_trust="fact",
        camera_mode="holding",
        door_names=["user"],
    )
    assert st.sound and st.sound["az_deg"] == 40


def test_sole_present_does_not_name_the_lobe():
    svc = _svc()
    st = svc.apply_instruction(
        {
            "op": "sound.heard",
            "az_deg": 67,
            "conf": 1.0,
            "source": "xvf3800",
            "person_id": "user",
            "id_source": "sole_present",
            "certain": True,
        }
    )
    assert st.sound.get("id_level") == "none"
    assert "person_id" not in st.sound
    assert st.sound.get("id_source") == "sole_present"
    assert "beam_hour" not in (st.sound or {})


def test_sound_id_level_table():
    f = PresenceService.sound_id_level
    assert f("sole_present", "user", certain=True) == "none"
    assert f("vocative_in_room", "user") == "none"
    assert f("is_user_block", "user") == "none"
    assert f("voice_emb", "user", certain=True) == "none"
    assert f("voice_emb", "user", certain=True, voice_sim=0.40) == "none"
    assert f("voice_emb", "user", certain=True, voice_sim=0.59) == "strong"
    assert f("voice_emb", "user", certain=False, voice_sim=0.40) == "none"
    assert f("voice_emb", "user", certain=False, voice_sim=0.59) == "strong"
    assert f("scratch_s1", "s1") == "strong"
    assert f("voice_emb", None) == "none"


def test_stale_sound_pub_is_none(monkeypatch):
    svc = _svc()
    svc.apply_instruction({"op": "sound.heard", "az_deg": 47, "conf": 1, "ts": 1.0})
    assert svc._sound and svc._sound["az_deg"] == 47
    monkeypatch.setenv("RAIL_SOUND_TTL_SEC", "6")
    pub = PresenceService._sound_pub(svc)
    assert pub is None
    # Internal last bearing stays so a late name can merge onto it.
    assert svc._sound and svc._sound["az_deg"] == 47


def test_unlabeled_source_is_not_identity():
    """sole_stale must not count as identity or clobber a last strong name."""
    svc = _svc()
    svc.apply_instruction(
        {
            "op": "sound.heard",
            "az_deg": 40,
            "person_id": "user",
            "id_source": "voice_emb",
            "certain": True,
            "voice_emb_sim": 0.70,
            "speech": True,
        }
    )
    st = svc.apply_instruction(
        {
            "op": "sound.heard",
            "az_deg": 41,
            "id_source": "sole_stale_unlabeled",
            "speech": True,
            "voice_emb_sim": 0.36,
        }
    )
    assert st.sound["person_id"] == "user"
    assert st.sound["id_level"] == "strong"
    assert st.sound["id_source"] == "voice_emb"


def test_identity_stamp_not_dropped_by_rate_limit():
    """Utterance-close id must land even if a DOA tick just fired."""
    svc = _svc()
    svc.apply_instruction({"op": "sound.heard", "az_deg": 10, "conf": 1, "speech": True})
    st = svc.apply_instruction(
        {
            "op": "sound.heard",
            "person_id": "user",
            "id_source": "voice_emb",
            "certain": True,
            "voice_emb_sim": 0.70,
            "speech": True,
        }
    )
    assert st.sound["az_deg"] == 10
    assert st.sound["person_id"] == "user"
    assert st.sound["id_level"] == "strong"


def test_az_only_tick_keeps_last_strong_id():
    svc = _svc()
    svc.apply_instruction(
        {
            "op": "sound.heard",
            "az_deg": 40,
            "conf": 1.0,
            "person_id": "user",
            "id_source": "voice_emb",
            "certain": True,
            "voice_emb_sim": 0.70,
            "speech": True,
        }
    )
    time.sleep(0.55)
    st = svc.apply_instruction(
        {"op": "sound.heard", "az_deg": 55, "conf": 1.0, "speech": True}
    )
    assert st.sound["az_deg"] == 55
    assert st.sound["person_id"] == "user"
    assert st.sound["id_level"] == "strong"
    assert st.sound["id_source"] == "voice_emb"
    assert st.sound.get("id_ts") is not None
    assert float(st.sound["id_ts"]) < float(st.sound["ts"])


def test_az_tick_does_not_refresh_identity_ttl(monkeypatch):
    """Puck ticks must not keep a name past RAIL_SOUND_TTL_SEC."""
    svc = _svc()
    now = time.time()
    svc.apply_instruction(
        {
            "op": "sound.heard",
            "az_deg": 40,
            "conf": 1.0,
            "person_id": "xena",
            "id_source": "voice_emb",
            "certain": True,
            "voice_emb_sim": 0.70,
            "speech": True,
            "ts": now - 20.0,
        }
    )
    monkeypatch.setenv("RAIL_SOUND_TTL_SEC", "6")
    st = svc.apply_instruction(
        {"op": "sound.heard", "az_deg": 55, "conf": 1.0, "speech": True, "ts": now}
    )
    assert st.sound["az_deg"] == 55
    assert "person_id" not in (st.sound or {})
    assert (st.sound or {}).get("id_level") in (None, "none")


def test_below_floor_voice_is_not_strong():
    svc = _svc()
    st = svc.apply_instruction(
        {
            "op": "sound.heard",
            "az_deg": 40,
            "person_id": "xena",
            "id_source": "voice_emb",
            "certain": True,
            "voice_emb_sim": 0.40,
            "speech": True,
        }
    )
    assert (st.sound or {}).get("id_level") in (None, "none")
    assert "person_id" not in (st.sound or {})


def test_puck_speech_does_not_look_without_impulse(monkeypatch):
    class _Cam:
        mode = "dir"
        last_error = None

        def grab(self):
            return None

        def grab_buffer(self, **kw):
            return []

    svc = PresenceService(camera=_Cam())
    calls = []
    monkeypatch.setattr(
        svc, "request_beam_look", lambda **k: calls.append(k) or {"ok": True}
    )
    svc.apply_instruction(
        {"op": "sound.heard", "az_deg": 181, "conf": 1, "speech": True}
    )
    assert calls == []
    svc.apply_instruction(
        {
            "op": "sound.heard",
            "az_deg": 181,
            "conf": 1,
            "speech": True,
            "impulse": True,
        }
    )
    assert calls == []
    svc.apply_instruction(
        {
            "op": "sound.heard",
            "az_deg": 181,
            "conf": 1,
            "speech": True,
            "impulse": True,
            "reason": "session_open",
        }
    )
    # Beam is Speaker evidence. Session look is POST /v1/f2f, not az slew.
    assert calls == []


def test_sound_clear_wipes_hearing():
    svc = _svc()
    svc.apply_instruction({"op": "sound.heard", "az_deg": 47, "conf": 1})
    st = svc.apply_instruction({"op": "sound.clear"})
    assert st.sound is None
    assert svc._sound is None
