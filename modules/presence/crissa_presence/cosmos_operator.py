"""Cosmos-as-operator: tool control of camera/STT over HTTP, chat TUI helpers.

Scripted auto PTZ (facefind/refind/look-around timers) is off when OPERATOR_MODE.
Cosmos does not open /dev/video0 — sole owner remains rail-camera :8192.
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths
from .room_heading import door_home_pose

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .fsio import atomic_write_text, append_jsonl_capped

# report history cap (2026-07-29 review: line 927 grew forever)
REPORT_HISTORY_MAX_BYTES = int(os.environ.get("RAIL_COSMOS_REPORT_HISTORY_MAX_BYTES", str(5 * 1024 * 1024)))
REPORT_HISTORY_KEEP = int(os.environ.get("RAIL_COSMOS_REPORT_HISTORY_KEEP", "500"))

# --- mode flags ---

def operator_mode_enabled() -> bool:
    v = os.environ.get("RAIL_COSMOS_OPERATOR", os.environ.get("RAIL_ORACLE_OPERATOR", "0"))
    return str(v).strip().lower() not in ("0", "false", "no", "off", "")


def operator_flags() -> dict[str, Any]:
    """Config snapshot for status / tests (no I/O)."""
    return {
        "operator_mode": operator_mode_enabled(),
        "auto_camera_probes": not operator_mode_enabled(),
        "refind_disabled": True if operator_mode_enabled() else None,
        "auto_look_around_disabled": True if operator_mode_enabled() else None,
        "driver_expected": "cosmos" if operator_mode_enabled() else None,
        "camera_http": os.environ.get(
            "RAIL_PRESENCE_SNAPSHOT_URL", _ports.camera("/snapshot")
        ).rsplit("/", 1)[0]
        or _ports.camera(),
        "stt_http": os.environ.get("RAIL_ORACLE_STT", _ports.url("stt", "/stt")).replace(
            "/stt", ""
        ),
        "kit_dir": str(default_kit_dir()),
        "report_path": str(default_report_path()),
        "note": (
            "Camera tools call rail-camera HTTP only; V4L sole_owner stays on camera service."
            if operator_mode_enabled()
            else "Operator mode off — scripted oracle probes may run per RAIL_ORACLE_*."
        ),
    }


def default_kit_dir() -> Path:
    return Path(
        os.environ.get(
            "RAIL_COSMOS_KIT",
            str(
                _paths.DATA / "cosmos-operator"
            ),
        )
    ).expanduser()


def default_report_path() -> Path:
    return Path(
        os.environ.get(
            "RAIL_COSMOS_REPORT",
            str(default_kit_dir() / "room_speaker_report.json"),
        )
    ).expanduser()


def default_history_path() -> Path:
    return default_kit_dir() / "chat_history.jsonl"


def default_error_log_path() -> Path:
    return Path(
        os.environ.get(
            "RAIL_COSMOS_ERROR_LOG",
            str(default_kit_dir() / "errors.jsonl"),
        )
    ).expanduser()


# --- tool surface ---

CAMERA_BASE = os.environ.get("RAIL_CAMERA_HTTP", _ports.camera())
STT_URL = os.environ.get("RAIL_ORACLE_STT", _ports.url("stt", "/stt"))
PRESENCE_URL = os.environ.get(
    "RAIL_PRESENCE_URL", os.environ.get("RAIL_PRESENCE", _ports.url("presence", "/v1/presence"))
)

# Door home for this house (entrance watch). Never firmware pan 0.
DOOR_HOME_PAN = int(door_home_pose()["pan"])   # measured in Setup, else the camera's forward (room_heading)

# Kit files the model may read via tools (relative to kit dir).
KIT_READABLE: dict[str, str] = {
    "SKILL.md": "Operator skill: scan room, STT, door watch, error logging",
    "CONTROL-SURFACE.md": "HTTP camera/STT control surface",
    "README.md": "Kit index",
    "camera-stops.json": "Named PTZ stops (door/desk/chair/wrap)",
    "docs/FIRMWARE-INSTA360-LINK2.md": "Link 2 axes, door home, facefinder",
    "docs/camera-control-reference.md": "Full PTZ reference",
    "docs/CAMERA-CONTROL-OPTIONS.md": "Control options notes",
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "camera_health",
        "description": "Camera service health (sole_owner, pan, buffer).",
        "params": {},
    },
    {
        "name": "snapshot",
        "description": "Grab current door/camera JPEG via HTTP; saves last_snapshot.jpg.",
        "params": {},
    },
    {
        "name": "buffer_meta",
        "description": "Ring buffer metadata (seq, length) without full JPEGs.",
        "params": {},
    },
    {
        "name": "aim",
        "description": "PTZ aim absolute pan/tilt. Door/entrance home is the taught door pan (door_home_pan in the state).",
        "params": {"pan": "int", "tilt": "int optional", "settle_sec": "float optional"},
    },
    {
        "name": "return_door",
        "description": "Release facefinder and hold entrance (the taught door home).",
        "params": {"settle_sec": "float optional"},
    },
    {
        "name": "facefinder",
        "description": "Firmware face-lock on/off. Always OFF after use; never free-run.",
        "params": {"enable": "bool"},
    },
    {
        "name": "look_around",
        "description": (
            "On-demand room scan (desk/chair) with facefinder, then auto return to door. "
            "Use when inventory is uncertain; rest state is always entrance/door."
        ),
        "params": {
            "stop_ids": "list optional e.g. [desk,chair]",
            "engage_sec": "float optional",
        },
    },
    {
        "name": "listen_stt",
        "description": "HTTP POST rail-stt /v1/listen (sole mic watcher = Link). No local device pick.",
        "params": {"seconds": "float optional default 3"},
    },
    {
        "name": "list_kit",
        "description": "List readable kit docs (SKILL, firmware, stops). No root needed.",
        "params": {},
    },
    {
        "name": "read_kit_doc",
        "description": "Read a kit doc by relative path (e.g. SKILL.md, docs/FIRMWARE-INSTA360-LINK2.md).",
        "params": {"path": "str required", "max_chars": "int optional default 12000"},
    },
    {
        "name": "list_stops",
        "description": "Named camera pan stops (door = the taught entrance, desk, chair, wrap_*).",
        "params": {},
    },
    {
        "name": "log_error",
        "description": (
            "Append an operator error to kit errors.jsonl with your suspected reason. "
            "Call whenever a tool fails, vision is unusable, STT is silent, or you are stuck."
        ),
        "params": {
            "error": "str required — what failed",
            "suspected_reason": "str required — your best guess why",
            "tool": "str optional",
            "context": "str optional",
        },
    },
    {
        "name": "rails_inventory",
        "description": (
            "Read live RoomPresence sticky inventory (script body count). "
            "Door FOV empty does NOT mean empty room — trust this for who is present."
        ),
        "params": {},
    },
    {
        "name": "sync_report_from_rails",
        "description": (
            "Publish room report from rails sticky inventory (correct empty CosmOS report). "
            "Optional speaking override."
        ),
        "params": {
            "speaking": "{id, text?, source?} or null optional",
            "note": "string optional",
        },
    },
    {
        "name": "publish_report",
        "description": "Publish who-is-in-room and who-is-speaking for house consumers.",
        "params": {
            "in_room": "list of {id, certain?, location?} or name strings",
            "speaking": "{id, text?, source?} or null",
            "note": "string optional",
        },
    },
]


def tool_catalog_text() -> str:
    lines = ["Available tools (call with a JSON line TOOL: {...}):"]
    for t in TOOL_SPECS:
        lines.append(f"- {t['name']}: {t['description']} params={t['params']}")
    lines.append(
        'Example: TOOL: {"name":"rails_inventory","args":{}}\n'
        'Example: TOOL: {"name":"sync_report_from_rails","args":{}}\n'
        'Example: TOOL: {"name":"look_around","args":{"stop_ids":["desk","chair"]}}\n'
        'Example: TOOL: {"name":"return_door","args":{}}\n'
        'Example: TOOL: {"name":"publish_report","args":{"in_room":[{"id":"user"}],'
        '"speaking":{"id":"user","text":"hi","source":"voice"}}}'
    )
    return "\n".join(lines)


def fetch_rails_inventory() -> dict[str, Any]:
    """Live RoomPresence sticky inventory (script path). HTTP only."""
    raw = _http_json(PRESENCE_URL, timeout=4)
    if not isinstance(raw, dict):
        return {"ok": False, "error": "bad presence response", "headcount": 0, "people": []}
    if raw.get("error") and "people" not in raw:
        return {
            "ok": False,
            "error": str(raw.get("error"))[:200],
            "headcount": 0,
            "people": [],
        }
    people_out: list[dict[str, Any]] = []
    for p in raw.get("people") or []:
        if not isinstance(p, dict):
            continue
        name = p.get("name") or p.get("id") or p.get("person_id")
        if not name:
            continue
        src = p.get("sources") if isinstance(p.get("sources"), dict) else {}
        people_out.append(
            {
                "id": str(name).strip().lower(),
                "certain": p.get("certain"),
                "location": p.get("seat") or src.get("location") or "in_room",
                "at_door": src.get("at_door"),
                "last_seen_ago": src.get("last_seen_ago"),
                "gender": p.get("gender"),
            }
        )
    return {
        "ok": True,
        "headcount": int(raw.get("headcount") or len(people_out)),
        "count_certain": raw.get("count_certain"),
        "count_trust": raw.get("count_trust"),
        "camera_mode": raw.get("camera_mode"),
        "door_names": raw.get("door_names") or [],
        "people": people_out,
        "ts_local": raw.get("ts_local"),
        "note": (
            "Rails sticky inventory is body truth for enter-once/stay. "
            "Empty door FOV is normal while people sit. Do not publish empty "
            "while rails headcount>0 unless you have exit evidence."
        ),
    }


def rails_inventory_block() -> str:
    inv = fetch_rails_inventory()
    if not inv.get("ok"):
        return f"RAILS_INVENTORY: unavailable ({inv.get('error') or 'error'})"
    people = inv.get("people") or []
    names = ", ".join(
        f"{p.get('id')}{'*' if p.get('certain') else '?'}" for p in people
    ) or "(none)"
    return (
        f"RAILS_INVENTORY (script sticky — trust for who is present):\n"
        f"- headcount={inv.get('headcount')} trust={inv.get('count_trust')} "
        f"certain={inv.get('count_certain')} mode={inv.get('camera_mode')}\n"
        f"- people: {names}\n"
        f"- door_names_now: {inv.get('door_names')}\n"
        f"- rule: empty door snapshot ≠ empty room when headcount>0; "
        f"use sync_report_from_rails or publish those ids."
    )


def sync_report_from_rails(
    *,
    speaking: Any = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Publish CosmOS room report from rails sticky inventory."""
    inv = fetch_rails_inventory()
    if not inv.get("ok"):
        return {"ok": False, "error": inv.get("error") or "rails inventory failed", "inventory": inv}
    in_room = [
        {
            "id": p["id"],
            "certain": p.get("certain"),
            "location": p.get("location") or "in_room",
        }
        for p in (inv.get("people") or [])
        if p.get("id")
    ]
    note_s = note or (
        f"synced from rails sticky headcount={inv.get('headcount')} "
        f"trust={inv.get('count_trust')}"
    )
    report = build_room_speaker_report(
        in_room=in_room,
        speaking=speaking,
        note=note_s,
        source="rails_sync",
    )
    path = publish_report(report)
    return {
        "ok": True,
        "path": str(path),
        "report": report,
        "inventory": inv,
    }


