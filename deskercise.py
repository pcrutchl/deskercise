#!/usr/bin/env python3
"""Deskercise — hourly macOS nudges to do knee/hip/balance/mobility work at your desk.

Subcommands:
  notify              Pick the next exercise in the rotation and fire a clickable
                      notification. (This is what the launchd agent runs hourly.)
  session [--id ID]   Run the guided, visual-countdown session for the pending
                      exercise (or a specific one). Clicking the notification runs this.
  now                 Feeling sedentary? Do the next exercise right now (guided,
                      in this terminal). Completing any exercise suppresses the
                      next scheduled nudge if it lands within min_gap_minutes.
  next                Show what's next in the rotation without advancing it.
  list                List every exercise in the rotation.
  stats               Today's tally, current streak, and lifetime total.
  recent [N]          Show the last N log entries (default 15).
  done [--id ID]      Manually log a completion (no timer).
  install             Generate + load the launchd agents + shortcuts.
  uninstall           Unload + remove the launchd agents + shortcuts.
  doctor              Check dependencies and agent status.

Posture rotation (a separate dwell-state subsystem — sit/stand/board):
  pose [sit|stand|board]   Set the current posture (no arg: show status).
  pose [posture] --since HH:MM   Set/backdate the start time (e.g. you've been
                           standing since 12:15).
  pose --next              Advance to the next posture in the rotation.
  posture-check            Nudge if the current posture is over budget (agent).
  posture-pause / -resume  Silence nudges (e.g. meetings) / restart the timer.
  posture-status [--json]  Show current posture, elapsed, and remaining.
  menubar                  Emit the xbar menu-bar dashboard.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LABEL = "com.deskercise.reminder"
POSTURE_LABEL = "com.deskercise.posture"
POSTURE_ICONS = {"sit": "🪑", "stand": "🧍", "board": "🛹"}

# ---------------------------------------------------------------------------
# Config / paths
# ---------------------------------------------------------------------------


def load_config() -> dict:
    with open(os.path.join(SCRIPT_DIR, "config.json")) as f:
        return json.load(f)


def load_exercises() -> list[dict]:
    with open(os.path.join(SCRIPT_DIR, "exercises.json")) as f:
        return json.load(f)["exercises"]


def state_dir() -> str:
    d = os.path.expanduser(load_config().get("state_dir", "~/.deskercise"))
    os.makedirs(d, exist_ok=True)
    return d


def state_path() -> str:
    return os.path.join(state_dir(), "state.json")


def log_path() -> str:
    return os.path.join(state_dir(), "log.csv")


def read_state() -> dict:
    try:
        with open(state_path()) as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    st.setdefault("up_idx", 0)  # position in the upright-exercise rotation
    st.setdefault("seat_idx", 0)  # position in the seated (nerve) rotation
    st.setdefault("pending", None)
    return st


def write_state(state: dict) -> None:
    with open(state_path(), "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FIELDS = ["timestamp", "date", "event", "exercise_id", "exercise_name", "category"]


def log_event(event: str, ex: dict) -> None:
    now = dt.datetime.now()
    exists = os.path.exists(log_path())
    with open(log_path(), "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if not exists:
            w.writeheader()
        w.writerow(
            {
                "timestamp": now.isoformat(timespec="seconds"),
                "date": now.date().isoformat(),
                "event": event,
                "exercise_id": ex.get("id", ""),
                "exercise_name": ex.get("name", ""),
                "category": ex.get("category", ""),
            }
        )


def read_log() -> list[dict]:
    try:
        with open(log_path(), newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def last_completed_at(rows: list[dict]) -> dt.datetime | None:
    times = [
        r["timestamp"]
        for r in rows
        if r.get("event") == "completed" and r.get("timestamp")
    ]
    if not times:
        return None
    try:
        return max(dt.datetime.fromisoformat(t) for t in times)
    except ValueError:
        return None


def should_skip_notify(rows: list[dict], now: dt.datetime, minutes: int) -> bool:
    """True if an exercise was completed within `minutes` before `now` — so a
    scheduled nudge would be redundant (you just moved)."""
    last = last_completed_at(rows)
    if last is None:
        return False
    return (now - last) < dt.timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


def find_by_id(exercises: list[dict], ex_id: str) -> dict | None:
    return next((e for e in exercises if e["id"] == ex_id), None)


def exercise_posture(ex: dict) -> str:
    """'seated' (doable sitting — the nerve work) or 'upright' (needs standing;
    board and standing are interchangeable, so both count as upright)."""
    return ex.get("posture") or (
        "seated" if ex.get("category") == "nerve" else "upright"
    )


def exercise_pools(exercises: list[dict]) -> tuple[list[dict], list[dict]]:
    upright = [e for e in exercises if exercise_posture(e) == "upright"]
    seated = [e for e in exercises if exercise_posture(e) == "seated"]
    return upright, seated


def peek_next(exercises: list[dict]) -> dict:
    """Next UPRIGHT exercise (what you'd do standing) — used for previews."""
    up, _ = exercise_pools(exercises)
    return up[read_state().get("up_idx", 0) % len(up)]


def nerve_due(rows: list[dict], now: dt.datetime, cooldown_min: int) -> bool:
    """True if it's been at least cooldown_min since the last seated/nerve
    completion — the 'allow nerve work' valve for sit phases."""
    times = [
        dt.datetime.fromisoformat(r["timestamp"])
        for r in rows
        if r.get("event") == "completed"
        and r.get("category") == "nerve"
        and r.get("timestamp")
    ]
    if not times:
        return True
    return (now - max(times)) >= dt.timedelta(minutes=cooldown_min)


def current_posture_is_upright() -> bool:
    """Upright = standing or on the board. If the posture subsystem is disabled,
    treat everything as upright so exercises fire on their own."""
    if not posture_cfg().get("enabled", True):
        return True
    return read_posture()["posture"] in ("stand", "board")


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------


class C:
    _on = sys.stdout.isatty()
    BOLD = "\033[1m" if _on else ""
    DIM = "\033[2m" if _on else ""
    RESET = "\033[0m" if _on else ""
    CYAN = "\033[36m" if _on else ""
    GREEN = "\033[32m" if _on else ""
    YELLOW = "\033[33m" if _on else ""
    MAGENTA = "\033[35m" if _on else ""


CATEGORY_LABEL = {
    "knee": "knee stability",
    "hip": "hip mobility",
    "balance": "balance",
    "upper": "upper body",
    "nerve": "hand / forearm",
}


# ---------------------------------------------------------------------------
# notify
# ---------------------------------------------------------------------------


def terminal_notifier_path() -> str | None:
    p = shutil.which("terminal-notifier")
    if p:
        return p
    for cand in (
        "/opt/homebrew/bin/terminal-notifier",
        "/usr/local/bin/terminal-notifier",
    ):
        if os.path.exists(cand):
            return cand
    return None


def cmd_notify(args) -> int:
    exercises = load_exercises()
    cfg = load_config()

    # Don't nudge if you've already moved recently (manual `now`, or a
    # notification you got to late). Don't advance the rotation either.
    now = dt.datetime.now()
    gap = cfg.get("min_gap_minutes", 15)
    if should_skip_notify(read_log(), now, gap):
        log_event("auto_skipped", {"name": f"(moved <{gap}m ago — nudge skipped)"})
        return 0

    state = read_state()
    up_pool, seated_pool = exercise_pools(exercises)

    if current_posture_is_upright():
        i = state.get("up_idx", 0) % len(up_pool)
        ex = up_pool[i]
        state["up_idx"] = (i + 1) % len(up_pool)
    else:
        # Sit phase: rest, but allow occasional seated (nerve) work.
        cooldown = posture_cfg().get("nerve_rest_cooldown_min", 180)
        if not seated_pool or not nerve_due(read_log(), now, cooldown):
            log_event("rest", {"name": "(sit phase — rest)", "category": "posture"})
            return 0
        i = state.get("seat_idx", 0) % len(seated_pool)
        ex = seated_pool[i]
        state["seat_idx"] = (i + 1) % len(seated_pool)

    # Stash which exercise the click should launch.
    state["pending"] = ex["id"]
    write_state(state)
    log_event("prompted", ex)

    tn = terminal_notifier_path()
    launcher = os.path.join(SCRIPT_DIR, "bin", "launch-session")

    duration = human_duration(ex)
    subtitle = f"{CATEGORY_LABEL.get(ex['category'], ex['category'])} · {duration}"
    message = f"{ex['name']} — click to start the guided timer"

    if tn is None:
        # Zero-dependency fallback so a missing brew install still nudges.
        script = (
            f"display notification {json.dumps(message)} "
            f'with title {json.dumps(cfg["title"])} '
            f"subtitle {json.dumps(subtitle)}"
        )
        subprocess.run(["/usr/bin/osascript", "-e", script])
        print(
            f"(terminal-notifier not installed — sent a basic notification for {ex['id']})"
        )
        return 0

    tn_args = [
        tn,
        "-title",
        cfg["title"],
        "-subtitle",
        subtitle,
        "-message",
        message,
        "-sound",
        cfg.get("sound", "Ping"),
        "-group",
        LABEL,
        "-execute",
        f'"{launcher}"',
    ]
    if cfg.get("ignore_dnd"):
        tn_args.append("-ignoreDnD")  # break through Focus / Do Not Disturb
    subprocess.run(tn_args)
    return 0


def seg_seconds(seg: dict) -> int:
    if "steps" in seg:
        return sum(s["seconds"] for s in seg["steps"])
    return seg.get("seconds", 0)


def human_duration(ex: dict) -> str:
    segs = ex["segments"]
    is_timed = ["reps" not in s for s in segs]
    if all(is_timed):
        total = sum(seg_seconds(s) for s in segs)
        # Show "Ns × M" only for simple, equal single-timers (no sub-steps).
        if len(segs) > 1 and all(
            "steps" not in s and s.get("seconds") == segs[0].get("seconds")
            for s in segs
        ):
            return f"{segs[0]['seconds']}s × {len(segs)}"
        return f"{total}s"
    if all("reps" in s for s in segs):
        if len(segs) > 1 and all(s["reps"] == segs[0]["reps"] for s in segs):
            return f"{segs[0]['reps']} reps × {len(segs)}"
        return " + ".join(f"{s['reps']} reps" for s in segs)
    return "mixed"


# ---------------------------------------------------------------------------
# session (guided, visual)
# ---------------------------------------------------------------------------


def clear() -> None:
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")


def wrap(text: str, width: int = 68, indent: str = "  ") -> str:
    import textwrap

    return "\n".join(
        textwrap.fill(
            line, width=width, initial_indent=indent, subsequent_indent=indent
        )
        for line in text.splitlines()
    )


def countdown(label: str, seconds: int) -> None:
    width = 30
    for remaining in range(seconds, -1, -1):
        mins, secs = divmod(remaining, 60)
        filled = int(width * (seconds - remaining) / seconds) if seconds else width
        bar = "█" * filled + "░" * (width - filled)
        print(
            f"\r  {C.BOLD}{label:<24}{C.RESET} {C.CYAN}{mins}:{secs:02d}{C.RESET}  "
            f"{C.DIM}{bar}{C.RESET}   ",
            end="",
            flush=True,
        )
        if remaining:
            time.sleep(1)
    print()


def rep_segment(label: str, reps: int) -> None:
    print(f"  {C.BOLD}{label}{C.RESET} — {C.CYAN}{reps} slow reps{C.RESET}")
    try:
        input(f"  {C.DIM}press Enter when done ▸{C.RESET} ")
    except EOFError:
        pass


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def exercise_image(ex: dict) -> str | None:
    """Find media/<exercise_id>.<ext> if the user has dropped one in."""
    for ext in IMAGE_EXTS:
        path = os.path.join(SCRIPT_DIR, "media", ex["id"] + ext)
        if os.path.exists(path):
            return path
    return None


def show_image(path: str, height_cells: int = 11) -> None:
    """Render an image inline using the iTerm2 protocol. No-op outside iTerm2."""
    if os.environ.get("TERM_PROGRAM") != "iTerm.app":
        return
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return
    payload = base64.b64encode(data).decode()
    name = base64.b64encode(os.path.basename(path).encode()).decode()
    sys.stdout.write(
        f"\033]1337;File=name={name};size={len(data)};inline=1;"
        f"height={height_cells};preserveAspectRatio=1:{payload}\a\n"
    )
    sys.stdout.flush()


def run_session(ex: dict) -> None:
    clear()
    cat = CATEGORY_LABEL.get(ex["category"], ex["category"])
    print()
    print(f"  {C.MAGENTA}{C.BOLD}DESKERCISE{C.RESET}  {C.DIM}· {cat}{C.RESET}")
    print(f"  {C.BOLD}{C.GREEN}{ex['name']}{C.RESET}")
    if ex.get("equipment") and ex["equipment"] != "none":
        print(f"  {C.DIM}gear: {ex['equipment']}{C.RESET}")
    print()
    print(wrap(ex["cue"]))
    img = exercise_image(ex)
    if img:
        print()
        show_image(img)
    print()

    try:
        for n in range(8, 0, -1):
            print(
                f"\r  {C.DIM}read the cue, get into position… {n}{C.RESET}   ",
                end="",
                flush=True,
            )
            time.sleep(1)
        print("\r" + " " * 48)

        for i, seg in enumerate(ex["segments"]):
            if i > 0:
                print(f"  {C.YELLOW}▸ switch{C.RESET}")
                time.sleep(4)
            if "steps" in seg:
                print(f"  {C.BOLD}{seg['label']}{C.RESET}")
                for j, step in enumerate(seg["steps"]):
                    if j > 0:
                        time.sleep(1)
                    countdown(step["cue"], step["seconds"])
            elif "seconds" in seg:
                countdown(seg["label"], seg["seconds"])
            else:
                rep_segment(seg["label"], seg["reps"])
    except KeyboardInterrupt:
        print()
        log_event("skipped", ex)
        print(f"\n  {C.YELLOW}skipped — logged. no worries.{C.RESET}")
        _linger(2)
        return

    log_event("completed", ex)
    print()
    print(f"  {C.GREEN}{C.BOLD}✓ done — logged.{C.RESET}")
    if ex.get("note"):
        print(f"  {C.DIM}{ex['note']}{C.RESET}")
    print()
    print(f"  {C.DIM}closing…{C.RESET}")
    _linger(6)


def _linger(seconds: int) -> None:
    """Brief pause so the summary is readable before the window auto-closes
    (see bin/launch-session). Ctrl-C skips the wait."""
    try:
        time.sleep(seconds)
    except KeyboardInterrupt:
        pass


def cmd_session(args) -> int:
    exercises = load_exercises()
    ex_id = args.id or read_state().get("pending")
    ex = find_by_id(exercises, ex_id) if ex_id else None
    if ex is None:
        # Nothing pending (e.g. run manually) — just take the next one.
        ex = peek_next(exercises)
    run_session(ex)
    return 0


def cmd_now(args) -> int:
    """Do the next exercise right now, in this terminal. You're opting to move,
    so this always serves an upright exercise. Completing it makes the next
    scheduled nudge self-skip (see should_skip_notify)."""
    up_pool, _ = exercise_pools(load_exercises())
    state = read_state()
    i = state.get("up_idx", 0) % len(up_pool)
    ex = up_pool[i]
    state["up_idx"] = (i + 1) % len(up_pool)
    state["pending"] = ex["id"]
    write_state(state)
    run_session(ex)
    return 0


# ---------------------------------------------------------------------------
# next / list / done / stats / recent
# ---------------------------------------------------------------------------


def cmd_next(args) -> int:
    ex = peek_next(load_exercises())
    print(
        f"{C.BOLD}{ex['name']}{C.RESET}  {C.DIM}({CATEGORY_LABEL.get(ex['category'])}, {human_duration(ex)}){C.RESET}"
    )
    return 0


def cmd_list(args) -> int:
    verbose = getattr(args, "verbose", False)
    for i, ex in enumerate(load_exercises(), 1):
        flag = f" {C.GREEN}·work-friendly{C.RESET}" if ex.get("work_friendly") else ""
        print(
            f"{i:>2}. {C.BOLD}{ex['name']:<40}{C.RESET} "
            f"{C.DIM}{CATEGORY_LABEL.get(ex['category']):<15}{C.RESET} "
            f"{human_duration(ex):<14}{flag}"
        )
        if verbose:
            if ex.get("equipment") and ex["equipment"] != "none":
                print(f"    {C.DIM}gear: {ex['equipment']}{C.RESET}")
            print(wrap(ex["cue"], width=72, indent="    "))
            if ex.get("note"):
                print(f"    {C.DIM}» {ex['note']}{C.RESET}")
            print()
    return 0


def cmd_done(args) -> int:
    exercises = load_exercises()
    ex_id = args.id or read_state().get("pending")
    ex = find_by_id(exercises, ex_id) if ex_id else peek_next(exercises)
    log_event("completed", ex)
    print(f"{C.GREEN}✓ logged {ex['name']}{C.RESET}")
    return 0


def compute_streak(dates_done: set[str]) -> int:
    if not dates_done:
        return 0
    today = dt.date.today()
    # Allow the streak to be "alive" if you've done one today or yesterday.
    start = today if today.isoformat() in dates_done else today - dt.timedelta(days=1)
    if start.isoformat() not in dates_done:
        return 0
    streak = 0
    day = start
    while day.isoformat() in dates_done:
        streak += 1
        day -= dt.timedelta(days=1)
    return streak


def cmd_stats(args) -> int:
    rows = read_log()
    completed = [r for r in rows if r["event"] == "completed"]
    today = dt.date.today().isoformat()
    today_done = [r for r in completed if r["date"] == today]
    dates_done = {r["date"] for r in completed}

    print()
    print(f"  {C.BOLD}{C.MAGENTA}Deskercise stats{C.RESET}")
    print(f"  today:    {C.GREEN}{C.BOLD}{len(today_done)}{C.RESET} completed")
    print(f"  streak:   {C.GREEN}{C.BOLD}{compute_streak(dates_done)}{C.RESET} day(s)")
    print(f"  lifetime: {C.BOLD}{len(completed)}{C.RESET} completed")
    if completed:
        last = completed[-1]
        print(
            f"  last:     {C.DIM}{last['exercise_name']} @ {last['timestamp']}{C.RESET}"
        )
    if today_done:
        print(f"\n  {C.DIM}today:{C.RESET}")
        for r in today_done:
            t = r["timestamp"].split("T")[-1]
            print(f"    {C.DIM}{t}{C.RESET}  {r['exercise_name']}")
    print()
    return 0


def cmd_recent(args) -> int:
    rows = read_log()[-args.n :]
    for r in rows:
        color = {"completed": C.GREEN, "skipped": C.YELLOW, "prompted": C.DIM}.get(
            r["event"], ""
        )
        print(
            f"{r['timestamp']}  {color}{r['event']:<10}{C.RESET} {r['exercise_name']}"
        )
    return 0


# ---------------------------------------------------------------------------
# install / uninstall / doctor
# ---------------------------------------------------------------------------


def plist_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


def build_plist() -> dict:
    cfg = load_config()
    intervals = [
        {"Weekday": w, "Hour": h, "Minute": cfg.get("minute", 0)}
        for w in cfg["weekdays"]
        for h in cfg["hours"]
    ]
    sd = state_dir()
    return {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            os.path.join(SCRIPT_DIR, "deskercise.py"),
            "notify",
        ],
        "StartCalendarInterval": intervals,
        "StandardOutPath": os.path.join(sd, "agent.out.log"),
        "StandardErrorPath": os.path.join(sd, "agent.err.log"),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        },
        "ProcessType": "Interactive",
    }


