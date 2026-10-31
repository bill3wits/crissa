"""Setup's krew link opens in the person's own browser (the User 2026-10-29: "let repo link offer in User browser"):
the player opens the configured address only, with the default browser; the page falls back to its own link."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_init as zi  # noqa: E402


def test_the_player_opens_the_configured_address_only(monkeypatch):
    monkeypatch.setattr(zi, "krew_url", lambda: "https://github.com/example/krew")
    seen = []
    got = zi.open_krew_link(opener=lambda u: seen.append(u) or True)
    assert got == {"ok": True, "url": "https://github.com/example/krew"} and seen == ["https://github.com/example/krew"]
    monkeypatch.setenv("CRISSA_KREW_LINK_DEFAULT_BROWSER", "0")
    assert zi.open_krew_link(opener=lambda u: seen.append(u) or True)["ok"] is False and len(seen) == 1


def test_no_address_opens_nothing_and_the_action_ignores_the_page(monkeypatch):
    monkeypatch.setattr(zi, "krew_url", lambda: "")
    assert zi.open_krew_link(opener=lambda u: True)["ok"] is False
    src = (ROOT / "lib" / "crissa_init.py").read_text(encoding="utf-8")
    assert 'if act == "open_krew":' in src and "return open_krew_link()" in src     # no url from the body
    page = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")
    assert 'JSON.stringify({ action: "open_krew" })' in page and 'window.open(v.krew_url, "_blank", "noopener")' in page