def _embedded_skill_excerpt(kit: Path, *, max_chars: int = 3500) -> str:
    """Inline SKILL.md so the model has door/STT rules without a tool call."""
    skill = kit / "SKILL.md"
    if not skill.is_file():
        return (
            "SKILL.md not yet on disk — follow DOOR WATCH + ERROR LOG rules in this brief."
        )
    try:
        text = skill.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"(could not read SKILL.md: {e})"
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…(truncated; use read_kit_doc)\n"
    return text


def capability_job_brief(*, kit_dir: Path | None = None) -> str:
    """First-turn system ask: AV capability + standing job."""
    kit = kit_dir or default_kit_dir()
    skill_excerpt = _embedded_skill_excerpt(kit, max_chars=1800)
    rails_block = rails_inventory_block()
    return f"""You are Cosmos3-Edge, the house room Oracle operator for this session.

## HARD LAW — DOOR FRAME IS NOT THE ROOM
- The default camera rest view is the **DOOR / ENTRANCE only** (pan={DOOR_HOME_PAN}).
- People **sit at desk/chair OUT OF the door frame**. An empty door picture is NORMAL.
- You MUST NOT invent "empty room" from the door JPEG alone.
- **Who is in the room** = RAILS_INVENTORY (script sticky) first, always.
- **Who is speaking** = listen_stt (or null if silent).
- **Appearance / seats** = look_around desk+chair (then return_door). Not door snapshot.
- If rails headcount>0 and door looks empty → people are still in the room. Report them.

{rails_block}

CONTROL (HTTP only — no /dev/video0, no root):
- Camera sole-owner: {CAMERA_BASE}
- Kit: {kit}
- After look_around/aim → return_door (entrance rest).

TOOLS YOU MUST USE FOR FULL ROOM:
1) rails_inventory or sync_report_from_rails — body count / names
2) look_around stop_ids=["desk","chair"] — see seats (NOT door-only)
3) listen_stt — speech
4) publish_report — never empty while rails headcount>0 without leave evidence

ERROR LOGGING: log_error with error= + suspected_reason= on failures.

STANDING JOB: who-in-room (rails) + who-speaking (STT). Door frame is enter/leave watch only.

FIRST REPLY: VIDEO yes (tools), AUDIO honest, YES I CAN DO THE JOB; then sync_report_from_rails if headcount>0.

--- SKILL (excerpt) ---
{skill_excerpt}
--- end ---

{tool_catalog_text()}
"""

