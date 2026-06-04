# clarm — a command-line alarm clock

`clarm` is a small alarm clock for your terminal. A background **daemon** watches
your alarms and rings them — playing a tone and posting a desktop notification —
even when no terminal is open. You manage alarms and respond to rings with quick
CLI commands.

- **Zero dependencies.** Pure Python standard library (3.11+). The alarm tone is
  *generated* at runtime with the `wave` module; sound is played through whatever
  is installed (`paplay`/`aplay`/`ffplay`/`cvlc`), falling back to the terminal bell.
- **One-time and recurring alarms**, snooze, labels, enable/disable.
- **Survives restarts** — alarms persist to a JSON file.
- Linux / POSIX (the daemon uses `fork`, `setsid` and POSIX signals).

## Install

```bash
pip install -e .          # provides the `clarm` command
# …or run without installing:
python -m clarm --help
```

## Quick start

```bash
clarm daemon start                 # start the background watcher
clarm add 07:30 --label "Wake up"  # one-time, next 07:30
clarm add in 10m                   # one-time, 10 minutes from now
clarm add 07:00 --repeat mon-fri   # recurring, weekday mornings
clarm list                         # see everything
```

When an alarm rings, from **any** terminal:

```bash
clarm snooze        # snooze for the default 9 minutes
clarm snooze 5m     # …or a custom amount
clarm dismiss       # stop it (recurring alarms re-arm for next time)
```

Manage the daemon:

```bash
clarm daemon status
clarm daemon stop
clarm daemon run    # run in the foreground (great for debugging; Ctrl-C to quit)
```

## Time formats

| Kind        | Examples                                             |
|-------------|------------------------------------------------------|
| Relative    | `in 30m`, `90s`, `1h30m`, `2 hours`                  |
| Time of day | `07:30` (the next time that clock-time occurs)       |
| Absolute    | `2026-06-05 07:30`, `2026-06-05T07:30`               |
| Repeat      | `daily`, `weekdays`, `weekends`, `mon-fri`, `mon,wed,fri` |

## How it works

```
  CLI (clarm add/snooze/…) ──writes──▶  alarms.json  ◀──reads/writes── daemon
            │                          (lock + atomic)                    │
            └──────────── SIGHUP "re-evaluate now" ──────────────────────▶┘
```

The JSON store is the single source of truth. The CLI mutates it under an
`flock` + atomic-replace, then sends the daemon `SIGHUP`. The daemon computes the
soonest `next_fire`, sleeps until then via a **self-pipe + `select`** (so signals
wake it instantly), rings due alarms, and writes every state change back.

Because a daemon has no terminal, **snooze/dismiss are CLI commands** rather than
keypresses: the CLI flips the ringing alarm's state in the store and signals the
daemon, which reloads mid-ring and reacts. A ring auto-dismisses after
`ring_timeout` so it never sounds forever.

### Layout

| Module               | Responsibility                                           |
|----------------------|----------------------------------------------------------|
| `clarm/timeparse.py` | parse time / repeat specs (pure)                         |
| `clarm/models.py`    | `Alarm` record + `next_fire`, snooze/dismiss (pure)      |
| `clarm/storage.py`   | lock-guarded, atomic JSON persistence                    |
| `clarm/sound.py`     | generate a WAV tone; play it; desktop notification       |
| `clarm/daemon.py`    | double-fork lifecycle, self-pipe loop, ring orchestration|
| `clarm/cli.py`       | the `clarm` command                                      |
| `clarm/paths.py`     | XDG file locations (`CLARM_HOME` override)               |

## Configuration

Environment overrides (all optional):

| Variable                | Meaning                                  | Default |
|-------------------------|------------------------------------------|---------|
| `CLARM_SNOOZE_MINUTES`  | default snooze length                    | `9`     |
| `CLARM_MAX_SNOOZES`     | snoozes allowed per ring before dismiss  | `3`     |
| `CLARM_RING_TIMEOUT`    | seconds before an unanswered ring stops  | `120`   |
| `CLARM_HOME`            | put store + runtime files under one dir  | XDG dirs|

Files live under `~/.local/share/clarm/` (alarms) and `~/.local/state/clarm/`
(pid, log, tone) unless `CLARM_HOME` is set.

## Tests

```bash
pip install -e '.[dev]'   # installs pytest
pytest                    # 65 tests: parsing, recurrence, storage, CLI dispatch
```

The time-critical logic is pure and `now` is always injected, so the suite is
deterministic and needs neither sound hardware nor a running daemon.

## Limitations / possible extensions

- POSIX-only daemon (no Windows `fork`).
- Rings one alarm at a time; a second due alarm waits for the first.
- Single built-in tone. Natural extensions: per-alarm sound files, a countdown
  `timer` subcommand, fade-in volume, and `systemd`/`cron` integration so alarms
  survive reboots without a resident process.
