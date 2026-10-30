"""Init installs Crissa's voice (the User 2026-10-07, the fresh-install test on Ubuntu): picked by what the machine can
run (Turbo on a strong GPU, Pocket on the CPU, the Mac's say, text - exactly that order), installed during setup,
falling down the chain when an install fails, and said in plain words. No installer runs here: the runner and the
installed() check are fakes."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_home  # noqa: E402
import crissa_voice_install as VI  # noqa: E402
import crissa_voice_tiers as VT  # noqa: E402


# Audio8 as a VOICE is behind its switch since 2026-10-17 (the User: Audio8 is her ear, Pocket her voice; lib/
# crissa_voice_tiers.not_a_voice). These tests pin the Audio8-voice path that the switch keeps; the rule itself is
# tests/test_audio8_not_a_voice_20261017.py.
@pytest.fixture(autouse=True)
def _audio8_may_speak(monkeypatch):
    monkeypatch.setenv("CRISSA_AUDIO8_TTS", "1")

ENV_NAME = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b")


@pytest.fixture
def pack(tmp_path, monkeypatch):
    monkeypatch.setattr(VT, "is_house", lambda: False)
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(crissa_home, "her_name", lambda paths=None: "Crissa")   # a pack: no house config
    monkeypatch.setattr(VT, "broken", lambda: {})
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "0")
    monkeypatch.delenv(VI.SWITCH, raising=False)
    monkeypatch.setenv(VI.ANN_SWITCH, "0")   # Crissa's own voice here; the announcer's install: test_crissa_voice_capability
    return tmp_path


def _facts(os_name="linux", arch="x86_64", ram=16.0, gpu=None, macos="", lang="en"):
    f = {"os": os_name, "arch": arch, "cpu_cores": 8, "ram_gb": ram, "python": "3.14", "piper_model": False,
         "gpu": gpu or {"kind": "none"}, "language": {"code": lang, "name": lang, "from": "test"}}
    if os_name == "darwin":
        f["macos"] = macos
        if arch == "arm64":
            f["gpu"] = {"kind": "mps", "name": "Apple M2", "unified_gb": ram}
    return f


CUDA = {"kind": "cuda", "name": "NVIDIA RTX 4090", "total_gb": 24.0, "free_gb": 23.0, "count": 1}


# ---- 1. the tier is chosen by what the machine can run -----------------------------------------------------------

@pytest.mark.parametrize("facts, main, chain", [
    (_facts(gpu=CUDA, ram=64.0), "turbo", ["turbo", "pocket", "text"]),
    (_facts(ram=8.0), "pocket", ["pocket", "kokoro", "text"]),
    (_facts(gpu=dict(CUDA, free_gb=3.0), ram=32.0), "audio8", ["audio8", "pocket", "text"]),   # no usable GPU, robust
    (dict(_facts(gpu=dict(CUDA, free_gb=3.0), ram=32.0), cpu_cores=4), "pocket", ["pocket", "kokoro", "text"]),
    (_facts("darwin", "arm64", 8.0, macos="14.5"), "pocket", ["pocket", "kokoro", "say", "text"]),
    (_facts("darwin", "arm64", 16.0, macos="13.6"), "say", ["say", "text"]),
    (_facts("darwin", "x86_64", 16.0, macos="14.5"), "audio8", ["audio8", "say", "text"]),   # robust Intel Mac
    (dict(_facts("darwin", "x86_64", 16.0, macos="14.5"), cpu_cores=4), "say", ["say", "text"]),
    (_facts("darwin", "arm64", 16.0, macos="14.5"), "audio8", ["audio8", "pocket", "say", "text"]),
    (_facts(ram=32.0), "audio8", ["audio8", "pocket", "text"]),             # a robust CPU box: Audio8, Pocket behind
    (_facts("windows", "amd64", 16.0), "text", ["text"]),
])
def test_the_voice_is_picked_by_machine_capability_in_exactly_init_s_order(pack, facts, main, chain):
    rec = VT.pick(facts, VT.table(pack))
    assert rec["main"] == main and rec["chain"] == chain
    for never in ("multilingual", "turbo_mlx", "piper"):
        assert never not in rec["chain"], "Init never picks %s" % never
    if rec["main"] == "pocket" and facts["language"]["code"] == "en":
        assert rec["chain"][1] == "kokoro", "Kokoro backs Pocket on a weak machine and an 8 GB Mac (2026-10-08)"
    else:
        assert "kokoro" not in rec["chain"], "Kokoro stays out of a Turbo or Audio8 chain"
    robust = facts["ram_gb"] >= 16 and facts["cpu_cores"] >= 8 and facts["os"] in ("linux", "darwin") and not (
        facts["os"] == "darwin" and facts.get("macos", "") < "14")
    assert ("audio8" in rec["chain"]) == (robust and main != "turbo"), \
        "Audio8 is her voice on a robust machine with no GPU voice, never on a GPU machine's chain (the User 2026-10-07)"


def test_init_s_order_lives_in_config_and_kokoro_stays_nameable(pack):
    tab = json.loads((ROOT / "config" / "crissa-voice-tiers.json").read_text())
    assert tab["order"] == ["turbo", "audio8", "pocket", "kokoro", "say", "text"]
    assert tab["announcer"]["order"] == ["audio8", "pocket", "kokoro", "say", "text"]
    assert tab["tiers"]["kokoro"]["backup_only_for"] == "pocket"
    assert tab["tiers"]["kokoro"]["stands_back_for"] == ["turbo", "audio8", "multilingual", "turbo_mlx"]
    assert tab["tiers"]["pocket"]["backup"] == ["kokoro", "say"] and tab["tiers"]["pocket"]["installs_backup"] is True
    assert tab["tiers"]["pocket"]["settings"]["voices"]["crissa"] == "crissa"   # round 28: her own voice
    assert tab["tiers"]["kokoro"]["settings"]["voices"]["crissa"] == "af_heart"
    assert VT.DEFAULT_TIERS["kokoro"]["backup_only_for"] == "pocket"
    assert VT.DEFAULT_TIERS["pocket"]["installs_backup"] is True
    assert "kokoro" in tab["tiers"] and "kokoro" in VT.ENGINES, "a person may still name Kokoro"
    assert VT.DEFAULT_ORDER == tab["order"], "the code's fallback copy matches the shipped table"


# ---- 2. the install, and the fallback chain when one fails ---------------------------------------------------------

class World:
    """Fake installers: `fail` names the engines whose install fails, with the installer's own last words."""

    def __init__(self, fail: dict):
        self.fail, self.have, self.ran = fail, set(), []

    def installed(self, eng):
        return None if eng in ("say", "text", "piper") else eng in self.have

    def runner(self, argv, progress):
        stem = Path(argv[1]).stem.replace("crissa-", "")
        eng = {"tts": "kokoro"}.get(stem, stem)   # Kokoro's installer is crissa-tts.py
        self.ran.append(argv)
        progress("%s: fetching ..." % eng)
        if eng in self.fail:
            return 2, "crissa-%s: %s" % (eng, self.fail[eng])
        self.have.add(eng)
        return 0, "installed"


