"""Runtime for enabled analysis scripts.

Each enabled script runs on its own daemon thread at a configurable rate. On
every tick it reads current values for its bound inputs from the ``DataHub``,
calls ``on_sample(ts, values)``, and publishes any returned outputs back into
the hub (so the Channels / Metrics / Graphs UI + CsvRecorder see them) and
optionally to an LSL outlet (so Unity / LabRecorder can subscribe).

Design notes:

* Threading model: one thread per enabled script. Scripts don't share state
  and the DataHub is thread-safe (``RLock``), so no coordination is needed
  between scripts.
* Failure isolation: exceptions in ``on_sample`` are caught, logged, and
  counted. After ``max_consecutive_errors`` (default 5) the instance is
  auto-disabled so a runaway user script can't flood the log.
* Slow-tick warning: if ``on_sample`` exceeds half the tick budget the
  instance flips an ``is_slow`` flag surfaced in the status object.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Type

from gaitlab import channels as channel_catalog
from gaitlab.hub import DataHub
from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput, hub_channel_id_for
from gaitlab.scripts.outlet import ScriptOutlet

log = logging.getLogger(__name__)


@dataclass
class ScriptBinding:
    """Which hub channel feeds one of the script's declared inputs."""

    symbol: str
    channel_id: str = ""


@dataclass
class ScriptStatus:
    enabled: bool = False
    ticks: int = 0
    samples_pushed_to_lsl: int = 0
    last_error: str = ""
    consecutive_errors: int = 0
    is_slow: bool = False
    last_tick_ts: float = 0.0
    rate_hz: float = 0.0


