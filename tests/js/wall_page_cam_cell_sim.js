// The Wall page's cam cell (display/voiceonly-home.html, paintSpeaker) against the desk Wall's
// (lib/house_wall_ascii_render._face_cell), the User 2026-10-11: "Brave Cam feed need to be fixed to align with
// recent update to cam wallpaper. Cam Brave failed to display any reading at all."
//
// Run by tests/test_wall_page_cam_cell_20261011.py, which computes the Python side of the same fixtures.
//   PAGE_HTML   the page to read (the house's display/ copy or the kit's crissa/overlay/ copy)
//   CASES_JSON  a file: [{name, sp, pres, now}] fixtures, shared with the Python cell
//   JSDOM_PATH  optional: when set, the whole page also runs in jsdom (part B)
// Prints one JSON object: {unit: {...}, page: {...} | null}.
"use strict";
const fs = require("fs");
const vm = require("vm");

const PAGE = process.env.PAGE_HTML;
const html = fs.readFileSync(PAGE, "utf8");
const CASES = JSON.parse(fs.readFileSync(process.env.CASES_JSON, "utf8"));
const tick = () => new Promise(r => setImmediate(r));
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---- part A: the page's own cam block and paintSpeaker, lifted out of the page and run with stand-ins ----------------

function lifted(src) {
  const a = src.indexOf("/* ---- the cam cell is the desk Wall's reading");
  const b = src.indexOf("function pct(x){", a);
  const c = src.indexOf("function paintSpeaker(){");
  const d = src.indexOf("\n/* ---- room clock", c);
  if (a < 0 || b < 0 || c < 0 || d < 0) throw new Error("the page's cam block or paintSpeaker was not found");
  return src.slice(a, b) + "\n" + src.slice(c, d);
}

function harness(opts) {
  opts = opts || {};
  const writes = [];
  const fetched = [];
  const spk = {
    attrs: {}, html: "",
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
  };
  Object.defineProperty(spk, "innerHTML", {
    get() { return this.html; },
    set(v) { this.html = String(v); writes.push(this.html); },
  });
  const pip = { getAttribute(k) { return k === "src" ? (opts.pipSrc === undefined ? "http://127.0.0.1:8191/visor" : opts.pipSrc) : null; } };
  const ctx = {
    console, URL, URLSearchParams, Promise, Error, JSON, Math, Number, String, Array, Object,
    location: { search: opts.search || "", href: "http://127.0.0.1:8099/" + (opts.search || "") },
    document: { getElementById(id) { return id === "spk" ? spk : (id === "pipframe" ? pip : null); } },
    SPK: { ok: true, name: "", locked: null, sim: null, stack: null, faces: [] },
    SOUND: { beam: 5.9 },
    who: { name: "", via: "none" },
    nowMs: 0,
    answer: () => Promise.reject(new Error("no answer")),
    esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); },
    paintWhoMode() {},
  };
  ctx.masterWho = () => ctx.who;
  ctx.speakerNone = () => !ctx.who.name;
  ctx.Date = { now: () => ctx.nowMs };
  ctx.fetch = (url, o) => { fetched.push(String(url)); return ctx.answer(String(url), o); };
  vm.createContext(ctx);
  vm.runInContext(lifted(html), ctx, { filename: "voiceonly-home.html#cam" });
  return { ctx, spk, writes, fetched };
}

const CAM_RE = /cam <span class="([^"]+)"(?: data-cam="([a-z]+)")?(?: style="opacity:([0-9.]+)")?>([^<]*)<\/span>/;

function camSpan(rowHtml) {
  const m = CAM_RE.exec(rowHtml || "");
  if (!m) return null;
  return { cls: m[1], state: m[2] || null, opacity: m[3] === undefined ? null : Number(m[3]), txt: m[4] };
}

function setCase(h, c) {
  h.ctx.SPK.sim = (c.sp && c.sp.sim !== undefined) ? c.sp.sim : null;
  h.ctx.SPK.stack = (c.sp && c.sp.stack !== undefined) ? c.sp.stack : null;
  h.ctx.CAMF.pres = (c.pres === undefined) ? null : c.pres;
  h.ctx.nowMs = c.now * 1000;
}

