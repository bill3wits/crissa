"""The machine check: can THIS computer run the Wall and her voice, and which way should it be set up (2026-10-05).

It measures what is here, writes one answer, and changes nothing:

    render  decode the face's listen loop at the Wall's size, paint a patch, encode the frame: times real time
    tts     one probe line through the speech engine this install would use: first-audio seconds and rtf
    asr     that line back through the speech-to-text service, when one answers: rtf
    align   that line through the aligner, when it runs: seconds per line

and recommends a tier from the bars in config/crissa-machine-tiers.json (a copy in the state home wins):

    local   every stage measured clears its bar, both voice stages measured: everything on this machine
    split   the Wall here, the voice on another machine on the LAN (a voice stage missed its bar or is absent)
    remote  never recommended; reported only when this install already sends its voice off the LAN

The answer is `<state home>/machine.json` (schema crissa-machine/1), written atomically; a RECOMMENDATION.
Applying it is the user's confirm in Init (answer()). `read()` never measures.

ON BY DEFAULT IN A PACK, BEHIND A PERMISSION CARD (the User 2026-10-10: "the only hardware checkpoint on a new install
is this one, and it decides the sound stack"). CRISSA_MACHINE_CHECK unset = on in a pack; =0 is the kill switch (off,
run_check() writes nothing and answers {"tier": None, "skipped": "off"}). Never on the house: there the default stays
off, and =1 answers {"skipped": "house"} (its default ports are its live ear) unless CRISSA_MACHINE_CHECK_HOUSE=1.
A stage that cannot run says why in `reasons`; the check never raises.

THE CARD (card(), both Init walks, after Connect and before the voice is picked or installed): what the check will do
on this computer now (plan(): only the stages that can run here; a voice not installed yet is not timed), how long
that takes (estimate_s(): the bars and two reference measurements, config `card`), that no sound plays (SOUND_PLAYS:
the probe line is made in memory and only sent to this computer's own hearing and aligner), and that nothing leaves
the computer (LOCAL_ONLY: a stage whose service is on another computer is not timed). Allow or Skip:
    allow   record_allow() then run_check(permission="allow"); the recommendation is shown, the person's answer() kept
    skip    record_skip(): machine.json {"skipped": "user"}, nothing measured; the voice is picked from the hardware
            facts the voice tier table reads (lib/crissa_voice_tiers: GPU, RAM, cores, OS)
The choice is kept in machine.json (`permission`), so a restart of Init does not ask again (answered()).
Better mouths follows the check (align_default(), lib/crissa_aligner): the measured align stage against its bar.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crissa_egress  # noqa: E402
import crissa_home  # noqa: E402

SCHEMA = "crissa-machine/1"
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "crissa-machine-tiers.json"
DEFAULTS = {"render_x_realtime_min": 3.0, "render_fps": 24, "asr_rtf_max": 1.0, "tts_rtf_max": 0.5,
            "tts_stream_rtf_max": 1.0,
            "tts_first_audio_s_max": 1.0, "align_s_per_line_max": 0.5, "frames_quick": 48, "frames_full": 240,
            "repeats_quick": 1, "repeats_full": 3,
            "density_candidates": [5.0, 4.0, 3.0, 2.0, 1.5, 1.0], "density_frames": 6, "density_timeout_s": 120,
            "density_margin": 1.1, "density_paint_ms": 5.0, "density_child_start_s": 1.5,
            "density_floor_by_width": [[3000, 2.0], [2000, 1.5]],
            "probe_text": "Good evening. I'm here whenever you need me, just say my name."}


SWITCH = "CRISSA_MACHINE_CHECK"
SCHEMA_KEYS = ("permission", "skipped", "tier", "answer")
SOUND_PLAYS = False      # stage_tts makes the probe line in memory; stage_asr/stage_align send it to this computer only
LOCAL_ONLY = True        # a stage whose service is on another computer is not timed (the card: nothing leaves it)
CARD_DEFAULTS = {"words_per_s": 2.4, "asr_cold_rtf": 4.1, "align_warm_s": 2.4}


def on(env: dict | None = None) -> bool:
    """unset = on in a pack, off on the house; 0/off = off everywhere (the kill switch); 1/on = on (the house still
    answers "house" in run_check unless CRISSA_MACHINE_CHECK_HOUSE=1)."""
    e = os.environ if env is None else env
    v = str(e.get(SWITCH) or "").strip().lower()
    if v in ("0", "off", "false", "no"):
        return False
    if v in ("1", "on", "true", "yes"):
        return True
    return not crissa_home.is_house()


def out_path(home: Path | None = None) -> Path:
    return (home or crissa_home.HOME) / "machine.json"


def thresholds(home: Path | None = None) -> dict:
    out = dict(DEFAULTS)
    for p in (CONFIG, (home or crissa_home.HOME) / "crissa-machine-tiers.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.update({k: v for k, v in d.items() if not k.startswith("_") and k in DEFAULTS})
    return out


def read(home: Path | None = None) -> dict | None:
    """The last answer, or None. Never measures."""
    try:
        d = json.loads(out_path(home).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("schema") == SCHEMA else None
    except (OSError, ValueError):
        return None


def _write(rec: dict, home: Path | None = None) -> dict:
    p = out_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return rec


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ---- the permission card's answers (the User 2026-10-10) ---------------------------------------------------------

def answered(home: Path | None = None) -> dict:
    """What the person already chose, so Init never asks twice: {"permission": "allow"|"skip"|None, "measured": bool,
    "tier", "reasons", "elapsed_s", "answer": "use"|"decline"|None}. A measured record with no permission key (the
    command line before 2026-10-10) counts as allowed: the person ran it."""
    rec = read(home) or {}
    perm = rec.get("permission")
    if perm not in ("allow", "skip"):
        perm = "skip" if rec.get("skipped") == "user" else ("allow" if rec.get("tier") else None)
    return {"permission": perm, "measured": bool(rec.get("tier")), "tier": rec.get("tier"),
            "reasons": list(rec.get("reasons") or []), "elapsed_s": rec.get("elapsed_s"),
            "answer": rec.get("answer") if rec.get("answer") in ("use", "decline") else None}


def record_skip(home: Path | None = None, by: str = "user") -> dict:
    """Skip on the card: nothing is measured; the voice is picked from the hardware facts the voice tier table reads."""
    return _write({"schema": SCHEMA, "at": _now(), "permission": "skip", "skipped": by, "tier": None, "stages": {},
                   "voice_from": "hardware", "reasons": [], "notes": []}, home)


def record_allow(home: Path | None = None, by: str = "user") -> dict:
    """Allow on the card, kept before the check runs: a restart in the middle measures again without asking."""
    rec = read(home)
    if rec and rec.get("tier") and rec.get("permission") == "allow":
        return rec
    return _write({"schema": SCHEMA, "at": _now(), "permission": "allow", "allowed_by": by, "tier": None,
                   "stages": {}, "reasons": [], "notes": [], "measuring": True}, home)


def answer(accept: bool, home: Path | None = None) -> dict:
    """The person's answer to the recommendation: "use" (confirmed: `confirmed` = the tier) or "decline"."""
    rec = read(home) or {}
    if not rec.get("tier"):
        return rec
    rec.update(answer="use" if accept else "decline", confirmed=rec["tier"] if accept else None, answered_at=_now())
    return _write(rec, home)


