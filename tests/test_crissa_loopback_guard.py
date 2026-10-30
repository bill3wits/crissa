"""No shipped file names a loopback address the port table does not give it (the User 2026-10-10).

At 05:35:17 on 2026-10-10 a trial ran the pack on the reference machine with every port moved by +10000, host network,
--no-camera - and the presence service's door-first start sent /v1/facelock and /v1/return_door to the reference
install's live camera on 8192. The service is the house's rail copied whole, and every neighbour it named was the
house's own port. That is a class: any copied file whose default address is a fixed port talks to whatever else is on
this machine. So this file scans every file a public package ships (the exporter's own resolution: roots, lib, files,
vendor sources, the face files) for a loopback literal with a port, and fails on each one that is not routed through
lib/crissa_ports.py, the ONE table. Routed means one of:

  * no literal at all: the code asks the table (lib/crissa_ports, or a module's inline copy of it - a module installs
    on its own - which test_every_inline_copy_of_the_table_answers_as_the_table holds equal, rule for rule);
  * the literal is the default of a variable the launcher (scripts/crissa.py) pins for every child from the table,
    which test_the_launcher_pins_every_key_to_this_install proves under an offset;
  * the line is an inline copy of one table entry (it names CRISSA_PORT_<NAME> and the number is the table's).

What is left is either ALLOW (documented, each with its reason) or OWED (a real case in a file outside this job's
reach, each with what it needs; the list may only shrink: test_every_listed_case_is_still_there fails on a stale row).
Documentation never counts: comments and docstrings in Python, .md / .txt files, comment lines elsewhere.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import re
import sys
import tokenize
from pathlib import Path

from _kit import kit  # the kit's copy of a shipped file (the Crissa merge, 2026-10-11)
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_ports  # noqa: E402

REFERENCE = {8000, 8005, 8024, 8088, 8090, 8094, 8096, 8099, 8100, 8124, 8184, 8185, 8186, 8187, 8188, 8189, 8190,
             8191, 8192, 8193, 8194, 8195, 8196, 8197, 8199, 8213, 8219, 7789, 8097}
OFFSET = 10000

# ---- the scan ----------------------------------------------------------------------------------------------------
LOOP = re.compile(r"(?:127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\]):(\d{2,5})")
PORTISH = (
    re.compile(r"""get\(\s*["'][A-Z0-9_]*PORT[A-Z0-9_]*["']\s*,\s*["']?(\d{4,5})"""),
    re.compile(r"""--port["'][^)\n]*default\s*=\s*(\d{4,5})"""),
    re.compile(r"""\bport\s*(?::\s*int)?\s*=\s*(\d{4,5})\b"""),
    re.compile(r"""\b[A-Z_]*PORT\s*=\s*(\d{4,5})\b"""),
    re.compile(r"""\.port\s*or\s*(\d{4,5})"""),
    re.compile(r"""location\.port\s*===?\s*['"](\d{4,5})"""),
)
KEY = re.compile(r"""get\(\s*["']([A-Z][A-Z0-9_]+)["']""")
DOC_SUFFIX = (".md", ".txt", ".rst")


def _exporter():
    spec = importlib.util.spec_from_file_location("crissa_export_for_guard", ROOT / "scripts" / "crissa-export.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def shipped_files() -> list[str]:
    """Every file a public package ships, by the exporter's own resolution (resolve_code + the face files + vendor)."""
    ex = _exporter()
    pkgs = json.loads((ROOT / "config" / "crissa-packages.json").read_text(encoding="utf-8"))["packages"]
    out: set[str] = set()
    for name, faces in ex.FACES.items():
        pkg = pkgs.get(name) or {}
        if not pkg.get("public"):
            continue
        code, _ = ex.resolve_code(pkgs, name)
        out |= set(code) | (set(ex.FACE_FILES) if faces else set()) | set(pkg.get("vendor") or {})
    return sorted(out)


def _doc_lines(text: str) -> set[int]:
    out: set[int] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0], "value", None), ast.Constant) \
                    and isinstance(b[0].value.value, str):
                out |= set(range(b[0].lineno, b[0].end_lineno + 1))
    return out


def _comment_cols(text: str) -> dict[int, int]:
    cols: dict[int, int] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                cols.setdefault(tok.start[0], tok.start[1])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return cols


