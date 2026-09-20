"""Real-time streaming Butterworth low-pass filter.

Zero-lag filters (``filtfilt``) require the whole signal up-front, so for
live use we use a causal SOS filter with persistent state (``sosfilt`` +
``sosfilt_zi``). This introduces a small group delay proportional to the
filter order and inversely proportional to the cutoff — trade-off between
smoothing and lag.

Typical use in running / gait research:

    * 6 Hz low-pass on joint kinematics before angle computation.
    * 20 Hz low-pass on trunk / tibial acceleration before peak detection
      (removes sensor noise while preserving impact spikes).
    * 3 Hz low-pass on pelvis position for cadence estimation from vertical
      bounce.

References:
    * Winter, D. A. (2009). "Biomechanics and Motor Control of Human
      Movement" (4th ed.), Chapter 3 — recommends 6 Hz low-pass for lower
      limb kinematics.
    * SciPy signal design: https://docs.scipy.org/doc/scipy/reference/signal.html
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

from gaitlab.scripts.api import AnalysisScript, ScriptInput, ScriptOutput


class LowpassFilter(AnalysisScript):
    id = "signal.lowpass"
    display_name = "Butterworth low-pass filter"
    default_rate_hz = 60.0

    inputs = [
        ScriptInput("signal", unit="",
                    default_channel="xsens.seg17.y",
                    description="Any 1D channel. Output has the same unit."),
    ]
    outputs = [
        ScriptOutput("filtered", unit="", description="Low-pass filtered signal, causal (has group delay)."),
        ScriptOutput("raw", unit="", description="Passthrough of the input for overlay comparison."),
    ]

    # Tunables — edit and Rescan.
    cutoff_hz: float = 6.0
    order: int = 4

    def setup(self) -> None:
        nyq = 0.5 * self.default_rate_hz
        wn = self.cutoff_hz / nyq
        if not (0.0 < wn < 1.0):
            raise RuntimeError(
                f"cutoff_hz={self.cutoff_hz} is invalid for rate {self.default_rate_hz} Hz "
                f"(Nyquist={nyq}). Pick a value in (0, {nyq})."
            )
        self._sos = butter(self.order, wn, btype="low", output="sos")
        # Steady-state initial conditions so the first output value isn't a huge transient.
        self._zi = sosfilt_zi(self._sos)
        self._primed = False

    def on_sample(self, ts: float, values: dict[str, float]) -> dict[str, float]:
        v = float(values.get("signal", 0.0))
        if not self._primed:
            self._zi = self._zi * v
            self._primed = True
        out_arr, self._zi = sosfilt(self._sos, np.array([v], dtype=np.float64), zi=self._zi)
        return {"filtered": float(out_arr[0]), "raw": v}