SHIM_DIR = os.path.expanduser("~/.local/bin")
SHIMS = {
    # command name -> extra args appended after the script path
    "deskercise": "",
    "desknow": " now",  # shortcut: do the next exercise right now
}


def install_shims() -> list[str]:
    """Put `deskercise` and `desknow` on PATH via small shim scripts."""
    os.makedirs(SHIM_DIR, exist_ok=True)
    script = os.path.join(SCRIPT_DIR, "deskercise.py")
    created = []
    for name, suffix in SHIMS.items():
        path = os.path.join(SHIM_DIR, name)
        with open(path, "w") as f:
            f.write(f'#!/bin/bash\nexec python3 "{script}"{suffix} "$@"\n')
        os.chmod(path, 0o755)
        created.append(path)
    return created


def remove_shims() -> None:
    for name in SHIMS:
        path = os.path.join(SHIM_DIR, name)
        # Only remove our own shims, not an unrelated file of the same name.
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    body = f.read()
            except OSError:
                continue
            if "deskercise.py" in body:
                os.remove(path)


def posture_plist_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{POSTURE_LABEL}.plist")


def build_posture_plist() -> dict:
    pc = posture_cfg()
    sd = state_dir()
    return {
        "Label": POSTURE_LABEL,
        "ProgramArguments": [
            sys.executable,
            os.path.join(SCRIPT_DIR, "deskercise.py"),
            "posture-check",
        ],
        "StartInterval": int(pc.get("check_every_min", 5)) * 60,
        "StandardOutPath": os.path.join(sd, "posture.out.log"),
        "StandardErrorPath": os.path.join(sd, "posture.err.log"),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        },
        "ProcessType": "Interactive",
    }


