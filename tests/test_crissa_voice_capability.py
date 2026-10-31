"""Voices by machine capability (the User 2026-10-07): "Init installs Audio8 as the announcer on robust CPU machines
... Order: announcer Audio8, then Pocket, then Kokoro, then text." That afternoon: on a robust CPU machine with no
GPU voice Crissa's own voice is Audio8 too, with Pocket as her backup; Turbo on a GPU; Pocket on weaker machines."

ROBUST is config (config/crissa-voice-tiers.json tiers.audio8.needs, with the measurements behind it), never code.
No installer runs here: the runner and installed() are fakes."""
from __future__ import annotations

import json
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

TABLE = json.loads((ROOT / "config" / "crissa-voice-tiers.json").read_text())
CUDA = {"kind": "cuda", "name": "NVIDIA RTX 4090", "total_gb": 24.0, "free_gb": 23.0, "count": 1}


@pytest.fixture
def pack(tmp_path, monkeypatch):
    monkeypatch.setattr(VT, "is_house", lambda: False)
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(crissa_home, "her_name", lambda paths=None: "Crissa")   # a pack: no house config
    monkeypatch.setattr(VT, "broken", lambda: {})
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "0")
    for k in (VI.SWITCH, VI.ANN_SWITCH):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CRISSA_TWO_VOICES", "0")    # these walks install the announcer (round 20 without krew: its own test)
    return tmp_path


def _facts(os_name="linux", arch="x86_64", ram=16.0, cores=8, gpu=None, macos="", lang="en"):
    f = {"os": os_name, "arch": arch, "cpu_cores": cores, "ram_gb": ram, "python": "3.14", "piper_model": False,
         "gpu": gpu or {"kind": "none"}, "language": {"code": lang, "name": lang, "from": "test"}}
    if os_name == "darwin":
        f["macos"] = macos
        if arch == "arm64":
            f["gpu"] = {"kind": "mps", "name": "Apple M2", "unified_gb": ram}
    return f


# ---- the thresholds live in config, with what was measured -----------------------------------------------------

def test_robust_is_config_with_its_measurements_and_the_code_copy_matches():
    a8 = TABLE["tiers"]["audio8"]
    assert a8["needs"]["ram_gb"] == 16 and a8["needs"]["cpu_cores"] == 8
    m = a8["measured"]
    assert m["rss_peak_gib"] < a8["needs"]["ram_gb"] / 8, "Audio8's peak is a small share of the RAM asked for"
    assert set(m["rtf_by_cpus"]) >= {"unlimited", "8", "5", "3"}
    code = VT.DEFAULT_TIERS["audio8"]["needs"]
    assert (code["ram_gb"], code["cpu_cores"]) == (a8["needs"]["ram_gb"], a8["needs"]["cpu_cores"])
    assert TABLE["announcer"]["order"] == ["audio8", "pocket", "kokoro", "say", "text"]
    assert VT.DEFAULT_ANNOUNCER["order"] == TABLE["announcer"]["order"]
    assert TABLE["order"] == ["turbo", "audio8", "pocket", "kokoro", "say", "text"], \
        "Crissa's own order: Audio8 on a robust CPU machine, Kokoro only behind Pocket (the User 2026-10-08)"
    assert VT.DEFAULT_ORDER == TABLE["order"] and VT.DEFAULT_TIERS["audio8"]["stands_back_for"] == \
        TABLE["tiers"]["audio8"]["stands_back_for"] and VT.DEFAULT_TIERS["audio8"]["installs_backup"] is True


def test_a_state_home_copy_can_move_the_threshold(pack):
    (pack / "crissa-voice-tiers.json").write_text(json.dumps({"tiers": {"audio8": {"needs": {"cpu_cores": 4}}}}))
    rec = VT.pick(_facts(ram=16.0, cores=4), VT.table(pack))
    assert rec["announcer_chain"][0] == "audio8"


# ---- the pick ---------------------------------------------------------------------------------------------------

def test_a_91gb_16_core_machine_with_no_gpu_speaks_and_announces_with_audio8_with_pocket_behind(pack):
    rec = VT.pick(_facts(ram=91.0, cores=16), VT.table(pack))
    assert rec["main"] == "audio8" and rec["chain"] == ["audio8", "pocket", "text"], "the User 2026-10-07, afternoon"
    assert rec["announcer"] == "", "Audio8 is her voice too: the record names no separate announcer"
    assert rec["announcer_chain"] == ["audio8", "pocket", "kokoro", "text"], "Audio8, Pocket, Kokoro, text"


