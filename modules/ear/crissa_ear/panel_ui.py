"""House Control Panel — hybrid shell (House | Work) + Voice master.

Tokens: static/house-ui.css (the panel's own sheet).
APIs: GET /panel/state · POST /panel/voice (local only).
"""

from __future__ import annotations

PANEL_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>House · Control Panel</title>
<link rel="stylesheet" href="/panel/house-ui.css"/>
<style>
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 var(--font);
       min-height:100vh;display:flex;flex-direction:column}
  #liveband{display:none;background:var(--live);color:#fff;text-align:center;
            font-weight:700;letter-spacing:.3em;text-transform:uppercase;
            padding:.55rem;font-size:.85rem}
  body.live #liveband{display:block;animation:livepulse 2s ease infinite}
  @keyframes livepulse{0%,100%{opacity:1}50%{opacity:.75}}
  @media (prefers-reduced-motion:reduce){body.live #liveband{animation:none}}
  #offband{display:none;background:var(--elev);color:var(--muted);text-align:center;
           padding:.5rem;font-size:.8rem;border-bottom:1px solid var(--line)}
  body.offline #offband{display:block}
  body.offline .house-pane{opacity:.55}
  header{display:flex;justify-content:space-between;align-items:center;
         padding:1rem 1.5rem .5rem;flex-wrap:wrap;gap:.75rem}
  .brand .mark{letter-spacing:.32em;text-transform:uppercase;font-size:.68rem;color:var(--muted)}
  .brand h1{font-size:1rem;font-weight:500;margin:.15rem 0 0}
  .tabs{display:flex;gap:.25rem;background:var(--elev);border:1px solid var(--line);
        border-radius:10px;padding:.2rem}
  .tab{appearance:none;border:0;background:transparent;color:var(--muted);font:inherit;
       font-size:.8rem;letter-spacing:.1em;text-transform:uppercase;padding:.45rem .9rem;
       border-radius:8px;cursor:pointer;min-height:40px}
  .tab.cur{color:var(--fg);background:var(--bg);border:1px solid var(--line)}
  .tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  main{flex:1;display:flex;flex-direction:column;min-height:0}
  .pane{display:none;flex:1;flex-direction:column;min-height:0}
  .pane.cur{display:flex}
  .house-inner{flex:1;display:grid;gap:1rem;padding:0 1.5rem 1.25rem;
               grid-template-columns:repeat(auto-fit,minmax(280px,1fr));align-content:start;
               max-width:1100px;width:100%;margin:0 auto}
  .zone{background:var(--elev);border:1px solid var(--line);border-radius:12px;
        padding:1.1rem 1.2rem;display:flex;flex-direction:column;gap:.75rem}
  .zone h2{margin:0;font-size:.72rem;letter-spacing:.13em;text-transform:uppercase;
           color:var(--muted);font-weight:600}
  .voicezone{grid-column:1/-1;flex-direction:row;align-items:center;
             justify-content:space-between;flex-wrap:wrap;gap:1rem}
  .voicezone .txt b{font-size:1.15rem;font-weight:650;display:block}
  .voicezone .txt span{color:var(--muted);font-size:.85rem}
  .switch{--w:76px;width:var(--w);height:44px;border-radius:999px;border:1px solid var(--line);
          background:#0e1015;position:relative;cursor:pointer;flex:none}
  .switch::after{content:"";position:absolute;top:4px;left:4px;width:34px;height:34px;
                 border-radius:50%;background:var(--muted);transition:left .2s,background .2s}
  body.live .switch{border-color:var(--live)}
  body.live .switch::after{left:calc(var(--w) - 38px);background:var(--live)}
  .switch:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  body.locked .switch{opacity:.45;cursor:not-allowed}
  .locknote{display:none;color:var(--accent);font-size:.78rem}
  body.locked .locknote{display:block}
  .row{display:flex;align-items:center;gap:.7rem;font-size:.9rem}
  .row .k{color:var(--muted);flex:1}
  .row .v{font-family:var(--mono);font-size:.78rem}
  .row .v.ok{color:var(--ok)}
  .row .v.warn{color:var(--accent)}
  button.act{background:#0e1015;border:1px solid var(--line);color:var(--fg);border-radius:8px;
             padding:.5rem .8rem;font:inherit;font-size:.8rem;cursor:pointer;min-height:44px}
  button.act:hover,button.act:focus-visible{border-color:var(--accent);outline:none}
  button.act.hot{border-color:var(--accent);color:var(--accent)}
  .cmd{font-family:var(--mono);font-size:.72rem;background:#0e1015;border:1px solid var(--line);
       border-radius:6px;padding:.45rem .6rem;color:var(--muted);overflow-x:auto;white-space:nowrap}
  .dialrow{display:flex;gap:1rem;align-items:center}
  .dial{position:relative;width:5.5rem;height:5.5rem;border-radius:50%;
        border:1px solid var(--line);background:var(--bg);flex:none}
  .dial i{position:absolute;left:50%;top:50%;width:1px;height:38%;
          background:var(--line);transform-origin:bottom center;margin-left:-.5px;margin-top:-38%}
  .dial .cam{position:absolute;left:50%;top:50%;width:8px;height:8px;margin:-4px 0 0 -4px;
             border-radius:50%;background:var(--accent);box-shadow:0 0 9px rgba(232,163,61,.55)}
  .dial .lab{position:absolute;font-size:.55rem;color:var(--muted);font-family:var(--mono);
             letter-spacing:.04em;text-transform:uppercase}
  .dial .door{left:50%;top:3px;transform:translateX(-50%);color:var(--accent)}
  .dial .b8{left:8%;bottom:18%}
  .dial .m10{right:6%;bottom:28%}
  .dial .wall{left:50%;bottom:4px;transform:translateX(-50%);opacity:.55}
  /* media flag: one tiny × on media hour (default 9) — red idle / green media */
  .dial .m9{left:2%;top:42%;font-size:.62rem;font-weight:800;letter-spacing:0;
            font-family:var(--mono);line-height:1;
            color:#e85d5d;opacity:.85;transition:color .15s,text-shadow .15s,opacity .15s}
  .dial .m9.on{color:#3dd68c;opacity:1;
               text-shadow:0 0 6px rgba(61,214,140,.85)}
  .dial .m9.hidden{display:none}
  .dial .beam{display:none}
  .dialtxt{font-size:.85rem;color:var(--muted);line-height:1.4}
  .dialtxt b{color:var(--fg);display:block;margin-bottom:.2rem;font-weight:600}
  footer{display:flex;gap:1rem;flex-wrap:wrap;justify-content:center;align-items:center;
         padding:.8rem 1.5rem 1.1rem;border-top:1px solid var(--line);
         color:var(--muted);font-size:.72rem}
  .lane{display:flex;align-items:center;gap:.35rem;font-family:var(--mono)}
  .lane i{width:7px;height:7px;border-radius:50%;background:var(--muted);display:inline-block}
  .lane.ok i{background:var(--ok)}
  .lane.warn i{background:var(--accent)}
  #workframe,#omniframe{flex:1;border:0;width:100%;min-height:70vh;background:#0a0c10}
  .work-bar{display:flex;gap:.75rem;align-items:center;padding:.5rem 1.5rem;
            border-bottom:1px solid var(--line);font-size:.8rem;color:var(--muted);flex-wrap:wrap}
  .work-bar a{color:var(--accent-soft)}
  #clock{font-family:var(--mono);font-size:.72rem;color:var(--muted)}
  .dispzone{grid-column:1/-1}
</style>
</head>
<body data-brand="house-panel" data-product="control-panel">
  <div id="liveband">Voice Live</div>
  <div id="offband">House rails unreachable — panel is read-only</div>
  <header>
    <div class="brand">
      <div class="mark">House</div>
      <h1>Control Panel</h1>
    </div>
    <div class="tabs" role="tablist">
      <button type="button" class="tab cur" data-tab="house" role="tab" aria-selected="true">House</button>
      <button type="button" class="tab" data-tab="omni" role="tab" aria-selected="false">Omni</button>
      <button type="button" class="tab" data-tab="work" role="tab" aria-selected="false">Work</button>
    </div>
    <div id="clock">—</div>
  </header>
  <main>
    <div class="pane cur" id="pane-house" role="tabpanel">
      <div class="house-inner">
        <div class="zone voicezone">
          <div class="txt">
            <b>Voice</b>
            <span id="voicesub">Off — Crissa hears, never speaks aloud</span>
            <span class="locknote">🔒 Flip from the desk only</span>
            <span style="display:block;font-size:.72rem;color:var(--muted);margin-top:.25rem">
              Separate from Hybrid. Hybrid never turns Voice off. Voice-off cannot stay Voice-only.
            </span>
          </div>
          <div class="switch" id="voiceswitch" role="switch" aria-checked="false"
               tabindex="0" data-requires="local-user" title="Voice master"></div>
        </div>

        <div class="zone voicezone dispzone">
          <div class="txt">
            <b>Display Brave</b>
            <span id="dispsub">Off — Crissa projector (separate profile, not your personal Brave)</span>
          </div>
          <div class="switch" id="dispswitch" role="switch" aria-checked="false"
               tabindex="0" title="Crissa display surface"></div>
        </div>

        <div class="zone">
          <h2>Room</h2>
          <div class="dialrow">
            <div class="dial" id="roomdial" aria-hidden="true"
                 title="Room clock — seats + media beam at 9 (DD desk)">
              <i style="transform:rotate(0deg)"></i><i style="transform:rotate(30deg)"></i>
              <i style="transform:rotate(60deg)"></i><i style="transform:rotate(90deg)"></i>
              <i style="transform:rotate(120deg)"></i><i style="transform:rotate(150deg)"></i>
              <i style="transform:rotate(180deg)"></i><i style="transform:rotate(210deg)"></i>
              <i style="transform:rotate(240deg)"></i><i style="transform:rotate(270deg)"></i>
              <i style="transform:rotate(300deg)"></i><i style="transform:rotate(330deg)"></i>
              <span class="cam"></span>
              <span class="lab door">12</span>
              <span class="lab b8">8</span>
              <span class="lab m10">10</span>
              <span class="lab m9" id="mediax" title="media × · red idle · green on">×</span>
              <span class="lab wall">4–7 wall</span>
            </div>
            <div class="dialtxt" id="dialtxt"><b>Room</b>loading…</div>
          </div>
        </div>

        <div class="zone">
          <h2>System</h2>
          <div class="row"><span class="k">Ear</span><span class="v" id="earv">—</span></div>
          <div class="row"><span class="k">Presence</span><span class="v" id="presv">—</span></div>
          <div class="row"><span class="k">Camera</span><span class="v" id="camv">door (rest)</span>
            <button type="button" class="act" id="btn-cam-home">Home to door</button></div>
        </div>

        <div class="zone">
          <h2>Oracle · prod feed</h2>
          <div class="row"><span class="k">Ops 35B</span><span class="v" id="opsv">—</span></div>
          <div class="row"><span class="k">Last CODE event</span><span class="v" id="evtv">—</span></div>
          <div class="cmd" id="feedline">Judgment owner = ops · shadows retired</div>
          <div class="row">
            <span class="k">Door feed</span>
            <button type="button" class="act" id="btn-oracle"
                    title="SoT + ops 35B door feed">Open</button>
          </div>
          <div class="row">
            <span class="k">Room rescan</span>
            <button type="button" class="act" id="btn-rescan"
                    title="Eval-driven error correct · seat pan only if needed">Rescan</button>
            <button type="button" class="act hot" id="btn-reinit"
                    title="Desk moved — pan + empty-room refs when possible">Desk moved</button>
          </div>
          <div class="row">
            <span class="k">Visitor Center</span>
            <button type="button" class="act hot" id="btn-visitor-enroll"
                    title="One name → one id · face then voice">EnrollID</button>
          </div>
          <div class="cmd" id="cmd">Visitor Center: EnrollID — type the name once on the coach</div>
        </div>

        <div class="zone">
          <h2>Identity</h2>
          <div id="banks"></div>
        </div>
      </div>
    </div>

    <div class="pane" id="pane-omni" role="tabpanel">
      <div class="work-bar">
        <span>Omni-log + stage (dashboard · oracle · presence)</span>
        <a href="/omni" target="_blank" rel="noopener">open outside</a>
        <a href="/omni?float=1" target="_blank" rel="noopener">log-only float</a>
      </div>
      <iframe id="omniframe" title="Omni log" src="about:blank"></iframe>
    </div>

    <div class="pane" id="pane-work" role="tabpanel">
      <div class="work-bar">
        <span>Work stage · the board</span>
        <a id="worklink" href="" target="_blank" rel="noopener">open outside</a>
      </div>
      <iframe id="workframe" title="Work" src="about:blank"></iframe>
    </div>
  </main>
  <footer id="lanes"></footer>
<script>
const sw = document.getElementById('voiceswitch');
const sub = document.getElementById('voicesub');
const dsw = document.getElementById('dispswitch');
const dsub = document.getElementById('dispsub');
let workLoaded = false;
let omniLoaded = false;
let workUrl = '';   // filled from /panel/state work_url (RAIL_STT_WORK_URL), "" = no work stage
let coachUrl = 'http://127.0.0.1:8195/';
// Full omni: left log + stage bar (dashboard/oracle/presence).
// float=1 is log-only (used inside Crissa shell rail — hides stage).
const omniUrl = '/omni';

function setTab(name) {
  document.querySelectorAll('.tab').forEach(t => {
    const on = t.dataset.tab === name;
    t.classList.toggle('cur', on);
    t.setAttribute('aria-selected', String(on));
  });
  document.querySelectorAll('.pane').forEach(p => {
    p.classList.toggle('cur', p.id === 'pane-' + name);
  });
  if (name === 'work' && !workLoaded) {
    document.getElementById('workframe').src = workUrl;
    workLoaded = true;
  }
  if (name === 'omni' && !omniLoaded) {
    document.getElementById('omniframe').src = omniUrl;
    omniLoaded = true;
  }
  try { localStorage.setItem('house.panel.tab', name); } catch (_) {}
}
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => setTab(t.dataset.tab));
});

