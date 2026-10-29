"""The letter reads markdown as text, and her echo is dropped when the switch says so (the User 2026-10-21, the
teaching take: "##" and "**" on her lesson; her own answer on the roll as a STRANGER row and a "?" row)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

LESSON = """# Lesson: Solving Linear Equations

## Solve 3x + 7 = 22

**Goal:** Isolate x by undoing operations in reverse order.

### Steps

**1. Subtract 7 from both sides**
Why: The +7 is blocking x.
3x + 7 - 7 = 22 - 7

### Watch Out
- Whatever you do to one side, do to `BOTH` sides.
---
### Try This
Solve 5x - 4 = 11 on your own."""

HER = ("Let us find the value of x in the equation three x plus seven equals twenty two. First, we subtract seven "
       "from both sides to move the constant term away from the variable term. This works because doing the same "
       "operation on both sides keeps the equation balanced while isolating the part with x. Next, we divide both "
       "sides by three to solve for x alone. This is necessary because x is currently multiplied by three, and "
       "division is the inverse operation that undoes multiplication. The answer is x equals five. Check this by "
       "plugging five back into the original equation to see if it holds true.")
# the two rows the ear put on the roll at 09:18:47 (read off the take's frame)
ECHO_1 = ("Sides by three to solve for x. The is necessary because x is currently multiplied by three, and division "
          "is the inverse operation that does multiplication. The answer is x equals five. Check this by putting five "
          "back into the original equat")
ECHO_2 = ECHO_1[:-5] + "equation to see if it hol"


def test_the_letter_shows_her_lesson_without_markdown_marks():
    import house_wall_ascii_render as R
    title, body = R.letter_body({"notes": LESSON, "notes_title": "# Lesson: Solving Linear Equations"})
    assert title == "Lesson: Solving Linear Equations"
    assert "#" not in body and "**" not in body and "`" not in body and "---" not in body
    assert "Solve 3x + 7 = 22" in body                 # math keeps its case
    assert "STEPS" in body and "WATCH OUT" in body and "TRY THIS" in body
    assert "Goal: Isolate x" in body and "• Whatever you do" in body and "do to BOTH sides" in body


def test_text_that_is_not_markdown_is_unchanged_and_the_kill_switch_works(monkeypatch):
    import house_wall_ascii_render as R
    assert R.letter_plain("3*4*5 = 60, a * b") == "3*4*5 = 60, a * b"
    monkeypatch.setenv("HOUSE_WALL_LETTER_MD", "0")
    assert R.letter_plain("## Steps") == "## Steps"


def test_the_echo_switch_means_what_it_says(monkeypatch):
    import house_wall_ascii_render as R
    monkeypatch.setenv("HOUSE_WALL_ROLL_ECHO", "1")
    assert R._roll_drop_echo() is True
    monkeypatch.setenv("HOUSE_WALL_ROLL_ECHO", "0")
    assert R._roll_drop_echo() is False


def _feed(R, chat, now=10.0):
    roll = R.Roll(since=0.0)
    roll.feed(chat, now=now)
    return roll


def test_her_0918_line_heard_back_with_slips_is_not_a_line(monkeypatch):
    import house_wall_ascii_render as R
    monkeypatch.setattr(R, "ROLL_DROP_ECHO", True)
    roll = _feed(R, [{"who": "crissa", "text": HER, "ts": 1.0},
                     {"who": "stranger", "text": ECHO_1, "ts": 2.0},
                     {"who": "?", "text": ECHO_2, "ts": 3.0},
                     {"who": "stranger", "text": "Wait, what does the inverse operation mean exactly?", "ts": 4.0}])
    texts = [ln["text"] for ln in roll.lines]
    assert texts == [HER, "Wait, what does the inverse operation mean exactly?"]


def test_the_share_dial_at_zero_is_the_exact_match_only(monkeypatch):
    import house_wall_ascii_render as R
    monkeypatch.setattr(R, "ROLL_DROP_ECHO", True)
    monkeypatch.setattr(R, "ROLL_ECHO_SHARE", 0.0)
    roll = _feed(R, [{"who": "crissa", "text": HER, "ts": 1.0}, {"who": "stranger", "text": ECHO_1, "ts": 2.0}])
    assert len(roll.lines) == 2


def test_the_house_still_keeps_every_line_unless_switched(monkeypatch):
    import house_wall_ascii_render as R
    import crissa_home
    monkeypatch.delenv("HOUSE_WALL_ROLL_ECHO", raising=False)
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert R._roll_drop_echo() is False


def _paint(R, body):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (2560, 1440), (20, 16, 12, 255))
    R._paint_notes_sheet(img, ImageDraw.Draw(img), R.Geom(2560, 1440), {"notes": body, "notes_title": "Lesson"})
    return img


def test_the_marked_step_gets_the_band_and_an_unmarked_letter_is_unchanged(monkeypatch):
    import house_wall_ascii_render as R
    monkeypatch.delenv("HOUSE_WALL_LETTER_POINTER", raising=False)
    marked = "Goal: Find x.\n▶ 1. Subtract 7 from both sides.\n┃ 3x = 15\n2. Divide both sides by 3."
    plain = marked.replace("▶ ", "").replace("┃ ", "")
    band = R.LETTER_BAND[:3]
    assert R._letter_marks(R.letter_lines(__import__("PIL.ImageDraw").ImageDraw.Draw(
        __import__("PIL.Image").Image.new("RGB", (10, 10))), marked, R.font("mono", 20), 10000)) == \
        [False, True, True, False]
    on = _paint(R, marked).convert("RGB")
    assert any(px == band for px in on.getdata())
    off = _paint(R, plain).convert("RGB")
    assert not any(px == band for px in off.getdata())
    monkeypatch.setenv("HOUSE_WALL_LETTER_POINTER", "0")
    assert not any(px == band for px in _paint(R, marked).convert("RGB").getdata())
