"""Enrollment take plans — desk-first User init vs later room angles.

The User 2026-07-31 hypothesis (test with a regular's desk bank):
  - Initial User enroll always at the **desk** (camera aimed desk/factory pan).
  - Front + profile + front while seated is enough to start.
  - Off-desk / door-angle extras only for *further* enrolls if desk bank fails
    match in the person's usual place.

The User 2026-08-06: record windows lengthened (was 4.2s) — last desk cue
"One two three four five…" was getting cut before the done-chirp.
"""

from __future__ import annotations

# 2026-09-11 (the User: "a 15 sec thing"): the front window fits the shorter
# DESK_LINE_FRONT (~5 s voiced), the turn is a 1–6 count. Two constants —
# the routine's schedule is the host clips plus these.
VOICE_FOX_SECONDS = "5.5"
VOICE_DESK_SECONDS = "6.0"
VOICE_TURN_SECONDS = "4.0"
VOICE_DESK_COUNT_SECONDS = VOICE_TURN_SECONDS  # alias — count-while-turned

# THE READ WINDOW FITS THE SENTENCE READ CALMLY (the User 2026-10-10, .101 round 4 AB). The house's 6.0 s window fits its
# own User, who reads the sentence briskly; the sentence is about 20 words with the name, which a person reading calmly
# takes 7 to 8 s over (.101's stand-in voice read it in 7.8 s and had to be played at 1.3x to fit). The window is sized
# from the sentence itself: its words at a calm reading pace (CALM_WORDS_PER_S, 150 a minute) plus READ_MARGIN_S for
# the reaction to the beep and a last word, never under the house's 6.0 s. The sentence stays the house's (same vowels,
# same "my name is"). The voiced-share bar a take must clear scales with it (voiced_bar), so the VOICED SECONDS it
# asks for stay the house's own (45 % of 6.0 s = 2.7 s): a brisk reader who leaves the end of a longer window silent
# still makes a print, and a silent take still makes none. The house's numbers above are unchanged.
CALM_WORDS_PER_S = 2.5
READ_MARGIN_S = 1.5


def read_window_s(line: str) -> float:
    """Seconds the read take records for this sentence (the name already in it)."""
    words = max(1, len(str(line or "").split()))
    return round(max(float(VOICE_DESK_SECONDS), words / CALM_WORDS_PER_S + READ_MARGIN_S), 1)


def voiced_bar(min_active: float, window_s: float, ref_s: float | None = None) -> float:
    """The voiced share a window_s take must clear so its voiced SECONDS equal min_active of the reference window."""
    ref = float(VOICE_DESK_SECONDS if ref_s is None else ref_s)
    return round(float(min_active) * ref / max(ref, float(window_s or ref)), 3)

from .cues import BLIND_VOICE_LINES, DESK_LINE_FRONT, DESK_LINE_TURN, BLIND_VOICE_LINE

# Initial exposure — desk, two takes, full vowel coverage each.
DESK_INIT_TAKES: list[dict[str, str]] = [
    {
        "id": "desk_front_1",
        "station": "desk",
        "pose": "front",
        "angle": "DESK front (face the camera)",
        "speak_angle": "Stay at the desk. Look straight at the camera.",
        "cue": DESK_LINE_FRONT,
        "snap_face": "1",
        "seconds": VOICE_DESK_SECONDS,
    },
    {
        "id": "desk_turn",
        "station": "desk",
        "pose": "sweep",
        "angle": "DESK slow head turn",
        "speak_angle": "Stay at the desk. Turn a quarter toward the side. Count.",
        "cue": DESK_LINE_TURN,
        "snap_face": "1",
        "sweep": "1",
        "seconds": VOICE_TURN_SECONDS,
    },
]

