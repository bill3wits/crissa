"""The machine check (2026-10-05): ON by default in a pack since 2026-10-10 (Init's permission card, the kill
CRISSA_MACHINE_CHECK=0), never on the house, measures what is here and recommends local / split (remote only when
the voice already leaves the LAN). The stages are stood in except render, which runs on a tiny film made here.
The card and its answers: tests/test_crissa_machine_card.py."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_machine as M  # noqa: E402

TH = dict(M.DEFAULTS)


def _st(render=10.0, tts_rtf=0.15, first=0.5, asr_rtf=0.3, align=None, tts_lan=True, asr_lan=True):
    st = {}
    if render is not None:
        st["render"] = {"x_realtime": render, "fps": render * 24}
    if tts_rtf is not None:
        st["tts"] = {"engine": "kokoro", "rtf": tts_rtf, "first_audio_s": first, "url_lan": tts_lan}
    if asr_rtf is not None:
        st["asr"] = {"rtf": asr_rtf, "url_lan": asr_lan}
    if align is not None:
        st["align"] = {"s_per_line": align}
    return st


def test_a_fast_machine_with_both_voice_stages_is_local():
    assert M.decide(_st(), TH) == ("local", [])


@pytest.mark.parametrize("kw,why", [
    ({"tts_rtf": 0.9}, "voice rtf"),
    ({"first": 2.0}, "first audio"),
    ({"asr_rtf": 1.2}, "speech-to-text rtf"),
    ({"asr_rtf": None}, "no speech-to-text"),
    ({"tts_rtf": None}, "no voice"),
])
def test_a_slow_or_missing_voice_stage_is_split_and_says_why(kw, why):
    tier, reasons = M.decide(_st(**kw), TH)
    assert tier == "split" and any(why in r for r in reasons), reasons


def test_a_slow_render_is_split_and_says_why():
    tier, reasons = M.decide(_st(render=1.5), TH)
    assert tier == "split" and any("the Wall itself is slow" in r for r in reasons), reasons


def test_missing_render_is_split_and_says_why():
    tier, reasons = M.decide(_st(render=None), TH)
    assert tier == "split" and any("render was not measured" in r for r in reasons), reasons


def test_a_slow_aligner_only_warns():
    tier, reasons = M.decide(_st(align=2.0), TH)
    assert tier == "local" and any("letter clock" in r for r in reasons)


def test_remote_is_only_reported_never_recommended():
    assert M.decide(_st(tts_lan=False), TH)[0] == "remote"
    assert M.decide(_st(asr_lan=False), TH)[0] == "remote"
    assert M.decide(_st(tts_lan=None), TH)[0] == "local", "a local engine with no url is not remote"


def test_on_by_default_in_a_pack_off_on_the_house_and_0_is_the_kill(tmp_path, monkeypatch):
    """the User 2026-10-10: on by default in a pack (behind Init's card); never on the house; =0 switches it off."""
    monkeypatch.setattr(M.crissa_home, "is_house", lambda *a, **k: False)
    assert M.on({}) is True and M.on({"CRISSA_MACHINE_CHECK": "1"}) is True
    for off in ("0", "off", "no"):
        assert M.on({"CRISSA_MACHINE_CHECK": off}) is False
        assert M.run_check(env={"CRISSA_MACHINE_CHECK": off}, home=tmp_path) == {"tier": None, "skipped": "off"}
    monkeypatch.setattr(M.crissa_home, "is_house", lambda *a, **k: True)
    assert M.on({}) is False, "the house's default stays off"
    assert M.run_check(env={}, home=tmp_path) == {"tier": None, "skipped": "off"}
    assert not (tmp_path / "machine.json").exists()


def test_never_on_the_house(tmp_path, monkeypatch):
    monkeypatch.setattr(M.crissa_home, "is_house", lambda *a, **k: True)
    assert M.run_check(env={"CRISSA_MACHINE_CHECK": "1"}, home=tmp_path) == {"tier": None, "skipped": "house"}
    assert not (tmp_path / "machine.json").exists()


def test_a_run_writes_one_answer_that_read_returns(tmp_path, monkeypatch):
    monkeypatch.setattr(M.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(M, "stage_render", lambda n, fps: ({"x_realtime": 9.0, "fps": 216.0}, ""))
    monkeypatch.setattr(M, "stage_tts", lambda text, rep, cfg=None: (
        {"engine": "say", "rtf": 0.1, "first_audio_s": 0.4, "url_lan": None}, "", b"\x01\x00" * 24000))
    monkeypatch.setattr(M, "stage_asr", lambda pcm, url, rep=1: (None, "asr: nobody at %s" % url))
    monkeypatch.setattr(M, "_align_url", lambda env: "")
    rec = M.run_check(quick=True, env={"CRISSA_MACHINE_CHECK": "1", "CRISSA_ASR_URL": "http://127.0.0.1:9"},
                      home=tmp_path, tts_cfg={"engine": "say"})           # a voice here: the hearing is expected too
    assert rec["tier"] == "split" and rec["schema"] == M.SCHEMA and rec["quick"] is True
    assert rec["permission"] == "allow" and rec["elapsed_s"] >= 0 and rec["pending"] == ["align"]
    assert "asr" not in rec["stages"] and any("nobody at http://127.0.0.1:9" in n for n in rec["notes"])
    assert M.read(tmp_path) == json.loads((tmp_path / "machine.json").read_text())
    assert not list(tmp_path.glob("*.tmp"))


def test_a_bar_can_be_changed_for_one_install(tmp_path):
    (tmp_path / "crissa-machine-tiers.json").write_text(json.dumps({"asr_rtf_max": 0.9, "not_a_bar": 1}))
    th = M.thresholds(tmp_path)
    assert th["asr_rtf_max"] == 0.9 and "not_a_bar" not in th
    assert th["tts_rtf_max"] == json.loads((ROOT / "config" / "crissa-machine-tiers.json").read_text())["tts_rtf_max"]


def test_the_shipped_bars_match_the_defaults():
    shipped = {k: v for k, v in json.loads((ROOT / "config" / "crissa-machine-tiers.json").read_text()).items()
               if not k.startswith("_") and k not in ("better_mouths", "card")}   # the aligner's rule; the card's estimate
    assert shipped == M.DEFAULTS


def test_read_is_none_for_nothing_or_another_schema(tmp_path):
    assert M.read(tmp_path) is None
    (tmp_path / "machine.json").write_text(json.dumps({"schema": "other/1"}))
    assert M.read(tmp_path) is None


def test_render_times_a_real_film(tmp_path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    film = tmp_path / "loop.mp4"
    w = cv2.VideoWriter(str(film), cv2.VideoWriter_fourcc(*"mp4v"), 24, (320, 176))
    for i in range(12):
        w.write(np.full((176, 320, 3), i * 10, np.uint8))
    w.release()
    got, why = M.stage_render(30, 24, film)
    assert why == "" and got["frames"] == 30 and got["x_realtime"] > 0, "it loops the film to reach the count"


def test_the_cli_says_off_and_exits_3(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("mc_cli", ROOT / "scripts" / "crissa-machine-check.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setenv("CRISSA_MACHINE_CHECK", "0")              # the kill switch (unset is on in a pack, 2026-10-10)
    assert cli.main(["--dry"]) == 3
    assert "off" in capsys.readouterr().err
    monkeypatch.delenv("CRISSA_MACHINE_CHECK", raising=False)
    monkeypatch.setattr(cli.M.crissa_home, "is_house", lambda *a, **k: True)
    assert cli.main(["--dry"]) == 3 and cli.main(["--card"]) == 3, "this tree is the house: off, and no card"
