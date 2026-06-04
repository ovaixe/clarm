from datetime import datetime, time

from clarm import models as m
from clarm.timeparse import parse_repeat


THU_10AM = datetime(2026, 6, 4, 10, 0, 0)  # Thursday


# ---- once -----------------------------------------------------------------

def test_once_next_fire_then_done():
    target = datetime(2026, 6, 4, 23, 30)
    a = m.make_once(1, target, now=THU_10AM, label="bed")
    assert a.next_fire(THU_10AM) == target
    assert not a.is_due(THU_10AM)
    assert a.is_due(datetime(2026, 6, 4, 23, 31))

    a.mark_ringing(target)
    assert a.state == m.RINGING
    assert a.next_fire(THU_10AM) is None  # ringing -> not re-scheduled

    a.dismiss(datetime(2026, 6, 4, 23, 32))
    assert a.state == m.DONE
    assert a.next_fire(THU_10AM) is None  # finished forever


# ---- recurring ------------------------------------------------------------

def test_recurring_first_fire_is_after_creation():
    a = m.make_recurring(1, time(9, 0), parse_repeat("daily"), now=THU_10AM)
    # 09:00 already passed today (created 10:00) -> next is tomorrow 09:00.
    assert a.next_fire(THU_10AM) == datetime(2026, 6, 5, 9, 0)


def test_recurring_weekday_skips_weekend():
    # Friday 10:00; weekdays-only alarm at 09:00 -> next is Monday.
    fri = datetime(2026, 6, 5, 10, 0)
    a = m.make_recurring(1, time(9, 0), parse_repeat("mon-fri"), now=fri)
    assert a.next_fire(fri) == datetime(2026, 6, 8, 9, 0)  # Mon


def test_recurring_advances_after_dismiss():
    a = m.make_recurring(1, time(9, 0), parse_repeat("daily"), now=THU_10AM)
    occ = a.next_fire(THU_10AM)  # Fri 09:00
    a.mark_ringing(occ)
    a.dismiss(occ)
    assert a.state == m.SCHEDULED
    # Next occurrence advances to the following day.
    assert a.next_fire(occ) == datetime(2026, 6, 6, 9, 0)  # Sat


# ---- snooze ---------------------------------------------------------------

def test_snooze_sets_until_and_count():
    target = datetime(2026, 6, 4, 23, 30)
    a = m.make_once(1, target, now=THU_10AM)
    a.mark_ringing(target)
    a.snooze(target, minutes=9)
    assert a.state == m.SNOOZED
    assert a.snooze_count == 1
    assert a.next_fire(THU_10AM) == datetime(2026, 6, 4, 23, 39)
    assert a.is_due(datetime(2026, 6, 4, 23, 39))


def test_disabled_never_fires():
    a = m.make_recurring(1, time(9, 0), parse_repeat("daily"), now=THU_10AM)
    a.enabled = False
    assert a.next_fire(THU_10AM) is None


# ---- serialization --------------------------------------------------------

def test_roundtrip_once():
    a = m.make_once(7, datetime(2026, 6, 4, 23, 30), now=THU_10AM, label="x")
    b = m.Alarm.from_dict(a.to_dict())
    assert b.to_dict() == a.to_dict()


def test_roundtrip_recurring():
    a = m.make_recurring(7, time(7, 0), parse_repeat("mon,wed,fri"), now=THU_10AM, label="gym")
    b = m.Alarm.from_dict(a.to_dict())
    assert b.to_dict() == a.to_dict()
    assert b.days == a.days


def test_describe_schedule():
    once = m.make_once(1, datetime(2026, 6, 5, 7, 30), now=THU_10AM)
    assert once.describe_schedule() == "once @ 2026-06-05 07:30"
    rec = m.make_recurring(2, time(7, 0), parse_repeat("mon-fri"), now=THU_10AM)
    assert rec.describe_schedule() == "Mon-Fri @ 07:00"
