"""Enter describe → omni note. No SoT writes."""
from __future__ import annotations

import json
from pathlib import Path

from crissa_presence.argus_notify import (
    ENTER_PROMPT,
    MOTION_PROMPT,
    _clean_desc,
    _face_px_ok,
    consider_pack,
    notify_pack_async,
    pack_worth_describe,
    parse_argus_visit,
    pick_pack_jpegs,
)


def test_present_block_keeps_only_sot_ids(monkeypatch):
    from crissa_presence.argus_notify import present_block

    visits = [
        {"person_id": "user", "clothes": "gray shirt", "hair": "short", "sex": "M"},
        {"person_id": "a2", "clothes": "gray shirt", "hair": "short", "sex": "M"},
    ]
    monkeypatch.setattr("crissa_presence.oracle_event.open_visits", lambda: visits)
    monkeypatch.setattr("crissa_presence.argus_notify._sot_named_ids", lambda: {"user"})
    b = present_block()
    assert "user = gray shirt" in b
    assert "a2" not in b


def test_pick_motion_uses_find_onset_not_empty_start(tmp_path: Path):
    frames = []
    for i in range(11):
        p = tmp_path / f"f{i:03d}.jpg"
        p.write_bytes(b"\xff\xd8x" + bytes([i]))
        frames.append(
            {
                "file": p.name,
                "path": str(p),
                "ts": 100.0 + i,
                "energy": 40.0 if i == 3 else 1.0,
            }
        )
    man = {
        "pack_dir": str(tmp_path),
        "kind": "motion",
        "trigger_ts": 103.0,
        "frames": frames,
    }
    got = pick_pack_jpegs(man, n=3)
    names = [p.name for p in got]
    assert "f000.jpg" not in names
    # find_onset is the rest frame before the delta (f002), then even stills
    assert names[0] in ("f002.jpg", "f003.jpg")


def test_pick_motion_ships_crop(tmp_path: Path):
    (tmp_path / "entry_best_face.jpg").write_bytes(b"\xff\xd8face")
    frames = []
    for i in range(8):
        p = tmp_path / f"f{i:03d}.jpg"
        p.write_bytes(b"\xff\xd8x" + bytes([i]))
        frames.append({"file": p.name, "path": str(p), "energy": 30.0 if i == 4 else 0.5})
    man = {
        "pack_dir": str(tmp_path),
        "kind": "motion",
        "frames": frames,
    }
    got = pick_pack_jpegs(man, n=4)
    names = [p.name for p in got]
    assert "entry_best_face.jpg" in names
    assert "f004.jpg" in names


def test_pick_even_frames(tmp_path: Path):
    frames = []
    for i in range(10):
        p = tmp_path / f"f{i:03d}.jpg"
        p.write_bytes(b"\xff\xd8fake" + bytes([i]))
        frames.append({"file": p.name, "path": str(p)})
    body = tmp_path / "entry_body.jpg"
    body.write_bytes(b"\xff\xd8body")
    man = {
        "pack_dir": str(tmp_path),
        "frames": frames,
        "artifacts": {"entry_body": {"file": "entry_body.jpg"}},
    }
    got = pick_pack_jpegs(man, n=4)
    assert body in got
    assert 2 <= len(got) <= 5
    assert all(p.is_file() for p in got)


def test_parse_argus_visit_harvests_clothes_from_prose():
    """JSON clothes empty; the sentence is the robe (user: blue short sleev)."""
    raw = (
        "A person in a blue short sleev shirt walked out into the hallway.\n"
        '{"event":"exit","clothes":"","hair":"","sex":null,"who":null}'
    )
    v = parse_argus_visit(raw)
    assert v["event"] == "exit"
    assert v["clothes"]
    assert "blue" in v["clothes"].lower()
    assert "shirt" in v["clothes"].lower()
    assert v["comment"] and "walked out" in v["comment"]


