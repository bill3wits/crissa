"""Crissa omni-log HTML — served at GET :8190/omni (also mirrored in crissa/display)."""

OMNI_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>House — Crissa surface</title>
<style>
  :root {
    --bg:#0d1214; --rail:#121a1d; --line:#1e2b30; --ink:#dde8e6; --dim:#7d9490;
    --accent:#6fb5ad; --warn:#e0b565; --bad:#e08a76; --good:#63c98f; --id:#9ec5fe;
  }
  * { box-sizing:border-box; }
  html,body { height:100%; margin:0; background:var(--bg); color:var(--ink);
              font:14px/1.5 system-ui,-apple-system,sans-serif; overflow:hidden; }
  #wrap { display:grid; grid-template-columns:min(380px,36vw) 1fr; height:100vh; }
  #rail { background:var(--rail); border-right:1px solid var(--line);
          display:flex; flex-direction:column; min-width:0; }
  #railhead { padding:.65rem .85rem; border-bottom:1px solid var(--line);
              display:flex; align-items:center; gap:.5rem; flex-wrap:wrap; }
  #dot { width:9px; height:9px; border-radius:50%; background:var(--dim); flex:0 0 auto; }
  #dot.live { background:var(--good); box-shadow:0 0 8px var(--good); }
  #railhead b { font-size:.78rem; letter-spacing:.08em; text-transform:uppercase; color:var(--dim); font-weight:600; }
  #presence { margin-left:auto; font-size:.72rem; color:var(--accent);
              font-variant-numeric:tabular-nums; max-width:100%; }
  #idbar { padding:.35rem .85rem; border-bottom:1px solid var(--line);
           font-size:.72rem; color:var(--dim); display:flex; gap:.75rem; flex-wrap:wrap; }
  #idbar .id { color:var(--id); font-weight:600; }
  #idbar .lvl { color:var(--good); }
  #idbar .lvl.provisional { color:var(--warn); }
  #idbar .lvl.none { color:var(--dim); }
  #filt { padding:.3rem .85rem; border-bottom:1px solid var(--line);
          display:flex; gap:.35rem; flex-wrap:wrap; align-items:center; }
  #filt b { font-size:.65rem; color:var(--dim); letter-spacing:.06em; text-transform:uppercase; margin-right:.2rem; }
  #filt button {
    appearance:none; border:1px solid var(--line); background:#0f1618; color:var(--dim);
    border-radius:999px; padding:.12rem .5rem; font:600 .65rem system-ui; cursor:pointer;
    letter-spacing:.04em;
  }
  #filt button:hover { border-color:var(--accent); color:var(--accent); }
  #filt button.on { color:var(--ink); border-color:var(--accent); background:rgba(111,181,173,.12); }
  #log .ev[data-hide="1"] { display:none; }
  #flags { padding:.25rem .85rem; border-bottom:1px solid var(--line);
           font-size:.68rem; min-height:1.4rem; display:flex; gap:.4rem; flex-wrap:wrap;
           align-items:center; color:var(--dim); }
  #flags:empty::after { content:"·"; opacity:.35; }
  #flags .f { padding:.05rem .4rem; border-radius:999px; border:1px solid var(--line);
              font-variant-numeric:tabular-nums; letter-spacing:.02em; }
  #flags .f.on { color:var(--good); border-color:rgba(99,201,143,.35); }
  #flags .f.off { color:var(--bad); border-color:rgba(224,138,118,.45); background:rgba(224,138,118,.08); }
  #flags .f.signal { color:var(--warn); border-color:rgba(224,181,101,.4); }
  #log { flex:1; overflow-y:auto; padding:.35rem 0; }
  .ev { padding:.28rem .85rem; border-bottom:1px solid rgba(255,255,255,.03);
        display:grid; grid-template-columns:40px 1fr; gap:.45rem; align-items:baseline; }
  .t { color:var(--dim); font-size:.68rem; font-variant-numeric:tabular-nums; }
  .m { min-width:0; word-break:break-word; font-size:.8rem; }
  .k { color:var(--accent); font-weight:600; }
  .ev.stt .k { color:var(--id); }
  .ev.insp .k { color:#c4a7e7; }
  .ev.door .k { color:var(--warn); }
  .ev.flag .k { color:var(--dim); }
  .ev.flag.off .m { color:var(--bad); }
  .ev.flag.signal .m { color:var(--warn); }
  .ev.err .k, .ev.err .m { color:var(--bad); }
  .ev.say .m { font-style:italic; }
  .pid { color:var(--id); font-weight:600; }
  .src { color:var(--dim); font-size:.7rem; }
  #railfoot { padding:.4rem .85rem; border-top:1px solid var(--line);
              font-size:.68rem; color:var(--dim); display:flex; gap:.7rem; flex-wrap:wrap; }
  #railfoot span::before { content:"● "; color:var(--dim); }
  #railfoot span.up::before { color:var(--good); }
  #railfoot span.warn::before { color:var(--warn); }
  #stage { position:relative; min-width:0; display:flex; flex-direction:column; }
  #stagebar { flex:0 0 auto; display:flex; gap:.35rem; flex-wrap:wrap; align-items:center;
              padding:.35rem .55rem; border-bottom:1px solid var(--line); background:var(--rail);
              font-size:.68rem; color:var(--dim); }
  #stagebar b { color:var(--dim); font-weight:600; letter-spacing:.06em; text-transform:uppercase; margin-right:.25rem; }
  #stagebar button, #stagebar a.btn {
    appearance:none; border:1px solid var(--line); background:#0f1618; color:var(--ink);
    border-radius:6px; padding:.2rem .5rem; font:inherit; cursor:pointer; text-decoration:none;
  }
  #stagebar button:hover, #stagebar a.btn:hover { border-color:var(--accent); color:var(--accent); }
  #stagebar .mode { margin-left:auto; color:var(--accent); font-variant-numeric:tabular-nums; }
  #stageframe { flex:1; position:relative; min-height:0; }
  #stageframe iframe { width:100%; height:100%; border:0; background:#fff; }
  #idle { position:absolute; inset:0; display:grid; place-items:center;
          color:var(--dim); font-size:.85rem; letter-spacing:.1em;
          text-transform:uppercase; background:var(--bg); }
  body.voice-only #stagebar { opacity:.55; } /* Crissa still uses houseShow; bar stays for CDP */
  /* float=1 — rail-only (shell left iframe). Must fill height so #log can scroll. */
  body.float-mode { overflow: hidden; height: 100%; }
  body.float-mode #wrap {
    grid-template-columns: 1fr;
    height: 100vh;
    height: 100dvh;
  }
  body.float-mode #stage { display:none !important; }
  body.float-mode #rail {
    border-right: 0;
    height: 100%;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
  body.float-mode #log {
    flex: 1 1 auto;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    -webkit-overflow-scrolling: touch;
  }
