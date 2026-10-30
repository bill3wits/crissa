"""Round 46 (the local review): the launcher's stop_strays matched a substring of the whole command line, so an editor
or a pager holding one of her scripts' paths was SIGTERMed with them. Now the script must be the program. Kill
CRISSA_STRAYS_BY_ARGV=0."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("crissa_launcher_r46", ROOT / "scripts" / "crissa.py")
Z = importlib.util.module_from_spec(spec)
spec.loader.exec_module(Z)
MINE = ["/k/scripts/crissa-align.py", "/k/scripts/house-speak.py"]


def test_her_programs_are_strays():
    assert Z._stray_argv("/usr/bin/python3 -u /k/scripts/house-speak.py --line hi", MINE)
    assert Z._stray_argv("python3 /k/scripts/crissa-align.py", MINE)
    assert Z._stray_argv("/k/scripts/house-speak.py --x", MINE)


def test_a_file_someone_has_open_is_not():
    assert not Z._stray_argv("vim /k/scripts/house-speak.py", MINE)
    assert not Z._stray_argv("less /k/scripts/crissa-align.py", MINE)
    assert not Z._stray_argv("python3 /k/other.py /k/scripts/house-speak.py", MINE)
    assert not Z._stray_argv("grep -n x /k/scripts/house-speak.py", MINE)


def test_the_kill(monkeypatch):
    assert Z.strays_by_argv_on() is True
    monkeypatch.setenv("CRISSA_STRAYS_BY_ARGV", "0")
    assert Z.strays_by_argv_on() is False
    src = (ROOT / "scripts" / "crissa.py").read_text(encoding="utf-8")
    assert "_stray_argv(cmd, mine) if strays_by_argv_on() else any(m in cmd for m in mine)" in src