async function unit() {
  const out = { cases: {}, pass: null, flash_s: null, dash: null };
  const h = harness();
  out.pass = h.ctx.CAMF.pass;
  out.flash_s = h.ctx.CAMF.flashS;
  out.dash = h.ctx.CAMF.dash;
  out.on = h.ctx.CAMF.on;
  for (const c of CASES) {
    setCase(h, c);
    // camFaceCell is fed what camCell feeds it: the /v1/speaker answer's sim and stack, presence, now
    const cell = h.ctx.camFaceCell({ sim: h.ctx.SPK.sim, stack: h.ctx.SPK.stack }, h.ctx.CAMF.pres, c.now);
    h.spk.attrs = {};
    h.ctx.paintSpeaker();
    out.cases[c.name] = { cell: JSON.parse(JSON.stringify(cell)), span: camSpan(h.spk.html) };
  }

  // the row repaints only when it changes: a live number paints once, a flash paints as it fades
  const live = CASES.find(c => c.name === "live_88"), flash = CASES.find(c => c.name === "presence_flash_3s");
  const r = harness();
  setCase(r, live);
  r.ctx.paintSpeaker(); r.ctx.paintSpeaker(); r.ctx.nowMs += 1000; r.ctx.paintSpeaker();
  out.live_writes = r.writes.length;
  const f = harness();
  setCase(f, flash);
  f.ctx.paintSpeaker(); f.ctx.paintSpeaker(); f.ctx.nowMs += 1000; f.ctx.paintSpeaker();
  out.flash_writes = f.writes.length;
  out.flash_opacities = f.writes.map(w => (camSpan(w) || {}).opacity);
  f.ctx.nowMs += 20000; f.ctx.paintSpeaker(); f.ctx.paintSpeaker();
  out.after_flash = camSpan(f.spk.html);
  out.after_flash_writes = f.writes.length;

  // her turn holds the row, this cell included
  const z = harness();
  setCase(z, live);
  z.ctx.who = { name: "crissa", via: "mouth" };
  z.ctx.paintSpeaker();
  out.her_turn_writes = z.writes.length;
  z.ctx.who = { name: "", via: "none" };
  z.ctx.paintSpeaker();
  out.after_her_turn = camSpan(z.spk.html);

  // the cell does not depend on who is named (the desk Wall's does not): a named speaker reads the same
  const n = harness();
  setCase(n, live);
  n.ctx.who = { name: "user", via: "face" };
  n.ctx.paintSpeaker();
  out.named = camSpan(n.spk.html);

  // presence: the address the camera frame names, a failed or refused read keeps the last answer
  const p = harness();
  let calls = 0;
  const good = { ts: 1, user: { user_face_sim: 0.88, face_age_s: 3 } };
  p.ctx.answer = () => {
    calls += 1;
    if (calls === 1) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(good) });
    if (calls === 2) return Promise.reject(new Error("refused"));
    if (calls === 3) return Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({ error: "down" }) });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([1, 2]) });
  };
  const kept = [];
  for (let i = 0; i < 4; i++) {
    p.ctx.pullPresence();
    for (let k = 0; k < 5; k++) await tick();
    kept.push(p.ctx.CAMF.pres === null ? null : JSON.stringify(p.ctx.CAMF.pres));
  }
  out.presence_urls = p.fetched;
  out.presence_kept = kept;
  out.presence_good = JSON.stringify(good);

  const bases = {};
  for (const src of ["http://127.0.0.1:8191/visor", "http://127.0.0.1:8219/visor", "http://host.example:9/pre/visor",
                     "__VO_PRESENCE__/visor", ""]) {
    const b = harness({ pipSrc: src });
    bases[src] = b.ctx.camfBase();
  }
  out.bases = bases;

  // the kill: ?camface=0 is the old cell (the stack's camera component, gold until green) and reads nothing
  const k0 = harness({ search: "?camface=0" });
  out.kill_on = k0.ctx.CAMF.on;
  k0.ctx.who = { name: "user", via: "face" };
  setCase(k0, { now: live.now, sp: { sim: null, stack: { face: 0.88, camera: 0.0, camera_green: false } }, pres: null });
  k0.ctx.paintSpeaker();
  out.kill_cell_cam0 = camSpan(k0.spk.html);
  setCase(k0, { now: live.now + 1, sp: { sim: null, stack: { face: 0.88, camera: 1.0, camera_green: true } }, pres: null });
  k0.ctx.paintSpeaker();
  out.kill_cell_cam1 = camSpan(k0.spk.html);
  k0.ctx.pullPresence();
  await tick();
  out.kill_fetches = k0.fetched.length;
  const k1 = harness({ search: "?camface=1" });
  out.camface1_on = k1.ctx.CAMF.on;
  return out;
}

