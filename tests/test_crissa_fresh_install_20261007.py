"""The 2026-10-07 Ubuntu retest of a fresh clone (the User): three install bugs, each pinned here.

1. scripts/crissa.py said "Permission denied": the entry scripts must come out 755 in the built repo.
2. Without the app's packages the launcher stopped with a cv2 traceback: it checks them first, uses the README's
   .venv when that has them, else says the one command in plain words and exits 2.
3. The startup log was too technical: a few plain lines on the console, every line in the full log, and any cut
   says so and names the full log; --verbose prints everything as before.
"""
from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The export and the repo builder live in the source repo only; a public clone runs the launcher tests and skips these.
BUILD_TOOLS = (ROOT / "scripts" / "crissa-export.py").is_file() and (ROOT / "scripts" / "crissa-build-repos.py").is_file()
EX = _load("crissa_export_fresh", "scripts/crissa-export.py") if BUILD_TOOLS else None
BR = _load("crissa_build_repos_fresh", "scripts/crissa-build-repos.py") if BUILD_TOOLS else None
needs_build_tools = pytest.mark.skipif(not BUILD_TOOLS, reason="the export and repo builder are not in a public clone")
_ENTRY_SCRIPTS_PUBLIC = ("scripts/crissa.py", "scripts/house-conductor.py", "scripts/crissa-ear.py",
                         "scripts/crissa-voice-tier.py", "scripts/crissa-panel-server.py", "scripts/crissa-mcp.py")
ENTRY_SCRIPTS = BR.ENTRY_SCRIPTS if BR else _ENTRY_SCRIPTS_PUBLIC


@needs_build_tools
def test_the_public_copy_of_the_entry_list_matches_the_builder():
    assert BR.ENTRY_SCRIPTS == _ENTRY_SCRIPTS_PUBLIC

# every script a README line or the launcher runs by name, in the wall pack and its two modules
SHIPPED_ENTRIES = ENTRY_SCRIPTS + ("modules/oracle/scripts/crissa-oracle.py",
                                      "modules/voice/scripts/crissa-multilingual.py")


# ---- 1. the execute bit -----------------------------------------------------------------------------------------

def test_the_entry_scripts_are_committed_executable():
    """The export takes modes from git (git archive): a 100644 entry is a 600 file in the pack."""
    if not (ROOT / ".git").exists():
        pytest.skip("not a git tree")
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "--", *SHIPPED_ENTRIES],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    modes = {line.split("\t")[1]: line.split()[0] for line in out}
    assert set(modes) == set(SHIPPED_ENTRIES), "an entry script is not in the tree"
    assert {k: v for k, v in modes.items() if v != "100755"} == {}


def _pack(tmp: Path) -> Path:
    """A pack the way the export writes it: files from git archive at 644 (the 100644 the retest hit)."""
    out = tmp / "pack"
    for rel in BR.ENTRY_SCRIPTS + ("lib/crissa_home.py", "lib/tool.py", "scripts/helper.json"):
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/usr/bin/env python3\n" if rel.endswith(".py") else "{}\n")
        os.chmod(p, 0o644)
    return out


@needs_build_tools
def test_the_lock_makes_an_entry_script_runnable_even_when_the_ref_carried_644(tmp_path):
    out = _pack(tmp_path)
    EX.lock_down(out)
    for rel in BR.ENTRY_SCRIPTS:
        assert stat.S_IMODE((out / rel).stat().st_mode) == 0o700, rel
    # only a #! file in a scripts/ folder: a library module with a shebang and a data file stay 600
    assert stat.S_IMODE((out / "lib" / "tool.py").stat().st_mode) == 0o600
    assert stat.S_IMODE((out / "scripts" / "helper.json").stat().st_mode) == 0o600


@needs_build_tools
def test_the_built_repo_keeps_the_exec_bit_on_the_entry_scripts(tmp_path):
    """export lock_down -> build-repos fix_modes: what git commits in the public repo is 100755."""
    out = _pack(tmp_path)
    EX.lock_down(out)
    BR.fix_modes(out)
    for rel in BR.ENTRY_SCRIPTS:
        assert stat.S_IMODE((out / rel).stat().st_mode) == 0o755, rel
    assert stat.S_IMODE((out / "lib" / "tool.py").stat().st_mode) == 0o644
    if subprocess.run(["git", "--version"], capture_output=True).returncode == 0:
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(["git", "init", "-q", str(out)], check=True, env=env)
        subprocess.run(["git", "-C", str(out), "add", "-A"], check=True, env=env)
        got = subprocess.run(["git", "-C", str(out), "ls-files", "-s", "scripts/crissa.py"],
                             capture_output=True, text=True, check=True, env=env).stdout
        assert got.startswith("100755 ")


