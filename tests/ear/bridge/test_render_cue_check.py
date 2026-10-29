import importlib.util, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "modules" / "ear"
spec = importlib.util.spec_from_file_location("rcc", ROOT / "scripts" / "render_cue_check.py")
rcc = importlib.util.module_from_spec(spec); spec.loader.exec_module(rcc)


def test_off_by_default_plays_nothing(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("RAIL_RENDER_CUE_CHECK", raising=False)
    played = []
    monkeypatch.setattr(rcc.urllib.request, "urlopen", lambda *a, **k: played.append(a))
    assert rcc.main(["--clip", str(tmp_path / "x.wav")]) == 2 and not played
    assert "OFF" in capsys.readouterr().out


def test_holds_during_a_live_session(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RAIL_RENDER_CUE_CHECK", "1")
    clip = tmp_path / "c.wav"; clip.write_bytes(b"RIFF")
    monkeypatch.setattr(rcc, "live_session", lambda: "a session with her is open")
    monkeypatch.setattr(rcc.time, "sleep", lambda s: None)
    assert rcc.main(["--clip", str(clip), "--wait", "0"]) == 3
    assert "HELD" in capsys.readouterr().out


def test_pass_and_fail_read_the_corrected_heard_text(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RAIL_RENDER_CUE_CHECK", "1")
    import wave
    clip = tmp_path / "c.wav"
    with wave.open(str(clip), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 160)
    log = tmp_path / "eb.jsonl"; log.write_text("")
    monkeypatch.setattr(rcc, "LOG", log)
    monkeypatch.setattr(rcc, "live_session", lambda: "")
    monkeypatch.setattr(rcc.time, "sleep", lambda s: None)
    fed = []
    class R:
        def read(self): return b"{}"
    def feed(text):
        def urlopen(req, timeout=None):
            fed.append(req.full_url)
            log.write_text(log.read_text() + json.dumps({"event": "heard", "text": text}) + "\n")
            return R()
        return urlopen
    played = []
    monkeypatch.setattr(rcc.subprocess, "run", lambda *a, **k: played.append(a))
    monkeypatch.setattr(rcc.urllib.request, "urlopen", feed("Put the render queue on the wall."))
    assert rcc.main(["--clip", str(clip)]) == 0 and "PASS" in capsys.readouterr().out
    monkeypatch.setattr(rcc.urllib.request, "urlopen", feed("Put the render cube on the wall."))
    assert rcc.main(["--clip", str(clip)]) == 1 and "FAIL" in capsys.readouterr().out
    assert fed and all(u.endswith("/stt") for u in fed)
    assert not played, "the check must never play audio"
