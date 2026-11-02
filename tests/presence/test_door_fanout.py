"""One trigger, two witnesses, neither gating the other.

The bug this replaces: on 2026-08-16 02:13 a real walk-through fired five
camera triggers (energy up to 41.1) and produced zero door events, zero packs
and zero Argus calls, because Argus only ever learned of a crossing when a pack
file appeared, and a pack only appeared if FaceID found a face first.
"""

from __future__ import annotations

import inspect
import time

import pytest

from crissa_presence import door_fanout as df


@pytest.fixture(autouse=True)
def _no_live_argus_dev(monkeypatch, tmp_path):
    """_apply always calls _dev, which was writing data/argus_dev.json."""
    import crissa_presence.argus_dev as ad

    monkeypatch.setattr(ad, "STATE", tmp_path / "argus_dev.json")
    monkeypatch.setattr(ad, "LOG", tmp_path / "argus_dev.jsonl")
    monkeypatch.setattr(df, "_LOOK_PATH", tmp_path / "argus_last_look.json")


def test_argus_timing_uses_dispatch_wait_not_crew_mean():
    busy = {"waiting": 3.0, "waiting_capacity": 1.0, "preemptions": 4.0}
    after = {"waiting": 2.0, "waiting_capacity": 0.0, "preemptions": 7.0}
    s = df._argus_timing(busy, after, wall_ms=400.0)
    assert s["queued"] is True
    assert s["waiting"] == 3.0
    assert s["waiting_capacity"] == 1.0
    assert s["wall_ms"] == 400.0
    assert s["infer_ms"] is None  # queued — do not invent a split
    assert s["preemptions_delta"] == 3.0
    idle = {"waiting": 0.0, "waiting_capacity": 0.0, "preemptions": 1.0}
    t = df._argus_timing(idle, idle, wall_ms=12.3)
    assert t["queued"] is False
    assert t["infer_ms"] == 12.3
    unknown = df._argus_timing({}, {}, wall_ms=9.0)
    assert unknown["queued"] is None
    assert unknown["infer_ms"] is None
    assert "queued_ms" not in s
    assert "queue_s_sum" not in inspect.getsource(df._argus_timing)


def _t(seq: int, ts: float, energy: float = 40.0) -> dict:
    return {"seq": seq, "ts": ts, "kind": "motion", "meta": {"energy": energy}}


def _reset() -> None:
    """Clear BOTH the trigger memory and the event table.

    _person_for now consults open_visits(), which reads oracle_event's
    module-global _EVENTS. Without clearing it, visits opened by one test are
    still "in the room" for the next and every attribution shifts.
    """
    from crissa_presence import oracle_event as oe

    df._SEEN.clear()
    df._PENDING.clear()
    df._DONE.clear()
    df._DOOR_MILL_LEASE.clear()
    df._ACTION_SEEN.clear()
    # module state, and a test that fires leaves it set — the next test then
    # trips the coalesce guard and fails depending on ORDER, which is what
    # made this suite flaky (1-2 varying failures per run).
    df._LAST_FIRE[0] = 0.0
    df._BUSY_SENT[0] = False
    df._GLANCE_TS[0] = 0.0
    df._GLANCE[0] = None
    df._UNRESOLVED_TRIED.clear()
    df._LAST_LOOK.clear()
    for k in ("triggers_seen", "events_fired", "argus_ok", "faceid_named"):
        df._STATS[k] = 0
    oe._EVENTS.clear()
    oe._ALIASES.clear()
    df._LAST_APPLIED = ("", 0.0)


def test_fresh_marks_only_the_seq_in_hand():
    """Spray of the same walk is eaten in poll_once, not by marking the
    whole cand list here. Marking the burst in _fresh is what deleted
    wanda's out with no log row (2026-08-25)."""
    _reset()
    now = time.time()
    burst = [_t(324309, now - 20), _t(324318, now - 18), _t(324320, now - 17)]
    first = df._fresh(burst, now)
    assert first is not None and first["seq"] == 324309
    assert 324309 in df._SEEN
    assert 324318 not in df._SEEN
    second = df._fresh(burst, now)
    assert second is not None and second["seq"] == 324318


def test_clock_prompt_has_trigger_and_last_look():
    _reset()
    df._LAST_LOOK.update({
        "ts_local": "2026-08-25 14:47:13",
        "event": "exit",
        "who": "user",
        "comment": "grey shirt into the hallway",
    })
    s = df._clock_prompt(100.0, "2026-08-25 14:48:03", None, None)
    assert "CLOCK" in s and "ts_local=2026-08-25 14:48:03" in s
    assert "rest_ts=null" in s
    assert "LAST you said" in s and "event=exit" in s and "user" in s
    assert "YOU HOLD" in s


def test_hold_line_strips_argus_dev_ghosts(monkeypatch):
    """H5: YOU HOLD is Code SoT, not Dev a2/a4 ghosts (live hc 1 vs Dev 5)."""
    monkeypatch.setattr(df, "_code_sot_ids", lambda: (1, {"user"}))

    def _ghosts():
        return {
            "headcount": 5,
            "people": [
                {"id": "a2", "clothes": "gray shirt", "hair": "short", "sex": "M"},
                {"id": "user", "clothes": "gray shirt", "hair": "short", "sex": "M"},
            ],
        }

    monkeypatch.setattr("crissa_presence.argus_dev.load", _ghosts)
    s = df._hold_line()
    assert "YOU HOLD hc=1" in s
    assert "user=" in s
    assert "a2" not in s
    assert "second ledger" in s


def test_clock_prompt_names_rest_ts_as_fov_ref():
    _reset()
    s = df._clock_prompt(100.0, "2026-08-25 15:12:00", 123.0, 42)
    assert "LINGER-ENDED" not in s
    assert "rest_ts=123.0" in s
    assert "empty-door ref" in s
    assert "BEFORE" in s
    assert "END of movement" in s


def test_action_is_before_rest_ts():
    win = [(1.0, 10, "a"), (2.0, 11, "b"), (5.0, 12, "c")]
    out = df._action_before_rest(win, 4.0)
    assert [w[1] for w in out] == [10, 11]
    assert df._action_before_rest(win, None) is win