def _rec(home, facts):
    rec = dict(VT.pick(facts, VT.table(home)), schema=VT.SCHEMA, facts=facts, picked_by="init")
    VT._write(rec, home)
    return rec


def test_turbo_is_installed_on_a_strong_gpu_and_the_command_is_the_module_installer(pack):
    rec = _rec(pack, _facts(gpu=CUDA, ram=64.0))
    w = World({})
    started = []
    res = VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, rec=rec,
                 start=started.append)
    assert res["chosen"] == "turbo" and res["ok"] and started == ["turbo"]
    argv = w.ran[0]
    assert argv[0] == sys.executable and argv[1].endswith("modules/voice/scripts/crissa-turbo.py")
    assert argv[2:] == ["--install"] and len(w.ran) == 1
    assert res["line"] == "Crissa's voice is ready: Chatterbox-Turbo."


def test_a_failed_turbo_install_falls_to_pocket_and_the_record_says_so(pack):
    rec = _rec(pack, _facts(gpu=CUDA, ram=64.0))
    w = World({"turbo": "Turbo's self-test failed: no kernel image for this GPU (set CRISSA_TURBO_CUDA=0)"})
    shown = []
    res = VI.run(pack, progress=shown.append, runner=w.runner, installed=w.installed, rec=rec)
    assert [Path(a[1]).name for a in w.ran] == ["crissa-turbo.py", "crissa-pocket.py"]
    assert res["chosen"] == "pocket" and not res["ok"]
    assert res["line"] == ("Chatterbox-Turbo could not be installed: Turbo's self-test failed: no kernel image for "
                           "this GPU. She will speak with Pocket TTS for now; you can retry from the Connect page.")
    kept = VT.read(pack)
    assert kept["main"] == "pocket" and kept["chain"] == ["pocket", "text"] and kept["picked_by"] == "init"
    assert kept["voice_install"]["first"] == "turbo" and kept["voice_install"]["chosen"] == "pocket"
    assert [t["ok"] for t in kept["voice_install"]["tried"]] == [False, True]
    assert any(s.startswith("Installing Crissa's voice: Chatterbox-Turbo") for s in shown)