def _extract_balanced_json_objects(text: str, *, start_at: int = 0) -> list[str]:
    """Find top-level {...} objects with balanced braces (for nested tool args)."""
    out: list[str] = []
    i = start_at
    while i < len(text):
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(text)):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    out.append(text[i : j + 1])
                    i = j + 1
                    break
        else:
            break
    return out


def parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """Extract TOOL: {json} (nested args OK) or fenced tool JSON from model text."""
    if not text:
        return []
    found: list[dict[str, Any]] = []
    for m in re.finditer(r"(?i)(?:TOOL|Tool)\s*:\s*\{", text):
        # re-find object starting at brace
        objs = _extract_balanced_json_objects(text, start_at=m.end() - 1)
        candidates = list(objs)
        if not candidates:
            # model often truncates closing braces — salvage from TOOL: { to end
            frag = text[m.end() - 1 :].strip()
            for _ in range(4):
                try:
                    obj = json.loads(frag)
                    if isinstance(obj, dict):
                        candidates = [frag]
                    break
                except Exception:
                    frag = frag + "}"
        for raw in candidates[:1]:
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict) and obj.get("name"):
                    found.append(obj)
            except Exception:
                continue
    for m in re.finditer(r"```(?:tool|json)\s*", text, flags=re.IGNORECASE):
        objs = _extract_balanced_json_objects(text, start_at=m.end())
        for raw in objs[:2]:
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict) and obj.get("name") and obj not in found:
                    found.append(obj)
            except Exception:
                continue
    return found


