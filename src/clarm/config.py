"""Tunable defaults, overridable via environment variables.

Keeping these in one place (rather than a config file) keeps the project
dependency-free and easy to reason about; power users can still override any of
them per-invocation, e.g. ``CLARM_SNOOZE_MINUTES=5``.
"""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def snooze_minutes() -> int:
    """Default snooze length when ``clarm snooze`` is called without a value."""
    return _int("CLARM_SNOOZE_MINUTES", 9)


def max_snoozes() -> int:
    """How many times a single ring may be snoozed before dismiss is forced."""
    return _int("CLARM_MAX_SNOOZES", 3)


def ring_timeout() -> int:
    """Seconds to keep ringing with no response before auto-dismissing."""
    return _int("CLARM_RING_TIMEOUT", 120)
