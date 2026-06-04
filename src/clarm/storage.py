"""Persistence for alarms: a single JSON file, read and written safely.

Both the CLI and the daemon touch the same file, so writes must not interleave
or leave a truncated file behind. We get that with two stdlib mechanisms:

- an advisory ``fcntl.flock`` on a sidecar lock file serialises whole
  read-modify-write cycles between processes, and
- writes go to a temp file in the same directory and are then ``os.replace``-d
  over the target, which is atomic on POSIX.

The public surface is small:

    load() -> list[Alarm]
    save(alarms)
    update(fn) -> result        # locked read-modify-write; fn(alarms) mutates
    add(alarm_factory) -> Alarm  # allocates the next id and appends
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from typing import Callable

from clarm import paths
from clarm.models import Alarm

_SCHEMA = 1


@contextmanager
def _locked():
    """Hold an exclusive cross-process lock for the duration of the block."""
    import fcntl

    lock_file = paths.data_dir() / ".alarms.lock"
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_unlocked() -> list[Alarm]:
    path = paths.store_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        # A corrupt/partial file should not crash the CLI; treat as empty.
        return []
    return [Alarm.from_dict(d) for d in raw.get("alarms", [])]


def _write_unlocked(alarms: list[Alarm]) -> None:
    path = paths.store_path()
    payload = {"schema": _SCHEMA, "alarms": [a.to_dict() for a in alarms]}
    data = json.dumps(payload, indent=2, sort_keys=False)
    # Atomic replace: write to a temp file in the same dir, fsync, rename.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".alarms.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load() -> list[Alarm]:
    """Return all alarms (taking the lock briefly for a consistent read)."""
    with _locked():
        return _read_unlocked()


def save(alarms: list[Alarm]) -> None:
    """Replace the whole store with ``alarms`` (locked, atomic)."""
    with _locked():
        _write_unlocked(alarms)


def update(fn: Callable[[list[Alarm]], object]):
    """Run a locked read-modify-write.

    ``fn`` receives the current list, may mutate it in place (or return a new
    list), and its return value is passed back to the caller. The (possibly
    mutated) list is then persisted atomically. This is the primitive every CLI
    mutation and the daemon use so concurrent changes never clobber each other.
    """
    with _locked():
        alarms = _read_unlocked()
        result = fn(alarms)
        _write_unlocked(alarms)
        return result


def next_id(alarms: list[Alarm]) -> int:
    return (max((a.id for a in alarms), default=0)) + 1


def add(make: Callable[[int], Alarm]) -> Alarm:
    """Allocate the next free id, build an alarm with it, append and persist."""
    def _do(alarms: list[Alarm]) -> Alarm:
        alarm = make(next_id(alarms))
        alarms.append(alarm)
        return alarm

    return update(_do)


def get(alarms: list[Alarm], alarm_id: int) -> Alarm | None:
    for a in alarms:
        if a.id == alarm_id:
            return a
    return None