def apply_tier(tier: str, home: Path | None = None) -> dict:
    """Init's confirm (lib/crissa_init calls it on Use this): the recommendation is kept as confirmed."""
    rec = read(home) or {}
    return answer(True, home) if rec.get("tier") == tier else rec


def align_default(home: Path | None = None) -> bool | None:
    """Better mouths' default from the check (lib/crissa_aligner): True / False when the person allowed the check
    and it timed the aligner against its bar; None otherwise (skipped, not run, or the aligner was not running)."""
    rec = read(home) or {}
    if answered(home)["permission"] != "allow":
        return None
    al = (rec.get("stages") or {}).get("align")
    if not isinstance(al, dict) or al.get("s_per_line") is None:
        return None
    bar = (rec.get("thresholds") or {}).get("align_s_per_line_max") or thresholds(home)["align_s_per_line_max"]
    return float(al["s_per_line"]) <= float(bar)


def card_cfg(home: Path | None = None) -> dict:
    """The card's estimate inputs (config `card`): speaking pace and two reference measurements, never a bar."""
    out = dict(CARD_DEFAULTS)
    for p in (CONFIG, (home or crissa_home.HOME) / "crissa-machine-tiers.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8")).get("card") or {}
        except (OSError, ValueError, AttributeError):
            continue
        out.update({k: float(v) for k, v in d.items() if k in CARD_DEFAULTS and isinstance(v, (int, float))})
    return out


# ---- stages: each returns (result dict | None, reason str) -------------------------------------------

def _listen_film() -> Path | None:
    """The current face's listen loop film, from its manifest and its library's inventory."""
    try:
        import avatar
        man = avatar.load()
        poses = ROOT / str(man.get("poses_dir") or "")
        inv = json.loads((poses / "inventory.json").read_text(encoding="utf-8"))
        clips = inv.get("clips") or {}
        key = avatar.listen_loop(name=man.get("_name")) or next(
            (k for k, c in clips.items() if c.get("role") == "loop" and "listen" in k), None)
        f = poses / str((clips.get(key) or {}).get("file") or "")
        return f if key and f.is_file() else None
    except Exception:  # noqa: BLE001
        return None


