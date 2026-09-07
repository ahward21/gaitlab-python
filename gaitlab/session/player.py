"""Deterministic replay of a recorded GaitLab CSV session into a DataHub.

Design contract (see docs/REPLAY_GUIDE.md for the researcher-facing version):

* The player owns the virtual clock. It walks the CSV in row order and, for
  each row, publishes every channel into the hub and then calls
  ``scripts.drive_sample(virtual_ts, row_values)`` synchronously. Enabled
  scripts have their timer threads paused for the duration of playback so
  ``drive_sample`` is the *only* code that mutates script state.
* Speed only changes how fast we walk the file. The sample sequence a script
  sees at speed 1x, 10x, and MAX is bit-identical; only the wall-clock delay
  between rows changes.
* Seek (forward or backward) is deterministic by full replay: reset the hub
  values, reset every enabled script (``teardown`` + fresh ``setup``), rewind
  to row 0, then drive every row up to the target with UI updates suppressed.
  Slow but provably identical to a straight play from 0 to the target.
* Recording a derived session (raw channels + script outputs to a new CSV)
  runs from Play forward only. Any seek issued while recording stops the
  recording — see ``on_seek_stops_recording`` in the researcher notes.

The player is UI-agnostic: it exposes ``play() / pause() / stop() / seek(t)``
plus an ``on_state_change`` callback the Sessions panel wires to its
transport widgets. No Qt imports here.
"""

from __future__ import annotations

import csv
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from gaitlab.hub import DataHub
from gaitlab.scripts.runner import ScriptRegistry

log = logging.getLogger(__name__)