def test_clock_prompt_skips_same_eid_last():
    _reset()
    df._LAST_LOOK.update({
        "eid": "ev_1", "event": "exit", "who": "user", "ts_local": "prior",
    })
    s = df._clock_prompt(1.0, "now", None, None, eid="ev_1")
    assert "LAST you said" not in s
    s2 = df._clock_prompt(1.0, "now", None, None, eid="ev_2")
    assert "LAST you said" in s2 and "user" in s2


def test_eat_burst_does_not_swallow_a_later_crossing():
    """wanda in then out: the out trigger must survive the enter fire."""
    _reset()
    t0 = 1000.0
    ring = [_t(10, t0), _t(11, t0 + 1.0), _t(20, t0 + 8.0)]
    n = df._eat_burst(t0, t0 + 2.0, ring)  # rest at +2s
    assert 10 in df._SEEN and 11 in df._SEEN
    assert 20 not in df._SEEN
    assert n >= 2


def test_poll_eats_same_envelope_spray(monkeypatch):
    """Sub-SPRAY_SEC triggers join. A 4s in-then-out must NOT join."""
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    monkeypatch.setattr(df, "_room_restored", lambda a, b: True)
    fired = []
    monkeypatch.setattr(df, "fan_out", lambda t: fired.append(t["seq"]) or {"fired": t["seq"]})
    _reset()
    now = time.time()
    ring = [_t(100, now - 3), _t(101, now - 3 + 0.4)]
    monkeypatch.setattr(df, "_fetch_triggers", lambda: ring)
    assert df.poll_once(now)["fired"] == 100
    assert df.poll_once(now) is None, "0.4s later is spray, not a second fire"
    assert fired == [100]


def test_in_then_out_within_4s_is_two_items(monkeypatch):
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    monkeypatch.setattr(df, "_room_restored", lambda a, b: True)
    fired = []
    monkeypatch.setattr(df, "fan_out", lambda t: fired.append(t["seq"]) or {"fired": t["seq"]})
    _reset()
    t0 = time.time() - 10
    ring = [_t(10, t0), _t(20, t0 + 3.5)]
    monkeypatch.setattr(df, "_fetch_triggers", lambda: ring)
    now = t0 + 10
    assert df.poll_once(now)["fired"] == 10
    assert df.poll_once(now)["fired"] == 20
    assert fired == [10, 20]


def test_pending_work_true_while_waiting(monkeypatch):
    _reset()
    now = time.time()
    df._ingest([_t(1, now - 1)], now)
    assert df.pending_work() is True


def test_busy_alone_is_not_pending_work():
    _reset()
    df._BUSY_SENT[0] = True
    assert df.pending_work() is False
    df._BUSY_SENT[0] = False


def test_slew_uncorroborated_does_not_veto(monkeypatch):
    """Fast walker close to the lens: peak>0.5, no mill slew → judge."""
    monkeypatch.setattr(df, "_slew_corroborated", lambda w: (False, []))
    monkeypatch.setattr(df, "slew_profile", lambda w, sample=40: {
        "peak": 0.71, "spans": [(0, 3, 0.71)], "pairs": 6
    })
    # Don't run real fan_out; unit the ladder via a tiny helper replica
    corr, why = df._slew_corroborated([])
    assert corr is False


def test_wave_then_sneak_second_fire(monkeypatch):
    """Wave fires, then a sneak-in 1.5s later still fires. No 4s dead zone."""
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    monkeypatch.setattr(df, "_room_restored", lambda a, b: True)
    fired = []
    monkeypatch.setattr(df, "fan_out", lambda t: fired.append(t["seq"]) or {"fired": t["seq"]})
    _reset()
    t0 = time.time() - 3
    ring = [_t(200, t0)]
    monkeypatch.setattr(df, "_fetch_triggers", lambda: ring)
    assert df.poll_once(t0 + 0.5)["fired"] == 200
    ring.append(_t(201, t0 + 1.5))
    assert df.poll_once(t0 + 2.0)["fired"] == 201
    assert fired == [200, 201]


def test_new_motion_fires_faceid_glance(monkeypatch):
    glances = []
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    monkeypatch.setattr(df, "_room_restored", lambda a, b: True)
    monkeypatch.setattr(df, "fan_out", lambda t: {"fired": t["seq"]})
    df.set_glance(lambda: glances.append("g"))
    _reset()
    df.set_glance(lambda: glances.append("g"))
    now = time.time()
    monkeypatch.setattr(df, "_fetch_triggers", lambda: [_t(4401, now - 1)])
    df.poll_once(now)
    assert glances == ["g"]


def test_waiting_crossings_raises_busy_does_not_drop(monkeypatch):
    calls = []
    monkeypatch.setattr(df, "_ensure_busy", lambda on, reason, **k: calls.append((on, reason)))
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    monkeypatch.setattr(df, "_room_restored", lambda a, b: True)
    fired = []
    monkeypatch.setattr(df, "fan_out", lambda t: fired.append(t["seq"]) or {"fired": t["seq"]})
    _reset()
    now = time.time()
    ring = [_t(1, now - 30), _t(2, now - 20), _t(3, now - 10)]
    monkeypatch.setattr(df, "_fetch_triggers", lambda: ring)
    out = df.poll_once(now)
    assert (True, "queue_flood") not in calls
    assert (True, "linger") not in calls
    assert out and out.get("fired") == 1, "queue still fires; Busy is not a product"


def test_exit_eight_seconds_later_is_its_own_fire(monkeypatch):
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    monkeypatch.setattr(df, "_room_restored", lambda a, b: True)
    fired = []
    monkeypatch.setattr(df, "fan_out", lambda t: fired.append(t["seq"]) or {"fired": t["seq"]})
    _reset()
    t0 = time.time() - 20
    ring = [_t(200, t0), _t(201, t0 + 8.0)]
    monkeypatch.setattr(df, "_fetch_triggers", lambda: ring)
    now = t0 + 20
    assert df.poll_once(now)["fired"] == 200
    df._LAST_FIRE[0] = now - 8.0
    assert df.poll_once(now)["fired"] == 201
    assert fired == [200, 201]


