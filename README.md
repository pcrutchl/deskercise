# Deskercise

Hourly macOS nudges to do knee-stability, hip-mobility, balance, upper-body, and
hand/forearm work without leaving your desk. Native notifications (clickable),
guided visual countdowns, and a log so you can prove to yourself you did them.

Built around a specific setup: powered standing desk, FluidStance balance board,
7' PVC pipe, and a gyro-ball forearm exerciser — with a cyclist's needs in mind
(knee stabilizers, hip flexibility, and cyclist's-palsy / ulnar-nerve relief).

## How it works

- A **launchd agent** runs `deskercise notify` at the top of each hour, 9:00–17:00,
  weekdays (all configurable in `config.json`).
- `notify` picks the next exercise in a **deterministic rotation** that cycles
  evenly through knee → hip → balance → upper-body → hand/forearm, and fires a
  clickable macOS notification via `terminal-notifier`.
- **Clicking the notification** opens a Terminal window with the guided session:
  the instructions, then a **visual countdown** for each timed hold (with discrete
  sub-timers, e.g. "palm down 15s → palm up 15s → thumb up 15s"), or a
  press-Enter-when-done prompt for rep-based moves.
- Finishing logs a completion; `stats` shows today's tally, your streak, and
  lifetime total. Ignoring a notification simply logs nothing — no guilt-tracking.

## Setup

```sh
brew install terminal-notifier      # clickable notifications
./deskercise install                # generate + load the launchd agent
```

Then, **one-time macOS setup** so the nudges are hard to ignore:

> System Settings → Notifications → **terminal-notifier**
> - Allow Notifications: **ON**
> - Alert style: **Alerts** (not Banners — Alerts stay on screen until dismissed)

Test it immediately:

```sh
./deskercise notify     # fires a notification now; click it to run the session
```

## Commands

| Command | What it does |
| --- | --- |
| `./deskercise notify` | Fire the next notification now (what the agent runs hourly). |
| `./deskercise session` | Run the guided session for the pending exercise. |
| `./deskercise session --id wall_sit` | Run a specific exercise on demand. |
| `./deskercise next` | Show what's next without advancing the rotation. |
| `./deskercise list` | List the full rotation. |
| `./deskercise done [--id ID]` | Log a completion manually (no timer). |
| `./deskercise stats` | Today's count, current streak, lifetime total. |
| `./deskercise recent [N]` | Show the last N log entries. |
| `./deskercise install` | (Re)generate and load the launchd agent from `config.json`. |
| `./deskercise uninstall` | Unload and remove the agent (keeps your log). |
| `./deskercise doctor` | Check dependencies, agent status, and next fire times. |

## Configuration

Edit `config.json`, then re-run `./deskercise install` to apply:

```json
{
  "hours": [9, 10, 11, 12, 13, 14, 15, 16, 17],
  "weekdays": [1, 2, 3, 4, 5],
  "minute": 0,
  "sound": "Ping",
  "title": "Deskercise",
  "state_dir": "~/.deskercise"
}
```

- `weekdays` uses launchd's convention: `0`/`7` = Sunday, `1` = Monday … `6` = Saturday.
- Runtime state (rotation position + `log.csv`) lives in `state_dir`, outside the repo.

## Editing the exercises

`exercises.json` is the whole library. Each entry has a `cue` (the instructions),
optional `equipment` and `note`, a `work_friendly` flag, and `segments`. A segment
is one of:

```jsonc
{"label": "Hold", "seconds": 45}                       // single timed hold
{"label": "Slow reps", "reps": 12}                     // press-Enter-when-done
{"label": "Right hand", "steps": [                     // sequence of sub-timers
  {"cue": "palm down", "seconds": 15},
  {"cue": "palm up",   "seconds": 15}
]}
```

The rotation is just the file order, laid out to interleave categories. Add or
reorder freely.

## Uninstall

```sh
./deskercise uninstall              # removes the agent, keeps your log
brew uninstall terminal-notifier    # optional
```

## Notes

- Not medical advice. The exercises are grounded in PT/sports-medicine guidance,
  but if knee/nerve symptoms persist, worsen, or come with weakness, see a
  professional — persistent ulnar-nerve numbness in particular shouldn't be ignored.
