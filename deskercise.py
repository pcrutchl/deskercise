#!/usr/bin/env python3
"""Deskercise — hourly macOS nudges to do knee/hip/balance/mobility work at your desk.

Subcommands:
  notify              Pick the next exercise in the rotation and fire a clickable
                      notification. (This is what the launchd agent runs hourly.)
  session [--id ID]   Run the guided, visual-countdown session for the pending
                      exercise (or a specific one). Clicking the notification runs this.
  next                Show what's next in the rotation without advancing it.
  list                List every exercise in the rotation.
  stats               Today's tally, current streak, and lifetime total.
  recent [N]          Show the last N log entries (default 15).
  done [--id ID]      Manually log a completion (no timer).
  install             Generate + load the launchd agent from config.json.
  uninstall           Unload + remove the launchd agent.
  doctor              Check dependencies and agent status.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LABEL = "com.deskercise.reminder"

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
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"index": 0, "pending": None}


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


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


def find_by_id(exercises: list[dict], ex_id: str) -> dict | None:
    return next((e for e in exercises if e["id"] == ex_id), None)


def peek_next(exercises: list[dict]) -> dict:
    idx = read_state().get("index", 0) % len(exercises)
    return exercises[idx]


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
    state = read_state()
    idx = state.get("index", 0) % len(exercises)
    ex = exercises[idx]

    # Advance rotation and stash which exercise the click should launch.
    state["index"] = (idx + 1) % len(exercises)
    state["pending"] = ex["id"]
    write_state(state)
    log_event("prompted", ex)

    cfg = load_config()
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

    subprocess.run(
        [
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
    )
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
        print(f"\n  {C.YELLOW}skipped — logged. no worries.{C.RESET}\n")
        return

    log_event("completed", ex)
    print()
    print(f"  {C.GREEN}{C.BOLD}✓ done — logged.{C.RESET}")
    if ex.get("note"):
        print(f"  {C.DIM}{ex['note']}{C.RESET}")
    print()
    try:
        input(f"  {C.DIM}press Enter to close…{C.RESET} ")
    except (EOFError, KeyboardInterrupt):
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
    for i, ex in enumerate(load_exercises(), 1):
        flag = f" {C.GREEN}·work-friendly{C.RESET}" if ex.get("work_friendly") else ""
        print(
            f"{i:>2}. {C.BOLD}{ex['name']:<40}{C.RESET} "
            f"{C.DIM}{CATEGORY_LABEL.get(ex['category']):<15}{C.RESET} "
            f"{human_duration(ex):<14}{flag}"
        )
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
    print(f"wrote {plist_path()}")

    uid = os.getuid()
    domain = f"gui/{uid}"
    subprocess.run(
        ["launchctl", "bootout", domain, plist_path()],
        capture_output=True,
    )
    r = subprocess.run(
        ["launchctl", "bootstrap", domain, plist_path()],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        # Older macOS fallback.
        subprocess.run(["launchctl", "unload", plist_path()], capture_output=True)
        r = subprocess.run(
            ["launchctl", "load", "-w", plist_path()], capture_output=True, text=True
        )
        if r.returncode != 0:
            print(f"{C.YELLOW}launchctl error:{C.RESET} {r.stderr.strip()}")
            return 1

    cfg = load_config()
    print(f"{C.GREEN}✓ agent loaded.{C.RESET}")
    print(
        f"  schedule: {cfg['hours'][0]}:00–{cfg['hours'][-1]:02d}:00, "
        f"top of each hour, weekdays.\n"
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
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", plist_path()], capture_output=True
    )
    subprocess.run(["launchctl", "unload", plist_path()], capture_output=True)
    if os.path.exists(plist_path()):
        os.remove(plist_path())
        print(f"removed {plist_path()}")
    print(f"{C.GREEN}✓ agent unloaded.{C.RESET} (Your log in {state_dir()} was kept.)")
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
    r = subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{LABEL}"], capture_output=True, text=True
    )
    print(f"  {ok if r.returncode == 0 else no} agent loaded in launchd")
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


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(prog="deskercise", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("notify")
    sp = sub.add_parser("session")
    sp.add_argument(
        "--id", help="specific exercise id (default: pending from last notify)"
    )
    sub.add_parser("next")
    sub.add_parser("list")
    dp = sub.add_parser("done")
    dp.add_argument("--id", help="specific exercise id")
    sub.add_parser("stats")
    rp = sub.add_parser("recent")
    rp.add_argument("n", nargs="?", type=int, default=15)
    sub.add_parser("install")
    sub.add_parser("uninstall")
    sub.add_parser("doctor")

    args = p.parse_args()
    handlers = {
        "notify": cmd_notify,
        "session": cmd_session,
        "next": cmd_next,
        "list": cmd_list,
        "done": cmd_done,
        "stats": cmd_stats,
        "recent": cmd_recent,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "doctor": cmd_doctor,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
