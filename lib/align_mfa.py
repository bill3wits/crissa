"""The word aligner's engine: Montreal Forced Aligner (2026-09-28).

WHAT. Montreal Forced Aligner with the english_us_arpa bundle (acoustic model 3.3.0, dictionary,
G2P 3.2.0) gives each spoken line word AND phone timings; the mouth clock reads them.
Pinned: MontrealCorpusTools/english_us_arpa @ 9c8d5d8e1d.

SHAPE. rail-align.py keeps its HTTP contract; with HOUSE_ALIGN_ENGINE=mfa it calls
`Engine.align(words, keep, wav16k_path)` here. The engine runs in the `mfa` env
(its own env, never the env the wall runs in).

A CRASH IN NATIVE CODE MUST NOT TAKE THE SERVICE DOWN. Measured 2026-09-28: one
KalpyAligner reused across lines segfaulted in compile_fst on a line whose audio
was shorter than its text. A fresh aligner per line (what MFA's align_one does)
raises AlignerError instead, and costs nothing measurable (median 149 ms, p95
167 ms for 1.6-2.3 s lines, warm). Even so every alignment runs in a warm child
process: if the child dies the request answers 503 and a new child warms.

`chars` from phones: every consumer (mouth_timing._letter_segments / viseme_onsets,
mouth_clock, pose_intake, clip_words, stamp-hits) only asks whether a letter is a
vowel or a consonant and when. MFA aligns phones, not letters,
so letter runs are paired with phone runs (vowel with vowel, consonant with
consonant, in order) and each letter run is spread over its paired time.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import re
import threading
import time
from pathlib import Path

BUNDLE_REPO = "models--MontrealCorpusTools--english_us_arpa"
BUNDLE_REV = "9c8d5d8e1dd5d9cf188a5571203de0e09ca6bede"


def hf_hub_cache(env: dict | None = None) -> Path:
    """The Hugging Face hub cache, resolved the way huggingface_hub resolves it: HF_HUB_CACHE, else
    HF_HOME/hub, else XDG_CACHE_HOME/huggingface/hub, else ~/.cache/huggingface/hub."""
    e = os.environ if env is None else env
    if (e.get("HF_HUB_CACHE") or "").strip():
        return Path(e["HF_HUB_CACHE"].strip()).expanduser()
    if (e.get("HF_HOME") or "").strip():
        return Path(e["HF_HOME"].strip()).expanduser() / "hub"
    xdg = (e.get("XDG_CACHE_HOME") or "").strip()
    return (Path(xdg).expanduser() if xdg else Path.home() / ".cache") / "huggingface" / "hub"


def bundle_path(env: dict | None = None, house: str | None = None) -> Path:
    """HOUSE_ALIGN_MFA_BUNDLE, else this machine's own copy (config/house-machine.json `mfa_bundle`), else the
    pinned snapshot in the Hugging Face cache. A machine's path lives in config, never here (2026-10-06)."""
    e = os.environ if env is None else env
    got = (e.get("HOUSE_ALIGN_MFA_BUNDLE") or "").strip()
    if got:
        return Path(got).expanduser()
    if house is None:
        try:
            import crissa_home
            house = crissa_home.house_value("mfa_bundle")
        except ImportError:          # the aligner env may run this file alone (scripts/crissa-align.py)
            house = ""
    if house:
        return Path(house).expanduser()
    return hf_hub_cache(e) / BUNDLE_REPO / "snapshots" / BUNDLE_REV


BUNDLE = bundle_path()
VOWEL_LETTERS = set("aeiou")
SIL = {"", "<eps>", "sil", "sp", "spn"}
# MFA's search settings (beam, retry_beam, boost_silence, self_loop_scale, transition_scale,
# acoustic_scale) as JSON; {} = MFA's defaults. The tuning round's verdict lives in the checkpoint.
import json as _json
OPTS = _json.loads(os.environ.get("HOUSE_ALIGN_MFA_OPTS", "") or "{}")


# ---- chars from phones (pure; the house tests reach it without MFA) ---------------------------

def letter_runs(word: str) -> list[tuple[str, list[str]]]:
    """[(kind, letters)] vowel/consonant runs of a word, the same rule as
    mouth_timing._letter_segments: y is a vowel unless it opens the word;
    apostrophes are skipped."""
    runs: list[tuple[str, list[str]]] = []
    for j, c in enumerate(word.lower()):
        if c == "'":
            continue
        kind = "v" if (c in VOWEL_LETTERS or (c == "y" and j > 0)) else "c"
        if runs and runs[-1][0] == kind:
            runs[-1][1].append(c)
        else:
            runs.append((kind, [c]))
    return runs


