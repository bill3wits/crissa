"""Dashboard page for :8190/ — same visual family as rail-presence."""

DASHBOARD_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>rail-stt — ear log</title>
<style>
body{font-family:ui-sans-serif,system-ui,sans-serif;background:#0c0d10;color:#e8eaed;margin:0;padding:20px}
h1{font-size:1.2rem;margin:0 0 4px}
.sub{color:#8b93a7;font-size:13px;margin-bottom:16px}
.card{background:#14161c;border:1px solid #252833;border-radius:10px;padding:14px 16px;max-width:900px;margin-bottom:12px}
.big{font-size:1.8rem;font-weight:700}
.ok{color:#3dd68c}.warn{color:#e6b84d}.bad{color:#f07178}.dim{color:#8b93a7}
table{border-collapse:collapse;width:100%;max-width:900px}
td,th{text-align:left;padding:6px 8px;border-bottom:1px solid #252833;font-size:14px;vertical-align:top}
code{background:#1a1d24;padding:1px 5px;border-radius:4px}
pre{font-size:11px;color:#8b93a7;overflow:auto;max-height:260px}
/* scrollable live log — table itself was growing without a viewport */
#logWrap{max-height:min(55vh,520px);overflow:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch}
#logWrap table{max-width:none}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;margin-right:6px;background:#1a1d24}
button{background:#1a1d24;color:#e8eaed;border:1px solid #252833;border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px;margin-right:6px}
button.primary{background:#1e3a5f;border-color:#3a6ea5}
button:disabled{opacity:.45;cursor:wait}
input{background:#1a1d24;color:#e8eaed;border:1px solid #252833;border-radius:8px;padding:6px 10px;font-size:13px;width:280px}
a{color:#7eb8ff}
</style>
</head>
<body>
<h1>rail-stt — ear log</h1>
<div class="sub">turns + presence stamp · <a href="/panel">control panel</a> · <a href="/health">health</a> · <a href="/v1/turns">turns.json</a> · presence <a href="http://127.0.0.1:8191/" target="_blank">:8191</a></div>

<div class="card" id="healthCard"><span class="dim">loading health…</span></div>
<div class="card" id="presenceCard"><span class="dim">loading presence…</span></div>

<div class="card">
  <button class="primary" id="earOpen" onclick="earOpen()">Open ear</button>
  <button id="earClose" onclick="earClose()">Close ear</button>
  <button id="l3" onclick="listen(3)">Listen 3s</button>
  <button id="l5" onclick="listen(5)">Listen 5s</button>
  <input id="fixture" placeholder="tests/fixtures/speech.wav"/>
  <button onclick="runFile()">Run file</button>
  <button onclick="clearLog()">Clear log</button>
  <span class="pill" id="actmsg"></span>
  <div class="sub" style="margin-top:8px">
    <b>Open ear</b> = continuous listen (mic → log). Long idle ~15 min / max 2 h.
    This is <b>hearing</b>, not Crissa speaking. Her mouth is the player's (<code>/v1/say</code>)
    (no “Voice ON” toggle on this page). One-shot Listen still available for tests.
  </div>
</div>

<div class="card" style="padding-bottom:8px">
  <div class="sub" style="margin-bottom:6px">live log · scrolls · polls 1.5s · ear must be open for continuous speech</div>
  <div id="logWrap">
  <table>
    <thead><tr><th>time</th><th>via</th><th>text</th><th>person</th><th>source</th><th>conf</th><th>engine</th><th>ms</th><th>rms</th><th>roster</th></tr></thead>
    <tbody id="turnsBody"><tr><td colspan="10" class="dim">no turns yet — speak, Listen, or run a fixture</td></tr></tbody>
  </table>
  </div>
</div>

<div class="card">
  <div class="sub" style="margin-bottom:6px">last turn raw</div>
  <pre id="raw">—</pre>
</div>

<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let busy = false;

function pill(txt, cls){ return `<span class="pill ${cls||''}">${esc(txt)}</span>`; }

function renderHealth(h){
  const el = document.getElementById('healthCard');
  if(!h){ el.innerHTML = pill('health unreachable','bad'); return; }
  const pk = (h.live_ear ?? h.parakeet)
    ? pill('live-ear ok','ok')
    : pill('live-ear DOWN','warn');
  const vb = Object.entries(h.voice_bank||{}).map(([k,v])=>`${k}:${v}`).join(' ') || 'empty';
  const ear = h.ear || {};
  const earPill = ear.open
    ? pill(`ear OPEN ${ear.open_for_sec||0}s idle=${ear.idle_sec||0}s speech=${ear.speech_chunks||0}`,'ok')
    : pill(ear.closed_reason ? `ear closed (${ear.closed_reason})` : 'ear closed','dim');
  el.innerHTML =
    pill(`engine ${h.engine}`) + pk +
    pill(`whisper ${h.whisper||'off'}`) +
    pill(`voice bank ${vb}`) +
    (h.presence_ok ? pill('presence link ok','ok') : pill('presence link down','warn')) +
    earPill;
}

function renderPresence(p){
  const el = document.getElementById('presenceCard');
  if(!p || p.headcount == null){ el.innerHTML = pill('presence down — stamps fail-open','bad'); return; }
  const trust = p.count_trust || (p.count_certain ? 'fact' : 'uncertain');
  const trustCls = trust === 'fact' ? 'ok' : 'warn';
  const people = (p.people||[]).map(x =>
    pill(x.name || 'unknown', x.certain ? 'ok' : 'warn')).join('') || pill('nobody','dim');
  el.innerHTML =
    `<span class="big ${p.camera_blind ? 'bad' : trustCls}">${esc(p.headcount)}</span> ` +
    pill(`trust ${trust}`, trustCls) +
    (p.camera_blind ? pill('camera blind','bad') : '') + people;
}

function renderTurns(t){
  const body = document.getElementById('turnsBody');
  const wrap = document.getElementById('logWrap');
  const turns = (t && t.turns) || [];
  if(!turns.length){
    body.innerHTML = '<tr><td colspan="10" class="dim">no turns yet — Open ear or Listen</td></tr>';
    document.getElementById('raw').textContent = '—';
    return;
  }
  // keep scroll position if user scrolled up; pin to top (newest) only if near top
  const stickTop = !wrap || wrap.scrollTop < 40;
  body.innerHTML = turns.map(u => {
    const st = u.stamp || {};
    const time = (u.ts_local || '').slice(11) || '—';
    const text = u.error
      ? `<span class="bad">${esc(u.error)}</span>`
      : (u.text ? esc(u.text) : '<span class="dim">(no text)</span>');
    const person = u.person_id ? `<b class="ok">${esc(u.person_id)}</b>` : '<span class="dim">—</span>';
    const conf = st.confidence != null ? Number(st.confidence).toFixed(2) : '—';
    const roster = (st.roster||[]).join(', ');
    return `<tr><td>${esc(time)}</td><td>${esc(u.via||'')}</td><td>${text}</td>` +
      `<td>${person}</td><td>${esc(st.source ?? '—')}</td><td>${conf}</td>` +
      `<td>${esc(u.engine ?? '')}</td><td>${u.decode_ms ?? ''}</td>` +
      `<td>${u.rms_db ?? ''}</td><td class="dim">${esc(roster)}</td></tr>`;
  }).join('');
  document.getElementById('raw').textContent = JSON.stringify(turns[0], null, 2);
  if (stickTop && wrap) wrap.scrollTop = 0;
}

async function tick(){
  const [h, p, t] = await Promise.all([
    fetch('/health',{cache:'no-store'}).then(r=>r.json()).catch(()=>null),
    fetch('/v1/presence_proxy',{cache:'no-store'}).then(r=>r.json()).catch(()=>null),
    fetch('/v1/turns?n=50',{cache:'no-store'}).then(r=>r.json()).catch(()=>null),
  ]);
  renderHealth(h); renderPresence(p); renderTurns(t);
}

function setBusy(b, msg, cls){
  busy = b;
  document.getElementById('l3').disabled = b;
  document.getElementById('l5').disabled = b;
  document.getElementById('earOpen').disabled = b;
  const m = document.getElementById('actmsg');
  m.textContent = msg || '';
  m.className = 'pill ' + (cls || '');
}

async function earOpen(){
  if(busy) return;
  setBusy(true, 'opening ear…', 'warn');
  try{
    const r = await fetch('/v1/ear/open', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        mode:'vad', frame_sec:0.3, hangover_sec:1.8, max_utter_sec:20,
        idle_timeout_sec:900, max_open_sec:7200
      })});
    const j = await r.json().catch(()=>({}));
    if(!r.ok){ setBusy(false, j.error || `error ${r.status}`, 'bad'); }
    else { setBusy(false, j.already_open ? 'ear already open' : 'ear open — speak freely', 'ok'); }
  }catch(e){ setBusy(false, 'ear open failed', 'bad'); }
  tick();
}

async function earClose(){
  try{
    await fetch('/v1/ear/close', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    setBusy(false, 'ear closed', 'dim');
  }catch(e){ setBusy(false, 'ear close failed', 'bad'); }
  tick();
}

async function listen(sec){
  if(busy) return;
  setBusy(true, `listening ${sec}s…`, 'warn');
  try{
    const r = await fetch('/v1/listen', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({seconds: sec})});
    const j = await r.json().catch(()=>({}));
    if(r.status === 409){ setBusy(false, j.error || 'listen busy', 'warn'); }
    else if(!r.ok){ setBusy(false, j.error || `error ${r.status}`, 'bad'); }
    else { setBusy(false, j.text ? 'heard: ' + j.text.slice(0,60) : 'no text (silence?)', j.text ? 'ok' : 'warn'); }
  }catch(e){ setBusy(false, 'listen failed', 'bad'); }
  tick();
}

async function runFile(){
  const path = document.getElementById('fixture').value.trim() || 'tests/fixtures/speech.wav';
  setBusy(true, 'transcribing file…', 'warn');
  try{
    const r = await fetch('/v1/file', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({path})});
    const j = await r.json().catch(()=>({}));
    setBusy(false, r.ok ? (j.text ? 'ok' : 'no text') : (j.error || `error ${r.status}`), r.ok ? 'ok' : 'bad');
  }catch(e){ setBusy(false, 'file failed', 'bad'); }
  tick();
}

async function clearLog(){
  await fetch('/v1/turns', {method:'DELETE'}).catch(()=>{});
  tick();
}

tick();
setInterval(()=>{ if(!busy) tick(); }, 1500);
</script>
</body>
</html>
"""