</style>
</head>
<body>
<div id="wrap">
  <aside id="rail">
    <div id="railhead">
      <span id="dot"></span><b>omni-log</b>
      <span id="presence">—</span>
    </div>
    <div id="idbar">
      <span>stt <span id="stt-id" class="id">—</span> <span id="stt-src" class="src"></span></span>
      <span>insp <span id="insp-id" class="id">—</span> <span id="insp-lvl" class="lvl none">—</span></span>
    </div>
    <div id="filt" title="the User 2026-08-06: strangers on omni only; VO wisp stays named">
      <b>show</b>
      <button type="button" data-f="all" class="on">all</button>
      <button type="button" data-f="people">people</button>
      <button type="button" data-f="strangers">strangers</button>
      <button type="button" data-f="media">media</button>
    </div>
    <div id="flags" title="sparse: offs + active signals only; empty when all quiet/ok"></div>
    <div id="log"></div>
    <div id="railfoot">
      <span id="s-ear">ear</span><span id="s-brain">brain</span>
      <span id="s-door">door</span><span id="s-mouth">mouth</span>
      <span id="s-insp">insp</span><span id="s-oracle">oracle</span>
    </div>
  </aside>
  <main id="stage">
    <div id="stagebar" title="Hybrid: click to stage. Voice-only: Crissa uses houseShow/CDP.">
      <b>stage</b>
      <button type="button" data-url="http://127.0.0.1:8190/">dashboard</button>
      <button type="button" data-url="http://127.0.0.1:8191/oracle">oracle</button>
      <button type="button" data-url="http://127.0.0.1:8191/">presence</button>
      <button type="button" id="stage-clear">clear</button>
      <span class="mode" id="surf-mode">hybrid</span>
    </div>
    <div id="stageframe"><div id="idle">stage idle · click or houseShow</div></div>
  </main>