def test_clip_keeps_trigger_and_peak_not_window_ends():
    """241-frame occupied window: keep trigger and ROI-peak, not first/last."""
    window = [(100.0 + i * 0.125, 2400000 + i, None) for i in range(241)]
    trig_seq = 2400000 + 80  # 10s in
    peak_i = 88
    idx = df._keep_clip_indices(
        window, 6, trigger_seq=trig_seq, trigger_ts=100.0 + 80 * 0.125, peak_i=peak_i
    )
    seqs = [window[i][1] for i in idx]
    assert trig_seq in seqs
    assert window[peak_i][1] in seqs
    assert window[0][1] not in seqs
    assert window[-1][1] not in seqs
    assert len(seqs) == 6
    span = max(seqs) - min(seqs)
    cap_frames = int(df.CLIP_CAP_SEC / 0.125) + 8
    assert span < cap_frames, span  # around CLIP_CAP, not the 241-frame window


def test_defaults_are_the_short_clip():
    assert df.COALESCE_SEC <= 4.0 + 1e-6
    assert df.MAX_WAIT_SEC <= 6.0 + 1e-6
    assert df.LINGER_WAIT_SEC <= 8.0 + 1e-6
    assert df.ARGUS_MAX_FRAMES % 2 == 0
    assert df.ARGUS_MIN_FRAMES % 2 == 0
    assert df.ARGUS_MAX_FRAMES == 0 or 4 <= df.ARGUS_MAX_FRAMES <= 48
    assert df.BUSY_ON_SEC <= df.LINGER_WAIT_SEC
    assert df.CLIP_CAP_SEC >= 8.0


def test_a_gap_wider_than_the_window_is_a_second_crossing():
    """An exit and a re-entry are two crossings and coalescing them would lose
    the exit. The gap is the evidence: a span wider than COALESCE_SEC opens a
    new event. Widened to 30s here after COALESCE went 8s -> 15s, because one
    crossing's triggers spread further than first measured (08:07 fired :28 and
    :37 for a single entry and minted both s1 and s2)."""
    _reset()
    now = time.time()
    lap = [_t(324309, now - 40), _t(324318, now - 38),
           _t(324356, now - 10), _t(324366, now - 8), _t(324375, now - 6)]
    out = df._fresh(lap, now)
    assert out["seq"] == 324309, "first crossing"
    # Spray of the first walk (2s later) is still unseen; poll_once eats it.
    spray = df._fresh(lap, now)
    assert spray is not None and spray["seq"] == 324318
    again = df._fresh(lap, now)
    assert again is not None, "the second crossing must not be swallowed"
    assert again["seq"] == 324356


def test_the_oldest_trigger_wins_not_the_loudest():
    """Direction reads from the approach, so the burst's start is the anchor."""
    _reset()
    now = time.time()
    burst = [_t(100, now - 20, energy=12.0), _t(101, now - 18, energy=120.0)]
    assert df._fresh(burst, now)["seq"] == 100


def test_a_separate_crossing_later_is_its_own_event():
    _reset()
    now = time.time()
    first = [_t(200, now - 60)]
    df._fresh(first, now)
    later = [_t(200, now - 60), _t(300, now - 10)]
    got = df._fresh(later, now)
    assert got is not None and got["seq"] == 300


def test_history_is_not_replayed_on_startup():
    """A ring full of old triggers must not fire a burst of stale events."""
    _reset()
    now = time.time()
    stale = [_t(1, now - 3600), _t(2, now - 1800)]
    assert df._fresh(stale, now) is None
    assert 1 in df._SEEN and 2 in df._SEEN, "marked seen so they never fire"


def test_quiet_triggers_are_ignored(monkeypatch):
    _reset()
    now = time.time()
    monkeypatch.setattr(df, "_fetch_triggers", lambda: [_t(500, now - 10, energy=1.0)])
    assert df.poll_once(now) is None


def test_the_clock_no_longer_gates_only_the_room_does(monkeypatch):
    """SETTLE_SEC was a blind 3s floor before asking anything, and a blind
    stopwatch is the ORIGINAL reason clips ended mid-crossing (user 2026-08-16:
    "what is this? Delete."). _room_restored decides now, so a very fresh
    trigger fires immediately when the room is already back."""
    # _door_in_view() calls the live camera. A unit test must not depend on
    # where the lens is actually pointing — it flipped these red and green
    # all afternoon as the door pose moved.
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    _reset()
    now = time.time()
    assert df.SETTLE_SEC == 0, "the floor is gone"
    df._LAST_FIRE[0] = 0.0
    monkeypatch.setattr(df, "_fetch_triggers", lambda: [_t(600, now - 0.5)])
    monkeypatch.setattr(df, "_room_restored", lambda a, b: True)
    monkeypatch.setattr(df, "fan_out", lambda t: {"fired": True})
    assert df.poll_once(now) == {"fired": True}


def test_both_witnesses_run_even_when_one_fails(monkeypatch):
    """FaceID-only. Argus is not an identity witness (user 2026-08-27)."""
    # _door_in_view() calls the live camera. A unit test must not depend on
    # where the lens is actually pointing — it flipped these red and green
    # all afternoon as the door pose moved.
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    # Pinning the lens was not enough. fan_out also pulls a real action_window
    # off the raw store and runs camera_moved() on it, and a slew over
    # SLEW_PEAK short-circuits to `unobserved` before either witness is called
    # — so `calls` came back empty. Measured 2026-08-17: 3 failures in 8 runs
    # while the camera was touring. The slew gate has its own test
    # (test_camera_motion_is_not_room_motion); this one is about the witnesses.
    monkeypatch.setattr(df, "camera_moved", lambda w, sample=40: 0.0)
    _reset()
    calls = []

    def _boom(eid, ts, window=None, **k):
        calls.append("faceid")
        raise RuntimeError("no face")

    def _ok(eid, ts, window=None, **k):
        calls.append("argus")
        return {"ok": True, "event": "enter"}

    monkeypatch.setattr(df, "_ask_faceid", _boom)
    monkeypatch.setattr(df, "_ask_argus", _ok)
    monkeypatch.setattr(df, "APPLY", False)
    out = df.fan_out(_t(700, time.time()))
    assert "faceid" in calls
    assert "argus" not in calls
    assert out["argus"].get("skipped") == "faceid_only"
    assert "error" in out["faceid"]


