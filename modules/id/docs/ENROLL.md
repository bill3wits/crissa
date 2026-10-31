# House enroll — portable identity init

**Purpose:** One durable entry path so a **user** (or agent) can stand up identity in **this office** or **another office**: family voice prints, person folders, empty-room reference frames.

**Not** presence inventory. **Not** training. Writes only under `HOUSE_ID_ROOT` (default: this repo).

**Voice register (wall):** Control Panel → Register an ID → `/?init=register`. Announcer greets by name. Usual seat, on then off. Appends the bank. Say next / again / done / desk / door / mic.

```bash
export HOUSE_ID_ROOT=<identity>   # or clone path on another machine
<identity>/modules/id/scripts/house-enroll doctor
<identity>/modules/id/scripts/house-enroll status
```

---

## Master init — **User only**, then on-demand Regulars

Initialization enrolls the **primary User** (`site.enroll.user_id`, e.g. `<user_id>`) at the desk.
It does **not** walk the whole family. After User is done:

> Inspector would be happy to take it from here — learning you from the room over time.
> If you would like to manually register another family member now, we can ensure the
> highest quality recordings.

**Add Regular Now?** (TTY prompt, or flags). Anytime later:

```bash
modules/id/scripts/house-init                                    # User init + handoff offer
modules/id/scripts/house-init --from user                        # re-do / resume User
modules/id/scripts/house-init --no-add-regular                   # handoff, skip offer
modules/id/scripts/house-enroll add-regular --id <regular_id> --display <Regular>
modules/id/scripts/house-init --from room-refs                   # empty-room refs when ready
```

Phases: **site → doctor → status → legend → people → user → handoff → room-refs → learn-window → done**  
State: `site/init-state.json` · **`site/learn-window.json`** (new office continued crons)

### New-office learning window (after User handoff)

A new User location is a **new visual/audio environment**. Init should arm a **continued learn period** (default **14 days**), not one-shot enroll:

| While learning | After window |
|----------------|--------------|
| Door-exit train full night window (e.g. 18:00–02:00) | Short maintain window (e.g. 18:00–20:30) if student ships well |
| Nightly empty room-refs | Same, lower urgency |
| Gold / bootcamp harvest on | Keep light auto-label |

Writes `site/learn-window.json` (`days`, `door_exit_train.until`, `after_days.until`).  
Ops host installs/adjusts `/etc/cron.d/the pack-qwen-door-exit` `TRAIN_UNTIL` from that file (Grok wire — see `docs/PLAN-GYM-RETURN-E2E.md` §C).

Home office already adapted: set `"days": 0` or skip phase.

## New office checklist

1. Copy / clone `house-id` (biometrics stay private).
2. `modules/id/scripts/house-init` (or `init-site` then edit **`site.json`**):
   - `audio.mic` — `pactl list short sources` (raw door mic, not echocancel)
   - `audio.sink` — room speakers (or null = Pulse default)
   - `rails.presence` / `rails.camera` — this host’s HTTP ports
   - `room_refs.stops_json` — pan/tilt stop list for **this** room’s camera
   - `room_refs.dir` — where empty frames land (default `site/reference-frames/`)
3. Doctor runs inside master init; fix anything red and re-run.
4. `person add` is available anytime; master ensures folders for roster rows.
5. Voice queue walks missing banks (one person per `house-init` by default).
6. When voice queue empty and room empty → room-refs phase.

Rails load banks from `people/*/face` and `people/*/voice` via `HOUSE_ID_ROOT` — no Crissa imports.

---

## Audio cue language (fixed forever)

| Sound | Meaning |
|--------|---------|
| **Long mid beep** | **GO** — speak when the tone **ends** |
| **Short high chirp** | **STOP** — record window closed |
| **Two tones** (low → high) | **Session over** |

**Seat + screen (the User 2026-08-01):**

| Who | Where | Screen |
|-----|--------|--------|
| **User** only (`site.enroll.user_id`) | **Desk** (monitor) | Coach UI optional |
| **Regulars** (family, …) | **Usual seat** in the room | **No** desk monitor — loud voice only |

