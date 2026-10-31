"""Better mouths' default (lib/crissa_aligner.default_on).

Since 2026-10-10 it FOLLOWS THE MACHINE CHECK, not RAM (tests/test_crissa_machine_card.py): the measured align stage
against its bar, else on (2026-10-10: Init installs it after the voice, every machine). The RAM rule of 2026-10-07 (ticked by default on a machine with plenty of
RAM: config/crissa-machine-tiers.json better_mouths.default_on_ram_gb, 16 GB) is the kill switch now,
CRISSA_ALIGNER_RAM_RULE=1, and these tests pin it under that switch. The person's own choice always wins; the house is
never touched. No download here: the install is a stand-in."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
CFG = json.loads((ROOT / "config" / "crissa-machine-tiers.json").read_text())


@pytest.fixture()
def A(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "home"))
    for k in ("CRISSA_BETTER_MOUTHS", "CRISSA_BETTER_MOUTHS_DEFAULT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CRISSA_ALIGNER_RAM_RULE", "1")      # the rule these tests pin is the kill switch's (2026-10-10)
    import crissa_home
    import crissa_aligner
    importlib.reload(crissa_home)
    mod = importlib.reload(crissa_aligner)
    monkeypatch.setattr(mod.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(mod, "running", lambda: False)
    started = []
    monkeypatch.setattr(mod.threading, "Thread", lambda target, name, daemon: type(
        "T", (), {"start": lambda self: started.append(name)})())
    mod._started = started
    yield mod
    monkeypatch.undo()
    importlib.reload(crissa_home)
    importlib.reload(crissa_aligner)


def test_the_bar_is_config_with_its_measurement():
    bm = CFG["better_mouths"]
    assert bm["default_on_ram_gb"] == 16 and "0.62 GB" in bm["measured"]
    import crissa_aligner
    assert crissa_aligner.DEFAULT_ON_RAM_GB == bm["default_on_ram_gb"], "the code's copy matches the file"
    import crissa_machine
    assert "better_mouths" not in crissa_machine.thresholds(), "the machine check's bars are unchanged"


@pytest.mark.parametrize("ram, ticked", [(91.0, True), (16.0, True), (15.5, False), (8.0, False), (None, False)])
def test_ticked_by_default_only_with_plenty_of_ram(A, monkeypatch, ram, ticked):
    monkeypatch.setattr(A, "ram_gb", lambda: ram)
    assert A.better() is ticked and A.status()["by"] == "default"


def test_the_person_s_choice_wins_and_is_kept(A, monkeypatch):
    monkeypatch.setattr(A, "ram_gb", lambda: 91.0)
    monkeypatch.setattr(A, "installed", lambda: True)
    monkeypatch.setattr(A, "stop", lambda: None)
    A.set_better(False)
    assert A.better() is False and A.status()["by"] == "you"
    A.boot()
    assert A.better() is False and A._started == [], "an untick survives a restart"


def test_boot_switches_it_on_and_installs_when_ticked_by_default(A, monkeypatch):
    monkeypatch.setattr(A, "ram_gb", lambda: 91.0)
    A.boot()
    assert A._started == ["better-mouths-install"]
    assert json.loads(A.SETTINGS.read_text()) == {"better": True, "by": "default"}
    assert A.status()["by"] == "default"


def test_a_small_machine_installs_nothing_at_boot(A, monkeypatch):
    monkeypatch.setattr(A, "ram_gb", lambda: 8.0)
    A.boot()
    assert A._started == [] and not A.SETTINGS.exists()


def test_the_default_has_its_own_switch_and_the_state_home_moves_the_bar(A, monkeypatch):
    monkeypatch.setattr(A, "ram_gb", lambda: 12.0)
    assert A.better() is False
    home = Path(A.crissa_home.HOME)
    home.mkdir(parents=True, exist_ok=True)
    (home / "crissa-machine-tiers.json").write_text(json.dumps({"better_mouths": {"default_on_ram_gb": 12}}))
    assert A.default_on_ram_gb() == 12.0 and A.better() is True
    monkeypatch.setenv("CRISSA_BETTER_MOUTHS_DEFAULT", "0")
    assert A.better() is False


def test_the_house_is_never_ticked(A, monkeypatch):
    monkeypatch.setattr(A, "ram_gb", lambda: 91.0)
    monkeypatch.setattr(A.crissa_home, "is_house", lambda *a, **k: True)
    assert A.better() is False and A.default_on() is False
    A.boot()
    assert A._started == [] and not A.SETTINGS.exists()


def test_the_connect_page_says_when_it_was_ticked_for_you():
    page = (ROOT / "display" / "crissa-connect.html").read_text()
    assert 'm.by === "default" || m.by === "init"' in page and "plenty of memory" in page
    assert "m.default_by" in page and "the machine check timed the aligner" in page
    assert "it is on by default and installs during setup" in page, "the default since 2026-10-10: on, Init installs it"


def test_without_the_kill_switch_ram_decides_nothing(A, monkeypatch):
    monkeypatch.delenv("CRISSA_ALIGNER_RAM_RULE")
    monkeypatch.setattr(A, "ram_gb", lambda: 91.0)
    monkeypatch.setattr(A, "installed", lambda: False)
    assert A.default_by() == "default" and A.better() is True, "on by default on every machine (2026-10-10)"
    monkeypatch.setattr(A, "ram_gb", lambda: 4.0)
    assert A.better() is True, "RAM decides nothing: a small machine is on too"
    A.boot()
    assert A._started == [] and not A.SETTINGS.exists(), "a player's start downloads nothing by itself: Init does"