def test_every_install_failing_leaves_text_and_says_it_plainly(pack):
    rec = _rec(pack, _facts(gpu=CUDA, ram=64.0))
    w = World({"turbo": "no space left on device", "pocket": "no space left on device"})
    res = VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, rec=rec)
    assert res["chosen"] == "text"
    assert res["line"] == ("Crissa's voice could not be installed: no space left on device. She will speak in text "
                           "for now; you can retry from the Connect page.")
    assert VT.read(pack)["main"] == "text"


def test_a_mac_whose_pocket_install_fails_speaks_with_kokoro_then_say(pack):
    rec = _rec(pack, _facts("darwin", "arm64", 8.0, macos="14.5"))
    assert rec["chain"] == ["pocket", "kokoro", "say", "text"]
    w = World({"pocket": "torch has no wheel for this Mac"})
    res = VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, rec=rec)
    assert res["chosen"] == "kokoro" and "She will speak with Kokoro for now" in res["line"]
    w = World({"pocket": "torch has no wheel for this Mac", "kokoro": "no wheel for this Mac"})
    res = VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, rec=rec)
    assert res["chosen"] == "say" and "She will speak with the Mac's own voice for now" in res["line"]


def test_an_installed_voice_is_not_installed_again(pack):
    rec = _rec(pack, _facts(ram=8.0))
    w = World({})
    w.have.add("pocket")
    res = VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, rec=rec)
    assert res["chosen"] == "pocket" and res["ok"]
    assert [Path(a[1]).name for a in w.ran] == ["crissa-tts.py"], "Pocket is not fetched again; Kokoro, its backup, is"
    assert res["backup"]["chosen"] == "kokoro"


def test_kill_search_installs_nothing_and_says_why(pack, monkeypatch):
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "1")
    rec = _rec(pack, _facts(ram=8.0))
    w = World({})
    res = VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, rec=rec)
    assert w.ran == [] and res["chosen"] == "text"
    assert "downloads are switched off on this machine" in res["line"]


def test_the_switch_and_the_house_turn_it_off(pack, monkeypatch):
    assert VI.enabled()
    monkeypatch.setenv(VI.SWITCH, "0")
    assert not VI.enabled()
    monkeypatch.delenv(VI.SWITCH)
    monkeypatch.setattr(VT, "is_house", lambda: True)
    assert not VI.enabled(), "the house runs its own voices and never installs one here"


# ---- 3. nothing the person reads names a setting or a command ------------------------------------------------------

