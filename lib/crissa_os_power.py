"""All OS Off / Turn On All Services: every service of this install off except the Control Panel, and back exactly.

Lifted from the house's switch (2026-10-06). The spec it was built to: "'All OS Off' means ALL services off, every
tile, except the Control Panel itself so you can turn things back on. 'Turn On All Services' restores exactly what
was on before. No hidden exception list."

So there is no list here to drift. Which units belong to the install is lib/crissa_units.py's answer (the panel's
registry rules), read fresh from systemd at the moment of the click: the same rules the panel's tiles use. The ONE
unit kept on is the panel itself (`self_unit` in the settings), and every report says so and why. A unit that would
not stop or would not come back is named with the reason, never a silent red.

Off, in order:
  1. Snapshot every unit that is on (and the GPU mode, when a GPU step is configured) BEFORE touching anything, so
     a crash half way still leaves On something exact to restore. A second Off while off ADDS to the snapshot.
  2. Stop the restarters first (every timer, path and socket, plus `restarters` from the settings) so nothing
     starts a unit back up while the rest go down.
  3. The GPU step, only when the settings name one (`gpu.cmd` + `gpu.park_args`): a machine whose GPU tenants
     must leave the card in a safe order parks them through its own command. Without one, every unit is stopped
     like any other (gpu policy "leave").
  4. Stop everything else that was on except the panel, verify each one, name what did not go.
On:
  1. The GPU step back to the mode you were in (`gpu.modes[mode].args`), when one is configured.
  2. Start exactly the units in the snapshot (system first, then user, restarters last) and verify each. Units that
     came up without being in the snapshot are named too. A oneshot job that was mid-run at Off is not re-run.

A caller the settings name as not-a-person (`refuse_env`: an environment variable that marks an agent's worker)
is refused: the switch is a person's, on the panel.

ALL OS OFF IS THE WHOLE INSTALL, THE PANEL TOO (the User 2026-11-01, 1.0.7: "someone wired AllOS off to act like Kre off. Not
what we had in mind there. How is someone supposed to shut down completly?" and "We advertise just like clean Ubuntu. Yet we
leave n several services and All OS Off is still showing here"). In a pack Off now stops every unit of the install INCLUDING
the Control Panel, and every unit an installed add-on names (an agent framework's gateways, dashboard and timers, through its panel.d
manifest: lib/crissa_panel_addons), so after it the box is a plain desktop with nothing of Crissa or an add-on running:
  * the panel is snapshotted with the rest (On brings it back) and stopped LAST, after the report is written, so the
    click still reads its answer. In units mode the panel is PartOf the launcher's unit with every other piece (lib/
    crissa_service desk units), so stopping any of that tree stops the panel at once: the tree (the panel, every unit it
    is PartOf, every unit PartOf those) is left for last. Everything else (the add-ons' units, timers, independent units) is
    stopped and verified first; the report is written; then a transient timer of the service manager, outside every
    cgroup Off touches, runs `crissa-os-power finish` `os_power.panel_last_s` later (default 5 s), which stops the tree,
    verifies it and completes the report on disk. A launcher run by hand (no units) is asked to quit at the finish;
  * the way back is her dock icon or `crissa.py` (scripts/crissa.py restore_whole_off): it runs `On` from the snapshot,
    which starts everything that was on, the panel and the add-ons included; while that restore runs the panel never retires
    the snapshot as stale (`os_power.restore_guard_s`, default 600 s);
  * Close Agent stays the light switch (the Wall stays): untouched.
Switch CRISSA_OS_OFF_WHOLE: default ON in a pack, OFF on the house (crissa_home.is_house); =0 is the 10-06 Off, panel kept.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent)) if str(Path(__file__).resolve().parent) not in sys.path else None
import crissa_panel_conf as conf  # noqa: E402
import crissa_units as zu  # noqa: E402

KINDS = zu.KINDS
GPU_POLICIES = ("park", "leave")
WHOLE_SWITCH = "CRISSA_OS_OFF_WHOLE"


def test_refusal(tool: str = "systemd-run", env=None) -> str:
    """UNDER A TEST THE REAL SERVICE MANAGER IS NEVER REACHED (2026-11-01, 1e: a menu test's act("stop") ran a real
    `systemd-run ... crissa-os-power off` on the build machine; it stopped nothing, and this is why it cannot again): with
    PYTEST_CURRENT_TEST set, `tool` is used only when it is the stand-in under tests/crissa_panel_fake. '' = go."""
    e = os.environ if env is None else env
    if not e.get("PYTEST_CURRENT_TEST"):
        return ""
    import shutil
    got = shutil.which(tool, path=e.get("PATH")) or ""
    return "" if "crissa_panel_fake" in got else "refused under a test: %s is not the stand-in (%s)" % (tool, got or "absent")


HOUSE_SWITCH = "CRISSA_OS_POWER_ON_HOUSE"


def house_refusal(env=None) -> str:
    """NEVER THE KIT'S OFF ON THE HOUSE (2026-11-01 07:09, 1e: a test's stray `crissa-os-power off` ran from the house's
    own checkout, read that machine's panel layer - its GPU step and its whole unit registry - and the GPU step parked the
    house's model servers; it was killed before the unit stops). The house has its own Off on its own panel; nothing there
    runs this switch. So on the house Off, On and the finish refuse; plan and status (read only) stay.
    CRISSA_OS_POWER_ON_HOUSE=1 is the only way past, by hand. '' = go."""
    e = os.environ if env is None else env
    if str(e.get(HOUSE_SWITCH, "")).strip().lower() in ("1", "on", "true", "yes"):
        return ""
    try:
        import crissa_home
        house = crissa_home.is_house()
    except Exception:  # noqa: BLE001
        house = False
    return ("this is the house's own install: its All OS Off is the house's own, never the kit's "
            "(%s=1 by hand overrides)" % HOUSE_SWITCH) if house else ""


def whole_on(env=None) -> bool:
    """All OS Off takes the panel and the add-ons too: ON in a pack, OFF on the house, the env wins over both."""
    e = os.environ if env is None else env
    v = str(e.get(WHOLE_SWITCH, "")).strip().lower()
    if v:
        return v not in ("0", "off", "false", "no")
    try:
        import crissa_home
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001 - no manifest reader: a pack
        return True
Runner = Callable[[list, float], "tuple[int, str, str]"]


class Power:
    """One install's switch, built from its settings (tests hand in their own)."""

    def __init__(self, cfg: dict | None = None, rules: zu.Rules | None = None):
        self.cfg = conf.load() if cfg is None else cfg
        self.rules = rules or zu.Rules.from_conf(self.cfg)
        op = dict(self.cfg.get("os_power") or {})
        self.state_dir = Path(op.get("state_dir") or (Path(self.cfg.get("_state") or ".") / "panel"))
        self.snapshot_path = self.state_dir / (op.get("snapshot") or "os-off.snapshot.json")
        self.report_path = self.state_dir / (op.get("report") or "os-power.last.json")
        self.switch_name = op.get("switch_name") or "All OS Off"
        self.on_name = op.get("on_name") or "Turn On All Services"
        self.self_unit = conf.full(self.cfg.get("self_unit") or "crissa-panel.service")
        self.whole = whole_on()
        self.keep = {} if self.whole else {self.self_unit: "the Control Panel itself - the switch you turn everything back on with"}
        self.panel_last_s = float(op.get("panel_last_s", 5.0))
        self.restore_guard_s = float(op.get("restore_guard_s", 600.0))
        self.restarters = tuple(conf.full(u) for u in self.cfg.get("restarters") or ())
        self.gpu = dict(self.cfg.get("gpu") or {})
        self.refuse_env = tuple(self.cfg.get("refuse_env") or ())
        self.cron_prefixes = tuple(self.cfg.get("cron_exec_prefixes") or ())

    # ---- which units: the registry, read fresh at the click --------------------------------------------
    def discover(self, run: Runner = zu.default_run) -> list:
        return [r for r in zu.discover(run, rules=self.rules) if r.get("role") != "masked"]

    def is_restarter(self, unit: str) -> bool:
        return unit.endswith((".timer", ".path", ".socket")) or unit in self.restarters

    @staticmethod
    def _is_on(row: dict) -> bool:
        return row["active"] in ("active", "reloading", "activating")

    @staticmethod
    def _is_job_midrun(row: dict) -> bool:
        return row["type"] == "oneshot" and row["active"] == "activating"

    def plan(self, run: Runner = zu.default_run, rows: Optional[list] = None) -> dict:
        rows = self.discover(run) if rows is None else rows
        stop, keep, off, jobs, unknown = [], [], [], [], []
        for r in rows:
            if r["unit"] in self.keep:
                keep.append(dict(r, why=self.keep[r["unit"]]))
            elif r.get("systemd") == "unreachable":
                unknown.append(r)
            elif not self._is_on(r):
                off.append(r)
            elif self._is_job_midrun(r):
                jobs.append(r)
            else:
                stop.append(r)
        return {"stop": stop, "keep": keep, "already_off": off, "jobs": jobs, "unknown": unknown, "units": rows}

    # ---- GPU (only when configured) ----------------------------------------------------------------------
    def has_gpu(self) -> bool:
        return bool(self.gpu.get("cmd"))

    def _read_json(self, path) -> dict:
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def gpu_mode(self) -> str:
        f = self.gpu.get("mode_file")
        if not f:
            return ""
        try:
            first = Path(f).read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
        except (OSError, IndexError):
            return self.gpu.get("default_mode") or "normal"
        return first or self.gpu.get("default_mode") or "normal"

    def gpu_state(self) -> dict:
        return self._read_json(self.gpu.get("state_file")) if self.gpu.get("state_file") else {}

    def gpu_recipe(self) -> Optional[str]:
        st = self.gpu_state()
        return st.get("prev_recipe") if self.gpu_mode() == "off" else st.get("recipe")

    def gpu_return_mode(self) -> str:
        m = self.gpu_mode()
        if m != "off":
            return m or (self.gpu.get("default_mode") or "normal")
        return str(self.gpu_state().get("prev_mode") or self.gpu.get("default_mode") or "normal")

    def gpu_held(self) -> list:
        f = self.gpu.get("hold_file")
        if not f:
            return []
        try:
            return sorted({ln.strip() for ln in Path(f).read_text().splitlines()
                           if ln.strip() and not ln.strip().startswith("#")})
        except OSError:
            return []

    def mode_args(self, mode: str, recipe: Optional[str]) -> "tuple[list, str]":
        modes = self.gpu.get("modes") or {}
        default = self.gpu.get("default_mode") or "normal"
        spec = modes.get(mode)
        note = ""
        if spec is None:
            spec = modes.get(default) or {"args": [default]}
            note = f"was {mode} before Off - came back to {self.mode_label(default)} (enter {mode} again from the panel)"
        args = []
        for a in spec.get("args") or []:
            if a == "{recipe}":
                a = recipe or spec.get("recipe_default") or ""
            if a:
                args.append(a)
        return args, note

    def mode_label(self, mode: str) -> str:
        if mode == "off":
            return self.gpu.get("off_label") or "Park GPU"
        return ((self.gpu.get("modes") or {}).get(mode) or {}).get("label") or (mode or "").capitalize()

    def gpu_cmd(self, args: list) -> list:
        cmd = self.gpu.get("cmd") or []
        cmd = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        env = self.gpu.get("env") or {}
        return (["env"] + [f"{k}={v}" for k, v in env.items()] if env else []) + cmd + list(args)

    def _gpu_step(self, args: list, run: Runner, timeout: float = 900.0) -> dict:
        rc, out, err = run(self.gpu_cmd(args), timeout)
        tail = [ln for ln in (out + "\n" + err).splitlines() if ln.strip()][-8:]
        name = Path(str((self.gpu.get("cmd") or ["gpu"])[-1])).name
        return {"cmd": f"{name} " + " ".join(args), "rc": rc, "tail": tail}

    # ---- snapshot ------------------------------------------------------------------------------------------
    def _write_json(self, path: Path, obj: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def read_snapshot(self) -> dict:
        d = self._read_json(self.snapshot_path)
        if not d or d.get("restored_ts"):
            return {}
        return d

    # A STALE ALL OS OFF IS RETIRED (round 15, .101 13:37: "Turn On All Services" lit gold while all 18 units ran;
    # in_force came from an os-off.snapshot.json of 2026-10-11 09:37, an older round, which no start had cleared).
    # Off is in force only while what it stopped is still off: a snapshot older than the launcher's current start or
    # than the last Open Agent, or one whose every unit runs again, is moved aside (kept, renamed .cleared-<time>) and
    # the panel stops offering the way back. Kill: CRISSA_OS_OFF_STALE=0.
    # "RUNS AGAIN" ONLY WHEN THINGS CAME BACK (round 16, .101 00:50:38: the panel logged "All OS Off cleared: everything
    # it stopped runs again" at the stop itself): the snapshot is written BEFORE anything stops, so a state read in the
    # seconds after it still has every unit on. The rule reads only a state taken OFF_SETTLE_S after the snapshot and
    # after the Off's own report, i.e. after the stop finished and the units came back.
    OFF_SETTLE_S = 60.0

    @staticmethod
    def _epoch(ts: str) -> float:
        try:
            return time.mktime(time.strptime(str(ts or "")[:19], "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, OverflowError):
            return 0.0

    def stale_why(self, rows: Optional[list] = None, launcher_started: str = "", agent_opened: str = "",
                  rows_at: Optional[float] = None) -> str:
        snap = self.read_snapshot()
        if not snap or str(os.environ.get("CRISSA_OS_OFF_STALE", "1")).strip().lower() in ("0", "off", "false", "no"):
            return ""
        if self.restoring(snap):
            return ""                       # the dock icon's On is bringing it back: never retired half way
        ts = str(snap.get("updated") or snap.get("ts") or "")
        if ts and launcher_started and ts < launcher_started:
            return "Crissa started again since (%s)" % launcher_started.replace("T", " ")
        if ts and agent_opened and ts < agent_opened:
            return "Open Agent since (%s)" % agent_opened.replace("T", " ")
        units = [u.get("unit") for u in snap.get("units") or [] if u.get("unit")]
        if rows is not None and units and rows_at is not None:
            last = self._read_json(self.report_path)
            settled = rows_at >= self._epoch(ts) + self.OFF_SETTLE_S
            after_off = last.get("action") != "off" or rows_at > self._epoch(str(last.get("ts") or ""))
            on = {r.get("unit") for r in rows if self._is_on(r)}
            if settled and after_off and all(u in on for u in units):
                return "everything it stopped runs again"
        return ""

    def restoring(self, snap: Optional[dict] = None) -> bool:
        """A restore (On) started within restore_guard_s: the dock icon's, or a second caller racing it."""
        snap = self.read_snapshot() if snap is None else snap
        at = self._epoch(str((snap or {}).get("restoring") or ""))
        return bool(at) and time.time() - at < self.restore_guard_s

    def begin_restore(self) -> bool:
        """Mark the snapshot as being restored (the dock icon calls this before it starts On). False = nothing to do:
        no Off in force, or a restore already running."""
        snap = self.read_snapshot()
        if not snap or self.restoring(snap):
            return False
        self._write_json(self.snapshot_path, dict(snap, restoring=zu._now()))
        return True

    def retire_snapshot(self, why: str) -> bool:
        """Move the Off snapshot aside (never deleted) and note why in the last report."""
        if not self.read_snapshot():
            return False
        try:
            self.snapshot_path.rename(self.snapshot_path.with_name(
                self.snapshot_path.name + ".cleared-" + time.strftime("%Y%m%d-%H%M%S")))
        except OSError:
            return False
        self._report({"action": "cleared", "ok": True, "ts": zu._now(), "why": why,
                      "headline": "%s is not in force: %s" % (self.switch_name, why)})
        return True

    def parked_by_off(self, unit: str) -> bool:
        snap = self.read_snapshot()
        if not snap:
            return False
        want = conf.full(unit)
        return any(u.get("unit") == want for u in snap.get("units") or []) or want in (snap.get("gpu_held") or [])

    # ---- stop / start ---------------------------------------------------------------------------------------
    @staticmethod
    def _by_scope(rows: Iterable[dict]) -> dict:
        out: dict = {"system": [], "user": []}
        for r in rows:
            out.setdefault(r["scope"], []).append(r["unit"])
        return out

    def _batch(self, verb: str, rows: list, run: Runner, timeout: float) -> dict:
        errs = {}
        for scope, units in self._by_scope(rows).items():
            if not units:
                continue
            rc, out, err = run(zu.sc(scope) + [verb] + units, timeout)
            if rc != 0:
                msg = (err or out).strip().splitlines()
                for u in units:
                    hit = [m for m in msg if u in m]
                    errs[u] = (hit[-1] if hit else (msg[-1] if msg else f"systemctl {verb} rc {rc}"))[:200]
        return errs

    def _why_text(self, scope: str, unit: str, err: str, run: Runner) -> str:
        low = err.lower()
        if "interactive authentication" in low or "access denied" in low or "not authorized" in low:
            return ("the panel may not " + ("stop" if "stop" in low else "start") + " system units without a "
                    "polkit grant for this user (see the README, 'System units')")
        if err:
            return err
        jr = ["journalctl", "--user"] if scope == "user" else ["journalctl"]
        rc, out, _ = run(jr + ["-u", unit, "-n", "1", "-o", "cat", "--no-pager"], 10.0)
        last = out.strip().splitlines()[-1] if rc == 0 and out.strip() else ""
        return ("last log: " + last[:180]) if last else "no reason given by systemd or the journal"

    def _settle(self, rows: list, want_on: bool, run: Runner, wait_s: float, sleep=time.sleep) -> dict:
        deadline = time.time() + wait_s
        final: dict = {}
        pending = list(rows)
        while True:
            for scope, units in self._by_scope(pending).items():
                if units:
                    for u, p in zu.show(scope, units, run, props=("Id", "ActiveState", "SubState")).items():
                        final[(scope, u)] = p

            def done(r):
                st = (final.get((r["scope"], r["unit"])) or {}).get("ActiveState", "unknown")
                return (st == "active") if want_on else (st in ("inactive", "failed"))
            pending = [r for r in pending if not done(r)]
            if not pending or time.time() >= deadline:
                return final
            sleep(1.0)

    def _cron_still_scheduled(self, run: Runner) -> list:
        """This install's cron lines (not services, so not stopped): named on the panel so nothing hides."""
        out = []
        if not self.cron_prefixes:
            return out
        rc, txt, _ = run(["crontab", "-l"], 10.0)
        if rc == 0:
            for ln in txt.splitlines():
                s = ln.strip()
                if s and not s.startswith("#") and "=" not in s.split()[0] and any(x in s for x in self.cron_prefixes):
                    out.append("crontab: " + s[:160])
        return out

    def refused(self, env=None) -> str:
        env = os.environ if env is None else env
        for k in self.refuse_env:
            if env.get(k):
                return f"{self.switch_name} / {self.on_name} is a person's switch on the Control Panel - a worker ({k}) may not flip it"
        return ""

    def all_off(self, run: Runner = zu.default_run, *, settle_s: float = 30.0, sleep=time.sleep,
                gpu: Optional[Callable[[], dict]] = None, gpu_policy: Optional[str] = None,
                held: Optional[Callable[[], list]] = None, env=None) -> dict:
        hw = house_refusal(env)
        if hw:
            return {"action": "off", "ok": False, "error": hw}          # nothing read, nothing written on the house
        why = self.refused(env)
        if why:
            return self._report({"action": "off", "ok": False, "error": why})
        gpu_policy = gpu_policy or ("park" if self.has_gpu() else "leave")
        if gpu_policy not in GPU_POLICIES:
            return self._report({"action": "off", "ok": False, "error": f"gpu_policy must be one of {GPU_POLICIES}"})
        if gpu_policy == "park" and not self.has_gpu() and gpu is None:
            gpu_policy = "leave"
        held = held or self.gpu_held
        p = self.plan(run)
        prev = self.read_snapshot()
        units = [{"unit": r["unit"], "scope": r["scope"], "sub": r["sub"], "restore": True} for r in p["stop"]]
        units += [{"unit": r["unit"], "scope": r["scope"], "sub": r["sub"], "restore": False,
                   "note": "a job mid-run - its timer runs it again"} for r in p["jobs"]]
        if prev:
            have = {(u["scope"], u["unit"]) for u in units}
            units = [u for u in prev.get("units") or [] if (u["scope"], u["unit"]) not in have] + units
        snap = {"schema": "crissa_os_off/v1", "ts": prev.get("ts") or zu._now(), "updated": zu._now(),
                "gpu_policy": prev.get("gpu_policy") or gpu_policy,
                "gpu_mode": prev.get("gpu_mode") or (self.gpu_return_mode() if self.has_gpu() else ""),
                "gpu_recipe": prev.get("gpu_recipe") or (self.gpu_recipe() if self.has_gpu() else None),
                "kept": [{"unit": u, "why": w} for u, w in self.keep.items()], "units": units,
                "gpu_held": prev.get("gpu_held") or []}
        self._write_json(self.snapshot_path, snap)

        tree = self.last_tree(p["stop"], run) if self.whole else set()
        last = [r for r in p["stop"] if r["unit"] in tree]
        targets = [r for r in p["stop"] if r["unit"] not in tree] + p["jobs"]
        if last:
            snap["last"] = [{"unit": r["unit"], "scope": r["scope"], "was": r["sub"]} for r in last]
            self._write_json(self.snapshot_path, snap)
        restarters = [r for r in targets if self.is_restarter(r["unit"])]
        errs = self._batch("stop", restarters, run, 120.0)
        gpu_step = None
        if gpu_policy == "park":
            park = list(self.gpu.get("park_args") or ["off"])
            gpu_step = gpu() if gpu else self._gpu_step(park, run)
            snap["gpu_held"] = sorted(set(snap["gpu_held"]) | set(held()))
            self._write_json(self.snapshot_path, snap)
        rest = [r for r in targets if not self.is_restarter(r["unit"]) and r["unit"] not in snap["gpu_held"]]
        errs.update(self._batch("stop", rest, run, 300.0))
        checked = restarters + rest
        final = self._settle(checked, False, run, settle_s, sleep)
        stopped, failed = [], []
        for r in checked:
            st = (final.get((r["scope"], r["unit"])) or {}).get("ActiveState", "unknown")
            if st in ("inactive", "failed"):
                stopped.append({"unit": r["unit"], "scope": r["scope"], "was": r["sub"]})
            else:
                failed.append({"unit": r["unit"], "scope": r["scope"], "state": st,
                               "why": self._why_text(r["scope"], r["unit"], errs.get(r["unit"], ""), run)})
        named = {f["unit"] for f in failed}
        for r in self.plan(run)["stop"]:
            if r["unit"] in named or r["unit"] in snap["gpu_held"] or r["unit"] in tree:
                continue
            failed.append({"unit": r["unit"], "scope": r["scope"], "state": r["active"],
                           "why": "is up after Off - something started it again (its Wants=, a timer, or a hand)"})
            stopped = [x for x in stopped if x["unit"] != r["unit"]]
        if gpu_step and gpu_step.get("rc") not in (0, None):
            failed.append({"unit": "GPU park (" + str(gpu_step.get("cmd")) + ")", "scope": "-",
                           "state": f"rc {gpu_step.get('rc')}",
                           "why": " | ".join(gpu_step.get("tail") or [])[-300:] or "the GPU park failed"})
        for r in p["unknown"]:
            failed.append({"unit": r["unit"], "scope": r["scope"], "state": "unknown",
                           "why": "systemd cannot be reached from the panel: " + (r.get("systemd_why") or "no user session")})
        paused = [x["unit"] for x in stopped] + [u for u in snap["gpu_held"] if u not in {x["unit"] for x in stopped}]
        rep = {"action": "off", "ok": not failed, "ts": zu._now(), "gpu_policy": snap["gpu_policy"],
               "stopped": stopped, "gpu_held": snap["gpu_held"], "paused": paused,
               "kept": [{"unit": r["unit"], "why": r["why"]} for r in p["keep"]] or
               [{"unit": u, "why": w, "note": "not installed as a unit here"} for u, w in self.keep.items()],
               "failed": failed, "already_off": [r["unit"] for r in p["already_off"]],
               "jobs_interrupted": [r["unit"] for r in p["jobs"]], "gpu": gpu_step,
               "return_mode": snap["gpu_mode"], "cron_still_scheduled": self._cron_still_scheduled(run),
               "addons": self._addon_units(p["stop"] + p["jobs"]), "whole": self.whole,
               "last": [{"unit": r["unit"], "scope": r["scope"], "after_s": self.panel_last_s,
                         "why": "the Control Panel and the launcher's pieces: stopped last, after this report"}
                        for r in last],
               "headline": self._headline_off(stopped, failed, snap["gpu_held"], last)}
        self._report(rep)
        if self.whole and not last and not self._can_schedule():
            # no service manager to hand the finish to (a Mac, a launcher by hand: no unit tree): the report is on disk,
            # so ask the launcher to quit from here; its pieces, the panel among them, go down with it
            return self.finish_off(run, settle_s=settle_s, sleep=sleep)
        if self.whole:                        # the report is on disk: now the tree, from outside every cgroup Off touches
            err = self._finish_later(run)
            if err:
                rep["failed"].append({"unit": ", ".join(r["unit"] for r in last) or "(the finish)", "scope": "user",
                                      "state": "active", "why": err})
                rep["ok"] = False
                rep["headline"] = self._headline_off(stopped, rep["failed"], snap["gpu_held"], last)
                self._report(rep)
        return rep

    def last_tree(self, rows: list, run: Runner) -> set:
        """The units that go down with the panel: the panel, every unit it is PartOf, every unit PartOf one of those
        (the launcher and its desk units, lib/crissa_service). Only units Off would stop."""
        on = {r["unit"]: r for r in rows}
        if self.self_unit not in on:
            return set()
        part = {}
        for scope, units in self._by_scope(rows).items():
            if units:
                for u, pr in zu.show(scope, units, run, props=("Id", "PartOf")).items():
                    part[u] = set(str(pr.get("PartOf") or "").split())
        tree = {self.self_unit} | (part.get(self.self_unit, set()) & set(on))
        grew = True
        while grew:
            add = {u for u, parents in part.items() if parents & tree} - tree
            grew = bool(add)
            tree |= add
        return tree

    @staticmethod
    def _addon_units(rows: list) -> dict:
        """{add-on id: [units]} among what Off stops (an add-on's gateways, dashboard, timers), so the report names them."""
        out: dict = {}
        for r in rows:
            why = str(r.get("why") or "")
            if why.startswith("add-on "):
                out.setdefault(why[len("add-on "):], []).append(r["unit"])
        return out

    @staticmethod
    def _can_schedule() -> bool:
        import shutil
        return bool(shutil.which("systemd-run")) and not test_refusal()

    def _finish_later(self, run: Runner) -> str:
        """Run `crissa-os-power finish` panel_last_s from now as a transient unit of the person's service manager, so it
        is in no cgroup the finish stops. '' = scheduled, else why not."""
        tool = Path(__file__).resolve().parent.parent / "scripts" / "crissa-os-power"
        argv = ["systemd-run", "--user", "--unit", "crissa-os-off-finish-%d" % int(time.time()), "--collect", "--quiet",
                "--on-active=%gs" % max(0.0, self.panel_last_s), sys.executable, str(tool), "finish"]
        for k in ("CRISSA_HOME", "CRISSA_PANEL_CONFIG", WHOLE_SWITCH):
            if os.environ.get(k):
                argv[2:2] = ["--setenv", "%s=%s" % (k, os.environ[k])]
        if run is zu.default_run:
            why = test_refusal()
            if why:
                return why
        rc, out, err = run(argv, 30.0)
        return "" if rc == 0 else ("the last stop (the Control Panel and the launcher) could not be scheduled: "
                                   + ((err or out).strip()[-200:] or "rc %d" % rc))

    def finish_off(self, run: Runner = zu.default_run, *, settle_s: float = 30.0, sleep=time.sleep,
                   quit_launcher: Optional[Callable[[], dict]] = None,
                   close_window: Optional[Callable[[], dict]] = None) -> dict:
        """The whole Off's last step (run by the timer _finish_later set): stop the tree left for last, verify it, ask a
        launcher run by hand to quit, close her browser, and complete the Off's report.

        HER BROWSER GOES TOO (the User 2026-11-01 ~08:15: "All OS OFF needs o wrap it all and close the Broswer to ensure
        nothing left, just as promised on th ebutton text. It worked in prod tests"): the launcher's own stop closes her
        instance (scripts/crissa.py close_her_window -> lib/crissa_window.close_own, .101 round 11 HH and round 14,
        2026-10-12), and her scope is PartOf the launcher. The finish then runs the same close_own once more as the sweep:
        only her profile's instance, never the person's own browser, a no-op when she is already gone."""
        why = house_refusal()
        if why:
            return {"action": "finish", "ok": False, "error": why}
        snap = self.read_snapshot()
        rep = self._read_json(self.report_path)
        if not snap or rep.get("action") != "off":
            return {"action": "finish", "ok": False, "error": "no All OS Off in progress"}
        last = [dict(r, sub=r.get("was", "")) for r in snap.get("last") or []]
        errs = self._batch("stop", last, run, 300.0) if last else {}
        final = self._settle(last, False, run, settle_s, sleep) if last else {}
        for r in last:
            st = (final.get((r["scope"], r["unit"])) or {}).get("ActiveState", "unknown")
            if st in ("inactive", "failed"):
                rep.setdefault("stopped", []).append({"unit": r["unit"], "scope": r["scope"], "was": r.get("was", ""),
                                                      "last": True})
            else:
                rep.setdefault("failed", []).append({"unit": r["unit"], "scope": r["scope"], "state": st,
                                                     "why": self._why_text(r["scope"], r["unit"], errs.get(r["unit"], ""), run)})
        try:
            q = quit_launcher() if quit_launcher else self._quit_launcher()
        except Exception as e:  # noqa: BLE001
            q = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        try:
            w = close_window() if close_window else self._close_her_browser()
        except Exception as e:  # noqa: BLE001
            w = {"ok": False, "how": ["%s: %s" % (type(e).__name__, e)], "left": []}
        if w.get("left"):
            rep.setdefault("failed", []).append({"unit": "her browser", "scope": "-", "state": "running",
                                                 "why": "still up after the close (pids %s)" % w["left"]})
        rep.update(ok=not rep.get("failed"), last_done=zu._now(), launcher_quit=q, browser=w, last=[])
        rep["headline"] = self._headline_off(rep.get("stopped") or [], rep.get("failed") or [], rep.get("gpu_held") or [])
        return self._report(rep)

    @staticmethod
    def _close_her_browser() -> dict:
        """Her own browser instance (her profile only), the launcher stop's own close (lib/crissa_window.close_own)."""
        try:
            import crissa_window
        except ImportError:
            return {"ok": True, "how": ["no window module here"], "left": []}
        return crissa_window.close_own()

    @staticmethod
    def _quit_launcher() -> dict:
        """A launcher run by hand (no units) is asked to quit, the way the Wall's menu stop does (crissa_procs)."""
        try:
            import crissa_home
            import crissa_procs
        except ImportError:
            return {"ok": True, "note": "no launcher record here"}
        rec = crissa_procs.load(crissa_home.HOME)
        alive, _why = crissa_procs.launcher_alive(rec)
        if not alive:
            return {"ok": True, "note": "no launcher running"}
        return crissa_procs.request_quit(crissa_home.HOME, by="All OS Off")

    def _headline_off(self, stopped: list, failed: list, held: Iterable = (), last: Iterable = ()) -> str:
        held, last = list(held), list(last)
        gpu = f" + {len(held)} parked by the GPU step" if held else ""
        if failed:
            names = ", ".join(f["unit"] for f in failed[:4]) + (" ..." if len(failed) > 4 else "")
            return f"{self.switch_name}: {len(stopped)} stopped{gpu}, {len(failed)} did NOT stop: {names}"
        if self.whole:
            tail = (f"; the Control Panel stops in {self.panel_last_s:g} s" if last else "") + \
                " - start Crissa again with her dock icon"
            return f"{self.switch_name}: {len(stopped)} services stopped{gpu}{tail}"
        return f"{self.switch_name}: {len(stopped)} services stopped{gpu}; only the Control Panel is on"

    def all_on(self, run: Runner = zu.default_run, *, mode: Optional[str] = None, settle_s: float = 90.0,
               sleep=time.sleep, gpu: Optional[Callable[[list], dict]] = None, run_gpu: bool = True, env=None) -> dict:
        hw = house_refusal(env)
        if hw:
            return {"action": "on", "ok": False, "error": hw}          # nothing read, nothing written on the house
        why = self.refused(env)
        if why:
            return self._report({"action": "on", "ok": False, "error": why})
        snap = self.read_snapshot()
        notes = []
        gpu_step, args = None, []
        policy = (snap.get("gpu_policy") if snap else ("park" if self.has_gpu() else "leave")) or "leave"
        if self.has_gpu() or gpu is not None:
            want = mode or (snap.get("gpu_mode") if snap else "") or self.gpu.get("default_mode") or "normal"
            args, note = self.mode_args(want, snap.get("gpu_recipe") if snap else None)
            if note:
                notes.append(note)
            if run_gpu and policy == "park":
                gpu_step = gpu(args) if gpu else self._gpu_step(args, run)
        if not snap:
            rep = {"action": "on", "ok": bool(gpu_step is None or gpu_step.get("rc") == 0), "ts": zu._now(),
                   "back": [], "failed": [], "extra": [], "gpu": gpu_step,
                   "notes": notes + [f"no {self.switch_name} snapshot on record - nothing to restore"
                                     + (" beyond the GPU" if gpu_step else "")],
                   "headline": f"{self.on_name}: no snapshot - nothing recorded to restore"}
            return self._report(rep)
        if not snap.get("restoring"):
            self._write_json(self.snapshot_path, dict(snap, restoring=zu._now()))
        held = set(snap.get("gpu_held") or []) if policy == "park" else set()
        restore = [{"unit": u["unit"], "scope": u["scope"], "sub": u.get("sub", "")}
                   for u in snap.get("units") or [] if u.get("restore", True) and u["unit"] not in self.keep]
        by_hand = [r for r in restore if r["unit"] not in held]
        skipped = [u["unit"] for u in snap.get("units") or [] if not u.get("restore", True)]
        first = [r for r in by_hand if not self.is_restarter(r["unit"])]
        last = [r for r in by_hand if self.is_restarter(r["unit"])]
        errs = self._batch("start", [r for r in first if r["scope"] == "system"], run, 300.0)
        errs.update(self._batch("start", [r for r in first if r["scope"] != "system"], run, 300.0))
        errs.update(self._batch("start", last, run, 120.0))
        final = self._settle(by_hand, True, run, settle_s, sleep)
        back, failed = [], []
        for r in by_hand:
            st = (final.get((r["scope"], r["unit"])) or {}).get("ActiveState", "unknown")
            if st == "active":
                back.append({"unit": r["unit"], "scope": r["scope"]})
            else:
                failed.append({"unit": r["unit"], "scope": r["scope"], "state": st,
                               "why": self._why_text(r["scope"], r["unit"], errs.get(r["unit"], ""), run)})
        had = {(u["scope"], u["unit"]) for u in snap.get("units") or []} | {("user", u) for u in self.keep}
        extra = [r["unit"] for r in self.plan(run)["stop"] if (r["scope"], r["unit"]) not in had]
        if gpu_step and gpu_step.get("rc") not in (0, None):
            failed.append({"unit": "GPU restore (" + str(gpu_step.get("cmd")) + ")", "scope": "-",
                           "state": f"rc {gpu_step.get('rc')}",
                           "why": " | ".join(gpu_step.get("tail") or [])[-300:] or "the GPU restore failed"})
        snap2 = dict(snap, restored_ts=zu._now(), restore_failed=[f["unit"] for f in failed])
        snap2.pop("restoring", None)
        if failed:
            snap2.pop("restored_ts")
        self._write_json(self.snapshot_path, snap2)
        if not failed:
            try:
                self.snapshot_path.rename(self.snapshot_path.with_name(
                    self.snapshot_path.name + ".restored-" + time.strftime("%Y%m%d-%H%M%S")))
            except OSError:
                pass
        head = (f"{self.on_name}: {len(back)} of {len(by_hand)} back" + (f" + GPU {' '.join(args)}" if gpu_step else "")
                + (f"; did NOT come back: {', '.join(f['unit'] for f in failed[:4])}" if failed else " - exactly what was on"))
        rep = {"action": "on", "ok": not failed, "ts": zu._now(), "back": back, "failed": failed, "extra": extra,
               "not_rerun_jobs": skipped, "gpu": gpu_step, "gpu_held": sorted(held), "notes": notes, "headline": head}
        return self._report(rep)

    def _report(self, rep: dict) -> dict:
        try:
            self._write_json(self.report_path, rep)
        except OSError:
            pass
        return rep

    def summary(self) -> dict:
        """Cheap (no systemctl): what the header needs on every poll."""
        snap = self.read_snapshot()
        if not snap:
            return {"in_force": False}
        paused = [u["unit"] for u in snap.get("units") or []]
        for u in snap.get("gpu_held") or []:
            if u not in paused:
                paused.append(u)
        last = self._read_json(self.report_path)
        return {"in_force": True, "since": snap.get("ts"), "paused": paused, "n": len(paused),
                "return_mode": snap.get("gpu_mode") or "", "kept": [k["unit"] for k in snap.get("kept") or []],
                "failed": [f["unit"] for f in (last.get("failed") or [])] if last.get("action") == "off" else []}

    def panel_view(self, run: Runner = zu.default_run, plan: Optional[dict] = None) -> dict:
        """The panel's Off/On card. `plan` is a plan already made (the panel server caches one, round 13); None makes
        one now."""
        snap = self.read_snapshot()
        view = {"ok": True, "in_force": bool(snap), "last": self._read_json(self.report_path),
                "return_mode": self.gpu_return_mode() if self.has_gpu() else "",
                "keep": [{"unit": u, "why": w} for u, w in self.keep.items()],
                "switch_name": self.switch_name, "on_name": self.on_name}
        if snap:
            view["snapshot"] = {"ts": snap.get("ts"), "gpu_mode": snap.get("gpu_mode"),
                                "gpu_held": snap.get("gpu_held") or [],
                                "units": [u["unit"] for u in snap.get("units") or []],
                                "restore_failed": snap.get("restore_failed") or []}
        try:
            p = plan if plan is not None else self.plan(run)
            view["would_stop"] = [r["unit"] for r in p["stop"] + p["jobs"]]
            view["already_off"] = len(p["already_off"])
            view["units"] = len(p["units"])
            view["unknown"] = [r["unit"] for r in p["unknown"]]
        except Exception as e:  # noqa: BLE001 - never 500 the panel
            view["plan_error"] = f"{type(e).__name__}: {e}"[:160]
        return view


def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="crissa-os-power", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan", help="what Off would stop and keep, and why each unit counts (read-only)")
    off = sub.add_parser("off")
    off.add_argument("--gpu", choices=GPU_POLICIES, default=None,
                     help="park: the GPU step runs (when configured); leave: every unit stops like any other")
    on = sub.add_parser("on")
    on.add_argument("--mode", default=None, help="GPU mode to come back to (default: the one before Off)")
    sub.add_parser("status")
    sub.add_parser("finish", help="the whole Off's last step (its timer runs it): the Control Panel and the launcher")
    a = ap.parse_args(argv)
    pw = Power()
    if a.cmd == "plan":
        p = pw.plan()
        for r in p["stop"] + p["jobs"]:
            print(f"stop  {r['scope']:6} {r['unit']:44} {r['sub']:9} {r['why']}")
        for r in p["keep"]:
            print(f"keep  {r['scope']:6} {r['unit']:44} {r['sub']:9} {r['why']}")
        for r in p["unknown"]:
            print(f"?     {r['scope']:6} {r['unit']:44} {'':9} systemd unreachable: {r.get('systemd_why', '')}")
        print(f"{len(p['stop']) + len(p['jobs'])} to stop, {len(p['keep'])} kept, "
              f"{len(p['already_off'])} already off, {len(p['units'])} units in all")
        return 0
    if a.cmd == "status":
        print(json.dumps(pw.panel_view(), indent=1))
        return 0
    if a.cmd == "finish":
        rep = pw.finish_off()
        print(rep.get("headline") or rep.get("error"))
        return 0 if rep.get("ok") else 1
    rep = pw.all_off(gpu_policy=a.gpu) if a.cmd == "off" else pw.all_on(mode=a.mode)
    print(rep.get("headline") or rep.get("error"))
    for f in rep.get("failed") or []:
        print(f"  NOT {('stopped' if a.cmd == 'off' else 'back')}: {f['unit']} ({f['state']}) - {f['why']}")
    for k in rep.get("kept") or []:
        print(f"  left on: {k['unit']} - {k['why']}")
    for e in rep.get("extra") or []:
        print(f"  also came up (not in the snapshot): {e}")
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