@pytest.mark.parametrize("ram, cores", [(8.0, 4), (15.0, 16), (64.0, 6)])
def test_a_small_box_announces_with_pocket(pack, ram, cores):
    rec = VT.pick(_facts(ram=ram, cores=cores), VT.table(pack))
    assert rec["main"] == "pocket" and rec["announcer_chain"][0] == "pocket"
    assert rec["announcer"] == "", "Pocket is Crissa's voice too: the record names no separate announcer"
    assert "audio8" in rec["announcer_passed"]


def test_a_gpu_box_speaks_with_turbo_and_a_robust_one_announces_with_audio8(pack):
    rec = VT.pick(_facts(ram=64.0, cores=16, gpu=CUDA), VT.table(pack))
    assert rec["main"] == "turbo" and rec["chain"] == ["turbo", "pocket", "text"]
    assert rec["announcer_chain"][0] == "audio8"
    rec = VT.pick(_facts(ram=16.0, cores=4, gpu=CUDA), VT.table(pack))
    assert rec["main"] == "turbo" and rec["announcer_chain"][0] == "pocket"


def test_a_mac_keeps_say_in_crissa_s_chain_and_a_robust_mac_speaks_and_announces_with_audio8(pack):
    rec = VT.pick(_facts("darwin", "arm64", 16.0, cores=10, macos="14.5"), VT.table(pack))
    assert rec["chain"] == ["audio8", "pocket", "say", "text"], "a robust Mac: Audio8, Pocket, the Mac's say, text"
    assert rec["announcer_chain"][:2] == ["audio8", "pocket"] and "say" in rec["announcer_chain"]
    rec = VT.pick(_facts("darwin", "arm64", 8.0, cores=8, macos="14.5"), VT.table(pack))
    assert rec["announcer_chain"][0] == "pocket" and rec["chain"] == ["pocket", "kokoro", "say", "text"], \
        "an 8 GB Mac: Pocket, then Kokoro, then say"
    rec = VT.pick(_facts("darwin", "arm64", 16.0, cores=10, macos="13.6"), VT.table(pack))
    assert "audio8" not in rec["chain"] and "audio8" not in rec["announcer_chain"], "Audio8 needs macOS 14"


# ---- the install ------------------------------------------------------------------------------------------------

class World:
    def __init__(self, fail: dict | None = None):
        self.fail, self.have, self.ran = fail or {}, set(), []

    def installed(self, eng):
        return None if eng in ("say", "text", "piper") else eng in self.have

    def runner(self, argv, progress):
        stem = Path(argv[1]).stem.replace("crissa-", "")
        eng = {"tts": "kokoro"}.get(stem, stem)   # Kokoro's installer is crissa-tts.py
        self.ran.append(eng)
        if eng in self.fail:
            return 2, "crissa-%s: %s" % (eng, self.fail[eng])
        self.have.add(eng)
        return 0, "installed"


def _rec(home, facts):
    rec = dict(VT.pick(facts, VT.table(home)), schema=VT.SCHEMA, facts=facts, picked_by="init")
    VT._write(rec, home)
    return rec


def test_init_installs_audio8_for_crissa_and_the_announcer_then_pocket_as_her_backup_on_a_robust_cpu_machine(pack):
    rec = _rec(pack, _facts(ram=91.0, cores=16))
    w, started, shown = World(), [], []
    res = VI.run(pack, progress=shown.append, runner=w.runner, installed=w.installed, rec=rec, start=started.append)
    # her backup is installed but not started: no role uses Pocket here (round 17, 2026-10-13, lib/crissa_voice_demand;
    # a line that needs it starts it); the switch off starts it as before
    assert w.ran == ["audio8", "pocket"] and started == ["audio8"], "the announcer's Audio8 is already there"
    assert res["chosen"] == "audio8" and res["ok"] and res["announcer"]["chosen"] == "audio8"
    assert res["backup"]["chosen"] == "pocket", "her backup is really installed (installs_backup)"
    assert res["line"] == "Crissa's voice is ready: Audio8 TTS."
    assert any(s.startswith("Installing Crissa's voice: Audio8 TTS") for s in shown)
    assert any(s.startswith("Installing Crissa's backup voice: Pocket TTS") for s in shown)
    kept = VT.read(pack)
    assert kept["announcer"] == "" and kept["announcer_install"]["chosen"] == "audio8"
    assert kept["backup_install"]["chosen"] == "pocket" and kept["chain"] == ["audio8", "pocket", "text"]
    argv = VI.installer_argv("audio8", VT.table(pack))
    assert argv[1].endswith("modules/voice/scripts/crissa-audio8.py") and argv[2:] == ["--install"]