def test_plain_reasons_drop_setting_names_commands_and_tracebacks():
    out = VI.plain("Traceback (most recent call last):\n  File x\ncrissa-pocket: Pocket needs Python 3.10 to 3.12. "
                   "Run it with python3.12 (CRISSA_TTS=pocket) or `uv venv --python 3.12`.")
    assert "python3" not in out and "CRISSA" not in out and "uv venv" not in out and "Traceback" not in out
    assert out.startswith("Pocket needs Python 3.10 to 3.12")


def test_user_facing_voice_lines_name_no_setting_or_command(pack, monkeypatch):
    import crissa_tts as T
    lines = [VT.missing_line({"main": "text"})]
    monkeypatch.setattr(VT, "installed", lambda e: False)
    lines.append(VT.missing_line({"main": "pocket", "chain": ["pocket", "text"]}))
    rec = {"schema": VT.SCHEMA, "main": "turbo", "chain": ["turbo", "pocket", "say", "text"]}
    monkeypatch.setattr(T, "_house", lambda: False)
    monkeypatch.setattr(T, "_tier_record", lambda home=None: rec)
    monkeypatch.setattr(T, "stream_up", lambda name, e=None: False)
    monkeypatch.setattr(T, "_platform", lambda: "darwin")
    got = T._by_tier_walk({}, {"CRISSA_TTS_DEFAULT_POCKET": "0", "CRISSA_TTS_DEFAULT_SAY": "0"}, pack)
    assert got == {}
    # the walk's own passed-over lines: rebuild them the way voice-state.json gets them
    passed = []
    orig = T._said
    monkeypatch.setattr(T, "_said", lambda p, r: passed.extend(p) or orig(p, r))
    monkeypatch.setattr(T, "mac_default", lambda e=None: True)
    T._by_tier_walk({}, {"CRISSA_TTS_DEFAULT_POCKET": "0"}, pack)
    lines += passed
    lines.append(VI.line({"chosen": "text", "first": "pocket", "tried": [{"engine": "pocket", "ok": False,
                                                                           "why": "no space left on device"}]}))
    for bad in (lambda: T._openai("hi", {"engine": "openai"}, "crissa", 1.0),):
        with pytest.raises(T.TTSError) as e:
            bad()
        lines.append(str(e.value))
    assert passed and len(lines) >= 5
    for s in lines:
        assert not ENV_NAME.search(s), s
        assert "python3" not in s and "--install" not in s, s


def test_init_s_voice_lines_on_the_plate_name_no_setting(pack, monkeypatch):
    import crissa_init as zi
    monkeypatch.setattr(zi, "SYNC", True)
    zi.configure(home=pack)
    try:
        class Mod:
            plain = staticmethod(VI.plain)

            @staticmethod
            def run(home, progress, retry, start):
                progress("pocket: building the env (CRISSA_VOICE_PYENV=1) ...")
                return {"ok": False, "chosen": "text", "line": VI.line(
                    {"chosen": "text", "first": "pocket", "tried": [{"engine": "pocket", "ok": False,
                                                                       "why": VI.plain("crissa-pocket: disk full")}]})}

            @staticmethod
            def launcher_start(home):
                return None
        monkeypatch.setattr(zi, "_voice_install_mod", lambda: Mod)
        got = zi.voice_install("")
        assert got["state"] == "failed"
        assert got["line"] == ("Crissa's voice could not be installed: disk full. She will speak in text for now; you "
                               "can retry from the Connect page.")
    finally:
        zi.configure()


def test_the_plate_draws_the_install_and_a_retry_key():
    # the machine card owns the words and the keys since 8e3f51a65 (2026-10-10): crissa_init.card_view says what
    # either glass draws (the install's title and progress; Retry / Continue when it failed), the plate draws the
    # card's keys as init2_machine_<verb>, and the wall posts the verb to Init's machine step
    import crissa_init as zi
    src = (ROOT / "lib" / "init_v2_render.py").read_text()
    assert '"init2_machine_" + verb' in src
    v = zi.card_view({"state": "voice"}, {"state": "installing", "lines": ["fetching", "unpacking"]})
    assert v["title"] == "Installing the voice…" and v["lines"] == ["fetching", "unpacking"] and v["keys"] == []
    v = zi.card_view({"state": "voice_failed", "line": "disk full"})
    assert v["keys"] == [("retry", "Retry"), ("continue", "Continue")] and v["body"] == "disk full"
    wall = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert 'elif name.startswith("init2_machine_"):' in wall
    assert '_init2_post("machine", {"verb": name[len("init2_machine_"):]})' in wall


