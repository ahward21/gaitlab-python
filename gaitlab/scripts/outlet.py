"""Thin ``pylsl.StreamOutlet`` wrapper for script outputs.

Each enabled script gets one outlet named ``GaitLabScript_<id>`` with one
float32 channel per declared output, in ``script.outputs`` order. Consumers
(Unity via LSL4Unity, LabRecorder, another gaitlab instance) resolve by name.
"""

from __future__ import annotations

import logging
from typing import Sequence

from gaitlab.scripts.api import ScriptOutput, lsl_stream_name_for

try:
    import pylsl  # type: ignore
except ImportError:  # pragma: no cover
    pylsl = None  # type: ignore

log = logging.getLogger(__name__)


class ScriptOutlet:
    """Optional LSL outlet for one script's outputs.

    Absent-pylsl or absent-liblsl environments create an inert instance whose
    ``push`` is a no-op — this keeps unit tests running without a network
    dependency.
    """

    def __init__(self, script_id: str, outputs: Sequence[ScriptOutput], rate_hz: float) -> None:
        self.script_id = script_id
        self.outputs = list(outputs)
        self.rate_hz = float(rate_hz)
        self._outlet = None
        self._last_values: list[float] = [0.0] * len(self.outputs)
        self._enabled = False

    def open(self) -> bool:
        if pylsl is None:
            log.info("pylsl not available; script outlet %s disabled", self.script_id)
            return False
        if not self.outputs:
            log.info("script %s has no outputs; outlet skipped", self.script_id)
            return False
        try:
            info = pylsl.StreamInfo(
                name=lsl_stream_name_for(self.script_id),
                type="Derived",
                channel_count=len(self.outputs),
                nominal_srate=float(self.rate_hz),
                channel_format=pylsl.cf_float32,
                source_id=f"gaitlab-script-{self.script_id}",
            )
            desc = info.desc()
            chans = desc.append_child("channels")
            for out in self.outputs:
                ch = chans.append_child("channel")
                ch.append_child_value("label", out.symbol)
                ch.append_child_value("unit", out.unit or "")
            self._outlet = pylsl.StreamOutlet(info)
            self._enabled = True
            return True
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Failed to open LSL outlet for script %s: %s", self.script_id, exc)
            self._outlet = None
            self._enabled = False
            return False

    def close(self) -> None:
        # StreamOutlet has no explicit close; drop the reference so liblsl GCs it.
        self._outlet = None
        self._enabled = False

    @property
    def is_open(self) -> bool:
        return self._enabled and self._outlet is not None

    def push(self, values_by_symbol: dict[str, float]) -> None:
        """Push one sample. Missing outputs reuse the last-known value so the
        XDF timeline has no sparse holes."""
        if not self.is_open:
            return
        sample: list[float] = []
        for idx, out in enumerate(self.outputs):
            if out.symbol in values_by_symbol:
                v = float(values_by_symbol[out.symbol])
                self._last_values[idx] = v
            else:
                v = self._last_values[idx]
            sample.append(v)
        try:
            self._outlet.push_sample(sample)  # type: ignore[union-attr]
        except Exception as exc:  # pragma: no cover
            log.warning("Failed to push sample for script %s: %s", self.script_id, exc)
