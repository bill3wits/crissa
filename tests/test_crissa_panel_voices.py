"""The Control Panel's Voices section (2026-10-06, the User): a Chatterbox tile shows Turbo's
state (on/off, its GPU, the failure named), Multilingual V3 has its own tile when installed (and an offer, not a
tile with a key, when Init found another language), and the Pocket tile stays. Audio8 (the announcer) has its tile
with its log, a Restart key and its failure named; the GPU voice's keep-warm has its tile with its log, Run now, the
last warm and its failure named.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_voice_panel as P  # noqa: E402
import crissa_voice_tiers as VT  # noqa: E402

GPU = {"kind": "cuda", "name": "NVIDIA RTX A6000", "free_gb": 20.0, "total_gb": 48.0}


def rec(main="turbo", lang="en", passed=None, chain=None):
    return {"main": main, "chain": chain or [main, "pocket", "kokoro"], "passed_over": passed or {},
            "facts": {"gpu": GPU, "language": {"code": lang, "name": VT.LANG_NAMES.get(lang, lang), "from": "LANG"}}}


class Mod:
    def __init__(self, st=None):
        self.st = st

    def record(self):
        return {"self_test": self.st} if self.st else {}


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path))
    monkeypatch.setattr(VT, "_home", lambda home=None: Path(home) if home else tmp_path)   # never the dev box's home
    import crissa_voice_warm as W
    monkeypatch.setattr(W, "_warm_aligner", lambda *a, **k: {"aligner": "not in this test"})
    w = {"rec": rec(), "installed": {"turbo": True, "multilingual": False, "pocket": True}, "up": set(), "st": {}}
    monkeypatch.setattr(VT, "read", lambda home=None: w["rec"])
    monkeypatch.setattr(VT, "_voice_mod", lambda name: Mod(w["st"].get(name.replace("_setup", ""))))

    def view():
        return P.view(up=lambda e: e in w["up"], installed=lambda e: w["installed"].get(e, False))
    w["view"] = view
    return w


def ids(v):
    return [t["id"] for t in v["tiles"]]


def test_chatterbox_leads_and_pocket_stays(world):
    world["up"] = {"turbo", "pocket"}
    v = world["view"]()
    assert ids(v) == ["turbo", "pocket", "audio8", "warm"]
    ch, po = v["tiles"][:2]
    assert ch["label"] == "Chatterbox" and ch["state"] == "ok" and ch["note"].startswith("on - Init's pick")
    assert "RTX A6000" in ch["gpu"] and "gpu" not in po
    assert po["label"] == "Pocket" and po["state"] == "ok" and "stand-in" in po["note"]


def test_chatterbox_off_says_why(world):
    v = world["view"]()
    assert v["tiles"][0]["state"] == "off" and "launcher starts it" in v["tiles"][0]["note"]
    world["installed"]["turbo"] = False
    t = world["view"]()["tiles"][0]
    assert t["state"] == "off" and "not installed" in t["note"] and "crissa-turbo.py --install" in t["note"]
    world["rec"] = rec("pocket", passed={"turbo": ["needs an NVIDIA GPU (CUDA); found none"]})
    t = world["view"]()["tiles"][0]
    assert "needs an NVIDIA GPU" in t["note"]


def test_a_failed_self_test_is_named_on_the_tile_with_the_gpu_it_ran_on(world):
    world["st"]["turbo"] = {"ok": False, "why": "CUDA error: no kernel image for sm_121", "gpu": "NVIDIA GB10",
                            "cap": "12.1"}
    world["rec"] = rec("pocket", passed={"turbo": ["failed its self-test: CUDA error"]})
    world["up"] = {"turbo"}                      # even if something answers, a failed self-test is the state
    t = world["view"]()["tiles"][0]
    assert t["state"] == "bad" and "self-test failed: CUDA error: no kernel image" in t["note"]
    assert t["gpu"] == "NVIDIA GB10 (sm_121)"


def test_multilingual_gets_its_own_tile_once_installed(world):
    world["installed"]["multilingual"] = True
    world["rec"] = rec("multilingual", "de", chain=["multilingual", "pocket", "kokoro"])
    world["up"] = {"multilingual"}
    v = world["view"]()
    assert ids(v) == ["turbo", "multilingual", "pocket", "audio8", "warm"]
    m = v["tiles"][1]
    assert m["label"] == "Chatterbox Multilingual V3" and m["state"] == "ok" and m["language"] == "de"
    assert "RTX A6000" in m["gpu"]
    world["st"]["multilingual"] = {"ok": False, "why": "out of GPU memory"}
    assert world["view"]()["tiles"][1]["note"] == "self-test failed: out of GPU memory"


def test_multilingual_is_only_an_offer_before_install_and_only_for_another_language(world):
    assert "multilingual" not in ids(world["view"]())                 # English: no tile at all
    world["rec"] = rec("turbo", "fr")
    m = world["view"]()["tiles"][1]
    assert m["offer"] and m["state"] == "off" and "offered for French" in m["note"]
    assert "crissa-multilingual.py --install" in m["note"]


def test_the_page_paints_voice_tiles_and_only_audio8_and_keep_warm_have_keys():
    page = (ROOT / "display" / "crissa-panel.html").read_text()
    assert "function paintVoices" in page and "SVC.voices" in page
    assert 'if(vid==="audio8"||vid==="warm") openVoiceLog(vid); return; }' in page   # other voice tiles: no key
    assert 'audio8:{act:"restart", label:"Restart"}, warm:{act:"run", label:"Run now"}' in page
    assert "/v1/voices/action" in page and "/v1/voices/log?id=" in page
    srv = (ROOT / "scripts" / "crissa-panel-server.py").read_text()
    assert '"/v1/voices"' in srv and '"voices": voices_view()' in srv
    assert '"/v1/voices/log"' in srv and '"/v1/voices/action"' in srv


def tile_of(v, tid):
    return next(t for t in v["tiles"] if t["id"] == tid)


def test_audio8_tile_names_its_failure_and_the_announcer_order(world):
    world["installed"]["audio8"] = True
    world["rec"]["announcer_chain"] = ["audio8", "pocket", "kokoro", "text"]
    world["up"] = {"audio8"}
    a = tile_of(world["view"](), "audio8")
    assert a["state"] == "ok" and "audio8 → pocket → kokoro → text" in a["note"]
    assert "restart" in a["actions"] and "log" in a["actions"]
    world["st"]["audio8"] = {"ok": False, "why": "onnxruntime: no CPU provider"}
    a = tile_of(world["view"](), "audio8")
    assert a["state"] == "bad" and "self-test failed: onnxruntime: no CPU provider" in a["note"]


def test_audio8_tile_says_why_it_is_not_first(world):
    world["installed"]["audio8"] = True
    world["rec"].update(announcer_chain=["pocket", "kokoro", "text"],
                        announcer_passed={"audio8": ["needs 16 GB of RAM, this machine has 8"]})
    a = tile_of(world["view"](), "audio8")
    assert "Audio8 is not first: needs 16 GB of RAM" in a["note"] or a["state"] == "off"


def test_keep_warm_tile_on_a_gpu_tier_and_off_elsewhere(world, monkeypatch):
    import crissa_voice_warm as W
    w = tile_of(world["view"](), "warm")
    assert w["engine"] == "turbo" and "run" in w["actions"] and w["last_ok"] == ""
    assert "never" in w["note"] and str(w["interval_s"]) in w["note"]
    world["rec"]["main"] = "pocket"
    w = tile_of(world["view"](), "warm")
    assert w["engine"] == "pocket", "round 28: the kit's table keeps Pocket warm too (on: all)"
    monkeypatch.setattr(W, "settings", lambda home=None: dict(W.DEFAULTS, on="gpu"))
    w = tile_of(world["view"](), "warm")
    assert w["state"] == "off" and "not one kept warm" in w["note"], "on: gpu is the GPU voices only"
    world["rec"]["main"] = "kokoro"
    monkeypatch.setattr(W, "settings", lambda home=None: dict(W.DEFAULTS, on="all"))
    assert tile_of(world["view"](), "warm")["state"] == "off", "a voice with no /stream is not warmed"


def test_keep_warm_run_records_the_last_warm_and_names_a_failure(world, tmp_path):
    import crissa_voice_warm as W
    import crissa_tts as T
    sent = []

    def ok(url, body, timeout):
        sent.append((url, body))
        return b"\0" * 48000, {"X-Sample-Rate": "24000"}
    out = W.warm_once(post=ok)
    assert out["ok"] and out["engine"] == "turbo" and sent[0][0] == T.stream_url("turbo")
    assert b"Mm, okay." in sent[0][1]
    w = tile_of(world["view"](), "warm")
    assert w["state"] == "ok" and w["last_ok"] == out["at"]

    def bad(url, body, timeout):
        raise ConnectionRefusedError("connection refused")
    out = W.warm_once(post=bad)
    assert not out["ok"] and "connection refused" in out["why"]
    w = tile_of(world["view"](), "warm")
    assert w["state"] == "bad" and "last warm failed: ConnectionRefusedError" in w["note"]
    log = P.log("warm")
    assert "connection refused" in log["text"] and log["path"].endswith("voice-warm.log")


def test_keep_warm_interval_and_switch_are_in_the_tier_table():
    import json
    tab = json.loads((ROOT / "config" / "crissa-voice-tiers.json").read_text())
    kw = tab["keep_warm"]
    assert kw["on"] == "all" and isinstance(kw["interval_s"], int) and kw["line"]   # round 28: Pocket too


def test_keep_warm_off_in_config_is_off(world, monkeypatch):
    import crissa_voice_warm as W
    monkeypatch.setattr(W, "settings", lambda home=None: dict(W.DEFAULTS, on=False))
    assert W.target()[0] == "" and "switched off" in W.target()[1]


def test_only_the_two_voice_keys_act():
    assert P.action("turbo", "restart")["ok"] is False
    assert P.action("warm", "rm -rf")["ok"] is False
    seen = []
    out = P.action("warm", "run", run=lambda argv: (seen.append(argv), (0, "{}"))[1])
    assert out["ok"] and seen[0][-1] == "--once" and seen[0][-2].endswith("crissa-voice-warm.py")
