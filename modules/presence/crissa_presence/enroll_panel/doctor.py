"""Preflight checks for enroll on this office."""

from __future__ import annotations

import json
import subprocess
import urllib.request
from typing import Callable

from .paths import house_id_root, site_config_path
from .site import Site, load_site


def _ok(msg: str) -> tuple[bool, str]:
    return True, msg


def _bad(msg: str) -> tuple[bool, str]:
    return False, msg


def check_site_file() -> tuple[bool, str]:
    p = site_config_path()
    if not p.is_file():
        return _bad(f"missing {p} — run: python -m crissa_presence.enroll_panel init-site")
    try:
        load_site()
        return _ok(f"site.json ok ({p})")
    except Exception as e:
        return _bad(f"site.json load error: {e}")


def check_mic(site: Site) -> tuple[bool, str]:
    mic = site.mic
    if not mic:
        return _bad("audio.mic empty in site.json")
    r = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True)
    sources = r.stdout or ""
    if mic in sources:
        return _ok(f"mic present: {mic}")
    return _bad(f"mic not in pactl sources: {mic}\n  have:\n{sources[:500]}")


def check_sink(site: Site) -> tuple[bool, str]:
    r = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True)
    default = (r.stdout or "").strip()
    want = site.sink or default
    if not want:
        return _bad("no sink (set audio.sink or pactl default)")
    return _ok(f"sink={want} (default={default or 'none'})")


def check_cosy(site: Site) -> tuple[bool, str]:
    if not site.speak_enabled:
        return _ok("speak disabled (HOUSE_ENROLL_QUIET or site)")
    base = site.speak_url.rsplit("/", 1)[0]
    for url in (f"{base}/health", base):
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                body = resp.read()[:200]
            return _ok(f"cosy reachable {url} {body[:80]!r}")
        except Exception:
            continue
    return _bad(f"cosy not reachable ({site.speak_url}) — spoken cues fall back to spd-say/print")


def check_presence(site: Site) -> tuple[bool, str]:
    url = f"{site.presence_url.rstrip('/')}/v1/presence"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            d = json.loads(resp.read().decode())
        return _ok(f"presence hc={d.get('headcount')} trust={d.get('count_trust')}")
    except Exception as e:
        return _bad(f"presence down: {e}")


def check_camera(site: Site) -> tuple[bool, str]:
    url = f"{site.camera_url.rstrip('/')}/health"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            resp.read()
        return _ok(f"camera health ok {site.camera_url}")
    except Exception as e:
        return _bad(f"camera down: {e}")


def check_ecapa() -> tuple[bool, str]:
    import sys
    from pathlib import Path

    tools = Path(__file__).resolve().parent.parent
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        import ecapa_embed

        if ecapa_embed.available():
            return _ok("ECAPA encoder loadable")
        return _bad(f"ECAPA unavailable: {ecapa_embed._err}")
    except Exception as e:
        return _bad(f"ECAPA import fail: {e}")


def check_stops(site: Site) -> tuple[bool, str]:
    p = site.stops_json()
    if p is None:
        return _bad("room_refs.stops_json not set (needed for room-refs)")
    if not p.is_file():
        return _bad(f"stops_json missing: {p}")
    try:
        cfg = json.loads(p.read_text())
        n = len(cfg.get("stops") or [])
        return _ok(f"stops_json {p.name} stops={n}")
    except Exception as e:
        return _bad(f"stops_json bad: {e}")


def run_doctor(*, deep: bool = False) -> int:
    print(f"HOUSE_ID_ROOT={house_id_root()}")
    checks: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
        ("site", check_site_file),
    ]
    site: Site | None = None
    try:
        site = load_site()
        print(f"site_id={site.site_id}  display={site.display}")
    except Exception as e:
        print(f"FAIL site load: {e}")
        return 1

    checks.extend(
        [
            ("mic", lambda: check_mic(site)),
            ("sink", lambda: check_sink(site)),
            ("speak", lambda: check_cosy(site)),
            ("ecapa", check_ecapa),
            ("presence", lambda: check_presence(site)),
            ("camera", lambda: check_camera(site)),
            ("stops", lambda: check_stops(site)),
        ]
    )

    bad = 0
    for name, fn in checks:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, str(e)
        mark = "OK  " if ok else "FAIL"
        if not ok:
            bad += 1
        print(f"  {mark} {name}: {msg}")
    print(f"\n{'ready' if bad == 0 else f'{bad} issue(s)'} for enroll @ {site.site_id}")
    return 0 if bad == 0 else 1
