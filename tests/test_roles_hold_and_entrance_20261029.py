"""The two blockers of the 10-29 review of cut 13: with v2 worn, (1) /v1/play camped and held the RAW classic key while
it played the worn library's clip, so Setup's held walk-in lost its hold and replayed; (2) Setup's entrance waited on
the asked name, so v2-walk-in's first frame read as "past the walk-in" and her entrance was cut."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_init as zi  # noqa: E402

COND = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def test_the_play_route_resolves_before_it_camps_or_holds():
    i = COND.index('if head == "/v1/play":')
    body = COND[i:COND.index('if head == "/v1/home":', i)]
    r = body.index("key = ph.seq.graph.resolve(key)")
    assert r < body.index("ph.seq.camp = key") and r < body.index('INIT_HOLD["key"] = key')


def test_setup_records_the_clip_the_player_took_and_the_entrance_waits_on_it(monkeypatch):
    posts = []
    monkeypatch.setattr(zi, "_post", lambda path, body, timeout=4.0: posts.append(body) or {"ok": True, "now": "v2-walk-in"})
    monkeypatch.setitem(zi._PLAYED_AS, "x", "y")
    assert zi.play(zi.CRISSA_ENTRANCE) is True and zi._PLAYED_AS[zi.CRISSA_ENTRANCE] == "v2-walk-in"
    seen = {}
    monkeypatch.setattr(zi, "wait_drawn", lambda key, frames=12, cap_s=8.0, not_key="": seen.update(not_key=not_key) or False)
    monkeypatch.setitem(zi._ENTERED, "key", zi._PLAYED_AS[zi.CRISSA_ENTRANCE])
    zi._entrance_then_up(cap_s=0.1)
    assert seen["not_key"] == "v2-walk-in"
    src = (ROOT / "lib" / "crissa_init.py").read_text(encoding="utf-8")
    assert '_ENTERED["key"] = _PLAYED_AS.get(key) or key' in src
    assert src.count('not_key=_ENTERED.get("key") or CRISSA_ENTRANCE') == 2


def test_a_refused_play_records_nothing(monkeypatch):
    monkeypatch.setattr(zi, "_post", lambda path, body, timeout=4.0: {"ok": False, "now": "listen"})
    zi._PLAYED_AS.pop("k-refused", None)
    assert zi.play("k-refused") is False and "k-refused" not in zi._PLAYED_AS


def test_a_first_boot_with_v2_worn_holds_v2s_walk_in_as_her_empty_office(monkeypatch):
    # the follow-up review of 539e9bc58: crissa-walk-in is not a row of v2's, so the first picture was Crissa at her desk
    monkeypatch.setattr(zi, "office_waits_on", lambda: True)
    monkeypatch.setattr(zi.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(zi, "needed", lambda: True)
    monkeypatch.setattr(zi, "marker_path", lambda: Path("/nonexistent/init.json"))
    monkeypatch.setattr(zi, "cues_mode", lambda: "voice")
    monkeypatch.setattr(zi, "no_desk_before_entrance", lambda: True)
    monkeypatch.delenv("CRISSA_INIT_SILENT", raising=False)
    roles = {zi.CRISSA_ENTRANCE: "v2-walk-in"}
    clips = {"v2-walk-in": {"file": "clip-v2-walk-in.mp4"}}
    assert zi.boot_office_key(clips, resolve=lambda k: roles.get(k, k)) == "v2-walk-in"
    assert zi.boot_office_key(clips) == ""                                    # the classic key alone finds nothing


def test_office_hold_keeps_the_clip_the_player_took(monkeypatch):
    monkeypatch.setattr(zi, "_post", lambda path, body, timeout=4.0: {"ok": True, "now": "v2-walk-in"})
    assert zi.office_hold() is True and zi.OFFICE["key"] == "v2-walk-in"