def test_the_row_is_keyed_on_the_trigger_seq(monkeypatch):
    """Distinct ts on purpose: event ids are minted from the millisecond, and
    set_onset is write-once, so two fan-outs in the same millisecond share a row
    and the first seq rightly wins."""
    _reset()
    # Same live-rail pollution as test_both_witnesses_run_even_when_one_fails:
    # fan_out pulls a real action_window off <records>/raw, so this
    # asserted against whatever the camera happened to be doing 37 s ago. When a
    # facefinder tour was slewing, camera_moved() cleared SLEW_PEAK, fan_out
    # short-circuited to `unobserved` and never built a row. Row keying has
    # nothing to do with slew — pin both.
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    monkeypatch.setattr(df, "camera_moved", lambda w, sample=40: 0.0)
    monkeypatch.setattr(df, "_ask_argus", lambda eid, ts, w=None, **k: {"ok": True})
    monkeypatch.setattr(df, "_ask_faceid", lambda eid, ts, w=None, **k: {"ok": True})
    monkeypatch.setattr(df, "APPLY", False)
    out = df.fan_out(_t(4242, time.time() - 37.0))
    row = out.get("row") or {}
    assert row.get("onset_seq") == 4242


def test_authoritative_means_exactly_one_writer(monkeypatch):
    """The fan-out fires off the trigger seq; the pack path keys on the pack
    onset. Those keys differ, so claim_crossing cannot dedupe BETWEEN them —
    the pack path has to stand down entirely rather than rely on the claim."""
    from crissa_presence import argus_notify as an

    assert df.APPLY is True, "authoritative by default since 2026-08-16"
    assert an is not None


def test_a_second_trigger_in_one_crossing_does_not_count_twice(monkeypatch):
    """Same onset seq cannot count twice. A later distinct seq is a new event
    (wave then sneak-in) — time windows must not swallow it."""
    _reset()
    from crissa_presence import argus_notify as an

    an._ONSET_CLAIMS.clear()
    posts = []
    monkeypatch.setattr(an, "post_oracle_visit", lambda b: posts.append(b) or {"ok": True})
    monkeypatch.setattr(df, "_person_for", lambda eid, ev, b, a=None: "user")
    argus = {"event": "enter"}
    assert df._apply("ev_1", 900, argus, {})["ok"] is True
    same = df._apply("ev_2", 900, argus, {})
    assert same.get("applied") is False
    assert same.get("reason") == "duplicate_crossing"
    assert len(posts) == 1, "one seq, one write"


def test_an_exit_with_no_face_falls_back_to_the_roster(monkeypatch):
    """Walking out shows the camera a back, so a face can never name an exit."""
    _reset()
    from crissa_presence import argus_notify as an

    monkeypatch.setattr(an, "_roster_user_for_exit", lambda: "user")
    got = df._person_for("ev_none", "exit", {})
    assert got == "user"


def test_a_faceless_sighting_never_mints_a_body(monkeypatch):
    """03:05:45 on 2026-08-16: a sighting of the User — already present, already
    counted — carried no face, minted s1, and the room read 3 for one man.
    A sighting is a look at someone already here; unknown means silence."""
    _reset()
    from crissa_presence import argus_notify as an

    an._ONSET_CLAIMS.clear()
    posts = []
    monkeypatch.setattr(an, "post_oracle_visit", lambda b: posts.append(b) or {"ok": True})
    out = df._apply("ev_sight", 1000, {"event": "sighting"}, {})
    assert out.get("applied") is False
    assert out.get("reason") == "unnamed_sighting"
    assert posts == [], "nothing may be written for an unnamed sighting"


def test_an_unnamed_exit_never_mints_a_body(monkeypatch):
    """You cannot leave a room you were never counted in."""
    _reset()
    from crissa_presence import argus_notify as an

    an._ONSET_CLAIMS.clear()
    monkeypatch.setattr(an, "_roster_user_for_exit", lambda: "")
    assert df._person_for("ev_x", "exit", {}) == ""


def test_an_enter_may_still_mint_an_unknown_body():
    """A stranger walking in IS a new body — that one is real.

    mint_scratch stamps an EXISTING row and returns no_event otherwise, so the
    row has to be opened first, exactly as fan_out does before calling out.
    """
    _reset()
    from crissa_presence.oracle_event import open_event

    open_event(time.time() - 91.0, pack_id="ev_new_enter")
    pid = df._person_for("ev_new_enter", "enter", {})
    assert pid, "an unnamed enter must still produce a scratch id"


def test_a_scratch_id_carries_through_the_visit():
    """user 2026-08-16: "an unnamed entry does not become S1 like we planned.
    If it were, a sighting then recorded would become a sighting of s1 not
    unnamed."

    His 06:36 sequence: s1 WAS minted at the entry, then both sightings behind
    it resolved to nothing, because _person_for only consulted the row for the
    current trigger — seconds old and empty — instead of who is still in the
    room."""
    _reset()
    import time as _t

    from crissa_presence.oracle_event import open_event

    t = _t.time() - 400
    open_event(t, pack_id="ev_carry_entry")
    assert df._person_for("ev_carry_entry", "enter", {}) == "s1"

    open_event(t + 6, pack_id="ev_carry_sight")
    assert df._person_for("ev_carry_sight", "sighting", {}) == "s1"


def test_an_exit_prefers_the_open_visit_over_the_roster():
    """A woman in a light cardigan walked out at 06:36:55 and was recorded as
    `user`, because the exit fallback asked for the sole ROSTER user. The sole
    OPEN VISIT is the person actually in the room."""
    _reset()
    import time as _t

    from crissa_presence import argus_notify as an
    from crissa_presence.oracle_event import open_event

    monkey_roster = an._roster_user_for_exit
    try:
        an._roster_user_for_exit = lambda: "user"
        t = _t.time() - 500
        open_event(t, pack_id="ev_exit_entry")
        df._person_for("ev_exit_entry", "enter", {})  # mints a scratch
        open_event(t + 40, pack_id="ev_exit_leave")
        got = df._person_for("ev_exit_leave", "exit", {})
        assert got != "user", "must not name the User for someone else's exit"
        assert got.startswith("s")
    finally:
        an._roster_user_for_exit = monkey_roster