On/off angles are from that place (face cam vs turn **in the seat**).  
Voice order: instruction → One, Two, Three → sentence → beep → they speak.  
Default line: *The quick brown fox jumps over the lazy dog.*

| URL | What |
|-----|------|
| **http://127.0.0.1:8190/panel** | Live Control Panel (House \| Work) — **product** |
| **http://127.0.0.1:8195/** | Enroll coach display (optional) — start with `house-enroll coach` |

Spoken coach (Cosy `:7789`, else `spd-say`) leads every take. Screen is optional.

Demo only:

```bash
modules/id/scripts/house-enroll legend
```

---

## Desk-first User init (default)

**Initial enroll always at the desk** — camera aims desk (this office pan≈0), not door rest.

| # | Pose | Voice line (default) | Camera |
|---|------|----------------------|--------|
| 1 | **Front** at desk | “My name is {Name}, and this is my voice for the house.” | facefind + emb |
| 2 | **Profile** 45–90° still at desk | “The camera can see me from this angle while I speak.” | facefind + emb |
| 3 | **Front** again | “One two three four five — room presence voice enroll.” | facefind + emb |

**Camera path — which face?** Enrollee is at the **desk** (or station). Never FF first.

```
1. Point camera at station (desk pan)     ← FOV = who we mean
2. Call facefinder (bounded ON)          ← locks that face
3. Snapshot + face emb
4. Facefinder OFF → door home
```

Rails: `POST :8192/v1/facefind` (same order inside grabber).  
Fallback: `/v1/look_around` one stop, then DIY aim→confirm pan→FF→snap.

**Hypothesis (test with a regular):** desk bank may be enough for her usual place; only run `--plan room` later if match fails off-desk.

### Coach display (User init GUI)

Browser on **`http://127.0.0.1:8195/`** — professional identity init surface (not rail-stt).  
Announcer + on-screen coach share the same lines. **Long beep → big GO!**; paragraph card for read-aloud; short chirp = stop.

```bash
modules/id/scripts/house-enroll voice <regular_id>              # desk + face + coach (default)
modules/id/scripts/house-enroll face <regular_id>               # face snaps only (backfill)
modules/id/scripts/house-enroll voice <regular_id> --plan room  # optional usual-place pass
modules/id/scripts/house-enroll voice <user_id> --only 2 --replace
# Live-mic re-enroll (Fable P0 2026-08-01 — fix channel mismatch, keep ECAPA)
modules/id/scripts/house-enroll voice <regular_id> --plan paragraph
modules/id/scripts/house-enroll voice <user_id> --plan paragraph
modules/id/scripts/house-enroll family
```

### Sectioned quality path (User & family)

**Claude designs** coach visuals; **Grok wires** sections/CLI.  
Prompt: `docs/PROMPT-CLAUDE-ENROLL-UX.md`.

| Section | What |
|---------|------|
| **1 · Face** | Aim **sit beam** (`enroll.sit_station` = desk) · front then profile · **quality gate** (det_score + emb) · retry if weak |
| **Bridge** | Spoken + on-screen: **“Register voice section is next.”** |
| **2 · Voice** | Full paragraph · ON then OFF · beep → **GO!** · normal cadence · ≈22 s |
| **Rest** | Camera returns to **ingress** (`enroll.rest_station` = door, pan≈450000) |

Camera law (this office — `<presence data>/camera-stops.json`):

| Aim | Stop id | Pan (approx) | Meaning |
|-----|---------|--------------|---------|
| **Sitting beam** | `desk` | **0** (factory forward) | Enrollee at desk / the User's seat |
| **Rest / ingress** | `door` | **450000** | Door FOV home — never leave cam on desk after enroll |

Encoder stays **ECAPA** (voice P0); bank = room/beam mic.