function paintVoice(on) {
  document.body.classList.toggle('live', !!on);
  sw.setAttribute('aria-checked', String(!!on));
  sub.textContent = on ? 'ON — spoken replies enabled' : 'Off — Crissa hears, never speaks aloud';
}

function paintDisplay(on) {
  dsw.setAttribute('aria-checked', String(!!on));
  dsw.style.borderColor = on ? 'var(--ok)' : '';
  dsub.textContent = on
    ? 'ON — her display browser (:9222, its own profile under the state home)'
    : 'Off — your own browser is free; her display browser is not running';
}

async function postVoice(on) {
  const r = await fetch('/panel/voice', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({on: !!on, on_by: 'user'}),
  });
  if (r.status === 403) {
    document.body.classList.add('locked');
    throw new Error('locked');
  }
  if (!r.ok) throw new Error('voice ' + r.status);
  return r.json();
}

async function postDisplay(on) {
  const r = await fetch('/panel/display', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({on: !!on}),
  });
  if (r.status === 403) throw new Error('locked');
  if (!r.ok) throw new Error('display ' + r.status);
  return r.json();
}

async function flip() {
  if (document.body.classList.contains('locked')) return;
  const on = !document.body.classList.contains('live');
  try {
    const s = await postVoice(on);
    paintVoice(s.voice === 'on' || s.voice_on);
  } catch (e) {
    if (String(e.message) === 'locked') return;
    console.warn(e);
  }
}
async function flipDisplay() {
  const on = dsw.getAttribute('aria-checked') !== 'true';
  dsub.textContent = on ? 'Starting projector…' : 'Stopping projector…';
  try {
    const s = await postDisplay(on);
    paintDisplay(!!s.on);
  } catch (e) {
    dsub.textContent = 'Display toggle failed — ' + (e && e.message || e);
    console.warn(e);
  }
}
sw.addEventListener('click', flip);
sw.addEventListener('keydown', e => {
  if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); flip(); }
});
dsw.addEventListener('click', flipDisplay);
dsw.addEventListener('keydown', e => {
  if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); flipDisplay(); }
});