</div>
<script>
const log = document.getElementById('log'), dot = document.getElementById('dot');
const flagsEl = document.getElementById('flags');
const MAX = 400;
const seenTurns = new Set();
let lastDoor = null, lastInspKey = null;
/** @type {Record<string, boolean|null>} last known on/off per flag element */
let lastFlagOn = {};

function esc(s){
  return String(s??'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function clock(tsLocal){
  if (tsLocal && tsLocal.length >= 16) return tsLocal.slice(11,16);
  const n = new Date();
  return String(n.getHours()).padStart(2,'0')+':'+String(n.getMinutes()).padStart(2,'0');
}
/** people | strangers | media | system — omni filter chips (the User stranger channel). */
let logFilter = 'all';
function applyLogFilter(){
  if (!log) return;
  log.querySelectorAll('.ev').forEach(el => {
    const tag = el.dataset.tag || 'system';
    let show = true;
    if (logFilter === 'people') show = (tag === 'people' || tag === 'system');
    else if (logFilter === 'strangers') show = (tag === 'strangers' || tag === 'system');
    else if (logFilter === 'media') show = (tag === 'media' || tag === 'system');
    else show = true; // all
    el.dataset.hide = show ? '0' : '1';
  });
}
document.getElementById('filt')?.addEventListener('click', (ev) => {
  const b = ev.target;
  if (!(b instanceof HTMLElement) || !b.dataset.f) return;
  logFilter = b.dataset.f;
  document.querySelectorAll('#filt button').forEach(x => x.classList.toggle('on', x.dataset.f === logFilter));
  try { localStorage.setItem('house.omniFilter', logFilter); } catch (_) {}
  applyLogFilter();
});
try {
  const saved = localStorage.getItem('house.omniFilter');
  if (saved && ['all','people','strangers','media'].includes(saved)) {
    logFilter = saved;
    document.querySelectorAll('#filt button').forEach(x => x.classList.toggle('on', x.dataset.f === logFilter));
  }
} catch (_) {}

function add(kind, msg, cls, tsLocal, tag){
  if (!log) return;
  const d = document.createElement('div');
  d.className = 'ev' + (cls ? ' '+cls : '');
  d.dataset.tag = tag || (kind === 'stt' ? 'people' : 'system');
  d.innerHTML = '<span class="t">'+esc(clock(tsLocal))+'</span><span class="m"><span class="k">'+
    esc(kind)+'</span> '+msg+'</span>';
  log.appendChild(d);
  while (log.children.length > MAX) log.removeChild(log.firstChild);
  applyLogFilter();
  log.scrollTop = log.scrollHeight;
}
window.houseLog = (kind, msg, cls) => add(kind, msg, cls||'');
function show(url){
  const frame = document.getElementById('stageframe');
  if (!frame) return;
  frame.innerHTML = '<iframe src="'+esc(url)+'" title="stage"></iframe>';
  add('stage', esc(url), '');
}
function clearStage(){
  const frame = document.getElementById('stageframe');
  if (!frame) return;
  frame.innerHTML = '<div id="idle">stage idle · click or houseShow</div>';
  add('stage', 'cleared', '');
}
window.houseShow = show;
window.houseClear = clearStage;
// Hybrid click bar → stage (same API Crissa uses via CDP evaluate houseShow)
document.getElementById('stagebar')?.addEventListener('click', (ev) => {
  const t = ev.target;
  if (!(t instanceof HTMLElement)) return;
  if (t.id === 'stage-clear') { clearStage(); return; }
  const u = t.getAttribute('data-url');
  if (u) show(u);
});
// Voice-only / float modes (query or localStorage)
try {
  const q = new URLSearchParams(location.search);
  const vo = q.get('voice') === '1' || localStorage.getItem('house.voiceOnly') === '1';
  if (vo) {
    document.body.classList.add('voice-only');
    const m = document.getElementById('surf-mode');
    if (m) m.textContent = 'voice-only';
  }
  if (q.get('float') === '1') {
    document.body.classList.add('float-mode');
    document.title = 'omni-log';
    // Never inject another float while we are already the rail content
    try { window.__houseOmniFloatInstalled = true; } catch (_) {}
  }
} catch (_) {}

const FLOAT = document.body.classList.contains('float-mode');
// Float rail: smaller pull + junk filter so open is not media/code/yeah spam
const OMNI = (location.port === '8190' || location.pathname.indexOf('omni')>=0)
  ? (FLOAT ? '/v1/omni?n=20' : '/v1/omni?n=40')
  : (FLOAT ? 'http://127.0.0.1:8190/v1/omni?n=20' : 'http://127.0.0.1:8190/v1/omni?n=40');
// After this much silence, fade/clear STT lines + drop memory ring so old
// mishears don't keep scrolling back (the User 2026-08-06). Disk jsonl untouched.
const _q = new URLSearchParams(location.search);
const IDLE_CLEAR_MS = Math.max(
  15000,
  parseInt(_q.get('idle_clear_ms') || (FLOAT ? '90000' : '180000'), 10) || 90000
);
let lastSpeechAt = Date.now();
let idleCleared = false;
let floorTs = 0; // ignore server turns older than this (after idle clear)
let clearRingInflight = false;

function turnKey(u){
  return (u.ts||'')+'|'+(u.text||'')+'|'+(u.person_id||'')+'|'+(u.via||'');
}

function clearSttDisplay(reason){
  if (!log) return;
  const kids = Array.from(log.querySelectorAll('.ev.stt'));
  for (const el of kids) el.remove();
  floorTs = Date.now() / 1000 - 1;
  idleCleared = true;
  add('house', 'log quiet — cleared STT ('+esc(reason||'silence')+')', '');
  // Soft-clear live ring so refresh/seed cannot resurrect ghosts.
  // Keep turns.jsonl for train.
  if (!clearRingInflight) {
    clearRingInflight = true;
    const url = (location.port === '8190' || location.pathname.indexOf('omni')>=0)
      ? '/v1/turns' : 'http://127.0.0.1:8190/v1/turns';
    fetch(url, {method:'DELETE', cache:'no-store'})
      .catch(()=>{})
      .finally(()=>{ clearRingInflight = false; });
  }
}

function maybeIdleClear(turns){
  const now = Date.now();
  // Any fresh (post-floor) speech bumps lastSpeechAt
  let newest = 0;
  for (const u of (turns||[])) {
    const ts = Number(u.ts) || 0;
    if (ts && ts > floorTs && (u.text||'').trim() && !isLogJunk(u)) {
      if (ts > newest) newest = ts;
    }
  }
  if (newest) {
    lastSpeechAt = Math.max(lastSpeechAt, newest * 1000);
    idleCleared = false;
  }
  if (!idleCleared && (now - lastSpeechAt) >= IDLE_CLEAR_MS) {
    clearSttDisplay(Math.round(IDLE_CLEAR_MS/1000)+'s silence');
  }
}

/** Media dumps, dead STT errors, filler — what "always starts with weird logs" is. */
function isLogJunk(u){
  const txt = (u.text || '').trim();
  const st = u.stamp || {};
  const src = String(st.source || '');
  // media kept as tag=media (not junk) so filter chip can show them
  if (src.startsWith('media_hold') || src === 'media_gate') return false;
  if (u.error && /live-ear unreachable|parakeet unreachable|connection refused|CUDA/i.test(String(u.error))) return true;
  const tok = txt.toLowerCase().replace(/[^a-z]+/g, '');
  if (txt.length > 0 && txt.length < 14 &&
      /^(yeah|yes|yep|yup|ok|okay|mm|mmm|hmm|uh|um|uhhuh|bye|right|sure|k|kay|mhm|mhmm)+$/.test(tok))
    return true;
  const pid = u.person_id || st.person_id;
  if (!pid && txt.length > 40 &&
      /transformer|tokenizer|dependency|version|package|kernel|mitigation|changelog|numpy|import /i.test(txt))
    return true;
  return false;
}
function sttFilterTag(u){
  const st = u.stamp || {};
  const src = String(st.source || '');
  const via = String(u.via || '');
  if (via === 'argus' || src === 'argus_describe') return 'system';
  if (src.startsWith('media_hold') || src === 'media_gate' || src.startsWith('media')) return 'media';
  const pid = String(u.person_id || st.person_id || '').toLowerCase().trim();
  if (!pid || pid === 'unknown' || pid === 'guest' || pid === 'you' || /^u\d/.test(pid) || pid.startsWith('__u'))
    return 'strangers';
  // named regulars / guests with real ids
  return 'people';
}

function renderIdBar(om){
  const turns = om.turns||[];
  const last = turns[0] || null; // newest first in ring
  const st = (last && last.stamp) || {};
  const pid = (last && last.person_id) || st.person_id || '—';
  document.getElementById('stt-id').textContent = pid;
  document.getElementById('stt-src').textContent = st.source
    ? ('· '+st.source+(st.confidence!=null ? ' '+Number(st.confidence).toFixed(2) : ''))
    : '';
  const ins = om.inspector||{};
  const ip = ins.person_id || '—';
  const lvl = ins.stamp_level || (ins.ok===false ? 'down' : '—');
  document.getElementById('insp-id').textContent = ip;
  const el = document.getElementById('insp-lvl');
  el.textContent = lvl + (ins.best_sim!=null ? ' sim '+Number(ins.best_sim).toFixed(2) : '');
  el.className = 'lvl ' + (lvl==='certain'||lvl==='confirmed' ? '' :
    (lvl==='provisional'||lvl==='uncertain' ? 'provisional' : 'none'));
}

let seededNote = false;
function ingestTurns(turns){
  // ring is newest-first; walk oldest→newest for log order of new ones
  let list = (turns||[]).slice().reverse();
  // First paint on float: only last ~10 clean lines (not 40 media dumps)
  if (FLOAT && !seededNote) {
    list = list.filter(u => !isLogJunk(u)).slice(-10);
  }
  let added = 0;
  for (const u of list){
    const k = turnKey(u);
    if (seenTurns.has(k)) continue;
    // After idle clear / silence: ignore pre-floor ghosts from ring or seed
    const ts = Number(u.ts) || 0;
    if (floorTs && ts && ts < floorTs) {
      seenTurns.add(k);
      continue;
    }
    seenTurns.add(k);
    if (seenTurns.size > 500) {
      const it = seenTurns.values(); seenTurns.delete(it.next().value);
    }
    // skip pure empty silence rows cluttering the rail
    if (!(u.text || '').trim() && !u.error) continue;
    if (isLogJunk(u)) continue;
    const pid = u.person_id || (u.stamp&&u.stamp.person_id);
    const src = (u.stamp&&u.stamp.source) || '';
    const conf = (u.stamp&&u.stamp.confidence!=null) ? Number(u.stamp.confidence).toFixed(2) : '';
    const tag = sttFilterTag(u);
    const who = pid
      ? '<span class="pid">'+esc(pid)+'</span>'
      : '<span class="src">'+(tag==='strangers'?'stranger':'unknown')+'</span>';
    const text = u.error
      ? '<span style="color:var(--bad)">'+esc(u.error)+'</span>'
      : esc((u.text||'(silence)').slice(0,160));
    const meta = [u.via, src, conf].filter(Boolean).join(' · ');
    const cls = (u.error?'err stt':'stt') + (tag==='media'?' media':'') + (tag==='strangers'?' stranger':'');
    add('stt', who+' '+text+(meta?' <span class="src">'+esc(meta)+'</span>':''),
        cls, u.ts_local, tag);
    lastSpeechAt = Date.now();
    idleCleared = false;
    added++;
  }
  if (!seededNote && (turns||[]).length) {
    seededNote = true;
    if (added === 0) {
      // all keys already seen — fine
    }
  }
}

function ingestInspector(ins){
  if (!ins || ins.ok===false && !ins.person_id) {
    document.getElementById('s-insp').classList.remove('up');
    return;
  }
  document.getElementById('s-insp').classList.add('up');
  const key = [ins.person_id, ins.stamp_level, ins.best_sim, (ins.reason_codes||[]).join(',')].join('|');
  if (key === lastInspKey) return;
  // only log when person/level changes meaningfully
  const prev = lastInspKey;
  lastInspKey = key;
  if (!ins.person_id && !prev) return;
  const pid = ins.person_id || '—';
  const bits = [
    '<span class="pid">'+esc(pid)+'</span>',
    esc(ins.stamp_level||''),
    ins.best_sim!=null ? 'sim '+Number(ins.best_sim).toFixed(2) : '',
    ins.shadow ? 'shadow' : '',
    ins.store_allowed===false ? 'no-store' : '',
  ].filter(Boolean).join(' · ');
  add('insp', bits, 'insp');
}

/**
 * Sparse flags:
 *  - service OFF → show red chip + log flip
 *  - signal ON (oracle) → show warn chip + log flip
 *  - all services on + no signals → strip empty (quiet, "just like this")
 *  - any combo of offs + signals ok
 * Never throws — STT ingest must keep running.
 */
function renderFlags(flags){
  try {
    if (!flagsEl) return;
    if (!flags || !flags.elements) {
      flagsEl.innerHTML = '';
      return;
    }
    const els = flags.elements;
    const off = flags.off || [];
    const signalsOn = flags.signals_on || [];
    // strip: only offs + active signals (not a wall of green)
    const chips = [];
    for (let i = 0; i < off.length; i++) {
      const name = off[i];
      const d = (els[name] && els[name].detail) ? ' '+els[name].detail : '';
      chips.push('<span class="f off" title="service off">'+esc(name)+' off'+esc(d)+'</span>');
    }
    for (let i = 0; i < signalsOn.length; i++) {
      const name = signalsOn[i];
      const d = (els[name] && els[name].detail) ? ' · '+els[name].detail : '';
      chips.push('<span class="f signal on" title="active signal">'+esc(name)+' on'+esc(d)+'</span>');
    }
    flagsEl.innerHTML = chips.join('');

    // footer dots: services up/warn; oracle up only when signaling
    const footMap = {
      ear: 's-ear', door: 's-door', brain: 's-brain',
      mouth: 's-mouth', insp: 's-insp', oracle: 's-oracle',
    };
    for (const name in els) {
      if (!Object.prototype.hasOwnProperty.call(els, name)) continue;
      const el = els[name] || {};
      const id = footMap[name];
      const on = !!el.on;
      if (id) {
        const node = document.getElementById(id);
        if (node) {
          if (on) { node.classList.add('up'); node.classList.remove('warn'); }
          else {
            node.classList.remove('up');
            if (name !== 'oracle') node.classList.add('warn');
            else node.classList.remove('warn');
          }
          if (name === 'ear') node.textContent = on ? 'ear open' : 'ear closed';
          else if (name === 'oracle') node.textContent = on ? ('oracle '+(el.detail||'on')) : 'oracle';
          else node.textContent = name;
        }
      }

      // log only on flip (not every poll)
      const prev = lastFlagOn[name];
      if (prev === undefined) {
        lastFlagOn[name] = on;
        // first sample: log offs + active signals only (no spam of all-on)
        if (el.kind === 'service' && !on) {
          add('flag', esc(name)+' <b>off</b>'+(el.detail?' · '+esc(el.detail):''), 'flag off');
        } else if (el.kind === 'signal' && on) {
          add('flag', esc(name)+' <b>on</b>'+(el.detail?' · '+esc(el.detail):''), 'flag signal');
        }
        continue;
      }
      if (prev === on) continue;
      lastFlagOn[name] = on;
      if (el.kind === 'service') {
        add('flag', esc(name)+' <b>'+(on?'on':'off')+'</b>'+(el.detail && !on ? ' · '+esc(el.detail):''),
            on ? 'flag' : 'flag off');
      } else {
        add('flag', esc(name)+' <b>'+(on?'on':'off')+'</b>'+(el.detail && on ? ' · '+esc(el.detail):''),
            on ? 'flag signal' : 'flag');
      }
    }
  } catch (err) {
    try { add('house', 'flags render error: '+esc(err && err.message || err), 'err'); } catch (_) {}
  }
}

let lastEarOpen = null;
let pollErrLogged = false;
let seededFromRing = false;
async function poll(){
  try {
    const r = await fetch(OMNI, {cache:'no-store'});
    if (!r.ok) throw new Error('omni HTTP '+r.status);
    const om = await r.json();
    pollErrLogged = false;

    // STT first — never blocked by flags/idbar
    const turns = om.turns || [];
    // If ring has speech but log never got STT lines (prior crash), re-seed once
    // Skip re-seed after an idle clear — that would resurrect ghosts.
    if (!seededFromRing && turns.length && !idleCleared) {
      const sttN = log ? log.querySelectorAll('.ev.stt').length : 0;
      if (sttN === 0) {
        for (let i = 0; i < turns.length; i++) {
          try { seenTurns.delete(turnKey(turns[i])); } catch (_) {}
        }
      }
      seededFromRing = true;
    }
    maybeIdleClear(turns);
    ingestTurns(turns);

    const p = om.presence || {};
    const names = (p.people||[]).map(x => x.name||'unknown');
    const presEl = document.getElementById('presence');
    if (presEl) {
      presEl.textContent =
        'hc '+(p.headcount??'—')+' · '+(names.join(', ')||'empty')+' · '+(p.count_trust||'');
    }
    if (p.headcount!=null) dot.classList.add('live'); else dot.classList.remove('live');
    if (p.last_event_kind && p.last_event_kind !== lastDoor) {
      lastDoor = p.last_event_kind;
      add('door', esc(p.last_event_kind), 'door');
    }
    try { renderIdBar(om); } catch (_) {}
    renderFlags(om.flags);
    try { ingestInspector(om.inspector||{}); } catch (_) {}

    const ear = om.ear || {};
    if (lastEarOpen === null) {
      lastEarOpen = !!ear.open;
      if (!ear.open) add('house', 'ear closed — open continuous ear or Listen to feed STT lines', '');
      else add('house', 'ear open — speech will stamp here', '');
    } else if (lastEarOpen !== !!ear.open) {
      lastEarOpen = !!ear.open;
      add('house', ear.open ? 'ear opened' : ('ear closed'+(ear.closed_reason?': '+ear.closed_reason:'')), '');
    }
    // Do NOT also log ear.last_text as STT — ingestTurns already prints the same
    // utterance (was printing the User twice: once turns, once ear.last_*).
    const earEl = document.getElementById('s-ear');
    if (earEl) {
      earEl.title =
        'turns='+((om.counts&&om.counts.turns)||0)+' ring='+((om.counts&&om.counts.ring_len)||0);
    }
  } catch (e) {
    dot.classList.remove('live');
    const earEl = document.getElementById('s-ear');
    if (earEl) earEl.classList.remove('up');
    if (!pollErrLogged) {
      pollErrLogged = true;
      add('house', 'omni feed error: '+esc(e && e.message || e), 'err');
    }
    renderFlags({
      elements: {
        ear:{on:false,kind:'service',detail:'feed'},
        presence:{on:false,kind:'service'},
        door:{on:false,kind:'service'},
        brain:{on:false,kind:'service'},
        mouth:{on:false,kind:'service'},
        insp:{on:false,kind:'service'},
        oracle:{on:false,kind:'signal'},
      },
      off:['ear','presence','door','brain','mouth','insp'],
      signals_on: [],
      quiet: false,
    });
  }
}
setInterval(poll, 1500);
add('house', 'omni-log · stt person_id + flags (off + signals)', '');
poll();
</script>
</body>
</html>
"""
