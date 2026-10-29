"""A Setup line ends once, whatever ends it (the User's walks of cut 13 and 14, 2026-10-29: "sound stuck as usual"): the
browser paused Lila's welcome film 0.44 s before its end, "ended" never came, and the mic card waited on a line that
was over. The page plays a paused line on, and ends a line 2 s past its length by the clock."""
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "display" / "crissa-init.html"


def test_a_paused_line_plays_on_and_a_late_one_ends_by_the_clock():
    src = PAGE.read_text(encoding="utf-8")
    body = src[src.index("function opPlay("):src.index("function opLoop(")]
    assert "nxt.onended = end;" in body
    assert "nxt.onpause = () => {" in body and "again.catch(end)" in body
    assert "setTimeout(end, 1000 * (Math.max(0, len - nxt.currentTime) + 2))" in body
    assert 'get("filmguard") !== "0"' in body                       # the kill
    assert "if (over || S.op.gen !== gen) return; over = true;" in body  # once, and only for the line still playing
    assert body.index("over = true;\n      nxt.pause();") > 0       # the page's own cut is not taken as a browser pause