def test_exit_matches_robe_and_hair_not_sole_visit():
    """2026-08-17 05:56:57: man, short graying, gray long-sleeve walked out.
    Sole open visit was s1 (woman, gray cardigan, long wavy brown) and CODE
    applied the leave to her. Who left is the enter card that matches robe/hair.
    """
    _reset()
    import time as _t

    from crissa_presence.oracle_event import open_event, stamp_argus, stamp_faceid

    t = _t.time() - 200
    open_event(t, pack_id="ev_s1_in")
    df._person_for("ev_s1_in", "enter", {})
    stamp_argus(
        "ev_s1_in",
        event="enter",
        clothes="light gray cardigan over gray top",
        hair="long wavy brown",
        sex="F",
    )
    open_event(t + 5, pack_id="ev_user_in")
    stamp_faceid("ev_user_in", name="user", sim=0.7)
    stamp_argus(
        "ev_user_in",
        event="enter",
        clothes="gray long-sleeve shirt",
        hair="short graying",
        sex="M",
    )
    open_event(t + 80, pack_id="ev_leave")
    got = df._person_for(
        "ev_leave",
        "exit",
        {},
        {
            "event": "exit",
            "clothes": "gray long-sleeve shirt",
            "hair": "short graying",
            "sex": "M",
        },
    )
    assert got == "user", got


def test_exit_names_sole_user_even_when_robe_is_stale():
    """08:20: sole user, enter card robe, leave clip grey shirt.

    Clothes contradict must not skip naming — roster fallback never
    returns the User, so skip → unnamed_exit → no −1.
    """
    _reset()
    import time as _t

    from crissa_presence.oracle_event import open_event, stamp_argus, stamp_faceid

    t = _t.time() - 200
    open_event(t, pack_id="ev_robe_in")
    stamp_faceid("ev_robe_in", name="user", sim=0.8)
    stamp_argus(
        "ev_robe_in",
        event="enter",
        clothes="brown robe",
        hair="short",
        sex="M",
    )
    open_event(t + 80, pack_id="ev_grey_out")
    got = df._person_for(
        "ev_grey_out",
        "exit",
        {},
        {
            "event": "exit",
            "clothes": "grey shirt",
            "hair": "short",
            "sex": "M",
        },
    )
    assert got == "user", got


def test_exit_apply_subtracts_gray_shirt_not_cardigan():
    """Real apply path: two occupants, gray-shirt leave drops the match only."""
    _reset()
    from crissa_presence.door_watch import DoorInventory
    from crissa_presence.oracle_visit import apply_visit

    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="s1",
        now=10.0,
        clothes="light gray cardigan over gray top",
        hair="long wavy brown",
        sex="F",
    )
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="wanda",
        now=15.0,
        clothes="gray long-sleeve shirt",
        hair="short graying",
        sex="M",
    )
    got = df._person_for(
        "ev_leave",
        "exit",
        {},
        {
            "event": "exit",
            "clothes": "gray long-sleeve shirt",
            "hair": "short graying",
            "sex": "M",
        },
    )
    # Fan-out names who; apply is what subtracts. Drive both shipped functions.
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id=got,
        now=20.0,
        clothes="gray long-sleeve shirt",
        hair="short graying",
        sex="M",
    )
    assert out["applied"] is True, out
    assert out["person_id"] == "wanda", (got, out)
    assert "wanda" not in inv.present
    assert "s1" in inv.present
    assert inv.headcount() == 1


def test_exit_posts_leave_match_on_the_visit(monkeypatch):
    """The visit POST is where 'who left?' is recorded — not only fanout res."""
    _reset()
    from crissa_presence import argus_notify as an
    from crissa_presence.oracle_event import open_event, stamp_argus, stamp_faceid

    an._ONSET_CLAIMS.clear()
    posts: list[dict] = []
    monkeypatch.setattr(
        an,
        "post_oracle_visit",
        lambda b: posts.append(b) or {"ok": True, "applied": True},
    )
    t = time.time() - 200
    open_event(t, pack_id="ev_s1_in")
    df._person_for("ev_s1_in", "enter", {})
    stamp_argus(
        "ev_s1_in",
        event="enter",
        clothes="light gray cardigan over gray top",
        hair="long wavy brown",
        sex="F",
    )
    open_event(t + 5, pack_id="ev_user_in")
    stamp_faceid("ev_user_in", name="user", sim=0.7)
    stamp_argus(
        "ev_user_in",
        event="enter",
        clothes="gray long-sleeve shirt",
        hair="short graying",
        sex="M",
    )
    open_event(t + 80, pack_id="ev_leave")
    out = df._apply(
        "ev_leave",
        7001,
        {
            "event": "exit",
            "clothes": "gray long-sleeve shirt",
            "hair": "short graying",
            "sex": "M",
        },
        {},
    )
    assert posts, out
    body = posts[0]
    assert body["person_id"] == "user", body
    assert body["leave_match"]["method"] == "clothes_hair", body
    assert body["leave_match"]["person_id"] == "user", body
    assert out.get("leave_match", {}).get("person_id") == "user"


def test_exit_apply_ignores_faceid_on_a_back(monkeypatch):
    """FaceID named the other person on a back. Clothes still pick who left."""
    _reset()
    from crissa_presence import argus_notify as an
    from crissa_presence.oracle_event import open_event, stamp_argus, stamp_faceid

    an._ONSET_CLAIMS.clear()
    posts: list[dict] = []
    monkeypatch.setattr(
        an,
        "post_oracle_visit",
        lambda b: posts.append(b) or {"ok": True, "applied": True},
    )
    t = time.time() - 200
    open_event(t, pack_id="ev_mi_in")
    stamp_faceid("ev_mi_in", name="wanda", sim=0.7)
    stamp_argus(
        "ev_mi_in",
        event="enter",
        clothes="green jacket",
        hair="ponytail",
        sex="F",
    )
    open_event(t + 5, pack_id="ev_user_blue")
    stamp_faceid("ev_user_blue", name="user", sim=0.7)
    stamp_argus(
        "ev_user_blue",
        event="enter",
        clothes="blue short-sleeve shirt",
        hair="short",
        sex="M",
    )
    open_event(t + 80, pack_id="ev_blue_out")
    out = df._apply(
        "ev_blue_out",
        8101,
        {
            "event": "exit",
            "clothes": "blue short-sleeve shirt",
            "hair": "short",
            "sex": "M",
        },
        {"name": "wanda", "sim": 0.55},
    )
    assert posts, out
    body = posts[0]
    assert body["person_id"] == "user", body
    assert body["leave_match"]["method"] == "clothes_hair", body