def test_the_launcher_plans_a_voice_lane_that_init_may_install_later(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("crissa_launcher_vi", ROOT / "scripts" / "crissa.py")
    z = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(z)
    import argparse
    a = argparse.Namespace(audio_at="host", face="crissa", tts="", tts_url="", voice=ROOT / "modules" / "voice",
                           with_aligner=False, with_tts=False, with_asr=False, asr_url="", port_offset=0,
                           with_turbo=False, with_pocket=False, with_multilingual=False, with_warm=False)
    monkeypatch.setattr(z, "plan", z.plan)
    try:
        p = z.plan(a, env_in={})
    except TypeError:
        p = z.plan(a)
    lanes = z.late_voice_lanes(a, p)
    # multilingual joined the late lanes with the one first-boot walk (76d5bcedb, 2026-10-10: every streamed voice the
    # start did not start waits as a late lane)
    assert set(lanes) == {"multilingual", "turbo", "pocket", "audio8"}
    assert lanes["pocket"]["argv"][1].endswith("modules/voice/scripts/crissa-pocket.py")
    monkeypatch.setenv("CRISSA_INIT_VOICE_INSTALL", "0")
    assert z.late_voice_lanes(a, p) == {}


def test_house_speak_s_no_voice_line_is_plain_words_and_only_a_pack_reaches_it():
    """2026-10-07: the text-only line a pack prints names no command or setting; the house never prints it (its
    seam answer is None before NO_VOICE, so its own path runs byte for byte)."""
    import ast
    src = (ROOT / "scripts" / "house-speak.py").read_text()
    tree = ast.parse(src)
    val = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
               and any(getattr(t, "id", "") == "NO_VOICE" for t in n.targets))
    assert not ENV_NAME.search(val) and "python3" not in val and "--install" not in val, val
    assert "Connect page" in val
    i = src.index("def _try_crissa_tts(")
    body = src[i:src.index("\ndef ", i + 10)]
    assert body.index("if cfg is None:\n        return None") < body.index("print(NO_VOICE")


# ---- the Connect page's "Her voice" box and the launcher's install on start (2026-10-07) ---------------------------

def test_the_install_writes_its_progress_for_the_connect_page(pack):
    rec = _rec(pack, _facts(gpu=CUDA, ram=64.0))
    w = World({"turbo": "no space left on device"})
    seen = []

    def runner(argv, progress):
        seen.append(VI.state(pack)["state"])           # the page sees "installing" while it runs
        return w.runner(argv, progress)
    res = VI.run(pack, progress=lambda s: None, runner=runner, installed=w.installed, rec=rec)
    assert seen == ["installing", "installing"]
    st = VI.state(pack)
    assert st["state"] == "failed" and st["engine"] == "pocket" and st["line"] == res["line"]
    assert any(l.startswith("Installing Crissa's voice") for l in st["lines"])
    v = VI.view(pack)
    assert v["ok"] and v["on"] and v["state"] == "failed" and v["main"] == "pocket" and v["label"] == "Pocket TTS"
    assert "retry from the Connect page" in res["line"]


def test_retry_refuses_when_off_and_never_starts_a_second_install(pack, monkeypatch):
    monkeypatch.setenv(VI.SWITCH, "0")
    assert VI.retry(pack) == {"ok": False, "error": "installing her voice is switched off on this machine"}
    monkeypatch.delenv(VI.SWITCH)
    VI._put(pack, state="installing", t=__import__("time").time())
    started = []
    monkeypatch.setattr(VI, "run", lambda *a, **k: started.append(1))
    assert VI.retry(pack)["note"] == "her voice is already installing" and started == []