// ---- part B: the whole page in jsdom, its own timers and fetches ---------------------------------------------------

async function page() {
  const jsdomPath = process.env.JSDOM_PATH;
  if (!jsdomPath) return null;
  const { JSDOM, VirtualConsole } = require(jsdomPath);
  const PRES_ADDR = "http://127.0.0.1:8219";      // what a pack's feed writes over the kit's __VO_PRESENCE__
  const src = html.replace(/__VO_PRESENCE__/g, PRES_ADDR).replace(/__VO_PLAYER__/g, "http://127.0.0.1:8100");

  function run(name, search, speaker, presence) {
    const fetched = [];
    const errors = [];
    let presCalls = 0;
    const vc = new VirtualConsole();
    vc.on("jsdomError", e => {
      const m = String((e && e.message) || e);
      if (!/Not implemented/.test(m)) errors.push(m.slice(0, 300));
    });
    const dom = new JSDOM(src, {
      runScripts: "dangerously", pretendToBeVisual: true, url: "http://127.0.0.1:8099/" + search, virtualConsole: vc,
      beforeParse(w) {
        w.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
        w.EventSource = function () { this.close = () => {}; this.addEventListener = () => {}; };
        w.fetch = (url) => {
          url = String(url);
          fetched.push(url);
          if (/\/v1\/presence$/.test(url)) {
            presCalls += 1;
            const p = presence(presCalls, w.Date.now() / 1000);
            if (p === "fail") return Promise.reject(new TypeError("Failed to fetch"));
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(p) });
          }
          let out = { ok: true };
          if (url.startsWith("/v1/speaker")) out = speaker;
          return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(out) });
        };
      },
    });
    return sleep(2700).then(() => {
      const d = dom.window.document;
      const row = d.getElementById("spk").innerHTML;
      const res = { cam: camSpan(row), presence_urls: [...new Set(fetched.filter(u => /presence/.test(u)))],
                    presence_calls: presCalls, errors };
      dom.window.close();
      return [name, res];
    });
  }

  const noFace = { ok: true, sim: null, name: "", n_faces: 0, stack: { face: 0.0, camera: 0.0, camera_green: false, age_s: 0.2 } };
  const sp = face => ({ ok: true, sim: null, name: "", n_faces: 1, stack: { face, camera: 0.0, camera_green: false, age_s: 0.2 } });
  const quiet = (n, now) => ({ ts: now, speaker: { face: 0.0, age_s: 1.0 }, user: { user_face_sim: 0.88, face_age_s: 469.1 } });
  const look = age => (n, now) => ({ ts: now, speaker: { face: 0.0, age_s: 1.0 }, user: { user_face_sim: 0.88, face_age_s: age } });
  const once = (n, now) => (n === 1 ? look(3)(n, now) : "fail");

  const runs = await Promise.all([
    run("live_88", "", sp(0.88), quiet),
    run("live_42", "", sp(0.42), quiet),
    run("presence_flash_3s", "", noFace, look(3)),
    run("presence_12s", "", noFace, look(12)),
    run("presence_then_fail", "", noFace, once),
    run("kill", "?camface=0", sp(0.88), look(3)),
  ]);
  return { presence_addr: PRES_ADDR, runs: Object.fromEntries(runs) };
}

(async () => {
  const out = { unit: await unit(), page: await page() };
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch(e => { process.stderr.write(String((e && e.stack) || e)); process.exit(1); });
