"""Round 33, 2026-10-19 (the User's design call on .101's round 32): each turned count is checked as it ends - its side
banked, else her quarter-turn line and that count again - and Your face has no left/right section once both sides are
in. A turned count whose looks find no face says so on the page; each reason a look found no face is logged once."""
from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


PKGS = [("crissa_presence", "presence", "crissa_presence.enroll_panel"), ("crissa_id", "id", "crissa_id.house_enroll")]


@pytest.fixture(params=PKGS, ids=["presence", "id"])
def pkg(request):
    """Round 34: both pack copies (a pack's Setup runs the id module's; the presence copy without one)."""
    return request.param


@pytest.fixture
def vr(monkeypatch, tmp_path, pkg):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "home"))
    for k in ("CRISSA_TURN_CHECK", "CRISSA_TURN_CHECK_TRIES"):
        monkeypatch.delenv(k, raising=False)
    top, mdir, base = pkg
    for k in [k for k in sys.modules if k == top or k.startswith(top + ".")]:
        monkeypatch.delitem(sys.modules, k)
    monkeypatch.syspath_prepend(str(ROOT / "modules" / mdir))
    mod = importlib.import_module(base + ".voice_register")
    monkeypatch.setattr(mod, "_house_tree", lambda: False)
    monkeypatch.setattr(mod, "_cancelled", lambda: False)
    monkeypatch.setattr(mod, "AFTER_BEEP_SEC", 0.0)
    monkeypatch.setattr(mod.cues, "wait_for_beep", lambda *a, **k: None)
    mod._TURNED_OK.clear()
    mod._LAST_TURN_GLASS.clear()
    return mod


def _wire(vr, monkeypatch, bank_on_try: int | None, quiet=False):
    """The count again banks its side on the given try (None: never)."""
    said, glass, quarantined, tries = [], [], [], {"n": 0}
    monkeypatch.setattr(vr, "_announce", lambda site, *t, **k: said.extend(t) or 0.0)
    monkeypatch.setattr(vr, "_glass", lambda phase, text="", **e: glass.append((phase, e.get("line"), e.get("beep"))))
    monkeypatch.setattr(vr, "_quarantine", lambda rec: quarantined.append(rec["wav"]))

    def snap(site, pid, **kw):
        tries["n"] += 1
        if bank_on_try is not None and tries["n"] >= bank_on_try:
            vr._TURNED_OK.add(kw["side"])
        th = threading.Thread(target=lambda: None)
        th.start()
        return th
    monkeypatch.setattr(vr, "_snap_soon", snap)
    monkeypatch.setattr(vr, "_take_voice", lambda site, pid, **kw: {"wav": "again-%d.wav" % tries["n"], "out_dir": "x"})
    monkeypatch.setattr(vr, "take_is_quiet", lambda wav: (quiet, 0.5, 2000))
    return said, glass, quarantined


def _check(vr, takes, rec):
    return vr._turn_check(SimpleNamespace(), "p1", i=3, n=4, side="left", th=None, take_id="seat_off", pose="sweep",
                          angle="off", station="desk", room_mic="m", window=5.0, sweep_paths=[], takes=takes, rec=rec)


def test_a_side_not_banked_gets_the_quarter_turn_and_the_count_again(vr, monkeypatch):
    said, glass, quarantined = _wire(vr, monkeypatch, bank_on_try=1)
    first = {"wav": "first.wav", "out_dir": "x"}
    takes = [first]
    assert _check(vr, takes, first)
    assert said == [vr.TURN_QUARTER_LINE]
    assert takes == [{"wav": "again-1.wav", "out_dir": "x", "min_peak": vr.V_MIN_PEAK}]
    assert quarantined == ["first.wav"], "the count again's good take replaces the first"
    assert ("turn", "", True) in glass, "a new beep on the count again"


def test_a_banked_side_moves_on_at_once(vr, monkeypatch):
    said, _g, _q = _wire(vr, monkeypatch, bank_on_try=None)
    vr._TURNED_OK.add("left")
    takes = [{"wav": "first.wav"}]
    assert _check(vr, takes, takes[0]) and said == [] and takes == [{"wav": "first.wav"}]