```bash
modules/id/scripts/house-enroll voice <regular_id>              # default: sectioned quality (live)
modules/id/scripts/house-enroll voice <regular_id> --plan quality
modules/id/scripts/house-enroll voice <regular_id> --no-voice   # face section only
modules/id/scripts/house-enroll add-regular --id <regular_id>   # same sectioned path
modules/id/scripts/house-enroll family --ids <regular_id> <user_id>
modules/id/scripts/house-enroll voice <user_id> --legacy-voice --plan desk   # old interleaved path
```

### Import Fable-prepped clips (primary re-enroll wire)

Fable drops **room/beam** WAVs; Grok embeds ECAPA into the bank (no coach required).

```text
site/reenroll-inbox/
  <regular_id>/01_on.wav  02_off.wav
  <user_id>/01_on.wav      02_off.wav
```

```bash
modules/id/scripts/house-enroll import-clips --scaffold          # dirs + README for Fable
modules/id/scripts/house-enroll import-clips --replace --verify  # after clips land
modules/id/scripts/house-enroll import-clips --person <regular_id> --replace
sudo systemctl restart rail-stt                     # reload bank in ear
```

Writes:

```text
people/<id>/voice/001.json              # ECAPA + station/pose + face_json link
people/<id>/voice/enroll_*.wav
people/<id>/face/enroll_desk_001.json   # face when embed works
people/<id>/face/refs/enroll_desk_001.jpg
people/<id>/face/refs/enroll_desk_001.orientation.json  # house_added, no HFLIP
```

After desk session the camera **returns to door home**. Weak audio is rejected; chirp still fires.

---

## Commands

| Command | Does |
|---------|------|
| **`init` / `house-init`** | **User-only** init → Inspector handoff → optional Add Regular |
| **`add-regular`** | On-demand high-quality desk enroll for family/office mate |
| `init-site` | `site.example.json` → `site.json` |
| `doctor` | mic, sink, Cosy, ECAPA, presence, camera, stops |
| `status` | roster face/voice counts + peaks |
| `legend` | speak + demo tones |
| `person add ID [--display Name]` | scaffold `face/` + `voice/` + index |
| `voice ID [--only N] [--replace] [--quiet]` | ECAPA enroll |
| `family [--min-takes 1] [--ids …]` | queue missing voice banks |
| `room-refs [--force]` | empty multi-stop stills → `site/reference-frames/` |

Python (same CLI):

```bash
cd <identity>/tools
<the id env's python> -m house_enroll status
```

ECAPA needs speechbrain (`<the id env>` on the home machine). Override: `HOUSE_ENROLL_PYTHON=/path/to/python`.

---

## Layout

```text
house-id/
  site.json              # THIS office (devices + URLs)
  site.example.json      # template for the next office
  site/reference-frames/ # empty-room stills for THIS site
  people/
    _index.json
    <id>/
      person.json
      face/              # face / refs (sibling of voice)
      voice/             # ECAPA bank
  crissa_id/
    house-enroll         # bash entry
    house_enroll/        # package
    ecapa_embed.py
    voice_enroll_room.py # thin compat wrapper
  ENROLL.md              # this file
```

---

## Env overrides

| Env | Role |
|-----|------|
| `HOUSE_ID_ROOT` | Identity tree root |
| `HOUSE_ENROLL_MIC` | Override `site.audio.mic` |
| `HOUSE_ENROLL_SINK` / `VOICE_SINK` | Speakers |
| `HOUSE_ENROLL_QUIET=1` | Tones only, no TTS |
| `COSY_URL` | Spoken coach endpoint |
| `HOUSE_ENROLL_PYTHON` | Interpreter for `house-enroll` |

---

## Face refs (today)

Face banks already live under `people/<id>/face/`. Door orientation law: `REFS-ORIENTATION.md`.  
Live **face snap enroll** from the door camera can plug into this CLI later as `house-enroll face <id>`; until then, drop refs under `face/refs/` and embed with the existing presence pipeline.

Room empty refs for a **new office** always go through `room-refs` + that office’s `stops_json`, not a museum path.
