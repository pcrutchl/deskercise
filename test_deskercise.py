"""Targeted tests for the pure logic in deskercise.py.

Deliberately narrow: the side-effecting commands (session UI, install/launchctl,
firing notifications) are thin wrappers and not worth unit-testing. These cover the
functions with real edge cases — especially next_fire_times, whose launchd weekday
convention (0/7 = Sunday) is easy to get wrong.
"""

import argparse
import datetime as dt

import deskercise as d

# --- seg_seconds ------------------------------------------------------------


def test_seg_seconds_single_timer():
    assert d.seg_seconds({"label": "Hold", "seconds": 45}) == 45


def test_seg_seconds_sums_substeps():
    seg = {"label": "R", "steps": [{"seconds": 15}, {"seconds": 15}, {"seconds": 15}]}
    assert d.seg_seconds(seg) == 45


def test_seg_seconds_reps_have_no_duration():
    assert d.seg_seconds({"label": "reps", "reps": 12}) == 0


# --- human_duration ---------------------------------------------------------


def _ex(segments):
    return {"segments": segments}


def test_human_duration_single_hold():
    assert d.human_duration(_ex([{"label": "Hold", "seconds": 45}])) == "45s"


def test_human_duration_equal_two_sided_timer():
    ex = _ex([{"seconds": 40}, {"seconds": 40}])
    assert d.human_duration(ex) == "40s × 2"


def test_human_duration_substep_segments_report_total():
    # gyro ball: two sides of 3×15s sub-timers = 90s total
    side = {"steps": [{"seconds": 15}, {"seconds": 15}, {"seconds": 15}]}
    assert d.human_duration(_ex([side, side])) == "90s"


def test_human_duration_equal_reps():
    assert d.human_duration(_ex([{"reps": 10}, {"reps": 10}])) == "10 reps × 2"


def test_human_duration_single_reps():
    assert d.human_duration(_ex([{"reps": 12}])) == "12 reps"


def test_human_duration_mixed_is_labelled_mixed():
    assert d.human_duration(_ex([{"seconds": 30}, {"reps": 10}])) == "mixed"


# --- compute_streak ---------------------------------------------------------


def _iso(days_ago):
    return (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()


def test_streak_empty_is_zero():
    assert d.compute_streak(set()) == 0


def test_streak_counts_consecutive_days_ending_today():
    assert d.compute_streak({_iso(0), _iso(1), _iso(2)}) == 3


def test_streak_alive_when_last_done_yesterday():
    # Not done today yet, but yesterday + the day before -> streak stays at 2.
    assert d.compute_streak({_iso(1), _iso(2)}) == 2


def test_streak_broken_by_gap():
    # Done today and three days ago, but not the two days between -> just today.
    assert d.compute_streak({_iso(0), _iso(3)}) == 1


def test_streak_stale_history_is_zero():
    # Last completion was two days ago -> streak has lapsed.
    assert d.compute_streak({_iso(2), _iso(3)}) == 0


# --- next_fire_times (launchd weekday convention: 0/7 = Sun, 1 = Mon .. 6 = Sat)


WEEKDAYS_MON_FRI = {"weekdays": [1, 2, 3, 4, 5], "hours": [9, 10, 11], "minute": 0}


def test_next_fire_same_day():
    now = dt.datetime(2026, 7, 14, 9, 30)  # Tue 09:30
    assert d.next_fire_times(WEEKDAYS_MON_FRI, now, count=1) == [
        dt.datetime(2026, 7, 14, 10, 0)
    ]


def test_next_fire_skips_to_next_day_after_last_hour():
    now = dt.datetime(2026, 7, 14, 18, 0)  # Tue evening, past last hour
    assert d.next_fire_times(WEEKDAYS_MON_FRI, now, count=1) == [
        dt.datetime(2026, 7, 15, 9, 0)  # Wed 09:00
    ]


def test_next_fire_skips_weekend():
    now = dt.datetime(2026, 7, 17, 18, 0)  # Fri evening
    # Sat (launchd 6) and Sun (launchd 0) excluded -> jumps to Monday.
    assert d.next_fire_times(WEEKDAYS_MON_FRI, now, count=1) == [
        dt.datetime(2026, 7, 20, 9, 0)  # Mon 09:00
    ]


def test_next_fire_sunday_via_7_convention():
    # weekday 7 must be treated as Sunday (the 0/7 quirk).
    cfg = {"weekdays": [7], "hours": [9], "minute": 0}
    now = dt.datetime(2026, 7, 14, 12, 0)  # Tue
    assert d.next_fire_times(cfg, now, count=1) == [
        dt.datetime(2026, 7, 19, 9, 0)  # Sun 09:00
    ]


def test_next_fire_sunday_via_0_convention():
    cfg = {"weekdays": [0], "hours": [9], "minute": 0}
    now = dt.datetime(2026, 7, 14, 12, 0)  # Tue
    assert d.next_fire_times(cfg, now, count=1) == [dt.datetime(2026, 7, 19, 9, 0)]


def test_next_fire_returns_requested_count_in_order():
    now = dt.datetime(2026, 7, 14, 8, 0)  # Tue, before first hour
    got = d.next_fire_times(WEEKDAYS_MON_FRI, now, count=4)
    assert got == [
        dt.datetime(2026, 7, 14, 9, 0),
        dt.datetime(2026, 7, 14, 10, 0),
        dt.datetime(2026, 7, 14, 11, 0),
        dt.datetime(2026, 7, 15, 9, 0),
    ]


# --- rotation (cmd_notify advances + wraps) ---------------------------------


def _isolate_state(tmp_path, monkeypatch):
    """Point state/log at a temp dir and stub out the actual notification."""
    monkeypatch.setattr(d, "state_dir", lambda: str(tmp_path))
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: None)


def test_rotation_advances_and_sets_pending(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    exercises = d.load_exercises()

    d.cmd_notify(argparse.Namespace())
    st = d.read_state()
    assert st["index"] == 1
    assert st["pending"] == exercises[0]["id"]

    d.cmd_notify(argparse.Namespace())
    st = d.read_state()
    assert st["index"] == 2
    assert st["pending"] == exercises[1]["id"]


def test_rotation_wraps_at_end(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    exercises = d.load_exercises()
    d.write_state({"index": len(exercises) - 1, "pending": None})

    d.cmd_notify(argparse.Namespace())
    st = d.read_state()
    assert st["index"] == 0
    assert st["pending"] == exercises[-1]["id"]
