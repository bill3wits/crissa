"""Round 43 (.101 round 42 note): Voice ID waited ~1 min on This computer's background check with nothing on screen.
While the walk is still on This computer with You done and This computer answered, Voice ID's section says
"Waiting for this computer's setup to finish". Text only: the order is unchanged. Kill CRISSA_VID_WAIT_SAYS=0."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_init as zi  # noqa: E402


def _st(**machine):
    m = {"name_taken": {"display": "Sam"}, "mic_heard": True}
    m.update(machine)
    return {"status": "running", "step_id": "machine", "steps_done": ["connect"], "machine": m}


def test_voice_id_says_it_waits_for_this_computer(monkeypatch):
    monkeypatch.delenv("CRISSA_VID_WAIT_SAYS", raising=False)
    monkeypatch.setenv("CRISSA_VID_WAIT_BG", "0")             # round 43's step rule (round 45 keys the background)
    vid = zi.one_page_sections(_st(permission="allow"))["vid"]
    assert vid["done"] is False and vid["wait"] == "Waiting for this computer's setup to finish"


def test_not_before_this_computer_is_answered_nor_after_it(monkeypatch):
    monkeypatch.delenv("CRISSA_VID_WAIT_SAYS", raising=False)
    monkeypatch.setenv("CRISSA_VID_WAIT_BG", "0")
    assert "wait" not in zi.one_page_sections(_st())["vid"]                       # This computer still asks
    st = dict(_st(permission="allow"), step_id="voice_id", steps_done=["connect", "machine"])
    assert "wait" not in zi.one_page_sections(st)["vid"]                          # the walk moved on


def test_the_kill(monkeypatch):
    monkeypatch.setenv("CRISSA_VID_WAIT_SAYS", "0")
    assert "wait" not in zi.one_page_sections(_st(permission="allow"))["vid"]


def test_the_page_shows_it_under_the_section():
    page = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")
    assert '$("op-sum-" + sec.id).textContent = isDone ? (st.summary || "Done") : (st.wait || "");' in page
