"""Round 41 (.101 round 40 C1): xAI's 403 for used-up credits / the monthly spending limit was said as "check the API
key", and agent-test.json kept no reason. It is said as out of credits, with the provider's own message."""
import io
import json
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_agent as za  # noqa: E402

XAI = {"url": "https://api.x.ai/v1", "model": "grok-4.20", "key": "xai-abc"}
BODY = json.dumps({"code": "permission-denied", "error": "Your team abc has either used all available credits or "
                   "reached its monthly spending limit. To continue making API requests, please purchase more credits "
                   "or raise your spending limit."})


def _err(code, body):
    return urllib.error.HTTPError("https://api.x.ai/v1/chat/completions", code, "x", {}, io.BytesIO(body.encode()))


def test_a_credits_403_is_out_of_credits_with_the_providers_words(monkeypatch):
    monkeypatch.delenv("CRISSA_CONNECT_BILLING", raising=False)
    e = za.plain_error(_err(403, BODY), XAI)
    msg = str(e)
    assert msg.startswith(za.CREDITS_PLAIN) and "console.x.ai, Billing" in msg
    assert "used all available credits" in msg and "check the API key" not in msg


def test_a_bad_key_is_still_a_bad_key(monkeypatch):
    monkeypatch.delenv("CRISSA_CONNECT_BILLING", raising=False)
    e = za.plain_error(_err(400, json.dumps({"code": "invalid-argument", "error": "Incorrect API key provided"})), XAI)
    assert str(e).startswith(za.KEY_PLAIN)
    assert str(za.plain_error(_err(401, "{}"), XAI)).startswith(za.KEY_PLAIN)


def test_the_kill_says_it_as_before(monkeypatch):
    monkeypatch.setenv("CRISSA_CONNECT_BILLING", "0")
    assert str(za.plain_error(_err(403, BODY), XAI)).startswith(za.KEY_PLAIN)


def test_the_record_keeps_the_reason_never_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_CONNECT_BILLING", raising=False)
    msg = str(za.plain_error(_err(403, BODY), XAI))
    rec = za.record_test(XAI, {"ok": False, "error": msg, "ms": 63}, home=tmp_path)
    on_disk = json.loads((tmp_path / "agent-test.json").read_text())
    assert rec["error"].startswith(za.CREDITS_PLAIN) and on_disk["error"] == rec["error"]
    assert "xai-abc" not in (tmp_path / "agent-test.json").read_text()


def test_provider_message_shapes():
    assert za.provider_message('{"error": {"message": "You exceeded your current quota"}}') == \
        "You exceeded your current quota"
    assert za.provider_message("plain text\n  body") == "plain text body"