XBAR_PLUGIN = "deskercise.30s.sh"


def xbar_plugins_dir() -> str | None:
    """The xbar plugins dir, or None if xbar isn't installed."""
    base = os.path.expanduser("~/Library/Application Support/xbar")
    return os.path.join(base, "plugins") if os.path.isdir(base) else None


def install_xbar() -> str | None:
    d = xbar_plugins_dir()
    if d is None:
        return None
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, XBAR_PLUGIN)
    script = os.path.join(SCRIPT_DIR, "deskercise.py")
    with open(path, "w") as f:
        f.write(f'#!/bin/bash\nexec "{sys.executable}" "{script}" menubar\n')
    os.chmod(path, 0o755)
    return path


def remove_xbar() -> None:
    d = xbar_plugins_dir()
    if d is None:
        return
    path = os.path.join(d, XBAR_PLUGIN)
    if os.path.isfile(path):
        os.remove(path)


def _reload_agent(plist: str) -> bool:
    """bootout then bootstrap a launchd agent (with an older-macOS fallback)."""
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, plist], capture_output=True)
    r = subprocess.run(
        ["launchctl", "bootstrap", domain, plist], capture_output=True, text=True
    )
    if r.returncode != 0:
        subprocess.run(["launchctl", "unload", plist], capture_output=True)
        r = subprocess.run(
            ["launchctl", "load", "-w", plist], capture_output=True, text=True
        )
    return r.returncode == 0