function paintBanks(banks) {
  const el = document.getElementById('banks');
  el.innerHTML = '';
  const ids = Object.keys(banks || {}).sort();
  if (!ids.length) {
    el.innerHTML = '<div class="row"><span class="k">no bank</span></div>';
    return;
  }
  ids.forEach(id => {
    const b = banks[id] || {};
    const row = document.createElement('div');
    row.className = 'row';
    const face = b.face ? 'face ✓' : 'face —';
    const vs = 'voice ' + (b.voice_slots ?? 0);
    const due = (b.voice_slots || 0) < 2;
    row.innerHTML =
      '<span class="k">' + id + '</span>' +
      '<span class="v ' + (due ? 'warn' : 'ok') + '">' + face + ' · ' + vs + '</span>';
    // Re-enroll known id — one click, no second name prompt
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'act' + (due ? ' hot' : '');
    btn.textContent = 'Re-enroll';
    btn.title = 'Re-enroll ' + id + ' (id already known)';
    btn.addEventListener('click', async () => {
      const msg = document.getElementById('cmd');
      msg.textContent = 'Re-enroll ' + id + '…';
      // Open coach with known id — no second name prompt
      window.open(
        coachUrl.replace(/\/?$/, '') + '/?id=' + encodeURIComponent(id) + '&autostart=1',
        '_blank',
        'noopener'
      );
      msg.textContent = 'Re-enroll ' + id + ' · coach opening (id known)';
    });
    row.appendChild(btn);
    el.appendChild(row);
  });
}

