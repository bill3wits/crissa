"""A long line is paired in seconds, not minutes (2026-10-23, the demo lap: a 918-character case brief held house-speak
163 s after her voice ended, in espeak_line's pairing). The search is pruned exactly - the same pairing word for word -
and the kill HOUSE_MOUTH_PHONES_PRUNE=0 puts the full search back."""
from __future__ import annotations

import importlib
import os
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _load(**env):
    keys = ("HOUSE_MOUTH_PHONES_PRUNE", *env)
    old = {k: os.environ.get(k) for k in keys}
    os.environ.pop("HOUSE_MOUTH_PHONES_PRUNE", None)
    os.environ.update(env)
    try:
        import mouth_viseme
        return importlib.reload(mouth_viseme)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(autouse=True)
def _back_to_default():
    yield
    _load()


def _path(back, n, m):
    out, i, j = [], n, m
    while (i, j) != (0, 0):
        step = back[i][j]
        out.append(step)
        i, j = step[0], step[1]
    return out[::-1]


def _line(rng, words):
    """Our words' citation phones and espeak's words for them: mostly one to one, some joined ("for the"), some
    expanded (a date read as seven words), some with a phone changed (a weak form)."""
    inv = list("aeiouptkbdgmnszfvlrwj") + ["@", "I", "D", "T"]
    alone = [[rng.choice(inv) for _ in range(rng.randint(1, 6))] for _ in range(words)]
    toks, i = [], 0
    while i < words:
        roll = rng.random()
        if roll < 0.08 and i + 1 < words:
            toks.append(alone[i] + alone[i + 1])
            i += 2
            continue
        if roll < 0.12:
            toks.extend([[rng.choice(inv) for _ in range(rng.randint(2, 5))] for _ in range(rng.randint(2, 8))])
        elif roll < 0.3:
            w = list(alone[i])
            w[rng.randrange(len(w))] = rng.choice(inv)
            toks.append(w)
        else:
            toks.append(list(alone[i]))
        i += 1
    return alone, toks


def test_the_pruned_search_pairs_exactly_as_the_full_one():
    mv = _load()
    rng = random.Random(20261023)
    for _ in range(40):
        alone, toks = _line(rng, rng.randint(5, 30))
        n, m = len(alone), len(toks)
        full_best, full_back = mv._pair_dp(alone, toks, float("inf"))
        bound = mv._pair_bound(alone, toks)
        best, back = mv._pair_dp(alone, toks, bound)
        assert best[n][m] == full_best[n][m]
        assert _path(back, n, m) == _path(full_back, n, m)


def test_a_long_line_does_a_small_part_of_the_work(monkeypatch):
    mv = _load()
    alone, toks = _line(random.Random(7), 140)          # the case brief was 149 words
    calls = {"n": 0}
    grow = mv._grow

    def counted(*a):
        calls["n"] += 1
        return grow(*a)

    monkeypatch.setattr(mv, "_grow", counted)
    mv._pair_dp(alone, toks, float("inf"))
    full = calls["n"]
    calls["n"] = 0
    mv._pair_dp(alone, toks, mv._pair_bound(alone, toks))
    assert calls["n"] * 10 < full, (calls["n"], full)   # measured 15x on this noisy line; prose is far more


def test_on_by_default_and_the_kill_is_the_full_search():
    assert _load().LINE_PRUNE is True
    assert _load(HOUSE_MOUTH_PHONES_PRUNE="0").LINE_PRUNE is False


@pytest.mark.skipif(_load()._espeak_cmd() is None, reason="no espeak-ng on this machine")
def test_a_real_line_pairs_the_same_either_way():
    words = ("in gideon versus wainwright decided on 20260913 at 9:30 for the court held that counsel "
             "is fundamental and in the states it binds").split()
    on = _load()
    on._LINE_CACHE.clear()
    pruned = on.espeak_line(words)
    off = _load(HOUSE_MOUTH_PHONES_PRUNE="0")
    off._LINE_CACHE.clear()
    assert pruned is not None and pruned == off.espeak_line(words)