def test_the_tries_are_capped_and_a_quiet_count_again_keeps_the_first(vr, monkeypatch):
    said, _g, quarantined = _wire(vr, monkeypatch, bank_on_try=None, quiet=True)
    first = {"wav": "first.wav", "out_dir": "x"}
    takes = [first]
    assert _check(vr, takes, first)
    assert said == [vr.TURN_QUARTER_LINE] * 3 + [vr.TURN_LATER_LINE] and takes == [first]
    assert quarantined == ["again-1.wav", "again-2.wav", "again-3.wav"]
    monkeypatch.setenv("CRISSA_TURN_CHECK_TRIES", "0")
    said.clear()
    assert _check(vr, takes, first) and said == []


def test_the_turn_cues_ask_for_a_quarter_turn_and_the_kill(vr, monkeypatch):
    words = [t[4] for t in vr.V_TAKES_R20]
    assert "a quarter turn to your left" in vr._cue(words[2]) and "a quarter turn to your right" in vr._cue(words[3])
    assert vr.turn_side(vr._cue(words[2])) == "left"
    monkeypatch.setenv("CRISSA_TURN_CHECK", "0")
    assert vr._cue(words[2]) == words[2]
    monkeypatch.delenv("CRISSA_TURN_CHECK")
    monkeypatch.setattr(vr, "_house_tree", lambda: True)
    assert not vr.turn_check_on(), "never on the house"


@pytest.mark.parametrize("d", ["modules/id/crissa_id/house_enroll", "modules/presence/crissa_presence/enroll_panel"])
def test_round_34_has_no_face_section_and_the_kill_keeps_round_32s(d):
    src = (ROOT / d / "voice_register.py").read_text()
    assert "_face_stage_checked" not in src and "TURN_SIDE_LINE" not in src, "no 'one side alone' fallback"
    i = src.index("        if turn_check_on():\n            # round 34 (the User): no Face photos section")
    assert "_face_stage(site, person_id, station=station)" in src[i:i + 400], "the kill: round 32's face section"
    ss = (ROOT / d / "sectioned_session.py").read_text()
    assert "def _turn_coach" in ss and "Now a quarter turn to your %s." in ss


def test_looks_with_no_face_say_so_and_log_the_reason_once(vr, monkeypatch, capsys):
    hints = []
    monkeypatch.setattr(vr, "_camera_off", lambda: False)
    monkeypatch.setattr(vr, "_turn_look", lambda site, station: (b"jpeg", ""))
    monkeypatch.setattr(vr, "_post", lambda url, body, timeout=0: (
        {"ok": True, "status": 200, "stored": False, "error": "no_face"} if "store_face" in url
        else {"ok": False, "status": 200, "error": "no_face"}))
    monkeypatch.setattr(vr, "_turn_hint", lambda text: hints.append(text))
    monkeypatch.setattr(vr, "coach_emit", lambda **e: None)
    clock = iter(i * 0.25 for i in range(1000))
    vr._snap_turned(SimpleNamespace(), "p1", station="desk", tag="profile", note="t", side="left",
                    sleep=lambda s: None, clock=lambda: next(clock))
    assert hints[0] == vr.TURN_BLIND_LINE and hints[-1] == ""
    out = capsys.readouterr().out
    assert out.count("a look found no face: no_face") == 1


def test_both_pack_copies_carry_the_check():
    for d in ("modules/id/crissa_id/house_enroll", "modules/presence/crissa_presence/enroll_panel"):
        src = (ROOT / d / "voice_register.py").read_text()
        assert "def _turn_check" in src and "CRISSA_TURN_CHECK" in src and "def _front_check" in src, d


# ── round 34: the front is checked like the turns (the User: "if either failed then run the correct-it script") ─────
def _front(vr, monkeypatch, bank_on: int | None):
    said, n = [], {"snaps": 0}
    monkeypatch.setattr(vr, "_announce", lambda site, *t, **k: said.extend(t) or 0.0)
    monkeypatch.setattr(vr, "_glass", lambda *a, **k: None)
    monkeypatch.setattr(vr, "_quarantine", lambda rec: None)

    def snap(site, pid, **kw):
        n["snaps"] += 1
        if bank_on is not None and n["snaps"] >= bank_on:
            vr._FRONT_OK.add(pid)
        return None
    monkeypatch.setattr(vr, "_snap_now", snap)
    monkeypatch.setattr(vr, "_take_voice", lambda site, pid, **kw: {"wav": "f%d.wav" % n["snaps"], "out_dir": "x"})
    monkeypatch.setattr(vr, "take_is_quiet", lambda wav: (False, 0.5, 2000))
    vr._FRONT_OK.discard("p1")
    return said, n


