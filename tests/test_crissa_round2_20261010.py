"""The .101 test's second round (the User 2026-10-10, RESULTS sections E-P and S, screenshots-20261010).

E  a bad key is said as one, whatever the status: xAI's 400, with its hint (an API key, not a management key)
F  a first boot opens Setup (/init) in the browser once; the desk Wall then shows a passive plate, no Tk dialogs
G  :8097 counts only the tiles it shows and names each one down; Crissa units are named as their pieces
H  the ear follows the sound server's default input unless one is named; Voice ID shows it and lets the person pick
I  one unit owns each port: a unit on its way down or up is not a crash, and never gets a by-hand copy beside it
J  the browser Wall reconnects by itself when the player answers again
K  ten_vad's TenVad.__del__ no longer prints an AttributeError for an object it never finished
L  "Start Crissa when I log in" is a choice on Init's last step, off unless chosen
M  only the session's own speaker (or speech naming her) continues and extends a session; 45 s in a pack
N  the bridge takes only turns newer than its own start, and waits for the ear quietly
O  a Crissa unit's tape is its own log file (live), the :8099 panel is Crissa's (crown, ear row, count, no house keys)
P  the browser Wall shows the ASCII glass when the feed answers, keeping /init and the Crissa key
S  the session-end kiosk call is made only where a kiosk is named; a 404 is "no kiosk here", said once
No unit, no browser, no network, no model: every piece is called with stand-ins."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path

from _kit import kit  # the kit's copy of a shipped file (the Crissa merge, 2026-10-11)

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "modules" / "ear"))
import crissa_home  # noqa: E402
import crissa_init as zi  # noqa: E402
import crissa_service as S  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def pack(tmp_path, monkeypatch):
    home = tmp_path / "crissa"
    home.mkdir()
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(crissa_home, "HOME", home)
    zi.configure(home=home)
    yield home
    zi.configure()


# ---- E ---------------------------------------------------------------------------------------------------------------
def _http_error(code, body):
    return urllib.error.HTTPError("https://api.x.ai/v1/chat/completions", code, "Bad Request", {},
                                  io.BytesIO(body.encode()))


def test_e_a_bad_key_is_said_as_one_with_xais_hint():
    import crissa_agent as A
    cfg = {"url": "https://api.x.ai/v1", "model": "grok-4.7"}
    e = A.plain_error(_http_error(400, '{"code":"invalid-argument","error":"Incorrect API key provided: xa***"}'), cfg)
    assert str(e).startswith(A.KEY_PLAIN) and "not a management key" in str(e)
    e = A.plain_error(_http_error(400, '{"error":"team cannot be accessed via a management key"}'),
                      {"url": "https://example.test/v1"})
    assert A.KEY_PLAIN in str(e) and "management key" in str(e)
    e = A.plain_error(_http_error(401, '{"error":{"type":"authentication_error"}}'), {"url": "https://api.anthropic.com/v1"})
    assert str(e) == A.KEY_PLAIN
    e = A.plain_error(_http_error(400, '{"error":"max_tokens is too large"}'), cfg)
    assert "HTTP 400" in str(e) and A.KEY_PLAIN not in str(e)


# ---- F ---------------------------------------------------------------------------------------------------------------
def test_f_a_first_boot_opens_setup_once_and_never_without_a_screen(pack, monkeypatch):
    L = _load("crissa_launcher_r2f", ROOT / "scripts" / "crissa.py")
    monkeypatch.setattr(L, "_log", lambda *a, **k: None)
    monkeypatch.setattr(L, "_say", lambda *a, **k: None)
    opened = []
    env = {"CRISSA_INIT": "1", "DISPLAY": ":0"}
    assert L.open_setup_once("http://127.0.0.1:8100", None, env, opener=lambda u: opened.append(u) or True) == "opened"
    assert opened == ["http://127.0.0.1:8100/init"]
    assert zi.browser_record()["url"] == "http://127.0.0.1:8100/init"
    assert L.open_setup_once("http://127.0.0.1:8100", None, env, opener=lambda u: opened.append(u) or True) == "already"
    assert len(opened) == 1                                  # once
    zi.browser_path().unlink()
    assert L.open_setup_once("http://x", None, dict(env, CRISSA_OPEN="0"), opener=opened.append) == "off"
    assert L.open_setup_once("http://x", None, {"CRISSA_INIT": "1"}, opener=opened.append, ) == "no screen" or \
        sys.platform == "darwin"
    assert L.open_setup_once("http://x", None, {"DISPLAY": ":0"}, opener=opened.append) == "not first boot"
    assert len(opened) == 1


def test_f_the_desk_wall_is_passive_while_the_browser_holds_setup(pack):
    import types
    import init_v2_render as V
    zi.mark_browser_opened("http://127.0.0.1:8100/init")
    snap = {"status": "running", "step_at": 0, "browser": zi.browser_led({"status": "running"}),
            "plate": {"kind": "connect", "title": "Connect an agent", "connect": {"editable": True}, "later": True}}
    assert snap["browser"] == {"led": True, "url": "http://127.0.0.1:8100/init", "by": "launcher", "own": False}
    hits = {}
    g = types.SimpleNamespace(W=1920, H=1080, u=1920 / 88.0, page_left=8.0)
    V.render(g, snap, hits)
    assert set(hits) == {"init2_open_browser"} or set(hits) == {"init2_open_browser", "init_cancel"}
    assert not any(k.startswith("init2_connect") for k in hits)
    snap["browser"] = {"led": False, "url": ""}
    hits = {}
    V.render(g, snap, hits)
    assert "init2_connect_edit" in hits                      # without the browser the plate keeps its keys
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    assert 'if name == "init2_open_browser":' in src and "open_url(url)" in src


# ---- G / O (:8097) ---------------------------------------------------------------------------------------------------
@pytest.fixture
def panel():
    return _load("crissa_panel_r2", ROOT / "scripts" / "crissa-panel-server.py")


def test_g_the_count_is_of_the_tiles_shown_each_named(panel):
    data = {"services": [{"unit": "crissa-stt", "label": "STT - the microphone's turns", "health": "bad"},
                         {"unit": "crissa-asr", "label": "ASR - speech to text", "health": "ok"},
                         {"unit": "lane:wall", "label": "Wall", "health": "warn"},
                         {"unit": "", "label": "Store", "link": True, "health": ""},
                         {"unit": "proc:x", "label": "Gone", "health": "bad", "blank": "needs an NVIDIA GPU"}],
            "voices": {"tiles": [{"id": "multilingual", "label": "Chatterbox Multilingual V3", "state": "bad",
                                  "unit": "proc:multilingual", "blank": "needs an NVIDIA GPU"},
                                 {"id": "pocket", "label": "Pocket", "state": "ok", "unit": "proc:pocket"}]}}
    fl = panel.fleet_of(data)
    assert fl["down"] == 1 and fl["down_names"] == ["STT"] and fl["warn_names"] == ["Wall"] and fl["total"] == 4
    page = (ROOT / "display" / "crissa-panel.html").read_text(encoding="utf-8")
    assert "fl.down_names" in page and '"down: "+(dn||down)' in page


def test_g_crissa_units_are_named_as_their_pieces(panel):
    rows = [{"unit": "crissa-stt", "label": "crissa-stt"}, {"unit": "crissa-panel", "label": "Control Panel"},
            {"unit": "house-janitor.timer", "label": "house-janitor.timer"}]
    panel.friendly_labels(rows)
    assert rows[0]["label"].startswith("STT") and rows[1]["label"] == "Control Panel"
    assert rows[2]["label"] == "house-janitor.timer"


def test_o_a_crissa_units_tape_is_its_own_log_file(panel, tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bridge.log").write_text("".join("line %d\n" % i for i in range(50)))
    monkeypatch.setattr(panel, "_lane_home", lambda: tmp_path)
    monkeypatch.setattr(panel, "resolve_unit", lambda u: ("crissa-bridge.service", "user"))
    monkeypatch.setattr(panel, "output_files", lambda u, scope: [str(logs / "bridge.log")])
    monkeypatch.setattr(panel, "_journal_scope", lambda *a, **k: (["systemd: Started"], "", ""))
    got = panel.journal("crissa-bridge", 10)
    assert got["file_index"] == 0 and got["lines"][-1] == "line 49"
    got = panel.journal("crissa-bridge", 10, file="journal")
    assert got["lines"][0] == "systemd: Started"
    monkeypatch.setattr(panel, "output_files", lambda u, scope: ["/var/log/other.log"])
    assert panel.live_file(["/var/log/other.log"]) is None
    page = (ROOT / "display" / "crissa-panel.html").read_text(encoding="utf-8")
    assert 'tape.file="journal"' in page


def test_g_the_mic_line_reads_the_ear_and_the_lines_box_starts_closed():
    page = (ROOT / "display" / "crissa-panel.html").read_text(encoding="utf-8")
    assert 'id="micNote"' in page and "MIC.on ? \"on, she hears you.\"" in page
    assert "The Mic key is her microphone: off, she does not hear you." not in page.split("<script>")[0]
    assert '<details id="lineBox">' in page and "crissa.panel.linesOpen" in page


# ---- O (:8099 in a pack) ---------------------------------------------------------------------------------------------
@pytest.fixture
def feed():
    return _load("voiceonly_feed_r2", ROOT / "scripts" / "voiceonly-home-server.py")


def test_o_the_feeds_panel_is_crissas_in_a_pack(feed, monkeypatch):
    monkeypatch.setattr(feed.crissa_home, "is_house", lambda manifest=None: False)
    html = feed.panel_page_html()
    assert b'<span class="wordmark">Crissa</span>' in html and b"House controls" not in html
    assert b"<title>Crissa Controls</title>" in html
    monkeypatch.setattr(feed.crissa_home, "is_house", lambda manifest=None: True)
    assert b"House controls" in feed.panel_page_html()


def test_o_the_feeds_count_folds_what_this_computer_cannot_run(feed):
    cap = {"features": {"turbo": {"ok": False}, "multilingual": {"ok": False}, "keep_warm": {"ok": False}}}
    rows = [{"unit": "proc:player", "label": "Crissa - her picture", "health": "ok"},
            {"unit": "proc:multilingual", "label": "Chatterbox Multilingual V3 - the GPU voice", "health": "bad"},
            {"unit": "proc:stt", "label": "STT - the microphone's turns", "health": "bad"},
            {"unit": "", "label": "krew", "placeholder": True, "health": ""}]
    shown, fl = feed.pack_fleet(rows, cap)
    assert [r["unit"] for r in shown] == ["proc:player", "proc:stt", ""]
    assert fl["down"] == 1 and fl["down_names"] == ["STT"] and fl["total"] == 2


def test_o_the_feeds_ear_row_reads_the_ears_lock(feed, monkeypatch):
    monkeypatch.setattr(feed, "fetch", lambda url, timeout=4.0: (200, b'{"voice": "on"}', "application/json"))
    assert feed.pack_voice() == {"voice_on": True, "ear": True}
    monkeypatch.setattr(feed, "fetch", lambda url, timeout=4.0: (0, b"", ""))
    assert feed.pack_voice() == {"voice_on": False, "ear": False}
    page = kit("display/voiceonly-panel.html").read_text(encoding="utf-8")
    assert 'v.ear===false ? "the ear is not answering"' in page
    assert 'house_register: ["#goRegister", "#goInitUser", "#initFirstBootTick"]' in page


# ---- H ---------------------------------------------------------------------------------------------------------------
def test_h_the_ear_follows_the_default_input_unless_one_is_named(monkeypatch):
    from crissa_ear import audio
    srcs = ["alsa_input.pci-0000_c5_00.6.analog-stereo", "alsa_input.usb-Shure_Inc_Shure_MV6-00.mono-fallback",
            "alsa_output.pci.monitor"]
    monkeypatch.setattr(audio._capture, "pulse_available", lambda: True)   # a machine with no sound server (CI) too
    monkeypatch.setattr(audio, "list_pulse_sources", lambda: list(srcs))
    monkeypatch.setattr(audio, "default_pulse_source", lambda: srcs[1])
    assert audio.resolve_mic("auto") == srcs[1]
    monkeypatch.setattr(audio, "default_pulse_source", lambda: "")
    assert audio.resolve_mic("auto") == srcs[0]                 # no default: the first input, as before
    monkeypatch.setattr(audio, "default_pulse_source", lambda: srcs[1])
    assert audio.resolve_mic("analog") == srcs[0]               # a named one wins


PACTL = {("list", "sources"): "Source #1\n\tName: alsa_input.analog\n\tDescription: Built-in Audio Analog Stereo\n"
                             "Source #2\n\tName: alsa_input.usb-Shure\n\tDescription: Shure MV6\n"
                             "Source #3\n\tName: alsa_output.x.monitor\n\tDescription: Monitor of Speakers\n",
         ("get-default-source",): "alsa_input.usb-Shure\n"}


def test_h_voice_id_says_which_microphone_and_a_pick_names_it_for_both(pack, monkeypatch):
    pactl = lambda *a, **k: PACTL.get(tuple(a), "")  # noqa: E731
    v = zi.mic_view(pactl)
    assert v["source"] == "alsa_input.usb-Shure" and v["label"] == "Shure MV6" and not v["named"]
    assert [c["name"] for c in v["choices"]] == ["alsa_input.analog", "alsa_input.usb-Shure"]
    assert v["line"] == "Microphone: Shure MV6 (the computer's default)."
    asked = []
    got = zi.set_mic_source("alsa_input.analog", pactl, restart=lambda: asked.append("stt") or {"ok": True})
    assert got["ok"] and asked == ["stt"]
    assert json.loads((pack / "ear.json").read_text())["ear"]["mic"] == "alsa_input.analog"
    assert json.loads((zi.identity_root() / "site.json").read_text())["enroll"]["mic"] == "alsa_input.analog"
    assert zi.mic_view(pactl)["named"] and zi.mic_view(pactl)["label"] == "Built-in Audio Analog Stereo"
    assert zi.set_mic_source("nope", pactl, restart=lambda: None)["ok"] is False
    zi.set_mic_source("auto", pactl, restart=lambda: {"ok": True})
    assert "mic" not in json.loads((pack / "ear.json").read_text())["ear"]
    page = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")
    # round 20: a pick is applied at once, from the list itself (the "Use this microphone" button re-drew the plate)
    assert 'act("mic_pick", { source: ev.target.value })' in page and 'id: "r-mic-use"' not in page


# ---- I ---------------------------------------------------------------------------------------------------------------
class _Sd:
    def __init__(self, active="active", start_rc=0, start_err=""):
        self.active, self.start_rc, self.start_err, self.calls = active, start_rc, start_err, []

    def __call__(self, argv, timeout=10.0):
        self.calls.append(list(argv))
        if "is-active" in argv:
            return (0 if self.active == "active" else 3), self.active + "\n", ""
        if "start" in argv:
            return self.start_rc, "", self.start_err
        if "show" in argv:
            return 0, "4242\n", ""
        return 0, "", ""


def test_i_a_unit_on_its_way_down_or_up_is_not_a_crash():
    for state in ("deactivating", "activating", "reloading", "active"):
        assert S.UnitProc("crissa-stt.service", _Sd(state)).poll() is None, state
    assert S.UnitProc("crissa-stt.service", _Sd("failed")).poll() == 1


def test_i_a_unit_that_did_not_start_never_gets_a_by_hand_copy(tmp_path, monkeypatch):
    L = _load("crissa_launcher_r2i", ROOT / "scripts" / "crissa.py")
    monkeypatch.setattr(L, "_log", lambda *a, **k: None)
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    monkeypatch.setattr(S, "write_desk_unit", lambda *a, **k: {"unit": tmp_path / "u"})
    popen = []
    sd = _Sd("failed", start_rc=1, start_err="Job for crissa-stt.service canceled.")
    g = L.Group(tmp_path / "logs", None, use_units=True, unit_run=sd)
    monkeypatch.setattr(g, "_spawn_popen", lambda *a, **k: popen.append(a) or object())
    p = g.spawn("stt", ["python3", "x.py"], {}, 8190)
    assert isinstance(p, S.UnitProc) and popen == []
    assert sum(1 for c in sd.calls if "start" in c) == 2      # once more after a beat, never by hand
    sd2 = _Sd("failed", start_rc=1, start_err="Failed to connect to bus: No medium found")
    g2 = L.Group(tmp_path / "logs2", None, use_units=True, unit_run=sd2)
    monkeypatch.setattr(g2, "_spawn_popen", lambda *a, **k: popen.append(a) or "by hand")
    assert g2.spawn("stt", ["python3", "x.py"], {}, 8190) == "by hand"   # no user manager at all: by hand


def test_i_the_acceptance_restarts_one_unit_alone():
    src = (ROOT / "scripts" / "public-loops-accept.py").read_text(encoding="utf-8")
    assert "def _one_unit_restart(" in src and 'if verb == "restart":' in src and 'STATE.write_text("deactivating")' in src


# ---- J / P -------------------------------------------------------------------------------------------------------------
def test_j_p_the_browser_wall_reconnects_and_shows_the_glass(monkeypatch):
    page = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
    for needle in ("function reconnect(", 'img.src = "/frame.mjpg?r="', "W.down = true", "boot !== W.boot",
                   "async function glassGate()", 'document.body.classList.add("glass")', 'Q.get("film") === "1"',
                   '<iframe id="glass"', "if (await initGate()) return;\n  await glassGate();"):
        assert needle in page, needle
    assert page.index("await initGate()") < page.index("await glassGate()")     # /init first, then the glass
    import crissa_menu as M
    assert M.glass_view({}, probe=lambda: True) == {"on": True, "up": True, "port": M.crissa_ports.port("feed"),
                                                    "path": "/"}
    assert M.glass_view({"CRISSA_WALL_GLASS": "0"}, probe=lambda: True)["on"] is False
    assert M.glass_view({}, probe=lambda: False)["up"] is False


# ---- K ---------------------------------------------------------------------------------------------------------------
def test_k_no_tenvad_is_left_to_free():
    """Round 41 (the User 2026-10-22: "drop TEN VAD"): the ear no longer loads TEN VAD, so its __del__ guard went with it."""
    from crissa_ear import vad
    assert not hasattr(vad, "_quiet_ten_del") and not hasattr(vad, "_ten_spans")


# ---- L ---------------------------------------------------------------------------------------------------------------
def test_l_login_start_is_a_choice_off_unless_chosen(pack, tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_LOGIN", raising=False)
    assert S.LOGIN_DEFAULT is False and S.login_wanted(pack) is False
    unit = tmp_path / "crissa-launcher.service"
    unit.write_text("[Unit]\n")
    monkeypatch.setattr(S, "service_file", lambda platform=None, env=None: unit)
    calls = []
    run = lambda argv, t=10.0: calls.append(argv) or (0, "", "")  # noqa: E731
    got = S.set_login(True, home=pack, run=run, platform="linux", by="test")
    assert got["ok"] and calls[-1] == ["systemctl", "--user", "enable", S.UNIT] and S.login_wanted(pack)
    S.set_login(False, home=pack, run=run, platform="linux")
    assert calls[-1] == ["systemctl", "--user", "disable", S.UNIT] and not S.login_wanted(pack)
    assert S.login_wanted(pack, env={"CRISSA_LOGIN": "1"}) is True
    import plistlib
    assert plistlib.loads(S.plist_bytes(["python3"], pack, {}))["RunAtLoad"] is False


def test_l_install_follows_the_choice(pack, tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_LOGIN", raising=False)
    unit = tmp_path / "units" / "crissa-launcher.service"
    monkeypatch.setattr(S, "service_file", lambda platform=None, env=None: unit)
    calls = []
    got = S.install(["--no-desk"], home=pack, start=False, run=lambda argv, t=10.0: calls.append(argv) or (0, "", ""),
                    platform="linux", env={"PATH": "/usr/bin"})
    assert ["systemctl", "--user", "disable", S.UNIT] in calls and ["systemctl", "--user", "enable", S.UNIT] not in calls
    assert got["login"] is False and "not started at login" in got["note"]


def test_l_inits_last_step_offers_it(pack, monkeypatch):
    v = zi.done_view({})
    assert v["login"]["on"] is False and v["login"]["label"] == "Start Crissa when I log in" and v["login"]["why"]
    seen = []
    monkeypatch.setattr(S, "set_login", lambda on, **k: seen.append(on) or {"ok": True, "note": "ok"})
    assert zi.apply("login", {"on": True})["ok"] and seen == [True]
    page = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")
    assert 'act("login", { on: box.checked })' in page


# ---- M / N / S (the bridge) ------------------------------------------------------------------------------------------
@pytest.fixture
def eb():
    import crissa_bridge.ear_bridge as eb
    return eb


def test_m_only_the_sessions_own_speaker_continues_it(eb, monkeypatch):
    monkeypatch.setattr(eb, "SESSION_OWNER_ONLY", True)
    assert eb.session_follow_ok("sam", "sam", "and the weather?")
    assert not eb.session_follow_ok("sam", "kim", "and the weather?")
    assert eb.session_follow_ok("sam", None, "it's a crisis"), "no stamp is never another voice (MM, 2026-10-11)"
    assert eb.session_follow_ok("sam", "kim", "Chrissa, the weather?")     # speech that names her
    assert eb.session_follow_ok(None, None, "and tomorrow?") and not eb.session_follow_ok(None, "kim", "hm")
    monkeypatch.setattr(eb, "SESSION_NONE_FOLLOWS", False)                  # the 2026-10-10 rule, by its kill switch
    assert not eb.session_follow_ok("sam", None, "it's a crisis")
    monkeypatch.setattr(eb, "SESSION_NONE_FOLLOWS", True)
    monkeypatch.setattr(eb, "SESSION_OWNER_ONLY", False)
    assert eb.session_follow_ok("sam", "kim", "anything")
    from crissa_bridge import settings
    assert settings.TABLE["session_sec"][1] == "20"     # the User 2026-10-14 (45 from 10-10)


def test_n_turns_older_than_the_bridge_are_never_acted_on(eb, monkeypatch):
    monkeypatch.setattr(eb, "ONLY_NEW_TURNS", True)
    assert eb.turn_is_old({"ts": 1000.0}, started=2000.0)
    assert not eb.turn_is_old({"ts": 1999.0}, started=2000.0)          # inside the slack
    assert not eb.turn_is_old({"ts": 2100.0}, started=2000.0)
    assert not eb.turn_is_old({"ts": "2026-10-10T15:00"}, started=2000.0)
    said = []
    monkeypatch.setattr(eb, "_log", said.append)
    monkeypatch.setattr(eb, "_QUIET", {})
    for _ in range(5):
        eb.quiet_log({"event": "poll_error", "error": "refused"})
    assert len(said) == 1
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text(encoding="utf-8")
    assert "if turn_is_old(t):" in src and "while time.time() < seed_end:" in src


def test_s_the_kiosk_call_only_where_a_kiosk_is_named_and_a_404_said_once(eb, monkeypatch):
    posted, said = [], []
    monkeypatch.setattr(eb, "_log", said.append)
    monkeypatch.setattr(eb, "DOC_WINDOW", Path("/nonexistent/doc-window.sh"))
    monkeypatch.setattr(eb, "WALL_HOME", False)
    monkeypatch.setattr(eb, "_post_json", lambda url, body, timeout=2.0: posted.append(url) or {"ok": True})
    eb.clear_session_board()
    assert eb.ACTION_URL not in posted and not [e for e in said if e.get("event") == "session_wall_home"]

    def nf(url, body, timeout=2.0):
        if url == eb.ACTION_URL:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return {"ok": True}
    monkeypatch.setattr(eb, "WALL_HOME", True)
    monkeypatch.setattr(eb, "_wall_home_absent", {"said": False})
    monkeypatch.setattr(eb, "_post_json", nf)
    eb.clear_session_board()
    eb.clear_session_board()
    homes = [e for e in said if e.get("event") == "session_wall_home"]
    assert homes == [{"event": "session_wall_home", "skipped": "no kiosk to move here (404)"}]


def test_i_the_launchers_table_follows_a_unit_systemd_restarted_by_itself():
    """The acceptance of crissa-87bcf6af: after `systemctl --user restart crissa-stt` the unit was its one process on
    its port, but the launcher's table kept the old pid, so the feed's panel read the lane as down."""
    src = (ROOT / "scripts" / "crissa.py").read_text(encoding="utf-8")
    i = src.index("a unit systemd restarted by itself")
    block = src[i - 400:i + 500]
    assert "isinstance(p, crissa_service.UnitProc)" in block and 'table.child(name).get("pid") != p.pid' in block
    assert 'table.mark(name, pid=p.pid, state="running", by="systemd"' in block


# ---- from the sandbox trial of crissa-87bcf6af (gathered for the next cut) ------------------------------------------
def test_o_the_feeds_tape_reads_a_launcher_piece_and_its_ear_row_survives_a_missing_upstream(feed, tmp_path, monkeypatch):
    import crissa_procs
    log = tmp_path / "player.log"
    log.write_text("".join("player line %d\n" % i for i in range(30)))
    monkeypatch.setattr(crissa_procs, "load", lambda home: {"children": {"player": {"log": str(log)}}})
    got = feed.proc_tape("player")
    assert got["ok"] and got["lines"][-1] == "player line 29"
    assert feed.proc_tape("../etc")["ok"] is False and feed.proc_tape("nope")["ok"] is False
    src = (ROOT / "scripts" / "voiceonly-home-server.py").read_text(encoding="utf-8")
    assert src.count('**({} if crissa_home.is_house() else {"voice": pack_voice()})') == 2
    assert 'if str(unit).startswith("proc:") and not crissa_home.is_house():' in src
    page = kit("display/voiceonly-panel.html").read_text(encoding="utf-8")
    assert "grid-column:1 / -1" in page and 'el.classList.remove("lit")' in page and 'el.tagName==="LABEL"' in page


def test_g_a_gpu_voices_launcher_piece_folds_with_its_voice(panel):
    data = {"services": [{"unit": "proc:multilingual", "health": "bad"}, {"unit": "proc:pocket", "health": "ok"}],
            "voices": {"tiles": []}, "addons": {"missing": []}, "gpu": None}
    cap = {"features": {"multilingual": {"ok": False, "why": "needs an NVIDIA GPU"}, "turbo": {"ok": True},
                        "keep_warm": {"ok": False}}}
    panel.blank_unavailable(data, cap)
    assert [r["unit"] for r in data["services"]] == ["proc:pocket"]


def test_h_with_no_sound_server_the_line_is_plain(pack):
    assert zi.mic_view(lambda *a, **k: "")["line"] == "Microphone: the computer's default input."
