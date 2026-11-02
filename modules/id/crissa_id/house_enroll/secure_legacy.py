"""Register's old way into the camera under Secure: it clicked the lamp off first (and released a keyboard hold).

Used only while HOUSE_REGISTER_TOKEN is off. With the token on, Register never names or touches the lamp
(register_camera). Delete this module when the switch is armed for good (the User 2026-10-02, Init step 4).
"""
from __future__ import annotations

import json
from typing import Any, Callable


def drop_secure(PRESENCE: str, _post: Callable[..., dict]) -> dict[str, Any]:
    """Register is the User's own press: Secure comes OFF first (the User
    2026-09-11: "it should have dropped Secure"). Under Secure the door
    watch homes the camera by force, lease or no lease — no desk still is
    possible. Click-off is immediate (visit_lamp op=click on a Secure lamp).
    The User clicks Secure back on afterwards; the routine never re-arms it."""
    try:
        import urllib.request as _ur

        with _ur.urlopen(f"{PRESENCE}/v1/visit_lamp", timeout=3) as r:
            lamp = json.loads(r.read().decode() or "{}")
    except Exception as e:
        return {"ok": False, "error": repr(e)[:100]}
    info = lamp.get("visit_lamp_info") if isinstance(lamp.get("visit_lamp_info"), dict) else {}
    out: dict[str, Any] = {"ok": True, "was_secure": bool(info.get("secure")), "was_held": bool(info.get("hold"))}
    if info.get("hold"):
        # Secure mode cannot override an AUTH command (the User 2026-09-11): the
        # keyboard hold goes first, then Secure, then the camera leaves the door
        rel = _post(f"{PRESENCE}/v1/visit_lamp", {"op": "release"}, timeout=8)
        print(f"  secure HOLD released for the register → {str(rel)[:100]}", flush=True)
        out["release"] = rel
    if info.get("secure"):
        clk = _post(f"{PRESENCE}/v1/visit_lamp", {"op": "click"}, timeout=8)
        print(f"  secure was ON — clicked off for the register → {str(clk)[:100]}", flush=True)
        out["click"] = clk
        out["ok"] = bool(clk.get("ok", True))
    return out