def test_retry_runs_the_install_from_init_s_first_pick_in_the_background(pack, monkeypatch):
    calls = []
    monkeypatch.setattr(VI, "run", lambda home, **k: calls.append(k))
    out = VI.retry(pack, log=lambda m: None)
    import time as _t
    for _ in range(50):
        if calls:
            break
        _t.sleep(0.01)
    assert out == {"ok": True, "note": "installing her voice"} and calls and calls[0]["retry"] is True
    assert VI.state(pack)["state"] == "installing"


def test_the_player_serves_the_voice_box_on_a_pack_only_and_the_page_has_the_key():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert src.count('if head == "/v1/voice-install" and not crissa_home.is_house():') == 2, "GET + POST, packs only"
    i = src.index('if head == "/v1/voice-install" and not crissa_home.is_house():\n            # "Retry')
    assert "connect_origin_ok(self.headers)" in src[i:i + 400]
    page = (ROOT / "display" / "crissa-connect.html").read_text()
    assert ">Retry voice install</button>" in page and 'post("/v1/voice-install"' in page
    assert 'fetch("/v1/voice-install"' in page
    assert not ENV_NAME.search(page[page.index('<div id="voice-box"'):page.index('<div id="mouths-box"')])


def test_the_launcher_installs_a_missing_voice_on_start_unless_init_will(monkeypatch, tmp_path):
    import argparse
    import importlib.util
    spec = importlib.util.spec_from_file_location("crissa_launcher_vi2", ROOT / "scripts" / "crissa.py")
    z = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(z)
    monkeypatch.setattr(VT, "is_house", lambda: False)
    ran = []

    class T:
        def __init__(self, target, name, daemon):
            self.t = target

        def start(self):
            ran.append(1)
    monkeypatch.setattr(__import__("threading"), "Thread", T)
    z.LATE_LANES.clear()
    z.LATE_LANES.update({"pocket": {"name": "pocket"}})
    a = argparse.Namespace(no_voice=False, voice=ROOT / "modules" / "voice", tts_url="", tts="",
                           tier={"main": "pocket", "chain": ["pocket", "text"]})
    assert z.install_voice_on_start(a, tmp_path, env={}) is True and ran == [1]
    assert z.install_voice_on_start(a, tmp_path, env={"CRISSA_INIT_VOICE_INSTALL": "0"}) is False
    monkeypatch.setattr(z, "init_needed", lambda env=None: True)
    assert z.install_voice_on_start(a, tmp_path, env={}) is False, "Init's walk installs it, with its plate"
    monkeypatch.setattr(z, "init_needed", lambda env=None: False)
    z.LATE_LANES.clear()
    assert z.install_voice_on_start(a, tmp_path, env={}) is False, "installed already: nothing to do"


