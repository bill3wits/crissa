// Drive the public Control Panel page (display/crissa-panel.html) in jsdom: normal, All OS Off in force, a machine
// with and without a GPU step, Park GPU. Prints what a person would see as JSON. Run by tests/test_crissa_panel.py.
// Exit 3 when jsdom is not installed (the test skips).
const fs = require("fs");
const path = require("path");
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || "jsdom")); }
catch (e) { process.stderr.write(String(e)); process.exit(3); }
const ROOT = path.resolve(__dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "display", "crissa-panel.html"), "utf8");

let osOff = { in_force: false };
let gpu = null;
const actions = [];
function services() {
  const units = ["crissa-panel", "crissa-wall", "crissa-tts"];
  return { ok: true, title: "Control Panel", systemd: "ok", gpu: gpu ? Object.assign({ os_off: osOff }, gpu) : null,
    os_off: osOff, fleet: { total: 3, down: 0, warn: osOff.in_force ? 0 : 0, parked: osOff.in_force ? 2 : 0 },
    services: units.map(u => {
      const off = osOff.in_force && u !== "crissa-panel";
      return { unit: u, label: u, section: "Services", active: off ? "inactive" : "active", sub: off ? "dead" : "running",
               health: off ? "warn" : "ok", parked: off, note: off ? "parked in All OS Off" : "" };
    }) };
}
const dom = new JSDOM(html, {
  runScripts: "dangerously", pretendToBeVisual: true, url: "http://127.0.0.1:8097/",
  virtualConsole: new VirtualConsole(),
  beforeParse(w) {
    w.confirm = () => true;
    w.fetch = (url, opt) => {
      const body = opt && opt.body ? JSON.parse(opt.body) : null;
      let out = { ok: true };
      if (url.startsWith("/v1/services")) out = services();
      else if (url.startsWith("/v1/caps")) out = { ok: true, switch_name: "All OS Off", on_name: "Turn On All Services" };
      else if (url.startsWith("/v1/registry")) out = { ok: true, n: 3, units: [], changes: [], rules: { name: ["crissa-"] } };
      else if (url.startsWith("/v1/os_power")) out = { ok: true, in_force: osOff.in_force, last: {} };
      else if (url.startsWith("/v1/action")) { actions.push(body.action); out = { ok: true, note: "ok" }; }
      return Promise.resolve({ status: 200, json: () => Promise.resolve(out) });
    };
  },
});
const w = dom.window, d = w.document;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const poll = () => sleep(3400);
const $ = id => d.getElementById(id);
const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const R = {};
(async () => {
  await sleep(400);
  R.normal = { word: $("masterWord").textContent, key: $("bHouseWord").textContent };
  R.nogpu_keys = $("gpuKeys").children.length;
  osOff = { in_force: true, since: "2026-10-06T05:00:00", n: 2, paused: ["crissa-wall.service", "crissa-tts.service"], failed: [] };
  await poll();
  R.off = { word: $("masterWord").textContent, sub: $("svcFleet").textContent, key: $("bHouseWord").textContent,
            tile_parked: d.querySelector('.unit[data-unit="crissa-tts"]').classList.contains("parked") };
  click($("bHouse")); await sleep(200);
  R.off_click = actions[actions.length - 1];
  osOff = { in_force: false };
  gpu = { mode: "off", return_mode: "render", off_label: "Park GPU", park_what: "the model servers",
          modes: [{ id: "normal", label: "Standard" }, { id: "render", label: "Render" }] };
  await poll();
  R.gpu_keys = Array.from($("gpuKeys").children).map(b => b.textContent);
  R.parked_word = $("masterWord").textContent;
  process.stdout.write(JSON.stringify(R));
  process.exit(0);
})();
