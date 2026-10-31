"""Init in the id module (modules/id/crissa_id/init_wizard.py, init_v2.py) on a pack, since 2026-10-10: a pack has ONE
first-boot walk, the player's lib/crissa_init.py (Connect, the machine card, Voice ID, face photos, done). Every public
entry of both id walkers hands over to it, and the module's own sequence file names the same four steps, so the
house's 13-step routine (and Init v2's eight) never walks in a pack. The walk itself is tests/test_crissa_init.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "modules" / "id" / "config" / "init-sequence.json"
for _p in (str(ROOT / "modules" / "id"), str(ROOT / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def walk(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("CRISSA_HOME", str(home))
    monkeypatch.setenv("CRISSA_INIT", "1")
    monkeypatch.setenv("CRISSA_INIT_SILENT", "1")
    monkeypatch.setenv("CRISSA_IDENTITY", str(home / "identity"))
    monkeypatch.setenv("CRISSA_INIT_WALKER", "standin")
    import crissa_home
    import crissa_init as zi
    import crissa_id.init_wizard as iw
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(zi, "SYNC", True)
    monkeypatch.setattr(zi, "_IDM", {"tried": False, "mods": None})
    monkeypatch.setattr(zi, "_machine_mod", lambda: None)
    monkeypatch.setattr(zi, "_voice_install_mod", lambda: None)
    monkeypatch.setattr(zi, "aligner_after_voice", lambda: {"state": "stood-in"})
    monkeypatch.setattr(zi, "_post", lambda path, body, timeout=4.0: {"ok": True})
    monkeypatch.setattr(iw, "_WALK", {"mod": None, "tried": False})
    zi.configure(base_url="http://conductor.invalid", home=home)
    yield {"zi": zi, "iw": iw, "home": home}
    zi.configure()


def test_the_shipped_sequence_is_the_short_walk():
    data = json.loads(SHIPPED.read_text(encoding="utf-8"))
    core = json.loads((ROOT / "config" / "init-sequence-pack.json").read_text(encoding="utf-8"))
    assert data["schema"] == "init_sequence/v2"
    assert data["steps"] == core["walk"], "the id module's copy and the player's are one walk"
    assert [s["id"] for s in data["steps"]] == ["connect", "machine", "voice_id", "face", "room", "hours"]
    text = SHIPPED.read_text(encoding="utf-8")
    for gone in ("welcome", "regulars_roster", "room_scan", "room_clock", "mornings", "news", "rehearsal", "crissa_seat",
                 "127.0.0.1"):
        assert gone not in text, gone


def test_every_entry_of_init_v1_hands_over(walk, monkeypatch):
    monkeypatch.setenv("CRISSA_CANCEL_LEAVES_NOTHING", "0")   # the 10-05 marker; round 34's cancel: test_round34_cancel
    iw, zi = walk["iw"], walk["zi"]
    assert iw._walk() is zi
    assert [s["id"] for s in iw.steps()] == ["connect", "machine", "voice_id", "face", "room", "hours"]
    assert iw.needed() is True
    snap = iw.start()
    assert snap["walker"] == "crissa" and snap["step_id"] == "connect" and snap["total"] == 5   # round 35: Face photos is not listed
    assert iw.snapshot()["run_id"] == snap["run_id"] == iw.attend()["run_id"]
    out = iw.apply("connect_later", {"run_id": snap["run_id"]})
    assert out["step_id"] == "voice_id", "the card had no machine module here: the voice, then Voice ID"
    snap = iw.abort()
    assert snap["status"] == "aborted"
    m = json.loads((walk["home"] / "init.json").read_text())
    assert m["done"] is False and m["steps_done"] == ["connect", "machine"]


def test_init_v2_hands_over_too_and_its_switch_chooses_nothing(walk, monkeypatch):
    import crissa_id.init_v2 as iv2
    monkeypatch.setenv("HOUSE_INIT_V2", "1")
    assert iv2.enabled() is True
    snap = iv2.apply("start", {})
    assert snap["walker"] == "crissa" and [s["id"] for s in iv2.steps()] == ["connect", "machine", "voice_id", "face", "room", "hours"]
    assert iv2.snapshot()["run_id"] == snap["run_id"] and iv2.needed() is True
    monkeypatch.delenv("HOUSE_INIT_V2")
    assert walk["iw"].apply("snapshot")["run_id"] == snap["run_id"]


def test_the_module_alone_names_its_six_steps(walk, monkeypatch):
    iw = walk["iw"]
    monkeypatch.setattr(iw, "_WALK", {"mod": None, "tried": True})       # no player's lib beside it
    import crissa_id.init_v2 as iv2
    assert [s["id"] for s in iw.steps()] == [s["id"] for s in iv2.steps()] == ["connect", "machine", "voice_id", "face", "room", "hours"]
    with pytest.raises(RuntimeError):
        iv2.apply("snapshot")


def test_the_site_example_names_no_address():
    text = (ROOT / "modules" / "id" / "config" / "site.example.json").read_text(encoding="utf-8")
    assert "127.0.0.1" not in text
    site = json.loads(text)
    assert site["audio"]["speak_url"] is None and site["rails"]["presence"] is None and site["rails"]["camera"] is None


def test_the_brain_check_reads_the_connect_endpoint_not_a_fixed_port():
    src = (ROOT / "modules" / "id" / "crissa_id" / "init_wizard.py").read_text(encoding="utf-8")
    i = src.index("def standard_ready")
    assert "127.0.0.1" not in src[i:i + 600] and "_paths.brain_url()" in src[i:i + 600]
