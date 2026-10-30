"""Motion trigger opens an event row; FaceID and Argus attach. No field race."""
from __future__ import annotations

from crissa_presence.oracle_event import (
    SPEAKER_EID,
    close_event,
    event_id_from_ts,
    get_event,
    list_events,
    mint_scratch,
    open_event,
    open_visits,
    record_leave,
    rekey_event,
    stamp_argus,
    stamp_faceid,
    stamp_person,
    stamp_speaker_face,
)


def setup_function() -> None:
    from crissa_presence import oracle_event as m

    m._EVENTS.clear()
    m._ALIASES.clear()


def test_event_id_is_trigger_ms():
    assert event_id_from_ts(1786796365.772) == "ev_1786796365772"


def test_open_then_parallel_stamps():
    ts = 1786796365.772
    row = open_event(ts)
    eid = row["event_id"]
    assert eid == "ev_1786796365772"
    assert get_event(eid)["event"] is None
    assert get_event(eid)["person_id"] is None
    assert get_event(eid)["counted"] is False

    b = stamp_faceid(eid, name="user", sim=0.71, face_px=[120, 160])
    assert b["ok"] is True
    a = stamp_argus(
        eid, event="enter", clothes="black long-sleeve", hair="short", sex="M"
    )
    assert a["ok"] is True
    got = get_event(eid)
    assert got["person_id"] == "user"
    assert got["short_id"] == "Us"   # generic initials: the household glyph map does not ship
    assert got["event"] == "enter"
    assert got["clothes"].startswith("black")
    assert got["faceid"]["sim"] == 0.71
    assert got["argus"]["kind"] == "enter"


def test_stamp_without_open_fails():
    out = stamp_argus("ev_1", event="exit")
    assert out["ok"] is False
    assert out["reason"] == "no_event"


def test_scratch_on_null_faceid():
    open_event(100.0)
    eid = event_id_from_ts(100.0)
    mint_scratch(eid)
    got = get_event(eid)
    assert got["scratch"] is True
    assert got["short_id"] == "S1"
    close_event(eid, now=200.0)
    open_event(300.0)
    eid2 = event_id_from_ts(300.0)
    mint_scratch(eid2)
    assert get_event(eid2)["short_id"] == "S1"


def test_list_events_survives_close():
    open_event(10.0)
    eid = event_id_from_ts(10.0)
    stamp_faceid(eid, name="wanda", sim=0.66)
    close_event(eid, now=20.0)
    rows = list_events()
    assert len(rows) == 1
    assert rows[0]["exit_ts"] == 20.0
    assert rows[0]["person_id"] == "wanda"


def test_rekey_trigger_to_pack_id():
    ts = 1786797088.443
    open_event(ts)
    trig = event_id_from_ts(ts)
    pack = "ev_1786797082220"
    out = rekey_event(trig, pack)
    assert out["ok"] is True
    assert get_event(pack)["event_id"] == pack
    assert get_event(trig)["event_id"] == pack
    stamp_faceid(trig, name="user", sim=0.7)
    assert get_event(pack)["person_id"] == "user"


def test_open_does_not_count():
    from crissa_presence.door_watch import DoorInventory
    from crissa_presence.oracle_visit import apply_visit

    inv = DoorInventory()
    open_event(50.0)
    assert inv.headcount() == 0
    visits: dict = {}
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="sighting",
        person_id="s1",
        event_id=event_id_from_ts(50.0),
        now=50.0,
    )
    assert out["event"] == "sighting"
    assert inv.headcount() == 0
    assert get_event(event_id_from_ts(50.0))["counted"] is False


def test_onset_survives_the_rekey_to_the_pack_id():
    """The row opens on the door tick and is rekeyed to the pack. The onset is
    set after that, and must be reachable from either id — a joiner holding the
    trigger-time id and one holding the pack id have to land on one frame."""
    from crissa_presence.oracle_event import rekey_event, set_onset

    trig = 1786800000.0
    open_event(trig, pack_id=event_id_from_ts(trig))
    rekey_event(event_id_from_ts(trig), "ev_1786799993800")  # 6.2s pre-roll
    set_onset("ev_1786799993800", seq=4412, ts=1786799993.8)

    assert get_event("ev_1786799993800")["onset_seq"] == 4412
    assert get_event(event_id_from_ts(trig))["onset_seq"] == 4412


def test_onset_is_write_once():
    """A measurement of the clip, not an opinion about it. A second writer that
    moved it would hand Argus and the facefinder different frames."""
    from crissa_presence.oracle_event import set_onset

    open_event(60.0)
    eid = event_id_from_ts(60.0)
    set_onset(eid, seq=100, ts=60.0)
    out = set_onset(eid, seq=999)
    assert out["ok"] is True and out["reason"] == "already_set"
    assert get_event(eid)["onset_seq"] == 100


def test_onset_needs_a_row_and_a_seq():
    from crissa_presence.oracle_event import set_onset

    open_event(70.0)
    assert set_onset(event_id_from_ts(70.0), seq=None)["reason"] == "no_seq"
    assert set_onset("ev_nope", seq=1)["reason"] == "no_event"


def test_record_leave_closes_the_enter_card():
    """Who left is the enter card; that card must leave the open set."""
    open_event(10.0, pack_id="ev_in")
    stamp_person("ev_in", "s1")
    stamp_argus(
        "ev_in",
        event="enter",
        clothes="light gray cardigan",
        hair="long wavy brown",
        sex="F",
    )
    open_event(20.0, pack_id="ev_out")
    out = record_leave(
        "ev_out",
        "s1",
        now=20.0,
        leave_match={"method": "clothes_hair", "person_id": "s1", "score": 0.9},
    )
    assert out["ok"] is True
    enter = get_event("ev_in")
    leave = get_event("ev_out")
    assert enter["exit_ts"] == 20.0
    assert enter["clothes"] == "light gray cardigan"
    assert leave["person_id"] == "s1"
    assert leave["event"] == "exit"
    assert leave["leave_match"]["method"] == "clothes_hair"
    assert leave["exit_ts"] == 20.0
    assert not any(r.get("person_id") == "s1" for r in open_visits())


def test_speaker_face_is_not_a_visit():
    """FaceID speaker init is on the ring, counted=False, not occupancy mill."""
    out = stamp_speaker_face(name="user", sim=0.81, sex="M", source="f2f")
    assert out["ok"] is True
    row = get_event(SPEAKER_EID)
    assert row["event"] == "speaker"
    assert row["person_id"] == "user"
    assert row["counted"] is False
    assert row["short_id"] == "Us"   # generic initials: the household glyph map does not ship
    assert any(r.get("event_id") == SPEAKER_EID for r in list_events())
    assert not any(r.get("event_id") == SPEAKER_EID for r in open_visits())
