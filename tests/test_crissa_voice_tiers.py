"""The voice tier (2026-10-06, the User: the voice is chosen by what the machine can handle; no hidden caps, no silent
fallbacks). The machines are simulated through probe()'s own seams (no nvidia-smi, no sysctl is run)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _credits() -> str:
    """CREDITS.md: config/crissa-licences/ in the source tree, the repo root in a clone."""
    for p in (ROOT / "config" / "crissa-licences" / "CREDITS.md", ROOT / "CREDITS.md"):
        if p.is_file():
            return p.read_text()
    raise FileNotFoundError("CREDITS.md")


sys.path.insert(0, str(ROOT / "lib"))
import crissa_voice_tiers as VT  # noqa: E402


# Audio8 as a VOICE is behind its switch since 2026-10-17 (the User: Audio8 is her ear, Pocket her voice; lib/
# crissa_voice_tiers.not_a_voice). These tests pin the Audio8-voice path that the switch keeps; the rule itself is
# tests/test_audio8_not_a_voice_20261017.py.
@pytest.fixture(autouse=True)
def _audio8_may_speak(monkeypatch):
    monkeypatch.setenv("CRISSA_AUDIO8_TTS", "1")

GB = 1024
HOUSE_PICK = VT.house_pick


def _runner(ram_gb: float, gpus=(), chip: str = ""):
    """A stand-in for subprocess: sysctl (Mac RAM, chip) and nvidia-smi (name, total MiB, free MiB)."""
    def run(cmd, timeout=5.0):
        if cmd[:3] == ["sysctl", "-n", "hw.memsize"]:
            return str(int(ram_gb * (1 << 30)))
        if cmd[:2] == ["sysctl", "-n"]:
            return chip
        if cmd and cmd[0] == "nvidia-smi":
            return "".join("%s, %d, %d\n" % g for g in gpus)
        return ""
    return run


def _facts(monkeypatch, plat, machine, ram_gb, gpus=(), chip="", macos="14.5"):
    monkeypatch.setattr(VT.shutil, "which", lambda n: "/usr/bin/nvidia-smi" if (n == "nvidia-smi" and gpus) else None)
    monkeypatch.setattr(VT, "_ram_gb", lambda p, run=None: ram_gb)
    f = VT.probe(run=_runner(ram_gb, gpus, chip), plat=plat, machine=machine, env={})
    if f["os"] == "darwin":
        f["macos"] = macos
    return f


@pytest.fixture()
def pack(monkeypatch, tmp_path):
    monkeypatch.setattr(VT, "is_house", lambda: False)
    monkeypatch.setattr(VT, "installed",
                        lambda engine: None if engine not in ("turbo", "pocket", "kokoro") else False)
    monkeypatch.setattr(VT, "broken", lambda: {})
    return tmp_path


def test_an_8gb_m1_gets_pocket_then_say(monkeypatch, pack):
    f = _facts(monkeypatch, "darwin", "arm64", 8.0, chip="Apple M1")
    assert f["gpu"]["kind"] == "mps" and f["os"] == "darwin"
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "pocket" and rec["chain"] == ["pocket", "kokoro", "say", "text"], \
        "Pocket, then Kokoro, then say (the User 2026-10-08)"
    assert rec["passed_over"]["turbo"], "Turbo is passed over, and why is said"
    assert "cosy" not in rec["passed_over"] and "cosy" not in rec["chain"], "Cosy left the tiers (2026-10-06)"
    assert "Not" in rec["why"] and rec["announcer"] == ""
    assert rec["settings"]["voices"]["crissa"] == "crissa" and rec["settings"]["emit"] == "frames", \
        "Crissa is her own voice on Pocket (round 28; azelma 2026-10-11 to 10-17)"


def test_an_m1_on_macos_13_is_told_why_not_pocket_and_gets_say(monkeypatch, pack):
    rec = VT.pick(_facts(monkeypatch, "darwin", "arm64", 8.0, chip="Apple M1", macos="13.6"), VT.table(pack))
    assert rec["main"] == "say" and "kokoro" not in rec["chain"], \
        "macOS 13 cannot run Pocket, so Kokoro is not her voice either (2026-10-08)"
    assert rec["passed_over"]["pocket"] == ["needs macOS 14.0 or later (this Mac runs 13.6)"]


def test_turbo_is_a_real_tier_and_a_v2_slot_is_still_named_and_never_picked(monkeypatch, pack):
    tab = VT.table(pack)
    assert "slot" not in tab["tiers"]["turbo"], "Turbo ships in this release"
    assert tab["tiers"]["turbo"]["install"].endswith("crissa-turbo.py --install")
    assert tab["tiers"]["turbo"]["settings"]["watermark"] is True, "Perth watermark ON by default, visible key"
    big = [("NVIDIA RTX 4090", 24 * GB, 23 * GB)]
    assert VT.pick(_facts(monkeypatch, "linux", "x86_64", 64.0, gpus=big), tab)["main"] == "turbo"
    # a state-home table can still park an engine as a v2 slot: named, never picked, never chosen
    (pack / "crissa-voice-tiers.json").write_text(json.dumps({"tiers": {"turbo": {"slot": "v2"}}}))
    f = _facts(monkeypatch, "linux", "x86_64", 64.0, gpus=big)
    f["cpu_cores"] = 16
    rec = VT.pick(f, VT.table(pack))
    assert rec["passed_over"]["turbo"] == ["is a v2 slot: its engine is not in this release yet"]
    assert rec["main"] == "audio8" and rec["chain"] == ["audio8", "pocket", "text"] and "turbo" not in rec["chain"], \
        "no usable GPU voice on a robust machine: Audio8, then Pocket (the User 2026-10-07)"
    f["cpu_cores"] = 4
    assert VT.pick(f, VT.table(pack))["main"] == "pocket", "a weaker CPU keeps Pocket"
    with pytest.raises(ValueError, match="v2 slot"):
        VT.override("turbo", pack)


def test_arm_linux_with_cuda_gets_turbo_then_pocket(monkeypatch, pack):
    """2026-10-06 (the User): Arm CUDA is Turbo, then Pocket. Not yet verified on Arm hardware: the self-test decides."""
    f = _facts(monkeypatch, "linux", "aarch64", 64.0, gpus=[("NVIDIA GH200", 96 * GB, 80 * GB)])
    f["cpu_cores"] = 72
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "turbo" and rec["chain"][:2] == ["turbo", "pocket"] and rec["announcer"] == "audio8"
    assert rec["announcer_chain"] == ["audio8", "pocket", "kokoro", "text"], "a robust CPU machine announces with Audio8"
    assert "NOT YET VERIFIED ON ARM HARDWARE" in VT.table(pack)["tiers"]["turbo"]["needs_why"]


def test_a_turbo_that_fails_its_self_test_falls_to_pocket_and_says_why(monkeypatch, pack):
    f = _facts(monkeypatch, "linux", "aarch64", 64.0, gpus=[("NVIDIA GB10", 120 * GB, 100 * GB)])
    f["cpu_cores"] = 20                           # a GB10 has 20 cores: robust, so Audio8 is her voice, Pocket behind
    why = "RuntimeError: this torch has no kernels for sm_121"
    rec = VT.pick(f, VT.table(pack), {"turbo": why})
    assert rec["main"] == "audio8" and rec["chain"][:2] == ["audio8", "pocket"] and "turbo" not in rec["chain"]
    assert rec["passed_over"]["turbo"] == ["is installed but failed its self-test on this machine: " + why]
    assert why in rec["why"], "the reason is in the line Init shows"
    # a saved Turbo pick is made again once the self-test fails, and the screen says so
    VT.ensure(pack, facts=f)
    assert VT.read(pack)["main"] == "turbo"
    monkeypatch.setattr(VT, "broken", lambda: {"turbo": why})
    lines = []
    got = VT.ensure(pack, facts=f, log=lines.append)
    assert got["main"] == "audio8" and VT.read(pack)["main"] == "audio8"
    assert lines[0] == "voice: turbo failed its self-test on this machine (%s); audio8 speaks instead" % why
    # a person's own Turbo pick is kept, and the screen says it fails
    VT.override("turbo", pack)
    assert any("it stays your choice" in l for l in VT.ensure(pack)["say"])


def test_an_intel_mac_cannot_run_pocket_and_speaks_with_say(monkeypatch, pack):
    f = _facts(monkeypatch, "darwin", "x86_64", 16.0)
    f["cpu_cores"] = 4
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "say" and "pocket" in rec["passed_over"] and "kokoro" not in rec["chain"]
    f["cpu_cores"] = 8                            # a robust Intel Mac: Audio8 (it runs on macOS 14+), then say
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "audio8" and rec["chain"] == ["audio8", "say", "text"], "Pocket cannot run on an Intel Mac"


def test_a_cpu_only_linux_box_gets_pocket_then_text(monkeypatch, pack):
    f = _facts(monkeypatch, "linux", "x86_64", 32.0)
    f["cpu_cores"] = 4
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "pocket" and rec["chain"] == ["pocket", "kokoro", "text"]
    assert "say" not in rec["chain"] and rec["chain"][-1] == "text"
    assert "turbo" in rec["passed_over"] and "cosy" not in rec["passed_over"]
    f["cpu_cores"] = 16                           # robust: Audio8 is her voice, Pocket her backup (the User 2026-10-07)
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "audio8" and rec["chain"] == ["audio8", "pocket", "text"]


def test_a_24gb_cuda_box_gets_the_gpu_voice_with_pocket_behind_it(monkeypatch, pack):
    f = _facts(monkeypatch, "linux", "x86_64", 64.0, gpus=[("NVIDIA RTX 4090", 24 * GB, 23 * GB)])
    assert f["gpu"] == {"kind": "cuda", "name": "NVIDIA RTX 4090", "total_gb": 24.0, "free_gb": 23.0, "count": 1}
    tab = VT.table(pack)
    f["cpu_cores"] = 16
    rec = VT.pick(f, tab)
    assert rec["main"] == "turbo" and rec["announcer"] == "audio8", "Audio8 announces on a robust machine (the User 2026-10-07)"
    assert rec["chain"] == ["turbo", "pocket", "text"], "Turbo, Pocket, text (2026-10-07)"
    assert rec["settings"]["watermark"] is True
    assert rec.get("beyond"), "a bigger machine is told what more it can run: never capped"


def test_a_busy_gpu_is_passed_over_with_its_reason_and_pocket_speaks(monkeypatch, pack):
    f = _facts(monkeypatch, "linux", "x86_64", 64.0, gpus=[("NVIDIA A6000", 48 * GB, 2 * GB)])
    f["cpu_cores"] = 4
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "pocket"
    assert any("free" in r for r in rec["passed_over"]["turbo"]), rec["passed_over"]
    f["cpu_cores"] = 16                           # no usable GPU on a robust machine: Audio8, then Pocket
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "audio8" and rec["chain"][:2] == ["audio8", "pocket"]


def test_the_pick_is_written_once_and_said_with_what_is_missing(monkeypatch, pack):
    f = _facts(monkeypatch, "linux", "x86_64", 32.0)
    f["cpu_cores"] = 4
    lines = []
    rec = VT.ensure(pack, facts=f, log=lines.append)
    saved = json.loads((pack / "voice-tier.json").read_text())
    assert saved["main"] == "pocket" and saved["picked_by"] == "init" and saved["schema"] == VT.SCHEMA
    assert lines[0].startswith("voice: pocket (picked now)")
    assert any("Pocket TTS is the best voice this machine can run and is not installed yet" in x and "Init installs it"
               in x and "--install" not in x for x in lines), "not installed: one plain line, no command (2026-10-07)"
    again = VT.ensure(pack, facts={"os": "darwin"})
    assert again["main"] == "pocket" and "(picked now)" not in again["say"][0], "probed once; later starts read it"
    assert rec["chain"] == VT.chain(None, pack)


def test_the_users_choice_wins_and_is_kept(monkeypatch, pack):
    f = _facts(monkeypatch, "linux", "x86_64", 32.0)
    f["cpu_cores"] = 4
    VT.ensure(pack, facts=f)
    rec = VT.override("say", pack)
    assert rec["main"] == "say" and rec["picked_by"] == "user" and rec["init_pick"] == "pocket"
    assert rec["warn"], "say on Linux: the screen says why it may not speak"
    assert VT.ensure(pack)["say"][0] == "voice: say (your choice; Init would pick pocket)"
    with pytest.raises(ValueError):
        VT.override("no-such-engine", pack)



def test_a_language_answer_keeps_the_users_pick(monkeypatch, pack):
    f = _facts(monkeypatch, "linux", "x86_64", 32.0)
    monkeypatch.setattr(VT, "probe", lambda *a, **k: dict(f))
    VT.ensure(pack, facts=f)
    VT.override("say", pack)
    res = VT.set_language("Deutsch", pack)
    assert res["ok"] and res["rec"]["main"] == "say" and res["rec"]["picked_by"] == "user", res["rec"]
    saved = json.loads((pack / "voice-tier.json").read_text())
    assert saved["main"] == "say" and saved["picked_by"] == "user"
    assert any("your choice" in x for x in res["rec"]["say"])

def test_a_state_home_table_changes_the_shipped_one_key_by_key(monkeypatch, pack):
    (pack / "crissa-voice-tiers.json").write_text(json.dumps({"tiers": {"turbo": {"needs": {"gpu_free_gb": 1}}}}))
    tab = VT.table(pack)
    assert tab["tiers"]["turbo"]["needs"]["gpu_free_gb"] == 1 and tab["tiers"]["turbo"]["needs"]["gpu"] == "cuda"
    rec = VT.pick(_facts(monkeypatch, "linux", "x86_64", 64.0, gpus=[("NVIDIA A6000", 48 * GB, 2 * GB)]), tab)
    assert rec["main"] == "turbo", "the user's own table, not a cap in code"


def test_a_machine_whose_own_config_names_its_voices_keeps_them_and_nothing_is_written(monkeypatch, tmp_path):
    cfg = tmp_path / "house-machine.json"
    cfg.write_text(json.dumps({"tts_main": "cosy", "tts_backup": "say", "tts_announcer": ""}))
    monkeypatch.setattr(VT, "is_house", lambda: True)
    monkeypatch.setattr(VT, "house_pick", lambda paths=None: HOUSE_PICK(cfg))
    rec = VT.ensure(tmp_path)
    assert rec["main"] == "cosy" and rec["backup"] == "say" and rec["announcer"] == ""
    assert not (tmp_path / "voice-tier.json").exists()
    assert "house" in rec["say"][0]


def test_the_tier_table_ships_no_machine_or_user_detail():
    text = (ROOT / "config" / "crissa-voice-tiers.json").read_text()
    for bad in ("/" "data/", "/" "home/", "127.0.0.1:", "localhost:"):
        assert bad not in text.lower(), bad
    export = ROOT / "scripts" / "crissa-export.py"
    if export.is_file():                     # the source tree: the export's own scrub, word for word
        import importlib.util
        spec = importlib.util.spec_from_file_location("crissa_export_scrub", export)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.scrub_text(text) == {}
    tab = json.loads(text)
    assert tab["order"] == ["turbo", "audio8", "pocket", "kokoro", "say", "text"], \
        "Init's order (the User 2026-10-08: Kokoro backs Pocket; Audio8 stays her voice on a robust CPU machine)"
    assert tab["tiers"]["audio8"]["stands_back_for"] == ["turbo", "multilingual", "turbo_mlx"], "a GPU voice keeps Pocket next"
    assert VT.DEFAULT_ORDER == tab["order"]
    assert tab["announcer"]["order"] == ["audio8", "pocket", "kokoro", "say", "text"]
    assert "cosy" not in tab["tiers"] and "cosy" not in VT.ENGINES, "Cosy left the tiers (the User 2026-10-06)"
    assert "slot" not in tab["tiers"]["pocket"], "Pocket is a real tier"
    assert "slot" not in tab["tiers"]["turbo"], "Turbo is a real tier"
    assert tab["announcer"]["audio8"]["voice"] == "anna", "the packs' Audio8 voice is a config value, anna for now"


def test_the_seam_walks_the_chain_and_names_what_it_passed_over(monkeypatch):
    import crissa_tts as T
    rec = {"schema": VT.SCHEMA, "main": "turbo", "chain": ["turbo", "kokoro", "text"], "announcer": "kokoro"}
    monkeypatch.setattr(T, "_tier_record", lambda home=None: rec)
    monkeypatch.setattr(T, "stream_up", lambda name, e=None: False)
    monkeypatch.setattr(T, "kokoro_default", lambda e=None: True)
    got = T._by_tier({}, {})
    assert got["engine"] == "kokoro" and got["tier_main"] == "turbo"
    assert got["passed"] and got["passed"][0].startswith("turbo:")
    rec.update(main="kokoro", chain=["kokoro", "text"])
    got = T._by_tier({}, {})
    assert got["engine"] == "kokoro" and "passed" not in got and "tier_main" not in got


def test_the_seam_falls_from_the_gpu_voice_to_pocket_and_says_so(monkeypatch):
    import crissa_tts as T
    rec = {"schema": VT.SCHEMA, "main": "turbo", "chain": ["turbo", "pocket", "kokoro", "text"], "announcer": "kokoro"}
    monkeypatch.setattr(T, "_tier_record", lambda home=None: rec)
    monkeypatch.setattr(T, "stream_up", lambda name, e=None: name == "pocket")
    monkeypatch.setattr(T, "kokoro_default", lambda e=None: True)
    got = T._by_tier({}, {"CRISSA_PORT_OFFSET": "0"})
    assert got["engine"] == "pocket" and got["tier_main"] == "turbo" and got["url"].endswith(":8188/stream")
    assert got["passed"][0].startswith("turbo: no Chatterbox-Turbo server") and got["backup"] == ["kokoro"]
    got = T._by_tier({}, {"CRISSA_TTS_DEFAULT_POCKET": "0"})
    assert got["engine"] == "kokoro" and any(p.startswith("pocket: switched off") for p in got["passed"])


def test_audio8_is_the_announcer_again_in_the_tiers_the_credits_and_the_voice_docs():
    """the User 2026-10-06 (correcting 7bd9b7606): Audio8 on the CPU stays the announcer on robust machines, backed by
    Pocket, then Kokoro, then say/text; its licence (Apache-2.0, Edge0) is credited, and no house voice ships."""
    credits = _credits()
    assert "Audio8 TTS by Edge0" in credits and "Apache-2.0" in credits and "Edge0-AI/Audio8_TTS" in credits
    tab = json.loads((ROOT / "config" / "crissa-voice-tiers.json").read_text())
    assert tab["tiers"]["audio8"]["needs"]["ram_gb"] >= 16 and tab["tiers"]["audio8"]["needs"]["cpu_cores"] >= 8
    assert "audio8" in VT.ENGINES and "Audio8" in (ROOT / "modules" / "voice" / "README.md").read_text()
    assert tab["announcer"]["pocket"]["anna"] == "anna" and tab["announcer"]["kokoro"] == "bm_george"


def test_an_8gb_mac_announces_with_pocket_and_a_small_linux_box_with_pocket_then_text(monkeypatch, pack):
    rec = VT.pick(_facts(monkeypatch, "darwin", "arm64", 8.0, chip="Apple M1"), VT.table(pack))
    assert rec["announcer_chain"][0] == "pocket" and "audio8" in rec["announcer_passed"]
    f = _facts(monkeypatch, "linux", "x86_64", 8.0)
    f["cpu_cores"] = 4
    rec = VT.pick(f, VT.table(pack))
    assert rec["announcer_chain"] == ["pocket", "kokoro", "text"]


def test_the_language_picks_the_voice_and_says_when_it_falls_to_text(monkeypatch, pack):
    gpu = [("NVIDIA RTX 4090", 24 * GB, 23 * GB)]
    monkeypatch.setattr(VT, "installed", lambda engine: None if engine != "kokoro" else False)
    f = _facts(monkeypatch, "linux", "x86_64", 64.0, gpus=gpu)
    f.update(cpu_cores=16, language={"code": "de", "name": "German", "from": "Init"})
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "audio8" and rec["chain"] == ["audio8", "pocket", "text"], \
        "German on a robust GPU box: Turbo speaks English only, Init never picks Multilingual (2026-10-07), so Audio8 " \
        "speaks German with Pocket behind it"
    f["cpu_cores"] = 4
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "pocket" and rec["chain"] == ["pocket", "text"], "a weaker CPU: Pocket speaks German"
    f["cpu_cores"] = 16
    f = _facts(monkeypatch, "linux", "x86_64", 32.0)
    f.update(cpu_cores=16, language={"code": "ja", "name": "Japanese", "from": "Init"})
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "audio8" and rec["chain"] == ["audio8", "text"] and rec["announcer_chain"][0] == "audio8", \
        "Japanese on a robust machine: Audio8 speaks it for Crissa and the announcer (Pocket does not speak it)"
    f.update(language={"code": "fi", "name": "Finnish", "from": "Init"})
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "text"
    view = VT.language_view(f, VT.table(pack), rec["chain"], rec["announcer_chain"])
    assert view["text_only"] and "Finnish" in view["line"] and view["here"] == ["text"]


def test_init_s_language_answer_is_kept_and_read_first(monkeypatch, pack):
    assert VT.parse_language("Deutsch") == "de" and VT.parse_language("français") == "fr"
    assert VT.parse_language("ja") == "ja"
    monkeypatch.setattr(VT, "ensure", lambda home=None, **k: {})
    VT.set_language("Italian", pack)
    assert VT.saved_language(pack)["code"] == "it" and "your answer" in VT.saved_language(pack)["from"]


def test_the_credits_voice_lines_name_no_recording():
    """The credits describe no recording of a person (2026-10-06). Since 2026-10-08 one clip ships for her: her own
    voice, cut from her Grok Imagine films' sound; the credits name it, its provenance file and "Created with Grok"
    (until then: "No clip for her speech engine ships")."""
    credits = _credits()
    line = next(l for l in credits.splitlines() if l.startswith("- Her voice:"))
    assert "Created with Grok" in line or "created with Grok" in line
    assert "crissa-imagine.wav" in credits and "crissa-imagine.json" in credits and "no real person" in credits
    for bad in ("reference voice", "clip of her", "imagine-generated speech", "imagine voice", "crissa.wav"):
        assert bad not in credits.lower(), bad


def test_init_s_pick_is_the_only_chooser_even_with_the_quiet_switch_on(monkeypatch, tmp_path):
    """2026-10-06: with nothing named, a pack speaks by Init's pick; the quiet-fallback switch never jumps ahead
    of it to say/Piper."""
    import crissa_tts as T
    rec = {"schema": VT.SCHEMA, "main": "pocket", "chain": ["pocket", "kokoro", "piper", "text"], "announcer": ""}
    monkeypatch.setattr(T, "_house", lambda: False)
    monkeypatch.setattr(T, "_tier_record", lambda home=None: rec)
    monkeypatch.setattr(T, "stream_up", lambda name, e=None: name == "pocket")
    monkeypatch.setattr(T, "kokoro_default", lambda e=None: True)
    model = tmp_path / "v.onnx"
    model.write_bytes(b"x")
    got = T.config({"CRISSA_TTS_FALLBACK": "1", "CRISSA_TTS_PIPER_MODEL": str(model)}, tmp_path)
    assert got["engine"] == "pocket" and got["backup"] == ["kokoro", "piper"]


def test_with_no_pick_yet_the_backup_comes_from_the_tier_table_not_a_list_in_code():
    import crissa_tts as T
    assert T._backup_of("turbo", {}) == VT.table()["tiers"]["turbo"]["backup"]
    assert T._backup_of("pocket", {}) == VT.table()["tiers"]["pocket"]["backup"]


def test_every_line_rewrites_the_status_mark_so_a_recovered_voice_clears_it(monkeypatch, tmp_path):
    import crissa_tts as T
    monkeypatch.setattr(T, "VOICE_STATE", tmp_path / "voice-state.json")
    passed = ["turbo: no Chatterbox-Turbo server answers", "kokoro: switched off (CRISSA_TTS_DEFAULT_KOKORO=0)",
              "a", "b", "c", "d"]
    T.note_choice({"engine": "pocket", "tier_main": "turbo", "passed": passed})
    st = json.loads((tmp_path / "voice-state.json").read_text())
    assert st["fallback"] and st["spoke"] == "pocket" and st["passed"] == passed, "every reason, none cut"
    T.note_choice({"engine": "turbo", "tier_main": "turbo"})
    st = json.loads((tmp_path / "voice-state.json").read_text())
    assert st["fallback"] is False and st["passed"] == []


def test_a_streamed_voice_that_drops_a_line_is_named_on_screen_and_in_the_log():
    src = (ROOT / "scripts" / "house-speak.py").read_text()
    i = src.index("def _try_backup()")
    block = src[i:i + 2400]
    assert 'main = _main_name()' in block and 'return _STREAMED["name"] or MAIN' in src
    assert "did not speak this line; {b} spoke it" in block and "_note_spoke(b, main" in block
    assert "for b in chain:" in block, "the whole backup chain is asked, not its first entry (2026-10-07)"


def test_cosy_is_gone_from_the_tiers_the_credits_the_installers_and_the_voice_docs():
    """the User 2026-10-06: Cosy left Crissa (the house keeps its own Cosy, named in its house config)."""
    assert "cosy" not in _credits().lower()
    # 2026-10-07: and from the voice seam, the tier code, the tier script and every file of the voice module
    voice = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "modules" / "voice").rglob("*")
                   if p.is_file() and p.suffix in (".py", ".md", ".json", ".in"))
    for rel in ["config/crissa-voice-tiers.json", "lib/crissa_ports.py", "lib/crissa_tts.py",
                "lib/crissa_voice_tiers.py", "scripts/crissa-voice-tier.py", *voice]:
        if (ROOT / rel).is_file():
            assert "cosy" not in (ROOT / rel).read_text().lower(), rel
    for rel in ("modules/voice/scripts/crissa-cosy.py", "modules/voice/crissa_voice/cosy_setup.py",
                "modules/voice/crissa_voice/cosy.py", "modules/voice/locks/cosy.in"):
        assert not (ROOT / rel).exists(), rel
    assert not list((ROOT / "modules" / "voice" / "locks").glob("cosy-*"))


def test_a_pack_that_names_a_voice_it_does_not_have_hears_init_s_pick_and_is_told_why(monkeypatch, tmp_path):
    import crissa_tts as T
    rec = {"schema": VT.SCHEMA, "main": "pocket", "chain": ["pocket", "kokoro", "text"], "announcer": ""}
    monkeypatch.setattr(T, "_house", lambda: False)
    monkeypatch.setattr(T, "_tier_record", lambda home=None: rec)
    monkeypatch.setattr(T, "stream_up", lambda name, e=None: name == "pocket")
    for name in ("cosy", "nonesuch"):     # the house's own voice and a typo are just not here
        got = T.config({"CRISSA_TTS": name}, tmp_path)
        assert got["engine"] == "pocket" and got["passed"][0] == (
            "%s: not one of this install's voices; the voice picked for this machine speaks instead" % name)
        assert "CRISSA" not in got["passed"][0]


def test_turbo_with_only_its_built_in_voice_counts_as_up(monkeypatch):
    """GPU run 2026-10-06: Turbo with no clip answers /health with voices [] and builtin_voice true."""
    import io
    import crissa_tts as T

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    for body, up in (({"ok": True, "voices": [], "builtin_voice": True}, True), ({"ok": True, "voices": []}, False),
                     ({"ok": True, "voices": ["alba"]}, True), ({"ok": False, "builtin_voice": True}, False)):
        monkeypatch.setattr(T.urllib.request, "urlopen", lambda url, timeout=0.3, b=body: R(json.dumps(b).encode()))
        assert T.stream_up("turbo", {}) is up, body



def test_turbo_mlx_is_top_mac_tier_above_pocket_when_ram_allows(monkeypatch, pack):
    """2026-10-06 Task 5: on Apple Silicon with 16 GB, Turbo MLX sits above Pocket; 8 GB stays on Pocket.
    NOT YET VERIFIED ON APPLE SILICON HARDWARE: the install refuses until a measured self-test lands."""
    f = _facts(monkeypatch, "darwin", "arm64", 16.0, chip="Apple M2")
    f["gpu"] = {"kind": "mps", "name": "Apple M2", "unified_gb": 16.0}
    f["macos"] = "14.5"
    f["cpu_cores"] = 8
    rec = VT.pick(f, VT.table(pack))
    assert rec["main"] == "audio8" and rec["chain"] == ["audio8", "pocket", "say", "text"], \
        "Init never picks Turbo MLX (2026-10-07): a 16 GB, 8-core Mac is robust, so Audio8 then Pocket (afternoon)"
    tab = VT.table(pack)
    tab["order"] = ["turbo_mlx"] + tab["order"]           # a state-home table may still put it first
    rec = VT.pick(f, tab)
    assert rec["main"] == "turbo_mlx" and rec["chain"][:2] == ["turbo_mlx", "pocket"], \
        "Audio8 stands back for a GPU voice: Pocket stays next"
    assert "NOT YET VERIFIED ON APPLE SILICON HARDWARE" in VT.table(pack)["tiers"]["turbo_mlx"]["needs_why"]
    f8 = _facts(monkeypatch, "darwin", "arm64", 8.0, chip="Apple M1")
    f8["gpu"] = {"kind": "mps", "name": "Apple M1", "unified_gb": 8.0}
    f8["macos"] = "14.5"
    rec8 = VT.pick(f8, tab)
    assert rec8["main"] == "pocket" and "turbo_mlx" in rec8["passed_over"]
    assert any("16 GB" in r for r in rec8["passed_over"]["turbo_mlx"])


def test_turbo_mlx_install_refuses_until_verified_and_onnx_is_not_a_tier():
    """ONNX Turbo is not a tier (Arm GPU path evaluated, not adopted); MLX install refuses without downloading."""
    import importlib.util
    tab = VT.table()
    assert "onnx" not in tab["order"] and "turbo_onnx" not in tab["tiers"]
    assert "chatterbox-turbo-ONNX" in tab["tiers"]["turbo"]["needs_why"]
    assert "NOT adopted" in tab["tiers"]["turbo"]["needs_why"] or "not a replacement" in tab["tiers"]["turbo"]["needs_why"]
    spec = importlib.util.spec_from_file_location(
        "turbo_mlx_setup", ROOT / "modules" / "voice" / "crissa_voice" / "turbo_mlx_setup.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert m.installed() is False
    # on this Linux box unsupported() fires first; on a capable Mac the refusal is the unverified marker
    try:
        m.install(log=lambda *_: None, quant="4bit")
        raise AssertionError("install should refuse")
    except RuntimeError as e:
        assert "NOT YET VERIFIED" in str(e) or "Apple Silicon only" in str(e)
    why = m.unsupported(plat="darwin", machine="arm64", mac_ver=(14, 5), ram_gb=16.0)
    assert why == ""
    try:
        # force past unsupported by patching
        real = m.unsupported
        m.unsupported = lambda **k: ""
        try:
            m.install(log=lambda *_: None)
            raise AssertionError("install should refuse")
        except RuntimeError as e:
            assert "NOT YET VERIFIED" in str(e)
        finally:
            m.unsupported = real
    except Exception:
        raise


def test_set_language_keeps_user_pick(monkeypatch, pack):
    """--pick lasts through a language answer; only --probe/--reset may wipe it (ultra voice-module)."""
    monkeypatch.setattr(VT, "probe", lambda *a, **k: {
        "os": "linux", "arch": "x86_64", "ram_gb": 32, "cpu_cores": 16,
        "gpu": {"kind": "cuda", "name": "T", "total_gb": 24, "free_gb": 20},
        "language": {"code": "en", "name": "English", "from": "test"},
    })
    monkeypatch.setattr(VT, "broken", lambda: {})
    VT.ensure(pack)
    VT.override("pocket", pack)
    assert VT.read(pack)["picked_by"] == "user" and VT.read(pack)["main"] == "pocket"
    out = VT.set_language("Deutsch", pack)
    assert out["ok"] and out["code"] == "de"
    saved = VT.read(pack)
    assert saved["picked_by"] == "user" and saved["main"] == "pocket"
    assert VT.saved_language(pack)["code"] == "de"
