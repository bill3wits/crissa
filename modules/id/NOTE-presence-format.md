# NOTE: the on-disk formats the id module writes, for modules/presence (2026-10-09)

Written by the id agent of the Crissa parity build, for the presence agent and whoever merges. Decisions
made overnight without the User; say so if one is wrong.

## One identity store, the house's layout

`crissa_id.paths.identity_root()` = `CRISSA_IDENTITY`, else `HOUSE_ID_ROOT`, else `<state home>/identity` -
the same directory `crissa_presence/paths.py` names as `IDENTITY`. Importing `crissa_id` exports `HOUSE_ID_ROOT`
into the process when unset, so `lib/house_user.py` and every rail reader land on the same tree.

    people/_index.json                 {"schema": "house_people/v1", "people": [{"id", "display", "active", "role"}]}
    people/<id>/person.json            {"id", "display", "active": true, "role": "user"|"regular"|"guest"[, "tier", "gender"]}
    people/<id>/face/<prefix>_NNN.json rail-presence's record (enrollments._encoding reads `encoding`|`embedding`):
                                       {"person_id", "modality": "face", "model": "auraface-v1", "dim": 512,
                                        "encoding": [512 floats, unit length], "det_score", "bbox", "yaw", "bin",
                                        "kps": [[x,y] x5], "source", "station", "pose", "ts", "ts_iso", "ok": true, ...}
    people/<id>/face/refs/<prefix>_NNN.jpg   the still the print came from (this machine only; never shipped)
    people/<id>/voice/ws_enroll_<ts>_<n>.json {"person_id", "modality": "voice", "model": "wespeaker/english",
                                               "dim": 256, "encoding": [256 floats], ...} (rail-stt's voice_bank
                                               filters by the `model` tag: wespeaker vs ecapa never mix)
    site.json                          house_site/v1 (modules/id/config/site.example.json is the template)
    site/init-wizard.json              Init v1's running state (house_init_wizard/v1)
    site/init-state.json               the master init phases (house_init_state/v1)
    site/first-boot.json               {"values_at": ...} once the first values are written

The face `model` tag is `auraface-v1` = `crissa_presence/face_engine.FAMILY`, the same 512-number space, so
`enrollments._same_family` accepts these prints. A store minted on the house (the other detector family) is
never read here, as presence already rules.

## The player's own records (what lib/crissa_presence, modules/oracle and the Wall read)

Kept byte-compatible with the retired stand-in walker:

    <state home>/init.json    {"schema": "crissa-init/1", "done", "at", "version", "steps_done", "service"[, "cancelled_at"]}
    <state home>/user.json    {"schema": "crissa-user/1", "name", "person_id", "role": "user", "at"}
    <state home>/enroll.json  {"schema": "crissa-enroll/1", "owner", "faces": [[512 floats], ...], "updated_at"}  mode 0600

`crissa_id.pack_store` writes them: the marker when a walk ends, user.json when the roster names the User
(`_write_roster` / Init v2 `set_name`), enroll.json beside every face print Register stores for the primary User
(`face_snap.capture_face` -> `pack_store.add_face`; presence's `enroll_store` reads the same keys).

## Two copies of house_enroll: a merge item

`modules/presence/crissa_presence/enroll_panel/` is presence's vendored copy of house-id's `house_enroll`
(its `scripts/crissa-enroll.py` serves the coach on 8196 from it). `modules/id/crissa_id/house_enroll/` is the id
module's copy, with the seams this brief asked for (the face engine child is `python -m crissa_id.face_engine`,
the identity root from the state home, the player's `/v1/speak` for the coach's lines, `pack_store` beside the
prints). Proposal: presence's `enroll_panel/*.py` become one-line re-exports of `crissa_id.house_enroll.*` once
both land, so the coach the launcher starts runs the id module's code; until then the two copies agree on the
store layout above and on the engine family, and differ in those seams.

## Ports the id module reads (lib/crissa_ports numbers, a variable wins)

player 8100 (speak, the conductor's cues), presence 8219, camera 8192, enroll 8196, stt 8190, ear-bridge 8194.
`RAIL_PRESENCE_URL` / `RAIL_CAMERA_URL` / `HOUSE_ENROLL_COACH_URL` / `VO_HOME_URL` / `HOUSE_CONDUCTOR_URL` still win.