def cmd_install(args) -> int:
    if terminal_notifier_path() is None:
        print(
            f"{C.YELLOW}warning:{C.RESET} terminal-notifier not found. "
            f"Install it for clickable notifications:\n"
            f"    brew install terminal-notifier\n"
            f"(A basic osascript notification will be used until then.)\n"
        )

    os.makedirs(os.path.dirname(plist_path()), exist_ok=True)
    with open(plist_path(), "wb") as f:
        plistlib.dump(build_plist(), f)
    if not _reload_agent(plist_path()):
        print(f"{C.YELLOW}launchctl error loading exercise agent.{C.RESET}")
        return 1

    cfg = load_config()
    minute = cfg.get("minute", 0)
    print(f"{C.GREEN}✓ exercise agent loaded.{C.RESET}")
    print(
        f"  schedule: {cfg['hours'][0]}:{minute:02d}–{cfg['hours'][-1]}:{minute:02d}, "
        f"hourly, weekdays.\n"
    )

    # Posture rotation agent + xbar menu-bar plugin.
    pc = posture_cfg()
    if pc.get("enabled", True):
        if not os.path.exists(posture_state_path()):
            write_posture(read_posture())  # seed initial state
        with open(posture_plist_path(), "wb") as f:
            plistlib.dump(build_posture_plist(), f)
        if _reload_agent(posture_plist_path()):
            print(
                f"{C.GREEN}✓ posture agent loaded.{C.RESET}  "
                f"rotation: {' → '.join(pc['rotation'])} "
                f"(every {pc.get('check_every_min', 5)}m)"
            )
        xb = install_xbar()
        if xb:
            print(f"{C.GREEN}✓ xbar plugin installed:{C.RESET} {xb}")
            print(
                f"  {C.DIM}refresh xbar (or ⌘R) to see the menu-bar dashboard{C.RESET}\n"
            )
        else:
            print(
                f"  {C.DIM}xbar not detected — menu bar skipped. "
                f"Install xbar, then re-run install.{C.RESET}\n"
            )

    shims = install_shims()
    print(f"{C.GREEN}✓ shell commands installed:{C.RESET} " + ", ".join(SHIMS))
    for p in shims:
        print(f"    {C.DIM}{p}{C.RESET}")
    if SHIM_DIR not in os.environ.get("PATH", "").split(os.pathsep):
        print(
            f"  {C.YELLOW}note:{C.RESET} {SHIM_DIR} isn't on your PATH. Add this to your shell rc:\n"
            f'    {C.CYAN}export PATH="{SHIM_DIR}:$PATH"{C.RESET}'
        )
    print(
        f"  {C.DIM}desknow{C.RESET} = do the next exercise now · "
        f"{C.DIM}deskercise <cmd>{C.RESET} = full CLI\n"
    )

    print(f"{C.BOLD}One-time macOS setup so these are hard to ignore:{C.RESET}")
    print("  System Settings → Notifications → terminal-notifier")
    print("    • Allow Notifications: ON")
    print("    • Alert style: Alerts (not Banners — Alerts stay until dismissed)\n")
    print(
        f"Test it now:  {C.CYAN}./deskercise notify{C.RESET}  (then click the notification)"
    )
    return 0