def _fcheck(vr, takes, rec):
    return vr._front_check(SimpleNamespace(), "p1", i=2, n=4, take_id="seat_count_on_2", pose="front", angle="on",
                           station="desk", room_mic="m", window=5.0, front_paths=[], takes=takes, rec=rec)


def test_a_missing_front_gets_her_line_and_the_facing_count_again(vr, monkeypatch):
    said, n = _front(vr, monkeypatch, bank_on=1)
    first = {"wav": "first.wav", "out_dir": "x"}
    takes = [first]
    assert _fcheck(vr, takes, first)
    assert said == [vr.TURN_FRONT_LINE] and n["snaps"] == 1 and takes[0]["wav"] == "f1.wav"


def test_a_banked_front_moves_on_and_the_tries_are_capped(vr, monkeypatch):
    said, n = _front(vr, monkeypatch, bank_on=None)
    vr._FRONT_OK.add("p1")
    assert _fcheck(vr, [{}], None) and said == []
    vr._FRONT_OK.discard("p1")
    assert _fcheck(vr, [{}], None) and said == [vr.TURN_FRONT_LINE] * 3 + [vr.TURN_LATER_LINE]


@pytest.mark.parametrize("d", ["modules/id/crissa_id/house_enroll", "modules/presence/crissa_presence/enroll_panel"])
def test_the_front_check_sits_after_the_last_facing_count(d):
    src = (ROOT / d / "voice_register.py").read_text()
    assert "and not any(not t[3] for t in plan[i:])):              # the last facing count: the front checked" in src
    assert "_FRONT_OK.add(person_id)                         # round 34: a front banked in the counts" in src


# ── round 34: a turned still only after the head moved; the sweep never banks a turned bin ──────────────────────────
def test_a_look_from_before_the_turn_is_never_stored(vr, monkeypatch, capsys):
    stored = []
    monkeypatch.setattr(vr, "_camera_off", lambda: False)
    monkeypatch.setattr(vr, "_turn_look", lambda site, station: (b"jpeg", ""))

    def post(url, body, timeout=0):
        if "store_face" in url:
            stored.append(body)
            return {"ok": True}
        return {"ok": True, "status": 200, "yaw": -0.107, "bin": "front", "det": 0.9}   # .101's +0.49 s look
    monkeypatch.setattr(vr, "_post", post)
    monkeypatch.setattr(vr, "coach_emit", lambda **e: None)
    clock = iter(i * 0.25 for i in range(1000))
    got = vr._snap_turned(SimpleNamespace(), "p1", station="desk", tag="profile", note="t", side="left",
                          sleep=lambda s: None, clock=lambda: next(clock))
    assert got["stored"] is False and got["why"] == "not turned" and stored == []
    assert "not stored - no look turned to the left" in capsys.readouterr().out
    assert "left" not in vr._TURNED_OK


def test_the_sweep_keeps_front_stills_only(vr, monkeypatch):
    posted = []
    monkeypatch.setattr(vr, "embed_jpegs", lambda paths: [
        {"ok": True, "bin": "front", "path": str(paths[0])}, {"ok": True, "bin": "three_quarter_L", "path": str(paths[0])}])
    monkeypatch.setattr(vr, "best_per_bin", lambda recs: recs)
    monkeypatch.setattr(vr, "_post", lambda url, body, timeout=0: posted.append(body["pose"]) or {"ok": True})
    monkeypatch.setattr(vr, "coach_emit", lambda **e: None)
    monkeypatch.setattr(vr, "_emit_angles", lambda have: None)
    p = Path(vr.__file__).parent / "voice_register.py"                 # any readable file as the jpeg
    have = vr._sweep_store(SimpleNamespace(), "p1", station="desk", paths=[p], have=[])
    assert posted == ["front"] and have == ["front"]
    monkeypatch.setenv("CRISSA_TURN_CHECK", "0")
    posted.clear()
    vr._sweep_store(SimpleNamespace(), "p1", station="desk", paths=[p], have=[])
    assert posted == ["front", "three_quarter_L"], "the kill: round 33's sweep"
