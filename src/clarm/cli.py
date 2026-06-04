"""The ``clarm`` command-line interface.

Subcommands fall into three groups:

- *editing*   — ``add``, ``rm``, ``enable``, ``disable``
- *ring ctrl* — ``snooze``, ``dismiss`` (act on whatever is ringing now)
- *daemon*    — ``daemon start|stop|status|run`` and ``list`` (read-only)

Every editing/ring command persists through :mod:`clarm.storage` and then pokes
the daemon with ``SIGHUP`` so it re-evaluates immediately (a no-op if the daemon
is not running).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from clarm import __version__, config, daemon, storage
from clarm import models as m
from clarm import timeparse as tp


def _poke_daemon() -> None:
    daemon.signal_daemon()  # SIGHUP; False/ignored if not running


# --------------------------------------------------------------------------
# command handlers (each returns a process exit code)
# --------------------------------------------------------------------------

def cmd_add(args) -> int:
    now = datetime.now()
    spec = " ".join(args.time).strip()
    label = args.label or ""

    try:
        if args.repeat:
            tod = tp.parse_time_of_day(spec)
            days = tp.parse_repeat(args.repeat)
            alarm = storage.add(lambda i: m.make_recurring(i, tod, days, now, label))
        else:
            target = tp.parse_oneshot(spec, now)
            alarm = storage.add(lambda i: m.make_once(i, target, now, label))
    except tp.ParseError as e:
        print(f"clarm: {e}", file=sys.stderr)
        return 2

    _poke_daemon()
    nf = alarm.next_fire(now)
    when = nf.strftime("%a %Y-%m-%d %H:%M") if nf else "?"
    print(f"Added alarm {alarm.id}: {alarm.describe_schedule()} (next: {when})"
          + (f" — {label}" if label else ""))
    if daemon.is_running() is None:
        print("note: the daemon is not running; start it with `clarm daemon start`")
    return 0


def cmd_list(args) -> int:
    now = datetime.now()
    alarms = storage.load()
    if not alarms:
        print("No alarms. Add one with `clarm add 07:30` or `clarm add in 10m`.")
        return 0

    rows = []
    for a in sorted(alarms, key=lambda x: x.id):
        nf = a.next_fire(now)
        nxt = _format_next(nf, now) if nf else ("—" if a.enabled else "disabled")
        flag = a.state if a.state != m.SCHEDULED else ("on" if a.enabled else "off")
        rows.append((str(a.id), flag, a.describe_schedule(), nxt, a.label or ""))

    headers = ("ID", "STATE", "SCHEDULE", "NEXT", "LABEL")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    for r in rows:
        print(fmt.format(*r))
    return 0


def _format_next(nf: datetime, now: datetime) -> str:
    delta = nf - now
    secs = int(delta.total_seconds())
    if secs < 0:
        return "due"
    if secs < 3600:
        return f"in {secs // 60}m{secs % 60:02d}s" if secs < 600 else f"in {secs // 60}m"
    if nf.date() == now.date():
        return nf.strftime("today %H:%M")
    return nf.strftime("%a %H:%M")


def cmd_rm(args) -> int:
    ids = set(args.id)

    def _do(alarms):
        before = len(alarms)
        alarms[:] = [a for a in alarms if a.id not in ids]
        return before - len(alarms)

    removed = storage.update(_do)
    _poke_daemon()
    print(f"Removed {removed} alarm(s)." if removed else "No matching alarms.")
    return 0 if removed else 1


def _set_enabled(alarm_id: int, enabled: bool) -> int:
    def _do(alarms):
        a = storage.get(alarms, alarm_id)
        if a is None:
            return False
        a.enabled = enabled
        if not enabled and a.state in (m.RINGING, m.SNOOZED):
            a.dismiss(datetime.now())
        return True

    ok = storage.update(_do)
    _poke_daemon()
    if not ok:
        print(f"clarm: no alarm with id {alarm_id}", file=sys.stderr)
        return 1
    print(f"Alarm {alarm_id} {'enabled' if enabled else 'disabled'}.")
    return 0


def cmd_enable(args) -> int:
    return _set_enabled(args.id, True)


def cmd_disable(args) -> int:
    return _set_enabled(args.id, False)


def cmd_snooze(args) -> int:
    now = datetime.now()
    minutes = config.snooze_minutes()
    if args.duration:
        try:
            minutes = max(1, round(tp.parse_duration(" ".join(args.duration)).total_seconds() / 60))
        except tp.ParseError as e:
            print(f"clarm: {e}", file=sys.stderr)
            return 2

    result = {"status": "none"}

    def _do(alarms):
        a = next((x for x in alarms if x.state == m.RINGING), None)
        if a is None:
            return
        if a.snooze_count >= config.max_snoozes():
            result["status"] = "maxed"
            result["id"] = a.id
            return
        a.snooze(now, minutes)
        result["status"] = "ok"
        result["id"] = a.id
        result["until"] = a.snooze_until

    storage.update(_do)
    _poke_daemon()

    if result["status"] == "none":
        print("Nothing is ringing.", file=sys.stderr)
        return 1
    if result["status"] == "maxed":
        print(f"Alarm {result['id']} hit the snooze limit "
              f"({config.max_snoozes()}). Use `clarm dismiss`.", file=sys.stderr)
        return 1
    print(f"Snoozed alarm {result['id']} for {minutes}m "
          f"(until {result['until'].strftime('%H:%M')}).")
    return 0


def cmd_dismiss(args) -> int:
    now = datetime.now()
    result = {"id": None}

    def _do(alarms):
        a = next((x for x in alarms if x.state in (m.RINGING, m.SNOOZED)), None)
        if a is None:
            return
        a.dismiss(now)
        result["id"] = a.id

    storage.update(_do)
    _poke_daemon()
    if result["id"] is None:
        print("Nothing to dismiss.", file=sys.stderr)
        return 1
    print(f"Dismissed alarm {result['id']}.")
    return 0


def cmd_daemon(args) -> int:
    action = args.action
    if action == "run":
        return daemon.run_foreground()
    if action == "start":
        ok, msg = daemon.start()
        print(msg, file=sys.stderr if not ok else sys.stdout)
        return 0 if ok else 1
    if action == "stop":
        ok, msg = daemon.stop()
        print(msg, file=sys.stderr if not ok else sys.stdout)
        return 0 if ok else 1
    if action == "status":
        print(daemon.status())
        return 0
    return 2


# --------------------------------------------------------------------------
# argument parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="clarm",
        description="A command-line alarm clock with a background daemon.",
    )
    p.add_argument("--version", action="version", version=f"clarm {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pa = sub.add_parser("add", help="add an alarm")
    pa.add_argument("time", nargs="+",
                    help="HH:MM, 'in 30m', '90s', '1h30m', or 'YYYY-MM-DD HH:MM'")
    pa.add_argument("-l", "--label", help="optional label/message")
    pa.add_argument("-r", "--repeat",
                    help="recurrence: daily, weekdays, weekends, mon-fri, mon,wed,fri")
    pa.set_defaults(func=cmd_add)

    pl = sub.add_parser("list", help="list alarms")
    pl.set_defaults(func=cmd_list)

    pr = sub.add_parser("rm", help="remove alarm(s) by id")
    pr.add_argument("id", nargs="+", type=int)
    pr.set_defaults(func=cmd_rm)

    pe = sub.add_parser("enable", help="enable an alarm")
    pe.add_argument("id", type=int)
    pe.set_defaults(func=cmd_enable)

    pd = sub.add_parser("disable", help="disable an alarm")
    pd.add_argument("id", type=int)
    pd.set_defaults(func=cmd_disable)

    ps = sub.add_parser("snooze", help="snooze the currently ringing alarm")
    ps.add_argument("duration", nargs="*", help="e.g. 5m (default from config)")
    ps.set_defaults(func=cmd_snooze)

    pdis = sub.add_parser("dismiss", help="dismiss the currently ringing alarm")
    pdis.set_defaults(func=cmd_dismiss)

    pdaemon = sub.add_parser("daemon", help="manage the background daemon")
    pdaemon.add_argument("action", choices=["start", "stop", "status", "run"])
    pdaemon.set_defaults(func=cmd_daemon)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