def cmd_uninstall(args) -> int:
    uid = os.getuid()
    for plist in (plist_path(), posture_plist_path()):
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}", plist], capture_output=True
        )
        subprocess.run(["launchctl", "unload", plist], capture_output=True)
        if os.path.exists(plist):
            os.remove(plist)
            print(f"removed {plist}")
    remove_shims()
    remove_xbar()
    print(f"removed shell commands: {', '.join(SHIMS)} (+ xbar plugin if present)")
    print(f"{C.GREEN}✓ agents unloaded.{C.RESET} (Your log in {state_dir()} was kept.)")
    return 0


def cmd_doctor(args) -> int:
    print()
    tn = terminal_notifier_path()
    ok = f"{C.GREEN}✓{C.RESET}"
    no = f"{C.YELLOW}✗{C.RESET}"
    print(
        f"  {ok if tn else no} terminal-notifier: {tn or 'NOT INSTALLED (brew install terminal-notifier)'}"
    )
    print(
        f"  {ok if os.path.exists(plist_path()) else no} launch agent plist: {plist_path()}"
    )

    uid = os.getuid()
    for label, name in ((LABEL, "exercise agent"), (POSTURE_LABEL, "posture agent")):
        r = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"], capture_output=True, text=True
        )
        print(f"  {ok if r.returncode == 0 else no} {name} loaded in launchd")
    xb = xbar_plugins_dir()
    xb_ok = xb is not None and os.path.isfile(os.path.join(xb, XBAR_PLUGIN))
    print(
        f"  {ok if xb_ok else no} xbar plugin: {'installed' if xb_ok else 'not installed'}"
    )
    iterm = os.path.isdir("/Applications/iTerm.app")
    print(
        f"  {ok if iterm else no} iTerm2 (session window): "
        f"{'installed' if iterm else 'NOT installed (brew install --cask iterm2)'}"
    )
    print(f"  {ok} python: {sys.executable}")
    print(f"  {ok} state dir: {state_dir()}")

    cfg = load_config()
    now = dt.datetime.now()
    upcoming = next_fire_times(cfg, now, count=3)
    print(f"\n  next fires:")
    for t in upcoming:
        print(f"    {C.DIM}{t:%a %Y-%m-%d %H:%M}{C.RESET}")
    print()
    return 0


