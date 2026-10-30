"""Round 40 (the User 2026-10-21: "just bake it in, it is so cheap. No one wants poor mouth placements if the fix is
free."): the launcher installs espeak-ng at start through this machine's own package manager (never shipped: GPL-3.0),
never asking anything; where it cannot, the letter rules stand and Setup's done plate names the command."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_espeak as E  # noqa: E402
import crissa_home  # noqa: E402


class R:
    def __init__(self, rc, err=""):
        self.returncode, self.stderr, self.stdout = rc, err, ""


def test_the_command_never_asks(monkeypatch):
    which = lambda n: "/usr/bin/" + n                                     # noqa: E731
    assert E.command("linux", which) == ["sudo", "-n", "apt-get", "install", "-y", "espeak-ng"], "-n: never a password prompt"
    assert E.command("darwin", which) == ["/usr/bin/brew", "install", "espeak-ng"]
    assert E.command("darwin", lambda n: None) is None and E.command("win32", which) is None


def test_ensure_installs_once_remembers_a_failure_and_never_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.delenv("CRISSA_ESPEAK_INSTALL", raising=False)
    monkeypatch.delenv("HOUSE_ESPEAK_DIR", raising=False)
    have = {"espeak-ng": None}
    which = lambda n: have.get(n, "/usr/bin/" + n) if n == "espeak-ng" else "/usr/bin/" + n   # noqa: E731
    runs, logs = [], []

    def run_ok(cmd, **kw):
        runs.append(cmd)
        have["espeak-ng"] = "/usr/bin/espeak-ng"
        return R(0)
    got = E.ensure(tmp_path, env={}, which=which, run=run_ok, log=logs.append, platform="linux")
    assert got["ok"] and runs == [["sudo", "-n", "apt-get", "install", "-y", "espeak-ng"]] and E.done_line(tmp_path, {}, which) == ""
    have["espeak-ng"] = None
    runs.clear()
    fail = lambda cmd, **kw: (runs.append(cmd), R(1, "sudo: a password is required"))[1]   # noqa: E731
    no_up = {"CRISSA_ESPEAK_UPSTREAM": "0"}                     # this test is the package manager's: no download
    got = E.ensure(tmp_path, env=no_up, which=which, run=fail, log=logs.append, platform="linux", now=1000.0)
    assert not got["ok"] and "password" in got["why"] and len(runs) == 1
    got = E.ensure(tmp_path, env=no_up, which=which, run=fail, log=logs.append, platform="linux", now=2000.0)
    assert not got["ok"] and len(runs) == 1, "not asked again on every start"
    assert "sudo apt install espeak-ng" in E.done_line(tmp_path, {}, which), "the done plate names the command"
    assert any("letter rules" in ln for ln in logs)


def test_never_on_the_house_and_the_kill(tmp_path, monkeypatch):
    runs = []
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: True)
    E.ensure(tmp_path, env={}, which=lambda n: None, run=lambda *a, **k: runs.append(a), log=lambda *a: None)
    assert runs == [] and E.done_line(tmp_path, {}, lambda n: None) == ""
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    E.ensure(tmp_path, env={"CRISSA_ESPEAK_INSTALL": "0"}, which=lambda n: None, run=lambda *a, **k: runs.append(a),
             log=lambda *a: None)
    assert runs == []


def test_the_launcher_starts_it_off_the_main_path():
    src = (ROOT / "scripts" / "crissa.py").read_text(encoding="utf-8")
    assert '_th.Thread(target=crissa_espeak.ensure, daemon=True, name="crissa-espeak",' in src
    # its words go to stderr: stdout is --service's one JSON document (2026-10-24)
    assert 'kwargs={"log": lambda m: print(m, file=sys.stderr, flush=True)}).start()' in src


REL = Path("/tmp/claude-1000/-home-agent-projects-house-rails/c3c558d8-a6e5-4516-a519-cd8ce56afa9b/scratchpad/piper-rel/piper_linux_x86_64.tar.gz")


def test_no_admin_fetches_the_pinned_upstream_build_with_the_network_stubbed(tmp_path, monkeypatch):
    """b3 / the User: "truly automatic and no prompt" - where sudo -n cannot run, the upstream release (rhasspy/piper
    2023.11.14-2, sha-pinned) is fetched into the state home; nothing of it ships."""
    import shutil
    import subprocess
    import pytest
    if not REL.is_file():
        pytest.skip("the upstream tarball is not cached on this machine")
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(E, "_arch", lambda: "x86_64")
    fetched = []

    def download(url, dst):                                   # the network, stubbed by the real artifact
        fetched.append(url)
        shutil.copyfile(REL, dst)
    fail = lambda cmd, **kw: R(1, "sudo: a password is required")   # noqa: E731
    got = E.ensure(tmp_path, env={}, which=lambda n: None if n == "espeak-ng" else "/usr/bin/" + n, run=fail,
                   log=lambda *a: None, platform="linux", download=download)
    assert got["ok"] and got["how"] == "upstream" and fetched == [E.UPSTREAM["x86_64"][0]]
    b = E.found({}, lambda n: None, tmp_path)
    assert b == str(tmp_path / "espeak-ng" / "espeak-ng")
    assert not (tmp_path / "espeak-ng" / "piper").exists() and not (tmp_path / "espeak-ng" / "libonnxruntime.so").exists(), \
        "espeak-ng's own files only"
    cmd, env = E.invocation(b)
    out = subprocess.run(cmd + ["-q", "-x", "-v", "en-us", "hello"], env=dict(env, PATH="/usr/bin:/bin"),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "h" in out.stdout and "oU" in out.stdout, (out.stdout, out.stderr)
    assert E.done_line(tmp_path, {}, lambda n: None) == "", "found: no done-plate line"


def test_a_wrong_download_is_refused_and_offline_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(E, "_arch", lambda: "x86_64")
    bad = E.fetch_upstream(tmp_path, download=lambda url, dst: Path(dst).write_bytes(b"not the release"), log=lambda *a: None)
    assert bad["ok"] is False and "sha256" in bad["why"] and not (tmp_path / "espeak-ng").exists()

    def offline(url, dst):
        raise OSError("network is unreachable")
    got = E.ensure(tmp_path, env={}, which=lambda n: None if n == "espeak-ng" else "/usr/bin/" + n,
                   run=lambda cmd, **kw: R(1, "no"), log=lambda *a: None, platform="linux", download=offline)
    assert got["ok"] is False and "unreachable" in got.get("upstream", "")
    assert "sudo apt install espeak-ng" in E.done_line(tmp_path, {}, lambda n: None), "offline: the done plate names it"
    assert E.upstream_on({"CRISSA_ESPEAK_UPSTREAM": "0"}) is False and E.upstream_on({"CRISSA_ESPEAK_INSTALL": "0"}) is False


def test_a_mac_app_from_finder_finds_homebrews_espeak_and_brew(tmp_path, monkeypatch):
    """house-rails-3e: an app opened from Finder has no /opt/homebrew/bin on its PATH."""
    import crissa_espeak as ze
    b = tmp_path / "opt-homebrew-bin"
    b.mkdir()
    (b / "espeak-ng").write_text("x")
    (b / "brew").write_text("x")
    monkeypatch.setattr(ze, "BREW_DIRS", (str(b),))
    monkeypatch.setattr(ze.sys, "platform", "darwin")
    env = {"HOME": str(tmp_path)}
    assert ze.found(env, which=lambda n: None, home=tmp_path / "state") == str(b / "espeak-ng")
    assert ze.command("darwin", which=lambda n: None) == [str(b / "brew"), "install", "espeak-ng"]
    assert ze.found(dict(env, CRISSA_ESPEAK_BREW_DIRS="0"), which=lambda n: None, home=tmp_path / "state") == ""
    monkeypatch.setattr(ze.sys, "platform", "linux")
    assert ze.found(env, which=lambda n: None, home=tmp_path / "state") == ""     # Linux never looks there
