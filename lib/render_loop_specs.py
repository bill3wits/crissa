"""Local hub loops from one anchor still: the written spec and the render command (2026-10-20).

The User: our local renders for "innate loop making on the nano anchors" were "not good" but "may have been
lacking instruction". This module is the instruction: `config/render-loop-specs/<type>.json` holds, in the
imagine-specs form, the exact words a recipe is sent, the sizes per frame (desk 16:9, portrait 9:16), the
recipe knobs, the negative words and the QC bar. `render(...)` fills the slots; `submit_argv(...)` is the
`scripts/render-submit.py` call that makes the take. Nothing here renders: the render window runs the card.

OFF by default: HOUSE_RENDER_LOOP_SPECS=1 turns the loader on; off, `enabled()` is False and the script
that reads this refuses with that reason. Kill: unset it (nothing else reads these files).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = ROOT / "config" / "render-loop-specs"
SCHEMA = "render_loop_spec/v1"
FRAMES = ("desk", "portrait")
RECIPES = ("ltx25-flf2v", "ltx23-flf2v", "wan22-i2v")
PRONOUNS = {"she": ("she", "her", "her"), "he": ("he", "his", "him"), "they": ("they", "their", "them")}
# the same forbidden phrases as the Imagine specs (config/imagine-qc.json words.forbidden): a bare "looks up" sends
# the eyes to the sky
FORBIDDEN = (r"looks up(?! from the screen (at|to) (the camera|the user))", r"look up(?! from the screen (at|to) (the camera|the user))")
# the 2026-10-15 lesson (avatar-films-need-motion): these words froze a set; a spec may not carry them
FREEZE_WORDS = ("head steady", "barely move", "does not move", "stays perfectly still")
# ... and the words must still ASK for life (the User 2026-10-20: the head band goes in the words, "no turn and no tilt
# beyond a few degrees", but breath, blinks and micro-moves stay so the take does not freeze)
MOTION_WORDS = ("breath", "blink", "natural movements")


class SpecError(ValueError):
    pass


def enabled(env: dict | None = None) -> bool:
    return (env if env is not None else os.environ).get("HOUSE_RENDER_LOOP_SPECS", "0") == "1"


def load(type_: str, path: Path | None = None) -> dict:
    p = (path or SPEC_DIR) / f"{type_}.json"
    try:
        spec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SpecError(f"{p.name}: {exc}") from exc
    validate(spec)
    return spec


def load_all(path: Path | None = None) -> dict:
    d = path or SPEC_DIR
    return {p.stem: load(p.stem, d) for p in sorted(d.glob("*.json"))}


def validate(spec: dict) -> None:
    if spec.get("schema") != SCHEMA:
        raise SpecError(f"schema {spec.get('schema')!r} is not {SCHEMA}")
    for k in ("type", "words", "negative", "frames", "recipes", "qc", "anchor_tags", "duration_s", "fps"):
        if k not in spec:
            raise SpecError(f"{spec.get('type')}: missing {k}")
    want_end = "last_anchor" if spec.get("role") == "transition" else "anchor"
    if spec.get("anchors", {}).get("start") != "anchor" or spec["anchors"].get("end") != want_end:
        raise SpecError(f"{spec['type']}: a hub loop starts and ends on the one anchor (a transition ends on last_anchor)")
    text = " ".join(spec["words"]).lower()
    for w in FREEZE_WORDS:
        if w in text:
            raise SpecError(f"{spec['type']}: the words say {w!r}: that froze the 2026-10-15 set; ask for motion, hold only the last second")
    for pat in FORBIDDEN:
        if re.search(pat, text):
            raise SpecError(f"{spec['type']}: a bare 'looks up' (say where the eyes go)")
    for w in MOTION_WORDS:
        if w not in text:
            raise SpecError(f"{spec['type']}: the words do not ask for {w!r}: a loop with no life reads robotic (2026-10-15)")
    if "{ends_note}" not in " ".join(spec["words"]):
        raise SpecError(f"{spec['type']}: the words must carry {{ends_note}} (the only rule a loop needs is its ends)")
    for fr in FRAMES:
        if fr not in spec["frames"]:
            raise SpecError(f"{spec['type']}: frames.{fr} missing")
        for r, size in spec["frames"][fr]["size_by_recipe"].items():
            w, h = (int(v) for v in size.lower().split("x"))
            if r.startswith("ltx") and (w % 32 or h % 32):
                raise SpecError(f"{spec['type']}: {r} {size} is not a multiple of 32")
            if r.startswith("wan") and (w % 16 or h % 16):
                raise SpecError(f"{spec['type']}: {r} {size} is not a multiple of 16")
    tags = spec["anchor_tags"]
    if int(tags.get("front", 0)) < 1 or int(tags.get("end", 0)) < 1:
        raise SpecError(f"{spec['type']}: anchor tags front/end must be held frames")


def ends_note(spec: dict, pronoun: str) -> str:
    p, _, _ = PRONOUNS[pronoun]
    if spec.get("role") == "transition":
        return ("For the last second {p} is completely still, exactly in the pose of the last photo. The first frame is exactly "
                "the first photo and the last frame is exactly the last photo.").format(p=p)
    return ("For the last second {p} is completely still, back in exactly the pose of this photo: same head, same eyes, "
            "same shoulders. The first frame and the last frame are exactly this photo.").format(p=p)


def check_stance(stance: str | None) -> str:
    """One sentence of theme (the demo's 'she listens like a patient tutor ...'), held to the same word rules as a spec."""
    st = (stance or "").strip()
    if not st:
        return ""
    if len(st) > 300:
        raise SpecError("a stance is one sentence (300 characters at most)")
    low = st.lower()
    for w in FREEZE_WORDS:
        if w in low:
            raise SpecError(f"the stance says {w!r}: that froze the 2026-10-15 set")
    for pat in FORBIDDEN:
        if re.search(pat, low):
            raise SpecError("the stance has a bare 'looks up' (say where the eyes go)")
    for w in ("speak", "talk", "says", "open mouth", "laugh"):
        if re.search(r"\b" + w, low):
            raise SpecError(f"the stance asks for {w!r}: the loop's mouth is painted over, it stays closed")
    return st if st.endswith((".", "!", "?")) else st + "."


def render(spec: dict, *, frame: str, subject: str = "person", look: str | None = None, pronoun: str = "she",
           stance: str | None = None) -> str:
    """The words a recipe is sent, every slot filled; a stance sentence (optional) follows the first line."""
    if frame not in FRAMES:
        raise SpecError(f"frame {frame!r} is not one of {FRAMES}")
    if pronoun not in PRONOUNS:
        raise SpecError(f"pronoun {pronoun!r}")
    p, pos, obj = PRONOUNS[pronoun]
    slots = {"subject": subject, "look": look or spec["frames"][frame].get("look_default", "the same hair and clothes"),
             "pronoun": p, "pronoun_pos": pos, "pronoun_obj": obj, "pronoun_cap": p.capitalize(), "pronoun_pos_cap": pos.capitalize(),
             "ends_note": ends_note(spec, pronoun)}
    out = []
    for line in spec["words"]:
        try:
            out.append(line.format(**slots))
        except KeyError as exc:
            raise SpecError(f"{spec['type']}: unknown slot {exc}") from exc
    st = check_stance(stance)
    if st:
        out.insert(1, st)
    return " ".join(out)


def negative(spec: dict) -> str:
    return ", ".join(spec["negative"])


def length_frames(seconds: float, fps: int, recipe: str) -> int:
    mod = 4 if recipe.startswith("wan") else 8          # Wan 4k+1, LTX 8k+1 (render-submit.wan_length)
    n = int(round(seconds * fps))
    return n + ((1 - n) % mod)


def submit_argv(spec: dict, *, frame: str, recipe: str, anchor: str, card: str, name: str, out_dir: str,
                subject: str = "person", look: str | None = None, pronoun: str = "she", seed_index: int = 0,
                seconds: float | None = None, python: str = "python3", stance: str | None = None,
                last_anchor: str | None = None) -> list[str]:
    """The scripts/render-submit.py call for one take. `anchor` must already be cut to the frame's aspect."""
    if recipe not in spec["recipes"]:
        raise SpecError(f"{spec['type']}: no recipe {recipe}")
    r = spec["recipes"][recipe]
    size = spec["frames"][frame]["size_by_recipe"][recipe]
    secs = float(seconds if seconds is not None else spec["duration_s"])
    if recipe.startswith("wan") and secs > float(r.get("seconds_max", secs)):
        secs = float(r["seconds_max"])
    seeds = r.get("seeds_to_try") or [r.get("noise_seed", 42)]
    noise = int(seeds[seed_index % len(seeds)])
    argv = [python, str(ROOT / "scripts" / "render-submit.py"), "--card", card, "--name", name, "--recipe", recipe,
            "--seed", anchor, "--size", size, "--seconds", f"{secs:g}", "--fps", str(int(spec["fps"])),
            "--steps", str(int(r["steps"])), "--noise-seed", str(noise), "--out-dir", out_dir,
            "--prompt", render(spec, frame=frame, subject=subject, look=look, pronoun=pronoun, stance=stance)]
    if recipe.endswith("flf2v") and r.get("guide") is not None:
        argv += ["--guide", f"{float(r['guide']):g}"]
    if spec.get("role") == "transition":
        if not last_anchor or not recipe.endswith("flf2v"):
            raise SpecError(f"{spec['type']}: a transition needs --last-anchor and a first/last-frame recipe")
        argv += ["--last", last_anchor]
    if recipe.startswith("wan") and r.get("cfg") is not None:
        argv += ["--cfg", f"{float(r['cfg']):g}"]
    return argv


def qc_bar(spec: dict) -> dict:
    return {k: spec["qc"][k] for k in ("motion_min", "seam_min", "ends_vs_anchor_min")}


def order_overrides(spec: dict, *, frame: str, recipe: str, anchor_basename: str, subject: str = "person",
                    look: str | None = None, pronoun: str = "she", seed_index: int = 0, seconds: float | None = None,
                    stance: str | None = None) -> dict:
    """The same take as `submit_argv`, as `NODE.INPUT=VALUE` overrides for a render ORDER (lib/render_orders.add,
    the render module's `orders add`): the pack's way, with no board card and no house mode switch. `anchor_basename`
    is the anchor as already staged in Comfy's input folder, cut to the frame's aspect."""
    if recipe not in spec["recipes"]:
        raise SpecError(f"{spec['type']}: no recipe {recipe}")
    r = spec["recipes"][recipe]
    n = r.get("graph_nodes") or {}
    if not n:
        raise SpecError(f"{spec['type']}: recipe {recipe} names no graph_nodes")
    w, h = (int(v) for v in spec["frames"][frame]["size_by_recipe"][recipe].lower().split("x"))
    secs = float(seconds if seconds is not None else spec["duration_s"])
    if recipe.startswith("wan") and secs > float(r.get("seconds_max", secs)):
        secs = float(r["seconds_max"])
    seeds = r.get("seeds_to_try") or [r.get("noise_seed", 42)]
    noise = int(seeds[seed_index % len(seeds)])
    ov = {f"{n['prompt']}.text": render(spec, frame=frame, subject=subject, look=look, pronoun=pronoun, stance=stance),
          f"{n['latent']}.width": w, f"{n['latent']}.height": h,
          f"{n['latent']}.length": length_frames(secs, int(spec["fps"]), recipe),
          f"{n['first_image']}.image": anchor_basename, f"{n['fps']}.fps": int(spec["fps"])}
    if n.get("negative"):
        ov[f"{n['negative']}.text"] = negative(spec) if recipe.startswith("ltx") else None
        if ov[f"{n['negative']}.text"] is None:
            del ov[f"{n['negative']}.text"]            # Wan keeps its stock negative in the graph
    if n.get("last_image"):
        ov[f"{n['last_image']}.image"] = anchor_basename
    for g in ("guide_first", "guide_last"):
        if n.get(g) and r.get("guide") is not None:
            ov[f"{n[g]}.strength"] = float(r["guide"])
    if n.get("sampler"):
        ov[f"{n['sampler']}.seed"] = noise
        ov[f"{n['sampler']}.steps"] = int(r["steps"])
    for s in n.get("samplers") or []:
        ov[f"{s}.noise_seed"] = noise
        ov[f"{s}.steps"] = int(r["steps"])
        if r.get("cfg") is not None:
            ov[f"{s}.cfg"] = float(r["cfg"])
    return ov