function openVisitorCenter() {
  const msg = document.getElementById('cmd');
  msg.textContent = 'Visitor Center: EnrollID — type the name once, then Start';
  window.open(coachUrl, '_blank', 'noopener');
}
document.getElementById('btn-visitor-enroll').addEventListener('click', openVisitorCenter);
document.getElementById('btn-oracle').addEventListener('click', () => {
  window.open('http://127.0.0.1:8191/oracle', '_blank', 'noopener');
});

async function runReinit(mode) {
  const msg = document.getElementById('cmd');
  msg.textContent = mode === 'user_reinit'
    ? 'Desk reinit running (eval + optional refs)…'
    : 'Room rescan (flexible)…';
  try {
    const r = await fetch('http://127.0.0.1:8191/v1/reinit_scan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: mode || 'auto', inspect: true}),
    });
    const d = await r.json();
    if (!d.ok) {
      msg.textContent = 'Rescan failed: ' + (d.error || r.status);
      return;
    }
    const ev = d.eval || {};
    const seat = d.seat_scan || {};
    const insp = d.inspect || {};
    const pa = d.presence_after || {};
    msg.textContent =
      'Rescan ' + (d.wall_sec || '?') + 's · pan=' +
      (seat.ran ? ('yes ' + (seat.wall_sec || '') + 's') : 'skipped') +
      ' · reasons ' + ((ev.reasons || []).join(',') || '—') +
      ' · faces ' + (insp.n_faces ?? '—') +
      ' · stamp ' + (insp.stamp_level || '—') +
      ' · hc ' + (pa.headcount ?? '—') +
      ' ' + ((pa.people || []).join(',') || '');
  } catch (e) {
    msg.textContent = 'Rescan error: ' + (e && e.message || e);
  }
}
document.getElementById('btn-rescan').addEventListener('click', () => runReinit('auto'));
document.getElementById('btn-reinit').addEventListener('click', () => {
  if (!confirm('Desk moved / room reinit?\n\nWill run flexible scan and try empty-room reference capture for 35B poster filter.\nRoom should be empty for best refs (or force).')) return;
  runReinit('user_reinit');
});

