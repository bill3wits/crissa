"""Your phone, as a QR card (round 36, the User on .101 2026-10-20, ASK-20261020-0230 point 3; open since round 29).

The menu's "Your phone" used to open the phone page in a phone-shaped window on this computer (a second Crissa on the
same screen). Now it shows a card: a QR code the phone scans, the phone address on the same Wi-Fi, and the ufw line for
when the phone cannot reach it. The User's ruling (2026-10-20): the phone takes the HOUSE connection only when it came
from this card, so the QR carries a hand-off - the phone server mints a single-use token (POST /api/handoff, loopback
only; crissa-3e owns it) and the card encodes the address it answers with. A phone server that does not answer
gives the bare address and the ufw line, no QR. The QR is drawn by segno when it is installed, else the card says so.

The player serves the card at GET /phone-card (lib/crissa_menu.phone_view gives the address and the ufw line).
"""
from __future__ import annotations

import html
import os
import json
import urllib.request

import crissa_phone_firewall as _fw

HANDOFF_TIMEOUT_S = 2.0
# What the card says to run when segno is missing (the User 2026-10-20, "I would like to include the QR code"): the
# README's .venv; the launcher installs it there itself at start (scripts/crissa.py EXTRA_PACKAGES).
SEGNO_INSTALL = ".venv/bin/pip install segno==1.6.6"


