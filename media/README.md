# media/ — optional exercise reference images

Drop an image here named after the exercise's **`id`** and it shows up inline at
the top of that exercise's guided session (iTerm2 only — silently skipped in
other terminals).

## Naming

`media/<exercise_id>.<ext>` — where `<ext>` is one of: `png`, `jpg`, `jpeg`,
`gif`, `webp` (first match wins, in that order).

The `id` is the first field of each entry in `../exercises.json`. Current ids:

| id | exercise |
| --- | --- |
| `single_leg_mini_squat` | Single-leg mini squats |
| `stealth_hip_flexor` | Standing 'stealth' hip-flexor stretch |
| `single_leg_board` | Single-leg stand on balance board |
| `pvc_pass_throughs` | PVC shoulder pass-throughs |
| `ulnar_nerve_glide` | Ulnar nerve glide |
| `wall_sit` | Wall sit |
| `standing_hip_cars` | Standing hip CARs |
| `board_weight_shifts` | Balance-board weight shifts |
| `pvc_overhead_side_bend` | PVC overhead reach + side bend |
| `gyro_ball_forearm` | Gyro ball forearm spin |
| `ext_rotated_half_squats` | Externally-rotated half squats |
| `standing_figure4` | Standing figure-4 hip stretch |
| `two_foot_board_hold` | Two-foot balance-board hold |
| `board_calf_stretch` | Balance-board calf stretch |
| `pvc_thoracic_rotation` | PVC thoracic rotations |

Example: `media/ulnar_nerve_glide.png` → shown during the ulnar nerve glide.

## Note

Image files (`*.png`, `*.jpg`, etc.) are **gitignored** — they stay local and are
never committed (keeps the public repo free of copyrighted reference images).
This README is the only tracked file in here.