class ScriptInstance:
    """One enabled script + its bindings, thread, and outlet."""

    max_consecutive_errors: int = 5

    def __init__(
        self,
        cls: Type[AnalysisScript],
        hub: DataHub,
        bindings: list[ScriptBinding],
        rate_hz: float,
        publish_lsl: bool = True,
    ) -> None:
        self.cls = cls
        self.hub = hub
        self.bindings = list(bindings)
        self.rate_hz = max(0.1, float(rate_hz))
        self.publish_lsl = bool(publish_lsl)
        self.status = ScriptStatus(rate_hz=self.rate_hz)
        self._script: AnalysisScript | None = None
        self._outlet: ScriptOutlet | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # Set while a session player owns this instance — suppresses the timer
        # thread's `_tick_once` so only `drive_sample` mutates script state.
        self._ticks_paused = threading.Event()
        # Optional hook the ScriptRegistry uses to remove this instance from its
        # bookkeeping when the instance auto-disables itself after too many errors.
        self.on_auto_disable = None  # type: ignore[assignment]

    # ------------------------------------------------------------------ meta

    @property
    def id(self) -> str:
        return self.cls.id

    @property
    def display_name(self) -> str:
        return self.cls.display_name or self.cls.__name__

    def output_channel_ids(self) -> list[str]:
        return [hub_channel_id_for(self.id, o.symbol) for o in self.cls.outputs]

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self.status.enabled:
            return
        try:
            self._script = self.cls()
            self._script.hub = self.hub
            self._script.setup()
        except Exception as exc:
            log.exception("Script %s setup() failed", self.id)
            self.status.last_error = f"setup failed: {exc}"
            self._script = None
            return

        # Proactively register outputs so the UI dropdowns list them before
        # the first tick lands, matching what LslManager / XsensUdpSource do.
        for out in self.cls.outputs:
            cid = hub_channel_id_for(self.id, out.symbol)
            channel_catalog.register_user_channel(
                cid,
                f"{self.display_name}: {out.symbol}",
                out.unit or "",
                channel_catalog.CAT_DERIVED,
            )
            if self.hub.try_get(cid) is None:
                self.hub.publish(cid, 0.0, out.unit or "")

        if self.publish_lsl:
            self._outlet = ScriptOutlet(self.id, self.cls.outputs, self.rate_hz)
            self._outlet.open()

        self._stop.clear()
        self.status.enabled = True
        self.status.ticks = 0
        self.status.samples_pushed_to_lsl = 0
        self.status.last_error = ""
        self.status.consecutive_errors = 0
        self.status.is_slow = False
        self._thread = threading.Thread(target=self._loop, name=f"script:{self.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.status.enabled:
            return
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.5)
        self._thread = None
        try:
            if self._script is not None:
                self._script.teardown()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Script %s teardown() failed: %s", self.id, exc)
        self._script = None
        if self._outlet is not None:
            self._outlet.close()
            self._outlet = None
        self.status.enabled = False

    def _auto_disable(self) -> None:
        """Called from a helper thread after too many consecutive errors."""
        self.stop()
        cb = self.on_auto_disable
        if cb is not None:
            try:
                cb(self.id)
            except Exception:  # pragma: no cover - defensive
                log.exception("on_auto_disable callback raised for %s", self.id)

    # ------------------------------------------------------------------ replay

    def pause_ticks(self) -> None:
        """Suspend the timer thread's on_sample calls (player takes over)."""
        self._ticks_paused.set()

    def resume_ticks(self) -> None:
        """Re-enable the timer thread's on_sample calls."""
        self._ticks_paused.clear()

    def reset(self) -> None:
        """Wipe script state without dropping the enabled/outlet lifecycle.

        The daemon thread stays running (and blocked in its next sleep) so
        `enabled` reporting doesn't flicker; only the user script's internal
        state is torn down and re-set-up. Safe to call from any thread.
        """
        script = self._script
        if script is None:
            return
        try:
            script.teardown()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Script %s teardown() during reset failed: %s", self.id, exc)
        fresh = self.cls()
        fresh.hub = self.hub
        try:
            fresh.setup()
        except Exception as exc:
            log.exception("Script %s setup() during reset failed", self.id)
            self.status.last_error = f"reset setup failed: {exc}"
            self._script = None
            return
        self._script = fresh
        self.status.ticks = 0
        self.status.consecutive_errors = 0
        self.status.last_error = ""

    def drive_sample(self, ts: float, values: dict[str, float] | None = None) -> None:
        """Synchronous replay tick — bypasses the timer thread for determinism.

        Player calls this in row order during Play (all speeds) and during a
        seek fast-forward. Reads bound inputs from the hub (or the passed
        `values` overlay), calls the script, and publishes outputs.
        Publishing to the LSL outlet is suppressed here — replayed samples
        would collide with any live listener; the derived-CSV recorder is the
        capture path for replay outputs.
        """
        script = self._script
        if script is None:
            return
        row: dict[str, float] = {}
        binding_by_symbol = {b.symbol: b for b in self.bindings}
        for inp in self.cls.inputs:
            b = binding_by_symbol.get(inp.symbol)
            cid = (b.channel_id if b else "") or inp.default_channel
            if values is not None and cid and cid in values:
                row[inp.symbol] = float(values[cid])
            else:
                row[inp.symbol] = self.hub.get_or_default(cid) if cid else 0.0
        try:
            outputs = script.on_sample(ts, row) or {}
        except Exception as exc:
            self.status.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("Script %s on_sample() raised during replay: %s", self.id, exc)
            return
        for out in self.cls.outputs:
            if out.symbol not in outputs:
                continue
            try:
                v = float(outputs[out.symbol])
            except (TypeError, ValueError):
                continue
            self.hub.publish(hub_channel_id_for(self.id, out.symbol), v, out.unit or "")
        self.status.ticks += 1
        self.status.last_tick_ts = ts

    # ------------------------------------------------------------------ loop

    def _loop(self) -> None:
        period = 1.0 / self.rate_hz
        soft_budget = period * 0.5
        next_tick = time.perf_counter()
        while not self._stop.is_set():
            now = time.perf_counter()
            sleep_for = next_tick - now
            if sleep_for > 0:
                if self._stop.wait(timeout=sleep_for):
                    break
            if not self._ticks_paused.is_set():
                self._tick_once()
            next_tick += period
            # If we fell more than a period behind, resync so we don't spin.
            if time.perf_counter() > next_tick + period:
                next_tick = time.perf_counter() + period
            self.status.is_slow = (time.perf_counter() - now) > soft_budget

    def _tick_once(self) -> None:
        script = self._script
        if script is None:
            return
        values: dict[str, float] = {}
        binding_by_symbol = {b.symbol: b for b in self.bindings}
        for inp in self.cls.inputs:
            b = binding_by_symbol.get(inp.symbol)
            cid = (b.channel_id if b else "") or inp.default_channel
            values[inp.symbol] = self.hub.get_or_default(cid) if cid else 0.0

        ts = time.time()
        try:
            outputs = script.on_sample(ts, values) or {}
        except Exception as exc:
            self.status.last_error = f"{type(exc).__name__}: {exc}"
            self.status.consecutive_errors += 1
            log.warning("Script %s on_sample() raised: %s", self.id, exc)
            if self.status.consecutive_errors >= self.max_consecutive_errors:
                log.error(
                    "Script %s hit %d consecutive errors — auto-disabling",
                    self.id,
                    self.status.consecutive_errors,
                )
                # Schedule self-stop from a helper thread; can't join our own.
                threading.Thread(
                    target=self._auto_disable, name=f"script:{self.id}:auto-stop", daemon=True
                ).start()
            return
        self.status.consecutive_errors = 0

        # Publish to hub — every declared output, using last-known or the
        # value the script just returned. Missing keys are legitimate: the
        # script chose not to update that output this tick.
        for out in self.cls.outputs:
            if out.symbol not in outputs:
                continue
            try:
                v = float(outputs[out.symbol])
            except (TypeError, ValueError):
                continue
            self.hub.publish(hub_channel_id_for(self.id, out.symbol), v, out.unit or "")

        # Push to LSL outlet — always a full sample (last-known fill), so the
        # XDF timeline has no sparse holes.
        if self._outlet is not None and self._outlet.is_open:
            self._outlet.push(outputs)
            self.status.samples_pushed_to_lsl += 1

        self.status.ticks += 1
        self.status.last_tick_ts = ts


