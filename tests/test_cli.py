"""CLI dispatch tests against an isolated CLARM_HOME (no real daemon).

The daemon is never started here; ``_poke_daemon`` just finds no pidfile and
returns, so these exercise the parsing + storage path end-to-end.
"""

from datetime import datetime, time

from clarm import cli, storage
from clarm import models as m
from clarm.timeparse import parse_repeat


def run(argv):
    return cli.main(argv)


def test_add_oneshot_relative(clarm_home, capsys):
    assert run(["add", "in", "30m", "--label", "tea"]) == 0
    out = capsys.readouterr().out
    assert "Added alarm 1" in out and "tea" in out
    alarms = storage.load()
    assert len(alarms) == 1 and alarms[0].kind == m.ONCE


def test_add_recurring(clarm_home, capsys):
    assert run(["add", "07:00", "--repeat", "mon-fri", "-l", "work"]) == 0
    a = storage.load()[0]
    assert a.kind == m.RECURRING
    assert a.days == parse_repeat("mon-fri")
    assert a.time_of_day == time(7, 0)


def test_add_rejects_garbage(clarm_home, capsys):
    assert run(["add", "banana"]) == 2
    assert "could not understand" in capsys.readouterr().err


def test_list_empty_then_populated(clarm_home, capsys):
    assert run(["list"]) == 0
    assert "No alarms" in capsys.readouterr().out

    run(["add", "23:30", "-l", "bed"])
    capsys.readouterr()
    assert run(["list"]) == 0
    out = capsys.readouterr().out
    assert "bed" in out and "ID" in out and "SCHEDULE" in out


def test_rm(clarm_home, capsys):
    run(["add", "23:30"])
    run(["add", "23:35"])
    capsys.readouterr()
    assert run(["rm", "1"]) == 0
    assert [a.id for a in storage.load()] == [2]
    # removing nothing returns non-zero
    assert run(["rm", "99"]) == 1


def test_enable_disable(clarm_home, capsys):
    run(["add", "23:30"])
    capsys.readouterr()
    assert run(["disable", "1"]) == 0
    assert storage.load()[0].enabled is False
    assert run(["enable", "1"]) == 0
    assert storage.load()[0].enabled is True
    assert run(["disable", "42"]) == 1  # unknown id


def test_snooze_requires_ringing(clarm_home, capsys):
    run(["add", "23:30"])
    capsys.readouterr()
    assert run(["snooze"]) == 1
    assert "Nothing is ringing" in capsys.readouterr().err


def _make_ringing(clarm_home):
    now = datetime(2026, 6, 4, 23, 30)
    storage.add(lambda i: m.make_once(i, now, now, "wake"))

    def _ring(alarms):
        alarms[0].mark_ringing(now)

    storage.update(_ring)
    return now


def test_snooze_then_dismiss_a_ringing_alarm(clarm_home, capsys):
    _make_ringing(clarm_home)

    assert run(["snooze", "5m"]) == 0
    a = storage.load()[0]
    assert a.state == m.SNOOZED and a.snooze_count == 1
    capsys.readouterr()

    # snoozed alarm can be dismissed
    assert run(["dismiss"]) == 0
    assert storage.load()[0].state == m.DONE


def test_snooze_limit_enforced(clarm_home, capsys, monkeypatch):
    monkeypatch.setenv("CLARM_MAX_SNOOZES", "1")
    _make_ringing(clarm_home)

    assert run(["snooze"]) == 0
    # re-ring and try to snooze again past the limit
    def _ring(alarms):
        alarms[0].mark_ringing(datetime(2026, 6, 4, 23, 39))
    storage.update(_ring)
    capsys.readouterr()
    assert run(["snooze"]) == 1
    assert "snooze limit" in capsys.readouterr().err


def test_daemon_status_when_stopped(clarm_home, capsys):
    assert run(["daemon", "status"]) == 0
    assert "stopped" in capsys.readouterr().out
