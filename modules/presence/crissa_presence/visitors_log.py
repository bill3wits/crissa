"""Today's door visitors — sequential IDs, not occupancy.

Named guests (visitor_today) + unnamed X# stills + door-glance FaceID
rows. Speech-look of the User is AUTH, not a visitor.

HTML lives at /visitors and /security. The FaceID HUD stays at /visor —
never serve this page there (face-time ASCII background).
"""
from __future__ import annotations

from . import paths as _paths

import json
import os
import time
from pathlib import Path
from typing import Any

from .guests import visitor_day
from .stranger_x import list_strangers

# Door IDs only. beam_look / f2f of the User is the AUTH look.
DOOR_SOURCES = frozenset({"door_glance", "scenery", "door_buffer", "enter"})


def _local_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(float(ts)))


def _hhmm(ts: float | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%H:%M", time.localtime(float(ts)))


def _faceidog_root() -> Path:
    import os

    raw = os.environ.get("RAIL_FACEID_LOG")
    if raw:
        return Path(raw)
    return _paths.DATA / "faceidog"


def _user_id() -> str:
    from .house_user import user_id as _record_user

    return _record_user()        # env, else the identity record's User (Init step 5)


def _faceid_span_today(day: str, root: Path) -> dict[str, tuple[float, float]]:
    """name -> (first_ts, last_ts) from today's faceid jsonl. Includes the User."""
    stamp = day.replace("-", "")
    p = root / f"{stamp}.jsonl"
    out: dict[str, tuple[float, float]] = {}
    if not p.is_file():
        return out
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        name = str(rec.get("name") or "").strip().lower()
        if not name:
            continue
        ts = rec.get("ts")
        try:
            ts_f = float(ts) if ts is not None else 0.0
        except (TypeError, ValueError):
            ts_f = 0.0
        if ts_f <= 0:
            continue
        prev = out.get(name)
        if prev is None:
            out[name] = (ts_f, ts_f)
        else:
            first, last = prev
            if ts_f < first:
                first = ts_f
            if ts_f > last:
                last = ts_f
            out[name] = (first, last)
    return out


def seen_since_midnight(
    *,
    now: float | None = None,
    visitor_today: list[dict] | None = None,
    faceid_root: Path | None = None,
) -> dict[str, Any]:
    """Distinct people Inspector has on file since local midnight.

    User counts as 1. Zero is a real vacation. Not occupancy headcount.
    """
    now = time.time() if now is None else float(now)
    day = visitor_day(now)
    uid = _user_id()
    root = faceid_root or _faceidog_root()
    ids: dict[str, dict[str, Any]] = {}

    def _add(pid: str, *, kind: str, ts: float) -> None:
        pid = str(pid or "").strip().lower()
        if not pid:
            return
        if pid == uid:
            kind = "user"
        ts_f = float(ts or 0)
        prev = ids.get(pid)
        if prev is None:
            ids[pid] = {"id": pid, "kind": kind, "ts": ts_f, "last_ts": ts_f}
            return
        first = float(prev.get("ts") or 0)
        last = float(prev.get("last_ts") or 0)
        if ts_f and (not first or ts_f < first):
            first = ts_f
        if ts_f > last:
            last = ts_f
        ids[pid] = {"id": pid, "kind": kind, "ts": first, "last_ts": last}

    for name, span in _faceid_span_today(day, root).items():
        kind = "stranger" if name.startswith("x") else "named"
        first, last = span
        _add(name, kind=kind, ts=first)
        _add(name, kind=kind, ts=last)
    for s in list_strangers():
        opened = s.get("opened_ts")
        last = s.get("last_ts")
        try:
            first_f = float(opened if opened is not None else last or 0.0)
        except (TypeError, ValueError):
            first_f = 0.0
        try:
            last_f = float(last if last is not None else first_f)
        except (TypeError, ValueError):
            last_f = first_f
        ts_f = first_f or last_f
        if ts_f and _local_day(ts_f) != day and last_f and _local_day(last_f) != day:
            continue
        _add(str(s.get("id") or ""), kind="stranger", ts=first_f or last_f)
        if last_f:
            _add(str(s.get("id") or ""), kind="stranger", ts=last_f)
    for v in visitor_today or []:
        if not isinstance(v, dict):
            continue
        if str(v.get("day") or "") not in ("", day):
            continue
        ts = v.get("ts")
        try:
            ts_f = float(ts) if ts is not None else 0.0
        except (TypeError, ValueError):
            ts_f = 0.0
        _add(str(v.get("person_id") or ""), kind="named", ts=ts_f)

    order = sorted(ids.values(), key=lambda r: float(r.get("ts") or 0), reverse=True)
    user_seen = uid in ids
    return {
        "ok": True,
        "day": day,
        "n": len(order),
        "user_seen": user_seen,
        "ids": [r["id"] for r in order],
        "people": order,
        "note": "Distinct since midnight. User is 1. Zero is vacation. Not occupancy.",
    }


def _faceid_today(day: str, root: Path) -> list[dict[str, Any]]:
    stamp = day.replace("-", "")
    p = root / f"{stamp}.jsonl"
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        src = str(rec.get("source") or "")
        name = str(rec.get("name") or "").strip().lower()
        uid = str(os.environ.get("HOUSE_USER_ID") or "user").strip().lower()
        if name == uid:
            continue
        if src not in DOOR_SOURCES and not name.startswith("x"):
            continue
        ts = rec.get("ts")
        try:
            ts_f = float(ts) if ts is not None else 0.0
        except (TypeError, ValueError):
            ts_f = 0.0
        crop = rec.get("crop")
        crop_url = None
        if crop:
            fn = Path(str(crop)).name
            crop_url = f"/v1/visitors/face/{fn}"
        out.append(
            {
                "ts": ts_f,
                "when": rec.get("ts_local") or _hhmm(ts_f),
                "id": name,
                "display": name.upper() if name.startswith("x") else name,
                "kind": "stranger" if name.startswith("x") else "named",
                "source": src,
                "sim": rec.get("sim"),
                "photo": crop_url,
            }
        )
    return out


def list_today(
    *,
    now: float | None = None,
    visitor_today: list[dict] | None = None,
    faceid_root: Path | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else float(now)
    day = visitor_day(now)
    rows: list[dict[str, Any]] = []
    for s in list_strangers():
        ts = s.get("last_ts") or s.get("opened_ts")
        try:
            ts_f = float(ts) if ts is not None else 0.0
        except (TypeError, ValueError):
            ts_f = 0.0
        if ts_f and _local_day(ts_f) != day:
            continue
        rows.append(
            {
                "ts": ts_f,
                "when": _hhmm(ts_f),
                "id": s.get("id"),
                "display": s.get("display") or s.get("id"),
                "kind": "stranger",
                "source": s.get("last_kind") or "enter",
                "sim": None,
                "photo": s.get("photo"),
                "n": s.get("n_events"),
            }
        )
    for v in visitor_today or []:
        if not isinstance(v, dict):
            continue
        if str(v.get("day") or "") not in ("", day):
            continue
        ts = v.get("ts")
        try:
            ts_f = float(ts) if ts is not None else 0.0
        except (TypeError, ValueError):
            ts_f = 0.0
        pid = str(v.get("person_id") or "").strip().lower()
        if not pid:
            continue
        uid = str(
            os.environ.get("HOUSE_USER_ID") or "user"
        ).strip().lower()
        if pid == uid:
            continue
        rows.append(
            {
                "ts": ts_f,
                "when": _hhmm(ts_f),
                "id": pid,
                "display": pid,
                "kind": "named",
                "source": v.get("source") or "visitor",
                "sim": None,
                "photo": None,
            }
        )
    rows.extend(_faceid_today(day, faceid_root or _faceidog_root()))
    rows.sort(key=lambda r: float(r.get("ts") or 0), reverse=True)
    # One card per id; keep latest still, count hits.
    seen: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in rows:
        key = str(r.get("id") or "")
        if not key:
            continue
        if key not in seen:
            seen[key] = dict(r)
            seen[key]["n"] = int(r.get("n") or 1)
            order.append(key)
        else:
            seen[key]["n"] = int(seen[key].get("n") or 1) + 1
            if r.get("photo") and not seen[key].get("photo"):
                seen[key]["photo"] = r.get("photo")
    people = [seen[k] for k in order]
    census = seen_since_midnight(
        now=now, visitor_today=visitor_today, faceid_root=faceid_root
    )
    return {
        "ok": True,
        "day": day,
        "n": int(census.get("n") or 0),
        "n_files": len(people),
        "user_seen": bool(census.get("user_seen")),
        "ids": list(census.get("ids") or []),
        "people": people,
        "note": census.get("note")
        or "Distinct since midnight. User is 1. Zero is vacation. Not occupancy.",
    }


# Security-files page. Not the visor HUD. User faces at the door will
# duplicate Me stills — that is expected, not a second occupancy object.
VISITORS_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Security · door files</title>
<style>
:root{--hud:#7dffc3;--hud2:#c8ff7a;--warn:#ffcc66;--dim:rgba(8,18,14,.72);--bd:rgba(125,255,195,.35)}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;background:#020403;color:var(--hud);font:14px/1.4 ui-monospace,Menlo,Consolas,monospace}
#top{position:sticky;top:0;z-index:2;display:flex;align-items:baseline;gap:18px;padding:16px 22px 12px;background:linear-gradient(#020403,rgba(2,4,3,.92));border-bottom:1px solid var(--bd)}
#mode{letter-spacing:.42em;font-size:11px;text-transform:uppercase;text-shadow:0 0 12px var(--hud)}
#auth{margin-left:auto;font-size:12px;letter-spacing:.12em}
#auth.on{color:var(--hud2)}
#auth.off{color:var(--warn)}
a.back,a.hud{color:var(--hud);text-decoration:none;letter-spacing:.2em;font-size:11px;opacity:.7}
#sub{padding:8px 22px 0;font-size:11px;letter-spacing:.16em;opacity:.7}
#still{position:fixed;top:14px;right:18px;width:160px;height:90px;object-fit:cover;border:1px solid var(--bd);background:#000;z-index:3}
#log{padding:18px 22px 40px;display:flex;flex-direction:column;gap:10px;max-width:920px}
.card{display:flex;gap:14px;align-items:center;background:var(--dim);border:1px solid var(--bd);padding:10px 12px}
.card img{width:72px;height:72px;object-fit:cover;border:1px solid var(--hud);background:#000;flex:none}
.card .who{font-size:22px;letter-spacing:.08em;text-transform:uppercase;text-shadow:0 0 12px var(--hud)}
.card .meta{font-size:11px;opacity:.8;margin-top:4px}
.empty{opacity:.55;padding:24px 4px}
</style>
</head>
<body>
<div id="top">
  <div id="mode">SECURITY FILES</div>
  <a class="back" href="/">SO T</a>
  <a class="hud" href="/visor">HUD</a>
  <div id="auth">AUTH —</div>
</div>
<img id="still" alt="door" src="/v1/door/still.jpg"/>
<div id="sub">Door stills · not the face-time HUD · Me duplicates are door crops</div>
<div id="log"><div class="empty">waiting</div></div>
<script>
function esc(s){return String(s??'').replace(/[&<>]/g,c=>({ '&':'&amp;','<':'&lt;','>':'&gt;' }[c]))}
function drawAuth(p){
  const on = !!p.user_auth;
  const el = document.getElementById('auth');
  el.className = on ? 'on' : 'off';
  el.textContent = on
    ? 'USER AUTH HELD'
    : ('AUTH ' + String(p.user_auth_reason || 'off').toUpperCase());
}
function skips(d){
  // The door skip counter (the User 2026-10-09): faces skipped as you, strangers tagged, since midnight.
  if(!d || typeof d!=='object') return '';
  const min = d.skip_min_sim!=null ? ' (≥' + Number(d.skip_min_sim).toFixed(2) + ')' : '';
  return ' · skipped as you ' + (d.skipped||0) + min + ' · strangers tagged ' + (d.logged||0);
}
function card(r){
  const photo = r.photo
    ? `<img src="${esc(r.photo)}?t=${encodeURIComponent(r.ts||0)}" alt="">`
    : `<img alt="" style="opacity:.25">`;
  const n = r.n && r.n>1 ? ` · ×${r.n}` : '';
  return `<div class="card">${photo}<div>
    <div class="who">${esc(r.display||r.id||'?')}</div>
    <div class="meta">${esc(r.when||'')} · ${esc(r.source||'door')}${n}</div>
  </div></div>`;
}
async function tick(){
  try{
    const [p,v] = await Promise.all([
      fetch('/v1/presence',{cache:'no-store'}).then(r=>r.json()),
      fetch('/v1/visitors',{cache:'no-store'}).then(r=>r.json()),
    ]);
    drawAuth(p);
    const people = v.people || [];
    document.getElementById('sub').textContent =
      (v.day||'today') + ' · ' + people.length + ' door files' + skips(v.door_skips) + ' · not the HUD';
    document.getElementById('log').innerHTML = people.length
      ? people.map(card).join('')
      : '<div class="empty">No door files yet today.</div>';
  }catch(e){
    document.getElementById('log').innerHTML = '<div class="empty">log unreachable</div>';
  }
}
tick(); setInterval(tick, 2500);
const still = document.getElementById('still');
setInterval(()=>{ still.src = '/v1/door/still.jpg?t=' + Date.now(); }, 2500);
still.addEventListener('error', ()=>{ still.style.display='none'; });
</script>
</body></html>
"""
