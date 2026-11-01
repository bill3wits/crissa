"""A pack's demo asks her own agent, and never speaks an error (2026-10-31, the overnight demo on crissatest 1.0.4: a
pack ships no scripts/crissa-hands.py, every broadcast question came back as python's "can't open file ...crissa-hands.py"
on stderr, and she read that aloud three times)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _script():
    spec = importlib.util.spec_from_file_location("xspaces_live_script_pack_ask", ROOT / "scripts" / "xspaces-live.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(tmp_path, hands_body: str | None) -> Path:
    (tmp_path / "scripts").mkdir()
    if hands_body is not None:
        (tmp_path / "scripts" / "crissa-hands.py").write_text(hands_body)
    return tmp_path


def test_a_pack_with_no_hands_asks_her_agent_through_the_player(monkeypatch, tmp_path):
    S = _script()
    monkeypatch.setattr(S, "ROOT", _tree(tmp_path, None))
    monkeypatch.setattr(S.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.delenv("CRISSA_DEMO_PACK_ASK", raising=False)
    sent = []

    def post(url, body, timeout=10):
        sent.append((url, body))
        return {"ok": True, "reply": "I'm Crissa. I live on your desktop and talk with you.", "error": None}

    monkeypatch.setattr(S, "_json_post", post)
    got = S.ask_hands("Crissa, say what this is.", pause=False)
    assert got["ok"] and got["via"] == "agent" and got["text"].startswith("I'm Crissa.")
    assert sent[0][0].endswith("/v1/ask") and sent[0][1] == {"text": "Crissa, say what this is.", "speak": False,
                                                             "by": "demo"}


def test_her_agent_failing_is_silence_with_a_reason(monkeypatch, tmp_path):
    S = _script()
    monkeypatch.setattr(S, "ROOT", _tree(tmp_path, None))
    monkeypatch.setattr(S.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(S, "_json_post", lambda url, body, timeout=10: {"ok": False, "error": "the agent is not reachable"})
    got = S.ask_hands("Crissa, two people?", pause=False)
    assert got["ok"] is False and got["text"] == "" and "not reachable" in got["why"]

    def boom(url, body, timeout=10):
        raise OSError("connection refused")

    monkeypatch.setattr(S, "_json_post", boom)
    got = S.ask_hands("Crissa, two people?", pause=False)
    assert got["text"] == "" and "connection refused" in got["why"]


def test_a_failed_hands_process_is_never_her_words_in_a_pack(monkeypatch, tmp_path):
    S = _script()
    monkeypatch.setattr(S, "ROOT", _tree(tmp_path, "import sys; sys.stderr.write('Traceback: boom'); sys.exit(2)\n"))
    monkeypatch.setattr(S.crissa_home, "is_house", lambda *a, **k: False)
    got = S.ask_hands("Crissa, anything?", pause=False)
    assert got["text"] == "" and "boom" in got["why"] and got["ok"] is False


def test_the_house_is_unchanged(monkeypatch, tmp_path):
    S = _script()
    monkeypatch.setattr(S, "ROOT", _tree(tmp_path, "import sys; sys.stderr.write('hands are already busy'); sys.exit(1)\n"))
    monkeypatch.setattr(S.crissa_home, "is_house", lambda *a, **k: True)
    got = S.ask_hands("Crissa, anything?", pause=False)
    assert got["text"] == "hands are already busy" and got["ok"] is False


def test_the_kill_switch_restores_the_old_path(monkeypatch, tmp_path):
    S = _script()
    monkeypatch.setattr(S, "ROOT", _tree(tmp_path, "import sys; sys.stderr.write('old path'); sys.exit(1)\n"))
    monkeypatch.setattr(S.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setenv("CRISSA_DEMO_PACK_ASK", "0")
    got = S.ask_hands("Crissa?", pause=False)
    assert got["text"] == "old path"