def scan(rel: str) -> list[dict]:
    p = kit(rel)
    if p.suffix in DOC_SUFFIX or not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    is_py = p.suffix == ".py" or text.startswith("#!/usr/bin/env python")
    docs = _doc_lines(text) if is_py else set()
    com = _comment_cols(text) if is_py else {}
    lines = text.splitlines()
    hits, seen = [], set()
    for i, line in enumerate(lines, 1):
        if i in docs:
            continue
        code = line[:com[i]] if i in com else line
        s = code.strip()
        if not is_py and s.startswith(("//", "#", "<!--", "*")):
            continue
        for rx in (LOOP,) + PORTISH:
            for m in rx.finditer(code):
                port = int(m.group(1))
                if (i, port) in seen:
                    continue
                seen.add((i, port))
                back = "\n".join(lines[max(0, i - 4):i])          # the statement the literal closes (a default)
                hits.append({"file": rel, "line": i, "port": port, "text": line.strip(), "context": back})
    return hits


def all_hits() -> list[dict]:
    out = []
    for rel in shipped_files():
        out += scan(rel)
    return out


# ---- what may stay ------------------------------------------------------------------------------------------------
# (file, port, a piece of the line) -> why it is not an address this install should take from the table
ALLOW = {
    ("scripts/phone-app-server.py", 8190, 'return "http://127.0.0.1:8190/v1/phone-mic"'):
        "the phone server's fallback when lib/crissa_ports does not import; the route is crissa_ports.port('stt') one "
        "line above, and 8190 is its DEFAULTS entry (round 35, the phone mic b3e2f1c55)",
    ("scripts/phone-mic-standin.py", 8226, 'ap.add_argument("--url", default="http://127.0.0.1:8226/api/mic")'):
        "a stdlib-only hand tool for .101's phone-mic proof (round 35): its default is the phone app's DEFAULTS port, "
        "--url overrides it",
    ("scripts/phone-mic-standin.py", 8190, 'ap.add_argument("--ear-status", default="http://127.0.0.1:8190/v1/ear/status")'):
        "the same hand tool's ear status default, the ear's DEFAULTS port; --ear-status overrides it",
    ("scripts/phone-app-server.py", 8100, 'return "http://127.0.0.1:8100"'):
        "the phone server's fallback when lib/crissa_ports does not import (the server run outside an install); the "
        "route is crissa_ports.port('player') one line above, and 8100 is its DEFAULTS entry (round 30, iPhone M5)",
    ("scripts/phone-app-server.py", 8096, 'return "http://127.0.0.1:8096/stt"'):
        "the phone server's fallback when lib/crissa_ports does not import; the route is crissa_ports.port('asr') one "
        "line above, and 8096 is its DEFAULTS entry (round 30, iPhone M4)",
    ("display/crissa-connect.html", 8000, "e.g. http://127.0.0.1:8000/v1"):
        "documentation: the Connect form's example address for the person's own engine",
    ("display/crissa-connect.html", 8000, '"local": {"url": "http://127.0.0.1:8000/v1"'):
        "the Connect form's 'local' kind: an address of the person's own engine they pick on the page (agent.json), "
        "not a Crissa piece; held equal to lib/crissa_agent.KINDS by tests/test_crissa_connect*",
    ("lib/crissa_agent.py", 8000, '"url": "http://127.0.0.1:8000/v1"'):
        "the same 'local' Connect kind (KINDS), the person's own engine, stored in agent.json when they pick it",
    ("lib/glass_mode.py", 9222, 'CDP = "http://127.0.0.1:9222/json/version"'):
        "the browser's DevTools port (an external program, Chromium's own default), not a Crissa piece",
    ("lib/house_wall_ascii.py", 9222, 'CRISSA_CDP = os.environ.get("HOUSE_WALL_CRISSA_CDP"'):
        "the browser's DevTools port (an external program), named by its own variable; not a Crissa piece",
    ("scripts/voiceonly-home-server.py", 9222, 'CDP_URL = os.environ.get("VO_CDP_URL"'):
        "the browser's DevTools port (an external program), named by its own variable; not a Crissa piece",
    ("lib/house_wall.py", 8100, 'return "http://127.0.0.1:8100"'):
        "routed: the ImportError fallback under crissa_ports.url('player') two lines up (the table's own number)",
    ("lib/house_wall_ascii.py", 8100, 'return "http://127.0.0.1:8100"'):
        "routed: the ImportError fallback under crissa_ports.url('player') two lines up (the table's own number)",
    ("lib/house_wall_ascii.py", 8199, 'open_url("http://127.0.0.1:8199/" if crissa_home.is_house()'):
        "the house's own branch (is_house()); a pack opens crissa_home.composer_url()",
    ("lib/crissa_presence.py", 8219, "DEFAULT_PORT = 8219"):
        "routed: the lamp's ImportError fallback under crissa_ports.port('presence') (the table's own number)",
    ("lib/crissa_presence.py", 8191, "HOUSE_PORT = 8191"):
        "not an address: the house service's port, named only so the lamp refuses to bind it",
    ("modules/ear/crissa_ear/dashboard.py", 8191, 'presence <a href="http://127.0.0.1:8191/"'):
        "page text kept as extracted and never served: crissa_ear/server.py answers / and /dashboard with the Wall "
        "(test_the_ear_pages_that_keep_house_text_are_never_served)",
    ("modules/ear/crissa_ear/omni_ui.py", 8190, 'data-url="http://127.0.0.1:8190/">dashboard'): "OMNI_HTML: never served",
    ("modules/ear/crissa_ear/omni_ui.py", 8191, 'data-url="http://127.0.0.1:8191/oracle">oracle'): "OMNI_HTML: never served",
    ("modules/ear/crissa_ear/omni_ui.py", 8191, 'data-url="http://127.0.0.1:8191/">presence'): "OMNI_HTML: never served",
    ("modules/ear/crissa_ear/omni_ui.py", 8190, "const OMNI = (location.port === '8190'"): "OMNI_HTML: never served",
    ("modules/ear/crissa_ear/omni_ui.py", 8190, "'http://127.0.0.1:8190/v1/omni?n=20'"): "OMNI_HTML: never served",
    ("modules/ear/crissa_ear/omni_ui.py", 8190, "const url = (location.port === '8190'"): "OMNI_HTML: never served",
    ("modules/ear/crissa_ear/omni_ui.py", 8190, "'http://127.0.0.1:8190/v1/turns'"): "OMNI_HTML: never served",
    ("modules/ear/crissa_ear/panel_ui.py", 8195, "let coachUrl = 'http://127.0.0.1:8195/'"): "PANEL_HTML: never served",
    ("modules/ear/crissa_ear/panel_ui.py", 8191, "window.open('http://127.0.0.1:8191/oracle'"): "PANEL_HTML: never served",
    ("modules/ear/crissa_ear/panel_ui.py", 8191, "fetch('http://127.0.0.1:8191/v1/reinit_scan'"): "PANEL_HTML: never served",
}

