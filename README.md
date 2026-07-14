# Deskercise

Hourly macOS nudges to do knee-stability, hip-mobility, balance, upper-body, and
hand/forearm work without leaving your desk. Native notifications (clickable),
guided visual countdowns, and a log so you can prove to yourself you did them.

Built around a specific setup: powered standing desk, FluidStance balance board,
7' PVC pipe, and a gyro-ball forearm exerciser — with a cyclist's needs in mind
(knee stabilizers, hip flexibility, and cyclist's-palsy / ulnar-nerve relief).

## How it works

- A **launchd agent** runs `deskercise notify` at **:10 past each hour, 9–17,
  weekdays** (`:10` instead of the top of the hour so it doesn't collide with
  meeting starts — all configurable in `config.json`).
- `notify` picks the next exercise in a **deterministic rotation** that cycles
  evenly through knee → hip → balance → upper-body → hand/forearm, and fires a
  clickable macOS notification via `terminal-notifier`.
- **Clicking the notification** opens a right-sized Terminal window with the
  guided session: the instructions, then a **visual countdown** for each timed
  hold (with discrete sub-timers, e.g. "palm down 15s → palm up 15s → thumb up
  15s"), or a press-Enter-when-done prompt for rep-based moves. The window
  **closes itself** when the session ends.
- Finishing logs a completion. `stats` shows today's tally, your streak, and
  lifetime total.

### Skip-if-you-just-moved

If you've **completed** an exercise within the last `min_gap_minutes` (default
15) — whether via `desknow` or by getting to a notification late — the next
scheduled nudge **self-skips** (and doesn't advance the rotation). So if the
12:10 nudge is one you finally do at 1:05, you won't also get pestered at 1:10.

### If you just ignore a notification

Nothing is logged as done, you get no streak credit, and the rotation still
advances (next hour offers the next exercise). Notifications are grouped, so an
unclicked one is **replaced** by the next hour's rather than piling up. Because
skipping keys off *completions*, ignoring a nudge does **not** suppress the next
one — the following hour fires normally.

## Setup

```sh
brew install terminal-notifier      # clickable notifications
./deskercise install                # loads the launchd agent + installs shortcuts
```

`install` also drops two commands into `~/.local/bin` (on your PATH):

- **`desknow`** — do the next exercise right now, in the current terminal.
- **`deskercise`** — the full CLI, runnable from anywhere.

If `~/.local/bin` isn't on your PATH, `install` prints the `export PATH=…` line to add.

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
| `desknow` | **Shortcut:** do the next exercise now (guided, in this terminal). |
| `deskercise now` | Same as `desknow`. |
| `deskercise notify` | Fire the next notification now (what the agent runs hourly). |
| `deskercise session [--id ID]` | Run the guided session for the pending (or a specific) exercise. |
| `deskercise next` | Show what's next without advancing the rotation. |
| `deskercise list` | List the full rotation. |
| `deskercise done [--id ID]` | Log a completion manually (no timer). |
| `deskercise stats` | Today's count, current streak, lifetime total. |
| `deskercise recent [N]` | Show the last N log entries. |
| `deskercise install` | (Re)load the agent + shortcuts from `config.json`. |
| `deskercise uninstall` | Remove the agent + shortcuts (keeps your log). |
| `deskercise doctor` | Check dependencies, agent status, and next fire times. |

(From the repo you can also invoke these as `./deskercise <cmd>` before installing.)

## Configuration

Edit `config.json`, then re-run `./deskercise install` to apply:

```json
{
  "hours": [9, 10, 11, 12, 13, 14, 15, 16, 17],
  "weekdays": [1, 2, 3, 4, 5],
  "minute": 10,
  "min_gap_minutes": 15,
  "ignore_dnd": true,
  "sound": "Ping",
  "title": "Deskercise",
  "state_dir": "~/.deskercise"
}
```

- `weekdays` uses launchd's convention: `0`/`7` = Sunday, `1` = Monday … `6` = Saturday.
- `minute` is the minutes-past-the-hour each nudge fires.
- `min_gap_minutes` is the skip-if-you-just-moved window described above.
- `ignore_dnd` (`-ignoreDnD`): when true, nudges fire even during Focus / Do Not
  Disturb. Set false if you'd rather Focus modes suppress them.
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

## Development

Pure-stdlib runtime — no dependencies to run. Dev tooling is managed with uv:

```sh
uv run pytest           # test suite (pure logic: rotation, streak, schedule, skip)
pre-commit install      # black formatting on commit
```

CI (GitHub Actions) runs `black --check` + `pytest` on every push.

## Uninstall

```sh
./deskercise uninstall              # removes the agent + shortcuts, keeps your log
brew uninstall terminal-notifier    # optional
```

## Notes

- Not medical advice. The exercises are grounded in PT/sports-medicine guidance,
  but if knee/nerve symptoms persist, worsen, or come with weakness, see a
  professional — persistent ulnar-nerve numbness in particular shouldn't be ignored.
