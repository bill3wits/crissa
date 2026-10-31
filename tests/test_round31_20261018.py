"""Round 31 (2026-10-18): .101's nine round 30 fixes as built (each pinned by the line .101 greps the kit for), the
neck cut on every pack face, and the ear that never strands its queue (test_ear_decode_heal_20261018)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ID = ROOT / "modules/id/crissa_id/house_enroll"
PR = ROOT / "modules/presence/crissa_presence/enroll_panel"
T = lambda p: (ROOT / p).read_text(encoding="utf-8") if isinstance(p, str) else p.read_text(encoding="utf-8")  # noqa: E731


def test_1_the_coach_parses_its_own_warm_body():
    for d in (ID, PR):
        src = T(d / "coach.py")
        warm = src.split('if path == "/warm":', 1)[1].split("if path ==", 1)[0]
        assert "obj" in warm and ("json.loads" in warm or "_read_json" in warm or "obj =" in warm)


def test_2_her_window_and_setups_mic_on_the_canceller():
    assert "def keep_her_sound" in T("lib/crissa_window.py")
    a = T("lib/audio_sink.py")
    assert "def capture_source" in a and "def move_inputs_of" in a and "CRISSA_SETUP_MIC_AEC" in a
    assert "keep_her_sound(_log)" in T("scripts/crissa.py")


def test_3_no_desk_before_her_entrance_and_on_her_way():
    s = T("lib/crissa_init.py")
    assert "CRISSA_NO_DESK_BEFORE_ENTRANCE" in s and "CRISSA_ON_HER_WAY" in s and "on my way to your office now" in s


def test_4_two_quick_photos_before_already():
    assert json.loads(T("config/setup-films.json")) and '"start_stop_f": 38' in T("config/setup-films.json")
    assert "face_start_on" in T("lib/crissa_init.py")      # .101's own whole files carry it here, not in voice_register
    for d in (ID, PR):
        assert '_film("face-start"' in T(d / "voice_register.py")


def test_5_the_neck_cut_on_every_pack_face_and_every_new_face():
    want = {"chin_cut": {"lip_half": 0.5, "chin_half": 0.22, "fade": 0.06}, "front_cut": {"from": 20}}
    desk = json.loads(T("crissa/overlay/display/crissa-poses/mouth-turn.json"))    # the kit's desk face (two bags)
    desk_house = json.loads(T("display/crissa-poses/mouth-turn.json"))
    lila_kit = json.loads(T("crissa/overlay/display/lila-poses/mouth-turn.json"))
    lila_house = json.loads(T("display/lila-poses/mouth-turn.json"))
    # round 32 (the User 2026-10-19): the desk face adds the jaw-line cut and hands over at 20 deg
    # (tests/test_round32_jaw_cut_20261019.py); Lila's kit face and new faces keep round 31's
    desk_want = dict(want, front_cut={"from": 20, "jaw": True})
    for k, v in want.items():
        assert desk[k] == desk_want[k] and lila_kit[k] == v
        assert k not in lila_house and k not in desk_house           # the house's faces keep their own files
    assert {k: v for k, v in lila_kit.items() if k not in want} == lila_house
    assert {k: v for k, v in desk.items() if k not in want} == dict(desk_house, **{"from": 20})
    pk = json.loads(T("config/crissa-packages.json"))["edition_overlay"]
    assert pk["display/lila-poses/mouth-turn.json"] == "crissa/overlay/display/lila-poses/mouth-turn.json"
    assert pk["display/crissa-poses/mouth-turn.json"] == "crissa/overlay/display/crissa-poses/mouth-turn.json"
    import sys
    sys.path.insert(0, str(ROOT / "lib"))
    import pack_turns
    for k, v in want.items():
        assert pack_turns.turn_rules({"45": "profile-45"})[k] == v


def test_6_to_9_the_small_ones():
    assert "def voice_chosen" in T("lib/crissa_procs.py")
    assert "_fh.register(_sig.SIGUSR1" in T("modules/ear/scripts/crissa-stt.py")
    assert "hung up before the answer" in T("modules/voice/scripts/crissa-asr.py")
    assert "mouth to %.2f s" in T("scripts/phone-app-server.py")


def test_the_ear_heal_has_its_kill():
    s = T("modules/ear/crissa_ear/ear_loop.py")
    assert "RAIL_STT_DECODE_HEAL" in s and "def _heal_decoder" in s