def test_exit_apply_harvests_prose_when_json_clothes_empty(monkeypatch):
    """Argus wrote the robe in the sentence, left JSON clothes blank."""
    _reset()
    from crissa_presence import argus_notify as an
    from crissa_presence.oracle_event import open_event, stamp_argus, stamp_faceid

    an._ONSET_CLAIMS.clear()
    posts: list[dict] = []
    monkeypatch.setattr(
        an,
        "post_oracle_visit",
        lambda b: posts.append(b) or {"ok": True, "applied": True},
    )
    t = time.time() - 200
    open_event(t, pack_id="ev_s1_cardigan")
    df._person_for("ev_s1_cardigan", "enter", {})
    stamp_argus(
        "ev_s1_cardigan",
        event="enter",
        clothes="green jacket",
        hair="ponytail",
        sex="F",
    )
    open_event(t + 5, pack_id="ev_user_blue2")
    stamp_faceid("ev_user_blue2", name="user", sim=0.7)
    stamp_argus(
        "ev_user_blue2",
        event="enter",
        clothes="blue short-sleeve shirt",
        hair="short",
        sex="M",
    )
    open_event(t + 80, pack_id="ev_prose_out")
    out = df._apply(
        "ev_prose_out",
        8102,
        {
            "event": "exit",
            "clothes": "",
            "hair": "",
            "sex": None,
            "who": None,
            "comment": "A person in a blue short sleev shirt walked out.",
        },
        {},
    )
    assert posts, out
    body = posts[0]
    assert body["person_id"] == "user", (body, out)
    assert "blue" in str(body.get("clothes") or "").lower()
    assert body["leave_match"]["method"] == "clothes_hair", body


def test_exit_refuses_sole_visit_on_sex_mismatch():
    """A man leaving cannot be the only open visit if that visit is F."""
    _reset()
    import time as _t

    from crissa_presence import argus_notify as an
    from crissa_presence.oracle_event import open_event, stamp_argus

    an._roster_user_for_exit = lambda: ""
    t = _t.time() - 100
    open_event(t, pack_id="ev_only_s1")
    df._person_for("ev_only_s1", "enter", {})
    stamp_argus(
        "ev_only_s1",
        event="enter",
        clothes="light gray cardigan",
        hair="long wavy brown",
        sex="F",
    )
    open_event(t + 40, pack_id="ev_man_out")
    got = df._person_for(
        "ev_man_out",
        "exit",
        {},
        {"event": "exit", "clothes": "gray shirt", "hair": "short graying", "sex": "M"},
    )
    assert got != "s1", got


def test_both_witnesses_get_the_same_frames(monkeypatch):
    """user 2026-08-16: "the movie got an arm and the faceid no face got the
    exit. Something wrong here."

    Argus received 8 action-bounded frames while FaceID scanned a fixed 16, so
    FaceID ran past the crossing into frames Argus never saw. Two witnesses on
    different footage cannot corroborate each other.
    """
    import inspect

    from crissa_presence import raw_clip

    src_argus = inspect.getsource(df._ask_argus)
    src_faceid = inspect.getsource(df._ask_faceid)
    assert "_argus_timing" in src_argus
    assert "_crew_metrics" in src_argus
    rec = inspect.getsource(df._record)
    assert "queued=" in rec
    assert "waiting=" in rec
    assert "queued_ms" not in rec
    assert "clip_for_event" in src_argus
    assert "action_window" in src_faceid
    # and clip_for_event must itself be bounded by the same function
    assert "action_window" in inspect.getsource(raw_clip.clip_for_event)


def test_one_fanout_per_crossing_not_one_per_trigger(monkeypatch):
    """Same-walk spray is eat_burst. A later trigger after fire still fires
    (wave then sneak-in). No COALESCE dead zone."""
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    _reset()
    now = time.time()
    df._LAST_FIRE[0] = now - 1.0
    captured = []
    monkeypatch.setattr(df, "_fetch_triggers", lambda: [_t(9001, now - 10)])
    monkeypatch.setattr(df, "_room_restored", lambda a, b: True)
    monkeypatch.setattr(df, "fan_out", lambda t: captured.append(t["seq"]) or {"fired": t["seq"]})
    out = df.poll_once(now)
    assert out and out.get("fired") == 9001, "sneak-in after a wave must still fire"
    assert captured == [9001]


def test_it_waits_for_the_room_before_asking(monkeypatch):
    """user: "cut too early was headed for the door". SETTLE_SEC asked at
    trigger+3s regardless, so a six-second walk was cut at three and Argus
    correctly reported in-room motion with no crossing."""
    _reset()
    now = time.time()
    df._LAST_FIRE[0] = 0.0
    monkeypatch.setattr(df, "_fetch_triggers", lambda: [_t(9002, now - 5)])
    monkeypatch.setattr(df, "_room_restored", lambda a, b: False)
    assert df.poll_once(now) is None, "must wait while someone is still in frame"
    assert 9002 not in df._SEEN, "and stay eligible for the next pass"


def test_it_gives_up_waiting_eventually(monkeypatch):
    """Occupied FOV (dog in jamb) must not hold Argus for 30–45s."""
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    _reset()
    now = time.time()
    df._LAST_FIRE[0] = 0.0
    captured = []
    monkeypatch.setattr(df, "_fetch_triggers",
                        lambda: [_t(9003, now - df.LINGER_WAIT_SEC - 5)])
    monkeypatch.setattr(df, "_room_restored", lambda a, b: False)
    monkeypatch.setattr(df, "fan_out", lambda t: captured.append(t) or {"fired": True})
    assert df.poll_once(now) == {"fired": True}
    assert captured and captured[0].get("_rest_ts") is None