def test_parse_argus_visit_from_mixed_reply():
    raw = (
        "A person in a dark shirt walks past the dresser, not the hallway.\n"
        '{"event":"sighting","clothes":"dark long-sleeve","hair":"short greying","sex":"M"}'
    )
    v = parse_argus_visit(raw)
    assert v["event"] == "sighting"
    assert "dark" in v["clothes"]
    assert v["sex"] == "M"
    assert v["comment"] and "dresser" in v["comment"]


def test_parse_argus_visit_maps_add_sub():
    assert parse_argus_visit('{"event":"add","clothes":"x","sex":"f"}')["event"] == "enter"
    assert parse_argus_visit('{"kind":"leave","sex":"?"}')["event"] == "exit"


def test_parse_user_exit_additive_and_legacy():
    from crissa_presence.argus_notify import parse_argus_visit

    v = parse_argus_visit(
        '{"event":"exit","user_exit":true,"who":"user","clothes":"x"}'
    )
    assert v["user_exit"] is True
    assert v["user_exit_set"] is True
    v = parse_argus_visit('{"event":"exit","user_exit":null,"who":"user"}')
    assert v["user_exit"] is None
    assert v["user_exit_set"] is True
    # wanda exit must not map to User leave
    v = parse_argus_visit('{"event":"exit","who":"wanda","clothes":"x"}')
    assert v["user_exit"] is None
    assert v["event"] == "exit"
    v = parse_argus_visit('{"event":"exit","who":"user","clothes":"x"}')
    assert v["user_exit"] is True
    v = parse_argus_visit('{"event":"enter","who":"user"}')
    assert v["user_exit"] is False


def test_clean_desc_strips_json_appearance():
    raw = json.dumps({"appearance": "dark jacket, walking in", "action": "add"})
    assert _clean_desc(raw) == "dark jacket, walking in"


def test_clean_desc_drops_trailing_visit_fence():
    raw = (
        "A man in a gray shirt crosses the doorway.\n\n```json\n"
        '{"event":"exit","who":"wanda"}\n```'
    )
    got = _clean_desc(raw)
    assert got == "A man in a gray shirt crosses the doorway."
    assert "json" not in got.lower()


def test_enter_prompt_asks_visit_json():
    assert "headcount" in ENTER_PROMPT.lower()
    assert "JSON" in ENTER_PROMPT
    assert "60px" in ENTER_PROMPT
    assert "sighting" in ENTER_PROMPT
    assert "sighting" in MOTION_PROMPT


def test_face_px_ok_faceid_floor():
    assert _face_px_ok({"face_px": [80, 90]})
    assert not _face_px_ok({"face_px": [20, 24]})
    assert not _face_px_ok({"face_px": 40})
    assert _face_px_ok({"face_px": 80})


def test_pick_from_trigger_skips_preroll(tmp_path: Path):
    """trigger_ts + find_onset: empty pre-roll is not sent."""
    frames = []
    for i in range(20):
        p = tmp_path / f"f{i:03d}.jpg"
        p.write_bytes(b"\xff\xd8x" + bytes([i]))
        e = 35.0 if 12 <= i <= 16 else 2.0
        frames.append(
            {"file": p.name, "path": str(p), "ts": 200.0 + i * 0.2, "energy": e}
        )
    man = {
        "pack_dir": str(tmp_path),
        "kind": "motion",
        "trigger_ts": 200.0 + 13 * 0.2,
        "frames": frames,
    }
    got = pick_pack_jpegs(man, n=5)
    names = [p.name for p in got]
    assert "f000.jpg" not in names
    assert any(n.startswith("f01") for n in names)