function paintLanes(lanes) {
  const foot = document.getElementById('lanes');
  const order = ['ops', 'crew', 'cosy', 'announcer', 'ear'];
  foot.innerHTML = '';
  order.forEach(name => {
    const L = (lanes || {})[name] || {};
    const span = document.createElement('span');
    span.className = 'lane ' + (L.ok ? 'ok' : 'warn');
    span.innerHTML = '<i></i>' + name;
    foot.appendChild(span);
  });
}

function paintDial(st) {
  const p = st.presence || {};
  const names = (p.people || []).map(x => x.name).filter(Boolean);
  const hc = p.headcount ?? names.length;
  const trust = p.count_trust || '—';
  const media = st.media || {};
  const mode = media.mode || (media.internal_block ? 'internal' : 'external');
  const mediaOn = !!media.media_on;
  const hour = media.media_station || media.locus_station || 9;
  const mediaX = document.getElementById('mediax');
  if (mediaX) {
    const show = mode !== 'internal' && hour != null;
    mediaX.classList.toggle('hidden', !show);
    mediaX.classList.toggle('on', mediaOn && show);
    mediaX.textContent = '×';
    // place at hour on dial (simple: 9 left; others approximate via title only for v1)
    mediaX.title = !show
      ? 'media: internal (same computer) — no dial mark'
      : (mediaOn
          ? ('MEDIA ON · hour ' + hour + ' · green ×')
          : ('media hour ' + hour + ' · red × idle · not a sit seat'));
  }
  const seats = (st.dial && st.dial.occupants) || [];
  const seatBits = seats
    .filter(o => o.station_guess != null)
    .map(o => (o.name || '?') + '@' + o.station_guess);
  let mediaLine;
  if (mode === 'internal') {
    mediaLine = '<br/>media · internal (same PC block)' +
      (mediaOn ? ' · <span style="color:#3dd68c">ACTIVE</span>' : '');
  } else {
    mediaLine = mediaOn
      ? ('<br/><span style="color:#3dd68c">media ×' + hour + ' green</span>')
      : ('<br/><span style="color:#e85d5d">media ×' + hour + ' red</span>');
  }
  document.getElementById('dialtxt').innerHTML =
    '<b>Room · ' + hc + ' present</b>' +
    (names.length ? names.join(', ') : 'empty') +
    (seatBits.length ? (' · seats ' + seatBits.join(' ')) : '') +
    '<br/>camera: ' + (st.camera_aim || 'door') + ' · trust ' + trust +
    mediaLine;
}

