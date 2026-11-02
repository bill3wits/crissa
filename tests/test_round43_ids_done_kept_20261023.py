"""Round 43 (.101 round 42, R42-3): the IDs done plate counts what was KEPT. ted's plate said "4 face photos" with every
still dropped as another face (no face folder); now its face count is the stills in the person's own folder. And a section
the run did not walk keeps "Not in this run" when the run ends (it read "Your hours: Done"). Kill CRISSA_IDS_DONE_KEPT=0."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_init as zi  # noqa: E402

PAGE = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")


def _st():
    return {"ids": True, "status": "done",
            "register": {"state": "done", "display": "ted", "person_id": "ted", "voice": 4, "face": 4}}


def _line(monkeypatch, faces_on_disk):
    monkeypatch.setattr(zi, "face_prints", lambda pid: faces_on_disk if pid == "ted" else 99)
    return next(x for x in zi.done_view(_st())["lines"] if x.startswith("Voice and Face ID"))


def test_dropped_stills_count_zero(monkeypatch):
    monkeypatch.delenv("CRISSA_IDS_DONE_KEPT", raising=False)
    assert _line(monkeypatch, 0) == "Voice and Face ID: ted, 4 voice prints, 0 face photos."
    assert _line(monkeypatch, 2) == "Voice and Face ID: ted, 4 voice prints, 2 face photos."
    assert _line(monkeypatch, 9) == "Voice and Face ID: ted, 4 voice prints, 4 face photos."   # never more than the run's


def test_the_kill_is_round_42(monkeypatch):
    monkeypatch.setenv("CRISSA_IDS_DONE_KEPT", "0")
    assert _line(monkeypatch, 0) == "Voice and Face ID: ted, 4 voice prints, 4 face photos."
    assert zi.ids_done_kept_on() is False


def test_the_snapshot_tells_the_page():
    src = (ROOT / "lib" / "crissa_init.py").read_text(encoding="utf-8")
    assert src.count('"done_keeps_fold": ids_done_kept_on(),') == 1


def test_the_page_keeps_not_in_this_run_at_the_end():
    assert "const notRun = !!snap.done_keeps_fold && !!(S.op.folded && S.op.folded.has(sec.id)) && !st.done;" in PAGE
    assert 'const isDone = !!st.done || (snap.status === "done" && !notRun);' in PAGE
    assert 'fold ? "Not in this run"' in PAGE
