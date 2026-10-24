"""The card beside her (2026-10-06): helpers put words, art, pages and web addresses on the Wall through the player's
/v1/screen - never the Wall's files, browser or modules."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_screen as zs  # noqa: E402


def test_fields_merge_clear_and_read_back_whole():
    s = zs.Screen(clock=lambda: 5.0)
    long_doc = "line\n" * 5000
    code, out = s.apply({"doc": long_doc, "doc_title": "Needs you", "by": "krew", "colour": "red"})
    assert code == 200 and out["screen"]["doc"] == long_doc, "nothing is shortened"
    assert out["ignored"] == ["colour"] and out["screen"]["by"] == "krew" and out["screen"]["up"]
    s.apply({"art": "/\\\n\\/", "art_label": "kite"})
    assert set(k for k in s.view() if k in zs.FIELDS) == {"doc", "doc_title", "art", "art_label"}
    s.apply({"clear": ["doc", "doc_title"]})
    assert "doc" not in s.view() and s.view()["art"] == "/\\\n\\/"
    s.apply({"clear": "all"})
    assert not s.view()["up"] and s.view()["at"] == 5.0


def test_clear_keeps_the_news_line_and_the_weather():
    s = zs.Screen()
    s.card["headline"] = "AI story"
    s.card["note"] = "via MIT"
    s.card["weather"] = "Overcast 78"
    s.card["notes"] = "a card"
    code, out = s.apply({"clear": ["headline", "note", "weather", "notes"],
                         "headline": None, "weather": ""})
    assert code == 200 and out["kept"] == ["headline", "note", "weather"]
    assert s.view()["headline"] == "AI story" and s.view()["weather"] == "Overcast 78"
    assert "notes" not in s.view()
    code, out = s.apply({"clear": "all"})
    assert "headline" not in out.get("kept", []) and s.view()["headline"] == "AI story"
    assert not any(k in zs.FIELDS for k in ("headline", "note", "weather"))
    code, out = s.apply({"headline": "a new story", "notes": "x"})
    assert code == 200 and out["ignored"] == ["headline"] and "kept" not in out, \
        "a real value is not kept on the player's card, so the caller is told it did not land"
    assert s.view()["headline"] == "AI story"


def test_bad_input_changes_nothing():
    s = zs.Screen()
    s.apply({"notes": "keep me"})
    for body in ({"url": "file:///etc/passwd"}, {"url": "javascript:alert(1)"}, {"notes": {"x": 1}},
                 {"clear": 3}, ["not", "an", "object"]):
        code, out = s.apply(body)
        assert code == 400 and not out["ok"], body
    assert s.view()["notes"] == "keep me"
    assert s.apply({"url": "https://example.com/x"})[0] == 200


def test_the_player_routes_follow_the_loopback_rule_and_the_page_draws_the_card():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    post = src.split('            # THE CARD BESIDE HER', 1)[1][:500]
    assert "connect_origin_ok(self.headers)" in post and "SCREEN.apply(body)" in post
    assert 'if head == "/v1/screen":' in src
    assert '"screen_at": SCREEN.at' in src
    page = (ROOT / "display" / "crissa-wall.html").read_text()
    card = page.split("async function refreshCard", 1)[1].split("\nasync function refreshAir", 1)[0]
    assert 'frame.setAttribute("sandbox", "")' in card and "srcdoc = c.html" in card, "a page runs no scripts"
    assert "textContent = c[k]" in card and "pre.textContent = c.art" in card, "words are text, never markup"
    assert "innerHTML" not in card
    assert 'clear: "all", by: "wall"' in card