class PlayerState(str, Enum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    SEEKING = "seeking"


@dataclass(frozen=True)
class SessionMetadata:
    """Header comments parsed from the CSV (`# key=value` lines)."""

    session: str = ""
    started: str = ""
    sample_mode: str = ""
    sample_hz: float = 0.0
    sample_interval_sec: float = 0.0
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionComputeResult:
    """Per-row time series captured from a full offline batch compute.

    ``times`` mirrors the loaded ``SessionFile.times``. ``input_series`` /
    ``output_series`` map channel_id -> tuple of values, one per row, aligned
    with ``times``. Output channels are whichever hub channels the enabled
    scripts publish; input channels are every column of the source CSV.
    """

    times: tuple[float, ...]
    input_series: dict[str, tuple[float, ...]]
    output_series: dict[str, tuple[float, ...]]

    @property
    def sample_count(self) -> int:
        return len(self.times)


@dataclass(frozen=True)
class SessionFile:
    """In-memory representation of a loaded GaitLab CSV.

    ``times`` are seconds since recording start (from the CSV's ``time_sec``
    column). ``rows`` is a list of dicts (channel_id -> float) aligned with
    ``times``. Missing cells become ``0.0`` — matching the ``0`` sentinel the
    MVN bridge uses for absent sensors.
    """

    path: Path
    channels: tuple[str, ...]
    times: tuple[float, ...]
    rows: tuple[dict[str, float], ...]
    metadata: SessionMetadata

    @property
    def duration_sec(self) -> float:
        return float(self.times[-1]) if self.times else 0.0

    @property
    def row_count(self) -> int:
        return len(self.times)


def load_session(path: Path | str) -> SessionFile:
    """Parse a GaitLab CSV. Empty cells → 0.0; malformed floats → 0.0."""
    p = Path(path)
    meta: dict[str, str] = {}
    with p.open("r", encoding="utf-8", newline="") as f:
        # Peel off "# key=value" header comments.
        header_lines: list[str] = []
        while True:
            pos = f.tell()
            line = f.readline()
            if not line:
                break
            if line.startswith("#"):
                header_lines.append(line)
                continue
            f.seek(pos)
            break
        for h in header_lines:
            body = h.lstrip("#").strip()
            if "=" in body:
                k, v = body.split("=", 1)
                meta[k.strip()] = v.strip()
        reader = csv.reader(f)
        header = next(reader, None)
        if not header or header[0] != "time_sec":
            raise ValueError(
                f"{p.name}: expected first column 'time_sec', got {header!r}"
            )
        channels = tuple(header[1:])
        times: list[float] = []
        rows: list[dict[str, float]] = []
        for raw in reader:
            if not raw:
                continue
            try:
                t = float(raw[0])
            except (TypeError, ValueError):
                continue
            row: dict[str, float] = {}
            for i, cid in enumerate(channels, start=1):
                if i >= len(raw):
                    row[cid] = 0.0
                    continue
                cell = raw[i]
                if cell == "":
                    row[cid] = 0.0
                    continue
                try:
                    row[cid] = float(cell)
                except (TypeError, ValueError):
                    row[cid] = 0.0
            times.append(t)
            rows.append(row)
    md = SessionMetadata(
        session=meta.get("session", ""),
        started=meta.get("started", ""),
        sample_mode=meta.get("sample_mode", ""),
        sample_hz=_safe_float(meta.get("sample_hz", "")),
        sample_interval_sec=_safe_float(meta.get("sample_interval_sec", "")),
        extra={k: v for k, v in meta.items()
               if k not in {"session", "started", "sample_mode",
                            "sample_hz", "sample_interval_sec", "sample_value"}},
    )
    return SessionFile(
        path=p,
        channels=channels,
        times=tuple(times),
        rows=tuple(rows),
        metadata=md,
    )


def _safe_float(text: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


StateCallback = Callable[["SessionPlayer"], None]


class SessionPlayer:
    """Deterministic replay engine.

    Threading model:
      * One playback thread when ``state == PLAYING``. It sleeps
        ``(next_row_t - prev_row_t) / speed`` between publishes so the timing
        follows the recording's own clock, not a nominal rate.
      * ``seek()`` and ``pause()`` may be called from any thread; they signal
        the playback thread via an event and, for seek, run the fast-forward
        replay inline (blocking the caller) so the state on return is exactly
        the post-seek state. The Sessions panel calls seek from the Qt event
        loop and gets a busy-cursor for the ~seconds a long fast-forward
        takes.

    The player does not touch the recorder itself. ``on_seek_during_record``
    is a callback the panel wires up to stop its own recorder before the
    fast-forward begins, keeping this module UI-agnostic.
    """

    MAX_SPEED = 1_000_000.0  # sentinel: "as fast as possible, no sleep"

    def __init__(
        self,
        hub: DataHub,
        scripts: ScriptRegistry,
    ) -> None:
        self.hub = hub
        self.scripts = scripts
        self.session: SessionFile | None = None
        self._state = PlayerState.IDLE
        self._cursor = 0  # next row index to publish
        self._speed = 1.0
        self._lock = threading.RLock()
        self._resume_evt = threading.Event()
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        # Monotonically increments on every seek/stop/load — consumers can
        # detect virtual-clock discontinuities by watching this.
        self._seek_generation: int = 0
        self.on_state_change: StateCallback | None = None
        self.on_seek_during_record: Callable[[], None] | None = None
        self.on_row_published: Callable[[int, float], None] | None = None

    # ------------------------------------------------------------------ props

    @property
    def state(self) -> PlayerState:
        return self._state

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def current_time_sec(self) -> float:
        s = self.session
        if s is None or self._cursor == 0:
            return 0.0
        idx = min(self._cursor - 1, s.row_count - 1)
        return s.times[idx]

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def seek_generation(self) -> int:
        """Monotonic counter; changes whenever the virtual clock discontinues."""
        return self._seek_generation

    def set_speed(self, speed: float) -> None:
        s = max(0.1, min(self.MAX_SPEED, float(speed)))
        with self._lock:
            self._speed = s

    # ------------------------------------------------------------------ load

    def load(self, path: Path | str) -> SessionFile:
        """Load a CSV. Stops any current playback. Does not auto-play.

        Pauses every enabled script's timer thread for as long as this session
        is loaded — script state is henceforth mutated only by `drive_sample`
        from the playback thread. Call `unload()` to hand control back.
        """
        self.stop(_release_ticks=True)
        session = load_session(path)
        with self._lock:
            self.session = session
            self._cursor = 0
        # Reseed hub with every channel from the file so UI dropdowns are
        # populated before playback (matches LslManager / XsensUdpSource's
        # proactive registration pattern).
        for cid in session.channels:
            if self.hub.try_get(cid) is None:
                self.hub.publish(cid, 0.0)
        self.scripts.pause_all_ticks()
        # Wipe any state accumulated by timer ticks that ran between enable()
        # and load() — the session owns script state from here forward.
        self.scripts.reset_all()
        self._set_state(PlayerState.PAUSED)
        return session

    def unload(self) -> None:
        """Release scripts back to their timer threads and drop the session."""
        self.stop(_release_ticks=True)
        with self._lock:
            self.session = None
            self._cursor = 0
        self._set_state(PlayerState.IDLE)

    # ------------------------------------------------------------------ transport

    def play(self) -> None:
        s = self.session
        if s is None:
            return
        # Auto-rewind if the cursor is at EOF — treat Play at end-of-file as
        # "start over", which is what a researcher pressing Play again
        # expects. Resets script state so the fresh playback matches a first-
        # time play (fixes stale cadence / filter state carrying over).
        if self._cursor >= s.row_count:
            self.stop()  # rewinds cursor to 0 and resets scripts + hub
        with self._lock:
            if self._state == PlayerState.PLAYING:
                return
            self._stop_evt.clear()
            self._resume_evt.set()
            self._set_state_locked(PlayerState.PLAYING)
            self._thread = threading.Thread(
                target=self._playback_loop, name="session-player", daemon=True
            )
            self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if self._state != PlayerState.PLAYING:
                return
            self._resume_evt.clear()
        # Wait for the loop to observe the pause and exit its inner sleep.
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None
        # Script ticks stay paused — they're owned by the player for the
        # duration of `load()` -> `unload()`.
        self._set_state(PlayerState.PAUSED)

    def stop(self, *, _release_ticks: bool = False) -> None:
        """Stop playback and rewind to row 0. Does not unload the session.

        Resets script state and zeros hub values — same guarantee as `seek(0)`.
        Without this, a subsequent Play would start with scripts still holding
        end-of-file state (peak detector last-peak-time, filter memory, etc.),
        producing constant/wrong outputs until enough new data flushed them.

        `_release_ticks` is used by `load()`/`unload()` to hand script control
        back to their timer threads. Public callers should not pass it.
        """
        with self._lock:
            self._stop_evt.set()
            self._resume_evt.clear()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None
        if _release_ticks:
            self.scripts.resume_all_ticks()
        else:
            # Session stays loaded — reset scripts + hub so the next Play
            # starts from a clean slate. Only meaningful when a session is
            # loaded (otherwise there are no scripts owned by the player).
            if self.session is not None:
                self.hub.reset_values()
                self.scripts.reset_all()
        with self._lock:
            self._cursor = 0
        # Bump seek generation so listeners (graphs) can drop virtual-clock buffers.
        self._seek_generation += 1
        self._set_state(PlayerState.PAUSED if self.session else PlayerState.IDLE)

    def seek(self, target_time_sec: float) -> None:
        """Deterministic jump to ``target_time_sec``.

        Pauses playback, calls ``on_seek_during_record`` (panel stops the
        derived recorder), resets hub values + every enabled script, then
        replays row 0 -> target row synchronously. Blocks until done.
        """
        s = self.session
        if s is None:
            return
        was_playing = self._state == PlayerState.PLAYING
        if was_playing:
            self.pause()
        cb = self.on_seek_during_record
        if cb is not None:
            try:
                cb()
            except Exception:  # pragma: no cover - defensive
                log.exception("on_seek_during_record callback raised")
        self._set_state(PlayerState.SEEKING)
        target_idx = self._row_index_for_time(target_time_sec)
        self.hub.reset_values()
        # Script ticks are already paused (owned by the player since load()).
        # Reset script state before driving so setup() runs against a clean hub.
        self.scripts.reset_all()
        # Drive from row 0 up to and including target_idx.
        for i in range(target_idx + 1):
            self._apply_row(i, notify=False)
        with self._lock:
            self._cursor = target_idx + 1
            self._seek_generation += 1
        self._set_state(PlayerState.PAUSED)
        if was_playing:
            self.play()

    def compute_full_session(
        self,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> SessionComputeResult:
        """Run every enabled script over every row and capture per-row outputs.

        Offline batch analog to Play at MAX speed: resets hub + scripts,
        drives every row in order, records the value of every input channel
        and every script-output channel *at each row* into aligned time
        series. Blocks the caller until done. No inter-row sleep, no LSL push.

        Same determinism guarantee as ``seek(duration)`` — the terminal hub
        state matches a straight play from 0 to end — but with the full
        per-sample trace kept so a researcher can graph the whole session
        without having to record and re-load a derived CSV.
        """
        s = self.session
        if s is None:
            raise RuntimeError("No session loaded")
        was_playing = self._state == PlayerState.PLAYING
        if was_playing:
            self.pause()
        cb = self.on_seek_during_record
        if cb is not None:
            try:
                cb()
            except Exception:  # pragma: no cover - defensive
                log.exception("on_seek_during_record callback raised")
        self._set_state(PlayerState.SEEKING)
        self.hub.reset_values()
        self.scripts.reset_all()

        output_ids: list[str] = []
        for sid in self.scripts.enabled_ids():
            inst = self.scripts.instance(sid)
            if inst is not None:
                output_ids.extend(inst.output_channel_ids())
        input_ids = list(s.channels)
        input_buf: dict[str, list[float]] = {cid: [] for cid in input_ids}
        output_buf: dict[str, list[float]] = {cid: [] for cid in output_ids}

        total = s.row_count
        for i in range(total):
            self._apply_row(i, notify=False)
            for cid in input_ids:
                input_buf[cid].append(self.hub.get_or_default(cid))
            for cid in output_ids:
                output_buf[cid].append(self.hub.get_or_default(cid))
            if progress_callback is not None and (i % 500 == 0 or i == total - 1):
                try:
                    progress_callback(i + 1, total)
                except Exception:  # pragma: no cover - defensive
                    log.exception("progress_callback raised")

        with self._lock:
            self._cursor = total
            self._seek_generation += 1
        self._set_state(PlayerState.PAUSED)
        return SessionComputeResult(
            times=tuple(s.times),
            input_series={k: tuple(v) for k, v in input_buf.items()},
            output_series={k: tuple(v) for k, v in output_buf.items()},
        )

    # ------------------------------------------------------------------ helpers

    def _row_index_for_time(self, target: float) -> int:
        """Largest row index whose timestamp is <= ``target``.

        `<=` (not `<`) matches researcher intent: "state at time t" means the
        last observed sample at or before t. This is also the only interpretation
        stable across CSV precision — recorded timestamps are 4-decimal but a
        seek target from a scrubber is full float, and exact-match seeks would
        otherwise land off-by-one when the target is a multiple of the sample
        period. Returns 0 if target is before or at the first sample.
        """
        s = self.session
        assert s is not None
        if target <= s.times[0]:
            return 0
        if target >= s.times[-1]:
            return s.row_count - 1
        lo, hi = 0, s.row_count - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2  # bias high so we converge on last <= target
            if s.times[mid] <= target:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def _apply_row(self, idx: int, *, notify: bool) -> None:
        """Publish row ``idx`` into hub and drive scripts once."""
        s = self.session
        if s is None:
            return
        row = s.rows[idx]
        for cid, value in row.items():
            self.hub.publish(cid, value)
        ts = s.times[idx]
        self.scripts.drive_sample(ts, row)
        if notify and self.on_row_published is not None:
            try:
                self.on_row_published(idx, ts)
            except Exception:  # pragma: no cover - defensive
                log.exception("on_row_published callback raised")

    def _playback_loop(self) -> None:
        s = self.session
        if s is None:
            return
        while not self._stop_evt.is_set() and self._resume_evt.is_set():
            with self._lock:
                idx = self._cursor
                speed = self._speed
            if idx >= s.row_count:
                break
            self._apply_row(idx, notify=True)
            with self._lock:
                self._cursor = idx + 1
            # Sleep the inter-row delta scaled by playback speed. First row
            # has no predecessor so we don't sleep before it.
            if idx + 1 < s.row_count and speed < self.MAX_SPEED:
                dt = (s.times[idx + 1] - s.times[idx]) / speed
                if dt > 0:
                    if self._stop_evt.wait(timeout=dt):
                        break
        # Loop exit: either stop, pause, or end-of-file. Script ticks stay
        # paused (session is still loaded until the caller unloads).
        if self._cursor >= s.row_count:
            self._set_state(PlayerState.PAUSED)

    def _set_state(self, new: PlayerState) -> None:
        with self._lock:
            self._set_state_locked(new)

    def _set_state_locked(self, new: PlayerState) -> None:
        if new == self._state:
            return
        self._state = new
        cb = self.on_state_change
        if cb is not None:
            # Fire outside the lock to avoid deadlocks if the callback
            # re-enters the player.
            def _fire() -> None:
                try:
                    cb(self)
                except Exception:  # pragma: no cover - defensive
                    log.exception("on_state_change callback raised")
            threading.Thread(target=_fire, daemon=True).start()
