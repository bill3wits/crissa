"""FaceID visor — live face ID HUD on the camera feed.

Not occupancy. FaceID reads the current FOV and paints a visor: name, sim,
bbox. F2F looks update it; POST /v1/faceid/scan IDs the current frame
without aiming.
"""

from __future__ import annotations

VISOR_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>FaceID · visor</title>
<style>
:root{--hud:#7dffc3;--hud2:#c8ff7a;--warn:#ffcc66;--bad:#ff6b7a;--dim:rgba(8,18,14,.55)}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:#020403;color:var(--hud);font:14px/1.35 ui-monospace,Menlo,Consolas,monospace;overflow:hidden}
#stage{position:fixed;inset:0;background:#000}
#stage img{width:100%;height:100%;object-fit:cover;background:#000;filter:contrast(1.05) saturate(.85)}
#hud{position:fixed;inset:0;pointer-events:none;
  background:radial-gradient(ellipse at center,transparent 42%,rgba(0,20,12,.45) 100%)}
.scan{position:absolute;left:8%;right:8%;height:1px;background:linear-gradient(90deg,transparent,var(--hud),transparent);opacity:.35;animation:sweep 3.6s linear infinite}
@keyframes sweep{from{top:10%}to{top:88%}}
.corner{position:absolute;width:48px;height:48px;border-color:var(--hud);opacity:.85}
.tl{top:18px;left:18px;border-top:2px solid;border-left:2px solid}
.tr{top:18px;right:18px;border-top:2px solid;border-right:2px solid}
.bl{bottom:18px;left:18px;border-bottom:2px solid;border-left:2px solid}
.br{bottom:18px;right:18px;border-bottom:2px solid;border-right:2px solid}
#mode{position:absolute;top:22px;left:50%;transform:translateX(-50%);letter-spacing:.42em;font-size:11px;text-transform:uppercase;text-shadow:0 0 12px var(--hud)}
#lock{position:absolute;top:22%;right:6%;width:min(280px,32vw);background:var(--dim);border:1px solid var(--hud);padding:12px 14px;pointer-events:auto}
#lock .k{font-size:10px;letter-spacing:.28em;opacity:.7}
#lock .name{font-size:28px;font-weight:700;margin:4px 0 8px;text-transform:uppercase;text-shadow:0 0 16px var(--hud)}
#lock .name.gold,#stack b.gold{color:var(--warn);text-shadow:0 0 16px var(--warn)}
#lock .name.green,#stack b.green{color:var(--hud);text-shadow:0 0 16px var(--hud)}
#lock .bar{height:4px;margin-top:6px;background:rgba(125,255,195,.12)}
#lock .bar i{display:block;height:100%;width:0;background:var(--bad);transition:width .3s}
#lock .bar i.green{background:var(--hud)}
#stack b.bad{color:var(--bad);text-shadow:0 0 16px var(--bad)}
#lock .meta{margin-top:8px;font-size:11px;opacity:.85}
#stack{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px 10px;margin-top:10px;font-size:11px;letter-spacing:.12em}
#stack b{display:block;font-size:16px;letter-spacing:.04em}
#faces{position:absolute;bottom:22px;left:22px;right:22px;display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;pointer-events:auto}
.chip{background:var(--dim);border:1px solid var(--hud);padding:8px 10px;min-width:120px}
.chip b{display:block;font-size:14px;letter-spacing:.08em}
.chip span{font-size:11px;opacity:.8}
#crop{position:absolute;bottom:22px;right:22px;width:120px;height:120px;border:1px solid var(--hud);object-fit:cover;background:#000;display:none;pointer-events:none}
#boxes{position:absolute;inset:0;pointer-events:none}
.box{position:absolute;border:1px solid var(--hud2);box-shadow:0 0 12px rgba(125,255,195,.25)}
.box label{position:absolute;left:-1px;top:-16px;font-size:11px;letter-spacing:.12em;background:#020403;padding:0 6px}
a.back{position:absolute;top:22px;left:78px;color:var(--hud);text-decoration:none;letter-spacing:.2em;font-size:11px;pointer-events:auto;opacity:.7}
body.f2f #mode{color:var(--hud2)}
</style>
</head>
<body>
<div id="stage"><img id="live" alt="fov" src="/v1/camera/mjpeg?scale=0.6&hz=12"/></div>
<div id="hud">
  <div class="scan"></div>
  <div class="corner tl"></div><div class="corner tr"></div>
  <div class="corner bl"></div><div class="corner br"></div>
  <div id="mode">FACEID</div>
  <a class="back" href="/">SO T</a>
  <div id="boxes"></div>
  <div id="lock">
    <div class="k">LIVE FACE ID</div>
    <div class="name" id="who">None</div>
    <div class="bar"><i id="simbar"></i></div>
    <div class="meta" id="meta"></div>
    <div id="stack">
      <div>BEAM<b id="sp_beam" class="gold">—</b></div>
      <div>CAMERA<b id="sp_cam" class="bad">00%</b></div>
      <div>VOICE<b id="sp_voice" class="gold">00%</b></div>
      <div>SEX<b id="sp_sex" class="gold">00%</b></div>
      <div>STACK<b id="sp_stack" class="gold">00%</b></div>
    </div>
  </div>
  <div id="faces"></div>
  <img id="crop" alt="crop"/>
</div>
<script>
const live = document.getElementById('live');
function esc(s){return String(s??'').replace(/[&<>]/g,c=>({ '&':'&amp;','<':'&lt;','>':'&gt;' }[c]))}
function modeLine(p,b){
  if(p.f2f) return 'F2F';
  return 'SCENERY';
}
function draw(p,b){
  document.body.classList.toggle('f2f', !!p.f2f);
  document.getElementById('mode').textContent = 'FACEID · ' + modeLine(p,b);
  const sp = p.speaker || b.speaker || {};
  const lock = sp.person_id || null;
  const whoEl = document.getElementById('who');
  const who = lock || 'None';
  if(whoEl.textContent !== who) whoEl.textContent = who;
  whoEl.classList.toggle('gold', !!lock && !!sp.name_gold && !sp.voice_green);
  whoEl.classList.toggle('green', !!lock && !!sp.voice_green);
  const pct = (x) => {
    let n = Math.round(Number(x==null?0:x)*100);
    if(!(n>=0)) n=0;
    return (n<10?'0':'')+n+'%';
  };
  function cell(id, v, green){
    const el = document.getElementById(id);
    if(!el) return;
    const t = pct(v);
    if(el.textContent !== t) el.textContent = t;
    el.classList.toggle('gold', !green);
    el.classList.toggle('green', !!green);
  }
  function clockCell(id, v, green){
    const el = document.getElementById(id);
    if(!el) return;
    const n = Number(v==null?0:v);
    const t = (n>=1) ? n.toFixed(1) : '—';
    if(el.textContent !== t) el.textContent = t;
    el.classList.toggle('gold', !green);
    el.classList.toggle('green', !!green);
  }
  clockCell('sp_beam', sp.beam, sp.beam_green);
  // CAMERA is the real face similarity (speaker.face) as a percentage, green at the 0.60 bar and red
  // below (the User 2026-10-01; it was the 0/100 pass/fail). The lock panel's bar shows the same number.
  const fsim = (sp.face==null || !isFinite(Number(sp.face))) ? null : Number(sp.face);
  const fpass = fsim!=null && fsim >= 0.60;
  const camEl = document.getElementById('sp_cam');
  if(camEl){
    const t = pct(fsim==null ? 0 : Math.max(0, Math.min(1, fsim)));
    if(camEl.textContent !== t) camEl.textContent = t;
    camEl.classList.toggle('green', fpass);
    camEl.classList.toggle('bad', !fpass);
    camEl.classList.remove('gold');
  }
  const bar = document.getElementById('simbar');
  if(bar){
    const w = (fsim==null ? 0 : Math.max(0, Math.min(1, fsim))*100).toFixed(1) + '%';
    if(bar.style.width !== w) bar.style.width = w;
    bar.classList.toggle('green', fpass);
  }
  cell('sp_voice', sp.voice, sp.voice_green);
  cell('sp_sex', sp.sex, sp.sex_green);
  cell('sp_stack', sp.stack, sp.stack_green);
}
async function tick(){
  try{
    const [p,b] = await Promise.all([
      fetch('/v1/presence',{cache:'no-store'}).then(r=>r.json()),
      fetch('/v1/faceid',{cache:'no-store'}).then(r=>r.json()),
    ]);
    draw(p,b);
    fetch('/v1/faceid/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(()=>{});
  }catch(e){}
}
tick(); setInterval(tick, 1200);
</script>
</body></html>
"""