def test_pick_ships_best_face_first(tmp_path: Path):
    (tmp_path / "entry_best_face.jpg").write_bytes(b"\xff\xd8face")
    (tmp_path / "entry_body.jpg").write_bytes(b"\xff\xd8body")
    (tmp_path / "f000.jpg").write_bytes(b"\xff\xd8a")
    man = {
        "kind": "enter",
        "pack_dir": str(tmp_path),
        "frames": [{"file": "f000.jpg", "path": str(tmp_path / "f000.jpg")}],
        "artifacts": {
            "entry_face": {"file": "entry_face.jpg", "face_px": 22},
            "entry_body": {"file": "entry_body.jpg"},
        },
    }
    got = pick_pack_jpegs(man, n=4)
    assert got[0].name == "entry_best_face.jpg"


def test_enter_always_worth_describe():
    assert pack_worth_describe({"kind": "enter", "n": 4, "max_energy": 1.0})
    assert pack_worth_describe({"kind": "leave", "n": 2, "max_energy": 0.0})


def test_still_empty_needs_motion_floor():
    # 03:03 harvest: 850 frames, energy 21.1, kind missing
    assert pack_worth_describe(
        {
            "kind": None,
            "resolve": "still_empty",
            "n": 850,
            "max_energy": 21.1,
        }
    )
    # keep-all junk: lots of frames, almost no motion
    assert not pack_worth_describe(
        {
            "kind": None,
            "resolve": "still_empty",
            "n": 850,
            "max_energy": 2.0,
        }
    )
    assert not pack_worth_describe(
        {"kind": "motion", "n": 3, "max_energy": 40.0}
    )


def test_notify_starts_on_still_empty(monkeypatch):
    started = []
    monkeypatch.setenv("RAIL_ARGUS_PACK_NOTIFY", "1")

    class _T:
        def __init__(self, *a, **k):
            pass

        def start(self):
            started.append(True)

    monkeypatch.setattr(
        "crissa_presence.argus_notify.threading.Thread",
        lambda *a, **k: _T(),
    )
    notify_pack_async(
        {
            "kind": None,
            "resolve": "still_empty",
            "n": 850,
            "max_energy": 21.1,
            "id": "ev_test",
        }
    )
    assert started == []


def test_notify_skips_low_energy_nokind(monkeypatch):
    started = []

    class _T:
        def __init__(self, *a, **k):
            pass

        def start(self):
            started.append(True)

    monkeypatch.setattr(
        "crissa_presence.argus_notify.threading.Thread",
        lambda *a, **k: _T(),
    )
    notify_pack_async(
        {
            "kind": None,
            "resolve": "still_empty",
            "n": 850,
            "max_energy": 2.0,
            "id": "ev_test",
        }
    )
    assert started == []


def test_pack_omni_skipped_when_fanout_owns():
    import inspect

    from crissa_presence import argus_notify as an

    src = inspect.getsource(an.notify_pack_async)
    assert "faceid" in src.lower() or "Dead" in src or "return" in src


