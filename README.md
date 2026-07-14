# Deskercise

Hourly macOS nudges to do knee-stability, hip-mobility, balance, upper-body, and
hand/forearm work without leaving your desk. Native notifications (clickable),
guided visual countdowns, and a log so you can prove to yourself you did them.
Plus a separate **posture-rotation** layer (sit / stand / balance board) with an
optional xbar menu-bar dashboard.

Built around a specific setup: powered standing desk, FluidStance balance board,
7' PVC pipe, and a gyro-ball forearm exerciser — with a cyclist's needs in mind
(knee stabilizers, hip flexibility, and cyclist's-palsy / ulnar-nerve relief).

## How it works

- A **launchd agent** runs `deskercise notify` at **:10 past each hour, 9–17,
  weekdays** (`:10` instead of the top of the hour so it doesn't collide with
  meeting starts — all configurable in `config.json`).
- `notify` picks the next exercise in a **deterministic rotation** that cycles
  evenly through knee → hip → balance → upper-body → hand/forearm, and fires a
  clickable macOS notification via `terminal-notifier`. Selection is
  **posture-aware** (see below): when you're upright it serves a standing/board
  exercise; when you're sitting it rests (with occasional seated nerve work).
- **Clicking the notification** opens a right-sized **iTerm2** window with the
  guided session: the instructions, then a **visual countdown** for each timed
  hold (with discrete sub-timers, e.g. "palm down 15s → palm up 15s → thumb up
  15s"), or a press-Enter-when-done prompt for rep-based moves. The window
  **closes itself** when the session ends. (iTerm2 is used for its scripting +
  inline-image support; install it via `brew install --cask iterm2`.)
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
brew install --cask iterm2          # guided-session window (scripting + images)
./deskercise install                # loads the launchd agents + shortcuts
```

`install` also:

- Drops two commands into `~/.local/bin` (on your PATH): **`desknow`** (do the
  next exercise now, in the current terminal) and **`deskercise`** (the full CLI).
  If `~/.local/bin` isn't on your PATH, it prints the `export PATH=…` line to add.
- Loads the **posture-rotation agent** and, if [xbar](https://xbarapp.com) is
  installed, drops in the **menu-bar plugin** (see *Posture rotation* below).

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
| `deskercise install` | (Re)load both agents + shortcuts + xbar plugin from `config.json`. |
| `deskercise uninstall` | Remove agents + shortcuts + xbar plugin (keeps your log). |
| `deskercise doctor` | Check dependencies, agent status, and next fire times. |

**Posture rotation:**

| Command | What it does |
| --- | --- |
| `deskercise pose` | Show current posture, elapsed, and time left. |
| `deskercise pose sit\|stand\|board` | Set the current posture (resets the timer). |
| `deskercise pose --next` | Advance to the next posture in the rotation. |
| `deskercise posture-pause` / `posture-resume` | Silence nudges (meetings) / restart the timer. |
| `deskercise posture-status [--json]` | Current posture state. |
| `deskercise menubar` | Emit the xbar dashboard (used by the plugin). |

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
  "state_dir": "~/.deskercise",
  "posture": {
    "enabled": true,
    "rotation": ["sit", "stand", "sit", "board"],
    "budgets_min": { "sit": 25, "stand": 12, "board": 18 },
    "check_every_min": 5
  }
}
```

- `weekdays` uses launchd's convention: `0`/`7` = Sunday, `1` = Monday … `6` = Saturday.
- `minute` is the minutes-past-the-hour each nudge fires.
- `min_gap_minutes` is the skip-if-you-just-moved window described above.
- `ignore_dnd` (`-ignoreDnD`): when true, nudges fire even during Focus / Do Not
  Disturb. Set false if you'd rather Focus modes suppress them.
- `posture`: the posture-rotation subsystem — see below. `rotation` is the order
  (repeats allowed, e.g. `sit` between the two upright modes); `budgets_min` is
  the dwell cap per posture; `check_every_min` is how often the agent checks.
- Runtime state (rotation position + `log.csv`) lives in `state_dir`, outside the repo.

## Posture rotation (menu bar)

Separate from the acute exercise nudges, this manages a **dwell state** — where
your body is *right now* — and rotates you through **sit → stand → sit → board**
so you never sit (or stand) too long. It's grounded in the movement-break
research: uninterrupted sitting ≥30 min independently raises risk, and static
standing is also fatiguing — so no posture is safe to camp in, and `sit` is
interleaved between the two upright modes.

- A second launchd agent (`posture-check`, every 5 min during work hours) fires a
  notification when the current posture exceeds its dwell budget: *"sit 27m →
  switch to stand."* **Clicking it advances the state.**
- There's no sensor to detect sit/stand on a dumb desk (and Apple Silicon Macs
  have no accelerometer anyway), so state is **confirm-on-transition**: you click
  the nudge, or run `deskercise pose <sit|stand|board>` if you go off-script.
- **Persistent by design:** if you ignore a nudge it keeps re-firing every check
  until you actually move. Use `posture-pause` for meetings; `posture-resume`
  restarts the timer.

### Posture-matched exercises

Almost every exercise needs you upright (only the two nerve moves are seated), so
the exercise nudge respects your current posture instead of fighting it:

- **Upright** (standing *or* on the board — they're interchangeable; step on/off
  as the exercise needs): serves the next standing/board exercise.
- **Sitting:** **rests** by default. The two seated nerve exercises are still
  eligible — one is offered only if you haven't done nerve work in the last
  `nerve_rest_cooldown_min` (default 180). Otherwise the nudge stays quiet and the
  upright rotation waits until you stand.
- `desknow` / the menu-bar **Do it now** always serve an upright exercise — if
  you're reaching for it, you're choosing to move.

### Menu bar (xbar)

If you have [xbar](https://xbarapp.com), `install` adds a menu-bar dashboard
(`~/Library/Application Support/xbar/plugins/deskercise.30s.sh`) showing your
current posture + time left **and** the next exercise + countdown:

```
🪑 12m · ⏱23m         ← posture remaining · time to next exercise
──────────────
Posture: sit · 13m / 25m
Advance → stand
Pause
Set: sit / stand / board
──────────────
Next exercise: Single-leg mini squats
in 23m (1:10)
Do it now            ← opens the guided session window
```

Refresh xbar (⌘R) after install to see it.

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

## Reference images (optional)

Drop an image at `media/<exercise_id>.<ext>` (`png`/`jpg`/`jpeg`/`gif`/`webp`)
and it renders inline at the top of that exercise's guided session — in **iTerm2
only** (via its inline-image protocol; silently skipped elsewhere). The `id`s and
naming are documented in [`media/README.md`](media/README.md). Image files are
gitignored, so they stay local and never get committed.

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