def phone_runs(phones: list[dict]) -> list[tuple[str, float, float]]:
    """[(kind, a, b)] vowel/consonant runs of phones. ARPA vowels carry a stress digit."""
    runs: list[tuple[str, float, float]] = []
    for p in phones:
        lab = str(p.get("p") or "")
        if lab.lower() in SIL:
            continue
        kind = "v" if re.search(r"\d$", lab) else "c"
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1], float(p["b"]))
        else:
            runs.append((kind, float(p["a"]), float(p["b"])))
    return runs


# A SILENT VOWEL LETTER IS CHEAPER THAN A SOUNDED CONSONANT ON A VOWEL (the User 2026-10-18, "impressed" held one shape
# through four phonemes): English writes vowels it does not say (the e of -ed, -es, a final e), and with every
# mismatch at 1 the two pairings of i-mpr-e-ss-e-d onto IH-MPR-EH-ST tied, and the tie gave i, mpr AND e to IH's one
# frame and the ss to the M-P-R closure - the sheet painted S through her four-frame M-P. A vowel letter on
# consonant phones costs half - less on the word's LAST vowel run (0.3), more on an earlier one (0.7), which breaks the
# tie between the two e's of -essed. Kill HOUSE_ALIGN_SILENT_VOWEL=0 (every mismatch 1, as before).
SILENT_VOWEL_ON = os.environ.get("HOUSE_ALIGN_SILENT_VOWEL", "1").strip().lower() not in ("0", "false", "off", "no")
SILENT_VOWEL_COST = 0.5 if SILENT_VOWEL_ON else 1.0


