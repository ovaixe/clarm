"""Filesystem locations used by clarm.

All paths derive from a single base directory so tests (and users who want an
isolated instance) can redirect everything by setting ``CLARM_HOME``:

    CLARM_HOME=/tmp/clarm python -m clarm list

Without the override we follow the XDG Base Directory spec: persistent alarm
data lives under ``$XDG_DATA_HOME`` and runtime files (pid, log, control pipe)
under ``$XDG_STATE_HOME``. When ``CLARM_HOME`` is set, *both* live under it so a
test gets a fully self-contained sandbox.
"""

from __future__ import annotations

import os
from pathlib import Path

_APP = "clarm"


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def data_dir() -> Path:
    """Directory holding persistent state (the alarm store)."""
    override = os.environ.get("CLARM_HOME")
    if override:
        base = Path(override)
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) / _APP if xdg else _home() / ".local" / "share" / _APP
    base.mkdir(parents=True, exist_ok=True)
    return base


def runtime_dir() -> Path:
    """Directory holding ephemeral daemon files (pid, log, control pipe)."""
    override = os.environ.get("CLARM_HOME")
    if override:
        base = Path(override) / "run"
    else:
        xdg = os.environ.get("XDG_STATE_HOME")
        base = Path(xdg) / _APP if xdg else _home() / ".local" / "state" / _APP
    base.mkdir(parents=True, exist_ok=True)
    return base


def store_path() -> Path:
    """Path to the JSON file containing all alarms."""
    return data_dir() / "alarms.json"


def pid_path() -> Path:
    """Path to the daemon pidfile."""
    return runtime_dir() / "daemon.pid"


def log_path() -> Path:
    """Path to the daemon log file."""
    return runtime_dir() / "daemon.log"


def tone_path() -> Path:
    """Path to the cached generated WAV tone."""
    return runtime_dir() / "tone.wav"