@needs_build_tools
def test_the_build_refuses_an_entry_script_that_lost_its_bit(tmp_path):
    out = _pack(tmp_path)                       # never locked: still 644, the old bug
    with pytest.raises(AssertionError, match="scripts/crissa.py"):
        BR.fix_modes(out)


# ---- 2. the packages, checked before anything starts --------------------------------------------------------------

ZL = _load("crissa_launcher_fresh", "scripts/crissa.py")


def test_the_check_covers_every_base_requirement():
    """In the source: the exporter's requirements + README text. In a public clone: the shipped requirements.txt + README."""
    reqs = EX.REQUIREMENTS if EX else (ROOT / "requirements.txt").read_text(encoding="utf-8")
    readme = EX.FACES_BODY if EX else (ROOT / "README.md").read_text(encoding="utf-8")
    base = [ln.split(">")[0].split("<")[0].split("=")[0].strip() for ln in reqs.splitlines()
            if ln.strip() and not ln.startswith("#")]
    if EX:      # the pack's own requirements: exactly what the launcher checks
        assert sorted(pip for _, pip in ZL.APP_PACKAGES) == sorted(base)
    else:       # the public repo's requirements also carry the Control Panel's: the launcher's are all in it
        assert {pip for _, pip in ZL.APP_PACKAGES} <= set(base)
    assert ZL.SETUP_CMD in readme, "the message names the README's own command"


def test_missing_packages_say_the_one_command_and_exit_2(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(ZL, "missing_packages", lambda python=None: ["opencv-python-headless"])
    monkeypatch.setattr(ZL, "venv_python", lambda root=None: None)
    assert ZL.packages_ready([], env={"CRISSA_PACKAGE_INSTALL": "0"}) == 2
    err = capsys.readouterr().err
    assert "Crissa needs its packages installed first (missing: opencv-python-headless)" in err
    assert ZL.SETUP_CMD in err and "Traceback" not in err


def test_a_venv_that_has_them_is_used_once(monkeypatch, tmp_path, capsys):
    vpy = tmp_path / ".venv" / "bin" / "python"
    vpy.parent.mkdir(parents=True)
    vpy.write_text("")
    monkeypatch.setattr(ZL, "venv_python", lambda root=None: vpy)
    monkeypatch.setattr(ZL, "missing_packages", lambda python=None: [] if python else ["numpy"])
    monkeypatch.delenv("CRISSA_VENV_SWITCHED", raising=False)
    calls = []
    ZL.packages_ready(["--no-open"], env={}, execv=lambda exe, argv: calls.append((exe, argv)))
    assert calls == [(str(vpy), [str(vpy), str(ROOT / "scripts" / "crissa.py"), "--no-open"])]
    assert "using the packages in" in capsys.readouterr().out
    calls.clear()                                  # already switched once: never loop, say the command instead
    assert ZL.packages_ready([], env={"CRISSA_VENV_SWITCHED": "1", "CRISSA_PACKAGE_INSTALL": "0"},
                             execv=lambda *x: calls.append(x)) == 2
    assert calls == []


def test_a_venv_is_told_apart_by_its_prefix_not_its_interpreter_file(monkeypatch, tmp_path):
    """Two venvs made from one Python point at the same base interpreter: realpath cannot tell the bare one
    the launcher runs under from the .venv that has the packages (found in the real run, 2026-10-07)."""
    import sys
    a, b = tmp_path / "bare", tmp_path / ".venv"
    for v in (a, b):
        if subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(v)], capture_output=True).returncode:
            pytest.skip("no venv module")
    assert os.path.realpath(a / "bin" / "python") == os.path.realpath(b / "bin" / "python")
    monkeypatch.setattr(sys, "prefix", str(a))
    monkeypatch.setattr(ZL, "venv_python", lambda root=None: b / "bin" / "python")
    monkeypatch.setattr(ZL, "missing_packages", lambda python=None: [] if python else ["numpy"])
    monkeypatch.delenv("CRISSA_VENV_SWITCHED", raising=False)
    calls = []
    ZL.packages_ready([], env={}, execv=lambda exe, argv: calls.append(exe))
    assert calls == [str(b / "bin" / "python")]
    monkeypatch.setattr(sys, "prefix", str(b))        # already inside it, still missing: say so, never loop
    monkeypatch.setattr(ZL, "missing_packages", lambda python=None: ["numpy"])
    calls.clear()
    assert ZL.packages_ready([], env={"CRISSA_PACKAGE_INSTALL": "0"},
                             execv=lambda exe, argv: calls.append(exe)) == 2 and calls == []


