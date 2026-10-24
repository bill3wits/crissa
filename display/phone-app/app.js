// Crissa on the phone (phone-pack/1 + phone-show/1, lib/phone_pack.py). Each clip is a pack; the one she talks on
// carries every mouth shape painted beside each frame, so the face and the mouth are drawn from the same decoded
// frame. She walks her clips like the Wall: loops dwell on their hub, a slide carries her to the other hub, a
// flourish plays once and lands back on its loop. She talks only on her talk loop; a line that arrives while she is
// elsewhere waits until she is back. Two video players take turns so a cut never shows an empty frame.
"use strict";

const $ = (id) => document.getElementById(id);
const face = $("face"), ctx = face.getContext("2d", { alpha: false });
const store = {
  get(k, d) { try { const v = localStorage.getItem("crissa." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("crissa." + k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
};

// her idle walk (seconds): how long she stays up with nobody talking before she may look down at her phone, and how
// long she stays down before she comes back on her own
const Q = new URLSearchParams(location.search), num = (k, d) => (Q.has(k) ? +Q.get(k) : d);   // ?idle=5 etc. for a test
const IDLE_UP_S = num("idle", 35), DOWN_MIN_S = num("downmin", 18), DOWN_MAX_S = num("downmax", 45),
  FLOURISH_P = num("flourish", 0.35), UP_FLOURISH_MIN_S = num("upmin", 20),
  // TALKING FLOURISHES (the User 2026-10-17): a clip baked with her mouths (a stamped flourish) is one she can talk
  // on. ?talkfl=0 = she talks on her talk loop only.
  TALK_FLOURISH_P = num("talkfl", 0.3);
// A CHANGE OF CLIP IS A SHORT BLEND (the User 2026-10-17: the video tool redraws her a little at a flourish's ends -
// strands of hair, skin - so a hard cut onto or off her loop showed; measured 7-8 after the clip is fitted to the
// hub, where her loop's own seam is ~2). The last frame on the glass fades out over the new clip. ?fade=0 = hard cuts.
const FADE_MS = num("fade", 250);
let fadeFrom = null, fadeAt = 0;
// A CHANGE OF MOUTH IS A SHORT BLEND TOO (the User 2026-10-20, "a touch of smoothing", as the desk Wall ships it since
// round 23: one semiframe, 20.833 ms). Both mouths come from the same decoded frame and land in the same box, so the
// blend is in time only and never moves where her mouth sits. The server says how long (/api/show xfade_ms; its kill
// CRISSA_PHONE_XFADE=0 sends 0); ?xfade=N for one tab, ?xfade=0 = hard swaps.
let XFADE_MS = num("xfade", 1000 / 48);
let mouthWas = null, mouthNow = null, mouthAt = 0;

let show = null, packs = {}, players = [], cur = 0;      // players[cur] is on the glass
let clip = null, enteredAt = 0, lastTalk = performance.now(), wantUp = false, lastFlourish = "";
let patch = null, pctx = null, mask = null;
let actx = null, line = null, waiting = null;            // waiting: a line made while she was not on her talk loop
let leadMs = store.get("leadMs", 0);
let mode = store.get("mode", "chat");
const history = [];

function packUrl(k) { return "/pack/" + (show.clips[k].pack || k) + "/"; }

function newPlayer() {
  const v = document.createElement("video");
  v.muted = true; v.playsInline = true; v.setAttribute("playsinline", ""); v.preload = "auto";
  v.className = "film";
  document.body.appendChild(v);
  v.addEventListener("ended", () => { if (v === players[cur]) next(); });
  return v;
}

async function load() {
  const r = await (await fetch("/api/show")).json();
  show = r.show; packs = r.packs;
  if (!Q.has("xfade") && typeof r.xfade_ms === "number") XFADE_MS = r.xfade_ms;
  const talk = packs[show.talk];
  const [, , W, H] = talk.base;
  face.width = W; face.height = H;
  const [, , bw, bh] = talk.box;
  patch = document.createElement("canvas"); patch.width = bw; patch.height = bh;
  pctx = patch.getContext("2d");
  mask = featherMask(bw, bh, talk.feather || 16);
  const poster = new Image();
  poster.onload = () => ctx.drawImage(poster, 0, 0, W, H);
  poster.src = packUrl(show.talk) + "poster.jpg";
  players = [newPlayer(), newPlayer()];
  cue(players[0], show.talk);
  clip = show.talk; enteredAt = performance.now();
}

function cue(v, k) {
  v.dataset.clip = k;
  v.src = packUrl(k) + packs[k].video;
  v.load();
}

// a clip carries her mouths when its pack has any mouth cells: an unpainted pack's `cells` is {}, which is true in
// JavaScript, so count them (an empty one let a line start on a slide and sent her talking into unpainted flourishes)
const hasMouths = (m) => !!(m && m.cells && Object.keys(m.cells).length);
function painted(k) { return (hasMouths(packs[k]) && TALK_FLOURISH_P > 0) || k === show.talk; }
function clipS(k) { return packs[k] && packs[k].n ? packs[k].n / (packs[k].fps || 24) : 10; }
function lineLeftS() { return line && actx ? line.start + line.sheet.dur - actx.currentTime : 0; }

function clipsWhere(test) { return Object.keys(show.clips).filter((k) => test(show.clips[k])); }
function loopOn(hub) { return clipsWhere((c) => c.role === "loop" && c.start === hub)[0]; }
function slide(from, to) { return clipsWhere((c) => c.role === "slide" && c.start === from && c.end === to)[0]; }

function choose() {
  // what plays after the clip that just ended (the Wall's walk, simplified)
  const c = show.clips[clip], hub = c.end, up = show.talk_hub;
  const busy = line || waiting || $("send").disabled;
  const idleS = (performance.now() - lastTalk) / 1000, hereS = (performance.now() - enteredAt) / 1000;
  if (c.role === "slide") return loopOn(hub) || show.talk;          // a slide always lands on its hub's loop
  if (hub !== up) {
    const wake = slide(hub, up);
    if (wantUp || busy) return wake || loopOn(hub) || clip;
    if (c.role === "loop") {
      const fl = clipsWhere((x) => x.role === "flourish" && x.start === hub);
      if (fl.length && hereS > DOWN_MIN_S / 2 && Math.random() < FLOURISH_P) {
        const pick = fl.filter((k) => k !== lastFlourish)[0] || fl[0];
        lastFlourish = pick;
        return pick;
      }
      if (wake && hereS > DOWN_MIN_S + Math.random() * (DOWN_MAX_S - DOWN_MIN_S)) return wake;
    }
    return loopOn(hub) || clip;
  }
  // an up flourish (the glance, 2026-10-17) plays now and then while she listens. While she talks only a PAINTED one
  // may start (her mouth goes with her), and only with enough of the line left to cover it; a line still coming
  // never sends her to an unpainted clip.
  const upFl = clipsWhere((x) => x.role === "flourish" && x.start === up);
  if (c.role === "loop" && upFl.length && (line || !wantUp)) {     // talking, she is up already: wantUp is met
    const talking = !!line, ok = talking ? upFl.filter((k) => painted(k) && lineLeftS() > clipS(k) * 0.6) : upFl;
    if (ok.length && (talking ? Math.random() < TALK_FLOURISH_P : !busy && hereS > UP_FLOURISH_MIN_S && Math.random() < FLOURISH_P)) {
      const pick = ok.filter((k) => k !== lastFlourish)[0] || ok[0];
      lastFlourish = pick;
      return pick;
    }
  }
  if (!busy && !wantUp && idleS > IDLE_UP_S && Math.random() < 0.5) {
    const down = clipsWhere((x) => x.role === "slide" && x.start === up)[0];
    if (down) return down;
  }
  return loopOn(up) || show.talk;
}

function next() {
  const was = clip, k = choose();
  const v = players[cur];
  if (k === was) {                      // stay: the same loop again from its first frame
    v.currentTime = 0; v.play().catch(() => {});
    return;
  }
  const n = players[1 - cur];
  const go = () => { holdLastFrame(); cur = 1 - cur; n.currentTime = 0; n.play().catch(() => {}); prefetch(); };
  if (n.dataset.clip !== k) cue(n, k);
  clip = k; enteredAt = performance.now();
  if (show.clips[k].start === show.talk_hub && show.clips[k].role === "loop") wantUp = false;
  if (n.readyState >= 2) go(); else n.addEventListener("canplay", go, { once: true });
}

function holdLastFrame() {
  // what is on the glass now, kept to fade out over the next clip
  if (FADE_MS <= 0 || !face.width) return;
  if (!fadeFrom) { fadeFrom = document.createElement("canvas"); }
  fadeFrom.width = face.width; fadeFrom.height = face.height;
  fadeFrom.getContext("2d").drawImage(face, 0, 0);
  fadeAt = performance.now();
}

function prefetch() {
  // the clip most likely next waits ready in the other player
  const c = show.clips[clip], n = players[1 - cur];
  let k = null;
  if (c.role === "slide") k = loopOn(c.end);
  else if (c.role === "flourish") k = loopOn(c.start);
  else if (c.start !== show.talk_hub) k = slide(c.start, show.talk_hub);
  else k = clipsWhere((x) => x.role === "slide" && x.start === c.start)[0];
  if (k && n.dataset.clip !== k) cue(n, k);
}

function featherMask(w, h, f) {
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  const g = c.getContext("2d"), img = g.createImageData(w, h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const d = Math.min(x, y, w - 1 - x, h - 1 - y);
    const a = Math.max(0, Math.min(1, d / f));
    img.data[(y * w + x) * 4 + 3] = Math.round(255 * a * a * (3 - 2 * a));
  }
  g.putImageData(img, 0, 0);
  return c;
}

function paintMouth(v, m, cell, alpha) {
  // one mouth shape from beside this frame, feathered into her box
  const [bx, by, bw, bh] = m.box;
  pctx.globalCompositeOperation = "copy";
  pctx.drawImage(v, cell[0], cell[1], bw, bh, 0, 0, bw, bh);
  pctx.globalCompositeOperation = "destination-in";
  pctx.drawImage(mask, 0, 0);
  if (alpha < 1) ctx.globalAlpha = alpha;
  ctx.drawImage(patch, bx, by);
  ctx.globalAlpha = 1;
}

function classNow(m) {
  const idle = (m || packs[show.talk]).idle;
  if (!line || !actx) return idle;
  const lat = (actx.outputLatency || 0) + (actx.baseLatency || 0);
  const t = actx.currentTime - line.start - lat + leadMs / 1000;
  const sh = line.sheet;
  if (t > sh.dur + 0.3) { line = null; lastTalk = performance.now(); return idle; }
  const i = Math.floor((t - sh.t0) / sh.hop);
  return (i >= 0 && i < sh.classes.length) ? sh.classes[i] : idle;
}

function frame() {
  const v = players[cur];
  if (show && v && v.readyState >= 2) {
    const k = v.dataset.clip, m = packs[k];
    const [, , W, H] = m.base;
    ctx.drawImage(v, 0, 0, W, H, 0, 0, face.width, face.height);
    if (painted(k) && hasMouths(m)) {
      if (waiting) { const w = waiting; waiting = null; play(w); }
      const want = classNow(m), cls = m.cells[want] ? want : m.idle, now = performance.now();
      if (cls !== mouthNow) { mouthWas = mouthNow; mouthNow = cls; mouthAt = now; }
      const a = XFADE_MS > 0 && mouthWas && m.cells[mouthWas] ? (now - mouthAt) / XFADE_MS : 1;
      if (a < 1) paintMouth(v, m, m.cells[mouthWas], 1);
      if (m.cells[cls]) paintMouth(v, m, m.cells[cls], Math.max(0, a));
    }
    if (fadeAt) {
      const a = 1 - (performance.now() - fadeAt) / FADE_MS;
      if (a > 0) { ctx.globalAlpha = a; ctx.drawImage(fadeFrom, 0, 0); ctx.globalAlpha = 1; } else fadeAt = 0;
    }
  }
  requestAnimationFrame(frame);
}

async function speak(res) {
  if (!res.ok) { caption(res.error || "She could not answer."); return; }
  const buf = await (await fetch(res.audio)).arrayBuffer();
  const audio = await new Promise((ok, bad) => actx.decodeAudioData(buf, ok, bad));
  const item = { audio, sheet: res.sheet };
  if (painted(players[cur].dataset.clip)) play(item);
  else { waiting = item; wantUp = true; }
}

function play(item) {
  if (line && line.src) { try { line.src.stop(); } catch (e) { /* ended */ } }
  const src = actx.createBufferSource();
  src.buffer = item.audio; src.connect(actx.destination);
  const start = actx.currentTime + 0.06;
  src.start(start);
  line = { src, start, sheet: item.sheet };
  lastTalk = performance.now();
}

function caption(t) { $("caption").textContent = t || ""; }

// THIS PHONE WAS UNPAIRED while the page was open (on the computer, or its pairing file was cleared): the server
// answers 401, so the page goes back to Connect and says how to come back, instead of a line that never comes.
const LOST_PAIRING = "This phone is no longer connected to your Crissa. Scan the QR code on your computer to connect it again.";
function lostPairing() {
  if (!pairing) return;
  paired = false; connWhy = LOST_PAIRING;
  showConn(); openConnect(true);
}

// THE PHONE HAS THE FLOOR (the User 2026-10-18, "one or the other from that button"): Send takes it, and it is kept
// (refreshed every 2 s) while she thinks and while her answer plays here, then given back; while the phone has it the
// computer's ear takes no turn, so the desk Crissa never answers the phone. A server without the route ignores it.
let floorTimer = 0;
function floor(on) {
  fetch("/api/floor", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on }) })
    .catch(() => {});
}
function holdFloor() {
  floor(true);
  if (floorTimer) return;
  floorTimer = setInterval(() => {
    if ($("send").disabled || line || waiting) { floor(true); return; }
    clearInterval(floorTimer); floorTimer = 0; floor(false);
  }, 2000);
}

async function send(text) {
  const bar = $("bar"), btn = $("send");
  bar.classList.add("thinking"); btn.disabled = true; wantUp = true; lastTalk = performance.now();
  holdFloor();
  caption(mode === "chat" ? "" : text);
  try {
    const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) }).then((r) => { if (r.status === 401) lostPairing(); return r.json(); });
    let words = text;
    if (mode === "chat") {
      const ans = await post("/api/chat", { text, history, say: false });
      if (!ans.ok) { caption(ans.error || "She could not answer."); return; }
      words = ans.reply;
      history.push({ role: "user", content: text }, { role: "assistant", content: words });
      caption(words);
    }
    await speak(await post("/api/say", { text: words }));
  } catch (e) {
    caption("No answer from the house. Check you are on the home Wi-Fi.");
  } finally {
    bar.classList.remove("thinking"); btn.disabled = false;
  }
}

