# Face / still orientation law (the User)

**One flip, at the grabber.** `RAIL_CAMERA_HFLIP=1` writes true-handed JPEGs.
`GET :8192/snapshot` and the ring buffer are **already** dresser-LEFT.

| Feed | Dresser / microwave |
|------|---------------------|
| USB raw (inside grabber only) | RIGHT — never expose this |
| `:8192/snapshot` / buffer / saved stills | **LEFT** ← truth |
| Door posters | NIRVANA / THE SMITHS read **forward** |

## Never flip a grabber JPEG

A second `cv2.flip` / `TRAIN_HFLIP=1` / `QWEN_HFLIP=1` records the room
backwards and trains models on a mirror. Defaults for those flags are **off**.
`--hflip` / `TRAIN_HFLIP=1` is only for **pre-2026-08-11 camera_raw** packs
that have no `.true_handed` / `ORIENTATION` marker.

## Time

Frames are seq-ascending, older→newer. Never reverse the film.

## 2026-08-18 correction

Museum desk shots `a regular_2..5` were mirrored (monitor text read
`ITAMAH IMAR` = RAMI HAMATI). Flipped in place; encodings refreshed.
`a regular_1` (phone, shirt text forward) and grabber enrolls
(`enroll_desk_*`, `enroll_ponytail_001`) were already true-handed.

Updated: 2026-08-18
