"""A dropped "heard" is sent again (the User's walk of cut 13, 2026-10-29: "cant progress from sound ... the bar reacts
to all"): act() posts nothing while another post is in flight, and the page had already marked heard as sent."""
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[1] / "display" / "crissa-init.html").read_text(encoding="utf-8")


def test_heard_is_marked_sent_only_when_the_player_took_it():
    i = PAGE.index('S.op.heardSent = true;')
    tail = PAGE[i:i + 900]
    assert 'Promise.resolve(act("machine", { verb: "heard" })).then((j) => { if (!j || j.ok === false) S.op.heardSent = false; });' in tail
    j = PAGE.index("async function act(action, body)")
    assert "if (S.posting) return null;" in PAGE[j:j + 200]          # the drop it answers
