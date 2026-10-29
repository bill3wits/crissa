"""HTTP API + human presence page — room_presence/v1."""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths
from . import guard as _guard  # round 46: this machine only (the module's copy of lib/crissa_guard)

import json
import os
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
from urllib.parse import urlparse

from .enrollments import face_sources
from .oracle_bench import get_bench, set_presence_getter, set_sot_hooks
from .service import PresenceService

_svc: PresenceService | None = None

# Directory of human pages on this port. Not the SoT inventory (/).
TREE_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>rail-presence · tree</title>
<style>
:root{--bg:#0c0d10;--card:#14161c;--bd:#252833;--tx:#e8eaed;--mut:#8b93a7;--acc:#7eb8ff;--hud:#7dffc3}
*{box-sizing:border-box}
body{font:14px/1.45 ui-monospace,Menlo,Consolas,monospace;background:var(--bg);color:var(--tx);margin:0;padding:22px}
h1{font-size:13px;letter-spacing:.28em;text-transform:uppercase;margin:0 0 6px;color:var(--hud)}
.sub{color:var(--mut);font-size:12px;margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px 16px;max-width:720px;margin-bottom:12px}
h2{font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--mut);margin:0 0 10px}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}
table{border-collapse:collapse;width:100%}
td{padding:5px 8px 5px 0;vertical-align:top}
td.p{white-space:nowrap;padding-right:16px}
td.p a{color:var(--hud)}
.k{color:var(--mut);font-size:12px}
</style></head><body>
<h1>__PRESENCE_HOSTPORT__</h1>
<p class="sub">Pages this process serves. Occupancy inventory is parked — not the product.</p>
<div class="card">
<h2>Use these</h2>
<table>
<tr><td class="p"><a href="/visor">/visor</a></td><td>HUD — live FOV + face ID. Face-time ASCII background. Conversation AUTH is this face, not a sticky occupancy bit.</td></tr>
<tr><td class="p"><a href="/visitors">/visitors</a></td><td>Security files — door stills / X# / named crops. Alias <a href="/security">/security</a>.</td></tr>
<tr><td class="p"><a href="/oracle">/oracle</a></td><td>Door feed + ops strip.</td></tr>
<tr><td class="p"><a href="/evidence">/evidence</a></td><td>Door evidence packs.</td></tr>
<tr><td class="p"><a href="/chat">/chat</a></td><td>CosmOS operator chat.</td></tr>
</table>
</div>
<div class="card">
<h2>Parked</h2>
<table>
<tr><td class="p"><a href="/inventory">/inventory</a></td><td>Old headcount / people / occupancy AUTH page. Mill still writes the JSON.</td></tr>
</table>
</div>
<div class="card">
<h2>JSON (not pages)</h2>
<table>
<tr><td class="p"><a href="/v1/presence">/v1/presence</a></td><td>SoT object</td></tr>
<tr><td class="p"><a href="/v1/faceid">/v1/faceid</a></td><td>Live face HUD payload</td></tr>
<tr><td class="p"><a href="/v1/visitors">/v1/visitors</a></td><td>Door-file list</td></tr>
<tr><td class="p"><a href="/v1/speaker">/v1/speaker</a></td><td>Beam / camera / embeds stack</td></tr>
<tr><td class="p"><a href="/v1/health">/v1/health</a></td><td>Process health</td></tr>
<tr><td class="p"><a href="/v1/visit_lamp">/v1/visit_lamp</a></td><td>Visit lamp (sage/amber/rust). Probe <a href="/visit-lamp">/visit-lamp</a>.</td></tr>
<tr><td class="p"><a href="/v1/inspector">/v1/inspector</a></td><td>Inspector status</td></tr>
</table>
<p class="k">Camera pixels live on :8192. VoiceOnly wall is :8099. STT panel is :8190/panel (redirects / to :8099).</p>
</div>
</body></html>
"""

# Prod door feed — SoT + ops status. Gemma/CosmOS/CODE shadow board retired.
COMPARE_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>House · Door feed (prod)</title>
<style>
:root{--bg:#0c0d10;--card:#14161c;--bd:#252833;--tx:#e8eaed;--mut:#8b93a7;--ok:#3dd68c;--warn:#e6b84d;--bad:#f07178;--acc:#7eb8ff}
*{box-sizing:border-box}
body{font-family:ui-sans-serif,system-ui,sans-serif;background:var(--bg);color:var(--tx);margin:0;padding:14px}
h1{font-size:1.25rem;margin:0 0 4px}
.banner{background:#142018;border:1px solid #2a4a30;border-radius:10px;padding:10px 14px;margin-bottom:12px;font-size:14px;line-height:1.45}
.banner code{background:#0f1116;padding:2px 6px;border-radius:4px}
.sub{color:var(--mut);font-size:13px;margin:8px 0}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start}
.feed{flex:1 1 420px;background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px;min-width:280px}
.feed img{width:100%;max-height:300px;object-fit:contain;background:#000;border-radius:8px;border:1px solid #222}
.col{flex:1 1 320px;background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px;min-width:280px}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;background:#1a1d24;color:var(--mut);margin-right:4px}
.pill.ok{color:var(--ok)}.pill.bad{color:var(--bad)}.pill.warn{color:var(--warn);background:#2a2410}
.big{font-size:1.6rem;font-weight:700;margin:6px 0}
.line{white-space:pre-wrap;font-size:14px;line-height:1.4;background:#0f1116;padding:10px;border-radius:8px;max-height:220px;overflow:auto}
table{border-collapse:collapse;width:100%}
td,th{text-align:left;padding:5px 6px;border-bottom:1px solid var(--bd);font-size:13px}
a{color:var(--acc)}
.meta{font-size:12px;color:var(--mut);margin:6px 0}
</style></head><body>
<h1>Door feed · prod</h1>
<div class="banner">
  <b>Gemma / CosmOS / CODE shadow oracles are off</b> (not guessing).<br>
  <b>Truth:</b> RoomPresence SoT · <b>Judgment owner:</b> ops (<code>:8000</code>) ·
  <b>Physics:</b> CODE door buffer + face emb ·
  <a href="/tree">tree</a> · <a href="/visor">FaceID visor</a> ·
  <a href="/visitors">Security files</a> ·
  <a href="__STT__/panel">Control Panel</a>
</div>
<div class="row">
  <div class="feed">
    <b>Door live</b> <span class="pill warn">mjpeg</span>
    <div class="meta" id="feedmeta">__CAMERA__/v1/mjpeg</div>
    <img id="livevid" alt="door" src="__CAMERA__/v1/mjpeg?scale=0.5&amp;hz=12"/>
    <b style="display:block;margin-top:10px">Door strip</b>
    <img id="strip" alt="strip" src="/v1/oracle/strip.jpg?t=0"/>
  </div>
  <div class="col">
    <b>ops</b> <span class="pill" id="opsPill">…</span>
    <div class="meta" id="opsMeta">—</div>
    <div class="line" id="opsLine">loading…</div>
    <div class="big" id="hc">—</div>
    <div class="meta" id="trust">—</div>
    <table id="people"><tr><th>who</th><th>state</th><th>seen</th></tr></table>
    <div class="meta" style="margin-top:10px"><b>Last CODE event</b></div>
    <div class="line" id="event">—</div>
  </div>
</div>
<script>
function esc(s){if(s==null)return '';return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function tick(){
  const r = await fetch('/v1/prod_feed',{cache:'no-store'});
  const d = await r.json();
  const ops = d.ops||{};
  const pr = d.presence||{};
  const le = d.last_event||{};
  const pill = document.getElementById('opsPill');
  pill.textContent = ops.ok ? 'up' : 'down';
  pill.className = 'pill ' + (ops.ok ? 'ok' : 'bad');
  document.getElementById('opsMeta').textContent =
    (ops.model||'ops') + ' · ' + (ops.root||ops.url||':8000') +
    (ops.latency_ms!=null ? (' · ' + ops.latency_ms + 'ms') : '');
  document.getElementById('opsLine').textContent = ops.summary || ops.note ||
    (ops.ok
      ? 'Judgment owner online. Continuous door count is CODE→SoT; ops weighs tools/embs when invoked (Voice / EA).'
      : 'ops unreachable — SoT still updates from CODE face buffer only.');
  const hc = pr.headcount;
  const certain = pr.count_certain;
  const f2f = !!pr.f2f;
  document.getElementById('hc').textContent = f2f
    ? ('F2F' + (pr.f2f_reason ? (' · ' + pr.f2f_reason) : ''))
    : ('headcount ' + (hc??'—') + (certain?'':' ?'));
  document.getElementById('hc').className = 'big ' + (f2f ? 'warn' : (pr.camera_blind ? 'bad' : ''));
  document.getElementById('trust').textContent =
    'trust=' + (pr.count_trust||'—') + ' · mode=' + (pr.camera_mode||'—') +
    ' · frame_age=' + (pr.frame_age_sec??'—') + 's · auth=' +
    ((pr.auth&&pr.auth.authed_ids||[]).join(',')||'none');
  const rows = (pr.people||[]).map(p =>
    '<tr><td>'+esc(p.name||p.display||'?')+'</td><td>'+
    (p.certain?'certain':'?')+(p.id_status?(' · '+esc(p.id_status)):'')+
    '</td><td>'+esc((p.sources&&p.sources.last_seen_ago)||'—')+'</td></tr>'
  ).join('') || '<tr><td colspan=3>empty</td></tr>';
  document.getElementById('people').innerHTML =
    '<tr><th>who</th><th>state</th><th>seen</th></tr>'+rows;
  document.getElementById('event').textContent =
    (le.kind||'—') + (le.source?(' · '+le.source):'') +
    (le.door_names&&le.door_names.length?(' · door '+le.door_names.join(',')):'') +
    '\\n' + JSON.stringify({count_rule:le.count_rule,id_rule:le.id_rule,detail:(le.detail||[]).slice(-3)},null,0);
  document.getElementById('strip').src = '/v1/oracle/strip.jpg?t=' + Date.now();
  document.getElementById('feedmeta').textContent =
    'door rest · shadows retired · oracle_bench=' + (d.oracle_bench_enabled?'on':'off');
}
tick(); setInterval(tick, 2000);
</script></body></html>
"""


HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta http-equiv="refresh" content="3"/>
<title>rail-presence</title>
<style>
body{font-family:ui-sans-serif,system-ui,sans-serif;background:#0c0d10;color:#e8eaed;margin:0;padding:20px}
h1{font-size:1.2rem;margin:0 0 4px}
.sub{color:#8b93a7;font-size:13px;margin-bottom:16px}
.card{background:#14161c;border:1px solid #252833;border-radius:10px;padding:14px 16px;max-width:900px;margin-bottom:12px}
.big{font-size:1.8rem;font-weight:700}
.ok{color:#3dd68c}.warn{color:#e6b84d}.bad{color:#f07178}
table{border-collapse:collapse;width:100%;max-width:900px}
td,th{text-align:left;padding:6px 8px;border-bottom:1px solid #252833;font-size:14px;vertical-align:top}
code{background:#1a1d24;padding:1px 5px;border-radius:4px}
pre{font-size:11px;color:#8b93a7;overflow:auto;max-height:200px}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;margin-right:6px;background:#1a1d24}
.line{white-space:pre-wrap;font-size:13px;line-height:1.35;max-width:42em}
button{background:#1a1d24;color:#e8eaed;border:1px solid #252833;border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px;margin-right:6px}
button.primary{background:#1e3a5f;border-color:#3a6ea5}
a{color:#7eb8ff}
</style></head><body>
<h1>rail-presence</h1>
<div class="sub">STONE spine · door-first · count before STT/TRT · not Crissa</div>
<div id="main">loading…</div>
<script>
function esc(s){
  return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
async function tick(){
  const [rp, feed] = await Promise.all([
    fetch('/v1/presence',{cache:'no-store'}).then(r=>r.json()),
    fetch('/v1/prod_feed',{cache:'no-store'}).then(r=>r.json()).catch(()=>({})),
  ]);
  const d = rp;
  const ops = (feed && feed.ops) || {};
  const certain = d.count_certain;
  const trust = d.count_trust||'?';
  const mode = d.camera_mode||'?';
  const cls = d.camera_blind ? 'bad' : (trust==='fact' ? 'ok' : 'warn');
  let people = (d.people||[]).map(p => {
    const s = p.sources||{};
    const seen = s.last_seen_local || (s.last_seen!=null ? String(s.last_seen) : '—');
    const ago = s.last_seen_ago || '';
    return `<tr>
      <td>${esc(p.name)||'<i>unnamed</i>'}</td>
      <td>${p.certain?'certain':'?'}${p.gender?' · '+esc(p.gender):''}</td>
      <td><span class="pill">${esc(seen)}</span>${ago?' <span class="sub">'+esc(ago)+'</span>':''}
          ${s.at_door?' <span class="pill ok">at door</span>':''}</td>
      <td><code>${esc(JSON.stringify(s))}</code></td>
    </tr>`;
  }).join('') ||
    '<tr><td colspan=4>empty</td></tr>';
  const opsCls = ops.ok ? 'ok' : 'bad';
  document.getElementById('main').innerHTML = `
  <div class="card">
    <div class="big ${cls}">headcount ${d.headcount}${certain?'':' ?'}</div>
    <div class="sub">
      <span class="pill">mode=${esc(mode)}</span>
      <span class="pill">trust=${esc(trust)}</span>
      <span class="pill">${esc(d.ts_local||'')}</span>
      certain=${certain} · blind=${d.camera_blind}
      · capture_age=${d.frame_age_sec}s · unidentified=${d.unidentified}
      · door=[${esc((d.door_names||[]).join(', '))}]
    </div>
  </div>
  <div class="card">
    <b>ops (judgment owner)</b>
    <span class="pill ${opsCls}">${ops.ok?'up':'down'}</span>
    <div class="sub" style="margin-top:6px">
      model=<code>${esc(ops.model||'ops')}</code>
      · ${esc(ops.root||ops.url||':8000')}
      ${ops.latency_ms!=null?(' · '+ops.latency_ms+'ms'):''}
    </div>
    <div class="line">${esc(ops.summary||ops.note||'—')}</div>
    <div class="sub">Shadows retired: gemma / cosmos / code continuous guesses = off.
      <a href="/oracle">Door feed (prod)</a> · <a href="__STT__/panel">Control Panel</a></div>
  </div>
  <div class="card"><b>People (SoT inventory)</b>
    <div class="sub">CODE door buffer + face emb → this object only · judgment owner ops</div>
    <table><tr><th>name</th><th>state</th><th>last seen</th><th>sources</th></tr>${people}</table>
  </div>
  <div class="card"><b>Guards</b> <code>${esc(JSON.stringify(d.guards||{}))}</code></div>
  <div class="card"><b>Last CODE event</b>
    <div class="sub">${esc(d.ts_local||'')} local · ${esc(d.ts_utc||'')} UTC
      ${(d.last_event&&d.last_event.capture_ts_local)?'· capture '+esc(d.last_event.capture_ts_local):''}
      ${(d.last_event&&d.last_event.ts_local)?'· event '+esc(d.last_event.ts_local):''}
    </div>
    <pre>${esc(JSON.stringify(d.last_event||{},null,2))}</pre>
  </div>
  <div class="sub">GET /v1/presence · /v1/prod_feed · POST /v1/reconcile · /v1/auth
    · <a href="/oracle">prod door feed</a></div>`;
}
tick(); setInterval(tick, 3000);
</script></body></html>
"""


def get_svc() -> PresenceService:
    global _svc
    if _svc is None:
        _svc = PresenceService()
        _svc.start()
    return _svc


def build_prod_feed(svc: PresenceService | None = None) -> dict:
    """Prod panel feed: SoT + ops health. No gemma/cosmos shadow guesses."""
    import time
    import urllib.request

    svc = svc or get_svc()
    st = svc.get()
    snap = st.to_dict() if hasattr(st, "to_dict") else {}
    if not snap:
        # PresenceSnapshot may expose asdict via service
        try:
            snap = {
                "headcount": st.headcount,
                "count_certain": st.count_certain,
                "count_trust": st.count_trust,
                "camera_mode": st.camera_mode,
                "camera_blind": st.camera_blind,
                "frame_age_sec": st.frame_age_sec,
                "people": [
                    {
                        "name": p.name,
                        "certain": p.certain,
                        "id_status": getattr(p, "id_status", None),
                        "display": getattr(p, "display", None),
                        "sources": getattr(p, "sources", None) or {},
                    }
                    for p in (st.people or [])
                ],
                "auth": getattr(st, "auth", None),
                "last_event": st.last_event,
                "ts_local": getattr(st, "ts_local", None),
            }
        except Exception as e:
            snap = {"error": str(e)[:120]}

    from .occupancy_llm import chat as occupancy_chat
    ops_url, ops_model = occupancy_chat()
    models_url = ops_url.split("/v1/")[0].rstrip("/") + "/v1/models"
    ops: dict = {
        "ok": False,
        "model": ops_model,
        "url": models_url,
        "role": "judgment_owner",
        "note": "ops owns presence judgment; continuous door physics is CODE→SoT",
    }
    t0 = time.time()
    try:
        req = urllib.request.Request(models_url, method="GET")
        with urllib.request.urlopen(req, timeout=1.2) as r:
            body = json.loads(r.read().decode())
        data = body.get("data") or []
        root = None
        for m in data:
            if m.get("id") == ops_model:
                root = m.get("root") or m.get("id")
                break
        if not root and data:
            root = data[0].get("root") or data[0].get("id")
        ops.update(
            {
                "ok": True,
                "latency_ms": int((time.time() - t0) * 1000),
                "root": str(root or "")[:120],
                "ids": [m.get("id") for m in data[:6]],
                "summary": (
                    f"Ops online ({ops_model}). SoT headcount is CODE door buffer + face emb; "
                    "ops is judgment owner for tools / LIVE-vs-SCREEN / voice discretion — "
                    "not a continuous gemma/cosmos guess column."
                ),
            }
        )
    except Exception as e:
        ops["error"] = str(e)[:160]
        ops["summary"] = "ops down — inventory still updates from CODE face buffer only."

    bench_on = os.environ.get("RAIL_ORACLE_BENCH", "0") not in ("0", "false", "no")
    mode = os.environ.get("RAIL_ORACLE_MODE", "off").strip().lower()
    try:
        from .room_events import snapshot as room_events_snapshot

        re_snap = room_events_snapshot()
    except Exception as e:
        re_snap = {"ok": False, "error": str(e)[:120], "last": None}
    return {
        "schema": "house_prod_feed/v1",
        "ok": True,
        "oracle_bench_enabled": bench_on and mode not in ("off", "0", "false"),
        "shadows": {
            "gemma": "retired",
            "cosmos": "retired",
            "code_guess_column": "retired",
            "note": "Continuous A/B/C oracle guesses off for prod (2026-08-02)",
        },
        "ops": ops,
        "presence": {
            "headcount": snap.get("headcount"),
            "count_certain": snap.get("count_certain"),
            "count_trust": snap.get("count_trust"),
            "camera_mode": snap.get("camera_mode"),
            "camera_blind": snap.get("camera_blind"),
            "frame_age_sec": snap.get("frame_age_sec"),
            "people": snap.get("people") or [],
            "auth": snap.get("auth"),
            "ts_local": snap.get("ts_local"),
        },
        "last_event": snap.get("last_event") or {},
        "last_room_event": re_snap.get("last"),
        "room_events": {
            "n": re_snap.get("n"),
            "legal_stations": re_snap.get("legal_stations"),
            "soft_seat_prior": re_snap.get("soft_seat_prior"),
            "tail": (re_snap.get("events") or [])[:5],
        },
        "links": {
            "panel": _ports.url("stt", "/panel"),
            "presence": os.environ.get("RAIL_PRESENCE", _ports.url("presence", "/v1/presence")),
            "door_mjpeg": _ports.camera("/v1/mjpeg"),
            "feed_ui": _ports.url("presence", "/oracle"),
            "room_events": _ports.url("presence", "/v1/room_events"),
        },
    }


def usefulness_probe(svc: PresenceService) -> dict:
    """
    Fail green-lies (same class as :8005 up but dead).
    - health.ok must reflect capture usefulness, not process liveness alone
    - live http/v4l: capture frame_age (from grabber X-Capture-Ts) must be fresh
    - grabber /health must be ok when mode=http
    - frame_seq must advance over cycles (stalled seq = frozen pipeline)
    - count_certain ⇒ every person named
    """
    h = svc.health()
    st = svc.get()
    mode = (h.get("camera_mode") or "auto").lower()
    fails: list[str] = []
    if st.schema != "room_presence/v1":
        fails.append("bad schema")
    uptime = float(h.get("uptime_sec") or 0)
    if svc.cycles < 1 and uptime > 45:
        fails.append("no successful cycles after 45s")
    cycle_age = h.get("cycle_age_sec")
    door_first = bool(h.get("door_first", True))
    max_cycle = 30 if door_first else 90
    if cycle_age is not None and cycle_age > max_cycle:
        fails.append(f"stale cycle_age {cycle_age} — presence loop stopped")
    # REAL capture age (from grabber timestamp), not "time since we stamped now"
    if mode in ("http", "v4l"):
        if st.frame_age_sec > 8:
            fails.append(
                f"stale capture frame_age {st.frame_age_sec}s "
                f"(room pixels not fresh — grabber/loop dead or frozen)"
            )
        g = h.get("grabber")
        if g is not None and not g.get("ok"):
            fails.append(f"grabber not ok: {g.get('error') or g}")
        stall_limit = 20 if door_first else 5
        if int(h.get("seq_stall_cycles") or 0) >= stall_limit:
            fails.append(
                f"frame_seq stalled {h.get('seq_stall_cycles')} cycles "
                f"(last_seq={h.get('last_frame_seq')})"
            )
    # STONE: continuous room-tour is a bug when door_first
    if door_first and int(h.get("sweep_cycles") or 0) > 3 and int(h.get("door_cycles") or 0) < 1:
        fails.append("door_first but no door cycles — stuck in sweep-only?")
    if st.camera_blind and mode in ("http", "v4l") and uptime > 30:
        fails.append("camera_blind while in live mode")
    if st.count_certain:
        for p in st.people:
            if not p.name:
                fails.append("certain but unnamed person")
                break
    # health.ok should already encode the above; surface if it disagrees
    if not h.get("ok") and not fails:
        fails.append("health.ok false")
    ok = len(fails) == 0
    return {
        "ok": ok,
        "fails": fails,
        "health": h,
        "snapshot": {
            "headcount": st.headcount,
            "count_certain": st.count_certain,
            "camera_mode": st.camera_mode,
            "count_trust": st.count_trust,
            "camera_open": st.camera_open,
            "camera_blind": st.camera_blind,
            "frame_age_sec": st.frame_age_sec,
            "people": [p.name for p in st.people],
            "door_names": st.door_names,
            "frame_seq": (st.last_event or {}).get("frame_seq"),
        },
    }


def see_route(svc, body: dict) -> tuple[int, dict]:
    """POST /v1/see {embedding: [...], source}: is this face the enrolled owner? A match on a real look source puts
    the owner on the roster (assert_present); AUTH itself stays the camera path's (Secure needs the door)."""
    from .enroll_store import _finite_row, bad_id
    from .enrollments import load_enrolled, match
    from .sweep import MATCH_SIM
    from .visit_lamp import user_id as _uid

    vec = _finite_row(body.get("embedding") if isinstance(body.get("embedding"), list) else None)
    if vec is None:
        return 400, {"ok": False, "error": "a list of numbers"}
    source = str(body.get("source") or "").strip().lower()
    import numpy as np

    name, sim = match(np.asarray(vec, dtype=np.float32), load_enrolled(), threshold=MATCH_SIM)
    owner = _uid()
    matched = bool(name) and name == owner and not bad_id(owner) and source not in ("", "door", "guess", "ring")
    if source == "oracle" and body.get("faces") is not None:
        # the oracle's look, with how many faces it saw: a fixed camera's seat view (fixed_cam, 2026-10-11)
        try:
            from . import fixed_cam
            fixed_cam.note_look(name=name if name else "", sim=float(sim), faces=body.get("faces"), owner=owner)
        except Exception:  # noqa: BLE001
            pass
    if matched:
        try:
            svc.assert_present([owner], source=source or "look")
        except Exception:  # noqa: BLE001
            pass
    return 200, {"ok": True, "matched": matched, "sim": round(float(max(sim, 0.0)), 4), "who": name}


def enroll_route(svc, body: dict) -> tuple[int, dict]:
    """POST /v1/enroll {owner, faces: [[...], ...]}: the owner's numbers into <state home>/enroll.json (never a
    picture); the identity bank re-reads it on its next load."""
    from . import paths as _paths
    from .enroll_store import accept_faces, bad_id, owner_slug, write_enroll
    from .visit_lamp import user_id as _uid

    vecs, err = accept_faces(body)
    if vecs is None:
        return 400, {"ok": False, "error": err}
    owner = owner_slug(str(body.get("owner") or _uid()))
    if not owner or bad_id(owner):
        return 400, {"ok": False, "error": "the owner is a name"}
    doc = write_enroll(_paths.HOME, owner, vecs)
    try:
        svc.engine.enrolled = {}            # the bank re-reads on the next sweep
    except Exception:  # noqa: BLE001
        pass
    return 200, {"ok": True, "owner": doc["owner"], "faces": len(doc["faces"])}


def people_reload_route(svc, body: dict) -> tuple[int, dict]:
    """POST /v1/people/reload {why}: the face bank re-reads the identity store on its next look (round 41, .101 round 40
    F1: a removed person stayed in the bank until presence restarted, and every check of the User read them). Setup's
    removal, forget and cancel post it."""
    try:
        eng = svc.engine
        eng.enrolled = {}
        eng._enrolled_ts = 0.0
    except Exception as e:  # noqa: BLE001
        return 200, {"ok": False, "error": type(e).__name__}
    print("[presence] the face bank re-reads the people (%s)" % str(body.get("why") or "asked")[:80], flush=True)
    return 200, {"ok": True}


def people_who_route(svc, body: dict, snap=None) -> tuple[int, dict]:
    """POST /v1/people/who: one look at the camera, every face named against the whole bank at the match bar
    (sweep.MATCH_SIM): {"ok", "faces": [{"name", "sim"}], "n_faces"}. Round 41 (.101 round 40 F1): Add/Edit IDs asks it
    at the person's Ready, so a face already kept under another name is asked about before anything is recorded."""
    from .enrollments import match as _match
    from .sweep import MATCH_SIM
    try:
        import cv2
        import numpy as np
        if snap is None:
            from . import room_sweep as _rs
            snap = _rs._snapshot
        jpeg = snap()
        svc.engine.ensure()
        face, enrolled = svc.engine.face, svc.engine.enrolled
        im = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        dets = list(face.detect(im) or []) if (face is not None and im is not None) else []
    except Exception as e:  # noqa: BLE001
        return 200, {"ok": False, "error": type(e).__name__, "faces": [], "n_faces": 0}
    out = []
    for fobj in dets:
        try:
            emb = face.embed(fobj)
            name, sim = _match(emb, enrolled, threshold=MATCH_SIM) if emb is not None else (None, -1.0)
        except Exception:  # noqa: BLE001
            continue
        out.append({"name": str(name or ""), "sim": round(float(sim if sim is not None else -1.0), 3)})
    return 200, {"ok": True, "faces": out, "n_faces": len(dets)}


# ROUND 46 (the local review of kit crissa-063a2813, 2026-10-23, the launch blocker): presence answered any web page
# open in the buyer's browser, with `Access-Control-Allow-Origin: *` on every JSON answer and on the camera proxy. A
# site could read who is in the room, the visitors, a soft ID, the camera; a cross-site form post could replace the
# owner's face (/v1/enroll) or store one (/v1/store_face). Every do_* asks the guard first (Host and Origin must be this
# machine; a cross-site no-Origin GET needs a loopback Referer) and an answer names only a loopback Origin back, so the
# Wall on :8099 still reads /v1/presence across ports. urllib callers (the player, Setup, the ear, grabber) send none of
# those headers and pass. Kill: CRISSA_LOOPBACK_GUARD=0 (the old open port and the old `*`).
def _cors_origin(headers) -> str:
    """Access-Control-Allow-Origin here: the loopback Origin echoed, '' = no header; `*` only under the kill."""
    return _guard.allow_origin(headers) if _guard.guard_on() else "*"


def _own_names(bind) -> tuple:
    """Round 46: a server bound to one named address (RAIL_PRESENCE_HOST) answers to that name as well as to loopback;
    a wildcard bind (0.0.0.0, ::) adds nothing, so a LAN caller by name is refused until the install names it."""
    b = str(bind or "").strip().strip("[]").lower()
    return () if b in ("", "0.0.0.0", "::", "*") else (b,)


# Round 46: a Content-Length of -1 read the socket until the peer closed, a word in it was a 500, and a JSON body that
# was not an object was a 500 from the first body.get(). All three are a 400 before any route runs. No size cap by
# default: the biggest body is a camera still in base64 and its size is the camera's, not ours to guess; an install
# that wants one names it in bytes (RAIL_PRESENCE_BODY_MAX). Kill: CRISSA_PRESENCE_BODY_CHECK=0 (the old reads).
def _body_check_on() -> bool:
    return os.environ.get("CRISSA_PRESENCE_BODY_CHECK", "1").strip().lower() not in ("0", "false", "no", "off")


def _body_cap() -> int | None:
    try:
        n = int(os.environ.get("RAIL_PRESENCE_BODY_MAX", "").strip() or 0)
    except ValueError:
        return None
    return n if n > 0 else None


# Round 46: POST /v1/store_face {"frame": <path>} read ANY file the process could open and fed it to the face store
# (and its error text came back). Every caller in the pack sends image_b64; a named frame must now sit under this
# module's own state (the state home: presence state, records, the evidence it wrote) or the identity store, after
# symlinks are resolved. Kill: CRISSA_PRESENCE_PATH_CONFINE=0 (any path, as before).
def _path_confine_on() -> bool:
    return os.environ.get("CRISSA_PRESENCE_PATH_CONFINE", "1").strip().lower() not in ("0", "false", "no", "off")


def confined_path(raw: str) -> pathlib.Path | None:
    """The file a request body names, when it is under this module's own homes; None = refused."""
    try:
        p = pathlib.Path(str(raw or "")).expanduser()
        if not str(raw or "").strip():
            return None
        if not _path_confine_on():
            return p
        p = p.resolve()
        for root in (_paths.HOME, _paths.ROOT, _paths.RECORDS, _paths.IDENTITY):
            r = pathlib.Path(root).expanduser().resolve()
            if p == r or r in p.parents:
                return p if p.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def make_handler():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _guarded(self) -> bool:
            return _guard.check(self, names=_own_names(os.environ.get("RAIL_PRESENCE_HOST", "")))

        def _json(self, code: int, obj: dict):
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # Crissa omni-log (Brave display) polls presence + inspector; round 46: only this machine's own pages
            _acao = _cors_origin(self.headers)
            if _acao:
                self.send_header("Access-Control-Allow-Origin", _acao)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)

        def _html(self, code: int, html: str):
            body = html.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._guarded():
                return
            path = urlparse(self.path).path
            svc = get_svc()
            if path in ("/", "/tree", "/index", "/help"):
                return self._html(200, _here(TREE_HTML))
            if path in ("/inventory", "/presence", "/v1/page"):
                return self._html(200, _here(HTML))
            if path in ("/chat", "/tui", "/cosmos", "/v1/cosmos/ui"):
                from .cosmos_operator import CHAT_HTML

                return self._html(200, _here(CHAT_HTML))
            if path in ("/v1/cosmos/status", "/cosmos/status"):
                from .cosmos_operator import get_chat_session

                return self._json(200, get_chat_session().status())
            if path in ("/v1/cosmos/history", "/cosmos/history"):
                from .cosmos_operator import get_chat_session

                sess = get_chat_session()
                return self._json(
                    200, {"history": sess.history[-100:], "status": sess.status()}
                )
            if path in ("/v1/cosmos/report", "/cosmos/report"):
                from .cosmos_operator import build_room_speaker_report, load_report

                rep = load_report()
                if rep is None:
                    rep = build_room_speaker_report(
                        in_room=[],
                        speaking=None,
                        note="no report published yet",
                        source="empty",
                    )
                return self._json(200, rep)
            if path in ("/v1/cosmos/kit", "/cosmos/kit"):
                from .cosmos_operator import default_kit_dir

                kit = default_kit_dir()
                files = []
                if kit.is_dir():
                    for p in sorted(kit.rglob("*")):
                        if p.is_file():
                            files.append(str(p.relative_to(kit)))
                return self._json(
                    200, {"kit_dir": str(kit), "files": files, "n": len(files)}
                )
            if path in ("/oracle", "/compare", "/v1/oracle", "/v1/compare", "/feed"):
                from .service import occupancy_on

                if not occupancy_on():
                    return self._html(
                        200,
                        (
                            "<!doctype html><meta charset=utf-8>"
                            "<title>occupancy mill parked</title>"
                            "<body style='font:14px ui-monospace,monospace;"
                            "background:#0c0d10;color:#e8eaed;padding:24px'>"
                            "<p>Occupancy mill is parked. Identity is FaceID visor "
                            "(<a href='/visor' style='color:#7dffc3'>/visor</a>). "
                            "Door Argus / CODE compare is not the product.</p>"
                            "</body>"
                        ),
                    )
                return self._html(200, _here(COMPARE_HTML))
            if path in ("/v1/faceid/visor", "/faceid", "/visor", "/v1/visor"):
                from .faceid_visor import VISOR_HTML

                return self._html(200, VISOR_HTML)
            if path in ("/v1/camera/mjpeg", "/camera/mjpeg"):
                cam = os.environ.get(
                    "RAIL_CAMERA_URL", _ports.camera()
                ).rstrip("/")
                qs = urlparse(self.path).query
                url = f"{cam}/v1/mjpeg"
                if qs:
                    url += "?" + qs
                try:
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=20) as r:
                        ctype = (
                            r.headers.get("Content-Type")
                            or "multipart/x-mixed-replace; boundary=frame"
                        )
                        self.send_response(200)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Cache-Control", "no-store")
                        _acao = _cors_origin(self.headers)      # round 46: never `*` on the room camera
                        if _acao:
                            self.send_header("Access-Control-Allow-Origin", _acao)
                            self.send_header("Vary", "Origin")
                        self.end_headers()
                        while True:
                            chunk = r.read(16384)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            try:
                                self.wfile.flush()
                            except BrokenPipeError:
                                return
                except Exception as e:
                    return self._json(
                        502, {"ok": False, "error": f"camera:{type(e).__name__}"}
                    )
                return
            if path in ("/visitors", "/security", "/v1/visitors/page"):
                from .visitors_log import VISITORS_HTML

                return self._html(200, VISITORS_HTML)
            if path in ("/v1/visitors",):
                from .visitors_log import list_today

                out = list_today(visitor_today=svc._visitor_today_pub())
                if isinstance(out, dict):
                    out["door_skips"] = svc._door_skips_pub()
                return self._json(200, out)
            if path in ("/v1/visit_lamp", "/visit_lamp.json"):
                return self._json(200, svc.visit_lamp_pub())
            if path in ("/visit-lamp", "/visit_lamp"):
                from .visit_lamp import PROBE_HTML

                return self._html(200, PROBE_HTML)
            if path in ("/v1/door/still.jpg", "/door/still.jpg"):
                try:
                    from . import room_sweep as _rs

                    jpeg = _rs._snapshot()
                except Exception as e:
                    return self._json(503, {"ok": False, "error": f"{type(e).__name__}"})
                if not jpeg:
                    return self._json(503, {"ok": False, "error": "no_still"})
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(jpeg)
                return
            if path in ("/v1/faceid", "/faceid.json"):
                return self._json(200, svc.faceid_hud())
            if path in ("/v1/speaker", "/speaker"):
                return self._json(200, {"ok": True, **(svc._speaker_pub() or {})})
            if path in ("/v1/strangers", "/strangers"):
                from .stranger_x import list_strangers

                return self._json(200, {"ok": True, "strangers": list_strangers()})
            if path.startswith("/v1/strangers/") and path.endswith("/photo.jpg"):
                from .stranger_x import photo_bytes

                xid = path[len("/v1/strangers/") : -len("/photo.jpg")]
                raw = photo_bytes(xid)
                if not raw:
                    return self._json(404, {"ok": False, "error": "no photo"})
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return
            if path.startswith("/v1/visitors/face/"):
                from pathlib import Path as _P

                fn = _P(path.split("/v1/visitors/face/", 1)[-1]).name
                if not fn.endswith(".jpg"):
                    return self._json(404, {"ok": False, "error": "bad face"})
                root = svc._faceidog_root() / "faces" / fn
                if not root.is_file():
                    return self._json(404, {"ok": False, "error": "no face"})
                raw = root.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return
            if path in ("/v1/faceid/crop.jpg", "/faceid/crop.jpg"):
                raw = getattr(svc, "_faceid_crop", None)
                if not raw:
                    return self._json(404, {"ok": False, "error": "no crop"})
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return
            if path in ("/v1/faceid/frame.jpg", "/faceid/frame.jpg"):
                raw = getattr(svc, "_faceid_frame", None)
                if not raw:
                    return self._json(404, {"ok": False, "error": "no frame"})
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return
            # What Argus and FaceID were actually handed, per door event.
            if path in ("/v1/evidence", "/evidence"):
                from .evidence_page import render as _ev_render

                return self._html(200, _ev_render())
            if path in ("/v1/evidence/asset", "/evidence/asset"):
                from urllib.parse import parse_qs as _pq

                from .evidence_page import asset as _ev_asset

                _q = _pq(urlparse(self.path).query)
                got = _ev_asset(
                    (_q.get("e") or [""])[0], (_q.get("f") or [""])[0]
                )
                if not got:
                    return self._json(404, {"ok": False, "error": "not found"})
                data, ctype = got
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(data)
                return None
            if path in ("/v1/prod_feed", "/prod_feed", "/v1/feed"):
                return self._json(200, build_prod_feed(get_svc()))
            if path in ("/v1/room_events", "/room_events"):
                from .room_events import snapshot as room_events_snapshot

                return self._json(200, room_events_snapshot())
            if path in ("/v1/room_events/last", "/room_events/last"):
                from .room_events import last as room_events_last

                items = room_events_last(1)
                return self._json(
                    200,
                    {
                        "ok": True,
                        "schema": "room_event/v1",
                        "last": items[0] if items else None,
                    },
                )
            # Init geometry SoT — eligible seats for stamp UI + LLM (no hardcode 1–12)
            if path in (
                "/v1/room_geometry",
                "/room_geometry",
                "/v1/seats/eligible",
                "/seats/eligible",
            ):
                try:
                    from .seat_slots import geometry_public

                    return self._json(200, geometry_public())
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:200]})
            if path in ("/v1/inventory/snapshot", "/inventory/snapshot"):
                from .inventory_persist import snapshot_path
                import pathlib

                p = snapshot_path()
                if not p.is_file():
                    return self._json(404, {"ok": False, "error": "no snapshot"})
                try:
                    return self._json(200, json.loads(p.read_text()))
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:160]})
            if path in ("/v1/oracle/all", "/oracle.json"):
                # Museum/API: still return bench snapshot, but mark retired when off
                snap = get_bench().snapshot()
                meta = snap.setdefault("meta", {})
                meta["prod_note"] = (
                    "Gemma/Cosmos/CODE continuous guesses retired for prod. "
                    "Use GET /v1/prod_feed (ops + SoT)."
                )
                meta["prod_feed"] = _ports.url("presence", "/v1/prod_feed")
                return self._json(200, snap)
            if path in ("/v1/oracle/code", "/v1/oracle/gemma", "/v1/oracle/cosmos"):
                key = path.rsplit("/", 1)[-1]
                return self._json(
                    200,
                    {
                        "provider": key,
                        "ok": False,
                        "retired": True,
                        "note": f"{key} continuous oracle retired — see /v1/prod_feed",
                    },
                )
            if path in ("/v1/oracle/pack", "/oracle/pack"):
                snap = get_bench().snapshot()
                pack_path = (snap.get("meta") or {}).get("pack_path") or (
                    (snap.get("meta") or {}).get("sensors") or {}
                ).get("pack_path")
                if pack_path:
                    try:
                        from pathlib import Path as _P
                        return self._json(200, json.loads(_P(pack_path).read_text()))
                    except Exception as e:
                        return self._json(500, {"ok": False, "error": str(e)[:200]})
                return self._json(404, {"ok": False, "error": "no pack yet — Run all"})
            if path in ("/v1/oracle/strip.jpg", "/oracle/strip.jpg", "/v1/oracle/strip"):
                snap = get_bench().snapshot()
                sensors = (snap.get("meta") or {}).get("sensors") or {}
                cam = sensors.get("camera") or {}
                live = sensors.get("live_strip") or {}
                from pathlib import Path as _P
                work = _P.home() / "projects/bench/presence-abc/work/oracle_live"
                # Prefer bench.board_strip_path (honors look-around hold → grid stays)
                try:
                    pinned = get_bench().board_strip_path()
                except Exception:
                    pinned = None
                mode = (snap.get("meta") or {}).get("live_strip_mode") or live.get(
                    "mode"
                )
                candidates = []
                if mode == "look_around_hold" or (
                    live.get("mode") == "look_around_hold"
                ):
                    candidates = [
                        pinned,
                        live.get("path"),
                        (snap.get("meta") or {}).get("live_strip_path"),
                        str(work / "look_around_vision.jpg"),
                        str(work / "look_around_pack_vision.jpg"),
                    ]
                else:
                    candidates = [
                        pinned,
                        live.get("path"),
                        (snap.get("meta") or {}).get("live_strip_path"),
                        cam.get("strip_live"),
                        cam.get("strip_path"),
                        sensors.get("frame"),
                        str(work / "strip_live.jpg"),
                    ]
                strip = None
                for c in candidates:
                    if c and _P(c).is_file() and _P(c).stat().st_size > 500:
                        strip = c
                        break
                if not strip:
                    strips = sorted(work.glob("strip_*.jpg"))
                    strip = str(strips[-1]) if strips else None
                # Live camera fallback when oracle models are off / strip is stale black
                try:
                    raw = _P(strip).read_bytes() if strip else b""
                except Exception:
                    raw = b""
                # mean brightness of sample — all-black strip is useless
                dead = len(raw) < 800
                if not dead and len(raw) > 2000:
                    try:
                        import numpy as _np
                        import cv2 as _cv2

                        arr = _cv2.imdecode(_np.frombuffer(raw, dtype=_np.uint8), _cv2.IMREAD_COLOR)
                        if arr is None or float(arr.mean()) < 3.0:
                            dead = True
                    except Exception:
                        pass
                if dead or not strip:
                    # Build a simple strip from current door snapshot (always-on camera)
                    try:
                        import urllib.request as _u
                        import numpy as _np
                        import cv2 as _cv2
                        from pathlib import Path as _P2

                        cam_url = __import__("os").environ.get(
                            "RAIL_PRESENCE_SNAPSHOT_URL",
                            _ports.camera("/snapshot"),
                        )
                        jpeg = _u.urlopen(cam_url, timeout=3).read()
                        frame = _cv2.imdecode(
                            _np.frombuffer(jpeg, dtype=_np.uint8), _cv2.IMREAD_COLOR
                        )
                        if frame is not None:
                            # 8-tile time-lookalike so UI "slice" is never empty
                            h, w = frame.shape[:2]
                            tile_w = max(80, w // 6)
                            tiles = []
                            for i in range(8):
                                x0 = min(w - tile_w, int(i * (w - tile_w) / 7)) if w > tile_w else 0
                                tiles.append(frame[:, x0 : x0 + tile_w])
                            strip_img = _np.hstack(tiles)
                            ok, buf = _cv2.imencode(".jpg", strip_img, [int(_cv2.IMWRITE_JPEG_QUALITY), 85])
                            if ok:
                                raw = buf.tobytes()
                                live_path = work / "strip_live.jpg"
                                live_path.parent.mkdir(parents=True, exist_ok=True)
                                live_path.write_bytes(raw)
                                strip = str(live_path)
                    except Exception as e:
                        if not strip:
                            return self._json(
                                404,
                                {
                                    "ok": False,
                                    "error": f"no strip / camera fallback failed: {e}"[:200],
                                },
                            )
                if not strip and not raw:
                    return self._json(404, {"ok": False, "error": "no strip yet — waiting for live ring"})
                try:
                    from pathlib import Path as _P
                    if not raw:
                        raw = _P(strip).read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:200]})
            if path in ("/health", "/v1/health"):
                return self._json(200, svc.health())
            if path in ("/v1/argus_dev", "/argus_dev"):
                from .argus_dev import snapshot as _argus_snap

                return self._json(200, _argus_snap())
            if path in ("/v1/presence", "/presence.json", "/occupancy", "/json"):
                out = svc.get().to_dict()
                # .101 10-19: the fixed camera's face check (laptop_auth, every 30-60 s): its time, score and the next
                # one's time, so the Wall's face score fades across the gap to the next check (house_wall_ascii_render)
                la = getattr(svc, "_laptop_auth", None)
                if la is not None and isinstance(out, dict):
                    last = dict(getattr(la, "last", None) or {})
                    out["face_check"] = {"at": last.get("at"), "sim": last.get("sim"), "found": bool(last.get("found")),
                                         "next_at": float(getattr(la, "next_at", 0.0) or 0.0)}
                return self._json(200, out)
            if path in ("/v1/fanout_stats", "/fanout_stats"):
                from . import door_fanout as _df

                st = _df.stats()
                st["ok"] = True
                st["pending_work"] = _df.pending_work()
                return self._json(200, st)
            if path in ("/v1/leave_package", "/leave_package"):
                try:
                    return self._json(200, svc.leave_package())
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # GET /v1/enter_still/<person_id>/<i>.jpg
            if path.startswith("/v1/enter_still/") or path.startswith("/enter_still/"):
                parts = [p for p in path.strip("/").split("/") if p]
                # v1 enter_still id i.jpg  OR enter_still id i.jpg
                try:
                    if parts[0] == "v1":
                        parts = parts[1:]
                    # enter_still, person_id, file
                    pid = parts[1]
                    fname = parts[2] if len(parts) > 2 else "0.jpg"
                    idx = int(fname.replace(".jpg", "").replace(".jpeg", "") or "0")
                    raw = svc.get_enter_still_bytes(pid, idx)
                    if not raw:
                        return self._json(404, {"ok": False, "error": "no still"})
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                except Exception as e:
                    return self._json(400, {"ok": False, "error": str(e)[:200]})
            if path in ("/v1/probe", "/probe"):
                rep = usefulness_probe(svc)
                return self._json(200 if rep["ok"] else 503, rep)
            if path in ("/v1/inspector", "/v1/inspector/status", "/inspector", "/inspector/status"):
                try:
                    from .inspector import status as inspector_status

                    presence = svc.get().to_dict()
                    return self._json(200, inspector_status(presence))
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Body-count match game (GET UI + photos + last result)
            if path in ("/v1/count_match", "/count_match"):
                from .body_audit import COUNT_MATCH_HTML

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                data = COUNT_MATCH_HTML.encode("utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path in ("/v1/count_match/last", "/count_match/last"):
                from .body_audit import last_count_check

                return self._json(200, last_count_check())
            if path.startswith("/v1/count_match/photo/") or path.startswith(
                "/count_match/photo/"
            ):
                from .body_audit import LAST_DIR
                from urllib.parse import unquote as _unq

                name = _unq(path.rsplit("/", 1)[-1])
                if ".." in name or "/" in name or "\\" in name:
                    return self._json(400, {"ok": False, "error": "bad name"})
                fp = LAST_DIR / name
                if not fp.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                data = fp.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            return self._json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            if not self._guarded():
                return
            path = urlparse(self.path).path
            svc = get_svc()
            if _body_check_on():
                n = _guard.body_length(self.headers, _body_cap())
                if n is None:
                    return self._json(400, {"ok": False, "error": "bad Content-Length"})
            else:
                n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            if _body_check_on() and not isinstance(body, dict):
                return self._json(400, {"ok": False, "error": "the body is a JSON object"})
            # Security snap -> FaceID -> X# -> Inspector (the User 2026-08-31).
            # SAME PATH AS DOOR MOTION: door_fanout calls record_event(kind=
            # "enter") for a body at the door; this is record_event(kind=
            # "speech") for someone who asked twice for something gated. Same
            # matcher, same X# folders, so the person who tried it is grouped
            # with the face that walked in rather than filed somewhere new.
            #
            # It lives HERE and not in rail-voice because record_event embeds
            # with FaceID via get_face_engine(). Calling it from the voice
            # process would load a second copy of the face model.
            # The oracle module posts each look's numbers here (modules/oracle, source "oracle"); Init and the
            # Connect page post the owner's enrollment. Same shapes as the stdlib lamp these replaced (2026-10-09).
            if path in ("/v1/see", "/see"):
                return self._json(*see_route(svc, body))
            if path in ("/v1/enroll", "/enroll"):
                return self._json(*enroll_route(svc, body))
            if path in ("/v1/people/reload", "/people/reload"):
                return self._json(*people_reload_route(svc, body))
            if path in ("/v1/people/who", "/people/who"):
                return self._json(*people_who_route(svc, body))
            if path in ("/v1/strangers/record", "/strangers/record"):
                try:
                    import base64

                    from .stranger_x import record_event

                    b64 = body.get("jpeg_b64") or ""
                    try:
                        jpeg = base64.b64decode(b64) if b64 else b""
                    except Exception:
                        return self._json(400, {"ok": False, "error": "bad_b64"})
                    if len(jpeg) < 512:
                        return self._json(400, {"ok": False, "error": "no_jpeg"})
                    kind = str(body.get("kind") or "speech")
                    out = record_event(
                        kind=kind,
                        jpeg=jpeg,
                        source=str(body.get("source") or "security"),
                        event_id=body.get("event_id") or None,
                    )
                    return self._json(200, {"ok": True, **(out or {})})
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:400]})
            # Cosmos operator chat TUI (tool loop)
            if path in ("/v1/cosmos/chat", "/cosmos/chat"):
                try:
                    from .cosmos_operator import chat_turn

                    text = str(body.get("text") or body.get("message") or "")
                    if body.get("capability"):
                        text = text or "start"
                    # long GPU turn — run sync so client can wait; UI disables buttons
                    out = chat_turn(text)
                    return self._json(200 if out.get("ok") else 503, out)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:400]})
            if path in ("/v1/cosmos/report", "/cosmos/report"):
                try:
                    from .cosmos_operator import (
                        build_room_speaker_report,
                        publish_report,
                    )

                    rep = build_room_speaker_report(
                        in_room=body.get("in_room") or body.get("people"),
                        speaking=body.get("speaking"),
                        note=body.get("note"),
                        source=str(body.get("source") or "http_post"),
                    )
                    path_out = publish_report(rep)
                    return self._json(
                        200, {"ok": True, "path": str(path_out), "report": rep}
                    )
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Force one A/B/C oracle cycle (slow if GPU models on)
            # On-demand look-around tool (Cosmos): facefinder per stop → door home
            if path in ("/v1/oracle/look_around", "/oracle/look_around", "/v1/look_around"):
                try:
                    wait = body.get("wait", True) is not False

                    def _run_la():
                        try:
                            get_bench().look_around_for_cosmos(
                                stop_ids=body.get("stop_ids"),
                                engage_sec=float(body.get("engage_sec") or 1.8),
                                run_cosmos=body.get("cosmos", True) is not False,
                            )
                        except Exception:
                            pass

                    if wait:
                        out = get_bench().look_around_for_cosmos(
                            stop_ids=body.get("stop_ids"),
                            engage_sec=float(body.get("engage_sec") or 1.8),
                            run_cosmos=body.get("cosmos", True) is not False,
                        )
                        return self._json(200 if out.get("ok") else 503, out)
                    import threading

                    threading.Thread(
                        target=_run_la, name="look-around-tool", daemon=True
                    ).start()
                    return self._json(
                        202,
                        {
                            "ok": True,
                            "accepted": True,
                            "async": True,
                            "tool": "look_around",
                            "routine": False,
                            "note": "facefinder per room stop → door home; poll /v1/oracle/cosmos",
                        },
                    )
                except Exception as e:
                    return self._json(
                        500,
                        {"ok": False, "tool": "look_around", "error": str(e)[:300]},
                    )
            if path in ("/v1/oracle/refresh", "/oracle/refresh"):
                try:
                    # run in thread so HTTP does not block minutes
                    import threading

                    def _run():
                        try:
                            get_bench().force_once()
                        except Exception:
                            pass

                    threading.Thread(target=_run, name="oracle-refresh", daemon=True).start()
                    return self._json(
                        202,
                        {
                            "ok": True,
                            "accepted": True,
                            "mode": "shadow",
                            "hint": "poll GET /v1/oracle/all — then POST /v1/oracle/vote",
                            "side_by_side": _ports.url("presence", "/oracle"),
                        },
                    )
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Operator: who beat the driver (code)?
            if path in ("/v1/oracle/vote", "/oracle/vote"):
                try:
                    return self._json(
                        200,
                        get_bench().vote(
                            winner=str(body.get("winner") or body.get("vote") or ""),
                            truth=body.get("truth") or body.get("note"),
                            run_id=body.get("run_id"),
                        ),
                    )
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Delta-body COUNT CHECK (the User 08-07 / 08-10): faceless body count
            # vs empty-room refs at clock stations. NO FaceFinder on this path.
            # FF is only for specific USER auth (use_facefind on /v1/auth).
            # {"capture_refs": true} re-shoots empty refs (hc==0 unless force).
            # {"tilt_deg": -20} for seated rightmost; default -20.
            # Argus error-check: 8 fps 6→12, write body+seat, return firmware.
            # Distinct from /v1/error_check_count (evidence-only count_check).
            if path in ("/v1/error_check", "/error_check"):
                try:
                    from .error_check import run_error_check

                    return self._json(200, run_error_check(svc))
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            if path in ("/v1/id_stations", "/id_stations"):
                return self._json(
                    410,
                    {
                        "ok": False,
                        "error": "id_stations_removed",
                        "use": "POST /v1/inspector {op:who_is_here} or /v1/look_at",
                    },
                )
            if path in (
                "/v1/body_audit",
                "/body_audit",
                "/v1/count_check",
                "/count_check",
                "/v1/error_check_count",
                "/error_check_count",
            ):
                try:
                    from .body_audit import capture_refs, run_audit, count_check

                    if body.get("capture_refs"):
                        return self._json(
                            200,
                            capture_refs(svc, force=bool(body.get("force"))),
                        )
                    tilt = body.get("tilt_deg", body.get("tilt"))
                    stns = body.get("stations")
                    if isinstance(stns, str):
                        stns = [
                            int(x) for x in stns.split(",") if str(x).strip().lstrip("-").isdigit()
                        ]
                    elif isinstance(stns, list):
                        stns = [int(x) for x in stns]
                    else:
                        stns = None
                    kw: dict = {}
                    if tilt is not None:
                        try:
                            kw["tilt_deg"] = int(tilt)
                        except (TypeError, ValueError):
                            pass
                    if stns:
                        kw["stations"] = stns
                    if body.get("mode"):
                        kw["mode"] = str(body.get("mode"))
                    if path.rstrip("/").endswith(
                        ("count_check", "error_check_count")
                    ):
                        return self._json(200, count_check(svc, **kw))
                    return self._json(200, run_audit(svc, **kw))
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Body-count match game stamp (POST)
            if path in ("/v1/count_match/stamp", "/count_match/stamp"):
                try:
                    from .body_audit import stamp_count_truth

                    return self._json(
                        200,
                        stamp_count_truth(
                            bodies=int(body.get("bodies") or body.get("n") or 0),
                            note=str(body.get("note") or ""),
                            stamped_by=str(body.get("stamped_by") or "user"),
                            system_guess=body.get("system_guess"),
                            sot_headcount=body.get("sot_headcount"),
                        ),
                    )
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:200]})
            # Multi-stop audit / end-of-task reconcile (STONE: only allowed sweep)
            # Not FaceFinder. Face ID on door buffer; FF only opt-in user auth.
            if path in ("/v1/sweep", "/sweep", "/v1/reconcile"):
                try:
                    st = svc.run_sweep_once(reason="reconcile")
                    return self._json(200, st.to_dict())
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Immediate auth sweep — FaceFinder ONLY if body.use_facefind true
            # (specific USER auth). Default: seat orient, no FF.
            if path in ("/v1/auth", "/auth"):
                try:
                    refused = svc._auth_refused_lights_out()
                    if refused:
                        return self._json(
                            403,
                            {
                                "ok": False,
                                "refused": True,
                                "reason": "lights_out",
                                "error": refused.get("error"),
                                "lights_out": True,
                                "luminance": refused.get("luminance"),
                                "threshold": refused.get("threshold"),
                                "note": refused.get("note"),
                                "presence": refused.get("presence"),
                            },
                        )
                    st = svc.run_sweep_once(reason="auth")
                    return self._json(200, st.to_dict())
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Voice while empty → surprise camera check (queued, no cooldown).
            # Default async so STT (short timeout) does not hold the ear open.
            if path in ("/v1/surprise", "/surprise"):
                try:
                    pid = body.get("person_id") or body.get("name")
                    src = str(body.get("source") or "voice_while_empty")
                    force = bool(body.get("force"))
                    wait = bool(body.get("wait"))
                    media = bool(body.get("media") or body.get("media_on"))
                    # body_count (default) | user_auth (facefinder every stop)
                    mode = body.get("mode") or body.get("surprise_mode")
                    if wait:
                        st = svc.surprise_from_voice(
                            person_id=pid,
                            source=src,
                            force=force,
                            mode=mode,
                            media=media,
                        )
                        return self._json(200, st.to_dict())
                    out = svc.enqueue_surprise(
                        person_id=pid,
                        source=src,
                        force=force,
                        mode=mode,
                        media=media,
                    )
                    return self._json(202, out)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Harvest / motion pack: FaceID crop + Argus describe. No SoT.
            if path in ("/v1/pack_consider", "/pack_consider"):
                try:
                    from pathlib import Path as _Path

                    from .argus_notify import consider_pack
                    from .door_capture import EVENTS_DIR

                    pack = str(
                        (body or {}).get("pack_dir")
                        or (body or {}).get("pack")
                        or ""
                    )
                    if not pack:
                        return self._json(
                            400, {"ok": False, "error": "need pack_dir"}
                        )
                    p = _Path(pack).resolve()
                    allowed = EVENTS_DIR.resolve()
                    if p != allowed and allowed not in p.parents:
                        return self._json(
                            400,
                            {"ok": False, "error": "pack_dir outside events"},
                        )
                    notify = bool((body or {}).get("notify", True))
                    write_face = bool((body or {}).get("write_face", True))
                    wait = bool((body or {}).get("wait"))

                    def _run() -> dict:
                        face_eng = None
                        enrolled = None
                        try:
                            svc.engine.ensure()
                            face_eng = svc.engine.face
                            enrolled = getattr(svc.engine, "enrolled", None)
                        except Exception:
                            face_eng = None
                        return consider_pack(
                            p,
                            engine=face_eng,
                            enrolled=enrolled,
                            notify=notify,
                            write_face=write_face,
                        )

                    if wait:
                        return self._json(200, _run())
                    import threading

                    threading.Thread(
                        target=_run, name="pack-consider", daemon=True
                    ).start()
                    return self._json(
                        202,
                        {
                            "ok": True,
                            "accepted": True,
                            "async": True,
                            "pack": p.name,
                        },
                    )
                except Exception as e:
                    return self._json(
                        500, {"ok": False, "error": str(e)[:400]}
                    )
            # Inspector — ID stamp + emb store gate (shadow-first; no headcount)
            if path in ("/v1/inspector", "/inspector"):
                try:
                    from .inspector import handle as inspector_handle

                    presence = svc.get().to_dict()
                    out = inspector_handle(body if isinstance(body, dict) else {}, presence)
                    return self._json(200, out)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:400]})
            # Quick error-correction peek (training curve) — buffer blast default
            if path in ("/v1/peek", "/peek", "/v1/error_peek"):
                try:
                    from .error_peek import run_peek

                    mode = str(
                        (body or {}).get("mode")
                        or (body or {}).get("peek")
                        or "buffer_blast"
                    )
                    out = run_peek(mode, **{
                        k: (body or {})[k]
                        for k in ("n", "stride", "scale", "gap_sec", "settle", "include_right")
                        if k in (body or {})
                    })
                    # optional: feed frames to inspector auto harvest only via door FOV snap
                    if (body or {}).get("inspect"):
                        from .inspector import maybe_auto_inspect

                        presence = svc.get().to_dict()
                        insp = maybe_auto_inspect(
                            presence,
                            trigger="manual",
                            background=bool((body or {}).get("background", True)),
                        )
                        out["inspector"] = insp
                    return self._json(200, out)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:400]})
            # Look-around = rescan room (Fable scan pulse / init room_scan C9)
            # Only difference init vs everyday: range full | limited
            # Room look-around / rescan (product). Differs from cosmos
            # POST /v1/look_around (facefinder seat peeks) — do not collide.
            # Naming: look_around == rescan; init = full range only.
            if path in (
                "/v1/reinit_scan",
                "/reinit_scan",
                "/v1/rescan",
                "/rescan",
                "/v1/rescan_room",
                "/rescan_room",
                "/v1/room_scan",
                "/room_scan",
            ):
                try:
                    from .reinit_scan import (
                        run_flexible_scan,
                        capture_reference_frames,
                        list_refs,
                    )
                    from .look_around import (
                        look_around as room_look_around,
                        init_room_scan,
                        load_geometry,
                    )

                    mode = str((body or {}).get("mode") or "auto").lower()
                    force_pan = bool((body or {}).get("force_pan"))
                    force_refs = bool(
                        (body or {}).get("force_refs")
                        or (body or {}).get("refs")
                        or mode in ("user_reinit", "desk_move", "refs", "reference")
                    )
                    if mode in ("refs_only", "reference_only"):
                        return self._json(200, capture_reference_frames(force=True))
                    if mode in ("list_refs", "refs_status"):
                        return self._json(200, list_refs())
                    if mode in ("geometry", "geom", "blocked"):
                        return self._json(200, {"ok": True, **load_geometry()})
                    # Product path: room rescan / init full (range only differs)
                    path_is_room = path.rstrip("/").endswith(
                        ("rescan_room", "room_scan", "rescan")
                    )
                    if mode in (
                        "room",
                        "rescan_room",
                        "look_around",
                        "lookaround",
                        "rescan",
                        "init",
                        "full",
                        "c9",
                        "360",
                    ) or path_is_room:
                        if mode in ("init", "full", "c9", "360") or bool(
                            (body or {}).get("init")
                        ):
                            tok = None
                            if str((body or {}).get("token") or "") == "register":
                                # Init's room sweep under Register's token (step 3): User mode only, given back after
                                tok = svc.register_take("init room sweep")
                                if not tok.get("ok"):
                                    return self._json({"not_user_mode": 403, "off": 404}.get(str(tok.get("error")), 409), tok)
                            from .camera_token import borrow as _borrow

                            try:
                                with _borrow(tok.get("lease_id") if tok else None):
                                    out = init_room_scan(
                                        inspect=body.get("inspect", True) is not False,
                                        capture_refs_if_empty=bool(
                                            body.get("capture_refs", True)
                                        ),
                                        source=str(body.get("source") or mode or "init"),
                                    )
                            finally:
                                if tok:
                                    svc.register_give_back()
                        else:
                            rng = body.get("range") or "limited"
                            out = room_look_around(
                                range=str(rng),
                                init=False,
                                inspect=body.get("inspect", True) is not False,
                                capture_refs_if_empty=body.get(
                                    "capture_refs", True
                                )
                                is not False,
                                source=str(body.get("source") or mode or "rescan"),
                            )
                        return self._json(200, out)
                    presence = svc.get().to_dict()
                    out = run_flexible_scan(
                        force_pan=force_pan or mode in ("seat",),
                        force_refs=force_refs,
                        inspect=body.get("inspect", True) is not False,
                        presence=presence,
                    )
                    return self._json(200, out)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:400]})
            # Seat slots table (null / pin / view)
            if path in ("/v1/seat_slots", "/seat_slots"):
                try:
                    action = str((body or {}).get("action") or "get").lower()
                    if action in ("null", "null_seats", "clear"):
                        return self._json(200, svc.rescan_null_seats())
                    if action in ("pin", "pin_user"):
                        return self._json(
                            200,
                            svc.pin_user_seat(
                                user_id=body.get("person_id") or body.get("user_id"),
                                seat=body.get("seat"),
                            ),
                        )
                    return self._json(
                        200, {"ok": True, "slots": svc.seat_slots_view()}
                    )
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            if path in ("/v1/inventory/save", "/inventory/save"):
                try:
                    svc._save_inventory_disk(force=True)
                    from .inventory_persist import snapshot_path

                    return self._json(
                        200, {"ok": True, "path": str(snapshot_path())}
                    )
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:200]})
            # Re-auth: orient; facefind ONLY if body.facefind true (default false —
            # facefinder-last law). Facefind always followed by reconcile peek.
            # body.sweep true → DESIGN-ROOM-SWEEP: one lease, desk auth + station
            # tour, one atomic auth+headcount publish.
            if path in ("/v1/reauth", "/reauth"):
                try:
                    if body.get("sweep"):
                        from .room_sweep import run_room_sweep

                        out = run_room_sweep(
                            svc,
                            reason=str(body.get("reason") or "on_demand"),
                            source="reauth",
                        )
                        return self._json(200 if out.get("ok") else 503, out)
                    st = svc.reauth(
                        person_id=body.get("person_id") or body.get("name") or "user",
                        use_facefind=bool(body.get("facefind")),
                    )
                    return self._json(200, st.to_dict())
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Visit lamp — AUTH family neighbour, not /v1/instruct (count physics).
            if path in ("/v1/visit_lamp", "/visit_lamp"):
                op = str(body.get("op") or "").strip().lower()
                if op in ("click", "release"):
                    # ONE TOGGLE (round 17 follow-ups): a step down out of Secure needs his face first
                    refused = svc.security_lower_refused(op)
                    if refused is not None:
                        return self._json(200, refused)
                if op == "face_check":
                    # the face-match sequence's look for the kit's settings (a camera that turns: FaceFinder at his seat)
                    return self._json(200, svc.visit_lamp_face_check())
                if op == "click":
                    return self._json(200, svc.visit_lamp_click())
                if op == "scan":
                    return self._json(200, svc.visit_lamp_scan())
                if op == "hold":
                    # The password is read once and never echoed or logged.
                    out = svc.visit_lamp_hold(str(body.get("password") or ""))
                    body.pop("password", None)
                    return self._json(200 if out.get("ok") else 403, out)
                if op == "release":
                    return self._json(200, svc.visit_lamp_release())
                if op == "guest_reset":
                    # Occupancy switch / operator: guest the User. No hunt.
                    return self._json(
                        200,
                        svc.drop_to_guest(
                            reason=str(body.get("reason") or "guest_reset")
                        ),
                    )
                if op == "secure_reauth":
                    # occupancy: keeps the lamp. A restart still token-FaceFinds or guests.
                    return self._json(
                        200,
                        svc.visit_secure_reauth(
                            reason=str(body.get("reason") or "occupancy")
                        ),
                    )
                return self._json(
                    409,
                    {
                        "ok": False,
                        "error": "op must be click|scan|hold|release|guest_reset|secure_reauth|face_check",
                    },
                )
            if path in ("/v1/camera/aim", "/camera/aim"):
                # the User's joystick: {op: nudge, dx, dy} degrees, or {op: home}
                try:
                    out = svc.camera_aim(str(body.get("op") or "nudge"),
                                         float(body.get("dx") or 0), float(body.get("dy") or 0))
                except Exception as e:  # noqa: BLE001
                    out = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
                code = 200 if out.get("ok") else (403 if out.get("error") == "not_authed" else 409)
                return self._json(code, out)
            if path in ("/v1/camera/register", "/camera/register"):
                # Register's token (Init step 3): {op: take|give_back, why}. Off unless RAIL_REGISTER_TOKEN=1.
                # Init v2 (the User 2026-10-04, RAIL_REGISTER_AIM, default off): op renew | aim, and give_back
                # reports the verified home. Off, renew/aim answer 404 {"error": "off"}.
                from .camera_token import register_aim_on as _reg_aim_on

                try:
                    op = str(body.get("op") or "take").lower()
                    if op in ("renew", "aim"):
                        if not _reg_aim_on():
                            out = {"ok": False, "error": "off", "why": "RAIL_REGISTER_AIM is off"}
                        elif op == "renew":
                            out = svc.register_renew(str(body.get("why") or "register"))
                        else:
                            out = svc.register_aim(body)
                    elif op in ("give_back", "home", "release"):
                        out = svc.register_give_back_verified() if _reg_aim_on() else svc.register_give_back()
                    else:
                        vis = str(body.get("visitor") or "")       # ADD A VISITOR's own take (round 20)
                        out = (svc.register_take(str(body.get("why") or "register"), visitor=vis) if vis
                               else svc.register_take(str(body.get("why") or "register")))
                except Exception as e:  # noqa: BLE001
                    out = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
                # 409 = the camera is not Register's to aim (not_holder, lost, busy, aim_refused, camera_leased);
                # 502 = the camera did not do what it was told (no picture is ever returned then)
                code = 200 if out.get("ok") else {
                    "not_user_mode": 403, "off": 404, "bad_request": 400,
                    "facefinder_off_failed": 502, "aim_failed": 502, "readback_mismatch": 502, "no_frames": 502,
                }.get(str(out.get("error")), 409)
                return self._json(code, out)
            if path in ("/v1/visit_lamp/click", "/visit_lamp/click"):
                refused = svc.security_lower_refused("click")       # ONE TOGGLE: a step down needs his face
                if refused is not None:
                    return self._json(200, refused)
                return self._json(200, svc.visit_lamp_click())
            if path in ("/v1/visit_lamp/scan", "/visit_lamp/scan"):
                return self._json(200, svc.visit_lamp_scan())
            # Command Auth gate: sticky or door-local chase (mode=full for parade)
            if path in (
                "/v1/ensure_user_auth",
                "/ensure_user_auth",
                "/v1/ensure_auth",
                "/ensure_auth",
            ):
                try:
                    voice_matched = body.get("voice_matched")
                    if voice_matched is None:
                        voice_matched = body.get("voice_ok")
                    if voice_matched is None and body.get("match") is not None:
                        voice_matched = bool(body.get("match"))
                    sim = body.get("voice_sim")
                    if sim is None:
                        sim = body.get("voice_emb_sim")
                    try:
                        sim_f = float(sim) if sim is not None else None
                    except (TypeError, ValueError):
                        sim_f = None
                    mode = body.get("mode") or body.get("hunt_mode")
                    out = svc.ensure_user_auth(
                        person_id=body.get("person_id") or body.get("name"),
                        voice_matched=bool(voice_matched),
                        voice_sim=sim_f,
                        reason=str(body.get("reason") or "command"),
                        force_facefind=bool(body.get("force_facefind")),
                        mode=str(mode) if mode else None,
                    )
                    return self._json(200, out)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # AUTH-first: grant face sticky without inventory / Oracle
            if path in (
                "/v1/grant_user_face_auth",
                "/grant_user_face_auth",
                "/v1/auth/grant",
            ):
                try:
                    sim = body.get("sim")
                    if sim is None:
                        sim = body.get("face_sim")
                    try:
                        sim_f = float(sim) if sim is not None else None
                    except (TypeError, ValueError):
                        sim_f = None
                    greet = body.get("greet")
                    if greet is None:
                        greet = True
                    out = svc.grant_user_face_auth(
                        sim=sim_f,
                        source=str(body.get("source") or "http"),
                        greet=bool(greet),
                    )
                    return self._json(200, out)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # AUTH-first: door-local face chase (ring + optional door FF)
            if path in (
                "/v1/chase_user_face_at_door",
                "/chase_user_face_at_door",
                "/v1/auth/chase_door",
            ):
                try:
                    budget = body.get("budget_sec")
                    try:
                        budget_f = float(budget) if budget is not None else None
                    except (TypeError, ValueError):
                        budget_f = None
                    allow_ff = body.get("allow_door_ff")
                    if allow_ff is None:
                        allow_ff = True
                    out = svc.chase_user_face_at_door(
                        budget_sec=budget_f,
                        allow_door_ff=bool(allow_ff),
                    )
                    return self._json(200, out)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Explicit post-facefind reconcile (door→L→door→R→door)
            if path in (
                "/v1/reconcile_after_facefind",
                "/reconcile_after_facefind",
            ):
                try:
                    out = svc.reconcile_after_facefind(
                        reason=str(body.get("reason") or "post_facefind")
                    )
                    st = svc.get().to_dict()
                    st["reconcile_after_facefind"] = out
                    return self._json(200, st)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Mark camera away (eyeball/snap) — count STALE until reconcile
            if path in ("/v1/away", "/away"):
                reason = str(body.get("reason") or "task")
                st = svc.mark_away(reason)
                return self._json(200, st.to_dict())
            # Door observe once (debug)
            if path in ("/v1/door", "/door"):
                try:
                    st = svc.run_door_once()
                    return self._json(200, st.to_dict())
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Voice / operator: assert who is in the room (back-of-head misses face match)
            if path in ("/v1/assert", "/assert", "/v1/present"):
                names = body.get("names") or body.get("people") or []
                if isinstance(names, str):
                    names = [names]
                one = body.get("name")
                if one:
                    names = list(names) + [one]
                if not names and body.get("headcount") is None:
                    return self._json(400, {"ok": False, "error": "need names or name"})
                try:
                    st = svc.assert_present(
                        list(names),
                        unnamed=body.get("unnamed"),
                        source=str(body.get("source") or "voice"),
                        replace=bool(body.get("replace")),
                    )
                    return self._json(200, st.to_dict())
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Soft ID stamp from ears (Shell/voice) — no facefinder
            if path in ("/v1/soft_id", "/soft_id"):
                pid = body.get("person_id") or body.get("name")
                if not pid:
                    return self._json(400, {"ok": False, "error": "need person_id"})
                try:
                    st = svc.apply_soft_id(
                        str(pid),
                        certain=bool(body.get("certain")),
                        source=str(body.get("source") or "voice"),
                        confirm=bool(body.get("confirm")),
                        create_if_missing=bool(body.get("create_if_missing", True)),
                    )
                    return self._json(200, st.to_dict())
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # VL delta apply (null/+1/−1) — tests / operator / voters
            if path in ("/v1/vl_delta", "/vl_delta"):
                try:
                    st = svc.apply_vl_delta(
                        int(body.get("entered") or 0),
                        int(body.get("left") or 0),
                        source=str(body.get("source") or "operator"),
                        stamp_names=body.get("stamp_names") or body.get("names"),
                        note=body.get("note"),
                        face_certain=bool(body.get("face_certain")),
                        force=bool(body.get("force")),
                        enter_traits=body.get("enter_traits"),
                        leave_traits=body.get("leave_traits"),
                        leave_person_id=body.get("leave_person_id"),
                        leave_match_conf=(
                            float(body["leave_match_conf"])
                            if body.get("leave_match_conf") is not None
                            else None
                        ),
                        leave_match_method=body.get("leave_match_method"),
                        enter_still_hint=body.get("enter_still_hint"),
                        leave_guest=body.get("leave_guest"),
                        station_guess=body.get("station_guess"),
                        pet=body.get("pet"),
                    )
                    return self._json(200, st.to_dict())
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Unified instruction port into SoT (count / roster / auth)
            # All ops update the same object; auth feed for other rails is GET.auth
            if path in ("/v1/instruct", "/instruct"):
                try:
                    st = svc.apply_instruction(body if isinstance(body, dict) else {})
                    return self._json(200, st.to_dict())
                except ValueError as e:
                    return self._json(400, {"ok": False, "error": str(e)[:300]})
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Voice / operator: person left (clears sticky — must not be immortal)
            if path in ("/v1/oracle_visit", "/oracle_visit"):
                try:
                    out = svc.apply_oracle_visit(body if isinstance(body, dict) else {})
                    code = 200 if out.get("ok") else 400
                    return self._json(code, out)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            if path in ("/v1/room_busy", "/room_busy"):
                try:
                    on = body.get("on")
                    if on is None:
                        on = body.get("busy")
                    out = svc.set_room_busy(
                        bool(on),
                        reason=str(body.get("reason") or ""),
                    )
                    return self._json(200, out)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # Argus names a frame; this runs the embedder on it. Serving, not
            # selecting — the code heuristics keep running in parallel.
            if path in ("/v1/face_embed", "/face_embed"):
                try:
                    from .face_embed import embed_frame

                    return self._json(200, embed_frame(
                        str(body.get("event_id") or ""),
                        str(body.get("frame") or ""),
                    ))
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # The one camera tool: look at a station (or raw pan), optional
            # tilt, come home. the User 2026-08-16: "I would like for crissa to be
            # able to look at anything with it for interaction via video ...
            # Just give her the lease on tilt and return at close of tool to
            # firmware." The lease/de-zoom/door-home contract is in seat_shot,
            # so every caller gets it whether they remember to or not.
            # The fan-out keeps counters and a last_error that nothing could
            # read, so a silent stall looked identical to a quiet room (the User's
            # 12:55 entry produced no row at all and no way to ask why).
            if path in ("/v1/faceid/scan", "/faceid/scan"):
                try:
                    return self._json(200, svc.faceid_scan())
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            if path in ("/v1/f2f", "/f2f"):
                try:
                    on = body.get("on")
                    if on is None:
                        on = body.get("f2f")
                    wait = bool(body.get("wait"))
                    look = body.get("look")
                    if look is None:
                        look = bool(on)
                    hour = body.get("hour")
                    if hour is None:
                        hour = body.get("beam_hour") or body.get("station")
                    out = svc.set_f2f(
                        bool(on),
                        reason=str(body.get("reason") or "command"),
                        look=bool(look),
                        force=bool(body.get("force")),
                        hour=hour,
                        person_id=body.get("person_id"),
                    )
                    if wait and out.get("f2f") and look:
                        # look is async; marker is already on
                        pass
                    return self._json(200, out)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            if path in ("/v1/blind_gap", "/blind_gap"):
                try:
                    out = svc.note_blind_gap(
                        from_ts=body.get("from_ts"),
                        to_ts=body.get("to_ts"),
                        holder=body.get("holder"),
                        why=body.get("why"),
                        seq=body.get("seq"),
                    )
                    return self._json(200, out)
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            if path in ("/v1/fanout_stats", "/fanout_stats"):
                try:
                    import threading as _th
                    import time as _t

                    from . import door_fanout as _df

                    st = _df.stats()
                    st["seen_cached"] = len(_df._SEEN)
                    st["action_seen_cached"] = len(_df._ACTION_SEEN)
                    st["pending_work"] = _df.pending_work()
                    st["last_fire_age_sec"] = (
                        round(_t.time() - _df._LAST_FIRE[0], 1)
                        if _df._LAST_FIRE[0] else None
                    )
                    st["poll_alive"] = any(
                        "fanout" in (t.name or "").lower()
                        or "door" in (t.name or "").lower()
                        for t in _th.enumerate()
                    )
                    st["threads"] = sorted(t.name for t in _th.enumerate())
                    return self._json(200, {"ok": True, **st})
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            # "Argus, that is another person" — an AUTH'd human names a face and it
            # is stored. Replaces the Inspector's store gate, which existed to
            # decide identity when nobody was there to say.
            # A TURNED STILL'S LOOK (round 17, 2026-10-13): one still's face and yaw, nothing stored. Voice ID's
            # turned grab looks through this until the head has turned (store_face.probe); an answer without a face is
            # still 200, so the coach tells "no route here" (an older presence: 404) from "no face yet".
            if path in ("/v1/face_yaw", "/face_yaw"):
                import base64 as _b64

                from .store_face import probe as _probe_face

                try:
                    jpeg = _b64.b64decode(str(body.get("image_b64") or ""))
                except Exception:
                    return self._json(400, {"ok": False, "error": "bad image_b64"})
                if not jpeg:
                    return self._json(400, {"ok": False, "error": "need image_b64"})
                return self._json(200, _probe_face(jpeg))
            if path in ("/v1/store_face", "/store_face"):
                import base64 as _b64

                from .store_face import store as _store_face

                pid = body.get("person_id") or body.get("name")
                if not pid:
                    return self._json(400, {"ok": False, "error": "need person_id"})
                jpeg = None
                if body.get("image_b64"):
                    try:
                        jpeg = _b64.b64decode(body["image_b64"])
                    except Exception:
                        return self._json(400, {"ok": False, "error": "bad image_b64"})
                elif body.get("frame"):
                    fp = confined_path(str(body["frame"]))       # round 46: only this module's own files
                    if fp is None:
                        return self._json(400, {"ok": False, "error": "frame is not a file of this install's state"})
                    try:
                        jpeg = fp.read_bytes()
                    except Exception as e:
                        return self._json(400, {"ok": False, "error": str(e)[:200]})
                else:
                    # default: whatever the camera is looking at right now
                    try:
                        from . import room_sweep as _rs

                        jpeg = _rs._snapshot()
                    except Exception as e:
                        return self._json(503, {"ok": False, "error": str(e)[:200]})
                try:
                    presence = svc.get().to_dict()
                except Exception:
                    presence = {}
                try:
                    res = _store_face(
                        str(pid), jpeg, presence=presence,
                        said_by=str(body.get("said_by") or "voice")[:40],
                        note=str(body.get("note") or "")[:200],
                        enroll=bool(
                            body.get("enroll")
                            or str(body.get("said_by") or "") in ("voice-register", "enroll")
                        ),
                        pose=str(body.get("pose") or "")[:20],
                        match_front=bool(body.get("match_front")),   # round 35: a turned still must be his face
                        station=str(body.get("station") or "")[:40],
                        prefix=str(body.get("prefix") or "")[:80],
                    )
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
                return self._json(200 if res.get("ok") else 400, res)
            if path in ("/v1/look_at", "/look_at"):
                import base64 as _b64

                from .seat_shot import look_at, stations

                tgt = body.get("station") or body.get("target") or body.get("pan")
                if tgt is None:
                    return self._json(200, {"ok": True, "stations": sorted(stations())})
                # `tilt_deg` is degrees, converted once by the camera profile (Init v2, the User
                # 2026-10-04); `tilt` stays raw units. look_at clamps and snaps either, as before.
                from .camera_profile import tilt_from_body

                try:
                    look_tilt = tilt_from_body(body)
                except (TypeError, ValueError) as e:
                    return self._json(400, {"ok": False, "error": "bad_tilt_deg", "why": str(e)[:120]})
                tok = None
                if str(body.get("token") or "") == "register":
                    # Init's Crissa-seat look under Register's token (step 3): User mode only, given back after
                    tok = svc.register_take(str(body.get("owner") or "init look")[:40])
                    if not tok.get("ok"):
                        return self._json({"not_user_mode": 403, "off": 404}.get(str(tok.get("error")), 409), tok)
                try:
                    from .camera_token import borrow as _borrow

                    with _borrow(tok.get("lease_id") if tok else None):
                        r = look_at(
                            tgt,
                            tilt=look_tilt,
                            facefinder=bool(body.get("facefinder", True)),
                            n=int(body.get("n") or 3),
                            owner=str(body.get("owner") or "look_at")[:40],
                        )
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
                finally:
                    if tok:
                        svc.register_give_back()
                frames = r.pop("frames", []) or []
                r["n_frames"] = len(frames)
                r["facefinder"] = bool(body.get("facefinder", True))
                # FF is for a usable face. Name whoever is enrolled in these
                # frames (not just the User). Read-only — caller writes SoT.
                try:
                    seen = svc._identify_all_in_jpegs(
                        list(frames),
                        source=f"look_at:{tgt}",
                        stop=str(tgt).strip().lower() if tgt is not None else None,
                    )
                    r["names"] = seen.get("names") or {}
                    r["n_faces"] = int(seen.get("n_faces") or 0)
                except Exception as e:
                    r["names"] = {}
                    r["n_faces"] = 0
                    r["id_error"] = f"{type(e).__name__}: {e}"[:160]
                if body.get("images"):
                    r["images"] = [
                        _b64.b64encode(f).decode() for f in frames[:6] if f
                    ]
                # Keep one still so Argus can look without another lease.
                if frames:
                    try:
                        from pathlib import Path as _P

                        dest = _paths.DATA / "body_audit_last"
                        dest.mkdir(parents=True, exist_ok=True)
                        name = f"look_at_{str(tgt).replace('/', '_')[-24:]}.jpg"
                        (dest / name).write_bytes(frames[-1])
                        r["photo"] = name
                        r["photo_url"] = f"/v1/count_match/photo/{name}"
                    except Exception:
                        pass
                return self._json(200, r)
            if path in ("/v1/rest_empty", "/rest_empty"):
                try:
                    return self._json(200, svc.adopt_rest_empty())
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            if path in ("/v1/leave", "/leave"):
                names = body.get("names") or body.get("people") or []
                if isinstance(names, str):
                    names = [names]
                one = body.get("name")
                if one:
                    names = list(names) + [one]
                if not names:
                    return self._json(400, {"ok": False, "error": "need name or names"})
                try:
                    st = svc.assert_leave(
                        list(names),
                        source=str(body.get("source") or "operator"),
                    )
                    return self._json(200, st.to_dict())
                except Exception as e:
                    return self._json(500, {"ok": False, "error": str(e)[:300]})
            return self._json(404, {"ok": False, "error": "not found"})

    return H


