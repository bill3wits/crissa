"""The Crissa key's menu (the User 2026-10-10, after the first real-hardware test on .101: the kit "looks great but
lacks features to operate it").

One small key, always on screen, on the desk Wall (Tk, lib/house_wall_ascii) and on the browser Wall (/wall,
display/crissa-wall.html), opens the same menu. The player serves it, so both Walls say and do the same things:

    GET  /v1/menu                   what the menu shows now (view())
    POST /v1/menu {"action": ...}   does one thing (act()); this machine's own pages only, like Connect

    mic          her microphone on or off: the ear's one lock, the same key as Init's done plate and the Control
                 Panel's Mic (lib/crissa_init.mic); the item shows its state
    connect      the browser Connect page (/connect): the Local, Claude API and xAI presets
    panel        the Control Panel (the port table's panel)
    composer     Store… (the Crissa store's catalog, crissa_home.leave_url, 2026-10-12), or Composer… when it opens one
                 (the person's own CRISSA_COMPOSER_URL; crissa_home.leave_label). The desk
                 Wall's rail keys (Krew, Composer, Control Panel, Advanced) are Ubuntu dock launchers on the house and
                 a pack installs none, so the rail never shows in a pack (checked 2026-10-10 on the pack's own Wall:
                 "hot strip: none under Ubuntu's dock"): this menu carries what they did - Krew opened the Control
                 Panel there too. Advanced is the house's own glass settings.
    lip_sync     Lip sync (the User 2026-10-11, before the .101 demo): this machine's one timing dial, - / + in half film
                 frames, kept in <state home>/lip-sync.json, never in the library's mouth-dials.json (lib/crissa_lipsync);
                 {"action": "lip_sync", "step": +1|-1 | "halves": k | "lead_ms": N | "reset": true}. The view carries
                 the value, the reachable range (the library's own lead inside the line clamp) and a test line for
                 /v1/speak. The player hands its live library lead in (lead_base_ms)
    voice_id     Voice ID again: Init's own Voice ID step, alone, for the User (lib/crissa_init.start_voice_id); its plate
                 shows on both Walls. With no voice print yet it runs at once (nothing to protect). Once the User has
                 prints, it needs proof it is the User: Crissa sees the User's face (presence's rule since 2026-10-03),
                 or the person types the computer's login password, checked by presence's own PAM path (its Secure
                 hold, {"action": "voice_id", "password": ...}; used once, never stored or logged). An unverified
                 speaker never adds to or replaces the User's prints (the User 2026-10-10, .101 round 4 DD)
    add_visitor  ADD A VISITOR (round 20, the User 2026-10-14: "We need to for added visitors"): the visitor's name (required),
                 then the same Voice ID (counts with a face still on each, then Face photos for an angle still missing)
                 for a NEW person kept as a visitor - labels only, never the User (lib/crissa_init start_visitor).
                 {"action": "add_visitor", "name": ...}. The face rule applies (Security On: the User's face first;
                 ID-only / Off: anyone at the computer). The view's `visitors` lists them;
    visitor_remove  {"action": "visitor_remove", "person_id": ...} forgets one (prints and stills). Kill:
                 CRISSA_ADD_VISITOR=0 (neither item)
    voice_id     ONE BUTTON (round 21, the User 2026-10-14, .101 round 20: "Voice ID and Add a visitor become one button";
      who        "Only the User can register them, but never as the User"): "Voice ID / add a person…" asks "Is this you,
                 or someone new?". {"action": "voice_id", "who": "me"} is the User's own Voice ID (its proof as above);
                 {"action": "voice_id", "who": "new", "name": ...} adds a visitor, and so does add_visitor. Adding or
                 removing a person needs the User in EVERY mode (person_proof: his face at the camera, his voice named by
                 the ear in the last VOICE_PROOF_S, or the login password), never only under Security On. Both run the
                 one Voice ID routine (lib/crissa_init ONE VOICE ID ROUTINE). Visitors… stays. Kill: CRISSA_ONE_VOICE_ID=0
                 (round 20's two items and rule)
    help         the keys (Ctrl+Q closes the desk Wall, F11 leaves its full screen), how to stop, how to start again
    wall_close   Close the Wall / Open the Wall (2026-10-10): the desk Wall closed on purpose stays closed - also across
    wall_open    launcher starts - until it is opened again (lib/crissa_watch CLOSED); the Control Panel has both keys.
                 Since 2026-10-12 the menus offer only Open the Wall (when it is closed): their close is Close Agent
    agent_close  CLOSE AGENT / OPEN AGENT (the User 2026-10-12: "Close Agent vs OS off ... make them match wherever
    agent_open   possible"): Crissa the agent goes - she stops listening and speaking (her ear and voice lanes stop and
                 stay stopped, lib/crissa_watch AGENT) and her window closes - while the desk Wall stays as the desktop,
                 the OS. Recorded like a closed Wall; Open Agent (the menus, the Control Panel, her dock icon) brings
                 her back. The watcher that keeps her Wall window open while the desk Wall is off stands down meanwhile
    stop         All OS Off (the house's own name for everything off: its Control Panel key and its Mode chip; this
                 item was "Stop Crissa" until 2026-10-12): everything of Crissa, cleanly, in both modes - the desk Wall,
                 her window, every lane or unit - a request to the launcher (lib/crissa_procs request_quit), which stops
                 its children (process mode) or its units (service mode) and exits 0, so its service does not start it
                 again until the next login. `systemctl --user stop 'crissa-*'` is the same from a terminal. The Walls
                 ask before they send it.
    fullscreen   the desk Wall's full screen, as it is NOW (the User 2026-10-10, .101 round 4 EE: the view said "Exit
                 full screen" while the Wall was the wallpaper): {"on", "label", "mode"} from the desk Wall's own note
                 (<home>/run/wall-screen.json, written at glass up and at each F11); the label is "Exit full screen"
                 only while it is full screen, and empty otherwise (a wallpaper Wall has no full screen). The browser
                 Wall labels its own key from the page's own state ("Full screen" / "Exit full screen")
    unavailable  what this computer cannot do, greyed with its reason (lib/crissa_capability, the same answer both
                 Control Panels show)
    security     SECURITY: ON / ID-ONLY / OFF (round 17, the User 2026-10-13: "Maybe then On, ID-only, Off"): who may
                 talk to her and act through her (lib/crissa_home.security_mode, <state home>/security.json; the ear
                 and the bridge read it on every turn, no restart). {"action": "security", "mode": on|id_only|off}.
                 The view: the mode, the label, each mode with its one line, whether it was chosen or is the default.
                 On: today's (only his voice and his face open User mode; Secure as before). ID-only, the default:
                 anyone at this computer talks to her, the speaker is named after each line, tools and house actions
                 need his face at the camera. Off: no identity checks. Secure greys outside On (presence's
                 fixed_cam.secure_offer)

Never on the house (crissa_home.is_house): the house has its own panels.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crissa_egress  # noqa: E402  every outbound open goes through the one switch
import crissa_home  # noqa: E402
import crissa_lipsync  # noqa: E402  this machine's lip sync (2026-10-11)
import crissa_ports  # noqa: E402

ROUTE = "/v1/menu"
ACTIONS = ("mic", "voice_id", "lip_sync", "security", "stop", "wall_close", "wall_open", "agent_close", "agent_open",
           "add_visitor", "visitor_remove", "face", "desk", "face_again")
ROOT = Path(__file__).resolve().parent.parent

# The words, one place for both Walls. NO "…" ON A LABEL (round 29, the User 2026-10-17 on .101: "Why are there three
# dots next to each menu item?"). Kill CRISSA_MENU_ELLIPSIS=1 (round 28's words).
LABELS = {
    "mic_on": "Microphone: on - turn it off",
    "mic_off": "Microphone: off - turn it on",
    "mic_none": "Microphone: the ear is not running",
    "connect": "Connect an agent",
    "connected": "Connected",
    "panel": "Control Panel",
    "composer": "Composer",
    "store": "Store",
    "voice_id": "Voice ID again",
    "voice_id_one": "Voice ID / add a person",
    "who_ask": "Is this you, or someone new?",
    "who_me": "It's me",
    "who_new": "Someone new",
    "name_ask": "Whose voice?",
    "add_visitor": "Add a visitor",
    "visitors": "Visitors",
    "lip_sync": "Lip sync",
    "security": "Security: %s",
    "help": "Help",
    "stop": "All OS Off",
    "fullscreen": "Exit full screen",
    "wall_close": "Close the Wall",
    "wall_open": "Open the Wall",
    "agent_close": "Close Agent",
    "agent_open": "Open Agent",
    "face": "Face only",
    "desk": "Back to her desk",
    "face_again": "Face photos again",
    "guests": "Edit guest list",
    "agent_only": "Agent only",
    "krew": "Krew",
    "skin": "Agent Look",    # the User 2026-10-31: the look (which outfit) is picked from the Menu; the agent from the CP
}
if os.environ.get("CRISSA_MENU_ELLIPSIS", "0").strip() == "1":
    LABELS.update({k: v + "\u2026" for k, v in LABELS.items() if k in (
        "connect", "panel", "composer", "store", "voice_id", "voice_id_one", "add_visitor", "visitors", "lip_sync",
        "security", "stop", "agent_close")})



def _skin_menu() -> bool:
    try:
        import crissa_avatar
        return crissa_avatar.menu_enabled()
    except Exception:  # noqa: BLE001
        return False

def agent_connected(home=None) -> bool:
    """An agent is connected (lib/crissa_agent: a saved, configured endpoint). Unreadable = not connected."""
    try:
        import crissa_agent
        return bool(crissa_agent.configured(crissa_agent.load(home)))
    except Exception:  # noqa: BLE001
        return False


def lan_ip() -> str:
    """This computer's address on its home network ('' when it has none): MEASURED from the default route's own
    connection (lib/crissa_homenet, the User 2026-10-28: a VPN is not home), else the route-source reading."""
    import crissa_homenet
    return crissa_homenet.home_ip()


def phone_view(home=None, ip=None) -> dict | None:
    """YOUR PHONE (round 29, the User 2026-10-17): when the phone module is installed and its packs are unpacked, the
    menu shows the address to open on the phone (same Wi-Fi), and the line that lets the phone in through ufw.
    None when there is no phone module here."""
    try:
        packs = Path(home or crissa_home.HOME) / "phone" / "packs"
    except Exception:  # noqa: BLE001
        return None
    if not (ROOT / "scripts" / "phone-app-server.py").is_file() or not packs.is_dir():
        return None
    try:
        import crissa_ports
        port = int(crissa_ports.port("phone"))
    except Exception:  # noqa: BLE001  no port table here: no address to show
        return None
    h = None
    if ip is None:
        import crissa_homenet
        h = crissa_homenet.home()
        ip = str(h["ip"]) if h.get("ok") else crissa_homenet.home_ip()
    if not ip:
        return {"label": "Your phone: this computer is on no network", "url": "", "how": ""}
    url = "http://%s:%d" % (ip, port)
    if h and h.get("ok"):
        # MEASURED (the User 2026-10-28, "Measure the network instead of assuming /24"): the real network on its own
        # connection - the same rule the firewall key runs (lib/crissa_phone_firewall.rule_line)
        rule = "sudo ufw allow in on %s from %s to any port %d proto tcp" % (h["dev"], h["net"], port)
    else:
        rule = "sudo ufw allow from %s to any port %d proto tcp" % (".".join(ip.split(".")[:3]) + ".0/24", port)
    out = {"label": "Your phone: %s" % url, "url": url,
           "how": "Open %s on your phone (same Wi-Fi). If it does not open: %s" % (url, rule)}
    try:                                           # round 36: the menu item opens the QR card (lib/crissa_phone_card)
        import crissa_phone_room
        if crissa_phone_room.card_on():
            out["card"] = crissa_ports.url("player") + "/phone-card"
    except Exception:  # noqa: BLE001
        pass
    return out


def connect_view(player: str, home=None) -> dict:
    """ONE CONNECT BUTTON (round 29, the User 2026-10-17 on .101: "make them ONE button - green Connected or
    Disconnect"): "Connected" (green on both Walls) when an agent is connected, else "Connect an agent"; either opens
    the Connect page, where Kind: Not connected disconnects her."""
    on = agent_connected(home)
    return {"url": player + "/connect", "label": LABELS["connected"] if on else LABELS["connect"], "connected": on}
STOP_ASK = ("All OS Off? Everything of Crissa stops: the Wall, her window, her voice and her ear - a plain desktop. "
            "Start her again with her dock icon or %s.")
AGENT_ASK = ("Close Agent? Crissa stops listening and speaking and her window closes; the Wall stays as your desktop. "
             "Open Agent (this menu, the Control Panel or her dock icon) brings her back.")


def _launcher_view(home: Path) -> dict:
    """How Crissa runs now: {"alive", "under": hand|systemd|launchd, "pid", "why"}."""
    out = {"alive": False, "under": "", "pid": None, "why": ""}
    try:
        import crissa_procs
        alive, why = crissa_procs.launcher_alive(crissa_procs.load(home))
        out.update(alive=bool(alive), why=why)
    except Exception as e:  # noqa: BLE001
        out["why"] = "%s" % type(e).__name__
    try:
        import crissa_service
        L = crissa_service.read_launcher(home)
        out.update(under=str(L.get("under") or ""), pid=L.get("pid"))
    except Exception:  # noqa: BLE001
        pass
    return out


def start_command(under: str = "", root: Path | None = None) -> str:
    """How to start Crissa again, as the person would type it."""
    if under == "systemd":
        return "systemctl --user start crissa-launcher (or log in again)"
    if under == "launchd":
        return "launchctl kickstart gui/$(id -u)/com.crissa.launcher (or log in again)"
    return "python3 scripts/crissa.py in %s" % str(root or ROOT)


def help_lines(under: str = "", root: Path | None = None) -> list:
    """Help: the keys and how to stop and start, for the way Crissa runs here."""
    lines = [
        "The Crissa key (bottom left of the Wall) opens this menu.",
        "On Linux the desk Wall is your desktop's background, under your windows, the dock and the top bar, unless "
        "you chose full screen (--fullscreen).",
        "Full screen: F11 leaves it (or Exit full screen in this menu); Ctrl+Q closes the Wall window when it has the "
        "keyboard - Crissa keeps running until you stop her.",
        "Close Agent (in this menu) stops her listening and speaking and closes her window; the Wall stays. "
        "All OS Off stops everything of Crissa cleanly.",
    ]
    if under == "systemd":
        lines.append("She runs as a service of your login: systemctl --user stop crissa-launcher also stops her.")
    elif under == "launchd":
        lines.append("She runs as a login agent: launchctl kill SIGTERM gui/$(id -u)/com.crissa.launcher also stops her.")
    else:
        lines.append("Started in a terminal: Ctrl-C there also stops her.")
    lines.append("Start her again: %s." % start_command(under, root))
    lines.append("Her settings, logs and voice prints are in %s." % crissa_home.HOME)
    return lines


def _ear_up(timeout: float = 0.4) -> bool:
    try:
        with crissa_egress.urlopen(crissa_ports.url("stt") + "/health", timeout, what="the menu: the ear") as r:
            return 200 <= int(getattr(r, "status", 200)) < 300
    except Exception:  # noqa: BLE001
        return False


def _user() -> dict:
    try:
        return json.loads((crissa_home.HOME / "user.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


VERBAL_NOTE = "Voice ID is starting: listen, and count to six at each beep."
VISITOR_NOTE = "Adding %s: they listen, and count to six at each beep."
VOICE_ID_PASSWORD = os.environ.get("CRISSA_VOICE_ID_PASSWORD", "1").strip().lower() not in ("0", "false", "no", "off")
# The password's hold lasts the take (2026-10-11); CRISSA_VOICE_ID_KEEP_HOLD=0 releases it at once, as before.
KEEP_HOLD = os.environ.get("CRISSA_VOICE_ID_KEEP_HOLD", "1").strip().lower() not in ("0", "false", "no", "off")
WHY_PROOF = ("Voice ID again needs to know it is you first, because your voice prints would change: sit in front of the "
             "camera until the Wall says User, or type this computer's login password.")
WHY_PROOF_NO_PW = ("Voice ID again needs Crissa to see it is you first: sit in front of the camera until the Wall says "
                   "User, then choose it again. With no camera or no face photos she cannot check, so your voice prints "
                   "stay as they are.")


def _voice_id_routine() -> str:
    """'verbal' (the spoken counts: the Walls start it at once, no page) or 'video' (the page in her window)."""
    try:
        import crissa_init
        return crissa_init.voice_id_routine()
    except Exception:  # noqa: BLE001
        return "verbal"


def voice_id_gate(presence=None) -> dict:
    """{"ok", "why", "password"}: can Voice ID run again now? A first boot with no User may, and so may a User with no
    voice print yet (Voice ID skipped or failed: nothing to protect). Once the User has prints, presence lends Register
    the camera only while she sees it is the User (its rule since 2026-10-03, unchanged here); `password` True says the
    computer's login password is the other way in (DD, 2026-10-10: with the face photos skipped or failed there was no
    way to redo Voice ID but Setup from the start). CRISSA_VOICE_ID_PASSWORD=0 takes the password way out."""
    try:
        import crissa_init
        st = crissa_init.load_state()
        if st.get("status") == "running":
            return {"ok": False, "why": "Setup is running: finish it there first."}
    except Exception:  # noqa: BLE001
        pass
    u = _user()
    if not u.get("person_id"):
        return {"ok": True, "why": ""}
    try:
        import crissa_init
        if crissa_init.voice_prints(str(u.get("person_id"))) < 1:
            # no voice print yet (Voice ID skipped or failed, the User 2026-10-10, .101 Y): nothing to protect, and with no
            # face print presence can never say it is the User, so asking for that would refuse it for good
            return {"ok": True, "why": ""}
    except Exception:  # noqa: BLE001
        pass
    if presence is None:
        presence = _presence()
    if presence.get("user_auth") or (presence.get("presence") or {}).get("user_auth"):
        return {"ok": True, "why": ""}
    if presence.get("_err"):
        # NOBODY CAN CHECK, SO NOBODY PASSES (DD): with presence down an unverified speaker would overwrite the User's
        # prints; the password goes through presence too, so it waits for presence as well
        return {"ok": False, "password": False,
                "why": "Voice ID again needs to know it is you, and the part of Crissa that checks (presence) is not "
                       "answering: start it again from the Control Panel, then choose Voice ID again."}
    if VOICE_ID_PASSWORD:
        return {"ok": False, "password": True, "why": WHY_PROOF}
    return {"ok": False, "password": False, "why": WHY_PROOF_NO_PW}


def one_routine() -> bool:
    """The one Voice ID routine and its one button (lib/crissa_init.one_voice_id_on; off on the house)."""
    try:
        import crissa_init
        return bool(crissa_init.one_voice_id_on())
    except Exception:  # noqa: BLE001
        return False


# ONLY THE USER ADDS OR REMOVES A PERSON, IN EVERY MODE (round 21, the User 2026-10-14: "Only the User can register them,
# but never as the User"). Round 20 let anyone at the computer add a visitor under ID-only and Off. Now the User proves
# it is him first, the same three ways his own Voice ID takes: his face at the camera now (presence's live face rule,
# with the face-match sequence's short wait while he turns to it), his voice - the ear's newest label naming him by
# voice at VOICE_PROOF_SIM (0.50, his floor) within VOICE_PROOF_S (30 s, round 20's face grace) - or this computer's
# login password (presence's PAM check, used once). Under Security On it is the same. Kill: CRISSA_ADD_NEEDS_USER=0
# (round 20: the face rule only under Security On).
VOICE_PROOF_S = float(os.environ.get("CRISSA_ADD_VOICE_PROOF_S", "30") or 30)
VOICE_PROOF_SIM = float(os.environ.get("CRISSA_ACT_VOICE_MIN_SIM", "0.50") or 0.50)
WHY_USER_ONLY = "Only you can add or remove a person: Crissa needs to see your face at the camera, or hear your voice."
WHY_USER_ONLY_PW = WHY_USER_ONLY + " Look at the camera and choose it again, or type this computer's login password."
WHY_USER_ONLY_NO_PW = WHY_USER_ONLY + " Look at the camera, or say something to her, then choose it again."


def add_needs_user_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_ADD_NEEDS_USER", "1")).strip().lower() not in ("0", "off", "false", "no")


# THE MENU'S PROOF CARRIES INTO THE RUN (round 41, .101 round 40 F3, the User: "once she says switch it is insane to ask
# for face again"): his proof on the menu screen (ids_proof) hands the desk Wall a token for that one run. The name box
# sends it back and start_ids takes it once: the page opens on the handover, and the person's Ready is the person's. No
# clock holds it (the User's no-limits rule). It is tied to the run: a new proof replaces it, the run's end or a cancel
# clears it, and a name box closed unsent takes the token with it. Kill CRISSA_IDS_PROOF_CARRY=0 (round 39: the run's
# Ready asks it again).
_IDS_PROOF: dict = {"token": "", "how": ""}


def ids_proof_carry_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_IDS_PROOF_CARRY", "1")).strip().lower() not in ("0", "off", "false", "no")


def hold_ids_proof(how: str) -> str:
    """Keep the menu's proof for the next run: its token ("" when the carry is off)."""
    if not ids_proof_carry_on():
        clear_ids_proof()
        return ""
    import secrets
    tok = secrets.token_hex(12)
    _IDS_PROOF.update(token=tok, how=str(how or "menu"))
    return tok