def test_the_check_has_a_kill_switch_and_passes_when_nothing_is_missing(monkeypatch):
    assert ZL.packages_ready([], env={"CRISSA_PACKAGE_CHECK": "0"}) is None
    monkeypatch.setattr(ZL, "missing_packages", lambda python=None: [])
    assert ZL.packages_ready([], env={}) is None


def test_a_python_without_the_packages_gets_plain_words_not_a_traceback(tmp_path):
    """For real: a bare venv (no numpy, no cv2) runs the launcher."""
    import sys
    venv = tmp_path / "bare"
    if subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], capture_output=True).returncode:
        pytest.skip("no venv module")
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CRISSA_", "HOUSE_", "PYTHON"))}
    env.update(HOME=str(home), CRISSA_HOME=str(home / ".crissa"), CRISSA_PACKAGE_INSTALL="0")
    r = subprocess.run([str(venv / "bin" / "python"), str(ROOT / "scripts" / "crissa.py"), "--no-open", "--no-voice",
                        "--allow-house", "--port-offset", "600"], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "Traceback" not in r.stdout + r.stderr
    assert "Crissa needs its packages installed first" in r.stderr and "numpy" in r.stderr


# ---- 3. a few plain lines; the detail in the full log -------------------------------------------------------------

@pytest.fixture
def con(tmp_path, monkeypatch):
    c = ZL.Console()
    c.path = tmp_path / "logs" / "crissa.log"
    monkeypatch.setattr(ZL, "CON", c)
    return c


def test_detail_goes_to_the_full_log_and_the_cut_is_said(con, capsys):
    ZL._say("Starting Crissa (Crissa)…")
    ZL._log("player  127.0.0.1:8700")
    ZL._log("voice: pocket (picked now) - 4 cores, 15 GB")
    ZL._log("ready: http://127.0.0.1:8700/connect  (voice plays on the computer)")
    ZL._cut_note()
    out = capsys.readouterr().out
    assert "127.0.0.1:8700  " not in out and "picked now" not in out and "crissa:" not in out
    assert out.splitlines()[0] == "Starting Crissa (Crissa)…"
    assert "(3 more lines not shown here: the full log is %s; --verbose prints everything)" % con.path in out
    full = con.path.read_text()
    for line in ("crissa: player  127.0.0.1:8700", "crissa: voice: pocket (picked now)", "crissa: Starting Crissa"):
        assert line in full
    ZL._cut_note()
    assert capsys.readouterr().out == "", "a cut is said once, not again with nothing new held back"


def test_an_error_always_shows_in_plain_words_and_names_the_full_log(con, capsys):
    ZL._log("the player did not come up: Traceback ... ModuleNotFoundError", error=True,
            plain="Crissa could not start: her player stopped before it was ready.")
    out = capsys.readouterr().out
    assert out.startswith("Crissa could not start") and "Traceback" not in out and str(con.path) in out
    assert "Traceback" in con.path.read_text()


def test_verbose_prints_every_line_as_before(con, capsys):
    con.verbose = True
    ZL._log("player  127.0.0.1:8700")
    ZL._log("lane tts went down: exit 1", *ZL.lane_plain("tts went down: exit 1"))
    ZL._cut_note()
    assert capsys.readouterr().out == "crissa: player  127.0.0.1:8700\ncrissa: lane tts went down: exit 1\n"


def test_lane_events_in_plain_words():
    assert ZL.lane_plain("pocket went down: exit 1 (killed)") == ("Her voice (Pocket) stopped; Crissa is starting it again", True)
    assert ZL.lane_plain("stt answering again") == ("Listening is back.", False)
    assert ZL.lane_plain("tts restarted (restart 2): exit 1") == (None, False)
    assert ZL.lane_plain("asr given up after 5 restarts")[1] is True


def test_the_voice_line_is_plain(monkeypatch):
    import crissa_home
    monkeypatch.setattr(crissa_home, "her_name", lambda paths=None: "Crissa")   # a pack: no house config

    class A:
        face, no_voice, voice, tts_url, tts = "crissa", False, Path("/v"), "", ""
        tier = {"main": "pocket"}
    assert ZL.voice_plain(A(), installing=True).startswith("Crissa's voice: Pocket TTS (installing in the background")
    A.no_voice = True
    assert ZL.voice_plain(A()) == "Crissa's voice: not started (--no-voice)"
    A.face = "lila"
    assert ZL.voice_plain(A()) == "Lila's voice: not started (--no-voice)", "another face keeps its own name"


def test_the_launcher_has_a_verbose_flag():
    src = (ROOT / "scripts" / "crissa.py").read_text()
    assert '"--verbose"' in src and "CRISSA_VERBOSE" in src
