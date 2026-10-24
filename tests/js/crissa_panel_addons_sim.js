// Drive the public Control Panel page (display/crissa-panel.html) in jsdom for the add-on slot: with no add-on the
// bottom section shows the placeholder (off mark + words, nothing to press); with one installed its tiles sit at the
// bottom, a link tile opens its page and a unit tile with a page gets an Open key. Prints what a person would see as
// JSON. Run by tests/test_crissa_panel_addons.py. Exit 3 when jsdom is not installed (the test skips).
const fs = require("fs");
const path = require("path");
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH || "jsdom")); }
catch (e) { process.stderr.write(String(e)); process.exit(3); }
const ROOT = path.resolve(__dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "display", "crissa-panel.html"), "utf8");

let installed = false;
const posts = [], opened = [];
function services() {
  const core = ["crissa-panel", "crissa-wall"].map(u => ({ unit: u, label: u, section: "Wall services",
    active: "active", sub: "running", health: "ok", note: "" }));
  const ph = { slot: "krew", label: "Krew", note: "krew not installed", state: "off", mark: "○",
               section: "krew services", placeholder: true };
  const tiles = [
    { unit: "agent-gateway", label: "Gateway - every profile", section: "krew services", addon: "krew", active: "active", sub: "running", health: "ok", note: "" },
    { unit: "agent-dashboard", label: "Dashboard - the agent's page", section: "krew services", addon: "krew", active: "active", sub: "running",
      health: "ok", note: "", href: "http://127.0.0.1:9999/" },
    { unit: "", label: "Board - the work", section: "krew services", addon: "krew", link: true, href: "http://127.0.0.1:9999/board", health: "", note: "" },
    { id: "keeper", unit: "krew-keeper.timer", label: "Keeper - the loop", section: "krew services", addon: "krew",
      active: "active", sub: "waiting", health: "ok", note: "", run: { unit: "krew-keeper.service" },
      off: { file: "/tmp/keeper.off" }, off_now: false },
  ];
  return { ok: true, title: "Control Panel", systemd: "ok", gpu: null, os_off: { in_force: false },
    fleet: { total: installed ? 5 : 2, down: 0, warn: 0, parked: 0 },
    services: installed ? core.concat(tiles) : core,
    addons: { section: "krew services", sections: ["krew services"],
              installed: installed ? [{ id: "krew", title: "Krew", tiles: 4 }] : [],
              missing: installed ? [] : [ph], errors: [] } };
}
const dom = new JSDOM(html, {
  runScripts: "dangerously", pretendToBeVisual: true, url: "http://127.0.0.1:8097/",
  virtualConsole: new VirtualConsole(),
  beforeParse(w) {
    w.confirm = () => true;
    w.open = (url) => { opened.push(url); return null; };
    w.fetch = (url, opt) => {
      let out = { ok: true };
      if (opt && opt.method === "POST") posts.push(url);
      if (url.startsWith("/v1/services")) out = services();
      else if (url.startsWith("/v1/caps")) out = { ok: true, switch_name: "All OS Off", on_name: "Turn On All Services" };
      else if (url.startsWith("/v1/registry")) out = { ok: true, n: 2, units: [], changes: [], rules: { name: ["crissa-"] } };
      else if (url.startsWith("/v1/os_power")) out = { ok: true, in_force: false, last: {} };
      else if (url.startsWith("/v1/journal")) out = { ok: true, lines: ["a line"] };
      return Promise.resolve({ status: 200, json: () => Promise.resolve(out) });
    };
  },
});
const w = dom.window, d = w.document;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const $ = id => d.getElementById(id);
const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const heads = () => Array.from(d.querySelectorAll(".grp span")).map(s => s.textContent);
const R = {};
(async () => {
  R.deck = {
    work: !!d.querySelector(".deck .work"),
    // the tiles sit in the work deck's left column since round 15 (ea64764e4: tiles, unit list and Off card left,
    // the log right)
    units: !!d.querySelector(".work > #leftCol > #svcRow"),
    log: !!d.querySelector(".work > #logPane"),
    ears: ["bDevMode", "bDemo", "bScriptHold", "bScriptDev"].every(id => $(id)),
  };
  await sleep(400);
  const ph = d.querySelector("#addonSlot .unit.ph");
  R.missing = { heads: heads(), placeholder: ph ? ph.textContent : null, word: $("masterWord").textContent,
                ph_has_unit: ph ? ph.hasAttribute("data-unit") : null, ph_tab: ph ? ph.hasAttribute("tabindex") : null };
  click(ph); await sleep(200);
  R.missing.after_click = { posts: posts.length, opened: opened.length, tape: $("tape").textContent };
  installed = true;
  await sleep(3400);
  const slot = $("addonSlot");
  R.installed = { heads: heads(), tiles: Array.from(slot.querySelectorAll(".unit")).map(t => t.querySelector(".nm").textContent),
                  placeholders: d.querySelectorAll(".unit.ph").length };
  click(slot.querySelector('.unit.link')); await sleep(100);
  R.installed.link_opened = opened.slice();
  click(slot.querySelector('.unit[data-unit="agent-dashboard"]')); await sleep(300);
  R.installed.open_key = !$("bOpen").hidden;
  click($("bOpen")); await sleep(100);
  R.installed.opened = opened.slice();
  click(slot.querySelector('.unit[data-unit="agent-gateway"]')); await sleep(300);
  R.installed.gateway = { open_key: !$("bOpen").hidden, restart_key: !$("bRestart").hidden, tape: $("tape").textContent };
  click(slot.querySelector('.unit[data-id="keeper"]')); await sleep(300);
  R.installed.keeper = { run: !$("bRun").hidden, off: $("bOff").textContent, off_hidden: $("bOff").hidden };
  process.stdout.write(JSON.stringify(R));
  process.exit(0);
})();
