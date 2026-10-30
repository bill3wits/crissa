"""Every way IN fades on the toolbar's idle, and they fade together.

User 2026-08-31: "fade with mouse and top menu buttons all together one
piece". The wall's affordances used to keep their own counsel — the vitals
caret and the two button rows sat on the glass permanently, the corner arrows
answered only to hover — so the wall never fully became the drawing.

The invariant worth pinning is not the opacity, it is the SET: four groups,
one selector, no member drifting out of it. A control still lit after the
others have gone reads as a fault rather than as a control.
"""
import re
from pathlib import Path

WALL = Path(__file__).resolve().parent.parent / "display" / "voiceonly-home.html"

# the four groups: the caret, the oracle dot, and the two corners WITH the
# button rows underneath them
IDLE_SET = ("vitalsTog", "oracleDot",
            "pipwrap", "pipfold", "arToggle", "arZoom",
            "ftPop", "ftFold", "ftCtl")

# the corners join the idle only while CONTRACTED — expanded they hold the
# camera and the room, which is information and must not fade
CONTRACTED_ONLY = ("pipwrap", "ftPop")

# every group must hold itself up while the pointer is on it, or a control
# fades out from under the hand aiming at it while staying clickable
HOLDERS = ("vitalsTog", "oracleDot", "pipwrap", "ftPop", "ftCtl",
           "arToggle", "arZoom")


def rule():
    """The selector list, comments stripped — it carries a note explaining why
    the two corners are listed as .bar, and that note sits between selectors."""
    html = re.sub(r"/\*.*?\*/", "", WALL.read_text(encoding="utf-8"), flags=re.S)
    m = re.search(r"((?:body:not\(\.chrome\) [#.\w]+,?\s*)+)\{([^}]*)\}", html)
    assert m, "no body:not(.chrome) idle rule on the wall"
    return m.group(1), m.group(2)


def test_all_four_groups_ride_the_same_idle():
    sel, _ = rule()
    got = set(re.findall(r"#(\w+)", sel))
    assert got == set(IDLE_SET), f"idle set drifted: {got ^ set(IDLE_SET)}"


def test_the_corners_fade_only_when_contracted():
    """Contracted, a corner carries no picture — the bar IS the way back, so it
    belongs in the idle. Expanded it holds the camera or the room, and fading
    that would take away information rather than an affordance."""
    sel, _ = rule()
    for cid in CONTRACTED_ONLY:
        assert f"#{cid}.bar" in sel, f"{cid} must fade only in .bar"
        # and never listed bare, which would fade the picture too
        assert f"#{cid}," not in sel and f"#{cid}{{" not in sel, cid


def test_every_group_holds_while_the_pointer_is_on_it():
    """The rail has always done this. The groups that joined its idle did not,
    so a control faded out from under the hand — and stayed clickable while
    invisible, which is worse than either alone."""
    html = WALL.read_text(encoding="utf-8")
    block = html.split("HOLD WHAT THE HAND IS ON")[1][:1400]
    for hid in HOLDERS:
        assert f'"{hid}"' in block, hid
    # #ftCtl is itself pointer-events:none, so only the bubbling pair reaches
    # it from its buttons — enter/leave would never fire
    assert '"mouseover"' in block and '"mouseout"' in block
    assert "mouseenter" not in block


def test_they_are_one_selector_not_several():
    """Split across rules they can be changed apart, which is the whole bug."""
    html = WALL.read_text(encoding="utf-8")
    # Count SELECTORS, not prose. A comment that names the selector — and the
    # #ftCtl block carries one — is matched by this regex all the way to the
    # next `{`, which read as a second block. Strip comments first: the
    # property is "one selector", and a comment mentioning it is not one.
    css_only = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    assert len(re.findall(r"body:not\(\.chrome\)[^{]*\{", css_only)) == 1


def test_the_idle_actually_wins():
    """#pipwrap.bar #pipfold and .clockwrap.dot #oracleDot both outrank a body
    class, so without !important the arrows stay lit through the idle."""
    _, body = rule()
    assert "opacity:0 !important" in body.replace(" !", " !")
    assert "pointer-events:none" in body


def test_it_moves_on_the_toolbar_curve():
    """One piece means one timing — the rail's own transition."""
    _, body = rule()
    assert "cubic-bezier(.2,.8,.2,1)" in body
    html = WALL.read_text(encoding="utf-8")
    rail = re.search(r"#rail\{[^}]*\}", html).group(0)
    assert "cubic-bezier(.2,.8,.2,1)" in rail


def test_nothing_that_carries_information_fades():
    """Only affordances go. The pictures, the roll and her stay."""
    sel, _ = rule()
    for keep in ("ascii", "roll", "pipframe", "ftCan", "dial"):
        assert f"#{keep}" not in sel, keep
    # the two corners appear ONLY as .bar — see
    # test_the_corners_fade_only_when_contracted for why that is the line
