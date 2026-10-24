"""A line's mouth sheet for the phone: house-speak's own sheet, caught, put on the clock of the line's audio file.

house-speak posts its sheet to VO_MOUTH_URL as payloads {air_at, pts0, hop_ms, rms[], visemes[]} on the wall clock,
with the sheet lead already in pts0 (the visemes run `lead` early on purpose). The phone plays the line's WAV, so the
rows must be on the WAV's clock: the envelope rows (the same pts0, so also `lead` early) are matched against the
WAV's own envelope, which says where air_at falls in the file; the viseme rows keep their lead.

    sheet = {"hop": seconds, "t0": WAV time of row 0, "classes": [class per hop], "dur": WAV seconds, "lead": s}
"""
from __future__ import annotations

import math
import os

# the wire's first 19 classes, rest = 0: every phone pack baked before round 41 paints exactly these
WIRE19 = ("rest", "MBP", "FV", "TH", "TDN", "L", "R", "KG", "S/Z", "SH/CH", "EE", "EH", "ER", "AH", "OH", "WOO",
          "Y", "H", "rest-open")
try:
    from mouth_viseme import CLASSES, CLASS_FALLBACK
except ImportError:                     # the phone app's own repo: the wire's class order, rest = 0
    CLASSES = WIRE19
    CLASS_FALLBACK = {"Ah": "AH", "Aa": "AH"}


def pack_has_v2(have) -> bool:
    """Does a pack paint his round 41 classes (its manifest's `classes` names Ah and Aa)? None/[] = an old pack."""
    return bool(have) and all(c in have for c in CLASS_FALLBACK)


def classes_env(have) -> dict[str, str]:
    """ROUND 41 GUARD (b3 2026-10-22: V2 ships ON in the kit, the phone's V2 bake is post-launch): a line for a pack
    that does not paint Ah/Aa is spoken with HOUSE_MOUTH_CLASSES_V2=0, so its sheet is today's 19 classes byte for
    byte (no merges, no ids 19/20). A pack that lists them gets nothing, and the kit's own default stands."""
    return {} if pack_has_v2(have) else {"HOUSE_MOUTH_CLASSES_V2": "0"}


def fold(classes: list, have) -> list:
    """Belt to classes_env: a class the pack cannot paint becomes its fallback (Ah/Aa -> AH), never a blank cell.
    `have` None/[] = an old pack, which paints WIRE19."""
    can = set(have or WIRE19)
    return [c if c in can else CLASS_FALLBACK.get(c, c) for c in classes]


def rows(payloads: list[dict]) -> tuple[list[tuple[float, str]], list[tuple[float, float]], float]:
    """([(pts, class)], [(pts, rms)], hop seconds) from caught payloads, in time order, one row per hop."""
    vis: dict[int, str] = {}
    env: dict[int, float] = {}
    hop = None
    for p in payloads:
        try:
            h = float(p.get("hop_ms") or 20.0) / 1000.0
            p0 = float(p["pts0"])
        except (KeyError, TypeError, ValueError):
            continue
        hop = hop or h
        for i, v in enumerate(p.get("visemes") or []):
            try:
                c = CLASSES[int(v)]
            except (TypeError, ValueError, IndexError):
                c = str(v)
            vis[int(round((p0 + i * h) / h))] = c
        for i, r in enumerate(p.get("rms") or []):
            env[int(round((p0 + i * h) / h))] = float(r)
    hop = hop or 0.02
    return ([(k * hop, vis[k]) for k in sorted(vis)], [(k * hop, env[k]) for k in sorted(env)], hop)


def wav_envelope(samples, sr: int, hop: float) -> list[float]:
    """RMS per hop of mono float samples."""
    n = max(1, int(round(sr * hop)))
    out = []
    for i in range(0, len(samples) - n + 1, n):
        blk = samples[i:i + n]
        out.append(math.sqrt(sum(x * x for x in blk) / n))
    return out


def _z(xs: list[float]) -> list[float]:
    if not xs:
        return []
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) or 1.0
    return [(x - m) / sd for x in xs]


