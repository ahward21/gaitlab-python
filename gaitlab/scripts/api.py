"""Public contract for user-authored analysis scripts.

Mirrors the ``MetricDefinition`` / ``MetricVariableBinding`` shape from
``gaitlab.metrics.definitions`` so the two systems feel consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class ScriptInput:
    """One named signal the script consumes from the hub.

    ``symbol`` is what the script sees inside ``on_sample`` (e.g. ``"signal"``);
    ``default_channel`` is the hub channel id pre-selected in the UI (may be
    empty — user picks in the panel).
    """

    symbol: str
    unit: str = ""
    default_channel: str = ""
    description: str = ""


@dataclass(frozen=True)
class ScriptOutput:
    """One named value the script publishes back to the hub / LSL outlet.

    The hub channel id is derived at runtime as ``script.<script_id>.<symbol>``
    so users can find outputs in the Channels / Graphs / Metrics dropdowns
    without having to know the internal naming.
    """

    symbol: str
    unit: str = ""
    description: str = ""


class AnalysisScript:
    """Base class every user script subclasses.

    Class attributes (override in subclass):
        id: stable dotted or kebab id, e.g. ``"gait.peak_detector"``. Used to
            name the LSL outlet and prefix output hub channels.
        display_name: shown in the Scripts panel list.
        inputs: ordered list of ``ScriptInput`` — what the script consumes.
        outputs: ordered list of ``ScriptOutput`` — what the script produces.
        default_rate_hz: preferred tick rate; user can override in the panel.

    Instance methods (override as needed):
        setup(): called once when the script is enabled. Initialise buffers,
            counters, filter state here. ``self.hub`` and ``self.session`` are
            already assigned by the runtime before ``setup()`` runs so scripts
            can inspect available channels or participant params at startup.
        on_sample(ts, values): called on every tick. ``values`` is a dict of
            bound-input symbol -> current hub value. Return a dict of
            output-symbol -> float. Missing keys are simply not published on
            this tick.
        teardown(): called once when the script is disabled. Release any
            resources acquired in ``setup``.

    Runtime attributes injected before ``setup()``:
        self.hub: the shared ``DataHub``. Use ``self.hub.get_or_default(cid)``
            or ``self.hub.try_get(cid)`` to read any hub channel — useful when a
            script needs many related channels (e.g. all 23 segments' quaternions
            for joint-angle computation) and declaring them as bound inputs
            would clutter the UI.
    """

    id: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    inputs: ClassVar[list[ScriptInput]] = []
    outputs: ClassVar[list[ScriptOutput]] = []
    default_rate_hz: ClassVar[float] = 60.0

    # Set by ScriptInstance before setup() — see runner.py.
    hub = None  # type: ignore[assignment]

    def setup(self) -> None:  # pragma: no cover - default no-op
        pass

    def on_sample(self, ts: float, values: dict[str, float]) -> dict[str, float]:
        raise NotImplementedError

    def teardown(self) -> None:  # pragma: no cover - default no-op
        pass


def hub_channel_id_for(script_id: str, output_symbol: str) -> str:
    """Canonical hub channel id for a script output."""
    return f"script.{script_id}.{output_symbol}"


def lsl_stream_name_for(script_id: str) -> str:
    """Canonical LSL outlet name for a script."""
    return f"GaitLabScript_{script_id}"
