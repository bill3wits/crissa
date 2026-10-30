# Camera orientation (true-handed) — hardcoded

**2026-08-11:** Door rest check (HFLIP on): **dresser + microwave LEFT**, **macrame RIGHT**.  
Grabber **always** writes true-handed JPEGs (product default `RAIL_CAMERA_HFLIP=1`).

**2026-08-18:** `TRAIN_HFLIP` / `QWEN_HFLIP` / `DOOR_HFLIP` default **off**.
A second flip on grabber JPEGs records the room backwards. Opt-in only for
pre-grabber camera_raw packs.

| Layer | Rule |
|-------|------|
| Grabber | HFLIP at encode — only place that flips |
| Door packs | Stamp `orientation=true_handed` (train contract) |
| Face enroll | No per-ref `orientation.json` sidecars |
| Site SoT | `house-id/site.json` → `camera.orientation=true_handed` |

Quarantined slop: `people/_quarantine-orientation-slop-20260811/`  
(`*.orientation.json`, `*_flip.json` emb duals).

Emergency only: `RAIL_CAMERA_HFLIP=0` → camera_raw (do not train direction).
