"""The background watcher that actually rings alarms.

Design in one breath: the JSON store is the source of truth; the daemon sleeps
until the soonest ``next_fire`` using a self-pipe + :func:`select.select` (so a
``SIGHUP`` from the CLI can wake it the instant an alarm is added or a ring is
snoozed/dismissed), rings due alarms one at a time, and persists every state
change back to the store.

Because a daemon has no terminal, snooze/dismiss are not keypresses: the CLI
mutates the alarm in the store and sends ``SIGHUP``; the daemon reloads mid-ring
and reacts. If nobody responds within ``ring_timeout`` the ring auto-dismisses
so it never sounds forever.

Lifecycle helpers (:func:`start`, :func:`stop`, :func:`status`) manage a
double-forked process and a pidfile. ``run_foreground`` is the same loop without
detaching — used by ``clarm daemon run`` and by tests.
"""

from __future__ import annotations

import errno
import os
import select
import signal
import sys
import time as _time
from datetime import datetime

from clarm import config, paths, storage
from clarm.models import RINGING
from clarm.sound import Ringer, notify

# Cap on how long we ever sleep, so a missed wake-up or clock change self-heals
# and the store is re-read periodically even without a signal.
_MAX_SLEEP = 30.0


# --------------------------------------------------------------------------
# pidfile helpers
# --------------------------------------------------------------------------