def test_with_the_on_demand_switch_off_init_starts_her_backup_too(pack, monkeypatch):
    """CRISSA_VOICE_ENGINES_ON_DEMAND=0 (round 17's kill switch): every voice Init installed starts, as before."""
    monkeypatch.setenv("CRISSA_VOICE_ENGINES_ON_DEMAND", "0")
    rec = _rec(pack, _facts(ram=91.0, cores=16))
    w, started = World(), []
    VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, rec=rec, start=started.append)
    assert started == ["audio8", "pocket"]


def test_a_failed_audio8_install_falls_to_pocket_and_says_so_in_plain_words(pack):
    rec = _rec(pack, _facts(ram=91.0, cores=16))
    w = World({"audio8": "no space left on device (set CRISSA_HOME=...)"})
    res = VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, rec=rec)
    assert w.ran == ["audio8", "pocket"], "a failed install is not fetched a second time for the announcer"
    assert res["chosen"] == "pocket" and not res["ok"] and res["announcer"]["chosen"] == "pocket"
    assert res["line"].startswith("Audio8 TTS") and "could not be installed: no space left on device" in res["line"]
    assert "She will speak with Pocket TTS for now; you can retry from the Connect page." in res["line"]
    assert res["line"].endswith("The announcer's voice (Audio8 TTS) could not be installed: no space left on device. "
                                "Pocket TTS says the announcer's lines for now; you can retry from the Connect page.")
    kept = VT.read(pack)
    assert kept["announcer_chain"][0] == "pocket" and kept["announcer"] == ""


def test_a_small_box_installs_one_voice_and_the_announcer_shares_it(pack):
    rec = _rec(pack, _facts(ram=8.0, cores=4))
    w = World()
    res = VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, rec=rec)
    assert w.ran == ["pocket", "kokoro"] and res["announcer"]["chosen"] == "pocket"
    assert res["backup"]["chosen"] == "kokoro", "Pocket's backup is installed with her"
    assert res["line"] == "Crissa's voice is ready: Pocket TTS."


def test_the_announcer_install_has_its_own_switch(pack, monkeypatch):
    monkeypatch.setenv(VI.ANN_SWITCH, "0")
    rec = _rec(pack, _facts(ram=91.0, cores=16))
    w = World()
    res = VI.run(pack, progress=lambda s: None, runner=w.runner, installed=w.installed, rec=rec)
    assert w.ran == ["audio8", "pocket"] and "announcer" not in res, "her voice and her backup; no announcer walk"


def test_the_launcher_installs_a_missing_announcer_on_start(monkeypatch, tmp_path):
    import argparse
    import importlib.util
    spec = importlib.util.spec_from_file_location("crissa_launcher_cap", ROOT / "scripts" / "crissa.py")
    z = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(z)
    monkeypatch.setattr(VT, "is_house", lambda: False)
    monkeypatch.setattr(z, "init_needed", lambda env=None: False)
    ran = []

    class T:
        def __init__(self, target, name, daemon):
            pass

        def start(self):
            ran.append(1)
    monkeypatch.setattr(__import__("threading"), "Thread", T)
    z.LATE_LANES.clear()
    z.LATE_LANES.update({"audio8": {"name": "audio8"}})
    a = argparse.Namespace(no_voice=False, voice=ROOT / "modules" / "voice", tts_url="", tts="",
                           tier={"main": "pocket", "chain": ["pocket", "text"],
                                 "announcer_chain": ["audio8", "pocket", "text"]})
    assert z.install_voice_on_start(a, tmp_path, env={}) is True and ran == [1]
    z.LATE_LANES.clear()