def handoff(phone_base: str, timeout: float = HANDOFF_TIMEOUT_S) -> dict:
    """The phone server's hand-off for this card: {"url", "expires", ...}, or {} when it does not answer."""
    if not phone_base:
        return {}
    req = urllib.request.Request(phone_base.rstrip("/") + "/api/handoff", method="POST",
                                 data=json.dumps({"by": "wall"}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
    except Exception:  # noqa: BLE001  an older server (404) or none: the bare address
        return {}
    return d if isinstance(d, dict) and d.get("ok") and str(d.get("url") or "").startswith("http") else {}


def qr_svg(text: str) -> str:
    """The QR as inline SVG, '' when segno is not installed here."""
    import importlib
    importlib.invalidate_caches()                  # a segno installed while the player runs draws on the next open
    try:
        import segno
    except ImportError:
        return ""
    import io
    buf = io.BytesIO()                             # segno writes its SVG as bytes
    segno.make(text, error="m", micro=False).save(buf, kind="svg", scale=8, border=4, dark="#000", light="#fff",
                                                  xmldecl=False, svgns=True)
    return buf.getvalue().decode("utf-8")


def card(view: dict | None, hand: dict | None = None) -> dict:
    """What the card shows: {"ok", "address", "how", "qr_text", "qr", "why"}."""
    v = view if isinstance(view, dict) else {}
    out = {"ok": False, "address": str(v.get("url") or ""), "how": str(v.get("how") or ""), "qr_text": "", "qr": "",
           "why": ""}
    if not v:
        out["why"] = "There is no phone module on this computer."
        return out
    if not out["address"]:
        out["why"] = str(v.get("label") or "This computer is on no network.")
        return out
    h = hand if isinstance(hand, dict) else {}
    if not h:
        out["why"] = "The phone server is not answering, so there is no code to scan; open the address on the phone."
        return out
    out["qr_text"] = str(h["url"])
    out["qr"] = qr_svg(out["qr_text"])
    if not out["qr"]:
        out["why"] = ("The QR code needs segno on this computer (%s, then open Your phone again): a phone gets in "
                      "only from the code." % SEGNO_INSTALL)
        return out
    out["ok"] = True
    return out


# THE CODE CLOSES (round 38). Kill CRISSA_PHONE_CARD_CLOSE_S=0 (open until the window closes, round 36).
CLOSE_S = 120.0
_CLOSE_JS = ("<script>(function(){var done=false;function shut(on){if(done)return;done=true;"
             "var q=document.getElementById('qr');if(q)q.remove();var c=document.getElementById('closes');"
             "if(c)c.textContent=on?'Your phone is on. This code is closed.':"
             "'This code has closed. Open Your phone in Crissa\\'s menu for a fresh one.';"
             "setTimeout(function(){try{window.close()}catch(e){}},on?2500:0);}"
             "setTimeout(function(){shut(false)},%(ms)d);"
             "setInterval(function(){fetch('/phone-card?state=1',{cache:'no-store'}).then(function(r){return r.json()})"
             ".then(function(j){if(j&&j.active)shut(true)}).catch(function(){})},3000);})();</script>")


# The keys' presses: one POST each (Allow -> /v1/phone/allow, Remove -> /v1/phone/remove), the answer written under
# it; a success swaps the keys (the page reloads its own card), and anything else of ours open on the network is named.
_FW_JS = ("<script>(function(){var s=document.getElementById('fwsaid');"
          "document.querySelectorAll('button[data-fw]').forEach(function(b){b.addEventListener('click',function(){"
          "b.disabled=true;s.textContent='Waiting for the password prompt...';"
          "fetch(b.getAttribute('data-fw'),{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})"
          ".then(function(r){return r.json()}).then(function(j){if(j&&j.ok){var x=(j.exposed||[]).map(function(e){"
          "return e.program+' on port '+e.port}).join(', ');s.textContent=(b.getAttribute('data-fw').indexOf('remove')>0"
          "?'Done: phone access is removed.':'Done: your phone can open Crissa now. Scan the code again.')+(x?' Also"
          " open on the network: '+x+'.':'');setTimeout(function(){location.reload()},2500);}"
          "else{s.textContent=(j&&j.said)||'Not changed.';b.disabled=false;}})"
          ".catch(function(){s.textContent='Not changed.';b.disabled=false;});});});})();</script>")


def firewall_html(fw: dict, ufw: str = "") -> str:
    """The Allow / Remove keys for the card (lib/crissa_phone_firewall.plan), '' when neither is offered."""
    e = html.escape
    fw = fw if isinstance(fw, dict) else {}
    rm = fw.get("remove") if isinstance(fw.get("remove"), dict) else {}
    out = []
    if fw.get("offer"):
        # THE KEY (the User 2026-10-26, "Can we run that when they click it? Many will not know how"; 10-28 measured):
        # it names the network it opens, runs exactly this rule through the desktop's password prompt; the line stays
        # ONE NOTE (the User 2026-10-29, "we could combine the two notes"): the password, the rule and the terminal way
        out.append("<p class=how><b>First time?</b> Let your phone in on this computer, once. This opens the phone port"
                   " only, and only to your <b id=fwwhere>%s</b>:</p>"
                   "<p><button id=fwkey type=button data-fw='/v1/phone/allow'>%s</button></p>"
                   "<p class=how>It asks for your login password and adds this one rule (no password prompt here? run it"
                   " in a terminal): <code id=fwrule>%s</code></p>"
                   % (e(fw.get("where") or ""), e(fw.get("label") or _fw.LABEL), e(fw.get("rule") or ufw or "")))
    if rm.get("offer"):
        out.append("<p class=how id=fwallowed>%s</p><p><button id=fwremove type=button data-fw='/v1/phone/remove'>%s"
                   "</button></p>" % (e(("Your phone is allowed on your %s." % (fw.get("where") or rm.get("where")))
                                         if rm.get("here") else
                                         ("Phone access is allowed on another network (%s), not the one this computer"
                                          " is on now." % rm.get("where"))), e(_fw.REMOVE_LABEL)))
    if not out:
        return ""
    out.append("<p class=how id=fwsaid></p>")
    out.append(_FW_JS)
    return "".join(out)


def close_s(env: dict | None = None) -> float:
    e = os.environ if env is None else env
    try:
        return max(0.0, float(e.get("CRISSA_PHONE_CARD_CLOSE_S", CLOSE_S)))
    except ValueError:
        return CLOSE_S


def page(c: dict) -> str:
    """The card as one small page (her own window opens it)."""
    e = html.escape
    qr = c.get("qr") or ""
    parts = ["<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
             "<title>Your phone</title>",
             "<style>body{font:16px system-ui,sans-serif;margin:24px;background:#fff;color:#111;text-align:center}"
             "svg{width:min(320px,80vw);height:auto}code{background:#f2f2f2;padding:2px 4px;border-radius:3px}"
             ".how{max-width:36em;margin:12px auto;text-align:left;color:#333}</style>",
             "<h1>Your phone</h1>"]
    if c.get("how") and "ufw" in c["how"]:
        # round 37 (.101 round 36 B): on a fresh install the first scan fails silently until the phone is let in, so
        # that step comes first
        ufw = c["how"].split("If it does not open:", 1)[-1].strip()
        keys = firewall_html(c.get("firewall"), ufw)
        if keys:
            parts.append(keys)
        else:
            parts.append("<p class=how><b>First time?</b> Let your phone in on this computer, once: <code id=ufw>%s</code>"
                         "</p>" % e(ufw))
    if qr:
        parts.append("<p>Scan this with your phone's camera. It opens Crissa there, on this computer's connection.</p>")
        parts.append("<div id=qr>%s</div>" % qr)
    elif c.get("why"):
        parts.append("<p id=why>%s</p>" % e(c["why"]))
    if c.get("address"):
        parts.append("<p>Or open <code id=address>%s</code> on your phone (same Wi-Fi).</p>" % e(c["address"]))
    if qr and close_s() > 0:
        # round 38 (the User: "can we close QR after a period?"): the code closes once a phone is on, or after
        # CLOSE_S, whichever comes first; one line says how to get a fresh one
        parts.append("<p class=how id=closes>This code closes in %d minutes or once your phone is on.</p>"
                     % max(1, round(close_s() / 60.0)))
        parts.append(_CLOSE_JS % {"ms": int(close_s() * 1000)})
    return "".join(parts)
