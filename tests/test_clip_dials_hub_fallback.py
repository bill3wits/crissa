"""A clip with no set wears its hub loop's set (the User 2026-09-26): own set -> hub loop's set -> lock."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def _block():
    start = SRC.index("HUB_SET_FALLBACK_ON = ")
    end = SRC.index("\ndef lock_dials(", start)
    return SRC[start:end]


def _exec(clips, playlist_library=None, rest_loop=None, env_off=False):
    import os
    calls = {"applied": [], "actions": []}

    class Lib:
        def playlist(self, key):
            return {"library": playlist_library} if playlist_library else {}

    class Graph:
        def hub_after(self, key):
            return "down"

        def rest_for(self, hub):
            return rest_loop

    class Seq:
        graph = Graph()

    class Ph:
        lib = Lib()
        seq = Seq()

    g = {
        "os": os, "STATE": {"playhead": Ph()}, "CLIP_DIALS": {"on": None, "prev": None},
        "load_clip_dials": lambda: clips, "preset_dials": lambda: {"edge_s": 0.1},
        "apply_preset": lambda row: calls["applied"].append(row), "face_dials_enter": lambda k: None,
        "action": lambda *a: calls["actions"].append(a), "dial_brief": lambda d: str(d),
        "print": lambda *a, **k: None,
    }
    if env_off:
        os.environ["HOUSE_CLIP_DIALS_HUB_FALLBACK"] = "0"
    else:
        os.environ.pop("HOUSE_CLIP_DIALS_HUB_FALLBACK", None)
    exec(compile(_block(), "conductor-slice", "exec"), g)
    return g, calls


def test_a_slide_with_no_set_wears_the_loop_named_by_its_sidecar():
    clips = {"lila-work": {"dials": {"edge_s": 0.36}}}
    g, calls = _exec(clips, playlist_library="lila-work")
    g["clip_dials_enter"]("lila-sit-down-chin")
    assert calls["applied"] == [clips["lila-work"]]
    assert g["CLIP_DIALS"]["on"] == "lila-work"
    assert "(hub loop lila-work)" in calls["actions"][0][1]


def test_without_a_sidecar_library_the_hubs_rest_loop_is_the_fallback():
    clips = {"work": {"dials": {"xfade_ms": 80}}}
    g, calls = _exec(clips, playlist_library=None, rest_loop="work")
    g["clip_dials_enter"]("flourish-glance")
    assert calls["applied"] == [clips["work"]] and g["CLIP_DIALS"]["on"] == "work"


def test_own_set_outranks_the_hub_loop_and_the_loop_itself_is_not_re_applied():
    clips = {"work": {"dials": {"xfade_ms": 80}}, "wake": {"dials": {"xfade_ms": 0}}}
    g, calls = _exec(clips, playlist_library="work")
    g["clip_dials_enter"]("wake")
    assert calls["applied"] == [clips["wake"]] and g["CLIP_DIALS"]["on"] == "wake"
    g2, calls2 = _exec(clips, playlist_library="work")
    g2["clip_dials_enter"]("work")
    g2["clip_dials_enter"]("flourish-nod")     # same source set: no second apply, no restore
    assert len(calls2["applied"]) == 1 and g2["CLIP_DIALS"]["on"] == "work"


def test_no_set_anywhere_restores_and_the_kill_turns_the_middle_step_off():
    clips = {"work": {"dials": {"xfade_ms": 80}}}
    g, calls = _exec(clips, playlist_library="work", env_off=True)
    g["clip_dials_enter"]("flourish-nod")
    assert calls["applied"] == [], "killed: a set-less clip falls to the lock as before"
    import os
    os.environ.pop("HOUSE_CLIP_DIALS_HUB_FALLBACK", None)