def next_fire_times(cfg: dict, now: dt.datetime, count: int = 3) -> list[dt.datetime]:
    """launchd Weekday: 0/7=Sun, 1=Mon..6=Sat. Python weekday(): Mon=0..Sun=6."""
    wanted_days = set(cfg["weekdays"])
    hours = sorted(cfg["hours"])
    minute = cfg.get("minute", 0)
    out: list[dt.datetime] = []
    day = now.date()
    for _ in range(21):
        launchd_dow = (day.weekday() + 1) % 7  # Mon=0->1 ... Sun=6->0
        if launchd_dow in wanted_days or (launchd_dow == 0 and 7 in wanted_days):
            for h in hours:
                cand = dt.datetime.combine(day, dt.time(h, minute))
                if cand > now:
                    out.append(cand)
                    if len(out) >= count:
                        return out
        day += dt.timedelta(days=1)
    return out


def in_work_window(cfg: dict, now: dt.datetime) -> bool:
    """True on a configured weekday within [min(hours), max(hours)]."""
    wd = (now.weekday() + 1) % 7  # launchd convention: Mon=1..Sun=0
    weekdays = set(cfg.get("weekdays", [1, 2, 3, 4, 5]))
    day_ok = wd in weekdays or (wd == 0 and 7 in weekdays)
    hours = cfg.get("hours", [9, 17])
    return day_ok and (min(hours) <= now.hour <= max(hours))


# ---------------------------------------------------------------------------
# posture rotation (dwell-state subsystem)
# ---------------------------------------------------------------------------

DEFAULT_POSTURE = {
    "enabled": True,
    "rotation": ["sit", "stand", "sit", "board"],
    "budgets_min": {"sit": 25, "stand": 12, "board": 18},
    "check_every_min": 5,
    "nerve_rest_cooldown_min": 180,
}


def posture_cfg() -> dict:
    c = dict(DEFAULT_POSTURE)
    c.update(load_config().get("posture", {}))
    return c


def posture_state_path() -> str:
    return os.path.join(state_dir(), "posture.json")


def read_posture() -> dict:
    pc = posture_cfg()
    try:
        with open(posture_state_path()) as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    st.setdefault("idx", 0)
    st.setdefault("posture", pc["rotation"][st["idx"] % len(pc["rotation"])])
    st.setdefault("since", dt.datetime.now().isoformat(timespec="seconds"))
    st.setdefault("paused", False)
    return st


def write_posture(st: dict) -> None:
    with open(posture_state_path(), "w") as f:
        json.dump(st, f, indent=2)


def next_posture(st: dict, pc: dict) -> str:
    rot = pc["rotation"]
    return rot[(st.get("idx", 0) + 1) % len(rot)]


