"""Plain-language labels for the kit's clip keys — clip DATA, not the brain.

Moved out of conductor_brain (2026-09-22, the startup split): the conductor
spreads these into KIT_LABELS at import, so the player needed the brain module
just to name its own loops. The brain's prompt imports them back from here, so
both read one dict under the same names.
"""
from __future__ import annotations

# Plain language for each key, so she is acting rather than picking an
# identifier. Kept here beside the prompt rather than in inventory.json —
# the inventory is the checker's file and carries geometry, not character.
WHAT_THEY_ARE = {
    "work":                   "settled, working, head down",
    "flourish-shift":         "shift in the chair, resettle",
    "flourish-tuck-work":     "tuck a loose strand back, still working",
    "flourish-glance":        "look over at the door, then back",
    "flourish-nod":           "a small nod to yourself",
    "flourish-notes":         "glance down at your notes",
    "flourish-makeup-am":     "check your face for a second",
    "flourish-listen-soften": "soften, listening",
    "flourish-listen-door":   "look up and linger, then settle",
    "flourish-listen-ponder": "look at the door, then back",
    "daydream-down":          "look up from work, turn to the hallway, come home to typing",
    "daydream-back":          "turn to the hallway, then back to you",
    "flourish-back-type":     "glance at the laptop screen, a small reaction, look back up",
    "flourish-back-think":    "look off, consider, come back",
    "flourish-back-settle":   "shift in the chair, resettle",
    "flourish-listen-alert":  "sharpen, listening harder",
    "emote-surprise":         "caught off guard",
    "emote-woe":              "that lands badly",
    "listen-user":            "sat back, attention up",
    "listen-guest":           "sat back, attention up",
    "rest":                   "lean back in to the desk",
    "wake":                   "push back from the desk",
    "loop-talk-work":         "talk while working, head down, a little sway",
    "loop-talk-work-b":       "talk while working, the other take",
    "flourish-type-work":     "eyes to the keys, type, back to the screen",
    "flourish-type-work-b":   "eyes to the keys, type, back to the screen",
    "flourish-bun-wobble-work": "bun lags and wobbles, then settles",
    "flourish-bun-wobble-think-work": "eyes closed, thinking, bun wobbles, back to the screen",
    "flourish-listen-close":  "listen on the close-up, a slow blink",
    "loop-listen-back":       "sat back, listening, a blink and a nod",
    "loop-listen-back-b":     "sat back, listening, the other take",
    # the comms cores (2026-09-10): where she can take a call
    "laptop-closeup-eyes-down": "the close-up, eyes on the screen, listening",
    "talk-close-user-listen": "the close-up, eyes on the User, listening",
    "talk-angled-user-listen": "the angled desk, eyes on the User, listening",
    "listen":                 "sat back from the desk, eyes on the User, listening",
    "talk-angled-work-hd":    "the angled desk, working, eyes on the screen",
    "talking-head":           "up close, face on, talking",
    "talk-back-casual":       "sat right back in the chair, relaxed, talking — off duty",
    "talk-down-work":         "heads down at the desk, working and talking — the hands-busy pose",
}
