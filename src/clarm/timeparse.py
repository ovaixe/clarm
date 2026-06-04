"""Parsing of human-friendly time and repeat specifications.

Everything here is pure: functions take their inputs (including ``now`` where a
"current time" is needed) and return values, so they are trivial to unit-test
without touching the clock or the filesystem.

Weekdays use the same convention as :meth:`datetime.date.weekday`:
Monday is ``0`` ... Sunday is ``6``.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta

__all__ = [
    "ParseError",
    "parse_duration",
    "parse_time_of_day",
    "parse_oneshot",
    "parse_repeat",
    "WEEKDAY_NAMES",
]


class ParseError(ValueError):
    """Raised when an input string cannot be understood."""


WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_WEEKDAY_INDEX = {name: i for i, name in enumerate(WEEKDAY_NAMES)}
# A few friendly aliases people actually type.
_WEEKDAY_INDEX.update(
    {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
        "tues": 1, "weds": 2, "thur": 3, "thurs": 4,
    }
)

_DURATION_RE = re.compile(r"(?P<value>\d+)\s*(?P<unit>[a-zA-Z]+)")
_UNIT_SECONDS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}


def parse_duration(text: str) -> timedelta:
    """Parse a compound duration like ``"1h30m"``, ``"90s"`` or ``"2 hours"``.

    Accepts an optional leading ``in`` (so ``"in 30m"`` works too). Multiple
    unit groups are summed. Raises :class:`ParseError` on anything else.
    """
    raw = text.strip().lower()
    if raw.startswith("in "):
        raw = raw[3:].strip()
    if not raw:
        raise ParseError("empty duration")

    matches = list(_DURATION_RE.finditer(raw))
    # Ensure the whole string is consumed by duration tokens (ignoring spaces).
    consumed = "".join(m.group(0) for m in matches).replace(" ", "")
    if not matches or consumed != raw.replace(" ", ""):
        raise ParseError(f"not a duration: {text!r}")

    total = 0
    for m in matches:
        unit = m.group("unit")
        if unit not in _UNIT_SECONDS:
            raise ParseError(f"unknown time unit {unit!r} in {text!r}")
        total += int(m.group("value")) * _UNIT_SECONDS[unit]
    if total <= 0:
        raise ParseError("duration must be positive")
    return timedelta(seconds=total)


def parse_time_of_day(text: str) -> time:
    """Parse ``"HH:MM"`` (24h) or ``"H:MM"`` into a :class:`datetime.time`."""
    raw = text.strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if not m:
        raise ParseError(f"not a HH:MM time: {text!r}")
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ParseError(f"time out of range: {text!r}")
    return time(hour=hour, minute=minute)


def parse_oneshot(text: str, now: datetime) -> datetime:
    """Resolve a one-time alarm spec to an absolute future ``datetime``.

    Understands, in order:

    - relative durations: ``"in 30m"``, ``"90s"``, ``"1h30m"`` -> ``now + delta``
    - a bare time of day: ``"07:30"`` -> the next time that clock-time occurs
    - an absolute datetime: ``"2026-06-05 07:30"`` or ISO ``"2026-06-05T07:30"``

    The result is always strictly in the future relative to ``now``.
    """
    raw = text.strip()

    # 1) relative duration
    try:
        return (now + parse_duration(raw)).replace(microsecond=0)
    except ParseError:
        pass

    # 2) bare time of day -> next occurrence
    try:
        tod = parse_time_of_day(raw)
    except ParseError:
        tod = None
    if tod is not None:
        candidate = now.replace(
            hour=tod.hour, minute=tod.minute, second=0, microsecond=0
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    # 3) absolute datetime in a few common shapes
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(microsecond=0)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw).replace(microsecond=0, tzinfo=None)
    except ValueError:
        pass

    raise ParseError(
        f"could not understand time {text!r}; try '07:30', 'in 30m', or "
        "'2026-06-05 07:30'"
    )


def parse_repeat(spec: str) -> frozenset[int]:
    """Parse a recurrence spec into a set of weekday indices (Mon=0).

    Supports keywords (``daily``/``everyday``, ``weekdays``, ``weekends``),
    ranges (``mon-fri``) and comma lists (``mon,wed,fri``), which may be mixed
    (``mon-wed,sat``). Raises :class:`ParseError` on unknown tokens or an empty
    result.
    """
    raw = spec.strip().lower()
    if not raw:
        raise ParseError("empty repeat spec")

    if raw in ("daily", "everyday", "every-day", "all"):
        return frozenset(range(7))
    if raw in ("weekday", "weekdays"):
        return frozenset(range(5))
    if raw in ("weekend", "weekends"):
        return frozenset({5, 6})

    days: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = _weekday(start_s), _weekday(end_s)
            # Inclusive range, wrapping around the week (e.g. sat-mon).
            i = start
            while True:
                days.add(i)
                if i == end:
                    break
                i = (i + 1) % 7
        else:
            days.add(_weekday(part))

    if not days:
        raise ParseError(f"no weekdays in repeat spec {spec!r}")
    return frozenset(days)


def _weekday(token: str) -> int:
    token = token.strip().lower()
    if token not in _WEEKDAY_INDEX:
        raise ParseError(f"unknown weekday {token!r}")
    return _WEEKDAY_INDEX[token]
