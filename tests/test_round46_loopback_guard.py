"""Round 46 (the local review of kit crissa-063a2813, the launch blocker): the player's 10-07 loopback guard, one rule
for every server of a pack (lib/crissa_guard.py), with a byte-equal copy in each module that installs on its own. This
file holds the rule and the copies; each server's own test proves it is wired (tests/test_round46_*)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_guard as G  # noqa: E402

COPIES = ["modules/presence/crissa_presence/guard.py", "modules/ear/crissa_ear/guard.py",
          "modules/ear/crissa_bridge/guard.py", "modules/id/crissa_id/guard.py",
          "modules/voice/crissa_voice/guard.py", "modules/oracle/crissa_oracle/guard.py"]


def test_every_copy_is_the_rule_byte_for_byte():
    want = (ROOT / "lib" / "crissa_guard.py").read_bytes()
    for rel in COPIES:
        assert (ROOT / rel).read_bytes() == want, rel


def test_the_rule():
    ok = {"Host": "127.0.0.1:8192"}
    assert G.foreign_request(ok) == "" and G.foreign_request({}) == ""                      # curl, the launcher
    assert G.foreign_request({"Host": "evil.example:8192"}) == "host"                       # DNS rebinding
    assert G.foreign_request({"Host": "[::1]:8192", "Origin": "http://localhost:8100"}) == ""
    assert G.foreign_request(dict(ok, Origin="https://evil.example")) == "origin"           # a cross-site POST / fetch
    assert G.foreign_request(dict(ok, Origin="null")) == "origin"
    assert G.foreign_request(dict(ok, **{"Sec-Fetch-Site": "cross-site"})) == "cross-site"  # an <img> on another site
    assert G.foreign_request(dict(ok, **{"Sec-Fetch-Site": "cross-site", "Referer": "http://127.0.0.1:8099/"})) == ""
    assert G.foreign_request({"Host": "crissa.local:8226"}, names=("crissa.local",)) == ""


def test_allow_origin_is_never_a_star():
    assert G.allow_origin({"Origin": "http://127.0.0.1:8099"}) == "http://127.0.0.1:8099"
    assert G.allow_origin({"Origin": "https://evil.example"}) == "" and G.allow_origin({}) == ""


def test_body_length_refuses_negative_and_oversize():
    assert G.body_length({}) == 0 and G.body_length({"Content-Length": "12"}) == 12
    assert G.body_length({"Content-Length": "-1"}) is None and G.body_length({"Content-Length": "x"}) is None
    assert G.body_length({"Content-Length": "100"}, cap=99) is None


def test_the_kill():
    assert G.guard_on({}) is True and G.guard_on({"CRISSA_LOOPBACK_GUARD": "0"}) is False
    assert G.guard_on({}, default="0") is False


class _H:
    def __init__(self, headers):
        self.headers, self.sent, self.wfile = headers, [], self

    def send_response(self, c):
        self.sent.append(c)

    def send_header(self, k, v):
        self.sent.append((k, v))

    def end_headers(self):
        pass

    def write(self, b):
        self.sent.append(b)


def test_check_refuses_with_403_and_no_cors():
    h = _H({"Host": "127.0.0.1", "Origin": "https://evil.example"})
    assert G.check(h, env={}) is False and h.sent[0] == 403
    assert not any(isinstance(x, tuple) and x[0] == "Access-Control-Allow-Origin" for x in h.sent)
    assert G.check(_H({"Host": "127.0.0.1"}), env={}) is True
    assert G.check(h, env={"CRISSA_LOOPBACK_GUARD": "0"}) is True


def test_a_refused_connection_closes():
    """The refused request's body is never read, so the connection must not be kept alive for a next request (a raw
    HTTP/1.1 client with a foreign Host waited on an open connection forever)."""
    h = _H({"Host": "x", "Content-Length": "5"})
    assert G.check(h, env={}) is False
    assert h.close_connection is True and ("Connection", "close") in h.sent