def posture_remaining(st: dict, pc: dict, now: dt.datetime) -> float:
    """Minutes left in the current posture's budget (negative = over)."""
    since = dt.datetime.fromisoformat(st["since"])
    budget = pc["budgets_min"].get(st["posture"], 20)
    return budget - (now - since).total_seconds() / 60


def _posture_lines(st, pc, now):
    budget = pc["budgets_min"].get(st["posture"], 20)
    remaining = int(round(posture_remaining(st, pc, now)))
    return budget - remaining, budget, remaining, next_posture(st, pc)


def post_notification(
    cfg, title, message, subtitle="", group=None, execute=None
) -> None:
    """Fire a macOS notification (terminal-notifier, osascript fallback)."""
    tn = terminal_notifier_path()
    if tn is None:
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                f"display notification {json.dumps(message)} with title {json.dumps(title)}",
            ]
        )
        return
    args = [tn, "-title", title, "-message", message]
    if subtitle:
        args += ["-subtitle", subtitle]
    args += ["-sound", cfg.get("sound", "Ping")]
    if group:
        args += ["-group", group]
    if execute:
        args += ["-execute", execute]
    if cfg.get("ignore_dnd"):
        args.append("-ignoreDnD")
    subprocess.run(args)


def _clear_posture_notification() -> None:
    """Remove any lingering posture notification (the clicked nudge)."""
    tn = terminal_notifier_path()
    if tn is not None:
        subprocess.run([tn, "-remove", POSTURE_LABEL], capture_output=True)


def _set_posture(
    st: dict, posture: str, idx: int | None = None, since: dt.datetime | None = None
) -> None:
    st["posture"] = posture
    if idx is not None:
        st["idx"] = idx
    st["since"] = (since or dt.datetime.now()).isoformat(timespec="seconds")
    st["paused"] = False
    write_posture(st)
    log_event("posture", {"name": f"→ {posture}", "category": "posture"})


def cmd_pose(args) -> int:
    pc = posture_cfg()
    rotation = pc["rotation"]
    st = read_posture()

    since = None
    if getattr(args, "since", None):
        try:
            t = dt.datetime.strptime(args.since, "%H:%M").time()
        except ValueError:
            print(f"{C.YELLOW}--since must be HH:MM (24-hour), e.g. 12:15{C.RESET}")
            return 1
        since = dt.datetime.combine(dt.date.today(), t)

    if getattr(args, "next", False):
        idx = (st.get("idx", 0) + 1) % len(rotation)
        _set_posture(st, rotation[idx], idx)
        # Clear the nudge that triggered this. No confirm notification — it would
        # persist inertly under Alerts style; the xbar menu bar shows state.
        _clear_posture_notification()
        print(f"{C.GREEN}→ {st['posture']}{C.RESET}")
        return 0

    if getattr(args, "posture", None):
        p = args.posture
        idx = rotation.index(p) if p in rotation else st.get("idx", 0)
        _set_posture(st, p, idx, since=since)
        extra = f" (since {args.since})" if since else ""
        print(f"{C.GREEN}→ {p}{C.RESET}{extra}")
        return 0

    if since:  # backdate the current posture's start time
        st["since"] = since.isoformat(timespec="seconds")
        write_posture(st)
        print(f"{C.GREEN}{st['posture']} since {args.since}{C.RESET}")
        return 0

    return cmd_posture_status(args)


def cmd_posture_check(args) -> int:
    force = getattr(args, "force", False)  # fire now regardless of budget/window
    pc = posture_cfg()
    if not force and not pc.get("enabled", True):
        return 0
    st = read_posture()
    if not force and st.get("paused"):
        return 0
    cfg = load_config()
    now = dt.datetime.now()
    if not force and not in_work_window(cfg, now):
        return 0
    if force or posture_remaining(st, pc, now) <= 0:
        elapsed = int(
            (now - dt.datetime.fromisoformat(st["since"])).total_seconds() // 60
        )
        nxt = next_posture(st, pc)
        icon = POSTURE_ICONS.get(st["posture"], "")
        advance = (
            f"{shlex.quote(sys.executable)} "
            f"{shlex.quote(os.path.join(SCRIPT_DIR, 'deskercise.py'))} pose --next"
        )
        post_notification(
            cfg,
            "Posture",
            f"{icon} {st['posture']} {elapsed}m → switch to {nxt}",
            group=POSTURE_LABEL,
            execute=advance,
        )
    return 0


def cmd_posture_pause(args) -> int:
    st = read_posture()
    st["paused"] = True
    write_posture(st)
    print("posture nudges paused (resume with: deskercise posture-resume)")
    return 0


def cmd_posture_resume(args) -> int:
    st = read_posture()
    st["paused"] = False
    st["since"] = dt.datetime.now().isoformat(timespec="seconds")
    write_posture(st)
    print(f"{C.GREEN}resumed{C.RESET} — {st['posture']} timer reset")
    return 0