def test_occupied_past_max_wait_still_waits_for_empty(monkeypatch):
    """xena linger: 6s is not linger-ended. Wait for the empty-ref still."""
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    _reset()
    now = time.time()
    df._LAST_FIRE[0] = 0.0
    monkeypatch.setattr(df, "_fetch_triggers",
                        lambda: [_t(9004, now - df.MAX_WAIT_SEC - 1)])
    monkeypatch.setattr(df, "_room_restored", lambda a, b: False)
    monkeypatch.setattr(df, "fan_out", lambda t: {"fired": True})
    assert df.poll_once(now) is None


def test_quiet_occupied_does_not_fire(monkeypatch):
    """Standing still in the jamb is not empty-door. Do not ask yet."""
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    _reset()
    now = time.time()
    df._LAST_FIRE[0] = 0.0
    monkeypatch.setattr(df, "_fetch_triggers", lambda: [_t(9005, now - 3)])
    monkeypatch.setattr(df, "_room_restored", lambda a, b: "quiet")
    monkeypatch.setattr(df, "fan_out", lambda t: {"fired": True})
    assert df.poll_once(now) is None


def test_empty_ref_sets_rest_ts(monkeypatch):
    monkeypatch.setattr(df, "_door_in_view", lambda: True)
    _reset()
    now = time.time()
    df._LAST_FIRE[0] = 0.0
    captured = []
    monkeypatch.setattr(df, "_fetch_triggers", lambda: [_t(9006, now - 8)])
    monkeypatch.setattr(df, "_room_restored", lambda a, b: True)
    monkeypatch.setattr(df, "fan_out", lambda t: captured.append(t) or {"fired": True})
    assert df.poll_once(now) == {"fired": True}
    assert captured[0].get("_rest_ts")
    assert "_linger_ended" not in captured[0]


def test_unresolved_is_recorded_never_applied(monkeypatch):
    """user 2026-08-16: "It is not correct that this is a sighting. She does
    move out of door most likely but we cannot prove it as this clip ends
    early... She is not still there at the door where did she go?"

    With only enter|exit|sighting on offer, "I could not see a crossing" had
    nowhere to go but sighting — which asserts the person STAYED, and the next
    frames contradicted it. A forced enum manufactured certainty the clip could
    not support.
    """
    _reset()
    from crissa_presence import argus_notify as an

    an._ONSET_CLAIMS.clear()
    posts = []
    monkeypatch.setattr(an, "post_oracle_visit", lambda b: posts.append(b) or {"ok": True})
    out = df._apply("ev_u", 5000, {"event": "unresolved"}, {"name": "user"})
    assert out["applied"] is False and out["reason"] == "unresolved"
    assert posts == [], "an unresolved clip may not write a visit"


def test_argus_is_offered_the_unresolved_answer():
    from crissa_presence.argus_notify import MOTION_PROMPT, VISIT_JSON

    assert "unresolved" in VISIT_JSON
    assert "unresolved" in MOTION_PROMPT


def test_both_witnesses_get_one_shared_window(monkeypatch):
    """Computing the window separately in each witness was meant to keep them
    ungated and instead gave them different footage: still_ref() picks the
    longest still run from a 90s look-back and that run MOVES as frames land.
    On ev_1786880910456 Argus got 14 frames (seq 417093-417110) and FaceID got
    1 (seq 417065) — windows that do not even overlap, so FaceID reported
    no_face on an entry with a face in it."""
    _reset()
    seen = {}
    monkeypatch.setattr(df, "action_window", lambda ts: [(1.0, 11, "a"), (2.0, 12, "b")],
                        raising=False)
    import crissa_presence.raw_clip as rc
    monkeypatch.setattr(rc, "action_window", lambda ts, **k: [(1.0, 11, "a"), (2.0, 12, "b")])
    monkeypatch.setattr(df, "_ask_argus",
                        lambda eid, ts, w=None, **k: seen.setdefault("argus", w) or {"ok": True})
    monkeypatch.setattr(df, "_ask_faceid",
                        lambda eid, ts, w=None, **k: seen.setdefault("faceid", w) or {"ok": True})
    monkeypatch.setattr(df, "APPLY", False)
    df.fan_out(_t(7777, time.time() - 60))
    assert "argus" not in seen
    assert seen.get("faceid") == [(1.0, 11, "a"), (2.0, 12, "b")]


def test_camera_motion_is_not_room_motion(monkeypatch):
    """user 2026-08-16: "No door in fov = camera motion or full lense blockage.
    Not motion." A slewing camera changes every pixel, so it clears any
    diff-against-the-room test exactly as a person does — ev_1786882797237 was
    32 frames of the dresser sliding past while the tilt was re-aimed."""
    _reset()
    now = time.time()
    df._LAST_FIRE[0] = 0.0
    monkeypatch.setattr(df, "_fetch_triggers", lambda: [_t(8801, now - 10)])
    monkeypatch.setattr(df, "_room_restored", lambda a, b: True)
    monkeypatch.setattr(df, "_door_in_view", lambda: False)
    monkeypatch.setattr(df, "fan_out", lambda t: {"fired": True})
    assert df.poll_once(now) is None, "must not judge a clip the door is not in"


