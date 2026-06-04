from datetime import datetime, timedelta

import pytest

from clarm import timeparse as tp


# ---- parse_duration -------------------------------------------------------

@pytest.mark.parametrize(
    "text,seconds",
    [
        ("30m", 1800),
        ("90s", 90),
        ("1h30m", 5400),
        ("2h", 7200),
        ("in 30m", 1800),
        ("1d", 86400),
        ("2 hours", 7200),
        ("1h 15m", 4500),
    ],
)
def test_parse_duration_ok(text, seconds):
    assert tp.parse_duration(text) == timedelta(seconds=seconds)


@pytest.mark.parametrize("text", ["", "abc", "10", "10x", "-5m", "0s", "10:30"])
def test_parse_duration_bad(text):
    with pytest.raises(tp.ParseError):
        tp.parse_duration(text)


# ---- parse_time_of_day ----------------------------------------------------

def test_parse_time_of_day_ok():
    t = tp.parse_time_of_day("07:05")
    assert (t.hour, t.minute) == (7, 5)


@pytest.mark.parametrize("text", ["7", "24:00", "07:60", "0730", "noon"])
def test_parse_time_of_day_bad(text):
    with pytest.raises(tp.ParseError):
        tp.parse_time_of_day(text)


# ---- parse_oneshot --------------------------------------------------------

NOW = datetime(2026, 6, 4, 10, 0, 0)  # a Thursday


def test_oneshot_relative():
    assert tp.parse_oneshot("in 30m", NOW) == NOW + timedelta(minutes=30)


def test_oneshot_time_later_today():
    assert tp.parse_oneshot("23:30", NOW) == datetime(2026, 6, 4, 23, 30)


def test_oneshot_time_rolls_to_tomorrow():
    # 09:00 has already passed at 10:00 -> tomorrow.
    assert tp.parse_oneshot("09:00", NOW) == datetime(2026, 6, 5, 9, 0)


def test_oneshot_absolute():
    assert tp.parse_oneshot("2026-06-05 07:30", NOW) == datetime(2026, 6, 5, 7, 30)


def test_oneshot_iso():
    assert tp.parse_oneshot("2026-06-05T07:30", NOW) == datetime(2026, 6, 5, 7, 30)


def test_oneshot_bad():
    with pytest.raises(tp.ParseError):
        tp.parse_oneshot("whenever", NOW)


# ---- parse_repeat ---------------------------------------------------------

@pytest.mark.parametrize(
    "spec,expected",
    [
        ("daily", set(range(7))),
        ("everyday", set(range(7))),
        ("weekdays", {0, 1, 2, 3, 4}),
        ("weekends", {5, 6}),
        ("mon-fri", {0, 1, 2, 3, 4}),
        ("mon,wed,fri", {0, 2, 4}),
        ("mon-wed,sat", {0, 1, 2, 5}),
        ("sat-mon", {5, 6, 0}),  # wraps around the week
        ("Monday", {0}),
    ],
)
def test_parse_repeat_ok(spec, expected):
    assert set(tp.parse_repeat(spec)) == expected


@pytest.mark.parametrize("spec", ["", "funday", "mon-funday"])
def test_parse_repeat_bad(spec):
    with pytest.raises(tp.ParseError):
        tp.parse_repeat(spec)
