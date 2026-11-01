"""`listen_loop` in avatars/<face>/manifest.json: optional, read by the player and the measure script (2026-10-02)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import avatar  # noqa: E402
import conductor  # noqa: E402


@pytest.fixture
def avatars(tmp_path, monkeypatch):
    def write(face, **keys):
        d = tmp_path / face
        d.mkdir(exist_ok=True)
        (d / "manifest.json").write_text(json.dumps(dict({"name": face, "poses_dir": "display/%s-lib" % face}, **keys)))
    monkeypatch.setenv("HOUSE_AVATARS_DIR", str(tmp_path))
    monkeypatch.delenv("CRISSA_LISTEN_LOOP", raising=False)
    monkeypatch.delenv("HOUSE_COMMS_CORES", raising=False)
    avatar.load.cache_clear()
    yield write
    avatar.load.cache_clear()


def test_declared_is_read_by_the_library_the_player_runs_on(avatars):
    avatars("ada", listen_loop="sofa")
    avatars("bo", listen_loop="window")
    assert avatar.listen_loop("/any/where/display/ada-lib") == "sofa"
    assert avatar.listen_loop(Path("display/bo-lib")) == "window"
    assert avatar.listen_loop("display/nobody-lib") is None


def test_undeclared_is_none_and_the_switch_reads_all_as_undeclared(avatars, monkeypatch):
    avatars("ada")
    avatars("bo", listen_loop="window")
    assert avatar.listen_loop("display/ada-lib") is None
    monkeypatch.setenv("CRISSA_LISTEN_LOOP", "0")
    assert avatar.listen_loop("display/bo-lib") is None


def test_by_name_when_no_library_is_given(avatars, monkeypatch):
    avatars("bo", listen_loop="window")
    monkeypatch.setenv("HOUSE_AVATAR", "bo")
    assert avatar.listen_loop() == "window"
    assert avatar.listen_loop(name="nobody") is None


def test_the_player_adds_the_declared_loop_and_nothing_else_moves(monkeypatch):
    monkeypatch.delenv("HOUSE_COMMS_CORES", raising=False)
    base = conductor.COMMS_CORES
    assert conductor.comms_cores_with(None) is base                      # undeclared pack: the list as it was
    assert conductor.comms_cores_with(base[0]) is base                    # already a core: unchanged, same order
    assert conductor.comms_cores_with("sofa") == base + ("sofa",)         # a made-up face gets its core
    monkeypatch.setenv("HOUSE_COMMS_CORES", "a,b")
    assert conductor.comms_cores_with("sofa") is base                     # a list named by hand is kept as given


def test_a_made_up_face_is_offered_its_declared_loop():
    clips = {"desk": {"role": "loop", "start": "down", "end": "down", "element": True},
             "sofa": {"role": "loop", "start": "down", "end": "down", "element": True}}
    seq = conductor.Sequencer(conductor.Graph(clips, {}), pool_name="work")
    assert seq.comms_offer() == []                                         # no key of this pack is written in code
    seq.comms_cores = conductor.comms_cores_with("sofa")
    assert seq.comms_offer() == ["sofa"]


@pytest.mark.parametrize("face", ["crissa", "lila"])
def test_the_house_manifests_declare_a_real_loop(face):
    m = json.loads((ROOT / "avatars" / face / "manifest.json").read_text(encoding="utf-8"))
    row = json.loads((ROOT / m["poses_dir"] / "inventory.json").read_text(encoding="utf-8"))["clips"][m["listen_loop"]]
    assert row["role"] == "loop" and (row.get("mouth") or row.get("mouth_sidecar"))


def test_crissas_declared_loop_changes_nothing_in_the_house():
    m = json.loads((ROOT / "avatars" / "crissa" / "manifest.json").read_text(encoding="utf-8"))
    assert conductor.comms_cores_with(m["listen_loop"]) is conductor.COMMS_CORES