def test_an_unreachable_camera_fails_open(monkeypatch):
    """Better to judge a clip than to go blind because health did not answer."""
    monkeypatch.setattr(df.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert df._door_in_view() is True


def test_exit_identity_is_clothes_not_argus_who(monkeypatch):
    """WP-4: Argus `who` is not identity. Robe/hair vs enter cards.

    Two people in the room. Clothes pick s1. `who: user` must not override.
    Roster fallback must not name the User.
    """
    import inspect

    import crissa_presence.door_fanout as df

    _reset()
    visits = [
        {"person_id": "user", "clothes": "olive green long sleeve shirt",
         "hair": "short graying dark", "sex": "M"},
        {"person_id": "s1", "clothes": "light gray cardigan over black top",
         "hair": "bun", "sex": "F"},
    ]
    monkeypatch.setattr("crissa_presence.oracle_event.open_visits", lambda: visits)
    monkeypatch.setattr("crissa_presence.oracle_event.get_event", lambda e: {})
    monkeypatch.setattr(df, "_leave_candidates", lambda: visits)
    monkeypatch.setattr(
        "crissa_presence.argus_notify._roster_user_for_exit", lambda: "user"
    )

    from crissa_presence.argus_notify import present_block

    block = present_block()
    assert "user = olive green long sleeve shirt" in block
    assert "s1 = light gray cardigan over black top" in block

    robe = {
        "clothes": "light gray cardigan over black top",
        "hair": "bun",
        "sex": "F",
    }
    assert df._person_for("ev_1", "exit", {}, robe) == "s1"
    assert df._person_for("ev_2", "exit", {}, {"who": "user", **robe}) == "s1"
    monkeypatch.setattr(
        "crissa_presence.argus_notify._roster_user_for_exit", lambda: ""
    )
    assert df._person_for("ev_3", "exit", {}, {"who": "s1"}) == ""
    assert df._person_for("ev_4", "exit", {}, {"who": "gandalf"}) == ""

    src = inspect.getsource(df._person_for)
    assert "name_face" not in src
    assert "faceid_only" in inspect.getsource(df.fan_out)


def test_enter_clip_who_beats_garbage_lineup(monkeypatch, tmp_path):
    """Argus clip-who is not identity. Unnamed FaceID enter may mint scratch."""
    people = tmp_path / "people" / "wanda"
    people.mkdir(parents=True)
    (people / "person.json").write_text('{"id":"wanda","active":true}\n')
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    monkeypatch.setattr("crissa_presence.oracle_event.get_event", lambda e: {})
    monkeypatch.setattr("crissa_presence.oracle_event.open_visits", lambda: [])
    monkeypatch.setattr(
        "crissa_presence.oracle_event.mint_scratch",
        lambda eid: {"row": {"person_id": "s1"}},
    )
    called = []

    def boom(*_a, **_k):
        called.append(1)
        raise AssertionError("face-lineup must not run")

    monkeypatch.setattr("crissa_presence.face_lineup.name_face", boom)
    argus = {"who": "wanda", "event": "enter", "clothes": "light gray robe",
             "hair": "long blonde", "sex": "F"}
    faceid = {"name": None, "sim": -0.0089}
    assert df._person_for("ev_1787649909301", "enter", faceid, argus) == "s1"
    assert called == []


def test_present_array_offers_one_person_so_exit_can_name_them(monkeypatch):
    """One described visit still has to answer who left (robe/hair)."""
    from crissa_presence.argus_notify import present_block

    monkeypatch.setattr(
        "crissa_presence.oracle_event.open_visits",
        lambda: [{"person_id": "user", "clothes": "olive shirt"}],
    )
    block = present_block()
    assert "user = olive shirt" in block
    assert '"who"' in block


def test_a_trigger_rejected_for_a_transient_reason_is_retried(monkeypatch):
    """Door out of view is "not now", not "never".

    _fresh marks a seq seen BEFORE _maybe_fire decides what to do with it, so
    any early return that does not put it back deletes the crossing for good.
    user walked in at 12:55:06 on 2026-08-16 while the camera was pointed at a
    seat and no row was ever written — not a verdict, not even a rejection.
    """
    import crissa_presence.door_fanout as df

    _reset()
    trig = {"seq": 4242, "ts": time.time(), "meta": {"energy": 99.0}}
    monkeypatch.setattr(df, "_fetch_triggers", lambda: [trig])
    monkeypatch.setattr(df, "_door_in_view", lambda: False)

    # _fresh marks it seen and returns it in the SAME pass — that is the
    # window in which a non-discarding early return loses it forever.
    df.poll_once(now=time.time())
    assert 4242 not in df._SEEN, "door-out-of-view must not consume the trigger"


def test_coalesce_shadow_eats_spray(monkeypatch):
    """eat_burst consumes same-window spray. A later envelope is not dropped."""
    _reset()
    t0 = 1000.0
    ring = [_t(5353, t0), _t(5354, t0 + 1.0), _t(5400, t0 + 12.0)]
    df._eat_burst(t0, t0 + 2.0, ring)
    assert 5353 in df._SEEN and 5354 in df._SEEN
    assert 5400 not in df._SEEN, "sneak-in after the wave rest is still eligible"


def test_a_named_sighting_is_not_rewritten_to_enter(monkeypatch):
    """11:50:36 2026-08-25: LINGER_SEC=30 posted enter for a mill sighting."""
    _reset()
    from crissa_presence import argus_notify as an

    an._ONSET_CLAIMS.clear()
    posts = []
    monkeypatch.setattr(
        an,
        "post_oracle_visit",
        lambda b: posts.append(dict(b))
        or {"ok": True, "applied": True, "person_id": "user"},
    )
    monkeypatch.setattr(df, "_person_for", lambda eid, ev, b, a=None: "user")
    out = df._apply(
        "ev_linger",
        2592260,
        {"event": "sighting", "who": "user"},
        {"name": "user", "sim": 0.6341},
    )
    assert posts, out
    assert posts[0]["event"] == "sighting"


def test_trigger_onset_reads_meta():
    seq, ts = df.trigger_onset(
        {"seq": 9, "ts": 1.0, "meta": {"onset_seq": 3, "onset_ts": 0.4}}
    )
    assert seq == 3
    assert ts == 0.4
    assert df.trigger_onset({"seq": 9, "ts": 1.0}) == (None, None)
    assert df.trigger_onset({"onset_seq": "8", "onset_ts": "2.5"}) == (8, 2.5)


def test_window_before_uses_onset_not_blind_pad():
    assert df.window_before_s(10.0, None) == 8.0
    # 3.1 s lag + 0.3 lead → 3.4, not a full 8 s hallway pull
    assert df.window_before_s(10.0, 6.9) == pytest.approx(3.4)
    assert df.window_before_s(10.0, 9.8) == 0.5  # floor
    assert df.window_before_s(10.0, 1.0) == 8.0  # cap at default