def best_shift(a: list[float], b: list[float], lo: int, hi: int) -> tuple[int, float]:
    """The shift k in [lo, hi] maximising sum a[i] * b[i + k] / overlap (b later by k when k > 0)."""
    za, zb = _z(a), _z(b)
    best, score = 0, -1e9
    for k in range(lo, hi + 1):
        s = c = 0
        for i, x in enumerate(za):
            j = i + k
            if 0 <= j < len(zb):
                s += x * zb[j]
                c += 1
        if c >= max(8, len(za) // 3):
            v = s / c
            if v > score:
                best, score = k, v
    return best, score


def sheet(payloads: list[dict], samples, sr: int, lead_s: float = 0.0, search_s: float = 3.0, have=None) -> dict:
    """The phone's sheet for one line (see the module doc). `samples`: the WAV, mono floats. `have`: the pack's own
    class list (its manifest's `classes`); a class it lacks is folded (fold)."""
    vis, env, hop = rows(payloads)
    if not vis:
        return {"hop": hop, "t0": 0.0, "classes": [], "dur": len(samples) / float(sr), "lead": lead_s, "match": None}
    wenv = wav_envelope(samples, sr, hop)
    # the envelope rows' audio time is pts + lead; find where row 0 of them falls in the WAV
    e0 = env[0][0] + lead_s if env else vis[0][0] + lead_s
    k, score = best_shift([r for _, r in env], wenv, -int(search_s / hop), int(search_s / hop)) if env else (0, None)
    wav_of_env0 = k * hop                          # WAV time of the first envelope row
    shift = wav_of_env0 - e0                       # pts (audio) -> WAV time
    t0 = vis[0][0] + shift                         # visemes keep their lead: pts as posted
    k0 = int(round(vis[0][0] / hop))
    classes = []
    by = {int(round(t / hop)): c for t, c in vis}
    for kk in range(k0, int(round(vis[-1][0] / hop)) + 1):
        classes.append(by.get(kk, classes[-1] if classes else "rest"))
    return {"hop": hop, "t0": round(t0, 4), "classes": fold(classes, have), "dur": round(len(samples) / float(sr), 4),
            "lead": lead_s, "match": None if score is None else round(score, 3)}


def class_at(sh: dict, t: float, idle: str = "rest") -> str:
    """The class the phone paints at WAV time t (the app's rule, here for tests)."""
    i = int(math.floor((t - float(sh["t0"])) / float(sh["hop"])))
    cl = sh.get("classes") or []
    return cl[i] if 0 <= i < len(cl) else idle


TUNE_INT = (("min_hold", "HOUSE_MOUTH_MIN_HOLD"), ("cons_hold", "HOUSE_MOUTH_CONS_HOLD"),
            ("sent_pad", "HOUSE_MOUTH_SENT_PAD"), ("word_pad", "HOUSE_MOUTH_WORD_PAD"))
TUNE_FLOAT = (("pause_words", "HOUSE_MOUTH_PAUSE_WORDS"), ("blip_words", "HOUSE_MOUTH_BLIP_WORDS"),
              ("tail_words", "HOUSE_MOUTH_TAIL_WORDS"), ("timing_lead_ms", "HOUSE_MOUTH_TIMING_LEAD_MS"))


def speak_env(dials: dict, sync_zero_ms: float = 0.0) -> dict[str, str]:
    """house-speak's env for one line on the pack's own tune (what a bench hands its Say, conductor
    speak_timing_env): the lock's lead plus its calibrated zero, its holds and pads, handed over as explicit so
    nothing asks a conductor (a phone server needs no bench running)."""
    out = {"HOUSE_MOUTH_LEAD_SRC": "explicit", "HOUSE_SPEAK_TEMPO": "1.000", "HOUSE_PLAY_RATE": "1.000",
           "HOUSE_MOUTH_SHEET_LEAD_MS": str(int(round(float(dials.get("sheet_lead_ms") or 0.0) + float(sync_zero_ms or 0.0))))}
    for k, name in TUNE_INT:
        if isinstance(dials.get(k), (int, float)):
            out[name] = str(int(dials[k]))
    for k, name in TUNE_FLOAT:
        if isinstance(dials.get(k), (int, float)):
            out[name] = "%.2f" % float(dials[k]) if k != "timing_lead_ms" else str(int(round(float(dials[k]))))
    # THE CALM MERGE AS TUNED (round 30, 2026-10-18): the phone's tune was made on a bench with HOUSE_MOUTH_CALM_HOPS=0,
    # but a phone line read house-speak's own default (3 hops, 1.5 f, or the state home's mouth-calm.json), so it merged
    # shapes the bench never did. The env outranks the file (house-speak resolve_calm): the lock's `calm_hops` when it
    # has one, else 0. Kill: PHONE_CALM_AS_TUNED=0 (house-speak's own default again).
    if str(os.environ.get("PHONE_CALM_AS_TUNED", "1")).strip().lower() not in ("0", "off", "false", "no"):
        ch = dials.get("calm_hops")
        out["HOUSE_MOUTH_CALM_HOPS"] = str(max(0, min(12, int(ch)))) if isinstance(ch, (int, float)) else "0"
    return out