def read_pid() -> int | None:
    p = paths.pid_path()
    try:
        return int(p.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as e:
        return e.errno == errno.EPERM  # exists but not ours
    return True


def is_running() -> int | None:
    """Return the live daemon pid, or ``None`` (cleaning up a stale pidfile)."""
    pid = read_pid()
    if pid is None:
        return None
    if _alive(pid):
        return pid
    try:
        paths.pid_path().unlink()
    except OSError:
        pass
    return None


def signal_daemon(sig: int = signal.SIGHUP) -> bool:
    """Send ``sig`` to a running daemon. Returns False if none is running."""
    pid = is_running()
    if pid is None:
        return False
    try:
        os.kill(pid, sig)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------

class _Daemon:
    def __init__(self):
        self._terminate = False
        self._rfd = -1
        self._wfd = -1

    # -- signal plumbing (self-pipe) ------------------------------------
    def _install_signals(self):
        self._rfd, self._wfd = os.pipe()
        os.set_blocking(self._rfd, False)
        os.set_blocking(self._wfd, False)
        # set_wakeup_fd makes Python write the signal number to _wfd whenever a
        # handled signal arrives, which is what breaks us out of select().
        signal.set_wakeup_fd(self._wfd)
        signal.signal(signal.SIGHUP, self._on_wake)
        signal.signal(signal.SIGTERM, self._on_term)
        signal.signal(signal.SIGINT, self._on_term)

    def _on_wake(self, signum, frame):
        pass  # the wakeup byte is enough; loop will re-read the store

    def _on_term(self, signum, frame):
        self._terminate = True

    def _wait(self, timeout: float) -> None:
        """Sleep up to ``timeout`` seconds, returning early on any signal."""
        if timeout <= 0:
            timeout = 0
        try:
            r, _, _ = select.select([self._rfd], [], [], timeout)
        except (InterruptedError, OSError):
            return
        if r:
            try:  # drain the pipe
                while os.read(self._rfd, 4096):
                    pass
            except (BlockingIOError, OSError):
                pass

    # -- main loop ------------------------------------------------------
    def run(self) -> int:
        self._install_signals()
        log(f"daemon started (pid {os.getpid()})")
        try:
            while not self._terminate:
                now = datetime.now()
                alarms = storage.load()

                target = self._pick_to_ring(alarms, now)
                if target is not None:
                    self._ring(target)
                    continue

                self._wait(self._sleep_for(alarms, now))
        finally:
            log("daemon stopping")
        return 0

    def _pick_to_ring(self, alarms, now):
        """Return an alarm id that should ring now, or None."""
        # Resume an in-progress ring first (e.g. after a daemon restart).
        for a in alarms:
            if a.enabled and a.state == RINGING:
                return a.id
        due = [a for a in alarms if a.is_due(now)]
        if not due:
            return None
        due.sort(key=lambda a: a.next_fire(now))
        return due[0].id

    def _sleep_for(self, alarms, now) -> float:
        upcoming = [a.next_fire(now) for a in alarms]
        upcoming = [t for t in upcoming if t is not None and t > now]
        if not upcoming:
            return _MAX_SLEEP
        secs = (min(upcoming) - now).total_seconds()
        return max(0.0, min(secs, _MAX_SLEEP))

    # -- a single ring session -----------------------------------------
    def _ring(self, alarm_id: int) -> None:
        now = datetime.now()

        # Mark ringing in the store (unless already marked from a prior cycle).
        def _begin(alarms):
            a = storage.get(alarms, alarm_id)
            if a is None:
                return None
            if a.state != RINGING:
                occ = a.next_fire(now) or now
                a.mark_ringing(occ)
            return a.label

        label = storage.update(_begin)
        if label is None:
            return  # alarm vanished

        log(f"RING alarm {alarm_id}" + (f" — {label}" if label else ""))
        notify("Alarm" + (f": {label}" if label else ""), "clarm — snooze or dismiss")

        ringer = Ringer()
        ringer.start()
        deadline = _time.monotonic() + config.ring_timeout()
        try:
            while not self._terminate:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    self._auto_dismiss(alarm_id)
                    log(f"alarm {alarm_id} auto-dismissed after timeout")
                    return
                self._wait(min(remaining, 5.0))
                # Re-read: did the CLI snooze/dismiss us?
                a = storage.get(storage.load(), alarm_id)
                if a is None or a.state != RINGING:
                    log(f"alarm {alarm_id} -> {a.state if a else 'removed'}")
                    return
        finally:
            ringer.stop()

    def _auto_dismiss(self, alarm_id: int) -> None:
        now = datetime.now()

        def _do(alarms):
            a = storage.get(alarms, alarm_id)
            if a is not None and a.state == RINGING:
                a.dismiss(now)

        storage.update(_do)


def run_foreground() -> int:
    return _Daemon().run()


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------

def log(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}"
    # In the daemon, stdout is the logfile; otherwise also tee to the file.
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        pass


# --------------------------------------------------------------------------
# lifecycle (double-fork)
# --------------------------------------------------------------------------

def start(wait_seconds: float = 3.0) -> tuple[bool, str]:
    """Start the daemon detached. Returns ``(ok, message)``."""
    pid = is_running()
    if pid is not None:
        return False, f"daemon already running (pid {pid})"

    # First fork: the CLI keeps running as the parent.
    child = os.fork()
    if child > 0:
        os.waitpid(child, 0)  # reap the intermediate exit below
        # Poll for the grandchild to write its pidfile.
        deadline = _time.monotonic() + wait_seconds
        while _time.monotonic() < deadline:
            running = is_running()
            if running is not None:
                return True, f"daemon started (pid {running})"
            _time.sleep(0.05)
        return False, "daemon did not come up in time (check the log)"

    # Intermediate child: detach into a new session, then fork the grandchild.
    os.setsid()
    grandchild = os.fork()
    if grandchild > 0:
        os._exit(0)  # intermediate exits immediately; reaped by the CLI

    # Grandchild: this is the daemon.
    _detach_io()
    paths.pid_path().write_text(str(os.getpid()))
    try:
        rc = run_foreground()
    finally:
        try:
            paths.pid_path().unlink()
        except OSError:
            pass
    os._exit(rc)


def _detach_io() -> None:
    os.chdir("/")
    log_file = open(paths.log_path(), "a", buffering=1)
    devnull = open(os.devnull, "r")
    os.dup2(devnull.fileno(), sys.stdin.fileno())
    os.dup2(log_file.fileno(), sys.stdout.fileno())
    os.dup2(log_file.fileno(), sys.stderr.fileno())


def stop(wait_seconds: float = 5.0) -> tuple[bool, str]:
    pid = is_running()
    if pid is None:
        return False, "daemon is not running"
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        return False, f"could not signal daemon: {e}"
    deadline = _time.monotonic() + wait_seconds
    while _time.monotonic() < deadline:
        if is_running() is None:
            return True, f"daemon stopped (pid {pid})"
        _time.sleep(0.05)
    return False, f"daemon (pid {pid}) did not stop in time"


def status() -> str:
    pid = is_running()
    if pid is None:
        return "daemon: stopped"
    return f"daemon: running (pid {pid})"