def _here(html: str) -> str:
    """The pages name this install's own addresses (2026-10-10): the house spelled its ports into them."""
    return (html.replace("__PRESENCE_HOSTPORT__", _ports.url("presence").split("//", 1)[1])
            .replace("__PRESENCE__", _ports.url("presence")).replace("__STT__", _ports.url("stt"))
            .replace("__CAMERA__", _ports.camera()))


def main(host: str | None = None, port: int | None = None) -> None:
    host = host or os.environ.get("RAIL_PRESENCE_HOST", "127.0.0.1")
    port = int(port or os.environ.get("RAIL_PRESENCE_PORT") or _ports.port("presence"))
    _ports.guard_camera()                         # the camera switch, read once more now the launch script has set it
    svc = get_svc()
    faces, legacy = face_sources()
    # Oracle A/B/C board: inject local presence without HTTP self-call
    set_presence_getter(lambda: svc.get().to_dict())
    # DNA: VL/CODE write into DoorInventory SoT (the User 2026-07-27)
    set_sot_hooks(
        apply_vl=lambda entered, left, source="gemma", stamp_names=None, note=None, face_certain=False, force=False: svc.apply_vl_delta(
            entered,
            left,
            source=source,
            stamp_names=stamp_names,
            note=note,
            face_certain=face_certain,
            force=force,
        ),
        code_write_through=lambda names, source="code", sims=None, live_face=None: svc.code_write_through(
            names, source=source, sims=sims or {}, live_face=live_face or {}
        ),
    )
    bench = get_bench()
    print(
        f"[presence] starting :{port} camera={svc.camera.mode} "
        f"faces={faces} legacy={legacy}",
        flush=True,
    )
    print(
        f"[presence] oracle board http://{host}:{port}/oracle "
        f"(GPU models={'on' if bench.snapshot().get('meta',{}).get('gpu') else 'off'})",
        flush=True,
    )
    class _QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            # Local pollers (board tabs) cancel requests constantly; a closed
            # socket is not an error worth a traceback in the journal.
            import sys as _sys
            exc = _sys.exception()
            if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                return
            super().handle_error(request, client_address)

    httpd = _QuietServer((host, port), make_handler())
    print(f"[presence] http://{host}:{port}/presence", flush=True)
    try:
        httpd.serve_forever()
    finally:
        bench.stop()
        svc.stop()


if __name__ == "__main__":
    main()
