"""The :class:`Alarm` record and the pure scheduling logic around it.

Times are naive local ``datetime`` objects (no timezone), matching
:mod:`clarm.timeparse`. The two functions that matter for correctness —
:meth:`Alarm.next_fire` and the state transitions — take ``now`` as a parameter
so they can be unit-tested without touching the real clock.

State machine
-------------
``scheduled`` --(due)--> ``ringing`` --(dismiss)--> ``scheduled``/``done``
``ringing``  --(snooze)--> ``snoozed`` --(due)--> ``ringing``

A one-time alarm ends in ``done``; a recurring alarm returns to ``scheduled``
with ``last_fired`` advanced so the next occurrence is computed correctly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

ONCE = "once"
RECURRING = "recurring"

SCHEDULED = "scheduled"
RINGING = "ringing"
SNOOZED = "snoozed"
DONE = "done"

_ISO = "%Y-%m-%dT%H:%M:%S"


def _to_iso(dt: datetime | None) -> str | None:
    return dt.replace(microsecond=0).strftime(_ISO) if dt else None


def _from_iso(s: str | None) -> datetime | None:
    return datetime.strptime(s, _ISO) if s else None


@dataclass
class Alarm:
    """A single alarm.

    For ``once`` alarms, ``target`` holds the absolute fire time. For
    ``recurring`` alarms, ``time_of_day`` + ``days`` (Mon=0) define the
    schedule and ``target`` is unused.
    """

    id: int
    kind: str  # ONCE | RECURRING
    label: str = ""
    enabled: bool = True
    state: str = SCHEDULED

    # once
    target: datetime | None = None
    # recurring
    time_of_day: time | None = None
    days: frozenset[int] = field(default_factory=frozenset)

    # bookkeeping
    created_at: datetime | None = None
    last_fired: datetime | None = None  # the occurrence most recently rung
    snooze_until: datetime | None = None
    snooze_count: int = 0

    # ---- scheduling (pure) ------------------------------------------------

    def next_fire(self, now: datetime) -> datetime | None:
        """When this alarm should next *start* ringing, or ``None``.

        Returns ``None`` when the alarm is disabled, finished, or currently
        ringing (the daemon handles an in-progress ring separately). A snoozed
        alarm reports its ``snooze_until``. The returned time may be in the past
        relative to ``now`` (a missed/just-due alarm) — callers fire when
        ``now >= next_fire``.
        """
        if not self.enabled or self.state in (DONE, RINGING):
            return None
        if self.state == SNOOZED:
            return self.snooze_until

        if self.kind == ONCE:
            return None if self.last_fired is not None else self.target

        # recurring: earliest matching occurrence strictly after the last one
        after = self.last_fired or self.created_at or now
        return self._next_occurrence(after)

    def is_due(self, now: datetime) -> bool:
        nf = self.next_fire(now)
        return nf is not None and nf <= now

    def _next_occurrence(self, after: datetime) -> datetime | None:
        if not self.days or self.time_of_day is None:
            return None
        tod = self.time_of_day
        # Scan up to 8 days to be safe across week wrap.
        start_date = after.date()
        for offset in range(0, 8):
            d = start_date + timedelta(days=offset)
            if d.weekday() in self.days:
                cand = datetime.combine(d, tod)
                if cand > after:
                    return cand
        return None

    # ---- transitions (pure; caller supplies ``now``) ----------------------

    def mark_ringing(self, occurrence: datetime) -> None:
        """Begin ringing for the given occurrence (advances ``last_fired``)."""
        self.state = RINGING
        self.last_fired = occurrence

    def snooze(self, now: datetime, minutes: int) -> None:
        """Snooze a ringing alarm by ``minutes``."""
        self.state = SNOOZED
        self.snooze_until = (now + timedelta(minutes=minutes)).replace(microsecond=0)
        self.snooze_count += 1

    def dismiss(self, now: datetime) -> None:
        """Stop the alarm: finish a one-time alarm, reschedule a recurring one."""
        self.snooze_until = None
        self.snooze_count = 0
        if self.kind == ONCE:
            self.state = DONE
            if self.last_fired is None:
                self.last_fired = now.replace(microsecond=0)
        else:
            self.state = SCHEDULED

    # ---- display helper ---------------------------------------------------

    def describe_schedule(self) -> str:
        """Human-readable schedule, e.g. ``'once @ 2026-06-05 07:30'`` or
        ``'Mon-Fri @ 07:00'``."""
        if self.kind == ONCE:
            when = self.target.strftime("%Y-%m-%d %H:%M") if self.target else "?"
            return f"once @ {when}"
        tod = self.time_of_day.strftime("%H:%M") if self.time_of_day else "?"
        return f"{_format_days(self.days)} @ {tod}"

    # ---- serialization ----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "enabled": self.enabled,
            "state": self.state,
            "target": _to_iso(self.target),
            "time_of_day": self.time_of_day.strftime("%H:%M") if self.time_of_day else None,
            "days": sorted(self.days),
            "created_at": _to_iso(self.created_at),
            "last_fired": _to_iso(self.last_fired),
            "snooze_until": _to_iso(self.snooze_until),
            "snooze_count": self.snooze_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Alarm":
        tod = None
        if d.get("time_of_day"):
            h, m = d["time_of_day"].split(":")
            tod = time(int(h), int(m))
        return cls(
            id=d["id"],
            kind=d["kind"],
            label=d.get("label", ""),
            enabled=d.get("enabled", True),
            state=d.get("state", SCHEDULED),
            target=_from_iso(d.get("target")),
            time_of_day=tod,
            days=frozenset(d.get("days", [])),
            created_at=_from_iso(d.get("created_at")),
            last_fired=_from_iso(d.get("last_fired")),
            snooze_until=_from_iso(d.get("snooze_until")),
            snooze_count=d.get("snooze_count", 0),
        )


_DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _format_days(days: frozenset[int]) -> str:
    if not days:
        return "never"
    s = set(days)
    if s == set(range(7)):
        return "Daily"
    if s == set(range(5)):
        return "Mon-Fri"
    if s == {5, 6}:
        return "Sat-Sun"
    return ",".join(_DAY_ABBR[i] for i in sorted(s))


def make_once(id: int, target: datetime, now: datetime, label: str = "") -> Alarm:
    return Alarm(
        id=id, kind=ONCE, label=label, target=target.replace(microsecond=0),
        created_at=now.replace(microsecond=0),
    )


def make_recurring(
    id: int, tod: time, days: frozenset[int], now: datetime, label: str = ""
) -> Alarm:
    # Seed last_fired with creation time so the first ring is the next
    # occurrence *after* creation (never the same minute it was created).
    created = now.replace(microsecond=0)
    return Alarm(
        id=id, kind=RECURRING, label=label, time_of_day=tod, days=days,
        created_at=created, last_fired=created,
    )