def test_plain_console_lines_for_listening_and_a_taken_port(capsys):
    """2026-10-07: the launcher's listening hint names no command; a taken port's last line (the one a status shows)
    is plain, the developer's ways round it stay in the log above it. The panel page has no Audio8 key left."""
    src = (ROOT / "scripts" / "crissa.py").read_text()
    assert "crissa-asr.py --install" not in src and "listening is not installed on this machine" in src
    import importlib.util
    spec = importlib.util.spec_from_file_location("crissa_asr_port", ROOT / "modules" / "voice" / "scripts" / "crissa-asr.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        assert m._bind("crissa-asr", s.getsockname()[1], None) is None
    finally:
        s.close()
    lines = [l for l in capsys.readouterr().err.splitlines() if l.strip()]
    assert "CRISSA_PORT_ASR" in lines[0], "the developer's line is kept"
    assert not ENV_NAME.search(lines[-1]) and "--port" not in lines[-1] and "already in use" in lines[-1]


# ---- LL (.101 2026-10-11): an upgrade's --install keeps the person's pick, and a pick rewrites the page's line -------

def _picked(pack, monkeypatch, pick, have=("pocket", "audio8")):
    facts = _facts(ram=91.0)                       # a robust CPU box: Init's own pick is Audio8 (.101)
    monkeypatch.setattr(VT, "probe", lambda *a, **k: facts)
    _rec(pack, facts)
    w = World({})
    w.have |= set(have)
    monkeypatch.setattr(VT, "installed", w.installed)
    VT.override(pick, pack)                       # the person's own pick (crissa-voice-tier.py --pick)
    return w


def test_an_upgrades_install_keeps_the_persons_pick(pack, monkeypatch):
    w = _picked(pack, monkeypatch, "pocket")
    VI._put(pack, state="done", engine="audio8", line="Crissa's voice is ready: Audio8 TTS.")   # an older install's
    VT.override("pocket", pack)
    assert VI.state(pack)["line"] == "Crissa's voice is your choice: Pocket TTS." and VI.state(pack)["engine"] == "pocket"
    res = VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, retry=True)   # the upgrade's
    kept = VT.read(pack)
    assert kept["main"] == "pocket" and kept["picked_by"] == "user" and kept["chain"][0] == "pocket", \
        "an upgrade keeps the person's pick (.101 lost Pocket to Audio8)"
    assert res["chosen"] == "pocket" and res["line"] == "Crissa's voice is ready: Pocket TTS." and w.ran == []
    assert VI.state(pack)["engine"] == "pocket" and VI.state(pack)["line"] == res["line"]


def test_init_s_own_pick_is_still_made_again_on_a_retry(pack, monkeypatch):
    facts = _facts(ram=91.0)
    monkeypatch.setattr(VT, "probe", lambda *a, **k: facts)
    rec = _rec(pack, dict(facts, ram_gb=8.0))     # an Init pick from a smaller machine: Pocket
    assert rec["main"] == "pocket"
    w = World({})
    w.have |= {"pocket", "audio8"}
    VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, retry=True)
    assert VT.read(pack)["main"] == "audio8" and VT.read(pack)["picked_by"] == "init"


def test_a_pick_not_installed_says_so_and_offers_the_install(pack, monkeypatch):
    _picked(pack, monkeypatch, "pocket", have=("audio8",))
    st = VI.state(pack)
    assert st["state"] == "idle" and st["engine"] == "pocket"
    assert st["line"] == ("Crissa's voice is your choice: Pocket TTS. It is not installed yet; you can retry from the "
                          "Connect page.")
    assert not ENV_NAME.search(st["line"])


def test_a_running_install_keeps_its_own_line(pack, monkeypatch):
    _picked(pack, monkeypatch, "audio8")
    VI._put(pack, state="installing", engine="pocket", line="", t=__import__("time").time(), pid=__import__("os").getpid())
    VT.override("pocket", pack)
    assert VI.state(pack)["state"] == "installing" and VI.state(pack)["line"] == ""


def test_the_keep_pick_switch_is_the_old_behaviour(pack, monkeypatch):
    w = _picked(pack, monkeypatch, "pocket")
    monkeypatch.setenv(VI.KEEP_PICK_SWITCH, "0")
    VI._put(pack, state="done", engine="audio8", line="old line")
    VT.override("pocket", pack)
    assert VI.state(pack)["line"] == "old line", "off: a pick leaves the page's line as it was"
    VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, retry=True)
    assert VT.read(pack)["main"] == "audio8" and VT.read(pack)["picked_by"] == "init", "off: a retry probes again"


def test_the_cli_install_and_pick_go_through_the_kept_pick():
    src = (ROOT / "scripts" / "crissa-voice-tier.py").read_text()
    assert "VI.run(retry=True" in src and "VT.override(a.pick)" in src
    body = (ROOT / "lib" / "crissa_voice_tiers.py").read_text()
    i = body.index("def override(")
    assert "crissa_voice_install.note_pick(rec, home)" in body[i:body.index("\ndef chain(", i)]
