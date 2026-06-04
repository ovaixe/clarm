# clarm — Planning Document

> Requirements, design, and implementation plan for a command-line alarm clock.
> This is the up-front thinking that drove the build; the shipped code matches it.

---

## 1. Context & brief

**Task:** build an alarm clock as a **CLI-only** Python application

**Two environment facts shaped the whole design:**

1. **pip / network is blocked.** → The app uses the **Python standard library
   only**, zero third-party runtime dependencies. This is a hard constraint, and
   it also makes the deliverable runnable anywhere Python 3.11+ exists.
2. **Audio is available** (`paplay`, `aplay`, `ffplay`, `cvlc`, plus the terminal
   bell). → Alarms can genuinely make sound, with graceful fallback.

**Decisions confirmed up front (the meaningful forks):**

| Question | Choice | Why |
|----------|--------|-----|
| How does it ring? | **Background daemon** | Alarms must fire even when no terminal is open — that's what an alarm clock *is*. |
| Feature scope | **One-time + recurring, with snooze** | The core of a real alarm clock; timers and multi-sound were deferred. |
| Rigor | **Tests + packaging** | It's an assignment: a `pyproject.toml`, `clarm` entry point, pytest suite, and README. |

### The one design consequence worth stating loudly

A daemon has **no controlling terminal**, so it cannot read a keypress to snooze.
Therefore **snooze/dismiss are CLI commands**, not keypresses: the ringing state
lives in the shared store; the daemon rings (sound + desktop notification) and
marks the alarm `ringing`; the user runs `clarm snooze` / `clarm dismiss` from any
terminal to act on it. This is a clean fit for the daemon/CLI split and is fully
testable.

---

## 2. Requirements

### 2.1 Functional

- **Add alarms**
  - One-time, absolute: `clarm add 07:30`, `clarm add "2026-06-05 07:30"`.
  - One-time, relative: `clarm add in 30m`, `clarm add 90s`, `clarm add 1h30m`.
  - Recurring: `clarm add 07:00 --repeat daily | weekdays | mon-fri | mon,wed,fri`.
  - Optional `--label "Standup"` per alarm.
- **Manage alarms:** `list`, `rm <id...>`, `enable <id>`, `disable <id>`.
- **Respond to a ring:** `snooze [<duration>]` (default 9m), `dismiss`.
- **Daemon lifecycle:** `daemon start | stop | status | run` (`run` = foreground).
- **Ring behaviour:** play a looping tone, post a desktop notification if
  `notify-send` exists, and **auto-dismiss after a timeout** (default 120s) so it
  never rings forever — one-time → done, recurring → next occurrence.
- **Snooze:** reschedule the ringing alarm to `now + N`, capped by `max_snoozes`
  (default 3); past the cap, dismiss is required.
- **Persistence:** alarms survive restarts (a JSON file on disk).

### 2.2 Non-functional

- **Standard library only;** Python 3.11+.
- **Safe concurrent writes:** CLI and daemon share one file → `flock` advisory lock
  + atomic `os.replace`.
- **Deterministic core:** all time logic (parsing, recurrence, due-calculation) is
  pure and takes `now` as a parameter, so it's unit-tested without the real clock.
- **Linux / POSIX** (uses `fork`, `setsid`, POSIX signals); documented as such.

### 2.3 Out of scope (explicit)

Countdown timer, multiple custom sounds per alarm, OS-scheduler/cron integration,
Windows daemonization. Listed in the README as natural extensions.

---

## 3. Design

### 3.1 Process model

```
  CLI (clarm add/snooze/…) ──writes──▶  alarms.json  ◀──reads/writes── daemon
            │                          (flock + atomic)                   │
            └──────────── SIGHUP "re-evaluate now" ─────────────────────▶ ┘
```

- **The JSON store is the single source of truth.** Both processes read/write it
  under an exclusive `flock`, and every write goes to a temp file then
  `os.replace`-d over the target (atomic on POSIX) — no torn reads, no clobbering.
- **CLI** mutates the store, then sends the daemon `SIGHUP` to re-evaluate
  immediately. If the daemon isn't running, the alarm simply waits until it is.
- **Daemon** computes the soonest fire time and sleeps until then using a
  **self-pipe + `select`** pattern: a signal handler writes a byte to a pipe, and
  the main loop `select`s on that pipe with `timeout = seconds-until-next-fire`.
  This avoids the classic signal-during-sleep race and is pure stdlib.
  `SIGHUP` → reload/re-evaluate; `SIGTERM`/`SIGINT` → graceful shutdown.

### 3.2 Data model — `Alarm`

`id` (small incrementing int, friendly for `clarm rm 3`), `label`, `kind`
(`once` | `recurring`), `target` (absolute datetime for once), `time_of_day` +
`days` (weekday set, Mon=0, for recurring), `enabled`, `state`
(`scheduled` | `ringing` | `snoozed` | `done`), `last_fired`, `snooze_until`,
`snooze_count`, `created_at`. Serialised to/from JSON.