class ScriptRegistry:
    """Owns all discovered script classes and the currently-enabled instances."""

    def __init__(self, hub: DataHub) -> None:
        self.hub = hub
        self._discovered: dict[str, Type[AnalysisScript]] = {}
        self._instances: dict[str, ScriptInstance] = {}

    # ---- discovery ingestion ----

    def register_class(self, cls: Type[AnalysisScript]) -> None:
        if not cls.id:
            return
        self._discovered[cls.id.lower()] = cls

    def register_classes(self, classes: list[Type[AnalysisScript]]) -> None:
        for c in classes:
            self.register_class(c)

    def clear_discovered(self) -> None:
        self._discovered.clear()

    def discovered_ids(self) -> list[str]:
        return sorted(self._discovered.keys())

    def get_class(self, script_id: str) -> Type[AnalysisScript] | None:
        return self._discovered.get((script_id or "").lower())

    # ---- lifecycle ----

    def enable(
        self,
        script_id: str,
        bindings: list[ScriptBinding],
        rate_hz: float | None = None,
        publish_lsl: bool = True,
    ) -> ScriptInstance | None:
        cls = self.get_class(script_id)
        if cls is None:
            return None
        # Restart if already enabled with new bindings.
        self.disable(script_id)
        rate = float(rate_hz) if rate_hz else float(cls.default_rate_hz or 60.0)
        inst = ScriptInstance(cls, self.hub, bindings, rate, publish_lsl)
        inst.on_auto_disable = self._on_auto_disable
        inst.start()
        if inst.status.enabled:
            self._instances[script_id.lower()] = inst
        return inst

    def _on_auto_disable(self, script_id: str) -> None:
        self._instances.pop((script_id or "").lower(), None)

    def disable(self, script_id: str) -> None:
        inst = self._instances.pop((script_id or "").lower(), None)
        if inst is not None:
            inst.stop()

    def disable_all(self) -> None:
        for key in list(self._instances.keys()):
            self.disable(key)

    def pause_all_ticks(self) -> None:
        for inst in self._instances.values():
            inst.pause_ticks()

    def resume_all_ticks(self) -> None:
        for inst in self._instances.values():
            inst.resume_ticks()

    def reset_all(self) -> None:
        """Reset every enabled instance in registration order.

        Used by the session player before a rewind. Iteration order matches
        enable order so scripts that depend on other scripts' outputs (chained
        `hub.get_or_default` reads) see a consistent baseline.
        """
        for inst in self._instances.values():
            inst.reset()

    def drive_sample(self, ts: float, values: dict[str, float] | None = None) -> None:
        """Drive every enabled instance synchronously for one replay row."""
        for inst in self._instances.values():
            inst.drive_sample(ts, values)

    def instance(self, script_id: str) -> ScriptInstance | None:
        return self._instances.get((script_id or "").lower())

    def enabled_ids(self) -> list[str]:
        return sorted(self._instances.keys())

    def status_summary(self) -> list[dict]:
        out = []
        for key, inst in self._instances.items():
            out.append(
                {
                    "id": key,
                    "display_name": inst.display_name,
                    "rate_hz": inst.rate_hz,
                    "ticks": inst.status.ticks,
                    "samples_pushed_to_lsl": inst.status.samples_pushed_to_lsl,
                    "last_error": inst.status.last_error,
                    "is_slow": inst.status.is_slow,
                }
            )
        return out