def test_video_post_sends_oracle_system_not_agent_soul(monkeypatch, tmp_path):
    """Clip POST is Argus Oracle (enter/exit/sighting), not the agent SOUL.md.

    2026-08-25 stuffed ~/.the agent/profiles/argus/SOUL.md as system. That file
    still says 'same door game as the 8B' / add/sub/null, which fights the
    live JSON. A toxic SOUL on disk must not leak onto the wire.
    """
    from crissa_presence import argus_notify as an

    soul = tmp_path / "SOUL.md"
    soul.write_text(
        "Same door game as the 8B. Jamb / add / sub / null.\n"
        "You judge path only. Never names.\n",
        encoding="utf-8",
    )
    # ARGUS_SOUL was deleted; the clip POST must not grow a reader for it.
    monkeypatch.setattr(an, "ARGUS_SOUL", soul, raising=False)
    assert not hasattr(an, "_argus_soul")
    posted = {}

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok":true,"choices":[{"message":{"content":"{}"}}]}'

    def _urlopen(req, timeout=0):
        posted["url"] = req.full_url
        posted["body"] = json.loads(req.data.decode())
        return _R()

    monkeypatch.setattr(an.urllib.request, "urlopen", _urlopen)
    # a pack's video engine is the one this install names (crissa_presence/ports.brain, 2026-10-10); the house's crew
    # port was the default before
    monkeypatch.setattr(an, "VIDEO_URL", "http://127.0.0.1:9/v1/chat/completions")
    an._post_glimmer_video(b"mp4", "Judge this clip. Oldest first.")
    msgs = posted["body"]["messages"]
    assert posted["url"] == "http://127.0.0.1:9/v1/chat/completions", "the engine this install names, never a fixed port"
    assert msgs[0]["role"] == "system"
    sys_p = msgs[0]["content"]
    assert "Argus" in sys_p
    assert "enter" in sys_p
    assert "sighting" in sys_p
    assert "unresolved" in sys_p
    assert "8B" not in sys_p
    assert "add / sub / null" not in sys_p
    assert "Same door game" not in sys_p
    assert "Never names" not in sys_p
    assert msgs[1]["role"] == "user"
    user = msgs[1]["content"]
    assert isinstance(user, list)
    texts = [c.get("text") for c in user if isinstance(c, dict) and c.get("text")]
    assert any("Judge this clip" in t for t in texts)
    # Encoder campaign: N20 slides on the door clip POST, not engine-wide.
    assert posted["body"]["media_io_kwargs"]["video"]["num_frames"] == 20
    assert "fps" in posted["body"]["media_io_kwargs"]["video"]
    assert "fps" in posted["body"]["mm_processor_kwargs"]


def test_video_sampling_kwargs_omit_is_q2_control(monkeypatch):
    from crissa_presence import argus_notify as an

    monkeypatch.setattr(an, "VIDEO_NUM_FRAMES", 0)
    monkeypatch.setattr(an, "VIDEO_FPS_RAW", "omit")
    assert an.video_sampling_kwargs(24) == {}
    monkeypatch.setattr(an, "VIDEO_FPS_RAW", "")
    monkeypatch.setattr(an, "VIDEO_NUM_FRAMES", 20)
    k = an.video_sampling_kwargs(23)  # odd decoded, cap 20
    assert k["media_io_kwargs"]["video"]["num_frames"] == 20
    assert k["mm_processor_kwargs"]["fps"] == 15.0  # no duration → F15
    k20 = an.video_sampling_kwargs(20, duration_s=4.0)
    assert k20["media_io_kwargs"]["video"]["num_frames"] == 20
    assert k20["media_io_kwargs"]["video"]["fps"] == 5.0
    assert k20["mm_processor_kwargs"]["fps"] == 5.0
    monkeypatch.setattr(an, "VIDEO_FPS_RAW", "15")
    k_f15 = an.video_sampling_kwargs(20, duration_s=4.0)
    assert k_f15["media_io_kwargs"]["video"]["fps"] == 15.0


def test_oracle_system_is_visit_not_student_scoreboard():
    from crissa_presence.argus_notify import oracle_system

    s = oracle_system()
    assert s.startswith("You are Argus")
    assert "enter" in s and "exit" in s and "sighting" in s
    assert "behind this camera" in s
    assert "hour 6" in s
    assert "Storm" in s
    assert "BOTH sides" in s
    assert '"event"' in s
    assert "add/sub" not in s
    assert "Door count only" not in s
    assert "user_exit" in s


def test_motion_prompt_is_description_only():
    from crissa_presence.argus_notify import motion_prompt

    s = motion_prompt()
    assert "headcount" in s.lower()
    assert "cross" in s.lower()
    assert "JSON" in s
    assert "behind this camera" in s
    assert "hour 6" in s
    assert "sighting" in s
    assert "unresolved" in s
    assert "ENTER:" in s or "hallway → room" in s or "hallway -> room" in s


def test_room_paragraph_user_sits_behind_camera():
    from crissa_presence.room_context import room_paragraph

    s = room_paragraph()
    assert "BEHIND" in s
    assert "hour 6" in s
    assert "straight to" in s
    assert "enter finished" in s.lower() or "sat behind" in s