async function tick() {
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) + ' · desk';
  try {
    const r = await fetch('/panel/state', {cache: 'no-store'});
    if (!r.ok) throw new Error('state');
    const st = await r.json();
    document.body.classList.remove('offline');
    paintVoice(!!st.voice_on);
    paintDisplay(!!(st.display && st.display.on));
    paintBanks(st.banks);
    paintLanes(st.lanes);
    paintDial(st);
    if (st.work_url) {
      workUrl = st.work_url;
      document.getElementById('worklink').href = workUrl;
    }
    if (st.coach_url) coachUrl = st.coach_url;
    const ear = st.ear || {};
    const earEl = document.getElementById('earv');
    earEl.textContent = (ear.engine || 'ear') + ' · ' + (ear.mode || '—') +
      (ear.open ? ' · open' : ' · closed');
    earEl.className = 'v ' + (ear.open ? 'ok' : '');
    const pr = st.presence || {};
    document.getElementById('presv').textContent =
      'hc ' + (pr.headcount ?? '—') + ' · ' + (pr.count_trust || '—');
    document.getElementById('camv').textContent = (st.camera_aim || 'door') + ' (rest)';
    // Ops 35B prod feed (not gemma/cosmos shadows)
    // Lanes.ops is a second vote — never show "down" if footer ops lane is green.
    const f = st.feed || {};
    const laneOps = (st.lanes || {}).ops || {};
    const opsUp = !!(f.ops_ok || laneOps.ok);
    const opsEl = document.getElementById('opsv');
    if (opsEl) {
      const shortRoot = (f.ops_root || '').split('/').filter(Boolean).pop() || '';
      opsEl.textContent = (opsUp ? 'up' : 'down') + ' · ' +
        (f.ops_model || 'ops') +
        (shortRoot ? (' · ' + shortRoot) : '') +
        (f.ops_latency_ms != null ? (' · ' + f.ops_latency_ms + 'ms') : '');
      opsEl.className = 'v ' + (opsUp ? 'ok' : 'warn');
      opsEl.title = f.ops_root || f.ops_summary || '';
    }
    const evtEl = document.getElementById('evtv');
    if (evtEl) {
      evtEl.textContent = (f.last_event_kind || '—') +
        (f.last_event_source ? (' · ' + f.last_event_source) : '');
    }
    const fl = document.getElementById('feedline');
    if (fl) {
      fl.textContent = f.ops_summary ||
        (opsUp
          ? 'Ops 35B judgment owner · CODE→SoT physics · shadows retired'
          : 'Ops down — SoT still from CODE face buffer');
    }
  } catch (e) {
    document.body.classList.add('offline');
  }
}

document.getElementById('btn-cam-home').addEventListener('click', async () => {
  try {
    await fetch('/panel/camera-home', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
  } catch (_) {}
});

try {
  const t = localStorage.getItem('house.panel.tab');
  if (t === 'work' || t === 'house' || t === 'omni') setTab(t);
} catch (_) {}
tick();
setInterval(tick, 2000);
</script>
</body>
</html>
"""
