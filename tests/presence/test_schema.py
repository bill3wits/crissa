from crissa_presence.schema import (
    SCHEMA_ID,
    empty_room,
    PersonSlot,
    RoomPresence,
    epoch_to_iso,
    enrich_timestamps,
)


def test_empty_schema_id():
    r = empty_room()
    d = r.to_dict()
    assert d["schema"] == SCHEMA_ID
    assert d["headcount"] == 0
    assert d["people"] == []
    assert d.get("ts_local")  # human-readable alongside epoch ts


def test_person_slot():
    r = RoomPresence(
        camera_open=True,
        camera_blind=False,
        frame_age_sec=0.1,
        headcount=1,
        count_certain=True,
        people=[PersonSlot(slot_id="s0", name="user", certain=True, gender="M")],
        unidentified=0,
        camera_mode="holding",
        count_trust="fact",
        door_names=["user"],
    )
    d = r.to_dict()
    assert d["people"][0]["name"] == "user"
    assert d["people"][0]["gender"] == "M"
    assert d["camera_mode"] == "holding"
    assert d["count_trust"] == "fact"
    assert d["door_names"] == ["user"]
    assert d.get("room_busy") is False
    assert d.get("rev") == 0
    assert "writers" in d


def test_epoch_to_iso_local():
    # 2026-07-23 04:18:27 local on this host (from forensic samples)
    s = epoch_to_iso(1784794707.9568315)
    assert s is not None
    assert s.startswith("2026-07-23")
    # stable wall format YYYY-MM-DD HH:MM:SS
    assert len(s) == 19
    assert s[10] == " "


def test_enrich_nested_capture_ts():
    d = enrich_timestamps(
        {"last_event": {"kind": "door_hold", "capture_ts": 1784794628.9989583}}
    )
    assert "capture_ts_local" in d["last_event"]
    assert d["last_event"]["capture_ts_local"].startswith("2026-07-23")


def test_format_last_seen_and_ago():
    from crissa_presence.schema import format_last_seen, epoch_to_ago
    import time

    now = time.time()
    f = format_last_seen(now - 45, now=now)
    assert f["last_seen_local"]
    assert f["last_seen_ago"] == "45s ago"
    assert epoch_to_ago(now - 125, now=now) == "2m ago"
    assert epoch_to_ago(now, now=now) == "just now"

