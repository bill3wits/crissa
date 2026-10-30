"""house-speak writes one start and one end record per line (the User 10-01).

Every run here is pointed at temp files: the line log, the TTS gate, the tool hold and HOME, so
nothing touches the house's own state. No audio plays: the lines end before synthesis or are
stopped while the drill path waits on a FIFO that never writes.
"""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEAK = ROOT / "scripts" / "house-speak.py"


def _env(tmp: Path, **over) -> dict:
    (tmp / "home").mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update({
        "HOME": str(tmp / "home"),
        "CRISSA_HOME": str(tmp / "state"),
        "HOUSE_SPEAK_LINE_LOG_PATH": str(tmp / "lines.jsonl"),
        "HOUSE_TTS_GATE": str(tmp / "gate.json"),
        "HOUSE_TOOL_HOLD": str(tmp / "no-hold.json"),
        "VO_MOUTH_URL": "http://127.0.0.1:9/v1/mouth",
    })
    env.update(over)
    return env


def _records(tmp: Path) -> list[dict]:
    p = tmp / "lines.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


def test_a_line_that_ends_writes_start_then_end(tmp_path):
    r = subprocess.run([sys.executable, str(SPEAK), "--voice", "crissa", "--text", ""],
                       env=_env(tmp_path), capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stderr[-400:]
    recs = _records(tmp_path)
    assert [x["event"] for x in recs] == ["start", "end"], recs
    start, end = recs
    assert start["pid"] == end["pid"] and start["voice"] == "crissa"
    assert end["rc"] == 2 and end["ended"] == "return" and end["clock"] == "letter"
    assert end["sheets"] == 0 and end["posts_ok"] == 0 and end["posts_failed"] == 0
    assert not any(k.startswith("_") for k in end), "internal keys stay out of the record"


def test_a_stopped_line_still_writes_its_end(tmp_path):
    fifo = tmp_path / "never.fifo"
    os.mkfifo(fifo)               # the drill's ffmpeg decode blocks on it: a line in flight
    p = subprocess.Popen([sys.executable, str(SPEAK), "--voice", "crissa", "--text", "hello there",
                          "--wav", str(fifo)], env=_env(tmp_path), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.time() + 60
        while time.time() < deadline and not _records(tmp_path):
            time.sleep(0.1)
        time.sleep(0.5)
        os.kill(p.pid, signal.SIGTERM)
        assert p.wait(timeout=30) == 143
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    recs = _records(tmp_path)
    assert [x["event"] for x in recs] == ["start", "end"], recs
    assert recs[1]["rc"] == 143 and recs[1]["ended"] == "signal %d" % signal.SIGTERM
    assert recs[1]["chars"] == len("hello there") and recs[0]["drill"] is True


def test_the_kill_switch_writes_nothing(tmp_path):
    subprocess.run([sys.executable, str(SPEAK), "--text", ""], env=_env(tmp_path, HOUSE_SPEAK_LINE_LOG="0"),
                   capture_output=True, timeout=120)
    assert not (tmp_path / "lines.jsonl").exists()


def test_a_failed_sheet_post_is_counted_with_its_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_SPEAK_LINE_LOG_PATH", str(tmp_path / "lines.jsonl"))
    monkeypatch.setenv("VO_MOUTH_URL", "http://127.0.0.1:9/v1/mouth")
    monkeypatch.setenv("HOUSE_TTS_GATE", str(tmp_path / "gate.json"))
    spec = importlib.util.spec_from_file_location("hs_line_log", SPEAK)
    hs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hs)
    monkeypatch.setattr(hs, "_mouth_air", lambda *a, **k: None)
    air = hs.MouthAir(time.time(), 24000)
    assert hs._LINE["sheets"] == 1
    air._q.put({"pts0": time.time(), "rms": [0.1]})
    import threading
    t = threading.Thread(target=air._drain, daemon=True)
    t.start()
    deadline = time.time() + 10
    while time.time() < deadline and not hs._LINE["posts_failed"]:
        time.sleep(0.05)
    air._stop.set()
    t.join(timeout=2)
    assert hs._LINE["posts_failed"] == 1 and hs._LINE["posts_ok"] == 0
    assert hs._LINE["post_error"].startswith("URLError"), hs._LINE["post_error"]