def _http_json(url: str, *, method: str = "GET", body: bytes | None = None, timeout: float = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if not raw:
                return {"ok": True, "empty": True}
            # snapshot returns jpeg
            ct = r.headers.get("Content-Type") or ""
            if "json" in ct or raw[:1] in (b"{", b"["):
                return json.loads(raw.decode())
            return {
                "ok": True,
                "content_type": ct,
                "bytes": len(raw),
                "note": "binary response",
            }
    except urllib.error.HTTPError as e:
        try:
            return {"ok": False, "error": e.read().decode()[:400], "status": e.code}
        except Exception:
            return {"ok": False, "error": str(e), "status": e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def _http_bytes(url: str, timeout: float = 15) -> tuple[bytes | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read(), r.headers.get("Content-Type") or ""
    except Exception as e:
        return None, str(e)[:200]


def _strip_heavy_fields(obj: Any, *, depth: int = 0) -> Any:
    """Drop jpeg_b64 / huge blobs so tool results fit in the model context."""
    if depth > 8:
        return None
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in ("jpeg_b64", "image_b64", "b64", "data_url"):
                out[k] = f"<stripped len={len(v) if isinstance(v, str) else '?'} chars>"
                continue
            if lk in ("jpeg", "image_bytes") and isinstance(v, (bytes, bytearray)):
                out[k] = f"<stripped {len(v)} bytes>"
                continue
            out[k] = _strip_heavy_fields(v, depth=depth + 1)
        return out
    if isinstance(obj, list):
        return [_strip_heavy_fields(x, depth=depth + 1) for x in obj[:40]]
    if isinstance(obj, str) and len(obj) > 4000:
        return obj[:4000] + "…(truncated)"
    return obj


def log_operator_error(
    *,
    error: str,
    suspected_reason: str,
    tool: str | None = None,
    context: str | None = None,
    source: str = "cosmos_tool",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append structured error + hypothesis to kit errors.jsonl."""
    path = default_error_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row: dict[str, Any] = {
        "schema": "cosmos_operator_error/v1",
        "ts": time.time(),
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "error": (error or "")[:800],
        "suspected_reason": (suspected_reason or "")[:800],
        "tool": tool,
        "context": (context or "")[:800] if context else None,
    }
    if extra:
        row["extra"] = _strip_heavy_fields(extra)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
    return {"ok": True, "path": str(path), "logged": row}


def _guess_tool_failure_reason(name: str, res: dict[str, Any]) -> str:
    err = str(res.get("error") or res.get("note") or "unknown")
    low = err.lower()
    if name == "listen_stt":
        if "no mic" in low or "pcm" in low:
            return "No PCM from parec — mic silent, wrong source, or peak≈0 (Insta360 often dead)"
        return f"STT path failed: {err[:200]}"
    if name == "look_around":
        return f"look_around failed or partial — check rail-camera sole_owner / facefinder: {err[:200]}"
    if name in ("aim", "return_door", "facefinder", "snapshot", "camera_health"):
        return f"Camera HTTP {CAMERA_BASE} issue or sole_owner conflict: {err[:200]}"
    if name == "read_kit_doc":
        return f"Kit path missing or not allowlisted: {err[:200]}"
    return f"Tool {name} returned not-ok: {err[:200]}"


def _auto_log_if_failed(name: str, res: dict[str, Any]) -> None:
    if not isinstance(res, dict):
        return
    if res.get("ok") is False or (name == "look_around" and res.get("n_ok") == 0 and res.get("error")):
        try:
            log_operator_error(
                error=f"{name}: {res.get('error') or res.get('note') or 'failed'}",
                suspected_reason=_guess_tool_failure_reason(name, res),
                tool=name,
                context="auto_from_execute_tool",
                source="rails_auto",
                extra={"result_keys": list(res.keys())[:20]},
            )
        except Exception:
            pass


def execute_tool(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one allowlisted tool against house HTTP services / kit files."""
    args = args or {}
    name = (name or "").strip()
    base = CAMERA_BASE.rstrip("/")
    res: dict[str, Any]
    if name == "camera_health":
        res = _http_json(f"{base}/health", timeout=5)
    elif name == "snapshot":
        data, ct = _http_bytes(f"{base}/snapshot", timeout=10)
        if data is None:
            res = {"ok": False, "error": ct}
        else:
            out = default_kit_dir() / "last_snapshot.jpg"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            res = {
                "ok": True,
                "path": str(out),
                "bytes": len(data),
                "content_type": ct,
            }
    elif name == "buffer_meta":
        res = _http_json(f"{base}/v1/buffer/meta", timeout=5)
    elif name == "aim":
        body = json.dumps(
            {
                "pan": int(args.get("pan", DOOR_HOME_PAN)),
                "tilt": int(args.get("tilt", 0)),
                "settle_sec": float(args.get("settle_sec", 1.2)),
            }
        ).encode()
        res = _http_json(f"{base}/v1/aim", method="POST", body=body, timeout=30)
    elif name == "return_door":
        body = json.dumps(
            {"settle_sec": float(args.get("settle_sec", 1.6))}
        ).encode()
        res = _http_json(
            f"{base}/v1/return_door", method="POST", body=body, timeout=30
        )
    elif name == "facefinder":
        en = args.get("enable", args.get("on", False))
        en = bool(en) if not isinstance(en, str) else en.lower() in ("1", "true", "on", "yes")
        body = json.dumps({"enable": en}).encode()
        res = _http_json(
            f"{base}/v1/facelock", method="POST", body=body, timeout=15
        )
    elif name == "look_around":
        payload: dict[str, Any] = {
            "facefinder": True,
            "engage_sec": float(args.get("engage_sec", 1.5)),
            "include_jpeg": False,  # paths only — never flood context with b64
        }
        if args.get("stop_ids"):
            payload["stop_ids"] = args["stop_ids"]
        body = json.dumps(payload).encode()
        raw = _http_json(
            f"{base}/v1/look_around", method="POST", body=body, timeout=180
        )
        res = _strip_heavy_fields(raw) if isinstance(raw, dict) else {"ok": False, "error": "bad look_around"}
        # Compact frame list for the model
        if isinstance(res, dict) and isinstance(res.get("frames"), list):
            compact = []
            for fr in res["frames"][:12]:
                if not isinstance(fr, dict):
                    continue
                compact.append(
                    {
                        "id": fr.get("id"),
                        "ok": fr.get("ok"),
                        "pan_cmd": fr.get("pan_cmd"),
                        "pan_actual": fr.get("pan_actual"),
                        "path": fr.get("path"),
                        "facefinder": fr.get("facefinder"),
                    }
                )
            res["frames"] = compact
            res["frame_paths"] = [f["path"] for f in compact if f.get("path")]
    elif name == "listen_stt":
        # Sole mic watcher is rail-stt (Link only). No local Pulse preference lists.
        sec = float(args.get("seconds", 3.0))
        listen_url = STT_URL.replace("/stt", "/v1/listen")
        body = json.dumps({"seconds": max(1.0, min(15.0, sec))}).encode()
        try:
            req = urllib.request.Request(
                listen_url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=max(30.0, sec + 60.0)) as r:
                stt = json.loads(r.read().decode())
            text = str((stt or {}).get("text") or "") if isinstance(stt, dict) else ""
            reason = (stt or {}).get("reason") if isinstance(stt, dict) else None
            rms = (stt or {}).get("rms_db") if isinstance(stt, dict) else None
            silent = (not text.strip()) and (
                reason == "silence_rms"
                or (isinstance(rms, (int, float)) and rms < -50)
            )
            res = {
                "ok": bool(text.strip()) or not silent,
                "stt": stt,
                "via": "rail-stt/v1/listen",
                "device": "rail-stt-owned",
            }
            if silent:
                res["ok"] = False
                res["error"] = "silent mic / empty transcript (rail-stt)"
                res["warning"] = res["error"]
        except Exception as e:
            res = {"ok": False, "error": str(e)[:300], "via": "rail-stt/v1/listen"}
    elif name == "list_kit":
        kit = default_kit_dir()
        items = []
        for rel, desc in KIT_READABLE.items():
            p = kit / rel
            items.append(
                {
                    "path": rel,
                    "description": desc,
                    "exists": p.is_file(),
                    "bytes": p.stat().st_size if p.is_file() else None,
                }
            )
        res = {"ok": True, "kit_dir": str(kit), "docs": items}
    elif name == "read_kit_doc":
        rel = str(args.get("path") or args.get("name") or "").strip().lstrip("./")
        if not rel or ".." in rel or rel.startswith("/") or "\\" in rel:
            res = {"ok": False, "error": "path must be relative kit file without .."}
        else:
            matched: str | None = None
            if rel in KIT_READABLE:
                matched = rel
            else:
                for k in KIT_READABLE:
                    if Path(rel).name == Path(k).name:
                        matched = k
                        break
            if not matched:
                res = {
                    "ok": False,
                    "error": f"not allowlisted: {rel}",
                    "allowlisted": list(KIT_READABLE.keys()),
                }
            else:
                path = default_kit_dir() / matched
                if not path.is_file():
                    res = {"ok": False, "error": f"missing file {path}"}
                else:
                    max_chars = int(args.get("max_chars") or 12000)
                    text = path.read_text(encoding="utf-8", errors="replace")
                    truncated = len(text) > max_chars
                    if truncated:
                        text = text[:max_chars]
                    res = {
                        "ok": True,
                        "path": matched,
                        "abs": str(path),
                        "truncated": truncated,
                        "text": text,
                    }
    elif name == "list_stops":
        stops_path = default_kit_dir() / "camera-stops.json"
        if not stops_path.is_file():
            # fallback rail-presence data
            alt = _paths.DATA / "camera-stops.json"
            stops_path = alt if alt.is_file() else stops_path
        if not stops_path.is_file():
            res = {
                "ok": True,
                "door_home_pan": DOOR_HOME_PAN,
                "stops": [
                    {"id": "door", "pan": DOOR_HOME_PAN, "tilt": 0},
                    {"id": "desk", "pan": 0, "tilt": 0},
                ],
                "note": "defaults; camera-stops.json missing",
            }
        else:
            try:
                data = json.loads(stops_path.read_text())
            except Exception as e:
                res = {"ok": False, "error": str(e)[:200]}
            else:
                res = {
                    "ok": True,
                    "path": str(stops_path),
                    "door_home_pan": DOOR_HOME_PAN,
                    "door": data.get("door") or {"pan": DOOR_HOME_PAN},
                    "stops": data.get("stops") or [],
                    "note": "Rest on door/entrance. Scan desk/chair only when needed.",
                }
    elif name == "log_error":
        err = str(args.get("error") or args.get("message") or "").strip()
        reason = str(
            args.get("suspected_reason")
            or args.get("reason")
            or args.get("hypothesis")
            or ""
        ).strip()
        if not err or not reason:
            res = {
                "ok": False,
                "error": "log_error requires error= and suspected_reason=",
            }
        else:
            res = log_operator_error(
                error=err,
                suspected_reason=reason,
                tool=args.get("tool"),
                context=args.get("context"),
                source="cosmos_tool",
            )
    elif name == "rails_inventory":
        res = fetch_rails_inventory()
    elif name == "sync_report_from_rails":
        res = sync_report_from_rails(
            speaking=args.get("speaking"),
            note=args.get("note"),
        )
    elif name == "publish_report":
        in_room = args.get("in_room") or args.get("people") or []
        note = args.get("note")
        # Never clobber sticky presence with empty door FOV guesses
        inv = fetch_rails_inventory()
        merged = False
        if (
            inv.get("ok")
            and int(inv.get("headcount") or 0) > 0
            and not normalize_in_room(in_room)
        ):
            in_room = [
                {
                    "id": p["id"],
                    "certain": p.get("certain"),
                    "location": p.get("location") or "in_room",
                }
                for p in (inv.get("people") or [])
                if p.get("id")
            ]
            merged = True
            note = (
                (note + " | " if note else "")
                + "merged rails sticky (model sent empty in_room)"
            )
        report = build_room_speaker_report(
            in_room=in_room,
            speaking=args.get("speaking"),
            note=note,
            source="cosmos_tool_merged" if merged else "cosmos_tool",
        )
        path = publish_report(report)
        res = {
            "ok": True,
            "path": str(path),
            "report": report,
            "merged_rails": merged,
            "rails_headcount": inv.get("headcount") if inv.get("ok") else None,
        }
    else:
        res = {"ok": False, "error": f"unknown tool {name!r}"}

    if isinstance(res, dict):
        res = _strip_heavy_fields(res)
        _auto_log_if_failed(name, res)
    return res if isinstance(res, dict) else {"ok": False, "error": "bad tool result"}


def normalize_in_room(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if raw is None:
        return out
    if isinstance(raw, dict) and "people" in raw:
        raw = raw["people"]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, str):
            out.append({"id": item.strip().lower(), "certain": None})
        elif isinstance(item, dict):
            pid = item.get("id") or item.get("name") or item.get("person_id")
            if pid:
                out.append(
                    {
                        "id": str(pid).strip().lower(),
                        "certain": item.get("certain"),
                        "location": item.get("location"),
                        "evidence": item.get("evidence"),
                    }
                )
    return out


def normalize_speaking(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"id": None, "text": None, "source": None}
    if isinstance(raw, str):
        return {"id": raw.strip().lower() or None, "text": None, "source": "model"}
    if isinstance(raw, dict):
        pid = raw.get("id") or raw.get("name") or raw.get("person_id")
        return {
            "id": str(pid).strip().lower() if pid else None,
            "text": raw.get("text") or raw.get("transcript"),
            "source": raw.get("source") or "model",
            "conf": raw.get("conf") or raw.get("confidence"),
        }
    return {"id": None, "text": None, "source": None}


def build_room_speaker_report(
    *,
    in_room: Any = None,
    speaking: Any = None,
    note: str | None = None,
    source: str = "cosmos_operator",
) -> dict[str, Any]:
    people = normalize_in_room(in_room)
    sp = normalize_speaking(speaking)
    return {
        "schema": "cosmos_room_speaker/v1",
        "ts": time.time(),
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "in_room": {
            "headcount": len(people),
            "people": people,
        },
        "speaking": sp,
        "note": note,
        "operator_mode": operator_mode_enabled(),
    }


def publish_report(report: dict[str, Any], path: Path | None = None) -> Path:
    path = path or default_report_path()
    atomic_write_text(path, json.dumps(report, indent=2, default=str))
    # also append history (capped; atomic rewrite at the cap)
    append_jsonl_capped(
        path.with_suffix(".jsonl"),
        json.dumps(report, default=str),
        max_bytes=REPORT_HISTORY_MAX_BYTES,
        keep_lines=REPORT_HISTORY_KEEP,
    )
    return path


def load_report(path: Path | None = None) -> dict[str, Any] | None:
    path = path or default_report_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def affirms_can_do_job(text: str) -> bool:
    """Heuristic: model states it can do who-in-room + who-speaking job."""
    if not text:
        return False
    t = text.lower()
    # strong yes
    if "yes i can do the job" in t or "i can do the job" in t:
        return True
    if "can do the job" in t and "cannot do the job" not in t and "can't do the job" not in t:
        return True
    # softer: yes on video + yes on job duties
    has_room = any(
        x in t
        for x in (
            "who is in the room",
            "who is present",
            "in the room",
            "presence",
            "who is here",
        )
    )
    has_speak = any(
        x in t for x in ("who is speaking", "speaker", "who is talking", "speaking")
    )
    yes = bool(re.search(r"\byes\b", t)) and "no reliable" not in t[:200]
    if yes and has_room and has_speak:
        return True
    if "i can" in t and has_room and has_speak and "cannot" not in t:
        return True
    return False


def extract_capability_bits(text: str) -> dict[str, Any]:
    t = (text or "").lower()
    video = None
    audio = None
    if re.search(r"video[^\n.]{0,40}\byes\b", t) or ("usable video" in t and "yes" in t):
        video = True
    if re.search(r"video[^\n.]{0,40}\bno\b", t):
        video = False
    if re.search(r"audio[^\n.]{0,40}\byes\b", t):
        audio = True
    if re.search(r"audio[^\n.]{0,40}\bno\b", t) or (
        "silent" in t and "audio" in t
    ):
        audio = False
    return {
        "video_usable": video,
        "audio_usable": audio,
        "can_do_job": affirms_can_do_job(text),
    }


# --- chat session (in-process) ---

class CosmosChatSession:
    """Conversation + tool loop for the operator TUI."""

    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []
        self.started = False
        self.capability_reply: str | None = None
        self.busy = False
        self.last_error: str | None = None
        self._lock_note = ""

    def status(self) -> dict[str, Any]:
        try:
            ensure_report_tracks_rails()
        except Exception:
            pass
        inv = fetch_rails_inventory()
        return {
            **operator_flags(),
            "started": self.started,
            "busy": self.busy,
            "turns": len(self.history),
            "capability_done": bool(self.capability_reply),
            "can_do_job": affirms_can_do_job(self.capability_reply or ""),
            "capability_bits": extract_capability_bits(self.capability_reply or ""),
            "last_error": self.last_error,
            "rails_inventory": inv,
            "report": load_report(),
        }

    def append(self, role: str, text: str, **extra: Any) -> None:
        row = {
            "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
            "role": role,
            "text": text,
            **extra,
        }
        self.history.append(row)
        try:
            hp = default_history_path()
            hp.parent.mkdir(parents=True, exist_ok=True)
            with hp.open("a") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        except Exception:
            pass

    def ensure_brief_seeded(self) -> str:
        brief = capability_job_brief()
        if not self.started:
            self.append("system", brief, kind="capability_job_brief")
            self.started = True
        return brief


_SESSION: CosmosChatSession | None = None


def ensure_report_tracks_rails() -> dict[str, Any] | None:
    """Keep CosmOS report aligned with rails sticky (same people as presence/STT)."""
    inv = fetch_rails_inventory()
    if not inv.get("ok"):
        return None
    rail_ids = {
        str(p.get("id") or "").lower()
        for p in (inv.get("people") or [])
        if p.get("id")
    }
    cur = load_report() or {}
    cur_people = ((cur.get("in_room") or {}).get("people")) or []
    cur_ids = {
        str(p.get("id") or "").lower()
        for p in cur_people
        if isinstance(p, dict) and p.get("id")
    }
    # Sync when rails has people CosmOS lacks, OR CosmOS has extras rails dropped,
    # OR headcounts disagree — so the User is never "on rails only".
    if rail_ids != cur_ids:
        return sync_report_from_rails(
            note=(
                f"auto-sync rails sticky headcount={inv.get('headcount')} "
                f"trust={inv.get('count_trust')} ids={sorted(rail_ids)}"
            )
        )
    return None


def get_chat_session() -> CosmosChatSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = CosmosChatSession()
        # restore capability reply from kit if prior session already answered
        try:
            cap = default_kit_dir() / "can_do_job.txt"
            if cap.is_file() and cap.stat().st_size > 20:
                text = cap.read_text()
                _SESSION.capability_reply = text
                _SESSION.started = True
                if not _SESSION.history:
                    _SESSION.append("system", capability_job_brief(), kind="restored")
                    _SESSION.append("assistant", text, kind="capability_restored")
        except Exception:
            pass
        # Fix empty CosmOS report vs live rails sticky on every process start
        try:
            ensure_report_tracks_rails()
        except Exception:
            pass
    return _SESSION


def _prefer_room_vision(vision_path: Path | str | None = None) -> tuple[Path, str]:
    """Pick vision: prefer last look_around desk/chair stills, else door snapshot.

    Returns (path, label). Label is injected so model does not treat door as full room.
    """
    work = Path(
        os.environ.get(
            "RAIL_ORACLE_WORK",
            str(_paths.DATA / "work/oracle_live"),
        )
    )
    if vision_path:
        p = Path(vision_path)
        if p.is_file():
            return p, "ATTACHED_IMAGE=operator_provided"
    # Prefer recent room seats over door rest
    frame_dir = work / "look_around_frames"
    for name in ("desk.jpg", "chair.jpg"):
        cand = frame_dir / name
        if cand.is_file() and cand.stat().st_size > 500:
            # prefer newest of the two if both exist
            desk = frame_dir / "desk.jpg"
            chair = frame_dir / "chair.jpg"
            picks = [x for x in (desk, chair) if x.is_file() and x.stat().st_size > 500]
            if picks:
                best = max(picks, key=lambda x: x.stat().st_mtime)
                return best, f"ATTACHED_IMAGE=look_around seat ({best.name}) NOT door-only"
    snap = execute_tool("snapshot")
    if snap.get("ok") and snap.get("path"):
        p = Path(str(snap["path"]))
        if p.is_file():
            return (
                p,
                "ATTACHED_IMAGE=DOOR_REST_FOV_ONLY — empty doorway is normal; "
                "do NOT inventory the room from this frame; use RAILS_INVENTORY + look_around",
            )
    import cv2
    import numpy as np

    out = work / "cosmos" / "chat" / "blank.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), np.zeros((64, 64, 3), dtype=np.uint8))
    return out, "ATTACHED_IMAGE=blank (no camera still)"


def run_cosmos_text(
    prompt: str,
    *,
    vision_path: Path | str | None = None,
    timeout: float = 600,
) -> str:
    """Invoke Cosmos reasoner (offline CLI) with optional single vision image."""
    from .oracle_bench import (
        COSMOS_CKPT_NAME,
        COSMOS_PYTHON,
        WORK,
        build_cosmos_reasoner_sample,
    )
    import subprocess

    py = COSMOS_PYTHON
    if not py or not Path(py).is_file() or not _paths.cosmos_root():
        raise RuntimeError("the Cosmos describer is not installed here (COSMOS_PYTHON, COSMOS_FRAMEWORK_ROOT)")
    work = WORK / "cosmos" / "chat"
    work.mkdir(parents=True, exist_ok=True)
    vp, vision_label = _prefer_room_vision(vision_path)
    # Banner so door JPEG never becomes the only truth
    prompt = (
        f"{vision_label}\n"
        f"{rails_inventory_block()}\n"
        "Remember: door rest FOV ≠ full room. People sit at desk/chair.\n\n"
        + prompt
    )
    sample = build_cosmos_reasoner_sample(vp, prompt=prompt, name="oracle_chat")
    sp = work / "sample.json"
    sp.write_text(json.dumps(sample, indent=2))
    out_dir = work / "out"
    if out_dir.is_dir():
        for old in out_dir.rglob("*"):
            if old.is_file():
                try:
                    old.unlink()
                except OSError:
                    pass
    out_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = _paths.cosmos_root() + ":" + env.get("PYTHONPATH", "")
    env.setdefault("HF_HOME", str(_paths.HF))
    env.setdefault("HF_HUB_CACHE", str(_paths.HF / "hub"))
    proc = subprocess.run(
        [
            py,
            "-m",
            "cosmos_framework.scripts.inference",
            "--parallelism-preset=latency",
            "-i",
            str(sp),
            "-o",
            str(out_dir),
            "--checkpoint-path",
            COSMOS_CKPT_NAME,
            "--seed",
            "0",
            "--no-guardrails",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=_paths.cosmos_root() or None,
    )
    text = ""
    for p in sorted(out_dir.rglob("reasoner_text.txt"), key=lambda x: x.stat().st_mtime):
        text = p.read_text()
    if not text:
        text = (proc.stderr or proc.stdout or f"cosmos empty rc={proc.returncode}")[
            -2500:
        ]
    if "Traceback" in text or proc.returncode != 0:
        raise RuntimeError(text[-1500:])
    return text


def _vision_path_from_tool_results(results: list[dict[str, Any]]) -> Path | None:
    """Prefer a look_around frame path, else snapshot path, for next vision call."""
    for row in results:
        res = row.get("result") if isinstance(row, dict) else None
        if not isinstance(res, dict):
            continue
        for key in ("frame_paths",):
            paths = res.get(key)
            if isinstance(paths, list):
                for p in paths:
                    if p and Path(str(p)).is_file():
                        return Path(str(p))
        frames = res.get("frames")
        if isinstance(frames, list):
            for fr in frames:
                if isinstance(fr, dict) and fr.get("path") and Path(str(fr["path"])).is_file():
                    return Path(str(fr["path"]))
        if res.get("path") and str(res["path"]).endswith((".jpg", ".jpeg", ".png")):
            p = Path(str(res["path"]))
            if p.is_file():
                return p
    return None


def chat_turn(user_text: str, *, max_tool_rounds: int = 4) -> dict[str, Any]:
    """One user message → optional tools → final assistant text; may publish report."""
    sess = get_chat_session()
    if sess.busy:
        return {"ok": False, "error": "busy"}
    sess.busy = True
    sess.last_error = None
    try:
        # Keep report honest vs script sticky before every turn
        try:
            ensure_report_tracks_rails()
        except Exception:
            pass
        brief = sess.ensure_brief_seeded()
        # Fresh rails block every turn (brief may be old)
        live_rails = rails_inventory_block()
        is_first = sess.capability_reply is None and (
            not user_text or user_text.strip().lower() in ("start", "begin", "hi", "hello")
        )
        if is_first:
            user_text = (
                "Please answer the FIRST REPLY REQUIRED: video yes/no, audio yes/no, "
                "and whether you can do the job of reporting who-is-in-room and who-is-speaking. "
                "If RAILS_INVENTORY headcount>0 call sync_report_from_rails. "
                "Tools: rails_inventory, camera_health, snapshot, look_around, listen_stt."
            )
        low = (user_text or "").lower()
        if any(
            k in low
            for k in (
                "look around",
                "scan",
                "how many people",
                "who is",
                "aim",
                "see all",
                "hair",
                "shirt",
                "in the room",
                "present",
            )
        ):
            user_text = (
                user_text
                + "\n\n[operator reminder] Start from RAILS_INVENTORY (script sticky). "
                "Call rails_inventory or sync_report_from_rails. look_around only for seats/"
                "appearance. Never publish empty if rails headcount>0. return_door after scan."
            )
        sess.append("user", user_text)

        prior = "\n\n".join(
            f"{h['role'].upper()}: {h['text'][:2000]}"
            for h in sess.history[-12:]
            if h.get("role") in ("user", "assistant", "system", "tool")
        )
        inv = fetch_rails_inventory()
        must_names = [
            str(p.get("id"))
            for p in (inv.get("people") or [])
            if p.get("id")
        ]
        must_line = (
            f"PRESENT NOW (must name these — script already has them): {', '.join(must_names)}"
            if must_names
            else "PRESENT NOW: (rails empty)"
        )
        prompt = (
            brief
            + "\n\n--- live inventory ---\n"
            + live_rails
            + "\n"
            + must_line
            + "\n\n--- conversation ---\n"
            + prior
            + "\n\nReply as Cosmos operator. TOOL: {json} lines when needed.\n"
            "FORBIDDEN: empty room / no one here / only door frame — if PRESENT NOW is non-empty.\n"
            "REQUIRED first line of answer: who is present = PRESENT NOW list (include the user if listed).\n"
            "Script/presence/STT already know these people; you must match them, not re-derive from door JPEG.\n"
            "End with a clear answer for the User."
        )

        tool_trace: list[dict[str, Any]] = []
        final = ""
        vision_path: Path | None = None
        for _round in range(max_tool_rounds + 1):
            final = run_cosmos_text(prompt, vision_path=vision_path)
            calls = parse_tool_calls(final)
            if not calls or _round >= max_tool_rounds:
                break
            results = []
            for c in calls[:5]:
                name = str(c.get("name") or "")
                args = c.get("args") if isinstance(c.get("args"), dict) else {}
                if not args and isinstance(c.get("arguments"), dict):
                    args = c["arguments"]
                res = execute_tool(name, args)
                tool_trace.append({"name": name, "args": args, "result": res})
                results.append({"name": name, "result": res})
            vp = _vision_path_from_tool_results(results)
            if vp is not None:
                vision_path = vp
            sess.append("tool", json.dumps(results, default=str)[:8000], kind="tool_results")
            prompt = (
                brief
                + "\n\n--- live inventory ---\n"
                + rails_inventory_block()
                + "\n\nTool results:\n"
                + json.dumps(results, default=str)[:6000]
                + "\n\nContinue. Prefer rails sticky ids for in_room. "
                "publish_report or sync_report_from_rails. Rest on door. "
                "log_error with suspected_reason on failure."
            )

        sess.append("assistant", final, tools=tool_trace)
        if sess.capability_reply is None:
            sess.capability_reply = final
            try:
                (default_kit_dir() / "can_do_job.txt").write_text(final)
            except Exception:
                pass
            bits = extract_capability_bits(final)
            if bits.get("can_do_job"):
                publish_report(
                    build_room_speaker_report(
                        in_room=[],
                        speaking=None,
                        note="capability accepted; room report pending observation",
                        source="cosmos_capability",
                    )
                )

        return {
            "ok": True,
            "reply": final,
            "tools": tool_trace,
            "status": sess.status(),
            "history_tail": sess.history[-8:],
        }
    except Exception as e:
        sess.last_error = str(e)[:400]
        try:
            log_operator_error(
                error=sess.last_error,
                suspected_reason="chat_turn exception — model/runtime failure",
                tool="chat_turn",
                source="rails_auto",
            )
        except Exception:
            pass
        return {"ok": False, "error": sess.last_error, "status": sess.status()}
    finally:
        sess.busy = False


CHAT_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<title>Cosmos Oracle — chat TUI</title>
<style>
:root{--bg:#0c0d10;--card:#14161c;--bd:#252833;--tx:#e8eaed;--mut:#8b93a7;--ok:#3dd68c;--warn:#e6b84d;--acc:#7eb8ff}
*{box-sizing:border-box}
body{font-family:ui-sans-serif,system-ui,sans-serif;background:var(--bg);color:var(--tx);margin:0;padding:14px}
h1{font-size:1.25rem;margin:0 0 8px}
a{color:var(--acc)}
.banner{background:#1a2230;border:1px solid #2a3a55;border-radius:10px;padding:10px 14px;margin-bottom:12px;font-size:13px;line-height:1.45}
.row{display:flex;gap:12px;flex-wrap:wrap}
.col{flex:1 1 320px;background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px;min-height:200px}
#log{height:55vh;overflow:auto;font-size:13px;white-space:pre-wrap;background:#0f1116;border-radius:8px;padding:10px}
.msg{margin:0 0 10px;padding:8px;border-radius:8px;border:1px solid var(--bd)}
.msg.user{border-color:#3a6ea5}
.msg.assistant{border-color:#2a4a30}
.msg.system{color:var(--mut);font-size:12px}
.msg.tool{color:#c9b27c;font-size:12px}
textarea{width:100%;min-height:70px;background:#0f1116;color:var(--tx);border:1px solid var(--bd);border-radius:8px;padding:8px}
button{background:#1e3a5f;color:var(--tx);border:1px solid #3a6ea5;border-radius:8px;padding:10px 14px;cursor:pointer;margin:4px 4px 0 0}
button:disabled{opacity:.5}
.pill{display:inline-block;background:#1a1d24;border:1px solid var(--bd);border-radius:999px;padding:2px 8px;font-size:12px;margin:2px}
.pill.ok{border-color:var(--ok);color:var(--ok)}
.pill.warn{border-color:var(--warn);color:var(--warn)}
pre{font-size:11px;overflow:auto;max-height:180px}
</style></head><body>
<h1>Cosmos Oracle — operator chat</h1>
<div class="banner">
  <b>One port</b>: <code>__PRESENCE__</code> —
  <a href="/">presence inventory</a> ·
  <a href="/chat">this chat TUI</a> ·
  <a href="/oracle">strip board</a> ·
  <a href="/v1/cosmos/report">JSON report</a><br/>
  Kit (Link docs + control surface):
  <code><data>/cosmos-operator/</code>
  · Camera tools via HTTP <code>:8192</code> (sole owner). Job: <b>who is in the room</b> + <b>who is speaking</b>.
</div>
<div class="row">
  <div class="col" style="flex:2 1 480px">
    <div id="status"></div>
    <div id="log"></div>
    <textarea id="inp" placeholder="Ask Cosmos… or click Start job for capability brief"></textarea>
    <div>
      <button id="btnStart" onclick="startJob()">Start job / capability</button>
      <button id="btnSend" class="primary" onclick="send()">Send</button>
      <button onclick="refresh()">Refresh</button>
    </div>
  </div>
  <div class="col">
    <b>Live report</b>
    <pre id="report">loading…</pre>
    <b>Operator flags</b>
    <pre id="flags"></pre>
  </div>
</div>
<script>
function esc(s){if(s==null)return '';return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function refresh(){
  const s = await (await fetch('/v1/cosmos/status',{cache:'no-store'})).json();
  const st = document.getElementById('status');
  const can = s.can_do_job;
  st.innerHTML =
    `<span class="pill ${s.operator_mode?'ok':'warn'}">operator=${!!s.operator_mode}</span>`+
    `<span class="pill ${can?'ok':'warn'}">can_do_job=${!!can}</span>`+
    `<span class="pill">busy=${!!s.busy}</span>`+
    `<span class="pill">turns=${s.turns||0}</span>`+
    (s.last_error?`<span class="pill warn">${esc(s.last_error)}</span>`:'');
  document.getElementById('flags').textContent = JSON.stringify(s, null, 2);
  const rep = await (await fetch('/v1/cosmos/report',{cache:'no-store'})).json();
  document.getElementById('report').textContent = JSON.stringify(rep, null, 2);
  const h = await (await fetch('/v1/cosmos/history',{cache:'no-store'})).json();
  const log = document.getElementById('log');
  log.innerHTML = (h.history||[]).map(m =>
    `<div class="msg ${esc(m.role)}"><b>${esc(m.role)}</b> <span style="color:#666">${esc(m.ts_local||'')}</span><div>${esc(m.text||'')}</div></div>`
  ).join('') || '<div class="msg system">No turns yet — press Start job.</div>';
  log.scrollTop = log.scrollHeight;
}
async function startJob(){
  document.getElementById('btnStart').disabled = true;
  document.getElementById('btnSend').disabled = true;
  try{
    await fetch('/v1/cosmos/chat', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text:'start', capability:true})});
  } finally {
    document.getElementById('btnStart').disabled = false;
    document.getElementById('btnSend').disabled = false;
    await refresh();
  }
}
async function send(){
  const inp = document.getElementById('inp');
  const text = inp.value.trim();
  if(!text) return;
  inp.value = '';
  document.getElementById('btnSend').disabled = true;
  try{
    await fetch('/v1/cosmos/chat', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text})});
  } finally {
    document.getElementById('btnSend').disabled = false;
    await refresh();
  }
}
refresh();
setInterval(refresh, 4000);
</script>
</body></html>
"""