// SHE IS CRISSA'S SECOND SCREEN (the User 2026-10-18, "what good is the app without a connection"): Chat answers
// through the agent set up in Connect on the computer. Until there is one, the toggle says so, she opens in Say,
// and a tap on Chat says where to connect; it asks again every 30 s, so connecting on the computer turns Chat on.
let connected = true;
async function checkAgent() {
  try {
    const r = await (await fetch("/api/agent", { cache: "no-store" })).json();
    if (!r.ok || typeof r.connected !== "boolean") return;          // an older server without the route: keep Chat
    const was = connected; connected = r.connected;
    if (!connected && mode === "chat") setMode("say");
    if (connected !== was) setMode(mode);
  } catch (e) { /* the house is unreachable: load() and send() already say so */ }
}
const NOT_CONNECTED = "Chat needs your agent. On your computer, open Crissa's Wall and use Connect. Say works now.";

function setMode(m) {
  mode = m; store.set("mode", m);
  $("mode").textContent = m === "chat" ? "Chat" : (connected ? "Say" : "Say · no agent");
  $("text").placeholder = m === "chat" ? "Talk to Crissa" : "A line for her to say";
}

$("mode").addEventListener("click", () => {
  if (mode === "say" && !connected) { caption(NOT_CONNECTED); return; }
  setMode(mode === "chat" ? "say" : "chat");
});
$("text").addEventListener("focus", () => { wantUp = true; lastTalk = performance.now(); });   // she looks up as you start
// WHICH CRISSA THIS PHONE TALKS TO (the User 2026-10-20: "default to house connection only if the QR code is used"):
// the desk Wall's QR card hands this page the house's session (?handoff=<single-use token>, claimed once here); a page
// opened without it does not take the house for granted and opens on Connect, where one tap uses your Crissa at this
// computer (reconnect) and Disconnect gives it back. Direct connections to your own agent are the Crissa app's. A server
// without the rule (an older one, or CRISSA_PHONE_HANDOFF=0) keeps today's page: always the house.
// A server in pairing mode (the User 2026-10-20: the QR is the whole story for a phone) answers this phone only once
// it is paired: the QR claim pairs it (a cookie the browser sends with every request, video included), Disconnect
// gives the pairing back, and without one the only way in is the QR.
let conn = store.get("conn", null), house = "", handoffRule = false, pairing = false, paired = false, connWhy = "";
const mayUse = () => !pairing || paired;
function showConn() {
  const on = (!handoffRule || (conn && conn.mode === "house")) && mayUse();
  const shown = handoffRule || pairing;
  const at = location.host || house || (conn && conn.host);          // where this phone reached it: this page's own address
  $("conn").textContent = !shown ? "" : (on ? "Connected to: your Crissa at " + at : "Not connected");
  $("connectNow").textContent = on ? "Connected to: your Crissa at " + at + "."
    : "This phone is not connected to a Crissa yet.";
  $("connectWhy").textContent = on ? "" : connWhy;
  $("useHouse").textContent = !mayUse() ? "Scan the QR code on your computer to connect this phone"
    : (house ? "Use your Crissa at " + house : "Your Crissa's computer is not reachable");
  $("useHouse").disabled = !house || !mayUse();
  $("useHouse").hidden = !!(on && shown);
  $("leaveHouse").hidden = !(on && shown);
}
function useHouse(via) {
  conn = { mode: "house", host: house, via, at: Date.now() };
  store.set("conn", conn); showConn();
}
function openConnect(open) { $("connect").classList.toggle("open", open); }
const isConnected = () => (!handoffRule || (conn && conn.mode === "house")) && mayUse();
async function checkConnection() {
  let r = null;
  try { r = await (await fetch("/api/connection", { cache: "no-store" })).json(); } catch (e) { r = null; }
  handoffRule = !!(r && r.ok && r.handoff);
  pairing = !!(r && r.ok && r.pairing); paired = !!(r && r.paired);
  house = (r && r.house) || location.host;
  const tok = Q.get("handoff");
  if (tok) {
    window.history.replaceState(null, "", location.pathname);   // (`history` is the chat's) the token is spent either way; never left in the address
    try {
      const got = await (await fetch("/api/handoff/claim", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: tok, name: "phone (QR)" }) })).json();
      if (got.ok) { house = got.house || house; if (got.paired) paired = true; connWhy = ""; useHouse("qr"); }
      else { connWhy = got.error || "That QR code was used or has expired."; caption(connWhy); }
    } catch (e) { connWhy = "No answer from your Crissa's computer. Check you are on the home Wi-Fi."; caption(connWhy); }
  }
  showConn();
  if (!isConnected()) openConnect(true);
  if (mayUse()) start();
}
$("conn").addEventListener("click", () => openConnect(true));
$("connectClose").addEventListener("click", () => openConnect(false));
$("useHouse").addEventListener("click", () => { useHouse("reconnect"); openConnect(false); });
$("leaveHouse").addEventListener("click", async () => {
  conn = null; store.set("conn", null); endSession();
  try { await fetch("/api/handoff/leave", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch (e) { /* gone */ }
  if (pairing) paired = false;
  connWhy = ""; showConn();
});

$("bar").addEventListener("submit", (e) => {
  e.preventDefault();
  const t = $("text").value.trim();
  if (!isConnected()) { openConnect(true); return; }
  if (!t || $("send").disabled) return;
  $("text").value = "";
  if (!actx) wake();
  send(t);
});
$("sync").addEventListener("click", () => {
  const p = $("syncPanel"), open = !p.classList.contains("open");
  p.classList.toggle("open", open); $("sync").setAttribute("aria-expanded", String(open));
});
$("lead").value = leadMs; $("leadOut").textContent = leadMs + " ms";
$("lead").addEventListener("input", (e) => {
  leadMs = +e.target.value; store.set("leadMs", leadMs); $("leadOut").textContent = leadMs + " ms";
});

function wake() {
  if (!actx) {
    actx = new (window.AudioContext || window.webkitAudioContext)();
    const s = actx.createBufferSource(); s.buffer = actx.createBuffer(1, 1, 22050); s.connect(actx.destination); s.start(0);
  }
  if (actx.state === "suspended") actx.resume();
  if (players[cur]) players[cur].play().catch(() => {});
  $("start").classList.add("gone");
}
$("start").addEventListener("click", wake);
document.addEventListener("visibilitychange", () => { if (!document.hidden && actx) wake(); });

// SHE IS ON THE PHONE (the User 2026-10-20): while this page is in use the desk Wall shows her empty office. Using it
// is the server's to count (the floor, her lines); a page that closes or hides ends the session at once, so a phone
// put in a pocket brings her back to the desk. A server without the route ignores it.
function endSession() {
  const b = new Blob([JSON.stringify({ end: true })], { type: "application/json" });
  if (!(navigator.sendBeacon && navigator.sendBeacon("/api/session", b)))
    fetch("/api/session", { method: "POST", body: b, keepalive: true }).catch(() => {});
}
window.addEventListener("pagehide", endSession);
document.addEventListener("visibilitychange", () => { if (document.hidden) endSession(); else beat(); });
// THE HEARTBEAT (round 39, .101 round 38: "i closed phone and she did not return"; an iPhone closing the page sent no
// end beacon): while visible the page says it is still here every 10 s, and the server ends the session when the beats
// stop, whatever happened to the beacon. A beat is never a use. A hidden page's timers stop, so it stops beating too.
function beat() {
  if (document.hidden || !isConnected()) return;
  fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ here: true }) }).catch(() => {});
}

setMode(mode);
// she loads once this phone may reach her: at once on an open server, after the QR claim on a pairing one
let started = false;
function start() {
  if (started) return;
  started = true;
  checkAgent(); setInterval(checkAgent, 30000);
  beat(); setInterval(beat, 10000);
  load().then(() => { players[cur].play().catch(() => {}); prefetch(); requestAnimationFrame(frame); })
    .catch(() => { started = false; caption("Could not load her. Is the house server running?"); });
}
checkConnection();
