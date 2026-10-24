"""Round 45 (via the conductor from the User, D12): without krew, the picture build keeps only the IDs run's Lila clips
(lila-ids-* and the host plate enter / loop / leave), at the very end after every Crissa clip; krew installed brings all
of hers back; the house is untouched. Dial CRISSA_PREFILL_LILA = ids | all (all = round 44)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import pytest  # noqa: E402

import wall_ascii_disk as D  # noqa: E402
import crissa_home  # noqa: E402


@pytest.fixture
def pk(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.delenv("CRISSA_PREFILL_LILA", raising=False)
    z = {"listen": {"role": "loop"}, "work": {"role": "loop"}, "fl-chin": {"role": "flourish"},
         "lila-ids-welcome": {"role": "oneshot"}, "lila-ids-vid-facing": {"role": "oneshot"},
         "lila-ob-welcome": {"role": "host"}, "lila-reg-listen": {"role": "loop"}, "crissa-entrance": {"role": "oneshot"}}
    l = {"lila-listen": {"role": "loop"}, "lila-work": {"role": "loop"}, "lila-listen-enter": {"role": "slide"},
         "lila-listen-leave": {"role": "slide"}, "lila-fl-hair": {"role": "flourish"}}
    for d, rows in (("crissa-poses", z), ("lila-poses", l)):
        (tmp_path / "display" / d).mkdir(parents=True)
        (tmp_path / "display" / d / "inventory.json").write_text(json.dumps({"clips": rows}))
    (tmp_path / "avatars" / "crissa").mkdir(parents=True)
    (tmp_path / "avatars" / "crissa" / "manifest.json").write_text(json.dumps({"boot_loop": "listen"}))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "init-sequence-pack.json").write_text(json.dumps({"host": {
        "enter": "lila-listen-enter", "leave": "lila-listen-leave", "loop": "lila-reg-listen", "welcome": "lila-ob-welcome"}}))
    return tmp_path


def test_only_the_ids_runs_lila_clips_and_last(pk):
    order, loops = D.clip_order(pk, {"lila-ids-welcome": 3})
    lila = [k for k in order if k.startswith("lila")]
    assert set(lila) == {"lila-ids-welcome", "lila-ids-vid-facing", "lila-reg-listen", "lila-listen-enter",
                         "lila-listen-leave"}
    assert order[:len(order) - len(lila)] == [k for k in order if not k.startswith("lila")], "after every Crissa clip"
    assert order[-len(lila):][:3] == ["lila-listen-enter", "lila-listen-leave", "lila-reg-listen"]
    assert not any(k.startswith("lila") for k in loops)


def test_krew_brings_all_of_hers_back(pk):
    order, _ = D.clip_order(pk, {}, krew=True)
    assert {"lila-ob-welcome", "lila-fl-hair", "lila-work"} <= set(order)


def test_the_dial_and_the_house(pk, monkeypatch):
    assert "lila-fl-hair" in D.clip_order(pk, {}, env={"CRISSA_PREFILL_LILA": "all"})[0]
    assert D.prefill_lila({"CRISSA_PREFILL_LILA": "nonsense"}) == "ids"
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert "lila-fl-hair" in D.clip_order(pk, {})[0]


def test_the_build_asks_krew():
    src = (ROOT / "lib" / "wall_ascii_disk.py").read_text(encoding="utf-8")
    assert "krew=krew_here(" in src
