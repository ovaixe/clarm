"""clarm — a standard-library-only alarm clock for the command line.

The package is split into small, mostly-pure modules so the time-critical logic
(parsing, recurrence, due-calculation) can be unit-tested deterministically by
injecting ``now``:

- :mod:`clarm.paths`     — XDG-style file locations (``CLARM_HOME`` override).
- :mod:`clarm.timeparse` — parse alarm time / repeat specs (pure).
- :mod:`clarm.models`    — the :class:`~clarm.models.Alarm` record + ``next_fire`` (pure).
- :mod:`clarm.storage`   — lock-guarded, atomic JSON persistence.
- :mod:`clarm.sound`     — generate a WAV tone and play it via an available player.
- :mod:`clarm.daemon`    — the background watcher that actually rings alarms.
- :mod:`clarm.cli`       — the ``clarm`` command-line entry point.
"""

__version__ = "0.1.0"
