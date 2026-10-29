"""Connect's kinds (the User 2026-10-10): Local, Claude API and xAI fill the address and the model, the key stays the
user's; one table in lib/crissa_agent.py and the page; a Claude model is asked without a temperature (Claude 4.7 and
later return a 400 for any value below 1 on Anthropic's OpenAI-compatible endpoint, its docs on 2026-10-10)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_agent as za  # noqa: E402

PAGE = (ROOT / "display" / "crissa-connect.html").read_text(encoding="utf-8")


def test_the_page_offers_the_three_kinds_and_fills_url_and_model_only():
    for v in ("local", "claude", "xai", "openai-compatible", "none"):
        assert f'<option value="{v}">' in PAGE, v
    js = json.loads(re.search(r"const KINDS = (\{.*?\});\n", PAGE).group(1))
    # round 17 (the Claude preset): the page also carries the Claude kind's model list and names; never the label
    assert js == {k: {kk: vv for kk, vv in v.items() if kk != "label"} for k, v in za.KINDS.items()}
    handler = PAGE[PAGE.index('$("preset").addEventListener("change"'):][:300]
    assert '$("url").value = k.url' in handler and '$("model").value = k.model' in handler and '"key"' not in handler


def test_the_kinds_are_real_endpoints_and_models_and_the_server_accepts_them():
    # round 17 (the User 2026-10-13, "add the Claude preset"): Haiku 5.5, the one for a voice avatar (was Fable 5.1)
    assert za.KINDS["claude"]["url"] == "https://api.anthropic.com/v1" and za.KINDS["claude"]["model"] == "claude-haiku-5-5"
    assert za.KINDS["xai"]["url"] == "https://api.x.ai/v1" and za.KINDS["xai"]["model"] == "grok-4.20-0309-non-reasoning"
    assert za.KINDS["local"] == {**za.KINDS["local"], "url": "http://127.0.0.1:8000/v1", "model": "ops"}
    for name, k in za.KINDS.items():
        assert not re.search(r"your[-_ ]model|model[-_ ]name|example", k["model"] + k["url"], re.I), name
        assert za.validate({"preset": name, "url": k["url"], "model": k["model"]}) is None, name


class _Resp:
    headers = {"Content-Type": "application/json"}

    def __init__(self):
        self.body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sent(monkeypatch, cfg):
    seen = {}

    def fake(req, timeout, what):
        seen["body"] = json.loads(req.data)
        seen["headers"] = dict(req.header_items())
        return _Resp()
    monkeypatch.setattr(za.crissa_egress, "urlopen", fake)
    assert za.ask("hello", cfg) == "ok"
    return seen


def test_a_claude_model_is_asked_without_a_temperature_and_others_keep_theirs(monkeypatch):
    c = _sent(monkeypatch, {"url": "https://api.anthropic.com/v1", "model": "claude-fable-5-1", "key": "k", "preset": "claude"})
    assert "temperature" not in c["body"] and c["headers"].get("Authorization") == "Bearer k"
    s = _sent(monkeypatch, {"url": "http://127.0.0.1:18401/v1", "model": "claude-sonnet-5-5", "key": "k"})   # a stand-in
    assert "temperature" not in s["body"]
    x = _sent(monkeypatch, {"url": "https://api.x.ai/v1", "model": "grok-4.7", "key": "k", "preset": "xai"})
    assert x["body"]["temperature"] == 0.4 and x["body"]["stream"] is True