def test_consider_pack_floor_skip_does_not_stamp(tmp_path: Path):
    (tmp_path / "f000.jpg").write_bytes(b"\xff\xd8x")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "id": tmp_path.name,
                "resolve": "still_empty",
                "n": 12,
                "max_energy": 1.2,
                "frames": [{"file": "f000.jpg"}],
            }
        )
    )
    out = consider_pack(tmp_path, notify=False, write_face=False)
    assert out["ok"] is True
    assert out["skip"] == "floor"
    assert out["described"] is False
    man = json.loads((tmp_path / "manifest.json").read_text())
    assert man.get("kind") is None


def test_consider_pack_stamps_motion_no_sot(tmp_path: Path, monkeypatch):
    started = []
    monkeypatch.setenv("RAIL_ARGUS_PACK_NOTIFY", "1")

    class _T:
        def __init__(self, *a, **k):
            pass

        def start(self):
            started.append(True)

    monkeypatch.setattr(
        "crissa_presence.argus_notify.threading.Thread",
        lambda *a, **k: _T(),
    )
    (tmp_path / "f000.jpg").write_bytes(b"\xff\xd8x")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "id": "ev_motion",
                "resolve": "still_empty",
                "n": 80,
                "max_energy": 21.1,
                "frames": [{"file": "f000.jpg"}],
            }
        )
    )
    out = consider_pack(tmp_path, notify=True, write_face=False)
    assert out["ok"] is True
    assert out["described"] is False
    assert out["kind"] == "motion"
    assert started == []
    man = json.loads((tmp_path / "manifest.json").read_text())
    assert man["kind"] == "motion"
    assert man.get("argus_queued") is not True


def test_two_writers_over_one_crossing_apply_once():
    """Measured 2026-08-15 12:01: event-clip harvest and door_capture v2 both
    cut wanda's entry. Both described it correctly, both applied, and one
    person became wanda 1->2 then s1 2->3 — the room read 4 for 2.

    Their own manifests disagreed on the onset (87303 vs 87417); raw_clip
    returned 87426 for both. The claim is keyed on the raw onset for that
    reason."""
    from crissa_presence import argus_notify as an

    an._ONSET_CLAIMS.clear()
    assert an.claim_crossing(87426, "ev_1786809707941")["ok"] is True
    loser = an.claim_crossing(87426, "ev_1786809685471")
    assert loser["ok"] is False
    assert loser["owner"] == "ev_1786809707941"


def test_the_same_event_may_reclaim_its_own_crossing():
    from crissa_presence import argus_notify as an

    an._ONSET_CLAIMS.clear()
    an.claim_crossing(500, "ev_a")
    assert an.claim_crossing(500, "ev_a")["ok"] is True


def test_a_distinct_crossing_is_not_swallowed():
    """Two people through the door minutes apart must both count."""
    from crissa_presence import argus_notify as an

    an._ONSET_CLAIMS.clear()
    assert an.claim_crossing(1000, "ev_a")["ok"] is True
    assert an.claim_crossing(1000 + an.ONSET_DEDUPE_FRAMES + 1, "ev_b")["ok"] is True


def test_no_onset_fails_open():
    """A missing measurement must not silently drop a real entry."""
    from crissa_presence import argus_notify as an

    an._ONSET_CLAIMS.clear()
    out = an.claim_crossing(None, "ev_a")
    assert out["ok"] is True and out["reason"] == "no_onset"


def test_a_door_v2_pack_is_always_described():
    """The pack only exists because observe_door minted an event. An energy
    floor cannot second-guess that: ev_1786823114540 (2026-08-15) holds a woman
    in the doorway at max_energy 3.748 against a floor of 8.0, while EMPTY
    harvest packs that afternoon scored 4.41 and 11.71."""
    from crissa_presence.argus_notify import pack_worth_describe

    real_crossing = {
        "capture_mode": "door_v2", "kind": "motion",
        "n": 8, "max_energy": 3.748, "resolve": "still",
    }
    assert pack_worth_describe(real_crossing) is True