def check_ids_proof(token: str) -> bool:
    """The menu proof's token is the one held (not used up: the Edit guest list's Remove and Access, round 41 F2)."""
    held = str(_IDS_PROOF.get("token") or "")
    if not token or not held or not ids_proof_carry_on():
        return False
    import hmac
    return hmac.compare_digest(str(token), held)


def take_ids_proof(token: str) -> str:
    """The menu's proof ("face", "password", ...) when `token` is the one it handed out, used up; else ""."""
    held, how = str(_IDS_PROOF.get("token") or ""), str(_IDS_PROOF.get("how") or "")
    if not token or not held or not ids_proof_carry_on():
        return ""
    clear_ids_proof()
    import hmac
    return how if hmac.compare_digest(str(token), held) else ""


def clear_ids_proof() -> None:
    _IDS_PROOF.update(token="", how="")


def owner_voice_sim(owner: str, now: float | None = None, path: Path | None = None) -> float | None:
    """The ear's newest label naming `owner` by voice at VOICE_PROOF_SIM or better within VOICE_PROOF_S: its sim, else
    None (<state home>/ear/speaker-labels.jsonl, written by the ear's id trail; her own voice is never the speaker)."""
    import time as _t
    now = _t.time() if now is None else float(now)
    p = path or (crissa_home.HOME / "ear" / "speaker-labels.jsonl")
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16384))
            rows = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for raw in reversed(rows):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        try:
            at = float(d.get("at") or d.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if now - at > VOICE_PROOF_S:
            return None                           # older rows are older still
        if str(d.get("who") or "").strip().lower() != owner or d.get("via") not in ("voice", "voice+face"):
            continue
        try:
            sim = float(d.get("sim"))
        except (TypeError, ValueError):
            continue
        if sim >= VOICE_PROOF_SIM:
            return sim
    return None


def person_proof(presence: dict | None = None, *, password: str | None = None, wait: bool = True, fetch=None,
                 look=None, voice=None) -> dict:
    """Is it the User asking to add or remove a person? {"ok", "how", "why", "password"} - how: face | voice | password
    | rule_off. `wait` False (the menu's view): only what is known now, no face sequence and no password."""
    try:
        import crissa_init
        owner = crissa_init._owner_id()             # the User Init made (user.json in Init's own state home)
    except Exception:  # noqa: BLE001
        owner = crissa_home.owner_id()
    if not owner:
        return {"ok": False, "how": "", "why": "Run Setup first: Crissa adds people once she knows you.",
                "password": False}
    if not add_needs_user_on():
        return {"ok": True, "how": "rule_off", "why": "", "password": False}
    pres = presence if isinstance(presence, dict) else _presence()
    ok, _sim, _why = crissa_home.live_face(pres, owner)
    if ok:
        return {"ok": True, "how": "face", "why": "", "password": False}
    if (voice or owner_voice_sim)(owner) is not None:
        return {"ok": True, "how": "voice", "why": "", "password": False}
    if password and VOICE_ID_PASSWORD:
        # THE PASSWORD FIRST (the User 2026-10-26: "you are forcing FF before the User auth check"): a typed password
        # is his auth, proven now - never after a 5 s wait for his face
        proof = prove_with_password(str(password))
        password = None
        if proof.get("ok"):
            return {"ok": True, "how": "password", "why": "", "password": False}
        return {"ok": False, "how": "", "why": proof.get("error") or "That password was not accepted.",
                "password": True}
    if wait:
        got = crissa_home.face_sequence(fetch or _presence, uid=owner, presence=pres, look=look or _face_look)
        if got.get("ok"):
            return {"ok": True, "how": "face", "why": "", "password": False}
    return {"ok": False, "how": "", "why": WHY_USER_ONLY_PW if VOICE_ID_PASSWORD else WHY_USER_ONLY_NO_PW,
            "password": bool(VOICE_ID_PASSWORD)}


def prove_with_password(password: str, post=None, keep: bool = False) -> dict:
    """{"ok", "error"}: the computer's login password, through presence's own PAM check (POST /v1/visit_lamp op hold:
    a right password holds Secure, which is the User's auth there, so Register's camera token follows). The hold is
    released at once (Secure stays, droppable by a door face as any Secure is) - unless `keep`: then the caller
    releases it (release_proof) when the take it was for has ended. On a fixed camera the User's own face counts as a
    face at the door and dropped Secure within 2-7 s of the password (.101 round 9), before Voice ID could start. The
    password is used once, never kept, logged or echoed."""
    secret = str(password or "")
    if not secret:
        return {"ok": False, "error": "Type this computer's login password."}
    post = post or _presence_post
    try:
        got = post("/v1/visit_lamp", {"op": "hold", "password": secret})
    except Exception as e:  # noqa: BLE001
        got = {"ok": False, "error": "presence did not answer (%s)" % type(e).__name__}
    secret = ""
    if not got.get("ok"):
        err = str(got.get("error") or "")
        if "moment" in err:
            return {"ok": False, "error": "Wait a moment, then try the password again."}
        if "did not answer" in err:
            return {"ok": False, "error": err}
        return {"ok": False, "error": "That password was not accepted."}
    if keep:
        return {"ok": True, "error": "", "held": True}
    release_proof(post)
    return {"ok": True, "error": ""}


def release_proof(post=None) -> bool:
    """Let go of the password's Secure hold (Secure stays, droppable by a door face as any Secure is)."""
    post = post or _presence_post
    try:
        return bool((post("/v1/visit_lamp", {"op": "release"}) or {}).get("ok", True))
    except Exception:  # noqa: BLE001  the hold stays until a click; Secure was the point
        return False


def _presence_post(path: str, body: dict, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(crissa_ports.url("presence") + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with crissa_egress.urlopen(req, timeout, what="the menu: proof for Voice ID") as r:
            d = json.loads(r.read(200000).decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            d = {"ok": False, "error": "HTTP %d" % e.code}
    return d if isinstance(d, dict) else {"ok": False}


# BACK TO HER DESK (round 33, .101 10-19, the User: "how do I get her out of close view"): nothing in either menu sent
# her from the laptop close-up (or any loop) back to her desk; .101 did it by hand with POST /v1/home {"by": "user"}.
# The menu's item does that one call. It changes no setting, so it is open to anyone (no face asked). A pack only.
# Kill CRISSA_MENU_DESK=0 (no item).
def desk_item_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_MENU_DESK", "1")).strip().lower() not in ("0", "off", "false", "no")


def face_again_view(gate: dict) -> dict | None:
    """Face photos again (round 34, lib/crissa_init.start_face_again): Voice ID again's own proof (its gate); None where
    it is not offered (the house, CRISSA_FACE_AGAIN=0, no User yet)."""
    if os.environ.get("CRISSA_FACE_AGAIN_ITEM", "0").strip() != "1":
        return None          # the User 2026-10-19: no item of its own; round 35's Add/Edit IDs edit clicks carry it
    try:
        import crissa_init
        if not crissa_init.face_again_on() or not crissa_init._owner_id():
            return None
    except Exception:  # noqa: BLE001
        return None
    return {"label": LABELS["face_again"], "action": "face_again", "ok": bool(gate.get("ok")),
            "why": str(gate.get("why") or ""), "password": bool(gate.get("password"))}


def face_again(body: dict) -> dict:
    """Start Face photos again behind Voice ID again's proof (the face through presence, else the login password)."""
    gate = voice_id_gate()
    password = body.pop("password", None)
    held = False
    if not gate["ok"]:
        if not (gate.get("password") and password):
            return {"ok": False, "error": gate["why"], "password": bool(gate.get("password"))}
        proof = prove_with_password(str(password), keep=KEEP_HOLD)
        password = None
        if not proof["ok"]:
            return {"ok": False, "error": proof["error"], "password": True}
        held = bool(proof.get("held"))
    try:
        import crissa_init
        got = crissa_init.start_face_again(proof_hold=held, led_by=str(body.get("page") or "")[:40])
    except Exception as e:  # noqa: BLE001
        if held:
            release_proof()
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    if held and not got.get("ok"):
        release_proof()
    return {"ok": bool(got.get("ok")), "error": got.get("error") or "",
            "note": "Face photos: look at the camera, then a quarter turn each way." if got.get("ok") else ""}


def desk_view() -> dict | None:
    return {"label": LABELS["desk"], "action": "desk"} if desk_item_on() else None


def to_desk(by: str = "the Crissa menu", post=None) -> dict:
    """Her desk now: the player's own /v1/home, as the person (by "user")."""
    body = {"by": "user", "via": str(by or "")[:40]}
    if post is not None:
        d = post(body)
    else:
        req = urllib.request.Request(crissa_ports.url("player") + "/v1/home", data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        try:
            with crissa_egress.urlopen(req, 4.0, what="the menu: back to her desk") as r:
                d = json.loads(r.read(200000).decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as e:
            try:
                d = json.loads(e.read().decode("utf-8", "replace") or "{}")
            except ValueError:
                d = {"taken": False, "why": "HTTP %d" % e.code}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "the player did not answer (%s)" % type(e).__name__}
    d = d if isinstance(d, dict) else {}
    if d.get("taken"):
        return {"ok": True, "note": "She is going back to her desk."}
    return {"ok": False, "error": "She stays where she is: %s." % (d.get("why") or "the player did not take it")}


def _presence() -> dict:
    try:
        with crissa_egress.urlopen(crissa_ports.url("presence") + "/v1/presence", 1.0,
                                   what="the menu: who is here") as r:
            d = json.loads(r.read(200000).decode("utf-8", "replace") or "{}")
            return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        return {"_err": type(e).__name__}


def view(home: Path | None = None, *, ear_up=None, presence=None, lead_base_ms: float | None = None) -> dict:
    """What the menu shows now, for either Wall. `lead_base_ms` is the player's live library lead (dial + zero),
    for the lip sync item's reachable range; None reads as 0."""
    home = Path(home or crissa_home.HOME)
    try:
        import crissa_init
        m = crissa_init.mic_state()
    except Exception:  # noqa: BLE001
        m = {"on": False}
    ear = (ear_up or _ear_up)()
    L = _launcher_view(home)
    player = crissa_ports.url("player")
    panel = os.environ.get("HOUSE_WALL_PANEL") or (crissa_ports.url("panel") + "/")
    if not (ROOT / "scripts" / "crissa-panel-server.py").is_file() and not os.environ.get("HOUSE_WALL_PANEL"):
        panel = ""                                   # the player pack ships no Control Panel
    if presence is None and not crissa_home.is_house():
        presence = _presence()                       # one read: the Voice ID gate and the Security item's camera tree
    gate = voice_id_gate(presence)
    try:
        import crissa_capability
        cap = crissa_capability.features(home)
    except Exception:  # noqa: BLE001
        cap = {"unavailable": []}
    return {"ok": True, "route": ROUTE,
            # what this computer cannot do, with its reason: the same answer the two Control Panels show. The house's
            # own keys (house_*) are greyed on the panels where they stand; the menu never offered them, so it does
            # not list them either
            "unavailable": [x for x in cap.get("unavailable") or [] if not str(x.get("id") or "").startswith("house_")],
            "mic": {"on": bool(m.get("on")), "ear": bool(ear),
                    "label": LABELS["mic_on"] if m.get("on") else (LABELS["mic_off"] if ear else LABELS["mic_none"])},
            "connect": connect_view(player, home),
            "panel": {"url": panel, "label": LABELS["panel"]},
            # CHOOSE SKIN FROM THE WALL (the User 2026-10-31: "a selection mechanism for User will matter to sales"):
            # the Control Panel's own section, one press away. Absent unless CRISSA_SKIN_MENU=1 and there is a panel
            **({"skin": {"url": panel + "#skin", "label": LABELS["skin"]}} if panel and _skin_menu() else {}),
            # Store when it opens the Crissa store (its catalog, named with this Wall to come back to), Composer when
            # it opens one (2026-10-12)
            "composer": {"url": crissa_home.leave_url(player),
                         "label": LABELS["store"] if crissa_home.leave_label() == "Store" else LABELS["composer"]},
            "lip_sync": lip_sync_view(home, lead_base_ms),
            # FACE ONLY (2026-10-16, lib/crissa_face): her face in a window of its own; a pack only
            "face": face_view(),
            "desk": desk_view(),                     # round 33: Back to her desk
            "phone": phone_view(home),
            "security": security_view(home, presence),
            "voice_id": voice_id_view(gate, presence),
            "face_again": None if ids_flow_on() else face_again_view(gate),   # round 34; round 36: in Add/Edit IDs
            "visitors": {} if ids_flow_on() else visitors_view(gate),
            "guests": guests_view(gate),             # round 41 (F2): Edit guest list (Redo / Remove), his proof first
            "help": {"label": LABELS["help"], "lines": help_lines(L["under"])},
            "stop": {"label": LABELS["stop"], "ask": STOP_ASK % start_command(L["under"]), "ok": bool(L["alive"]),
                     "why": "" if L["alive"] else (L["why"] or "the launcher is not running")},
            "fullscreen": desk_screen(home),
            "wall": wall_state(home),
            "agent_only": agent_only_view(home),    # 2026-10-28: the Wall closes, her face stays (one key)
            "krew": krew_view(panel, cap),          # the places row's third key: the panel's add-on slot, greyed if absent
            "agent": agent_state(home),
            "start": start_view(home),
            "build": build_view(home),
            "glass": glass_view(),
            "launcher": L}


def face_view() -> dict:
    """The Face only item: {} where it is not offered (the house, CRISSA_FACE_ONLY=0)."""
    try:
        import crissa_face
        return {"label": LABELS["face"], "action": "face"} if crissa_face.on() else {}
    except Exception:  # noqa: BLE001
        return {}


# NO USER YET, SO REGISTER IS SETUP (the User 2026-10-26, a cancelled first Setup: "register ID said no when i pressed,
# said to run init. There is nothing other than register new ID"; the house: a first boot's Register IS Init). With no
# User, no Setup running and none finished, a press on Voice ID, Someone new or Add/Edit IDs starts Setup - the walk's
# restart, as the Wall's RUN SETUP key does - instead of refusing with no way in. Kill CRISSA_MENU_REGISTER_RUNS_SETUP=0.
SETUP_INSTEAD_WHY = "Crissa learns you first: this starts Setup."
SETUP_INSTEAD_NOTE = "Setup is starting in Crissa's window."


def register_runs_setup_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_MENU_REGISTER_RUNS_SETUP", "1")).strip().lower() not in ("0", "off", "false", "no")


def setup_first() -> bool:
    """No User yet, Setup neither running nor finished (a cancelled or never-run first boot)."""
    if crissa_home.is_house() or not register_runs_setup_on():
        return False
    try:
        import crissa_init
        return (not crissa_init._owner_id() and str(crissa_init.load_state().get("status") or "") != "running"
                and not crissa_init.marker().get("done"))
    except Exception:  # noqa: BLE001
        return False


def run_setup_instead(by: str = "the Crissa menu") -> dict:
    try:
        import crissa_init
        got = crissa_init.start(force=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    ok = str(got.get("status") or "") == "running" and got.get("ok", True) is not False
    print("[menu] no User yet: %s started Setup instead of Register (%s)" % (by, "running" if ok else got.get("error")),
          flush=True)
    return {"ok": ok, "setup": True, "error": "" if ok else str(got.get("error") or "Setup did not start"),
            "note": SETUP_INSTEAD_NOTE if ok else ""}


def voice_id_view(gate: dict, presence: dict | None = None) -> dict:
    """The Voice ID item: round 20's for the User alone, or (ONE BUTTON, round 21) "Voice ID / add a person…" with its
    question and both answers: `who` {"ask", "me": {label, ok, why, password}, "new": {label, ok, why, password}}."""
    out = {"ok": bool(gate["ok"]), "why": gate["why"], "label": LABELS["voice_id"],
           "password": bool(gate.get("password")), "name": str(_user().get("name") or ""),
           "routine": _voice_id_routine()}
    if not one_routine():
        return out
    me = {"label": LABELS["who_me"], "ok": out["ok"], "why": out["why"], "password": out["password"]}
    new = {"label": LABELS["who_new"], "ok": False, "why": "", "password": False}
    try:
        import crissa_init
        busy = str(gate.get("why") or "") if str(gate.get("why") or "").startswith("Setup is running") else ""
        if not crissa_init.visitor_on():
            new["why"] = "Adding people is switched off on this computer."
        elif not crissa_init._owner_id():
            if setup_first():                        # 2026-10-26: the press starts Setup (NO USER YET, SO REGISTER IS SETUP)
                new.update(ok=True, why=SETUP_INSTEAD_WHY, setup=True)
            else:
                new["why"] = "Run Setup first: Crissa adds people once she knows you."
        elif busy:
            new["why"] = busy
        else:
            pr = person_proof(presence, wait=False)
            # the button is always offered to a person at the computer: a proof not known yet is asked on the press
            # (his face at the camera, with the face sequence's short wait), and the password box comes with it
            new.update(ok=True, proved=bool(pr["ok"]), password=bool(not pr["ok"] and pr.get("password")),
                       why="" if pr["ok"] else str(pr.get("why") or ""))
    except Exception:  # noqa: BLE001
        new["why"] = "Adding people is not available here."
    out.update(label=LABELS["voice_id_one"], ok=bool(me["ok"] or new["ok"]),
               who={"ask": LABELS["who_ask"], "me": me, "new": new})
    if name_only_on():
        out["name_only"] = {"ask": LABELS["name_ask"], "help": NAME_ONLY_HELP,
                            "password": bool(me["password"] or new["password"])}
        if ids_flow_on():                            # round 36: the one button; the proof is asked at Ready, on the page
            out.update(label=IDS_LABEL, ok=True, why="", password=False)
            out["name_only"] = {"ask": LABELS["name_ask"], "help": IDS_HELP, "password": False}
    return out


# THE NAME ONLY (round 23, the User 2026-10-15, the third time asked: "skip this is it you. I never will be after Init.
# silly"; "it must be User when requested, just ask the name and you will determine conflict"): Voice ID / add a person…
# asks the name and nothing else. Crissa decides: the User's own name is his own Voice ID again (its proof, his face or
# the password, as before); any other name is a new person (Add a person, its proof as before); a name already kept for
# someone is said, and Begin again records them again. Kill: CRISSA_VOICE_ID_NAME_ONLY=0 (round 21's question).
NAME_ONLY_HELP = ("Type your own name to record your voice again, or the name of the person to add. Crissa learns their "
                  "voice and face so she can name them on the log. They sit in front of the camera and count to six at "
                  "each beep.")


# ADD/EDIT IDS AND ROOM (round 36, the User 2026-10-19): ONE button replaces Voice ID again, Face photos again and Add a
# person. It is the name box (round 23): the typed name decides, and the run opens on the one page in her window and
# waits for a Ready press (lib/crissa_init.start_ids). The proof (his face or the login password) is asked at his
# Ready, on the page, never here. Kill CRISSA_IDS_FLOW=0 (round 34's three items).
IDS_LABEL = "Add/Edit IDs and Room"
IDS_HELP = ("Type your own name to open your file (redo your voice, your room or your hours with a click), or the name "
            "of the person to add. The page opens in Crissa's window and waits for Ready before anything is recorded.")


def ids_flow_on(env=None) -> bool:
    try:
        import crissa_init
        return bool(crissa_init.ids_flow_on(env))
    except Exception:  # noqa: BLE001
        return False


def name_only_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_VOICE_ID_NAME_ONLY", "1")).strip().lower() not in ("0", "off", "false", "no")


def _same_name(a: str, b: str) -> bool:
    norm = lambda x: " ".join(str(x or "").split()).casefold()   # noqa: E731
    return bool(norm(a)) and (norm(a) == norm(b) or norm(a).split(" ")[0] == norm(b))


def _owner_name() -> str:
    """The User's name as Setup kept it (its user.json), else this menu's own read of it."""
    try:
        import crissa_init
        got = str(crissa_init._load_json(crissa_init.user_path()).get("name") or "")
        if got:
            return got
    except Exception:  # noqa: BLE001
        pass
    return str(_user().get("name") or "")


def resolve_name(name: str, again: bool = False) -> dict:
    """{"who": "me" | "new" | "ask", "why"}: the User's own name (the whole name or the first name he is called) is his
    own Voice ID; a person already kept under that name is asked once (again=True records them again); else new."""
    owner = _owner_name()
    if _same_name(name, owner):
        return {"who": "me"}
    try:
        import crissa_init
        pid = crissa_init.visitor_id_for(name)
        kept = bool(pid) and crissa_init.is_visitor(pid)
    except Exception:  # noqa: BLE001
        kept = False
    if kept and not again:
        first = " ".join(str(name).split())
        return {"who": "ask", "why": "%s is already kept. Begin again to record %s again, or type a different name."
                % (first, first.split(" ")[0])}
    return {"who": "new"}


def visitors_view(gate: dict | None = None) -> dict:
    """ADD A VISITOR's item: {} when switched off or before Setup has a User; else {"ok", "why", "label", "list_label",
    "list": [{person_id, display, voice, face}]}. Not while a walk runs (Voice ID again's own reason)."""
    try:
        import crissa_init
        if not crissa_init.visitor_on() or not crissa_init._owner_id():
            return {}
        rows = crissa_init.list_visitors()
    except Exception:  # noqa: BLE001
        return {}
    busy = str((gate or {}).get("why") or "") if str((gate or {}).get("why") or "").startswith("Setup is running") else ""
    if one_routine():
        # ONE BUTTON (round 21): adding is the Voice ID item's "Someone new"; this item only lists and removes
        return {"ok": not busy, "why": busy, "list_label": LABELS["visitors"], "list": rows}
    return {"ok": not busy, "why": busy, "label": LABELS["add_visitor"], "list_label": LABELS["visitors"],
            "list": rows}


# EDIT GUEST LIST (round 41, .101 round 40 F2, the User: "how would User remove Ted"; his name for it: "Edit guest list"
# with Redo / Remove). With Add/Edit IDs on (a pack's default since round 36) the Visitors item - the only Remove - was
# never drawn. This item is drawn whatever the IDs flow: behind his proof on the menu screen (his face, else the
# password box at once), every kept person with Redo (Add/Edit IDs on their file, round 39 N8) and Remove (prints and
# photos deleted, with a confirm), and their access (Guest; User access only under CRISSA_GUEST_USER_ACCESS=1). It works
# while the face store is confused: the password proves him. Kill CRISSA_GUEST_LIST=0.
def guest_list_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_GUEST_LIST", "1")).strip().lower() not in ("0", "off", "false", "no")


def guests_view(gate: dict | None = None) -> dict:
    """{} when off or before Setup has a User; else {"label", "ok", "why", "list": [{person_id, display, voice, face,
    access}], "access": {level: {label, help}}, "user_access": bool}."""
    if not guest_list_on() or not ids_flow_on():     # without Add/Edit IDs, round 34's Visitors item lists and removes
        return {}
    try:
        import crissa_init
        if not crissa_init.visitor_on() or not crissa_init._owner_id():
            return {}
        rows = crissa_init.list_visitors()
        levels = {k: {"label": crissa_init.ACCESS_LABELS[k], "help": crissa_init.ACCESS_HELP[k]}
                  for k in crissa_init.ACCESS_LEVELS}
        user_access = crissa_init.user_access_on()
    except Exception:  # noqa: BLE001
        return {}
    busy = str((gate or {}).get("why") or "") if str((gate or {}).get("why") or "").startswith("Setup is running") else ""
    return {"label": LABELS["guests"], "ok": not busy, "why": busy, "list": rows, "access": levels,
            "user_access": user_access}


SECURITY_ON_NO_PRINT = ("Security is On. You have no voice print yet, so until Voice ID again records one she treats "
                        "every voice as a guest.")


def security_view(home: Path | None = None, presence: dict | None = None) -> dict:
    """The Security item (crissa_home.security_state: the positions this camera's tree offers - Secure and Double
    Secure only on a camera that turns, presence's own word - `lock` and the ask, each mode's `face` and `password`),
    with this menu's label."""
    home = Path(home or crissa_home.HOME)
    v = crissa_home.security_state(home, presence)
    v["label"] = LABELS["security"] % crissa_home.SECURITY_LABELS.get(v["mode"], v["mode"])
    return v


SECURE_NOT_HERE = "Secure needs a camera that can turn to the door; this camera does not turn."
DOUBLE_NEEDS_PW = "Double Secure holds Secure with this computer's login password: type it."
SECURE_NOT_SEEN = "Secure looks for you at your seat first, and it did not see you; Security is as it was."


def _lamp(presence: dict | None) -> tuple:
    """(secure, held) as presence publishes its lamp."""
    p = presence if isinstance(presence, dict) else {}
    info = p.get("visit_lamp_info") if isinstance(p.get("visit_lamp_info"), dict) else {}
    color = str(p.get("visit_lamp") or info.get("color") or "").strip().lower()
    return color == "sage", bool(info.get("hold") or p.get("hold"))


def _secure_step(cur: str, target: str, presence: dict | None, password: str = "", post=None) -> dict | None:
    """ONE TOGGLE on a camera that turns: Secure's own keys, run by presence (today's Secure exactly) - raise to Secure
    = presence's click (its own look at his seat), to Double Secure = its password hold, from Double Secure to Secure =
    its release, out of Secure = its click; presence checks his face for a step down (its route, the one face-match
    sequence with its own look) and writes each edge back to the toggle. None = no Secure step (the toggle alone);
    {"ok": False, ...} = presence refused, nothing changed."""
    post = post or _presence_post
    secure, held = _lamp(presence)
    hi = crissa_home.SECURE_POSITIONS
    steps = []
    if target == "double_secure" and not held:
        if not password:
            return {"ok": False, "password": True, "error": DOUBLE_NEEDS_PW}
        steps.append({"op": "hold", "password": password})
    elif target == "secure" and held:
        steps.append({"op": "release"})
    elif target == "secure" and not secure:
        steps.append({"op": "click"})
    elif target not in hi and (secure or cur in hi):
        if secure:
            steps.append({"op": "click"})                # out of Secure (a held one too: a click ends the hold)
    for body in steps:
        op = body["op"]
        try:
            got = post("/v1/visit_lamp", body) or {}
        except Exception as e:  # noqa: BLE001
            got = {"ok": False, "error": "presence did not answer (%s)" % type(e).__name__}
        body.pop("password", None)
        err = str(got.get("error") or got.get("why") or "")
        if got.get("face") == "refused":
            return {"ok": False, "face": "refused", "error": err or crissa_home.FACE_SEQ_FAIL}
        sage = str(got.get("visit_lamp") or got.get("color") or "").strip().lower() == "sage"
        if op == "hold" and not got.get("held"):
            return {"ok": False, "password": True, "error": "That password was not accepted." if "refused" in err
                    else (err or "Double Secure was not held.")}
        if op == "click" and target in hi and not sage:
            return {"ok": False, "error": err or SECURE_NOT_SEEN}
        if op == "click" and target not in hi and sage:
            return {"ok": False, "error": err or "Secure did not turn off."}
        if op == "release" and got.get("ok") is False:
            return {"ok": False, "error": err or "Double Secure was not released."}
    return None


def set_security(mode: str, home: Path | None = None, by: str = "the Crissa menu", presence: dict | None = None,
                 password: str = "", post=None) -> dict:
    """Save the Security position; its view comes back. On with no voice print of the User's says what that means.
    ONE TOGGLE: Secure and Double Secure only where this camera turns, and run by presence (_secure_step)."""
    home = Path(home or crissa_home.HOME)
    m = str(mode or "").strip().lower().replace("-", "_")
    if crissa_home.one_toggle() and not crissa_home.is_house():
        if presence is None:
            presence = _presence()
        cam = crissa_home.camera_kind_of(presence)
        cur = crissa_home.security_position(home_dir=home, camera=cam)
        if m in crissa_home.SECURE_POSITIONS and m not in crissa_home.security_positions(cam):
            return {"ok": False, "error": SECURE_NOT_HERE, "security": security_view(home, presence)}
        if m in crissa_home.SECURITY_POSITIONS and crissa_home.camera_turns(cam):
            refused = _secure_step(cur, m, presence, password, post)
            password = ""
            if refused is not None:
                return dict(refused, security=security_view(home, presence))
    got = crissa_home.set_security_mode(m, by=by, home_dir=home)
    if not got.get("ok"):
        return dict(got, security=security_view(home, presence))
    note = "Security: %s. %s" % (crissa_home.SECURITY_LABELS[got["mode"]], crissa_home.SECURITY_ABOUT[got["mode"]])
    if got["mode"] == "on":
        u = _user()
        try:
            import crissa_init
            if u.get("person_id") and crissa_init.voice_prints(str(u.get("person_id"))) < 1:
                note = SECURITY_ON_NO_PRINT
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "mode": got["mode"], "note": note, "security": security_view(home, presence)}


# EVERY CHANGE WHILE SECURITY IS ON NEEDS HIS FACE (lib/crissa_home: the User 2026-10-13, "a stranger should then be
# locked out from any further adjusting ... All tools or switches requesting auth gets face match sequence"). face_gate
# is the one call every setting route of the kit makes before it changes anything (this menu, the Control Panel, the
# feed's keys, Setup's settings, Connect): None = go on; else the refusal to answer with, nothing changed. The sequence
# reads presence here (_presence), and on a camera that turns asks presence for its own look at his seat
# (_face_look: POST /v1/visit_lamp {op: "face_check"}). What stays open to anyone: crissa_home.OPEN_TO_ANYONE.
def _face_look() -> dict:
    try:
        return _presence_post("/v1/visit_lamp", {"op": "face_check"}, timeout=12.0) or {}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "checked": False, "error": type(e).__name__}


def face_gate(action: str, *, target: str | None = None, home: Path | None = None, presence: dict | None = None,
              fetch=None, look=None) -> dict | None:
    """Before a change (above): None = go on; else {"ok": False, "face": "refused", "error", "ask"}
    (crissa_home.face_gate with this menu's presence read and look)."""
    return crissa_home.face_gate(action, fetch=fetch or _presence, look=look or _face_look, target=target,
                                 presence=presence, home_dir=Path(home) if home else None)


glass_gate_name = crissa_home.glass_gate_name     # the glass's own view is open; how it draws her is locked


def _menu_gate_name(action: str, body: dict, home: Path | None) -> str:
    """The lock's name for a menu action (crissa_home.OPEN_TO_ANYONE names the open ones)."""
    if action == "mic":
        return "mic" if bool(body.get("on", True)) else "mic_off"
    if action == "agent_open" and agent_state(home).get("closed"):
        # OPEN AGENT IS THE WAY BACK, AND THE CAMERA IS DOWN WITH HER (Close Agent stops the camera, presence and the
        # oracle with her lanes, lib/crissa_watch AGENT_EYES): no face could be seen, so a face rule here would keep
        # her closed for good. Opening her changes no setting; every change after it is locked again.
        return "agent_close"
    return action


def lip_sync_view(home: Path | None = None, lead_base_ms: float | None = None) -> dict:
    """The Lip sync item: lib/crissa_lipsync.view with this menu's label."""
    v = crissa_lipsync.view(float(lead_base_ms or 0.0), Path(home or crissa_home.HOME))
    v["label"] = LABELS["lip_sync"]
    return v


SCREEN_NOTE = "run/wall-screen.json"


def desk_screen(home: Path | None = None) -> dict:
    """{"on", "label", "mode"}: the desk Wall's full screen as the Wall last noted it. No note (no desk Wall, or one
    that has not drawn yet): not full screen."""
    try:
        d = json.loads((Path(home or crissa_home.HOME) / SCREEN_NOTE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    on = bool(d.get("fullscreen")) if isinstance(d, dict) else False
    mode = str((d or {}).get("mode") or "") if isinstance(d, dict) else ""
    return {"on": on, "label": LABELS["fullscreen"] if on else "", "mode": mode}


def note_desk_screen(mode: str | None, fullscreen: bool, home: Path | None = None) -> None:
    """The desk Wall notes its screen (glass up, each F11): its launch mode (None keeps the noted one) and whether the
    window is full screen now. Never raises."""
    try:
        f = Path(home or crissa_home.HOME) / SCREEN_NOTE
        if mode is None:
            mode = desk_screen(home).get("mode") or ""
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"mode": str(mode), "fullscreen": bool(fullscreen)}), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass


def glass_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_WALL_GLASS", "1")).strip().lower() not in ("0", "off", "false", "no")


def glass_view(env: dict | None = None, probe=None) -> dict:
    """THE BROWSER WALL IS THE ASCII GLASS (the User 2026-10-10, .101: "the browser Wall is not the ASCII look"). The
    feed serves the glass the desk Wall draws (display/voiceonly-home.html at the feed's /); when it answers, the /wall
    page shows it whole and keeps its own two things over it: the first-boot gate to /init and the Crissa key's menu.
    {"on", "up", "port", "path"}; the page builds the address from its own host and this port. Off:
    CRISSA_WALL_GLASS=0 (the film page as before); ?film=1 on /wall for one tab."""
    port = int(crissa_ports.port("feed"))
    if not glass_on(env):
        return {"on": False, "up": False, "port": port, "path": "/"}
    if probe is None:
        def probe() -> bool:
            try:
                with crissa_egress.urlopen(crissa_ports.url("feed") + "/health", 0.5, what="the menu: the glass") as r:
                    return 200 <= int(getattr(r, "status", 200)) < 300
            except Exception:  # noqa: BLE001
                return False
    return {"on": True, "up": bool(probe()), "port": port, "path": "/"}


AGENT_ONLY_ASK = ("Agent only: this Wall closes and her face stays on top of your windows. Open the Wall (the Control "
                  "Panel, or her window's menu) brings the glass back.")


def agent_only_view(home: Path | None = None) -> dict | None:
    """AGENT ONLY (the User 2026-10-28: "closes ascii and leaves render window for agent only view"): one key that
    closes the desk Wall on purpose while her face window stays on top (wall_close + lib/crissa_face wall_off).
    None where there is no desk Wall to close, or it is already closed (Open the Wall is the item then), or the face
    does not stay (CRISSA_WALL_OFF_FACE=0: that is plain Close the Wall, not an agent-only view)."""
    w = wall_state(home)
    if not w.get("present") or w.get("closed"):
        return None
    try:
        import crissa_face
        if not crissa_face.wall_off_on():
            return None
    except Exception:  # noqa: BLE001
        return None
    return {"label": LABELS["agent_only"], "action": "wall_close", "ok": True, "ask": AGENT_ONLY_ASK}


def krew_view(panel_url: str, cap: dict | None = None) -> dict:
    """The places row's Krew key: krew's tiles (or its placeholder) live in the Control Panel's add-on slot, so the key
    opens the panel; with krew not installed it stands greyed with the capability's reason."""
    na = {str(x.get("id") or ""): x for x in ((cap or {}).get("unavailable") or []) if isinstance(x, dict)}
    if "krew" in na:
        return {"label": LABELS["krew"], "url": "", "why": str(na["krew"].get("why") or "krew is not installed")}
    return {"label": LABELS["krew"], "url": panel_url or "", "why": ""}


def wall_state(home: Path | None = None) -> dict:
    """{"closed", "by", "label", "action"}: the desk Wall closed on purpose stays closed (lib/crissa_watch CLOSED)."""
    home = Path(home or crissa_home.HOME)
    try:
        import crissa_watch
        rec = crissa_watch.closed(home, "wall")
    except Exception:  # noqa: BLE001
        rec = {}
    try:
        import crissa_procs
        present = bool(rec) or crissa_procs.known(home, "wall")
    except Exception:  # noqa: BLE001
        present = bool(rec)
    if not present:                                  # no desk Wall in this install (--no-desk): nothing to offer
        return {"closed": False, "by": "", "action": "", "label": "", "present": False}
    return {"closed": bool(rec), "by": str(rec.get("by") or ""), "present": True,
            "action": "wall_open" if rec else "wall_close",
            "label": LABELS["wall_open"] if rec else LABELS["wall_close"]}


def wall_close(home: Path | None = None, by: str = "the Crissa menu") -> dict:
    """Close the desk Wall on purpose: recorded first (so the watch never starts it again), then the launcher stops
    its lane (process mode: the process; service mode: its unit)."""
    home = Path(home or crissa_home.HOME)
    import crissa_watch
    crissa_watch.set_closed("wall", True, by=by, home=home)
    try:
        import crissa_procs
        got = crissa_procs.request(home, "wall", "stop")
    except Exception as e:  # noqa: BLE001
        got = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    face = ""
    try:                                   # THE WALL OFF (2026-10-16): her face stays on top (lib/crissa_face)
        import crissa_face
        face = crissa_face.wall_off(crissa_ports.url("player"), home)
    except Exception:  # noqa: BLE001  the Wall closes either way
        face = ""
    return {"ok": True, "closed": True, "note": ("The Wall is closed and stays closed until you open it again. "
                                                 + face).strip(),
            "launcher": got, "face_on_top": bool(face)}


def wall_open(home: Path | None = None) -> dict:
    """Open the desk Wall again: the close is cleared and the launcher starts its lane (a Wall it never started this
    run, because it was closed, too)."""
    home = Path(home or crissa_home.HOME)
    import crissa_watch
    crissa_watch.set_closed("wall", False, home=home)
    try:                                   # the face window the Wall off opened goes (lib/crissa_face)
        import crissa_face
        crissa_face.wall_on(crissa_ports.url("player"))
    except Exception:  # noqa: BLE001
        pass
    try:
        import crissa_procs
        got = crissa_procs.request(home, "wall", "start")
    except Exception as e:  # noqa: BLE001
        got = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    if not got.get("ok"):
        return {"ok": False, "closed": False, "error": "The Wall is open again for the next start, but the launcher "
                "did not take it now: %s" % (got.get("error") or "?")}
    return {"ok": True, "closed": False, "note": "Opening the Wall…"}


# THE START BAR (the User 2026-10-12, .101's fresh install: "If it takes a while for the mic to start, let's put a bar up
# or something: starting services", "then turns green and auto-loads the wallpaper"). At every start of the launcher the
# glass says what she still waits for - her ear (its loop open and its recognizer answering), a voice print (the bank
# holds one of a person and its encoder is loaded: the ear's /health loads it), her voice (the voice's own server
# answering) - and turns green when nothing is left (lib/crissa_wall_key plate_state, display/crissa-wall.html). Not
# while Setup runs (Setup has its own plates) or her agent is closed. Kill: CRISSA_START_BAR=0.
START_LINE = "Starting Crissa…"
START_READY = "Crissa is ready"


def _ear_health(timeout: float = 0.6) -> dict | None:
    try:
        with crissa_egress.urlopen(crissa_ports.url("stt") + "/health", timeout, what="the menu: the start bar") as r:
            d = json.loads(r.read(200000).decode("utf-8", "replace") or "{}")
            return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return None


def voice_health_url(cfg: dict, env: dict | None = None) -> str:
    """WHERE HER VOICE SAYS IT IS UP (round 16, .101 00:32: the start bar stayed on "Starting Crissa… voice"): the
    voice's address is the one it speaks on - Pocket's, Turbo's and Multilingual's end in /stream - and its health is
    the SERVER's, at the root: /health (Audio8: /api/health, its upstream's). Kokoro named with no address is its own
    server (crissa_tts.kokoro_url). say, Piper and a remote voice (OpenAI) have no server of hers: "" (nothing to wait
    for). Only loopback. Pure: tests read every tier's shape."""
    from urllib.parse import urlsplit
    engine = str((cfg or {}).get("engine") or "").strip().lower()
    url = str((cfg or {}).get("url") or "").strip()
    if not url and engine == "kokoro":
        try:
            import crissa_tts
            url = crissa_tts.kokoro_url(env)
        except Exception:  # noqa: BLE001
            url = ""
    if not url or engine in ("say", "piper", "openai", "text"):
        return ""
    try:
        u = urlsplit(url)
    except ValueError:
        return ""
    if u.scheme != "http" or u.hostname not in ("127.0.0.1", "localhost") or not u.port:
        return ""
    return "http://%s:%d%s" % (u.hostname, u.port, "/api/health" if engine == "audio8" else "/health")


def _voice_up(timeout: float = 0.6) -> bool | None:
    """Her voice's own server answering; None when this install names no voice server of hers (nothing to wait for)."""
    try:
        import crissa_tts
        cfg = crissa_tts.config() or {}
    except Exception:  # noqa: BLE001
        return None
    url = voice_health_url(cfg)
    if not url:
        return None
    try:
        with crissa_egress.urlopen(url, timeout, what="the menu: the start bar") as r:
            return 200 <= int(getattr(r, "status", 200)) < 300
    except Exception:  # noqa: BLE001
        return False


def build_view(home: Path | None = None, *, init_running=None, env=None) -> dict:
    """{"show", "title", "line"}: the building plate (round 15, 2026-10-12, the User: "needs a popup building crissa"):
    her pictures for this screen being built and her loops not yet (lib/crissa_build.plate_view), never while Setup
    runs (Setup's own splash rule stands). Both Walls show it in their one plate, after the start bar
    (lib/crissa_wall_key.plate_state). Kill: CRISSA_BUILD_PLATE=0."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_BUILD_PLATE", "1")).strip().lower() in ("0", "off", "false", "no"):
        return {"show": False}
    try:
        import crissa_build
        pv = crissa_build.plate_view(crissa_build.build_view(home))
    except Exception:  # noqa: BLE001 - a line about the build never costs the menu
        return {"show": False}
    if not pv.get("show"):
        return {"show": False}
    if init_running is None:
        try:
            import crissa_init
            init_running = str(crissa_init.load_state().get("status") or "") == "running"
        except Exception:  # noqa: BLE001
            init_running = False
    return {"show": False} if init_running else pv


def start_view(home: Path | None = None, *, ear=None, voice=None, init_running=None, env=None) -> dict:
    """{"applies", "ready", "waiting": [ear, voice print, voice], "started", "line"}: what this start still waits for."""
    e = os.environ if env is None else env
    home = Path(home or crissa_home.HOME)
    if str(e.get("CRISSA_START_BAR", "1")).strip().lower() in ("0", "off", "false", "no"):
        return {"applies": False}
    try:
        import crissa_procs
        tab = crissa_procs.load(home)
        alive, _why = crissa_procs.launcher_alive(tab) if tab else (False, "")
    except Exception:  # noqa: BLE001
        tab, alive = {}, False
    if not alive:
        return {"applies": False}
    try:
        import crissa_watch
        if crissa_watch.agent_closed(home):
            return {"applies": False}
    except Exception:  # noqa: BLE001
        pass
    if init_running is None:
        try:
            import crissa_init
            init_running = str(crissa_init.load_state().get("status") or "") == "running"
        except Exception:  # noqa: BLE001
            init_running = False
    if init_running:
        return {"applies": False}
    kids = tab.get("children") or {}
    waiting = []
    if any(n in kids for n in ("stt", "ear")):        # this install listens: her ear and a print
        h = (ear or _ear_health)()
        if not h:
            waiting.append("ear")
        else:
            loop = h.get("ear") if isinstance(h.get("ear"), dict) else None
            if loop is not None and not loop.get("open"):
                waiting.append("ear")
            # HER EARS WARM UP BEFORE SHE IS READY (round 33, .101 round 32: "no mic for 5 s" at the start; stt.log `the
            # ASR answers after 5 s`, `the ASR warmed in 16822 ms`). The bar waited for her ear's loop to open, and the
            # loop opens before the ASR answers, so it read "Crissa is ready" while nothing she heard could be decoded.
            # It waits on the ear's `asr_warm` now. The ear stops waiting for the ASR after CRISSA_ASR_WARM_WAIT_S and
            # says `cold`, so the bar never stays up for good. Kill CRISSA_START_BAR_ASR=0.
            if h.get("asr_warm") == "warming" and str(e.get("CRISSA_START_BAR_ASR", "1")).strip().lower() not in (
                    "0", "off", "false", "no"):
                waiting.append("ears warming up")
            bank = h.get("voice_bank")
            if isinstance(bank, dict):
                people = sum(int(v or 0) for k, v in bank.items() if not str(k).endswith("_tts") and k != "announcer")
                if people <= 0 or h.get("voice_encoder_ok") is False:
                    waiting.append("voice print")
    up = (voice or _voice_up)()
    if up is False:
        waiting.append("voice")
    started = str((tab.get("launcher") or {}).get("started") or "")
    return {"applies": True, "ready": not waiting, "waiting": waiting, "started": started,
            "line": START_READY if not waiting else "%s %s" % (START_LINE, ", ".join(waiting))}


def agent_state(home: Path | None = None) -> dict:
    """{"closed", "by", "label", "action", "ask"}: Close Agent or Open Agent, as it stands now."""
    home = Path(home or crissa_home.HOME)
    try:
        import crissa_watch
        rec = crissa_watch.agent_closed(home)
    except Exception:  # noqa: BLE001
        rec = {}
    return {"closed": bool(rec), "by": str(rec.get("by") or ""),
            "action": "agent_open" if rec else "agent_close",
            "label": LABELS["agent_open"] if rec else LABELS["agent_close"], "ask": "" if rec else AGENT_ASK}


def agent_close(home: Path | None = None, by: str = "the Crissa menu") -> dict:
    """Close Agent: recorded; the launcher's next pass stops her listening and speaking lanes and closes her window
    (scripts/crissa.py agent_pass). The desk Wall stays."""
    home = Path(home or crissa_home.HOME)
    import crissa_watch
    crissa_watch.set_closed(crissa_watch.AGENT, True, by=by, home=home)
    return {"ok": True, "closed": True, "agent": agent_state(home),
            "note": "Closing the agent: Crissa stops listening and speaking, and her window closes. The Wall stays."}


def agent_open(home: Path | None = None, by: str = "the Crissa menu") -> dict:
    """Open Agent: the close is cleared; the launcher's next pass starts the lanes Close Agent held."""
    home = Path(home or crissa_home.HOME)
    import crissa_watch
    crissa_watch.set_closed(crissa_watch.AGENT, False, home=home)
    return {"ok": True, "closed": False, "agent": agent_state(home), "note": "Opening the agent: Crissa listens and "
            "speaks again in a moment.", "by": str(by or "")[:40]}


def stop(home: Path | None = None) -> dict:
    """Stop Crissa: ask the launcher to stop everything (both modes: its children or its units) and exit 0."""
    home = Path(home or crissa_home.HOME)
    try:
        import crissa_procs
    except ImportError:
        return {"ok": False, "error": "this install has no launcher record"}
    return crissa_procs.request_quit(home, by="menu")


def act(action: str, body: dict | None = None, home: Path | None = None, lead_base_ms: float | None = None) -> dict:
    body = body or {}
    action = str(action or "").strip().lower()
    if crissa_home.is_house():
        return {"ok": False, "error": "the house has its own panels"}
    pres = None
    if action == "desk":
        if not desk_item_on():
            return {"ok": False, "error": "Back to her desk is off here (CRISSA_MENU_DESK=0)"}
        return to_desk(str(body.get("by") or "the Crissa menu"))   # no setting changes: open to anyone, no face
    if action in ("voice_id", "ids_proof", "add_visitor") and setup_first():
        return run_setup_instead(str(body.get("by") or "the Crissa menu"))   # NO USER YET, SO REGISTER IS SETUP
    if action == "ids_proof":
        # round 39 (.101 round 38 N10, the User: "the menu has a screen before the popup, that menu screen is where to
        # look"): the User's proof as he picks Add/Edit IDs, before the name box - his face, else his password; nobody
        # else gets as far as typing a name. Round 41 (F3): the run it starts takes it as its Ready's proof (_IDS_PROOF).
        pw = body.get("password")
        got = person_proof(password=str(pw) if pw else None, wait=True)
        pw = None
        tok = hold_ids_proof(str(got.get("how") or "")) if got.get("ok") else ""
        if not got.get("ok"):
            clear_ids_proof()
        return {"ok": bool(got.get("ok")), "how": str(got.get("how") or ""), "why": str(got.get("why") or ""),
                "password": bool(got.get("password")), "proof": tok}
    if action == "voice_id" and str(body.get("who") or "").strip().lower() == "name" and one_routine():
        name = " ".join(str(body.get("name") or "").split())[:40]   # THE NAME ONLY (round 23): Crissa decides
        if not name:
            return {"ok": False, "error": "Type a name. A first name is fine.", "name_error": True}
        r = resolve_name(name, again=bool(body.get("again")))
        if r["who"] == "ask":
            return {"ok": False, "error": r["why"], "name_error": True, "ask_again": True}
        if ids_flow_on():                            # round 36: Add/Edit IDs and Room (the page asks the proof at Ready)
            try:
                import crissa_init
                got = crissa_init.start_ids(name, led_by=str(body.get("page") or "ids")[:40],
                                            proof=str(body.get("proof") or ""))
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            return {"ok": bool(got.get("ok")), "error": str(got.get("error") or ""), "who": got.get("who"),
                    "name_error": bool(got.get("name_error")), "ids": True,
                    "note": ("%s's page is open in Crissa's window. Press Ready there when you are in front of the camera."
                             % (got.get("name") or name)) if got.get("ok") else ""}
        body = dict(body, who=r["who"])
        if r["who"] == "me":
            body.pop("name", None)                   # his own: the name he already has
    if action == "voice_id" and str(body.get("who") or "").strip().lower() == "new" and one_routine():
        action = "add_visitor"                       # ONE BUTTON (round 21): "Someone new"
    gate_name = _menu_gate_name(action, body, home) if action in ACTIONS else ""
    own_proof = ("voice_id", "face_again") + (("add_visitor", "visitor_remove") if (one_routine() and add_needs_user_on()) else ())
    if gate_name and gate_name not in own_proof and gate_name not in crissa_home.OPEN_TO_ANYONE:
        # Voice ID again keeps its own proof (voice_id_gate: his face through presence, or the login password)
        pres = _presence()
        target = None
        if action == "security":
            target = str(body.get("mode") or "").strip().lower().replace("-", "_")
            if crissa_home.one_toggle() and crissa_home.camera_turns(crissa_home.camera_kind_of(pres)) and _lamp(pres)[0]:
                target = None                        # Secure is on: presence checks his face for its step down
        refused = face_gate(gate_name, target=target, home=home, presence=pres) \
            if (action != "security" or target is not None) else None
        if refused is not None:
            out = dict(refused)
            if action == "security":
                out["security"] = security_view(home, refused.get("presence") or pres)
            out.pop("presence", None)
            return out
    if action == "mic":
        try:
            import crissa_init
            out = crissa_init.mic(bool(body.get("on", True)))
        except Exception as e:  # noqa: BLE001
            out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        return dict(out, menu=view(home))
    if action == "face_again":
        return face_again(body)                      # round 34: Voice ID again's own proof (face_again)
    if action == "voice_id":
        # VOICE ID AGAIN ON ONE PAGE (the User 2026-10-11, .101 round 9: "no name entry avail either"): the page in her
        # window (/init?voice_id=1) sends the name, the password when the gate asks for one, and `replace`; the take
        # starts here at once. The password's Secure hold is kept until the take has ended (crissa_init releases it).
        gate = voice_id_gate()
        password = body.pop("password", None)
        held = False
        if not gate["ok"]:
            if not (gate.get("password") and password):
                return {"ok": False, "error": gate["why"], "password": bool(gate.get("password"))}
            proof = prove_with_password(str(password), keep=KEEP_HOLD)
            password = None
            if not proof["ok"]:
                return {"ok": False, "error": proof["error"], "password": True}
            held = bool(proof.get("held"))
        kw = {}
        name = str(body.get("name") or "").strip()
        if name:
            kw["name"] = name[:40]
        if body.get("replace"):
            kw["replace"] = True
        if held:
            kw["proof_hold"] = True
        if body.get("page"):
            kw["led_by"] = str(body.get("page"))[:40]
        try:
            import crissa_init
            got = crissa_init.start_voice_id(**kw)
        except Exception as e:  # noqa: BLE001
            if held:
                release_proof()
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        if held and not got.get("ok"):
            release_proof()
        verbal = _voice_id_routine() == "verbal"
        return {"ok": bool(got.get("ok")), "error": got.get("error") or "",
                "note": ((VERBAL_NOTE if verbal else ("Voice ID is starting." if name else "Voice ID is on the Wall now."))
                         if got.get("ok") else "")}
    if action == "add_visitor":
        # ADD A VISITOR: the same Voice ID for a new person. Round 21: only the User, in every mode (person_proof); round
        # 20 (the switches off): the face rule above (On: his face; ID-only / Off: anyone)
        name = str(body.get("name") or "").strip()[:40]
        if not name:
            return {"ok": False, "error": "Type the person's name. A first name is fine.", "name_error": True}
        if one_routine() and add_needs_user_on():
            pr = person_proof(password=body.pop("password", None))
            if not pr["ok"]:
                return {"ok": False, "error": pr["why"], "password": bool(pr.get("password")), "proof": "user"}
        try:
            import crissa_init
            got = crissa_init.start_visitor(name)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        who = str((got.get("visitor") or {}).get("display") or name)
        return {"ok": bool(got.get("ok")), "error": got.get("error") or "", "name_error": bool(got.get("name_error")),
                "note": (VISITOR_NOTE % who) if got.get("ok") else ""}
    if action == "guest_access":
        if not guest_list_on():
            return {"ok": False, "error": "Edit guest list is off here (CRISSA_GUEST_LIST=0)"}
        if not check_ids_proof(str(body.get("proof") or "")):
            pr = person_proof(password=body.pop("password", None))
            if not pr["ok"]:
                return {"ok": False, "error": pr["why"], "password": bool(pr.get("password")), "proof": "user"}
        try:
            import crissa_init
            return crissa_init.set_access(str(body.get("person_id") or ""), str(body.get("access") or ""))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    if action == "visitor_remove":
        if check_ids_proof(str(body.get("proof") or "")):
            pass                                     # round 41 (F2): proven on the Edit guest list's menu screen
        elif one_routine() and add_needs_user_on():
            pr = person_proof(password=body.pop("password", None))
            if not pr["ok"]:
                return {"ok": False, "error": pr["why"], "password": bool(pr.get("password")), "proof": "user"}
        try:
            import crissa_init
            got = crissa_init.remove_visitor(str(body.get("person_id") or ""))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        return dict(got, note=("%s is forgotten: their voice prints and photos are deleted." % got.get("display"))
                    if got.get("ok") else "")
    if action == "lip_sync":
        # this machine's lip sync: <state home>/lip-sync.json only, never the library's mouth-dials.json
        out = crissa_lipsync.act(body, float(lead_base_ms or 0.0), Path(home or crissa_home.HOME))
        out.setdefault("lip_sync", {})["label"] = LABELS["lip_sync"]
        return out
    if action == "security":
        pw = str(body.pop("password", "") or "")
        return set_security(str(body.get("mode") or ""), home, by=str(body.get("by") or "the Crissa menu")[:40],
                            presence=pres, password=pw)
    if action == "wall_close":
        return wall_close(home, by=str(body.get("by") or "the Crissa menu")[:40])
    if action == "wall_open":
        return wall_open(home)
    if action == "agent_close":
        return agent_close(home, by=str(body.get("by") or "the Crissa menu")[:40])
    if action == "agent_open":
        return agent_open(home, by=str(body.get("by") or "the Crissa menu")[:40])
    if action == "face":
        # FACE ONLY IS THE TILE'S WALL KEY (round 27, the User 2026-10-16: "Wallpaper did not disappear on Face Only. It
        # should"; "we make menu button match is all"): where there is a desk Wall, Face only closes it and her face
        # stays on top, exactly as her tile's WALL key; its WALL key brings the Wall back. No desk Wall (--no-desk):
        # her face window, as before. Kill: CRISSA_FACE_ONLY_CLOSES_WALL=0.
        if (str(os.environ.get("CRISSA_FACE_ONLY_CLOSES_WALL", "1")).strip().lower() not in ("0", "off", "false", "no")
                and wall_state(home).get("present")):
            return wall_close(home, by=str(body.get("by") or "Face only")[:40])
        try:
            import crissa_face
            ok = crissa_face.open_window(crissa_ports.url("player"), home)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        return {"ok": ok, "note": "Her face is opening in a window of its own." if ok else "",
                "error": "" if ok else "Face only is not available here."}
    if action == "stop":
        if not body.get("confirm"):
            return {"ok": False, "ask": True, "error": "All OS Off needs {\"confirm\": true}"}
        return stop(home)
    return {"ok": False, "error": "unknown action %r (one of %s)" % (action, ", ".join(ACTIONS))}
