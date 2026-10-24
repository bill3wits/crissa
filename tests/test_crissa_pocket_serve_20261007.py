"""Crissa on Pocket with Audio8 installed (2026-10-07, the .101 retest of final-20261007c): the Connect page said "Crissa's
voice is ready: Pocket TTS. The announcer speaks with Audio8 TTS." and Talk failed with "no speech engine answered
(voice http://127.0.0.1:8188/stream, backup kokoro)".

  - Pocket WAS serving: that line is what house-speak printed for any failure on the streamed path, playing the line
    included (reproduced with `pactl` missing). The last line - the one the Wall shows - now says what failed, and the
    launcher says at start when the programs her voice plays through are missing.
  - "backup kokoro": the saved voice pick was the 10-07 morning release's ([pocket, kokoro, text]) and was read
    forever. A pick Init made is made again when this release picks differently; the old one is kept beside it.
  - Audio8 was installed and never asked: Crissa's chain and every streamed voice's backup now carry the voices that
    are installed (Pocket first, then Audio8, then the later fallbacks). Nothing is removed from any chain.
  - A launcher stopped mid-install left "installing" behind for an hour; an install whose process is gone is not running.
No server runs here: health, installs and processes are fakes."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_home  # noqa: E402
import crissa_tts as T  # noqa: E402
import crissa_voice_install as VI  # noqa: E402
import crissa_voice_tiers as VT  # noqa: E402


# Audio8 as a VOICE is behind its switch since 2026-10-17 (the User: Audio8 is her ear, Pocket her voice; lib/
# crissa_voice_tiers.not_a_voice). These tests pin the Audio8-voice path that the switch keeps; the rule itself is
# tests/test_audio8_not_a_voice_20261017.py.
@pytest.fixture(autouse=True)
def _audio8_may_speak(monkeypatch):
    monkeypatch.setenv("CRISSA_AUDIO8_TTS", "1")


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HS = _load("house_speak_pocket_serve", "scripts/house-speak.py")
ZL = _load("crissa_launcher_pocket_serve", "scripts/crissa.py")


@pytest.fixture
def pack(monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(T, "_house", lambda: False)
    monkeypatch.setattr(VT, "is_house", lambda: False)
    monkeypatch.setattr(VT, "broken", lambda: {})
    monkeypatch.delenv("CRISSA_TTS_INSTALLED_BACKUP", raising=False)
    monkeypatch.delenv("CRISSA_VOICE_REPICK", raising=False)


def _audio8_installed(monkeypatch, yes=True):
    monkeypatch.setattr(T, "_installed_here", lambda name: yes and name == "audio8")


# ---- B: what is installed stands in, Pocket first, then Audio8 -------------------------------------------------------

def test_audio8_joins_crissas_chain_right_after_the_streamed_voices(pack, monkeypatch):
    _audio8_installed(monkeypatch)
    assert T.with_installed(["pocket", "text"]) == ["pocket", "audio8", "text"]
    assert T.with_installed(["pocket", "kokoro", "text"]) == ["pocket", "audio8", "kokoro", "text"]
    assert T.with_installed(["turbo", "pocket", "say", "text"]) == ["turbo", "pocket", "audio8", "say", "text"]
    assert T.with_installed(["say"], "pocket") == ["audio8", "say"], "a backup list behind Pocket: Audio8 first"
    assert T.with_installed(["pocket", "audio8", "text"]) == ["pocket", "audio8", "text"], "never twice"
    assert T.with_installed(["pocket", "text"], "audio8") == ["pocket", "text"], "never behind itself"


def test_nothing_is_added_when_audio8_is_not_installed_or_switched_off_or_on_the_house(pack, monkeypatch):
    _audio8_installed(monkeypatch, yes=False)
    assert T.with_installed(["pocket", "kokoro", "text"]) == ["pocket", "kokoro", "text"]
    _audio8_installed(monkeypatch)
    assert T.with_installed(["pocket", "text"], env={"CRISSA_TTS_INSTALLED_BACKUP": "0"}) == ["pocket", "text"]
    monkeypatch.setattr(T, "_house", lambda: True)
    assert T.with_installed(["pocket", "text"]) == ["pocket", "text"], "the house names its own voices"


def test_pockets_backup_is_audio8_then_the_later_fallbacks(pack, monkeypatch):
    _audio8_installed(monkeypatch)
    assert T._backup_of("pocket", {"chain": ["pocket", "text"]}) == ["audio8", "kokoro", "say"]  # nothing after Pocket: the table's backup
    assert T._backup_of("pocket", {"chain": ["pocket", "kokoro", "text"]}) == ["audio8", "kokoro"]  # Kokoro already in the chain
    _audio8_installed(monkeypatch, yes=False)
    assert T._backup_of("pocket", {"chain": ["pocket", "kokoro", "text"]}) == ["kokoro"], "unchanged without Audio8"


def test_pocket_up_speaks_with_audio8_named_as_its_backup(pack, monkeypatch):
    _audio8_installed(monkeypatch)
    rec = {"schema": VT.SCHEMA, "main": "pocket", "chain": ["pocket", "text"], "announcer": "audio8",
           "announcer_chain": ["audio8", "pocket", "kokoro", "text"]}
    monkeypatch.setattr(T, "_tier_record", lambda home=None: rec)
    monkeypatch.setattr(T, "stream_up", lambda name, e=None: name == "pocket")
    got = T._by_tier({}, {"CRISSA_PORT_OFFSET": "0"})
    assert got["engine"] == "pocket" and got["url"].endswith(":8188/stream")
    assert got["backup"] == ["audio8", "kokoro", "say"] and got["announcer"] == "audio8"


def test_pocket_down_audio8_speaks_and_the_screen_says_pocket_was_passed_over(pack, monkeypatch):
    _audio8_installed(monkeypatch)
    rec = {"schema": VT.SCHEMA, "main": "pocket", "chain": ["pocket", "text"], "announcer": "audio8",
           "announcer_chain": ["audio8", "pocket", "kokoro", "text"]}
    monkeypatch.setattr(T, "_tier_record", lambda home=None: rec)
    monkeypatch.setattr(T, "stream_up", lambda name, e=None: False)
    monkeypatch.setattr(T, "audio8_up", lambda e=None, **k: True)
    got = T._by_tier({}, {})
    assert got["engine"] == "audio8" and got["tier_main"] == "pocket"
    assert got["passed"] and got["passed"][0].startswith("pocket: no Pocket server")
    monkeypatch.setattr(T, "audio8_up", lambda e=None, **k: False)
    assert T._by_tier({}, {}) == {}, "neither answers: text, as before"


def test_house_speak_asks_the_whole_backup_chain_audio8_included():
    src = (ROOT / "scripts" / "house-speak.py").read_text()
    seam = src.split("def _crissa_tts_cfg()", 1)[1].split("def _try_crissa_tts(", 1)[0]
    assert 'b in crissa_tts.ENGINES' in seam and '_STREAMED["backup_chain"] = backup' in seam
    assert "audio8" in T.ENGINES and "audio8" in T.INSTALLED_BACKUPS


# ---- A: the Wall's last line says what failed ------------------------------------------------------------------------

def test_a_missing_player_is_said_as_that_not_as_no_engine():
    why, made = HS.why_failed(FileNotFoundError(2, "No such file or directory", "pactl"))
    assert made is False and "'pactl' is not installed" in why and "pulseaudio-utils" in why
    why, made = HS.why_failed(FileNotFoundError(2, "No such file or directory", "paplay"))
    assert made is False and "paplay" in why


def test_a_voice_that_is_down_or_refuses_is_said_with_its_address():
    url = "http://127.0.0.1:8188/stream"
    why, made = HS.why_failed(urllib.error.URLError(ConnectionRefusedError(111, "refused")), url)
    assert made and why == "nothing answered at 127.0.0.1:8188"
    why, made = HS.why_failed(urllib.error.HTTPError(url, 500, "boom", {}, None), url)
    assert made and "refused the line: HTTP 500" in why
    why, made = HS.why_failed(subprocess.CalledProcessError(1, ["paplay", "x.wav"]))
    assert made is False and "paplay exited 1" in why


def test_the_final_line_carries_the_reason_and_keeps_the_old_words():
    src = (ROOT / "scripts" / "house-speak.py").read_text()
    tail = src.split("why, made = why_failed(e)", 1)[1].split("# ref fallback", 1)[0]
    assert "no speech engine answered (voice %s, backup %s): %s." in tail, "the engine case keeps its words + why"
    assert "she could not be heard: %s (her voice: %s at %s; backup %s)" in tail
    assert tail.index("rc = _try_backup()") < tail.index("she could not be heard"), "the backups are still asked first"
    backup = src.split("def _try_backup()", 1)[1].split("\n    # THE VOICE SEAM", 1)[0]
    assert 'print(f"backup {b} fail: {e}", flush=True)' in backup, "a backup that cannot play moves on, no traceback"


def test_the_launcher_says_at_start_when_her_sound_programs_are_missing():
    class A:
        no_voice, voice, audio_at = False, "/x/modules/voice", "host"
    none = lambda t: None  # noqa: E731
    if sys.platform.startswith("linux"):
        assert ZL.sound_tools_missing(A, which=none) == ["pactl", "paplay"]
        assert ZL.sound_tools_missing(A, which=lambda t: "/usr/bin/" + t) == []
        line = ZL.sound_tools_plain(["pactl", "paplay"])
        assert "pactl and paplay are not installed" in line and "pulseaudio-utils" in line and "sudo" not in line
    A.audio_at = "page"
    assert ZL.sound_tools_missing(A, which=none) == [], "the page plays her voice: nothing to say"
    src = (ROOT / "scripts" / "crissa.py").read_text()
    assert "missing = sound_tools_missing(a)" in src


# ---- the saved pick from an earlier release ----------------------------------------------------------------------------

def _facts():
    return {"os": "linux", "arch": "x86_64", "cpu_cores": 32, "ram_gb": 90.0, "python": "3.14", "piper_model": False,
            "gpu": {"kind": "none"}, "language": {"code": "en", "name": "English", "from": "test"}}


def test_a_pick_from_the_morning_release_is_made_again_and_kept_beside(pack, tmp_path):
    f = _facts()
    now = VT.pick(f, VT.table(tmp_path))
    old = dict(now, schema=VT.SCHEMA, facts=f, picked_by="init", chain=["pocket", "kokoro", "text"])
    VT.record_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    VT.record_path(tmp_path).write_text(json.dumps(old))
    assert "pocket -> kokoro -> text" in VT.stale_pick(old, tmp_path, {})
    rec = VT.ensure(tmp_path, facts=f)
    assert rec["chain"] == now["chain"] and "kokoro" not in rec["chain"]
    assert any("earlier release" in s for s in rec["say"])
    kept = json.loads((tmp_path / VT.PREV_NAME).read_text())
    assert kept["chain"] == ["pocket", "kokoro", "text"], "the old pick is kept, never deleted"
    assert VT.stale_pick(VT.read(tmp_path), tmp_path, {}) == "", "the new pick is stable"


def test_a_current_pick_and_the_persons_own_pick_are_kept(pack, tmp_path, monkeypatch):
    f = _facts()
    now = dict(VT.pick(f, VT.table(tmp_path)), schema=VT.SCHEMA, facts=f, picked_by="init")
    assert VT.stale_pick(now, tmp_path, {}) == ""
    mine = dict(now, picked_by="user", chain=["pocket", "kokoro", "text"])
    assert VT.stale_pick(mine, tmp_path, {}) == ""
    monkeypatch.setenv("CRISSA_VOICE_REPICK", "0")
    assert VT.stale_pick(dict(now, chain=["pocket", "kokoro", "text"]), tmp_path, {}) == ""


# ---- an install whose launcher is gone is not running -----------------------------------------------------------------

def test_a_dead_installers_flag_does_not_block_the_next_start(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(VI, "_home", lambda home=None: tmp_path)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    VI._put(tmp_path, state="installing", t=time.time(), pid=dead.pid)
    assert VI.running(tmp_path) is False
    VI._put(tmp_path, state="installing", t=time.time(), pid=os.getpid())
    assert VI.running(tmp_path) is True
    st = json.loads((tmp_path / VI.STATE_FILE).read_text())
    st.pop("pid")
    (tmp_path / VI.STATE_FILE).write_text(json.dumps(st))
    assert VI.running(tmp_path) is True, "an older state file (no pid) reads as before"
    VI._put(tmp_path, state="installing", t=time.time() - 7200, pid=os.getpid())
    assert VI.running(tmp_path) is False, "the hour still ends it, as before"


# ---- every machine: the chain is still Init's pick for THAT machine, plus only what Init installed there ---------------

_CUDA = {"kind": "cuda", "name": "NVIDIA RTX 4090", "total_gb": 24.0, "free_gb": 23.0, "count": 1}


def _f(os_name="linux", arch="x86_64", ram=16.0, cores=8, gpu=None, macos=""):
    f = {"os": os_name, "arch": arch, "cpu_cores": cores, "ram_gb": ram, "python": "3.14", "piper_model": False,
         "gpu": gpu or {"kind": "none"}, "language": {"code": "en", "name": "English", "from": "test"}}
    if os_name == "darwin":
        f["macos"] = macos
        if arch == "arm64":
            f["gpu"] = {"kind": "mps", "name": "Apple M1", "unified_gb": ram}
    return f


TIERS = {
    "nvidia, strong cpu": _f(ram=64, cores=16, gpu=_CUDA),
    "nvidia, weak cpu": _f(ram=12, cores=4, gpu=_CUDA),
    "strong cpu, no gpu": _f(ram=90, cores=32),
    "weak cpu, no gpu": _f(ram=8, cores=4),
    "mac m1 8 GB": _f("darwin", "arm64", 8, 8, macos="14.0"),
    "mac m2 16 GB": _f("darwin", "arm64", 16, 8, macos="14.0"),
}


# Her chain on each machine: Audio8 on a robust CPU with Pocket behind it; Turbo first on NVIDIA with Pocket next
# and Audio8 after it where installed; Pocket then Kokoro on a weak machine and an 8 GB Mac (the User 2026-10-08).
WANT = {
    "nvidia, strong cpu": ["turbo", "pocket", "audio8", "text"],
    "nvidia, weak cpu": ["turbo", "pocket", "text"],
    "strong cpu, no gpu": ["audio8", "pocket", "text"],
    "weak cpu, no gpu": ["pocket", "kokoro", "text"],
    "mac m1 8 GB": ["pocket", "kokoro", "say", "text"],
    "mac m2 16 GB": ["audio8", "pocket", "say", "text"],
}


@pytest.mark.parametrize("name", sorted(TIERS))
def test_each_tier_keeps_its_own_order_and_gains_audio8_only_where_init_installed_it(pack, monkeypatch, name):
    rec = VT.pick(TIERS[name], VT.table())
    ann = rec.get("announcer_chain") or []
    # Init installs Audio8 only on a robust machine (its needs); elsewhere it is not there
    monkeypatch.setattr(T, "_installed_here", lambda n: n == "audio8" and ann[:1] == ["audio8"])
    chain = VT.chain(rec)
    got = T.with_installed(chain)
    assert got == WANT[name], (name, got)
    assert [c for c in got if c != "audio8"] == [c for c in chain if c != "audio8"], "nothing dropped or reordered"
    assert ann[:1] == (["audio8"] if "audio8" in got else ["pocket"]), "the announcer: Audio8 where robust, else Pocket"
    if TIERS[name]["gpu"].get("kind") == "cuda":
        assert got[:2] == ["turbo", "pocket"], "the GPU voice first, Pocket behind it, as before"
    if TIERS[name]["os"] == "darwin":
        assert "say" in got and got.index("say") == len(got) - 2, "the Mac's own voice stays, just before text"


# ---- the afternoon (the User 2026-10-07): Audio8 is Crissa's voice on a robust CPU machine, and she is never a man ------

def test_a_robust_cpu_machines_old_pocket_pick_is_made_again_as_audio8_then_pocket(pack, tmp_path):
    """.101's saved pick ([pocket, kokoro, text] or this morning's [pocket, text]) is made again by this release."""
    f = _facts()
    for chain in (["pocket", "kokoro", "text"], ["pocket", "text"]):
        old = dict(VT.pick(f, VT.table(tmp_path)), schema=VT.SCHEMA, facts=f, picked_by="init", main="pocket",
                   tier="pocket", chain=chain)
        VT.record_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        VT.record_path(tmp_path).write_text(json.dumps(old))
        assert VT.stale_pick(old, tmp_path, {}), chain
        rec = VT.ensure(tmp_path, facts=f)
        assert rec["main"] == "audio8" and rec["chain"] == ["audio8", "pocket", "text"], rec["chain"]
        assert rec["announcer_chain"][0] == "audio8" and rec.get("announcer", "") == ""


def test_audio8_as_her_voice_has_pocket_as_its_backup_when_it_fails_a_line(pack, monkeypatch):
    rec = {"schema": VT.SCHEMA, "main": "audio8", "chain": ["audio8", "pocket", "text"],
           "announcer_chain": ["audio8", "pocket", "kokoro", "text"]}
    monkeypatch.setattr(T, "_tier_record", lambda home=None: rec)
    monkeypatch.setattr(T, "_installed_here", lambda name: name == "audio8")
    monkeypatch.setattr(T, "audio8_up", lambda e=None, **k: True)
    monkeypatch.setattr(T, "stream_up", lambda name, e=None: name == "pocket")
    got = T._by_tier({}, {})
    assert got["engine"] == "audio8" and got["backup"][0] == "pocket", got
    order = []

    def fake(name):
        def fn(text, cfg, speaker, timeout):
            order.append((name, speaker))
            if name == "audio8":
                raise T.TTSError("cannot reach Audio8: refused")
            return b"\0\0" * 24000, 24000
        return fn
    monkeypatch.setattr(T, "_audio8", fake("audio8"))
    monkeypatch.setattr(T, "_streamed", fake("pocket"))
    monkeypatch.setattr(T, "_note_spoke", lambda *a, **k: None)
    pcm, sr = T.synth("Hello there.", "crissa", got)
    assert order == [("audio8", "crissa"), ("pocket", "crissa")] and sr == 24000
    monkeypatch.setattr(T, "audio8_up", lambda e=None, **k: False)            # Audio8 stopped: Pocket is her voice
    got = T._by_tier({}, {})
    assert got["engine"] == "pocket" and got["tier_main"] == "audio8"
    assert got["passed"][0].startswith("audio8: no Audio8 server")


def test_a_gpu_machine_keeps_pocket_right_behind_turbo(pack, monkeypatch):
    f = dict(_facts(), gpu={"kind": "cuda", "name": "NVIDIA RTX 4090", "total_gb": 24.0, "free_gb": 23.0, "count": 1})
    rec = VT.pick(f, VT.table())
    assert rec["chain"] == ["turbo", "pocket", "text"] and rec["announcer_chain"][0] == "audio8"
    _audio8_installed(monkeypatch)
    assert T.with_installed(VT.chain(rec)) == ["turbo", "pocket", "audio8", "text"]


def test_crissa_is_never_a_mans_voice_on_any_engine(pack, monkeypatch, capsys):
    assert T.she_voice("kokoro", "crissa", "bm_george") == "af_heart"
    assert T.she_voice("kokoro", "lila", "am_adam") == "bf_emma"
    assert T.she_voice("kokoro", "crissa", "af_heart") == "af_heart"
    assert T.she_voice("say", "crissa", "Daniel") == "Samantha" and T.she_voice("say", "crissa", "Karen") == "Karen"
    assert T.she_voice("audio8", "crissa", "some_clone") == "crissa_imagine" and T.she_voice("audio8", "crissa", "anna") == "anna"
    assert T.she_voice("kokoro", "announcer", "bm_george") == "bm_george", "the announcer keeps its own voices"
    assert "is not a woman's voice" in capsys.readouterr().err
    assert T.face_default("audio8", "crissa") == "crissa_imagine" and T.face_default("pocket", "crissa") == "crissa"
    assert T.face_default("kokoro", "crissa") == "af_heart" and T.face_default("say", "crissa") == "Samantha"


def test_audio8_asks_for_her_own_voice_never_the_announcers(pack, monkeypatch):
    seen = []

    class R:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            import io
            import wave
            b = io.BytesIO()
            with wave.open(b, "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(b"\0\0" * 24000)
            return b.getvalue()

    def fake(req, timeout, why):
        seen.append(json.loads(req.data)["voice_name"])
        return R(req.data)
    monkeypatch.setattr(T.crissa_egress, "urlopen", fake)
    T._audio8("Hello.", {"url": "http://127.0.0.1:1"}, "crissa", 5)
    T._audio8("Hello.", {"url": "http://127.0.0.1:1", "voice": "george"}, "crissa", 5)
    T._audio8("Hello.", {"url": "http://127.0.0.1:1"}, "announcer", 5)
    assert seen[:2] == ["crissa_imagine", "crissa_imagine"] and seen[2] == T.audio8_voice()


def test_crissas_face_clip_is_a_womans_voice_on_every_engine():
    F = _load("faces_pocket_serve", "modules/voice/crissa_voice/faces.py")
    # her own clip since 2026-10-08 (created with Grok, ships); jane (VCTK p339, a woman) is her backup clip
    assert F.STOCK["crissa"]["stock"] == "crissa_imagine" and F.STOCK["crissa"]["shipped"] == "faces/crissa-imagine.wav"
    assert F.FACE_BACKUP["crissa"] == "jane" and F.EXTRA["jane"]["file"] == "vctk/p339_023_enhanced.wav"
    sys.path.insert(0, str(ROOT / "modules" / "voice"))
    from crissa_voice import pocket_setup as PS  # noqa: PLC0415
    assert "alba" in PS.VOICES and "alba" not in PS.FEMALE, "alba stays allowed by name; it is no face's voice"
    srv = (ROOT / "modules" / "voice" / "scripts" / "crissa-pocket.py").read_text()
    assert '"crissa": "crissa"' in srv and '"default": "azelma"' in srv
    tab = json.loads((ROOT / "config" / "crissa-voice-tiers.json").read_text())
    assert tab["tiers"]["pocket"]["settings"]["voices"]["crissa"] == "crissa"   # round 28
    assert "only when the person names it" not in json.dumps(tab), "the old rule is gone from the docs"


def test_the_browsers_own_output_goes_to_the_full_log(monkeypatch):
    got, argv = [], []

    class P:
        def __init__(self, a, **kw):
            import io
            argv.append((a, kw))
            self.stdout = io.BytesIO(b"[1:2:ERROR:zygote_host_impl_linux.cc] Failed sending zygote boot message: "
                                     b"Broken pipe\n\nOpening in existing browser session.\n")
    import threading
    done = threading.Event()

    def log(line):
        got.append(line)
        if len(got) == 2:
            done.set()
    assert ZL.open_page("http://127.0.0.1:9000/connect", plat="linux", which=lambda n: "/usr/bin/" + n,
                        popen=P, log=log)
    done.wait(5)
    assert got == ["[1:2:ERROR:zygote_host_impl_linux.cc] Failed sending zygote boot message: Broken pipe",
                   "Opening in existing browser session."], "every line kept, none on the console's plain lines"
    a, kw = argv[0]
    assert a == ["/usr/bin/xdg-open", "http://127.0.0.1:9000/connect"]
    assert kw["stdout"] == subprocess.PIPE and kw["stderr"] == subprocess.STDOUT and kw["restore_signals"] is False
    assert kw["start_new_session"] is True
    src = (ROOT / "scripts" / "crissa.py").read_text()
    run_src = src.split("def _run(")[1].split("\ndef service_cmd(")[0]
    assert "open_page(" not in run_src, "the desk does not open the talking page"
    assert "def open_page(" in src
    assert "webbrowser.open(page)" not in src.split("def open_page")[0]


def test_every_line_says_which_voice_spoke_it():
    """Proof on the machine, not a guess (2026-10-07): Pocket names its stock voice in the reply and its log, and
    house-speak / the seam print it for Audio8, Pocket, Kokoro and say."""
    srv = (ROOT / "modules" / "voice" / "scripts" / "crissa-pocket.py").read_text()
    assert 'self.send_header("X-Voice", stock)' in srv and '_log(event="job", face=voice[:40], voice=stock' in srv
    hs = (ROOT / "scripts" / "house-speak.py").read_text()
    assert 'resp.headers.get("X-Voice")' in hs
    src = (ROOT / "lib" / "crissa_tts.py").read_text()
    for eng in ("audio8", "kokoro", "say"):
        assert '"voice: %s speaks this line as %%s (for %%s)"' % eng in src, eng


def test_audio8_speaks_every_word_of_a_long_line(monkeypatch):
    """The old Audio8 request cut text at 800 characters and audio at 512 tokens (a 474-character line lost its
    last 4 s on the machine). Now a long line goes in pieces at sentence ends, each with room for every word."""
    import io, json, wave
    line = ("Okay, so here is the plan for the afternoon. First we look at the wall and check that every card has a "
            "name and a date on it. Then we go through the voice settings one at a time, so that nothing changes "
            "without you hearing it first. ") * 6
    for t in (line, "word " * 400, "x" * 1500, "Hi."):
        ps = T.audio8_pieces(t)
        assert "".join(ps).replace(" ", "") == "".join(t.split()), t[:20]
        assert all(len(p) <= T.AUDIO8_PIECE_CHARS for p in ps)
        assert all(T.audio8_tokens(p) >= 2 * len(p) for p in ps)
    sent = []

    class R:
        def __init__(self, n):
            b = io.BytesIO()
            with wave.open(b, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\0\0" * n)
            self.d = b.getvalue()
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.d

    def fake(req, timeout, what):
        body = json.loads(req.data)
        sent.append(body)
        return R(len(body["text"]))
    monkeypatch.setattr(T.crissa_egress, "urlopen", fake)
    pcm, sr = T._audio8(line, {"url": "http://127.0.0.1:1"}, "crissa", 5)
    assert len(sent) > 1 and " ".join(b["text"] for b in sent) == " ".join(line.split())
    assert all(b["voice_name"] == "crissa_imagine" and b["max_new_tokens"] >= 2 * len(b["text"]) for b in sent)
    assert sr == 24000 and len(pcm) == 2 * sum(len(b["text"]) for b in sent)


def test_stopping_the_audio8_lane_stops_its_server(tmp_path):
    """The panel's Stop for Audio8 sends SIGTERM to the lane (crissa-audio8.py); the server it started must stop
    too (2026-10-07: the scratch run left uvicorn serving on the port after 'audio8 stopped')."""
    import signal, subprocess, sys, time
    wrapper = (ROOT / "modules" / "voice" / "scripts" / "crissa-audio8.py").read_text()
    serve = wrapper.split('print("crissa-audio8: serving on', 1)[1]
    assert "signal.signal(signal.SIGTERM, _stop)" in serve and "p.terminate()" in serve
    # the same shape, run for real: a wrapper whose child would outlive it without the handler
    prog = tmp_path / "w.py"
    prog.write_text(
        "import signal, subprocess, sys\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "stopping = []\n"
        "def _stop(_s, _f):\n"
        "    stopping.append(True)\n"
        "    p.terminate()\n"
        "signal.signal(signal.SIGTERM, _stop)\n"
        "print(p.pid, flush=True)\n"          # only once the handler is in (a TERM before it was a race)
        "rc = p.wait()\n"
        "raise SystemExit(0 if stopping else rc)\n")
    w = subprocess.Popen([sys.executable, str(prog)], stdout=subprocess.PIPE, text=True)
    child = int(w.stdout.readline())
    w.send_signal(signal.SIGTERM)
    assert w.wait(10) == 0
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the child outlived its wrapper")
