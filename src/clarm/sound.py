"""Make noise — with zero third-party dependencies.

The alarm tone is *generated* at runtime with the stdlib :mod:`wave` and
:mod:`math` modules (a two-note beep), cached as a small WAV file. Playback
shells out to whichever common player is installed (``paplay``, ``aplay``,
``ffplay`` or ``cvlc``); if none is found we fall back to the terminal bell.

A :class:`Ringer` owns one looping playback subprocess so the daemon can start
ringing and later stop it cleanly on snooze/dismiss/timeout.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import time
import wave

from clarm import paths

_SAMPLE_RATE = 44100
# Players that take a WAV path as the final argument. Order = preference.
_PLAYERS = [
    ("paplay", []),
    ("aplay", ["-q"]),
    ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
    ("cvlc", ["--play-and-exit", "--intf", "dummy"]),
]


def _tone_samples(freq: float, seconds: float, volume: float = 0.5):
    n = int(_SAMPLE_RATE * seconds)
    for i in range(n):
        # Apply a short linear fade in/out to avoid clicks.
        fade = min(1.0, i / 400, (n - i) / 400)
        yield int(volume * fade * 32767 * math.sin(2 * math.pi * freq * i / _SAMPLE_RATE))


def generate_tone(path=None) -> str:
    """Write the alarm WAV (if not already cached) and return its path.

    The pattern is a pleasant-but-insistent two-note chirp followed by a short
    silence, ~1s total, designed to be looped.
    """
    out = str(path or paths.tone_path())
    target = paths.tone_path() if path is None else path
    if path is None and target.exists() and target.stat().st_size > 0:
        return out

    frames = bytearray()
    for f, dur in [(880.0, 0.18), (1320.0, 0.18)]:
        for s in _tone_samples(f, dur):
            frames += struct.pack("<h", s)
    # trailing silence so the loop has a gap between chirps
    frames += b"\x00\x00" * int(_SAMPLE_RATE * 0.5)

    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SAMPLE_RATE)
        w.writeframes(bytes(frames))
    return out


def find_player():
    """Return ``(executable, args)`` for the first available player, or ``None``."""
    for name, args in _PLAYERS:
        exe = shutil.which(name)
        if exe:
            return exe, args
    return None


def notify(title: str, message: str) -> None:
    """Best-effort desktop notification via ``notify-send`` (ignored if absent)."""
    exe = shutil.which("notify-send")
    if not exe:
        return
    try:
        subprocess.Popen(
            [exe, "-u", "critical", "-a", "clarm", title, message],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


class Ringer:
    """Loop the alarm tone until :meth:`stop` is called.

    When a real player is available we spawn it once per loop iteration in a
    background thread. With no player, we fall back to writing the terminal bell
    character on a timer. Either way :meth:`stop` halts promptly.
    """

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._stop = False
        self._thread = None

    def start(self) -> None:
        import threading

        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        player = find_player()
        if player is None:
            self._bell_loop()
            return
        exe, args = player
        tone = generate_tone()
        while not self._stop:
            try:
                self._proc = subprocess.Popen(
                    [exe, *args, tone],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self._proc.wait()
            except OSError:
                self._bell_loop()
                return

    def _bell_loop(self) -> None:
        while not self._stop:
            try:
                sys.stdout.write("\a")
                sys.stdout.flush()
            except (OSError, ValueError):
                pass
            time.sleep(1.0)

    def stop(self) -> None:
        self._stop = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)