def _pair(lr: list, pr: list) -> list[list[int]]:
    """For each letter run, the phone runs it takes: a monotonic many-to-many
    alignment (DTW) where a kind mismatch costs 1. Every run on each side is used."""
    n, m = len(lr), len(pr)
    last_v = max((k for k, r in enumerate(lr) if r[0] == "v"), default=-1)
    INF = float("inf")
    cost = [[INF] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if lr[i - 1][0] == pr[j - 1][0]:
                c = 0.0
            elif lr[i - 1][0] == "v":         # a vowel the word writes and does not say: most often its LAST one (-ed, -es, -e)
                c = SILENT_VOWEL_COST * ((0.6 if i - 1 == last_v else 1.4) if SILENT_VOWEL_ON else 1.0)
            else:
                c = 1.0
            best = min((cost[i - 1][j - 1], (i - 1, j - 1)), (cost[i - 1][j], (i - 1, j)),
                       (cost[i][j - 1], (i, j - 1)), key=lambda t: t[0])
            cost[i][j] = best[0] + c
            back[i][j] = best[1]
    take: list[list[int]] = [[] for _ in range(n)]
    i, j = n, m
    while i > 0 and j > 0:
        take[i - 1].append(j - 1)
        i, j = back[i][j]
    return [sorted(set(t)) for t in take]


def chars_from_phones(word: str, a: float, b: float, phones: list[dict]) -> list[dict]:
    """Per-letter spans [{c, a, b}] for one word from its phone intervals, every one
    inside [a, b]. Letters of a run share its time evenly; runs that take the same
    phone run share that run's time evenly, in order."""
    lr, pr = letter_runs(word), phone_runs(phones)
    if not lr:
        return []
    if not pr:
        pr = [("v", a, b)]
    take = _pair(lr, pr)
    # runs that share a phone run split it in order
    owners: dict[int, list[int]] = {}
    for li, js in enumerate(take):
        for j in js:
            owners.setdefault(j, []).append(li)
    spans: list[tuple[float, float]] = []
    for li, js in enumerate(take):
        pieces = []
        for j in js:
            _, pa, pb = pr[j]
            share = owners[j]
            k, n = share.index(li), len(share)
            pieces.append((pa + (pb - pa) * k / n, pa + (pb - pa) * (k + 1) / n))
        spans.append((min(p[0] for p in pieces), max(p[1] for p in pieces)))
    out = []
    for (kind, letters), (ra, rb) in zip(lr, spans):
        ra, rb = max(a, ra), min(b, rb)
        if rb < ra:
            rb = ra
        step = (rb - ra) / len(letters)
        for k, c in enumerate(letters):
            out.append({"c": c, "a": round(ra + step * k, 4), "b": round(ra + step * (k + 1), 4)})
    return out


def words_from_ctm(words: list[str], keep: list[int], intervals: list[dict]) -> list[dict]:
    """Map MFA word intervals (silences dropped) back onto the caller's words in
    order; `i` is the caller's original index, never renumbered."""
    ivs = [iv for iv in intervals if str(iv.get("w") or "").lower() not in SIL]
    out, k = [], 0
    for w, idx in zip(words, keep):
        while k < len(ivs) and ivs[k]["w"] != w and ivs[k]["w"] not in (w.replace("'", ""),):
            k += 1                      # a token MFA split or dropped: skip to the next match
        if k >= len(ivs):
            break
        iv = ivs[k]
        k += 1
        row = {"w": w, "i": idx, "a": round(iv["a"], 4), "b": round(iv["b"], 4), "score": None,
               "phones": [{"p": p["p"], "a": round(p["a"], 4), "b": round(p["b"], 4)} for p in iv["phones"]
                          if str(p["p"]).lower() not in SIL]}
        row["chars"] = chars_from_phones(w, row["a"], row["b"], row["phones"])
        out.append(row)
    return out


# ---- the MFA child (runs only in the mfa env) --------------------------------------------------

def _child(conn, bundle: str) -> None:
    import warnings
    warnings.filterwarnings("ignore")
    from kalpy.aligner import KalpyAligner
    from kalpy.data import Segment
    from kalpy.feat.cmvn import CmvnComputer
    from kalpy.fstext.lexicon import LexiconCompiler, Pronunciation
    from kalpy.utterance import Utterance
    from montreal_forced_aligner.models import AcousticModel, G2PModel
    B = Path(bundle)
    am = AcousticModel(B / "acoustic")
    g2p = G2PModel(B / "g2p" / "english_us_arpa")
    lc = LexiconCompiler(
        disambiguation=False, silence_probability=am.parameters["silence_probability"],
        initial_silence_probability=am.parameters["initial_silence_probability"],
        final_silence_correction=am.parameters["final_silence_correction"],
        final_non_silence_correction=am.parameters["final_non_silence_correction"],
        silence_phone=am.parameters["optional_silence_phone"], oov_phone=am.parameters["oov_phone"],
        position_dependent_phones=am.parameters["position_dependent_phones"],
        phones=am.parameters["non_silence_phones"], ignore_case=True)
    lc.load_pronunciations(B / "dictionary" / "english_us_arpa.dict")
    lc.create_fsts()
    lc.clear()
    conn.send({"ready": True})
    while True:
        job = conn.recv()
        if job is None:
            return
        try:
            for w in job["words"]:
                if not lc.word_table.member(w):
                    p = g2p.rewriter(w)
                    if p:
                        lc.add_pronunciation(Pronunciation(w, p[0].pronunciation, None, None, None, None, None))
            utt = Utterance(Segment(job["wav"], 0.0, None, 0), " ".join(job["words"]))
            utt.generate_mfccs(am.mfcc_computer)
            utt.apply_cmvn(CmvnComputer().compute_cmvn_from_features([utt.mfccs]))
            ctm = KalpyAligner(am, lc, **OPTS).align_utterance(utt)
            conn.send({"ok": True, "intervals": [
                {"w": wi.label, "a": float(wi.begin), "b": float(wi.end),
                 "phones": [{"p": p.label, "a": float(p.begin), "b": float(p.end)} for p in wi.phones]}
                for wi in ctm.word_intervals]})
        except Exception as e:  # noqa: BLE001
            conn.send({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e).strip().splitlines()[0][:160]
                                                        if str(e).strip() else "")})


class Engine:
    """A warm MFA child behind a lock; a dead child answers once and is replaced."""

    def __init__(self, bundle: Path = BUNDLE):
        self.bundle = str(bundle)
        self.lock = threading.Lock()
        self.proc = None
        self.conn = None
        self.load_s = 0.0

    def start(self) -> None:
        t0 = time.time()
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        self.proc = ctx.Process(target=_child, args=(child, self.bundle), daemon=True, name="align-mfa")
        self.proc.start()
        msg = parent.recv()
        if not msg.get("ready"):
            raise RuntimeError("mfa child did not warm")
        self.conn = parent
        self.load_s = time.time() - t0

    def align(self, words: list[str], keep: list[int], wav16k: str) -> dict:
        with self.lock:
            if self.proc is None or not self.proc.is_alive():
                self.start()
            try:
                self.conn.send({"words": words, "wav": wav16k})
                msg = self.conn.recv()
            except (EOFError, OSError, BrokenPipeError):
                self.proc = None                       # the child died mid-line: a new one warms next time
                return {"ok": False, "error": "the aligner child died on this line; it is restarting"}
        if not msg.get("ok"):
            return {"ok": False, "error": msg.get("error") or "alignment failed"}
        return {"ok": True, "words": words_from_ctm(words, keep, msg["intervals"])}