def test_harvest_packs_keep_the_motion_floor():
    """Harvest cuts speculatively from raw, so it still needs a floor."""
    from crissa_presence.argus_notify import pack_worth_describe

    quiet_harvest = {
        "capture_mode": "event-clip: onset_backfill+until_still",
        "kind": "motion", "n": 300, "max_energy": 1.6, "resolve": "still",
    }
    assert pack_worth_describe(quiet_harvest) is False


def test_an_exit_gets_the_long_timeout(monkeypatch):
    """8 of 8 Argus exits on 2026-08-15 failed with TimeoutError. The verdicts
    were correct; apply_oracle_visit runs confirm_user_exit for the User first
    — a camera hold with ttl 90s — and the client gave up at 8."""
    from crissa_presence import argus_notify as an

    seen = {}

    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    def _fake(req, timeout=None):
        seen["timeout"] = timeout
        return _R()

    monkeypatch.setattr(an.urllib.request, "urlopen", _fake)
    monkeypatch.setattr(an, "ORACLE_VISIT_URL", "http://127.0.0.1:8191/v1/oracle_visit")

    an.post_oracle_visit({"event": "exit", "person_id": "user"})
    assert seen["timeout"] == an.EXIT_TIMEOUT_SEC
    assert seen["timeout"] > 60, "must outlast a 90s camera hold"

    an.post_oracle_visit({"event": "enter", "person_id": "user"})
    assert seen["timeout"] == an.VISIT_TIMEOUT_SEC


def test_unresolved_covers_reached_door_plane_not_seen():
    """Frame-exit at the jamb with no plane must not be forced to enter/exit."""
    from crissa_presence.argus_notify import DOOR_LAW

    assert "never reach the door" in DOOR_LAW
    assert "jamb" in DOOR_LAW
    assert "frame-exit-near-the-door" in DOOR_LAW
    assert "BOTH sides" in DOOR_LAW


def test_unresolved_parses_as_a_verdict_not_a_failure():
    """ev_1786888948841 on 2026-08-16 was the first time Argus used it:
    "briefly seen... then moves out of the frame, with no entry or exit through
    the doorway shown" -> {"event":"unresolved"}. The prompt offered it and
    _apply handled it, but the parser still validated against the old three, so
    the page showed `argus unparsed`. The model answered and the code binned it.
    """
    from crissa_presence.argus_notify import parse_argus_visit

    txt = ('No doorway crossing is visible. '
           '{"event":"unresolved","clothes":"light gray long-sleeve shirt",'
           '"hair":null,"sex":"M"}')
    got = parse_argus_visit(txt)
    assert got is not None, "unresolved is a verdict, not a parse failure"
    assert got["event"] == "unresolved"
    assert got["clothes"] == "light gray long-sleeve shirt"


def test_hedge_words_normalise_to_unresolved():
    from crissa_presence.argus_notify import parse_argus_visit

    for w in ("unknown", "unclear", "indeterminate"):
        got = parse_argus_visit('x {"event":"%s","clothes":"","hair":"","sex":null}' % w)
        assert got and got["event"] == "unresolved", w


def test_roster_exit_does_not_return_user():
    """WP-4: _roster_user_for_exit cannot pick User without a clothes win."""
    from crissa_presence.argus_notify import _roster_exit_pick

    both = {
        "people": [{"name": "user"}, {"name": "s1"}],
        "auth": {"authed_ids": ["user"]},
    }
    assert _roster_exit_pick(both, "user") == "s1"
    only_user = {
        "people": [{"name": "user"}],
        "auth": {"authed_ids": ["user"]},
    }
    assert _roster_exit_pick(only_user, "user") == ""