**State machine:**

```
scheduled ──(due)──▶ ringing ──(dismiss)──▶ scheduled (recurring) / done (once)
                       │
                  (snooze)
                       ▼
                    snoozed ──(due)──▶ ringing
```

### 3.3 Scheduling logic (pure, the testable heart)

`Alarm.next_fire(now) -> datetime | None` answers "when does this next ring?":

- `once`: the stored `target`, or `None` once it has fired (→ `done`).
- `recurring`: the next datetime matching `days` at `time_of_day`, scanning
  forward from `last_fired`. Seeded so the **first** ring is the next occurrence
  *after* creation (never the same minute it was created).
- `snoozed`: `snooze_until` overrides everything.
- disabled / ringing → `None` (a ring in progress is handled separately).

A recurring alarm advances correctly because `mark_ringing(occurrence)` sets
`last_fired = occurrence`, so the next computation skips past it.

### 3.4 Sound (no assets, no deps)

The tone is **generated at runtime** with stdlib `wave` + `math` + `struct` (a
two-note chirp with fade-in/out and a trailing gap, cached as a WAV). Playback
picks the first available player (`paplay` → `aplay` → `ffplay` → `cvlc`); with
none, it falls back to repeating the terminal bell. A `Ringer` owns one looping
playback subprocess so the daemon can start and cleanly stop it on
snooze/dismiss/timeout. Desktop notifications are best-effort via `notify-send`.

### 3.5 Files & configuration

XDG-style locations, fully overridable by `CLARM_HOME` (critical for test
isolation): alarms under `~/.local/share/clarm/`, runtime files (pid, log, tone)
under `~/.local/state/clarm/`. Tunables (`snooze_minutes`, `max_snoozes`,
`ring_timeout`) are read live from env vars with sensible defaults — no config
file needed.

### 3.6 Module layout

| Module | Responsibility | Pure? |
|--------|----------------|:-----:|
| `timeparse.py` | parse time / duration / repeat specs | ✅ |
| `models.py` | `Alarm` record, `next_fire`, transitions, (de)serialise | ✅ |
| `storage.py` | `flock`-guarded atomic JSON load/save/update | — |
| `sound.py` | WAV tone generation, player detection, play/stop, notify | — |
| `daemon.py` | double-fork lifecycle, self-pipe loop, ring orchestration | — |
| `cli.py` | argparse subcommands + dispatch | — |
| `config.py` | env-overridable defaults | ✅ |
| `paths.py` | XDG dirs, `CLARM_HOME` override | — |

---

## 4. Implementation plan (ordered)

1. **Scaffold** — `pyproject.toml` (setuptools, `src/` layout, `clarm` entry
   point), `README.md`, package skeleton, `__main__.py`.
2. **`paths.py`** — resolve data/runtime dirs honouring `CLARM_HOME`.
3. **`timeparse.py`** (pure) — durations, time-of-day, absolute datetimes, repeat
   specs → weekday set. **+ tests.**
4. **`models.py`** (pure) — `Alarm`, JSON round-trip, `next_fire`, snooze/dismiss.
   **+ tests.**
5. **`storage.py`** — `flock` + atomic replace; id allocation; load/save/update.
   **+ tests.**
6. **`sound.py`** — WAV tone generator, player detection, looped play + stop,
   `notify-send`.
7. **`daemon.py`** — `daemonize()` (double-fork/`setsid`, redirect stdio, pidfile),
   self-pipe signal loop, due-detection (reusing `next_fire`), ring orchestration
   with timeout + snooze/dismiss handling.
8. **`cli.py`** — argparse subcommands; each mutation persists then `SIGHUP`s the
   daemon; human-friendly `list` table. **+ dispatch tests.**
9. **README** + final verification (full pytest + live daemon smoke test).

---

## 5. Testing strategy

- **Unit (deterministic core):** `timeparse`, recurrence/`next_fire` with injected
  `now`, storage round-trip + locked rewrite, and CLI dispatch against a temp
  `CLARM_HOME`. No sound hardware or running daemon required.
- **End-to-end smoke (manual):**
  1. `clarm daemon start`
  2. `clarm add in 3s --label smoke` → rings at +3s (state → `ringing`)
  3. `clarm snooze 1m` → sound stops, re-arms
  4. `clarm dismiss` → state → `done`
  5. `clarm daemon stop` → pidfile gone, clean exit
- **Recurring:** `next_fire` advance-after-dismiss is unit-tested (minute
  granularity makes a live recurring ring impractical to wait on).

---

## 6. Status

Implemented as planned. **65 unit tests pass**; the live daemon smoke test passes
(start → add → ring → snooze → dismiss → stop, confirmed in the daemon log); the
`clarm` console entry point and editable install are verified. See
[README.md](README.md) for usage.
