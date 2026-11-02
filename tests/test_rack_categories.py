"""A new library's bare category ids do not crash the clip menu (the User 2026-10-16: "fix the new library menu
crash" - /v1/kit raised on `dict("formal")` for every library intake-house or new-library made)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HC = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def _fn():
    ns: dict = {}
    exec(HC[HC.index("DEFAULT_CATEGORIES = ("):HC.index("def rack_plate(")], ns)
    return ns["rack_category"]


def test_a_bare_id_is_its_default_category():
    rc = _fn()
    assert rc("formal")["label"] == "Formal" and rc("casual")["secure"] is True
    assert rc("studio") == {"id": "studio", "label": "Studio", "sub": ""}


def test_a_dict_passes_and_junk_is_dropped():
    rc = _fn()
    assert rc({"id": "x", "label": "X"}) == {"id": "x", "label": "X"}
    assert rc({"label": "no id"}) is None and rc("") is None and rc(3) is None


def test_the_menu_reads_through_it():
    assert "[rack_category(c) for c in cats if rack_category(c)]" in HC and "[dict(c) for c in cats]" not in HC
