"""Secure token — pure rules (secure_token.py), no camera."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence import secure_token as stok  # noqa: E402


def test_mint_reuses_the_id_across_a_bounce():
    a = stok.mint("user", now=1.0, reason="click", token_id=None)
    b = stok.mint("user", now=2.0, reason="restart_token", hold=False, token_id=a["id"])
    assert a["id"] == b["id"] and len(a["id"]) == 32
    assert b["uid"] == "user" and b["ts"] == 2.0


def test_decide_reauths_only_the_users_token():
    tok = stok.mint("user", now=1.0, reason="click", token_id="aa")
    assert stok.decide(tok, "user")["act"] == "reauth"
    assert stok.decide(tok, "wanda")["act"] == "guest"
    assert stok.decide(None, "user")["act"] == "guest"
    assert stok.decide({"uid": "user"}, "user")["act"] == "guest"  # no id
    hold = stok.mint("user", now=1.0, reason="keyboard_hold", hold=True, token_id="bb")
    assert stok.decide(hold, "user")["hold"] is True