def cmd_posture_status(args) -> int:
    pc = posture_cfg()
    st = read_posture()
    now = dt.datetime.now()
    elapsed, budget, remaining, nxt = _posture_lines(st, pc, now)
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "posture": st["posture"],
                    "elapsed_min": elapsed,
                    "budget_min": budget,
                    "remaining_min": remaining,
                    "next": nxt,
                    "paused": st.get("paused", False),
                }
            )
        )
        return 0
    icon = POSTURE_ICONS.get(st["posture"], "")
    paused = f" {C.YELLOW}(paused){C.RESET}" if st.get("paused") else ""
    print()
    print(
        f"  {icon} {C.BOLD}{st['posture']}{C.RESET}{paused} — "
        f"{elapsed}m of {budget}m, {C.CYAN}{remaining}m left{C.RESET}"
    )
    print(f"  {C.DIM}next: {nxt}{C.RESET}")
    print()
    return 0


def _fmt_dur(m: int | None) -> str:
    if m is None:
        return "—"
    if m >= 60:
        return f"{m // 60}h{m % 60:02d}m"
    return f"{m}m"


def cmd_menubar(args) -> int:
    """Emit the xbar dashboard: posture in the title, plus the next exercise."""
    pc = posture_cfg()
    st = read_posture()
    now = dt.datetime.now()
    p_elapsed, p_budget, p_remaining, p_next = _posture_lines(st, pc, now)

    cfg = load_config()
    nxt_ex = peek_next(load_exercises())
    fires = next_fire_times(cfg, now, 1)
    ex_rem = int((fires[0] - now).total_seconds() // 60) if fires else None
    will_skip = bool(
        fires
        and should_skip_notify(read_log(), fires[0], cfg.get("min_gap_minutes", 15))
    )

    picon = POSTURE_ICONS.get(st["posture"], "")
    if st.get("paused"):
        title, color = f"{picon} paused", ""
    elif p_remaining > 0:
        title, color = f"{picon} {p_remaining}m", ""
    else:
        title, color = f"{picon} +{-p_remaining}m", " | color=red"
    ex_part = f" · ⏱{_fmt_dur(ex_rem)}" if ex_rem is not None else ""
    print(f"{title}{ex_part}{color}")
    print("---")

    py = sys.executable
    script = os.path.join(SCRIPT_DIR, "deskercise.py")
    launcher = os.path.join(SCRIPT_DIR, "bin", "launch-session")

    def action(label, *params):
        attrs = [f'shell="{py}"', f'param1="{script}"']
        for i, pv in enumerate(params, start=2):
            attrs.append(f'param{i}="{pv}"')
        attrs += ["terminal=false", "refresh=true"]
        print(f"{label} | " + " ".join(attrs))

    print(f"Posture: {st['posture']} · {p_elapsed}m / {p_budget}m")
    action(f"Advance → {p_next}", "pose", "--next")
    if st.get("paused"):
        action("Resume", "posture-resume")
    else:
        action("Pause", "posture-pause")
    for p in ("sit", "stand", "board"):
        action(f"Set: {p}", "pose", p)

    print("---")
    print(f"Next upright exercise: {nxt_ex['name']}")
    if not current_posture_is_upright():
        print("(sit phase — rests until you stand, minus occasional nerve work)")
    if ex_rem is not None:
        when = fires[0].strftime("%-I:%M")
        note = "  ⚠︎ will skip (moved recently)" if will_skip else ""
        print(f"next check in {_fmt_dur(ex_rem)} ({when}){note}")
    print(f'Do it now | shell="{launcher}" param1="now" terminal=false refresh=true')
    print("---")
    print("Deskercise")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        prog="deskercise",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("notify")
    sp = sub.add_parser("session")
    sp.add_argument(
        "--id", help="specific exercise id (default: pending from last notify)"
    )
    sub.add_parser("now")
    sub.add_parser("next")
    lp = sub.add_parser("list")
    lp.add_argument(
        "-v", "--verbose", action="store_true", help="also print each exercise's cue"
    )
    dp = sub.add_parser("done")
    dp.add_argument("--id", help="specific exercise id")
    sub.add_parser("stats")
    rp = sub.add_parser("recent")
    rp.add_argument("n", nargs="?", type=int, default=15)
    sub.add_parser("install")
    sub.add_parser("uninstall")
    sub.add_parser("doctor")

    pp = sub.add_parser("pose")
    pp.add_argument("posture", nargs="?", choices=["sit", "stand", "board"])
    pp.add_argument("--next", action="store_true", help="advance to the next posture")
    pp.add_argument(
        "--since", metavar="HH:MM", help="backdate the start time (e.g. 12:15)"
    )
    pcp = sub.add_parser("posture-check")
    pcp.add_argument(
        "--force", action="store_true", help="fire a nudge now (ignore budget/window)"
    )
    sub.add_parser("posture-pause")
    sub.add_parser("posture-resume")
    psp = sub.add_parser("posture-status")
    psp.add_argument("--json", action="store_true")
    sub.add_parser("menubar")

    args = p.parse_args()
    handlers = {
        "notify": cmd_notify,
        "session": cmd_session,
        "now": cmd_now,
        "next": cmd_next,
        "list": cmd_list,
        "done": cmd_done,
        "stats": cmd_stats,
        "recent": cmd_recent,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "doctor": cmd_doctor,
        "pose": cmd_pose,
        "posture-check": cmd_posture_check,
        "posture-pause": cmd_posture_pause,
        "posture-resume": cmd_posture_resume,
        "posture-status": cmd_posture_status,
        "menubar": cmd_menubar,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