def stage_render(frames: int, fps: float, film: Path | None = None) -> tuple[dict | None, str]:
    """Decode, resize to the Wall frame, blend a mouth-sized patch, JPEG-encode: the player's per-frame work."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None, "render: the player's cv2/numpy are not installed"
    film = film or _listen_film()
    if film is None:
        return None, "render: no listen loop film found for this face"
    cap = cv2.VideoCapture(str(film))
    patch = np.full((60, 100, 3), 128, np.uint8)
    n, t0 = 0, time.perf_counter()
    try:
        while n < frames:
            ok, img = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, img = cap.read()
                if not ok:
                    break
            img = cv2.resize(img, (736, 400), interpolation=cv2.INTER_AREA)
            roi = img[230:290, 320:420]
            img[230:290, 320:420] = cv2.addWeighted(roi, 0.5, patch, 0.5, 0)
            cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 86])
            n += 1
    finally:
        cap.release()
    dt = time.perf_counter() - t0
    if n == 0 or dt <= 0:
        return None, "render: the film did not decode"
    got = n / dt
    return {"frames": n, "fps": round(got, 1), "x_realtime": round(got / float(fps), 2), "film": film.name}, ""


# THE DENSITY HER FIRST WORDS ARE DRAWN AT (round 15, 2026-10-12, .101's fresh install: the Wall started at 1.0 where
# the User's own 5.0 "looks far better"; "Looking for first live words to be a good impression"). With the desk Wall
# installed, the card times the Wall's own whole render at each candidate density on the Wall's glass size (the Wall
# notes it at glass up) and picks the highest that holds one picture a film frame; apply_density() makes it the Wall's
# density unless the person has set one, so the Wall and its build (lib/wall_ascii_disk) start there. The timing runs
# in the Wall's own code, in a process of its own (`wall_ascii_disk.py --density-probe`). Kill CRISSA_MACHINE_DENSITY=0.
DENSITY_PICK = "density-pick.json"


def density_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_MACHINE_DENSITY", "1")).strip().lower() not in ("0", "off", "false", "no")


# THE STRICT PICK (round 21): the probe times the film child's round trip, as the Wall draws, against the Wall's own
# stride margin and the rest of its pass (lib/wall_ascii_disk.density_probe; density_margin, density_paint_ms).
# CRISSA_DENSITY_PICK_STRICT=0 is round 20's pick (the render alone, its median against one film frame).
def density_strict_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_DENSITY_PICK_STRICT", "1")).strip().lower() not in ("0", "off", "false", "no")


def _glass_size(home: Path | None = None) -> tuple[int, int] | None:
    try:
        d = json.loads(((home or crissa_home.HOME) / "run" / "wall-glass.json").read_text(encoding="utf-8"))
        return int(d["W"]), int(d["H"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def stage_density(th: dict, home: Path | None = None, env: dict | None = None, run=None) -> tuple[dict | None, str]:
    """The Wall's own render timed at each candidate density (highest first) on its glass size: the probe's answer
    {W, H, bar_ms, candidates: [{density, p50_ms, p90_ms}], pick}, or None and why."""
    e = os.environ if env is None else env
    if not density_on(e):
        return None, ""
    probe = ROOT / "lib" / "wall_ascii_disk.py"
    if not probe.is_file():
        return None, "density: no desk Wall in this install"
    size = _glass_size(home)
    if size is None:
        return None, "density: the desk Wall has not said its size yet"
    film = _listen_film()
    if film is None:
        return None, "density: no listen loop film found for this face"
    arg = {"W": size[0], "H": size[1], "film": str(film), "candidates": list(th["density_candidates"]),
           "frames": int(th["density_frames"]), "fps": float(th["render_fps"])}
    if density_strict_on(e):
        arg.update(via_child=True, margin=float(th["density_margin"]), paint_ms=float(th["density_paint_ms"]))
    import subprocess
    try:
        r = (run or subprocess.run)([sys.executable, str(probe), "--density-probe", json.dumps(arg)],
                                    capture_output=True, text=True, timeout=float(th["density_timeout_s"]),
                                    cwd=str(probe.parent))
        out = json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001 - the card's other answers stand
        return None, "density: the probe did not answer (%s)" % type(exc).__name__
    return (out, "") if isinstance(out, dict) else (None, "density: the probe's answer was not read")


def _glass_url() -> str:
    """The shared glass record's server, the one the desk Wall reads its density from (its HOUSE): HOUSE_WALL_HOUSE,
    else this machine's panel_url (the house), else a pack's feed (lib/crissa_ports). Round 16, 2026-10-13: .101's
    card posted to panel_url, which a pack does not have, so the record kept 1.0 and the Wall never moved."""
    url = os.environ.get("HOUSE_WALL_HOUSE") or crissa_home.house_value("panel_url")
    if not url:
        try:
            import crissa_ports
            url = crissa_ports.url("feed")
        except Exception:  # noqa: BLE001
            url = ""
    return url.rstrip("/")


# ONE STEP FINER FOR THE FIRST LOOK (round 27, the User 2026-10-16 after .101's round 26: "let's load on higher res ascii
# (just one)", "better first impression on higher resolution"; .101's card picked 2.044 on 3440x1440). The card's pick
# goes on the glass one RESOLUTION click finer (house_wall_prefs.nudge_step: 2.044 -> 2.146 there), and the build is
# made at that density. Kill: CRISSA_DENSITY_ONE_FINER=0 (the card's own pick).
def finer(pick: float, wall_prefs=None, env: dict | None = None) -> float:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_DENSITY_ONE_FINER", "1")).strip().lower() in ("0", "off", "false", "no"):
        return float(pick)
    if wall_prefs is None:
        import house_wall_prefs as wall_prefs
    top = float(getattr(wall_prefs, "DENSITY_MAX", 5.0))
    return min(top, round(float(pick) + float(wall_prefs.nudge_step(float(pick))), 3))


# NEVER THE COARSEST ON A BIG SCREEN (round 28, .101 2026-10-17: the probe ran while Setup's installs had the CPU,
# nothing fit 48 fps - even 1.0 took 46 ms - the pick was null and the Wall stayed at 1.0, 334x86 on 3440x1440, where
# round 26 on the same box picked 2.044). A null pick on a wide glass takes the floor for its width
# (density_floor_by_width, [[min W, density], ...]), and then the one step finer as any pick. Kill:
# CRISSA_DENSITY_FLOOR=0 (a null pick leaves the Wall's default).
def density_floor(stage: dict | None, th: dict | None = None, env: dict | None = None) -> float | None:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_DENSITY_FLOOR", "1")).strip().lower() in ("0", "off", "false", "no"):
        return None
    try:
        w = int((stage or {}).get("W") or 0)
    except (TypeError, ValueError):
        return None
    rows = (th or DEFAULTS).get("density_floor_by_width") or []
    for min_w, dens in sorted(([int(a), float(b)] for a, b in rows), reverse=True):
        if w >= min_w:
            return dens
    return None


def apply_density(stage: dict | None, home: Path | None = None, post=None, get=None, by: str | None = None) -> dict:
    """Make the card's pick the Wall's density, unless the person has set one: the density is applied when it is still
    the Wall's default or the card's own last pick. The density on the glass is the shared record's (the feed's
    /v1/glass-prefs; the Wall's own file only when no record answers), so that is what is read and what is posted;
    the Wall's file follows. <state home>/density-pick.json keeps the pick and when (`ts`), which the desk Wall also
    reads to take it live (lib/house_wall_ascii). {"applied": bool, "density", "why"}."""
    pick = (stage or {}).get("pick")
    floored = False
    if not pick:
        pick = density_floor(stage, thresholds(home))
        if not pick:
            return {"applied": False, "why": "no pick"}
        floored = True
    home = Path(home or crissa_home.HOME)
    import house_wall_prefs as wall_prefs
    pick = finer(float(pick), wall_prefs)
    url = "" if crissa_home.is_house() else _glass_url()
    cur = None
    if url:
        try:
            rec = json.loads((get or (lambda u: crissa_egress.urlopen(u, 3.0, "glass prefs").read()))(
                url + "/v1/glass-prefs"))
            cur = float(rec["density"])
        except Exception:  # noqa: BLE001 - no record answering: the Wall's own file says
            cur = None
    if cur is None:
        cur = float(wall_prefs.load().get("density", wall_prefs.DEFAULTS["density"]))
    try:
        last = float(json.loads((home / DENSITY_PICK).read_text(encoding="utf-8"))["density"])
    except (OSError, ValueError, KeyError, TypeError):
        last = None
    if abs(cur - float(wall_prefs.DEFAULTS["density"])) > 1e-6 and (last is None or abs(cur - last) > 1e-6):
        return {"applied": False, "density": cur, "why": "the person set %g" % cur}
    p = wall_prefs.load()
    p["density"] = float(pick)
    saved = wall_prefs.save(p)
    posted = False
    try:
        if url:
            req = urllib.request.Request(url + "/v1/glass-prefs", data=json.dumps({"density": float(pick)}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            (post or (lambda q: crissa_egress.urlopen(q, 3.0, "glass prefs").read()))(req)
            posted = True
    except Exception:  # noqa: BLE001 - the Wall takes the pick from density-pick.json itself
        posted = False
    try:
        tmp = home / (DENSITY_PICK + ".tmp")
        rec_ = {"density": float(pick), "at": _now(), "ts": time.time()}
        if by:
            rec_["by"] = by
        tmp.write_text(json.dumps(rec_), encoding="utf-8")
        os.replace(tmp, home / DENSITY_PICK)
    except OSError:
        pass
    out = {"applied": True, "density": saved.get("density", pick), "was": cur, "posted": posted}
    if floored:
        out["why"] = ("the first look: the floor for a %s px glass" if by == "wall" else
                      "nothing fit while the machine was busy: the floor for a %s px glass") % (stage or {}).get("W")
    return out


# THE FIRST LOOK AT THE MACHINE'S DENSITY (round 29, .101 2026-10-18: the Wall drew 334x86 at 1.0 from 12:05:45 until
# the card's pick at 12:07:45; the User: "ASCII on Init (very first impression) not hi res", a new user "may not make it
# through"). The card's answer on a busy first boot is the floor for the glass's width (density_floor) one step finer,
# and the Wall knows its width at glass up: so the desk Wall's first glass takes that, through apply_density (the person's
# own density still wins), before the card has measured. Its density-pick.json says `by: "wall"`, which the build does
# not take as the card's answer (crissa_build.density_wait) and apply_density counts as the last pick, so the card's
# own pick still replaces it. Only while no pick is on file. Kill: CRISSA_DENSITY_FIRST_LOOK=0.
def first_look(W: int, H: int, home: Path | None = None, env: dict | None = None, post=None, get=None) -> dict:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_DENSITY_FIRST_LOOK", "1")).strip().lower() in ("0", "off", "false", "no"):
        return {"applied": False, "why": "off"}
    if not density_on(e):
        return {"applied": False, "why": "the machine density is off"}
    h = Path(home or crissa_home.HOME)
    if (h / DENSITY_PICK).exists():
        return {"applied": False, "why": "a pick is on file"}
    return apply_density({"W": int(W), "H": int(H), "pick": None}, h, post=post, get=get, by="wall")


def stage_tts(text: str, repeats: int, cfg: dict | None = None) -> tuple[dict | None, str, bytes]:
    """One probe line through the engine this install would use. Non-streaming engines: first audio = the line."""
    try:
        import crissa_tts
    except ImportError:
        return None, "tts: the voice seam is not in this install", b""
    cfg = crissa_tts.config() if cfg is None else cfg
    if not cfg:
        return None, "tts: no speech engine is set up on this install", b""
    times, pcm = [], b""
    try:
        for _ in range(max(1, repeats)):
            t0 = time.perf_counter()
            pcm, sr = crissa_tts.synth(text, _face(), cfg)
            times.append(time.perf_counter() - t0)
    except crissa_tts.TTSError as e:
        return None, "tts (%s): %s" % (cfg.get("engine"), e), b""
    audio_s = len(pcm) / 2 / float(crissa_tts.HOUSE_SR)
    warm = sorted(times)[len(times) // 2] if len(times) > 1 else times[0]
    return ({"engine": cfg.get("engine"), "first_audio_s": round(warm, 3), "audio_s": round(audio_s, 2),
             "rtf": round(warm / audio_s, 3) if audio_s else None, "cold_s": round(times[0], 3),
             "url_lan": _is_lan_url(str(cfg.get("url") or ""))}, "", pcm)


# ---- THE VOICE LADDER (the User 2026-10-10, the .101 test) ---------------------------------------------------------------
# On .101 (16 threads, 90 GB, no NVIDIA GPU) Allow timed one engine, Audio8, with ONE line - its first after the server
# started, so the model load was billed as her speed (first audio 13.67 s = its cold start, rtf 3.46) - and recommended
# "the voice on another computer". The voice is CPU-dependent: from Pocket (the smallest) up to the largest that runs
# well here, and Turbo only with an NVIDIA GPU. So Allow times the voices INSTALLED here from the largest down - Turbo
# (only with an NVIDIA GPU), Audio8, Pocket, Kokoro - each with one untimed line first (its warm-up), then a timed one:
# first audio is the first sound (a streamed voice's first chunk, Kokoro's whole line), rtf the whole line over its
# audio. The first that clears both bars is recommended (the largest; the rest are not timed); "split" only when none
# does. A voice whose lane is not running is started through the launcher's request queue and waited for. Use this
# keeps it as her voice (crissa_voice_tiers.override, Init's card) and the launcher starts its lane: no restart.
# Kill CRISSA_MACHINE_LADDER=0: the one engine the voice seam would use, timed as before.
LADDER = ("turbo", "audio8", "pocket", "kokoro")
LANE = {"kokoro": "tts"}                    # the launcher's lane for an engine (lib/crissa_procs VOICE_PROC)
LABELS = {"turbo": "Chatterbox Turbo", "audio8": "Audio8", "pocket": "Pocket", "kokoro": "Kokoro"}
WARM_TIMEOUT_S = 120.0                      # the untimed first line: a cold model load on a CPU
LINE_TIMEOUT_S = 90.0
LANE_WAIT_S = 90.0                          # a lane asked to start: its model loads before its port answers


def ladder_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_MACHINE_LADDER", "1")).strip().lower() not in ("0", "off", "false", "no")


def _vt():
    try:
        import crissa_voice_tiers
        return crissa_voice_tiers
    except ImportError:
        return None


def ladder(installed=None, facts: dict | None = None) -> list:
    """The voices to time here, largest first: the ladder's engines installed on this machine; Turbo only with an
    NVIDIA GPU (a CUDA GPU in the probe's facts)."""
    vt = _vt()
    if installed is None:
        installed = (lambda e: bool(vt.installed(e))) if vt is not None else (lambda e: False)
    if facts is None:
        try:
            facts = vt.probe() if vt is not None else {}
        except Exception:  # noqa: BLE001
            facts = {}
    cuda = str(((facts or {}).get("gpu") or {}).get("kind") or "") == "cuda"
    out = []
    for eng in LADDER:
        if eng == "turbo" and not cuda:
            continue
        if vt is not None and vt.not_a_voice(eng):     # Audio8 is her ear, never timed or offered as a voice (10-17)
            continue
        try:
            if installed(eng):
                out.append(eng)
        except Exception:  # noqa: BLE001
            continue
    return out


def engine_cfg(eng: str) -> dict:
    """The voice seam's config for one engine on this machine (its loopback server)."""
    try:
        import crissa_tts
    except ImportError:
        return {"engine": eng}
    if eng == "kokoro":
        return {"engine": eng, "url": crissa_tts.kokoro_url()}
    if eng == "audio8":
        return {"engine": eng, "url": crissa_tts.audio8_url()}
    if eng in crissa_tts.STREAMED:
        return {"engine": eng, "url": crissa_tts.stream_url(eng)}
    return {"engine": eng}


def _lane_up(eng: str, home: Path | None = None, wait_s: float = LANE_WAIT_S, port_open=None, start=None,
             sleep=time.sleep) -> str:
    """'' when the engine's server answers (asking the launcher to start its lane when it does not); else why not."""
    port_open = port_open or _port_open
    url = str(engine_cfg(eng).get("url") or "")
    if not url:
        return "no server address"
    if port_open(url):
        return ""
    if start is None:
        try:
            import crissa_procs
            h = home or crissa_home.HOME
            start = lambda lane: crissa_procs.request(h, lane, "start")   # noqa: E731
        except ImportError:
            start = None
    if start is None:
        return "its server is not running"
    try:
        got = start(LANE.get(eng, eng)) or {}
    except Exception as e:  # noqa: BLE001
        got = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    if got.get("ok") is False:
        return "its server is not running and could not be started (%s)" % str(got.get("error") or "?")[:120]
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        if port_open(url):
            return ""
        sleep(1.0)
    return "its server did not answer within %d s of starting" % int(wait_s)


def _stream_timed(url: str, body: dict, timeout: float) -> "tuple[float, float, bytes, int]":
    """(first sound s, whole line s, pcm, sample rate) from a streamed voice's /stream, read as it arrives."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
                                 method="POST")
    t0 = time.perf_counter()
    first = None
    out = []
    with crissa_egress.urlopen(req, timeout, "the machine check") as r:
        sr = int(r.headers.get("X-Sample-Rate") or 24000)
        while True:
            chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
            if not chunk:
                break
            if first is None:
                first = time.perf_counter() - t0
            out.append(chunk)
    total = time.perf_counter() - t0
    return (first if first is not None else total), total, b"".join(out), sr


# A BUSY VOICE IS NOT A SLOW ONE (round 29; .101 2026-10-17: Setup's check timed Pocket while Lila's Voice ID lines
# spoke on it, read 2.33 s first audio against 107-161 ms on every real line, and put her on Kokoro). Pocket makes one
# line at a time and says so (`busy` on /health); a timed line waits until it is free, up to the line's own timeout,
# and the wait is reported, never counted. A server that does not say (older Pocket, the others) is timed as before.
# Kill CRISSA_MACHINE_WAIT_IDLE=0.
def wait_idle_on() -> bool:
    return os.environ.get("CRISSA_MACHINE_WAIT_IDLE", "1").strip().lower() not in ("0", "false", "off", "no")


def _health_url(url: str) -> str:
    from urllib.parse import urlsplit
    u = urlsplit(str(url))
    return "%s://%s/health" % (u.scheme or "http", u.netloc)


def wait_idle(url: str, limit_s: float, poll_s: float = 0.1, now=time.monotonic, sleep=time.sleep, get=None) -> float:
    """Seconds waited for the voice at `url` to stop saying `busy` (0.0 at once, or when it does not say)."""
    def _get(h):
        with crissa_egress.urlopen(h, 2.0, "the machine check") as r:
            return json.loads(r.read() or b"{}")
    get = get or _get
    t0, h = now(), _health_url(url)
    while True:
        try:
            busy = bool((get(h) or {}).get("busy"))
        except Exception:  # noqa: BLE001  no answer: time it as before
            return round(now() - t0, 3)
        if not busy or now() - t0 >= limit_s:
            return round(now() - t0, 3)
        sleep(poll_s)


def time_line(eng: str, text: str, cfg: dict | None = None, timeout: float = LINE_TIMEOUT_S) -> dict:
    """One line through one engine: {"first_audio_s", "total_s", "audio_s", "streamed", "pcm"} (pcm 24 kHz s16le)."""
    import crissa_tts
    cfg = dict(cfg or engine_cfg(eng))
    face = _face()
    if eng == "audio8":
        t0 = time.perf_counter()
        first, parts, sr = None, [], 24000
        try:
            for pcm, sr in crissa_tts.audio8_stream(text, cfg, face, timeout=timeout):
                if first is None:
                    first = time.perf_counter() - t0
                parts.append(pcm)
            total = time.perf_counter() - t0
            pcm = crissa_tts.to_house_rate(b"".join(parts), sr)
            streamed = True
        except crissa_tts.TTSError:              # no stream on this server: the buffered line, Audio8 alone
            t0 = time.perf_counter()
            raw, sr = crissa_tts._audio8(text, cfg, face, timeout)
            total = time.perf_counter() - t0
            pcm = crissa_tts.to_house_rate(raw, sr)
            first, streamed = total, False
    elif eng in crissa_tts.STREAMED:
        voice = crissa_tts.voice_for(cfg, face) or face
        waited = wait_idle(str(cfg["url"]), timeout) if wait_idle_on() else 0.0
        first, total, raw, sr = _stream_timed(str(cfg["url"]), {"text": text, "voice": voice}, timeout)
        pcm = crissa_tts.to_house_rate(raw, sr)
        streamed = True
    elif eng == "kokoro":                         # Kokoro alone: never the seam's fallbacks
        t0 = time.perf_counter()
        raw, sr = crissa_tts._kokoro(text, cfg, face, timeout)
        total = time.perf_counter() - t0
        pcm = crissa_tts.to_house_rate(raw, sr)
        first, streamed = total, False
    else:
        raise ValueError("%s is not on the voice ladder" % eng)
    audio_s = len(pcm) / 2 / float(crissa_tts.HOUSE_SR)
    out = {"first_audio_s": round(float(first if first is not None else total), 3), "total_s": round(total, 3),
           "audio_s": round(audio_s, 2), "streamed": streamed, "pcm": pcm}
    if eng in crissa_tts.STREAMED and waited:
        out["waited_idle_s"] = waited
    return out


def stage_voice(eng: str, text: str, repeats: int = 1, home: Path | None = None, timer=None) -> "tuple[dict | None, str, bytes]":
    """One engine of the ladder: brought up, one untimed line (its warm-up, recorded as cold_s), then `repeats` timed
    lines (the median). (stage, why not, pcm)."""
    timer = timer or time_line
    why = _lane_up(eng, home)
    if why:
        return None, "%s: %s" % (LABELS.get(eng, eng), why), b""
    try:
        cold = timer(eng, text, timeout=WARM_TIMEOUT_S)
        runs = [timer(eng, text) for _ in range(max(1, int(repeats)))]
    except Exception as e:  # noqa: BLE001  one engine that fails is a reason, never the check's end
        return None, "%s: %s" % (LABELS.get(eng, eng), str(e)[:160] or type(e).__name__), b""
    runs.sort(key=lambda r: r["total_s"])
    mid = runs[len(runs) // 2]
    audio_s = float(mid["audio_s"] or 0)
    return ({"engine": eng, "first_audio_s": mid["first_audio_s"], "audio_s": mid["audio_s"],
             "rtf": round(mid["total_s"] / audio_s, 3) if audio_s else None, "cold_s": cold["total_s"],
             "streamed": bool(mid["streamed"]), "url_lan": True}, "", mid["pcm"])     # loopback: on this computer


def rtf_bar(stage: dict | None, th: dict) -> tuple[float, bool]:
    """(the rtf bar, strict) for a timed voice: a streaming voice stays ahead of playback below tts_stream_rtf_max
    (strictly, 1.0 by default); a voice that makes the whole line first is held to tts_rtf_max (the User 2026-10-10)."""
    if (stage or {}).get("streamed"):
        return float(th.get("tts_stream_rtf_max", 1.0)), True
    return float(th["tts_rtf_max"]), False


def voice_clears(stage: dict | None, th: dict) -> list:
    """[] when the timed voice clears both bars; else what it missed, in words."""
    if not stage:
        return ["not timed"]
    out = []
    bar, strict = rtf_bar(stage, th)
    rtf = stage.get("rtf")
    if rtf is None or (float(rtf) >= bar if strict else float(rtf) > bar):
        out.append("rtf %s is %s %.2f%s" % (("%.2f" % rtf) if rtf is not None else "?", "not under" if strict else "over",
                                            bar, " (streamed)" if strict else ""))
    if float(stage.get("first_audio_s") or 0) > float(th["tts_first_audio_s_max"]):
        out.append("first audio %.2f s is over %.2f s" % (float(stage["first_audio_s"]), float(th["tts_first_audio_s_max"])))
    return out


def run_ladder(text: str, th: dict, repeats: int = 1, engines: list | None = None, home: Path | None = None,
               stage=None) -> "tuple[dict, bytes]":
    """Down the ladder: {"ladder", "measured": {eng: stage}, "missed": {eng: [why]}, "recommended": eng or None,
    "label"}, and the recommended voice's probe line (for the speech-to-text stage)."""
    stage = stage or (lambda eng: stage_voice(eng, text, repeats, home))
    engines = ladder() if engines is None else list(engines)
    out: dict = {"ladder": list(engines), "measured": {}, "missed": {}, "recommended": None, "label": ""}
    pcm_any = b""
    for eng in engines:
        got, why, pcm = stage(eng)
        if got is None:
            out["missed"][eng] = [why or "not timed"]
            continue
        out["measured"][eng] = got
        pcm_any = pcm_any or pcm
        miss = voice_clears(got, th)
        if miss:
            out["missed"][eng] = miss
            continue
        out["recommended"], out["label"] = eng, LABELS.get(eng, eng)
        return out, pcm
    return out, pcm_any


def _face() -> str:
    try:
        import avatar
        return avatar.current_name()
    except ImportError:
        return "crissa"


def _post(url: str, data: bytes, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with crissa_egress.urlopen(req, timeout, "machine check") as r:
        return json.loads(r.read() or b"{}")


def _to_16k(pcm24: bytes) -> bytes:
    import array
    a = array.array("h")
    a.frombytes(pcm24)
    return array.array("h", (a[int(i * 1.5)] for i in range(int(len(a) / 1.5)))).tobytes()


def stage_asr(pcm24: bytes, url: str, repeats: int = 1) -> tuple[dict | None, str]:
    """One untimed decode first (a service's first decode pays its warm-up: 4.1 rtf cold vs ~0.4 warm on the
    reference box), then `repeats` timed ones; the median is the answer."""
    if not pcm24:
        return None, "asr: no probe line to transcribe (no speech engine)"
    pcm = _to_16k(pcm24)
    audio_s = len(pcm) / 2 / 16000.0
    hdr = {"Content-Type": "application/octet-stream", "X-Sample-Rate": "16000"}
    times, got = [], {}
    try:
        cold0 = time.perf_counter()
        _post(url.rstrip("/") + "/stt", pcm, hdr, timeout=120)
        cold = time.perf_counter() - cold0
        for _ in range(max(1, repeats)):
            t0 = time.perf_counter()
            got = _post(url.rstrip("/") + "/stt", pcm, hdr, timeout=60)
            times.append(time.perf_counter() - t0)
    except (OSError, ValueError) as e:
        return None, "asr: no speech-to-text service answers at %s (%s)" % (url, type(e).__name__)
    dt = sorted(times)[len(times) // 2]
    return {"rtf": round(dt / audio_s, 3), "decode_s": round(dt, 3), "cold_s": round(cold, 3),
            "text": str(got.get("text") or "")[:120], "url_lan": _is_lan_url(url)}, ""


def stage_align(pcm24: bytes, text: str, url: str) -> tuple[dict | None, str]:
    if not pcm24:
        return None, "align: no probe line to align"
    import base64
    body = json.dumps({"text": text, "sr": 24000, "pcm16_b64": base64.b64encode(pcm24).decode()}).encode()
    try:
        t0 = time.perf_counter()
        _post(url, body, {"Content-Type": "application/json"}, timeout=60)
        return {"s_per_line": round(time.perf_counter() - t0, 3)}, ""
    except (OSError, ValueError) as e:
        return None, "align: the aligner does not answer at %s (%s)" % (url, type(e).__name__)


def _is_lan_url(url: str) -> bool | None:
    if not url:
        return None
    try:
        import crissa_net
        return bool(crissa_net.is_lan(urlparse(url).hostname or ""))
    except Exception:  # noqa: BLE001
        host = urlparse(url).hostname or ""
        return host in ("127.0.0.1", "localhost", "::1")


def _asr_url(env: dict) -> str:
    v = str(env.get("CRISSA_ASR_URL") or "").strip()
    if v:
        return v
    try:
        import crissa_ports
        return crissa_ports.url("asr", env)
    except (ImportError, KeyError, AttributeError):
        return "http://127.0.0.1:%d" % (8096 + int(str(env.get("CRISSA_PORT_OFFSET") or "0") or 0))


def _align_url(env: dict) -> str:
    try:
        import crissa_aligner
        e = crissa_aligner.line_env()
        return e.get("HOUSE_ALIGN_URL", "")
    except Exception:  # noqa: BLE001
        return ""


# ---- what the check would do here, before it is allowed to (the card) ----------------------------------------------

_HERE = ("127.0.0.1", "localhost", "::1", "")


def _here(url: str) -> bool:
    """A service on THIS computer (loopback), or no url at all (an engine run in this process: say, piper)."""
    return (urlparse(url).hostname or "") in _HERE if url else True


def _tts_url(cfg: dict) -> str:
    """Where a line for this engine goes; '' for an engine that runs here with no service (say, piper)."""
    eng = str(cfg.get("engine") or "")
    if eng in ("say", "piper", ""):
        return ""
    if cfg.get("url"):
        return str(cfg["url"])
    try:
        import crissa_tts
        if eng == "kokoro":
            return crissa_tts.kokoro_url()
        if eng == "audio8":
            return crissa_tts.audio8_url()
        if eng in crissa_tts.STREAMED:
            return crissa_tts.stream_url(eng)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _port_open(url: str, timeout: float = 0.3) -> bool:
    import socket
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def _tts_ready(cfg: dict | None) -> dict:
    """The engine a probe line would use, read from the voice seam (it asks which voice answers on loopback); {}
    when no voice is set up here yet. Never makes a line."""
    if cfg is not None:
        return cfg
    try:
        import crissa_tts
        return crissa_tts.config() or {}
    except Exception:  # noqa: BLE001
        return {}


def plan(env: dict | None = None, home: Path | None = None, tts_cfg: dict | None = None) -> dict:
    """Which stages the check would time on this computer now, without timing anything:
    {"render", "tts", "asr", "align": bool, "pending": [stages not installed or running here yet], "off": {stage:
    "lan"|"internet"} for a service on another computer (never timed: nothing leaves this computer), "tts_cfg"}."""
    e = dict(os.environ if env is None else env)
    out: dict = {"render": True, "tts": False, "asr": False, "align": False, "pending": [], "off": {}, "tts_cfg": {},
                 "ladder": []}
    if tts_cfg is None and ladder_on(e):
        out["ladder"] = ladder()
    cfg = _tts_ready(tts_cfg)
    if out["ladder"] and not cfg.get("engine"):
        cfg = engine_cfg(out["ladder"][0])        # a voice is installed: the ear is timed with the ladder's line
    url = _tts_url(cfg) if cfg.get("engine") else ""
    if not cfg.get("engine"):
        out["pending"] += ["tts", "asr"]
    elif LOCAL_ONLY and not _here(url):
        out["off"]["tts"] = "lan" if _is_lan_url(url) else "internet"
        out["pending"].append("asr")
    else:
        out["tts"], out["tts_cfg"] = True, cfg
        au = _asr_url(e)
        if LOCAL_ONLY and not _here(au):
            out["off"]["asr"] = "lan" if _is_lan_url(au) else "internet"
        elif str(e.get("CRISSA_ASR_URL") or "").strip() or _port_open(au):
            out["asr"] = True                      # named, or answering here: a miss now is a miss
        else:
            out["pending"].append("asr")           # nothing listens on its port: not installed or not started yet
    if _align_url(e):
        out["align"] = True
    else:
        out["pending"].append("align")
    return out


def estimate_s(pl: dict, quick: bool = True, th: dict | None = None, cc: dict | None = None) -> float:
    """How long the check takes here, at most, on a computer that just clears every bar: render at its bar, one
    probe line per repeat at the first-audio bar, the speech-to-text's untimed cold decode at the reference box's
    cold rtf plus each timed one at its bar, the aligner's warm-up plus one line at its bar. A faster computer is
    quicker; the plate says the time it really took (elapsed_s)."""
    th = th or thresholds()
    cc = cc or card_cfg()
    k = "quick" if quick else "full"
    reps = max(1, int(th["repeats_" + k]))
    line_s = len(str(th["probe_text"]).split()) / max(0.5, float(cc["words_per_s"]))
    t = float(th["frames_" + k]) / (float(th["render_fps"]) * float(th["render_x_realtime_min"]))
    if pl.get("ladder"):
        # each voice down the ladder at most: its untimed warm-up line and the timed ones, at the first-audio bar
        t += len(pl["ladder"]) * (1 + reps) * max(float(th["tts_first_audio_s_max"]), line_s * float(th["tts_rtf_max"]))
    elif pl.get("tts"):
        t += reps * float(th["tts_first_audio_s_max"])
    if pl.get("asr"):
        t += line_s * (float(cc["asr_cold_rtf"]) + reps * float(th["asr_rtf_max"]))
    if pl.get("align"):
        t += float(cc["align_warm_s"]) + float(th["align_s_per_line_max"])
    if density_on() and (ROOT / "lib" / "wall_ascii_disk.py").is_file():
        # the Wall's render at each density, one warm-up and the timed frames, at the frame's bar (stage_density)
        t += len(th["density_candidates"]) * (int(th["density_frames"]) + 1) / float(th["render_fps"])
        if density_strict_on():
            t += float(th["density_child_start_s"])         # the strict pick's one film child (stage_density)
    return round(t, 1)


def say_seconds(s: float) -> str:
    n = max(1, int(round(float(s))))
    if n >= 90:
        return "about %d minutes" % round(n / 60.0)
    return "about %d second%s" % (n, "" if n == 1 else "s")


def _join(parts: list) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


# THE SCAN NEVER SHOWS HER (round 17, the User 2026-10-13, .101 round 16: "Crissa plays a few seconds of her video" read
# as her film on the screen, and Crissa was on screen for This computer - by the player's own wrap rule, which Setup
# now holds off: lib/crissa_init.lila_holding). The render and density stages decode her film inside this process and
# the Wall's probe process, never on the glass, so in a pack the card says so; Lila holds the screen meanwhile.
# Kill: CRISSA_LILA_HOLDS_PLATE=0 (the round-16 words).
VIDEO_WORDS = "plays a few seconds of her video"
VIDEO_WORDS_OFF_GLASS = "runs a few seconds of her video off screen"


def _video_words() -> str:
    off = os.environ.get("CRISSA_LILA_HOLDS_PLATE", "1").strip().lower() in ("0", "off", "false", "no")
    return VIDEO_WORDS if (off or crissa_home.is_house()) else VIDEO_WORDS_OFF_GLASS


def card(quick: bool = True, env: dict | None = None, home: Path | None = None, tts_cfg: dict | None = None) -> dict:
    """The permission card's words, from what the stages would really do on this computer now (plan()), how long that
    takes (estimate_s, quick = Init's; the full one is the command line's), whether a sound plays (SOUND_PLAYS) and
    that nothing leaves the computer (LOCAL_ONLY). No setting name, no command, no character's name."""
    pl = plan(env, home, tts_cfg)
    th, cc = thresholds(home), card_cfg(home)
    secs, full = estimate_s(pl, quick, th, cc), estimate_s(pl, not quick, th, cc)
    does = [_video_words()]
    if pl.get("ladder"):
        names = [LABELS.get(x, x) for x in pl["ladder"]]
        does.append("makes a short test line in %s, from the largest down until one is fast enough"
                    % ("her voice (%s)" % names[0] if len(names) == 1 else "each of her voices here (%s)"
                       % _join(names)))
    elif pl["tts"]:
        does.append("makes one short test line in her voice")
    if pl["asr"]:
        does.append("hears it back")
    if pl["align"]:
        does.append("lines up its words")
    what = "Crissa %s, times %s, and suggests where her voice should run." % (
        _join(does), "it" if len(does) == 1 else "each step")
    if not pl["tts"]:
        what += " Her voice is not installed yet, so it is not timed: it is chosen from this computer's hardware."
    facts = "It takes %s here. %s %s" % (
        say_seconds(secs),
        "A sound plays." if SOUND_PLAYS else ("No sound plays: the test line stays inside this computer."
                                              if pl["tts"] else "No sound plays."),
        "Nothing leaves this computer." if LOCAL_ONLY else "")
    return {"title": "Check this computer?", "what": what, "facts": facts.strip(),
            "skip": "Skip, and the voice is chosen from this computer's hardware alone: its graphics card, memory, "
                    "cores and system. Nothing is measured.",
            "seconds": secs, "seconds_other": full, "quick": bool(quick), "sound": SOUND_PLAYS,
            "stays_here": LOCAL_ONLY, "stages": [s for s in ("render", "tts", "asr", "align") if pl[s]],
            "pending": list(pl["pending"]), "ladder": list(pl.get("ladder") or [])}


# ---- the tier --------------------------------------------------------------------------------------

def decide(stages: dict, th: dict, pending: tuple | list = (), off: dict | None = None) -> tuple[str, list]:
    """(tier, reasons) from the measured stages and the bars. `pending`: stages not installed or running here yet
    (a new install, before its voice): not timed and not held against this computer, said in the reasons. `off`:
    stages whose service is on another computer, never timed (nothing leaves this one): "internet" is remote,
    "lan" is split."""
    reasons, voice_ok = [], True
    off = off or {}
    r = stages.get("render")
    if r is None:
        voice_ok = False
        reasons.append("render was not measured")
    elif r["x_realtime"] < th["render_x_realtime_min"]:
        voice_ok = False
        reasons.append("render %.1fx real time is under %.1fx: the Wall itself is slow here"
                       % (r["x_realtime"], th["render_x_realtime_min"]))
    t, a, al = stages.get("tts"), stages.get("asr"), stages.get("align")
    if (t and t.get("url_lan") is False) or (a and a.get("url_lan") is False) or "internet" in off.values():
        return "remote", reasons + ["this install already sends its voice off the LAN"]
    if t is None and off.get("tts") == "lan":
        voice_ok = False
        reasons.append("the voice is set up on another computer on your network (not timed: nothing leaves this one)")
    elif t is None and "tts" in pending:
        reasons.append("the voice is not installed here yet, so it was not timed: it is chosen from this computer's "
                       "hardware")
    elif isinstance(stages.get("voices"), dict) and stages["voices"].get("ladder"):
        lad = stages["voices"]
        rec_ = lad.get("recommended")
        if rec_:
            reasons.append("her voice here: %s, the largest voice that clears the bars on this computer"
                           % (lad.get("label") or rec_))
        else:
            voice_ok = False
        for eng in lad.get("ladder") or []:
            if eng == rec_:
                break
            miss = (lad.get("missed") or {}).get(eng)
            if miss:
                reasons.append("%s: %s" % (LABELS.get(eng, eng), "; ".join(str(m) for m in miss)))
    elif t is None:
        voice_ok = False
        reasons.append("no voice measured here")
    else:
        bar, strict = rtf_bar(t, th)
        if t["rtf"] is not None and (t["rtf"] >= bar if strict else t["rtf"] > bar):
            voice_ok = False
            reasons.append("voice rtf %.2f is %s %.2f" % (t["rtf"], "not under" if strict else "over", bar))
        if t["first_audio_s"] > th["tts_first_audio_s_max"]:
            voice_ok = False
            reasons.append("voice first audio %.2f s is over %.2f s" % (t["first_audio_s"], th["tts_first_audio_s_max"]))
    if a is None and off.get("asr") == "lan":
        voice_ok = False
        reasons.append("speech-to-text is set up on another computer on your network (not timed)")
    elif a is None and "asr" in pending:
        if "tts" not in pending:
            reasons.append("speech-to-text is not running here yet, so it was not timed")
    elif a is None:
        voice_ok = False
        reasons.append("no speech-to-text measured here")
    elif a["rtf"] > th["asr_rtf_max"]:
        voice_ok = False
        reasons.append("speech-to-text rtf %.2f is over %.2f" % (a["rtf"], th["asr_rtf_max"]))
    if al is not None and al["s_per_line"] > th["align_s_per_line_max"]:
        reasons.append("the aligner takes %.2f s a line (over %.2f s): mouths fall back to the letter clock"
                       % (al["s_per_line"], th["align_s_per_line_max"]))
    return ("local" if voice_ok else "split"), reasons


def run_check(quick: bool = False, env: dict | None = None, home: Path | None = None, tts_cfg: dict | None = None,
              write: bool = True, permission: str = "allow") -> dict:
    e = dict(os.environ if env is None else env)
    if not on(e):
        return {"tier": None, "skipped": "off"}
    if crissa_home.is_house() and str(e.get("CRISSA_MACHINE_CHECK_HOUSE", "")).strip() != "1":
        # the house's default ports are its live ear and aligner: a probe line there would land in the ear
        return {"tier": None, "skipped": "house"}
    t0 = time.monotonic()
    th = thresholds(home)
    k = "quick" if quick else "full"
    pl = plan(e, home, tts_cfg)
    stages, notes = {}, []
    stages["render"], why = stage_render(int(th["frames_" + k]), float(th["render_fps"]))
    notes += [why] if why else []
    stages["density"], why = stage_density(th, home, e)
    notes += [why] if why else []
    pcm = b""
    if pl.get("ladder"):
        lad, pcm = run_ladder(str(th["probe_text"]), th, int(th["repeats_" + k]), pl["ladder"], home)
        stages["voices"] = lad
        rec_ = lad.get("recommended")
        stages["tts"] = (lad["measured"].get(rec_) if rec_ else
                         next((lad["measured"][e_] for e_ in lad["ladder"] if e_ in lad["measured"]), None))
        notes += ["%s: %s" % (LABELS.get(e_, e_), "; ".join(w)) for e_, w in (lad.get("missed") or {}).items()
                  if e_ not in lad["measured"]]
    elif pl["tts"]:
        stages["tts"], why, pcm = stage_tts(str(th["probe_text"]), int(th["repeats_" + k]), pl["tts_cfg"] or None)
        notes += [why] if why else []
    if pl["asr"]:
        stages["asr"], why = stage_asr(pcm, _asr_url(e), int(th["repeats_" + k]))
        notes += [why] if why else []
    if pl["align"]:
        stages["align"], why = stage_align(pcm, str(th["probe_text"]), _align_url(e))
        notes += [why] if why else []
    stages = {s: v for s, v in stages.items() if v is not None}
    tier, reasons = decide(stages, th, pending=pl["pending"], off=pl["off"])
    voice = None
    if isinstance(stages.get("voices"), dict) and stages["voices"].get("recommended"):
        voice = {"engine": stages["voices"]["recommended"], "label": stages["voices"].get("label") or ""}
    rec = {"schema": SCHEMA, "at": _now(), "ref": _ref(), "quick": quick, "permission": permission,
           "tier": tier, "stages": stages, "thresholds": th, "reasons": reasons, "notes": notes,
           "pending": list(pl["pending"]), "off": dict(pl["off"]), "elapsed_s": round(time.monotonic() - t0, 2),
           "voice": voice}
    if write:
        if stages.get("density"):
            try:
                stages["density"]["applied"] = apply_density(stages["density"], home)
            except Exception as exc:  # noqa: BLE001
                stages["density"]["applied"] = {"applied": False, "why": "%s" % type(exc).__name__}
        _write(rec, home)
        try:
            rec["announcer_fit"] = fit_announcer(home, rec, e)
        except Exception as exc:  # noqa: BLE001  the card's answer stands; the announcer keeps its order
            rec["announcer_fit"] = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    return rec


# THE ANNOUNCER FOLLOWS THE SAME LADDER (the User 2026-10-10, .101: after the voice went to Pocket the announcer stayed
# on Audio8's anna, which the card had timed at rtf 3.00 / first audio 2.10 s there). A voice the card measured and that
# missed its bars goes behind the voices that cleared them in the announcer's order - Pocket's anna then speaks - through
# the tier table's own per-install copy (<state home>/crissa-voice-tiers.json, `announcer.order`, layered key by key
# over the shipped table by lib/crissa_voice_tiers.table), so the voice pick and its re-pick check agree with it. The
# saved pick's announcer_chain is brought in line too. Never removes a voice: a slow one is still there behind the
# others. Kill CRISSA_ANNOUNCER_FOLLOWS_CARD=0.
ANNOUNCER_FIT_KEY = "_announcer_fit"


def slow_voices(rec: dict | None) -> dict:
    """{engine: [why]} for each voice the card timed that missed a bar (not one it could not time)."""
    lad = (((rec or {}).get("stages") or {}).get("voices") or {})
    measured = lad.get("measured") or {}
    return {e: list(w) for e, w in (lad.get("missed") or {}).items() if e in measured and w}


def fit_announcer(home: Path | None = None, rec: dict | None = None, env: dict | None = None) -> dict:
    """Put the card's slow voices behind the fast ones in the announcer's order. {"order", "slow", "chain", "wrote"}."""
    e = os.environ if env is None else env
    out: dict = {"order": [], "slow": {}, "chain": [], "wrote": False}
    if str(e.get("CRISSA_ANNOUNCER_FOLLOWS_CARD", "1")).strip().lower() in ("0", "off", "false", "no"):
        return dict(out, skipped="off")
    if crissa_home.is_house():
        return dict(out, skipped="house")
    home = Path(home or crissa_home.HOME)
    VT = _vt()
    if VT is None:
        return dict(out, skipped="no voice tiers")
    rec = read(home) if rec is None else rec
    slow = slow_voices(rec)
    own = home / "crissa-voice-tiers.json"
    try:
        mine = json.loads(own.read_text(encoding="utf-8")) if own.is_file() else {}
    except (OSError, ValueError):
        mine = {}
    shipped = list((VT.table(home=Path("/nonexistent")).get("announcer") or {}).get("order") or [])
    order = ([x for x in shipped if x not in slow and x != "text"] + [x for x in shipped if x in slow]
             + (["text"] if "text" in shipped else []))       # on-screen text stays the last resort
    ann = dict(mine.get("announcer")) if isinstance(mine.get("announcer"), dict) else {}
    had_ours = bool(mine.get(ANNOUNCER_FIT_KEY))
    if slow and order != shipped:
        mine["announcer"] = dict(ann, order=order)
        mine[ANNOUNCER_FIT_KEY] = {"slow": slow, "at": _now(), "why": "the machine card timed these voices over its "
                                   "bars here, so the announcer speaks with the ones that cleared them first"}
    elif had_ours:                                 # the card no longer says so: the shipped order again
        ann.pop("order", None)
        if ann:
            mine["announcer"] = ann
        else:
            mine.pop("announcer", None)
        mine.pop(ANNOUNCER_FIT_KEY, None)
        order = shipped
    else:
        return dict(out, order=shipped, slow=slow)
    if mine:
        own.write_text(json.dumps(mine, indent=2) + "\n", encoding="utf-8")
    else:
        own.unlink(missing_ok=True)
    out.update(order=order, slow=slow, wrote=True)
    tr = VT.read(home)
    if tr:
        try:
            chain, passed = VT.announcer_chain(tr.get("facts") or VT.probe(), VT.table(home), VT.broken())
        except Exception:  # noqa: BLE001  the record is left as it is; the next pick takes the order
            return out
        if chain != tr.get("announcer_chain"):
            tr.update(announcer_chain=chain, announcer_passed=passed,
                      announcer=chain[0] if chain and chain[0] != tr.get("main") else "")
            VT._write(tr, home)
        out["chain"] = chain
    return out


def _ref() -> str | None:
    import re
    try:
        m = re.search(r"ref `([0-9a-f]{7,40})`", (ROOT / "README.md").read_text(encoding="utf-8"))
        return m.group(1)[:12] if m else None
    except OSError:
        return None
