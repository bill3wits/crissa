"""The lap runs on a glass with no zoom of its own (2026-10-27, the kit on +150: GET /v1/house is 404 on its player)."""
import importlib.util
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_zoom_get_on_a_glass_without_v1_house(monkeypatch):
    sys.path.insert(0, str(ROOT / "lib"))
    spec = importlib.util.spec_from_file_location("xspaces_live_script", ROOT / "scripts" / "xspaces-live.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def boom(*a, **k):
        raise urllib.error.HTTPError("http://x/v1/house", 404, "Not Found", {}, None)
    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    assert mod.zoom_get() == (False, 1.0)