# Real cases outside this job's files (2026-10-10), each with what it needs. The list may only shrink.
# Moved to the table the same day (the Crissa rename pass): the feed's ACTIONS and probes (stt/bridge/enroll/asr from
# the table, presence/comfy/engines/voice from config/house-machine.json on the house), the home page's pip frame and
# FEED_BASE and both panel pages' DEMO_HOLD and player fallback (written in at serve time), the demo helper ('demo'),
# the demo's clone voice (house voice_url, else this install's turbo) and the chip ('chip', the house's chip_url).
# The machine-check job's seven (lib/crissa_init's coach fallback, init_wizard's engine probe, the id module's
# sequence and site example) moved to the table on 2026-10-10 with the short walk: the list is empty.
OWED: dict = {}


def _listed(hit: dict, table: dict):
    for (rel, port, piece), why in table.items():
        if rel == hit["file"] and port == hit["port"] and piece in hit["text"]:
            return why
    return None


# ---- the launcher's pins -----------------------------------------------------------------------------------------
def _launcher():
    spec = importlib.util.spec_from_file_location("crissa_launcher_for_guard", ROOT / "scripts" / "crissa.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _args(**kw):
    base = dict(face="crissa", audio_at="host", port_offset=OFFSET, voice=None, with_asr=False, with_aligner=False,
                no_camera=False, no_enroll=False, no_desk=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _plan(monkeypatch=None, **kw):
    z = _launcher()
    if monkeypatch is not None:
        monkeypatch.setattr(z, "presence_module_installed", lambda pmod: True)
    return z.plan(_args(**kw), env_in={})


def pinned_keys() -> set[str]:
    z = _launcher()
    z.presence_module_installed = lambda pmod: True
    keys: set[str] = set()
    for p in (z.plan(_args(), env_in={}), z.plan(_args(no_camera=True), env_in={})):
        for k in p["children"]:
            keys |= set(k["env"])
    return keys


def default_keys(hit: dict) -> list[str]:
    """The variables whose default IS this literal: `get("A", get("B", "http://127.0.0.1:N..."))` -> [A, B]."""
    flat = " ".join(hit["context"].split("\n"))
    rx = re.compile(r"""((?:get\(\s*["'][A-Z][A-Z0-9_]+["']\s*,\s*(?:os\.environ\.)?)+)["']?(?:https?://)?"""
                    r"""(?:127\.0\.0\.1:)?%d\b""" % hit["port"])
    m = None
    for m in rx.finditer(flat):
        pass
    return KEY.findall(m.group(1)) if m else []


def _routed_by_a_pinned_key(hit: dict, pinned: set[str]) -> bool:
    return any(k in pinned for k in default_keys(hit))


def _inline_table_entry(hit: dict) -> bool:
    for name, n in crissa_ports.DEFAULTS.items():
        if "CRISSA_PORT_" + name.upper() in hit["context"] and hit["port"] == n:
            return True
    return False


# ---- the tests ---------------------------------------------------------------------------------------------------
def test_the_shipped_set_is_the_exporters():
    files = shipped_files()
    assert len(files) > 300, len(files)
    for rel in ("modules/presence/crissa_presence/service.py", "modules/ear/crissa_bridge/ear_bridge.py",
                "modules/oracle/crissa_oracle/camera.py", "modules/presence/crissa_presence/ports.py",
                "scripts/crissa.py", "lib/crissa_ports.py", "scripts/voiceonly-home-server.py"):
        assert rel in files, rel


def test_no_shipped_file_names_a_loopback_port_the_table_does_not_give_it():
    pinned = pinned_keys()
    bad = []
    for h in all_hits():
        if _listed(h, ALLOW) or _listed(h, OWED):
            continue
        if _routed_by_a_pinned_key(h, pinned) or _inline_table_entry(h):
            continue
        bad.append("%s:%d (%d): %s" % (h["file"], h["line"], h["port"], h["text"][:140]))
    assert not bad, "a loopback address not from lib/crissa_ports (route it, or list it with a reason):\n" + "\n".join(bad)


def test_every_listed_case_is_still_there():
    hits = all_hits()
    stale = [k for table in (ALLOW, OWED) for k in table
             if not any(h["file"] == k[0] and h["port"] == k[1] and k[2] in h["text"] for h in hits)]
    assert not stale, "these rows no longer match a line (fixed? then drop the row): %s" % stale


def test_the_launcher_pins_every_key_to_this_install(monkeypatch):
    """Under an offset no child is handed a reference port, every ADDRESS_KEYS variable reaches every child from the
    table, and a value the caller already set wins."""
    z = _launcher()
    monkeypatch.setattr(z, "presence_module_installed", lambda pmod: True)
    p = z.plan(_args(), env_in={})
    ref = re.compile(r":(%s)\b" % "|".join(str(n) for n in sorted(REFERENCE)))
    for k in p["children"]:
        for key, val in k["env"].items():
            if isinstance(val, str) and "127.0.0.1:" in val:
                assert not ref.search(val), (k["name"], key, val)
        for key in crissa_ports.ADDRESS_KEYS:
            assert key in k["env"], (k["name"], key)
    feed = next(k for k in p["children"] if k["name"] == "feed")
    assert feed["env"]["VO_PRESENCE_URL"] == "http://127.0.0.1:18219/v1/presence"
    assert feed["env"]["HOUSE_WALL_CONDUCTOR"] == "http://127.0.0.1:18100"
    mine = z.plan(_args(), env_in={"CRISSA_PLAYER": "http://10.0.0.5:9000"})
    assert all("CRISSA_PLAYER" not in k["env"] for k in mine["children"]), "the caller's own address wins (inherited)"


def test_the_pinned_defaults_are_named():
    """Which keyed defaults stand on the launcher's pins: printed, so the report and a reader see the same list."""
    pinned = pinned_keys()
    used = sorted({(h["file"], k) for h in all_hits() for k in default_keys(h) if k in pinned})
    assert used, "the guard found no keyed default at all: the scan is broken"
    print("\n".join("%s  %s" % u for u in used))


def _by_path(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _modules_inline_tables():
    """The module copies of the table that a package imports (they install on their own). Loaded without leaving
    anything behind: crissa_id.paths exports HOUSE_ID_ROOT into the process on import, so the environment is put back."""
    import os
    saved = dict(os.environ)
    out = [("crissa_presence.ports", _by_path("guard_presence_ports", "modules/presence/crissa_presence/ports.py")),
           ("crissa_id.paths", _by_path("guard_id_paths", "modules/id/crissa_id/paths.py"))]
    os.environ.clear()
    os.environ.update(saved)
    sys.path.insert(0, str(ROOT / "modules" / "ear"))
    try:
        for mod in ("crissa_ear.settings", "crissa_bridge.settings"):
            out.append((mod, importlib.import_module(mod)))
    finally:
        sys.path.remove(str(ROOT / "modules" / "ear"))
    return out


def test_every_inline_copy_of_the_table_answers_as_the_table(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "home"))
    envs = [{}, {"CRISSA_PORT_OFFSET": "10000"}, {"CRISSA_PORT_OFFSET": "nonsense"},
            {"HOUSE_CONDUCTOR_PORT": "8150", "CRISSA_PORT_OFFSET": "7"}, {"CRISSA_ORACLE_PORT": "8297"}]
    for mod_name, mod in _modules_inline_tables():
        table = getattr(mod, "_DEFAULT_PORTS", None) or getattr(mod, "DEFAULT_PORTS")
        for name, n in table.items():
            assert crissa_ports.DEFAULTS.get(name) == n, (mod_name, name, n)
            for env in envs + [{"CRISSA_PORT_" + name.upper(): "9123"}]:
                assert mod.port(name, env) == crissa_ports.port(name, env), (mod_name, name, env)
    # the scripts that install alone repeat the numbers only (tests/test_crissa_wall_page.py holds the rest)
    for rel in ("modules/oracle/scripts/crissa-oracle.py", "modules/id/scripts/crissa-id.py"):
        src = (ROOT / rel).read_text()
        got = dict((k, int(v)) for k, v in re.findall(r'"(\w+)": (\d+)', re.search(r"_DEFAULT_PORTS = \{[^}]*\}", src).group(0)))
        assert got == {k: crissa_ports.DEFAULTS[k] for k in got}, rel


def test_the_full_copies_are_the_whole_table():
    for mod_name, mod in _modules_inline_tables():
        if mod_name == "crissa_id.paths":
            continue
        table = getattr(mod, "_DEFAULT_PORTS", None) or getattr(mod, "DEFAULT_PORTS")
        assert table == crissa_ports.DEFAULTS, mod_name


def test_the_id_module_names_the_ear_bridge_as_the_table_does():
    _, paths = [t for t in _modules_inline_tables() if t[0] == "crissa_id.paths"][0]
    assert paths.ear_bridge_url() == crissa_ports.url("bridge")
    assert paths.port("bridge", {"CRISSA_PORT_BRIDGE": "18194"}) == 18194, "the launcher's own variable reaches it"


def test_the_ear_pages_that_keep_house_text_are_never_served():
    src = (ROOT / "modules" / "ear" / "crissa_ear" / "server.py").read_text()
    assert not re.search(r"_html\([^)]*(DASHBOARD_HTML|OMNI_HTML|PANEL_HTML)", src)
    assert "OMNI_HTML" not in src and "PANEL_HTML" not in src
    for route in ('("/", "/dashboard")', '("/omni", "/v1/omni/ui")'):
        i = src.index(route)
        assert "self._gone(_glass_url())" in src[i:i + 120], route


def test_the_port_table_carries_the_render_modules_comfy_on_its_own_number():
    assert crissa_ports.DEFAULTS["comfy"] not in [v for k, v in crissa_ports.DEFAULTS.items() if k != "comfy"]
    sys.path.insert(0, str(ROOT / "modules" / "render"))
    try:
        from crissa_render import settings
        cfg = settings.load(path=ROOT / "nonexistent.json", env={"CRISSA_PORT_OFFSET": "10000"})
        assert cfg["comfy_url"] == "http://127.0.0.1:18184"
    finally:
        sys.path.remove(str(ROOT / "modules" / "render"))
