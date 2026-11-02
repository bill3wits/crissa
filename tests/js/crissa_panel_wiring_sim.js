// Drive the Control Panel page (display/crissa-panel.html) in jsdom: open a launcher child's log (a marker for what is
// above, Earlier, Whole log, the reason it is down), page back, press Restart and read the outcome, open a timer
// (its Restart reads "Run now"). Prints what a person would see as JSON. Exit 3 when jsdom is missing.
const fs = require("fs");
const path = require("path");
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || "jsdom")); }
catch (e) { process.stderr.write(String(e)); process.exit(3); }
const ROOT = path.resolve(__dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "display", "crissa-panel.html"), "utf8");
const posts = [];
let reportState = "running";
function services() {
  return { ok: true, title: "Control Panel", systemd: "ok", gpu: null, os_off: { in_force: false },
    fleet: { total: 3, down: 1, warn: 0, parked: 0 }, addons: { section: "Add-ons", missing: [] },
    services: [
      { unit: "crissa-panel", label: "Control Panel - this screen", section: "Control Panel", active: "active", sub: "running", health: "ok", note: "" },
      { unit: "crissa-night.timer", label: "crissa-night.timer", section: "Services", active: "active", sub: "waiting", health: "ok", note: "", run_now: true },
      { unit: "proc:turbo", proc: "turbo", label: "Chatterbox Turbo - the GPU voice", section: "Voices", active: "failed", sub: "exited",
        health: "bad", note: "exit 1: RuntimeError: CUDA out of memory" } ] };
}
function journal(url) {
  if (url.indexOf("before=") >= 0) return { ok: true, lines: ["turbo line 0", "turbo line 1"] };
  return { ok: true, lines: ["turbo line 2", "turbo line 3", "turbo line 4"], earlier: "120",
           marker: "── 120 bytes earlier in turbo.log not shown · Earlier reads them, Whole log opens the file ──" };
}
const dom = new JSDOM(html, {
  runScripts: "dangerously", pretendToBeVisual: true, url: "http://127.0.0.1:8097/",
  virtualConsole: new VirtualConsole(),
  beforeParse(w) {
    w.confirm = () => true;
    w.open = () => null;
    w.fetch = (url, opt) => {
      const body = opt && opt.body ? JSON.parse(opt.body) : null;
      let out = { ok: true };
      if (url.startsWith("/v1/services")) out = services();
      else if (url.startsWith("/v1/caps")) out = { ok: true };
      else if (url.startsWith("/v1/registry")) out = { ok: true, n: 0, units: [], changes: [], rules: {} };
      else if (url.startsWith("/v1/os_power")) out = { ok: true, last: {} };
      else if (url.startsWith("/v1/journal")) out = journal(url);
      else if (url.startsWith("/v1/restart-report")) out = reportState === "done" ?
        { state: "done", ok: true, note: "turbo restarted · :8186 answering (pid 4242)" } : { state: "queued" };
      else if (url.startsWith("/v1/action")) { posts.push(body); out = { ok: true, job: "j1", note: "Restarting turbo…" }; }
      return Promise.resolve({ status: 200, json: () => Promise.resolve(out) });
    };
  },
});
const w = dom.window, d = w.document;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const $ = id => d.getElementById(id);
const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const R = {};
function seen() {
  const txt = $("svcJ").textContent.split("\n");
  const body = txt.filter(l => !l.startsWith("──"));
  return { marker_shown: txt[0].startsWith("── 120 bytes earlier"), earlier_key: !$("bEarlier").hidden,
           follow_key: !$("bFollow").hidden, lines: body.length, first_line: body[0],
           whole_href: $("bWhole").getAttribute("href"), why: $("svcWhy").hidden ? "" : $("svcWhy").textContent };
}
(async () => {
  await sleep(500);
  click(d.querySelector('.unit[data-unit="proc:turbo"]')); await sleep(300);
  R.first = seen();
  click($("bEarlier")); await sleep(300);
  R.earlier = seen();
  R.earlier.lines = $("svcJ").textContent.split("\n").filter(l => !l.startsWith("──")).length;
  click($("bRestart")); await sleep(300);
  reportState = "done"; await sleep(1500);
  R.restart = { posted: posts[posts.length - 1], toast: $("toast").textContent };
  click(d.querySelector('.unit[data-unit="crissa-night.timer"]')); await sleep(200);
  R.timer = { key: $("bRestart").textContent };
  process.stdout.write(JSON.stringify(R));
  process.exit(0);
})();