# Optional later pass — three blind lines (union 15/15 vowels).
ROOM_EXTRA_TAKES: list[dict[str, str]] = [
    {
        "id": "door_1",
        "station": "door",
        "pose": "front",
        "angle": "DOOR / usual place 1",
        "speak_angle": "Stand where you usually are. Face the door camera.",
        "cue": BLIND_VOICE_LINES[0],
        "snap_face": "1",
        "seconds": VOICE_FOX_SECONDS,
    },
    {
        "id": "door_2",
        "station": "door",
        "pose": "profile",
        "angle": "DOOR / usual place 2",
        "speak_angle": "Still in your usual place. Turn a little.",
        "cue": BLIND_VOICE_LINES[1],
        "snap_face": "1",
        "seconds": VOICE_FOX_SECONDS,
    },
    {
        "id": "door_3",
        "station": "door",
        "pose": "front",
        "angle": "DOOR / usual place 3",
        "speak_angle": "Face the door camera again.",
        "cue": BLIND_VOICE_LINES[2],
        "snap_face": "1",
        "seconds": VOICE_FOX_SECONDS,
    },
]

# Long text for offline import / lab only (not spoken as "read the screen")
REENROLL_PARAGRAPH_LONG = (
    "When the sunlight strikes raindrops in the air, they act as a prism and "
    "form a rainbow. The rainbow is a division of white light into many "
    "beautiful colors. These take the shape of a long round arch, with its "
    "path high above, and its two ends apparently beyond the horizon. "
    "This is my everyday voice, on the room microphone, so the house can "
    "recognize me whenever I am here."
)

# Default live cue — short, memorable, one hear → one repeat
REENROLL_PARAGRAPH = BLIND_VOICE_LINE

PARAGRAPH_REENROLL_TAKES: list[dict[str, str]] = [
    {
        "id": "para_on",
        "station": "desk",
        "pose": "front",
        "angle": "ON · look at camera, say fox",
        "speak_angle": "Look at the camera and say:",
        "cue": REENROLL_PARAGRAPH,
        "snap_face": "0",
        "seconds": VOICE_FOX_SECONDS,
        "kind": "paragraph",
        "blind": "1",
    },
    {
        "id": "para_off",
        "station": "desk",
        "pose": "profile",
        "angle": "OFF · look left, say fox",
        "speak_angle": "Look to the left and say:",
        "cue": REENROLL_PARAGRAPH,
        "snap_face": "0",
        "seconds": VOICE_FOX_SECONDS,
        "kind": "paragraph",
        "blind": "1",
    },
]

# Legacy labels kept for --plan legacy
LEGACY_TAKES: list[dict[str, str]] = [
    {
        "id": "on1",
        "station": "door",
        "pose": "front",
        "angle": "ON-camera (face the door cam)",
        "speak_angle": "Face the door camera.",
        "cue": "My name is {name}, and this is my voice for the house.",
        "snap_face": "1",
        "seconds": VOICE_DESK_SECONDS,
    },
    {
        "id": "off1",
        "station": "door",
        "pose": "profile",
        "angle": "OFF-angle (turn 45–90°, keep speaking)",
        "speak_angle": "Turn away from the camera about forty five to ninety degrees. Keep speaking clearly.",
        "cue": "The camera can see me from this angle while I speak.",
        "snap_face": "1",
        "seconds": VOICE_DESK_SECONDS,
    },
    {
        "id": "on2",
        "station": "door",
        "pose": "front",
        "angle": "ON-camera (face the door cam)",
        "speak_angle": "Face the door camera again.",
        "cue": "One two three four five — room presence voice enroll.",
        "snap_face": "1",
        "seconds": VOICE_DESK_COUNT_SECONDS,
    },
]


def resolve_plan(name: str = "desk") -> list[dict[str, str]]:
    n = (name or "desk").strip().lower()
    if n in ("desk", "init", "user", "default"):
        return DESK_INIT_TAKES
    if n in ("room", "extra", "door"):
        return ROOM_EXTRA_TAKES
    if n in ("paragraph", "para", "reenroll", "p0", "live-mic"):
        return PARAGRAPH_REENROLL_TAKES
    if n in ("legacy", "old"):
        return LEGACY_TAKES
    raise ValueError(
        f"unknown plan {name!r}; use desk|room|paragraph|legacy"
    )
