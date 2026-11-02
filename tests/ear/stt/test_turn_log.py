import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crissa_ear.turn_log import TurnLog  # noqa: E402


def test_append_and_newest_first():
    tl = TurnLog(maxlen=10, jsonl=None)
    tl.append({"text": "one"}, via="stt")
    tl.append({"text": "two"}, via="listen")
    turns = tl.list()
    assert [t["text"] for t in turns] == ["two", "one"]
    assert turns[0]["via"] == "listen"
    assert turns[0]["ts"] >= turns[1]["ts"]


def test_ring_maxlen():
    tl = TurnLog(maxlen=5, jsonl=None)
    for i in range(12):
        tl.append({"text": str(i)})
    turns = tl.list(n=100)
    assert len(turns) == 5
    assert [t["text"] for t in turns] == ["11", "10", "9", "8", "7"]


def test_list_via_allowlist_excludes_argus():
    tl = TurnLog(maxlen=20, jsonl=None)
    tl.append({"text": "hey zoe"}, via="ear")
    tl.append({"text": "person crossed the doorway"}, via="argus")
    tl.append({"text": "ok"}, via="stt")
    ear = tl.list(n=20, via="ear,stt")
    assert [t["via"] for t in ear] == ["stt", "ear"]
    assert all(t["via"] != "argus" for t in ear)
    omni = tl.list(n=20)
    assert {t["via"] for t in omni} == {"ear", "stt", "argus"}


def test_list_n_and_clear():
    tl = TurnLog(maxlen=10, jsonl=None)
    for i in range(6):
        tl.append({"text": str(i)})
    assert len(tl.list(n=3)) == 3
    assert tl.clear() == 6
    assert tl.list() == []


def test_append_does_not_mutate_input():
    tl = TurnLog(maxlen=5, jsonl=None)
    src = {"text": "hi"}
    tl.append(src)
    assert "ts" not in src and "via" not in src


def test_jsonl_append(tmp_path):
    path = tmp_path / "logs" / "turns.jsonl"
    tl = TurnLog(maxlen=5, jsonl=path)
    tl.append({"text": "a"})
    tl.append({"text": "b"})
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2


def test_seed_prefers_speech_over_silence(tmp_path):
    """After restart, seed must prefer rows with text so ear silence cannot empty omni."""
    import json
    import time

    path = tmp_path / "turns.jsonl"
    now = time.time()
    with path.open("w") as f:
        for i in range(20):
            f.write(json.dumps({"text": "", "via": "ear", "ts": now - 80 + i}) + "\n")
        f.write(json.dumps({"text": "hello house", "person_id": "user", "via": "listen", "ts": now - 5}) + "\n")
        f.write(json.dumps({"text": "second speech", "person_id": "second", "via": "ear", "ts": now - 4}) + "\n")
        for i in range(30):
            f.write(json.dumps({"text": "", "via": "ear", "ts": now - 3 + i * 0.01}) + "\n")
    tl = TurnLog(maxlen=50, jsonl=path)
    texts = [t.get("text") for t in tl.list(n=50)]
    assert "hello house" in texts
    assert "second speech" in texts
    # Should not be all silence
    assert any((t or "").strip() for t in texts)


def test_thread_safety_smoke():
    tl = TurnLog(maxlen=200, jsonl=None)

    def worker(tag):
        for i in range(100):
            tl.append({"text": f"{tag}-{i}"})

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(tl) == 200
